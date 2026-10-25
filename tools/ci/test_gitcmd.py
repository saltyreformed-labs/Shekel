"""Git run in ONE repository, the two integration branches by their full names, and the one
question of ancestry, read from throwaway repositories under pytest's tmp_path; no network.

:data:`gitcmd.DEV`, :data:`gitcmd.MAIN`, :func:`gitcmd.tracking` and :func:`gitcmd.is_ancestor`
moved here from ``tools/ci/trailers.py`` by step X-cx's S3b (ruling ``balance:R-BAL389``), with
their tests from ``test_trailers.py``.  The cases of the deleted card-trailer check
(``commit_trailers``) that held a tag or a local branch named like ``origin/dev`` from standing
in for it (the L9d delta review 2's LOW-2, and its fix's review, nit 2) are ported here onto
the name :func:`gitcmd.tracking` gives, which every reader of the two branches now asks.
"""
from __future__ import annotations

import pytest

from tools.ci import scratch
from tools.ci.gitcmd import DEV, MAIN, GitError, git, is_ancestor, run, tracking
from tools.ci.scratch import commit, point_dev, point_main
from tools.ci.scratch import run as _run


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    return scratch.repository(tmp_path)


def test_a_parent_and_the_commit_itself_are_ancestors_and_a_child_is_not(repo):
    """``older`` in ``newer``'s history: a parent is in its child's, a commit is in its own,
    and a child is not in its parent's."""
    base = commit(repo, "base")
    leaf = commit(repo, "leaf", parents=[base])
    assert is_ancestor(repo, base, leaf)
    assert is_ancestor(repo, leaf, leaf)
    assert not is_ancestor(repo, leaf, base)


def test_is_ancestor_raises_on_a_commit_git_does_not_have(repo):
    """Exit 128 is an error, never "not an ancestor"."""
    base = commit(repo, "base")
    with pytest.raises(GitError):
        is_ancestor(repo, "0" * 40, base)


@pytest.mark.parametrize("branch", [DEV, MAIN])
@pytest.mark.parametrize("kind", ["tag", "branch"])
def test_a_reference_named_like_dev_or_main_does_not_stand_in_for_it(repo, branch, kind):
    """Git resolves a short name to a tag or a local branch of that name before the
    remote-tracking branch, and CI's checkout fetches every tag, so one named ``origin/dev``
    (or ``origin/main``) at another commit would be read as the branch.  The full name
    :func:`tracking` gives reads the branch itself.  The control: the short name IS
    shadowed."""
    landed = commit(repo, "landed")
    elsewhere = commit(repo, "elsewhere", parents=[landed])
    point_dev(repo, landed)
    point_main(repo, landed)
    _run(repo, kind, branch, elsewhere)
    assert _run(repo, "rev-parse", branch) == elsewhere, "control: the short name is shadowed"
    assert _run(repo, "rev-parse", tracking(branch)) == landed


@pytest.mark.parametrize("full", [False, True], ids=["short name", "full name"])
@pytest.mark.parametrize("branch", [DEV, MAIN])
def test_a_tag_named_like_a_missing_branch_does_not_say_it_exists(repo, branch, full):
    """With the branch missing, git still resolves its full name to a tag named like it
    (``refs/tags/refs/remotes/origin/dev``), so whether the branch exists is asked of the
    exact reference (``show-ref --verify``, :func:`tracking`'s caution), which a tag of
    either name does not answer.  The control: that full name resolves, to the tag."""
    base = commit(repo, "base")
    _run(repo, "tag", tracking(branch) if full else branch, base)
    assert run(repo, "show-ref", "--verify", "--quiet", tracking(branch)).returncode == 1
    resolved = run(repo, "rev-parse", "--verify", "--quiet", tracking(branch))
    assert resolved.stdout.strip() == (base if full else ""), "control: a tag stands in"


# -- an environment bound to another repository reaches nothing but the directory named ----------

@pytest.mark.parametrize("binding", scratch.BINDINGS)
def test_a_bound_environment_reaches_no_repository_but_the_one_named(binding, tmp_path,
                                                                     monkeypatch):
    """The 2026-10-04 incident's mechanism replayed onto a sentinel: every write the tests'
    builder makes and every git call this module makes lands in the directory named, every
    answer is that directory's, and the sentinel -- its refs, config, HEADs and indexes --
    is byte-identical afterwards.  The sentinel is proven bound, and its indexes not empty
    (review cp3 M-1's control), before it is handed over (``scratch._prove_bound``).  The
    tracker tool's own calls are held the same way in ``tools/quill/test__git.py``."""
    sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
    work, leaf = scratch.leaf_on_dev(tmp_path)
    assert git(work, "rev-parse", "dev").strip() == leaf
    assert is_ancestor(work, leaf, leaf)

    assert scratch.sentinel_state(sentinel) == before
