"""Architecture test: pytest-xdist runs an ``xdist_group`` on one worker, every run.

Plan step **plan#2954**, ruling **plan#3624**.  The suite keeps its tests that
change a table's definition or a cluster-wide role (the ``shekel_app`` role)
away from each other with ``@pytest.mark.xdist_group``, which ``--dist=loadgroup``
(``pytest.ini``'s ``addopts``) turns into "run these on one worker".  pytest
9.2.0 broke that with pytest-xdist 3.8.0 and said nothing: 9.2.0's
``Node.nodeid`` no longer reads the ``_nodeid`` xdist 3.8.0 writes the group's
name into, so every group scattered across the workers, and the suite went red
only when the scheduler happened to split a group whose tests collide
(``test_zz_it_was_built_once_for_all_of_them``, and the ``shekel_app`` role race
in ``tests/test_models/test_audit_migration.py``).  A break of that kind must
fail every run, never by scheduling luck.

**It grades the outcome, never xdist's mechanism.**  The test runs a probe suite
of its own in a subprocess, on the interpreter and packages this suite runs on,
and reads which worker each probe test ran on.  It never reads the ``@group``
suffix xdist 3.8.0 puts on a node id: xdist's fix (pytest-dev/pytest-xdist#1388,
merged 2026-10-07, unreleased on 2026-11-12) sends each group's name apart from
the node id, so a test reading the suffix would call the fixed release broken.

**Why the outcome is the same every run.**  The probe is two tests in one group
and one test in none, run with ``-n 2 --dist=loadgroup`` and no plugin but
xdist's.  xdist's scope scheduler (``xdist/scheduler/loadscope.py``,
``LoadScopeScheduling.schedule``, which loadgroup inherits) makes one work unit
of each scope, and gives each worker one unit before any gets a second:

- **a group xdist reads** is one unit, so the units are the group and the lone
  test, and the two workers take one each: the group's two tests run on one
  worker, the lone test on the other;
- **a group xdist no longer reads** is two units of one test each, the first two
  units in the order collected, so its two tests run on two workers.

Measured 2026-11-12, ten probe runs on each pair, every run alike: pytest 9.1.1
with xdist 3.8.0 (the lock's) ran the group on ``gw0`` and the lone test on
``gw1``; pytest 9.2.0 with xdist 3.8.0 ran the group's two tests on ``gw0`` and
``gw1``; pytest 9.2.0 with xdist's master (``3.8.1.dev112+g1710344f1``) ran the
group on ``gw0`` and the lone test on ``gw1``.  The lone test is the control:
its worker differs from the group's only when two workers were live, so a probe
that ran on one worker cannot pass as a group kept whole.

What it does not cover: that the suite's own runs pass ``--dist=loadgroup``
(``pytest.ini`` and ``ci.yml``'s shard step say so; ``tests/_shard.py`` keeps a
group whole across CI's shards and ``test_the_shards_partition_the_suite.py``
grades that).
"""

import os
import subprocess
import sys
from pathlib import Path

#: The probe suite: two tests in one group and one in none, each writing the
#: worker it ran on into a file named for it, beside the probe.
_PROBE = '''\
"""The probe suite of tests/test_arch/test_an_xdist_group_runs_on_one_worker.py."""
import os
from pathlib import Path

import pytest


def _ran(name):
    """Write the worker this test ran on into a file named for it."""
    (Path(__file__).parent / f"{name}.worker").write_text(os.environ["PYTEST_XDIST_WORKER"])


@pytest.mark.xdist_group("probe")
def test_first():
    """The group's first test."""
    _ran("first")


@pytest.mark.xdist_group("probe")
def test_second():
    """The group's second test."""
    _ran("second")


def test_alone():
    """The control, in no group."""
    _ran("alone")
'''

#: How long the probe may run before it counts as hung: below the 50 s
#: ``timeout`` of ``pytest.ini``, so a hang fails here with the probe's own
#: output rather than as a bare timeout.  It ran in 0.12 - 0.20 s (2026-11-12).
_PROBE_SECONDS = 40

#: The variables the probe inherits from this run: what finds the interpreter's
#: tools, and no ``PYTEST_*`` or coverage variable of this run's, so the probe
#: runs on the installed packages alone.
_KEPT = ("PATH", "HOME", "TMPDIR")


def _probe(directory: Path) -> subprocess.CompletedProcess:
    """Run :data:`_PROBE` in ``directory`` under ``-n 2 --dist=loadgroup``, on
    this interpreter, with only xdist's plugin loaded and no ini file of the
    suite's: what pytest printed, and its exit status.

    Args:
        directory: An empty directory the probe and its worker files go in.

    Returns:
        The finished ``python -m pytest`` run.
    """
    (directory / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (directory / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    env = {name: os.environ[name] for name in _KEPT if name in os.environ}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "xdist.plugin", "-p", "no:cacheprovider",
         "-n", "2", "--dist=loadgroup", "-c", str(directory / "pytest.ini"),
         "--rootdir", str(directory), "-q", str(directory / "test_probe.py")],
        cwd=directory, env=env, capture_output=True, text=True, check=False,
        timeout=_PROBE_SECONDS,
    )


def test_an_xdist_group_runs_on_one_worker(tmp_path):
    """The probe's group runs on one worker and its lone test on the other.

    A split group is the defect plan#2954 found: the installed pytest and
    pytest-xdist no longer keep a group's tests together, and every
    ``xdist_group`` in the suite is then a race.
    """
    done = _probe(tmp_path)
    assert done.returncode == 0, (
        f"the probe suite failed (exit {done.returncode}):\n{done.stdout}{done.stderr}")
    workers = {name: (tmp_path / f"{name}.worker").read_text(encoding="utf-8")
               for name in ("first", "second", "alone")}
    assert workers["first"] == workers["second"], (
        f"pytest-xdist split one xdist_group across workers {workers['first']} and "
        f"{workers['second']}: the installed pytest and pytest-xdist no longer keep a group "
        "on one worker, so every xdist_group in the suite is a race (plan#2954: pytest "
        "9.2.0 with pytest-xdist 3.8.0)")
    assert workers["alone"] != workers["first"], (
        f"the probe's lone test ran on {workers['alone']}, the group's worker: two workers "
        "were not live, so the probe graded nothing")
