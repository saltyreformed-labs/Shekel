"""The organization's issue fields, as ``setup_tracker`` checks them (rulings R-BAL348, R-BAL354;
four kept 2026-10-25); no GitHub calls.  Split out of ``test_setup_tracker.py``, which the board's
Status (ruling plan#1873) would take past the 1,000 lines ``too-many-lines`` allows."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.ci.arcs import ARCS
from tools.quill import setup_tracker
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


#: GitHub's four default fields as the organization held them on 2026-10-25, in the order
#: GitHub answered them (read by GraphQL that evening): the developer keeps them beside Arc.
_KEPT = (_issue_field("Priority", options=("Urgent", "High", "Medium", "Low")),
         _issue_field("Start date", "DATE"), _issue_field("Target date", "DATE"),
         _issue_field("Effort", options=("High", "Medium", "Low")))


def _declared_but(name):
    """The five declared fields as the organization held them on 2026-10-25, ``name`` out."""
    return [node for node in (*_KEPT, _issue_field("Arc", options=ARCS))
            if node["name"] != name]


def test_the_five_declared_fields_holding_their_options_in_any_order_match():
    """Arc, a single select of the six arcs (R-BAL348), and the four defaults the developer
    kept on 2026-10-25, each a single select holding its options in any order, in any order
    of fields."""
    reordered = [_issue_field(node["name"], node["dataType"],
                              [option["name"] for option in reversed(node.get("options", []))])
                 for node in reversed(_KEPT)]
    assert not setup_tracker.issue_field_differences(
        _fields(_issue_field("Arc", options=reversed(ARCS)), *reordered))


def test_the_organization_as_read_on_2026_10_25_matches():
    """The developer, 2026-10-25, of GitHub's four default fields, which R-BAL348 ruled
    deleted (that click on the web was never made): "I don't want to delete them unless the
    tracker won't work with them empty. I want to use those fields even if they are empty
    for now."  So the organization as read that evening, the four beside Arc, differs
    nowhere."""
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
     "['Urgent', 'High', 'Medium', 'Low']: correct it"),
    ("Effort", _issue_field("Effort", options=("High", "Medium", "Low", "None")),
     "issue field 'Effort' holds the options ['High', 'Medium', 'Low', 'None'], not the options "
     "declared here ['High', 'Medium', 'Low']: correct it"),
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
