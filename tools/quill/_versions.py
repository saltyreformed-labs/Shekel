"""The versions a change moves in the files that pin them (ruling plan#2351, which narrows
``balance:R-BAL362``): a change that moves nothing but pinned versions lands with no card, as
CI's write rule reads it (``tools/quill/landing.py``, rule 9), and anything else, a major
version included, still needs one.  Three readers judge by the one walk here
(:func:`_why`): the landing check, over each file of production a change moves
(:func:`unversioned`), and the daily update queue, over every file a pull request moves
(:func:`unversioned_between`), both through :func:`_reasons`, its run over each file a
change moves; the queue puts a pull request in the merge queue only when the walk finds no
reason (ruling plan#2449), and mentions the developer once for each thing one waits for a
card on (:class:`Reason`; rulings plan#2450, plan#2467).  The third is the Monday lock
refresh, over the lock alone (:func:`lock_unversioned`): it judges the week's lock by it
before any pull request carries it, and each package it moves alone with
``pyproject.toml`` as it is, to keep that change only where the walk finds it needs a card.

**A version-only change moves only version data in each file it changes** (:func:`moves`),
and only in the files that pin versions (:func:`pins_versions`):

- ``uv.lock``, read as TOML, as uv reads it (:func:`_lock_read`): its header (all but its
  packages) as it was, the same packages by name, each named once, and of each package from
  a registry only its files (``sdist`` and ``wheels``, each with its hash) and, where its
  version moves, that version; a package from no registry (the project's own) as it was.
  A package added or removed is no version moved, and neither is an edit to
  ``pyproject.toml``, which the image holds and which pins nothing, so a new package,
  direct or indirect, or a new range needs a card.  **What a package needs stays as it
  was** (ruling plan#2490): each package it declares it needs, and each its extras need,
  with that entry's extras and marker.  A move that changed it could bring a package the
  lock holds for one install set (the dev tools) into another (production, the image's
  build, the landing job) with no card, or take one out, and no install set is named here
  to tell them apart: so every package is held to it, the lock's every install set kept as
  it was.
- **and each file of a package whose version or files move is that release's on its
  registry** (:func:`_released`): on the one host the lock's files from that registry were
  on before the change, named for that package at the version the lock gives it (PyPI
  refuses an upload whose file name names another project or release, so the name ties the
  file to the release), with a hash, and with nothing uv does not write of a file
  (:data:`_FILE_KEYS`).  ``uv sync --locked`` downloads each file from the URL the lock
  names and checks only the hash the lock names beside it (measured 2026-10-31), so a
  change that repointed a file, moving no version, would install whatever that URL serves,
  land with no card and appear in no release's listing of the versions it moves; and a
  version moved with its files as they were would be listed as a release it never
  installs.  A package with a wheel keeps one, and one with none (psycopg-c, which the
  image compiles against its own libpq) gains none (:func:`_judge`).
- a ``FROM`` line of the ``Dockerfile`` and an ``image:`` line of a compose file
  (``docker-compose*.yml``), each pinning an image by tag and digest
  (``name:<version><variant>@sha256:<digest>``): only the tag's version and the digest
  move, the image's name, the tag's variant (``-alpine``) and the rest of the line as they
  were.  **An image's version is read from its tag alone** (ruling plan#2474): Docker pulls
  a digest only from the image's own repository, so no other repository's image can stand
  in, and which digests a tag named before cannot be read, so whether a digest is its tag's
  is not asked.
- a ``uses:`` line of a workflow or of one of this repository's own actions (the files
  Dependabot's ``github-actions`` entry moves), pinning an action by commit with its
  release beside it (``owner/repo@<sha> # v<version>``): only the commit and the release
  move, **and the commit is the release's** (ruling plan#2474, :func:`_vouched`): the one
  the action's own repository tags ``v<version>``, read from GitHub (:func:`_tagged`).
  GitHub runs the commit, never the label beside it, and runs a commit that only a fork of
  the repository holds under the original's name, so a label alone vouches for nothing.  A
  commit moved under an unchanged release is refused before any read: one release has one
  commit, so either the release's tag was moved after the fact or one of the two pins was
  never its commit.  What the repository tags now is read as the release, a tag it moves or
  creates itself included: Dependabot's seven-day cooldown is what holds a new one back.
  GitHub unread is no judgment at all: the read's :class:`~tools.ci.gitcmd.GitError` is
  raised, which the landing check reports as a change it could not judge and the daily
  queue as a pull request it failed on, each failing closed, never as a change needing a
  card.

Every other line of those files is as it was, byte for byte: each file is read as git holds
it, refused when its bytes decode to no text (:func:`_text`, which every file this module
reads is read through) and when its text does not hash back to its object (a line ending
changed, or bytes no decoding keeps), so a line compared equal is equal.  Which of them
production runs is :mod:`tools.quill._production`'s to say, never this module's.

**A major is the release up to its first number that is not zero** (:func:`_major`, its
epoch first), so ``12.3.0`` to ``13.0.0`` crosses one and a 0.x package's minor is its
major, as ``pyproject.toml``'s ranges hold each direct package (``uv add --bounds major``'s
form); a calendar version (``2026.5``) crosses one each year.  Every package is judged,
indirect ones included: ``uv lock --upgrade`` moves an indirect package across a major
without any ``pyproject.toml`` edit (pillow ``12.3.0`` to ``13.0.0`` through
``qrcode[pil]``, measured 2026-10-30 at plan#2325).  A version this module cannot read moves
no version it can vouch for, so it is refused.

**What one side pins** (:func:`pinned`, read by :func:`versions_moved`) asks nothing of what a
change moves, since a release carries cards' own edits to these files beside the versions an
update moved: each
package the lock holds at its version, a lock forked by environment pinning each version a
name holds, and each line of any other such file that pins one, by what it pins and its
version as the line spells it, the tag's version through the digest
(``3.14-slim@sha256:<digest>``) or the commit through the release (``<commit> # v7.0.1``).
A line pinning by any other form (a tag alone, a tag naming no version, an action of this
repository's own) pins none this module reads.

**What a release would move** (:func:`versions_moved`, each :class:`Moved`) is what each side
pins, compared over the files that differ.  The release tool's listing of a release's versions
reads production's (``tools/release/_changes.py``), and :func:`versions_waiting`, the question
the daily job's mention of a release (``tools/update/due.py``) and ``quill next`` both ask of
``dev`` against ``main`` (rulings plan#2829, plan#2876), reads the whole tree: ``main`` runs its
own copy of every workflow, so a version only they pin still waits for a release.  A pinning
file one side holds no copy of pins every version against none there, so while ``main`` holds
no lock (until the first release of plan#1183) every locked package reads as waiting: true, as
a release would bring them all, and never a list anyone is shown, since the job reaches
``main`` in that same release.

**What a lock change does to versions alone** (:func:`lock_moves`) is :func:`lock_change`'s
reading with no judgment: the Monday refresh's reading of each lock it makes from an edited
``pyproject.toml`` (a new major prepared as a change of its own, ruling plan#2450), whose
own entry in the lock moves with the edit and which needs a card anyway.  Both build each
:class:`Move` by the one reading of a major.
"""
from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from tools.ci.gitcmd import DEV, MAIN, git, tracking
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
#: What uv writes of each file a registry serves (measured 2026-11-01: these four on every
#: one of the lock's 897), of which a file must name its URL and its hash.
_FILE_KEYS = frozenset({"url", "hash", "size", "upload-time"})
#: What a package's entry declares it needs: the packages it depends on, and its extras'.
_NEEDS = ("dependencies", "optional-dependencies")
#: What a package from a registry may change where its version moves, as
#: :func:`lock_change` reads it: that version, its files and what it needs, which it
#: reports (:attr:`LockChange.bringing`) for the walk to refuse and the refresh to hold.
_MOVED = _DISTRIBUTIONS | {"version", *_NEEDS}
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
    tag, an action's release), across a ``major`` or not.  A digest moved under the same tag
    is a move from that version to itself; a commit moved under the same release is refused
    (:func:`_vouched`)."""

    what: str
    old: str
    new: str
    major: bool


@dataclass(frozen=True)
class Reason:
    """One reason the walk (:func:`_why`) gives that a change is no change that moves
    pinned versions alone, none across a major: ``clause``, saying so and naming its file;
    and ``waits_on``, what the change waits for a card on: for a move across a major, what
    moves and the major it moves into (``pillow 13``, ``zeta 0.7``), the same however far
    within that major and in whichever file it moves, and for a refusal its clause."""

    clause: str
    waits_on: str


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
#: Where the repository of each action a ``uses:`` line pins is read (:func:`_tagged`):
#: GitHub, whose git server lists a public repository's tags with no credential.
_GITHUB = "https://github.com"
#: What a read of :data:`_GITHUB` runs with beside its environment: git's terminal prompt off,
#: so a repository GitHub answers for with a request for credentials (one it does not hold)
#: is a refusal rather than a wait on a terminal no one watches.
_NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0"}
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


def ceiling(version: str) -> str | None:
    """The first release of the major after ``version``'s (:func:`_major`), as a ``<``
    bound holds a package below it: ``14`` for ``13.0.1``, ``0.7`` for ``0.6.3``, ``2027``
    for ``2026.5`` and ``1!3`` for ``1!2.0``, so every version from ``version`` up to it
    names ``version``'s major; None where no such release follows (a version starting with
    no release, or a release of zeros alone, whose every successor crosses a major)."""
    if (major := _major(version)) is None or len(major) < 2:
        return None
    epoch, *release = major
    release[-1] += 1
    return _spelled((epoch, *release))


def _spelled(major: tuple[int, ...]) -> str:
    """``major``, a major as :func:`_major` gives it (its epoch, then its release), spelled
    as a release is: ``14``, ``0.7``, ``1!3``, and a release of zeros alone ``0``."""
    epoch, *release = major
    text = ".".join(map(str, release)) or "0"
    return f"{epoch}!{text}" if epoch else text


def _text(root: Path, path: str, oid: str) -> str:
    """The text of ``path``'s object ``oid``, as git reads it out: how every file this module
    reads is read.

    Raises:
        NotVersions: when its bytes decode to no text, so no line of it can be read.
        tools.ci.gitcmd.GitError: when git cannot read it.
    """
    try:
        return git(root, "cat-file", "blob", oid)
    except UnicodeDecodeError as error:
        raise NotVersions(f"{path} holds bytes this check cannot decode as text") from error


def _exact(root: Path, path: str, oid: str) -> list[str]:
    """The lines of ``path``'s object ``oid``, as git holds it.

    Raises:
        NotVersions: when its bytes decode to no text (:func:`_text`), or its text does not
            hash back to ``oid`` (a line ending, or bytes no decoding keeps), so its lines
            are not the file's.
        tools.ci.gitcmd.GitError: when git cannot read or hash it.
    """
    text = _text(root, path, oid)
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
            changes more than what it pins, or moves an action's commit to one that is not
            its release's (the module's rules, :func:`_vouched`).
        tools.ci.gitcmd.GitError: when git cannot read or hash either object, or read an
            action's repository from GitHub (:func:`_tagged`).
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
        if rule is _USES:
            _vouched(root, f"{path} pins {new_pin['what']} on line {number}", old_pin, new_pin)
        found.append(Move(old_pin["what"], old_pin["version"], new_pin["version"],
                          _major(old_pin["version"]) != _major(new_pin["version"])))
    return tuple(found)


def _vouched(root: Path, where: str, old_pin: re.Match[str], new_pin: re.Match[str]) -> None:
    """Refuse a ``uses:`` line's move from ``old_pin`` to ``new_pin``, ``where`` naming its
    file, line and action, unless its commit is its release's (ruling plan#2474): a commit
    moved under an unchanged release is refused unread, and any other move's commit must be
    the one the action's repository tags with the line's release (:func:`_tagged`).

    Raises:
        NotVersions: when it is not.
        tools.ci.gitcmd.GitError: when GitHub cannot be read (:func:`_tagged`).
    """
    release, commit = new_pin["version"], new_pin["exact"]
    if release == old_pin["version"]:
        raise NotVersions(f"{where} at another commit under its release v{release}: one "
                          "release has one commit, so its tag was moved or a pin was never "
                          "its commit")
    repository = "/".join(new_pin["what"].split("/")[:2])
    tagged = _tagged(root, repository, release)
    if tagged is None:
        raise NotVersions(f"{where} as v{release}, a release {repository} does not tag")
    if tagged != commit:
        raise NotVersions(f"{where} at {commit} as v{release}, which {repository} tags at "
                          f"{tagged}")


def _tagged(root: Path, repository: str, release: str) -> str | None:
    """The commit ``repository`` (``owner/repo``) tags with ``release``: its tag
    ``v<release>``, an annotated tag read through to the commit it tags, as GitHub
    (:data:`_GITHUB`) lists it to ``git ls-remote``, run in ``root``; None where it holds no
    such tag.

    Raises:
        tools.ci.gitcmd.GitError: when GitHub cannot be read, with git's words.
    """
    tag = f"refs/tags/v{release}"
    listed = {name: commit for commit, _, name in
              (line.partition("\t") for line in git(
                  root, "ls-remote", f"{_GITHUB}/{repository}", tag, f"{tag}^{{}}",
                  environment=_NO_PROMPT).splitlines())}
    return listed.get(f"{tag}^{{}}", listed.get(tag))


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


@dataclass(frozen=True)
class LockChange:
    """What a change to the lock does (:func:`lock_change`): ``moves``, each package it moves,
    by name; ``added`` and ``removed``, each package it adds or removes, by name; and
    ``bringing``, each package whose move changes what it brings with it (what it needs,
    :func:`_needs`), by name, at its version before: a package's new version needing a
    package or no longer needing one, the lock holding it or not, or needing one under
    another marker or with other extras."""

    moves: tuple[Move, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    bringing: Mapping[str, str]


def _needs(package: dict[str, object]) -> tuple[object, ...]:
    """What a lock's ``package`` needs, as the lock declares it (:data:`_NEEDS`): each entry
    as it is, with its extras and marker, so two readings are equal only where every install
    set takes the same packages through the package."""
    return tuple(package.get(key) for key in _NEEDS)


def lock_change(old: str, new: str) -> LockChange:
    """What the change from the lock text ``old`` to ``new`` does (:class:`LockChange`), each
    package both hold judged by the module's rules (:func:`_judge`).

    Raises:
        NotVersions: when either is no TOML or holds a package :func:`_packages` refuses, or
            the change moves more than versions in a package both hold, or changes the
            lock's header (the module's rules), or one it moves names no version
            :func:`_major` reads.
    """
    before, after = _read(old), _read(new)
    if header := sorted(key for key in before.keys() | after.keys()
                        if key != "package" and before.get(key) != after.get(key)):
        raise NotVersions(f"{LOCK} changes its {', '.join(header)}")
    was, now = _packages(before), _packages(after)
    homes = _homes(was.values())
    for name in sorted(was.keys() & now.keys()):
        _judge(name, was[name], now[name], homes)
    return _change(was, now)


def lock_moves(old: str, new: str) -> LockChange:
    """What the change from the lock text ``old`` to ``new`` does to the versions the lock
    holds (:class:`LockChange`), judging nothing else it changes: the Monday refresh's reading
    of a lock it made from an edited ``pyproject.toml``, whose own entry in the lock (the
    project's requirements) :func:`lock_change` refuses to see change.

    Raises:
        NotVersions: when either is no TOML or holds a package :func:`_packages` refuses, or
            one it moves names no version :func:`_major` reads.
    """
    return _change(_packages(_read(old)), _packages(_read(new)))


def _change(was: dict[str, dict[str, object]],
            now: dict[str, dict[str, object]]) -> LockChange:
    """What the change from the lock holding the packages ``was`` to the one holding ``now``
    (each by name, :func:`_packages`) does to versions (:class:`LockChange`).

    Raises:
        NotVersions: when a package both hold moves a version :func:`_major` cannot read.
    """
    added, removed = now.keys() - was.keys(), was.keys() - now.keys()
    moved = tuple(move for name in sorted(was.keys() & now.keys())
                  if (move := _move(name, was[name], now[name])) is not None)
    return LockChange(moved, tuple(sorted(added)), tuple(sorted(removed)),
                      {move.what: move.old for move in moved
                       if _needs(was[move.what]) != _needs(now[move.what])})


@dataclass(frozen=True)
class _Read:
    """What the walk reads of a change to one file that pins versions: ``moves``, each
    version it moves, and ``refusal``, the clause saying what more it does, None where it
    moves versions alone.  A lock change can say both: a package added beside another moved
    across a major (:func:`_lock_read`)."""

    moves: tuple[Move, ...]
    refusal: str | None = None


def _lock_read(old: str, new: str) -> _Read:
    """What the walk reads of the change from the lock text ``old`` to ``new``: each version
    it moves, by package name, and the first of what more it does: a package added, a
    package removed, or a move to a version that changes what it needs
    (:attr:`LockChange.bringing`).

    Raises:
        NotVersions: as :func:`lock_change` raises it, when no version it moves can be read.
    """
    change = lock_change(old, new)
    refusal = None
    if change.added:
        refusal = f"{LOCK} adds {', '.join(change.added)}, a package of no version before"
    elif change.removed:
        refusal = f"{LOCK} removes {', '.join(change.removed)}"
    elif change.bringing:
        moved = " and ".join(f"{move.what} from {move.old} to {move.new}"
                             for move in change.moves if move.what in change.bringing)
        refusal = f"{LOCK} moves {moved}, a new version that changes what it needs"
    return _Read(change.moves, refusal)


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


def canonical(project: str) -> str:
    """A project's name as PEP 503 compares it: lower case, each run of ``-``, ``_`` and
    ``.`` one ``-``."""
    return _SEPARATORS.sub("-", project).lower()


def _release_named(file_name: str) -> tuple[str, str] | None:
    """The project (:func:`canonical`) and version a distribution's ``file_name`` names, a
    wheel's or a source distribution's; None where it is neither's."""
    if file_name.endswith(_WHEEL_ENDING):
        fields = file_name.removesuffix(_WHEEL_ENDING).split("-")
        return (canonical(fields[0]), fields[1]) if len(fields) in _WHEEL_FIELDS else None
    for ending in _SOURCE_ENDINGS:
        if file_name.endswith(ending):
            project, dash, version = file_name.removesuffix(ending).rpartition("-")
            return (canonical(project), version) if project and dash and version else None
    return None


def _released(name: str, version: str, package: dict[str, object],
              hosts: frozenset[str]) -> None:
    """Refuse the lock's entry ``package``, for package ``name`` from a registry at
    ``version``, unless each file it names is that release's on the registry (the module's
    rules): on the one host of ``hosts``, where the registry's files lay before the change,
    and named for that package at that version.

    Raises:
        NotVersions: when the registry's files lay on no one host before, or a file names no
            URL, or no hash, or more than uv writes of a file (:data:`_FILE_KEYS`), lies on
            another host, or is named for no file of that release.
    """
    if len(hosts) != 1:
        raise NotVersions(f"{LOCK}'s files from {name}'s registry lay on {len(hosts)} hosts "
                          "before, not one")
    (home,) = hosts
    for file in _distributions(package):
        if (url := _url(file)) is None:
            raise NotVersions(f"{LOCK} names a file of {name} by no URL")
        file_name = urlsplit(url).path.rpartition("/")[2]
        if more := sorted(set(file) - _FILE_KEYS):
            raise NotVersions(f"{LOCK} names {name}'s file {file_name} with {', '.join(more)}, "
                              "which uv writes of no file")
        if "hash" not in file:
            raise NotVersions(f"{LOCK} names {name}'s file {file_name} with no hash")
        if (host := _host(url)) != home:
            raise NotVersions(f"{LOCK} moves {name}'s file {file_name} to {host}, not {home}, "
                              "where its registry's files lay")
        if _release_named(file_name) != (canonical(name), version):
            raise NotVersions(f"{LOCK} gives {name} {version} the file {file_name}, which is "
                              "no file of that release")


def _judge(name: str, old: dict[str, object], new: dict[str, object],
           homes: dict[str, frozenset[str]]) -> None:
    """Refuse the change from the lock's entry ``old`` for package ``name`` to ``new`` unless
    it moves only versions: a package from a registry may change only its files, each that
    release's on the registry (:func:`_released`, its hosts from ``homes``, by registry, as
    :func:`_homes` reads them before the change), and where its version moves, that version
    and what it needs too (:data:`_MOVED`; what it needs is :func:`_lock_read`'s to
    refuse); one from no registry nothing.

    Where its version or its files move, each file is the release's, so a version moved
    with its files as they were is refused; and a package with a wheel keeps one, and one
    with none gains none, since every install would otherwise build from source a package it
    installed built, or install built one it compiled (psycopg-c against the image's libpq).

    Raises:
        NotVersions: when the change moves more than that, names a file that is not the
            release's, or gives a package a wheel where it had none or none where it had one.
    """
    was, now = old.get("version"), new.get("version")
    registry = _registry(old)
    free = (_MOVED if was != now else _DISTRIBUTIONS) if registry is not None else frozenset()
    if changed := sorted(key for key in old.keys() | new.keys()
                         if key not in free and old.get(key) != new.get(key)):
        raise NotVersions(f"{LOCK} changes {name}'s {', '.join(changed)}")
    if registry is None or (was == now and all(old.get(key) == new.get(key)
                                                for key in _DISTRIBUTIONS)):
        return
    if bool(old.get("wheels")) != bool(new.get("wheels")):
        raise NotVersions(f"{LOCK} gives {name} {now} "
                          + ("no wheel where it had one, so it would be built from its source"
                             if old.get("wheels") else
                             "a wheel where it had none, so it would no longer be built from "
                             "its source"))
    _released(name, str(now), new, homes.get(registry, frozenset()))


def _move(name: str, old: dict[str, object], new: dict[str, object]) -> Move | None:
    """The version the change from the lock's entry ``old`` for package ``name`` to ``new``
    moves, None where it moves none.

    Raises:
        NotVersions: when it moves a version :func:`_major` cannot read.
    """
    was, now = old.get("version"), new.get("version")
    if was == now:
        return None
    majors = [_major(version) if isinstance(version, str) else None for version in (was, now)]
    if None in majors:
        raise NotVersions(f"{LOCK} moves {name} from {was} to {now}, a version this check "
                          "cannot read")
    return Move(name, str(was), str(now), majors[0] != majors[1])


def _moved(root: Path, path: str, was: str | None, now: str | None,
           where: str) -> _Read:
    """What the walk reads of a change to ``path``, a file whose mode and object ``where``
    held as ``was`` before the change and as ``now`` after it (:func:`moves`).

    Raises:
        NotVersions: when the change moves more than versions there and no version it moves
            can be read (the module's rules).
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
        return _lock_read(_text(root, path, old), _text(root, path, new))
    return _Read(_lines(root, path, rule, old, new))


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
    read = _moved(root, path, was, now, where)
    if read.refusal is not None:
        raise NotVersions(read.refusal)
    return read.moves


def _reasons(root: Path, changed: Mapping[str, tuple[str | None, str | None]],
             where: str) -> tuple[Reason, ...]:
    """Why a change is no change that moves pinned versions alone, none across a major, each
    a :class:`Reason` whose clause names its file; none when it is one.  ``changed`` holds
    each path where ``where`` differs (:func:`moves`), with its mode and object before and
    after.  The walk (:func:`_why`) over each such file, which the landing check (rule 9) and
    the daily update queue both judge by.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read a file it changes.
    """
    return tuple(reason for path, (was, now) in changed.items()
                 for reason in _why(path, partial(_moved, root, path, was, now, where)))


def unversioned(root: Path, changed: Mapping[str, tuple[str | None, str | None]],
                where: str = "production") -> tuple[str, ...]:
    """The clause of each reason :func:`_reasons` gives over ``changed``, where
    ``where`` differs: the landing check's reading.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read a file it changes.
    """
    return tuple(reason.clause for reason in _reasons(root, changed, where))


def lock_unversioned(old: str, new: str) -> tuple[str, ...]:
    """Why the change from the lock text ``old`` to ``new`` is no change that moves pinned
    versions alone, none across a major, each as a clause; none when it is one: the walk
    (:func:`_why`) over the lock alone, which the Monday refresh judges the week's lock by
    before any pull request carries it, and each package it moves alone with
    ``pyproject.toml`` as it is."""
    return tuple(reason.clause for reason in _why(LOCK, partial(_lock_read, old, new)))


def _why(path: str, moved: Callable[[], _Read]) -> tuple[Reason, ...]:
    """Why the change ``moved`` reads in ``path`` is no change that moves pinned versions
    alone, none across a major: what more it does, waiting on that, then each move across a
    major, waiting on the major it moves into (every :class:`Move`'s versions are ones
    :func:`_major` reads, as both its builders refuse any other), so a major is told beside a
    package added; or, where no version it moves can be read, the reason ``moved`` refuses it
    (:class:`NotVersions`), waiting on that; none when it is one.

    Raises:
        tools.ci.gitcmd.GitError: when ``moved`` cannot read a file, or an action's
            repository on GitHub.
    """
    try:
        read = moved()
    except NotVersions as error:
        return (Reason(str(error), str(error)),)
    return (*((Reason(read.refusal, read.refusal),) if read.refusal is not None else ()),
            *(Reason(f"{path} moves {move.what} from {move.old} to {move.new}, a new major "
                     "version", f"{move.what} {_spelled(_major(move.new))}")
              for move in read.moves if move.major))


def unversioned_between(root: Path, old: str, new: str) -> tuple[Reason, ...]:
    """Why the change from commit ``old`` to commit ``new`` is no change that moves pinned
    versions alone, none across a major, over every file of the tree it changes, each a
    :class:`Reason` (:func:`_reasons`); none when it is one: the daily update queue's
    reading, which leaves a pull request with a reason and mentions the developer once for
    each thing it waits on.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read either commit, or a file it changes.
    """
    return _reasons(root, differences(tree(root, old), tree(root, new)), "the tree")


def lock_pins(text: str) -> frozenset[Pin]:
    """Every version the lock whose text is ``text`` pins: each package at its version, a
    package of no version (a project whose version is dynamic) pinning none, and a lock
    forked by environment pinning each version a name holds.

    Raises:
        NotVersions: when it is no TOML (:func:`_read`) or holds a package with no name
            (:func:`_named`).
    """
    return frozenset(Pin(package["name"], str(package["version"]))
                     for package in _named(_read(text)) if "version" in package)


def pinned(root: Path, path: str, held: str | None) -> frozenset[Pin]:
    """Every version ``path``, a file that pins versions (:func:`pins_versions`), pins where
    production holds it as ``held`` (its mode and object, as :func:`_production.changed`
    gives them; None where that production holds no such file, which pins none): in the
    lock what :func:`lock_pins` reads; in any other file what each line that pins one
    (:data:`_LINES`) pins, at its version as the line spells it (:func:`_version`).  It asks
    nothing of what a change moves.

    Raises:
        NotVersions: when it pins no version, is no file (:func:`_object`), holds bytes
            that decode to no text (:func:`_text`), or is a lock :func:`lock_pins` refuses.
        tools.ci.gitcmd.GitError: when git cannot read its object.
    """
    if not pins_versions(path):
        raise NotVersions(f"{path} pins no version")
    if held is None:
        return frozenset()
    text = _text(root, path, _object(path, held))
    if (rule := _rule(path)) is None:
        return lock_pins(text)
    return frozenset(Pin(pin["what"], _version(pin))
                     for pin in map(rule.line.fullmatch, text.split("\n")) if pin is not None)


@dataclass(frozen=True)
class Moved:
    """One thing whose versions a release moves in a file that pins versions: ``what`` (a
    package, an image, an action) in ``path``, at each version the release's base pins it at
    (``old``) and each its cut does (``new``), each sorted, empty where that side pins it at
    none."""

    path: str
    what: str
    old: tuple[str, ...]
    new: tuple[str, ...]

    def __str__(self) -> str:
        """How a listing names it, ``none`` for a side that pins it at none."""
        return (f"{self.path}: {self.what} {', '.join(self.old) or 'none'} -> "
                f"{', '.join(self.new) or 'none'}")


def _by_what(pins: Iterable[Pin]) -> dict[str, tuple[str, ...]]:
    """The versions ``pins`` holds, by what each pins, each sorted."""
    found: dict[str, list[str]] = {}
    for pin in pins:
        found.setdefault(pin.what, []).append(pin.version)
    return {what: tuple(sorted(versions)) for what, versions in found.items()}


def _differing(root: Path, path: str, was: str | None, now: str | None) -> tuple[Moved, ...]:
    """Each thing whose versions differ in ``path`` between ``was`` and ``now``, its mode and
    object at the base and at the cut (as :func:`differences` gives them), by what it is.

    Raises:
        NotVersions: when either side is no file, or a lock that is no TOML or holds a package
            with no name.
        tools.ci.gitcmd.GitError: when git cannot read either.
    """
    old, new = (_by_what(pinned(root, path, held)) for held in (was, now))
    return tuple(Moved(path, what, old.get(what, ()), new.get(what, ()))
                 for what in sorted(old.keys() | new.keys()) if old.get(what) != new.get(what))


def versions_moved(root: Path, changed: Mapping[str, tuple[str | None, str | None]],
                   ) -> tuple[Moved, ...]:
    """Each version a release moves, by path and then by what it is, ``changed`` holding each
    path of the files read (:func:`differences`) where the cut differs from the base, with its
    mode and object in each.

    Raises:
        NotVersions: as :func:`_differing` raises it.
        tools.ci.gitcmd.GitError: when git cannot read either side of a file.
    """
    return tuple(moved for path, (was, now) in changed.items() if pins_versions(path)
                 for moved in _differing(root, path, was, now))


def versions_waiting(root: Path) -> tuple[Moved, ...]:
    """Each version a release of ``dev`` into ``main`` would move, in every file of the tree
    that pins versions, as this clone's remote-tracking references hold the two
    (:func:`~tools.ci.gitcmd.tracking`; it fetches nothing): none when no release is due for a
    version (rulings plan#2829, plan#2876).

    Raises:
        NotVersions: as :func:`versions_moved` raises it.
        tools.ci.gitcmd.GitError: when git cannot read either branch.
    """
    return versions_moved(root, differences(tree(root, tracking(MAIN)),
                                            tree(root, tracking(DEV))))
