"""The card-only reader (X-cx L9d a1b): cards read by number and without the board, by a
token that may read the tracker's issues and nothing else; nothing calls GitHub.

Each test of a read starts from the RECORDED lookup of cards #1, #2, #11, #12, #13 and
#99999 (the one ``test__tracker.py``'s ``cards`` replay reads), as ``test__tracker_place.py``
does, and changes only what it is about, and says so; the board decisions' test builds its
cards by hand.  The recording was kept before the App's rename
(R-BAL227), so its App is ``shekel-plan-tool``.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from tools.quill._github import GitHub
from tools.quill._recorded import Replay, Scratch, recording
from tools.quill._state import leaf_placement, unsplit
from tools.quill._tracker import BareCard, Card, CardReader, Child, card_from
from tools.quill.setup_tracker import FILING, PLAN, REHEARSAL

#: The App's login as the recording holds it (kept before the rename).
APP = "shekel-plan-tool"
#: The line of a card's read that asks for its board items, as the recording sent it.
BOARD_ITEMS = "  projectItems(first: 100) { totalCount nodes { id project { id } } }\n"


def _recorded_lookup():
    """The recorded lookup's query and the ``repository`` GitHub answered it with."""
    for exchange in recording("tracker")["exchanges"]:
        query = (exchange["body"] or {}).get("query") or ""
        if "c99999: issue(number: 99999)" in query:
            return query, copy.deepcopy(exchange["answer"]["data"]["repository"])
    raise LookupError("the lookup of #99999")


class _Lookup:
    """A GitHub answering every lookup with one ``repository``; it keeps each query."""

    def __init__(self, repository):
        """Hold the answer."""
        self.repository = repository
        self.sent = []

    def graphql_lookup(self, query):
        """Keep the query; answer it."""
        self.sent.append(query)
        return {"repository": copy.deepcopy(self.repository)}


def _without_board(repository):
    """The recorded answer as GitHub gives it to a read that did not ask for board items."""
    for node in repository.values():
        if node is not None:
            del node["projectItems"]
    return repository


def _facts(card):
    """What a card read without the board holds, by name."""
    return {field.name: getattr(card, field.name) for field in dataclasses.fields(BareCard)}


def test_the_reader_asks_what_the_app_asks_but_board_items_and_reads_the_same_facts():
    """Its query is the recorded one with only each card's board-item line left out, and each
    card it reads is a :class:`BareCard` holding every fact the board-reading card holds,
    a person's close (#12) and the App's reopen (#13) read alike; a number nobody filed
    (#99999) is absent."""
    query, repository = _recorded_lookup()
    assert query.count(BOARD_ITEMS) == 6
    board = repository["c2"]["projectItems"]["nodes"][0]["project"]["id"]
    with_board = {node["number"]: card_from(node, PLAN, board, APP)
                  for node in repository.values() if node is not None}
    github = _Lookup(_without_board(repository))
    cards = CardReader(github, PLAN, APP).cards([1, 2, 11, 12, 13, 99999])
    assert github.sent == [query.replace(BOARD_ITEMS, "")]
    assert sorted(cards) == [1, 2, 11, 12, 13]
    for number, card in cards.items():
        assert isinstance(card, BareCard) and not isinstance(card, Card)
        assert not hasattr(card, "board_item")
        assert _facts(card) == _facts(with_board[number])
    assert with_board[2].board_item is not None
    assert cards[12].touched_by_hand and not cards[13].touched_by_hand


def test_card_from_builds_the_type_its_reader_read_and_a_board_read_never_guesses_one():
    """No board id: a :class:`BareCard`.  A board id: a :class:`Card`, whose read must hold the
    board items: an answer without them is an error, never a card read as off the board."""
    _, repository = _recorded_lookup()
    node = repository["c2"]
    assert isinstance(card_from(node, PLAN, "board", APP), Card)
    del node["projectItems"]
    assert not isinstance(card_from(node, PLAN, None, APP), Card)
    with pytest.raises(KeyError, match="projectItems"):
        card_from(node, PLAN, "board", APP)
    with pytest.raises(TypeError, match="board_item"):
        Card(**_facts(card_from(node, PLAN, None, APP)))


def test_a_reader_reads_its_own_places_links_as_inside_and_the_real_trackers_as_outside():
    """A reader names its own place, as :class:`_tracker.Tracker` does.  From #2's recorded
    node, its parent link moved to the rehearsal tracker: read there it is #2's parent, read
    on the real tracker it is a link outside (R-BAL188)."""
    _, repository = _recorded_lookup()
    repository["c2"]["parent"]["repository"]["nameWithOwner"] = REHEARSAL.full_name
    github = _Lookup(_without_board({"c2": repository["c2"]}))
    rehearsal = CardReader(github, REHEARSAL, APP).cards([2])[2]
    assert (rehearsal.parent, rehearsal.outside) == (1, ())
    real = CardReader(github, PLAN, APP).cards([2])[2]
    assert real.parent is None and [link.what for link in real.outside] == ["parent"]


def test_a_reader_given_no_login_reads_a_close_by_the_declared_app_as_the_tools():
    """CI's token is no App's, so it cannot ask GitHub which App is quill's: the reader takes
    the declared login, ``shekel-quill``.  From #12's recorded node, its last close moved from
    the developer's login to the App's."""
    _, repository = _recorded_lookup()
    repository["c12"]["timelineItems"]["nodes"][0]["actor"]["login"] = "shekel-quill"
    github = _Lookup(_without_board(repository))
    assert CardReader(github, PLAN).cards([12])[12].closed_by_tool
    assert CardReader(github, PLAN, APP).cards([12])[12].touched_by_hand


def _step(number, card_type=BareCard, **changes):
    """An open step of the plan's tracker, of ``card_type``, with ``changes`` applied."""
    fields = {
        "number": number, "id": 1000 + number, "node_id": f"I_{number}", "title": f"card {number}",
        "kind": "step", "labels": ("balance",), "is_open": True, "state_reason": None,
        "parent": None, "children": (), "blocked_by": (), "closed_by_tool": False,
        "touched_by_hand": False, "outside": (), **changes,
    }
    return card_type(**fields)


def test_a_board_decision_handed_a_card_read_without_the_board_raises():
    """The coordinator's 13:33 ruling: a card read without the board has no board item, so a
    decision about the board raises rather than read it as off the board.  Step #5 split into
    #6 alone, still being filed: taking #6 out puts a plain #5 back on the board.  Read with
    the board and off it, the undo ADDS #5; read without the board, it raises."""
    leaf = _step(6, parent=5, labels=("balance", FILING))
    split = {"children": (Child(6, "step", True),)}
    board = [(6, "PVTI_6")]
    off_board = _step(5, Card, board_item=None, **split)
    assert unsplit(off_board, [6], {5: off_board, 6: leaf}, (), board).add
    bare = _step(5, **split)
    with pytest.raises(AttributeError, match="board_item"):
        unsplit(bare, [6], {5: bare, 6: leaf}, (), board)
    with pytest.raises(AttributeError, match="board_item"):
        leaf_placement(board, bare, 7, {5: bare, 6: leaf}, ())


def test_the_reader_reads_what_a_card_blocks_as_the_apps_tracker_does():
    """plan#1772 moved the read of what a card blocks down to this reader, where CI's landing
    check reads the findings a claim's card owns: over the recorded read of what step #220
    blocks (``recorded/blocking.json``, which ``test__tracker.py`` reads through the App's
    tracker), it sends the very request recorded and reads the same two cards, the newest
    link first."""
    replay = Replay("blocking")
    place = Scratch.from_json(recording("blocking")["scratch"]).place
    assert CardReader(GitHub("token", replay), place, APP).blocking(220) == (
        Child(222, "ruling", False), Child(221, "finding", True))
    assert [body["variables"] for _, _, body in replay.sent] == [{"number": 220}]
