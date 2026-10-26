"""Tests for commit C-19: TOCTOU duplicate CC Payback prevention.

Closes audit finding F-008.  Three load-bearing layers are exercised:

1. **Database invariant** -- the partial unique index
   ``uq_transactions_credit_payback_unique`` is declared on
   ``budget.transactions(credit_payback_for_id)`` with the predicate
   ``credit_payback_for_id IS NOT NULL AND is_deleted = FALSE``.  We
   assert the index exists and verify its predicate by attempting
   raw INSERTs that should and should not violate it.
2. **Serialisation** -- since plan step ``balance:X-bn`` (ruling
   **R-CC106**) the request's owner write lock
   (:mod:`app.db_transaction`) makes two concurrent requests wait for
   each other: every command transaction a signed-in request opens
   takes it before it reads any of the owner's data, so the second Mark
   Credit or credit purchase reads the first's committed payback.
   *Until that step* ``credit_workflow.mark_as_credit`` *and*
   ``entry_credit_workflow.sync_entry_payback`` *each wrapped their
   read-then-insert in* ``SELECT ... FOR NO KEY UPDATE`` *on the source
   row, and two tests here searched the SQL for that clause; they were
   deleted with it (the developer's rule-5 answer, 2026-09-29).*
3. **Route-level idempotency** -- for a writer that holds no request,
   and so no owner lock, the partial index converts the duplicate
   INSERT into an ``IntegrityError`` that the route layer catches and
   converts into the same 200 response the user would have seen from a
   serialised request.

Concurrent-thread tests use ``threading.Barrier`` -- the same
pattern as ``tests/test_concurrent/test_race_conditions.py`` -- so
the two requests start together and race for the owner's lock.  Each
thread runs in its own Flask app context with its own SQLAlchemy
session so the session-scoped identity map does not mask the race we
are trying to verify.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.category import Category
from app.models.ref import AccountType, Status, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import credit_workflow
from app.services.auth_service import hash_password
from app.services.entry_credit_workflow import sync_entry_payback
from app.services import account_service
from app.utils.dates import display_today
from tests._test_helpers import (
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    open_owner_calendar,
    record_paydays_across_a_hole,
    rhythm_of,
)
from app.models.amount_ownership import AmountOwnership


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


UNIQUE_INDEX_NAME = "uq_transactions_credit_payback_unique"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_projected_expense(seed_user, seed_periods, amount="100.00", period_index=0):
    """Insert a projected expense in ``seed_periods[period_index]``."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = one_off_row_of(
        seed_periods[period_index],
        name="Test Expense",
        amount=Decimal(amount),
        user_id=seed_periods[period_index].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.flush()
    return txn


def _insert_payback_directly(
    source_txn, period, account_id, scenario_id, category_id,
    amount="100.00", is_deleted=False,
):
    """Insert a payback row that bypasses the credit_workflow service.

    Used for tests that exercise the database-level partial unique
    index in isolation -- the service layer is intentionally skipped,
    and a direct insert outside any request takes no owner lock, so the
    index becomes the only safeguard being asserted.
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    payback = Transaction(
        user_id=period.user_id,
        pay_period_id=period.id,
        scenario_id=scenario_id,
        account_id=account_id,
        status_id=projected.id,
        name=f"CC Payback: {source_txn.name}",
        category_id=category_id,
        transaction_type_id=expense_type.id,
        amount_ownership=AmountOwnership.own(Decimal(amount)),
        credit_payback_for_id=source_txn.id,
        is_deleted=is_deleted,
    )
    db.session.add(payback)
    db.session.flush()
    return payback


def _create_concurrent_user(db_session):
    """Create a self-contained user + account + scenario + categories for
    threaded tests.

    The default ``seed_user`` fixture is application-context-bound
    and tied to the main test session; threaded tests instead create
    a fresh user via the test session and then have each worker
    open its own app context for its own session.
    """
    from datetime import date as _date  # pylint: disable=import-outside-toplevel

    user = User(
        email="c19-concurrent@shekel.local",
        password_hash=hash_password("c19concurrent"),
        display_name="C-19 Concurrent",
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(UserSettings(user_id=user.id))

    # The owner's opening pay period.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    _bootstrap = open_owner_calendar(user.id, _date(2024, 1, 5))[0]

    checking_type = (
        db_session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("5000.00"),
        ),
    )

    scenario = Scenario(
        user_id=user.id, name="Baseline", is_baseline=True,
    )
    db_session.add(scenario)
    db_session.flush()

    categories = {}
    for group, item in (
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ):
        cat = Category(user_id=user.id, group_name=group, item_name=item)
        db_session.add(cat)
        categories[item] = cat
    db_session.flush()

    # Three biweekly periods so mark_as_credit always has a "next period".
    # Indices 1, 2, 3 -- offset past the bootstrap period (index 0) so the
    # uq_pay_periods_user_index constraint holds; the account re-anchors to
    # periods[0] (index 1) below, leaving the bootstrap unreferenced.
    # The APP's today, not the process's.  Every period boundary below has to
    # contain the day the containment lookup will look for, and that lookup
    # is on the display clock -- so building the window from ``date.today()``
    # leaves the app's today outside it whenever the two calendars disagree.
    today = display_today()
    base = today - timedelta(days=today.weekday())  # Monday this week
    # Three fortnightly periods from one batch.  They append past the opening
    # 2024 payday, so they take indices 1..3 exactly as the hand-built rows
    # did -- and the opening period's own end becomes the day before this
    # batch rather than a stored value nothing reconciles.  Two years past
    # that opening payday is a hole the writer refuses (plan step
    # pay_calendar:C17-c-2a, ruling R-PC67), so the rows come through the
    # tree's helper for exactly that state.
    periods = record_paydays_across_a_hole(user.id, base, 3, rhythm_of(14))
    db_session.commit()

    return {
        "user": user,
        "account": account,
        "scenario": scenario,
        "categories": categories,
        "periods": periods,
    }


def _run_concurrent(app, func_a, func_b, timeout=10):
    """Run two callables concurrently in fresh app contexts."""
    barrier = threading.Barrier(2, timeout=timeout)
    results = [None, None]
    errors = [None, None]

    def _worker(index, func):
        try:
            with app.app_context():
                barrier.wait()
                results[index] = func()
        except Exception as exc:  # pylint: disable=broad-except
            errors[index] = exc

    t_a = threading.Thread(target=_worker, args=(0, func_a))
    t_b = threading.Thread(target=_worker, args=(1, func_b))
    t_a.start()
    t_b.start()
    t_a.join(timeout=timeout)
    t_b.join(timeout=timeout)

    assert not t_a.is_alive(), "Thread A did not complete within timeout"
    assert not t_b.is_alive(), "Thread B did not complete within timeout"

    return results[0], results[1], errors[0], errors[1]


def _make_auth_client(app, email, password):
    """Create and authenticate a Flask test client.

    Used for tests that share a single authenticated client between
    two threads (the only working pattern for concurrent tests in
    this repo -- see ``tests/test_concurrent/test_race_conditions.py``
    ``test_concurrent_true_up`` for the same approach).  Trying to
    log in two distinct test clients as the same user produces a
    second client whose POST /login returns 302 to /dashboard
    without ever having captured a ``_user_id`` in its session,
    rendering it effectively anonymous for subsequent requests.
    """
    client = app.test_client(use_cookies=True)
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 302, (
        f"Login failed for {email}: {resp.status_code}"
    )
    return client


# ---------------------------------------------------------------------------
# Layer 1: Partial unique index existence and predicate
# ---------------------------------------------------------------------------


class TestPartialUniqueIndexShape:
    """The partial unique index is present in the live schema with the
    documented predicate."""

    def test_index_exists_in_pg_catalog(self, app, db):
        """``pg_indexes`` carries ``uq_transactions_credit_payback_unique``
        scoped to the budget schema."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT indexname, indexdef "
                "FROM pg_indexes "
                "WHERE schemaname = 'budget' "
                "  AND indexname = :name"
            ), {"name": UNIQUE_INDEX_NAME}).fetchone()
            assert row is not None, (
                f"Index {UNIQUE_INDEX_NAME} not found in pg_indexes -- the "
                "C-19 model declaration / migration was not applied."
            )
            indexdef = row[1].lower()
            assert "unique" in indexdef, (
                f"Index {UNIQUE_INDEX_NAME} is not UNIQUE: {indexdef}"
            )
            assert "credit_payback_for_id" in indexdef, indexdef
            # Partial predicate references both columns we care about.
            assert "is_deleted" in indexdef, indexdef
            assert "is not null" in indexdef, indexdef

    def test_index_predicate_excludes_null_and_deleted(self, app, db):
        """The pg_index predicate matches the C-19 specification.

        Reads back the predicate via ``pg_get_expr`` so the test
        is robust against PostgreSQL's normalisation of the WHERE
        clause -- the rendered form may differ slightly from the
        migration's input string but must mention both columns and
        the matching booleans.
        """
        with app.app_context():
            predicate = db.session.execute(text(
                "SELECT pg_get_expr(indpred, indrelid) "
                "FROM pg_index "
                "WHERE indexrelid = ("
                "  SELECT c.oid FROM pg_class c "
                "  JOIN pg_namespace n ON c.relnamespace = n.oid "
                "  WHERE n.nspname = 'budget' AND c.relname = :name"
                ")"
            ), {"name": UNIQUE_INDEX_NAME}).scalar()
            assert predicate is not None, (
                f"Index {UNIQUE_INDEX_NAME} has no partial predicate -- the "
                "migration created a full unique index instead."
            )
            normalised = predicate.lower()
            assert "credit_payback_for_id" in normalised, predicate
            assert "is not null" in normalised, predicate
            assert "is_deleted" in normalised, predicate
            assert "false" in normalised, predicate


class TestPartialUniqueIndexEnforcement:
    """The index actually rejects forbidden rows and accepts permitted ones."""

    def test_blocks_duplicate_active_payback_via_direct_insert(
        self, app, db, seed_user, seed_periods,
    ):
        """Two active paybacks for the same source row hit the index."""
        with app.app_context():
            source = _make_projected_expense(seed_user, seed_periods)
            _insert_payback_directly(
                source, seed_periods[1],
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Payback"].id,
            )
            db.session.commit()

            with pytest.raises(IntegrityError) as excinfo:
                _insert_payback_directly(
                    source, seed_periods[1],
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=seed_user["categories"]["Payback"].id,
                )
                db.session.commit()
            # The IntegrityError must name the C-19 partial unique index
            # so the route handler's discriminator catches the right
            # constraint and not, say, a coincidental FK violation.
            assert UNIQUE_INDEX_NAME in str(excinfo.value.orig)

    def test_allows_two_paybacks_when_first_is_soft_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """The partial predicate excludes soft-deleted rows.

        Re-marking a transaction as credit after the previous payback
        was soft-deleted is a legal user flow (the deleted payback
        stays in the table for the audit trail; the new one takes
        over).  The index must permit this even though both rows
        share the same ``credit_payback_for_id``.
        """
        with app.app_context():
            source = _make_projected_expense(seed_user, seed_periods)
            old_payback = _insert_payback_directly(
                source, seed_periods[1],
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Payback"].id,
                is_deleted=True,
            )
            db.session.commit()
            new_payback = _insert_payback_directly(
                source, seed_periods[1],
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Payback"].id,
                is_deleted=False,
            )
            db.session.commit()
            assert old_payback.is_deleted is True
            assert new_payback.is_deleted is False
            assert old_payback.id != new_payback.id

            # Both rows persist; the unique index treats only the
            # active row as occupying the slot.
            count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=source.id)
                .count()
            )
            assert count == 2

    def test_allows_paybacks_for_different_sources(
        self, app, db, seed_user, seed_periods,
    ):
        """Active paybacks for different source rows are independent."""
        with app.app_context():
            source_a = _make_projected_expense(seed_user, seed_periods, period_index=0)
            source_b = _make_projected_expense(seed_user, seed_periods, period_index=0)
            _insert_payback_directly(
                source_a, seed_periods[1],
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Payback"].id,
            )
            _insert_payback_directly(
                source_b, seed_periods[1],
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Payback"].id,
            )
            db.session.commit()
            # Both paybacks survived -- the index keys on
            # credit_payback_for_id, not on (source, period).
            count = (
                db.session.query(Transaction)
                .filter(
                    Transaction.credit_payback_for_id.in_(
                        [source_a.id, source_b.id]
                    )
                )
                .count()
            )
            assert count == 2

    def test_index_does_not_block_regular_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Regular transactions (NULL credit_payback_for_id) are unaffected.

        ``credit_payback_for_id IS NOT NULL`` excludes the
        overwhelming majority of rows from the index, so the
        constraint never fires when ordinary expenses are inserted.
        """
        with app.app_context():
            for i in range(5):
                _make_projected_expense(
                    seed_user, seed_periods, amount=f"{10 * (i + 1)}.00",
                )
            db.session.commit()
            null_count = (
                db.session.query(Transaction)
                .filter(Transaction.credit_payback_for_id.is_(None))
                .count()
            )
            # 5 expenses + 0 paybacks; all NULL credit_payback_for_id.
            assert null_count == 5


# ---------------------------------------------------------------------------
# Layer 2: the owner lock's serialisation of mark_as_credit
# ---------------------------------------------------------------------------


class TestMarkAsCreditTOCTOUPrevention:
    """``credit_workflow.mark_as_credit`` is safe under concurrent calls."""

    def test_double_call_is_idempotent(self, app, db, seed_user, seed_periods):
        """Sequential second call returns the existing payback (no duplicate)."""
        with app.app_context():
            source = _make_projected_expense(seed_user, seed_periods)
            first = credit_workflow.mark_as_credit(
                source.id, seed_user["user"].id,
            )
            db.session.commit()
            first_id = first.id
            second = credit_workflow.mark_as_credit(
                source.id, seed_user["user"].id,
            )
            db.session.commit()
            assert second.id == first_id, (
                "Second mark_as_credit must return the existing payback, "
                "not create a duplicate."
            )
            count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=source.id)
                .count()
            )
            assert count == 1

    def test_mark_after_softdelete_creates_new_payback(
        self, app, db, seed_user, seed_periods,
    ):
        """A re-mark after the prior payback was soft-deleted succeeds.

        The partial unique index leaves room for a new active payback
        once the previous one carries ``is_deleted = TRUE``; the
        service-level idempotency check sees no live payback and the
        normal create path runs.  Asserts the regression that the
        partial predicate is the right shape end-to-end.
        """
        with app.app_context():
            source = _make_projected_expense(seed_user, seed_periods)
            first = credit_workflow.mark_as_credit(
                source.id, seed_user["user"].id,
            )
            db.session.commit()
            first.is_deleted = True
            # Reset source back to projected so mark_as_credit's status
            # guard permits a re-mark.
            projected_id = (
                db.session.query(Status).filter_by(name="Projected").one().id
            )
            source.status_id = projected_id
            db.session.expire(source, ["status"])
            db.session.commit()

            second = credit_workflow.mark_as_credit(
                source.id, seed_user["user"].id,
            )
            db.session.commit()
            assert second.id != first.id, (
                "Re-mark after soft-delete must create a fresh payback."
            )
            # Both rows persist, but only one is active; the partial
            # unique index permits this.
            active_count = (
                db.session.query(Transaction)
                .filter_by(
                    credit_payback_for_id=source.id, is_deleted=False,
                )
                .count()
            )
            assert active_count == 1

    def test_concurrent_mark_credit_yields_one_payback(self, app, db):
        """Two simultaneous /mark-credit POSTs end with exactly one payback.

        The request's owner write lock serialises the threads at the
        database level: the losing request's transaction waits for it
        before reading the row, then reads the winner's committed CREDIT
        status and short-circuits to "return existing payback" without
        inserting a duplicate.  *Until plan step ``balance:X-bn`` a
        ``FOR NO KEY UPDATE`` inside ``mark_as_credit`` did this, and
        ``populate_existing()`` refreshed the row.*
        """
        data = _create_concurrent_user(db.session)
        source = _make_projected_expense(
            seed_user={
                "scenario": data["scenario"],
                "account": data["account"],
                "categories": data["categories"],
                "user": data["user"],
            },
            seed_periods=data["periods"],
        )
        db.session.commit()
        source_id = source.id

        # Single shared client with two concurrent requests -- see
        # ``_make_auth_client`` docstring for why two distinct clients
        # do not work against the same user account.
        client = _make_auth_client(
            app, "c19-concurrent@shekel.local", "c19concurrent",
        )

        resp_a, resp_b, err_a, err_b = _run_concurrent(
            app,
            lambda: client.post(f"/transactions/{source_id}/mark-credit"),
            lambda: client.post(f"/transactions/{source_id}/mark-credit"),
        )

        assert err_a is None, f"Thread A errored: {err_a!r}"
        assert err_b is None, f"Thread B errored: {err_b!r}"
        assert resp_a.status_code != 500, (
            f"Thread A 500'd: {resp_a.data[:200]!r}"
        )
        assert resp_b.status_code != 500, (
            f"Thread B 500'd: {resp_b.data[:200]!r}"
        )
        # Both threads must observe a successful 200 -- the loser
        # gets idempotent success from the idempotency check it reaches
        # after waiting on the owner's lock.
        assert resp_a.status_code == 200, (
            f"Thread A status {resp_a.status_code}: {resp_a.data[:200]!r}"
        )
        assert resp_b.status_code == 200, (
            f"Thread B status {resp_b.status_code}: {resp_b.data[:200]!r}"
        )

        # Final-state invariant: exactly one active payback exists.
        db.session.expire_all()
        active_paybacks = (
            db.session.query(Transaction)
            .filter_by(
                credit_payback_for_id=source_id, is_deleted=False,
            )
            .count()
        )
        assert active_paybacks == 1, (
            f"Expected 1 active payback, found {active_paybacks}"
        )


# ---------------------------------------------------------------------------
# Layer 2: the owner lock's serialisation of sync_entry_payback
# ---------------------------------------------------------------------------


class TestSyncEntryPaybackTOCTOUPrevention:
    """``entry_credit_workflow.sync_entry_payback`` is safe under
    concurrent entry mutations on the same parent."""

    def _make_envelope_template_and_txn(self, seed_user, seed_periods):
        """Insert an envelope (entry-tracked) definition + its engine-generated parent row."""
        template = make_expense_template(
            db.session, seed_user, amount="400.00", name="Tracked Groceries",
            category_key="Groceries", is_envelope=True,
        )
        return generate_row_of(template, seed_periods[0])

    def test_double_sync_with_no_credit_entries_is_noop(
        self, app, db, seed_user, seed_periods,
    ):
        """Two ``sync_entry_payback`` calls with no credit entries do
        nothing each time -- no payback created, no error raised."""
        with app.app_context():
            txn = self._make_envelope_template_and_txn(seed_user, seed_periods)
            db.session.commit()

            assert sync_entry_payback(txn.id, seed_user["user"].id) is None
            db.session.commit()
            assert sync_entry_payback(txn.id, seed_user["user"].id) is None
            db.session.commit()

            paybacks = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert paybacks == 0

    def test_concurrent_sync_yields_one_payback(self, app, db):
        """Two concurrent credit-flagged entry POSTs leave exactly one payback.

        Each thread inserts a credit entry on the same parent
        envelope transaction.  Unserialised, the two
        ``sync_entry_payback`` calls would both find no existing
        payback and both insert one.  **This grades the OUTCOME, not a
        lock**: two locks each serialise the pair -- the request's owner
        lock (plan step ``balance:X-bn``; C-19's row lock until then) and
        the parent row's ``FOR NO KEY UPDATE`` that the arrival trigger
        takes on every purchase INSERT (:mod:`app.deleted_row_infrastructure`,
        ruling **R-CC96**) -- so it passes with the owner lock removed
        (measured 2026-09-29, 3 of 3 runs, while the Mark Credit race above
        failed 3 of 3).  The owner lock's order on the purchase route is
        graded by ``tests/test_services/test_cc5_4a4_row_lock_races/test_the_database_locks.py``'s
        ``TestTheOwnersLockComesFirst::test_the_purchase_door``.  The
        database ends up with one payback whose amount equals the sum of
        both entries.
        """
        data = _create_concurrent_user(db.session)
        # The threaded owner's envelope definition and its engine-generated
        # parent row; the owner dict has the shape the shared builder reads.
        template = make_expense_template(
            db.session, data, amount="400.00", name="Concurrent Tracked",
            category_key="Groceries", is_envelope=True,
        )
        txn = generate_row_of(template, data["periods"][0])
        db.session.commit()
        txn_id = txn.id

        # Single shared client (see ``_make_auth_client`` docstring).
        client = _make_auth_client(
            app, "c19-concurrent@shekel.local", "c19concurrent",
        )

        # The APP's today.  ``entry_service._reject_future_purchase_date`` refuses
        # an entry dated after ``display_today()`` (ruling R-M), so posting the
        # PROCESS date makes this a 400 for the four hours a day the two
        # calendars disagree -- which is exactly how it failed CI at 23:39 ET
        # on 2026-07-31.
        today_iso = display_today().isoformat()

        def _post_entry(amount):
            return client.post(
                f"/transactions/{txn_id}/entries",
                data={
                    "amount": amount,
                    "description": "Concurrent purchase",
                    "purchased_on": today_iso,
                    "is_credit": "true",
                },
            )

        resp_a, resp_b, err_a, err_b = _run_concurrent(
            app,
            lambda: _post_entry("50.00"),
            lambda: _post_entry("75.00"),
        )

        assert err_a is None, f"Thread A errored: {err_a!r}"
        assert err_b is None, f"Thread B errored: {err_b!r}"
        assert resp_a.status_code != 500, (
            f"Thread A 500'd: {resp_a.data[:200]!r}"
        )
        assert resp_b.status_code != 500, (
            f"Thread B 500'd: {resp_b.data[:200]!r}"
        )
        assert resp_a.status_code == 200, (
            f"Thread A status {resp_a.status_code}: {resp_a.data[:200]!r}"
        )
        assert resp_b.status_code == 200, (
            f"Thread B status {resp_b.status_code}: {resp_b.data[:200]!r}"
        )

        db.session.expire_all()
        # Exactly one active payback should exist after both threads
        # complete.
        active_paybacks = (
            db.session.query(Transaction)
            .filter_by(credit_payback_for_id=txn_id, is_deleted=False)
            .all()
        )
        assert len(active_paybacks) == 1, (
            f"Expected 1 active payback, got {len(active_paybacks)}: "
            f"{[(p.id, p.estimated_amount) for p in active_paybacks]!r}"
        )
        # Both entries are flagged credit; the payback's amount must
        # equal their sum (50 + 75 = 125).
        assert active_paybacks[0].estimated_amount == Decimal("125.00"), (
            f"Payback amount {active_paybacks[0].estimated_amount} does "
            "not match sum of both credit entries (125.00)"
        )
        # And both entries must point at the single payback.
        entries = (
            db.session.query(TransactionEntry)
            .filter_by(transaction_id=txn_id, is_credit=True)
            .all()
        )
        assert len(entries) == 2
        assert all(
            entry.credit_payback_id == active_paybacks[0].id
            for entry in entries
        ), "Both credit entries must link to the surviving payback."


# ---------------------------------------------------------------------------
# Layer 3: Route-level IntegrityError catch
# ---------------------------------------------------------------------------


class TestMigrationPreFlightCheck:
    """The C-19 migration's upgrade() refuses to run when duplicates
    exist in the live data."""

    def test_upgrade_and_downgrade_round_trip(self, app, db):
        """``upgrade()`` creates the index, ``downgrade()`` drops it.

        Smoke test for the migration's reversibility: starting from
        the post-``db.create_all()`` state where the index exists,
        run ``downgrade()`` (drops the index), assert the catalog no
        longer carries it, then run ``upgrade()`` (recreates the
        index against an empty data set, so the pre-flight passes
        trivially), and assert the catalog carries it again.

        Bootstraps an Alembic ``MigrationContext`` against the test
        database connection so the migration's ``op.create_index`` /
        ``op.drop_index`` calls reach a real schema-mutation backend
        rather than the unbound proxy that Alembic installs at
        import time.
        """
        # pylint: disable=import-outside-toplevel
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        from migrations.versions.b3d8f4a01c92_add_partial_unique_index_for_credit_ import (
            INDEX_NAME, downgrade, upgrade,
        )

        with app.app_context():
            # Sanity: index exists before we start (created by
            # db.create_all() in setup_database fixture).
            present = db.session.execute(text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_indexes "
                "  WHERE schemaname = 'budget' AND indexname = :name"
                ")"
            ), {"name": INDEX_NAME}).scalar()
            assert present is True, (
                f"Pre-condition failed: {INDEX_NAME} missing from "
                "schema before round-trip test ran."
            )

            try:
                # Bind a fresh MigrationContext against the test
                # session's connection so ``op.drop_index`` /
                # ``op.create_index`` resolve to real DDL through
                # Alembic's Operations proxy.
                # ``Operations.context(migration_context)`` is the
                # classmethod that registers the proxy for the
                # duration of the ``with`` block.
                ctx = MigrationContext.configure(
                    connection=db.session.connection(),
                )
                with Operations.context(ctx):
                    # 1. Drop via the migration's downgrade().
                    downgrade()

                    after_downgrade = db.session.execute(text(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM pg_indexes "
                        "  WHERE schemaname = 'budget' "
                        "    AND indexname = :name"
                        ")"
                    ), {"name": INDEX_NAME}).scalar()
                    assert after_downgrade is False, (
                        f"downgrade() failed to drop {INDEX_NAME}."
                    )

                    # 2. Recreate via the migration's upgrade().
                    upgrade()

                    after_upgrade = db.session.execute(text(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM pg_indexes "
                        "  WHERE schemaname = 'budget' "
                        "    AND indexname = :name"
                        ")"
                    ), {"name": INDEX_NAME}).scalar()
                    assert after_upgrade is True, (
                        f"upgrade() failed to re-create {INDEX_NAME}."
                    )
                db.session.commit()
            finally:
                # Restore the partial unique index even if the test
                # raised mid-run.  ``IF NOT EXISTS`` makes this a
                # no-op when the round trip completed cleanly.
                db.session.rollback()
                db.session.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
                    "ON budget.transactions (credit_payback_for_id) "
                    "WHERE credit_payback_for_id IS NOT NULL "
                    "  AND is_deleted = FALSE"
                ))
                db.session.commit()

    def test_upgrade_raises_when_duplicates_present(
        self, app, db, seed_user, seed_periods,
    ):
        """Pre-flight raises a clear error listing the offending rows.

        Simulates the production-data state that would have existed
        before C-19: two active paybacks pointing at the same source
        transaction.  Drops the partial unique index first (since
        ``db.create_all()`` already installed it as part of the test
        fixture) so the duplicate insert can succeed and the
        migration's pre-flight check has something to detect.
        Restores the index afterwards so subsequent tests run against
        the normal schema.
        """
        # pylint: disable=import-outside-toplevel
        from migrations.versions.b3d8f4a01c92_add_partial_unique_index_for_credit_ import (
            INDEX_NAME, upgrade,
        )

        with app.app_context():
            # 1. Drop the partial unique index so we can sneak duplicates in.
            db.session.execute(text(
                f"DROP INDEX IF EXISTS budget.{INDEX_NAME}"
            ))
            db.session.commit()

            try:
                # 2. Insert two active paybacks for the same source.
                source = _make_projected_expense(seed_user, seed_periods)
                _insert_payback_directly(
                    source, seed_periods[1],
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=seed_user["categories"]["Payback"].id,
                )
                _insert_payback_directly(
                    source, seed_periods[1],
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    category_id=seed_user["categories"]["Payback"].id,
                )
                db.session.commit()

                # 3. Run the migration's upgrade() against the dirty DB.
                #    The pre-flight should raise with a clear message.
                with pytest.raises(RuntimeError) as excinfo:
                    # The migration's upgrade() uses op.get_bind() which
                    # requires an Alembic migration context.  Patch
                    # op.get_bind to return our test connection so the
                    # pre-flight query runs against the live test DB.
                    from alembic import op  # pylint: disable=import-outside-toplevel
                    with patch.object(op, "get_bind", return_value=db.session.connection()):
                        # Patch op.create_index too -- if the
                        # pre-flight passed (it must not), the
                        # subsequent CREATE INDEX would raise its
                        # own IntegrityError that would mask the
                        # cleaner pre-flight failure we expect.
                        with patch.object(op, "create_index"):
                            upgrade()
                # 4. Error message must name the index and the
                #    offending source transaction id.
                msg = str(excinfo.value)
                assert INDEX_NAME in msg, msg
                assert f"source_txn_id={source.id}" in msg, msg
                assert "count=2" in msg, msg
            finally:
                # Restore the partial unique index for subsequent tests.
                # First clean up the duplicate we inserted so the
                # CREATE UNIQUE INDEX succeeds.
                db.session.rollback()
                db.session.execute(text(
                    "DELETE FROM budget.transactions "
                    "WHERE credit_payback_for_id IS NOT NULL"
                ))
                db.session.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
                    "ON budget.transactions (credit_payback_for_id) "
                    "WHERE credit_payback_for_id IS NOT NULL "
                    "  AND is_deleted = FALSE"
                ))
                db.session.commit()


class TestMarkCreditRouteIntegrityErrorCatch:
    """The mark_credit route converts the partial-unique-index violation
    into an idempotent 200 response."""

    def test_unique_violation_returns_200(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Pre-seed a payback so the route's own INSERT collides.

        Inserts an active payback for the source row outside any
        request, so no owner lock orders it against the route that
        follows.  The route's call to ``mark_as_credit`` then runs the
        normal flow but sees ``status_id == projected`` (we leave the
        source row's status alone), so the idempotency check does
        not short-circuit; the eventual ``db.session.add(payback)``
        flush triggers the unique-index violation and the route's
        ``except IntegrityError`` branch fires.

        Verifies the user-facing contract: 200 OK with an HTMX
        ``gridRefresh`` trigger, identical to a serialised second
        click.
        """
        with app.app_context():
            source = _make_projected_expense(seed_user, seed_periods)
            # Pre-seed a duplicate-blocking payback that the service
            # cannot see via its idempotency check (because source's
            # status is still "Projected", and the idempotency clause
            # only fires when status == "Credit").
            _insert_payback_directly(
                source, seed_periods[1],
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                category_id=seed_user["categories"]["Payback"].id,
            )
            db.session.commit()
            source_id = source.id

        # The pre-seed leaves the system in a state that mark_as_credit
        # naturally cannot reach via its own service path; that's the
        # whole point -- the route's IntegrityError catch is the
        # backstop.
        resp = auth_client.post(f"/transactions/{source_id}/mark-credit")
        assert resp.status_code == 200, (
            f"Expected idempotent 200, got {resp.status_code}: "
            f"{resp.data[:200]!r}"
        )
        assert resp.headers.get("HX-Trigger") == "gridRefresh"

        # Active payback count remains 1 -- the duplicate INSERT was
        # rolled back.
        with app.app_context():
            active = (
                db.session.query(Transaction)
                .filter_by(
                    credit_payback_for_id=source_id, is_deleted=False,
                )
                .count()
            )
            assert active == 1

    def test_other_integrity_errors_still_return_400(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Non-credit-payback IntegrityErrors do not silently 200.

        Patches ``credit_workflow.mark_as_credit`` to raise an
        ``IntegrityError`` whose ``orig.diag.constraint_name`` is a
        different name -- the route's discriminator must reject it
        and surface the standard 400 response so unrelated FK or
        check-constraint failures stay visible.
        """
        with app.app_context():
            source = _make_projected_expense(seed_user, seed_periods)
            db.session.commit()
            source_id = source.id

        # Build an IntegrityError that names a different constraint;
        # the route must NOT treat it as idempotent success.
        class _FakeDiag:  # pylint: disable=too-few-public-methods
            constraint_name = "ck_some_unrelated_constraint"

        class _FakeOrig(Exception):
            diag = _FakeDiag()

        forged = IntegrityError(
            "stmt", {}, _FakeOrig("forged-other-violation"),
        )

        def _raise_other_integrity_error(*_args, **_kwargs):
            raise forged

        with patch(
            "app.routes.transactions.mutations.credit_workflow.mark_as_credit",
            _raise_other_integrity_error,
        ):
            resp = auth_client.post(f"/transactions/{source_id}/mark-credit")
        assert resp.status_code == 400, (
            f"Non-credit-payback IntegrityError must surface as 400, "
            f"got {resp.status_code}: {resp.data[:200]!r}"
        )
