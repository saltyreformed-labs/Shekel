"""
Shekel Budget App -- Trusted Device Service

A trusted device skips only the two-factor code, never the password (plan#2173,
ruling plan#2406).  This module owns the record's four acts: trust a device,
find the live device a browser's token names, list a user's live devices, and
forget one.

**When a device is live is stated once**, in :func:`_live`: trusted less than
:data:`~app.models.trusted_device.TRUST_LIFETIME` ago, and not before its
user's ``session_invalidated_at``.  The lookup and the list read it, and the
purge reads its negation, so the three can never disagree about which devices
are trusted.  Revocation is that comparison and nothing else: every writer of
``session_invalidated_at`` forgets every trusted device of that user without a
write here.  "Not before" is the session rule's own boundary
(``app.utils.session_helpers``): a device trusted at the very instant of a bump
survives it, as a session created at that instant does.

**The clock is the process's**, ``datetime.now(timezone.utc)``, read once per
act: the same clock the sign-in stamps and most ``session_invalidated_at``
writers use.  Two writers (the owner's companion password reset and
deactivation, ``app/routes/settings.py``) use the database's ``now()``; on one
host the two clocks are one.

Architecture:
  - No Flask imports.  Takes plain ids and strings, returns rows or the token.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, and_, delete, or_, select

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.trusted_device import USER_AGENT_MAX_LENGTH, TrustedDevice
from app.models.user import User


#: Bytes of randomness in a device token: 256 bits (ruling plan#2406).
_TOKEN_BYTES = 32


def _token_hash(token: str) -> str:
    """Return the stored form of *token*: its SHA-256 as lowercase hex.

    No salt and no stretching, because the token is 256 random bits: there is
    no dictionary to search, so a fast hash loses nothing a slow one would
    keep, and it lets the lookup find the row by an index.

    Args:
        token: The browser's token, as the cookie carries it.

    Returns:
        Sixty-four lowercase hex digits.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _live(now: datetime) -> ColumnElement[bool]:
    """Return the ONE statement of when a trusted device is live, as SQL.

    Live while :attr:`~app.models.trusted_device.TrustedDevice.expires_at` is
    strictly later than *now* (so a device thirty days old to the second is
    dead), AND its user's ``session_invalidated_at`` is NULL or not later than
    the device's ``trusted_at``.  The user's column is read by a correlated
    subquery rather than a join, so the clause stands alone: it filters a
    SELECT and, negated, a DELETE, and the negation is exact because neither
    arm can be NULL (``trusted_at`` is NOT NULL, and the NULL invalidation is
    answered by its own arm).

    Args:
        now: The moment to judge liveness at.

    Returns:
        A boolean clause over ``auth.trusted_devices``.
    """
    invalidated_at = (
        select(User.session_invalidated_at)
        .where(User.id == TrustedDevice.user_id)
        .scalar_subquery()
    )
    return and_(
        TrustedDevice.expires_at > now,
        or_(
            invalidated_at.is_(None),
            TrustedDevice.trusted_at >= invalidated_at,
        ),
    )


def trust_device(user_id: int, user_agent: str) -> str:
    """Trust the browser a user just typed a two-factor code on.

    Writes one row holding the hash of a fresh 256-bit token, and in the same
    write deletes the user's dead rows (expired, or forgotten by a
    ``session_invalidated_at`` bump), so a user's devices never outgrow the
    ones they have trusted in the last thirty days.  Another user's dead rows
    are theirs to purge.

    Args:
        user_id: The ``auth.users.id`` who typed the code.
        user_agent: The browser's User-Agent header, ``""`` when it sent none.
            Kept to name the device in settings and cut to
            :data:`~app.models.trusted_device.USER_AGENT_MAX_LENGTH`.

    Returns:
        The token, for the browser's cookie.  This is the only place it
        exists: the row holds its hash.
    """
    now = datetime.now(timezone.utc)
    db.session.execute(
        delete(TrustedDevice).where(
            TrustedDevice.user_id == user_id, ~_live(now),
        )
    )
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    db.session.add(TrustedDevice(
        user_id=user_id,
        token_hash=_token_hash(token),
        user_agent=user_agent[:USER_AGENT_MAX_LENGTH],
        trusted_at=now,
    ))
    db.session.flush()
    return token


def find_live_device(user_id: int, token: str) -> TrustedDevice | None:
    """Return the live device *token* names for *user_id*, or ``None``.

    ``None`` alike for a token no device holds, a device of another user and a
    dead device, so a caller cannot tell them apart and neither can a browser.

    Args:
        user_id: The ``auth.users.id`` signing in.
        token: The token the browser's cookie carries.

    Returns:
        The live :class:`~app.models.trusted_device.TrustedDevice`, or ``None``.
    """
    now = datetime.now(timezone.utc)
    return db.session.execute(
        select(TrustedDevice).where(
            TrustedDevice.token_hash == _token_hash(token),
            TrustedDevice.user_id == user_id,
            _live(now),
        )
    ).scalar_one_or_none()


def list_live_devices(user_id: int) -> list[TrustedDevice]:
    """Return *user_id*'s live devices, most recently trusted first.

    Args:
        user_id: The ``auth.users.id`` whose devices to list.

    Returns:
        The live :class:`~app.models.trusted_device.TrustedDevice` rows,
        ordered by ``trusted_at`` descending, then ``id`` descending so two
        trusted in one instant still list in one order.
    """
    now = datetime.now(timezone.utc)
    return list(db.session.execute(
        select(TrustedDevice)
        .where(TrustedDevice.user_id == user_id, _live(now))
        .order_by(TrustedDevice.trusted_at.desc(), TrustedDevice.id.desc())
    ).scalars())


def forget_device(user_id: int, device_id: int) -> None:
    """Forget one of *user_id*'s devices: its row is deleted.

    A device that is already dead is still the user's to forget, so liveness
    is not asked: a device the user saw in settings a moment ago and which
    expired since is deleted, not refused.

    Args:
        user_id: The ``auth.users.id`` forgetting the device.
        device_id: The ``auth.trusted_devices.id`` to forget.

    Raises:
        NotFoundError: No device *device_id* belongs to *user_id*.  One
            refusal for "no such device" and "another user's device", so the
            answer is no oracle for which ids exist.
    """
    device = db.session.execute(
        select(TrustedDevice).where(
            TrustedDevice.id == device_id,
            TrustedDevice.user_id == user_id,
        )
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError(
            f"Trusted device {device_id} not found for user {user_id}."
        )
    db.session.delete(device)
    db.session.flush()
