"""What an account's books opened with, as a recorded fact (plan step X-f3c-2a).

Grades ``budget.account_openings`` and the one loader that reads it
(:func:`app.services.cash_ledger.account_opening_fact`), plus the property the
whole step exists for: a BACK-DATED assertion no longer redefines the level
every historical balance rests on.

The fold's own arithmetic over that level is graded in
``test_cash_fold/test_the_opening_equity_is_the_seed.py``
(``TestTheOpeningEquityIsTheSeed``) and
the worked-out ledger's use of it in
``tests/test_services/test_ledger_lines/test_an_accounts_corrections/``; this file
is about the RECORD.

**Every account here is built with**
:func:`tests._test_helpers.create_account_via_service` **rather than the shared**
``create_account_of_type``, and that is the point rather than a detail (plan
step X-f3c-2b).  The shared factory returns an account that has ALREADY
EXISTED: it appends a second ``budget.account_openings`` row so a fixture can
date movements before the account's own creation day.  That is the right
default everywhere else and it is exactly wrong here, where the subject IS the
one row ``account_service.create_account`` writes -- a suite asserting "there
is one opening row, dated where the assertion is" would otherwise be grading
the fixture.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import AccountOpeningSourceEnum
from app.extensions import db as _db
from app.models.account_opening import (
    AccountOpening,
    AccountOpeningImmutableError,
)
from app import ref_cache
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import account_opening_fact
from tests._test_helpers import (
    account_never_asserted,
    append_balance_assertion,
    create_account_via_service,
    settle_instant_on,
)

_ZERO = Decimal("0.00")


def _governing(account):
    """Return *account*'s governing opening record, through the app's loader."""
    return account_opening_fact(account.id)


def _openings(account):
    """Return every opening row for *account*, oldest first."""
    return (
        _db.session.query(AccountOpening)
        .filter_by(account_id=account.id)
        .order_by(AccountOpening.created_at, AccountOpening.id)
        .all()
    )


class TestCreateAccountRecordsWhatTheBooksOpenedWith:
    """Every account carries exactly one opening row from the moment it exists."""

    def test_the_declared_balance_becomes_the_opening_equity(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A brand-new account's opening equity IS the balance its owner typed.

        There are no records for the assertion to already contain, so the
        declaration and the opening equity are the same figure -- which is why
        ``create_account`` writes ``user_declared`` rather than deriving
        anything.  The DAY is the assertion's own observed day, so the posted
        ``account_opening`` entry is dated where the books opened.
        """
        with app.app_context():
            account = create_account_via_service(
                seed_user, db.session, "Checking", "Opened Checking",
                anchor_balance=Decimal("1234.56"),
            )
            db.session.commit()

            rows = _openings(account)
            assert len(rows) == 1
            assert rows[0].opening_equity == Decimal("1234.56")
            assert rows[0].source_id == ref_cache.account_opening_source_id(
                AccountOpeningSourceEnum.USER_DECLARED,
            )
            fact = _governing(account)
            assert fact.opening_equity == Decimal("1234.56")
            assert fact.opened_on == account.anchor_history[0].observed_on

    def test_an_amortizing_account_gets_one_too(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A LOAN carries an opening record, and the reason is REACHABILITY.

        ``balance_at.balance_at`` -- the kind-correct entry the savings page and
        the net-worth surfaces read -- dispatches on
        ``_resolution.configured_loan``, which answers ``None`` for an
        amortizing account with no :class:`~app.models.loan_params.LoanParams`
        and falls through to the cash replay.  Creating a Mortgage and not
        finishing the loan-params form reaches exactly that state, so an
        amortizing account WITHOUT an opening record would raise on a live
        screen.  With every account carrying one, absence is unreachable and
        the loader can refuse it instead of fabricating a level.

        A CONFIGURED loan never reads this row -- its opening is
        ``LoanParams.original_principal`` -- so the row is inert there rather
        than a second answer.
        """
        with app.app_context():
            loan = create_account_via_service(
                seed_user, db.session, "Auto Loan", "Unconfigured Van Loan",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()

            assert len(_openings(loan)) == 1
            assert _governing(loan).opening_equity == _ZERO

            # The reachable state itself: no LoanParams, so the kind-correct
            # scalar folds this account through the cash replay and must not
            # raise.
            assert loan.loan_params is None
            ctx = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"],
                as_of=date(2026, 6, 1),
            )
            assert balance_at.balance_at(loan, ctx, date(2026, 6, 1)) == _ZERO


class TestAMissingOpeningRecordIsRefused:
    """A balance folded from a level nothing recorded is worse than no balance."""

    def test_the_loader_raises_rather_than_answering_zero(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The FIRING control for the fabrication this step refuses to keep.

        Answering ``Decimal("0.00")`` for an account with no opening record was
        the alternative, and it is the exact shape ruling R-I was: a figure
        nothing recorded, silently re-levelling every balance the account has
        ever rendered.  Deleting the row is not reachable through any door
        (``create_account`` writes one and migration ``a7c41f9d2b60``
        backfilled every account that predated the table), so this forces it.
        """
        with app.app_context():
            # **Built rather than emptied** (plan step X-f3c-2c).  This
            # used to create an account and then bulk-delete its opening row,
            # with a comment saying the bulk form was chosen because the ORM
            # guard refuses a session-mediated one -- and that hole is closed:
            # ``budget.refuse_append_only_change`` refuses every spelling.  The
            # state remains reachable exactly one way, which is the way this
            # loader's own error message names: an ``Account`` row constructed
            # without routing through the canonical factory.
            account = account_never_asserted(
                seed_user, db.session, name="Doomed Checking",
            )
            _db.session.commit()

            with pytest.raises(RuntimeError, match="zero AccountOpening rows"):
                account_opening_fact(account.id)


class TestTheRecordIsAppendOnly:
    """A restatement is a NEW row, so what the opening used to be survives it."""

    def test_an_update_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Overwriting the level every balance rests on is not a silent act."""
        with app.app_context():
            account = create_account_via_service(
                seed_user, db.session, "Checking", "Immutable Checking",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()

            row = _openings(account)[0]
            row.opening_equity = Decimal("999.00")
            with pytest.raises(AccountOpeningImmutableError):
                db.session.flush()
            db.session.rollback()
            assert _governing(account).opening_equity == Decimal("500.00")

    def test_a_delete_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every account must keep a level for its fold to stand on."""
        with app.app_context():
            account = create_account_via_service(
                seed_user, db.session, "Checking", "Undeletable Checking",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()

            db.session.delete(_openings(account)[0])
            with pytest.raises(AccountOpeningImmutableError):
                db.session.flush()
            db.session.rollback()
            assert len(_openings(account)) == 1

    def test_the_latest_recorded_row_governs_and_the_earlier_one_survives(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A restatement wins, and the figure it replaced is still on file.

        The order is the RECORDING instant, not the business day: ``created_at``
        is set by the database on INSERT and no door lets a user move it.  That
        is the difference from the positional read this step deletes, which
        ordered by an ``observed_on`` any owner may back-date.
        """
        with app.app_context():
            account = create_account_via_service(
                seed_user, db.session, "Checking", "Restated Checking",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            first = _openings(account)[0]

            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=first.opened_on,
                opening_equity=Decimal("1125.21"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()

            assert _governing(account).opening_equity == Decimal("1125.21")
            assert [row.opening_equity for row in _openings(account)] == [
                Decimal("500.00"), Decimal("1125.21"),
            ]


class TestABackDatedAssertionNoLongerRedefinesTheOpening:
    """The defect plan step X-f3c-2a exists to close, shown not firing.

    ``cash_ledger._events.cash_anchor_facts`` marked the opening POSITIONALLY
    (``is_opening = index == 0`` over ``(observed_on, created_at, id)``) and
    ``anchor_service.resolve_observation_day`` permits a back-dated assertion.
    So recording a balance for a day earlier than any on file re-elected the
    opening, and the fold recomputed the level every pre-opening balance rests
    on -- with no surface saying so.
    """

    def test_the_stored_equity_and_the_pre_opening_balance_both_hold(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Insert an EARLIER assertion; the recorded level does not move.

        Hand-computed.  The account's books open at $1,000.00 on the fixture's
        own day.  A balance of $250.00 is then asserted for a day two weeks
        BEFORE it -- which is now the earliest assertion, and under the deleted
        rule would have become "the opening", making the derived level
        $250.00 and moving every date before it by $750.00.

        The stored fact does not move, and neither does the balance at a date
        before every assertion.  What the back-dated row DOES do is book its own
        correction on its own day, which is what an assertion disagreeing with
        the books is supposed to do.
        """
        with app.app_context():
            account = create_account_via_service(
                seed_user, db.session, "Checking", "Back-dated Checking",
                anchor_balance=Decimal("1000.00"),
            )
            db.session.commit()
            opened_on = _governing(account).opened_on

            before_any = opened_on - timedelta(days=30)
            ctx = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"],
                as_of=opened_on + timedelta(days=30),
            )
            assert balance_at.cash_balance_at(
                account, ctx, before_any,
            ) == Decimal("1000.00")

            append_balance_assertion(
                db.session, account, Decimal("250.00"),
                settle_instant_on(opened_on - timedelta(days=14)),
            )
            db.session.commit()

            # The RECORD is untouched: one row, still $1,000.00.
            assert len(_openings(account)) == 1
            assert _governing(account).opening_equity == Decimal("1000.00")

            # And so is the balance at a date before every assertion, which is
            # the figure the deleted rule would have moved by $750.00.
            fresh = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"],
                as_of=opened_on + timedelta(days=30),
            )
            assert balance_at.cash_balance_at(
                account, fresh, before_any,
            ) == Decimal("1000.00")
