"""A browser holds one record: auth.trusted_devices becomes auth.known_devices

Plan#2772, ruling **plan#2994**.  A browser that has finished a sign-in holds
one record and one cookie, and "Trust this device" is one fact on that record.
So the table now holds browsers nobody trusted, and its name says so.

**What moves.**

* The table, its four named constraints, its index, its sequence and its audit
  trigger take the new name.  A trigger stays on a renamed table under its old
  name, and the deploy's census counts by name, so it is dropped and created.
  ``system.audit_log`` keeps its past rows under ``trusted_devices``: a history
  is not rewritten.
* ``last_signed_in_at`` -- the last finished sign-in, which the browser's known
  standing is judged from (ruling plan#2995).  NOT NULL, in the three steps a
  populated table takes.  Every row was written by a sign-in that finished at
  ``trusted_at``, so that is the derivation, and it is exact for a browser that
  has not signed in since; one that has is known for less time than it could be
  until its next sign-in, which renews it.
* ``failed_login_count`` -- this browser's wrong passwords in a row (ruling
  plan#2996).  Zero for every row: no count exists to carry over.
  ``ck_known_devices_failed_login_count_non_negative`` pins its lower bound.
* ``trusted_at`` loses NOT NULL: a sign-in without the box ticked writes no
  trust.

**Production.**  Its rows and their cookies keep working: the cookie's name does
not change, and each row holds a trust.  So each such browser still skips the
code while its thirty days last, and is known from the first day.

**The downgrade deletes the rows holding no trust**, which the old table cannot
hold, so those browsers are strangers until their next sign-in on the upgraded
code.  The rows holding a trust return as they were, each browser's cookie still
naming its row.  The DELETE runs under the audit trigger, so the trail records it.

Review: Josh, 2026-11-06 -- APPROVED, asked in those words: the rename, the trust
date allowed to be empty, and what the downgrade deletes (plan#2772).

Revision ID: b7e4c19a2d60
Revises: 0fe9c332b9b0
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'b7e4c19a2d60'
# The head of ``dev`` when ``card/2772`` took it in for its pull request,
# measured 2026-11-10 at ``94c3b0649`` (one head; ``897a6663d221`` at
# authoring, before ``6740ce13e174`` and ``0fe9c332b9b0`` landed).  Another
# lane landing a revision first makes two heads, which git merges cleanly and
# ``flask db upgrade`` refuses: re-point this at the measured head as the last
# edit before the pull request.
down_revision = '0fe9c332b9b0'
branch_labels = None
depends_on = None


_OLD_TABLE = 'trusted_devices'
_NEW_TABLE = 'known_devices'

#: Each named constraint's suffix, the one part the rename keeps.
_CONSTRAINT_SUFFIXES = (
    ('pk', ''),
    ('fk', '_user_id'),
    ('uq', '_token_hash'),
    ('ck', '_token_hash_sha256_hex'),
)

_NEW_CHECK = 'ck_known_devices_failed_login_count_non_negative'


def _rename_everything(old: str, new: str) -> None:
    """Rename the table from *old* to *new*, with all that carries its name.

    Args:
        old: The table's current name.
        new: The name it takes.
    """
    op.rename_table(old, new, schema='auth')
    for prefix, suffix in _CONSTRAINT_SUFFIXES:
        op.execute(
            f"ALTER TABLE auth.{new} RENAME CONSTRAINT "
            f"{prefix}_{old}{suffix} TO {prefix}_{new}{suffix}"
        )
    op.execute(f"ALTER INDEX auth.ix_{old}_user_id RENAME TO ix_{new}_user_id")
    op.execute(f"ALTER SEQUENCE auth.{old}_id_seq RENAME TO {new}_id_seq")
    op.execute(f"DROP TRIGGER IF EXISTS audit_{old} ON auth.{new}")
    op.execute(
        f"CREATE TRIGGER audit_{new} "
        f"AFTER INSERT OR UPDATE OR DELETE ON auth.{new} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Rename the table and give it the known browser's facts."""
    _rename_everything(_OLD_TABLE, _NEW_TABLE)

    op.add_column(
        _NEW_TABLE,
        sa.Column('last_signed_in_at', sa.DateTime(timezone=True), nullable=True),
        schema='auth',
    )
    op.execute(f"UPDATE auth.{_NEW_TABLE} SET last_signed_in_at = trusted_at")
    unfilled = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM auth.{_NEW_TABLE} WHERE last_signed_in_at IS NULL"
    )).scalar()
    if unfilled:
        raise RuntimeError(
            f"{unfilled} row(s) kept no last sign-in after the backfill: "
            f"SELECT id FROM auth.{_NEW_TABLE} WHERE last_signed_in_at IS NULL"
        )
    op.alter_column(
        _NEW_TABLE, 'last_signed_in_at', schema='auth', nullable=False,
        existing_type=sa.DateTime(timezone=True),
    )

    op.add_column(
        _NEW_TABLE,
        sa.Column(
            'failed_login_count', sa.Integer(), nullable=False,
            server_default=sa.text('0'),
        ),
        schema='auth',
    )
    op.create_check_constraint(
        _NEW_CHECK, _NEW_TABLE, 'failed_login_count >= 0', schema='auth',
    )

    op.alter_column(
        _NEW_TABLE, 'trusted_at', schema='auth', nullable=True,
        existing_type=sa.DateTime(timezone=True),
    )


def downgrade():
    """Delete the rows holding no trust and restore the trusted-device table."""
    op.execute(f"DELETE FROM auth.{_NEW_TABLE} WHERE trusted_at IS NULL")
    op.alter_column(
        _NEW_TABLE, 'trusted_at', schema='auth', nullable=False,
        existing_type=sa.DateTime(timezone=True),
    )
    op.drop_constraint(_NEW_CHECK, _NEW_TABLE, schema='auth', type_='check')
    op.drop_column(_NEW_TABLE, 'failed_login_count', schema='auth')
    op.drop_column(_NEW_TABLE, 'last_signed_in_at', schema='auth')
    _rename_everything(_NEW_TABLE, _OLD_TABLE)
