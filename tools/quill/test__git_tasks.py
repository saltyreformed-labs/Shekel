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

from tools.ci import scratch
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


def test_a_claims_own_commits_name_their_tasks_by_their_trailer(code):
    """Ruling plan#1872: a commit names the task it builds by ``Task: plan#T``, its key read in
    any case; a value that names no card (``plan#<number>``) names nothing, and a commit dev or
    main holds is no commit of the claim, so a task it names is not the claim's."""
    root, base = code
    landed = commit_files(root, base, "card 3's piece\n\nTask: plan#40", {"a.py": "a\n"})
    point_dev(root, landed)
    claimed = commit_files(root, landed, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    one = commit_files(root, claimed, "piece one\n\nTask: plan#41", {"b.py": "b\n"})
    two = commit_files(root, one, "piece two\n\ntask: plan#42", {"c.py": "c\n"})
    tip = commit_files(root, two, "loose\n\nTask: 43\nTask: plan#044", {"d.py": "d\n"})
    assert _git.begun_tasks(root, tip) == {41, 42}


def test_a_task_is_in_progress_until_the_claims_tip_holds_its_file(code):
    """Ruling plan#1872: a task a commit of the claim names is in progress until the tip holds
    its file, written in the commit that ends its piece; the claim reads it so, and a task
    named by no commit of the claim is in progress nowhere, its file or none."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    begun = commit_files(root, claimed, "start plan#41\n\nTask: plan#41", {})
    ended = commit_files(root, begun, "piece one, its file\n\nTask: plan#41",
                         {"b.py": "b\n", **_done(41)})
    assert _git.in_progress(root, begun) == {41}
    assert _git.in_progress(root, ended) == frozenset()
    tip = commit_files(root, ended, "start plan#42\n\nTask: plan#42", _done(43))
    run(root, "update-ref", f"refs/remotes/origin/{_git.card_branch(8)}", tip)
    held = _git.claims(root, [8])[8]
    assert held.in_progress == {42}
    assert held.tasks == {41, 43}


def test_a_drop_ends_a_task_begun_until_a_commit_names_it_again(code):
    """Ruling plan#1900: a commit recording a task's drop (``Dropped: plan#T``) ends it as its
    file would, without marking it done: beside its name in one commit (as ``quill drop``
    writes it) or in a commit of its own, the drop wins; a commit naming it after the drop
    begins it again; and another task's drop leaves it as it was."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    begun = commit_files(root, claimed, "start plan#41\n\nTask: plan#41", {})
    dropped = commit_files(root, begun,
                           "drop plan#41: one\n\nsplit\n\nTask: plan#41\nDropped: plan#41", {})
    assert _git.in_progress(root, begun) == {41}
    assert _git.in_progress(root, dropped) == frozenset()
    assert _git.tasks_in(root, dropped)[0] == frozenset()
    again = commit_files(root, dropped, "start plan#41\n\nTask: plan#41", {})
    assert _git.in_progress(root, again) == {41}
    other = commit_files(root, again, "drop plan#42\n\ndropped: plan#42", {})
    assert _git.in_progress(root, other) == {41}
    alone = commit_files(root, other, "dropped by hand\n\nDropped: plan#41", {})
    assert _git.begun_tasks(root, alone) == frozenset()


def test_a_drop_is_read_before_the_start_it_ends_whatever_the_clocks_say(code, monkeypatch):
    """Each task's newest record decides by the history, never by commit dates: a drop whose
    clock ran behind, on one line of a merge, is read before the start it grew from, though
    the other line, dated later, also holds that start (git's ``--topo-order``)."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-24T10:00:00Z")
    begun = commit_files(root, claimed, "start plan#41\n\nTask: plan#41", {})
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-24T12:00:00Z")
    other = commit_files(root, begun, "other work", {"o.py": "o\n"})
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-24T09:00:00Z")
    dropped = commit_files(root, begun, "drop plan#41\n\nTask: plan#41\nDropped: plan#41", {})
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-24T13:00:00Z")
    tip = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", other, dropped),
              "-p", other, "-p", dropped, "-m", "merge")
    assert _git.in_progress(root, tip) == frozenset()


def test_a_claims_own_commits_are_read_with_what_their_trailers_name(code):
    """``stamps``, the one reading of a claim's commits' trailers (ruling plan#1872): each of
    its own commits, a merge first, ahead of the commits it grew from, with its parents, its
    subject, the tasks its ``Task`` trailers name and those its ``Dropped`` trailers name, each
    key in any case and in the message's order, a value naming no card left out; and no commit
    dev or main holds, whatever it names."""
    root, base = code
    landed = commit_files(root, base, "card 3's piece\n\nTask: plan#40", {"a.py": "a\n"})
    point_dev(root, landed)
    claimed = commit_files(root, landed, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-24T08:00:00Z\n"})
    one = commit_files(root, claimed, "piece one\n\nTask: plan#42\ntask: plan#41\nTask: 43",
                       {"b.py": "b\n"})
    side = commit_files(root, claimed, "a drop\n\nTask: plan#41\nDROPPED: plan#41", {})
    tip = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", one, side),
              "-p", one, "-p", side, "-m", "merge the drop")
    found = _git.stamps(root, tip)
    assert (found[0], found[-1].sha) == (_git.Stamp(tip, (one, side), "merge the drop", (), ()),
                                         claimed)
    assert {stamp.sha: stamp for stamp in found} == {
        tip: found[0], claimed: _git.Stamp(claimed, (landed,), "claim plan#8", (), ()),
        one: _git.Stamp(one, (claimed,), "piece one", (42, 41), ()),
        side: _git.Stamp(side, (claimed,), "a drop", (41,), (41,))}
