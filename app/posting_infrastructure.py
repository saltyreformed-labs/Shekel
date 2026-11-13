"""Shared definitions for the posting ledger's database-tier integrity kit.

Two pieces live here: the balanced-journal constraint trigger and the
append-only privilege posture for the least-privilege ``shekel_app`` role.

**The balanced-journal trigger.**  The double-entry posting ledger
(Build-Order Step 2, :mod:`app.models.journal_entry`) requires a
per-journal-entry invariant that PostgreSQL cannot express as a row-level
CHECK: every entry's posting legs must ``SUM(amount) = 0`` and there must be
``COUNT(*) >= 2`` of them.  Both are cross-row facts about an entry, so they
live in a **deferred constraint trigger** that validates at COMMIT -- after
all of an entry's legs are inserted -- rather than after the first leg.

**The append-only privilege posture** (the 2026-07-02 adversarial review's
M1/R4).  The trigger deliberately has no DELETE arm (CASCADE disposal would
see a transient ``COUNT < 2`` mid-cascade), and its UPDATE arm checks only
the NEW row's entry -- so a raw-SQL single-leg DELETE, or a raw-SQL reparent
of a leg, silently breaks the trial balance (demonstrated live in the
review).  The ORM immutability listeners do not fire for raw SQL either.
:func:`apply_ledger_append_only_privileges` closes that surface at the
database tier: ``REVOKE UPDATE, DELETE`` on ``budget.journal_entries`` and
``budget.account_postings`` from ``shekel_app``, so the running app -- and an
attacker who pivots into its role -- can only ever APPEND.  Referential
actions are unaffected: PostgreSQL executes CASCADE / SET NULL as the table
owner, not the client role (verified live on the dev stack: a pay-period
delete as ``shekel_app`` cascaded 18 entries + 37 postings; a transaction
delete nulled the entry back-link -- both with the revoke in force).

Three callers must produce identical trigger infrastructure, exactly as
:mod:`app.audit_infrastructure` keeps the audit trigger in lock-step across
its three callers:

1. The Alembic migration that creates ``budget.account_postings`` (Commit 3
   of the posting-ledger plan) -- ``apply_posting_infrastructure(op.execute)``
   after the table is created.
2. ``scripts/init_database.py``, which initialises a fresh database via
   ``db.create_all()`` + an Alembic ``stamp`` -- a path that bypasses the
   migration chain.  ``create_all`` materialises the ``account_postings``
   table (a SQLAlchemy model) but NOT the trigger or its function, which are
   raw SQL outside the model registry, so this caller must apply them
   explicitly (the same gap ``audit_infrastructure`` fills for the audit
   trigger).
3. ``scripts/build_test_template.py``, which builds the
   ``shekel_test_template`` database the test suite clones per worker.  It
   runs the migration chain (which already applies this infrastructure) and
   then RE-applies it idempotently so the latest in-code trigger definition
   wins over any migration-frozen state -- mirroring its idempotent
   re-application of ``apply_audit_infrastructure``.

Centralising the SQL here is the only way to keep those three call sites
from drifting.

The privilege posture has the same three Python callers (the Alembic
migration for existing databases, ``scripts/init_database.py`` for the
fresh-DB path that stamps past the migration chain, and
``scripts/build_test_template.py`` for the test template) **plus one psql
caller that cannot import this module**: ``scripts/init_db_role.sql``, which
``entrypoint.sh`` re-runs on EVERY container start and whose blanket
``GRANT ... ON ALL TABLES IN SCHEMA budget`` would otherwise silently
re-grant ``UPDATE, DELETE`` on the ledger tables at each restart, undoing a
one-time migration.  That file carries the same targeted REVOKE in SQL form
with a cross-reference here; a change to either MUST be mirrored in the
other.

**Caller contract: the table must already exist.**  Unlike
:func:`app.audit_infrastructure.apply_audit_infrastructure`, this function is
NOT guarded against a missing target table, and deliberately so: the trigger
function's body references ``budget.account_postings``, and PostgreSQL's
default ``check_function_bodies = on`` validates that reference at
``CREATE FUNCTION`` time, so applying this infrastructure before the table
exists fails loudly (the right signal).  All three callers above apply it
only after the table is materialised.  There is no early "rebuild" migration
that runs it against a not-yet-created table (the case that forces the audit
module's ``pg_class`` existence guard), so no guard is needed here.
"""

from __future__ import annotations

from typing import Callable


# Name of the trigger function and the constraint trigger.  The trigger name
# intentionally uses the ``ck_`` prefix (not ``audit_``) so the audit
# trigger-count check, which counts triggers whose name starts ``audit_``
# (``app.audit_infrastructure.AUDIT_TRIGGER_COUNT_SQL``), never counts it.
_TRIGGER_FUNCTION_NAME = "budget.assert_journal_entry_balanced"
_TRIGGER_NAME = "ck_account_postings_balanced"
_POSTINGS_TABLE = "budget.account_postings"


# CREATE OR REPLACE FUNCTION is idempotent: it atomically swaps the body if a
# previous version exists.  The function recomputes the owning entry's leg
# sum and count from scratch on every fire, so it is correct whether invoked
# for the first leg or the last.  It returns NULL because an AFTER trigger's
# return value is ignored.
_CREATE_TRIGGER_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION_NAME}()
RETURNS TRIGGER AS $$
DECLARE
    -- Unbounded NUMERIC, not NUMERIC(12,2): this variable accumulates a SUM
    -- of NUMERIC(12,2) legs, and an accumulator must be wider than its
    -- operands.  A balanced entry sums to 0, but on the rejection path an
    -- imbalance can approach the per-leg maximum, and assigning that back
    -- into a NUMERIC(12,2) would raise an opaque overflow instead of the
    -- clean "sum to %" message below.  (Overflow would still fail closed --
    -- it never false-passes -- but the message would be unhelpful.)
    v_sum   NUMERIC;
    v_count INTEGER;
BEGIN
    -- Recompute the owning entry's leg total and count.  Deferred to
    -- COMMIT, so by the time this runs every leg of NEW's entry is present.
    SELECT COALESCE(SUM(amount), 0), COUNT(*)
      INTO v_sum, v_count
      FROM budget.account_postings
     WHERE journal_entry_id = NEW.journal_entry_id;

    IF v_count < 2 THEN
        RAISE EXCEPTION
            'journal entry % has % posting(s); >= 2 required',
            NEW.journal_entry_id, v_count;
    END IF;

    IF v_sum <> 0 THEN
        RAISE EXCEPTION
            'journal entry % postings sum to %; must be 0',
            NEW.journal_entry_id, v_sum;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


# PostgreSQL has no CREATE CONSTRAINT TRIGGER IF NOT EXISTS, so the apply
# function pairs a guarded DROP with a fresh CREATE to stay idempotent.
_DROP_TRIGGER_SQL = (
    f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON {_POSTINGS_TABLE}"
)

# A CONSTRAINT trigger (so it can be DEFERRABLE) firing AFTER INSERT OR
# UPDATE -- never on DELETE.  Excluding DELETE is load-bearing: the CASCADE
# disposal path (a tenancy delete cascades through journal_entries into
# account_postings) would otherwise see a transient ``COUNT < 2`` mid-cascade
# and abort a legitimate disposal.  INITIALLY DEFERRED moves the check to
# COMMIT so a multi-leg entry is validated as a whole, not after its first
# leg.  Including UPDATE catches a raw-SQL amount edit that would unbalance an
# entry (no legitimate UPDATE path exists -- postings are append-only).
_CREATE_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_TRIGGER_NAME} "
    f"AFTER INSERT OR UPDATE ON {_POSTINGS_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    f"FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION_NAME}()"
)


def apply_posting_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently materialise the balanced-journal constraint trigger.

    Executes, in order:

    1. ``CREATE OR REPLACE FUNCTION budget.assert_journal_entry_balanced``
       -- the per-entry sum-to-zero / at-least-two-legs check.
    2. ``DROP TRIGGER IF EXISTS`` followed by ``CREATE CONSTRAINT TRIGGER``
       attaching that function to ``budget.account_postings`` as an
       ``AFTER INSERT OR UPDATE``, ``DEFERRABLE INITIALLY DEFERRED`` trigger.

    All statements are idempotent: running the function twice in a row is
    indistinguishable from running it once (``CREATE OR REPLACE FUNCTION``
    swaps the body; ``DROP TRIGGER IF EXISTS`` + ``CREATE`` re-pins the
    trigger).  The caller MUST have already created ``budget.account_postings``
    (see the module docstring's caller contract).

    Args:
        executor: Single-argument callable that accepts a SQL string and
            runs it.  Pass ``op.execute`` from inside an Alembic migration;
            pass ``lambda s: session.execute(text(s))`` from inside a
            SQLAlchemy session.  Errors propagate -- the caller owns the
            outer transaction (Alembic wraps the migration; SQLAlchemy
            callers must commit explicitly).
    """
    executor(_CREATE_TRIGGER_FUNC_SQL)
    executor(_DROP_TRIGGER_SQL)
    executor(_CREATE_TRIGGER_SQL)


def remove_posting_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_posting_infrastructure` for a migration downgrade.

    Drops, in safe order:

    1. The ``ck_account_postings_balanced`` constraint trigger (guarded with
       ``IF EXISTS`` so a partially-built infrastructure unwinds cleanly).
    2. The ``budget.assert_journal_entry_balanced`` trigger function.

    Idempotent: both statements use ``IF EXISTS``, so running the function
    twice is indistinguishable from running it once, and running it on a
    database that never had the infrastructure is a clean no-op.  A
    downgrade that drops ``budget.account_postings`` would cascade the
    trigger away with the table, but the explicit drops here make the
    teardown order well-defined and let the function be called independently
    (e.g. in the apply/remove idempotency tests).

    Args:
        executor: Single-argument callable that accepts a SQL string and
            runs it.  Same contract as :func:`apply_posting_infrastructure`.
    """
    executor(_DROP_TRIGGER_SQL)
    executor(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION_NAME}()")


# The least-privilege runtime role (provisioned by scripts/init_db_role.sql)
# and the two append-only ledger tables the posture applies to.
# ledger_accounts is deliberately NOT listed: it is mutable by design
# (display-name snapshots, archival), and only the journal is append-only.
_APP_ROLE = "shekel_app"
_LEDGER_TABLES = "budget.journal_entries, budget.account_postings"

# Both statements are wrapped in a role-existence guard (the same pattern the
# audit rebuild migration a5be2a99ea14 uses) so they succeed on a cluster
# where the production app role has never been provisioned.  The test cluster
# is not one: ``scripts/build_test_db_image.py`` creates the role before the
# template is built, as the entrypoint does before it migrates.  REVOKE and
# GRANT are idempotent, so re-running either is indistinguishable from running
# it once.
_REVOKE_LEDGER_WRITE_SQL = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
        REVOKE UPDATE, DELETE ON {_LEDGER_TABLES} FROM {_APP_ROLE};
    END IF;
END$$
"""

_GRANT_LEDGER_WRITE_SQL = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
        GRANT UPDATE, DELETE ON {_LEDGER_TABLES} TO {_APP_ROLE};
    END IF;
END$$
"""


def apply_ledger_append_only_privileges(executor: Callable[[str], object]) -> None:
    """Idempotently revoke ``UPDATE, DELETE`` on the ledger tables from the app role.

    Makes the ledger's append-only property real at the DATABASE tier for the
    running application (review M1/R4): the deferred balanced trigger has no
    DELETE arm and its UPDATE arm checks only the NEW row's entry, and the ORM
    immutability listeners do not fire for raw SQL -- so without this posture
    a raw-SQL single-leg DELETE silently breaks the trial balance.  ``SELECT``
    and ``INSERT`` are untouched (appending reversal/delta entries is the
    correction mechanism), and referential actions keep working because
    PostgreSQL executes CASCADE / SET NULL as the table owner, not the client
    role (verified live on the dev stack, 2026-07-02).

    A no-op when the ``shekel_app`` role does not exist (see the guard's
    comment above), and idempotent when it does.  The caller MUST have
    already created both ledger tables (the same caller contract as
    :func:`apply_posting_infrastructure`); ``scripts/init_db_role.sql``
    carries its own table-existence guard because it runs BEFORE migrations
    on a fresh boot.

    Args:
        executor: Single-argument callable that accepts a SQL string and
            runs it.  Same contract as :func:`apply_posting_infrastructure`;
            must run with a role privileged to REVOKE (the owner role -- the
            migration runner, the entrypoint, and the test-template builder
            all do).
    """
    executor(_REVOKE_LEDGER_WRITE_SQL)


def remove_ledger_append_only_privileges(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_ledger_append_only_privileges` (migration downgrade).

    Restores the blanket-DML posture ``scripts/init_db_role.sql`` grants on
    every ``budget`` table, so a downgraded database matches the pre-R4
    grant state exactly.  Role-guarded and idempotent like the apply side.

    Args:
        executor: Single-argument callable that accepts a SQL string and
            runs it.  Same contract as :func:`apply_posting_infrastructure`.
    """
    executor(_GRANT_LEDGER_WRITE_SQL)
