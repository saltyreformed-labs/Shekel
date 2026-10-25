"""CI's landing check, rule 9 (rulings ``balance:R-BAL362``, ``R-BAL364``, ``R-BAL400`` and
``R-BAL401``): a change to what production runs must add, change or delete a ship marker.

Every history is a throwaway repository's, built and judged as the other landing tests build
and judge theirs (:mod:`tools.quill._histories`), on a base whose ignore file keeps the
markers, ``tools/``, ``docs/``, the ``Dockerfile``, ``deploy/`` and ``.github/`` out of the
image, as the repository's own does (:func:`_image`); the files the image holds are read by
:mod:`tools.quill._production`, graded on its own in ``test__production.py``.  The other
landing tests' base keeps everything out of the image, so there each test grades rules 1-8
alone.
"""
from __future__ import annotations

import re

import pytest

from tools.ci.scratch import commit_files, point_dev, point_main
from tools.quill._git import marker
from tools.quill._histories import card_node, claim, judging, landed, never, reader, start
from tools.quill._histories import tracker, undone, verdict
from tools.quill._histories import Lookup

#: The base's ignore file: what the image leaves out, as the repository's own leaves it out.
_IGNORED = "plan/\ndocs/\ntools/\nDockerfile\n.dockerignore\ndeploy/\n.github/\n"
#: The workflow that builds and signs the image (R-BAL401), by its path.
_PUBLISH = ".github/workflows/docker-publish.yml"


def _refused(head, paths):
    """Rule 9's sentence for a change from ``head`` to production's ``paths``, as named."""
    return (f"REFUSED -- {head} changes what production runs ({paths}), but adds, changes or "
            "deletes no ship marker, so no release would count it: a change to a file the "
            "production image holds, the Dockerfile, docker-compose.yml, deploy/ (a "
            "README.md there excepted) or .github/workflows/docker-publish.yml lands from a "
            "card's claim (quill claim <card>), whose marker records it")


@pytest.fixture(name="image")
def _image(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base (the start of
    every history) with :data:`_IGNORED` for its ignore file, an app file the image holds,
    and the Dockerfile, a deploy script, deploy's README, the publish workflow, another
    workflow, a tool and a document it does not; the repository and the base."""
    root, start_ = start(tmp_path)
    base = commit_files(root, start_, "the image", {
        ".dockerignore": _IGNORED, "Dockerfile": "FROM python\n", "deploy/run.sh": "deploy\n",
        "deploy/README.md": "how\n", _PUBLISH: "on: push\n",
        ".github/workflows/ci.yml": "on: pull_request\n", "tools/t.py": "tool\n",
        "docs/d.md": "doc\n"})
    point_dev(root, base)
    point_main(root, base)
    return root, base


@pytest.fixture(name="check")
def _check(capsys, image):
    """The check over pull request #600 of branch ``head`` at ``head_sha`` into ``base``,
    whose tip is ``onto`` (:func:`_histories.judging`), reading the tracker through
    ``github`` (no read allowed when None); its exit status, output and error."""
    judged = judging(capsys, image[0])

    def _run(onto, head_sha, head, *, base="dev", github=None):
        """Judge pull request #600 of ``head`` into ``base`` (the fixture)."""
        return judged(onto, head_sha, head, base, reader(github) if github else never)

    return _run


@pytest.mark.parametrize(("change", "paths"), [
    ({"app.py": "a money fix\n"}, "app.py"),
    ({"app.py": None}, "app.py"),
    ({"Dockerfile": "FROM python:slim\n"}, "Dockerfile"),
    ({"deploy/run.sh": "deploy twice\n"}, "deploy/run.sh"),
    ({_PUBLISH: "on: [push, workflow_dispatch]\n"}, _PUBLISH),
    ({_PUBLISH: "# builds the image\non: push\n"}, _PUBLISH),
    ({".dockerignore": _IGNORED.replace("tools/\n", "")}, "tools/t.py"),
    ({f"app/{n}.py": "new\n" for n in range(7)},
     "app/0.py, app/1.py, app/2.py, app/3.py, app/4.py and 2 more"),
], ids=["an app edit", "an app delete", "the Dockerfile", "the deploy script",
        "the publish workflow", "a comment in the publish workflow",
        "tools/ taken into the image", "seven files"])
def test_a_change_to_what_production_runs_writing_no_marker_is_refused(check, image, change,
                                                                       paths):
    """fix/grid-total, no card's claim, changes production: an image file edited or deleted,
    the Dockerfile, the deploy script or the publish workflow (read whole, a comment
    included, R-BAL401) although the image excludes them, or the ignore file taking a file
    into the image.  It is refused, naming at most five paths, and the tracker is not
    read."""
    root, base = image
    head = commit_files(root, base, "fix", change)
    status, out, _ = check(base, head, "fix/grid-total")
    assert (status, out.splitlines()) == (
        1, [_refused("fix/grid-total", paths), verdict("fix/grid-total", 0, 1)])


@pytest.mark.parametrize("change", [
    {"tools/t.py": "tool v2\n"}, {"docs/d.md": "doc v2\n"}, {"deploy/README.md": "how v2\n"},
    {".github/workflows/ci.yml": "on: merge_group\n"},
    {".dockerignore": f"# what the image leaves out\n{_IGNORED}"},
], ids=["a tool", "a document", "deploy's README", "another workflow",
        "an ignore-file comment"])
def test_a_change_production_never_runs_lands_without_a_marker(check, image, change):
    """A change to a tool, a document, deploy's README, a workflow that only tests or the
    ignore file's comments leaves production as it was, so it lands from any branch,
    unread."""
    root, base = image
    head = commit_files(root, base, "docs", change)
    assert check(base, head, "docs/typo")[:2] == (0, verdict("docs/typo", 0, 0) + "\n")


def test_the_cards_own_claim_changing_production_lands(check, image):
    """Card 212's claim writes its marker with its code, so rule 9 passes it; rules 1-8 judge
    the marker, and rule 7 reads card 212."""
    root, base = image
    _, tip = claim(root, base, 212)
    work = commit_files(root, tip, "the fix", {"app.py": "a money fix\n"})
    github = Lookup(tracker(card_node(212)))
    assert check(base, work, "card/212", github=github)[:2] == (
        0, verdict("card/212", 1, 0) + "\n")
    assert github.asked == [[212]]


def test_a_revert_deleting_the_marker_with_its_code_lands(check, image):
    """A revert of card 212's landing deletes its marker with its code: it un-records the
    card, so it lands from any branch, unread."""
    root, base = image
    shipped = landed(root, base, 212)
    point_dev(root, shipped)
    reverted = undone(root, shipped, 212)
    assert check(shipped, reverted, "revert-212")[:2] == (0, verdict("revert-212", 1, 0) + "\n")


def test_a_release_carrying_its_cards_markers_lands(check, image):
    """dev's cut brings card 212's code and its marker to main, carried through the merge
    that took the marker in (rule 4): rule 9 passes it, and the tracker is not read again."""
    root, base = image
    cut = landed(root, base, 212)
    point_dev(root, cut)
    assert check(base, cut, "release/2026-10-26", base="main")[:2] == (
        0, verdict("release/2026-10-26", 1, 0, base="main") + "\n")


def test_a_resync_bringing_mains_hotfix_lands(check, image):
    """main's hotfix 700, its marker with its code, brought back into dev passes: what it
    brings of production comes with the marker it carries."""
    root, base = image
    hotfix = landed(root, base, 700)
    point_main(root, hotfix)
    dev = commit_files(root, base, "dev's own work", {"docs/d.md": "dev\n"})
    assert check(dev, hotfix, "main")[:2] == (0, verdict("main", 1, 0) + "\n")


def test_a_release_of_code_whose_marker_dev_deleted_is_refused(check, image):
    """Card 212 lands on dev, and a later change deletes its marker but keeps its code (a
    person's "not done", which lands by rule 2).  A release of that cut changes production
    and records no card, so no release could count the code: it is refused, as from any
    branch (no release arm passes it)."""
    root, base = image
    shipped = landed(root, base, 212)
    kept = commit_files(root, shipped, "not done", {marker(212): None})
    point_dev(root, kept)
    status, out, _ = check(base, kept, "release/2026-10-26", base="main")
    assert (status, out.splitlines()) == (1, [
        _refused("release/2026-10-26", "w212.txt"),
        verdict("release/2026-10-26", 0, 1, base="main")])


def test_a_branch_of_another_repository_is_named_so(check, image):
    """A fork's pull request changing production is refused, its branch named as no claim."""
    root, base = image
    head = commit_files(root, base, "fork", {"app.py": "theirs\n"})
    status, out, _ = check(base, head, None)
    assert status == 1
    assert out.splitlines()[0] == _refused("a branch of another repository", "app.py")


def test_an_ignore_file_docker_refuses_is_not_judged(check, image):
    """A change whose ignore file Docker refuses builds no image, so nothing is judged (exit
    2), saying why."""
    root, base = image
    head = commit_files(root, base, "broken", {".dockerignore": "!\n", "app.py": "x\n"})
    status, out, err = check(base, head, "fix/ignore")
    assert (status, out) == (2, "")
    assert re.fullmatch(r"landing check: [0-9a-f]{12} could not be judged, so nothing was "
                        r'checked: a line "!" names no pattern, which Docker refuses\n', err)
