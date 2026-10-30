"""The Reconcile page's HERO: the four figures that answer *am I done*.

**The one part of the page that reads the account's
:class:`~app.services.bank_agreement.BankAgreement`** -- the books' balance
beside the bank's, day by day.  Everything else
:func:`~._reconcile.reconcile_page` renders is about the statement LINES:
which are waiting, which an act explains, which the owner skipped, and what
the last import recorded; it reads the agreement only to check that it names
the scope's account and to build this.  That is the seam this module was cut
on, out of :mod:`._reconcile` at its 1,000-line ceiling (plan#2365,
2026-10-30), before finding **BAL-651**'s fix adds the day the records run
through to the hero.  The page carries the hero as
:attr:`~._reconcile.ReconcilePage.hero`, which
:attr:`~._reconcile.ReconcilePage.is_done` reads.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import, no clock read, and no query: the
agreement arrives built, because only a route builds the
:class:`~app.services.balance_at.BalanceContext` it needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import date
    from decimal import Decimal

    from app.services.bank_agreement import BankAgreement


@dataclass(frozen=True)
class Hero:
    """The four figures that answer *am I done*.

    Ruling: the number is the hero (the design language's first principle).
    Done is ``off_by`` at zero and ``to_explain`` at zero, and the page says
    exactly that under them.

    Attributes:
        day: The day all three figures are about -- the latest day BOTH
            records state a balance for, the books' opening day included
            (:attr:`~app.services.bank_agreement.BankAgreement.headline`,
            ruling **R-BAL286**) -- or ``None`` exactly when no drawn day
            carries both, e.g. no anchored import, or a bank balance placed
            only before the books open, after NOW or past the two-year draw.
        bank: What the bank's own record says the account held that day, or
            ``None`` with *day*.
        books: What the app's records say it held that day, or ``None`` with
            *day*.
        off_by: The books LESS the bank, or ``None`` with *day*.  **Signed in
            that direction**, which is
            :attr:`~app.services.bank_agreement.AgreementDay.gap`'s own
            convention: positive means the books stand higher.
        to_explain: How many lines the inbox holds.
        unpriced_after: How many drawn days after :attr:`day` the books state
            a balance for and the bank's own record cannot price -- a later
            import placing no balance, or the gap before one.  **Zero is what
            makes the hero current**, and the other four do not imply it, so
            :attr:`~._reconcile.ReconcilePage.is_done` refuses done over it
            and the page says *priced through <day>*.  Drawn days end at the
            last statement line, so a record AFTER every statement is not
            counted: statements stopping three weeks before the records read
            zero and can be done (finding **BAL-651**).  Named by adversarial
            review 2026-08-29.

    **The three money figures are all-or-nothing**, and that is the point of
    carrying the day beside them: a hero that printed two of three would be
    inviting a subtraction the app could not perform.
    """

    day: "date | None"
    bank: "Decimal | None"
    books: "Decimal | None"
    off_by: "Decimal | None"
    to_explain: int
    unpriced_after: int


def hero(agreement: "BankAgreement | None", to_explain: int) -> Hero:
    """Return the four figures that answer *am I done*.

    Args:
        agreement: The account's :class:`~app.services.bank_agreement
            .BankAgreement`, or ``None`` for an account holding no recorded
            line.
        to_explain: How many lines the inbox holds.

    Returns:
        The :class:`Hero`.  Its three money figures are all present or all
        absent, because they are one comparison on one day: an account with no
        anchored import has a bank side nothing places, and printing two of
        three would invite a subtraction the app cannot perform.
    """
    day = None if agreement is None else agreement.headline
    if day is None:
        return Hero(
            day=None, bank=None, books=None, off_by=None,
            to_explain=to_explain, unpriced_after=0,
        )
    return Hero(
        day=day.day,
        bank=day.bank_balance,
        books=day.app_balance,
        off_by=day.gap,
        to_explain=to_explain,
        # Each later day has a books balance (the hero's is on or after the
        # opening) and no bank one (the hero is the LATEST carrying both).
        unpriced_after=sum(1 for other in agreement.days if other.day > day.day),
    )
