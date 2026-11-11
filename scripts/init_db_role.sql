-- Shekel Budget App -- the web login's privileges
--
-- Two-role policy (audit finding F-081 / remediation Commit C-13; the
-- owner split from the superuser by plan#2166):
--   * shekel_owner (owner)           -- owns the database, its schemas
--     and every object in them, and runs the deploy: DDL, migrations,
--     the deployment scripts.  Not a superuser.  Its URL is
--     DATABASE_URL, in the migrate service alone.
--   * shekel_app  (least-privilege)  -- DML-only role used by the
--     runtime Flask app.  Cannot DROP TABLE, ALTER TABLE, or DROP
--     ROLE.  An attacker with RCE in the Gunicorn process cannot
--     remove the audit triggers that the rebuild migration adds.
--     Its URL is DATABASE_URL_APP.
--
-- Both logins, and their passwords, are made in the database container
-- (deploy/postgres/provision_roles.sh; rulings plan#3412, plan#3413).
-- This file only grants: scripts/deploy.sh runs it as shekel_owner after
-- init_db.sql, on every deploy, before the migrations.  A database without
-- shekel_app stops here, at the first GRANT, naming the role.

-- ── Connection + schema usage ─────────────────────────────────────
-- Note: the database name "shekel" is the production default; if a
-- deployment uses a different DB name the GRANT below should be
-- updated to match, or the operator can run `GRANT CONNECT ON
-- DATABASE <name> TO shekel_app` separately.  The role-provisioning
-- file is intentionally not parameterised on DB name to keep it
-- readable -- a developer running this against a dev-named database
-- updates the literal once and moves on.
GRANT CONNECT ON DATABASE shekel TO shekel_app;
GRANT USAGE ON SCHEMA auth, budget, salary, ref TO shekel_app;

-- ── DML privileges on existing tables ─────────────────────────────
-- ALL TABLES IN SCHEMA covers every table that already exists at
-- this point (most importantly the ref tables seeded by
-- seed_ref_tables.py).  Tables created later by Alembic migrations
-- pick up DML privileges via the ALTER DEFAULT PRIVILEGES block
-- below, which only affects future objects -- existing objects must
-- be granted directly here so seed scripts can populate them.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA auth, budget, salary TO shekel_app;
GRANT SELECT
    ON ALL TABLES IN SCHEMA ref TO shekel_app;

GRANT USAGE
    ON ALL SEQUENCES IN SCHEMA auth, budget, salary, ref TO shekel_app;

-- ── Posting ledger: append-only for the runtime role ──────────────
-- The double-entry journal (budget.journal_entries + account_postings)
-- is append-only: corrections are new reversing entries, never edits,
-- and disposal is FK CASCADE, which PostgreSQL executes as the table
-- owner -- so the runtime role never needs UPDATE or DELETE there.
-- Revoking them makes append-only real at the database tier (the
-- 2026-07-02 balance-architecture review, M1/R4: a raw-SQL single-leg
-- DELETE otherwise breaks the trial balance silently -- the balanced
-- trigger has no DELETE arm by design).
--
-- This block MUST follow the blanket GRANT above and MUST live in this
-- file (not only in migration e3c23fadb21d): scripts/deploy.sh re-runs
-- this file on every deploy, so the blanket GRANT re-opens
-- UPDATE/DELETE on the ledger tables at each deploy and this REVOKE
-- re-closes it in the same run.  Guarded on table existence because on
-- a FRESH database this file runs before the tables are created --
-- that first deploy's posture is closed later in the same deploy by
-- scripts/init_database.py (apply_ledger_append_only_privileges, the
-- shared SQL in app/posting_infrastructure.py; keep both in sync).
DO $$
BEGIN
    IF to_regclass('budget.journal_entries') IS NOT NULL
       AND to_regclass('budget.account_postings') IS NOT NULL THEN
        REVOKE UPDATE, DELETE
            ON budget.journal_entries, budget.account_postings
            FROM shekel_app;
    END IF;
END
$$;

-- ── Default privileges for future objects ─────────────────────────
-- ALTER DEFAULT PRIVILEGES is scoped to the role that runs it (the
-- "FOR ROLE" clause defaults to current_user, which is shekel_owner
-- when this file runs from scripts/deploy.sh).  The clause grants the
-- listed privileges automatically on every new object that
-- shekel_owner creates in the named schema -- so when Alembic
-- creates a new table in budget/salary/auth, the runtime app role
-- immediately has DML on it without a manual GRANT after every
-- migration.  (A database deployed before plan#2166 also holds the
-- same defaults for shekel_user, the superuser that ran its deploys;
-- they cover only what that role creates, which is no longer the
-- migrations.)
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, budget, salary
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ref
    GRANT SELECT ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, budget, salary, ref
    GRANT USAGE ON SEQUENCES TO shekel_app;

-- ── system schema (audit log) ─────────────────────────────────────
-- The audit infrastructure (table, function, triggers) is created
-- by the rebuild migration, not here -- but the schema-level USAGE
-- grant and the default-privilege rule for FUTURE objects in the
-- system schema must be in place BEFORE the migration runs so the
-- audit_log table inherits the right ACL when the migration creates
-- it.  The conditional SELECT/INSERT GRANT block inside the
-- migration covers the table itself once it exists; the ALTER
-- DEFAULT PRIVILEGES below covers any further system.* objects
-- (e.g. the planned read-audit instrumentation in remediation
-- Commit C-52).
GRANT USAGE ON SCHEMA system TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA system
    GRANT SELECT, INSERT ON TABLES TO shekel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA system
    GRANT USAGE ON SEQUENCES TO shekel_app;

-- Direct grant on the audit table when it ALREADY exists.  A database
-- restored from a dump taken with --no-privileges (the prod->dev clone
-- procedure) arrives with alembic_version at head, so the rebuild
-- migration's own conditional GRANT never re-runs and every write 500s
-- inside system.audit_trigger_func() with "permission denied for table
-- audit_log" (hit 2026-06-11).  This block makes any clone self-heal at
-- the next deploy; it is an idempotent no-op when the migration's
-- grant is already in place, and a no-op on a fresh database where the
-- table does not exist yet (the migration grants it on creation).
DO $$
BEGIN
    IF to_regclass('system.audit_log') IS NOT NULL THEN
        GRANT SELECT, INSERT ON system.audit_log TO shekel_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA system TO shekel_app;
    END IF;
END
$$;
