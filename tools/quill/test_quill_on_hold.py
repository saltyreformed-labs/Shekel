"""``quill claim`` of a step On hold (ruling plan#1832, plan#1820): refused with nothing written,
whether the mark is its own or a step's above it, and free once the mark is cleared.  Over
:class:`_fake.FakeTracker` and a throwaway git repository (the ``code`` fixture,
``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from tools.quill import _git
from tools.quill._fake import FakeTracker, run
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
