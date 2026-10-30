"""``quill arc`` and ``quill label`` (ruling plan#2221): a card's Arc set and its arc labels put
back to display it, and a label a person chooses added or removed, through the tracker's own
writes -- over :class:`_fake.FakeTracker` and a throwaway git repository (the ``code``
fixture, ``conftest.py``).  ``Tracker.set_arc`` and ``Tracker.relabel`` are graded against
GitHub's recorded answers in ``test__tracker_arc.py``.  Nothing here calls GitHub.
"""
from __future__ import annotations

import pytest

from tools.quill._arcs import ArcCard, ArcVerdict, arc_verdict
from tools.quill._fake import FailOnce, FakeTracker, run
from tools.quill.check import ruling_body
from tools.quill.setup_tracker import FILING

#: A ruling as its filing leaves it: closed as completed by the tool.
CLOSED_RULING = {"is_open": False, "state_reason": "COMPLETED", "closed_by_tool": True}


# -- arc ---------------------------------------------------------------------------------

def test_arc_moves_a_card_and_its_arc_label_keeping_its_other_labels(code, capsys):
    """The Arc is the arc's one home (R-BAL349) and its arc label display of it (R-BAL354):
    both move, in that order, each printed as it lands, and a label a person chose stays.
    ``quill sync``'s pass then finds nothing to put back, by the one rule."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", "security"))
    assert run(tracker, code, "arc", "plan#1", "--to", "tooling") == 0
    assert tracker.writes == [("set_arc", 1, "tooling"),
                              ("relabel", 1, ("tooling",), ("balance",))]
    assert capsys.readouterr().out == (
        "set plan#1 [step, balance] card 1's Arc to 'tooling'\n"
        "  put back its arc labels to display it (R-BAL354): removing ['balance'], adding "
        "['tooling']\n")
    moved = tracker.cards_by_number[1]
    assert (moved.arc, moved.labels) == ("tooling", ("security", "tooling"))
    assert arc_verdict(ArcCard(1, "step", moved.title, moved.labels, moved.arc),
                       False) == ArcVerdict()


def test_arc_run_again_writes_nothing(code, capsys):
    """A card that already holds the Arc, its labels displaying it, is written nothing."""
    tracker = FakeTracker()
    tracker.add(1, labels=("tooling",), arc="tooling")
    assert run(tracker, code, "arc", "plan#1", "--to", "tooling") == 0
    assert capsys.readouterr().out == (
        "plan#1 [step, tooling] card 1 holds the Arc 'tooling' already: not written\n")
    assert not tracker.writes


def test_arc_cut_short_after_its_arc_finishes_with_the_labels_alone(code, capsys):
    """A failed label write leaves the Arc moved and its old arc label held; the same command
    run again writes only the labels."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "relabel")
    assert run(tracker, code, "arc", "plan#1", "--to", "tooling") == 2
    assert tracker.writes == [("set_arc", 1, "tooling")]
    capsys.readouterr()
    assert run(tracker, code, "arc", "plan#1", "--to", "tooling") == 0
    assert tracker.writes == [("set_arc", 1, "tooling"),
                              ("relabel", 1, ("tooling",), ("balance",))]
    assert capsys.readouterr().out == (
        "plan#1 [step, tooling] card 1 holds the Arc 'tooling' already: not written\n"
        "  put back its arc labels to display it (R-BAL354): removing ['balance'], adding "
        "['tooling']\n")


def test_arc_moves_a_closed_ruling(code):
    """R-BAL220 keeps a ruling's title and text, the developer's words; its Arc is no part of
    them, so a closed ruling moves (plan#2178 and plan#2179 are to)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=1,
                **CLOSED_RULING)
    assert run(tracker, code, "arc", "plan#2", "--to", "tooling") == 0
    assert (tracker.cards_by_number[2].arc, tracker.cards_by_number[2].labels) == (
        "tooling", ("tooling",))


def test_arc_refuses_a_task(code, capsys):
    """A task's arc is its card's (R-BAL386): nothing is written."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    assert run(tracker, code, "arc", "plan#2", "--to", "tooling") == 1
    assert capsys.readouterr().err == (
        "refused: plan#2 [task, no arc] card 2 is a task, a piece of plan#1's work (R-BAL386): "
        "it holds no Arc and no label of its own, as its arc is its card's\n")
    assert not tracker.writes


@pytest.mark.parametrize(("state", "why"), [
    ({}, "finish its filing first (`quill show plan#1` says how)"),
    ({"is_open": False, "state_reason": "NOT_PLANNED", "touched_by_hand": True},
     "closed while still marked, it keeps them so that command files nothing over it"),
], ids=["unfinished", "closed-marked"])
@pytest.mark.parametrize("argv", [("arc", "plan#1", "--to", "tooling"),
                                  ("label", "plan#1", "--add", "security")],
                         ids=["arc", "label"])
def test_a_card_carrying_the_filing_mark_is_refused(code, capsys, argv, state, why):
    """Its ``quill file`` command, run again, refuses a card whose Arc or labels are not its
    own (``_filing._refuse_relabel``), so neither command changes one: nothing is written."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", FILING), **state)
    assert run(tracker, code, *argv) == 1
    assert capsys.readouterr().err == (
        "refused: plan#1 [step, balance] card 1 carries the 'filing' mark (R-BAL202): its "
        "`quill file` command, run again, refuses it once its Arc or labels are not its own "
        f"(R-BAL186); {why}\n")
    assert not tracker.writes


def test_arc_refuses_a_card_the_check_refuses(code, capsys):
    """The check every written card passes on its type and Arc (``check.violations``): a card
    with no type is no kind the plan reads, so nothing is written."""
    tracker = FakeTracker()
    tracker.add(1, kind=None)
    assert run(tracker, code, "arc", "plan#1", "--to", "tooling") == 1
    assert capsys.readouterr().err == (
        "refused: not moved:\n  its type is None, not one of step, finding, ruling, question\n")
    assert not tracker.writes


def test_arc_takes_only_an_arc(code, capsys):
    """``--to`` is one of the arcs, the command line's own refusal (exit 2) before anything is
    read."""
    tracker = FakeTracker()
    tracker.add(1)
    with pytest.raises(SystemExit) as exited:
        run(tracker, code, "arc", "plan#1", "--to", "security")
    assert exited.value.code == 2
    assert "invalid choice: 'security'" in capsys.readouterr().err
    assert not tracker.writes


# -- label -------------------------------------------------------------------------------

def test_label_adds_and_removes_a_label_its_other_labels_kept(code, capsys):
    """One write each, printed as it lands; the arc label stays, display of the Arc."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", "moves-money"))
    assert run(tracker, code, "label", "plan#1", "--add", "security") == 0
    assert run(tracker, code, "label", "plan#1", "--remove", "moves-money") == 0
    assert tracker.writes == [("relabel", 1, ("security",), ()),
                              ("relabel", 1, (), ("moves-money",))]
    assert capsys.readouterr().out == (
        "labelled plan#1 [step, balance] card 1 'security'\n"
        "took 'moves-money' off plan#1 [step, balance] card 1\n")
    assert tracker.cards_by_number[1].labels == ("balance", "security")


def test_label_run_again_writes_nothing(code, capsys):
    """A label carried already is not added, and one not carried is not removed (GitHub
    answers that removal 404, ``Tracker.relabel``)."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", "security"))
    assert run(tracker, code, "label", "plan#1", "--add", "security") == 0
    assert run(tracker, code, "label", "plan#1", "--remove", "moves-money") == 0
    assert capsys.readouterr().out == (
        "plan#1 [step, balance] card 1 carries 'security' already: not written\n"
        "plan#1 [step, balance] card 1 does not carry 'moves-money': not written\n")
    assert not tracker.writes


def test_label_labels_a_closed_ruling(code):
    """A ruling's label is no part of the developer's words (R-BAL220): plan#2178 and
    plan#2179 take ``security``."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=1,
                **CLOSED_RULING)
    assert run(tracker, code, "label", "plan#2", "--add", "security") == 0
    assert tracker.cards_by_number[2].labels == ("balance", "security")


def test_label_refuses_a_task(code, capsys):
    """A task holds no label of its own (R-BAL386): nothing is written."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    assert run(tracker, code, "label", "plan#2", "--add", "security") == 1
    assert "is a task, a piece of plan#1's work (R-BAL386)" in capsys.readouterr().err
    assert not tracker.writes


@pytest.mark.parametrize("label", ["balance", "tooling", FILING])
@pytest.mark.parametrize("flag", ["--add", "--remove"])
def test_label_takes_no_arc_label_and_not_the_filing_mark(code, capsys, flag, label):
    """An arc label displays the Arc, which ``quill arc`` moves, and the filing mark is quill's
    (R-BAL202): the command line refuses each (exit 2) before anything is read."""
    tracker = FakeTracker()
    tracker.add(1)
    with pytest.raises(SystemExit) as exited:
        run(tracker, code, "label", "plan#1", flag, label)
    assert exited.value.code == 2
    assert f"invalid choice: '{label}'" in capsys.readouterr().err
    assert not tracker.writes
