"""The weekly image scan's judge (:mod:`tools.update.scan`; ruling plan#3720): the scanner's
JSON judged as data, each CVE with a fix rated HIGH or CRITICAL by any source counted.  The scan
is a trimmed copy of a real one, ``recorded/scan.json``: seven of the 422 matches Grype v0.121.2
made on dev's image 2026-11-13, each whole, with the scan's own source, distro and descriptor.
A case the real scan does not hold is that copy with one value changed."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci.arcs import REPO
from tools.update import scan
from tools.update.app import marker, markers

#: The trimmed copy of a real scan.
RECORDED = Path(__file__).resolve().parent / "recorded" / "scan.json"
#: The one match of the recorded scan that counts: the interpreter's CRITICAL CVE.
_CRITICAL = scan.Fixed("CVE-2026-1999903", "binary", "python", "3.14.8",
                       ("3.11.18", "3.12.16", "3.13.18", "3.14.10", "3.15.2", "3.16.0"),
                       scan.RATINGS.index("critical"))


def _recorded() -> dict:
    """The recorded scan, read afresh, so a test may change it."""
    return json.loads(RECORDED.read_text(encoding="utf-8"))


def _match(vulnerability: str, package: str) -> dict:
    """The recorded scan's one match of ``vulnerability`` in the package named ``package``."""
    found, = (match for match in _recorded()["matches"]
              if (match["vulnerability"]["id"], match["artifact"]["name"]) == (vulnerability,
                                                                               package))
    return found


def _scan(*matches: dict) -> dict:
    """The recorded scan holding ``matches`` alone."""
    return {**_recorded(), "matches": list(matches)}


def _ratings(match: dict) -> list[str | None]:
    """Each rating ``match`` is given, its own first, then each related record's."""
    return [record.get("severity") for record in (match["vulnerability"],
                                                  *match["relatedVulnerabilities"])]


def _given_a_fix(match: dict, *versions: str) -> dict:
    """``match`` as Grype would write it once it knew a fix in ``versions``."""
    match["vulnerability"]["fix"] = {"versions": list(versions), "state": "fixed"}
    return match


def test_the_recorded_scan_lists_its_one_fixed_cve_rated_critical():
    """Of the seven matches, the interpreter's CRITICAL CVE with a fix alone counts: its CVE,
    its package's type, name and installed version, and each version Grype names the fix in."""
    assert scan.judged(_recorded()) == [_CRITICAL]


@pytest.mark.parametrize(("vulnerability", "package", "state", "ratings"), [
    ("CVE-2026-9999902", "python", "fixed", ["Medium"]),
    ("CVE-2026-1999901", "python", "fixed", ["Low"]),
    ("CVE-2019-9000001", "libc-bin", "not-fixed", ["Negligible", "Critical"]),
    ("CVE-2026-9999901", "perl", "wont-fix", ["High", "High"]),
    ("CVE-2026-1999902", "libkrb5-3", "not-fixed", ["High", "High"]),
    ("CVE-2026-9999903", "openssl", "not-fixed", ["Unknown", "Unknown"]),
], ids=["fixed_but_medium", "fixed_but_low", "critical_to_nvd_with_no_fix",
        "high_that_debian_will_not_fix", "high_with_no_fix", "unrated_with_no_fix"])
def test_a_match_with_no_fix_or_rated_below_high_by_every_source_is_not_listed(
        vulnerability, package, state, ratings):
    """Each other match of the recorded scan, as Grype wrote it, the state and ratings its id
    names read from the copy: none counts."""
    match = _match(vulnerability, package)
    assert (match["vulnerability"]["fix"]["state"], _ratings(match)) == (state, ratings)
    assert not scan.judged(_scan(match))


@pytest.mark.parametrize(("vulnerability", "package", "rating"), [
    ("CVE-2019-9000001", "libc-bin", "critical"),
    ("CVE-2026-9999901", "perl", "high"),
], ids=["negligible_to_debian_critical_to_nvd", "high_to_both"])
def test_a_fix_turns_a_match_red_by_the_highest_rating_any_source_gives(
        vulnerability, package, rating):
    """Ruling plan#3720's case from the real scan: the C library's CVE, negligible to Debian and
    CRITICAL to NVD, once Debian ships a fix, is listed as CRITICAL; Perl's, HIGH to both."""
    match = _given_a_fix(_match(vulnerability, package), "1.0-1")
    found, = scan.judged(_scan(match))
    assert (found.vulnerability, found.package, found.fixes, found.rating) == (
        vulnerability, package, ("1.0-1",), scan.RATINGS.index(rating))


@pytest.mark.parametrize("spelled", ["HIGH", "high", "hIgH"])
def test_a_rating_is_read_ignoring_case_as_grype_reads_it(spelled):
    """Grype's own ``ParseSeverity`` lowercases what it reads."""
    match = _match("CVE-2026-9999902", "python")
    match["vulnerability"]["severity"] = spelled
    found, = scan.judged(_scan(match))
    assert found.rating == scan.RATINGS.index("high")


def test_a_missing_rating_reads_as_unknown():
    """Grype writes no rating it was not given, and reads none as unknown: the CRITICAL CVE
    unrated is listed only once a related record rates it HIGH."""
    match = _match("CVE-2026-1999903", "python")
    del match["vulnerability"]["severity"]
    assert not scan.judged(_scan(match))
    match["relatedVulnerabilities"] = [{"id": "CVE-2026-1999903", "severity": "High"}]
    found, = scan.judged(_scan(match))
    assert found.rating == scan.RATINGS.index("high")


@pytest.mark.parametrize("spelled", ["Important", "Moderate", "Severe", "", 3, None])
def test_a_rating_grype_does_not_spell_refuses_a_fixed_match(spelled):
    """A rating outside Grype's scale on a match with a fix could be a HIGH one read as nothing,
    so the scan is refused, never read as clean; on a related record as on the match.  An empty
    one Grype reads as unknown, as a missing one."""
    for record in ("vulnerability", "related"):
        match = _given_a_fix(_match("CVE-2026-9999901", "perl"), "1.0-1")
        (match["vulnerability"] if record == "vulnerability" else
         match["relatedVulnerabilities"][0])["severity"] = spelled
        if spelled == "":
            assert len(scan.judged(_scan(match))) == 1
            continue
        with pytest.raises(scan.NotAScan, match="which is no rating Grype gives"):
            scan.judged(_scan(match))


def test_a_rating_on_a_match_with_no_fix_decides_nothing_and_is_not_read():
    """Only a match with a fix is rated, so one with none may be rated anything."""
    match = _match("CVE-2026-1999902", "libkrb5-3")
    match["vulnerability"]["severity"] = "Important"
    assert not scan.judged(_scan(match))


@pytest.mark.parametrize("state", ["", "unknown", "not-fixed", "wont-fix"])
def test_a_match_whose_fix_grype_does_not_know_is_not_listed(state):
    """Each fix state but ``fixed`` that Grype writes: none for a record without metadata."""
    match = _match("CVE-2026-1999903", "python")
    match["vulnerability"]["fix"]["state"] = state
    assert not scan.judged(_scan(match))


@pytest.mark.parametrize("state", ["Fixed", "patched", 1, None])
def test_a_fix_state_grype_does_not_write_refuses_the_scan(state):
    """A state Grype does not write could be a fix read as none."""
    match = _match("CVE-2026-1999902", "libkrb5-3")
    match["vulnerability"]["fix"]["state"] = state
    with pytest.raises(scan.NotAScan, match="match 0's fix"):
        scan.judged(_scan(match))


@pytest.mark.parametrize(("where", "value"), [
    (("artifact", "name"), "py`thon"),
    (("artifact", "name"), f"python <!-- update: mention {'a' * 64} -->"),
    (("artifact", "name"), "@someone"),
    (("artifact", "name"), ""),
    (("artifact", "name"), None),
    (("artifact", "version"), "3.14.8 "),
    (("artifact", "type"), "<binary>"),
    (("vulnerability", "id"), "CVE-1,CVE-2"),
    (("vulnerability", "fix", "versions"), ["3.14.10`"]),
    (("vulnerability", "fix", "versions"), [3]),
], ids=["a_backtick", "a_marker", "a_mention", "empty", "none", "a_space", "angle_brackets",
        "a_comma", "a_backtick_in_a_fix", "a_number_for_a_fix"])
def test_a_value_that_cannot_be_listed_refuses_the_scan(where, value):
    """What the image holds writes a package's name and version: none a counted match names may
    end a code span it is quoted in, mention anyone or spell a marker ``quill sync`` would
    read."""
    match = _match("CVE-2026-1999903", "python")
    node = match
    for key in where[:-1]:
        node = node[key]
    node[where[-1]] = value
    with pytest.raises(scan.NotAScan, match="which cannot be listed"):
        scan.judged(_scan(match))


@pytest.mark.parametrize("given", [
    [],
    {"matches": {}},
    {"matches": [1]},
    {"matches": [{"vulnerability": {}}]},
    {"matches": [{"vulnerability": {"fix": {}}}]},
], ids=["no_object", "matches_no_list", "a_match_no_object", "no_fix", "no_fix_state"])
def test_a_scan_not_shaped_as_grype_writes_one_is_refused(given):
    """Nothing read as no match: a JSON of another shape is refused whole."""
    with pytest.raises(scan.NotAScan, match="holds no"):
        scan.judged(given)


@pytest.mark.parametrize("dropped", [("relatedVulnerabilities",), ("artifact",),
                                     ("vulnerability", "fix", "versions")])
def test_a_counted_match_missing_what_it_names_is_refused(dropped):
    """A match with a fix but none of its related records, its package, or the versions its
    fix is in: refused, never counted short."""
    match = _match("CVE-2026-1999903", "python")
    node = match
    for key in dropped[:-1]:
        node = node[key]
    del node[dropped[-1]]
    with pytest.raises(scan.NotAScan, match="holds no"):
        scan.judged(_scan(match))


def test_each_match_is_listed_once_for_each_package_and_version_installed():
    """A match repeated is counted once; the same CVE in a second copy of the package, at
    another version, is counted again under the same marker; in another package, under another
    marker."""
    first = _match("CVE-2026-1999903", "python")
    second = copy.deepcopy(first)
    second["artifact"]["version"] = "3.14.9"
    deb = copy.deepcopy(first)
    deb["artifact"].update(name="python3.14", type="deb")
    found = scan.judged(_scan(first, copy.deepcopy(first), second, deb))
    assert [(item.kind, item.package, item.installed) for item in found] == [
        ("binary", "python", "3.14.8"), ("binary", "python", "3.14.9"),
        ("deb", "python3.14", "3.14.8")]
    assert found[0].marker == found[1].marker != found[2].marker


def test_the_highest_rated_are_listed_first_then_by_cve():
    """CRITICAL before HIGH, whatever their CVEs; each rating's by its CVE's id as text."""
    perl = _given_a_fix(_match("CVE-2026-9999901", "perl"), "5.40.1-7")
    older = _given_a_fix(_match("CVE-2026-1999902", "libkrb5-3"), "1.21.3-6")
    found = scan.judged(_scan(perl, _match("CVE-2026-1999903", "python"), older))
    assert [item.vulnerability for item in found] == [
        "CVE-2026-1999903", "CVE-2026-1999902", "CVE-2026-9999901"]


def test_the_marker_names_the_cve_and_the_package_in_the_form_quill_sync_reads():
    """One marker per CVE and package (:func:`tools.update.app.marker`), which the reader of the
    update's record finds again (:func:`tools.update.app.markers`)."""
    assert _CRITICAL.marker == marker("scan CVE-2026-1999903 binary python")
    assert markers(_CRITICAL.marker) == (_CRITICAL.marker,)


def test_a_file_that_is_no_utf_8_json_is_refused(tmp_path):
    """Grype's JSON is read as UTF-8; anything else is refused, never read as no match."""
    assert scan.read(RECORDED) == _recorded()
    for name, data in (("text", b"no json"), ("bytes", b'{"matches": "\xff"}')):
        (tmp_path / name).write_bytes(data)
        with pytest.raises(scan.NotAScan, match="it is no JSON"):
            scan.read(tmp_path / name)


def test_the_module_runs_with_no_package_installed():
    """Run by an interpreter given no installed package (``-S``; ``-E`` so no variable of this
    run's puts one back), the module imports and answers."""
    done = subprocess.run([sys.executable, "-E", "-S", "-m", scan.__name__, "--help"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.startswith("usage: python -m tools.update.scan ")


def test_main_says_how_many_matches_of_the_recorded_scan_count(capsys):
    """The recorded scan, judged from its file: its one CRITICAL CVE counts, and it exits 0."""
    assert scan.main([str(RECORDED)]) == 0
    assert capsys.readouterr() == (
        "update scan: CVEs with a fix rated HIGH or CRITICAL: 1\n", "")


@pytest.mark.parametrize("given", ["missing", "not-json"])
def test_main_refuses_a_scan_it_cannot_read(given, tmp_path, capsys):
    """A file missing, or no scan, exits 1 saying why, never read as a clean scan."""
    path = tmp_path / "scan.json"
    if given == "not-json":
        path.write_text('{"matches": [', encoding="utf-8")
    assert scan.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"update scan: {path} was refused: ")
