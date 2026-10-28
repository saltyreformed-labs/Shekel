"""The ``quill show`` command (:func:`cmd_show`): a card's facts, among them the title of
each card linked to it and, for a step, each finding, ruling and question it owns (ruling
``balance:R-BAL395``, plan#1861's item 10); then its text, its comments and the code
repository's history for it.

Split out of the command module, which the list of the findings and rulings a step owns
would take past the 1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from tools.ci.gitcmd import DEV, MAIN
from tools.quill import _git
from tools.quill._command import Refused, _label, _one, _outside_parent, card_number
from tools.quill._comments import byline, unanswered
from tools.quill._notes import superseded
from tools.quill._state import OWNED, done_tasks, filing_unfinished, holder, never_split, owner
from tools.quill._tasks import in_progress
from tools.quill._tracker import Card, Tracker, TrackerError
from tools.quill.check import alias_mark
from tools.quill.setup_tracker import FILING, IN_ORDER, TASK

#: What a commit did to a ship marker, by its status in :func:`_git.history`.
_MARKER_CHANGES = {"A": "added", "M": "changed", "D": "deleted", "T": "changed the type of"}
_YES = {True: "yes", False: "no"}


def _resolve_reference(tracker: Tracker, text: str) -> int:
    """``plan#N`` -> N; an old id (``R-BAL80``, ``balance:X-bi-6-4d``) -> its titled card."""
    try:
        return card_number(text)
    except argparse.ArgumentTypeError:
        pass
    arc, _, ident = text.rpartition(":")
    alias = alias_mark(ident)
    found = [
        number for number, title in tracker.find_titles(ident)
        if title.startswith(alias)
    ]
    if arc:
        found = [n for n, card in tracker.cards(found).items() if arc in card.labels]
    if len(found) != 1:
        raise Refused(f"{text!r} names {len(found)} cards ({found}); name one as plan#N"
                      + ("" if arc else " or give its arc, e.g. balance:" + ident))
    return found[0]


def _card_lines(card: Card, tracker: Tracker, cards: dict[int, Card], reads: _git.Reads,
                listed: dict[int, Card]) -> list[str]:
    """What the tracker and git (``reads``) say about a card, one fact a line.  ``cards``
    holds every step linked below it (:func:`_steps_below`), and ``listed`` each card it
    lists as a child, a blocker or a card it owns (:func:`_listed`), whose lines are
    :func:`_link_lines`'.  A task says whether git says it is done
    (:func:`_state.done_tasks`), in place of whether it shipped, and whether it is in progress
    (:func:`_tasks.in_progress`, ruling plan#1872), and a task under it how many comments no
    answer has followed (:func:`_waiting`), claimed or not, so a session that claims a card
    sees a comment left on one of its tasks before the claim.  A card on the board that the
    order read does not list is in no order when it is a task; a step or question is in the
    order, so the read lags a write (``_tracker``'s doctrine), and it says so."""
    claim = reads.claims.get(card.number)
    done = done_tasks({**listed, card.number: card}, reads.shipped.tasks, reads.claims)
    begun = in_progress({**listed, card.number: card}, reads.claims)
    order = [n for n, _ in tracker.board.order()]
    state = "open" if card.is_open else f"closed ({card.state_reason})"
    ship = (f"done (git): {_YES[card.number in done]}\n"
            f"  in progress (git): {_YES[card.number in begun]}" if card.kind == TASK else
            f"shipped (git): {_YES[card.number in reads.shipped.cards]}")
    place = (f"#{order.index(card.number) + 1}" if card.number in order else
             "not on it" if card.board_item is None else
             "on it, but the board's order read does not list it yet: the board lags a write; "
             "show it again" if card.kind in IN_ORDER else
             "on it, in no order: the order is its steps and questions (R-BAL177)")
    lines = [
        _label(card),
        f"  state: {state}",
        f"  {ship}",
        f"  board: {place}",
        f"  claim: {holder(claim)}" if claim else "  claim: none",
        (f"  parent: plan#{card.parent}" if card.parent else
         f"  parent: {outside} (outside the tracker)" if (outside := _outside_parent(card))
         else "  parent: none"),
    ]
    lines += _link_lines(card, cards, reads, listed, (done, begun, _waiting(tracker, card)))
    if filing_unfinished(card):
        lines.append("  filing: not finished (R-BAL202) -- never offered until its `quill file` "
                     "command runs again; with that command lost, make its missing writes on "
                     f"the web and remove its {FILING!r} label last")
    lines += [f"  {link.what} outside the tracker: {link.issue} -- not offered until it is "
              "removed (R-BAL188)" for link in card.outside if link.what != "parent"]
    return lines


def _waiting(tracker: Tracker, card: Card) -> dict[int, int]:
    """How many comments of a person's no answer of quill's has followed
    (:func:`_comments.unanswered`) each task under ``card`` holds that holds one, by number: the
    comments of every task under it, claimed or not, read in one read of many cards
    (:meth:`_tracker.Tracker.comments_of`, ruling plan#1931), none for a card with no task."""
    return {number: len(held) for number, comments
            in tracker.comments_of(card.task_children).items()
            if (held := unanswered(comments, tracker.app_login))}


def _link_lines(card: Card, cards: dict[int, Card], reads: _git.Reads, listed: dict[int, Card],
                tasks: tuple[frozenset[int], frozenset[int], Mapping[int, int]]) -> list[str]:
    """A line for each card ``card`` links to, with its title from ``listed``: each child, in
    the card's order, its sub-issues' (the build order of its tasks, ruling plan#1868) --
    a step linked under it that is no leaf of it saying so (:func:`_state.never_split`, as
    ``next`` reads it, over ``cards``), a task whether git says it is done, the one in
    progress that it is, and one holding comments of a person's that no answer of quill's has
    followed how many (:func:`_comments.unanswered`, ruling plan#1924; ``tasks``: those done,
    those in progress, and how many each such task holds) --
    then each blocker, a finding's, a ruling's or a question's owner named so (its one
    blocker, :func:`_state.owner`, R-BAL395, as ``sync``'s report reads it), then each
    finding, ruling and question ``listed`` holds whose owner is ``card``, which it owns."""
    stale = set(never_split(card, cards, reads.shipped.cards))
    done, begun, waiting = tasks
    lines = [f"  child: plan#{child.number} ({child.kind}, "
             f"{'open' if child.is_open else 'closed'}"
             + ("; no leaf: closed while still being filed, so never part of the split, "
                "R-BAL205" if child.number in stale else "")
             + (f"; done (git): {_YES[child.number in done]}" if child.kind == TASK else "")
             + ("; in progress (git): yes" if child.number in begun else "")
             + (f"; {waiting[child.number]} comment{'s' if waiting[child.number] > 1 else ''} "
                "no answer of quill's has followed" if child.number in waiting else "")
             + f") {listed[child.number].title}"
             for child in card.children]
    held_by = owner(card)
    lines += [f"  {'owner (blocked by)' if blocker == held_by else 'blocked by'}: "
              f"plan#{blocker} {listed[blocker].title}" for blocker in card.blocked_by]
    lines += [f"  owns: plan#{number} ({owned.kind}, {'open' if owned.is_open else 'closed'}) "
              f"{owned.title}" for number, owned in sorted(listed.items())
              if owner(owned) == card.number]
    return lines


def _linked(tracker: Tracker, numbers: set[int], card: Card) -> dict[int, Card]:
    """The cards ``numbers``, each linked below ``card``, blocking it or blocked by it, read
    by number; :class:`TrackerError` when the read does not hold one the link names (a read
    that lags the link, or a card deleted or moved since).  No numbers, no read."""
    if not numbers:
        return {}
    found = tracker.cards(numbers)
    if absent := sorted(numbers - set(found)):
        raise TrackerError(f"plan#{absent[0]} is linked below plan#{card.number}, blocks it or "
                           "is blocked by it, but a read by its number does not hold it: run "
                           "show again")
    return found


def _steps_below(tracker: Tracker, card: Card) -> dict[int, Card]:
    """``card`` and every step linked below it, read by number: all that the leaf rule
    reads (:func:`_state.leaves` reads a linked step's own leaves, never a blocker or a
    parent), so the leaf rule's read is no more than it needs; the cards ``show`` lists
    are :func:`_listed`'s read."""
    cards, wanted = {card.number: card}, set(card.step_children)
    while wanted:
        found = _linked(tracker, wanted, card)
        cards.update(found)
        wanted = {number for step in found.values() for number in step.step_children} - set(cards)
    return cards


def _owned(tracker: Tracker, card: Card) -> set[int]:
    """The findings, rulings and questions a step owns (ruling ``balance:R-BAL395``: each is
    blocked by its owner step), found by the step's own blocking read
    (:meth:`_tracker.Tracker.blocking`), which only a step's ``show`` makes: no other kind
    owns a card.  Found only: :func:`_listed` reads each by its number, and that read
    decides whether ``card`` is its owner (:func:`_link_lines`), as ``sync``'s listings
    find and a read by number decides."""
    if card.kind != "step":
        return set()
    return {child.number for child in tracker.blocking(card.number) if child.kind in OWNED}


def _listed(tracker: Tracker, card: Card, read: dict[int, Card],
            owned: set[int]) -> dict[int, Card]:
    """Each card ``card`` lists as a child, a blocker or one of ``owned``
    (:func:`_owned`), whose title ``show`` prints, and, for a task, whose state: from
    ``read`` (the steps :func:`_steps_below` read already), and the rest read by number in
    one more read (:meth:`_tracker.Tracker.cards`: one request per
    :data:`_tracker._LOOKUP_BATCH` cards), made only when one is left."""
    numbers = {child.number for child in card.children} | set(card.blocked_by) | owned
    cards = {**read, **_linked(tracker, numbers - set(read), card)}
    return {number: cards[number] for number in numbers}


def _print_comments(tracker: Tracker, number: int) -> None:
    """Card ``number``'s comments, oldest first, each in full but a stop's note older than its
    newest (:func:`_notes.superseded`, ruling plan#1921), which a line names: the next session
    reads the current note, and the card keeps every older one."""
    comments = tracker.comments(number)
    if not comments:
        print("no comments")
    older = superseded(comments, tracker.app_login)
    for index, comment in enumerate(comments):
        if index in older:
            print(f"== comment, {byline(comment)}: a stop's note, superseded by the newest, of "
                  f"{older[index].created_at}; the card holds its text")
            continue
        print(f"== comment, {byline(comment)}")
        print(comment.body.rstrip())


def cmd_show(args, tracker: Tracker, root: Path) -> int:
    """A card -- the title of each card linked below it, blocking it or, for a step, each
    finding, ruling and question it owns (R-BAL395), and each task under it holding a comment
    no answer has followed saying so (ruling plan#1924), among its facts -- then its text, its
    comments oldest first, each in full but a stop's note older than the card's newest, which
    a line names (:func:`_notes.superseded`, ruling plan#1921), and the code repository's
    history for it: each commit on ``dev`` or ``main`` that added, changed or deleted its ship
    marker (:func:`_git.history`), newest first."""
    number = _resolve_reference(tracker, args.card)
    card = _one(tracker, number)
    reads = _git.read(root)
    steps = _steps_below(tracker, card)
    listed = _listed(tracker, card, steps, _owned(tracker, card))
    print("\n".join(_card_lines(card, tracker, steps, reads, listed)))
    print()
    print(tracker.body(number))
    print()
    _print_comments(tracker, number)
    print()
    history = _git.history(root, number)
    for status, sha, subject in history:
        print(f"{_MARKER_CHANGES[status]} {_git.marker(number)}: {sha[:12]} {subject}")
    if not history:
        print(f"no commit on {DEV} or {MAIN} adds, changes or deletes {_git.marker(number)}")
    return 0
