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
daemon; and the scanner holds the file alone, no socket (ruling plan#3706).  The commit the
image was built from is read from the scanner's own record of the file it scanned
(:func:`revision`), so the commit and the findings are one read of the same bytes.  Measured
2026-11-13 on production's image: the save, a 98 MB archive, in 0.8 s, and the scan, its
database downloaded fresh, in 42 s.

**The one judge.**  A match counts when Grype knows its fix (its ``fix.state`` is ``fixed``) and
the highest rating any source gives it is HIGH or CRITICAL (ruling plan#3720): the match's own,
which for a Debian package is Debian's, or that of any record related to it, NVD's among them.
Each match counted is one :class:`Fixed` for each package it was found in, its CVE as Grype
names it.

**Read as data.**  What a package's name and version say is written by what the image holds.
So :func:`judged` refuses the scan whole (:class:`NotAScan`) where it is not the shape Grype
writes, where a fix state or a rating deciding a match is one Grype does not spell (read at its
pinned v0.121.2, :data:`FIX_STATES`, :data:`RATINGS`), and where a value a counted match names
holds a character outside :data:`_LISTABLE`: none can then close a code span it is quoted in,
mention anyone, or spell a marker that ``quill sync`` reads.  A scan refused is never read as a
clean one.

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
#: The longest an inspect or a save may take: the save of production's image took 0.8 s
#: (2026-11-13), so a daemon still busy after this is a daemon that is not answering.
DOCKER_SECONDS = 120
#: The longest the scanner may take: 42 s on 2026-11-13, most of it its database's download
#: (ruling plan#3708), so a run past this is one whose download or scan has stalled.
SCAN_SECONDS = 1800


class NotAScan(ValueError):
    """The scanner's answer is no scan as Grype writes one, holds what a counted match cannot
    name, or names no commit the scanned image was built from; the message says what and
    where."""


class ScanFailed(RuntimeError):
    """The scan could not run: a docker command failed or did not finish, the deploy script
    names no container, or the daemon the scanner would run on is not rootless; the message
    says which, and what docker said."""


@dataclass(frozen=True)
class Fixed:
    """One match the scan counts (the module's one judge): ``vulnerability``, the CVE as Grype
    names it; the package it was found in, by its ``kind`` (Grype's type: ``deb``, ``python``,
    ``binary``), its ``package`` name and its ``installed`` version; ``fixes``, each version
    Grype names the fix in, in its order; and ``rating``, the highest any source gives it, an
    index of :data:`RATINGS`."""

    vulnerability: str
    kind: str
    package: str
    installed: str
    fixes: tuple[str, ...]
    rating: int

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
    found: tuple[Fixed, ...]


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


def _counted(match: object, where: str) -> Fixed | None:
    """``match``, one of the scan's, as a :class:`Fixed`, or None when it does not count (the
    module's one judge)."""
    vulnerability = _value(match, "vulnerability", dict, where)
    fix = _value(vulnerability, "fix", dict, f"{where}'s vulnerability")
    if (state := _value(fix, "state", str, f"{where}'s fix")) not in FIX_STATES:
        raise NotAScan(f"{where}'s fix state is {state!r}, which Grype does not write")
    if state != FIXED:
        return None
    related = _value(match, "relatedVulnerabilities", list, where)
    rating = max(_rating(record, f"{where}'s {name}") for name, record in (
        ("vulnerability", vulnerability),
        *((f"related vulnerability {index}", record) for index, record in enumerate(related))))
    if rating < RED:
        return None
    artifact = _value(match, "artifact", dict, where)
    fixes = _value(fix, "versions", list, f"{where}'s fix")
    return Fixed(_listable(vulnerability.get("id"), f"{where}'s vulnerability's id"),
                 *(_listable(artifact.get(key), f"{where}'s artifact's {key}")
                   for key in ("type", "name", "version")),
                 tuple(_listable(version, f"{where}'s fix's version {index}")
                       for index, version in enumerate(fixes)),
                 rating)


def judged(scan: object) -> list[Fixed]:
    """Each match of ``scan``, Grype's JSON as read, that the scan counts (the module's one
    judge), each once, the highest rated first, then by its CVE's id as text, the package's
    type and name, and the version installed.

    Raises:
        NotAScan: ``scan`` is no scan as Grype writes one, or holds what a counted match cannot
            name; the message says where.
    """
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


def scanned(root: Path, docker: Docker = _docker) -> Scanned:
    """One scan of the image production is running, from the checkout at ``root`` (the module's
    first paragraph): the rootless daemon asked first, so a scan it would refuse reads nothing
    of production; then production's container's image read, saved to a file of the scan's own
    that the scanner can read, and scanned there; then judged.  The file is deleted with its
    directory however the scan ends.

    Raises:
        ScanFailed: the scan could not run (:class:`ScanFailed`).
        NotAScan: the scanner's answer is no scan, or names no commit (:func:`judged`,
            :func:`revision`).
        OSError: as reading the deploy script, making the file or running docker raises it.
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
        # the file's owner nor in its group, and docker writes the file 0600, which the scanner
        # then cannot open (measured 2026-11-13: "permission denied", exit 1).
        saved.chmod(0o644)
        answer = docker(scanner, ["compose", "--file", str(root / COMPOSE), "run", "--rm",
                                  "--no-TTY", "scan"], {"SCAN_IMAGE": str(saved)}, SCAN_SECONDS)
    scan = parsed(answer)
    return Scanned(image, revision(scan), tuple(judged(scan)))
