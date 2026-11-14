"""The seam's derived payoff on real loans: against the contract, past it, and carried by the
figures.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import balance_at
from app.services.balance_at._positions import memoized_payoff
from app.services.installment_calendar import final_installment_date
from app.services.balance_at import BalanceContext
from app.utils.dates import add_months
from tests._test_helpers import insert_trueup_event

from ._helpers import (
    _PRINCIPAL,
    _create_loan,
    _attach_derive_extra,
    _reference_payoffs,
    _current_period,
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

        With no confirmed past and no extra, its plan is the contract line, so
        the derived payoff is the contract's last installment -- the
        contractual payoff, a future date
        (:func:`~app.services.installment_calendar.final_installment_date`;
        the from-origination schedule's last row until plan step
        recurrence:R16-f deleted it).
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
            expected = final_installment_date(
                loan_params.origination_date, loan_params.payment_day,
                loan_params.term_months,
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) == expected

    def test_non_loan_account_raises(self, app, seed_user):
        """A non-loan account is a caller error -- fail loud, like ``positions``."""
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            with pytest.raises(ValueError, match="requires a configured loan"):
                balance_at.loan_payoff_date(seed_user["account"], ctx)


class TestPayoffTailExtension:
    """C8c (N-16): a recurring payment pays past the contractual date until the loan clears.

    A loan with NO recurring payment is planned as its contract line, whose
    last installment closes it at maturity (ruling **plan#3982**), so the
    window past the contract is a recurring payment's alone, and every case
    here but the balloon's pays by one: a derive-mode payment at the level
    P&I with no extra.  Until plan step recurrence:R16-f these fixtures had
    no recurring payment, and the contract-priced estimate carried sixty
    level payments past the term instead.
    """

    def test_underpaying_loan_clears_in_the_extension(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan behind the schedule clears a few months PAST the contractual date.

        A true-up leaves the balance $500 above where the contractual schedule
        expects it (an underpayment / drift), so the fold does not reach zero at
        the contractual installment.  Before C8c that residue made
        ``loan_payoff_date`` return ``None``; the recurring payment's
        occurrences past the contract now clear it, so it derives a REAL date
        -- and a LATER one than the resolver's ``is_last_month``-forced
        contractual payoff (the deliberate N-16 move).
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Underpaid",
            )
            _attach_derive_extra(seed_user, account, Decimal("0.00"))
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
            # which two level-payment (~$1,798 P&I) occurrences past it clear
            # (~$1,783 principal each), so payoff lands two months past the
            # engine's forced contractual date.
            assert derived == add_months(contractual_payoff, 2), (
                f"underpaid payoff {derived} should be two extension installments "
                f"past the engine's forced contractual {contractual_payoff}"
            )

    def test_a_loan_with_no_recurring_payment_closes_at_maturity(
        self, app, seed_user, seed_periods_today,
    ):
        """The same $500 behind, with no recurring payment: paid off on the contract's date.

        Ruling **plan#3982**: the loan is planned as its contract line, so
        every installment bills the level P&I and the term's last closes it
        with the balloon, whatever the residue has grown to.  The payoff is
        the contractual date, and the balance there 0.00; until plan step
        recurrence:R16-f the plan's sixty level payments past the term
        cleared it two installments later, the case above's figure.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Balloon",
            )
            insert_trueup_event(loan_params, _PRINCIPAL + Decimal("500.00"))
            db.session.commit()
            _, contractual_payoff = _reference_payoffs(
                loan_params, seed_user["scenario"].id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) == contractual_payoff
            assert balance_at.balance_at(account, ctx, contractual_payoff) == (
                Decimal("0.00")
            )

    def test_severe_underpayment_never_amortizes_returns_none(
        self, app, seed_user, seed_periods_today,
    ):
        """A balance so high the level payment cannot cover its interest -- ``None``.

        Trued up to $400k against a $300k contractual P&I (~$1,798), the monthly
        interest (~$2,000) EXCEEDS the payment, so the balance grows every month
        and the recurring payment never clears it: the fold reports ``None``
        rather than inventing a payoff.  (The resolver's ``is_last_month`` would
        force a date by absorbing the grown balance in a phantom final payment;
        the fold does not, and nor does the plan of a loan its recurring payment
        pays.)
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Negam",
            )
            _attach_derive_extra(seed_user, account, Decimal("0.00"))
            insert_trueup_event(loan_params, Decimal("400000.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) is None

    def test_healthy_loan_is_not_resurrected_past_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A healthy loan's balance stays $0 past its payoff -- the window is inert.

        The recurring payment's occurrences run PAST the contractual date; on a
        healthy loan (already zero there) they must fold to no-ops.  A year past the
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
            _attach_derive_extra(seed_user, account, Decimal("0.00"))
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
        A loan its recurring payment pays, trued up $500 above the contractual
        schedule, clears two installments LATER than the resolver's
        ``is_last_month``-forced contractual payoff (the C8c window), so the two
        answers differ and the figure must carry the fold's.  It had no
        recurring payment until plan step recurrence:R16-f; such a loan closes
        at maturity now (ruling plan#3982), where the two answers agree.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Cutover Underpaid",
            )
            _attach_derive_extra(seed_user, account, Decimal("0.00"))
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
