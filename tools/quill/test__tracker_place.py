"""A tracker's place, its board's fence and the paged title search (X-cx L7), against
scripted GitHub answers; nothing calls GitHub.

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
from tools.quill._draft import Draft
from tools.quill._recorded import recording
from tools.quill._tracker import (
    BOARD_ADD,
    BOARD_AFTER,
    BOARD_REMOVE,
    BOARD_TOP,
    Board,
    Tracker,
    TrackerError,
    board_of,
    card_from,
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
    tracker.cards([5])
    tracker.body(5)
    tracker.edits(5)
    tracker.comments(5)
    tracker.find_titles("x")
    queries = [query for kind, query, _ in github.sent if kind == "GRAPHQL"]
    assert len(queries) == 5
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
                                       "number": 9, "issueType": {"name": "step"},
                                       "repository": {"nameWithOwner": REHEARSAL.full_name}}}]}}}

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
    tracker.create(Draft("step", "t", "b", ("balance",), None))
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
