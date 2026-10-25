"""Filings that ended, X-cx L2's leaf C (applications of ruling ``balance:R-BAL207``, recorded
beside R-BAL202..R-BAL206): ``quill file`` run again writes nothing over a filing an earlier
run made -- a card still marked is found in any state, and a ruling among the cards its
owner blocks (R-BAL395) -- and never files over one a decision ended; and a leaf closed while still
marked is no leaf of its split step, unless git says it shipped (``_state.leaves``) -- and,
leaf C2, ``sync`` unlinks it, putting a split step it leaves bare back on the board
(R-BAL205, ``_state.unsplit``).  Over
:class:`_fake.FakeTracker` and a throwaway git repository (the ``code`` fixture,
``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace

from tools.ci.scratch import run as _run
from tools.quill import _git
from tools.quill._fake import (AnswerLostOnce, FailOnce, FakeTracker, leaf_filing, ruling_filing,
                              run, ship, unship)
from tools.quill._github import GitHubError
from tools.quill._tracker import Child
from tools.quill.check import ruling_body
from tools.quill.setup_tracker import FILING


def _closed_by_hand(tracker, number, reason="COMPLETED"):
    """A person closes plan#``number`` on the web (GitHub's default reason: completed)."""
    tracker.cards_by_number[number] = replace(
        tracker.cards_by_number[number], is_open=False, state_reason=reason,
        closed_by_tool=False, touched_by_hand=True)


def _rulings(tracker):
    """Every ruling card: (number, parent, the cards it is blocked by -- its owner,
    R-BAL395 -- open, marked)."""
    return [(card.number, card.parent, card.blocked_by, card.is_open, FILING in card.labels)
            for card in tracker.cards_by_number.values() if card.kind == "ruling"]


# -- (a) a ruling's filing run again ------------------------------------------------------

def test_a_finished_rulings_command_run_again_writes_nothing(code, tmp_path, capsys):
    """Review rbal202b M1 probe 3: a finished ruling is closed and unmarked, so neither the
    open listing nor the marked one holds it, and the plain repeat filed a second ruling;
    it is found among the cards its owner blocks (R-BAL395)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes
    assert capsys.readouterr().out.startswith("plan#2 [ruling, balance] Home is filed already")
    assert _rulings(tracker) == [(2, None, (1,), False, False)]


def test_a_ruling_whose_unmark_landed_but_lost_its_answer_is_not_filed_again(code, tmp_path):
    """Review rbal202b M1 probe 1b, rbal202b2 LOW 5: the unmark lands and its answer is lost
    (exit 2); the documented retry filed a second ruling."""
    tracker = FakeTracker()
    tracker.add(1)
    AnswerLostOnce(tracker, "unmark")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    assert _rulings(tracker) == [(2, None, (1,), False, False)]
    writes = list(tracker.writes)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes and _rulings(tracker) == [(2, None, (1,), False, False)]


def test_a_ruling_whose_mark_was_removed_mid_filing_is_not_filed_again(code, tmp_path, capsys):
    """Review rbal202b M1 probe 1: another session (or a person) removes the mark after the
    close, so the tool's unmark is answered 404 (exit 2); the same command run again writes
    nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    close = tracker.close

    def close_then_unmark_elsewhere(number, reason):
        close(number, reason)
        held = tracker.cards_by_number[number]
        tracker.cards_by_number[number] = replace(
            held, labels=tuple(label for label in held.labels if label != FILING))

    tracker.close = close_then_unmark_elsewhere
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    assert "Label does not exist" in capsys.readouterr().err
    writes = list(tracker.writes)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes and _rulings(tracker) == [(2, None, (1,), False, False)]


def test_a_withdrawn_ruling_is_never_filed_over_until_a_person_restores_it(code, tmp_path,
                                                                             capsys):
    """R-BAL206: a ruling closed as not planned was withdrawn, a person's decision; its
    command run again refuses, writing nothing, and says how to restore it -- reopened and
    closed as completed, it is the filed ruling again."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    err = capsys.readouterr().err
    assert ("was withdrawn (closed as not planned, R-BAL206), so nothing is filed over it.  To "
            "record it after all, reopen it and close it as completed on the web\n") in err
    assert tracker.writes == writes
    _closed_by_hand(tracker, 2, "COMPLETED")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes and _rulings(tracker) == [(2, None, (1,), False, False)]


def test_a_ruling_withdrawn_before_its_link_landed_is_found_by_its_mark(code, tmp_path, capsys):
    """A ruling whose link failed is blocked by no owner; withdrawn on the web, it is still
    marked, so the marked listing holds it and the re-run refuses; restored, the same
    command finishes it -- linked, closed already, unmarked -- with no second ruling."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "block")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    assert ("(closed as not planned, before its filing finished, R-BAL206)" in
            capsys.readouterr().err)
    assert tracker.writes == writes
    _closed_by_hand(tracker, 2, "COMPLETED")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes[len(writes):] == [("block", 2, 1), ("unmark", 2)]
    assert _rulings(tracker) == [(2, None, (1,), False, False)]


def test_a_withdrawn_extra_never_hides_the_ruling_that_stands(code, tmp_path, capsys):
    """Closing an extra as a duplicate is how a person withdraws it, so a finished ruling
    beside a withdrawn copy is the one filed; two that stand are refused, and the remedy
    for a closed one is a close as not planned, which `quill drop` cannot make."""
    body = ruling_body("Where?", "Here.")
    tracker = FakeTracker()
    tracker.add(1)
    for number in (2, 3):
        tracker.add(number, "ruling", body=body, title="Home", blocked_by=(1,), is_open=False,
                    state_reason="COMPLETED", closed_by_tool=True)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    assert ("2 cards have this kind, title and text (plan#2, plan#3): withdraw the extras -- "
            "`quill drop` an open one; reopen a closed one and close it as not planned on the "
            "web") in capsys.readouterr().err
    _closed_by_hand(tracker, 2, "DUPLICATE")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert "plan#3 [ruling, balance] Home is filed already" in capsys.readouterr().out
    assert not tracker.writes


def test_a_ruling_a_person_moved_under_another_owner_is_not_found(code, tmp_path):
    """The one place the ruling search does not reach (``_same_filing``): a closed ruling a
    person re-homed is blocked by no owner this command names, so the command files one for
    the owner it names."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(5)
    tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", blocked_by=(5,),
                is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert _rulings(tracker) == [(2, None, (5,), False, False), (6, None, (1,), False, False)]


def test_a_card_dropped_while_its_command_ran_is_never_filed_over(code, tmp_path, capsys,
                                                                    monkeypatch):
    """The listing of marked cards holds the step open; the read by its number, a moment
    later, finds it closed: dropped while this command ran.  It was called filed already,
    "it carries no filing mark", which was false; it is refused, unwritten, saying how to
    restore it."""
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *args) == 2
    dropped = replace(tracker.cards_by_number[1], is_open=False, state_reason="NOT_PLANNED",
                      touched_by_hand=True)
    listed = tracker.cards
    monkeypatch.setattr(tracker, "cards", lambda numbers: {
        number: dropped if number == 1 else card for number, card in listed(numbers).items()})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert ("was closed as not planned before its filing finished, so nothing is filed over "
            "it.  To file it after all, reopen it on the web, then run this again to finish "
            "its filing") in capsys.readouterr().err
    assert tracker.writes == writes


# -- (b) a leaf closed while still marked -------------------------------------------------

def _split_by_an_unfinished_leaf(tracker, code, tmp_path):
    """plan#1, on the board, split by leaf plan#2 whose unmark failed: plan#2 holds plan#1's
    place, still marked, and plan#1 is off the board."""
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    assert tracker.board.items == [2] and FILING in tracker.cards_by_number[2].labels


def test_a_person_closing_the_only_unfinished_leaf_leaves_a_plain_step(code, tmp_path,
                                                                          capsys):
    """Review rbal202b LOW 11: a person closes plan#1's only leaf on the web while it is
    still marked.  It was never part of the split, so plan#1 is a plain step again: never
    closed as not planned by `sync` (R-BAL187 counted it a split step whose leaves were all
    dropped).  Leaf C2: `sync` puts it back on the board in the leaf's place, then unlinks
    the leaf (R-BAL205), so `next` offers it -- at leaf C it was named off the board until
    a person ran `quill move`."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[len(writes):] == [("board_add", 1), ("board_place", 1, "PVTI_2"),
                                            ("remove_child", 1, 2)]
    assert tracker.cards_by_number[1].is_open and tracker.board.items == [2, 1]
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: plan#1 [step, balance] card 1\n"
    assert run(tracker, code, "claim", "plan#1") == 0


def test_a_read_that_lags_a_drops_unlink_never_drops_the_split_step(code, tmp_path,
                                                                     monkeypatch, capsys):
    """Round 4 LOW 6: `quill drop` of the only unfinished leaf unlinks it, then closes it; a
    `sync` whose read of plan#1 still lists the link saw a split step whose only leaf was
    dropped, and closed it as not planned for good.  Leaf C2 (review M3): that lagging read
    makes `sync` send the unlink again, and GitHub refuses an unlink of a card that is no
    sub-issue, 403 (``recorded/unlink.json``): a failed call, after every state write and
    every REPORT and HISTORY line.  plan#1 is never closed, and the next `sync`, its read
    caught up, writes nothing."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    assert run(tracker, code, "drop", "plan#2", "--why", "not splitting after all") == 0
    assert tracker.cards_by_number[2].parent is None
    lagging = {1: replace(tracker.cards([1])[1], children=(Child(2, "step", False),)),
               2: replace(tracker.cards([2])[2], parent=1)}
    real = tracker.cards
    monkeypatch.setattr(tracker, "cards", lambda numbers: {
        number: lagging.get(number, card) for number, card in real(list(numbers)).items()})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 2
    assert tracker.writes == writes and tracker.cards_by_number[1].is_open
    assert "403" in capsys.readouterr().err
    monkeypatch.setattr(tracker, "cards", real)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and tracker.cards_by_number[1].is_open


def test_a_leaf_that_shipped_counts_however_it_was_closed_while_marked(code, tmp_path,
                                                                          capsys):
    """Review rbal202b2 M-B(1): the unfinished leaf ships (its marker lands on dev) and a
    person closes it, as `sync` asks; its work is done, so plan#1 is a split step whose
    leaves are done -- closed as completed by `sync`, never offered again."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    ship(code, 2)
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "completed")
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"


def test_an_open_unfinished_leaf_still_splits_its_step(code, tmp_path, capsys):
    """Review rbal202b3 M-C: a leaf whose filing is under way (open, marked) counts, so its
    split step is not offered as work, and is refused a board place, mid-filing."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "NOT ON THE BOARD" not in capsys.readouterr().out
    assert run(tracker, code, "move", "plan#1", "--top") == 1
    assert "no step split into leaves" in capsys.readouterr().err


def test_dropping_a_split_step_beside_a_finished_leaf_leaves_its_close_to_sync(code, tmp_path):
    """R-BAL190: a finished leaf carries the split step's drop, so the drop closes only the
    leaves -- and, leaf C2 (review M1), unlinks the unfinished one after its close
    (R-BAL205); `sync` then closes plan#1 as not planned (R-BAL187)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "First")) == 0
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Second")) == 2
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2), ("comment", 3), ("close", 3),
        ("remove_child", 1)]
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[len(writes):] == [("close", 1, "not_planned")]


def test_a_split_step_left_bare_by_its_drop_is_closed_after_its_leaves(code, tmp_path):
    """No finished leaf carries the drop of a step split only by an unfinished leaf, so the
    drop closes the step itself, last, after unlinking the leaf (leaf C2, review M1); if
    that close fails, the same drop run again finds a plain step and drops it."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    close = tracker.close

    def close_failing_for_the_split_step(number, reason):
        if number == 1 and not getattr(close_failing_for_the_split_step, "failed", False):
            close_failing_for_the_split_step.failed = True
            raise GitHubError(502, "bad gateway")
        close(number, reason)

    tracker.close = close_failing_for_the_split_step
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 2
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2), ("remove_child", 1)]
    assert tracker.cards_by_number[1].is_open
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert tracker.writes[-2:] == [("comment", 1, "Dropped: replanned"),
                                   ("close", 1, "not_planned")]


def test_a_step_left_bare_below_a_split_step_is_dropped_and_the_split_step_left_to_sync(
        code, tmp_path):
    """A step left bare under a split step is dropped itself, with the reason, after the
    unfinished leaf is unlinked from it (leaf C2, review M1), and the split step above it,
    still split by it, is left to `sync` (R-BAL187)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "Middle")) == 0
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Bottom", "plan#2")) == 2
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 3), ("close", 3), ("remove_child", 2), ("comment", 2),
        ("close", 2)]
    assert tracker.cards_by_number[1].is_open
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[len(writes):] == [("close", 1, "not_planned")]


def test_an_unfinished_leaf_beside_one_closed_while_marked_is_its_steps_only_leaf(code,
                                                                                    tmp_path):
    """R-BAL205: dropping plan#3, whose unmark failed, puts plan#1 back on the board in its
    place, since plan#2 -- closed by a person while still marked -- was never a leaf."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Other")) == 2
    assert 1 not in tracker.board.items
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#3", "--why", "not splitting after all") == 0
    assert tracker.writes[len(writes):len(writes) + 3] == [
        ("board_add", 1), ("board_place", 1, "PVTI_3"), ("remove_child", 1, 3)]


# -- the delta round: leaf C's review (plan-tracker-2026-10-05/leafc_review.md) -----------

def test_a_card_dropped_while_marked_is_refused_whatever_the_listings_show(code, tmp_path,
                                                                             capsys):
    """Review M3 (P1, P1b): a card closed while still marked is found by its mark, so its
    command run again is refused with the same advice whether or not a listing lags; a
    current listing filed a second card, and a second leaf beside the closed one."""
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *args) == 2
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    tracker.add(2)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 2
    _closed_by_hand(tracker, 3)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "plan#1 [step, balance] Alone has this kind, title and text, and was closed as not " \
           "planned before its filing finished" in capsys.readouterr().err
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 1
    assert "To file it after all, reopen it on the web" in capsys.readouterr().err
    assert tracker.writes == writes
    tracker.cards_by_number[3] = replace(tracker.cards_by_number[3], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 0
    assert tracker.writes[len(writes):] == [("unmark", 3)]


def test_a_withdrawn_ruling_is_refused_as_withdrawn_whatever_its_labels(code, tmp_path,
                                                                         capsys):
    """Review LOW 1 (P2): the label check ran first, so a withdrawn ruling relabelled since
    was told "relabel it by hand"; the withdrawal is the reason nothing is filed."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2],
                                         labels=("balance", "moves-money"))
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    err = capsys.readouterr().err
    assert "was withdrawn (closed as not planned, R-BAL206)" in err and "relabel" not in err


def test_a_finding_closed_after_its_filing_finished_is_filed_anew(code):
    """The boundary ``_same_filing`` states (review LOW 4, P6): only a card still marked,
    or a ruling blocked by its owner (R-BAL395), is looked for among closed cards; a
    finding dropped after its filing finished is not, and the same command files another."""
    args = ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", "plan#1",
            "--text", "The report counts a refund twice.")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *args) == 0
    assert run(tracker, code, "drop", "plan#2", "--why", "not a defect") == 0
    writes = list(tracker.writes)
    assert run(tracker, code, *args) == 0
    assert tracker.writes[len(writes)][:2] == ("create", 3)


def test_a_sync_whose_open_listing_lags_a_leaf_closing_never_reopens_the_dropped_step(
        code, tmp_path, monkeypatch):
    """Review M1 (P7): `quill drop` closed the split step itself, its only leaf still being
    filed; a `sync` whose open listing still showed that leaf open took the split step for
    split again and reopened it, losing the drop once the listing caught up.  Each card
    sync decides over is read by its number."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    stale = tracker.cards([2])[2]
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    listed = tracker.open_cards
    monkeypatch.setattr(tracker, "open_cards", lambda: {**listed(), 2: stale})
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and not tracker.cards_by_number[1].is_open


def test_a_drop_never_writes_over_a_persons_close_of_the_split_step(code, tmp_path):
    """Review LOW 2 (P5): a split step a person already closed keeps their close; the drop
    reaches its leaves only -- closing the unfinished one, then unlinking it (leaf C2,
    review M1)."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2), ("remove_child", 1)]
    assert tracker.cards_by_number[1].touched_by_hand


def test_a_new_leaf_waits_on_an_unfinished_leaf_split_below_by_hand(code, tmp_path):
    """Review M2 (P3b): an OPEN marked leaf counts even when a step a person linked under it
    was dropped, so R-BAL204 refuses a new leaf beside it."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1, labels=("balance", FILING), children=(Child(3, "step", False),))
    tracker.add(3, parent=2, is_open=False, state_reason="NOT_PLANNED", touched_by_hand=True,
                on_board=False)
    assert run(tracker, code, *leaf_filing(tmp_path, "Beside")) == 1
    assert tracker.cards_by_number[1].step_children == (2,)


# -- the third round: leaf C's delta review (plan-tracker-2026-10-05/leafc_review_delta.md) --

def test_shipped_work_closed_while_marked_is_never_refused_nor_filed_again(code, tmp_path,
                                                                           capsys):
    """Delta DM1 (D2): a step's filing stops before its unmark, it ships, and a person closes
    it as `sync` asks.  It was not dropped (git), so its command run again was refused as
    if a decision ended it and told to REOPEN shipped work; it now writes nothing and says
    it shipped."""
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *args) == 2
    ship(code, 1)
    _closed_by_hand(tracker, 1)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    out = capsys.readouterr().out
    assert "shipped in git" in out and "reopen" not in out
    assert tracker.writes == writes


def test_a_drop_closes_a_split_step_the_tool_had_shown_completed(code, tmp_path):
    """Delta DM3 (D6): only a PERSON's close is kept.  The tool showed plan#1 completed
    (its only leaf shipped); a revert deleting its marker undid the leaf and a person
    reopened it; a
    drop of plan#1 skipped it as closed, and the next `sync` reopened it as a plain step,
    losing the drop."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    ship(code, 2)
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "completed")
    unship(code, 2)
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert tracker.writes[-2:] == [("remove_child", 1, 2), ("close", 1, "not_planned")]
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and not tracker.cards_by_number[1].is_open


def test_a_listed_card_its_number_cannot_read_fails_sync_unwritten(code, tmp_path,
                                                                    monkeypatch, capsys):
    """Delta LOW 1 (D4): a card the listing holds but a read by its number does not kept
    its stale listing copy, which decided sync; sync now fails closed, writing nothing."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    stale = tracker.cards([2])[2]
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    listed, by_number = tracker.open_cards, tracker.cards
    monkeypatch.setattr(tracker, "open_cards", lambda: {**listed(), 2: stale})
    monkeypatch.setattr(tracker, "cards", lambda numbers: {
        n: c for n, c in by_number(numbers).items() if n != 2})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 2
    assert "plan#2 is listed, but a read by its number does not hold it" in (
        capsys.readouterr().err)
    assert tracker.writes == writes


def test_a_leaf_dropped_with_its_split_step_is_refused_for_its_drop_under_any_parent(
        code, tmp_path, capsys):
    """Delta LOW 2 (D3): the re-homing refusal came first, so a re-plan filing the same leaf
    under a new step was told "re-homing a card is done by hand"; the drop is the reason.
    (The mark does not record the parent, so identical leaf text is refused under any.)"""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    tracker.add(9)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#9")) == 1
    err = capsys.readouterr().err
    assert "before its filing finished" in err and "re-homing" not in err


def test_a_lagging_listing_turns_a_new_filing_into_filed_already(code, monkeypatch, capsys):
    """Delta LOW 3 (D5), the boundary ``_same_filing`` states: a finding closed after its
    filing finished is not looked for, but a listing that lags its close still shows it
    open; read closed by its number, it is filed already and nothing is written."""
    args = ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", "plan#1",
            "--text", "The report counts a refund twice.")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *args) == 0
    stale = tracker.cards([2])[2]
    assert run(tracker, code, "drop", "plan#2", "--why", "not a defect") == 0
    listed = tracker.open_cards
    monkeypatch.setattr(tracker, "open_cards", lambda: {**listed(), 2: stale})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    assert "is filed already" in capsys.readouterr().out and tracker.writes == writes


# -- the fourth round: leaf C's round 3 (plan-tracker-2026-10-05/leafc_review_r3.md) ------

def test_a_stray_marker_never_decides_a_withdrawn_ruling(code, tmp_path, capsys):
    """Round 3 RM1 (R1): a ruling is never shipped (R-BAL184: a ship marker naming one is not
    acted on), so a stray marker must not turn its withdrawal into "filed already"."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    ship(code, 2)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    assert "was withdrawn (closed as not planned, R-BAL206)" in capsys.readouterr().err
    assert tracker.writes == writes


def test_a_stray_marker_never_decides_a_question_dropped_while_marked(code, tmp_path,
                                                                     capsys):
    """Round 3 RM1 (R2): a question is never shipped either, so one closed while still
    marked is refused as dropped, never told it "shipped in git"."""
    spec = tmp_path / "q.md"
    spec.write_text("Which day?")
    args = ("file", "question", "--arc", "balance", "--title", "Day", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *args) == 2
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    ship(code, 1)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "was closed as not planned before its filing finished" in capsys.readouterr().err
    assert tracker.writes == writes


# -- leaf C2: sync undoes a split a leaf closed while still marked began (R-BAL205) -------

def _unlabelled_by_hand(tracker, number):
    """A person removes plan#``number``'s filing label on the web: a tidy-up of a closed
    card nothing warns against."""
    held = tracker.cards_by_number[number]
    tracker.cards_by_number[number] = replace(
        held, labels=tuple(label for label in held.labels if label != FILING))


def test_a_stale_label_removed_after_sync_never_drops_a_step_that_shipped(code, tmp_path,
                                                                           capsys):
    """Review M4 (probe P4): a person closed plan#1's only leaf while it was still being
    filed; plan#1, a plain step again, was claimed, built and shipped -- its branch merged
    into dev, which ends the claim.  At leaf C the link stayed and the label was all that
    kept the leaf out, so removing it made `sync` re-close the shipped plan#1 "not planned,
    every leaf dropped" and call its Ships trailer a stale number.  `sync` unlinks the leaf
    at its first run, so the label decides nothing."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.cards_by_number[2].parent is None
    assert run(tracker, code, "claim", "plan#1") == 0
    _run(code, "push", "--quiet", "origin", "refs/heads/card/1:refs/heads/dev")
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "completed") and not _git.read(code).claims
    _unlabelled_by_hand(tracker, 2)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and capsys.readouterr().out == ""


def test_a_stale_label_removed_after_sync_releases_nothing_that_waits(code, tmp_path,
                                                                      capsys):
    """Review delta DM2 (probe D1): plan#1 not built yet, plan#3 waiting on it.  At leaf C,
    removing the stale label made plan#1 a split step whose leaves were all dropped: `next`
    offered plan#3, though plan#1 was never built, and `sync` closed plan#1 as not planned.
    Unlinked by `sync`, the leaf decides nothing: plan#1 is offered and plan#3 waits."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    tracker.add(3, blocked_by=(1,))
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    _unlabelled_by_hand(tracker, 2)
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and tracker.cards_by_number[1].is_open
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: plan#1 [step, balance] card 1\n"


def test_sync_prints_the_undo_it_would_make_then_makes_it(code, tmp_path, capsys):
    """Each write printed: under ``--dry-run`` as what would be written, with nothing
    written; then as it lands."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert tracker.writes == writes
    assert capsys.readouterr().out == (
        "would undo the split of plan#1 [step, balance] card 1 by plan#2, closed while still "
        "being filed (R-BAL205)\n"
        "  board: plan#1 would be added back at the bottom, a plain step again\n"
        "  board: plan#1 would be moved into plan#2's place\n"
        "  plan#2 would be unlinked from plan#1: its filing never finished, so it was never "
        "part of that split (R-BAL205)\n")
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == (
        "undo the split of plan#1 [step, balance] card 1 by plan#2, closed while still being "
        "filed (R-BAL205)\n"
        "  board: plan#1 added back at the bottom, a plain step again\n"
        "  board: plan#1 moved into plan#2's place\n"
        "  plan#2 unlinked from plan#1: its filing never finished, so it was never part of "
        "that split (R-BAL205)\n")


def test_a_sync_whose_unlink_failed_is_finished_by_the_next_with_no_second_board_add(
        code, tmp_path):
    """The split step goes back on the board before the unlink, so a failed unlink leaves
    the link for the next `sync`, which sees the step on the board and only unlinks."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    FailOnce(tracker, "remove_child")
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 2
    assert tracker.writes[len(writes):] == [("board_add", 1), ("board_place", 1, "PVTI_2")]
    assert tracker.cards_by_number[2].parent == 1
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[len(writes):] == [("board_add", 1), ("board_place", 1, "PVTI_2"),
                                            ("remove_child", 1, 2)]


def test_a_split_step_a_person_closed_is_unlinked_from_and_stays_off_the_board(code,
                                                                                tmp_path):
    """A person's close of the split step is their decision: `sync` (which reads it through
    plan#3, waiting on it) unlinks the leaf and never puts plan#1 back on the board."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    tracker.add(3, blocked_by=(1,))
    _closed_by_hand(tracker, 2)
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[len(writes):] == [("remove_child", 1, 2)]
    assert 1 not in tracker.board.items


def test_a_leaf_reopened_as_sync_unlinked_it_is_relinked_by_its_own_command(code, tmp_path):
    """A person reopens the leaf after `sync` unlinked it and put plan#1 back on the board
    (or as it ran: a read that lags the reopen does the same).  The leaf, open, marked and
    unlinked, is an unfinished filing of its own, like a leaf whose link failed (R-BAL202):
    plan#1 is plain work meanwhile.  Its command run again links it again, moves it into
    plan#1's place, takes plan#1 off the board and removes the mark -- the split it began,
    finished."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == [2, 1] and tracker.cards_by_number[2].parent is None
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    writes = list(tracker.writes)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    assert tracker.writes[len(writes):] == [("add_child", 1, 2), ("board_place", 2, "PVTI_1"),
                                            ("board_remove", 1), ("unmark", 2)]
    assert tracker.board.items == [2] and tracker.cards_by_number[2].parent == 1


def test_show_names_a_linked_step_that_is_no_leaf(code, tmp_path, capsys):
    """`quill show` of the split step says the leaf a person closed while it was still being
    filed is no leaf, so it agrees with `next`, which offers the step as plain work; an
    open leaf still being filed, and the line once `sync` unlinked it, say nothing of it."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#1") == 0
    assert "\n  child: plan#2 (step, open) Half\n" in capsys.readouterr().out
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "show", "plan#1") == 0
    assert ("\n  child: plan#2 (step, closed; no leaf: closed while still being filed, so "
            "never part of the split, R-BAL205) Half\n") in capsys.readouterr().out
    assert run(tracker, code, "sync") == 0
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#1") == 0
    assert "child:" not in capsys.readouterr().out


def test_a_plain_steps_drop_unlinks_a_leaf_closed_while_still_being_filed(code, tmp_path):
    """Review C2 M1: a person closed plan#1's only leaf while it was still being filed and no
    `sync` has run since, so plain plan#1 still holds the link; its drop unlinks the leaf
    before closing it, so no `sync` needs to reach the closed plan#1.  The same path
    finishes a split step's drop whose unlink failed: run again, it finds a plain step."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert tracker.writes[len(writes):] == [("remove_child", 1, 2),
                                            ("comment", 1, "Dropped: replanned"),
                                            ("close", 1, "not_planned")]
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    FailOnce(tracker, "remove_child")
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 2
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2)]
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]][3:] == [
        ("remove_child", 1), ("comment", 1), ("close", 1)]
    assert tracker.cards_by_number[2].parent is None


def test_next_says_sync_places_a_step_whose_leaf_closed_while_still_being_filed(code, tmp_path,
                                                                                capsys):
    """C2 delta DM1: `next` asked a person to `quill move` a step whose leaf a person closed
    while it was still being filed, and the next `sync` moved it back after that leaf; it
    now says `sync` puts it back.  A step off the board with no such link is still told to
    `quill move`, and after the sync puts it back, `next` offers it."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    tracker.add(3, on_board=False)
    _closed_by_hand(tracker, 2)
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert ("NOT ON THE BOARD, so in no order: plan#1 [step, balance] card 1 -- `quill sync` puts "
            "it back on the board, unlinking") in out
    assert "plan#3 [step, balance] card 3 -- place it with `quill move plan#3 --after" in out
    assert run(tracker, code, "sync") == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#1 ")


def test_sync_finishes_an_undo_that_failed_after_its_own_close_of_the_split_step(code,
                                                                                tmp_path):
    """C2 delta DM2: plan#1's finished leaf is dropped and its other leaf, still being filed,
    was closed by a person; `sync` closes plan#1 as not planned (R-BAL187) and its unlink
    fails.  No open card links to the closed plan#1 any more, but `sync` reads every card
    still marked, so the next run reaches the link and finishes the undo; a person
    reopening the leaf afterwards leaves the dropped plan#1 dropped (with the link left,
    it made plan#1 split again and `sync` reopened it)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "First")) == 0
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Second")) == 2
    _closed_by_hand(tracker, 3)
    assert run(tracker, code, "drop", "plan#2", "--why", "not needed") == 0
    FailOnce(tracker, "remove_child")
    assert run(tracker, code, "sync") == 2
    assert ("close", 1, "not_planned") in tracker.writes and tracker.cards_by_number[3].parent == 1
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("remove_child", 1, 3)
    tracker.cards_by_number[3] = replace(tracker.cards_by_number[3], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    assert run(tracker, code, "sync") == 1
    assert ("reopen", 1) not in tracker.writes and not tracker.cards_by_number[1].is_open
