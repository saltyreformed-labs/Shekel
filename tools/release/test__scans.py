"""The daily image scan as a release reads it (:mod:`tools.release._scans`; rulings plan#3792,
plan#4027, plan#4028): the newest run of its unit to end, as PID 1 recorded its start job's end
in the journal, refusing the release when it failed, ended more than two days before, or is not
there; and the journal's answer read as data, a record not as PID 1 writes it refused whole."""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from tools.release import _scans
from tools.release._model import ReleaseError
from tools.release._scans import DONE, READ, START, STALE_AFTER, UNIT, Ran, read
from tools.release.conftest import answering, scan_ended, scan_started

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _ago(**delta) -> datetime:
    """The instant ``delta`` before :data:`NOW`."""
    return NOW - timedelta(**delta)


def test_a_run_done_an_hour_ago_refuses_nothing_and_is_read_as_pid_1_wrote_it():
    """Its start's record holds no result and is no run; its end's is the run, to the
    microsecond, with its invocation id."""
    finished = _ago(hours=1) + timedelta(microseconds=7)
    scan = read(NOW, answering(scan_started(_ago(hours=1, minutes=1), 5),
                               scan_ended(finished, invocation=5)))
    assert scan.newest == Ran(finished, DONE, f"{5:032x}")
    assert not scan.refusals


def test_the_newest_run_is_the_one_that_ended_last_wherever_the_journal_lists_it():
    """Read newest by when each ended, not by the answer's order: yesterday's done run, listed
    after today's failed one, does not hide it."""
    scan = read(NOW, answering(scan_ended(_ago(hours=2), "failed", 2),
                               scan_ended(_ago(days=1), DONE, 1)))
    assert (scan.newest.result, len(scan.refusals)) == ("failed", 1)


def test_a_run_still_going_has_no_end_so_the_run_before_it_is_judged():
    """Today's run has started and not ended: the failed run before it still refuses."""
    scan = read(NOW, answering(scan_ended(_ago(days=1), "failed", 1),
                               scan_started(_ago(minutes=5), 2)))
    assert scan.newest.invocation == f"{1:032x}"
    assert len(scan.refusals) == 1


def test_a_failed_newest_run_refuses_though_one_before_it_was_done():
    """The newest run failed an hour ago, the one before it was done: refused, naming the
    run's own log and how to start one."""
    scan = read(NOW, answering(scan_ended(_ago(days=1), DONE, 1),
                               scan_ended(_ago(hours=1), "failed", 2)))
    assert scan.refusals == (
        "the newest image scan, ended 2026-11-13 11:00 UTC, failed: read why with "
        f"`journalctl _SYSTEMD_INVOCATION_ID={2:032x}`, and once that is fixed `{START}` "
        "starts one",)


@pytest.mark.parametrize("result", ["canceled", "timeout", "dependency", "skipped"])
def test_a_run_ending_any_way_but_done_is_failed(result):
    """Whatever systemd names a result other than done, the run is no scan that ran."""
    scan = read(NOW, answering(scan_ended(_ago(hours=1), result)))
    assert len(scan.refusals) == 1 and f", {result}: read why" in scan.refusals[0]


def test_a_run_done_exactly_two_days_ago_is_not_more_than_two_days_old():
    """The refusal is for a run that ended MORE than two days before (ruling plan#4028)."""
    assert not read(NOW, answering(scan_ended(NOW - STALE_AFTER))).refusals


def test_a_run_done_two_days_and_a_microsecond_ago_refuses_as_a_stopped_timer():
    """One microsecond past the window: refused, saying the timer stopped and how to start
    a run."""
    scan = read(NOW, answering(scan_ended(NOW - STALE_AFTER - timedelta(microseconds=1))))
    assert scan.refusals == (
        "the newest image scan ended 2026-11-11 11:59 UTC, more than 2 days before "
        "2026-11-13 12:00 UTC, so its timer has stopped: `systemctl status "
        f"shekel-image-scan.timer` says why, and `{START}` starts one",)


def test_a_failed_run_past_the_window_refuses_for_both():
    """The newest run failed nine days ago and none ran since: both reasons hold."""
    refusals = read(NOW, answering(scan_ended(_ago(days=9), "failed"))).refusals
    assert [reason.split(",", maxsplit=1)[0] for reason in refusals] == [
        "the newest image scan", "the newest image scan ended 2026-11-04 12:00 UTC"]


@pytest.mark.parametrize("answer", ["", json.dumps(scan_started(NOW, 1)) + "\n"])
def test_no_run_on_record_refuses_and_says_how_to_start_one(answer):
    """No record at all, or a start with no end yet: refused, never passed by default."""
    scan = read(NOW, lambda: answer)
    assert scan.newest is None
    assert scan.refusals == (
        f"no run of the image scan ({UNIT}) is in the journal this user reads, so its timer "
        f"is not installed or has never run: `{START}` starts one",)


def test_no_way_to_read_the_journal_judges_nothing():
    """Every release reads the scan, so a caller giving no reader is stopped."""
    with pytest.raises(ReleaseError, match="no way to read it was given"):
        read(NOW, None)


@pytest.mark.parametrize("line", ["not json", "[1, 2]", '"a string"'])
def test_a_line_that_is_no_json_record_is_refused(line):
    """journalctl writes one JSON object a line; anything else is no answer to read."""
    with pytest.raises(ReleaseError, match="no JSON record"):
        read(NOW, lambda: line + "\n")


@pytest.mark.parametrize(("field", "value"), [
    ("__REALTIME_TIMESTAMP", None), ("__REALTIME_TIMESTAMP", "-5"),
    ("__REALTIME_TIMESTAMP", ["1", "2"]), ("JOB_RESULT", "Done"), ("JOB_RESULT", ""),
    ("JOB_RESULT", [104, 105]), ("INVOCATION_ID", None), ("INVOCATION_ID", "ab"),
    ("INVOCATION_ID", "`@someone`" + "0" * 22),
])
def test_a_run_s_record_not_as_pid_1_writes_it_is_refused_whole(field, value):
    """A field missing, repeated (journalctl writes a list), binary (a list of bytes) or not
    of its shape: the release is judged on nothing rather than on a guess, and nothing it
    held is printed into a refusal unchecked."""
    record = scan_ended(_ago(hours=1))
    if value is None:
        del record[field]
    else:
        record[field] = value
    with pytest.raises(ReleaseError, match=f"holds {field} as "):
        read(NOW, answering(scan_ended(_ago(days=1), invocation=2), record))


def test_the_read_names_pid_1_the_unit_and_its_start_jobs_as_json():
    """The matches that make a record PID 1's own, of this unit's runs: ``_PID`` is the field
    journald adds itself, so no other process's line passes for a run."""
    assert READ[0] == "journalctl"
    assert {"--output=json", "--no-pager", "_PID=1", f"UNIT={UNIT}", "JOB_TYPE=start"} == set(
        READ[1:])


def test_the_journal_is_journalctl_s_answer_to_the_read(monkeypatch):
    """The command line's reader runs :data:`READ` and fails on journalctl's failure."""
    seen = {}

    def ran(argv, **options):
        seen.update(argv=argv, **options)
        return subprocess.CompletedProcess(argv, 0, stdout="answer\n", stderr="")

    monkeypatch.setattr(_scans.subprocess, "run", ran)
    assert _scans.journal() == "answer\n"
    assert seen == {"argv": READ, "capture_output": True, "text": True, "check": True}
