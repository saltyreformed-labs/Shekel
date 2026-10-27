"""The organization's issue fields on the board (finding plan#1839): GitHub lists each one the
board shows as a field of its own, marked ``isIssueField``, and its value is the issue's, so it
is no stored field; no GitHub calls.

Split from ``test_setup_tracker.py``, which this would take past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

import copy

from tools.quill import setup_tracker
from tools.quill.setup_tracker import Report

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


def _field(name, data_type, issue_field):
    """One board field as the board query answers it, its id from its name."""
    return {"id": f"F_{name}", "name": name, "dataType": data_type,
            "isIssueField": issue_field}


def _board(fields, shown, cards=3):
    """A private, linked board with no automation, holding ``cards`` cards, its one Plan view an
    unsorted, ungrouped table of open cards showing the fields named ``shown``."""
    by_name = {name: _field(name, kind, issue) for name, kind, issue in fields}
    return {
        "id": "P", "title": setup_tracker.PROJECT_TITLE, "public": False,
        "url": "https://github.com/orgs/o/projects/1",
        "repositories": {"totalCount": 1,
                         "nodes": [{"nameWithOwner": setup_tracker.PLAN.full_name}]},
        "fields": {"nodes": list(by_name.values())},
        "workflows": {"nodes": []},
        "views": {"nodes": [{
            "id": "V1", "number": 1, "name": "Plan", "filter": "is:open",
            "layout": "TABLE_LAYOUT", "sortByFields": {"totalCount": 0},
            "groupByFields": {"totalCount": 0}, "verticalGroupByFields": {"totalCount": 0},
            "fields": {"nodes": [dict(by_name[name]) for name in shown]}}]},
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
    board and two of them in the Plan view, is no difference."""
    assert not setup_tracker.board_differences(_board(_MEASURED, _MEASURED_VIEW))


def test_a_board_made_field_of_an_issue_field_s_name_is_still_a_difference():
    """Only GitHub's mark decides: a single select made on the board stores a value per card
    whatever it is called, on the board and as a column of the Plan view."""
    fields = [(name, kind, issue and name != "Arc") for name, kind, issue in _MEASURED]
    assert setup_tracker.board_differences(_board(fields, _MEASURED_VIEW)) == [
        "the board stores a field, 'Arc'",
        "the 'Plan' view shows stored fields ['Arc']",
    ]


def test_check_reads_the_mark_and_passes_the_real_board():
    """The check itself, through its reads of the board: zero differences."""
    report = Report()
    setup_tracker.check_board(_GitHub(_board(_MEASURED, _MEASURED_VIEW)), {"node_id": "R"},
                              apply=False, report=report)
    assert report.differences == 0


def test_apply_keeps_the_issue_field_columns_and_hides_a_stored_one():
    """``--apply`` hides each stored column of the Plan view and keeps the rest, the issue
    fields among them; with no stored column to hide it sends no view update."""
    fields = [*_MEASURED, ("Rank", "NUMBER", False)]
    github = _GitHub(_board(fields, (*_MEASURED_VIEW, "Rank")))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert github.mutations == [
        {"id": "V1", "fields": [f"F_{name}" for name in _MEASURED_VIEW]}]
    assert "made    hid ['Rank'] from the 'Plan' view" in report.lines
    github = _GitHub(_board(_MEASURED, _MEASURED_VIEW))
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=Report())
    assert not github.mutations
