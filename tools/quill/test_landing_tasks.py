"""CI's landing check over a task's done file (ruling ``balance:R-BAL386``): the one write rule
the ship markers are judged by, over ``plan/tasks/`` too, and the one arm it adds, a card's
pull request refused while a task under it that was not dropped lacks its done file.

A task is a piece of one card, a commit inside its one pull request, done when its file
``plan/tasks/T`` is in the merge of dev and main.  The picked text's three rules: only that
card's branch writes a file for a task under it; the card's pull request is refused while an
open task lacks its file; an entry under ``plan/tasks/`` that is no task's file is refused.
Graded hardest, as the plan asks (``plan-tracker-2026-10-23/r385_r386_s3_plan.md``, T2), on a
release and a resync carrying task files, and on a task re-parented.  Every history is built
and judged, and every tracker answered, by the builders the landing tests share
(:mod:`tools.quill._histories`).
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import commit_files, point_dev, point_main, run
from tools.quill._git import marker, task_file
from tools.quill._histories import MON, PERSON, TUE, Lookup, card_node, claim, closed, judging
from tools.quill._histories import merge, never, reader, start, tracker, verdict
from tools.quill.setup_tracker import APP_LOGIN


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base commit; the
    repository and the base (:func:`_histories.start`)."""
    return start(tmp_path)


@pytest.fixture(name="check")
def _check(capsys, code):
    """Judge pull request #600 of branch ``head`` at ``head_sha`` into ``base``, whose tip is
    ``onto`` (:func:`_histories.judging`), reading the tracker through ``github`` (no read
    allowed when None): its exit status, output and error."""
    judged = judging(capsys, code[0])

    def _run(onto, head_sha, head, *, base="dev", github=None):
        """Judge pull request #600 of ``head`` into ``base`` (the fixture)."""
        return judged(onto, head_sha, head, base, reader(github) if github else never)

    return _run


def _piece(root, on, task, when=MON):
    """The commit on ``on`` finishing task ``task``: its work, and its done file as ``quill
    task-file`` writes it, ``done <when>``; its sha."""
    return commit_files(root, on, f"task plan#{task}",
                        {f"t{task}.txt": "work\n", task_file(task): f"done {when}\n"})


def _built(root, on, card, *tasks):
    """Card ``card``'s claim on ``on`` (:func:`_histories.claim`), then the commit finishing
    each of ``tasks``; the branch's tip."""
    tip = claim(root, on, card)[1]
    for task in tasks:
        tip = _piece(root, tip, task)
    return tip


def _reverted(root, on, card, task):
    """A revert on ``on`` of card ``card``'s landing with task ``task``'s piece: its marker,
    its work and the task's work and done file deleted; its sha."""
    return commit_files(root, on, f"Revert plan#{card}", {
        marker(card): None, f"w{card}.txt": None, f"t{task}.txt": None, task_file(task): None})


def _owes(number, card=7, head="card/7", *, shown=False):
    """The open-task arm's refusal of ``head``, recording card ``card``, which owes task
    ``number``'s done file; ``shown``, the task closed by the tool as done, which ``quill
    drop`` drops only once ``quill sync`` has reopened it."""
    why, drop = ((" (quill closed it as done while a branch held that file)",
                  f"drop the task once quill sync reopens it (quill sync, then quill drop "
                  f"plan#{number})") if shown else
                 ("", f"drop the task (quill drop plan#{number})"))
    return (f"REFUSED -- plan/tasks/{number}: {head} records card {card}, but holds no done file "
            f"for task plan#{number} under it, which is neither done nor dropped{why}: write it "
            f"in the commit that finishes the task (quill task-file plan#{number}), or {drop}")


# ------------------------------------------------- the card's claim, and the tasks it owes


def test_a_claim_lands_its_card_with_the_done_file_of_each_task_under_it(check, code):
    """card/7 lands card 7 with the piece finishing task 12, its done file beside it: task 12
    is a task under card 7, read once with it, so it lands."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(12, "task", parent=7)))
    status, out, _ = check(base, _built(root, base, 7, 12), "card/7", github=github)
    assert (status, out) == (0, verdict("card/7", 1, 0, tasks=1) + "\n")
    assert github.asked == [[7, 12]]


@pytest.mark.parametrize(("close", "owed"), [
    (None, _owes(12)), (("COMPLETED", APP_LOGIN), _owes(12, shown=True)),
    (("COMPLETED", PERSON), None), (("NOT_PLANNED", APP_LOGIN), None)],
    ids=["open", "closed by the tool as done", "closed by a person", "dropped by quill drop"])
def test_a_claim_is_refused_while_a_task_under_its_card_lacks_its_done_file(check, code, close,
                                                                           owed):
    """card/7 records card 7 with no done file for task 12 under it.  An open task is owed,
    and so is one the TOOL closed as done, as that close only shows git (the branch it
    showed may since have been rewritten, or the piece reverted on it): refused, the task
    read by its number, and the drop it offers for that one waits for ``quill sync`` to
    reopen it, as ``quill drop`` refuses a closed card.  A person's close is a drop, and so
    is ``quill drop``'s: it lands."""
    root, base = code
    task = card_node(12, "task", parent=7)
    github = Lookup(tracker(card_node(7), closed(task, *close) if close else task))
    status, out, _ = check(base, claim(root, base, 7)[1], "card/7", github=github)
    assert (status, out.splitlines()) == ((1, [owed, verdict("card/7", 1, 1)]) if owed
                                          else (0, [verdict("card/7", 1, 0)]))
    assert github.asked == [[7], [12]]


def test_a_sub_issue_of_its_card_that_is_no_task_is_never_owed(check, code):
    """Card 7 lists sub-issue 11, of no type: no task, so card/7 owes no done file for it,
    and it is never read by its number."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(11, None, parent=7)))
    status, out, _ = check(base, claim(root, base, 7)[1], "card/7", github=github)
    assert (status, out) == (0, verdict("card/7", 1, 0) + "\n")
    assert github.asked == [[7]]


def test_a_task_its_card_lists_that_the_tracker_lacks_refuses_to_judge(check, code):
    """Card 7 lists task 12, which the tracker no longer holds when read by its number:
    whether it is owed cannot be read, so nothing is judged (exit 2)."""
    root, base = code
    nodes = tracker(card_node(7), card_node(12, "task", parent=7))
    del nodes[12]
    status, out, err = check(base, claim(root, base, 7)[1], "card/7", github=Lookup(nodes))
    assert (status, out) == (2, "")
    assert "cards [12] are named by others but do not exist" in err


def test_a_task_re_parented_is_done_on_its_new_cards_claim_only(check, code):
    """Task 12 was a piece of card 7 when card/7 wrote its done file; a person then moved it
    under card 9.  card/7's file marks nothing done, as sync reads it: refused there.  card/9
    owes it: refused without its file, and it lands with a done file of its own."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(9), card_node(12, "task", parent=9)))
    status, out, _ = check(base, _built(root, base, 7, 12), "card/7", github=github)
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/tasks/12: card/7 writes it, but task plan#12 is under plan#9, not "
        "plan#7, so it marks nothing done (a task is done on its own card's branch only): take "
        "it off card/7", verdict("card/7", 1, 1, tasks=1)])
    status, out, _ = check(base, claim(root, base, 9)[1], "card/9", github=github)
    assert (status, out.splitlines()) == (1, [_owes(12, 9, "card/9"), verdict("card/9", 1, 1)])
    assert check(base, _built(root, base, 9, 12), "card/9", github=github)[:2] == (
        0, verdict("card/9", 1, 0, tasks=1) + "\n")


def test_a_task_whose_done_file_landed_under_its_old_card_is_not_owed_by_its_new(check, code):
    """Card 7 landed on dev with task 12's piece; a person then moved task 12 under card 9.
    card/9, cut from that dev, writes no done file for task 12, but M's tree holds the one
    card 7 landed, so task 12 is done in git, as sync reads it: card/9 does not owe it."""
    root, base = code
    point_dev(root, landed := merge(root, base, _built(root, base, 7, 12), "Merge card/7"))
    github = Lookup(tracker(card_node(7), card_node(9), card_node(12, "task", parent=9)))
    status, out, _ = check(landed, claim(root, landed, 9)[1], "card/9", github=github)
    assert (status, out) == (0, verdict("card/9", 1, 0) + "\n")
    assert github.asked == [[9], [12]]


@pytest.mark.parametrize(("cards", "why"), [
    ((card_node(12),), "plan#12 is a step, no task"),
    ((), "plan#12 does not exist"),
    ((card_node(12, "task"),), "task plan#12 is under no card, not plan#7"),
], ids=["a step", "no card", "a task under no card"])
def test_a_claim_writing_the_done_file_of_no_task_of_its_card_is_refused(check, code, cards,
                                                                        why):
    """card/7 writes ``plan/tasks/12`` where 12 is a step, no card at all, or a task linked
    under none: the file marks nothing done, as sync reads it, so it is refused."""
    root, base = code
    github = Lookup(tracker(card_node(7), *cards))
    status, out, _ = check(base, _built(root, base, 7, 12), "card/7", github=github)
    assert (status, out.splitlines()) == (1, [
        f"REFUSED -- plan/tasks/12: card/7 writes it, but {why}, so it marks nothing done (a "
        "task is done on its own card's branch only): take it off card/7",
        verdict("card/7", 1, 1, tasks=1)])


@pytest.mark.parametrize("head", ["feat/x", None], ids=["a branch of no card", "a fork's"])
def test_a_done_file_from_no_claim_is_refused_unread(check, code, head):
    """A branch that is no claim writes task 12's done file: only the claim of the card the
    task is under may, so it is refused, the tracker never read."""
    root, base = code
    status, out, _ = check(base, _piece(root, base, 12), head)
    who = head or "a branch of another repository"
    assert (status, out.splitlines()) == (1, [
        f"REFUSED -- plan/tasks/12: {who} adds it, but a task's done file is written only on "
        "the claim of the card the task is under (quill task-file plan#12 writes it there)",
        verdict(who, 0, 1, tasks=1)])


# ------------------------------------------------ rules 1 and 2: what a done file is, a delete


@pytest.mark.parametrize("path", ["plan/tasks/notes", "plan/tasks/012", "plan/tasks/12/inner",
                                  "plan/tasks"])
def test_an_entry_that_is_no_tasks_done_file_is_refused_and_its_delete_lands(check, code, path):
    """A name that is no task number, a leading zero, a directory below, and the directory
    itself a file are no task's done file: refused from any branch; a change deleting one
    lands, the tracker never read."""
    root, base = code
    status, out, _ = check(base, commit_files(root, base, "stray", {path: "x\n"}), "feat/x")
    assert (status, out.splitlines()[0]) == (1, (
        f"REFUSED -- {path}: no task's done file is named so (a done file is the file "
        "plan/tasks/<task number>), so no change may add or change it"))
    point_dev(root, stray := commit_files(root, base, "stray", {path: "x\n"}))
    assert check(stray, commit_files(root, stray, "clean", {path: None}), "clean")[0] == 0


def test_a_delete_of_a_done_file_lands_from_any_branch_unread(check, code):
    """Card 7 landed on dev with task 12's piece.  A branch of no card deletes task 12's done
    file, a person's "not done": a delete, which lands, the tracker never read."""
    root, base = code
    landed = merge(root, base, _built(root, base, 7, 12), "Merge card/7")
    not_done = commit_files(root, landed, "task 12 is not done", {task_file(12): None})
    assert check(landed, not_done, "feat/x")[:2] == (0, verdict("feat/x", 0, 0, tasks=1) + "\n")


def test_a_gitlink_at_a_tasks_done_file_is_no_done_file(check, code):
    """A submodule's commit (a gitlink) at ``plan/tasks/12``: every reader of the task files
    names it no task's, so rule 1 refuses it."""
    root, base = code
    run(root, "checkout", "--quiet", "--detach", base)
    run(root, "update-index", "--add", "--cacheinfo", f"160000,{base},{task_file(12)}")
    run(root, "commit", "--quiet", "-m", "a gitlink at plan/tasks/12")
    status, out, _ = check(base, run(root, "rev-parse", "HEAD"), "feat/x")
    assert (status, out.splitlines()[0]) == (1, (
        "REFUSED -- plan/tasks/12: a done file is a file, and what this change writes there is "
        "none (a submodule's commit), which no reader counts as task 12's done file, so no "
        "change may add or change it"))


def test_the_records_door_never_opens_for_a_done_file(check, code):
    """A change into main adding card 2's marker and task 3's done file is no records-only
    change (R-BAL363 records cards alone): each is refused as its own claim's to write, the
    tracker never read."""
    root, base = code
    records = commit_files(root, base, "records", {marker(2): "landed before records\n",
                                                   task_file(3): f"done {MON}\n"})
    status, out, _ = check(base, records, "records", base="main")
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/ships/2: records adds it, but only card 2's own claim, card/2, may "
        "record it",
        "REFUSED -- plan/tasks/3: records adds it, but a task's done file is written only on "
        "the claim of the card the task is under (quill task-file plan#3 writes it there)",
        verdict("records", 1, 2, "main", tasks=1)])


# -------------------------------------- rules 3 and 4: a release, a resync, a revert of a revert


def test_a_release_carries_the_done_files_dev_took_in_unread(check, code):
    """Card 7 lands on dev with task 12's piece.  The release brings the merge through which
    dev took the done file in with the marker: carried, judged where it landed, so it lands
    in main, the tracker never read."""
    root, base = code
    point_dev(root, dev := merge(root, base, _built(root, base, 7, 12), "Merge card/7"))
    assert check(base, dev, "dev", base="main")[:2] == (
        0, verdict("dev", 1, 0, "main", tasks=1) + "\n")


def test_a_resync_carries_a_hotfixs_done_files_unread(check, code):
    """Hotfix card 8 lands on main with task 13's piece; main, brought back into dev, carries
    the merge through which main took its done file in: it lands, the tracker never read."""
    root, base = code
    point_main(root, hotfix := merge(root, base, _built(root, base, 8, 13), "Merge card/8"))
    dev = commit_files(root, base, "dev's own work", {"app.py": "dev\n"})
    assert check(dev, hotfix, "main")[:2] == (0, verdict("main", 1, 0, tasks=1) + "\n")


def test_a_copy_of_a_done_file_dev_took_in_is_refused(check, code):
    """Card 7 lands on dev with task 12's piece.  A change into main copying task 12's done
    file alone holds dev's line without the merge that landed its work: refused from any
    branch, as a marker's copy is."""
    root, base = code
    point_dev(root, merge(root, base, _built(root, base, 7, 12), "Merge card/7"))
    copied = commit_files(root, base, "copy", {task_file(12): f"done {MON}\n"})
    status, out, _ = check(base, copied, "copy", base="main")
    assert (status, out.splitlines()[0]) == (1, (
        "REFUSED -- plan/tasks/12: origin/dev took this very line in, but this change does not "
        "bring the commit through which it did, or holds the one through which it undid it, so "
        "it would record task 12 where its code is not, or mark it done again with no change "
        "to its record: the line comes from origin/dev only through the merge that took it in, "
        "and a task undone is done again from a fresh done file (quill task-file plan#12), "
        "which writes a new line"))


def test_a_task_undone_is_done_again_only_from_a_fresh_done_file(check, code):
    """Card 7 lands on dev with task 12's piece, and dev reverts it, done file and all.  A
    fresh claim of card 7 bringing back task 12's old line (a revert of its revert) would
    mark it done with no change to its record: refused, the claim's own branch included.  A
    fresh done file, a new line, lands."""
    root, base = code
    landed = merge(root, base, _built(root, base, 7, 12), "Merge card/7")
    reverted = _reverted(root, landed, 7, 12)
    again = claim(root, reverted, 7, TUE)[1]
    github = Lookup(tracker(card_node(7), card_node(12, "task", parent=7)))
    status, out, _ = check(reverted, _piece(root, again, 12), "card/7", github=github)
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/tasks/12: origin/dev held this very line once and undid it, so it "
        "would mark task 12 done again with no change to its record: a task undone is done "
        "again from a fresh done file (quill task-file plan#12), which writes a new line",
        verdict("card/7", 1, 1, tasks=1)])
    assert check(reverted, _piece(root, again, 12, TUE), "card/7", github=github)[:2] == (
        0, verdict("card/7", 1, 0, tasks=1) + "\n")


# ------------------------------------------------ rule 8: a done file left as it was, changed


def test_a_resync_keeping_a_done_file_mains_hotfix_deleted_is_refused(check, code):
    """Card 7 lands on dev with task 12's piece and is released; a hotfix on main reverts it,
    done file and all.  A resync built by hand takes the hotfix but keeps task 12's done
    file: it writes nothing there, yet task 12 would read done with its code gone.  Refused,
    in a task's words."""
    root, base = code
    dev = merge(root, base, _built(root, base, 7, 12), "Merge card/7")
    point_main(root, main_tip := _reverted(root, merge(root, base, dev, "release"), 7, 12))
    kept = commit_files(root, dev, "keep the done file",
                        {marker(7): None, "w7.txt": None, "t12.txt": None})
    resync = run(root, "commit-tree", f"{kept}^{{tree}}", "-p", dev, "-p", main_tip, "-m",
                 "resync")
    status, out, _ = check(dev, resync, "resync")
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/tasks/12: task 12 would read done once this change and origin/main "
        "meet, as it does not once origin/dev and origin/main do, though this change writes no "
        "line of it: a merge keeping a done file the other side deleted marks a task done "
        "again with no change to its record; take the delete their merge takes, or write task "
        "12's done file again (quill task-file plan#12), which writes a new line",
        verdict("resync", 1, 1)])


def test_a_resync_dropping_a_hotfixs_done_file_is_refused(check, code):
    """Hotfix card 8 lands on main with task 13's piece.  A resync built by hand takes its
    code but drops task 13's done file: task 13 would read not done with its code running.
    Refused, in a task's words; git's own resync lands."""
    root, base = code
    point_main(root, hotfix := merge(root, base, _built(root, base, 8, 13), "Merge card/8"))
    dropped = commit_files(root, hotfix, "drop the done file", {task_file(13): None})
    resync = run(root, "commit-tree", f"{dropped}^{{tree}}", "-p", base, "-p", hotfix, "-m",
                 "resync")
    status, out, _ = check(base, resync, "resync")
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/tasks/13: task 13 would read not done once this change and "
        "origin/main meet, where it reads done once origin/dev and origin/main do, though this "
        "change writes no line of it: it drops task 13's done file, which origin/main holds, "
        "with no change to its record, and no commit it brings, merges aside, deletes that "
        "very line; if this change keeps task 13's code, take origin/main's file, merging "
        "origin/main itself if this change has not; if it drops task 13's code too, take "
        "origin/main's file, then revert task 13 in a commit that is no merge (a revert "
        "deletes the done file with its code)", verdict("resync", 1, 1)])
    assert check(base, merge(root, base, hotfix, "resync"), "resync")[0] == 0


def test_a_branch_deleting_a_hotfixs_done_file_in_a_commit_of_its_own_lands(check, code):
    """Hotfix card 8 lands on main with task 13's piece.  A branch that merged main in, then
    deletes task 13's done file in a commit of its own, a person's "not done": the change
    shows the delete (R-BAL391), so it lands, as the same drop made in a merge's resolution
    does not (:func:`test_a_resync_dropping_a_hotfixs_done_file_is_refused`)."""
    root, base = code
    point_main(root, hotfix := merge(root, base, _built(root, base, 8, 13), "Merge card/8"))
    merged = merge(root, base, hotfix, "merge main in")
    not_done = commit_files(root, merged, "task 13 is not done", {task_file(13): None})
    assert check(base, not_done, "feat/x")[:2] == (0, verdict("feat/x", 1, 0) + "\n")


def test_a_resync_settling_a_done_file_conflict_on_a_line_main_undid_is_refused(check, code):
    """Two done files of task 12 (lines Y and Z) cross: dev merges Z into Y keeping Y, and
    main merges Y into Z also keeping Y, then main's hotfix deletes Y.  dev's and main's
    merge then conflicts on the file though main undid dev's very line.  A resync built by
    hand keeping Y would mark task 12 done again with no change to its record: refused, in a
    task's words, though the file conflicted BEFORE."""
    root, base = code
    one = commit_files(root, base, "done Y", {task_file(12): f"done {MON}\n"})
    two = commit_files(root, base, "done Z", {task_file(12): f"done {TUE}\n"})
    dev = run(root, "commit-tree", f"{one}^{{tree}}", "-p", one, "-p", two, "-m", "dev keeps Y")
    took = run(root, "commit-tree", f"{one}^{{tree}}", "-p", two, "-p", one, "-m", "main takes Y")
    point_main(root, main_tip := commit_files(root, took, "hotfix", {task_file(12): None}))
    resync = run(root, "commit-tree", f"{dev}^{{tree}}", "-p", dev, "-p", main_tip, "-m",
                 "resync")
    status, out, _ = check(dev, resync, "resync")
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/tasks/12: origin/dev and origin/main conflict on it, and this change "
        "settles it on a line origin/main held once and undid, though it writes no line of it, "
        "so it would mark task 12 done again with no change to its record: a task undone is "
        "done again from a fresh done file (quill task-file plan#12), which writes a new line",
        verdict("resync", 0, 1)])


def test_a_conflict_a_change_brings_on_a_done_file_refuses_to_judge(check, code):
    """Hotfix card 8 lands on main with task 14's piece (line W), is undone there, and lands
    again with a fresh done file (line Z).  A branch that merged main's first landing in,
    dropping task 14's done file, would make dev and main conflict on it once it lands,
    where before they merge cleanly: whether task 14 is done would be unknown, so nothing is
    judged (exit 2)."""
    root, base = code
    first = merge(root, base, _built(root, base, 8, 14), "Merge card/8")
    undone = _reverted(root, first, 8, 14)
    point_main(root, merge(root, undone, _piece(root, claim(root, undone, 8, TUE)[1], 14, TUE),
                           "Merge card/8 again"))
    dropped = commit_files(root, merge(root, base, first, "merge main in"), "drop it",
                           {task_file(14): None})
    status, out, err = check(base, dropped, "feat/x")
    assert (status, out) == (2, "")
    assert ("merge with a conflict on plan/tasks/14, which origin/dev and origin/main merge "
            "without, though this change writes none of them, so whether those tasks are done "
            "would be unknown once it lands") in err
