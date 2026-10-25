"""CI's landing check, rule 8 (rulings ``balance:R-BAL385`` and ``R-BAL390``): a marker a
change leaves as it was may not change what dev and main together say of its card; and rule 1
read as every reader of the markers reads them.

Rule 8 compares what BEFORE (the merge of the change's first parent with the other
integration branch) and AFTER (the merge of the change with it) say of each card: not
shipped, or shipped on a line.  Its first case is X-cx L8's resync probe
(``plan-tracker-2026-10-23/m3_resync_probe.md``, measured 2026-10-23): a resync built by hand
that keeps the marker ``main``'s hotfix deleted, which writes nothing on ``dev``'s line and so
passed every rule that reads what a change writes.  R-BAL390 widened it to L1's review's two
near-misses (``plan-tracker-2026-10-23/l1_review_probes/p3.py`` and ``p4.py``): a resync built
by hand keeping a line ``main`` undid where ``main`` shipped the card again, and one dropping
the marker of a hotfix ``main`` holds.  R-BAL391 re-heard the second: a delete passes where a
commit the change brings shows it in a change of its own, no merge (the widening's review,
``plan-tracker-2026-10-23/l1_widening_review_probes/p_revert_in_branch.py``: a revert made in a
branch that merged the other in).  Every history is built and judged by the builders
``test_landing.py`` shares (:mod:`tools.quill._histories`); no test here reads the tracker.
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import commit_files, point_main, run
from tools.quill import _git
from tools.quill._git import marker
from tools.quill._histories import MON, TUE, claim, judging, landed, merge, never, pull, start
from tools.quill._histories import undone, verdict
from tools.quill.landing import main

#: When a third claim of one card is made.
WED = "2026-10-21T08:00:00Z"


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base commit; the
    repository and the base (:func:`_histories.start`)."""
    return start(tmp_path)


@pytest.fixture(name="check")
def _check(capsys, code):
    """Judge pull request #600 of branch ``head`` at ``head_sha`` into ``base``, whose tip is
    ``onto`` (:func:`_histories.judging`), the tracker never read: its exit status, output
    and error."""
    judged = judging(capsys, code[0])
    return lambda onto, head_sha, head, base="dev": judged(onto, head_sha, head, base, never)


def _resolved(root, tree_of, first, second, message):
    """A merge of ``first`` and ``second`` whose tree is ``tree_of``'s, as a resolution
    written by hand writes it, whatever git's own merge would hold; its sha."""
    return run(root, "commit-tree", f"{tree_of}^{{tree}}", "-p", first, "-p", second, "-m",
               message)


def _hotfixed(root, base, number):
    """Card ``number`` landed on dev, a release taking it to main, and a hotfix on main
    deleting its marker and its work (the probe's history); dev's tip and main's, main's
    pointed at."""
    dev = landed(root, base, number)
    point_main(root, main_tip := undone(root, merge(root, base, dev, "release"), number))
    return dev, main_tip


def _reshipped(root, base, number):
    """The ruling's pass case: :func:`_hotfixed`, and dev, meanwhile, undid card ``number``
    and shipped it again from a fresh claim, a new line: dev's tip and main's; whose merge
    conflicts on the marker, dev's line changed and main's deleted."""
    dev, main_tip = _hotfixed(root, base, number)
    return landed(root, undone(root, dev, number), number, TUE), main_tip


def _refused(number, other="origin/main", target="origin/dev"):
    """Rule 8's refusal of card ``number``, made shipped by a change into ``target``."""
    return (f"REFUSED -- plan/ships/{number}: card {number} would read shipped once this change "
            f"and {other} meet, as it does not once {target} and {other} do, though this change "
            "writes no line of it: a merge keeping a marker the other side deleted re-ships a "
            "card with no change to its record, so a release would count none of its code; "
            f"take the delete their merge takes, or claim card {number} again (quill claim "
            f"{number}), which writes a new line")


# ------------------------------------------------- a marker left as it was made shipped


def test_a_resync_keeping_the_marker_mains_hotfix_deleted_is_refused(check, code):
    """The probe: card 7 lands on dev and is released; a hotfix on main deletes its marker and
    its code.  A resync built by hand takes main's code but keeps the marker: it writes
    nothing under ``plan/ships/`` on dev's line, yet card 7 would read shipped with no claim,
    and the next release would count it with none of its code.  Refused."""
    root, base = code
    dev, main_tip = _hotfixed(root, base, 7)
    kept = commit_files(root, dev, "keep the marker", {"w7.txt": None})
    status, out, _ = check(dev, _resolved(root, kept, dev, main_tip, "resync"), "resync")
    assert (status, out.splitlines()) == (1, [_refused(7), verdict("resync", 0, 1)])


@pytest.mark.parametrize("head", ["main", "resync"])
def test_gits_own_resync_takes_the_delete_and_lands(check, code, head):
    """The usual resync, main itself or a branch of git's own merge of the two, takes the
    hotfix's delete: a delete, which lands."""
    root, base = code
    dev, main_tip = _hotfixed(root, base, 7)
    resync = main_tip if head == "main" else merge(root, dev, main_tip, "resync")
    assert check(dev, resync, head)[:2] == (0, verdict(head, 1, 0) + "\n")


def test_a_release_keeping_a_marker_dev_deleted_is_refused(check, code):
    """The same rule into main: hotfix 9 lands on main and is resynced to dev, which then
    reverts it.  A release built by hand that keeps the marker would make card 9 read
    shipped again with no change to its record: refused."""
    root, base = code
    hotfix = landed(root, base, 9)
    point_main(root, hotfix)
    dev = undone(root, merge(root, base, hotfix, "resync"), 9)
    run(root, "update-ref", "refs/remotes/origin/dev", dev)
    kept = commit_files(root, dev, "keep the marker", {marker(9): f"claimed {MON}\n"})
    status, out, _ = check(hotfix, _resolved(root, kept, dev, hotfix, "release"), "release",
                           base="main")
    assert (status, out.splitlines()[0]) == (
        1, _refused(9, other="origin/dev", target="origin/main"))


def test_a_later_queue_entry_reads_before_at_the_merge_the_earlier_one_makes(capsys, code):
    """A group of two pull requests into dev, whose tip has not moved yet: card/8's entry,
    then an app change.  The app change's run reads BEFORE at the merge card/8's entry makes
    (M's first parent), where card 8 already reads shipped, never at dev's tip, where it does
    not: it lands."""
    root, base = code
    first = merge(root, base, claim(root, base, 8)[1], "Merge pull request #601")
    app = commit_files(root, base, "an app change", {"app.py": "app\n"})
    later = merge(root, first, app, "Merge pull request #602")
    assert main(["merge_group", later, f"refs/heads/gh-readonly-queue/dev/pr-602-{first}"],
                never, pull("feat/app", app), root) == 0
    assert "0 ship marker change(s), 0 refused" in capsys.readouterr().out


# ------------------------------------- a marker left as it was, its line changed or dropped


def _moved(number, reads, why):
    """Rule 8's refusal of card ``number`` by a change into dev, whose answer once it and
    main meet ``reads`` (to "than", "as it does not" or "where it reads shipped"), for the
    reason ``why``."""
    return (f"REFUSED -- plan/ships/{number}: card {number} would read {reads} once origin/dev "
            f"and origin/main do, though this change writes no line of it: {why}")


@pytest.mark.parametrize("undid", [True, False], ids=["a line main undid", "one main never held"])
def test_a_resync_keeping_devs_line_where_main_holds_another_is_refused(check, code, undid):
    """L1's review's ``p3.py``: card 7 lands on dev (line Y) and is released; main's hotfix
    deletes it and main ships it again from a fresh claim (line Z).  A resync built by hand
    keeping dev's Y writes nothing, and card 7 reads shipped once dev and main meet either
    way, but on Y where git's merge takes Z: the next release would bring back a line main
    undid, refused there by rule 3 (and so would the obvious repair, a copy of Z).  Refused
    here; and where main never held Y (its release kept its own hotfix's line), refused
    without saying main undid it.  No claim is offered: git says card 7 shipped, which quill
    claim refuses (R-BAL391's approval).  git's own resync then lands, and the release after
    it."""
    root, base = code
    dev = landed(root, base, 7)
    if undid:
        main_tip = landed(root, undone(root, merge(root, base, dev, "release"), 7), 7, TUE)
    else:
        hotfix = landed(root, base, 7, TUE)
        main_tip = _resolved(root, hotfix, hotfix, dev, "release, keeping main's line")
    point_main(root, main_tip)
    status, out, _ = check(dev, _resolved(root, dev, dev, main_tip, "resync"), "resync")
    held = ", which origin/main held once and undid," if undid else ""
    assert (status, out.splitlines()) == (1, [_moved(
        7, "shipped on another line once this change and origin/main meet than",
        f"it keeps origin/dev's line{held} where their merge takes another, with no change to "
        "its record; take the line their merge takes"), verdict("resync", 0, 1)])
    resync = merge(root, dev, main_tip, "resync")
    assert check(dev, resync, "resync")[:2] == (0, verdict("resync", 1, 0) + "\n")
    run(root, "update-ref", "refs/remotes/origin/dev", resync)
    assert check(main_tip, resync, "dev", base="main")[:2] == (0, verdict("dev", 0, 0, "main")
                                                               + "\n")


def _dropped(number, other="origin/main", target="origin/dev"):
    """Rule 8's refusal of card ``number``, whose marker ``other`` holds, dropped by a change
    into ``target`` that shows no delete of it (R-BAL391)."""
    return (f"REFUSED -- plan/ships/{number}: card {number} would read not shipped once this "
            f"change and {other} meet, where it reads shipped once {target} and {other} do, "
            f"though this change writes no line of it: it drops card {number}'s marker, which "
            f"{other} holds, with no change to its record, and no commit it brings, merges "
            f"aside, deletes that very line; if this change keeps card {number}'s code, take "
            f"{other}'s file, merging {other} itself if this change has not; if it drops card "
            f"{number}'s code too, take {other}'s file, then revert card {number} in a commit "
            "that is no merge (a revert deletes the marker with its code)")


def _meet(root, change, other, number=8):
    """What dev and main hold once ``change``, landed, and ``other`` meet: each of card
    ``number``'s marker and its work, None where their merge holds none."""
    tree = _git.merge(root, (change, other)).settled()
    return _git.blob_at(root, tree, marker(number)), _git.blob_at(root, tree, f"w{number}.txt")


def test_a_resync_dropping_the_marker_of_a_hotfix_main_holds_is_refused(check, code):
    """L1's review's ``p4.py``: hotfix 8 lands on main.  A resync built by hand takes its
    code but drops its marker: it writes nothing on dev's line, yet card 8 would read not
    shipped while its code runs, and the next release would delete the marker from main.
    Refused; git's own resync takes main's file and lands."""
    root, base = code
    point_main(root, hotfix := landed(root, base, 8))
    dropped = commit_files(root, hotfix, "drop the marker, keep the code", {marker(8): None})
    status, out, _ = check(base, _resolved(root, dropped, base, hotfix, "resync"), "resync")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("resync", 0, 1)])
    assert check(base, merge(root, base, hotfix, "resync"), "resync")[0] == 0


def test_a_merge_dropping_the_marker_with_main_as_its_first_parent_is_refused(check, code):
    """The same resync built by hand with main as its merge's first parent, whose change
    against that parent deletes card 8's marker: a merge's resolution shows no delete,
    whichever parent it names first (R-BAL391), so refused."""
    root, base = code
    point_main(root, hotfix := landed(root, base, 8))
    dropped = commit_files(root, hotfix, "drop the marker, keep the code", {marker(8): None})
    status, out, _ = check(base, _resolved(root, dropped, hotfix, base, "resync"), "resync")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("resync", 0, 1)])


def test_each_way_out_of_a_dropped_marker_lands_as_it_says(check, code):
    """The refusal's ways out, each applied to a history of the kind it names.  A resync built
    by hand dropping hotfix 8's marker and keeping its work, which merged main itself, takes
    main's file: it lands, and card 8 reads shipped with its work.  A branch whose merge of
    main dropped both, keeping only the branch's side (as ``git merge -s ours`` writes it),
    takes main's file and then reverts card 8 in a commit of its own: it lands, and card 8
    reads not shipped with its work gone.  Record and code agree either way."""
    root, base = code
    point_main(root, hotfix := landed(root, base, 8))
    line = {marker(8): f"claimed {MON}\n"}
    kept = _resolved(root, commit_files(root, hotfix, "drop the marker", {marker(8): None}),
                     base, hotfix, "resync")
    assert check(base, kept, "resync")[0] == 1
    taken = commit_files(root, kept, "take main's file", line)
    assert check(base, taken, "resync")[:2] == (0, verdict("resync", 1, 0) + "\n")
    assert None not in _meet(root, merge(root, base, taken), hotfix)
    dropped = _resolved(root, base, base, hotfix, "Merge origin/main, dropping card 8")
    assert check(base, dropped, "revert-8")[0] == 1
    reverted = commit_files(root, commit_files(root, dropped, "take main's file", line),
                            "Revert plan#8", {marker(8): None})
    assert check(base, reverted, "revert-8")[:2] == (0, verdict("revert-8", 0, 0) + "\n")
    assert _meet(root, merge(root, base, reverted), hotfix) == (None, None)


def test_keeping_the_code_takes_mains_file_through_main_itself(check, code):
    """R-BAL391's review, its M2: hotfix 8 lands on main by the merge of card/8.  A branch
    merging card/8's tip itself, not main, by hand, drops the marker and keeps the work:
    refused.  main's file taken alone is a copy of a line main took in, which rule 4 refuses;
    taken with a merge of main itself, as the refusal says, it lands, and card 8 reads
    shipped with its work."""
    root, base = code
    tip = claim(root, base, 8)[1]
    point_main(root, hotfix := merge(root, base, tip, "Merge card/8"))
    dropped = _resolved(root, commit_files(root, tip, "drop the marker", {marker(8): None}),
                        base, tip, "Merge card/8 by hand, dropping its marker")
    status, out, _ = check(base, dropped, "feat/x")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("feat/x", 0, 1)])
    taken = commit_files(root, dropped, "take main's file", {marker(8): f"claimed {MON}\n"})
    status, out, _ = check(base, taken, "feat/x")
    assert (status, out.splitlines()[-1]) == (1, verdict("feat/x", 1, 1))
    assert "origin/main took this very line in" in out
    through = merge(root, taken, hotfix, "Merge origin/main")
    assert check(base, through, "feat/x")[:2] == (0, verdict("feat/x", 1, 0) + "\n")
    assert None not in _meet(root, merge(root, base, through), hotfix)


# ------------------------------------------- a marker's delete shown in history (R-BAL391)


def test_a_branch_reverting_mains_hotfix_after_merging_main_in_lands(check, code):
    """The widening's review, its HIGH (``p_revert_in_branch.py`` (a)): hotfix 8 lands on
    main.  A branch off dev runs git's own merge of main, then reverts hotfix 8 in a commit
    of its own, marker and work: the delete shows in history, so it lands, and card 8 then
    reads not shipped with its work gone once dev and main meet."""
    root, base = code
    point_main(root, hotfix := landed(root, base, 8))
    reverted = undone(root, merge(root, base, hotfix, "Merge origin/main"), 8)
    assert check(base, reverted, "revert-8")[:2] == (0, verdict("revert-8", 0, 0) + "\n")
    assert _meet(root, merge(root, base, reverted), hotfix) == (None, None)


def test_a_release_holding_a_card_back_by_a_revert_lands(check, code):
    """The review's case (b): card 9 lands on dev.  A release branch off main merges dev and
    reverts card 9 in a commit of its own, holding it back from production: into main, it
    lands, and card 9 then reads not shipped with its work gone once dev and main meet."""
    root, base = code
    run(root, "update-ref", "refs/remotes/origin/dev", dev := landed(root, base, 9))
    held_back = undone(root, merge(root, base, dev, "Merge origin/dev"), 9)
    assert check(base, held_back, "release", base="main")[:2] == (
        0, verdict("release", 0, 0, "main") + "\n")
    assert _meet(root, merge(root, base, held_back), dev, 9) == (None, None)


def test_a_commit_deleting_only_the_marker_after_merging_main_in_lands(check, code):
    """R-BAL391's "still allowed, exactly as on dev's own line": a branch that merged main's
    hotfix 8 in deletes card 8's marker alone, keeping its code, in a commit of its own -- a
    person's visible "not done", as rule 2 passes on dev's own line."""
    root, base = code
    point_main(root, hotfix := landed(root, base, 8))
    brought = merge(root, base, hotfix, "Merge origin/main")
    not_done = commit_files(root, brought, "card 8 is not done", {marker(8): None})
    assert check(base, not_done, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


def test_a_delete_of_an_older_line_shows_no_delete_of_the_one_main_holds(check, code):
    """Hotfix 8 lands on main (line V), is undone there, and ships again (line W).  A branch
    that merged main's first landing in reverts it in a commit of its own, deleting V, then
    merges main by hand, dropping W: no commit it brings deletes W, so refused."""
    root, base = code
    first = landed(root, base, 8)
    point_main(root, main_tip := landed(root, undone(root, first, 8), 8, WED))
    reverted = undone(root, merge(root, base, first, "Merge origin/main"), 8)
    dropped = _resolved(root, reverted, reverted, main_tip, "Merge origin/main, dropping W")
    status, out, _ = check(base, dropped, "feat/x")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("feat/x", 0, 1)])


def test_a_line_changed_and_then_deleted_shows_no_delete_of_it(check, code):
    """A branch that merged main's hotfix 8 in changes card 8's line by hand (a write rule 4
    refuses where it lands) and then reverts card 8 in a commit of its own: that commit
    deletes the line it wrote, never main's, so no delete of main's line shows: refused."""
    root, base = code
    point_main(root, hotfix := landed(root, base, 8))
    changed = commit_files(root, merge(root, base, hotfix, "Merge origin/main"),
                           "another line", {marker(8): f"claimed {TUE}\n"})
    status, out, _ = check(base, undone(root, changed, 8), "feat/x")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("feat/x", 0, 1)])


def test_a_delete_on_mains_own_line_is_none_the_change_brings(check, code):
    """Hotfix 8 lands on main, which reverts it and then reverts the revert (a history main's
    own rule 3 refuses, built here by hand), so main holds card 8's line.  A branch merging
    main by hand, dropping the marker and its work, brings no delete of its own: main's
    revert is main's, so refused."""
    root, base = code
    hotfix = landed(root, base, 8)
    point_main(root, back := commit_files(root, undone(root, hotfix, 8), 'Revert "Revert plan#8"',
                                          {marker(8): f"claimed {MON}\n", "w8.txt": "work\n"}))
    dropped = _resolved(root, base, base, back, "Merge origin/main, dropping card 8")
    status, out, _ = check(base, dropped, "feat/x")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("feat/x", 0, 1)])


def test_a_delete_on_devs_own_line_is_none_the_change_brings(check, code):
    """dev took card 8's line in by a copy and deleted it (built here by hand), while hotfix 8
    lands on main with that very line.  A branch merging main by hand, dropping the marker,
    brings no delete of its own: dev's delete is dev's, so refused."""
    root, base = code
    copy = commit_files(root, base, "a copy", {marker(8): f"claimed {MON}\n"})
    dev = commit_files(root, copy, "Revert the copy", {marker(8): None})
    point_main(root, hotfix := landed(root, base, 8))
    dropped = _resolved(root, dev, dev, hotfix, "Merge origin/main, dropping card 8")
    status, out, _ = check(dev, dropped, "feat/x")
    assert (status, out.splitlines()) == (1, [_dropped(8), verdict("feat/x", 0, 1)])


def test_a_delete_the_branch_took_back_shows_none_where_dev_holds_the_marker(check, code):
    """The copy history above (main took a copy of dev's card 7 line and deleted it) with a
    branch that deletes card 7's marker in a commit of its own and writes it back, then
    merges main's copy in: dev's line still holds the marker, so the change deletes none,
    and what moves their merge's answer is what it brings of main: refused."""
    root, base = code
    dev = commit_files(root, base, "claim plan#7", {marker(7): f"claimed {MON}\n"})
    copy = commit_files(root, base, "a copy", {marker(7): f"claimed {MON}\n"})
    point_main(root, commit_files(root, copy, "Revert plan#7", {marker(7): None}))
    gone = commit_files(root, dev, "not done", {marker(7): None})
    back = commit_files(root, gone, "done again", {marker(7): f"claimed {MON}\n"})
    status, out, _ = check(dev, merge(root, back, copy, "merge main's copy in"), "feat/x")
    assert (status, out.splitlines()) == (1, [_moved(
        7, "not shipped once this change and origin/main meet, where it reads shipped",
        "what it brings of origin/main moves their merge's answer, with no change to its "
        "record; leave out what it brings of origin/main"), verdict("feat/x", 0, 1)])


def test_a_branch_bringing_mains_delete_is_refused_where_main_took_the_line_back(check, code):
    """L1's review, its L2: the sentence for a card made shipped names who deleted the marker.
    Card 7 lands on dev and is released; main's hotfix deletes it and dev reverts it too.
    main then takes the very line back -- a revert of its revert, which main's own rule 3
    refuses but git can hold -- so dev and main merge with dev's delete.  A branch that
    merged main's hotfix in moves their merge onto main's line though it writes no marker:
    refused, naming dev's delete.  Once main reverts what took it back, the branch lands."""
    root, base = code
    dev = landed(root, base, 7)
    hotfix = undone(root, merge(root, base, dev, "release"), 7)
    point_main(root, commit_files(root, hotfix, 'Revert "Revert plan#7"',
                                  {marker(7): f"claimed {MON}\n", "w7.txt": "work\n"}))
    reverted = undone(root, dev, 7)
    feature = commit_files(root, merge(root, reverted, hotfix, "merge main in"), "work",
                           {"x.py": "x\n"})
    status, out, _ = check(reverted, feature, "feat/x")
    assert (status, out.splitlines()) == (1, [_moved(
        7, "shipped once this change and origin/main meet, as it does not",
        "origin/dev deleted it, and what this change brings of origin/main makes their merge "
        "take origin/main's line instead, which origin/main holds again after a commit of its "
        "history held none, with no change to its record; revert on origin/main what took it "
        "back (a revert deletes the marker with its code), or leave out what this change "
        "brings of origin/main"), verdict("feat/x", 0, 1)])
    point_main(root, undone(root, _git.TIPS[1], 7))
    assert check(reverted, feature, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


def test_a_branch_bringing_mains_copy_of_a_line_without_its_delete_is_refused(check, code):
    """Card 7's line reaches dev and main through two commits of their own (a copy, which
    rule 4 refuses where it lands, built here by hand), and main then deletes it: dev and
    main merge keeping dev's, since their merge base holds none.  A branch that merged
    main's copy in, and not its delete, moves their merge onto main's delete though it writes
    no marker: refused; the branch without main's copy lands."""
    root, base = code
    dev = commit_files(root, base, "claim plan#7", {marker(7): f"claimed {MON}\n"})
    copy = commit_files(root, base, "a copy", {marker(7): f"claimed {MON}\n"})
    point_main(root, commit_files(root, copy, "Revert plan#7", {marker(7): None}))
    feature = merge(root, dev, copy, "merge main's copy in")
    status, out, _ = check(dev, feature, "feat/x")
    assert (status, out.splitlines()) == (1, [_moved(
        7, "not shipped once this change and origin/main meet, where it reads shipped",
        "what it brings of origin/main moves their merge's answer, with no change to its "
        "record; leave out what it brings of origin/main"), verdict("feat/x", 0, 1)])
    alone = commit_files(root, dev, "work alone", {"x.py": "x\n"})
    assert check(dev, alone, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


def test_a_branch_moving_their_merge_onto_mains_line_over_devs_is_refused(check, code):
    """L1's widening's review, its L2: the arm where AFTER takes a line other than the
    change's own.  Card 7's line X is in both histories; dev changes it to Y, and main takes
    a copy of Y and then X back (each a history main's own rules 3 and 4 refuse, built here
    by hand), so dev and main merge keeping dev's Y.  A branch that merged main's copy in,
    and not what took X back, moves their merge onto main's X though it writes no marker:
    refused, saying their merge's answer moves."""
    root, base = code
    both = commit_files(root, base, "claim plan#7", {marker(7): f"claimed {MON}\n"})
    dev = commit_files(root, both, "claim plan#7 again", {marker(7): f"claimed {TUE}\n"})
    copy = commit_files(root, both, "a copy", {marker(7): f"claimed {TUE}\n"})
    point_main(root, commit_files(root, copy, "X back", {marker(7): f"claimed {MON}\n"}))
    status, out, _ = check(dev, merge(root, dev, copy, "merge main's copy in"), "feat/x")
    assert (status, out.splitlines()) == (1, [_moved(
        7, "shipped on another line once this change and origin/main meet than",
        "what it brings of origin/main moves their merge's answer, with no change to its "
        "record; leave out what it brings of origin/main"), verdict("feat/x", 0, 1)])


# ------------------------------------------------------- a marker that conflicts BEFORE


def test_a_resync_keeping_the_line_dev_shipped_again_lands(check, code):
    """The ruling's pass case: dev shipped card 7 again from a fresh claim while main's
    hotfix deleted its first line, so the two conflict on its marker.  A resync built by hand
    keeping dev's new line, judged where it landed and never held by main, settles it: it
    lands."""
    root, base = code
    dev, main_tip = _reshipped(root, base, 7)
    assert check(dev, _resolved(root, dev, dev, main_tip, "resync"), "resync")[:2] == (
        0, verdict("resync", 0, 0) + "\n")


@pytest.mark.parametrize(("line", "want"), [(MON, 1), (None, 0)],
                         ids=["the line dev undid", "main's delete"])
def test_a_resync_writing_a_conflicted_marker_is_judged_by_what_it_writes(check, code, line,
                                                                         want):
    """The same conflict, settled by writing the marker: back to the first line, which dev
    held once and undid (rule 3: refused), or main's delete (rule 2: it lands)."""
    root, base = code
    dev, main_tip = _reshipped(root, base, 7)
    written = commit_files(root, dev, "settle", {marker(7): line and f"claimed {line}\n"})
    status, out, _ = check(dev, _resolved(root, written, dev, main_tip, "resync"), "resync")
    assert status == want
    assert ("origin/dev held this very line once and undid it" in out) is (line is not None)


def test_a_resync_settling_a_conflict_on_a_line_main_undid_is_refused(check, code):
    """Two claims of card 7 (lines Y and Z) cross: dev merges Z into Y keeping Y, and main
    merges Y into Z also keeping Y, then main's hotfix deletes Y.  The two then have two
    merge bases whose own merge conflicts, so dev's and main's merge conflicts on the marker
    though main undid dev's very line.  A resync built by hand keeping Y would re-ship what
    main undid: refused, though the marker conflicted BEFORE."""
    root, base = code
    one = commit_files(root, base, "claim Y", {marker(7): f"claimed {MON}\n"})
    two = commit_files(root, base, "claim Z", {marker(7): f"claimed {TUE}\n"})
    dev = _resolved(root, one, one, two, "dev keeps Y")
    took = _resolved(root, one, two, one, "main takes Y")
    point_main(root, main_tip := commit_files(root, took, "hotfix", {marker(7): None}))
    status, out, _ = check(dev, _resolved(root, dev, dev, main_tip, "resync"), "resync")
    assert (status, out.splitlines()[0]) == (1, (
        "REFUSED -- plan/ships/7: origin/dev and origin/main conflict on it, and this change "
        "settles it on a line origin/main held once and undid, though it writes no line of "
        "it, so it would re-ship card 7 with no change to its record, and a release would "
        "count none of its code: a card undone ships again from a fresh claim (quill claim "
        "7), which writes a new line"))


@pytest.mark.parametrize("again", ["a fresh claim", "a revert of its revert"])
def test_a_branch_bringing_mains_delete_lands_where_main_shipped_the_card_again(check, code,
                                                                                again):
    """Card 7 lands on dev and is released; main's hotfix deletes it and dev reverts it too.
    main then ships it again with a new line -- from a fresh claim, or, in a history main's
    own rule 3 refuses but git can hold, a fresh claim reverted and its revert reverted --
    so dev and main conflict on its marker.  A branch that merged main's hotfix in settles
    the conflict on main's line, which main holds: card 7 shipped there, so it lands."""
    root, base = code
    dev = landed(root, base, 7)
    hotfix = undone(root, merge(root, base, dev, "release"), 7)
    shipped = landed(root, hotfix, 7, TUE)
    if again == "a revert of its revert":
        shipped = commit_files(root, undone(root, shipped, 7), 'Revert "Revert plan#7"',
                               {marker(7): f"claimed {TUE}\n", "w7.txt": "work\n"})
    point_main(root, shipped)
    reverted = undone(root, dev, 7)
    feature = commit_files(root, merge(root, reverted, hotfix, "merge main in"), "work",
                           {"x.py": "x\n"})
    assert check(reverted, feature, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


@pytest.mark.parametrize("into", ["dev", "main"])
def test_an_app_change_lands_while_dev_and_main_conflict_on_a_marker(check, code, into):
    """In a week dev and main conflict on card 7's marker, a change of app code alone, into
    either, leaves the conflict as it was: it lands, the tracker never read."""
    root, base = code
    dev, main_tip = _reshipped(root, base, 7)
    run(root, "update-ref", "refs/remotes/origin/dev", dev)
    onto = dev if into == "dev" else main_tip
    head = commit_files(root, onto, "an app change", {"app.py": "app\n"})
    assert check(onto, head, "feat/app", base=into)[:2] == (
        0, verdict("feat/app", 0, 0, into) + "\n")


def test_an_app_change_lands_while_a_file_meets_a_directory_at_a_marker(check, code):
    """L1's review, its L1: dev changes card 7's line while main makes ``plan/ships/7`` a
    directory (a stray rule 1 refuses wherever it is written, so built here by hand).  git
    names that conflict after each merge's own tips, so BEFORE's name and AFTER's differ; read
    by its path, it is one conflict before and after an app change, which lands."""
    root, base = code
    both = commit_files(root, base, "seven", {marker(7): f"claimed {MON}\n"})
    dev = commit_files(root, both, "dev", {marker(7): f"claimed {TUE}\n"})
    point_main(root, commit_files(root, commit_files(root, both, "main", {marker(7): None}),
                                  "a directory", {f"{marker(7)}/x": "x\n"}))
    head = commit_files(root, dev, "an app change", {"app.py": "app\n"})
    assert check(dev, head, "feat/app")[:2] == (0, verdict("feat/app", 0, 0) + "\n")


def test_a_delete_of_a_marker_main_changed_lands_though_the_two_then_conflict(check, code):
    """L1's review, its L3: rule 8 reads only a marker a change leaves, so a marker it writes
    stays rules 1-7's.  Card 7 lands on dev and is released; main's hotfix deletes it and main
    ships it again from a fresh claim.  A revert of card 7 on dev deletes dev's line, which
    main changed: a delete (rule 2), which lands, though dev and main then conflict on it."""
    root, base = code
    dev, hotfix = _hotfixed(root, base, 7)
    point_main(root, landed(root, hotfix, 7, TUE))
    reverted = undone(root, dev, 7)
    assert check(dev, reverted, "revert-7")[:2] == (0, verdict("revert-7", 1, 0) + "\n")
    assert _git.merge(root, (reverted, _git.TIPS[1])).conflicts == (marker(7),)


def test_a_conflict_a_change_brings_without_writing_it_refuses_to_judge(check, code):
    """Hotfix 8 lands on main (line W), is undone there, and ships again (line Z).  A branch
    that merged main's first landing in, dropping its marker by hand, would make dev and main
    conflict on it once it lands, where before they merge cleanly: whether card 8 shipped
    would be unknown, so nothing is judged (exit 2)."""
    root, base = code
    first = landed(root, base, 8)
    point_main(root, landed(root, undone(root, first, 8), 8, WED))
    dropped = commit_files(root, merge(root, base, first, "merge main in"), "drop it",
                           {marker(8): None})
    status, out, err = check(base, dropped, "feat/x")
    assert (status, out) == (2, "")
    assert ("merge with a conflict on plan/ships/8, which origin/dev and origin/main merge "
            "without, though this change writes none of them") in err


# ------------------------------------------- rule 1: a marker is read as M's tree reads it


def _gitlink(root, on, number):
    """A commit on ``on`` adding, at card ``number``'s marker, a submodule's commit (a
    gitlink, mode 160000) instead of a file; its sha."""
    run(root, "checkout", "--quiet", "--detach", on)
    run(root, "update-index", "--add", "--cacheinfo", f"160000,{on},{marker(number)}")
    run(root, "commit", "--quiet", "-m", f"a gitlink at plan/ships/{number}")
    return run(root, "rev-parse", "HEAD")


def _no_file(number):
    """Rule 1's refusal of a gitlink at card ``number``'s marker."""
    return (f"REFUSED -- plan/ships/{number}: a marker is a file, and what this change writes "
            f"there is none (a submodule's commit), which no reader counts as card {number}'s "
            "marker, so no change may add or change it")


def test_a_gitlink_at_a_cards_marker_is_no_marker_even_from_its_claim(check, code):
    """card/7 adds a gitlink at ``plan/ships/7``: every reader of the markers names it no
    card's (``_git.markers_in``), so rule 1 refuses it, and card/7 records nothing."""
    root, base = code
    status, out, _ = check(base, _gitlink(root, base, 7), "card/7")
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- card/7 adds or changes no plan/ships/7, so it records nothing, though a "
        "change from card/7 records card 7 (pushed again after its merge? later work is a "
        "card of its own)", _no_file(7), verdict("card/7", 1, 2)])


@pytest.mark.parametrize("beside", [False, True], ids=["alone", "beside a marker file"])
def test_a_gitlink_through_the_records_door_is_no_marker(check, code, beside):
    """A change into main adding a gitlink at ``plan/ships/2``, alone or beside card 3's
    marker file, adds something that is no card's marker, so it is no records-only change:
    rule 1 refuses the gitlink, card 3 may then be recorded only by card/3, and the tracker
    is never read."""
    root, base = code
    on = commit_files(root, base, "records", {marker(3): "landed before\n"}) if beside else base
    status, out, _ = check(base, _gitlink(root, on, 2), "records", base="main")
    assert (status, out.splitlines()) == (1, [_no_file(2), *(
        ["REFUSED -- plan/ships/3: records adds it, but only card 3's own claim, card/3, may "
         "record it"] if beside else []), verdict("records", 1 + beside, 1 + beside, "main")])
