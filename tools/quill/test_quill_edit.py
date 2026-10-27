"""``quill edit`` and ``quill comment`` (step X-cx's leaf A, card plan#27): a card's title or
text rewritten through the tracker's own writes after the check ``file`` runs on what it
writes, a ruling never rewritten (R-BAL220), and a comment added -- over
:class:`_fake.FakeTracker` and a throwaway git repository (the ``code`` fixture,
``conftest.py``).  ``Tracker.retitle``, ``Tracker.set_body`` and ``Tracker.comment`` are
graded against GitHub's recorded answers in ``test__tracker.py``.  Nothing here calls
GitHub.
"""
from __future__ import annotations

import pytest

from tools.quill._fake import FailOnce, FakeTracker, run
from tools.quill._github import GitHubError
from tools.quill.check import TITLE_CAP, ruling_body
from tools.quill.setup_tracker import FILING

#: A finding's one sentence, of the shape R-BAL136 gives as its example.
SENTENCE = "The report counts a purchase twice when its refund lands in a later pay period."


def _text(tmp_path, text, name="text.md"):
    """A ``--body-file`` holding ``text``; its path, as the command line passes it."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# -- edit ------------------------------------------------------------------------------------

def test_edit_rewrites_a_title_and_a_text_through_the_trackers_writes(code, tmp_path,
                                                                       capsys):
    """One retitle and one body write, each printed as it lands; the text's change is a saved
    version ``spec-history`` reads (R-BAL174)."""
    tracker = FakeTracker()
    tracker.add(1, body="Build the walk.")
    assert run(tracker, code, "edit", "plan#1", "--title", "The walk",
               "--body-file", _text(tmp_path, "Build the walk, leg by leg.\n")) == 0
    assert tracker.writes == [("retitle", 1, "The walk"),
                              ("set_body", 1, "Build the walk, leg by leg.\n")]
    assert capsys.readouterr().out == (
        "retitled plan#1: 'card 1' -> 'The walk'\n"
        "plan#1's text replaced (`quill spec-history plan#1` shows the change)\n")
    assert [version.body for version in tracker.edits(1)[1]] == [
        "Build the walk.", "Build the walk, leg by leg.\n"]


#: A ruling as its filing leaves it (closed as completed by the tool), and one a person
#: reopened: R-BAL220 refuses any ruling card, whatever its state.
RULING_STATES = [
    {"is_open": False, "state_reason": "COMPLETED", "closed_by_tool": True},
    {"is_open": True, "state_reason": "REOPENED", "touched_by_hand": True},
]


@pytest.mark.parametrize("state", RULING_STATES, ids=["closed", "reopened"])
@pytest.mark.parametrize("argv", [("--title", "Fixed"), ("--body-file", "TEXT")])
def test_edit_refuses_a_ruling_whatever_it_would_change(code, tmp_path, capsys, argv, state):
    """R-BAL220: a ruling records the developer's question and answer word for word, so
    ``edit`` refuses it, title or text, open or closed, with nothing written; a correction
    is a new ruling."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=1,
                **state)
    argv = tuple(_text(tmp_path, "Question: Where?\n\nAnswer: There.") if arg == "TEXT" else arg
                 for arg in argv)
    assert run(tracker, code, "edit", "plan#2", *argv) == 1
    assert capsys.readouterr().err == (
        "refused: plan#2 [ruling, balance] Home is a ruling, the developer's question and "
        "answer word for word: edit never rewrites one, its title or its text; a correction is "
        "a new ruling that names the one it amends (R-BAL220)\n")
    assert not tracker.writes


def test_edit_refuses_a_ruling_or_a_marked_card_before_reading_its_text(code, tmp_path,
                                                                        capsys):
    """The card's own refusals come before ``--body-file`` is read, so a ruling, or a card
    still marked, is refused (exit 1) even when the file cannot be read, which would
    otherwise be a failed call (exit 2)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "ruling", parent=1, **RULING_STATES[0])
    tracker.add(3, labels=("balance", FILING))
    missing = str(tmp_path / "missing.md")
    assert run(tracker, code, "edit", "plan#2", "--body-file", missing) == 1
    assert "is a ruling" in capsys.readouterr().err
    assert run(tracker, code, "edit", "plan#3", "--body-file", missing) == 1
    assert "carries the 'filing' mark" in capsys.readouterr().err
    assert not tracker.writes


@pytest.mark.parametrize(("title", "said"), [
    ("", "its title is a short name, and it has none"),
    ("  ", "its title is a short name, and it has none"),
    ("t" * (TITLE_CAP + 1), f"its title is a short name of at most {TITLE_CAP} characters; "
                            f"it is {TITLE_CAP + 1}"),
])
def test_edit_holds_a_title_to_the_check_file_runs(code, capsys, title, said):
    """R-BAL178 and R-BAL180: a title written is a short name of at most 100 characters,
    graded by the one check (:func:`check.violations`) before anything is sent."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "edit", "plan#1", "--title", title) == 1
    assert capsys.readouterr().err == f"refused: not edited:\n  {said}\n"
    assert not tracker.writes


@pytest.mark.parametrize(("fields", "said"), [
    ({"kind": None}, "its type is None, not one of step, finding, ruling, question"),
    ({"arc": None}, "it holds one arc in its Arc field, one of"),
    ({"arc": None, "labels": ()}, "it holds one arc in its Arc field, one of"),
])
def test_edit_holds_the_cards_type_and_arc_to_the_check(code, capsys, fields, said):
    """Every card the check passes has one of the four kinds and holds one arc in its Arc
    field (R-BAL354), so a card ``edit`` would write must too (:func:`check.violations`
    grades both whatever the change): a card with no type, or no Arc, its arc label kept or
    not, is refused, nothing written."""
    tracker = FakeTracker()
    tracker.add(1, **fields)
    assert run(tracker, code, "edit", "plan#1", "--title", "Renamed") == 1
    err = capsys.readouterr().err
    assert err.startswith("refused: not edited:\n  ") and said in err
    assert not tracker.writes


def test_edit_never_blanks_a_cards_text(code, tmp_path, capsys):
    """A ``--body-file`` that holds no text, or only space, would wipe a step's spec or a
    question: refused for every kind, nothing written (``comment`` refuses one too)."""
    tracker = FakeTracker()
    tracker.add(1, body="Build the walk.")
    tracker.add(2, "question", body="Ship it tonight?")
    for number, text in ((1, ""), (2, "  \n\t\n")):
        path = _text(tmp_path, text, f"empty{number}.md")
        assert run(tracker, code, "edit", f"plan#{number}", "--body-file", path) == 1
        assert capsys.readouterr().err == (
            f"refused: {path} holds no text: edit never blanks a card's text\n")
    assert not tracker.writes and tracker.bodies == {1: "Build the walk.", 2: "Ship it tonight?"}


def test_edit_rewrites_a_closed_card_whose_filing_finished(code, tmp_path):
    """A closed card with no filing mark -- here a step ``sync`` closed as shipped -- is
    edited: no command looks for it by its text, and ``spec-history`` shows the change
    (R-BAL174)."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    assert run(tracker, code, "edit", "plan#1", "--title", "Shipped walk",
               "--body-file", _text(tmp_path, "Built leg by leg.")) == 0
    assert tracker.writes == [("retitle", 1, "Shipped walk"),
                              ("set_body", 1, "Built leg by leg.")]


def test_edit_holds_a_findings_text_to_one_sentence_and_grades_only_what_it_writes(
        code, tmp_path, capsys):
    """R-BAL136: a finding's text written is one sentence of at most 400 characters; and only
    what a change writes is graded, so a finding whose text nobody edits keeps it, however
    long, while its title is rewritten."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "finding", body="One. Two. " * 50, parent=1)
    assert run(tracker, code, "edit", "plan#2",
               "--body-file", _text(tmp_path, "It fails. It also double counts.")) == 1
    assert ("a finding's text is ONE sentence; a second begins at 'It also double counts.'"
            in capsys.readouterr().err)
    assert not tracker.writes
    assert run(tracker, code, "edit", "plan#2", "--title", "Refund counted twice") == 0
    assert tracker.writes == [("retitle", 2, "Refund counted twice")]
    assert run(tracker, code, "edit", "plan#2", "--body-file", _text(tmp_path, SENTENCE)) == 0
    assert tracker.writes[-1] == ("set_body", 2, SENTENCE)


def test_edit_writes_only_what_differs_so_a_run_cut_short_is_finished_by_the_same_command(
        code, tmp_path, capsys):
    """The retitle lands and the body write fails (exit 2); the same command run again writes
    only the text, and run once more writes nothing.  A text that differs only as the rules
    do not read it (CRLF, the space around it) is the same text."""
    tracker = FakeTracker()
    tracker.add(1, body="Build the walk.")
    FailOnce(tracker, "set_body")
    args = ("edit", "plan#1", "--title", "The walk",
            "--body-file", _text(tmp_path, "Build the walk, leg by leg."))
    assert run(tracker, code, *args) == 2
    assert tracker.writes == [("retitle", 1, "The walk")]
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    assert tracker.writes[1:] == [("set_body", 1, "Build the walk, leg by leg.")]
    assert capsys.readouterr().out.startswith(
        "plan#1's title is already 'The walk': not written\n")
    writes = list(tracker.writes)
    assert run(tracker, code, "edit", "plan#1", "--title", " The walk ", "--body-file",
               _text(tmp_path, "\r\nBuild the walk, leg by leg.\r\n")) == 0
    assert tracker.writes == writes
    assert capsys.readouterr().out == (
        "plan#1's title is already 'The walk': not written\n"
        "plan#1's text is already this text: not written\n")


def test_edit_refuses_a_card_still_marked_so_its_filing_command_finds_it_again(code, tmp_path,
                                                                                 capsys):
    """R-BAL186, R-BAL202: ``quill file`` run again finds the card it began by kind, title and
    text; rewritten while still marked, the re-run would file a second card.  A card closed
    while still marked is refused too: its title and text are what keep that command from
    filing anything over it, and no way out by hand is offered (``_card_refusal``)."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance", FILING))
    tracker.add(2, labels=("balance", FILING), is_open=False, state_reason="NOT_PLANNED",
                closed_by_tool=True)
    assert run(tracker, code, "edit", "plan#1", "--title", "Renamed") == 1
    assert capsys.readouterr().err == (
        "refused: plan#1 [step, balance] card 1 carries the 'filing' mark (R-BAL202): its "
        "`quill file` command finds it by its kind, title and text, so a new title or text "
        "would make that command file a second card (R-BAL186); finish its filing first "
        "(`quill show plan#1` says how)\n")
    assert run(tracker, code, "edit", "plan#2", "--body-file", _text(tmp_path, "New.")) == 1
    assert capsys.readouterr().err.endswith(
        "; closed while still marked, it keeps them so that command files nothing over it, "
        "and so it is not edited\n")
    assert not tracker.writes


def test_edit_refuses_a_questions_text_in_a_rulings_shape_and_its_conversion_still_runs(
        code, tmp_path, capsys):
    """A ruling-shaped text is what only a question's conversion writes, and ``file``'s
    conversion reads such an edit of quill's as an earlier conversion's (``_filing._Asked``):
    refused.  A plain new text is written, and the question still becomes its ruling with
    that text as its question."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight")
    shaped = _text(tmp_path, ruling_body("Ship it tonight?", "Yes."))
    assert run(tracker, code, "edit", "plan#2", "--body-file", shaped) == 1
    assert "is a question, and this text has a ruling's shape" in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, "edit", "plan#2", "--body-file",
               _text(tmp_path, "Ship it tomorrow?")) == 0
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Tomorrow",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", _text(tmp_path, "Yes.", "answer.md")) == 0
    assert tracker.bodies[2] == ruling_body("Ship it tomorrow?", "Yes.")
    assert tracker.cards_by_number[2].kind == "ruling"


def test_edit_with_nothing_to_write_or_no_such_card_is_refused(code, tmp_path, capsys):
    """Neither ``--title`` nor ``--body-file``: nothing to write; a number nobody filed: no
    card.  Each refused with nothing written."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "edit", "plan#1") == 1
    assert capsys.readouterr().err == (
        "refused: nothing to edit: pass --title, --body-file or both\n")
    assert run(tracker, code, "edit", "plan#9", "--title", "Nine") == 1
    assert capsys.readouterr().err == "refused: plan#9 does not exist\n"
    assert run(tracker, code, "edit", "plan#1", "--body-file", str(tmp_path / "missing")) == 2
    assert "failed: " in capsys.readouterr().err
    assert not tracker.writes


# -- comment ---------------------------------------------------------------------------------

def test_comment_posts_the_files_text_on_the_card_a_ruling_too(code, tmp_path, capsys):
    """A comment is a note beside a card's text, which it never changes, so a ruling takes one
    as any card does; the text is sent as the file holds it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "ruling", parent=1, is_open=False, state_reason="COMPLETED")
    note = _text(tmp_path, "Measured again on 10-06: still 9 rows.\n")
    assert run(tracker, code, "comment", "plan#1", "--body-file", note) == 0
    assert run(tracker, code, "comment", "plan#2", "--body-file", note) == 0
    assert tracker.writes == [("comment", 1, "Measured again on 10-06: still 9 rows.\n"),
                              ("comment", 2, "Measured again on 10-06: still 9 rows.\n")]
    assert capsys.readouterr().out == ("commented on plan#1 [step, balance] card 1\n"
                                       "commented on plan#2 [ruling, balance] card 2\n")


def test_comment_refuses_an_empty_text_and_a_card_that_does_not_exist(code, tmp_path, capsys):
    """Nothing is written for a text that is only space -- refused before any card is read
    -- nor for a number nobody filed; a file that cannot be read is a failed call (exit 2),
    as for every ``--body-file``."""
    tracker = FakeTracker()
    tracker.add(1)
    empty = _text(tmp_path, " \n\t\n")
    real = tracker.cards

    def unreadable(_numbers):
        raise GitHubError(502, "bad gateway")

    tracker.cards = unreadable
    assert run(tracker, code, "comment", "plan#1", "--body-file", empty) == 1, (
        "refused before any card is read: the failing read never runs")
    assert capsys.readouterr().err == (
        f"refused: {empty} holds no text: a comment says something\n")
    tracker.cards = real
    assert run(tracker, code, "comment", "plan#9",
               "--body-file", _text(tmp_path, "A note.")) == 1
    assert capsys.readouterr().err == "refused: plan#9 does not exist\n"
    assert run(tracker, code, "comment", "plan#1", "--body-file", str(tmp_path / "gone")) == 2
    assert not tracker.writes


def test_comment_needs_its_text_on_the_command_line(code):
    """``--body-file`` is required: argparse's usage error, exit 2, before anything is read."""
    with pytest.raises(SystemExit) as stopped:
        run(FakeTracker(), code, "comment", "plan#1")
    assert stopped.value.code == 2
