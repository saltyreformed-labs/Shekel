"""A recurring transfer into a configured loan is bounded by the loan's start.

Card plan#2436, task plan#4087, ruling **plan#3954**: the recurrence books
floor asks where an account's books START (:func:`app.services.cash_ledger
.books_start`), so a configured loan bounds a recurring definition at its
origination, as its balance and every movement into it are, and its opening
RECORD bounds nothing.  Three readers ask it, and each is graded here:

* the floor a definition's walk starts above
  (``balance_at._definition_books.definition_books_opened_on``), which
  generation and the unarchive's walk both read;
* the bound the held-rows loader filters by
  (``definition_unarchive._could_be_held``), which must reach a loan whose
  origination is later than every opening record;
* the day a "stays deleted" sentence names an account by
  (``definition_unarchive._opened_on``), which must be the day the floor took.

The first two classes use production's shape: a loan originating 2026-01-01
whose record says 2026-03-20 (the Van Loan began 2023-02-14 and its record
says 2026-04-22).  The seeded owner's ten paychecks are paid every other
Friday from 2026-01-02 to 2026-05-08, and Checking's books open years before,
so the loan's books are the binding ones.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models.account import Account
from app.models.account_opening import AccountOpening
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import account_opening_fact, books_start
from app.services.recurring_definition import resolved_rule_of
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_a_late_opening_record,
    loan_params_for,
)
from tests.test_routes.test_archived_rows_bound_the_books import (
    _flashed,
    _monthly_save_into,
    _transfers,
)
from tests.test_services.test_opening_restatement_planned_rows import (
    _account_opened_early,
)

#: The loan's origination, the day its books start.
_ORIGINATED = date(2026, 1, 1)

#: Its opening RECORD, eleven weeks later.
_RECORDED = date(2026, 3, 20)

#: What the loan originated owing.
_PRINCIPAL = Decimal("5000.00")


def _a_loan_recorded_late(db, seed_user):
    """Return a configured loan whose opening record is later than its origination.

    Args:
        db: The ``db`` fixture.
        seed_user: The seeded user bundle.

    Returns:
        The committed loan :class:`~app.models.account.Account`.
    """
    loan = create_loan_with_a_late_opening_record(
        seed_user, db.session, principal=_PRINCIPAL, originated=_ORIGINATED,
        recorded=_RECORDED,
    )
    # The control every case leans on: a reader of the record would floor the
    # loan's rows at 2026-03-20, a reader of its start at 2026-01-01.
    assert account_opening_fact(loan.id).opened_on == _RECORDED
    assert books_start(loan.id).opened_on == _ORIGINATED
    return loan


def _due_days(rows):
    """Return the due days of *rows*, ascending."""
    return sorted(row.due_date for row in rows)


class TestTheFloorIsTheLoansStart:
    """A recurring transfer into the loan keeps the occurrences its record would drop."""

    def test_the_floor_is_the_origination_not_the_record(
        self, app, db, seed_user, seed_periods,
    ):
        """Every paycheck after the origination gets its transfer.

        All ten paydays fall after 2026-01-01, so all ten rows are written.
        Floored at the record, the six paid on or before 2026-03-20 (Jan 2
        to Mar 13) would not be, leaving four.
        """
        with app.app_context():
            loan = _a_loan_recorded_late(db, seed_user)

            template = _monthly_save_into(seed_user, seed_periods, loan)

            resolved = resolved_rule_of(
                template, BalanceContext.build(seed_user["user"].id),
            )
            assert resolved.books_opened_on == _ORIGINATED
            assert _due_days(_transfers(template.id, deleted=False)) == [
                period.start_date for period in seed_periods
            ]


class TestTheUnarchiveJudgesTheLoanByItsStart:
    """An archived transfer's hidden rows between the two days come back."""

    def test_every_hidden_row_after_the_origination_is_restored(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """All ten rows return, and the page names none as staying deleted.

        Judged by the record, the six rows due Jan 2 to Mar 13 would sit
        inside books opening 2026-03-20 and stay deleted, both by their own
        day and by the walk.
        """
        with app.app_context():
            loan = _a_loan_recorded_late(db, seed_user)
            template = _monthly_save_into(seed_user, seed_periods, loan)
            every = {row.id for row in _transfers(template.id)}
            # The control: every paycheck got its row, so the unarchive is
            # asked about the six in the window.
            assert len(every) == len(seed_periods) == 10
            assert auth_client.post(
                f"/transfers/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()
            # The control: the archive hid every row.
            assert {
                row.id for row in _transfers(template.id, deleted=True)
            } == every

            resp = auth_client.post(
                f"/transfers/{template.id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"deleted:" not in resp.data
            db.session.expire_all()
            assert {
                row.id for row in _transfers(template.id, deleted=False)
            } == every


class TestALoanStartingAfterEveryRecord:
    """Rows left on a loan that starts after every opening record are still held.

    A loan set up today for a closing next month: its record dates the
    account's creation, and its origination is the later day its terms name
    (the setup form takes a future origination).  The records here are the
    creation day's, 2026-03-20 (the today ``tests/test_services`` freezes),
    and the earlier restatements the fixtures append; the loan's origination
    becomes the first payday after every opening row the owner holds,
    2026-03-27.  Its recurring transfer was archived before the terms named
    that day and has since moved to Savings, so its hidden rows stayed on
    the loan (ruling R-PC99), and the seven due Jan 2 to Mar 27 sit inside
    the loan's books.
    """

    def test_the_rows_left_inside_its_books_stay_deleted_and_are_named(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The seven rows stay deleted, named as inside the loan's books.

        The held-rows loader filters by the latest day any of the owner's
        books start, and by the books of the accounts the definition names.
        The definition names Checking and Savings now, so only the loan's own
        start reaches 2026-03-27.  Read off the opening rows instead, the
        bound is 2026-03-20: the row due Mar 27 is due and filed after it, is
        never loaded, and comes back inside the loan's books, while the page
        names only six.  The sentence names the loan only when its day is
        read as the floor reads it.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                origination_date=_ORIGINATED,
            )
            savings = _account_opened_early(seed_user, name="Savings")
            template = _monthly_save_into(seed_user, seed_periods, loan)
            assert auth_client.post(
                f"/transfers/{template.id}/archive",
            ).status_code == 302
            latest_row = db.session.scalar(
                select(func.max(AccountOpening.opened_on))
                .join(Account, Account.id == AccountOpening.account_id)
                .where(Account.user_id == seed_user["user"].id)
            )
            later = min(
                period.start_date for period in seed_periods
                if period.start_date > latest_row
            )
            # The terms name the later closing.  No door re-dates an
            # origination, and the setup door's other writes are not what this
            # grades, so the day is moved on the stored terms.
            loan_params_for(db.session, loan.id).origination_date = later
            # The definition moves; its hidden rows stay where they sit, as an
            # account move leaves the rows no maintain pass reached.
            template = db.session.get(TransferTemplate, template.id)
            template.to_account_id = savings.id
            db.session.commit()
            # The control: the loan starts after every opening row, superseded
            # ones included, so only a bound reading its start can reach it.
            assert (latest_row, later) == (date(2026, 3, 20), date(2026, 3, 27))
            assert books_start(loan.id).opened_on == later
            held = {
                row.id for row in _transfers(template.id)
                if row.due_date <= later
            }
            # Jan 2, Jan 16, Jan 30, Feb 13, Feb 27, Mar 13 and Mar 27.
            assert len(held) == 7
            assert {row.to_account_id for row in _transfers(template.id)} == {
                loan.id,
            }

            resp = auth_client.post(
                f"/transfers/{template.id}/unarchive", follow_redirects=True,
            )

            assert resp.status_code == 200
            # Checking's books open 2024-01-04, so the sentence names only the
            # account whose books open on the day that holds the rows.
            assert _flashed(
                "7 items due 2026-01-02 to 2026-03-27 stay deleted: they fall "
                "inside Test Loan's books, which open 2026-03-27."
            ) in resp.data
            db.session.expire_all()
            assert held <= {
                row.id for row in _transfers(template.id, deleted=True)
            }
