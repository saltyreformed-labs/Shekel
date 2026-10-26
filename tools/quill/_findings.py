"""Findings in quill: a finding fixed inside its owner step's pull request ships in it (ruling
plan#1777, "With its fix, enforced"), and every open finding has exactly one owner, a step a
branch builds (ruling plan#1784, "One owner, always a leaf"; ``_state.can_own``).

A finding names its owner by GitHub's "blocked by" link to it (``R-BAL395``,
``_state.owner``).  Here are ``quill finding-file``, which writes a fixed finding's ship marker
``plan/ships/F`` into the working tree of its owner's branch for the session to commit with the
fix (:func:`cmd_finding_file`), and ``quill hand``, which hands an open finding from its one
owner to another step (:func:`cmd_hand`).  The marker is the finding's one record that it
shipped (``R-BAL359``), whichever branch wrote it, so ``quill sync`` and the release tool read
it unchanged.

Every other door that sets or changes a finding's owner keeps the one-owner rule with what is
here: ``quill block`` (:func:`refuse_block`), ``quill drop`` (:func:`refuse_orphaning`) and
the split, ``quill file step --parent``, which hands the split step's findings to its new leaf
(:func:`hand_on`, from ``_filing``), each over the findings a step still owes
(:func:`owned`).  And CI's landing check holds every pull request to it (``landing``'s rules
5 and 7): a claim writes another card's marker only as a finding its card owns
(:func:`foreign`), and is refused while its card owes a finding (:func:`unfixed`) or a step
above it does (:func:`unhanded`, the backstop for a hand edit on the web).

Split out of the command module and the landing check, which these would take past the
1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path

from tools.ci.gitcmd import DEV, MAIN
from tools.quill import _git
from tools.quill._command import Refused, _label, _one, with_cards, with_closure
from tools.quill._state import above, can_own, dropped, holder, owner
from tools.quill._tracker import BareCard, Card, CardReader, Tracker

#: What a marker ``quill finding-file`` writes says: the instant it was written
#: (:func:`_git.instant`), so each write is a new line, as each claim's marker is.
FIXED = "fixed "


def owed(card: BareCard, shipped: Collection[int]) -> bool:
    """Whether ``card`` is a finding still owed, the one spelling: not dropped
    (:func:`_state.dropped`) and not shipped by git's answer (``shipped``).  A finding the
    TOOL closed as completed whose marker a revert deleted is owed: it is not dropped."""
    return (card.kind == "finding" and card.number not in shipped
            and not dropped(card.number, {card.number: card}, set(shipped)))


def owned(reader: CardReader, step: int, shipped: Collection[int],
          held: dict[int, BareCard] | None = None) -> list[BareCard]:
    """The findings step ``step`` blocks (``R-BAL395``'s link) that are still owed
    (:func:`owed`), in card order, each read by its number, of the type ``reader`` reads: a
    step's read of what it blocks lists each card's type and state, never who closed it or
    why.  ``held``: the cards already read, so each is read once (:func:`_command.with_cards`;
    CI's landing check threads its own), which then holds those read here.  No card read when
    it blocks no finding.

    Raises:
        TrackerError: when a finding it blocks does not exist.
    """
    numbers = sorted(child.number for child in reader.blocking(step) if child.kind == "finding")
    if not numbers:
        return []
    cards = with_cards(reader, {} if held is None else held, numbers)
    return [cards[number] for number in numbers if owed(cards[number], shipped)]


def _outside_blockers(card: BareCard) -> list[str]:
    """The issues outside the tracker that ``card`` is blocked by (``R-BAL188``)."""
    return [link.issue for link in card.outside if link.what == "blocker"]


def unowned(card: BareCard) -> str:
    """Why ``card``, a finding :func:`_state.owner` names no owner for, has none, and how a
    person gives it its one owner: a link to an issue outside the tracker removed on the web
    (``R-BAL188``); with no blocker, ``quill block``; blocked by two cards, ``quill hand`` to
    the one that owns it; by more, ``quill block --remove`` of each other."""
    number = card.number
    if outside := _outside_blockers(card):
        return (f"it is blocked by {', '.join(outside)}, outside the tracker, which no branch "
                "of this repository builds: remove that link on the web first (R-BAL188)")
    if not card.blocked_by:
        return (f"it is blocked by no card: `quill block plan#{number} --by plan#S` gives it "
                "one, S a step a branch builds")
    names = ", ".join(f"plan#{held}" for held in card.blocked_by)
    if len(card.blocked_by) == 2:
        return (f"it is blocked by {names}, so it names no one owner: `quill hand "
                f"plan#{number} --to plan#S`, S the one of them that owns it, removes the "
                "other's link")
    return (f"it is blocked by {names}, so it names no one owner: `quill block plan#{number} "
            "--by plan#X --remove` each of them but its owner")


#: What a finding's owner is (``_state.can_own``), as a refusal names it.
_OWNER_RULE = ("a finding's owner is a step a branch builds: live work, never a step split "
               "into smaller steps (R-BAL395, plan#1784)")


def _refuse_owner(to: Card, card: Card, cards: dict[int, Card], shipped: frozenset[int]) -> None:
    """Refuse ``to`` as the owner of ``card``, a finding, unless it may own one
    (:func:`_state.can_own`)."""
    if not can_own(to.number, cards, shipped):
        raise Refused(f"{_label(to)} may not own {_label(card)}: {_OWNER_RULE}")


def _finding(tracker: Tracker, number: int) -> Card:
    """Card ``number``; :class:`Refused` when it does not exist or is no finding, before
    anything else is read."""
    card = _one(tracker, number)
    if card.kind != "finding":
        raise Refused(f"{_label(card)} is no finding: only a finding is marked fixed by its "
                      "owner's branch, or handed from one owner to another")
    return card


def _refuse_closed(card: Card, cards: dict[int, Card], shipped: frozenset[int]) -> None:
    """Refuse ``card``, a finding, when it shipped in git or was dropped: either leaves nothing
    to mark fixed or to hand on."""
    if card.number in shipped:
        raise Refused(f"{_label(card)} shipped in git ({_git.marker(card.number)} is on {DEV} "
                      f"or {MAIN}): nothing is left of it to mark fixed or to hand on")
    if dropped(card.number, cards, set(shipped)):
        raise Refused(f"{_label(card)} was dropped (closed as {card.state_reason}): nothing is "
                      "left of it to mark fixed or to hand on; reopen it on the web first if it "
                      "is work after all")


def cmd_finding_file(args, tracker: Tracker, root: Path,
                     clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Write finding ``plan#F``'s ship marker ``plan/ships/F`` (:func:`_git.marker`), one line,
    ``fixed <instant>``, into the working tree, for the session to commit with the fix: written
    only while the branch checked out is ``card/<its owner>``, the one step it is blocked by
    (``R-BAL395``), the step whose pull request fixes it (ruling plan#1777).  It writes no
    tracker state: ``sync`` closes the finding once git says it shipped.  Refused, with nothing
    written, for a card that is no finding, one shipped in git or dropped, one claimed as work
    of its own (``card/F``, whose own marker records it), one whose owner is no step a branch
    builds (:func:`_state.can_own`, ruling plan#1784), and any other branch checked out, or
    none."""
    card = _finding(tracker, args.finding)
    reads = _git.read(root)
    shipped = reads.shipped.cards
    cards = with_closure(tracker, {card.number: card})
    _refuse_closed(card, cards, shipped)
    if (claim := reads.claims.get(card.number)) is not None:
        raise Refused(f"{_label(card)} is claimed as work of its own, {holder(claim)}: that "
                      "claim's own marker records it, so it ships from its own branch")
    number = owner(card)
    if number is None:
        raise Refused(f"{_label(card)} has no one owner, so no branch may write its marker "
                      f"(R-BAL395): {unowned(card)}")
    if not can_own(number, cards, shipped):
        raise Refused(f"{_label(card)}'s owner is {_label(cards[number])}, and {_OWNER_RULE}, "
                      f"so no branch may write its marker: `quill hand plan#{card.number} --to "
                      "plan#S` hands it to one")
    branch = _git.card_branch(number)
    here = _git.checked_out(root)
    if here != branch:
        raise Refused(f"{_label(card)} is owned by plan#{number}, so its marker is written on "
                      f"{branch}, and {here or 'no branch'} is checked out: switch to {branch} "
                      f"(`quill claim plan#{number}` makes it), or, if another step fixes it, "
                      f"hand it to that one first (`quill hand plan#{card.number} --to "
                      "plan#S`)")
    path = root / _git.marker(card.number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{FIXED}{_git.instant(clock())}\n", encoding="utf-8")
    print(f"wrote {_git.marker(card.number)}: commit it with the fix and push {branch}; once "
          f"{branch} lands, git says {_label(card)} shipped, and `quill sync` closes it")
    return 0


def cmd_hand(args, tracker: Tracker, root: Path) -> int:
    """Hand open finding ``plan#F`` from its one owner to step ``plan#S`` (``R-BAL395``'s
    link): F blocked by S first, then the old owner's link removed, each write printed as it
    lands, so a run cut short leaves F blocked by both, naming no one owner, which ``quill
    sync`` reports and CI refuses on the old owner's pull request (fails closed); run again, it
    reads F blocked by S and exactly one other card as its own hand-off unfinished, and removes
    that other link.  S may own it (:func:`_state.can_own`): a step a branch builds, live work
    and never one split into smaller steps (ruling plan#1784).  Refused, with nothing
    written, for a card that is no finding, one shipped in git or dropped, an S that may not
    own it, a finding linked to an issue outside the tracker (``R-BAL188``), one S already
    owns, and one that names no one owner to hand on (:func:`unowned`)."""
    card = _finding(tracker, args.finding)
    to = _one(tracker, args.to)
    shipped = _git.read(root).shipped.cards
    cards = with_closure(tracker, {card.number: card, to.number: to})
    _refuse_closed(card, cards, shipped)
    _refuse_owner(to, card, cards, shipped)
    others = [number for number in card.blocked_by if number != to.number]
    if _outside_blockers(card):
        raise Refused(f"{_label(card)} is not handed on: {unowned(card)}")
    if to.number in card.blocked_by and not others:
        raise Refused(f"plan#{to.number} owns {_label(card)} already: nothing to hand on")
    if len(others) != 1:
        raise Refused(f"{_label(card)} is not handed on: {unowned(card)}")
    hand_on(tracker, card, to, cards[others[0]])
    return 0


def hand_on(tracker: Tracker, card: BareCard, to: Card, old: Card, prefix: str = "") -> None:
    """Hand ``card``, a finding, from ``old`` to ``to`` (``R-BAL395``'s link), in the one order
    that fails closed: blocked by ``to`` first, unless it already is (a hand-off cut short
    before), then ``old``'s link removed; each write printed as it lands, after ``prefix``.
    ``quill hand`` and the split (``_filing``) both hand a finding on here."""
    if to.number in card.blocked_by:
        print(f"{prefix}plan#{card.number} is blocked by plan#{to.number} already: an earlier "
              "hand-off was cut short, so this one finishes it")
    else:
        tracker.block(card.number, to)
        print(f"{prefix}plan#{card.number} is blocked by plan#{to.number}, its new owner "
              "(R-BAL395)")
    tracker.unblock(card.number, old)
    print(f"{prefix}plan#{card.number} is no longer blocked by plan#{old.number}: "
          f"plan#{to.number} owns it")


def refuse_block(tracker: Tracker, root: Path, card: Card, blocker: Card, remove: bool) -> None:
    """Refuse ``quill block`` of ``card``, a finding, by ``blocker`` (or with ``remove``, its
    link's delete) when it would break the one-owner rule (ruling plan#1784) for a finding
    still owed (:func:`owed`): a new link while it has any blocker, in the tracker or outside
    it (re-homing is ``quill hand``, :func:`cmd_hand`), or to a ``blocker`` that may not own it
    (:func:`_state.can_own`); a delete of the one link it has, which would leave it with no
    owner.  A finding shipped or dropped is linked as asked: it is owed by no one."""
    shipped = _git.read(root).shipped.cards
    cards = with_closure(tracker, {card.number: card, blocker.number: blocker})
    if not owed(card, shipped):
        return
    if remove:
        if card.blocked_by == (blocker.number,) and not _outside_blockers(card):
            raise Refused(f"plan#{blocker.number} is the one owner of {_label(card)}: removing "
                          "its link would leave an open finding with no owner (plan#1784); "
                          f"`quill hand plan#{card.number} --to plan#S` hands it to another "
                          f"step, or `quill drop plan#{card.number}` drops it")
        return
    if card.blocked_by or _outside_blockers(card):
        how = (f"`quill hand plan#{card.number} --to plan#{blocker.number}` hands it on"
               if owner(card) is not None else unowned(card))
        raise Refused(f"{_label(card)} has an owner already, and an open finding has one "
                      f"(R-BAL395, plan#1784): {how}")
    _refuse_owner(blocker, card, cards, shipped)


def refuse_orphaning(tracker: Tracker, steps: list[Card], shipped: frozenset[int]) -> None:
    """Refuse a drop that would leave an open finding with no owner (ruling plan#1784): while
    any of ``steps`` -- the card dropped, the leaves a split step's drop drops, and every step
    above the card, which a drop of its last leaf leaves dropped (R-BAL187), and which owns
    no finding but by a hand edit on the web -- owns a finding still owed (:func:`owned`),
    each named with the hand-off that frees it.  A step above is held whether or not this is
    its last leaf, as CI's landing check holds a claim under it: fail closed."""
    held = [(step, finding) for step in steps if step.kind == "step"
            for finding in owned(tracker, step.number, shipped)]
    if held:
        raise Refused("not dropped: a dropped step owns no open finding (plan#1784), so hand "
                      "each on first:\n  " + "\n  ".join(
                          f"`quill hand plan#{finding.number} --to plan#S`, owned by "
                          f"plan#{step.number}" for step, finding in held))


# -- CI's landing check (``landing``'s rules 5 and 7) -----------------------------------------


def foreign(card: BareCard, claim: BareCard | None) -> str | None:
    """Whether a marker of ``card`` written on the branch of claim ``claim`` (None when the
    change records no card of its claim, so none was read), which is no claim of ``card``'s
    own, passes: None when ``card`` is a finding whose one owner (:func:`_state.owner`,
    ``R-BAL395``) is ``claim``, a step, so the step's pull request ships its fix (ruling
    plan#1777); else what the refusal adds to rule 5's own sentence -- for a finding, the
    owner's branch that may record it beside its own claim, or why none may."""
    if card.kind != "finding" or claim is None:
        return ""
    if (number := owner(card)) is None:
        return f", and it names no one owner whose claim may (R-BAL395): {unowned(card)}"
    if number != claim.number:
        return (f", or its owner's, {_git.card_branch(number)} (a finding's marker is written "
                f"on its owner's branch by `quill finding-file plan#{card.number}`, R-BAL395)")
    if claim.kind != "step":
        return f", and its one owner, plan#{number}, is no step: {_OWNER_RULE}"
    return None


def unfixed(reader: CardReader, claim: BareCard, shipped: Collection[int],
            held: dict[int, BareCard]) -> list[str]:
    """The owed-finding arm (ruling plan#1777): one sentence for each finding ``claim``, whose
    card the change from its claim records, blocks that is still owed once the change and the
    other integration branch meet (:func:`owed`, ``shipped`` git's answer there): its marker
    is written in the commit that fixes it (:func:`cmd_finding_file`), or it is handed to the
    step that fixes it (:func:`cmd_hand`).  One blocked by ``claim`` and by another card
    names no one owner, so no pull request owes it: refused too (fail closed), with how to
    mend it (:func:`unowned`).  ``held``: the cards already read (:func:`owned`)."""
    reasons, head = [], _git.card_branch(claim.number)
    for card in owned(reader, claim.number, shipped, held):
        path, number = _git.marker(card.number), card.number
        if owner(card) == claim.number:
            reasons.append(
                f"{path}: {head} records card {claim.number}, but holds no marker for finding "
                f"plan#{number}, which it owns (R-BAL395) and which is neither shipped nor "
                f"dropped: write it in the commit that fixes it (quill finding-file "
                f"plan#{number}), or hand it to the step that does (quill hand plan#{number} "
                "--to plan#S)")
        else:
            reasons.append(
                f"{path}: {head} records card {claim.number}, but open finding plan#{number}, "
                "which it blocks, has no one owner, so no pull request owes it (R-BAL395, "
                f"plan#1784): {unowned(card)}")
    return reasons


def unhanded(reader: CardReader, claim: BareCard, cards: Mapping[int, BareCard],
             shipped: Collection[int], held: dict[int, BareCard]) -> list[str]:
    """The backstop for a hand edit on the web (ruling plan#1784): one sentence for each
    finding a step above ``claim`` (:func:`_state.above`, over ``cards``, which hold them)
    blocks that is still owed (:func:`owed`), each named with the hand-off down to
    ``claim``, whose card the change from its claim records.  A step above a claim is split
    into smaller steps, which no branch builds, so no quill door leaves it a finding; one a
    person links there is held here, at the pull request of every leaf below it, not lost.
    ``held``: the cards already read (:func:`owned`)."""
    return [f"{_git.marker(card.number)}: {_git.card_branch(claim.number)} records card "
            f"{claim.number}, but "
            f"plan#{step.number}, a step above it split into smaller steps, which no branch "
            f"builds, owns open finding plan#{card.number} (R-BAL395, plan#1784): hand it down "
            f"first (quill hand plan#{card.number} --to plan#{claim.number}), or to the leaf "
            "that fixes it"
            for step in above(claim, cards) for card in owned(reader, step.number, shipped, held)]
