"""R7d-a: a loan's forward plan prices from its own DEFINITION, not the contract.

Plan step **R7d-a** (``docs/plans/implementation_plan_recurrence_redesign.md``).
``balance_at._plan``'s ESTIMATED tier fills every future installment no
materialised row covers.  It used to fill it with the CONTRACT's P&I whatever
the loan's own recurring payment said it would pay, and that made a loan's
payoff a function of which rows happened to have been WRITTEN -- the loop R7d
has to break before it can stop storing the recurrence's closing bound.

Three properties are pinned here:

* the loan's STANDING payment is its oldest active definition
  (:func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`)
  and what that definition says is read off ITS settings row
  (:func:`~app.services.recurring_transfer_query.loan_payment_config`), so
  "no definition" is told from "a definition saying zero" -- the bundled
  ``standing_payment`` / ``StandingPayment`` these graded until plan step
  R7d-g-3 went with the last figure read off the picked definition (ruling
  **R-R83**; plan ledger row **D49**);
* the ESTIMATED tier prices an occurrence through the amount model's own arm
  (:func:`~app.services.cash_ledger.definition_cash`, plan step R16-b-2, which
  deleted the ``standing_installment_cash`` copy this module used to grade;
  its three arms are graded against the row's own rule in
  ``test_amount_source``);
* **the payoff does not move when the future rows are deleted** -- the invariant
  the whole step exists to establish, exercised through the production door
  (``pay_period_admin.regenerate_pay_periods``) that presents that state.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.services import (
    balance_at, loan_loaders, pay_period_write,
    template_amount_service,
)
from app.services import transfer_recurrence
from app.services.generation_schedule import GenerationSchedule
from app.services.balance_at import BalanceContext
from app.services.balance_at._loan_stream import loan_timeline
from app.services.balance_at._plan import loan_plan
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
    active_recurring_transfer_templates,
    loan_payment_config,
)
from app.services.template_amount_service import owns_its_amount
from tests._test_helpers import (
    bind_rule_to_loan,
    record_paydays_across_a_hole,
    rhythm_of,
    add_escrow_line,
    create_loan_account,
    loan_params_for,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import MONTHLY

# A short amortizing loan whose whole contractual schedule is enumerable:
# $12,000 at 6% over 6 months, originated 2026-01-01, due on the 1st.  Read at
# 2026-04-15, so 05-01 / 06-01 / 07-01 are the FUTURE installments the ESTIMATED
# tier fills and 02-01 / 03-01 / 04-01 are past (and, with no record, pay
# nothing -- the B-9 fix).
_PRINCIPAL = Decimal("12000.00")
_RATE = Decimal("0.06")
_TERM = 6
_ORIGINATION = date(2026, 1, 1)
_AS_OF = date(2026, 4, 15)


def _loan(seed_user, escrow_annual=None):
    """Create the controlled short loan, optionally escrowing, and its context."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    if escrow_annual is not None:
        params = loan_params_for(db.session, account.id)
        add_escrow_line(
            db.session, account.id, "Property Tax", escrow_annual,
            effective_date=params.origination_date,
        )
        db.session.flush()
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    return account, ctx


def _recurring_payment(seed_user, loan, base, *, derive=None, extra=None,
                       stated=False, effective_on=None, name="Loan Payment"):
    """Attach an active recurring payment paying *base* into *loan*.

    Mirrors what ``routes/loan/payment_transfer.py`` writes: the definition
    first, its ``loan_payment_settings`` row when the test needs a MODE, the
    cadence onto it (plan step R-F6), and the loan's own START onto the
    cadence (``bind_rule_to_loan``, plan step C9a) -- **the last since plan
    step R16-b-2**.  ``fires_on_day=1`` alone starts the rule on the first 1st
    the seed schedule reaches, 2024-02-01, two years before this loan exists;
    ruling **R-R64** prices every occurrence the schedule places that no row
    answers, as generation would write them, so a definition authored ahead
    of its loan would pay for months the loan did not exist -- the shape the
    door refuses and the fold used to hide by never pricing the past.

    *stated* opens the price SERIES through ``template_amount_service.set_amount``,
    the one write door, which is what makes the definition one that STATES its
    price -- ``default_amount`` alone is a scalar the amount model refuses to
    read as a price for a date.  *effective_on* dates that statement; it defaults
    to the loan's origination so the series covers every installment.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan.id,
        name=name,
        default_amount=base,
    )
    if derive is not None or extra is not None:
        template.settings = LoanPaymentSettings(
            derive_from_loan=bool(derive),
            extra_principal=Decimal("0.00") if extra is None else extra,
        )
    db.session.add(template)
    db.session.flush()
    if stated:
        template_amount_service.set_amount(
            template, base,
            effective_on=_ORIGINATION if effective_on is None else effective_on,
        )
    rule = make_cadence_rule(template, MONTHLY, fires_on_day=1)
    bind_rule_to_loan(rule, loan.id)
    db.session.flush()
    return template


def _contractual_pi(account):
    """Return the contractual P&I of every installment on the test loan.

    The fixture is fixed-rate, so every row carries the same figure; the test
    asserts against it rather than hardcoding a level payment the amortization
    engine derives.
    """
    rows = contractual_schedule_from_origination(
        account.loan_params, loan_loaders.load_rate_changes(account.id),
    )
    return rows[0].payment


class TestStandingPayment:
    """The standing identity and what its settings row says, read apart.

    Until plan step R7d-g-3 these graded ``standing_payment``, which bundled
    the oldest definition with ITS ``extra_principal`` for the balance seam's
    resolver to thread.  The seam threads nothing now (ruling **R-R88**, which re-ruled R-R83's seam clause
    there); the identity survives for the opening-bound sync alone
    and the settings row is read per definition, and each of the three facts
    is pinned on the producer that still answers it.
    """

    def test_a_loan_with_no_recurring_payment_has_no_standing_payment(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """No active recurring payment -> ``None``, not a zeroed value.

        The distinction is load-bearing: ``None`` means the contract is the only
        estimate there is, where a definition stating ``0.00`` would mean the
        owner plans to pay nothing.
        """
        account, _ = _loan(seed_user)

        assert active_recurring_transfer_template(
            account.id, seed_user["user"].id,
        ) is None

    def test_it_reads_the_stated_base_when_there_is_no_settings_row(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A definition with no settings row STATES its base and derives nothing.

        This is the shape both of the developer's live loans are in
        (``budget.loan_payment_settings`` holds 0 rows on production).
        """
        account, _ = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, Decimal("1910.95"), stated=True,
        )

        standing = active_recurring_transfer_template(
            account.id, seed_user["user"].id,
        )

        assert standing is template
        assert loan_payment_config(standing) == (False, Decimal("0.00"))
        # The PRICE is not on the value: the tier resolves it per occurrence
        # through the amount model (``cash_ledger.definition_cash``).

    def test_it_reads_the_mode_and_the_extra_from_the_settings_row(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A DERIVE-mode payment with a standing extra reports both."""
        account, _ = _loan(seed_user)
        _recurring_payment(
            seed_user, account, Decimal("1.00"),
            derive=True, extra=Decimal("250.00"),
        )

        standing = active_recurring_transfer_template(
            account.id, seed_user["user"].id,
        )

        assert loan_payment_config(standing) == (True, Decimal("250.00"))
        # The MODE is read off the template rather than copied onto a value,
        # so a template switched between modes cannot leave a stale flag behind.
        assert owns_its_amount(standing) is False


class TestEveryDefinitionIntoAnAccount:
    """:func:`active_recurring_transfer_templates`, plan step **R16-b-2** (R-R35).

    The SET the ESTIMATED tier sums, where the standing payment is one row
    of it.  One filter states both: the standing payment is the set's oldest
    member, so the two cannot disagree about which definitions qualify.
    """

    def test_it_returns_every_active_ruled_definition_oldest_first(
        self, seed_user, db,
    ):
        """Two definitions into one loan are both returned, ascending by id."""
        account, _ = _loan(seed_user)
        first = _recurring_payment(
            seed_user, account, Decimal("1910.95"), stated=True,
        )
        second = _recurring_payment(
            seed_user, account, Decimal("200.00"), stated=True, name="Extra",
        )
        db.session.flush()

        templates = active_recurring_transfer_templates(
            account.id, seed_user["user"].id,
        )

        assert [template.id for template in templates] == [first.id, second.id]
        assert active_recurring_transfer_template(
            account.id, seed_user["user"].id,
        ) is templates[0]

    def test_an_archived_or_rule_less_definition_is_not_in_the_set(
        self, seed_user, db,
    ):
        """Inactive, or carrying no rule, is not a definition that pays forward."""
        account, _ = _loan(seed_user)
        kept = _recurring_payment(
            seed_user, account, Decimal("1910.95"), stated=True,
        )
        archived = _recurring_payment(
            seed_user, account, Decimal("300.00"), stated=True, name="Old",
        )
        archived.is_active = False
        unruled = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=account.id,
            name="One-off",
            default_amount=Decimal("500.00"),
        )
        db.session.add(unruled)
        db.session.flush()

        templates = active_recurring_transfer_templates(
            account.id, seed_user["user"].id,
        )

        assert [template.id for template in templates] == [kept.id]

    def test_an_account_with_no_definition_answers_the_empty_set(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """``[]`` here is ``None`` one function over; both mean the contract is the only estimate."""
        account, _ = _loan(seed_user)

        assert active_recurring_transfer_templates(
            account.id, seed_user["user"].id,
        ) == []
        assert active_recurring_transfer_template(
            account.id, seed_user["user"].id,
        ) is None


class TestEstimatedTierPricing:
    """What :func:`loan_plan` synthesizes for an installment no row covers."""

    def test_an_estimated_installment_takes_the_stated_base_over_the_contract(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A definition paying UNDER contract is modelled at what it pays.

        Before plan step R7d-a every ESTIMATED slot carried the contract's P&I,
        so a loan the owner is underpaying was projected to pay itself off on
        the servicer's schedule.
        """
        account, ctx = _loan(seed_user)
        under = _contractual_pi(account) - Decimal("500.00")
        _recurring_payment(seed_user, account, under, stated=True)

        plan = loan_plan(account, ctx)

        estimated = [p for p in plan.payments if p.is_estimated]
        assert estimated, "the loan has no rows, so every slot is ESTIMATED"
        assert {payment.cash for payment in estimated} == {under}

    def test_an_estimated_installment_states_its_own_escrow_beside_the_cash(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """The (cash, escrow) pair is the one the loan replay takes.

        A stated base is escrow-inclusive, so a tier calling its escrow ``0.00``
        would route the escrow into principal and pay the loan down by it every
        month.  $7,403.88 a year is $616.99 a month.
        """
        account, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        _recurring_payment(
            seed_user, account, Decimal("1910.95"), stated=True,
        )

        plan = loan_plan(account, ctx)

        estimated = [p for p in plan.payments if p.is_estimated]
        assert estimated
        for payment in estimated:
            assert payment.cash == Decimal("1910.95")
        # The escrow is the ACCRUAL's since plan step R16-a -- a month impounds
        # it, not a payment -- and the pair the allocator takes is still
        # (this cash, that charge).  A tier calling its escrow 0.00 would route
        # the escrow into principal exactly as before, so the assertion moves
        # with the value rather than weakening.  The charges are read off the
        # one timeline since plan step recurrence:R16-c-2 (ruling R-R100).
        assert {
            charge.escrow
            for charge in loan_timeline(account, ctx).stream.charges
        } == {Decimal("616.99")}

    def test_a_derived_installment_takes_the_contract_plus_escrow_and_extra(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """DERIVE mode keeps reading the contract -- that is what it means."""
        account, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        _recurring_payment(
            seed_user, account, Decimal("1.00"),
            derive=True, extra=Decimal("100.00"),
        )
        contractual_pi = _contractual_pi(account)

        plan = loan_plan(account, ctx)

        estimated = [p for p in plan.payments if p.is_estimated]
        assert estimated
        # The stored 1.00 base is NOT read; the contract's P&I is.
        expected = contractual_pi + Decimal("616.99") + Decimal("100.00")
        assert estimated[0].cash == expected

    def test_a_stated_price_is_resolved_AS_OF_each_occurrence(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """A price stated for a FUTURE date must not reach earlier occurrences.

        **The defect an adversarial review of plan step R7d-a found**, kept
        as a property of the plan now that the copy it was found in is gone.
        ``template.default_amount`` sits on the NEWEST price the series states
        rather than the price on a date -- measured on a production clone, an
        owner stating ``$700.00`` effective 2028-01-01 moved the Van Loan's
        derived payoff six installments EARLY once its future rows were
        absent.  The tier prices each occurrence through the amount model's
        series arm on the date its row would carry, so the rise reaches the
        06-01 installment and every later one, and no earlier one.
        """
        account, ctx = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, Decimal("300.00"), stated=True,
        )
        template_amount_service.set_amount(
            template, Decimal("700.00"), effective_on=date(2026, 6, 1),
        )
        db.session.flush()
        # The scalar has moved to the NEWEST price; the series has not.
        assert template.default_amount == Decimal("700.00")

        plan = loan_plan(account, ctx)

        by_due = {p.due_date: p.cash for p in plan.payments if p.is_estimated}
        assert by_due[date(2026, 5, 1)] == Decimal("300.00")
        assert by_due[date(2026, 6, 1)] == Decimal("700.00")
        assert by_due[date(2026, 7, 1)] == Decimal("700.00")

    def test_a_loan_with_no_recurring_payment_still_takes_the_contract(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """No definition -> the contract's P&I plus escrow, as before the step.

        The regression guard for the case R7d-a must NOT move: a loan the owner
        pays by hand has no stated figure, so the contract is the estimate.
        """
        account, ctx = _loan(seed_user, escrow_annual=Decimal("7403.88"))
        contractual_pi = _contractual_pi(account)

        plan = loan_plan(account, ctx)

        estimated = [p for p in plan.payments if p.is_estimated]
        assert estimated
        assert estimated[0].cash == contractual_pi + Decimal("616.99")
        assert {
            charge.escrow
            for charge in loan_timeline(account, ctx).stream.charges
        } == {Decimal("616.99")}


class TestPayoffDoesNotMoveWithMaterialisation:
    """The invariant plan step R7d-a exists to establish.

    A loan's projected payoff must be the same whether the rows its recurrence
    will generate have been WRITTEN yet or not.  Without it the recurrence's
    closing bound -- which is derived from that payoff -- reads one date at the
    moment generation resolves it and another afterwards, and the difference is
    payments the owner owes that are never budgeted.

    **The property is about FUTURE installments, and that bound is deliberate.**
    A row for an installment already due pays the loan down when it exists and
    is not re-synthesized when it does not, because the ESTIMATED tier never
    fills a past slot -- an overdue installment with no record pays nothing
    (finding B-9, the retired forward walk that amortized `-$15,755.38` per
    period).  So removing a PAST row legitimately moves the payoff later, and
    ``pay_period_admin.regenerate_pay_periods`` -- the door that presents this
    state -- only ever deletes periods that have not started.
    """

    def _materialised(self, seed_user, base_delta):
        """Build the loan, a payment *base_delta* off contract, and GENERATE its rows.

        Returns ``(account, ctx, future_row_ids)``.  The row ids are the test's
        positive control: a fixture that generated nothing would make the
        deletion below a no-op and the assertion a tautology, which is exactly
        how this test read before it was measured against a planted regression.
        """
        account, ctx = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, _contractual_pi(account) + base_delta,
            stated=True,
        )
        # A calendar reaching past the loan's last contractual installment
        # (2026-07-01), so the rule genuinely names every future slot.
        periods = record_paydays_across_a_hole(
            user_id=seed_user["user"].id,
            first_payday=date(2026, 1, 2),
            num_periods=20,
            rhythm=rhythm_of(14),
        )
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods},
            ),
            seed_user["scenario"].id,
        )
        db.session.flush()
        future = (
            db.session.query(Transfer.id)
            .filter(
                Transfer.to_account_id == account.id,
                Transfer.due_date >= _AS_OF,
            )
            .all()
        )
        future_ids = [row[0] for row in future]
        assert future_ids, (
            "the fixture must generate rows for the loan's FUTURE installments "
            "-- with none, deleting them proves nothing"
        )
        return account, ctx, future_ids

    def _delete(self, row_ids):
        """Delete the transfers *row_ids*, as a pay-period CASCADE would.

        Each took its two shadow rows first until plan step
        ``balance:X-bi-6-4d-2`` deleted the shadows; plan step
        ``balance:X-bi-6-5`` dropped the column they named it by.
        """
        db.session.execute(db.text(
            "DELETE FROM budget.transfers WHERE id = ANY(:ids)"
        ), {"ids": row_ids})
        db.session.expire_all()

    # Both non-zero deltas are sized so the FUTURE rows are what decides the
    # payoff, measured against a planted regression rather than reasoned about.
    # Too small and the difference rounds inside one installment; too large --
    # ``+2500.00`` was tried -- and the three ALREADY-DUE rows clear the loan on
    # their own, so deleting the future ones changes nothing and the case agrees
    # for the wrong reason.  ``0.00`` is the control, and it is the shape both of
    # the developer's live loans are in: a definition stating exactly what the
    # contract asks, where no pricing rule can make a difference.
    @pytest.mark.parametrize("base_delta", [
        Decimal("-500.00"),    # the definition pays UNDER contract
        Decimal("0.00"),       # the definition pays the contract exactly
        Decimal("1200.00"),    # the definition pays OVER contract
    ])
    def test_the_payoff_is_the_same_with_the_future_rows_absent(
        self, seed_user, db, base_delta,  # pylint: disable=unused-argument
    ):
        """Deleting every FUTURE payment row must not move the payoff.

        The state is not hypothetical: ``pay_period_admin.regenerate_pay_periods``
        deletes the rebuildable tail of pay periods and repopulates, and
        ``budget.transfers.pay_period_id`` CASCADEs -- so generation resolves
        the loan's closing bound against exactly this.  Measured on a production
        clone before this step: a Van payment standing at `$300.00` answered
        `2029-02-22` at that moment against `2030-02-22` before and after --
        twelve installments the owner owes, never generated.
        """
        account, ctx, future_ids = self._materialised(seed_user, base_delta)

        with_rows = balance_at.loan_payoff_date(account, ctx)
        self._delete(future_ids)
        without_rows = balance_at.loan_payoff_date(
            account, BalanceContext.build(seed_user["user"].id, _AS_OF),
        )

        assert without_rows == with_rows

    def test_the_payoff_is_the_same_however_far_the_horizon_reaches(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """An underpaying loan is modelled at its own figure for its WHOLE life.

        **This varies the HORIZON, which is the property its name states.** An
        earlier version deleted rows instead, making it a byte-copy of the case
        above; an adversarial review caught that the stated property was
        untested. The forward plan used to switch figures at the materialised
        horizon -- the owner's inside it, the servicer's past it -- so extending
        the pay calendar moved a payoff that nothing about the loan had changed.
        Measured on a production clone before the fix: `2030-02-22` with rows to
        2028-07 and `2030-04-22` with rows to 2029-01.
        """
        account, ctx = _loan(seed_user)
        template = _recurring_payment(
            seed_user, account, _contractual_pi(account) - Decimal("500.00"),
            stated=True,
        )
        # A SHORT calendar, but one already covering every ALREADY-DUE
        # installment (Feb / Mar / Apr, read at 2026-04-15).  That bound is the
        # test's subject: a past slot with no row pays nothing and the ESTIMATED
        # tier never refills it (finding B-9's fix, recorded as D46), so a
        # calendar that grew across ``as_of`` would move the payoff for that
        # reason instead of the one this test is about.  What the extension
        # below adds is FUTURE slots only.
        short = self._generate(seed_user, template, num_periods=8)
        near_horizon = balance_at.loan_payoff_date(account, ctx)

        # Now extend it well past that installment, so most of the plan is
        # PLANNED rows instead.  Nothing about the LOAN has changed.
        self._generate(
            seed_user, template, num_periods=20,
            first_payday=short[-1].start_date + timedelta(days=14),
        )
        far_horizon = balance_at.loan_payoff_date(
            account, BalanceContext.build(seed_user["user"].id, _AS_OF),
        )

        assert far_horizon == near_horizon

    def _generate(self, seed_user, template, num_periods, first_payday=None):
        """Create *num_periods* paydays and generate *template*'s rows into them.

        The opening block sits beside ``seed_user``'s 2024 payday, a hole the
        writer refuses (plan step ``pay_calendar:C17-c-2a``), so it goes
        through the tree's helper; a later block CONTINUES the record and is
        the writer's own.
        """
        record = (
            pay_period_write.record_paydays if first_payday is not None
            else record_paydays_across_a_hole
        )
        periods = record(
            user_id=seed_user["user"].id,
            first_payday=(
                date(2026, 1, 2) if first_payday is None else first_payday
            ),
            num_periods=num_periods,
            rhythm=rhythm_of(14),
        )
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods},
            ),
            seed_user["scenario"].id,
        )
        db.session.flush()
        return periods


class TestTheEscrowPairingFoldsToTheSameBalance:
    """The WASH claim, as behaviour rather than as two field assertions.

    Plan step R7d-a made the ESTIMATED tier state an installment's real escrow
    beside its cash where it used to pass ``0.00`` beside an escrow-free
    contractual P&I.  The ONE allocation
    (``app.utils.money.apply_payment_cash``) computes
    ``principal = cash - interest - escrow``, so adding the escrow to BOTH sides
    moves no balance -- and until this class that claim was pinned only by
    asserting the two FIELDS.  A reader who "restored" ``escrow=0.00`` while the
    cash stayed escrow-inclusive would pay a mortgage down by its whole escrow
    every month, and an adversarial review measured the entire balance, payoff,
    interest and equity suite staying green under exactly that mutation.
    """

    def test_an_escrowing_loan_folds_like_its_escrow_free_twin(
        self, seed_user, db,  # pylint: disable=unused-argument
    ):
        """Adding escrow to a contract-priced, CURRENT loan moves NO projected figure.

        Two identical loans, one escrowing `$7,403.88` a year (`$616.99` a
        month) and one escrowing nothing, both with no recurring payment so the
        whole plan is ESTIMATED.  The escrow is collected and disbursed by the
        servicer; it pays no principal, so the payoff and every projected
        balance must be identical.  Under the mutation this class exists for,
        the escrowing loan pays down `$616.99` a month faster.

        **Both loans originate on the read day**, so no installment is
        missed.  On the module's 2026-01-01 loan read at 2026-04-15 three
        months are unpaid, and since plan step R16-b-2 a skipped month accrues
        its charge -- interest AND escrow -- until a payment clears it (ruling
        R-R71); a catch-up payment at the level cash then clears the escrow
        arrears before principal, so a DELINQUENT escrowing loan genuinely
        pays down slower than its twin, which is the servicer's arithmetic and
        not the mutation.  The invariant this test states holds for a loan
        that is current, which is the loan it now builds.
        """
        escrowing = create_loan_account(
            seed_user, db.session, name="Escrowing",
            principal=_PRINCIPAL, rate=_RATE, term=_TERM,
            origination_date=_AS_OF, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        add_escrow_line(
            db.session, escrowing.id, "Property Tax", Decimal("7403.88"),
            effective_date=_AS_OF,
        )
        bare = create_loan_account(
            seed_user, db.session, name="No Escrow",
            principal=_PRINCIPAL, rate=_RATE, term=_TERM,
            origination_date=_AS_OF, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db.session.flush()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        dates = [date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)]
        assert balance_at.positions(escrowing, ctx, dates) == \
            balance_at.positions(bare, ctx, dates)
        assert balance_at.loan_payoff_date(escrowing, ctx) == \
            balance_at.loan_payoff_date(bare, ctx)
