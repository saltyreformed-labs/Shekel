"""The create that files a card holding its Arc (rulings ``balance:R-BAL349``, ``R-BAL354``):
:meth:`_tracker.Tracker.create`'s write of the field beside the arc label, its read of the
field's id, and its read-back; nothing calls GitHub.

No recording holds a create that sends a field value: none did when they were recorded
(``test__tracker.py`` replays them, each create sending no Arc).  So each test answers from
the RECORDED create answer, its labels and field values made the ones sent, each value in the
shape GitHub answered plan#1811's Arc on 2026-10-27, its issue's read and the write of it alike
(``tracker_fix/build_1811/first_write.out``; GitHub's REST description answers a create in
the shape of that read), and the issue fields as quill's App read them that day
(``build_1811/s3/probe_issue_fields.out``).  The first real create sending one, plan#1852's
filing (10:56 EDT the same day, this create's code), was read back by it and again by REST:
the Arc ``balance`` and the label ``balance`` beside it.
"""
from __future__ import annotations

import copy

import pytest

from tools.quill import _queries
from tools.quill._arcs import arc_answered
from tools.quill._draft import Draft
from tools.quill._recorded import recording
from tools.quill._tracker import Board, Tracker, TrackerError
from tools.quill.setup_tracker import FILING, PLAN, REHEARSAL

#: The App's login as the recording holds it (kept before the rename, R-BAL227).
APP = "shekel-plan-tool"
#: The repository's issue fields as quill's App read them, by :func:`_queries.issue_fields`'
#: text, 2026-10-27 10:43 EDT: the two date fields answer ``{}``.
FIELDS = {"repository": {"issueFields": {"totalCount": 5, "nodes": [
    {"fullDatabaseId": "47847921", "name": "Priority"}, {}, {},
    {"fullDatabaseId": "47847924", "name": "Effort"},
    {"fullDatabaseId": "48771436", "name": "Arc"}]}}}
#: The Arc's REST id in that read.
ARC_ID = 48771436


def _value(name, field="Arc", field_id=ARC_ID):
    """A single select's value as GitHub answered plan#1811's Arc, ``name`` its option."""
    return {"issue_field_id": field_id, "node_id": "IFSSV_kgDOAOOB9Q",
            "data_type": "single_select", "issue_field_name": field, "value": 85383809,
            "single_select_option": {"id": 85383809, "name": name, "color": "gray"}}


class _GitHub:
    """A GitHub that answers the issue fields' read with ``fields`` and a create with the
    recorded create answer, carrying the type and labels sent and, for each value sent, its
    value in GitHub's shape (``answered`` in its place when given), keeping every call."""

    def __init__(self, fields=None, answered=None):
        """Hold the answers; keep each call."""
        self.fields = FIELDS if fields is None else fields
        self.answered = answered
        self.calls = []
        self.filed = next(copy.deepcopy(exchange["answer"])
                          for exchange in recording("tracker")["exchanges"]
                          if exchange["method"] == "POST"
                          and exchange["url"].endswith("/shekel-plan/issues"))

    def graphql(self, query, **variables):
        """The issue fields' read."""
        self.calls.append(("graphql", query, variables))
        return copy.deepcopy(self.fields)

    def rest(self, method, path, body=None):
        """The recorded create, as sent."""
        self.calls.append((method, path, body))
        values = [_value(value["value"]) for value in body.get("issue_field_values", [])]
        return {**self.filed, "type": {**self.filed["type"], "name": body["type"]},
                "labels": [{"name": name} for name in body["labels"]],
                "issue_field_values": values if self.answered is None else self.answered}


def _tracker(github, place=PLAN):
    """A tracker of ``place`` over ``github``."""
    return Tracker(github, Board(github, place, "PVT_x"), APP)


def test_a_card_is_filed_holding_its_arc_and_its_arc_label_in_one_create():
    """R-BAL354, "quill sets both when it files a card": the Arc's id read, then the one
    create sending the field's value beside the arc label and the mark, read back."""
    github = _GitHub()
    number = _tracker(github).create(Draft("finding", "t", "b", ("moves-money",), "salary"))
    assert number == github.filed["number"]
    assert github.calls == [
        ("graphql", _queries.issue_fields(PLAN), {}),
        ("POST", f"{PLAN.path}/issues", {
            "title": "t", "body": "b", "type": "finding",
            "labels": [FILING, "moves-money", "salary"],
            "issue_field_values": [{"field_id": ARC_ID, "value": "salary"}]})]


@pytest.mark.parametrize(("answered", "held"), [
    ([], "None"), ([_value("High", "Effort", 47847924)], "None"),
    ([_value("balance")], "'balance'")])
def test_an_arc_github_did_not_set_as_sent_is_refused_not_trusted(answered, held):
    """GitHub drops a field value silently where the feature is off (its REST description),
    so a card answered holding no Arc, or another, was filed without its arc: refused, as a
    dropped type is."""
    tracker = _tracker(_GitHub(answered=answered))
    with pytest.raises(TrackerError, match=rf"was filed as \('finding', \['filing', 'salary'\], "
                                           rf"{held}\), not .*'salary'\): fix it by hand"):
        tracker.create(Draft("finding", "t", "b", (), "salary"))


def test_a_task_is_filed_holding_no_arc_and_no_field_is_read():
    """R-BAL386: a task holds no Arc and no arc label, so its create sends no field value and
    reads no field's id: its request is what it was before the Arc."""
    github = _GitHub()
    _tracker(github).create(Draft("task", "t", "b", (), None))
    assert github.calls == [("POST", f"{PLAN.path}/issues",
                             {"title": "t", "body": "b", "type": "task", "labels": [FILING]})]


@pytest.mark.parametrize(("nodes", "total", "said"), [
    ([{"fullDatabaseId": "47847921", "name": "Priority"}], 1, "holds 0 issue fields named "
     "'Arc' in a read of 1 of its 1"),
    ([{"fullDatabaseId": "47847921", "name": "Priority"}, None], 120, "holds 0 issue fields "
     "named 'Arc' in a read of 2 of its 120"),
])
def test_a_repository_whose_read_holds_no_arc_field_files_nothing(nodes, total, said):
    """No Arc in the read, the repository holding none or GitHub cutting the read short, is
    refused before the create is sent: a card filed so would hold no arc."""
    github = _GitHub(fields={"repository": {"issueFields": {"totalCount": total, "nodes": nodes}}})
    with pytest.raises(TrackerError, match=said):
        _tracker(github).create(Draft("step", "t", "b", (), "balance"))
    assert [call[0] for call in github.calls] == ["graphql"]


def test_the_issue_fields_are_read_on_the_trackers_own_repository():
    """The read names its place's repository, as every read and write does (``_tracker``)."""
    github = _GitHub()
    _tracker(github, REHEARSAL).create(Draft("step", "t", "b", (), "balance"))
    assert github.calls[0][1] == _queries.issue_fields(REHEARSAL)
    assert f'name: "{REHEARSAL.name}"' in github.calls[0][1]
    assert github.calls[1][1] == f"{REHEARSAL.path}/issues"


def test_only_the_arc_field_s_value_is_the_arc_github_answered():
    """Another single select's value beside it is passed over, and none is no arc."""
    assert arc_answered([_value("High", "Effort", 47847924), _value("recurrence")]) == "recurrence"
    assert arc_answered([_value("High", "Effort", 47847924)]) is None
    assert arc_answered([]) is None
