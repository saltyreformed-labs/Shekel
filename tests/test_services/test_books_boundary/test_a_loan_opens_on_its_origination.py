"""A configured loan's books open on its origination, at the database tier.

Card plan#2436, task plan#3960, ruling **plan#3954**: for an account whose
type amortizes and that carries loan terms, ``budget.account_books_opened_on``
answers ``loan_params.origination_date``, and the movement, matched-line and
openings arms judge the loan by that day.  Three triggers grade the writes that
decide whether an account is such a loan.

Every case writes past the Python tier on purpose -- a raw statement, or a
row the ORM adds with no door in front of it -- because what is graded here is
the database's own refusal, and since task plan#3964 the Python tier refuses
the same states first (``test_the_service_judges_a_loan_by_its_start.py``).
Each refusal is paired with the same write made legal, so no case passes
against a trigger that refuses everything.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from app import ref_cache
from app.enums import AcctTypeEnum, InterestAccrualConventionEnum
from app.models.account_opening import AccountOpening
from app.models.loan_params import LoanParams
from app.services.cash_ledger import account_opening_fact
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
    match_two_lines,
    open_books_before_the_first_assertion,
    refused_by_database_rule,
)

from ._helpers import _ONE_DAY, _OPENED_ON_IN_SQL

#: The loan every case starts from: originated on the seeded calendar's eve.
_ORIGINATED = date(2026, 1, 1)

#: A payment into it, inside ``seed_periods[5]`` (2026-03-13 to 03-26).
_PAID_ON = date(2026, 3, 13)


def _books_day(db, account):
    """Return the day ``budget.account_books_opened_on`` gives *account*.

    Args:
        db: The ``db`` fixture.
        account: The account to ask about.

    Returns:
        The civil day, or ``None``.
    """
    return db.session.execute(
        sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
    ).scalar()


def _record_opening(db, account, opened_on):
    """Append an opening record for *account* dated *opened_on*, uncommitted.

    The ORM adds it with no door in front of it, which is how a loan's
    record comes to sit after its payments in these cases: the restate door
    refuses a loan, and the database is what is graded.

    Args:
        db: The ``db`` fixture.
        account: The account.
        opened_on: The record's day.
    """
    db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=opened_on,
        opening_equity=Decimal("0.00"),
        source_id=account_opening_fact(account.id).source_id,
    ))


def _paid_loan(db, seed_user, seed_periods, name):
    """Return a configured loan originated 2026-01-01 with one payment.

    Its books record opens before the origination and the calendar
    (``create_loan_account``), and the payment moves money on 2026-03-13.

    Args:
        db: The ``db`` fixture.
        seed_user: The seeded user bundle.
        seed_periods: The seeded periods.
        name: The loan's name.

    Returns:
        The committed loan :class:`~app.models.account.Account`.
    """
    loan = create_loan_account(
        seed_user, db.session, name=name, origination_date=_ORIGINATED,
    )
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, seed_periods[5],
        amount=Decimal("50.00"), settled_on=_PAID_ON,
    )
    db.session.commit()
    return loan


def _paid_loan_with_a_late_record(db, seed_user, seed_periods, name):
    """Return :func:`_paid_loan`'s loan with its opening record AFTER the payment.

    Production's shape: the Van Loan originated 2023-02-14 and its record
    opens 2026-04-22.  Legal while the loan is configured, because its books
    open on its origination.

    Args:
        db: The ``db`` fixture.
        seed_user: The seeded user bundle.
        seed_periods: The seeded periods.
        name: The loan's name.

    Returns:
        The committed loan :class:`~app.models.account.Account`.
    """
    loan = _paid_loan(db, seed_user, seed_periods, name)
    _record_opening(db, loan, _PAID_ON + timedelta(days=7))
    db.session.commit()
    return loan


class TestTheBooksDayFollowsTheBalancesDispatch:
    """``budget.account_books_opened_on`` asks both halves of a configured loan."""

    def test_a_configured_loan_opens_on_its_origination_not_its_record(
        self, app, db, seed_user, seed_periods,
    ):
        """The record says 2026-03-20; the loan's books open 2026-01-01."""
        with app.app_context():
            loan = _paid_loan_with_a_late_record(
                db, seed_user, seed_periods, "Configured",
            )

            assert account_opening_fact(loan.id).opened_on == date(2026, 3, 20)
            assert _books_day(db, loan) == _ORIGINATED

    def test_terms_on_a_type_that_does_not_amortize_are_not_a_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the ``has_amortization`` half.

        The balance refuses to amortize a ``loan_params`` row on such an
        account, so the boundary must not read its origination either.  Read
        inside the transaction that re-types it, then rolled back.
        """
        with app.app_context():
            loan = _paid_loan_with_a_late_record(
                db, seed_user, seed_periods, "Re-typed",
            )
            db.session.execute(sa.text(
                "UPDATE budget.accounts SET account_type_id = :t WHERE id = :a"
            ), {
                "t": ref_cache.acct_type_id(AcctTypeEnum.CHECKING),
                "a": loan.id,
            })

            assert _books_day(db, loan) == date(2026, 3, 20)
            db.session.rollback()

    def test_an_amortizing_account_without_terms_opens_on_its_record(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the ``loan_params`` half: a Mortgage whose terms
        were never entered is cash to the balance, and to the boundary."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, AcctTypeEnum.AUTO_LOAN.value,
                "Not Set Up",
            )
            db.session.commit()

            assert _books_day(db, account) == (
                account_opening_fact(account.id).opened_on
            )


class TestTheArmsJudgeALoanByItsOrigination:
    """The movement, matched-line and openings arms, with no change of their own."""

    def test_a_loans_record_may_be_restated_past_its_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """The openings arm: a cash account's books cannot move past a
        movement (``test_the_database_trigger.py``), and a loan's record can,
        because the record is not where its books open."""
        with app.app_context():
            loan = _paid_loan(db, seed_user, seed_periods, "Late Record")
            _record_opening(db, loan, _PAID_ON)
            db.session.commit()

            assert account_opening_fact(loan.id).opened_on == _PAID_ON

    def test_a_payment_on_or_before_the_origination_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the movement arm: ruling plan#3954's refusal.

        The loan originated 2026-01-01 and its payment settled 2026-03-13;
        re-dated onto the origination day itself, which the loan's full amount
        already holds (ruling plan#4040), the database refuses it at COMMIT.

        *It was a mortgage closing 2026-04-15 whose money moved 2026-03-10,
        settled through the transfer writer, until plan task plan#3964*, while
        the Python tier read the opening record and let the day through.  The
        service now refuses that write itself
        (``test_the_service_judges_a_loan_by_its_start.py``), and under the
        suite's clock (2026-03-20) a loan closing in April holds no payment a
        raw statement could re-date, so the case is rebuilt on a payment
        after the start (ruling plan#4040's rule for these cases) and re-dated
        past the service, the shape every other case in this module takes.
        """
        with app.app_context():
            loan = _paid_loan(db, seed_user, seed_periods, "Re-dated Payment")
            db.session.execute(sa.text(
                "UPDATE budget.transaction_entries "
                "SET settled_on = :d, purchased_on = :d WHERE account_id = :a"
            ), {"d": _ORIGINATED, "a": loan.id})
            with refused_by_database_rule(r"books open \(2026-01-01\)"):
                db.session.commit()
            db.session.rollback()

    def test_a_matched_line_after_the_origination_is_accepted_under_the_record(
        self, app, db, seed_user,
    ):
        """The matched-line arm, legal side: two lines between the origination
        and a later record, which the record alone would refuse."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Statement Loan",
                origination_date=_ORIGINATED,
            )
            _record_opening(db, loan, date(2026, 3, 20))
            db.session.commit()

            match_two_lines(
                db.session, loan, seed_user["user"].id,
                _ORIGINATED + _ONE_DAY, date(2026, 2, 1),
            )

            assert _books_day(db, loan) == _ORIGINATED

    def test_a_matched_line_on_the_origination_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the matched-line arm on a loan."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Early Statement",
                origination_date=_ORIGINATED,
            )
            with refused_by_database_rule(
                r"bank statement line .* books open \(2026-01-01\)",
            ):
                match_two_lines(
                    db.session, loan, seed_user["user"].id,
                    _ORIGINATED, date(2026, 2, 1),
                )
            db.session.rollback()


class TestTheWritesThatMakeALoanAreGraded:
    """The three attachments: terms, an account's type, a type's flag."""

    @staticmethod
    def _terms(account, origination_date):
        """Return a :class:`LoanParams` for *account* originating then.

        Args:
            account: The amortizing account.
            origination_date: The origination day.

        Returns:
            The unsaved row.
        """
        return LoanParams(
            account_id=account.id,
            original_principal=Decimal("1000.00"),
            interest_accrual_convention_id=(
                ref_cache.interest_accrual_convention_id(
                    InterestAccrualConventionEnum.MONTHLY_1_12,
                )
            ),
            term_months=24,
            origination_date=origination_date,
            payment_day=1,
        )

    @staticmethod
    def _paid_before_setup(db, seed_user, seed_periods):
        """Return an Auto Loan account, not set up, that records a payment.

        The setup door accepts payments recorded before setup
        (``routes/loan/params.py``), which is the state the terms trigger
        exists for.

        Args:
            db: The ``db`` fixture.
            seed_user: The seeded user bundle.
            seed_periods: The seeded periods.

        Returns:
            The committed account.
        """
        account = create_account_of_type(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN.value, "Pre-Setup",
        )
        db.session.flush()
        open_books_before_the_first_assertion(
            db.session, account, also_before=_ORIGINATED,
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[5], amount=Decimal("50.00"), settled_on=_PAID_ON,
        )
        db.session.commit()
        return account

    def test_terms_originating_on_a_recorded_payment_are_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the INSERT arm, the one a door reaches."""
        with app.app_context():
            account = self._paid_before_setup(db, seed_user, seed_periods)
            db.session.add(self._terms(account, _PAID_ON))
            with refused_by_database_rule(
                r"cannot open its books on 2026-03-13: a movement is already "
                r"dated 2026-03-13",
            ):
                db.session.commit()
            db.session.rollback()

    def test_terms_originating_before_a_recorded_payment_are_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The same write a day earlier."""
        with app.app_context():
            account = self._paid_before_setup(db, seed_user, seed_periods)
            db.session.add(self._terms(account, _PAID_ON - _ONE_DAY))
            db.session.commit()

            assert _books_day(db, account) == _PAID_ON - _ONE_DAY

    def test_moving_the_origination_onto_a_payment_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the UPDATE arm.  No door edits the origination
        (``LoanParamsUpdateSchema``), so this is a raw statement."""
        with app.app_context():
            loan = _paid_loan(db, seed_user, seed_periods, "Moved Origination")
            db.session.execute(sa.text(
                "UPDATE budget.loan_params SET origination_date = :d "
                "WHERE account_id = :a"
            ), {"d": _PAID_ON, "a": loan.id})
            with refused_by_database_rule(
                r"cannot open its books on 2026-03-13",
            ):
                db.session.commit()
            db.session.rollback()

    def test_deleting_terms_that_admitted_a_payment_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the DELETE arm: the account falls back to a
        record that sits after its payment."""
        with app.app_context():
            loan = _paid_loan_with_a_late_record(
                db, seed_user, seed_periods, "Deleted Terms",
            )
            db.session.execute(sa.text(
                "DELETE FROM budget.loan_params WHERE account_id = :a"
            ), {"a": loan.id})
            with refused_by_database_rule(
                r"cannot open its books on 2026-03-20",
            ):
                db.session.commit()
            db.session.rollback()

    def test_deleting_terms_whose_record_holds_the_payment_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The same delete when the record is before the payment."""
        with app.app_context():
            loan = _paid_loan(db, seed_user, seed_periods, "Kept Record")
            db.session.execute(sa.text(
                "DELETE FROM budget.loan_params WHERE account_id = :a"
            ), {"a": loan.id})
            db.session.commit()

            assert _books_day(db, loan) == (
                account_opening_fact(loan.id).opened_on
            )

    def test_retyping_a_loan_past_its_payment_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the ``budget.accounts`` attachment."""
        with app.app_context():
            loan = _paid_loan_with_a_late_record(
                db, seed_user, seed_periods, "Re-typed Loan",
            )
            db.session.execute(sa.text(
                "UPDATE budget.accounts SET account_type_id = :t WHERE id = :a"
            ), {
                "t": ref_cache.acct_type_id(AcctTypeEnum.CHECKING),
                "a": loan.id,
            })
            with refused_by_database_rule(
                r"cannot open its books on 2026-03-20",
            ):
                db.session.commit()
            db.session.rollback()

    def test_a_type_that_stops_amortizing_past_its_loans_payment_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL for the ``ref.account_types`` attachment: one edit
        re-kinds every account of the type, with no account row touched."""
        with app.app_context():
            loan = _paid_loan_with_a_late_record(
                db, seed_user, seed_periods, "Type Flipped",
            )
            db.session.execute(sa.text(
                "UPDATE ref.account_types SET has_amortization = false, "
                "max_term_months = NULL WHERE id = :t"
            ), {"t": loan.account_type_id})
            with refused_by_database_rule(
                r"cannot open its books on 2026-03-20",
            ):
                db.session.commit()
            db.session.rollback()


class TestAnAccountEditDoesNotReserveTheTable:
    """The ``budget.accounts`` attachment's WHEN clause, graded by DDL.

    A deferred constraint trigger queues its event at statement time, and
    PostgreSQL refuses ``ALTER TABLE`` on a table carrying a pending event
    (``test_the_database_trigger.py`` states the cost).  ``budget.accounts``
    is written on every rename, so only a real change of type may queue one.
    """

    _DDL = "ALTER TABLE budget.accounts ENABLE TRIGGER audit_accounts"

    def test_a_rename_leaves_ddl_on_the_table_legal(
        self, app, db, seed_user,
    ):
        """A write naming ``account_type_id`` with its own value queues nothing."""
        with app.app_context():
            account = seed_user["account"]
            db.session.execute(sa.text(
                "UPDATE budget.accounts SET name = 'Renamed', "
                "account_type_id = account_type_id WHERE id = :a"
            ), {"a": account.id})
            db.session.execute(sa.text(self._DDL))
            db.session.rollback()

    def test_a_real_retype_still_reserves_it(
        self, app, db, seed_user,
    ):
        """The control: the case above passes because of WHEN, not because the
        DDL is always legal."""
        with app.app_context():
            account = seed_user["account"]
            db.session.execute(sa.text(
                "UPDATE budget.accounts SET account_type_id = :t WHERE id = :a"
            ), {
                "t": ref_cache.acct_type_id(AcctTypeEnum.SAVINGS),
                "a": account.id,
            })
            with pytest.raises(OperationalError, match="pending trigger events"):
                db.session.execute(sa.text(self._DDL))
            db.session.rollback()
