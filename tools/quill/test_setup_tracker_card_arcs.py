"""setup_tracker's check of every card's arc labels against its Arc (ruling
``balance:R-BAL354``, views_spec s.6, plan#1811): over the read ``quill sync``'s label pass
makes, by the producer it puts them back by; no GitHub calls.

Split from ``test_setup_tracker.py``, which this would take past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

import pytest

from tools.quill import _arcs, setup_tracker
from tools.quill._github import TrackerError
from tools.quill.setup_tracker import Report

#: An Arc's value as the listing reads it, measured on plan#1811.
ARC = {"name": "balance", "field": {"name": "Arc"}}


def _node(number, labels, arc=None, kind="step"):
    """A card as the every-card listing answers it, in the shape measured 2026-10-27
    (``tracker_fix/build_1811/s4/probe_card_arcs.out``)."""
    values = [] if arc is None else [{**ARC, "name": arc}]
    return {"number": number, "title": f"card {number}", "issueType": {"name": kind},
            "labels": {"totalCount": len(labels), "nodes": [{"name": n} for n in labels]},
            "issueFieldValues": {"totalCount": len(values), "nodes": values}}


class _GitHub:
    """A GitHub answering the listing with ``nodes`` on one page, keeping each query."""

    def __init__(self, *nodes):
        """Hold the nodes."""
        self.nodes, self.queries = list(nodes), []

    def graphql(self, query, **_variables):
        """The one page."""
        self.queries.append(query)
        return {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                          "nodes": self.nodes}}}


def test_each_card_whose_arc_labels_do_not_display_its_arc_is_a_difference():
    """Every card sync's rule finds anything for (``_arcs.arc_verdict``) is one difference:
    one whose labels sync puts back named with what it lacks and holds (a swapped label, a
    missing one, a task's stray one), and a card holding no Arc named as sync reports it, its
    label kept (plan#1811's review, M2: not "holds ['balance']", which would delete the only
    record of its arc); a card whose arc labels display its Arc, its other labels aside, is
    none.  The read is sync's."""
    github = _GitHub(_node(1, ("balance", "moves-money"), "balance"),
                     _node(2, ("recurrence",), "balance"),
                     _node(3, (), "salary", "question"),
                     _node(4, ("balance",), None, "task"),
                     _node(5, ("balance",)),
                     _node(6, (), None, "task"))
    report = Report()
    setup_tracker.check_card_arcs(github, report)
    assert report.lines == [
        "DIFFERS card shekel-plan#2's arc labels do not display its Arc 'balance': it lacks "
        "['balance'] and holds ['recurrence']",
        "DIFFERS card shekel-plan#3's arc labels do not display its Arc 'salary': it lacks "
        "['salary'] and holds []",
        "DIFFERS card shekel-plan#4's arc labels do not display its Arc None: it lacks [] and "
        "holds ['balance']",
        "DIFFERS card shekel-plan#5 holds no Arc, its arc's one home (R-BAL349), so no arc "
        "label can display it: set its Arc on the web (its labels: ['balance'])"]
    assert report.differences == 4
    assert github.queries == [_arcs.listing("saltyreformed-labs", "shekel-plan")]


def test_each_card_whose_arc_a_person_must_fix_is_named_as_sync_reports_it():
    """By sync's own rule (plan#1811's review, M2 and L1): a card but a task holding no Arc and
    no arc label, one holding an Arc none of quill's arcs, and a task holding one are each a
    difference named as sync's REPORT names it, never as a label to add or remove."""
    github = _GitHub(_node(7, ()), _node(8, ("balance",), "tracker"),
                     _node(9, (), "balance", "task"))
    report = Report()
    setup_tracker.check_card_arcs(github, report)
    assert report.lines == [
        "DIFFERS card shekel-plan#7 holds no Arc, its arc's one home (R-BAL349), so no arc "
        "label can display it: set its Arc on the web (its labels: [])",
        "DIFFERS card shekel-plan#8 holds the Arc 'tracker', none of quill's arcs (balance, "
        "recurrence, pay_calendar, credit_card, bank_import, salary), so no arc label can "
        "display it: set its Arc on the web to one of them",
        "DIFFERS card shekel-plan#9 is a task, whose arc is its card's (R-BAL386), and holds "
        "the Arc 'balance': clear it on the web"]
    assert report.differences == 3


def test_every_card_displaying_its_arc_is_one_ok_line():
    """No difference: one line, counting the cards read."""
    report = Report()
    setup_tracker.check_card_arcs(_GitHub(_node(1, ("balance",), "balance"),
                                          _node(2, (), None, "task")), report)
    assert (report.lines, report.differences) == (
        ["ok      every card's arc labels display its Arc (2 cards)"], 0)


def test_a_card_read_cut_short_fails_the_run(monkeypatch, capsys):
    """A card's labels read cut short is refused (:func:`_github.counted`), and the run fails
    as it does when GitHub refuses a call, printing what it checked first."""
    node = _node(1, ("balance",), "balance")
    node["labels"]["totalCount"] = 101
    monkeypatch.setattr(setup_tracker, "user_token", lambda: "t")
    monkeypatch.setattr(setup_tracker, "GitHub", lambda token: _GitHub(node))
    monkeypatch.setattr(setup_tracker, "check_repository", lambda *_: {"node_id": "R"})
    for name in ("check_labels", "check_issue_types", "check_issue_fields", "check_board",
                 "check_app"):
        monkeypatch.setattr(setup_tracker, name, lambda *_: None)
    assert setup_tracker.main([]) == 2
    assert "failed: plan#1 has 101 labels; the read holds 1" in capsys.readouterr().err
    with pytest.raises(TrackerError):
        setup_tracker.check_card_arcs(_GitHub(node), Report())
