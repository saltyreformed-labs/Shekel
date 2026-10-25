"""What git says shipped, read from throwaway repositories under pytest's tmp_path; no network.

Moved here with :mod:`tools.ci.trailers` from what is now
``tools/quill/test__git.py`` by step X-cx's L4; the tracker tool's own git calls
are tested there still.
"""
from __future__ import annotations

import pytest

from tools.ci import scratch
from tools.ci.gitcmd import GitError, git
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import point_dev as _dev
from tools.ci.scratch import run as _run
from tools.ci.trailers import DEV, History, cancels, history, is_ancestor, shipped


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    return scratch.repository(tmp_path)


def test_only_the_trailer_block_claims_a_card(repo):
    """A sentence naming plan#3 claims nothing; the last paragraph's trailers do."""
    base = _commit(repo, "base")
    prose = _commit(repo, "fix", "Ships: plan#3 was the wrong card to cite here.",
                    "Co-Authored-By: A <a@example.invalid>", parents=[base])
    leaf = _commit(repo, "leaf", "Why it changed.",
                   "Ships: plan#4\nShips: plan#5\nCo-Authored-By: A <a@example.invalid>",
                   parents=[prose])
    _dev(repo, leaf)
    found = history(repo)
    assert [(t.key, t.card) for t in found.trailers] == [("Ships", 4), ("Ships", 5)]
    assert {t.sha for t in found.trailers} == {leaf}
    assert set(shipped(repo, found)[0]) == {4, 5}


def test_the_old_form_is_left_to_the_plan_gate_and_anything_else_is_reported(repo):
    """``balance:X-1`` is the plan gate's until the cutover; every other value names nothing."""
    leaf = _commit(repo, "leaf", "Ships: balance:X-bi-6-4d-1\nShips: pay_calendar:C21\n"
                                 "Ships: plan #7\nShips: Plan#8\nShips: #9\nShips: pla#10\n"
                                 "Ships: banking:X-1\nReopens: plan#9a")
    _dev(repo, leaf)
    found = history(repo)
    assert not found.trailers
    assert [line.split(" ", 1)[1] for line in found.malformed] == [
        "Ships: 'plan #7' is not plan#<number>",
        "Ships: 'Plan#8' is not plan#<number>",
        "Ships: '#9' is not plan#<number>",
        "Ships: 'pla#10' is not plan#<number>",
        "Ships: 'banking:X-1' is not plan#<number>",
        "Reopens: 'plan#9a' is not plan#<number>",
    ]


def test_an_old_form_ships_is_returned_as_its_own_record_over_the_range_named(repo):
    """The release tool asks which steps ``base..cut`` carries in the old form: each
    ``Ships: <arc>:<id>`` is one record, with its commit, arc and id; an old-form
    ``Reopens:`` is one record of its own, never a claim (the card reader refuses one a cut
    holding steps.md holds, and reads any other as prose); a card trailer and a malformed
    value stay where they were; and
    a claim on ``base`` is outside the range."""
    base = _commit(repo, "base", "Ships: balance:X-bi-6-4d-1")
    leaf = _commit(repo, "leaf", "Ships: balance:X-bi-6-4d-2\nShips: pay_calendar:C21\n"
                                 "Ships: plan#4\nShips: banking:X-1\n"
                                 "Reopens: recurrence:R20", parents=[base])
    found = history(repo, f"{base}..{leaf}")
    assert [(c.sha, c.arc, c.ident, c.key) for c in found.old_form] == [
        (leaf, "balance", "X-bi-6-4d-2", "balance:X-bi-6-4d-2"),
        (leaf, "pay_calendar", "C21", "pay_calendar:C21"),
    ]
    assert [(c.sha, c.key, c.trailer) for c in found.old_form_reopens] == [
        (leaf, "recurrence:R20", "Reopens")]
    assert [(t.key, t.card) for t in found.trailers] == [("Ships", 4)]
    assert [line.split(" ", 1)[1] for line in found.malformed] == [
        "Ships: 'banking:X-1' is not plan#<number>"]
    assert [c.key for c in history(repo, leaf).old_form] == [
        "balance:X-bi-6-4d-2", "pay_calendar:C21", "balance:X-bi-6-4d-1"]


def test_a_history_built_from_its_two_card_fields_holds_no_old_form_claim():
    """``commit_trailers`` builds a History from trailers and malformed lines alone
    (``owed_at_landing``); the old-form fields default to empty, so that call is
    unchanged."""
    assert not History((), ()).old_form
    assert not History((), ()).old_form_reopens


def test_a_reopen_cancels_the_ships_it_was_built_on_and_a_reship_stands(repo):
    """Ships, Reopens, Ships again: shipped, by the last commit only."""
    first = _commit(repo, "ship", "Ships: plan#2")
    undo = _commit(repo, "revert", "Reopens: plan#2", parents=[first])
    _dev(repo, undo)
    assert shipped(repo, history(repo)) == ({}, ())
    again = _commit(repo, "ship again", "Ships: plan#2", parents=[undo])
    _dev(repo, again)
    standing, strays = shipped(repo, history(repo))
    assert [t.sha for t in standing[2]] == [again] and not strays


def test_a_ship_the_reopen_never_saw_still_stands(repo):
    """Ancestry, not date: a parallel branch's Ships merged after the Reopens stands."""
    base = _commit(repo, "base")
    first = _commit(repo, "ship", "Ships: plan#6", parents=[base])
    undo = _commit(repo, "revert", "Reopens: plan#6", parents=[first])
    parallel = _commit(repo, "ship elsewhere", "Ships: plan#6", parents=[base])
    merge = _commit(repo, "merge", parents=[undo, parallel])
    _dev(repo, merge)
    assert [t.sha for t in shipped(repo, history(repo))[0][6]] == [parallel]


def test_a_reopen_that_cancels_nothing_is_a_stray_and_the_card_stays_shipped(repo):
    """R-BAL181: a branch started before #7 shipped never held its code; its Reopens is
    reported, never acted on -- most likely a mistyped number."""
    base = _commit(repo, "base")
    ship = _commit(repo, "ship", "Ships: plan#7", parents=[base])
    early = _commit(repo, "fix", "Reopens: plan#7\nReopens: plan#71", parents=[base])
    _dev(repo, _commit(repo, "merge", parents=[ship, early]))
    standing, strays = shipped(repo, history(repo))
    assert [t.sha for t in standing[7]] == [ship]
    assert sorted((t.card, t.sha) for t in strays) == [(7, early), (71, early)]


def test_a_commit_reopening_two_cards_keeps_the_stray_of_the_one_that_cancels_nothing(repo):
    """BAL-608: the cancelling ``Reopens:`` were keyed by their commit, so a commit carrying
    ``Reopens: plan#4``, which cancels plan#4's ship, beside ``Reopens: plan#5``, which
    cancels nothing, hid plan#5's stray from ``quill show`` and ``quill sync``.  Each
    trailer is its own: plan#4 is reopened, plan#5's is the one stray.  The control:
    alone on the commit, ``Reopens: plan#5`` is a stray and plan#4 stays shipped."""
    ship = _commit(repo, "ship", "Ships: plan#4")
    both = _commit(repo, "revert", "Reopens: plan#4\nReopens: plan#5", parents=[ship])
    _dev(repo, both)
    standing, strays = shipped(repo, history(repo))
    assert not standing
    assert [(t.key, t.card, t.sha) for t in strays] == [("Reopens", 5, both)]
    alone = _commit(repo, "typo", "Reopens: plan#5", parents=[ship])
    _dev(repo, alone)
    standing, strays = shipped(repo, history(repo))
    assert [t.sha for t in standing[4]] == [ship]
    assert [(t.key, t.card, t.sha) for t in strays] == [("Reopens", 5, alone)]


def test_a_commit_that_ships_and_reopens_the_same_card_leaves_it_open(repo):
    """A commit is in its own history, so its Reopens cancels its own Ships."""
    both = _commit(repo, "confused", "Ships: plan#4\nReopens: plan#4")
    _dev(repo, both)
    assert shipped(repo, history(repo)) == ({}, ())


def test_a_reopen_cancels_only_its_own_card_and_only_what_it_was_built_on(repo):
    """:func:`cancels`, the one spelling of R-BAL181's test, which CI's ``commit_trailers``
    check also asks when it names the ``Reopens:`` that cancelled a ship."""
    ship = _commit(repo, "ship", "Ships: plan#4")
    undo = _commit(repo, "revert", "Reopens: plan#4\nReopens: plan#5", parents=[ship])
    _dev(repo, undo)
    (claim,) = [t for t in history(repo).trailers if t.key == "Ships"]
    four, five = sorted((t for t in history(repo).trailers if t.key == "Reopens"),
                        key=lambda t: t.card)
    assert cancels(repo, four, claim)
    assert not cancels(repo, five, claim)
    sibling = _commit(repo, "elsewhere", "Reopens: plan#4")
    _dev(repo, sibling)
    (stray,) = history(repo).trailers
    assert not cancels(repo, stray, claim)


def test_only_dev_is_read(repo):
    """A Ships trailer on a branch not yet merged into dev has shipped nothing."""
    base = _commit(repo, "base")
    _run(repo, "update-ref", "refs/heads/feature", _commit(repo, "leaf", "Ships: plan#3",
                                                           parents=[base]))
    _dev(repo, base)
    assert not history(repo).trailers
    assert [t.card for t in history(repo, "feature").trailers] == [3]


def test_a_range_reads_only_the_commits_it_names(repo):
    """``base..head`` is a change set: the commits ``head`` holds and ``base`` does not.

    CI's ``commit_trailers`` check reads a pull request's own commits this way,
    so a trailer already on ``base`` is not read again.
    """
    base = _commit(repo, "base", "Ships: plan#2")
    leaf = _commit(repo, "leaf", "Ships: plan#3", parents=[base])
    assert [(t.card, t.sha) for t in history(repo, f"{base}..{leaf}").trailers] == [(3, leaf)]
    assert {t.card for t in history(repo, leaf).trailers} == {2, 3}


def test_several_tips_are_read_as_one_history_each_commit_once(repo):
    """Two lines beside each other, each named: one walk reads both, and the commit they
    share once."""
    base = _commit(repo, "base", "Ships: plan#2")
    left = _commit(repo, "left", "Ships: plan#3", parents=[base])
    right = _commit(repo, "right", "Ships: plan#4", parents=[base])
    assert sorted(t.card for t in history(repo, left, right).trailers) == [2, 3, 4]


def test_a_shallow_clone_is_refused_not_read_as_nothing_shipped(repo, tmp_path):
    """A depth-1 clone holds no commit before its tip: every older ship would vanish."""
    base = _commit(repo, "base", "Ships: plan#19")
    _run(repo, "update-ref", "refs/heads/dev", _commit(repo, "tip", parents=[base]))
    shallow = tmp_path / "shallow"
    _run(tmp_path, "clone", "--quiet", "--depth=1", "--branch=dev", f"file://{repo}",
         str(shallow))
    with pytest.raises(GitError, match="shallow clone"):
        history(shallow)


def test_a_missing_branch_is_an_error_not_an_empty_history(repo):
    """An unfetched dev must not read as "nothing shipped"."""
    with pytest.raises(GitError, match="origin/dev"):
        history(repo, DEV)


def test_is_ancestor_raises_on_a_commit_git_does_not_have(repo):
    """Exit 128 is an error, never "not an ancestor"."""
    base = _commit(repo, "base")
    with pytest.raises(GitError):
        is_ancestor(repo, "0" * 40, base)


# -- an environment bound to another repository reaches nothing but the directory named ----------

@pytest.mark.parametrize("binding", scratch.BINDINGS)
def test_a_bound_environment_reaches_no_repository_but_the_one_named(binding, tmp_path,
                                                                     monkeypatch):
    """The 2026-10-04 incident's mechanism replayed onto a sentinel: every write the tests'
    builder makes and every git call this package makes lands in the directory named,
    every answer is that directory's, and the sentinel -- its refs, config, HEADs and
    indexes -- is byte-identical afterwards.  The sentinel is proven bound, and its
    indexes not empty (review cp3 M-1's control), before it is handed over
    (``scratch._prove_bound``).  The tracker tool's own calls are held the same way in
    ``tools/quill/test__git.py``."""
    sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
    work, leaf = scratch.leaf_on_dev(tmp_path)
    assert git(work, "rev-parse", "dev").strip() == leaf
    assert [(t.card, t.sha) for t in history(work, "dev").trailers] == [(3, leaf)]
    assert is_ancestor(work, leaf, leaf)

    assert scratch.sentinel_state(sentinel) == before
