"""Recorded GitHub exchanges: kept by a session that calls GitHub once, replayed by the tests.

Quill's tests never call GitHub (the build plan, L2), yet a test fed a
response its author imagined grades the author's imagination.  So the
exchanges the tests replay were RECORDED: :class:`Recorder` wraps the session
a real :class:`_github.GitHub` sends through and keeps every request and
GitHub's answer, and :class:`Replay` answers the same requests with the same
answers, in the order they were recorded, refusing any request it never saw.

A recording is JSON: ``{"scratch": {...}, "exchanges": [{"method", "url",
"body", "status", "answer"}, ...]}``.  Nothing secret is in one: the token
rides in a header, and headers are not kept.

**A recording session writes to scratch cards only, and keeps the text of
scratch cards only.**  A recording lives in the PUBLIC code repository, and the
tracker it reads is private: real production figures are allowed there
(``R-BAL172``).  So:

- **The scratch cards are fixed before anything is sent** (:class:`Scratch`):
  the tracker they are on (its :class:`setup_tracker.Place`: the real tracker, or
  X-cx's rehearsal tracker), the cards the recording session has checked carry a
  scratch title (:data:`SCRATCH`), and that tracker's board's id.  "The tracker"
  below is that place's.  The set grows only by what the
  session itself makes: a card it files with a scratch title, and the board
  item it adds for a scratch card.
- **Only a known write to a scratch card is sent** (:func:`refusal`, asked of
  every request before it goes out, and of every request a recording keeps): a
  REST write takes one of the routes in :data:`_ROUTES` on the tracker, with
  exactly the body keys quill sends (their values are not checked but
  for the cards they name) and every card it names -- in its path, or by its
  REST id -- a scratch card; a GraphQL write is one of the
  board's four, word for word, on the plan board, moving a scratch card's
  board item.  A body sent other than as JSON is refused too.  Anything else is
  refused, unsent.
- **Every string an answer holds is redacted** (:data:`REDACTED`) unless its
  key is one of :data:`_KEPT` -- ids, states, names of labels, types and
  repositories, dates, error messages -- or it belongs to a scratch card, or it
  is the board's own title (the place's ``board_title``, already in this
  repository).  An object carrying a ``number`` is that card of the
  tracker only when it names the tracker as its repository, or names none and
  sits where the request reads the tracker's own cards: the answer itself to a
  REST request under the tracker, or a field of the tracker's ``repository``
  (and its ``issues``) in a GraphQL query whose every ``repository(...)`` is
  the tracker's and that aliases nothing as ``repository``.  Any other
  numbered object is no card.  An object with no number that carries a
  string of its own belongs to NO card -- except the answer itself, which is
  the card the request's URL names, and the edit history and the comments of
  an object that carries a card's number (:data:`_OWN_CONTENT`); any other
  object, and a string in a list, shares its enclosing object's card, and
  nothing encloses the answer.

What a recording can still hold: a string under a :data:`_KEPT` key, of any
card (so a key enters that set only when no card's text can be stored under
it); everything on the REQUEST side, kept as sent because the replay matches it
-- read URLs, search terms, GraphQL variables -- so a recording session puts no
private text in a request; a scratch card's own
text, including any edit history or comment from before it carried a scratch
title (the recording sessions file their scratch cards as scratch); and, where
the request reads the tracker's own cards, the text of an object carrying a
number that is not a card's (a milestone numbered like a scratch card), read as
that card's.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import requests

from tools.quill._github import API
from tools.quill._tracker import BOARD_ADD, BOARD_AFTER, BOARD_REMOVE, BOARD_TOP
from tools.quill.setup_tracker import FILING, PLAN, Place

#: Where the recordings the tests replay are kept.
RECORDINGS = Path(__file__).resolve().parent / "recorded"
#: Every card a recording may write to, or keep text of, has a title beginning with this.
SCRATCH = "[L2 measurement] scratch"
#: What stands in a recording for a string it may not keep.
REDACTED = "[redacted: not a scratch card]"
#: The keys whose strings are kept whichever card they belong to: none holds a
#: card's text.
_KEPT = frozenset({
    "__typename", "authoredDate", "createdAt", "date", "editedAt", "endCursor",
    "fullDatabaseId", "full_name", "id", "login", "merged_at", "message", "name",
    "nameWithOwner", "node_id", "path", "ref", "repository_url", "sha", "state",
    "stateReason", "state_reason", "type",
})
#: The keys under which an object with no number is its enclosing card's own content: its
#: saved versions and its comments.
_OWN_CONTENT = frozenset({"userContentEdits", "comments"})
#: GitHub's node-id prefix for a board (a ProjectV2).
_BOARD_NODE = "PVT_"
_GRAPHQL_URL = f"{API}/graphql"
#: Each GraphQL write a recording may send, and the variable naming what it moves: the
#: card's node id for an add, its board item for the rest.
_BOARD_WRITES = {BOARD_ADD: "c", BOARD_REMOVE: "i", BOARD_TOP: "i", BOARD_AFTER: "i"}
_MUTATION = re.compile(r"\bmutation\b")
_REPOSITORY_ARGUMENTS = re.compile(r"\brepository\s*\(([^)]*)\)")
_REPOSITORY_ALIAS = re.compile(r"\brepository\s*:")
#: A GraphQL string (a block string, then a one-line one), or what GraphQL reads as
#: nothing between two tokens: a comment, to the end of its line (``\n`` or ``\r``), a
#: comma, and a byte-order mark.
_STRING_OR_IGNORED = re.compile(
    r'"""(?:\\"""|[^"]|"(?!""))*"""|"(?:\\.|[^"\\\n\r])*"|#[^\n\r]*|[,\ufeff]')
#: The bodies quill PATCHes an issue with: retype, retitle, a new body, close, reopen.
_PATCHES = frozenset(frozenset(keys) for keys in (
    {"type"}, {"title"}, {"body"}, {"state", "state_reason"}, {"state"}))


def _repo_url(place: Place) -> str:
    """The REST URL of ``place``'s repository: every path under it, and only those, is the
    tracker's."""
    return f"{API}{place.path}"


def _the_tracker(place: Place) -> re.Pattern:
    """A GraphQL ``repository(...)``'s arguments naming ``place``'s repository, whole."""
    return re.compile(rf'\s*owner:\s*"{re.escape(place.owner)}"\s*'
                      rf'name:\s*"{re.escape(place.name)}"\s*')


@dataclass
class Scratch:
    """The cards a recording may write to and keep the text of -- each by its number, its
    REST id and its node id -- the board items added for them, the tracker's board, and the
    tracker's :class:`setup_tracker.Place`.

    ``place`` is :data:`setup_tracker.PLAN` unless a session recording on X-cx's
    rehearsal tracker names that one.  Named wrongly, a recording fails CLOSED for every
    REST write (each matched on a path under the place's URL, so the other tracker's are
    refused unsent) and for every text (redacted as no card's).  A board write is held to
    ``board`` instead, the id the recording session reads for the board it means to move.
    """

    numbers: set[int] = field(default_factory=set)
    ids: set[int] = field(default_factory=set)
    nodes: set[str] = field(default_factory=set)
    items: set[str] = field(default_factory=set)
    board: str | None = None
    place: Place = PLAN

    def take(self, issue: dict) -> None:
        """Count a card a REST answer describes (``number``, ``id``, ``node_id``) as scratch."""
        self.numbers.add(issue["number"])
        self.ids.add(issue["id"])
        self.nodes.add(issue["node_id"])

    def as_json(self) -> dict:
        """The set as a recording keeps it."""
        return {"numbers": sorted(self.numbers), "ids": sorted(self.ids),
                "nodes": sorted(self.nodes), "items": sorted(self.items), "board": self.board,
                "place": [self.place.owner, self.place.name, self.place.board_title]}

    @classmethod
    def from_json(cls, kept: dict) -> Scratch:
        """The set a recording kept.  One with no ``place`` was kept before recordings named
        theirs (X-cx's L7), when every recording session read the real tracker."""
        place = Place(*kept["place"]) if "place" in kept else PLAN
        return cls(set(kept["numbers"]), set(kept["ids"]), set(kept["nodes"]),
                   set(kept["items"]), kept["board"], place)


_Allows = Callable[[re.Match, dict, Scratch], bool]
#: Every REST write a recording may send, under the tracker's repository: its method,
#: its path, and whether its body is exactly what quill sends, naming scratch cards
#: only.
_ROUTES: tuple[tuple[str, re.Pattern, _Allows], ...] = tuple(
    (method, re.compile(path), allows) for method, path, allows in (
        ("POST", r"/issues",
         lambda _m, body, _s: set(body) == {"title", "body", "type", "labels"}
         and str(body["title"]).startswith(SCRATCH)),
        ("PATCH", r"/issues/([0-9]+)",
         lambda m, body, s: int(m[1]) in s.numbers and frozenset(body) in _PATCHES
         and str(body.get("title", SCRATCH)).startswith(SCRATCH)),
        ("POST", r"/issues/([0-9]+)/comments",
         lambda m, body, s: int(m[1]) in s.numbers and set(body) == {"body"}),
        ("POST", r"/issues/([0-9]+)/sub_issues",
         lambda m, body, s: int(m[1]) in s.numbers and set(body) == {"sub_issue_id"}
         and body["sub_issue_id"] in s.ids),
        ("DELETE", r"/issues/([0-9]+)/sub_issue",
         lambda m, body, s: int(m[1]) in s.numbers and set(body) == {"sub_issue_id"}
         and body["sub_issue_id"] in s.ids),
        ("POST", r"/issues/([0-9]+)/dependencies/blocked_by",
         lambda m, body, s: int(m[1]) in s.numbers and set(body) == {"issue_id"}
         and body["issue_id"] in s.ids),
        ("DELETE", r"/issues/([0-9]+)/dependencies/blocked_by/([0-9]+)",
         lambda m, body, s: int(m[1]) in s.numbers and int(m[2]) in s.ids and not body),
        ("DELETE", rf"/issues/([0-9]+)/labels/{FILING}",
         lambda m, body, s: int(m[1]) in s.numbers and not body),
    )
)


def _rest_allows(method: str, url: str, body: dict, scratch: Scratch) -> bool:
    """Whether a REST write takes a route of :data:`_ROUTES` to scratch cards only.  A route
    is matched on the path under the tracker's URL, so a URL of any other repository, whose
    prefix stays on (or leaves a rest such as ``-rehearsal/issues``), matches none."""
    path = url.removeprefix(_repo_url(scratch.place))
    return any(method == route and (found := pattern.fullmatch(path)) is not None
               and allows(found, body, scratch) for route, pattern, allows in _ROUTES)


def _graphql_allows(body: dict, scratch: Scratch) -> bool:
    """Whether a GraphQL request is a read, or one of the board's writes moving a scratch
    card's item."""
    query = str(body.get("query", ""))
    if query in _BOARD_WRITES:
        variables = body.get("variables") or {}
        moved = variables.get(_BOARD_WRITES[query])
        return (scratch.board is not None and variables.get("p") == scratch.board
                and moved in (scratch.nodes if query == BOARD_ADD else scratch.items))
    return not _MUTATION.search(query)


def refusal(method: str, url: str, body, scratch: Scratch) -> str | None:
    """Why a recording may not send this request; None when it may.

    A recording writes to scratch cards only, so what it keeps of every write
    is a scratch card's: a request cannot be redacted, since the replay
    matches it as sent.
    """
    body = body or {}
    if url == _GRAPHQL_URL:
        allowed = _graphql_allows(body, scratch)
    else:
        allowed = method == "GET" or _rest_allows(method, url, body, scratch)
    return None if allowed else (f"a recording sends only reads and known writes to scratch "
                                 f"cards: {method} {url} {json.dumps(body)[:200]}")


def _as_graphql_reads_it(query: str) -> str:
    """``query`` with each comment, comma and byte-order mark read as the space GraphQL
    reads it as, and each string kept as text: a ``#`` or a comma in a string is text."""
    return _STRING_OR_IGNORED.sub(
        lambda found: found[0] if found[0].startswith('"') else " ", query)


def _about_the_tracker(url: str, body, place: Place) -> bool:
    """Whether a request's answer holds the tracker's own cards where :func:`_slot` looks:
    a REST path under the tracker, or a GraphQL query with a ``repository(...)``, every one
    the tracker's, and no alias named ``repository`` (:func:`_as_graphql_reads_it`)."""
    if url == _GRAPHQL_URL:
        query = _as_graphql_reads_it(str((body or {}).get("query", "")))
        arguments = _REPOSITORY_ARGUMENTS.findall(query)
        tracker = _the_tracker(place)
        return (bool(arguments) and all(tracker.fullmatch(each) for each in arguments)
                and not _REPOSITORY_ALIAS.search(query))
    return url.startswith(_repo_url(place) + "/")


def _slot(path: tuple[str, ...], graphql: bool) -> bool:
    """Whether an object at ``path`` (its keys from the answer down) is where a request about
    the tracker reads the tracker's own cards: the answer to a REST request (each item, when
    it is a list), or a field of the tracker's ``repository`` (or of its ``issues``) in a
    GraphQL one."""
    if not graphql:
        return not path
    return ((len(path) == 3 and path[:2] == ("data", "repository"))
            or path == ("data", "repository", "issues", "nodes"))


def _named_card(url: str, place: Place) -> int | None:
    """The card a REST request about the tracker names in its URL."""
    found = re.match(rf"{re.escape(_repo_url(place))}/issues/([0-9]+)(?:/|$)", url)
    return int(found[1]) if found else None


def _card_of(value: dict, slot: bool, place: Place) -> int | None:
    """The tracker card an object carrying a ``number`` is: when it names the tracker as its
    repository, or names none and sits in one of the request's card slots (``slot``); None
    otherwise."""
    repository, url = value.get("repository"), value.get("repository_url")
    if isinstance(repository, dict):
        named = repository.get("nameWithOwner") or repository.get("full_name")
        return value["number"] if named == place.full_name else None
    if url is not None:
        return value["number"] if url == _repo_url(place) else None
    return value["number"] if slot else None


@dataclass(frozen=True)
class _Owner:
    """Where a part of an answer sits, and whose text it holds: ``card``; the card the
    request names; whether the part is a card's own content (its edit history, its
    comments); whether the request reads the tracker's own cards
    (:func:`_about_the_tracker`), and by GraphQL; its ``path`` (its keys from the answer
    down); and whether it is the answer itself (``top``), not an item of it."""

    card: int | None
    named: int | None
    own: bool
    scoped: bool
    graphql: bool
    path: tuple[str, ...] = ()
    top: bool = False


def _redact(value, key: str | None, owner: _Owner, scratch: Scratch):
    """``value``, held under ``key`` where ``owner`` places it, with every string redacted
    that is neither a scratch card's nor under a :data:`_KEPT` key."""
    if isinstance(value, list):
        return [_redact(item, key, replace(owner, top=False), scratch) for item in value]
    if isinstance(value, str):
        return value if key in _KEPT or owner.card in scratch.numbers else REDACTED
    if not isinstance(value, dict):
        return value
    numbered = "number" in value
    if numbered:
        card = _card_of(value, owner.scoped and _slot(owner.path, owner.graphql), scratch.place)
        owner = replace(owner, card=card, own=False)
    elif any(isinstance(item, str) and name not in _KEPT for name, item in value.items()):
        owner = replace(owner, card=owner.named if owner.top else
                        owner.card if owner.own else None)
    board = (str(value.get("id", "")).startswith(_BOARD_NODE)
             and value.get("title") == scratch.place.board_title)
    return {
        name: (item if board and name == "title"
               else _redact(item, name, replace(
                   owner, own=owner.own or (numbered and name in _OWN_CONTENT),
                   path=(*owner.path, name), top=False), scratch))
        for name, item in value.items()
    }


def redacted(exchanges: list[dict], scratch: Scratch) -> list[dict]:
    """``exchanges`` with every string redacted but what a scratch card or a kept key holds.

    Raises:
        ValueError: when a request is one a recording may not send
            (:func:`refusal`).
    """
    kept = []
    for exchange in exchanges:
        url, body = exchange["url"], exchange["body"]
        why = refusal(exchange["method"], url, body, scratch)
        if why:
            raise ValueError(why)
        named = _named_card(url, scratch.place)
        answer = _redact(exchange["answer"], None,
                         _Owner(None, named, False, _about_the_tracker(url, body, scratch.place),
                                url == _GRAPHQL_URL, top=True), scratch)
        kept.append({**exchange, "answer": answer})
    return kept


def recording(name: str, directory: Path = RECORDINGS) -> dict:
    """The recording ``<directory>/<name>.json``: its scratch cards and its exchanges."""
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


def _key(method: str, url: str, body) -> str:
    """One request, as the replay matches it: method, URL and body, keys sorted."""
    return json.dumps([method, url, body], sort_keys=True)


class _Answer:
    """The slice of ``requests.Response`` :class:`_github.GitHub` reads.  A recording keeps
    no headers (module docstring), so a replayed answer carries none."""

    def __init__(self, status: int, answer) -> None:
        """Hold one recorded status and JSON answer (None: an empty body)."""
        self.status_code = status
        self.content = b"" if answer is None else json.dumps(answer).encode()
        self.text = self.content.decode()
        self.headers: dict[str, str] = {}
        self._answer = answer

    def json(self):
        """The recorded answer."""
        return self._answer


class Recorder:
    """A ``requests`` session that keeps every exchange it sends, and sends only what a
    recording may (:func:`refusal`)."""

    def __init__(self, scratch: Scratch, session=None) -> None:
        """Send through ``session`` (a real ``requests`` session unless a test passes one);
        ``scratch``: the cards the caller has checked are scratch cards, the only ones it
        may write to."""
        self._session = session or requests.Session()
        self.scratch = scratch
        self.exchanges: list[dict] = []

    def request(self, method, url, **kwargs):
        """Send the request to GitHub, unless it is refused; keep it and the answer.

        Raises:
            ValueError: for a request a recording may not send, before it is sent.
        """
        body = kwargs.get("json")
        why = refusal(method, url, body, self.scratch)
        if set(kwargs) - {"json", "headers", "timeout", "allow_redirects"}:
            why = f"a recording sends a body only as JSON: {sorted(kwargs)}"
        if why:
            raise ValueError(f"not sent: {why}")
        response = self._session.request(method, url, **kwargs)
        answer = response.json() if response.content else None
        self.exchanges.append({"method": method, "url": url, "body": body,
                               "status": response.status_code, "answer": answer})
        if response.status_code < 400:
            self._take(method, url, body, answer)
        return response

    def _take(self, method: str, url: str, body, answer) -> None:
        """Count what a write just made for a scratch card as scratch: a card filed with a
        scratch title, and a scratch card's board item."""
        if method == "POST" and url == f"{_repo_url(self.scratch.place)}/issues":
            self.scratch.take(answer)
        elif url == _GRAPHQL_URL and (body or {}).get("query") == BOARD_ADD:
            added = ((answer or {}).get("data") or {}).get("addProjectV2ItemById")
            if added:
                self.scratch.items.add(added["item"]["id"])

    def save(self, name: str, directory: Path = RECORDINGS) -> Path:
        """Write the scratch cards and the exchanges to ``<directory>/<name>.json``
        (``recorded/`` unless a test names another), redacted (:func:`redacted`); refuse
        one holding a token (an installation token is minted through a client this
        never wraps)."""
        held = [e["url"] for e in self.exchanges if isinstance(e["answer"], dict)
                and "token" in e["answer"]]
        if held:
            raise ValueError(f"a recording would keep a token from {held}")
        kept = {"scratch": self.scratch.as_json(),
                "exchanges": redacted(self.exchanges, self.scratch)}
        path = directory / f"{name}.json"
        path.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")
        return path


class Replay:
    """A ``requests`` session answering only what a recording holds, in its order."""

    def __init__(self, name: str) -> None:
        """Load ``recorded/<name>.json``."""
        self._answers: dict[str, deque] = defaultdict(deque)
        for exchange in recording(name)["exchanges"]:
            key = _key(exchange["method"], exchange["url"], exchange["body"])
            self._answers[key].append((exchange["status"], exchange["answer"]))
        self.sent: list[tuple[str, str, object]] = []

    def request(self, method, url, **kwargs):
        """The next recorded answer to this exact request; an unrecorded one is an error."""
        body = kwargs.get("json")
        self.sent.append((method, url, body))
        queue = self._answers.get(_key(method, url, body))
        if not queue:
            raise AssertionError(f"no recorded answer left for {method} {url} {body}")
        return _Answer(*queue.popleft())

    def unused(self) -> int:
        """How many recorded answers no request has taken yet."""
        return sum(len(queue) for queue in self._answers.values())
