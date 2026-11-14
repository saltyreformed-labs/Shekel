"""The regroup's writes (rulings plan#4011, plan#4012, plan#4096; :mod:`_regroup_pass`): a ``quill
sync`` pass and a ``quill priority`` run each leave the board grouped, Urgent, then High, the
cards waiting under the High cards, Medium, Low and the rest, with the developer's order kept
inside each group; a pass reads quill's suggestion on each card no Priority ranks and on no
other; a dry run writes nothing; a second pass moves nothing; two rows moved down are sent after
one row, last first, and read back where they end (plan#3906); a card ``sync`` closes in the same
pass is never moved; a Priority cleared, through quill or on the web, puts the card at the top
of the no-Priority group, and a card set Low at the bottom goes above it (finding plan#4092); a
``quill file`` filing leaves the board grouped too, a new card suggested Urgent or High waiting
under the High cards and one suggested Medium or Low at the bottom, a card it just added and an
order read does not list yet planned at the bottom, a leaf in its split step's place (R-BAL179),
and a filing run again after a cut places its card again; the weekly update's card is left out
of the moves and placed first in the High group after them (ruling plan#4014), which
``test__waits.py`` drives through ``sync``.  Over the in-memory tracker
(:class:`_fake.FakeTracker`) and the ``code`` fixture's real git; the board's own back-to-back
move and its read back of several placements over scripted GitHub answers, and the fake
board's read back by the same check, so a test sees a read back asking for the wrong place.  The
plan itself is ``test__regroup.py``'s.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill import _git, _sync
from tools.quill._board_lag import BOARD_WAIT_SECONDS
from tools.quill._draft import Suggestion
from tools.quill._fake import QUILL_ACTOR, FailOnce, FakeTracker, leaf_filing, run, ship, unship
from tools.quill._regroup import moves
from tools.quill._regroup_pass import board_cards, filing_pass
from tools.quill._suggestion import Pending
from tools.quill._tracker import BOARD_AFTER, BOARD_TOP, Board, Child
from tools.quill.setup_tracker import FILING, PLAN
from tools.quill.test__tracker import _ReadBoard

#: The line a move prints, ``{verb}`` "moved" or "would move".
MOVED = ("{verb} on the board {where} (ruling {ruling}): "
         "plan#{number} [step, balance] card {number}")


def _moved(number, where, ruling="plan#4011", verb="moved"):
    """The line a move of card ``number`` to ``where`` prints."""
    return MOVED.format(verb=verb, number=number, where=where, ruling=ruling)


def _regroup_lines(out):
    """The lines of ``out`` the regroup printed: its moves and its read back."""
    return [line for line in out.splitlines()
            if " on the board " in line and "(ruling plan#401" in line
            or "does not show every move" in line]


def _board_moves(tracker):
    """Each back-to-back move the board was sent: ``(card number, item it went after)``."""
    return [write[1:] for write in tracker.writes if write[0] == "board_move"]


def _suggest(tracker, number, priority):
    """quill's suggestion of ``priority`` pending on card ``number``, as a filing leaves it."""
    tracker.suggestions.held[number] = Pending(Suggestion(priority, "A test card's reason."),
                                               QUILL_ACTOR)


def _out_of_group():
    """A board as the developer left it: Low plan#1, High plan#2, Medium plan#3, and plan#4,
    which holds no Priority and no suggestion, so it is in the last group, at the bottom
    already (ruling plan#4096)."""
    tracker = FakeTracker()
    tracker.add(1, priority="Low")
    tracker.add(2, priority="High")
    tracker.add(3, priority="Medium")
    tracker.add(4)
    return tracker


# -- quill sync ---------------------------------------------------------------------------------

def test_a_sync_pass_leaves_the_board_grouped_and_a_second_pass_moves_nothing(code, capsys):
    """The Low card goes under the Medium one, the High and Medium cards already in order stay,
    and the card holding no Priority stays at the bottom, in the last group; the move is printed
    and read back.
    Run again, it finds the board grouped and moves nothing."""
    tracker = _out_of_group()
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == [2, 3, 1, 4]
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(1, "after plan#3")]
    assert run(tracker, code, "sync") == 0
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == []


def test_two_rows_moved_down_go_after_one_row_and_read_back_where_they_end(code, capsys):
    """High plan#1 and plan#2 above Urgent plan#3 to plan#5: plan#2 is sent under plan#5, then
    plan#1 under plan#5 too, above it, so the High cards keep their order after each move (the
    review's H1, plan#3906); the read back asks for plan#2 under plan#1, where it ends, not under
    plan#5, where it was sent, so it holds and says nothing (M1)."""
    tracker = FakeTracker()
    for number, priority in ((1, "High"), (2, "High"), (3, "Urgent"), (4, "Urgent"),
                             (5, "Urgent")):
        tracker.add(number, priority=priority)
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == [3, 4, 5, 1, 2]
    assert _board_moves(tracker) == [(2, "PVTI_5"), (1, "PVTI_5")]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(2, "after plan#5"),
                                                      _moved(1, "after plan#5")]


def test_a_dry_run_prints_each_move_and_writes_none(code, capsys):
    """Under ``--dry-run`` each move is printed as what would be written; none is sent, and the
    board is not read back: read back, a board that lags would be said not to show it."""
    tracker = _out_of_group()
    tracker.board.lagging = True
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert tracker.board.items == [1, 2, 3, 4] and not _board_moves(tracker)
    assert _regroup_lines(capsys.readouterr().out) == [
        _moved(1, "after plan#3", verb="would move")]


def test_a_card_sync_closes_in_the_same_pass_is_never_moved(code, capsys):
    """High plan#2, below Low plan#1, shipped: sync closes it, and the regroup reads it as closed,
    so it moves nothing, though the cards sync read before its close show it open."""
    tracker = FakeTracker()
    tracker.add(1, priority="Low")
    tracker.add(2, priority="High")
    ship(code, 2)
    assert run(tracker, code, "sync") == 0
    assert not tracker.cards_by_number[2].is_open
    assert tracker.board.items == [1, 2] and not _board_moves(tracker)
    assert _regroup_lines(capsys.readouterr().out) == []


def test_a_split_step_sync_drops_in_the_same_pass_is_never_moved(code, capsys):
    """High plan#1, split into plan#2 and plan#3, both dropped by a person, and put back on the
    board by hand below Low plan#5: sync closes it as not planned (R-BAL187), and the regroup
    reads it as closed, so it moves nothing."""
    tracker = FakeTracker()
    tracker.add(5, priority="Low")
    tracker.add(1, priority="High",
                children=(Child(2, "step", False), Child(3, "step", False)))
    for leaf in (2, 3):
        tracker.add(leaf, parent=1, is_open=False, state_reason="NOT_PLANNED",
                    touched_by_hand=True)
    assert run(tracker, code, "sync") == 0
    assert tracker.cards_by_number[1].state_reason == "NOT_PLANNED"
    assert tracker.board.items == [5, 1] and not _board_moves(tracker)
    assert _regroup_lines(capsys.readouterr().out) == []


def test_a_card_sync_reopens_in_the_same_pass_is_regrouped_in_it(code, capsys):
    """High plan#2, closed by the tool as shipped and still on the board, below Low plan#1: a
    revert deleted its marker, so sync reopens it, and the regroup reads it open and moves it in
    the same pass."""
    tracker = FakeTracker()
    tracker.add(1, priority="Low")
    tracker.add(2, priority="High", is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.board.items.append(2)
    ship(code, 2)
    unship(code, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.cards_by_number[2].is_open
    assert tracker.board.items == [2, 1]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(2, "to the top")]


def test_a_board_that_does_not_show_the_moves_yet_is_said_so(code, capsys):
    """The read back after the moves does not show them (the board lags): the pass says so, and
    the next one moves what did not land."""
    tracker = _out_of_group()
    tracker.board.lagging = True
    assert run(tracker, code, "sync") == 0
    assert _regroup_lines(capsys.readouterr().out) == [
        _moved(1, "after plan#3"),
        "  the board does not show every move yet (it lags a write): the next regroup moves each "
        "that did not land"]


def test_a_card_suggested_high_at_the_bottom_moves_under_the_high_cards(code, capsys):
    """Ruling plan#4096: plan#6 holds no Priority and quill's suggestion of High, at the bottom:
    the pass reads the suggestion and moves it under the High card, above the Medium one; plan#5,
    suggested Low, stays in the last group."""
    tracker = FakeTracker()
    for number, priority in enumerate(("Urgent", "High", "Medium", "Low", None, None), start=1):
        tracker.add(number, priority=priority)
    _suggest(tracker, 5, "Low")
    _suggest(tracker, 6, "High")
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == [1, 2, 6, 3, 4, 5]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(6, "after plan#2")]


def test_a_priority_cleared_on_the_web_goes_to_the_top_of_the_no_priority_group(code, capsys):
    """Ruling plan#4096: High plan#1, its Priority cleared on the web (written by no quill
    command), goes at the next ``sync`` just under the Low card, above the cards holding none,
    as a clear through quill does."""
    tracker = FakeTracker()
    for number, priority in enumerate(("High", "High", "Low", None, None), start=1):
        tracker.add(number, priority=priority)
    tracker.cards_by_number[1] = replace(tracker.cards_by_number[1], priority=None)
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == [2, 3, 1, 4, 5]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(1, "after plan#3")]


def test_a_pass_reads_the_suggestion_of_each_card_no_priority_ranks_and_of_no_other(
        code, monkeypatch):
    """The pass reads quill's suggestion on plan#2 and plan#6, which no Priority ranks, and on
    no card a Priority ranks (plan#1, and leaf plan#4 through its split step), a paused card
    (plan#3), a closed one (plan#5) or a task (plan#7): a read for each card waiting on the
    developer's decision and none for the rest (``_regroup_pass``'s docstring)."""
    tracker = FakeTracker()
    tracker.add(9, priority="High", on_board=False, children=(Child(4, "step", True),))
    tracker.add(1, priority="Medium")
    tracker.add(2)
    tracker.add(3, priority="On hold")
    tracker.add(4, parent=9)
    tracker.add(5, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.board.items.append(5)
    tracker.add(6)
    tracker.add_task(7, 1)
    read = []
    pending = tracker.suggestions.pending
    monkeypatch.setattr(tracker.suggestions, "pending",
                        lambda card: read.append(card.number) or pending(card))
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert read == [2, 6]


def test_the_read_by_the_boards_rows_plans_what_syncs_read_plans(code):
    """``quill priority``'s read (:func:`_regroup_pass.board_cards`: the board's rows by number
    and every step above each) plans the same moves as ``sync``'s read of every card, over a
    board holding a leaf ranked through its split step, a leaf paused through one, a card On
    hold, a closed card and one holding no Priority and quill's suggestion of High; and the plan
    moves cards, so the two agree on something."""
    tracker = FakeTracker()
    tracker.add(10, priority="High", on_board=False,
                children=(Child(11, "step", True), Child(12, "step", True)))
    tracker.add(20, priority="On hold", on_board=False, children=(Child(21, "step", True),))
    tracker.add(1, priority="Low")
    tracker.add(11, parent=10)
    tracker.add(2, priority="On hold")
    tracker.add(21, parent=20, priority="Urgent")
    tracker.add(3, priority="Urgent")
    tracker.add(4, priority="Medium", is_open=False, state_reason="COMPLETED",
                closed_by_tool=True)
    tracker.board.items.append(4)
    tracker.add(12, parent=10)
    tracker.add(5)
    order = tracker.board.order()
    wide = _sync.every_card(tracker, _git.read(code), _git.ever_marked(code)).cards
    planned = moves(order, wide, {5: "High"})
    assert planned
    assert moves(order, board_cards(tracker, [number for number, _ in order]),
                 {5: "High"}) == planned


# -- quill priority -----------------------------------------------------------------------------

def test_a_priority_set_moves_the_card_to_the_end_of_its_group_and_run_again_moves_nothing(
        code, capsys):
    """plan#3, at the bottom holding none, set High: it ends last among the High cards, above
    the Medium one; the same command run again writes nothing and moves nothing."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="Medium")
    tracker.add(3)
    assert run(tracker, code, "priority", "plan#3", "--to", "High") == 0
    assert tracker.board.items == [1, 3, 2]
    assert run(tracker, code, "priority", "plan#3", "--to", "High") == 0
    assert tracker.board.items == [1, 3, 2] and len(_board_moves(tracker)) == 1
    out = capsys.readouterr().out
    assert len(_regroup_lines(out)) == 1
    assert out.endswith("plan#3 [step, balance] card 3 holds the Priority 'High' already: not "
                        "written\n")


def test_a_card_at_the_bottom_set_low_ends_above_the_cards_holding_none(code):
    """Finding plan#4092: plan#5, at the bottom holding none, set Low, ends last among the Low
    cards, above plan#3 and plan#4, which hold none, never below them."""
    tracker = FakeTracker()
    for number, priority in enumerate(("Medium", "Low", None, None, None), start=1):
        tracker.add(number, priority=priority)
    assert run(tracker, code, "priority", "plan#5", "--to", "Low") == 0
    assert tracker.board.items == [1, 2, 5, 3, 4]
    assert _board_moves(tracker) == [(5, "PVTI_2")]


def test_a_priority_set_is_regrouped_by_the_value_written_whatever_a_read_shows(code,
                                                                               monkeypatch):
    """A read of the card just after its write still shows it holding none (GitHub's lag, which
    no read here waits out): the pass reads it as holding the Priority written, and moves it."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="Medium")
    tracker.add(3)
    monkeypatch.setattr(tracker, "set_field", lambda number, field, value: None)
    assert run(tracker, code, "priority", "plan#3", "--to", "High") == 0
    assert tracker.cards_by_number[3].priority is None
    assert tracker.board.items == [1, 3, 2]


def test_a_clear_puts_the_card_at_the_top_of_the_no_priority_group_and_run_again_stays(
        code, capsys):
    """Rulings plan#4013, plan#4096: High plan#1 cleared drops to the top of the last group, just
    under the Low card, above the cards holding none; the same command run again finds it there
    and moves nothing."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="High")
    tracker.add(3, priority="Low")
    tracker.add(4)
    tracker.add(5)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert tracker.board.items == [2, 3, 1, 4, 5]
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(1, "after plan#3")]
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == []


def test_a_clear_of_a_card_holding_none_suggested_high_leaves_it_waiting(code):
    """plan#2 holds no Priority and quill's suggestion of High, waiting under the High card:
    cleared, it is written nothing and its suggestion is not taken back (``quill priority``'s
    docstring), so it keeps its group and nothing moves."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2)
    tracker.add(3, priority="Low")
    _suggest(tracker, 2, "High")
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert tracker.board.items == [1, 2, 3] and not _board_moves(tracker)
    assert not [write for write in tracker.writes if write[0] in ("set_field", "withdraw")]


def test_a_clear_the_regroup_moves_with_others_is_one_of_its_moves(code, capsys):
    """A drag left Low plan#3 above Medium plan#4: the clear of High plan#1 is planned with the
    rest, in the sort's order, Medium plan#4 up first (of the two single moves that group the
    rest, the one keeping the earlier run, ``_regroup._kept``), then the cleared card to the top
    of the last group, under the Low card that move leaves last of its group."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="High")
    tracker.add(3, priority="Low")
    tracker.add(4, priority="Medium")
    tracker.add(5)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert tracker.board.items == [2, 4, 3, 1, 5]
    assert _regroup_lines(capsys.readouterr().out) == [
        _moved(4, "after plan#2"), _moved(1, "after plan#3")]


def test_a_clear_that_leaves_a_leaf_ranked_through_its_step_is_the_regroups(code):
    """Leaf plan#2 holds Urgent of its own under High plan#1: cleared, it ranks High through
    plan#1, first in High where it stands, so nothing moves; it never goes to the no-Priority
    group."""
    tracker = FakeTracker()
    tracker.add(1, priority="High", on_board=False,
                children=(Child(2, "step", True), Child(3, "step", True)))
    tracker.add(9, priority="Urgent")
    tracker.add(2, parent=1, priority="Urgent")
    tracker.add(3, parent=1)
    tracker.add(4, priority="Low")
    tracker.add(5)
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert tracker.board.items == [9, 2, 3, 4, 5] and not _board_moves(tracker)


def _closed_step(tracker):
    """Step plan#2, closed and still on the board, second, holding High."""
    tracker.add(2, priority="High", is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.board.items.insert(1, 2)


def _task(tracker):
    """Task plan#2 under plan#1, which holds Low, on the board under it, holding High."""
    tracker.add_task(2, 1, priority="High")


def _off_the_board(tracker):
    """Step plan#2, off the board, holding High."""
    tracker.add(2, priority="High", on_board=False)


@pytest.mark.parametrize("second", [_closed_step, _task, _off_the_board],
                         ids=["closed", "task", "off-the-board"])
def test_a_clear_of_a_card_in_no_place_of_the_order_moves_nothing(code, second):
    """A closed card, a task (in no place of the board's order, ruling plan#1814) and a card off
    the board, cleared, are never moved, though nothing ranks them; the board, grouped, moves
    nothing."""
    tracker = FakeTracker()
    tracker.add(1, priority="Low")
    second(tracker)
    tracker.add(3)
    held = list(tracker.board.items)
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert tracker.board.items == held and not _board_moves(tracker)


def test_a_run_that_writes_no_priority_still_regroups(code):
    """Low plan#1, holding Low already, is written nothing, and the pass runs all the same, so
    the same command run again finishes a regroup a failure cut short."""
    tracker = _out_of_group()
    assert run(tracker, code, "priority", "plan#1", "--to", "Low") == 0
    assert not [write for write in tracker.writes if write[0] == "set_field"]
    assert tracker.board.items == [2, 3, 1, 4]


def test_an_effort_set_moves_nothing(code):
    """``quill effort`` writes the Effort alone: the regroup is ``priority``'s."""
    tracker = _out_of_group()
    assert run(tracker, code, "effort", "plan#4", "--to", "2") == 0
    assert tracker.board.items == [1, 2, 3, 4] and not _board_moves(tracker)


# -- quill file ---------------------------------------------------------------------------------

#: The reason each filing below gives for its suggestion.
_WHY = "A test card's reason: nothing waits on it."


def _filing(tmp_path, kind, suggestion):
    """``quill file`` filing the top-level step, or the question, "New", suggested
    ``suggestion``, its text written to a file; a step sized at two sessions."""
    text = tmp_path / "new.md"
    text.write_text("New work.")
    sized = ("--effort", "2") if kind == "step" else ()
    return ("file", kind, "--arc", "balance", "--title", "New", "--body-file", str(text), *sized,
            "--suggest", suggestion, "--why", _WHY)


def _grouped():
    """A board grouped as the regroup leaves it: Urgent plan#1, High plan#2, Medium plan#3, Low
    plan#4, and plan#5, which holds no Priority, at the bottom."""
    tracker = FakeTracker()
    for number, priority in enumerate(("Urgent", "High", "Medium", "Low", None), start=1):
        tracker.add(number, priority=priority)
    return tracker


#: Where a new card filed on :func:`_grouped`'s board ends, by its suggestion, and the moves the
#: board was sent: under the High cards, or at the bottom, where GitHub adds it.
_WAITS = {
    "Urgent": ([1, 2, 6, 3, 4, 5], [(6, "PVTI_2")]),
    "High": ([1, 2, 6, 3, 4, 5], [(6, "PVTI_2")]),
    "Medium": ([1, 2, 3, 4, 5, 6], []),
    "Low": ([1, 2, 3, 4, 5, 6], []),
}


@pytest.mark.parametrize("kind", ["step", "question"])
@pytest.mark.parametrize("suggestion", list(_WAITS))
def test_a_new_card_waits_by_its_suggestion_under_the_high_cards_or_at_the_bottom(
        code, tmp_path, capsys, kind, suggestion):
    """Rulings plan#4017, plan#4096: a new step or question suggested Urgent or High is moved by
    the regroup, which reads its suggestion, just under the High cards, above the Medium ones,
    before its mark comes off; one suggested Medium or Low stays at the bottom, where GitHub adds
    it, in the last group.  The next pass finds the board grouped and moves nothing."""
    board, moved = _WAITS[suggestion]
    tracker = _grouped()
    assert run(tracker, code, *_filing(tmp_path, kind, suggestion)) == 0
    assert tracker.board.items == board and _board_moves(tracker) == moved
    assert tracker.writes[-1] == ("unmark", 6)
    lines = [f"moved on the board after plan#2 (ruling plan#4011): plan#6 [{kind}, balance] New"]
    assert _regroup_lines(capsys.readouterr().out) == ([f"  {lines[0]}"] if moved else [])
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == board and _board_moves(tracker) == moved


def test_a_filing_regroups_the_board_with_its_card_in_it(code, tmp_path, capsys):
    """A drag left Low plan#2 above Medium plan#3: the filing of a card suggested High plans its
    card with the rest, in the sort's order, the card first, under High plan#1, then Medium
    plan#3 under it; one suggested Low stays at the bottom, and Medium plan#3 alone moves."""
    lines = {"High": ["  " + _moved(4, "after plan#1").replace("card 4", "New"),
                      "  " + _moved(3, "after plan#4")],
             "Low": ["  " + _moved(3, "after plan#1")]}
    for suggestion, board in (("High", [1, 4, 3, 2]), ("Low", [1, 3, 2, 4])):
        tracker = FakeTracker()
        for number, priority in ((1, "High"), (2, "Low"), (3, "Medium")):
            tracker.add(number, priority=priority)
        assert run(tracker, code, *_filing(tmp_path, "step", suggestion)) == 0
        assert tracker.board.items == board
        assert _regroup_lines(capsys.readouterr().out) == lines[suggestion]


def test_a_card_the_filing_added_that_the_order_read_does_not_list_yet_is_moved(
        code, tmp_path, monkeypatch):
    """The board's order read lags the add (``_board_lag``): the card the filing just added at
    the bottom, suggested High, is planned there all the same, and moved under the High cards."""
    tracker = _grouped()
    listed = tracker.board.order
    monkeypatch.setattr(tracker.board, "order", lambda: [row for row in listed() if row[0] != 6])
    assert run(tracker, code, *_filing(tmp_path, "step", "High")) == 0
    assert _board_moves(tracker) == [(6, "PVTI_2")]


def test_a_leaf_the_order_read_does_not_list_yet_keeps_its_split_steps_place(
        code, tmp_path, monkeypatch):
    """The leaf of High plan#2 goes into its place, above High plan#6, and the board's order read
    after does not list it yet: it was placed, not added at the bottom, so the pass plans
    nothing for it, where planning it at the bottom would move it under plan#6 (R-BAL179)."""
    tracker = FakeTracker()
    for number, priority in ((1, "High"), (2, "High"), (6, "High"), (4, "Medium")):
        tracker.add(number, priority=priority)
    listed = tracker.board.order
    monkeypatch.setattr(tracker.board, "order", lambda: [row for row in listed() if row[0] != 7])
    assert run(tracker, code, *leaf_filing(tmp_path, "Leaf", parent="plan#2")) == 0
    assert tracker.board.items == [1, 7, 6, 4] and not _board_moves(tracker)


def test_the_updates_card_is_left_out_of_the_moves_and_placed_first_in_high_after_them(capsys):
    """Ruling plan#4014: the weekly update's card, High plan#4, at the bottom on a board a drag
    left Low plan#2 above Medium plan#3: the regroup's move over the board without it is sent
    first, Medium plan#3 under Urgent plan#1, then the card's, first in High, under plan#1 on the
    board that move leaves, so no move goes after the card about to leave the bottom."""
    tracker = FakeTracker()
    for number, priority in ((1, "Urgent"), (2, "Low"), (3, "Medium"), (4, "High")):
        tracker.add(number, priority=priority)
    filing_pass(tracker, tracker.cards_by_number[4], "PVTI_4", True, False)
    assert tracker.board.items == [1, 4, 3, 2]
    assert _regroup_lines(capsys.readouterr().out) == [
        "  " + _moved(3, "after plan#1"), "  " + _moved(4, "after plan#1", ruling="plan#4014")]


def test_a_card_a_person_gave_a_priority_while_filed_is_the_regroups(code, tmp_path):
    """A filing cut short, its card marked at the bottom, and a person set it Urgent meanwhile:
    run again, it is ranked, so its suggestion of High places nothing, and the regroup puts it at
    the end of the Urgent group, not under the High card."""
    tracker = _grouped()
    tracker.add(6, title="New", body="New work.", labels=("balance", FILING), effort="2",
                priority="Urgent")
    assert run(tracker, code, *_filing(tmp_path, "step", "High")) == 0
    assert tracker.board.items == [1, 6, 2, 3, 4, 5]
    assert _board_moves(tracker) == [(6, "PVTI_1")]


def test_a_filing_run_again_after_a_cut_places_its_card_again(code, tmp_path):
    """The move under the High cards fails, the card left marked at the bottom; the same command
    run again moves it there, then removes the mark, and a run after that writes nothing."""
    tracker = _grouped()
    FailOnce(tracker.board, "move")
    argv = _filing(tmp_path, "step", "High")
    assert run(tracker, code, *argv) == 2
    assert tracker.board.items == [1, 2, 3, 4, 5, 6]
    assert FILING in tracker.cards_by_number[6].labels
    assert run(tracker, code, *argv) == 0
    assert tracker.board.items == [1, 2, 6, 3, 4, 5]
    assert tracker.writes[-2:] == [("board_move", 6, "PVTI_2"), ("unmark", 6)]
    writes = list(tracker.writes)
    assert run(tracker, code, *argv) == 0
    assert tracker.writes == writes


def test_a_leaf_is_filed_in_its_split_steps_place_and_the_board_regrouped(code, tmp_path):
    """R-BAL179: the leaf of High plan#2 goes into its place and plan#2 leaves the board; the
    regroup that follows keeps it there, since it ranks High through plan#2, and moves the Low
    card a drag left above the Medium one."""
    tracker = FakeTracker()
    for number, priority in ((1, "High"), (2, "High"), (3, "Low"), (4, "Medium")):
        tracker.add(number, priority=priority)
    assert run(tracker, code, *leaf_filing(tmp_path, "Leaf", parent="plan#2")) == 0
    assert tracker.board.items == [1, 5, 4, 3]
    assert ("board_place", 5, "PVTI_2") in tracker.writes
    assert [number for number, _ in _board_moves(tracker)] in ([3], [4])


# -- the board's back-to-back move and its read back ---------------------------------------------

class _Writes:
    """GitHub as the board's writes meet it: each logged and answered empty, and any read
    refused, since a move reads nothing."""

    def __init__(self):
        """Nothing sent yet."""
        self.sent = []

    def graphql(self, query, **variables):
        """Log a write and answer it; refuse a read."""
        assert query.lstrip().startswith("mutation"), "a move reads nothing"
        self.sent.append((query, variables))
        return {}


def test_a_move_is_one_write_naming_the_board_and_reads_nothing():
    """:meth:`_tracker.Board.move` sends the top's or the after's write, naming the board as
    ``$p``, and no read: the caller reads back once, after the last."""
    github = _Writes()
    board = Board(github, PLAN, "PVT_p", sleep=lambda _seconds: None)
    board.move("A", None)
    board.move("B", "A")
    assert github.sent == [(BOARD_TOP, {"p": "PVT_p", "i": "A"}),
                           (BOARD_AFTER, {"p": "PVT_p", "i": "B", "a": "A"})]


#: Read backs of several placements on a board reading A, B, C, and whether each holds.
_READ_BACKS = pytest.mark.parametrize("placed, shown", [
    ([("B", "A"), ("C", "B")], True),
    ([("A", None), ("C", "B")], True),
    ([("B", "A"), ("C", "A")], False),
    ([("B", "A"), ("D", "C")], False),
    ([("B", None)], False),
    ([("D", None)], False),
], ids=["each-after-its-own", "first-and-after", "one-after-another", "one-not-on-it",
        "not-first", "not-on-it-first"])


@_READ_BACKS
def test_a_read_back_shows_the_moves_only_where_each_sits_right_after_its_own(placed, shown):
    """The board reads A, B, C: a read back of several placements holds only when every item
    sits right after the item paired with it (first when None); else it is waited on for the
    board's wait, then reported."""
    slept = []
    board = Board(_ReadBoard(("A", "B", "C")), PLAN, "PVT_p", sleep=slept.append)
    assert board.shows(placed) is shown
    assert sum(slept) == (0 if shown else BOARD_WAIT_SECONDS)


@_READ_BACKS
def test_the_fake_boards_read_back_holds_where_the_boards_does(placed, shown):
    """The fake board holding cards 1, 2 and 3, items A, B and C, answers each read back as
    the board does (``_board_lag.sits``), so a test of the regroup sees a read back asking for a
    place no item sits in (the review's M1, plan#3906)."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    assert tracker.board.items == [1, 2, 3]
    item = {"A": "PVTI_1", "B": "PVTI_2", "C": "PVTI_3", "D": "PVTI_4", None: None}
    assert tracker.board.shows([(item[at], item[after]) for at, after in placed]) is shown
