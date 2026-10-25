"""``quill file`` under ruling ``balance:R-BAL202``: every card it creates is born marked
``filing``, the mark comes off with its filing's last write, and a card still marked is
never offered, is named by ``next`` and ``sync``, and is finished by the same command --
for a leaf, its move made again -- over :class:`_fake.FakeTracker` and a throwaway git
repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill._fake import (AnswerLostOnce, FailOnce, FakeTracker, claim, leaf_filing,
                              ruling_filing, run, ship)
from tools.quill._github import GitHubError
from tools.quill._tracker import Child
from tools.quill.setup_tracker import FILING


def test_a_leaf_and_a_ruling_are_created_marked_and_unmarked_by_their_last_write(code,
                                                                                tmp_path):
    """R-BAL202: a leaf is created marked, linked, added, moved, its split step taken off the
    board, then unmarked; a ruling is created marked, blocked by its owner (R-BAL395),
    closed, then unmarked.  (A
    finding's and a question's writes are pinned in ``test_quill_file.py``.)"""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    assert run(tracker, code, *leaf_filing(tmp_path, "half", "plan#2")) == 0
    assert tracker.writes == [("create", 4, "step", "half", ("balance", FILING)),
                              ("add_child", 2, 4), ("board_add", 4), ("board_place", 4, "PVTI_2"),
                              ("board_remove", 2), ("unmark", 4)]
    tracker.writes.clear()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#4")) == 0
    assert tracker.writes == [("create", 5, "ruling", "Home", ("balance", FILING)),
                              ("block", 5, 4), ("close", 5, "completed"), ("unmark", 5)]


class _HandUnmarks:
    """``board.add`` after which a person removes the card's mark by hand on the web (as
    ``quill show`` advises for a filing whose command is lost), while its command still runs."""

    def __init__(self, tracker):
        """Stand in for ``tracker.board.add``."""
        self.tracker, self.real = tracker, tracker.board.add
        tracker.board.add = self

    def __call__(self, card):
        """Add the card, then take its mark off."""
        item = self.real(card)
        held = self.tracker.cards_by_number[card.number]
        self.tracker.cards_by_number[card.number] = replace(
            held, labels=tuple(label for label in held.labels if label != FILING))
        return item


def test_a_mark_removed_by_hand_mid_filing_fails_its_unmark_and_the_same_command_ends_it(
        code, tmp_path, capsys):
    """Review rbal202b M1 (c), LOW 4: the tool's unmark finds the mark already gone (a person,
    another session, or the label deleted) and GitHub answers 404, a failed call (exit 2)
    after every other write landed; the same command run again finds the step filed and
    writes nothing.  (A ruling's: ``test_quill_file_ended.py``.)"""
    tracker = FakeTracker()
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    _HandUnmarks(tracker)
    assert run(tracker, code, *args) == 2
    assert "Label does not exist" in capsys.readouterr().err
    assert tracker.writes == [("create", 1, "step", "Alone", ("balance", FILING)),
                              ("board_add", 1)]
    tracker.writes.clear()
    assert run(tracker, code, *args) == 0
    assert not tracker.writes and "filed already" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["link", "add", "move", "read-back", "remove", "unmark"])
def test_a_leaf_cut_short_after_its_create_is_never_offered_and_the_same_command_finishes_it(
        code, tmp_path, capsys, failure):
    """Review cp5 MEDIUM-1, R-BAL202: plan#1 waits on question plan#9.  Its leaf's filing
    stops at each write after the create in turn (a failed read-back after the create is
    pinned, for a question, in ``test_quill_file.py``); until the same command runs again,
    ``quill next`` names the leaf as an unfinished filing and never offers it, and ``quill
    claim`` refuses it.  Under R-BAL201's order (link last), a stop at the move, its
    read-back or the link left the leaf a top-level step, offered and claimed past
    plan#1's wait."""
    tracker = FakeTracker()
    tracker.add(9, "question")
    tracker.add(1, blocked_by=(9,))
    if failure == "link":
        FailOnce(tracker, "add_child")
    elif failure == "move":
        FailOnce(tracker.board, "place")
    elif failure == "read-back":
        AnswerLostOnce(tracker.board, "place")
    elif failure == "unmark":
        FailOnce(tracker, "unmark")
    else:
        FailOnce(tracker.board, failure)
    args = leaf_filing(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.startswith("next: nothing\n")
    assert "UNFINISHED FILING: plan#10's filing has not finished" in out
    assert run(tracker, code, "claim", "plan#10") == 1
    assert "its filing has not finished (R-BAL202)" in capsys.readouterr().err
    assert run(tracker, code, *args) == 0
    card = tracker.cards_by_number[10]
    assert (card.parent, card.labels, tracker.board.items) == (1, ("balance",), [9, 10])
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"


def test_dropping_the_split_step_reaches_a_marked_leaf_linked_under_it(code, tmp_path, capsys):
    """Review cp5 MEDIUM-1, R-BAL202: the leaf is linked by its second write, so a drop of the
    split step drops it too, and a dropped card's unfinished filing is moot."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker.board, "place")
    assert run(tracker, code, *leaf_filing(tmp_path, "Trailer check")) == 2
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert not tracker.cards_by_number[2].is_open
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"


def test_a_leaf_cut_short_before_its_link_is_reported_after_its_split_step_is_dropped(
        code, tmp_path, capsys):
    """Review cp5 MEDIUM-1, R-BAL202: the link failed, so the drop of the split step cannot
    reach the leaf, and the same command is refused (its parent is not live); the leaf is
    never offered, and ``next`` and ``sync`` name it until ``quill drop`` drops it -- under
    R-BAL201's order it was offered as work, and sync said nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    args = leaf_filing(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "a step's parent is the LIVE step it splits" in capsys.readouterr().err
    assert run(tracker, code, "next") == 0
    assert ("UNFINISHED FILING: plan#2's filing has not finished, so it is never offered "
            "(R-BAL202): unless a `quill file` command is filing it now, run that command again "
            "to finish it, or `quill drop` it; with that command lost, `quill show plan#2` says "
            "how to finish it\n") in capsys.readouterr().out
    assert run(tracker, code, "sync", "--dry-run") == 1
    assert "REPORT: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, "drop", "plan#2", "--why", "replanned") == 0
    assert run(tracker, code, "sync", "--dry-run") == 0


def _split(tracker):
    """plan#1, split into plan#2 and plan#3 and off the board; plan#4 below its leaves."""
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, parent=1)
    tracker.add(4)


def test_a_card_added_below_a_leaf_cut_short_leaves_its_move_to_the_same_command(code,
                                                                                tmp_path):
    """Review cp4e M1 (a), R-BAL202: whatever joined the board below the leaf, the re-run
    moves it after the leaves filed before it."""
    tracker = FakeTracker()
    _split(tracker)
    FailOnce(tracker.board, "place")
    args = leaf_filing(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    tracker.add(6)
    assert tracker.board.items == [2, 3, 4, 5, 6]
    assert run(tracker, code, *args) == 0
    assert tracker.board.items == [2, 3, 5, 4, 6]


def test_no_leaf_is_filed_while_another_leaf_of_its_split_step_is_unfinished(code, tmp_path,
                                                                            capsys):
    """Review cp4e M1 (b), R-BAL204: plan#5's move failed, so it may sit anywhere; plan#6 is
    refused, naming it, with nothing written, until plan#5's own command finishes it --
    then the board holds the leaves in their filing order."""
    tracker = FakeTracker()
    _split(tracker)
    FailOnce(tracker.board, "place")
    first = leaf_filing(tmp_path, "First")
    assert run(tracker, code, *first) == 2
    writes = list(tracker.writes)
    capsys.readouterr()
    second = leaf_filing(tmp_path, "Second")
    assert run(tracker, code, *second) == 1
    err = capsys.readouterr().err
    assert ("refused: the filing of plan#5, of plan#1's leaves, has not finished (R-BAL204): "
            "finish it first") in err
    assert "Unless a `quill file` command is filing it now, run that command again" in err
    assert "make its missing writes on the web and remove its 'filing' label last" in err
    assert "(`quill show` shows its parent and board place)" in err
    assert tracker.writes == writes
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [2, 3, 5, 6, 4]


def test_a_first_leaf_whose_link_failed_goes_above_the_leaf_that_took_its_place(code,
                                                                                tmp_path):
    """Review cp4e M1 (c), R-BAL204 (its second answer: the rule placing a leaf just above
    the leaves filed after it is kept): plan#5's link failed, so it was no leaf of plan#1
    and plan#6 was not refused; plan#6 took plan#1's place and took plan#1 off the board;
    plan#5's re-run links it, adds it, and puts it just above plan#6."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(4)
    FailOnce(tracker, "add_child")
    first = leaf_filing(tmp_path, "First")
    assert run(tracker, code, *first) == 2
    assert run(tracker, code, *leaf_filing(tmp_path, "Second")) == 0
    assert tracker.board.items == [6, 4]
    assert run(tracker, code, *first) == 0
    assert tracker.board.items == [5, 6, 4]
    assert [child.number for child in tracker.cards_by_number[1].children] == [6, 5]


def test_a_leaf_whose_move_into_its_split_steps_place_failed_is_finished_first(code, tmp_path,
                                                                              capsys):
    """Review cp4e M1 (c), R-BAL204: plan#5's move into plan#1's place failed, so plan#1 is
    still on the board and plan#6 is refused until plan#5's re-run puts plan#5 in plan#1's
    place; plan#6 then follows it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(4)
    FailOnce(tracker.board, "place")
    first = leaf_filing(tmp_path, "First")
    second = leaf_filing(tmp_path, "Second")
    assert run(tracker, code, *first) == 2
    assert tracker.board.items == [1, 4, 5]
    assert run(tracker, code, *second) == 1
    assert "has not finished (R-BAL204)" in capsys.readouterr().err
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [5, 6, 4]


def test_a_placed_leaf_whose_unmark_failed_holds_the_next_leaf_until_it_is_finished(
        code, tmp_path, capsys, monkeypatch):
    """Review rbal202a M3, R-BAL204: plan#9 is in its right place with only its unmark
    missing; plan#10 is refused -- the mark read by plan#9's card number, so neither
    listing (marked cards, open cards) having caught up changes anything -- and once plan#9
    is finished
    plan#10 follows it: [7, 9, 10, 8], where the leaf-A build gave [7, 8, 9, 10]."""
    tracker = FakeTracker()
    for number in (7, 1, 8):
        tracker.add(number)
    FailOnce(tracker, "unmark")
    first = leaf_filing(tmp_path, "First")
    second = leaf_filing(tmp_path, "Second")
    assert run(tracker, code, *first) == 2
    assert tracker.board.items == [7, 9, 8]
    real_marked, real_open = tracker.marked, tracker.open_cards
    monkeypatch.setattr(tracker, "marked", dict)
    monkeypatch.setattr(tracker, "open_cards", lambda: {
        number: card for number, card in real_open().items() if number != 9})
    capsys.readouterr()
    assert run(tracker, code, *second) == 1
    assert "the filing of plan#9, of plan#1's leaves, has not finished" in capsys.readouterr().err
    monkeypatch.setattr(tracker, "marked", real_marked)
    monkeypatch.setattr(tracker, "open_cards", real_open)
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [7, 9, 10, 8]


def test_a_step_filed_at_the_top_level_is_never_refiled_as_a_leaf(code, tmp_path, capsys):
    """Review cp4e M1 (d), cp5 LOW 6, R-BAL202: its filing finished at the top level, so the
    same title and text filed with a parent is a re-homing, a person's call: refused with
    nothing written, where b5213af4d linked it and left it below the leaves."""
    tracker = FakeTracker()
    _split(tracker)
    tracker.add(5, body="Build Trailer check.", title="Trailer check")
    assert run(tracker, code, *leaf_filing(tmp_path, "Trailer check")) == 1
    assert ("its filing finished at the top level, not under plan#1: re-homing a card is done "
            "by hand") in capsys.readouterr().err
    assert not tracker.writes


def test_a_split_step_a_leaf_filing_left_on_the_board_takes_no_leaf_above_it(code, tmp_path,
                                                                               capsys):
    """Review cp5 LOW 4, R-BAL204: plan#4's filing stopped before taking plan#2 off the board;
    plan#5 is refused until plan#4's re-run takes plan#2 off, so it never lands above
    plan#4 in plan#2's place."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    FailOnce(tracker.board, "remove")
    first = leaf_filing(tmp_path, "First", "plan#2")
    second = leaf_filing(tmp_path, "Second", "plan#2")
    assert run(tracker, code, *first) == 2
    assert tracker.board.items == [1, 2, 4, 3]
    assert run(tracker, code, *second) == 1
    assert "has not finished (R-BAL204)" in capsys.readouterr().err
    assert run(tracker, code, *first) == 0
    assert run(tracker, code, *second) == 0
    assert tracker.board.items == [1, 4, 5, 3]


def test_a_ruling_closed_but_still_marked_is_finished_by_its_unmark_alone(code, tmp_path,
                                                                          capsys):
    """R-BAL202: a ruling's filing closes it before the mark comes off, so the closed, marked
    ruling is the unfinished filing -- named by ``next`` and ``sync``, which read it from
    the marked cards, since the open ones no longer hold it -- and the re-run closes nothing
    twice."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    args = ruling_filing(tmp_path, "plan#1")
    assert run(tracker, code, *args) == 2
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "UNFINISHED FILING: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, "sync", "--dry-run") == 1
    assert "REPORT: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, *args) == 0
    assert tracker.writes == [*writes, ("unmark", 2)]
    assert "  closed already\n" in capsys.readouterr().out


def test_no_leaf_conversion_or_leaf_move_rides_on_a_filing_that_has_not_finished(code,
                                                                                tmp_path,
                                                                                capsys):
    """R-BAL202: a leaf under a step still marked would make it a split step its own
    filing's re-run puts on the board (plan#1 is marked and off the board, as after a failed
    board add); a question still marked is converted only once its filing finishes; and a
    marked step is not moved by hand, since if it is a leaf (one whose link failed reads as
    a top-level step) its re-run moves it again -- plan#1 is off the board, so the move
    would add it.  A finding under a marked step, and a move of a marked question, which
    no re-run moves, are not refused."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", FILING), on_board=False)
    tracker.add(2, "question", body="Ship it?", title="Ship", labels=("balance", FILING))
    tracker.add(3)
    tracker.add(4, children=(Child(5, "step", True),))
    tracker.add(5, parent=4, labels=("balance", FILING))
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 1
    assert "plan#1 [step, balance] card 1's own filing has not finished" in (
        capsys.readouterr().err)
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship",
               "--owner", "plan#3", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 1
    assert "Ship's filing has not finished (R-BAL202): finish it with the `quill file` " in (
        capsys.readouterr().err)
    assert run(tracker, code, "move", "plan#5", "--top") == 1
    assert "its `quill file` command moves it again after this move" in capsys.readouterr().err
    assert run(tracker, code, "move", "plan#1", "--top") == 1
    assert "one whose link has not landed reads as a top-level step" in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Twice",
               "--owner", "plan#1", "--text", "The report counts a refund twice.") == 0


def test_a_card_dropped_before_its_filing_finished_is_never_finished(code, tmp_path, capsys):
    """R-BAL202: a card closed while marked was dropped, and what its filing left undone is
    moot -- it is neither named as unfinished nor finished by the same command.  Run again,
    that command is refused, nothing written, saying how to restore the card: the drop is a
    decision, and filing over it would undo it (leaf C, review M3; it filed a new card until
    then, but only when no listing lagged the drop)."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    args = ("file", "question", "--arc", "recurrence", "--title", "Day", "--body-file",
            str(text))
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, "drop", "plan#1", "--why", "asked elsewhere") == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"
    writes = list(tracker.writes)
    assert run(tracker, code, *args) == 1
    assert "was closed as not planned before its filing finished" in capsys.readouterr().err
    assert tracker.writes == writes
    assert tracker.cards_by_number[1].labels == ("filing", "recurrence")


@pytest.mark.parametrize("closer", ["drop", "person"])
def test_a_ruling_withdrawn_before_its_filing_finished_is_no_unfinished_filing(code, tmp_path,
                                                                               capsys, closer):
    """Review rbal202a M2: only the tool's own close as completed is a ruling filing's close;
    a ruling dropped, or closed by a person, while marked was withdrawn, so ``sync`` stops
    naming it -- it named it on every run, and the one exit it offered re-linked the
    withdrawn ruling to its owner."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "block")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, "sync", "--dry-run") == 1
    reports = [line for line in capsys.readouterr().out.splitlines() if "plan#2" in line]
    assert len(reports) == 1 and "filing has not finished" in reports[0]
    if closer == "drop":
        assert run(tracker, code, "drop", "plan#2", "--why", "asked again") == 0
    else:
        tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=False,
                                             state_reason="NOT_PLANNED", closed_by_tool=False,
                                             touched_by_hand=True)
    capsys.readouterr()
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert "plan#2" not in capsys.readouterr().out


def test_dropping_a_leaf_whose_filing_never_finished_undoes_the_split(code, tmp_path,
                                                                       capsys):
    """Review rbal202a M1, R-BAL205: S (plan#1) is split by its first leaf plan#3, linked, and
    the board add fails; dropping plan#3 unlinks it before closing it, so S is the plain step
    it was, offered as work, and Q (plan#2) keeps waiting on S -- where R-BAL187 dropped S
    and offered Q, though nobody built S."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, blocked_by=(1,))
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *leaf_filing(tmp_path, "Trailer check")) == 2
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "(which unlinks it from plan#1 first: it was never part of that split, R-BAL205)" in (
        capsys.readouterr().out)
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#3", "--why", "not splitting after all") == 0
    assert tracker.writes[len(writes):] == [
        ("remove_child", 1, 3), ("comment", 3, "Dropped: not splitting after all"),
        ("close", 3, "not_planned")]
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#1 ")
    assert run(tracker, code, "sync", "--dry-run") == 0


def test_a_marked_leaf_dropped_with_its_split_step_is_unlinked_before_the_step_closes(
        code, tmp_path):
    """R-BAL205: dropping the split step itself is the decision; its marked leaf is dropped
    with it.  Closed while marked, the leaf was never part of the split, so no leaf carries
    the drop and the split step is closed itself (``left_bare``, leaf C).  Leaf C2 (review
    M1) reverses leaf A's "not unlinked": the leaf is unlinked after its close and before
    the step's, since no sync reaches a closed split step to do it, and a person reopening
    the leaf would make it a leaf again and revive the dropped step."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *leaf_filing(tmp_path, "Trailer check")) == 2
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2), ("remove_child", 1), ("close", 1)]
    assert tracker.writes[-1] == ("close", 1, "not_planned")
    assert tracker.cards_by_number[2].parent is None and not tracker.cards_by_number[2].is_open


def test_a_marked_leaf_shows_its_filing_until_it_finishes(code, tmp_path, capsys):
    """R-BAL202, review rbal202a LOW 9: ``quill show`` says the filing has not finished, and
    how to finish it by hand; once it finishes it says nothing (a card dropped while marked:
    ``test_a_dropped_card_still_carrying_the_mark_shows_no_unfinished_filing``)."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker.board, "add")
    args = leaf_filing(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 2
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#2") == 0
    assert "\n  filing: not finished (R-BAL202) -- never offered until its `quill file` " in (
        capsys.readouterr().out)
    assert run(tracker, code, *args) == 0
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#2") == 0
    assert "filing:" not in capsys.readouterr().out


def test_a_dropped_card_still_carrying_the_mark_shows_no_unfinished_filing(code, capsys):
    """R-BAL202: a card closed as not planned while marked was dropped; what its filing left
    undone is moot, so ``quill show`` says nothing of it."""
    tracker = FakeTracker()
    tracker.add(5, labels=("balance", FILING), is_open=False, state_reason="NOT_PLANNED",
                closed_by_tool=True)
    assert run(tracker, code, "show", "plan#5") == 0
    assert "filing:" not in capsys.readouterr().out


def test_a_ruling_a_person_closed_as_completed_is_finished_by_its_command(code, tmp_path,
                                                                          capsys):
    """R-BAL206: the ruling's link failed and a person closed it with the web's default close,
    completed; it is still an unfinished filing, named by ``next``, and its command links it
    to its owner (blocked by it, R-BAL395) and removes the mark, filing no second ruling."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "block")
    args = ruling_filing(tmp_path, "plan#1")
    assert run(tracker, code, *args) == 2
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=False,
                                         state_reason="COMPLETED", closed_by_tool=False,
                                         touched_by_hand=True)
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "UNFINISHED FILING: plan#2's filing has not finished" in capsys.readouterr().out
    assert run(tracker, code, *args) == 0
    rulings = [card for card in tracker.cards_by_number.values() if card.kind == "ruling"]
    assert [(card.number, card.parent, card.blocked_by, card.labels)
            for card in rulings] == [(2, None, (1,), ("balance",))]
    assert tracker.writes.count(("block", 2, 1)) == 1


def test_a_re_run_waits_on_another_leaf_whose_filing_has_not_finished(code, tmp_path, capsys):
    """Review rbal202a2 LOW 1, R-BAL204 applied to a re-run: M's link failed, so L was filed
    into S's place, and L's unmark failed; M's re-run is refused until L is finished, and
    the leaves end in their filing order, [A, M, L, X] -- without the refusal on a re-run M
    was linked beside the unfinished L and the board ended [A, X, M, L]."""
    tracker = FakeTracker()
    for number in (3, 1, 4):
        tracker.add(number)
    first, second = leaf_filing(tmp_path, "M"), leaf_filing(tmp_path, "L")
    FailOnce(tracker, "add_child")
    assert run(tracker, code, *first) == 2
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *second) == 2
    assert tracker.board.items == [3, 6, 4]
    capsys.readouterr()
    assert run(tracker, code, *first) == 1
    assert "the filing of plan#6, of plan#1's leaves, has not finished" in capsys.readouterr().err
    assert run(tracker, code, *second) == 0
    assert run(tracker, code, *first) == 0
    assert tracker.board.items == [3, 5, 6, 4]


def test_only_an_unfinished_leaf_holds_a_new_leaf(code, tmp_path):
    """R-BAL204: a marked leaf off the board holds the next leaf; a leaf a person closed while
    marked was dropped and holds nothing; a marked finding under the split step is no leaf
    and holds nothing; and a finding filed under the split step is never held."""
    marked = ("balance", FILING)
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False), Child(3, "finding", True),
                             Child(4, "step", True)), on_board=False)
    tracker.add(2, parent=1, labels=marked, is_open=False, state_reason="NOT_PLANNED",
                touched_by_hand=True, on_board=False)
    tracker.add(3, "finding", parent=1, labels=marked)
    tracker.add(4, parent=1)
    assert run(tracker, code, *leaf_filing(tmp_path, "Free")) == 0
    tracker.add(9, children=(Child(10, "step", True),), on_board=False)
    tracker.add(10, parent=9, labels=marked, on_board=False)
    assert run(tracker, code, *leaf_filing(tmp_path, "Held", "plan#9")) == 1
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Twice",
               "--owner", "plan#9", "--text", "The report counts a refund twice.") == 0


def test_a_finished_leaf_a_lagging_listing_still_shows_marked_is_left_alone(code, tmp_path,
                                                                            capsys,
                                                                            monkeypatch):
    """Review rbal202a2 LOW 9: the matched card's mark is read again by its number, so a
    listing of marked cards that has not caught up with the unmark neither moves the leaf
    again nor sends an unmark the card no longer carries."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(4)
    args = leaf_filing(tmp_path, "Trailer check")
    assert run(tracker, code, *args) == 0
    stale = replace(tracker.cards([5])[5], labels=("balance", FILING))
    monkeypatch.setattr(tracker, "marked", lambda: {5: stale})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    assert tracker.writes == writes
    assert ("Trailer check is filed already: a card with this kind, title and text exists and "
            "its filing finished (it carries no filing mark), so nothing is written") in (
        capsys.readouterr().out)


def test_the_fake_tracker_refuses_an_unlink_from_another_parent_as_github_does():
    """The fake answers an unlink of a card that is no sub-issue of the parent named as
    GitHub answered it (``recorded/unlink.json``): 403, and nothing written.  (Leaf C2:
    it promised the tool never sends one, but a read lagging the tool's own unlink makes
    ``sync`` send it.)"""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    card = tracker.add(2, parent=3)
    with pytest.raises(GitHubError, match="403"):
        tracker.remove_child(1, card)
    assert not tracker.writes and tracker.cards_by_number[2].parent == 3


def test_a_lone_leaf_dropped_after_its_split_step_left_the_board_puts_it_back(code, tmp_path,
                                                                              capsys):
    """Review rbal202a3 M1, R-BAL205: every write of S's (plan#2's) only leaf landed but its
    unmark, so S had left the board; dropping the leaf puts S back on the board in its
    place, before the unlink, so S is the plain step it was, offered as work, and Q
    (plan#4) keeps waiting on it -- S was left off the board, offered by nothing."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    tracker.add(4, blocked_by=(2,), on_board=False)
    claim(code, 1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 2
    assert tracker.board.items == [1, 5, 3]
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#5", "--why", "not splitting after all") == 0
    assert tracker.writes[len(writes):] == [
        ("board_add", 2), ("board_place", 2, "PVTI_5"), ("remove_child", 2, 5),
        ("comment", 5, "Dropped: not splitting after all"), ("close", 5, "not_planned")]
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#2 ")


def test_a_drop_whose_unlink_failed_after_the_board_add_is_finished_by_the_same_drop(
        code, tmp_path):
    """R-BAL205: the split step is back on the board before the unlink, so when the unlink
    fails the leaf is still linked, and the same drop run again unlinks and drops it,
    adding the split step to the board no second time."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 2
    FailOnce(tracker, "remove_child")
    args = ("drop", "plan#4", "--why", "not splitting after all")
    assert run(tracker, code, *args) == 2
    assert tracker.cards_by_number[4].parent == 2 and tracker.board.items == [1, 4, 2, 3]
    assert run(tracker, code, *args) == 0
    assert [write[0] for write in tracker.writes].count("board_add") == 2
    assert tracker.cards_by_number[4].parent is None
    assert not tracker.cards_by_number[2].step_children


def test_only_a_marked_leaf_is_unlinked_and_shipped_work_never(code, capsys):
    """R-BAL205: a marked finding is no leaf, so its drop unlinks nothing; a marked leaf git
    says shipped is refused before any write."""
    marked = ("balance", FILING)
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "finding", True), Child(3, "step", True)))
    tracker.add(2, "finding", parent=1, labels=marked)
    tracker.add(3, parent=1, labels=marked)
    assert run(tracker, code, "drop", "plan#2", "--why", "a duplicate") == 0
    assert not [write for write in tracker.writes if write[0] == "remove_child"]
    ship(code, 3)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#3", "--why", "late") == 1
    assert "shipped in git" in capsys.readouterr().err and tracker.writes == writes


def test_a_matched_card_its_number_cannot_read_yet_is_a_failed_call(code, tmp_path, capsys,
                                                                    monkeypatch):
    """Review rbal202a3 LOW 5: the listing holds the matched card but its read by number does
    not yet; the run fails closed, writing nothing, rather than trusting the listing's mark."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(5, parent=1, title="Trailer check", body="Build Trailer check.",
                labels=("balance", FILING), on_board=False)
    tracker.cards_by_number[1] = replace(tracker.cards_by_number[1],
                                         children=(Child(5, "step", True),))
    real_cards, real_body, matched = tracker.cards, tracker.body, []
    monkeypatch.setattr(tracker, "body", lambda number: matched.append(number) or (
        real_body(number)))
    monkeypatch.setattr(tracker, "cards", lambda numbers: {
        n: c for n, c in real_cards(numbers).items() if not (n == 5 and 5 in matched)})
    assert run(tracker, code, *leaf_filing(tmp_path, "Trailer check")) == 2
    assert "a read by its number does not hold it" in capsys.readouterr().err
    assert not tracker.writes


def test_a_drop_whose_board_add_failed_unlinked_nothing_and_is_finished_by_the_same_drop(
        code, tmp_path, capsys):
    """R-BAL205: the split step's board add comes before the unlink, so when the add fails the
    leaf is still linked, and the same drop run again puts the split step back and offers
    it -- with the unlink first, the re-run would find the leaf unlinked and leave the split
    step off the board."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    claim(code, 1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 2
    FailOnce(tracker.board, "add")
    args = ("drop", "plan#4", "--why", "not splitting after all")
    assert run(tracker, code, *args) == 2
    assert tracker.cards_by_number[4].parent == 2
    assert run(tracker, code, *args) == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#2 ")


def test_a_marked_leaf_dropped_beside_finished_leaves_leaves_its_split_step_split(code,
                                                                                  tmp_path):
    """R-BAL205: plan#2's first leaf finished and its second's unmark failed; dropping the
    second unlinks it, and plan#2, still split by the first, stays off the board."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    assert run(tracker, code, *leaf_filing(tmp_path, "First", "plan#2")) == 0
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Second", "plan#2")) == 2
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#5", "--why", "one leaf is enough") == 0
    assert [write[0] for write in tracker.writes[len(writes):]] == ["remove_child", "comment",
                                                                   "close"]
    assert tracker.cards_by_number[2].step_children == (4,) and 2 not in tracker.board.items


def test_a_closed_or_non_step_parent_is_never_put_on_the_board(code):
    """Review rbal202a4 LOW 1, R-BAL205: only an open step whose only leaf is dropped is a
    plain step again; a split step a person closed, or a finding a marked step was linked
    under by hand, is unlinked from but never put on the board."""
    marked = ("balance", FILING)
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False, is_open=False,
                state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.add(2, parent=1, labels=marked)
    tracker.add(3, "finding", children=(Child(4, "step", True),))
    tracker.add(4, parent=3, labels=marked)
    assert run(tracker, code, "drop", "plan#2", "--why", "gone") == 0
    assert run(tracker, code, "drop", "plan#4", "--why", "gone") == 0
    assert [write[0] for write in tracker.writes] == ["remove_child", "comment", "close"] * 2


def test_a_lone_leaf_off_the_board_puts_its_split_step_at_the_bottom(code, capsys):
    """Review rbal202a4 LOW 2, R-BAL205: with the leaf off the board there is no place of its
    to take, so its split step goes on the board where GitHub adds it, at the bottom, and is
    not moved."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1, labels=("balance", FILING), on_board=False)
    tracker.add(3)
    assert run(tracker, code, "drop", "plan#2", "--why", "not splitting after all") == 0
    assert [write[0] for write in tracker.writes] == ["board_add", "remove_child", "comment",
                                                      "close"]
    assert tracker.board.items == [3, 1]
    assert "board: plan#1 added back at the bottom" in capsys.readouterr().out


def test_a_split_step_with_a_closed_leaf_besides_stays_split(code):
    """Review rbal202a4 LOW 9, R-BAL205: any other leaf, open or closed, keeps the split step
    split, so dropping its marked leaf never puts it on the board."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True,
                on_board=False)
    tracker.add(3, parent=1, labels=("balance", FILING))
    assert run(tracker, code, "drop", "plan#3", "--why", "gone") == 0
    assert "board_add" not in [write[0] for write in tracker.writes]
