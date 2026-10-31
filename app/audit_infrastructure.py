"""Shared definitions for the ``system.audit_log`` audit infrastructure.

Three callers must produce identical audit infrastructure across the
project:

1. The Alembic rebuild migration that ships with commit C-13 of the
   2026-04-15 security remediation plan
   (``migrations/versions/<rev>_rebuild_audit_infrastructure.py``).
2. ``scripts/init_database.py``, which initialises a fresh database
   via ``db.create_all()`` followed by an Alembic ``stamp`` -- a path
   that bypasses the migration chain and would otherwise leave the
   audit triggers absent (audit finding F-028).
3. ``tests/conftest.py``, which mirrors the production schema during
   the test session so service-layer behaviour can be asserted
   end-to-end with audit_log rows present.

Centralising the SQL strings and the canonical table list here is the
only way to keep those three call sites in lock-step.  The previous
arrangement (raw SQL embedded both in the migration file and in
``conftest.py``) drifted: the test fixture grew six tables behind the
production migration, with no automated check to catch the gap.

Adding a NEW table to the ``budget``, ``salary``, or ``auth`` schema
REQUIRES adding a row to ``AUDITED_TABLES`` here AND running
``flask db migrate`` so the rebuild migration's idempotent CREATE
TRIGGER picks it up on the next ``flask db upgrade``.  The
``coding-standards.md`` "Schema Design" section documents this
invariant; the deploy's trigger-count check
(:func:`require_audit_triggers`) enforces it at container start.
"""

from __future__ import annotations

from typing import Callable, Iterable


# ---------------------------------------------------------------------------
# Canonical audited-table list
# ---------------------------------------------------------------------------
#
# Sorted alphabetically by ``(schema, table)``.  Sorting (rather than
# topical grouping) makes a forgotten table immediately visible in a
# diff and removes the temptation to slot a new table "where it fits"
# rather than at the end of its schema block.
#
# Inclusion criteria:
#   * Tables in the ``budget``, ``salary``, or ``auth`` schema that
#     hold user-controlled financial state, salary configuration,
#     calibration overrides, tax-config admin data, or auth state.
#   * The ``ref`` schema is intentionally excluded with one exception
#     (``ref.account_types``).  Read-only seed catalogues like
#     ``ref.statuses`` or ``ref.transaction_types`` would drown the
#     trail in seed-script noise without any forensic value, so they
#     stay out.  ``ref.account_types`` is special because commit C-28
#     converted it into a multi-tenant table -- owners can create,
#     rename, and delete their own custom rows through
#     ``app/routes/accounts.py``, and those mutations need a
#     tamper-resistant forensic record for the same reasons every
#     other user-mutable financial table does.  The seed script's
#     idempotent upsert pattern still touches the seeded built-ins
#     occasionally, but those writes carry ``user_id IS NULL`` in the
#     audit row and are easy to filter out of operator queries.
#   * The ``system`` schema (``audit_log`` itself) is excluded to
#     avoid recursive trigger fires.
AUDITED_TABLES: tuple[tuple[str, str], ...] = (
    # ── auth schema ──────────────────────────────────────────────────
    ("auth", "mfa_configs"),
    ("auth", "trusted_devices"),
    ("auth", "user_settings"),
    ("auth", "users"),
    # ── budget schema ────────────────────────────────────────────────
    ("budget", "account_anchor_history"),
    ("budget", "account_external_identities"),
    ("budget", "account_openings"),
    ("budget", "account_postings"),
    ("budget", "accounts"),
    ("budget", "anchor_releases"),
    ("budget", "asset_appreciation_params"),
    ("budget", "bank_statement_lines"),
    ("budget", "categories"),
    ("budget", "credit_card_params"),
    ("budget", "escrow_component_versions"),
    ("budget", "escrow_lines"),
    ("budget", "interest_params"),
    ("budget", "investment_params"),
    ("budget", "journal_entries"),
    ("budget", "ledger_accounts"),
    ("budget", "loan_anchor_events"),
    ("budget", "loan_anchor_withdrawals"),
    ("budget", "loan_params"),
    ("budget", "loan_payment_settings"),
    ("budget", "merchant_rules"),
    ("budget", "merchants"),
    ("budget", "pay_eras"),
    ("budget", "pay_periods"),
    ("budget", "pay_schedule"),
    ("budget", "rate_history"),
    ("budget", "recurrence_rules"),
    ("budget", "recurrence_weekday_anchors"),
    ("budget", "savings_goals"),
    ("budget", "scenarios"),
    ("budget", "statement_imports"),
    ("budget", "statement_line_sightings"),
    ("budget", "statement_line_skips"),
    ("budget", "statement_match_creations"),
    ("budget", "statement_match_members"),
    ("budget", "statement_matches"),
    ("budget", "template_amount_versions"),
    ("budget", "transaction_entries"),
    ("budget", "transaction_templates"),
    ("budget", "transactions"),
    ("budget", "transfer_templates"),
    ("budget", "transfers"),
    # ── ref schema (multi-tenant tables only) ────────────────────────
    # Per the inclusion criteria above: ``ref.account_types`` carries
    # owner-scoped rows after C-28 / F-044 and is the only ref table
    # in the audited set.  Other ref tables remain read-only seed
    # catalogues and must NOT be added without a corresponding shift
    # in the seed script's write semantics.
    ("ref", "account_types"),
    # ── salary schema ────────────────────────────────────────────────
    ("salary", "calibration_overrides"),
    ("salary", "fica_configs"),
    ("salary", "pay_entries"),
    ("salary", "pay_stub_line_amounts"),
    ("salary", "pay_stub_one_offs"),
    ("salary", "pay_stub_withholdings"),
    ("salary", "pay_stubs"),
    ("salary", "paycheck_lines"),
    ("salary", "pension_profiles"),
    ("salary", "salary_profiles"),
    ("salary", "salary_raises"),
    ("salary", "state_child_deductions"),
    ("salary", "state_tax_configs"),
    ("salary", "tax_bracket_sets"),
    ("salary", "tax_brackets"),
    ("salary", "ytd_tax_checkpoints"),
)


# Number of triggers the live database must carry once the rebuild
# migration has run.  The deploy refuses to commit when the database
# carries fewer (:func:`require_audit_triggers`); the migration tests
# assert the same number round-trips through upgrade and downgrade.
# ``len(AUDITED_TABLES)`` rather than a hard-coded constant so adding a
# row above is the only edit a future commit needs to make.
EXPECTED_TRIGGER_COUNT: int = len(AUDITED_TABLES)

#: The prefix every audit trigger's name carries (:func:`_audit_trigger_name`),
#: and the one :data:`AUDIT_TRIGGER_COUNT_SQL` counts by.
_AUDIT_TRIGGER_PREFIX = "audit_"

# The one spelling of "how many audit triggers does this database carry"
# that the deploy's check and the test-template build share (plan step
# balance:X-cv leaf 2): the non-internal triggers whose name starts with
# :data:`_AUDIT_TRIGGER_PREFIX`.  The tests count with spellings of their
# own on purpose -- an oracle that shared this one could not see it wrong --
# and ``scripts/build_test_db_image.py``'s image check still carries its own
# ``LIKE 'audit\_%'``.  ``starts_with`` rather than ``LIKE 'audit_%'``:
# LIKE reads a bare ``_`` as any one character, and a ``%`` means something
# different to each driver path that runs this text.  The test-template
# build runs it verbatim on a raw psycopg cursor, and the deploy runs it
# EMBEDDED in a statement with named parameters
# (:data:`_AUDIT_TRIGGER_CENSUS_SQL`), where a ``%`` would be read as a
# placeholder: this text must never contain one.
AUDIT_TRIGGER_COUNT_SQL = (
    "SELECT count(*) FROM pg_trigger "
    f"WHERE starts_with(tgname, '{_AUDIT_TRIGGER_PREFIX}') AND NOT tgisinternal"
)


def require_audit_triggers(connection) -> int:
    """Refuse a database that carries fewer audit triggers than this image expects.

    The deploy's check, run inside its one transaction just before the
    commit (``scripts/init_database.py``, ruling **R-BAL122**), so a
    database missing a trigger is refused with nothing committed and
    ``deploy/shekel-deploy.sh`` re-pins the previous image.  Until plan step
    balance:X-cv it was ``entrypoint.sh`` step 7, a shell ``psql`` count run
    AFTER step 3 had committed, whose refusal left a stamp the previous
    image could not always resolve.

    AT LEAST :data:`EXPECTED_TRIGGER_COUNT`, as step 7 asked: a missing
    trigger means writes to an audited table leave no audit row, which is
    what this refuses.  A surplus trigger (one this image's
    :data:`AUDITED_TABLES` does not name) is not; the test-template build
    asks for the exact number instead, because it builds from this list.

    **The refusal names every audited table missing its trigger, and the
    statement that re-creates it** (ruling **R-BAL129**, X-cv leaf 2b).  The
    count alone told an operator nothing they could act on: the X-cv rehearsal
    dropped one trigger, the release refused and re-pinned the previous image,
    that image refused the same database, and neither log named the table.
    The names are read per table from :data:`AUDITED_TABLES`, never inferred
    from the count, so once the count is short a surplus trigger cannot keep a
    missing one out of the list.  A surplus can still keep the count from
    being short (one audited trigger fewer than
    :data:`EXPECTED_TRIGGER_COUNT` plus one surplus passes, R-BAL122's AT
    LEAST), and then nothing is refused or named: the pass/fail rule is the
    count, unchanged.  The count and the list are read in one statement
    (:func:`_audit_trigger_census`), so they describe one snapshot.

    Args:
        connection: A SQLAlchemy ``Connection``; on the deploy, the one its
            transaction runs on, so the count sees that transaction's own DDL.

    Returns:
        int: The number of audit triggers found, for the deploy log.

    Raises:
        RuntimeError: When fewer than :data:`EXPECTED_TRIGGER_COUNT` exist,
            naming each missing table and, for a trigger dropped by hand, the
            statements that re-create them, runnable as printed.
    """
    found, missing = _audit_trigger_census(connection)
    if found < EXPECTED_TRIGGER_COUNT:
        raise RuntimeError(
            f"Audit trigger check failed: {found} audit trigger(s), at least "
            f"{EXPECTED_TRIGGER_COUNT} expected (one per "
            "app.audit_infrastructure.AUDITED_TABLES entry).  Missing: "
            + ", ".join(f"{schema}.{table}" for schema, table in missing)
            + " (writes to them leave no audit row).  If a trigger was "
            "dropped by hand, re-create it as the database owner: "
            + " ".join(
                f"{_create_trigger_sql(schema, table)};"
                for schema, table in missing
            )
            + "  If instead a table newly added to AUDITED_TABLES is named, "
            "its migration did not attach the trigger, and the migration is "
            "the fix."
        )
    return found


#: The count :data:`AUDIT_TRIGGER_COUNT_SQL` makes, and every
#: :data:`AUDITED_TABLES` entry whose OWN audit trigger is absent (in list
#: order), in ONE statement.  The list is driven by three parallel arrays built
#: from :data:`AUDITED_TABLES` and :func:`_audit_trigger_name`, so neither the
#: audited set nor the naming rule is spelled a second time here.
_AUDIT_TRIGGER_CENSUS_SQL = (
    f"SELECT ({AUDIT_TRIGGER_COUNT_SQL}) AS found, "
    "ARRAY("
    "  SELECT ARRAY[a.schema_name, a.table_name] "
    "  FROM unnest(%(schemas)s::text[], %(tables)s::text[], %(names)s::text[]) "
    "    WITH ORDINALITY AS a(schema_name, table_name, trigger_name, position) "
    "  WHERE NOT EXISTS ("
    "    SELECT 1 FROM pg_trigger tg "
    "    JOIN pg_class c ON c.oid = tg.tgrelid "
    "    JOIN pg_namespace n ON n.oid = c.relnamespace "
    "    WHERE n.nspname = a.schema_name AND c.relname = a.table_name "
    "      AND tg.tgname = a.trigger_name AND NOT tg.tgisinternal"
    "  ) "
    "  ORDER BY a.position"
    ") AS missing"
)


def _audit_trigger_census(connection) -> tuple[int, list[tuple[str, str]]]:
    """Count the audit triggers and list the audited tables missing theirs.

    ONE statement, so both answers read one snapshot.  As two statements under
    READ COMMITTED, a trigger re-created between them (an operator repairing
    during a crash loop) left a short count whose list named nothing
    (measured by the adversarial review of ruling R-BAL129: a trigger
    committed between the two reads gave 55 found and an empty list).  Within one
    snapshot a short count always names at least one table: the audited
    entries are distinct, and each one present is counted.

    Args:
        connection: A SQLAlchemy ``Connection``.

    Returns:
        tuple[int, list[tuple[str, str]]]: The number of audit triggers, and
        the missing ``(schema, table)`` entries in :data:`AUDITED_TABLES`
        order.
    """
    row = connection.exec_driver_sql(
        _AUDIT_TRIGGER_CENSUS_SQL,
        {
            "schemas": [schema for schema, _ in AUDITED_TABLES],
            "tables": [table for _, table in AUDITED_TABLES],
            "names": [_audit_trigger_name(table) for _, table in AUDITED_TABLES],
        },
    ).one()
    return row.found, [(pair[0], pair[1]) for pair in row.missing]


# ---------------------------------------------------------------------------
# Idempotent SQL building blocks
# ---------------------------------------------------------------------------
#
# Each constant below is a single SQL statement.  They are intentionally
# split into individual statements so callers using ``op.execute`` (one
# statement per call, the Alembic idiom) and callers using
# ``session.execute(text(...))`` (one statement per ``text``, the
# SQLAlchemy idiom) both work without surgery.  Multi-statement strings
# would also work in PostgreSQL via simple-query, but splitting keeps
# error messages on the right line.

_CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS system"

# CREATE TABLE IF NOT EXISTS leaves a pre-existing table untouched
# (it does not re-validate the column list or constraints), so the
# definition here must match the original a8b1c2d3e4f5 migration's
# columns exactly.  Any future column change needs its own migration
# step that ALTERs the table -- this file should not be edited to
# "fix" a column drift between the constant and the live schema.
_CREATE_AUDIT_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS system.audit_log (
    id              BIGSERIAL       PRIMARY KEY,
    table_schema    VARCHAR(50)     NOT NULL,
    table_name      VARCHAR(100)    NOT NULL,
    operation       VARCHAR(10)     NOT NULL,
    row_id          INTEGER,
    old_data        JSONB,
    new_data        JSONB,
    changed_fields  TEXT[],
    user_id         INTEGER,
    db_user         VARCHAR(100)    DEFAULT current_user,
    executed_at     TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_log_operation
        CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE'))
)
"""

_CREATE_INDEXES_SQL: tuple[str, ...] = (
    """CREATE INDEX IF NOT EXISTS idx_audit_log_table
        ON system.audit_log (table_schema, table_name)""",
    """CREATE INDEX IF NOT EXISTS idx_audit_log_executed
        ON system.audit_log (executed_at)""",
    """CREATE INDEX IF NOT EXISTS idx_audit_log_row
        ON system.audit_log (table_name, row_id)""",
)

# CREATE OR REPLACE FUNCTION is idempotent and atomically swaps the
# function body if a previous version exists -- safe to re-run on a
# database that already has the trigger function.  The body is
# verbatim from the original a8b1c2d3e4f5 migration; the only change
# in the rebuild is moving from a non-idempotent CREATE TABLE / CREATE
# INDEX above to the IF NOT EXISTS variants and tightening the
# operation column with a CHECK constraint.
_CREATE_TRIGGER_FUNC_SQL = r"""
CREATE OR REPLACE FUNCTION system.audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    v_old_data  JSONB;
    v_new_data  JSONB;
    v_changed   TEXT[] := '{}';
    v_user_id   INTEGER;
    v_row_id    INTEGER;
    v_key       TEXT;
BEGIN
    -- Read the application user_id from the session-local variable
    -- set by the Flask before_request hook.  current_setting(...,
    -- true) returns NULL instead of raising when the variable is
    -- absent; the inner BEGIN ... EXCEPTION block additionally
    -- swallows the integer parse error that would fire on a
    -- malformed value (e.g. an empty string written by a future
    -- caller).  v_user_id NULL is the documented "no authenticated
    -- user" sentinel; tests assert this path on direct DB writes.
    BEGIN
        v_user_id := current_setting('app.current_user_id', true)::INTEGER;
    EXCEPTION WHEN OTHERS THEN
        v_user_id := NULL;
    END;

    IF TG_OP = 'DELETE' THEN
        v_old_data := to_jsonb(OLD);
        v_row_id   := OLD.id;
        INSERT INTO system.audit_log
            (table_schema, table_name, operation, row_id,
             old_data, new_data, changed_fields, user_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
             v_old_data, NULL, NULL, v_user_id);
        RETURN OLD;

    ELSIF TG_OP = 'INSERT' THEN
        v_new_data := to_jsonb(NEW);
        v_row_id   := NEW.id;
        INSERT INTO system.audit_log
            (table_schema, table_name, operation, row_id,
             old_data, new_data, changed_fields, user_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
             NULL, v_new_data, NULL, v_user_id);
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        v_old_data := to_jsonb(OLD);
        v_new_data := to_jsonb(NEW);
        v_row_id   := NEW.id;
        -- Compute the symmetric difference of OLD and NEW JSONB.
        -- IS DISTINCT FROM treats two NULLs as equal so a column that
        -- moved between NULL and the same NULL does not flag.
        FOR v_key IN
            SELECT key FROM jsonb_each(v_new_data)
            WHERE NOT v_old_data ? key
               OR v_old_data -> key IS DISTINCT FROM v_new_data -> key
        LOOP
            v_changed := array_append(v_changed, v_key);
        END LOOP;
        -- Suppress no-op UPDATEs (UPDATE x SET name = name).  Without
        -- this guard the audit log fills with empty changed_fields
        -- rows that add no forensic value.
        IF array_length(v_changed, 1) IS NULL THEN
            RETURN NEW;
        END IF;
        INSERT INTO system.audit_log
            (table_schema, table_name, operation, row_id,
             old_data, new_data, changed_fields, user_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
             v_old_data, v_new_data, v_changed, v_user_id);
        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

# Conditional GRANT block: if the ``shekel_app`` role exists (created
# by ``scripts/init_db.sql`` in the production Docker entrypoint),
# grant USAGE/SELECT/INSERT on the audit infrastructure so the
# least-privilege app role can write audit_log rows through the
# trigger and read them for forensic queries.  When the role does
# not exist (developer laptop, CI test container), the block is a
# no-op -- the migration must still succeed.
_GRANT_APP_ROLE_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'shekel_app') THEN
        GRANT USAGE ON SCHEMA system TO shekel_app;
        GRANT SELECT, INSERT ON system.audit_log TO shekel_app;
        GRANT USAGE ON SEQUENCE system.audit_log_id_seq TO shekel_app;
    END IF;
END$$
"""


#: The GUC the audit trigger reads to attribute a row to the acting user, and
#: the statement that binds it.  ``set_config(..., is_local => true)`` rather
#: than ``SET LOCAL`` so the value travels as a real bind parameter, and
#: TRANSACTION-scoped either way: it dies with the transaction that set it.
#:
#: **That lifetime is why it is stated HERE rather than at the one hook that
#: used to issue it** (plan step balance:X-i3).  A request no longer runs in a
#: single transaction: :mod:`app.db_transaction` gives a render its own
#: read-only snapshot and gives a render's declared write a command
#: transaction of its own, and **every transaction that CAN WRITE has to be
#: told who is acting**.  A hook that binds the actor once, before any of them
#: exists, attributes the FIRST transaction and no other -- which is how every
#: pay period the rolling top-up appends came to land in
#: ``system.audit_log`` with a NULL user during that step's own build,
#: silently, because the trigger's ``EXCEPTION WHEN OTHERS`` reads an unset
#: GUC as "no authenticated user".
#:
#: **A render's snapshot is deliberately NOT told, and an earlier revision of
#: this note said it was.**  That transaction is ``READ ONLY``, so PostgreSQL
#: refuses every write to an audited table inside it and no trigger in it can
#: read this setting -- so binding one there is a round trip with no reader.
#: The rule and its one door are
#: :func:`app.db_transaction.bind_request_actor`; a reader who "fixes" a
#: missing bind by following the older sentence re-adds three round trips per
#: authenticated GET.
_BIND_AUDIT_ACTOR_SQL = "SELECT set_config('app.current_user_id', %(uid)s, true)"


def bind_audit_actor(connection, user_id) -> None:
    """Tell the audit triggers who is acting, for THIS transaction.

    Args:
        connection: A SQLAlchemy ``Connection`` on the transaction to bind.
        user_id: The acting ``auth.users.id``.
    """
    connection.exec_driver_sql(_BIND_AUDIT_ACTOR_SQL, {"uid": str(user_id)})


def _audit_trigger_name(table: str) -> str:
    """Return the name of *table*'s audit trigger: ``audit_<table>``.

    The one spelling of the naming rule; its prefix is
    :data:`_AUDIT_TRIGGER_PREFIX`, which :data:`AUDIT_TRIGGER_COUNT_SQL`
    counts by.  :func:`_audit_trigger_census` looks each trigger up by the
    name, and every builder below attaches or drops by it.

    Args:
        table: Table name (e.g. ``"transactions"``).

    Returns:
        str: The trigger name.
    """
    return f"{_AUDIT_TRIGGER_PREFIX}{table}"


def _create_trigger_sql(schema: str, table: str) -> str:
    """Return the bare ``CREATE TRIGGER`` statement that attaches *table*'s audit trigger.

    The one spelling in code of the trigger's definition:
    :func:`_trigger_sql_for_table` wraps it in its existence guard, and
    :func:`require_audit_triggers`' refusal quotes it for the operator to run.

    Args:
        schema: PostgreSQL schema (e.g. ``"budget"``).
        table:  Table name (e.g. ``"transactions"``).

    Returns:
        str: One statement, without a trailing semicolon.
    """
    return (
        f"CREATE TRIGGER {_audit_trigger_name(table)} "
        f"AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def _drop_trigger_sql(schema: str, table: str) -> str:
    """Return the ``DROP TRIGGER IF EXISTS`` statement for *table*'s audit trigger.

    Args:
        schema: PostgreSQL schema (e.g. ``"budget"``).
        table:  Table name (e.g. ``"transactions"``).

    Returns:
        str: One statement, without a trailing semicolon.
    """
    return f"DROP TRIGGER IF EXISTS {_audit_trigger_name(table)} ON {schema}.{table}"


def _trigger_sql_for_table(schema: str, table: str) -> Iterable[str]:
    """Yield the (idempotent) SQL statements that attach an audit trigger.

    PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so the function
    pairs ``DROP TRIGGER IF EXISTS`` with a fresh ``CREATE TRIGGER`` to
    achieve the same effect.  The drop is harmless on a database that
    has never carried the trigger; the create is identical on every run
    so a re-execution leaves the trigger pinned to the same definition
    as the latest deploy.

    The CREATE TRIGGER is wrapped in a ``DO $$ ... IF EXISTS ...``
    guard against ``pg_class`` so this function is safe to call on a
    database where the target table does not yet exist.  Migration
    replay from scratch hits this path: the rebuild migration
    (``a5be2a99ea14``) calls ``apply_audit_infrastructure`` against
    the CURRENT in-code ``AUDITED_TABLES`` list, which may reference
    tables created by LATER migrations in the chain (Commit 12 /
    ``budget.loan_anchor_events`` was the first such case).  Without
    the guard, replay would fail with ``relation does not exist`` at
    the rebuild step.  The later migration that actually creates the
    target table attaches the trigger explicitly via its own DROP +
    CREATE pair, so the end-of-chain state still has a trigger
    per audited table.

    The companion DROP is already guarded by ``IF EXISTS`` against the
    trigger (Postgres tolerates a missing schema/table on the DROP);
    the CREATE is the only statement that needs the existence wrap.

    Args:
        schema: PostgreSQL schema (e.g. ``"budget"``).
        table:  Table name (e.g. ``"transactions"``).

    Yields:
        Two SQL statements: a guarded DROP and a guarded CREATE.
        Trigger name is fixed at ``audit_<table>`` to keep counting by
        that prefix (:data:`AUDIT_TRIGGER_COUNT_SQL`) simple.
    """
    yield _drop_trigger_sql(schema, table)
    # ``IF EXISTS`` guard via the ``pg_class``/``pg_namespace`` system
    # catalogues.  The CREATE TRIGGER fires only when the target table
    # is materialised; on a fresh-DB migration replay where a later
    # migration owns the table creation, this step quietly no-ops and
    # the owning migration attaches its own trigger.
    yield (
        "DO $$ BEGIN "
        "IF EXISTS ("
        "  SELECT 1 FROM pg_class c "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"  WHERE n.nspname = '{schema}' AND c.relname = '{table}'"
        ") THEN "
        f"  {_create_trigger_sql(schema, table)}; "
        "END IF; END $$"
    )


def apply_audit_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently materialise the audit log infrastructure.

    Executes, in order:

    1. ``CREATE SCHEMA IF NOT EXISTS system``
    2. ``CREATE TABLE IF NOT EXISTS system.audit_log`` and three
       supporting indexes
    3. ``CREATE OR REPLACE FUNCTION system.audit_trigger_func``
    4. ``DROP TRIGGER IF EXISTS`` followed by ``CREATE TRIGGER`` for
       every entry in :data:`AUDITED_TABLES`
    5. A guarded ``GRANT`` block that gives the ``shekel_app`` role
       (when present) the USAGE/SELECT/INSERT it needs to write audit
       rows through the trigger; the block is a no-op when the role
       has not been provisioned (developer laptop, CI container).

    All statements are idempotent: running the function twice in a row
    must be indistinguishable from running it once.  This invariant is
    exercised by ``tests/test_models/test_audit_migration.py`` and by
    the upgrade-downgrade-upgrade test in
    ``tests/test_integration/test_audit_triggers.py``.

    Args:
        executor: Single-argument callable that accepts a SQL string
            and runs it.  Pass ``op.execute`` from inside an Alembic
            migration; pass ``lambda s: session.execute(text(s))``
            from inside a SQLAlchemy session.  Errors propagate --
            the caller is responsible for the outer transaction
            (Alembic wraps the migration; SQLAlchemy callers must
            commit explicitly).
    """
    executor(_CREATE_SCHEMA_SQL)
    executor(_CREATE_AUDIT_LOG_TABLE_SQL)
    for index_sql in _CREATE_INDEXES_SQL:
        executor(index_sql)
    executor(_CREATE_TRIGGER_FUNC_SQL)
    for schema, table in AUDITED_TABLES:
        for sql in _trigger_sql_for_table(schema, table):
            executor(sql)
    executor(_GRANT_APP_ROLE_SQL)


def remove_audit_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_audit_infrastructure` for the migration's downgrade.

    Drops, in safe order:

    1. Every audit trigger named in :data:`AUDITED_TABLES` (guarded
       with ``IF EXISTS`` so a partially-built infrastructure unwinds
       cleanly).
    2. ``system.audit_trigger_func``.
    3. ``system.audit_log`` (with ``CASCADE`` to break any forensic
       view that might have been built on it -- the audit trail is
       the user-facing artefact, the table is its storage).

    Notes:
        * The ``system`` schema itself is intentionally **not** dropped.
          ``CREATE SCHEMA IF NOT EXISTS system`` runs idempotently in
          ``scripts/init_db.sql`` and a downgrade should not hand a
          subsequent re-upgrade an empty schema slot.
        * The conditional ``shekel_app`` GRANT block from
          :func:`apply_audit_infrastructure` does not require a paired
          REVOKE -- dropping the table cascades the privileges away.

    Args:
        executor: Single-argument callable that accepts a SQL string
            and runs it.  Same contract as
            :func:`apply_audit_infrastructure`.
    """
    for schema, table in AUDITED_TABLES:
        executor(_drop_trigger_sql(schema, table))
    executor("DROP FUNCTION IF EXISTS system.audit_trigger_func()")
    executor("DROP TABLE IF EXISTS system.audit_log CASCADE")
