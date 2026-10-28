"""A stop's note (rulings plan#1921, plan#1922, plan#1923): what a session that stops leaves the
next one, as a comment of quill's on the card that session resumes, never as a file.

``quill note plan#N --body-file NOTE`` (:func:`cmd_note`) posts it on the task card N's next
session starts (:func:`resumed`): the one in progress, else the first open task in N's order
that git does not say is done, else N itself; and prints that session's prompt,
``Resume plan#T``.  ``quill show plan#T`` prints the newest note on a card in full and names
each older one in a line (:func:`superseded`), so a task begun across several sessions shows
its current note, not a stack of stale ones.  The note holds only what git and the card cannot
say: things made outside git, what was not measured, traps; decisions for the review go in the
piece's commit message.  So quill refuses a note over :data:`NOTE_CAP` characters (ruling
plan#1922): GitHub's own cap on a comment, tens of thousands of characters, is no brake.

Its own module: the command module stands within fifty lines of the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.quill import _git
from tools.quill._command import _label, _one, _read
from tools.quill._exits import Refused
from tools.quill._state import done_tasks
from tools.quill._tasks import in_progress
from tools.quill._tracker import BareCard, Card, Comment, Tracker

#: The longest note quill posts, in characters of its text, the space around it not counted
#: (ruling plan#1922: the size of a ruling's cap, ``check.RULING_CAP``, a cap of its own).
NOTE_CAP = 2000
#: What every note of quill's ends with, an HTML comment GitHub does not render: what tells a
#: note from quill's other comments.  :func:`note_text` writes it and :func:`is_note` reads it,
#: the one writer and the one reader.
NOTE_MARK = "<!-- quill: note -->"


def is_note(comment: Comment, app_login: str) -> bool:
    """Whether ``comment`` is a stop's note: one of quill's (``app_login``'s) carrying
    :data:`NOTE_MARK`."""
    return comment.author == app_login and NOTE_MARK in comment.body


def note_text(text: str) -> str:
    """What ``quill note`` posts for ``text``: its text, then :data:`NOTE_MARK`."""
    return f"{text.strip()}\n\n{NOTE_MARK}\n"


def superseded(comments: Sequence[Comment], app_login: str) -> dict[int, Comment]:
    """Each note among ``comments`` (:func:`is_note`) but the newest, by its place among them,
    with the newest note: what ``quill show`` names in a line, not in full.  Order is the
    tracker's, oldest first (``_comments.unanswered`` says why order, never ``created_at``)."""
    notes = [index for index, comment in enumerate(comments) if is_note(comment, app_login)]
    return {index: comments[notes[-1]] for index in notes[:-1]}


def resumed(card: Card, held: Mapping[int, BareCard], reads: _git.Reads) -> int:
    """The card that ``card``'s next session starts: the task under it in progress on its claim
    (:func:`_tasks.in_progress`), else the first task in its order of them that is open and
    that git does not say is done (:func:`_state.done_tasks`), else ``card`` itself.  ``held``
    holds each task under it, read by number; ``reads`` is what this command read of git."""
    tasks = [number for number in card.task_children if number in held]
    begun = in_progress(reads.claims)
    done = done_tasks(held, reads.shipped.tasks, reads.claims)
    return next((number for number in tasks if number in begun),
                next((number for number in tasks
                      if held[number].is_open and number not in done), card.number))


def cmd_note(args, tracker: Tracker, root: Path) -> int:
    """Post a stop's note, the text of ``--body-file``, on the card claimed card N's next
    session starts (:func:`resumed`), and print that session's prompt.  Refused, with nothing
    written: for a text that is only space, and one over :data:`NOTE_CAP` characters, before
    any card is read; and for a card ``origin`` holds no claim of (``card/N``), which no
    session resumes."""
    text = _read(args.body_file)
    if not text.strip():
        raise Refused(f"{args.body_file} holds no text: a note says what git and the card "
                      "cannot")
    if (length := len(text.strip())) > NOTE_CAP:
        raise Refused(f"{args.body_file} holds {length} characters, and a note is at most "
                      f"{NOTE_CAP} (ruling plan#1922): keep only what git and the card cannot "
                      "say -- things made outside git, what was not measured, traps")
    card = _one(tracker, args.card)
    reads = _git.read(root)
    if card.number not in reads.claims:
        raise Refused(f"{_label(card)} has no claim: origin holds no "
                      f"{_git.card_branch(card.number)}, so no session resumes it")
    held = tracker.cards(card.task_children)
    number = resumed(card, held, reads)
    tracker.comment(number, note_text(text))
    print(f"noted on {_label(held.get(number, card))}")
    print(f"the next session's prompt: Resume plan#{number}")
    return 0
