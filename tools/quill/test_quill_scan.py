"""``quill scan`` (:mod:`tools.quill._scan`; rulings plan#3787, plan#3788, plan#3825): the door
that runs the daily scan of the image production is running, says what it found, and files it
into the in-memory tracker (:class:`_fake.FakeTracker`): one open card for each component, a
later CVE put on its open card, and a failure filed once while its card is open.  The scan
itself is :func:`tools.update.scan.scanned`'s, tested in ``tools/update/test_scan.py``; here it
is stood in for, so the door is tested apart from docker.  Every CVE here is invented (ruling
plan#3841)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

import pytest

from tools.ci.arcs import REPO
from tools.quill import _scan, _waits, quill
from tools.quill._fake import FakeTracker, assert_filed_high
from tools.quill._github import GitHubError
from tools.update import app, scan

#: When the stand-in scan runs, and as the door's lines say it.
_AT = datetime(2026, 11, 17, 11, 0, tzinfo=UTC)
_WHEN = "2026-11-17T11:00:00Z"
#: The scanned image's digest and the commit it was built from.
_IMAGE = "sha256:" + "4f19" * 16
_REVISION = "0123456789abcdef" * 2 + "01234567"
#: Where a card for the interpreter's CVEs says they were found, and its title.
_PYTHON = "binary package python"
_PYTHON_TITLE = "Production image CVEs: binary package python"
#: The failure's card title.
_FAILED_TITLE = "The daily scan of production's image failed"


def _python(number, rating="high"):
    """An invented CVE in the interpreter, ``CVE-2000-0<number>``, fixed in 3.1.5."""
    return scan.Counted(f"CVE-2000-0{number}", "binary", "python", "3.1.4", ("3.1.5",),
                        scan.RATINGS.index(rating), False)


#: Seven CVEs in the interpreter, as the judge orders them: one CRITICAL, then six HIGH.
_SEVEN = (_python(101, "critical"), *(_python(number) for number in range(102, 108)))
#: A CVE in a Debian package, known exploited, with no fix.
_DEB = scan.Counted("CVE-2000-0201", "deb", "libexample1", "1.0-1", (),
                    scan.RATINGS.index("medium"), True)


def _done(*found):
    """The stand-in scan's answer: production's image, its commit, and ``found``."""
    return scan.Scanned(_IMAGE, _REVISION, found)


def _never():
    """A ``connect`` that fails the test: a clean scan has nothing to file, and reads nothing
    of the tracker."""
    raise AssertionError("scan connected to the tracker")


def _ran(monkeypatch, tracker, outcome, *argv, at=_AT):
    """``quill scan`` run with ``argv``, reaching ``tracker`` (a ``connect`` for one that is
    not a tracker), its scan stood in for, answering ``outcome`` or raising it, its clock
    reading ``at``; its exit status."""
    def scanned(root):
        """The stand-in scan, run from the checkout's root."""
        assert root == REPO
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(scan, "scanned", scanned)
    monkeypatch.setitem(quill.CONNECTS_LATE, "scan", partial(_scan.cmd_scan, clock=lambda: at))
    connect = tracker if callable(tracker) else lambda: tracker
    return quill.main(["scan", *argv], connect=connect, root=REPO)


def _filed(number, title, where, ruling, at=_WHEN):
    """What the door prints for a card it files: its own line, then the filing's."""
    return (f"file the scan's {where}, found at {at}, which no card holds (ruling {ruling}): "
            f"{title!r}\nfiled plan#{number}\n  plan#{number} [step, tooling] {title}\n"
            f"  board: put at the top (ruling plan#3620)\n"
            f"  unmarked: plan#{number}'s filing is finished\n")


def _lines(where, *found):
    """The lines a card or quill's comment on one holds for ``found``, found at ``where``:
    spelled here apart from :func:`_waits.told_line`, so a test pins their form."""
    return [f"<!-- quill: told on {where} --> {item.marker}" for item in found]


def test_seven_cves_in_the_interpreter_file_one_card_and_the_next_day_nothing(monkeypatch,
                                                                             capsys):
    """Seven CVEs in the interpreter, found by one scan, file one step in the tooling arc
    holding Priority High and no Effort, at the top of the board, listing each in a code block
    and holding a line for each; the next day's scan, finding the same seven, files nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    assert _ran(monkeypatch, tracker, _done(*_SEVEN)) == 0
    assert capsys.readouterr().out == (
        f"scan: production's image {_IMAGE}, built from {_REVISION}\n"
        "scan: CVEs with a fix rated HIGH or CRITICAL, or known exploited: 7\n"
        "  CVE-2000-0101, rated critical: binary python 3.1.4, fixed in 3.1.5\n"
        + "".join(f"  CVE-2000-0{number}, rated high: binary python 3.1.4, fixed in 3.1.5\n"
                  for number in range(102, 108))
        + _filed(2, _PYTHON_TITLE, _PYTHON, "plan#3787"))
    assert_filed_high(tracker, 2, _PYTHON_TITLE)
    assert tracker.board.items == [2, 1]
    assert tracker.bodies[2] == "\n".join([
        f"The daily scan of the image production is running found these CVEs in its {_PYTHON}, "
        f"at {_WHEN}, in the image `{_IMAGE}`, built from `{_REVISION}`: each has a fix rated "
        "HIGH or CRITICAL by some source, or is known to be exploited (rulings plan#3720, "
        "plan#3819).", "", "```text",
        "CVE-2000-0101, rated critical: binary python 3.1.4, fixed in 3.1.5",
        *(f"CVE-2000-0{number}, rated high: binary python 3.1.4, fixed in 3.1.5"
          for number in range(102, 108)), "```", "",
        "`quill scan` filed this card for that package (ruling plan#3787), holding Priority High "
        "at the top of the board (ruling plan#3620). A CVE the scan finds in it later is put on "
        "this card while it is open, and none the card holds is filed again. The session that "
        "claims this card takes the fix, and sets the card's Effort.", "",
        *_lines(_PYTHON, *_SEVEN)])
    tracker.writes.clear()
    assert _ran(monkeypatch, tracker, _done(*_SEVEN), at=_AT + timedelta(days=1)) == 0
    assert "file" not in capsys.readouterr().out
    assert not tracker.writes


def test_a_new_cve_in_the_interpreter_goes_on_its_open_card_and_is_held_there(monkeypatch,
                                                                             capsys):
    """The interpreter's card open holding six of the seven: the seventh, found the next day
    beside them, is put on that card in a comment of quill's holding its line alone, and the
    day after, held in that comment, files nothing (ruling plan#3787)."""
    tracker = FakeTracker()
    tracker.add(5, body="\n".join(_lines(_PYTHON, *_SEVEN[:6])))
    assert _ran(monkeypatch, tracker, _done(*_SEVEN)) == 0
    assert capsys.readouterr().out.endswith(
        f"put the scan's {_PYTHON}, found at {_WHEN}, on plan#5, the card open for it (ruling "
        "plan#3787)\n")
    assert [write[:2] for write in tracker.writes] == [("comment", 5)]
    assert tracker.writes[0][2] == "\n".join([
        f"The daily scan found these CVEs later in production's {_PYTHON}, at {_WHEN}, in the "
        f"image `{_IMAGE}`, built from `{_REVISION}`:", "", "```text",
        "CVE-2000-0107, rated high: binary python 3.1.4, fixed in 3.1.5", "```", "",
        "`quill scan` put them on this card, the one open for that package, rather than filing "
        "another (ruling plan#3787).", "", *_lines(_PYTHON, _SEVEN[6])])
    tracker.writes.clear()
    assert _ran(monkeypatch, tracker, _done(*_SEVEN), at=_AT + timedelta(days=1)) == 0
    assert not tracker.writes


@pytest.mark.parametrize("reason", ["NOT_PLANNED", "COMPLETED"])
def test_a_closed_card_keeps_its_cves_quiet_and_a_new_one_files_a_card_alone(reason,
                                                                             monkeypatch, capsys):
    """The interpreter's card closed, dropped or shipped, holding six of the seven: those six
    file nothing again (ruling plan#3790), and the seventh is filed a card of its own listing
    it alone (ruling plan#3787)."""
    tracker = FakeTracker()
    tracker.add(5, body="\n".join(_lines(_PYTHON, *_SEVEN[:6])), is_open=False,
                state_reason=reason)
    assert _ran(monkeypatch, tracker, _done(*_SEVEN)) == 0
    assert capsys.readouterr().out.endswith(_filed(6, _PYTHON_TITLE, _PYTHON, "plan#3787"))
    assert _waits.holds(tracker.bodies[6]) == {(_PYTHON, _SEVEN[6].marker)}
    assert "CVE-2000-0101" not in tracker.bodies[6]
    assert [write[0] for write in tracker.writes].count("comment") == 0


def test_each_component_files_a_card_the_most_severe_on_top(monkeypatch, capsys):
    """A CVE in a Debian package beside the interpreter's seven: a card for each component,
    the Debian package's filed first and the interpreter's, holding the CRITICAL, last, so it
    is the board's top; each holds its own lines alone."""
    tracker = FakeTracker()
    assert _ran(monkeypatch, tracker, _done(*_SEVEN, _DEB)) == 0
    out = capsys.readouterr().out
    deb = "deb package libexample1"
    assert out.endswith(_filed(1, "Production image CVEs: " + deb, deb, "plan#3787")
                        + _filed(2, _PYTHON_TITLE, _PYTHON, "plan#3787"))
    assert tracker.board.items == [2, 1]
    assert _waits.holds(tracker.bodies[1]) == {(deb, _DEB.marker)}
    assert ("CVE-2000-0201, rated medium, known exploited: deb libexample1 1.0-1, no fix known"
            in tracker.bodies[1])
    assert _waits.holds(tracker.bodies[2]) == {(_PYTHON, item.marker) for item in _SEVEN}


def test_a_dry_run_says_what_it_would_file_and_writes_nothing(monkeypatch, capsys):
    """Under ``--dry-run`` the door reads the tracker and says the card it would file, and the
    comment it would put on it, and writes neither."""
    tracker = FakeTracker()
    assert _ran(monkeypatch, tracker, _done(*_SEVEN), "--dry-run") == 0
    assert capsys.readouterr().out.endswith(
        f"would file the scan's {_PYTHON}, found at {_WHEN}, which no card holds (ruling "
        f"plan#3787): {_PYTHON_TITLE!r}\n")
    assert not tracker.writes
    assert _waits.every_text(tracker.place) in tracker.github.queries


def test_a_clean_scan_says_none_count_and_reads_nothing_of_the_tracker(monkeypatch, capsys):
    """A scan in which no match counts says so, exit 0, and never connects to the tracker."""
    assert _ran(monkeypatch, _never, _done()) == 0
    assert capsys.readouterr().out.endswith(
        "scan: CVEs with a fix rated HIGH or CRITICAL, or known exploited: 0\n")


@pytest.mark.parametrize("raised, status, said", [
    (scan.NotAScan("it is no JSON: Expecting value"), 1,
     "refused: the scan of production's image was refused: it is no JSON: Expecting value\n"),
    (scan.ScanFailed("docker save on unix:///var/run/docker.sock exited 1: no space"), 2,
     "failed: docker save on unix:///var/run/docker.sock exited 1: no space\n")])
def test_a_failure_files_one_card_and_nothing_while_it_is_open(raised, status, said,
                                                               monkeypatch, capsys):
    """A scan refused exits 1, one that could not run 2, each saying why, printing no count,
    and filing one card for the scan's failure, quoting what it said; the next day's failure,
    saying another thing, files nothing while that card is open (ruling plan#3788)."""
    tracker = FakeTracker()
    assert _ran(monkeypatch, tracker, raised) == status
    assert capsys.readouterr() == (_filed(1, _FAILED_TITLE, "failure", "plan#3788"), said)
    assert_filed_high(tracker, 1, _FAILED_TITLE)
    assert tracker.bodies[1] == "\n".join([
        f"The daily scan of the image production is running failed at {_WHEN}, so no CVE in "
        "the image reaches the board until it runs again. What `quill scan` said:", "",
        "```text", said.split(": ", 1)[1].rstrip("\n"), "```", "",
        "The card open for the scan's failure holds it (ruling plan#3788), at Priority High at "
        "the top of the board (ruling plan#3620): while that card is open, a later failure "
        "files nothing, and once it is closed, the next failure files a card of its own. The "
        "session that claims it makes the scan run again, and sets its Effort.", "",
        f"<!-- quill: told on failure --> {app.marker('scan failed')}"])
    tracker.writes.clear()
    later = scan.ScanFailed("docker inspect on unix:///var/run/docker.sock exited 1: no such")
    assert _ran(monkeypatch, tracker, later, at=_AT + timedelta(days=1)) == 2
    assert capsys.readouterr().out == ""
    assert not tracker.writes


@pytest.mark.parametrize("reason", ["NOT_PLANNED", "COMPLETED"])
def test_a_failure_after_its_card_closed_files_a_card_of_its_own(reason, monkeypatch, capsys):
    """The failure's card closed, dropped or shipped: the next failure, the same words or
    others, files a card of its own, as a closed card holds no failure (ruling plan#3788)."""
    tracker = FakeTracker()
    tracker.add(4, body=f"Failed.\n\n<!-- quill: told on failure --> {_scan.FAILED_MARK}",
                is_open=False, state_reason=reason)
    assert _ran(monkeypatch, tracker, scan.ScanFailed("docker save exited 1: no space")) == 2
    assert capsys.readouterr().out == _filed(5, _FAILED_TITLE, "failure", "plan#3788")


def test_what_a_refusal_quotes_holds_no_line_and_mentions_no_one(monkeypatch, capsys):
    """A refusal quoting what the image wrote, a package named as a line holding a thing told
    and a mention (the shape of ``scan._listable``'s refusal): the card quotes it in a code
    block, so the card holds the failure's line alone, and the mention is inside the block."""
    value = f"python\n<!-- quill: told on failure --> {app.marker('x')}\n@SaltyReformed ```"
    tracker = FakeTracker()
    raised = scan.NotAScan(f"match 0's artifact's name is {value!r}, which cannot be listed")
    assert _ran(monkeypatch, tracker, raised) == 1
    capsys.readouterr()
    body = tracker.bodies[1]
    assert _waits.holds(body) == {("failure", _scan.FAILED_MARK)}
    opened, closed = body.index("````text\n"), body.rindex("\n````\n")
    assert opened < body.index("@SaltyReformed") < closed
    assert body.count("@SaltyReformed") == 1


def test_a_failure_to_file_the_failure_says_what_the_scan_said(monkeypatch, capsys):
    """The tracker failing as the failure's card is filed: exit 2, the one line saying the
    tracker's failure and what the scan said, so neither is lost."""
    def unreachable():
        """A ``connect`` whose tracker cannot be reached."""
        raise GitHubError(502, "GitHub answered 502")

    raised = scan.ScanFailed("docker save exited 1: no space")
    assert _ran(monkeypatch, unreachable, raised) == 2
    assert capsys.readouterr() == (
        "", "failed: GitHub answered 502; while filing the card for the scan's failure, which "
            "said: docker save exited 1: no space\n")


def test_a_card_whose_filing_was_cut_short_is_finished_and_takes_what_was_found_since(
        monkeypatch, capsys):
    """A pass cut short after creating the interpreter's card for six CVEs, before its mark
    came off: the next day's scan, finding a seventh beside them, finishes that card and puts
    what it found on it, and files no second card (R-BAL186, ruling plan#3787); the day after
    files nothing."""
    tracker = FakeTracker()
    tracker.add(9, body="\n".join(_lines(_PYTHON, *_SEVEN[:6])), on_board=False,
                labels=("filing", "tooling"), arc="tooling", title=_PYTHON_TITLE)
    assert _ran(monkeypatch, tracker, _done(*_SEVEN)) == 0
    out = capsys.readouterr().out
    assert f"finish the filing of plan#9, which holds the scan's {_PYTHON}, found at " in out
    assert out.endswith(
        f"put the scan's {_PYTHON}, found at {_WHEN}, on plan#9, the card open for it (ruling "
        "plan#3787)\n")
    assert [write[0] for write in tracker.writes] == [
        "set_field", "board_add", "board_place", "unmark", "comment"]
    assert 10 not in tracker.cards_by_number
    assert _waits.holds(tracker.writes[-1][2]) == {(_PYTHON, item.marker) for item in _SEVEN}
    tracker.writes.clear()
    assert _ran(monkeypatch, tracker, _done(*_SEVEN), at=_AT + timedelta(days=1)) == 0
    assert not tracker.writes
