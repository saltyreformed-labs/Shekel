"""The requirements ``pyproject.toml`` holds, as the Monday refresh lifts and moves them
(:mod:`tools.update.pyproject`, ruling plan#2450).
"""
from __future__ import annotations

import pytest

from tools.update.pyproject import (
    RequirementError,
    lifted,
    moved,
    requirements,
    rewritten,
)

#: A ``pyproject.toml`` in the real file's shapes: a range with extras, an extra's range, a
#: range with an exclusion, an exact pin, a requirement with no range, a group's include,
#: and comments.
_FILE = """[project]
name = "shekel"
version = "0"
dependencies = [
    # The runtime set.
    "psycopg[c]>=3.3.6,<4.0.0",
    "Flask_SQLAlchemy>=3.1.1,<4.0.0",
]

[project.optional-dependencies]
docs = ["sphinx>=8.0,<9.0"]

[dependency-groups]
dev = [
    "rumdl>=0.2.78,<0.3.0,!=0.2.79",
]
build = [
    "setuptools==83.0.0",
]
landing = [
    "requests",
    { include-group = "build" },
]
"""


def _one(text):
    """The one requirement of a file listing ``text`` alone."""
    (requirement,) = requirements(f'[project]\ndependencies = ["{text}"]\n')
    return requirement


def test_each_requirement_is_read_in_the_files_order_by_its_packages_name():
    """Each entry of the dependencies, every extra and every group, in order, its package
    named as PEP 503 compares names, its extras kept in its head; a group's include names
    none."""
    assert [(entry.name, entry.head, entry.clauses, entry.capped, entry.exact)
            for entry in requirements(_FILE)] == [
        ("psycopg", "psycopg[c]", ((">=", "3.3.6"), ("<", "4.0.0")), True, False),
        ("flask-sqlalchemy", "Flask_SQLAlchemy", ((">=", "3.1.1"), ("<", "4.0.0")), True,
         False),
        ("sphinx", "sphinx", ((">=", "8.0"), ("<", "9.0")), True, False),
        ("rumdl", "rumdl", ((">=", "0.2.78"), ("<", "0.3.0"), ("!=", "0.2.79")), True, False),
        ("setuptools", "setuptools", (("==", "83.0.0"),), True, True),
        ("requests", "requests", (), False, False)]


def test_a_requirements_marker_is_kept_as_written():
    """An environment marker rides along every spelling."""
    entry = _one("tomli>=2.0,<3.0; python_version < '3.11'")
    assert (entry.marker, lifted(entry), moved(entry, "3.1")) == (
        "; python_version < '3.11'", "tomli>=2.0; python_version < '3.11'",
        "tomli>=3.1,<4; python_version < '3.11'")


@pytest.mark.parametrize(("text", "free"), [
    ("psycopg[c]>=3.3.6,<4.0.0", "psycopg[c]>=3.3.6"),
    ("rumdl>=0.2.78,<0.3.0,!=0.2.79", "rumdl>=0.2.78,!=0.2.79"),
    ("setuptools==83.0.0", "setuptools>=83.0.0"),
    ("requests", "requests"),
])
def test_a_lifted_requirement_has_no_cap_and_keeps_every_other_clause(text, free):
    """Each ``<`` goes and each ``==`` becomes ``>=``; ``>=`` and ``!=`` stay."""
    assert lifted(_one(text)) == free


@pytest.mark.parametrize(("text", "version", "now"), [
    ("psycopg[c]>=3.3.6,<4.0.0", "4.1.0", "psycopg[c]>=4.1.0,<5"),
    ("rumdl>=0.2.78,<0.3.0,!=0.2.79", "0.3.0", "rumdl>=0.3.0,<0.4,!=0.2.79"),
    ("setuptools==83.0.0", "84.2.1", "setuptools==84.2.1"),
    ("calver>=2025.1,<2026", "2026.5", "calver>=2026.5,<2027"),
    ("zero>=0.0.0,<0.0.1", "0", "zero==0"),
])
def test_a_moved_requirement_keeps_its_own_form(text, version, now):
    """A range moves to ``>=new,<next`` by the one reading of a major (a 0.x minor and a
    calendar year included), an exact pin to ``==new``, each exclusion kept; a release of
    zeros, which no next major follows, is pinned as the refresh holds one."""
    assert moved(_one(text), version) == now


@pytest.mark.parametrize("text", [
    "foo~=1.2", "foo==1.*", "foo>1", "foo<=2", "foo===1", "foo @ https://example.com/f.whl",
    "foo (>=1)",
])
def test_a_requirement_in_a_form_the_refresh_cannot_move_is_refused(text):
    """Any operator but ``>=``, ``<``, ``==`` and ``!=``, a wildcard, a direct URL, a
    parenthesised list."""
    with pytest.raises(RequirementError, match="a requirement in a form the refresh cannot"):
        _one(text)


@pytest.mark.parametrize("key", ["constraint-dependencies", "override-dependencies",
                                 "dev-dependencies"])
def test_a_cap_held_where_the_refresh_lifts_none_is_refused(key):
    """``[tool.uv]``'s own requirements could cap a package the lifted lock then never sees
    past."""
    with pytest.raises(RequirementError, match=f"holds {key}, whose caps"):
        requirements(f'[project]\ndependencies = []\n\n[tool.uv]\n{key} = ["foo<2"]\n')


def test_a_rewrite_changes_only_the_edited_strings():
    """Every other byte of the file, its comments included, is as it was."""
    assert rewritten(_FILE, {"rumdl>=0.2.78,<0.3.0,!=0.2.79": "rumdl>=0.3.0,<0.4,!=0.2.79",
                             "setuptools==83.0.0": "setuptools==84.2.1"}) == _FILE.replace(
        '"rumdl>=0.2.78,<0.3.0,!=0.2.79"', '"rumdl>=0.3.0,<0.4,!=0.2.79"').replace(
        '"setuptools==83.0.0"', '"setuptools==84.2.1"')


def test_a_rewrite_reads_every_edit_against_the_file_as_it_was():
    """Two edits whose texts trade places are each made once, never one atop the other."""
    file = '[project]\ndependencies = ["a>=1,<2"]\n\n[dependency-groups]\ndev = ["a>=2,<3"]\n'
    assert rewritten(file, {"a>=1,<2": "a>=2,<3", "a>=2,<3": "a>=1,<2"}) == (
        '[project]\ndependencies = ["a>=2,<3"]\n\n[dependency-groups]\ndev = ["a>=1,<2"]\n')


def test_a_requirement_listed_twice_is_moved_in_both_places():
    """The same string in two groups is one requirement spelled twice."""
    file = '[project]\ndependencies = ["a>=1,<2"]\n\n[dependency-groups]\ndev = ["a>=1,<2"]\n'
    assert rewritten(file, {"a>=1,<2": "a>=2,<3"}) == file.replace("a>=1,<2", "a>=2,<3")


@pytest.mark.parametrize(("file", "why"), [
    ('[project]\n# once "a>=1,<2"\ndependencies = ["a>=1,<2"]\n', "spells"),
    ("[project]\ndependencies = ['a>=1,<2']\n", "spells"),
    ('[project]\n# once "a>=1,<2"\ndependencies = [\'a>=1,<2\']\n', "edited holds"),
], ids=["also in a comment", "a literal string", "a comment standing in for a literal"])
def test_an_edit_whose_string_the_file_spells_otherwise_is_refused(file, why):
    """A requirement's string also in a comment, or one written as a literal string, is not
    edited blind; nor is one whose only spelling as a basic string is a comment, which the
    count alone would take for the entry."""
    with pytest.raises(RequirementError, match=why):
        rewritten(file, {"a>=1,<2": "a>=2,<3"})
