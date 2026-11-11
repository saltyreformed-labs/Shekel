"""A passkey signs in on its own: auth.passkeys, and two banner kinds

Plan#2538, rulings **plan#3465** (a passkey signs in on its own) and
**plan#3467** (an account with a passkey signs in only by passkey).  Two
changes, one subject: the table the passkeys live in, and the two "was this
you?" banners a passkey's adding and removing raise.

**The table, ``auth.passkeys``.**

* ``user_id`` -- whose passkey.  ``ON DELETE CASCADE``: a deleted user's keys
  sign nobody in.  Indexed: the Security page, the closed-password-road
  question and the re-confirm each ask for one user's passkeys.
* ``credential_id`` -- the id the browser names the key by at every sign-in,
  which finds the account on its own, so ``uq_passkeys_credential_id`` makes
  one id name one row.  ``ck_passkeys_credential_id_length`` holds it to
  WebAuthn's 1 to 1023 bytes.
* ``public_key`` -- the COSE key the signature is checked against;
  ``ck_passkeys_public_key_not_empty`` refuses an empty one.
* ``sign_count`` -- the authenticator's counter, an unsigned 32-bit integer,
  so ``ck_passkeys_sign_count_range`` holds it to 0 through 4294967295.
* ``name`` -- the label the user gave it, never blank
  (``ck_passkeys_name_not_blank``).
* ``created_at``, and ``last_used_at`` (NULL until it first signs someone
  in), which is also the replay bound: the service refuses a challenge issued
  at or before it.
* **Absent on purpose** (CLAUDE.md rule 14): the authenticator's model id,
  its transports, a per-user WebAuthn handle (the service derives it from the
  user's id), a count of a user's passkeys.

**Audit.**  ``auth.passkeys`` joins ``AUDITED_TABLES``, so
``EXPECTED_TRIGGER_COUNT`` moves with it.  Adding a passkey adds a way into
the account and removing one is a DELETE, so the audit row records both.

**The banner kinds.**  ``ck_users_security_event_kind`` whitelists the kinds
of "was this you?" banner, and it gains ``passkey_added`` and
``passkey_removed``.  PostgreSQL cannot widen a CHECK in place, so the
upgrade drops it and creates it again with six kinds, in this migration's one
transaction, so no write runs between the two.

**The downgrade** drops the table, which forgets every passkey: each account
signs in with its password and code again, the road ruling plan#3467 closes
only while a passkey exists, so nothing is locked out.  It then puts the CHECK
back to four kinds, and a user row whose unacknowledged banner is a passkey
kind could not satisfy it, so that banner is cleared first (its kind and its
time together, as ``ck_users_security_event_at_kind_paired`` requires): the
passkey it reported is gone with the table anyway.

Review: Josh, 2026-11-11 (ruling plan#3497): the drop and re-create of
``ck_users_security_event_kind`` approved.

Revision ID: 66184d995b1c
Revises: 5f2c46a35a79
Create Date: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '66184d995b1c'
# The head of ``card/2538`` at authoring, measured (236 revisions, one head).
# Another lane landing a revision first makes two heads, which git merges
# cleanly and ``flask db upgrade`` refuses: re-point this at the measured head
# as the last edit before the pull request.
down_revision = '5f2c46a35a79'
branch_labels = None
depends_on = None


#: The table this migration creates, which ``app.audit_infrastructure`` also
#: lists.  Stated once because the CREATE, the trigger and the DROP take the
#: same name.
_NEW_TABLE = 'passkeys'

#: The banner-kind CHECK this migration rebuilds.
_KIND_CHECK = 'ck_users_security_event_kind'

#: The kinds the CHECK admitted before this migration.  Spelled here rather
#: than imported from ``app.utils.security_events`` so the migration means
#: the same thing whatever that module later holds.
_KINDS_BEFORE = (
    'password_changed',
    'mfa_enabled',
    'mfa_disabled',
    'backup_codes_regenerated',
)

#: The kinds this migration adds.
_PASSKEY_KINDS = ('passkey_added', 'passkey_removed')


def _kind_check_sql(kinds):
    """Render the CHECK clause admitting NULL or one of *kinds*.

    Args:
        kinds: The kind values the clause admits.

    Returns:
        The clause, in the form the column's original migration wrote it.
    """
    quoted = ", ".join(f"'{value}'" for value in kinds)
    return (
        "last_security_event_kind IS NULL OR "
        f"last_security_event_kind IN ({quoted})"
    )


def _rebuild_kind_check(kinds):
    """Drop the banner-kind CHECK and create it again admitting *kinds*.

    Args:
        kinds: The kind values the rebuilt CHECK admits.
    """
    op.drop_constraint(_KIND_CHECK, 'users', schema='auth', type_='check')
    op.create_check_constraint(
        _KIND_CHECK, 'users', _kind_check_sql(kinds), schema='auth',
    )


def upgrade():
    """Create the passkeys table with its trigger, and admit the two kinds."""
    op.create_table(
        _NEW_TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.LargeBinary(), nullable=False),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column(
            'sign_count', sa.BigInteger(), nullable=False,
            server_default=sa.text('0'),
        ),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_passkeys'),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'],
            name='fk_passkeys_user_id', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('credential_id', name='uq_passkeys_credential_id'),
        sa.CheckConstraint(
            'octet_length(credential_id) BETWEEN 1 AND 1023',
            name='ck_passkeys_credential_id_length',
        ),
        sa.CheckConstraint(
            'octet_length(public_key) > 0',
            name='ck_passkeys_public_key_not_empty',
        ),
        sa.CheckConstraint(
            'sign_count BETWEEN 0 AND 4294967295',
            name='ck_passkeys_sign_count_range',
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''", name='ck_passkeys_name_not_blank',
        ),
        schema='auth',
    )
    op.create_index(
        'ix_passkeys_user_id', _NEW_TABLE, ['user_id'],
        unique=False, schema='auth',
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

    _rebuild_kind_check(_KINDS_BEFORE + _PASSKEY_KINDS)


def downgrade():
    """Clear any passkey banner, narrow the kinds, and drop the table."""
    quoted = ", ".join(f"'{value}'" for value in _PASSKEY_KINDS)
    op.execute(
        "UPDATE auth.users SET last_security_event_kind = NULL, "
        "last_security_event_at = NULL "
        f"WHERE last_security_event_kind IN ({quoted})"
    )
    _rebuild_kind_check(_KINDS_BEFORE)
    op.drop_index('ix_passkeys_user_id', table_name=_NEW_TABLE, schema='auth')
    op.drop_table(_NEW_TABLE, schema='auth')
