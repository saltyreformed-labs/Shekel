"""The account's sign-out time ends only trusts: auth.users.trusts_ended_at

Plan#2171, task plan#3931, rulings **plan#3927** and **plan#3952**.
``auth.users.session_invalidated_at`` had two jobs: a session signed in before
it was refused, and a device trusted before it was trusted no longer.  Ruling
plan#3927 moved the first onto the sign-in records (migration
``764fd7dfb6d4``): every sign-out everywhere deletes the user's rows in
``auth.sign_ins``, and ``load_user`` reads no cutoff.  The column keeps the
second job alone and is renamed for it (ruling plan#3952).  Its one writer is
``app.services.sign_in_service.sign_out_everywhere`` and its one reader
``app.services.known_device_service._trusted``.

**A rename and nothing else.**  The type, the nullability (NULL: no trust of
the user's has ended yet) and every value carry over, so no trust starts or
ends at the release.  No index, constraint or view names the column.  The audit
trigger writes each row whole (``to_jsonb``), so the ``system.audit_log`` rows
of ``auth.users`` written before the release carry the old name and those
after it the new one.

**The downgrade renames it back, and loses nothing**: a rename carries every
value, so after the downgrade the column holds exactly what it held before the
upgrade.

Review: Josh, 2026-11-13 -- APPROVED: his pick of the name, ruling plan#3952,
is also his approval of this destructive migration.

Revision ID: dc6a49d5f720
Revises: 764fd7dfb6d4
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'dc6a49d5f720'
down_revision = '764fd7dfb6d4'
branch_labels = None
depends_on = None


#: The column's name before this revision and after it.  Stated once because
#: the upgrade and the downgrade take both.
_OLD_NAME = 'session_invalidated_at'
_NEW_NAME = 'trusts_ended_at'


def _rename(old: str, new: str) -> None:
    """Rename ``auth.users``' column *old* to *new*, its type and nullability kept."""
    op.alter_column(
        'users', old,
        new_column_name=new,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        schema='auth',
    )


def upgrade():
    """Rename the column for its one job, ending trusts."""
    _rename(_OLD_NAME, _NEW_NAME)


def downgrade():
    """Rename the column back."""
    _rename(_NEW_NAME, _OLD_NAME)
