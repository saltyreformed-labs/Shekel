"""The tracker's reads and writes, against GitHub answers RECORDED from the live tracker.

``recorded/tracker.json`` was kept 2026-10-04 (re-recorded 22:13-22:15 EDT for
R-BAL202's filing mark, every create carrying it) by a session that ran every
:class:`_tracker.Tracker` and :class:`_tracker.Board` call once against scratch
cards #11-#25 (``_recorded.Recorder``, which writes to scratch cards only and
redacts every other card's text); ``recorded/label_missing.json`` was kept the
same evening, before the mark's label existed; ``recorded/comments.json`` was kept
2026-10-06 20:23 EDT, after the App's rename, on scratch cards #28-#30;
``recorded/blocking.json`` and ``recorded/show.json`` were kept 2026-10-24 on the
throwaway ``plan-views-demo`` (R-BAL395), on scratch cards #220-#222 and #223-#228.
These tests replay them (``_recorded.Replay``), which answers only a request
it recorded, in the order recorded.  Nothing here calls GitHub.  A query whose
text changes no longer matches its recording: re-record it, never edit an
answer by hand.  A test that needs an answer no scratch card can give (a link
to another repository, a pending release) starts from a recorded answer and
changes the one field it is about, and says so.  The recorder's own guard and
redaction are graded in ``test__recorded.py``.  The claims' exchanges were deleted
from the recordings at X-cx's M1, when a claim left the tracker to become the code
repository's branch ``card/N`` (ruling ``balance:R-BAL359``).
"""
from __future__ import annotations

import copy

import pytest

from tools.quill import quill
from tools.quill._fake import Sent
from tools.quill._github import GitHub, GitHubError
from tools.quill._recorded import (
    REDACTED,
    SCRATCH,
    Replay,
    Scratch,
    recording,
)
from tools.quill._state import filing_unfinished
from tools.quill._tracker import (
    BOARD_WAIT_SECONDS,
    Board,
    Child,
    Comment,
    OutsideLink,
    Tracker,
    TrackerError,
    card_from,
)
from tools.quill.setup_tracker import FILING, PLAN, find_board

#: The real tracker's ``owner/name``, as every recording here names it.
TRACKER = PLAN.full_name

#: The App's login as these recordings hold it: they were kept 2026-10-04 and -05, before the
#: App was renamed ``shekel-quill`` (R-BAL227), and a recording keeps GitHub's answer as given.
APP = "shekel-plan-tool"
#: Card #22's REST id, the scratch finding the recording filed.
FILED_ID = 5705455461
#: A GraphQL lookup of a repository that does not exist (review cp3 L-c).
MISSING_REPOSITORY = ('query { repository(owner: "saltyreformed-labs", name: '
                      '"no-such-repository") { c1: issue(number: 1) { number } } }')


@pytest.fixture(name="recorded")
def _recorded():
    """The tracker over the recording, its board found by its title, as ``connect``'s
    :func:`_tracker.board_of` finds it (the link read that follows is recorded nowhere)."""
    replay = Replay("tracker")
    github = GitHub("token", replay)
    board = Board(github, PLAN, find_board(github, PLAN), sleep=lambda _seconds: None)
    return Tracker(github, board, APP), replay


def test_open_cards_reads_each_cards_facts(recorded):
    """The container off the board, its leaves on it, their blockers, a card with no type."""
    tracker, _ = recorded
    cards = tracker.open_cards()
    assert sorted(cards) == [*range(1, 12), 13, 14, 15]
    container, leaf = cards[1], cards[2]
    assert container.step_children and container.board_item is None
    # A parent lists its sub-issues in the order they were added: L1 added them in plan order.
    assert [child.number for child in container.children] == [2, 3, 4, 5, 6, 9, 7, 8, 10]
    assert (leaf.kind, leaf.labels, leaf.parent, leaf.step_children) == ("step", ("balance",), 1,
                                                                         ())
    assert leaf.board_item and leaf.id == 5696958955
    assert cards[7].blocked_by == (9, 6, 5, 4, 3, 2)
    assert cards[11].kind is None and not cards[11].labels
    assert cards[2].title == REDACTED and cards[13].title.startswith(SCRATCH)


def test_cards_leaves_out_a_number_nobody_filed_and_reads_a_persons_close(recorded):
    """GitHub answers a missing number NOT_FOUND beside the rest; #12 was closed by the
    developer's own login, so the tool must not read it as its own display."""
    tracker, _ = recorded
    cards = tracker.cards([1, 2, 11, 12, 13, 99999])
    assert sorted(cards) == [1, 2, 11, 12, 13]
    closed = cards[12]
    assert (closed.is_open, closed.state_reason) == (False, "NOT_PLANNED")
    assert closed.touched_by_hand and not closed.closed_by_tool
    assert [(c.number, c.is_open) for c in cards[11].children] == [
        (12, False), (13, True), (14, True), (15, True), (16, False), (17, False), (18, False),
        (19, False), (20, False)]


def test_the_board_reads_in_drag_order(recorded):
    """Position order, which is not number order (L9 sits before L7)."""
    tracker, _ = recorded
    assert [number for number, _ in tracker.board.order()] == [2, 3, 4, 5, 6, 9, 7, 8, 10]


def test_edits_are_every_saved_version_oldest_first_each_in_full(recorded):
    """Measured: GitHub keeps the FULL body per edit, with its editor; a card never edited
    has none, so its body as filed is its one version."""
    tracker, _ = recorded
    body, versions = tracker.edits(11)
    assert [v.editor for v in versions] == [APP, APP, "SaltyReformed", APP]
    assert versions[0].body.startswith("L2 probe body v0") and body == versions[-1].body
    assert [v.edited_at for v in versions] == sorted(v.edited_at for v in versions)
    assert versions[0].body != versions[-1].body
    body, versions = tracker.edits(12)
    assert len(versions) == 1 and versions[0].edit_id is None and versions[0].body == body


def test_find_titles_reads_a_title_search_as_the_app(recorded):
    """A title search as the App."""
    tracker, _ = recorded
    assert [number for number, _ in tracker.find_titles("L2 measurement")] == [
        15, 14, 13, 11, 20, 19, 18, 17, 16, 21, 12]


def _replay_reads(tracker):
    """A repository GitHub cannot find, edit histories and a title search."""
    with pytest.raises(GitHubError, match="Could not resolve to a Repository"):
        tracker.github.graphql_lookup(MISSING_REPOSITORY)
    for number in (11, 12):
        tracker.edits(number)
    tracker.find_titles("L2 measurement")


def _replay_filed_card(tracker):
    """A card filed marked (R-BAL202), linked and unmarked, as a finding's filing is; refused
    a second parent, changed every way, closed, reopened, then closed as completed, again as
    NOT planned and again as completed: GitHub changes the reason of a closed card either
    way (sync re-closes a split step as its leaves change, R-BAL187, R-BAL190)."""
    number = tracker.create("finding", f"{SCRATCH} finding, filed by the recorder (delete me)",
                            "The recorder files this card to record GitHub's answers.",
                            ["balance"])
    assert number == 22
    card = tracker.cards([number])[number]
    assert (card.kind, card.labels, card.id) == ("finding", ("balance", FILING), FILED_ID)
    both = tracker.cards([11, 12])
    tracker.add_child(11, card)
    tracker.unmark(number)
    assert tracker.cards([number])[number].labels == ("balance",)
    with pytest.raises(GitHubError, match="Sub issue may only have one parent"):
        tracker.github.rest("POST", f"/repos/{TRACKER}/issues/13/sub_issues",
                            {"sub_issue_id": card.id})
    assert tracker.cards([number])[number].parent == 11
    tracker.retype(number, "question")
    tracker.retitle(number, f"{SCRATCH} question, retitled by the recorder (delete me)")
    tracker.set_body(number, "Is this recorded?")
    tracker.comment(number, "A comment the recorder posts.")
    tracker.block(number, both[12])
    assert tracker.cards([number])[number].blocked_by == (12,)
    tracker.unblock(number, both[12])
    tracker.close(number, "not_planned")
    closed = tracker.cards([number])[number]
    assert (closed.is_open, closed.state_reason, closed.closed_by_tool) == (False, "NOT_PLANNED",
                                                                            True)
    assert (closed.kind, closed.parent) == ("question", 11)
    tracker.reopen(number)
    reopened = tracker.cards([number])[number]
    assert reopened.is_open and not (reopened.closed_by_tool or reopened.touched_by_hand)
    tracker.close(number, "completed")
    assert tracker.cards([number])[number].state_reason == "COMPLETED"
    tracker.close(number, "not_planned")
    assert tracker.cards([number])[number].state_reason == "NOT_PLANNED"
    tracker.close(number, "completed")
    assert tracker.cards([number])[number].state_reason == "COMPLETED"
    return both[11]


def _replay_board(tracker, order, parent):
    """A card added, placed, read back while in the wrong place, placed again, removed."""
    assert tracker.board.order() == order
    item = tracker.board.add(parent)
    assert tracker.board.place(item, order[0][1])
    quick = Board(tracker.github, PLAN, tracker.board_id, sleep=lambda _seconds: None)
    assert quick.shows(item, None) is False, "second on the board is not first"
    assert tracker.board.place(item, None)
    tracker.board.remove(item)
    tracker.board.order()


def _replay_marked_step(tracker):
    """A top-level step S filed as ``quill file`` files one -- created marked, put on the
    board, unmarked -- with its mark read by number and in the listing of marked cards
    just after the create and just after the unmark (cp5 LOW 3: each showed the write at
    that first read; one sample, the delay not timed), and unmarked again: GitHub answers
    404 "Label does not exist" for a mark the card lacks."""
    split = tracker.create("step", f"{SCRATCH} step S, split by the recorder (delete me)",
                           "The recorder files this step to record the filing mark.",
                           ["balance"])
    assert split == 23
    assert tracker.cards([split])[split].labels == ("balance", FILING)
    assert split in tracker.marked() and split in tracker.open_cards()
    item = tracker.board.add(tracker.cards([split])[split])
    tracker.unmark(split)
    assert tracker.cards([split])[split].labels == ("balance",)
    assert split not in tracker.marked()
    with pytest.raises(GitHubError, match="Label does not exist") as refused:
        tracker.unmark(split)
    assert refused.value.status == 404
    return split, item


def _replay_unfinished_leaf_and_ruling(tracker, split, split_item):
    """S's leaf L filed to its last write but one (created marked, linked, added, moved
    into S's place, S off the board) and its ruling R likewise (created marked, linked,
    closed as completed): the listing of marked cards holds the open L and the CLOSED R,
    each still an unfinished filing (R-BAL206), and not S, whose filing finished."""
    leaf = tracker.create("step", f"{SCRATCH} leaf L of step S, by the recorder (delete me)",
                          "The recorder files this leaf; its filing stops before its unmark.",
                          ["balance"])
    tracker.add_child(split, tracker.cards([leaf])[leaf])
    item = tracker.board.add(tracker.cards([leaf])[leaf])
    assert tracker.board.place(item, split_item)
    tracker.board.remove(split_item)
    ruling = tracker.create("ruling", f"{SCRATCH} ruling R of step S, by the recorder (delete me)",
                            "The recorder files this ruling; its filing stops before its unmark.",
                            ["balance"])
    tracker.add_child(split, tracker.cards([ruling])[ruling])
    tracker.close(ruling, "completed")
    marked = tracker.marked()
    assert (leaf, ruling) == (24, 25) and split not in marked
    assert (marked[leaf].is_open, marked[leaf].kind, marked[leaf].parent) == (True, "step", split)
    assert (marked[ruling].is_open, marked[ruling].state_reason, marked[ruling].kind,
            marked[ruling].parent) == (False, "COMPLETED", "ruling", split)
    assert filing_unfinished(marked[leaf]) and filing_unfinished(marked[ruling])
    return leaf, ruling


def _replay_drop_of_an_unfinished_leaf(tracker, split, leaf, ruling):
    """``quill drop`` of L, its filing unfinished and S's only leaf (R-BAL205): S back on the
    board just after L, L unlinked, commented and closed as not planned.  Read just after the
    unlink, by number and in the listing of open cards ``sync`` reads, S no longer lists L
    (round 4 LOW 6: each showed it at that first read; one sample, the delay not timed);
    then the cleanup leaves S, L and R closed and off the board."""
    s_card, l_card = tracker.cards([split])[split], tracker.cards([leaf])[leaf]
    assert s_card.board_item is None and s_card.step_children == (leaf,)
    item = tracker.board.add(s_card)
    assert tracker.board.place(item, l_card.board_item)
    tracker.remove_child(split, l_card)
    after = tracker.cards([split, leaf])
    assert [c.number for c in after[split].children] == [ruling] and after[leaf].parent is None
    assert not tracker.open_cards()[split].step_children
    tracker.comment(leaf, "Dropped: recording R-BAL205's undo of a split (delete me)")
    tracker.close(leaf, "not_planned")
    tracker.unmark(ruling)
    tracker.board.remove(item)
    tracker.board.remove(l_card.board_item)
    tracker.close(split, "not_planned")
    end = tracker.cards([split, leaf, ruling])
    assert [(c.state_reason, c.labels, c.board_item) for _, c in sorted(end.items())] == [
        ("NOT_PLANNED", ("balance",), None), ("NOT_PLANNED", ("balance", FILING), None),
        ("COMPLETED", ("balance",), None)]


def test_the_recorded_session_replays_end_to_end(recorded):
    """Every write the tool makes, in the order recorded, each answered as GitHub answered;
    with what checkpoint 2's review found unrecorded (review M2, M3): GitHub refusing a
    second parent, and a board read that keeps showing an item in the wrong place;
    checkpoint 3's (L-c, R-BAL187): a repository GitHub cannot find, and a closed card's
    reason changed; and leaf B's of R-BAL202: the filing mark's create, listing and
    removal, and a leaf's unlink (R-BAL205).  The board ends in the order it began, with no
    scratch card on it."""
    tracker, replay = recorded
    tracker.open_cards()
    tracker.cards([1, 2, 11, 12, 13, 99999])
    order = tracker.board.order()
    _replay_reads(tracker)
    parent = _replay_filed_card(tracker)
    _replay_board(tracker, order, parent)
    split, item = _replay_marked_step(tracker)
    leaf, ruling = _replay_unfinished_leaf_and_ruling(tracker, split, item)
    _replay_drop_of_an_unfinished_leaf(tracker, split, leaf, ruling)
    assert tracker.board.order() == order
    assert replay.unused() == 0


def test_a_create_naming_a_label_the_tracker_lacks_makes_the_label():
    """Recorded 2026-10-04 22:12, before the mark's label existed: GitHub filed the card
    carrying it and made the label itself (grey, no description), so a card filed before
    the label exists is still born marked (R-BAL202), and ``setup_tracker.py --apply``
    then corrects the label's colour and description.  That the label was missing, and
    what GitHub made, were read with the developer's token beside the recording (its
    output is kept with the lane's records), not recorded: this replay alone would pass
    on a recording made after the label existed."""
    replay = Replay("label_missing")
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, PLAN, recording("label_missing")["scratch"]["board"]),
                      APP)
    number = tracker.create("finding", f"{SCRATCH} finding filed before the filing label "
                            "exists (delete me)", "The recorder files this card to record "
                            "GitHub's answer to a label it lacks.", ["balance"])
    assert tracker.cards([number])[number].labels == ("balance", FILING)
    tracker.unmark(number)
    assert tracker.cards([number])[number].labels == ("balance",)
    tracker.close(number, "not_planned")
    assert replay.unused() == 0


def _recorded_answer(method, url_end):
    """GitHub's recorded answer to the first ``method`` request whose URL ends ``url_end``."""
    for exchange in recording("tracker")["exchanges"]:
        if exchange["method"] == method and exchange["url"].endswith(url_end):
            return copy.deepcopy(exchange["answer"])
    raise LookupError(url_end)


class _Answers:
    """A GitHub that answers every REST call with one answer."""

    def __init__(self, answer):
        """Hold the answer."""
        self.answer = answer

    def rest(self, _method, _path, _body=None):
        """The answer, whatever was asked."""
        return self.answer


def test_a_type_github_dropped_is_refused_not_trusted():
    """GitHub silently drops a type its writer may not set; ``create`` reads it back.  The
    recorded answer as it came is accepted (its labels are the ones sent, the mark
    included), so the refusal is the dropped type's alone."""
    filed = _recorded_answer("POST", "/shekel-plan/issues")
    assert filed["type"]["name"] == "finding"
    assert Tracker(_Answers(filed), Board(None, PLAN, "B"), APP).create(
        "finding", "t", "b", ["balance"]) == filed["number"]
    tracker = Tracker(_Answers({**filed, "type": None}), Board(None, PLAN, "B"), APP)
    with pytest.raises(TrackerError, match="not .*'finding'"):
        tracker.create("finding", "t", "b", ["balance"])


def test_a_mark_github_dropped_is_refused_not_trusted():
    """A card filed without its mark would read as a finished filing to every re-run
    (R-BAL202), so ``create`` reads the labels back too.  Measured, GitHub keeps the mark
    even before its label exists (``label_missing.json``); from the recorded answer, the
    mark taken out."""
    filed = _recorded_answer("POST", "/shekel-plan/issues")
    assert FILING in [label["name"] for label in filed["labels"]]
    dropped = {**filed, "labels": [l for l in filed["labels"] if l["name"] != FILING]}
    tracker = Tracker(_Answers(dropped), Board(None, PLAN, "B"), APP)
    with pytest.raises(TrackerError):
        tracker.create("finding", "t", "b", ["balance"])


class _Echo:
    """A GitHub that files a card as asked: the recorded answer, carrying the labels sent, in
    the order sent (the read-back sorts them, so the order GitHub lists them in, which no
    recording has varied, does not matter)."""

    def __init__(self, answer):
        """Hold the recorded answer; keep each body sent."""
        self.answer, self.sent = answer, []

    def rest(self, _method, _path, body=None):
        """The recorded answer with the labels ``body`` names."""
        self.sent.append(body)
        return {**self.answer, "labels": [{"name": name} for name in body["labels"]]}


def test_the_mark_is_sent_and_read_back_whatever_the_labels_sort_beside_it():
    """Review rbal202b LOW 1: a create that appended the mark after its sorted labels would
    refuse, at its read-back, every filing with a label that sorts after the mark
    (``salary``, ``moves-money``, ``pay_calendar``, ``recurrence``).  From the recorded
    answer, its labels made the ones sent."""
    github = _Echo(_recorded_answer("POST", "/shekel-plan/issues"))
    tracker = Tracker(github, Board(None, PLAN, "B"), APP)
    number = tracker.create("finding", "t", "b", ["salary", "moves-money"])
    assert number == github.answer["number"]
    assert sorted(github.sent[0]["labels"]) == [FILING, "moves-money", "salary"]


def _recorded_node(number):
    """One card's GraphQL node as the recording holds it."""
    for exchange in recording("tracker")["exchanges"]:
        found = ((exchange["answer"] or {}).get("data") or {}).get("repository") or {}
        if f"c{number}" in found and found[f"c{number}"]:
            return copy.deepcopy(found[f"c{number}"])
    raise LookupError(number)


def test_a_connection_github_cut_short_is_an_error_not_a_partial_card():
    """A card with more sub-issues than the read holds must not look like fewer."""
    node = _recorded_node(1)
    assert card_from(node, PLAN, "board", APP).step_children
    node["subIssues"]["totalCount"] += 1
    with pytest.raises(TrackerError, match="10 sub-issues; the read holds 9"):
        card_from(node, PLAN, "board", APP)


class _StuckBoard:
    """A board read that never shows the item placed (GitHub's lag, at its worst)."""

    def graphql(self, query, **_variables):
        """Answer a placement, and every read with one other item."""
        if query.lstrip().startswith("mutation"):
            return {}
        return {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                   "nodes": [{"id": "OTHER", "content": {
                                       "number": 2, "repository": {"name": "shekel-plan"}}}]}}}


def test_a_placement_the_board_never_shows_is_reported_after_the_wait():
    """The writer waits out the lag, then says so instead of waiting forever."""
    slept = []
    board = Board(_StuckBoard(), PLAN, "B", sleep=slept.append)
    assert board.place("MINE", "OTHER") is False
    assert sum(slept) == BOARD_WAIT_SECONDS


class _ReadBoard:
    """A board whose every read shows the items ``order``, in that order."""

    def __init__(self, order):
        """Read as ``order``."""
        self.items = order

    def graphql(self, query, **_variables):
        """Answer a placement, and every read with :attr:`items`."""
        if query.lstrip().startswith("mutation"):
            return {}
        return {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                   "nodes": [{"id": item, "content": {
                                       "number": number, "repository": {
                                           "nameWithOwner": PLAN.full_name}}}
                                             for number, item in enumerate(self.items, 2)]}}}


@pytest.mark.parametrize("order, shown", [
    (("OTHER", "MINE"), True), (("MINE", "OTHER"), False), (("OTHER", "X", "MINE"), False),
], ids=["right-after", "before-it", "after-another"])
def test_a_placement_is_shown_only_right_after_the_item_it_was_put_after(order, shown):
    """A card placed after OTHER is shown placed only where it sits right after it; on the
    board anywhere else it is waited on, then reported (fix round 2 of B2b-1: a ``shows``
    that took any read holding the card survived every test)."""
    slept = []
    board = Board(_ReadBoard(order), PLAN, "B", sleep=slept.append)
    assert board.place("MINE", "OTHER") is shown
    assert sum(slept) == (0 if shown else BOARD_WAIT_SECONDS)


def test_a_card_nobody_ever_closed_is_neither_the_tools_display_nor_a_persons():
    """Review M2: with no close or reopen on record the card is untouched; read as a
    person's, sync would only report every card it should close."""
    node = _recorded_node(2)
    assert not node["timelineItems"]["nodes"]
    card = card_from(node, PLAN, "board", APP)
    assert not (card.touched_by_hand or card.closed_by_tool)


def _recorded_query(fragment):
    """GitHub's recorded ``data`` for the first GraphQL query whose text holds ``fragment``."""
    for exchange in recording("tracker")["exchanges"]:
        if fragment in ((exchange["body"] or {}).get("query") or ""):
            return copy.deepcopy(exchange["answer"]["data"])
    raise LookupError(fragment)


def _open_node(number):
    """One card's node as the recorded read of every open card holds it."""
    nodes = _recorded_query("states: [OPEN]")["repository"]["issues"]["nodes"]
    return next(node for node in nodes if node["number"] == number)


class _GraphQLAnswer:
    """A GitHub answering every GraphQL read with one ``data``."""

    def __init__(self, data):
        """Hold the data."""
        self.data = data

    def graphql(self, _query, **_variables):
        """The data, whatever was asked."""
        return self.data


def test_an_item_on_another_board_is_not_this_boards():
    """Review M2: an issue may sit on several boards. From card #2's recorded node, its item
    moved to a board with another id."""
    node = _recorded_node(2)
    board = node["projectItems"]["nodes"][0]["project"]["id"]
    assert card_from(node, PLAN, board, APP).board_item
    node["projectItems"]["nodes"][0]["project"]["id"] = "PVT_another_board"
    assert card_from(node, PLAN, board, APP).board_item is None


def test_a_board_item_of_another_repository_is_not_in_the_order():
    """Review M2: after L5 the code repository's issues can sit on the board. From the
    recorded board read, its first item's repository renamed."""
    data = _recorded_query("orderBy: {field: POSITION")
    first = data["node"]["items"]["nodes"][0]
    first["content"]["repository"]["nameWithOwner"] = "saltyreformed-labs/Shekel"
    order = Board(_GraphQLAnswer(data), PLAN, "B").order()
    assert [number for number, _ in order] == [3, 4, 5, 6, 9, 7, 8, 10]


def test_a_board_item_that_is_not_an_issue_of_the_tracker_is_not_in_the_order():
    """Review cp4 M59: a draft issue on the board answers the ``... on Issue`` fragment with
    an empty object, and an item whose content was deleted with null; neither names the
    tracker, so neither is a card."""
    data = _recorded_query("orderBy: {field: POSITION")
    nodes = data["node"]["items"]["nodes"]
    nodes[:2] = [{**nodes[0], "content": {}}, {**nodes[1], "content": None}]
    order = Board(_GraphQLAnswer(data), PLAN, "B").order()
    assert [number for number, _ in order] == [4, 5, 6, 9, 7, 8, 10]


def test_a_pull_request_a_title_search_finds_is_not_a_card():
    """Review M2: GitHub's issue search returns pull requests too, marked so. From the
    recorded search, one item copied as a pull request."""
    found = next(exchange["answer"] for exchange in recording("tracker")["exchanges"]
                 if "/search/issues?" in exchange["url"])
    found = copy.deepcopy(found)
    found["items"].append({**found["items"][0], "number": 99, "pull_request": {"url": "u"}})
    tracker = Tracker(_Answers(found), Board(None, PLAN, "B"), APP)
    assert [number for number, _ in tracker.find_titles("L2 measurement")] == [
        15, 14, 13, 11, 20, 19, 18, 17, 16, 21, 12]


@pytest.mark.parametrize("link", ["parent", "sub-issue", "blocker"])
def test_a_link_to_an_issue_of_another_repository_is_carried_apart_not_read_as_a_card(link):
    """R-BAL188 (replacing review L4's refusal of the whole read): read by number alone, the
    link named the tracker's card of that number; now the card keeps it among its outside
    links, and the plan never reads it as a card.  From recorded nodes (#2's parent, #1's
    first sub-issue, #7's first blocker), its repository renamed; as recorded, each names
    the tracker."""
    node, target = {
        "parent": lambda n: (n, n["parent"]),
        "sub-issue": lambda n: (n, n["subIssues"]["nodes"][0]),
        "blocker": lambda n: (n, n["blockedBy"]["nodes"][0]),
    }[link]({"parent": _recorded_node(2), "sub-issue": _recorded_node(1),
             "blocker": _open_node(7)}[link])
    assert target["repository"]["nameWithOwner"] == TRACKER
    inside = card_from(node, PLAN, "board", APP)
    assert not inside.outside
    target["repository"]["nameWithOwner"] = "saltyreformed-labs/Shekel"
    card = card_from(node, PLAN, "board", APP)
    assert card.outside == (OutsideLink(link, f"saltyreformed-labs/Shekel#{target['number']}"),)
    if link == "parent":
        assert (inside.parent, card.parent) == (target["number"], None)
    elif link == "sub-issue":
        assert card.children == inside.children[1:]
    else:
        assert card.blocked_by == inside.blocked_by[1:]


def test_a_repository_github_cannot_find_is_an_error_not_a_card_read_as_absent():
    """Review cp3 L-c: ``graphql_lookup`` dropped every NOT_FOUND, so a missing repository
    left ``data.repository`` null and ``cards()`` hit a TypeError (exit 1, a traceback).
    GitHub's recorded answer, with the tracker's own query as the request."""
    (answer,) = [exchange for exchange in recording("tracker")["exchanges"]
                 if (exchange["body"] or {}).get("query") == MISSING_REPOSITORY]
    assert answer["answer"]["errors"][0]["path"] == ["repository"]
    github = GitHub("token", Sent(200, answer["answer"]))
    with pytest.raises(GitHubError, match="Could not resolve to a Repository"):
        Tracker(github, Board(github, PLAN, "B"), APP).cards([1])


def test_a_second_board_add_is_answered_with_the_item_the_card_has():
    """Recorded 2026-10-05 08:00 EDT on scratch #23 (leaf C2, review M3): a read that lags a
    board add makes the tool add the card again; GitHub answers with the SAME item and
    makes no second one, so the repeat is harmless.  The recording also holds an unlink of
    a card that is no sub-issue of the parent named (#24 from #23, then closed): 403."""
    replay = Replay("twice")
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, PLAN, recording("twice")["scratch"]["board"]), APP)
    cards = tracker.cards([23, 24])
    with pytest.raises(GitHubError, match="403.*Resource not accessible by integration"):
        tracker.remove_child(23, cards[24])
    first = tracker.board.add(cards[23])
    assert tracker.board.add(cards[23]) == first
    tracker.board.remove(first)
    assert replay.unused() == 0


def test_an_unlink_of_a_card_not_linked_is_refused_and_a_closed_parent_is_not_why():
    """Recorded 2026-10-05 08:02 EDT on scratch #23-#25 (leaf C2, review M3), separating
    ``twice``'s 403: under the CLOSED #23 its sub-issue #25 is unlinked (200) and linked
    again (201); with #23 reopened, an unlink of #24, which is no sub-issue of it, is
    refused 403 "Resource not accessible by integration" -- the words of a permission
    denial, which the App was not short of, since #25's unlink landed.  So the refusal
    means "not linked"; the tool reads it as a failed call, never as done."""
    replay = Replay("unlink")
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, PLAN, recording("unlink")["scratch"]["board"]), APP)
    cards = tracker.cards([23, 24, 25])
    assert not cards[23].is_open and cards[25].parent == 23
    tracker.remove_child(23, cards[25])
    tracker.add_child(23, cards[25])
    tracker.reopen(23)
    with pytest.raises(GitHubError, match="403.*Resource not accessible by integration"):
        tracker.remove_child(23, cards[24])
    tracker.close(23, "not_planned")
    assert not [number for number, _ in tracker.board.order() if number in (23, 24, 25)]
    end = tracker.cards([23, 24, 25])
    assert (end[23].is_open, end[25].parent, end[24].parent) == (False, 23, None)
    assert replay.unused() == 0


#: The App's login as the comments recording holds it: kept after the App's rename (R-BAL227).
QUILL = "shekel-quill"
#: The scratch cards the comments recording filed: step S, the finding C linked under it (and
#: the step #30 blocking S, which the show recording's test replaced, R-BAL395).
S, C = 28, 29


def _comments_recording():
    """The tracker over the comments recording, and the replay."""
    replay = Replay("comments")
    github = GitHub("token", replay)
    board = Board(github, PLAN, recording("comments")["scratch"]["board"])
    return Tracker(github, board, QUILL), replay


def test_comments_are_read_oldest_first_each_as_posted():
    """Recorded 2026-10-06 20:23 EDT (X-cx leaf B): a card no one commented on has none; the
    two comments the App posted on #28, within the same second, come back in the order
    posted, each with its author's login, its time and its text as sent, the second's
    trailing newline kept."""
    tracker, _ = _comments_recording()
    assert tracker.comments(C) == []
    assert tracker.comments(S) == [
        Comment(QUILL, "2026-10-07T00:23:24Z", "A first comment the recorder posts."),
        Comment(QUILL, "2026-10-07T00:23:24Z",
                "A second comment, on two lines:\nthe recorder posts it too.\n")]


class _Pages:
    """A GitHub answering each GraphQL read with the next of ``pages``, keeping the ``after``
    each read was sent."""

    def __init__(self, pages):
        """Hold the pages."""
        self.pages, self.afters = list(pages), []

    def graphql(self, _query, **variables):
        """The next page."""
        self.afters.append(variables["after"])
        return self.pages.pop(0)


def test_comments_are_read_page_by_page_and_an_author_may_be_gone():
    """No scratch card holds a hundred comments, so from #28's recorded answer: its two
    comments split over two pages, the first saying another follows after "CURSOR"; and the
    second's author null, which GitHub's schema allows (an account since deleted;
    unmeasured)."""
    data = next(copy.deepcopy(exchange["answer"]["data"])
                for exchange in recording("comments")["exchanges"]
                if "comments(first" in ((exchange["body"] or {}).get("query") or "")
                and exchange["body"]["variables"]["number"] == S)
    first, second = copy.deepcopy(data), copy.deepcopy(data)
    nodes = data["repository"]["issue"]["comments"]["nodes"]
    first["repository"]["issue"]["comments"] = {
        "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR"}, "nodes": nodes[:1]}
    second["repository"]["issue"]["comments"] = {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{**nodes[1], "author": None}]}
    github = _Pages([first, second])
    comments = Tracker(github, Board(None, PLAN, "B"), QUILL).comments(S)
    assert github.afters == [None, "CURSOR"]
    assert [(comment.author, comment.body) for comment in comments] == [
        (QUILL, nodes[0]["body"]), (None, nodes[1]["body"])]


def test_edits_are_read_page_by_page_and_the_text_is_the_last_pages():
    """No scratch card holds a hundred saved versions, so from #11's recorded answer: its
    four versions split over two pages, the first saying more follow after "CURSOR", and the
    second page's card text changed, to show which page the text is read from.  Every
    version is read, oldest first, and the card's text is the last page's."""
    data = next(copy.deepcopy(exchange["answer"]["data"])
                for exchange in recording("tracker")["exchanges"]
                if "userContentEdits(first" in ((exchange["body"] or {}).get("query") or "")
                and exchange["body"]["variables"]["number"] == 11)
    first, second = copy.deepcopy(data), copy.deepcopy(data)
    nodes = data["repository"]["issue"]["userContentEdits"]["nodes"]
    first["repository"]["issue"]["userContentEdits"] = {
        "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR"}, "nodes": nodes[:2]}
    second["repository"]["issue"]["userContentEdits"] = {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes[2:]}
    second["repository"]["issue"]["body"] = "the last page's text"
    github = _Pages([first, second])
    body, versions = Tracker(github, Board(None, PLAN, "B"), QUILL).edits(11)
    assert github.afters == [None, "CURSOR"] and body == "the last page's text"
    assert [version.edit_id for version in versions] == [
        node["id"] for node in sorted(nodes, key=lambda node: node["editedAt"])]


#: The scratch cards the show recording filed on the throwaway plan-views-demo, 2026-10-24
#: (R-BAL395): step SS with the task ST under it, the finding SF and the ruling SR it owns
#: (each blocked by it), the step SB blocking it and the step SW waiting on it.
SS, ST, SF, SR, SB, SW = 223, 224, 225, 226, 227, 228


def _show_line(name, kind):
    """A scratch card's title in the show recording."""
    return f"{SCRATCH} show {name} ({kind}), R-BAL395"


def test_show_over_the_recording_lists_a_steps_links_and_owned_cards_then_its_comments(
        code, capsys):
    """The ``quill show`` of a step and of the finding it owns, recorded 2026-10-24 12:04 EDT
    on the throwaway plan-views-demo (``recorded/show.json``, R-BAL395), replayed: every read
    show makes of the tracker, in its order, answered as GitHub answered it, and nothing
    written.  The step: by number; its blocking read, which lists the finding, the ruling and
    the step waiting on it; its task, its blocker and the two cards it owns in ONE read by
    number, for their titles -- never the waiting step, which is no card it owns; the board;
    its text; its comments.  The finding: by number; its owner, its one blocker, in one read;
    the board; its text; its comments.  Whether either shipped, its claim and its marker's
    history are git's (``code``: no marker, no ``card/N``), not the tracker's."""
    replay = Replay("show")
    scratch = Scratch.from_json(recording("show")["scratch"])
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, scratch.place, scratch.board), APP)
    assert quill.main(["show", f"plan#{SS}"], connect=lambda: tracker, root=code) == 0
    assert capsys.readouterr().out == (
        f"plan#{SS} [step, no arc] {_show_line('S', 'step')}\n"
        "  state: open\n"
        "  shipped (git): no\n"
        "  board: not on it\n"
        "  claim: none\n"
        "  parent: none\n"
        f"  child: plan#{ST} (task, open; done (git): no) {_show_line('T', 'task')}\n"
        f"  blocked by: plan#{SB} {_show_line('B', 'step')}\n"
        f"  owns: plan#{SF} (finding, open) {_show_line('F', 'finding')}\n"
        f"  owns: plan#{SR} (ruling, closed) {_show_line('R', 'ruling')}\n"
        "\n"
        "THROWAWAY recording of quill show, card S; deleted after.\n"
        "\n"
        "== comment, 2026-10-24T16:04:50Z by SaltyReformed\n"
        "A comment the recorder posts on step S.\n"
        "\n"
        f"no commit on origin/dev or origin/main adds, changes or deletes plan/ships/{SS}\n")
    assert quill.main(["show", f"plan#{SF}"], connect=lambda: tracker, root=code) == 0
    assert capsys.readouterr().out == (
        f"plan#{SF} [finding, no arc] {_show_line('F', 'finding')}\n"
        "  state: open\n"
        "  shipped (git): no\n"
        "  board: not on it\n"
        "  claim: none\n"
        "  parent: none\n"
        f"  owner (blocked by): plan#{SS} {_show_line('S', 'step')}\n"
        "\n"
        "THROWAWAY recording of quill show, card F; deleted after.\n"
        "\n"
        "no comments\n"
        "\n"
        f"no commit on origin/dev or origin/main adds, changes or deletes plan/ships/{SF}\n")
    assert replay.unused() == 0
    repo = f"/repos/{scratch.place.full_name}"
    assert [(method, url.removeprefix("https://api.github.com"))
            for method, url, _ in replay.sent] == [
        ("POST", "/graphql"), ("POST", "/graphql"), ("POST", "/graphql"), ("POST", "/graphql"),
        ("GET", f"{repo}/issues/{SS}"), ("POST", "/graphql"),
        ("POST", "/graphql"), ("POST", "/graphql"), ("POST", "/graphql"),
        ("GET", f"{repo}/issues/{SF}"), ("POST", "/graphql")]
    queries = [str((body or {}).get("query", "")) for _, _, body in replay.sent]
    assert not any("mutation" in query for query in queries)
    assert f"c{SS}: issue" in queries[0] and "blocking(first" in queries[1]
    assert [number for number in (ST, SF, SR, SB, SW) if f"c{number}: issue" in queries[2]] == [
        ST, SF, SR, SB]
    assert "ProjectV2" in queries[3] and "comments(first" in queries[5]
    assert f"c{SF}: issue" in queries[6] and f"c{SS}: issue" in queries[7]
    assert "ProjectV2" in queries[8] and "comments(first" in queries[10]


class _Markdown:
    """GitHub's Markdown API: every text asked, answered as HTML."""

    def __init__(self):
        """Nothing asked yet."""
        self.sent = []

    def rest_text(self, method, path, body=None):
        """Remember the request; answer it."""
        self.sent.append((method, path, body))
        return '<p dir="auto">rendered</p>'


def test_a_render_asks_githubs_markdown_api_in_gfm_mode_in_the_trackers_context():
    """R-BAL355's grade: the text goes to ``POST /markdown`` as a card's comment renders
    (``gfm``), the tracker as context so ``#N`` links its card N, and GitHub's HTML comes
    back as it is."""
    github = _Markdown()
    tracker = Tracker(github, Board(github, PLAN, "B"), APP)
    assert tracker.render("see #2") == '<p dir="auto">rendered</p>'
    assert github.sent == [("POST", "/markdown", {"text": "see #2", "mode": "gfm",
                                                  "context": TRACKER})]


def test_a_steps_blocking_read_lists_the_findings_and_rulings_it_owns():
    """R-BAL395, recorded on the throwaway plan-views-demo (``recorded/blocking.json``): an
    open finding and a ruling closed after its link, each blocked by a step.  Each is read
    with that step as its one blocker and no parent, and the step with no sub-issue; the
    step's own read lists both, each with its type and state, the newest link first, the
    closed one too."""
    replay = Replay("blocking")
    place = Scratch.from_json(recording("blocking")["scratch"]).place
    github = GitHub("token", replay)
    tracker = Tracker(github, Board(github, place, "none"), APP)
    cards = tracker.cards([220, 221, 222])
    step, finding, ruling = cards[220], cards[221], cards[222]
    assert (step.kind, step.children, step.blocked_by) == ("step", (), ())
    assert [(card.kind, card.is_open, card.parent, card.blocked_by)
            for card in (finding, ruling)] == [("finding", True, None, (220,)),
                                               ("ruling", False, None, (220,))]
    assert tracker.blocking(220) == (Child(222, "ruling", False), Child(221, "finding", True))
    assert replay.unused() == 0
