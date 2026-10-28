"""``quill file question --owner``: a question linked to the step it is asked for, by the
blocked-by link a ruling names its owner with (R-BAL395), which ``show`` reads both ways and the
ruling it becomes keeps (plan#1861's item 10), over :class:`_fake.FakeTracker` and a throwaway
git repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from tools.quill._fake import FailOnce, FakeTracker, run
from tools.quill.check import ruling_body
from tools.quill.setup_tracker import FILING


def _question(tmp_path, *owner):
    """``file question``'s arguments, its text in a file under ``tmp_path``, and ``owner``
    (``--owner plan#S``, or nothing)."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    return ("file", "question", "--arc", "balance", "--title", "Day", "--body-file", str(text),
            *owner)


def _ruling(tmp_path, *owner):
    """``file ruling --from-question plan#2``'s arguments, its answer in a file under
    ``tmp_path``, and ``owner`` (``--owner plan#S``, or nothing)."""
    answer = tmp_path / "a.md"
    answer.write_text("Tuesday.")
    return ("file", "ruling", "--arc", "balance", "--title", "Day", "--from-question", "plan#2",
            "--answer-file", str(answer), *owner)


def test_a_question_given_an_owner_is_blocked_by_it_and_stays_in_the_order(code, tmp_path):
    """Spec item 10: the step it is asked for blocks it, as an owner blocks a ruling, and it is
    no sub-issue of that step, whose progress would count it (R-BAL395); a question is in the
    board's order whatever its owner (R-BAL177)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *_question(tmp_path, "--owner", "plan#1")) == 0
    assert tracker.writes == [("create", 2, "question", "Day", ("balance", FILING), "balance"),
                              ("block", 2, 1), ("board_add", 2), ("unmark", 2)]
    question = tracker.cards_by_number[2]
    assert (question.parent, question.blocked_by) == (None, (1,))
    assert tracker.cards_by_number[1].children == ()
    assert tracker.board.items == [1, 2]


def test_a_question_with_no_owner_is_linked_to_no_card(code, tmp_path):
    """The owner is optional: a question that asks which step does a thing is asked of none
    yet (most open questions on 2026-10-27, "Which step ...")."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *_question(tmp_path)) == 0
    assert tracker.writes == [("create", 2, "question", "Day", ("balance", FILING), "balance"),
                              ("board_add", 2), ("unmark", 2)]
    assert tracker.cards_by_number[2].blocked_by == ()


def test_a_question_with_no_owner_run_again_keeps_a_link_a_person_gave_it(code, tmp_path,
                                                                          capsys):
    """A filing that names no owner says nothing of one (``_filing._refuse_rehoming``): run
    again after a person linked the question to a step on the web, it finds the question filed
    and writes nothing, the link standing."""
    tracker = FakeTracker()
    tracker.add(1)
    args = _question(tmp_path)
    assert run(tracker, code, *args) == 0
    tracker.block(2, tracker.cards_by_number[1])
    writes = list(tracker.writes)
    assert run(tracker, code, *args) == 0
    assert "is filed already" in capsys.readouterr().out
    assert tracker.writes == writes
    assert tracker.cards_by_number[2].blocked_by == (1,)


def test_show_names_a_questions_owner_and_the_step_lists_it(code, tmp_path, capsys):
    """Spec item 10 and ruling plan#1868 ("the card links its findings, questions, rulings and
    pull request"): the question names its owner, and the step's ``show`` lists the question
    it owns."""
    tracker = FakeTracker()
    tracker.add(1, title="Build it")
    assert run(tracker, code, *_question(tmp_path, "--owner", "plan#1")) == 0
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#2") == 0
    assert "  owner (blocked by): plan#1 Build it" in capsys.readouterr().out.splitlines()
    assert run(tracker, code, "show", "plan#1") == 0
    assert "  owns: plan#2 (question, open) Day" in capsys.readouterr().out.splitlines()


def test_a_question_whose_owner_is_no_step_is_refused_unwritten(code, tmp_path, capsys):
    """The check (``check._owner_problems``): a question is asked for a step, never for a
    finding, a ruling or another question."""
    tracker = FakeTracker()
    tracker.add(1, "finding")
    assert run(tracker, code, *_question(tmp_path, "--owner", "plan#1")) == 1
    assert ("a question that names an owner is blocked by the step it is asked for (R-BAL395); "
            "its owner is finding") in capsys.readouterr().err
    assert not tracker.writes


def test_a_question_filing_whose_owner_link_failed_is_finished_once(code, tmp_path):
    """R-BAL186: the same command run again finishes the filing a failed link cut short, the
    link written once, and files no second card."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "block")
    args = _question(tmp_path, "--owner", "plan#1")
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, *args) == 0
    assert [number for number, card in tracker.cards_by_number.items()
            if card.kind == "question"] == [2]
    assert tracker.writes.count(("block", 2, 1)) == 1
    assert tracker.writes[-1] == ("unmark", 2)
    assert tracker.cards_by_number[2].blocked_by == (1,)


def test_a_question_filed_under_a_step_is_refused_as_a_question_with_an_owner(code, tmp_path,
                                                                            capsys):
    """Re-homing is a person's call (``_filing._refuse_reowning``): a card the filing finds that
    is a sub-issue of a step is refused, the message naming a question with an owner."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add(2, "question", body="Which day?", title="Day", parent=3,
                labels=("balance", FILING))
    assert run(tracker, code, *_question(tmp_path, "--owner", "plan#1")) == 1
    assert ("is a sub-issue of plan#3, but a question that names an owner is blocked by its "
            "owner, plan#1, and is no card's sub-issue (R-BAL395)") in capsys.readouterr().err
    assert not tracker.writes


def test_a_ruling_from_a_question_keeps_its_owner_with_no_owner_named(code, tmp_path, capsys):
    """Spec item 10: ``--from-question`` carries the question's owner to the ruling, the same
    card, so its link is kept and written no second time."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Which day?", title="Day", blocked_by=(1,))
    assert run(tracker, code, *_ruling(tmp_path)) == 0
    assert "  already blocked by plan#1, its owner" in capsys.readouterr().out.splitlines()
    ruling = tracker.cards_by_number[2]
    assert (ruling.kind, ruling.blocked_by, ruling.parent, ruling.is_open) == ("ruling", (1,),
                                                                               None, False)
    assert not [write for write in tracker.writes if write[0] == "block"]
    assert tracker.bodies[2] == ruling_body("Which day?", "Tuesday.")


def test_a_ruling_from_a_question_with_no_owner_needs_one_named(code, tmp_path, capsys):
    """A ruling is blocked by a step (R-BAL395): from a question asked of no step, the
    conversion is refused, naming ``--owner``, with nothing written; named, it is filed."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Which day?", title="Day")
    assert run(tracker, code, *_ruling(tmp_path)) == 1
    assert ("names no one owner step for its ruling to keep (R-BAL395): name the step the "
            "ruling is for with --owner plan#S") in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, *_ruling(tmp_path, "--owner", "plan#1")) == 0
    assert tracker.cards_by_number[2].blocked_by == (1,)


def test_a_ruling_from_a_question_owned_elsewhere_is_refused(code, tmp_path, capsys):
    """An ``--owner`` that is not the question's is a re-homing, a person's call: refused, the
    question kept as it is."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add(2, "question", body="Which day?", title="Day", blocked_by=(1,))
    assert run(tracker, code, *_ruling(tmp_path, "--owner", "plan#3")) == 1
    assert ("is blocked by plan#1, not only by plan#3, the owner this filing names (R-BAL395)"
            in capsys.readouterr().err)
    assert not tracker.writes


def test_a_ruling_from_a_question_file_still_needs_its_owner(code, tmp_path, capsys):
    """``--owner`` is no longer required by the command line, as a ruling from a question may
    take its question's; a ruling from ``--question-file`` names no question, so the check
    refuses it with no owner, nothing written."""
    asked, answered = tmp_path / "asked.md", tmp_path / "answered.md"
    asked.write_text("Where should the rule live?")
    answered.write_text("In the service.")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Home",
               "--question-file", str(asked), "--answer-file", str(answered)) == 1
    assert ("a ruling is blocked by a step, its owner (R-BAL395); its owner is none"
            in capsys.readouterr().err)
    assert not tracker.writes
