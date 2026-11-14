"""Plan step balance:X-bn -- where a request's owner lock is taken, and where it ends.

The owner's write lock is taken where each command transaction a signed-in
request opens begins (:mod:`app.db_transaction`).  Four rulings and one rule-5
approval decide the edges of that, and each is graded here with the arm that
proves it fires:

* **R-CC121** ("Lock, then check"): sign-in finds the account, takes the
  signing-in person's owner lock, and only then reads the codes or a known
  browser's count.  Graded by statement ORDER on a known browser's password
  step (a wrong password writes its record, ``auth.known_devices``) and the
  backup-code step (the code check reads ``auth.mfa_configs``), by the
  browser's count being read under the lock and a race proving it (another
  transaction raises the count while the sign-in waits, and the sign-in
  counts on top of it), and by a held key making each step WAIT, with the
  free-key control beside it.  A companion's sign-in takes its OWNER's key.
  A STRANGER's password step takes the key only after its check has
  committed, on the way to signing in, and its count is serialised by the
  guess record's row lock instead (ruling plan#3110): that is
  ``tests/test_routes/test_strangers_sign_in.py``.
* **R-CC122** ("Refusals first"): a request the form-token check or the
  app-wide rate limit refuses never takes the lock.  The registration ORDER is
  pinned by the exact hook list in ``tests/test_routes/test_auth_required.py``,
  the only arm that sees the rate-limit half (``TestConfig`` switches the
  limiter off); this module grades the form-token half's behaviour, with the
  control that the same request takes the owner's key once the check lets it
  through.
* **R-CC123**: a request's transaction -- and the lock riding it -- ends where
  the request ends, as production's app-context teardown ends it.  Graded on a
  correct password on an account that still owes its code, which commits
  nothing once it holds the key (its try's judgment commits before), so its
  transaction would otherwise outlive the request under the test client's
  shared app context.
* **The rule-5 approval of 2026-09-25** (the lock sits BEFORE the sign-in
  gate): a companion deactivated while their save waits for the owner's lock
  is turned away, because taking the lock expires the signed-in user's row and
  the gate reads ``is_active`` again after the wait.  The control is the same
  wait with the companion left active, which saves.
* **Ruling plan#4070**: a save whose sign-in a sign-out ends while it waits
  for the owner's lock is turned away, because the lock's hook asks for the
  sign-in's record again once the lock is held.  The control is the
  deactivation arm's.

The seed owner is made with two-factor (plan#3600), so its password step owes
the code; a backup-code case gives the owner's row known codes
(``tests._test_helpers.store_backup_codes``).
"""

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.user_write_lock import _USER_WRITE_LOCK_NAMESPACE
from app.utils.bearer_tokens import token_hash
from tests._guess_records import count_of
from tests._session_copies import token_of
from tests._sign_in import browser_count, make_known
from tests._test_helpers import (
    HeldOwnerKey,
    advisory_lock_keys,
    assert_timed_out_on_owner_key,
    bound_lock_waits,
    capture_sql_statements,
    generate_row_of,
    make_expense_template,
    owner_lock_key,
    store_backup_codes,
)
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD

#: How long a thread is given to reach the lock and wait on it, and to finish
#: once the holder lets go.  Generous: the arm polls and moves on the moment
#: the wait is visible, so the figure only bounds a failure.
_THREAD_DEADLINE_S = 20.0

#: The backup codes the MFA arms give the owner; any one of them signs in once.
_BACKUP_CODES = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]


def _first_index(statements, predicate, start=0):
    """The index of the first captured statement whose text satisfies *predicate*.

    Args:
        statements: The ``(statement, parameters)`` list from
            :func:`tests._test_helpers.capture_sql_statements`.
        predicate: A callable taking the statement text.
        start: The first index to consider.

    Returns:
        The index, or ``None`` when no statement at or after *start* matches.
    """
    return next(
        (
            i for i, (sql, _params) in enumerate(statements)
            if i >= start and predicate(sql)
        ),
        None,
    )


def _lock_index(statements):
    """The index of the first owner-lock acquisition in *statements*, or ``None``."""
    return _first_index(statements, lambda sql: "pg_advisory_xact_lock" in sql)


def _give_backup_codes(user_id):
    """Give *user_id*'s two-factor the known backup codes.

    Args:
        user_id: The user, who has two-factor.
    """
    store_backup_codes(db.session, user_id, _BACKUP_CODES)


def _post_login(client, password=SEED_USER_PASSWORD, email=SEED_USER_EMAIL):
    """POST the sign-in form as a browser does.

    Args:
        client: The test client.
        password: The password typed.
        email: The email typed.

    Returns:
        The response.
    """
    return client.post("/login", data={"email": email, "password": password})


class TestSignInLocksBeforeItChecks:
    """R-CC121: a known browser's or the code step's lock: after the look-up, before the check.

    And a companion's sign-in takes its owner's key.  A stranger's password
    step, the companion's here, takes it only after its check (ruling
    plan#3110), so that case grades the key and not the order.
    """

    def test_a_known_browser_s_wrong_password_locks_before_it_counts(
        self, client, seed_user,
    ):
        """The browser's count is read and written under the lock; the lookup before it.

        A known browser's wrong password is its password step's one WRITE (its
        record, ``auth.known_devices``), and "Lock, then check" puts the lock
        between the email lookup (which must run first: it is how the owner is
        found) and that write.  Every neighbour is asserted to exist, so the
        order cannot pass over a run that never looked the account up, never
        read the browser under the lock or never counted the attempt.
        """
        user_id = seed_user["user"].id
        make_known(client)

        response, statements = capture_sql_statements(
            lambda: _post_login(client, password="not-the-password"),
        )

        assert response.status_code == 200, "the refusal re-renders the form"
        lookup_at = _first_index(
            statements, lambda sql: "FROM auth.users" in sql,
        )
        lock_at = _lock_index(statements)
        assert None not in (lookup_at, lock_at), (lookup_at, lock_at)
        # The count is read under the lock: counting on a copy read before it
        # is the race the ruling closed (two wrong passwords counted once).
        read_at = _first_index(
            statements, lambda sql: "FROM auth.known_devices" in sql, start=lock_at,
        )
        count_at = _first_index(
            statements,
            lambda sql: sql.lstrip().startswith("UPDATE auth.known_devices"),
        )
        assert None not in (read_at, count_at), (read_at, count_at)
        assert lookup_at < lock_at < read_at < count_at
        assert advisory_lock_keys(statements)[0] == owner_lock_key(user_id)
        assert browser_count(user_id) == 1

    def test_a_backup_code_locks_before_it_reads_the_codes(self, client, seed_user):
        """The code step takes the lock before its first read of the codes.

        The password step leaves the sign-in pending on the second factor; the
        capture covers only the code step, so the lock it grades is the one
        ``mfa_verify`` takes, not the password step's.
        """
        user_id = seed_user["user"].id
        _give_backup_codes(user_id)
        pending = _post_login(client)
        assert urlsplit(pending.headers["Location"]).path == "/mfa/verify"

        response, statements = capture_sql_statements(
            lambda: client.post(
                "/mfa/verify", data={"backup_code": _BACKUP_CODES[0]},
            ),
        )

        assert response.status_code == 302
        assert urlsplit(response.headers["Location"]).path != "/login", (
            "the backup code did not sign in, so the order graded a refusal"
        )
        lock_at = _lock_index(statements)
        codes_at = _first_index(statements, lambda sql: "mfa_configs" in sql)
        assert None not in (lock_at, codes_at), (lock_at, codes_at)
        assert lock_at < codes_at
        assert advisory_lock_keys(statements)[0] == owner_lock_key(user_id)

    def test_a_companion_s_sign_in_locks_its_owner_s_key(
        self, client, seed_user, seed_companion,
    ):
        """A companion writes its owner's data, so it waits on its owner's key."""
        owner_id = seed_user["user"].id
        assert seed_companion["user"].id != owner_id

        response, statements = capture_sql_statements(
            lambda: _post_login(
                client, email="companion@shekel.local",
                password="companionpass",
            ),
        )

        assert response.status_code == 302
        assert advisory_lock_keys(statements)[0] == owner_lock_key(owner_id)


class TestAHeldKeyMakesSignInWait:
    """The order above is only worth grading if the lock really serialises."""

    def test_the_password_step_waits_on_a_held_key(self, client, seed_user):
        """Another transaction holding the owner's key blocks the sign-in."""
        with HeldOwnerKey(seed_user["user"].id):
            bound_lock_waits()
            with pytest.raises(OperationalError) as excinfo:
                _post_login(client)
        assert_timed_out_on_owner_key(excinfo.value, seed_user["user"].id)

    @pytest.mark.usefixtures("seed_user")
    def test_the_password_step_completes_when_the_key_is_free(self, client):
        """The control: the same sign-in under the same bound, nothing held."""
        bound_lock_waits()
        response = _post_login(client)
        assert response.status_code == 302

    def test_the_code_step_waits_on_a_held_key(self, client, seed_user):
        """The backup-code step blocks too: it takes the lock before the codes."""
        user_id = seed_user["user"].id
        _give_backup_codes(user_id)
        _post_login(client)
        with HeldOwnerKey(user_id):
            bound_lock_waits()
            with pytest.raises(OperationalError) as excinfo:
                client.post("/mfa/verify", data={"backup_code": _BACKUP_CODES[0]})
        assert_timed_out_on_owner_key(excinfo.value, user_id)

    def test_the_code_step_completes_when_the_key_is_free(self, client, seed_user):
        """The control for the code step."""
        _give_backup_codes(seed_user["user"].id)
        _post_login(client)
        bound_lock_waits()
        response = client.post(
            "/mfa/verify", data={"backup_code": _BACKUP_CODES[0]},
        )
        assert response.status_code == 302
        assert urlsplit(response.headers["Location"]).path != "/login"


class TestARefusedRequestTakesNoLock:
    """R-CC122: the form-token check answers before the lock is asked for."""

    @pytest.mark.usefixtures("seed_user")
    def test_a_request_without_a_form_token_takes_no_lock(
        self, app, auth_client, monkeypatch,
    ):
        """With the check on, a token-less save is refused and locks nothing.

        Flask-WTF reads ``WTF_CSRF_ENABLED`` per request, so switching it on
        for this test's live app is the production configuration for the one
        request that matters.
        """
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

        response, statements = capture_sql_statements(
            lambda: auth_client.post("/logout"),
        )

        assert response.status_code == 400
        taken = advisory_lock_keys(statements)
        assert not taken, taken

    def test_the_same_request_takes_the_owner_s_key_when_let_through(
        self, auth_client, seed_user,
    ):
        """The control: with the check off, the same save takes the owner's key.

        So the empty list above is the refusal's doing, not a request that
        never locks.
        """
        response, statements = capture_sql_statements(
            lambda: auth_client.post("/logout"),
        )

        assert response.status_code == 302
        assert advisory_lock_keys(statements)[0] == owner_lock_key(
            seed_user["user"].id,
        )


class TestTheLockEndsWithItsRequest:
    """R-CC123: the request's transaction ends where the request ends."""

    def test_a_sign_in_that_saves_nothing_leaves_the_key_free(self, client, seed_user):
        """After a correct password that saves nothing, another connection can take the key.

        A correct password commits its try's judgment and then takes the key
        (ruling plan#3110), and commits nothing after that while the account
        still owes its code, so the sign-in's transaction is still open when
        its route returns -- holding the owner's key.  A finished sign-in
        would grade this too: its browser's record commits (plan#2772), and
        then ``login_user`` reads the user that commit expired, which begins a
        new transaction whose first act is the owner's key, and the route
        returns holding it.  Measured 2026-11-10 with the teardown's rollback
        removed: after a finished sign-in another connection could not take
        the key, as after this one.  Production's app-context teardown ends
        it; under the test client the app context outlives the request, and
        the request teardown is what ends it.  The capture proves the sign-in
        took the key at all, so a free key afterwards is the teardown's doing.
        """
        user_id = seed_user["user"].id
        response, statements = capture_sql_statements(
            lambda: _post_login(client),
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/mfa/verify")
        assert owner_lock_key(user_id) in advisory_lock_keys(statements)

        probe = db.engine.connect()
        try:
            free = probe.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :owner)"),
                {"ns": _USER_WRITE_LOCK_NAMESPACE, "owner": user_id},
            ).scalar()
        finally:
            probe.rollback()
            probe.close()
        assert free is True, "the sign-in's transaction outlived its request"


def _wait_until_the_owner_s_key_is_awaited(owner_id, pending):
    """Block until a transaction in THIS database waits for *owner_id*'s lock.

    Read from ``pg_locks`` on a connection of its own: an advisory lock taken
    with two integer keys is listed with ``classid`` = the first, ``objid`` =
    the second and ``objsubid`` = 2, and an ungranted row is a waiter.
    **Scoped to this test's database**, because ``pg_locks`` lists the whole
    cluster, and every xdist worker's clone seeds its owner with the same id:
    unscoped, another worker's waiter satisfied the poll, the holder let go
    before this request had signed in, and the arm passed on the sign-in
    loader's refusal rather than the re-read it names (measured by the
    checkpoint review, 2026-09-25).

    Args:
        owner_id: The owner whose key is held.
        pending: The request's future; a request that finishes without
            waiting is reported with its own outcome at once.

    Raises:
        AssertionError: When the request finished without waiting, or no
            waiter appears within the deadline -- either way the arm would
            grade nothing.
    """
    deadline = time.monotonic() + _THREAD_DEADLINE_S
    with db.engine.connect() as watcher:
        while time.monotonic() < deadline:
            if pending.done():
                # ``result`` re-raises the thread's own exception, if any.
                raise AssertionError(
                    f"the request finished without waiting on the owner's "
                    f"lock: {pending.result()!r}"
                )
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                    " AND NOT granted AND classid = :ns AND objid = :owner"
                    " AND objsubid = 2 AND database = ("
                    "SELECT oid FROM pg_database"
                    " WHERE datname = current_database())"
                ),
                {"ns": _USER_WRITE_LOCK_NAMESPACE, "owner": owner_id},
            ).scalar()
            watcher.rollback()
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError("the request never waited on the owner's lock")


def _run_behind(held, app, request):
    """Run *request* on a thread while *held* keeps the key, then let it go.

    The request runs in an app context of its own, as production's does, and
    waits on the key *held* owns; once it is seen waiting, the holder
    COMMITS, which releases the key and publishes whatever the holder wrote at
    one instant -- so the request found its user before the change, and sees
    it only if something makes it read again.

    Args:
        held: The :class:`~tests._test_helpers.HeldOwnerKey` holding the owner's key.
        app: The application.
        request: A zero-argument callable issuing the request.

    Returns:
        The request's response.
    """
    def run():
        """The request, in an app context of its own."""
        with app.app_context():
            return request()

    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(run)
        try:
            _wait_until_the_owner_s_key_is_awaited(held.owner_id, pending)
            held.connection.commit()
        finally:
            # Released BEFORE the pool's exit joins the thread, which would
            # otherwise wait on this key for ever.  A no-op after the commit.
            held.connection.rollback()
        # ``result`` re-raises the thread's own exception, if any.
        return pending.result(timeout=_THREAD_DEADLINE_S)


class TestTheCheckReadsWhatTheLockProtects:
    """R-CC121's point, raced: a known browser's count is read after the lock, not before."""

    def test_a_count_raised_during_the_wait_is_counted_on(self, app, client, seed_user):
        """Another transaction raises the browser's count while the sign-in waits.

        The holder takes the owner's key and writes 3 on the known browser's
        record without committing; a wrong password from that browser then
        finds the account and the browser (no count committed), waits, and
        after the holder commits counts ON TOP of what it wrote.  Counting on
        a copy read before the lock writes 1 -- the "two wrong passwords
        counted once" race the ruling closed.  The account's count is a
        stranger's and stays 0.
        """
        user_id = seed_user["user"].id
        make_known(client)
        with HeldOwnerKey(user_id) as held:
            held.connection.execute(
                text(
                    "UPDATE auth.known_devices SET failed_login_count = 3 "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )
            response = _run_behind(
                held, app, lambda: _post_login(client, password="not-the-password"),
            )

        assert response.status_code == 200, "the refusal re-renders the form"
        assert browser_count(user_id) == 4
        assert count_of(SEED_USER_EMAIL) == 0


def _companion_visible_bill(seed_user, period):
    """A companion-visible Projected bill, and its id and status."""
    template = make_expense_template(
        db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent", companion_visible=True,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row.id, row.status_id


class TestADeactivatedCompanionIsTurnedAwayAfterTheWait:
    """The rule-5 approval: the lock before the gate re-reads ``is_active``."""

    def test_a_companion_deactivated_during_the_wait_is_turned_away(
        self, app, seed_user, seed_companion, seed_periods_today, companion_client,
    ):
        """The save waits, the owner deactivates the companion, the save is refused.

        The gate answers the sign-in redirect and the row stays Projected: the
        save never reached its view.
        """
        txn_id, before = _companion_visible_bill(
            seed_user, seed_periods_today[4],
        )
        assert before == ref_cache.status_id(StatusEnum.PROJECTED)

        with HeldOwnerKey(seed_user["user"].id) as held:
            held.connection.execute(
                text("UPDATE auth.users SET is_active = false WHERE id = :id"),
                {"id": seed_companion["user"].id},
            )
            response = _run_behind(
                held, app,
                lambda: companion_client.post(f"/transactions/{txn_id}/mark-done"),
            )

        assert response.status_code in (302, 303), response.status_code
        assert urlsplit(response.headers["Location"]).path == "/login"
        db.session.expire_all()
        assert db.session.get(Transaction, txn_id).status_id == before

    @pytest.mark.usefixtures("seed_companion")
    def test_the_same_wait_without_the_deactivation_saves(
        self, app, seed_user, seed_periods_today, companion_client,
    ):
        """The control: the same wait, the companion left active, the row is Paid.

        So the refusal above is the deactivation read after the wait, not
        something the wait itself does to the request.
        """
        txn_id, _before = _companion_visible_bill(
            seed_user, seed_periods_today[4],
        )

        with HeldOwnerKey(seed_user["user"].id) as held:
            response = _run_behind(
                held, app,
                lambda: companion_client.post(f"/transactions/{txn_id}/mark-done"),
            )

        assert response.status_code == 200, response.status_code
        db.session.expire_all()
        assert db.session.get(Transaction, txn_id).status_id == ref_cache.status_id(
            StatusEnum.DONE,
        )


class TestASaveWhoseSignInEndedInTheWaitIsTurnedAway:
    """Ruling plan#4070: once the lock is held, the save's sign-in is asked for again.

    ``load_user`` asks for the session's sign-in record before the lock, as it
    must, since the lock's key is the signed-in user's owner, and a sign-out
    can end the record while the save waits.  The control is the class
    above's: the same wait with nothing changed saves.
    """

    def test_a_save_whose_sign_in_ended_during_the_wait_is_turned_away(
        self, app, seed_user, seed_periods_today, companion_client,
    ):
        """The save waits, a sign-out deletes its sign-in's record, the save is refused.

        The holder deletes the companion's record, as ``/logout`` on a copy of
        her cookie would, and commits only once the save waits: so the save
        found the record before the lock (one that did not would finish
        without waiting, which :func:`_run_behind` reports).  The sign-in
        page answers and the row stays Projected: the save never reached its
        view.
        """
        txn_id, before = _companion_visible_bill(
            seed_user, seed_periods_today[4],
        )
        assert before == ref_cache.status_id(StatusEnum.PROJECTED)
        record = token_hash(token_of(companion_client))

        with HeldOwnerKey(seed_user["user"].id) as held:
            deleted = held.connection.execute(
                text("DELETE FROM auth.sign_ins WHERE token_hash = :hash"),
                {"hash": record},
            ).rowcount
            assert deleted == 1, "the companion's sign-in had no record to end"
            response = _run_behind(
                held, app,
                lambda: companion_client.post(f"/transactions/{txn_id}/mark-done"),
            )

        assert response.status_code in (302, 303), response.status_code
        assert urlsplit(response.headers["Location"]).path == "/login"
        db.session.expire_all()
        assert db.session.get(Transaction, txn_id).status_id == before
