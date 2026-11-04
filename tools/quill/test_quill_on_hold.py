"""``quill claim`` of a step On hold (ruling plan#1832, plan#1820): refused with nothing written,
whether the mark is its own or a step's above it, and free once the mark is cleared; and what
``quill next`` prints of the order and of a step waiting on one (``_next``'s decisions are
``test__next.py``'s).  Over :class:`_fake.FakeTracker` and a throwaway git repository (the
``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from tools.quill import _git
from tools.quill._fake import FakeTracker, run, ship
from tools.quill._tracker import Child


def test_a_claim_of_a_step_on_hold_is_refused_with_nothing_written(code, capsys):
    """No write to the tracker and no branch on origin; the refusal names the mark."""
    tracker = FakeTracker()
    tracker.add(1, priority="On hold")
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "plan#1 [step, balance] card 1 is never offered as work: it is On hold" in (
        capsys.readouterr().err)
    assert not tracker.writes
    assert not _git.read(code).claims


def test_a_claim_of_a_leaf_of_a_split_step_on_hold_is_refused(code, capsys):
    """A leaf inherits the step above it (R-BAL182): its pause is that step's, by name."""
    tracker = FakeTracker()
    tracker.add(1, priority="On hold", on_board=False, children=(Child(2, "step", True),))
    tracker.add(2, parent=1)
    assert run(tracker, code, "claim", "plan#2") == 1
    assert "never offered as work: plan#1 above it is On hold" in capsys.readouterr().err
    assert not tracker.writes


def test_the_mark_cleared_the_claim_is_taken(code):
    """The pause is the mark and nothing else: clear it, and the same claim lands."""
    tracker = FakeTracker()
    tracker.add(1, priority="On hold")
    assert run(tracker, code, "claim", "plan#1") == 1
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert run(tracker, code, "claim", "plan#1") == 0
    assert 1 in _git.read(code).claims


def _next(tracker, code, capsys, *argv) -> list[str]:
    """What ``quill next`` prints, a line each; it exits 0."""
    capsys.readouterr()
    assert run(tracker, code, "next", *argv) == 0
    return capsys.readouterr().out.splitlines()


def test_next_hands_out_the_step_a_marked_step_needs_read_from_the_real_board(code, capsys):
    """The ruling's example with C marked High, through the command: D, then C once D ships."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    tracker.add(3, blocked_by=(4,), priority="High")
    tracker.add(4)
    assert _next(tracker, code, capsys)[0] == "next: plan#4 [step, balance] card 4"
    ship(code, 4)
    assert _next(tracker, code, capsys)[0] == "next: plan#3 [step, balance] card 3"
    assert not tracker.writes


def test_next_names_a_step_waiting_on_one_on_hold_and_hands_out_the_next(code, capsys):
    """Spec: "a step that needs one is reported as waiting on it, by name"; a person reading it
    knows which mark to lift."""
    tracker = FakeTracker()
    tracker.add(1, blocked_by=(3,))
    tracker.add(2)
    tracker.add(3, priority="On hold")
    assert _next(tracker, code, capsys) == [
        "next: plan#2 [step, balance] card 2",
        "WAITING: plan#1 [step, balance] card 1 needs plan#3 [step, balance] card 3, which is "
        "On hold",
    ]


def test_next_with_nothing_to_hand_out_still_says_what_waits(code, capsys):
    """Every step paused or waiting: nothing is the answer, and why is on the next line."""
    tracker = FakeTracker()
    tracker.add(1, blocked_by=(2,))
    tracker.add(2, priority="On hold")
    assert _next(tracker, code, capsys, "--arc", "balance") == [
        "next in balance: nothing",
        "WAITING: plan#1 [step, balance] card 1 needs plan#2 [step, balance] card 2, which is "
        "On hold",
    ]
