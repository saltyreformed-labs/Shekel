"""What a release changes of what production runs, and each pull request's own part of it
(:mod:`tools.release._changes`), over throwaway repositories under tmp_path: by CI's write
rule's reading of production, never a list of the skill's.  No network, no docker, never a
real checkout."""
from __future__ import annotations

import pytest

from tools.quill.door import IgnoreFileError
from tools.release._changes import Change, Changes, changes
from tools.release.conftest import pull_request

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
