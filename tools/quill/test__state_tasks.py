"""The plan's decisions about tasks (ruling ``balance:R-BAL386``) -- which are done, what
``sync`` writes and reports, what a card's drop takes -- over hand-built cards and claims.

A task is a sub-issue of type ``task`` under a step or finding, done when its file
``plan/tasks/N`` is in the merge of ``dev`` and ``main`` or the live claim of the card it is
under wrote it; its open or closed state is the tool's display of that.
"""
from __future__ import annotations

from tools.quill._git import Claim
from tools.quill._state import done_tasks, drop_takes, sync_plan
from tools.quill._tracker import Card, Child


def _card(number, kind="step", **changes):
    """An open card, unclaimed and unblocked, with ``changes`` applied."""
    fields = {
        "number": number, "id": 1000 + number, "node_id": f"I_{number}", "title": f"card {number}",
        "kind": kind, "labels": ("balance",), "is_open": True, "state_reason": None,
        "parent": None, "children": (), "blocked_by": (), "board_item": None,
        "closed_by_tool": False, "touched_by_hand": False, "outside": (),
    }
    return Card(**{**fields, **changes})


def _task(number, parent, **changes):
    """An open task under the card ``parent``, with ``changes`` applied."""
    return _card(number, "task", labels=(), parent=parent, **changes)


def _closed(card, by_tool=True, reason="COMPLETED"):
    """``card`` closed: by the tool (sync or drop) or by a person."""
    return Card(**{**card.__dict__, "is_open": False, "state_reason": reason,
                   "closed_by_tool": by_tool, "touched_by_hand": not by_tool})


def _with(parent, *tasks):
    """``parent`` listing ``tasks`` as its sub-issues, and the tasks, by number."""
    children = tuple(Child(task.number, "task", task.is_open) for task in tasks)
    linked = Card(**{**parent.__dict__, "children": (*parent.children, *children)})
    return {linked.number: linked, **{task.number: task for task in tasks}}


def _claim(number, *tasks):
    """The live claim ``card/<number>``, its own commits having written the file of each of
    ``tasks``."""
    return Claim(number, f"sha{number}", "2026-10-24T08:00:00+00:00", "2026-10-24T07:00:00Z",
                 frozenset(tasks))


def test_a_task_is_done_by_its_file_on_dev_and_main_or_on_its_own_cards_claim():
    """R-BAL386: its file in the merge of dev and main, or written by its card's live claim.  A
    file on another card's claim, and one naming a card that is no task, make nothing done."""
    cards = {**_with(_card(1), _task(11, 1), _task(12, 1), _task(13, 1)),
             **_with(_card(2), _task(21, 2)), 3: _card(3)}
    claims = {1: _claim(1, 12, 21), 2: _claim(2)}
    assert done_tasks(cards, {11, 3}, claims) == {11, 12}


def test_sync_shows_each_tasks_state_from_git_both_ways():
    """An open task git says is done is closed as completed; one the tool closed that git no
    longer says is done (a revert took its file, or its claim was released) is reopened; a
    person's close is a drop, left whatever git says; the tool's drop is left too."""
    tasks = (_task(11, 1), _closed(_task(12, 1)), _closed(_task(13, 1), by_tool=False),
             _closed(_task(14, 1), by_tool=False, reason="NOT_PLANNED"),
             _closed(_task(15, 1), reason="NOT_PLANNED"), _closed(_task(16, 1)))
    plan = sync_plan(_with(_card(1), *tasks), set(), {1: _claim(1, 11, 13, 15, 16)})
    assert (plan.close, plan.reopen, plan.drop, plan.reports) == ([11], [12], [], [])


def test_sync_reports_a_done_task_a_person_reopened():
    """A person's reopen is never overridden: it is reported, as a step's is."""
    reopened = _task(11, 1, touched_by_hand=True)
    plan = sync_plan(_with(_card(1), reopened), set(), {}, {11})
    assert not plan.close
    assert plan.reports == [
        "task plan#11 is done in git (plan/tasks/11 is on its card's claim, or on origin/dev "
        "or origin/main) but a person reopened it: close it by hand, or take its file out of "
        "the branch that holds it"]


def test_an_open_task_its_card_can_never_take_is_reported_never_closed():
    """Under a card that was dropped, is no work (a split step, a ruling), or shipped without
    its file, or under no card: reported, and closed by no write; under work still to do it
    is not; a task reopened now under a card shipped without its file is reported at once."""
    leaf = _card(41, parent=4)
    split = Card(**{**_card(4).__dict__, "children": (Child(41, "step", True),)})
    cards = {
        **_with(_closed(_card(1), by_tool=False, reason="NOT_PLANNED"), _task(11, 1)),
        **_with(split, _task(42, 4)), 41: leaf,
        **_with(_closed(_card(5, "ruling")), _task(51, 5)),
        **_with(_closed(_card(6)), _task(61, 6), _closed(_task(62, 6))),
        **_with(_card(7), _task(71, 7)),
        8: _task(8, None),
    }
    plan = sync_plan(cards, {6}, {})
    assert (plan.close, plan.reopen, plan.drop) == ([], [62], [])
    drop = "so it is never done: drop it (`quill drop plan#{0}`)"
    assert plan.reports == [
        "task plan#8 is open under no card: link it under the step or finding it is a piece of, "
        "or drop it (`quill drop plan#8`)",
        f"task plan#11 is open under plan#1, which was dropped, {drop.format(11)}",
        f"task plan#42 is open under plan#4, which is a container, which no change ships, "
        f"{drop.format(42)}",
        f"task plan#51 is open under plan#5, which is a ruling, which no change ships, "
        f"{drop.format(51)}",
        f"task plan#61 is open under plan#6, which shipped without plan/tasks/61, "
        f"{drop.format(61)}",
        f"task plan#62 is open under plan#6, which shipped without plan/tasks/62, "
        f"{drop.format(62)}",
    ]


def test_a_task_file_that_marks_nothing_done_is_named():
    """On dev and main, a file naming a card that does not exist or is no task is HISTORY,
    which no tracker write changes (R-BAL184); written by a live claim, one naming no task, or
    a task under another card or none, is a REPORT: a person takes it off that branch."""
    cards = {**_with(_card(1), _task(11, 1)), **_with(_card(2), _task(21, 2)), 3: _card(3),
             9: _task(9, None)}
    plan = sync_plan(cards, set(), {1: _claim(1, 11, 21, 3, 77, 9)}, {3, 77, 21})
    assert plan.history == [
        "the task file plan/tasks/3 on origin/dev or origin/main marks nothing done (plan#3 is "
        "a step, no task): it is not acted on",
        "the task file plan/tasks/77 on origin/dev or origin/main marks nothing done (plan#77 "
        "does not exist): it is not acted on",
    ]
    tail = ("so it marks nothing done (a task is done on its own card's branch only): take it "
            "off card/1")
    assert plan.reports == [
        f"card/1's tip holds plan/tasks/3, but plan#3 is a step, no task, {tail}",
        f"card/1's tip holds plan/tasks/9, but task plan#9 is under no card, not plan#1, {tail}",
        f"card/1's tip holds plan/tasks/21, but task plan#21 is under plan#2, not plan#1, {tail}",
        f"card/1's tip holds plan/tasks/77, but plan#77 does not exist, {tail}",
        "task plan#9 is open under no card: link it under the step or finding it is a piece of, "
        "or drop it (`quill drop plan#9`)",
    ]
    assert plan.close == [11, 21]


def test_a_cards_drop_takes_each_task_still_to_do_and_leaves_the_rest():
    """R-BAL190's shape: open, or closed by the tool as done (its display of git), is taken;
    a person's close and the tool's drop are left; and one whose file is on dev or main,
    which no drop undoes."""
    assert drop_takes(_task(11, 1), set())
    assert drop_takes(_closed(_task(12, 1)), set())
    assert not drop_takes(_closed(_task(13, 1), by_tool=False), set())
    assert not drop_takes(_closed(_task(14, 1), reason="NOT_PLANNED"), set())
    assert not drop_takes(_task(15, 1), {15})
