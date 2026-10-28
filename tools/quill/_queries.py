"""The GraphQL text of each read quill makes of a tracker: the fields of a card, each
listing of cards, a card's histories, and the board's order and links.

What pins each text: the recordings (``_recorded.Replay``) replay most of them word for
word, so a change to one no longer matches its recording: re-record it, never edit an
answer by hand.  One no recording holds: :data:`BARE_CARD_FIELDS` (graded against the
recorded lookup, less its board items, by ``test__tracker_card_reader.py``).

Split out of :mod:`_tracker`, which sends them and reads their answers, at X-cx L9d's
card-only reader, whose change would otherwise take that module past the 1,000 lines
``too-many-lines`` allows.  The lookups of cards by number compose these fields there,
and the board's writes stay there beside :meth:`_tracker.Board._write`, the one door they
are sent through.
"""
from __future__ import annotations

from tools.quill._arcs import LABELS_AND_ARC
from tools.quill._board_fields import GITHUB_STATUS_FIELD
from tools.quill.setup_tracker import FILING, LINKED_REPOSITORIES, Place

#: What a card says of itself and its links, read first: its labels and its arc among them, the
#: value of its issue field Arc (:data:`_arcs.LABELS_AND_ARC`, rulings ``R-BAL349``, ``R-BAL354``).
_ISSUE_FIELDS = """
  id number fullDatabaseId title state stateReason
  issueType { name }
""" + LABELS_AND_ARC + """\
  parent { number repository { nameWithOwner } }
  subIssues(first: 100) { totalCount nodes {
    number state issueType { name } repository { nameWithOwner } } }
  blockedBy(first: 100) { totalCount nodes { number repository { nameWithOwner } } }
"""
#: A card's items on the organization's boards, which only a reader of the board reads.
_BOARD_ITEM_FIELDS = """\
  projectItems(first: 100) { totalCount nodes { id project { id } } }
"""
#: Who last closed or reopened a card.
_LAST_CLOSE_FIELDS = """\
  timelineItems(itemTypes: [CLOSED_EVENT, REOPENED_EVENT], last: 1) { nodes {
    __typename
    ... on ClosedEvent { actor { login } }
    ... on ReopenedEvent { actor { login } } } }
"""
#: What :class:`_tracker.Tracker` reads of every card: in this order, the text every
#: recording replays word for word.
CARD_FIELDS = _ISSUE_FIELDS + _BOARD_ITEM_FIELDS + _LAST_CLOSE_FIELDS
#: What :class:`_tracker.CardReader` reads of every card: the same, but its board items.
BARE_CARD_FIELDS = _ISSUE_FIELDS + _LAST_CLOSE_FIELDS
#: How many cards one page of a listing of cards holds (:func:`open_cards`,
#: :func:`marked_cards`), inside GitHub's limit of about ten seconds a request.  Measured
#: 2026-10-26 15:27-15:33 on the tracker, 684 open cards, three whole walks: 100 a page took
#: up to 3.7-4.2 s, and at 14:17 was cut off at 10.6 and 11.1 s (finding plan#1806); 25 a
#: page took at most 1.3-2.6 s, typically 1.0.  The marked listing reads the same fields, so
#: a filing pass that marks dozens of cards costs per card what the open listing does.  The
#: recordings' six listings were re-keyed to this text at plan#1809, their answers untouched:
#: each holds at most 16 cards and no next page, as GitHub answers the same read at 25.
_LISTING_PAGE = 25


def open_cards(place: Place) -> str:
    """The listing of every open card of ``place``."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: %d, after: $after, states: [OPEN]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
        % (place.owner, place.name, _LISTING_PAGE, CARD_FIELDS)
    )


def marked_cards(place: Place) -> str:
    """The listing of every card of ``place`` carrying the :data:`setup_tracker.FILING`
    mark, open or closed: a ruling's filing closes it before its last write removes the
    mark, and a ruling closed as completed by anyone is still unfinished (R-BAL206)."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: %d, after: $after, labels: ["%s"], states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } }"""
        % (place.owner, place.name, _LISTING_PAGE, FILING, CARD_FIELDS)
    )


#: What quill reads of a comment (:func:`_tracker._comment`).
COMMENT_FIELDS = "author { login } createdAt body"
#: What a read of many cards by number reads of each card's comments
#: (:meth:`_tracker.Tracker.comments_of`, ruling plan#1931): their first page, a page as
#: :func:`comments` reads one, and whether another follows, which is then read whole that way.
COMMENT_PAGE_FIELDS = ("number comments(first: 100) { pageInfo { hasNextPage } nodes { %s } }"
                       % COMMENT_FIELDS)


#: The board's cards in their positions, each with its issue type, a page at a time
#: (:meth:`_tracker.Board.held`): the type is what keeps a task, which the board holds
#: nested under its step (ruling plan#1814), out of the drag order (:meth:`_tracker.Board.order`).
BOARD_ORDER = """query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
  items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { id content { ... on Issue {
      number issueType { name } repository { nameWithOwner } } } } } } } }"""


#: The board's Status field, with its id and its options' ids, and each item with the Status
#: it holds, a page at a time (:meth:`_tracker.Board.statuses`, rulings plan#1873, plan#1905):
#: its own read, so no recording of :data:`BOARD_ORDER` changes.
BOARD_STATUS = """query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
  field(name: "%s") { ... on ProjectV2SingleSelectField { id options { id name } } }
  items(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id content { ... on Issue { number repository { nameWithOwner } } }
      fieldValueByName(name: "%s") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } }
  } } }""" % (GITHUB_STATUS_FIELD, GITHUB_STATUS_FIELD)


#: What quill reads of one saved version of a card's text or a comment's.  GitHub keeps
#: each version of a card's text in full in ``diff`` (measured 2026-10-04,
#: :meth:`_tracker.Tracker.edits`); whether a comment's ``diff`` is whole too is
#: unmeasured, since no comment on the tracker was ever edited.
EDIT_FIELDS = "id editedAt editor { login } diff"


def edits(place: Place) -> str:
    """The read of one card's saved versions, a page at a time."""
    return (
        """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number body createdAt author { login }
    userContentEdits(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { %s } } } } }"""
        % (place.owner, place.name, EDIT_FIELDS)
    )


def blocking(place: Place) -> str:
    """The read of the cards one card blocks, each with its state and type (ruling
    ``balance:R-BAL395``: a step's are the findings, rulings and questions it owns, beside
    any card waiting on it).  Its own read, never a field of every card's: only ``quill show``, a
    ruling's filing run again, the release tool (``tools/release/_cards.py``, R-BAL395's
    groups), and the doors that keep a finding's one owner
    (``_findings.owned``: ``quill drop``, the split, and CI's landing check, which reads it
    through the card-only reader) ask it."""
    return (
        """query($number: Int!) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number
    blocking(first: 100) { totalCount nodes {
      number state issueType { name } repository { nameWithOwner } } } } } }"""
        % (place.owner, place.name)
    )


def comments(place: Place, fields: str = COMMENT_FIELDS) -> str:
    """The read of one card's comments, a page at a time, each read as ``fields``:
    :data:`COMMENT_FIELDS`, or the backup's :data:`EXPORT_COMMENT_FIELDS`."""
    return (
        """query($number: Int!, $after: String) { repository(owner: "%s", name: "%s") {
  issue(number: $number) { number
    comments(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { %s } } } } }"""
        % (place.owner, place.name, fields)
    )


#: What the daily backup (:mod:`_export`) reads of a comment: quill's fields, its id, and
#: when it was last edited, which says whether its saved versions are read too
#: (:data:`COMMENT_EDITS`).  Measured 2026-10-21 on 202 comments of a public repository
#: (``cli/cli``; no comment on the tracker or the code repository was ever edited): the 4
#: holding saved versions each had a last edit, and none of the 198 without did.
EXPORT_COMMENT_FIELDS = f"id {COMMENT_FIELDS} lastEditedAt"


def export_cards(place: Place) -> str:
    """The listing of every card of ``place``, open and closed, as the daily backup
    (:mod:`_export`) reads it: :data:`CARD_FIELDS`, its text, who filed it and when, when it
    closed, its milestone's number, and its first hundred saved versions and comments, each
    connection saying whether more follow.

    Twenty-five cards a page: measured 2026-10-21 on a public repository's issues
    (``cli/cli``, long texts and threads), a page of 100 took 7.0 s and the next was refused
    502 at 11 s, past GitHub's limit; 600 cards read 50 a page took at most 5.1 s a page,
    and 25 a page at most 4.0 s, so 25 leaves room for histories that only grow.  A
    comment's own saved versions are not nested here: GitHub refuses the listing that nests
    them, at up to 1,000,000 possible nodes past its 500,000 (measured the same day on the
    tracker, ``MAX_NODE_LIMIT_EXCEEDED``), so they are read apart (:data:`COMMENT_EDITS`)."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 25, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { %s body author { login } createdAt closedAt
      milestone { number }
      userContentEdits(first: 100) {
        totalCount pageInfo { hasNextPage endCursor } nodes { %s } }
      comments(first: 100) {
        totalCount pageInfo { hasNextPage endCursor } nodes { %s } } } } } }"""
        % (place.owner, place.name, CARD_FIELDS, EDIT_FIELDS, EXPORT_COMMENT_FIELDS)
    )


def milestones(place: Place) -> str:
    """The listing of every milestone of ``place``, open and closed, whole, a page at a time
    (the daily backup, :mod:`_export`)."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  milestones(first: 100, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor }
    nodes { number title description state dueOn closedAt } } } }"""
        % (place.owner, place.name)
    )


#: The read of one comment's saved versions, by the comment's node id, a page at a time.
COMMENT_EDITS = """query($id: ID!, $after: String) { node(id: $id) { ... on IssueComment {
  userContentEdits(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor } nodes { %s } } } } }""" % EDIT_FIELDS


def issue_fields(place: Place) -> str:
    """The read of ``place``'s issue fields, each single select's REST id and name, how many
    the repository holds beside them: what a filing reads to write a card's Arc
    (:meth:`_tracker.Tracker.create`).  A field of another type answers ``{}``, as
    :data:`_arcs.FIELD_VALUES`' values do.  Measured 2026-10-27 with quill's App token: it
    reads them (Arc's ``fullDatabaseId`` 48771436), five fields in all."""
    return (
        """query { repository(owner: "%s", name: "%s") {
  issueFields(first: 100) { totalCount nodes {
    ... on IssueFieldSingleSelect { fullDatabaseId name } } } } }"""
        % (place.owner, place.name)
    )


#: The repositories a board is linked to, read when quill connects
#: (:func:`_tracker.board_of`).
BOARD_REPOSITORIES = """query($id: ID!) { node(id: $id) { ... on ProjectV2 {
  %s } } }""" % LINKED_REPOSITORIES
