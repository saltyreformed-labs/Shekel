"""Whether the daily image scan still runs (plan#2444; rulings plan#3792, plan#4027, plan#4028):
a release is refused while the scan's newest run failed or finished more than
:data:`STALE_AFTER` ago, so a timer that stopped quietly surfaces at the next release.  A failed
run pushes an alert and files a card of its own (rulings plan#3969, plan#3788); a run that never
happens does neither, and a release is what notices it.

**The record is systemd's** (ruling plan#4027): the scan's unit (:data:`UNIT`) runs on this
host, from ``shekel-image-scan.timer``, and leaves nothing on GitHub (ruling plan#3785), while a
clean scan files nothing in the tracker.  What records every run is the journal: PID 1 writes the
end of each run's start job, its ``JOB_RESULT`` (``done``, or how it failed: ``failed`` for an
exit other than 0), at the instant it ended, with the run's ``INVOCATION_ID`` (measured
2026-11-13 on ``shekel-plan-sync.service``'s 145 runs: one end record each, 141 ``done`` and 4
``failed``, beside each start's record, which holds no result).  The release reads those
records (:data:`READ`) and stores nothing.  ``systemctl show``'s last result is no record: it
lives in PID 1's memory and is gone at a reboot.  The journal is kept on disk across boots
(``/var/log/journal``, 30 days on this host), and the matches name PID 1 by ``_PID``, a field
journald adds itself and no client can write (``systemd.journal-fields(7)``), so no other
process's line can pass for a run.

**What counts.**  ``quill scan`` exits 0 on a clean scan and on a red one whose cards it filed,
and the unit is ``Type=oneshot``, whose start job ends only when its process does, so a run
``done`` is a scan that ran; a unit of another type ends its start job ``done`` as its process
starts, which would read every run as done whatever the scan did: this reading rests on that
type, as the unit's source says beside it.  A run whose job ended any other way, whatever
systemd names the result, is failed until a later run is done.  The newest run by when it
ended is the one judged: a run still going has no end yet, so the run before it is judged.
None on record refuses the release too, saying how to start one: the timer not installed
(plan#3971 installs it), or this user unable to read the system's journal, whose runs it then
cannot see.  A record not as PID 1 writes it is refused whole
(:class:`~tools.release._model.ReleaseError`), and journalctl failing judges nothing: the
release's exit 2, never a pass.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from operator import attrgetter

from tools.release._model import ReleaseError

#: The scan's unit, as its source in ``/opt/docker/scripts`` names it (ruling plan#3963).
UNIT = "shekel-image-scan.service"
#: The timer that runs it daily (ruling plan#3820).
TIMER = "shekel-image-scan.timer"
#: How long ago the newest run may have ended before a release is refused: the scan's day and a
#: day of slack (ruling plan#4028), as ruling plan#3792's 8 days were a week and a day.
STALE_AFTER = timedelta(days=2)
#: The result of a start job that ended as asked: the unit's process exited 0.
DONE = "done"
#: What starts a run by hand.
START = f"sudo systemctl start {UNIT}"
#: The journal's records of the ends of the unit's start jobs, as PID 1 writes them, one JSON
#: object a line; each start's own record is read too, and holds no ``JOB_RESULT``.
READ = ("journalctl", "--output=json", "--no-pager", "_PID=1", f"UNIT={UNIT}",
        "JOB_TYPE=start")
#: What each field read of a record holds, as PID 1 writes it: the instant in microseconds since
#: the epoch, a job's result as one of systemd's lowercase words, a run's 128-bit id in hex.
_FIELDS = {"__REALTIME_TIMESTAMP": re.compile(r"[0-9]+"), "JOB_RESULT": re.compile(r"[a-z]+"),
           "INVOCATION_ID": re.compile(r"[0-9a-f]{32}")}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Ran:
    """One run of the scan's unit, as PID 1 recorded its start job's end: when it ended, its
    result (:data:`DONE`, or how it failed) and the run's invocation id."""

    finished: datetime
    result: str
    invocation: str


@dataclass(frozen=True)
class Scan:
    """The image scan as a release reads it at ``now``: its newest run to end, None when the
    journal holds none."""

    now: datetime
    newest: Ran | None

    @property
    def refusals(self) -> tuple[str, ...]:
        """Why the scan refuses the release: none, or that no run is on record, or that the
        newest failed, or ended more than :data:`STALE_AFTER` before ``now``, or both."""
        if self.newest is None:
            return (f"no run of the image scan ({UNIT}) is in the journal this user reads, so "
                    f"its timer is not installed or has never run: `{START}` starts one",)
        at = f"{self.newest.finished:%Y-%m-%d %H:%M} UTC"
        reasons = []
        if self.newest.result != DONE:
            reasons.append(f"the newest image scan, ended {at}, {self.newest.result}: read why "
                           f"with `journalctl _SYSTEMD_INVOCATION_ID={self.newest.invocation}`, "
                           f"and once that is fixed `{START}` starts one")
        if self.newest.finished < self.now - STALE_AFTER:
            reasons.append(f"the newest image scan ended {at}, more than {STALE_AFTER.days} "
                           f"days before {self.now:%Y-%m-%d %H:%M} UTC, so its timer has "
                           f"stopped: `systemctl status {TIMER}` says why, and `{START}` "
                           "starts one")
        return tuple(reasons)


def journal() -> str:
    """The journal's answer to :data:`READ`, on this host.

    Raises:
        subprocess.CalledProcessError: when journalctl exits non-zero.
        OSError: when it cannot be run.
    """
    return subprocess.run(READ, capture_output=True, text=True, check=True).stdout


def runs(answer: str) -> list[Ran]:
    """Each run whose end the journal's ``answer`` to :data:`READ` records.

    Raises:
        ReleaseError: when a line is no JSON object, or a record of a run's end does not hold
            each field read as PID 1 writes it.
    """
    found = []
    for line in answer.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReleaseError(f"the journal's answer holds a line that is no JSON record "
                               f"({error}): {line[:200]!r}") from error
        if not isinstance(record, dict):
            raise ReleaseError(f"the journal's answer holds a line that is no JSON record: "
                               f"{line[:200]!r}")
        if "JOB_RESULT" in record:
            found.append(_ran(record))
    return found


def _ran(record: Mapping[str, object]) -> Ran:
    """The run whose end ``record`` is.

    Raises:
        ReleaseError: when a field read is missing or not as PID 1 writes it.
    """
    held = {}
    for field, shape in _FIELDS.items():
        value = record.get(field)
        if not isinstance(value, str) or not shape.fullmatch(value):
            raise ReleaseError(f"the journal's record of a run of {UNIT} holds {field} as "
                               f"{value!r}, which PID 1 does not write")
        held[field] = value
    return Ran(_EPOCH + timedelta(microseconds=int(held["__REALTIME_TIMESTAMP"])),
               held["JOB_RESULT"], held["INVOCATION_ID"])


def read(now: datetime, read_journal: Callable[[], str] | None) -> Scan:
    """The image scan at ``now``, as the journal ``read_journal`` answers says.

    Raises:
        ReleaseError: when no way to read the journal was given, or a record is refused
            (:func:`runs`).
        subprocess.CalledProcessError, OSError: when the journal cannot be read.
    """
    if read_journal is None:
        raise ReleaseError("a release reads the image scan's runs from this host's journal "
                           "(ruling plan#4027), and no way to read it was given")
    return Scan(now, max(runs(read_journal()), key=attrgetter("finished"), default=None))
