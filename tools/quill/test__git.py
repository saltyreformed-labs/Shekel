"""The tracker tool's own git calls, read from throwaway repositories under pytest's tmp_path.

What a card's trailers mean (``Ships:``, ``Reopens:``) moved with :mod:`tools.ci.trailers` to
``tools/ci/test_trailers.py`` (step X-cx's L4); quill reads none since the ship marker (X-cx's
M1, ruling ``balance:R-BAL359``).  The marker and the claim are read and written here against a
real bare ``origin`` (:func:`_origin`), so the pushes, the lease and the fetch run as git runs
them.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.gitcmd import GitError
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import commit_files as _files
from tools.ci.scratch import point_dev as _dev
from tools.ci.scratch import run as _run
from tools.quill import _git

#: When the tests' claims are made, and when a later one is.
_MON, _TUE = datetime(2026, 10, 19, 13, 0, tzinfo=UTC), datetime(2026, 10, 20, 9, 30, tzinfo=UTC)


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    return scratch.repository(tmp_path)


def _origin(work):
    """A bare ``origin`` for ``work`` (:func:`scratch.with_origin`), holding ``dev`` and
    ``main`` at one base commit that ``work`` has fetched; the base's sha."""
    base = _files(work, _commit(work, "root"), "base", {"README": "base\n"})
    scratch.with_origin(work, base)
    return base


def _push(work, sha, branch):
    """Point ``origin``'s ``branch`` at ``sha``, forced, as a merge or a push would."""
    _run(work, "push", "--quiet", "--force", "origin", f"{sha}:refs/heads/{branch}")


def _merge(work, tree_of, *parents, message="merge"):
    """A merge of ``parents`` whose tree is ``tree_of``'s, written as a resolution would
    write it, whatever its parents hold; its sha."""
    tree = _run(work, "rev-parse", f"{tree_of}^{{tree}}")
    return _run(work, "commit-tree", tree, *[arg for parent in parents for arg in ("-p", parent)],
                "-m", message)


def _deny(work, refused):
    """A ``pre-receive`` hook in ``work``'s bare ``origin`` refusing every push that writes
    a ref the shell test ``refused`` (over ``$new`` and ``$ref``) holds for."""
    hook = Path(_run(work, "remote", "get-url", "origin")) / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nwhile read old new ref; do\n"
                    f"  if {refused}; then echo refused >&2; exit 1; fi\ndone\n")
    hook.chmod(0o755)


def _marker(number, when="2026-10-19T13:00:00Z"):
    """``{plan/ships/<number>: its line}``, a marker as a claim writes it."""
    return {_git.marker(number): f"claimed {when}\n"}


def test_what_shipped_is_the_merge_of_dev_and_main(repo):
    """R-BAL359: a hotfix's marker on ``main`` counts before ``main`` is merged into ``dev``; a
    marker ``dev`` deleted (a revert) is unshipped before the release; anything under
    ``plan/ships/`` that is no card's marker is a HISTORY line, never a card."""
    base = _origin(repo)
    both = _files(repo, base, "five shipped", _marker(5))
    _push(repo, both, "dev")
    _push(repo, both, "main")
    dev = _files(repo, both, "revert five, ship seven, and two strays",
                 {_git.marker(5): None, **_marker(7),
                  "plan/ships/notes": "x\n", "plan/ships/8/inner": "x\n"})
    _push(repo, dev, "dev")
    _push(repo, _files(repo, both, "hotfix nine", _marker(9)), "main")
    _git.fetch(repo)
    found = _git.shipped(repo)
    assert found.cards == {7, 9}
    assert [line.split(" ")[0] for line in found.history] == ["plan/ships/8/inner",
                                                             "plan/ships/notes"]


def test_every_card_a_marker_ever_named_is_read_after_its_revert(repo):
    """``sync`` reopens a card the tool showed shipped once a revert deletes its marker, so
    the cards whose marker any commit on ``dev`` or ``main`` ever touched are read: a
    reverted one with them, an entry that is no card's marker never."""
    base = _origin(repo)
    shipped = _files(repo, base, "ship five and a stray", {**_marker(5),
                                                           "plan/ships/notes": "x\n"})
    _push(repo, _files(repo, shipped, "Revert five", {_git.marker(5): None}), "dev")
    _push(repo, _files(repo, base, "hotfix nine", _marker(9)), "main")
    _git.fetch(repo)
    assert _git.shipped(repo).cards == {9}
    assert _git.ever_marked(repo) == {5, 9}


def _shipped_then_reverted_then_merged_in(work, base):
    """M1's review H1 (a): card 5 lands on dev and is reverted there; card/9, which merged
    dev in to update itself, lands after.  The landing merge matches card/9's side on
    ``plan/ships/``, so git's default walk followed that side alone."""
    five = _files(work, base, "claim plan#5", {**_marker(5), "five": "x\n"})
    landed = _merge(work, five, base, five)
    nine = _files(work, base, "claim plan#9", _marker(9))
    reverted = _files(work, landed, "Revert plan#5", {_git.marker(5): None, "five": None})
    updated = _files(work, reverted, "nine's tree", _marker(9))
    side = _merge(work, updated, nine, reverted, message="Merge dev into card/9")
    _push(work, _merge(work, updated, reverted, side, message="Merge card/9"), "dev")
    return 5


def _hotfix_reverted_on_main_then_released(work, base):
    """H1 (b): hotfix 700 lands on main and is reverted there; a release of dev (shipping
    12) merges after."""
    hotfix = _files(work, base, "claim plan#700", _marker(700))
    reverted = _files(work, hotfix, "Revert plan#700", {_git.marker(700): None})
    dev = _files(work, base, "dev work", {"code": "d\n", **_marker(12)})
    _push(work, dev, "dev")
    _push(work, _merge(work, dev, reverted, dev, message="release"), "main")
    return 700


def _a_resolution_drops_a_side_marker(work, base):
    """H1 (c): a merge resolution keeps none of the side that added card 5's marker."""
    work_side = _files(work, base, "feature", {"code": "f\n"})
    five = _files(work, base, "claim plan#5", _marker(5))
    evil = _merge(work, work_side, work_side, five, message="merge dev into feature")
    _push(work, _merge(work, work_side, five, evil, message="Merge feature"), "dev")
    return 5


def _a_resolution_alone_adds_and_another_deletes(work, base):
    """A marker no commit of one parent ever held: one merge's resolution added card 6's,
    another's deleted it -- the case ``--full-history`` alone misses (measured)."""
    side = _files(work, base, "side", {"code": "s\n"})
    added = _merge(work, _files(work, side, "tree", _marker(6)), base, side, message="add")
    other = _files(work, base, "other", {"other": "o\n"})
    dropped = _files(work, added, "tree", {_git.marker(6): None, "other": "o\n"})
    _push(work, _merge(work, dropped, added, other, message="drop"), "dev")
    return 6


@pytest.mark.parametrize("history", [
    _shipped_then_reverted_then_merged_in, _hotfix_reverted_on_main_then_released,
    _a_resolution_drops_a_side_marker, _a_resolution_alone_adds_and_another_deletes])
def test_every_marker_any_commit_held_is_read_however_the_merges_hide_it(repo, history):
    """M1's review H1: ``sync`` reopens a card the tool closed as shipped only if the walk
    of the markers' history names it, and git's default walk lost three of these.  The one
    walk names the card in ``ever_marked`` and in ``history`` alike (one producer), and the
    card is not shipped now."""
    number = history(repo, _origin(repo))
    _git.fetch(repo)
    assert number not in _git.shipped(repo).cards
    assert number in _git.ever_marked(repo)
    assert _git.history(repo, number)


def test_a_marker_moved_to_another_name_reads_deleted_and_added(repo):
    """Read with no rename detection (git's own default finds one): card 5's marker moved to
    card 6's name is 5 deleted and 6 added, so the walk names 6, which shipped only through
    the move -- what lets ``sync`` read every card shipped now from the walk alone."""
    five = _files(repo, _origin(repo), "claim plan#5", _marker(5))
    moved = _files(repo, five, "move", {_git.marker(5): None, **_marker(6)})
    _push(repo, moved, "dev")
    _git.fetch(repo)
    assert _git.shipped(repo).cards == {6}
    assert _git.ever_marked(repo) == {5, 6}
    assert _git.history(repo, 5) == [("D", moved, "move"), ("A", five, "claim plan#5")]
    assert _git.history(repo, 6) == [("A", moved, "move")]


def test_a_marker_named_with_a_leading_zero_is_no_cards(repo):
    """``plan/ships/007`` names no card (a card's number has no leading zero): a HISTORY
    line, never card 7, shipped or ever marked."""
    _push(repo, _files(repo, _origin(repo), "zero", {"plan/ships/007": "claimed x\n"}), "dev")
    _git.fetch(repo)
    found = _git.shipped(repo)
    assert (found.cards, [line.split(" ")[0] for line in found.history]) == (
        frozenset(), ["plan/ships/007"])
    assert not _git.ever_marked(repo)


def test_a_read_brings_what_another_clone_pushed(repo):
    """M1's review M2: dev and main move on GitHub and other sessions claim from other
    clones, so what this clone reads is what its own fetch brings -- dev's marker, main's,
    and a claim whose commit this clone has never held."""
    _origin(repo)
    other = scratch.elsewhere(repo)
    base = _run(other, "rev-parse", _git.tracking(_git.DEV))
    _run(other, "push", "--quiet", "origin", f"{_files(other, base, 'dev', _marker(5))}:dev",
         f"{_files(other, base, 'hotfix', _marker(9))}:main")
    claimed = _git.claim(other, 11, _git.tracking(_git.DEV), _MON)
    assert _run(repo, "rev-parse", _git.tracking(_git.DEV)) == base
    reads = _git.read(repo)
    assert reads.shipped.cards == {5, 9}
    assert {number: claim.sha for number, claim in reads.claims.items()} == {11: claimed}


def test_a_marker_deleted_beside_an_identical_one_added_is_no_rename(repo):
    """M1's review L1: two markers claimed in the same second hold the same line.  Dev
    deleting 5 and adding 6 is no rename of 5, so main's change to 5 is the conflict it is,
    never carried silently into 6."""
    base = _files(repo, _origin(repo), "five", _marker(5))
    _push(repo, _files(repo, base, "dev", {_git.marker(5): None, **_marker(6)}), "dev")
    _push(repo, _files(repo, base, "main", _marker(5, "2026-10-20T09:30:00Z")), "main")
    _git.fetch(repo)
    with pytest.raises(GitError, match="conflict on plan/ships/5"):
        _git.shipped(repo)


def test_a_conflict_on_a_marker_refuses_to_answer_and_one_elsewhere_does_not(repo):
    """Two claims' markers for one card meeting in the merge leave its record unknown, so
    ``shipped`` refuses; a conflict in code only leaves every marker readable."""
    base = _origin(repo)
    _push(repo, _files(repo, base, "dev", {"README": "dev\n", **_marker(4)}), "dev")
    _push(repo, _files(repo, base, "main", {"README": "main\n", **_marker(6)}), "main")
    _git.fetch(repo)
    assert _git.shipped(repo).cards == {4, 6}
    _push(repo, _files(repo, base, "main again", _marker(4, "2026-10-20T09:30:00Z")), "main")
    _git.fetch(repo)
    with pytest.raises(GitError, match="conflict on plan/ships/4"):
        _git.shipped(repo)


def test_a_merge_names_each_marker_that_conflicts_and_only_merged_tree_refuses_it(repo):
    """``merge`` answers every merge, naming each marker that conflicts and no other path:
    dev changes card 7's line and main deletes it (each also changing the README), and the
    tree holds dev's line where the two conflict, which is no settled record, so
    ``merged_tree`` refuses it."""
    base = _files(repo, _origin(repo), "seven", _marker(7))
    dev = _files(repo, base, "dev", {"README": "dev\n", **_marker(7, "2026-10-20T09:30:00Z")})
    main = _files(repo, base, "main", {"README": "main\n", _git.marker(7): None})
    merged = _git.merge(repo, (dev, main))
    assert (merged.tips, merged.conflicts) == ((dev, main), (_git.marker(7),))
    assert _run(repo, "rev-parse", f"{merged.tree}:{_git.marker(7)}") == _run(
        repo, "rev-parse", f"{dev}:{_git.marker(7)}")
    with pytest.raises(_git.MarkerConflict, match="conflict on plan/ships/7") as raised:
        _git.merged_tree(repo, (dev, main))
    assert raised.value.paths == (_git.marker(7),)


def test_a_file_meeting_a_directory_at_a_marker_is_named_by_its_path(repo):
    """L1's review, its L1: dev changes card 7's line and main makes ``plan/ships/7`` a
    directory.  git lists that conflict under the name of the file it moved aside,
    ``<path>~<tip>``, the tip as given; the merge names it by its path, however each tip is
    spelled and whichever side holds the file."""
    base = _files(repo, _origin(repo), "seven", _marker(7))
    dev = _files(repo, base, "dev", _marker(7, "2026-10-20T09:30:00Z"))
    main = _files(repo, _files(repo, base, "main", {_git.marker(7): None}), "a directory",
                  {"plan/ships/7/x": "x\n"})
    _push(repo, dev, "dev")
    _push(repo, main, "main")
    _git.fetch(repo)
    for tips in ((dev, main), (main, dev), _git.TIPS, tuple(reversed(_git.TIPS))):
        assert _git.merge(repo, tips).conflicts == (_git.marker(7),), tips


def test_blob_at_reads_a_file_and_nothing_else(repo):
    """The blob of the file at a path; None where nothing is, where a directory is, and
    where a submodule's commit (a gitlink) is."""
    base = _files(repo, _origin(repo), "files", {**_marker(5), "plan/ships/6/inner": "x\n"})
    _run(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},{_git.marker(7)}")
    _run(repo, "commit", "--quiet", "-m", "a gitlink")
    tip = _run(repo, "rev-parse", "HEAD")
    assert _git.blob_at(repo, tip, _git.marker(5)) == _run(repo, "rev-parse",
                                                           f"{tip}:{_git.marker(5)}")
    for path in (_git.marker(6), _git.marker(7), _git.marker(8)):
        assert _git.blob_at(repo, tip, path) is None


def test_landings_names_the_commit_on_the_branch_line_that_took_each_line_in(repo):
    """CI's write rule asks through which commit of its OWN line a branch took each marker's
    line in.  Card 212's claim and work land on dev by one merge, which a release brings to
    main by another; dev reverts the card and a fresh claim re-ships it with a new line.
    dev took the first line in through its merge, never through the claim commit beside its
    line (a change merging that commit alone would carry the line without the code); main
    through the release; the card's own branch through its claim; and the revert leaves
    the landing in dev's past, naming the revert as the commit that undid it."""
    base = _origin(repo)
    claimed = _files(repo, base, "claim plan#212", _marker(212))
    work = _files(repo, claimed, "work", {"app.py": "212\n"})
    landed = _merge(repo, work, base, work, message="Merge card/212")
    released = _merge(repo, landed, base, landed, message="release")
    reverted = _files(repo, landed, "Revert plan#212", {_git.marker(212): None, "app.py": None})
    again = _files(repo, reverted, "claim plan#212 again", _marker(212, "2026-10-21T08:00:00Z"))
    first, second = (_run(repo, "rev-parse", f"{sha}:{_git.marker(212)}")
                     for sha in (claimed, again))
    assert first != second
    path = _git.marker(212)
    assert _git.landings(repo, again) == {(path, second): (_git.Took(again, None),),
                                          (path, first): (_git.Took(landed, reverted),)}
    assert _git.landings(repo, released) == {(path, first): (_git.Took(released, None),)}
    assert _git.landings(repo, work) == {(path, first): (_git.Took(claimed, None),)}
    assert not _git.landings(repo, base)


def test_the_walk_names_each_blob_before_and_passes_over_merges_when_asked(repo):
    """Each change the walk lists names the blob its entry held before it (``was``): none
    for an add, the line for a delete.  Asked for commits of one parent alone, it passes over
    a merge, even one whose resolution alone deleted a marker, and lists a plain delete
    (CI's landing check, R-BAL391)."""
    base = _origin(repo)
    claimed = _files(repo, base, "claim plan#5 and plan#6", {**_marker(5), **_marker(6)})
    five, six = (_git.marker(number) for number in (5, 6))
    lines = {path: _run(repo, "rev-parse", f"{claimed}:{path}") for path in (five, six)}
    dropped = _merge(repo, _files(repo, claimed, "drop 5", {five: None}), claimed, base,
                     message="a merge dropping plan#5")
    plain = _files(repo, dropped, "Revert plan#6", {six: None})
    found = {flag: [(change.sha, change.entry.status, change.entry.path, change.was)
                    for change in _git.marker_changes(repo, (plain, f"^{base}"), no_merges=flag)]
             for flag in (False, True)}
    added = [(claimed, "A", five, None), (claimed, "A", six, None)]
    assert found[False] == [(plain, "D", six, lines[six]), (dropped, "D", five, lines[five]),
                            *added]
    assert found[True] == [(plain, "D", six, lines[six]), *added]


def test_changes_names_every_entry_at_any_path_with_its_blob_and_no_rename(repo):
    """The change between two commits, at every path: a marker deleted beside an identical
    one added reads as both (a rename read would name one entry), a changed file names its
    new blob, a deleted one none; no change, no entry."""
    base = _origin(repo)
    one = _files(repo, base, "one", {**_marker(5), "app.py": "a\n"})
    two = _files(repo, one, "two", {_git.marker(5): None, **_marker(6), "app.py": "b\n",
                                    "README": None})
    assert set(_git.changes(repo, one, two)) == {
        _git.Entry("D", _git.marker(5), None), _git.Entry("D", "README", None),
        _git.Entry("A", _git.marker(6), _run(repo, "rev-parse", f"{two}:{_git.marker(6)}")),
        _git.Entry("M", "app.py", _run(repo, "rev-parse", f"{two}:app.py"))}
    assert not _git.changes(repo, two, two)
    assert set(_git.changes(repo, one, two, _git.MARKERS)) == {
        _git.Entry("D", _git.marker(5), None),
        _git.Entry("A", _git.marker(6), _run(repo, "rev-parse", f"{two}:{_git.marker(6)}"))}
    assert _git.changes(repo, one, _run(repo, "rev-parse", f"{two}^{{tree}}"), "README") == (
        _git.Entry("D", "README", None),)


def test_markers_in_reads_one_trees_markers_and_names_each_other_entry(repo):
    """A tree's markers, a commit naming its own: each card whose marker file it holds, and
    the path of every other entry under the markers' directory, which no card is."""
    base = _origin(repo)
    one = _files(repo, base, "one", {**_marker(5), **_marker(12), "plan/ships/notes": "x\n",
                                     "plan/ships/8/inner": "y\n", "app.py": "a\n"})
    assert _git.markers_in(repo, one) == (frozenset({5, 12}),
                                          ("plan/ships/8/inner", "plan/ships/notes"))
    tree = _run(repo, "rev-parse", f"{one}^{{tree}}")
    assert _git.markers_in(repo, tree) == _git.markers_in(repo, one)
    assert _git.markers_in(repo, base) == (frozenset(), ())


def test_lines_in_reads_each_markers_line_beside_the_entries_markers_in_names(repo):
    """The one reading of a tree's markers, with each card's line (its blob): two claims'
    lines differ, and every other entry under the markers' directory is named as
    :func:`_git.markers_in` names it."""
    base = _origin(repo)
    one = _files(repo, base, "one", {**_marker(5), **_marker(12, "2026-10-21T08:00:00Z"),
                                     "plan/ships/notes": "x\n", "app.py": "a\n"})
    lines, strays = _git.lines_in(repo, one)
    assert lines == {number: _run(repo, "rev-parse", f"{one}:{_git.marker(number)}")
                     for number in (5, 12)}
    assert lines[5] != lines[12]
    assert strays == ("plan/ships/notes",) == _git.markers_in(repo, one)[1]
    assert _git.lines_in(repo, base) == ({}, ())


@pytest.mark.parametrize(("path", "card"), [
    ("plan/ships/7", 7), ("plan/ships/1234", 1234), ("plan/ships/07", None),
    ("plan/ships/0", None), ("plan/ships/notes", None), ("plan/ships/8/inner", None),
    ("plan/ships", None), ("plan/shipsX/7", None), ("docs/plan/ships/7", None)])
def test_a_path_names_a_cards_marker_only_as_plan_ships_and_its_number(path, card):
    """A marker is the file ``plan/ships/<card number>``, and nothing else is one."""
    assert _git.card_of(path) == card


@pytest.mark.parametrize(("branch", "card"), [
    ("card/12", 12), ("card/012", None), ("card/x", None), ("card/12/x", None),
    ("card/", None), ("cards/12", None), ("feat/card/12", None)])
def test_a_branch_is_a_cards_claim_only_as_card_and_its_number(branch, card):
    """A claim is the branch ``card/<card number>``, and no other name is one."""
    assert _git.claimed_card(branch) == card


@pytest.mark.parametrize(("branch", "card"), [
    ("released/12/20261019T130000Z", 12), ("released/012/x", None), ("released/12", None),
    ("released/12/", None), ("released/x/y", None), ("card/12", None),
    ("feat/released/12/x", None)])
def test_a_branch_keeps_a_released_claim_only_as_released_its_number_and_when(branch, card):
    """A released claim's work is kept on ``released/<card number>/<when>``, and no other
    name is one."""
    assert _git.released_card(branch) == card


def test_ever_claimed_reads_every_claim_and_released_claim_the_tracking_refs_name(repo):
    """A live claim and a released one each name their card; a branch of another shape, a
    local branch and a tag name none."""
    base = _origin(repo)
    for ref in ("refs/remotes/origin/card/12", "refs/remotes/origin/released/13/20261019T1300Z",
                "refs/remotes/origin/card/x", "refs/remotes/origin/released/14",
                "refs/remotes/origin/cards/15", "refs/heads/card/16", "refs/tags/card/17"):
        _run(repo, "update-ref", ref, base)
    assert _git.ever_claimed(repo) == {12, 13}


def test_shipped_reads_the_merge_of_the_two_tips_it_is_named(repo):
    """The landing check reads what will have shipped once a pull request's merge and the
    other branch meet: the merge of the commit and main it names, never dev and main."""
    base = _origin(repo)
    _push(repo, _files(repo, base, "hotfix", _marker(9)), "main")
    change = _files(repo, base, "claim plan#5", _marker(5))
    _git.fetch(repo)
    assert _git.shipped(repo).cards == {9}
    assert _git.shipped(repo, (change, _git.tracking(_git.MAIN))).cards == {5, 9}


def test_a_claim_is_created_once_and_read_with_its_marker(repo):
    """The claim's push is create-only: a second claim, a fast-forward a plain push would take,
    is ClaimTaken; the claim read back names its tip, when it was committed and when its
    marker says it was claimed.  Its commit adds the marker and nothing else."""
    base = _origin(repo)
    sha = _git.claim(repo, 12, _git.tracking(_git.DEV), _MON)
    assert _run(repo, "diff", "--name-status", base, sha) == "A\tplan/ships/12"
    assert _run(repo, "cat-file", "blob", f"{sha}:plan/ships/12") == "claimed 2026-10-19T13:00:00Z"
    assert _git.on_origin(repo) == {12: sha}
    with pytest.raises(_git.ClaimTaken):
        _git.claim(repo, 12, sha, _TUE)
    reads = _git.read(repo)
    claim = reads.claims[12]
    assert (claim.sha, claim.claimed, claim.branch) == (sha, "2026-10-19T13:00:00Z", "card/12")
    assert datetime.fromisoformat(claim.committed)
    assert reads.shipped.cards == frozenset()


def test_the_same_claim_pushed_again_is_taken(repo, monkeypatch):
    """Session 8's row 2: git answers a push of the very commit the branch holds as up to date
    (``=``), exit 0, without testing the lease.  Two sessions of one machine claiming the same
    card on the same base in the same second write that same commit (same base, marker,
    identity and instants), so the second is refused as taken, never handed the first's claim
    (M1's review L7).  The expected answer changed on the developer's approval, CLAUDE.md rule
    5's exception (``plan-tracker-2026-10-22/m1_rule5_identical_claim.md``)."""
    _origin(repo)
    for variable in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(variable, "2026-10-19T13:00:00+00:00")
    first = _git.claim(repo, 3, _git.tracking(_git.DEV), _MON)
    with pytest.raises(_git.ClaimTaken):
        _git.claim(repo, 3, _git.tracking(_git.DEV), _MON)
    assert _git.on_origin(repo) == {3: first}


def test_a_claim_push_origin_refuses_is_a_failure_not_a_claim_taken(repo):
    """M1's review L2: only a lease's ``stale info`` (or this very commit there already)
    means another session holds the card; origin refusing the push (a hook, a ruleset) is
    a failed call, and no claim stands."""
    _origin(repo)
    _deny(repo, 'case "$ref" in refs/heads/card/*) true;; *) false;; esac')
    with pytest.raises(GitError, match="pre-receive hook declined") as failed:
        _git.claim(repo, 2, _git.tracking(_git.DEV), _MON)
    assert not isinstance(failed.value, _git.ClaimTaken)
    assert not _git.on_origin(repo)


def test_only_a_card_number_under_card_is_a_claim_and_merged_work_is_none(repo):
    """``card/x`` and ``card/007`` name no card; a ``card/N`` whose tip ``dev`` or ``main``
    holds is merged work whose branch outlived its merge, not a claim."""
    base = _origin(repo)
    for branch in ("card/x", "card/007", "feat/y"):
        _push(repo, base, branch)
    merged = _git.claim(repo, 8, _git.tracking(_git.DEV), _MON)
    _push(repo, merged, "dev")
    hotfix = _git.claim(repo, 9, _git.tracking(_git.MAIN), _MON)
    _push(repo, hotfix, "main")
    live = _git.claim(repo, 10, _git.tracking(_git.DEV), _MON)
    assert _git.on_origin(repo) == {8: merged, 9: hotfix, 10: live}
    assert list(_git.read(repo).claims) == [10]


def test_a_claim_tip_without_its_marker_reads_no_claim_instant(repo):
    """A ``card/N`` someone pushed by hand, holding no marker for N, is still a claim (origin
    holds the branch), with no instant to report."""
    base = _origin(repo)
    _push(repo, _files(repo, base, "by hand", {"work": "x\n"}), "card/4")
    assert _git.read(repo).claims[4].claimed is None


def test_a_claim_tip_holding_a_directory_for_its_marker_reads_no_claim_instant(repo):
    """M1's review L9: the marker is read only as the file it is; a ``plan/ships/4``
    directory at the tip of a ``card/4`` pushed by hand is no marker, so no instant."""
    base = _origin(repo)
    _push(repo, _files(repo, base, "by hand", {"plan/ships/4/inner": "claimed x\n"}), "card/4")
    assert _git.read(repo).claims[4].claimed is None


def test_check_out_writes_no_upstream(repo):
    """The claim's local branch tracks nothing, so the shared configuration gains no
    ``branch.card/N`` section."""
    _origin(repo)
    sha = _git.claim(repo, 5, _git.tracking(_git.DEV), _MON)
    assert _git.check_out(repo, 5, sha) is None
    assert _run(repo, "symbolic-ref", "--short", "HEAD") == "card/5"
    assert _git.has_branch(repo, "card/5") and not _git.has_branch(repo, "card/6")
    assert "branch.card/5" not in _run(repo, "config", "--list")


def test_a_release_keeps_the_work_and_deletes_only_the_tip_it_read(repo):
    """The work moves to ``released/N/<instant>`` first; the delete names the tip read, so a
    newer push survives a release read before it, and is named."""
    _origin(repo)
    sha = _git.claim(repo, 6, _git.tracking(_git.DEV), _MON)
    newer = _files(repo, sha, "more work", {"work": "x\n"})
    _push(repo, newer, "card/6")
    with pytest.raises(GitError, match="moved since it was read"):
        _git.release(repo, 6, sha, _MON)
    assert _git.on_origin(repo) == {6: newer}
    kept = _git.release(repo, 6, newer, _TUE)
    assert kept == "released/6/20261020T093000Z"
    assert not _git.on_origin(repo)
    assert _run(repo, "ls-remote", "origin", f"refs/heads/{kept}").split("\t")[0] == newer


def test_a_release_origin_refuses_to_delete_says_so_not_that_it_moved(repo):
    """M1's review L6: the delete refused by origin, not by the lease, keeps the claim and
    names the refusal; "moved since it was read" is the lease's answer alone.  The work is
    kept either way."""
    _origin(repo)
    sha = _git.claim(repo, 6, _git.tracking(_git.DEV), _MON)
    _deny(repo, '[ "$new" = 0000000000000000000000000000000000000000 ]')
    with pytest.raises(GitError, match="pre-receive hook declined") as failed:
        _git.release(repo, 6, sha, _TUE)
    assert "moved" not in str(failed.value)
    assert _git.on_origin(repo) == {6: sha}
    assert _run(repo, "ls-remote", "origin", "refs/heads/released/6/*").split("\t")[0] == sha


def test_history_lists_each_commit_that_touched_the_marker(repo):
    """``show``'s history: the claim's commit that added it, the revert that deleted it, newest
    first, on ``dev`` or ``main``."""
    _origin(repo)
    sha = _git.claim(repo, 2, _git.tracking(_git.DEV), _MON)
    _push(repo, sha, "dev")
    revert = _files(repo, sha, "Revert claim plan#2", {_git.marker(2): None})
    _push(repo, revert, "dev")
    _git.fetch(repo)
    assert _git.history(repo, 2) == [("D", revert, "Revert claim plan#2"),
                                     ("A", sha, "claim plan#2")]


def test_a_branch_starts_at_the_commit_it_grew_from(repo, monkeypatch):
    """Review cp3 M-2: ``spec-history --since <branch>`` starts where the work started, the
    author date of the dev commit the branch's oldest own commit sits on; a branch with no
    commit off dev is its own start."""
    dated = {}
    for name, when, parents in (("fork", "2026-09-30T12:00:00-04:00", ()),
                                ("first", "2026-10-01T09:00:00-04:00", ("fork",)),
                                ("dev", "2026-10-02T09:00:00-04:00", ("fork",)),
                                ("tip", "2026-10-03T09:00:00-04:00", ("first",))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["dev"])
    _run(repo, "update-ref", "refs/heads/feature", dated["tip"])
    assert _git.started(repo, "feature") == "2026-09-30T12:00:00-04:00"
    _run(repo, "update-ref", "refs/heads/fresh", dated["dev"])
    assert _git.started(repo, "fresh") == "2026-10-02T09:00:00-04:00"


def test_a_branch_holding_a_merge_starts_at_the_earliest_line_it_grew_from(repo, monkeypatch):
    """Review cp4 L-5: a merge listing a peer line FIRST put that line's base first, so the
    branch's own commit b1 fell outside the window; every line it grew from counts."""
    dated = {}
    for name, when, parents in (("d0", "2026-09-01T00:00:00-04:00", ()),
                                ("b1", "2026-09-02T00:00:00-04:00", ("d0",)),
                                ("b2", "2026-09-03T00:00:00-04:00", ("b1",)),
                                ("d5", "2026-09-05T00:00:00-04:00", ("d0",)),
                                ("p1", "2026-09-06T00:00:00-04:00", ("d5",)),
                                ("p2", "2026-09-07T00:00:00-04:00", ("p1",)),
                                ("m", "2026-09-09T00:00:00-04:00", ("p2", "b2"))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["d5"])
    _run(repo, "update-ref", "refs/heads/feature", dated["m"])
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"
    _run(repo, "update-ref", "refs/heads/feature",
         _commit(repo, "m2", parents=[dated["b2"], dated["p2"]]))
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"


def test_a_branch_with_a_root_of_its_own_starts_at_that_root(repo, monkeypatch):
    """A line no dev commit lies under (an orphan root) has no boundary; its root is where
    that line's work started."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-10T00:00:00-04:00")
    _dev(repo, _commit(repo, "dev"))
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-01T00:00:00-04:00")
    root = _commit(repo, "orphan")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-05T00:00:00-04:00")
    _run(repo, "update-ref", "refs/heads/feature", _commit(repo, "work", parents=[root]))
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"


def test_a_branch_starts_at_the_earliest_of_every_line_it_grew_from(repo, monkeypatch):
    """A dev commit under one line and an orphan root under another: the earlier of the two,
    whichever git lists last."""
    dated = {}
    for name, when, parents in (("d", "2026-09-01T00:00:00-04:00", ()),
                                ("b1", "2026-09-03T00:00:00-04:00", ("d",)),
                                ("r", "2026-09-05T00:00:00-04:00", ()),
                                ("w", "2026-09-06T00:00:00-04:00", ("r",)),
                                ("m", "2026-09-07T00:00:00-04:00", ("w", "b1"))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["d"])
    _run(repo, "update-ref", "refs/heads/feature", dated["m"])
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"


def test_author_date_is_when_the_commit_was_first_written(repo, monkeypatch):
    """``spec-history --since <ref>`` reads the AUTHOR date, ISO with its offset: a rebase
    or amend moves the committer date later and would narrow the window."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-01T09:00:00-04:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-03T09:00:00-04:00")
    sha = _commit(repo, "base")
    assert _git.author_date(repo, sha) == "2026-10-01T09:00:00-04:00"


# -- an environment bound to another repository reaches nothing but the directory named ----------

@pytest.mark.parametrize("binding", scratch.BINDINGS)
def test_a_bound_environment_reaches_no_repository_but_the_one_named(binding, tmp_path,
                                                                     monkeypatch):
    """The incident's mechanism replayed onto a sentinel: every write the tests' builder makes
    and every git call this module makes -- the fetch, the record's merge, the markers'
    history, the claim's objects, push and branch, the release's two pushes -- lands in the
    directory named, every answer is that directory's, and the sentinel -- its refs, config,
    HEADs and indexes -- is byte-identical afterwards.  The sentinel is proven bound, and its
    indexes not empty (review cp3 M-1's control), before it is handed over
    (``scratch._prove_bound``).  The trailer rules' own calls, ``is_ancestor`` among them,
    are held the same way in ``tools/ci/test_trailers.py``."""
    sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
    work = scratch.repository(tmp_path, "work")
    _origin(work)
    sha = _git.claim(work, 3, _git.tracking(_git.DEV), _MON)
    assert _git.check_out(work, 3, sha) is None
    reads = _git.read(work)
    assert (list(reads.claims), reads.shipped.cards) == ([3], frozenset())
    _push(work, sha, "dev")
    _git.fetch(work)
    assert _git.shipped(work).cards == {3}
    assert _git.history(work, 3) == [("A", sha, "claim plan#3")]
    assert _git.ever_marked(work) == {3}
    assert _git.author_date(work, sha)
    assert _git.release(work, 3, sha, _TUE).startswith("released/3/")
    assert _git.has_branch(work, "card/3")

    assert scratch.sentinel_state(sentinel) == before


def test_a_rebased_branch_starts_at_its_earliest_own_commit(repo, monkeypatch):
    """Review cp4b L5: a rebase keeps an own commit's author date and moves it onto a newer
    base, so the base's date alone opened the window after the commit was written."""
    dated = {}
    for name, when, parents in (("d0", "2026-09-01T00:00:00-04:00", ()),
                                ("d9", "2026-09-09T00:00:00-04:00", ("d0",)),
                                ("b1", "2026-09-02T00:00:00-04:00", ("d9",))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["d9"])
    _run(repo, "update-ref", "refs/heads/feature", dated["b1"])
    assert _git.started(repo, "feature") == "2026-09-02T00:00:00-04:00"


def test_the_start_is_the_earliest_moment_whatever_each_dates_offset(repo, monkeypatch):
    """Review cp4b G4: dates written in different offsets compare as moments, not as text
    (``+05:00`` 09-02 01:00 is 09-01 20:00 UTC, before ``-04:00`` 09-01 22:00)."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-01T22:00:00-04:00")
    base = _commit(repo, "dev")
    _dev(repo, base)
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-02T01:00:00+05:00")
    _run(repo, "update-ref", "refs/heads/feature", _commit(repo, "own", parents=[base]))
    assert _git.started(repo, "feature") == "2026-09-02T01:00:00+05:00"
