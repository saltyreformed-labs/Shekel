"""The database's own locks: on the row, on the transfer, and the owner's lock taken first."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from sqlalchemy import event, text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.services.settle_day import SettleDay
from tests._test_helpers import (
    advisory_lock_keys,
    create_account_of_type,
    create_transfer,
    generate_row_of,
    make_expense_template,
    owner_lock_key,
    typed,
)

from ._helpers import (
    _WAIT,
    _signed_in_clients,
    _state,
    _groceries,
    _hotel,
    _dinner,
    _paid_hotel,
    _purchase,
    _delete,
    _mark_paid,
    _archive,
    _mark_credit,
    _save_an_actual,
)


#: The database arrival arm's refusal, as it words it.
_ARRIVAL_REFUSED = "was deleted: a payment or purchase cannot be recorded under it"


@dataclass
class _Outcome:
    """What one raw click came to: ``"committed"``, or the exception it raised."""

    result: object = None
    waited: bool = False

    @property
    def committed(self) -> bool:
        """Whether the click's transaction committed."""
        return self.result == "committed"


def _race_statements(
    app, first: Callable[[], None], second: Callable[[], None],
    *, then: Callable[[], None] | None = None,
):
    """Run raw *first* uncommitted here, raw *second* in another session, then commit in that order.

    The harness for :class:`TestTheDatabaseLocksTheRow` and
    :class:`TestTheDatabaseLocksTheTransfer`, whose clicks are raw statements
    rather than requests.  Returns ``(first_outcome, second_outcome)``; the
    second records whether PostgreSQL showed it WAITING on a lock before the
    first committed (``waited``).

    *then*, when given, is the rest of the FIRST click: run after the second
    is seen waiting and before the first commits.  A click whose later
    statement takes a lock of its own splits there, so the wait measured is
    on the lock *first* took and on nothing *then* takes.  It is refused
    when the second FINISHED without waiting: its statements returned but
    its transaction is still open and holds its locks, so a *then* needing
    one would block until the second stopped waiting for its turn
    (``_WAIT``) and committed FIRST, ending the race in the other order.
    """
    first_outcome, second_outcome = _Outcome(), _Outcome()
    acted, go, done = threading.Event(), threading.Event(), threading.Event()
    pid_box = {}

    def second_click():
        with app.app_context():
            try:
                pid_box["pid"] = _db.session.execute(
                    text("SELECT pg_backend_pid()"),
                ).scalar_one()
                acted_ok = False
                try:
                    second()
                    acted_ok = True
                finally:
                    acted.set()
                if acted_ok:
                    go.wait(_WAIT)
                    _db.session.commit()
                    second_outcome.result = "committed"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # A race test records whatever the loser raised -- the
                # trigger's raw error above all -- so the assertion can say
                # which; narrowing it would turn the very failure the test
                # grades into an unreported thread crash.
                _db.session.rollback()
                second_outcome.result = exc
            finally:
                _db.session.remove()
                done.set()

    first()
    worker = threading.Thread(target=second_click)
    worker.start()
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline and not acted.is_set():
        pid = pid_box.get("pid")
        if pid is not None and _db.session.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_locks "
                 "WHERE pid = :pid AND NOT granted)"),
            {"pid": pid},
        ).scalar_one():
            second_outcome.waited = True
            break
        time.sleep(0.02)
    assert second_outcome.waited or acted.is_set(), (
        "the second click neither waited on a lock nor finished -- the "
        "harness is not measuring the race"
    )
    if then is not None:
        assert second_outcome.waited, (
            "the second click finished without waiting on a lock, so the "
            "rest of the first click would wait on the second's locks and "
            "the second would commit first -- the harness is not measuring "
            "the race"
        )
        then()
    try:
        _db.session.commit()
        first_outcome.result = "committed"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The first click's commit can itself be the loser (the hiding arm
        # refusing at COMMIT); recorded for the assertion, as above.
        _db.session.rollback()
        first_outcome.result = exc
    go.set()
    assert done.wait(_WAIT * 2), "the second click never finished"
    worker.join(_WAIT)
    _db.session.expire_all()
    return first_outcome, second_outcome


def _posted_groceries(seed_user):
    """Groceries ($300.00) holding a DATED $40.00 KROGER purchase, so its postings exist; returns its id."""
    _template, row_id = _groceries(seed_user)
    day = seed_user["bootstrap_period"].start_date + timedelta(days=1)
    entry_service.create_entry(
        row_id, seed_user["user"].id,
        entry_service.EntryDetails(
            figure=typed(Decimal("40.00")), description="KROGER",
            purchased_on=day,
            settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.OBSERVED),
        ),
    )
    _db.session.commit()
    return row_id


class TestTheDatabaseLocksTheRow:
    """The arrival arm's lock, raced with raw statements: the writer nobody enumerated."""

    @staticmethod
    def _raw_insert(row_id, source_row_id):
        """Return a click copying *source_row_id*'s movement under *row_id*, raw."""
        copied = ", ".join(
            column.name for column in TransactionEntry.__table__.columns
            if column.name not in ("id", "transaction_id")
        )

        def click():
            _db.session.execute(
                text(
                    f"INSERT INTO budget.transaction_entries "
                    f"(transaction_id, {copied}) "
                    f"SELECT :row, {copied} FROM budget.transaction_entries "
                    f"WHERE transaction_id = :src"
                ),
                {"row": row_id, "src": source_row_id},
            )
        return click

    @staticmethod
    def _raw_hide(row_id):
        """Return a click hiding *row_id* with a raw ``UPDATE``."""
        def click():
            _db.session.execute(
                text("UPDATE budget.transactions SET is_deleted = TRUE "
                     "WHERE id = :i"),
                {"i": row_id},
            )
        return click

    @staticmethod
    def _two_rows(seed_user):
        """An empty Groceries row, and a Pantry envelope row holding the purchase to copy."""
        _template, row_id = _groceries(seed_user)
        pantry = make_expense_template(
            _db.session, seed_user, amount="50.00", name="Pantry",
            category_key="Groceries", is_envelope=True,
        )
        source_id = generate_row_of(pantry, seed_user["bootstrap_period"]).id
        _db.session.commit()
        entry_service.create_entry(
            source_id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("12.34")), description="KROGER",
                purchased_on=(
                    seed_user["bootstrap_period"].start_date + timedelta(days=1)
                ),
            ),
        )
        _db.session.commit()
        return row_id, source_id

    def test_hide_first_the_insert_waits_and_is_refused(self, app, db, seed_user):
        """Hide open, insert arrives: it waits for the hide, then the arm refuses it."""
        with app.app_context():
            row_id, source_id = self._two_rows(seed_user)
            hide, insert = _race_statements(
                app, self._raw_hide(row_id), self._raw_insert(row_id, source_id),
            )
            assert hide.committed
            assert insert.waited, "the arrival did not wait for the open hide"
            assert _ARRIVAL_REFUSED in str(insert.result)
            assert _state(row_id) == (True, 0)

    def test_insert_first_the_hide_waits_and_is_refused(self, app, db, seed_user):
        """Insert open, hide arrives: it waits for the insert, then the hiding arm refuses it."""
        with app.app_context():
            row_id, source_id = self._two_rows(seed_user)
            insert, hide = _race_statements(
                app, self._raw_insert(row_id, source_id), self._raw_hide(row_id),
            )
            assert insert.committed
            assert hide.waited
            assert "while it still holds a recorded payment or purchase" in (
                str(hide.result)
            )
            assert _state(row_id) == (False, 1)


class TestTheDatabaseLocksTheTransfer:
    """The transfer arm's lock, raced the same way (plan step ``balance:X-bi-6-4d-2``).

    A transfer side's payment names its transfer by a side link, so the
    arrival arm reads the TRANSFER's ``is_deleted`` under the same
    ``FOR NO KEY UPDATE`` (ruling **R-CC96**), and the transfer hiding arm
    refuses at COMMIT.  Without the lock each order would end with a hidden
    transfer holding a payment, the state ruling R-CC92 forbids.
    """

    @staticmethod
    def _transfer(seed_user):
        """A Projected $500.00 Checking -> Savings transfer: ``(id, from, to, owner)`` ids."""
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Race Savings",
        )
        _db.session.commit()
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("500.00"),
        )
        _db.session.commit()
        return xfer.id, xfer.from_account_id, xfer.to_account_id, xfer.user_id

    @staticmethod
    def _raw_insert(transfer_id, from_account_id, to_account_id, owner_id):
        """Return the record click's first half: both sides' kept records, raw, in one statement.

        A revert's kept pair (ruling R-BAL61), whose figure the second half
        stores (:meth:`_raw_figure`): a transfer holds a record on both sides
        or on neither, and a figure above zero exactly when it holds both
        (the band's figure arm, graded at COMMIT), so a save that records a
        payment writes all three.  Each record states no figure and no
        figure source: a side's record holds neither since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half
        (``ck_transaction_entries_side_record_states_no_figure``,
        ``ck_transaction_entries_side_record_names_no_figure_source``).
        """
        def click():
            _db.session.execute(
                text(
                    "INSERT INTO budget.transaction_entries (expense_transfer_id, "
                    "income_transfer_id, account_id, owner_id, user_id, "
                    "description, covers_settlement) "
                    "VALUES (:t, NULL, :f, :o, :o, 'Race record', TRUE), "
                    "(NULL, :t, :to, :o, :o, 'Race record', TRUE)"
                ),
                {
                    "t": transfer_id, "f": from_account_id,
                    "to": to_account_id, "o": owner_id,
                },
            )
        return click

    @staticmethod
    def _raw_figure(transfer_id):
        """Return the record click's second half: the transfer stores its pair's $500.00, raw.

        An ``UPDATE`` of the transfer, so it takes the transfer's row lock
        itself: run after the hide is seen waiting (``_race_statements``'
        *then*), the wait it measures is on the arrival arm's lock alone.
        """
        def click():
            _db.session.execute(
                text("UPDATE budget.transfers SET settled_figure = 500.00, "
                     "settled_figure_source_id = :s WHERE id = :t"),
                {
                    "t": transfer_id,
                    "s": ref_cache.movement_figure_source_id(
                        MovementFigureSourceEnum.RESOLVED,
                    ),
                },
            )
        return click

    @staticmethod
    def _raw_hide(transfer_id):
        """Return a click hiding the transfer with a raw ``UPDATE``."""
        def click():
            _db.session.execute(
                text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
                {"t": transfer_id},
            )
        return click

    @staticmethod
    def _state(transfer_id):
        """Return ``(is_deleted, side records held)`` for the transfer, read fresh."""
        return tuple(_db.session.execute(
            text("SELECT x.is_deleted, (SELECT count(*) FROM "
                 "budget.transaction_entries e WHERE e.expense_transfer_id = x.id "
                 "OR e.income_transfer_id = x.id) "
                 "FROM budget.transfers x WHERE x.id = :t"),
            {"t": transfer_id},
        ).one())

    def test_hide_first_the_insert_waits_and_is_refused(self, app, db, seed_user):
        """Hide open, records arrive: the first waits for the hide, then the arm refuses it.

        The record click races with its first half alone (:meth:`_raw_insert`):
        the arm refuses that ``INSERT``, so the half storing the figure never
        runs.  Raced whole, its figure ``UPDATE`` would wait on the hide's own
        row lock, and ``waited`` would no longer single out the arrival arm's
        (measured: with the arm's lock removed, the whole click waited there
        and COMMITTED).
        """
        with app.app_context():
            transfer_id, from_id, to_id, owner_id = self._transfer(seed_user)
            hide, insert = _race_statements(
                app, self._raw_hide(transfer_id),
                self._raw_insert(transfer_id, from_id, to_id, owner_id),
            )
            assert hide.committed
            assert insert.waited, "the arrival did not wait for the open hide"
            assert "was deleted: a payment cannot be recorded under it" in (
                str(insert.result)
            )
            assert self._state(transfer_id) == (True, 0)

    def test_insert_first_the_hide_waits_and_is_refused(self, app, db, seed_user):
        """Records open, hide arrives: it waits for the records, then the hiding arm refuses it.

        The record click inserts both sides' records, and stores the
        transfer's figure only once the hide is seen waiting: an ``UPDATE``
        of the transfer before then would hold the transfer's row lock
        itself, and the hide would wait on that lock rather than on the one
        the arrival arm takes (ruling R-CC96).  The click's two halves commit
        together, so the transfer ends holding both records and its figure
        (developer 2026-10-24, "Yes, 1 becomes 2": one record alone is
        unstorable since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half).
        """
        with app.app_context():
            transfer_id, from_id, to_id, owner_id = self._transfer(seed_user)
            insert, hide = _race_statements(
                app, self._raw_insert(transfer_id, from_id, to_id, owner_id),
                self._raw_hide(transfer_id),
                then=self._raw_figure(transfer_id),
            )
            assert insert.committed
            assert hide.waited
            assert "while it still holds a recorded payment" in str(hide.result)
            assert self._state(transfer_id) == (False, 2)


class TestTheOwnersLockComesFirst:
    """Every door this module races takes the owner's write lock before it reads the owner's data.

    Ruling **R-CC100** (developer 2026-09-23, "Write lock first here"):
    *"Every door this step locks (add purchase, Mark Paid, the popover's
    Actual, Delete, Archive, Mark Credit) takes the owner's write lock before
    the row's lock"* -- and since plan step ``balance:X-bn`` (ruling
    **R-CC106**) the door takes no row lock at all: the REQUEST takes the
    owner's lock where its transaction begins (:mod:`app.db_transaction`).
    Review 5 (M1) measured the cycle the order closes: a Delete holding its
    row and then asking for the owner's lock against the reconcile tick
    holding the owner's lock and then asking for the row ended
    ``DeadlockDetected``.

    Graded on each door's REAL request, run on a thread of its own so it has
    its own session: the owner's key is locked, it is the first advisory lock
    the request takes, and no statement before it reads or writes a budget or
    salary table -- the only rows read before it are the signed-in user's own.
    """

    @staticmethod
    def _statements(click):
        """Run *click* on a thread of its own; return its response and every ``(statement, params)`` it issued."""
        seen, errors = [], []
        box = {}

        def record(_conn, _cursor, statement, params, _ctx, _many):
            if threading.get_ident() == box.get("thread"):
                seen.append((statement, params))

        def run():
            box["thread"] = threading.get_ident()
            try:
                box["response"] = click()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                # Re-raised below, on the test's thread, with its traceback.
                errors.append(exc)

        event.listen(_db.engine, "before_cursor_execute", record)
        try:
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(_WAIT * 3)
            assert not worker.is_alive(), "the request never finished"
        finally:
            event.remove(_db.engine, "before_cursor_execute", record)
        if errors:
            raise errors[0]
        return box["response"], seen

    @classmethod
    def _assert_the_owners_lock_first(cls, owner_id, click, status):
        """Assert *click*'s request answers *status*, locking the owner before any owner's data."""
        response, statements = cls._statements(click)
        assert response.status_code == status, response.status_code
        keys = advisory_lock_keys(statements)
        assert keys, "the request took no advisory lock"
        assert keys[0] == owner_lock_key(owner_id), keys
        first_lock = next(
            i for i, (sql, _params) in enumerate(statements)
            if "pg_advisory_xact_lock" in sql
        )
        touched = [
            sql for sql, _params in statements[:first_lock]
            if "budget." in sql or "salary." in sql
            or sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
            or " FOR " in sql.upper()
        ]
        assert not touched, touched
        assert any(
            "budget." in sql for sql, _params in statements[first_lock:]
        ), "the door read no budget row -- nothing was measured"

    def test_the_purchase_door(self, app, db, seed_user):
        """A $12.34 purchase on Groceries locks the owner before it reads the row."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _purchase(client, row_id), 200,
            )

    def test_mark_paid(self, app, db, seed_user):
        """Mark Paid on the $120.00 Hotel locks the owner before it reads the row."""
        with app.app_context():
            _template, row_id = _hotel(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _mark_paid(client, row_id), 200,
            )

    def test_the_popover_actual(self, app, db, seed_user):
        """A $125.00 Actual saved on a Paid Hotel locks the owner before it reads the row.

        Through the popover's own Save, so the route's field writes -- a typed
        note flushed before the status arm (review 6, M1) -- are inside the
        measurement: none precedes the owner's lock.
        """
        with app.app_context():
            row_id = _paid_hotel(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id,
                _save_an_actual(client, row_id, "125.00"), 200,
            )

    def test_delete(self, app, db, seed_user):
        """Delete of a posted Groceries locks the owner before it reads the row."""
        with app.app_context():
            row_id = _posted_groceries(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _delete(client, row_id), 200,
            )

    def test_archive(self, app, db, seed_user):
        """Archive locks the owner before it reads the item or its rows."""
        with app.app_context():
            template, _row_id = _groceries(seed_user)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _archive(client, template.id), 302,
            )

    def test_mark_credit(self, app, db, seed_user, seed_periods):
        """Mark Credit on an $80.00 Dinner locks the owner before it reads the row."""
        with app.app_context():
            row_id = _dinner(seed_user, seed_periods)
            (client,) = _signed_in_clients(app, 1)
            self._assert_the_owners_lock_first(
                seed_user["user"].id, _mark_credit(client, row_id), 200,
            )
