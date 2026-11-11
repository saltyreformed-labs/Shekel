"""The board's Inbox view as ``setup_tracker`` checks and makes it (rulings plan#3229,
plan#3231): the open steps and questions holding no Priority, leaves left out, newest first,
declared in :mod:`_board_views`; no GitHub calls.

Split out of ``test_setup_tracker.py``, which the Inbox would take past the 1,000 lines
``too-many-lines`` allows; it reads that file's fake board, which applies the writes it is sent,
so the two files grade one fake.
"""
from __future__ import annotations

import pytest

from tools.quill import _board_views, _fields, check, setup_tracker
from tools.quill.setup_tracker import Report
from tools.quill.test_setup_tracker import (
    _DECLARED_VIEWS,
    _FIELDS,
    _INBOX,
    _Board,
    _project,
    _visible,
)

#: The Inbox's page on the fake board, where it is view 2.
_PAGE = "https://github.com/orgs/o/projects/1/views/2"


def _board(views=_DECLARED_VIEWS, cards=3):
    """A board as declared but its ``views``: private, linked, no automation, no stored field
    but Status, holding ``cards`` cards; its Plan view showing the Parent issue and Status."""
    project = _project(cards=cards, views=views)
    project["fields"]["nodes"] = [_FIELDS["F1"], _FIELDS["F2"], _FIELDS["F3"]]
    project["workflows"]["nodes"] = []
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1", "F2", "F3")
    return project


def _inbox(project):
    """The board's Inbox, view 2."""
    return project["views"]["nodes"][1]


def test_the_inbox_filter_is_the_text_measured_its_kinds_those_that_take_a_suggestion():
    """GitHub drops a clause it cannot read without a word, so the filter is right only as the
    text measured on 2026-11-10 (``_board_views``' module docstring); its kinds are read from
    the one spelling of those that take a suggestion, which the filing's check reads too."""
    assert _board_views.INBOX.filter == "is:open no:priority no:parent-issue type:step,question"
    assert check.SUGGESTED_KINDS is _fields.SUGGESTED_KINDS
    assert not setup_tracker.board_differences(_board())


def test_a_board_holding_the_inbox_as_declared_is_named_so_in_the_check_s_line():
    """The line the check prints for a board that matches names both views as declared."""
    report = Report()
    setup_tracker.check_board(_Board(_board()), {"node_id": "R"}, apply=False, report=report)
    assert report.differences == 0
    assert report.lines[0].endswith(
        "and its views: the 'Plan' view, a table filtered 'is:open', unsorted, ungrouped, "
        "showing the Parent issue and Status columns and no other stored field; the 'Inbox' "
        "view, a table filtered 'is:open no:priority no:parent-issue type:step,question', "
        "sorted by Created, descending, ungrouped, showing no stored field but Status")


@pytest.mark.parametrize(("sorts", "now"), [
    pytest.param((), "unsorted", id="unsorted"),
    pytest.param((("UPDATED", "DESC", "Updated"),), "sorted by Updated, descending",
                 id="another-field"),
    pytest.param((("CREATED", "ASC", "Created"),), "sorted by Created, ascending",
                 id="oldest-first"),
    pytest.param((("CREATED", "DESC", "Created"), ("TITLE", "ASC", "Title")),
                 "sorted by Created, descending; Title, ascending", id="a-second-key"),
])
def test_an_inbox_sorted_otherwise_is_a_difference_naming_its_page(sorts, now):
    """The API cannot sort a view, so a sort but Created, descending, alone is reported with the
    Inbox's own page, where the developer sets it."""
    project = _board(views=(_DECLARED_VIEWS[0], (*_INBOX[:2], sorts)))
    assert setup_tracker.board_differences(project) == [
        f"the 'Inbox' view is {now}, not sorted by Created, descending: the API cannot sort a "
        f"view, so sort it at {_PAGE}"]


def test_a_sort_read_short_names_the_keys_it_did_not_hold():
    """A read GitHub cut short holds fewer keys than the view is sorted by: it is a difference,
    and it says so rather than naming the declared sort as the view's."""
    project = _board()
    _inbox(project)["sortByFields"]["totalCount"] = 2
    assert setup_tracker.board_differences(project) == [
        "the 'Inbox' view is sorted by Created, descending and 1 more the read did not hold, not "
        f"sorted by Created, descending: the API cannot sort a view, so sort it at {_PAGE}"]


def test_an_inbox_grouped_laid_out_as_a_board_or_showing_a_stored_field_is_a_difference():
    """A grouping splits its newest-first order, a board layout's columns are a stored field,
    and the board shows no stored field but Status, as for the Plan view."""
    project = _board()
    project["fields"]["nodes"].append(_FIELDS["F4"])
    inbox = _inbox(project)
    inbox["layout"], inbox["verticalGroupByFields"]["totalCount"] = "BOARD_LAYOUT", 1
    inbox["fields"]["nodes"] = _visible("F1", "F4")
    assert setup_tracker.board_differences(project) == [
        "the board stores a field, 'Rank'",
        "the 'Inbox' view shows stored fields ['Rank']",
        "the 'Inbox' view is a BOARD_LAYOUT, whose columns are a stored field: make it a table "
        "at https://github.com/orgs/o/projects/1",
        "the 'Inbox' view is grouped, which splits the newest-first order: remove its grouping "
        "at https://github.com/orgs/o/projects/1"]


def test_apply_makes_a_missing_inbox_filters_it_and_reports_the_sort_it_cannot_write():
    """``--apply`` makes the Inbox a table and writes its filter, and the re-read board's one
    difference is its sort, with the page to set it on; a view of another name is never
    touched."""
    github = _Board(_board(views=(_DECLARED_VIEWS[0], ("By arc", ""))))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert github.mutations == [
        ("createProjectV2View", {"p": "P", "name": "Inbox"}),
        ("updateProjectV2View", {"id": "V3", "name": "Inbox",
                                 "filter": _board_views.INBOX.filter})]
    assert [line for line in report.lines if line.startswith(("made", "DIFFERS"))] == [
        "made    board view 'Inbox'",
        f"made    board view 'Inbox' -> 'Inbox' ({_board_views.INBOX.filter})",
        "DIFFERS the 'Inbox' view is unsorted, not sorted by Created, descending: the API "
        "cannot sort a view, so sort it at https://github.com/orgs/o/projects/1/views/3"]


def test_apply_writes_an_inbox_s_filter_and_hides_its_stored_column_and_check_mode_does_not():
    """A view holds no card data, so its filter and columns are corrected whatever the board
    holds; check mode reports both and sends nothing."""
    project = _board()
    project["fields"]["nodes"].append(_FIELDS["F4"])
    _inbox(project)["filter"] = "is:open"
    _inbox(project)["fields"]["nodes"] = _visible("F1", "F4")
    github = _Board(project)
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=False, report=report)
    assert not github.writes()
    assert "DIFFERS the 'Inbox' view's filter is 'is:open'" in report.lines
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert github.mutations == [
        ("updateProjectV2View", {"id": "V2", "name": "Inbox",
                                 "filter": _board_views.INBOX.filter}),
        ("updateProjectV2View", {"id": "V2", "fields": ["F1"]})]
    assert "made    hid ['Rank'] from the 'Inbox' view" in report.lines
    assert report.lines.count("DIFFERS the board stores a field, 'Rank'") == 1
    assert report.differences == 1
