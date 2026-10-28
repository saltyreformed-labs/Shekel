"""The developer's comments on a card, and quill's answers to them (plan#1822).

The developer leaves a comment on a card for the session working it, and that session reads
it, acts on it and answers it with ``quill answer`` (:func:`cmd_answer`), saying what it did.
His comments show as SaltyReformed and quill's as its App, ``shekel-quill``.  An answer opens
with an @mention of each person it answers and quotes their comments, because only a mention
reaches him: on plan#1822 on 2026-10-27, after his comment, a plain note of quill's did not
notify him and a reply opening with his @mention did (his answer to the lane's test).  Each
answer carries :data:`ANSWER_MARK`, so a comment of a person's is unanswered until an answer
follows it, whatever other notes of quill's do (a drop's reason, say: :func:`unanswered`, the
one reading).  ``/charter`` says a session acts only on his, and when it reads them: when it
claims the card, at each fresh session start (``Resume plan#T``, its item 0), and before each
leaf's pull request.  ``quill show`` prints each comment whole, an answer's mark among its
text, but for a stop's note older than the card's newest, which it names in a line
(``_notes.superseded``), and says of each task under the card it shows that holds a comment
still unanswered so (``_show``); ``quill next`` names every claimed card, and every
task under one, holding a comment still unanswered (:func:`unanswered_lines`, ruling
plan#1924), and ``quill answer`` answers a task's as it answers a card's.

Kept apart from the command module and ``_state``, each near the 1,000 lines
``too-many-lines`` allows (933 and 978 lines on 2026-10-27).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.quill._command import Refused, _label, _one, _read
from tools.quill._git import Claim
from tools.quill._state import holder
from tools.quill._tracker import Card, Comment, Tracker

#: What every answer of quill's ends with, an HTML comment GitHub does not render: what tells
#: an answer from quill's other notes, so only an answer answers.  :func:`answer_text` writes
#: it and :func:`unanswered` reads it, the one writer and the one reader.
ANSWER_MARK = "<!-- quill: answer -->"


def byline(comment: Comment) -> str:
    """When ``comment`` was written and by whom, as quill prints it (``?`` for an account since
    deleted): the one spelling, which ``quill show`` heads each comment with and ``quill next``
    names an unanswered one by."""
    return f"{comment.created_at} by {comment.author or '?'}"


def unanswered(comments: Sequence[Comment], app_login: str) -> list[Comment]:
    """The comments on a card that its session has yet to answer, oldest first: every comment
    of a person's after quill's newest answer (one of ``app_login``'s carrying
    :data:`ANSWER_MARK`), or every one of them when quill has answered none.  ``app_login`` is
    the App's login as GitHub's GraphQL names a comment's author (``recorded/comments.json``).

    Every comment not quill's is a person's, as a close not quill's is
    (``_tracker.card_from``'s ``touched_by_hand``), and in the plan's private tracker that
    person is the developer; one whose account was since deleted (no author) is a person's
    too.  Order is the tracker's, oldest first (:meth:`_tracker.Tracker.comments`), never
    ``created_at``: two comments can share a second, as the recording's two do.  A comment
    of his posted while an answer was being written, and so before it in that order, reads
    as answered though the answer does not quote it: unhandled, as the race needs his
    comment to land in the second between ``quill answer``'s read and its write."""
    answers = [index for index, comment in enumerate(comments)
               if comment.author == app_login and ANSWER_MARK in comment.body]
    since = answers[-1] + 1 if answers else 0
    return [comment for comment in comments[since:] if comment.author != app_login]


def _quoted(comment: Comment) -> str:
    """``comment``'s text as a Markdown quote, each line of it."""
    return "\n".join(f"> {line}" if line else ">"
                     for line in comment.body.rstrip().splitlines())


def answer_text(comments: Sequence[Comment], text: str) -> str:
    """What ``quill answer`` posts to answer ``comments`` (:func:`unanswered`'s, at least
    one) with ``text``: an @mention of each of their authors that has a login, the time of
    each, each quoted, then ``text``, then :data:`ANSWER_MARK`."""
    mentions = " ".join(dict.fromkeys(f"@{c.author}" for c in comments if c.author))
    plural = "s" if len(comments) > 1 else ""
    times = ", ".join(comment.created_at for comment in comments)
    head = (f"{mentions}, answering your comment{plural} of {times}:" if mentions else
            f"Answering the comment{plural} of {times}:")
    quotes = "\n\n".join(_quoted(comment) for comment in comments)
    return f"{head}\n\n{quotes}\n\n{text.strip()}\n\n{ANSWER_MARK}\n"


def cmd_answer(args, tracker: Tracker, _root: Path) -> int:
    """Answer the comments of a person's on a card that no answer of quill's has followed
    (:func:`unanswered`) with the text of ``--body-file``, in one comment
    (:func:`answer_text`).  Refused, with nothing written: for a text that is only space,
    before any card is read; and for a card holding no comment to answer (a note that is no
    answer is ``quill comment``'s)."""
    text = _read(args.body_file)
    if not text.strip():
        raise Refused(f"{args.body_file} holds no text: an answer says what was done")
    card = _one(tracker, args.card)
    waiting = unanswered(tracker.comments(card.number), tracker.app_login)
    if not waiting:
        raise Refused(f"{_label(card)} holds no comment of a person's after quill's last "
                      "answer: nothing to answer (`quill comment` adds a note)")
    tracker.comment(card.number, answer_text(waiting, text))
    print(f"answered on {_label(card)}: " + "; ".join(map(byline, waiting)))
    return 0


def unanswered_lines(tracker: Tracker, claims: Mapping[int, Claim],
                     cards: Mapping[int, Card]) -> list[str]:
    """A line for each claimed card (``claims``), and each task under one (ruling plan#1924),
    holding a comment of a person's that no answer of quill's has followed
    (:func:`unanswered`): each claimed card in card order, then its tasks in its order of them,
    what ``quill next`` prints.  ``cards`` holds the cards ``next`` read already.  A claimed
    card it does not hold (closed, so no open listing finds it, as one dropped while claimed
    is) is read by its number first, and one that read does not find either (a ``card/N``
    pushed by hand for a number nobody filed) is passed over.  Then one read of the comments
    of every claimed card and every task under each, open or closed, many cards a request
    (:meth:`_tracker.Tracker.comments_of`, ruling plan#1931); a task deleted between the two
    reads is absent from the second, and passed over too."""
    claimed = {number: cards[number] for number in claims if number in cards}
    claimed.update(tracker.cards(set(claims) - set(cards)))
    comments = tracker.comments_of(
        {*claimed, *(task for card in claimed.values() for task in card.task_children)})
    lines = []
    for number in sorted(claimed):
        for asked in (number, *claimed[number].task_children):
            if waiting := unanswered(comments.get(asked, ()), tracker.app_login):
                lines.append(_unanswered_line(asked, number, claims[number], waiting))
    return lines


def _unanswered_line(asked: int, card: int, claim: Claim, waiting: Sequence[Comment]) -> str:
    """The line ``quill next`` names card ``asked`` by, claimed card ``card`` itself or a task
    under it, ``claim`` its claim: how many comments of a person's no answer has followed
    (``waiting``), the newest's byline, and the commands its session reads and answers them
    with."""
    what = f"plan#{asked}" if asked == card else f"plan#{asked}, a task of plan#{card},"
    return (f"{what} on {holder(claim)}: {len(waiting)} comment"
            f"{'s' if len(waiting) > 1 else ''} no answer of quill's has followed, "
            f"the newest {byline(waiting[-1])} -- its session reads them "
            f"(`quill show plan#{asked}`) and answers them "
            f"(`quill answer plan#{asked} --body-file TEXT`)")
