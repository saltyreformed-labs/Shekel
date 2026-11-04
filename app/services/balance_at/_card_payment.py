"""Balance-at-T seam -- a card's worked-out payment, priced inside its balance.

Plan step ``credit_card:CC-6``, rulings **R-CC22** (the rule) and plan#2666 (the
three amounts).  A worked-out payment stores no figure (plan#2667); this module
works it out from the card's running total, so it is a balance producer and lives
in a private module of the seam (W9910).  Every reader reaches this one answer:
the amount model asks through :attr:`~app.services.cash_ledger.AmountBasis.card_payments`,
so checking's leg, the card's leg, the grid cell and the popover read one figure.

**The rule, stated once for all three amounts**: ``price = max(0, A - X)``.

* **A** is what the card owes on the rule's day: at the end of the day before
  the row's day (:attr:`~app.enums.CardPaymentModeEnum.CARD_BALANCE`), or at the
  close of the statement the row pays, whose figure is the end of the day before
  that close (:attr:`~app.enums.CardPaymentModeEnum.STATEMENT_BALANCE`), or that
  statement's minimum (:attr:`~app.enums.CardPaymentModeEnum.MINIMUM_PAYMENT`).
* **X** is every other transfer or credit into the card dated on or before the
  row's day and not yet inside A: placed and unpaid, or paid later.

So a one-off in the same paycheck shrinks the row and the paycheck's total stays
what the card owes; a payment on the close day counts and one after the due date
does not; an earlier row paid late is not paid twice; and a purchase on the row's
own day rolls to the next row.

**The statement a row pays is the one closing strictly before its day.**  A due
day equal to the close day makes the row's day itself a close, and the row pays
the statement before it (:func:`app.services.card_statement.due_date_for`).

**A row is priced in date order off the running total WITHOUT worked-out rows**,
and each priced row then joins what the next one reads.  That order is sound
because a row lands on or after its dated day: every worked-out row landing by a
row's valuation day is dated earlier, so it is already priced.  Two rows on one
day are ordered by id, and the later sees the earlier as a credit.

**A credit's date is the earlier of its row's date and the day its money moved.**
A row paid late still counts from its own date, so it is not paid twice, and a
row paid early counts from the day the money arrived, for the same reason.  A
refund under an expense row is dated its own day: the row it came back to was
budgeted for something else.

**A row outside the plan answers too**, because a budget figure is asked of every
status (:func:`~app.services.cash_ledger.amounts_by_key`): by the same rule, its
own money taken out of both A and X, so a settled row's plan is what the card
asked of it and not what is left after paying it.

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
from app.enums import CardPaymentModeEnum
from app.exceptions import AmountUnresolvable
from app.models.credit_card_params import CreditCardParams
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.card_statement import cycle_containing, minimum_payment
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
        dated_on: The day it counts from: on or before a row's day, it can
            shrink that row.
        held_from: The day it joins the card's running total: after the rule's
            day, it is not yet inside A.  Never before :attr:`dated_on`.
        amount: What it brings in, positive.
        transfer_id: The transfer it is a side of, ``None`` for a plan row's: a
            row's own money is never a credit against itself.
    """

    dated_on: date
    held_from: date
    amount: Decimal
    transfer_id: "int | None"


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


def _day_before_the_row(dated_on: date, _terms: _CardTerms) -> date:
    """Return the rule's day for the balance: the end of the day before the row."""
    return dated_on - _ONE_DAY


def _end_of_the_statement(dated_on: date, terms: _CardTerms) -> date:
    """Return the rule's day for a statement: the end of the day before its close.

    The statement a row pays is the one closing STRICTLY before its day: the
    cycle holding the day before the row opened on that close.
    """
    closes = cycle_containing(terms.versions, dated_on - _ONE_DAY).opens
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
        valued_on: The row's dated day and the card's terms to the rule's day.
        figure: What was owed on that day, that day and the terms, to A.
    """

    valued_on: "Callable[[date, _CardTerms], date]"
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
        return fact.settled_on
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
                    transfer, row.dated_on, list(priced.values()),
                ),
                transfer_id=transfer.id,
            )
        return priced

    def _price(
        self, xfer: Transfer, dated_on: date, priced: "list[_Credit]",
    ) -> Decimal:
        """Return ``max(0, A - X)`` for one row (the module docstring).

        Args:
            xfer: The row, whose definition names the mode.
            dated_on: The row's dated day.
            priced: The worked-out rows the rule may already count.

        Returns:
            The price, never negative.

        Raises:
            AmountUnresolvable: A mode that reads a statement, on a card with
                no terms.
        """
        rule = _MODE_RULES[ref_cache.card_payment_mode_member(
            xfer.template.card_payment_settings.mode_id,
        )]
        valued_on = rule.valued_on(dated_on, self._terms)
        money_in = self._others + priced
        own_money_inside = sum(
            (
                credit.amount for credit in money_in
                if credit.transfer_id == xfer.id
                and credit.held_from <= valued_on
            ),
            ZERO,
        )
        not_yet_inside = sum(
            (
                credit.amount for credit in money_in
                if credit.transfer_id != xfer.id
                and credit.dated_on <= dated_on
                and credit.held_from > valued_on
            ),
            ZERO,
        )
        held = sample_cumulative(
            self.seed, self.steps, [valued_on],
        )[valued_on] + sum(
            (
                credit.amount for credit in priced
                if credit.held_from <= valued_on
            ),
            ZERO,
        )
        figure = rule.figure(
            owed(held) + own_money_inside, valued_on, self._terms,
        )
        return max(_ZERO_MONEY, figure - not_yet_inside)

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
        return self._price(
            xfer, period.attribution_day(xfer.due_date),
            list(self._priced.values()),
        )

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
