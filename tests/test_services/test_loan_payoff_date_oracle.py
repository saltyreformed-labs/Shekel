"""C8b oracle: the derived payoff date is a fold to zero.

Plan step C8b (``docs/audits/balance_architecture/README.md``).  The seam's
``balance_at.loan_payoff_date`` folds a loan's forward plan from its
confirmed-present seed and returns the date the balance reaches ``0.00`` -- the
DERIVED payoff the arc substitutes for the blind-schedule copies
(``LoanState.payoff_date``, ``RecurrenceRule.end_date``).  This proves it two
ways:

* :class:`TestPlanPayoffDate` -- the pure fold-to-zero on hand-built plans
  (``plan_payoff_date`` from ``tests/oracles/loan_forward_fold``: the retired
  ``(seed, plan)`` contract over the ONE timeline since plan step
  recurrence:R16-c-1, running the production replay), so the reaches-zero /
  retired-seed / negative-amortization / due-order rules are pinned to
  arithmetic anyone can check.
* :class:`TestLoanPayoffDateSeam` -- the seam entry against the engine's OWN
  contractual projection (``compute_payoff_scenarios``' contractual slice and
  ``project_forward`` with the extra, an independent producer) on real loans,
  so a HEALTHY or OVERPAYING loan's derived payoff EQUALS the contract's (those
  cutovers at C8c move no baseline) and a standing extra beats the contractual
  date (the fold-to-zero, not ``plan[-1].date``).

**Baseline parity is a healthy/overpaying claim, NOT universal.** An UNDERPAYING
loan (a balance behind the contractual schedule) clears a few months PAST the
contractual date -- the ESTIMATED tail's post-contractual extension (C8c,
:class:`TestPayoffTailExtension`) -- so it derives a LATER date than the
resolver's ``is_last_month``-forced contractual one (the deliberate move C8c
makes).  Only a drift too severe to clear within the extension folds to ``None``.
The PURE fold-to-zero on a truncated plan (``plan_payoff_date`` handed a plan with
no further installments) still returns ``None``, pinned below; the drift that
produces an underpayment is what C7's payment-drift warning surfaces.

The producer is ADDITIVE and UNWIRED at C8b -- only this oracle reads it.
"""

import random
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.services import balance_at, loan_loaders
from app.services.balance_at._positions import memoized_payoff
from app.services.balance_at._plan import PlannedPayment
from tests.oracles.loan_forward_fold import (
    HandPlan,
    fold_forward,
    plan_interest_in_year,
    plan_payoff_date,
    plan_required_extra,
)
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from app.services.balance_at import BalanceContext
from app.utils.dates import add_months
from tests._test_helpers import (
    bind_rule_to_loan,
    contract_forward_references,
    create_loan_account,
    insert_trueup_event,
    last_covered_day,
    loan_params_for,
    make_cadence_rule,
)
from tests.oracles.loan_monthly_composition import (
    accrual_charge,
    charge_then_allocate,
)
from tests.oracles.recurrence_baseline import MONTHLY

_PRINCIPAL = Decimal("300000.00")
_RATE = Decimal("0.06000")
_TERM = 360


def _payment(due, cash, *, effective=None):
    """Build one :class:`PlannedPayment` for the pure-fold unit tests."""
    return PlannedPayment(
        due_date=due,
        effective_date=effective if effective is not None else due,
        cash=Decimal(cash),
        is_estimated=False,
    )


def _plan(payments, *, rate="0.00", escrow="0.00"):
    """Bundle *payments* with one CHARGE per accrual period they occupy.

    Stated by hand rather than taken from the leaf's calendar
    (``loan_ledger.contract_charges``, ruling R-R100), so these tests
    grade the FOLD against arithmetic anyone can check rather than against the
    derivation that feeds it.  One charge per ``(year, month)`` the payments
    occupy, dated at the earliest due in it -- which for the one-payment-a-month
    plans below is each payment's own due date.
    """
    opens = {}
    for payment in payments:
        slot = (payment.due_date.year, payment.due_date.month)
        if payment.due_date < opens.get(slot, date.max):
            opens[slot] = payment.due_date
    return HandPlan(
        payments=list(payments),
        charges=[
            accrual_charge(on_date, Decimal(rate), Decimal(escrow))
            for on_date in sorted(opens.values())
        ],
        # Every payment here has a charge standing over it (one per slot,
        # dated at its earliest due), so the fold never asks for a calendar.
    )


def _clears_within(seed, plan, extra, target):
    """Whether *extra* a month puts the balance at zero BY *target*.

    The sweep's independent oracle: it re-folds the plan here rather than asking
    the producer, and keys on the payment's EFFECTIVE date -- when its cash
    actually moves -- which is the property "clear by the target" actually means.

    **It folds through the RETIRED ONE-PAYMENT-PER-MONTH composition
    deliberately** (plan step R16-a; ``tests.oracles.loan_monthly_composition``
    since plan step X-au-g-2c-3b-2 deleted it from ``app/``).  Every plan this
    oracle grades puts exactly one payment in each accrual period, and on that
    shape charging a month inside the per-payment step is the same arithmetic as
    charging it as its own event -- so agreeing with this oracle is the
    equivalence claim itself, measured rather than asserted.  A plan with two
    payments in one period is NOT gradeable here and is pinned directly instead
    (:class:`TestChargePerPeriodNotPerPayment`).
    """
    charged = {
        (charge.on_date.year, charge.on_date.month): charge
        for charge in plan.charges
    }
    balance = seed
    for payment in sorted(
        plan.payments, key=lambda p: (p.due_date, p.effective_date),
    ):
        charge = charged[(payment.due_date.year, payment.due_date.month)]
        parts = charge_then_allocate(
            payment.cash + extra, balance,
            charge.period.annual_rate, charge.escrow,
        )
        balance = parts.balance_after
        if balance <= Decimal("0.00"):
            return payment.effective_date <= target
    return False


class TestPlanPayoffDate:
    """The pure fold-to-zero, on hand-built plans (arithmetic anyone can check)."""

    def test_folds_to_zero_returns_the_clearing_installments_due_date(self):
        """The DUE date of the first payment whose running balance hits zero.

        At 0% interest every dollar is principal.  Seed $1000: after a $600
        payment the balance is $400; the next $600 payment clears the $400
        (principal capped at the balance), so the loan is paid off on the SECOND
        installment's due date.
        """
        seed = Decimal("1000.00")
        plan = _plan([
            _payment(date(2027, 1, 1), "600.00"),
            _payment(date(2027, 2, 1), "600.00"),
        ])
        # 1000 - 600 = 400 (2027-01-01); 400 - 400 = 0 (2027-02-01).
        assert plan_payoff_date(seed, plan) == date(2027, 2, 1)

    def test_folds_in_due_order_not_input_order(self):
        """The fold sorts by due date, so payoff keys on the contract, not input.

        Given a $900 payment due 01-01 and a $600 payment due 02-01, in REVERSE
        input order: due-order folds 900 first (1000 -> 100), then 600 (100 -> 0)
        on 02-01.  Folding in input order would clear it on 01-01 instead -- so
        asserting 02-01 proves the due-order sort.
        """
        seed = Decimal("1000.00")
        plan = _plan([
            _payment(date(2027, 2, 1), "600.00"),
            _payment(date(2027, 1, 1), "900.00"),
        ])
        # Due order: 1000 - 900 = 100 (01-01); 100 - 100 = 0 (02-01).
        assert plan_payoff_date(seed, plan) == date(2027, 2, 1)

    def test_already_retired_seed_returns_none(self):
        """A seed at or below zero has no forward crossing -- ``None``, not a date.

        A retired loan owes nothing at the projection seed; the first planned
        payment must NOT be mistaken for a payoff (the caller badges it via
        ``is_retired`` instead).
        """
        plan = _plan([_payment(date(2027, 1, 1), "600.00")])
        assert plan_payoff_date(Decimal("0.00"), plan) is None
        assert plan_payoff_date(Decimal("-5.00"), plan) is None

    def test_negative_amortization_never_reaches_zero_returns_none(self):
        """A payment below the period interest grows the balance -- ``None``.

        Seed $1000 at 12%/yr accrues $10.00 interest a month; a $5.00 payment
        leaves principal -$5.00, so the balance rises to $1005 and never clears.
        """
        seed = Decimal("1000.00")
        plan = _plan(
            [_payment(date(2027, m, 1), "5.00") for m in range(1, 7)],
            rate="0.12000",
        )
        assert plan_payoff_date(seed, plan) is None

    def test_empty_plan_returns_none(self):
        """No payments means the balance never moves to zero -- ``None``."""
        assert plan_payoff_date(Decimal("1000.00"), _plan([])) is None

    def test_truncated_plan_that_never_clears_returns_none(self):
        """The PURE fold on a plan whose installments run out above zero -- ``None``.

        ``plan_payoff_date`` folds exactly the plan it is handed: two $300 payments
        at 0% take $1000 down to $400, then the plan ENDS with the balance still
        positive, so no installment drives it to ``<= 0``.  This is the pure
        function's contract (a plan too short -> ``None``), independent of the
        SEAM's ESTIMATED tail, which extends past the contractual date so a real
        underpaying loan clears there instead (:class:`TestPayoffTailExtension`).
        """
        seed = Decimal("1000.00")
        plan = _plan([
            _payment(date(2027, 1, 1), "300.00"),
            _payment(date(2027, 2, 1), "300.00"),
        ])
        # 1000 - 300 = 700 (01-01); 700 - 300 = 400 (02-01); plan ends above zero.
        assert plan_payoff_date(seed, plan) is None


def _create_loan(seed_user, period, origination_date, *, name):
    """Create a fixed mortgage originating on *origination_date*, anchored to *period*."""
    account = create_loan_account(
        seed_user, db.session, name=name,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=origination_date, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    return account, loan_params_for(db.session, account.id)


def _attach_derive_extra(seed_user, loan_account, extra):
    """Attach a derive-from-loan recurring payment carrying a standing extra.

    **Bound to the loan through the loan door's own sync since plan step
    R16-b-2.**  ``fires_on_day=1`` alone starts the rule on the first 1st the
    fixture schedule reaches, which is one or two months BEFORE the loan
    originates; the loan door never leaves a payment there
    (``bind_rule_to_loan`` writes ``starts_on`` as the first contractual
    installment, plan step C9a).  Until R16-b-2 the fold never priced an
    occurrence dated before ``as_of``, so those pre-origination occurrences
    were invisible; ruling **R-R64** prices every occurrence the schedule
    places that no row answers -- exactly as generation would write them --
    so a definition authored ahead of its loan now pays for months the loan
    did not exist, the shape the door exists to refuse.  Authoring as the door
    does is what keeps the fixture a loan payment.
    """
    user = seed_user["user"]
    # Authored through the write door (plan step R7c-b): the day the rule
    # fires on is its first occurrence's own day, so "the 1st" is stated as a
    # DATE the fixture schedule reaches rather than as a separate column.
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        name="Mortgage Payment",
    )
    template.settings = LoanPaymentSettings(
        derive_from_loan=True, extra_principal=extra,
    )
    db.session.add(template)
    db.session.commit()
    # The definition first, then the cadence onto it (plan step R-F6), then
    # the loan's own start onto the cadence, as ``routes/loan/payment_transfer``
    # does.
    rule = make_cadence_rule(
        template, MONTHLY, fires_on_day=1,
    )
    bind_rule_to_loan(rule, loan_account.id)
    db.session.commit()


def _reference_payoffs(loan_params, scenario_id, as_of, extra):
    """Return (payoff with *extra* a month, pure-contractual payoff) -- the reference.

    The independent reference (:func:`tests._test_helpers.contract_forward_references`):
    ``project_forward`` over the CONTRACT from the replayed starting state, a
    walk that amortizes month by month and consumes no payment records at all
    -- a different code path from the seam's fold, so agreement is meaningful,
    not tautological.  *It was the composer's ACCELERATED slice until plan
    step R7d-g-3 deleted the composer's planned slices (ruling **R-R88**).*
    """
    scenarios, with_extra = contract_forward_references(
        loan_params, scenario_id, as_of, extra,
    )
    return with_extra[-1].payment_date, scenarios.original_forward[-1].payment_date


def _current_period(periods, today):
    """The seeded pay period containing *today*."""
    return next(
        period for period in periods
        if period.start_date <= today <= last_covered_day(period)
    )


class TestLoanPayoffDateSeam:
    """The seam entry on real loans, against the engine's own contractual projection."""

    def test_healthy_loan_matches_the_contracts_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A healthy loan's derived payoff EQUALS the contract's -- baseline unmoved.

        The loan originates at the current period (clean past), so the fold and
        the contractual schedule agree on the timeline.  ``loan_payoff_date``
        (fold to zero) must equal the contract's last installment (the
        composer's ``original_forward``), so the C8c cutover moves nothing.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Healthy",
            )
            scenario_id = seed_user["scenario"].id
            _, contractual_payoff = _reference_payoffs(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) == contractual_payoff

    def test_standing_extra_matches_the_engine_and_beats_contractual(
        self, app, seed_user, seed_periods_today,
    ):
        """With a standing extra the payoff is the engine's contract-plus-extra date --

        and STRICTLY BEFORE the pure-contractual date, proving the fold-to-zero
        reaches the accelerated payoff rather than returning ``plan[-1].date`` (the
        last contractual installment).  Depends on C8a: the ESTIMATED tail now
        folds the extra past the shadow horizon.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Extra",
            )
            _attach_derive_extra(seed_user, account, Decimal("500.00"))
            scenario_id = seed_user["scenario"].id
            accelerated_payoff, contractual_payoff = _reference_payoffs(
                loan_params, scenario_id, today, Decimal("500.00"),
            )
            assert accelerated_payoff < contractual_payoff, (
                "extra did not accelerate payoff; the test would be vacuous"
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            derived = balance_at.loan_payoff_date(account, ctx)
            assert derived == accelerated_payoff
            assert derived < contractual_payoff

    def test_retired_loan_returns_none(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan trued up to $0 has no forward payoff -- ``None`` (badge via is_retired).

        The loan owes $0.00 at the read (retired), so there is no forward
        crossing to date; ``loan_payoff_date`` returns ``None`` rather than
        inventing a future date from the contractual tail.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Retired",
            )
            insert_trueup_event(loan_params, Decimal("0.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) is None

    def test_not_yet_originated_loan_folds_to_contractual_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan not yet originated folds its whole contractual timeline to payoff.

        With no confirmed past and no extra, its plan is the pure contractual
        schedule, so the derived payoff is that schedule's last installment -- the
        contractual payoff, a future date.
        """
        with app.app_context():
            today = date.today()
            future = next(
                period for period in seed_periods_today
                if period.start_date > today
            )
            account, loan_params = _create_loan(
                seed_user, future, future.start_date, name="Payoff Future",
            )
            assert loan_params.origination_date > today
            rate_changes = loan_loaders.load_rate_changes(account.id)
            contractual = contractual_schedule_from_origination(
                loan_params, rate_changes,
            )
            expected = contractual[-1].payment_date

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) == expected

    def test_non_loan_account_raises(self, app, seed_user):
        """A non-loan account is a caller error -- fail loud, like ``positions``."""
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            with pytest.raises(ValueError, match="requires a configured loan"):
                balance_at.loan_payoff_date(seed_user["account"], ctx)


class TestPayoffTailExtension:
    """C8c (N-16): the fold pays past the contractual date until the loan clears."""

    def test_underpaying_loan_clears_in_the_extension(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan behind the schedule clears a few months PAST the contractual date.

        A true-up leaves the balance $500 above where the contractual schedule
        expects it (an underpayment / drift), so the fold does not reach zero at
        the contractual installment.  Before C8c that residue made
        ``loan_payoff_date`` return ``None``; the post-contractual extension now
        clears it, so it derives a REAL date -- and a LATER one than the resolver's
        ``is_last_month``-forced contractual payoff (the deliberate N-16 move).
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Underpaid",
            )
            # $500 behind the contractual schedule -- the underpayment residue.
            insert_trueup_event(loan_params, _PRINCIPAL + Decimal("500.00"))
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            _, contractual_payoff = _reference_payoffs(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            derived = balance_at.loan_payoff_date(account, ctx)
            assert derived is not None, (
                "an underpaying loan must clear in the post-contractual extension, "
                "not report None (finding N-16)"
            )
            # The $500-behind balance compounds at 6% over the ~30-year term to a
            # residue of ~$500 * 1.005**360 ~= $3,010 at the contractual payoff,
            # which two level-payment (~$1,798 P&I) extension installments clear
            # (~$1,783 principal each), so payoff lands two months past the
            # engine's forced contractual date.
            assert derived == add_months(contractual_payoff, 2), (
                f"underpaid payoff {derived} should be two extension installments "
                f"past the engine's forced contractual {contractual_payoff}"
            )

    def test_severe_underpayment_never_amortizes_returns_none(
        self, app, seed_user, seed_periods_today,
    ):
        """A balance so high the level payment cannot cover its interest -- ``None``.

        Trued up to $400k against a $300k contractual P&I (~$1,798), the monthly
        interest (~$2,000) EXCEEDS the payment, so the balance grows every month
        and the extension never clears it: the fold reports ``None`` rather than
        inventing a payoff.  (The resolver's ``is_last_month`` would force a date
        by absorbing the grown balance in a phantom final payment; the fold does
        not.)
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Negam",
            )
            insert_trueup_event(loan_params, Decimal("400000.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) is None

    def test_healthy_loan_is_not_resurrected_past_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A healthy loan's balance stays $0 past its payoff -- the extension is inert.

        The extension appends installments PAST the contractual date; on a healthy
        loan (already zero there) they must fold to no-ops.  A year past the
        contractual payoff the balance is still ``$0.00`` -- the extension neither
        moves the payoff (still the contractual date, ``test_healthy_loan...``) nor
        resurrects a paid-off balance.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Healthy2",
            )
            scenario_id = seed_user["scenario"].id
            _, contractual_payoff = _reference_payoffs(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            past_payoff = add_months(contractual_payoff, 12)
            assert balance_at.balance_at(account, ctx, past_payoff) == (
                Decimal("0.00")
            )


class TestPayoffCutover:
    """Plan C8d: the seam's figures carry the DERIVED payoff, derived ONCE.

    The oracle above grades the producer.  These grade the CUTOVER -- that the
    figure every consumer reads (:attr:`~app.services.balance_at.LoanFigures.payoff_date`,
    the single funnel behind the loan card's chip, the /savings cockpit and
    Horizon, and the equity chart's axis) IS that producer's answer and not the
    resolver's schedule endpoint it used to be.
    """

    def test_figures_payoff_is_the_derived_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """``LoanFigures.payoff_date`` equals ``loan_payoff_date`` on a healthy loan."""
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Cutover Healthy",
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            assert figures is not None
            assert figures.payoff_date == balance_at.loan_payoff_date(
                account, ctx,
            )

    def test_figures_payoff_leaves_the_schedule_endpoint_behind(
        self, app, seed_user, seed_periods_today,
    ):
        """On an UNDERPAYING loan the figure is the fold's date, not the schedule's.

        The control that makes the test above non-vacuous: for a healthy loan the
        two producers agree, so equality alone cannot show which one is wired.
        A loan trued up $500 above the contractual schedule clears two
        installments LATER than the resolver's ``is_last_month``-forced
        contractual payoff (the C8c extension), so the two answers differ and the
        figure must carry the fold's.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Cutover Underpaid",
            )
            insert_trueup_event(loan_params, _PRINCIPAL + Decimal("500.00"))
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            _, contractual_payoff = _reference_payoffs(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            assert figures is not None
            assert figures.payoff_date == add_months(contractual_payoff, 2)
            assert figures.payoff_date != contractual_payoff, (
                "the figure still reports the contract's schedule endpoint, so "
                "the cutover is not wired"
            )

    def test_a_retired_loan_reports_no_payoff_but_is_retired(
        self, app, seed_user, seed_periods_today,
    ):
        """A retired loan's figure is ``None`` + ``is_retired`` -- the badge state.

        Finding B-20: it used to report the loan's ORIGINATION date as a
        "payoff" (the resolver's empty-schedule fallback), which is a past date
        presented as a future event.  The two fields together are what let the
        chip badge "Paid off" instead of inventing a date.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Cutover Retired",
            )
            insert_trueup_event(loan_params, Decimal("0.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            assert figures is not None
            assert figures.payoff_date is None
            assert figures.is_retired is True
            assert figures.payoff_date != loan_params.origination_date

    def test_the_payoff_is_derived_once_per_read_pass(
        self, app, seed_user, seed_periods_today,
    ):
        """Reading the figures twice folds the plan to zero ONCE.

        The payoff cache (:attr:`~app.services.balance_at.BalanceContext.payoffs`)
        exists because a single ``/savings`` render asks two callers for the same
        loan's figures.  Proven by inspecting the pass's cache directly: after
        ``loan_figures`` runs, the slot keyed by the account id is populated, so the
        second read is served from it rather than re-folding.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Cutover Memo",
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            assert not ctx.payoffs, "the cache starts empty"
            first = balance_at.loan_figures(account, ctx)
            assert first is not None

            # The slot the seam's funnel filled -- keyed on the account id now the
            # deriver is no longer injected (plan step D-ctx-b).
            assert account.id in ctx.payoffs
            assert ctx.payoffs[account.id] == first.payoff_date

            second = balance_at.loan_figures(account, ctx)
            assert second is not None
            assert second.payoff_date == first.payoff_date

    def test_a_fresh_pass_derives_again(
        self, app, seed_user, seed_periods_today,
    ):
        """The payoff cache is scoped to ONE read pass, never carried across passes.

        The property that lets a WRITE path (``loan_recurrence_sync``) build a
        context mid-mutation and see the post-write loan: a new context starts with
        an empty payoff cache and derives from scratch.  (Plan step D-ctx-b retired
        the injected-deriver design, so this observes the PUBLIC cache filling
        rather than counting deriver calls.)
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Cutover Fresh",
            )

            first_ctx = BalanceContext.build(seed_user["user"].id)
            assert account.id not in first_ctx.payoffs
            memoized_payoff(account, first_ctx)
            assert account.id in first_ctx.payoffs, "one pass derives and caches"

            second_ctx = BalanceContext.build(seed_user["user"].id)
            assert account.id not in second_ctx.payoffs, (
                "a NEW pass starts empty -- the cache is per-pass, and a write "
                "path depends on that to see its own writes"
            )
            memoized_payoff(account, second_ctx)
            assert account.id in second_ctx.payoffs, "the new pass derives again"

    def test_a_no_baseline_context_fails_loud_on_every_call(
        self, app, seed_user, seed_periods_today,
    ):
        """``memoized_payoff`` raises on a no-baseline context -- on EVERY call.

        The payoff funnel folds through ``loan_payoff_date``, whose
        ``require_scenario`` raises when the context has no baseline scenario.
        ``_memoize_once`` caches only a returned value, so the raising build is
        never cached and the guard cannot be worn down by retrying -- the property
        the funnel docstrings assert but no test pinned before plan step D-ctx-b.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="No Baseline Payoff",
            )
            no_baseline = BalanceContext(
                user_id=seed_user["user"].id, scenario=None, as_of=today,
            )
            for _ in range(2):
                with pytest.raises(ValueError):
                    memoized_payoff(account, no_baseline)
            assert account.id not in no_baseline.payoffs, (
                "a raising build must never cache"
            )


class TestPlanRequiredExtra:
    """The pure target-date search, on hand-built plans (plan step C8f).

    ``plan_required_extra`` answers "what must I add to every payment to be done
    by X", folded from the SAME plan and seed the payoff and the balance use.  It
    replaced ``loan_resolver.target_date_outlook``, which binary-searched the
    contractual schedule walk -- a walk that amortizes installments nobody paid
    (finding B-9), so it could answer "no extra needed" for a target the loan does
    not actually reach, contradicting the payoff chip on the same page.

    Every case here runs at a 0% rate so the arithmetic is inspectable.
    """

    def test_a_plan_that_already_clears_by_the_target_needs_nothing(self):
        """Two $600 payments clear $1,000 by 2026-02-01, inside the target."""
        plan = _plan([
            _payment(date(2026, 1, 1), "600.00"),
            _payment(date(2026, 2, 1), "600.00"),
        ])
        assert plan_required_extra(
            Decimal("1000.00"), plan, date(2026, 3, 1),
        ) == Decimal("0.00")

    def test_the_searched_extra_is_the_least_that_reaches_the_target(self):
        """$1,000 against two $400 payments leaves $200 -- exactly $100 each.

        Without extra the plan pays $800 of $1,000 and never clears.  Spreading
        the $200 shortfall over the two payments is $100 apiece, so that is the
        answer; a cent less must miss, which the minimality half asserts by
        folding it back through the payoff.
        """
        plan = _plan([
            _payment(date(2026, 1, 1), "400.00"),
            _payment(date(2026, 2, 1), "400.00"),
        ])
        seed, target = Decimal("1000.00"), date(2026, 2, 1)
        assert plan_payoff_date(seed, plan) is None, (
            "precondition: the un-topped-up plan must NOT clear the loan"
        )

        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("100.00")
        # Correctness: the found extra really does reach the target...
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target
        # ...and minimality: a cent less does not.
        missed = plan_payoff_date(seed, plan, found - Decimal("0.01"))
        assert missed is None or missed > target

    def test_an_unreachable_target_returns_none(self):
        """No installment falls on or before the target, so no amount lands in time."""
        plan = _plan([_payment(date(2026, 5, 1), "400.00")])
        assert plan_required_extra(
            Decimal("1000.00"), plan, date(2026, 1, 1),
        ) is None

    def test_a_retired_loan_needs_nothing(self):
        """A loan owing nothing is already done by any target."""
        assert plan_required_extra(
            Decimal("0.00"), _plan([_payment(date(2026, 1, 1), "400.00")]),
            date(2026, 1, 1),
        ) == Decimal("0.00")


class TestPlanRequiredExtraEdges:
    """The two ways the search can silently return a WRONG amount.

    Both were found by the randomized sweep below, not by hand-written cases, and
    both return a plausible-looking figure that does not do what it claims -- the
    worst failure mode for a number a user acts on.
    """

    def test_the_answer_rounds_UP_so_it_cannot_miss_by_a_fraction(self):
        """A threshold between cents must round UP, never to nearest.

        $1,000 against four $300 payments at 0%: the plan pays $900 by the third
        installment, so clearing by then needs $100 spread over three payments --
        $33.3333... each, which is not a whole cent.  Rounded to NEAREST that is
        $33.33, and 3 x $333.33 = $999.99 leaves a CENT owing, pushing the payoff
        to the fourth installment, past the target.  Rounded UP it is $33.34, and
        3 x $333.34 = $1,000.02 clears.  A sub-cent shortfall costs a whole
        installment because it lands exactly at the payoff boundary.
        """
        plan = _plan([
            _payment(date(2026, 1, 1), "300.00"),
            _payment(date(2026, 2, 1), "300.00"),
            _payment(date(2026, 3, 1), "300.00"),
            _payment(date(2026, 4, 1), "300.00"),
        ])
        seed, target = Decimal("1000.00"), date(2026, 3, 1)

        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("33.34")
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target
        # The half-up answer is the control: it misses by one installment.
        missed = plan_payoff_date(seed, plan, Decimal("33.33"))
        assert missed is None or missed > target

    def test_a_target_in_the_past_is_unreachable_not_a_huge_number(self):
        """An overdue-but-projected payment must not make a PAST target look met.

        Ruling D1 clamps an overdue projected payment's EFFECTIVE date forward to
        ``as_of + 1d`` but leaves its DUE date in the past.  A reachability test
        keyed on due dates therefore passes for a target that has already gone by,
        the fold "clears" the loan on that past due date, and the search returns a
        six-figure extra for a date on which no money can be paid any more --
        rendered as the panel's headline.  Keyed on the EFFECTIVE date (when the
        cash actually moves) the target is correctly unreachable.
        """
        overdue = PlannedPayment(
            due_date=date(2026, 2, 1),        # already passed...
            effective_date=date(2026, 7, 20),  # ...but the cash moves tomorrow
            cash=Decimal("500.00"),
            is_estimated=False,
        )
        plan = _plan([overdue, _payment(date(2026, 8, 1), "500.00")])
        # A target BEFORE the overdue payment's effective date but AFTER its due
        # date -- the exact window the due-date test got wrong.
        assert plan_required_extra(
            Decimal("100000.00"), plan, date(2026, 6, 19),
        ) is None

    def test_the_upper_bound_is_found_not_assumed(self):
        """A payment smaller than its own interest breaks the obvious bound.

        Paying the whole balance as extra looks like a guaranteed upper bound --
        surely the first installment then clears the loan.  It is not: the ONE
        allocation (``app.utils.money.apply_payment_cash``) takes the standing
        interest out of the cash FIRST, so on
        $100,000 at 25% (a $2,083.33 monthly accrual, quantized by the shared
        split) against $500 payments, an extra of exactly the balance still
        leaves $1,583.33 owing at the first installment.  Clearing by then needs
        the balance PLUS that accrual, less the payment's own cash:
        $100,000.00 + $2,083.33 - $500.00 = $101,583.33, which pays exactly
        $100,000.00 of principal and lands the balance on zero.
        """
        plan = _plan(
            [
                _payment(date(2026, 1, 1), "500.00"),
                _payment(date(2026, 2, 1), "500.00"),
            ],
            rate="0.25",
        )
        seed, target = Decimal("100000.00"), date(2026, 1, 1)
        # The naive bound really does fall short -- this is why it is searched.
        naive = plan_payoff_date(seed, plan, seed)
        assert naive is not None and naive > target

        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("101583.33")
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target

    def test_every_generated_plan_gets_an_answer_that_holds(self):
        """A seeded sweep: whatever it returns must be true of the fold.

        Property-based, because the two bugs above both produced figures that
        looked entirely reasonable.  For 250 generated loans it asserts the only
        three things the contract promises: a returned extra REACHES the target,
        it is minimal to within a cent, and ``None`` is returned only when no
        installment falls on or before the target.  The seed is fixed, so a
        failure is reproducible rather than a flake.
        """
        rng = random.Random(20260719)
        violations = []
        for trial in range(250):
            seed = Decimal(str(rng.randrange(1000, 500000))) + Decimal("0.37")
            count = rng.randrange(2, 60)
            cash = Decimal(str(rng.randrange(50, 3000)))
            rate = Decimal(rng.choice(["0.00", "0.03", "0.06", "0.12", "0.25"]))
            # A third of the sweeps carry the ruling-D1 clamp on their earliest
            # records: an overdue-but-still-projected payment keeps its PAST due
            # date while its cash moves at ``as_of + 1d``.  Without this the
            # generated plans all had ``effective == due`` and the sweep was
            # blind to every date question that turns on which of the two is
            # read -- which is how finding H1 reached a review.
            clamp_to = add_months(date(2026, 1, 1), 6) if trial % 3 == 0 else None
            payments = []
            for offset in range(count):
                due = add_months(date(2026, 1, 1), offset)
                effective = (
                    max(due, clamp_to) if clamp_to is not None else due
                )
                payments.append(PlannedPayment(
                    due_date=due,
                    effective_date=effective,
                    cash=cash,
                    is_estimated=False,
                ))
            plan = _plan(payments, rate=str(rate))
            # Drawn from BEYOND the plan's span in both directions, not just
            # from its own due dates: a target before the first installment (or
            # in the past) is the case the earlier version of this sweep could
            # never generate, and it is exactly where finding H1 lived.
            target = add_months(
                payments[rng.randrange(0, count)].effective_date,
                rng.randrange(-18, 6),
            )

            found = plan_required_extra(seed, plan, target)
            if found is None:
                # A ``None`` is only honest if NO extra reaches the target.  The
                # oracle is the fold itself at an absurd extra, not a restatement
                # of the producer's own guard.
                huge = seed * 1000 + Decimal("1000000.00")
                if _clears_within(seed, plan, huge, target):
                    violations.append(
                        f"None though {huge} clears by {target} (seed {seed})"
                    )
                continue
            if not _clears_within(seed, plan, found, target):
                violations.append(
                    f"extra {found} does not clear by {target} (seed {seed})"
                )
            if found > Decimal("0.00") and _clears_within(
                seed, plan, found - Decimal("0.02"), target,
            ):
                violations.append(
                    f"extra {found} is not minimal (seed {seed})"
                )
        assert not violations, violations[:5]


class TestLoanRequiredExtraSeam:
    """The seam entry on real loans (plan step C8f)."""

    def test_a_standing_extra_lowers_the_required_top_up(
        self, app, seed_user, seed_periods_today,
    ):
        """F-27's surviving acceptance: paying more already needs less on top.

        The finding F-27 closed was that a user already overpaying was told they
        needed the full raw extra again.  Two identical loans, one carrying a
        $500/mo standing overpayment: the overpayer must need STRICTLY less added
        to hit the same target, because their plan's cash already carries the
        extra.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            plain, _ = _create_loan(
                seed_user, current, current.start_date, name="Extra None",
            )
            paying, _ = _create_loan(
                seed_user, current, current.start_date, name="Extra Rich",
            )
            _attach_derive_extra(seed_user, paying, Decimal("500.00"))
            target = add_months(today, 240)

            ctx = BalanceContext.build(seed_user["user"].id)
            plain_extra = balance_at.loan_required_extra(plain, ctx, target)
            paying_extra = balance_at.loan_required_extra(paying, ctx, target)
            assert plain_extra is not None and plain_extra > Decimal("0.00")
            assert paying_extra is not None
            assert paying_extra < plain_extra

    def test_it_agrees_with_the_payoff_the_chip_shows(
        self, app, seed_user, seed_periods_today,
    ):
        """"No extra needed" and the payoff chip cannot contradict each other.

        The H1 defect in one assertion: both read the same fold, so the search
        reports ``0.00`` for a target at or after the derived payoff and a real
        amount for one before it.  The pre-C8f search walked the contractual
        schedule and could answer ``0.00`` for a target the chip's payoff was
        months past.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Agreement",
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            payoff = balance_at.loan_payoff_date(account, ctx)
            assert payoff is not None
            assert balance_at.loan_required_extra(
                account, ctx, payoff,
            ) == Decimal("0.00")
            earlier = add_months(payoff, -12)
            needed = balance_at.loan_required_extra(account, ctx, earlier)
            assert needed is not None and needed > Decimal("0.00")


class TestChargePerPeriodNotPerPayment:
    """A period charges ONCE however many payments fall inside it (plan step R16-a).

    **The defect these pin is that the fold had no time dimension.**  While
    interest was charged inside the per-payment step, the payment COUNT was the
    clock: measured on a production clone, 30 payments of ``$531.94`` fourteen
    days apart charged the identical ``$1,096.34`` as 30 a month apart, split for
    split, so the Van Loan paid off in half the time modelled the same interest
    Nothing in the live corpus
    puts two payments in one month -- both loans are MONTH-unit with one
    definition each -- so the frozen production baseline is byte-identical and
    CANNOT see this change.  These are what see it.

    Every case runs at 12%/yr on a $10,000 seed, where a month's interest is
    exactly $100.00, so the two models are told apart by inspection rather than
    by a rounding difference.
    """

    _SEED = Decimal("10000.00")
    _JANUARY = Decimal("100.00")   # 10,000 x 12% / 12

    def _two_in_january(self, *, rate="0.12", escrow="0.00"):
        """A plan paying twice inside one accrual period, on the 1st and the 15th."""
        return _plan(
            [
                _payment(date(2026, 1, 1), "500.00"),
                _payment(date(2026, 1, 15), "500.00"),
            ],
            rate=rate, escrow=escrow,
        )

    def test_a_second_payment_in_one_period_clears_no_fresh_interest(self):
        """Two payments inside January charge January ONCE.

        The charge lands on the period's opening (the 1st, its earliest due), so
        the first payment splits $500 into $100.00 interest and $400.00
        principal, leaving $9,600.00.  The second payment clears no fresh charge
        and is $500.00 of pure principal, leaving **$9,100.00**.

        Charging per PAYMENT gives $9,196.00 instead -- the second payment would
        accrue a second month on $9,600.00 ($96.00) and pay only $404.00 down --
        so the balance below tells the two models apart outright.
        """
        plan = self._two_in_january()
        balances = fold_forward(
            self._SEED, date(2020, 1, 1), plan, [date(2026, 1, 31)],
        )
        assert balances[date(2026, 1, 31)] == Decimal("9100.00")
        assert plan_interest_in_year(self._SEED, plan, 2026) == self._JANUARY

    def test_the_charge_is_applied_BEFORE_a_payment_sharing_its_date(self):
        """January's interest accrues on the balance the 1st has not yet reduced.

        The charge and the first payment fall on the same day.  Charged first,
        January costs 12% / 12 of $10,000.00 = $100.00; allocated first, it would
        cost 12% / 12 of $9,500.00 = $95.00.  Asserting $100.00 pins the order.
        """
        assert plan_interest_in_year(
            self._SEED, self._two_in_january(), 2026,
        ) == self._JANUARY

    def test_escrow_is_impounded_once_a_period_not_once_a_payment(self):
        """A month's escrow is a month's obligation, whoever pays it.

        At 0% with a $200.00 monthly escrow, the first payment covers the escrow
        and pays $300.00 down ($9,700.00); the second owes no further escrow and
        pays $500.00 down, leaving **$9,200.00**.  Impounding per payment would
        take $200.00 twice and leave $9,400.00 -- an escrow the owner never paid.
        """
        plan = self._two_in_january(rate="0.00", escrow="200.00")
        balances = fold_forward(
            self._SEED, date(2020, 1, 1), plan, [date(2026, 1, 31)],
        )
        assert balances[date(2026, 1, 31)] == Decimal("9200.00")

    def test_the_hypothetical_extra_is_per_MONTH_not_per_payment(self):
        """``plan_required_extra`` answers what its own name promises.

        $1,000.00 at 0% against three $300.00 payments -- two in January (the 1st
        and the 15th) and one in February -- pays $900.00 and never clears.  The
        $100.00 shortfall spread over the TWO months the plan spans is **$50.00 a
        month**.  Added per PAYMENT it would be $33.34, which is $100.02 of extra
        against a $100.00 gap: the figure the panel reports as "add this much a
        month" would be a third short of what a fortnightly payer must actually
        find.
        """
        plan = _plan([
            _payment(date(2026, 1, 1), "300.00"),
            _payment(date(2026, 1, 15), "300.00"),
            _payment(date(2026, 2, 1), "300.00"),
        ])
        seed, target = Decimal("1000.00"), date(2026, 2, 1)
        assert plan_payoff_date(seed, plan) is None, (
            "precondition: the un-topped-up plan must NOT clear the loan"
        )
        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("50.00")
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target
        missed = plan_payoff_date(seed, plan, found - Decimal("0.01"))
        assert missed is None or missed > target

    def test_a_closed_loan_charges_nothing_and_refunds_the_cash(self):
        """Past payoff a period charges no interest and no escrow.

        The first payment clears the whole $500.00 seed; the February charge then
        has nothing to accrue on, and February's payment is a refund in full --
        so the balance stays at zero rather than going NEGATIVE by a phantom
        paydown or POSITIVE by a phantom accrual.
        """
        plan = _plan(
            [
                _payment(date(2026, 1, 1), "500.00"),
                _payment(date(2026, 2, 1), "500.00"),
            ],
            rate="0.12", escrow="50.00",
        )
        seed = Decimal("445.00")   # 500 cash - 4.45 interest - 50.00 escrow
        balances = fold_forward(
            seed, date(2020, 1, 1), plan,
            [date(2026, 1, 31), date(2026, 3, 1)],
        )
        assert balances[date(2026, 1, 31)] == Decimal("0.00")
        assert balances[date(2026, 3, 1)] == Decimal("0.00")
        assert plan_interest_in_year(seed, plan, 2026) == Decimal("4.45")

    def test_a_generated_sweep_of_MONTHLY_plans_folds_exactly_as_it_did(self):
        """The equality claim over the input SPACE, not over two live loans.

        The production baseline (``verify_r7d_estimate_equality.py``) is
        byte-identical across R16-a, but it can only say so about two monthly
        loans at one rate each -- it cannot vary the axis the change lives on.
        This sweeps 300 generated ONE-PAYMENT-PER-MONTH plans over rate, escrow,
        cash, seed, length and the ruling-D1 clamp, and grades the new
        charge-then-allocate fold against the RETIRED fold R16-a replaced:
        charge a month INSIDE each payment's split
        (``tests.oracles.loan_monthly_composition.charge_then_allocate``, which
        composes exactly that, and which plan step X-au-g-2c-3b-2 moved out of
        ``app/`` into the oracle package precisely so this comparison survives
        the deletion).  Every payoff date and every year's interest must agree to
        the cent.

        The seed is fixed, so a failure is reproducible rather than a flake.
        """
        rng = random.Random(20260826)
        for trial in range(300):
            rate = Decimal(rng.choice(
                ["0.00", "0.02875", "0.06", "0.06875", "0.12", "0.25"],
            ))
            escrow = Decimal(rng.choice(["0.00", "100.00", "616.99"]))
            cash = Decimal(str(rng.randrange(50, 4000)))
            seed = Decimal(str(rng.randrange(500, 400000))) + Decimal("0.37")
            count = rng.randrange(1, 48)
            clamp_to = add_months(date(2026, 1, 1), 4) if trial % 4 == 0 else None
            payments = []
            for offset in range(count):
                due = add_months(date(2026, 1, 1), offset)
                payments.append(PlannedPayment(
                    due_date=due,
                    effective_date=(
                        max(due, clamp_to) if clamp_to is not None else due
                    ),
                    cash=cash,
                    is_estimated=False,
                ))
            plan = _plan(payments, rate=rate, escrow=str(escrow))

            # The fold R16-a replaced, transcribed here over the same inputs.
            balance, payoff_before = seed, None
            interest_before: dict[int, Decimal] = {}
            for payment in payments:
                parts = charge_then_allocate(
                    payment.cash, balance, rate, escrow,
                )
                balance = parts.balance_after
                year = payment.effective_date.year
                interest_before[year] = (
                    interest_before.get(year, Decimal("0.00")) + parts.interest
                )
                if balance <= Decimal("0.00") and payoff_before is None:
                    payoff_before = payment.due_date

            assert plan_payoff_date(seed, plan) == payoff_before, (
                f"trial {trial}: payoff moved (rate {rate}, escrow {escrow}, "
                f"cash {cash}, seed {seed}, {count} payments)"
            )
            for year in sorted(interest_before):
                assert plan_interest_in_year(seed, plan, year) == (
                    interest_before[year]
                ), f"trial {trial}: {year} interest moved"

