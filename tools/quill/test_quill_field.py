"""``quill priority`` and ``quill effort`` (plan#2491): a card's Priority or Effort set or
cleared through the tracker's one write of a field value, and no Effort set on a step split into
smaller steps (ruling plan#2555), over :class:`_fake.FakeTracker` and a throwaway git repository
(the ``code`` fixture, ``conftest.py``).  ``Tracker.set_field`` is
graded against GitHub's answers in ``test__tracker_arc.py``.  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill import _git
from tools.quill._fake import FakeTracker, run, ship
from tools.quill._tracker import Child
from tools.quill.check import ruling_body
from tools.quill.setup_tracker import FILING

#: A ruling as its filing leaves it: closed as completed by the tool.
CLOSED_RULING = {"is_open": False, "state_reason": "COMPLETED", "closed_by_tool": True}
#: How the check refuses each command's field on a kind that may not hold it, before the kind.
RULES = {
    "effort": "Effort is held by a step or finding alone (ruling plan#2506)",
    "priority": "Priority is held by a step, finding or question alone (ruling plan#2507)",
}


@pytest.mark.parametrize(("command", "field", "value"), [
    ("priority", "Priority", "High"), ("effort", "Effort", "2")])
def test_a_step_s_field_is_set_and_cleared(code, capsys, command, field, value):
    """The one write of the value, then its clear, each printed as it lands; the card's fact
    holding it is named as the command is."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, command, "plan#1", "--to", value) == 0
    assert getattr(tracker.cards_by_number[1], command) == value
    assert run(tracker, code, command, "plan#1", "--clear") == 0
    assert getattr(tracker.cards_by_number[1], command) is None
    assert tracker.writes == [("set_field", 1, field, value), ("set_field", 1, field, None)]
    assert capsys.readouterr().out == (
        f"set plan#1 [step, balance] card 1's {field} to {value!r}\n"
        f"cleared plan#1 [step, balance] card 1's {field}, which was {value!r}\n")


def test_a_value_held_is_replaced_and_the_other_field_kept(code, capsys):
    """REST's add replaces the value the field holds; the card's other field is untouched."""
    tracker = FakeTracker()
    tracker.add(1, priority="Low", effort="3")
    assert run(tracker, code, "effort", "plan#1", "--to", "5+") == 0
    assert tracker.writes == [("set_field", 1, "Effort", "5+")]
    assert capsys.readouterr().out == (
        "set plan#1 [step, balance] card 1's Effort to '5+', which was '3'\n")
    held = tracker.cards_by_number[1]
    assert (held.priority, held.effort) == ("Low", "5+")


def test_run_again_writes_nothing(code, capsys):
    """A card holding the value already, or no value for a clear, is written nothing: GitHub
    refuses a clear of a value the card does not hold (``_fake.FakeTracker.set_field``)."""
    tracker = FakeTracker()
    tracker.add(1, priority="High")
    assert run(tracker, code, "priority", "plan#1", "--to", "High") == 0
    assert run(tracker, code, "effort", "plan#1", "--clear") == 0
    assert not tracker.writes
    assert capsys.readouterr().out == (
        "plan#1 [step, balance] card 1 holds the Priority 'High' already: not written\n"
        "plan#1 [step, balance] card 1 holds no Effort already: not written\n")


@pytest.mark.parametrize(("kind", "command", "value"), [
    ("finding", "effort", "1"), ("finding", "priority", "Urgent"),
    ("question", "priority", "Medium")])
def test_each_kind_that_may_hold_a_field_takes_it(code, kind, command, value):
    """Rulings plan#2506 and plan#2507: a finding holds both, and a question a Priority."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, kind, parent=1)
    assert run(tracker, code, command, "plan#2", "--to", value) == 0
    assert getattr(tracker.cards_by_number[2], command) == value


@pytest.mark.parametrize(("kind", "command", "value"), [
    ("question", "effort", "2"), ("ruling", "effort", "2"), ("task", "effort", "1"),
    ("ruling", "priority", "High"), ("task", "priority", "Low")])
def test_a_value_on_a_kind_that_may_not_hold_it_is_refused(code, capsys, kind, command, value):
    """The check every written card passes (``check.violations``): nothing is written."""
    tracker = FakeTracker()
    tracker.add(1)
    if kind == "task":
        tracker.add_task(2, 1)
    elif kind == "ruling":
        tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=1,
                    **CLOSED_RULING)
    else:
        tracker.add(2, kind, parent=1)
    assert run(tracker, code, command, "plan#2", "--to", value) == 1
    assert capsys.readouterr().err == (
        f"refused: not written:\n  {RULES[command]}; this {kind} would hold {value!r}\n")
    assert not tracker.writes


@pytest.mark.parametrize("kind", ["ruling", "task"])
def test_a_value_set_on_the_web_where_none_belongs_is_cleared(code, kind):
    """A clear writes no value, so the check passes it on any card: quill takes off a Priority
    or an Effort a person set on the web on a ruling or a task."""
    tracker = FakeTracker()
    tracker.add(1)
    held = {"priority": "High", "effort": "2"}
    if kind == "task":
        tracker.add_task(2, 1, **held)
    else:
        tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=1,
                    **CLOSED_RULING, **held)
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert run(tracker, code, "effort", "plan#2", "--clear") == 0
    assert tracker.writes == [("set_field", 2, "Priority", None), ("set_field", 2, "Effort", None)]


#: A card still being filed (R-BAL202): marked and open, and a ruling its own filing closed
#: before its last write removed the mark.
STILL_FILED = [{}, {"kind": "ruling", **CLOSED_RULING}]


@pytest.mark.parametrize("state", STILL_FILED, ids=["open", "closed-ruling"])
@pytest.mark.parametrize("argv", [("priority", "plan#1", "--to", "High"),
                                  ("effort", "plan#1", "--clear")], ids=["priority", "effort"])
def test_a_card_still_being_filed_is_refused(code, capsys, argv, state):
    """Its ``quill file`` command, run again, finishes it (R-BAL186): nothing is written."""
    tracker = FakeTracker()
    card = tracker.add(1, labels=("balance", FILING), effort="2", **state)
    assert run(tracker, code, *argv) == 1
    assert capsys.readouterr().err == (
        f"refused: plan#1 [{card.kind}, balance] card 1's filing has not finished (R-BAL202): "
        "its `quill file` command, run again, finishes it (R-BAL186), so finish it first "
        "(`quill show plan#1` says how)\n")
    assert not tracker.writes


def test_a_closed_card_and_one_closed_while_still_marked_are_written(code):
    """A closed step is still work a branch built, and one dropped before its filing finished
    is no longer being filed (``_state.filing_unfinished``)."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(2, labels=("balance", FILING), is_open=False, state_reason="NOT_PLANNED",
                touched_by_hand=True)
    assert run(tracker, code, "effort", "plan#1", "--to", "4") == 0
    assert run(tracker, code, "priority", "plan#2", "--to", "Low") == 0
    assert tracker.writes == [("set_field", 1, "Effort", "4"), ("set_field", 2, "Priority", "Low")]


def test_a_card_the_check_refuses_on_its_type_is_not_written(code, capsys):
    """The check every written card passes on its type and Arc: a card with no type is no
    kind the plan reads, so nothing is written, a clear included."""
    tracker = FakeTracker()
    tracker.add(1, kind=None, effort="2")
    assert run(tracker, code, "effort", "plan#1", "--clear") == 1
    assert capsys.readouterr().err == (
        "refused: not written:\n  its type is None, not one of step, finding, ruling, question\n")
    assert not tracker.writes


def test_a_card_that_does_not_exist_is_refused(code, capsys):
    """Nothing to write: refused, as every command refuses a number nobody filed."""
    assert run(FakeTracker(), code, "priority", "plan#9", "--to", "High") == 1
    assert capsys.readouterr().err == "refused: plan#9 does not exist\n"


@pytest.mark.parametrize("argv", [
    ("effort", "plan#1", "--to", "6"), ("effort", "plan#1", "--to", "3-4"),
    ("effort", "plan#1", "--to", "High"), ("priority", "plan#1", "--to", "Critical"),
    ("priority", "plan#1", "--to", "high"), ("priority", "plan#1"),
    ("effort", "plan#1", "--to", "2", "--clear"),
])
def test_the_command_line_takes_one_of_the_field_s_options_or_a_clear(code, capsys, argv):
    """``--to`` is one of the field's options as ``setup_tracker.ISSUE_FIELDS`` declares them
    (Effort's ruling plan#2508), or ``--clear`` in its place, one of the two: the command
    line's own refusal (exit 2) before anything is read."""
    tracker = FakeTracker()
    tracker.add(1)
    with pytest.raises(SystemExit) as exited:
        run(tracker, code, *argv)
    assert exited.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert not tracker.writes


@pytest.mark.parametrize(("command", "options"), [
    ("priority", ("Urgent", "High", "Medium", "Low")), ("effort", ("1", "2", "3", "4", "5+"))])
def test_every_declared_option_is_taken(code, command, options):
    """Each option the field declares is a value the command writes."""
    tracker = FakeTracker()
    tracker.add(1)
    for option in options:
        assert run(tracker, code, command, "plan#1", "--to", option) == 0
    assert [write[3] for write in tracker.writes] == list(options)


def _split(tracker, effort=None, **leaf):
    """Step #1, holding ``effort``, with the step #2 linked under it, off the board, ``leaf``
    its facts."""
    tracker.add(1, effort=effort)
    tracker.add(2, parent=1, on_board=False, **leaf)
    tracker.cards_by_number[1] = replace(tracker.cards_by_number[1],
                                         children=(Child(2, "step", True),))


#: A step closed while still being filed: dropped by a person on the web before its filing's
#: last write removed its mark (R-BAL202).
DROPPED_WHILE_FILED = {"labels": ("balance", FILING), "is_open": False,
                       "state_reason": "NOT_PLANNED", "touched_by_hand": True}


def test_an_effort_on_a_split_step_is_refused_and_its_clear_taken(code, capsys):
    """Ruling plan#2555: #1, split into #2, holds an Effort a person set on the web.  A value is
    refused, naming the clear while it holds one; the clear is written; and its Priority, no
    part of the ruling, is still set."""
    tracker = FakeTracker()
    _split(tracker, effort="4")
    said = ("refused: plan#1 [step, balance] card 1 is split into smaller steps, and a split "
            "step holds no Effort (ruling plan#2555)")
    assert run(tracker, code, "effort", "plan#1", "--to", "3") == 1
    assert capsys.readouterr().err == (
        f"{said}; it holds '4', which `quill effort plan#1 --clear` clears\n")
    assert not tracker.writes
    assert run(tracker, code, "effort", "plan#1", "--clear") == 0
    assert run(tracker, code, "effort", "plan#1", "--to", "3") == 1
    assert capsys.readouterr().err == f"{said}\n"
    assert run(tracker, code, "priority", "plan#1", "--to", "High") == 0
    assert tracker.writes == [("set_field", 1, "Effort", None),
                              ("set_field", 1, "Priority", "High")]


def test_a_step_whose_one_linked_step_was_dropped_while_filed_takes_an_effort(code):
    """A leaf dropped before its filing finished is no leaf (``_state.leaves``), so before
    ``sync`` unlinks it #1 is a plain step, which holds an Effort."""
    tracker = FakeTracker()
    _split(tracker, **DROPPED_WHILE_FILED)
    assert run(tracker, code, "effort", "plan#1", "--to", "3") == 0
    assert tracker.writes == [("set_field", 1, "Effort", "3")]


def test_a_linked_step_closed_while_filed_that_git_says_shipped_is_a_leaf(code, capsys):
    """Git's answer decides it: the same step #2, shipped (its marker on ``dev``), is a leaf,
    so #1 is split, and the value is refused, nothing written."""
    tracker = FakeTracker()
    _split(tracker, **DROPPED_WHILE_FILED)
    ship(code, 2)
    assert run(tracker, code, "effort", "plan#1", "--to", "3") == 1
    assert "is split into smaller steps" in capsys.readouterr().err
    assert not tracker.writes


def test_git_is_read_only_for_a_step_with_a_step_linked_under_it(code, monkeypatch):
    """A step with no step under it (a task alone) and a finding are no split step, decided
    without git, and so is any clear, a split step's among them."""
    def unread(_root):
        raise AssertionError("git was read")

    plain, split = FakeTracker(), FakeTracker()
    plain.add(1)
    plain.add_task(2, 1)
    plain.add(3, "finding", parent=1)
    _split(split, effort="4")
    monkeypatch.setattr(_git, "read", unread)
    assert run(plain, code, "effort", "plan#1", "--to", "2") == 0
    assert run(plain, code, "effort", "plan#3", "--to", "1") == 0
    assert run(plain, code, "effort", "plan#1", "--clear") == 0
    assert run(split, code, "effort", "plan#1", "--clear") == 0
    assert plain.writes == [("set_field", 1, "Effort", "2"), ("set_field", 3, "Effort", "1"),
                            ("set_field", 1, "Effort", None)]
    assert split.writes == [("set_field", 1, "Effort", None)]
