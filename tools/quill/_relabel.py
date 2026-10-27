"""``quill sync``'s arc-label pass (ruling ``balance:R-BAL354``, views_spec s.3): each card's arc
labels put back to display its Arc, the arc's one home (``R-BAL349``), by the one producer of
what does it (:func:`_arcs.label_corrections`), over one read of every card
(:meth:`_tracker.Tracker.card_arcs`).  GitHub offers no label only a tool can write, so a
person can change one; the pass is the fence R-BAL354 rules, putting it back and saying so.

A correction is no REPORT: R-BAL184 makes ``sync`` exit 1 only for what a person must fix.
What no label can fix is one (:func:`arc_report`), and the card is left as it is: a card but a
task holding no Arc, whose labels then say what its Arc should be and are kept for whoever sets
it, and a task holding one.  Every card is read, the ones still carrying the filing mark too: a
card is born holding its Arc and its arc label in the one create that files it
(``_tracker.Tracker.create``), and a filing run again compares its Arc and its other labels,
never its arc labels (``_filing._refuse_relabel``), so no correction here changes what it
decides.

Split out of the command module, which it would take past the 1,000 lines ``too-many-lines``
allows.
"""
from __future__ import annotations

from tools.quill._arcs import ArcCard, label_corrections
from tools.quill._command import _label
from tools.quill._tracker import Tracker
from tools.quill.setup_tracker import TASK


def arc_report(card: ArcCard) -> str | None:
    """What a person must fix in ``card``'s Arc, which no label can; None when nothing: a card
    but a task holding none, from which no label can be derived (views_spec s.3), and a task
    holding one, whose arc is its card's (ruling ``balance:R-BAL386``), so a value of its own
    would be a second home of it (rule 14)."""
    if card.kind == TASK:
        if card.arc is None:
            return None
        return (f"{_label(card)} is a task, whose arc is its card's (R-BAL386), and holds the "
                f"Arc {card.arc!r}: clear it on the web")
    if card.arc is not None:
        return None
    return (f"{_label(card)} holds no Arc, its arc's one home (R-BAL349), so no arc label can "
            f"display it: set its Arc on the web (its labels: {list(card.labels)})")


def put_back(tracker: Tracker, dry_run: bool) -> int:
    """The pass, over every card in number order: each card a person must fix
    (:func:`arc_report`) printed as a REPORT and left as it is, and each other card's arc
    labels put back to display its Arc (:func:`_arcs.label_corrections`, a task's to none),
    each correction printed with what it removes and adds as it lands, or under ``dry_run``
    printed as what would be written and not made.  Run again after a failure, it finds the
    corrections still to make.  The number of REPORT lines, each of which makes ``sync`` exit 1."""
    reports = 0
    for card in sorted(tracker.card_arcs(), key=lambda card: card.number):
        if why := arc_report(card):
            print(f"REPORT: {why}")
            reports += 1
            continue
        add, remove = label_corrections(card.labels, card.arc)
        if not (add or remove):
            continue
        if not dry_run:
            tracker.relabel(card.number, add, remove)
        print(f"{'would put' if dry_run else 'put'} back {_label(card)}'s arc labels to display "
              f"its Arc (R-BAL354): removing {list(remove)}, adding {list(add)}")
    return reports
