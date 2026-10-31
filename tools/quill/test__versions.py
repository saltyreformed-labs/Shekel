"""The versions a change moves in the files production runs (:mod:`tools.quill._versions`,
ruling plan#2351): what a change that moves pinned versions alone is, and what a major is.

Each file's two sides are blobs written to a throwaway repository (:mod:`tools.ci.scratch`),
judged as CI's landing check judges a path where production differs
(:func:`_production.changed`'s mode and object); the landing check's own use of them is
``test_landing_versions.py``'s.
"""
from __future__ import annotations

import hashlib

import pytest

from tools.ci.gitcmd import git
from tools.ci.scratch import repository
from tools.quill._production import COMPOSE, DOCKERFILE, PUBLISH
from tools.quill._versions import LOCK, Move, NotVersions, Pin, moves, pinned

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
    source distribution, and that many wheels, each hashed by its name."""
    def hashed(file):
        return f'hash = "sha256:{hashlib.sha256(file.encode()).hexdigest()}"'
    host = "https://files.pythonhosted.org/packages"
    wheels = "".join(f'    {{ url = "{host}/{name}-{version}-{n}.whl", '
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


def test_a_moved_version_may_change_what_it_needs(repo):
    """qrcode's new version needs zeta too, a package the lock already holds: what a version
    declares moves with it."""
    assert _moved(repo, LOCK, _lock(), _lock(_at("qrcode", "8.3", ("pillow", "zeta")))) == (
        Move("qrcode", "8.2", "8.3", False),)


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
    (_PUBLISH.replace(f"login-action@{_SHA}", f"login-action@{_LATER}"),
     (Move("docker/login-action", "4.6.0", "4.6.0", False),)),
    (_PUBLISH.replace(_SHA, _LATER).replace("v7.0.1", "v7.0.2").replace("v4.6.0", "v4.7.0"),
     (Move("actions/checkout", "7.0.1", "7.0.2", False),
      Move("docker/login-action", "4.6.0", "4.7.0", False))),
], ids=["a minor", "a major", "a commit under its release", "both, a list item one"])
def test_a_uses_line_moving_its_commit_and_release_moves_versions(repo, new, found):
    """An action's commit moves with its release, or alone under it."""
    assert _moved(repo, PUBLISH, _PUBLISH, new) == found


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
