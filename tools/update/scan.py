"""The daily scan of the image production is running (plan#2444; rulings plan#3786, plan#3820,
plan#3822, plan#3825), and its judge (ruling plan#3720).  Its findings stay off GitHub: no
issue, log or artifact there holds them, as an open security finding is never public (ruling
plan#3785).  ``quill scan`` runs it (:func:`scanned`), says what it found, and files it into the
private tracker (``tools/quill/_scan.py``, ruling plan#3787).

**The scan reads production and writes nothing there** (ruling plan#3822).  Production's app
container runs on the system daemon (:data:`PRODUCTION`), under the name main's deploy script
gives it (:func:`container`), and the scan's only acts on that daemon are reads: the inspect
that names the image the container runs, by its digest, and the save of that image to a file
of the scan's own.  The scanner, ``docker-compose.scan.yml``'s Grype, then reads the file on
the developer's rootless daemon, once that daemon says it is rootless
(``scripts/private_cluster.py``'s ``asked_rootless``, the one spelling of that question), so
nothing the scan starts, no container, network or scanner image, lands on production's
daemon; and the scanner holds that file and the one it writes its catalog to alone, no socket
(rulings plan#3706, plan#3875).  The image's own python runs on that daemon too, in a copy of
the file loaded there and removed however the scan ends (ruling plan#3876).  The scanner's own
compose project is taken down there however the scan's runs in it end, so the network compose
makes for the scanner, which ``run --rm`` leaves standing, goes with it (plan#3864).  A scan
systemd stops ends so too: systemd stops a run by SIGTERM, at its unit's limit or at a shutdown,
and Python's own action for that signal ends the process with none of those removals run, so
:func:`scanned` takes the signal as an exit (:func:`_stopped`) for as long as it runs.  The
commit the image was built from is read from the scanner's own record of the file it scanned
(:func:`revision`), so the commit and the findings are one read of the same bytes.  Measured
2026-11-13 on production's image: the save, a 98 MB archive, in 0.8 s; the load in 2 s and
the image's python's run in 2 s; and the scan, its database downloaded fresh, in 42 s.

**What it saw** (rulings plan#3788, plan#3875, plan#3876).  Grype's JSON lists only what it
matched, so an interpreter with no CVE leaves no trace there.  So the scanner's one run writes
too its catalog of every package it read (:func:`catalogued`), and the image's own python
reports its version, the file it runs from, its venv and its platform (:func:`reported`).
:func:`seen` refuses the scan unless every package a match names is one the catalog lists, so
the findings are of that catalog, and unless the catalog lists the interpreter at that version,
found at that file: Grype finds the interpreter by its binary alone, and a scan that stopped
finding it would read clean without having looked.  :func:`held` refuses it unless the Python
packages the catalog lists in that venv are the lock's set for the image exactly, each at its
locked version (ruling plan#3877): the set the developer's uv works out (:func:`locked`) from
the lock at the commit the image was built from, for the groups that commit's Dockerfile
installs (:func:`chosen`, ruling plan#3884), filtered to the platform and version the image's
python reported (:func:`told`).  A set worked out wrong refuses the scan, never passes it,
unless Grype misses the very package the set does.

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
holds a character outside :data:`tools.update._scan_data._LISTABLE`: none can then close a code
span it is quoted in, mention anyone, or spell a marker that ``quill sync`` reads.  The catalog
and the report are read so too (:mod:`tools.update.scan_seen`), and refused where they are not
as Grype and the scan's own probe write them.  A scan refused is never read as a clean one.

**It imports the standard library alone**, with :mod:`tools.update.app`,
:mod:`tools.update._scan_data`, :mod:`tools.update.scan_seen`, :mod:`tools.ci.gitcmd` and
``scripts/private_cluster.py``, which do too, so it runs on an interpreter given no installed
package (ruling plan#3825).
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts import private_cluster
from tools.ci.gitcmd import MAIN, git, tracking
from tools.update.app import marker
from tools.update._scan_data import NotAScan, _listable, _value, parsed
from tools.update.scan_seen import REPORT, Catalogued, Interpreter, catalogued, reported, seen

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

#: The daemon production runs on: the system one, at docker's own default socket, where the
#: deploy script, which hands docker no daemon, rolls production's container.
PRODUCTION = "unix:///var/run/docker.sock"
#: The deploy script, whose default for ``SHEKEL_CONTAINER_NAME`` names production's app
#: container: the line the release reads that name from too (``.claude/skills/release/
#: SKILL.md``, its step 1).
DEPLOY_SCRIPT = "deploy/shekel-deploy.sh"
#: The branch whose deploy script production is deployed with, by its remote-tracking ref as
#: ``quill scan``'s one fetch brings it in (ruling plan#3970): ``main`` (ruling plan#4069).
DEPLOYED = tracking(MAIN)
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
#: The implementation ``uv tree`` works the lock's set out for, as its ``--python`` names it.
#: uv evaluates the lock's implementation conditions, and works the set out on an interpreter
#: it finds for itself, the one ``--python`` names (measured 2026-11-13 at uv 0.13.7: the lock
#: with its two ``!= 'PyPy'`` conditions turned ``==`` left out psycopg-c, cffi and pycparser;
#: with no ``--python``, ``uv python find`` there named this checkout's active venv; and
#: ``--python pypy``, with no PyPy here, refused, exit 2), so the scan names it: CPython, the
#: one the image's base image runs.  An image whose python reports another is refused, never
#: worked out as CPython.
IMPLEMENTATION = "cpython"
#: The operating system and C library of the one platform the scan tells uv, as the image's
#: python names them (``sys.platform``, ``platform.libc_ver``): Linux with glibc, which uv names
#: ``<machine>-unknown-linux-gnu``.  uv refuses a machine it does not name so (measured
#: 2026-11-13: ``armv7l`` refused, exit 2).
PLATFORM = ("linux", "glibc")
#: The files of the commit the image was built from that ``uv tree`` reads (ruling plan#3877):
#: the project, which declares its groups, and its lock.  ``.python-version`` is not among
#: them: it names the interpreter uv finds, which ``--python`` names instead (measured
#: 2026-11-13: the same 42 entries with it and without it).
LOCK_FILES = ("pyproject.toml", "uv.lock")
#: The file of that commit that built the image: its one ``uv sync`` chooses the groups the
#: image installs (ruling plan#3884).
DOCKERFILE = "Dockerfile"
#: The flags of that ``uv sync`` that change nothing the lock's set holds: two change how it
#: installs, and ``--no-install-project`` leaves out the project, which the lock holds as a
#: virtual package (``package = false``), a source uv never installs and :func:`locked` leaves
#: out whatever the flag.
_UNCHOSEN = frozenset({"--locked", "--compile-bytecode", "--no-install-project"})
#: The flags of that ``uv sync`` that choose the groups it installs, which ``uv tree`` takes as
#: they stand: the one taking no group, and the one taking the group after it.
_NO_DEFAULT_GROUPS = "--no-default-groups"
_GROUP = "--group"
#: The schema ``uv tree``'s JSON names (``schema.version``): its preview one, the one it writes
#: (measured 2026-11-13 at uv 0.13.7, which warns it may change); any other is refused.
TREE_SCHEMA = "preview"
#: The kinds of a tree's entry that name no package of their own: the workspace, and a
#: package's extra or the project's group, each an object naming it (measured 2026-11-13).
_WORKSPACE = "workspace"
_NAMED_KINDS = frozenset({"extra", "group"})
#: The kind of a tree's entry that is a package.
_PACKAGE = "package"
#: The source of a package uv never installs: a virtual one, such as the project itself.
_VIRTUAL = "virtual"
#: What PEP 503 folds into one ``-`` in a package's name: the venv is held to the lock by the
#: name each normalizes to, PEP 503's identity of a package, not by how Grype or uv spells it
#: (measured 2026-11-13: each tool spelled every one of the venv's 41 names normalized).
_FOLDED = re.compile(r"[-_.]+")
#: The longest an inspect, a save, a load, the probe's run or a removal may take: the save of
#: production's image took 0.8 s, its load 2 s and the probe 2 s (2026-11-13), so a daemon still
#: busy after this is a daemon that is not answering.
DOCKER_SECONDS = 120
#: The longest the scanner may take: 42 s on 2026-11-13, most of it its database's download
#: (ruling plan#3708), so a run past this is one whose download or scan has stalled.
SCAN_SECONDS = 1800
#: The longest ``uv tree`` may take: 9 ms on 2026-11-13, reading the lock offline, so a uv
#: still running after this is one that has hung.
UV_SECONDS = 60


class ScanFailed(RuntimeError):
    """The scan could not run: a docker or uv command failed or did not finish, the deploy
    script names no container, the daemon the scanner would run on is not rootless, the image
    it loaded is not production's or was not removed, or the scanner's compose project was not
    taken down; the message says which, and what docker or uv said."""


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

    @property
    def line(self) -> str:
        """The match as one line, as ``quill scan`` prints it and a card it files lists it: its
        CVE, its rating, whether it is known exploited, the package it is in and each version
        Grype names its fix in, or that Grype knows no fix."""
        return (f"{self.vulnerability}, rated {RATINGS[self.rating]}"
                f"{', known exploited' if self.exploited else ''}: {self.kind} {self.package} "
                f"{self.installed}, "
                + (f"fixed in {', '.join(self.fixes)}" if self.fixes else "no fix known"))


@dataclass(frozen=True)
class Scanned:
    """One scan of the image production is running (:func:`scanned`): ``image``, its digest, as
    production's container names it; ``revision``, the commit it was built from; and
    ``found``, each match that counts (:func:`judged`), in its order."""

    image: str
    revision: str
    found: tuple[Counted, ...]


#: How the scan runs one docker command: on the daemon given (as ``DOCKER_HOST`` spells it),
#: with its arguments, the variables added to this process's environment and the seconds it may
#: take; what it writes on its standard output, which a failure raises
#: :class:`ScanFailed` for instead.
Docker = Callable[[str, Sequence[str], Mapping[str, str], int], bytes]
#: How the scan runs ``uv``: in the directory given, with its arguments and the seconds it may
#: take; what it writes on its standard output, which a failure raises :class:`ScanFailed` for
#: instead.
Uv = Callable[[Path, Sequence[str], int], bytes]


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


def _normalized(name: str) -> str:
    """``name``, a Python package's, as PEP 503 normalizes it: in lower case, each run of
    ``-``, ``_`` and ``.`` one ``-``."""
    return _FOLDED.sub("-", name).lower()


def held(catalog: Sequence[Catalogued], venv: str, lock: frozenset[tuple[str, str]]) -> None:
    """Refuse the scan unless the Python packages ``catalog`` lists in ``venv``, each found at a
    file under it, are ``lock`` exactly, the lock's set for the image (:func:`locked`), each by
    its normalized name and its version: none missing, none at another version, and none the
    set does not hold (the module's paragraph on what it saw).  A Python package found outside
    the venv, the base image's own, is no package the lock installs, and is not read here.

    Raises:
        NotAScan: a package the catalog lists in the venv names what cannot be listed, or the
            two differ; the message names each package of the set the catalog does not list,
            and each the catalog lists that the set does not hold, so a package at another
            version is named in both.
    """
    found = frozenset(
        (_normalized(_listable(item.package, "the name of a python package the catalog lists in "
                                             f"{venv}")),
         _listable(item.version, f"the version of the python package {item.package!r} the "
                                 f"catalog lists in {venv}"))
        for item in catalog if item.kind == "python"
        and any(path.startswith(f"{venv}/") for path in item.paths))
    said = [f"{heading}: {', '.join(f'{name} {version}' for name, version in sorted(packages))}"
            for heading, packages in (("missing from it, as the lock holds them", lock - found),
                                      ("in it, but not in the lock's set", found - lock))
            if packages]
    if said:
        raise NotAScan(f"Grype's catalog of the venv {venv} is not the lock's set for the image, "
                       "so the scan did not see every locked package at its locked version: "
                       + "; ".join(said))


def locked(answer: bytes) -> frozenset[tuple[str, str]]:
    """The lock's set for the image, each package by its normalized name and its version, as
    ``uv tree``'s ``answer``, its JSON, lists it (ruling plan#3877): each entry of the kind
    package, but one whose source is virtual, which uv never installs (the project itself).  The
    workspace's entry, and that of a package's extra or of the project's group, name no package
    of their own.

    Raises:
        NotAScan: ``answer`` is no tree as uv writes one: no JSON, of a schema other than
            :data:`TREE_SCHEMA`, or an entry, its kind, or a package's source, name or version
            not as uv writes them.
    """
    where = "uv tree's answer"
    try:
        tree = parsed(answer)
    except NotAScan as error:
        raise NotAScan(f"{where}: {error}") from error
    schema = _value(_value(tree, "schema", dict, where), "version", str, f"{where}'s schema")
    if schema != TREE_SCHEMA:
        raise NotAScan(f"{where} is of the schema {schema!r}, where the scan reads uv's "
                       f"{TREE_SCHEMA!r} alone")
    found = set()
    for key, entry in _value(tree, "resolution", dict, where).items():
        at = f"{where}'s entry {key!r}"
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if kind == _WORKSPACE or (isinstance(kind, dict) and len(kind) == 1
                                  and all(isinstance(named, str) and name in _NAMED_KINDS
                                          for name, named in kind.items())):
            continue
        if kind != _PACKAGE:
            raise NotAScan(f"{at} is of the kind {kind!r}, which uv does not write")
        if _VIRTUAL not in _value(entry, "source", dict, at):
            found.add((_normalized(_listable(entry.get("name"), f"{at}'s name")),
                       _listable(entry.get("version"), f"{at}'s version")))
    return frozenset(found)


def chosen(dockerfile: str) -> tuple[str, ...]:
    """The flags choosing the groups the image installs, in their order, as ``dockerfile``, the
    Dockerfile of the commit the image was built from, runs ``uv sync`` (ruling plan#3884):
    read by Docker's rules for its default ``\\`` escape, a comment line and a blank one dropped
    wherever they stand, and each RUN instruction's commands parted where the shell parts them.

    Raises:
        NotAScan: the Dockerfile runs ``uv sync`` other than once, or with a flag whose effect on
            what it installs the scan does not know (none of :data:`_UNCHOSEN`,
            :data:`_NO_DEFAULT_GROUPS` and :data:`_GROUP`), or names a group that cannot be
            listed, or a RUN instruction holds words the shell cannot part.
    """
    kept = "\n".join(line for line in dockerfile.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))
    syncs = []
    for instruction in re.sub(r"\\[ \t]*\n", " ", kept).splitlines():
        keyword, *shell = instruction.split(None, 1)
        if keyword.upper() != "RUN" or not shell:
            continue
        lexer = shlex.shlex(shell[0], posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            words = [*lexer, ";"]
        except ValueError as error:
            raise NotAScan(f"the Dockerfile the image was built by runs {instruction!r}, which "
                           f"the shell cannot part: {error}") from error
        command: list[str] = []
        for word in words:
            if word.strip(lexer.punctuation_chars):
                command.append(word)
                continue
            if command[:2] == ["uv", "sync"]:
                syncs.append(command[2:])
            command = []
    if len(syncs) != 1:
        raise NotAScan(f"the Dockerfile the image was built by runs uv sync {len(syncs)} times "
                       "where it runs it once, so the scan cannot tell which groups it installs")
    flags, found = iter(syncs[0]), []
    for flag in flags:
        if flag == _GROUP:
            found += [flag, _listable(next(flags, None), f"the group its uv sync's {_GROUP} "
                                                         "names")]
        elif flag == _NO_DEFAULT_GROUPS:
            found.append(flag)
        elif flag not in _UNCHOSEN:
            raise NotAScan(f"the Dockerfile the image was built by runs uv sync with {flag!r}, "
                           "a flag whose effect on what it installs the scan does not know")
    return tuple(found)


def told(interpreter: Interpreter) -> tuple[str, ...]:
    """What ``uv tree`` is told of the image, as ``interpreter``, its own python, reports it
    (ruling plan#3877): the implementation it works the lock's set out for
    (:data:`IMPLEMENTATION`), the platform, ``<machine>-unknown-linux-gnu`` (:data:`PLATFORM`),
    and the version.

    Raises:
        NotAScan: the image runs another implementation, operating system or C library, which
            the scan cannot tell uv, so the scan is refused rather than worked out for another.
    """
    if interpreter.implementation != IMPLEMENTATION:
        raise NotAScan(f"the image's python is {interpreter.implementation}, where the scan has "
                       f"uv work the lock's set out for {IMPLEMENTATION} alone")
    if (interpreter.platform, interpreter.libc) != PLATFORM:
        raise NotAScan(f"the image's python runs on {interpreter.platform} with the C library "
                       f"{interpreter.libc!r}, where the scan can tell uv {PLATFORM[0]} with "
                       f"{PLATFORM[1]} alone")
    return ("--python", IMPLEMENTATION,
            "--python-platform", f"{interpreter.machine}-unknown-linux-gnu",
            "--python-version", interpreter.version)


def container(root: Path) -> str:
    """The name of production's app container, as the deploy script (:data:`DEPLOY_SCRIPT`) at
    :data:`DEPLOYED`, main, in the git of the checkout at ``root`` defaults
    ``SHEKEL_CONTAINER_NAME`` to it (ruling plan#4069): the script production is deployed with,
    not the checkout's own, which on the timer is dev's tip, so a rename landing in dev before
    a release fails no scan.

    Raises:
        ScanFailed: the script holds no such line, or more than one.
        GitError: git cannot read the script at that branch.
    """
    names = _CONTAINER.findall(git(root, "cat-file", "blob", f"{DEPLOYED}:{DEPLOY_SCRIPT}"))
    if len(names) != 1:
        raise ScanFailed(f"{DEPLOY_SCRIPT} names production's container {len(names)} times "
                         "where it names it once, so the scan cannot tell which to read")
    return names[0]


def _ran(command: Sequence[str], named: str, seconds: int, *,
         env: Mapping[str, str] | None = None, cwd: Path | None = None) -> bytes:
    """``command`` run with ``env`` as its environment (None, this process's) in ``cwd`` (None,
    this process's), its standard output.

    Raises:
        ScanFailed: it exits other than 0, saying its last line of error, or does not finish
            within ``seconds``; ``named`` names it.
        OSError: it cannot be run.
    """
    try:
        done = subprocess.run(command, capture_output=True, check=False, timeout=seconds,
                              env=env, cwd=cwd)
    except subprocess.TimeoutExpired as error:
        raise ScanFailed(f"{named} did not finish within {seconds} s") from error
    if done.returncode:
        said = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ScanFailed(f"{named} exited {done.returncode}: "
                         f"{said[-1] if said else 'it said nothing'}")
    return done.stdout


def _docker(daemon: str, arguments: Sequence[str], added: Mapping[str, str],
            seconds: int) -> bytes:
    """The scan's :data:`Docker`: ``docker`` run with ``arguments`` on ``daemon``, its
    standard output.

    Raises:
        ScanFailed: docker exits other than 0, saying its last line of error, or does not
            finish within ``seconds``.
        OSError: docker cannot be run.
    """
    return _ran(["docker", *arguments], f"docker {arguments[0]} on {daemon}", seconds,
                env={**os.environ, **added, "DOCKER_HOST": daemon})


def _uv(directory: Path, arguments: Sequence[str], seconds: int) -> bytes:
    """The scan's :data:`Uv`: the developer's ``uv``, the first on the path (ruling
    plan#3877), run with ``arguments`` in ``directory``, its standard output.

    Raises:
        ScanFailed: uv exits other than 0, saying its last line of error, or does not finish
            within ``seconds``.
        OSError: uv cannot be run.
    """
    return _ran(["uv", *arguments], f"uv {arguments[0]}", seconds, cwd=directory)


def _locks(root: Path, commit: str, interpreter: Interpreter,
           uv: Uv) -> frozenset[tuple[str, str]]:
    """The lock's set for the image built from ``commit`` (:func:`locked`; ruling plan#3877):
    the commit's :data:`LOCK_FILES`, read from the git of the checkout at ``root``, which
    ``quill scan``'s one fetch of ``dev`` and ``main`` brings it into first (ruling plan#3970),
    written to a directory of the scan's own, deleted however the run ends, where ``uv tree``
    works the set out from the lock alone, offline, for the groups the commit's
    :data:`DOCKERFILE` chooses (:func:`chosen`, ruling plan#3884) and the image :func:`told`
    tells it of.

    Raises:
        GitError: git cannot read a file at ``commit``.
        NotAScan: as :func:`chosen`, :func:`told` and :func:`locked` raise it.
        ScanFailed: uv failed or did not finish.
        OSError: as writing the files or running uv raises it.
    """
    arguments = ["tree", "--frozen", "--offline", *told(interpreter), "--format", "json",
                 *chosen(git(root, "cat-file", "blob", f"{commit}:{DOCKERFILE}"))]
    with tempfile.TemporaryDirectory(prefix="shekel-scan-lock-") as directory:
        for name in LOCK_FILES:
            (Path(directory) / name).write_text(
                git(root, "cat-file", "blob", f"{commit}:{name}"), encoding="utf-8")
        answer = uv(Path(directory), arguments, UV_SECONDS)
    return locked(answer)


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


def _undone(undo: Callable[[], bytes], left: str, after: Exception | None) -> None:
    """Undo what the scan made on the scanner's daemon: ``undo``, the docker command that does.

    Raises:
        ScanFailed: the command failed, saying ``left``, what it leaves there, then what docker
            said, and ``after``, the failure it followed, when there was one.
    """
    try:
        undo()
    except (ScanFailed, OSError) as error:
        followed = f", after this failure: {after}" if after else ""
        raise ScanFailed(f"{left}: {error}{followed}") from error


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
        _undone(lambda: docker(daemon, ["image", "rm", image], {}, DOCKER_SECONDS),
                f"the copy of production's image loaded onto {daemon} was not removed", failure)


def _read(root: Path, docker: Docker, scanner: str,
          image: str) -> tuple[Interpreter, bytes, tuple[Catalogued, ...]]:
    """What the scan reads of ``image``, production's, on ``scanner``, the rootless daemon, from
    the checkout at ``root``: the image saved off production's daemon to a file of the scan's
    own; the image's own python run there (:func:`_probed`), its report; and the file scanned
    there, the scanner's answer and the catalog it writes to a second file.  The scanner's
    compose project is taken down once those runs end, however they end; the files are deleted
    with their directory however the read ends.

    Raises:
        ScanFailed: a docker command failed or did not finish, the probe's image was not loaded
            or not removed (:func:`_probed`), or the compose project was not taken down.
        NotAScan: the image's python's report or the catalog is not as written
            (:func:`reported`, :func:`catalogued`).
        OSError: as making the files or running docker raises it.
    """
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
        compose = ["compose", "--file", str(root / COMPOSE)]
        run = [*compose, "run", "--rm", "--no-TTY"]
        failure: Exception | None = None
        try:
            interpreter = reported(_probed(docker, scanner, run, added))
            answer = docker(scanner, [*run, "scan"], added, SCAN_SECONDS)
        except (ScanFailed, NotAScan, OSError) as error:
            failure = error
            raise
        finally:
            # ``run --rm`` removes the run's container and leaves the network compose made for
            # the scanner, its project's default; ``down`` removes it, handed the same
            # variables, without which compose refuses to read the file, and does nothing
            # where nothing stands, as after a probe that failed.  It leaves a network a
            # container is still on, exiting 0: a scan run beside this one, whose own ``down``
            # then removes it, or one killed mid-scan, whose container the daemon removes once
            # its command ends (``--rm`` is the daemon's AutoRemove), the network then going
            # with the next run's (each measured 2026-11-13, compose 5.7.1).
            _undone(lambda: docker(scanner, [*compose, "down"], added, DOCKER_SECONDS),
                    f"the scanner's compose project on {scanner} was not taken down", failure)
        return interpreter, answer, catalogued(written.read_bytes())


def _stopped(signum: int, _frame: object) -> None:
    """Take ``signum``, SIGTERM, as an exit with the status a shell gives a process the signal
    ended, raised where the scan is, so each removal it owes runs (the module's first paragraph
    says which).

    Raises:
        SystemExit: always.
    """
    raise SystemExit(128 + signum)


def scanned(root: Path, docker: Docker = _docker, uv: Uv = _uv) -> Scanned:
    """One scan of the image production is running, from the checkout at ``root`` (the module's
    first paragraphs): the rootless daemon asked first, so a scan it would refuse reads nothing
    of production; then production's container's image read, by its digest, and saved, probed
    and scanned (:func:`_read`); then judged, and refused unless it saw what the image runs
    (:func:`seen`) and every package the lock installs there, at its locked version
    (:func:`held`), the lock's set worked out by ``uv`` (:func:`_locks`).  SIGTERM, while it
    runs, is an exit (:func:`_stopped`); the action before it is restored as it returns.

    Raises:
        SystemExit: SIGTERM stopped it, once each removal it owes ran.
        ScanFailed: the scan could not run (:class:`ScanFailed`).
        NotAScan: the scanner's answer is no scan, names no commit, or saw not what the image
            runs, or its catalog, the image's python's report, uv's tree or the Dockerfile's
            ``uv sync`` is not as written (:func:`judged`, :func:`revision`,
            :func:`catalogued`, :func:`reported`, :func:`seen`, :func:`told`, :func:`chosen`,
            :func:`locked`, :func:`held`).
        GitError: git cannot read main's deploy script, or a file of the commit the image was
            built from.
        OSError: as making the files or running docker or uv raises it.
    """
    before = signal.signal(signal.SIGTERM, _stopped)
    try:
        scanner = _rootless()
        name = container(root)
        image = docker(PRODUCTION, ["inspect", "--format", "{{.Image}}", name], {},
                       DOCKER_SECONDS).decode("utf-8", errors="replace").strip()
        if not _DIGEST.fullmatch(image):
            raise ScanFailed(f"production's container {name} runs {image!r}, which is no image "
                             "digest")
        interpreter, answer, catalog = _read(root, docker, scanner, image)
        scan = parsed(answer)
        found = judged(scan)
        seen(scan, catalog, interpreter)
        commit = revision(scan)
        held(catalog, interpreter.prefix, _locks(root, commit, interpreter, uv))
        return Scanned(image, commit, tuple(found))
    finally:
        signal.signal(signal.SIGTERM, before)
