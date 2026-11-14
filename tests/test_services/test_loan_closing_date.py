"""A loan's ONE closing date, past AND future (plan step ``recurrence:R7d-h``).

``balance_at.loan_payoff_date`` answers HALF its own question: it folds FORWARD
from the confirmed present seed, so a loan already at zero has no crossing left
and returns ``None`` -- which does not mean *never*, it means *not my half*.
``loan_closing_date`` composes that forward crossing with the BACKWARD one over
the loan's recorded events, so ``None`` means only *never pays off*.

The defect these tests exist for is the RETIRED branch.  Its bound used to be
the read pass's own ``as_of``, so a finished loan's stop moved with the day the
page was rendered -- and once ``recurrence:R7d-g`` NULLs the stored column,
nothing pins it at all.  ``test_a_retired_loans_closing_date_does_NOT_move_with_the_read``
is the one that fails on the old rule; every other test here would pass under
it, so it is what this file rests on.

All money is ``Decimal`` from strings.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import BaselineMissingError
from app.services import balance_at
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_loan_payment_template,
)


class TestLoanClosingDate:
    """The producer, against real loans."""

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today past every date these loans are read at."""
        freeze_today(monkeypatch, date(2026, 12, 25))

    def _ctx(self, seed_user, as_of):
        """A read pass at *as_of* for this owner."""
        return BalanceContext.build(seed_user["user"].id, as_of)

    def _cleared_loan(self, seed_user, db_session, on_date):
        """A $12,000 / 24-month / 5% loan trued to zero on *on_date*."""
        loan = create_loan_account(
            seed_user, db_session, name="Cleared Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"),
            term=24, origination_date=date(2026, 1, 1), payment_day=1,
        )
        insert_trueup_event(
            loan_params_for(db_session, loan.id), Decimal("0.00"),
            anchor_date=on_date,
        )
        return loan

    def test_a_retired_loans_closing_date_does_NOT_move_with_the_read(
        self, app, db, seed_user, seed_periods,
    ):
        """THE regression: a finished loan's stop is a fact about the LOAN.

        The old rule answered ``ctx.as_of`` for a retired loan, so this same
        untouched loan reported a different closing date every day it was
        looked at -- 2026-07-01 on one read and 2026-12-25 on the next, with
        nothing about the loan having changed between them.  Downstream that
        grew the admitted occurrence set by one per cadence period, so
        ``recurrence:R7d-c-2`` would write a fresh past-dated payment row per
        pass against a debt that is gone.

        Two reads of ONE loan at two as-ofs, both after it closed.  Asserting
        they are EQUAL is the whole test; asserting the value pins which date
        they agree on.
        """
        with app.app_context():
            loan = self._cleared_loan(seed_user, db.session, date(2026, 6, 15))
            db.session.commit()

            early = balance_at.loan_closing_date(
                loan, self._ctx(seed_user, date(2026, 7, 1)),
            )
            late = balance_at.loan_closing_date(
                loan, self._ctx(seed_user, date(2026, 12, 25)),
            )

            assert early == late, (
                "a retired loan's closing date moved with the read pass's "
                f"as-of ({early} then {late}) -- it is a fact about the loan"
            )
            assert early == date(2026, 6, 15), (
                "the closing date is the day the loan was cleared, "
                f"got {early}"
            )

    def test_a_retired_loan_closes_on_the_day_it_became_closed(
        self, app, db, seed_user, seed_periods,
    ):
        """The BACKWARD crossing, and the flag it makes unnecessary.

        ``payoff_date`` is ``None`` here and ``is_retired`` is True -- the pair
        every consumer used to have to read together.  ``closing_date`` states
        the answer on its own.
        """
        with app.app_context():
            loan = self._cleared_loan(seed_user, db.session, date(2026, 6, 15))
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))
            figures = balance_at.loan_figures(loan, ctx)

            assert figures.payoff_date is None, (
                "precondition: a retired loan has no FORWARD crossing left"
            )
            assert figures.is_retired is True, "precondition: it owes nothing"
            assert figures.closing_date == date(2026, 6, 15)

    def test_a_loan_closed_then_trued_back_UP_takes_the_LATER_crossing(
        self, app, db, seed_user, seed_periods,
    ):
        """Two crossings, and the rule takes the last (developer, 2026-09-03).

        The loan clears on 2026-03-01, an operator trues it back up to
        ``$1,200.00`` on 2026-04-01, and a second true-up clears it again on
        2026-05-01.  Taking the FIRST crossing would report the loan finished
        on 2026-03-01 and stop its recurrence there -- so the two months it
        genuinely owed ``$1,200.00`` would admit no occurrences, and a payment
        settling in that span would have no projected row behind it.  The
        later crossing is also what "the day it LAST became closed" means.
        """
        with app.app_context():
            loan = self._cleared_loan(seed_user, db.session, date(2026, 3, 1))
            params = loan_params_for(db.session, loan.id)
            insert_trueup_event(
                params, Decimal("1200.00"), anchor_date=date(2026, 4, 1),
            )
            insert_trueup_event(
                params, Decimal("0.00"), anchor_date=date(2026, 5, 1),
            )
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))

            assert balance_at.loan_closing_date(loan, ctx) == date(2026, 5, 1), (
                "a reopened loan must close on its LATER crossing, not the "
                "first one"
            )

    def test_a_loan_still_owing_closes_on_its_FORWARD_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The other half, unchanged: a live loan reports the projected payoff.

        Asserted against the seam's own ``payoff_date`` as well as the date, so
        this cannot pass by agreeing with a constant the seam has moved off.

        The loan originates 2026-12-01 with a ``payment_day`` of 1, so at the
        read pass's 2026-12-25 its first installment (2027-01-01) is still
        ahead and NOTHING is overdue -- the contractual payoff is the 24th
        installment, 2028-12-01.  Originating it earlier would leave unpaid
        overdue installments, which since finding B-9 no longer pay the loan
        down, so the fold would clear it LATER than the contract and the date
        here would be measuring delinquency rather than the forward crossing.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Live Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 12, 1), payment_day=1,
            )
            make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))
            figures = balance_at.loan_figures(loan, ctx)

            assert figures.is_retired is False, "precondition: it still owes"
            assert figures.closing_date == figures.payoff_date, (
                "a loan that still owes must report the FORWARD crossing "
                "itself, not a second derivation of it"
            )
            assert figures.closing_date == date(2028, 12, 1)

    def test_a_loan_that_never_pays_off_answers_None(
        self, app, db, seed_user, seed_periods,
    ):
        """``None`` now means one thing only.

        A $240,000 / 30-year contract at 6% trued up to $900,000, paid by a
        recurring payment at its level P&I: the ~$1,439 cannot cover $4,500 of
        monthly interest, so the balance grows and the fold never reaches
        zero.  The loan still OWES, so this ``None`` is not a retired loan's
        -- which is exactly the ambiguity the producer deletes.  It had no
        recurring payment until plan step recurrence:R16-f; such a loan is
        planned as its contract line now, whose last installment closes it
        with the balloon (ruling plan#3982), so it answers a date.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Never Clears",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2026, 1, 1),
            )
            make_loan_payment_template(db.session, seed_user, loan)
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("900000.00"),
                anchor_date=date(2026, 2, 1),
            )
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))
            figures = balance_at.loan_figures(loan, ctx)

            assert figures.is_retired is False, "precondition: it still owes"
            assert figures.closing_date is None


    def test_a_loan_NOT_YET_ORIGINATED_reports_its_forward_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The zero before a loan exists is not a closed loan.

        An unborrowed loan owes ``$0.00`` -- the fold's honest answer for a
        date with no facts -- and is emphatically NOT retired: its whole debt
        line is ahead of it.  The origination guard inside ``_is_retired`` is
        what separates the two, and it is load-bearing rather than defensive:
        without it this loan would take the BACKWARD branch and report a
        closing date in the past for a debt nobody has taken on yet.

        The deleted ``recurrence_end_date`` answered ``payoff_date`` for this
        loan (``is_retired`` being False), so this pins the case where the new
        composition must NOT differ.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Not Yet Borrowed",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2027, 3, 1), payment_day=1,
            )
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))
            figures = balance_at.loan_figures(loan, ctx)

            assert figures.terms.is_originated is False, (
                "precondition: the loan must NOT have been borrowed yet"
            )
            assert figures.is_retired is False, (
                "an unborrowed loan owes $0.00 and is still not retired"
            )
            assert figures.closing_date is not None, (
                "it must report the FORWARD crossing, not a past date and not "
                "the None that means never"
            )
            assert figures.closing_date == figures.payoff_date

    def test_a_loan_cleared_ON_its_origination_day_closes_that_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The first visible balance is already ``<= 0``, on ONE boundary.

        ``anchor_service`` admits ``anchor_date >= origination_date``, so a
        ``$0.00`` true-up can share the origination date -- and because the
        collapse gives a date ONE net balance, the two facts land on a single
        boundary that is already zero.  The scan treats the state before the
        first event as OPEN precisely so this reads as "closed that day"
        rather than as "never closed": there is no earlier boundary for the
        open-to-closed transition to be found at.

        (A zero OPENING anchor cannot produce this -- it is synthesized from
        ``original_principal``, which ``ck_loan_params_orig_principal``
        constrains to be positive.)
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Cleared At Birth",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 2, 1), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=date(2026, 2, 1),
            )
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))
            figures = balance_at.loan_figures(loan, ctx)

            assert figures.is_retired is True, "precondition: it owes nothing"
            assert figures.closing_date == date(2026, 2, 1), (
                "a loan whose only visible balance is already zero closed on "
                f"that day, got {figures.closing_date}"
            )

    def test_it_REFUSES_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-R30**: a producer needing a scenario refuses, never guesses.

        Pinned because the refusal arrives by a DIFFERENT mechanism here than
        it does through ``loan_figures``.  This entry evaluates the retired
        predicate first, so the raise comes out of ``ctx.loan_walk`` reading
        ``ctx.scenario_id`` rather than out of the payoff's ``require_scenario``
        -- same exception type, different path, and nothing else would notice
        if that path stopped raising.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="No Baseline",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 12, 1), payment_day=1,
            )
            db.session.commit()

            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None,
                as_of=date(2026, 12, 25),
            )
            with pytest.raises(BaselineMissingError):
                balance_at.loan_closing_date(loan, ctx)

    def test_a_NON_loan_account_fails_loud(
        self, app, db, seed_user, seed_periods,
    ):
        """It refuses rather than returning the ``None`` that means *never*.

        Returning ``None`` for "not a loan" would re-introduce exactly the
        overloaded absence this producer exists to delete.  A consumer that
        must tell a non-loan apart takes ``loan_figures``, which returns
        ``None`` for one.
        """
        with app.app_context():
            checking = create_account_of_type(
                seed_user, db.session, "Checking", "Everyday",
            )
            db.session.commit()

            ctx = self._ctx(seed_user, date(2026, 12, 25))

            assert balance_at.loan_figures(checking, ctx) is None
            with pytest.raises(ValueError, match="requires a configured loan"):
                balance_at.loan_closing_date(checking, ctx)
