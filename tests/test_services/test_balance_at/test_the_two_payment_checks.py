"""The loan's two yes/no payment checks read its ONE record (plan step recurrence:R16-e).

Ruling R-R93: :attr:`~app.services.balance_at.LoanFigures.is_paid_off` ("retired, and a
payment behind it") and :attr:`~app.services.balance_at.LoanFigures.has_own_plan` (the
payoff calculator's "this loan has a plan of its own") read the pass's facts walk, the
resolved loan's definitions and the pass's forward plan.  They read the loan's payment
LIST until that step, which existed only to feed a walk the step deleted.

Every case is one loan, ``$1,000.00`` at 5% over 24 months, originated 2026-01-01 and due
on the 1st, read on 2026-03-20 (the suite's frozen today, ``tests/test_services/conftest.py``,
and the pass's pinned ``as_of``), varying only what stands behind it: a settled payment, a
projected row, a recurring definition, or nothing.  Each test also states the three facts
the checks read, so it says which arm answered and cannot pass on another one.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.balance_at._plan import memoized_plan
from app.services.balance_at._resolution import resolved_loan
from app.services.loan_loaders import projected_income_legs
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    create_transfer,
    insert_trueup_event,
    make_loan_payment_template,
)

#: The day every pass reads on: the suite's frozen today, inside period 5 (2026-03-13).
_READ_ON = date(2026, 3, 20)
#: The day the retired loans are trued to ``$0.00``: before the read, after the settled
#: payment, and with no installment between it and the read (the next is 2026-04-01).
_CLEARED_ON = date(2026, 3, 15)


def _loan(seed_user, db_session):
    """The loan every case reads: $1,000.00 at 5% over 24 months, from 2026-01-01, due the 1st."""
    return create_loan_account(
        seed_user, db_session, name="Checked Loan",
        principal=Decimal("1000.00"), rate=Decimal("0.05000"), term=24,
        origination_date=date(2026, 1, 1), payment_day=1,
    )


def _settle_a_payment(seed_user, db_session, loan, periods):
    """A $100.00 payment for the 2026-03-01 installment, settled 2026-02-13 (period 3)."""
    create_settled_transfer(
        seed_user, db_session, seed_user["account"], loan, periods[3],
        amount=Decimal("100.00"), settled_on=date(2026, 2, 13),
        due_date=date(2026, 3, 1),
    )


def _project_a_row(seed_user, db_session, loan, period, due):
    """A $100.00 projected payment row in *period* for the installment due *due*."""
    create_transfer(
        seed_user, db_session, seed_user["account"], loan, period,
        amount=Decimal("100.00"), due_date=due,
    )


def _read(seed_user, loan):
    """The loan's figures and the three facts they read, on :data:`_READ_ON`.

    Returns:
        ``(figures, recorded, definitions, planned)``: the seam's
        :class:`~app.services.balance_at.LoanFigures`, how many payments the
        pass's facts walk holds, how many definitions pay into the loan, and
        the due dates of the forward plan's projected-row payments.
    """
    ctx = BalanceContext.build(seed_user["user"].id, as_of=_READ_ON)
    figures = balance_at.loan_figures(loan, ctx)
    return (
        figures,
        len(ctx.loan_walk(loan).settled_splits),
        len(resolved_loan(loan, ctx).definitions),
        [
            payment.due_date for payment in memoized_plan(loan, ctx).payments
            if not payment.is_estimated
        ],
    )


class TestIsPaidOffReadsTheRecord:
    """``is_paid_off`` is the retired loan with a payment in its record.

    Every loan here is RETIRED: trued to ``$0.00`` on 2026-03-15, so the fold of its
    record on 2026-03-20 is that ``$0.00`` (no installment is charged between the
    two; the next is due 2026-04-01).  Only what pays into it varies, so the
    payment guard is the one thing graded.
    """

    def test_a_settled_payment_is_paid_off(self, app, db, seed_user, seed_periods):
        """A payment settled 2026-02-13 is in the record: retired and paid off."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _settle_a_payment(seed_user, db.session, loan, seed_periods)
            insert_trueup_event(loan.loan_params, Decimal("0.00"), _CLEARED_ON)
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (1, 0, [])
            assert figures.is_retired is True
            assert figures.is_paid_off is True

    def test_a_projected_row_only_is_not_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """A row due 2026-05-01 that has not settled is no payment in the record."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _project_a_row(
                seed_user, db.session, loan, seed_periods[7], date(2026, 5, 1),
            )
            insert_trueup_event(loan.loan_params, Decimal("0.00"), _CLEARED_ON)
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (0, 0, [date(2026, 5, 1)])
            assert figures.is_retired is True
            assert figures.is_paid_off is False

    @pytest.mark.usefixtures("seed_periods")
    def test_a_definition_only_is_not_paid_off(self, app, db, seed_user):
        """A recurring payment with no row generated is no payment in the record."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            make_loan_payment_template(db.session, seed_user, loan)
            insert_trueup_event(loan.loan_params, Decimal("0.00"), _CLEARED_ON)
            db.session.commit()

            figures, recorded, definitions, _planned = _read(seed_user, loan)

            assert (recorded, definitions) == (0, 1)
            assert figures.is_retired is True
            assert figures.is_paid_off is False

    @pytest.mark.usefixtures("seed_periods")
    def test_nothing_behind_it_is_not_paid_off(self, app, db, seed_user):
        """A loan trued to $0.00 with nothing paid into it is retired, never paid off.

        The degenerate case the guard exists for: a ``$0`` assertion with no payment
        is a misconfiguration or a lump sum recorded as a true-up, not an
        achievement to badge.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            insert_trueup_event(loan.loan_params, Decimal("0.00"), _CLEARED_ON)
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (0, 0, [])
            assert figures.is_retired is True
            assert figures.is_paid_off is False


class TestHasOwnPlan:
    """``has_own_plan``: a payment in the record, a definition, or a planned row.

    Every loan here still owes its ``$1,000.00`` opening less whatever settled, so
    each case grades the arm that answers and the plan the payoff calculator
    compares with the contract.
    """

    def test_a_settled_payment_is_a_plan(self, app, db, seed_user, seed_periods):
        """The record's payment answers alone: no definition and no planned row."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _settle_a_payment(seed_user, db.session, loan, seed_periods)
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (1, 0, [])
            assert figures.has_own_plan is True

    def test_a_projected_row_only_is_a_plan(self, app, db, seed_user, seed_periods):
        """A row due 2026-05-01, after the loan's only assertion (its 2026-01-01
        opening), is a payment the forward plan folds: the plan's arm answers."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _project_a_row(
                seed_user, db.session, loan, seed_periods[7], date(2026, 5, 1),
            )
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (0, 0, [date(2026, 5, 1)])
            assert figures.has_own_plan is True

    @pytest.mark.usefixtures("seed_periods")
    def test_a_definition_only_is_a_plan(self, app, db, seed_user):
        """A recurring payment with no row generated: the definitions' arm answers."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (0, 1, [])
            assert figures.has_own_plan is True

    @pytest.mark.usefixtures("seed_periods")
    def test_nothing_behind_it_is_no_plan(self, app, db, seed_user):
        """With none of the three, the plan is the contract's own installments."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert (recorded, definitions, planned) == (0, 0, [])
            assert figures.has_own_plan is False

    def test_a_row_its_true_up_covers_is_no_plan(self, app, db, seed_user, seed_periods):
        """A row due 2026-02-01, inside a $950.00 true-up dated 2026-02-15, is no plan.

        The one case where the record reads differently from the payment list it
        replaced, and the reason it is right.  The forward plan does not fold a
        payment due at or before the loan's latest assertion (ruling R-R72: the
        assertion already states the balance after it), so with nothing else the
        plan is the contract's own installments, and the payoff calculator would
        compare the contract with itself.  The list held the row, so the check
        said yes; the row is still there, unsettled, as the loader shows.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _project_a_row(
                seed_user, db.session, loan, seed_periods[1], date(2026, 2, 1),
            )
            insert_trueup_event(
                loan.loan_params, Decimal("950.00"), date(2026, 2, 15),
            )
            db.session.commit()

            figures, recorded, definitions, planned = _read(seed_user, loan)

            assert len(projected_income_legs(
                loan.id, seed_user["scenario"].id, options=(),
            )) == 1
            assert (recorded, definitions, planned) == (0, 0, [])
            assert figures.has_own_plan is False
