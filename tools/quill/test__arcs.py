"""A card's arc read from its issue field Arc (rulings ``balance:R-BAL349``, ``R-BAL354``):
:func:`_arcs.arc_of` and :func:`_tracker.card_from`'s read of it; nothing calls GitHub.

No recording plan#1811 re-keyed holds a card with an Arc value: no card held one when they
were recorded (their cards' values are ``recorded/sources/issue_field_values.json``); the
status and priority recordings, made after it, file a step holding one, which their replays
in ``test__tracker.py`` read back.  So each test starts from a
RECORDED card node, the lookup ``test__tracker.py``'s ``cards`` replay reads, and changes only
its field values, given in the shape GitHub answered them for plan#1811 on 2026-10-27
(``tracker_fix/build_1811/first_write.out``).  That every read of a card asks for them, word
for word, is graded by the replays, which match each request so.
"""
from __future__ import annotations

import copy

import pytest

from tools.quill._arcs import ARC_FIELD, arc_of
from tools.quill._recorded import recording
from tools.quill._tracker import TrackerError, card_from
from tools.quill.setup_tracker import PLAN

#: The App's login as the recording holds it (kept before the rename, R-BAL227).
APP = "shekel-plan-tool"
#: plan#1811's Arc as GitHub answered it once set (``first_write.out``).
BALANCE = {"name": "balance", "field": {"name": ARC_FIELD}}


def _recorded_card(key="c2"):
    """Card #2's node as the recorded lookup of #1, #2, #11, #12, #13 and #99999 holds it."""
    for exchange in recording("tracker")["exchanges"]:
        query = (exchange["body"] or {}).get("query") or ""
        if "c99999: issue(number: 99999)" in query:
            return copy.deepcopy(exchange["answer"]["data"]["repository"][key])
    raise LookupError("the lookup of #99999")


def _with_values(*values, total=None):
    """Card #2's recorded node holding the field ``values`` (``total`` of them, GitHub's count,
    when the read was cut short)."""
    node = _recorded_card()
    node["issueFieldValues"] = {"totalCount": len(values) if total is None else total,
                                "nodes": list(values)}
    return node


def test_a_recorded_card_holds_no_arc():
    """As recorded, a card holds no field value, so no arc, whichever reader reads it."""
    node = _recorded_card()
    assert node["issueFieldValues"] == {"totalCount": 0, "nodes": []}
    assert card_from(node, PLAN, None, APP).arc is None
    board = node["projectItems"]["nodes"][0]["project"]["id"]
    assert card_from(node, PLAN, board, APP).arc is None


def test_a_card_s_arc_is_its_arc_field_s_value_whichever_reader_reads_it():
    """Set, the Arc's value is the card's arc, read alike with the board and without it."""
    node = _with_values(BALANCE)
    board = node["projectItems"]["nodes"][0]["project"]["id"]
    assert card_from(node, PLAN, None, APP).arc == "balance"
    assert card_from(node, PLAN, board, APP).arc == "balance"


def test_only_the_arc_field_s_value_is_the_arc():
    """Another single select's value is no arc, wherever it sits, and a value of another type
    (a date, which the read answers ``{}``) is passed over."""
    effort = {"name": "High", "field": {"name": "Effort"}}
    assert card_from(_with_values(effort, {}, BALANCE), PLAN, None, APP).arc == "balance"
    assert card_from(_with_values(effort, {}), PLAN, None, APP).arc is None
    assert arc_of([{"name": "salary", "field": None}]) is None


def test_a_field_values_read_cut_short_is_refused():
    """GitHub counts a card's values; a read holding fewer than it counts may have left the Arc
    out, so it is refused rather than read as none."""
    node = _with_values(BALANCE, total=11)
    with pytest.raises(TrackerError, match="plan#2 has 11 issue field values; the read holds 1"):
        card_from(node, PLAN, None, APP)
