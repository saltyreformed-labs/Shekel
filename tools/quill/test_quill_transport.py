"""Every way a read of the tracker can fail ends in quill's one clean line (plan#1809 items 3
and 4): ``failed:``, naming the read that failed and its page or batch, exit 2.  Driven
through ``main`` over a real :class:`_tracker.Tracker` whose GitHub answers from a script,
so the whole path from the session to the printer runs and nothing but the network is
replaced; nothing here calls GitHub.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime

import pytest
import requests

from tools.quill import _tracker, landing, quill, setup_tracker
from tools.quill._queries import marked_cards, open_cards
from tools.quill._draft import Draft
from tools.quill._export import snapshot
from tools.quill._github import (
    CUT_OFF_RETRIES,
    GitHub,
    GitHubError,
    described,
    installation_token,
)
from tools.quill._recorded import recording
from tools.quill._tracker import (
    Board,
    CardReader,
    Tracker,
    board_of,
    comment_versions,
    milestone_nodes,
)
from tools.quill.setup_tracker import APP_LOGIN, PLAN, Report, check_repository

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


def _tracker_over(github: GitHub) -> Tracker:
    """A real tracker over ``github``."""
    return Tracker(github, Board(github, PLAN, "PVT_x"), APP_LOGIN)


#: Every door that reads what GitHub answered a WRITE (plan#1812's class: the answer reaching
#: ``.get`` as None), sending through a session, each with the answers before its write's:
#: filing a card, moving it to a milestone, changing its kind, minting the App's token, and
#: the tracker repository's creation and its settings' correction.
_WRITES_READ = {
    "create": (lambda session: _tracker_over(_github(session)).create(
        Draft("step", "t", "b", ("balance",), None)), []),
    "set_milestone": (lambda session: _tracker_over(_github(session)).set_milestone(5, 2), []),
    "retype": (lambda session: _tracker_over(_github(session)).retype(5, "finding"), []),
    "installation_token": (lambda session: installation_token(
        7, "jwt", repository=PLAN.name, session=session), []),
    "repository created": (lambda session: check_repository(_github(session), True, Report()),
                           [_Answer(404, '{"message": "Not Found"}')]),
    "repository settings": (lambda session: check_repository(_github(session), True, Report()),
                            [_Answer(200, "{}")]),
}


@pytest.mark.parametrize("write, before", _WRITES_READ.values(), ids=_WRITES_READ.keys())
def test_a_write_answered_with_an_empty_body_is_refused_not_a_traceback(write, before):
    """plan#1812: a 2xx write answered with no body reached its caller as None, and the
    caller's ``.get`` raised AttributeError, a traceback that hid that GitHub had done the
    write.  The one door refuses it, naming the write and saying to read the tracker back;
    the write is sent once."""
    session = _Script(*before, _Answer(201, ""))
    with pytest.raises(GitHubError) as refused:
        write(session)
    assert re.fullmatch(r"(POST|PATCH) \S+ -> 201 with an empty body; GitHub answered the "
                        r"write, which quill never re-sends: read the tracker back before "
                        r"running the command again", str(refused.value))
    assert (len(session.sent), session.answers) == (len(before) + 1, [])


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


@pytest.mark.parametrize("listing", [open_cards, marked_cards])
def test_a_listing_of_cards_asks_25_a_page(listing):
    """plan#1809 item 1: the open listing at 100 a page was cut off at GitHub's limit, and
    answered at 25 in about a second (``_queries._LISTING_PAGE``, its measurement); the
    marked listing reads the same fields."""
    assert "issues(first: 25, after: $after" in listing(PLAN)


def test_cards_read_by_number_are_asked_25_a_batch_and_a_failure_names_its_batch():
    """plan#1809 item 1: sixty numbers are three reads of at most 25 (``_tracker._LOOKUP_BATCH``,
    its measurement); the third, cut off, is named with the cards it asked."""
    session = _Script(_data({"repository": {f"c{n}": None for n in range(1, 26)}}),
                      _data({"repository": {f"c{n}": None for n in range(26, 51)}}),
                      _Answer(200, '{"data": null}'))
    with pytest.raises(GitHubError) as failed:
        CardReader(_github(session), PLAN).cards(range(1, 61))
    asked = [re.findall(r"c(\d+): issue", body["query"]) for _, _, body in session.sent]
    assert [(batch[0], batch[-1], len(batch)) for batch in asked] == [
        ("1", "25", 25), ("26", "50", 25), ("51", "60", 10)]
    assert described(failed.value).endswith(
        "; while reading the cards by number, batch 3 of 3, plan#51-plan#60")


#: What ``requests`` raises when GitHub does not answer in time, as it words it.
_TIMEOUT = ("HTTPSConnectionPool(host='api.github.com', port=443): Read timed out. (read "
            "timeout=30)")


@pytest.mark.parametrize("read, named", [
    (lambda tracker: tracker.body(5), "plan#5's body"),
    (lambda tracker: tracker.find_titles("x"), "the title search for 'x', page 1"),
], ids=["body", "find_titles"])
def test_a_read_that_is_no_walk_names_itself_when_github_does_not_answer(read, named):
    """Review s56 M3: ``requests``' own message names the host, never the path, so a read
    outside :func:`_github.reading` failed naming nothing.  A read's failure carries no
    note of a write."""
    with pytest.raises(requests.ReadTimeout) as failed:
        read(_tracker_over(_github(_Script(requests.ReadTimeout(_TIMEOUT)))))
    assert described(failed.value) == f"{_TIMEOUT}; while reading {named}"


_APP = _Answer(200, json.dumps({"slug": APP_LOGIN}))
_INSTALLATION = _Answer(200, '{"id": 7}')


@pytest.mark.parametrize("before, named", [
    ([], "quill's App"),
    ([_APP], f"quill's App's installation on {PLAN.owner}"),
    ([_APP, _INSTALLATION], f"quill's App's token for {PLAN.full_name}"),
], ids=["app", "installation", "token"])
def test_connect_s_three_requests_name_themselves(monkeypatch, before, named):
    """Every command's first three requests, before the board's two reads: each, unanswered,
    names itself."""
    monkeypatch.setattr(_tracker, "app_credentials", lambda: ("Iv23client", b"pem"))
    monkeypatch.setattr(_tracker, "app_jwt", lambda *_: "jwt")
    session = _Script(*before, requests.ReadTimeout(_TIMEOUT))
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(requests.ReadTimeout) as failed:
        Tracker.connect(PLAN)
    assert described(failed.value).endswith(f"; while reading {named}")
    assert (len(session.sent), session.answers) == (len(before) + 1, [])


def test_the_landing_check_s_read_of_the_pull_request_names_itself(monkeypatch):
    """CI's landing check reads the pull request it judges first: unanswered, it says so."""
    monkeypatch.setenv(landing.PULL_TOKEN_VARIABLE, "t")
    monkeypatch.setenv(landing.REPOSITORY_VARIABLE, "o/r")
    monkeypatch.setattr(requests, "Session", lambda: _Script(requests.ReadTimeout(_TIMEOUT)))
    with pytest.raises(requests.ReadTimeout) as failed:
        landing.pull_from_environment(7)
    assert described(failed.value) == f"{_TIMEOUT}; while reading pull request #7 of o/r"


def test_a_write_github_did_not_answer_says_to_read_the_tracker_back(code, capsys, tmp_path):
    """Review s56 M3: a write unanswered in time may have been done, as one cut off may: its
    line names the write, says it is never re-sent and to read the tracker back."""
    text = tmp_path / "comment.txt"
    text.write_text("a note\n", encoding="utf-8")
    status, out, err, session = _main(
        code, capsys, ["comment", "plan#2", "--body-file", str(text)],
        _data({"repository": {"c2": _recorded_card(2)}}), requests.ReadTimeout(_TIMEOUT))
    assert (status, out, len(session.sent)) == (2, "", 2)
    assert err == (f"failed: {_TIMEOUT}; while writing POST https://api.github.com/repos/"
                   "saltyreformed-labs/shekel-plan/issues/2/comments, which is never re-sent, "
                   "as GitHub may have done it before the answer was lost: read the tracker "
                   "back before running the command again\n")


def test_setup_tracker_prints_a_failure_as_one_line_and_a_timeout_is_no_traceback(
        monkeypatch, capsys):
    """Review s56 L6: the configuration check printed GitHub's error raw and let a timeout
    out as a traceback; both now end in the one ``failed:`` line, exit 2."""
    monkeypatch.setattr(setup_tracker, "user_token", lambda: "t")
    for failure in (requests.ReadTimeout(_TIMEOUT), _Answer(500, "<html>\n</html>")):
        session = _Script(failure)
        monkeypatch.setattr(setup_tracker, "GitHub", lambda _token, used=session: _github(used))
        assert setup_tracker.main([]) == 2
        _, err = capsys.readouterr()
        assert err.startswith("failed: ") and err.count("\n") == 1
    assert err == f"failed: GET https://api.github.com{PLAN.path} -> 500: <html> </html>\n"
