"""A card's tasks in the order they are built (ruling plan#1868): ``quill file task`` places a
new task above the card's finishing tasks still open, or above the task ``--before`` names,
over the in-memory tracker (:class:`_fake.FakeTracker`), whose reorder keeps the order
``recorded/priority.json`` shows GitHub keeping.
"""
from __future__ import annotations

from tools.quill._fake import FailOnce, FakeTracker, run
from tools.quill._tasks import FINISHING

(REVIEW, _), (LAND, _) = FINISHING


def _card(review_done=False):
    """Step 1 and its tasks in order: 2 ("piece a"), then its finishing tasks 3 (the review,
    done when ``review_done``) and 4 (the suite and pull request)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1, title="piece a")
    tracker.add_task(3, 1, title=REVIEW, **({"is_open": False, "state_reason": "COMPLETED",
                                             "closed_by_tool": True} if review_done else {}))
    tracker.add_task(4, 1, title=LAND)
    return tracker


def _filing(tmp_path, title="piece b", before=()):
    """``quill file task`` filing ``title`` under plan#1, with ``before`` (``--before plan#T``)
    when given."""
    text = tmp_path / f"{title}.md"
    text.write_text(f"Build {title}.")
    return ("file", "task", "--parent", "plan#1", "--title", title, "--body-file", str(text),
            *before)


def _order(tracker):
    """plan#1's tasks in its order."""
    return [child.number for child in tracker.cards([1])[1].children]


def test_a_task_is_placed_above_the_cards_finishing_tasks(tmp_path, code, capsys):
    """Filed last, it is moved above the review, inside its filing, before its mark comes off
    (R-BAL202)."""
    tracker = _card()
    assert run(tracker, code, *_filing(tmp_path)) == 0
    assert _order(tracker) == [2, 5, 3, 4]
    assert tracker.writes == [("create", 5, "task", "piece b", ("filing",)), ("add_child", 1, 5),
                              ("place_child", 1, 5, 3), ("board_add", 5), ("unmark", 5)]
    assert "  in plan#1's order: placed above plan#3\n" in capsys.readouterr().out


def test_a_task_filed_after_the_review_is_placed_above_the_suite_and_pull_request(tmp_path,
                                                                                code):
    """The review done, a fix it found goes above the finishing task still open."""
    tracker = _card(review_done=True)
    assert run(tracker, code, *_filing(tmp_path, "the review's fix")) == 0
    assert _order(tracker) == [2, 3, 5, 4]


def test_a_task_is_placed_above_the_task_before_names(tmp_path, code):
    """A split or an added piece goes where it is built: above the open task named."""
    tracker = _card()
    assert run(tracker, code, *_filing(tmp_path, "piece a's first half",
                                       ("--before", "plan#2"))) == 0
    assert _order(tracker) == [5, 2, 3, 4]


def test_before_names_an_open_task_under_the_same_card(tmp_path, code, capsys):
    """A step, a task under another card and a task done are each refused, before anything is
    written."""
    tracker = _card()
    tracker.add(6)
    tracker.add_task(7, 6)
    tracker.add_task(8, 1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    for named in ("plan#6", "plan#7", "plan#8"):
        assert run(tracker, code, *_filing(tmp_path, "piece b", ("--before", named))) == 1
        assert ("is no open task under plan#1: a task is placed above another piece of its card "
                "still to build") in capsys.readouterr().err
    assert not tracker.writes


def test_a_finishing_task_and_a_task_with_none_open_stay_last(tmp_path, code, capsys):
    """The suite and pull request is never moved above the review; and with no finishing task
    open, a new task stays where its link put it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1, title=REVIEW)
    assert run(tracker, code, *_filing(tmp_path, LAND)) == 0
    assert _order(tracker) == [2, 3]
    other = FakeTracker()
    other.add(1)
    other.add_task(2, 1, title="piece a")
    capsys.readouterr()
    assert run(other, code, *_filing(tmp_path)) == 0
    assert _order(other) == [2, 3]
    assert "  in plan#1's order: last, no finishing task being open\n" in capsys.readouterr().out
    assert not [write for write in tracker.writes + other.writes if write[0] == "place_child"]


def test_a_task_filing_run_again_sends_no_second_move(tmp_path, code, capsys):
    """Cut short after its move, the same command finishes the filing and finds it in place."""
    tracker = _card()
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *_filing(tmp_path)) == 2
    capsys.readouterr()
    assert run(tracker, code, *_filing(tmp_path)) == 0
    assert "  in plan#1's order: already above plan#3\n" in capsys.readouterr().out
    assert [write for write in tracker.writes if write[0] == "place_child"] == [
        ("place_child", 1, 5, 3)]
    assert _order(tracker) == [2, 5, 3, 4]
