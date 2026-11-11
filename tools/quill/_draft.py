"""A card as a change would leave it (:class:`Draft`), its owner (:class:`Owner`) and the
Priority its filing suggests (:class:`Suggestion`): what :mod:`check` grades, and what
:meth:`_tracker.Tracker.create` then files, the one description of a card both read.

A leaf: it imports no module of quill's, so :mod:`_tracker`, which :mod:`check` reads through
:mod:`_state`, can import it.  Moved out of :mod:`check` at plan#1811, whose create files the
card the check passed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    """A card's owner, as the check reads it -- the card a finding, ruling or question is
    blocked by (``R-BAL395``), a step's or task's parent: its issue type (None when it has
    none), and the caller's answers to whether it is still work (``live``) and whether it is
    a step split into smaller steps (``split``, which no branch builds) -- quill asks git
    and the cards, the Action the cards.  ``split`` has no default, so no caller holds a
    finding to its owner rule without answering it (plan#1784)."""

    kind: str | None
    live: bool
    split: bool


@dataclass(frozen=True)
class Suggestion:
    """The Priority a filing session suggests for a card, and ``why``: one plain sentence
    saying what goes wrong for the developer if the card is never built, or for a question
    what waits on its answer (rulings plan#3224, plan#3236).  GitHub holds it as a pending
    suggestion on the card's issue field Priority, ``why`` its reason, the Priority itself
    blank until the developer accepts it or sets another (``_suggestion``)."""

    priority: str
    why: str


@dataclass(frozen=True)
class Draft:  # pylint: disable=too-many-instance-attributes
    """A card as it would stand after the change; ``owner`` None when it has none
    (:class:`Owner`).

    Pylint: too-many-instance-attributes (9/7) -- **nine because a card as a change leaves
    it states nine facts**, each its own GitHub field, link or suggestion -- its type, title,
    text, labels, Arc, owner, Priority, Effort and suggested Priority -- which the check and
    the filing read between them, and no subset of them travels apart from the others.

    ``arc``: the value of its issue field Arc, None when it holds none;
    ``labels``: its labels but its arc's, which a filing writes beside them as display of its
    Arc (``_filed.filed_labels``), and which the check never reads (R-BAL354).
    ``priority`` and ``effort``: the values of its issue fields Priority and Effort
    (:mod:`_fields`, plan#2491), None for one it holds none of or the change does not write,
    as the check reads them only where the change writes them.
    ``suggestion``: the Priority its filing suggests (:class:`Suggestion`), None for a change
    writing none: only a filing writes one, and no read of a card holds one, as quill keeps no
    copy of a suggestion (ruling plan#3236)."""

    kind: str | None
    title: str
    body: str | None
    labels: tuple[str, ...]
    arc: str | None
    owner: Owner | None = None
    priority: str | None = None
    effort: str | None = None
    suggestion: Suggestion | None = None
