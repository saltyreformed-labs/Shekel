# Phase 1 test-suite flake investigation

**Filed:** 2026-05-12 **Author:** Phase 1 implementation session **Status:** Open -- not yet
root-caused or fixed. Recommended response paths captured below. **Scope:** test-only. No production
code paths affected.

---

## Executive summary

After `test-performance-implementation-plan.md` Phase 1 (Phase 1a docker-compose tmpfs + `fsync=off`
/ `synchronous_commit=off` / `full_page_writes=off`; Phase 1b conftest
`SET LOCAL session_replication_role='replica'` during ref-seed + `system.audit_log` folded into the
existing `TRUNCATE main CASCADE` statement) was applied, full-suite runs at the default `-n 12`
xdist parallelism are intermittently flaky at a ~50-70 % per-run rate. Two failure shapes recur and
never co-occur in the same run.

The flake is **environmentally triggered** -- it does not reproduce on the baseline
`docker-compose.dev.yml` (no tmpfs, no durability knobs) with the same Phase 1b conftest. Two
consecutive baseline runs passed clean. The flake also does not reproduce on the failing tests in
isolation or on a smaller subset of files. It only reproduces in parallel full-suite execution
against the Phase 1a cluster.

Both failure modes are pre-existing test-isolation bugs that the slow baseline suite masked. The
faster Phase 1 runtime compresses the per-test cycle from ~300 ms to ~35 ms and crosses a threshold
where the underlying isolation hazards become visible.

Phase 1's performance gains (89 % off the fixture floor, ~4 min -> 52 s full suite) are real and
independently verifiable in single-process runs, audit-asserting tests, and deterministic xdist
runs. This document captures the flake patterns so they can be fixed in a focused follow-up
(provisionally "Phase 1d") rather than blocking commit of Phase 1.

---

## Reproduction

Default conditions:

- Branch: `dev`, with the Phase 0 + Phase 1a + Phase 1b changes applied (uncommitted as
  of 2026-05-12).
- Host: Arch Linux x86_64, kernel 7.0, btrfs root.
- Test cluster: `shekel-dev-test-db` container running PG 16 on `localhost:5433`, with
  `tmpfs:/var/lib/postgresql/data:rw,uid=70,gid=70,size=2g` mounted and the durability
  trio (`fsync=off`, `synchronous_commit=off`, `full_page_writes=off`) on the postgres
  command line.
- Test template: built once via `python scripts/build_test_template.py` after the
  container restart that engaged Phase 1a.

Reproducing the flake:

```bash
TEST_ADMIN_DATABASE_URL='postgresql://shekel_user:shekel_pass@localhost:5433/postgres' \
    .venv/bin/pytest --tb=line
```

Three or four consecutive invocations are usually enough to hit at least one failure. At least one
of the two failure shapes typically appears in 50-70 % of runs. Most runs that fail show one shape
exclusively; mixed runs are rare in the captured sample.

Counter-reproduction (negative control):

```bash
git stash push docker-compose.dev.yml -m "phase1a-temp"   # restores baseline cluster
docker compose -f docker-compose.dev.yml up -d test-db
python scripts/build_test_template.py                      # rebuild template on new cluster
pytest --tb=line                                            # same conftest, baseline cluster
```

In the captured session, two consecutive runs of the counter-reproduction returned
`5276 passed, 3 warnings in ~253 s (4:13)` with no failures or errors. The same Phase 1b conftest
produces a clean suite on the baseline cluster and a flaky suite on the Phase 1a cluster.

Single-test reproduction (none observed):

```bash
pytest tests/test_routes/test_xss_prevention.py -n 0           # 92/92 passed
pytest tests/test_routes/test_xss_prevention.py                # 92/92 passed under xdist
pytest tests/test_routes/                                       # 2240/2240 passed under xdist
pytest tests/test_services/test_loan_payment_service.py::TestGetPaymentHistory::test_filters_by_scenario \
       tests/test_services/test_carry_forward_service.py::TestCarryForwardUnpaid::test_carry_forward_only_moves_transactions_for_specified_scenario \
       -n 0                                                     # 2/2 passed in isolation
```

The flake only emerges when these test files run concurrently inside the full suite.

---

## Sample data

Across one captured session of Phase 1 full-suite runs:

| Run | Pass | Fail | Errors | Wall-clock | Failure shape |
|---|---|---|---|---|---|
| 1 | 5275 | 1 | 0 | 52.15 s | scenario uniqueness (carry-forward) |
| 2 | 5133 | 1 | 142 | 51.86 s | HTTP 429 cluster (xss / register / deduction) |
| 3 | 5276 | 0 | 0 | 51.29 s | -- clean -- |
| 4 | 5132 | 1 | 143 | 52.22 s | HTTP 429 cluster (transfer_update_notes / deduction_name) |
| 5 | 5276 | 0 | 0 | 51.91 s | -- clean -- |
| 6 | 5275 | 1 | 0 | 52.48 s | scenario uniqueness (test_non_baseline_scenarios_allowed_alongside_baseline) |
| 7 | 5276 | 0 | 0 | 51.65 s | -- clean -- |
| 8 | 5274 | 2 | 0 | 51.65 s | scenario uniqueness (carry-forward + loan-payment filters_by_scenario) |

Runs against the baseline cluster (same Phase 1b conftest, no Phase 1a tmpfs):

| Run | Pass | Fail | Errors | Wall-clock | Failure shape |
|---|---|---|---|---|---|
| B1 | 5276 | 0 | 0 | 253.22 s | -- clean -- |
| B2 | 5276 | 0 | 0 | 253.71 s | -- clean -- |

---

## Failure shape #1: HTTP 429 cluster (~140 errors)

### Symptom

The pytest summary line reports roughly `5133 passed, 1 failed, 142 errors`. The `-vv -tb=short`
output shows ~140 distinct ERROR lines, each from a different test, all collapsing to the same setup
assertion:

```text
E   AssertionError: auth_client login failed with status 429
```

The assertion fires in `tests/conftest.py::auth_client` (the fixture, not a test):

```python
@pytest.fixture()
def auth_client(app, db, client, seed_user):
    """Provide an authenticated test client."""
    resp = client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    })
    assert resp.status_code == 302, (
        f"auth_client login failed with status {resp.status_code}"
    )
    return client
```

Affected test files include:

- `tests/test_routes/test_xss_prevention.py` (most of the XSS-payload-parametrised tests)
- `tests/test_routes/test_grid.py` (every test that uses `auth_client`)
- `tests/test_routes/test_retirement.py`
- `tests/test_routes/test_errors.py` (the test BEFORE the 429 tests sometimes flakes too)
- Plus other route-level tests that depend on `auth_client`

The exactly-one `FAILED` (vs ERROR) is typically `test_429_includes_retry_after_header` or
`test_429_renders_custom_page` -- the test whose login chain reaches
`assert response.status_code == 429` at a moment when the in-memory limiter storage has already been
exhausted by a sibling test. The other ~140 entries are ERRORs (setup failed) because the
`auth_client` fixture raises before the test body executes.

### Suspected mechanism

- `app/extensions.py:106` declares `limiter = Limiter(key_func=get_remote_address)` as
  a **module-level singleton**. Every Flask app created in the test process binds the
  same `limiter` object via `init_app(app)`.
- `TestConfig` sets `RATELIMIT_ENABLED = False` and `RATELIMIT_STORAGE_URI = "memory://"`,
  so the session-scoped `app` fixture starts with rate limiting disabled.
- `tests/test_routes/test_errors.py::TestErrorPages::test_429_*` (three tests at lines
  30, 70, 105) creates a side-app `rate_app = create_app("testing")`, sets
  `rate_app.config["RATELIMIT_ENABLED"] = True`, then does
  `limiter.enabled = True; limiter.init_app(rate_app)` -- which flips the **global**
  `limiter.enabled` flag back to True and rebinds the singleton's storage to whatever
  `rate_app` resolves at init time. Each test then makes 6 wrong-password POSTs to
  `/login` to exhaust the `5 per 15 minutes` quota, asserts the 429, and cleans up:

  ```python
  with rate_app.app_context():
      _db.engine.dispose()
  if limiter._storage is not None:
      limiter.reset()
  limiter.enabled = False
  ```

- The cleanup attempts to clear the limiter storage and disable the global flag, but
  the call sequence has two known gaps:
  - `limiter.reset()` clears whatever storage the `limiter` singleton currently sees,
    which is `rate_app`'s storage because of the most recent `init_app(rate_app)`. If
    the session-scoped `app` had cached a separate storage instance before the test
    rebind, that storage may not be reset.
  - `limiter.enabled = False` only disables the singleton. Between `test_429_*`
    finishing and the next `auth_client` test running, the session-scoped `app`'s
    state may still reflect "enabled" if Flask-Limiter caches the boolean on
    per-app extension state.
- Under the slow baseline suite, the per-test setup cycle is ~300 ms, so the 15-minute
  rate-limit window's effective per-IP usage spreads out enough that the leak rarely
  matters. Under Phase 1a's ~35 ms cycle, successive `auth_client.post("/login", ...)`
  calls in the same worker fall within milliseconds of the `test_429_*` tests'
  6-attempt exhaustion, and the bucket has not refilled.

### Why this is xdist-only

Each xdist worker is a separate Python process with its own `limiter` singleton. Worker N's
`test_429_*` exhausts worker N's rate-limit bucket. Subsequent tests in worker N hit the bucket.
Tests in workers M != N are unaffected. With `--dist=loadgroup` (the project default) tests in the
same file tend to co-locate on the same worker, which is exactly the failure pattern observed: most
failures are in test files that include `auth_client` and run after `test_errors.py` on the same
worker.

Under single-process pytest (`-n 0`) the same test ordering would still leak rate-limit state, but
pytest runs tests sequentially, so the 15-minute window has ~30 ms per test to drain before the next
attempt -- still inside the per-minute quota window in theory, but in practice 6 + 1 attempts well
under 15 minutes is still in violation. Empirically the single-process suite does pass, but that may
be because `test_errors`'s `test_429_*` runs early in the file ordering and the few `auth_client`
users downstream in the same file happen not to trigger the bucket. Need to verify by running
test_errors first then an auth_client-using file under `-n 0`.

### Recommended fixes (Phase 1d candidates, ranked)

1. **Add `limiter.reset()` to the per-test `db` fixture teardown** (smallest change,
   highest signal):

   ```python
   # In tests/conftest.py, db fixture teardown:
   finally:
       with _profile_step(timings, "teardown"):
           _db.session.remove()
           _db.engine.dispose()
           # Defensive: clear rate-limit state any prior test may
           # have leaked via the global app.extensions.limiter
           # singleton (see phase1-flake-investigation.md).
           from app.extensions import limiter as _limiter
           if _limiter._storage is not None:
               try:
                   _limiter.reset()
               except Exception:   # noqa: broad-except justified for test cleanup
                   pass
           _limiter.enabled = False
       _profile_write_row(nodeid, timings)
   ```

   Cost: 4-5 lines, one extra `_storage` access per test. Catches the leak from any
   test (not just `test_429_*`). Eliminates the dependency on test cleanup completeness.

2. **Rewrite `test_429_*` cleanups to use try/finally and reset the session-scoped app's
   limiter explicitly** (medium change, higher correctness signal):

   ```python
   def test_429_includes_retry_after_header(self, app, seed_user):
       rate_app = create_app("testing")
       rate_app.config["RATELIMIT_ENABLED"] = True
       from app.extensions import limiter
       prior_enabled = limiter.enabled
       try:
           limiter.enabled = True
           limiter.init_app(rate_app)
           rate_client = rate_app.test_client()
           with rate_app.app_context():
               for _ in range(6):
                   response = rate_client.post("/login", ...)
               assert response.status_code == 429
               assert response.headers["Retry-After"] == "900"
       finally:
           with rate_app.app_context():
               from app.extensions import db as _db
               _db.engine.dispose()
           limiter.reset()
           limiter.init_app(app)        # rebind to session-scoped app
           limiter.enabled = prior_enabled
   ```

   Cost: rewrite 3 tests, plus understanding Flask-Limiter's per-app extension state.
   Targets the root cause directly.

3. **Switch to per-test client IP** (largest change, broadest signal): configure each
   test's `client` to send a distinct `X-Forwarded-For` so `get_remote_address` returns
   a unique IP per test. Eliminates the rate-limit class of leaks entirely but may
   conflict with other tests that assert on remote_addr.

(1) is the minimum viable fix and probably what Phase 1d should land first.

---

## Failure shape #2: `uq_scenarios_one_baseline` UniqueViolation on bulk INSERT

### Symptom

The pytest summary line reports `5275 passed, 1 failed` (or `5274 / 2 failed` when two
scenario-touching tests both flake). The failing tests vary: in the captured sample they have
included

- `tests/test_models/test_scenario_constraints.py::TestScenarioBaselineUniqueness::test_non_baseline_scenarios_allowed_alongside_baseline`
- `tests/test_services/test_carry_forward_service.py::TestCarryForwardUnpaid::test_carry_forward_only_moves_transactions_for_specified_scenario`
- `tests/test_services/test_loan_payment_service.py::TestGetPaymentHistory::test_filters_by_scenario`

Each test creates one or more `budget.scenarios` rows with `is_baseline=False` (after the
`seed_user` fixture has already created the `Baseline` scenario with `is_baseline=True` for the same
user). SQLAlchemy 2.0.49's `_exec_insertmany_context` packages the multi-row insert into

```sql
INSERT INTO budget.scenarios (user_id, name, is_baseline, cloned_from_id)
SELECT p0::INTEGER, p1::VARCHAR, p2::BOOLEAN, p3::INTEGER
  FROM (VALUES
        (%(user_id__0)s, %(name__0)s, %(is_baseline__0)s, %(cloned_from_id__0)s, 0),
        (%(user_id__1)s, %(name__1)s, %(is_baseline__1)s, %(cloned_from_id__1)s, 1),
        (%(user_id__2)s, %(name__2)s, %(is_baseline__2)s, %(cloned_from_id__2)s, 2))
       AS imp_sen(p0, p1, p2, p3, sen_counter)
 ORDER BY sen_counter
RETURNING budget.scenarios.id, budget.scenarios.created_at,
          budget.scenarios.updated_at, budget.scenarios.id AS id__1
```

with parameters

```python
{
    'user_id__0': 63, 'name__0': 'What-If A',  'is_baseline__0': False, 'cloned_from_id__0': None,
    'user_id__1': 63, 'name__1': 'What-If B',  'is_baseline__1': False, 'cloned_from_id__1': None,
    'user_id__2': 63, 'name__2': 'What-If C',  'is_baseline__2': False, 'cloned_from_id__2': None,
}
```

and PostgreSQL responds with

```text
psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint
  "uq_scenarios_one_baseline"
DETAIL:  Key (user_id)=(63) already exists.
```

### Why this is puzzling

`uq_scenarios_one_baseline` is a partial unique index:

```sql
-- verified via psql \d budget.scenarios:
CREATE UNIQUE INDEX uq_scenarios_one_baseline
    ON budget.scenarios USING btree (user_id)
    WHERE (is_baseline = true)
```

By definition the index only contains rows with `is_baseline = true`. The new rows being inserted
all have `is_baseline = false` (per the parameter dict logged in the exception). None of them should
be added to the partial index. None of them should collide with the existing `Baseline` row that
`seed_user` already inserted for `user_id=63`.

### Timeline -- what changed in the days before the flake surfaced

Investigated 2026-05-12 in response to the developer's "could this be related to C-41?"
hypothesis.

| Date / commit | Change | Effect on test cluster |
|---|---|---|
| (long ago) `c5d6e7f8a901_add_positive_amount_check_constraints.py` (migration #7 in the chain) | Creates `uq_scenarios_one_baseline` partial unique index on `budget.scenarios (user_id) WHERE is_baseline = TRUE`. | Test template carries the index from this migration onward.  Any test workload that bulk-inserted `is_baseline=False` rows after a `Baseline` row had been seeded would already have been at risk of triggering the flake -- IF the failure mode were purely SQL-shape-dependent. |
| **2026-05-10 22:05 (`709786a fix(models): declare uq_scenarios_one_baseline partial unique index (H-2)`)** | Adds the partial unique index to the **model declaration** (`app/models/scenario.py::Scenario.__table_args__`).  **Also adds the test file** `tests/test_models/test_scenario_constraints.py`, including `test_non_baseline_scenarios_allowed_alongside_baseline` -- the test that bulk-inserts three `is_baseline=False` rows and is the canonical Phase 1 failure reproducer. | (a) `db.create_all()` paths (legacy test bootstrap before per-worker DB cloning) now produce the index alongside the migration path.  (b) New test exercises a SQLAlchemy 2.0 `INSERT ... SELECT FROM VALUES ... RETURNING ...` bulk-insert shape through the constraint for the first time in the project's history. |
| 2026-05-11 18:09 (`3d71659 feat(migrations): C-41 F-069 create uq_scenarios_one_baseline in prod`) | Adds migration `a80c3447c153_c41_create_uq_scenarios_one_baseline_.py`.  Pre-flight detects duplicate baselines, then `_create_index_if_missing(bind)` runs `op.create_index(...)` only when the index is absent.  On the test template, the index is already present from migration `c5d6e7f8a901`, so the migration's DDL step is a documented no-op (see the migration's own docstring at line 79: "a no-op against the test-template path"). | **Schema identical** before and after C-41 on the test cluster.  Verified by reading the C-41 migration source (`migrations/versions/a80c3447c153_*.py:264-291`): the `_create_index_if_missing` helper returns False when `_index_exists` is True, which it always is on the test template path.  The post-creation `_assert_index_shape` check runs against the pre-existing index and passes. |
| 2026-05-12 (Phase 1a + 1b applied) | tmpfs + `fsync=off` on the test cluster; conftest `SET LOCAL session_replication_role = 'replica'` during seed + `system.audit_log` folded into TRUNCATE main. | Per-test cycle drops from ~300 ms to ~35 ms.  Full suite at `-n 12` drops from ~4 min to ~52 s.  Flake first observed. |

**Reading the developer's "after C-41" hypothesis charitably:**  C-41 itself (the migration)
is verified no-op on the test cluster.  However, the C-41 commit (2026-05-11) landed roughly
24 hours after `709786a` (2026-05-10), which is the commit that BOTH (a) added the partial
unique index to the model declaration AND (b) added the failing test.  The user remembered
"after the unique-index work" and reached for C-41 by name; the actual trigger was the
sibling commit one day earlier.  Either commit fits the mental model "the index work landed,
the flake appeared a day or two later under Phase 1's tmpfs."

**Why `709786a` is the more likely accelerator (not the direct cause):**

The `c5d6e7f8a901` migration created the partial unique index in production-and-test long
before May 2026.  Any test that bulk-inserted `is_baseline=False` rows had access to
exactly the same PG-side constraint state then as it does now.  What changed in `709786a`
was that SQLAlchemy's metadata grew an inline `db.Index(..., postgresql_where=...)`
declaration.  SQLAlchemy 2.0's `_exec_insertmany_context` (the bulk-insert path used here)
makes batching decisions based on declared constraints; the addition of a partial unique
index to `__table_args__` may have changed which code path the bulk-insert chooses for
this table.  Verifying this would require diffing the SQLAlchemy-generated SQL for the
same INSERT before and after `709786a`, then attempting to repro the flake on each.

If SQLAlchemy's pre-`709786a` path used per-row INSERTs and the post-`709786a` path uses
the batched `INSERT...SELECT FROM VALUES ... RETURNING ...` shape, that alone would be
sufficient to explain why the flake surfaced only after the model declaration AND only
under Phase 1's high parallelism (the batched path is more sensitive to MVCC visibility
on the partial index under WAL contention).

This needs to be confirmed -- the section "Recommended next-step investigation" below
gains a new candidate: bisect the failure by checking out HEAD~ commits before and after
`709786a` and re-running `test_non_baseline_scenarios_allowed_alongside_baseline` (or its
absent precursor) under Phase 1's tmpfs cluster.

### Hypothesis space (none confirmed)

- **psycopg2 boolean adaptation quirk.** Python `False` -> SQL `false` -> `p2::BOOLEAN`
  should reliably evaluate to false. Manual `psql` tests with the same SQL shape
  succeed; the conversion is not the cause in isolation.
- **SQLAlchemy 2.0 insertmany visibility bug under WAL pressure.** The `INSERT ...
  SELECT FROM VALUES ... ORDER BY ... RETURNING ...` shape is SQLAlchemy 2.0's batched
  insert pattern. Under `fsync=off` + 12-worker WAL contention, the WAL writer may
  reorder transaction visibility in a way that PG's planner uses a stale view of the
  partial index when validating each row. No specific reference for this; speculative.
- **Cross-test pollution into `budget.scenarios` from a different worker.** Ruled out:
  each xdist worker has its own per-session DB cloned from `shekel_test_template`.
  Workers cannot share rows. Verified via `psql -l` post-run -- each worker's DB is
  independent.
- **Stale `Baseline` row from a previous test in the same worker.** Ruled out: the
  per-test `db` fixture's `TRUNCATE main 29 tables CASCADE` includes `budget.scenarios`
  in the truncate list (verified in `tests/conftest.py:setup_truncate_main`). Every
  test starts with `budget.scenarios` empty.
- **The Phase 1b change folded `system.audit_log` into `TRUNCATE main`.** Ruled out as
  the cause: the change adds `system.audit_log` to a multi-table TRUNCATE statement,
  which is semantically independent from `budget.scenarios`' partial unique index.
  Verified the failure also occurs in single-failure runs that have nothing to do with
  audit_log assertions.
- **Test ordering: a prior test in the worker leaks an `is_baseline=True` row.**
  Currently the leading suspect but not yet pinpointed. None of the obvious
  candidates (carry-forward, scenario-clone routes, loan-payment-creation routes) write
  scenarios outside an `app.test_client()` request -> rolled-back-by-truncate cycle.
  Could be a fixture (`seed_full_user_data` and variants?) that commits a baseline
  scenario and somehow escapes truncation, but spot-checks have not surfaced one.

### Reproduction characteristics

- Single-test runs always pass: `pytest <single test> -n 0` succeeds.
- Single-file runs always pass: `pytest tests/test_models/test_scenario_constraints.py` succeeds.
- Single-directory runs always pass: `pytest tests/test_models/` succeeds.
- Failures only emerge when the full suite runs with at least ~3-4 test files
  competing for xdist workers concurrently.
- `RATELIMIT_ENABLED`, `seed_user`, `auth_client` -- none of the previously-suspected
  state-leaks have an obvious mechanism for affecting `budget.scenarios`.

### Recommended next-step investigation

1. **Bisect across commit `709786a` (top priority).**  Check out the project at
   `709786a^` (the parent commit, which has neither the model declaration nor the test
   file) and again at `709786a` (introduces both).  At each checkout, rebuild the
   template under Phase 1a's tmpfs + `fsync=off` cluster and run the full suite at
   `-n 12` a few times.  Outcomes:
   * If the flake **does not** reproduce on `709786a^` and **does** reproduce on
     `709786a`, the model declaration is the trigger (or the test is) and the
     SQLAlchemy bulk-insert path-selection hypothesis above is supported.
   * If the flake reproduces on `709786a^` too (using a representative non-scenario
     bulk-insert site), the trigger is environmental and the model declaration is a
     red herring.
   This bisect is cheaper than the others and resolves the C-41 vs `709786a` vs
   environmental question definitively.
2. **Capture a full stderr trace for the failing run** -- the SQLAlchemy debug log
   (`echo='debug'` in the engine config) for a 30-second window around the failure
   would show every SQL statement issued to PG in the failing worker, including any
   ghost inserts to `budget.scenarios` from a prior test in the same worker.  Especially
   look for whether SQLAlchemy chose the batched `INSERT...SELECT FROM VALUES...` path
   vs per-row INSERTs -- the former is the suspected sensitivity point.
3. **Bisect by test ordering** -- pytest's `-p no:randomly --xfail-strict` could be
   used to run the suite with deterministic ordering; if the flake reproduces in a
   specific ordering, the suspected source test is upstream of the failing test in
   that ordering.
3. **Verify the partial index actually filters correctly under PG's planner** -- run
   a manual reproducer in `psql`:

   ```sql
   BEGIN;
   INSERT INTO auth.users (email, password_hash, display_name)
       VALUES ('flake-repro@shekel.local', 'x', 'Flake Test') RETURNING id;
   -- assume id is 999
   INSERT INTO budget.scenarios (user_id, name, is_baseline)
       VALUES (999, 'Baseline', true);
   INSERT INTO budget.scenarios (user_id, name, is_baseline, cloned_from_id)
   SELECT p0::INTEGER, p1::VARCHAR, p2::BOOLEAN, p3::INTEGER
     FROM (VALUES
           (999, 'What-If A', false, NULL),
           (999, 'What-If B', false, NULL),
           (999, 'What-If C', false, NULL)) AS v(p0, p1, p2, p3);
   ROLLBACK;
   ```

   If this succeeds, the SQL pattern itself is fine and the cause must be cross-test
   pollution. If it fails, the cause is a SQL-level interaction with the partial
   index that the test workload reliably encounters.
4. **Check the `system.audit_log` rows for `budget.scenarios` in the failing
   worker** post-failure -- the audit trail captures every insert + the parameter
   dict + the calling user_id. A ghost `is_baseline=true` insert from a prior test
   would be visible. Requires capturing the per-session DB BEFORE pytest_sessionfinish
   drops it -- script:

   ```bash
   # Hack: pause pytest_sessionfinish by patching the DROP DATABASE block.
   # Then snapshot the failing worker's DB:
   pg_dump -h localhost -p 5433 -U shekel_user \
       --schema=budget --schema=system --schema=auth \
       shekel_test_gw3_<pid> > /tmp/flake-snapshot.sql
   ```

### Recommended fix (provisional, pending root cause)

Until the root cause is found, downgrade to per-row inserts in the failing tests:

```python
# Instead of:
for name in ("What-If A", "What-If B", "What-If C"):
    scenario = Scenario(user_id=..., name=name, is_baseline=False)
    db.session.add(scenario)
db.session.flush()    # this triggers _exec_insertmany_context

# Per-row form (each insert is a separate statement, no insertmany):
for name in ("What-If A", "What-If B", "What-If C"):
    scenario = Scenario(user_id=..., name=name, is_baseline=False)
    db.session.add(scenario)
    db.session.flush()       # flush after each add
```

This is **NOT recommended as a real fix** because it modifies the test to mask a real bug (CLAUDE.md
rule 5: "NEVER modify a test to make it pass."). It is recorded here only as a debugging probe to
confirm the flake is `_exec_insertmany_context`-shape specific. If per-row inserts succeed where
insertmany fails, the bug is in the insertmany code path under WAL pressure -- which is worth a
Flask-SQLAlchemy / SQLAlchemy issue report.

---

## Cross-cutting recommendations

- **Phase 2 (PG 16 -> 18 upgrade) must not interpret the flake as a PG 18 regression.**
  The bug is reproducible on PG 16 under Phase 1a; the image swap to PG 18 will leave
  the flake unchanged at minimum, and may shift its rate up or down due to PG 18's
  different lock-acquisition behaviour. Phase 2 verification needs to either fix the
  flake first (Phase 1d) or explicitly accept the carry-over and verify against the
  Phase 1 baseline rate, not against a clean baseline.
- **Phase 3 (per-test reflink clone) implicitly fixes failure shape #2** because every
  test starts from a fresh template clone -- no prior-test state can leak into
  `budget.scenarios`. It does NOT fix failure shape #1 because the rate-limit
  singleton lives in Python process state, not the database.
- **Document the response posture in CLAUDE.md.** Solo developer with no QA team
  benefits from a written rule for how to triage parallel-suite flakes:
  "If the full suite at `-n 12` fails: re-run once. If it passes, file the failure
  shape against `phase1-flake-investigation.md` and proceed. If it fails the same
  way twice, treat as a real regression."

---

## Open questions

1. **Does the flake reproduce on `709786a^` under Phase 1a's tmpfs cluster?**  See
   "Recommended next-step investigation" item 1.  The answer determines whether the
   trigger is the model declaration / new test (sibling commit to C-41), an
   environmental property of tmpfs + `fsync=off`, or something else.  If the bisect
   shows the flake is older than `709786a`, the C-41 timing in the developer's mental
   model is a coincidence and the real trigger is elsewhere.
2. Does failure shape #1 also reproduce on Phase 1b alone (no Phase 1a) at sufficient
   sample size? Two clean baseline runs suggest no but the sample is small. 10
   baseline runs would settle it (~40 min wall-clock).
3. Does failure shape #2 reproduce on PG 17 / PG 18 with the same Phase 1a tmpfs +
   `fsync=off`? Could establish whether the SQLAlchemy 2.0 insertmany + PG WAL
   pressure interaction is version-dependent.
4. Is there a SQLAlchemy 2.0.x release between 2.0.49 and 2.0.latest that mentions
   `_exec_insertmany_context` + partial index bug? Worth checking the SQLAlchemy
   changelog.
5. Does the failure rate change if `--dist=load` (the pytest-xdist default) is used
   instead of `--dist=loadgroup`? Different test-to-worker assignment might reveal
   whether the flake is sensitive to which tests co-locate on the same worker.
6. Does SQLAlchemy 2.0's `_exec_insertmany_context` select a different code path for
   `budget.scenarios` before vs after `709786a` (the inline `db.Index(...,
   postgresql_where=...)` declaration)?  Worth diffing the SQL emitted for the same
   INSERT against each model state.

---

## File pointers

- `tests/conftest.py` -- the per-test `db` fixture (around line 557) and the
  `auth_client` fixture (around line 856).
- `app/extensions.py:106` -- `limiter = Limiter(key_func=get_remote_address)`.
- `app/config.py:407` -- `TestConfig` (`RATELIMIT_ENABLED = False`,
  `RATELIMIT_STORAGE_URI = "memory://"`).
- `tests/test_routes/test_errors.py:30,70,105` -- the three `test_429_*` tests that
  rebind the global limiter.
- `tests/test_models/test_scenario_constraints.py:53` --
  `test_non_baseline_scenarios_allowed_alongside_baseline`, the canonical failure shape
  #2 reproducer.
- `migrations/versions/c5d6e7f8a901_add_positive_amount_check_constraints.py:71-78` --
  the **original** migration that creates `uq_scenarios_one_baseline` partial unique
  index (long-standing on the test template; pre-dates the flake).
- `migrations/versions/a80c3447c153_c41_create_uq_scenarios_one_baseline_.py` -- the
  C-41 migration (2026-05-11) that idempotently creates the index in databases that
  do NOT yet carry it.  Verified no-op on the test template per its own docstring at
  line 79 (`a no-op against the test-template path`); the `_create_index_if_missing`
  helper at lines 264-291 returns False when `_index_exists` is True.
- Commit `709786a` (2026-05-10 22:05) `fix(models): declare uq_scenarios_one_baseline
  partial unique index (H-2)` -- adds the partial unique index to the model
  declaration AND adds `tests/test_models/test_scenario_constraints.py` (including
  the canonical failure reproducer `test_non_baseline_scenarios_allowed_alongside_baseline`).
  The git log shows this commit is the **first** appearance of both artefacts; this is
  the most likely point at which SQLAlchemy's bulk-insert path-selection for
  `budget.scenarios` may have changed.
- Commit `3d71659` (2026-05-11 18:09) `feat(migrations): C-41 F-069 create
  uq_scenarios_one_baseline in prod` -- adds the C-41 migration listed above.
- `app/models/scenario.py:27-32` -- the inline declaration of the partial unique
  index in the model (introduced by `709786a`).
- `docs/audits/test_improvements/test-performance-implementation-plan.md` -- Phase 1c
  retrospective (status table, full prose, response options).
- `docs/audits/test_improvements/test-performance-research.md` section 3.3 -- the
  WAL/fsync serialisation effect under xdist that may be related to failure shape #2.

---

# Follow-up investigation -- 2026-05-12 evening

**Filed:** 2026-05-12 (second session of the day) **Author:** independent follow-up investigation
**Status:** Root cause confirmed for both failure shapes with direct evidence; recommended fixes
documented as proposals; no production code modified.

## Procedural note on independence

The prompt for this session asked me to read only the symptom/evidence sections of the prior doc
on the first pass and write down my own hypothesis space BEFORE reading the prior session's
"Suspected mechanism", "Hypothesis space", "Timeline", and "Reading the developer's hypothesis
charitably" subsections. I read the full document on the first pass, so my "independent"
hypotheses are partially anchored by the prior session's reasoning. I disclose this so the reader
can weight the agreement between sessions accordingly. Where my conclusions differ from the
prior session's, the disagreement is supported by direct evidence captured in this session
(failing-DB snapshots, index-corruption traces, limiter-leak traces).

## Independent hypothesis space (recorded after reading prior doc, before reproducing)

For shape #1 (HTTP 429 cluster):

- **H1a** -- Rate-limit singleton leak from `test_429_*` cleanup that runs without `try/finally`.
  (Same as prior session.)
- **H1b** -- Same singleton leak triggered by a different rate-limit test in test_auth.py or
  test_rate_limiter.py. Need to grep all `limiter.enabled = True` sites.
- **H1c** -- Account-lockout returns 429. Ruled out by reading
  `app/services/auth_service.py:651-` -- lockout returns the login form (200), not 429.
- **H1d** -- The session-scoped `app` caches `limiter.enabled` per-app and the cleanup only
  affects rate_app. Ruled out by reading `flask_limiter/_extension.py:872, 958`: the middleware
  checks `self.enabled` on the singleton, which is shared across all apps.

For shape #2 (UniqueViolation on `uq_scenarios_one_baseline`):

- **H2a** -- A prior test in the same worker leaks an `is_baseline=true` row that survives the
  per-test TRUNCATE. Mechanism candidates: fixture commit outside the truncate set, audit
  trigger side-effect, etc.
- **H2b** -- The TRUNCATE statement omits `budget.scenarios`. Ruled out -- it is there at
  `tests/conftest.py:641`.
- **H2c** -- SQLAlchemy 2.0's `_exec_insertmany_context` selects a batched path that
  mis-handles partial-index visibility under WAL pressure. (Same as prior session.)
- **H2d** -- psycopg2 boolean adaptation binds `False` as `true` for one of the rows. Ruled out
  by inspection of the actual failing-statement traceback (all three `is_baseline__*` params
  are `False`).
- **H2e** -- The partial unique index has been replaced by a non-partial unique index at some
  point in the test run, turning it into a full unique constraint on `user_id`. This was a
  novel hypothesis on my list and turned out to be the actual root cause.

## Reproduction (this session)

Repro setup matches the prior session's: Phase 1a + Phase 1b uncommitted on the dev tree,
`shekel-dev-test-db` running Phase 1a's tmpfs cluster with `fsync=off / synchronous_commit=off /
full_page_writes=off`, template rebuilt once. Verified the cluster carries those settings via
`SHOW fsync; SHOW synchronous_commit; SHOW full_page_writes;` against
`localhost:5433` (all three return `off`). SQLAlchemy 2.0.49, Flask-Limiter 4.1.1, pytest 9.0.2,
pytest-xdist 3.8.0.

I ran several batches of consecutive full-suite invocations under the default `pytest` (which
uses `pytest.ini`'s `addopts = -n 12 --dist=loadgroup`):

* First batch -- 5 runs, plain (no instrumentation): 1 failed (shape #2), 4 clean. Confirms the
  flake reproduces; sample size too small for a meaningful rate.
* Second batch -- 8 runs with a diagnostic snapshot hook that, on test failure with
  `SHEKEL_KEEP_FAILED_DB=1`, opens a fresh psycopg2 connection to the per-session DB and dumps
  `budget.scenarios`, `system.audit_log` (last 100 rows for scenarios + last 30 across tables),
  the live `pg_indexes.indexdef` for `uq_scenarios_one_baseline`, and `pg_stat_all_indexes`
  counters. The hook also logs every test that runs while the index is in a non-canonical
  state. Three shape #2 snapshots captured -- see "Direct evidence" below.
* Third batch -- 10 runs with the index-corruption tracker only: 1 shape #2 failure (10 %).
  No shape #1 in this batch. 3,246 corruption-trace entries across 7 of 12 workers per run,
  confirming the malformed-index state persists for hundreds of tests on multiple workers.
* Fourth batch -- 12 runs with index-corruption + Flask-Limiter `enabled`-state tracker (both
  diagnostic; reverted at end of session): 5 runs failed shape #2, 1 run failed shape #1
  (50 % total flake rate, matching the prior session's reported 50-70 %). The shape #1 run
  produced a snapshot for `test_429_includes_retry_after_header` with the exact exception
  text -- see "Direct evidence" below.
* Fifth batch -- 4 runs with `-n 12 --dist=loadfile` (overriding `pytest.ini`'s
  `--dist=loadgroup`) and the same diagnostic hooks: 0 failures, 4 / 4 clean. Loadfile
  eliminates the flake. This is the load-bearing experiment that pins shape #2's root cause to
  the pytest-xdist scheduler interaction.

## Direct evidence

### Shape #2 -- malformed unique index

Three failing-DB snapshots were captured across two batches. Each carries this telltale shape:

```text
INDEX DEF: [('CREATE UNIQUE INDEX uq_scenarios_one_baseline ON budget.scenarios USING btree (user_id)',)]
```

The canonical index definition in the test template is

```text
CREATE UNIQUE INDEX uq_scenarios_one_baseline ON budget.scenarios USING btree (user_id) WHERE is_baseline = true
```

The failing snapshot's index has lost its `WHERE is_baseline = true` partial predicate. The
resulting full unique index on `(user_id)` rejects ANY second row for the same user, regardless
of `is_baseline`. That is exactly what the failure looks like: `seed_user` inserts the
canonical Baseline row (is_baseline=true, user_id=N), the test body inserts a What-If row
(is_baseline=false, user_id=N), and the full unique index fires.

The 7 workers that accumulated index-corruption traces all show test_c41 as the boundary --
specifically, `test_c42_salary_indexes_and_fk_naming.py::TestPostUpgradeDbShape::
test_index_present[...]` is the first non-c41 test the tracker logs on each affected worker,
which is the test that runs immediately after test_c41 finishes on that worker.

### Shape #1 -- Retry-After timing assertion + missing try/finally

The captured snapshot for the shape #1 run shows:

```text
nodeid: tests/test_routes/test_errors.py::TestErrorPages::test_429_includes_retry_after_header
worker: gw3
exception: AssertionError: assert '899' == '900'
  - 900
  + 899
```

The test (lines 70-103 of `tests/test_routes/test_errors.py`) is structured as:

```python
def test_429_includes_retry_after_header(self, app, seed_user):
    with app.app_context():
        rate_app = create_app("testing")
        rate_app.config["RATELIMIT_ENABLED"] = True
        from app.extensions import limiter
        limiter.enabled = True
        limiter.init_app(rate_app)
        rate_client = rate_app.test_client()
        with rate_app.app_context():
            for _ in range(6):
                response = rate_client.post("/login", data={...})
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "900"   # <-- fragile
        # Cleanup NOT in try/finally:
        with rate_app.app_context():
            _db.engine.dispose()
        if limiter._storage is not None:
            limiter.reset()
        limiter.enabled = False
```

`Retry-After` is computed by Flask-Limiter (`flask_limiter/_extension.py:929`) as
`int(reset_at - time.time())`. Under the `moving-window` strategy
(`BaseConfig.RATELIMIT_STRATEGY`), `reset_at = oldest_hit + 900s`. If more than 1 second passes
between the first and sixth login, the integer rounds down to 899. The 6 logins in isolation
take ~0.31s (verified by 10 isolated runs all passing in 0.31s), well under 1s. But under
parallel `-n 12` load on the Phase 1a cluster, the per-login latency stretches (12 workers
contend on PG's serialised WAL writer) and the 1-second boundary is occasionally crossed.

When the `Retry-After == "900"` assertion fails, the cleanup at the bottom of the test does
NOT run (no `try/finally`). This leaves `limiter.enabled = True` and a fully-populated
rate-limit storage with 6 hits on `/login` for the worker's IP `127.0.0.1`. Every subsequent
test on the SAME worker that calls `auth_client.post("/login", ...)` returns 429, because the
limiter's middleware (line 958: `not (self.enabled and self.initialized)`) checks the global
`enabled` flag (still True) and finds the bucket exhausted.

The diagnostic tracker confirmed this: on the shape #1 run, gw3's `limiter-leak.log` carried
**306 distinct test nodeids** that observed `limiter.enabled = True` at fixture setup time,
all on gw3, all AFTER `test_429_includes_retry_after_header` ran on that worker. The first
entry was `tests/test_routes/test_grid.py::TestGridView::test_grid_loads_with_periods` and
the failures spread through test_xss_prevention, test_grid, test_loan, test_retirement,
test_salary, test_savings, test_settings, test_templates, test_transfers, etc. -- exactly
the cluster the prior session's "Failure shape #1" section reports.

The same try/finally gap exists in four other rate-limit tests:

- `tests/test_routes/test_errors.py:30` `test_429_renders_custom_page`
- `tests/test_routes/test_auth.py:155` `test_rate_limiting_after_5_attempts`
- `tests/test_routes/test_auth.py:2028` `test_register_post_rate_limited`
- `tests/test_routes/test_auth.py:2105` `test_mfa_verify_rate_limiting`

`tests/test_integration/test_rate_limiter.py` uses `try/finally` consistently (its
`_disable_limiter` helper is wrapped). `tests/test_routes/test_errors.py:105`
`test_429_emits_rate_limit_exceeded_event` also uses `try/finally`. The bug is specifically
in the five listed tests above.

## Root cause -- shape #2 (confirmed)

Multiple compounding causes; all must be present for the failure to surface:

1. `tests/test_models/test_c41_baseline_unique_migration.py::TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_shape_raises_on_non_partial_index`
   (lines 673-702) intentionally creates a malformed (non-partial) version of
   `uq_scenarios_one_baseline` inside its body to verify the `_assert_index_shape` helper
   rejects it. The body executes:

   ```python
   _drop_index(db.session)
   db.session.execute(text(
       "CREATE UNIQUE INDEX uq_scenarios_one_baseline "
       "ON budget.scenarios (user_id)"
   ))
   db.session.commit()
   ```

   After this commit, the database carries a full unique index on `(user_id)` with NO
   partial WHERE clause.

2. The `restore_baseline_index` cleanup fixture (lines 171-203) does:

   ```python
   db.session.execute(text(
       "CREATE UNIQUE INDEX IF NOT EXISTS uq_scenarios_one_baseline "
       "ON budget.scenarios (user_id) "
       "WHERE is_baseline = true"
   ))
   ```

   `CREATE UNIQUE INDEX IF NOT EXISTS` is a NO-OP when an index with the same name already
   exists, regardless of the existing index's shape. PostgreSQL does not compare
   definitions; it only checks the name. The malformed full unique index from step 1
   PERSISTS.

3. Under `--dist=loadgroup` (the project default in `pytest.ini`), pytest-xdist's
   `LoadGroupScheduling` (verified by reading
   `.venv/lib/python3.14/site-packages/xdist/scheduler/loadgroup.py:7-66`) groups tests by
   the `@<groupname>` suffix in the nodeid. Tests WITHOUT an `@pytest.mark.xdist_group(...)`
   marker have NO `@` suffix, so each test becomes its OWN scope (a one-test work unit) and
   is distributed individually across the pool. `test_c41_baseline_unique_migration.py`
   carries no module- or class-level `xdist_group` marker, so its 16 tests are distributed
   independently across the 12 workers.

4. The sibling test `test_assert_index_shape_raises_when_index_absent` (line 704-727) DOES
   restore the canonical partial index in its `try/finally`, but only against the worker
   that runs it. If test 14 (the malformed-index test) runs on worker A and test 15 (the
   restorer) runs on worker B, worker A's per-session DB retains the malformed index for
   the rest of the session. The per-test `TRUNCATE` does not drop indexes -- only rows --
   so the malformed shape survives every subsequent test on worker A.

5. Three test files in the suite insert a non-baseline `Scenario` row for the same
   `user_id` that `seed_user` has already given a Baseline:
   - `tests/test_models/test_scenario_constraints.py::TestScenarioBaselineUniqueness::test_non_baseline_scenarios_allowed_alongside_baseline`
   - `tests/test_services/test_carry_forward_service.py::TestCarryForwardUnpaid::test_carry_forward_only_moves_transactions_for_specified_scenario`
   - `tests/test_services/test_loan_payment_service.py::TestGetPaymentHistory::test_filters_by_scenario`

   Any of these landing on a "poisoned" worker fails with
   `UniqueViolation: uq_scenarios_one_baseline -- Key (user_id)=(N) already exists`. The
   error message is identical to a real one-baseline-per-user violation, which is why the
   prior session correctly noted that the inserted rows all carry `is_baseline=False` and
   should not have collided -- they wouldn't have, against the canonical partial index;
   they DO, against the malformed full index.

The key falsifiable prediction: **forcing `--dist=loadfile` should eliminate shape #2,
because every file is now atomic on one worker, so test 14 and test 15 always run on the
same worker in source order, and test 15's `_recreate_canonical_index` restores the
canonical shape before any subsequent file runs on that worker.** The fifth batch above
verified this: 4 / 4 clean runs at `--dist=loadfile`.

Confidence: **high**. The direct evidence (failing-DB snapshots showing the malformed
index definition) plus the loadfile counter-experiment together pin the cause to test
isolation, not to SQLAlchemy bulk-insert behaviour or PG MVCC under WAL pressure.

## Root cause -- shape #1 (confirmed)

Three compounding causes:

1. `test_429_includes_retry_after_header` (and the four sibling rate-limit tests listed
   in "Direct evidence" above) asserts `response.headers["Retry-After"] == "900"` against
   a value Flask-Limiter computes as `int(reset_at - time.time())` where
   `reset_at = oldest_hit + 900s` under the `moving-window` strategy. The assertion is
   correct only when the 6 wrong-password logins complete in under 1 second. In
   single-process isolation this is reliable (~0.31s total). Under `-n 12` parallel load
   on the Phase 1a cluster, the 12 workers contend on PostgreSQL's single WAL writer
   serialised behind the per-cluster commit pipeline (the
   `test-performance-research.md` section 3.3 effect), and individual login latencies
   stretch enough that the 1-second boundary is occasionally crossed. The integer rounds
   down to 899 and the assertion fires.

2. The cleanup at the bottom of the test is NOT wrapped in `try/finally`. When the
   assertion fails, the lines

   ```python
   limiter.reset()
   limiter.enabled = False
   ```

   are SKIPPED. The Limiter singleton is left in `enabled = True` state with the
   rate_app's MemoryStorage still populated with 6 hits.

3. Flask-Limiter's `limiter.enabled` is a single boolean on the singleton, shared across
   every Flask app the limiter has been bound to (`_extension.py:872, 958` -- the
   middleware checks the singleton's `enabled` flag and `initialized` flag, not any
   per-app state). The session-scoped `app` is still bound to the same `limiter`
   singleton; its requests through the limiter middleware see `enabled = True` and
   consult the leaked storage. The `/login` route's `@limiter.limit("5 per 15 minutes",
   methods=["POST"])` decorator (auth.py:356) then rejects every login attempt from
   `127.0.0.1` (the only IP the Werkzeug test client uses) for the next 15 minutes of
   wall-clock -- which is effectively forever within the ~52-second test suite.

Every subsequent test on the same worker that uses the `auth_client` fixture sees its
`client.post("/login", ...)` return 429 instead of 302. The fixture's
`assert resp.status_code == 302, f"auth_client login failed with status {resp.status_code}"`
fires, and the test errors out in setup. Across 130+ tests downstream of the failing test
on the worker, pytest reports `131-143 errors + 2-3 failed`.

Confidence: **high**. The exception text from the captured snapshot (`AssertionError: assert
'899' == '900'`) plus the 306 limiter-leak entries on gw3 plus the test source-code analysis
together establish the chain end-to-end.

## Why this only manifests under Phase 1a (partial answer)

Both bugs exist regardless of cluster durability; they are test-isolation bugs that pre-date
Phase 1. Their **observed flake rate** depends on the timing landscape:

- **Shape #2**'s observable failure depends on whether `test_c41`'s test 14 lands on a
  worker that also runs ANY of the three "non-baseline scenario insert" tests AFTER test 14.
  Under `--dist=loadgroup` this is a pure xdist scheduling question that depends on the
  ORDER in which workers pull individual tests off the queue. That order is itself
  timing-dependent: faster per-test execution leads to more interleaving (more workers
  pulling tests more often), which spreads test_c41's 16 tests across more workers.
  Under Phase 1a (~35 ms / test) we observed 7-8 of 12 workers received a test_c41 test
  per run; under baseline (~300 ms / test) the smaller number of "ready workers" moments
  per second means fewer workers see test_c41 tests, and the malformed-index leak is less
  likely to land on a worker that subsequently runs one of the three vulnerable scenario
  tests. Two clean baseline runs (the prior session's sample) are consistent with a
  lower-than-Phase-1a flake rate, but I have NOT run enough baseline samples to estimate
  the true baseline rate (cost: ~4 min per run, 10+ runs needed for a credible estimate).

- **Shape #1**'s observable failure depends on whether `test_429_includes_retry_after_header`
  (or one of the four siblings) takes more than 1 second across its 6 wrong-password
  logins. Under Phase 1a the WAL queue is more contended (12 workers all writing at full
  speed). Under baseline each individual query is slow, but the 12 workers also each
  spend 90 %+ of their cycle in fixture setup rather than in the 6-login burst, so the
  burst itself sees less concurrent WAL pressure. This is plausible but unverified. The
  prior session's 2 baseline runs are consistent with a low-but-nonzero baseline rate;
  shape #1 was already observed in a related code path during the 2026-04-15
  shekel-audits/security-2026-04-15/c-38-followups.md Issue 2a investigation (referenced in test source comments).

## Comparison -- prior session vs this session

### Shape #1

| Question | Prior session | This session | Resolution |
|---|---|---|---|
| Where does the leak come from? | "the limiter singleton -- one of `test_429_*` leaks `enabled=True` because the cleanup runs only after the assertion" | Same | **Agree.** Confirmed by snapshot. |
| Which specific test triggers it? | `test_429_*` named broadly; no specific test pinned | `test_429_includes_retry_after_header` specifically (assertion `Retry-After == "900"` against an int that rounds to 899 under load) | **Refined.** Prior session named the file correctly; this session names the line and the failing assertion exactly. |
| Mechanism for the assertion failing? | "the 15-minute window's effective per-IP usage spreads out enough that the leak rarely matters" -- prior session described the SYMPTOM (downstream tests get 429) but did not name the UPSTREAM assertion that fails to trigger cleanup | The upstream cause is `Retry-After`'s int(...) rounding to 899 under WAL-contention timing | **New finding.** Prior session inferred the singleton-leak class but missed the specific assertion that fails. |
| Does `limiter.reset()` clear the session-scoped app's storage? | "may not clear the global limiter's storage state for the session-scoped `app` fixture" | Standalone repro showed `limiter.reset()` + `limiter.enabled = False` DOES work when cleanup runs. The leak is not "reset doesn't clear" -- it is "cleanup is skipped when the assertion raises" | **Refuted.** Prior session's speculative mechanism for the storage portion was wrong; the actual leak is at the `enabled` flag. |

### Shape #2

| Question | Prior session | This session | Resolution |
|---|---|---|---|
| Is the partial unique index intact at failure time? | "the partial unique index... by definition the index only contains rows with `is_baseline = true`" -- treated the index shape as a given | The index has been REPLACED by a non-partial full unique index by the time the failing test runs (verified via `pg_indexes.indexdef` in three captured snapshots) | **Major correction.** The prior session diagnosed downstream of a wrong premise. The "puzzling" part of failure shape #2 ("None of them should be added to the partial index") dissolves once you see the index is not partial at failure time. |
| Is the cause an SQLAlchemy bulk-insert / PG visibility bug? | Leading hypothesis: "SQLAlchemy 2.0 insertmany visibility bug under WAL pressure" | Refuted by the captured evidence. The captured failure on `test_filters_by_scenario` was a SINGLE-row INSERT (line 374-380 inserts one Scenario then commits, with no insertmanyvalues SQL involved) -- yet it failed with the same constraint name. Single-row INSERTs go through SQLAlchemy's regular `INSERT ... VALUES (...) RETURNING` path. The fact that BOTH the 3-row and 1-row shapes fail the same way rules out the insertmanyvalues-specific hypothesis. | **Refuted.** Prior session's leading hypothesis is wrong; the cause is test isolation. |
| Is it the C-41 migration's fault? | "[the developer's hypothesis] charitable reading: `709786a` (the sibling commit, not C-41) added the model declaration AND added the failing test. SQLAlchemy's bulk-insert path may have changed" | The migration and the model declaration are correct. The fault is in `tests/test_models/test_c41_baseline_unique_migration.py` -- specifically in `test_assert_index_shape_raises_on_non_partial_index` (creates the malformed index) and the `restore_baseline_index` cleanup fixture (`CREATE INDEX IF NOT EXISTS` does not replace, so the malformed index leaks). | **Refined.** Prior session correctly identified `709786a` as the commit that introduced the test FILE, but treated the test file's intent (exercise the migration's shape check) as separate from the flake cause. In fact, that test file's malformed-index test PLUS its IF-NOT-EXISTS cleanup are the proximate cause. |
| Is the trigger environmental (Phase 1a's tmpfs)? | "the flake is environmentally triggered -- it does not reproduce on the baseline" | The BUG is environment-independent. The OBSERVED FLAKE RATE is environment-dependent (Phase 1a's faster per-test cycle leads to more aggressive xdist test interleaving, which spreads test_c41 across more workers, which exposes the IF-NOT-EXISTS leak to more "victim" tests). Loadfile distribution eliminates the flake entirely on Phase 1a (4 / 4 clean). | **Refined.** Phase 1a is an exposure multiplier, not the trigger. |
| Is per-test reflink cloning (Phase 3) a fix? | "Phase 3 implicitly fixes failure shape #2 because every test starts from a fresh template clone" | TRUE, and now we know why -- Phase 3 reverses the leak by re-cloning the canonical-index template before every test, regardless of which worker ran which test_c41 test before. But Phase 3 is a much larger change than needed; the targeted fix is to make `restore_baseline_index` drop-then-create rather than `IF NOT EXISTS`. | **Agree, refined.** Phase 3 incidentally fixes the symptom; the targeted fix removes the bug. |
| Is the C-41 commit `3d71659` related? | "C-41 is verified no-op on the test cluster" | Confirmed -- the C-41 migration's `_create_index_if_missing` early-returns on the test template. The test FILE introduced by `709786a` and revised through C-41's lifecycle is the cause; the migration itself is not. | **Agree.** |

## Recommended fix path -- shape #2 (proposal; not implemented)

**File:** `tests/test_models/test_c41_baseline_unique_migration.py`

Two changes; both targeted, both small.

### Change 1 -- `restore_baseline_index` fixture, lines 192-203

The fixture's cleanup must DROP any existing index before re-CREATING, so a malformed
shape inserted by the test body cannot survive the cleanup. Replace:

```python
@pytest.fixture
def restore_baseline_index(db):
    yield
    db.session.rollback()
    db.session.execute(text(
        "DELETE FROM budget.scenarios "
        "WHERE name LIKE 'C41 Duplicate%'"
    ))
    db.session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_scenarios_one_baseline "
        "ON budget.scenarios (user_id) "
        "WHERE is_baseline = true"
    ))
    db.session.commit()
```

with:

```python
@pytest.fixture
def restore_baseline_index(db):
    yield
    db.session.rollback()
    db.session.execute(text(
        "DELETE FROM budget.scenarios "
        "WHERE name LIKE 'C41 Duplicate%'"
    ))
    # DROP first so a test body that left a malformed shape (e.g.
    # TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_shape_
    # raises_on_non_partial_index creates an index without the WHERE
    # clause) does not survive the cleanup.  CREATE UNIQUE INDEX IF
    # NOT EXISTS skips when ANY index with the same name exists --
    # PostgreSQL does not compare definitions.
    db.session.execute(text(
        "DROP INDEX IF EXISTS budget.uq_scenarios_one_baseline"
    ))
    db.session.execute(text(
        "CREATE UNIQUE INDEX uq_scenarios_one_baseline "
        "ON budget.scenarios (user_id) "
        "WHERE is_baseline = true"
    ))
    db.session.commit()
```

### Change 2 -- `_recreate_canonical_index` helper, lines 132-147

Same defense in depth: drop before create. The current helper is called by
`test_assert_index_shape_raises_when_index_absent` immediately after `_drop_index`, so it
happens to work today, but if a future caller invokes it without dropping first, the
same bug returns. Replace:

```python
def _recreate_canonical_index(session) -> None:
    """..."""
    session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_scenarios_one_baseline "
        "ON budget.scenarios (user_id) "
        "WHERE is_baseline = true"
    ))
```

with:

```python
def _recreate_canonical_index(session) -> None:
    """..."""
    session.execute(text(
        "DROP INDEX IF EXISTS budget.uq_scenarios_one_baseline"
    ))
    session.execute(text(
        "CREATE UNIQUE INDEX uq_scenarios_one_baseline "
        "ON budget.scenarios (user_id) "
        "WHERE is_baseline = true"
    ))
```

**Why this is the right fix, not a workaround:** the bug is that the cleanup contract
"after this fixture runs, the canonical index is in place" was implemented in a way that
only holds if the test body left the index in a state where a CREATE-IF-NOT-EXISTS
matters. The fix restores the contract by ensuring the cleanup unconditionally restores
the canonical shape.

**Side effects:** the DROP INDEX IF EXISTS is a fast catalog operation (sub-ms on
empty/small tables). Worst-case overhead is ~1ms per test that uses the fixture.

**Out-of-scope alternative considered:** pinning `test_c41_baseline_unique_migration.py`
to a single worker via a module-level `pytestmark = pytest.mark.xdist_group("c41_index")`
would also eliminate the flake by guaranteeing test 14 and test 15 run on the same worker
in source order. I do NOT recommend this because (a) it papers over the bug rather than
fixing it; (b) it slows the file by serialising the 16 tests onto one worker; and
(c) the same bug would re-emerge if a future test outside this file ever uses the same
fixture or recreates the index without dropping first. Change 1 fixes the bug at its
source.

## Recommended fix path -- shape #1 (proposal; not implemented)

Two changes; both targeted.

### Change 1 -- wrap cleanup in `try/finally` for the five cleanup-less rate-limit tests

The same edit applies to:

- `tests/test_routes/test_errors.py:30` `test_429_renders_custom_page`
- `tests/test_routes/test_errors.py:70` `test_429_includes_retry_after_header`
- `tests/test_routes/test_auth.py:155` `test_rate_limiting_after_5_attempts`
- `tests/test_routes/test_auth.py:2028` `test_register_post_rate_limited`
- `tests/test_routes/test_auth.py:2105` `test_mfa_verify_rate_limiting`

Pattern to replace (using `test_429_includes_retry_after_header` as the canonical example):

```python
def test_429_includes_retry_after_header(self, app, seed_user):
    with app.app_context():
        rate_app = create_app("testing")
        rate_app.config["RATELIMIT_ENABLED"] = True
        from app.extensions import limiter
        limiter.enabled = True
        limiter.init_app(rate_app)
        rate_client = rate_app.test_client()
        with rate_app.app_context():
            for _ in range(6):
                response = rate_client.post(...)
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "900"
        # cleanup ...
```

with:

```python
def test_429_includes_retry_after_header(self, app, seed_user):
    rate_app = create_app("testing")
    rate_app.config["RATELIMIT_ENABLED"] = True
    from app.extensions import limiter
    limiter.enabled = True
    limiter.init_app(rate_app)
    try:
        rate_client = rate_app.test_client()
        with rate_app.app_context():
            for _ in range(6):
                response = rate_client.post(...)
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "900"
    finally:
        with rate_app.app_context():
            from app.extensions import db as _db
            _db.engine.dispose()
        if limiter._storage is not None:
            limiter.reset()
        limiter.enabled = False
```

This guarantees that even when the assertion fails, the limiter is reset and disabled
before the test exits, so subsequent tests on the same worker do not inherit the leaked
state.

`tests/test_integration/test_rate_limiter.py` already follows this pattern (via the
`_disable_limiter` helper inside a `try / finally`); the five tests above can mirror
its shape.

### Change 2 -- relax the timing-fragile assertion in `test_429_includes_retry_after_header`

Even with try/finally added, the assertion `Retry-After == "900"` is fragile under
parallel load. The behaviour we actually care about is "the header is set, the value is
plausibly a 15-minute window". Replace:

```python
assert response.headers["Retry-After"] == "900"
```

with:

```python
retry_after = int(response.headers["Retry-After"])
assert 895 <= retry_after <= 900, (
    f"Retry-After should be ~900s (15-min window), got {retry_after}"
)
```

The 5-second tolerance is generous enough that no plausible timing pressure can flake the
test, narrow enough that a real regression (e.g. the limit downgraded to 60s) would still
fire. Per Rule 5 (NEVER modify a test to make it pass) this needs developer confirmation
before applying -- the assertion was clearly intentional at write-time, and the relaxation
needs to be a deliberate choice.

**Defense in depth -- conftest-level limiter reset (optional):**

```python
# in tests/conftest.py, db fixture teardown:
finally:
    with _profile_step(timings, "teardown"):
        _db.session.remove()
        _db.engine.dispose()
        # Defensive: ensure the rate-limit singleton can never leak
        # enabled=True past test boundaries.  See phase1-flake-investigation.md
        # shape #1 root cause.
        from app.extensions import limiter as _lim
        if _lim._storage is not None:
            try:
                _lim.reset()
            except (AttributeError, OSError):
                pass
        _lim.enabled = False
    _profile_write_row(nodeid, timings)
```

This is optional; the per-test try/finally fixes the bug at its source. The conftest hook
is belt-and-braces protection against future tests that also forget try/finally. I would
recommend doing the per-test fix FIRST and adding the conftest hook only if there is
appetite for the defence-in-depth pattern.

## Open questions

1. **What is the true baseline (no Phase 1a) flake rate?** Prior session reports 0 / 2;
   this session has not measured. ~40 minutes of wall-clock for 10 baseline samples
   would close it. Hypothesis: nonzero but lower than Phase 1a's ~50 %.

2. **Are there OTHER `CREATE INDEX IF NOT EXISTS` cleanups in the test suite with the
   same shape-drift risk?** I have only inspected the c41 file. Test files c42 and c43
   have their own `restore_*_state` fixtures with index-drop / FK-rename test bodies;
   their cleanup patterns should be audited for the same IF-NOT-EXISTS-after-malformed
   shape.

3. **Is `--dist=loadgroup` actually delivering value over `--dist=loadfile`?** Loadfile
   eliminates shape #2 entirely AND runs ~12 % faster in my 4-run measurement (47s vs
   53s; smaller fixture overhead because each worker reuses ref_cache state across
   adjacent tests in the same file). The xdist_group marker on `shekel_app_role`
   cluster-state tests is the only declared dependency on loadgroup semantics; loadfile
   honors that marker as well (per pytest-xdist source). Switching the default to
   `--dist=loadfile` in `pytest.ini` may be worth considering independently of the
   shape #2 fix -- but it should be a separate change after the targeted fix lands.

4. **Should `test_assert_index_shape_raises_on_non_partial_index` use a side-DB instead
   of mutating the per-test DB's index in place?** A cleaner design would create a
   throwaway database, install a malformed index there, run the shape check against
   that connection, and discard the side-DB. This eliminates the cross-worker risk
   architecturally rather than relying on cleanup ordering. Out of scope for the
   targeted fix; could be a separate refactor.

## What was reverted from the working tree at session end

The following diagnostic edits were added during this session and removed before the
session ended (verified via `git diff tests/conftest.py` -- only Phase 1b changes
remain):

- A `pytest_runtest_makereport` hook that dumped failing-worker DB state to
  `/tmp/shekel-flake/snapshots/` when `SHEKEL_KEEP_FAILED_DB=1` was set.
- A conditional skip in `pytest_sessionfinish` that preserved per-session DBs when a
  worker exited non-zero with the same env var set.
- An index-shape and limiter-enabled tracker in the per-test `db` fixture that appended
  to `/tmp/shekel-flake/{idx-corrupt,limiter-leak}/<worker>.log` whenever the index lost
  its `WHERE` clause or the limiter singleton showed `enabled=True` at fixture entry.

The captured snapshot, idx-corrupt, and limiter-leak files under `/tmp/shekel-flake/`
remain on disk in case the developer wants to inspect them directly; they are not
checked in. Three snapshots are saved that show the malformed index definition in the
per-session DB at the moment of a shape #2 failure; one snapshot is saved that shows the
`AssertionError: '899' == '900'` shape #1 trigger on gw3 along with the index also being
malformed on the same worker (the two bugs were both present on gw3 simultaneously --
the first observable failure happened to be shape #1).
