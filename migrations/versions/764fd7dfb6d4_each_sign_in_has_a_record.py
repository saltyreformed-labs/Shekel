"""Each sign-in has a record on the server: auth.sign_ins

Plan#2171, ruling **plan#3925**.  A session cookie is signed, but the server
kept nothing of it, so signing out edited only the cookie the browser sent back
and a copy taken earlier kept working.  Each finished sign-in now writes a row
here, the session cookie carries the row's token, ``load_user`` refuses a
session whose token names no live row of its user, and signing out deletes the
row.

**The columns, and what is absent on purpose.**

* ``user_id`` -- whose sign-in.  ``ON DELETE CASCADE``: a deleted user's
  sign-ins sign nobody in.  Indexed, because the purge at each sign-in asks for
  one user's rows.
* ``token_hash`` -- the SHA-256, as lowercase hex, of the 256-bit random token
  the session cookie carries.  The token itself is never stored.
  ``uq_sign_ins_token_hash`` makes a hash name one sign-in, and
  ``ck_sign_ins_token_hash_sha256_hex`` refuses anything that is not 64 hex
  digits, so a writer that stored the raw token is refused here.
* ``signed_in_at`` -- the sign-in's instant, the one its browser's record was
  renewed at (ruling plan#3069).  The only time column, and never updated.  A
  row is live while this is less than thirty days ago, derived at read time.
* **No ended-at column, no revoked flag and no expiry** (CLAUDE.md rule 14): a
  sign-in ends by its row being deleted, or by its age.

**Audit.**  ``auth.sign_ins`` joins ``AUDITED_TABLES``, so
``EXPECTED_TRIGGER_COUNT`` moves with ``len(AUDITED_TABLES)``.  A sign-out is
a DELETE, and the audit row is what records that it happened.  The rows carry
the hash, which is no more use there than here without the token.

**Production.**  Every session open at the release carries no token, so each
browser signs in once then, as at ruling plan#3069's release.

**The downgrade drops the table, and what that costs is stated rather than
guarded**: every session signed in on the upgraded code carries only a token
the old code never reads and lacks the stamp it requires, so each browser signs
in once more.  Nothing else reads the table, and no money is involved.

Revision ID: 764fd7dfb6d4
Revises: c3a7e5d91f42
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '764fd7dfb6d4'
# The head of ``card/2171`` at authoring, measured 2026-11-13 at ``03918927a``
# (one head).  Another lane landing a revision first makes two heads, which git
# merges cleanly and ``flask db upgrade`` refuses: re-point this at the
# measured head as the last edit before the pull request.
down_revision = 'c3a7e5d91f42'
branch_labels = None
depends_on = None


#: The table this migration creates, which ``app.audit_infrastructure`` also
#: lists.  Stated once because the CREATE, the trigger and the DROP take the
#: same name.
_NEW_TABLE = 'sign_ins'

_INDEX = 'ix_sign_ins_user_id'


def upgrade():
    """Create the sign-in table and attach its audit trigger."""
    op.create_table(
        _NEW_TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('signed_in_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_sign_ins'),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'],
            name='fk_sign_ins_user_id', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('token_hash', name='uq_sign_ins_token_hash'),
        # Only a hash is ever stored: a raw token is refused.
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name='ck_sign_ins_token_hash_sha256_hex',
        ),
        schema='auth',
    )
    op.create_index(
        _INDEX, _NEW_TABLE, ['user_id'], unique=False, schema='auth',
    )

    # ── Attach the audit trigger ─────────────────────────────────────────
    #
    # Named ``audit_<table>``, the name the deploy's trigger census counts
    # by.  The shared ``system.audit_trigger_func`` already exists from the
    # rebuild migration; DROP IF EXISTS first so a re-run is idempotent.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_NEW_TABLE} ON auth.{_NEW_TABLE}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON auth.{_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop the sign-in table (its index and audit trigger go with it)."""
    op.drop_index(_INDEX, table_name=_NEW_TABLE, schema='auth')
    op.drop_table(_NEW_TABLE, schema='auth')
