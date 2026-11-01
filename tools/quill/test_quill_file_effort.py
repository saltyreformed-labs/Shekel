"""A step or a finding is sized when it is filed (ruling plan#2492): ``quill file step`` and
``quill file finding`` require ``--effort``, its choices the field's options, and file the card
holding it; no other kind takes one (ruling plan#2506); a filing run again writes the Effort the
card it finishes does not hold, and one that finished is never rewritten; and a question
converted into its ruling has its Priority cleared, and any Effort a person set on it, as a
ruling holds neither (rulings plan#2506, plan#2507).  The fake tracker holds what a create
filed on the card, which each test reads; the create's one call carrying it is
``test__tracker_arc.py``'s.
"""
from __future__ import annotations

import dataclasses

import pytest

from tools.quill import quill
from tools.quill._fake import FailOnce, FakeTracker, finding_filing, ruling_filing, run
from tools.quill._fields import EFFORT_FIELD, PRIORITY_FIELD
from tools.quill._tracker import TrackerError
from tools.quill.check import EFFORT, HOLDERS
from tools.quill.setup_tracker import CARD_TYPES, FILING, ISSUE_FIELDS, TASK


def _step(tmp_path, *effort):
    """``quill file step`` filing the top-level step "New", its spec "New work." written to a
    file, ``effort`` (``--effort`` and its value, or nothing) after it."""
    spec = tmp_path / "new.md"
    spec.write_text("New work.")
    return ("file", "step", "--arc", "balance", "--title", "New", "--body-file", str(spec),
            *effort)


def _filings(tmp_path):
    """Each kind's ``quill file`` command line naming no Effort, by kind, under or for plan#1
    (a step's is top-level); its files written."""
    body = tmp_path / "body.md"
    body.write_text("Which day?")
    return {
        "step": _step(tmp_path),
        "finding": ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner",
                    "plan#1", "--text", "The report counts a refund twice."),
        "ruling": ruling_filing(tmp_path, "plan#1"),
        "question": ("file", "question", "--arc", "balance", "--title", "Day", "--body-file",
                     str(body)),
        TASK: ("file", TASK, "--parent", "plan#1", "--title", "Piece", "--body-file", str(body)),
    }


def _set_fields(tracker):
    """The field writes in ``tracker``'s log."""
    return [write for write in tracker.writes if write[0] == "set_field"]


@pytest.mark.parametrize("effort", ISSUE_FIELDS[EFFORT_FIELD].options)
def test_a_step_is_filed_holding_each_option_of_its_effort(code, tmp_path, effort):
    """Every option of the field is a filing's choice, and the card is born holding it, in
    the create itself: no write of the field follows."""
    tracker = FakeTracker()
    assert run(tracker, code, *_step(tmp_path, "--effort", effort)) == 0
    card = tracker.cards_by_number[1]
    assert (card.kind, card.effort, card.priority) == ("step", effort, None)
    assert not _set_fields(tracker)


def test_a_finding_is_filed_holding_its_effort(code):
    """A finding is sized when it is filed too, the work a branch builds (ruling plan#2506)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *finding_filing("plan#1")) == 0
    card = tracker.cards_by_number[2]
    assert (card.kind, card.effort, card.priority) == ("finding", "1", None)
    assert not _set_fields(tracker)


@pytest.mark.parametrize("kind", HOLDERS[EFFORT][1])
def test_a_kind_that_holds_an_effort_is_never_filed_without_one(code, tmp_path, capsys, kind):
    """argparse refuses the filing before anything is read: usage, exit 2."""
    tracker = FakeTracker()
    tracker.add(1)
    with pytest.raises(SystemExit) as stopped:
        quill.main(list(_filings(tmp_path)[kind]), connect=lambda: tracker, root=code)
    assert stopped.value.code == 2
    assert "the following arguments are required: --effort" in capsys.readouterr().err
    assert not tracker.writes and len(tracker.cards_by_number) == 1


@pytest.mark.parametrize("effort", ["3-4", "6", "0", "High"])
def test_an_effort_that_is_no_option_of_the_field_is_refused(code, tmp_path, capsys, effort):
    """plan#1821's 3-4 and GitHub's old High are options no more (ruling plan#2508)."""
    tracker = FakeTracker()
    with pytest.raises(SystemExit) as stopped:
        quill.main(list(_step(tmp_path, "--effort", effort)), connect=lambda: tracker,
                   root=code)
    assert stopped.value.code == 2
    assert f"argument --effort: invalid choice: '{effort}'" in capsys.readouterr().err
    assert not tracker.writes


@pytest.mark.parametrize("kind", [kind for kind in (*CARD_TYPES, TASK)
                                  if kind not in HOLDERS[EFFORT][1]])
def test_a_kind_that_holds_no_effort_takes_none(code, tmp_path, capsys, kind):
    """A ruling, a question or a task is no work a branch builds (ruling plan#2506): its
    command line has no ``--effort``."""
    tracker = FakeTracker()
    tracker.add(1)
    with pytest.raises(SystemExit) as stopped:
        quill.main([*_filings(tmp_path)[kind], "--effort", "1"], connect=lambda: tracker,
                   root=code)
    assert stopped.value.code == 2
    assert "unrecognized arguments: --effort 1" in capsys.readouterr().err
    assert not tracker.writes


@pytest.mark.parametrize(("held", "said"), [(None, "  its Effort: '3'\n"),
                                            ("1", "  its Effort: '3', not '1'\n")])
def test_a_filing_run_again_writes_the_effort_its_card_does_not_hold(code, tmp_path, capsys,
                                                                     held, said):
    """R-BAL186: a card an earlier run made holding no Effort (older quill sent none) or
    another is given the filing's, before the mark is removed, last (R-BAL202)."""
    tracker = FakeTracker()
    tracker.add(1, body="New work.", title="New", labels=("balance", FILING), effort=held,
                on_board=False)
    assert run(tracker, code, *_step(tmp_path, "--effort", "3")) == 0
    assert tracker.writes == [("set_field", 1, EFFORT_FIELD, "3"), ("board_add", 1),
                              ("unmark", 1)]
    assert tracker.cards_by_number[1].effort == "3"
    assert said in capsys.readouterr().out


def test_a_filing_run_again_over_a_card_holding_its_effort_writes_none(code, tmp_path):
    """The Effort the card holds is the filing's: nothing to write but the rest."""
    tracker = FakeTracker()
    tracker.add(1, body="New work.", title="New", labels=("balance", FILING), effort="3",
                on_board=False)
    assert run(tracker, code, *_step(tmp_path, "--effort", "3")) == 0
    assert tracker.writes == [("board_add", 1), ("unmark", 1)]


def test_a_create_whose_effort_github_dropped_is_finished_holding_it(code, tmp_path, capsys,
                                                                     monkeypatch):
    """The create lands without its Effort and its read-back fails the run, exit 2, the card
    still marked (``_tracker.Tracker.create``); the same command run again finishes it, its
    Effort written."""
    tracker = FakeTracker()
    create = tracker.create

    def dropped(card, milestone=None):
        number = create(card, milestone)
        tracker.cards_by_number[number] = dataclasses.replace(tracker.cards_by_number[number],
                                                              effort=None)
        raise TrackerError(f"plan#{number} was filed holding no Effort: run the same command "
                           "again, which writes it")

    monkeypatch.setattr(tracker, "create", dropped)
    assert run(tracker, code, *_step(tmp_path, "--effort", "4")) == 2
    assert FILING in tracker.cards_by_number[1].labels
    monkeypatch.setattr(tracker, "create", create)
    assert run(tracker, code, *_step(tmp_path, "--effort", "4")) == 0
    assert tracker.cards_by_number[1].effort == "4"
    assert FILING not in tracker.cards_by_number[1].labels
    assert len(tracker.cards_by_number) == 1, "no second card filed"
    assert "  its Effort: '4'\n" in capsys.readouterr().out


def test_a_filing_that_finished_is_never_resized(code, tmp_path, capsys):
    """R-BAL186, R-BAL202: an unmarked card's filing finished, and a person may have resized it
    since: nothing is written."""
    tracker = FakeTracker()
    tracker.add(1, body="New work.", title="New", labels=("balance",), effort="1")
    assert run(tracker, code, *_step(tmp_path, "--effort", "3")) == 0
    assert not tracker.writes and tracker.cards_by_number[1].effort == "1"
    assert "is filed already" in capsys.readouterr().out


def _converted(tmp_path):
    """``quill file ruling --from-question plan#2`` answering it "Yes." for plan#1."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    return ("file", "ruling", "--arc", "balance", "--title", "Tonight", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))


@pytest.mark.parametrize(("priority", "effort"), [("High", None), (None, "2"), ("Urgent", "5+")])
def test_a_question_becomes_a_ruling_holding_no_priority_and_no_effort(code, tmp_path, capsys,
                                                                       priority, effort):
    """A ruling is a record (rulings plan#2506, plan#2507): the question's Priority, and an
    Effort a person set on it by hand, are each cleared once it is a ruling, before it is
    closed, last."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight", priority=priority,
                effort=effort)
    assert run(tracker, code, *_converted(tmp_path)) == 0
    card = tracker.cards_by_number[2]
    assert (card.kind, card.priority, card.effort, card.is_open) == ("ruling", None, None, False)
    cleared = [("set_field", 2, field, None)
               for field, held in ((PRIORITY_FIELD, priority), (EFFORT_FIELD, effort)) if held]
    assert _set_fields(tracker) == cleared
    writes = tracker.writes
    assert writes.index(("retype", 2, "ruling")) < writes.index(cleared[0])
    assert writes.index(cleared[-1]) < writes.index(("close", 2, "completed"))
    out = capsys.readouterr().out
    assert (f"  cleared its Priority, {priority!r}: a ruling holds none (ruling plan#2507)\n"
            in out) is bool(priority)
    assert (f"  cleared its Effort, {effort!r}: a ruling holds none (ruling plan#2506)\n"
            in out) is bool(effort)


def test_a_question_holding_neither_field_has_none_cleared(code, tmp_path):
    """Nothing to clear is no write: a clear of a value the card does not hold is GitHub's 404
    (``_tracker.Tracker.set_field``)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight")
    assert run(tracker, code, *_converted(tmp_path)) == 0
    assert not _set_fields(tracker)


def test_a_conversion_cut_short_at_the_clear_is_finished_by_the_same_command(code, tmp_path):
    """R-BAL186: run again, the card is a ruling still open holding its Priority, which is
    cleared once."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight", priority="High")
    FailOnce(tracker, "set_field")
    assert run(tracker, code, *_converted(tmp_path)) == 2
    assert tracker.cards_by_number[2].kind == "ruling"
    assert run(tracker, code, *_converted(tmp_path)) == 0
    card = tracker.cards_by_number[2]
    assert (card.priority, card.is_open) == (None, False)
    assert _set_fields(tracker) == [("set_field", 2, PRIORITY_FIELD, None)]
