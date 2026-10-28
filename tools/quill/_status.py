"""The board's Status column: quill's display of git, as a card's open or closed state is
(rulings plan#1873, plan#1905, plan#1906).

Each step, finding and task the board holds shows where its work stands (:func:`wanted`, the
one answer): Done once git says so (a step's or finding's ship marker, ``_state``'s
``shipped``; a task's file, :func:`_state.done_tasks`); In Progress while a step or finding is
claimed, or while a task's card's claim has begun it and not finished it
(:func:`_tasks.in_progress`); Dropped once it was dropped (:func:`_state.dropped`); else Todo.
A question, a ruling, or a card of no type holds none.  A step split into smaller steps,
which the board does not hold (its leaves take its place), shows its leaves
(:func:`_state.container_shown`), should a person put it back.

Only quill writes it: ``quill sync`` reads every card's Status on the board, decides each, and
writes each that differs, a person's edit put back (:func:`sync_statuses`, ruling plan#1905),
and ``quill watch`` does the same for each card a moved branch names (its ``scope``,
:mod:`_watch`); ``--dry-run`` prints each write unmade.  Its options are set on the web, and
``setup_tracker`` checks them: while the field lacks one of :data:`setup_tracker.STATUS_OPTIONS`,
sync reports it and writes no card's Status at all (ruling plan#1906), so no card is shown a
state the board cannot hold.  An item ``sync``'s own put-on added this run that the board's
read lags is written by the next run (``_tracker`` measured the board lagging an add).

Split out of the command module, which this would take past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace

from tools.quill import _git
from tools.quill._command import _label, with_cards, with_closure
from tools.quill._state import SHIPPABLE, container_shown, done_tasks, dropped, is_container
from tools.quill._tasks import in_progress
from tools.quill._tracker import BareCard, Card, Statuses, Tracker
from tools.quill.setup_tracker import (
    DONE,
    DROPPED,
    GITHUB_STATUS_FIELD,
    IN_PROGRESS,
    STATUS_OPTIONS,
    TASK,
    TODO,
    lacking,
)

#: What a split step shows, by the state the tool shows it in (:func:`_state.container_shown`).
_CONTAINER = {None: TODO, "COMPLETED": DONE, "NOT_PLANNED": DROPPED}


@dataclass(frozen=True)
class Progress:
    """What git says of the cards whose Status is decided, read once a run (:func:`progress`):
    the cards ``shipped`` (their ship markers) and ``claimed``, the tasks ``done``
    (:func:`_state.done_tasks`) and the tasks ``begun`` (:func:`_tasks.in_progress`)."""

    shipped: frozenset[int]
    claimed: frozenset[int]
    done: frozenset[int]
    begun: frozenset[int]


def progress(cards: Mapping[int, BareCard], reads: _git.Reads) -> Progress:
    """:class:`Progress` from one read of git (``reads``), each task read by ``cards``."""
    return Progress(frozenset(reads.shipped.cards), frozenset(reads.claims),
                    done_tasks(cards, reads.shipped.tasks, reads.claims),
                    in_progress(cards, reads.claims))


def wanted(card: BareCard, cards: Mapping[int, BareCard], git: Progress) -> str | None:
    """The Status ``card`` shows, or None for none: the ONE answer (module docstring), git's
    first, then a drop, then Todo.  ``cards`` holds what a decision over it reads
    (``_command.with_closure``)."""
    if card.kind == TASK:
        held = (DONE if card.number in git.done else
                IN_PROGRESS if card.number in git.begun else None)
    elif card.kind in SHIPPABLE:
        if is_container(card, cards, git.shipped):
            return _CONTAINER[container_shown(card, cards, git.shipped)]
        held = (DONE if card.number in git.shipped else
                IN_PROGRESS if card.number in git.claimed else None)
    else:
        return None
    return held or (DROPPED if dropped(card.number, cards, set(git.shipped)) else TODO)


@dataclass(frozen=True)
class StatusWrite:
    """One write of a card's Status: ``card``'s board ``item``, the Status it ``held`` (None
    for none), and the one it is given, ``status`` (None: cleared)."""

    card: int
    item: str
    held: str | None
    status: str | None


def status_writes(statuses: Statuses, cards: Mapping[int, BareCard], reads: _git.Reads
                  ) -> list[StatusWrite]:
    """Each write that makes the board's Status show :func:`wanted` for every card it holds
    (``statuses.held``), in card order: a card already showing its answer is no write, so a
    run that finds the board right writes nothing, and one a person edited is written back.
    ``cards`` holds each of them and what a decision over it reads."""
    git = progress(cards, reads)
    return [StatusWrite(number, item, held, status)
            for number, (item, held) in sorted(statuses.held.items())
            if (status := wanted(cards[number], cards, git)) != held]


def unwritable(statuses: Statuses) -> str | None:
    """Why no card's Status can be written, as a REPORT line: the board has no single-select
    field named Status, or that field lacks one of :data:`setup_tracker.STATUS_OPTIONS`
    (:func:`setup_tracker.lacking`, ruling plan#1906); None when every one can be."""
    if statuses.field is None:
        return (f"the board has no single-select field named {GITHUB_STATUS_FIELD!r} (GitHub's "
                "built-in one was renamed), so no card's Status is written: rename it back "
                "(`python -m tools.quill.setup_tracker` checks it)")
    if missing := lacking(statuses.options):
        return (f"the board's {GITHUB_STATUS_FIELD} lacks {', '.join(missing)}, so no card's "
                f"Status is written until it holds {', '.join(STATUS_OPTIONS)} (rulings "
                f"plan#1873, plan#1906): add {', '.join(missing)} on the web, in the board's "
                f"settings under {GITHUB_STATUS_FIELD}")
    return None


def sync_statuses(tracker: Tracker, cards: Mapping[int, Card], reads: _git.Reads,
                  dry_run: bool, scope: Collection[int] | None = None) -> list[str]:
    """``quill sync``'s last pass, display of the plan: the board's Status read, every card it
    holds that ``cards`` lacks read by its number, and each write :func:`status_writes` names
    made (or under ``dry_run`` printed and not made), each printed as it lands; none while
    the field cannot hold every answer (:func:`unwritable`).  Its REPORT lines, for ``sync``
    to print and exit 1 on.  With ``scope``, only the cards it numbers that the board holds
    are decided and written: ``quill watch``'s pass over the cards a moved branch names
    (:mod:`_watch`), ``cards`` holding each and what a decision over it reads.

    Raises:
        _tracker.TrackerError: when a card the board holds does not exist by its number.
    """
    statuses = tracker.board.statuses()
    if why := unwritable(statuses):
        return [why]
    if scope is not None:
        statuses = replace(statuses, held={number: held for number, held in statuses.held.items()
                                           if number in scope})
    held = with_closure(tracker, with_cards(tracker, dict(cards), statuses.held))
    verb = "would " if dry_run else ""
    for write in status_writes(statuses, held, reads):
        if not dry_run:
            tracker.board.set_status(write.item, statuses.field,
                                     None if write.status is None else
                                     statuses.options[write.status])
        was = write.held or "none"
        print(f"{verb}clear the board's {GITHUB_STATUS_FIELD} of {_label(held[write.card])}, "
              f"which shows none (it showed {was})" if write.status is None else
              f"{verb}show {_label(held[write.card])} as {write.status} in the board's "
              f"{GITHUB_STATUS_FIELD} (it showed {was})")
    return []
