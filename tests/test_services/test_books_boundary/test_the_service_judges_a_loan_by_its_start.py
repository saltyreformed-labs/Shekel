"""A configured loan's books start on its origination, at the Python tier.

Card plan#2436, task plan#3964, ruling **plan#3954**: the service asks where an
account's books START (:func:`app.services.cash_ledger.books_start`) with the
balance's own dispatch, so it refuses exactly what the database tier
(``test_a_loan_opens_on_its_origination.py``) refuses.  Every loan here carries
an opening RECORD later than its origination, which is production's shape (the
Van Loan began 2023-02-14 and its record says 2026-04-22): a reader of the
record and a reader of the loan's start answer differently on every day
between the two, and each case is placed in that window or on its edge.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import AcctTypeEnum
from app.exceptions import ValidationError
from app.models.account_opening import AccountOpening
from app.models.transaction_entry import TransactionEntry
from app.services.cash_ledger import (
    BooksStart,
    account_opening_fact,
    books_start,
    reject_movement_before_books_open,
    required_books_start,
)
from tests._test_helpers import (
    account_never_asserted,
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
)

from ._helpers import _ONE_DAY

#: The loan's origination: the seeded calendar's eve.
_ORIGINATED = date(2026, 1, 1)

#: Its opening RECORD, ten weeks later.
_RECORDED = date(2026, 3, 20)

#: A day inside ``seed_periods[5]`` (2026-03-13 to 03-26), after the
#: origination and before the record: the window the two readers disagree on.
_BETWEEN = date(2026, 3, 13)

#: What the loan originated owing.
_PRINCIPAL = Decimal("5000.00")


def _loan_with_a_late_record(db, seed_user, name):
    """Return a configured loan originated 2026-01-01, its record dated 2026-03-20.

    ``create_loan_account`` opens the record before the origination; the
    later record is appended with no door in front of it, since the restate
    door refuses a loan.  The database accepts it: the loan records no money,
    and its books open on its origination anyway (plan task plan#3960).

    Args:
        db: The ``db`` fixture.
        seed_user: The seeded user bundle.
        name: The loan's name.

    Returns:
        The committed loan :class:`~app.models.account.Account`.
    """
    loan = create_loan_account(
        seed_user, db.session, name=name, principal=_PRINCIPAL,
        origination_date=_ORIGINATED,
    )
    db.session.add(AccountOpening(
        account_id=loan.id,
        opened_on=_RECORDED,
        opening_equity=Decimal("0.00"),
        source_id=account_opening_fact(loan.id).source_id,
    ))
    db.session.commit()
    return loan


def _record_day(loan):
    """Return the day *loan*'s governing opening record names.

    Args:
        loan: The loan account.

    Returns:
        The record's ``opened_on``.
    """
    return account_opening_fact(loan.id).opened_on


class TestTheStartFollowsTheBalancesDispatch:
    """``books_start`` asks both halves of a configured loan, as the balance does."""

    def test_a_configured_loan_starts_on_its_origination_owing_its_principal(
        self, app, db, seed_user,
    ):
        """The record says 2026-03-20; the loan's books start 2026-01-01 owing $5,000.00."""
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Configured")

            expected = BooksStart(
                opened_on=_ORIGINATED, figure=_PRINCIPAL, is_loan_start=True,
            )
            assert required_books_start(loan.id) == expected
            assert books_start(loan.id) == expected
            # The control: the record is a different day, so the answer above
            # is the dispatch's and not the record's.
            assert _record_day(loan) == _RECORDED

    def test_terms_on_a_type_that_does_not_amortize_start_on_the_record(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the KIND half.

        Loan terms on an account whose type does not amortize are not a loan to
        the balance, so its books start on its record.  Re-typed inside the
        transaction and rolled back; the account is expired so its type is
        read again, as a later request would read it.
        """
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Re-typed")
            db.session.execute(sa.text(
                "UPDATE budget.accounts SET account_type_id = :t WHERE id = :a"
            ), {
                "t": ref_cache.acct_type_id(AcctTypeEnum.CHECKING),
                "a": loan.id,
            })
            db.session.expire(loan)

            start = required_books_start(loan.id)

            assert start == BooksStart(
                opened_on=_RECORDED, figure=Decimal("0.00"),
                is_loan_start=False,
            )
            db.session.rollback()

    def test_an_amortizing_account_without_terms_starts_on_its_record(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the TERMS half: a loan type whose terms were never
        entered is cash to the balance, and starts on its record."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, AcctTypeEnum.AUTO_LOAN.value,
                "Not Set Up", anchor_balance=Decimal("250.00"),
            )
            db.session.commit()
            opening = account_opening_fact(account.id)

            assert required_books_start(account.id) == BooksStart(
                opened_on=opening.opened_on, figure=opening.opening_equity,
                is_loan_start=False,
            )

    def test_an_account_with_no_record_and_no_terms_has_no_start(
        self, app, db, seed_user,
    ):
        """The two empty-state policies: ``None`` from the floor's producer, a
        refusal from the refusals', where the SQL tier answers NULL."""
        with app.app_context():
            account = account_never_asserted(seed_user, db.session, name="Bare")
            db.session.flush()

            assert books_start(account.id) is None
            with pytest.raises(RuntimeError, match="zero"):
                required_books_start(account.id)


class TestTheMovementRefusalJudgesALoanByItsStart:
    """``reject_movement_before_books_open`` and the writer that reaches it."""

    def test_a_day_after_the_origination_and_before_the_record_is_accepted(
        self, app, db, seed_user,
    ):
        """The record would refuse 2026-03-13; the loan's start does not."""
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Window")

            assert _BETWEEN <= _record_day(loan)
            reject_movement_before_books_open(loan.id, _BETWEEN)

    def test_the_origination_day_is_refused_naming_the_loans_start(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the start day itself is inside the loan's full amount
        (ruling plan#4040), and the sentence names the loan, not an opening."""
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Start Day")

            with pytest.raises(ValidationError) as refused:
                reject_movement_before_books_open(loan.id, _ORIGINATED)

            assert str(refused.value) == (
                "Money cannot have moved on 2026-01-01: this loan started on "
                "2026-01-01 owing $5,000.00, and that is what it owed at the "
                "end of its first day -- so anything that moved by then is "
                "already inside it.  Date it after 2026-01-01."
            )

    def test_the_day_after_the_origination_is_accepted(
        self, app, db, seed_user,
    ):
        """The other side of the edge, so neither case passes off by one."""
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Day After")

            reject_movement_before_books_open(loan.id, _ORIGINATED + _ONE_DAY)

    def test_a_payment_settled_inside_the_window_commits(
        self, app, db, seed_user, seed_periods,
    ):
        """Through the transfer writer: a $50.00 payment settled 2026-03-13 into
        the loan lands and commits, the database agreeing at COMMIT.  Before
        plan task plan#3964 the leg's settle (``status_seam._side``) read the
        record and refused it."""
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Paid In Window")

            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[5], amount=Decimal("50.00"), settled_on=_BETWEEN,
            )
            db.session.commit()

            legs = db.session.query(TransactionEntry.settled_on).filter(
                TransactionEntry.account_id == loan.id,
            ).all()
            assert legs == [(_BETWEEN,)]

    def test_a_payment_settled_on_the_origination_is_refused_before_commit(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL through the same writer: the service refuses with the
        loan's sentence, so no write reaches the database's backstop."""
        with app.app_context():
            loan = _loan_with_a_late_record(db, seed_user, "Paid On Start")

            with pytest.raises(
                ValidationError, match=r"this loan started on 2026-01-01",
            ):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan,
                    seed_periods[0], amount=Decimal("50.00"),
                    settled_on=_ORIGINATED,
                )
            db.session.rollback()

            assert db.session.query(TransactionEntry).filter(
                TransactionEntry.account_id == loan.id,
            ).count() == 0
