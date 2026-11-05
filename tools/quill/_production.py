"""What production runs, as one commit's tree says it (rulings ``balance:R-BAL362``,
``R-BAL364``, ``R-BAL400`` and ``R-BAL401``): every file the image built from that commit
holds, and the files outside the image that build or run it.  CI's write rule refuses a
change to any of them that records no card (``tools/quill/landing.py``), so a release counts
every change production gets from the cards it brings, unless the change moves pinned
versions alone (ruling plan#2351, :mod:`tools.quill._versions`), which the release lists.

**The image holds what the ``Dockerfile``'s ``COPY . .`` copies of a clean checkout**
(``.github/workflows/docker-publish.yml`` builds it with the repository's root as its
context): every entry the commit's tree holds that the ignore file Docker reads leaves in the
context.  Docker reads ``Dockerfile.dockerignore`` beside the Dockerfile when there is one,
else ``.dockerignore`` at the root (:func:`ignore_file`; Docker's documentation, "Build
context", which gives the first precedence).  An entry is told by its mode and object
(:func:`files`), so an edit, a mode change, an add, a delete and an ignore-file edit that
moves an entry in or out of the image each change what it holds.

**Docker's rules, not git's** (:func:`rules`, :func:`kept`), ported from
``moby/patternmatcher`` v0.6.1 (``5a6d8429a``: ``ignorefile.ReadAll``, ``New``,
``Pattern.compile``, ``Pattern.match`` and ``MatchesUsingParentResults``), the source
Docker's documentation names for its matching, as BuildKit's walk of the context calls it
(``tonistiigi/fsutil``'s ``filter.go``, ``c1c5bffae``: each path judged with its directory's
results).  A pattern is anchored at the context's root, so ``*.md`` names the root's markdown
files and no others; ``*`` and ``?`` never cross a ``/``; ``**`` matches any number of
directories, none included; what a pattern says of a directory it says of everything under
it; and the last pattern that matches decides, a ``!`` line making an exception.  A file
Docker refuses builds no image, so production cannot change through it: this port refuses
the lone ``!``, a pattern ending in a lone ``\\``, and one Python's regular expressions cannot
compile (:class:`IgnoreFileError`), and reads any other as written.  **Where it reads
otherwise than Docker** (S3b's review, its finding 5; no line of this repository's ignore
file, nor any edit it plausibly takes): Go's ``filepath.Match``, which Docker asks first,
refuses a malformed class (``[a-]``, ``[]a]``), which this port reads as an expression; Go's
expressions read a POSIX class (``[[:alpha:]]``) and accept ``\\Q``, which Python reads as a
set and refuses; and ``New`` trims a pattern again after ``ReadAll`` has cleaned it, so
``foo/ /`` names ``foo`` to Docker and ``foo/ `` here.

**Outside the image** (:func:`runs_it`, ``R-BAL400``): the ``Dockerfile``, the image's
recipe; ``docker-compose.yml``, which production keeps a byte-for-byte copy of; every file
under ``deploy/`` but a ``README.md``: the deploy script production runs from the main
checkout, the compose override and the nginx settings it keeps copies of, the release's
post-deploy check, and the bundled nginx file the production override disables (measured
2026-10-24 by X-cx L8, read only on the host); ``scripts/_container_lib.sh``, the running
check the deploy script sources from that same checkout (``R-BAL254``: "the production
deploy script becomes two files from the same checkout instead of one"), the only file the
image excludes that production's deploy loads (S3b's review, its finding 1); and
(``R-BAL401``) the workflow that builds the image on ``main`` and signs it, read whole, its
comments included, as the others are, with each action of this repository's it loads by a
step's ``uses:`` and each that one loads in turn (plan#2482), and each file of the tree that
the workflow or such an action names outside a comment (ruling plan#2573), all read by
:func:`reached`: its tax-law gate, which decides whether the image is built, runs
``.github/actions/install-lock``, which loads ``.github/actions/dockerfile-uv``, and runs
``scripts/check_tax_law.py``, which the image excludes.  Which files those are is read from
each commit's own workflow, the one home of what it runs, so no list of them is kept here.
A file named only in a step's name or an ``echo`` counts too, as ruled.  **What the reading
does not see** includes a path built at run time (``scripts/${name}.py``), one written
another way (``python -m scripts.check_tax_law``), one relative to a step's
``working-directory:``, a directory named whose files run (``python tools/pkg``), a file a
tool reads with no name written, a file a named one loads in turn, and the rest of a line
after a ``#`` that follows whitespace inside quotes, which it reads as a comment.
``docker-compose.dev.yml``, ``docker-compose.build.yml``, ``cloudflared/config.yml`` (a
template the host's copy differs from) and the other workflows, which only test, are none
of production's, nor an action that only they load or a file that only they name.

**What production does not hold** (:func:`outside`, :func:`changed_outside`) is the rest of
each commit's tree, each side read at its own commit, so a file that crosses the line between
two commits is production's on one side and outside on the other.  The release tool lists the
versions moved there apart (``tools/release/_changes.py``, ruling plan#2876).
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from tools.ci.gitcmd import git

#: The image's recipe, which production's build reads from the context's root.
DOCKERFILE = "Dockerfile"
#: The compose file production keeps a byte-for-byte copy of (R-BAL400).
COMPOSE = "docker-compose.yml"
#: The workflow that builds the image on ``main`` and signs it (R-BAL401).
PUBLISH = ".github/workflows/docker-publish.yml"
#: The helper the deploy script sources from its own checkout, beside ``deploy/`` (R-BAL254).
DEPLOY_HELPER = "scripts/_container_lib.sh"
#: The files outside the image that build or run production, by path (R-BAL400, R-BAL401).
RECIPE = (DOCKERFILE, COMPOSE, PUBLISH, DEPLOY_HELPER)
#: The directory of the deploy script and the settings production keeps copies of.
DEPLOY = "deploy"
#: A file under :data:`DEPLOY` that production never reads, by its name.
README = "README.md"
#: The ignore file Docker reads, each in the order it looks: the Dockerfile's own first.
IGNORE_FILES = (f"{DOCKERFILE}.dockerignore", ".dockerignore")
#: A step's ``uses:`` naming an action this repository holds, by its directory from the root:
#: the ``$/`` form GitHub loads from the workflow's own commit (ruling plan#2375) or the
#: ``./`` form it reads from the checkout, quoted or not, a comment after it.
_LOCAL_USES = re.compile(r"""\s*(?:-\s+)?uses:\s*(["']?)[$.]/(?P<directory>[^\s"'#]+)\1"""
                         r"\s*(?:#.*)?")
#: The files GitHub reads a local action from, in its directory.
ACTION_FILES = ("action.yml", "action.yaml")
#: A word of a line that may name a file from the checkout's root: a run of the characters a
#: path is written in, from the first that is no ``/`` or ``-``.
_PATH_WORD = re.compile(r"[\w.][\w./-]*")
#: A shell variable by its bare name (``$GITHUB_WORKSPACE``), which is no part of a path a
#: line names: it ends the word before it, as the ``}`` of a braced one does.
_VARIABLE = re.compile(r"\$\w+")
#: Where a comment starts after a line's other text, as YAML and the shell both read it: a
#: ``#`` after whitespace.
_COMMENT_AFTER = re.compile(r"\s#")
#: What Go's ``strings.TrimSpace`` trims (``unicode.IsSpace``): Python's ``str.strip()``
#: trims U+001C-U+001F too.
_SPACE = "\t\n\v\f\r \x85\xa0 " + "".join(map(chr, range(0x2000, 0x200B))) + (
    "    　")
#: The characters ``Pattern.compile`` escapes: special in a regular expression, never in a
#: pattern.
_ESCAPED = ".+()|{}$"
#: The UTF-8 byte order mark ``ignorefile.ReadAll`` strips from the first line.
_BOM = "﻿"


class IgnoreFileError(ValueError):
    """An ignore file holds a pattern Docker refuses, so no image is built from it."""


@dataclass(frozen=True)
class Rule:
    """One pattern of an ignore file, as ``Pattern.compile`` reads it: ``exclusion``, whether
    it is a ``!`` line (an exception); ``pattern``, the cleaned pattern; ``kind``, how it
    matches (``exact``, ``prefix``, ``suffix`` or ``regexp``); and ``regexp``, the expression
    a ``regexp`` one matches the whole path with."""

    exclusion: bool
    pattern: str
    kind: str
    regexp: re.Pattern[str] | None

    def matches(self, path: str) -> bool:
        """Whether the pattern names ``path`` itself (``Pattern.match``)."""
        if self.kind == "exact":
            return path == self.pattern
        if self.kind == "prefix":
            return path.startswith(self.pattern[:-2])
        if self.kind == "suffix":
            suffix = self.pattern[2:]
            return path.endswith(suffix) or (suffix[:1] == "/" and path == suffix[1:])
        return self.regexp is not None and self.regexp.fullmatch(path) is not None


def _clean(path: str) -> str:
    """``path`` as Go's ``filepath.Clean`` writes it: one slash where several were, no ``.``
    element, an inner ``..`` taken with the element before it, ``/..`` at the start read as
    ``/``, and ``.`` for nothing left.  POSIX's ``normpath`` keeps exactly two leading
    slashes, which Go's does not."""
    cleaned = posixpath.normpath(path)
    return cleaned[1:] if cleaned.startswith("//") else cleaned


def _compiled(exclusion: bool, pattern: str) -> Rule:
    """The rule a cleaned ``pattern`` is (``Pattern.compile``): an exact path, a prefix (a
    trailing ``**`` after no wildcard), a suffix (a leading ``**`` before no other wildcard),
    or a regular expression.

    Raises:
        IgnoreFileError: when the pattern ends in a lone ``\\``, or its expression does not
            compile.
    """
    expression, kind, at, step = "", "exact", 0, 0
    while at < len(pattern):
        char, at = pattern[at], at + 1
        if char == "*" and pattern[at:at + 1] == "*":
            at += 2 if pattern[at + 1:at + 2] == "/" else 1
            if at < len(pattern):
                expression, kind = expression + "(.*/)?", "regexp"
            elif kind == "exact":
                kind = "prefix"
            else:
                expression, kind = expression + ".*", "regexp"
            kind = "suffix" if step == 0 else kind
        elif char in "*?":
            expression, kind = expression + ("[^/]*" if char == "*" else "[^/]"), "regexp"
        elif char in _ESCAPED:
            expression += f"\\{char}"
        elif char == "\\" and at < len(pattern):
            expression, kind, at = expression + f"\\{pattern[at]}", "regexp", at + 1
        elif char == "\\":
            raise IgnoreFileError(f"the pattern {pattern!r} ends in a lone backslash, which "
                                  "Docker refuses")
        else:
            expression += char
            kind = "regexp" if char in "[]" else kind
        step += 1
    if kind != "regexp":
        return Rule(exclusion, pattern, kind, None)
    try:
        return Rule(exclusion, pattern, kind, re.compile(expression))
    except re.error as error:
        raise IgnoreFileError(
            f"the pattern {pattern!r} is not one Docker reads: {error}") from error


def rules(text: str) -> tuple[Rule, ...]:
    """The rules of an ignore file whose text is ``text``, in its order
    (``ignorefile.ReadAll``, then ``New``): a line starting ``#`` is a comment, whitespace
    around a pattern and after a ``!`` is dropped, a blank line is skipped, and each pattern
    is cleaned (:func:`_clean`) and loses a leading ``/``, so the context's root is the
    working and the root directory both; ``New`` then cleans it again, ``!`` included.

    Raises:
        IgnoreFileError: for a ``!`` naming no pattern, or a pattern :func:`_compiled`
            refuses.
    """
    found = []
    for number, line in enumerate(text.split("\n")):
        line = line.removeprefix(_BOM) if number == 0 else line
        if line.startswith("#") or not (pattern := line.strip(_SPACE)):
            continue
        invert = pattern.startswith("!")
        pattern = pattern[1:].strip(_SPACE) if invert else pattern
        if pattern:
            pattern = _clean(pattern)
            pattern = pattern[1:] if len(pattern) > 1 and pattern.startswith("/") else pattern
        pattern = _clean(f"!{pattern}" if invert else pattern)
        if pattern == "!":
            raise IgnoreFileError('a line "!" names no pattern, which Docker refuses')
        exclusion = pattern.startswith("!")
        found.append(_compiled(exclusion, pattern[1:] if exclusion else pattern))
    return tuple(found)


def _matched(found: tuple[Rule, ...], path: str,
             parent: tuple[bool, ...]) -> tuple[bool, tuple[bool, ...]]:
    """Whether the rules ``found`` leave ``path`` out of the context, and which of them name
    it, given which named its directory (``parent``, empty at the root):
    ``MatchesUsingParentResults``.  A rule that named the directory names ``path``; the last
    rule naming it decides, an exception keeping it in."""
    matched, named = False, []
    for at, rule in enumerate(found):
        match = bool(parent) and parent[at]
        if not match:
            if rule.exclusion != matched:
                named.append(False)
                continue  # an exception only undoes a match, and a pattern only makes one
            match = rule.matches(path)
        named.append(match)
        if match:
            matched = not rule.exclusion
    return matched, tuple(named)


def kept(found: tuple[Rule, ...], paths: Iterable[str]) -> set[str]:
    """The paths of ``paths``, each a file's from the context's root, that the rules
    ``found`` leave in the context, each judged with its directory's results, as BuildKit's
    walk judges it (:func:`_matched`), every directory's read once."""
    named: dict[str, tuple[bool, ...]] = {"": ()}

    def of(directory: str) -> tuple[bool, ...]:
        """Which rules name ``directory``, read once."""
        if directory not in named:
            named[directory] = _matched(found, directory, of(posixpath.dirname(directory)))[1]
        return named[directory]

    return {path for path in paths
            if not _matched(found, path, of(posixpath.dirname(path)))[0]}


def ignore_file(root: Path, commit: str) -> str:
    """The text of the ignore file Docker reads for an image built from ``commit``
    (:data:`IGNORE_FILES`, the first the commit's tree holds as a file), empty when it holds
    neither.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read ``commit``.
    """
    for name in IGNORE_FILES:
        if git(root, "ls-tree", "--format=%(objecttype)", commit, "--", name).strip() == "blob":
            return git(root, "cat-file", "blob", f"{commit}:{name}")
    return ""


def runs_it(path: str) -> bool:
    """Whether ``path`` is, by its path alone, a file outside the image that builds or runs
    production (``R-BAL400``, ``R-BAL401``): the image's recipe, the compose file production
    copies, the workflow that builds and signs the image, the helper the deploy script sources
    (``R-BAL254``), or a file under :data:`DEPLOY` but a :data:`README`.  The actions that
    workflow loads, and the files it names, are its commit's to say (:func:`reached`)."""
    return path in RECIPE or (path.startswith(f"{DEPLOY}/")
                              and posixpath.basename(path) != README)


def _named(line: str) -> set[str]:
    """Each path ``line`` names outside a comment (ruling plan#2573): none on a line whose
    first character but whitespace is ``#``; on another, each :data:`_PATH_WORD` before
    :data:`_COMMENT_AFTER`, each :data:`_VARIABLE` read as a space, its trailing dots dropped
    and written as ``posixpath.normpath`` writes it (``./a``, ``a//b`` and ``a/./b`` each read
    ``a/b``).  A character no path is written in ends a word, so a path after ``~/``, a
    variable or a leading ``/`` is read from the checkout's root, which can count a file the
    step never reads."""
    if line.lstrip().startswith("#"):
        return set()
    text = _VARIABLE.sub(" ", _COMMENT_AFTER.split(line, maxsplit=1)[0])
    return {posixpath.normpath(word.rstrip(".")) for word in _PATH_WORD.findall(text)}


def reached(root: Path, held: Mapping[str, str], path: str) -> set[str]:
    """Each file of ``held`` (a tree's files, :func:`tree`) that the file ``path`` of it
    reaches: each action of this repository's it loads by a step's ``uses:``, by the file of
    :data:`ACTION_FILES` ``held`` holds it in, and each that one loads in turn; and each file
    ``path`` or such an action names outside a comment (:func:`_named`; ruling plan#2573),
    which is not read in turn.  None when ``held`` holds no ``path``.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read a file it reads.
    """
    actions: set[str] = set()
    named: set[str] = set()
    reading = [path] if path in held else []
    while reading:
        for line in git(root, "cat-file", "blob", held[reading.pop()].split(" ")[1]).split("\n"):
            named |= {name for name in _named(line) if name in held}
            if (match := _LOCAL_USES.fullmatch(line)) is None:
                continue
            for name in ACTION_FILES:
                action = posixpath.join(posixpath.normpath(match["directory"]), name)
                if action in held and action not in actions:
                    actions.add(action)
                    reading.append(action)
    return actions | named


def files(root: Path, commit: str) -> dict[str, str]:
    """Every file of production at ``commit``, by its path, each its mode and object: those
    the image built from it holds (:func:`kept` by :func:`ignore_file`'s rules), and those
    outside the image that build or run it (:func:`runs_it`, and each file the publish
    workflow reaches, :func:`reached`).

    Raises:
        IgnoreFileError: as :func:`rules` raises it.
        tools.ci.gitcmd.GitError: when git cannot read ``commit``, or a file it reads.
    """
    found = rules(ignore_file(root, commit))
    held = tree(root, commit)
    image = kept(found, held)
    publishing = reached(root, held, PUBLISH)
    return {path: what for path, what in held.items()
            if path in image or path in publishing or runs_it(path)}


def tree(root: Path, commit: str) -> dict[str, str]:
    """Every file ``commit``'s tree holds, by its path, each its mode and object.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read ``commit``.
    """
    listed = git(root, "ls-tree", "-r", "-z", "--full-tree",
                 "--format=%(objectmode) %(objectname)%x09%(path)", commit)
    return dict(reversed(record.split("\t", 1)) for record in listed.split("\0") if record)


def differences(before: Mapping[str, str],
                after: Mapping[str, str]) -> dict[str, tuple[str | None, str | None]]:
    """Each path, in order, where two sets of files (each a path's mode and object, as
    :func:`tree` gives them) differ: in one and not the other, or a different mode or
    object; with its mode and object in each, None in the one that holds no such file."""
    return {path: (before.get(path), after.get(path))
            for path in sorted(before.keys() | after.keys())
            if before.get(path) != after.get(path)}


def changed(root: Path, old: str, new: str) -> dict[str, tuple[str | None, str | None]]:
    """Each path, in order, where production built from ``new`` differs from production
    built from ``old`` (:func:`files`, :func:`differences`).

    Raises:
        IgnoreFileError: as :func:`rules` raises it, for either.
        tools.ci.gitcmd.GitError: when git cannot read either.
    """
    return differences(files(root, old), files(root, new))


def outside(root: Path, commit: str) -> dict[str, str]:
    """Every file ``commit``'s tree holds that is not production's at ``commit`` (:func:`tree`,
    :func:`files`), by its path, each its mode and object.

    Raises:
        IgnoreFileError: as :func:`rules` raises it.
        tools.ci.gitcmd.GitError: when git cannot read ``commit``, or a file it reads.
    """
    production = files(root, commit)
    return {path: what for path, what in tree(root, commit).items() if path not in production}


def changed_outside(root: Path, old: str, new: str,
                    ) -> dict[str, tuple[str | None, str | None]]:
    """Each path, in order, where the files outside production differ between ``old`` and
    ``new`` (:func:`outside`, :func:`differences`), as :func:`changed` gives those inside it.  A
    file that crosses the line is in both, each against None on the side it does not hold it.

    Raises:
        IgnoreFileError: as :func:`rules` raises it, for either.
        tools.ci.gitcmd.GitError: when git cannot read either.
    """
    return differences(outside(root, old), outside(root, new))
