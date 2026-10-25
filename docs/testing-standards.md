# Testing Standards and Problem Reporting

These standards apply to all testing activities in the Shekel project. They are referenced from
CLAUDE.md and are loaded when working on tests or when test-related decisions arise.

**This file is the rationale tier and the one home for the suite's dated measurements.** CLAUDE.md
states each rule in one line and points here; `.claude/rules/testing.md` carries the path-scoped
must-knows; a fact lives in one tier and the other tiers point at it.

---

## Test Infrastructure

- Tests use a real PostgreSQL database (`TEST_DATABASE_URL` or TestConfig defaults).
- `conftest.py` uses session-scoped app/db setup, truncates tables between tests.
- Test categories: `test_routes/`, `test_services/`, `test_models/`, `test_integration/`,
  `test_adversarial/`, `test_scripts/`.
- **Use existing fixtures** from `conftest.py` (`seed_user`, `seed_second_user`, `auth_client`,
  `second_auth_client`, etc.). Do not create ad-hoc user setup in test methods.
- **Check for existing coverage** before writing a new test. Duplicate tests waste time and create
  maintenance burden.

## Test Run Guidelines

- **Invoke via `./scripts/test.sh`, not bare `pytest`.**
  **The wrapper gives the run a POSTGRES CLUSTER OF ITS OWN** (`balance:X-br-4`, 2026-09-05): a
  container started from the image `scripts/build_test_db_image.py` bakes -- the template already
  inside it -- on a rootless docker daemon, reached over a unix socket in a per-run directory,
  removed on every exit path including Ctrl-C. It exports `TEST_DATABASE_URL` and
  `TEST_ADMIN_DATABASE_URL` at that socket rather than reading them from `.env`, defaults the marker
  expression, and forwards all arguments verbatim.
  **There is no shared-container path and no flag to select one**; a run that cannot reach a
  rootless daemon exits 2 with instructions rather than falling back to the daemon that serves
  production. CI runs the same wrapper, on its runners' own throwaway daemons (see **CI** below).
- **What the private cluster costs.** Measured 2026-09-05 on `tests/test_utils/` (385 tests), same
  host, back to back: 12.7 s against the shared cluster, 14.1 s in a private one with the image
  built and its layers warm, 22.0 s on the first run after a rebuild. The fixed part is the image
  verification (1.4 s, and it runs on EVERY invocation rather than trusting the tag) plus the
  container's whole life -- start, readiness and removal -- at 0.33 s. Quote none of these without
  the date.
- **Container-spawning deploy tests are excluded by default.** The `tests/test_deploy` integration
  tests that drive a real `docker` daemon are marked `@pytest.mark.docker`, and `./scripts/test.sh`
  defaults to `-m "not docker"` so a routine local run never spawns containers on the host's
  production Docker daemon (which the homelab `wud`/`cadvisor`/`alloy` stack watches). CI's shards
  pass `-m ""` to the wrapper, so they still execute them. A `tests/test_deploy/conftest.py` guard
  also skips them if a bare `pytest` reaches the system daemon outside CI. Since the wrapper selects
  and exports an isolated `DOCKER_HOST` itself, the local opt-in is now just
  `PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy/...` --
  `SHEKEL_ALLOW_HOST_DOCKER=1` is no longer part of it and means "accept the churn on the production
  daemon". Measured 2026-09-05: 25 passed, 3 skipped on the rootless daemon against 28 skipped on
  the system one; the 3 are a published-port collision in the nginx fixtures, and they SKIP rather
  than fail, so that defect thins a green suite silently. Full rationale and the daemon-isolation
  plan: `docs/test-harness-isolation.md`.
- **Full suite:** ~13,000 tests, roughly 5-8 min at the default `-n 12` parallelism (set in
  `pytest.ini` `addopts`). Measured 2026-08-30: 11,788 passed in 278-296 s over four runs, ~18 s
  run-to-run variance. Measured 2026-09-04 on `chore/test-restart-default`, all three on the shared
  cluster under the (now deleted) suite slot with `RESTART_TEST_DB=1`: 13,019 passed in 477 s,
  13,019 in 370 s, and 13,020 in 325 s. Measured 2026-09-05 on `refactor/test-delete-fences`, the
  first figures from a PRIVATE cluster and nothing else running: 13,238 passed in 349 s.
  **Do not quote any of these without their date** -- eight runs across seven days spread from 278 s
  to 477 s, so a bare number is not evidence, and the count moves with the branch (the 2026-09-04
  third figure differs because the commit that produced it adds a test). None of them is a
  contention measurement: each ran alone. The wrapper's own output is the current measurement.
- **Concurrent invocations need no coordination, and the slot that provided it is deleted.**
  `scripts/suite_slot.sh` (PR #199, 2026-09-02) was a mandatory mkdir-based mutex; `balance:X-br-4`
  removed it with the shared postmaster it protected. Its header named TWO hazards and only one of
  them was about shared state:
  - **Correctness is now structural.** No run can restart another's backends (there is no shared
    container to restart) and none can collide on a per-worker database name (there is no shared
    name space). The measured incident that made the slot mandatory -- the live-backend probe
    reading ZERO while a 756 s full-suite run was live on 2026-09-04, and the restart that followed
    voiding it with 155 setup errors -- is unrepresentable rather than mitigated. That finding is
    **N-457** and this is its closure.
  - **Contention survives, because the cores do not multiply.** The slot's own header carried 859 s
    contended against 304 s alone, measured on ONE postmaster where the cluster-wide `pg_database`
    catalog lock was the serialised resource; that serialiser is gone, so the figure does not carry
    over and was re-measured. **2026-09-05, 24-core host:** one suite alone, 349 s; with THREE
    running (two private clusters plus a peer's gating run on the shared one), two of them reached
    ~38% in 13 minutes, at a run-queue of 32, ~950,000 context switches/sec and 28% iowait.
    **Neither produced a failing test**, and the slowest single test is 2.58 s against
    `pytest.ini`'s per-test timeout, 30 s at the time -- about 11x of headroom, which this
    measurement was sitting on. A fourth concurrent suite is roughly where a timeout would start
    failing a test that is not broken; that has not been measured. (The cap is 50 s since 2026-09-22
    and is sized to CI's clock, not this host's: see **Test timeout** below.)

  So what remains is a resource fact rather than a defect, and the instrument is information: the
  wrapper prints any other live pytest it can see, with its worktree, and proceeds.
  **The cwd locates a peer and the argv does not** -- every worktree on this host shares one venv,
  so a peer's command line names the main checkout whatever tree it is testing.
- **First-time setup: none, and a migration needs no manual rebuild.** The template is baked into a
  tagged image whose cache key is derived from every input the build reads, and the wrapper
  re-verifies that image on EVERY invocation rather than trusting the tag -- so a stale or damaged
  one is rebuilt at the door instead of being cloned from for the whole run. See "Building the test
  template" below for the one caller that still runs `scripts/build_test_template.py` directly.
- **Before reporting done:** every batch (or the single full- suite invocation) must end in
  `<N> passed`; any `failed`, `errors`, or `xfailed` lines block the "done" report.
- **During development:** run only relevant test files; targeted runs typically finish in seconds.
- **Override parallelism:** `-n 0` for single-process debugging, `-n auto` to match the host CPU
  count, or any specific number. The CLI flag overrides `pytest.ini`'s default. Past `-n 12` the
  marginal speedup falls off because PostgreSQL's cluster- wide `pg_database` catalog lock (formerly
  the WAL/fsync pipeline pre-Phase-3) is the serialised resource; see
  `docs/audits/test_improvements/test-performance-research.md` for the full profile.
- **CI: six shards, each through the wrapper at `-n logical`** (plan step `bank_import:X-gy`,
  rulings R-BI38..R-BI41, 2026-09-22). `.github/workflows/ci.yml` runs a `scope` job (the change-set
  classifier), `commit-trailers` (step X-cx's L4, on the range `scope` hands over), `landing` (step
  X-cx's M2: which change may write a ship marker), `plan-gate`, `tax-law`, `lint` and six `test`
  shards in parallel, and a `lint-and-test` job -- the check branch protection requires -- that
  needs them all, runs `if: always()` and fails closed through `tools/ci/ci_verdict.py` (GitHub
  counts a SKIPPED required check as passing). Each shard exports
  `SHEKEL_TEST_SHARD=<index>/<total>` and keeps the share `tests/_shard.py` assigns: a stable hash
  of each test's `xdist_group` or node id, so the shards partition the collection by construction
  and every group lands whole on one shard --
  **a test that depends on another test's session must share an `xdist_group` with it.** The weekly
  `calendar-sweep.yml` runs the wrapper too, so every CI run has the one cluster configuration a
  local run has. **Why, measured on the runner in a matched A/B** (draft PR #447, runs 35803297014,
  35806759874, 35808935180): the hosted runner is CPU-BOUND -- 4 vCPUs that are 2 physical cores
  with SMT, a fixed CPU benchmark 1.2-2.1x slower per core than this host, the fleet a mix of EPYC
  7763 / 9V74 / 9V45 and Xeon 6973P. On one 1,230-test slice, arms run in sequence on ONE runner:
  two or four postgres clusters per runner gained 0-7% over one; PGDATA on tmpfs saved 2-11%, and
  the wrapper's cluster (socket, baked image, its flags) 8-15% over the TCP service container; and
  it ran it at `-n 12` / `-n 6` / `-n 4` / `-n 3` in 285.1 / 265.8 / 259.1 / 301.4 s on one runner
  and 310.4 / 276.5 / 272.6 / 311.2 s on another. Per-test wall clock scaled with the worker
  count -- setup plus body 0.79 s at `-n 4`, 2.46 s at `-n 12` -- so the single job's 41-61 min (the
  suite 35-52 of them at `-n 12`) was CPU time stretched by oversubscription, not a database or disk
  defect. Ledger **BI-496**'s 5-13x per-test ratio is CONSISTENT with that rather than fully
  measured: 3.1x from the worker count (2.46 / 0.79) times 1.2-2.1x per core is 3.7-6.5x, and the
  top of the range fits SMT and postgres sharing two physical cores, which no arm measured apart.
  Six shards ran 6.4-9.7 min per job; their union was the runner's own collection of that tree
  exactly (15,166 items, none in two shards, the container tests included). Quote none of these
  without the date.
- **Test timeout:** 50 s per test, configured in `pytest.ini`; it covers setup + call + teardown,
  and anything past it raises a timeout error rather than hanging the suite.
  **The cap is a hang detector sized to CI's clock, and CI's clock is not this host's.** Measured
  2026-09-22 under the six-shard layout (run 35808935180, five of six shards on the slowest CPU the
  fleet drew, an EPYC 7763): the slowest test was `test_pay_grid_month_kinds.py`'s
  `test_the_semi_monthly_grid_matches_the_listing_on_every_pair` at a 16.16 s call, and the
  settled-bound family (four cases, `filed_acts` = 51 real door calls each, in
  `tests/test_services/test_statement_match/test_reconcile.py::TestTheSettledBoundIsLIFTABLE` and
  `tests/test_routes/test_statement_reconcile.py::TestTheSettledBoundIsWiredToThePage`) 5.1-8.6 s.
  50 s is 3x the slowest (ruling **R-BI41**), the same 3x the cap was sized by before.
  **Its history, for the numbers it replaced:** 30 s until 2026-09-13; then 90 s, sized as 3x the 30
  s that run 34737731259 attempt 1 crossed on an honest test (call cut at 28.06 s) when the single
  job ran `-n 12` on 4 cores -- the twelve full CI runs 34711892311 .. 34737731259 (2026-09-12
  18:40Z to 2026-09-13 04:22Z) took the suite 26:39-47:38 against 6:01-6:07 here, and the family's
  call read 12.7-28.1 s there against 2.3-2.5 s here.
  **A local `--durations` figure is never the basis for a CI budget.** Re-measure from the durations
  table each CI shard prints, never from this host.

## Why the cluster is per-run, not shared

Phase 3b's per-test drop+reclone gives strict isolation at a price PostgreSQL only charges a
LONG-LIVED postmaster, and the price is why `scripts/test.sh` now throws its cluster away after
every run.

**The behaviour.** Over many back-to-back suite runs on one container, full-suite wall clock drifts
linearly -- measured from a freshly restarted container as 71 s, 72 s, 76 s, 81 s over four runs and
220 s at ~50 runs / 37 h uptime, entirely inside `DROP DATABASE WITH (FORCE)`. It is not on-disk
bloat: `VACUUM`, `VACUUM (FULL)` on `pg_database` / `pg_shdepend` / `pg_shseclabel` /
`pg_db_role_setting` and `CHECKPOINT` moved DROP time not at all, and the catalogs are 1 page with 5
live rows even in the degraded state. The accumulation is in shared memory -- the `sinval` queue,
syscache and relcache invalidations every DDL broadcasts, consumed slowly by the long-lived backends
an xdist worker pool holds. Verified by the negative: 5,000 CREATE/DROP cycles through fresh `psql`
connections do NOT fragment (DROP stays ~3 ms), so it takes both halves, many long-lived backends
AND heavy DDL. Only restarting the postmaster resets it.

**Which is why a cluster that lives for one run cannot accumulate any of it.** The wrapper used to
carry a `RESTART_TEST_DB` hygiene restart for this, plus a live-backend probe so the restart did not
kill a peer worktree's run; `balance:X-br-4` deleted both along with the shared container. Do not
restore a shared cluster without restoring the restart, and do not quote the figures above as
current: they were taken at `c1e9c775` (2026-05-20) against ~5,504 tests under `STRATEGY FILE_COPY`,
and the clone now uses `STRATEGY WAL_LOG`. One companion figure is not merely stale but
self-refuting and is WITHDRAWN rather than re-pinned: a CREATE/DROP round-trip "past ~15 ms" was
once offered as the signal to restart, and the same table read 14.6 ms on a freshly restarted
container and 15.6 ms after ONE run -- a cutoff inside one run's worth of movement, which is
"restart every time" wearing the clothes of a measurement.

**Why not just VACUUM the shared catalogs from a sessionstart hook?** Tried; does not help, for the
reason above -- the fragmentation is in shared memory, not on-disk pages.

**Why not switch back to TRUNCATE-based reset?** The Phase 3b move to drop+reclone was driven by
audit-trigger and DDL-state isolation requirements (see
`docs/audits/test_improvements/per-worker-database-plan.md`). Reverting would re-introduce the bugs
Phase 3b fixed.

**The clone strategy is `WAL_LOG`, not `FILE_COPY`** -- see
`tests/conftest.py::_clone_worker_database` for the measurements that forced the change. `FILE_COPY`
forces three cluster-wide checkpoints per drop+create cycle against one for `WAL_LOG`, which costs
nothing on a cluster with `fsync=off` and 20x on any cluster with durability on, including CI until
2026-08-18. The private cluster is started with `fsync=off`, `synchronous_commit=off` and
`full_page_writes=off` copied from the compose file's shared test-db, and that is what makes it
affordable: with docker's default durability the same suite took 753 s against 356 s, because one
drop+create cycle is 1618 ms with fsync on and 31 ms with it off.

## Optional per-directory batching (historical)

The 8-batch split below was required when the suite was ~28 min sequentially and the 10-min CI
timeout forced sub-batches. At the `-n 12` default (the dated full-suite measurement is under Test
Run Guidelines above) it is **purely historical** -- batched invocations no longer offer any
wall-clock benefit and individual batches finish in seconds, so the bisecting-a-regression and
sequential- debugging scenarios are better served by `pytest <specific-file> -v` rather than a whole
batch. The table is preserved so existing references to "Batch N" in old commits or docs remain
decodable; DO NOT cite these timings in new measurements.

| Batch | Tests | Notes |
|---|---|---|
| `tests/test_config.py tests/test_models/ tests/test_services/` | ~1,740 | includes the Phase 0 harness slice (test_models, 253 tests) |
| `tests/test_routes/test_a* tests/test_routes/test_c*` (includes `test_auth.py`, slowest single file) | ~860 | -- |
| `tests/test_routes/test_d* test_e* test_g* test_h* test_i*` | ~390 | -- |
| `tests/test_routes/test_l* test_m* test_o* test_p*` | ~290 | -- |
| `tests/test_routes/test_r* test_s* test_t* test_x*` | ~690 | -- |
| `tests/test_integration/` | ~220 | -- |
| `tests/test_adversarial/ tests/test_scripts/ tests/test_deploy/` | ~545 | -- |
| `tests/test_audit_fixes.py test_ref_cache.py test_schemas/ test_utils/ test_concurrent/` | ~400 | -- |

Total then, in that era's own figures: ~5,504 tests / ~65 s at `-n 12` via `./scripts/test.sh` (full
suite is faster than the sum of batches because pytest startup + 12-worker bootstrap overhead
amortises over the full inventory rather than paying 8x); DO NOT cite these timings in new
measurements either. `tests/test_performance/` (the audit trigger's overhead REPORT, which prints
and asserts no time) is excluded from the default `addopts` and runs SERIALLY, as CI step 7b runs
it: `./scripts/test.sh tests/test_performance -q -n 0 -p no:randomly --override-ini=addopts=`. The
required check is `tests/test_integration/test_audit_trigger_work.py`, in the ordinary suite.

## Building the test template

`shekel_test_template` is the PostgreSQL template database that
`tests/conftest.py::_bootstrap_worker_database` clones into a uniquely-named per-session DB at the
start of every pytest invocation (and every pytest-xdist worker within a session). Cloning a
populated template is roughly two orders of magnitude faster than running migrations + audit
infrastructure + reference seed per session, which is what unlocks the parallel and concurrent-safe
test runs documented above.

**One caller runs it, and it is not you.** `scripts/build_test_db_image.py` runs it inside a bake
container and commits the result as a tagged image. `./scripts/test.sh` -- every local run, and
since `bank_import:X-gy` every CI job -- clones from the baked image, so there is no first-time
build to remember and no rebuild step after a migration: the image's cache key is derived from every
input the build reads, and the wrapper re-verifies the image on EVERY invocation, so a key that
moved rebuilds at the door.

**The key is not a function of the migrations alone**, which is why it covers more than
`migrations/`: `_populate_template` re-applies the in-code trigger definitions AFTER
`alembic upgrade`, deliberately, so the latest definition wins over the migration-frozen one.
Editing `app/audit_infrastructure.py`, `app/posting_infrastructure.py` or
`app/opening_infrastructure/` changes the template without touching a migration at all. A key that
hashed only the migrations would go stale silently, which is the one failure mode that corrupts
results rather than merely slowing them -- and the verification, not the key, is the correctness
argument.

**Running it by hand** is still the recovery path if you want to inspect the build: it is
idempotent, dropping and recreating `shekel_test_template` on every run, and prints three steps --
drop+create, populate (Alembic chain to `head` + audit infrastructure + reference seed +
`TRUNCATE system.audit_log`), verify (account-type count, audit trigger count, `system.audit_log`
row count). It reads `TEST_ADMIN_DATABASE_URL` for the admin DSN (default `postgresql:///postgres`).
`SECRET_KEY` is defaulted by the script -- the template DB is never reachable through Gunicorn so
the value is purely scaffolding for app construction.
**The template's NAME is a constant, not a knob**: it was resolved from `TEST_TEMPLATE_DATABASE` so
two checkouts on one postmaster could name their templates apart, and `balance:X-br-4` deleted that
override with the shared postmaster. `tests/conftest.py` and this script now spell the same literal
and MUST agree.

**If the bootstrap raises `RuntimeError`** complaining the template is missing or has the wrong
row/trigger count, the error message names the offending count and the likely root cause. Under the
wrapper that should be unreachable -- the image is verified before pytest starts -- so reaching it
means something else supplied the cluster.

## Cluster-state tests and `xdist_group`

PostgreSQL has two kinds of state. Per-database state (rows, indexes, triggers, schemas) is isolated
by the per-session DB clone: two xdist workers writing to `budget.transactions` cannot collide
because each writes to its own database. Cluster-scoped state (`CREATE ROLE`, replication slots,
`pg_advisory_lock`) is shared across all databases in the cluster; two workers racing on the same
cluster-level operation will collide.

The only test file in the current suite that mutates cluster state is
`tests/test_models/test_audit_migration.py`: the `shekel_app_role` fixture executes
`CREATE ROLE shekel_app` and `DROP ROLE shekel_app`, and `apply_audit_infrastructure` (called from
sibling test classes in the same file) conditionally `GRANT`s to the role when it exists. Both touch
the cluster.

**Pattern:** pin all tests that touch cluster state to one pytest-xdist worker via
`@pytest.mark.xdist_group("name")`:

```python
import pytest

# Module-level marker pins every test in this file to a single
# pytest-xdist worker.  pytest.ini's --dist=loadgroup is what
# makes the marker actually serialise (under the default
# --dist=load the marker is metadata only).
pytestmark = pytest.mark.xdist_group("shekel_app_role")
```

Use a **module-level** marker when sibling classes share the cluster-state coupling (as in
`test_audit_migration.py`). Use a **class-level** or **test-level** marker when only some tests in
the file are affected.

The marker **name** must be unique per cluster-state resource: tests sharing a name run on the same
worker, so two unrelated cluster-state tests with the same name would unnecessarily serialise.
Prefer distinct names per resource.

If you add a new test that mutates cluster state, add the marker and a comment naming the resource.
Without it the test will race across workers and produce intermittent failures like
`role "X" already exists` or `DependentObjectsStillExist` on `DROP ROLE`.

## Zero Tolerance for Failing Tests

When you run the test suite -- targeted or full -- every test must pass. If any test fails, you must
investigate. Do not report "done" while any test is failing.

If a test you did not write is failing:

1. Determine what it tests.
2. Determine whether your changes caused the failure.
3. If your changes caused it, fix your code (not the test -- see CLAUDE.md rule 5).
4. If your changes did not cause it, report the failure with full details and ask how to proceed.

Never assume a failing test is someone else's problem. There is no one else.

## Test Output is Evidence

When reporting test results, include the actual output -- pass counts, fail counts, error messages.
Do not summarize "tests passed" without showing it. If output is long, show the final summary lines
at minimum.

## Test Quality Standards

A test that does not verify behavior is worse than no test -- it creates false confidence.

### Route Tests

Route tests must assert **response content, not just status codes.** A 200 means Flask did not
error. It does not mean the response is correct. After the status code, assert: correct records
present, financial amounts correct, right template rendered, expected HTML fragments in HTMX
responses. For JSON, assert structure and values. For form submissions, assert database state
changed correctly.

### Service Tests

Service tests must assert **computed values with exact expectations.** Do not assert `result > 0` or
`result is not None` when you can compute the expected value by hand. For financial calculations,
every test should include a comment showing the arithmetic that produces the expected value.

### Edge Case Tests

Edge case tests must assert the **specific edge behavior**, not just that the function did not
crash. A test for "zero amount" must assert what happens with zero, not just that no exception was
raised.

### General Test Requirements

- **All tests need docstrings** explaining what is verified and why.
- **Tests must be independent.** Each test sets up its own preconditions. No ordering dependencies
  or shared mutable state between tests.
- **Test the behavior, not the implementation.** Assert what the function produces, not how it
  produces it. Implementation-coupled tests break on every refactor.

---

## The ambient clock and the ambient calendar

**Depth, pitfalls and how to read a failure from either gate: `docs/test-suite-clocks.md`.**

**A test that passes on some days and fails on others is not a flaky test. It is a broken test with
a schedule.** Every "flake" this suite has produced has turned out to be one of the two couplings
below, and each was diagnosed only after it blocked a merge gate. Both are cheap to avoid and
expensive to find.

### 1. Use the APPLICATION's clock, never the process's

`date.today()` reads the PROCESS timezone. The application's civil day is
`app.utils.dates.display_today()` (`America/New_York`). Production and `docker-compose.dev.yml` both
pin `TZ: America/New_York`, so the two agree there -- **CI does not pin it and runs UTC**, so for
the four hours a day the calendars disagree, a test that mixes them fails.

> **Whenever a test builds a date that the application will compare against its own "today", the
> test must use `display_today()`.**

That covers an `entry_date` posted to a route (`entry_service._reject_future_entry_date` refuses
anything after `display_today()`, ruling R-M), a pay-period window that must contain the day
`get_current_period` looks for, an anchor's `observed_on`, and any assertion on a date the app
derived. Three live examples, all of which failed CI on 2026-08-01 and none of which was
reproducible in a dev shell:

| site | was | is |
|---|---|---|
| `test_c19_credit_payback_unique.py` -- posted `entry_date` | `date.today()` | `display_today()` |
| `test_c19_credit_payback_unique.py` -- period window | `date.today()` | `display_today()` |
| `test_optimistic_locking_c18.py::_make_entry` | `date.today()` | `display_today()` |
| `test_account_anchor_invariant.py` -- signup period | `date.today()` | `display_today()` |

**Do not "fix" this class by pinning CI's timezone.** That hides the coupling instead of removing
it, and the coupling is what breaks the moment any environment differs.

**How to check your work:** run the suite with the process clock shifted off the display clock. The
whole suite must pass unchanged.

```bash
TZ=Pacific/Kiritimati ./scripts/test.sh     # process date one day AHEAD of the app's
```

**This is a gate, not a suggestion.** `ci.yml`'s `lint-and-test` job sets `TZ: Pacific/Kiritimati`
deliberately, so the process date runs a day ahead of the app's for eighteen hours out of every
twenty-four and the coupling fails on most runs rather than on the four-hour window that let three
of these reach `main`. Never "fix" a failure there by setting CI's zone to `America/New_York`.

### 2. A fixture must not depend on WHERE IN THE CALENDAR it runs

Deriving fixture dates from "today" makes the SHAPE of the fixture depend on the date the suite
runs. Findings N-131 (six cross-page tests failing on the last days of a month), N-132 (fixtures
separating events by hours against a rule that reads civil days), R8 (an offset that silently became
a duplicate of its sibling) and the 2026-08-01 loan failures are all this.

The 2026-08-01 case is the clearest: a fixture originated a loan at the current period's start with
`payment_day=1`, and its own docstring promised *"clean past: no overdue installment"*. Whether that
was true depended on the day of the month --

- on the **1st** the first installment fell on today, where `balance_at` reads the ledger
  (`balance_at/_positions.py`) and the committed schedule shows the post-payment projection;
- on the days **between the 1st and the month's first Monday** it was overdue and unpaid, so the
  fold pays nothing for it (D1 / B-9) while the schedule still lists it -- and every later row
  diverges.

**State the property the fixture needs, then construct it so the property holds on every calendar
day**, rather than deriving from today and hoping. Pin the read
(`BalanceContext.build(user_id, as_of=...)`) to a date the fixture itself controls, and derive the
other dates from THAT.

Two shapes worth naming because both shipped:

- **Never `date.replace(year=...)`.** On 29 February it raises
  `ValueError: day 29 must be in range 1..28`. Use `app.utils.dates.add_months(d, 12)`, which clamps
  to 2029-02-28.
- **Never hard-code a date that is "in the future".** `target_date=date(2027, 6, 1)` stops being in
  the future on 2027-06-01, and the test that depends on it starts failing that morning. Derive it:
  `add_months(display_today(), 10)`.

**The gate:** `.github/workflows/calendar-sweep.yml` runs the whole suite weekly as if today were a
leap day, both sides of a year boundary, a month end, and the first of a month. Run one locally with

```bash
SHEKEL_FAKE_TODAY=2028-02-29 ./scripts/test.sh
```

### What the sweep cannot see, and the marker that says so

`time-machine` moves Python's clock. It cannot move POSTGRES: `created_at` / `updated_at` are
`server_default=db.func.now()` and `paid_at` is `db.func.now()`. So under a faked date a
server-stamped row carries the REAL instant, and any test comparing it against a Python-derived date
fails by the offset -- an artifact of the instrument, not a defect.

Those tests carry `@pytest.mark.server_clock`, and the sweep deselects them. Four of them assert the
database's clock on purpose (the `CURRENT_DATE` server default; the audit trigger's `executed_at`;
the stamp arm and the up-then-down round trip in
`test_a_transfer_side_knows_its_own_day_migration.py`, which grade a Paid press's day against its
own audit row's `executed_at`).

**The marker is a statement about the instrument, never a way to quiet a failure.** Every marked
test still runs in ordinary CI. A test earns the marker only after its failure has been traced to
the two clocks -- and **"it fails at some dates but not others" is not sufficient evidence**:
`test_two_same_day_trueups_reconcile` varies by date and is still an artifact, because its two
clocks only diverge once the faked date moves far enough. Trace to the clocks; do not infer from the
pattern.

---

## Problem Reporting Protocol

You are the only automated safeguard this project has. If you see a problem and say nothing, that
problem ships to production.

### What Counts as a Problem

A failing test. A linter warning. A logic error noticed while reading code. A function that does not
handle an edge case. A query missing a `user_id` filter. A Decimal compared to a float. A TODO that
has been there for months. An unused import. A migration that does not match the model. Any
discrepancy between what the code does and what it should do.

### Response Protocol

1. **Within scope of the current task:** Fix it. Test the fix. Include it in the commit.
2. **Outside scope but quick and safe:** Report it to the developer. Fix in a separate commit only
   if the developer approves.
3. **Outside scope and risky or complex:** Report it immediately. State: what the problem is, where
   it is (file and function), what the impact could be, and your recommended next step. Lead with
   it -- do not bury it at the end of a long message.

### What You Must Never Do

- Say "this test was already failing" and move on.
- Say "this is unrelated to my changes" without investigating and reporting.
- Say "tests pass" when any test failed.
- Treat a pre-existing bug as acceptable because it predates your work.
- Assume the developer knows about a problem. If you are not certain, tell them.
