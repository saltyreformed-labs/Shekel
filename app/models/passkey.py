"""
Shekel Budget App -- Passkey Model (auth schema)

One passkey a user added to their account (plan#2538): a key their phone or
security key made for this site, which signs Shekel's challenge when the
person unlocks it with Face ID, a fingerprint or the device's passcode.

**What it lets its user do** (rulings plan#3465, plan#3467).  A passkey signs
in on its own, with no password and no code, and answers the re-confirm Shekel
asks before a sensitive change.  An account that holds one signs in only by
passkey: its password-and-code sign-in is closed while a row here names it,
so the account cannot be phished.  The one statement of that predicate is
in ``app.services.passkey_service``.

**What the row stores is what verifying a signature needs, and no more.**
The credential's id, which the browser sends back to name the key; its public
key, as the COSE bytes the authenticator gave (RFC 9052), from which the
signature is checked; the signature counter; the name the user gave it; when
it was added; and when it last signed Shekel in.  Nothing derivable is stored
(CLAUDE.md rule 14): not the authenticator's model id (AAGUID), not the
transports it reported (no reader needs either), not a count of the user's
passkeys, and not a WebAuthn user handle, which the service derives from the
user's id by one function.

**Nothing here is secret.**  A public key and a credential id sign nobody in
without the private key, which never leaves the person's device.  The table
is audited, so ``system.audit_log`` records each passkey added and removed.

Related service: app/services/passkey_service.py
"""

from app.extensions import db


#: The longest name a passkey keeps.  A name only labels the key on the
#: Security page, so it is short; the form and the service refuse a longer one
#: rather than letting the database refuse it.
NAME_MAX_LENGTH = 60

#: The longest credential id WebAuthn allows, in bytes (Web Authentication
#: Level 3, "Credential ID": at most 1023 bytes).
CREDENTIAL_ID_MAX_BYTES = 1023

#: The largest signature counter WebAuthn can report: the field is an
#: unsigned 32-bit integer in the authenticator data.
SIGN_COUNT_MAX = 2**32 - 1


class Passkey(db.Model):
    """One passkey a user added; it signs that user in on its own."""

    __tablename__ = "passkeys"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="pk_passkeys"),
        # The browser names the key by this id at every sign-in, and the
        # sign-in finds the account by it alone, so one id names one row.
        db.UniqueConstraint("credential_id", name="uq_passkeys_credential_id"),
        db.CheckConstraint(
            f"octet_length(credential_id) BETWEEN 1 AND {CREDENTIAL_ID_MAX_BYTES}",
            name="ck_passkeys_credential_id_length",
        ),
        db.CheckConstraint(
            "octet_length(public_key) > 0",
            name="ck_passkeys_public_key_not_empty",
        ),
        db.CheckConstraint(
            f"sign_count BETWEEN 0 AND {SIGN_COUNT_MAX}",
            name="ck_passkeys_sign_count_range",
        ),
        db.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_passkeys_name_not_blank",
        ),
        db.Index("ix_passkeys_user_id", "user_id"),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer)
    credential_id = db.Column(db.LargeBinary, nullable=False)
    public_key = db.Column(db.LargeBinary, nullable=False)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "auth.users.id", name="fk_passkeys_user_id", ondelete="CASCADE",
        ),
        nullable=False,
    )
    # The authenticator's signature counter at its last accepted signature.
    # Apple's synced passkeys always report 0; a key that counts lets the
    # service refuse a count that fails to rise, the sign of a copied key.
    sign_count = db.Column(
        db.BigInteger, nullable=False, default=0, server_default=db.text("0"),
    )
    name = db.Column(db.String(NAME_MAX_LENGTH), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    # NULL means the passkey has not signed anyone in since it was added.
    # Two readers: the Security page shows it as "last used", and the
    # service refuses a challenge issued at or before it, so one signed
    # answer cannot sign in twice (Apple's counter never moves, so the
    # counter cannot do that job).
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Passkey id={self.id} user_id={self.user_id}>"
