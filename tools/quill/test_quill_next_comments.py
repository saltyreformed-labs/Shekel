"""The developer's comments on a card and quill's answers to them (plan#1822, :mod:`_comments`):
``quill answer`` answers every comment of a person's no answer has followed, opening with an
@mention of each author and quoting each comment; and ``quill next`` names every claimed card
holding one, and no other card -- not one he commented on that nobody claims, nor one quill
answered after him -- while a note of quill's that is no answer (a drop's reason) answers
nothing.  Over :class:`_fake.FakeTracker` and a
throwaway git repository (the ``code`` fixture, ``conftest.py``); and
:func:`_comments.unanswered` over the comments GitHub answered for quill's App
(``recorded/comments.json``).  Nothing here calls GitHub.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from tools.quill._comments import ANSWER_MARK, unanswered
from tools.quill._fake import FakeTracker, claim, run
from tools.quill._github import GitHub
from tools.quill._recorded import Replay, recording
from tools.quill._tracker import Board, Comment, Tracker, TrackerError
from tools.quill.setup_tracker import APP_LOGIN, PLAN

#: The developer's login, as GitHub's GraphQL named the author of his comment on plan#1822
#: (read 2026-10-27).
DEVELOPER = "SaltyReformed"
#: When every claim's branch here was last committed, so each line is known whole.
COMMITTED = "2026-10-05T09:00:00-04:00"
#: The scratch step the comments recording filed, on which the App posted two comments.
S = 28


def _said(tracker, number, author, when, text="Split this before you build it."):
    """A comment by ``author`` on card ``number`` at ``when``, after its others, as GitHub lists
    a card's comments, oldest first."""
    tracker.notes.setdefault(number, []).append(Comment(author, when, text))


def _next(tracker, code, capsys) -> list[str]:
    """The UNANSWERED COMMENT lines ``quill next`` prints; it exits 0."""
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    return [line for line in capsys.readouterr().out.splitlines()
            if line.startswith("UNANSWERED COMMENT: ")]


def _line(number, when, author=DEVELOPER, count=1) -> str:
    """The line ``next`` names claimed card ``number`` by: ``count`` comments unanswered, the
    newest ``author``'s at ``when``."""
    return (f"UNANSWERED COMMENT: plan#{number} on card/{number} (claimed 2026-10-04T12:00:00Z; "
            f"last commit {COMMITTED}): {count} comment{'s' if count > 1 else ''} no answer of "
            f"quill's has followed, the newest {when} by {author} -- its session reads them "
            f"(`quill show plan#{number}`) and answers them "
            f"(`quill answer plan#{number} --body-file TEXT`)")


def _text(tmp_path, text="Split into three leaves, plan#5-plan#7.") -> str:
    """A file holding ``text``, for a ``--body-file``; its path."""
    path = tmp_path / "text.md"
    path.write_text(text)
    return str(path)


def test_his_comment_is_named_until_quill_answers_it_and_again_when_he_comments_after(
        code, capsys, tmp_path, monkeypatch):
    """plan#1 is claimed and he comments on it: next names it.  A plain ``quill comment`` is no
    answer: next still names it.  ``quill answer`` answers it: next names nothing.  He comments
    again: next names it again, by that comment."""
    tracker = FakeTracker()
    tracker.add(1)
    monkeypatch.setenv("GIT_COMMITTER_DATE", COMMITTED)
    claim(code, 1)
    _said(tracker, 1, DEVELOPER, "2026-10-05T14:00:00Z")
    assert _next(tracker, code, capsys) == [_line(1, "2026-10-05T14:00:00Z")]
    assert run(tracker, code, "comment", "plan#1", "--body-file", _text(tmp_path, "A note.")) == 0
    assert _next(tracker, code, capsys) == [_line(1, "2026-10-05T14:00:00Z")]
    assert run(tracker, code, "answer", "plan#1", "--body-file", _text(tmp_path)) == 0
    assert _next(tracker, code, capsys) == []
    _said(tracker, 1, DEVELOPER, "2026-10-07T09:30:00Z", "Leave plan#7 for later.")
    assert _next(tracker, code, capsys) == [_line(1, "2026-10-07T09:30:00Z")]


def test_only_a_claimed_card_holding_a_comment_unanswered_is_named(code, capsys, monkeypatch):
    """plan#3 holds his comment but nobody claims it, plan#4 is claimed and holds quill's
    note only, plan#5 is claimed and holds none: none is named.  plan#2, claimed, holds two
    comments of his, and plan#8, claimed, one by an account since deleted, a person's too: both
    are named, in card order, which a set of the two does not keep (CPython iterates
    ``{2, 8}`` as 8, then 2; measured 2026-10-27 on 3.14)."""
    tracker = FakeTracker()
    for number in (1, 2, 3, 4, 5, 8):
        tracker.add(number)
    monkeypatch.setenv("GIT_COMMITTER_DATE", COMMITTED)
    for number in (8, 2, 4, 5):
        claim(code, number)
    _said(tracker, 3, DEVELOPER, "2026-10-05T14:00:00Z")
    _said(tracker, 4, APP_LOGIN, "2026-10-05T14:00:00Z", "Estimated: two sessions.")
    _said(tracker, 2, DEVELOPER, "2026-10-05T15:00:00Z")
    _said(tracker, 2, DEVELOPER, "2026-10-05T15:30:00Z")
    _said(tracker, 8, None, "2026-10-05T16:00:00Z")
    assert list({2, 8}) == [8, 2]
    assert _next(tracker, code, capsys) == [
        _line(2, "2026-10-05T15:30:00Z", count=2), _line(8, "2026-10-05T16:00:00Z", "?")]


def test_a_drop_note_after_his_comment_answers_nothing(code, capsys, monkeypatch):
    """Review SF-2: he comments on claimed plan#1, then a drop closes it, its reason a note of
    quill's after his comment and no answer.  Closed, no open listing holds plan#1, so it is
    read by its number, and his comment is still named."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    monkeypatch.setenv("GIT_COMMITTER_DATE", COMMITTED)
    claim(code, 1)
    _said(tracker, 1, DEVELOPER, "2026-10-05T14:00:00Z", "Not a duplicate: keep it.")
    assert run(tracker, code, "drop", "plan#1", "--why", "a duplicate of plan#2") == 0
    assert not tracker.cards_by_number[1].is_open
    assert [comment.author for comment in tracker.comments(1)] == [DEVELOPER, APP_LOGIN]
    assert _next(tracker, code, capsys) == [_line(1, "2026-10-05T14:00:00Z")]


def test_a_card_branch_naming_no_card_is_passed_over(code, capsys):
    """``card/99`` was pushed by hand, and nobody filed plan#99: a read of its comments would
    be GitHub's NOT_FOUND error, so it is never made, and next names nothing and exits 0."""
    tracker = FakeTracker()
    tracker.add(1)
    claim(code, 99)
    assert _next(tracker, code, capsys) == []


def test_answer_mentions_and_quotes_each_comment_unanswered_and_ends_with_the_mark(
        code, capsys, tmp_path):
    """plan#1 holds an answered comment of his, then quill's answer, then two of his and one
    by an account since deleted: the answer mentions him once, names the three times, quotes
    the three (a blank line of his kept as a bare quote line), then the text, then the mark,
    and says what it answered."""
    tracker = FakeTracker()
    tracker.add(1)
    _said(tracker, 1, DEVELOPER, "2026-10-05T13:00:00Z", "Answered already.")
    _said(tracker, 1, APP_LOGIN, "2026-10-05T13:30:00Z", f"Done.\n\n{ANSWER_MARK}\n")
    _said(tracker, 1, DEVELOPER, "2026-10-05T14:00:00Z", "Why three leaves?\n\nNot two?")
    _said(tracker, 1, None, "2026-10-05T14:30:00Z", "Agreed.")
    _said(tracker, 1, DEVELOPER, "2026-10-05T15:00:00Z", "And plan#7?\n")
    capsys.readouterr()
    assert run(tracker, code, "answer", "plan#1", "--body-file",
               _text(tmp_path, "\nThree, one per screen.\n")) == 0
    assert tracker.writes[-1] == ("comment", 1, (
        "@SaltyReformed, answering your comments of 2026-10-05T14:00:00Z, "
        "2026-10-05T14:30:00Z, 2026-10-05T15:00:00Z:\n\n"
        "> Why three leaves?\n>\n> Not two?\n\n> Agreed.\n\n> And plan#7?\n\n"
        f"Three, one per screen.\n\n{ANSWER_MARK}\n"))
    assert capsys.readouterr().out == (
        "answered on plan#1 [step, balance] card 1: 2026-10-05T14:00:00Z by SaltyReformed; "
        "2026-10-05T14:30:00Z by ?; 2026-10-05T15:00:00Z by SaltyReformed\n")


def test_answer_with_no_login_to_mention_names_no_one(code, tmp_path):
    """Only an account since deleted wrote the comment unanswered: no @mention is made."""
    tracker = FakeTracker()
    tracker.add(1)
    _said(tracker, 1, None, "2026-10-05T14:00:00Z", "Agreed.")
    assert run(tracker, code, "answer", "plan#1", "--body-file", _text(tmp_path, "Noted.")) == 0
    assert tracker.writes[-1] == ("comment", 1, (
        f"Answering the comment of 2026-10-05T14:00:00Z:\n\n> Agreed.\n\nNoted.\n\n"
        f"{ANSWER_MARK}\n"))


def test_answer_refuses_an_empty_text_and_a_card_with_nothing_to_answer(code, capsys,
                                                                       tmp_path):
    """Refused, with nothing written: a text that is only space, before any card is read (so
    for a card nobody filed too); a card with no comment; and one whose comments of his are
    all followed by an answer, though a note of quill's came after it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    capsys.readouterr()
    assert run(tracker, code, "answer", "plan#99", "--body-file", _text(tmp_path, " \n")) == 1
    assert "holds no text" in capsys.readouterr().err
    assert run(tracker, code, "answer", "plan#1", "--body-file", _text(tmp_path)) == 1
    assert "holds no comment of a person's after quill's last answer" in (
        capsys.readouterr().err)
    _said(tracker, 2, DEVELOPER, "2026-10-05T14:00:00Z")
    _said(tracker, 2, APP_LOGIN, "2026-10-05T14:30:00Z", f"Done.\n\n{ANSWER_MARK}\n")
    _said(tracker, 2, APP_LOGIN, "2026-10-05T15:00:00Z", "A note.")
    assert run(tracker, code, "answer", "plan#2", "--body-file", _text(tmp_path)) == 1
    assert not tracker.writes


def test_the_comments_github_answered_for_quills_app_are_quills():
    """Recorded 2026-10-06 (``recorded/comments.json``): GitHub names the App that posted
    #28's two comments by :data:`setup_tracker.APP_LOGIN`, the login every
    ``Tracker.connect`` checks, so neither is a person's and nothing is unanswered.  Made his,
    the newest is unanswered, and so is the older, as the newest is a note and no answer; made
    an answer, the newest answers the older made his, though both were posted within the same
    second: order decides, never the time."""
    replay = Replay("comments")
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, PLAN, recording("comments")["scratch"]["board"]),
                      APP_LOGIN)
    older, newest = tracker.comments(S)
    assert (older.author, newest.author) == (APP_LOGIN, APP_LOGIN)
    assert older.created_at == newest.created_at
    assert unanswered([older, newest], APP_LOGIN) == []
    his = dataclasses.replace(newest, author=DEVELOPER)
    assert unanswered([older, his], APP_LOGIN) == [his]
    his = dataclasses.replace(older, author=DEVELOPER)
    assert unanswered([his, newest], APP_LOGIN) == [his]
    answer = dataclasses.replace(newest, body=newest.body + ANSWER_MARK)
    assert unanswered([his, answer], APP_LOGIN) == []
    assert unanswered([], APP_LOGIN) == []


class _Answered:
    """A GitHub answering every GraphQL read with ``data``."""

    def __init__(self, data):
        """Hold the answer."""
        self.data = data

    def graphql(self, _query, **_variables):
        """The answer."""
        return self.data


def test_a_comment_github_answers_as_null_is_refused_not_read():
    """Review N-9: GitHub's schema lets a comment in the list be null, which no author can be
    read from.  From #28's recorded answer, its newest comment made null: the read refuses it
    as every other connection's null node is refused (``connection_nodes``), with a
    TrackerError, which ``quill.main`` ends in one ``failed:`` line (exit 2), where reading it
    raised a TypeError, which ``main`` does not catch."""
    data = next(copy.deepcopy(exchange["answer"]["data"])
                for exchange in recording("comments")["exchanges"]
                if "comments(first" in ((exchange["body"] or {}).get("query") or "")
                and exchange["body"]["variables"]["number"] == S)
    data["repository"]["issue"]["comments"]["nodes"][-1] = None
    tracker = Tracker(_Answered(data), Board(None, PLAN, "B"), APP_LOGIN)
    with pytest.raises(TrackerError, match="answered one of plan#28's comments as null"):
        tracker.comments(S)
