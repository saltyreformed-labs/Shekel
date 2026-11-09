"""
Shekel Budget App -- Guess Record Service

A stranger's wrong guesses count on a record of the email they typed, whether
or not an account has that email (plan#2772, ruling plan#3110).  This module
owns the record's acts: the email's keyed digest, the one judgment of a try,
the read of the lock, and the clear.  The account's threshold and the lock's
length are the caller's (``app.services.auth_service`` reads them), so the
lock's numbers are stated where they always were.

**One statement judges every try** (:func:`judge_try`): a try the lock refuses
is not counted, a wrong one is counted, and the count reaching the threshold
opens the lock and zeroes the count.  Every judgment stamps its time, so it
writes one row, and the audit trail holds one row per judgment (ruling
plan#3113), which is one per try but for a sign-in whose password changed
during its check, judged again as wrong: a refused try and a counted one cost
the same write.  The statement is the same
text for every try, whether the email has a record, an account, an open lock or
a right password (ruling plan#3110): an insert of the email's first record
that, where the email has one, judges that one instead.  The row lock it takes
is held to the caller's commit, so two tries at one email are judged one after
the other and never count once; no other lock is needed for that.

**Each rule is stated once**, as SQL: :func:`_lock_open` (when a lock is open)
and :func:`_judged` (what one try leaves on a record), which the judgment
applies both to the record an email has and to the empty one its first try
starts from.

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

from sqlalchemy import ColumnElement, and_, case, delete, literal, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.extensions import db
from app.models.guess_record import GuessRecord
from app.models.user import User


#: What the digest's key is derived under, so the key Flask signs cookies with
#: is never itself the key of this digest.
_DIGEST_KEY_LABEL = b"shekel guess record email digest"

#: The record as it stood before the try :func:`judge_try` judged, in that
#: statement's RETURNING: PostgreSQL 18's ``old`` row, which the statement
#: names by an alias of the table called ``old`` because SQLAlchemy has no
#: spelling of its own for it.  Every column of it is NULL when the try made
#: the record.  On a PostgreSQL older than 18 the statement is refused
#: (``missing FROM-clause entry for table "old"``), so no sign-in passes
#: unjudged; production and the test image both run 18.
_BEFORE = GuessRecord.__table__.alias("old")


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


def _lock_open(
    locked_until: ColumnElement[datetime | None], now: datetime,
) -> ColumnElement[bool]:
    """Return the ONE statement of when a lock is open, as SQL.

    Open while its end is strictly later than *now*, so at the instant it
    equals *now* the lock is over.  Never NULL: a record with no lock, the
    empty record a first try starts from and the ``old`` row of a try that made
    its record all read as closed, so the clause can be negated and returned
    as it stands.

    Args:
        locked_until: The lock's end, as a column or the empty record's NULL.
        now: The moment to judge at.

    Returns:
        A boolean clause.
    """
    return and_(locked_until.is_not(None), locked_until > now)


def is_locked(email: str) -> bool:
    """Return True while the lock on *email*'s record is open.

    A plain read, with no lock: a signed-in door and the code form ask it
    before their check so that a locked attempt is refused without one, and
    :func:`judge_try` judges again, under the record's row lock, when the try
    is counted.

    Args:
        email: The email whose record to read, as :func:`email_digest` takes
            it.

    Returns:
        True when the record exists and :func:`_lock_open` says its lock is.
    """
    return db.session.execute(
        select(_lock_open(GuessRecord.locked_until, datetime.now(timezone.utc)))
        .where(GuessRecord.email_digest == email_digest(email))
    ).scalar_one_or_none() is True


class _Holding(NamedTuple):
    """What a record holds before a try, as SQL: the two values a try judges on.

    Attributes:
        count: The wrong tries counted.
        locked_until: The lock's end, or NULL.
    """

    count: ColumnElement[int]
    locked_until: ColumnElement[datetime | None]


#: What an email's record holds, where it has one: its own columns, as the
#: judgment's update reads them.
_THE_RECORD = _Holding(GuessRecord.failed_login_count, GuessRecord.locked_until)

#: What an email with no record holds: no count and no lock, the empty record
#: its first try is judged from.
_NO_RECORD = _Holding(literal(0), literal(None, GuessRecord.locked_until.type))


def _judged(
    holding: _Holding, now: datetime,
    *, wrong: bool, threshold: int, duration: timedelta,
) -> dict[str, ColumnElement]:
    """Return what one try leaves on a record *holding* a count and a lock, as SQL.

    The ONE statement of the judgment:

    * The try's time is stamped, whatever is decided, so every try writes.
    * While the lock is open (:func:`_lock_open`) the try is refused: the
      count and the lock stay as they were.
    * Else a wrong try is counted, and the count reaching *threshold* opens
      the lock for *duration* and zeroes the count, so a second lock takes
      another *threshold* wrong tries rather than one more.

    *wrong* is a bound parameter, never spelled into the SQL, so a right
    password and a wrong one send the same statement text.

    Args:
        holding: What the record holds before the try: :data:`_THE_RECORD`
            or :data:`_NO_RECORD`.
        now: The try's instant.
        wrong: Whether the password or code was wrong.
        threshold: How many wrong tries in a row open the lock.
        duration: How long an opened lock stays open.

    Returns:
        The record's three judged columns, by name.
    """
    count, locked_until = holding
    locked = _lock_open(locked_until, now)
    is_wrong = literal(wrong)
    trips = and_(is_wrong, count + 1 >= threshold)
    return {
        "failed_login_count": case(
            (locked, count), (trips, 0), (is_wrong, count + 1), else_=count,
        ),
        "locked_until": case(
            (locked, locked_until), (trips, now + duration), else_=locked_until,
        ),
        "last_attempt_at": literal(now, GuessRecord.last_attempt_at.type),
    }


def judge_try(
    email: str, *, wrong: bool, threshold: int, duration: timedelta,
) -> TryOutcome:
    """Judge one stranger's try at *email* on its record, counting it if wrong.

    The ONE judgment of a try, whichever door it came to, in ONE statement
    (ruling plan#3110): an insert of the email's first record, judged from an
    empty one (no count, no lock), which, where the email already has a
    record, judges that record instead (:func:`_judged`, applied to each).
    So every try sends the same statement text and writes one row, whether or
    not the email has a record, an account or an open lock, and the audit
    trail holds one row per judgment.  The row lock the statement takes is held
    until the caller commits, and a second try at the same email, its first
    one included, waits for this one's judgment and is judged on what it left.

    The statement returns what was decided from the record before and after
    the try (:data:`_BEFORE`): the try was refused when the lock was open
    before it, and it opened the lock when the lock's end moved, which only
    the opening of a lock does.

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
    first = insert(GuessRecord).values(
        email_digest=email_digest(email),
        **_judged(
            _NO_RECORD, now, wrong=wrong, threshold=threshold, duration=duration,
        ),
    )
    statement = first.on_conflict_do_update(
        index_elements=[GuessRecord.email_digest],
        set_=_judged(
            _THE_RECORD, now, wrong=wrong, threshold=threshold, duration=duration,
        ),
    ).returning(
        _lock_open(_BEFORE.c.locked_until, now),
        case(
            (
                GuessRecord.locked_until.is_distinct_from(_BEFORE.c.locked_until),
                GuessRecord.locked_until,
            ),
        ),
    )
    refused, opened_lock_until = db.session.execute(statement).one()
    _delete_quiet_records(now, duration)
    return TryOutcome(refused=refused, opened_lock_until=opened_lock_until)


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
        ~_lock_open(GuessRecord.locked_until, now),
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
