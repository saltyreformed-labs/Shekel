"""A card as a change would leave it (:class:`Draft`) and its owner (:class:`Owner`): what
:mod:`check` grades, and what :meth:`_tracker.Tracker.create` then files, the one
description of a card both read.

A leaf: it imports no module of quill's, so :mod:`_tracker`, which :mod:`check` reads through
:mod:`_state`, can import it.  Moved out of :mod:`check` at plan#1811, whose create files the
card the check passed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    """A card's owner, as the check reads it -- the card a finding or ruling is blocked
    by (``R-BAL395``), a step's or task's parent: its issue type (None when it has
    none), and the caller's answers to whether it is still work (``live``) and whether it is
    a step split into smaller steps (``split``, which no branch builds) -- quill asks git
    and the cards, the Action the cards.  ``split`` has no default, so no caller holds a
    finding to its owner rule without answering it (plan#1784)."""

    kind: str | None
    live: bool
    split: bool


@dataclass(frozen=True)
class Draft:
    """A card as it would stand after the change; ``owner`` None when it has none
    (:class:`Owner`).  ``arc``: the value of its issue field Arc, None when it holds none;
    ``labels``: its labels but its arc's, which a filing writes beside them as display of its
    Arc (``_tracker.filed_labels``), and which the check never reads (R-BAL354)."""

    kind: str | None
    title: str
    body: str | None
    labels: tuple[str, ...]
    arc: str | None
    owner: Owner | None = None
