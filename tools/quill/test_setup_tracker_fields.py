"""The organization's issue fields, as ``setup_tracker`` checks them: the five declared
(rulings R-BAL348, R-BAL354; four kept 2026-10-25), and each one the board shows, which GitHub
lists as a field of its own, marked ``isIssueField``, whose value is the issue's, so it is no
stored field (finding plan#1839); no GitHub calls.

Split out of ``test_setup_tracker.py``, which the board's Status (ruling plan#1873) and the
board's issue fields (finding plan#1839) would each take past the 1,000 lines
``too-many-lines`` allows; the two splits, made apart, were joined here at plan#1861's merge
of plan#1811.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from tools.ci.arcs import ARCS
from tools.quill import _board_fields, _board_views, setup_tracker
from tools.quill.setup_tracker import Report


def _issue_field(name, data_type="SINGLE_SELECT", options=None):
    """One issue field as the issue fields query answers it."""
    field = {"name": name, "dataType": data_type}
    if data_type == "SINGLE_SELECT":
        field["options"] = [{"name": option} for option in options or ()]
    return field


def _fields(*nodes, total=None):
    """The organization's ``issueFields`` connection holding ``nodes``."""
    return {"totalCount": len(nodes) if total is None else total, "nodes": list(nodes)}


#: GitHub's four default fields as the organization held them on 2026-11-04, in the order
#: GitHub answered them (read by GraphQL at 07:49 EST): the developer keeps them beside Arc,
#: each as held on 2026-10-25 but Effort, whose options he set to the sessions a card's work
#: takes on 2026-11-01 (ruling plan#2508; on 2026-10-25 it held High, Medium and Low), and
#: Priority, to which he added On hold on 2026-11-04 (ruling plan#1832).
_KEPT = (_issue_field("Priority", options=("Urgent", "High", "Medium", "Low", "On hold")),
         _issue_field("Start date", "DATE"), _issue_field("Target date", "DATE"),
         _issue_field("Effort", options=("1", "2", "3", "4", "5+")))


def _declared_but(name):
    """The five declared fields as the organization held them on 2026-11-04, ``name`` out."""
    return [node for node in (*_KEPT, _issue_field("Arc", options=ARCS))
            if node["name"] != name]


def test_the_five_declared_fields_holding_their_options_in_any_order_match():
    """Arc, a single select of the arcs (R-BAL348), and the four defaults the developer
    kept on 2026-10-25, each a single select holding its options in any order, in any order
    of fields."""
    reordered = [_issue_field(node["name"], node["dataType"],
                              [option["name"] for option in reversed(node.get("options", []))])
                 for node in reversed(_KEPT)]
    assert not setup_tracker.issue_field_differences(
        _fields(_issue_field("Arc", options=reversed(ARCS)), *reordered))


def test_the_organization_as_read_on_2026_11_04_matches():
    """The developer, 2026-10-25, of GitHub's four default fields, which R-BAL348 ruled
    deleted (that click on the web was never made): "I don't want to delete them unless the
    tracker won't work with them empty. I want to use those fields even if they are empty
    for now."  On 2026-11-01 he set Effort's options to 1, 2, 3, 4 and 5+ (ruling
    plan#2508), and on 2026-11-04 he added On hold to Priority's (ruling plan#1832).  So the
    organization as read that morning, the four beside Arc, differs nowhere."""
    read = _fields(*_KEPT, _issue_field("Arc", options=ARCS))
    assert not setup_tracker.issue_field_differences(read)


@pytest.mark.parametrize(("read", "difference"), [
    (_fields(*_KEPT), "issue field 'Arc' is missing: make it"),
    (_fields(*_KEPT, _issue_field("Arc", "TEXT")),
     "issue field 'Arc' is a TEXT, not a single select: correct it"),
    (_fields(*_KEPT, _issue_field("Arc", options=ARCS[:-1])),
     f"issue field 'Arc' holds the options {list(ARCS[:-1])}, not the arcs {list(ARCS)}: "
     "correct it"),
    (_fields(*_KEPT, _issue_field("Arc", options=ARCS), total=6),
     "the organization has 6 issue fields and the read holds 5: the rest are unread"),
])
def test_arc_missing_mistyped_or_holding_other_options_or_a_short_read_differs(read, difference):
    """Each way Arc can be wrong beside the four kept fields, and a read GitHub cut short."""
    assert setup_tracker.issue_field_differences(read) == [difference]


@pytest.mark.parametrize(("name", "node", "difference"), [
    ("Priority", None, "issue field 'Priority' is missing: make it"),
    ("Target date", None, "issue field 'Target date' is missing: make it"),
    ("Start date", _issue_field("Start date", "TEXT"),
     "issue field 'Start date' is a TEXT, not a date: correct it"),
    ("Effort", _issue_field("Effort", "DATE"),
     "issue field 'Effort' is a DATE, not a single select: correct it"),
    ("Priority", _issue_field("Priority", options=("High", "Low")),
     "issue field 'Priority' holds the options ['High', 'Low'], not the options declared here "
     "['Urgent', 'High', 'Medium', 'Low', 'On hold']: correct it"),
    ("Effort", _issue_field("Effort", options=("1", "2", "3-4", "5+")),
     "issue field 'Effort' holds the options ['1', '2', '3-4', '5+'], not the options "
     "declared here ['1', '2', '3', '4', '5+']: correct it"),
    ("Effort", _issue_field("Effort", options=("High", "Medium", "Low")),
     "issue field 'Effort' holds the options ['High', 'Medium', 'Low'], not the options "
     "declared here ['1', '2', '3', '4', '5+']: correct it"),
])
def test_a_kept_field_missing_mistyped_or_holding_other_options_differs(name, node, difference):
    """Each of the four the developer kept is declared as the organization held it, so one
    missing, of another type or holding other options is a difference, as Arc is."""
    read = _fields(*_declared_but(name), *([node] if node else []))
    assert setup_tracker.issue_field_differences(read) == [difference]


def test_check_issue_fields_names_the_page_to_settle_each_difference_on():
    """Each difference names the organization's issue fields page (measured 2026-10-24: a
    route GitHub knows, as its issue types page is); none, and the check says so."""
    def read(fields):
        return SimpleNamespace(
            graphql=lambda _query, **_: {"organization": {"issueFields": fields}})

    report = Report()
    setup_tracker.check_issue_fields(read(_fields(*_KEPT, _issue_field("Arc", options=ARCS),
                                                  _issue_field("Size", options=("Low",)))),
                                     report)
    assert report.lines == [
        "DIFFERS issue field 'Size' is not declared here: delete it at "
        "https://github.com/organizations/saltyreformed-labs/settings/issue-fields"]
    report = Report()
    setup_tracker.check_issue_fields(read(_fields(*_KEPT, _issue_field("Arc", options=ARCS))),
                                     report)
    assert report.lines == ["ok      issue fields: exactly the 5 declared: 'Arc', 'Priority', "
                            "'Start date', 'Target date', 'Effort'"]
    assert report.differences == 0


#: The real board's fields, as GitHub answered them 2026-10-27 10:0x (``tracker_fix/build_1811/
#: board_fields_isIssueField.json``): ``(name, dataType, isIssueField)``.  Before plan#1839's fix
#: its four issue fields were read as stored fields, and its Plan view's two as stored columns:
#: the five differences the finding names.
_MEASURED = (
    ("Title", "TITLE", False), ("Assignees", "ASSIGNEES", False),
    ("Status", "SINGLE_SELECT", False), ("Labels", "LABELS", False),
    ("Linked pull requests", "LINKED_PULL_REQUESTS", False), ("Milestone", "MILESTONE", False),
    ("Repository", "REPOSITORY", False), ("Reviewers", "REVIEWERS", False),
    ("Parent issue", "PARENT_ISSUE", False), ("Sub-issues progress", "SUB_ISSUES_PROGRESS", False),
    ("Created", "CREATED", False), ("Updated", "UPDATED", False), ("Closed", "CLOSED", False),
    ("Arc", "SINGLE_SELECT", True), ("Priority", "SINGLE_SELECT", True),
    ("Start date", "DATE", True), ("Effort", "SINGLE_SELECT", True),
)
#: The real Plan view's columns, the same day.
_MEASURED_VIEW = ("Title", "Assignees", "Labels", "Linked pull requests", "Parent issue",
                  "Sub-issues progress", "Arc", "Effort")
#: Those columns and Status, which quill writes, so the Plan view shows it (ruling plan#1873):
#: the view as declared, its issue fields as measured.
_PLAN_VIEW = (*_MEASURED_VIEW, _board_fields.GITHUB_STATUS_FIELD)


def _field(name, data_type, issue_field):
    """One board field as the board query answers it, its id from its name; Status holding
    the options quill writes (rulings plan#1873, plan#1906), which the board's read asks."""
    field = {"id": f"F_{name}", "name": name, "dataType": data_type,
             "isIssueField": issue_field}
    if name == _board_fields.GITHUB_STATUS_FIELD:
        field["options"] = [{"name": option} for option in _board_fields.STATUS_OPTIONS]
    return field


#: The real board's other three views, read by GraphQL 2026-11-10 at 12:1x EST, by number:
#: each one's name, layout, the field it is sorted by, descending, if any, whether it is grouped
#: by a column, and its columns.  View 4 is the Inbox, which the developer renamed from "Needs
#: a Priority" and sorted on the web (ruling plan#3231); its filter is the board's argument.
_BUCKET = ("Title", "Assignees", "Status", "Linked pull requests", "Sub-issues progress")
_REAL_VIEWS = {
    2: ("Status Buckets", "BOARD_LAYOUT", "Updated", 1, _BUCKET),
    3: ("Arc Buckets", "BOARD_LAYOUT", None, 1, _BUCKET),
    4: ("Inbox", "TABLE_LAYOUT", "Created", 0, (*_BUCKET, "Arc", "Priority")),
}


def _board(fields, shown, cards=3, inbox_filter=_board_views.INBOX.filter):
    """A private, linked board with no automation, holding ``cards`` cards: its Plan view an
    unsorted, ungrouped table of open cards showing the fields named ``shown``, and the real
    board's other views (:data:`_REAL_VIEWS`), the Inbox filtered ``inbox_filter``."""
    by_name = {name: _field(name, kind, issue) for name, kind, issue in fields}
    plan = ("Plan", "TABLE_LAYOUT", None, 0, shown)
    views = []
    for number, (name, layout, sort, grouped, columns) in {1: plan, **_REAL_VIEWS}.items():
        keys = [{"direction": "DESC", "field": dict(by_name[sort])}] if sort else []
        views.append({
            "id": f"V{number}", "number": number, "name": name, "layout": layout,
            "filter": {"Plan": "is:open", "Inbox": inbox_filter}.get(name, ""),
            "sortByFields": {"totalCount": len(keys), "nodes": keys},
            "groupByFields": {"totalCount": 0}, "verticalGroupByFields": {"totalCount": grouped},
            "fields": {"nodes": [dict(by_name[column]) for column in columns]}})
    return {
        "id": "P", "title": setup_tracker.PROJECT_TITLE, "public": False,
        "url": "https://github.com/orgs/o/projects/1",
        "repositories": {"totalCount": 1,
                         "nodes": [{"nameWithOwner": setup_tracker.PLAN.full_name}]},
        "fields": {"nodes": list(by_name.values())},
        "workflows": {"nodes": []},
        "views": {"nodes": views},
        "items": {"totalCount": cards},
    }


class _GitHub:
    """A fake GitHub holding one board: it answers the organization's list of boards, the
    board, and its items (none), and records each mutation without applying it.  It answers a
    read of the board only when the read asks every board field and every view column whether
    it is an issue field, as the answer it gives says."""

    def __init__(self, project):
        """Hold ``project``, the board every read answers."""
        self.project = project
        self.mutations = []

    def graphql(self, query, **variables):
        """Record a mutation; answer a read."""
        if query.lstrip().startswith("mutation"):
            self.mutations.append(variables)
            return {}
        if "projectsV2(first" in query:
            return {"organization": {"id": "O", "projectsV2": {"nodes": [
                {"id": "P", "title": setup_tracker.PROJECT_TITLE}]}}}
        if "items(first: 100" in query:
            return {"node": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}
        assert query.count("dataType isIssueField") == 2, "a board read must ask isIssueField"
        return {"node": copy.deepcopy(self.project)}


def test_the_real_board_s_issue_fields_are_no_difference():
    """plan#1839 itself: the board as GitHub answered it today, its four issue fields on the
    board and two of them in the Plan view, is no difference (its Status as declared)."""
    assert not setup_tracker.board_differences(_board(_MEASURED, _PLAN_VIEW))


def test_a_board_made_field_of_an_issue_field_s_name_is_still_a_difference():
    """Only GitHub's mark decides: a single select made on the board stores a value per card
    whatever it is called, on the board and as a column of the Plan view."""
    fields = [(name, kind, issue and name != "Arc") for name, kind, issue in _MEASURED]
    assert setup_tracker.board_differences(_board(fields, _PLAN_VIEW)) == [
        "the board stores a field, 'Arc'",
        "the 'Plan' view shows stored fields ['Arc']",
        "the 'Inbox' view shows stored fields ['Arc']",
    ]


def test_check_reads_the_mark_and_passes_the_real_board():
    """The check itself, through its reads of the board: zero differences."""
    report = Report()
    setup_tracker.check_board(_GitHub(_board(_MEASURED, _PLAN_VIEW)), {"node_id": "R"},
                              apply=False, report=report)
    assert report.differences == 0


def test_apply_keeps_the_issue_field_columns_and_hides_a_stored_one():
    """``--apply`` hides each stored column of the Plan view and keeps the rest, the issue
    fields among them; with no stored column to hide it sends no view update."""
    fields = [*_MEASURED, ("Rank", "NUMBER", False)]
    github = _GitHub(_board(fields, (*_PLAN_VIEW, "Rank")))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert github.mutations == [
        {"id": "V1", "fields": [f"F_{name}" for name in _PLAN_VIEW]}]
    assert "made    hid ['Rank'] from the 'Plan' view" in report.lines
    github = _GitHub(_board(_MEASURED, _PLAN_VIEW))
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=Report())
    assert not github.mutations


def test_the_real_board_s_inbox_differs_by_its_filter_alone_which_apply_writes():
    """plan#3248 on the real board: its views as read 2026-11-10 (its fields and its Plan
    view's columns as read 2026-10-27, :data:`_MEASURED`).  The developer renamed "Needs a
    Priority" to Inbox and sorted it by Created, descending, and its filter was still
    ``is:open``, so the check's one difference is that filter; the two bucket views, of other
    names, are none, board layouts, sorted and grouped as they are.  ``--apply`` writes the
    declared filter in one write, to the Inbox alone."""
    board = _board(_MEASURED, _PLAN_VIEW, inbox_filter="is:open")
    assert setup_tracker.board_differences(board) == ["the 'Inbox' view's filter is 'is:open'"]
    github = _GitHub(board)
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=Report())
    assert github.mutations == [
        {"id": "V4", "name": "Inbox",
         "filter": "is:open no:priority no:parent-issue type:step,question"}]
