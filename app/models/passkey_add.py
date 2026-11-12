"""
Shekel Budget App -- Passkey Add Model (auth schema)

One account's add of a passkey, in flight (ruling plan#3543).  Adding a
passkey is three acts (ruling plan#3525, ``app.services.passkey_service``):
the device is asked to make a passkey, the registration it answers is
checked, and the device signs in with the new passkey, which only then is
kept.  Between the acts the add waits here, and not in the session, which in
Shekel is the signed cookie: a browser drops a cookie past about 4 KB, and an
unusual key alone outgrows that.

**One row per account** (``uq_passkey_adds_user_id``).  A newer add replaces
the account's row under a new ``id``, so an older add's state names a row
that is gone and completes nothing.  The row is the add's one home (CLAUDE.md
rule 14): the session keeps only what tells a completion which ceremony it
answers, the ceremony's name and this row's ``id``, and no copy of what the
row holds.

**Two phases, one row.**  Begun, it holds the registration's challenge and
the moment it was issued.  Once the registration completes, it holds the
challenge and moment of the sign-in by the new passkey instead, and that
passkey: its credential id, its public key trimmed to what a sign-in verifies
by, its counter and the name typed for it.  Those four are empty together or
filled together (``ck_passkey_adds_made_together``), and each filled one is
held to the CHECK ``auth.passkeys`` holds it to, so the keeping copies them
across as they are.

**Used once, and for five minutes.**  The keeping deletes the row in the
transaction that keeps the passkey, so its answer and state replayed later,
even after that passkey is removed, keep nothing.  A row whose challenge is
five minutes old is refused, and deleted, when a completion reads it.  One
never read again, an add abandoned, can complete nothing either, and is
deleted by the next add any account begins (ruling plan#3578), its own
account's replacing it, or with its account: one row per account at most.

**Nothing here is secret.**  A challenge is sent to the browser in the clear,
and a public key and credential id sign nobody in without the private key,
which never leaves the person's device.  The table is audited, so
``system.audit_log`` records each add begun, registered and kept.

Related service: app/services/passkey_service/
"""

from app.extensions import db
from app.models.passkey import CREDENTIAL_ID_MAX_BYTES, NAME_MAX_LENGTH, SIGN_COUNT_MAX


#: The shortest challenge a row may hold, in bytes: the least Web
#: Authentication Level 3 advises (13.4.3, "Cryptographic Challenges"), and
#: the least fido2 accepts.  fido2 makes 32.
CHALLENGE_MIN_BYTES = 16


class PasskeyAdd(db.Model):
    """One account's add of a passkey, between its acts."""

    __tablename__ = "passkey_adds"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="pk_passkey_adds"),
        # One add in flight per account: a newer one replaces it.
        db.UniqueConstraint("user_id", name="uq_passkey_adds_user_id"),
        db.ForeignKeyConstraint(
            ["user_id"], ["auth.users.id"],
            name="fk_passkey_adds_user_id", ondelete="CASCADE",
        ),
        db.CheckConstraint(
            f"octet_length(challenge) >= {CHALLENGE_MIN_BYTES}",
            name="ck_passkey_adds_challenge_length",
        ),
        db.CheckConstraint(
            "num_nulls(credential_id, public_key, sign_count, name) IN (0, 4)",
            name="ck_passkey_adds_made_together",
        ),
        db.CheckConstraint(
            f"octet_length(credential_id) BETWEEN 1 AND {CREDENTIAL_ID_MAX_BYTES}",
            name="ck_passkey_adds_credential_id_length",
        ),
        db.CheckConstraint(
            "octet_length(public_key) > 0",
            name="ck_passkey_adds_public_key_not_empty",
        ),
        db.CheckConstraint(
            f"sign_count BETWEEN 0 AND {SIGN_COUNT_MAX}",
            name="ck_passkey_adds_sign_count_range",
        ),
        db.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_passkey_adds_name_not_blank",
        ),
        {"schema": "auth"},
    )

    # Renewed when a newer add replaces the account's row, so the session's
    # state of the older add names no row.
    id = db.Column(db.Integer)
    user_id = db.Column(db.Integer, nullable=False)
    # The challenge the act in flight must be answered to, and when it was
    # issued: the registration's, then the sign-in's.
    challenge = db.Column(db.LargeBinary, nullable=False)
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False)
    # The passkey the registration made.  NULL while the registration is in
    # flight, because the device has made none yet; all four together.
    credential_id = db.Column(db.LargeBinary, nullable=True)
    public_key = db.Column(db.LargeBinary, nullable=True)
    sign_count = db.Column(db.BigInteger, nullable=True)
    name = db.Column(db.String(NAME_MAX_LENGTH), nullable=True)

    def __repr__(self):
        return f"<PasskeyAdd id={self.id} user_id={self.user_id}>"
