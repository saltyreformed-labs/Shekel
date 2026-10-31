"""What a day's unanswered movements put between the books and the bank: the one rule.

Plan step **balance:X-bk-2** (plan#1192).  A day on the disagreement list is
apart by its residue, what the app's rows move less what the bank's lines move
(:func:`app.services.outstanding_difference.disagreement_list`), so the
movements no match answers carry it as their own rows' total less their own
lines' total.  Two readers ask it: the act sheet, before the acts, of every
movement it classes (:attr:`reconcile_inputs.ClassedDay.adds_up`), and the
rehearsal's done test, after them, of the movements no act answers -- the card
items and the act ruling plan#2433 holds back (:mod:`reconcile_rehearsal`).

**It is a module of its own because the two readers run different apps.**
The act sheet needs this checkout's ``app/`` and the rehearsal runs
production's, so neither can import the other's module; this imports no
``app`` at all, so both reach the one rule instead of each spelling it.
"""

from collections.abc import Iterable
from decimal import Decimal

ZERO = Decimal("0.00")


def residue_of(rows: "Iterable[Decimal]", lines: "Iterable[Decimal]") -> Decimal:
    """Return what movements put between the books and the bank on their day.

    Args:
        rows: The app's movements' amounts, signed as the app records them.
        lines: The bank's lines' amounts, signed as the bank posts them.

    Returns:
        Their rows' total less their lines' total: positive when the app has
        more money in.
    """
    return sum(rows, ZERO) - sum(lines, ZERO)
