"""A card's Priority and Effort, two of the organization's issue fields
(``setup_tracker.ISSUE_FIELDS``), which quill writes and ``quill show`` prints (plan#2491): the
part of a card holding each and the one pairing of each part with its field (:data:`FIELDS`);
and the one read of the option a card holds in any single select of them, from a GraphQL read
of its values (:func:`value_of`) and from GitHub's answer to a REST write of one
(:func:`answered_value`), which :mod:`_arcs` names for the Arc.  Which kinds of card hold
each is a rule of the plan's, so :mod:`check` holds it; which kinds take a suggested Priority
(:data:`SUGGESTED_KINDS`) is here, as the board's Inbox view filters on them too.

A leaf: it imports no module of quill's, so :mod:`_arcs` and :mod:`setup_tracker`, which every
other module imports, can import it.
"""
from __future__ import annotations

from collections.abc import Iterable

#: The issue field saying how urgent a card's work is, a single select of
#: ``setup_tracker.ISSUE_FIELDS``' options.
PRIORITY_FIELD = "Priority"
#: Priority's options the plan reads: what hands a step out ahead of the board's order (ruling
#: plan#1831), and what pauses it (ruling plan#1832).
URGENT, HIGH, ON_HOLD = "Urgent", "High", "On hold"
#: Priority's options, as the developer set them on the web (On hold on 2026-11-04, read by
#: GraphQL at 07:49 EST, in this order).  Medium and Low hand a step out as a step holding none.
PRIORITY_OPTIONS = (URGENT, HIGH, "Medium", "Low", ON_HOLD)
#: The Priorities a filing may suggest for a card (``_draft.Suggestion``): each of Priority's
#: options but On hold, as plan#2798's spec names them, Urgent, High, Medium or Low.
SUGGESTED_OPTIONS = tuple(option for option in PRIORITY_OPTIONS if option != ON_HOLD)
#: The kinds of card that take a suggested Priority (``check.takes_suggestion``): a step, at the
#: top level alone, and a question, which the board's Inbox lists while they hold no Priority
#: (``_board_views.INBOX``, ruling plan#3231).  Here, beside the options a suggestion takes, so
#: the Inbox's filter reads the one spelling: ``check`` imports ``setup_tracker``, which reads
#: the board's views.
SUGGESTED_KINDS = ("step", "question")
#: The rubric a suggestion follows, as the developer's rulings leave it: its one home, a path
#: from the repository's root, which ``quill file``'s help names to the filing session.
RUBRIC = "docs/plans/priority.md"
#: The issue field counting the sessions a card's work takes, a single select of
#: ``setup_tracker.ISSUE_FIELDS``' options (ruling plan#2508).
EFFORT_FIELD = "Effort"
#: The parts of a card holding those two fields: each the name of the fact a ``_draft.Draft``
#: and a ``_tracker.BareCard`` hold its value in, and of the command writing it
#: (``_arc_label.cmd_field``).
PRIORITY, EFFORT = "priority", "effort"
#: Each part's issue field, by the part: the one pairing of the two (rule 14), which the
#: check's rules (``check.HOLDERS``), the create (``_filed.filed_values``), the read of a card
#: (``_tracker.card_from``), the filing and the commands each walk.
FIELDS = {PRIORITY: PRIORITY_FIELD, EFFORT: EFFORT_FIELD}


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
