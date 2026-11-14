"""Shekel Budget App -- Session lifecycle helpers.

What a signed-in session carries, which ``load_user`` and
``fresh_login_required`` consult on every authenticated request (commit
C-10 -- F-006, F-035, F-045), and the acts that end one.

What the session carries
------------------------

Each signed-in session carries, in the signed session cookie:

  * ``_session_token`` (``SESSION_TOKEN_KEY``) -- the token naming its
    sign-in's record on the server (ruling plan#3925,
    ``app.services.sign_in_service``).  ``load_user`` refuses a session
    whose token names no live record of the user it loads
    (:func:`sign_in_is_live`).

  * ``_session_last_activity_at``
    (``SESSION_LAST_ACTIVITY_KEY``) -- the moment of the most
    recent authenticated request.  Refreshed automatically by the
    ``before_request`` hook in ``app/__init__.py`` on every request
    where ``current_user.is_authenticated`` is True; ``load_user``
    rejects the session when the gap to ``now()`` exceeds
    ``IDLE_TIMEOUT_MINUTES`` (F-006).

  * ``_fresh_login_at`` (``FRESH_LOGIN_AT_KEY``) -- the moment of the
    most recent password (or password+TOTP) verification.  The
    :func:`~app.utils.auth_helpers.fresh_login_required` decorator
    uses this to gate high-value operations: any value older than
    ``FRESH_LOGIN_MAX_AGE_MINUTES`` redirects to ``/reauth`` (F-045).
    Set on a sign-in, on a successful ``/reauth`` and on a password
    change -- every code path that requires the user to type a password.

**A session is live exactly while its sign-in's record is** (rulings
plan#3925, plan#3927).  The cookie is signed, but a copy of it is as good as
the original, so the server holds a row per finished sign-in and only
deleting the row ends the session.  Signing out deletes this browser's
(:func:`end_this_sign_in`), and every act that signs a user out everywhere
deletes all of theirs, through the one act
(``app.services.sign_in_service.sign_out_everywhere``).  The two a signed-in
browser presses, "Log out all other sessions" and Change password, keep that
browser signed in under a new token (:func:`end_every_other_sign_in`, ruling
plan#4010), so every copy of its old cookie dies with the rest.  Nothing on
the account and nothing else in the cookie can keep a session alive past its
record.

**A session ends** :data:`~app.utils.sign_in_lifetimes.KNOWN_LIFETIME` **after
its sign-in, however active** (ruling plan#3069), read from the row's
``signed_in_at``, which nothing after the sign-in moves: the act that keeps
the pressing browser replaces its record's token and not its instant.  The
sign-in renews the browser's record from the same instant, so a browser that
is signed in is a known one (plan#2772), unless ten wrong guesses on its
cookie, its own or a copy's, removed its record.

**A session is signed in only by the app's own sign-in, and only as the
user that sign-in named** (plan#2173, finding S-02 of the 2026-10-29
security review).  The sign-in's record holds its user, and ``load_user``
asks for a live record of the user it loads, so a token from another user's
sign-in signs nobody in.  It reads a session without
``_session_last_activity_at`` as stale.

What the rule exists to refuse is Flask-Login's remember-me restore.  A
"Remember me" cookie is the same ``<id>|HMAC(SECRET_KEY, id)`` value for
every remembered sign-in, and Flask-Login honours it whether or not the
app ever issues one again: whenever the loader refuses the session's own
user, or the session names none, Flask-Login writes the cookie's user id
into the CURRENT session and calls the loader again.  That session may be
empty, carrying no token, or it may be another user's, refused for that
user but still carrying that user's token and timestamps, which would pass
for the cookie's user were the token not asked whose sign-in it named.
Before plan#2173 both happened: a copied cookie signed a browser in past
every "log out all sessions" (the 2026-10-29 review), and then, with the
timestamps required, a copied cookie riding another user's signed-out or
deactivated session signed in as the cookie's user (the 2026-10-31 review
of plan#2173, measured).  Flask-Login's own ``_user_id`` cannot say whose
sign-in the session was, because the restore rewrites it; the record's
user is written by the app's sign-in alone.  The rule refuses every such
cookie ever issued, with no ``SECRET_KEY`` rotation (ruling plan#2406,
which also deleted the checkbox).

The two timestamps are deliberately distinct.  An idle-but-not-stale
session can still complete a benign GET (``_session_last_activity_at``
keeps refreshing as the user clicks around), but a high-value POST
will demand a re-auth even on an actively-used session if more than
``FRESH_LOGIN_MAX_AGE_MINUTES`` have passed since the last password
entry.

Stamping helpers
----------------

The two ``stamp_*`` helpers below own the only writes to those keys
outside ``load_user`` / the ``before_request`` hook and the two acts that
end or renew this browser's sign-in.  Each maps to a single semantic event
so the caller cannot accidentally write the wrong subset:

  * :func:`stamp_login_session` -- a finished sign-in (the password POST
    with no code owed, a successful MFA verify, a trusted device's
    password).  Writes the sign-in's token and both timestamps, the set
    :func:`login_session_stamps` names.

  * :func:`stamp_reauth_session` -- the user typed their password again on
    a signed-in session: a successful ``/reauth``, or a password change.
    Writes activity + fresh; the token stays, so neither is a sign-in and
    neither restarts the thirty days.

Audit references: F-002, F-003, F-006, F-032, F-035, F-045 (commits
C-08 and C-10 of the 2026-04-15 security remediation plan).  See
also the ``app/routes/auth/`` package for the call sites that consume
these helpers, and ``app/__init__.py:load_user`` for the read paths.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import session as flask_session

from app.extensions import db
from app.models.user import User
from app.services import sign_in_service


# Session-cookie keys consumed by ``load_user``,
# ``fresh_login_required``, and the ``before_request`` activity-refresh
# hook.  Centralised so the helpers, the loader, the decorator, and
# every test that pokes at the cookie share a single source of truth;
# a typo on any one key would silently break the lifecycle invariant
# it gates.
SESSION_LAST_ACTIVITY_KEY = "_session_last_activity_at"
FRESH_LOGIN_AT_KEY = "_fresh_login_at"
SESSION_TOKEN_KEY = "_session_token"


def login_session_stamps(now: datetime, token: str) -> dict[str, str]:
    """Return the stamps a sign-in writes, keyed by session key.

    The one statement of WHICH keys a new sign-in carries:
    :func:`stamp_login_session` writes exactly these, and a script that
    builds a signed-in session by hand (``tests/manual``) writes them too,
    so ``load_user`` cannot tell the two apart and a key added here
    reaches both.

    Args:
        now: Single timezone-aware UTC datetime for both timestamps: the
            instant the sign-in's record holds.
        token: The token
            :func:`~app.services.sign_in_service.record_sign_in` returned
            for this sign-in's record.

    Returns:
        ``{key: value}``: ``_session_token`` as the token, and
        ``_session_last_activity_at`` and ``_fresh_login_at`` as ISO
        timestamps.
    """
    iso = now.isoformat()
    return {
        SESSION_TOKEN_KEY: token,
        SESSION_LAST_ACTIVITY_KEY: iso,
        FRESH_LOGIN_AT_KEY: iso,
    }


def session_stamp(key: str) -> datetime | None:
    """Return the moment this session's stamp *key* holds, or ``None``.

    The one parse of every ISO-8601 stamp the session holds, whichever check
    reads it (plan#3157).  ``None``, never an exception, for a missing key, a
    value that is not an ISO-8601 string and a naive one: ``flask.session`` is
    signed but not encrypted, so no stamp the app never wrote may answer with
    a 500 (commit C-10, F-006).  What ``None`` means, and what a stamp in the
    future means, is each check's to say at its own site, so a future stamp
    is returned as it is.

    Args:
        key: The session key of a stamp the app writes as
            ``datetime.isoformat()``.

    Returns:
        The stamp as a timezone-aware datetime, or ``None``.

    Raises:
        RuntimeError: No active Flask request context.
    """
    try:
        stamped = datetime.fromisoformat(flask_session.get(key))
    except (TypeError, ValueError):
        return None
    return None if stamped.tzinfo is None else stamped


def session_token() -> str | None:
    """Return this session's sign-in token, or ``None``.

    The one read of ``_session_token``.  ``None``, never an exception, for a
    session carrying none and for a value that is not a string, which no
    sign-in writes: ``flask.session`` is signed but not encrypted, so no value
    the app never wrote may answer with a 500 (as :func:`session_stamp`).

    Returns:
        The token :func:`stamp_login_session` or
        :func:`end_every_other_sign_in` wrote, or ``None``.

    Raises:
        RuntimeError: No active Flask request context.
    """
    token = flask_session.get(SESSION_TOKEN_KEY)
    return token if isinstance(token, str) else None


def sign_in_is_live(user_id: int) -> bool:
    """Return True while this session's token names a live sign-in of *user_id*.

    The ONE door the session's sign-in is read through, and the one rule for
    whether a session is signed in (rulings plan#3925, plan#3927): the
    server's record answers, never the cookie, so a session whose record a
    sign-out deleted is refused however good its cookie looks.  ``load_user``
    asks it, and a signed-in request asks it again once it holds its owner's
    write lock, since a sign-out can end the record while the request waits
    (:mod:`app.db_transaction`, ruling plan#4070).
    The record is live for :data:`~app.utils.sign_in_lifetimes.KNOWN_LIFETIME`
    after its sign-in, however active (ruling plan#3069), at the boundary the
    browser's record keeps: a sign-in thirty days old to the second is too old
    (``app.services.sign_in_service._live``).

    False for a session carrying no token, which every session signed in
    before the record existed is, so each is signed out once; and for a token
    naming another user's sign-in, which is what a remember-me restore onto
    another user's session would carry (plan#2173).

    Args:
        user_id: The ``auth.users.id`` being loaded.

    Returns:
        True when :func:`session_token` names a live record of *user_id*.

    Raises:
        RuntimeError: No active Flask request context.
    """
    return sign_in_service.is_live(
        user_id, session_token(), datetime.now(timezone.utc),
    )


def end_this_sign_in() -> None:
    """End this session's sign-in on the server, then drop its token.

    What ``/logout`` does before Flask-Login clears its own keys (ruling
    plan#3925): the record goes first, committed, so every copy of this
    cookie is refused from the commit on.  The token leaves the cookie only
    after the commit: a commit that failed with the token already gone would
    leave this browser unable to name the record a retry must delete, while
    the copies stayed signed in.

    A session carrying no token deletes nothing and commits nothing new.

    Raises:
        RuntimeError: No active Flask request context.
        sqlalchemy.exc.SQLAlchemyError: The delete or its commit failed.  The
            token stays in the cookie, so signing out again can end it.
    """
    sign_in_service.end_sign_in(session_token())
    db.session.commit()
    flask_session.pop(SESSION_TOKEN_KEY, None)


def end_every_other_sign_in(user: User, now: datetime) -> None:
    """End every sign-in of *user* but this browser's, and every trust; commit.

    What "Log out all other sessions" and Change password do (rulings
    plan#3927, plan#4010), through the one act
    (:func:`~app.services.sign_in_service.sign_out_everywhere`): every other
    browser's session is refused at its next request, and this browser's
    sign-in goes on under a new token, with its thirty days unchanged, so a
    copy of this browser's cookie taken before is refused too.

    The commit carries whatever the caller staged before it, so a password
    change's new hash and its security event land with the act or not at all.
    The new token reaches the cookie only after the commit, as a sign-in's
    does: a commit that failed leaves the old record and the old token, so
    this browser stays signed in and the act can be pressed again.

    *user* must be the user signed in on this request: the token kept is this
    session's, and naming any other user would keep none of theirs and sign
    this browser out.

    Args:
        user: The signed-in :class:`~app.models.user.User`, persistent.
        now: The act's instant, which every trust ends at.

    Raises:
        RuntimeError: No active Flask request context.
        sqlalchemy.exc.SQLAlchemyError: The act or its commit failed.  The
            cookie is not changed.
    """
    token = sign_in_service.sign_out_everywhere(
        user, keeping=session_token(), now=now,
    )
    db.session.commit()
    flask_session[SESSION_TOKEN_KEY] = token


def stamp_login_session(now: datetime, token: str) -> None:
    """Stamp the sign-in's token and every lifecycle timestamp on a sign-in.

    Use only where a sign-in finishes: ``_complete_sign_in``, the one
    ``login_user`` site (every ``/login`` and ``/mfa/verify`` that signs a
    user in).  A password change re-authenticates without signing in, and
    stamps through :func:`stamp_reauth_session`.  Every stamp shares a
    single ``now`` value so a later subtraction against
    ``datetime.now(timezone.utc)`` cannot accidentally distinguish them.

    Args:
        now: Single timezone-aware UTC datetime to write into both
            timestamps: the instant the browser's record was renewed at
            and the sign-in's record holds.  Reusing one value (rather
            than calling :func:`datetime.now` for each) avoids microsecond
            drift between the writes that an audit comparison would see
            as clock skew.
        token: The token of this sign-in's record
            (:func:`~app.services.sign_in_service.record_sign_in`).

    Raises:
        RuntimeError: No active Flask request context (raised by
            ``flask.session`` on access).  The caller is always a
            route handler, so this would only surface if the helper
            were called from a CLI command or a background job by
            mistake.
    """
    flask_session.update(login_session_stamps(now, token))


def stamp_reauth_session(now: datetime) -> None:
    """Stamp activity and fresh-login when the user types their password again.

    On the ``/reauth`` success branch (commit C-10), and on a password
    change, which checks the current password the same way: the user just
    proved who they are, so ``_fresh_login_at`` restarts the step-up window,
    and the request is activity.  Neither is a sign-in, so the token stays
    and the session's thirty days run on from its own sign-in (ruling
    plan#3069).  A sign-in (login, MFA verify) calls
    :func:`stamp_login_session` instead.

    Args:
        now: Single timezone-aware UTC datetime to write into both
            keys.  Same single-``now`` rationale as
            :func:`stamp_login_session`.

    Raises:
        RuntimeError: No active Flask request context.
    """
    iso = now.isoformat()
    flask_session[SESSION_LAST_ACTIVITY_KEY] = iso
    flask_session[FRESH_LOGIN_AT_KEY] = iso
