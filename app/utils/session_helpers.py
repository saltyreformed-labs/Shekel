"""Shekel Budget App -- Session lifecycle helpers.

Two cooperating concerns live in this module:

  1. Force-invalidate every session for a given user EXCEPT the one
     making the current request (commit C-08 -- F-002, F-003, F-032).
  2. Stamp the three lifecycle timestamps that ``load_user`` and
     ``fresh_login_required`` consult on every authenticated request
     (commit C-10 -- F-006, F-035, F-045).

Lifecycle timestamps
--------------------

Each authenticated session carries three timestamps in the signed
session cookie:

  * ``_session_created_at`` (``SESSION_CREATED_AT_KEY``) -- the moment
    the session was established (or refreshed after a credential
    change).  ``load_user`` rejects the session if this value is
    strictly older than the database-side
    ``users.session_invalidated_at``, which is how
    :func:`invalidate_other_sessions` makes parallel sessions
    self-terminate.

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
    Set on initial login, on the password leg of MFA verify, on
    successful ``/reauth``, and on ``change_password`` -- every code
    path that requires the user to type a password.

Beside them the session carries ``_session_user_id``
(``SESSION_USER_ID_KEY``): the id of the user whose sign-in wrote the
stamps, and ``_session_token`` (``SESSION_TOKEN_KEY``): the token naming its
sign-in's record on the server.

**A session is live only while its sign-in's record is** (ruling
plan#3925, ``app.services.sign_in_service``).  The cookie is signed, but a
copy of it is as good as the original, so the server holds a row per finished
sign-in and ``load_user`` refuses a session whose token names no live row of
its user (:func:`sign_in_is_live`).  Signing out deletes the row
(:func:`end_this_sign_in`), so every copy of the cookie dies with it.

**A session ends** :data:`~app.models.known_device.KNOWN_LIFETIME` **after
its sign-in, however active** (ruling plan#3069), read from the row's
``signed_in_at``, which nothing after the sign-in moves.  The sign-in renews
the browser's record from the same instant, so a browser that is signed in is
a known one (plan#2772), unless ten wrong guesses on its cookie, its own or a
copy's, removed its record.  ``_session_created_at`` cannot be that bound:
Change password, "log out all other sessions" and a backup-code sign-in each
move it to now, so that the session which pressed it stays past the bump it
writes, and none of them renews the browser's record.

**A session is signed in only by the app's own sign-in, and only as the
user that sign-in named** (plan#2173, finding S-02 of the 2026-10-29
security review).  Every sign-in writes the user stamp, the sign-in's token
and the three timestamps in the request that calls ``login_user``
(:func:`stamp_login_session`, from ``_complete_sign_in``, the one
``login_user`` site).  ``load_user`` reads ``_session_created_at`` only
through :func:`sign_in_created_at`, which answers ``None`` unless the user
stamp is the user being loaded and the stamp can be read, and refuses the
session then; it reads a session without ``_session_last_activity_at`` as
stale.

What the rule exists to refuse is Flask-Login's remember-me restore.  A
"Remember me" cookie is the same ``<id>|HMAC(SECRET_KEY, id)`` value for
every remembered sign-in, and Flask-Login honours it whether or not the
app ever issues one again: whenever the loader refuses the session's own
user, or the session names none, Flask-Login writes the cookie's user id
into the CURRENT session and calls the loader again.  That session may be
empty, or it may be another user's, refused for that user but still
carrying that user's timestamps, which would pass for the cookie's user.
Before the user stamp both happened: a copied cookie signed a browser in
past every "log out all sessions" (the 2026-10-29 review), and then, with
the timestamps required, a copied cookie riding another user's
signed-out or deactivated session signed in as the cookie's user (the
2026-10-31 review of plan#2173, measured).  Flask-Login's own
``_user_id`` cannot say whose sign-in wrote the stamps, because the
restore rewrites it; the user stamp is written by the app's sign-in
alone.  The rule refuses every such cookie ever issued, with no
``SECRET_KEY`` rotation (ruling plan#2406, which also deleted the
checkbox).

The three keys are deliberately distinct.  An idle-but-not-stale
session can still complete a benign GET (``_session_last_activity_at``
keeps refreshing as the user clicks around), but a high-value POST
will demand a re-auth even on an actively-used session if more than
``FRESH_LOGIN_MAX_AGE_MINUTES`` have passed since the last password
entry.

Stamping helpers
----------------

The four ``stamp_*`` helpers below own the only writes to those
keys outside ``load_user`` / the ``before_request`` hook.  Each maps
to a single semantic event so the caller cannot accidentally write
the wrong subset:

  * :func:`stamp_login_session` -- a finished sign-in (the password POST
    with no code owed, a successful MFA verify, a trusted device's
    password).  Writes the user stamp, the sign-in's token and all three
    timestamps, the set :func:`login_session_stamps` names.

  * :func:`stamp_password_change` -- a password change on an existing
    session.  Writes all three timestamps and leaves the user stamp and
    the token alone, because a password change is not a sign-in.

  * :func:`stamp_reauth_session` -- ``/reauth`` success on an
    existing session.  Writes activity + fresh; ``_session_created_at``
    stays at its original value so any earlier session that pre-dates
    a future ``invalidate_other_sessions`` call still gets caught.

  * :func:`stamp_session_refresh` -- ``/invalidate-sessions`` where
    the user did not actually re-authenticate.  Writes created +
    activity; ``_fresh_login_at`` stays untouched so a stale
    fresh-login window does not silently extend.

Invalidate-other-sessions mechanism
-----------------------------------

The user record carries ``session_invalidated_at``, a timezone-aware
column updated whenever the user's other sessions must be terminated.
On every request, ``load_user`` in ``app/__init__.py`` rejects any
session whose cookie-side ``_session_created_at`` is strictly older
than the database-side ``session_invalidated_at``.

``invalidate_other_sessions`` writes a single ``now`` timestamp to
both fields atomically (commit, then refresh the cookie), so:

  * Every other live session for the user has a strictly older
    ``_session_created_at`` and is rejected on its next request.
  * The session making this request has ``_session_created_at`` equal
    to ``session_invalidated_at`` -- the strict less-than comparison
    in ``load_user`` is False, so the current session survives.

Caller contract for :func:`invalidate_other_sessions`
-----------------------------------------------------

  * The caller must have already committed any state change that
    motivates the invalidation (its one caller's: the backup-code list
    a consumed code shortened).  The helper commits its own write
    of ``session_invalidated_at`` and would inadvertently flush any
    in-flight session state with it.

  * The helper requires an active Flask request context because it
    refreshes the session cookie via ``flask.session``.  Calling it
    outside a request context is a programming error -- it will
    surface as a ``RuntimeError`` from Flask, not a silent no-op.

  * The helper assumes ``user`` is the same identity authenticated on
    the current request.  Passing a different user would invalidate
    that user's sessions but refresh the WRONG cookie -- the caller's
    session would still be rejected on the next request.  Documented
    here rather than enforced at runtime: ``flask_login.current_user``
    is a request-local proxy and importing it into this module would
    couple the helper to the auth blueprint's request lifecycle for
    no defensive gain.

Audit references: F-002, F-003, F-006, F-032, F-035, F-045 (commits
C-08 and C-10 of the 2026-04-15 security remediation plan).  See
also the ``app/routes/auth/`` package for the call sites that consume
these helpers, and ``app/__init__.py:load_user`` for the read paths.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import session as flask_session

from app.extensions import db
from app.models.user import User
from app.services import sign_in_service
from app.utils.log_events import AUTH, EVT_OTHER_SESSIONS_INVALIDATED, log_event


logger = logging.getLogger(__name__)


# Session-cookie keys consumed by ``load_user``,
# ``fresh_login_required``, and the ``before_request`` activity-refresh
# hook.  Centralised so the helpers, the loader, the decorator, and
# every test that pokes at the cookie share a single source of truth;
# a typo on any one key would silently break the lifecycle invariant
# it gates.
SESSION_CREATED_AT_KEY = "_session_created_at"
SESSION_LAST_ACTIVITY_KEY = "_session_last_activity_at"
FRESH_LOGIN_AT_KEY = "_fresh_login_at"
SESSION_USER_ID_KEY = "_session_user_id"
SESSION_TOKEN_KEY = "_session_token"


def login_session_stamps(
    now: datetime, user_id: int, token: str,
) -> dict[str, str]:
    """Return the stamps a sign-in writes, keyed by session key.

    The one statement of WHICH keys a new sign-in carries:
    :func:`stamp_login_session` writes exactly these, and a script that
    builds a signed-in session by hand (``tests/manual``) writes them too,
    so ``load_user`` cannot tell the two apart and a key added here
    reaches both.

    Args:
        now: Single timezone-aware UTC datetime for all three timestamps:
            the instant the sign-in's record holds.
        user_id: The ``auth.users.id`` being signed in.
        token: The token
            :func:`~app.services.sign_in_service.record_sign_in` returned
            for this sign-in's record.

    Returns:
        ``{key: value}``: ``_session_user_id`` as the id's string, the
        spelling Flask-Login hands ``load_user`` (``User.get_id()``),
        ``_session_token`` as the token, and ``_session_created_at``,
        ``_session_last_activity_at`` and ``_fresh_login_at`` as ISO
        timestamps.
    """
    iso = now.isoformat()
    return {
        SESSION_USER_ID_KEY: str(user_id),
        SESSION_TOKEN_KEY: token,
        SESSION_CREATED_AT_KEY: iso,
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


def _stamped_as(user_id: str) -> bool:
    """Return whether the app's own sign-in of *user_id* wrote this session's stamps.

    The module docstring says why Flask-Login's own ``_user_id`` cannot
    answer this.

    Args:
        user_id: The id Flask-Login asks ``load_user`` for, as a string.

    Returns:
        True when the session's user stamp is *user_id*.
    """
    return flask_session.get(SESSION_USER_ID_KEY) == user_id


def sign_in_created_at(user_id: str) -> datetime | None:
    """Return this session's ``_session_created_at`` for *user_id*, or ``None``.

    The ONE door ``load_user`` reads ``_session_created_at`` through: it
    answers only when the app's own sign-in of *user_id* wrote the session's
    stamps, so a session another user's sign-in stamped, or none did, reads
    as never signed in.  The stamp is read by :func:`session_stamp`, so a
    stamp that is missing, not ISO-8601 or naive reads the same way: "sign
    in again", never a 500.

    Args:
        user_id: The id Flask-Login asks ``load_user`` for, as a string.

    Returns:
        The session's ``_session_created_at`` as a timezone-aware datetime
        when its user stamp is *user_id* and the stamp can be read, else
        ``None``.

    Raises:
        RuntimeError: No active Flask request context.
    """
    if not _stamped_as(user_id):
        return None
    return session_stamp(SESSION_CREATED_AT_KEY)


def session_token() -> str | None:
    """Return this session's sign-in token, or ``None``.

    The one read of ``_session_token``.  ``None``, never an exception, for a
    session carrying none and for a value that is not a string, which no
    sign-in writes: ``flask.session`` is signed but not encrypted, so no value
    the app never wrote may answer with a 500 (as :func:`session_stamp`).

    Returns:
        The token :func:`stamp_login_session` wrote, or ``None``.

    Raises:
        RuntimeError: No active Flask request context.
    """
    token = flask_session.get(SESSION_TOKEN_KEY)
    return token if isinstance(token, str) else None


def sign_in_is_live(user_id: int) -> bool:
    """Return True while this session's token names a live sign-in of *user_id*.

    The ONE door ``load_user`` reads the session's sign-in through (ruling
    plan#3925): the server's record answers, never the cookie, so a session
    whose record was deleted by a sign-out is refused however good its cookie
    looks.  The record is live for
    :data:`~app.models.known_device.KNOWN_LIFETIME` after its sign-in, however
    active (ruling plan#3069), at the boundary the browser's record keeps: a
    sign-in thirty days old to the second is too old
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


def stamp_login_session(now: datetime, user_id: int, token: str) -> None:
    """Stamp the user, the sign-in's token and every lifecycle timestamp on a sign-in.

    Use only where a sign-in finishes: ``_complete_sign_in``, the one
    ``login_user`` site (every ``/login`` and ``/mfa/verify`` that signs a
    user in).  A password change re-authenticates without signing in, and
    stamps through :func:`stamp_password_change`.  Every stamp shares a
    single ``now`` value so a later subtraction against
    ``datetime.now(timezone.utc)`` cannot accidentally distinguish them.

    Args:
        now: Single timezone-aware UTC datetime to write into all three
            timestamps: the instant the browser's record was renewed at
            and the sign-in's record holds.  Reusing one value (rather
            than calling :func:`datetime.now` for each) avoids microsecond
            drift between the writes that an audit comparison would see
            as clock skew.
        user_id: The ``auth.users.id`` this sign-in is for, written as
            the session's user stamp (:func:`sign_in_created_at`).
        token: The token of this sign-in's record
            (:func:`~app.services.sign_in_service.record_sign_in`).

    Raises:
        RuntimeError: No active Flask request context (raised by
            ``flask.session`` on access).  The caller is always a
            route handler, so this would only surface if the helper
            were called from a CLI command or a background job by
            mistake.
    """
    flask_session.update(login_session_stamps(now, user_id, token))


def stamp_password_change(now: datetime) -> None:
    """Stamp a password change on the session that made it.

    The change bumps the user's ``session_invalidated_at`` to *now*, so
    ``_session_created_at`` moves to *now* for this session to survive the
    bump while every other one is refused.  The user just typed their
    current password, so ``_fresh_login_at`` restarts the step-up window,
    and the request is activity.  It is not a sign-in: the user stamp and
    the token stay as they are, so a password change does not restart the
    thirty days a session lasts (ruling plan#3069), as it renews no
    browser's record.

    Args:
        now: The single instant the change's bump and these three stamps
            share.

    Raises:
        RuntimeError: No active Flask request context.
    """
    iso = now.isoformat()
    flask_session[SESSION_CREATED_AT_KEY] = iso
    flask_session[SESSION_LAST_ACTIVITY_KEY] = iso
    flask_session[FRESH_LOGIN_AT_KEY] = iso


def stamp_reauth_session(now: datetime) -> None:
    """Stamp activity and fresh-login on a successful ``/reauth``.

    The session continues -- ``_session_created_at`` deliberately
    stays at its original value so any earlier parallel session that
    pre-dates a future :func:`invalidate_other_sessions` call still
    gets caught.  Writing a fresh ``_session_created_at`` here would
    make ``/reauth`` a covert "promote my session past every prior
    invalidation point" button.

    Use only on the ``/reauth`` success branch (commit C-10).  A sign-in
    (login, MFA verify) calls :func:`stamp_login_session` instead, and a
    password change :func:`stamp_password_change`.

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


def stamp_session_refresh(now: datetime) -> None:
    """Refresh ``_session_created_at`` after a no-re-auth invalidation.

    Use on the ``/invalidate-sessions`` route, which bumps the user's
    ``session_invalidated_at`` so every parallel session is rejected
    on its next request.  The current session needs its
    ``_session_created_at`` advanced past the new invalidation point
    so it survives -- the same trick :func:`invalidate_other_sessions`
    uses internally -- but ``_fresh_login_at`` deliberately stays
    untouched: the user did not actually re-authenticate, only
    pressed a "log everyone else out" button while already signed in.
    Updating fresh-login here would let the same UI extend the
    high-value-operation grace window indefinitely without ever
    re-typing a password.

    ``_session_last_activity_at`` is also refreshed because the user
    just performed an authenticated action; the ``before_request``
    hook would do the same on the very next request anyway, but
    writing it here keeps every cookie write atomic with the DB
    write that motivated it.

    Args:
        now: Single timezone-aware UTC datetime to write into both
            updated keys.

    Raises:
        RuntimeError: No active Flask request context.
    """
    iso = now.isoformat()
    flask_session[SESSION_CREATED_AT_KEY] = iso
    flask_session[SESSION_LAST_ACTIVITY_KEY] = iso


def invalidate_other_sessions(user: User, reason: str) -> None:
    """Invalidate every session for ``user`` except the one on this request.

    Stamps ``user.session_invalidated_at`` to ``datetime.now(UTC)``,
    commits, then refreshes the request's session cookie so this
    session survives the bump.  See module docstring for the
    cookie-vs-DB comparison the bump targets.

    Args:
        user: The :class:`~app.models.user.User` whose other sessions
            must be terminated.  Must be a persistent ORM instance --
            the helper writes through SQLAlchemy and commits the
            current session.  Must also be the same identity
            authenticated on the current request, or the wrong cookie
            will be refreshed (see module docstring).
        reason: Short machine-readable label for the audit log, as its
            one caller's ``"backup_code_consumed"``.  Required, non-empty
            -- a blank reason defeats the audit trail this helper exists
            to produce.

    Raises:
        ValueError: ``user`` is None, or ``reason`` is empty/None.
            Raised before any DB or session mutation so the caller's
            state is unchanged on misuse.
        RuntimeError: No active Flask request context (raised by
            ``flask.session`` on access).
        sqlalchemy.exc.SQLAlchemyError: The commit of
            ``session_invalidated_at`` failed.  The session cookie is
            NOT updated in this case -- the existing cookie remains
            valid against the un-bumped DB column, which is the safe
            default (no surprise logouts on transient DB errors).

    Side effects:
        * Writes ``user.session_invalidated_at`` and commits.
        * Updates ``flask.session[SESSION_CREATED_AT_KEY]``.  Does NOT
          touch ``SESSION_LAST_ACTIVITY_KEY`` or ``FRESH_LOGIN_AT_KEY``
          -- those are owned by :func:`stamp_login_session` /
          :func:`stamp_reauth_session` and are the caller's
          responsibility when the invalidation happens alongside a
          re-auth event (e.g. the backup-code branch of
          ``/mfa/verify`` already calls ``stamp_login_session`` before
          this helper, so no second write is needed here).
        * Emits a structured ``other_sessions_invalidated`` audit log
          event tagged with ``reason`` and ``user_id``.
    """
    if user is None:
        raise ValueError(
            "invalidate_other_sessions requires a User instance; got None"
        )
    if not reason:
        raise ValueError(
            "invalidate_other_sessions requires a non-empty reason "
            f"for the audit log; got {reason!r}"
        )

    # Single ``now`` so the DB column and the cookie carry the
    # identical timestamp.  load_user compares ``cookie < db`` with
    # strict less-than, so equality lets the current session survive
    # while every earlier session is rejected.  Reusing one value
    # also avoids a 2nd datetime.now() call drifting microseconds
    # into the future and accidentally invalidating this session if
    # the comparison ever changes to <=.
    now = datetime.now(timezone.utc)
    user.session_invalidated_at = now
    db.session.commit()

    flask_session[SESSION_CREATED_AT_KEY] = now.isoformat()

    log_event(
        logger, logging.INFO, EVT_OTHER_SESSIONS_INVALIDATED, AUTH,
        "Other sessions invalidated",
        user_id=user.id, reason=reason,
    )
