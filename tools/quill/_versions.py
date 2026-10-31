"""The versions a change moves in the files that pin them (ruling plan#2351, which narrows
``balance:R-BAL362``): a change that moves nothing but pinned versions lands with no card, as
CI's write rule reads it (``tools/quill/landing.py``, rule 9), and anything else, a major
version included, still needs one.  Two readers judge by the one walk here
(:func:`unversioned`): the landing check, over each file of production a change moves, and
the daily update queue, over every file a pull request moves, which it puts in the merge
queue only when the walk finds no reason (ruling plan#2449).

**A version-only change moves only version data in each file it changes** (:func:`moves`),
and only in the files that pin versions (:func:`pins_versions`):

- ``uv.lock``, read as TOML, as uv reads it (:func:`_lock`): its header (all but its
  packages) as it was, the same packages by name, each named once, and of each package from
  a registry only its files (``sdist`` and ``wheels``, each with its hash) and, where its
  version moves, that version and the dependencies it declares; a package from no registry
  (the project's own) as it was.  A package added or removed is no version moved, and
  neither is an edit to ``pyproject.toml``, which the image holds and which pins nothing,
  so a new package, direct or indirect, or a new range needs a card.
- **and each file of a package whose files change is that release's on its registry**
  (:func:`_released`): on the one host the lock's files from that registry were on before
  the change, and named for that package at the version the lock gives it (PyPI refuses an
  upload whose file name names another project or release, so the name ties the file to
  the release).  ``uv sync --locked`` downloads each file from the URL the lock names and
  checks only the hash the lock names beside it (measured 2026-10-31), so a change that
  repointed a file, moving no version, would install whatever that URL serves, land with
  no card and appear in no release's listing of the versions it moves.
- a ``FROM`` line of the ``Dockerfile`` and an ``image:`` line of a compose file
  (``docker-compose*.yml``), each pinning an image by tag and digest
  (``name:<version><variant>@sha256:<digest>``): only the tag's version and the digest
  move, the image's name, the tag's variant (``-alpine``) and the rest of the line as they
  were.
- a ``uses:`` line of a workflow or of one of this repository's own actions (the files
  Dependabot's ``github-actions`` entry moves), pinning an action by commit with its
  release beside it (``owner/repo@<sha> # v<version>``): only the commit and the release
  move.

Every other line of those files is as it was, byte for byte: each file is read as git holds
it, and refused when its text does not hash back to its object (a line ending changed, or
bytes no decoding keeps), so a line compared equal is equal.  Which of them production runs
is :mod:`tools.quill._production`'s to say, never this module's: today the ``Dockerfile``,
``docker-compose.yml`` and the workflow that builds and signs the image.

**A major is the release up to its first number that is not zero** (:func:`_major`, its
epoch first), so ``12.3.0`` to ``13.0.0`` crosses one and a 0.x package's minor is its
major, as ``pyproject.toml``'s ranges hold each direct package (``uv add --bounds major``'s
form); a calendar version (``2026.5``) crosses one each year.  Every package is judged,
indirect ones included: ``uv lock --upgrade`` moves an indirect package across a major
without any ``pyproject.toml`` edit (pillow ``12.3.0`` to ``13.0.0`` through
``qrcode[pil]``, measured 2026-10-30 at plan#2325).  A version this module cannot read moves
no version it can vouch for, so it is refused.

**What one side pins** (:func:`pinned`, read by the release tool's listing of the versions a
release moves, ``tools/release/_changes.py``) asks nothing of what a change moves, since a
release carries cards' own edits to these files beside the versions an update moved: each
package the lock holds at its version, a lock forked by environment pinning each version a
name holds, and each line of any other such file that pins one, by what it pins and its
version as the line spells it, the tag's version through the digest
(``3.14-slim@sha256:<digest>``) or the commit through the release (``<commit> # v7.0.1``).
A line pinning by any other form (a tag alone, a tag naming no version, an action of this
repository's own) pins none this module reads.
"""
from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from tools.ci.gitcmd import git
from tools.quill._production import DOCKERFILE, differences, tree

#: The lock every install reads (ruling ``balance:R-BAL294``).
LOCK = "uv.lock"
#: The modes of a file whose text a version-only change may move: a file, executable or not.
_FILE_MODES = ("100644", "100755")
#: A version's epoch and release (PEP 440), as uv writes them, any pre, post, dev or local
#: part after.
_RELEASE = re.compile(r"(?:(?P<epoch>[0-9]+)!)?(?P<release>[0-9]+(?:\.[0-9]+)*)")
#: An image pinned by tag and digest, the tag a version and a variant: what it pins, the
#: version, the variant and the digest.
_IMAGE = (r"(?P<what>[^\s@]+):(?P<version>[0-9]+(?:\.[0-9]+)*)(?P<variant>[A-Za-z0-9_.-]*)"
          r"@sha256:(?P<exact>[0-9a-f]{64})")
#: What a package from a registry may change where its version does not move: its files, a
#: source distribution and wheels, each with its hash.
_DISTRIBUTIONS = frozenset({"sdist", "wheels"})
#: What a package from a registry may change where its version moves: that version, its
#: files and the dependencies it declares.
_MOVED = _DISTRIBUTIONS | {"version", "dependencies", "optional-dependencies"}
#: The endings of a source distribution's file name a registry serves: PEP 625's
#: ``.tar.gz``, and the ``.zip`` older releases on PyPI still carry.
_SOURCE_ENDINGS = (".tar.gz", ".zip")
#: The ending of a wheel's file name, which holds five or six fields split by ``-`` (its
#: project, version, an optional build number, then its Python, ABI and platform tags).
_WHEEL_ENDING = ".whl"
#: How many fields a wheel's file name holds, without and with a build number.
_WHEEL_FIELDS = (5, 6)
#: A run of the characters PEP 503 reads as one ``-`` in a project's name.
_SEPARATORS = re.compile(r"[-_.]+")


class NotVersions(ValueError):
    """A change to a file production runs that moves more than its pinned versions; its
    message says what more, as a clause naming the file."""


@dataclass(frozen=True)
class Move:
    """One version a change moves in a file production runs: ``what`` (a package, an image,
    an action) moves from ``old`` to ``new`` (a package's version, the version in an image's
    tag, an action's release), across a ``major`` or not.  A digest or a commit moved under
    the same tag or release is a move from that version to itself."""

    what: str
    old: str
    new: str
    major: bool


@dataclass(frozen=True)
class Pin:
    """One version a file production runs pins (:func:`pinned`): ``what`` (a package, an
    image, an action) at ``version``, a package's as the lock holds it, an image's as its
    line spells its tag's version through its digest, an action's its commit through its
    release."""

    what: str
    version: str


@dataclass(frozen=True)
class _Pins:
    """How one file pins versions, a line each: ``line``, the expression a pinning line
    matches whole, whose ``version`` and ``exact`` groups are what a version-only change may
    move and whose ``what`` group is what it pins; ``slots``, how a sentence names the two."""

    line: re.Pattern[str]
    slots: str


#: An action pinned by commit, its release beside it: a ``uses:`` line of a workflow or an
#: action.
_USES = _Pins(re.compile(r"\s*(?:-\s+)?uses:\s*(?P<what>[^\s@]+)@(?P<exact>[0-9a-f]{40})"
                         r"\s+#\s+v(?P<version>[0-9]+(?:\.[0-9]+)*)\s*"),
              "its action's commit and release")
#: How each file that pins versions a line each pins them: the rule of the first pattern its
#: path matches whole (``PurePath.full_match``, whose ``*`` crosses no ``/``).
_LINES = (
    (DOCKERFILE, _Pins(re.compile(rf"\s*(?i:FROM)\s+(?:--\S+\s+)*{_IMAGE}(?:\s.*)?"),
                       "the version in its image's tag and its digest")),
    ("docker-compose*.yml", _Pins(re.compile(rf"\s*image:\s*{_IMAGE}(?:\s.*)?"),
                                  "the version in its image's tag and its digest")),
    (".github/workflows/*.yml", _USES),
    (".github/actions/*/action.yml", _USES),
)


def _rule(path: str) -> _Pins | None:
    """How ``path`` pins versions a line each (:data:`_LINES`), None where it pins none so."""
    return next((rule for pattern, rule in _LINES if PurePosixPath(path).full_match(pattern)),
                None)


def pins_versions(path: str) -> bool:
    """Whether ``path`` is a file that pins versions: the lock, or a file that pins them a
    line each (:data:`_LINES`)."""
    return path == LOCK or _rule(path) is not None


def _major(version: str) -> tuple[int, ...] | None:
    """The major version ``version`` names: its epoch, then its release up to and including
    its first number that is not zero, trailing zeros dropped first (so ``1`` and ``1.0.2``
    name one major, and ``0.6.3`` names ``0.6``); None when it starts with no release."""
    if (match := _RELEASE.match(version)) is None:
        return None
    release = [int(number) for number in match["release"].split(".")]
    while release and not release[-1]:
        release.pop()
    end = next((at + 1 for at, number in enumerate(release) if number), 0)
    return (int(match["epoch"] or 0), *release[:end])


def _exact(root: Path, path: str, oid: str) -> list[str]:
    """The lines of ``path``'s object ``oid``, as git holds it.

    Raises:
        NotVersions: when its text does not hash back to ``oid`` (a line ending, or bytes no
            decoding keeps), so its lines are not the file's.
        tools.ci.gitcmd.GitError: when git cannot read or hash it.
    """
    text = git(root, "cat-file", "blob", oid)
    if git(root, "hash-object", "--stdin", stdin=text).strip() != oid:
        raise NotVersions(f"{path} holds text this check cannot read byte for byte (a line "
                          "ending, or bytes no decoding keeps)")
    return text.split("\n")


def _object(path: str, held: str) -> str:
    """The object of ``path``, held as ``held`` (its mode and object, as
    :func:`_production.changed` gives them).

    Raises:
        NotVersions: when it is no file, but a link or a submodule's commit, whose text is
            not the file's.
    """
    mode, oid = held.split(" ")
    if mode not in _FILE_MODES:
        raise NotVersions(f"{path} is no file, but a link or a submodule's commit")
    return oid


def _spans(pin: re.Match[str]) -> list[tuple[int, int]]:
    """Where a pinning line's (:class:`_Pins`) ``version`` and ``exact`` groups lie in it,
    the earlier first."""
    return sorted((pin.span("version"), pin.span("exact")))


def _frame(pin: re.Match[str]) -> tuple[str, ...]:
    """A pinning line (:class:`_Pins`) without what a version-only change may move: its text
    around its ``version`` and ``exact`` groups."""
    (start, end), (later, last) = _spans(pin)
    line = pin.string
    return line[:start], line[end:later], line[last:]


def _version(pin: re.Match[str]) -> str:
    """The version a pinning line (:class:`_Pins`) pins, as it spells it: its text from the
    first of its ``version`` and ``exact`` groups through the last."""
    (start, _), (_, last) = _spans(pin)
    return pin.string[start:last]


def _lines(root: Path, path: str, rule: _Pins, old: str, new: str) -> tuple[Move, ...]:
    """Each version the change from object ``old`` to ``new`` of ``path``, a file pinning
    versions a line each by ``rule`` (:data:`_LINES`), moves, in the file's order.

    Raises:
        NotVersions: when a line is added or deleted, or one changes that pins nothing, or
            changes more than what it pins (the module's rules).
        tools.ci.gitcmd.GitError: when git cannot read or hash either object.
    """
    before, after = _exact(root, path, old), _exact(root, path, new)
    if len(before) != len(after):
        raise NotVersions(f"{path} adds or deletes a line")
    found = []
    for number, (was, now) in enumerate(zip(before, after), start=1):
        if was == now:
            continue
        old_pin, new_pin = rule.line.fullmatch(was), rule.line.fullmatch(now)
        if old_pin is None or new_pin is None:
            raise NotVersions(f"{path} changes line {number}, which pins no version by "
                              f"{rule.slots}")
        if _frame(old_pin) != _frame(new_pin):
            raise NotVersions(f"{path} changes more than {rule.slots} on line {number}")
        found.append(Move(old_pin["what"], old_pin["version"], new_pin["version"],
                          _major(old_pin["version"]) != _major(new_pin["version"])))
    return tuple(found)


def _read(text: str) -> dict[str, object]:
    """The lock whose text is ``text``, as TOML reads it.

    Raises:
        NotVersions: when it is no TOML.
    """
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise NotVersions(f"{LOCK} is no TOML: {error}") from error


def _named(lock: dict[str, object]) -> list[dict[str, object]]:
    """Each package ``lock`` holds, in its order.

    Raises:
        NotVersions: when one has no name.
    """
    packages = lock.get("package", [])
    if not isinstance(packages, list) or not all(
            isinstance(package, dict) and isinstance(package.get("name"), str)
            for package in packages):
        raise NotVersions(f"{LOCK} holds a package with no name")
    return packages


def _packages(lock: dict[str, object]) -> dict[str, dict[str, object]]:
    """Each package ``lock`` holds (:func:`_named`), by its name.

    Raises:
        NotVersions: as :func:`_named` raises it, or when a name is held more than once (a
            resolution forked by environment), which a version-only change cannot be told
            by.
    """
    packages = _named(lock)
    if twice := sorted(name for name, count in Counter(
            package["name"] for package in packages).items() if count > 1):
        raise NotVersions(f"{LOCK} holds {', '.join(twice)} more than once")
    return {package["name"]: package for package in packages}


def _lock(old: str, new: str) -> tuple[Move, ...]:
    """Each version the change from the lock text ``old`` to ``new`` moves, by package name.

    Raises:
        NotVersions: when either is no TOML or holds a package :func:`_packages` refuses, or
            the change moves more than versions (the module's rules), or one it moves names
            no version :func:`_major` reads.
    """
    before, after = _read(old), _read(new)
    if header := sorted(key for key in before.keys() | after.keys()
                        if key != "package" and before.get(key) != after.get(key)):
        raise NotVersions(f"{LOCK} changes its {', '.join(header)}")
    was, now = _packages(before), _packages(after)
    if added := sorted(now.keys() - was.keys()):
        raise NotVersions(f"{LOCK} adds {', '.join(added)}, a package of no version before")
    if removed := sorted(was.keys() - now.keys()):
        raise NotVersions(f"{LOCK} removes {', '.join(removed)}")
    homes = _homes(was.values())
    return tuple(move for name in sorted(was)
                 if (move := _package(name, was[name], now[name], homes)) is not None)


def _registry(package: dict[str, object]) -> str | None:
    """The URL of the registry a lock's ``package`` comes from, None where it comes from
    none (the project's own, a path, a URL)."""
    source = package.get("source")
    if not isinstance(source, dict) or set(source) != {"registry"}:
        return None
    return str(source["registry"])


def _distributions(package: dict[str, object]) -> list[object]:
    """Each file a lock's ``package`` names: its source distribution, then each wheel; a
    ``wheels`` that is no list counts as one file, which names no URL."""
    wheels = package.get("wheels", [])
    return [*([package["sdist"]] if "sdist" in package else []),
            *(wheels if isinstance(wheels, list) else [wheels])]


def _url(file: object) -> str | None:
    """The URL a lock's ``file`` entry names, None where it names none."""
    url = file.get("url") if isinstance(file, dict) else None
    return url if isinstance(url, str) else None


def _host(url: str) -> str:
    """The scheme and host ``url`` is served from, as a URL spells them."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _homes(packages: Iterable[dict[str, object]]) -> dict[str, frozenset[str]]:
    """The hosts each registry's files lie on in a lock holding ``packages``, by the
    registry's URL: the host (:func:`_host`) of every file a package from it names."""
    found: dict[str, set[str]] = {}
    for package in packages:
        if (registry := _registry(package)) is not None:
            found.setdefault(registry, set()).update(
                _host(url) for file in _distributions(package)
                if (url := _url(file)) is not None)
    return {registry: frozenset(hosts) for registry, hosts in found.items()}


def _canonical(project: str) -> str:
    """A project's name as PEP 503 compares it: lower case, each run of ``-``, ``_`` and
    ``.`` one ``-``."""
    return _SEPARATORS.sub("-", project).lower()


def _release_named(file_name: str) -> tuple[str, str] | None:
    """The project (:func:`_canonical`) and version a distribution's ``file_name`` names, a
    wheel's or a source distribution's; None where it is neither's."""
    if file_name.endswith(_WHEEL_ENDING):
        fields = file_name.removesuffix(_WHEEL_ENDING).split("-")
        return (_canonical(fields[0]), fields[1]) if len(fields) in _WHEEL_FIELDS else None
    for ending in _SOURCE_ENDINGS:
        if file_name.endswith(ending):
            project, dash, version = file_name.removesuffix(ending).rpartition("-")
            return (_canonical(project), version) if project and dash and version else None
    return None


def _released(name: str, version: str, package: dict[str, object],
              hosts: frozenset[str]) -> None:
    """Refuse the lock's entry ``package``, for package ``name`` from a registry at
    ``version``, unless each file it names is that release's on the registry (the module's
    rules): on the one host of ``hosts``, where the registry's files lay before the change,
    and named for that package at that version.

    Raises:
        NotVersions: when the registry's files lay on no one host before, or a file names no
            URL, lies on another host, or is named for no file of that release.
    """
    if len(hosts) != 1:
        raise NotVersions(f"{LOCK}'s files from {name}'s registry lay on {len(hosts)} hosts "
                          "before, not one")
    (home,) = hosts
    for file in _distributions(package):
        if (url := _url(file)) is None:
            raise NotVersions(f"{LOCK} names a file of {name} by no URL")
        file_name = urlsplit(url).path.rpartition("/")[2]
        if (host := _host(url)) != home:
            raise NotVersions(f"{LOCK} moves {name}'s file {file_name} to {host}, not {home}, "
                              "where its registry's files lay")
        if _release_named(file_name) != (_canonical(name), version):
            raise NotVersions(f"{LOCK} gives {name} {version} the file {file_name}, which is "
                              "no file of that release")


def _package(name: str, old: dict[str, object], new: dict[str, object],
             homes: dict[str, frozenset[str]]) -> Move | None:
    """The version the change from the lock's entry ``old`` for package ``name`` to ``new``
    moves, None where it moves none: a package from a registry may change only its files,
    each that release's on the registry (:func:`_released`, its hosts from ``homes``, by
    registry, as :func:`_homes` reads them before the change), and where its version moves,
    that version and the dependencies it declares too; one from no registry nothing.

    Raises:
        NotVersions: when the change moves more than that, names a file that is not the
            release's, or moves a version :func:`_major` cannot read.
    """
    was, now = old.get("version"), new.get("version")
    registry = _registry(old)
    free = (_MOVED if was != now else _DISTRIBUTIONS) if registry is not None else frozenset()
    if changed := sorted(key for key in old.keys() | new.keys()
                         if key not in free and old.get(key) != new.get(key)):
        raise NotVersions(f"{LOCK} changes {name}'s {', '.join(changed)}")
    if registry is not None and any(old.get(key) != new.get(key) for key in _DISTRIBUTIONS):
        _released(name, str(now), new, homes.get(registry, frozenset()))
    if was == now:
        return None
    majors = [_major(version) if isinstance(version, str) else None for version in (was, now)]
    if None in majors:
        raise NotVersions(f"{LOCK} moves {name} from {was} to {now}, a version this check "
                          "cannot read")
    return Move(name, str(was), str(now), majors[0] != majors[1])


def moves(root: Path, path: str, was: str | None, now: str | None,
          where: str = "production") -> tuple[Move, ...]:
    """Each version a change moves in ``path``, a file whose mode and object ``where`` (the
    files the change is read in: production, as :func:`_production.changed` gives them, or
    the whole tree) held as ``was`` before the change and as ``now`` after it (None where it
    holds no such file): by package name in the lock, in the file's order in any other.

    Raises:
        NotVersions: when the change moves more than versions there (the module's rules).
        tools.ci.gitcmd.GitError: when git cannot read or hash either object.
    """
    if not pins_versions(path):
        raise NotVersions(f"{path} pins no version")
    if was is None or now is None:
        raise NotVersions(f"{path} {'enters' if was is None else 'leaves'} {where}")
    if was.split(" ")[0] != now.split(" ")[0]:
        raise NotVersions(f"{path} changes its mode")
    old, new = _object(path, was), _object(path, now)
    if (rule := _rule(path)) is None:
        return _lock(git(root, "cat-file", "blob", old), git(root, "cat-file", "blob", new))
    return _lines(root, path, rule, old, new)


def unversioned(root: Path, changed: Mapping[str, tuple[str | None, str | None]],
                where: str = "production") -> tuple[str, ...]:
    """Why a change is no change that moves pinned versions alone, none across a major, each
    as a clause naming its file; none when it is one.  ``changed`` holds each path where
    ``where`` differs (:func:`moves`), with its mode and object before and after.  The one
    walk the landing check (rule 9) and the daily update queue both judge by.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read a file it changes.
    """
    reasons: list[str] = []
    for path, (was, now) in changed.items():
        try:
            reasons += [f"{path} moves {move.what} from {move.old} to {move.new}, a new major "
                        "version" for move in moves(root, path, was, now, where) if move.major]
        except NotVersions as error:
            reasons.append(str(error))
    return tuple(reasons)


def unversioned_between(root: Path, old: str, new: str) -> tuple[str, ...]:
    """Why the change from commit ``old`` to commit ``new`` is no change that moves pinned
    versions alone, none across a major, over every file of the tree it changes
    (:func:`unversioned`); none when it is one.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read either commit, or a file it changes.
    """
    return unversioned(root, differences(tree(root, old), tree(root, new)), "the tree")


def pinned(root: Path, path: str, held: str | None) -> frozenset[Pin]:
    """Every version ``path``, a file that pins versions (:func:`pins_versions`), pins where
    production holds it as ``held`` (its mode and object, as :func:`_production.changed`
    gives them; None where that production holds no such file, which pins none): in the
    lock each package at its version, a package of no version (a project whose version is
    dynamic) pinning none; in any other file what each line that pins one (:data:`_LINES`)
    pins, at its version as the line spells it (:func:`_version`).  It asks nothing of what
    a change moves, so a lock forked by environment pins each version a name holds.

    Raises:
        NotVersions: when it pins no version, is no file (:func:`_object`), or is a lock
            that is no TOML (:func:`_read`) or holds a package with no name (:func:`_named`).
        tools.ci.gitcmd.GitError: when git cannot read its object.
    """
    if not pins_versions(path):
        raise NotVersions(f"{path} pins no version")
    if held is None:
        return frozenset()
    text = git(root, "cat-file", "blob", _object(path, held))
    if (rule := _rule(path)) is None:
        return frozenset(Pin(package["name"], str(package["version"]))
                         for package in _named(_read(text)) if "version" in package)
    return frozenset(Pin(pin["what"], _version(pin))
                     for pin in map(rule.line.fullmatch, text.split("\n")) if pin is not None)
