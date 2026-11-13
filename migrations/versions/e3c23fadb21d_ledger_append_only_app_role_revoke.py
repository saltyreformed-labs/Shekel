"""Ledger append-only at the database tier: revoke UPDATE/DELETE from shekel_app

Revision ID: e3c23fadb21d
Revises: f3d6b1a8c2e4
Create Date: 2026-07-02 12:00:00.000000

The 2026-07-02 adversarial review's M1/R4
(``docs/audits/balance_architecture/adversarial_review_balance_architecture_2026-07-02.md``).

**What this closes.**  The posting ledger's append-only property was enforced
at two tiers that both stop short of raw SQL: the deferred balanced trigger
has no DELETE arm (CASCADE disposal would see a transient ``COUNT < 2``
mid-cascade) and its UPDATE arm checks only the NEW row's entry, and the ORM
immutability listeners fire only for ORM-mediated writes.  The review
demonstrated the consequence live: a raw-SQL DELETE of one leg of a balanced
entry succeeded silently and broke the trial balance by the leg's amount.
This migration makes append-only bind at the DATABASE tier for the runtime
role: ``REVOKE UPDATE, DELETE ON budget.journal_entries,
budget.account_postings FROM shekel_app``.  ``SELECT`` and ``INSERT`` are
untouched -- corrections are appended reversal entries, never edits -- so no
application code path changes (the app never legitimately UPDATEs or DELETEs
ledger rows; disposal is FK CASCADE, which PostgreSQL executes as the table
owner, not the client role).

**Verified on the dev stack before authoring** (all probes rolled back):
with the revoke in force and acting as ``shekel_app``, all four tamper forms
(UPDATE/DELETE on either table) fail with ``permission denied for table``;
a pay-period delete still cascades its 18 entries + 37 postings; a
transaction delete still SET-NULLs the entry back-link.

**Role guard.**  The REVOKE is wrapped in a ``pg_roles`` existence check
(the ``a5be2a99ea14`` pattern) so the migration succeeds on a cluster where
the production app role is not provisioned, such as a developer's own.  The
test cluster is not one: ``scripts/build_test_db_image.py`` creates the role
before the template is built (plan#2954).  The SQL lives in
``app.posting_infrastructure.apply_ledger_append_only_privileges`` -- shared
with ``scripts/init_database.py`` (the fresh-DB path stamps past this
migration) and ``scripts/build_test_template.py``.  ``scripts/init_db_role.sql``
carries the same REVOKE in psql form because ``entrypoint.sh`` re-runs its
blanket ``GRANT ... ON ALL TABLES`` on every container start, which would
otherwise silently re-open the hole this migration closes.

**Downgrade** re-grants ``UPDATE, DELETE`` on both tables (role-guarded),
restoring the exact pre-R4 blanket-DML posture.  NOTE: on a running
deployment the next container start re-runs ``init_db_role.sql``, whose
version determines the steady-state posture -- downgrading this migration
without also reverting that file leaves the revoke re-applied at the next
boot, by design (the file is the every-start source of truth).
"""

from alembic import op

from app.posting_infrastructure import (
    apply_ledger_append_only_privileges,
    remove_ledger_append_only_privileges,
)

# revision identifiers, used by Alembic.
revision = "e3c23fadb21d"
down_revision = "f3d6b1a8c2e4"
branch_labels = None
depends_on = None


def upgrade():
    """Revoke UPDATE/DELETE on the two ledger tables from ``shekel_app``.

    Idempotent and a no-op when the role does not exist; see the module
    docstring and :func:`app.posting_infrastructure.apply_ledger_append_only_privileges`.
    """
    apply_ledger_append_only_privileges(op.execute)


def downgrade():
    """Re-grant UPDATE/DELETE on the two ledger tables to ``shekel_app``.

    Restores the blanket-DML posture ``init_db_role.sql`` grants on every
    ``budget`` table; role-guarded and idempotent like the upgrade.
    """
    remove_ledger_append_only_privileges(op.execute)
