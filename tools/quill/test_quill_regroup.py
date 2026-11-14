"""The regroup's writes (rulings plan#4011, plan#4012, plan#4013; :mod:`_regroup_pass`): a ``quill
sync`` pass and a ``quill priority`` run each leave the board grouped, Urgent, then High, Medium,
Low and the rest, with the developer's order kept inside each group; a dry run writes nothing; a
second pass moves nothing; a card ``sync`` closes in the same pass is never moved; a clear
through quill puts the card at the top of the no-Priority group.  Over the in-memory tracker
(:class:`_fake.FakeTracker`) and the ``code`` fixture's real git; the board's own back-to-back
move and its read back of several placements over scripted GitHub answers.  The plan itself is
``test__regroup.py``'s.
"""
from __future__ import annotations

import pytest

from tools.quill import _git, _sync
from tools.quill._board_lag import BOARD_WAIT_SECONDS
from tools.quill._fake import FakeTracker, run, ship, unship
from tools.quill._regroup import moves
from tools.quill._regroup_pass import board_cards
from tools.quill._tracker import BOARD_AFTER, BOARD_TOP, Board, Child
from tools.quill.setup_tracker import PLAN
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


def _out_of_group():
    """A board as the developer left it: Low plan#1, High plan#2, Medium plan#3, and plan#4,
    which holds no Priority, so the regroup leaves it alone (ruling plan#4017)."""
    tracker = FakeTracker()
    tracker.add(1, priority="Low")
    tracker.add(2, priority="High")
    tracker.add(3, priority="Medium")
    tracker.add(4)
    return tracker


# -- quill sync ---------------------------------------------------------------------------------

def test_a_sync_pass_leaves_the_board_grouped_and_a_second_pass_moves_nothing(code, capsys):
    """The Low card goes under the Medium one, the High and Medium cards already in order stay,
    and the card holding no Priority stays at the bottom; the move is printed and read back.
    Run again, it finds the board grouped and moves nothing."""
    tracker = _out_of_group()
    assert run(tracker, code, "sync") == 0
    assert tracker.board.items == [2, 3, 1, 4]
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == [_moved(1, "after plan#3")]
    assert run(tracker, code, "sync") == 0
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == []


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


def test_the_read_by_the_boards_rows_plans_what_syncs_read_plans(code):
    """``quill priority``'s read (:func:`_regroup_pass.board_cards`: the board's rows by number
    and every step above each) plans the same moves as ``sync``'s read of every card, over a
    board holding a leaf ranked through its split step, a leaf paused through one, a card On
    hold, a closed card and one holding no Priority; and the plan moves cards, so the two agree
    on something."""
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
    planned = moves(order, wide)
    assert planned
    assert moves(order, board_cards(tracker, [number for number, _ in order])) == planned


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
    """Ruling plan#4013 applied under R-BAL207: High plan#1 cleared goes just under the Low card,
    above the cards holding none; the same command run again finds it there and moves
    nothing."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="High")
    tracker.add(3, priority="Low")
    tracker.add(4)
    tracker.add(5)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert tracker.board.items == [2, 3, 1, 4, 5]
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == [
        _moved(1, "after plan#3", ruling="plan#4013")]
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert _board_moves(tracker) == [(1, "PVTI_3")]
    assert _regroup_lines(capsys.readouterr().out) == []


def test_a_clear_of_a_card_holding_none_puts_it_there_too(code):
    """The command's end state: a card holding no Priority, waiting under the High cards as a
    new card suggested High does (ruling plan#4017), cleared, goes under the Low cards."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2)
    tracker.add(3, priority="Low")
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert tracker.board.items == [1, 3, 2]
    assert not [write for write in tracker.writes if write[0] == "set_field"]


def test_a_clear_the_regroup_moves_with_others_sends_the_cards_move_after_theirs(code, capsys):
    """A drag left Low plan#3 above Medium plan#4: the clear of High plan#1 sends the regroup's
    move first (Medium plan#4 up, of the two single moves that group the rest, the one keeping
    the earlier run, ``_regroup._kept``), then the card's, after it, where the board as that move
    leaves it puts the top of the no-Priority group."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="High")
    tracker.add(3, priority="Low")
    tracker.add(4, priority="Medium")
    tracker.add(5)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert tracker.board.items == [2, 4, 3, 1, 5]
    assert _regroup_lines(capsys.readouterr().out) == [
        _moved(4, "after plan#2"), _moved(1, "after plan#3", ruling="plan#4013")]


def test_a_cleared_card_is_left_out_of_the_plan_so_no_move_goes_after_it(code, capsys):
    """High plan#2, the last High card, cleared, above Low plan#4 and Medium plan#3: the plan is
    made over the board without it (``_regroup.goes_after``'s contract), so Medium plan#3 goes
    just under High plan#1, not under the card about to leave, and then the card goes under
    the Low card."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="High")
    tracker.add(4, priority="Low")
    tracker.add(3, priority="Medium")
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert tracker.board.items == [1, 3, 4, 2]
    assert _regroup_lines(capsys.readouterr().out) == [
        _moved(3, "after plan#1"), _moved(2, "after plan#4", ruling="plan#4013")]


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
    """Task plan#2 under plan#1, which holds none, on the board under it, holding High."""
    tracker.add_task(2, 1, priority="High")


def _off_the_board(tracker):
    """Step plan#2, off the board, holding High."""
    tracker.add(2, priority="High", on_board=False)


@pytest.mark.parametrize("second", [_closed_step, _task, _off_the_board],
                         ids=["closed", "task", "off-the-board"])
def test_a_clear_of_a_card_in_no_place_of_the_order_places_nothing(code, second):
    """A closed card, a task (in no place of the board's order, ruling plan#1814) and a card off
    the board, cleared, are never placed, though nothing ranks them; the board, grouped, moves
    nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    second(tracker)
    tracker.add(3, priority="Low")
    held = list(tracker.board.items)
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert tracker.board.items == held and not _board_moves(tracker)


def test_a_cleared_card_the_boards_order_read_does_not_list_yet_is_placed(code, monkeypatch):
    """The board's order read lags the card's place on it: the card is placed all the same,
    since where it sits is unread."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    tracker.add(2, priority="Low")
    tracker.add(3)
    listed = tracker.board.order
    monkeypatch.setattr(tracker.board, "order",
                        lambda: [row for row in listed() if row[0] != 1])
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert _board_moves(tracker) == [(1, "PVTI_2")]


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


@pytest.mark.parametrize("placed, shown", [
    ([("B", "A"), ("C", "B")], True),
    ([("A", None), ("C", "B")], True),
    ([("B", "A"), ("C", "A")], False),
    ([("B", "A"), ("D", "C")], False),
    ([("B", None)], False),
    ([("D", None)], False),
], ids=["each-after-its-own", "first-and-after", "one-after-another", "one-not-on-it",
        "not-first", "not-on-it-first"])
def test_a_read_back_shows_the_moves_only_where_each_sits_right_after_its_own(placed, shown):
    """The board reads A, B, C: a read back of several placements holds only when every item
    sits right after the item paired with it (first when None); else it is waited on for the
    board's wait, then reported."""
    slept = []
    board = Board(_ReadBoard(("A", "B", "C")), PLAN, "PVT_p", sleep=slept.append)
    assert board.shows(placed) is shown
    assert sum(slept) == (0 if shown else BOARD_WAIT_SECONDS)
