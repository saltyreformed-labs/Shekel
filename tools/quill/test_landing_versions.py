"""CI's landing check, rule 9's narrowing (ruling plan#2351): a change that moves pinned
versions alone lands with no card, and anything else, a major included, still needs one.

Every history is a throwaway repository's, built and judged as the other landing tests build
and judge theirs (:mod:`tools.quill._histories`), on a base holding each file that pins
versions as the repository's own pins them, the lock and ``pyproject.toml`` in the image and
the rest outside it (:func:`_pinned`).  What a version-only change is, file by file, is
graded on its own in ``test__versions.py``; here each kind lands alone and together, and
each refusal names what more the change does.
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import commit_files, point_dev, point_main
from tools.quill._git import task_file
from tools.quill._histories import MON, Lookup, card_node, claim, judging, never, reader, start
from tools.quill._histories import tracker, verdict

#: What the base's image leaves out, as the repository's own ignore file does: the lock,
#: ``pyproject.toml`` and the app are in it.
_IGNORED = "plan/\n.dockerignore\nDockerfile\ndocker-compose*.yml\n.github/\n"
#: The workflow that builds and signs the image (R-BAL401), and another that only tests.
_PUBLISH, _CI = ".github/workflows/docker-publish.yml", ".github/workflows/ci.yml"
#: Two digests, and two commits: a pin's before and after.
_OLD, _NEW, _SHA, _LATER = "a" * 64, "b" * 64, "1" * 40, "2" * 40
#: The base's pinned files, each before a change moves it.
_BASE = {
    "uv.lock": ('version = 1\nrevision = 5\nrequires-python = ">=3.14"\n\n'
                '[[package]]\nname = "pillow"\nversion = "12.3.0"\n'
                'source = { registry = "https://pypi.org/simple" }\n'
                f'sdist = {{ url = "https://files.pythonhosted.org/p-12.3.0.tar.gz", '
                f'hash = "sha256:{_OLD}" }}\n'),
    "pyproject.toml": '[project]\nname = "shekel"\ndependencies = ["qrcode>=8.2,<9.0"]\n',
    "Dockerfile": f"FROM python:3.14-slim@sha256:{_OLD} AS builder\nRUN uv sync --locked\n",
    "docker-compose.yml": (f"services:\n  db:\n    image: postgres:18-alpine@sha256:{_OLD}\n"
                           "    container_name: db\n"),
    _PUBLISH: f"jobs:\n  build:\n    steps:\n      - uses: actions/checkout@{_SHA} # v7.0.1\n",
    _CI: f"jobs:\n  test:\n    steps:\n      - uses: actions/checkout@{_SHA} # v7.0.1\n",
}
#: Each pinned file's version moved within its major, by path.
_MOVED = {
    "uv.lock": _BASE["uv.lock"].replace("12.3.0", "12.4.1").replace(_OLD, _NEW),
    "Dockerfile": _BASE["Dockerfile"].replace(_OLD, _NEW),
    "docker-compose.yml": _BASE["docker-compose.yml"].replace(
        f"18-alpine@sha256:{_OLD}", f"18.7-alpine@sha256:{_NEW}"),
    _PUBLISH: _BASE[_PUBLISH].replace(f"{_SHA} # v7.0.1", f"{_LATER} # v7.1.0"),
}
#: Where a card's claim must record a change, as rule 9 has always said it.
_CARD = ("but adds, changes or deletes no ship marker, so no release would count it: a change to "
         "a file the production image holds, Dockerfile, docker-compose.yml, "
         ".github/workflows/docker-publish.yml, scripts/_container_lib.sh or deploy/ (a "
         "README.md there excepted) lands from a card's claim (quill claim <card>), whose "
         "marker records it, and only a change that moves pinned versions alone, writing no "
         "plan record, lands with none (ruling plan#2351): here")


def _refused(head, paths, why):
    """Rule 9's sentence for a change from ``head`` to production's ``paths`` that moves more
    than versions, ``why`` naming what more."""
    return f"REFUSED -- {head} changes what production runs ({paths}), {_CARD} {why}"


@pytest.fixture(name="pinned")
def _pinned(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base (the start of
    every history) with :data:`_IGNORED` for its ignore file, an app file, a task's done file
    and each of :data:`_BASE`; the repository and the base."""
    root, start_ = start(tmp_path)
    base = commit_files(root, start_, "the pins", {
        ".dockerignore": _IGNORED, task_file(9): f"done {MON}\n", **_BASE})
    point_dev(root, base)
    point_main(root, base)
    return root, base


@pytest.fixture(name="check")
def _check(capsys, pinned):
    """The check over pull request #600 of branch ``head`` at ``head_sha`` into ``dev``,
    whose tip is ``onto`` (:func:`_histories.judging`), reading the tracker through
    ``github`` (no read allowed when None); its exit status, output and error."""
    judged = judging(capsys, pinned[0])

    def _run(onto, head_sha, head, *, github=None):
        """Judge pull request #600 of ``head`` into ``dev`` (the fixture)."""
        return judged(onto, head_sha, head, "dev", reader(github) if github else never)

    return _run


@pytest.mark.parametrize("paths", [
    ("uv.lock",), ("Dockerfile",), ("docker-compose.yml",), (_PUBLISH,),
    ("uv.lock", "Dockerfile", "docker-compose.yml", _PUBLISH),
], ids=["the lock", "a FROM line", "a compose image line", "a publish uses line", "all four"])
def test_a_change_moving_pinned_versions_alone_lands_with_no_card(check, pinned, paths):
    """dependabot/... or the Monday refresh moves versions within their majors, in one file
    or all four: it changes production, writes no marker, and lands, the tracker unread."""
    root, base = pinned
    head = commit_files(root, base, "chore(deps): versions",
                        {path: _MOVED[path] for path in paths})
    assert check(base, head, "dependabot/versions")[:2] == (
        0, verdict("dependabot/versions", 0, 0) + "\n")


def test_a_version_bump_beside_a_workflow_that_only_tests_lands(check, pinned):
    """Dependabot's actions group moves checkout in the publish workflow and in CI's: CI's
    workflow is none of production's, so the change still moves versions alone."""
    root, base = pinned
    head = commit_files(root, base, "ci(deps): checkout", {
        _PUBLISH: _MOVED[_PUBLISH],
        _CI: _BASE[_CI].replace(f"{_SHA} # v7.0.1", f"{_LATER} # v7.1.0")})
    assert check(base, head, "dependabot/actions")[:2] == (
        0, verdict("dependabot/actions", 0, 0) + "\n")


@pytest.mark.parametrize(("change", "paths", "why"), [
    ({"uv.lock": _BASE["uv.lock"].replace("12.3.0", "13.0.0")}, "uv.lock",
     "uv.lock moves pillow from 12.3.0 to 13.0.0, a new major version"),
    ({"docker-compose.yml": _BASE["docker-compose.yml"].replace(
        f"18-alpine@sha256:{_OLD}", f"19-alpine@sha256:{_NEW}")}, "docker-compose.yml",
     "docker-compose.yml moves postgres from 18 to 19, a new major version"),
    ({_PUBLISH: _BASE[_PUBLISH].replace(f"{_SHA} # v7.0.1", f"{_LATER} # v8.0.0")}, _PUBLISH,
     f"{_PUBLISH} moves actions/checkout from 7.0.1 to 8.0.0, a new major version"),
    ({"uv.lock": _MOVED["uv.lock"],
      "pyproject.toml": _BASE["pyproject.toml"].replace("<9.0", "<10.0")},
     "pyproject.toml, uv.lock", "pyproject.toml pins no version"),
    ({"Dockerfile": _MOVED["Dockerfile"].replace("--locked", "--frozen")}, "Dockerfile",
     "Dockerfile changes line 2, which pins no version by the version in its image's tag and "
     "its digest"),
    ({"docker-compose.yml": _BASE["docker-compose.yml"].replace(
        f"postgres:18-alpine@sha256:{_OLD}", f"evil/postgres:18-alpine@sha256:{_NEW}")},
     "docker-compose.yml", "docker-compose.yml changes more than the version in its image's tag "
     "and its digest on line 3"),
    ({_PUBLISH: _MOVED[_PUBLISH].replace("jobs:", "on: push\njobs:")}, _PUBLISH,
     f"{_PUBLISH} adds or deletes a line"),
    ({"uv.lock": _MOVED["uv.lock"], "app.py": "a money fix\n"}, "app.py, uv.lock",
     "app.py pins no version"),
    ({"uv.lock": _MOVED["uv.lock"], task_file(9): None}, "uv.lock",
     "it changes plan/tasks/9 too, a plan record"),
], ids=["a lock major", "an image major", "an action major", "pyproject.toml beside the lock",
        "another Dockerfile edit", "a digest moved with the image's name",
        "another publish edit", "an app edit beside the lock", "a plan record beside the lock"])
def test_a_change_moving_more_than_versions_is_refused_saying_what_more(check, pinned, change,
                                                                       paths, why):
    """A major (an indirect package's, an image's, an action's), ``pyproject.toml`` beside
    the lock, a version line with another edit to its file, a digest moved with its image's
    name, an app edit, or a plan record changed beside the versions: each still needs a
    card, and the refusal says what more the change does.  The tracker is not read."""
    root, base = pinned
    head = commit_files(root, base, "chore(deps): versions", change)
    status, out, _ = check(base, head, "dependabot/versions")
    tasks = sum(1 for path in change if path.startswith("plan/tasks/"))
    assert (status, out.splitlines()) == (1, [
        _refused("dependabot/versions", paths, why),
        verdict("dependabot/versions", 0, 1, tasks=tasks)])


def test_every_reason_is_named_up_to_five(check, pinned):
    """A card-less change doing more than versions in six ways names five, then counts the
    rest, as the paths are counted."""
    root, base = pinned
    head = commit_files(root, base, "much", {
        "uv.lock": _MOVED["uv.lock"], **{f"app/{n}.py": "new\n" for n in range(6)}})
    status, out, _ = check(base, head, "fix/much")
    assert (status, out.splitlines()) == (1, [
        _refused("fix/much", "app/0.py, app/1.py, app/2.py, app/3.py, app/4.py and 2 more",
                 "app/0.py pins no version; app/1.py pins no version; app/2.py pins no "
                 "version; app/3.py pins no version; app/4.py pins no version and 1 more"),
        verdict("fix/much", 0, 1)])


@pytest.mark.parametrize("lock", [_MOVED["uv.lock"], _BASE["uv.lock"].replace("12.3.0", "13.0.0")],
                         ids=["a minor", "a major"])
def test_a_cards_claim_carrying_a_version_move_lands_by_its_marker(check, pinned, lock):
    """Card 212's claim moves the lock, within its major or across one, with its marker:
    rule 9 passes it by the marker, as it passes any claim's change, and a major lands this
    way, on a card.  The exemption's "writes no plan record" never refuses a claim's change
    (the developer's ruling of 2026-10-30, at plan#2352)."""
    root, base = pinned
    _, tip = claim(root, base, 212)
    work = commit_files(root, tip, "the move", {"uv.lock": lock})
    github = Lookup(tracker(card_node(212)))
    assert check(base, work, "card/212", github=github)[:2] == (
        0, verdict("card/212", 1, 0) + "\n")
