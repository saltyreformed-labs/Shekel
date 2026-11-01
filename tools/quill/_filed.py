"""What a create sends of a card, beside its title, text and type: its labels
(:func:`filed_labels`) and its issue field values (:func:`filed_values`), each composed once
from the :class:`_draft.Draft` the check passed, which :meth:`_tracker.Tracker.create` sends
and reads back.

Split out of :mod:`_tracker` at plan#2491, whose create sends a card's Effort beside its Arc
(ruling plan#2492) and would otherwise take that module past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Iterable

from tools.quill._arcs import ARC_FIELD, arc_labels
from tools.quill._draft import Draft
from tools.quill._fields import EFFORT_FIELD, PRIORITY_FIELD
from tools.quill.setup_tracker import FILING


def filed_labels(labels: Iterable[str], arc: str | None) -> list[str]:
    """The labels a create sends for a card filed in ``arc`` (None: a task, which holds no
    arc) beside ``labels``: those, its arc's label as display of its Arc
    (:func:`_arcs.arc_labels`, ruling ``balance:R-BAL354``) and the :data:`setup_tracker.FILING`
    mark (R-BAL202), sorted, as GitHub's answer is read back.  The one composition, which
    :meth:`_tracker.Tracker.create` sends and the tests' tracker files by."""
    return sorted({*labels, *arc_labels(arc), FILING})


def filed_values(card: Draft) -> dict[str, str]:
    """The issue field values a create sends for ``card``, by field: its Arc, its Priority
    and its Effort, each that it holds (``quill file`` names an Effort for a step or a
    finding, ruling plan#2492, and a Priority for none, ruling plan#2507); empty for a task,
    which holds none of them (R-BAL386).  Every value a draft holds is sent, and the create
    refuses an answer not holding each (:meth:`_tracker.Tracker.create`)."""
    held = {ARC_FIELD: card.arc, PRIORITY_FIELD: card.priority, EFFORT_FIELD: card.effort}
    return {field: value for field, value in held.items() if value is not None}
