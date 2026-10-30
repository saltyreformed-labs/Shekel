"""``quill arc`` and ``quill label`` (ruling plan#2221): a card's Arc and the labels a person
chooses for it, written through quill as every write of the tracker is (ruling
``balance:R-BAL170``), where before them only the web could write either once a card was
filed.

``arc`` sets a card's Arc, its arc's one home (rulings ``balance:R-BAL349``, ``R-BAL354``),
then puts its arc labels back to display it (:func:`_arcs.label_corrections`), as ``quill
sync``'s pass would at its next run (:mod:`_relabel`).  ``label`` adds or removes one of
:data:`setup_tracker.CHOSEN_LABELS`: never an arc label, which displays the Arc, nor quill's
filing mark.  Each writes only what differs, each write printed as it lands, so the same
command run again finishes one a failure cut short.  Each works on a ruling, whose title and
text alone are the developer's words (R-BAL220), and on a closed card; each refuses a task
and a card carrying the filing mark (:func:`_refusal`).

Split out of the command module, which they would take past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

from pathlib import Path

from tools.quill._arcs import label_corrections, other_labels
from tools.quill._command import _label, _one
from tools.quill._exits import Refused
from tools.quill._state import filing_unfinished
from tools.quill._tracker import Card, Tracker
from tools.quill.check import Draft, violations
from tools.quill.setup_tracker import FILING, TASK


def _refusal(card: Card) -> str | None:
    """Why neither command writes ``card``; None when they may.  A task holds no Arc and no
    label of its own: its arc is its card's (R-BAL386), and quill files it with none.  A card
    carrying the filing mark (R-BAL202) is found again by its ``quill file`` command, which
    refuses one whose Arc or labels are not its own (``_filing._refuse_relabel``), so a change
    here would stop that filing finishing."""
    if card.kind == TASK:
        return (f"{_label(card)} is a task, a piece of plan#{card.parent}'s work (R-BAL386): it "
                "holds no Arc and no label of its own, as its arc is its card's")
    if FILING in card.labels:
        return (f"{_label(card)} carries the {FILING!r} mark (R-BAL202): its `quill file` "
                "command, run again, refuses it once its Arc or labels are not its own "
                "(R-BAL186); "
                + (f"finish its filing first (`quill show plan#{card.number}` says how)"
                   if filing_unfinished(card) else
                   "closed while still marked, it keeps them so that command files nothing "
                   "over it"))
    return None


def cmd_arc(args, tracker: Tracker, _root: Path) -> int:
    """Set a card's Arc to ``--to`` (:meth:`_tracker.Tracker.set_arc`) and put its arc labels
    back to display it (:meth:`_tracker.Tracker.relabel`), after the check every written card
    passes on its type and Arc (:func:`check.violations`, nothing else graded).  Refused, with
    nothing written, for :func:`_refusal`'s cards and what the check refuses.  The Arc is
    written only when it differs, and the labels only when they do not display it."""
    card = _one(tracker, args.card)
    if why := _refusal(card):
        raise Refused(why)
    draft = Draft(card.kind, card.title, None, other_labels(card.labels), args.to)
    if problems := violations(draft, frozenset()):
        raise Refused("not moved:\n  " + "\n  ".join(problems))
    if card.arc == args.to:
        print(f"{_label(card)} holds the Arc {args.to!r} already: not written")
    else:
        tracker.set_arc(card.number, args.to)
        print(f"set {_label(card)}'s Arc to {args.to!r}")
    add, remove = label_corrections(card.labels, args.to)
    if add or remove:
        tracker.relabel(card.number, add, remove)
        print(f"  put back its arc labels to display it (R-BAL354): removing {list(remove)}, "
              f"adding {list(add)}")
    return 0


def cmd_label(args, tracker: Tracker, _root: Path) -> int:
    """Add ``--add`` to a card's labels, or take ``--remove`` from them, its other labels
    left as they are (:meth:`_tracker.Tracker.relabel`).  Refused, with nothing written, for
    :func:`_refusal`'s cards.  Nothing is written when the card already carries the label
    added, or does not carry the one removed."""
    card = _one(tracker, args.card)
    if why := _refusal(card):
        raise Refused(why)
    if args.add is not None and args.add in card.labels:
        print(f"{_label(card)} carries {args.add!r} already: not written")
    elif args.add is not None:
        tracker.relabel(card.number, (args.add,), ())
        print(f"labelled {_label(card)} {args.add!r}")
    elif args.remove not in card.labels:
        print(f"{_label(card)} does not carry {args.remove!r}: not written")
    else:
        tracker.relabel(card.number, (), (args.remove,))
        print(f"took {args.remove!r} off {_label(card)}")
    return 0
