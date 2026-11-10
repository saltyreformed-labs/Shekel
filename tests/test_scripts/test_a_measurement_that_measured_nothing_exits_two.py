"""A production-dump measurement that measured nothing exits 2, never 1 (finding plan#2826).

Exit 1 from any of these says figures part, a question for the developer; 2 says nothing was
measured.  Python's own status for an uncaught exception is 1, so before
``tests/manual/exit_status.py`` a dump older than head's migrations, which raises in
``ref_cache.init`` before a row is read, ran ``measure_worked_out_read.py`` to exit 1, a count
that moved (measured 2026-11-04, ``PRE_FICA_DEDUCTION``).

**Each script runs as a child, under the production config**, because that is the only config in
which the measured failure exists: the development and testing configs seed the ref tables
inside ``create_app`` before the cache is read.  The child is pointed at this test's own
database, as ``test_seed_ref_tables`` points its child.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
from sqlalchemy import text

from tests.test_scripts._production_child import production_env

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANUAL = _REPO_ROOT / "tests" / "manual"
_PROBE = _MANUAL / "measure_worked_out_read.py"
# ``verify_worked_out_ledger.py`` read a copy too, and
# ``run_worked_out_ledger_on_dump.py`` ran it, until plan task plan#3077 deleted
# both: the harness checked the store the deploy's cash resync had just
# brought to target, and the resync went with the deploy's hooks.
_READING_A_COPY = (_PROBE,)
# ``run_deploy_rewrites_on_dump.py`` was a runner too, until plan task
# plan#3080 deleted it with the stored ledger it diffed.
_EVERY_MEASUREMENT = (
    *_READING_A_COPY,
    _MANUAL / "run_statements_on_dump.py",
)
# Where Python's own printing of an uncaught exception opens.
_A_CRASH = "Traceback (most recent call last)"
# No child reads this: each that is handed it ends before it connects.
_NO_DATABASE = "postgresql://nowhere.invalid/none"


def _run(argv, env):
    """Run *argv* as a child from the repository root; the finished process."""
    return subprocess.run(
        argv, env=env, cwd=_REPO_ROOT, capture_output=True, text=True,
        timeout=25, check=False,
    )


def _read_copy(script, db, report):
    """Run *script* on this test's database, writing *report*; the finished process."""
    return _run(
        [sys.executable, str(script), "--report", str(report)],
        production_env(db.engine.url.render_as_string(hide_password=False)),
    )


def _by_name(path):
    """Return a script's name, as a parametrised test's id."""
    return path.stem


class TestACopyThatCannotBeRead:
    """A copy the run cannot read is nothing measured, whichever way it fails."""

    @pytest.mark.parametrize("script", _READING_A_COPY, ids=_by_name)
    def test_a_copy_missing_a_ref_row_cannot_start(self, script, db, tmp_path):
        """The measured failure: ``ref_cache.init`` refuses, and it exits 2 saying which.

        ``ref.statement_sources`` 'secu_checking_csv' stands in for the row a dump older than
        head's migrations lacks: it backs an enum member and nothing in a per-test database
        references it.  No report is left: the run never opened one.
        """
        db.session.execute(text(
            "DELETE FROM ref.statement_sources WHERE name = 'secu_checking_csv'"
        ))
        db.session.commit()
        report = tmp_path / "report.txt"

        result = _read_copy(script, db, report)

        assert result.returncode == 2, result.stdout + result.stderr
        assert "StatementSource.SECU_CHECKING_CSV" in result.stderr
        assert not report.exists()

    @pytest.mark.parametrize("script", _READING_A_COPY, ids=_by_name)
    def test_a_crash_part_way_exits_two(self, script, db, tmp_path):
        """A table gone once the app is up exits 2, with its report opened.

        ``create_app`` reads no scenario, so the run starts and its report opens, which is
        what parts this failure from the one above; every run reads the scenarios.
        """
        db.session.execute(text(
            "ALTER TABLE budget.scenarios RENAME TO scenarios_gone"
        ))
        db.session.commit()
        report = tmp_path / "report.txt"

        result = _read_copy(script, db, report)

        assert result.returncode == 2, result.stdout + result.stderr
        assert _A_CRASH in result.stderr
        assert "budget.scenarios" in result.stderr
        assert report.exists()

    @pytest.mark.parametrize("script", _READING_A_COPY, ids=_by_name)
    def test_a_report_already_there_cannot_start(self, script, db, tmp_path):
        """A report that exists is refused with exit 2, and left as it was."""
        report = tmp_path / "report.txt"
        report.write_text("an earlier run's\n", encoding="utf-8")

        result = _read_copy(script, db, report)

        assert result.returncode == 2, result.stdout + result.stderr
        assert "FileExistsError" in result.stderr
        assert report.read_text(encoding="utf-8") == "an earlier run's\n"


class TestACopyThatCanBeRead:
    """The negative controls: the plants above are what ended those runs."""

    def test_the_probe_on_a_copy_with_no_owner_exits_zero(self, db, tmp_path):
        """With nothing planted it starts, reads every owner (none) and exits 0."""
        report = tmp_path / "report.txt"

        result = _read_copy(_PROBE, db, report)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _A_CRASH not in result.stderr
        assert report.exists()


class TestEveryMeasurementIsArmedBeforeItsImports:
    """Each script exits 2 even when an import of its own is what fails."""

    @pytest.mark.parametrize("script", _EVERY_MEASUREMENT, ids=_by_name)
    def test_an_import_that_fails_exits_two(self, script, tmp_path):
        """A module that will not import ends it with 2, the module's message on stderr.

        Argparse's own usage error is exit 2 too, so the planted message is what grades it:
        ``time_machine`` is what the probe that reads a copy meets first, ``psycopg`` the runner.
        """
        for module in ("time_machine", "psycopg"):
            (tmp_path / f"{module}.py").write_text(
                f"raise ImportError('planted in {module}')\n", encoding="utf-8",
            )

        result = _run(
            [sys.executable, str(script), "--help"],
            {**production_env(_NO_DATABASE), "PYTHONPATH": str(tmp_path)},
        )

        assert result.returncode == 2, result.stdout + result.stderr
        assert "planted in " in result.stderr

    @pytest.mark.parametrize("script", _EVERY_MEASUREMENT, ids=_by_name)
    def test_with_nothing_planted_it_reaches_its_parser(self, script):
        """The negative control: the same command, nothing planted, prints usage and exits 0."""
        result = _run(
            [sys.executable, str(script), "--help"],
            production_env(_NO_DATABASE),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "usage:" in result.stdout


class TestTheHook:
    """What arming changes: the status of a crash, and nothing else."""

    @staticmethod
    def _script(name, body):
        """Run a child that arms under the name *name*, then runs *body*."""
        return _run(
            [sys.executable, "-c",
             "import sys\n"
             "from tests.manual import exit_status\n"
             f"exit_status.arm({name!r})\n"
             "print('before', end='')\n"
             f"{body}\n"],
            production_env(_NO_DATABASE),
        )

    def test_python_itself_exits_one_on_a_crash(self):
        """Unarmed, as a script imported under its module name is: the collision itself."""
        result = self._script("tests.manual.a_sibling", "raise TypeError('planted')")

        assert result.returncode == 1, result.stdout + result.stderr
        assert "TypeError: planted" in result.stderr

    def test_an_armed_crash_exits_two_and_loses_nothing_it_said(self):
        """Armed: exit 2, the traceback as Python prints it, what it printed flushed."""
        result = self._script("__main__", "raise TypeError('planted')")

        assert result.returncode == 2, result.stdout + result.stderr
        assert _A_CRASH in result.stderr
        assert "TypeError: planted" in result.stderr
        assert result.stdout == "before"

    @pytest.mark.parametrize("status", (0, 1, 2))
    def test_a_status_the_script_returns_is_its_own(self, status):
        """Armed, a verdict's ``sys.exit`` passes untouched: a real exit 1 is still 1."""
        result = self._script("__main__", f"sys.exit({status})")

        assert result.returncode == status, result.stdout + result.stderr
        assert _A_CRASH not in result.stderr
