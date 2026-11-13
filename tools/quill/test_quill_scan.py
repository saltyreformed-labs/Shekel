"""``quill scan`` (:mod:`tools.quill._scan`; ruling plan#3825): the door that runs the daily scan
of the image production is running and says what it found, its exit statuses the command's.
The scan itself is :func:`tools.update.scan.scanned`'s, tested in ``tools/update/test_scan.py``;
here it is stood in for, so the door is tested apart from docker."""
from __future__ import annotations

import pytest

from tools.ci.arcs import REPO
from tools.quill import quill
from tools.update import scan

#: What the stand-in scan found: two matches, as the judge orders them.
_DONE = scan.Scanned(
    "sha256:" + "4f19" * 16, "0123456789abcdef" * 2 + "01234567",
    (scan.Fixed("CVE-2000-0002", "binary", "python", "3.1.4", ("3.1.5", "3.2.1"),
                scan.RATINGS.index("critical")),
     scan.Fixed("CVE-2000-0001", "deb", "libexample1", "1.0-1", ("1.0-2",),
                scan.RATINGS.index("high"))))


def _never():
    """A ``connect`` that fails the test: the scan writes nothing to the tracker yet."""
    raise AssertionError("scan connected to the tracker")


def _stood_in(monkeypatch, outcome) -> list:
    """``scan.scanned`` stood in for: it answers ``outcome``, or raises it when it is an
    exception; the roots it was handed, recorded."""
    handed = []

    def scanned(root):
        """The stand-in scan."""
        handed.append(root)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(scan, "scanned", scanned)
    return handed


def test_a_scan_says_the_image_the_commit_and_each_match_that_counts(monkeypatch, capsys):
    """Exit 0, the scan run from the checkout's root, nothing read of the tracker."""
    handed = _stood_in(monkeypatch, _DONE)
    assert quill.main(["scan"], connect=_never, root=REPO) == 0
    assert handed == [REPO]
    assert capsys.readouterr() == (
        f"scan: production's image {_DONE.image}, built from {_DONE.revision}\n"
        "scan: CVEs with a fix rated HIGH or CRITICAL: 2\n"
        "  CVE-2000-0002, rated critical: binary python 3.1.4, fixed in 3.1.5, 3.2.1\n"
        "  CVE-2000-0001, rated high: deb libexample1 1.0-1, fixed in 1.0-2\n", "")


def test_a_clean_scan_says_none_count(monkeypatch, capsys):
    """A scan in which no match counts says so, exit 0."""
    _stood_in(monkeypatch, scan.Scanned(_DONE.image, _DONE.revision, ()))
    assert quill.main(["scan"], connect=_never, root=REPO) == 0
    assert capsys.readouterr().out.endswith("scan: CVEs with a fix rated HIGH or CRITICAL: 0\n")


@pytest.mark.parametrize("raised, status, said", [
    (scan.NotAScan("it is no JSON: Expecting value"), 1,
     "refused: the scan of production's image was refused: it is no JSON: Expecting value\n"),
    (scan.ScanFailed("docker save on unix:///var/run/docker.sock exited 1: no space"), 2,
     "failed: docker save on unix:///var/run/docker.sock exited 1: no space\n")])
def test_a_scan_refused_or_unable_to_run_is_never_read_as_clean(raised, status, said, monkeypatch,
                                                                capsys):
    """A refused scan exits 1, one that could not run 2, each saying why and printing no
    count."""
    _stood_in(monkeypatch, raised)
    assert quill.main(["scan"], connect=_never, root=REPO) == status
    assert capsys.readouterr() == ("", said)
