"""``quill sync``'s arc-label pass (ruling ``balance:R-BAL354``, views_spec s.3): each card's arc
labels put back to display its Arc, the arc's one home (``R-BAL349``), by the one rule of what
a card's arc labels and Arc call for (:func:`_arcs.arc_verdict`), over one read of every card
(:meth:`_tracker.Tracker.card_arcs`), the rule and the read :mod:`setup_tracker`'s check
makes.  GitHub offers no label only a tool can write, so a person can change one; the pass is
the fence R-BAL354 rules, putting it back and saying so.

A correction is no REPORT: R-BAL184 makes ``sync`` exit 1 only for what a person must fix.
What no label can fix is one, and the card is left as it is: a card but a task holding no Arc,
whose labels then say what its Arc should be and are kept for whoever sets it, one holding an
Arc that is none of quill's arcs, and a task holding one.  Every card is read, the ones still
carrying the filing mark too: a card is born holding its Arc and its arc label in the one
create that files it (``_tracker.Tracker.create``), and a filing run again compares its Arc and
its other labels, never its arc labels (``_filing._refuse_relabel``), so no correction here
changes what it decides.

Split out of the command module, which it would take past the 1,000 lines ``too-many-lines``
allows.
"""
from __future__ import annotations

from tools.quill._arcs import arc_verdict
from tools.quill._command import _label
from tools.quill._tracker import Tracker
from tools.quill.setup_tracker import TASK


def put_back(tracker: Tracker, dry_run: bool) -> int:
    """The pass, over every card in number order, by :func:`_arcs.arc_verdict`: each card a
    person must fix printed as a REPORT and left as it is, and each other card's arc labels
    put back to display its Arc (a task's to none), each correction printed with what it
    removes and adds as it lands, or under ``dry_run`` printed as what would be written and
    not made.  Run again after a failure, it finds the corrections still to make.  The number
    of REPORT lines, each of which makes ``sync`` exit 1."""
    reports = 0
    for card in sorted(tracker.card_arcs(), key=lambda card: card.number):
        fault, add, remove = arc_verdict(card, card.kind == TASK)
        if fault:
            print(f"REPORT: {_label(card)} {fault}")
            reports += 1
            continue
        if not (add or remove):
            continue
        if not dry_run:
            tracker.relabel(card.number, add, remove)
        print(f"{'would put' if dry_run else 'put'} back {_label(card)}'s arc labels to display "
              f"its Arc (R-BAL354): removing {list(remove)}, adding {list(add)}")
    return reports
