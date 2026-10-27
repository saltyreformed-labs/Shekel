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
command.  A write cannot reach another tracker's cards or board:

- the App's token is minted NARROWED to the place's one repository
  (:meth:`Tracker.connect`).  That narrows repository access only: the board is an
  ORGANIZATION project, which the token may still reach by node id;
- so the board is resolved by its title in the place's organization and refused
  unless it is linked to the place's repository (:func:`board_of`), and every board
  write is sent through :meth:`Board._write`, which names that board and no other.

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
from functools import partial
from typing import overload
from urllib.parse import quote

from tools.quill import _queries
from tools.quill._arcs import ARC_FIELD, arc_answered, arc_labels, arc_of
from tools.quill._draft import Draft
from tools.quill._github import (
    GitHub,
    app_credentials,
    app_installation,
    app_jwt,
    installation_token,
    paged,
    reading,
)
from tools.quill.setup_tracker import (
    APP_LOGIN,
    FILING,
    IN_ORDER,
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
#: How many cards one read by number asks for (:meth:`CardReader._lookup`), inside GitHub's
#: limit of about ten seconds a request.  Measured 2026-10-26 15:27-15:33 on the tracker,
#: the slowest batch in each of three rounds: of 50 cards, 1.8-2.5 s; of 25, 1.4 s.
_LOOKUP_BATCH = 25
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

    Pylint: too-many-instance-attributes (15/7) -- **fifteen because a card
    states fifteen facts the plan reads without the board**, each its own GitHub
    field, and no subset of them travels apart from the others.

    ``arc``: the value of its issue field Arc, the arc's one home (rulings
    ``balance:R-BAL349``, ``R-BAL354``; :mod:`_arcs`), None when it holds none; its arc label
    is display.

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
    arc: str | None
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


def listing(github: GitHub, query: str, what: str) -> list[dict]:
    """Every issue node a listing ``query`` holds (:func:`_queries.open_cards`,
    :func:`_queries.marked_cards`, :func:`_queries.export_cards`), read page by page
    (:func:`_github.paged`); ``what`` names the listing in a failure's line."""
    return paged(partial(github.graphql, query),
                 lambda answer: answer["repository"]["issues"], what)[0]


def saved_versions(github: GitHub, place: Place, number: int) -> tuple[list[dict], dict]:
    """Every saved version of card ``number``'s text as GitHub answers it
    (:func:`_queries.edits`), and the last answer read, which holds the card's text, who
    filed it and when.  A number nobody filed is GitHub's NOT_FOUND error, which
    :meth:`_github.GitHub.graphql` raises: a command refuses one before it reads here
    (``_command._one``)."""
    return paged(partial(github.graphql, _queries.edits(place), number=number),
                 lambda answer: answer["repository"]["issue"]["userContentEdits"],
                 f"plan#{number}'s saved versions")


def comment_nodes(github: GitHub, place: Place, number: int,
                  fields: str = _queries.COMMENT_FIELDS) -> list[dict]:
    """Every comment on card ``number`` as GitHub answers it, each read as ``fields``
    (:func:`_queries.comments`)."""
    return paged(partial(github.graphql, _queries.comments(place, fields), number=number),
                 lambda answer: answer["repository"]["issue"]["comments"],
                 f"plan#{number}'s comments")[0]


def comment_versions(github: GitHub, comment: str) -> list[dict]:
    """Every saved version of the comment whose node id is ``comment``, as GitHub answers
    it (:data:`_queries.COMMENT_EDITS`)."""
    return paged(partial(github.graphql, _queries.COMMENT_EDITS, id=comment),
                 lambda answer: answer["node"]["userContentEdits"],
                 f"comment {comment}'s saved versions")[0]


def milestone_nodes(github: GitHub, place: Place) -> list[dict]:
    """Every milestone of ``place``, open and closed, as GitHub answers it
    (:func:`_queries.milestones`)."""
    return paged(partial(github.graphql, _queries.milestones(place)),
                 lambda answer: answer["repository"]["milestones"], "the milestones")[0]


def _is_tracker_issue(node: dict, place: Place) -> bool:
    """Whether a link or a board item's content names an issue of ``place``'s tracker: the
    one test, for a parent, a sub-issue, a blocker and the board alike, on the whole
    ``owner/name`` token."""
    return (node.get("repository") or {}).get("nameWithOwner") == place.full_name


def filed_labels(labels: Iterable[str], arc: str | None) -> list[str]:
    """The labels a create sends for a card filed in ``arc`` (None: a task, which holds no
    arc) beside ``labels``: those, its arc's label as display of its Arc
    (:func:`_arcs.arc_labels`, ruling ``balance:R-BAL354``) and the :data:`setup_tracker.FILING`
    mark (R-BAL202), sorted, as GitHub's answer is read back.  The one composition, which
    :meth:`Tracker.create` sends and the tests' tracker files by."""
    return sorted({*labels, *arc_labels(arc), FILING})


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
        "arc": arc_of(_counted(node["issueFieldValues"], "issue field values", number)),
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


def board_of(github: GitHub, place: Place) -> str:
    """The node id of ``place``'s board: found by its title in the place's organization,
    and refused unless the read of its links proves it linked to the place's repository
    (:func:`setup_tracker.linked`), so a board titled like another tracker's is never
    written as this one's.

    Raises:
        TrackerError: When no board has the title, or the one that has it is not linked
            to the place's repository, or a read GitHub cut short leaves that unread.
    """
    with reading(f"the board titled {place.board_title!r}"):
        board_id = find_board(github, place)
    if board_id is None:
        raise TrackerError(f"{place.full_name} has no board titled {place.board_title!r}; "
                           "run python -m tools.quill.setup_tracker")
    with reading(f"the repositories the board titled {place.board_title!r} is linked to"):
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


def read_until[T](read: Callable[[], list[T]], holds: Callable[[list[T]], bool],
                  sleep: Callable[[float], None]) -> tuple[list[T], bool]:
    """Read the board (``read``, :meth:`Board.held`) every :data:`POLL_SECONDS` until a read
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


def in_order(held: Iterable[tuple[int, str, str | None]]) -> list[tuple[int, str]]:
    """The developer's drag order (R-BAL177) of what a board holds (:meth:`Board.held`), its
    steps and questions alone (:data:`setup_tracker.IN_ORDER`): ``(card number, board item
    id)``.  The one projection, which :meth:`Board.order` and the tests' board both read."""
    return [(number, item) for number, item, kind in held if kind in IN_ORDER]


class Board:
    """``place``'s board: the steps and questions in the developer's drag order (R-BAL177),
    and each step's tasks, which nest under it in the Plan view and are in no order (ruling
    plan#1814).  :meth:`order` is that order, read by each item's issue type, so no reader
    of it meets a task.

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

    def held(self) -> list[tuple[int, str, str | None]]:
        """Every card the board holds, in its positions: ``(card number, board item id, issue
        type)``.  An item that is no issue of the tracker (a draft, a deleted issue, another
        repository's) is no card, and is passed over."""
        items, _ = paged(partial(self.github.graphql, _queries.BOARD_ORDER, id=self.board_id),
                         lambda answer: answer["node"]["items"], "the board's order")
        return [(item["content"]["number"], item["id"],
                 (item["content"]["issueType"] or {}).get("name")) for item in items
                if _is_tracker_issue(item["content"] or {}, self.location)]

    def order(self) -> list[tuple[int, str]]:
        """The board's cards in the developer's drag order, its steps and questions alone
        (:func:`in_order`): ``(card number, board item id)``."""
        return in_order(self.held())

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
        for at most :data:`BOARD_WAIT_SECONDS` (:func:`read_until`).  It reads every item's
        position (:meth:`held`), not the order: a move is a write of positions, which it
        makes whatever the item's type."""
        def placed(positions: list[tuple[int, str, str | None]]) -> bool:
            items = [item_id for _, item_id, _ in positions]
            if item not in items:
                return False
            index = items.index(item)
            return (after is None and index == 0) or bool(index and items[index - 1] == after)
        return read_until(self.held, placed, self._sleep)[1]


class CardReader:
    """A tracker's cards, read by number and without the board: what a token that may read
    the tracker's issues and nothing else reads (built for CI's landing check, X-cx L9d),
    each a :class:`BareCard`.  :class:`Tracker` is one that reads each card's board item too.

    ``github`` is any object with :class:`_github.GitHub`'s ``graphql_lookup`` and
    ``graphql``, and ``place`` the tracker's :class:`setup_tracker.Place`.  ``app_login``, by
    which a close is the tool's or a person's (:func:`card_from`), is
    :data:`setup_tracker.APP_LOGIN` unless given: a token that is no App's cannot ask GitHub
    which App is quill's, so this reader takes the declared login, which every
    :meth:`Tracker.connect` checks against GitHub.  Whether such a token can read every field
    it asks for is unmeasured until X-cx L9d a3 reads the tracker with one, and its read of
    what a card blocks (:meth:`blocking`) until plan#1772's own pull request runs the landing
    check.
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

    def blocking(self, number: int) -> tuple[Child, ...]:
        """The cards of this tracker that card ``number`` blocks, each with its type and state
        as they stand (:func:`_queries.blocking`): a step's are the findings and rulings it
        owns (ruling ``balance:R-BAL395``), beside any card waiting on it.  An issue outside
        the tracker is no card, and is passed over.

        Raises:
            TrackerError: When GitHub answers the card as null, or cut the list short.
        """
        with reading(f"the cards plan#{number} blocks"):
            issue = self.github.graphql(_queries.blocking(self.place),
                                        number=number)["repository"]["issue"]
        if issue is None:
            raise TrackerError(f"plan#{number} could not be read: GitHub answered it as null")
        return tuple(
            Child(node["number"], (node["issueType"] or {}).get("name"), node["state"] == "OPEN")
            for node in _counted(issue["blocking"], "cards it blocks", number)
            if _is_tracker_issue(node, self.place))

    def _lookup[T](self, numbers: Iterable[int], card_fields: str,
                   card: Callable[[dict], T]) -> dict[int, T]:
        """The cards numbered ``numbers`` that exist, each read with ``card_fields`` and
        built by ``card``: the one walk of a read by number, which each reader's
        :meth:`cards` names its fields and its card type to (and
        :meth:`Tracker.milestones_of` its one field).  A number past
        :data:`GRAPHQL_INT_MAX` is never asked, so it is absent like one nobody filed: asked,
        it is an argument GraphQL's ``Int`` cannot hold, which the specification's input
        coercion refuses, and with it the read's every other card (GitHub's own answer is
        unmeasured).  A batch's failure names the batch (:func:`_github.reading`)."""
        wanted = sorted(number for number in set(numbers) if number <= GRAPHQL_INT_MAX)
        found = {}
        batches = -(-len(wanted) // _LOOKUP_BATCH)
        for start in range(0, len(wanted), _LOOKUP_BATCH):
            batch = wanted[start:start + _LOOKUP_BATCH]
            fields = " ".join(f"c{n}: issue(number: {n}) {{ {card_fields} }}" for n in batch)
            with reading(f"the cards by number, batch {start // _LOOKUP_BATCH + 1} of "
                         f"{batches}, plan#{batch[0]}-plan#{batch[-1]}"):
                answer = self.github.graphql_lookup(
                    f'query {{ repository(owner: "{self.place.owner}", '
                    f'name: "{self.place.name}") {{ {fields} }} }}'
                )["repository"]
            for number in batch:
                if answer[f"c{number}"] is not None:
                    found[number] = card(answer[f"c{number}"])
        return found


class Tracker(CardReader):  # pylint: disable=too-many-public-methods
    """A tracker as the App sees it: its cards, each with its board item, and its
    :class:`Board`.

    Pylint: ``too-many-public-methods`` (22/20, measured at plan#1772, which moved the read
    of what a card blocks down to :class:`CardReader`, where CI's landing check reads it) --
    ``connect``, then **one method per read or write quill makes of the tracker**
    (most one request; this module is the one place it speaks to GitHub), the
    board's own writes already apart in :class:`Board`: among them the filing
    mark's read and its removal (R-BAL202), a leaf's unlink (R-BAL205), ``show``'s
    read of a card's comments, and ``quill file step --parent``'s read of the
    milestones a split step and its leaves are in and its move of a card's
    milestone (the split step's to its leaf, and back when the leaf is dropped
    unfinished; X-cx's S3a).  The claims' three reads and writes and the read of a
    commit's pull requests left at X-cx's M1: a claim is a branch of the code
    repository since R-BAL359 (``_git``).  X-cx's migration's six (its whole-card
    read, its question of what a number is, its read and make of milestones, its
    close of a ruling with its mark's removal in one write, and its grade's
    (R-BAL355) render of a card's Markdown) left with the migration at X-cx's S3b
    (29 before it).

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
        with reading("quill's App"):
            login = GitHub(jwt).rest("GET", "/app")["slug"]
        if wrong := another_app(login):
            raise TrackerError(wrong)
        with reading(f"quill's App's installation on {place.owner}"):
            installation = app_installation(place.owner, jwt)["id"]
        with reading(f"quill's App's token for {place.full_name}"):
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

    def _listing(self, query: str, what: str) -> dict[int, Card]:
        """Every card a listing ``query`` holds, by number; ``what`` names it in a failure's
        line."""
        return {node["number"]: self._card(node) for node in listing(self.github, query, what)}

    def open_cards(self) -> dict[int, Card]:
        """Every open card, by number."""
        return self._listing(_queries.open_cards(self.place), "the open cards")

    def marked(self) -> dict[int, Card]:
        """Every card carrying the :data:`setup_tracker.FILING` mark, open or closed, by
        number: each a filing quill began; whether it is still unfinished is
        ``_state.filing_unfinished``'s answer (one dropped while marked is not)."""
        return self._listing(_queries.marked_cards(self.place), "the marked cards")

    def milestones_of(self, numbers: Iterable[int]) -> dict[int, int | None]:
        """The number of the milestone each card numbered ``numbers`` is in, None for none;
        one that does not exist is absent: ``quill file step --parent``'s read of a split
        step's milestone and its leaves' (``_filing``), the one field it reads, so no listing
        a recording replays changes."""
        return self._lookup(numbers, "milestone { number }",
                            lambda node: (node["milestone"] or {}).get("number"))

    def body(self, number: int) -> str:
        """A card's body as it stands."""
        with reading(f"plan#{number}'s body"):
            return self.github.rest("GET", f"{self.place.path}/issues/{number}")["body"] or ""

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
            with reading(f"the title search for {text!r}, page {page}"):
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

    def create(self, card: Draft, milestone: int | None = None) -> int:
        """File ``card``, the card ``check`` passed (its owner is linked by a write of its
        own), marked :data:`setup_tracker.FILING` beside its labels in this, its first write
        (R-BAL202): every card quill files is born marked, and its filing's last write,
        :meth:`unmark`, removes the mark.  Its number, once GitHub's answer shows the type,
        labels, Arc and milestone sent.

        Its arc is written in the same call to its issue field Arc, the arc's one home, and
        as its arc label beside, display of it (rulings ``balance:R-BAL349``, ``R-BAL354``;
        :func:`filed_labels`); the Arc's id is read first (:meth:`_arc_field_id`).  A task,
        which holds neither (R-BAL386), sends no field and reads no id, so its request is
        what it was.  GitHub drops a field value silently where the feature is off (its REST
        description), so the Arc is read back as the type is.

        ``milestone``: the number of the milestone the card is filed in: a leaf's that
        ``quill file step --parent`` files in its split step's while that step holds one
        (``_filing``).  None sends no milestone at all, so every other create's request is
        what it was.

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
        labels = filed_labels(card.labels, card.arc)
        sent = {"title": card.title, "body": card.body, "type": card.kind, "labels": labels}
        if card.arc is not None:
            sent["issue_field_values"] = [{"field_id": self._arc_field_id(), "value": card.arc}]
        if milestone is not None:
            sent["milestone"] = milestone
        issue = self.github.rest("POST", f"{self.place.path}/issues", sent)
        got = ((issue.get("type") or {}).get("name"), sorted(l["name"] for l in issue["labels"]),
               arc_answered(issue.get("issue_field_values") or []))
        if got != (wanted := (card.kind, labels, card.arc)):
            raise TrackerError(f"plan#{issue['number']} was filed as {got}, not {wanted}: fix it "
                               "by hand")
        held = (issue.get("milestone") or {}).get("number")
        if held != milestone:
            raise TrackerError(f"plan#{issue['number']} was filed in milestone {held}, not "
                               f"{milestone}: fix it by hand")
        return issue["number"]

    def _arc_field_id(self) -> int:
        """The REST id of the tracker's issue field :data:`_arcs.ARC_FIELD`, read
        (:func:`_queries.issue_fields`) for the create that writes a card's Arc.

        Raises:
            TrackerError: When the repository holds no such field, or the read GitHub cut
                short does not hold it.
        """
        with reading(f"{self.place.full_name}'s issue field {ARC_FIELD!r}"):
            fields = self.github.graphql(_queries.issue_fields(self.place))[
                "repository"]["issueFields"]
        found = [node["fullDatabaseId"] for node in fields["nodes"]
                 if (node or {}).get("name") == ARC_FIELD]
        if len(found) != 1:
            raise TrackerError(
                f"{self.place.full_name} holds {len(found)} issue fields named {ARC_FIELD!r} "
                f"in a read of {len(fields['nodes'])} of its {fields['totalCount']}: run "
                "python -m tools.quill.setup_tracker")
        return int(found[0])

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
