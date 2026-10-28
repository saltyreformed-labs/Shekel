"""Every card's arc labels against its Arc (ruling ``balance:R-BAL354``, plan#1811): the one
producer of what puts them back (:func:`_arcs.label_corrections`), the one read of every
card's labels and Arc (:func:`_arcs.card_arcs`, :meth:`_tracker.Tracker.card_arcs`), and the
write that puts them back (:meth:`_tracker.Tracker.relabel`); nothing calls GitHub.

No recording holds the listing or a label add (the recorder's routes hold neither), so each
answer here is scripted in a measured shape: a listing node as quill's App read the tracker's
whole listing on 2026-10-27 (``tracker_fix/build_1811/s4/probe_card_arcs.out``: plan#1, a step
holding no Arc), an Arc's value as GitHub answered plan#1811's (``build_1811/first_write.out``),
and a label answer as GitHub answered the filing mark's removal (``recorded/tracker.json``:
the card's labels after it, each with its name).
"""
from __future__ import annotations

import copy

import pytest

from tools.quill import _arcs
from tools.quill._arcs import ArcCard, card_arcs, label_corrections
from tools.quill._tracker import Board, Tracker, TrackerError
from tools.quill.setup_tracker import PLAN

#: plan#1 as the listing answered it, 2026-10-27: a step holding no Arc, its label ``balance``.
NODE = {"number": 1, "title": "[X-cx] Move the plan into this tracker and retire the coordinator",
        "issueType": {"name": "step"},
        "labels": {"totalCount": 1, "nodes": [{"name": "balance"}]},
        "issueFieldValues": {"totalCount": 0, "nodes": []}}
#: An Arc's value as the listing's fragment reads it (``_arcs.FIELD_VALUES``), measured on
#: plan#1811.
ARC = {"name": "balance", "field": {"name": "Arc"}}


def _node(number, labels=("balance",), values=(), kind="step", replaced=None):
    """A listing node: ``NODE``'s shape, its number, labels, field values and type these, and
    each connection ``replaced`` names (``labels``, ``issueFieldValues``) answered as it says."""
    node = copy.deepcopy(NODE)
    node.update(number=number, title=f"card {number}", issueType={"name": kind})
    node["labels"] = {"totalCount": len(labels), "nodes": [{"name": n} for n in labels]}
    node["issueFieldValues"] = {"totalCount": len(values), "nodes": list(values)}
    node.update(replaced or {})
    return node


class _Listing:
    """A GitHub answering the listing a page at a time from ``pages``, keeping each call."""

    def __init__(self, *pages):
        """Hold the pages, each a list of nodes."""
        self.pages, self.calls = list(pages), []

    def graphql(self, query, **variables):
        """The next page, saying whether another follows."""
        self.calls.append((query, variables))
        index = len(self.calls) - 1
        more = index + 1 < len(self.pages)
        return {"repository": {"issues": {
            "pageInfo": {"hasNextPage": more, "endCursor": f"C{index + 1}" if more else None},
            "nodes": copy.deepcopy(self.pages[index])}}}


class _Labels:
    """A GitHub answering a label add or removal with the card's labels ``after`` (each a
    label as GitHub answers one, its name and more), keeping each call."""

    def __init__(self, after):
        """Hold the labels it answers."""
        self.after, self.calls = after, []

    def rest(self, method, path, body=None):
        """The card's labels after the write."""
        self.calls.append((method, path, body))
        return [{"id": 1, "name": name, "default": False} for name in self.after]


def _tracker(github):
    """The plan's tracker over ``github``."""
    return Tracker(github, Board(github, PLAN, "PVT_x"), "shekel-quill")


# -- the producer -------------------------------------------------------------------------

@pytest.mark.parametrize(("labels", "arc", "corrections"), [
    (("balance",), "balance", ((), ())),
    (("recurrence",), "balance", (("balance",), ("recurrence",))),
    ((), "salary", (("salary",), ())),
    (("balance", "recurrence"), "balance", ((), ("recurrence",))),
    (("moves-money", "filing", "recurrence"), "balance", (("balance",), ("recurrence",))),
    (("balance",), None, ((), ("balance",))),
    (("moves-money",), None, ((), ())),
])
def test_label_corrections_put_the_arc_labels_back_to_its_arc_and_keep_the_rest(
        labels, arc, corrections):
    """R-BAL354: a card's arc labels become exactly its Arc's (none for none); every other
    label, the filing mark and a release's among them, is no arc label and is kept."""
    assert label_corrections(labels, arc) == corrections


# -- the read -----------------------------------------------------------------------------

def test_card_arcs_reads_every_card_page_by_page_with_its_labels_and_arc():
    """One listing of every card, open and closed, 100 a page: each node read as its number,
    type, title, labels and Arc, and the next page asked by the last one's cursor."""
    github = _Listing([NODE, _node(1811, values=[ARC])],
                      [_node(1852, ("balance", "filing"), [ARC]), _node(2, (), kind="task")])
    assert card_arcs(github, PLAN.owner, PLAN.name) == [
        ArcCard(1, "step", NODE["title"], ("balance",), None),
        ArcCard(1811, "step", "card 1811", ("balance",), "balance"),
        ArcCard(1852, "step", "card 1852", ("balance", "filing"), "balance"),
        ArcCard(2, "task", "card 2", (), None)]
    text = _arcs.listing(PLAN.owner, PLAN.name)
    assert github.calls == [(text, {"after": None}), (text, {"after": "C1"})]
    assert ('repository(owner: "saltyreformed-labs", name: "shekel-plan")' in text
            and "issues(first: 100, after: $after, states: [OPEN, CLOSED])" in text
            and _arcs.FIELD_VALUES in text)


def test_the_trackers_card_arcs_is_that_read_of_its_own_place():
    """:meth:`Tracker.card_arcs` reads its place's cards by the one listing."""
    github = _Listing([NODE])
    assert _tracker(github).card_arcs() == [ArcCard(1, "step", NODE["title"], ("balance",), None)]
    assert github.calls == [(_arcs.listing(PLAN.owner, PLAN.name), {"after": None})]


@pytest.mark.parametrize(("connections", "refusal"), [
    ({"labels": {"totalCount": 101, "nodes": [{"name": "balance"}]}},
     "plan#7 has 101 labels; the read holds 1"),
    ({"labels": None}, "GitHub answered plan#7's labels as null"),
    ({"issueFieldValues": {"totalCount": 11, "nodes": [ARC]}},
     "plan#7 has 11 issue field values; the read holds 1"),
    ({"issueFieldValues": {"totalCount": 1, "nodes": [None]}},
     "GitHub answered one of plan#7's issue field values as null"),
])
def test_a_card_whose_labels_or_arc_are_read_cut_short_or_null_is_refused(connections, refusal):
    """A silent partial read would read an arc label or the Arc as missing, so the read is
    refused, as every read of a card's connections is (:func:`_github.counted`)."""
    with pytest.raises(TrackerError, match=refusal):
        card_arcs(_Listing([_node(7, replaced=connections)]), PLAN.owner, PLAN.name)


# -- the write ----------------------------------------------------------------------------

def test_relabel_adds_and_removes_only_the_labels_it_names_and_reads_them_back():
    """One add naming every label added, then one removal per label, each label's name
    quoted in its path; never GitHub's "set labels", which would replace them all."""
    github = _Labels(["balance", "moves-money"])
    _tracker(github).relabel(7, ("balance",), ("recurrence", "pay_calendar"))
    path = "/repos/saltyreformed-labs/shekel-plan/issues/7/labels"
    assert github.calls == [("POST", path, {"labels": ["balance"]}),
                            ("DELETE", f"{path}/recurrence", None),
                            ("DELETE", f"{path}/pay_calendar", None)]


def test_relabel_that_only_removes_sends_no_add():
    """A task's stray arc label is removed and nothing is added."""
    github = _Labels([])
    _tracker(github).relabel(7, (), ("balance",))
    assert github.calls == [
        ("DELETE", "/repos/saltyreformed-labs/shekel-plan/issues/7/labels/balance", None)]


@pytest.mark.parametrize("after", [["moves-money"], ["balance", "recurrence"]])
def test_relabel_refuses_an_answer_lacking_a_label_added_or_holding_one_removed(after):
    """GitHub's last answer is the card's labels after the writes: one that does not show
    them made is refused, as a create's labels are."""
    with pytest.raises(TrackerError, match=r"plan#7 holds the labels .* after adding "
                                           r"\['balance'\] and removing \['recurrence'\]"):
        _tracker(_Labels(after)).relabel(7, ("balance",), ("recurrence",))
