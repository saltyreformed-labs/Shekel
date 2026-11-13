"""phase0 retarget anchor fk to no action deferrable

Phase 0 of the pay-period CRUD work (see
``docs/plans/implementation_plan_pay_period_crud.md``).  Retargets the
``budget.accounts.current_anchor_period_id`` foreign key from
``ON DELETE SET NULL`` to ``ON DELETE NO ACTION DEFERRABLE INITIALLY
IMMEDIATE``.

Why this change:

  * The column is ``NOT NULL`` (every account must point at a live
    anchor period -- E-19 / Commit 3, migration ``cfb15e782f86``).  The
    inherited ``SET NULL`` action is therefore a latent landmine: if any
    future code path deletes a pay period that an account anchors to,
    PostgreSQL would try to NULL a ``NOT NULL`` column.  The pay-period
    CRUD work introduces exactly such delete paths (truncate /
    regenerate), so the action is tightened here, up front, as a
    standalone prerequisite.
  * ``NO ACTION`` (not ``RESTRICT``) is chosen deliberately.  Both refuse
    the delete of an anchored period -- with the application-level anchor
    lock in ``pay_period_admin`` (Phase 1) and this FK, an anchored
    period cannot be deleted through any path.  But ``RESTRICT`` is
    checked immediately and CANNOT be deferred, whereas ``NO ACTION`` can.
    The future full-reset path (``reset_pay_periods``, Phase 3) must
    delete the old anchor period and re-point each account to a fresh
    period inside ONE transaction; it does so via
    ``SET CONSTRAINTS ... DEFERRED`` so the FK validates at commit (by
    which point every account points at a live new period).  Declaring
    the FK ``DEFERRABLE INITIALLY IMMEDIATE`` keeps the fail-fast
    immediate check for every ordinary path while letting only that one
    reset transaction opt into deferral.  This migration ships now even
    though the reset path lands later: a ``DEFERRABLE INITIALLY
    IMMEDIATE`` FK behaves identically to a plain immediate ``NO ACTION``
    FK for every non-reset statement, so it is inert until Phase 3 uses
    it, and shipping it here avoids a second FK migration later.

The constraint keeps its existing Alembic-default name
``accounts_current_anchor_period_id_fkey`` (one of the ~35 default names
the C-43 sweep deliberately retained -- the model declares no explicit
``name=``, so create_all and this migration agree).  A drop-and-recreate
is used because ``ON DELETE`` and ``DEFERRABLE`` are part of the
constraint definition and PostgreSQL has no in-place ``ALTER`` for them;
both statements run inside the migration's single transaction, so the
window during which the FK is absent is invisible to concurrent
statements.  A post-recreate assertion reads ``pg_constraint`` back and
fails loudly if the new action/deferrability did not take -- the same
verification value the C-43 ``assert_fk_ondelete`` helper provides,
inlined here for a single constraint (the C-43 idempotent drop/recreate
machinery is not reused: this FK is always present in the exact state the
chain leaves it, so there is no partial-apply/replay case to guard, and
the plain ``op`` idiom matches migration ``047bfed04987`` which owns this
FK).

Review: solo developer, 2026-06-13 (pay-period CRUD Phase 0).
Destructive (constraint drop-and-recreate per the destructive-migration
policy in ``docs/coding-standards.md``).  Downgrade is symmetric and
fully working: it restores ``ON DELETE SET NULL`` (not deferrable), the
exact pre-migration state installed by ``047bfed04987``.  No data is
read, written, or transformed in either direction.

Revision ID: d410f6b9caa3
Revises: 0dfd2537fecb
Create Date: 2026-06-13 16:30:03.581637
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'd410f6b9caa3'
down_revision = '0dfd2537fecb'
branch_labels = None
depends_on = None


# The anchor FK identity.  Name retained from the Alembic default
# (see module docstring); schema-qualified target is budget.pay_periods.
_FK_NAME = "accounts_current_anchor_period_id_fkey"
_SOURCE_TABLE = "accounts"
_SOURCE_COLUMN = "current_anchor_period_id"
_TARGET_TABLE = "pay_periods"
_TARGET_COLUMN = "id"
_SCHEMA = "budget"


def _anchor_fk_state(bind) -> tuple[str | None, bool | None]:
    """Return ``(confdeltype, condeferrable)`` for the anchor FK.

    Reads ``pg_constraint`` for the ``budget.accounts`` foreign key named
    :data:`_FK_NAME`.  ``confdeltype`` is the single-character ondelete
    code (``'n'`` = SET NULL, ``'a'`` = NO ACTION, ``'r'`` = RESTRICT,
    ``'c'`` = CASCADE); ``condeferrable`` is the boolean deferrability
    flag.  Returns ``(None, None)`` when the constraint is absent so the
    post-recreate assertion can distinguish "missing" from "present with
    the wrong action".

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing ``execute``.

    Returns:
        A ``(confdeltype, condeferrable)`` tuple, or ``(None, None)`` when
        the FK does not exist.
    """
    row = bind.execute(
        sa.text(
            "SELECT cn.confdeltype, cn.condeferrable "
            "FROM pg_constraint cn "
            "JOIN pg_class c ON c.oid = cn.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE cn.conname = :name AND n.nspname = :schema "
            "AND c.relname = :table AND cn.contype = 'f'"
        ),
        {"name": _FK_NAME, "schema": _SCHEMA, "table": _SOURCE_TABLE},
    ).first()
    if row is None:
        return (None, None)
    return (row[0], row[1])


def upgrade():
    """Retarget the anchor FK to NO ACTION DEFERRABLE INITIALLY IMMEDIATE.

    Drops and recreates ``accounts_current_anchor_period_id_fkey`` with
    the new action, then asserts the catalog reflects ``NO ACTION``
    (``confdeltype == 'a'``) and ``condeferrable IS TRUE``.  Raises
    ``RuntimeError`` if the recreate silently rendered the wrong clause.
    """
    op.drop_constraint(
        _FK_NAME, _SOURCE_TABLE, schema=_SCHEMA, type_="foreignkey",
    )
    op.create_foreign_key(
        _FK_NAME, _SOURCE_TABLE, _TARGET_TABLE,
        [_SOURCE_COLUMN], [_TARGET_COLUMN],
        source_schema=_SCHEMA, referent_schema=_SCHEMA,
        ondelete="NO ACTION", deferrable=True, initially="IMMEDIATE",
    )

    confdeltype, condeferrable = _anchor_fk_state(op.get_bind())
    if confdeltype != "a" or condeferrable is not True:
        raise RuntimeError(
            f"Post-recreate check failed for {_SCHEMA}.{_FK_NAME}: catalog "
            f"reports confdeltype={confdeltype!r}, condeferrable="
            f"{condeferrable!r}; expected ('a', True) for NO ACTION "
            f"DEFERRABLE.  Inspect with `\\d+ {_SCHEMA}.{_SOURCE_TABLE}` "
            f"and re-run after correcting the migration."
        )


def downgrade():
    """Restore the anchor FK to ON DELETE SET NULL (not deferrable).

    Reverts to the exact pre-migration state installed by
    ``047bfed04987``.  Pure DDL; no data is touched.
    """
    op.drop_constraint(
        _FK_NAME, _SOURCE_TABLE, schema=_SCHEMA, type_="foreignkey",
    )
    op.create_foreign_key(
        _FK_NAME, _SOURCE_TABLE, _TARGET_TABLE,
        [_SOURCE_COLUMN], [_TARGET_COLUMN],
        source_schema=_SCHEMA, referent_schema=_SCHEMA,
        ondelete="SET NULL",
    )
