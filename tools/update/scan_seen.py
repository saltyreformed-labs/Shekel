"""What the daily image scan saw (:mod:`tools.update.scan`, its paragraph on what it saw;
rulings plan#3788, plan#3875, plan#3876): the scanner's catalog of every package it read
(:func:`catalogued`), the image's own python's report of itself (:func:`reported`), and
the refusal of a scan whose matches are not of that catalog or that did not see the
interpreter (:func:`seen`).  It imports the standard library alone (ruling plan#3825)."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tools.update._scan_data import NotAScan, _listable, _value, parsed

#: What the catalog says it is: Grype's catalog is a CycloneDX report (ruling plan#3875).
CATALOG_FORMAT = "CycloneDX"
#: The property of a catalog's component naming its package's type, as Grype's type names it
#: (``deb``, ``python``, ``binary``); a component naming none is a file or the operating
#: system, no package (measured 2026-11-13 on production's image, Grype v0.121.2: 147 of its
#: 4,964 components name one, each once).
_KIND = "syft:package:type"
#: The properties naming each file a package was found at, numbered from 0.
_LOCATION = re.compile(r"syft:location:[0-9]+:path")
#: The interpreter as Grype catalogues it, by its type and name: the package its binary
#: classifier finds at the interpreter's file (measured 2026-11-13 on production's image).
INTERPRETER = ("binary", "python")
#: What the image's own python runs (the probe of ``docker-compose.scan.yml``, ruling
#: plan#3876): one line of JSON, its version, the real path of the file it runs from, its venv
#: (``sys.prefix``), and what the lock's conditions test of its platform: its implementation,
#: operating system and machine, with its C library, which names the platform uv is told.
REPORT = ("import json, os, platform, sys; print(json.dumps({"
          "'version': platform.python_version(), "
          "'executable': os.path.realpath(sys.executable), 'prefix': sys.prefix, "
          "'implementation': sys.implementation.name, 'platform': sys.platform, "
          "'machine': platform.machine(), 'libc': platform.libc_ver()[0]}))")
#: A file the report names: an absolute path whose every part
#: :data:`tools.update._scan_data._LISTABLE` could list.
_EXECUTABLE = re.compile(r"(?:/[A-Za-z0-9._+~-]+)+")


@dataclass(frozen=True)
class Catalogued:
    """One package Grype's catalog lists (:func:`catalogued`): its ``kind`` (Grype's type, as
    :class:`tools.update.scan.Counted`'s), its ``package`` name, its ``version`` and ``paths``,
    each file it was found at."""

    kind: str
    package: str
    version: str
    paths: frozenset[str]


@dataclass(frozen=True)
class Interpreter:
    """What the image's own python reports of itself (:func:`reported`): its ``version``;
    ``executable``, the real path of the file it runs from; ``prefix``, its venv; and its
    ``implementation`` (``sys.implementation.name``), ``platform`` (``sys.platform``),
    ``machine`` (``platform.machine``) and ``libc``, its C library (``platform.libc_ver``'s,
    empty where it names none)."""

    version: str
    executable: str
    prefix: str
    implementation: str
    platform: str
    machine: str
    libc: str


def catalogued(written: bytes) -> tuple[Catalogued, ...]:
    """Each package the catalog the scanner ``written`` lists (its CycloneDX report, ruling
    plan#3875), in its order: each component naming its package's type (:data:`_KIND`), with
    each file it was found at.  A version Grype leaves out (CycloneDX's is omitted when empty)
    is read as an empty one, as Grype writes a match's.

    Raises:
        NotAScan: ``written`` is no catalog as Grype writes one: no JSON, no CycloneDX report,
            or a component, or a package's name, version or properties, not as it writes them.
    """
    try:
        catalog = parsed(written)
    except NotAScan as error:
        raise NotAScan(f"the scanner's catalog: {error}") from error
    if not isinstance(catalog, dict) or catalog.get("bomFormat") != CATALOG_FORMAT:
        raise NotAScan(f"the scanner's catalog is no {CATALOG_FORMAT} report")
    found = []
    for index, component in enumerate(_value(catalog, "components", list, "the catalog")):
        where = f"the catalog's component {index}"
        if not isinstance(component, dict):
            raise NotAScan(f"{where} is no object, which Grype does not write")
        properties = component.get("properties", [])
        if not (isinstance(properties, list) and all(
                isinstance(item, dict) and isinstance(item.get("name"), str)
                and isinstance(item.get("value"), str) for item in properties)):
            raise NotAScan(f"{where}'s properties are not as Grype writes them")
        kinds = [item["value"] for item in properties if item["name"] == _KIND]
        if len(kinds) > 1:
            raise NotAScan(f"{where} names its package's type {len(kinds)} times")
        if not kinds:
            continue
        if not isinstance(version := component.get("version", ""), str):
            raise NotAScan(f"{where}'s version is no string, which Grype does not write")
        found.append(Catalogued(kinds[0], _value(component, "name", str, where), version,
                                frozenset(item["value"] for item in properties
                                          if _LOCATION.fullmatch(item["name"]))))
    return tuple(found)


def reported(answer: bytes) -> Interpreter:
    """What the image's own python reports of itself, its probe's ``answer`` (:data:`REPORT`,
    ruling plan#3876).  Its C library is read as it stands, an empty one too, which
    :func:`tools.update.scan.told` refuses.

    Raises:
        NotAScan: ``answer`` is no report as the probe writes it, or names a version, a file, a
            venv, an implementation, an operating system or a machine that cannot be listed.
    """
    where = "the image's python's report"
    try:
        report = parsed(answer)
    except NotAScan as error:
        raise NotAScan(f"{where}: {error}") from error
    if not _EXECUTABLE.fullmatch(executable := _value(report, "executable", str, where)):
        raise NotAScan(f"{where} names a file it runs from that cannot be listed")
    if not _EXECUTABLE.fullmatch(prefix := _value(report, "prefix", str, where)):
        raise NotAScan(f"{where} names a venv that cannot be listed")
    return Interpreter(_listable(_value(report, "version", str, where), f"{where}'s version"),
                       executable, prefix,
                       *(_listable(_value(report, key, str, where), f"{where}'s {key}")
                         for key in ("implementation", "platform", "machine")),
                       _value(report, "libc", str, where))


def seen(scan: object, catalog: Sequence[Catalogued], interpreter: Interpreter) -> None:
    """Refuse ``scan``, Grype's JSON, unless it saw what the image runs
    (:mod:`tools.update.scan`, its paragraph on what it saw): every package a match names, by
    its type, name and version, is one ``catalog`` lists, so the matches were made of that
    catalog; and ``catalog`` lists the interpreter (:data:`INTERPRETER`) at ``interpreter``'s
    version, found at the file it runs from.

    Raises:
        NotAScan: a match names a package the catalog does not list, or the catalog does not
            list the interpreter so.
    """
    listed = {(item.kind, item.package, item.version) for item in catalog}
    for index, match in enumerate(_value(scan, "matches", list, "the scan")):
        artifact = _value(match, "artifact", dict, f"match {index}")
        if tuple(artifact.get(key) for key in ("type", "name", "version")) not in listed:
            raise NotAScan(f"match {index}'s package is none the scanner's catalog lists, so the "
                           "catalog is not the one its matches were made of")
    kind, name = INTERPRETER
    if not any((item.kind, item.package, item.version) == (kind, name, interpreter.version)
               and interpreter.executable in item.paths for item in catalog):
        raise NotAScan(f"the scanner's catalog lists no {kind} package {name} at "
                       f"{interpreter.version} found at {interpreter.executable}, the "
                       "interpreter the image runs, so the scan did not see it")
