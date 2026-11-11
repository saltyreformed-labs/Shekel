"""The fixture profile harness: opt-in timings of the ``db`` fixture's steps.

``tests/conftest.py`` imports this module first, before the worker database is
bootstrapped, so ``_profile_session_init`` runs at import where it always ran.  The ``db``
fixture (:mod:`tests._app_fixtures`) times its steps with :func:`_profile_step`, and the
conftest's ``pytest_sessionfinish`` prints :func:`_profile_print_summary`.  It sits
directly under ``tests/`` because its CSV directory is ``tests/.fixture-profile/``, which
``.gitignore`` names.
"""

import csv
import os
import pathlib
import statistics
import time
from contextlib import contextmanager


# ---------------------------------------------------------------------------
# Fixture profile harness (Phase 0 of test-performance-implementation-plan)
# ---------------------------------------------------------------------------
# Permanent instrumentation around the per-test ``db`` fixture inner
# steps, gated behind ``SHEKEL_TEST_FIXTURE_PROFILE=1`` so the default
# test path is unaffected.  When the flag is set, each test appends
# one row to a per-worker CSV in ``tests/.fixture-profile/`` recording
# elapsed milliseconds for rollback / TRUNCATE main / seed_ref /
# commit / TRUNCATE audit_log / refresh_ref_cache / call / teardown.
# At session end the aggregator reads every worker CSV and prints a
# summary table whose shape matches
# ``docs/audits/test_improvements/test-performance-research.md``
# section 3.1.
#
# What lives here and what does not: the ``with _profile_step(...)``
# wrappers must be physically interleaved with the ``db`` fixture's
# body, so they stay in it (``tests/_app_fixtures.py``), and the
# summary must be printed from ``pytest_sessionfinish``, a
# conftest-level hook, so that call stays in ``tests/conftest.py``.
# The helpers they call need neither ``_db`` nor the fixture's local
# state, so they live here.
#
# Why the flag is checked once at module load (not per-test): we want
# zero per-test cost when disabled.  A single module-level boolean
# costs one branch per ``with _profile_step(...)`` block at fixture
# entry -- well below the noise floor of the operations it wraps.

_FIXTURE_PROFILE_ENABLED = os.environ.get("SHEKEL_TEST_FIXTURE_PROFILE") == "1"
_FIXTURE_PROFILE_DIR = pathlib.Path(__file__).parent / ".fixture-profile"

# Step names, in column order.  Drives the CSV header, the per-test
# row writer, and the row order in the summary table.  The leading
# ``setup_`` prefix tags steps that contribute to "Fixture setup
# total" in the aggregator (vs. ``call`` and ``teardown`` which are
# reported but not part of the fixture-percent column).  The names
# match the labels in the published baseline so a future reader can
# diff the two tables cell-for-cell.
_FIXTURE_PROFILE_STEPS = (
    "setup_rollback",
    "setup_drop_db",
    "setup_clone_template",
    "setup_refresh_ref_cache",
    "call",
    "teardown",
)

# Pretty labels for each step, used only by the aggregator's print
# pass.  Kept beside _FIXTURE_PROFILE_STEPS so future edits stay in
# sync.  Phase 3b replaced ``setup_truncate_main`` /
# ``setup_seed_ref`` / ``setup_commit_after_seed`` with
# ``setup_drop_db`` + ``setup_clone_template``; the published
# baseline comparison in
# ``docs/audits/test_improvements/test-performance-implementation-plan.md``
# remains diff-able per-step because the surviving step keys
# (``setup_rollback``, ``setup_refresh_ref_cache``, ``call``,
# ``teardown``) and their labels are unchanged.
_FIXTURE_PROFILE_LABELS = {
    "setup_rollback": "rollback",
    "setup_drop_db": "DROP DATABASE WITH (FORCE)",
    "setup_clone_template": "CREATE DATABASE TEMPLATE STRATEGY WAL_LOG",
    "setup_refresh_ref_cache": "refresh_ref_cache",
    "call": "Test body (call)",
    "teardown": "Teardown",
}

# Per-worker CSV path.  ``PYTEST_XDIST_WORKER`` is ``"gw0"``,
# ``"gw1"``, ... under xdist and unset under single-process pytest
# (we use ``"main"`` for the latter, matching the bootstrap's
# ``worker_id`` convention).  Each worker writes to its own file so
# concurrent appends never contend on a lock.
_FIXTURE_PROFILE_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "main")
_FIXTURE_PROFILE_CSV = (
    _FIXTURE_PROFILE_DIR / f"{_FIXTURE_PROFILE_WORKER_ID}.csv"
)


def _is_xdist_master():
    """Return True for the pytest-xdist controller process.

    The controller spawns workers and runs collection but does NOT
    execute tests; it sets ``PYTEST_XDIST_TESTRUNUID`` but leaves
    ``PYTEST_XDIST_WORKER`` unset.  Workers (``gw0``, ``gw1``, ...)
    set both, and single-process runs set neither.  The harness uses
    this distinction to skip the per-test CSV setup on the master
    while still running the aggregator there (the master is the only
    process that sees every worker's output after they exit).
    """
    return (
        bool(os.environ.get("PYTEST_XDIST_TESTRUNUID"))
        and not os.environ.get("PYTEST_XDIST_WORKER")
    )


def _profile_session_init():
    """Wipe stale CSVs and prepare this process's profile file.

    Two-phase:

    1. The xdist master (or single-process run) wipes any leftover
       ``*.csv`` from a previous pytest invocation before workers
       spawn.  Without this, a previous run with ``-n 16`` would
       leave ``gw13``..``gw15.csv`` on disk and the next ``-n 12``
       run's aggregator would mistakenly include their stale rows.
       Worker subprocesses load the conftest, and so this module,
       AFTER the master, so the wipe is finished by the time they
       create their own CSVs.
    2. Every process whose conftest load happens before xdist sets
       ``PYTEST_XDIST_TESTRUNUID`` creates the dir and writes a
       header row to its worker CSV.  Empirically (pytest-xdist
       3.8 on Python 3.14) this includes both single-process runs
       AND the xdist master -- the master never runs tests, so its
       ``main.csv`` ends up as a header-only stub.  Workers
       (``gw0``..``gwN``) load conftest later, with ``TESTRUNUID``
       already set, but they are detected via ``PYTEST_XDIST_WORKER``
       not via TESTRUNUID, so the ``_is_xdist_master`` check below
       is defence-in-depth for a future xdist that sets
       ``TESTRUNUID`` earlier on the master.

    Truncating-on-init means two consecutive pytest runs with the
    same worker id do not accumulate -- the second run starts from
    a clean header row.

    No-op when ``SHEKEL_TEST_FIXTURE_PROFILE`` is unset.
    """
    if not _FIXTURE_PROFILE_ENABLED:
        return

    # Phase 1: master / single-process wipes stale CSVs.  In xdist
    # mode the master loads conftest before workers spawn, so this
    # runs first; workers see a clean directory.  (The master also
    # writes its own main.csv header in phase 2 below -- the master
    # never runs tests so that file ends up as a header-only stub.
    # The aggregator handles it correctly: ``DictReader`` returns
    # zero data rows, so the stub contributes nothing to the
    # summary.  Removing it would require an extra teardown step
    # for ~150 bytes of harmless residue.)
    if not os.environ.get("PYTEST_XDIST_WORKER") and _FIXTURE_PROFILE_DIR.exists():
        for stale_csv in _FIXTURE_PROFILE_DIR.glob("*.csv"):
            stale_csv.unlink()

    # The xdist master does not run tests -- it has nothing to
    # write into its own CSV, so skip phase 2 entirely.
    if _is_xdist_master():
        return

    # Phase 2: this worker's CSV gets a fresh header.  Open mode
    # ``"w"`` truncates; subsequent per-test rows are appended in
    # mode ``"a"`` from ``_profile_write_row``.
    _FIXTURE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with _FIXTURE_PROFILE_CSV.open("w", newline="", encoding="utf-8") as csv_fp:
        writer = csv.writer(csv_fp)
        writer.writerow(["nodeid", "worker_id", *_FIXTURE_PROFILE_STEPS])


@contextmanager
def _profile_step(timings, step_name):
    """Record elapsed milliseconds of the wrapped block into ``timings``.

    Wraps a block of fixture code so the harness can measure each
    inner step without restructuring the fixture itself.

    Args:
        timings: Either a dict (profiling enabled) keyed by step name
            with float-millisecond values, or ``None`` (profiling
            disabled).  ``None`` short-circuits the timer so the
            wrapped block runs with zero added cost.
        step_name: One of ``_FIXTURE_PROFILE_STEPS``; the key under
            which to store the elapsed time.

    Even when the wrapped block raises, the timer captures the
    elapsed time before the exception propagates.  The exception
    itself is not suppressed -- the harness must never mask test or
    fixture errors.
    """
    if timings is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step_name] = (time.perf_counter() - start) * 1000.0


def _profile_new_timings():
    """Allocate a per-test timings dict, or ``None`` when disabled.

    Pre-populates every step key with ``0.0`` so the CSV row is
    well-formed even if a setup step raises and short-circuits the
    rest of the fixture body: the steps that ran record real timings,
    the bypassed ones keep their ``0.0`` floor, and the aggregator
    can still parse the row instead of choking on missing columns.
    """
    if not _FIXTURE_PROFILE_ENABLED:
        return None
    return {step: 0.0 for step in _FIXTURE_PROFILE_STEPS}


def _profile_write_row(nodeid, timings):
    """Append one CSV row capturing this test's per-step timings.

    No-op when ``timings`` is ``None`` (profiling disabled) or when
    the flag was unset at module load.  Each row carries the full
    column set in ``_FIXTURE_PROFILE_STEPS`` order so the aggregator
    can read it without per-row schema lookups.
    """
    if timings is None or not _FIXTURE_PROFILE_ENABLED:
        return
    with _FIXTURE_PROFILE_CSV.open("a", newline="", encoding="utf-8") as csv_fp:
        writer = csv.writer(csv_fp)
        writer.writerow([
            nodeid,
            _FIXTURE_PROFILE_WORKER_ID,
            *(f"{timings[step]:.4f}" for step in _FIXTURE_PROFILE_STEPS),
        ])


_profile_session_init()


def _profile_step_stats(values):
    """Compute summary statistics for a list of float milliseconds.

    Returns dict with keys ``avg``, ``p50``, ``p95``, ``p99``,
    ``max``.  Uses :func:`statistics.quantiles` with ``n=100`` (the
    "inclusive" method, which linearly interpolates between sample
    values) for percentiles; exact at sample sizes we expect (one
    row per test, dozens to thousands).

    Special cases:

    * Empty list -- returns all zeros so the aggregator can render a
      well-formed row even if a step contributed no samples.
    * Single sample -- ``statistics.quantiles`` rejects ``n < 2``,
      so we short-circuit to the single value for every percentile.
    """
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    if len(values) == 1:
        single = values[0]
        return {
            "avg": single,
            "p50": single,
            "p95": single,
            "p99": single,
            "max": single,
        }

    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return {
        "avg": sum(values) / len(values),
        # quantiles(n=100) returns 99 cut points; index 49 is p50,
        # index 94 is p95, index 98 is p99.
        "p50": cuts[49],
        "p95": cuts[94],
        "p99": cuts[98],
        "max": max(values),
    }


def _profile_load_rows():
    """Read every per-worker CSV in the profile dir into memory.

    Returns ``(rows, workers)`` where ``rows`` is a list of dicts
    keyed by step name with float-millisecond values, and ``workers``
    is the sorted set of worker ids that contributed.  Skips silently
    when the directory is missing (no harness output yet) so the
    aggregator can short-circuit on the empty case.
    """
    rows = []
    workers = set()
    if not _FIXTURE_PROFILE_DIR.exists():
        return rows, workers
    for csv_path in sorted(_FIXTURE_PROFILE_DIR.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8") as csv_fp:
            reader = csv.DictReader(csv_fp)
            for raw in reader:
                workers.add(raw["worker_id"])
                rows.append(
                    {step: float(raw[step]) for step in _FIXTURE_PROFILE_STEPS}
                )
    return rows, sorted(workers)


def _profile_print_summary():
    """Print the per-step summary table to stdout.

    Worker-aware: aggregates across every CSV in
    ``tests/.fixture-profile/``.  Called only on the xdist controller
    or the single-process pytest run -- the two scenarios where
    ``PYTEST_XDIST_WORKER`` is unset and the process has visibility
    into every other worker's output.

    Output shape mirrors ``test-performance-research.md`` section
    3.1 (the "Per-test phase breakdown" table): one row per fixture
    inner step, then a ``Fixture setup total`` line summarising the
    sum-of-setup-steps, then ``call`` and ``teardown`` as informational
    rows (no percent column because they are not part of fixture
    setup cost).  The ``% of fixture`` column is computed relative
    to the average fixture setup total so the percentages sum to
    100 within rounding.
    """
    rows, workers = _profile_load_rows()
    if not rows:
        print()
        print("Fixture profile summary: no rows captured "
              f"(check {_FIXTURE_PROFILE_DIR})")
        print()
        return

    setup_steps = [s for s in _FIXTURE_PROFILE_STEPS if s.startswith("setup_")]
    setup_totals = [sum(row[s] for s in setup_steps) for row in rows]
    setup_avg_total = sum(setup_totals) / len(setup_totals)

    header = ["Step", "Avg", "p50", "p95", "p99", "Max", "% of fixture"]
    widths = [34, 10, 10, 10, 10, 10, 14]
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    fmt = "| " + fmt + " |"
    sep = "|-" + "-|-".join("-" * w for w in widths) + "-|"

    print()
    print("=" * 100)
    print(f"  Fixture profile summary -- {len(rows)} tests across "
          f"{len(workers)} worker(s): {', '.join(workers)}")
    print("=" * 100)
    print(fmt.format(*header))
    print(sep)

    for step in setup_steps:
        stats = _profile_step_stats([row[step] for row in rows])
        pct = (stats["avg"] / setup_avg_total * 100.0) if setup_avg_total else 0.0
        print(fmt.format(
            _FIXTURE_PROFILE_LABELS[step],
            f"{stats['avg']:.1f} ms",
            f"{stats['p50']:.1f}",
            f"{stats['p95']:.1f}",
            f"{stats['p99']:.1f}",
            f"{stats['max']:.1f}",
            f"{pct:.1f} %",
        ))

    setup_stats = _profile_step_stats(setup_totals)
    print(fmt.format(
        "Fixture setup total",
        f"{setup_stats['avg']:.1f} ms",
        f"{setup_stats['p50']:.1f}",
        f"{setup_stats['p95']:.1f}",
        f"{setup_stats['p99']:.1f}",
        f"{setup_stats['max']:.1f}",
        "100.0 %",
    ))
    print(sep)

    for step in ("call", "teardown"):
        stats = _profile_step_stats([row[step] for row in rows])
        print(fmt.format(
            _FIXTURE_PROFILE_LABELS[step],
            f"{stats['avg']:.1f} ms",
            f"{stats['p50']:.1f}",
            f"{stats['p95']:.1f}",
            f"{stats['p99']:.1f}",
            f"{stats['max']:.1f}",
            "--",
        ))
    print()
