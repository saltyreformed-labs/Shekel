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
property over every migration whose source prints a re-stamp, found by a scan
of ``migrations/versions`` rather than listed, so a migration added later with
a literal of its own is graded the day it lands.  The refusal is FORCED for
each one -- the message is code only a raised error runs.
"""
import pathlib
import re

import pytest

from tests._test_helpers import load_migration_module

#: Where Alembic's revisions live.
_VERSIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

#: A re-stamp instruction in either spelling the tree uses, capturing the
#: revision it names.  Alembic revision ids are 12 lowercase hex digits.
_RESTAMP = re.compile(
    r"(?:version_num\s*=\s*'|db stamp\s+)([0-9a-f]{12})\b"
)

#: The source text that marks a migration as one that prints a re-stamp.
#: Matched on the SOURCE, before formatting, so a literal and an
#: ``{down_revision}`` interpolation are both found.
_PRINTS_A_RESTAMP = re.compile(r"version_num\s*=\s*'|db stamp\s")


def _migrations_printing_a_restamp() -> list[str]:
    """Return the filename of every migration whose source prints a re-stamp.

    Returns:
        The filenames, sorted, for ``pytest.mark.parametrize``.
    """
    return sorted(
        path.name
        for path in _VERSIONS_DIR.glob("*.py")
        if _PRINTS_A_RESTAMP.search(path.read_text(encoding="utf-8"))
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
    """The revision a refusal tells the operator to stamp is ``down_revision``.

    Forces the refusal, reads every revision id the message names in a
    re-stamp instruction, and requires each to be the module's own parent.
    For ``b6d41f0a9c27`` that is ``b7c3d9e1f204``, where the literal it
    replaced said ``f2a94c7e1b60``.
    """
    module = load_migration_module(filename)
    with pytest.raises(NotImplementedError) as refusal:
        module.downgrade()
    named = _RESTAMP.findall(str(refusal.value))
    assert named, (
        f"{filename}'s source prints a re-stamp but its refusal names no "
        f"revision in one: {refusal.value}"
    )
    assert set(named) == {module.down_revision}, (
        f"{filename} re-stamps to {named}, but its parent revision is "
        f"{module.down_revision}"
    )
