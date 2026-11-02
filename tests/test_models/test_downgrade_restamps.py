"""A refusing downgrade re-stamps to its OWN parent revision (finding plan#1902).

Three migrations refuse an automatic downgrade and print the SQL an operator
runs by hand instead, ending with a re-stamp of the revision below them
(``UPDATE alembic_version SET version_num = '...'`` or ``flask db stamp ...``).
That revision is the module's ``down_revision``, and until finding plan#1902
each message spelled it as a literal: ``b6d41f0a9c27``'s named
``f2a94c7e1b60``, the parent it had until a 2026-08-15 merge re-pointed it onto
``b7c3d9e1f204``, so an operator following it landed one revision low and the
next ``upgrade`` re-ran ``b7c3d9e1f204`` and failed re-adding
``journal_entries.transaction_entry_id``.

Each message now prints ``down_revision`` itself.  This module grades that
property over every migration whose source names a re-stamp, found by a scan
of ``migrations/versions`` rather than listed, so a migration added later with
a literal of its own is graded the day it lands.  The refusal is FORCED for
each one -- the message is code only a raised error runs.

**What the scan covers, stated because a narrower one claimed more.** It first
matched two spellings, ``version_num = '`` and ``db stamp ``, beside the
sentence above; in a migration other than the three the census case names, a
double-quoted literal, an ``alembic stamp`` or an ``INSERT INTO
alembic_version`` went unseen (raised by the adversarial review of plan step
R8-b, measured 2026-11-02 for task plan#2627).  It now reads every STRING
LITERAL in a migration's source that is not a docstring, and takes the
migration when one names the word ``stamp`` or the table ``alembic_version``.
Every instruction an operator could follow to re-stamp names one of the two --
a command the word, SQL the table -- and it is printed from a string wherever
the message is built: the ``raise`` itself, a module constant, an f-string.
The assertion reads every revision id the forced message names rather than
parsing the instruction, so no spelling matters to it either.

Docstrings and comments are not read, because they print nothing: two
downgrades (``c5d6e7f8a901``, ``c9f4b1e78d02``) mention a stamp in their
docstrings, and a scan of the downgrade's whole source took both.
"""
import ast
import pathlib
import re

import pytest

from tests._test_helpers import load_migration_module

#: Where Alembic's revisions live.
_VERSIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

#: What every re-stamp instruction names, whatever its spelling: the command's
#: word (``flask db stamp``, ``alembic stamp``) or the table SQL writes
#: (``UPDATE`` / ``INSERT INTO alembic_version``).  ``\b`` keeps ``timestamp``
#: out; case is ignored, so ``Stamp the prior revision`` counts.
_NAMES_A_RESTAMP = re.compile(r"\bstamp\b|alembic_version", re.IGNORECASE)

#: An Alembic revision id: 12 lowercase hex digits.
_REVISION_ID = re.compile(r"\b[0-9a-f]{12}\b")


def _docstrings(tree: ast.Module) -> set[int]:
    """Return the ``id`` of every docstring node in *tree*.

    Args:
        tree: A parsed migration module.

    Returns:
        The ids of the string constants that open the module, a class or a
        function, which Python keeps as documentation and never prints.
    """
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }


def _prints_a_restamp(source: str) -> bool:
    """Return whether a string the migration can print names a re-stamp.

    Args:
        source: The migration module's source.

    Returns:
        ``True`` when a string literal outside every docstring -- a whole
        string, or a literal part of an f-string -- names a re-stamp.
    """
    # Most of the tree names neither word anywhere, so most files are never
    # parsed: 29 of 222 pass this test (measured 2026-11-02).
    if not _NAMES_A_RESTAMP.search(source):
        return False
    tree = ast.parse(source)
    docstrings = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and _NAMES_A_RESTAMP.search(node.value)
        for node in ast.walk(tree)
    )


def _migrations_printing_a_restamp() -> list[str]:
    """Return the filename of every migration whose source prints a re-stamp.

    Returns:
        The filenames, sorted, for ``pytest.mark.parametrize``.
    """
    return sorted(
        path.name
        for path in _VERSIONS_DIR.glob("*.py")
        if _prints_a_restamp(path.read_text(encoding="utf-8"))
    )


_PRINTING = _migrations_printing_a_restamp()


def test_the_census_finds_the_three_known_refusals():
    """The scan finds every migration this finding measured, so it measures something.

    A scan that matched nothing would parametrize the case below over an empty
    list and pass with no assertion run; naming the three it must find is what
    makes a green run mean the property was graded.
    """
    revisions = {name.split("_", 1)[0] for name in _PRINTING}
    assert {"a3f7c8e21b64", "a80c3447c153", "b6d41f0a9c27"} <= revisions


@pytest.mark.parametrize("filename", _PRINTING)
def test_a_refusing_downgrade_restamps_to_its_own_parent(filename):
    """The only revision a refusal names, besides its own, is ``down_revision``.

    Forces the refusal and reads every revision id its message names.  Less
    its own (a message may say which migration is refusing), what is left
    must be exactly the module's parent: the one revision an operator may be
    told to stamp.  For ``b6d41f0a9c27`` that is ``b7c3d9e1f204``, where the
    literal it replaced said ``f2a94c7e1b60``.

    Two shapes fail here rather than pass.  A message naming a third revision,
    which may be a reason to restate the message rather than a defect.  And a
    migration whose downgrade does not refuse before it touches a database --
    ``NotImplementedError`` with nothing connected -- so its re-stamp is one
    this module cannot force: the remedy is to refuse first.
    """
    module = load_migration_module(filename)
    with pytest.raises(NotImplementedError) as refusal:
        module.downgrade()
    named = set(_REVISION_ID.findall(str(refusal.value))) - {module.revision}
    assert named == {module.down_revision}, (
        f"{filename} names {sorted(named)} besides itself, but its parent "
        f"revision is {module.down_revision}: {refusal.value}"
    )
