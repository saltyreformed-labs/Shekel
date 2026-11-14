"""The daily image scan's read of the Debian the image runs (:mod:`tools.update.scan`, its
paragraph on what it saw; ruling plan#4063): dpkg's status file read as dpkg writes it, and the
refusal of a scan whose distro is not the release the image's os-release names, or whose
catalog's Debian packages are not those dpkg lists as installed.  ``recorded/status`` is a
trimmed copy of a real status file: the stanzas of the four Debian packages the recorded
catalog lists (``recorded/catalog.json``), each whole, at the versions it lists them, from an
image the Dockerfile built 2026-11-13; ``recorded/os-release`` is that image's os-release.
The recorded scan (``recorded/scan.json``) names its distro ``debian`` ``13.7``."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.update import scan, scan_seen

#: The trimmed copies of a real scan, catalog and status file.
RECORDED = Path(__file__).resolve().parent / "recorded" / "scan.json"
CATALOGUED, STATUS = RECORDED.with_name("catalog.json"), RECORDED.with_name("status")
#: The packages the recorded status file lists as installed: the four Debian packages the
#: recorded catalog lists, at its versions.
_INSTALLED = frozenset({("libc-bin", "2.41-12+deb13u4"), ("libkrb5-3", "1.21.3-5+deb13u1"),
                        ("openssl", "3.5.7-1~deb13u3"), ("perl", "5.40.1-6+deb13u1")})
#: The Debian that image runs, as its python reports it (measured 2026-11-14: os-release's
#: ``ID`` ``debian``, ``VERSION_ID`` ``13``).
_RUNS = scan_seen.Release("debian", "13", _INSTALLED)


def _status() -> str:
    """The recorded status file's text."""
    return STATUS.read_text(encoding="utf-8")


def _catalog() -> dict:
    """The recorded catalog, read afresh, so a test may change it."""
    return json.loads(CATALOGUED.read_text(encoding="utf-8"))


def _seen(scanned: dict | None = None, catalog: dict | None = None,
          release: scan_seen.Release = _RUNS) -> None:
    """``scanned`` (the recorded scan where None) checked against ``catalog`` (the recorded
    one where None) and ``release``."""
    scanned = json.loads(RECORDED.read_text(encoding="utf-8")) if scanned is None else scanned
    written = json.dumps(_catalog() if catalog is None else catalog).encode()
    scan_seen.seen_debian(scanned, scan.catalogued(written), release)


def _with_status(old: str, new: str) -> str:
    """The recorded status file with its one ``old`` made ``new``."""
    text = _status()
    assert text.count(old) == 1
    return text.replace(old, new)


def test_the_recorded_status_file_lists_its_four_packages_installed():
    """Each stanza is installed, so each package is listed, by its name and version."""
    assert scan_seen.installed(_status()) == _INSTALLED


def test_the_recorded_scan_read_the_release_and_the_packages_the_image_runs():
    """The recorded scan names the release the image's os-release does, at its point release,
    and its catalog's Debian packages are the four the status file lists installed."""
    _seen()


@pytest.mark.parametrize("version", ["13", "13.7", "13.99"])
def test_a_distro_at_the_image_s_release_is_read_whatever_its_point_release(version):
    """Grype writes the point release ``/etc/debian_version`` holds after the release, and
    matches Debian's records by the release alone, so any point release of it, or none, is
    the release the image runs."""
    scanned = json.loads(RECORDED.read_text(encoding="utf-8"))
    scanned["distro"]["version"] = version
    _seen(scanned)


@pytest.mark.parametrize(("distro", "said"), [
    ({"name": "debian", "version": "14.1", "idLike": []}, "'debian' at '14.1'"),
    ({"name": "debian", "version": "130.2", "idLike": []}, "'debian' at '130.2'"),
    ({"name": "debian", "version": "1", "idLike": []}, "'debian' at '1'"),
    ({"name": "ubuntu", "version": "13.7", "idLike": ["debian"]}, "'ubuntu' at '13.7'"),
    ({"name": "", "version": "", "idLike": []}, "'' at ''"),
    ({"name": "debian", "idLike": []}, "'debian' at None"),
    ({"name": "debian", "version": 13.7, "idLike": []}, "'debian' at 13.7"),
], ids=["another_release", "a_release_it_opens", "a_release_opening_it", "another_distro",
        "none_recognized", "no_version", "a_version_no_string"])
def test_a_scan_that_did_not_read_the_release_the_image_runs_is_refused(distro, said):
    """Ruling plan#4063's case: Grype naming another release, a release whose digits merely
    open with or open the image's, another distro, or none, would match not the records of the
    release the image runs, and the scan is refused, naming both."""
    scanned = json.loads(RECORDED.read_text(encoding="utf-8"))
    scanned["distro"] = distro
    with pytest.raises(scan.NotAScan, match=(
            f"^the scan names the distro {said}, where the image runs debian 13, so Grype did "
            "not read the release the image runs$")):
        _seen(scanned)


def test_a_scan_that_names_no_distro_is_refused():
    """A scan without Grype's ``distro`` is no scan as Grype writes one."""
    scanned = json.loads(RECORDED.read_text(encoding="utf-8"))
    del scanned["distro"]
    with pytest.raises(scan.NotAScan, match="^the scan holds no distro that is a dict$"):
        _seen(scanned)


def _without_component(name: str) -> dict:
    """The recorded catalog without its component named ``name``."""
    catalog = _catalog()
    catalog["components"] = [item for item in catalog["components"] if item["name"] != name]
    return catalog


def _versioned(name: str, version: str) -> dict:
    """The recorded catalog with its component named ``name`` at ``version``."""
    catalog = _catalog()
    found, = (item for item in catalog["components"] if item["name"] == name)
    found["version"] = version
    return catalog


@pytest.mark.parametrize(("catalog", "release", "said"), [
    (_without_component("perl"), _RUNS,
     "missing from it, as dpkg lists them installed: perl 5.40.1-6+deb13u1"),
    (_versioned("openssl", "3.5.7-1~deb13u4"), _RUNS,
     "missing from it, as dpkg lists them installed: openssl 3.5.7-1~deb13u3; in it, but not "
     "installed as dpkg lists it: openssl 3.5.7-1~deb13u4"),
    (_catalog(), scan_seen.Release("debian", "13", _INSTALLED - {("perl", "5.40.1-6+deb13u1")}),
     "in it, but not installed as dpkg lists it: perl 5.40.1-6+deb13u1"),
], ids=["a_package_missing", "a_package_at_another_version", "a_package_dpkg_does_not_list"])
def test_a_catalog_whose_debian_packages_are_not_dpkg_s_is_refused(catalog, release, said):
    """Ruling plan#4063's case: the catalog's Debian packages are the set dpkg lists installed
    exactly, or the scan is refused, naming each package one set holds and the other lacks: one
    missing, one at another version (named in both), and one dpkg does not list installed."""
    with pytest.raises(scan.NotAScan, match=(
            "^Grype's catalog of the image's Debian packages is not the set dpkg lists as "
            "installed, so the scan did not see every package the image holds at its installed "
            f"version: {re.escape(said)}$")):
        _seen(None, catalog, release)


def test_a_debian_package_the_catalog_names_that_cannot_be_listed_is_refused():
    """A name the catalog writes is read as data, as a match's is."""
    catalog = _versioned("perl", "5.40.1`")
    with pytest.raises(scan.NotAScan, match="the version of the Debian package perl the catalog "
                                            "lists is '5.40.1`', which cannot be listed"):
        _seen(None, catalog)


@pytest.mark.parametrize(("status", "counted"), [
    ("install ok installed", True),
    ("hold ok installed", True),
    ("deinstall ok installed", True),
    ("purge reinstreq installed", True),
    ("unknown ok installed", True),
    ("install ok half-configured", False),
    ("install ok unpacked", False),
    ("install ok half-installed", False),
    ("install ok triggers-pending", False),
    ("install ok triggers-awaited", False),
    ("deinstall ok config-files", False),
    ("purge ok not-installed", False),
])
def test_a_package_is_listed_installed_where_dpkg_s_state_is_installed(status, counted):
    """Installed is dpkg's state, whatever is wanted of the package and whatever its flag: a
    package held, or selected for removal and not yet removed, is installed; one in any other
    state is not, though Grype's catalog lists it in each but ``config-files`` (measured
    2026-11-14), which a scan is then refused for."""
    text = _with_status("Package: perl\nStatus: install ok installed\n",
                        f"Package: perl\nStatus: {status}\n")
    perl = {("perl", "5.40.1-6+deb13u1")}
    assert scan_seen.installed(text) == (_INSTALLED if counted else _INSTALLED - perl)


def test_a_package_in_another_state_that_grype_catalogued_refuses_the_scan():
    """A package half configured, which Grype's catalog lists (measured 2026-11-14), is one dpkg
    does not list installed, so the scan is refused, naming it."""
    text = _with_status("Package: perl\nStatus: install ok installed\n",
                        "Package: perl\nStatus: install ok half-configured\n")
    with pytest.raises(scan.NotAScan, match="in it, but not installed as dpkg lists it: perl "
                                            "5.40.1-6"):
        _seen(None, None, scan_seen.Release("debian", "13", scan_seen.installed(text)))


@pytest.mark.parametrize(("status", "said"), [
    (_status()[:-1], "does not end its last stanza with an empty line"),
    ("", "does not end its last stanza with an empty line"),
    (_status().replace("\n\nPackage: perl\n", "\n\n\nPackage: perl\n"),
     "stanza 3 holds the line '', which dpkg does not write there"),
    (_with_status("Package: libkrb5-3\n", ""), "stanza 1 names no package"),
    (_with_status("Package: libkrb5-3\n", "Package: \n"), "stanza 1 names no package"),
    (_with_status("Package: openssl\nStatus: install ok installed\n", "Package: openssl\n"),
     "stanza 2's status is None"),
    (_with_status("Package: perl\nStatus: install ok installed\n",
                  "Package: perl\nStatus: install ok\n"), "stanza 3's status is 'install ok'"),
    (_with_status("Package: perl\nStatus: install ok installed\n",
                  "Package: perl\nStatus: install  ok installed\n"),
     "stanza 3's status is 'install  ok installed'"),
    (_with_status("Package: perl\nStatus: install ok installed\n",
                  "Package: perl\nStatus: bogus ok installed\n"), "stanza 3's status is 'bogus"),
    (_with_status("Package: perl\nStatus: install ok installed\n",
                  "Package: perl\nStatus: install hold installed\n"),
     "stanza 3's status is 'install hold"),
    (_with_status("Package: perl\nStatus: install ok installed\n",
                  "Package: perl\nStatus: install ok bogus\n"),
     "stanza 3's status is 'install ok bogus'"),
    (_with_status("Section: libs\nInstalled-Size: 2122\n", "Section libs\nInstalled-Size: 2122\n"),
     "stanza 0 holds the line 'Section libs'"),
    (_with_status("Package: perl\n", "Package: perl\nPackage: perl\n"),
     "stanza 3 holds the line 'Package: perl'"),
    (_with_status("Package: libc-bin\n", " Package: libc-bin\n"),
     "stanza 0 holds the line ' Package: libc-bin'"),
    (_with_status("Package: libc-bin\n", "Package: libc-bin\n\tcontinued\n"),
     "stanza 0 holds the line '\\\\tcontinued'"),
    (_with_status("Version: 5.40.1-6+deb13u1\n", ""),
     "stanza 3's version is None, which cannot be listed"),
    (_with_status("Package: perl\n", "Package: perl`\n"),
     "stanza 3's package is 'perl`', which cannot be listed"),
], ids=["no_last_empty_line", "empty", "an_empty_stanza", "no_package", "a_package_named_empty",
        "no_status", "a_status_of_two_words", "a_status_spaced_twice", "a_want_dpkg_lacks",
        "a_flag_dpkg_lacks", "a_state_dpkg_lacks", "a_line_no_field", "a_field_twice",
        "a_continuation_first", "a_continuation_by_tab", "an_installed_package_no_version",
        "a_name_unlistable"])
def test_a_status_file_not_as_dpkg_writes_it_is_refused(status, said):
    """Read as data: a status file dpkg does not write is refused whole, never read as one
    listing fewer packages."""
    with pytest.raises(scan.NotAScan, match=f"^the image's dpkg status file('s)? {said}"):
        scan_seen.installed(status)


def _report(**values: object) -> bytes:
    """The probe's report of the recorded image, with ``values`` in place of its own."""
    return json.dumps({"version": "3.14.8", "executable": "/usr/local/bin/python3.14",
                       "prefix": "/opt/venv", "implementation": "cpython", "platform": "linux",
                       "machine": "x86_64", "libc": "glibc",
                       "release": {"ID": "debian", "VERSION_ID": "13"}, "dpkg": _status(),
                       **values}).encode()


def test_the_image_s_report_reads_as_its_release_and_its_installed_packages():
    """os-release's ``ID`` and ``VERSION_ID``, and the packages its status file lists
    installed."""
    assert scan.reported(_report()).release == _RUNS


@pytest.mark.parametrize(("report", "said"), [
    (_report(release={"ID": "debian"}), "report's release holds no VERSION_ID that is a str"),
    (_report(release={"VERSION_ID": "13"}), "report's release holds no ID that is a str"),
    (_report(release={"ID": "debian", "VERSION_ID": 13}),
     "report's release holds no VERSION_ID that is a str"),
    (_report(release={"ID": "de`bian", "VERSION_ID": "13"}),
     "report's release's ID is 'de`bian', which cannot be listed"),
    (_report(release=["debian", "13"]), "report holds no release that is a dict"),
    (_report(dpkg=None), "report holds no dpkg that is a str"),
], ids=["no_version_id", "no_id", "a_version_id_no_string", "an_id_unlistable",
        "a_release_no_object", "a_status_file_no_text"])
def test_a_report_of_the_release_not_as_the_probe_writes_one_is_refused(report, said):
    """Read as data: the release a report names, its two keys each a string that can be
    listed, and the status file's text, or the report is refused."""
    with pytest.raises(scan.NotAScan, match=f"^the image's python's {said}"):
        scan.reported(report)


def test_a_report_whose_status_file_dpkg_does_not_write_is_refused():
    """The status file the report holds is read as dpkg writes it (:func:`installed`)."""
    with pytest.raises(scan.NotAScan, match="^the image's dpkg status file does not end"):
        scan.reported(_report(dpkg=_status()[:-1]))


def test_a_scan_of_an_image_reporting_another_release_is_refused_before_uv_runs(monkeypatch):
    """The scan itself refuses what the image's python reports differing from what Grype read
    (ruling plan#4063), before the lock's set is worked out: its read, the probe's report, the
    scanner's answer and its catalog, is the recorded scan's, but for the release reported."""
    report = scan.reported(_report(release={"ID": "debian", "VERSION_ID": "14"}))
    monkeypatch.setattr(scan, "_rootless", lambda: "unix:///run/user/1000/docker.sock")
    monkeypatch.setattr(scan, "container", lambda root: "shekel-prod-app")
    monkeypatch.setattr(scan, "_read", lambda root, docker, scanner, image: (
        report, RECORDED.read_bytes(), scan.catalogued(CATALOGUED.read_bytes())))
    ran = []
    with pytest.raises(scan.NotAScan, match="where the image runs debian 14, so Grype did not "):
        scan.scanned(RECORDED.parent, lambda *_: b"sha256:" + b"4f19" * 16,
                     lambda *called: ran.append(called) or b"")
    assert not ran
