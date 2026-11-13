"""The lock's set the daily image scan holds Grype's venv packages to (:mod:`tools.update.scan`;
rulings plan#3788, plan#3877, plan#3884): the groups the Dockerfile's ``uv sync`` chooses, what
``uv tree`` is told of the image, its JSON read as data, and the refusal of a scan whose catalog
of the venv is not that set.  The tree read is a trimmed copy of a real one,
``recorded/tree.json``: seven of the 46 entries uv 0.13.7 wrote 2026-11-13 for the lock at
production's commit, filtered to its platform, each whole (the three packages of the venv the
recorded catalog lists, the extra and the project that name one, the project's group and the
workspace), its directory, the scan's own, renamed.  The catalog is the recorded one
(``recorded/catalog.json``, read in ``test_scan_seen.py``)."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.ci.arcs import REPO
from tools.update import scan

#: The trimmed copy of a real tree, and of a real catalog.
TREE = Path(__file__).resolve().parent / "recorded" / "tree.json"
CATALOGUED = TREE.with_name("catalog.json")
#: The venv of production's image, as its python reports it (measured 2026-11-13).
_VENV = "/opt/venv"
#: The lock's set the recorded tree lists: the three packages of the venv the recorded catalog
#: lists, at their versions.
_LOCKED = frozenset({("alembic", "1.20.0"), ("gunicorn", "26.2.0"), ("psycopg-c", "3.3.6")})
#: The image's python as production's reports it (measured 2026-11-13).
_RUNS = scan.Interpreter("3.14.8", "/usr/local/bin/python3.14", _VENV, "cpython", "linux",
                         "x86_64", "glibc")


# -- the groups the image installs ----------------------------------------------------------

def test_the_dockerfile_installs_the_server_group_and_no_default_one():
    """The real Dockerfile's one ``uv sync``, its two continued lines read as one, chooses the
    groups so; its flags that change only how it installs are dropped."""
    assert scan.chosen((REPO / scan.DOCKERFILE).read_text(encoding="utf-8")) == (
        "--no-default-groups", "--group", "server")


def test_a_uv_sync_among_other_commands_and_comments_is_read_whole():
    """Docker drops a comment line and a blank one inside an instruction, and the shell parts
    its commands at ``&&`` and ``;``: the sync's flags are its own, the group flags in order."""
    dockerfile = ("FROM python:3.14-slim AS builder\n"
                  "# RUN uv sync --group commented\n"
                  "RUN echo first && uv sync --locked --group one \\\n"
                  "    # a comment inside the instruction\n"
                  "\n"
                  "    --no-default-groups --group 'two';echo --group three\n"
                  "CMD [\"uv\", \"sync\", \"--group\", \"exec\"]\n")
    assert scan.chosen(dockerfile) == ("--group", "one", "--no-default-groups", "--group", "two")


@pytest.mark.parametrize(("dockerfile", "said"), [
    ("FROM python\n# RUN uv sync --group server\n", "runs uv sync 0 times"),
    ("FROM python\nRUN uv sync --group a\nRUN uv sync --group b\n", "runs uv sync 2 times"),
    ("FROM python\nRUN uv sync --group a && uv sync --group b\n", "runs uv sync 2 times"),
    ("FROM python\nRUN uv sync --locked --all-groups\n", "with '--all-groups', a flag whose"),
    ("FROM python\nRUN uv sync --frozen --group a\n", "with '--frozen', a flag whose"),
    ("FROM python\nRUN uv sync --group=a\n", "with '--group=a', a flag whose"),
    ("FROM python\nRUN uv sync --no-default-groups --group\n", "names is None, which cannot"),
    ("FROM python\nRUN uv sync --group --locked\n", "names is '--locked', which cannot"),
    ("FROM python\nRUN uv sync --group 'a`b'\n", "names is 'a`b', which cannot"),
    ("FROM python\nRUN uv sync --group 'server\n", "which the shell cannot part"),
], ids=["no_sync", "two_instructions", "two_commands", "a_flag_of_its_own", "another_flag",
        "a_group_joined_to_its_flag", "a_group_flag_last", "a_group_flag_naming_a_flag",
        "a_group_unlistable", "a_quote_unclosed"])
def test_a_dockerfile_whose_uv_sync_the_scan_cannot_read_is_refused(dockerfile, said):
    """The scan reads the groups of one ``uv sync`` it knows every flag of, or refuses: a flag
    whose effect on what it installs it does not know could add a group or take one away."""
    with pytest.raises(scan.NotAScan, match=said):
        scan.chosen(dockerfile)


# -- what uv is told of the image -----------------------------------------------------------

def test_uv_is_told_cpython_on_linux_with_glibc_at_the_image_s_machine_and_version():
    """What the image's python reports becomes uv's ``--python``, ``--python-platform`` and
    ``--python-version``."""
    assert scan.told(_RUNS) == ("--python", "cpython", "--python-platform",
                                "x86_64-unknown-linux-gnu", "--python-version", "3.14.8")
    assert scan.told(replace(_RUNS, machine="aarch64", version="3.15.0"))[3:] == (
        "aarch64-unknown-linux-gnu", "--python-version", "3.15.0")


@pytest.mark.parametrize(("change", "said"), [
    ({"implementation": "pypy"}, "is pypy, where the scan has uv work the lock's set out for "
                                 "cpython alone"),
    ({"libc": ""}, "runs on linux with the C library '', where the scan can tell uv linux with "
                   "glibc alone"),
    ({"platform": "darwin", "libc": ""}, "runs on darwin with the C library ''"),
], ids=["another_implementation", "no_glibc", "another_system"])
def test_an_image_uv_cannot_be_told_of_is_refused_never_worked_out_as_another(change, said):
    """uv reads the lock's implementation conditions off the interpreter it runs on, which the
    scan names CPython, and its platform off the platform it is told: an image the scan cannot
    tell it of is refused (ruling plan#3877)."""
    with pytest.raises(scan.NotAScan, match=said):
        scan.told(replace(_RUNS, **change))


# -- uv's tree, read as data ----------------------------------------------------------------

def _tree() -> dict:
    """The recorded tree, read afresh, so a test may change it."""
    return json.loads(TREE.read_text(encoding="utf-8"))


def _entry(tree: dict, name: str) -> dict:
    """``tree``'s one entry of the kind package named ``name``."""
    found, = (entry for entry in tree["resolution"].values()
              if entry.get("name") == name and entry.get("kind") == "package")
    return found


def test_the_recorded_tree_lists_each_package_but_the_virtual_project():
    """Each entry of the kind package, by its name and version: the project ``shekel``, a
    virtual package, left out, and the extra, the group and the workspace naming none."""
    assert scan.locked(TREE.read_bytes()) == _LOCKED


@pytest.mark.parametrize(("change", "said"), [
    (lambda tree: tree.update(schema={"version": "1"}), "is of the schema '1', where"),
    (lambda tree: tree.pop("schema"), "answer holds no schema"),
    (lambda tree: tree.update(resolution=[]), "answer holds no resolution"),
    (lambda tree: tree["resolution"].update(x=[]), "entry 'x' is of the kind None"),
    (lambda tree: _entry(tree, "gunicorn").update(kind="dependency"), "of the kind 'dependency'"),
    (lambda tree: _entry(tree, "gunicorn").update(kind={"marker": "x"}), "of the kind {'marker'"),
    (lambda tree: _entry(tree, "gunicorn").update(kind={"extra": "a", "group": "b"}),
     "of the kind {'extra'"),
    (lambda tree: _entry(tree, "gunicorn").update(kind={"extra": 1}), "of the kind {'extra': 1}"),
    (lambda tree: _entry(tree, "gunicorn").pop("source"), "holds no source"),
    (lambda tree: _entry(tree, "gunicorn").update(name="gunicorn <!-- -->"),
     "name is 'gunicorn <!-- -->', which cannot"),
    (lambda tree: _entry(tree, "gunicorn").pop("version"), "version is None, which cannot"),
], ids=["another_schema", "no_schema", "no_resolution", "an_entry_no_object", "another_kind",
        "a_named_kind_of_its_own", "two_named_kinds", "a_kind_naming_no_string", "no_source",
        "a_name_unlistable", "no_version"])
def test_a_tree_not_as_uv_writes_one_is_refused(change, said):
    """uv's JSON is its preview shape, which a later uv may change: a tree of another shape is
    refused whole, never read as a set holding less."""
    tree = _tree()
    change(tree)
    with pytest.raises(scan.NotAScan, match=said):
        scan.locked(json.dumps(tree).encode())


def test_a_tree_that_is_no_json_is_refused():
    """What uv answered is judged as data."""
    with pytest.raises(scan.NotAScan, match="uv tree's answer: it is no JSON"):
        scan.locked(b"warning: no tree")


# -- the venv held to the lock's set --------------------------------------------------------

def _catalog() -> dict:
    """The recorded catalog, read afresh, so a test may change it."""
    return json.loads(CATALOGUED.read_text(encoding="utf-8"))


def _component(catalog: dict, name: str) -> dict:
    """``catalog``'s one component named ``name``."""
    found, = (component for component in catalog["components"] if component["name"] == name)
    return found


def _added(catalog: dict, name: str, version: str, path: str) -> None:
    """``catalog`` listing too a Python package ``name`` at ``version``, found at the file
    ``path``: a copy of the recorded alembic's component, as Grype writes one."""
    added = copy.deepcopy(_component(catalog, "alembic"))
    added.update(name=name, version=version)
    added["properties"] = [
        {"name": item["name"], "value": path} if item["name"].endswith(":path") else item
        for item in added["properties"]]
    catalog["components"].append(added)


def _held(catalog: dict, lock: frozenset[tuple[str, str]] = _LOCKED) -> None:
    """``catalog`` held to ``lock`` in the venv of production's image."""
    scan.held(scan.catalogued(json.dumps(catalog).encode()), _VENV, lock)


def test_the_recorded_catalog_holds_the_lock_s_set_in_the_venv():
    """Each package of the set is one the catalog lists in the venv at its version, and the
    catalog lists none there the set does not hold."""
    _held(_catalog())


def _refusal(*said: str) -> str:
    """The refusal's message, with the parts ``said`` that name the packages."""
    return (f"Grype's catalog of the venv {_VENV} is not the lock's set for the image, so the "
            f"scan did not see every locked package at its locked version: {'; '.join(said)}")


@pytest.mark.parametrize(("change", "said"), [
    (lambda catalog: catalog["components"].remove(_component(catalog, "alembic")),
     ["missing from it, as the lock holds them: alembic 1.20.0"]),
    (lambda catalog: _component(catalog, "gunicorn").update(version="26.2.1"),
     ["missing from it, as the lock holds them: gunicorn 26.2.0",
      "in it, but not in the lock's set: gunicorn 26.2.1"]),
    (lambda catalog: _added(catalog, "pip", "25.3",
                            f"{_VENV}/lib/python3.14/site-packages/pip-25.3.dist-info/METADATA"),
     ["in it, but not in the lock's set: pip 25.3"]),
    (lambda catalog: [catalog["components"].remove(_component(catalog, name))
                      for name in ("alembic", "psycopg-c")],
     ["missing from it, as the lock holds them: alembic 1.20.0, psycopg-c 3.3.6"]),
], ids=["a_locked_package_missing", "at_another_version", "one_the_lock_does_not_hold",
        "each_named"])
def test_a_venv_that_is_not_the_lock_s_set_is_refused_naming_each_package(change, said):
    """Ruling plan#3788's case: a locked package missing, at another version, or one the lock
    does not hold, each refused, naming it; a package at another version is named on both
    sides."""
    catalog = _catalog()
    change(catalog)
    with pytest.raises(scan.NotAScan) as refused:
        _held(catalog)
    assert str(refused.value) == _refusal(*said)


@pytest.mark.parametrize("path", [
    "/usr/local/lib/python3.14/site-packages/pip-25.3.dist-info/METADATA",
    "/opt/venv2/lib/python3.14/site-packages/pip-25.3.dist-info/METADATA",
    "/opt/venv",
], ids=["the_base_image_s", "a_venv_named_like_it", "the_venv_itself"])
def test_a_python_package_found_outside_the_venv_is_not_read(path):
    """The base image's own Python packages are none the lock installs: a package found
    outside the venv, at a file of a directory whose name starts like the venv's too, or at the
    venv's own path, is held to nothing."""
    catalog = _catalog()
    _added(catalog, "pip", "25.3", path)
    _held(catalog)


def test_a_package_s_name_is_held_to_the_lock_s_as_pep_503_normalizes_it():
    """A package is one name however either tool spells it: a name the catalog or the tree
    spells otherwise than normalized is held as PEP 503 normalizes it."""
    catalog = _catalog()
    _component(catalog, "psycopg-c").update(name="Psycopg_C")
    _held(catalog)
    tree = _tree()
    _entry(tree, "psycopg-c").update(name="psycopg.c")
    assert scan.locked(json.dumps(tree).encode()) == _LOCKED


@pytest.mark.parametrize(("change", "said"), [
    (lambda catalog: _component(catalog, "gunicorn").update(name="gunicorn <!-- -->"),
     "the name of a python package the catalog lists in /opt/venv is 'gunicorn <!-- -->', "
     "which cannot"),
    (lambda catalog: _component(catalog, "gunicorn").pop("version"),
     "the version of the python package 'gunicorn' the catalog lists in /opt/venv is '', "
     "which cannot"),
], ids=["a_name_unlistable", "no_version"])
def test_a_package_of_the_venv_that_cannot_be_listed_is_refused(change, said):
    """A refusal names the venv's packages, so each is read as data first."""
    catalog = _catalog()
    change(catalog)
    with pytest.raises(scan.NotAScan, match=said):
        _held(catalog)
