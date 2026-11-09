"""
Shekel Budget App -- Concurrent Modification Tests (L-15)

Verifies that critical financial endpoints handle simultaneous requests
without data corruption.  Uses threading.Barrier to synchronize two
threads hitting the same endpoint at the same instant against a real
PostgreSQL database.

Each test creates its own data, runs two concurrent operations, and
asserts that the final database state satisfies an invariant.  The
tests do not assert which thread "wins" -- only that the outcome is
consistent and no data is lost or corrupted.

No application code is modified.  If a test reveals an actual race
condition bug, it is documented with a comment and marked xfail.
"""

import threading
from urllib.parse import urlsplit
from datetime import date, timedelta
from decimal import Decimal


from app.models.account import Account, AccountAnchorHistory
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services import (
    account_service,
    pay_schedule_service,
)
from tests._test_helpers import (
    assert_pay_period_invariants,
    capture_sql_statements,
    last_covered_day,
    mint_fixture_era,
    one_off_row_of,
    open_books_before_the_first_assertion,
    open_owner_calendar,
    resolved_amount,
)
from app.services import cash_ledger
from app.utils.dates import display_today


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_client(app, email, password):
    """Create and authenticate a test client for use in a concurrent thread.

    Each thread must have its own client to avoid session interference.
    Uses use_cookies=True (default) and a fresh client instance so
    each client maintains its own session cookie.

    Args:
        app:      The Flask application (session-scoped fixture).
        email:    User email to log in with.
        password: User password.

    Returns:
        A logged-in Flask test client.
    """
    # use_cookies=True is the default but being explicit here.
    client = app.test_client(use_cookies=True)
    resp = client.post("/login", data={
        "email": email,
        "password": password,
    })
    assert resp.status_code == 302, (
        f"Thread client login failed for {email}: {resp.status_code}"
    )
    return client


def _run_concurrent(app, func_a, func_b, timeout=10):
    """Run two callables concurrently using a barrier for synchronization.

    Both functions receive no arguments and are expected to return an
    HTTP response or similar result.  Exceptions from either thread
    are captured and re-raised in the main thread.

    Args:
        app:     The Flask application (needed for app context in threads).
        func_a:  Callable for thread A.
        func_b:  Callable for thread B.
        timeout: Seconds to wait for each thread to finish.

    Returns:
        Tuple (result_a, result_b).
    """
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

    if errors[0]:
        raise errors[0]
    if errors[1]:
        raise errors[1]

    return results[0], results[1]


# ---------------------------------------------------------------------------
# Shared Fixture Data
# ---------------------------------------------------------------------------


def _create_user_with_data(db_session):
    """Create a user with a checking account, scenario, category, and periods.

    Returns a dict with all the objects needed by the concurrent tests.
    """
    user = User(
        email="concurrent@shekel.local",
        password_hash=hash_password("concurrent12"),
        display_name="Concurrent User",
    )
    db_session.add(user)
    db_session.flush()

    settings = UserSettings(user_id=user.id)
    db_session.add(settings)

    # Pay periods must exist before the account so the E-19 factory
    # has an anchor to assign.  Two periods: past, and current
    # (containing today).
    #
    # **The APP's civil day, never the process's.**  ``pay_period_admin
    # .top_up_rolling_window`` defaults ``as_of`` to ``display_today()``, so a
    # fixture built from ``date.today()`` places its period boundaries on a
    # DIFFERENT day whenever the process timezone has already rolled over and
    # the display one has not.  CI pins ``TZ=Pacific/Kiritimati`` (UTC+14)
    # exactly to catch that, and on 2026-08-23 it did: the process read Monday
    # 08-24 while the app read Sunday 08-23, so the "past" period still ended
    # ON the app's today, counted INSIDE the rolling window, and the deficit
    # came out 3 where the test expected 4.  It fires only when the two clocks
    # straddle a Sunday/Monday boundary, which is why it took until the first
    # CI run inside that window to appear.
    today = display_today()
    current_start = today - timedelta(days=today.weekday())  # Monday this week
    # TWO periods from one batch, through the writer that owns the table.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    # A two-payday batch at a fortnightly cadence derives exactly the pair
    # this built by hand: the first ends the day before the second opens, and
    # the second ends its own payday plus thirteen.
    past_period, current_period = open_owner_calendar(
        user.id, current_start - timedelta(days=14), num_periods=2,
    )

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
    # Its BOOKS open before anything this fixture dates (plan step
    # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
    # asserts -- the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db_session, account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db_session.add(scenario)

    category = Category(
        user_id=user.id,
        group_name="Home",
        item_name="Test Expense",
    )
    db_session.add(category)
    db_session.commit()

    return {
        "user": user,
        "account": account,
        "scenario": scenario,
        "category": category,
        "past_period": past_period,
        "current_period": current_period,
    }


# ---------------------------------------------------------------------------
# Test Scenario 1: Simultaneous mark-done on the same transaction
# ---------------------------------------------------------------------------


class TestConcurrentMarkDone:
    """Verify concurrent mark-done requests produce consistent state.

    Two threads simultaneously POST /transactions/<id>/mark-done on
    the same projected transaction.  The invariant is that after both
    complete, the transaction has a settled status (Paid or Received)
    and was transitioned exactly once -- no duplicate writes, no
    corruption, no 500 errors.
    """

    def test_concurrent_mark_done_expense(self, app, db):
        """Two threads mark the same expense as done simultaneously.

        Invariant: transaction ends up with status=Paid, no 500 errors.
        """
        data = _create_user_with_data(db.session)
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        txn = one_off_row_of(
            data["past_period"],
            name="Rent",
            amount=Decimal("1500.00"),
            user_id=data['past_period'].user_id,
            account_id=data["account"].id,
            scenario_id=data["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=data["category"].id,
        )
        db.session.commit()
        txn_id = txn.id
        client_a = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )
        client_b = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client_a.post(f"/transactions/{txn_id}/mark-done"),
            lambda: client_b.post(f"/transactions/{txn_id}/mark-done"),
        )

        # Neither request should produce a 500.
        assert resp_a.status_code != 500, f"Thread A got 500: {resp_a.data[:200]}"
        assert resp_b.status_code != 500, f"Thread B got 500: {resp_b.data[:200]}"

        # At least one must succeed.
        assert resp_a.status_code == 200 or resp_b.status_code == 200, (
            f"Neither thread succeeded: A={resp_a.status_code}, B={resp_b.status_code}"
        )

        # Invariant: transaction is in a settled (paid) state.
        db.session.expire_all()
        final = db.session.get(Transaction, txn_id)
        assert final is not None
        assert final.status.is_settled, (
            f"Transaction should be settled, got status '{final.status.name}'"
        )

    def test_concurrent_mark_done_income(self, app, db):
        """Two threads mark the same income as received simultaneously.

        Invariant: transaction ends up with status=Received.
        """
        data = _create_user_with_data(db.session)
        income_type = (
            db.session.query(TransactionType).filter_by(name="Income").one()
        )

        txn = one_off_row_of(
            data["past_period"],
            name="Paycheck",
            amount=Decimal("3000.00"),
            user_id=data['past_period'].user_id,
            account_id=data["account"].id,
            scenario_id=data["scenario"].id,
            transaction_type_id=income_type.id,
            category_id=data["category"].id,
        )
        db.session.commit()
        txn_id = txn.id
        client_a = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )
        client_b = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client_a.post(f"/transactions/{txn_id}/mark-done"),
            lambda: client_b.post(f"/transactions/{txn_id}/mark-done"),
        )

        assert resp_a.status_code != 500
        assert resp_b.status_code != 500

        db.session.expire_all()
        final = db.session.get(Transaction, txn_id)
        assert final is not None
        assert final.status.is_settled, (
            f"Income should be settled, got status '{final.status.name}'"
        )


# ---------------------------------------------------------------------------
# Test Scenario 2: Carry-forward during transaction edit
# ---------------------------------------------------------------------------


class TestConcurrentCarryForwardAndEdit:
    """Verify carry-forward and transaction edit running simultaneously.

    Thread A carries forward projected transactions from a past period
    to the current period.  Thread B edits the estimated_amount of a
    transaction in that same past period.  The invariant is that the
    transaction ends up in exactly one period with a valid amount --
    not lost, not duplicated.
    """

    def test_carry_forward_during_edit(self, app, db):
        """Transaction is not lost or duplicated when carry-forward and edit race.

        Invariant: after both threads complete, the transaction exists
        exactly once across all periods with a valid amount.
        """
        data = _create_user_with_data(db.session)
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        txn = one_off_row_of(
            data["past_period"],
            name="Groceries",
            amount=Decimal("100.00"),
            user_id=data['past_period'].user_id,
            account_id=data["account"].id,
            scenario_id=data["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=data["category"].id,
        )
        db.session.commit()
        txn_id = txn.id
        past_period_id = data["past_period"].id
        current_period_id = data["current_period"].id
        client_a = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )
        client_b = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client_a.post(f"/pay-periods/{past_period_id}/carry-forward"),
            lambda: client_b.patch(
                f"/transactions/{txn_id}",
                data={"estimated_amount": "200.00"},
            ),
        )

        # Neither request should produce a 500.
        assert resp_a.status_code != 500, f"Carry-forward got 500: {resp_a.data[:200]}"
        assert resp_b.status_code != 500, f"Edit got 500: {resp_b.data[:200]}"

        # Invariant: the transaction exists exactly once (not deleted,
        # not duplicated) and is in either the past or current period.
        db.session.expire_all()
        final = db.session.get(Transaction, txn_id)
        assert final is not None, "Transaction was lost"
        assert not final.is_deleted, "Transaction was unexpectedly deleted"
        assert final.pay_period_id in (past_period_id, current_period_id), (
            f"Transaction in unexpected period {final.pay_period_id}"
        )
        # Amount must be one of the valid values -- read through the resolver:
        # a one-off's row stores no figure, its definition prices it (ruling
        # R-BAL60); a typed figure that landed would have restated it.
        worth = resolved_amount(final)
        assert worth in (
            Decimal("100.00"), Decimal("200.00"),
        ), f"Unexpected amount: {worth}"


# ---------------------------------------------------------------------------
# Test Scenario 3: Simultaneous anchor balance updates
# ---------------------------------------------------------------------------


class TestConcurrentAnchorUpdate:
    """Verify concurrent balance ASSERTIONS produce consistent state.

    Two threads simultaneously PATCH /accounts/<id>/true-up with
    different balance values.  The invariants:

      1. Neither request produces a 500.
      2. BOTH return 200, and BOTH assertions are recorded.
      3. The resolved balance is one of the two submitted values.

    **Invariant 2 was the opposite until plan step X-f1c3c** (ruling R-EN),
    and this docstring is rewritten rather than annotated because the old
    text read as instructions to restore the deleted behaviour.  It said:
    after commit C-17 the route is no longer last-write-wins, the loser
    detects a stale ``version_id`` at flush time, and the route answers 409 --
    so at least one request returns 200 and any non-200 must be a 409.

    None of that survives, because the true-up stopped writing the row the
    lock guarded.  A true-up APPENDS an assertion, so there is nothing to
    contend for: two assertions of different balances are two FACTS, the
    later-observed one is current, and neither is lost.  **A 409 here is now a
    regression, not a contract.**  Two threads asserting the SAME balance on
    the same day are still idempotent -- since ruling R-EQ the write door
    compares against the governing assertion under the owner's write lock, so
    the waiter sees the winner's row, writes nothing and reports success.

    **A fourth invariant graded the STORED ledger until plan#2914**: the
    account's stored linked ledger summed to the resolved assertion.  A
    true-up also reconciles the account's stored corrections, a
    read-modify-write that two interleaved requests could leave apart from the
    assertions; the serialisation is explicit and per-owner
    (:mod:`app.services.user_write_lock`), with its own deterministic tests in
    ``tests/test_services/test_user_write_lock.py``.  The worked-out ledger is
    computed from the assertions themselves and writes nothing two requests
    could race on, so that invariant went with the suite's grading of the
    store.

    The old "tolerance for serialised-without-contention runs" paragraph is
    gone with the branch it excused: {200, 200} is no longer the rare
    scheduler-dependent outcome, it is the only correct one, so this no longer
    passes for two different reasons depending on the OS scheduler.
    """

    def test_concurrent_true_up(self, app, db):
        """Two threads assert different balances simultaneously; both are kept.

        See the class docstring for the invariant set and for what ruling
        R-EN changed.  Non-vacuity: the two asserted values are read back out
        of ``account_anchor_history``, so a pair of 200s that recorded nothing
        fails here.
        """
        data = _create_user_with_data(db.session)
        account_id = data["account"].id
        # Single authenticated client -- both threads share it.
        # Flask test client is not truly thread-safe, but for this
        # test the requests are synchronized by the barrier and
        # don't overlap at the session-cookie level.
        client = _make_auth_client(
            app, "concurrent@shekel.local", "concurrent12",
        )

        resp_a, resp_b = _run_concurrent(
            app,
            lambda: client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "2000.00"},
            ),
            lambda: client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "3000.00"},
            ),
        )

        # Invariant 1: neither request produces a 500.
        assert resp_a.status_code != 500, f"Thread A got 500: {resp_a.data[:200]}"
        assert resp_b.status_code != 500, f"Thread B got 500: {resp_b.data[:200]}"

        # Invariant 3: BOTH requests succeed, and that is ruling R-EN.
        # This used to require "200 for the winner, 409 for the loser" -- the
        # C-17 optimistic lock, which fired because a true-up UPDATEd the
        # ``accounts`` row.  It appends an ASSERTION now, so there is no row
        # to contend for and nothing for a loser to have overwritten: two
        # assertions of different balances are two facts, and the later-observed
        # one is current.  A 409 here would be a regression, not a contract.
        for label, resp in (("A", resp_a), ("B", resp_b)):
            assert resp.status_code == 200, (
                f"Thread {label} returned {resp.status_code}; an append-only "
                f"assertion has no conflict to report.  Body: {resp.data[:200]}"
            )

        # Invariant 2: BOTH assertions are recorded -- neither is lost -- and
        # the resolved balance is whichever of them is current.  The original
        # 5000.00 is superseded by both.
        db.session.expire_all()
        final = db.session.get(Account, account_id)
        assert final is not None
        asserted = {
            row.anchor_balance
            for row in db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            )
        }
        assert {Decimal("2000.00"), Decimal("3000.00")} <= asserted, (
            f"Both concurrent assertions must survive; recorded: {asserted}"
        )
        resolved = cash_ledger.resolve_anchor(final).balance
        assert resolved in (Decimal("2000.00"), Decimal("3000.00")), (
            f"Resolved balance is {resolved}, "
            f"expected one of the two concurrently asserted values"
        )


# ---------------------------------------------------------------------------
# Test Scenario 4: Concurrent rolling-window top-ups
# ---------------------------------------------------------------------------


class TestConcurrentRollingTopUp:
    """Verify concurrent rolling top-ups never land a duplicate payday.

    The continuous rolling window is refilled on every grid / dashboard
    load, so two requests can hit ``top_up_rolling_window`` for the same
    user at the same instant.  ``uq_pay_periods_user_start`` is the hard
    guard against a duplicate payday; the per-user advisory lock is the UX
    layer that turns the racing loser's would-be IntegrityError into a clean
    re-read-and-no-op.  These tests assert the combined contract: no 500, no
    duplicate payday, the window filled exactly to target, and the structure
    invariants intact.

    **Driven through the production doors** -- ``GET /grid`` and ``POST
    /pay-periods/extend`` -- because since plan step ``balance:X-bn`` the
    lock is the REQUEST's: ``/grid``'s ``write_transaction`` block and the
    extend route's command transaction each take the owner's lock where they
    begin (:mod:`app.db_transaction`), and neither service takes one.  Two
    threads calling the services directly race with no lock at all, which
    production never does.  *Until that step these threads called
    ``top_up_rolling_window`` and ``extend_pay_periods`` directly and the
    services locked for themselves.*
    """

    @staticmethod
    def _enable_rolling(db_session, user_id, target):
        """Give the user a schedule row with rolling on at ``target``."""
        mint_fixture_era(user_id, date(2026, 1, 2), 14)
        pay_schedule_service.set_rolling(
            user_id, enabled=True, target_periods=target,
        )
        db_session.commit()

    @staticmethod
    def _two_signed_in_clients(app):
        """Two clients, each signed in as the concurrent user.

        Returns:
            ``(client_a, client_b)``, both signed in as the concurrent user.
        """
        clients = []
        for _ in range(2):
            clients.append(_make_auth_client(
                app, "concurrent@shekel.local", "concurrent12",
            ))
        return clients[0], clients[1]

    def test_concurrent_topups_one_fills_one_noops(self, app, db):
        """Two simultaneous grid loads: one fills the deficit, the other no-ops.

        With one current period and a target of 5 (deficit 4), exactly one
        of the two requests appends: one fills the deficit and the other --
        serialised behind the owner's lock -- re-reads a full window and
        appends nothing.  Counted over both threads' statements, because
        which request won is not observable from its page.  No
        IntegrityError, no duplicate payday, exactly 5 current-and-future
        periods afterward.
        """
        data = _create_user_with_data(db.session)
        user_id = data["user"].id
        self._enable_rolling(db.session, user_id, target=5)
        client_a, client_b = self._two_signed_in_clients(app)

        (resp_a, resp_b), statements = capture_sql_statements(
            lambda: _run_concurrent(
                app,
                lambda: client_a.get("/grid"),
                lambda: client_b.get("/grid"),
            ),
        )

        assert (resp_a.status_code, resp_b.status_code) == (200, 200)
        inserts = [
            sql for sql, _params in statements
            if sql.lstrip().upper().startswith("INSERT INTO BUDGET.PAY_PERIODS")
        ]
        # ONE append statement across both loads (SQLAlchemy sends the four
        # paydays as one multi-row INSERT): one load filled the deficit and
        # the other appended nothing.  That it was all four, and no more, is
        # the exactly-five window below.
        assert len(inserts) == 1, (
            f"expected one load to append the deficit and the other nothing, "
            f"got {len(inserts)} append statements"
        )

        db.session.expire_all()
        periods = db.session.query(PayPeriod).filter_by(user_id=user_id).all()
        # **Keyed on the PAYDAY, which is the only thing a race can duplicate
        # since plan step ``pay_calendar:C4-c``.**  This asserted that the
        # ORDINALS were distinct, and that became a theorem the moment the
        # ordinal stopped being a column: it is the row's position in the
        # owner's sorted payday set, so over an owner's COMPLETE set it is
        # exactly ``0..n-1`` and the assertion could not fail for any database
        # state -- including one where two appenders had both landed.  The
        # payday is what ``uq_pay_periods_user_start`` protects and what a
        # racing append can really collide on (adversarial review, 2026-09-01).
        paydays = [period.start_date for period in periods]
        assert len(paydays) == len(set(paydays)), (
            f"two appends landed the same payday: {sorted(paydays)}"
        )
        # And the schedule the race leaves behind is still ON CADENCE -- a
        # second appender computing its floor from a stale read would append at
        # some other spacing, which distinct paydays alone would not catch.
        gaps = {
            (later - earlier).days
            for earlier, later in zip(sorted(paydays), sorted(paydays)[1:])
        }
        # The OWNER's own stored cadence, not a constant restated here: it is
        # what the top-up appends at and what the derivation projects the last
        # period from, so this cannot go stale if the fixture's cadence moves.
        cadence = pay_schedule_service.resolve_cadence(user_id).days
        assert gaps == {cadence}, (
            f"the race left an off-cadence schedule: gaps {sorted(gaps)} "
            f"against a stored cadence of {cadence}"
        )
        # The same clock the door counted with; see ``_create_user_with_data``.
        future = [p for p in periods if last_covered_day(p) >= display_today()]
        assert len(future) == 5, (
            f"window should hold exactly the target of 5, found {len(future)}"
        )
        assert_pay_period_invariants(db.session, user_id)

    def test_topup_racing_manual_extend_no_duplicate(self, app, db):
        """A grid load racing a manual extend never lands a duplicate payday.

        The rolling top-up and the manual extend are both append paths;
        they serialise on the owner's lock, so neither hits the unique
        constraint as a 500.  Regardless of which ran first, every payday is
        unique, the window is at least the rolling target, and the structure
        invariants hold.

        **Both requests must really append**, or nothing raced.  With one
        current period and a target of 5, either order appends twice: the
        top-up's four then the extend's three, or the extend's three then the
        top-up's one.  So exactly two append statements are counted over both
        threads.  The extend answers the same redirect to the same page on
        success and on a refusal (``app/routes/pay_periods.py``'s
        ``_append_periods`` and ``extend``), and a refusal raised by the
        repopulation rolls back an append already sent, so the extend's
        success flash, set only after its commit, is asserted as well.
        """
        data = _create_user_with_data(db.session)
        user_id = data["user"].id
        self._enable_rolling(db.session, user_id, target=5)
        client_a, client_b = self._two_signed_in_clients(app)

        (resp_a, resp_b), statements = capture_sql_statements(
            lambda: _run_concurrent(
                app,
                lambda: client_a.get("/grid"),
                lambda: client_b.post(
                    "/pay-periods/extend", data={"num_periods": "3"},
                ),
            ),
        )

        assert resp_a.status_code == 200, resp_a.status_code
        assert resp_b.status_code == 302, resp_b.status_code
        assert urlsplit(resp_b.headers["Location"]).path != "/login", (
            "the extend was turned away at sign-in, so nothing raced"
        )
        inserts = [
            sql for sql, _params in statements
            if sql.lstrip().upper().startswith("INSERT INTO BUDGET.PAY_PERIODS")
        ]
        assert len(inserts) == 2, (
            f"expected the top-up and the extend to append once each, got "
            f"{len(inserts)} append statements"
        )
        with client_b.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert ("success", "Added 3 pay periods.") in flashes, (
            f"the extend did not commit its append: {flashes}"
        )

        db.session.expire_all()
        periods = db.session.query(PayPeriod).filter_by(user_id=user_id).all()
        # **Keyed on the PAYDAY, which is the only thing a race can duplicate
        # since plan step ``pay_calendar:C4-c``.**  This asserted that the
        # ORDINALS were distinct, and that became a theorem the moment the
        # ordinal stopped being a column: it is the row's position in the
        # owner's sorted payday set, so over an owner's COMPLETE set it is
        # exactly ``0..n-1`` and the assertion could not fail for any database
        # state -- including one where two appenders had both landed.  The
        # payday is what ``uq_pay_periods_user_start`` protects and what a
        # racing append can really collide on (adversarial review, 2026-09-01).
        paydays = [period.start_date for period in periods]
        assert len(paydays) == len(set(paydays)), (
            f"two appends landed the same payday: {sorted(paydays)}"
        )
        # And the schedule the race leaves behind is still ON CADENCE -- a
        # second appender computing its floor from a stale read would append at
        # some other spacing, which distinct paydays alone would not catch.
        gaps = {
            (later - earlier).days
            for earlier, later in zip(sorted(paydays), sorted(paydays)[1:])
        }
        # The OWNER's own stored cadence, not a constant restated here: it is
        # what the top-up appends at and what the derivation projects the last
        # period from, so this cannot go stale if the fixture's cadence moves.
        cadence = pay_schedule_service.resolve_cadence(user_id).days
        assert gaps == {cadence}, (
            f"the race left an off-cadence schedule: gaps {sorted(gaps)} "
            f"against a stored cadence of {cadence}"
        )
        # The same clock the door counted with; see ``_create_user_with_data``.
        future = [p for p in periods if last_covered_day(p) >= display_today()]
        assert len(future) >= 5, (
            f"window should be filled to at least the target of 5, "
            f"found {len(future)}"
        )
        assert_pay_period_invariants(db.session, user_id)
