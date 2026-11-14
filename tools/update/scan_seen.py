"""What the daily image scan saw (:mod:`tools.update.scan`, its paragraph on what it saw;
rulings plan#3788, plan#3875, plan#3876, plan#4063): the scanner's catalog of every package it
read (:func:`catalogued`), the image's own python's report of itself and of the Debian it runs
on (:func:`reported`), dpkg's status file read as dpkg writes it (:func:`installed`), and the
refusal of a scan whose matches are not of that catalog or that did not see the interpreter
(:func:`seen`), or that did not read the Debian release and packages the image runs
(:func:`seen_debian`).  It imports the standard library alone (ruling plan#3825)."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tools.update._scan_data import NotAScan, _apart, _listable, _value, parsed

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
#: The type Grype gives each package its catalog read from dpkg's status file.
DEB = "deb"
#: The file dpkg records each package's state in, from which Grype's catalog reads the image's
#: Debian packages (measured 2026-11-14 on the base image the Dockerfile pins: its 87 stanzas,
#: each installed, are Grype's 87 :data:`DEB` packages, by name and version).
STATUS = "/var/lib/dpkg/status"
#: What the image's own python runs (the probe of ``docker-compose.scan.yml``, rulings
#: plan#3876, plan#4063): one line of JSON, its version, the real path of the file it runs
#: from, its venv (``sys.prefix``), and what the lock's conditions test of its platform: its
#: implementation, operating system and machine, with its C library, which names the platform
#: uv is told; then the release os-release names, its ``ID`` and ``VERSION_ID``, and the text
#: of dpkg's status file (:data:`STATUS`), which the scan reads on its own side
#: (:func:`installed`), so what the image wrote is judged by code its tests grade.
REPORT = ("import json, os, platform, sys; print(json.dumps({"
          "'version': platform.python_version(), "
          "'executable': os.path.realpath(sys.executable), 'prefix': sys.prefix, "
          "'implementation': sys.implementation.name, 'platform': sys.platform, "
          "'machine': platform.machine(), 'libc': platform.libc_ver()[0], "
          "'release': {key: value for key, value in platform.freedesktop_os_release().items() "
          "if key in ('ID', 'VERSION_ID')}, "
          f"'dpkg': open({STATUS!r}, encoding='utf-8').read()}}))")
#: A file the report names: an absolute path whose every part
#: :data:`tools.update._scan_data._LISTABLE` could list.
_EXECUTABLE = re.compile(r"(?:/[A-Za-z0-9._+~-]+)+")
#: The words of a stanza's ``Status`` as dpkg writes them, one space apart: what is wanted of
#: the package, its flag and its state, each word as dpkg 1.22.22 reads it (measured
#: 2026-11-14 in the base image the Dockerfile pins: ``dpkg-query`` read each of these and
#: refused another word in each place).
_WANTS = frozenset({"unknown", "install", "hold", "deinstall", "purge"})
_FLAGS = frozenset({"ok", "reinstreq"})
_STATES = frozenset({"not-installed", "config-files", "half-installed", "unpacked",
                     "half-configured", "triggers-awaited", "triggers-pending", "installed"})
#: The state of a package dpkg holds installed.
INSTALLED = "installed"
#: A stanza's line opening a field as dpkg writes it: its name, a colon, then a space and its
#: value, or nothing where its value opens on the next line (``Conffiles``).  A line after it
#: opening with a space continues its value.
_FIELD = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9-]*):(?: (?P<value>.*))?")


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


@dataclass(frozen=True)
class Release:
    """The Debian the image runs, as its own python reports it (:func:`reported`): ``distro``
    and ``version``, the release its os-release names, by its ``ID`` and ``VERSION_ID``; and
    ``installed``, each package dpkg's status file lists as installed (:func:`installed`), by
    its name and version."""

    distro: str
    version: str
    installed: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class Reported:
    """What the image's own python reports (:func:`reported`): the ``interpreter`` it is, and
    the ``release`` of Debian it runs on."""

    interpreter: Interpreter
    release: Release


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


def installed(status: str) -> frozenset[tuple[str, str]]:
    """Each package ``status``, the text of dpkg's status file (:data:`STATUS`), lists as
    installed, by its name and its version: each stanza whose state is :data:`INSTALLED`,
    whatever is wanted of the package.  Grype's catalog lists a package in every state but
    ``config-files`` (measured 2026-11-14: one half configured, unpacked, its triggers pending,
    and one not installed at all, each listed), so a catalog listing one in another state is
    refused (:func:`seen_debian`), as one listing a package dpkg does not list at all is.

    Read as dpkg writes the file: each stanza a field to a line (:data:`_FIELD`), each field
    once, a value continued on the lines after it, each opening with a space, and each stanza
    ended by an empty line; its ``Package`` named, and its ``Status`` three words, one space
    apart, that dpkg writes (:data:`_WANTS`, :data:`_FLAGS`, :data:`_STATES`).

    Raises:
        NotAScan: ``status`` is not as dpkg writes it, or names an installed package, or its
            version, that cannot be listed; the message names the stanza, counted from 0.
    """
    where = "the image's dpkg status file"
    if not status.endswith("\n\n"):
        raise NotAScan(f"{where} does not end its last stanza with an empty line, as dpkg "
                       "writes it")
    found = set()
    for index, stanza in enumerate(status.split("\n\n")[:-1]):
        at = f"{where}'s stanza {index}"
        fields: dict[str, str] = {}
        for line in stanza.split("\n"):
            if fields and line.startswith(" "):
                continue
            field = _FIELD.fullmatch(line)
            if field is None or field["name"] in fields:
                raise NotAScan(f"{at} holds the line {line!r}, which dpkg does not write there")
            fields[field["name"]] = field["value"] or ""
        if not fields.get("Package"):
            raise NotAScan(f"{at} names no package, which dpkg does not write")
        words = fields.get("Status", "").split(" ")
        if not (len(words) == 3 and words[0] in _WANTS and words[1] in _FLAGS
                and words[2] in _STATES):
            raise NotAScan(f"{at}'s status is {fields.get('Status')!r}, which dpkg does not "
                           "write")
        if words[2] == INSTALLED:
            found.add((_listable(fields["Package"], f"{at}'s package"),
                       _listable(fields.get("Version"), f"{at}'s version")))
    return frozenset(found)


def reported(answer: bytes) -> Reported:
    """What the image's own python reports, its probe's ``answer`` (:data:`REPORT`, rulings
    plan#3876, plan#4063): the interpreter it is, and the Debian release it runs and the
    packages dpkg lists as installed there (:func:`installed`).  Its C library is read as it
    stands, an empty one too, which :func:`tools.update.scan.told` refuses.

    Raises:
        NotAScan: ``answer`` is no report as the probe writes it, or names a version, a file, a
            venv, an implementation, an operating system, a machine or a release that cannot
            be listed, or a status file not as dpkg writes it (:func:`installed`).
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
    release = _value(report, "release", dict, where)
    return Reported(
        Interpreter(_listable(_value(report, "version", str, where), f"{where}'s version"),
                    executable, prefix,
                    *(_listable(_value(report, key, str, where), f"{where}'s {key}")
                      for key in ("implementation", "platform", "machine")),
                    _value(report, "libc", str, where)),
        Release(*(_listable(_value(release, key, str, f"{where}'s release"),
                            f"{where}'s release's {key}") for key in ("ID", "VERSION_ID")),
                installed(_value(report, "dpkg", str, where))))


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


def seen_debian(scan: object, catalog: Sequence[Catalogued], release: Release) -> None:
    """Refuse ``scan``, Grype's JSON, unless it read the Debian the image runs (ruling
    plan#4063): its ``distro`` names ``release``'s, os-release's ``ID`` at its ``VERSION_ID``,
    and the Debian packages ``catalog`` lists (:data:`DEB`) are ``release``'s installed ones
    exactly, each by its name and its version: none missing, none at another version, and
    none dpkg does not list as installed.  A scan that stopped recognizing the release would
    match none of Debian's records and read clean.  Grype writes the release with the point
    release ``/etc/debian_version`` holds after it, ``13.7`` for ``VERSION_ID`` ``13``
    (measured 2026-11-14: that file set to ``13.99`` moved it, os-release's own
    ``DEBIAN_VERSION_FULL`` did not), and matches Debian's records by the release alone (the
    recorded scan's namespace ``debian:distro:debian:13``), so the point release is not read.

    Raises:
        NotAScan: the scan names no distro, or another release, or a Debian package the catalog
            lists names what cannot be listed, or the two sets differ; the message names each
            package of either set the other lacks, so a package at another version is named
            in both.
    """
    distro = _value(scan, "distro", dict, "the scan")
    name, version = distro.get("name"), distro.get("version")
    if not (name == release.distro and isinstance(version, str)
            and (version == release.version or version.startswith(f"{release.version}."))):
        raise NotAScan(f"the scan names the distro {name!r} at {version!r}, where the image "
                       f"runs {release.distro} {release.version}, so Grype did not read the "
                       "release the image runs")
    found = frozenset(
        (_listable(item.package, "the name of a Debian package the catalog lists"),
         _listable(item.version, f"the version of the Debian package {item.package} the "
                                 "catalog lists"))
        for item in catalog if item.kind == DEB)
    if said := _apart(release.installed, found, "missing from it, as dpkg lists them installed",
                      "in it, but not installed as dpkg lists it"):
        raise NotAScan("Grype's catalog of the image's Debian packages is not the set dpkg "
                       "lists as installed, so the scan did not see every package the image "
                       f"holds at its installed version: {said}")
