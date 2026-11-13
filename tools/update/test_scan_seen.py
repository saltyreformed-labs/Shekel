"""What the daily image scan saw (:mod:`tools.update.scan`; rulings plan#3788, plan#3875,
plan#3876): the scanner's catalog, the image's own python's report, and the refusal of a scan
that did not see the interpreter the image runs.  The catalog read is a trimmed copy of a real
one, ``recorded/catalog.json``: eleven of the 4,964 components Grype v0.121.2 catalogued in
production's image 2026-11-13, each whole (the interpreter, the four Debian packages the
recorded scan's matches name, three Python packages of the venv, the operating system and two
of its files), with the report's own metadata and none of its vulnerabilities, so it names no
CVE (ruling plan#3841).  The recorded scan (``recorded/scan.json``) is of dev's image the same
day, whose packages it names are at the versions this catalog lists them."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.update import scan

#: The trimmed copy of a real scan, and of a real catalog.
RECORDED = Path(__file__).resolve().parent / "recorded" / "scan.json"
CATALOGUED = RECORDED.with_name("catalog.json")
#: The interpreter production's image runs, as its own python reports it (measured
#: 2026-11-13: ``3.14.8``, run from ``/usr/local/bin/python3.14``, its venv ``/opt/venv``, on
#: Linux with glibc on x86_64).
_RUNS = scan.Interpreter("3.14.8", "/usr/local/bin/python3.14", "/opt/venv", "cpython", "linux",
                         "x86_64", "glibc")
#: That report as the probe's script writes it.
_REPORT = {"version": "3.14.8", "executable": "/usr/local/bin/python3.14", "prefix": "/opt/venv",
           "implementation": "cpython", "platform": "linux", "machine": "x86_64",
           "libc": "glibc"}


def _catalog() -> dict:
    """The recorded catalog, read afresh, so a test may change it."""
    return json.loads(CATALOGUED.read_text(encoding="utf-8"))


def _written(catalog: object) -> bytes:
    """``catalog`` as the scanner writes it."""
    return json.dumps(catalog).encode()


def _component(catalog: dict, name: str) -> dict:
    """``catalog``'s one component named ``name``."""
    found, = (component for component in catalog["components"] if component["name"] == name)
    return found


def _seen(catalog: dict, interpreter: scan.Interpreter = _RUNS) -> None:
    """The recorded scan checked against ``catalog`` and ``interpreter``."""
    scanned = json.loads(RECORDED.read_text(encoding="utf-8"))
    scan.seen(scanned, scan.catalogued(_written(catalog)), interpreter)


def test_the_recorded_catalog_lists_each_package_with_the_files_it_was_found_at():
    """Each component naming its package's type, in its order, the operating system and the
    files naming none: the interpreter found at its file and its library."""
    found = scan.catalogued(CATALOGUED.read_bytes())
    assert [(item.kind, item.package, item.version) for item in found] == [
        ("python", "alembic", "1.20.0"), ("python", "gunicorn", "26.2.0"),
        ("deb", "libc-bin", "2.41-12+deb13u4"), ("deb", "libkrb5-3", "1.21.3-5+deb13u1"),
        ("deb", "openssl", "3.5.7-1~deb13u3"), ("deb", "perl", "5.40.1-6+deb13u1"),
        ("python", "psycopg-c", "3.3.6"), ("binary", "python", "3.14.8")]
    assert found[-1].paths == {"/usr/local/bin/python3.14", "/usr/local/lib/libpython3.14.so.1.0"}


def test_the_recorded_scan_saw_the_interpreter_the_image_runs():
    """Every package the recorded scan's matches name is one the catalog lists, and the
    catalog lists the interpreter at the version and file the image's python reports."""
    _seen(_catalog())


@pytest.mark.parametrize(("change", "said"), [
    (lambda catalog, python: catalog["components"].remove(python), "3.14.8"),
    (lambda catalog, python: python.update(version="3.14.9"), "3.14.8"),
    (lambda catalog, python: python.update(name="python3"), "3.14.8"),
    (lambda catalog, python: python["properties"].remove(
        {"name": "syft:package:type", "value": "binary"}), "3.14.8"),
    (lambda catalog, python: python["properties"].remove(
        {"name": "syft:location:0:path", "value": "/usr/local/bin/python3.14"}), "3.14.8"),
], ids=["not_listed", "at_another_version", "named_otherwise", "no_package",
        "not_at_its_file"])
def test_a_catalog_that_lists_not_the_interpreter_the_image_runs_is_refused(change, said):
    """Ruling plan#3788's case: Grype finds the interpreter by its binary alone, so a catalog
    without it, at another version, by another name, as no package, or found only at another
    file (its library alone) did not see it, and the scan is refused.  The matches the
    interpreter's CVEs made are left out where it is, so the refusal is the interpreter's."""
    catalog = _catalog()
    change(catalog, _component(catalog, "python"))
    scanned = json.loads(RECORDED.read_text(encoding="utf-8"))
    scanned["matches"] = [match for match in scanned["matches"]
                          if match["artifact"]["type"] != "binary"]
    with pytest.raises(scan.NotAScan, match=(
            f"lists no binary package python at {said} found at /usr/local/bin/python3.14, the "
            "interpreter the image runs, so the scan did not see it")):
        scan.seen(scanned, scan.catalogued(_written(catalog)), _RUNS)


@pytest.mark.parametrize("interpreter", [replace(_RUNS, version="3.14.9"),
                                         replace(_RUNS, executable="/usr/bin/python3")],
                         ids=["another_version", "another_file"])
def test_an_interpreter_the_catalog_does_not_list_is_refused(interpreter):
    """The image's python reporting another version, or another file, than the catalog's
    interpreter: refused, naming what the image runs."""
    with pytest.raises(scan.NotAScan, match=(
            f"no binary package python at {interpreter.version} found at "
            f"{interpreter.executable}")):
        _seen(_catalog(), interpreter)


@pytest.mark.parametrize("change", [
    lambda catalog: catalog["components"].remove(_component(catalog, "perl")),
    lambda catalog: _component(catalog, "openssl").update(version="3.5.7-1~deb13u4"),
], ids=["not_listed", "at_another_version"])
def test_a_match_whose_package_the_catalog_does_not_list_is_refused(change):
    """The matches were made of the catalog the same run wrote, or the scan is refused: a
    package a match names missing from it, or at another version."""
    catalog = _catalog()
    change(catalog)
    with pytest.raises(scan.NotAScan, match=r"match \d's package is none the scanner's catalog "
                                            "lists"):
        _seen(catalog)


def test_a_package_whose_version_grype_left_out_is_read_as_an_empty_one():
    """CycloneDX's version is omitted when empty, as Grype writes a match's empty."""
    catalog = _catalog()
    del _component(catalog, "alembic")["version"]
    assert scan.catalogued(_written(catalog))[0].version == ""


@pytest.mark.parametrize(("change", "said"), [
    (lambda catalog: catalog.update(bomFormat="SPDX"), "is no CycloneDX report"),
    (lambda catalog: catalog.pop("bomFormat"), "is no CycloneDX report"),
    (lambda catalog: catalog.update(components={}), "the catalog holds no components"),
    (lambda catalog: catalog["components"].append([]), "component 11 is no object"),
    (lambda catalog: _component(catalog, "perl").update(properties={}), "properties are not"),
    (lambda catalog: _component(catalog, "perl")["properties"].append({"name": "x"}),
     "properties are not"),
    (lambda catalog: _component(catalog, "perl")["properties"].append(
        {"name": "x", "value": 1}), "properties are not"),
    (lambda catalog: _component(catalog, "perl")["properties"].append(
        {"name": "syft:package:type", "value": "binary"}), "names its package's type 2 times"),
    (lambda catalog: _component(catalog, "perl").update(version=5), "version is no string"),
    (lambda catalog: _component(catalog, "perl").pop("name"), "component 5 holds no name"),
], ids=["another_format", "no_format", "components_no_list", "a_component_no_object",
        "properties_no_list", "a_property_no_value", "a_value_no_string", "two_types",
        "a_version_no_string", "a_package_no_name"])
def test_a_catalog_not_as_grype_writes_one_is_refused(change, said):
    """Read as data: a catalog of another shape is refused whole, never read as one listing
    less."""
    catalog = _catalog()
    change(catalog)
    with pytest.raises(scan.NotAScan, match=said):
        scan.catalogued(_written(catalog))


def test_a_catalog_that_is_no_json_is_refused():
    """An empty file, the one the scan made, left so by a scanner that wrote nothing."""
    for written in (b"", b"[1,", b'{"bomFormat": "\xff"}'):
        with pytest.raises(scan.NotAScan, match="the scanner's catalog: it is no JSON"):
            scan.catalogued(written)
    with pytest.raises(scan.NotAScan, match="is no CycloneDX report"):
        scan.catalogued(b"[]")


def test_the_image_s_report_reads_as_its_version_file_venv_and_platform():
    """The report the probe's script writes, one line of JSON."""
    assert scan.reported(json.dumps(_REPORT).encode() + b"\n") == _RUNS


def test_a_c_library_the_image_names_none_of_is_read_as_an_empty_one():
    """``platform.libc_ver`` names none where it finds none (a musl image's, say), which is
    read as it stands, for the platform uv is told to refuse (``test_scan_lock.py``)."""
    assert scan.reported(json.dumps({**_REPORT, "libc": ""}).encode()) == replace(_RUNS, libc="")


def _without(key: str) -> bytes:
    """The report with no ``key``."""
    return json.dumps({name: value for name, value in _REPORT.items() if name != key}).encode()


def _with(**values: object) -> bytes:
    """The report with ``values`` in place of its own."""
    return json.dumps({**_REPORT, **values}).encode()


@pytest.mark.parametrize(("report", "said"), [
    (b"Traceback (most recent call last):", "report: it is no JSON"),
    (b"[]", "report holds no executable"),
    *((_without(key), f"report holds no {key}") for key in _REPORT),
    (_with(version="3.14.8 <!-- -->"), "report's version is"),
    (_with(version=3.14), "report holds no version"),
    (_with(executable="python3.14"), "report names a file it runs from that"),
    (_with(executable="/usr/local/bin/python 3"), "report names a file it runs from that"),
    (_with(executable="/"), "report names a file it runs from that"),
    (_with(prefix="venv"), "report names a venv that"),
    (_with(prefix="/opt/venv/"), "report names a venv that"),
    (_with(implementation="c`python`"), "report's implementation is"),
    (_with(platform=""), "report's platform is"),
    (_with(machine="x86 64"), "report's machine is"),
    (_with(libc=None), "report holds no libc"),
], ids=["no_json", "no_object", *(f"no_{key}" for key in _REPORT), "a_version_unlistable",
        "a_version_no_string", "a_relative_file", "a_file_with_a_space", "the_root",
        "a_relative_venv", "a_venv_ending_in_a_slash", "an_implementation_unlistable",
        "no_platform_named", "a_machine_unlistable", "a_libc_no_string"])
def test_a_report_not_as_the_probe_writes_one_is_refused(report, said):
    """Read as data, as the scanner's answer is: a report the probe's script does not write is
    refused, never read as an interpreter seen."""
    with pytest.raises(scan.NotAScan, match=f"the image's python's {said}"):
        scan.reported(report)
