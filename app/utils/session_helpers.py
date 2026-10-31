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

**A session missing either of the first two stamps is not signed in**
(plan#2173, finding S-02 of the 2026-10-29 security review):
``load_user`` refuses one without ``_session_created_at``, and reads one
without ``_session_last_activity_at`` as stale.  Every sign-in writes both
in the request that calls ``login_user`` (:func:`stamp_login_session`, at
both of the app's ``login_user`` sites), so a session without them is one
something else started.  What the rule exists to refuse is Flask-Login's
remember-me restore, which calls the loader on an EMPTY session.  A
"Remember me" cookie is the same ``<id>|HMAC(SECRET_KEY, id)`` value for
every remembered sign-in, and Flask-Login honours it whether or not the
app ever issues one again, so until this rule a copied one signed a
browser in past every "log out all sessions".  The rule refuses every
such cookie ever issued, with no ``SECRET_KEY`` rotation (ruling
plan#2406, which also deleted the checkbox).

The three keys are deliberately distinct.  An idle-but-not-stale
session can still complete a benign GET (``_session_last_activity_at``
keeps refreshing as the user clicks around), but a high-value POST
will demand a re-auth even on an actively-used session if more than
``FRESH_LOGIN_MAX_AGE_MINUTES`` have passed since the last password
entry.

Stamping helpers
----------------

The three ``stamp_*`` helpers below own the only writes to those
keys outside ``load_user`` / the ``before_request`` hook.  Each maps
to a single semantic event so the caller cannot accidentally write
the wrong subset:

  * :func:`stamp_login_session` -- new login (password POST,
    successful MFA verify, password change).  Writes all three keys,
    the set :func:`login_session_stamps` names.

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
    motivates the invalidation (password hash update, MFA disable,
    backup-code list mutation).  The helper commits its own write
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


def login_session_stamps(now: datetime) -> dict[str, str]:
    """Return the lifecycle stamps a sign-in writes, keyed by session key.

    The one statement of WHICH keys a new sign-in carries:
    :func:`stamp_login_session` writes exactly these, and a script that
    builds a signed-in session by hand (``tests/manual``) writes them too,
    so ``load_user`` cannot tell the two apart and a key added here
    reaches both.

    Args:
        now: Single timezone-aware UTC datetime for all three keys.

    Returns:
        ``{key: iso_timestamp}`` for ``_session_created_at``,
        ``_session_last_activity_at`` and ``_fresh_login_at``.
    """
    iso = now.isoformat()
    return {
        SESSION_CREATED_AT_KEY: iso,
        SESSION_LAST_ACTIVITY_KEY: iso,
        FRESH_LOGIN_AT_KEY: iso,
    }


def stamp_login_session(now: datetime) -> None:
    """Stamp every lifecycle timestamp on a freshly authenticated session.

    Use at every call site that completes a primary credential check:
    the ``/login`` POST when no MFA is configured, the ``/mfa/verify``
    POST that consumes the second factor, and ``change_password``
    (which re-authenticates via the user's current password).  All
    three keys share a single ``now`` value so a later subtraction
    against ``datetime.now(timezone.utc)`` cannot accidentally
    distinguish them.

    Args:
        now: Single timezone-aware UTC datetime to write into all
            three keys.  Reusing one value (rather than calling
            :func:`datetime.now` three times) avoids microsecond
            drift between the writes that an audit comparison would
            see as clock skew.

    Raises:
        RuntimeError: No active Flask request context (raised by
            ``flask.session`` on access).  The caller is always a
            route handler, so this would only surface if the helper
            were called from a CLI command or a background job by
            mistake.
    """
    flask_session.update(login_session_stamps(now))


def stamp_reauth_session(now: datetime) -> None:
    """Stamp activity and fresh-login on a successful ``/reauth``.

    The session continues -- ``_session_created_at`` deliberately
    stays at its original value so any earlier parallel session that
    pre-dates a future :func:`invalidate_other_sessions` call still
    gets caught.  Writing a fresh ``_session_created_at`` here would
    make ``/reauth`` a covert "promote my session past every prior
    invalidation point" button.

    Use only on the ``/reauth`` success branch (commit C-10).  Other
    re-auth events (login, MFA verify, password change) all establish
    a new session and should call :func:`stamp_login_session`.

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
        reason: Short machine-readable label for the audit log
            (``"password_change"``, ``"mfa_disabled"``,
            ``"backup_code_consumed"``).  Required, non-empty -- a
            blank reason defeats the audit trail this helper exists
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
