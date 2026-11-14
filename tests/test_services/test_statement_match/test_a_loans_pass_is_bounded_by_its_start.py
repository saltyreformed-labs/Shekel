"""A review pass over a configured loan bounds its lines at the loan's start.

Card plan#2436, task plan#4085, ruling **plan#3954**: the statement-match pass
asks where an account's books START (:func:`app.services.cash_ledger
.required_books_start`), so a configured loan's lines are split and refused at
its origination, where its movements are, and its opening RECORD bounds
nothing.  Two producers ask it: the pass (``ReviewScope.build``, which the
screen splits on and every door refuses on) and the grid's badge
(``awaiting_review_count``), so each is graded here.

**No door builds a pass for a loan**: each loads its account through
``load_cash_account_or_404``, and the grid's control gates on
``serves_cash_detail``.  So these cases call the service, and they grade the
DAY and the FIGURE the bound and the refusal carry, never their wording,
which is a cash account's (``cash_ledger.reject_line_before_books_open``).

Every loan here originated 2026-01-01 owing $5,000.00 and carries an opening
record dated 2026-03-20, production's shape (the Van Loan began 2023-02-14 and
its record says 2026-04-22).  The seeded owner's pay calendar opens
2024-01-05, so no line here falls before the calendar and the books bound is
the one that holds a line.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.services.cash_ledger import account_opening_fact
from app.services.statement_match import awaiting_review_count, review_set
from tests._test_helpers import create_loan_with_a_late_opening_record

from ._builders import a_bank_line, a_scope, an_import

#: The loan's origination, the day its books start.
_ORIGINATED = date(2026, 1, 1)

#: Its opening RECORD, ten weeks later.
_RECORDED = date(2026, 3, 20)

#: A day after the origination and before the record: the window the two
#: readers disagree on.
_BETWEEN = date(2026, 3, 13)

#: What the loan originated owing.
_PRINCIPAL = Decimal("5000.00")


def _a_loan_with_two_lines(db, seed_user):
    """Return a loan with a bank line on its origination and one inside the window.

    Args:
        db: The ``db`` fixture.
        seed_user: The seeded user bundle.

    Returns:
        ``(loan, held, work)``: the loan, the line posted on its origination
        and the line posted on :data:`_BETWEEN`.
    """
    loan = create_loan_with_a_late_opening_record(
        seed_user, db.session, principal=_PRINCIPAL, originated=_ORIGINATED,
        recorded=_RECORDED,
    )
    statement = an_import(seed_user, account=loan)
    held = a_bank_line(
        seed_user, statement, posted_on=_ORIGINATED, sequence_in_group=0,
    )
    work = a_bank_line(
        seed_user, statement, posted_on=_BETWEEN, sequence_in_group=1,
    )
    # The control every case leans on: the record names a later day than the
    # start, so a reader of the record would have held BOTH lines.
    assert account_opening_fact(loan.id).opened_on == _RECORDED
    return loan, held, work


class TestTheSplitJudgesALoanByItsStart:
    """The screen's half: which lines the pass holds back, and the badge's count."""

    def test_only_the_line_on_the_origination_is_held(
        self, app, db, seed_user,
    ):
        """The bound is the loan's start, owing its principal; the window's line is work.

        Read off the record, the bound would name 2026-03-20 holding $0.00 and
        hold both lines.
        """
        with app.app_context():
            loan, held, work = _a_loan_with_two_lines(db, seed_user)

            review = review_set(a_scope(seed_user, account=loan))
            bound = review.bounds.books

            assert bound.opened_on == _ORIGINATED
            assert bound.opening_equity == _PRINCIPAL
            assert bound.count == 1
            assert bound.last_day == held.posted_on
            # The sentence is composed from the start's day and figure.
            assert "Jan 1, 2026" in bound.said
            assert "$5,000.00" in bound.said
            assert [line.line_id for line in review.unmatched] == [work.id]

    def test_the_grid_badge_counts_the_windows_line(
        self, app, db, seed_user,
    ):
        """``awaiting_review_count`` reads the start too: 1 line of work, not 0.

        The badge asks the producer itself rather than off a pass, so it is
        a second site to grade.  The calendar's first day is passed as the
        grid passes it.
        """
        with app.app_context():
            loan, _held, _work = _a_loan_with_two_lines(db, seed_user)

            assert awaiting_review_count(
                seed_user["user"].id, loan.id,
                seed_user["bootstrap_period"].start_date,
            ) == 1


class TestTheDoorsRefuseALoanLineByItsStart:
    """The doors' half: the pass's own refusal, which every door asks."""

    def test_a_line_on_the_origination_is_refused_naming_the_start(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the start day is inside the loan's full amount
        (ruling plan#4040), and the refusal names that day and that amount."""
        with app.app_context():
            loan, _held, _work = _a_loan_with_two_lines(db, seed_user)
            scope = a_scope(seed_user, account=loan)

            with pytest.raises(ValidationError) as refused:
                scope.reject_line_before_books_open(_ORIGINATED, "this match")

            assert (
                "Your bank posted this line on 2026-01-01, and this account's "
                "books open on 2026-01-01 holding $5,000.00"
            ) in str(refused.value)

    def test_a_line_after_the_origination_and_before_the_record_is_accepted(
        self, app, db, seed_user,
    ):
        """The record would refuse 2026-03-13; the loan's start does not."""
        with app.app_context():
            loan, _held, _work = _a_loan_with_two_lines(db, seed_user)
            scope = a_scope(seed_user, account=loan)

            scope.reject_line_before_books_open(_BETWEEN, "this match")
