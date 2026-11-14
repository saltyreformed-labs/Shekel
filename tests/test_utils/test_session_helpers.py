"""Shekel Budget App -- Unit tests for app.utils.session_helpers.

Locks down the contracts documented in ``app/utils/session_helpers.py``:

1. A session is live exactly while its sign-in's record is (rulings
   plan#3925, plan#3927): :func:`sign_in_is_live` asks the record, signing
   out deletes this browser's (:func:`end_this_sign_in`), and "Log out all
   other sessions" and Change password delete every other one, keeping this
   browser's under a new token (:func:`end_every_other_sign_in`, ruling
   plan#4010).

2. The two ``stamp_*`` helpers (``stamp_login_session``,
   ``stamp_reauth_session``) write the right SUBSET of lifecycle keys for
   each semantic event so the loader, the idle-timeout check, and the
   fresh-login check all stay coherent (commit C-10 -- F-006, F-035,
   F-045).  A regression that wrote the sign-in's token on /reauth or a
   password change would make either a sign-in.

These tests exercise the helpers in isolation against a real DB and
a real Flask test request context -- no mocks of ``db.session`` or
``flask.session``, because correctness depends on the exact
serialization round-trip of those two pieces of state.
"""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy
import time_machine

from app.extensions import db
from app.models.sign_in import SignIn
from app.models.user import User
from app.services import sign_in_service
from app.utils.bearer_tokens import token_hash
from app.utils.session_helpers import (
    FRESH_LOGIN_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_TOKEN_KEY,
    end_every_other_sign_in,
    end_this_sign_in,
    session_stamp,
    session_token,
    sign_in_is_live,
    stamp_login_session,
    stamp_reauth_session,
)

#: A sign-in token for the stamping tests that read no record: the cookie
#: carries whatever token it is handed, and only the tests that ask whether a
#: sign-in is live record one.
_A_TOKEN = "a-sign-in-token"


class TestStampLoginSession:
    """``stamp_login_session`` writes the token and BOTH lifecycle timestamps."""

    def test_writes_both_timestamps_with_identical_value(self, app):
        """Each timestamp carries the same ISO-8601 string from the single ``now``.

        Reusing one value across the writes is required so a later audit
        comparison cannot falsely flag the keys as clock-skewed.  This test
        is the one enforcement of that contract: no integration test compares
        the keys, and
        ``tests/test_adversarial/test_step_up/test_fresh_login_and_reauth.py``
        checks only that /login stamps ``_fresh_login_at``.
        """
        with app.test_request_context("/") as ctx:
            now = datetime.now(timezone.utc)
            stamp_login_session(now, _A_TOKEN)

            iso = now.isoformat()
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso

    def test_overwrites_existing_values(self, app):
        """Subsequent calls overwrite, never append.

        A regression that mutated rather than overwrote could leak
        an older timestamp into a freshly-stamped session, and the
        idle / fresh checks would compare against the leaked value.
        """
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_LAST_ACTIVITY_KEY] = "stale-1"
            ctx.session[FRESH_LOGIN_AT_KEY] = "stale-2"
            ctx.session[SESSION_TOKEN_KEY] = "stale-3"

            now = datetime.now(timezone.utc)
            stamp_login_session(now, _A_TOKEN)

            iso = now.isoformat()
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso
            assert ctx.session[SESSION_TOKEN_KEY] == _A_TOKEN


class TestStampReauthSession:
    """``stamp_reauth_session`` updates activity + fresh; the token stays."""

    def test_updates_activity_and_fresh_and_leaves_the_sign_in(self, app):
        """Activity and fresh move to ``now``; the sign-in's token stays.

        /reauth and a password change each restart the step-up window, and
        neither is a sign-in: a regression that wrote the token would stand
        for a new sign-in, and one that dropped it would sign the session
        out.  The session's thirty days are its record's (ruling plan#3069).
        """
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_TOKEN_KEY] = _A_TOKEN
            now = datetime.now(timezone.utc)

            stamp_reauth_session(now)

            iso = now.isoformat()
            assert ctx.session[SESSION_LAST_ACTIVITY_KEY] == iso
            assert ctx.session[FRESH_LOGIN_AT_KEY] == iso
            assert ctx.session[SESSION_TOKEN_KEY] == _A_TOKEN


class TestStampHelpersWriteIsoStrings:
    """Both stamp helpers serialize as ``datetime.isoformat()``.

    ``app.load_user`` and the decorators read every stamp through
    ``session_stamp``, which reads a non-string or a naive value as no
    stamp at all; a regression that wrote one (e.g. the raw datetime,
    or ``str(now)`` of a naive value) would sign every session out, or
    send it to /reauth, on its first request after a successful login.
    """

    @pytest.mark.parametrize(
        "stamp",
        [
            pytest.param(lambda now: stamp_login_session(now, _A_TOKEN), id="login"),
            pytest.param(stamp_reauth_session, id="reauth"),
        ],
    )
    def test_both_timestamps_parse_as_aware_iso_strings(self, app, stamp):
        """Activity and fresh keys parse with ``datetime.fromisoformat``, aware."""
        with app.test_request_context("/") as ctx:
            stamp(datetime.now(timezone.utc))
            for key in (SESSION_LAST_ACTIVITY_KEY, FRESH_LOGIN_AT_KEY):
                value = ctx.session[key]
                assert isinstance(value, str)
                parsed = datetime.fromisoformat(value)
                assert parsed.tzinfo is not None, (
                    f"{key} must round-trip as a timezone-aware "
                    "datetime: session_stamp reads a naive one as no "
                    "stamp, which signs the session out or sends it to "
                    "/reauth."
                )


#: Ruling plan#3069's thirty days, spelled here as an oracle the model's
#: constant cannot move silently.
_THIRTY_DAYS = timedelta(days=30)


class TestTheSignInToken:
    """A sign-in writes ``_session_token``."""

    def test_stamp_login_session_writes_it(self, app):
        """The token the sign-in's record was given rides the session."""
        with app.test_request_context("/") as ctx:
            stamp_login_session(datetime.now(timezone.utc), _A_TOKEN)

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
                ctx.session[SESSION_LAST_ACTIVITY_KEY] = value

            assert session_stamp(SESSION_LAST_ACTIVITY_KEY) is None

    def test_an_aware_stamp_is_its_moment(self, app):
        """A future one too: each check says what a clock jump means to it."""
        with app.test_request_context("/") as ctx:
            moment = datetime.now(timezone.utc) + timedelta(hours=1)
            ctx.session[SESSION_LAST_ACTIVITY_KEY] = moment.isoformat()

            assert session_stamp(SESSION_LAST_ACTIVITY_KEY) == moment


def _signed_in(user_id, signed_in_at):
    """Record a sign-in of *user_id* at *signed_in_at* and stamp this session with it.

    The two halves of ``_complete_sign_in`` this module's helpers own, run in
    the test's request context.

    Returns:
        The sign-in's token.
    """
    token = sign_in_service.record_sign_in(user_id, None, now=signed_in_at)
    stamp_login_session(signed_in_at, token)
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


def _hashes_of(user_id):
    """Return the hashes of *user_id*'s sign-in records, read fresh."""
    db.session.expire_all()
    return set(db.session.execute(
        sqlalchemy.select(SignIn.token_hash).where(SignIn.user_id == user_id)
    ).scalars())


class TestEndEveryOtherSignIn:
    """"Log out all other sessions" and Change password: this browser alone goes on."""

    def test_every_other_sign_in_ends_and_this_one_goes_on_under_a_new_token(
        self, app, seed_user,
    ):
        """Read back after the commit: one record, this session's new token's.

        Its ``signed_in_at`` is the sign-in's, so its thirty days run on
        (ruling plan#3069), and the old token names nothing, so a copy of
        this browser's cookie is refused (ruling plan#4010).
        """
        user_id = seed_user["user"].id
        signed_in_at = datetime.now(timezone.utc) - timedelta(days=3)
        with app.test_request_context("/") as ctx:
            other = sign_in_service.record_sign_in(user_id, None, now=signed_in_at)
            old = _signed_in(user_id, signed_in_at)

            end_every_other_sign_in(seed_user["user"], datetime.now(timezone.utc))

            new = ctx.session[SESSION_TOKEN_KEY]
        assert new not in (old, other)
        assert _hashes_of(user_id) == {token_hash(new)}
        assert db.session.query(SignIn).filter_by(user_id=user_id).one().signed_in_at == (
            signed_in_at
        )

    def test_it_moves_the_trusts_end_to_the_acts_instant(self, app, seed_user):
        """Committed: every trust of the user from before it has ended."""
        user_id = seed_user["user"].id
        now = datetime.now(timezone.utc)
        with app.test_request_context("/"):
            _signed_in(user_id, now)

            end_every_other_sign_in(seed_user["user"], now)

        db.session.expire_all()
        assert db.session.get(User, user_id).session_invalidated_at == now

    def test_a_failed_commit_leaves_the_cookie_as_it_was(
        self, app, seed_user, monkeypatch,
    ):
        """The token reaches the cookie only after the commit.

        A commit that fails rolls the act back, so the old record is still
        this browser's, and the cookie must still name it, or the browser
        would be signed out by an act that never happened.
        """
        user_id = seed_user["user"].id

        def refuse():
            raise sqlalchemy.exc.OperationalError("COMMIT", {}, Exception("refused"))

        with app.test_request_context("/") as ctx:
            old = _signed_in(user_id, datetime.now(timezone.utc))
            db.session.commit()
            monkeypatch.setattr(db.session, "commit", refuse)

            with pytest.raises(sqlalchemy.exc.OperationalError):
                end_every_other_sign_in(seed_user["user"], datetime.now(timezone.utc))

            assert ctx.session[SESSION_TOKEN_KEY] == old
            monkeypatch.undo()
            db.session.rollback()
        assert _hashes_of(user_id) == {token_hash(old)}

    def test_another_users_sign_ins_stay(self, app, seed_user, seed_second_user):
        """The act is the one user's: another user's record keeps its token."""
        user_id = seed_user["user"].id
        theirs = sign_in_service.record_sign_in(
            seed_second_user["user"].id, None, now=datetime.now(timezone.utc),
        )
        with app.test_request_context("/"):
            _signed_in(user_id, datetime.now(timezone.utc))

            end_every_other_sign_in(seed_user["user"], datetime.now(timezone.utc))

        assert _hashes_of(seed_second_user["user"].id) == {token_hash(theirs)}
