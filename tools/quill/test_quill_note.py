"""A stop's note (rulings plan#1921, plan#1922, plan#1923, :mod:`_notes`): ``quill note``
posts it on the task a claimed card's next session starts -- the one in progress, else the
first open one git does not say is done, else the card -- and prints that session's prompt;
it refuses a note over :data:`_notes.NOTE_CAP` characters, one that is only space, and a card
no claim holds, writing nothing; and ``quill show`` prints a card's newest note in full and
names each older one in a line.  Over :class:`_fake.FakeTracker` and a throwaway git
repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from tools.quill._comments import ANSWER_MARK
from tools.quill._fake import FakeTracker, begun, claim, piece, run, two_tasks
from tools.quill._notes import NOTE_CAP, NOTE_MARK
from tools.quill._tracker import Comment
from tools.quill.setup_tracker import APP_LOGIN

#: The developer's login, as GitHub names the author of his comments.
DEVELOPER = "SaltyReformed"


def _note(tmp_path, text="The dev database is a clone of today's dump.") -> str:
    """A file holding ``text``, for ``--body-file``; its path."""
    path = tmp_path / "note.md"
    path.write_text(text)
    return str(path)


def _noted(tracker, code, capsys, tmp_path, text="The dev database is a clone of today's dump."):
    """Run ``quill note plan#1`` with ``text``: the card it wrote on, and what it printed."""
    capsys.readouterr()
    assert run(tracker, code, "note", "plan#1", "--body-file", _note(tmp_path, text)) == 0
    (write,) = [write for write in tracker.writes if write[0] == "comment"]
    tracker.writes.clear()
    return write[1], capsys.readouterr().out


def test_the_note_lands_on_the_task_in_progress_and_prints_its_prompt(code, capsys, tmp_path):
    """plan#1 is claimed and its claim has begun its second task, plan#3, while plan#2, before
    it in the card's order, is open and not begun: the note lands on plan#3, its text then the
    mark, and the prompt names plan#3."""
    tracker = two_tasks()
    claim(code, 1)
    begun(code, 1, 3)
    number, out = _noted(tracker, code, capsys, tmp_path, "\n  Not measured: the deploy.\n")
    assert number == 3
    assert tracker.notes[3][-1].body == f"Not measured: the deploy.\n\n{NOTE_MARK}\n"
    assert out == ("noted on plan#3 [task, no arc] piece b\n"
                   "the next session's prompt: Resume plan#3\n")


def test_with_none_in_progress_the_note_lands_on_the_first_open_task_git_says_is_not_done(
        code, capsys, tmp_path):
    """plan#1 holds tasks plan#2, plan#4, plan#5 and plan#3, in that order.  plan#2 is done on
    the claim, though the tracker still shows it open; plan#4 was dropped (closed); plan#5 is
    open and not begun: the note lands on plan#5, the first in the card's order, not plan#3,
    the lowest number."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(4, 1, is_open=False, state_reason="NOT_PLANNED")
    tracker.add_task(5, 1)
    tracker.add_task(3, 1)
    claim(code, 1)
    piece(code, 1, 2)
    number, out = _noted(tracker, code, capsys, tmp_path)
    assert number == 5 and out.endswith("the next session's prompt: Resume plan#5\n")


def test_with_every_task_done_the_note_lands_on_the_card(code, capsys, tmp_path):
    """Both of plan#1's tasks are done on its claim, and a card with no task holds none to
    start: each note lands on the card itself, and the prompt names it."""
    tracker = two_tasks()
    tracker.add(6)
    claim(code, 1)
    claim(code, 6)
    piece(code, 1, 2, 3)
    number, out = _noted(tracker, code, capsys, tmp_path)
    assert number == 1 and out == ("noted on plan#1 [step, balance] card 1\n"
                                   "the next session's prompt: Resume plan#1\n")
    capsys.readouterr()
    assert run(tracker, code, "note", "plan#6", "--body-file", _note(tmp_path)) == 0
    assert tracker.writes[-1][:2] == ("comment", 6)


def test_a_note_at_the_cap_is_posted_and_one_past_it_is_refused(code, capsys, tmp_path):
    """Ruling plan#1922: a note of exactly :data:`_notes.NOTE_CAP` characters, the space around
    it not counted, is posted; one character more is refused with nothing written, saying how
    long it is and what a note holds."""
    tracker = two_tasks()
    claim(code, 1)
    number, _ = _noted(tracker, code, capsys, tmp_path, f"\n\n{'x' * NOTE_CAP}\n\n")
    assert number == 2
    assert run(tracker, code, "note", "plan#1", "--body-file",
               _note(tmp_path, "x" * (NOTE_CAP + 1))) == 1
    assert (f"holds {NOTE_CAP + 1} characters, and a note is at most {NOTE_CAP} (ruling "
            "plan#1922): keep only what git and the card cannot say") in capsys.readouterr().err
    assert not tracker.writes


def test_a_note_of_only_space_and_one_for_a_card_nobody_claims_are_refused(code, capsys,
                                                                          tmp_path):
    """Refused, with nothing written: a note that is only space, before any card is read (so
    for a card nobody filed too); and a note for plan#1, which no ``card/1`` on origin
    claims, so no session resumes it."""
    tracker = two_tasks()
    capsys.readouterr()
    assert run(tracker, code, "note", "plan#99", "--body-file", _note(tmp_path, " \n\t")) == 1
    assert "holds no text: a note says what git and the card cannot" in capsys.readouterr().err
    assert run(tracker, code, "note", "plan#1", "--body-file", _note(tmp_path)) == 1
    assert ("plan#1 [step, balance] card 1 has no claim: origin holds no card/1, so no session "
            "resumes it") in capsys.readouterr().err
    assert not tracker.writes


def test_show_prints_the_newest_note_in_full_and_names_each_older_one(code, capsys, tmp_path):
    """plan#2 holds a note, his comment, quill's answer to it, then a second note: show prints
    the second note in full and the first in a line naming it superseded, and his comment and
    the answer in full, each as before; a note a person wrote the mark into is no note of
    quill's, so it is printed whole."""
    tracker = two_tasks()
    claim(code, 1)
    _noted(tracker, code, capsys, tmp_path, "The first stop's note.")
    tracker.notes[2].append(Comment(DEVELOPER, "2026-10-06T01:00:00Z",
                                    f"Mine, with the mark.\n\n{NOTE_MARK}\n"))
    tracker.notes[2].append(Comment(APP_LOGIN, "2026-10-06T02:00:00Z",
                                    f"Done.\n\n{ANSWER_MARK}\n"))
    _noted(tracker, code, capsys, tmp_path, "The second stop's note.")
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#2") == 0
    out = capsys.readouterr().out
    comments = out[out.index("== comment"):out.index("no commit on")]
    assert comments == (
        "== comment, 2026-10-06T00:00:00Z by shekel-quill: a stop's note, superseded by the "
        "newest, of 2026-10-06T00:00:00Z; the card holds its text\n"
        f"== comment, 2026-10-06T01:00:00Z by SaltyReformed\nMine, with the mark.\n\n{NOTE_MARK}\n"
        f"== comment, 2026-10-06T02:00:00Z by shekel-quill\nDone.\n\n{ANSWER_MARK}\n"
        f"== comment, 2026-10-06T00:00:00Z by shekel-quill\nThe second stop's note.\n\n"
        f"{NOTE_MARK}\n\n")
