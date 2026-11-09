"""A stranger's sign-in password: the same steps for every try, and no lock across its check.

Plan#2772, ruling **plan#3110**.  At ``/login`` a wrong password for a real
account used to write and commit its count while an email no account has wrote
nothing, and the account's owner lock was held through the password check: a
real email's reply was slower by the write, and two tries at once at it took
twice as long (measured on the real app; the figures are on plan#3117).  Every
stranger's try now runs the same steps -- the password check against the
account's hash or the dummy one, the guess record's one statement, a commit --
and takes the owner's lock only on the way to signing in, after the check,
confirming under it that the hash checked is still the account's.

* :class:`TestEveryRefusalSendsTheSameStatements` -- an email no account has,
  a wrong password, and a locked account's wrong and right passwords send one
  sequence of statements, the same text in the same order, with or without
  made-up device cookies named for the accounts (ruling plan#3130); and a
  browser known to one account is a stranger at another's email.
* :class:`TestNoRefusalTakesTheKey` -- with the owner's key held elsewhere,
  every refusal still answers, a made-up cookie for the account included; a
  known browser's waits, which is the control.
* :class:`TestARightPasswordLocksAfterItsCheck` -- the key is taken after the
  try's judgment commits, and the account is read again under it.
* :class:`TestAPasswordChangedDuringTheCheck` -- counts as a wrong guess and
  is refused; the same flow with the hash left alone signs in.
* :class:`TestABrowserMadeAStrangerMeanwhile` -- known when asked without the
  lock and gone under it, a browser signs in or counts as a stranger.
* :class:`TestTheRecordSerialisesTheCount` -- with no owner lock, a count
  raised while the try waits on the record's row is counted on.

What a reply COSTS is measured on the real app and never here: this suite's
cluster runs with fsync off, so a commit costs it nothing.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models.user import User
from app.services import auth_service, known_device_service
from app.services.guess_record_service import email_digest
from app.utils.digit_strings import MAX_INTEGER_COLUMN
from tests._guess_records import count_of, set_record
from tests._sign_in import browser_count, make_known
from tests._test_helpers import (
    HeldOwnerKey,
    advisory_lock_keys,
    assert_timed_out_on_owner_key,
    bound_lock_waits,
    owner_lock_key,
)
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_WRONG = "not-the-password"
_NO_ACCOUNT = "nobody@shekel.local"
_SECOND_EMAIL = "second@shekel.local"
_SECOND_PASSWORD = "secondpass12"
_REFUSED = b"Invalid email or password."

#: Where :func:`_capture` marks a commit among the statements.
_COMMIT = "COMMIT"

#: How long a thread is given to reach a lock and wait on it, and to finish
#: once the holder lets go.  Generous: the arm polls and moves on the moment
#: the wait is visible, so the figure only bounds a failure.
_THREAD_DEADLINE_S = 20.0


def _post_login(client, email=SEED_USER_EMAIL, password=SEED_USER_PASSWORD):
    """POST the sign-in form as a browser does; return the response."""
    return client.post("/login", data={"email": email, "password": password})


def _refused(response):
    """Return whether *response* is ``/login``'s refusal: the form again, with its words."""
    return response.status_code == 200 and _REFUSED in response.data


def _signed_in(response):
    """Return whether *response* is a finished sign-in's redirect."""
    return (
        response.status_code == 302
        and urlsplit(response.headers["Location"]).path not in ("/login", "/mfa/verify")
    )


def _capture(fn):
    """Run *fn*, recording each statement and each commit the engine sends, in order.

    :func:`tests._test_helpers.capture_sql_statements` records statements
    alone, and this module's claims are about where the COMMIT falls among
    them (the judgment's commit before the owner's key), which a commit
    counted apart from the statements cannot place.  It records them in the
    same ``(statement, parameters)`` shape, so
    :func:`~tests._test_helpers.advisory_lock_keys` reads it as it reads that.

    Args:
        fn: A zero-argument callable.

    Returns:
        ``(result, events)``: *fn*'s result, and ``(statement, parameters)``
        pairs in the order sent, a commit as ``(_COMMIT, None)``.
    """
    events = []

    def on_execute(_conn, _cursor, statement, parameters, *_args, **_kwargs):
        events.append((statement, parameters))

    def on_commit(_conn):
        events.append((_COMMIT, None))

    event.listen(db.engine, "before_cursor_execute", on_execute)
    event.listen(db.engine, "commit", on_commit)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on_execute)
        event.remove(db.engine, "commit", on_commit)
    return result, events


def _index(events, predicate, start=0):
    """The index of the first event from *start* whose text satisfies *predicate*, or ``None``."""
    return next(
        (i for i, (sql, _params) in enumerate(events) if i >= start and predicate(sql)),
        None,
    )


def _lock_the_second_user():
    """Open a ten-minute lock on the second user's email, committed."""
    now = datetime.now(timezone.utc)
    set_record(
        _SECOND_EMAIL, count=0, locked_until=now + timedelta(minutes=10),
        last_attempt_at=now,
    )
    db.session.commit()


def _send_made_up_cookies(client, user_ids):
    """Give the browser a device cookie named for each of *user_ids*, naming no record."""
    for user_id in user_ids:
        client.set_cookie(f"shekel_device_{user_id}", "made-up-token")


class TestEveryRefusalSendsTheSameStatements:
    """What a stranger is refused for cannot be read off what the refusal sends."""

    @pytest.mark.parametrize("cookies", ["none", "made-up", "past-the-column"])
    def test_four_refusals_send_one_sequence(
        self, client, seed_user, seed_second_user, cookies,
    ):
        """No account, a wrong password, and a locked account's wrong and right one.

        Each sends the account's lookup, the guess record's one statement
        (an insert that judges the email's record where it has one), the
        clean-up's read and delete, and one commit: the same text in the same
        order, whichever it was.  With made-up cookies named for both
        accounts, each first asks once which users the browser is known to,
        whatever email was typed (ruling plan#3130); before, only a real
        email's try asked, of its own cookie.  A cookie named for an id no
        ``id`` column holds is no device cookie, so it asks nothing and
        raises nothing.  The counts show each was judged: 1 on each email
        tried once with a wrong password, 0 on the locked one, whose tries
        the lock refused.
        """
        _lock_the_second_user()
        _send_made_up_cookies(client, {
            "none": [],
            "made-up": [seed_user["user"].id, seed_second_user["user"].id],
            "past-the-column": [MAX_INTEGER_COLUMN + 1],
        }[cookies])
        tries = {
            "no account": (_NO_ACCOUNT, _WRONG),
            "a wrong password": (SEED_USER_EMAIL, _WRONG),
            "locked, a wrong password": (_SECOND_EMAIL, _WRONG),
            "locked, the right password": (_SECOND_EMAIL, _SECOND_PASSWORD),
        }

        sent = {}
        for name, (email, password) in tries.items():
            response, events = _capture(
                lambda e=email, p=password: _post_login(client, e, p),
            )
            assert _refused(response), name
            sent[name] = [sql for sql, _params in events]

        for name, statements in sent.items():
            assert statements == sent["no account"], name
        baseline = list(sent["no account"])
        if cookies == "made-up":
            assert "FROM auth.known_devices" in baseline.pop(0)
        assert not any("known_devices" in sql for sql in baseline)
        assert len(baseline) == 5, baseline
        assert "FROM auth.users" in baseline[0]
        assert baseline[1].lstrip().startswith("INSERT INTO auth.guess_records")
        assert "ON CONFLICT (email_digest) DO UPDATE" in baseline[1]
        assert baseline[2].lstrip().startswith("SELECT auth.users.email")
        assert baseline[3].lstrip().startswith("DELETE FROM auth.guess_records")
        assert baseline[4] == _COMMIT
        assert not any("pg_advisory" in sql for sql in baseline)

        assert count_of(_NO_ACCOUNT) == 1
        assert count_of(SEED_USER_EMAIL) == 1
        assert count_of(_SECOND_EMAIL) == 0

    @pytest.mark.usefixtures("seed_second_user")
    def test_a_browser_known_to_one_account_is_a_stranger_at_another(
        self, client, seed_user,
    ):
        """Known to the seed user, the browser types the second user's email and a wrong password.

        The question finds the seed user alone, so the try is a stranger's:
        the statements an email no account has sends from the same browser,
        the question first, no owner lock, and 1 counted on the second
        user's email.
        """
        make_known(client)
        _response, unknown = _capture(lambda: _post_login(client, _NO_ACCOUNT, _WRONG))
        response, other = _capture(lambda: _post_login(client, _SECOND_EMAIL, _WRONG))

        assert _refused(response)
        statements = [sql for sql, _params in other]
        assert statements == [sql for sql, _params in unknown]
        assert "FROM auth.known_devices" in statements[0]
        assert not any("pg_advisory" in sql for sql in statements)
        assert count_of(_SECOND_EMAIL) == 1
        assert count_of(SEED_USER_EMAIL) == 0
        assert browser_count(seed_user["user"].id) == 0


class TestNoRefusalTakesTheKey:
    """A stranger's refusal never waits on the account's owner lock."""

    @pytest.mark.parametrize(
        "password, with_cookie",
        [
            pytest.param(_WRONG, False, id="a-wrong-password"),
            pytest.param(_WRONG, True, id="a-made-up-cookie"),
        ],
    )
    def test_it_answers_while_the_key_is_held(
        self, client, seed_user, password, with_cookie,
    ):
        """The owner's key held elsewhere, lock waits bounded: the refusal still answers.

        A cookie named for the account but naming no record of it is looked
        up with no lock held, so it does not make the stranger take the key.
        """
        user_id = seed_user["user"].id
        if with_cookie:
            client.set_cookie(f"shekel_device_{user_id}", "made-up-token")
        with HeldOwnerKey(user_id):
            bound_lock_waits()
            response = _post_login(client, password=password)

        assert _refused(response)
        assert count_of(SEED_USER_EMAIL) == 1

    @pytest.mark.usefixtures("seed_user")
    def test_a_locked_account_answers_while_the_key_is_held(
        self, client, seed_second_user,
    ):
        """The same for a locked account's right password: refused, without the key."""
        _lock_the_second_user()
        with HeldOwnerKey(seed_second_user["user"].id):
            bound_lock_waits()
            response = _post_login(client, _SECOND_EMAIL, _SECOND_PASSWORD)

        assert _refused(response)

    def test_a_known_browser_waits_on_the_held_key(self, client, seed_user):
        """The control: a browser known to the account checks under the key, so it waits."""
        user_id = seed_user["user"].id
        make_known(client)
        with HeldOwnerKey(user_id):
            bound_lock_waits()
            with pytest.raises(OperationalError) as excinfo:
                _post_login(client, password=_WRONG)
        assert_timed_out_on_owner_key(excinfo.value, user_id)


class TestARightPasswordLocksAfterItsCheck:
    """On the way to signing in, the key is taken after the judgment's commit."""

    def test_the_key_follows_the_commit_and_the_account_is_read_under_it(
        self, client, seed_user,
    ):
        """Judgment, commit, the owner's key, then the account read again."""
        user_id = seed_user["user"].id

        response, events = _capture(lambda: _post_login(client))

        assert _signed_in(response)
        judged_at = _index(
            events, lambda sql: sql.lstrip().startswith("INSERT INTO auth.guess_records"),
        )
        assert judged_at is not None
        committed_at = _index(events, lambda sql: sql == _COMMIT, start=judged_at)
        lock_at = _index(events, lambda sql: "pg_advisory_xact_lock" in sql)
        assert None not in (committed_at, lock_at), (committed_at, lock_at)
        reread_at = _index(events, lambda sql: "FROM auth.users" in sql, start=lock_at)
        assert reread_at is not None
        assert judged_at < committed_at < lock_at < reread_at
        assert advisory_lock_keys(events)[0] == owner_lock_key(user_id)


class TestAPasswordChangedDuringTheCheck:
    """Under the lock the sign-in confirms the hash it checked is still the account's."""

    @pytest.mark.parametrize(
        "changed", [True, False], ids=["changed", "left-alone"],
    )
    def test_a_changed_hash_counts_as_a_wrong_guess(
        self, client, seed_user, monkeypatch, changed,
    ):
        """The account's hash is rewritten and committed while bcrypt checks the old one.

        Changed: refused, 1 counted on the email, and the new hash stands.
        Left alone (the same hash written back): the right password signs in.
        """
        user_id = seed_user["user"].id
        old_hash = db.session.get(User, user_id).password_hash
        written = auth_service.hash_password("a-new-password-12") if changed else old_hash
        real_check = auth_service.verify_password

        def check_then_write(password, password_hash):
            """The real check, then another connection writes the account's hash."""
            result = real_check(password, password_hash)
            with db.engine.connect() as other:
                other.execute(
                    text("UPDATE auth.users SET password_hash = :hash WHERE id = :id"),
                    {"hash": written, "id": user_id},
                )
                other.commit()
            return result

        monkeypatch.setattr(auth_service, "verify_password", check_then_write)
        response = _post_login(client)

        db.session.expire_all()
        assert db.session.get(User, user_id).password_hash == written
        if changed:
            assert _refused(response)
            assert count_of(SEED_USER_EMAIL) == 1
        else:
            assert _signed_in(response)
            assert count_of(SEED_USER_EMAIL) == 0


class TestABrowserMadeAStrangerMeanwhile:
    """Known when asked without the lock, gone once the lock is held: it goes on as a stranger."""

    @pytest.mark.parametrize("right", [False, True], ids=["wrong", "right"])
    def test_it_signs_in_or_counts_as_a_stranger(
        self, client, seed_user, monkeypatch, right,
    ):
        """The look-up says known; under the lock no record answers the cookie.

        The browser's own tenth wrong guess, in another request, can delete
        its record between the two reads.  Simulated by a look-up that
        answers known for a cookie naming no record.  A wrong password is
        refused and counts on the email's record, as a stranger's does; the
        right one signs in, and the finished sign-in clears that record.
        """
        user_id = seed_user["user"].id
        client.set_cookie(f"shekel_device_{user_id}", "a-record-gone-since")
        monkeypatch.setattr(
            known_device_service, "known_to", lambda _tokens: frozenset({user_id}),
        )

        response = _post_login(client, password=SEED_USER_PASSWORD if right else _WRONG)

        if right:
            assert _signed_in(response)
            assert count_of(SEED_USER_EMAIL) == 0
        else:
            assert _refused(response)
            assert count_of(SEED_USER_EMAIL) == 1


def _wait_until_a_statement_waits_on_a_lock(pending):
    """Block until a statement in THIS database waits on a lock.

    Read from ``pg_stat_activity`` on a connection of its own, scoped to this
    test's database: every xdist worker's clone is a database of its own.

    Args:
        pending: The request's future; one that finishes without waiting is
            reported with its own outcome at once.

    Raises:
        AssertionError: When the request finished without waiting, or no
            wait appears within the deadline -- either way the arm would
            grade nothing.
    """
    deadline = time.monotonic() + _THREAD_DEADLINE_S
    with db.engine.connect() as watcher:
        while time.monotonic() < deadline:
            if pending.done():
                raise AssertionError(
                    f"the request finished without waiting: {pending.result()!r}"
                )
            waiting = watcher.execute(text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database()"
                " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )).scalar()
            watcher.rollback()
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError("the request never waited on a lock")


class TestTheRecordSerialisesTheCount:
    """With no owner lock, the record's own row lock keeps two counts from counting once."""

    @pytest.mark.usefixtures("seed_user")
    def test_a_count_raised_during_the_wait_is_counted_on(self, app, client):
        """Another transaction writes 3 on the email's record and holds it; the try counts 4.

        The holder inserts the record without committing, so the try's one
        statement waits on it; once the holder commits, the try judges the
        record it left and counts on top.  Counting on what the try saw
        before the wait would write 1.
        """
        holder = db.engine.connect()
        try:
            holder.execute(
                text(
                    "INSERT INTO auth.guess_records "
                    "(email_digest, failed_login_count, last_attempt_at) "
                    "VALUES (:digest, 3, now())"
                ),
                {"digest": email_digest(SEED_USER_EMAIL)},
            )

            def run():
                """The request, in an app context of its own, as production's is."""
                with app.app_context():
                    return _post_login(client, password=_WRONG)

            with ThreadPoolExecutor(max_workers=1) as worker:
                pending = worker.submit(run)
                try:
                    _wait_until_a_statement_waits_on_a_lock(pending)
                    holder.commit()
                finally:
                    # Released BEFORE the pool's exit joins the thread, which
                    # would otherwise wait on the row for ever.  A no-op after
                    # the commit.
                    holder.rollback()
                response = pending.result(timeout=_THREAD_DEADLINE_S)
        finally:
            holder.close()

        assert _refused(response)
        assert count_of(SEED_USER_EMAIL) == 4
