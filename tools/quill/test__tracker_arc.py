"""The create that files a card holding its Arc (rulings ``balance:R-BAL349``, ``R-BAL354``):
:meth:`_tracker.Tracker.create`'s write of the field beside the arc label, its read of the
field's id, and its read-back; and the one write of a card's field value
(:meth:`_tracker.Tracker.set_field`, plan#2491), the Arc's (``set_arc``), its Priority's and
its Effort's.  Nothing calls GitHub.

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


class _Setter:
    """A GitHub that answers the issue fields' read with :data:`FIELDS` and the write of a
    value with ``answer``, keeping every call."""

    def __init__(self, answer):
        """Hold the answer; keep each call."""
        self.answer, self.calls = answer, []

    def graphql(self, query, **variables):
        """The issue fields' read."""
        self.calls.append(("graphql", query, variables))
        return copy.deepcopy(FIELDS)

    def rest(self, method, path, body=None):
        """The write of the value."""
        self.calls.append((method, path, body))
        return copy.deepcopy(self.answer)


def test_set_arc_adds_the_one_value_and_reads_it_back():
    """Ruling plan#1856's fill: the Arc's id read, then REST's add of the one value (never its
    set, PUT, which would replace Priority and Effort too), the answer the card's values after
    it, in the shape measured on plan#1811."""
    github = _Setter([_value("High", "Effort", 47847924), _value("salary")])
    _tracker(github).set_arc(7, "salary")
    assert github.calls == [
        ("graphql", _queries.issue_fields(PLAN), {}),
        ("POST", f"{PLAN.path}/issues/7/issue-field-values",
         {"issue_field_values": [{"field_id": ARC_ID, "value": "salary"}]})]


@pytest.mark.parametrize(("answer", "said"), [
    ([_value("balance")], r"plan#7 holds the Arc 'balance', not 'salary', after it was set"),
    ([], r"plan#7 holds the Arc None, not 'salary', after it was set"),
    ({"message": "Not Found"}, r"plan#7's Arc was answered \{'message': 'Not Found'\}, not a "
                               r"list of its field values"),
    ([{"issue_field_name": "Arc", "single_select_option": None}],
     r"plan#7's Arc was answered .*, not a list of its field values"),
])
def test_set_arc_refuses_an_answer_not_showing_the_arc_set(answer, said):
    """An answer that does not show the Arc set, another or none, or that is not the list of
    the card's values, is refused, never trusted."""
    with pytest.raises(TrackerError, match=said):
        _tracker(_Setter(answer)).set_arc(7, "salary")


def test_only_the_arc_field_s_value_is_the_arc_github_answered():
    """Another single select's value beside it is passed over, and none is no arc."""
    assert arc_answered([_value("High", "Effort", 47847924), _value("recurrence")]) == "recurrence"
    assert arc_answered([_value("High", "Effort", 47847924)]) is None
    assert arc_answered([]) is None


# -- the one write of a field value (plan#2491) ---------------------------------------------
#
# Each answer below is one GitHub gave on 2026-11-01 to ``quill priority`` and ``quill effort``
# setting and clearing plan#2491's Priority and Effort
# (``effort-priority-2026-11-01/measure_set_field.out``): the add answered the card's values
# after it, the delete 204 and no body, and the read after it the card's values left.  The
# issue fields quill's App read that day are :data:`FIELDS`, their ids unchanged.

PRIORITY_ID, EFFORT_ID = 47847921, 47847924
#: plan#2491's values as GitHub answered them, each by its field's name.
ARC = {"issue_field_id": ARC_ID, "node_id": "IFSSV_kgDOAO_E3A", "data_type": "single_select",
       "issue_field_name": "Arc", "value": 86134945,
       "single_select_option": {"id": 86134945, "name": "tooling", "color": "red"}}
PRIORITY = {"issue_field_id": PRIORITY_ID, "node_id": "IFSSV_kgDOAO_4kA",
            "data_type": "single_select", "issue_field_name": "Priority", "value": 83761749,
            "single_select_option": {"id": 83761749, "name": "High", "color": "red"}}
EFFORT = {"issue_field_id": EFFORT_ID, "node_id": "IFSSV_kgDOAO_4kQ",
          "data_type": "single_select", "issue_field_name": "Effort", "value": 86353218,
          "single_select_option": {"id": 86353218, "name": "3", "color": "gray"}}
#: Where a card's values are written and read.
VALUES = f"{PLAN.path}/issues/7/issue-field-values"


class _Values:
    """A GitHub that answers the issue fields' read with ``fields``, an add with ``added``,
    a delete with no body (204), and the read of the card's values with ``listed``, keeping
    every call."""

    def __init__(self, added=None, listed=None, fields=None):
        """Hold the answers; keep each call."""
        self.added, self.listed = added, listed
        self.fields = FIELDS if fields is None else fields
        self.calls = []

    def graphql(self, query, **variables):
        """The issue fields' read."""
        self.calls.append(("graphql", query, variables))
        return copy.deepcopy(self.fields)

    def rest(self, method, path, body=None):
        """The add, the delete or the read of the card's values."""
        self.calls.append((method, path, body))
        return copy.deepcopy({"POST": self.added, "DELETE": None, "GET": self.listed}[method])


@pytest.mark.parametrize(("field", "value", "field_id", "added"), [
    ("Priority", "High", PRIORITY_ID, [ARC, PRIORITY]),
    ("Effort", "3", EFFORT_ID, [ARC, PRIORITY, EFFORT])])
def test_a_value_is_the_add_of_the_one_value_read_back_from_its_answer(field, value, field_id,
                                                                        added):
    """The field's id read, then REST's add of the one value (never its set, PUT, which would
    replace every value), its answer the card's values after it, the Arc kept."""
    github = _Values(added=added)
    _tracker(github).set_field(7, field, value)
    assert github.calls == [
        ("graphql", _queries.issue_fields(PLAN), {}),
        ("POST", VALUES, {"issue_field_values": [{"field_id": field_id, "value": value}]})]


@pytest.mark.parametrize(("field", "field_id", "listed"), [
    ("Priority", PRIORITY_ID, [ARC, EFFORT]), ("Effort", EFFORT_ID, [ARC])])
def test_a_clear_is_the_delete_of_the_value_then_a_read_of_the_card_s_values(field, field_id,
                                                                             listed):
    """The delete answers no body (204), so the card's values are read after it, and the
    field holding none there is the clear done."""
    github = _Values(listed=listed)
    _tracker(github).set_field(7, field, None)
    assert github.calls == [
        ("graphql", _queries.issue_fields(PLAN), {}),
        ("DELETE", f"{VALUES}/{field_id}", None),
        ("GET", f"{VALUES}?per_page=100", None)]


@pytest.mark.parametrize(("value", "added", "said"), [
    ("3", [ARC, PRIORITY], r"plan#7 holds the Effort None, not '3', after it was set"),
    ("4", [ARC, PRIORITY, EFFORT], r"plan#7 holds the Effort '3', not '4', after it was set"),
    ("3", {"message": "Not Found"}, r"plan#7's Effort was answered \{'message': 'Not Found'\}, "
                                    r"not a list of its field values"),
    ("3", [{"issue_field_name": "Effort", "single_select_option": None}],
     r"plan#7's Effort was answered .*, not a list of its field values"),
])
def test_a_set_github_did_not_make_is_refused(value, added, said):
    """An answer that does not show the value set, another or none, or that is not the list
    of the card's values, is refused, never trusted."""
    with pytest.raises(TrackerError, match=said):
        _tracker(_Values(added=added)).set_field(7, "Effort", value)


@pytest.mark.parametrize(("listed", "said"), [
    ([ARC, PRIORITY], r"plan#7 holds the Priority 'High', not None, after it was cleared"),
    ({"message": "Not Found"}, r"plan#7's Priority was answered \{'message': 'Not Found'\}, "
                               r"not a list of its field values"),
])
def test_a_clear_github_did_not_make_is_refused(listed, said):
    """A read after the delete still showing the value, or not the list of the card's values,
    is refused, never trusted."""
    with pytest.raises(TrackerError, match=said):
        _tracker(_Values(listed=listed)).set_field(7, "Priority", None)


def test_a_full_page_read_after_a_clear_is_refused():
    """A page of a hundred values, REST's most, may have another after it holding the value
    cleared, so it says nothing of the clear; ninety-nine is the whole list."""
    with pytest.raises(TrackerError, match=r"plan#7's issue field values were answered a full "
                                           r"page of 100, which another may follow: read its "
                                           r"Priority back by hand"):
        _tracker(_Values(listed=[ARC] * 100)).set_field(7, "Priority", None)
    _tracker(_Values(listed=[ARC] * 99)).set_field(7, "Priority", None)


def test_a_field_the_repository_lacks_writes_nothing():
    """No such field in the read is refused before any write is sent."""
    github = _Values(fields={"repository": {"issueFields": {"totalCount": 1, "nodes": [
        {"fullDatabaseId": "48771436", "name": "Arc"}]}}})
    with pytest.raises(TrackerError, match="holds 0 issue fields named 'Effort' in a read of 1 "
                                           "of its 1"):
        _tracker(github).set_field(7, "Effort", "2")
    assert [call[0] for call in github.calls] == ["graphql"]
