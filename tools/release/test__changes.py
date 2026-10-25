"""What each pull request a release carries changes of what production runs
(:mod:`tools.release._changes`), over throwaway repositories under tmp_path: each named for
its own change, by CI's write rule's reading of production, never a list of the skill's.
No network, no docker, never a real checkout."""
from __future__ import annotations

import pytest

from tools.quill.door import IgnoreFileError
from tools.release._changes import Change, changes
from tools.release.conftest import pull_request

#: An ignore file leaving out the documentation, the workflows, the scripts, the deploy
#: directory, the compose files and every environment file but the template, as this
#: repository's own does: what production runs from outside the image is left out of it.
IGNORE = "docs\n.github\nscripts\ndeploy\ndocker-compose*\n.env*\n!.env.example\n"


def production(repo):
    """A base holding a file of each kind production's reading sorts: in the image or left
    out by the ignore file, and outside it, building or running production or not."""
    return repo.commit("base", {
        ".dockerignore": IGNORE, "app.py": "0", "docs/a.md": "0", ".env.example": "0",
        ".env.local": "0", "docker-compose.yml": "0", "docker-compose.dev.yml": "0",
        "deploy/shekel-deploy.sh": "0", "deploy/README.md": "0",
        ".github/workflows/docker-publish.yml": "0", ".github/workflows/ci.yml": "0",
        "scripts/_container_lib.sh": "0", "scripts/tool.py": "0"})


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
    assert changes(repo.root, base, publish) == (
        Change(publish, "Merge pull request", (".github/workflows/docker-publish.yml",)),
        Change(outside, "Merge pull request",
               ("deploy/shekel-deploy.sh", "docker-compose.yml", "scripts/_container_lib.sh")),
        Change(template, "Merge pull request", (".env.example",)),
        Change(nothing, "Merge pull request", ()),
        Change(image, "Merge pull request", ("app.py",)))


def test_a_merge_is_read_against_its_first_parent_never_its_branch(repo):
    """dev took #1 (``app.py``) while #2's branch, built before it, changed
    ``gunicorn.conf.py``: #2's merge holds both, and names only its own change, never the
    change #1 brought, which its branch lacks."""
    base = repo.commit("base", {"app.py": "0", "gunicorn.conf.py": "0"})
    first = pull_request(repo, base, files={"app.py": "1"})
    branch = repo.commit("work", {"gunicorn.conf.py": "1"}, parents=(base,))
    second = repo.commit("Merge pull request", {"app.py": "1", "gunicorn.conf.py": "1"},
                         parents=(first, branch))
    assert changes(repo.root, base, second) == (
        Change(second, "Merge pull request", ("gunicorn.conf.py",)),
        Change(first, "Merge pull request", ("app.py",)))


def test_an_ignore_file_docker_refuses_refuses_the_listing(repo):
    """A pull request writing an ignore file Docker refuses builds no image: the listing
    refuses, never naming the release's changes as none."""
    base = production(repo)
    cut = pull_request(repo, base, files={".dockerignore": "!\n"})
    with pytest.raises(IgnoreFileError):
        changes(repo.root, base, cut)
