"""
Shekel Budget App -- Guess Record Service

A stranger's wrong guesses count on a record of the email they typed, whether
or not an account has that email (plan#2772, ruling plan#3110).  This module
owns the record's acts: the email's keyed digest, the one judgment of a try,
the read of the lock, and the clear.  The account's threshold and the lock's
length are the caller's (``app.services.auth_service`` reads them), so the
lock's numbers are stated where they always were.

**One judgment for every try** (:func:`judge_try`): a try the lock refuses is
not counted, a wrong one is counted, and the count reaching the threshold opens
the lock and zeroes the count.  Every try stamps its time, so every try writes
one row, and the audit trail holds one row per try (ruling plan#3113): a
refused one and a counted one cost the same write.  The record is read for
update under its row lock, held to the caller's commit, so two tries at one
email are judged one after the other and never count once; no other lock is
needed for that.

**The clean-up is in the same write** (:func:`_delete_quiet_records`), as a
finished sign-in's purge of the user's dead browsers is: each judgment deletes
the records quiet for the lock's length that no account's email names, or that
hold nothing (no count and no open lock).  An account's count is never deleted
by it, so the count runs "in a row" exactly as it did on the account's row.

**The clock is the process's**, ``datetime.now(timezone.utc)``, read once per
act, as ``app.services.known_device_service`` reads it.

Architecture:
  - No Flask imports.  Takes plain strings and numbers.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.extensions import db
from app.models.guess_record import GuessRecord
from app.models.user import User


#: What the digest's key is derived under, so the key Flask signs cookies with
#: is never itself the key of this digest.
_DIGEST_KEY_LABEL = b"shekel guess record email digest"


class TryOutcome(NamedTuple):
    """What :func:`judge_try` decided about one try.

    Attributes:
        refused: The lock was open, so the try was refused and not counted.
        opened_lock_until: When this try opened the lock, the moment the lock
            ends; else ``None``.
    """

    refused: bool
    opened_lock_until: datetime | None


def _digest_key() -> bytes:
    """Return the key :func:`email_digest` is computed under.

    Derived from ``SECRET_KEY``, read from the environment at call time as
    every key in this layer is (``mfa_service`` reads ``TOTP_ENCRYPTION_KEY``
    the same way).  A new ``SECRET_KEY`` therefore names new records: every
    count starts again, as every session does.

    Returns:
        The 32-byte key.

    Raises:
        RuntimeError: If ``SECRET_KEY`` is unset or empty, so no digest could
            be computed and no guess could be counted.
    """
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set, so a guess record's "
            "email digest cannot be computed."
        )
    return hmac.new(
        secret.encode("utf-8"), _DIGEST_KEY_LABEL, hashlib.sha256,
    ).digest()


def email_digest(email: str) -> str:
    """Return the keyed digest a guess record names *email* by.

    HMAC-SHA256 under :func:`_digest_key`, as lowercase hex.  Keyed because an
    email is guessable: a plain hash of one could be found by hashing a list
    of addresses, and the record's whole point is to hold no address.

    Args:
        email: The email as the account stores it and every auth form
            normalizes it (stripped and lowercased): the typed address at
            sign-in, ``User.email`` at the doors a session reaches.  Not
            normalized again here, so the one normalization rule stays the
            form schemas' and registration's.

    Returns:
        The digest, 64 lowercase hex characters.
    """
    return hmac.new(
        _digest_key(), email.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def is_locked(email: str) -> bool:
    """Return True while the lock on *email*'s record is open.

    A plain read, with no lock: a door asks it before its check so that a
    locked attempt is refused without one, and :func:`judge_try` judges again,
    under the record's row lock, when the try is counted.

    Args:
        email: The email whose record to read, as :func:`email_digest` takes
            it.

    Returns:
        True when the record exists and its ``locked_until`` is strictly
        later than now.
    """
    locked_until = db.session.execute(
        select(GuessRecord.locked_until).where(
            GuessRecord.email_digest == email_digest(email),
        )
    ).scalar_one_or_none()
    return locked_until is not None and locked_until > datetime.now(timezone.utc)


def judge_try(
    email: str, *, wrong: bool, threshold: int, duration: timedelta,
) -> TryOutcome:
    """Judge one stranger's try at *email* on its record, counting it if wrong.

    The ONE judgment of a try, whichever door it came to.  The record is read
    for update, so the row lock it takes is held until the caller commits and
    a second try at the same email waits for this one's judgment.  Then:

    * The try's time is stamped, whatever is decided, so every try writes.
    * While the lock is open the try is refused and not counted.
    * Else a wrong try is counted, and the count reaching *threshold* opens
      the lock for *duration* and zeroes the count, so a second lock takes
      another *threshold* wrong tries rather than one more.

    An email with no record yet is judged as a record with no count and no
    lock, and its record is inserted holding what was decided: one row
    written, as for a record that exists, so one audit row per try.  If
    another try inserted it first, that one's record is read for update and
    this try is judged on it.

    Then the quiet records are deleted (:func:`_delete_quiet_records`).

    Args:
        email: The email the try named, as :func:`email_digest` takes it.
        wrong: Whether the password or code was wrong.
        threshold: How many wrong tries in a row open the lock.
        duration: How long an opened lock stays open, and how long a record
            must be quiet before the clean-up may delete it.

    Returns:
        The :class:`TryOutcome`.
    """
    now = datetime.now(timezone.utc)
    digest = email_digest(email)
    record = _record_for_update(digest)
    if record is None:
        fresh = GuessRecord(
            email_digest=digest, failed_login_count=0, locked_until=None,
        )
        outcome = _judge(fresh, now, wrong=wrong, threshold=threshold, duration=duration)
        if _insert_judged(fresh):
            _delete_quiet_records(now, duration)
            return outcome
        record = _record_for_update(digest)
    outcome = _judge(record, now, wrong=wrong, threshold=threshold, duration=duration)
    db.session.flush()
    _delete_quiet_records(now, duration)
    return outcome


def _record_for_update(digest: str) -> GuessRecord | None:
    """Return the record *digest* names, read fresh under its row lock, or ``None``.

    Args:
        digest: The email's digest.

    Returns:
        The record, or ``None`` when there is none.
    """
    return db.session.execute(
        select(GuessRecord)
        .where(GuessRecord.email_digest == digest)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _insert_judged(fresh: GuessRecord) -> bool:
    """Insert *fresh*, an email's first record, holding what its try decided.

    Never added to the session: the insert yields to a record another try
    inserted first, which the caller then reads and judges on instead.

    Args:
        fresh: The judged, unsaved record.

    Returns:
        True when this insert made the record.
    """
    made = db.session.execute(
        insert(GuessRecord)
        .values(
            email_digest=fresh.email_digest,
            failed_login_count=fresh.failed_login_count,
            locked_until=fresh.locked_until,
            last_attempt_at=fresh.last_attempt_at,
        )
        .on_conflict_do_nothing(index_elements=[GuessRecord.email_digest])
        .returning(GuessRecord.id)
    ).scalar_one_or_none()
    return made is not None


def _judge(
    record: GuessRecord, now: datetime,
    *, wrong: bool, threshold: int, duration: timedelta,
) -> TryOutcome:
    """Apply one try to *record*, read for update at *now* or not yet saved.

    Args:
        record: The email's record, under its row lock, or its first one.
        now: The try's instant.
        wrong: Whether the password or code was wrong.
        threshold: How many wrong tries in a row open the lock.
        duration: How long an opened lock stays open.

    Returns:
        The :class:`TryOutcome`.
    """
    record.last_attempt_at = now
    if record.locked_until is not None and record.locked_until > now:
        return TryOutcome(refused=True, opened_lock_until=None)
    if not wrong:
        return TryOutcome(refused=False, opened_lock_until=None)
    record.failed_login_count += 1
    if record.failed_login_count < threshold:
        return TryOutcome(refused=False, opened_lock_until=None)
    record.locked_until = now + duration
    record.failed_login_count = 0
    return TryOutcome(refused=False, opened_lock_until=record.locked_until)


def _delete_quiet_records(now: datetime, duration: timedelta) -> None:
    """Delete the records quiet for *duration* that no one needs.

    A record quiet that long holds nothing a try could act on when either no
    account's email names it (a made-up email, or one an account has since
    changed), or it holds no count and no open lock (the same as no record).
    An account's count is never deleted here, however quiet.

    The accounts' digests are computed from every account's email, read in
    the same statement on every judgment, so the clean-up costs the same
    whichever email the try named.  Rows another transaction holds are
    skipped rather than waited for: that transaction is judging a try on one,
    so it is not quiet, and two clean-ups never wait on each other.

    Args:
        now: The judgment's instant.
        duration: How long a record must be quiet.
    """
    account_digests = [
        email_digest(email)
        for email in db.session.execute(select(User.email)).scalars()
    ]
    holds_nothing = and_(
        GuessRecord.failed_login_count == 0,
        or_(GuessRecord.locked_until.is_(None), GuessRecord.locked_until <= now),
    )
    quiet = (
        select(GuessRecord.id)
        .where(
            GuessRecord.last_attempt_at <= now - duration,
            or_(GuessRecord.email_digest.not_in(account_digests), holds_nothing),
        )
        .with_for_update(skip_locked=True)
    )
    db.session.execute(
        delete(GuessRecord)
        .where(GuessRecord.id.in_(quiet))
        .execution_options(synchronize_session="fetch")
    )


def clear(email: str) -> bool:
    """Delete *email*'s record, once every check its account faces has passed.

    No record is the same as one with no count and no open lock, so the clear
    is a delete.

    Args:
        email: The account's email, as :func:`email_digest` takes it.

    Returns:
        True when there was a record to delete.
    """
    result = db.session.execute(
        delete(GuessRecord)
        .where(GuessRecord.email_digest == email_digest(email))
        .execution_options(synchronize_session="fetch")
    )
    return result.rowcount > 0
