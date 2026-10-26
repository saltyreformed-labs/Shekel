"""Every door that sets or changes a finding's owner keeps Josh's rule of 2026-10-26 (ruling
plan#1784, "One owner, always a leaf"): an open finding has exactly one owner, and it is a
step a branch builds -- live work, never a step split into smaller steps -- so some pull
request always owes it.  The doors are the filing, ``quill block``, the split (``quill file
step --parent``) and ``quill drop``; the hand-off is ``quill hand`` (``test_quill_findings``),
and the backstop for a hand edit on the web is CI's landing check
(``test_landing_findings``).  Each door both ways, over :class:`_fake.FakeTracker` and a
throwaway git repository (the ``code`` fixture, ``conftest.py``).
"""
from __future__ import annotations

from tools.quill._fake import FakeTracker, run
from tools.quill._tracker import Child
from tools.quill.setup_tracker import FILING


def _split(tracker, step=1, leaf=2):
    """Step ``step`` split into the one leaf ``leaf``, both open."""
    tracker.add(step, children=(Child(leaf, "step", True),), on_board=False)
    tracker.add(leaf, parent=step)


def _finding(owner):
    """``quill file finding`` filing the finding "Twice" for ``owner``."""
    return ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", owner,
            "--text", "The report counts a refund twice.")


# -- the filing ------------------------------------------------------------------------------

def test_a_finding_filed_for_a_split_step_is_refused_with_nothing_written(code, capsys):
    """A split step is live, but no branch builds it, so it is never a finding's owner: the
    check refuses it before anything is sent, naming why."""
    tracker = FakeTracker()
    _split(tracker)
    assert run(tracker, code, *_finding("plan#1")) == 1
    assert ("its owner is a step split into smaller steps" in capsys.readouterr().err
            and not tracker.writes)


def test_a_finding_filed_for_the_leaf_of_a_split_step_is_filed(code):
    """The leaf is live work: the finding is filed blocked by it, its owner."""
    tracker = FakeTracker()
    _split(tracker)
    assert run(tracker, code, *_finding("plan#2")) == 0
    assert tracker.writes == [("create", 3, "finding", "Twice", ("balance", FILING)),
                              ("block", 3, 2), ("unmark", 3)]
