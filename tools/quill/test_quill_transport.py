"""Every way a read of the tracker can fail ends in quill's one clean line (plan#1809 items 3
and 4): ``failed:``, naming the read that failed and its page or batch, exit 2.  Driven
through ``main`` over a real :class:`_tracker.Tracker` whose GitHub answers from a script,
so the whole path from the session to the printer runs and nothing but the network is
replaced; nothing here calls GitHub.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest
import requests

from tools.quill import quill
from tools.quill._export import snapshot
from tools.quill._github import CUT_OFF_RETRIES, GitHub, GitHubError, described
from tools.quill._recorded import recording
from tools.quill._tracker import Board, Tracker, board_of, comment_versions, milestone_nodes
from tools.quill.setup_tracker import APP_LOGIN, PLAN

#: GitHub's page for a request it cut off, as it answered quill's sync at 14:04 2026-10-26,
#: shortened: HTML over several lines, which the failure line must hold as one.
_CUT_OFF_PAGE = ("<!DOCTYPE html>\n<html>\n  <head>\n    <title>Unicorn! &middot; GitHub</title>\n"
                 "  </head>\n</html>\n")


class _Answer:
    """The slice of ``requests.Response`` quill reads: a status, a body and headers."""

    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        """Hold one answer: its status, its body's text and its headers (none by default)."""
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.headers = headers or {}

    def json(self):
        """The body decoded, raising as ``requests`` does for a body that is not JSON."""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as error:
            raise requests.JSONDecodeError(error.msg, error.doc, error.pos) from error


def _data(data) -> _Answer:
    """A GraphQL answer holding ``data``."""
    return _Answer(200, json.dumps({"data": data}))


class _Script:
    """A session answering each request with the next scripted answer, or raising the next
    scripted failure; it keeps every request sent, and one sent past the script fails the
    test (IndexError), so a request quill must not re-send is seen."""

    def __init__(self, *answers) -> None:
        """Hold the answers and failures, in order."""
        self.answers = list(answers)
        self.sent = []

    def request(self, method, url, **kwargs):
        """Keep the request; answer it with the next scripted answer, or raise the failure."""
        self.sent.append((method, url, kwargs.get("json")))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _github(session: _Script) -> GitHub:
    """A client over ``session`` that never waits."""
    return GitHub("token", session, sleep=lambda _seconds: None, clock=lambda: 1_000.0)


def _main(code, capsys, argv, *answers) -> tuple[int, str, str, _Script]:
    """Run ``quill argv`` on the code repository ``code`` over a real tracker whose GitHub
    answers ``answers`` in order: its exit status, what it printed, and the session."""
    session = _Script(*answers)
    github = _github(session)
    tracker = Tracker(github, Board(github, PLAN, "PVT_x"), APP_LOGIN)
    status = quill.main(argv, connect=lambda: tracker, root=code)
    out, err = capsys.readouterr()
    return status, out, err, session


#: Each transport failure the spec names (item 4), as the session meets it: GitHub's cut-off
#: answered to the first send and to every re-send; no answer in time; the connection reset;
#: an answer whose body is not JSON; an answer whose ``data`` is null; and an empty body.
_FAILURES = {
    "a 504 to the send and to every re-send":
        [_Answer(504, _CUT_OFF_PAGE)] * (CUT_OFF_RETRIES + 1),
    "a read timeout": [requests.ReadTimeout(
        "HTTPSConnectionPool(host='api.github.com', port=443): Read timed out. (read "
        "timeout=30)")],
    "a connection reset": [requests.ConnectionError(
        "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))")],
    "a body that is not JSON": [_Answer(200, _CUT_OFF_PAGE)],
    "an answer whose data is null": [_Answer(200, '{"data": null}')],
    "an empty body": [_Answer(200, "")],
}


@pytest.mark.parametrize("answers", _FAILURES.values(), ids=_FAILURES.keys())
def test_next_failing_on_its_first_read_names_the_open_cards_page_1(code, capsys, answers):
    """``next``'s first read of the tracker is the open cards' first page: each failure ends
    in ONE ``failed:`` line naming it, exit 2, nothing on stdout; and only a cut-off is
    re-sent (every scripted answer read, none past them)."""
    status, out, err, session = _main(code, capsys, ["next"], *answers)
    assert (status, out) == (2, "")
    assert err.startswith("failed: ") and err.count("\n") == 1
    assert err.endswith("; while reading the open cards, page 1\n")
    assert (len(session.sent), session.answers) == (len(answers), [])


@pytest.mark.parametrize("answers", _FAILURES.values(), ids=_FAILURES.keys())
def test_show_failing_on_its_read_of_the_card_names_the_batch(code, capsys, answers):
    """``show plan#5`` reads the card by its number first: each failure names that batch."""
    status, out, err, session = _main(code, capsys, ["show", "plan#5"], *answers)
    assert (status, out) == (2, "")
    assert err.startswith("failed: ") and err.count("\n") == 1
    assert err.endswith("; while reading the cards by number, batch 1 of 1, plan#5-plan#5\n")
    assert (len(session.sent), session.answers) == (len(answers), [])


def test_a_failure_on_a_later_page_names_that_page(code, capsys):
    """The open cards' first page says more follow; the second is cut off every time: the
    line names page 2, and the second page was asked after the first's cursor."""
    first = _data({"repository": {"issues": {
        "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"}}}})
    status, _, err, session = _main(code, capsys, ["next"], first,
                                    *_FAILURES["a 504 to the send and to every re-send"])
    assert status == 2
    assert err.endswith("; while reading the open cards, page 2\n")
    assert [sent[2]["variables"]["after"] for sent in session.sent] == [None] + ["cursor-1"] * 4


def test_a_cut_off_answer_is_one_line_holding_the_status_and_the_re_sends(code, capsys):
    """GitHub's HTML page spread over lines is held on the one line, after the status and how
    many times the read was re-sent."""
    _, _, err, _ = _main(code, capsys, ["next"], *_FAILURES["a 504 to the send and to every "
                                                           "re-send"])
    assert err.startswith(f"failed: POST https://api.github.com/graphql -> 504 after "
                          f"{CUT_OFF_RETRIES} re-sends: <!DOCTYPE html> <html> <head> <title>")


def _recorded_card(number: int) -> dict:
    """Card ``number``'s node as the recorded read by number holds it."""
    for exchange in recording("tracker")["exchanges"]:
        found = ((exchange["answer"] or {}).get("data") or {}).get("repository") or {}
        if found.get(f"c{number}"):
            return copy.deepcopy(found[f"c{number}"])
    raise LookupError(number)


def test_a_write_github_cut_off_is_sent_once_and_its_line_says_to_read_it_back(
        code, capsys, tmp_path):
    """``comment plan#2``: the card is read, then its comment, a write, is answered 504; it is
    never re-sent (the script holds no second answer for it), and the line says why."""
    text = tmp_path / "comment.txt"
    text.write_text("a note\n", encoding="utf-8")
    status, out, err, session = _main(
        code, capsys, ["comment", "plan#2", "--body-file", str(text)],
        _data({"repository": {"c2": _recorded_card(2)}}), _Answer(504, _CUT_OFF_PAGE))
    assert (status, out) == (2, "")
    assert [method for method, _, _ in session.sent] == ["POST", "POST"]
    assert session.sent[1][1].endswith("/issues/2/comments")
    assert err.startswith("failed: POST https://api.github.com/repos/saltyreformed-labs/"
                          "shekel-plan/issues/2/comments -> 504: <!DOCTYPE html>")
    assert err.count("\n") == 1
    assert err.endswith("; a write is never re-sent, as GitHub may have done it before it cut "
                        "the answer off: read the tracker back before running the command "
                        "again\n")


def test_the_board_s_two_reads_name_themselves():
    """``board_of`` (every command's connect) reads the board by its title, then the
    repositories it is linked to: a failure of either names which."""
    with pytest.raises(GitHubError) as failed:
        board_of(_github(_Script(_Answer(200, ""))), PLAN)
    assert described(failed.value).endswith(f"; while reading the board titled "
                                            f"{PLAN.board_title!r}")
    titled = _data({"organization": {"id": "O", "projectsV2": {"nodes": [
        {"id": "PVT_x", "title": PLAN.board_title}]}}})
    with pytest.raises(requests.ConnectionError) as failed:
        board_of(_github(_Script(titled, requests.ConnectionError("reset"))), PLAN)
    assert described(failed.value) == (f"reset; while reading the repositories the board "
                                       f"titled {PLAN.board_title!r} is linked to")


def test_the_read_of_what_a_card_blocks_names_the_card():
    """``blocking`` (``show``, the landing check, the finding doors) names the card it read."""
    session = _Script(_Answer(200, '{"data": null}'))
    tracker = Tracker(_github(session), Board(None, PLAN, "PVT_x"), APP_LOGIN)
    with pytest.raises(GitHubError) as failed:
        tracker.blocking(7)
    assert described(failed.value).endswith("; while reading the cards plan#7 blocks")


@pytest.mark.parametrize("read, named", [
    (Tracker.marked, "the marked cards"),
    (lambda tracker: tracker.edits(5), "plan#5's saved versions"),
    (lambda tracker: tracker.comments(5), "plan#5's comments"),
    (lambda tracker: comment_versions(tracker.github, "IC_x"), "comment IC_x's saved versions"),
    (lambda tracker: milestone_nodes(tracker.github, PLAN), "the milestones"),
    (lambda tracker: tracker.board.order(), "the board's order"),
    (lambda tracker: snapshot(tracker, datetime(2026, 10, 26, tzinfo=UTC)),
     "every card, for the backup"),
], ids=["marked", "saved versions", "comments", "comment versions", "milestones", "board",
        "backup"])
def test_every_paged_read_names_itself(read, named):
    """Each walk of a connection quill takes (``_github.paged``'s every caller) names its own
    read in a failure."""
    session = _Script(_Answer(200, ""))
    github = _github(session)
    with pytest.raises(GitHubError) as failed:
        read(Tracker(github, Board(github, PLAN, "PVT_x"), APP_LOGIN))
    assert described(failed.value).endswith(f"; while reading {named}, page 1")
