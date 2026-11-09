"""
Shekel Budget App -- Known Device Service

One browser, one record, one cookie (plan#2772, ruling plan#2994).  A finished
sign-in writes the record; a trust is one fact on it, and skips only the
two-factor code, never the password (plan#2173, ruling plan#2406).  This module
owns the record's acts: read what a browser's token is worth (or, before any
lock, only whether it names a known browser), record a finished sign-in or
re-confirm (each gives the browser a new token and clears its count), count a
wrong guess, list a user's trusted devices, and forget one.
Beside them, :func:`device_named_by` picks a browser's device out of a list
already read, so the token's hash is spelled in this module alone.

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
writers use.  A finished sign-in's instant is its caller's, handed to
:func:`record_sign_in`, because the session's sign-in stamp is written from
the same instant (ruling plan#3069).  Two writers (the owner's companion password reset and
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


def _the_known_browser(now: datetime, user_id: int, token: str) -> ColumnElement[bool]:
    """Return the ONE statement of which record *token* names for *user_id*, as SQL.

    The record holding *token*'s hash, of *user_id*, and known at *now*
    (:func:`_known`): so a token no record holds, another user's token and a
    browser past its lifetime all match nothing.

    Args:
        now: The moment to judge at.
        user_id: The ``auth.users.id`` signing in.
        token: The token the browser's cookie carries.

    Returns:
        A boolean clause over ``auth.known_devices``.
    """
    return and_(
        KnownDevice.token_hash == _token_hash(token),
        KnownDevice.user_id == user_id,
        _known(now),
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
            _the_known_browser(now, user_id, token),
        )
    ).one_or_none()
    if row is None:
        return None
    return BrowserStanding(device=row[0], trusted=row[1] is True)


def is_known(user_id: int, token: str | None) -> bool:
    """Return whether *token* names a browser known to *user_id*, loading no record.

    ``/login``'s question before it takes any lock: a known browser checks
    its password under the owner's lock, and a stranger checks with none held
    (ruling plan#3110), so a made-up cookie never makes a stranger take the
    lock.  The answer is a bool, never the record: the record the sign-in
    acts on is read under the lock, by :func:`find_browser`, because one read
    before it holds the count as it stood before the lock, and a guess
    counted on that copy would write back over one counted meanwhile.

    Args:
        user_id: The ``auth.users.id`` signing in.
        token: The token the browser's cookie carries, or ``None`` when it
            sent none.

    Returns:
        True when :func:`find_browser` would, at this moment, answer a
        standing.
    """
    if token is None:
        return False
    now = datetime.now(timezone.utc)
    return db.session.execute(
        select(KnownDevice.id).where(_the_known_browser(now, user_id, token))
    ).first() is not None


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
    user_id: int, token: str | None, user_agent: str,
    *, now: datetime, trust: bool = False,
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
        now: The sign-in's instant: the record's ``last_signed_in_at``, its
            ``trusted_at`` when *trust*, and the moment the standing and the
            purge are judged at.  Required, with no default reading the
            clock, because the session's sign-in stamp must be written from
            this same instant: one a moment later would outlive the record
            by the gap (ruling plan#3069).
        trust: Whether the user typed a code and asked to be trusted.

    Returns:
        The new token, for the browser's cookie.  This is the only place it
        exists: the row holds its hash.
    """
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
    device.user_agent = user_agent[:USER_AGENT_MAX_LENGTH]
    device.last_signed_in_at = now
    if trust:
        device.trusted_at = now
    return _renew(device)


def record_re_confirm(user_id: int, token: str | None) -> str | None:
    """Record a finished re-confirm on this browser, and return its new token.

    The browser's record keeps its row, its last sign-in and any trust, with a
    new token and no wrong guesses counted (ruling plan#3070): so a stolen copy
    of the cookie stops working the moment the owner re-confirms, as it does at
    their next sign-in.  The thirty days do not move: a browser stays known
    for thirty days from its last sign-in (ruling plan#2995), the session ends
    thirty days after its sign-in (ruling plan#3069), and a re-confirm is not
    a sign-in.

    The row is read here, not handed in, for the reason :func:`record_sign_in`
    gives: with two-factor, the code step's commit has ended the transaction
    that read it, and the guesses of a copy of this cookie may have deleted it
    since.  Under the owner's lock taken for THIS transaction, a row deleted
    since is simply no longer found.

    Args:
        user_id: The ``auth.users.id`` who finished re-confirming.
        token: The token this browser's cookie carries, or ``None``.

    Returns:
        The new token, for the browser's cookie, or ``None`` when *token*
        names no live record of *user_id*: the browser is a stranger now, and
        there is no record to renew.
    """
    standing = find_browser(user_id, token)
    if standing is None:
        return None
    return _renew(standing.device)


def _renew(device: KnownDevice) -> str:
    """Give *device* a new token and clear its count; return the token.

    What a finished sign-in and a finished re-confirm both do to a browser's
    record, stated once (ruling plan#3070): the old token names nothing from
    here on, and the browser's row of wrong guesses is broken (ruling
    plan#2996).

    Args:
        device: The record to renew, read under the owner's lock in this
            transaction, or new in it.

    Returns:
        The new token.  This is the only place it exists: the row holds its
        hash.
    """
    new_token = secrets.token_urlsafe(_TOKEN_BYTES)
    device.token_hash = _token_hash(new_token)
    device.failed_login_count = 0
    db.session.flush()
    return new_token


def count_failed_attempt(device: KnownDevice, threshold: int) -> bool:
    """Count one wrong guess on *device*, deleting the record at *threshold*.

    The browser's own count, never its account's: a guess counted here cannot
    lock the account, and leaves the account's count as it was.  Reaching
    *threshold* deletes the record, so the browser is a stranger again and
    every other browser of the user is untouched (ruling plan#2996): its
    cookie then names nothing, and :func:`find_browser` answers ``None`` for a
    token no record holds.  The count runs "in a row" because only a finished
    sign-in (:func:`record_sign_in`) or re-confirm (:func:`record_re_confirm`)
    clears it.

    Args:
        device: The known browser's record, as :func:`find_browser` read it in
            this transaction.
        threshold: How many wrong guesses in a row delete the record.  The
            account lock's own threshold, which the caller reads, so the two
            are one number.

    Returns:
        True when this guess deleted the record.
    """
    device.failed_login_count += 1
    deleted = device.failed_login_count >= threshold
    if deleted:
        db.session.delete(device)
    db.session.flush()
    return deleted


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


def forget_trust(user_id: int, device_id: int) -> None:
    """Forget *user_id*'s trust of a device; the browser stays known.

    Only the trust goes, by ruling plan#2997, because forgetting takes no
    password: if it made the browser a stranger, a thief with a live session
    could press it, lock the account with wrong passwords, and keep the owner
    out with no way to throw them out.  The next sign-in there asks for the
    code again, which is all the button says it does.  A device whose trust has
    already died is still the user's to forget, so liveness is not asked: one
    the user saw in settings a moment ago and which expired since is cleared,
    not refused.

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
    device.trusted_at = None
    db.session.flush()
