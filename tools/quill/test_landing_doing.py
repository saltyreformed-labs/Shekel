"""CI's landing check over the file naming a card's task in progress, ``plan/doing/N`` (rulings
plan#2009 and plan#2022; ``tools/quill/_doing.py``): only ``card/N`` adds or changes it, a
release or a resync carries it, a delete passes from any branch, and an entry there that is no
card's file, or a file naming no task, is refused.

Graded hardest on a revert of a card's merge, which lands from a branch of no card and deletes
the file (ruling plan#2022), and on a re-claim writing the very line its first claim did, which
the marker's rule 3 would refuse.  A release and a resync carrying the file are graded where
they carry the task files beside it (``test_landing_tasks.py``, whose cards begin each task as
``quill start`` does).  Every history is built and judged, and every tracker answered, by the
builders the landing tests share (:mod:`tools.quill._histories`): card 7, a step with task 12
under it; card 8, a step with task 13 under it; and card 9, a step.  Each refusal's sentence is
held word for word once.
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import commit_files, point_dev, point_main, run
from tools.quill._git import doing_file
from tools.quill._histories import TUE, Lookup, built, card_node, claim, judging, merge, never
from tools.quill._histories import reader, start, tracker, undone, verdict


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


def _cards():
    """The tracker the module's docstring names, as GitHub answers it."""
    return Lookup(tracker(card_node(7), card_node(12, "task", parent=7), card_node(8),
                          card_node(13, "task", parent=8), card_node(9)))


def _not_own(card, who):
    """The refusal of a change from ``who`` that writes card ``card``'s file naming its task
    in progress, which is no claim of that card's and carries no line of the other branch."""
    return (f"REFUSED -- plan/doing/{card}: {who} writes it, but only card {card}'s own claim, "
            f"card/{card}, may add or change the file naming its task in progress (quill start "
            "writes it there), and a release or a resync carries one only through the merge "
            "that took it in")


def _stray(path):
    """The refusal of a change writing ``path``, which is no card's file naming its task in
    progress."""
    return (f"REFUSED -- {path}: no card's file naming its task in progress is this (one is a "
            "file, plan/doing/<card number>), so no change may add or change it")


# ------------------------------------------------------------- the card's own claim writes it


def test_a_claim_lands_its_own_file_naming_its_task_in_progress(check, code):
    """card/7 begins task 12 (``plan/doing/7`` names it) and finishes it: its own claim writes
    its own card's file, so it lands, the tracker read for its card and task alone."""
    root, base = code
    github = _cards()
    status, out, _ = check(base, built(root, base, 7, 12), "card/7", github=github)
    assert (status, out) == (0, verdict("card/7", 1, 0, tasks=1) + "\n")
    assert github.asked == [[7, 12]]


def test_a_re_claim_writes_the_very_line_its_first_claim_did(check, code):
    """Card 7 lands on dev with task 12 begun and finished, and dev reverts it.  A fresh claim
    begins task 12 again, a finishing task a re-claim does not file twice, so its file holds
    the very line dev held once and undid: no claim's own line, as a marker's is, so the
    marker's rule 3 is not applied and it lands with a fresh done file."""
    root, base = code
    reverted = undone(root, merge(root, base, built(root, base, 7, 12), "Merge card/7"), 7, 12)
    again = built(root, reverted, 7, 12, when=TUE)
    assert check(reverted, again, "card/7", github=_cards())[:2] == (
        0, verdict("card/7", 1, 0, tasks=1) + "\n")


@pytest.mark.parametrize("head", ["feat/x", None], ids=["a branch of no card", "a fork's"])
def test_a_cards_file_added_from_no_claim_is_refused_unread(check, code, head):
    """A branch that is no claim adds card 7's file naming task 12: only card/7 may, so it is
    refused, the tracker never read."""
    root, base = code
    added = commit_files(root, base, "begin it by hand", {doing_file(7): "plan#12\n"})
    who = head or "a branch of another repository"
    status, out, _ = check(base, added, head)
    assert (status, out.splitlines()) == (1, [_not_own(7, who), verdict(who, 0, 1)])


def test_a_cards_file_changed_from_no_claim_is_refused_unread(check, code):
    """Card 7 lands on dev, its file naming task 12.  A branch of no card changes it to name
    task 13: refused, the tracker never read."""
    root, base = code
    point_dev(root, landed := merge(root, base, built(root, base, 7, 12), "Merge card/7"))
    changed = commit_files(root, landed, "point it elsewhere", {doing_file(7): "plan#13\n"})
    status, out, _ = check(landed, changed, "feat/x")
    assert (status, out.splitlines()) == (1, [_not_own(7, "feat/x"), verdict("feat/x", 0, 1)])


def test_a_claim_writing_another_cards_file_is_refused(check, code):
    """card/9 writes card 7's file naming task 12, a card it does not claim: refused, naming
    card 7's own claim, though card/9 records its own card."""
    root, base = code
    written = commit_files(root, claim(root, base, 9)[1], "begin card 7's task",
                           {doing_file(7): "plan#12\n"})
    status, out, _ = check(base, written, "card/9", github=_cards())
    assert (status, out.splitlines()) == (1, [_not_own(7, "card/9"), verdict("card/9", 1, 1)])


# --------------------------------------------------------- a delete passes, from any branch


@pytest.mark.parametrize("head", ["revert-7", "feat/x", None],
                         ids=["a revert", "a branch of no card", "a fork's"])
def test_a_revert_of_a_cards_merge_deletes_its_file_from_any_branch_unread(check, code, head):
    """Card 7 lands on dev with its file naming task 12.  A revert of its merge deletes the
    file with its marker, work and done file, from a branch of no card (ruling plan#2022): a
    delete, which lands, the tracker never read."""
    root, base = code
    point_dev(root, landed := merge(root, base, built(root, base, 7, 12), "Merge card/7"))
    who = head or "a branch of another repository"
    assert check(landed, undone(root, landed, 7, 12), head)[:2] == (
        0, verdict(who, 1, 0, tasks=1) + "\n")


def test_a_cards_file_deleted_alone_lands_unread(check, code):
    """Card 7 lands on dev with its file naming task 12.  A branch of no card deletes that
    file alone: a delete, which lands, the tracker never read."""
    root, base = code
    point_dev(root, landed := merge(root, base, built(root, base, 7, 12), "Merge card/7"))
    cleared = commit_files(root, landed, "clear it", {doing_file(7): None})
    assert check(landed, cleared, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


# ---------------------------------------------------- a merge of the other, a copy of its line


def test_a_claim_that_merged_main_in_carries_a_hotfixs_file(check, code):
    """Hotfix card 8 lands on main with its file naming task 13.  card/7, which merged main
    in, lands card 8's file with its own: carried from main, so only card 7 and task 12 are
    read, and it lands."""
    root, base = code
    point_main(root, hotfix := merge(root, base, built(root, base, 8, 13), "Merge card/8"))
    own = built(root, base, 7, 12)
    github = _cards()
    status, out, _ = check(base, merge(root, own, hotfix, "merge main in"), "card/7",
                           github=github)
    assert (status, out) == (0, verdict("card/7", 2, 0, tasks=2) + "\n")
    assert github.asked == [[7, 12]]


def test_a_copy_of_the_file_dev_took_in_is_refused(check, code):
    """Card 7 lands on dev with its file naming task 12.  A change into main copying that file
    alone holds dev's line without the merge that took it in: refused."""
    root, base = code
    point_dev(root, merge(root, base, built(root, base, 7, 12), "Merge card/7"))
    copied = commit_files(root, base, "copy", {doing_file(7): "plan#12\n"})
    status, out, _ = check(base, copied, "copy", base="main")
    assert (status, out.splitlines()) == (1, [_not_own(7, "copy"),
                                              verdict("copy", 0, 1, "main")])


# ------------------------------------------------- what is no card's file, or names no task


@pytest.mark.parametrize("path", ["plan/doing/notes", "plan/doing/012", "plan/doing/7/inner",
                                  "plan/doing"])
def test_an_entry_that_is_no_cards_file_is_refused_and_its_delete_lands(check, code, path):
    """A name that is no card number, a leading zero, a directory below, and the directory
    itself a file are no card's file naming its task in progress: refused from any branch; a
    change deleting one lands, the tracker never read."""
    root, base = code
    status, out, _ = check(base, commit_files(root, base, "stray", {path: "plan#12\n"}), "feat/x")
    assert (status, out.splitlines()) == (1, [_stray(path), verdict("feat/x", 0, 1)])
    point_dev(root, stray := commit_files(root, base, "stray", {path: "plan#12\n"}))
    assert check(stray, commit_files(root, stray, "clean", {path: None}), "clean")[0] == 0


def test_a_gitlink_at_a_cards_file_is_no_file(check, code):
    """A submodule's commit (a gitlink) at ``plan/doing/7``: every reader of the tree names it
    no card's file, so it is refused."""
    root, base = code
    run(root, "checkout", "--quiet", "--detach", base)
    run(root, "update-index", "--add", "--cacheinfo", f"160000,{base},{doing_file(7)}")
    run(root, "commit", "--quiet", "-m", "a gitlink at plan/doing/7")
    status, out, _ = check(base, run(root, "rev-parse", "HEAD"), "feat/x")
    assert (status, out.splitlines()) == (1, [_stray(doing_file(7)), verdict("feat/x", 0, 1)])


@pytest.mark.parametrize("line", ["task 12\n", "plan#12\nplan#13\n", "plan#012\n", ""],
                         ids=["no plan#", "two lines", "a leading zero", "empty"])
def test_a_file_naming_no_task_is_refused_from_its_own_claim(check, code, line):
    """card/7 writes its own file, but with a line naming no task, which ``quill start``
    never writes and every reader takes as nothing in progress, silently: refused."""
    root, base = code
    tip = built(root, base, 7, 12)
    written = commit_files(root, tip, "a hand-written line", {doing_file(7): line})
    status, out, _ = check(base, written, "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/doing/7: what this change writes there names no task, which reads as "
        "nothing in progress on card/7, silently: the file is one line, plan#<task number>, as "
        "quill start writes it", verdict("card/7", 1, 1, tasks=1)])
