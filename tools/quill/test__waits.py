"""``quill sync``'s pass over the weekly update's waits (:mod:`_waits`, rulings plan#3619,
plan#3620, plan#3722): a card filed for each pull request or issue the update told a thing on
while no card for it is open, and each later thing told there put on that card, over the
in-memory tracker (:class:`_fake.FakeTracker`, whose client :class:`_fake.FakeGitHub` answers the
pass's reads) and the ``code`` fixture's real git.

What the update told is as GitHub answered it on 2026-11-13 (PR 667, 668, 669 of the code
repository and #676): the App's comments word for word, its login ``shekel-updates``, and the
workflow bot's ``github-actions``.
"""
from __future__ import annotations

import pytest

from tools.quill import _waits
from tools.quill._fake import FakeTracker, assert_filed_high, run
from tools.quill._tracker import Comment
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
#: PR 667's lock refresh title.
_LOCK = "chore(deps): the Monday lock refresh moves 17 packages"


def _line(where, mark):
    """The line a card or quill's comment on one holds for the thing ``mark`` names, told on
    ``where``: spelled here apart from :func:`_waits.told_line`, so a test pins its form."""
    return f"<!-- quill: told on {where} --> {mark}"


#: PR 669's two lines, in its markers' order.
_TWO_LINES = tuple(_line("PR 669", mark) for mark in _TWO_MARKS)


def _red(head):
    """The queue's comment saying PR 667 is red at ``head``, as :data:`_RED` says it at its
    own, and its marker: a later Monday's."""
    mark = app.marker(f"red {head}")
    return _RED.replace("8518a3f40f86", head).replace(_RED_MARK, mark), mark


def _by(login, kind="Bot"):
    """The author GraphQL names: an actor of ``kind`` called ``login``."""
    return {"__typename": kind, "login": login}


def _comment(number, body, at, ident=1, by=(app.APP, "Bot")):
    """A comment on pull request ``number`` as :data:`_waits.PULLS` reads it, by the actor
    ``by`` names, its login and its kind (:func:`_by`), the App's bot unless a test says
    another; None for one since deleted, which GitHub answers as null."""
    return {"author": by and _by(*by), "body": body, "createdAt": at,
            "url": f"{_PULL.format(number)}#issuecomment-{ident}"}


def _pull(number, title, *comments):
    """An open pull request as :data:`_waits.PULLS` reads it, its comments on one page."""
    return {"number": number, "title": title,
            "comments": {"nodes": list(comments), "pageInfo": {"hasNextPage": False}}}


def _issue(number, title, login=app.ACTIONS, at="2026-11-16T14:20:00Z", kind="Bot"):
    """An open issue of the code repository as :data:`_waits.ISSUES` reads it, opened by an
    actor of ``kind`` called ``login``."""
    return {"number": number, "title": title, "author": _by(login, kind), "createdAt": at,
            "body": "@SaltyReformed the weekly update's run failed: https://run\n\n"
                    "- refresh: failure", "url": f"https://github.com/o/Shekel/issues/{number}"}


def _rumdl(tracker, number=669, ident=6694294810):
    """PR 669, or another pull request ``number`` moving the same capped requirements, open,
    holding its one comment of the App's, of two markers."""
    tracker.github.pulls.append(_pull(
        number, "chore(deps): rumdl from 0.2.78 to 0.3.2, waiting for a card",
        _comment(number, _TWO, "2026-11-06T20:05:54Z", ident=ident)))


def _lock(tracker, *heads):
    """PR 667 open, holding its red comment at 8518a3f40f86 and one more at each of ``heads``,
    a Monday apart, as the queue tells red once for each head."""
    tracker.github.pulls.append(_pull(667, _LOCK, _comment(667, _RED, "2026-11-09T23:09:14Z"), *(
        _comment(667, _red(head)[0], f"2026-11-{16 + 7 * week}T23:09:14Z", ident=2 + week)
        for week, head in enumerate(heads))))


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
    assert_filed_high(tracker, 2, title)
    assert tracker.board.items == [2, 1]
    words = _TWO.split("\n\n<!--", maxsplit=1)[0]
    assert tracker.bodies[2] == "\n".join([
        f"The weekly update told this on Shekel's PR 669 at 2026-11-06T20:05:54Z: "
        f"`{_PULL.format(669)}#issuecomment-6694294810`. The title there, then what was told:",
        "", "```text", "chore(deps): rumdl from 0.2.78 to 0.3.2, waiting for a card", "", words,
        "```", "",
        "`quill sync` filed this card for it (ruling plan#3619), holding Priority High at the "
        "top of the board (ruling plan#3620). The words above say what waits; the session that "
        "claims this card takes it, and sets the card's Effort.", "", *_TWO_LINES])
    _todo(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""
    assert len(tracker.writes) == 4


def test_what_any_card_holds_files_nothing_open_closed_or_by_hand(code, capsys):
    """A card holding the comment's lines at the end of its text, however it came to: filed by
    hand with them added (plan#2957's, at this card's landing), or closed since as not planned,
    or shipped, so a closed card still holds them: nothing is filed or put on it, and the
    every-card read is made."""
    for state in ({}, {"is_open": False, "state_reason": "NOT_PLANNED"},
                  {"is_open": False, "state_reason": "COMPLETED"}):
        tracker = FakeTracker()
        tracker.add(2957, body="Read rumdl's changelog.\n\n" + "\n".join(_TWO_LINES), **state)
        _todo(tracker, 2957)
        _rumdl(tracker)
        assert run(tracker, code, "sync") == 0
        assert capsys.readouterr().out == ""
        assert not tracker.writes
        assert _waits.every_text(tracker.place) in tracker.github.queries


def test_a_comment_with_one_thing_an_open_card_holds_and_one_more_is_put_on_it(code, capsys):
    """A comment holding a thing the card open for its pull request holds beside one it does
    not (a later major on a pull request a card already waits on) is put on that card, in a
    comment of quill's holding both lines; with the card closed, it is filed a card of its own
    holding both (ruling plan#3722)."""
    tracker = FakeTracker()
    tracker.add(5, body=_TWO_LINES[0])
    _todo(tracker, 5)
    _rumdl(tracker)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == (
        "put the update's PR 669, told at 2026-11-06T20:05:54Z, on plan#5, the card open for it "
        "(ruling plan#3722)\n")
    assert [write[:2] for write in tracker.writes] == [("comment", 5)]
    assert tracker.writes[0][2].endswith("\n\n" + "\n".join(_TWO_LINES))
    closed = FakeTracker()
    closed.add(5, body=_TWO_LINES[0], is_open=False, state_reason="COMPLETED")
    _rumdl(closed)
    assert run(closed, code, "sync") == 0
    assert "filed plan#6\n" in capsys.readouterr().out
    assert closed.bodies[6].endswith("\n\n" + "\n".join(_TWO_LINES))


def test_nothing_told_reads_no_card_and_files_nothing(code, capsys):
    """No open pull request holds a comment of the App's with a marker -- a person's comment
    holding one counts for nothing, one since deleted's, or a user's holding the App's slug as
    their login (:func:`tools.update.app.bot_of`), as an App comment holding none does -- so
    the pass reads no card's text and files nothing."""
    tracker = FakeTracker()
    tracker.github.pulls = [_pull(
        5, "feat: work",
        _comment(5, _RED, "2026-11-09T23:09:14Z", by=("SaltyReformed", "User")),
        _comment(5, _RED, "2026-11-09T23:09:14Z", by=(app.APP, "User")),
        _comment(5, _RED, "2026-11-09T23:09:14Z", by=None),
        _comment(5, "no marker", "2026-11-09T23:09:15Z"))]
    tracker.github.issues = [_issue(676, "A release is due: dev and main pin different "
                                         "versions")]
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""
    assert tracker.github.queries == [_waits.PULLS, _waits.ISSUES]


def test_an_unfinished_filing_is_finished_not_filed_again(code, capsys):
    """A card an earlier pass created for the comment, still marked: its Priority dropped and
    off the board, the pass cut short.  The pass finishes it, writing what it lacks, and files
    no other; ``sync``'s own REPORT of the unfinished filing, made before the pass, exits 1,
    and the next ``sync``, reading it finished, exits 0 and writes nothing."""
    tracker = FakeTracker()
    tracker.add(9, body="\n".join(_TWO_LINES), on_board=False, labels=("filing", "tooling"),
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
    _todo(tracker, 9)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""


def test_the_alerts_open_issue_is_filed_once_and_no_other_issue(code, capsys):
    """The alert's open issue, by the workflow's bot and titled as the alert titles one, is
    filed once, its card holding the marker made from its number; the release-due issue (the
    same bot), a stranger's issue titled as the alert's, and one by a user holding the bot's
    login (:func:`tools.update.app.bot_of`), are not."""
    tracker = FakeTracker()
    tracker.github.issues = [
        _issue(680, "The weekly update failed 2026-11-16"),
        _issue(676, "A release is due: dev and main pin different versions"),
        _issue(681, "The weekly update failed 2026-11-16", login="someone", kind="User"),
        _issue(682, "The weekly update failed 2026-11-16", kind="User")]
    title = "Shekel issue 680: The weekly update failed 2026-11-16"
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == _filed_lines(1, title, "issue 680",
                                                   "2026-11-16T14:20:00Z")
    assert tracker.bodies[1].endswith("\n\n" + _line("issue 680", app.marker("alert 680")))
    _todo(tracker, 1)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""


def test_two_things_told_are_filed_oldest_first_the_newest_on_top(code, capsys):
    """PR 667's red comment and PR 669's, read newest first: each filed, the older first, so
    the newer sits at the top."""
    tracker = FakeTracker()
    _lock(tracker)
    _rumdl(tracker)
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert out.index("PR 669") < out.index("PR 667")
    assert tracker.board.items == [2, 1]
    assert tracker.bodies[2].endswith("\n\n" + _line("PR 667", _RED_MARK))


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
    them whole and mentions no one; the title there is quoted in it too, above them, as a
    title can hold a mention or a fence as words can (finding 9 of plan#3613's review)."""
    said = "@SaltyReformed ```\nrun `gh pr merge 7 --merge`\n````\n\n" + app.marker("red x")
    told = _waits.Told("PR 7", "fix: ``````` for @someone", "link", said, "at",
                       (app.marker("red x"),))
    body = _waits.card_draft(told).body
    assert ("\n````````text\nfix: ``````` for @someone\n\n@SaltyReformed ```\n"
            "run `gh pr merge 7 --merge`\n````\n````````\n") in body
    assert body.count("@someone") == 1
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


def test_the_same_comment_on_two_pull_requests_files_two_cards(code, capsys):
    """Two pull requests moving the same capped requirements are each told the walk's same
    refusals, so their comments hold the same markers: each is filed a card of its own, and the
    card open for PR 669 holds nothing of PR 671's, nor takes it (finding 1 of plan#3613's
    review)."""
    tracker = FakeTracker()
    _rumdl(tracker)
    _rumdl(tracker, 671, ident=6694299999)
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out.count("filed plan#") == 2
    assert tracker.bodies[1].endswith("\n\n" + "\n".join(_TWO_LINES))
    assert tracker.bodies[2].endswith("\n\n" + "\n".join(
        _line("PR 671", mark) for mark in _TWO_MARKS))
    held = FakeTracker()
    held.add(2957, body="\n".join(_TWO_LINES))
    _todo(held, 2957)
    _rumdl(held)
    _rumdl(held, 671, ident=6694299999)
    assert run(held, code, "sync") == 0
    assert "filed plan#2958\n" in capsys.readouterr().out
    assert [write[:2] for write in held.writes if write[0] in ("create", "comment")] == [
        ("create", 2958)]


@pytest.mark.parametrize("text", [
    "\n".join(_TWO_MARKS),
    "The App's comments on PR 669 hold " + " and ".join(_TWO_MARKS) + ".",
    "\n".join(f"- `{line}`" for line in _TWO_LINES),
    "```\n" + "\n".join(_TWO_LINES) + "\n```",
    "Append:\n\n" + "\n".join("    " + line for line in _TWO_LINES),
    "\n".join(_TWO_LINES) + "\n\nAppend those lines to plan#2957.",
    "\n".join(f"<!-- quill: told on PR 669 --> {mark} {mark}" for mark in _TWO_MARKS),
], ids=["markers alone", "prose", "code spans", "fenced block", "indented block",
        "prose after", "two markers on a line"])
def test_a_line_or_marker_quoted_anywhere_but_the_end_holds_nothing(code, capsys, text):
    """A card quoting the queue's markers or the pass's lines anywhere but in the run of lines
    ending its text, as plan#3676's spec quoted all five of PR 667-669's (finding 4 of
    plan#3613's review), holds nothing: the comment is filed a card."""
    tracker = FakeTracker()
    tracker.add(3676, body=text)
    _todo(tracker, 3676)
    _rumdl(tracker)
    assert run(tracker, code, "sync") == 0
    assert "filed plan#3677\n" in capsys.readouterr().out
    assert not _waits.holds(text)


def test_the_lines_ending_a_text_are_what_it_holds():
    """:func:`_waits.holds` reads the run of lines ending a text, blank lines and a carriage
    return after each aside, each line's where and its one marker."""
    text = "Prose.\r\n\r\n" + "\r\n".join(_TWO_LINES) + "\r\n\r\n"
    assert _waits.holds(text) == {("PR 669", mark) for mark in _TWO_MARKS}
    assert _waits.told_line("PR 669", _TWO_MARKS[0]) == _TWO_LINES[0]


def test_a_later_red_goes_on_the_card_open_for_its_pull_request(code, capsys):
    """PR 667 told red on 11-09, whose card is open, then red again at the next Monday's head:
    the later red is put on that card in a comment of quill's, which mentions no one, and no
    card is filed; the next pass, reading that comment, writes nothing (ruling plan#3722)."""
    tracker = FakeTracker()
    tracker.add(2955, body="Land the lock refresh.\n\n" + _line("PR 667", _RED_MARK))
    _todo(tracker, 2955)
    _lock(tracker, "c0ffee123456")
    said, mark = _red("c0ffee123456")
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert capsys.readouterr().out == (
        "would put the update's PR 667, told at 2026-11-16T23:09:14Z, on plan#2955, the card "
        "open for it (ruling plan#3722)\n")
    assert not tracker.writes
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == (
        "put the update's PR 667, told at 2026-11-16T23:09:14Z, on plan#2955, the card open "
        "for it (ruling plan#3722)\n")
    words = said.split("\n\n<!--", maxsplit=1)[0]
    assert tracker.writes == [("comment", 2955, "\n".join([
        f"The weekly update told this later on Shekel's PR 667, at 2026-11-16T23:09:14Z: "
        f"`{_PULL.format(667)}#issuecomment-2`", "", "```text", words, "```", "",
        "`quill sync` put it on this card, the one open for PR 667, rather than filing another "
        "(ruling plan#3722).", "", _line("PR 667", mark)]))]
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""
    assert len(tracker.writes) == 1


def test_a_red_told_after_its_card_closed_is_filed_a_card_of_its_own(code, capsys):
    """PR 667's card closed, shipped or dropped, holding its first red and the next Monday's
    in quill's comment on it: the third Monday's red is filed a new card holding it alone, and
    neither earlier one is filed or put again (ruling plan#3722)."""
    first, second = _red("c0ffee123456")[1], _red("decade654321")[1]
    for reason in ("COMPLETED", "NOT_PLANNED"):
        tracker = FakeTracker()
        tracker.add(2955, body=_line("PR 667", _RED_MARK), is_open=False, state_reason=reason)
        tracker.notes[2955] = [Comment(tracker.app_login, "2026-11-17T02:00:00Z",
                                       "Later.\n\n" + _line("PR 667", first))]
        _lock(tracker, "c0ffee123456", "decade654321")
        assert run(tracker, code, "sync") == 0
        assert "filed plan#2956\n" in capsys.readouterr().out
        assert tracker.bodies[2956].endswith("\n\n" + _line("PR 667", second))
        assert [write[0] for write in tracker.writes].count("comment") == 0


def test_two_reds_in_one_pass_file_one_card_and_put_the_later_on_it(code, capsys):
    """PR 667's red at two heads, no card for it yet: the older is filed a card and the later
    put on that card, each said in a dry run first, which writes nothing (ruling plan#3722)."""
    tracker = FakeTracker()
    _lock(tracker, "c0ffee123456")
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert capsys.readouterr().out == (
        "would file the update's PR 667, told at 2026-11-09T23:09:14Z, which no card holds "
        f"(ruling plan#3619): {'Shekel PR 667: ' + _LOCK!r}\n"
        "would put the update's PR 667, told at 2026-11-16T23:09:14Z, on the card this pass "
        "would file for it above (ruling plan#3722)\n")
    assert not tracker.writes
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out.endswith(
        "put the update's PR 667, told at 2026-11-16T23:09:14Z, on plan#1, the card open for it "
        "(ruling plan#3722)\n")
    assert [write[:2] for write in tracker.writes if write[0] in ("create", "comment")] == [
        ("create", 1), ("comment", 1)]
    assert tracker.writes[-1][2].endswith(
        "\n\n" + _line("PR 667", _red("c0ffee123456")[1]))


def test_only_quills_comments_hold_and_the_lowest_open_card_takes_a_later_thing(code, capsys):
    """A person's comment holding the later red's line holds nothing, so that red is put on
    the card open for PR 667; of two open for it, the lowest numbered, one closed aside."""
    tracker = FakeTracker()
    for number in (2955, 2990):
        tracker.add(number, body=_line("PR 667", _RED_MARK))
    tracker.add(2950, body=_line("PR 667", _RED_MARK), is_open=False, state_reason="COMPLETED")
    _todo(tracker, 2955, 2990)
    tracker.notes[2955] = [Comment("SaltyReformed", "2026-11-17T02:00:00Z",
                                   _line("PR 667", _red("c0ffee123456")[1]))]
    _lock(tracker, "c0ffee123456")
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == (
        "put the update's PR 667, told at 2026-11-16T23:09:14Z, on plan#2955, the card open "
        "for it (ruling plan#3722)\n")
    assert [write[:2] for write in tracker.writes] == [("comment", 2955)]
