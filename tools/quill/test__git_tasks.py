"""The tracker tool's git reading of a task's done file (ruling ``balance:R-BAL386``), from
throwaway repositories under pytest's tmp_path.

A task is done when its file ``plan/tasks/T`` is in the merge of ``dev`` and ``main``, or in
the tip of its card's live claim when the claim's own commits wrote it, read as a marker is
read (:func:`_git.lines_in`).  Which card a task is under only the tracker says, so git's answer
for a claim names every task file its own commits wrote; ``quill sync`` keeps those of tasks
under that claim's card.  The tracking refs are
pointed directly (:func:`scratch.point_dev`, :func:`scratch.point_main`), as the readers here
take them as they are and fetch nothing.
"""
from __future__ import annotations

import pytest

from tools.ci import gitcmd, scratch
from tools.ci.scratch import commit_files, point_dev, point_main, run
from tools.quill import _git


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository under tmp_path whose ``origin/dev`` and ``origin/main`` hold one base
    commit; the repository and the base."""
    root = scratch.repository(tmp_path)
    base = commit_files(root, scratch.commit(root, "root"), "base", {"app.py": "base\n"})
    point_dev(root, base)
    point_main(root, base)
    return root, base


def _done(number, when="2026-10-23T22:00:00Z"):
    """``{plan/tasks/<number>: its line}``, a task's file as the commit finishing it writes it."""
    return {_git.task_file(number): f"done {when}\n"}


def test_a_tasks_file_is_named_as_a_marker_is():
    """A task's file is ``plan/tasks/<number>``, read by the markers' own predicate: a name
    that is no number, a file below the directory, or the directory itself names no task; and
    neither directory's file names the other's."""
    assert _git.task_file(12) == "plan/tasks/12"
    assert _git.task_of("plan/tasks/12") == 12
    for path in ("plan/tasks/notes", "plan/tasks/012", "plan/tasks/1/inner", "plan/tasks",
                 "plan/ships/12"):
        assert _git.task_of(path) is None, path
    assert _git.card_of("plan/tasks/12") is None


def test_what_is_done_is_the_merge_of_dev_and_main(code):
    """A task done on dev counts, and so does one done by a hotfix on main before main is
    merged into dev; one main deleted counts no longer; an entry under ``plan/tasks/`` that
    is no task's file is a HISTORY line, never a task; and the cards are read as before."""
    root, base = code
    both = commit_files(root, base, "card 8 with tasks 21 and 23",
                        {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n", **_done(21),
                         **_done(23)})
    point_dev(root, commit_files(root, both, "a stray", {"plan/tasks/notes": "x\n"}))
    point_main(root, commit_files(root, both, "task 22 done, task 23 undone",
                                  {**_done(22), _git.task_file(23): None}))
    found = _git.shipped(root)
    assert (found.cards, found.tasks) == ({8}, {21, 22})
    assert found.history == ("plan/tasks/notes on origin/dev or origin/main is no task's file "
                             "(a task's file is a file plan/tasks/<task number>): it is not "
                             "acted on",)


def test_a_revert_of_the_commit_finishing_a_task_makes_it_not_done(code):
    """R-BAL386's "a revert reopens it": card 8 lands with task 30's file written by the
    commit finishing it; ``git revert`` of that commit deletes the file with its work, so the
    task is no longer done, and the card stays shipped."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n"})
    piece = commit_files(root, claimed, "task 30's piece", {"w30.txt": "work\n", **_done(30)})
    landed = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", base, piece),
                 "-p", base, "-p", piece, "-m", "Merge card/8")
    point_dev(root, landed)
    assert _git.shipped(root).tasks == {30}
    run(root, "checkout", "--quiet", "--detach", landed)
    run(root, "revert", "--no-edit", piece)
    point_dev(root, run(root, "rev-parse", "HEAD"))
    assert (_git.shipped(root).cards, _git.shipped(root).tasks) == ({8}, frozenset())


def test_a_tasks_file_that_conflicts_refuses_quills_read_but_not_the_markers_readers(code):
    """dev and main each write task 31's file anew from the same line: their merge holds
    git's conflict text there, so whether it is done is unknown and quill's read refuses,
    naming it.  The markers' readers (CI's landing check, the release tool's
    ``merged_tree``) see no conflict: the release tool never reads a task."""
    root, base = code
    both = commit_files(root, base, "task 31", _done(31))
    point_dev(root, dev := commit_files(root, both, "dev", _done(31, "2026-10-24T08:00:00Z")))
    point_main(root, main := commit_files(root, both, "main", _done(31, "2026-10-24T09:00:00Z")))
    merged = _git.merge(root, (dev, main))
    assert (merged.conflicts, merged.task_conflicts) == ((), ("plan/tasks/31",))
    assert _git.merged_tree(root, (dev, main)) == merged.tree
    with pytest.raises(_git.MarkerConflict, match=(
            "merge with a conflict on plan/tasks/31, so whether those cards shipped or those "
            "tasks are done is unknown")) as raised:
        _git.shipped(root)
    assert raised.value.paths == ("plan/tasks/31",)


def test_the_markers_walk_reads_task_files_only_where_asked(code):
    """CI's write rule reads markers and task files in ONE walk (R-BAL386): asked for both
    directories, ``landings`` names the merge through which dev took task 60's line in beside
    card 8's marker's, and the walk lists both files' adds; asked nothing, as the release tool
    and quill's own readers ask it, the markers alone."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n"})
    piece = commit_files(root, claimed, "task 60's piece", {"w60.txt": "work\n", **_done(60)})
    landed = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", base, piece),
                 "-p", base, "-p", piece, "-m", "Merge card/8")
    both = (_git.MARKERS, _git.TASKS)
    lines = {path: run(root, "rev-parse", f"{landed}:{path}")
             for path in (_git.marker(8), _git.task_file(60))}
    took = (_git.Took(landed, None),)
    assert _git.landings(root, landed, both) == {key: took for key in lines.items()}
    assert _git.landings(root, landed) == {(_git.marker(8), lines[_git.marker(8)]): took}
    walked = {flag: {change.entry.path for change in _git.marker_changes(
        root, (landed, f"^{base}"), directories=flag)} for flag in (both, (_git.MARKERS,))}
    assert walked == {both: {_git.marker(8), _git.task_file(60)},
                      (_git.MARKERS,): {_git.marker(8)}}


def test_a_claim_names_every_task_file_its_own_commits_wrote(code):
    """A live claim names each task whose file its own commits wrote, whatever card the
    tracker says each is under (sync keeps those under the claim's card); a stray there names
    none; a claim with no task file names none."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n"})
    tip = commit_files(root, claimed, "two pieces and a stray",
                       {**_done(40), **_done(41), "plan/tasks/x/y": "x\n"})
    other = commit_files(root, base, "claim plan#9",
                         {_git.marker(9): "claimed 2026-10-19T13:00:00Z\n"})
    for number, sha in ((8, tip), (9, other)):
        run(root, "update-ref", f"refs/remotes/origin/{_git.card_branch(number)}", sha)
    found = _git.claims(root, [8, 9])
    assert {number: claim.tasks for number, claim in found.items()} == {
        8: {40, 41}, 9: frozenset()}


def test_a_claim_names_no_task_file_it_took_in_from_dev_or_main(code):
    """T1's review, its H1: ``quill claim`` cuts ``card/N`` from dev, so its tip holds every task
    file dev holds, and a merge of dev or main into it brings more; none of those is the
    claim's.  Cut from dev after card 7 landed tasks 49 and 50 there, then merging main, where
    a hotfix wrote task 51 (main behind dev, as before a release), claim 8 names only task 52,
    whose file its own piece wrote, and task 50, whose line its own commit changed."""
    root, base = code
    landed = commit_files(root, base, "card 7 lands with tasks 49 and 50",
                          {_git.marker(7): "claimed 2026-10-19T13:00:00Z\n", **_done(49),
                           **_done(50)})
    point_dev(root, landed)
    point_main(root, hotfix := commit_files(root, base, "a hotfix's task 51", _done(51)))
    claimed = commit_files(root, landed, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    own = commit_files(root, claimed, "task 52's piece, and task 50's line rewritten",
                       {**_done(52), **_done(50, "2026-10-24T09:00:00Z")})
    tip = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", own, hotfix),
              "-p", own, "-p", hotfix, "-m", "Merge origin/main into card/8")
    run(root, "update-ref", f"refs/remotes/origin/{_git.card_branch(8)}", tip)
    assert _git.tasks_in(root, tip)[0] == {49, 50, 51, 52}
    assert _git.claims(root, [8])[8].tasks == {50, 52}


def _doing(card, task):
    """``{plan/doing/<card>: plan#<task>}``, card ``card``'s file naming its task in progress
    as ``quill start`` writes it; with ``task`` None, its delete, as ``quill drop`` makes it."""
    return {_git.doing_file(card): None if task is None else f"plan#{task}\n"}


def _claimed(root, base, card=8):
    """Card ``card``'s claim on ``base``: a commit adding its marker; its sha."""
    return commit_files(root, base, f"claim plan#{card}",
                        {_git.marker(card): "claimed 2026-10-24T08:00:00Z\n"})


def test_a_task_is_in_progress_while_its_cards_file_names_it_and_its_file_is_not_there(code):
    """Ruling plan#2009: the task card 8's file ``plan/doing/8`` names is in progress until the
    tip holds its done file, written in the commit that ends its piece; the claim reads it so.
    A tip holding no such file, or one naming no task, has none in progress, and a commit's
    ``Task`` trailer alone begins nothing."""
    root, base = code
    claimed = _claimed(root, base)
    assert _git.in_progress(root, claimed, 8) is None
    named = commit_files(root, claimed, "work\n\nTask: plan#41", {"a.py": "a\n"})
    assert _git.in_progress(root, named, 8) is None
    begun = commit_files(root, claimed, "start plan#41\n\nTask: plan#41", _doing(8, 41))
    ended = commit_files(root, begun, "piece one, its file\n\nTask: plan#41",
                         {"b.py": "b\n", **_done(41)})
    assert _git.in_progress(root, begun, 8) == 41
    assert _git.in_progress(root, ended, 8) is None
    loose = commit_files(root, ended, "by hand", {_git.doing_file(8): "41\n"})
    assert _git.in_progress(root, loose, 8) is None
    tip = commit_files(root, ended, "start plan#42\n\nTask: plan#42", {**_doing(8, 42),
                                                                       **_done(43)})
    run(root, "update-ref", f"refs/remotes/origin/{_git.card_branch(8)}", tip)
    held = _git.claims(root, [8])[8]
    assert held.in_progress == 42
    assert held.tasks == {41, 43}


def test_a_drop_deletes_the_file_so_none_is_in_progress_until_a_start(code):
    """Ruling plan#2009: a drop deletes the card's file, so no task is in progress and none is
    marked done; a start after it begins one again."""
    root, base = code
    begun = commit_files(root, _claimed(root, base), "start plan#41", _doing(8, 41))
    dropped = commit_files(root, begun, "drop plan#41: one\n\nsplit\n\nTask: plan#41",
                           _doing(8, None))
    assert _git.in_progress(root, dropped, 8) is None
    assert _git.tasks_in(root, dropped)[0] == frozenset()
    again = commit_files(root, dropped, "start plan#41", _doing(8, 41))
    assert _git.in_progress(root, again, 8) == 41


def test_another_cards_work_merged_or_rebased_in_puts_nothing_in_progress(code):
    """Plan#1991's case: card 1's claim merges card 5's branch, which began task 5, and later
    takes it in again by a rebase onto it; card 1's tip holds ``plan/doing/5``, which card 1
    never reads, so its own task in progress, or none, is as its own file says."""
    root, base = code
    five = commit_files(root, _claimed(root, base, 5), "start plan#55", _doing(5, 55))
    one = _claimed(root, base, 1)
    merged = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", one, five),
                 "-p", one, "-p", five, "-m", "Merge card/5 into card/1")
    assert _git.in_progress(root, merged, 5) == 55
    assert _git.in_progress(root, merged, 1) is None
    begun = commit_files(root, merged, "start plan#12", _doing(1, 12))
    assert _git.in_progress(root, begun, 1) == 12
    rebased = commit_files(root, five, "claim plan#1, rebased onto card/5",
                           {_git.marker(1): "claimed 2026-10-24T08:00:00Z\n"})
    assert _git.in_progress(root, rebased, 1) is None


def test_two_copies_of_a_branch_that_began_different_tasks_conflict_on_its_file(code):
    """Ruling plan#2009: two copies of card 8's branch, each beginning another task, cannot
    merge without a conflict on ``plan/doing/8``, which git reports, so neither task is read
    as in progress beside the other; two that began the same task merge clean, naming it."""
    root, base = code
    claimed = _claimed(root, base)
    one = commit_files(root, claimed, "start plan#41", _doing(8, 41))
    other = commit_files(root, claimed, "start plan#42", _doing(8, 42))
    same = commit_files(root, claimed, "start plan#41 elsewhere\n\nTask: plan#41",
                        _doing(8, 41))
    conflicted = gitcmd.run(root, *scratch.ISOLATED, "merge-tree", "--write-tree",
                            "--name-only", one, other)
    assert conflicted.returncode == 1
    assert conflicted.stdout.split("\n\n")[0].splitlines()[1:] == [_git.doing_file(8)]
    tree = run(root, "merge-tree", "--write-tree", one, same)
    assert run(root, "cat-file", "blob", f"{tree}:{_git.doing_file(8)}") == "plan#41"


def test_a_claims_own_commits_are_read_with_what_their_trailers_name(code):
    """``stamps``, the one reading of a claim's commits' trailers (ruling plan#1872): each of
    its own commits, a merge first, ahead of the commits it grew from, with its parents, its
    subject and the tasks its ``Task`` trailers name, each key in any case and in the message's
    order, a value naming no card left out; and no commit dev or main holds, whatever it
    names."""
    root, base = code
    landed = commit_files(root, base, "card 3's piece\n\nTask: plan#40", {"a.py": "a\n"})
    point_dev(root, landed)
    claimed = commit_files(root, landed, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    one = commit_files(root, claimed, "piece one\n\nTask: plan#42\ntask: plan#41\nTask: 43",
                       {"b.py": "b\n"})
    side = commit_files(root, claimed, "a drop\n\nTASK: plan#41", {})
    tip = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", one, side),
              "-p", one, "-p", side, "-m", "merge the drop")
    found = _git.stamps(root, tip)
    assert (found[0], found[-1].sha) == (_git.Stamp(tip, (one, side), "merge the drop", ()),
                                         claimed)
    assert {stamp.sha: stamp for stamp in found} == {
        tip: found[0], claimed: _git.Stamp(claimed, (landed,), "claim plan#8", ()),
        one: _git.Stamp(one, (claimed,), "piece one", (42, 41)),
        side: _git.Stamp(side, (claimed,), "a drop", (41,))}
