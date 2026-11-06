"""
Shekel Budget App -- Known Device Service

One browser, one record, one cookie (plan#2772, ruling plan#2994).  A finished
sign-in writes the record; a trust is one fact on it, and skips only the
two-factor code, never the password (plan#2173, ruling plan#2406).  This module
owns the record's acts: read what a browser's token is worth, record a finished
sign-in, list a user's trusted devices, and forget one.  Beside them,
:func:`device_named_by` picks a browser's device out of a list already read, so
the token's hash is spelled in this module alone.

**Each standing is stated once**: :func:`_known`, a finished sign-in less than
:data:`~app.models.known_device.KNOWN_LIFETIME` ago, and :func:`_trusted`, a
trust less than :data:`~app.models.known_device.TRUST_LIFETIME` old and not
before its user's ``session_invalidated_at``.  Ending a trust is that comparison
and nothing else: every writer of ``session_invalidated_at`` ends every trust of
that user without a write here.  "Not before" is the session rule's own boundary
(``app.utils.session_helpers``): a device trusted at the very instant of a bump
survives it, as a session created at that instant does.  Being known reads no
bump, by ruling plan#2997, so that none of those acts can leave the owner's
browsers refused by a lock a stranger holds shut.

**The clock is the process's**, ``datetime.now(timezone.utc)``, read once per
act: the same clock the sign-in stamps and most ``session_invalidated_at``
writers use.  Two writers (the owner's companion password reset and
deactivation, ``app/routes/settings.py``) use the database's ``now()``, which
is the moment their transaction BEGAN, not the moment it commits: a device
trusted while such a request runs is not before the bump and survives it, as a
session started then does.  On one host the database's clock and the
process's are one clock.

Architecture:
  - No Flask imports.  Takes plain ids and strings, returns rows or the token.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import ColumnElement, and_, delete, or_, select

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.known_device import (
    KNOWN_LIFETIME,
    TRUST_LIFETIME,
    USER_AGENT_MAX_LENGTH,
    KnownDevice,
)
from app.models.user import User


#: Bytes of randomness in a device token: 256 bits (ruling plan#2406).
_TOKEN_BYTES = 32


class BrowserStanding(NamedTuple):
    """What a browser's token is worth for one user, judged at one moment.

    Attributes:
        device: The known browser's row.
        trusted: Whether it skips the two-factor code.
    """

    device: KnownDevice
    trusted: bool


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


def _known(now: datetime) -> ColumnElement[bool]:
    """Return the ONE statement of when a browser is known, as SQL.

    Its last finished sign-in is strictly after *now* less
    :data:`~app.models.known_device.KNOWN_LIFETIME`, so a browser thirty days
    old to the second is a stranger.  The bound is computed here, in Python,
    for the reason :func:`_trusted` gives.  The negation is exact, because
    ``last_signed_in_at`` is NOT NULL.

    Args:
        now: The moment to judge at.

    Returns:
        A boolean clause over ``auth.known_devices``.
    """
    return KnownDevice.last_signed_in_at > now - KNOWN_LIFETIME


def _trusted(now: datetime) -> ColumnElement[bool]:
    """Return the ONE statement of when a device is trusted, as SQL.

    Trusted while it was trusted strictly after *now* less
    :data:`~app.models.known_device.TRUST_LIFETIME` (so a device thirty days
    old to the second is dead), AND its user's ``session_invalidated_at`` is
    NULL or not later than the device's ``trusted_at``.  The age bound is
    computed here, in Python, where subtracting a ``timedelta`` is exact;
    adding the lifetime as an SQL interval would count calendar days in the
    database session's TimeZone instead (the model's ``trust_expires_at`` says
    more).  The user's column is read by a correlated subquery rather than a
    join, so the clause stands alone: it filters a SELECT and it is a column of
    one.  A device no one trusted has a NULL ``trusted_at``, which this reads
    as NULL, not false: a filter drops it, and :func:`_standing_at` reads the
    column as ``is True``.

    Args:
        now: The moment to judge at.

    Returns:
        A boolean clause over ``auth.known_devices``.
    """
    invalidated_at = (
        select(User.session_invalidated_at)
        .where(User.id == KnownDevice.user_id)
        .scalar_subquery()
    )
    return and_(
        KnownDevice.trusted_at > now - TRUST_LIFETIME,
        or_(
            invalidated_at.is_(None),
            KnownDevice.trusted_at >= invalidated_at,
        ),
    )


def _standing_at(
    now: datetime, user_id: int, token: str | None,
) -> BrowserStanding | None:
    """Return what *token* is worth for *user_id* at *now*, or ``None``.

    Args:
        now: The moment to judge at.
        user_id: The ``auth.users.id`` signing in.
        token: The token the browser's cookie carries, or ``None``.

    Returns:
        The browser's :class:`BrowserStanding`, or ``None`` for a stranger.
    """
    if token is None:
        return None
    row = db.session.execute(
        select(KnownDevice, _trusted(now)).where(
            KnownDevice.token_hash == _token_hash(token),
            KnownDevice.user_id == user_id,
            _known(now),
        )
    ).one_or_none()
    if row is None:
        return None
    return BrowserStanding(device=row[0], trusted=row[1] is True)


def find_browser(user_id: int, token: str | None) -> BrowserStanding | None:
    """Return what *token* is worth for *user_id*, or ``None`` for a stranger.

    ``None`` alike for no token, a token no device holds, a device of another
    user and a device past its lifetime, so a caller cannot tell them apart and
    neither can a browser.  Both standings are read by the one statement, so
    they are judged at one moment.

    Args:
        user_id: The ``auth.users.id`` signing in.
        token: The token the browser's cookie carries, or ``None`` when it
            sent none.

    Returns:
        The browser's :class:`BrowserStanding`, or ``None``.
    """
    return _standing_at(datetime.now(timezone.utc), user_id, token)


def record_sign_in(
    user_id: int, token: str | None, user_agent: str, *, trust: bool = False,
) -> str:
    """Record a finished sign-in on this browser, and return its new token.

    A browser whose *token* names its live record keeps that row, with a new
    token, a new last sign-in and no wrong passwords counted; any other browser
    gets a new row.  So a stolen copy of a cookie stops working at the real
    browser's next sign-in.  The token changes but a trust does not: the code
    was typed when it was typed, and ruling plan#2406 counts its thirty days
    from then, so only *trust* moves ``trusted_at``.

    The row is read here, not handed in, because a code step's commit has ended
    the transaction that read it: under the owner's lock taken for THIS
    transaction, a row another request deleted since is simply no longer found.

    In the same write this deletes the user's rows past their lifetime, so a
    user's rows never outgrow the browsers they have signed in on in the last
    thirty days.  Another user's dead rows are theirs to purge.

    Args:
        user_id: The ``auth.users.id`` who finished signing in.
        token: The token this browser's cookie carries, or ``None``.
        user_agent: The browser's User-Agent header, ``""`` when it sent none.
            Kept to name the device in settings and cut to
            :data:`~app.models.known_device.USER_AGENT_MAX_LENGTH`.
        trust: Whether the user typed a code and asked to be trusted.

    Returns:
        The new token, for the browser's cookie.  This is the only place it
        exists: the row holds its hash.
    """
    now = datetime.now(timezone.utc)
    db.session.execute(
        delete(KnownDevice).where(
            KnownDevice.user_id == user_id, ~_known(now),
        )
    )
    standing = _standing_at(now, user_id, token)
    if standing is None:
        device = KnownDevice(user_id=user_id)
        db.session.add(device)
    else:
        device = standing.device
    new_token = secrets.token_urlsafe(_TOKEN_BYTES)
    device.token_hash = _token_hash(new_token)
    device.user_agent = user_agent[:USER_AGENT_MAX_LENGTH]
    device.last_signed_in_at = now
    device.failed_login_count = 0
    if trust:
        device.trusted_at = now
    db.session.flush()
    return new_token


def list_trusted_devices(user_id: int) -> list[KnownDevice]:
    """Return *user_id*'s trusted devices, most recently trusted first.

    Args:
        user_id: The ``auth.users.id`` whose devices to list.

    Returns:
        The trusted :class:`~app.models.known_device.KnownDevice` rows, ordered
        by ``trusted_at`` descending, then ``id`` descending so two trusted in
        one instant still list in one order.
    """
    now = datetime.now(timezone.utc)
    return list(db.session.execute(
        select(KnownDevice)
        .where(KnownDevice.user_id == user_id, _trusted(now))
        .order_by(KnownDevice.trusted_at.desc(), KnownDevice.id.desc())
    ).scalars())


def device_named_by(
    devices: list[KnownDevice], token: str | None,
) -> KnownDevice | None:
    """Return the device in *devices* that *token* names, or ``None``.

    Settings marks the browser it is rendered on among the devices it lists.
    It asks this of the list it already holds rather than looking the token up
    again, so the mark and the list are judged at one moment: a second query
    could find the device dead an instant after the list found it live.

    Args:
        devices: Devices already read, as :func:`list_trusted_devices` returns
            them.
        token: The token this browser's cookie carries, or ``None`` when it
            sent none.

    Returns:
        The one device whose stored hash is *token*'s, or ``None``.
    """
    if token is None:
        return None
    token_hash = _token_hash(token)
    return next(
        (device for device in devices if device.token_hash == token_hash),
        None,
    )


def forget_device(user_id: int, device_id: int) -> None:
    """Forget one of *user_id*'s devices: its row is deleted.

    A device that is already dead is still the user's to forget, so liveness
    is not asked: a device the user saw in settings a moment ago and which
    expired since is deleted, not refused.

    Args:
        user_id: The ``auth.users.id`` forgetting the device.
        device_id: The ``auth.known_devices.id`` to forget.

    Raises:
        NotFoundError: No device *device_id* belongs to *user_id*.  One
            refusal for "no such device" and "another user's device", so the
            answer is no oracle for which ids exist.
    """
    device = db.session.execute(
        select(KnownDevice).where(
            KnownDevice.id == device_id,
            KnownDevice.user_id == user_id,
        )
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError(
            f"Trusted device {device_id} not found for user {user_id}."
        )
    db.session.delete(device)
    db.session.flush()
