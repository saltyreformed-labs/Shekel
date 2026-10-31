"""The Monday lock refresh (:mod:`tools.update.refresh`, ruling plan#2351): every package moved
but each move that needs a card, held back until the lock lands with none.

uv is faked (:func:`_uv`): it resolves from a table of releases, each package at its newest
release a hold allows, with the packages that release needs, and writes the lock as uv lays
it out.  The real uv was run once against the live index (2026-10-31): six packages moved,
pyee held below 14, the lock byte for byte the one ``uv lock --upgrade --upgrade-package
'pyee<14'`` writes by hand.
"""
from __future__ import annotations

import pytest

from tools.update.refresh import Hold, RefreshError, refreshed, run

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


def _locker(lock):
    """A ``run`` locker handing back ``lock`` after scribbling on the checkout's lock, as
    uv writes it in place."""
    def make(root, _old):
        def scribbled(arguments):
            (root / "uv.lock").write_text("scribbled\n", encoding="utf-8")
            return lock(arguments)
        return scribbled
    return make


def test_the_week_is_written_and_the_checkouts_lock_left_as_it_was(tmp_path):
    """``run`` writes the week's lock, title and text, holds included, and puts the
    checkout's lock back as it was."""
    (tmp_path / "uv.lock").write_text(_lock(_BASE), encoding="utf-8")
    lock, _ = _uv(_MINORS | {"pillow": [*_MINORS["pillow"], ("13.0.0", ())]})
    run(tmp_path, tmp_path / "out", _locker(lock))
    written = tmp_path / "out" / "lock"
    assert ((tmp_path / "uv.lock").read_text(encoding="utf-8"),
            (written / "title.txt").read_text(encoding="utf-8"),
            (written / "uv.lock").read_text(encoding="utf-8")) == (
        _lock(_BASE), "chore(deps): the Monday lock refresh moves 3 packages\n",
        _lock({"pillow": ("12.4.1", ()), "qrcode": ("8.2.1", ("pillow",)),
               "zeta": ("0.6.4", ())}))
    body = (written / "body.md").read_text(encoding="utf-8")
    assert "| pillow | 12.3.0 | 12.4.1 |" in body
    assert "- pillow, held to `<13`: 13.0.0 is a new major." in body


def test_a_week_moving_nothing_writes_nothing(tmp_path):
    """No package moves, so no pull request is prepared."""
    (tmp_path / "uv.lock").write_text(_lock(_BASE), encoding="utf-8")
    lock, _ = _uv({name: [(version, needs)] for name, (version, needs) in _BASE.items()})
    assert not run(tmp_path, tmp_path / "out", _locker(lock)).moves
    assert not (tmp_path / "out").exists()


def test_a_failed_refresh_leaves_the_checkouts_lock_as_it_was(tmp_path):
    """The checkout's lock is put back even when the refresh fails."""
    (tmp_path / "uv.lock").write_text(_lock(_BASE), encoding="utf-8")
    lock, _ = _uv(_MINORS, revision=6)
    with pytest.raises(RefreshError):
        run(tmp_path, tmp_path / "out", _locker(lock))
    assert (tmp_path / "uv.lock").read_text(encoding="utf-8") == _lock(_BASE)
