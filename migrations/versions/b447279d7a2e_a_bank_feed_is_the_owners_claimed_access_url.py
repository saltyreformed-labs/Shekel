"""A bank feed is the owner's claimed access URL, as ciphertext: budget.bank_feeds

Plan step ``bank_import:X-f6b-2``, ruling **bank_import:R-BI12**.  One table,
one subject: the owner's standing permission to fetch their bank's lines
without a file -- the SimpleFIN access URL, claimed once from a setup token
and kept under the field-encryption key.  MOVES NO MONEY: nothing here is a
figure, a day or a posting; what a feed fetches is recorded by the sync door
through the recording path a CSV upload already takes.

This revision lands the TABLE; the claim door that writes a row, the
``simplefin`` source row it maps accounts under, and the sync that reads it
are the leaves of the same step after it.

**Why a row per OWNER** (ruling **R-BI12**, the developer's option text): "New
table budget.bank_feeds (owner, access URL as Fernet ciphertext, claimed_at).
You paste a setup token on the statements page; the app claims it, lists the
Bridge accounts, you map each to a Shekel account (the existing
account_external_identities row, source simplefin).  Disconnect deletes the
row; revoke at Bridge is yours.  rotate_totp_key.py extends to the column;
the log redaction list gains it; decrypted only in the sync and the claim
door.  Exposure class = the TOTP secret's (dump + key), token read-only and
revocable."  Rejected there: a docker secret file claimed by hand (one global
feed, no in-app connect or disconnect), the ``.env`` channel, a plaintext
column.

**The columns.**

* ``user_id`` -- the owner; ``fk_bank_feeds_user_id`` onto ``auth.users``,
  ``ON DELETE CASCADE`` like every user-owned row's.  UNIQUE under
  ``uq_bank_feeds_user``: ONE feed per owner, structurally rather than by the
  claim door checking first, so a double-submitted claim cannot store two
  access URLs and "the owner's feed" has at most one answer.
* ``access_url_encrypted`` -- ``BYTEA NOT NULL``, the URL written through
  ``app.utils.field_encryption.encrypt_secret`` under ``FIELD_ENCRYPTION_KEY``,
  the cipher and key ``auth.mfa_configs.totp_secret_encrypted`` is under;
  ``scripts/rotate_field_key.py`` re-wraps both columns.  NOT NULL because a
  feed row holding no URL is not a feed: disconnecting deletes the row.
* ``claimed_at`` -- ``TIMESTAMPTZ NOT NULL DEFAULT now()``: when the setup
  token was claimed, which is the instant the row was written, because the
  claim door (the next leaf) inserts the row in the transaction that follows
  Bridge's answer.
  It is the row's ``created_at`` under the name of the act, and there is no
  second timestamp beside it (CLAUDE.md rule 14).

**No index beyond the keys.**  The only reads the design gives it are
"this owner's feed" (``uq_bank_feeds_user`` is that index) and "every feed"
(the nightly door, a full scan of a table with one row per owner).

**Audit.**  ``budget.bank_feeds`` joins ``app.audit_infrastructure
.AUDITED_TABLES``, so ``EXPECTED_TRIGGER_COUNT`` moves with
``len(AUDITED_TABLES)`` and the entrypoint health check enumerates one more
trigger.  The trigger writes ``to_jsonb(NEW)`` / ``to_jsonb(OLD)`` on every
INSERT, UPDATE and DELETE, ciphertext included, exactly as it does for
``mfa_configs`` -- the same exposure class the ruling accepts -- so a claim,
a rotation and a disconnect each leave the ciphertext readable under the key
in ``system.audit_log`` for ``AUDIT_RETENTION_DAYS``; revoking the token at
Bridge is what makes those copies worthless.

**The downgrade drops the table, and what that costs is stated rather than
guarded.**  It destroys the owner's claimed access URL, so the feed must be
connected again by pasting a fresh setup token (Bridge issues one on request;
the old token stays claimed there until revoked).  Nothing derived from a
feed survives it, because nothing is derived from one: the imports it
recorded stand on their own with their declared windows, and the account
mapping stays in ``account_external_identities``.  The dropped rows remain
readable in ``system.audit_log`` (``table_name = 'bank_feeds'``).  It is NOT
refused the way ``af07125d00f1``'s is: that downgrade would have destroyed a
placed level, which moves money; this one destroys a permission.

Revision ID: b447279d7a2e
Revises: 5f2c46a35a79
Create Date: 2026-09-19 21:50
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'b447279d7a2e'
# ``5f2c46a35a79`` (the per-user tax tables' drop, plan#1142) is the single
# head of ``dev`` at ``fd3f17810``.  This file was authored against
# ``22b23085394d`` (``recurrence:R20``) and re-parented at each of the
# lane's dev merges as the head moved: onto ``45f10b870c8b`` at the second,
# onto ``5f2c46a35a79`` when card/1240 took the held branch in (task
# plan#3333), rather than leave the chain with two heads
# (``alembic.script.ScriptDirectory.get_heads()`` on the merged tree -> one
# entry, ``6efa1fb46af8``).  The parent is spelled on THREE lines of this
# file (the docstring's ``Revises:``, this comment, the assignment below)
# and nowhere else in the tree.
down_revision = '5f2c46a35a79'
branch_labels = None
depends_on = None


#: The table this migration creates that ``app.audit_infrastructure`` also
#: lists.  Stated once because the CREATE and the DROP take the same name, and
#: a second spelling is how one of them comes to be missing it.
_AUDITED_NEW_TABLE = 'bank_feeds'


def upgrade():
    """Create the feed table and attach its audit trigger."""
    op.create_table(
        _AUDITED_NEW_TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        # The access URL under FIELD_ENCRYPTION_KEY; a Fernet token is bytes.
        sa.Column('access_url_encrypted', sa.LargeBinary(), nullable=False),
        # The claim IS the row's birth: server-defaulted like every
        # created_at here, named for the act.
        sa.Column(
            'claimed_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'],
            name='fk_bank_feeds_user_id', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # ONE feed per owner, structurally rather than by a writer checking
        # first.
        sa.UniqueConstraint('user_id', name='uq_bank_feeds_user'),
        schema='budget',
    )

    # ── Attach the audit trigger ─────────────────────────────────────────
    #
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).  The
    # shared ``system.audit_trigger_func`` already exists from the rebuild
    # migration; DROP IF EXISTS first so a re-run is idempotent.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_AUDITED_NEW_TABLE} "
        f"ON budget.{_AUDITED_NEW_TABLE}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_AUDITED_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{_AUDITED_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop the feed table (its audit trigger goes with it)."""
    op.drop_table(_AUDITED_NEW_TABLE, schema='budget')
