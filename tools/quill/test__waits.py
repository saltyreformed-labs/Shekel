"""``quill sync``'s pass over the weekly update's waits (:mod:`_waits`, rulings plan#3619,
plan#3620): a card filed for each thing the update told that no card holds, over the in-memory
tracker (:class:`_fake.FakeTracker`, whose client :class:`_fake.FakeGitHub` answers the pass's
reads) and the ``code`` fixture's real git.

What the update told is as GitHub answered it on 2026-11-13 (PR 667, 668, 669 of the code
repository and #676): the App's comments word for word, its login ``shekel-updates``, and the
workflow bot's ``github-actions``.
"""
from __future__ import annotations

import pytest

from tools.quill import _waits
from tools.quill._fake import FakeTracker, run
from tools.quill.setup_tracker import FILING
from tools.update import app

#: PR 669's one comment of the App's, word for word as read 2026-11-13: two clauses, two
#: markers.
_TWO = ("@SaltyReformed This pull request waits for a card: pyproject.toml pins no version; "
        "uv.lock changes shekel's metadata. A session reads the changelog, claims a card and "
        "takes its change; until then the daily update queue leaves it (rulings plan#2351, "
        "plan#2450).\n\n"
        "<!-- update: mention "
        "0ff52c4bc4f08733209079a4eed33cd6fcd40bc7d535fbd85137bd8046f46ea1 -->\n"
        "<!-- update: mention "
        "feff69fc96efd06871a0164289534de4b2576f5435b8d90eefc1b1fe8c441a4f -->\n")
#: Its two markers, in its order.
_TWO_MARKS = tuple(line for line in _TWO.splitlines() if line.startswith("<!--"))
#: PR 667's second comment of the App's, word for word as read 2026-11-13: one marker.
_RED = ("@SaltyReformed This pull request moves pinned versions alone, but its checks at "
        "8518a3f40f86 are FAILURE. The daily update queue puts it in the merge queue once every "
        "check at that commit passes, and judges it afresh at a new one (ruling plan#2351).\n\n"
        "<!-- update: mention 39f6a15f0d6ce61b18928a4b67ec57dc15cfbe4857d9ffa14614650b2cb24547 -->"
        "\n")
_RED_MARK = _RED.splitlines()[-1]
#: Where the code repository's pull requests live.
_PULL = "https://github.com/saltyreformed-labs/Shekel/pull/{}"


def _comment(number, body, at, login=app.APP, ident=1):
    """A comment on pull request ``number`` as :data:`_waits.PULLS` reads it."""
    return {"author": {"login": login}, "body": body, "createdAt": at,
            "url": f"{_PULL.format(number)}#issuecomment-{ident}"}


def _pull(number, title, *comments):
    """An open pull request as :data:`_waits.PULLS` reads it, its comments on one page."""
    return {"number": number, "title": title,
            "comments": {"nodes": list(comments), "pageInfo": {"hasNextPage": False}}}


def _issue(number, title, login=app.ACTIONS, at="2026-11-16T14:20:00Z"):
    """An open issue of the code repository as :data:`_waits.ISSUES` reads it."""
    return {"number": number, "title": title, "author": {"login": login}, "createdAt": at,
            "body": "@SaltyReformed the weekly update's run failed: https://run\n\n"
                    "- refresh: failure", "url": f"https://github.com/o/Shekel/issues/{number}"}


def _rumdl(tracker):
    """PR 669 open, holding its one comment of the App's, of two markers."""
    tracker.github.pulls = [_pull(
        669, "chore(deps): rumdl from 0.2.78 to 0.3.2, waiting for a card",
        _comment(669, _TWO, "2026-11-06T20:05:54Z", ident=6694294810))]


def _todo(tracker, *numbers):
    """The board shows each card of ``numbers`` Todo already, the Status ``sync`` writes of a
    card open, unclaimed and not done (``_status``), so a run prints the pass alone."""
    tracker.board.status.update(dict.fromkeys(numbers, "Todo"))


def _filed_lines(number, title, where, at, finish=""):
    """What a pass prints for a card it files: its own line, then the filing's."""
    return (f"file the update's {where}, told at {at}, which no card holds (ruling plan#3619): "
            f"{title!r}\nfiled plan#{number}\n  plan#{number} [step, tooling] {title}\n"
            f"{finish}  board: put at the top (ruling plan#3620)\n"
            f"  unmarked: plan#{number}'s filing is finished\n")


def test_a_comment_no_card_holds_is_filed_once_high_at_the_top(code, capsys):
    """PR 669's comment, whose markers no card holds: a dry run says so and writes nothing; the
    pass files one step in the tooling arc holding Priority High and no Effort, its two markers
    in its text and the comment's words in a code block, put at the top of the board before its
    mark comes off; the next pass, reading the card's text, files nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    _todo(tracker, 1)
    _rumdl(tracker)
    title = "Shekel PR 669: chore(deps): rumdl from 0.2.78 to 0.3.2, waiting for a card"
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert capsys.readouterr().out == (
        f"would file the update's PR 669, told at 2026-11-06T20:05:54Z, which no card holds "
        f"(ruling plan#3619): {title!r}\n")
    assert not tracker.writes
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == _filed_lines(2, title, "PR 669", "2026-11-06T20:05:54Z")
    assert tracker.writes == [
        ("create", 2, "step", title, ("filing", "tooling"), "tooling"),
        ("board_add", 2), ("board_place", 2, None), ("unmark", 2)]
    card = tracker.cards_by_number[2]
    assert (card.priority, card.effort, card.labels, card.is_open) == (
        "High", None, ("tooling",), True)
    assert tracker.board.items == [2, 1]
    words = _TWO.split("\n\n<!--", maxsplit=1)[0]
    assert tracker.bodies[2] == "\n".join([
        f"The weekly update told this on Shekel's PR 669, \"chore(deps): rumdl from 0.2.78 to "
        f"0.3.2, waiting for a card\", at 2026-11-06T20:05:54Z: "
        f"`{_PULL.format(669)}#issuecomment-6694294810`", "", "```text", words, "```", "",
        "`quill sync` filed this card for it (ruling plan#3619), holding Priority High at the "
        "top of the board (ruling plan#3620). The words above say what waits; the session that "
        "claims this card takes it, and sets the card's Effort.", "", *_TWO_MARKS])
    _todo(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""
    assert len(tracker.writes) == 4


def test_a_marker_any_card_holds_files_nothing_open_closed_or_by_hand(code, capsys):
    """A card holding the comment's markers in its text, however it came to: filed by hand
    with them added (plan#2957's, at this card's landing), or closed since as not planned, or
    shipped: nothing is filed, and the every-card read is made."""
    for state in ({}, {"is_open": False, "state_reason": "NOT_PLANNED"},
                  {"is_open": False, "state_reason": "COMPLETED"}):
        tracker = FakeTracker()
        tracker.add(2957, body="Read rumdl's changelog.\n\n" + "\n".join(_TWO_MARKS), **state)
        _todo(tracker, 2957)
        _rumdl(tracker)
        assert run(tracker, code, "sync") == 0
        assert capsys.readouterr().out == ""
        assert not tracker.writes
        assert _waits.every_text(tracker.place) in tracker.github.queries


def test_a_comment_with_one_marker_no_card_holds_is_filed(code, capsys):
    """A comment holding a marker a card holds beside one no card holds (a later major on a
    pull request a card already waits on) is filed, its card holding both."""
    tracker = FakeTracker()
    tracker.add(5, body=_TWO_MARKS[0])
    _todo(tracker, 5)
    _rumdl(tracker)
    assert run(tracker, code, "sync") == 0
    assert "filed plan#6\n" in capsys.readouterr().out
    assert tracker.bodies[6].endswith("\n".join(_TWO_MARKS))


def test_nothing_told_reads_no_card_and_files_nothing(code, capsys):
    """No open pull request holds a comment of the App's with a marker -- a person's comment
    holding one counts for nothing, as an App comment holding none does -- so the pass reads
    no card's text and files nothing."""
    tracker = FakeTracker()
    tracker.github.pulls = [_pull(5, "feat: work", _comment(5, _RED, "2026-11-09T23:09:14Z",
                                                             login="SaltyReformed"),
                                  _comment(5, "no marker", "2026-11-09T23:09:15Z"))]
    tracker.github.issues = [_issue(676, "A release is due: dev and main pin different "
                                         "versions")]
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""
    assert tracker.github.queries == [_waits.PULLS, _waits.ISSUES]


def test_an_unfinished_filing_is_finished_not_filed_again(code, capsys):
    """A card an earlier pass created for the comment, still marked: its Priority dropped and
    off the board, the pass cut short.  The pass finishes it, writing what it lacks, and files
    no other; ``sync``'s own REPORT of the unfinished filing, made before the pass, exits 1."""
    tracker = FakeTracker()
    tracker.add(9, body="\n".join(_TWO_MARKS), on_board=False, labels=("filing", "tooling"),
                arc="tooling", title="Shekel PR 669: earlier")
    _rumdl(tracker)
    assert run(tracker, code, "sync") == 1
    out = capsys.readouterr().out
    assert out.startswith("REPORT: plan#9's filing has not finished, so it is never offered ")
    assert out.endswith(
        "finish the filing of plan#9, which holds the update's PR 669, told at "
        "2026-11-06T20:05:54Z\nfinishing plan#9 [step, tooling] Shekel PR 669: earlier: its "
        f"filing has not finished (it is still marked {FILING!r}), so this command finishes it "
        "rather than filing another (R-BAL186, R-BAL202)\n  its Priority: 'High'\n"
        "  board: put at the top (ruling plan#3620)\n  unmarked: plan#9's filing is finished\n")
    assert tracker.writes == [
        ("set_field", 9, "Priority", "High"), ("board_add", 9), ("board_place", 9, None),
        ("unmark", 9)]
    assert 10 not in tracker.cards_by_number


def test_the_alerts_open_issue_is_filed_once_and_no_other_issue(code, capsys):
    """The alert's open issue, by the workflow's bot and titled as the alert titles one, is
    filed once, its card holding the marker made from its number; the release-due issue (the
    same bot) and a stranger's issue titled as the alert's are not."""
    tracker = FakeTracker()
    tracker.github.issues = [
        _issue(680, "The weekly update failed 2026-11-16"),
        _issue(676, "A release is due: dev and main pin different versions"),
        _issue(681, "The weekly update failed 2026-11-16", login="someone")]
    title = "Shekel issue 680: The weekly update failed 2026-11-16"
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == _filed_lines(1, title, "issue 680",
                                                   "2026-11-16T14:20:00Z")
    assert tracker.bodies[1].endswith("\n\n" + app.marker("alert 680"))
    _todo(tracker, 1)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""


def test_two_things_told_are_filed_oldest_first_the_newest_on_top(code, capsys):
    """PR 667's red comment and PR 669's, read newest first: each filed, the older first, so
    the newer sits at the top."""
    tracker = FakeTracker()
    _rumdl(tracker)
    tracker.github.pulls.insert(0, _pull(
        667, "chore(deps): the Monday lock refresh moves 17 packages",
        _comment(667, _RED, "2026-11-09T23:09:14Z")))
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert out.index("PR 669") < out.index("PR 667")
    assert tracker.board.items == [2, 1]
    assert tracker.bodies[2].endswith(_RED_MARK)


def test_a_card_the_check_refuses_is_a_report_and_nothing_is_written(code, capsys,
                                                                    monkeypatch):
    """Every card quill files passes the check first: one it refuses (here an arc quill does
    not know) is filed no card, and the pass prints a REPORT, which makes ``sync`` exit 1."""
    monkeypatch.setattr(_waits, "ARC", "nowhere")
    tracker = FakeTracker()
    _rumdl(tracker)
    assert run(tracker, code, "sync") == 1
    out = capsys.readouterr().out
    assert out.startswith("REPORT: the update's PR 669, told at 2026-11-06T20:05:54Z, is filed "
                          "no card: it holds one arc in its Arc field")
    assert not tracker.writes


@pytest.mark.parametrize(("title", "cut"), [
    ("x" * 85, "Shekel PR 7: " + "x" * 85),
    ("x" * 87, "Shekel PR 7: " + "x" * 87),
    ("x" * 88, "Shekel PR 7: " + "x" * 84 + "..."),
    ("word " * 30, "Shekel PR 7: " + " ".join(["word"] * 17) + "..."),
    ("word " * 16 + "x" * 9, "Shekel PR 7: " + " ".join(["word"] * 16) + "..."),
])
def test_a_title_is_cut_at_a_word_to_the_cap(title, cut):
    """The card's title is where it was told and that one's title, at most 100 characters:
    cut at the last of that title's words that fits, or inside its one word where none does,
    with three dots."""
    told = _waits.Told("PR 7", title, "link", "said", "at", ("m",))
    assert _waits.card_draft(told).title == cut
    assert len(cut) <= 100


def test_the_words_are_fenced_by_more_backticks_than_they_hold():
    """Words holding a fence of their own are quoted in a longer one, so the code block holds
    them whole and mentions no one."""
    said = "@SaltyReformed ```\nrun `gh pr merge 7 --merge`\n````\n\n" + app.marker("red x")
    told = _waits.Told("PR 7", "t", "link", said, "at", (app.marker("red x"),))
    body = _waits.card_draft(told).body
    assert "\n`````text\n@SaltyReformed ```\nrun `gh pr merge 7 --merge`\n````\n`````\n" in body
    assert body.count(app.marker("red x")) == 1


class _Pages:
    """GitHub's GraphQL answering :func:`_waits.told`'s reads a page at a time: two pages of open
    pull requests, the second one's comments on two pages, and no issue; each query and its
    variables logged."""

    def __init__(self):
        """Nothing read yet."""
        self.calls = []

    def graphql(self, query, **variables):
        """The page of ``query`` that ``variables`` names."""
        self.calls.append((query, variables))
        more, last = {"hasNextPage": True, "endCursor": "C1"}, {"hasNextPage": False,
                                                                 "endCursor": None}
        if query == _waits.ISSUES:
            return {"repository": {"issues": {"nodes": [], "pageInfo": last}}}
        if query == _waits.PULLS and variables["after"] is None:
            return {"repository": {"pullRequests": {"pageInfo": more, "nodes": [
                _pull(667, "lock", _comment(667, _RED, "2026-11-09T23:09:14Z"))]}}}
        if query == _waits.PULLS:
            first = {"nodes": [_comment(669, "first page", "2026-11-01T00:00:00Z")],
                     "pageInfo": {"hasNextPage": True}}
            return {"repository": {"pullRequests": {"pageInfo": last, "nodes": [
                {"number": 669, "title": "rumdl", "comments": first}]}}}
        page = ({"nodes": [_comment(669, "first page", "2026-11-01T00:00:00Z")], "pageInfo": more}
                if variables["after"] is None else
                {"nodes": [_comment(669, _TWO, "2026-11-06T20:05:54Z")], "pageInfo": last})
        return {"repository": {"pullRequest": {"comments": page}}}


def test_told_reads_every_page_and_a_long_pull_requests_comments_whole():
    """The open pull requests are read page by page, and a pull request whose first page of
    comments says another follows is read again whole, page by page, by its number: PR 669's
    comment on its second page is told."""
    github = _Pages()
    found = _waits.told(github)
    assert [(item.where, item.marks) for item in found] == [("PR 669", _TWO_MARKS),
                                                           ("PR 667", (_RED_MARK,))]
    owner, name = _waits.CODE
    assert [(query, variables) for query, variables in github.calls
            if query == _waits.COMMENTS] == [
        (_waits.COMMENTS, {"owner": owner, "name": name, "number": 669, "after": None}),
        (_waits.COMMENTS, {"owner": owner, "name": name, "number": 669, "after": "C1"})]
    assert (owner, name) == ("saltyreformed-labs", "Shekel")


def test_two_comments_of_the_same_markers_file_one_card(code, capsys):
    """Two runs of the queue racing can each write a comment of the same markers, neither
    reading the other's: the pass files one card for the older, and the newer, whose markers
    that card now holds, none."""
    tracker = FakeTracker()
    _rumdl(tracker)
    tracker.github.pulls[0]["comments"]["nodes"].append(
        _comment(669, _TWO, "2026-11-06T20:05:59Z", ident=6694294999))
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out.count("filed plan#") == 1
    assert sorted(tracker.cards_by_number) == [1]


def test_every_cards_text_is_read_open_and_closed():
    """The read of which markers cards hold lists closed cards too, so a card shipped or
    dropped still holds what it was filed for."""
    assert "states: [OPEN, CLOSED]" in _waits.every_text(FakeTracker.place)
