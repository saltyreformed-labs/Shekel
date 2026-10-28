"""The board's Status, one answer per card (``_status.wanted``, rulings plan#1873, plan#1906), and
the report that holds every write back while the field cannot hold an answer
(``_status.unwritable``): pure decisions over cards the in-memory tracker builds."""
from __future__ import annotations

from tools.quill._board_fields import STATUS_OPTIONS
from tools.quill._fake import FakeTracker
from tools.quill._status import Progress, unwritable, wanted
from tools.quill._tracker import Child, Statuses

#: A card the tool closed as not planned (``quill drop``), one a person closed, and one the tool
#: closed as completed: the display of git, which a revert can leave unshipped.
DROPPED = {"is_open": False, "state_reason": "NOT_PLANNED", "closed_by_tool": True}
BY_HAND = {"is_open": False, "state_reason": "COMPLETED", "touched_by_hand": True}
SHOWN_DONE = {"is_open": False, "state_reason": "COMPLETED", "closed_by_tool": True}


def _answer(tracker, number, **git):
    """:func:`_status.wanted` for card ``number`` over every card ``tracker`` holds, git saying
    ``git`` (each of :class:`_status.Progress`' sets by name, empty unless named)."""
    cards = tracker.cards_by_number
    return wanted(cards[number], cards, Progress(**{
        name: frozenset(git.get(name, ())) for name in ("shipped", "claimed", "done", "begun")}))


def test_a_task_shows_done_in_progress_dropped_or_todo_by_git_first():
    """Done once its file is in git, whatever its card shows (a dropped task done in git shows
    Done); In Progress while begun and not done; Dropped once the tool or a person closed it
    as anything but the tool's display of done; else Todo, a task the tool showed done that
    git no longer says is done included (sync reopens it)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(3, 1, **DROPPED)
    tracker.add_task(4, 1, **BY_HAND)
    tracker.add_task(5, 1, **SHOWN_DONE)
    assert [_answer(tracker, 2, done={2}), _answer(tracker, 2, begun={2}),
            _answer(tracker, 2)] == ["Done", "In Progress", "Todo"]
    assert [_answer(tracker, 3), _answer(tracker, 4), _answer(tracker, 3, done={3})] == [
        "Dropped", "Dropped", "Done"]
    assert _answer(tracker, 5) == "Todo"


def test_a_step_or_finding_shows_done_shipped_in_progress_claimed_dropped_or_todo():
    """Done once git says it shipped, claimed or not; In Progress while claimed; Dropped once
    dropped, unless git says it shipped; else Todo, one the tool showed shipped that a revert
    unshipped included.  A step and a finding answer alike."""
    for kind in ("step", "finding"):
        tracker = FakeTracker()
        tracker.add(1, kind)
        tracker.add(2, kind, **BY_HAND)
        tracker.add(3, kind, **SHOWN_DONE)
        assert [_answer(tracker, 1, shipped={1}, claimed={1}), _answer(tracker, 1, claimed={1}),
                _answer(tracker, 1)] == ["Done", "In Progress", "Todo"], kind
        assert [_answer(tracker, 2), _answer(tracker, 2, shipped={2}),
                _answer(tracker, 3)] == ["Dropped", "Done", "Todo"], kind


def test_a_split_step_shows_its_leaves_and_a_card_of_no_work_shows_none():
    """A split step, should the board hold it, shows what the tool shows it as
    (``_state.container_shown``): Todo while a leaf is still work, Done once every leaf
    shipped, Dropped once every leaf was dropped.  A question, a ruling and a card of no type
    hold none."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),))
    tracker.add(2, parent=1)
    assert [_answer(tracker, 1), _answer(tracker, 1, shipped={2})] == ["Todo", "Done"]
    tracker.add(2, parent=1, **DROPPED)
    assert _answer(tracker, 1) == "Dropped"
    tracker.add(4, "question")
    tracker.add(5, "ruling", is_open=False, state_reason="COMPLETED")
    tracker.add(6, None)
    assert [_answer(tracker, 4, claimed={4}), _answer(tracker, 5), _answer(tracker, 6)] == [
        None, None, None]


def test_no_status_is_written_while_the_field_is_renamed_or_lacks_an_option():
    """Ruling plan#1906: the board's Status holds the four options quill writes, Dropped added
    on the web; lacking one, or renamed, it can hold no answer, and the report says what to
    fix.  An extra option holds nothing back."""
    options = {name: f"O_{name}" for name in STATUS_OPTIONS}
    assert unwritable(Statuses("F", {**options, "Blocked": "O_B"}, {})) is None
    assert unwritable(Statuses(None, {}, {})) == (
        "the board has no single-select field named 'Status' (GitHub's built-in one was "
        "renamed), so no card's Status is written: rename it back (`python -m "
        "tools.quill.setup_tracker` checks it)")
    del options["Dropped"]
    assert unwritable(Statuses("F", options, {})) == (
        "the board's Status lacks Dropped, so no card's Status is written until it holds Todo, "
        "In Progress, Done, Dropped (rulings plan#1873, plan#1906): add Dropped on the web, in "
        "the board's settings under Status")
