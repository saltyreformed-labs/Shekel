"""A trusted device skips only the two-factor code: auth.trusted_devices

Plan#2173, ruling **plan#2406**.  One table, one subject: a browser on which a
user typed a two-factor code and asked to be trusted, so that signing in there
again takes the password and no code.

**The columns, and the two that are absent on purpose.**

* ``user_id`` -- whose device.  ``ON DELETE CASCADE``: a device of a deleted
  user trusts nothing.  Indexed, because the sign-in, the settings list and
  the purge each ask for one user's devices.
* ``token_hash`` -- the SHA-256, as lowercase hex, of the 256-bit random token
  the browser holds in its cookie.  The token itself is never stored.
  ``uq_trusted_devices_token_hash`` makes a hash name one device, and
  ``ck_trusted_devices_token_hash_sha256_hex`` refuses anything that is not 64
  hex digits, so a writer that stored the raw token (43 URL-safe characters)
  is refused here.
* ``user_agent`` -- only to name the device in Settings > Security, cut to 255
  characters by the service before it is written.
* ``trusted_at`` -- when the code was typed.  The only time column: the row is
  never updated, so it is also when the row was made.
* **No expiry column and no revoked flag** (CLAUDE.md rule 14).  A device is
  live while ``trusted_at`` is within 30 days of now AND not before the user's
  ``auth.users.session_invalidated_at``; both are derived at read time
  (``app.services.trusted_device_service._live``), so every writer of that
  column already forgets every trusted device with no new write.

**Audit.**  ``auth.trusted_devices`` joins ``AUDITED_TABLES``, so
``EXPECTED_TRIGGER_COUNT`` moves with ``len(AUDITED_TABLES)``.  Trusting a
device is a security decision and forgetting one is a DELETE, so the audit row
is what records that either happened.  The rows carry the hash, which is no
more use there than here without the token.

**The downgrade drops the table, and what that costs is stated rather than
guarded**: every trusted device is forgotten, so each user types a code at
their next sign-in.  Nothing else reads the table, and no money is involved.

Revision ID: 69634349bdce
Revises: 1712c506ee99
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '69634349bdce'
# The head of ``card/2173`` at authoring, measured (221 revisions, one head).
# Another lane landing a revision first makes two heads, which git merges
# cleanly and ``flask db upgrade`` refuses: re-point this at the measured head
# as the last edit before the pull request.
down_revision = '1712c506ee99'
branch_labels = None
depends_on = None


#: The table this migration creates, which ``app.audit_infrastructure`` also
#: lists.  Stated once because the CREATE, the trigger and the DROP take the
#: same name.
_AUDITED_NEW_TABLE = 'trusted_devices'


def upgrade():
    """Create the trusted-device table and attach its audit trigger."""
    op.create_table(
        _AUDITED_NEW_TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_agent', sa.String(length=255), nullable=False),
        sa.Column('trusted_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_trusted_devices'),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'],
            name='fk_trusted_devices_user_id', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('token_hash', name='uq_trusted_devices_token_hash'),
        # Only a hash is ever stored: a raw token is refused.
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name='ck_trusted_devices_token_hash_sha256_hex',
        ),
        schema='auth',
    )
    op.create_index(
        'ix_trusted_devices_user_id', _AUDITED_NEW_TABLE, ['user_id'],
        unique=False, schema='auth',
    )

    # ── Attach the audit trigger ─────────────────────────────────────────
    #
    # Named ``audit_<table>``, the name the deploy's trigger census counts
    # by.  The shared ``system.audit_trigger_func`` already exists from the
    # rebuild migration; DROP IF EXISTS first so a re-run is idempotent.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_AUDITED_NEW_TABLE} "
        f"ON auth.{_AUDITED_NEW_TABLE}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_AUDITED_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON auth.{_AUDITED_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop the trusted-device table (its index and audit trigger go with it)."""
    op.drop_index(
        'ix_trusted_devices_user_id',
        table_name=_AUDITED_NEW_TABLE, schema='auth',
    )
    op.drop_table(_AUDITED_NEW_TABLE, schema='auth')
