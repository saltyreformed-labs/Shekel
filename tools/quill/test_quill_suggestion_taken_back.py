"""quill's own pending suggestion goes when quill writes the card's Priority or a question becomes
its ruling (plan#2798): GitHub's API leaves the suggestion pending beside a Priority written
(measured 2026-11-10 on plan#3178), where a later Accept would overwrite the developer's choice,
and a ruling holds no Priority (ruling plan#2507).  ``quill priority``'s set or clear takes it
back first (``_arc_label.cmd_field``), and so does the conversion (``_filing._convert_question``),
each through ``_suggestion.take_back``; ``quill effort`` never touches it.  Over the fake
tracker's suggestions (``_fake.FakeSuggestions``); the take-back itself is graded against a
recording in ``test__suggestion.py``.
"""
from __future__ import annotations

import pytest

from tools.quill._draft import Suggestion
from tools.quill._fake import QUILL_ACTOR, FailOnce, FakeTracker, run
from tools.quill._suggestion import Pending

#: quill's suggestion, pending on a card a test holds.
_PENDING = Pending(Suggestion("Low", "A test card's reason: nothing waits on it."), QUILL_ACTOR)
#: How ``quill priority`` says it took back :data:`_PENDING` on step plan#1.
_TOOK_BACK = "took back quill's suggested Priority 'Low' on plan#1 [step, balance] card 1: its "


def _suggested(tracker, number, **fields):
    """Put in ``tracker`` the step ``number``, holding ``fields``, with quill's suggestion
    :data:`_PENDING` pending on it."""
    tracker.add(number, **fields)
    tracker.suggestions.held[number] = _PENDING


@pytest.mark.parametrize(("argv", "value"), [(("--to", "High"), "High"), (("--clear",), None)])
def test_a_priority_set_or_cleared_takes_back_quills_suggestion_first(code, capsys, argv,
                                                                     value):
    """The take-back lands before the write, so no failure leaves the two together."""
    tracker = FakeTracker()
    _suggested(tracker, 1, priority=None if value else "Medium")
    assert run(tracker, code, "priority", "plan#1", *argv) == 0
    assert tracker.writes == [("withdraw", 1), ("set_field", 1, "Priority", value)]
    assert not tracker.suggestions.held
    assert capsys.readouterr().out.startswith(f"{_TOOK_BACK}Priority is decided\n")


def test_a_priority_held_already_still_takes_back_the_suggestion_beside_it(code, capsys):
    """A Priority set through the API (the developer's, or older quill's) left quill's
    suggestion pending beside it: the same value written again writes nothing but the
    take-back."""
    tracker = FakeTracker()
    _suggested(tracker, 1, priority="High")
    assert run(tracker, code, "priority", "plan#1", "--to", "High") == 0
    assert tracker.writes == [("withdraw", 1)]
    assert "holds the Priority 'High' already: not written" in capsys.readouterr().out


def test_an_effort_never_touches_the_suggestion(code):
    """The suggestion is of a Priority alone."""
    tracker = FakeTracker()
    _suggested(tracker, 1)
    assert run(tracker, code, "effort", "plan#1", "--to", "3") == 0
    assert tracker.writes == [("set_field", 1, "Effort", "3")]
    assert tracker.suggestions.held == {1: _PENDING}


def test_a_take_back_that_failed_writes_no_priority_and_the_same_command_finishes(code,
                                                                                capsys):
    """Exit 2, the Priority unwritten and the suggestion still pending; run again, it makes
    both writes."""
    tracker = FakeTracker()
    _suggested(tracker, 1)
    FailOnce(tracker.suggestions, "withdraw")
    assert run(tracker, code, "priority", "plan#1", "--to", "Urgent") == 2
    assert "bad gateway" in capsys.readouterr().err
    assert not tracker.writes and tracker.suggestions.held == {1: _PENDING}
    assert run(tracker, code, "priority", "plan#1", "--to", "Urgent") == 0
    assert tracker.writes == [("withdraw", 1), ("set_field", 1, "Priority", "Urgent")]


def _conversion(tmp_path):
    """``file ruling --from-question plan#2`` for owner plan#1, its answer in a file."""
    answer = tmp_path / "a"
    answer.write_text("Tuesday.")
    return ("file", "ruling", "--arc", "balance", "--title", "Day", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))


def test_a_question_becoming_its_ruling_takes_back_quills_suggestion(code, tmp_path, capsys):
    """A ruling holds no Priority, so a suggestion of one would wait on it forever, and an
    Accept would write one; it is taken back before the ruling closes."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Which day?", title="Day")
    tracker.suggestions.held[2] = _PENDING
    assert run(tracker, code, *_conversion(tmp_path)) == 0
    assert tracker.writes.index(("withdraw", 2)) < tracker.writes.index(("close", 2, "completed"))
    assert not tracker.suggestions.held
    assert ("  took back quill's suggested Priority 'Low': a ruling holds no Priority (ruling "
            "plan#2507)\n") in capsys.readouterr().out


def test_a_conversion_cut_short_after_its_take_back_takes_back_nothing_again(code, tmp_path):
    """R-BAL186: the same command finishes it, reading no suggestion pending."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Which day?", title="Day")
    tracker.suggestions.held[2] = _PENDING
    FailOnce(tracker, "close")
    assert run(tracker, code, *_conversion(tmp_path)) == 2
    assert ("withdraw", 2) in tracker.writes
    tracker.writes.clear()
    assert run(tracker, code, *_conversion(tmp_path)) == 0
    assert ("withdraw", 2) not in tracker.writes and ("close", 2, "completed") in tracker.writes
