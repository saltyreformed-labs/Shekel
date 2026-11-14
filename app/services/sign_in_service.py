"""
Shekel Budget App -- Sign-in Service

One finished sign-in, one record (ruling plan#3925).  The session cookie is
signed but the server used to keep nothing of it, so signing out could only
edit the cookie the browser sent back.  Now each finished sign-in writes a row
(:class:`~app.models.sign_in.SignIn`), the session carries the row's token,
and a session is live only while its token names a live row of its user, so
deleting the row ends the session and every copy of its cookie with it.

This module owns the record's four acts: record a finished sign-in, answer
whether a session's token names a live one, end one, and end every one of a
user (:func:`sign_out_everywhere`).

**A session is live exactly when its row is** (ruling plan#3927).  Signing out
deletes one row; every act that signs a user out everywhere deletes all of
theirs through :func:`sign_out_everywhere`, the one act, which also ends every
trust of theirs.  So nothing in the cookie but the token decides whether a
session is signed in, and nothing on the account does.

**When a row is live is stated once**, in :func:`_live`: its sign-in is less
than :data:`~app.models.known_device.KNOWN_LIFETIME` ago (ruling plan#3069), at
the boundary the browser's record keeps
(``app.services.known_device_service._known``), so a sign-in thirty days old to
the second is dead.  The sign-in's instant is its caller's, handed to
:func:`record_sign_in`, because the browser's record is renewed from the same
instant: a session then ends when its browser stops being known.

Architecture:
  - No Flask imports.  Takes plain ids, tokens and instants; returns tokens
    and booleans.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, delete, exists, or_, select, update

from app.extensions import db
from app.models.known_device import KNOWN_LIFETIME
from app.models.sign_in import SignIn
from app.models.user import User
from app.utils.bearer_tokens import new_token, token_hash


def _live(now: datetime) -> ColumnElement[bool]:
    """Return the ONE statement of when a sign-in is live, as SQL.

    Its sign-in is strictly after *now* less
    :data:`~app.models.known_device.KNOWN_LIFETIME`, so one thirty days old
    to the second is dead.  The bound is computed in Python, where subtracting
    a ``timedelta`` is exact, as ``known_device_service._known`` does.  The
    negation is exact, because ``signed_in_at`` is NOT NULL.

    Args:
        now: The moment to judge at.

    Returns:
        A boolean clause over ``auth.sign_ins``.
    """
    return SignIn.signed_in_at > now - KNOWN_LIFETIME


def record_sign_in(user_id: int, old_token: str | None, *, now: datetime) -> str:
    """Record a finished sign-in of *user_id* and return its new token.

    In the same write this deletes the row the browser's previous session
    token names, whoever's, since the browser's session is being replaced (a
    copy of that old cookie still in use elsewhere dies with it), and the
    user's rows past their lifetime, so a user's rows never outgrow the
    sign-ins of the last thirty days.  Another user's dead rows are theirs to
    purge at their own next sign-in.

    Args:
        user_id: The ``auth.users.id`` who finished signing in.
        old_token: The session token this browser's cookie carried from an
            earlier sign-in, or ``None``.
        now: The sign-in's instant: the row's ``signed_in_at`` and the moment
            the purge is judged at.  Required, with no default reading the
            clock, because the browser's record is renewed from this same
            instant (ruling plan#3069).

    Returns:
        The new token, for the session.  This is the only place it exists:
        the row holds its hash.
    """
    ended = and_(SignIn.user_id == user_id, ~_live(now))
    if old_token is not None:
        ended = or_(ended, SignIn.token_hash == token_hash(old_token))
    db.session.execute(delete(SignIn).where(ended))
    token = new_token()
    db.session.add(SignIn(
        user_id=user_id, token_hash=token_hash(token), signed_in_at=now,
    ))
    db.session.flush()
    return token


def is_live(user_id: int, token: str | None, now: datetime) -> bool:
    """Return whether *token* names a live sign-in of *user_id* at *now*.

    One statement, loading no row.  False alike for no token, a token no row
    holds (signed out, or never issued), another user's token and a sign-in
    past its lifetime, so no caller can tell them apart.

    Args:
        user_id: The ``auth.users.id`` the session is loaded for.
        token: The session's token, or ``None`` when it carries none.
        now: The moment to judge at.

    Returns:
        True when the row exists, is *user_id*'s and is live (:func:`_live`).
        No statement is sent when *token* is ``None``.
    """
    if token is None:
        return False
    return db.session.execute(select(exists().where(
        SignIn.user_id == user_id,
        SignIn.token_hash == token_hash(token),
        _live(now),
    ))).scalar_one()


def end_sign_in(token: str | None) -> None:
    """End the sign-in *token* names: delete its row.

    A session whose token names no row is refused at its next request, so
    every copy of its cookie stops working.  A token no row holds deletes
    nothing, so signing out twice is signing out once.

    Args:
        token: The session's token, or ``None`` when it carries none, which
            sends no statement.
    """
    if token is None:
        return
    db.session.execute(delete(SignIn).where(SignIn.token_hash == token_hash(token)))


def sign_out_everywhere(
    user: User, *, keeping: str | None, now: datetime,
) -> str | None:
    """End every sign-in of *user* and every trust of theirs, from *now*.

    The ONE act of every sign-out-everywhere (ruling plan#3927), so none can
    end sessions without ending trusts, or the reverse: "Log out all other
    sessions" and Change password, which keep the browser that pressed them;
    a backup-code sign-in, which runs it before the sign-in it finishes; and,
    keeping nothing, the owner's change of a companion's password, her
    deactivation, a reset of two-factor and ``scripts/rotate_sessions.py``.

    Every row of *user*'s sign-ins is deleted but the one *keeping* names, so
    each session whose token named one is refused at its next request.  The
    kept sign-in goes on under a new token (ruling plan#4010): its row's hash
    is replaced and its ``signed_in_at`` is not, so its thirty days run on
    from its sign-in (ruling plan#3069), and every copy of the pressing
    browser's cookie, which still carries the old token, is refused with the
    rest.

    Every trust ends by the user's ``session_invalidated_at`` moving to *now*:
    a device is trusted only while it was trusted at or after it
    (``app.services.known_device_service._trusted``).  The browsers stay
    known, which no act a person presses ends (ruling plan#2997).

    Args:
        user: The persistent user to sign out.
        keeping: The token of the sign-in that stays, the pressing browser's
            session's, or ``None`` to end every one.
        now: The act's instant, which the trusts end at.  Required, with no
            default reading the clock: a password change writes its
            security event from the same instant, and the rotation script
            signs every user out from one.

    Returns:
        The kept sign-in's new token, for the pressing browser's session, or
        ``None`` when *keeping* is ``None``.  A *keeping* that names no row of
        *user*'s (a sign-out of this browser's cookie, or a copy of it, ended
        it while this act ran) renews nothing, and its new token names no row,
        so that browser is signed out like the rest.
    """
    ended = SignIn.user_id == user.id
    if keeping is not None:
        ended = and_(ended, SignIn.token_hash != token_hash(keeping))
    db.session.execute(delete(SignIn).where(ended))
    user.session_invalidated_at = now
    token = None
    if keeping is not None:
        token = new_token()
        db.session.execute(
            update(SignIn)
            .where(SignIn.user_id == user.id, SignIn.token_hash == token_hash(keeping))
            .values(token_hash=token_hash(token))
        )
    db.session.flush()
    return token
