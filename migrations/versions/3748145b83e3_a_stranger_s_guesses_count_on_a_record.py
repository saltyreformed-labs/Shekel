"""A stranger's guesses count on a record of the email typed: auth.guess_records

Plan#2772, ruling **plan#3110**.  A stranger's wrong passwords and codes counted
on the account's row, which an email with no account does not have, so a wrong
password at a real email wrote and committed and one at a made-up email wrote
nothing: a real email's reply was slower by that write (measured 2026-11-09:
+6.39 ms a try), a clue to which emails have accounts.  The count and the lock
move to a record every email can have, named by the email's keyed digest
(``app.services.guess_record_service.email_digest``) and never by the address.

**What moves.**

* ``auth.guess_records``: an id, the digest (``uq_guess_records_email_digest``,
  and ``ck_guess_records_email_digest_sha256_hex`` refusing anything but 64
  lowercase hex characters, so the address itself can never be stored), the
  count (``ck_guess_records_failed_login_count_non_negative``), the lock's end,
  and the last try's time (``ix_guess_records_last_attempt_at``, the clean-up's
  filter).  Audited, ruling **plan#3113**: ``audit_guess_records``.
* Each account with a count or a lock gets its record, holding that count and
  lock.  Its last try's time is this migration's moment: the old row never
  held one, and the stamp only decides when the clean-up may delete a record
  that holds nothing; a carried count or open lock it keeps however quiet.
* ``auth.users.failed_login_count``, its CHECK
  ``ck_users_failed_login_count_non_negative``, and ``auth.users.locked_until``
  are deleted (CLAUDE.md rule 14: the count has one home).

**The digest needs ``SECRET_KEY``**, read from the environment as the running
app reads it, and only when an account has a count or a lock to carry.  The
deploy's entrypoint sets it before it migrates.

**The downgrade** puts the two columns back as migration ``d2883fc44071`` made
them, copies each account's count and lock back from the record its email
names, and deletes the table, and with it the records of emails no account
has, which have no home in the old columns.  Dropping the table writes no
audit row for its records; the trail keeps the rows their tries wrote.

Review: Josh, 2026-11-09 -- APPROVED, asked in those words: the upgrade, the
two columns deleted, and the downgrade, counts and locks carried both ways
(ruling plan#3115).

Revision ID: 3748145b83e3
Revises: b7e4c19a2d60
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa

from app.services.guess_record_service import email_digest


# Revision identifiers, used by Alembic.
revision = '3748145b83e3'
# The head of ``card/2772`` at authoring, measured (one head), as
# ``b7e4c19a2d60``'s note says: re-point that one at the measured head of
# ``dev`` as the last edit before the pull request, and this one stays on it.
down_revision = 'b7e4c19a2d60'
branch_labels = None
depends_on = None


_TABLE = 'guess_records'
_USERS_CHECK = 'ck_users_failed_login_count_non_negative'

#: Characters in the digest: SHA-256 as hex.
_DIGEST_LENGTH = 64


def upgrade():
    """Create the record, carry each account's count and lock into it, delete the columns."""
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_digest', sa.String(length=_DIGEST_LENGTH), nullable=False),
        sa.Column(
            'failed_login_count', sa.Integer(), nullable=False,
            server_default=sa.text('0'),
        ),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_guess_records'),
        sa.UniqueConstraint('email_digest', name='uq_guess_records_email_digest'),
        sa.CheckConstraint(
            f"email_digest ~ '^[0-9a-f]{{{_DIGEST_LENGTH}}}$'",
            name='ck_guess_records_email_digest_sha256_hex',
        ),
        sa.CheckConstraint(
            'failed_login_count >= 0',
            name='ck_guess_records_failed_login_count_non_negative',
        ),
        schema='auth',
    )
    op.create_index(
        'ix_guess_records_last_attempt_at', _TABLE, ['last_attempt_at'],
        schema='auth',
    )
    op.execute(f"DROP TRIGGER IF EXISTS audit_{_TABLE} ON auth.{_TABLE}")
    op.execute(
        f"CREATE TRIGGER audit_{_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON auth.{_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    bind = op.get_bind()
    carried = bind.execute(sa.text(
        "SELECT email, failed_login_count, locked_until FROM auth.users "
        "WHERE failed_login_count <> 0 OR locked_until IS NOT NULL"
    )).all()
    for email, count, locked_until in carried:
        bind.execute(
            sa.text(
                f"INSERT INTO auth.{_TABLE} "
                "(email_digest, failed_login_count, locked_until, last_attempt_at) "
                "VALUES (:digest, :count, :locked_until, now())"
            ),
            {
                'digest': email_digest(email), 'count': count,
                'locked_until': locked_until,
            },
        )

    op.drop_constraint(_USERS_CHECK, 'users', schema='auth', type_='check')
    op.drop_column('users', 'locked_until', schema='auth')
    op.drop_column('users', 'failed_login_count', schema='auth')


def downgrade():
    """Put the columns back, carry each account's count and lock back, delete the record."""
    op.add_column(
        'users',
        sa.Column(
            'failed_login_count', sa.Integer(),
            nullable=False, server_default='0',
        ),
        schema='auth',
    )
    op.add_column(
        'users',
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        schema='auth',
    )
    op.create_check_constraint(
        _USERS_CHECK, 'users', 'failed_login_count >= 0', schema='auth',
    )

    bind = op.get_bind()
    records = {
        digest: (count, locked_until)
        for digest, count, locked_until in bind.execute(sa.text(
            f"SELECT email_digest, failed_login_count, locked_until FROM auth.{_TABLE}"
        ))
    }
    if records:
        for user_id, email in bind.execute(sa.text("SELECT id, email FROM auth.users")):
            held = records.get(email_digest(email))
            if held is None:
                continue
            bind.execute(
                sa.text(
                    "UPDATE auth.users SET failed_login_count = :count, "
                    "locked_until = :locked_until WHERE id = :user_id"
                ),
                {'count': held[0], 'locked_until': held[1], 'user_id': user_id},
            )

    # Its trigger and index go with it.
    op.drop_table(_TABLE, schema='auth')
