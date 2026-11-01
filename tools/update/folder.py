"""The folder the Monday lock refresh writes each change it prepares to, and the publish reads
them from (rulings plan#2351, plan#2450): the one home of its layout, which both import.

The refresh (:mod:`tools.update.refresh`) runs package code and holds no secret; the publish
(:mod:`tools.update.publish`) holds the update App's token and runs no package code but the
``landing`` group's, installed from main's lock by hash.  So the folder crosses from a job
that ran package code to one holding a key, as the workflow's artifact
(``.github/workflows/updates.yml``), and is read as data: :func:`read` refuses it whole
(:class:`FolderError`) where it holds anything outside this layout, before anything is
published.

- ``summary.md``: the refresh's report, which its command writes once it has finished,
  whether or not anything moved (:func:`write_summary`), so a folder holding none is a
  refresh that never finished, never a week with nothing to publish;
- ``lock/``: the week's lock, ``uv.lock``, with ``title.txt`` and ``body.md``;
- ``major/<package>/``: each package prepared alone, ``pyproject.toml``, ``uv.lock`` or both,
  with ``title.txt`` and ``body.md``; the package named as PEP 503 names a project, since its
  folder's name becomes a branch's.

``title.txt`` holds one line.  Each change is published on the branch its folder names under
``update/`` (:attr:`Change.branch`): ``update/lock``, ``update/major/<package>``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tools.quill.door import LOCK
from tools.update.pyproject import PYPROJECT

#: The refresh's report, written whether or not anything moved.
SUMMARY = "summary.md"
#: Each change's pull request's title, one line, and its text.
TITLE, BODY = "title.txt", "body.md"
#: The week's lock's folder, and the folder holding each package prepared alone.
WEEK, ALONE = "lock", "major"
#: What every change's branch is named under.
PREFIX = "update/"
#: A project's name as PEP 503 normalizes it: runs of letters and digits, each joined by one
#: hyphen.
_PACKAGE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
#: The files a change of each kind may carry.
_CARRIED = {WEEK: frozenset({LOCK}), ALONE: frozenset({LOCK, PYPROJECT})}


class FolderError(ValueError):
    """The folder holds something outside its layout; the message says what."""


@dataclass(frozen=True)
class Change:
    """One change the refresh prepared: ``where`` its folder lies under the folder
    (``lock``, ``major/<package>``), the ``files`` it carries by name with their text, and its
    pull request's ``title`` (one line) and ``body``."""

    where: str
    files: Mapping[str, str]
    title: str
    body: str

    @property
    def branch(self) -> str:
        """The branch the change is published on."""
        return PREFIX + self.where


def alone(package: str) -> str:
    """Where the change preparing ``package`` alone lies under the folder."""
    return f"{ALONE}/{package}"


def ours(branch: str) -> bool:
    """Whether ``branch`` is one a change of this layout is published on."""
    where = branch.removeprefix(PREFIX)
    kind, _, package = where.partition("/")
    return branch.startswith(PREFIX) and (
        where == WEEK or (kind == ALONE and _PACKAGE.fullmatch(package) is not None))


def write(out: Path, change: Change) -> None:
    """``change`` written under ``out``, in a folder of its own that does not exist yet."""
    folder = out / change.where
    folder.mkdir(parents=True)
    for name, text in change.files.items():
        (folder / name).write_text(text, encoding="utf-8")
    (folder / TITLE).write_text(f"{change.title}\n", encoding="utf-8")
    (folder / BODY).write_text(change.body, encoding="utf-8")


def write_summary(out: Path, text: str) -> None:
    """The refresh's report ``text`` written under ``out``, made if it does not exist."""
    out.mkdir(parents=True, exist_ok=True)
    (out / SUMMARY).write_text(text, encoding="utf-8")


def _entries(folder: Path) -> dict[str, Path]:
    """Each entry of ``folder``, by name.

    Raises:
        FolderError: when ``folder`` is no folder, or a link to one, or holds a link.
    """
    if folder.is_symlink() or not folder.is_dir():
        raise FolderError(f"{folder} is no folder")
    entries = {path.name: path for path in folder.iterdir()}
    if links := sorted(name for name, path in entries.items() if path.is_symlink()):
        raise FolderError(f"{folder} holds a link: {', '.join(links)}")
    return entries


def _text(path: Path) -> str:
    """The text of the file at ``path``.

    Raises:
        FolderError: when it is no file, or holds no UTF-8.
    """
    if not path.is_file():
        raise FolderError(f"{path} is no file")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise FolderError(f"{path} holds no UTF-8 text: {error}") from error


def _change(out: Path, where: str, carried: frozenset[str]) -> Change:
    """The change in ``out / where``, which may carry the files ``carried``.

    Raises:
        FolderError: when it is no folder, lacks its title or text, carries no file or one
            it may not, or its title is not one line.
    """
    names = set(_entries(out / where))
    files = names - {TITLE, BODY}
    if not {TITLE, BODY} <= names or not files or not files <= carried:
        raise FolderError(f"{where} holds {', '.join(sorted(names)) or 'nothing'}: a change "
                          f"holds {TITLE}, {BODY} and one or more of "
                          f"{', '.join(sorted(carried))}")
    title = _text(out / where / TITLE)
    if not title.endswith("\n") or "\n" in title[:-1] or not title[:-1].strip():
        raise FolderError(f"{where}/{TITLE} is not one line")
    return Change(where, {name: _text(out / where / name) for name in sorted(files)},
                  title[:-1], _text(out / where / BODY))


def read(out: Path) -> tuple[Change, ...]:
    """Each change the folder ``out`` holds: the week's lock first, then each package alone
    by name.

    Raises:
        FolderError: when the folder holds anything outside the layout (the module
            docstring), or no :data:`SUMMARY`.
    """
    names = _entries(out)
    if SUMMARY not in names:
        raise FolderError(f"{out} holds no {SUMMARY}, so the refresh never finished")
    _text(names[SUMMARY])
    if unknown := sorted(names.keys() - {SUMMARY, WEEK, ALONE}):
        raise FolderError(f"{out} holds {', '.join(unknown)}, outside the layout")
    changes = [_change(out, WEEK, _CARRIED[WEEK])] if WEEK in names else []
    for package in sorted(_entries(out / ALONE) if ALONE in names else ()):
        if _PACKAGE.fullmatch(package) is None:
            raise FolderError(f"{alone(package)} names no project as PEP 503 normalizes one")
        changes.append(_change(out, alone(package), _CARRIED[ALONE]))
    return tuple(changes)
