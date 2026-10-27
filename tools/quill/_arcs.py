"""A card's arc: the organization's issue field Arc, its one home (rulings ``balance:R-BAL349``,
``R-BAL354``), whose matching arc label quill keeps as display.  This module holds the field's
name, how a card's value is read, from a GraphQL read (:func:`arc_of`) and from GitHub's
answer to a REST write (:func:`arc_answered`), and the arc labels that display it
(:func:`arc_labels`), for the tracker's configuration (:mod:`setup_tracker`, which declares
the field), its reads (:mod:`_queries`), its cards and the create that files one
(:mod:`_tracker`).

A leaf: it imports no module of quill's, so :mod:`setup_tracker`, which every other module
imports, can import it.
"""
from __future__ import annotations

from collections.abc import Iterable

from tools.ci.arcs import ARCS

#: The organization's issue field holding a card's arc, a single select whose options are the
#: arcs (``setup_tracker.ISSUE_FIELDS``).
ARC_FIELD = "Arc"

#: What quill reads of a card's issue fields: how many it holds, and each single select's
#: value with its field's name.  The organization declares five fields
#: (``setup_tracker.ISSUE_FIELDS``), so ten leave room, and a read GitHub cut short is refused
#: by its count (``_tracker.card_from``).  A value of another type answers ``{}``: GitHub
#: answers an inline fragment that does not match so.
FIELD_VALUES = """\
  issueFieldValues(first: 10) { totalCount nodes { ... on IssueFieldSingleSelectValue {
    name field { ... on IssueFieldSingleSelect { name } } } } }
"""


def arc_of(values: Iterable[dict]) -> str | None:
    """A card's arc: the value of its :data:`ARC_FIELD` among ``values``, its field values as
    read by :data:`FIELD_VALUES`; None when it holds none (measured 2026-10-27 on plan#1811: a
    value is ``{"name": "balance", "field": {"name": "Arc"}}``)."""
    return next((value["name"] for value in values
                 if (value.get("field") or {}).get("name") == ARC_FIELD), None)


def arc_answered(values: Iterable[dict]) -> str | None:
    """A card's arc from GitHub's answer to a REST write of it, its ``issue_field_values``:
    the option named in the value of its :data:`ARC_FIELD`; None when it holds none
    (measured 2026-10-27 on plan#1811: a value is ``{"issue_field_name": "Arc",
    "single_select_option": {"name": "balance"}, ...}``, and GitHub's REST description
    answers an issue's create in the shape of its read)."""
    return next((value["single_select_option"]["name"] for value in values
                 if value.get("issue_field_name") == ARC_FIELD), None)


def arc_labels(arc: str | None) -> tuple[str, ...]:
    """The arc labels a card holding ``arc`` carries, as display of its Arc (ruling
    ``R-BAL354``): its arc's own, and none when it holds none, as a task does.  The one
    producer of a card's arc labels, which the create that files a card writes
    (:func:`_tracker.filed_labels`)."""
    return () if arc is None else (arc,)


def other_labels(labels: Iterable[str]) -> tuple[str, ...]:
    """``labels`` but the arc labels, which display a card's Arc and decide nothing
    (R-BAL354): what a filing compares and writes beside its Arc."""
    return tuple(label for label in labels if label not in ARCS)
