"""A card's arc: the organization's issue field Arc, its one home (rulings ``balance:R-BAL349``,
``R-BAL354``), whose matching arc label quill keeps as display.  This module holds the field's
name and how a card's value is read, for the tracker's configuration (:mod:`setup_tracker`,
which declares the field), its reads (:mod:`_queries`) and its cards (:mod:`_tracker`).

A leaf: it imports no module of quill's, so :mod:`setup_tracker`, which every other module
imports, can import it.
"""
from __future__ import annotations

from collections.abc import Iterable

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
