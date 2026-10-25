"""The tracker's cards and board order, read and written by quill.

Every card write goes through quill's GitHub App (ruling
``balance:R-BAL170``), a separate identity from the developer's login, so a
reader can tell the tool's write from his own edit and never act on his.  This
module is the only place quill speaks to the tracker -- the daily backup
(:mod:`_export`) keeps GitHub's own answers to the reads here, each built on the one
walk of a connection, :func:`_github.paged` -- and it decides nothing about the plan
(``_state`` does), it only reads and writes.

**No claim is the tracker's**: since the ship marker (ruling ``balance:R-BAL359``) a
claim is the code repository's branch ``card/N``, read and written by ``_git``.

**What each read can see, measured on the tracker 2026-10-04:**

- A card's REST id (what the sub-issue and blocked-by calls take) is its
  GraphQL ``fullDatabaseId``.
- The board's order is ``items(orderBy: {field: POSITION})``, and it LAGS a
  write (L1 measured 7 of 9 just-added cards listed, all 9 about 20 seconds
  later), so a write that places a card reads the board back until it shows
  the card where it was put (:data:`BOARD_WAIT_SECONDS`).
- GitHub silently drops an issue's ``type`` when the writer lacks push access,
  so every write that sets one reads it back.
- The listings of open cards and of marked cards (the :data:`setup_tracker.FILING`
  label), and a card's sub-issues read by its number or in a listing, each
  showed a create, a mark's removal or an unlink at the first read of it after
  the write (2026-10-04 22:13, one sample each, on a quiet tracker; the delay
  itself was not timed).  Nothing here waits for them, as it does for the board;
  one sample does not show they never lag.  Removing a mark a card does not
  carry is answered 404 "Label does not exist".

**Every read and write names its tracker's :class:`setup_tracker.Place`**, the one
the :class:`Board` was resolved for: :data:`setup_tracker.PLAN` for every quill
command, and the rehearsal tracker for X-cx's migration (L7).  A write cannot
reach another tracker's cards or board:

- the App's token is minted NARROWED to the place's one repository
  (:meth:`Tracker.connect`).  That narrows repository access only: the board is an
  ORGANIZATION project, which the token may still reach by node id;
- so the board is resolved by its title in the place's organization and refused
  unless it is linked to the place's repository (:func:`board_of`), and every board
  write is sent through :meth:`Board._write`, which names that board and no other.

**The numbering** (:func:`numbering`): GitHub numbers issues and pull requests in one
sequence and answers a DELETED issue's number ``410 Gone`` (#11-#25 and #28-#30 on
the real tracker), so a listing of the cards that exist shows gaps.  Each gap is
read by number, which is how X-cx's migration (L7, Code B) is to find a card whose
create answer was lost rather than file it twice.

**A link to an issue outside the tracker is carried, never followed**
(ruling ``balance:R-BAL188``).  GitHub lets a parent, sub-issue or blocked-by
link cross repositories in one organization (Shekel itself joins it at X-cx's
L5); read by number alone, such a link would name the tracker's card of that
number instead.  So every link and every board item is asked which repository
it names (:func:`_is_tracker_issue`, the one test), and a card keeps its
outside links apart (:attr:`Card.outside`) for the plan to report.

**A card can be read without the board** (:class:`CardReader`), by a token that may read
the tracker's issues and nothing else (built for CI's landing check, X-cx L9d).  Such a
card is a :class:`BareCard`, which has no board item at all, so a decision about the board
handed one raises instead of reading it as a card off the board; :class:`Tracker` is the
reader that reads each card with its board item, a :class:`Card`.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import overload
from urllib.parse import quote

from tools.quill import _queries
from tools.quill._github import (
    GitHub,
    GitHubError,
    app_credentials,
    app_installation,
    app_jwt,
    installation_token,
    paged,
)
from tools.quill.setup_tracker import (
    APP_LOGIN,
    FILING,
    TASK,
    Place,
    another_app,
    find_board,
    linked,
)

#: How long a write waits for the board to show what it placed: three times
#: the ~20 seconds L1 measured, so a slow read is waited out and a stuck one
#: is reported rather than waited on forever.
BOARD_WAIT_SECONDS = 60
#: How often a board that lags a write is read again.
POLL_SECONDS = 5
#: A connection GitHub caps per card (a parent holds at most 100 sub-issues).
_PAGE = 100
#: The largest number a read by number can ask for: GraphQL's ``Int`` is a signed 32-bit
#: integer (the GraphQL specification), so no issue is numbered past it.
GRAPHQL_INT_MAX = 2**31 - 1
#: GitHub's issue search serves at most this many results of one query, whatever its
#: ``total_count`` says.
_SEARCH_CAP = 1000

#: The board's writes, the only GraphQL mutations quill sends (a recording
#: sends no other: ``_recorded.refusal``), each sent by :meth:`Board._write` alone.
#: ``$p`` is the board, ``$c`` a card's node id, ``$i`` a board item, ``$a`` the item
#: it goes after.
BOARD_ADD = ("mutation($p: ID!, $c: ID!) { addProjectV2ItemById(input: {projectId: $p, "
             "contentId: $c}) { item { id } } }")
BOARD_REMOVE = ("mutation($p: ID!, $i: ID!) { deleteProjectV2Item(input: {projectId: $p, "
                "itemId: $i}) { deletedItemId } }")
BOARD_TOP = ("mutation($p: ID!, $i: ID!) { updateProjectV2ItemPosition(input: "
             "{projectId: $p, itemId: $i}) { clientMutationId } }")
BOARD_AFTER = ("mutation($p: ID!, $i: ID!, $a: ID!) { updateProjectV2ItemPosition(input: "
               "{projectId: $p, itemId: $i, afterId: $a}) { clientMutationId } }")


class TrackerError(RuntimeError):
    """The tracker refused a write or answered something quill cannot read."""


@dataclass(frozen=True)
class Child:
    """A sub-issue as its parent lists it."""

    number: int
    kind: str | None
    is_open: bool


@dataclass(frozen=True)
class OutsideLink:
    """A card's link to an issue outside the tracker: ``what`` the link is (``parent``,
    ``sub-issue`` or ``blocker``) and ``issue``, ``owner/name#number``."""

    what: str
    issue: str


@dataclass(frozen=True)
class BareCard:  # pylint: disable=too-many-instance-attributes
    """One card, as the plan reads it without the board (:class:`CardReader`): every fact
    of a :class:`Card` but its board item, which it does not have at all, so a decision
    about the board that is handed one raises AttributeError, never reads it as off the
    board.

    Pylint: too-many-instance-attributes (14/7) -- **fourteen because a card
    states fourteen facts the plan reads without the board**, each its own GitHub
    field, and no subset of them travels apart from the others.

    ``parent``, ``children`` and ``blocked_by`` name cards of the tracker only;
    ``outside`` holds every link to an issue anywhere else (R-BAL188).
    ``closed_by_tool``: the card is closed, and the last time anyone closed or
    reopened it, it was quill -- so its state is the tool's display of
    git, not a person's decision.  ``touched_by_hand``: the last close or reopen
    was a person's.

    A card holds only what GitHub says of it.  Which of its sub-issues are LEAVES
    of the plan, and so whether it is a container, reads its children's own marks
    and git too, so the plan decides it (``_state.leaves``), never the card.
    """

    number: int
    id: int
    node_id: str
    title: str
    kind: str | None
    labels: tuple[str, ...]
    is_open: bool
    state_reason: str | None
    parent: int | None
    children: tuple[Child, ...]
    blocked_by: tuple[int, ...]
    closed_by_tool: bool
    touched_by_hand: bool
    outside: tuple[OutsideLink, ...]

    @property
    def step_children(self) -> tuple[int, ...]:
        """The steps linked under this card, as GitHub lists them: each a leaf of it unless
        the plan says otherwise (``_state.leaves``, the one spelling of a leaf)."""
        return tuple(child.number for child in self.children if child.kind == "step")

    @property
    def task_children(self) -> tuple[int, ...]:
        """The tasks linked under this card, as GitHub lists them (R-BAL386): each a piece of
        its work, done by its own file (``_state.done_tasks``), never a leaf."""
        return tuple(child.number for child in self.children if child.kind == TASK)


@dataclass(frozen=True)
class Card(BareCard):
    """One card, as the plan reads it with the board (:class:`Tracker`): a
    :class:`BareCard` and ``board_item``, its item on the tracker's board, None when the
    board does not hold it."""

    board_item: str | None


@dataclass(frozen=True)
class WholeCard:
    """A card with its text: its :class:`Card`, its body, every comment on it, oldest
    first, and the number of its milestone, None for none (X-cx's migration reads every
    card this way, L7)."""

    card: Card
    body: str
    comments: tuple[Comment, ...]
    milestone: int | None


@dataclass(frozen=True)
class Milestone:
    """One of a tracker's milestones: its number, title and description (X-cx's migration
    gives each outcome one, ruling ``balance:R-BAL243``)."""

    number: int
    title: str
    description: str


class NumberState(Enum):
    """What GitHub answers a REST read of an issue number with (:meth:`Tracker.number_state`)."""

    ISSUE = "an issue"
    PULL_REQUEST = "a pull request"
    DELETED = "a deleted issue (410)"
    NOT_FOUND = "nothing this token can read (404)"


@dataclass(frozen=True)
class Numbering:
    """The numbers a tracker's listing of cards lacks, read one by one (:func:`numbering`):
    the deleted ones, the pull requests, and ``stopped_at``, the first number above the
    highest listed that answers 404, where the read stopped.  It is the number the next
    create takes only when nothing the token cannot read holds it: GitHub answers 404 for
    that too, whatever it is."""

    deleted: tuple[int, ...]
    pull_requests: tuple[int, ...]
    stopped_at: int


@dataclass(frozen=True)
class Edit:
    """One saved version of a card's body, in full."""

    edit_id: str | None
    edited_at: str
    editor: str | None
    body: str


@dataclass(frozen=True)
class Comment:
    """One comment on a card: who wrote it (None for an account since deleted), when, and
    its text."""

    author: str | None
    created_at: str
    body: str


def connection_nodes(connection: dict | None, what: str, number: int) -> list:
    """A connection's nodes, refusing one GitHub answered as null, which would read as none,
    and one holding a null node, which no fact can be read from: GitHub's schema lets it
    answer an issue's ``labels`` so, any connection's ``nodes``, and each node in them
    (``[Label]``, ``[Issue]``, ``[IssueTimelineItems]``; read 2026-10-18)."""
    if connection is None or connection["nodes"] is None:
        raise TrackerError(f"GitHub answered plan#{number}'s {what} as null, so the read "
                           "holds none of them")
    if None in connection["nodes"]:
        raise TrackerError(f"GitHub answered one of plan#{number}'s {what} as null, so the "
                           "read cannot say what it is")
    return connection["nodes"]


def _counted(connection: dict | None, what: str, number: int) -> list:
    """A connection's nodes (:func:`connection_nodes`), refusing one GitHub truncated (a silent
    partial read)."""
    nodes = connection_nodes(connection, what, number)
    if connection["totalCount"] > len(nodes):
        raise TrackerError(
            f"plan#{number} has {connection['totalCount']} {what}; the read holds "
            f"{len(nodes)}"
        )
    return nodes


def listing(github: GitHub, query: str) -> list[dict]:
    """Every issue node a listing ``query`` holds (:func:`_queries.open_cards`,
    :func:`_queries.marked_cards`, :func:`_queries.all_cards`,
    :func:`_queries.export_cards`), read page by page (:func:`_github.paged`)."""
    return paged(partial(github.graphql, query),
                 lambda answer: answer["repository"]["issues"])[0]


def saved_versions(github: GitHub, place: Place, number: int) -> tuple[list[dict], dict]:
    """Every saved version of card ``number``'s text as GitHub answers it
    (:func:`_queries.edits`), and the last answer read, which holds the card's text, who
    filed it and when.

    Raises:
        TrackerError: When a page answers the card as null.
    """
    def saved(answer: dict) -> dict:
        if answer["repository"]["issue"] is None:
            raise TrackerError(f"plan#{number} does not exist")
        return answer["repository"]["issue"]["userContentEdits"]

    return paged(partial(github.graphql, _queries.edits(place), number=number), saved)


def comment_nodes(github: GitHub, place: Place, number: int,
                  fields: str = _queries.COMMENT_FIELDS) -> list[dict]:
    """Every comment on card ``number`` as GitHub answers it, each read as ``fields``
    (:func:`_queries.comments`)."""
    return paged(partial(github.graphql, _queries.comments(place, fields), number=number),
                 lambda answer: answer["repository"]["issue"]["comments"])[0]


def comment_versions(github: GitHub, comment: str) -> list[dict]:
    """Every saved version of the comment whose node id is ``comment``, as GitHub answers
    it (:data:`_queries.COMMENT_EDITS`)."""
    return paged(partial(github.graphql, _queries.COMMENT_EDITS, id=comment),
                 lambda answer: answer["node"]["userContentEdits"])[0]


def milestone_nodes(github: GitHub, place: Place) -> list[dict]:
    """Every milestone of ``place``, open and closed, as GitHub answers it
    (:func:`_queries.milestones`)."""
    return paged(partial(github.graphql, _queries.milestones(place)),
                 lambda answer: answer["repository"]["milestones"])[0]


def _is_tracker_issue(node: dict, place: Place) -> bool:
    """Whether a link or a board item's content names an issue of ``place``'s tracker: the
    one test, for a parent, a sub-issue, a blocker and the board alike, on the whole
    ``owner/name`` token."""
    return (node.get("repository") or {}).get("nameWithOwner") == place.full_name


def _comment(node: dict) -> Comment:
    """A :class:`Comment` from one GraphQL comment node (:data:`_queries.COMMENT_FIELDS`)."""
    return Comment((node["author"] or {}).get("login"), node["createdAt"], node["body"])


def _outside(node: dict, what: str) -> OutsideLink:
    """A link to an issue outside the tracker."""
    return OutsideLink(what, f"{node['repository']['nameWithOwner']}#{node['number']}")


@overload
def card_from(node: dict, place: Place, board_id: str, app_login: str) -> Card: ...
@overload
def card_from(node: dict, place: Place, board_id: None, app_login: str) -> BareCard: ...
def card_from(node: dict, place: Place, board_id: str | None, app_login: str) -> BareCard:
    """A card of ``place``'s tracker from one GraphQL issue node, of the type its reader
    read: a :class:`Card`, its item on the board ``board_id`` read too
    (:data:`_queries.CARD_FIELDS`), or, for a reader that reads no board (``board_id``
    None), a :class:`BareCard` (:data:`_queries.BARE_CARD_FIELDS`).  ``app_login`` is the
    reader's: a close by it is the tool's."""
    number = node["number"]
    events = connection_nodes(node["timelineItems"], "close events", number)
    last_actor = ((events[-1].get("actor") or {}).get("login")) if events else None
    is_open = node["state"] == "OPEN"
    links = [(node["parent"], "parent")] if node["parent"] else []
    links += [(child, "sub-issue") for child in _counted(node["subIssues"], "sub-issues", number)]
    links += [(blocker, "blocker")
              for blocker in _counted(node["blockedBy"], "blockers", number)]
    inside = [(link, what) for link, what in links if _is_tracker_issue(link, place)]
    facts = {
        "number": number,
        "id": int(node["fullDatabaseId"]),
        "node_id": node["id"],
        "title": node["title"],
        "kind": (node["issueType"] or {}).get("name"),
        "labels": tuple(label["name"] for label in _counted(node["labels"], "labels", number)),
        "is_open": is_open,
        "state_reason": node["stateReason"],
        "parent": next((link["number"] for link, what in inside if what == "parent"), None),
        "children": tuple(
            Child(link["number"], (link["issueType"] or {}).get("name"), link["state"] == "OPEN")
            for link, what in inside if what == "sub-issue"
        ),
        "blocked_by": tuple(link["number"] for link, what in inside if what == "blocker"),
        "closed_by_tool": not is_open and last_actor == app_login,
        "touched_by_hand": bool(events) and last_actor != app_login,
        "outside": tuple(_outside(link, what) for link, what in links
                         if not _is_tracker_issue(link, place)),
    }
    if board_id is None:
        return BareCard(**facts)
    items = [
        item["id"] for item in _counted(node["projectItems"], "board items", number)
        if item["project"]["id"] == board_id
    ]
    return Card(**facts, board_item=items[0] if items else None)


def numbering(listed: Iterable[int], state_of: Callable[[int], NumberState]) -> Numbering:
    """Read by number every number from #1 that a listing of every card lacks, up to the
    first one above the highest listed that answers 404.

    A deleted issue (410) and a pull request are stepped over.  What it REFUSES, naming the
    number: an issue the listing lacked (GitHub's listing lags a create: read again later);
    a 404 BELOW the highest listed, which GitHub's one sequence leaves only where something
    this token cannot read holds the number; and, raised by ``state_of``, an issue moved to
    another repository (301).

    Args:
        listed: The numbers of every card the listing holds, open and closed.
        state_of: How GitHub answers a REST read of one number
            (:meth:`Tracker.number_state`).

    Returns:
        The deleted numbers and pull requests among the gaps, and the number the read
        stopped at (:class:`Numbering`).

    Raises:
        TrackerError: For each refusal above.
    """
    listed = set(listed)
    highest = max(listed, default=0)
    deleted, pull_requests, number = [], [], 0
    while True:
        number += 1
        if number in listed:
            continue
        state = state_of(number)
        if state is NumberState.ISSUE:
            raise TrackerError(f"#{number} is an issue the listing of every card lacks "
                               "(GitHub's listing lags a create): read it again")
        if state is NumberState.NOT_FOUND:
            if number < highest:
                raise TrackerError(f"#{number} answers 404 below #{highest}, which the "
                                   "listing holds: GitHub gave it out, to something this "
                                   "token cannot read")
            return Numbering(tuple(deleted), tuple(pull_requests), number)
        (deleted if state is NumberState.DELETED else pull_requests).append(number)


def board_of(github: GitHub, place: Place) -> str:
    """The node id of ``place``'s board: found by its title in the place's organization,
    and refused unless the read of its links proves it linked to the place's repository
    (:func:`setup_tracker.linked`), so a board titled like another tracker's is never
    written as this one's.

    Raises:
        TrackerError: When no board has the title, or the one that has it is not linked
            to the place's repository, or a read GitHub cut short leaves that unread.
    """
    board_id = find_board(github, place)
    if board_id is None:
        raise TrackerError(f"{place.full_name} has no board titled {place.board_title!r}; "
                           "run python -m tools.quill.setup_tracker")
    repositories = github.graphql(_queries.BOARD_REPOSITORIES,
                                  id=board_id)["node"]["repositories"]
    link = linked(place, repositories)
    if link is not True:
        names = [node["nameWithOwner"] for node in repositories["nodes"]]
        verdict = (f"not to {place.full_name}" if link is False else
                   f"a read GitHub cut short of its {repositories['totalCount']}, none of "
                   f"them {place.full_name}: whether it is linked is unread")
        raise TrackerError(f"the board titled {place.board_title!r} is linked to {names}, "
                           f"{verdict}")
    return board_id


def read_until(read: Callable[[], list[tuple[int, str]]],
               holds: Callable[[list[tuple[int, str]]], bool],
               sleep: Callable[[float], None]) -> tuple[list[tuple[int, str]], bool]:
    """Read the board (``read``, :meth:`Board.order`) every :data:`POLL_SECONDS` until a read
    of it ``holds``, for at most :data:`BOARD_WAIT_SECONDS`: the board's lag waited out in
    one place.  The last read, and whether it holds."""
    waited = 0
    while True:
        order = read()
        if holds(order):
            return order, True
        if waited >= BOARD_WAIT_SECONDS:
            return order, False
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS


class Board:
    """``place``'s board: the steps and questions in the developer's drag order (R-BAL177).

    ``sleep`` waits out the board's lag; tests pass one that does not wait.
    """

    def __init__(self, github: GitHub, place: Place, board_id: str,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """Read and write the board ``board_id`` (``place``'s, :func:`board_of`) through
        ``github``.  The place is :attr:`location` (:meth:`place` moves a card)."""
        self.github = github
        self.location = place
        self.board_id = board_id
        self._sleep = sleep

    def _write(self, mutation: str, **variables: str) -> dict:
        """Send one of the board's writes, naming this board as ``$p`` and no other: the
        fence every board write passes through."""
        return self.github.graphql(mutation, p=self.board_id, **variables)

    def order(self) -> list[tuple[int, str]]:
        """The board's cards in their drag order: ``(card number, board item id)``."""
        items, _ = paged(partial(self.github.graphql, _queries.BOARD_ORDER, id=self.board_id),
                         lambda answer: answer["node"]["items"])
        return [(item["content"]["number"], item["id"]) for item in items
                if _is_tracker_issue(item["content"] or {}, self.location)]

    def add(self, card: Card) -> str:
        """Put a card on the board (GitHub adds it at the bottom); its board item id."""
        return self._write(BOARD_ADD, c=card.node_id)["addProjectV2ItemById"]["item"]["id"]

    def remove(self, item: str) -> None:
        """Take a card off the board (the card itself is untouched)."""
        self._write(BOARD_REMOVE, i=item)

    def place(self, item: str, after: str | None) -> bool:
        """Move a board item to just after ``after`` (the top when None); whether the
        board shows it there within :data:`BOARD_WAIT_SECONDS`."""
        if after is None:
            self._write(BOARD_TOP, i=item)
        else:
            self._write(BOARD_AFTER, i=item, a=after)
        return self.shows(item, after)

    def shows(self, item: str, after: str | None) -> bool:
        """Read the board until ``item`` sits right after ``after`` (first when None),
        for at most :data:`BOARD_WAIT_SECONDS` (:func:`read_until`)."""
        def placed(order: list[tuple[int, str]]) -> bool:
            items = [item_id for _, item_id in order]
            if item not in items:
                return False
            index = items.index(item)
            return (after is None and index == 0) or bool(index and items[index - 1] == after)
        return read_until(self.order, placed, self._sleep)[1]


class CardReader:
    """A tracker's cards, read by number and without the board: what a token that may read
    the tracker's issues and nothing else reads (built for CI's landing check, X-cx L9d),
    each a :class:`BareCard`.  :class:`Tracker` is one that reads each card's board item too.

    ``github`` is any object with :class:`_github.GitHub`'s ``graphql_lookup``, and
    ``place`` the tracker's :class:`setup_tracker.Place`.  ``app_login``, by which a close is
    the tool's or a person's (:func:`card_from`), is :data:`setup_tracker.APP_LOGIN` unless
    given: a token that is no App's cannot ask GitHub which App is quill's, so this reader
    takes the declared login, which every :meth:`Tracker.connect` checks against GitHub.
    Whether such a token can read every field it asks for is unmeasured until X-cx L9d a3
    reads the tracker with one.
    """

    def __init__(self, github: GitHub, place: Place, app_login: str = APP_LOGIN) -> None:
        """Read ``place``'s cards through ``github`` as ``app_login`` reads closes."""
        self.github = github
        self.place = place
        self.app_login = app_login

    def _card(self, node: dict) -> BareCard:
        """A :class:`BareCard` of this tracker from one GraphQL issue node."""
        return card_from(node, self.place, None, self.app_login)

    def cards(self, numbers: Iterable[int]) -> dict[int, BareCard]:
        """The cards numbered ``numbers``, open or closed; one that does not exist is
        absent from the answer (a trailer can name a number nobody filed)."""
        return self._lookup(numbers, _queries.BARE_CARD_FIELDS, self._card)

    def _lookup[T](self, numbers: Iterable[int], card_fields: str,
                   card: Callable[[dict], T]) -> dict[int, T]:
        """The cards numbered ``numbers`` that exist, each read with ``card_fields`` and
        built by ``card``: the one walk of a read by number, which each reader's
        :meth:`cards` names its fields and its card type to (and
        :meth:`Tracker.milestones_of` its one field).  A number past
        :data:`GRAPHQL_INT_MAX` is never asked, so it is absent like one nobody filed: asked,
        it is an argument GraphQL's ``Int`` cannot hold, which the specification's input
        coercion refuses, and with it the read's every other card (GitHub's own answer is
        unmeasured)."""
        wanted = sorted(number for number in set(numbers) if number <= GRAPHQL_INT_MAX)
        found = {}
        for start in range(0, len(wanted), _PAGE // 2):
            batch = wanted[start:start + _PAGE // 2]
            fields = " ".join(f"c{n}: issue(number: {n}) {{ {card_fields} }}" for n in batch)
            answer = self.github.graphql_lookup(
                f'query {{ repository(owner: "{self.place.owner}", name: "{self.place.name}") '
                f'{{ {fields} }} }}'
            )["repository"]
            for number in batch:
                if answer[f"c{number}"] is not None:
                    found[number] = card(answer[f"c{number}"])
        return found


class Tracker(CardReader):  # pylint: disable=too-many-public-methods
    """A tracker as the App sees it: its cards, each with its board item, and its
    :class:`Board`.

    Pylint: ``too-many-public-methods`` (29/20) -- ``connect``, then **one
    method per read or write quill makes of the tracker** (most one
    request; this module is the one place it speaks to GitHub), the board's
    own writes already apart in :class:`Board`.  The filing mark's read and its
    removal (R-BAL202), a leaf's unlink (R-BAL205), ``show``'s read of a card's
    comments, X-cx's migration's (L7) whole-card read, its question of what a
    number is, its read and make of milestones and its close of a ruling with
    its mark's removal in one write, and its grade's (R-BAL355) render of a
    card's Markdown as GitHub does here took it past 20.  The claims' three
    reads and writes and the read of a commit's pull requests left it at X-cx's
    M1: a claim is a branch of the code repository since R-BAL359 (``_git``).
    ``quill file step --parent``'s read of the milestones a split step and its
    leaves are in, and its move of a card's milestone (the split step's to its
    leaf, and back when the leaf is dropped unfinished), came with X-cx's S3a
    (measured 29 then; 27 before it, where this line said 26).

    ``github`` is any object with :class:`_github.GitHub`'s ``rest``,
    ``graphql`` and ``graphql_lookup``.  The tracker's :class:`setup_tracker.Place`
    is its board's (:attr:`Board.location`), its one home.
    """

    def __init__(self, github: GitHub, board: Board, app_login: str) -> None:
        """Read and write through ``github`` as ``app_login``, on ``board``'s place."""
        super().__init__(github, board.location, app_login)
        self.board = board
        self.board_id = board.board_id

    @classmethod
    def connect(cls, place: Place) -> Tracker:
        """``place``'s tracker, through a fresh installation token of quill's App narrowed to
        the place's repository, and its board (:func:`board_of`).

        Raises:
            TrackerError: Before it asks GitHub for an installation or a token, when the
                App's credentials sign in as an App that is not quill's
                (:func:`setup_tracker.another_app`): its closes would be read as a person's,
                and the card-only reader, which reads the declared login, would disagree.
        """
        client_id, key = app_credentials()
        jwt = app_jwt(client_id, key, int(time.time()))
        login = GitHub(jwt).rest("GET", "/app")["slug"]
        if wrong := another_app(login):
            raise TrackerError(wrong)
        installation = app_installation(place.owner, jwt)["id"]
        github = GitHub(installation_token(installation, jwt, repository=place.name))
        return cls(github, Board(github, place, board_of(github, place)), APP_LOGIN)

    # -- reads ---------------------------------------------------------------

    def _card(self, node: dict) -> Card:
        """A :class:`Card` of this tracker from one GraphQL issue node, its board item read."""
        return card_from(node, self.place, self.board_id, self.app_login)

    def cards(self, numbers: Iterable[int]) -> dict[int, Card]:
        """The cards numbered ``numbers``, open or closed, each with its board item; one that
        does not exist is absent from the answer (a trailer can name a number nobody
        filed)."""
        return self._lookup(numbers, _queries.CARD_FIELDS, self._card)

    def _listing(self, query: str) -> dict[int, Card]:
        """Every card a listing ``query`` holds, by number."""
        return {node["number"]: self._card(node) for node in listing(self.github, query)}

    def open_cards(self) -> dict[int, Card]:
        """Every open card, by number."""
        return self._listing(_queries.open_cards(self.place))

    def marked(self) -> dict[int, Card]:
        """Every card carrying the :data:`setup_tracker.FILING` mark, open or closed, by
        number: each a filing quill began; whether it is still unfinished is
        ``_state.filing_unfinished``'s answer (one dropped while marked is not)."""
        return self._listing(_queries.marked_cards(self.place))

    def milestones_of(self, numbers: Iterable[int]) -> dict[int, int | None]:
        """The number of the milestone each card numbered ``numbers`` is in, None for none;
        one that does not exist is absent: ``quill file step --parent``'s read of a split
        step's milestone and its leaves' (``_filing``), the one field it reads, so no listing
        a recording replays changes."""
        return self._lookup(numbers, "milestone { number }",
                            lambda node: (node["milestone"] or {}).get("number"))

    def all_cards(self) -> dict[int, WholeCard]:
        """Every card, open and closed, by number, each WHOLE: its body and every comment.

        The listing carries a card's first hundred comments; a card with more has them
        all read by :meth:`comments`, so no card's comments are cut short.
        """
        cards = {}
        for node in listing(self.github, _queries.all_cards(self.place)):
            number, held = node["number"], node["comments"]
            comments = ([_comment(each) for each in held["nodes"]]
                        if held["totalCount"] <= len(held["nodes"]) else self.comments(number))
            cards[number] = WholeCard(self._card(node), node["body"] or "", tuple(comments),
                                      (node["milestone"] or {}).get("number"))
        return cards

    def milestones(self) -> dict[str, Milestone]:
        """Every milestone, open and closed, by title, read page by page.

        X-cx's migration finds each outcome's milestone by its title (L7 draft 4 s.11
        V3-5), so a lost create's answer is found rather than made twice.

        Raises:
            TrackerError: When two milestones share a title, which would make the one
                found by it a guess.
        """
        found, page = {}, 1
        while True:
            more = "" if page == 1 else f"&page={page}"
            listed = self.github.rest(
                "GET", f"{self.place.path}/milestones?state=all&per_page=100{more}") or []
            for node in listed:
                if node["title"] in found:
                    raise TrackerError(f"two milestones of {self.place.full_name} are titled "
                                       f"{node['title']!r}: #{found[node['title']].number} and "
                                       f"#{node['number']}")
                found[node["title"]] = Milestone(node["number"], node["title"],
                                                 node["description"] or "")
            if len(listed) < 100:
                return found
            page += 1

    def number_state(self, number: int) -> NumberState:
        """What GitHub answers a REST read of issue ``number`` with (:func:`numbering`).

        Raises:
            TrackerError: For an issue moved to another repository (GitHub's 301, which
                :class:`_github.GitHub` never follows).
            GitHubError: For any other refusal.
        """
        try:
            issue = self.github.rest("GET", f"{self.place.path}/issues/{number}")
        except GitHubError as error:
            if error.status == 410:
                return NumberState.DELETED
            if error.status == 404:
                return NumberState.NOT_FOUND
            if error.status == 301:
                raise TrackerError(f"#{number} of {self.place.full_name} was moved to another "
                                   "repository (301)") from error
            raise
        return NumberState.PULL_REQUEST if "pull_request" in issue else NumberState.ISSUE

    def body(self, number: int) -> str:
        """A card's body as it stands."""
        return self.github.rest("GET", f"{self.place.path}/issues/{number}")["body"] or ""

    def render(self, text: str) -> str:
        """``text`` as GitHub renders a card's Markdown in this tracker, as HTML: its
        Markdown API in ``gfm`` mode with the tracker as context, which links ``#N`` to card
        N where card N exists, and stores nothing (X-cx's migration grades the cards'
        numbers by it, ``_migrate_numbers.grade_rendered``)."""
        return self.github.rest_text("POST", "/markdown", {
            "text": text, "mode": "gfm", "context": self.place.full_name})

    def blocking(self, number: int) -> tuple[Child, ...]:
        """The cards of this tracker that card ``number`` blocks, each with its type and state
        as they stand (:func:`_queries.blocking`): a step's are the findings and rulings it
        owns (ruling ``balance:R-BAL395``), beside any card waiting on it.  An issue outside
        the tracker is no card, and is passed over.

        Raises:
            TrackerError: When GitHub answers the card as null, or cut the list short.
        """
        issue = self.github.graphql(_queries.blocking(self.place),
                                    number=number)["repository"]["issue"]
        if issue is None:
            raise TrackerError(f"plan#{number} could not be read: GitHub answered it as null")
        return tuple(
            Child(node["number"], (node["issueType"] or {}).get("name"), node["state"] == "OPEN")
            for node in _counted(issue["blocking"], "cards it blocks", number)
            if _is_tracker_issue(node, self.place))

    def edits(self, number: int) -> tuple[str, list[Edit]]:
        """A card's body as it stands and every saved version of it, oldest first.

        GitHub keeps one entry per saved edit, each holding the FULL body (not a
        diff; measured 2026-10-04), and on the first edit adds one for the body
        as filed.  A card never edited has none: its body is the filed text.
        """
        nodes, last = saved_versions(self.github, self.place, number)
        versions = [Edit(node["id"], node["editedAt"], (node["editor"] or {}).get("login"),
                         node["diff"] or "") for node in nodes]
        issue = last["repository"]["issue"]
        body = issue["body"] or ""
        if not versions:
            versions = [Edit(None, issue["createdAt"], (issue["author"] or {}).get("login"), body)]
        return body, sorted(versions, key=lambda edit: edit.edited_at)

    def comments(self, number: int) -> list[Comment]:
        """Every comment on a card, oldest first, read page by page.  A number nobody
        filed is GitHub's NOT_FOUND error, which :meth:`_github.GitHub.graphql` raises."""
        return [_comment(node) for node in comment_nodes(self.github, self.place, number)]

    def find_titles(self, text: str) -> list[tuple[int, str]]:
        """Cards whose title holds ``text`` (GitHub's search: words, not characters), read
        page by page until every hit is read.

        Raises:
            TrackerError: When GitHub says its search timed out (``incomplete_results``),
                finds more hits than it serves (:data:`_SEARCH_CAP`), or answers a page
                empty before every hit is read, so no answer would be every card.
        """
        query = quote(f'repo:{self.place.full_name} in:title "{text}"', safe="")
        items, page = [], 1
        while True:
            more = "" if page == 1 else f"&page={page}"
            found = self.github.rest("GET", f"/search/issues?q={query}&per_page=100{more}")
            if found["incomplete_results"] or found["total_count"] > _SEARCH_CAP:
                raise TrackerError(
                    f"GitHub's title search for {text!r} finds {found['total_count']} "
                    f"(incomplete: {found['incomplete_results']}); it serves {_SEARCH_CAP} "
                    "at most and every one: name the card as plan#N")
            if not found["items"] and len(items) < found["total_count"]:
                raise TrackerError(
                    f"GitHub's title search for {text!r} answered page {page} empty after "
                    f"{len(items)} of {found['total_count']} hits: name the card as plan#N")
            items += found["items"]
            if len(items) >= found["total_count"]:
                break
            page += 1
        return [(item["number"], item["title"]) for item in items
                if "pull_request" not in item]

    # -- writes --------------------------------------------------------------

    def create(self, kind: str, title: str, body: str, labels: Iterable[str],
               milestone: int | None = None) -> int:
        """File a card, marked :data:`setup_tracker.FILING` beside ``labels`` in this, its
        first write (R-BAL202): every card quill files is born marked, and its
        filing's last write, :meth:`unmark`, removes the mark.  Its number, once GitHub's
        answer shows the type, labels and milestone sent.

        ``milestone``: the number of the milestone the card is filed in: X-cx's migration's
        (L7 draft 4 s.3: a step's create carries its outcome's, a split step's carried to its
        leaves instead, ``_migrate_source._carried_outcomes``), and a leaf's that ``quill file
        step --parent`` files in its split step's while that step holds one (``_filing``).
        None sends no milestone at all, so every other create's request is what it was.

        A create naming a label the tracker lacks makes that label (grey, no description;
        measured 2026-10-04 on the mark itself, before it existed; ``setup_tracker.py
        --apply`` then corrected it), so a card filed before the mark exists is still
        born marked.

        **A retry may file a second card.**  A create refused for a rate limit is re-sent
        on GitHub's word (:mod:`_github`), whose pages do not say whether the refused one
        was performed.  The extra is born marked like the first, so ``quill file`` run again
        finds both by kind, title and text and refuses while two stand
        (``_filing._same_filing``); this answer is the re-sent create's card.
        """
        labels = sorted({*labels, FILING})
        sent = {"title": title, "body": body, "type": kind, "labels": labels}
        if milestone is not None:
            sent["milestone"] = milestone
        issue = self.github.rest("POST", f"{self.place.path}/issues", sent)
        got = ((issue.get("type") or {}).get("name"), sorted(l["name"] for l in issue["labels"]))
        if got != (kind, labels):
            raise TrackerError(
                f"plan#{issue['number']} was filed as {got}, not {(kind, labels)}: fix it by hand"
            )
        held = (issue.get("milestone") or {}).get("number")
        if held != milestone:
            raise TrackerError(f"plan#{issue['number']} was filed in milestone {held}, not "
                               f"{milestone}: fix it by hand")
        return issue["number"]

    def create_milestone(self, title: str, description: str) -> int:
        """Make a milestone (X-cx's migration, one per outcome, R-BAL243); its number."""
        return self.github.rest("POST", f"{self.place.path}/milestones",
                                {"title": title, "description": description})["number"]

    def set_milestone(self, number: int, milestone: int | None) -> None:
        """Put a card in the milestone numbered ``milestone``, or with None take it out of its
        one, reading it back: a step split into smaller steps keeps none once its leaf carries
        it (``_filing``), and gets it back from a leaf dropped before its filing finished
        (``quill._unsplit``).

        Raises:
            TrackerError: When GitHub's answer shows the card in any other milestone, or none.
        """
        issue = self.github.rest("PATCH", f"{self.place.path}/issues/{number}",
                                 {"milestone": milestone})
        held = (issue.get("milestone") or {}).get("number")
        if held != milestone:
            raise TrackerError(f"plan#{number} is in milestone {held}, not {milestone}: fix it "
                               "by hand")

    def retype(self, number: int, kind: str) -> None:
        """Change a card's kind, reading it back."""
        issue = self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"type": kind})
        if (issue.get("type") or {}).get("name") != kind:
            raise TrackerError(f"GitHub did not make plan#{number} a {kind}")

    def retitle(self, number: int, title: str) -> None:
        """Rename a card."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"title": title})

    def set_body(self, number: int, body: str) -> None:
        """Replace a card's body (its spec, for a step)."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"body": body})

    def comment(self, number: int, text: str) -> None:
        """Add a comment to a card.

        A retry may add it twice, as a create may file twice (:meth:`create`); no command
        reads how many comments a card has, so a doubled note is shown on the card and
        decides nothing."""
        self.github.rest("POST", f"{self.place.path}/issues/{number}/comments", {"body": text})

    def close(self, number: int, reason: str) -> None:
        """Close a card: ``completed`` (it shipped) or ``not_planned`` (it was dropped)."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}",
                         {"state": "closed", "state_reason": reason})

    def reopen(self, number: int) -> None:
        """Reopen a card."""
        self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {"state": "open"})

    def unmark(self, number: int) -> None:
        """Remove a card's :data:`setup_tracker.FILING` mark: its filing's last write."""
        self.github.rest("DELETE", f"{self.place.path}/issues/{number}/labels/{FILING}")

    def close_unmarked(self, number: int, labels: Iterable[str]) -> None:
        """Close a card as completed AND remove its :data:`setup_tracker.FILING` mark, in ONE
        write: a migrated ruling's last (L7 draft 4 s.11 V3-8, one write where
        :meth:`close` and :meth:`unmark` are two).  ``labels`` are the card's as read; the
        write sets them without the mark, so it is sent only during the cutover's freeze,
        when nothing else labels a card.  GitHub's answer is read back.

        Raises:
            TrackerError: When the answer shows the card open, closed for another reason,
                or labelled otherwise.
        """
        kept = sorted(set(labels) - {FILING})
        issue = self.github.rest("PATCH", f"{self.place.path}/issues/{number}", {
            "state": "closed", "state_reason": "completed", "labels": kept})
        got = (issue["state"], issue.get("state_reason"),
               sorted(label["name"] for label in issue["labels"]))
        if got != ("closed", "completed", kept):
            raise TrackerError(f"plan#{number} reads {got} after its close and unmark, not "
                               f"{('closed', 'completed', kept)}: fix it by hand")

    def add_child(self, parent: int, child: Card) -> None:
        """Make ``child`` a sub-issue of ``parent``."""
        self.github.rest("POST", f"{self.place.path}/issues/{parent}/sub_issues",
                         {"sub_issue_id": child.id})

    def remove_child(self, parent: int, child: Card) -> None:
        """Unlink ``child`` from ``parent``, whose sub-issue it is."""
        self.github.rest("DELETE", f"{self.place.path}/issues/{parent}/sub_issue",
                         {"sub_issue_id": child.id})

    def block(self, number: int, blocker: Card) -> None:
        """Record that ``number`` is blocked by ``blocker``."""
        self.github.rest("POST", f"{self.place.path}/issues/{number}/dependencies/blocked_by",
                         {"issue_id": blocker.id})

    def unblock(self, number: int, blocker: Card) -> None:
        """Remove the record that ``number`` is blocked by ``blocker``."""
        self.github.rest(
            "DELETE", f"{self.place.path}/issues/{number}/dependencies/blocked_by/{blocker.id}"
        )
