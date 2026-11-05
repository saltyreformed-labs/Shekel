"""The versions a change moves in the files production runs (:mod:`tools.quill._versions`,
ruling plan#2351): what a change that moves pinned versions alone is, and what a major is.

Each file's two sides are blobs written to a throwaway repository (:mod:`tools.ci.scratch`),
judged as CI's landing check judges a path where production differs
(:func:`_production.changed`'s mode and object); the landing check's own use of them is
``test_landing_versions.py``'s.  What an action's repository tags is the ``tags`` fixture's
(``conftest.py``), but where the read of GitHub itself is graded: there ``git ls-remote``
reads real repositories in a directory standing in for GitHub (:func:`_github`).
"""
from __future__ import annotations

import hashlib
import re

import pytest

from tools.ci.gitcmd import GitError, git
from tools.ci.scratch import commit, commit_files, repository
from tools.ci.scratch import run as _scratch
from tools.quill import _versions
from tools.quill._production import COMPOSE, DOCKERFILE, PUBLISH
from tools.quill._versions import LOCK, Move, NotVersions, Pin, Reason, moves, pinned
from tools.quill._versions import pins_versions
from tools.quill._versions import ceiling, lock_change, lock_moves, lock_unversioned, unversioned
from tools.quill._versions import unversioned_between

#: PyPI's simple index, every locked package's registry.
_REGISTRY = '{ registry = "https://pypi.org/simple" }'
#: The lock's packages before each change: by name, its version and the packages it needs.
_PACKAGES = {"pillow": ("12.3.0", ()), "qrcode": ("8.2", ("pillow",)),
             "zeta": ("0.6.3", ())}
#: Two digests, an image's before and after a move.
_OLD, _NEW = "a" * 64, "b" * 64
#: Two commits, an action's before and after a move.
_SHA, _LATER = "1" * 40, "2" * 40
#: A Dockerfile pinning uv and Python, each by tag and digest, as the repository's does.
_DOCKERFILE = (f"# the image\nFROM ghcr.io/astral-sh/uv:0.13.6@sha256:{_OLD} AS uv\n\n"
               f"FROM python:3.14-slim@sha256:{_OLD} AS builder\nRUN uv sync --locked\n\n"
               f"FROM python:3.14-slim@sha256:{_OLD}\nCOPY . .\n")
#: A compose file pinning postgres by tag and digest, as the repository's does.
_COMPOSE = (f"services:\n  db:\n    # Pinned by tag and digest.\n"
            f"    image: postgres:18-alpine@sha256:{_OLD}\n    container_name: db\n")
#: The publish workflow pinning two actions by commit, each its release beside it.
_PUBLISH = (f"jobs:\n  build:\n    steps:\n      - name: Check out\n"
            f"        uses: actions/checkout@{_SHA} # v7.0.1\n"
            f"      - uses: docker/login-action@{_SHA} # v4.6.0\n")


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """A throwaway repository the blobs are written to."""
    return repository(tmp_path)


def _blob(repo, text, mode="100644"):
    """``text`` written to ``repo`` as a blob, as production's mode and object name it."""
    return f"{mode} {git(repo, 'hash-object', '-w', '--stdin', stdin=text).strip()}"


def _moved(repo, path, old, new):
    """What :func:`moves` says of ``path`` changed from the text ``old`` to ``new``."""
    return moves(repo, path, _blob(repo, old), _blob(repo, new))


def _refusal(repo, path, old, new):
    """Why :func:`moves` refuses ``path`` changed from the text ``old`` to ``new``."""
    with pytest.raises(NotVersions) as refused:
        _moved(repo, path, old, new)
    return str(refused.value)


def _files(name, version, files):
    """The lines naming package ``name`` at ``version``'s files, its ``files`` there: a
    source distribution, and that many wheels, each named as a registry names that
    release's (one platform tag each) and hashed by its name."""
    def hashed(file):
        return f'hash = "sha256:{hashlib.sha256(file.encode()).hexdigest()}"'
    host = "https://files.pythonhosted.org/packages"
    wheels = "".join(f'    {{ url = "{host}/{name}-{version}-py3-none-platform{n}.whl", '
                     f'{hashed(f"{name}-{version}-{n}")} }},\n' for n in range(files))
    return (f'sdist = {{ url = "{host}/{name}-{version}.tar.gz", '
            f'{hashed(f"{name}-{version}")} }}\nwheels = [\n{wheels}]\n')


def _lock(packages=None, *, header="", files=None, extra=""):
    """A lock holding ``packages`` (:data:`_PACKAGES` when None), each from PyPI with one
    wheel, or ``files`` of them by name, then the project's own, which needs qrcode; its
    header ``header`` more, and ``extra`` after it all."""
    entries = []
    for name, (version, needs) in (_PACKAGES if packages is None else packages).items():
        deps = "".join(f'    {{ name = "{need}" }},\n' for need in needs)
        entries.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                       f'source = {_REGISTRY}\n' + (f"dependencies = [\n{deps}]\n" if deps else "")
                       + _files(name, version, (files or {}).get(name, 1)))
    entries.append('[[package]]\nname = "shekel"\nversion = "0"\nsource = { virtual = "." }\n'
                   'dependencies = [\n    { name = "qrcode" },\n]\n\n[package.metadata]\n'
                   'requires-dist = [{ name = "qrcode", specifier = ">=8.2,<9.0" }]\n')
    return (f'version = 1\nrevision = 5\nrequires-python = ">=3.14"\n{header}\n'
            f'[options]\nexclude-newer-span = "P7D"\n\n' + "\n".join(entries) + extra)


def _at(name, version, needs=None):
    """:data:`_PACKAGES` with package ``name`` at ``version``, needing ``needs`` (its own when
    None)."""
    return _PACKAGES | {name: (version, _PACKAGES[name][1] if needs is None else needs)}


def test_a_minor_move_of_an_indirect_package_is_one_move_within_its_major(repo):
    """``uv lock --upgrade`` moves pillow, which only qrcode needs, within its major: one
    move, by name, its version, files and hashes all moved, and nothing else."""
    assert _moved(repo, LOCK, _lock(), _lock(_at("pillow", "12.4.1"))) == (
        Move("pillow", "12.3.0", "12.4.1", False),)


@pytest.mark.parametrize(("old", "new", "major"), [
    ("12.3.0", "13.0.0", True), ("13.0.0", "12.3.0", True), ("12.3.0", "12.3.1", False),
    ("0.6.3", "0.7.0", True), ("0.6.3", "0.6.4", False), ("0.0.3", "0.0.4", True),
    ("2026.5", "2027.1", True), ("2026.5", "2026.10.23", False), ("1", "1.0.2", False),
    ("1.0", "1!1.0", True), ("3.20", "3.20.1.post1", False),
], ids=["a major", "a major back", "a patch", "a 0.x minor", "a 0.x patch", "a 0.0.x patch",
        "a calendar year", "within a calendar year", "trailing zeros", "an epoch",
        "a post release"])
def test_a_major_is_the_release_up_to_its_first_number_not_zero(repo, old, new, major):
    """The major ``pyproject.toml``'s ranges hold each direct package to: the release up to
    its first number that is not zero, the epoch before it, so a 0.x minor is a major and a
    calendar version crosses one each year; every package is judged, indirect ones too."""
    assert _moved(repo, LOCK, _lock(_at("zeta", old)), _lock(_at("zeta", new))) == (
        Move("zeta", old, new, major),)


def test_new_files_for_an_unmoved_version_move_nothing(repo):
    """A wheel uploaded later for pillow's same version changes its files and hashes alone:
    no version moves, and nothing is refused."""
    assert not _moved(repo, LOCK, _lock(), _lock(files={"pillow": 2}))


#: The lock with qrcode moved to 8.3, needing what 8.2 needed.
_QRCODE = _lock(_at("qrcode", "8.3"))
#: qrcode's one need, as the lock writes it.
_PILLOW = '{ name = "pillow" }'


@pytest.mark.parametrize("new", [
    _lock(_at("qrcode", "8.3", ("pillow", "zeta"))),
    _lock(_at("qrcode", "8.3", ())),
    _QRCODE.replace(_PILLOW, '{ name = "pillow", marker = "sys_platform == \'linux\'" }'),
    _QRCODE.replace(_PILLOW, '{ name = "pillow", extra = ["fpx"] }'),
    _QRCODE.replace('version = "8.3"\n',
                    'version = "8.3"\noptional-dependencies = { pil = [{ name = "zeta" }] }\n'),
], ids=["a need the lock holds", "a need dropped", "a need's marker", "a need's extras",
        "an extra's need"])
def test_a_moved_version_changing_what_it_needs_is_refused(repo, new):
    """qrcode 8.3 needs zeta too, which the lock holds for another install set; or no longer
    needs pillow; or needs it under a marker, with an extra, or through an extra of its own:
    each could move a package into or out of what production installs with no package added
    or removed (ruling plan#2490), so each is more than a version moved."""
    assert _refusal(repo, LOCK, _lock(), new) == (
        "uv.lock moves qrcode from 8.2 to 8.3, a new version that changes what it needs")


def test_every_package_whose_needs_change_is_named(repo):
    """Two moves that change what each needs are named together, in the lock's order; a
    move beside them that needs what it did is not."""
    new = _lock(_PACKAGES | {"pillow": ("12.4.1", ("zeta",)), "qrcode": ("8.3", ()),
                             "zeta": ("0.6.4", ())})
    assert _refusal(repo, LOCK, _lock(), new) == (
        "uv.lock moves pillow from 12.3.0 to 12.4.1 and qrcode from 8.2 to 8.3, a new version "
        "that changes what it needs")


@pytest.mark.parametrize(("new", "why"), [
    (_lock(_PACKAGES | {"olefile": ("0.47", ())}),
     "uv.lock adds olefile, a package of no version before"),
    (_lock({name: _PACKAGES[name] for name in ("pillow", "qrcode")}), "uv.lock removes zeta"),
    (_lock().replace("revision = 5", "revision = 6"), "uv.lock changes its revision"),
    (_lock().replace('"P7D"', '"P1D"'), "uv.lock changes its options"),
    (_lock(header='resolution-markers = ["sys_platform == \'linux\'"]\n'),
     "uv.lock changes its resolution-markers"),
    (_lock().replace(f'name = "zeta"\nversion = "0.6.3"\nsource = {_REGISTRY}',
                     'name = "zeta"\nversion = "0.6.3"\nsource = { registry = "https://evil" }'),
     "uv.lock changes zeta's source"),
    (_lock(_at("qrcode", "8.2", ("pillow", "zeta"))), "uv.lock changes qrcode's dependencies"),
    (_lock().replace(">=8.2,<9.0", ">=8.2,<10.0"), "uv.lock changes shekel's metadata"),
    (_lock().replace('name = "shekel"\nversion = "0"', 'name = "shekel"\nversion = "1"'),
     "uv.lock changes shekel's version"),
    (_lock(extra='\n[[package]]\nname = "zeta"\nversion = "0.7.0"\n'
                 f'source = {_REGISTRY}\n'), "uv.lock holds zeta more than once"),
    (_lock(extra='\n[[package]]\nversion = "1.0"\n'), "uv.lock holds a package with no name"),
    (_lock(_at("pillow", "banana")),
     "uv.lock moves pillow from 12.3.0 to banana, a version this check cannot read"),
], ids=["a package added", "a package removed", "the lock's revision", "its options",
        "a header table", "a package's source", "what an unmoved version needs",
        "the project's own ranges", "the project's own version", "a package twice",
        "a package with no name", "a version no release starts"])
def test_a_lock_change_moving_more_than_versions_is_refused(repo, new, why):
    """A package added or removed, the header, a package's source, what an unmoved version
    needs, the project's own entry (from no registry, so even its version is fixed), a lock
    no package of which is told by its name, and a version this check cannot read: each is
    more than a version moved."""
    assert _refusal(repo, LOCK, _lock(), new) == why


#: Where every file of the lock :func:`_lock` writes lies.
_HOST = "https://files.pythonhosted.org/packages"
#: pillow's first wheel at 12.3.0, as :func:`_files` names it.
_WHEEL = "pillow-12.3.0-py3-none-platform0.whl"


@pytest.mark.parametrize(("new", "why"), [
    (_lock().replace(f"{_HOST}/pillow", "https://example.invalid/packages/pillow"),
     "uv.lock moves pillow's file pillow-12.3.0.tar.gz to https://example.invalid, not "
     "https://files.pythonhosted.org, where its registry's files lay"),
    (_lock().replace(_WHEEL, "evil-12.3.0-py3-none-platform0.whl"),
     "uv.lock gives pillow 12.3.0 the file evil-12.3.0-py3-none-platform0.whl, which is no "
     "file of that release"),
    (_lock(_at("pillow", "12.4.1")).replace(_WHEEL.replace("12.3.0", "12.4.1"), _WHEEL),
     f"uv.lock gives pillow 12.4.1 the file {_WHEEL}, which is no file of that release"),
    (_lock().replace(_WHEEL, "pillow-12.3.0.egg"),
     "uv.lock gives pillow 12.3.0 the file pillow-12.3.0.egg, which is no file of that "
     "release"),
    (_lock().replace(_WHEEL, "pillow-12.3.0-any.whl"),
     "uv.lock gives pillow 12.3.0 the file pillow-12.3.0-any.whl, which is no file of that "
     "release"),
    (_lock().replace(f'sdist = {{ url = "{_HOST}/pillow-12.3.0.tar.gz"',
                     'sdist = { path = "pillow-12.3.0.tar.gz"'),
     "uv.lock names a file of pillow by no URL"),
    (_lock().replace('name = "pillow"\nversion = "12.3.0"', 'name = "pillow"\nversion = "12.4.1"'),
     "uv.lock gives pillow 12.4.1 the file pillow-12.3.0.tar.gz, which is no file of that "
     "release"),
    (_lock().replace(f'{{ url = "{_HOST}/pillow-12.3.0.tar.gz", ',
                     f'{{ url = "{_HOST}/pillow-12.3.0.tar.gz", path = "pillow.tar.gz", '),
     "uv.lock names pillow's file pillow-12.3.0.tar.gz with path, which uv writes of no file"),
    (re.sub(r'(pillow-12\.3\.0\.tar\.gz"), hash = "sha256:[0-9a-f]{64}"', r"\1", _lock()),
     "uv.lock names pillow's file pillow-12.3.0.tar.gz with no hash"),
], ids=["another host", "another project's file", "another release's file",
        "no distribution's name", "a wheel of too few fields", "no URL",
        "a version moved, its files not", "a key uv never writes", "no hash"])
def test_a_file_that_is_not_the_releases_on_its_registry_is_refused(repo, new, why):
    """``uv sync --locked`` downloads each file from the URL the lock names (measured
    2026-10-31), so a change moving no version may still repoint one: each file of a package
    whose files change must lie where the registry's files lay and be named for that package
    at its version, a moved version's included."""
    assert _refusal(repo, LOCK, _lock(), new) == why


def test_a_registry_whose_files_lay_on_two_hosts_has_no_home(repo):
    """Where the lock's files from a registry lay on two hosts before the change, no host is
    the registry's own, so a change to any of its packages' files is refused."""
    elsewhere = f"{_HOST}/zeta", "https://mirror.example/packages/zeta"
    assert _refusal(repo, LOCK, _lock().replace(*elsewhere),
                    _lock(_at("pillow", "12.4.1")).replace(*elsewhere)) == (
        "uv.lock's files from pillow's registry lay on 2 hosts before, not one")


def _as_uv_writes(lock):
    """``lock`` with each file entry as uv writes one of a registry's: its size and upload
    time beside its URL and hash."""
    return re.sub(r'(hash = "sha256:[0-9a-f]{64}") }',
                  r'\1, size = 1024, upload-time = "2026-10-30T12:00:00.000Z" }', lock)


def test_a_file_entry_as_uv_writes_it_moves_its_version(repo):
    """Every key uv writes of a file (measured on the lock 2026-11-01: a URL, a hash, a size
    and an upload time) may move with a release's files."""
    assert _moved(repo, LOCK, _as_uv_writes(_lock()),
                  _as_uv_writes(_lock(_at("pillow", "12.4.1")))) == (
        Move("pillow", "12.3.0", "12.4.1", False),)


@pytest.mark.parametrize(("old", "new", "why"), [
    (_lock(), _lock(_at("pillow", "12.4.1"), files={"pillow": 0}),
     "uv.lock gives pillow 12.4.1 no wheel where it had one, so it would be built from its "
     "source"),
    (_lock(), _lock(files={"pillow": 0}),
     "uv.lock gives pillow 12.3.0 no wheel where it had one, so it would be built from its "
     "source"),
    (_lock(files={"zeta": 0}), _lock(_at("zeta", "0.6.4")),
     "uv.lock gives zeta 0.6.4 a wheel where it had none, so it would no longer be built "
     "from its source"),
], ids=["a release of no wheel", "every wheel dropped", "a wheel where none was"])
def test_a_package_built_from_source_or_installed_built_stays_so(repo, old, new, why):
    """A release with no wheel, or a change dropping every wheel of one, would have every
    install build from source a package it installed built; a wheel where there was none
    would install built one the image compiles (psycopg-c against its own libpq)."""
    assert _refusal(repo, LOCK, old, new) == why


def test_a_release_with_fewer_wheels_still_moves_its_version(repo):
    """A release that drops one platform's wheel and keeps another is the release's files:
    one move."""
    assert _moved(repo, LOCK, _lock(files={"pillow": 2}), _lock(_at("pillow", "12.4.1"))) == (
        Move("pillow", "12.3.0", "12.4.1", False),)


def _raw(repo, data):
    """The bytes ``data`` written to ``repo`` as a blob, as production's mode and object name
    it."""
    held = repo / "raw.bin"
    held.write_bytes(data)
    return f"100644 {git(repo, 'hash-object', '-w', str(held)).strip()}"


@pytest.mark.parametrize(("path", "text"), [(DOCKERFILE, _DOCKERFILE), (LOCK, _lock())],
                         ids=["a Dockerfile", "the lock"])
def test_bytes_that_decode_to_no_text_are_refused_not_raised(repo, path, text):
    """A Latin-1 byte (``é``) in a comment of a file that pins versions decodes to no text:
    :func:`moves`, :func:`pinned` and the walk each refuse it as one, so a landing check
    names it and the daily queue leaves its pull request, where both raised before."""
    broken = _raw(repo, text.encode() + "# café\n".encode("latin-1"))
    why = f"{path} holds bytes this check cannot decode as text"
    refusals = []
    for read in (lambda: moves(repo, path, _blob(repo, text), broken),
                 lambda: pinned(repo, path, broken)):
        with pytest.raises(NotVersions) as refused:
            read()
        refusals.append(str(refused.value))
    assert (refusals, unversioned(repo, {path: (_blob(repo, text), broken)})) == (
        [why, why], (why,))


def test_a_file_name_is_read_as_pep_503_compares_a_project(repo):
    """A wheel spells a project's ``-`` as ``_`` and an old source distribution keeps it,
    and either is the release's file: the project is compared as PEP 503 compares names."""
    old = _lock({"typing-extensions": ("4.15.0", ())})
    new = _lock({"typing-extensions": ("4.15.1", ())}).replace(
        "typing-extensions-4.15.1-py3", "typing_extensions-4.15.1-py3")
    assert _moved(repo, LOCK, old, new) == (
        Move("typing-extensions", "4.15.0", "4.15.1", False),)


def test_a_lock_that_is_no_toml_is_refused(repo):
    """A lock TOML cannot read is no version moved."""
    assert _refusal(repo, LOCK, _lock(), _lock() + "[[package\n").startswith(
        "uv.lock is no TOML: ")


@pytest.mark.parametrize(("new", "found"), [
    (_DOCKERFILE.replace(f"3.14-slim@sha256:{_OLD} AS builder", f"3.14-slim@sha256:{_NEW} "
                         "AS builder").replace(f"3.14-slim@sha256:{_OLD}\n",
                                               f"3.14-slim@sha256:{_NEW}\n"),
     (Move("python", "3.14", "3.14", False), Move("python", "3.14", "3.14", False))),
    (_DOCKERFILE.replace(f"uv:0.13.6@sha256:{_OLD}", f"uv:0.13.9@sha256:{_NEW}"),
     (Move("ghcr.io/astral-sh/uv", "0.13.6", "0.13.9", False),)),
    (_DOCKERFILE.replace(f"uv:0.13.6@sha256:{_OLD}", f"uv:0.14.0@sha256:{_NEW}"),
     (Move("ghcr.io/astral-sh/uv", "0.13.6", "0.14.0", True),)),
], ids=["both Python digests", "uv's patch", "uv's 0.x minor"])
def test_a_from_line_moving_its_tag_and_digest_moves_versions(repo, new, found):
    """A ``FROM`` line's digest, or the version in its tag with it, moves, each line's move
    in the file's order, a 0.x minor a major."""
    assert _moved(repo, DOCKERFILE, _DOCKERFILE, new) == found


@pytest.mark.parametrize(("new", "why"), [
    (_DOCKERFILE.replace(f"FROM python:3.14-slim@sha256:{_OLD} AS",
                         f"FROM pypy:3.14-slim@sha256:{_NEW} AS"),
     "Dockerfile changes more than the version in its image's tag and its digest on line 4"),
    (_DOCKERFILE.replace(f"3.14-slim@sha256:{_OLD} AS", f"3.14-alpine@sha256:{_NEW} AS"),
     "Dockerfile changes more than the version in its image's tag and its digest on line 4"),
    (_DOCKERFILE.replace(f"{_OLD} AS builder", f"{_NEW} AS build"),
     "Dockerfile changes more than the version in its image's tag and its digest on line 4"),
    (_DOCKERFILE.replace("RUN uv sync --locked", "RUN uv sync"),
     "Dockerfile changes line 5, which pins no version by the version in its image's tag "
     "and its digest"),
    (_DOCKERFILE.replace("# the image\n", "# the image\n# measured 2026-10-30\n"),
     "Dockerfile adds or deletes a line"),
    (_DOCKERFILE.replace("\n", "\r\n"),
     "Dockerfile holds text this check cannot read byte for byte (a line ending, or bytes no "
     "decoding keeps)"),
], ids=["the image's name", "the tag's variant", "the stage's name", "another line",
        "a line added", "line endings"])
def test_a_dockerfile_change_moving_more_than_versions_is_refused(repo, new, why):
    """A ``FROM`` line naming another image, variant or stage, an edit to a line that pins
    nothing, a line added, and line endings changed: each is more than a version moved."""
    assert _refusal(repo, DOCKERFILE, _DOCKERFILE, new) == why


def test_a_tag_naming_no_version_moves_nothing_this_check_reads(repo):
    """A digest moved under a tag that names no version (``alpine``) could cross any major,
    so it is refused."""
    old = f"FROM nginx:alpine@sha256:{_OLD}\n"
    assert _refusal(repo, DOCKERFILE, old, old.replace(_OLD, _NEW)) == (
        "Dockerfile changes line 1, which pins no version by the version in its image's tag "
        "and its digest")


def test_a_compose_image_line_moves_its_tag_and_digest(repo):
    """postgres moves to its next minor, tag and digest together: one move within the major;
    its next major is one across it."""
    minor = _COMPOSE.replace(f"18-alpine@sha256:{_OLD}", f"18.7-alpine@sha256:{_NEW}")
    major = _COMPOSE.replace(f"18-alpine@sha256:{_OLD}", f"19-alpine@sha256:{_NEW}")
    assert (_moved(repo, COMPOSE, _COMPOSE, minor), _moved(repo, COMPOSE, _COMPOSE, major)) == (
        (Move("postgres", "18", "18.7", False),), (Move("postgres", "18", "19", True),))


@pytest.mark.parametrize(("new", "why"), [
    (_COMPOSE.replace(f"postgres:18-alpine@sha256:{_OLD}",
                      f"evil/postgres:18-alpine@sha256:{_NEW}"),
     "docker-compose.yml changes more than the version in its image's tag and its digest on "
     "line 4"),
    (_COMPOSE.replace(f"@sha256:{_OLD}", f"@sha256:{_NEW}  # 18.7"),
     "docker-compose.yml changes more than the version in its image's tag and its digest on "
     "line 4"),
    (_COMPOSE.replace("container_name: db", "container_name: prod-db"),
     "docker-compose.yml changes line 5, which pins no version by the version in its image's "
     "tag and its digest"),
], ids=["the image's name", "a comment on the line", "another line"])
def test_a_compose_change_moving_more_than_versions_is_refused(repo, new, why):
    """A digest moved with the image's name, a comment on its line or another line changed
    is more than a version moved."""
    assert _refusal(repo, COMPOSE, _COMPOSE, new) == why


@pytest.mark.parametrize(("new", "found"), [
    (_PUBLISH.replace(f"checkout@{_SHA} # v7.0.1", f"checkout@{_LATER} # v7.1.0"),
     (Move("actions/checkout", "7.0.1", "7.1.0", False),)),
    (_PUBLISH.replace(f"checkout@{_SHA} # v7.0.1", f"checkout@{_LATER} # v8.0.0"),
     (Move("actions/checkout", "7.0.1", "8.0.0", True),)),
    (_PUBLISH.replace(_SHA, _LATER).replace("v7.0.1", "v7.0.2").replace("v4.6.0", "v4.7.0"),
     (Move("actions/checkout", "7.0.1", "7.0.2", False),
      Move("docker/login-action", "4.6.0", "4.7.0", False))),
], ids=["a minor", "a major", "both, a list item one"])
def test_a_uses_line_moving_its_commit_and_release_moves_versions(repo, tags, new, found):
    """An action's commit moves with its release, the commit its repository tags with that
    release, each asked once, in the file's order."""
    tags.tagged.update({(move.what, move.new): _LATER for move in found})
    assert (_moved(repo, PUBLISH, _PUBLISH, new), tags.reads) == (
        found, [(move.what, move.new) for move in found])


def test_a_commit_moved_under_its_release_is_refused_unread(repo, tags):
    """A commit that moves while its release stays is a tag moved after the fact, the shape
    of a release repointed at another commit: refused before GitHub is asked (ruling
    plan#2474)."""
    new = _PUBLISH.replace(f"login-action@{_SHA}", f"login-action@{_LATER}")
    assert (_refusal(repo, PUBLISH, _PUBLISH, new), tags.reads) == (
        f"{PUBLISH} pins docker/login-action on line 6 at another commit under its release "
        "v4.6.0: one release has one commit, so its tag was moved or a pin was never its "
        "commit", [])


@pytest.mark.parametrize(("tagged", "why"), [
    ({}, "as v7.1.0, a release actions/checkout does not tag"),
    ({("actions/checkout", "7.1.0"): "3" * 40},
     f"at {_LATER} as v7.1.0, which actions/checkout tags at {'3' * 40}"),
], ids=["no such tag", "another commit"])
def test_a_commit_its_release_does_not_tag_is_refused(repo, tags, tagged, why):
    """GitHub runs the commit, never its label, and runs one only a fork of the repository
    holds under the original's name: a commit labelled with a release its repository tags at
    another commit, or does not tag, is more than a version moved."""
    tags.tagged.update(tagged)
    new = _PUBLISH.replace(f"checkout@{_SHA} # v7.0.1", f"checkout@{_LATER} # v7.1.0")
    assert _refusal(repo, PUBLISH, _PUBLISH, new) == (
        f"{PUBLISH} pins actions/checkout on line 5 {why}")


def test_a_label_moved_alone_is_read_against_its_new_release(repo, tags):
    """The label moves and the commit stays: the commit must be the new release's, as when
    both move."""
    tags.tagged[("actions/checkout", "7.0.2")] = _SHA
    assert (_moved(repo, PUBLISH, _PUBLISH, _PUBLISH.replace("# v7.0.1", "# v7.0.2")),
            tags.reads) == ((Move("actions/checkout", "7.0.1", "7.0.2", False),),
                            [("actions/checkout", "7.0.2")])


def test_an_action_below_its_repository_is_read_from_the_repository(repo, tags):
    """``github/codeql-action/init`` is an action of ``github/codeql-action``, whose tags
    name its release's commit."""
    old = f"steps:\n  - uses: github/codeql-action/init@{_SHA} # v4.1.0\n"
    tags.tagged[("github/codeql-action", "4.2.0")] = _LATER
    assert (_moved(repo, ".github/workflows/ci.yml", old,
                   old.replace(f"{_SHA} # v4.1.0", f"{_LATER} # v4.2.0")), tags.reads) == (
        (Move("github/codeql-action/init", "4.1.0", "4.2.0", False),),
        [("github/codeql-action", "4.2.0")])


@pytest.fixture(name="github")
def _github(tmp_path, monkeypatch):
    """A directory standing in for GitHub (``_versions._GITHUB``), each repository under it a
    real one ``git ls-remote`` reads, as it reads GitHub's; its path."""
    root = tmp_path / "github"
    root.mkdir()
    monkeypatch.setattr(_versions, "_GITHUB", str(root))
    return root


def _upstream(github, owner, name):
    """The repository ``owner/name`` under ``github``, holding one commit; it and the
    commit."""
    (github / owner).mkdir(exist_ok=True)
    root = repository(github / owner, name)
    return root, commit(root, "a release")


#: A workflow of no production's, where :func:`_checkout` pins checkout.
_CI = ".github/workflows/ci.yml"


def _checkout(at, release):
    """A workflow pinning checkout at the commit ``at`` as ``release``, on line 2."""
    return f"steps:\n  - uses: actions/checkout@{at} # v{release}\n"


@pytest.mark.parametrize("annotation", [(), ("-a", "-m", "v7.1.0")],
                         ids=["a lightweight tag", "an annotated tag"])
def test_a_release_tag_on_github_vouches_for_its_commit(repo, github, annotation):
    """The repository's tag ``v7.1.0`` names the commit, an annotated tag read through to the
    commit it tags: the move is a version moved."""
    upstream, released = _upstream(github, "actions", "checkout")
    _scratch(upstream, "tag", *annotation, "v7.1.0", released)
    assert _moved(repo, _CI, _checkout(_SHA, "7.0.1"), _checkout(released, "7.1.0")) == (
        Move("actions/checkout", "7.0.1", "7.1.0", False),)


def test_an_annotated_tag_s_own_object_is_no_release_s_commit(repo, github):
    """A line pinning the tag's own object, not the commit it tags, is refused: GitHub runs
    no tag object."""
    upstream, released = _upstream(github, "actions", "checkout")
    _scratch(upstream, "tag", "-a", "-m", "v7.1.0", "v7.1.0", released)
    tag = _scratch(upstream, "rev-parse", "refs/tags/v7.1.0")
    assert tag != released
    assert _refusal(repo, _CI, _checkout(_SHA, "7.0.1"), _checkout(tag, "7.1.0")) == (
        f"{_CI} pins actions/checkout on line 2 at {tag} as v7.1.0, which actions/checkout "
        f"tags at {released}")


def test_a_release_its_repository_on_github_does_not_tag_is_refused(repo, github):
    """The repository holds ``v7.0.1`` alone: ``v7.1.0`` is a release it does not tag."""
    upstream, released = _upstream(github, "actions", "checkout")
    _scratch(upstream, "tag", "v7.0.1", released)
    assert _refusal(repo, _CI, _checkout(_SHA, "7.0.1"), _checkout(released, "7.1.0")) == (
        f"{_CI} pins actions/checkout on line 2 as v7.1.0, a release actions/checkout does "
        "not tag")


def test_a_repository_github_cannot_read_is_no_judgment(repo, github):
    """No such repository: no judgment is made, as for any file git cannot read, so the
    landing check reports a change it could not judge and the queue a pull request it failed
    on, never one needing a card; the walk raises git's own error, naming the repository."""
    changed = {_CI: (_blob(repo, _checkout(_SHA, "7.0.1")),
                     _blob(repo, _checkout(_LATER, "7.1.0")))}
    with pytest.raises(GitError, match=f"ls-remote {re.escape(str(github))}/actions/checkout "):
        unversioned(repo, changed, "the tree")


@pytest.mark.parametrize(("new", "why"), [
    (_PUBLISH.replace(f"actions/checkout@{_SHA} # v7.0.1", f"evil/checkout@{_LATER} # v7.0.2"),
     ".github/workflows/docker-publish.yml changes more than its action's commit and release "
     "on line 5"),
    (_PUBLISH.replace("- name: Check out", "- name: Check it out"),
     ".github/workflows/docker-publish.yml changes line 4, which pins no version by its "
     "action's commit and release"),
], ids=["the action", "another line"])
def test_a_publish_change_moving_more_than_versions_is_refused(repo, new, why):
    """Another action, or another line, is more than a version moved."""
    assert _refusal(repo, PUBLISH, _PUBLISH, new) == why


def test_a_file_pinning_no_version_is_refused(repo):
    """``pyproject.toml`` and the app's code pin no version a change may move alone."""
    with pytest.raises(NotVersions, match="^pyproject.toml pins no version$"):
        moves(repo, "pyproject.toml", _blob(repo, "a\n"), _blob(repo, "b\n"))


@pytest.mark.parametrize(("was", "now", "why"), [
    (None, "100644", "uv.lock enters production"),
    ("100644", None, "uv.lock leaves production"),
    ("100644", "100755", "uv.lock changes its mode"),
    ("120000", "120000", "uv.lock is no file, but a link or a submodule's commit"),
], ids=["added", "deleted", "made executable", "a link"])
def test_a_pinning_file_added_deleted_or_no_file_is_refused(repo, was, now, why):
    """A file that pins versions, added to production or taken from it, its mode changed, or
    no file at all, moves no version as its text."""
    with pytest.raises(NotVersions) as refused:
        moves(repo, LOCK, was and _blob(repo, _lock(), was),
              now and _blob(repo, _lock(_at("pillow", "12.4.1")), now))
    assert str(refused.value) == why


# -- what one side pins ---------------------------------------------------------------------

def _pins(repo, path, text, mode="100644"):
    """What :func:`pinned` says ``path`` pins where production holds the text ``text``."""
    return pinned(repo, path, _blob(repo, text, mode))


def test_a_lock_pins_each_package_at_its_version(repo):
    """Every package the lock holds pins its version, the project's own included."""
    assert _pins(repo, LOCK, _lock()) == {Pin("pillow", "12.3.0"), Pin("qrcode", "8.2"),
                                          Pin("zeta", "0.6.3"), Pin("shekel", "0")}


def test_a_forked_lock_pins_each_version_a_name_holds(repo):
    """A resolution forked by environment holds zeta twice, which :func:`moves` refuses: what
    a side pins asks nothing of a move, so each version is pinned."""
    forked = _lock(extra=f'\n[[package]]\nname = "zeta"\nversion = "0.7.0"\n'
                         f'source = {_REGISTRY}\n')
    assert _pins(repo, LOCK, forked) == {Pin("pillow", "12.3.0"), Pin("qrcode", "8.2"),
                                         Pin("zeta", "0.6.3"), Pin("zeta", "0.7.0"),
                                         Pin("shekel", "0")}


def test_a_package_of_no_version_pins_none(repo):
    """A project whose version is dynamic is locked with no version, and pins none."""
    dynamic = _lock().replace('name = "shekel"\nversion = "0"\n', 'name = "shekel"\n')
    assert _pins(repo, LOCK, dynamic) == {Pin("pillow", "12.3.0"), Pin("qrcode", "8.2"),
                                          Pin("zeta", "0.6.3")}


@pytest.mark.parametrize(("text", "why"), [
    (_lock() + "[[package\n", "uv.lock is no TOML: "),
    (_lock(extra='\n[[package]]\nversion = "1.0"\n'), "uv.lock holds a package with no name"),
], ids=["no TOML", "a package with no name"])
def test_a_lock_no_pin_of_which_can_be_named_is_refused(repo, text, why):
    """A lock TOML cannot read, or holding a package with no name, names no pin."""
    with pytest.raises(NotVersions) as refused:
        _pins(repo, LOCK, text)
    assert str(refused.value).startswith(why)


def test_a_dockerfile_pins_each_image_its_from_lines_pin(repo):
    """Each ``FROM`` line pinning by tag and digest pins its image at the tag's version
    through its digest, two lines pinning one image alike pinning it once; a stage from
    nothing, and an image under a tag naming no version, pin none."""
    text = _DOCKERFILE + f"\nFROM scratch\nFROM nginx:alpine@sha256:{_NEW}\n"
    assert _pins(repo, DOCKERFILE, text) == {
        Pin("ghcr.io/astral-sh/uv", f"0.13.6@sha256:{_OLD}"),
        Pin("python", f"3.14-slim@sha256:{_OLD}")}


def test_a_dockerfile_of_other_line_endings_pins_alike(repo):
    """What a side pins asks nothing of its line endings, which :func:`moves` refuses."""
    assert (_pins(repo, DOCKERFILE, _DOCKERFILE.replace("\n", "\r\n"))
            == _pins(repo, DOCKERFILE, _DOCKERFILE))


def test_a_compose_file_pins_each_image_pinned_by_tag_and_digest(repo):
    """postgres is pinned at its tag's version through its digest; the app's own image,
    under a tag alone, pins none."""
    text = _COMPOSE + "  app:\n    image: ghcr.io/saltyreformed-labs/shekel:latest\n"
    assert _pins(repo, COMPOSE, text) == {Pin("postgres", f"18-alpine@sha256:{_OLD}")}


def test_the_publish_workflow_pins_each_action_at_its_commit_and_release(repo):
    """Each action is pinned at its commit through its release; an action of the
    repository's own pins none."""
    text = _PUBLISH + "      - uses: $/.github/actions/install-lock\n"
    assert _pins(repo, PUBLISH, text) == {Pin("actions/checkout", f"{_SHA} # v7.0.1"),
                                          Pin("docker/login-action", f"{_SHA} # v4.6.0")}


def test_a_file_production_does_not_hold_pins_none(repo):
    """A side holding no such file pins nothing."""
    assert pinned(repo, LOCK, None) == frozenset()


def test_a_link_where_a_pinning_file_was_is_refused(repo):
    """A link's text is not the file's, so it names no pin."""
    with pytest.raises(NotVersions,
                       match="^uv.lock is no file, but a link or a submodule's commit$"):
        _pins(repo, LOCK, _lock(), mode="120000")


# -- every file Dependabot and the update move (the daily queue's reading) ------------------

#: A workflow pinning one action by commit, its release beside it, as ci.yml does.
_WORKFLOW = (f"jobs:\n  test:\n    steps:\n      - uses: actions/checkout@{_SHA} # v7.0.1\n"
             "      - run: pytest\n")
#: A local action of this repository's own, pinning one action by its commit and release.
_ACTION = (f"runs:\n  using: composite\n  steps:\n"
           f"    - uses: actions/cache@{_SHA} # v6.1.0\n")


@pytest.mark.parametrize(("path", "pinning"), [
    (LOCK, True), (DOCKERFILE, True), (COMPOSE, True), ("docker-compose.dev.yml", True),
    (PUBLISH, True), (".github/workflows/ci.yml", True),
    (".github/actions/install-lock/action.yml", True), ("pyproject.toml", False),
    ("app/__init__.py", False), (".github/dependabot.yml", False),
    ("deploy/docker-compose.yml", False), (".github/workflows/old/ci.yml", False),
    (".github/actions/a/b/action.yml", False), ("plan/ships/1183", False),
])
def test_the_files_that_pin_versions_are_the_lock_and_each_line_rules_file(path, pinning):
    """The lock and every file Dependabot moves a version in: the Dockerfile, each compose
    file at the root, each workflow and each of this repository's actions.  A pattern's
    ``*`` crosses no directory, so a compose file elsewhere, or a workflow in a directory
    below, pins none this module reads."""
    assert pins_versions(path) is pinning


@pytest.fixture(name="released")
def _released(tags):
    """The releases the cases below move an action to, each tagged at :data:`_LATER`."""
    tags.tagged.update({("actions/checkout", "8.0.0"): _LATER,
                        ("actions/cache", "6.2.0"): _LATER})


@pytest.mark.parametrize(("path", "old", "new", "found"), [
    ("docker-compose.dev.yml", _COMPOSE,
     _COMPOSE.replace(f"18-alpine@sha256:{_OLD}", f"18.7-alpine@sha256:{_NEW}"),
     (Move("postgres", "18", "18.7", False),)),
    (".github/workflows/ci.yml", _WORKFLOW,
     _WORKFLOW.replace(f"{_SHA} # v7.0.1", f"{_LATER} # v8.0.0"),
     (Move("actions/checkout", "7.0.1", "8.0.0", True),)),
    (".github/actions/test-only/action.yml", _ACTION,
     _ACTION.replace(f"{_SHA} # v6.1.0", f"{_LATER} # v6.2.0"),
     (Move("actions/cache", "6.1.0", "6.2.0", False),)),
], ids=["the dev compose file", "a test workflow's major", "a local action"])
@pytest.mark.usefixtures("released")
def test_a_file_production_never_runs_moves_its_versions_by_its_kind(repo, path, old, new,
                                                                    found):
    """A compose file, workflow or action production never runs pins versions by the same
    rule as production's own of its kind, an action's commit its release's."""
    assert _moved(repo, path, old, new) == found


def test_a_workflow_line_that_pins_nothing_is_refused(repo):
    """Any other line of a workflow is more than a version moved."""
    assert _refusal(repo, ".github/workflows/ci.yml", _WORKFLOW,
                    _WORKFLOW.replace("run: pytest", "run: pytest -x")) == (
        ".github/workflows/ci.yml changes line 5, which pins no version by its action's commit "
        "and release")


def test_a_file_pinning_no_version_names_no_pin(repo):
    """What a side pins is asked only of a file that pins versions."""
    with pytest.raises(NotVersions, match="^pyproject.toml pins no version$"):
        pinned(repo, "pyproject.toml", _blob(repo, "a\n"))


def test_the_walk_names_every_reason_and_nothing_for_versions_alone(repo):
    """:func:`unversioned` names each major and each refusal, a clause each, in the order of
    the paths it is given, and nothing for a change moving versions alone; the side a file
    is added to is the one the walk is told it reads."""
    major = _lock(_at("pillow", "13.0.0"))
    changed = {LOCK: (_blob(repo, _lock()), _blob(repo, major)),
               "app/x.py": (_blob(repo, "a\n"), _blob(repo, "b\n")),
               ".github/workflows/new.yml": (None, _blob(repo, _WORKFLOW))}
    assert unversioned(repo, changed, "the tree") == (
        "uv.lock moves pillow from 12.3.0 to 13.0.0, a new major version",
        "app/x.py pins no version", ".github/workflows/new.yml enters the tree")
    assert not unversioned(repo, {LOCK: (_blob(repo, _lock()),
                                         _blob(repo, _lock(_at("pillow", "12.4.1"))))})


def _tree_of(root, base, files):
    """A commit on ``base`` (an empty commit when None) writing ``files``; its sha."""
    return commit_files(root, base or commit(root, "start"), "files", files)


def test_a_change_between_two_commits_is_judged_over_every_file_it_changes(repo, tags):
    """:func:`unversioned_between` reads each file the tree changes, production's or not: a
    Dependabot-like change moving a workflow's action and the dev compose file's digest
    moves versions alone; one also writing a plan record, or crossing a major, is not, each
    reason waiting on its refusal or on what moves into which major."""
    tags.tagged[("actions/checkout", "7.1.0")] = _LATER
    base = _tree_of(repo, None, {LOCK: _lock(), ".github/workflows/ci.yml": _WORKFLOW,
                                 "docker-compose.dev.yml": _COMPOSE, "app/x.py": "a\n"})
    alone = _tree_of(repo, base, {
        ".github/workflows/ci.yml": _WORKFLOW.replace(f"{_SHA} # v7.0.1", f"{_LATER} # v7.1.0"),
        "docker-compose.dev.yml": _COMPOSE.replace(_OLD, _NEW),
        LOCK: _lock(_at("pillow", "12.4.1"))})
    more = _tree_of(repo, alone, {"plan/ships/9": "x\n", LOCK: _lock(_at("pillow", "13.0.0"))})
    assert (unversioned_between(repo, base, alone), unversioned_between(repo, alone, more)) == (
        (), (Reason("plan/ships/9 pins no version", "plan/ships/9 pins no version"),
             Reason("uv.lock moves pillow from 12.4.1 to 13.0.0, a new major version",
                    "pillow 13")))


def test_a_major_beside_a_package_added_is_named_beside_it(repo):
    """The lock adds olefile and moves pillow across a major: the walk names the refusal,
    then the major, each waiting on its own thing, where it named the refusal alone; what
    :func:`moves` raises is the refusal, as before."""
    new = _lock(_PACKAGES | {"pillow": ("13.0.0", ()), "olefile": ("0.47", ())})
    added = "uv.lock adds olefile, a package of no version before"
    major = "uv.lock moves pillow from 12.3.0 to 13.0.0, a new major version"
    base = _tree_of(repo, None, {LOCK: _lock()})
    assert (unversioned_between(repo, base, _tree_of(repo, base, {LOCK: new})),
            lock_unversioned(_lock(), new), _refusal(repo, LOCK, _lock(), new)) == (
        (Reason(added, added), Reason(major, "pillow 13")), (added, major), added)


def test_a_move_across_a_major_waits_on_the_major_it_enters_however_far_and_wherever(repo,
                                                                                     tags):
    """Each move across a major waits on what moves and the major it moves into, spelled as
    :func:`ceiling` spells a release: the same for any release of that major and in any file
    (a lock's package, an action's release, an image's tag), and another major for the next;
    a 0.x package's minor is its major.  Only what each waits on is new here: each clause is
    the one :func:`unversioned` names."""
    tags.tagged.update({("actions/checkout", release): _LATER
                        for release in ("8.0.0", "8.3.1", "9.0.0")})
    base = _tree_of(repo, None, {LOCK: _lock(), ".github/workflows/ci.yml": _WORKFLOW,
                                 "docker-compose.dev.yml": _COMPOSE})
    waiting = {}
    for case, lock, release, image in (
            ("first", {"pillow": "13.0.0", "zeta": "0.7.0"}, "8.0.0", "19"),
            ("later", {"pillow": "13.2.1", "zeta": "0.7.4"}, "8.3.1", "19.1"),
            ("next", {"pillow": "14.0.0", "zeta": "0.8.0"}, "9.0.0", "20")):
        moved = _tree_of(repo, base, {
            LOCK: _lock(_PACKAGES | {name: (version, ()) for name, version in lock.items()}),
            ".github/workflows/ci.yml": _WORKFLOW.replace(f"{_SHA} # v7.0.1",
                                                          f"{_LATER} # v{release}"),
            "docker-compose.dev.yml": _COMPOSE.replace("postgres:18-", f"postgres:{image}-")})
        reasons = unversioned_between(repo, base, moved)
        assert all(reason.clause.endswith(", a new major version") for reason in reasons)
        waiting[case] = sorted(reason.waits_on for reason in reasons)
    assert waiting == {
        "first": ["actions/checkout 8", "pillow 13", "postgres 19", "zeta 0.7"],
        "later": ["actions/checkout 8", "pillow 13", "postgres 19", "zeta 0.7"],
        "next": ["actions/checkout 9", "pillow 14", "postgres 20", "zeta 0.8"]}


# -- what the Monday refresh reads (tools/update/refresh.py) --------------------------------

@pytest.mark.parametrize(("version", "below"), [
    ("13.0.1", "14"), ("0.6.3", "0.7"), ("0.0.3", "0.0.4"), ("2026.5", "2027"),
    ("1!2.0", "1!3"), ("3.20.1.post1", "4"), ("0.0.0", None), ("banana", None),
], ids=["a major", "a 0.x minor", "a 0.0.x patch", "a calendar year", "an epoch",
        "a post release", "zeros alone", "no release"])
def test_the_ceiling_is_the_first_release_of_the_next_major(version, below):
    """A package held below :func:`ceiling` keeps its major as :func:`_major` reads it, the
    epoch included; a version whose every successor crosses a major has none."""
    assert ceiling(version) == below


def test_a_move_needing_a_package_the_lock_lacked_brings_it():
    """qrcode's new version needs olefile, which the lock did not hold: the change adds
    olefile, and qrcode, at its version before, brings it."""
    new = _lock(_at("qrcode", "8.3", ("pillow", "olefile")) | {"olefile": ("0.47", ())})
    change = lock_change(_lock(), new)
    assert (change.added, change.removed, dict(change.bringing), change.moves) == (
        ("olefile",), (), {"qrcode": "8.2"}, (Move("qrcode", "8.2", "8.3", False),))


def test_a_move_changing_what_it_needs_brings_with_no_package_added():
    """qrcode's new version needs zeta too, which the lock already holds: nothing is added,
    and qrcode, at its version before, is one whose move changes what it brings, which the
    refresh holds back as it holds a move adding a package."""
    change = lock_change(_lock(), _lock(_at("qrcode", "8.3", ("pillow", "zeta"))))
    assert (change.added, change.removed, dict(change.bringing), change.moves) == (
        (), (), {"qrcode": "8.2"}, (Move("qrcode", "8.2", "8.3", False),))


def test_a_move_no_longer_needing_a_package_only_it_needed_drops_it():
    """qrcode's new version no longer needs zeta, which only it needed: the change removes
    zeta, and qrcode, at its version before, drops it; pillow, still needed, stays."""
    old = _lock(_at("qrcode", "8.2", ("pillow", "zeta")))
    new = _lock({"pillow": ("12.3.0", ()), "qrcode": ("8.3", ("pillow",))})
    change = lock_change(old, new)
    assert (change.added, change.removed, dict(change.bringing)) == ((), ("zeta",),
                                                                      {"qrcode": "8.2"})


def test_a_lock_made_from_an_edited_pyproject_is_read_for_its_versions_alone():
    """qrcode's range moved to its new major with the lock: the project's own entry changes
    with it, which :func:`lock_change` refuses, and :func:`lock_moves` reads past to the
    versions, each move by the one reading of a major, an added package named."""
    old = _lock()
    new = _lock(_at("qrcode", "9.0", ("pillow", "olefile")) | {"olefile": ("0.47", ())}).replace(
        'specifier = ">=8.2,<9.0"', 'specifier = ">=9.0,<10"')
    with pytest.raises(NotVersions, match="^uv.lock changes shekel's metadata$"):
        lock_change(old, new)
    change = lock_moves(old, new)
    assert (change.moves, change.added, change.removed, dict(change.bringing)) == (
        (Move("qrcode", "8.2", "9.0", True),), ("olefile",), (), {"qrcode": "8.2"})


def test_the_lock_alone_is_judged_by_the_walk_of_every_file():
    """:func:`lock_unversioned` names a major and a refusal as :func:`unversioned` does, and
    nothing for versions alone."""
    assert (lock_unversioned(_lock(), _lock(_at("pillow", "13.0.0"))),
            lock_unversioned(_lock(), _lock(_PACKAGES | {"olefile": ("0.47", ())})),
            lock_unversioned(_lock(), _lock(_at("pillow", "12.4.1")))) == (
        ("uv.lock moves pillow from 12.3.0 to 13.0.0, a new major version",),
        ("uv.lock adds olefile, a package of no version before",), ())
