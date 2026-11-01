"""``quill arc`` and ``quill label`` (ruling plan#2221), and ``quill priority`` and ``quill
effort`` (plan#2491): a card's Arc, the labels a person chooses for it, and its Priority and
Effort, written through quill as every write of the tracker is (ruling ``balance:R-BAL170``),
where before them only the web could write any of them once a card was filed.

``arc`` sets a card's Arc, its arc's one home (rulings ``balance:R-BAL349``, ``R-BAL354``),
then puts its arc labels back to display it (:func:`_arcs.label_corrections`), as ``quill
sync``'s pass would at its next run (:mod:`_relabel`).  ``label`` adds or removes one of
:data:`setup_tracker.CHOSEN_LABELS`: never an arc label, which displays the Arc, nor quill's
filing mark.  ``priority`` and ``effort`` set a card's Priority or Effort to one of its
options (``setup_tracker.ISSUE_FIELDS``), or clear it, one function writing either
(:func:`cmd_field`).  Each writes only what differs, each write printed as it lands, so the
same command run again finishes one a failure cut short.  Each works on a ruling, whose title
and text alone are the developer's words (R-BAL220), and on a closed card, and refuses a card
still being filed (:func:`_filing_refusal`); ``arc`` and ``label`` refuse a task
(:func:`_refusal`), and ``priority`` and ``effort`` a value on a kind the check refuses it on
(``check.HOLDERS``).

Split out of the command module, which they would take past the 1,000 lines
``too-many-lines`` allows; their arguments are here too (:func:`add_parsers`).
"""
from __future__ import annotations

from pathlib import Path

from tools.ci.arcs import ARCS
from tools.quill._arcs import label_corrections, other_labels
from tools.quill._command import _label, _one, card_number
from tools.quill._exits import Refused
from tools.quill._state import filing_unfinished
from tools.quill._tracker import Card, Tracker
from tools.quill.check import HOLDERS, Draft, violations
from tools.quill.setup_tracker import CHOSEN_LABELS, ISSUE_FIELDS, TASK


def _filing_refusal(card: Card, cost: str) -> str | None:
    """Why no command here writes ``card`` while its filing has not finished
    (:func:`_state.filing_unfinished`, R-BAL202), ``cost`` saying what such a write would cost
    that filing; None when it has.  Its ``quill file`` command, run again, finishes it.  One
    closed while still marked is no longer being filed, and is written."""
    if not filing_unfinished(card):
        return None
    return (f"{_label(card)}'s filing has not finished (R-BAL202): its `quill file` command, run "
            f"again, finishes it{cost}, so finish it first (`quill show plan#{card.number}` says "
            "how)")


def _refusal(card: Card) -> str | None:
    """Why neither ``arc`` nor ``label`` writes ``card``; None when they may (ruling
    plan#2221).  A task's arc is its card's (R-BAL386), and its card carries what marks the
    work.  A card still being filed (:func:`_filing_refusal`): its ``quill file`` command
    refuses a card whose Arc or labels are not its own (``_filing._refuse_relabel``), so a
    change here would stop that filing finishing."""
    if card.kind == TASK:
        return (f"{_label(card)} is a task (R-BAL386): its arc is its card's, which carries "
                "the Arc and the labels of its work: write them there")
    return _filing_refusal(card, ", and refuses a card whose Arc or labels are not its own "
                                 "(R-BAL186)")


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


def cmd_field(args, tracker: Tracker, _root: Path) -> int:
    """``quill priority`` and ``quill effort``: set a card's Priority or Effort, the command's
    issue field (``check.HOLDERS``, keyed by the command's name), to ``--to``, or with
    ``--clear`` clear it (:meth:`_tracker.Tracker.set_field`), after the check every written
    card passes on its type and Arc and on the kind that may hold the field
    (:func:`check.violations`, grading that part alone).  Refused, with nothing written, for
    a card still being filed (:func:`_filing_refusal`) and what the check refuses.  Written
    only when it differs from what the card holds."""
    part = args.command
    field = HOLDERS[part][0]
    card = _one(tracker, args.card)
    if why := _filing_refusal(card, " (R-BAL186)"):
        raise Refused(why)
    value = None if args.clear else args.to
    draft = Draft(card.kind, card.title, None, other_labels(card.labels), card.arc,
                  **{part: value})
    if problems := violations(draft, frozenset({part})):
        raise Refused("not written:\n  " + "\n  ".join(problems))
    held = getattr(card, part)
    if held == value:
        holds = f"no {field}" if value is None else f"the {field} {value!r}"
        print(f"{_label(card)} holds {holds} already: not written")
        return 0
    tracker.set_field(card.number, field, value)
    if value is None:
        print(f"cleared {_label(card)}'s {field}, which was {held!r}")
    else:
        print(f"set {_label(card)}'s {field} to {value!r}"
              + ("" if held is None else f", which was {held!r}"))
    return 0


def add_parsers(commands) -> None:
    """``arc``, ``label``, ``priority`` and ``effort`` and their arguments: the arcs
    (:data:`tools.ci.arcs.ARCS`), the labels a person chooses
    (:data:`setup_tracker.CHOSEN_LABELS`), and each field's options
    (``setup_tracker.ISSUE_FIELDS``), each the command line's one refusal of any other."""
    arc = commands.add_parser("arc", help="set a card's Arc, its arc label following it")
    arc.add_argument("card", type=card_number)
    arc.add_argument("--to", required=True, choices=ARCS, help="the arc it moves to")
    label = commands.add_parser("label", help="add or remove a label a person chooses")
    label.add_argument("card", type=card_number)
    which = label.add_mutually_exclusive_group(required=True)
    which.add_argument("--add", choices=CHOSEN_LABELS)
    which.add_argument("--remove", choices=CHOSEN_LABELS)
    for part, (field, _, ruling) in HOLDERS.items():
        written = commands.add_parser(part, help=f"set or clear a card's {field} (ruling "
                                                 f"{ruling})")
        written.add_argument("card", type=card_number)
        value = written.add_mutually_exclusive_group(required=True)
        value.add_argument("--to", choices=ISSUE_FIELDS[field].options,
                           help=f"the {field} it holds")
        value.add_argument("--clear", action="store_true", help=f"hold no {field}")
