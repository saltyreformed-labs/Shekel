"""
Shekel Budget App -- The bank FEED (budget schema)

One table, one subject (plan step ``bank_import:X-f6b-2``, ruling
**bank_import:R-BI12**): the owner's standing permission to fetch their bank's
lines without a file -- the SimpleFIN access URL, claimed once from a setup
token and kept as ciphertext.

**Why it is a row per OWNER and not a docker secret** (ruling **R-BI12**).  The
access URL is a credential the owner obtains from Bridge for THEIR bank
accounts, so it is scoped the way every ownership gate here is scoped: by
``user_id``, one feed per owner (``uq_bank_feeds_user``).  A host-side secret
file would make it one global feed found through the identity rows, with no
in-app connect or disconnect, and a rotation that is a host file edit and a
restart.  The exposure class is the TOTP secret's -- a database dump AND the
field key -- for a token that is read-only and revocable at Bridge.

**What the ciphertext IS.**  :attr:`BankFeed.access_url_encrypted` is the URL
``https://<user>:<password>@bridge.simplefin.org/simplefin`` written through
:func:`app.utils.field_encryption.encrypt_secret` under ``FIELD_ENCRYPTION_KEY``
-- the same cipher, key and rotation as ``auth.mfa_configs``' TOTP secret
(``scripts/rotate_field_key.py`` re-wraps both).  **The ruling allows it to be
decrypted in exactly two places, the claim door and the sync** -- the two
leaves of ``X-f6b-2`` after this table; at this commit nothing decrypts it --
which is why this model exposes NO property that decrypts on attribute
access: a reader that could reach the plaintext by naming an attribute is a
reader a template, a ``repr`` or a log line could reach it through by
accident.  A door calls :func:`~app.utils.field_encryption.decrypt_secret` on
the bytes, in the transaction that needs them, and holds the result no longer
than the request.

**Disconnect DELETES the row, and the mappings declared under it go with
it** (ruling **R-BI12**: "Disconnect deletes the row; revoke at Bridge is
yours"; ruling **R-BI26**: a mapping declared on the feed panel dies with the
feed).  The imports it recorded stand on their own (each is a :class:`~app
.models.statement_import.StatementImport` with its declared window); the
account mapping -- ``budget.account_external_identities`` under the
``simplefin`` source, ``feed_id`` naming this row -- is the owner's
declaration made under this permission, so the CASCADE on
``fk_account_external_identities_feed_owner`` removes it with the permission,
and a re-connect declares its accounts again (the mapping form, the next
commit of leaf 3c).  *Until R-BI26 this
paragraph said the mapping stayed so a re-connect would find it; the
developer ruled the other way on 2026-09-20, because a mapping with no feed
behind it is a state nothing could act on.*  The table joins
:data:`app.audit_infrastructure.AUDITED_TABLES` like every ``budget`` table,
so ``system.audit_log`` keeps every row written here -- the INSERT, a
rotation's UPDATE, the DELETE -- ciphertext included, as it keeps
``mfa_configs``', for ``AUDIT_RETENTION_DAYS``; revoking the token at Bridge
is what makes those copies worthless, and the disconnect sentence says so.

**It moves no money.**  A feed is a standing permission; what it fetches is
recorded by the sync door through the same recording path a CSV upload takes,
and that is where the leaf boundary for money sits.
"""

from app.extensions import db


class BankFeed(db.Model):
    """The owner's claimed SimpleFIN access URL, as ciphertext.

    Columns:
        user_id -- the owner.  ``FK auth.users.id ON DELETE CASCADE`` and
            UNIQUE (``uq_bank_feeds_user``): one feed per owner, the way
            ``auth.mfa_configs`` is one row per user.  Declared inline
            rather than through :class:`~app.models.mixins.UserScopedMixin`,
            which that mixin's docstring reserves for the NON-unique shape.
        access_url_encrypted -- the access URL under the field key, NOT NULL:
            a feed row that holds no URL is not a feed, and disconnecting is
            deleting the row rather than clearing the column.  ``LargeBinary``
            because a Fernet token is bytes, as ``totp_secret_encrypted`` is.
        claimed_at -- when the setup token was claimed, which is the instant
            this row was written: the claim door (the leaf after this table)
            inserts the row in the transaction that follows Bridge's answer,
            so the row's birth IS the claim and there is no ``created_at``
            beside it (CLAUDE.md rule 14: one fact, one home).
            Server-defaulted like every ``created_at`` here.  There is no
            ``updated_at`` because a feed is never edited: reconnecting is
            deleting and claiming again.

    No ``last_synced_at``: the newest ``simplefin`` import IS when the feed
    last synced (plan step ``bank_import:X-f6b-3`` derives it), and a stored
    copy would be a stale cache with no reconciler.
    """

    __tablename__ = "bank_feeds"
    __table_args__ = (
        # ONE feed per owner, structurally rather than by the claim door
        # checking first: a double-submitted claim cannot store two access
        # URLs for one owner, and "the owner's feed" has at most one answer.
        db.UniqueConstraint("user_id", name="uq_bank_feeds_user"),
        # The superkey ``fk_account_external_identities_feed_owner`` targets
        # (ruling **R-BI26**), so a declared mapping's owner IS its feed's
        # owner by key.  It constrains nothing on its own (``id`` is the
        # primary key); PostgreSQL requires a UNIQUE over exactly the
        # referenced columns -- ``uq_accounts_id_user``'s construction.
        db.UniqueConstraint("id", "user_id", name="uq_bank_feeds_id_user"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "auth.users.id", ondelete="CASCADE", name="fk_bank_feeds_user_id",
        ),
        nullable=False,
    )
    access_url_encrypted = db.Column(db.LargeBinary, nullable=False)
    claimed_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )

    def __repr__(self):
        # The ciphertext is deliberately absent: a repr reaches log lines
        # and tracebacks, and ciphertext under a key the same host holds
        # is not something to print.
        return f"<BankFeed id={self.id} user={self.user_id}>"
