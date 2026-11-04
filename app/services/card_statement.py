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
* **A version dated ON a close date governs the statement closing that day.**
  The close date is the closing date the issuer prints on the statement, and
  an issuer applies a change effective on a date to the billing cycle that
  contains it, the one closing on or after it: a "statements closing on or
  after Mar 5" change entered as Mar 5 makes Mar 5 the first close on the new
  day.  This cycle excludes its close date (closed-open, below), so every day
  of that statement falls before the version's date; the boundary follows
  the issuer's date anyway, because that is the date the owner enters.  Read
  from the version in force on the day BEFORE the close instead, the same
  change would first close on Apr 5.

So what a change of terms leaves standing is every statement that closes
BEFORE its date and every reward on a day before it.  The one exception is a
version dated before the card's earliest: the earliest held the days before
it flat (:func:`terms_in_effect`), and those days now take the new earliest's
terms, as the nearest terms anyone stated for them.

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

**A payment that repeats on each statement's due date** (plan step CC-6,
ruling plan#2671) walks :class:`DueDateSchedule`: the slice of the dated terms
the due dates read, held as plain values so the recurrence walk that takes it
can key a memo on it, and its :meth:`DueDateSchedule.on_or_after`, every due
date in date order.  Close order is not date order: a short transitional cycle
can fall due before the long one ahead of it.

All arithmetic is :class:`~decimal.Decimal`; :func:`~app.utils.money.round_money`
is applied at the one boundary where a fraction of a cent can arise (the
percentage of the balance), never in between (**R-CC8**'s convention).
"""

import heapq
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from app.utils.dates import (
    CALENDAR_DATE_MAX,
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

#: The furthest a statement's due date can fall after its close.  The due date
#: is the first occurrence of the due day strictly after the close
#: (:func:`due_date_for`): in the close's own month when that day is still
#: ahead, which is at most 30 days on; otherwise the next month's, which is at
#: most the close's month length on (a due day at or below the close's day
#: lands at or below that day next month) or, for a close on its month's last
#: day, at most the next month's length.  No month is longer than 31 days.
#: :meth:`DueDateSchedule.on_or_after` starts its walk this far before the
#: day it is asked from, so no statement falling due on or after that day is
#: missed.
_LONGEST_DUE_AFTER_CLOSE = timedelta(days=31)


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


@dataclass(frozen=True)
class DueDateTerms:
    """The slice of one terms version a statement's due date reads.

    The three columns :func:`due_date_for` and the close walks ask of a
    :class:`~app.models.credit_card_params.CreditCardParams` version, held as
    a plain value: the version is a mutable ORM row compared by identity, and
    a same-date save rewrites it in place (:func:`app.services.card_terms.set_terms`),
    so a memo keyed on the row would serve dates the card no longer has.

    Attributes:
        effective_date: The first day the version governs.
        statement_close_day: Its nominal close day, 1-31.
        payment_due_day: Its nominal due day, 1-31.
    """

    effective_date: date
    statement_close_day: int
    payment_due_day: int


@dataclass(frozen=True)
class DueDateSchedule:
    """A card's statement due dates, as the dated terms that decide them.

    What a repeat on each statement's due date walks (plan step CC-6, ruling
    plan#2671): the read pass builds one from the card's versions
    (:meth:`of`) and hands it to the recurrence walk, which never loads terms
    itself.  Hashable and compared by value, so two reads of unchanged terms
    are equal and a change of terms is a different value.

    Attributes:
        terms: The card's versions as :class:`DueDateTerms`, earliest
            effective date first; never empty (a dormant card has no
            statements, so it has no schedule).
    """

    terms: tuple[DueDateTerms, ...]

    def __post_init__(self) -> None:
        """Refuse an empty schedule: a dormant card has no statement to fall due.

        Raises:
            ValueError: When *terms* is empty.
        """
        if not self.terms:
            raise ValueError(
                "A due-date schedule needs at least one terms version: a card "
                "with none is dormant and has no statement to fall due. Gate "
                "on app.services.card_terms.load_card_term_versions first."
            )

    @classmethod
    def of(cls, versions: "Iterable[CreditCardParams]") -> "DueDateSchedule":
        """Return the schedule *versions* decide.

        Args:
            versions: The card's versions
                (:func:`app.services.card_terms.load_card_term_versions`), in
                any order; at least one.

        Returns:
            The :class:`DueDateSchedule`, its terms earliest first.

        Raises:
            ValueError: When *versions* is empty.
        """
        return cls(terms=tuple(sorted(
            (
                DueDateTerms(
                    effective_date=version.effective_date,
                    statement_close_day=version.statement_close_day,
                    payment_due_day=version.payment_due_day,
                )
                for version in versions
            ),
            key=lambda terms: terms.effective_date,
        )))

    def falls_due_on(self, day: date) -> bool:
        """Return whether a statement falls due on *day* under these terms.

        What a terms change asks of a payment's date: it moved only where the
        terms before the change answered yes and the terms after it answer no
        (``routes/card/_payments_follow``).

        Args:
            day: The day asked about.

        Returns:
            ``True`` when *day* is one of :meth:`on_or_after`'s dates.
        """
        return next(self.on_or_after(day), None) == day

    def on_or_after(self, day: date) -> Iterator[date]:
        """Yield every statement due date on or after *day*, ascending, each once.

        Each statement's due date is :func:`due_date_for`'s, under the version
        in force at its close, so a change of terms moves the due dates of the
        statements closing from its day and leaves the rest.

        **In date order, which is not close order.**  A short transitional
        cycle can fall due before the long one ahead of it: close day 20 and
        due day 19, then close day 5 and due day 6 from Mar 1, closes Feb 20
        (due Mar 19) and then Mar 5 (due Mar 6).  So the statements are walked
        in close order and their due dates held until no statement still to
        come can fall due before them: every later statement closes after the
        current close and falls due after its own close, so a held date on or
        before the current close is final.  **Two statements due on one day
        are one date**: the later statement's balance holds the earlier one's
        unpaid balance, so one payment answers both.

        Bounded at :data:`~app.utils.dates.CALENDAR_DATE_MAX`, as every
        recurrence walk is.

        Args:
            day: The first day to yield from.

        Yields:
            The due dates, ascending, none repeated, none after the calendar's
            last day.
        """
        held: list[date] = []
        last: date | None = None
        closes = _first_close_after(
            self.terms, day - _LONGEST_DUE_AFTER_CLOSE - _ONE_DAY,
        )
        while closes <= CALENDAR_DATE_MAX:
            heapq.heappush(held, due_date_for(self.terms, closes))
            while held and held[0] <= closes:
                due = heapq.heappop(held)
                if due >= day and due != last:
                    last = due
                    yield due
            closes = _first_close_after(self.terms, closes)
        while held and held[0] <= CALENDAR_DATE_MAX:
            due = heapq.heappop(held)
            if due >= day and due != last:
                last = due
                yield due


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
            ``[prior.closes, due_date_for(versions, prior.closes)]``, as a
            positive amount.

    Returns:
        ``True`` when nothing was owed, or the credits cover the balance.
    """
    if prior_balance <= ZERO:
        return True
    return credited_by_due >= prior_balance
