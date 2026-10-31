"""
Shekel Budget App -- Trusted Device Model (auth schema)

A browser on which a user typed a two-factor code and asked to be trusted
(plan#2173, ruling plan#2406).  Signing in there still takes the password;
what trust skips is the code, and only that.

**What the row does NOT store is the design.**  There is no expiry column and
no revoked flag (CLAUDE.md rule 14): a device is live while it was trusted
less than :data:`TRUST_LIFETIME` ago AND not before its user's
``session_invalidated_at``.  Both halves are derived from facts stored once
elsewhere, so every existing writer of ``session_invalidated_at`` (log out all
sessions, a password change, the owner's companion password reset or
deactivation, turning two-factor off, a backup-code sign-in,
``scripts/rotate_sessions.py``) forgets every trusted device with no write of
its own.  The one statement of that predicate is
``app.services.trusted_device_service._live``.

**The browser's token is never stored**: only its SHA-256, as lowercase hex,
which ``ck_trusted_devices_token_hash_sha256_hex`` pins so a writer that
stored the token itself is refused at the database.  The token is 256 random
bits, so the hash needs no salt or stretching: there is no dictionary to
search.  The table is audited, so ``system.audit_log`` holds each row's hash
too, which is as useless as this table's copy without the token.

Related service: app/services/trusted_device_service.py
"""

from datetime import timedelta

from sqlalchemy.ext.hybrid import hybrid_property

from app.extensions import db


#: How long a device stays trusted after the code was typed on it (ruling
#: plan#2406).  The one statement of the length: :attr:`TrustedDevice.expires_at`
#: reads it, and the device cookie's lifetime is to read it too.
TRUST_LIFETIME = timedelta(days=30)

#: The longest User-Agent a record keeps.  It is kept only to name the device
#: in Settings > Security, and a browser chooses the header's length, so the
#: service cuts it here rather than letting a long one refuse the trust.
USER_AGENT_MAX_LENGTH = 255

#: Characters in a SHA-256 digest written as hex: the length of
#: :attr:`TrustedDevice.token_hash`.
TOKEN_HASH_LENGTH = 64


class TrustedDevice(db.Model):
    """One browser a user trusted to skip the two-factor code.

    Never updated: a device trusted again is a new row with a new token, and
    the trust that wrote it purges the user's dead rows.  So there is no
    ``updated_at``, and no ``created_at`` beside :attr:`trusted_at`, which is
    the moment the row was made: a second column holding it would be a second
    home for one fact.
    """

    __tablename__ = "trusted_devices"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="pk_trusted_devices"),
        db.UniqueConstraint("token_hash", name="uq_trusted_devices_token_hash"),
        db.CheckConstraint(
            f"token_hash ~ '^[0-9a-f]{{{TOKEN_HASH_LENGTH}}}$'",
            name="ck_trusted_devices_token_hash_sha256_hex",
        ),
        db.Index("ix_trusted_devices_user_id", "user_id"),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "auth.users.id", name="fk_trusted_devices_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    token_hash = db.Column(db.String(TOKEN_HASH_LENGTH), nullable=False)
    # NOT NULL: a browser that sends no User-Agent is stored as the empty
    # string, which names it no less than NULL would.
    user_agent = db.Column(db.String(USER_AGENT_MAX_LENGTH), nullable=False)
    trusted_at = db.Column(db.DateTime(timezone=True), nullable=False)

    @hybrid_property
    def expires_at(self):
        """Return the moment this device stops being trusted by age.

        :attr:`trusted_at` plus :data:`TRUST_LIFETIME`, and ONE body for both
        levels: on a row it is a ``datetime``, and at class level the same
        expression is SQL (``trusted_at + interval``), so the liveness
        predicate and the date Settings shows are one spelling.  A device is
        alive by age while this is strictly later than now, so at exactly
        thirty days it is dead.  It does not say whether the device was
        forgotten by a ``session_invalidated_at`` bump: only the predicate in
        ``app.services.trusted_device_service`` says that.

        Returns:
            The expiry moment, timezone-aware.
        """
        return self.trusted_at + TRUST_LIFETIME

    def __repr__(self):
        return f"<TrustedDevice id={self.id} user_id={self.user_id}>"
