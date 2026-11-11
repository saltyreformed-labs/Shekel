"""The books boundary itself, the one ORM writer, and the bulk writer asking for itself."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum, TxnTypeEnum

from app.exceptions import ValidationError
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.services import status_seam
from app.services.cash_ledger import (
    AnchorPoint,
    cash_anchor_facts,
    reject_movement_before_books_open,
)
from app.services.pay_calendar import calendar_for
from app.services.reconcile_service import Statement, record_settled_days
from app.services.settle_day import record_settle_day
from tests._test_helpers import (
    account_never_asserted,
    create_account_of_type,
    create_settled_cash_transaction,
    figure_source_columns,
    one_off_row_of,
)

from ._helpers import _ONE_DAY, _books_open_on, _entered


class TestTheBoundaryItself:
    """The predicate, pinned from BOTH sides of the day it turns on.

    Asserted at the boundary rather than at an arbitrary depth so the comparison
    direction is what the tests measure: a ``<`` written where the code has
    ``<=`` moves exactly the opening day across the line and fails
    :meth:`test_the_opening_day_itself_is_refused` while every other case here
    still passes.
    """

    def test_a_day_before_the_books_open_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The day before the opening raises, naming both dates."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            opened_on = _books_open_on(account)
            with pytest.raises(ValidationError) as exc:
                reject_movement_before_books_open(
                    account.id, opened_on - _ONE_DAY,
                )
            message = str(exc.value)
            assert (opened_on - _ONE_DAY).isoformat() in message
            assert opened_on.isoformat() in message

    def test_the_opening_day_itself_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """**The `<=` half of the rule, and the whole of R-HG.**

        The opening equity is the CLOSING balance for its own day, so money that
        moved that day is inside it.  Paired with the case below so neither can
        pass while the comparison is off by one in either direction.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            opened_on = _books_open_on(account)
            with pytest.raises(ValidationError, match=r"books open on"):
                reject_movement_before_books_open(account.id, opened_on)

    def test_the_day_after_the_books_open_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One day past the opening is the first recordable day."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            reject_movement_before_books_open(
                account.id, _books_open_on(account) + _ONE_DAY,
            )

    def test_an_account_with_no_opening_record_RAISES_rather_than_permitting(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A broken invariant fails loud instead of reading as "anything goes".

        The alternative -- treating a missing record as an unbounded account --
        is the fabrication ``account_opening_fact`` exists to refuse on the read
        side, arriving through the write side instead.
        """
        with app.app_context():
            # **Built rather than emptied** (plan step X-f3c-2c).
            # ``budget.account_openings`` is append-only at the database tier,
            # so nothing may remove an account's opening record; the only route
            # to an account that has none is one the factory never touched --
            # which is what ``account_opening_fact``'s own message tells a
            # reader to go looking for.
            account = account_never_asserted(
                seed_user, db.session, name="Bounded",
            )
            db.session.flush()
            with pytest.raises(RuntimeError, match="zero"):
                reject_movement_before_books_open(account.id, date(2030, 1, 1))


class TestTheOneOrmWriter:
    """``record_settle_day`` asks it, so every ORM door inherits the refusal.

    ``status_seam.apply_status_change``, both ``entry_service`` doors and the
    statement matcher all reach ``settled_on`` through this one function, which
    is why the rule is asked there rather than at each of them.
    """

    def test_a_transaction_cannot_be_settled_on_the_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """And the row is left UNTOUCHED, both columns."""
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            row = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="dated-probe",
            )
            before = (row.settled_on, row.settled_day_basis_id)
            with pytest.raises(ValidationError, match=r"books open on"):
                record_settle_day(row, _entered(opened_on))
            assert (row.settled_on, row.settled_day_basis_id) == before

    def test_a_purchase_cannot_be_settled_on_the_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The SECOND table carries the same rule, from the same call.

        ``transaction_entries`` is the other :class:`SettleDatedMixin` table and
        the fold reads a posted purchase as a movement exactly as it reads a
        settled transaction (ruling **R-FM**).  A rule stated for one table and
        enforced on one table is a rule the second table does not have.
        """
        with app.app_context():
            account = seed_user["account"]
            parent = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY, name="parent",
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="probe",
                purchased_on=_books_open_on(account) - _ONE_DAY,
                is_credit=False,
            )
            with pytest.raises(ValidationError, match=r"books open on"):
                record_settle_day(entry, _entered(_books_open_on(account)))
            assert entry.settled_on is None

    def test_CLEARING_a_settle_day_is_never_bounded(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A revert withdraws an assertion and states nothing about when.

        The same carve-out ``settle_day_for_status`` makes for the schedule
        floor (ruling **R-EG**'s unlock path): bounding the clear arm would
        strand a legacy row whose stored day precedes its own books, which is
        the one row that most needs to be revertible.
        """
        with app.app_context():
            account = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY, name="revert-probe",
            )
            record_settle_day(row, None)
            assert row.settled_on is None
            assert row.settled_day_basis_id is None

    def test_the_seam_refuses_it_for_every_caller(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``apply_status_change`` inherits it, so a programmatic caller does too.

        The bound is a SEAM invariant and not a door policy, which is the
        difference from the schedule floor (ruling **R-EL**): a pre-schedule
        settle is absorbed by an assertion and is correct, where a pre-opening
        one is counted twice and never is.
        """
        with app.app_context():
            account = seed_user["account"]
            row = one_off_row_of(
                seed_periods[0],
                name="seam-probe",
                amount=Decimal("25.00"),
                user_id=seed_periods[0].user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.flush()
            done = ref_cache.status_id(StatusEnum.DONE)
            with pytest.raises(ValidationError, match=r"books open on"):
                status_seam.apply_status_change(
                    row, done,
                    settle_day=_entered(_books_open_on(account)),
                    settlement=status_seam.Settlement.from_settle(
                        Decimal("10.00"), None,
                    ),
                )


class TestTheBulkWriterAsksForItself:
    """``reconcile_service.record_settled_days`` has no instance to be guarded.

    It stamps a statement's day onto every ticked purchase with one
    ``query.update()``, by design (its own comment: "there is no ORM instance
    to hand that function"), so the refusal
    :func:`app.services.settle_day.record_settle_day` inherits cannot reach it.
    It asks the same predicate itself, and this is the control that says so.

    **Reachable rather than theoretical.**  An assertion's ``observed_on`` is
    bounded below only by ``pay_period_service.earliest_recordable_day``, which
    on the developer's own data is 2026-03-26 -- the very day Checking's books
    open after this step's migration -- so a statement recorded for that day
    would stamp every ticked purchase inside the opening equity.
    """

    def test_a_statement_dated_inside_the_opening_stamps_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The whole act is refused, and no entry is written.

        Refused per ACT rather than per entry, because the day being stamped is
        the STATEMENT's: either every tick lands inside the opening equity or
        none does.  Asserted on the entry's columns after the refusal, so a
        version that raised AFTER its ``UPDATE`` would fail here.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            parent = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("40.00"),
                settled_on=opened_on + timedelta(days=5), name="envelope",
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="ticked purchase",
                purchased_on=opened_on - _ONE_DAY,
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()

            governing = cash_anchor_facts(account.id)[0]
            statement = Statement(
                calendar_for(seed_user["user"].id),
                account.id,
                AnchorPoint(
                    anchor_id=governing.anchor_id,
                    balance=governing.anchor_balance,
                    observed_on=opened_on,
                    created_at=governing.asserted_at,
                ),
            )
            with pytest.raises(ValidationError, match=r"books open on"):
                record_settled_days(statement, {entry.id})
            db.session.refresh(entry)
            assert entry.settled_on is None
            assert entry.reconciled_by_id is None
