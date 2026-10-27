"""The ``quill file`` command: a step, finding, ruling, question or task filed after
:func:`check.violations` passes, a filing a failure cut short finished by the same
command (R-BAL186), and nothing written over one an earlier run filed -- ruling
``balance:R-BAL202``'s mark, its leaf rule (R-BAL204) and ruling closes (R-BAL206) --
and no leaf filed under a step a branch has claimed, or one with a task still open.  A
task (ruling ``balance:R-BAL386``) is filed under the piece of work it is part of, and
carries no arc label; a finding and a ruling are blocked by their owner step, never its
sub-issues (``R-BAL395``); a leaf is filed in its split step's milestone while that step
holds one, which it then gives up (:func:`_leaf_milestone`).  ``quill.py``'s module docstring is
the command's usage.

Split out of the command module at X-cx L2's leaf C, whose change would otherwise take
that module past the 1,000 lines ``too-many-lines`` allows (it has been ``quill.py``
since the tool's rename).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.quill import _findings, _git
from tools.quill._command import (
    Refused,
    _label,
    _one,
    _outside_parent,
    _read,
    on_board,
    with_closure,
)
from tools.quill._state import (
    OWNED,
    filing_ended,
    filing_unfinished,
    holder,
    is_container,
    is_live,
    leaf_placement,
    leaves,
    leaves_below,
    release_hint,
    withdrawn_filing,
)
from tools.quill._tracker import Card, Tracker, TrackerError
from tools.quill.check import (
    Draft,
    Owner,
    in_ruling_shape,
    normalized,
    ruling_body,
    ruling_question,
    violations,
)
from tools.quill.setup_tracker import FILING, TASK, unmarked


@dataclass(frozen=True)
class _Asked:
    """The question card a ruling is converted from, its body as read, and whether an EDIT
    of quill's (not its filing) ever saved it in a ruling's shape -- an earlier
    conversion's work, whoever saved the card since."""

    card: Card
    body: str
    converted_before: bool


def _converted_body(asked: _Asked, answer: str) -> str:
    """The body an answered question takes as its ruling: its question, then ``answer``.

    A conversion cut short may already have written it (R-BAL186), so a body
    that is exactly what this conversion writes gives back its question, never
    wrapped twice.  A body in a ruling's shape with ANOTHER answer, on a card an
    earlier conversion once rewrote (an edit of the tool's saved a ruling's
    shape), is that conversion's work -- touched up since or not -- not the
    developer's question, and is refused, as is a card already typed a ruling
    with another answer: an answer is the developer's record, and another answer
    is another ruling.  Any other body is the developer's question, word for
    word, whatever marks it holds -- so a question quoting a ruling's shape on a
    card the tool once rewrote is refused too, and is put back on the web
    instead.
    """
    question = ruling_question(asked.body, answer)
    if question is None and asked.card.kind == "ruling":
        raise Refused(f"{_label(asked.card)} is already a ruling, and not with this answer: "
                      "its answer is the developer's record, so another answer is another ruling")
    if question is None and asked.converted_before and in_ruling_shape(asked.body):
        number = asked.card.number
        raise Refused(f"{_label(asked.card)}'s text is an earlier conversion's, with another "
                      f"answer: put the developer's question back first (`quill spec-history "
                      f"plan#{number}` lists its saved versions, `quill spec-revert plan#{number} "
                      "--to EDIT_ID` restores one), then run this again")
    return ruling_body(asked.body if question is None else question, answer)


#: What a filing with no parent holds of git, which it does not read (:func:`_filing_for`).
_NOTHING_READ = _git.Reads(_git.Shipped(frozenset(), frozenset(), ()), {}, {})


@dataclass(frozen=True)
class _LeafMilestone:
    """The milestone a new leaf is filed in, its split step's, which the filing then clears
    (``leaf``), and, when the split step holds none, each leaf below it in one, by number,
    with that milestone's (``below``), named for a person to choose from; None, or empty,
    for none."""

    leaf: int | None
    below: tuple[tuple[int, int], ...]


#: What a filing that is no leaf reads of milestones: nothing.
_NO_MILESTONE = _LeafMilestone(None, ())


def _leaf_milestone(tracker: Tracker, parent: Card, cards: dict[int, Card],
                    shipped: frozenset[int]) -> _LeafMilestone:
    """The milestone a new leaf of ``parent`` is filed in: its split step's (the
    coordinator's code call, 14:4x 2026-10-21, under ruling ``balance:R-BAL274``'s one
    milestone per card), ``parent``'s own while it holds one, which the filing then clears:
    a step split into smaller steps leaves the board, and its milestone would count it
    beside its leaves.

    Once ``parent`` holds none -- split already, or never in one -- the leaf is filed in
    none, and each leaf below it in a milestone (nested splits included,
    :func:`_state.leaves_below`) is named with it, as that leaf's, for a person to set one
    on the web: the tracker records no outcome for the split step.  None is
    taken from them: a leaf X-cx's migration filed may hold its OWN outcome's, not one it
    carried down from the nearest split step above holding one, and the tracker cannot tell
    the two apart (X-cx S3a's review, HIGH 1: on the plan at the cutover, 17 of 32 split
    steps would have filed a new leaf in a sibling's outcome, recurrence R5's in place of
    R5's own).
    Where a split step's outcome should live once its first leaf carries it is a question
    for after the cutover."""
    below = [card.number for card in leaves_below(parent, cards, shipped)]
    held = tracker.milestones_of([parent.number, *below])
    if (own := held.get(parent.number)) is not None:
        return _LeafMilestone(own, ())
    return _LeafMilestone(None, tuple((number, held[number]) for number in below
                                      if held.get(number) is not None))


@dataclass(frozen=True)
class _Filing:
    """What ``file`` writes, and what it read to write it.

    ``draft``: the card.  ``parent``: the card it is linked to, read by number -- a
    finding's or ruling's owner, which it is blocked by (``R-BAL395``), or the step or piece
    of work a step or task is a sub-issue of; None for a top-level card.  ``cards``: every
    card a decision over that parent reads (:func:`with_closure`: the cards linked under it among
    them, each read by number); ``shipped``: the cards git says shipped; and
    ``claims``: every claim, by card, from the same read of git
    (:func:`_git.read`) -- all three empty with no parent.  ``asked``: for a
    ruling converted from a question, that question as read.  ``milestone``: for a leaf,
    the milestone it is filed in, which its split step gives up (:func:`_leaf_milestone`).
    """

    draft: Draft
    parent: Card | None
    cards: dict[int, Card]
    shipped: frozenset[int]
    claims: dict[int, _git.Claim]
    asked: _Asked | None
    milestone: _LeafMilestone


def _filing_for(args, tracker: Tracker, root: Path) -> _Filing:
    """The card ``file`` would write, its parent, and what was read to decide over them.

    Whether the parent is still LIVE is git's answer and the cards', read the
    way ``next`` reads a step (:func:`_state.is_live`), never the card's open
    state alone, which is display.  A task's parent is a piece of work whose own filing
    has finished, never a step split into smaller steps: no change ships one, so no task
    under it is ever done.
    """
    parent_number = getattr(args, "owner", None) or getattr(args, "parent", None)
    parent = _one(tracker, parent_number) if parent_number else None
    if args.kind == "step" and parent is not None and filing_unfinished(parent):
        raise Refused(f"{_label(parent)}'s own filing has not finished (R-BAL202): finish it "
                      "with the same `quill file` command first, then split it -- a leaf under "
                      "it would make it a split step its own filing puts on the board")
    if args.kind == TASK and filing_unfinished(parent):
        raise Refused(f"{_label(parent)}'s own filing has not finished (R-BAL202): finish it "
                      "with the same `quill file` command first, then file its tasks")
    owner, cards, reads = None, {}, _NOTHING_READ
    if parent is not None:
        reads = _git.read(root)
        cards = with_closure(tracker, {parent.number: parent})
        owner = Owner(parent.kind, is_live(parent.number, cards, reads.shipped.cards),
                      is_container(parent, cards, reads.shipped.cards))
    if args.kind == TASK and is_container(parent, cards, reads.shipped.cards):
        raise Refused(f"{_label(parent)} is split into smaller steps (R-BAL177): a task is a "
                      "piece of one of them, so file it under that one")
    asked = None
    if args.kind in ("step", "question", TASK):
        body = _read(args.body_file)
    elif args.kind == "finding":
        body = args.text
    elif args.from_question:
        card = _one(tracker, args.from_question)
        text, versions = tracker.edits(card.number)
        asked = _Asked(card, text, any(version.editor == tracker.app_login
                                       and in_ruling_shape(version.body)
                                       for version in versions[1:]))
        body = _converted_body(asked, _read(args.answer_file))
    else:
        body = ruling_body(_read(args.question_file), _read(args.answer_file))
    arc = () if args.kind == TASK else (args.arc,)
    labels = tuple(dict.fromkeys((*arc, *(getattr(args, "label", None) or ()))))
    return _Filing(Draft(args.kind, args.title, body, labels, owner), parent, cards,
                   reads.shipped.cards, reads.claims, asked,
                   _leaf_milestone(tracker, parent, cards, reads.shipped.cards)
                   if args.kind == "step" and parent is not None else _NO_MILESTONE)


def _place_leaf(tracker: Tracker, leaf: Card, filing: _Filing) -> None:
    """Put a leaf where the step it splits sat (R-BAL179), each board write printed as it
    lands: onto the board if it is not on it, moved to its place, and the split step off
    the board if it is still on it.  A leaf is placed only while its filing is unfinished
    (R-BAL202), so its move is made again on every run until its last write lands."""
    parent = filing.parent
    where = leaf_placement(tracker.board.order(), parent, leaf.number, filing.cards,
                           filing.shipped)
    item = leaf.board_item
    if item is None:
        item = tracker.board.add(leaf)
        print("  board: added at the bottom")
    if where.move:
        shown = tracker.board.place(item, where.after)
        print(f"  board: moved {where.note}" + ("" if shown else
                                                " (the board has not shown it yet)"))
    else:
        print(f"  board: not moved: {where.note}")
    if where.remove is not None:
        tracker.board.remove(where.remove)
        print(f"  board: plan#{parent.number} left it, as a container")


def _same_filing(tracker: Tracker, draft: Draft, parent: Card | None) -> Card | None:
    """The card an earlier run of this filing made, if one exists: a card with ``draft``'s
    kind, title and text that is OPEN, or still MARKED in any state (a filing the tool
    began: unfinished, or ended by a person's or ``quill drop``'s close) -- or, for a
    RULING, the one kind its own filing closes, ANY ruling its owner blocks
    (:meth:`_tracker.Tracker.blocking`, ``R-BAL395``), read by number.  A ruling is
    blocked by its owner before its filing closes it and removes its mark, so one whose
    filing finished is found by its owner, whatever answer a write of it lost.  The caller
    finishes a filing cut short and files nothing over one that ended (R-BAL186, R-BAL202).

    Not found: a card of another kind closed after its filing finished (by ``sync`` as
    shipped, or dropped since), whose command run again files another; and a ruling a
    person re-homed to another owner, or whose owner link a person removed, which this
    command files again for the owner it names (a listing that lags such a close may still
    show the card open: it is then read closed, and nothing is written).  A filing that ended
    (:func:`_state.filing_ended`) is the answer only when no other card matches:
    closing one as a duplicate is how a person withdraws an extra.  Refused when two
    matching cards stand."""
    found = {number: card for number, card in tracker.marked().items()
             if card.kind == draft.kind}
    if draft.kind == "ruling":
        found.update(tracker.cards(card.number for card in tracker.blocking(parent.number)
                                   if card.kind == "ruling"))
    candidates = {**tracker.open_cards(), **found}
    same = [
        card for _, card in sorted(candidates.items())
        if card.kind == draft.kind and card.title.strip() == draft.title.strip()
        and normalized(tracker.body(card.number)) == normalized(draft.body)
    ]
    standing = [card for card in same if not filing_ended(card)]
    if len(standing) > 1:
        raise Refused(f"{len(standing)} cards have this kind, title and text "
                      f"({', '.join(f'plan#{card.number}' for card in standing)}): withdraw "
                      "the extras -- `quill drop` an open one; reopen a closed one and close it "
                      "as not planned on the web")
    return (standing or same or [None])[0]


def _refuse_relabel(card: Card, draft: Draft) -> None:
    """Refuse a card with ``draft``'s kind, title and text whose labels, the filing mark
    aside, are not ``draft``'s: relabelling is a person's call."""
    labels = sorted(unmarked(card.labels))
    if labels != sorted(draft.labels):
        raise Refused(f"{_label(card)} has this kind, title and text but the labels "
                      f"{labels}, not {sorted(draft.labels)}: relabel it by hand")


def _refuse_rehoming(card: Card, kind: str, parent: Card | None) -> None:
    """Refuse to file ``card`` as a ``kind`` under ``parent`` (None: at the top level) when it
    is a sub-issue of another card, in the tracker or outside it -- or, filed as a finding or
    ruling, a sub-issue of any card, or blocked by any card but ``parent``, its owner
    (``R-BAL395``): re-homing a card is a person's call."""
    if kind in OWNED:
        _refuse_reowning(card, parent)
        return
    if outside := _outside_parent(card):
        raise Refused(f"{_label(card)} is a sub-issue of {outside}, outside the tracker: "
                      "re-homing a card is done by hand")
    if card.parent is not None and card.parent != (parent.number if parent else None):
        where = f"plan#{parent.number}" if parent else "a top-level card, as this filing names"
        raise Refused(f"{_label(card)} is a sub-issue of plan#{card.parent}, not {where}: "
                      "re-homing a card is done by hand")


def _refuse_reowning(card: Card, owner: Card) -> None:
    """Refuse to file ``card``, a finding or ruling, for ``owner`` when a link of it says
    otherwise: a sub-issue of any card, which the parent's progress would count, or blocked
    by any card but ``owner`` (``R-BAL395``), in the tracker or outside it.  Re-homing is a
    person's call, made by hand -- for a finding blocked by one other card of the tracker,
    with ``quill hand`` (step plan#1772), which hands it to the owner this filing names."""
    outside = {what: [f"{link.issue}, outside the tracker" for link in card.outside
                      if link.what == what] for what in ("parent", "blocker")}
    if under := ([f"plan#{card.parent}"] if card.parent is not None else []) + outside["parent"]:
        raise Refused(f"{_label(card)} is a sub-issue of {under[0]}, but a finding or ruling is "
                      f"blocked by its owner, plan#{owner.number}, and is no card's sub-issue "
                      "(R-BAL395): re-homing a card is done by hand")
    blockers = [number for number in card.blocked_by if number != owner.number]
    if others := [f"plan#{number}" for number in blockers] + outside["blocker"]:
        how = (f"`quill hand plan#{card.number} --to plan#{owner.number}` re-homes it"
               if card.kind == "finding" and len(others) == 1 and blockers else
               "re-homing a card is done by hand")
        raise Refused(f"{_label(card)} is blocked by {', '.join(others)}, not only by "
                      f"plan#{owner.number}, the owner this filing names (R-BAL395): {how}")


def _attach(tracker: Tracker, card: Card, kind: str, parent: Card | None) -> None:
    """Link ``card``, filed as a ``kind``, to ``parent`` (None: no card), unless it already
    is: a finding or ruling blocked by it, its owner (``R-BAL395``), any other kind its
    sub-issue; the caller has already refused a card linked otherwise."""
    if parent is None:
        return
    if kind in OWNED and parent.number in card.blocked_by:
        print(f"  already blocked by plan#{parent.number}, its owner")
        return
    if kind in OWNED:
        tracker.block(card.number, parent)
        print(f"  blocked by plan#{parent.number}, its owner (R-BAL395)")
        return
    if card.parent == parent.number:
        print(f"  already a sub-issue of plan#{parent.number}")
        return
    tracker.add_child(parent.number, card)
    print(f"  a sub-issue of plan#{parent.number}")


def _refuse_beside_unfinished(filing: _Filing, leaf: Card | None) -> None:
    """Refuse to file a leaf of ``filing``'s parent -- a new one, or ``leaf``'s filing
    again -- while another leaf of it has an unfinished filing (R-BAL204): its place is
    not known to be right, so no leaf is placed by it.  Each leaf's mark is read by its
    card number (:func:`_filing_for`), not from the listing of marked cards, which may
    lag its removal."""
    parent = filing.parent
    others = [filing.cards[number]
              for number in sorted(leaves(parent, filing.cards, filing.shipped))
              if filing_unfinished(filing.cards[number])
              and (leaf is None or number != leaf.number)]
    if others:
        names = ", ".join(f"plan#{card.number}" for card in others)
        raise Refused(f"the filing of {names}, of plan#{parent.number}'s leaves, has not "
                      "finished (R-BAL204): finish it first, then run this again.  Unless a "
                      "`quill file` command is filing it now, run that command again; with that "
                      "command lost, or the card's title or text changed since, make its "
                      f"missing writes on the web and remove its {FILING!r} label last "
                      "(`quill show` shows its parent and board place)")


def _refuse_under_a_claim(filing: _Filing, leaf: Card | None) -> None:
    """Refuse to file a leaf of ``filing``'s parent -- a new one, or ``leaf``'s filing again
    -- while a claim names that parent (C2 review M4): a branch is building it as one piece
    of work, and a step split into smaller steps is never shipped itself (R-BAL177), so a
    split would leave the claim on a step no commit ships.  A run that finishes an earlier
    filing is refused too: where its link has not landed (a leaf whose link failed,
    R-BAL202, or one a person reopened after ``sync`` unlinked it), that link IS the
    split; where it has, finishing would complete a split over the claim.  Refused, the
    leaf stays marked, so it is never offered and ``next`` and ``sync`` name it.  The
    refusal names the ways out: release the claim, or -- when ``leaf`` is a card an
    earlier run created, git does not say it shipped, and no other leaf splits the step
    -- drop it, which keeps the step whole (R-BAL205).

    The claims are ``filing``'s, read before any write of the leaf's.  A claim this refusal
    cannot see still lands on a split step: one made after this read and before the
    link lands (GitHub has no conditional sub-issue write); one made while a leaf closed
    while still being filed left the step plain, before a person reopened that leaf or
    removed its stale mark; one older than this refusal; and a sub-issue a person links
    under a claimed step on the web.  ``sync`` reports every such claim
    (:func:`_state.sync_plan`)."""
    parent = filing.parent
    claim = filing.claims.get(parent.number)
    if claim is None:
        return
    others = set(leaves(parent, filing.cards, filing.shipped)) - {getattr(leaf, "number", None)}
    keep = ("" if leaf is None or leaf.number in filing.shipped or others else
            f"; or, to keep plan#{parent.number} whole, `quill drop plan#{leaf.number}`: its "
            "filing never finished, so it was never part of the split (R-BAL205)")
    raise Refused(f"plan#{parent.number} is claimed, {holder(claim)}: a branch is "
                  "building it as one piece of work, and a step "
                  "split into smaller steps is never shipped itself (R-BAL177), so no leaf is "
                  "filed under it while it is claimed.  Release the claim first "
                  f"({release_hint(claim)}, by the session that holds it, or once its work is "
                  "abandoned), then run this again" + keep)


def _refuse_over_tasks(filing: _Filing) -> None:
    """Refuse to file a leaf of ``filing``'s parent -- a new one, or one's filing again -- while
    a task under that parent is open (R-BAL386): a step split into smaller steps is never
    shipped itself (R-BAL177), so no task under it could ever be done.  Its tasks are dropped
    first, and each filed again under the smaller step it is a piece of."""
    parent = filing.parent
    if tasks := [child.number for child in parent.children
                 if child.kind == TASK and child.is_open]:
        names = ", ".join(f"plan#{number}" for number in tasks)
        raise Refused(f"plan#{parent.number} has tasks still open ({names}): a step split into "
                      "smaller steps is never shipped itself (R-BAL177), so no task under it "
                      "could ever be done.  Drop each first (`quill drop plan#N`), then file it "
                      "again under the smaller step it is a piece of")


def _ended_unfinished(card: Card) -> str:
    """Why nothing is filed over ``card``, whose filing a decision ended
    (:func:`_state.withdrawn_filing`), and how a person restores it: filing it again
    would undo that decision."""
    how = (f"closed as {card.state_reason.lower().replace('_', ' ')}" if card.state_reason
           else "closed")
    marked = FILING in card.labels
    if card.kind == "ruling":
        return (f"{_label(card)} has this kind, title and text, and was withdrawn ({how}"
                f"{', before its filing finished' if marked else ''}, R-BAL206), so nothing is "
                "filed over it.  To record it after all, reopen it and close it as completed "
                "on the web" + (", then run this again to finish its filing" if marked else ""))
    return (f"{_label(card)} has this kind, title and text, and was {how} before its filing "
            "finished, so nothing is filed over it.  To file it after all, reopen it on the "
            "web, then run this again to finish its filing")


def _filed_already(card: Card, kind: str, parent: Card | None) -> int:
    """Nothing is written over a filing that finished, or is moot.  ``card`` carries no
    filing mark -- open, a ruling closed as completed, or a card closed since that a
    lagging listing still showed open -- so each of its filing's writes landed (R-BAL202),
    and a person may have moved or edited it since; or it shipped in git and was closed
    still marked, its filing stopped at its last write, which decides nothing now
    (:func:`_state.withdrawn_filing`).  Refused when it sits at the top level and this
    filing names ``parent`` -- for a ``kind`` that is a finding or ruling, when no card
    blocks it (``R-BAL395``): re-homing a card is a person's call (one linked otherwise the
    caller has already refused, :func:`_refuse_rehoming`)."""
    if kind in OWNED and parent is not None and parent.number not in card.blocked_by:
        raise Refused(f"{_label(card)} has this kind, title and text, and its filing finished "
                      f"blocked by no card, not by plan#{parent.number}, its owner: re-homing "
                      "a card is done by hand")
    if card.parent is None and parent is not None and kind not in OWNED:
        raise Refused(f"{_label(card)} has this kind, title and text, and its filing finished "
                      f"at the top level, not under plan#{parent.number}: re-homing a card is "
                      "done by hand")
    if FILING in card.labels:
        print(f"{_label(card)} has this kind, title and text and shipped in git; it was "
              f"closed before its filing's last write removed its {FILING!r} label, which "
              "decides nothing now, so nothing is written (R-BAL202)")
        return 0
    print(f"{_label(card)} is filed already: a card with this kind, title and text exists "
          "and its filing finished (it carries no filing mark), so nothing is written "
          "(R-BAL186, R-BAL202)")
    return 0


def _shipped_for(filing: _Filing, card: Card, root: Path) -> frozenset[int]:
    """The cards git says shipped, to decide over ``card``: those read with ``filing``'s
    parent, else read now -- for a top-level filing, which reads git nowhere else, only
    when ``card`` was closed while still marked, the one card the answer decides."""
    if filing.parent is not None or card.is_open or FILING not in card.labels:
        return filing.shipped
    return _git.read(root).shipped.cards


def cmd_file(args, tracker: Tracker, root: Path) -> int:
    """File a step, finding, ruling, question or task, after :func:`check.violations` passes;
    finish one a failure cut short (R-BAL186); write nothing over one the earlier run
    filed (:func:`_same_filing`): refused when a decision ended it
    (:func:`_ended_unfinished`), else filed already (:func:`_filed_already`).  A leaf
    whose filing would write anything is refused under a claimed step
    (:func:`_refuse_under_a_claim`), beside an unfinished sibling
    (:func:`_refuse_beside_unfinished`) and under a step with a task still open
    (:func:`_refuse_over_tasks`).

    Every card is created marked, :data:`setup_tracker.FILING` sent in the call
    that creates it (by :meth:`_tracker.Tracker.create`, never through ``--label``,
    so :func:`check.violations` never sees it), and the mark is removed last
    (R-BAL202).  So a card still marked is
    a filing some of whose writes may not have landed: run again, the same command
    makes every write of the filing that the card does not show done -- the link,
    the close, the board place, and for a leaf the move whenever it has a place to
    go, since no read can tell the tool's move from a person's drag -- then
    removes the mark.
    """
    filing = _filing_for(args, tracker, root)
    problems = violations(filing.draft)
    if problems:
        raise Refused("not filed:\n  " + "\n  ".join(problems))
    if filing.asked is not None:
        return _convert_question(args, tracker, filing.draft, filing.parent, filing.asked)
    card = _same_filing(tracker, filing.draft, filing.parent)
    if card is not None:
        fresh = tracker.cards([card.number]).get(card.number)
        if fresh is None:
            raise TrackerError(f"plan#{card.number} is listed, but a read by its number does not "
                               "hold it (that read has not caught up yet, or the card was deleted "
                               "or moved since): run the same command again")
        card = fresh
        if withdrawn_filing(card, _shipped_for(filing, card, root)):
            raise Refused(_ended_unfinished(card))
        _refuse_rehoming(card, args.kind, filing.parent)
        _refuse_relabel(card, filing.draft)
        if not filing_unfinished(card):
            return _filed_already(card, args.kind, filing.parent)
    if args.kind == "step" and filing.parent is not None:
        _refuse_under_a_claim(filing, card)
        _refuse_beside_unfinished(filing, card)
        _refuse_over_tasks(filing)
    if card is None:
        card = _created(args.kind, tracker, filing.draft, filing.milestone)
    else:
        print(f"finishing {_label(card)}: its filing has not finished (it is still marked "
              f"{FILING!r}), so this command finishes it rather than filing another (R-BAL186, "
              "R-BAL202)")
    _finish(tracker, card, filing)
    return 0


def _created(kind: str, tracker: Tracker, draft: Draft, milestone: _LeafMilestone) -> Card:
    """A new card filed from ``draft``, marked in the call that creates it (R-BAL202), and a
    leaf in its split step's milestone, while that step holds one, in that call too
    (:func:`_leaf_milestone`), as read back; a card that cannot be read back yet is a
    failed call, not a refusal: it was filed."""
    number = tracker.create(kind, draft.title, draft.body, draft.labels,
                            milestone=milestone.leaf)
    print(f"filed plan#{number}")
    if milestone.leaf is not None:
        print(f"  in milestone #{milestone.leaf}, its split step's")
    if milestone.below:
        held = ", ".join(f"plan#{leaf} is in milestone #{number}"
                         for leaf, number in milestone.below)
        print(f"  in no milestone: its split step holds none, so the tracker records no "
              f"outcome for it; below it, {held}: if this leaf belongs with one of them, set "
              "it on the web")
    card = tracker.cards([number]).get(number)
    if card is None:
        raise TrackerError(f"plan#{number} was filed but cannot be read back yet: once "
                           f"`quill show plan#{number}` reads it, run the same command again "
                           "to finish it (R-BAL186, R-BAL202); run while GitHub's lists of "
                           "open and marked cards do not hold it yet, it files a second card")
    print(f"  {_label(card)}")
    return card


def _finish(tracker: Tracker, card: Card, filing: _Filing) -> None:
    """Every write of a filing after the create that ``card`` does not show done, each
    printed as it lands, then its mark removed, last (R-BAL202): the link, a ruling's
    close, a leaf's place and its split step's milestone cleared (after the leaf's create
    carried it, so a run cut short between them leaves it held, and the same command run
    again clears it), then each open finding the split step owns handed to the leaf, which a
    split step never owns (ruling plan#1784, :func:`_findings.hand_on`: a run cut short
    between a finding's two writes leaves it two owners, and the same command run again
    finishes it), a top-level step's or question's board place, or a task's under a step
    (:func:`_command.on_board`, ruling plan#1814), at the bottom, where the order does not
    read it."""
    kind, parent = filing.draft.kind, filing.parent
    _attach(tracker, card, kind, parent)
    if kind == "ruling" and card.is_open:
        tracker.close(card.number, "completed")
        print("  closed: a ruling is a record")
    elif kind == "ruling":
        print("  closed already")
    elif kind == "step" and parent is not None:
        _place_leaf(tracker, card, filing)
        if (split := filing.milestone.leaf) is not None:
            tracker.set_milestone(parent.number, None)
            print(f"  plan#{parent.number} left milestone #{split}: a split step keeps none")
        for finding in _findings.owned(tracker, parent.number, filing.shipped):
            _findings.hand_on(tracker, finding, card, parent, prefix="  ")
    elif on_board(kind, parent) and card.board_item is None:
        tracker.board.add(card)
        print("  board: added at the bottom")
    elif on_board(kind, parent):
        print("  board: already on it")
    tracker.unmark(card.number)
    print(f"  unmarked: plan#{card.number}'s filing is finished")


def _convert_question(args, tracker: Tracker, draft: Draft, owner: Card, asked: _Asked) -> int:
    """An answered question becomes its ruling: one card, so the question is never copied.

    Each write is skipped when it already landed, so the same command finishes a
    conversion a failure cut short (R-BAL186): a card already typed a ruling but
    still open, with this answer, is one.  Its parent is checked before anything
    is written.
    """
    question = asked.card
    if not question.is_open or question.kind not in ("question", "ruling"):
        raise Refused(f"{_label(question)} is not an open question")
    if filing_unfinished(question):
        raise Refused(f"{_label(question)}'s filing has not finished (R-BAL202): finish it with "
                      "the `quill file` command that filed it first, then convert it")
    if args.arc not in question.labels:
        raise Refused(f"{_label(question)} is not in the {args.arc} arc; pass its own --arc")
    _refuse_rehoming(question, "ruling", owner)
    if question.kind == "ruling":
        print(f"finishing {_label(question)}: its conversion into a ruling was cut short "
              "(R-BAL186)")
    if normalized(asked.body) != normalized(draft.body):
        tracker.set_body(question.number, draft.body)
        print(f"  plan#{question.number}'s body: the question, then the answer")
    if question.kind != "ruling":
        tracker.retype(question.number, "ruling")
        print(f"  plan#{question.number} is now a ruling")
    if draft.title.strip() != question.title.strip():
        tracker.retitle(question.number, draft.title)
        print(f"  retitled {draft.title!r}")
    _attach(tracker, question, "ruling", owner)
    if question.board_item is not None:
        tracker.board.remove(question.board_item)
        print("  board: taken off it (a ruling is not in the order)")
    tracker.close(question.number, "completed")
    print("  closed: a ruling is a record")
    return 0
