"""The count and lock on a guess record, as the tests read and set them.

A stranger's wrong guesses count on the record of the email typed (plan#2772,
ruling plan#3110), not on ``auth.users``.  A test that read
``user.failed_login_count`` or ``user.locked_until`` reads :func:`count_of` or
:func:`locked_until_of` of the account's email, and one that set them to stage
a state sets the record with :func:`set_record`.  Each read is fresh from the
database, so a value a request committed is what a test sees.
"""

from datetime import datetime

from sqlalchemy import select

from app.extensions import db
from app.models.guess_record import GuessRecord
from app.services.guess_record_service import email_digest


def record_of(email: str) -> GuessRecord | None:
    """Return *email*'s guess record, read fresh, or ``None`` when it has none.

    Args:
        email: The email, as the account stores it.

    Returns:
        The record, or ``None``.
    """
    return db.session.execute(
        select(GuessRecord)
        .where(GuessRecord.email_digest == email_digest(email))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def count_of(email: str) -> int:
    """Return the wrong guesses counted on *email*'s record; no record is zero.

    Args:
        email: The email, as the account stores it.

    Returns:
        The count.
    """
    record = record_of(email)
    return 0 if record is None else record.failed_login_count


def locked_until_of(email: str) -> datetime | None:
    """Return when the lock on *email*'s record ends, or ``None`` when none opened.

    Args:
        email: The email, as the account stores it.

    Returns:
        The lock's end, or ``None``.
    """
    record = record_of(email)
    return None if record is None else record.locked_until


def set_record(
    email: str, *, count: int, locked_until: datetime | None,
    last_attempt_at: datetime,
) -> None:
    """Stage *email*'s record with a count and a lock, flushed but not committed.

    Args:
        email: The email, as the account stores it.
        count: The wrong guesses to hold.
        locked_until: The lock's end, or ``None`` for no lock.
        last_attempt_at: The last try's time the record holds.
    """
    record = record_of(email)
    if record is None:
        record = GuessRecord(email_digest=email_digest(email))
        db.session.add(record)
    record.failed_login_count = count
    record.locked_until = locked_until
    record.last_attempt_at = last_attempt_at
    db.session.flush()
