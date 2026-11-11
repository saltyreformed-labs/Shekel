"""An add of a passkey in flight lives on the server: auth.passkey_adds

Plan#2538, ruling **plan#3543**: between the three acts of adding a passkey
(ruling plan#3525) the add waits in this table, one row per account, and not
in the session's signed cookie, which a browser drops past about 4 KB.

**The table, ``auth.passkey_adds``.**

* ``id`` -- what the session's state names the add by.  A newer add replaces
  the account's row under a new id, so an older add's state names nothing.
* ``user_id`` -- whose add.  ``uq_passkey_adds_user_id`` keeps one per
  account, and its index serves the lookup by account.  ``ON DELETE
  CASCADE``: a deleted user's add goes with them.
* ``challenge`` and ``issued_at`` -- the challenge the act in flight must be
  answered to and when it was issued, the registration's and then the
  sign-in's.  ``ck_passkey_adds_challenge_length`` holds the challenge to at
  least the 16 bytes WebAuthn advises.
* ``credential_id``, ``public_key``, ``sign_count``, ``name`` -- the passkey
  the registration made, NULL until it is made, all four together
  (``ck_passkey_adds_made_together``), each held to the CHECK
  ``auth.passkeys`` holds it to.

**Audit.**  ``auth.passkey_adds`` joins ``AUDITED_TABLES``, so
``EXPECTED_TRIGGER_COUNT`` moves with it.

**The downgrade** drops the table: an add in flight is forgotten, so a person
mid-add is asked to begin again, and nothing else changes.  Every passkey
already kept stays in ``auth.passkeys``.

Revision ID: 1ea44adfeec8
Revises: 66184d995b1c
Create Date: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '1ea44adfeec8'
down_revision = '66184d995b1c'
branch_labels = None
depends_on = None


#: The table this migration creates, which ``app.audit_infrastructure`` also
#: lists.  Stated once because the CREATE, the trigger and the DROP take the
#: same name.
_NEW_TABLE = 'passkey_adds'


def upgrade():
    """Create the passkey adds table and attach its audit trigger."""
    op.create_table(
        _NEW_TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('challenge', sa.LargeBinary(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('credential_id', sa.LargeBinary(), nullable=True),
        sa.Column('public_key', sa.LargeBinary(), nullable=True),
        sa.Column('sign_count', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=60), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_passkey_adds'),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'],
            name='fk_passkey_adds_user_id', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('user_id', name='uq_passkey_adds_user_id'),
        sa.CheckConstraint(
            'octet_length(challenge) >= 16',
            name='ck_passkey_adds_challenge_length',
        ),
        sa.CheckConstraint(
            'num_nulls(credential_id, public_key, sign_count, name) IN (0, 4)',
            name='ck_passkey_adds_made_together',
        ),
        sa.CheckConstraint(
            'octet_length(credential_id) BETWEEN 1 AND 1023',
            name='ck_passkey_adds_credential_id_length',
        ),
        sa.CheckConstraint(
            'octet_length(public_key) > 0',
            name='ck_passkey_adds_public_key_not_empty',
        ),
        sa.CheckConstraint(
            'sign_count BETWEEN 0 AND 4294967295',
            name='ck_passkey_adds_sign_count_range',
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''", name='ck_passkey_adds_name_not_blank',
        ),
        schema='auth',
    )

    # ── Attach the audit trigger ─────────────────────────────────────────
    #
    # Named ``audit_<table>``, the name the deploy's trigger census counts
    # by.  The shared ``system.audit_trigger_func`` already exists from the
    # rebuild migration; DROP IF EXISTS first so a re-run is idempotent.
    op.execute(f"DROP TRIGGER IF EXISTS audit_{_NEW_TABLE} ON auth.{_NEW_TABLE}")
    op.execute(
        f"CREATE TRIGGER audit_{_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON auth.{_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def downgrade():
    """Drop the table, forgetting any add in flight; its trigger goes with it."""
    op.drop_table(_NEW_TABLE, schema='auth')
