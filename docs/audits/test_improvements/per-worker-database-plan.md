# Plan: Per-pytest-worker PostgreSQL database isolation via template cloning + pytest-xdist parallelism

## Status (as of 2026-05-11)

**All phases complete.** Phases 0 through 4 plus the drift fix landed on `dev` earlier in the day;
Phase 5 (CI workflow + docs) landed in commits `8ab5dca` (CI) and `825cfea` (docs). Full suite
passes end-to-end at the new `-n 12` default in ~4 min wall-clock, down from ~28 min sequential
pre-Phase 0. CI now builds `shekel_test_template` before pytest runs; concurrent pytest invocations
are safe; `docs/testing-standards.md` and `CLAUDE.md` reflect the new flow.

| Phase | Status | Commit | Notes |
|---|---|---|---|
| Phase 0: add pytest-xdist dependency | **Done** | `e41d136` | `pytest-xdist==3.8.0` in `requirements-dev.txt`.  Verified: `tests/test_config.py` 55 passed. |
| Phase 1: consolidate ref-table seed | **Done** | `073c548` | New `app.ref_seeds.seed_reference_data(session, *, verbose=False)`; all three call sites (conftest, `app/__init__.py`, `scripts/seed_ref_tables.py`) rerouted.  Verified: 163 targeted tests pass; pylint 9.50/10 unchanged. |
| Drift findings doc | **Done** | `d5baf4a` | `shekel-audits/security-2026-04-15/model-migration-drift.md` catalogues every divergence with file references and recommended fixes (model edit vs new migration). |
| Plan saved into repo | **Done** | `7c6af1e` | This file -- mirrored from `~/.claude/plans/`. |
| Drift fix (H-1..H-5, M-1, L-1) | **Done** | `9a5cca1`..`49fa13b` | All seven findings closed: H-1 (`9a5cca1`), H-2 (`709786a`), H-3 (`6384c77`), H-4 (`d6f31b5`), M-1 (`2a28f8e`), L-1 (`cfc8572`), H-5 (`7939c8a`), doc update (`49fa13b`).  Re-verification on 2026-05-11 confirmed only EXPECTED divergences remain (alembic_version, FK names, column ordering, `\restrict` tokens). |
| Phase 2: template builder + drift pre-flight | **Done** | `2aa3d17` | `scripts/build_test_template.py` (335 lines, pylint 10/10) drops + recreates `shekel_test_template`, runs the Alembic chain to head, applies audit infra, seeds reference data, then truncates `system.audit_log` so the template ships with a zeroed log.  Verified end-to-end with `EXPECTED_TRIGGER_COUNT` imported from the code (currently 31, not the plan's stale "45"). |
| Phase 3: conftest module-level bootstrap | **Done** | `db95461` | `tests/conftest.py` gains module-level `_bootstrap_worker_database()` (orphan cleanup via `pg_stat_activity`, template existence check, clone via `CREATE DATABASE ... TEMPLATE`, post-clone row-count verification, sets `TEST_DATABASE_URL` before app import).  `setup_database` collapsed to a ref-cache refresh.  `pytest_sessionfinish` drops the per-session DB via psycopg2 `WITH (FORCE)`.  `_db.engine.dispose()` added to per-test `db` teardown.  Full 5,148-test suite passes across 8 batches; concurrent invocations no longer deadlock.  **One deviation from spec:** `pytest_sessionfinish` does NOT call `_db.session.remove()` + `_db.engine.dispose()` because Flask-SQLAlchemy 3.x requires an app context that has already torn down at hook time; `WITH (FORCE)` is the documented safety net.  Reasoning in the hook's docstring. |
| Phase 4: enable pytest-xdist parallelism + cluster-state markers | **Done** | `511132f` | `pytest.ini` registers `xdist_group` marker and adds `--dist=loadgroup` to `addopts` (without it the marker is a no-op under default `--dist=load`).  **One deviation from spec:** the marker moved from `TestLeastPrivilegeRole` class-level to the entire `tests/test_models/test_audit_migration.py` module via `pytestmark = pytest.mark.xdist_group("shekel_app_role")`.  Required because `TestApplyIdempotent` and `TestRoundTrip` call `apply_audit_infrastructure`, whose conditional GRANT to `shekel_app` would otherwise leak into other workers' per-session DBs and block `DROP ROLE` with `DependentObjectsStillExist`.  Multi-worker verification at `-n 4` showed ~2.2-2.6x speedup; `-n 12` showed ~7x. |
| `-n 12` default | **Done** | `0937975` | `pytest.ini` `addopts` adds `-n 12`.  Empirically captured during Phase 4.5 profiling (1,756-test batch: 561s sequential, 218s at -n 4, 113s at -n 8, 79s at -n 12, 63s at -n 16).  Past -n 12 each doubling of workers buys roughly half the previous gain due to PG's cluster-wide WAL/fsync serialisation; CPU is not the bottleneck (24 cores, only 16 used at -n 16).  Override with `-n 0` for single-process debugging or `-n auto` on a quiet box. |
| Phase 5: update CI workflow + docs | **Done** | `8ab5dca`..`825cfea` | CI (`8ab5dca`): adds `TEST_ADMIN_DATABASE_URL` to job env, new "Build test template database" step before pytest, refreshes stale comments on the postgres service and `TEST_DATABASE_URL`.  Docs (`825cfea`): rewrites `CLAUDE.md` Tests block + Common Commands tests line for ~4 min full suite / `-n 12` default / concurrent-safe / first-time template-build setup; rewrites `docs/testing-standards.md` "Test Run Guidelines" (8-batch table reframed as optional fallback with refreshed `-n 12` / `-n 0` figures); adds "Building the test template" + "Cluster-state tests and `xdist_group`" sections.  **Two deviations from original spec:** (a) no explicit `ALTER ROLE shekel_test CREATEDB` step -- the postgres image's `POSTGRES_USER` env creates a superuser implicitly, so `CREATEDB` is already granted; (b) the CI pytest invocation stays as bare `pytest --tb=short -q`, not `-n auto`, because `pytest.ini` `addopts` already carries `-n 12 --dist=loadgroup` from commit `0937975`. |
| Performance research follow-up (Phases A-C) | **Done** | `185275f`, `c0cd0bb`, `c249846`, `4a34884`, `99d3a98`, `5888d25`, `9575514`, `e329151`, `376afd3`, `c91ca45` (Phase 3d retrospective pending) | `docs/audits/test_improvements/test-performance-research.md` recommended three phases (A: cluster durability knobs + replica-role suppression; B: hybrid SAVEPOINT; C: PG 18 reflink clones).  Phase A was implemented as Phase 1 of `test-performance-implementation-plan.md` (commits `185275f` profile harness + `c0cd0bb` tmpfs + `c249846` Phase 1d flake fixes); Phase B was rejected in favour of going straight to C (the audit-trigger contract is preserved by C without the SAVEPOINT classification risk); Phase C landed as Phase 2 (PG 16 -> 18 across test/CI/dev/prod -- commits `4a34884`/`99d3a98`/`5888d25`/`9575514`) + Phase 3 (btrfs subvolume + per-test reflink drop+reclone -- commits `e329151`/`376afd3`/`c91ca45`).  Per-test fixture floor (single-process): 298 ms (Phase 0 baseline) -> 25 ms (Phase 3d, -92%).  Full suite at `-n 12`: ~4 min -> ~62 s on a fresh test-db container (~72 s back-to-back plateau; container restart returns to baseline).  Trade-off surfaced at Phase 3b: xdist fixture floor went UP from 53.9 ms (Phase 2e) to 82.6 ms (Phase 3b) because PG's cluster-level `pg_database` catalog lock serialises CREATE/DROP DATABASE across xdist workers -- single-process gains dominate; xdist gains were narrower than the plan projected.  See `docs/audits/test_improvements/test-performance-implementation-plan.md` Phase 3 retrospectives for the detailed measurement chain. |

**Branch state:** seven commits on `dev` ahead of `origin/dev` (`2aa3d17`, `db95461`, `511132f`,
`0937975`, `ff57072`, `8ab5dca`, `825cfea`) plus prior drift-fix commits. Not pushed. All
plan-related files (script, conftest, pytest.ini, CI workflow, docs) are committed; the
performance-research follow-up document `test-performance-research.md` is committed in `ff57072` and
remains a filed decision (no related code changes).

**All phases complete.** The "Resuming from a fresh session" section at the bottom of this document
is preserved as a historical record of the hand-off point that existed while Phase 5 was
outstanding; it is no longer load-bearing.

## Context

The Shekel test suite currently shares a single PostgreSQL test database (`shekel_test`) across all
pytest invocations. `tests/conftest.py::db` runs `TRUNCATE TABLE ... CASCADE` between every test,
which requires `AccessExclusiveLock` on every named table. Two concurrent pytest processes deadlock
by design on that lock. Beyond the deadlock, the architecture forbids `pytest-xdist` parallelism for
the same reason -- the suite has grown to ~5,131 tests / ~28 min sequential wall-clock, and the user
wants the option to run faster batches concurrently or in parallel without losing test isolation.

This plan adopts Pattern B (per-pytest-session database cloned from a PostgreSQL template) plus
`pytest-xdist` for parallelism within one invocation. Each pytest invocation -- and each xdist
worker within an invocation -- gets a uniquely-named database cloned at session start, runs the
existing TRUNCATE-based per-test cleanup inside its own DB, and drops the cloned DB on session
teardown. Deadlocks between sessions become structurally impossible. The per-test cleanup pattern
(which is correct for this audit-trail-heavy app: SAVEPOINT rollback would break `system.audit_log`
assertions) is preserved unchanged.

The user is a solo developer who explicitly wants best practice over speed: every phase below is
independently committable with a clear verification gate, so the migration can be paused or rolled
back at any phase boundary.

## High-level approach

1. Build a `shekel_test_template` PostgreSQL database once per developer setup (and after any
   migration). It contains all schemas, all tables (built via `flask db upgrade head` to validate
   the migration chain), audit infrastructure (triggers + `system.audit_log`), and reference seed
   data. It is shared across all pytest sessions.
2. On every pytest session start (including each xdist worker), `tests/conftest.py` clones the
   template into a uniquely-named per-session database (`shekel_test_<worker_id>_<pid>`), overrides
   `TEST_DATABASE_URL` before the `app` module is imported, then proceeds with the existing fixture
   stack.
3. On session teardown, the per-session DB is dropped. Crashed-session orphans are cleaned up on the
   next pytest startup.
4. `pytest-xdist` is added so a single invocation can fan out workers; each worker gets its own DB.
5. Documentation (CLAUDE.md, testing-standards.md) and CI (`.github/workflows/ci.yml`) are updated
   to match the new flow.

The user has chosen `flask db upgrade head` for the template build (validates the migration chain on
every rebuild) and authorised the CI workflow update.

## Architectural decisions and constraints

- **Per-test cleanup stays TRUNCATE-based.** Validated against the audit-trigger constraint:
  SAVEPOINT-rollback isolation would roll back audit-log rows along with the test data, breaking
  `test_integration/test_audit_triggers.py` and `test_scripts/test_audit_cleanup.py`. The current
  per-test cycle (TRUNCATE 28 tables CASCADE → re-seed ref → TRUNCATE `system.audit_log` → refresh
  ref_cache + Jinja globals) is preserved verbatim inside each per-session DB.
- **Module-level conftest bootstrap.** The per-session DB must exist and `TEST_DATABASE_URL` must
  point at it BEFORE `app/config.py::TestConfig.SQLALCHEMY_DATABASE_URI` is evaluated (which happens
  at class-body time during the first `import app`). The conftest already sets `SECRET_KEY` at
  module load before the app import; the new bootstrap follows the same pattern.
- **Master-vs-worker detection.** When `pytest-xdist` is active, the master process also imports
  conftest (for test collection) but never runs tests. If the master ran the bootstrap, it would
  create a DB that nothing uses and is never dropped. The bootstrap skips when both
  `PYTEST_XDIST_TESTRUNUID` is set AND `PYTEST_XDIST_WORKER` is unset -- the xdist master signature.
  Single-process pytest (no `-n` flag) has `PYTEST_XDIST_TESTRUNUID` unset and runs the bootstrap.
  xdist workers have both set and run the bootstrap. Verified against pytest-xdist 3.x behaviour.
- **Cluster-level state.** `tests/test_models/test_audit_migration.py::shekel_app_role` fixture
  executes `CREATE ROLE shekel_app` / `DROP ROLE shekel_app`. Roles are cluster-scoped, not
  database-scoped, so two xdist workers would race on this.
  `@pytest.mark.xdist_group("shekel_app_role")` on the relevant test classes forces those tests onto
  one worker.
- **Three seed call sites.** The reference-data seed logic exists in three places today:
  `tests/conftest.py::_seed_ref_tables` (lines 1152-1242), `app/__init__.py::_seed_ref_tables` (line
  861, called eagerly inside `create_app()` for dev/test configs), and `scripts/seed_ref_tables.py`.
  All three must converge on a single function so the template, runtime app, and production seed
  produce identical state. This is in scope -- leaving the duplication is a known-drift hazard.

## Phase-by-phase plan

Each phase ends with a single verification command. Each phase is independently committable. If
verification fails at any phase, revert that phase's commit and diagnose before continuing.

### Phase 0 -- Add pytest-xdist dependency (no behaviour change) -- **DONE 2026-05-10 (commit `e41d136`)**

**Files:**

- `requirements-dev.txt` -- add `pytest-xdist==3.7.0` (latest stable)

**Verification:**

```bash
pip install -r requirements-dev.txt
pytest tests/test_config.py -v
```

Pass count must be identical to baseline.

**Why first:** dependency exists before any conftest code reads `PYTEST_XDIST_WORKER`. Reversible
(single-line revert).

### Phase 1 -- Consolidate the reference-data seed into one function -- **DONE 2026-05-10 (commit `073c548`)**

**What landed:** `app/ref_seeds.py` gained `seed_reference_data(session, *, verbose=False)` (the
canonical idempotent seed for every ref-schema table). All three pre-existing call sites now
delegate to it: `tests/conftest.py::_seed_ref_tables` (the per-test path),
`app/__init__.py::_seed_ref_tables` (the dev/test factory eager seed, wrapped in a
`try/except ProgrammingError` for the boot-time-before-tables case), and
`scripts/seed_ref_tables.py::seed_ref_tables` (the production deploy path; passes `verbose=True`).
The function does not commit; callers own the transaction boundary. Verified: 163 targeted tests
passed in 51.75s; pylint score 9.50/10 unchanged.

**Original spec (kept for reference -- now historical):**

**Files:**

- `app/ref_seeds.py` -- add `seed_reference_data(session)` function. Pure, idempotent. Body
  extracted from `tests/conftest.py::_seed_ref_tables` (the canonical version -- mirrors production
  seed semantics). Takes a SQLAlchemy session, returns None. Uses existing `ACCT_TYPE_SEEDS`
  constant.
- `tests/conftest.py` -- `_seed_ref_tables` becomes a 2-line wrapper:
  `from app.ref_seeds import seed_reference_data; seed_reference_data(_db.session)`.
- `app/__init__.py` -- `_seed_ref_tables` (line 861) rerouted to call `seed_reference_data`. Keep
  the outer `try/except sqlalchemy.exc.ProgrammingError` guard since it handles the "tables don't
  exist yet" branch on first-boot.
- `scripts/seed_ref_tables.py` -- rerouted to call `seed_reference_data`.

**Verification:**

```bash
pytest tests/test_models/ tests/test_ref_cache.py tests/test_audit_fixes.py -v
pylint app/ scripts/ --fail-on=E,F
```

Pass count must be identical to baseline. Pylint score must not drop.

**Why second:** the deepest "no test-behaviour change" refactor. If anything breaks here, the cause
is the seed extraction, not the DB plumbing. Lands on its own commit.

### Phase 2 -- Build the template database script -- **DONE 2026-05-11 (commit `2aa3d17`)**

**What landed:** `scripts/build_test_template.py` (335 lines, pylint 10/10 in isolation) drops +
recreates `shekel_test_template` via an admin DSN in autocommit mode (`WITH (FORCE)` to sever any
lingering connections), runs the Alembic chain to head via `alembic.command.upgrade` (matching the
existing `scripts/init_database.py` idiom rather than the plan's `flask_migrate.upgrade()`), applies
the audit infrastructure idempotently, seeds reference data, then truncates `system.audit_log` so
the template ships with a zeroed log (mirrors the per-test pattern in `tests/conftest.py::db`).

**Notable deviation from spec:** the plan called for asserting `EXPECTED_TRIGGER_COUNT == 45` -- by
the time Phase 2 ran the canonical constant in `app.audit_infrastructure.AUDITED_TABLES` was
`len(...) == 31`. The script imports `EXPECTED_TRIGGER_COUNT` rather than hardcoding the number, so
a future addition to `AUDITED_TABLES` automatically flows through the verification step.

**Verification (committed evidence):**

```text
python scripts/build_test_template.py
# Step 1/3: dropped and recreated empty database.
# Step 2/3: migrated to head, applied audit, seeded reference data.
# Step 3/3: verified (18 account types, 31 audit triggers, 0 audit_log rows).
# DONE: shekel_test_template ready.

# Re-run (idempotency): same output, same counts.
```

**Pre-flight outcome (now historical):** the Phase 2a drift check ran on 2026-05-10 and surfaced
four high-severity, one medium-severity, and one low-severity divergence between `db.create_all()`
and `flask db upgrade head` outputs. All seven findings (H-1..H-5 + M-1 + L-1) closed before Phase 2
resumed; the 2026-05-11 re-verification of the comparison script returned only the EXPECTED
divergences (alembic_version, FK names, column ordering, `\restrict`). Catalogue:
`shekel-audits/security-2026-04-15/model-migration-drift.md`.

**Original spec (kept for reference -- now historical):**

**Files (new):**

- `scripts/build_test_template.py` -- idempotent template builder. Pseudocode:
  1. Read env vars: `TEST_TEMPLATE_DATABASE` (default `shekel_test_template`),
     `TEST_ADMIN_DATABASE_URL` (default `postgresql:///postgres`).
  2. Open psycopg2 connection to admin DB with autocommit (required: `CREATE DATABASE` cannot run
     inside a transaction).
  3. `DROP DATABASE IF EXISTS shekel_test_template WITH (FORCE)` then
     `CREATE DATABASE shekel_test_template`.
  4. Close admin connection.
  5. Set `TEST_DATABASE_URL` and `SECRET_KEY` env vars; import `create_app`; build app pointed at
     the new template DB.
  6. In an app context: create the 5 schemas (`ref`, `auth`, `budget`, `salary`, `system`); run
     `flask db upgrade head` (via `flask_migrate.upgrade()` programmatic call -- ensures the
     migration chain is validated against an empty DB on every template rebuild); call
     `app.audit_infrastructure.apply_audit_infrastructure()`; call
     `app.ref_seeds.seed_reference_data(db.session)`; commit.
  7. Verify expected state: `assert ref.account_types row count == 18`;
     `assert system.audit_log row count == 0`;
     `assert pg_trigger count of audit_* triggers == EXPECTED_TRIGGER_COUNT (45)`.
  8. Print one-line summary on success.

**Pre-flight drift check (phase 2a):** before running the template build for real, run
`flask db upgrade head` against a temporary empty DB to detect any model-vs-migration drift in the
current codebase. If the migration produces a schema that differs from `db.create_all()`'s output
(which today's conftest uses), the test suite has been masking the drift. Surface this as a known
issue BEFORE switching the template to migrations. (If drift is found, stop and surface to user; the
drift fix is out of scope for this work.)

**Verification:**

```bash
# Pre-flight drift check (one-off, manual)
createdb shekel_drift_check
flask db upgrade head  # against a clean shekel_drift_check
# Compare schema with `db.create_all()` output

# Template build itself
python scripts/build_test_template.py
psql shekel_test_template -c "SELECT count(*) FROM ref.account_types;"  # must return 18
psql shekel_test_template -c "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%' AND NOT tgisinternal;"  # must return 45
psql shekel_test_template -c "SELECT count(*) FROM system.audit_log;"  # must return 0

# Idempotency
python scripts/build_test_template.py  # re-run, must succeed
psql shekel_test_template -c "SELECT count(*) FROM ref.account_types;"  # still 18
```

**Why third:** new script, no existing test paths touched. Run it manually before touching conftest.
Lands on its own commit.

### Phase 3 -- Wire conftest to per-worker DB cloning -- **DONE 2026-05-11 (commit `db95461`)**

**What landed:** `tests/conftest.py` gained a module-level `_bootstrap_worker_database()` (165-line
function with full docstring covering xdist-master detection, orphan cleanup via `pg_stat_activity`,
template existence check, clone via `CREATE DATABASE ... TEMPLATE`, post-clone row-count
verification, and the `TEST_DATABASE_URL` env-var write that must precede the first
`from app import ...`). Result stored in module-level `_BOOTSTRAP_RESULT` for `pytest_sessionfinish`
to key off. `setup_database` collapsed from ~50 lines to 4 (only refreshes ref_cache; cloned
template already has schemas + tables

- audit infra + seed). `pytest_sessionfinish` drops the
per-session DB via psycopg2 `WITH (FORCE)`. `_db.engine.dispose()` added to the per-test `db`
fixture teardown. The now-unused `_create_audit_infrastructure` helper was removed.

**Notable deviation from spec:** the plan called for `pytest_sessionfinish` to invoke
`_db.session.remove()` and `_db.engine.dispose()` before dropping the database. Implementation
revealed Flask-SQLAlchemy 3.x scopes `db.session` and `db.engine` to the current app context, and
the session-scoped `app` fixture has already torn down by the time `pytest_sessionfinish` runs --
those calls raise `RuntimeError("Working outside of application context")`. Two options considered:

- Keep the session-scoped `app` alive via a module-level reference
  so the hook could push its context. Rejected as additional
  global mutable state for a marginal cleanliness gain.
- Build a fresh `create_app("testing")` inside the hook. Rejected
  because the new app would open connections to the about-to-be-
  dropped DB.

Chosen: psycopg2-only drop with `WITH (FORCE)` as the safety net (the per-test `db` fixture already
disposes the engine after every test, so by the time the hook runs there are no live SQLAlchemy
connections to release). Reasoning is in the hook's docstring.

**Verification (committed evidence):**

```text
Batch 1 (config+models+services):  1756 passed in 9:21
Batch 2 (routes a/c):                857 passed in 4:40
Batch 3 (routes d-i):                392 passed in 2:17
Batch 4 (routes l/m/o/p):            288 passed in 1:39
Batch 5 (routes r/s/t/x):            689 passed in 4:01
Batch 6 (integration):               220 passed in 1:17
Batch 7 (adversarial+scripts+deploy): 545 passed in 2:59
Batch 8 (audit+ref+schemas+utils+concurrent+performance):
                                     401 passed in 2:02
Total:                              5148 passed (~28 min sequential)

Concurrent invocation gate (two pytest runs in two terminals):
   Run #1 test_audit_migration.py: 11 passed (5.13s)
   Run #2 test_audit_migration.py: 11 passed (4.76s)
   No deadlock errors, both runs independent.

Cleanup: post-session psql -l shows only shekel_test (legacy,
untouched) and shekel_test_template; no shekel_test_main_* orphans.
pylint app/ stays at 9.50/10.
```

**Original spec (kept for reference -- now historical):**

**Files:**

- `tests/conftest.py` -- module-level bootstrap and `pytest_sessionfinish` hook. Reorganised as:
  1. **Top of file (before any `app` import):** new `_bootstrap_worker_database()` function that:
     - Detects xdist master:
       `if os.environ.get("PYTEST_XDIST_TESTRUNUID") and not os.environ.get("PYTEST_XDIST_WORKER"): return None`.
       Master skips bootstrap entirely.
     - Computes `worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")`,
       `db_name = f"shekel_test_{worker_id}_{os.getpid()}"`.
     - Reads admin URL from env (`TEST_ADMIN_DATABASE_URL`, default `postgresql:///postgres`).
     - Opens psycopg2 admin connection with autocommit.
     - Cleans up orphans:
       `SELECT datname FROM pg_database WHERE datname LIKE 'shekel_test_{worker_id}_%'` minus any
       DBs with active connections in `pg_stat_activity`. Drops the rest. (Avoids the PID-reuse trap
       by checking `pg_stat_activity` not just name patterns.)
     - Verifies the template exists:
       `SELECT 1 FROM pg_database WHERE datname = 'shekel_test_template'`. If missing, raise with
       `RuntimeError("Test template database 'shekel_test_template' not found. Run: python scripts/build_test_template.py")`.
     - Issues `CREATE DATABASE "{db_name}" TEMPLATE "shekel_test_template"`.
     - Closes admin connection.
     - Verifies the new DB is correct: open a brief psycopg2 connection to `{db_name}`, count
       `ref.account_types` rows. If != 18, raise with "template appears corrupted, rebuild with
       `python scripts/build_test_template.py`."
     - Returns `(db_name, admin_url)`.
  2. **Just after the bootstrap call:** if a bootstrap happened, `os.environ["TEST_DATABASE_URL"]`
     is set to the per-session URL. Existing `os.environ.setdefault("SECRET_KEY", ...)` line stays
     where it is.
  3. **Existing `from app import create_app` and `from app.extensions import db as _db` imports stay
     where they are** -- they now read the per-session URL.
  4. **Simplify `setup_database` fixture** (currently lines 118-166): remove `_db.create_all()`,
     `_create_audit_infrastructure()`, `_seed_ref_tables()`, and the teardown block
     (`_db.drop_all()` + `DROP SCHEMA CASCADE`). Keep ONLY the
     `_refresh_ref_cache_and_jinja_globals(app)` call and the `yield`. The cloned template already
     has schemas + tables + audit triggers + ref data; the only Python-side initialisation needed is
     the in-process ref_cache.
  5. **Add `pytest_sessionfinish(session, exitstatus)` at module level** that:
     - Returns early if bootstrap was skipped (master).
     - Calls `_db.session.remove()` and `_db.engine.dispose()` to release all SQLAlchemy connections
       to the per-session DB.
     - Opens psycopg2 admin connection.
     - Issues `DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)` (the WITH FORCE clause handles any
       lingering backend that escaped the engine.dispose; requires PG 13+, which the project's PG 16
       covers).
     - Closes admin connection.
  6. **Per-test `db` fixture (lines 168-266) stays exactly as-is.** TRUNCATE + reseed + audit_log
     truncate + ref_cache refresh -- the existing per-test cleanup remains the contract every test
     depends on.
  7. **Add `_db.engine.dispose()` to the `db` fixture teardown** (after `_db.session.remove()`) to
     defensively release any leaked NullPool connection that a previous test might have held --
     belt-and-braces protection so per-test isolation cannot be subtly compromised by
     connection-pool state.

**Verification:**

```bash
# Single-process sanity check
pytest tests/test_config.py -v
pytest tests/test_models/ -v
pytest tests/test_ref_cache.py tests/test_audit_fixes.py -v

# Per-session DB is dropped after pytest exits
psql -l | grep shekel_test_main_  # must be empty

# Two concurrent invocations no longer deadlock
# (run these in two terminals at the same time)
pytest tests/test_models/test_audit_migration.py -v   # terminal 1
pytest tests/test_models/test_audit_migration.py -v   # terminal 2
# Both must pass independently; neither shows "OperationalError: deadlock detected"
```

All pass counts must equal baseline.

**Why fourth:** substantive change. Lands on its own commit. If verification fails at any sub-step,
revert and diagnose.

### Phase 4 -- Enable pytest-xdist parallelism and mark cluster-state tests -- **DONE 2026-05-11 (commit `511132f`, plus `-n 12` default in `0937975`)**

**What landed:** `pytest.ini` registers `xdist_group(name)` as a marker and adds `--dist=loadgroup`
to `addopts` (without that flag the marker is metadata-only under pytest-xdist's default
`--dist=load`). The marker itself moved to the entire `tests/test_models/test_audit_migration.py`
module via `pytestmark = pytest.mark.xdist_group("shekel_app_role")`.

**Notable deviation from spec:** the plan called for the marker on the `TestLeastPrivilegeRole`
class only. Implementation verification at -n 4 surfaced a deeper coupling: `TestApplyIdempotent`
and `TestRoundTrip` (sibling classes in the same file) call `apply_audit_infrastructure` whose
`_GRANT_APP_ROLE_SQL` conditionally `GRANT`s privileges to `shekel_app` WHEN the role exists. With
only the class-level marker, those sibling tests on other workers would silently leak GRANTs into
their per-session DBs while `TestLeastPrivilegeRole` had the role alive; the `shekel_app_role`
fixture teardown's `DROP OWNED BY` only scopes to its own database, so the cross-database grants
would block `DROP ROLE` with `DependentObjectsStillExist`. Pinning the entire module to one worker
sidesteps the race; other test files continue to fan out across workers normally.

**Bonus that landed in commit `0937975`:** `-n 12` was added to `pytest.ini` `addopts` after Phase
4.5 profiling on a 24-core host revealed PG's cluster-wide WAL/fsync pipeline (not CPU) is the
serialised resource past -n 12. Scaling table:

| Workers | Batch 1 wall-clock | Speedup | Efficiency |
|---|---|---|---|
| -n 1 (seq) | 561s | 1.00x | 100% |
| -n 4 | 218s | 2.57x | 64% |
| -n 8 | 114s | 4.93x | 62% |
| -n 12 | 79s | 7.07x | 59% |
| -n 16 | 63s | 8.95x | 56% |

-n 12 is the sweet spot where most parallelism is captured without the thrash risk of saturating the
WAL queue. Override with `-n 0` for single-process debugging.

**Verification (committed evidence):**

```text
pytest tests/test_models/test_audit_migration.py -n 4:
  11 passed in 6.78s, all on gw0 (marker correctly pins).
pytest tests/test_models/ -n 4:
  148 passed in 20.87s (vs 46.06s sequential -- 2.2x speedup).
pytest tests/test_routes/test_a* test_c* -n 4:
  857 passed in 1:49 (vs 4:40 sequential -- 2.6x speedup).
pytest tests/test_config.py tests/test_models/ tests/test_services/ -n 4:
  1756 passed in 3:38 (vs 9:21 sequential -- 2.5x speedup).
pytest tests/test_adversarial/ tests/test_scripts/ tests/test_deploy/ -n 4:
  545 passed in 1:21 (vs 2:59 sequential -- 2.2x speedup).
pytest tests/test_models/test_audit_migration.py (no -n):
  11 passed in 3.72s -- single-process unaffected.
```

**Original spec (kept for reference -- now historical):**

**Files:**

- `tests/test_models/test_audit_migration.py` -- add `@pytest.mark.xdist_group("shekel_app_role")`
  to the `TestLeastPrivilegeRole` class (line 389) and any sibling class that uses the
  `shekel_app_role` fixture. Cluster-level roles cannot be created/dropped concurrently across
  workers.
- `pytest.ini` -- register the `xdist_group` marker in a `markers =` section to silence pytest's
  "unknown marker" warning under strict-markers (defensive; the project doesn't currently use strict
  markers but the registration is no-cost).

**Verification:**

```bash
# Multi-worker basic
pytest tests/test_models/ -n 4 -v

# Multi-worker with the cluster-role tests
pytest tests/test_models/test_audit_migration.py -n 4 -v

# Confirm the marker serialises correctly
pytest tests/test_models/test_audit_migration.py -n 4 --collect-only -q | head -30

# Multi-worker route batch
pytest tests/test_routes/test_a* tests/test_routes/test_c* -n 4 -v
```

All pass. No "role already exists" or "role does not exist" errors. Per-worker DBs cleaned up on
exit.

**Why fifth:** xdist is the user-visible parallelism change. Lands on its own commit.

### Phase 5 -- Update CI workflow and documentation -- **DONE 2026-05-11 (commits `8ab5dca` (CI) + `825cfea` (docs))**

**What landed (CI -- `8ab5dca`):** `.github/workflows/ci.yml` gains `TEST_ADMIN_DATABASE_URL` in the
job env block (consumed by both `scripts/build_test_template.py` and the conftest bootstrap; points
at the maintenance `postgres` DB so `DROP DATABASE` is not blocked by the connection being to the
database it is trying to drop). A new "Build test template database" step runs
`python scripts/build_test_template.py` between the lint and pytest steps. Stale comments on the
postgres service block and on `TEST_DATABASE_URL` are refreshed to explain that the conftest
bootstrap overrides `TEST_DATABASE_URL` per session at runtime.

**Notable deviations from spec (CI):**

- The original plan called for an explicit `ALTER ROLE
  shekel_test CREATEDB` step (either via `POSTGRES_INITDB_ARGS`
  or a setup `psql` step). Skipped in the as-shipped change:
  the postgres Docker image creates `POSTGRES_USER` as a
  SUPERUSER by documented behaviour, so `CREATEDB` is already
  implicit. An `ALTER ROLE` step would be redundant (and would
  have to connect as `shekel_test` itself, which only works
  because of the very superuser attribute that makes the
  explicit grant unnecessary). A one-line comment in the env
  block documents the reasoning.
- The pytest invocation stays as bare `pytest --tb=short -q`
  rather than the plan's `-n auto` -- `pytest.ini` `addopts`
  already carries `-n 12 --dist=loadgroup` from commit
  `0937975`, so the CLI flag would be redundant. `-n 12` is
  also the empirically-tuned sweet spot from Phase 4.5
  profiling, whereas `-n auto` would scale with the runner's
  CPU count (4 on `ubuntu-latest`); the WAL/fsync pipeline
  serialisation that plateaus past `-n 12` means `-n 12` >=
  `-n 4` even on a 4-core runner.

**What landed (docs -- `825cfea`):**

- `CLAUDE.md`: the `## Tests -- 5,100+ tests, ~28 min full
  suite -- NEVER run as one command` block (a hard "always
  batch, never concurrent" warning) is rewritten as `## Tests
  -- 5,148 tests, ~4 min full suite at -n 12 (pytest-xdist
  default)`. New body documents the `-n 12` default + the
  concurrent-safe bootstrap, adds a first-time-setup line for
  `python scripts/build_test_template.py`, and cross-references
  `docs/testing-standards.md`. The `## Common Commands` tests
  line is also refreshed (previously "~13 minutes (3200+
  tests), always run in batches"; now reflects the actual ~4
  min / 5,148 tests and surfaces the template build).
- `docs/testing-standards.md`: `## Test Run Guidelines`
  rewritten -- full suite ~4 min at `-n 12`, single invocation
  viable, concurrent runs safe, override via `-n 0` / `-n auto`
  with rationale, 30s per-test timeout note. The 8-batch table
  is preserved as `### Optional per-directory batching` with
  refreshed `-n 12` and `-n 0` columns (the `-n 12` figures are
  derived from the Phase 4.5 batch-1 measurement applied to the
  sequential numbers; treated as ballpark, not measured per-
  batch). New `## Building the test template` section
  documents the script, three rebuild triggers (migrations,
  `app/ref_seeds.py` edits, `app/audit_infrastructure.py`
  edits), the bootstrap error path, and the
  `TEST_ADMIN_DATABASE_URL` environment. New `## Cluster-state
  tests and \`xdist_group\`` section explains the
  per-database vs cluster-state distinction, documents the
  module-level marker pattern from `test_audit_migration.py`,
  notes the`--dist=loadgroup` dependency, and warns about the
  intermittent failure modes you get without the marker.

**Verification (committed evidence):**

- CI YAML parsed cleanly via `python -c "import yaml;
  yaml.safe_load(open('.github/workflows/ci.yml'))"`.
- Pylint `app/` stayed at `9.50/10` (no code paths changed).
- No test run -- no code paths changed, only docs + CI config.
  CI itself will exercise the full path on the next push to
  `origin/dev`.

**Original spec (kept for reference -- now historical):**

**Files:**

- `.github/workflows/ci.yml`:
  1. Add `TEST_ADMIN_DATABASE_URL: postgresql://shekel_test:shekel_test@localhost:5432/postgres` to
     the job's `env` block (admin connection points at the maintenance DB, not the test DB).
  2. Update Postgres service to grant the `shekel_test` role `CREATEDB` privilege (add
     `POSTGRES_INITDB_ARGS` or a setup step that runs `ALTER ROLE shekel_test CREATEDB`).
  3. Add a new step BEFORE `Run tests`: `Build test template` running
     `python scripts/build_test_template.py`.
  4. Change the `Run tests` step's command from `pytest --tb=short -q` to
     `pytest --tb=short -q -n auto`.
- `docs/testing-standards.md`:
  1. Replace the "NEVER run two pytest processes concurrently" warning (was added in this session)
     with the new guarantee: "Concurrent invocations are safe -- each session gets its own DB cloned
     from `shekel_test_template`."
  2. Add a "Building the test template" subsection: how to build it
     (`python scripts/build_test_template.py`), when to rebuild (after migrations, after
     `app/ref_seeds.py` changes), and where to look if it's missing.
  3. Add a "Cluster-level tests" subsection documenting the `xdist_group` pattern for the
     `shekel_app_role` tests, so future role-touching tests adopt the same marker.
  4. Update the batch table to reflect xdist-parallel wall-clock estimates (full suite ~6-10 min
     with `-n auto` vs ~28 min sequential).
- `CLAUDE.md`:
  1. Replace the "NEVER run concurrent pytest processes" line with: "Concurrent invocations are safe
     (each session gets its own per-pytest DB cloned from `shekel_test_template`). Use
     `pytest -n auto` to parallelise within one invocation."
  2. Add a one-line first-time setup note: "Build the test template once:
     `python scripts/build_test_template.py`."

**Verification:**

- Re-run the CI workflow on a feature branch. Confirm green.
- Read each doc top-to-bottom. The new flow should be discoverable without prior context.

**Why last:** documentation updates are riskless once behaviour is verified. CI update can land in
the same commit since it's a small, surgical change.

## Critical files to be modified

| File | Phase | Change type |
|---|---|---|
| `requirements-dev.txt` | 0 | Add `pytest-xdist` |
| `app/ref_seeds.py` | 1 | Add `seed_reference_data(session)` function |
| `tests/conftest.py` | 1, 3 | Reroute `_seed_ref_tables` (P1); module-level bootstrap + sessionfinish + simplify `setup_database` + add `engine.dispose` to `db` teardown (P3) |
| `app/__init__.py` | 1 | Reroute `_seed_ref_tables` (line 861) to call `seed_reference_data` |
| `scripts/seed_ref_tables.py` | 1 | Reroute to call `seed_reference_data` |
| `scripts/build_test_template.py` | 2 | NEW file -- template builder using `flask db upgrade head` |
| `tests/test_models/test_audit_migration.py` | 4 | Add `@pytest.mark.xdist_group("shekel_app_role")` on `TestLeastPrivilegeRole` and sibling classes that use `shekel_app_role` fixture |
| `pytest.ini` | 4 | Register `xdist_group` marker |
| `.github/workflows/ci.yml` | 5 | Add CREATEDB grant, add template-build step, change pytest invocation to `-n auto` |
| `docs/testing-standards.md` | 5 | Replace deadlock warning, document template build, document `xdist_group` |
| `CLAUDE.md` | 5 | Replace deadlock warning, document first-time template build |

## Existing functions/utilities to reuse

- `app/audit_infrastructure.py::apply_audit_infrastructure(executor)` -- already idempotent, already
  exported, already in use by current `tests/conftest.py::_create_audit_infrastructure`. Called from
  the template builder.
- `app/audit_infrastructure.py::AUDITED_TABLES` and `EXPECTED_TRIGGER_COUNT` -- used by the template
  builder's verification step.
- `app/ref_seeds.py::ACCT_TYPE_SEEDS` -- the canonical account-type seed list; consumed by the new
  `seed_reference_data` function.
- `tests/conftest.py::_refresh_ref_cache_and_jinja_globals` -- already in use; the simplified
  `setup_database` continues to call it.
- `flask_migrate.upgrade(directory, revision)` -- used by `scripts/build_test_template.py` to run
  migrations against the freshly-created template DB.
- The current `db` fixture's TRUNCATE block (lines 196-229) and the table list it cites are correct
  and preserved verbatim.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `flask db upgrade head` exposes existing model-vs-migration drift on first template build | Medium -- the suite has been masking this for some time | Phase 2a pre-flight drift check. If drift detected, surface to user before continuing. Drift fix is out of scope for this work. |
| User runs pytest before building the template | High (first-time setup) | Bootstrap raises with explicit "Run: Python scripts/build_test_template.py" |
| Crashed pytest leaks per-session DB | Low | Bootstrap cleans up orphans using `pg_stat_activity` not just name patterns. PID-reuse trap avoided. |
| Two xdist workers race on `shekel_app` role creation | High under parallel runs | `@pytest.mark.xdist_group("shekel_app_role")` forces serialisation onto one worker |
| `pytest_sessionfinish` not called on SIGKILL | Medium | Next session's bootstrap cleanup handles orphans |
| `DROP DATABASE WITH (FORCE)` not supported on PG <13 | None (project uses PG 16 in prod and CI) | Document the version requirement in `scripts/build_test_template.py` |
| CI fails on first push after merge | Low (Phase 5 updates CI in the same change) | CI updates land before merge |
| Per-session DB name length exceeds PostgreSQL 63-char limit | None (longest realistic name is ~30 chars) | No mitigation needed |
| Connection pool in the secondary apps used by fresh-app tests (test_errors, test_auth, test_rate_limiter) holds connections that block DROP DATABASE | Low | Each fresh-app test already calls `_db.engine.dispose()` in teardown; `WITH (FORCE)` is a final safety net |
| `app/__init__.py::_seed_ref_tables` silently swallows `ProgrammingError` and quietly writes a stale schema | Low after Phase 1 | Phase 1 consolidates the three seed call sites; future column-shape changes propagate through `seed_reference_data` consistently |

## Out of scope (documented as follow-ups)

- Refactoring `scripts/seed_user.py` and `scripts/seed_tax_brackets.py` to share more logic with the
  test paths. The test paths use fixtures (`seed_user`, `seed_full_user_data`) that are
  intentionally different from production seeds.
- Adding a `scripts/clean_test_databases.py` utility for manual operator cleanup. The conftest's
  startup orphan-cleanup covers the common case; an explicit utility is a future convenience.
- Renaming `shekel_test` to something more neutral. The test DB name appears in CI hardcodes and
  `.env.example`; a rename is a separate cleanup pass.
- Switching the per-test cleanup from TRUNCATE to per-test template clone (Pattern C). Pattern C was
  evaluated and rejected for this codebase -- see the comparison in the planning discussion. The
  TRUNCATE pattern is the right contract for the audit-trail invariants.

## End-to-end verification (after all phases land)

Run, in order:

```bash
# 1. Single-process suite, every directory
pytest tests/test_config.py tests/test_models/ tests/test_services/ -q
pytest tests/test_routes/test_a* tests/test_routes/test_c* -q
pytest tests/test_routes/test_d* tests/test_routes/test_e* tests/test_routes/test_g* tests/test_routes/test_h* tests/test_routes/test_i* -q
pytest tests/test_routes/test_l* tests/test_routes/test_m* tests/test_routes/test_o* tests/test_routes/test_p* -q
pytest tests/test_routes/test_r* tests/test_routes/test_s* tests/test_routes/test_t* tests/test_routes/test_x* -q
pytest tests/test_integration/ -q
pytest tests/test_adversarial/ tests/test_scripts/ tests/test_deploy/ -q
pytest tests/test_audit_fixes.py tests/test_ref_cache.py tests/test_schemas/ tests/test_utils/ tests/test_concurrent/ -q

# 2. Parallel suite via xdist
pytest -n auto -q

# 3. Concurrent invocations (proves the deadlock is gone)
# Run in two terminals at once:
pytest tests/test_models/test_audit_migration.py -v   # terminal A
pytest tests/test_models/test_audit_migration.py -v   # terminal B

# 4. Pylint
pylint app/ scripts/ --fail-on=E,F

# 5. CI
# Push to a feature branch; confirm the CI workflow goes green.

# 6. Cleanup verification
psql -l | grep '^[[:space:]]*shekel_test_'
# Should show only `shekel_test_template`. No per-session DBs left over.
```

Each step's pass count must equal or exceed the baseline (5,131 tests). No failed, errored, xfailed,
or unexpected-passed lines are tolerated.

## Time estimate

- Phase 0: 10 minutes -- **Done in ~5 min; commit `e41d136`**
- Phase 1: 1 hour -- **Done in ~45 min; commit `073c548`**
- Drift fix (out-of-original-scope insertion): ~5-6 hours estimate --
  **Done in ~3 hours across seven commits `9a5cca1`..`49fa13b`; see `shekel-audits/security-2026-04-15/model-migration-drift.md`**
- Phase 2: 1.5 hours -- **Done in ~1 hour; commit `2aa3d17`**
- Phase 3: 2 hours --
  **Done in ~1.5 hours including the Flask-SQLAlchemy 3.x app-context deviation; commit `db95461`**
- Phase 4: 30 minutes -- **Done in ~45 min including the module-level marker scope-up; commits
  `511132f` (markers) + `0937975` (`-n 12` default + Phase 4.5 profiling)**
- Phase 5: 1 hour -- **Done in ~45 min including the CREATEDB-grant and `-n auto` deviations;
  commits `8ab5dca` (CI) + `825cfea` (docs)**

**Realised total:** ~7.75 hours wall-clock across three sessions, covering all phases (0 through 5)
plus the drift fix plus the Phase 4.5 performance profile.

Each phase is independently committable and revertable. All changes through `825cfea` live on `dev`;
not yet pushed to `origin/dev`.

## Resuming from a fresh session (historical)

**This section is historical. All phases are now complete (see the status table at the top of this
document); the procedure below was the hand-off plan used between sessions while Phase 5 was the
outstanding work, preserved as a record of how that hand-off was structured.**

Phases 0 through 4 are complete on `dev`. The only remaining work is Phase 5 (CI workflow + docs).
Entry points:

- This document (status table at the top).
- `shekel-audits/security-2026-04-15/model-migration-drift.md`
  (drift fix retrospective; all seven findings resolved).
- `docs/audits/test_improvements/test-performance-research.md`
  (Phase 4.5 profile + community research; operator decision
  pending on whether to optimise the remaining 230ms TRUNCATE
  cost).

### 1. Verify current state

```bash
git log --oneline -12 | grep -E "(e41d136|073c548|2aa3d17|db95461|511132f|0937975)"
# should show all six landing commits (Phases 0, 1, 2, 3, 4, and the
# -n 12 default that landed alongside Phase 4)

git status
# working tree should be clean of these changes (apart from any
# in-progress edits unrelated to this plan)

grep "pytest-xdist" requirements-dev.txt   # 3.8.0
grep "seed_reference_data" app/ref_seeds.py    # exists
ls scripts/build_test_template.py          # exists
grep "_bootstrap_worker_database" tests/conftest.py    # exists
grep "xdist_group" pytest.ini              # marker registered + -n 12 + --dist=loadgroup in addopts
grep "pytestmark = pytest.mark.xdist_group" tests/test_models/test_audit_migration.py    # module-level marker
```

If any of those checks fail, the prior phases have been reverted; re-read the relevant phase section
above and re-run its verification commands before continuing.

### 2. Phase 5 -- CI workflow + docs (the only remaining work)

Files:

- `.github/workflows/ci.yml`:
  - Add `TEST_ADMIN_DATABASE_URL: postgresql://shekel_test:shekel_test@localhost:5432/postgres` (or
    appropriate DSN) to the job's `env` block.
  - Grant the CI test role `CREATEDB` privilege (it needs to create per-session DBs cloned from the
    template).
  - Add a setup step BEFORE `Run tests` that runs `python scripts/build_test_template.py`.
  - The pytest invocation can drop the `-n auto` suggestion from the original plan -- `pytest.ini`
    `addopts` now carries `-n 12` and `--dist=loadgroup`, so a bare `pytest --tb=short -q` is
    sufficient.
- `docs/testing-standards.md`:
  - Replace the "NEVER run two pytest processes concurrently" warning -- now obsolete after Phase 3.
  - Refresh the per-batch timings table (now ~3x stale at the new `-n 12` default).
  - Add a "Building the test template" subsection documenting
    `python scripts/build_test_template.py` and when to rebuild (after migrations, after
    `app/ref_seeds.py` or `app/audit_infrastructure.py` changes).
  - Document the `xdist_group` pattern so future tests that touch cluster-wide state adopt the same
    marker.
  - Note that single-invocation full-suite runs are now viable (~4 min at `-n 12`, well under the
    10-min timeout) -- the 8-batch split is no longer mandatory but remains supported for slow-PG
    scenarios.
- `CLAUDE.md`:
  - Replace the "NEVER run concurrent pytest processes" line with: "Concurrent invocations are safe
    (each pytest session gets its own per-session DB cloned from `shekel_test_template`). Default is
    `-n 12` workers; override with `-n 0` for debugging."
  - Add a first-time-setup note: "Build the test template once:
    `python scripts/build_test_template.py`."
  - Update the "Tests -- 5,100+ tests, ~28 min full suite -- NEVER run as one command" line; at
    `-n 12` the full suite is ~4 min.

### 3. Decide on the Phase 4.5 performance research

The 230ms-per-test TRUNCATE CASCADE is the remaining floor.
`docs/audits/test_improvements/test-performance-research.md` catalogues three tiered options:

- **Tier A (no test-code rewrite, ~2-4 hours):** DELETE-of-empty-tables
  - `session_replication_role=replica` during seed + PG
  `synchronous_commit=off` on the test cluster. Expected: ~4 min -> ~1.5-2 min.
- **Tier B (hybrid SAVEPOINT, ~1-3 days):** Django-style split with
  audit-asserting tests marked. Expected: another 30-50% off Tier A.
- **Tier C (PG 18 + reflink, larger infra change):** per-test
  TEMPLATE clone. Expected: ~20-40s full suite.

No commitment yet; pending operator review. Phase 5 does NOT depend on any of these landing.

### 4. Helpful pointers

- The project follows the rules in `CLAUDE.md`, especially: do not
  modify code outside the current task's scope, do not introduce
  TODOs, fix root causes not symptoms, never rewrite a test to
  make it pass.
- Pylint must stay at 9.50/10 or better on `app/`. Run
  `pylint app/ --fail-on=E,F` after any change.
- Concurrent pytest invocations are now safe (Phase 3 closed the
  TRUNCATE deadlock). Two terminals running `pytest` against the
  same TEST_ADMIN cluster will each get their own per-session DB.
- The session-scoped `app` fixture in `tests/conftest.py` is the
  load-bearing scaffolding. Read it end-to-end before changing
  fixture behaviour.
- The audit-trigger invariants (`app/audit_infrastructure.py`,
  `system.audit_log`) are the strictest correctness contract in
  the codebase. Any change to per-test isolation must preserve
  them; SAVEPOINT-rollback was specifically rejected for this
  reason in the original plan -- see "Architectural decisions"
  above.
- Use `TEST_ADMIN_DATABASE_URL` to point the bootstrap at the
  right admin DSN. Project convention: PG on `localhost:5433` with
  `shekel_user`/`shekel_pass` (override per environment).
