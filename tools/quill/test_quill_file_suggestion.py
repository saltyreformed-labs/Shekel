"""A step at the top level and a question are filed with the Priority the filing session
suggests and its reason (rulings plan#2800, plan#3224, plan#3236): ``quill file`` requires
``--suggest`` and ``--why`` of them, refuses one on any other card (rulings plan#3226,
plan#3229), and writes it as GitHub's own pending suggestion on the card's Priority before the
mark comes off, the Priority itself left blank; a run finishing a filing cut short writes it
only where quill's suggestion is not already pending as stated (R-BAL186).  The fake tracker
holds the pending suggestions (``_fake.FakeSuggestions``); the writes themselves are graded
against a recording in ``test__suggestion.py``.
"""
from __future__ import annotations

import pytest

from tools.quill import quill
from tools.quill._draft import Suggestion
from tools.quill._fake import (
    QUILL_ACTOR,
    SUGGESTED,
    AnswerLostOnce,
    FailOnce,
    FakeTracker,
    finding_filing,
    leaf_filing,
    ruling_filing,
    run,
    suggested,
)
from tools.quill._fields import SUGGESTED_OPTIONS
from tools.quill._suggestion import Pending
from tools.quill.setup_tracker import FILING, TASK

#: The reason :data:`_fake.SUGGESTED` names.
_WHY = SUGGESTED[3]
#: The filing's refusal of a step at the top level or a question naming no suggestion.
_REQUIRED = ("a step at the top level or a question is filed with the Priority its filing "
             "suggests, --suggest PRIORITY, and its reason, --why SENTENCE")


def _step(tmp_path, *more):
    """``quill file step`` filing the top-level step "New", its spec "New work." written to a
    file and sized at two sessions, ``more`` after it."""
    spec = tmp_path / "new.md"
    spec.write_text("New work.")
    return ("file", "step", "--arc", "balance", "--title", "New", "--body-file", str(spec),
            "--effort", "2", *more)


def _question(tmp_path, *more):
    """``quill file question`` filing the question "Day", its text in a file, ``more`` after
    it."""
    text = tmp_path / "day.md"
    text.write_text("Which day?")
    return ("file", "question", "--arc", "balance", "--title", "Day", "--body-file", str(text),
            *more)


def _earlier(tracker, **fields):
    """The step :func:`_step` files, as an earlier run of its filing left it: created, marked
    and off the board; ``fields`` as that run left them."""
    return tracker.add(1, body="New work.", title="New", labels=("balance", FILING),
                       effort="2", on_board=False, **fields)


@pytest.mark.parametrize("option", SUGGESTED_OPTIONS)
def test_a_top_level_step_is_filed_with_its_suggestion_and_no_priority(code, tmp_path, capsys,
                                                                       option):
    """Each of the four is a filing's choice; the suggestion is written after the create and
    before the board place and the mark's removal, last (R-BAL202), and the card's Priority
    stays blank until the developer sets one (ruling plan#3224)."""
    tracker = FakeTracker()
    assert run(tracker, code, *_step(tmp_path, "--suggest", option, "--why", _WHY)) == 0
    assert tracker.writes == [("create", 1, "step", "New", ("balance", FILING), "balance"),
                              ("suggest", 1, option, _WHY), ("board_add", 1), ("unmark", 1)]
    assert tracker.cards_by_number[1].priority is None
    assert tracker.suggestions.held == {1: Pending(Suggestion(option, _WHY), QUILL_ACTOR)}
    assert f"  suggested Priority {option!r}, its reason: {_WHY}\n" in capsys.readouterr().out


@pytest.mark.parametrize("owner", [(), ("--owner", "plan#1")])
def test_a_question_with_an_owner_or_none_is_filed_with_its_suggestion(code, tmp_path, owner):
    """A question waits on the developer's answer, whatever step it is asked for."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *_question(tmp_path, *SUGGESTED, *owner)) == 0
    assert suggested(2) in tracker.writes
    assert tracker.writes.index(suggested(2)) == 1
    assert tracker.cards_by_number[2].priority is None


def test_the_reason_is_written_without_the_space_around_it(code, tmp_path):
    """The reason as the check grades it is the reason written and read back."""
    tracker = FakeTracker()
    assert run(tracker, code, *_step(tmp_path, "--suggest", "High", "--why", f"  {_WHY}\n")) == 0
    assert tracker.suggestions.held[1].suggestion == Suggestion("High", _WHY)


@pytest.mark.parametrize("filing", [_step, _question])
def test_a_top_level_step_or_a_question_naming_no_suggestion_is_refused_unwritten(
        code, tmp_path, capsys, filing):
    """Filing asks for a suggestion now (the card's spec, changing ruling plan#2507's "asks
    for none"): refused, exit 1, before anything is written."""
    tracker = FakeTracker()
    assert run(tracker, code, *filing(tmp_path)) == 1
    assert _REQUIRED in capsys.readouterr().err
    assert not tracker.writes and not tracker.cards_by_number


@pytest.mark.parametrize("half", [("--suggest", "Low"), ("--why", _WHY)])
def test_a_suggestion_without_its_reason_or_a_reason_without_one_is_refused(code, tmp_path,
                                                                            capsys, half):
    """The two name one suggestion together."""
    tracker = FakeTracker()
    assert run(tracker, code, *_step(tmp_path, *half)) == 1
    assert ("--suggest PRIORITY and --why SENTENCE name a suggested Priority and its reason "
            "together: give both") in capsys.readouterr().err
    assert not tracker.writes and not tracker.cards_by_number


def test_a_leaf_naming_a_suggestion_is_refused_unwritten(code, tmp_path, capsys):
    """Its split step's Priority ranks it (ruling plan#3229)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "half"), *SUGGESTED) == 1
    assert ("a suggested Priority is held by a step at the top level or a question alone "
            "(rulings plan#3226, plan#3229); this leaf would hold 'Low'") in (
                capsys.readouterr().err)
    assert not tracker.writes


@pytest.mark.parametrize("kind", ["finding", "ruling", TASK])
def test_a_finding_a_ruling_or_a_task_takes_no_suggestion(code, tmp_path, capsys, kind):
    """Their command lines have no ``--suggest``: argparse refuses it before anything is
    read, exit 2."""
    body = tmp_path / "piece.md"
    body.write_text("Build it.")
    argv = {"finding": finding_filing("plan#1"), "ruling": ruling_filing(tmp_path, "plan#1"),
            TASK: ("file", TASK, "--parent", "plan#1", "--title", "Piece", "--body-file",
                   str(body))}[kind]
    tracker = FakeTracker()
    tracker.add(1)
    with pytest.raises(SystemExit) as stopped:
        quill.main([*argv, *SUGGESTED], connect=lambda: tracker, root=code)
    assert stopped.value.code == 2
    assert "unrecognized arguments: --suggest Low --why" in capsys.readouterr().err
    assert not tracker.writes


@pytest.mark.parametrize("option", ["On hold", "high"])
def test_a_suggestion_that_is_none_of_the_four_is_refused(code, tmp_path, capsys, option):
    """On hold pauses a step, which no filing suggests; argparse names the four (how it
    quotes them is its version's, so only its refusal of the value is read)."""
    tracker = FakeTracker()
    with pytest.raises(SystemExit) as stopped:
        quill.main(list(_step(tmp_path, "--suggest", option, "--why", _WHY)),
                   connect=lambda: tracker, root=code)
    assert stopped.value.code == 2
    assert f"argument --suggest: invalid choice: '{option}'" in capsys.readouterr().err
    assert not tracker.writes


def test_a_reason_the_check_refuses_files_nothing(code, tmp_path, capsys):
    """Two sentences are no one sentence: refused before the create."""
    tracker = FakeTracker()
    why = "The grid is wrong. It stays so."
    assert run(tracker, code, *_step(tmp_path, "--suggest", "Low", "--why", why)) == 1
    assert "a suggestion's reason is ONE sentence" in capsys.readouterr().err
    assert not tracker.writes and not tracker.cards_by_number


# -- a filing cut short (R-BAL186) ----------------------------------------------------------

def test_a_filing_run_again_writes_the_suggestion_its_card_does_not_hold(code, tmp_path):
    """An earlier run cut short before its suggestion, or older quill, which wrote none."""
    tracker = FakeTracker()
    _earlier(tracker)
    assert run(tracker, code, *_step(tmp_path, *SUGGESTED)) == 0
    assert tracker.writes == [suggested(1), ("board_add", 1), ("unmark", 1)]


def test_a_filing_run_again_over_its_suggestion_pending_writes_none(code, tmp_path, capsys):
    """quill's suggestion pending as stated is the earlier run's write, which landed."""
    tracker = FakeTracker()
    _earlier(tracker)
    tracker.suggestions.held[1] = Pending(Suggestion("Low", _WHY), QUILL_ACTOR)
    assert run(tracker, code, *_step(tmp_path, *SUGGESTED)) == 0
    assert tracker.writes == [("board_add", 1), ("unmark", 1)]
    assert "  its suggested Priority 'Low' is pending already\n" in capsys.readouterr().out


@pytest.mark.parametrize("held", [Suggestion("High", _WHY), Suggestion("Low", "Another.")])
def test_a_filing_run_again_over_another_suggestion_takes_it_back_and_states_its_own(
        code, tmp_path, capsys, held):
    """A card still being filed holds the filing session's estimate, which this run states
    again, as it states its Effort again; the one pending is taken back first."""
    tracker = FakeTracker()
    _earlier(tracker)
    tracker.suggestions.held[1] = Pending(held, QUILL_ACTOR)
    assert run(tracker, code, *_step(tmp_path, *SUGGESTED)) == 0
    assert tracker.writes == [("withdraw", 1), suggested(1), ("board_add", 1), ("unmark", 1)]
    assert tracker.suggestions.held[1].suggestion == Suggestion("Low", _WHY)
    assert (f"  took back its suggested Priority {held.priority!r}, another run's\n"
            in capsys.readouterr().out)


def test_a_filing_run_again_over_a_card_holding_a_priority_suggests_none(code, tmp_path,
                                                                        capsys):
    """A Priority a person set on the card while it was still being filed is decided: the
    Inbox lists only a card holding none (ruling plan#3231)."""
    tracker = FakeTracker()
    _earlier(tracker, priority="Medium")
    assert run(tracker, code, *_step(tmp_path, *SUGGESTED)) == 0
    assert tracker.writes == [("board_add", 1), ("unmark", 1)]
    assert not tracker.suggestions.held
    assert ("  no suggested Priority: it holds the Priority 'Medium' already\n"
            in capsys.readouterr().out)


def test_a_suggestion_that_failed_leaves_the_card_marked_and_is_finished_once(code, tmp_path,
                                                                              capsys):
    """The write fails, exit 2, the card still marked and off the board, so never offered;
    the same command finishes it."""
    tracker = FakeTracker()
    FailOnce(tracker.suggestions, "put")
    args = _step(tmp_path, *SUGGESTED)
    assert run(tracker, code, *args) == 2
    assert "bad gateway" in capsys.readouterr().err
    assert FILING in tracker.cards_by_number[1].labels and not tracker.board.items
    tracker.writes.clear()
    assert run(tracker, code, *args) == 0
    assert tracker.writes == [suggested(1), ("board_add", 1), ("unmark", 1)]
    tracker.writes.clear()
    assert run(tracker, code, *args) == 0
    assert not tracker.writes and "filed already" in capsys.readouterr().out


def test_a_suggestion_whose_answer_was_lost_is_never_written_twice(code, tmp_path):
    """The write landed and its answer was lost: the run again reads it pending as stated."""
    tracker = FakeTracker()
    AnswerLostOnce(tracker.suggestions, "put")
    args = _step(tmp_path, *SUGGESTED)
    assert run(tracker, code, *args) == 2
    tracker.writes.clear()
    assert run(tracker, code, *args) == 0
    assert tracker.writes == [("board_add", 1), ("unmark", 1)]
    assert tracker.suggestions.held[1].suggestion == Suggestion("Low", _WHY)
