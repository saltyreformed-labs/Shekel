"""Every reader of a card's arc reads its issue field Arc, never its arc label (ruling
``balance:R-BAL354``: "quill and the release tools read only the field"; R-BAL349): how a card
is named, ``next --arc``, ``show OLD-ID``, a ruling's conversion from a question, a filing
run again and ``edit``.  Each case gives a card whose arc label says one arc and whose Arc
another, or none: the field wins, and the label, display that ``sync`` puts back, decides
nothing.
"""
from __future__ import annotations

from tools.quill._command import _label
from tools.quill._fake import FakeTracker, run
from tools.quill._state import next_step
from tools.quill.setup_tracker import FILING


def test_a_card_is_named_by_its_arc_field_never_its_label():
    """``plan#7 [step, salary] title``: the Arc, whatever the label says; no Arc, no arc."""
    tracker = FakeTracker()
    assert _label(tracker.add(1, labels=("balance",), arc="salary")) == (
        "plan#1 [step, salary] card 1")
    assert _label(tracker.add(2, labels=("balance",), arc=None)) == "plan#2 [step, no arc] card 2"


def test_next_offers_a_step_in_the_arc_its_field_holds():
    """``next --arc``: a step labelled one arc and holding another is the other's, and a step
    holding no Arc is no arc's."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance",), arc="salary")
    tracker.add(2, labels=("balance",), arc=None)
    tracker.add(3, labels=("salary",), arc="balance")
    cards = tracker.cards([1, 2, 3])
    assert next_step([1, 2, 3], cards, (), {}, "salary").card.number == 1
    assert next_step([1, 2, 3], cards, (), {}, "balance").card.number == 3
    assert next_step([1, 2], cards, (), {}, "balance").card is None


def test_show_finds_an_old_id_in_the_arc_its_field_holds(code, capsys):
    """``ARC:OLD-ID`` names the card holding that Arc, not the one labelled it."""
    tracker = FakeTracker()
    tracker.add(1, "ruling", title="[R-GU] one", labels=("balance",), arc="bank_import",
                is_open=False)
    tracker.add(2, "ruling", title="[R-GU] two", labels=("bank_import",), arc="balance",
                is_open=False)
    assert run(tracker, code, "show", "bank_import:R-GU") == 0
    assert capsys.readouterr().out.startswith("plan#1 [ruling, bank_import] [R-GU] one")


def test_a_question_becomes_a_ruling_in_the_arc_its_field_holds(code, tmp_path, capsys):
    """``--from-question`` refuses an ``--arc`` the question's Arc does not hold, whatever its
    label says, and converts it in the one it holds."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it?", title="Ship", labels=("balance",),
                arc="salary")
    convert = ("file", "ruling", "--title", "Ship", "--owner", "plan#1", "--from-question",
               "plan#2", "--answer-file", str(answer))
    assert run(tracker, code, *convert, "--arc", "balance") == 1
    assert "plan#2 [question, salary] Ship is not in the balance arc" in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, *convert, "--arc", "salary") == 0
    assert {("retype", 2, "ruling"), ("close", 2, "completed")} <= set(tracker.writes)


def _spec(tmp_path):
    """A step's spec, written to a file."""
    spec = tmp_path / "spec.md"
    spec.write_text("New work.")
    return str(spec)


def test_a_filing_run_again_over_a_card_whose_arc_was_changed_by_hand_is_refused(
        code, tmp_path, capsys):
    """R-BAL186 finishes only the filing it repeats: a card an earlier run filed whose Arc a
    person changed since is theirs to fix, and nothing is written."""
    tracker = FakeTracker()
    tracker.add(4, body="New work.", title="New", labels=("salary", FILING), arc="balance")
    assert run(tracker, code, "file", "step", "--arc", "salary", "--title", "New",
               "--body-file", _spec(tmp_path)) == 1
    assert capsys.readouterr().err == (
        "refused: plan#4 [step, balance] New has this kind, title and text but holds the Arc "
        "'balance', not 'salary': set its Arc by hand\n")
    assert not tracker.writes


def test_a_filing_run_again_over_a_card_whose_arc_label_was_changed_is_finished(code, tmp_path):
    """Its arc label is display, which ``sync`` puts back (R-BAL354): a person's change of it
    stops no filing, which the same command finishes, its Arc as it was filed."""
    tracker = FakeTracker()
    tracker.add(4, body="New work.", title="New", labels=("balance", FILING), arc="salary",
                on_board=False)
    assert run(tracker, code, "file", "step", "--arc", "salary", "--title", "New",
               "--body-file", _spec(tmp_path)) == 0
    assert tracker.writes == [("board_add", 4), ("unmark", 4)]


def test_edit_reads_the_arc_from_the_field_never_the_label(code):
    """A card whose arc labels, none or two, disagree with its Arc is edited: the check grades
    the Arc it holds."""
    tracker = FakeTracker()
    tracker.add(1, labels=("salary", "recurrence"), arc="balance")
    tracker.add(2, labels=(), arc="balance")
    for card in ("plan#1", "plan#2"):
        assert run(tracker, code, "edit", card, "--title", "Renamed") == 0
    assert tracker.writes == [("retitle", 1, "Renamed"), ("retitle", 2, "Renamed")]
