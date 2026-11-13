"""
Shekel Budget App -- Known Device Model (auth schema)

One browser a user has finished a sign-in on (plan#2772, ruling plan#2994).
The row holds two separate standings, and neither ever signs anyone in:

**Known**: the user finished a sign-in here within :data:`KNOWN_LIFETIME`.  A
known browser is not stopped by the lock strangers' wrong guesses put on the
account, at any door, and its own wrong passwords and codes count on
:attr:`KnownDevice.failed_login_count` rather than on the account (OWASP's
"Slow Down Online Guessing Attacks with Device Cookies").  It still needs the
password, and the code too on an account with two-factor.  Nothing a person
presses ends this standing, so a thief with a live session cannot press a
button and then lock the owner out (ruling plan#2997): it ends by age, or when
the browser guesses wrong too many times in a row, which deletes the row
(ruling plan#2996).

**Trusted**: the user typed a two-factor code here and asked to be trusted
(plan#2173, ruling plan#2406).  What trust skips is the code, and only that.
A device is trusted while :attr:`KnownDevice.trusted_at` is less than
:data:`TRUST_LIFETIME` ago AND not before its user's
``session_invalidated_at``, so every existing writer of that column (log out
all sessions, a password change, the owner's companion password reset or
deactivation, turning two-factor off (``/mfa/disable`` or the emergency
``scripts/reset_mfa.py``), a backup-code sign-in,
``scripts/rotate_sessions.py``) ends every trust with no write of its own.

**What the row does NOT store is the design.**  There is no expiry column, no
revoked flag and no lock (CLAUDE.md rule 14): each standing is derived from
facts stored once.  The one statement of each predicate is in
``app.services.known_device_service``: ``_known`` and ``_trusted``.

**The browser's token is never stored**: only its SHA-256, as lowercase hex,
which ``ck_known_devices_token_hash_sha256_hex`` pins so a writer that stored
the token itself is refused at the database.  The token is 256 random bits, so
the hash needs no salt or stretching: there is no dictionary to search.  The
table is audited, so ``system.audit_log`` holds each row's hash too, which is
as useless as this table's copy without the token.

Related service: app/services/known_device_service.py
"""

from datetime import timedelta

from app.extensions import db


#: How long a device stays trusted after the code was typed on it (ruling
#: plan#2406).  The one statement of the length: the trust's bound
#: (``app.services.known_device_service._trusted``),
#: :attr:`KnownDevice.trust_expires_at` and every sentence that names it read
#: this.
TRUST_LIFETIME = timedelta(days=30)

#: How long a browser stays known after its last finished sign-in: the same
#: length as trust (ruling plan#2995), so it is stated once and named twice.
#: A trust begins at a finished sign-in, which renews this too, so the
#: browser's cookie, which lasts this long, can never die before a trust does.
#: A session ends this long after its sign-in too, however active (ruling
#: plan#3069, ``app.utils.session_helpers.sign_in_is_live``): the sign-in that
#: starts it renews the record from the same instant, so a browser that is
#: signed in is known, unless ten wrong guesses on its cookie, its own or a
#: copy's, removed its record.
KNOWN_LIFETIME = TRUST_LIFETIME

#: The longest User-Agent a record keeps.  It is kept only to name the device
#: on the Security page, and a browser chooses the header's length, so the
#: service cuts it here rather than letting a long one refuse the sign-in.
USER_AGENT_MAX_LENGTH = 255

#: Characters in a SHA-256 digest written as hex: the length of
#: :attr:`KnownDevice.token_hash`.
TOKEN_HASH_LENGTH = 64


class KnownDevice(db.Model):
    """One browser a user finished a sign-in on; a trust is one fact on it.

    One browser holds one row per user, renewed at each finished sign-in, so
    there is no ``created_at`` or ``updated_at``: nothing reads them, and
    :attr:`last_signed_in_at` is the moment the standing is judged from.
    """

    __tablename__ = "known_devices"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="pk_known_devices"),
        db.UniqueConstraint("token_hash", name="uq_known_devices_token_hash"),
        db.CheckConstraint(
            f"token_hash ~ '^[0-9a-f]{{{TOKEN_HASH_LENGTH}}}$'",
            name="ck_known_devices_token_hash_sha256_hex",
        ),
        db.CheckConstraint(
            "failed_login_count >= 0",
            name="ck_known_devices_failed_login_count_non_negative",
        ),
        db.Index("ix_known_devices_user_id", "user_id"),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "auth.users.id", name="fk_known_devices_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    token_hash = db.Column(db.String(TOKEN_HASH_LENGTH), nullable=False)
    # NOT NULL: a browser that sends no User-Agent is stored as the empty
    # string, which names it no less than NULL would.
    user_agent = db.Column(db.String(USER_AGENT_MAX_LENGTH), nullable=False)
    last_signed_in_at = db.Column(db.DateTime(timezone=True), nullable=False)
    # NULL means no one ever asked this browser to be trusted, which is what
    # most browsers are: a sign-in without the box ticked writes no trust.
    trusted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Wrong passwords and codes in a row from this browser since its last
    # finished sign-in or re-confirm.  Not the account's count: a stranger's
    # guess never moves it.
    failed_login_count = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )

    @property
    def trust_expires_at(self):
        """Return the moment this device stops being trusted by age.

        :attr:`trusted_at` plus :data:`TRUST_LIFETIME`, in Python, for display,
        and so asked only of a device holding a trust, as Settings' list is.
        A device is alive by age while this is strictly later than now, so at
        exactly thirty days it is dead; the service's bound says the same as
        ``trusted_at > now - TRUST_LIFETIME``, its exact inverse.

        **Not SQL.**  This was a hybrid whose class-level form,
        ``trusted_at + interval '30 days'``, the bound filtered on, and
        PostgreSQL adds an interval's days in the session's TimeZone: thirty
        calendar days in that zone, not 720 hours, so across a daylight-saving
        change the database and this value parted by an hour (plan#2173's
        review, measured under ``America/New_York``).  The bound now subtracts
        in Python, where a ``timedelta`` is exact.  It does not say whether
        the trust ended by a ``session_invalidated_at`` bump: only the
        predicate in ``app.services.known_device_service`` says that.

        Returns:
            The expiry moment, timezone-aware.
        """
        return self.trusted_at + TRUST_LIFETIME

    def __repr__(self):
        return f"<KnownDevice id={self.id} user_id={self.user_id}>"
