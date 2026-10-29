"""What a pass of ``quill sync``'s producer decides over, and its writes of each card's state:
ONE producer, which ``sync`` runs over every card (:func:`every_card`) and ``watch`` over the
cards a moved branch names (:func:`decided_over`, :mod:`_watch`).

A pass decides each step's, finding's and task's open or closed state from git
(:func:`_state.sync_plan`) and writes each change (:func:`write_states`); the board's Status is
``_status``'s.  Every card a decision over a card reads is held with it -- each card
:func:`_command.with_closure` reads, every task under a card held, and every task file the claim
of a card held wrote -- so a card decided in a pass over a few cards is decided as in a pass
over all: the cards :func:`_state.sync_plan` asks to be handed.

Split out of the command module, which ``watch`` would take past the 1,000 lines
``too-many-lines`` allows, and which ``watch`` cannot import, the command module importing it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tools.ci.gitcmd import DEV, MAIN
from tools.quill import _git
from tools.quill._command import _label, with_closure
from tools.quill._state import SyncPlan, sync_plan
from tools.quill._tracker import Card, Tracker, TrackerError


@dataclass(frozen=True)
class Decided:
    """What one pass decides over (:func:`decided_over`): ``cards``, each card it decides and
    every card a decision over one reads; ``claims``, the claim of each of those, and of each
    card it was asked for, that is claimed; ``tasks``, each task file on ``dev`` or ``main``
    (:attr:`_git.Shipped.tasks`) that names one of them; and ``history``, a HISTORY line for each
    card it was asked for whose ship marker is on ``dev`` or ``main`` and that does not exist."""

    cards: dict[int, Card]
    claims: dict[int, _git.Claim]
    tasks: frozenset[int]
    history: list[str]


def _wanted(numbers: set[int], cards: Mapping[int, Card],
            claims: Mapping[int, _git.Claim]) -> set[int]:
    """The cards a pass holding ``cards`` reads to decide ``numbers``: those, every task file
    the claim (one of ``claims``) of one of them wrote (:attr:`_git.Claim.tasks`), and every
    task under a card ``cards`` holds."""
    named = {task for number in numbers if number in claims for task in claims[number].tasks}
    return numbers | named | {task for card in cards.values() for task in card.task_children}


def decided_over(tracker: Tracker, reads: _git.Reads, seeds: Iterable[int]) -> Decided:
    """What a pass reads to decide the cards numbered ``seeds`` as a pass over every card
    decides them (:class:`Decided`): each read by its number, with every task file the claim of
    a card held wrote and every task under a card held (:func:`_wanted`), and every card a
    decision over them reads (:func:`with_closure`), until none is missing.  Each card is read
    by its number, never taken from a listing, which may still show a card as it was before a
    write.  A number naming no card is asked once and passed over: a ship marker naming none is
    a HISTORY line here, and a task file naming none is :func:`_state.sync_plan`'s to name.

    Raises:
        TrackerError: when a card a decision reads (a parent, a blocker, a step linked under a
            card) does not exist by its number.
    """
    seeds = set(seeds)
    cards: dict[int, Card] = {}
    asked: set[int] = set()
    while wanted := _wanted(seeds | set(cards), cards, reads.claims) - asked - set(cards):
        asked |= wanted
        cards = with_closure(tracker, {**cards, **tracker.cards(wanted)})
    held = seeds | set(cards)
    return Decided(
        cards, {number: claim for number, claim in reads.claims.items() if number in held},
        reads.shipped.tasks & held,
        [f"the marker {_git.marker(number)} on {DEV} or {MAIN} names plan#{number}, which does "
         "not exist" for number in sorted((reads.shipped.cards & seeds) - set(cards))])


def every_card(tracker: Tracker, reads: _git.Reads, ever: frozenset[int]) -> Decided:
    """What ``quill sync`` decides over: every card :func:`decided_over` reads for each open
    card, each card still marked, open or closed (a filing unfinished, or one closed while still
    being filed, whose link under its split step ``sync`` undoes wherever that step is), each
    card a ship marker ever named (``ever``, :func:`_git.ever_marked`, which holds every card
    git says shipped now too), each claimed card, and each task a task file on ``dev`` or
    ``main`` names.

    The two listings only FIND cards: each is then read by its number, which decides, since a
    listing may still show a card as it was before a write: a leaf's close the listing lagged
    made its split step, which ``quill drop`` had just closed, read as split again, and sync
    reopened it.  A card a marker ever named is read so that one the tool closed as shipped is
    reopened once a revert has deleted its marker, which no listing finds (it is closed, and
    carries no mark); a claimed one so that a claim on a split step is reported whatever that
    step's state (:func:`_state.sync_plan`); and a task through the card it is under
    (R-BAL386), so that one the tool closed as done is reopened once git no longer says it is
    done.  (Not found: a task under a card a PERSON closed while it was claimed, never shipped
    nor marked, whose claim was released since; it stays shown done under that dropped card.)

    Raises:
        TrackerError: when a card a listing holds is not held by a read by its number (that
            read has not caught up yet, or the card was deleted or moved since), or as
            :func:`decided_over` raises.
    """
    listed = {**tracker.open_cards(), **tracker.marked()}
    decided = decided_over(tracker, reads,
                           ever | set(listed) | set(reads.claims) | reads.shipped.tasks)
    if unread := sorted(set(listed) - set(decided.cards)):
        raise TrackerError(f"plan#{unread[0]} is listed, but a read by its number does not hold "
                           "it (that read has not caught up yet, or the card was deleted or "
                           "moved since): run sync again")
    return decided


def write_states(tracker: Tracker, decided: Decided, shipped: frozenset[int],
                 dry_run: bool) -> SyncPlan:
    """Write each card's open or closed state that ``decided`` holds from git (display only),
    as :func:`_state.sync_plan` decides over it with what git says ``shipped``: each close, drop
    and reopen printed as it lands, or under ``dry_run`` printed as what would be written and
    not made; then each REPORT line, for a thing a person must fix in the tracker, and each
    HISTORY line, for a thing ``dev`` and ``main`` already hold wrongly, which no tracker write
    can change (R-BAL184).  The plan, for what the caller writes after it."""
    changes = sync_plan(decided.cards, shipped, decided.claims, decided.tasks)
    verb = "would " if dry_run else ""
    for number in changes.close:
        if not dry_run:
            tracker.close(number, "completed")
        print(f"{verb}close {_label(decided.cards[number])}")
    for number in changes.drop:
        if not dry_run:
            tracker.close(number, "not_planned")
        print(f"{verb}close as not planned, every leaf dropped (R-BAL187): "
              f"{_label(decided.cards[number])}")
    for number in changes.reopen:
        if not dry_run:
            tracker.reopen(number)
        print(f"{verb}reopen {_label(decided.cards[number])}")
    for line in changes.reports:
        print(f"REPORT: {line}")
    for line in decided.history + changes.history:
        print(f"HISTORY: {line}")
    return changes
