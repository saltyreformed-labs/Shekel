"""The weekly image scan's report (:mod:`tools.update.scan`; rulings plan#3710, plan#3720): the
scanner's JSON judged as data, each CVE with a fix rated HIGH or CRITICAL by any source listed
with its marker in an issue of the scan's own, which closes the one before.  The scan is a
trimmed copy of a real one, ``recorded/scan.json``: seven of the 422 matches Grype v0.121.2 made
on dev's image 2026-11-13, each whole, with the scan's own source, distro and descriptor.  A
case the real scan does not hold is that copy with one value changed.  ``gh`` is faked, as a
recorder of what it is handed or a program on PATH; what the real one does with each call is
measured in :mod:`tools.update.alert`'s notes."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from tools.ci.arcs import REPO
from tools.update import fake_gh, scan
from tools.update.alert import BOT, CLOSED
from tools.update.app import DEVELOPER, marker, markers
from tools.update.fake_gh import LINK as _LINK
from tools.update.fake_gh import REPOSITORY as _REPOSITORY

#: The trimmed copy of a real scan.
RECORDED = Path(__file__).resolve().parent / "recorded" / "scan.json"
_RUN = "https://github.com/owner/shekel/actions/runs/7"
_DAY = date(2026, 11, 17)
#: The one match of the recorded scan that counts: the interpreter's CRITICAL CVE.
_CRITICAL = scan.Fixed("CVE-2026-1999903", "binary", "python", "3.14.8",
                       ("3.11.18", "3.12.16", "3.13.18", "3.14.10", "3.15.2", "3.16.0"),
                       scan.RATINGS.index("critical"))
#: Its issue, spelled out once here, so a change to what the issue says fails this file.
_ISSUE = {
    "title": "The weekly image scan found fixed CVEs 2026-11-17",
    "body": "The weekly image scan of `dev`'s image found these CVEs, each with a fix and rated "
            f"HIGH or CRITICAL by some source (ruling plan#3720), in the run {_RUN}:\n\n"
            "- `CVE-2026-1999903` in `python` (`binary`) `3.14.8`, rated CRITICAL, fixed in "
            "`3.11.18`, `3.12.16`, `3.13.18`, `3.14.10`, `3.15.2`, `3.16.0` "
            f"{marker('scan CVE-2026-1999903 binary python')}\n\n"
            "`quill sync` files a card for each one no card holds yet (ruling plan#3710). The "
            "next red scan closes this issue as it opens its own, and a clean one closes it."}


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
    so the scan is refused, which fails the report's job, which the alert reports; on a related
    record as on the match.  An empty one Grype reads as unknown, as a missing one."""
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
def test_a_value_the_issue_cannot_list_refuses_the_scan(where, value):
    """What the image holds writes a package's name and version: none listed may end its code
    span, mention anyone or spell a marker ``quill sync`` would read."""
    match = _match("CVE-2026-1999903", "python")
    node = match
    for key in where[:-1]:
        node = node[key]
    node[where[-1]] = value
    with pytest.raises(scan.NotAScan, match="which the issue cannot list"):
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
def test_a_counted_match_missing_what_its_line_needs_is_refused(dropped):
    """A match with a fix but none of its related records, its package, or the versions its
    fix is in: refused, never listed short."""
    match = _match("CVE-2026-1999903", "python")
    node = match
    for key in dropped[:-1]:
        node = node[key]
    del node[dropped[-1]]
    with pytest.raises(scan.NotAScan, match="holds no"):
        scan.judged(_scan(match))


def test_each_match_is_listed_once_for_each_package_and_version_installed():
    """A match repeated is one line; the same CVE in a second copy of the package, at another
    version, is a second line under the same marker; in another package, another marker."""
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
    """One marker per CVE and package (:func:`tools.update.app.marker`), found again in the
    issue's text by the reader of the update's record."""
    assert _CRITICAL.marker == marker("scan CVE-2026-1999903 binary python")
    assert markers(scan.issue(_DAY, _RUN, [_CRITICAL])["body"]) == (_CRITICAL.marker,)


def test_the_issue_lists_each_cve_with_its_marker_and_mentions_no_one():
    """Its title is the day; its text the run, then each CVE on a line of its own, every value
    in a code span and its marker last; it mentions no one."""
    assert scan.issue(_DAY, _RUN, [_CRITICAL]) == _ISSUE
    assert f"@{DEVELOPER}" not in _ISSUE["body"]


def test_a_fix_no_version_names_says_so():
    """Grype marks a fix known with no version named, which the line says rather than ending
    short."""
    item = scan.Fixed("CVE-1", "deb", "a", "1", (), scan.RATINGS.index("high"))
    line, = (row for row in scan.issue(_DAY, _RUN, [item])["body"].splitlines()
             if row.startswith("- "))
    assert line == (f"- `CVE-1` in `a` (`deb`) `1`, rated HIGH, fixed in no version Grype "
                    f"names {item.marker}")


def test_a_file_that_is_no_utf_8_json_is_refused(tmp_path):
    """Grype's JSON is read as UTF-8; anything else is refused, never read as no match."""
    assert scan.read(RECORDED) == _recorded()
    for name, data in (("text", b"no json"), ("bytes", b'{"matches": "\xff"}')):
        (tmp_path / name).write_bytes(data)
        with pytest.raises(scan.NotAScan, match="it is no JSON"):
            scan.read(tmp_path / name)


def _issue(number=9, title=_ISSUE["title"], by=BOT):
    """One line of the listing, as :func:`tools.update.alert.listed`'s ``--jq`` prints it."""
    return json.dumps({"number": number, "title": title, "body": "a list", "by": by})


class _Gh(fake_gh.Issues):
    """The module's two calls of ``gh`` faked (:class:`tools.update.fake_gh.Issues`), as
    :func:`scan.run` makes them."""

    def run(self, found):
        """:func:`scan.run` over this fake, given the issue ``found`` makes for a red scan or
        None for a clean one, as :func:`scan.main` gives it: its exit status."""
        opening = scan.issue(_DAY, _RUN, found) if found else None
        return scan.run(_REPOSITORY, opening, listing=self.listing, send=self.send)


def test_a_red_scan_with_none_open_opens_its_issue(capsys):
    """One issue opened, its link printed."""
    gh = _Gh()
    assert gh.run([_CRITICAL]) == 0
    assert gh.written == [(None, _ISSUE)]
    assert capsys.readouterr().out == f"update scan: opened {_LINK}\n"


def test_a_red_scan_opens_its_issue_then_closes_each_earlier_one_of_its_own():
    """Its own earlier issues, of any day, are closed once the new one is open; a stranger's
    issue of its title, and the alert's by the same token, are left."""
    gh = _Gh(_issue(number=3, title=f"{scan.FOUND}2026-11-03"), _issue(number=5, by="a-stranger"),
             _issue(number=6, title="The weekly update failed 2026-11-10"), _issue(number=9))
    assert gh.run([_CRITICAL]) == 0
    assert gh.written == [(None, _ISSUE), (3, CLOSED), (9, CLOSED)]


def test_a_refused_open_closes_nothing_and_exits_1(capsys):
    """The earlier list stands while no newer one is open."""
    gh = _Gh(_issue(), refused=(None,))
    assert gh.run([_CRITICAL]) == 1
    assert gh.written == [(None, _ISSUE)]
    assert capsys.readouterr().err == (
        "update scan: no issue was opened, so none was closed: gh: refused (HTTP 403)\n")


def test_a_clean_scan_closes_each_of_its_own_and_opens_none(capsys):
    """Nothing counted: each open issue of its own is closed, its reason completed."""
    gh = _Gh(_issue(number=3), _issue(number=9))
    assert gh.run([]) == 0
    assert gh.written == [(3, CLOSED), (9, CLOSED)]
    assert capsys.readouterr().out == (
        f"update scan: closed {_LINK}\nupdate scan: closed {_LINK}\n")


def test_a_clean_scan_with_none_open_writes_nothing():
    """Nothing counted and nothing open: no write."""
    gh = _Gh(_issue(number=5, by="a-stranger"))
    assert gh.run([]) == 0
    assert not gh.written


def test_a_listing_gh_refuses_writes_nothing_and_exits_1(capsys):
    """No listing, no knowing which to close, and a new issue opened then would leave the earlier
    one standing beside it."""
    gh = _Gh(listing_status=1)
    assert gh.run([_CRITICAL]) == 1
    assert not gh.written
    assert capsys.readouterr().err == (
        "update scan: no issue was read: gh: Bad credentials (HTTP 401)\n")


def test_a_close_gh_refuses_exits_1_each_other_still_tried(capsys):
    """A close GitHub refuses fails the job, which the alert reports; the next is still tried."""
    gh = _Gh(_issue(number=3), _issue(number=9), refused=(3,))
    assert gh.run([]) == 1
    assert [number for number, _ in gh.written] == [3, 9]
    assert capsys.readouterr().err == (
        "update scan: issue 3 was not closed: gh: refused (HTTP 403)\n")


def test_the_module_runs_with_no_package_installed():
    """Its job installs the interpreter and no package (``install: python``): run by one given
    no installed package (``-S``; ``-E`` so no variable of this run's puts one back), the module
    imports and answers."""
    done = subprocess.run([sys.executable, "-E", "-S", "-m", scan.__name__, "--help"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.startswith("usage: python -m tools.update.scan ")


def test_main_exits_1_naming_each_variable_unset(monkeypatch, capsys):
    """No token or no repository reads nothing, naming each missing."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(scan, "run", lambda *args: pytest.fail("ran with no token"))
    assert scan.main([str(RECORDED), _RUN]) == 1
    assert capsys.readouterr().err == (
        "update scan: GH_TOKEN, GITHUB_REPOSITORY unset, so nothing was read\n")


@pytest.mark.parametrize("given", ["missing", "not-json"])
def test_main_refuses_a_scan_it_cannot_read_and_writes_nothing(given, tmp_path, monkeypatch,
                                                               capsys):
    """A file missing, or no scan, fails the job, which the alert reports; no issue is opened
    or closed, as a clean scan would close the open one."""
    monkeypatch.setenv("GH_TOKEN", "t0k")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPOSITORY)
    monkeypatch.setattr(scan, "run", lambda *args: pytest.fail("ran on no scan"))
    path = tmp_path / "scan.json"
    if given == "not-json":
        path.write_text('{"matches": [', encoding="utf-8")
    assert scan.main([str(path), _RUN]) == 1
    assert capsys.readouterr().err.startswith(f"update scan: {path} was refused: ")


def test_main_opens_the_issue_for_the_recorded_scan_end_to_end(tmp_path, monkeypatch, capsys):
    """The recorded scan, the real listing and sender, and a ``gh`` first on PATH whose listing
    holds one earlier issue of the scan's: one POST of the issue the scan says, then one PATCH
    closing the earlier, and how many it listed."""
    fake_gh.on_path(tmp_path, monkeypatch, listing=_issue(number=3) + "\n")
    assert scan.main([str(RECORDED), _RUN], today=lambda: _DAY) == 0
    assert [(args[1:4], body) for args, body in fake_gh.calls(tmp_path) if body is not None] == [
        (["repos/owner/shekel/issues", "--method", "POST"], _ISSUE),
        (["repos/owner/shekel/issues/3", "--method", "PATCH"], CLOSED)]
    assert capsys.readouterr().out == (
        f"update scan: opened {_LINK}\nupdate scan: closed {_LINK}\n"
        "update scan: CVEs with a fix rated HIGH or CRITICAL: 1\n")


def test_main_closes_the_open_issue_on_a_clean_scan_end_to_end(tmp_path, monkeypatch, capsys):
    """The recorded scan with its one counted match dropped, over the same ``gh``: no POST, one
    PATCH closing the open issue, and none listed."""
    fake_gh.on_path(tmp_path, monkeypatch, listing=_issue(number=3) + "\n")
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(_scan(*(match for match in _recorded()["matches"]
                                         if match["vulnerability"]["id"] != "CVE-2026-1999903"))),
                     encoding="utf-8")
    assert scan.main([str(clean), _RUN], today=lambda: _DAY) == 0
    assert [(args[1:4], body) for args, body in fake_gh.calls(tmp_path) if body is not None] == [
        (["repos/owner/shekel/issues/3", "--method", "PATCH"], CLOSED)]
    assert capsys.readouterr().out == (
        f"update scan: closed {_LINK}\n"
        "update scan: CVEs with a fix rated HIGH or CRITICAL: 0\n")
