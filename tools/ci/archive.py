"""The ARCHIVE arm: a historical document declares itself one, on its first line.

**This module exists because a session read an archived implementation plan as
the plan of record.**  The document had been superseded months earlier and said
nothing about it, so nothing in the act of opening it was a warning, and the
work that followed was planned against a document the developer had deliberately
retired.

**Keeping live documents from CITING an archived path does not fix that**, which
is why the predicate is not written that way.  A grep, a glob or a
half-remembered filename reaches an archived file without any live document's
help, and the archive is precisely where a filename that once meant something
still resolves -- so a rule policing live citations would have been GREEN
throughout the incident it was meant to prevent.  The banner goes on the
ARTIFACT instead, at the first line, where a reader arriving from anywhere
cannot get around it.

Moved here from the plan gate (``tools/plan_gate/_archive.py``) when step X-cx's
S3b deleted the gate: it never graded a registry, its subject is every markdown
file under an archived directory, its only shared input is the repository root
(:data:`tools.ci.arcs.REPO`), and CLAUDE.md still holds every archived document
to it.  ``test_archive.py`` grades it, and pre-commit runs that test whenever an
archived document is edited.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from tools.ci import arcs

#: Directory names whose contents are a historical record and govern nothing.
#: Three separate trees hold them -- ``docs/audits/**/archive``,
#: ``docs/historical`` and ``docs/plans/historical`` -- so the walk is rooted at
#: ``docs`` and keys on the directory NAME.  A walk rooted at any one arc's
#: archive would miss the other two, and the document that caused this rule was
#: reachable from none of them.
ARCHIVED_DIRS = frozenset({"archive", "historical"})

#: What every archived document must open with.
ARCHIVE_BANNER = "> **ARCHIVED."


def archived_docs() -> Iterator[Path]:
    """Every markdown file under an ``archive/`` or ``historical/`` directory."""
    for path in sorted((arcs.REPO / "docs").rglob("*.md")):
        if ARCHIVED_DIRS & set(path.relative_to(arcs.REPO).parts):
            yield path


def archive_banner_violations() -> list[str]:
    """An archived document SAYS SO on its first line.

    Returns:
        One message per archived document that does not carry the banner.
    """
    problems = []
    for path in archived_docs():
        first = path.read_text().lstrip().splitlines()[:1]
        if not first or not first[0].startswith(ARCHIVE_BANNER):
            problems.append(
                f"{path.relative_to(arcs.REPO)} is archived and its first "
                f"line does not say so.  It must open with {ARCHIVE_BANNER!r} "
                f"... so a reader who arrives by grep is told before they read "
                f"a word of it",
            )
    return problems
