"""The daily scan of the image production is running (plan#2444; rulings plan#3786, plan#3820,
plan#3822, plan#3825), and its judge (ruling plan#3720).  Its findings stay off GitHub: no
issue, log or artifact there holds them, as an open security finding is never public (ruling
plan#3785).  ``quill scan`` runs it (:func:`scanned`) and says what it found.

**The scan reads production and writes nothing there** (ruling plan#3822).  Production's app
container runs on the system daemon (:data:`PRODUCTION`), under the name the deploy script
gives it (:func:`container`), and the scan's only acts on that daemon are reads: the inspect
that names the image the container runs, by its digest, and the save of that image to a file
of the scan's own.  The scanner, ``docker-compose.scan.yml``'s Grype, then reads the file on
the developer's rootless daemon, once that daemon says it is rootless
(``scripts/private_cluster.py``'s ``asked_rootless``, the one spelling of that question), so
nothing the scan starts, no container, network or scanner image, lands on production's
daemon; and the scanner holds that file and the one it writes its catalog to alone, no socket
(rulings plan#3706, plan#3875).  The image's own python runs on that daemon too, in a copy of
the file loaded there and removed however the scan ends (ruling plan#3876).  The commit the
image was built from is read from the scanner's own record of the file it scanned
(:func:`revision`), so the commit and the findings are one read of the same bytes.  Measured
2026-11-13 on production's image: the save, a 98 MB archive, in 0.8 s; the load in 2 s and
the image's python's run in 2 s; and the scan, its database downloaded fresh, in 42 s.

**What it saw** (rulings plan#3788, plan#3875, plan#3876).  Grype's JSON lists only what it
matched, so an interpreter with no CVE leaves no trace there.  So the scanner's one run writes
too its catalog of every package it read (:func:`catalogued`), and the image's own python
reports its version and the file it runs from (:func:`reported`).  :func:`seen` refuses the
scan unless every package a match names is one the catalog lists, so the findings are of that
catalog, and unless the catalog lists the interpreter at that version, found at that file:
Grype finds the interpreter by its binary alone, and a scan that stopped finding it would read
clean without having looked.

**The one judge.**  A match counts when Grype knows its fix (its ``fix.state`` is ``fixed``) and
the highest rating any source gives it is HIGH or CRITICAL (ruling plan#3720): the match's own,
which for a Debian package is Debian's, or that of any record related to it, NVD's among them.
A match Grype marks known exploited, on its own record or on a related one, counts at any
rating, with a fix or without one (ruling plan#3819).  Grype marks it from CISA's list of CVEs
known to be exploited, which its database holds as the provider :data:`KEV`; a scan whose
database names no such provider could mark nothing, so it is refused, never read as one in
which nothing is exploited.  Each match counted is one :class:`Counted` for each package it
was found in, its CVE as Grype names it.

**Read as data.**  What a package's name and version say is written by what the image holds.
So :func:`judged` refuses the scan whole (:class:`NotAScan`) where it is not the shape Grype
writes, where a fix state, a mark or a rating deciding a match is one Grype does not write (read
at its pinned v0.121.2, :data:`FIX_STATES`, :func:`_exploited`, :data:`RATINGS`), where a match
counted for its fix names no version it is fixed in, and where a value a counted match names
holds a character outside :data:`_LISTABLE`: none can then close a code span it is quoted in,
mention anyone, or spell a marker that ``quill sync`` reads.  The catalog and the report are
read so too, and refused where they are not as Grype and the scan's own probe write them.  A
scan refused is never read as a clean one.

**It imports the standard library alone**, with :mod:`tools.update.app` and
``scripts/private_cluster.py``, which do too, so it runs on an interpreter given no installed
package (ruling plan#3825).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts import private_cluster
from tools.update.app import marker

#: Grype's ratings, lowest first, as it reads each, ignoring case: ``ParseSeverity``, which reads
#: any other spelling as unknown (``grype/vulnerability/severity.go``, read 2026-11-13 at
#: v0.121.2).  A rating may be missing (its JSON field is ``omitempty``,
#: ``grype/presenter/models/vulnerability_metadata.go``), which Grype reads as unknown too; any
#: other spelling deciding a match is refused, as a rating this scale does not hold, ``Important``
#: say, could be a HIGH one read as nothing.
RATINGS = ("unknown", "negligible", "low", "medium", "high", "critical")
#: The lowest rating that turns the scan red (ruling plan#3720).
RED = RATINGS.index("high")
#: The fix state of a match whose fix Grype knows.
FIXED = "fixed"
#: Every fix state Grype writes: the four of ``AllFixStates`` (``grype/vulnerability/fix.go``,
#: read 2026-11-13 at v0.121.2), and none, which it writes for a match whose record holds no
#: metadata (``grype/presenter/models/vulnerability.go``'s ``NewVulnerability``).
FIX_STATES = frozenset({FIXED, "not-fixed", "wont-fix", "unknown", ""})
#: The provider of Grype's database that holds CISA's list of CVEs known to be exploited, as the
#: scan's ``descriptor.db.providers`` names each provider the database was built from
#: (``cmd/grype/cli/commands/root.go``'s ``dbInfo``, read 2026-11-13 at v0.121.2; Grype writes
#: no provider at all where it could not read them).
KEV = "kev"
#: What each value a counted match names may hold: letters and digits, then those and the marks
#: the names and versions of Debian's packages, of PEP 440 and of Go's modules use.  No space,
#: backtick, ``<``, ``>`` or ``@``, so nothing it holds can end a code span it is quoted in,
#: mention anyone or spell a marker; and no comma, which would part a list of versions.
_LISTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:!/-]*")

#: The daemon production runs on: the system one, at docker's own default socket, where the
#: deploy script, which hands docker no daemon, rolls production's container.
PRODUCTION = "unix:///var/run/docker.sock"
#: The deploy script, whose default for ``SHEKEL_CONTAINER_NAME`` names production's app
#: container: the line the release reads that name from too (``.claude/skills/release/
#: SKILL.md``, its step 1).
DEPLOY_SCRIPT = "deploy/shekel-deploy.sh"
#: That line, the name in it as docker allows a container's (a letter or digit, then those and
#: ``_``, ``.`` and ``-``).
_CONTAINER = re.compile(r'^CONTAINER_NAME="\$\{SHEKEL_CONTAINER_NAME:-'
                        r'(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)\}"$', re.MULTILINE)
#: The scanner's compose file (ruling plan#3706), from the checkout's root.
COMPOSE = "docker-compose.scan.yml"
#: An image's digest as docker names the image a container runs.
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
#: The label naming the commit an image was built from, which ``docker/metadata-action`` writes
#: (``.github/workflows/docker-publish.yml``; ruling plan#3786).
REVISION = "org.opencontainers.image.revision"
#: A commit as that label names it: its whole sha.
_COMMIT = re.compile(r"[0-9a-f]{40}")
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
#: plan#3876): one line of JSON, its version and the real path of the file it runs from.
REPORT = ("import json, os, platform, sys; print(json.dumps({'version': "
           "platform.python_version(), 'executable': os.path.realpath(sys.executable)}))")
#: A file the report names: an absolute path whose every part :data:`_LISTABLE` could list.
_EXECUTABLE = re.compile(r"(?:/[A-Za-z0-9._+~-]+)+")
#: The longest an inspect, a save, a load, the probe's run or a removal may take: the save of
#: production's image took 0.8 s, its load 2 s and the probe 2 s (2026-11-13), so a daemon still
#: busy after this is a daemon that is not answering.
DOCKER_SECONDS = 120
#: The longest the scanner may take: 42 s on 2026-11-13, most of it its database's download
#: (ruling plan#3708), so a run past this is one whose download or scan has stalled.
SCAN_SECONDS = 1800


class NotAScan(ValueError):
    """The scanner's answer is no scan as Grype writes one, read with no list of CVEs known to
    be exploited, holds what a counted match cannot name, or names no commit the scanned image
    was built from; its catalog or the image's python's report is not as written; or the scan
    did not see what the image runs (:func:`seen`); the message says what and where."""


class ScanFailed(RuntimeError):
    """The scan could not run: a docker command failed or did not finish, the deploy script
    names no container, the daemon the scanner would run on is not rootless, or the image it
    loaded is not production's or was not removed; the message says which, and what docker
    said."""


@dataclass(frozen=True)
class Counted:
    """One match the scan counts (the module's one judge): ``vulnerability``, the CVE as Grype
    names it; the package it was found in, by its ``kind`` (Grype's type: ``deb``, ``python``,
    ``binary``), its ``package`` name and its ``installed`` version; ``fixes``, each version
    Grype names the fix in, in its order, none when Grype knows no fix; ``rating``, the highest
    any source gives it, an index of :data:`RATINGS`; and ``exploited``, whether Grype marks it
    known exploited."""

    vulnerability: str
    kind: str
    package: str
    installed: str
    fixes: tuple[str, ...]
    rating: int
    exploited: bool

    @property
    def marker(self) -> str:
        """The marker naming the CVE and the package (:func:`tools.update.app.marker`), the
        same whatever version is installed or fixes it, so a CVE still unfixed the next day
        is the same thing found again."""
        return marker(f"scan {self.vulnerability} {self.kind} {self.package}")


@dataclass(frozen=True)
class Scanned:
    """One scan of the image production is running (:func:`scanned`): ``image``, its digest, as
    production's container names it; ``revision``, the commit it was built from; and
    ``found``, each match that counts (:func:`judged`), in its order."""

    image: str
    revision: str
    found: tuple[Counted, ...]


@dataclass(frozen=True)
class Catalogued:
    """One package Grype's catalog lists (:func:`catalogued`): its ``kind`` (Grype's type, as
    :class:`Counted`'s), its ``package`` name, its ``version`` and ``paths``, each file it was
    found at."""

    kind: str
    package: str
    version: str
    paths: frozenset[str]


@dataclass(frozen=True)
class Interpreter:
    """What the image's own python reports of itself (:func:`reported`): its ``version``, and
    ``executable``, the real path of the file it runs from."""

    version: str
    executable: str


#: How the scan runs one docker command: on the daemon given (as ``DOCKER_HOST`` spells it),
#: with its arguments, the variables added to this process's environment and the seconds it may
#: take; what it writes on its standard output, which a failure raises
#: :class:`ScanFailed` for instead.
Docker = Callable[[str, Sequence[str], Mapping[str, str], int], bytes]


def _value(node: object, key: str, kind: type, where: str):
    """``node``'s ``key``, which must be a ``kind``; :class:`NotAScan` saying ``where`` when
    ``node`` is no object or holds no such value."""
    if not isinstance(node, dict) or not isinstance(value := node.get(key), kind):
        raise NotAScan(f"{where} holds no {key} that is a {kind.__name__}")
    return value


def _listable(value: object, where: str) -> str:
    """``value``, a string a counted match names; :class:`NotAScan` when it is none, or holds a
    character outside :data:`_LISTABLE`."""
    if not isinstance(value, str) or not _LISTABLE.fullmatch(value):
        raise NotAScan(f"{where} is {value!r}, which cannot be listed")
    return value


def _rating(record: object, where: str) -> int:
    """The rating ``record``, a vulnerability's, gives it, as an index of :data:`RATINGS`: a
    missing one as unknown, as Grype reads it; :class:`NotAScan` for any spelling Grype's scale
    does not hold."""
    spelled = record.get("severity", "") if isinstance(record, dict) else None
    if not isinstance(spelled, str) or spelled.lower() not in ("", *RATINGS):
        raise NotAScan(f"{where} is rated {spelled!r}, which is no rating Grype gives")
    return RATINGS.index(spelled.lower() or "unknown")


def _exploited(record: object, where: str) -> bool:
    """Whether Grype marks ``record``, a vulnerability's, known exploited.  Grype writes its
    ``knownExploited`` only where CISA's list names the CVE, as a list of that list's entries,
    each naming the CVE (``cve``), and leaves it out where the list names none (it is
    ``omitempty``, ``grype/presenter/models/vulnerability_metadata.go``, read 2026-11-13 at
    v0.121.2).  Any other shape is refused: an empty list or a null, say, could be a mark read
    as none.

    Raises:
        NotAScan: ``record`` is no object, or its mark is not one Grype writes.
    """
    if not isinstance(record, dict):
        raise NotAScan(f"{where} is {record!r}, which is no record Grype writes")
    if "knownExploited" not in record:
        return False
    marks = record["knownExploited"]
    if not (isinstance(marks, list) and marks and all(
            isinstance(mark, dict) and isinstance(mark.get("cve"), str) for mark in marks)):
        raise NotAScan(f"{where} is marked known exploited as {marks!r}, which Grype does not "
                       "write")
    return True


def _counted(match: object, where: str) -> Counted | None:
    """``match``, one of the scan's, as a :class:`Counted`, or None when it does not count (the
    module's one judge).  Every record's mark is read, so one Grype does not write is refused
    however the match is judged; a rating is read only where it decides the match or is
    listed."""
    vulnerability = _value(match, "vulnerability", dict, where)
    fix = _value(vulnerability, "fix", dict, f"{where}'s vulnerability")
    if (state := _value(fix, "state", str, f"{where}'s fix")) not in FIX_STATES:
        raise NotAScan(f"{where}'s fix state is {state!r}, which Grype does not write")
    records = (("vulnerability", vulnerability), *(
        (f"related vulnerability {index}", record) for index, record in
        enumerate(_value(match, "relatedVulnerabilities", list, where))))
    marks = [_exploited(record, f"{where}'s {name}") for name, record in records]
    exploited = any(marks)
    if state != FIXED and not exploited:
        return None
    rating = max(_rating(record, f"{where}'s {name}") for name, record in records)
    if rating < RED and not exploited:
        return None
    artifact = _value(match, "artifact", dict, where)
    fixes = _value(fix, "versions", list, f"{where}'s fix") if state == FIXED else []
    if state == FIXED and not fixes:
        raise NotAScan(f"{where}'s fix is {FIXED} in no version, which Grype does not write")
    return Counted(_listable(vulnerability.get("id"), f"{where}'s vulnerability's id"),
                   *(_listable(artifact.get(key), f"{where}'s artifact's {key}")
                     for key in ("type", "name", "version")),
                   tuple(_listable(version, f"{where}'s fix's version {index}")
                         for index, version in enumerate(fixes)),
                   rating, exploited)


def _refused_without_kev(scan: object) -> None:
    """Refuse ``scan`` unless its database was built with CISA's list of CVEs known to be
    exploited (:data:`KEV` among the providers its descriptor names), as without it no match
    can be marked (the module's one judge).

    Raises:
        NotAScan: the scan names no such provider.
    """
    descriptor = _value(scan, "descriptor", dict, "the scan")
    database = _value(descriptor, "db", dict, "the scan's descriptor")
    providers = _value(database, "providers", dict, "the scan's database")
    if not isinstance(providers.get(KEV), dict):
        raise NotAScan(f"the scan's database names no provider {KEV}, so it read no list of "
                       "CVEs known to be exploited and could mark none")


def judged(scan: object) -> list[Counted]:
    """Each match of ``scan``, Grype's JSON as read, that the scan counts (the module's one
    judge), each once, the highest rated first, then by its CVE's id as text, the package's
    type and name, and the version installed.

    Raises:
        NotAScan: ``scan`` is no scan as Grype writes one, was read with no list of CVEs known
            to be exploited, or holds what a counted match cannot name; the message says where.
    """
    _refused_without_kev(scan)
    found = {item for index, match in enumerate(_value(scan, "matches", list, "the scan"))
             if (item := _counted(match, f"match {index}")) is not None}
    return sorted(found, key=lambda item: (-item.rating, item.vulnerability, item.kind,
                                           item.package, item.installed))


def revision(scan: object) -> str:
    """The commit the image ``scan`` read was built from: its :data:`REVISION` label, as the
    scanner read it from the image's own configuration (Grype's ``source.target.labels``).

    Raises:
        NotAScan: the scan holds no such label naming a whole commit, so no later step can tell
            which commit's fixes the image holds.
    """
    target = _value(_value(scan, "source", dict, "the scan"), "target", dict, "the scan's source")
    labels = target.get("labels")
    commit = labels.get(REVISION) if isinstance(labels, dict) else None
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise NotAScan(f"the scanned image's label {REVISION} is {commit!r}, which names no "
                       "commit it was built from")
    return commit


def parsed(answer: bytes) -> object:
    """The JSON the scanner's ``answer``, its standard output, holds.

    Raises:
        NotAScan: the answer holds no UTF-8 JSON.
    """
    try:
        return json.loads(answer.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NotAScan(f"it is no JSON: {error}") from error


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
    ruling plan#3876).

    Raises:
        NotAScan: ``answer`` is no report as the probe writes it, or names a version or a file
            that cannot be listed.
    """
    where = "the image's python's report"
    try:
        report = parsed(answer)
    except NotAScan as error:
        raise NotAScan(f"{where}: {error}") from error
    if not _EXECUTABLE.fullmatch(executable := _value(report, "executable", str, where)):
        raise NotAScan(f"{where} names a file it runs from that cannot be listed")
    return Interpreter(_listable(_value(report, "version", str, where), f"{where}'s version"),
                       executable)


def seen(scan: object, catalog: Sequence[Catalogued], interpreter: Interpreter) -> None:
    """Refuse ``scan``, Grype's JSON, unless it saw what the image runs (the module's paragraph
    on what it saw): every package a match names, by its type, name and version, is one
    ``catalog`` lists, so the matches were made of that catalog; and ``catalog`` lists the
    interpreter (:data:`INTERPRETER`) at ``interpreter``'s version, found at the file it runs
    from.

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


def container(root: Path) -> str:
    """The name of production's app container, as the checkout at ``root``'s deploy script
    defaults ``SHEKEL_CONTAINER_NAME`` to it (:data:`DEPLOY_SCRIPT`).

    Raises:
        ScanFailed: the script holds no such line, or more than one.
        OSError: as reading the script raises it.
    """
    names = _CONTAINER.findall((root / DEPLOY_SCRIPT).read_text(encoding="utf-8"))
    if len(names) != 1:
        raise ScanFailed(f"{DEPLOY_SCRIPT} names production's container {len(names)} times "
                         "where it names it once, so the scan cannot tell which to read")
    return names[0]


def _docker(daemon: str, arguments: Sequence[str], added: Mapping[str, str],
            seconds: int) -> bytes:
    """The scan's :data:`Docker`: ``docker`` run with ``arguments`` on ``daemon``, its
    standard output.

    Raises:
        ScanFailed: docker exits other than 0, saying its last line of error, or does not
            finish within ``seconds``.
        OSError: docker cannot be run.
    """
    try:
        done = subprocess.run(["docker", *arguments], capture_output=True, check=False,
                              timeout=seconds,
                              env={**os.environ, **added, "DOCKER_HOST": daemon})
    except subprocess.TimeoutExpired as error:
        raise ScanFailed(f"docker {arguments[0]} on {daemon} did not finish within {seconds} "
                         "s") from error
    if done.returncode:
        said = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ScanFailed(f"docker {arguments[0]} on {daemon} exited {done.returncode}: "
                         f"{said[-1] if said else 'it said nothing'}")
    return done.stdout


def _rootless() -> str:
    """The daemon the scanner runs on (ruling plan#3822): the one ``scripts/private_cluster.py``
    names (``DOCKER_HOST``, else the rootless daemon's socket), once it says it is rootless.
    No sanction lets the scanner run on another.

    Raises:
        ScanFailed: that daemon could not be asked, or is not rootless.
    """
    daemon = private_cluster.endpoint()
    if (rootless := private_cluster.asked_rootless()) is not True:
        where = daemon or "docker's default daemon"
        raise ScanFailed(f"the scanner runs on the rootless daemon alone, and {where} "
                         + ("could not be asked what it is" if rootless is None else
                            "is not rootless")
                         + ": start it with 'systemctl --user start docker.service'")
    return daemon


def _removed(docker: Docker, daemon: str, image: str, after: Exception | None) -> None:
    """Remove ``image``, the copy of production's image the scan loaded onto ``daemon``.

    Raises:
        ScanFailed: the removal failed, saying so, and ``after``, the failure the removal
            followed, when there was one.
    """
    try:
        docker(daemon, ["image", "rm", image], {}, DOCKER_SECONDS)
    except (ScanFailed, OSError) as error:
        followed = f", after this failure: {after}" if after else ""
        raise ScanFailed(f"the copy of production's image loaded onto {daemon} was not removed: "
                         f"{error}{followed}") from error


def _probed(docker: Docker, daemon: str, run: Sequence[str], added: Mapping[str, str]) -> bytes:
    """What the image's own python answers (:data:`REPORT`; ruling plan#3876), run with
    ``run``, the compose command, and ``added``, its variables: the saved file they hand the
    scanner (``SCAN_IMAGE``) loaded onto ``daemon``, the image it loads checked to be the one
    they hand the probe (``SCAN_PROBE``), production's, so the probe runs what was loaded; that
    image removed however the run ends.

    Raises:
        ScanFailed: the load, the probe or the removal failed, or the load named another image.
        OSError: as running docker raises it.
    """
    image = added["SCAN_PROBE"]
    loaded = docker(daemon, ["load", "--input", added["SCAN_IMAGE"]], {}, DOCKER_SECONDS)
    failure: Exception | None = None
    try:
        if loaded.decode("utf-8", errors="replace").strip() != f"Loaded image ID: {image}":
            raise ScanFailed(f"docker load on {daemon} loaded no image {image}, production's")
        return docker(daemon, [*run, "probe", REPORT], added, DOCKER_SECONDS)
    except (ScanFailed, OSError) as error:
        failure = error
        raise
    finally:
        _removed(docker, daemon, image, failure)


def scanned(root: Path, docker: Docker = _docker) -> Scanned:
    """One scan of the image production is running, from the checkout at ``root`` (the module's
    first paragraphs): the rootless daemon asked first, so a scan it would refuse reads nothing
    of production; then production's container's image read and saved to a file of the scan's
    own; the image's own python run there (:func:`_probed`); the file scanned there, the
    scanner writing its catalog to a second file; then judged, and refused unless it saw what
    the image runs (:func:`seen`).  The files are deleted with their directory however the scan
    ends.

    Raises:
        ScanFailed: the scan could not run (:class:`ScanFailed`).
        NotAScan: the scanner's answer is no scan, names no commit, or saw not what the image
            runs, or its catalog or the image's python's report is not as written
            (:func:`judged`, :func:`revision`, :func:`catalogued`, :func:`reported`,
            :func:`seen`).
        OSError: as reading the deploy script, making the files or running docker raises it.
    """
    scanner = _rootless()
    name = container(root)
    image = docker(PRODUCTION, ["inspect", "--format", "{{.Image}}", name], {},
                   DOCKER_SECONDS).decode("utf-8", errors="replace").strip()
    if not _DIGEST.fullmatch(image):
        raise ScanFailed(f"production's container {name} runs {image!r}, which is no image "
                         "digest")
    with tempfile.TemporaryDirectory(prefix="shekel-scan-") as directory:
        saved = Path(directory) / "image.tar"
        docker(PRODUCTION, ["save", "--output", str(saved), image], {}, DOCKER_SECONDS)
        # The scanner runs as uid 65532, which the rootless daemon maps to a uid that is neither
        # the file's owner nor in its group, so it reads and writes a file by its mode for
        # others alone: docker writes the image 0600, which it then cannot open (measured
        # 2026-11-13: "permission denied", exit 1), and the catalog's file is made for it to
        # write, its directory 0700 so no other user of the host reaches it.
        saved.chmod(0o644)
        written = Path(directory) / "catalog.json"
        written.write_bytes(b"")
        written.chmod(0o666)
        # Compose reads every variable the file names for whichever service it runs (measured
        # 2026-11-13: the probe alone, run without the scanner's, refused), so both get all.
        added = {"SCAN_IMAGE": str(saved), "SCAN_CATALOG": str(written), "SCAN_PROBE": image}
        run = ["compose", "--file", str(root / COMPOSE), "run", "--rm", "--no-TTY"]
        interpreter = reported(_probed(docker, scanner, run, added))
        answer = docker(scanner, [*run, "scan"], added, SCAN_SECONDS)
        catalog = catalogued(written.read_bytes())
    scan = parsed(answer)
    found = judged(scan)
    seen(scan, catalog, interpreter)
    return Scanned(image, revision(scan), tuple(found))
