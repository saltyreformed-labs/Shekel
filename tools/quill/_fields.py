"""A card's Priority and Effort, two of the organization's issue fields
(``setup_tracker.ISSUE_FIELDS``), which quill writes and ``quill show`` prints (plan#2491); and
the one read of the option a card holds in any single select of them, from a GraphQL read of
its values (:func:`value_of`) and from GitHub's answer to a REST write of one
(:func:`answered_value`), which :mod:`_arcs` names for the Arc.  Which kinds of card hold
each is a rule of the plan's, so :mod:`check` holds it.

A leaf: it imports no module of quill's, so :mod:`_arcs` and :mod:`setup_tracker`, which every
other module imports, can import it.
"""
from __future__ import annotations

from collections.abc import Iterable

#: The issue field saying how urgent a card's work is, a single select of
#: ``setup_tracker.ISSUE_FIELDS``' options.
PRIORITY_FIELD = "Priority"
#: The issue field counting the sessions a card's work takes, a single select of
#: ``setup_tracker.ISSUE_FIELDS``' options (ruling plan#2508).
EFFORT_FIELD = "Effort"


def value_of(values: Iterable[dict], field: str) -> str | None:
    """The option a card holds in its single select ``field``, among ``values``, its field
    values as ``_arcs.FIELD_VALUES`` reads them; None when it holds none (measured 2026-10-27
    on plan#1811: a value is ``{"name": "balance", "field": {"name": "Arc"}}``, and a value of
    another type ``{}``)."""
    return next((value["name"] for value in values
                 if (value.get("field") or {}).get("name") == field), None)


def answered_value(values: Iterable[dict], field: str) -> str | None:
    """The option a card holds in its single select ``field``, from GitHub's answer to a REST
    write of a value, its ``issue_field_values``: the option named in the value of that field;
    None when it holds none (measured 2026-10-27 on plan#1811: a value is
    ``{"issue_field_name": "Arc", "single_select_option": {"name": "balance"}, ...}``, and
    GitHub's REST description answers an issue's create, and its read of an issue's values, in
    the shape of that write's answer)."""
    return next((value["single_select_option"]["name"] for value in values
                 if value.get("issue_field_name") == field), None)
