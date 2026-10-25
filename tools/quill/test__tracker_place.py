"""A tracker's place, its board's fence, the whole-card listing, the numbering probe and the
paged title search (X-cx L7), against scripted GitHub answers; nothing calls GitHub.

No recording holds these reads yet: the rehearsal tracker they are for does not exist,
and the real tracker's own reads are kept on the real tracker's scratch cards only
(``_recorded``).  So, as ``test__tracker.py`` does for an answer no scratch card can give,
each test starts from a RECORDED node where one exists and changes only what it is
about, and says so; the real tracker's query text staying byte-identical is graded by
``test__tracker.py``'s replays, which match every request word for word.
"""
from __future__ import annotations

import copy
import re

import pytest

from tools.quill import _tracker, quill
from tools.quill._fake import FakeTracker
from tools.quill._github import GitHubError
from tools.quill._recorded import recording
from tools.quill._tracker import (
    BOARD_ADD,
    BOARD_AFTER,
    BOARD_REMOVE,
    BOARD_TOP,
    Board,
    Comment,
    NumberState,
    Numbering,
    Tracker,
    TrackerError,
    board_of,
    card_from,
    numbering,
)
from tools.quill.setup_tracker import PLAN, REHEARSAL

APP = "shekel-quill"


def _recorded_open_node(number):
    """Card ``number``'s node as the recorded read of every open card holds it."""
    for exchange in recording("tracker")["exchanges"]:
        query = ((exchange["body"] or {}).get("query") or "")
        if "states: [OPEN]" in query:
            nodes = exchange["answer"]["data"]["repository"]["issues"]["nodes"]
            return copy.deepcopy(next(node for node in nodes if node["number"] == number))
    raise LookupError(number)


class _Scripted:
    """A GitHub whose every answer a test scripts: ``rest`` by ``(method, path)``, GraphQL
    by a callable of the query and its variables.  It keeps every call it is sent."""

    def __init__(self, rest=None, graphql=None):
        """Hold the answers (an exception is raised, not answered)."""
        self.answers = rest or {}
        self.answer_graphql = graphql
        self.sent = []

    def rest(self, method, path, body=None):
        """Keep the call; answer it, or raise the scripted error."""
        self.sent.append((method, path, body))
        answer = self.answers[(method, path)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def graphql(self, query, **variables):
        """Keep the call; answer from the script."""
        self.sent.append(("GRAPHQL", query, variables))
        return self.answer_graphql(query, **variables)

    graphql_lookup = graphql


# -- the place --------------------------------------------------------------

def test_the_rehearsal_tracker_is_another_repository_whose_name_holds_the_real_ones():
    """The hazard every comparison of a place's names is written against: the rehearsal's
    repository name BEGINS with the real one's, in the same organization."""
    assert REHEARSAL.name.startswith(PLAN.name) and REHEARSAL.owner == PLAN.owner
    assert REHEARSAL.full_name != PLAN.full_name and REHEARSAL.path.startswith(PLAN.path)
    assert REHEARSAL.board_title != PLAN.board_title


def _names_in(text):
    """Every ``owner``/``name`` pair a query's ``repository(...)`` names."""
    return re.findall(r'repository\(owner: "([^"]+)", name: "([^"]+)"\)', text)


def test_every_read_of_a_rehearsal_tracker_names_the_rehearsal_repository_whole():
    """Each listing, lookup, history and search of a tracker on :data:`REHEARSAL` names the
    rehearsal repository as a whole token, and never the real one: a query built from the
    real tracker's constants would name ``shekel-plan``, which a substring test would pass."""
    empty_page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}

    def answer(query, **_variables):
        if "userContentEdits" in query:
            return {"repository": {"issue": {
                "number": 5, "body": "", "createdAt": "t", "author": None,
                "userContentEdits": empty_page}}}
        if "comments(first: 100, after" in query:
            return {"repository": {"issue": {"number": 5, "comments": empty_page}}}
        if "issues(first" in query:
            return {"repository": {"issues": empty_page}}
        return {"repository": {"c5": None}}

    path = REHEARSAL.path
    search = ("/search/issues?q=repo%3Asaltyreformed-labs%2Fshekel-plan-rehearsal"
              "%20in%3Atitle%20%22x%22&per_page=100")
    github = _Scripted(
        rest={("GET", f"{path}/issues/5"): {"body": "b"},
              ("GET", search): {"total_count": 0, "incomplete_results": False, "items": []}},
        graphql=answer)
    tracker = Tracker(github, Board(github, REHEARSAL, "PVT_r"), APP)
    assert tracker.place is REHEARSAL
    tracker.open_cards()
    tracker.marked()
    tracker.all_cards()
    tracker.cards([5])
    tracker.body(5)
    tracker.edits(5)
    tracker.comments(5)
    tracker.find_titles("x")
    queries = [query for kind, query, _ in github.sent if kind == "GRAPHQL"]
    assert len(queries) == 6
    for query in queries:
        assert _names_in(query) == [(REHEARSAL.owner, REHEARSAL.name)], query
    assert [path for kind, path, _ in github.sent if kind == "GET"] == [
        f"{path}/issues/5", search]


def test_a_link_to_the_real_tracker_is_outside_a_rehearsal_card_and_the_reverse():
    """From card #2's recorded node (its parent #1, its board item): read on the real
    tracker, #1 is its parent; read on the rehearsal tracker, the same link names another
    repository, so it is carried apart (R-BAL188) and never read as the rehearsal's #1.
    And a link naming the rehearsal is outside a real card."""
    node = _recorded_open_node(2)
    assert node["parent"]["repository"]["nameWithOwner"] == PLAN.full_name
    assert card_from(node, PLAN, "board", APP).parent == 1
    rehearsal = card_from(node, REHEARSAL, "board", APP)
    assert rehearsal.parent is None
    assert [(link.what, link.issue) for link in rehearsal.outside] == [
        ("parent", f"{PLAN.full_name}#1")]
    node["parent"]["repository"]["nameWithOwner"] = REHEARSAL.full_name
    assert card_from(node, REHEARSAL, "board", APP).parent == 1
    assert card_from(node, PLAN, "board", APP).parent is None


# -- the board's fence ------------------------------------------------------

def test_every_board_write_names_the_board_it_was_resolved_for_and_no_other():
    """Each of the board's four writes is sent with ``$p`` the board's own id, whatever its
    caller passes: :meth:`Board._write` is the one door they share."""
    def answer(query, **_variables):
        if query == BOARD_ADD:
            return {"addProjectV2ItemById": {"item": {"id": "PVTI_9"}}}
        if query.lstrip().startswith("mutation"):
            return {}
        return {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                   "nodes": [{"id": "PVTI_9", "content": {
                                       "number": 9, "repository": {
                                           "nameWithOwner": REHEARSAL.full_name}}}]}}}

    github = _Scripted(graphql=answer)
    board = Board(github, REHEARSAL, "PVT_r", sleep=lambda _seconds: None)
    card = card_from(_recorded_open_node(2), REHEARSAL, "PVT_r", APP)
    assert board.add(card) == "PVTI_9"
    assert board.place("PVTI_9", None)
    board.place("PVTI_9", "PVTI_8")
    board.remove("PVTI_9")
    writes = [(query, variables) for _, query, variables in github.sent
              if query in (BOARD_ADD, BOARD_TOP, BOARD_AFTER, BOARD_REMOVE)]
    assert [query for query, _ in writes] == [BOARD_ADD, BOARD_TOP, BOARD_AFTER, BOARD_REMOVE]
    assert all(variables["p"] == "PVT_r" for _, variables in writes)
    assert board.order() == [(9, "PVTI_9")]


def _org(boards, linked):
    """A GitHub holding the organization's boards (``(id, title)``) and each board's linked
    repositories (``{id: [owner/name, ...]}``)."""
    def answer(query, **variables):
        if "projectsV2" in query:
            return {"organization": {"id": "O", "projectsV2": {"nodes": [
                {"id": ident, "title": title} for ident, title in boards]}}}
        names = linked[variables["id"]]
        return {"node": {"repositories": {"totalCount": len(names), "nodes": [
            {"nameWithOwner": name} for name in names]}}}
    return _Scripted(graphql=answer)


def test_the_board_is_the_one_titled_for_the_place_and_linked_to_its_repository():
    """Both trackers' boards live in one organization; each place finds its own by title,
    and the link to its repository is read before quill writes to it."""
    github = _org([("PVT_p", PLAN.board_title), ("PVT_r", REHEARSAL.board_title)],
                  {"PVT_p": [PLAN.full_name], "PVT_r": [REHEARSAL.full_name]})
    assert board_of(github, PLAN) == "PVT_p"
    assert board_of(github, REHEARSAL) == "PVT_r"


@pytest.mark.parametrize(("boards", "linked", "refusal"), [
    ([("PVT_p", PLAN.board_title)], {}, "has no board titled 'Shekel plan rehearsal'"),
    ([("PVT_r", REHEARSAL.board_title)], {"PVT_r": [PLAN.full_name]},
     "is linked to \\['saltyreformed-labs/shekel-plan'\\]"),
    ([("PVT_r", REHEARSAL.board_title)], {"PVT_r": []}, "is linked to \\[\\]"),
])
def test_a_board_not_found_or_not_linked_to_the_places_repository_is_refused(
        boards, linked, refusal):
    """A board titled for the rehearsal but linked only to the real tracker is the board a
    mistyped title would find: it is refused before anything is written to it."""
    with pytest.raises(TrackerError, match=refusal):
        board_of(_org(boards, linked), REHEARSAL)


def _short_read(names):
    """A board GitHub says links 101 repositories, read as holding ``names`` only."""
    def answer(query, **_variables):
        if "projectsV2" in query:
            return {"organization": {"id": "O", "projectsV2": {"nodes": [
                {"id": "PVT_r", "title": REHEARSAL.board_title}]}}}
        return {"node": {"repositories": {"totalCount": 101, "nodes": [
            {"nameWithOwner": name} for name in names]}}}
    return _Scripted(graphql=answer)


def test_a_cut_short_read_that_names_the_repository_proves_the_link():
    """Review A2 round 3: a read GitHub cut short can hide a repository, never invent one,
    so one that names the place's repository proves the link."""
    assert board_of(_short_read([REHEARSAL.full_name]), REHEARSAL) == "PVT_r"


def test_a_cut_short_read_that_does_not_name_the_repository_is_refused_as_unread():
    """Absent from a short read, the place's repository may be among the ones it left out:
    the board is refused, and the refusal says the read was short."""
    with pytest.raises(TrackerError,
                       match="a read GitHub cut short of its 101, none of them "
                             "saltyreformed-labs/shekel-plan-rehearsal: whether it is "
                             "linked is unread$"):
        board_of(_short_read([PLAN.full_name]), REHEARSAL)


# -- every card, whole -------------------------------------------------------

def _whole(number, body, comments, total=None, milestone=None):
    """Card ``number``'s recorded node with a body, comments and a milestone (its number, or
    None for none) added, as the whole-card listing reads it."""
    node = _recorded_open_node(number)
    node["body"] = body
    node["milestone"] = None if milestone is None else {"number": milestone}
    node["comments"] = {"totalCount": len(comments) if total is None else total,
                        "nodes": [{"author": {"login": who}, "createdAt": when, "body": text}
                                  for who, when, text in comments]}
    return node


def test_every_card_is_listed_whole_page_by_page_and_a_long_comment_thread_read_in_full():
    """Two pages; card #3's thread has more comments than the listing carries, so its
    comments are read in full by number; a card with no body reads as an empty one."""
    pages = [
        {"repository": {"issues": {"pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                                   "nodes": [_whole(2, "two", [("a", "t1", "hello")])]}}},
        {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                   "nodes": [_whole(3, None, [("a", "t1", "first")], total=101),
                                             _whole(4, "four", [])]}}},
    ]
    thread = {"repository": {"issue": {"number": 3, "comments": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"author": {"login": "a"}, "createdAt": f"t{n}", "body": f"c{n}"}
                  for n in range(101)]}}}}
    afters = []

    def answer(query, **variables):
        if "comments(first: 100, after" in query:
            assert variables["number"] == 3
            return copy.deepcopy(thread)
        afters.append(variables["after"])
        assert "states: [OPEN, CLOSED]" in query and " body\n" in query
        return pages.pop(0)

    github = _Scripted(graphql=answer)
    cards = Tracker(github, Board(github, PLAN, "B"), APP).all_cards()
    assert afters == [None, "C1"]
    assert sorted(cards) == [2, 3, 4]
    assert (cards[2].card.number, cards[2].body, cards[2].comments) == (
        2, "two", (Comment("a", "t1", "hello"),))
    assert cards[3].body == "" and len(cards[3].comments) == 101
    assert cards[3].comments[100] == Comment("a", "t100", "c100")
    assert cards[4].comments == ()


def test_the_whole_card_listing_reads_each_cards_milestone_by_number():
    """The listing selects a card's milestone (L7 draft 4 s.12: there, not in the fields
    every query shares) and a card holds its number, None for none."""
    page = {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                      "nodes": [_whole(2, "two", [], milestone=3),
                                                _whole(3, "three", [])]}}}

    def answer(query, **_variables):
        assert "milestone { number }" in query
        return copy.deepcopy(page)

    github = _Scripted(graphql=answer)
    cards = Tracker(github, Board(github, PLAN, "B"), APP).all_cards()
    assert (cards[2].milestone, cards[3].milestone) == (3, None)


# -- the numbering -----------------------------------------------------------

#: The real tracker on 2026-10-08 (review 3, read-only): #1-#10, #26 and #27 exist;
#: #11-#25 and #28-#30 answer 410; #31 answers 404.
TODAY = {**dict.fromkeys([*range(1, 11), 26, 27], NumberState.ISSUE),
         **dict.fromkeys([*range(11, 26), 28, 29, 30], NumberState.DELETED)}


def _state_of(states):
    """A REST read by number over ``states``; any other number answers 404.  It keeps every
    number read."""
    read = []

    def state_of(number):
        read.append(number)
        return states.get(number, NumberState.NOT_FOUND)
    return state_of, read


def test_the_numbering_steps_over_deleted_numbers_and_stops_at_the_first_404_above():
    """Every gap below the highest listed is read, and every number above it up to the
    first 404: the read stops at #31, not #28."""
    state_of, read = _state_of(TODAY)
    listed = [number for number, state in TODAY.items() if state is NumberState.ISSUE]
    assert numbering(listed, state_of) == Numbering(
        tuple([*range(11, 26), 28, 29, 30]), (), 31)
    assert read == [*range(11, 26), 28, 29, 30, 31]


def test_a_pull_request_is_stepped_over_and_an_empty_tracker_starts_at_one():
    """A pull request takes a number from the same sequence and is no card."""
    state_of, _ = _state_of({1: NumberState.ISSUE, 2: NumberState.PULL_REQUEST})
    assert numbering([1], state_of) == Numbering((), (2,), 3)
    assert numbering([], _state_of({})[0]) == Numbering((), (), 1)


@pytest.mark.parametrize(("states", "listed", "refusal"), [
    ({**TODAY, 31: NumberState.ISSUE}, [*range(1, 11), 26, 27], "#31 is an issue the listing"),
    ({**TODAY, 12: NumberState.ISSUE}, [*range(1, 11), 26, 27], "#12 is an issue the listing"),
    ({1: NumberState.ISSUE, 3: NumberState.ISSUE}, [1, 3], "#2 answers 404"),
])
def test_an_issue_the_listing_lacks_or_a_hole_below_the_highest_is_refused(
        states, listed, refusal):
    """A create whose answer was lost, read by number past a listing that lags it, is
    refused rather than filed again; GitHub's one sequence leaves no unissued number below
    an issued one."""
    with pytest.raises(TrackerError, match=refusal):
        numbering(listed, _state_of(states)[0])


@pytest.mark.parametrize(("answer", "state"), [
    (GitHubError(410, "This issue was deleted"), NumberState.DELETED),
    (GitHubError(404, "Not Found"), NumberState.NOT_FOUND),
    ({"number": 5, "pull_request": {"url": "u"}}, NumberState.PULL_REQUEST),
    ({"number": 5}, NumberState.ISSUE),
])
def test_a_number_is_read_by_rest_and_answered_by_its_status(answer, state):
    """What each answer to ``GET /repos/<place>/issues/<n>`` means."""
    github = _Scripted(rest={("GET", f"{REHEARSAL.path}/issues/5"): answer})
    assert Tracker(github, Board(github, REHEARSAL, "B"), APP).number_state(5) is state


def test_a_moved_issue_is_refused_and_any_other_refusal_raised():
    """A 301 (an issue transferred away; never followed) is a refusal naming it; a 500 is
    GitHub's error, raised as it came."""
    path = f"{PLAN.path}/issues/5"
    moved = _Scripted(rest={("GET", path): GitHubError(301, "Moved Permanently")})
    with pytest.raises(TrackerError, match="#5 of saltyreformed-labs/shekel-plan was moved"):
        Tracker(moved, Board(moved, PLAN, "B"), APP).number_state(5)
    broken = _Scripted(rest={("GET", path): GitHubError(500, "boom")})
    with pytest.raises(GitHubError, match="boom"):
        Tracker(broken, Board(broken, PLAN, "B"), APP).number_state(5)


def test_the_fake_tracker_numbers_as_github_does_over_deleted_numbers():
    """Code B's migration is graded on the fake: a deleted number is read as GitHub reads one
    and is never given out again, so the probe's stopping number and the fake's next
    create agree."""
    tracker = FakeTracker()
    for number in (*range(1, 11), 26, 27):
        tracker.add(number)
    tracker.gone = {*range(11, 26), 28, 29, 30}
    assert numbering(tracker.all_cards(), tracker.number_state).stopped_at == 31
    assert tracker.create("step", "t", "b", ["balance"]) == 31


# -- the title search --------------------------------------------------------

def _hits(first, count):
    """``count`` search hits numbered from ``first``."""
    return [{"number": n, "title": f"[A-{n}] t"} for n in range(first, first + count)]


_SEARCH = ("/search/issues?q=repo%3Asaltyreformed-labs%2Fshekel-plan%20in%3Atitle%20%22A%22"
           "&per_page=100")


def test_a_title_search_reads_every_page_its_first_one_as_before():
    """150 hits on two pages, one of them a pull request; the first page's URL is the one
    ``recorded/tracker.json`` holds the shape of (no page number)."""
    pull = {**_hits(200, 1)[0], "pull_request": {"url": "u"}}
    github = _Scripted(rest={
        ("GET", _SEARCH): {"total_count": 150, "incomplete_results": False,
                           "items": _hits(1, 100)},
        ("GET", _SEARCH + "&page=2"): {"total_count": 150, "incomplete_results": False,
                                       "items": [*_hits(101, 49), pull]},
    })
    found = Tracker(github, Board(github, PLAN, "B"), APP).find_titles("A")
    assert [number for number, _ in found] == list(range(1, 150))
    assert [path for _, path, _ in github.sent] == [_SEARCH, _SEARCH + "&page=2"]


@pytest.mark.parametrize(("total", "incomplete"), [(1001, False), (3, True)])
def test_a_search_github_cannot_serve_whole_is_refused(total, incomplete):
    """GitHub serves at most 1,000 hits of a search, and says when one timed out; neither
    answer is every card, so neither is read as one."""
    github = _Scripted(rest={("GET", _SEARCH): {
        "total_count": total, "incomplete_results": incomplete, "items": _hits(1, 3)}})
    with pytest.raises(TrackerError, match="name the card as plan#N"):
        Tracker(github, Board(github, PLAN, "B"), APP).find_titles("A")


def test_a_board_linked_only_to_the_rehearsal_is_not_the_real_trackers():
    """The other direction of the prefix hazard: the rehearsal's ``owner/name`` begins with
    the real one's, and a board linked to the rehearsal alone is refused for the real
    tracker, which a prefix test would accept."""
    github = _org([("PVT_p", PLAN.board_title)], {"PVT_p": [REHEARSAL.full_name]})
    refusal = (r"linked to \['saltyreformed-labs/shekel-plan-rehearsal'\], "
               r"not to saltyreformed-labs/shekel-plan$")
    with pytest.raises(TrackerError, match=refusal):
        board_of(github, PLAN)


def _connect(monkeypatch, linked, slug=APP):
    """``Tracker.connect`` over a scripted App whose login is ``slug``: no key read, nothing
    sent to GitHub.  The token minted is kept with the keywords it was minted with."""
    minted = []
    monkeypatch.setattr(_tracker, "app_credentials", lambda: ("Iv23client", b"pem"))
    monkeypatch.setattr(_tracker, "app_jwt", lambda *_: "jwt")
    monkeypatch.setattr(_tracker, "app_installation",
                        lambda owner, _jwt: {"id": 7} if owner == REHEARSAL.owner else None)

    def token(installation, jwt, **keywords):
        minted.append((installation, jwt, keywords))
        return "narrowed"

    monkeypatch.setattr(_tracker, "installation_token", token)
    boards = _org([("PVT_p", PLAN.board_title), ("PVT_r", REHEARSAL.board_title)], linked)
    app = _Scripted(rest={("GET", "/app"): {"slug": slug}})
    monkeypatch.setattr(_tracker, "GitHub", lambda used: app if used == "jwt" else boards)
    return minted


def test_connect_mints_a_token_narrowed_to_the_place_and_checks_its_boards_link(monkeypatch):
    """Review A2 finding 2: the narrowing and the link check are what ``connect`` does,
    so they are graded there, not only where each is built."""
    minted = _connect(monkeypatch, {"PVT_r": [REHEARSAL.full_name]})
    tracker = Tracker.connect(REHEARSAL)
    assert minted == [(7, "jwt", {"repository": REHEARSAL.name})]
    assert (tracker.place, tracker.board_id, tracker.app_login) == (REHEARSAL, "PVT_r", APP)
    _connect(monkeypatch, {"PVT_r": [PLAN.full_name]})
    with pytest.raises(TrackerError, match="not to saltyreformed-labs/shekel-plan-rehearsal"):
        Tracker.connect(REHEARSAL)


#: What ``connect`` says of credentials that sign in as the App before its rename.
_ANOTHER_APP = "sign in as 'shekel-plan-tool', not as quill's App 'shekel-quill'"


def test_connect_refuses_credentials_of_another_app_before_it_asks_for_an_installation(
        monkeypatch):
    """L9d a1 (the coordinator's 13:33 ruling): the App's login decides which closes are the
    tool's, and the card-only reader reads the declared one, so ``connect`` holds the live
    App to it: one that differs is refused, naming both, before it asks GitHub for the
    App's installation or mints a token."""
    minted = _connect(monkeypatch, {"PVT_r": [REHEARSAL.full_name]}, slug="shekel-plan-tool")
    asked = []
    monkeypatch.setattr(_tracker, "app_installation",
                        lambda owner, _jwt: asked.append(owner) or {"id": 7})
    with pytest.raises(TrackerError, match=_ANOTHER_APP):
        Tracker.connect(REHEARSAL)
    assert not asked and not minted


def test_quill_stops_with_exit_2_on_credentials_of_another_app(monkeypatch, capsys):
    """The refusal reaches the person through quill's named-error path: exit 2, both logins
    in its text, through the command line's own connection to the plan's tracker."""
    _connect(monkeypatch, {"PVT_p": [PLAN.full_name]}, slug="shekel-plan-tool")
    assert quill.main(["next"], connect=quill.CONNECT) == 2
    assert _ANOTHER_APP in capsys.readouterr().err


class _AnyWrite(_Scripted):
    """A GitHub that answers every REST call as a filed issue would be answered (a create
    reads its type and labels back; a retype its type), keeping each call."""

    def rest(self, method, path, body=None):
        """Keep the call; answer it."""
        self.sent.append((method, path, body))
        if path.endswith("/issues") and method == "POST":
            return {"number": 5, "type": {"name": body["type"]},
                    "labels": [{"name": name} for name in body["labels"]]}
        return {"type": {"name": "step"}}


def test_every_write_to_a_rehearsal_tracker_goes_to_the_rehearsal_repository():
    """Review A2 finding 4: each REST write, on the rehearsal's path and never the real
    one's (which is not a prefix of it once the ``/`` is on).  A claim is no tracker write
    since R-BAL359: it is the code repository's branch ``card/N``."""
    github = _AnyWrite()
    tracker = Tracker(github, Board(github, REHEARSAL, "PVT_r"), APP)
    card = card_from(_recorded_open_node(2), REHEARSAL, "PVT_r", APP)
    tracker.create("step", "t", "b", ["balance"])
    tracker.retype(5, "step")
    tracker.retitle(5, "t")
    tracker.set_body(5, "b")
    tracker.comment(5, "c")
    tracker.close(5, "completed")
    tracker.reopen(5)
    tracker.unmark(5)
    tracker.add_child(5, card)
    tracker.remove_child(5, card)
    tracker.block(5, card)
    tracker.unblock(5, card)
    paths = [path for _, path, _ in github.sent]
    assert len(paths) == 12
    assert all(path.startswith(REHEARSAL.path + "/") for path in paths), paths


def test_a_search_answering_a_page_empty_before_every_hit_is_refused():
    """Review A2 finding 6: a page that comes back empty while hits remain would end the
    read short, and a short read can name one card where the whole names another."""
    github = _Scripted(rest={
        ("GET", _SEARCH): {"total_count": 150, "incomplete_results": False,
                           "items": _hits(1, 100)},
        ("GET", _SEARCH + "&page=2"): {"total_count": 150, "incomplete_results": False,
                                       "items": []},
    })
    with pytest.raises(TrackerError, match="answered page 2 empty after 100 of 150"):
        Tracker(github, Board(github, PLAN, "B"), APP).find_titles("A")
