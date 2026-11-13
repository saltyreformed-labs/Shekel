"""Shekel Budget App -- Unit tests for app.utils.session_helpers.

Locks down two cooperating contracts documented in
``app/utils/session_helpers.py``:

1. Every auth-factor state change that needs to invalidate parallel
   sessions does so by stamping ``users.session_invalidated_at`` and
   refreshing ``flask.session[SESSION_CREATED_AT_KEY]`` with the SAME
   timestamp.  The strict less-than comparison in :func:`app.load_user`
   then rejects every other live session for that user while letting
   the request that issued the helper survive (commit C-08 -- F-002,
   F-003, F-032).

2. The three ``stamp_*`` helpers (``stamp_login_session``,
   ``stamp_reauth_session``, ``stamp_session_refresh``) write the
   right SUBSET of lifecycle keys for each semantic event so the
   loader, the idle-timeout check, and the fresh-login check all
   stay coherent (commit C-10 -- F-006, F-035, F-045).  A regression
   that wrote ``_session_created_at`` on /reauth would silently let
   an attacker bypass a future ``invalidate_other_sessions`` bump;
   a regression that wrote ``_fresh_login_at`` on /invalidate-sessions
   would let the same UI extend the step-up window without typing a
   password.

These tests exercise the helpers in isolation against a real DB and
a real Flask test request context -- no mocks of ``db.session`` or
``flask.session``, because correctness depends on the exact
serialization round-trip of those two pieces of state.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from app.extensions import db
from app.models.sign_in import SignIn
from app.models.user import User
from app.services import sign_in_service
from app.utils.log_events import AUTH
from app.utils.session_helpers import (
    FRESH_LOGIN_AT_KEY,
    SESSION_CREATED_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_TOKEN_KEY,
    SESSION_USER_ID_KEY,
    end_this_sign_in,
    invalidate_other_sessions,
    session_stamp,
    session_token,
    sign_in_created_at,
    sign_in_is_live,
    stamp_login_session,
    stamp_password_change,
    stamp_reauth_session,
    stamp_session_refresh,
)

#: A user id for the stamping tests that load no user: the stamp is written
#: whatever the id, and only the loader tests below look one up.
_STAMPED_USER_ID = 7

#: A sign-in token for the stamping tests that read no record: the cookie
#: carries whatever token it is handed, and only the tests that ask whether a
#: sign-in is live record one.
_A_TOKEN = "a-sign-in-token"


class _LogCapture:
    """Capture log records emitted on a specific logger.

    Mirrors ``tests/test_utils/test_log_events._LogCapture`` -- kept
    duplicated rather than imported because the two files exercise
    distinct contracts and a shared helper would create an import-
    order dependency between two unrelated test modules.
    """

    def __init__(self, logger):
        """Build a handler that keeps every record *logger* emits, unattached."""
        self._logger = logger
        self.records = []
        self._handler = logging.Handler()
        self._handler.emit = self.records.append

    def __enter__(self):
        """Attach the handler and open the logger to every level."""
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        """Detach the handler, so no record outside the block is kept."""
        self._logger.removeHandler(self._handler)


class TestInvalidateOtherSessionsHappyPath:
    """The helper's documented behaviour on a normal authenticated call."""

    def test_bumps_session_invalidated_at_on_user_row(
        self, app, db, seed_user
    ):
        """``user.session_invalidated_at`` is set to a recent UTC datetime.

        Reload the row from the DB after the call to verify the
        write was committed (not just held in the session unit-of-
        work).  Bound the timestamp between ``before`` and ``after``
        wall-clock samples taken around the helper invocation so a
        regression that wrote a stale or wildly-future value would
        be caught by the bounded-recency assertion.
        """
        user = seed_user["user"]
        assert user.session_invalidated_at is None, (
            "Setup error: seed_user must start with no prior "
            "invalidation timestamp."
        )

        with app.test_request_context("/"):
            before = datetime.now(timezone.utc)
            invalidate_other_sessions(user, "test_reason")
            after = datetime.now(timezone.utc)

        # Reload from DB to assert the write was committed.
        reloaded = db.session.get(User, user.id)
        assert reloaded.session_invalidated_at is not None
        assert before <= reloaded.session_invalidated_at <= after

    def test_refreshes_session_created_at_to_match_invalidation(
        self, app, db, seed_user
    ):
        """The cookie's ``_session_created_at`` equals the DB column.

        The :func:`app.load_user` comparison is strict less-than
        (``cookie < db`` => reject).  Equality is the boundary case
        that lets the current session survive -- a regression that
        used two different ``now`` calls would push the cookie
        microseconds ahead and the test would still pass with == OR
        cookie > db.  Asserting equality (not >=) locks down the
        single-``now`` invariant the helper documents.
        """
        user = seed_user["user"]

        with app.test_request_context("/") as ctx:
            invalidate_other_sessions(user, "single_now_check")
            cookie_value = ctx.session.get(SESSION_CREATED_AT_KEY)

        # Reload from DB.
        reloaded = db.session.get(User, user.id)
        assert cookie_value is not None, (
            "Helper must write SESSION_CREATED_AT_KEY into the session."
        )
        cookie_dt = datetime.fromisoformat(cookie_value)
        assert cookie_dt == reloaded.session_invalidated_at, (
            f"Cookie ({cookie_dt!r}) must equal DB column "
            f"({reloaded.session_invalidated_at!r}); the helper must "
            "use a single now() value for both writes."
        )

    def test_session_created_at_is_iso_8601_string(
        self, app, seed_user,
    ):
        """The cookie value parses with ``datetime.fromisoformat``.

        ``app.load_user`` reads the cookie value through
        ``session_stamp``, which reads a non-ISO or naive value as no
        stamp at all: had the helper written one, the session that
        asked for the invalidation would be signed out by it.
        """
        user = seed_user["user"]
        with app.test_request_context("/") as ctx:
            invalidate_other_sessions(user, "iso_format_check")
            cookie_value = ctx.session.get(SESSION_CREATED_AT_KEY)

        # Must be a string (not a datetime) and must round-trip.
        assert isinstance(cookie_value, str)
        parsed = datetime.fromisoformat(cookie_value)
        # Must be timezone-aware so the load_user comparison works.
        assert parsed.tzinfo is not None, (
            "The cookie value must be timezone-aware ISO 8601; "
            "session_stamp reads a naive one as no stamp, and load_user "
            "signs the session out."
        )

    def test_emits_other_sessions_invalidated_event_with_reason(
        self, app, seed_user,
    ):
        """A structured ``other_sessions_invalidated`` event is logged.

        Locks down the audit-trail contract: every call must emit
        exactly one event with the supplied ``reason`` and the
        ``user_id`` of the user being invalidated.  Without this
        check, a future refactor that swallowed the log call would
        leave operators blind to credential-change activity.
        """
        user = seed_user["user"]
        with app.test_request_context("/"):
            with _LogCapture(
                logging.getLogger("app.utils.session_helpers")
            ) as cap:
                invalidate_other_sessions(user, "password_change")

        matching = [
            r for r in cap.records
            if getattr(r, "event", None) == "other_sessions_invalidated"
        ]
        assert len(matching) == 1, (
            "Helper must emit exactly one other_sessions_invalidated "
            f"event per call; got {len(matching)}."
        )
        record = matching[0]
        assert record.category == AUTH
        assert record.reason == "password_change"
        assert record.user_id == user.id
        assert record.levelno == logging.INFO

    def test_idempotent_under_repeated_call(self, app, db, seed_user):
        """Calling the helper twice in a row leaves the user in a
        consistent post-invalidation state.

        Verifies that a second call does not corrupt the timestamp,
        does not raise, and leaves the cookie matching the latest DB
        value.  Important because future call sites might call the
        helper from both the route and a hook that fires for the same
        state change -- a non-idempotent helper would split the two
        timestamps and lock the user out of their current session.
        """
        user = seed_user["user"]
        with app.test_request_context("/") as ctx:
            invalidate_other_sessions(user, "first_call")
            first_db = db.session.get(User, user.id).session_invalidated_at
            first_cookie = ctx.session.get(SESSION_CREATED_AT_KEY)

            invalidate_other_sessions(user, "second_call")
            second_db = db.session.get(User, user.id).session_invalidated_at
            second_cookie = ctx.session.get(SESSION_CREATED_AT_KEY)

        # Second call's timestamps must be >= first (monotonic).
        assert second_db >= first_db
        assert datetime.fromisoformat(second_cookie) >= datetime.fromisoformat(first_cookie)
        # Cookie still matches DB after the second call.
        assert datetime.fromisoformat(second_cookie) == second_db


class TestInvalidateOtherSessionsValidation:
    """Defensive validation on the helper's inputs."""

    def test_rejects_none_user(self, app):
        """Passing ``user=None`` raises before any state mutation.

        A None user would silently no-op or raise an obscure
        AttributeError mid-helper.  The early-validation contract
        guarantees the caller's session state is unchanged on misuse.
        """
        with app.test_request_context("/") as ctx:
            with pytest.raises(ValueError, match="None"):
                invalidate_other_sessions(None, "any_reason")
            # Cookie was not touched.
            assert SESSION_CREATED_AT_KEY not in ctx.session

    def test_rejects_empty_reason(self, app, db, seed_user):
        """Empty/None reason raises before any state mutation.

        The audit log relies on ``reason`` to distinguish password-
        change events from MFA-disable events from backup-code
        consumption events.  An empty value defeats the audit trail
        the helper exists to produce.  Verify both the user row and
        the cookie are untouched on misuse.
        """
        user = seed_user["user"]
        with app.test_request_context("/") as ctx:
            with pytest.raises(ValueError, match="reason"):
                invalidate_other_sessions(user, "")
            assert SESSION_CREATED_AT_KEY not in ctx.session

        # User row still has its initial session_invalidated_at value.
        reloaded = db.session.get(User, user.id)
        assert reloaded.session_invalidated_at is None

    def test_rejects_none_reason(self, app, seed_user):
        """``reason=None`` is also rejected (not just empty string).

        Separate from the empty-string test because the implementation
        could conceivably check truthiness for one and not the other.
        Both must fail.
        """
        user = seed_user["user"]
        with app.test_request_context("/"):
            with pytest.raises(ValueError, match="reason"):
                invalidate_other_sessions(user, None)


class TestInvalidateOtherSessionsLoadUserIntegration:
    """The helper's writes correctly drive ``app.load_user``.

    The helper's contract only matters if ``load_user`` actually
    accepts the current cookie and rejects older ones.  These tests
    exercise the integration without going through a full route, so
    a regression in either the helper or the loader is caught here.
    """

    def test_current_cookie_value_passes_load_user_after_call(
        self, app, seed_user,
    ):
        """Cookie ``_session_created_at`` written by the helper makes
        ``load_user`` return the user (current session survives).

        Drives the loader directly via ``login_manager._user_callback``
        with the cookie value the helper just wrote, so the test grades
        the loader's own answer for that cookie.

        The session is signed in first (its record, then
        ``stamp_login_session``), as its one caller's is: ``/mfa/verify``
        signs it in just before its backup-code call.  Since plan#2173 the loader refuses a session with no
        activity stamp, and the developer confirmed that change on
        2026-10-30, so a bare request context no longer stands in for one.
        """
        # Pylint: ``import-outside-toplevel``, ``protected-access`` -- the
        # local import keeps the LoginManager dependency next to the
        # test that needs it, and the loader callback is driven directly
        # rather than through current_user, so the answer graded is the
        # loader's alone.
        # pylint: disable=import-outside-toplevel,protected-access
        from app.extensions import login_manager

        user = seed_user["user"]

        with app.test_request_context("/"):
            now = datetime.now(timezone.utc)
            token = sign_in_service.record_sign_in(user.id, None, now=now)
            stamp_login_session(now, user.id, token)
            invalidate_other_sessions(user, "current_passes_check")
            # The cookie the helper just wrote is still on the
            # request context's session.  Drive the loader.
            loaded = login_manager._user_callback(str(user.id))

            assert loaded is not None, (
                "Current session must survive the helper's invalidation "
                "bump; load_user returned None instead."
            )
            assert loaded.id == user.id

    def test_older_cookie_value_fails_load_user_after_call(
        self, app, seed_user,
    ):
        """A cookie with a pre-call ``_session_created_at`` is rejected.

        Sets the cookie's timestamp to one hour BEFORE the helper
        runs, then invokes the helper, then drives the loader.  The
        loader must return None for the stale cookie -- the entire
        point of the helper.

        The session is signed in first (its record, then
        ``stamp_login_session``) so the stale ``_session_created_at`` is the
        ONLY thing the loader can refuse it for: since plan#2173 a session
        with no activity stamp is refused too, and since ruling plan#3925 one
        whose token names no record, and without the sign-in this test would
        pass on either instead, measuring nothing about the comparison it
        exists for.
        """
        # Pylint: ``import-outside-toplevel``, ``protected-access`` -- as in
        # test_current_cookie_value_passes_load_user_after_call above: the
        # import stays local and the loader callback is driven directly.
        # pylint: disable=import-outside-toplevel,protected-access
        from app.extensions import login_manager

        user = seed_user["user"]

        with app.test_request_context("/") as ctx:
            now = datetime.now(timezone.utc)
            token = sign_in_service.record_sign_in(user.id, None, now=now)
            stamp_login_session(now, user.id, token)
            stale_value = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()
            ctx.session[SESSION_CREATED_AT_KEY] = stale_value

            invalidate_other_sessions(user, "older_fails_check")

            # Replace the helper-written cookie with the stale value
            # before calling load_user, simulating a parallel session
            # that has not yet refreshed its cookie.
            ctx.session[SESSION_CREATED_AT_KEY] = stale_value

            loaded = login_manager._user_callback(str(user.id))
            assert loaded is None, (
                "A session whose _session_created_at predates the "
                "invalidation bump must be rejected by load_user; "
                "got a non-None user instead."
            )


class TestStampLoginSession:
    """``stamp_login_session`` writes ALL THREE lifecycle keys."""

    def test_writes_all_three_keys_with_identical_value(self, app):
        """Every key carries the same ISO-8601 string from the
        single ``now`` argument.

        Reusing one value across the three writes is required so a
        later audit comparison cannot falsely flag the keys as
        clock-skewed.  This test is the one enforcement of that
        contract: no integration test compares the three keys, and
        ``tests/test_adversarial/test_step_up/test_fresh_login_and_reauth.py``
        checks only that /login stamps ``_fresh_login_at``.
        """
        with app.test_request_context("/") as ctx:
            now = datetime.now(timezone.utc)
            stamp_login_session(now, _STAMPED_USER_ID, _A_TOKEN)

            iso = now.isoformat()
            assert ctx.session[SESSION_CREATED_AT_KEY] == iso
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso

    def test_writes_the_user_as_flask_login_spells_the_id(self, app):
        """The user stamp is the id's string, as ``load_user`` is handed it."""
        with app.test_request_context("/") as ctx:
            stamp_login_session(datetime.now(timezone.utc), _STAMPED_USER_ID, _A_TOKEN)

            assert ctx.session[SESSION_USER_ID_KEY] == str(_STAMPED_USER_ID)

    def test_overwrites_existing_values(self, app):
        """Subsequent calls overwrite, never append.

        A regression that mutated rather than overwrote could leak
        an older timestamp into a freshly-stamped session, and the
        idle / fresh checks would compare against the leaked value.
        """
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_CREATED_AT_KEY] = "stale-1"
            ctx.session[SESSION_LAST_ACTIVITY_KEY] = "stale-2"
            ctx.session[FRESH_LOGIN_AT_KEY] = "stale-3"
            ctx.session[SESSION_USER_ID_KEY] = "stale-4"

            now = datetime.now(timezone.utc)
            stamp_login_session(now, _STAMPED_USER_ID, _A_TOKEN)

            iso = now.isoformat()
            assert ctx.session[SESSION_CREATED_AT_KEY] == iso
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso
            assert ctx.session[SESSION_USER_ID_KEY] == str(_STAMPED_USER_ID)


class TestSignInCreatedAt:
    """``sign_in_created_at`` answers only for the user the sign-in stamped."""

    def test_the_stamped_user_gets_the_creation_stamp(self, app):
        """Asked for the user the sign-in named: its ``_session_created_at``."""
        with app.test_request_context("/"):
            now = datetime.now(timezone.utc)
            stamp_login_session(now, _STAMPED_USER_ID, _A_TOKEN)

            assert sign_in_created_at(str(_STAMPED_USER_ID)) == now

    def test_another_user_gets_nothing(self, app):
        """Asked for any other user, as a remember-cookie restore asks: ``None``."""
        with app.test_request_context("/"):
            stamp_login_session(datetime.now(timezone.utc), _STAMPED_USER_ID, _A_TOKEN)

            assert sign_in_created_at(str(_STAMPED_USER_ID + 1)) is None

    def test_timestamps_without_a_user_stamp_get_nothing(self, app):
        """A session with the timestamps but no user stamp: ``None``.

        The shape of every session signed in before the user stamp existed,
        so each is signed out once by the deploy that adds it.
        """
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_CREATED_AT_KEY] = (
                datetime.now(timezone.utc).isoformat()
            )

            assert sign_in_created_at(str(_STAMPED_USER_ID)) is None


class TestStampReauthSession:
    """``stamp_reauth_session`` updates activity + fresh; preserves created."""

    def test_updates_activity_and_fresh_only(self, app):
        """``_session_created_at`` is left untouched.

        Critical invariant: writing ``_session_created_at`` on
        /reauth would silently promote the session past every prior
        ``invalidate_other_sessions`` bump.  An attacker with a
        hijacked cookie could then use /reauth to defeat the user's
        later "log out all sessions" click -- a security-relevant
        regression.
        """
        with app.test_request_context("/") as ctx:
            original_created = "2026-01-01T00:00:00+00:00"
            ctx.session[SESSION_CREATED_AT_KEY] = original_created

            now = datetime.now(timezone.utc)
            stamp_reauth_session(now)

            iso = now.isoformat()
            # Created is preserved.
            assert ctx.session[SESSION_CREATED_AT_KEY] == original_created
            # Activity and fresh are advanced.
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso

    def test_does_not_create_session_created_at_key(self, app):
        """The helper never CREATES ``_session_created_at`` either.

        Belt-and-suspenders: a session that somehow reached /reauth
        without ``_session_created_at`` (e.g. a pre-C-08 cookie)
        should remain in that state.  Adding the key here would
        accidentally upgrade the cookie past prior invalidations.
        """
        with app.test_request_context("/") as ctx:
            assert SESSION_CREATED_AT_KEY not in ctx.session

            stamp_reauth_session(datetime.now(timezone.utc))

            assert SESSION_CREATED_AT_KEY not in ctx.session


class TestStampSessionRefresh:
    """``stamp_session_refresh`` updates created + activity; preserves fresh."""

    def test_updates_created_and_activity_only(self, app):
        """``_fresh_login_at`` is left untouched.

        Critical invariant: writing ``_fresh_login_at`` on
        /invalidate-sessions would let the same UI silently extend
        the step-up window without typing a password -- a security-
        relevant regression of F-045.
        """
        with app.test_request_context("/") as ctx:
            original_fresh = "2026-01-01T00:00:00+00:00"
            ctx.session[FRESH_LOGIN_AT_KEY] = original_fresh

            now = datetime.now(timezone.utc)
            stamp_session_refresh(now)

            iso = now.isoformat()
            # Created and activity are advanced.
            assert ctx.session[SESSION_CREATED_AT_KEY] == iso
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            # Fresh is preserved.
            assert ctx.session[FRESH_LOGIN_AT_KEY] == original_fresh

    def test_does_not_create_fresh_login_at_key(self, app):
        """The helper never CREATES ``_fresh_login_at`` either.

        A session that lacks ``_fresh_login_at`` (pre-C-10 cookie or
        a session that has never required a fresh re-auth) must stay
        in that state -- creating the key would let
        /invalidate-sessions silently bypass the very next
        ``fresh_login_required`` gate.
        """
        with app.test_request_context("/") as ctx:
            assert FRESH_LOGIN_AT_KEY not in ctx.session

            stamp_session_refresh(datetime.now(timezone.utc))

            assert FRESH_LOGIN_AT_KEY not in ctx.session


class TestStampHelpersWriteIsoStrings:
    """All three stamp helpers serialize as ``datetime.isoformat()``.

    ``app.load_user`` and the decorators read every stamp through
    ``session_stamp``, which reads a non-string or a naive value as no
    stamp at all; a regression that wrote one (e.g. the raw datetime,
    or ``str(now)`` of a naive value) would sign every session out, or
    send it to /reauth, on its first request after a successful login.
    """

    def test_stamp_login_session_writes_iso_strings(self, app):
        """All three keys parse with ``datetime.fromisoformat``."""
        with app.test_request_context("/") as ctx:
            stamp_login_session(datetime.now(timezone.utc), _STAMPED_USER_ID, _A_TOKEN)
            for key in (
                SESSION_CREATED_AT_KEY,
                SESSION_LAST_ACTIVITY_KEY,
                FRESH_LOGIN_AT_KEY,
            ):
                value = ctx.session[key]
                assert isinstance(value, str)
                parsed = datetime.fromisoformat(value)
                assert parsed.tzinfo is not None, (
                    f"{key} must round-trip as a timezone-aware "
                    "datetime: session_stamp reads a naive one as no "
                    "stamp, which signs the session out or sends it to "
                    "/reauth."
                )

    def test_stamp_reauth_session_writes_iso_strings(self, app):
        """Activity and fresh keys parse with ``datetime.fromisoformat``."""
        with app.test_request_context("/") as ctx:
            stamp_reauth_session(datetime.now(timezone.utc))
            for key in (SESSION_LAST_ACTIVITY_KEY, FRESH_LOGIN_AT_KEY):
                value = ctx.session[key]
                assert isinstance(value, str)
                parsed = datetime.fromisoformat(value)
                assert parsed.tzinfo is not None

    def test_stamp_session_refresh_writes_iso_strings(self, app):
        """Created and activity keys parse with ``datetime.fromisoformat``."""
        with app.test_request_context("/") as ctx:
            stamp_session_refresh(datetime.now(timezone.utc))
            for key in (SESSION_CREATED_AT_KEY, SESSION_LAST_ACTIVITY_KEY):
                value = ctx.session[key]
                assert isinstance(value, str)
                parsed = datetime.fromisoformat(value)
                assert parsed.tzinfo is not None


#: Ruling plan#3069's thirty days, spelled here as an oracle the model's
#: constant cannot move silently.
_THIRTY_DAYS = timedelta(days=30)


class TestTheSignInToken:
    """Only a sign-in writes ``_session_token``; a password change does not."""

    def test_stamp_login_session_writes_it(self, app):
        """The token the sign-in's record was given rides the session."""
        with app.test_request_context("/") as ctx:
            stamp_login_session(datetime.now(timezone.utc), _STAMPED_USER_ID, _A_TOKEN)

            assert ctx.session[SESSION_TOKEN_KEY] == _A_TOKEN

    def test_stamp_password_change_writes_the_three_and_leaves_the_sign_in(
        self, app,
    ):
        """Created, activity and fresh move to ``now``; the user and token stay.

        So a password change survives its own bump and restarts the step-up
        window, and does not restart the session's thirty days, which its
        sign-in's record holds.
        """
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_USER_ID_KEY] = str(_STAMPED_USER_ID)
            ctx.session[SESSION_TOKEN_KEY] = _A_TOKEN
            now = datetime.now(timezone.utc)

            stamp_password_change(now)

            iso = now.isoformat()
            assert ctx.session[SESSION_CREATED_AT_KEY] == iso
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso
            assert ctx.session[SESSION_USER_ID_KEY] == str(_STAMPED_USER_ID)
            assert ctx.session[SESSION_TOKEN_KEY] == _A_TOKEN


class TestSessionToken:
    """``session_token`` reads the token, or ``None`` for one no sign-in wrote."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="missing"),
            pytest.param(12345, id="not-a-string"),
        ],
    )
    def test_no_token_is_none(self, app, value):
        """Never an exception: each of these must read as "sign in again"."""
        with app.test_request_context("/") as ctx:
            if value is not None:
                ctx.session[SESSION_TOKEN_KEY] = value

            assert session_token() is None

    def test_a_token_is_itself(self, app):
        """The string a sign-in wrote is returned as written."""
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_TOKEN_KEY] = _A_TOKEN

            assert session_token() == _A_TOKEN


class TestSessionStamp:
    """``session_stamp`` reads a stamp, or ``None`` for one that cannot be read."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="missing"),
            pytest.param("not-a-timestamp", id="malformed"),
            pytest.param(12345, id="not-a-string"),
            pytest.param("2026-03-01T12:00:00", id="naive"),
        ],
    )
    def test_an_unreadable_stamp_is_none(self, app, value):
        """Never an exception: each of these must read as "sign in again"."""
        with app.test_request_context("/") as ctx:
            if value is not None:
                ctx.session[SESSION_CREATED_AT_KEY] = value

            assert session_stamp(SESSION_CREATED_AT_KEY) is None

    def test_an_aware_stamp_is_its_moment(self, app):
        """A future one too: each check says what a clock jump means to it."""
        with app.test_request_context("/") as ctx:
            moment = datetime.now(timezone.utc) + timedelta(hours=1)
            ctx.session[SESSION_CREATED_AT_KEY] = moment.isoformat()

            assert session_stamp(SESSION_CREATED_AT_KEY) == moment


def _signed_in(user_id, signed_in_at):
    """Record a sign-in of *user_id* at *signed_in_at* and stamp this session with it.

    The two halves of ``_complete_sign_in`` this module's helpers own, run in
    the test's request context.

    Returns:
        The sign-in's token.
    """
    token = sign_in_service.record_sign_in(user_id, None, now=signed_in_at)
    stamp_login_session(signed_in_at, user_id, token)
    return token


class TestSignInIsLive:
    """A sign-in is live while its record is: younger than thirty days, never ended."""

    @pytest.mark.parametrize(
        "age, live",
        [
            pytest.param(_THIRTY_DAYS, False, id="thirty-days-to-the-second"),
            pytest.param(
                _THIRTY_DAYS - timedelta(seconds=1), True, id="a-second-younger",
            ),
            pytest.param(-timedelta(hours=1), True, id="in-the-future"),
        ],
    )
    def test_its_age_decides(self, app, seed_user, age, live):
        """Frozen, so the age is exact: thirty days to the second is too old.

        The age is the record's, which the session's cookie cannot move.
        """
        user_id = seed_user["user"].id
        moment = datetime.now(timezone.utc)
        with time_machine.travel(moment, tick=False):
            with app.test_request_context("/"):
                _signed_in(user_id, moment - age)

                assert sign_in_is_live(user_id) is live

    def test_another_users_session_is_not_live(self, app, seed_user):
        """Asked for any user but the one whose record the token names: ``False``.

        What a remember-me restore onto this session would ask (plan#2173).
        The control is the same session asked for its own user.
        """
        user_id = seed_user["user"].id
        with app.test_request_context("/"):
            _signed_in(user_id, datetime.now(timezone.utc))

            assert sign_in_is_live(user_id) is True
            assert sign_in_is_live(user_id + 1) is False

    def test_a_session_with_no_token_is_not_live(self, app, seed_user):
        """Every session signed in before the record existed is one."""
        user_id = seed_user["user"].id
        with app.test_request_context("/") as ctx:
            _signed_in(user_id, datetime.now(timezone.utc))
            del ctx.session[SESSION_TOKEN_KEY]

            assert sign_in_is_live(user_id) is False

    def test_a_session_whose_record_is_gone_is_not_live(self, app, seed_user):
        """However good its cookie: the record, not the cookie, answers.

        The record is deleted as a sign-out deletes it, from another request:
        this session's cookie still carries its token.
        """
        user_id = seed_user["user"].id
        with app.test_request_context("/"):
            token = _signed_in(user_id, datetime.now(timezone.utc))
            assert sign_in_is_live(user_id) is True

            sign_in_service.end_sign_in(token)

            assert session_token() == token
            assert sign_in_is_live(user_id) is False


class TestEndThisSignIn:
    """Sign-out's server half: the record goes, committed, then the token."""

    def test_it_deletes_the_record_and_then_the_token(self, app, seed_user):
        """Read back after the commit: no row holds the token, and the cookie has none."""
        user_id = seed_user["user"].id
        with app.test_request_context("/") as ctx:
            _signed_in(user_id, datetime.now(timezone.utc))

            end_this_sign_in()

            assert SESSION_TOKEN_KEY not in ctx.session
        db.session.expire_all()
        assert db.session.query(SignIn).filter_by(user_id=user_id).count() == 0

    def test_it_leaves_the_users_other_sign_ins(self, app, seed_user):
        """Another browser's sign-in of the same user stays live."""
        user_id = seed_user["user"].id
        now = datetime.now(timezone.utc)
        with app.test_request_context("/"):
            other = sign_in_service.record_sign_in(user_id, None, now=now)
            _signed_in(user_id, now)

            end_this_sign_in()

            assert sign_in_service.is_live(user_id, other, now) is True

    def test_a_session_with_no_token_ends_nothing(self, app, seed_user):
        """Nothing to name, so nothing is deleted: another sign-in stays."""
        user_id = seed_user["user"].id
        now = datetime.now(timezone.utc)
        with app.test_request_context("/"):
            other = sign_in_service.record_sign_in(user_id, None, now=now)

            end_this_sign_in()

            assert sign_in_service.is_live(user_id, other, now) is True
