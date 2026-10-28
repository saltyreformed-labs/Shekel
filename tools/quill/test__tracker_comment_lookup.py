"""The read of many cards' comments in one request (:meth:`_tracker.Tracker.comments_of`, ruling
plan#1931), against the GitHub answer RECORDED for it.

``recorded/comment_lookup.json`` was kept 2026-10-27 21:58 EDT on the throwaway
``plan-views-demo``, with the developer's own token, by the session that kept
``recorded/show.json`` there, on the same scratch cards #244-#249, each deleted after
(plan#1925, ruling plan#1950; ``show.json`` was made again on #250-#255 for plan#1861's merge
of plan#1811, and this, which reads no card's fields, was kept as it was): one read of the
comments of the step, its task, the finding and a number nobody filed.  These tests replay it
(``_recorded.Replay``), and a test that needs an answer no scratch card can give (a hundred
comments, a null) starts from the recorded answer and changes the one field it is about, and
says so.  Split out of ``test__tracker.py``, which these would take past the 1,000 lines
``too-many-lines`` allows.  Nothing here calls GitHub.
"""
from __future__ import annotations

import copy

import pytest

from tools.quill._github import GitHub
from tools.quill._recorded import Replay, Scratch, recording
from tools.quill._tracker import Board, Comment, Tracker, TrackerError
from tools.quill.setup_tracker import APP_LOGIN, PLAN

#: The scratch cards the recording reads: the step S, the task T under it, each holding the
#: developer's one comment, and the finding F, holding none (the step, task and finding of the
#: show recording kept beside it; ``test__tracker.py``'s SS, ST and SF are theirs in the one
#: made again).
S, T, F = 244, 245, 246
#: A number nobody filed on plan-views-demo, which the recording asks for.
NOBODY = 99999
#: The developer's login, as GitHub named the author of each comment the recording read.
DEVELOPER = "SaltyReformed"


def _answer():
    """The recording's one answer, a copy to change."""
    (exchange,) = recording("comment_lookup")["exchanges"]
    return copy.deepcopy(exchange["answer"])


class _Answers:
    """A GitHub answering a read of many cards with ``lookup``, then each read of one card's
    comments with the next of ``pages``, keeping the ``after`` each was sent."""

    def __init__(self, lookup, pages):
        """Hold the answers."""
        self.lookup, self.pages = lookup, list(pages)
        self.lookups, self.afters = 0, []

    def graphql_lookup(self, _query, **_variables):
        """The lookup's answer."""
        self.lookups += 1
        return self.lookup

    def graphql(self, _query, **variables):
        """The next page of one card's comments."""
        self.afters.append(variables["after"])
        return self.pages.pop(0)


def _read(github) -> dict[int, list[Comment]]:
    """``comments_of`` over ``github`` for the cards the recording asked for."""
    return Tracker(github, Board(None, PLAN, "B"), APP_LOGIN).comments_of([S, T, F, NOBODY])


def test_the_comments_of_many_cards_are_read_in_one_request():
    """Replayed: the comments of the step, its task, the finding and a number nobody filed, in
    ONE request, whatever order they are asked in, each card's oldest first with its author,
    time and text as posted; the finding holds none; the number nobody filed, answered null
    beside a NOT_FOUND error, is absent.  Asked for no card, it sends nothing."""
    replay = Replay("comment_lookup")
    scratch = Scratch.from_json(recording("comment_lookup")["scratch"])
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, scratch.place, scratch.board), APP_LOGIN)
    assert tracker.comments_of([NOBODY, F, T, S]) == {
        S: [Comment(DEVELOPER, "2026-10-28T01:58:40Z", "A comment the recorder posts on step S.")],
        T: [Comment(DEVELOPER, "2026-10-28T01:58:41Z", "A comment the recorder posts on task T.")],
        F: []}
    assert len(replay.sent) == 1 and replay.unused() == 0
    assert not tracker.comments_of([]) and len(replay.sent) == 1


def test_a_card_whose_first_page_says_more_follow_is_read_again_whole():
    """No scratch card holds a hundred comments, so from the recorded answer: the step's first
    page says another follows, so its comments are read again page by page, in two pages built
    from that page's one comment (the second's author made null) -- the answer is the whole
    read's, never the first page's; the task, whose first page is its last, is read no more."""
    data = _answer()["data"]
    data["repository"][f"c{S}"]["comments"]["pageInfo"]["hasNextPage"] = True
    (node,) = data["repository"][f"c{S}"]["comments"]["nodes"]
    pages = [{"repository": {"issue": {"number": S, "comments": {
        "pageInfo": {"hasNextPage": more, "endCursor": "CURSOR" if more else None},
        "nodes": [comment]}}}}
        for more, comment in ((True, node), (False, {**node, "author": None}))]
    github = _Answers(data, pages)
    found = _read(github)
    assert github.lookups == 1 and github.afters == [None, "CURSOR"]
    assert [comment.author for comment in found[S]] == [DEVELOPER, None]
    assert [comment.body for comment in found[T]] == ["A comment the recorder posts on task T."]


@pytest.mark.parametrize(("card", "change", "message"), [
    (F, lambda held: held.update(comments=None), f"answered plan#{F}'s comments as null"),
    (S, lambda held: held["comments"]["nodes"].__setitem__(0, None),
     f"answered one of plan#{S}'s comments as null"),
])
def test_comments_github_answers_as_null_are_refused(card, change, message):
    """From the recorded answer, a card's list of comments made null, and one comment in a
    card's list made null: each is refused as the read of one card refuses it
    (``_github.connection_nodes``), never read as none or as a comment with no author."""
    data = _answer()["data"]
    change(data["repository"][f"c{card}"])
    with pytest.raises(TrackerError, match=message):
        _read(_Answers(data, []))
