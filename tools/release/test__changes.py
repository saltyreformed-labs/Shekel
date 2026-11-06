"""What a release changes of what production runs, and each pull request's own part of it
(:mod:`tools.release._changes`), over throwaway repositories under tmp_path: by CI's write
rule's reading of production, never a list of the skill's.  No network, no docker, never a
real checkout."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run
from tools.quill.door import IgnoreFileError, NotVersions, versions_waiting
from tools.release._changes import Change, Changes, Moved, changes
from tools.release.conftest import lock, pull_request

#: An ignore file leaving out itself, the documentation, the workflows, the scripts, the
#: deploy directory, the compose files and every environment file but the template, as this
#: repository's own does: what production runs from outside the image is left out of it.
IGNORE = ".dockerignore\ndocs\n.github\nscripts\ndeploy\ndocker-compose*\n.env*\n!.env.example\n"
MERGE = "Merge pull request"


def production(repo):
    """A base holding a file of each kind production's reading sorts: in the image or left
    out by the ignore file, and outside it, building or running production or not."""
    return repo.commit("base", {
        ".dockerignore": IGNORE, "app.py": "0", "docs/a.md": "0", ".env.example": "0",
        ".env.local": "0", "docker-compose.yml": "0", "docker-compose.dev.yml": "0",
        "deploy/shekel-deploy.sh": "0", "deploy/README.md": "0",
        ".github/workflows/docker-publish.yml": "0", ".github/workflows/ci.yml": "0",
        "scripts/_container_lib.sh": "0", "scripts/tool.py": "0", "gunicorn.conf.py": "0"})


def test_each_pull_request_is_named_for_its_own_change_to_what_production_runs(repo):
    """Five pull requests, newest first: each names exactly the paths its own merge changes
    of production, and one changing none names none.  The compose file, the environment
    template, the deploy helper and the publish workflow are production's, which the
    skill's old list (the ignore file's header, the Dockerfile, deploy/, every workflow)
    named only in part; the dev compose file, the test workflow, a README under deploy/ and
    a file the ignore file leaves out are not."""
    base = production(repo)
    image = pull_request(repo, base, files={"app.py": "1", "docs/a.md": "1"})
    nothing = pull_request(repo, image, files={
        "docs/a.md": "2", ".env.local": "1", "docker-compose.dev.yml": "1",
        "deploy/README.md": "1", ".github/workflows/ci.yml": "1", "scripts/tool.py": "1"})
    template = pull_request(repo, nothing, files={".env.example": "1"})
    outside = pull_request(repo, template, files={
        "docker-compose.yml": "1", "deploy/shekel-deploy.sh": "1",
        "scripts/_container_lib.sh": "1"})
    publish = pull_request(repo, outside, files={".github/workflows/docker-publish.yml": "1"})
    assert changes(repo.root, base, publish) == Changes(
        (".env.example", ".github/workflows/docker-publish.yml", "app.py",
         "deploy/shekel-deploy.sh", "docker-compose.yml", "scripts/_container_lib.sh"),
        (Change(publish, MERGE, (".github/workflows/docker-publish.yml",)),
         Change(outside, MERGE,
                ("deploy/shekel-deploy.sh", "docker-compose.yml", "scripts/_container_lib.sh")),
         Change(template, MERGE, (".env.example",)),
         Change(nothing, MERGE, ()),
         Change(image, MERGE, ("app.py",))))


def test_an_ignore_file_edit_is_named_for_the_files_it_moves(repo):
    """An edit to the ignore file, which the image leaves out, changes what production runs
    by the files it moves into the image: it is named for those, never for itself."""
    base = production(repo)
    cut = pull_request(repo, base,
                       files={".dockerignore": IGNORE.replace("scripts\n", "")})
    assert changes(repo.root, base, cut) == Changes(
        ("scripts/tool.py",), (Change(cut, MERGE, ("scripts/tool.py",)),))


def test_a_merge_is_read_against_its_first_parent_never_its_branch(repo):
    """dev took #1 (``app.py``) while #2's branch, built before it, changed
    ``gunicorn.conf.py``: #2's merge holds both, and names only its own change, never the
    change #1 brought, which its branch lacks."""
    base = repo.commit("base", {"app.py": "0", "gunicorn.conf.py": "0"})
    first = pull_request(repo, base, files={"app.py": "1"})
    branch = repo.commit("work", {"gunicorn.conf.py": "1"}, parents=(base,))
    second = repo.commit(MERGE, {"app.py": "1", "gunicorn.conf.py": "1"},
                         parents=(first, branch))
    assert changes(repo.root, base, second).pulls == (
        Change(second, MERGE, ("gunicorn.conf.py",)), Change(first, MERGE, ("app.py",)))


def _hotfixed(repo):
    """``main`` took a hotfix to the deploy script and ``gunicorn.conf.py`` while ``dev``
    took a change to ``app.py``: ``(main, dev's merge)``, ``main`` being what production
    runs."""
    start = production(repo)
    hotfix = pull_request(repo, start, files={"deploy/shekel-deploy.sh": "1",
                                              "gunicorn.conf.py": "1"})
    return hotfix, pull_request(repo, start, files={"app.py": "1"})


def test_a_resync_names_nothing_of_a_hotfix_production_already_runs(repo):
    """``main`` resynced into ``dev`` before the release: the resync's own change holds the
    hotfix, which production already runs, so the release changes ``app.py`` alone and the
    resync is named for nothing."""
    main, work = _hotfixed(repo)
    resync = repo.commit("Merge main into dev", {"deploy/shekel-deploy.sh": "1",
                                                 "gunicorn.conf.py": "1"},
                         parents=(work, main))
    found = changes(repo.root, main, resync)
    assert found == Changes(("app.py",), (Change(resync, "Merge main into dev", ()),
                                          Change(work, MERGE, ("app.py",))))
    assert not found.unowned


def test_a_change_no_pull_requests_own_change_made_is_named_apart(repo):
    """A resync whose resolution kept ``dev``'s ``gunicorn.conf.py``: the release takes the
    hotfix to it out of production, though no pull request's own change touches it, so the
    sum of their changes would miss it; it is named apart."""
    main, work = _hotfixed(repo)
    resync = repo.commit("Merge main into dev", {"deploy/shekel-deploy.sh": "1"},
                         parents=(work, main))
    found = changes(repo.root, main, resync)
    assert found == Changes(("app.py", "gunicorn.conf.py"),
                            (Change(resync, "Merge main into dev", ()),
                             Change(work, MERGE, ("app.py",))))
    assert found.unowned == ("gunicorn.conf.py",)


def test_an_ignore_file_docker_refuses_refuses_the_listing(repo):
    """A pull request writing an ignore file Docker refuses builds no image: the listing
    refuses, never naming the release's changes as none."""
    base = production(repo)
    cut = pull_request(repo, base, files={".dockerignore": "!\n"})
    with pytest.raises(IgnoreFileError):
        changes(repo.root, base, cut)


# -- the versions a release moves -----------------------------------------------------------

#: Two digests and two commits, each one side's.
_OLD, _NEW, _SHA, _LATER = "a" * 64, "b" * 64, "1" * 40, "2" * 40
#: A Dockerfile pinning uv and Python by tag and digest, Python twice, as the repository's
#: does.
DOCKERFILE = (f"FROM ghcr.io/astral-sh/uv:0.13.6@sha256:{_OLD} AS uv\n"
              f"FROM python:3.14-slim@sha256:{_OLD} AS builder\n"
              f"FROM python:3.14-slim@sha256:{_OLD}\nCOPY . .\n")
COMPOSE = f"services:\n  db:\n    image: postgres:18-alpine@sha256:{_OLD}\n"
PUBLISH = f"jobs:\n  build:\n    steps:\n      - uses: actions/checkout@{_SHA} # v7.0.1\n"


def pinning(repo):
    """A base holding each file that pins versions, among the files production runs."""
    return repo.commit("base", {
        ".dockerignore": IGNORE, "app.py": "0", "Dockerfile": DOCKERFILE,
        "docker-compose.yml": COMPOSE, ".github/workflows/docker-publish.yml": PUBLISH,
        "uv.lock": lock(("flask", "3.1.0"), ("zeta", "0.6.3"))})


def test_each_version_a_release_moves_is_named_from_the_base_to_the_cut(repo):
    """An update moving versions alone, then a card's own change to the Dockerfile and the
    lock, more than versions: the release names each version moved from the base's to the
    cut's, by file and then what it pins, a package added or removed against none, and
    never the app's code."""
    base = pinning(repo)
    update = pull_request(repo, base, files={
        "uv.lock": lock(("flask", "3.1.1"), ("zeta", "0.6.3")),
        "docker-compose.yml": COMPOSE.replace(f"18-alpine@sha256:{_OLD}",
                                              f"18.7-alpine@sha256:{_NEW}"),
        ".github/workflows/docker-publish.yml": PUBLISH.replace(
            f"{_SHA} # v7.0.1", f"{_LATER} # v7.1.0")})
    card = pull_request(repo, update, files={
        "app.py": "1",
        "Dockerfile": DOCKERFILE.replace(f"uv:0.13.6@sha256:{_OLD}", f"uv:0.13.9@sha256:{_NEW}")
        + "RUN uv sync --locked\n",
        "uv.lock": lock(("flask", "3.1.1"), ("olefile", "0.47"))})
    assert changes(repo.root, base, card).versions == (
        Moved(".github/workflows/docker-publish.yml", "actions/checkout",
              (f"{_SHA} # v7.0.1",), (f"{_LATER} # v7.1.0",)),
        Moved("Dockerfile", "ghcr.io/astral-sh/uv",
              (f"0.13.6@sha256:{_OLD}",), (f"0.13.9@sha256:{_NEW}",)),
        Moved("docker-compose.yml", "postgres",
              (f"18-alpine@sha256:{_OLD}",), (f"18.7-alpine@sha256:{_NEW}",)),
        Moved("uv.lock", "flask", ("3.1.0",), ("3.1.1",)),
        Moved("uv.lock", "olefile", (), ("0.47",)),
        Moved("uv.lock", "zeta", ("0.6.3",), ()))


def test_what_is_pinned_at_more_than_one_version_is_named_with_each(repo):
    """One of the Dockerfile's two Python lines moved, and a lock forked by environment
    holding zeta twice: each side's versions of what moved are named, in order."""
    base = pinning(repo)
    cut = pull_request(repo, base, files={
        "Dockerfile": DOCKERFILE.replace(f"{_OLD}\nCOPY", f"{_NEW}\nCOPY"),
        "uv.lock": lock(("flask", "3.1.0"), ("zeta", "0.6.3"), ("zeta", "0.7.0"))})
    assert changes(repo.root, base, cut).versions == (
        Moved("Dockerfile", "python", (f"3.14-slim@sha256:{_OLD}",),
              (f"3.14-slim@sha256:{_OLD}", f"3.14-slim@sha256:{_NEW}")),
        Moved("uv.lock", "zeta", ("0.6.3",), ("0.6.3", "0.7.0")))


def test_a_pinning_file_entering_or_leaving_production_names_each_of_its_pins(repo):
    """The publish workflow deleted and a compose file brought in: each pin of either is
    named against none on the side that holds no such file."""
    base = repo.commit("base", {".dockerignore": IGNORE, "app.py": "0",
                                ".github/workflows/docker-publish.yml": PUBLISH})
    cut = pull_request(repo, base, files={".github/workflows/docker-publish.yml": None,
                                          "docker-compose.yml": COMPOSE})
    assert changes(repo.root, base, cut).versions == (
        Moved(".github/workflows/docker-publish.yml", "actions/checkout",
              (f"{_SHA} # v7.0.1",), ()),
        Moved("docker-compose.yml", "postgres", (), (f"18-alpine@sha256:{_OLD}",)))


def test_a_pinning_file_changed_in_no_pin_moves_no_version(repo):
    """A card's edit to the Dockerfile and the lock's header changes production and moves no
    version: the release names the files and no version."""
    base = pinning(repo)
    cut = pull_request(repo, base, files={
        "Dockerfile": DOCKERFILE + "RUN uv sync --locked\n",
        "uv.lock": lock(("flask", "3.1.0"), ("zeta", "0.6.3")).replace("revision = 5",
                                                                        "revision = 6")})
    found = changes(repo.root, base, cut)
    assert (found.net, found.versions) == (("Dockerfile", "uv.lock"), ())


def test_a_lock_no_pin_of_which_can_be_named_refuses_the_listing(repo):
    """A cut whose lock is no TOML names no version, never one moved as none."""
    base = pinning(repo)
    cut = pull_request(repo, base, files={"uv.lock": "[[package\n"})
    with pytest.raises(NotVersions):
        changes(repo.root, base, cut)


# -- the versions a release moves outside production -----------------------------------------

UPDATES = ".github/workflows/updates.yml"
DEV_COMPOSE = "docker-compose.dev.yml"
TOKEN = (f"jobs:\n  mint:\n    steps:\n      - uses: actions/create-github-app-token@{_SHA}"
         " # v3.0.0\n")
TOKEN_MOVED = TOKEN.replace(f"{_SHA} # v3.0.0", f"{_LATER} # v3.1.0")
TOKEN_MOVE = Moved(UPDATES, "actions/create-github-app-token",
                   (f"{_SHA} # v3.0.0",), (f"{_LATER} # v3.1.0",))
#: The publish workflow's and a compose file's one pin, each moved.
PUBLISH_MOVED = PUBLISH.replace(f"{_SHA} # v7.0.1", f"{_LATER} # v7.1.0")
COMPOSE_MOVED = COMPOSE.replace(f"18-alpine@sha256:{_OLD}", f"18.7-alpine@sha256:{_NEW}")


def beside(repo, ignore=IGNORE):
    """:func:`pinning`'s base with what pins versions outside production beside it: a workflow
    only it runs, and the development compose file, read by the ignore file ``ignore``."""
    return repo.commit("base", {".dockerignore": ignore, UPDATES: TOKEN, DEV_COMPOSE: COMPOSE},
                       parents=(pinning(repo),))


def _waiting(repo, main, dev):
    """What the release-due issue lists with ``main`` and ``dev`` at those commits."""
    run(repo.root, "update-ref", "refs/remotes/origin/main", main)
    run(repo.root, "update-ref", "refs/remotes/origin/dev", dev)
    return versions_waiting(repo.root)


def test_a_move_only_outside_production_is_named_apart_with_nothing_of_production(repo):
    """A week moving only the action that handles the update App's key: production's files
    change in nothing, so the first list is empty and the move is in the second."""
    base = beside(repo)
    cut = pull_request(repo, base, files={UPDATES: TOKEN_MOVED})
    assert changes(repo.root, base, cut) == Changes(
        (), (Change(cut, MERGE, ()),), (), (TOKEN_MOVE,))


def test_a_move_inside_production_is_named_under_it_alone(repo):
    """A move in the lock and the publish workflow is production's: the second list is empty,
    with other files that pin versions beside them unmoved."""
    base = beside(repo)
    cut = pull_request(repo, base, files={
        "uv.lock": lock(("flask", "3.1.1"), ("zeta", "0.6.3")),
        ".github/workflows/docker-publish.yml": PUBLISH_MOVED})
    found = changes(repo.root, base, cut)
    assert found.versions == (
        Moved(".github/workflows/docker-publish.yml", "actions/checkout",
              (f"{_SHA} # v7.0.1",), (f"{_LATER} # v7.1.0",)),
        Moved("uv.lock", "flask", ("3.1.0",), ("3.1.1",)))
    assert not found.outside


def test_a_mixed_release_names_each_move_once_under_its_files_list(repo):
    """One image at one version moving in the production compose file and the development one
    is two moves, one in each list, and an action outside production beside them is in the
    second alone: no move is named in both, and none is dropped."""
    base = beside(repo)
    cut = pull_request(repo, base, files={"docker-compose.yml": COMPOSE_MOVED,
                                          DEV_COMPOSE: COMPOSE_MOVED,
                                          UPDATES: TOKEN_MOVED})
    moved = (f"18-alpine@sha256:{_OLD}",), (f"18.7-alpine@sha256:{_NEW}",)
    found = changes(repo.root, base, cut)
    assert found.versions == (Moved("docker-compose.yml", "postgres", *moved),)
    assert found.outside == (TOKEN_MOVE, Moved(DEV_COMPOSE, "postgres", *moved))


def test_a_release_moving_no_version_names_none_in_either_list(repo):
    """A change to the app's code, and to what a pinning file outside production holds beyond
    its pins: every file that pins versions is still as it was in what it pins."""
    base = beside(repo)
    cut = pull_request(repo, base, files={"app.py": "1", UPDATES: TOKEN + "# a comment\n"})
    found = changes(repo.root, base, cut)
    assert (found.net, found.versions, found.outside) == (("app.py",), (), ())


def test_the_two_lists_together_are_what_the_release_due_issue_names(repo):
    """The disagreement this list closes: the issue asks of the whole tree and the approval of
    production and the rest, and over files that stay on their side of production's line they
    name the same moves, none twice."""
    base = beside(repo)
    cut = pull_request(repo, base, files={"docker-compose.yml": COMPOSE_MOVED,
                                          DEV_COMPOSE: COMPOSE_MOVED,
                                          UPDATES: TOKEN_MOVED,
                                          "uv.lock": lock(("flask", "3.1.1"), ("zeta", "0.6.3"))})
    found = changes(repo.root, base, cut)
    assert len(found.versions) == 2
    assert len(found.outside) == 2
    assert sorted(found.versions + found.outside, key=str) == sorted(_waiting(repo, base, cut),
                                                                     key=str)


def test_a_workflow_added_or_deleted_outside_production_is_named_against_none(repo):
    """The first release brings the update workflow, and a later one may delete a workflow:
    each pin of either is named in the second list, against none on the side with no such
    file."""
    base = repo.commit("base", {".dockerignore": IGNORE, "app.py": "0"})
    added = pull_request(repo, base, files={UPDATES: TOKEN})
    token = "actions/create-github-app-token", f"{_SHA} # v3.0.0"
    assert changes(repo.root, base, added).outside == (
        Moved(UPDATES, token[0], (), (token[1],)),)
    deleted = pull_request(repo, added, files={UPDATES: None})
    found = changes(repo.root, added, deleted)
    assert found.outside == (Moved(UPDATES, token[0], (token[1],), ()),)
    assert not found.net


def test_an_action_the_publish_workflow_does_not_load_is_outside_production(repo):
    """This repository's own action, loaded by no publish step, is no file production runs: a
    move in it is in the second list."""
    action = ".github/actions/other/action.yml"
    base = repo.commit("base", {".dockerignore": IGNORE, "app.py": "0", action: TOKEN})
    cut = pull_request(repo, base, files={action: TOKEN_MOVED})
    found = changes(repo.root, base, cut)
    assert found.outside == (Moved(action, TOKEN_MOVE.what, TOKEN_MOVE.old, TOKEN_MOVE.new),)
    assert not found.versions


#: The development compose file's pin at each side, as a crossing file names it.
_DEV_OLD, _DEV_NEW = f"18-alpine@sha256:{_OLD}", f"18.7-alpine@sha256:{_NEW}"
NO_COMPOSE = IGNORE.replace("docker-compose*\n", "")


def test_a_file_entering_production_with_its_pin_moved_is_named_on_both_lists(repo):
    """An ignore-file edit brings the development compose file into the image as its pin moves:
    the version it leaves is named outside and the version it brings under production."""
    base = beside(repo)
    cut = pull_request(repo, base, files={".dockerignore": NO_COMPOSE,
                                          DEV_COMPOSE: COMPOSE_MOVED})
    found = changes(repo.root, base, cut)
    assert found.versions == (Moved(DEV_COMPOSE, "postgres", (), (_DEV_NEW,)),)
    assert found.outside == (Moved(DEV_COMPOSE, "postgres", (_DEV_OLD,), ()),)


def test_a_file_leaving_production_with_its_pin_moved_is_named_on_both_lists(repo):
    """The reverse: the cut still pins it, at a new version, so that version is named outside
    and never only ``-> none``, which would read as removed and name the new version nowhere."""
    base = beside(repo, NO_COMPOSE)
    cut = pull_request(repo, base, files={".dockerignore": IGNORE, DEV_COMPOSE: COMPOSE_MOVED})
    found = changes(repo.root, base, cut)
    assert found.versions == (Moved(DEV_COMPOSE, "postgres", (_DEV_OLD,), ()),)
    assert found.outside == (Moved(DEV_COMPOSE, "postgres", (), (_DEV_NEW,)),)
    assert _waiting(repo, base, cut) == (
        Moved(DEV_COMPOSE, "postgres", (_DEV_OLD,), (_DEV_NEW,)),)


def test_an_action_the_publish_workflow_stops_loading_is_named_on_both_lists(repo):
    """The shape of the repository's own actions, each loaded by more than one workflow: the
    publish workflow stops loading one that another keeps, as its pin moves.  Which files are
    production's is each commit's own workflow's to say, with no ignore-file edit."""
    action = ".github/actions/build/action.yml"
    loads = PUBLISH + "      - uses: ./.github/actions/build\n"
    base = repo.commit("base", {".dockerignore": IGNORE, "app.py": "0", action: TOKEN,
                                ".github/workflows/docker-publish.yml": loads})
    cut = pull_request(repo, base, files={action: TOKEN_MOVED,
                                          ".github/workflows/docker-publish.yml": PUBLISH})
    found = changes(repo.root, base, cut)
    assert found.versions == (Moved(action, TOKEN_MOVE.what, TOKEN_MOVE.old, ()),)
    assert found.outside == (Moved(action, TOKEN_MOVE.what, (), TOKEN_MOVE.new),)


def test_a_file_crossing_production_with_its_pin_unmoved_is_named_on_both_lists(repo):
    """No version moved, so the issue names nothing; what production pins and what the rest pins
    each still differ, and the approval says so, with the one version on both lines."""
    base = beside(repo)
    cut = pull_request(repo, base, files={".dockerignore": NO_COMPOSE})
    found = changes(repo.root, base, cut)
    assert found.versions == (Moved(DEV_COMPOSE, "postgres", (), (_DEV_OLD,)),)
    assert found.outside == (Moved(DEV_COMPOSE, "postgres", (_DEV_OLD,), ()),)
    assert not _waiting(repo, base, cut)


def test_a_lock_outside_production_no_pin_of_which_can_be_named_refuses_the_listing(repo):
    """A pin the listing cannot read refuses it wherever the file sits: never a release moving
    no version outside production because the one that moved could not be read."""
    base = repo.commit("base", {".dockerignore": IGNORE + "uv.lock\n", "app.py": "0",
                                "uv.lock": lock(("flask", "3.1.0"))})
    readable = pull_request(repo, base, files={"uv.lock": lock(("flask", "3.1.1"))})
    assert changes(repo.root, base, readable).outside == (
        Moved("uv.lock", "flask", ("3.1.0",), ("3.1.1",)),)
    cut = pull_request(repo, base, files={"uv.lock": "[[package\n"})
    with pytest.raises(NotVersions):
        changes(repo.root, base, cut)
