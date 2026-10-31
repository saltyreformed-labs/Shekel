"""The requirements ``pyproject.toml`` holds, as the Monday refresh lifts and moves them (ruling
plan#2450): each entry of ``project.dependencies``, ``project.optional-dependencies`` and
``dependency-groups``, in the forms the file's own header holds them to: a range
(``uv add --bounds major``'s ``>=`` and ``<``), an exact pin (``==``) and an exclusion
(``!=``), with any extras and environment marker kept as written.

- **Lifted** (:func:`lifted`): each cap gone, each ``<`` dropped and each ``==`` made ``>=``,
  so a lock made from the lifted file holds every package at the newest release past every
  range.
- **Moved** (:func:`moved`) to a new release: a range to ``>=new,<next``, the next major by
  the landing check's one reading of a major (:func:`tools.quill.door.ceiling`), and an
  exact pin to ``==new``; each ``!=`` exclusion kept, so none is dropped unseen.
- **Written back** (:func:`rewritten`): only the edited entries' strings change; the rest of
  the file, its comments included, as it was.

A requirement in any other form (another operator, a wildcard, a direct URL) is one the
refresh cannot lift or move, and so is a cap held where it lifts none (``[tool.uv]``'s
constraint, override or dev dependencies): each is refused (:class:`RequirementError`), so
no range is left unlifted unseen.
"""
from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from tools.quill.door import canonical, ceiling

#: The file the project's requirements live in.
PYPROJECT = "pyproject.toml"
#: The ``[tool.uv]`` settings that hold requirements the refresh lifts none of.
_UNLIFTED = ("constraint-dependencies", "override-dependencies", "dev-dependencies")
#: A requirement the refresh reads: a name and its extras, its version clauses, and its
#: environment marker after ``;``.
_REQUIREMENT = re.compile(r"\s*(?P<head>(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
                          r"(?:\s*\[[^\]]*\])?)(?P<clauses>[^;]*)(?P<marker>;.*)?")
#: One version clause the refresh reads: its operator and its version.
_CLAUSE = re.compile(r"\s*(?P<op>>=|<|==|!=)\s*(?P<version>[0-9][0-9A-Za-z.!+-]*)\s*")
#: The operators that cap a package below a release: a range's upper bound and an exact pin.
_CAPS = ("<", "==")


class RequirementError(ValueError):
    """``pyproject.toml`` holds a requirement the refresh cannot lift or move; its message
    says which."""


@dataclass(frozen=True)
class Requirement:
    """One requirement ``pyproject.toml`` holds: ``text``, as the file spells it; ``name``,
    the package it names (PEP 503); ``head``, its name and extras as written; ``clauses``,
    each version clause, its operator and version, in order; and ``marker``, its environment
    marker with its ``;``, or nothing."""

    text: str
    name: str
    head: str
    clauses: tuple[tuple[str, str], ...]
    marker: str

    @property
    def capped(self) -> bool:
        """Whether it caps its package below a release: a range's upper bound or an exact
        pin."""
        return any(op in _CAPS for op, _ in self.clauses)

    @property
    def exact(self) -> bool:
        """Whether it pins its package exactly."""
        return any(op == "==" for op, _ in self.clauses)


def _strings(project: dict[str, object]) -> list[str]:
    """Each requirement the parsed ``project`` file lists, in its order: its dependencies,
    each extra's, then each dependency group's (a group's ``include-group`` table names no
    package).

    Raises:
        RequirementError: when ``[tool.uv]`` holds requirements the refresh lifts none of.
    """
    uv = project.get("tool", {}).get("uv", {})
    if unlifted := [key for key in _UNLIFTED if key in uv]:
        raise RequirementError(f"{PYPROJECT}'s [tool.uv] holds {', '.join(unlifted)}, whose "
                               "caps the refresh lifts none of")
    table = project.get("project", {})
    lists = [table.get("dependencies", []), *table.get("optional-dependencies", {}).values(),
             *project.get("dependency-groups", {}).values()]
    return [entry for entries in lists for entry in entries if isinstance(entry, str)]


def _requirement(text: str) -> Requirement:
    """The requirement ``text`` spells.

    Raises:
        RequirementError: when it is in a form the refresh cannot lift or move.
    """
    match = _REQUIREMENT.fullmatch(text)
    clauses = match["clauses"].strip() if match is not None else ""
    found = [_CLAUSE.fullmatch(clause) for clause in clauses.split(",")] if clauses else []
    if match is None or None in found:
        raise RequirementError(f"{PYPROJECT} holds {text!r}, a requirement in a form the "
                               "refresh cannot lift or move")
    return Requirement(text, canonical(match["name"]), match["head"].strip(),
                       tuple((clause["op"], clause["version"]) for clause in found),
                       match["marker"] or "")


def requirements(text: str) -> tuple[Requirement, ...]:
    """Each requirement the ``pyproject.toml`` whose text is ``text`` holds, in its order.

    Raises:
        RequirementError: when one is in a form the refresh cannot lift or move, or the file
            holds requirements it lifts none of.
    """
    return tuple(map(_requirement, _strings(tomllib.loads(text))))


def _spelled(requirement: Requirement, clauses: list[tuple[str, str]]) -> str:
    """``requirement`` with ``clauses`` in place of its own."""
    return (requirement.head + ",".join(op + version for op, version in clauses)
            + requirement.marker)


def lifted(requirement: Requirement) -> str:
    """``requirement`` with no cap: each ``<`` dropped and each ``==`` made ``>=``, every
    other clause kept."""
    return _spelled(requirement, [(">=" if op == "==" else op, version)
                                  for op, version in requirement.clauses if op != "<"])


def moved(requirement: Requirement, version: str) -> str:
    """``requirement``, a range or an exact pin, moved to ``version`` in its own form: an
    exact pin to ``==version``, a range to ``>=version,<next`` (the next major,
    :func:`ceiling`, or ``==version`` where none follows, as the refresh holds such a
    package); each ``!=`` exclusion kept after it."""
    kept = [(op, held) for op, held in requirement.clauses if op == "!="]
    if requirement.exact or (below := ceiling(version)) is None:
        return _spelled(requirement, [("==", version), *kept])
    return _spelled(requirement, [(">=", version), ("<", below), *kept])


def rewritten(text: str, edits: Mapping[str, str]) -> str:
    """The ``pyproject.toml`` whose text is ``text`` with each requirement ``edits`` names
    (its text as the file spells it) spelled as ``edits`` gives it, every other byte as it
    was.

    Raises:
        RequirementError: when an edited requirement's string appears in the file other than
            once per entry holding it (in a comment, or as a literal string), or the edit
            leaves the file holding other requirements than these.
    """
    before = [entry.text for entry in requirements(text)]
    held = Counter(before)
    for old in edits:
        if (spelled := text.count(f'"{old}"')) != held[old]:
            raise RequirementError(f'{PYPROJECT} spells "{old}" {spelled} times where it '
                                   f"lists it {held[old]}")
    edited = re.sub("|".join(re.escape(f'"{old}"') for old in edits),
                    lambda quoted: f'"{edits[quoted[0][1:-1]]}"', text) if edits else text
    if (now := [entry.text for entry in requirements(edited)]) != [
            edits.get(entry, entry) for entry in before]:
        raise RequirementError(f"{PYPROJECT} edited holds {now}, not the requirements edited")
    return edited
