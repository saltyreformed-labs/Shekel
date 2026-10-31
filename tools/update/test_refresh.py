"""The Monday lock refresh (:mod:`tools.update.refresh`, ruling plan#2351): every package moved
but each move that needs a card, held back until the lock lands with none.

uv is faked (:func:`_uv`): it resolves from a table of releases, each package at its newest
release a hold allows, with the packages that release needs, and writes the lock as uv lays
it out.  The real uv was run once against the live index (2026-10-31): six packages moved,
pyee held below 14, the lock byte for byte the one ``uv lock --upgrade --upgrade-package
'pyee<14'`` writes by hand.

Each package that needs a card, prepared as a change of its own (ruling plan#2450), is tested
against a fuller fake (:func:`_resolver`): it reads the ``pyproject.toml`` it is given, keeps
the lock's version of each package it does not free while the version still fits, as uv
prefers it, and finds no solution as uv does, exit 1.  Its world (:data:`_SHEKEL`) is the
real one of 2026-10-31, where a lock with every range lifted moved pyee to 14.0.0, rumdl to
0.3.0 and setuptools to 84.2.1, and ``uv lock --upgrade-package pyee`` from ``dev``'s lock
moved nothing.
"""
from __future__ import annotations

import os
import re
import subprocess
import tomllib

import pytest

from tools.quill.door import Move
from tools.update.refresh import (
    Hold,
    RefreshError,
    Refreshed,
    prepared,
    refreshed,
    run,
    write_alone,
)
from tools.update.refresh import _uv as _real_uv

#: Where every file of a faked lock lies, as PyPI serves it.
_HOST = "https://files.pythonhosted.org/packages"
#: ``dev``'s lock before each refresh: each package's version and what it needs.
_BASE = {"pillow": ("12.3.0", ()), "qrcode": ("8.2", ("pillow",)), "zeta": ("0.6.3", ())}


def _lock(packages, revision=5):
    """A lock holding ``packages`` (each its version and what it needs), each from PyPI with
    a source distribution named for its release, its header of uv's ``revision``."""
    entries = []
    for name, (version, needs) in sorted(packages.items()):
        deps = "".join(f'    {{ name = "{need}" }},\n' for need in needs)
        entries.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                       'source = { registry = "https://pypi.org/simple" }\n'
                       + (f"dependencies = [\n{deps}]\n" if deps else "")
                       + f'sdist = {{ url = "{_HOST}/{name}-{version}.tar.gz", '
                       f'hash = "sha256:{"0" * 64}" }}\n')
    return (f'version = 1\nrevision = {revision}\nrequires-python = ">=3.14"\n\n'
            + "\n".join(entries))


def _number(version):
    """``version`` as a tuple of its numbers, which the fake compares."""
    return tuple(int(part) for part in version.split("."))


def _allows(version, spec):
    """Whether the hold ``spec`` (``<V``, ``==V`` or None) allows ``version``."""
    if spec is None:
        return True
    if spec.startswith("=="):
        return version == spec[2:]
    return _number(version) < _number(spec[1:])


def _uv(releases, keeps=True, revision=5):
    """A fake ``uv lock --upgrade`` over ``releases`` (each package's releases, oldest first,
    each with what it needs) and the record of each call's arguments: every package
    :data:`_BASE` holds, and each one a chosen release needs, at its newest release the
    holds allow (or ignore, unless ``keeps``)."""
    calls = []

    def lock(arguments):
        calls.append(tuple(arguments))
        holds = {}
        for requirement in arguments[1::2]:
            at = next(at for at, char in enumerate(requirement) if char in "<=")
            holds[requirement[:at]] = requirement[at:]
        chosen, todo = {}, sorted(_BASE)
        while todo:
            name = todo.pop()
            if name not in chosen:
                version, needs = [release for release in releases[name]
                                  if _allows(release[0], holds.get(name) if keeps else None)][-1]
                chosen[name] = (version, needs)
                todo += needs
        return _lock(chosen, revision)
    return lock, calls


#: The releases :data:`_BASE`'s packages have, each a minor or patch past it.
_MINORS = {"pillow": [("12.3.0", ()), ("12.4.1", ())],
           "qrcode": [("8.2", ("pillow",)), ("8.2.1", ("pillow",))],
           "zeta": [("0.6.3", ()), ("0.6.4", ())]}


def _moved(week):
    """What ``week`` moves, each package from and to."""
    return [(move.what, move.old, move.new) for move in week.moves]


def test_every_move_within_its_major_is_taken_with_no_hold():
    """Each package moves to its newest minor or patch, in one call with no hold."""
    lock, calls = _uv(_MINORS)
    week = refreshed(_lock(_BASE), lock)
    assert (_moved(week), dict(week.held), calls) == (
        [("pillow", "12.3.0", "12.4.1"), ("qrcode", "8.2", "8.2.1"), ("zeta", "0.6.3", "0.6.4")],
        {}, [()])


def test_a_major_is_held_below_its_next_major_and_takes_its_own_minors():
    """pillow's newest release is 13.0.0, a new major: it is held below 13, so it still
    moves to 12.4.1, and the lock is made again from ``dev``'s with that hold."""
    lock, calls = _uv(_MINORS | {"pillow": [*_MINORS["pillow"], ("13.0.0", ())]})
    week = refreshed(_lock(_BASE), lock)
    assert (_moved(week)[0], dict(week.held), calls) == (
        ("pillow", "12.3.0", "12.4.1"), {"pillow": Hold("<13", "13.0.0 is a new major")},
        [(), ("--upgrade-package", "pillow<13")])


def test_a_zero_x_minor_is_a_major_held_below_its_next_minor():
    """zeta is a 0.x package, so 0.7.0 is a new major: held below 0.7, it takes 0.6.4."""
    lock, _ = _uv(_MINORS | {"zeta": [*_MINORS["zeta"], ("0.7.0", ())]})
    week = refreshed(_lock(_BASE), lock)
    assert (_moved(week)[2], dict(week.held)) == (
        ("zeta", "0.6.3", "0.6.4"), {"zeta": Hold("<0.7", "0.7.0 is a new major")})


def test_a_move_adding_a_package_is_held_at_its_version():
    """qrcode 8.3 needs olefile, which the lock lacks: a new package needs a card, so qrcode
    is held at 8.2 and olefile never enters the lock."""
    releases = _MINORS | {"qrcode": [("8.2", ("pillow",)), ("8.3", ("pillow", "olefile"))],
                          "olefile": [("0.47", ())]}
    lock, calls = _uv(releases)
    week = refreshed(_lock(_BASE), lock)
    assert (_moved(week), dict(week.held), calls[1]) == (
        [("pillow", "12.3.0", "12.4.1"), ("zeta", "0.6.3", "0.6.4")],
        {"qrcode": Hold("==8.2", "its next release adds or removes a package")},
        ("--upgrade-package", "qrcode==8.2"))


def test_a_hold_at_a_version_wins_over_one_below_a_major():
    """pillow 13.0.0 is a new major, and 12.4.1 below it needs olefile: held below 13 first,
    then at 12.3.0, never loosened, so the third lock moves only zeta."""
    releases = _MINORS | {"pillow": [("12.3.0", ()), ("12.4.1", ("olefile",)), ("13.0.0", ())],
                          "olefile": [("0.47", ())]}
    lock, calls = _uv(releases)
    week = refreshed(_lock(_BASE), lock)
    assert (dict(week.held), calls, _moved(week)) == (
        {"pillow": Hold("==12.3.0", "its next release adds or removes a package")},
        [(), ("--upgrade-package", "pillow<13"), ("--upgrade-package", "pillow==12.3.0")],
        [("qrcode", "8.2", "8.2.1"), ("zeta", "0.6.3", "0.6.4")])


def test_a_hold_uv_does_not_keep_fails_the_refresh():
    """A lock still crossing a major once every hold is made fails, in the landing check's
    words."""
    lock, _ = _uv(_MINORS | {"pillow": [*_MINORS["pillow"], ("13.0.0", ())]}, keeps=False)
    with pytest.raises(RefreshError,
                       match="^uv.lock moves pillow from 12.3.0 to 13.0.0, a new major version$"):
        refreshed(_lock(_BASE), lock)


def test_a_change_no_hold_can_undo_fails_at_once():
    """Another uv writes another header, which no hold undoes: the first lock fails."""
    lock, calls = _uv(_MINORS, revision=6)
    with pytest.raises(RefreshError, match="^uv.lock changes its revision$"):
        refreshed(_lock(_BASE), lock)
    assert calls == [()]



def _project(*dependencies, dev=(), build=()):
    """A ``pyproject.toml`` whose project needs ``dependencies``, its ``dev`` group ``dev``
    and its ``build`` group ``build``, a comment among them as the real file has."""
    def listed(entries):
        return "".join(f'    "{entry}",\n' for entry in entries)
    return ('[project]\nname = "shekel"\nversion = "0"\nrequires-python = ">=3.14"\n'
            f"dependencies = [\n    # The runtime set.\n{listed(dependencies)}]\n\n"
            f"[dependency-groups]\ndev = [\n{listed(dev)}]\nbuild = [\n{listed(build)}]\n")


def _wants(text):
    """The package a requirement (or a release's need) ``text`` names, and its clauses, each
    an operator and a version, as the fake reads them."""
    name, rest = re.fullmatch(r"([a-z0-9-]+)(?:\[[^\]]*\])?(.*)", text).groups()
    return name, [re.fullmatch(r"(>=|<|==|!=)(.+)", clause).groups()
                  for clause in rest.split(",") if clause]


def _fits(version, clauses):
    """Whether ``version`` satisfies every clause of ``clauses``."""
    tests = {">=": lambda held: _number(version) >= _number(held),
             "<": lambda held: _number(version) < _number(held),
             "==": lambda held: version == held, "!=": lambda held: version != held}
    return all(tests[op](held) for op, held in clauses)


def _requires(project):
    """Each requirement ``project``, a ``pyproject.toml``'s text, lists."""
    table = tomllib.loads(project)
    return [*table["project"]["dependencies"],
            *(entry for group in table["dependency-groups"].values() for entry in group)]


def _locked(chosen, project, revision=5):
    """The lock uv writes holding ``chosen`` (each package's version and needs) for
    ``project``: its packages, then the project's own entry, which names its requirements as
    uv's does."""
    requires = "".join(f'    "{entry}",\n' for entry in _requires(project))
    return (_lock({name: (version, tuple(_wants(need)[0] for need in needs))
                   for name, (version, needs) in chosen.items()}, revision)
            + '\n[[package]]\nname = "shekel"\nversion = "0"\nsource = { virtual = "." }\n\n'
            f"[package.metadata]\nrequires-dist = [\n{requires}]\n")


def _refused(arguments, why):
    """uv's failure to find a solution for ``uv lock`` with ``arguments``, saying ``why``."""
    return subprocess.CalledProcessError(
        1, ["uv", "lock", *arguments],
        stderr=f"error: No solution found when resolving dependencies:\n  cause: {why}\n")


def _asked(project, arguments, releases):
    """What ``uv lock`` with ``arguments`` asks of the fake: each package's clauses so far,
    by name, from ``project``'s requirements and each bound after ``--upgrade-package``;
    each package the project needs, in order; and each package it frees."""
    clauses, needed = {}, []
    for entry in _requires(project):
        name, held = _wants(entry)
        clauses.setdefault(name, []).extend(held)
        needed.append(name)
    free = set(releases) if "--upgrade" in arguments else set()
    for requirement in (arguments[at + 1] for at, argument in enumerate(arguments)
                        if argument == "--upgrade-package"):
        name, held = _wants(requirement)
        free.add(name)
        clauses.setdefault(name, []).extend(held)
    return clauses, needed, free


def _resolver(releases, revision=5):
    """A fake ``uv lock`` over ``releases`` (each package's releases, oldest first, each with
    what it needs, a requirement each) and the record of each call's arguments: from the
    ``pyproject.toml`` and the lock it is given, each package the project needs, then each a
    chosen release needs, in turn, at the lock's version where that package is not freed
    (``--upgrade``, ``--upgrade-package P`` with any bound after it) and the version still
    fits, else at its newest release that fits; a package with none that fits, or a choice a
    later need refuses, is uv finding no solution (exit 1)."""
    calls = []

    def uv(project, start, arguments):
        calls.append(tuple(arguments))
        base = {package["name"]: package["version"]
                for package in tomllib.loads(start)["package"]}
        clauses, todo, free = _asked(project, arguments, releases)
        chosen = {}
        while todo:
            name = todo.pop(0)
            if name in chosen:
                continue
            fitting = [release for release in releases[name]
                       if _fits(release[0], clauses.get(name, []))]
            if not fitting:
                raise _refused(arguments, f"no {name} fits {clauses.get(name)}")
            chosen[name] = ([release for release in fitting
                             if name not in free and release[0] == base.get(name)]
                            or fitting)[-1]
            for need in chosen[name][1]:
                needed, held = _wants(need)
                clauses.setdefault(needed, []).extend(held)
                todo.append(needed)
        if late := [name for name, (version, _) in chosen.items()
                    if not _fits(version, clauses[name])]:
            raise _refused(arguments, f"{', '.join(late)} chosen before a need refused it")
        return _locked(chosen, project, revision)
    return uv, calls


def _checkout(uv):
    """A ``run`` uv handing each lock to ``uv`` after scribbling on the checkout's lock and
    ``pyproject.toml``, as uv is given them in place."""
    def make(root):
        def scribbled(project, start, arguments):
            for name in ("uv.lock", "pyproject.toml"):
                (root / name).write_text("scribbled\n", encoding="utf-8")
            return uv(project, start, arguments)
        return scribbled
    return make


#: ``dev``'s project for the run tests: qrcode and zeta held to their majors, pillow
#: through qrcode.
_PILLOW = _project("qrcode[pil]>=8.2,<9.0", "zeta>=0.6.3,<0.7.0")


def _checked_out(root, project, packages):
    """A checkout at ``root`` of ``project`` locked at ``packages``; its lock's text."""
    lock = _locked(packages, project)
    (root / "pyproject.toml").write_text(project, encoding="utf-8")
    (root / "uv.lock").write_text(lock, encoding="utf-8")
    return lock


def test_the_week_is_written_and_the_checkouts_lock_left_as_it_was(tmp_path):
    """``run`` writes the week's lock, title and text, holds included, and puts the
    checkout's lock and ``pyproject.toml`` back as they were."""
    lock = _checked_out(tmp_path, _PILLOW, _BASE)
    uv, _ = _resolver(_MINORS | {"pillow": [*_MINORS["pillow"], ("13.0.0", ())]})
    run(tmp_path, tmp_path / "out", _checkout(uv))
    written = tmp_path / "out" / "lock"
    assert ((tmp_path / "uv.lock").read_text(encoding="utf-8"),
            (tmp_path / "pyproject.toml").read_text(encoding="utf-8"),
            (written / "title.txt").read_text(encoding="utf-8"),
            (written / "uv.lock").read_text(encoding="utf-8")) == (
        lock, _PILLOW, "chore(deps): the Monday lock refresh moves 3 packages\n",
        _locked({"pillow": ("12.4.1", ()), "qrcode": ("8.2.1", ("pillow",)),
                 "zeta": ("0.6.4", ())}, _PILLOW))
    body = (written / "body.md").read_text(encoding="utf-8")
    assert "| pillow | 12.3.0 | 12.4.1 |" in body
    assert "- pillow, held to `<13`: 13.0.0 is a new major." in body


def test_a_week_moving_nothing_writes_nothing(tmp_path):
    """No package moves and none needs a card, so no pull request is prepared."""
    _checked_out(tmp_path, _PILLOW, _BASE)
    uv, _ = _resolver({name: [(version, needs)] for name, (version, needs) in _BASE.items()})
    monday = run(tmp_path, tmp_path / "out", _checkout(uv))
    assert (monday.week.moves, monday.alone) == ((), ())
    assert not (tmp_path / "out").exists()


def test_a_failed_refresh_leaves_the_checkouts_lock_as_it_was(tmp_path):
    """The checkout's lock and ``pyproject.toml`` are put back even when the refresh
    fails."""
    _checked_out(tmp_path, _PILLOW, _BASE)
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    uv, _ = _resolver(_MINORS, revision=6)
    with pytest.raises(RefreshError):
        run(tmp_path, tmp_path / "out", _checkout(uv))
    assert ((tmp_path / "uv.lock").read_text(encoding="utf-8"),
            (tmp_path / "pyproject.toml").read_text(encoding="utf-8")) == (lock, _PILLOW)


#: ``dev``'s project for the tests of each change prepared alone, as the real one stood on
#: 2026-10-31: playwright and rumdl held to their majors, rumdl's 0.2.79 excluded, and
#: setuptools pinned exactly.
_SHEKEL = _project(dev=("playwright>=1.63.0,<2.0.0", "rumdl>=0.2.78,<0.3.0,!=0.2.79"),
                   build=("setuptools==83.0.0",))
#: ``dev``'s lock for :data:`_SHEKEL`: playwright 1.63.0 caps pyee below 14.
_DEV = {"playwright": ("1.63.0", ("pyee<14",)), "pyee": ("13.0.1", ()),
        "rumdl": ("0.2.78", ()), "setuptools": ("83.0.0", ())}
#: Each release of :data:`_DEV`'s packages, as the index offered them past the cooldown on
#: 2026-10-31: playwright 1.64.1 admits pyee 14 but not 15.
_LATEST = {"playwright": [("1.63.0", ("pyee<14",)), ("1.64.1", ("pyee<15",))],
           "pyee": [("13.0.1", ()), ("14.0.0", ()), ("15.0.0", ())],
           "rumdl": [("0.2.78", ()), ("0.2.79", ()), ("0.3.0", ())],
           "setuptools": [("83.0.0", ()), ("84.2.1", ())]}


def _monday(project, packages, releases):
    """The week's lock and each change prepared alone for ``project`` locked at
    ``packages``, over ``releases``, and the record of uv's calls."""
    uv, calls = _resolver(releases)
    old = _locked(packages, project)
    week = refreshed(old, lambda arguments: uv(project, old, ("--upgrade", *arguments)))
    return week, {alone.move.what: alone for alone in prepared(project, week, uv)}, calls


def test_the_three_waiting_majors_are_each_prepared_alone():
    """As on 2026-10-31: the week moves playwright and holds pyee below 14, and three
    packages are prepared alone, pyee as the week held it, rumdl past its range and
    setuptools past its exact pin."""
    week, alone, _ = _monday(_SHEKEL, _DEV, _LATEST)
    assert (_moved(week), dict(week.held)) == (
        [("playwright", "1.63.0", "1.64.1")], {"pyee": Hold("<14", "14.0.0 is a new major")})
    assert {name: (each.move.old, each.move.new) for name, each in alone.items()} == {
        "pyee": ("13.0.1", "14.0.0"), "rumdl": ("0.2.78", "0.3.0"),
        "setuptools": ("83.0.0", "84.2.1")}


def test_a_held_indirect_major_is_prepared_on_top_of_the_weeks_lock():
    """From ``dev``'s lock pyee moves nowhere, as playwright 1.63.0 caps it below 14; on top
    of the week's lock, whose playwright 1.64.1 admits 14, it moves to 14.0.0 and nothing
    else, with no requirement to move, needing a card in the landing check's words."""
    _, alone, calls = _monday(_SHEKEL, _DEV, _LATEST)
    uv, _ = _resolver(_LATEST)
    old = _locked(_DEV, _SHEKEL)
    assert uv(_SHEKEL, old, ("--upgrade-package", "pyee")) == old
    pyee = alone["pyee"]
    assert (pyee.lock, pyee.pyproject, pyee.edits, pyee.why, pyee.change.moves) == (
        _locked(_DEV | {"playwright": ("1.64.1", ("pyee<15",)), "pyee": ("14.0.0", ())},
                _SHEKEL),
        None, {}, "uv.lock moves pyee from 13.0.1 to 14.0.0, a new major version",
        (Move("pyee", "13.0.1", "14.0.0", True),))
    assert ("--upgrade-package", "pyee") in calls


def test_a_capped_range_moves_to_the_new_major_keeping_its_exclusion():
    """rumdl's range holds it below 0.3.0: its requirement moves to ``>=0.3.0,<0.4`` in its
    own form, ``!=0.2.79`` kept, and the lock moves rumdl alone on top of the week's."""
    week, alone, calls = _monday(_SHEKEL, _DEV, _LATEST)
    rumdl = alone["rumdl"]
    moved = _SHEKEL.replace('"rumdl>=0.2.78,<0.3.0,!=0.2.79"', '"rumdl>=0.3.0,<0.4,!=0.2.79"')
    assert (rumdl.edits, rumdl.pyproject, rumdl.why, rumdl.change.moves) == (
        {"rumdl>=0.2.78,<0.3.0,!=0.2.79": "rumdl>=0.3.0,<0.4,!=0.2.79"}, moved,
        "0.3.0 is a new major, past pyproject.toml's range",
        (Move("rumdl", "0.2.78", "0.3.0", True),))
    assert rumdl.lock == _locked(
        _DEV | {"playwright": ("1.64.1", ("pyee<15",)), "rumdl": ("0.3.0", ())}, moved)
    assert week.text != rumdl.lock and ("--upgrade-package", "rumdl") in calls


def test_an_exact_pin_moves_to_the_newest_release_in_its_own_form():
    """setuptools is pinned at 83.0.0: the pin moves to ``==84.2.1``."""
    _, alone, _ = _monday(_SHEKEL, _DEV, _LATEST)
    setuptools = alone["setuptools"]
    assert (setuptools.edits, setuptools.why, setuptools.change.moves) == (
        {"setuptools==83.0.0": "setuptools==84.2.1"},
        "84.2.1 is a new major, past pyproject.toml's exact pin",
        (Move("setuptools", "83.0.0", "84.2.1", True),))


def test_a_newer_release_in_an_exact_pins_own_major_is_prepared_too():
    """An exact pin holds setuptools below 83.0.1, a patch: the week cannot take it, so it
    is prepared alone, its pin moved."""
    releases = _LATEST | {"setuptools": [("83.0.0", ()), ("83.0.1", ())]}
    week, alone, _ = _monday(_SHEKEL, _DEV, releases)
    setuptools = alone["setuptools"]
    assert ("setuptools" not in {move.what for move in week.moves},
            setuptools.edits, setuptools.why, setuptools.change.moves) == (
        True, {"setuptools==83.0.0": "setuptools==83.0.1"},
        "83.0.1 is past pyproject.toml's exact pin",
        (Move("setuptools", "83.0.0", "83.0.1", False),))


def test_a_range_admitting_the_newest_release_is_not_prepared():
    """A minor within rumdl's range is the week's, never a change of its own."""
    releases = _LATEST | {"rumdl": [("0.2.78", ()), ("0.2.80", ())],
                          "setuptools": [("83.0.0", ())]}
    week, alone, _ = _monday(_SHEKEL, _DEV, releases)
    assert ("rumdl", "0.2.78", "0.2.80") in _moved(week)
    assert set(alone) == {"pyee"}


#: A project whose flask caps werkzeug below 4 and whose flask 4 needs werkzeug 4: neither
#: moves to its new major alone.
_FLASK = _project("flask>=3.1.3,<4.0.0", "werkzeug>=3.1.9,<4.0.0")
#: ``dev``'s lock for :data:`_FLASK`, and each release the index offers.
_FLASK_DEV = {"flask": ("3.1.3", ("werkzeug<4",)), "werkzeug": ("3.1.9", ())}
_FLASK_LATEST = {"flask": [("3.1.3", ("werkzeug<4",)), ("4.0.0", ("werkzeug>=4",))],
                 "werkzeug": [("3.1.9", ()), ("4.0.0", ())]}


def test_a_move_uv_cannot_lock_alone_arrives_as_the_edit_and_uvs_words(tmp_path):
    """Lifted together, flask and werkzeug reach 4.0.0; alone, each is refused by the
    other's cap: each arrives as its requirement moved, no lock, and uv's refusal."""
    _checked_out(tmp_path, _FLASK, _FLASK_DEV)
    uv, _ = _resolver(_FLASK_LATEST)
    monday = run(tmp_path, tmp_path / "out", _checkout(uv))
    alone = {each.move.what: each for each in monday.alone}
    assert {name: (each.edits, each.lock, each.change) for name, each in alone.items()} == {
        "flask": ({"flask>=3.1.3,<4.0.0": "flask>=4.0.0,<5"}, None, None),
        "werkzeug": ({"werkzeug>=3.1.9,<4.0.0": "werkzeug>=4.0.0,<5"}, None, None)}
    assert alone["werkzeug"].refusal.startswith("error: No solution found")
    folder = tmp_path / "out" / "major" / "werkzeug"
    assert sorted(path.name for path in folder.iterdir()) == [
        "body.md", "pyproject.toml", "title.txt"]
    assert (folder / "pyproject.toml").read_text(encoding="utf-8") == _FLASK.replace(
        '"werkzeug>=3.1.9,<4.0.0"', '"werkzeug>=4.0.0,<5"')
    body = (folder / "body.md").read_text(encoding="utf-8")
    assert "uv cannot lock it alone" in body
    assert alone["werkzeug"].refusal.strip() in body


def test_any_other_uv_failure_fails_the_run(tmp_path):
    """uv failing on a moved requirement other than by finding no solution (exit 2: the
    index unreachable) fails the run, never reads as a refusal, and the checkout is put
    back."""
    lock = _checked_out(tmp_path, _SHEKEL, _DEV)
    uv, _ = _resolver(_LATEST)

    def unreachable(project, start, arguments):
        if arguments[:1] == ("--upgrade-package",) and project != _SHEKEL:
            raise subprocess.CalledProcessError(2, ["uv", "lock", *arguments])
        return uv(project, start, arguments)
    with pytest.raises(subprocess.CalledProcessError):
        run(tmp_path, tmp_path / "out", _checkout(unreachable))
    assert ((tmp_path / "uv.lock").read_text(encoding="utf-8"),
            (tmp_path / "pyproject.toml").read_text(encoding="utf-8")) == (lock, _SHEKEL)


def test_a_held_move_adding_a_package_is_prepared_with_it():
    """qrcode 8.3 needs olefile, so the week holds qrcode at 8.2; alone, it moves to 8.3
    and brings olefile, in the landing check's words."""
    releases = _MINORS | {"qrcode": [("8.2", ("pillow",)), ("8.3", ("pillow", "olefile"))],
                          "olefile": [("0.47", ())]}
    week, alone, _ = _monday(_PILLOW, _BASE, releases)
    qrcode = alone["qrcode"]
    assert (dict(week.held), qrcode.change.moves, qrcode.change.added, qrcode.why) == (
        {"qrcode": Hold("==8.2", "its next release adds or removes a package")},
        (Move("qrcode", "8.2", "8.3", False),), ("olefile",),
        "uv.lock adds olefile, a package of no version before")


def test_a_held_package_uv_moves_nowhere_alone_has_no_change_of_its_own():
    """d 2.0 needs p 2, and d 1.0 caps p below 2: the week holds both; alone, p moves
    nowhere, and d's own change carries p."""
    project = _project("r>=1.0,<2.0")
    releases = {"r": [("1.0", ("d",))], "d": [("1.0", ("p<2",)), ("2.0", ("p>=2",))],
                "p": [("1.0", ()), ("2.0", ())]}
    week, alone, _ = _monday(project, {"r": ("1.0", ("d",)), "d": ("1.0", ("p<2",)),
                                       "p": ("1.0", ())}, releases)
    assert (sorted(week.held), list(alone), alone["d"].change.moves) == (
        ["d", "p"], ["d"], (Move("d", "1.0", "2.0", True), Move("p", "1.0", "2.0", True)))


def test_each_change_prepared_alone_is_written_with_its_title_and_text(tmp_path):
    """rumdl's folder holds the moved ``pyproject.toml``, its lock, a title and a text
    naming the requirement moved and what the lock moves on top of the week's."""
    _checked_out(tmp_path, _SHEKEL, _DEV)
    uv, _ = _resolver(_LATEST)
    monday = run(tmp_path, tmp_path / "out", _checkout(uv))
    rumdl = next(each for each in monday.alone if each.move.what == "rumdl")
    folder = tmp_path / "out" / "major" / "rumdl"
    assert ((folder / "pyproject.toml").read_text(encoding="utf-8"),
            (folder / "uv.lock").read_text(encoding="utf-8"),
            (folder / "title.txt").read_text(encoding="utf-8")) == (
        rumdl.pyproject, rumdl.lock,
        "chore(deps): rumdl from 0.2.78 to 0.3.0, waiting for a card\n")
    body = (folder / "body.md").read_text(encoding="utf-8")
    for line in ("It needs a card: 0.3.0 is a new major, past pyproject.toml's range.",
                 "- `rumdl>=0.2.78,<0.3.0,!=0.2.79` to `rumdl>=0.3.0,<0.4,!=0.2.79`",
                 "| rumdl | 0.2.78 | 0.3.0 |",
                 "It is made on top of this week's lock refresh"):
        assert line in body
    assert sorted(path.name for path in (tmp_path / "out" / "major" / "pyee").iterdir()) == [
        "body.md", "title.txt", "uv.lock"]


@pytest.mark.parametrize(("releases", "edits", "new"), [
    ([("0.45.0", ()), ("0.46.0", ())], {}, "0.46.0"),
    ([("0.45.0", ()), ("0.46.0", ()), ("1.2.0", ())],
     {"wheel>=0.37,<1.0": "wheel>=1.2.0,<2"}, "1.2.0"),
], ids=["its range admits it", "its range holds it below"])
def test_a_requirement_moves_only_where_it_holds_its_package_below(releases, edits, new):
    """wheel's range ``<1.0`` admits 0.46.0, a new 0.x major the week holds back: it moves
    with no requirement edit; where 1.2.0 is out, past the range, the range moves to it."""
    project = _project(build=("wheel>=0.37,<1.0",))
    week, alone, _ = _monday(project, {"wheel": ("0.45.0", ())}, {"wheel": releases})
    assert (dict(week.held), list(alone), alone["wheel"].edits,
            alone["wheel"].change.moves) == (
        {"wheel": Hold("<0.46", "0.46.0 is a new major")}, ["wheel"], edits,
        (Move("wheel", "0.45.0", new, True),))


def test_a_held_package_whose_move_alone_needs_no_card_has_no_change_of_its_own():
    """The week held qrcode at 8.2 for a release that brings a package; alone, uv moves it
    to 8.2.1, which brings none: that needs no card, so it is no change waiting for one."""
    old = _locked(_BASE, _PILLOW)
    week = Refreshed(old, (), {"qrcode": Hold("==8.2", "its next release adds or removes "
                                              "a package")})
    minor = _locked(_BASE | {"qrcode": ("8.2.1", ("pillow",))}, _PILLOW)

    def uv(_project, start, arguments):
        return minor if arguments == ("--upgrade-package", "qrcode") else start
    assert not prepared(_PILLOW, week, uv)


def test_a_change_on_top_of_a_week_moving_nothing_carries_no_weeks_moves(tmp_path):
    """d's change is made on a week that moved nothing, so its text says nothing of the
    week's pull request."""
    project = _project("r>=1.0,<2.0")
    releases = {"r": [("1.0", ("d",))], "d": [("1.0", ()), ("2.0", ())]}
    week, alone, _ = _monday(project, {"r": ("1.0", ("d",)), "d": ("1.0", ())}, releases)
    write_alone(tmp_path, alone["d"], week)
    body = (tmp_path / "major" / "d" / "body.md").read_text(encoding="utf-8")
    assert (week.moves, "| d | 1.0 | 2.0 |" in body, "this week's lock refresh" in body) == (
        (), True, False)


def test_uv_runs_in_the_checkout_with_the_files_it_is_given(tmp_path, monkeypatch, capsys):
    """The real adapter writes the ``pyproject.toml`` and lock it is given into the
    checkout, runs ``uv lock`` there with the arguments, copies uv's words to standard
    error and reads the lock uv wrote; uv's failure is raised with its exit status and its
    words."""
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "uv").write_text(
        '#!/bin/sh\necho "$@" > arguments\n'
        'if grep -q refuse pyproject.toml; then echo "error: No solution found" >&2; exit 1; fi\n'
        'cat pyproject.toml uv.lock > made && mv made uv.lock\necho "Resolved" >&2\n',
        encoding="utf-8")
    (tools / "uv").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    lock = _real_uv(checkout)
    assert (lock("project\n", "start\n", ("--upgrade-package", "pyee")),
            (checkout / "arguments").read_text(encoding="utf-8")) == (
        "project\nstart\n", "lock --upgrade-package pyee\n")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        lock("refuse\n", "start\n", ("--upgrade",))
    assert (refused.value.returncode, refused.value.stderr) == (
        1, "error: No solution found\n")
    assert capsys.readouterr().err == "Resolved\nerror: No solution found\n"
