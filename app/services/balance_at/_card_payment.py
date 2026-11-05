"""Balance-at-T seam -- a card's worked-out payment, priced inside its balance.

Plan step ``credit_card:CC-6``, rulings **R-CC22** (the rule) and plan#2666 (the
three amounts).  A worked-out payment stores no figure (plan#2667); this module
works it out from the card's running total, so it is a balance producer and lives
in a private module of the seam (W9910).  Every reader reaches this one answer:
the amount model asks through :attr:`~app.services.cash_ledger.AmountBasis.card_payments`,
so checking's leg, the card's leg, the grid cell and the popover read one figure.

**The rule, stated once for all three amounts**:
``price = max(0, min(A - X, B - Y))``.

* **A** is what the card owes on the rule's day: at the end of the day before
  the row's day (:attr:`~app.enums.CardPaymentModeEnum.CARD_BALANCE`), or at the
  close of the statement the row pays, whose figure is the end of the day before
  that close (:attr:`~app.enums.CardPaymentModeEnum.STATEMENT_BALANCE`), or that
  statement's minimum (:attr:`~app.enums.CardPaymentModeEnum.MINIMUM_PAYMENT`).
  Money dated by the rule's day and not yet held is on that statement all the
  same (the date decides the statement, ruling plan#2910), so the minimum is
  worked out as if it were held.
* **X** is every other transfer or credit into the card that the row counts,
  dated after the rule's day and not yet inside A: placed and unpaid, or paid
  later.
* **B - Y is the ceiling, what is left now of what this row pays** (ruling
  plan#2887: "The scheduled payment always covers what the balance is now").
  **B** is what the card owes on the rule's day, or at the last true-up before
  the row lands if that is later, because a true-up restates the balance.
  **Y** is the money the row counts, or dated by the day before it lands, that is
  not yet inside B.

**A charge made after B's day is no part of the ceiling**: it is a later row's to
pay, because each purchase has its own due date (ruling plan#2888).  So a later
charge cannot lift a row back up to its statement.

**An unmarked row dated by that true-up is not in Y**, because a balance read off
the website already holds money sent and not yet marked.  A row this rule has
priced is the exception: its figure was worked out from that true-up, and the
money is still to leave, so the next row reads it.  This reaches the ceiling only:
the fold still lands such a row after the true-up (ruling R-G), so a rule's day
after it reads that money as arrived.

**Which money a row counts follows its repeat, never its amount.**  An each-payday
row counts money dated anywhere in its own paycheck, because its date is only the
paycheck's: a one-off in that paycheck shrinks the row wherever in it the one-off
falls, and the paycheck's total stays what the card owes.  Every other row, a
due-date row above all, counts money dated on or before its own day, because that
day is a deadline: Dec 18's $100 does not count against a Dec 15 row (R-CC22).  So
a payment on the close day counts and one after the due date does not; an earlier
row paid late is not paid twice; and a purchase on the row's own day rolls to the
next row.  In the everything-on-the-card amount, that makes the next payday after
a purchase the one that pays it off (ruling plan#2910), whichever paycheck the
purchase's row sits in.

**Money dated by the day before an overdue row lands counts wherever its window
ends.**  Only an overdue row has such money, because only its landing day is later
than its date.  So with no true-up after its date, an unmarked one-off and a marked
one read one figure.  The cost: a row the bank really did take reads low once money
or a lower true-up follows it, $0.00 if that is all of it, and checking reads high
by the difference until the row is marked with the real figure.

**Which statement a row pays follows its repeat too.**  A row repeating on paydays
pays the last to close strictly before its day, its date being only the paycheck's.
Every other row is dated by a deadline, so it pays the statement falling due next on
that day, or one that closed later and has already fallen due, whose balance holds it
(:func:`app.services.card_statement.close_of_statement_paid_on`).  The two part only
where a change of terms moves a due date or cuts a cycle: close 5 due 27, then close
20 due 15 from Jan 10, and Jan 27's row pays the Jan 5 statement though Jan 20 has
closed since.  A due day equal to the close day makes the row's day itself a close,
and the row pays the statement before it.

**A row is priced in date order off the running total WITHOUT worked-out rows**,
and each priced row then joins what the next one reads.  That order is sound
because landing keeps it: a row dated earlier never lands later, so every
worked-out row landing by a row's valuation day or before its landing day is
dated earlier, and is already priced.  Two rows on one day, or landing on one day,
are ordered by date and then id, and the later sees the earlier as a credit.

**A credit's date is the earlier of its row's date and the day its money moved.**
A row paid late still counts from its own date, so it is not paid twice, and a
row paid early counts from the day the money arrived, for the same reason.  A
refund under an expense row is dated the day it was made, in flight or posted: the
row it came back to was budgeted for something else, so that row's date says nothing
about it.  Posting it sets only the day it joins the balance, so no row reads
differently unless a true-up was stated on or after the day it was made: a refund
posted by then is inside that balance, and the fold lands one in flight after it
(ruling R-G).

**A row outside the plan answers too**, because a budget figure is asked of every
status (:func:`~app.services.cash_ledger.amounts_by_key`): by the same rule, its
own money taken out of A, B, X and Y, so a settled row's plan is what the card
asked of it and not what is left after paying it.  It never lands, so its own
date stands in for the day it would land.

Nothing here reads or writes the stored ledger (plan#2516): the figures are the
fold's own.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property

from app import ref_cache
from app.enums import CardPaymentModeEnum, RecurrenceUnitEnum
from app.exceptions import AmountUnresolvable
from app.models.credit_card_params import CreditCardParams
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.card_statement import (
    close_of_statement_paid_on,
    cycle_containing,
    minimum_payment,
)
from app.services.card_terms import load_card_term_versions
from app.services.cash_ledger import CashLedgerWalk, CashSourceFact, sum_projected
from app.services.liability_sign import owed
from app.services.pay_calendar import FiledRow, PayCalendar
from app.services.transfer_legs import TransferLeg
from app.utils.money import ZERO

from ._cash_plan import _CashPlan, _WorkedOutRow, _planned_day
from ._fold import sample_cumulative

_ONE_DAY = timedelta(days=1)
_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class _Credit:
    """Money into the card, and the two days the rule reads of it.

    Attributes:
        dated_on: The day it counts from: by the last day a row counts
            (:meth:`CardPayments._last_day_counted`), it can shrink that row.
        held_from: The day it joins the card's running total: after a day the
            rule reads, it is not yet inside that day's balance.  Never before
            :attr:`dated_on`.
        amount: What it brings in, positive.
        transfer_id: The transfer it is a side of, ``None`` for a plan row's: a
            row's own money is never a credit against itself.
        is_unmarked: Whether it is a plan row the owner has not marked, which a
            true-up on or after its date is taken to hold already.  ``False``
            for recorded money, and for a row this rule priced.
    """

    dated_on: date
    held_from: date
    amount: Decimal
    transfer_id: "int | None"
    is_unmarked: bool


class _CardTerms:
    """A card's dated terms, read at most once and only if a mode asks."""

    def __init__(self, card_id: int) -> None:
        """Hold the card whose terms a statement's rule may ask for.

        Args:
            card_id: The card's account id.
        """
        self._card_id = card_id

    @cached_property
    def versions(self) -> "list[CreditCardParams]":
        """The card's terms versions, refusing a card that has none.

        Returns:
            The versions, non-empty.

        Raises:
            AmountUnresolvable: A dormant card has no statement to price from,
                a state the setup door refuses.
        """
        versions = load_card_term_versions(self._card_id)
        if not versions:
            raise AmountUnresolvable(
                f"Card {self._card_id}'s payment is worked out from a statement "
                "and the card has no terms, so it has no statement. State the "
                "card's terms first."
            )
        return versions


def _money_coming(
    money_in: "list[_Credit]", after: date, dated_by: date,
) -> Decimal:
    """Return the money not yet held at the end of *after*, and dated by *dated_by*.

    Args:
        money_in: The money to read.
        after: The day whose balance it is not yet inside.
        dated_by: The last day it can carry and count.

    Returns:
        The sum, never negative.
    """
    return sum(
        (
            credit.amount for credit in money_in
            if credit.held_from > after and credit.dated_on <= dated_by
        ),
        ZERO,
    )


def _repeats_on_paydays(xfer: Transfer) -> bool:
    """Return whether *xfer*'s definition repeats on paydays, each or every few.

    Such a row's date is only its paycheck's, where any other row's is a
    deadline (the module docstring), so the money a row counts and the statement
    it pays both ask this.

    Args:
        xfer: The row, whose definition names its repeat.

    Returns:
        ``True`` for a repeat counted in paydays.
    """
    repeat = xfer.template.recurrence_rule
    return (
        repeat is not None
        and repeat.unit_id == ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD)
    )


def _day_before_the_row(_xfer: Transfer, dated_on: date, _terms: _CardTerms) -> date:
    """Return the rule's day for the balance: the end of the day before the row."""
    return dated_on - _ONE_DAY


def _end_of_the_statement(xfer: Transfer, dated_on: date, terms: _CardTerms) -> date:
    """Return the rule's day for a statement: the end of the day before its close.

    A row repeating on paydays pays the last statement to close STRICTLY before
    its day: the cycle holding the day before the row opened on that close.  Any
    other row's day is a deadline (the module docstring).
    """
    if _repeats_on_paydays(xfer):
        closes = cycle_containing(terms.versions, dated_on - _ONE_DAY).opens
    else:
        closes = close_of_statement_paid_on(terms.versions, dated_on)
    return closes - _ONE_DAY


def _what_is_owed(owed_then: Decimal, _valued_on: date, _terms: _CardTerms) -> Decimal:
    """Return the balance or the statement balance: what was owed on the rule's day."""
    return owed_then


def _the_statement_minimum(
    owed_then: Decimal, valued_on: date, terms: _CardTerms,
) -> Decimal:
    """Return the minimum of the statement whose balance is *owed_then*."""
    return minimum_payment(terms.versions, valued_on + _ONE_DAY, owed_then)


@dataclass(frozen=True)
class _ModeRule:
    """What one mode reads: the rule's day, and A from what was owed on it.

    Attributes:
        valued_on: The row, its dated day and the card's terms to the rule's day.
        figure: What was owed on that day, that day and the terms, to A.
    """

    valued_on: "Callable[[Transfer, date, _CardTerms], date]"
    figure: "Callable[[Decimal, date, _CardTerms], Decimal]"


# One entry per amount, keyed by the member.  A member added to the enum raises
# at this lookup instead of pricing as whichever arm happened to be last.
_MODE_RULES = {
    CardPaymentModeEnum.CARD_BALANCE: _ModeRule(_day_before_the_row, _what_is_owed),
    CardPaymentModeEnum.STATEMENT_BALANCE: _ModeRule(
        _end_of_the_statement, _what_is_owed,
    ),
    CardPaymentModeEnum.MINIMUM_PAYMENT: _ModeRule(
        _end_of_the_statement, _the_statement_minimum,
    ),
}


def _filed(fact: CashSourceFact) -> FiledRow:
    """Return the row a recorded movement's parent is filed as."""
    if fact.transfer_id is not None:
        return FiledRow(
            table=Transfer.__table__.fullname,
            row_id=fact.transfer_id,
            period_id=fact.pay_period_id,
        )
    return FiledRow(
        table=Transaction.__table__.fullname,
        row_id=fact.transaction_id,
        period_id=fact.pay_period_id,
    )


def _recorded_credit_date(fact: CashSourceFact, calendar: PayCalendar) -> date:
    """Return the day a recorded credit counts from (the module docstring)."""
    if not fact.is_income:
        return fact.purchased_on
    period = calendar.require_period(_filed(fact))
    return min(period.attribution_day(fact.parent_due_date), fact.settled_on)


def _recorded_credits(
    walk: CashLedgerWalk, calendar: PayCalendar,
) -> "list[_Credit]":
    """Return the money the card has been recorded taking in.

    Args:
        walk: The card's walk.
        calendar: The owner's pay calendar.

    Returns:
        One :class:`_Credit` per dated movement worth more than zero.
    """
    return [
        _Credit(
            dated_on=_recorded_credit_date(fact, calendar),
            held_from=fact.settled_on,
            amount=fact.delta,
            transfer_id=fact.transfer_id,
            is_unmarked=False,
        )
        for fact in walk.source_facts
        if fact.delta > ZERO
    ]


def _planned_credits(plan: _CashPlan, calendar: PayCalendar) -> "list[_Credit]":
    """Return the money the card's plan brings in, its worked-out rows aside.

    Valued by the reduction every plan item is (``sum_projected``), one item at
    a time, because the rule reads each credit's own two days.

    Args:
        plan: The card's plan.
        calendar: The owner's pay calendar.

    Returns:
        One :class:`_Credit` per planned item worth more than zero.
    """
    money_in: "list[_Credit]" = []
    for lands_on, items in plan.by_day.items():
        for item in items:
            income, expense = sum_projected([item], plan.basis)
            if income - expense > ZERO:
                money_in.append(_Credit(
                    dated_on=_planned_day(item, calendar),
                    held_from=lands_on,
                    amount=income - expense,
                    transfer_id=(
                        item.transfer.id if isinstance(item, TransferLeg)
                        else None
                    ),
                    is_unmarked=True,
                ))
    return money_in


@dataclass(frozen=True)
class CardPayments:
    """What one account's worked-out card payments are priced at.

    Built for every account and costing nothing until asked: an account whose
    plan holds no worked-out row is asked only by a row outside the plan, and an
    account that is not a card never is.

    Attributes:
        card_id: The account.
        seed: Its running total's seed, before any worked-out row.
        steps: Its running total's steps, before any worked-out row.
        walk: Its walk.
        plan: Its plan.
        calendar: The owner's pay calendar.
    """

    card_id: int
    seed: Decimal
    steps: "list[tuple[date, Decimal]]"
    walk: CashLedgerWalk
    plan: _CashPlan
    calendar: PayCalendar

    @cached_property
    def _terms(self) -> _CardTerms:
        """The card's terms, read only if a mode asks."""
        return _CardTerms(self.card_id)

    @cached_property
    def _ordered_rows(self) -> "list[_WorkedOutRow]":
        """The plan's worked-out rows in the order they are priced."""
        return sorted(
            self.plan.worked_out,
            key=lambda row: (row.dated_on, row.leg.transfer.id),
        )

    @cached_property
    def _others(self) -> "list[_Credit]":
        """Every credit that is not a worked-out row of the plan."""
        return (
            _recorded_credits(self.walk, self.calendar)
            + _planned_credits(self.plan, self.calendar)
        )

    @cached_property
    def _priced(self) -> "dict[int, _Credit]":
        """Each worked-out row as the credit its price makes, by transfer id.

        In date order, each row joining what the next one reads.
        """
        priced: "dict[int, _Credit]" = {}
        for row in self._ordered_rows:
            transfer = row.leg.transfer
            priced[transfer.id] = _Credit(
                dated_on=row.dated_on,
                held_from=row.lands_on,
                amount=self._price(
                    transfer, row.dated_on, row.lands_on, list(priced.values()),
                ),
                transfer_id=transfer.id,
                is_unmarked=False,
            )
        return priced

    def _last_day_counted(self, xfer: Transfer, dated_on: date) -> date:
        """Return the latest date money can carry and still shrink *xfer*.

        Args:
            xfer: The row, whose definition names its repeat.
            dated_on: The row's dated day.

        Returns:
            The last day of the row's paycheck for an each-payday row, and
            *dated_on* for any other (the module docstring).

        Raises:
            RuntimeError: The row names a pay period the calendar does not
                hold.
        """
        if _repeats_on_paydays(xfer):
            return self.calendar.require_period(FiledRow.for_row(xfer)).end_date
        return dated_on

    def _last_true_up(self, day: date) -> date:
        """Return the last day the card's balance was stated, by *day*.

        Args:
            day: The latest day it can be.

        Returns:
            That day, the books' opening among them; ``date.min`` with none.
        """
        return max(
            (
                fact.observed_on for fact in self.walk.anchor_facts
                if fact.observed_on <= day
            ),
            default=date.min,
        )

    def _owed_without(
        self, xfer: Transfer, day: date, priced: "list[_Credit]",
    ) -> Decimal:
        """Return what the card owes at the end of *day*, *xfer*'s money out.

        Args:
            xfer: The row, whose recorded money is taken back out.
            day: The day.
            priced: The worked-out rows already priced.

        Returns:
            What is owed, negative for a card in credit.
        """
        held = sample_cumulative(self.seed, self.steps, [day])[day] + sum(
            (credit.amount for credit in priced if credit.held_from <= day),
            ZERO,
        )
        own_money_inside = sum(
            (
                credit.amount for credit in self._others
                if credit.transfer_id == xfer.id and credit.held_from <= day
            ),
            ZERO,
        )
        return owed(held) + own_money_inside

    def _price(
        self,
        xfer: Transfer,
        dated_on: date,
        lands_on: date,
        priced: "list[_Credit]",
    ) -> Decimal:
        """Return ``max(0, min(A - X, B - Y))`` for one row (the module docstring).

        Args:
            xfer: The row, whose definition names the mode.
            dated_on: The row's dated day.
            lands_on: The day it joins the card's running total; its dated day
                for a row outside the plan, which never does.
            priced: The worked-out rows the rule may already count.

        Returns:
            The price, never negative.

        Raises:
            AmountUnresolvable: A mode that reads a statement, on a card with
                no terms.
            RuntimeError: An each-payday row names a pay period the calendar
                does not hold.
        """
        rule = _MODE_RULES[ref_cache.card_payment_mode_member(
            xfer.template.card_payment_settings.mode_id,
        )]
        valued_on = rule.valued_on(xfer, dated_on, self._terms)
        last_counted = self._last_day_counted(xfer, dated_on)
        others = [
            credit for credit in self._others + priced
            if credit.transfer_id != xfer.id
        ]
        on_the_statement = _money_coming(others, valued_on, valued_on)
        asked = rule.figure(
            self._owed_without(xfer, valued_on, priced) - on_the_statement,
            valued_on, self._terms,
        ) - (_money_coming(others, valued_on, last_counted) - on_the_statement)

        before_landing = lands_on - _ONE_DAY
        restated_on = self._last_true_up(before_landing)
        left_from = max(valued_on, restated_on)
        left_now = self._owed_without(xfer, left_from, priced) - _money_coming(
            [
                credit for credit in others
                if not (credit.is_unmarked and credit.dated_on <= restated_on)
            ],
            left_from,
            max(last_counted, before_landing),
        )
        return max(_ZERO_MONEY, min(asked, left_now))

    def with_prices(self, day_nets: "dict[date, Decimal]") -> "dict[date, Decimal]":
        """Return *day_nets* with each worked-out row's price on its landing day.

        Args:
            day_nets: The plan's nets without worked-out rows.

        Returns:
            A new mapping; *day_nets* is left as it was.
        """
        merged = dict(day_nets)
        for credit in self._priced.values():
            merged[credit.held_from] = (
                merged.get(credit.held_from, _ZERO_MONEY) + credit.amount
            )
        return merged

    def price_of(self, xfer: Transfer) -> Decimal:
        """Return what the worked-out payment *xfer* is priced at.

        Args:
            xfer: The row, on any status and deleted or not.

        Returns:
            The price.

        Raises:
            AmountUnresolvable: A mode that reads a statement, on a card with
                no terms.
            RuntimeError: A row outside the plan names a pay period the
                calendar does not hold.
        """
        if xfer.id in self._priced:
            return self._priced[xfer.id].amount
        period = self.calendar.require_period(FiledRow.for_row(xfer))
        dated_on = period.attribution_day(xfer.due_date)
        return self._price(xfer, dated_on, dated_on, list(self._priced.values()))

    def next_price(self, template_id: int) -> Decimal:
        """Return what the next payment of a definition is priced at.

        Args:
            template_id: The definition.

        Returns:
            The price of its earliest row still in the plan, ``0.00`` with none.
        """
        for row in self._ordered_rows:
            if row.leg.transfer.transfer_template_id == template_id:
                return self._priced[row.leg.transfer.id].amount
        return _ZERO_MONEY
