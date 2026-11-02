"""
Shekel Budget App -- The Card Statement (pure)

A credit card's STATEMENT, derived from its terms and the balance seam's
figure -- never stored (ruling **R-CC2**; plan step **credit_card:CC-3**,
design ``docs/design/credit_card_from_scratch.md`` 3.4).  No Flask, no ``db``:
the caller loads the card's terms
(:func:`app.services.card_terms.load_card_term_versions`) and reads the fold
through the seam, and every function here takes plain values and returns
plain data -- the :mod:`app.services.rate_period_engine` discipline.

**The terms are a DATED SERIES** (plan#2331, developer ruling **plan#2333**):
each :class:`~app.models.credit_card_params.CreditCardParams` version is in
force from its ``effective_date`` until the next one's, and before the
earliest the earliest (:func:`terms_in_effect`, the ONE walk every reader of
the terms takes -- here, because this pure module is the one every reader can
reach).  So every function below takes the card's SERIES, never a bare day or
rule, and picks the version itself:

* **A day is a CLOSE DATE when it is the close day, clamped, of the version in
  force on that day.**  A change of the close day therefore cuts one
  TRANSITIONAL cycle, from the last close under the old day to the first under
  the new one -- short or long by the date the owner enters, as Regulation Z
  allows (official interpretation comment 2(a)(4)-3 exempts it from the
  equal-cycle rule).  plan#2333's example: close 20 through February, close 5
  from Mar 1, so the Feb 20 statement is ``[Jan 20, Feb 20)`` and the next
  ``[Feb 20, Mar 5)``.  A month that holds a change can hold two closes, which
  is why nothing here is keyed by month.
* **A statement's due date and minimum read the version in force at its
  CLOSE** -- the statement the bank printed then -- never today's, and never
  one the caller picked: :func:`due_date_for` and :func:`minimum_payment` take
  the series and the close.

**The cycle is CLOSED-OPEN.**  A statement that closes on the 20th holds every
movement dated from the previous 20th up to but NOT including this one; a row
dated AT the close belongs to the next cycle.  So "the fold at the close
instant" is the balance at the END of the day before the close -- the seam's
:func:`~app.services.balance_at.cash_balance_at` samples the end of a day
(:class:`~app.services.balance_at.CashDayFacts`) -- and that is the figure
:attr:`CycleWindow.valuation_date` names, the same instant ruling **R-CC22**
prices a payday row at ("the card's balance at the end of the day before its
day").  The finance charge (design 3.6) will be a row dated at the close and
so prices the cycle before it, by this same rule.

**Every day-of-month term is NOMINAL and clamps through the ONE clamp**
(:func:`app.utils.dates.clamped_day`, ruling `pay_calendar:R-PC79` /
`recurrence:R-R3`): a close day of 31 is the 31st in January and the 28th in
February, and never decays to the 30th for good.  Which close or due date a
day falls before or after is the monthly-grid set beside that clamp
(:func:`app.utils.dates.grid_month_after` and its siblings), which the loan
calendar, the pay grid and the recurrence walk read too -- this module spelled
the comparisons itself until plan step ``recurrence:R25`` (ruling
``recurrence:R-R122``, finding ``recurrence:REC-547``).

**Every statement figure is an OWED amount** (developer ruling **R-CC29**,
2026-09-18): positive when the owner owes, negative when the card holds a
credit.  The seam reports a card's cash balance NEGATIVE when money is owed
(the card is a plain liability riding the cash fold, **R-CC14**), and the ONE
flip from that held balance to an owed figure is
:func:`app.services.liability_sign.owed` -- it lived here as ``owed`` until plan
step credit_card:CC-5-5a moved it into the balance seam (ruling **R-CC47**),
and plan step CC-5-5b moved it out again beside the balance doors that read
it, so the net-worth surfaces, the doors and the statement read ONE flip.  The statement
balance is ``owed(cash_balance_at(account, ctx, window.valuation_date))`` for
the producer that will state it (no ``app/`` module calls this one yet); a
payday row's base under **R-CC22** is the same flip of the fold at the end of
the day before its day.  A consumer that flipped the sign itself would be a
second home for it.

**The due date** (developer ruling **R-CC26**, 2026-09-18) is the first
occurrence of the due day strictly AFTER the close date -- the same month when
its clamped date falls after the close (closes Sep 5, due Sep 28), the next
month otherwise (closes Sep 20, due Oct 15).  Issuers state both days and this
reads them as stated.

All arithmetic is :class:`~decimal.Decimal`; :func:`~app.utils.money.round_money`
is applied at the one boundary where a fraction of a cent can arise (the
percentage of the balance), never in between (**R-CC8**'s convention).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from app.utils.dates import (
    clamped_day,
    grid_month_after,
    grid_month_on_or_before,
)
from app.utils.effective_dated import (
    in_effect_or_earliest,
    span_in_effect_or_earliest,
)
from app.utils.money import ZERO, round_money

if TYPE_CHECKING:
    from app.models.credit_card_params import CreditCardParams

# The close instant is the end of the day BEFORE the close date, and the
# close walks step from a span's end to the day before it by one day.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class CycleWindow:
    """One statement cycle: the closed-open span ``[opens, closes)``.

    Attributes:
        opens: The first day IN the cycle -- the previous statement's close
            date.
        closes: This statement's close date -- the first day NOT in the
            cycle.  A movement dated here belongs to the NEXT cycle.
    """

    opens: date
    closes: date

    def contains(self, day: date) -> bool:
        """Return True iff *day* falls inside this cycle (closed-open).

        Args:
            day: Any date.

        Returns:
            ``opens <= day < closes``.
        """
        return self.opens <= day < self.closes

    @property
    def valuation_date(self) -> date:
        """The day whose END-of-day balance is the statement balance.

        The day before :attr:`closes`: the seam values an account at the end
        of a day, and the cycle excludes its close date, so the balance at the
        close instant is the fold at ``closes - 1 day``.
        """
        return self.closes - _ONE_DAY


def terms_in_effect(
    versions: "list[CreditCardParams]", day: date,
) -> "CreditCardParams | None":
    """Return the version of the card's terms that governs *day*.

    The ONE walk (:func:`app.utils.effective_dated.in_effect_or_earliest`):
    the version with the greatest ``effective_date <= day``, and the earliest
    version for a day before every version -- the series holds flat
    backwards, ruling **R-I**'s shape for a stated figure, which a recurring
    definition's price series already takes
    (:func:`app.services.template_amount_service.amount_as_of`).  So a card
    with one version derives exactly as the undated row did, and the date the
    owner first saves terms can never make a statement before it vanish.  The
    APR series answers ``None`` before its first row instead
    (:func:`app.services.card_apr.apr_in_effect`): that is one rate a finance
    charge multiplies, and ``None`` refuses to price interest at a rate
    nobody stated for those days.

    Every reader of the terms takes this walk: the statement's close, due date
    and minimum below, the cash detail page's terms in force, and a reward at
    its purchase's day.  It lives in this module, not beside the loader in
    :mod:`app.services.card_terms`, because this module is pure and so is
    the one every reader can import.

    Args:
        versions: The card's versions
            (:func:`app.services.card_terms.load_card_term_versions`), in any
            order.
        day: The day to resolve.

    Returns:
        The governing version; ``None`` only for an empty series (a dormant
        card).
    """
    return in_effect_or_earliest(versions, day)


def _in_force(
    versions: "list[CreditCardParams]", day: date,
) -> "tuple[CreditCardParams, date | None, date | None]":
    """Return the version in force on *day* and the span ``[start, end)`` it governs.

    :func:`terms_in_effect`'s version and the days that walk gives it, both
    from the leaf (:func:`app.utils.effective_dated.span_in_effect_or_earliest`).
    The close walks below jump from span to span, because within one span the
    close day is one version's and the monthly-grid set answers its next or
    last grid day directly.

    A dormant card has no terms and so no statement; every card feature gates
    on a version existing (design 3.4), and a caller that reaches here
    without one is told so rather than meeting an ``AttributeError`` or a
    ``TypeError``.

    Args:
        versions: The card's versions, in any order.
        day: The day whose governing version to resolve.

    Returns:
        ``(terms, start, end)``: ``start`` is ``None`` for the earliest
        version and ``end`` is ``None`` for the latest.

    Raises:
        ValueError: When *versions* is empty.
    """
    span = span_in_effect_or_earliest(versions, day)
    if span is None:
        raise ValueError(
            f"The card has no terms version, so no statement exists on {day}: "
            f"gate on app.services.card_terms.load_card_term_versions first."
        )
    return span


def _governing(versions: "list[CreditCardParams]", day: date) -> "CreditCardParams":
    """Return :func:`terms_in_effect`'s version for *day*, refusing an empty series.

    :func:`_in_force`'s version, so the refusal of a dormant card has one home.

    Raises:
        ValueError: When *versions* is empty.
    """
    terms, _start, _end = _in_force(versions, day)
    return terms


def _first_close_after(versions: "list[CreditCardParams]", day: date) -> date:
    """Return the card's first close date strictly after *day*.

    The version in force on the next day governs every day up to its span's
    end, so its first grid day after *day* is the close when it falls before
    that end; when it does not, no day of the span is a close, and the walk
    resumes from the span's end under the next version.  Each step moves to a
    later span and the latest span is unbounded, so the walk ends.

    Args:
        versions: The card's versions (non-empty).
        day: The date to search after.

    Returns:
        The first date after *day* that is the clamped close day of the
        version in force on it.
    """
    while True:
        terms, _start, end = _in_force(versions, day + _ONE_DAY)
        close_day = terms.statement_close_day
        close = clamped_day(grid_month_after(day, close_day), close_day)
        if end is None or close < end:
            return close
        day = end - _ONE_DAY


def _last_close_on_or_before(versions: "list[CreditCardParams]", day: date) -> date:
    """Return the card's latest close date on or before *day*.

    :func:`_first_close_after`'s mirror: the version in force on *day*
    governs back to its span's start, so its last grid day on or before
    *day* is the close when it falls inside the span; when it does not, the
    walk resumes from the day before the span under the version before.  The
    earliest span is unbounded backwards, so the walk ends.

    Args:
        versions: The card's versions (non-empty).
        day: The date to search on or before.

    Returns:
        The latest date on or before *day* that is the clamped close day of
        the version in force on it.
    """
    while True:
        terms, start, _end = _in_force(versions, day)
        close_day = terms.statement_close_day
        close = clamped_day(grid_month_on_or_before(day, close_day), close_day)
        if start is None or close >= start:
            return close
        day = start - _ONE_DAY


def cycle_containing(versions: "list[CreditCardParams]", day: date) -> CycleWindow:
    """Return the cycle *day* belongs to.

    From the card's latest close on or before *day* up to its first close
    strictly after it (closed-open): a day before a close is in the cycle
    that close ends, and the close date itself opens the next.  Each close
    is the close day of the version in force on that date (the module
    docstring), so the cycle open when the close day changes is the
    transitional one.  The placement the payment's "fold at the LAST close"
    (design 3.5, CC-6) and the finance charge's "the cycle before the close
    it posts at" (design 3.6, CC-3i) both start from.

    Args:
        versions: The card's terms versions (non-empty), in any order.
        day: The date to place.

    Returns:
        The :class:`CycleWindow` with ``window.contains(day)``.

    Raises:
        ValueError: When *versions* is empty (a dormant card).
    """
    return CycleWindow(
        opens=_last_close_on_or_before(versions, day),
        closes=_first_close_after(versions, day),
    )


def statement_sequence(
    versions: "list[CreditCardParams]", first: date, last: date,
) -> list[CycleWindow]:
    """Return every cycle whose CLOSE date falls within ``[first, last]``.

    Ascending, each opening at the close before it; empty when no close date
    lies in the inclusive span (a span shorter than a month can hold zero or
    one, and a month holding a change of the close day can hold two).

    Args:
        versions: The card's terms versions (non-empty), in any order.
        first: The earliest close date to include.
        last: The latest close date to include.

    Returns:
        The :class:`CycleWindow` list, oldest first.

    Raises:
        ValueError: When *versions* is empty (a dormant card).
    """
    windows: list[CycleWindow] = []
    closes = _first_close_after(versions, first - _ONE_DAY)
    while closes <= last:
        opens = (
            windows[-1].closes if windows
            else _last_close_on_or_before(versions, closes - _ONE_DAY)
        )
        windows.append(CycleWindow(opens=opens, closes=closes))
        closes = _first_close_after(versions, closes)
    return windows


def due_date_for(versions: "list[CreditCardParams]", closes: date) -> date:
    """Return the payment due date of the statement that closed on *closes*.

    The first occurrence of the due day strictly after *closes*
    (**R-CC26**): this month's when that date is still ahead of the close,
    next month's otherwise, so a due day equal to the close day is next
    month's.  The due day is the version in force AT THE CLOSE's (the module
    docstring): a change of the due day that takes effect after a close does
    not move the payment that statement printed.

    The clamp is the ONE clamp's and the "which month's occurrence" comparison
    over it is the monthly-grid set's
    (:func:`app.utils.dates.grid_month_after`), which the loan calendar's
    on-or-after and on-or-before questions read too since plan step
    ``recurrence:R25`` closed ledger row ``recurrence:REC-547``.

    Args:
        versions: The card's terms versions (non-empty), in any order.
        closes: The statement's close date (:attr:`CycleWindow.closes`).

    Returns:
        The due date, clamped to its month's length.

    Raises:
        ValueError: When *versions* is empty (a dormant card).
    """
    due_day = _governing(versions, closes).payment_due_day
    return clamped_day(grid_month_after(closes, due_day), due_day)


def minimum_payment(
    versions: "list[CreditCardParams]", closes: date, balance: Decimal,
) -> Decimal:
    """Return the issuer's minimum payment on the statement closing on *closes*.

    ``max(floor, round_money(percent x balance))``, clamped to the balance
    and never below zero (design 3.4; **R-CC29**): a balance smaller than
    the floor is due whole, and a zero or credit balance has nothing due.
    The percent and the floor are the version in force AT THE CLOSE's
    (:attr:`~app.models.credit_card_params.CreditCardParams.min_payment_percent`,
    ``Decimal("0.0250")`` for 2.5%, and
    :attr:`~app.models.credit_card_params.CreditCardParams.min_payment_floor`).

    Args:
        versions: The card's terms versions (non-empty), in any order.
        closes: The statement's close date (:attr:`CycleWindow.closes`).
        balance: The statement balance as an OWED figure
            (:func:`app.services.liability_sign.owed`).

    Returns:
        The minimum, cent-quantized on every arm: ``0.00`` for a zero or
        credit balance, else ``min(max(floor, round_money(percent x
        balance)), balance)``.

    Raises:
        ValueError: When *versions* is empty (a dormant card).
    """
    terms = _governing(versions, closes)
    if balance <= ZERO:
        return round_money(ZERO)
    return min(
        max(terms.min_payment_floor,
            round_money(terms.min_payment_percent * balance)),
        balance,
    )


def grace_kept(prior_balance: Decimal, credited_by_due: Decimal) -> bool:
    """Return True iff the PRIOR statement was paid in full by its due date.

    Grace (ruling **R-CC2**): no interest accrues on a cycle when the previous
    statement's balance was paid in full by its due date.  The caller sums
    the credits into the card -- payments, refunds, redemptions -- dated from
    the prior close date through its due date inclusive; a credit dated AT the
    close is outside that statement's balance (closed-open) and so counts
    toward paying it.

    Args:
        prior_balance: The prior statement's balance as an OWED figure.
        credited_by_due: The credits into the card dated in
            ``[prior.closes, due_date_for(prior.closes, due_day)]``, as a
            positive amount.

    Returns:
        ``True`` when nothing was owed, or the credits cover the balance.
    """
    if prior_balance <= ZERO:
        return True
    return credited_by_due >= prior_balance
