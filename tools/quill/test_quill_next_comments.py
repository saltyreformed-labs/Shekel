"""The developer's comments on a card and quill's answers to them (plan#1822, :mod:`_comments`):
``quill answer`` answers every comment of a person's no answer has followed, opening with an
@mention of each author and quoting each comment; and ``quill next`` names every claimed card
holding one, and every task under one holding one (ruling plan#1924), after one read of all
their comments (ruling plan#1931), and no other card -- not one he commented on that nobody
claims, nor a task under it, nor one quill answered after him -- while a note of quill's
that is no answer (a drop's reason) answers nothing; ``quill show`` says on each task's line
how many of his no answer has followed.  Over :class:`_fake.FakeTracker` and a
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


class _Asked(FakeTracker):
    """A tracker in memory keeping each set of cards whose comments a read of many asked for,
    and each card whose comments were read alone (``answer`` reads them so; ``next`` never
    does)."""

    def __init__(self):
        """An empty tracker, asked nothing yet."""
        super().__init__()
        self.asked: list[set[int]] = []
        self.alone: list[int] = []

    def comments_of(self, numbers):
        """Keep what was asked, then answer it."""
        self.asked.append(set(numbers))
        return super().comments_of(numbers)

    def comments(self, number):
        """Keep the card read alone, then answer it."""
        self.alone.append(number)
        return super().comments(number)


def _task_line(task, card, when, author=DEVELOPER, count=1) -> str:
    """The line ``next`` names task ``task`` under claimed card ``card`` by: ``count``
    comments unanswered, the newest ``author``'s at ``when``."""
    return (f"UNANSWERED COMMENT: plan#{task}, a task of plan#{card}, on card/{card} (claimed "
            f"2026-10-04T12:00:00Z; last commit {COMMITTED}): {count} comment"
            f"{'s' if count > 1 else ''} no answer of quill's has followed, the newest {when} by "
            f"{author} -- its session reads them (`quill show plan#{task}`) and answers them "
            f"(`quill answer plan#{task} --body-file TEXT`)")


def test_his_comment_on_a_claimed_cards_task_is_named_until_quill_answers_it(
        code, capsys, tmp_path, monkeypatch):
    """Ruling plan#1924: claimed plan#1 holds tasks plan#4, closed, and plan#3, open, in that
    order.  His comments on both, two on plan#3, and one on plan#1 itself: next names the card
    first, then its tasks in its order of them, not in card number order, each the way it
    names a card, after one read of the comments of the card and both its tasks (ruling
    plan#1931).  ``quill answer plan#3`` answers the task's two: next names it no more, and
    still names the others."""
    tracker = _Asked()
    tracker.add(1)
    tracker.add_task(4, 1, is_open=False)
    tracker.add_task(3, 1)
    monkeypatch.setenv("GIT_COMMITTER_DATE", COMMITTED)
    claim(code, 1)
    _said(tracker, 3, DEVELOPER, "2026-10-05T14:00:00Z")
    _said(tracker, 3, DEVELOPER, "2026-10-05T14:30:00Z", "And the test?")
    _said(tracker, 4, DEVELOPER, "2026-10-05T15:00:00Z")
    _said(tracker, 1, DEVELOPER, "2026-10-05T16:00:00Z")
    assert _next(tracker, code, capsys) == [
        _line(1, "2026-10-05T16:00:00Z"), _task_line(4, 1, "2026-10-05T15:00:00Z"),
        _task_line(3, 1, "2026-10-05T14:30:00Z", count=2)]
    assert tracker.asked == [{1, 3, 4}] and not tracker.alone
    assert run(tracker, code, "answer", "plan#3", "--body-file", _text(tmp_path)) == 0
    assert tracker.writes[-1][:2] == ("comment", 3)
    assert _next(tracker, code, capsys) == [
        _line(1, "2026-10-05T16:00:00Z"), _task_line(4, 1, "2026-10-05T15:00:00Z")]


def test_a_task_under_a_card_nobody_claims_is_not_read_by_next(code, capsys, monkeypatch):
    """plan#1, unclaimed, holds task plan#2 with his comment, and claimed plan#5 holds task
    plan#6 with none: next names nothing, and its one read of comments asks for plan#5 and
    plan#6 alone, never plan#2."""
    tracker = _Asked()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add(5)
    tracker.add_task(6, 5)
    monkeypatch.setenv("GIT_COMMITTER_DATE", COMMITTED)
    claim(code, 5)
    _said(tracker, 2, DEVELOPER, "2026-10-05T14:00:00Z")
    assert _next(tracker, code, capsys) == []
    assert tracker.asked == [{5, 6}] and not tracker.alone


def test_a_task_deleted_since_its_card_was_read_is_passed_over(code, capsys, monkeypatch):
    """Claimed plan#1 still lists task plan#2, which was deleted after his comment on it, and
    plan#3, which holds his comment: the read of their comments holds no plan#2, so next
    passes it over, names plan#3 and exits 0.  The board holds no plan#2 either, as the
    board's read passes a deleted issue over (``_tracker.Board.held``)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(3, 1)
    monkeypatch.setenv("GIT_COMMITTER_DATE", COMMITTED)
    claim(code, 1)
    _said(tracker, 2, DEVELOPER, "2026-10-05T14:00:00Z")
    _said(tracker, 3, DEVELOPER, "2026-10-05T15:00:00Z")
    del tracker.cards_by_number[2]
    tracker.board.items.remove(2)
    assert [child.number for child in tracker.cards([1])[1].children] == [2, 3]
    assert _next(tracker, code, capsys) == [_task_line(3, 1, "2026-10-05T15:00:00Z")]


def test_show_says_on_each_tasks_line_how_many_of_his_comments_no_answer_has_followed(
        code, capsys, tmp_path):
    """Ruling plan#1924: plan#1, claimed by nobody (show reads every card's tasks, so a session
    that claims it sees what he left first), holds task plan#2 with two comments of his,
    task plan#3 with one, and task plan#4 with an answered one: plan#2's line and plan#3's say
    how many, plan#4's says nothing.  Once ``quill answer plan#2`` answers it, its line says
    nothing either."""
    tracker = FakeTracker()
    tracker.add(1)
    for task in (2, 3, 4):
        tracker.add_task(task, 1)
    _said(tracker, 2, DEVELOPER, "2026-10-05T14:00:00Z")
    _said(tracker, 2, DEVELOPER, "2026-10-05T14:30:00Z")
    _said(tracker, 3, DEVELOPER, "2026-10-05T15:00:00Z")
    _said(tracker, 4, DEVELOPER, "2026-10-05T13:00:00Z")
    _said(tracker, 4, APP_LOGIN, "2026-10-05T13:30:00Z", f"Done.\n\n{ANSWER_MARK}\n")

    def children() -> list[str]:
        capsys.readouterr()
        assert run(tracker, code, "show", "plan#1") == 0
        return [line for line in capsys.readouterr().out.splitlines()
                if line.startswith("  child: ")]

    assert children() == [
        "  child: plan#2 (task, open; done (git): no; 2 comments no answer of quill's has "
        "followed) card 2",
        "  child: plan#3 (task, open; done (git): no; 1 comment no answer of quill's has "
        "followed) card 3",
        "  child: plan#4 (task, open; done (git): no) card 4"]
    assert run(tracker, code, "answer", "plan#2", "--body-file", _text(tmp_path)) == 0
    assert children()[0] == "  child: plan#2 (task, open; done (git): no) card 2"


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
