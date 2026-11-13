"""The daily image scan (:mod:`tools.update.scan`): its judge (rulings plan#3720, plan#3819), the
scanner's JSON judged as data, each CVE with a fix rated HIGH or CRITICAL by any source counted,
and each Grype marks known exploited, at any rating, with a fix or without; and the scan itself
(ruling plan#3822), production's image read on its daemon and scanned on the rootless one, run
with a stand-in for docker.  The scan judged is a trimmed copy of a real one,
``recorded/scan.json``: seven of the 422 matches Grype v0.121.2 made on dev's image 2026-11-13,
each whole, with the scan's own source, distro and descriptor, its CVE ids, descriptions and
links invented (ruling plan#3841).  None is marked known exploited.  A case the real scan does
not hold is that copy with one value changed."""
from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import private_cluster
from tools.ci.arcs import REPO
from tools.update import scan
from tools.update.app import marker, markers

#: The trimmed copy of a real scan.
RECORDED = Path(__file__).resolve().parent / "recorded" / "scan.json"
#: The one match of the recorded scan that counts: the interpreter's CRITICAL CVE.
_CRITICAL = scan.Counted("CVE-2026-1999903", "binary", "python", "3.14.8",
                         ("3.11.18", "3.12.16", "3.13.18", "3.14.10", "3.15.2", "3.16.0"),
                         scan.RATINGS.index("critical"), False)


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


def _marked(record: dict) -> dict:
    """``record``, a vulnerability's, as Grype writes it once CISA's list of CVEs known to be
    exploited names its CVE: one entry of that list, in Grype's shape, its values invented but
    the CVE's (ruling plan#3841)."""
    record["knownExploited"] = [{
        "cve": record["id"], "vendorProject": "Example", "product": "Example",
        "dateAdded": "2026-11-02", "requiredAction": "Apply the vendor's fix.",
        "dueDate": "2026-11-23", "knownRansomwareCampaignUse": "Unknown",
        "urls": ["https://example.invalid/advisory"], "cwes": ["CWE-787"]}]
    return record


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


def test_a_match_counted_for_its_fix_naming_no_version_is_refused():
    """Grype writes a fix's state as fixed only beside a version it is fixed in, so a match
    that would be listed for its fix with none is refused, never listed as having no fix."""
    match = _given_a_fix(_match("CVE-2026-1999903", "python"))
    with pytest.raises(scan.NotAScan, match="match 0's fix is fixed in no version"):
        scan.judged(_scan(match))


def test_a_cve_known_exploited_counts_at_medium_with_a_fix():
    """Ruling plan#3819: the interpreter's Medium CVE with a fix does not count until Grype
    marks it known exploited; then it is listed at Medium, with its fix."""
    match = _match("CVE-2026-9999902", "python")
    assert not scan.judged(_scan(match))
    _marked(match["vulnerability"])
    assert scan.judged(_scan(match)) == [scan.Counted(
        "CVE-2026-9999902", "binary", "python", "3.14.8",
        ("3.10.22", "3.11.17", "3.12.15", "3.13.16", "3.14.9", "3.15.0rc3"),
        scan.RATINGS.index("medium"), True)]


@pytest.mark.parametrize("state", ["not-fixed", "wont-fix", "unknown", ""])
def test_a_cve_known_exploited_counts_at_low_with_no_fix(state):
    """Ruling plan#3819: the interpreter's Low CVE, as Grype writes it with no fix (each state
    but ``fixed``, no version), counted once Grype marks it known exploited, listed as fixed in
    no version."""
    match = _match("CVE-2026-1999901", "python")
    match["vulnerability"]["fix"] = {"versions": [], "state": state}
    assert not scan.judged(_scan(match))
    _marked(match["vulnerability"])
    assert scan.judged(_scan(match)) == [scan.Counted(
        "CVE-2026-1999901", "binary", "python", "3.14.8", (), scan.RATINGS.index("low"), True)]


@pytest.mark.parametrize(("vulnerability", "package", "rating"), [
    ("CVE-2019-9000001", "libc-bin", "critical"),
    ("CVE-2026-9999903", "openssl", "unknown"),
], ids=["negligible_to_debian_critical_to_nvd", "unrated_by_both"])
def test_a_mark_on_a_related_record_counts_as_one_on_the_match(vulnerability, package, rating):
    """Grype reads CISA's list for each record of a match, its own and each related one, and a
    mark on either is the CVE's: a Debian CVE with no fix whose NVD record alone is marked is
    counted, at the highest rating any source gives it."""
    match = _match(vulnerability, package)
    _marked(match["relatedVulnerabilities"][0])
    found, = scan.judged(_scan(match))
    assert (found.vulnerability, found.package, found.fixes, found.rating, found.exploited) == (
        vulnerability, package, (), scan.RATINGS.index(rating), True)


@pytest.mark.parametrize("marks", [None, [], {}, True, "CVE-2026-1999902", ["CVE-2026-1999902"],
                                   [{}], [{"cve": 7}], [{"cve": "CVE-2026-1999902"}, None]],
                         ids=["null", "empty", "an_object", "true", "a_string", "a_list_of_ids",
                              "an_entry_naming_no_cve", "a_cve_no_string", "a_second_entry"])
@pytest.mark.parametrize("record", ["vulnerability", "related", "related_beside_a_mark"])
def test_a_mark_grype_does_not_write_refuses_the_scan(marks, record):
    """Grype leaves the mark out where CISA's list names no CVE, and writes it otherwise as a
    list of that list's entries: any other shape could be a mark read as none, so it is refused,
    on a match that would not count unmarked as on any other, and on a related record beside
    the match's own mark, as every record's mark is read."""
    match = _match("CVE-2026-1999902", "libkrb5-3")
    if record == "related_beside_a_mark":
        _marked(match["vulnerability"])
    (match["vulnerability"] if record == "vulnerability" else
     match["relatedVulnerabilities"][0])["knownExploited"] = marks
    with pytest.raises(scan.NotAScan, match="which Grype does not write"):
        scan.judged(_scan(match))


def test_a_match_whose_related_records_are_not_as_grype_writes_them_is_refused():
    """Every record of every match is read for its mark, so a match with no fix and no list of
    related records, or one holding a record that is no object, is refused."""
    match = _match("CVE-2026-1999902", "libkrb5-3")
    match["relatedVulnerabilities"].append(3)
    with pytest.raises(scan.NotAScan, match="related vulnerability 1 is 3, which is no record"):
        scan.judged(_scan(match))
    del match["relatedVulnerabilities"]
    with pytest.raises(scan.NotAScan, match="match 0 holds no relatedVulnerabilities"):
        scan.judged(_scan(match))


def test_a_rating_on_a_match_counted_for_its_mark_is_read():
    """A match counted for its mark alone is listed with its rating, so one Grype does not
    spell is refused there, as on a match counted for its fix."""
    match = _match("CVE-2026-1999902", "libkrb5-3")
    match["vulnerability"]["severity"] = "Important"
    _marked(match["vulnerability"])
    with pytest.raises(scan.NotAScan, match="which is no rating Grype gives"):
        scan.judged(_scan(match))


@pytest.mark.parametrize(("descriptor", "said"), [
    ({"db": {"providers": {"nvd": {"captured": "2026-11-13T00:22:41Z"}}}},
     "names no provider kev"),
    ({"db": {"providers": {}}}, "names no provider kev"),
    ({"db": {"providers": {"kev": None}}}, "names no provider kev"),
    ({"db": {"providers": None}}, "the scan's database holds no providers"),
    ({"db": {}}, "the scan's database holds no providers"),
    ({}, "the scan's descriptor holds no db"),
    (None, "the scan holds no descriptor"),
], ids=["another_provider", "no_provider", "kev_no_object", "providers_null", "no_providers",
        "no_db", "no_descriptor"])
def test_a_scan_read_with_no_list_of_exploited_cves_is_refused(descriptor, said):
    """A database built without CISA's list marks nothing, so a scan that names no such
    provider is refused, never read as one in which nothing is exploited; Grype writes no
    provider at all where it could not read them.  The recorded scan names it."""
    assert set(_recorded()["descriptor"]["db"]["providers"]) >= {scan.KEV, "nvd", "debian"}
    with pytest.raises(scan.NotAScan, match=said):
        scan.judged({**_recorded(), "descriptor": descriptor})


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


@pytest.mark.parametrize(("matches", "said"), [
    ({}, "the scan holds no matches"),
    ([1], "match 0 holds no vulnerability"),
    ([{"vulnerability": {}}], "match 0's vulnerability holds no fix"),
    ([{"vulnerability": {"fix": {}}}], "match 0's fix holds no state"),
], ids=["matches_no_list", "a_match_no_object", "no_fix", "no_fix_state"])
def test_a_scan_not_shaped_as_grype_writes_one_is_refused(matches, said):
    """Nothing read as no match: a JSON of another shape is refused whole, the recorded scan's
    descriptor kept so each refusal is the one its case names."""
    with pytest.raises(scan.NotAScan, match="the scan holds no descriptor"):
        scan.judged([])
    with pytest.raises(scan.NotAScan, match=said):
        scan.judged({**_recorded(), "matches": matches})


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
    with pytest.raises(scan.NotAScan, match=f"holds no {dropped[-1]} "):
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


def test_an_answer_that_is_no_utf_8_json_is_refused():
    """Grype's JSON is read as UTF-8; anything else is refused, never read as no match."""
    assert scan.parsed(RECORDED.read_bytes()) == _recorded()
    for answer in (b"no json", b'{"matches": "\xff"}', b""):
        with pytest.raises(scan.NotAScan, match="it is no JSON"):
            scan.parsed(answer)


def test_the_module_imports_with_no_package_installed():
    """Imported by an interpreter given no installed package (``-S``; ``-E`` so no variable of
    this run's puts one back), the module and what it imports load (ruling plan#3825)."""
    done = subprocess.run([sys.executable, "-E", "-S", "-c", f"import {scan.__name__}"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


# -- the scan of production's image ----------------------------------------------------------

#: The daemon the stand-in names rootless.
_ROOTLESS = "unix:///run/user/1000/docker.sock"
#: The digest the stand-in's production container runs.
_IMAGE = "sha256:" + "4f19" * 16
#: The commit the stand-in's image was built from.
_BUILT_FROM = "0123456789abcdef" * 2 + "01234567"


def _labelled(labels: object) -> dict:
    """The recorded scan as Grype writes it of an image whose labels are ``labels``."""
    scanned = _recorded()
    scanned["source"]["target"]["labels"] = labels
    return scanned


class _Docker:
    """A stand-in for docker (:data:`tools.update.scan.Docker`): each call recorded, as its
    daemon, its arguments, the variables it adds and its seconds.  An inspect answers
    ``image``; a save writes the file it names as docker does, 0600; the scanner answers
    ``answer``, the mode of the file it was handed recorded as it runs; and the command
    ``failing`` names raises as a failed one."""

    def __init__(self, image: str = _IMAGE, answer: bytes | None = None,
                 failing: str | None = None) -> None:
        """A stand-in whose inspect answers ``image``, whose scanner answers ``answer`` (a
        labelled copy of the recorded scan when None), and whose ``failing`` command fails."""
        self.calls: list[tuple[str, list[str], dict[str, str], int]] = []
        self.image = image
        self.answer = (json.dumps(_labelled({scan.REVISION: _BUILT_FROM})).encode()
                       if answer is None else answer)
        self.failing = failing
        self.mode: int | None = None

    def __call__(self, daemon, arguments, added, seconds) -> bytes:
        """One docker command, as the class says."""
        self.calls.append((daemon, list(arguments), dict(added), seconds))
        if arguments[0] == self.failing:
            raise scan.ScanFailed(f"docker {arguments[0]} failed")
        if arguments[0] == "inspect":
            return f"{self.image}\n".encode()
        if arguments[0] == "save":
            saved = Path(arguments[arguments.index("--output") + 1])
            saved.write_bytes(b"an image")
            saved.chmod(0o600)
            return b""
        self.mode = stat.S_IMODE(Path(added["SCAN_IMAGE"]).stat().st_mode)
        return self.answer


@pytest.fixture(name="rootless")
def _rootless(monkeypatch):
    """The cluster module's daemon, the rootless one, says it is rootless."""
    monkeypatch.setattr(private_cluster, "endpoint", lambda: _ROOTLESS)
    monkeypatch.setattr(private_cluster, "asked_rootless", lambda: True)


def _saved(docker: _Docker) -> Path:
    """The file the stand-in's save wrote."""
    save, = (arguments for _, arguments, _, _ in docker.calls if arguments[0] == "save")
    return Path(save[save.index("--output") + 1])


@pytest.mark.usefixtures("rootless")
def test_a_scan_reads_production_on_its_daemon_and_scans_the_saved_file_on_the_rootless_one():
    """Production's container's image is read and saved on the system daemon, by its digest; the
    scanner reads the saved file, world-readable, on the rootless daemon; the commit is the
    scanned image's label; and the file is gone once the scan ends (ruling plan#3822)."""
    docker = _Docker()
    assert scan.scanned(REPO, docker) == scan.Scanned(_IMAGE, _BUILT_FROM, (_CRITICAL,))
    saved = str(_saved(docker))
    assert docker.calls == [
        (scan.PRODUCTION, ["inspect", "--format", "{{.Image}}", "shekel-prod-app"], {},
         scan.DOCKER_SECONDS),
        (scan.PRODUCTION, ["save", "--output", saved, _IMAGE], {}, scan.DOCKER_SECONDS),
        (_ROOTLESS, ["compose", "--file", str(REPO / scan.COMPOSE), "run", "--rm", "--no-TTY",
                     "scan"], {"SCAN_IMAGE": saved}, scan.SCAN_SECONDS)]
    assert docker.mode == 0o644
    assert not Path(saved).parent.exists()


@pytest.mark.parametrize("asked, said", [(False, "is not rootless"),
                                         (None, "could not be asked what it is")])
def test_a_scanner_daemon_not_rootless_refuses_the_scan_before_production_is_read(
        asked, said, monkeypatch):
    """No sanction lets the scanner run on another daemon than a rootless one, and the refusal
    comes before any read of production, saying how to start the rootless daemon."""
    monkeypatch.setattr(private_cluster, "endpoint", lambda: "")
    monkeypatch.setattr(private_cluster, "asked_rootless", lambda: asked)
    monkeypatch.setenv("SHEKEL_ALLOW_HOST_DOCKER", "1")
    docker = _Docker()
    with pytest.raises(scan.ScanFailed, match=f"docker's default daemon {said}: start it"):
        scan.scanned(REPO, docker)
    assert not docker.calls


@pytest.mark.usefixtures("rootless")
@pytest.mark.parametrize("image", ["", "4f19" * 16, "sha256:" + "4F19" * 16,
                                   f"ghcr.io/saltyreformed-labs/shekel@{_IMAGE}"])
def test_a_container_that_names_no_image_digest_saves_nothing(image):
    """What the inspect answers is a digest or the scan stops there."""
    docker = _Docker(image=image)
    with pytest.raises(scan.ScanFailed, match="which is no image digest"):
        scan.scanned(REPO, docker)
    assert [arguments[0] for _, arguments, _, _ in docker.calls] == ["inspect"]


@pytest.mark.usefixtures("rootless")
@pytest.mark.parametrize("failing", ["save", "compose"])
def test_the_saved_file_is_deleted_however_the_scan_fails(failing):
    """A save or a scan that fails leaves no saved image behind."""
    docker = _Docker(failing=failing)
    with pytest.raises(scan.ScanFailed, match=f"docker {failing} failed"):
        scan.scanned(REPO, docker)
    assert not _saved(docker).parent.exists()


@pytest.mark.usefixtures("rootless")
@pytest.mark.parametrize("labels", [None, {}, {scan.REVISION: "latest"},
                                    {scan.REVISION: _BUILT_FROM[:12]},
                                    {scan.REVISION: _BUILT_FROM.upper()},
                                    {scan.REVISION: 7}])
def test_a_scanned_image_naming_no_commit_is_refused(labels):
    """The commit later steps read the image's fixes at is the label's whole sha, or the scan
    is refused: dev's recorded image, built with no label, among them."""
    docker = _Docker(answer=json.dumps(_labelled(labels)).encode())
    with pytest.raises(scan.NotAScan, match="names no commit it was built from"):
        scan.scanned(REPO, docker)
    with pytest.raises(scan.NotAScan, match="names no commit it was built from"):
        scan.revision(_recorded())


@pytest.mark.usefixtures("rootless")
def test_a_scanner_answer_that_is_no_scan_is_refused():
    """What the scanner writes is judged as data: no JSON is no scan."""
    with pytest.raises(scan.NotAScan, match="it is no JSON"):
        scan.scanned(REPO, _Docker(answer=b"Error: no image"))


@pytest.mark.parametrize("lines, count", [([], 0), (['CONTAINER_NAME="${SHEKEL_CONTAINER_NAME'
                                                     ':-a}"', 'CONTAINER_NAME="${SHEKEL_'
                                                     'CONTAINER_NAME:-b}"'], 2)])
def test_production_s_container_is_the_one_the_deploy_script_names_once(lines, count, tmp_path):
    """The deploy script's default for ``SHEKEL_CONTAINER_NAME`` names the container, as the
    release reads it; a script naming none, or two, is refused."""
    assert scan.container(REPO) == "shekel-prod-app"
    (tmp_path / "deploy").mkdir()
    (tmp_path / scan.DEPLOY_SCRIPT).write_text("\n".join(["#!/usr/bin/env bash", *lines]) + "\n",
                                               encoding="utf-8")
    with pytest.raises(scan.ScanFailed, match=f"names production's container {count} times"):
        scan.container(tmp_path)


# -- the scan's docker, a real process -------------------------------------------------------

#: A stand-in ``docker`` on the path: what each command does is the script's, which records
#: the daemon it was handed, and the variable the scanner reads, for each command it runs.
_FAKE_DOCKER = """#!/bin/sh
printf '%s %s %s\\n' "$1" "$DOCKER_HOST" "$SCAN_IMAGE" >> "$SCAN_TEST_LOG"
case "$1" in
  inspect) {inspect} ;;
  save) printf 'an image' > "$3"; chmod 600 "$3" ;;
  compose) cat "$SCAN_TEST_ANSWER" ;;
esac
"""


def _on_path(tmp_path: Path, monkeypatch, inspect: str) -> Path:
    """A stand-in ``docker`` first on the path whose inspect runs ``inspect``, its log's path;
    the scanner answers a labelled copy of the recorded scan."""
    binary = tmp_path / "bin" / "docker"
    binary.parent.mkdir()
    binary.write_text(_FAKE_DOCKER.replace("{inspect}", inspect), encoding="utf-8")
    binary.chmod(0o755)
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(_labelled({scan.REVISION: _BUILT_FROM})), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SCAN_TEST_ANSWER", str(answer))
    monkeypatch.setenv("SCAN_TEST_LOG", str(tmp_path / "log"))
    monkeypatch.setenv("DOCKER_HOST", "unix:///an/inherited/daemon")
    monkeypatch.setenv("SCAN_IMAGE", "/an/inherited/file")
    return tmp_path / "log"


@pytest.mark.usefixtures("rootless")
def test_each_docker_command_runs_on_the_daemon_the_scan_names(tmp_path, monkeypatch):
    """The real docker runner: each command is handed its own daemon whatever ``DOCKER_HOST``
    this process holds, and only the scanner the saved file's path."""
    log = _on_path(tmp_path, monkeypatch, f"echo {_IMAGE}")
    done = scan.scanned(REPO)
    assert (done.image, done.revision, done.found) == (_IMAGE, _BUILT_FROM, (_CRITICAL,))
    inspect, save, run = log.read_text(encoding="utf-8").splitlines()
    assert inspect == save.replace("save", "inspect", 1) == (
        f"inspect {scan.PRODUCTION} /an/inherited/file")
    assert run.startswith(f"compose {_ROOTLESS} /") and run.endswith("/image.tar")


@pytest.mark.usefixtures("rootless")
@pytest.mark.parametrize("inspect, said", [
    ('echo "first line" >&2; echo "Error: No such object: shekel-prod-app" >&2; exit 3',
     "exited 3: Error: No such object: shekel-prod-app"),
    ("exit 1", "exited 1: it said nothing")])
def test_a_docker_command_that_fails_says_its_last_line_of_error(inspect, said, tmp_path,
                                                                 monkeypatch):
    """docker's exit status and the last line it wrote on its error stream are the failure's."""
    _on_path(tmp_path, monkeypatch, inspect)
    with pytest.raises(scan.ScanFailed, match=f"docker inspect on {scan.PRODUCTION} {said}$"):
        scan.scanned(REPO)


@pytest.mark.usefixtures("rootless")
def test_a_docker_command_that_does_not_finish_in_time_fails(tmp_path, monkeypatch):
    """A daemon that does not answer ends the scan as a failure, never a hang."""
    _on_path(tmp_path, monkeypatch, "exec sleep 5")
    monkeypatch.setattr(scan, "DOCKER_SECONDS", 1)
    with pytest.raises(scan.ScanFailed, match="did not finish within 1 s"):
        scan.scanned(REPO)
