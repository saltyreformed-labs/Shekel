"""
Shekel Budget App -- Guess Record Model (auth schema)

A stranger's wrong guesses at one email, whether or not an account has that
email (plan#2772, ruling plan#3110).  "A stranger" is any browser that is not
known to the account (``app.models.known_device``): a known browser's guesses
count on its own record, and this one never moves for them.

**Every email can have one, so an email with no account can cost what a real
one does.**  The lock and the count lived on the account's row until this
table, and an email with no account had no row: a stranger's wrong password
wrote and committed for a real email and wrote nothing for a made-up one, so a
real email's reply was slower by that write, a clue to which emails have
accounts.  The one judgment in ``app.services.guess_record_service`` counts,
locks and stamps a made-up email's record exactly as a real one's.

**The email is never stored**: only its keyed digest
(:func:`~app.services.guess_record_service.email_digest`), as lowercase hex,
which ``ck_guess_records_email_digest_sha256_hex`` pins so a writer that
stored the address itself is refused at the database.  Unlike a device token,
an email is guessable, so the digest is keyed: without the key, a copy of this
table cannot be searched for the emails strangers typed.

The table is audited (ruling plan#3113): every stranger's try writes its
record, so the audit trail holds one row per try, with the digest and never
the address.

Related service: app/services/guess_record_service.py
"""

from app.extensions import db


#: Characters in a SHA-256 digest written as hex: the length of
#: :attr:`GuessRecord.email_digest`.
EMAIL_DIGEST_LENGTH = 64


class GuessRecord(db.Model):
    """A stranger's wrong guesses at one email, and the lock they open.

    No ``created_at`` or ``updated_at``: :attr:`last_attempt_at` is the one
    moment anything reads, and the clean-up reads it.
    """

    __tablename__ = "guess_records"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="pk_guess_records"),
        db.UniqueConstraint(
            "email_digest", name="uq_guess_records_email_digest",
        ),
        db.CheckConstraint(
            f"email_digest ~ '^[0-9a-f]{{{EMAIL_DIGEST_LENGTH}}}$'",
            name="ck_guess_records_email_digest_sha256_hex",
        ),
        # Defensive lower bound: the service only ever adds one to a
        # non-negative count or zeroes it, but a negative value written by
        # hand would never reach the threshold, and the lock would never open.
        db.CheckConstraint(
            "failed_login_count >= 0",
            name="ck_guess_records_failed_login_count_non_negative",
        ),
        # The clean-up's filter: every write deletes the records quiet for
        # the lock's length.
        db.Index("ix_guess_records_last_attempt_at", "last_attempt_at"),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer)
    email_digest = db.Column(db.String(EMAIL_DIGEST_LENGTH), nullable=False)
    # Wrong passwords and codes in a row since the account's checks last all
    # passed or the lock last opened, which zeroes it.
    failed_login_count = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    # Exclusive upper bound on the lock, or NULL when no lock has opened since
    # the record was written.  Strictly greater than now is open: at the
    # instant it equals now the lock is over, and a past value is the same as
    # NULL to every check.
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    # The last try judged on this record, counted or not: a try the lock
    # refuses still stamps it, so every stranger's try writes.
    last_attempt_at = db.Column(db.DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<GuessRecord id={self.id}>"
