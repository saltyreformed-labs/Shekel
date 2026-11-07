"""Balance-at-T seam -- the cash fold's PLANNED tier: what is still to happen.

The still-Projected rows of one account, loaded once and keyed onto the day each
lands on (:func:`_cash_plan`), and what each landing day is worth
(:func:`_planned_day_nets`).  :mod:`._cash_fold` merges those nets into its one
running total and :mod:`._cash_periods` regroups the same rows by the period
each was budgeted to, so both read one load.

**A module of its own since plan step credit_card:CC-6** (plan#2677), a pure
move: :mod:`._cash_fold` stood at 997 lines against the 1,000-line ceiling, and
the card's worked-out payment is priced beside this tier.  A plan is a job of
its own, sharing one record with the fold's readers, and the dependency runs
one way: :mod:`._cash_fold` imports this and this imports nothing of it.

**A bill on a card counts from its date, marked or not** (ruling plan#2926).  The
standing rule R-G lands an overdue row tomorrow because a plan cannot have already
happened, which is right for checking, where the owner still has to pay it, and
wrong for a card that pays the bill by itself.  So on an account whose type is
revolving (:func:`~app.services.account_projection.is_revolving`) a row counts from
its own date, whichever day the reader looks, and :func:`_day_it_counts_from` names
what keeps R-G and why.  Checking and every other account land exactly as they did.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.account_projection import is_revolving
from app.services.cash_ledger import (
    AmountRule,
    CashLedgerWalk,
    InFlightMovement,
    AmountBasis,
    planned_cash_rows,
    sum_projected,
    transfer_amount_rule,
)
from app.services.pay_calendar import FiledRow, PayCalendar
from app.services.transfer_legs import TransferLeg, parent_of
from app.utils.books_boundary import books_hold

_PlanItem = Transaction | TransferLeg | InFlightMovement  # one plan item

# Ruling R-G's clamp floor: the earliest day a plan can still happen is the day
# AFTER the reader's as-of.  Named (and spelled the same way) as the loan twin's
# ``_plan._ONE_DAY``, because it is the same rule -- ruling D1 for loans, R-G for
# cash -- and the two are meant to read as one.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class _WorkedOutRow:
    """A card's worked-out payment still in the plan, and its two days.

    Attributes:
        leg: The to-side :class:`~app.services.transfer_legs.TransferLeg`, on
            the card.
        dated_on: The day the payment is dated (:func:`_planned_day`).
        lands_on: The day its money joins the card's running total: *dated_on*
            clamped up to the reader's tomorrow (ruling R-G).
    """

    leg: TransferLeg
    dated_on: date
    lands_on: date


@dataclass(frozen=True)
class _CashPlan:
    """The account's still-Projected rows, loaded and dated once.

    The PLANNED tier's inputs, shared by the fold's readers so a
    plan is loaded (and its live override map built) ONCE per call whichever
    reader is asking.  Splitting the LOAD from the reduction is what lets the
    period view group the same rows on the budget clock without a second query
    -- redundant derivation being where a divergence hides (the lesson the read
    pass's context was built on).

    Attributes:
        rows: What :func:`~app.services.cash_ledger.planned_cash_rows` loads,
            unwindowed: every still-Projected contributing row of the
            account's OWN, one
            :class:`~app.services.transfer_legs.TransferLeg` per
            still-projected transfer the account is on (ruling
            **R-BAL13**), and one
            :class:`~app.services.cash_ledger.InFlightMovement` per un-dated
            purchase on the account (ruling **R-BAL77**).
        by_day: The same items keyed by the day each LANDS on (ruling R-G's
            clamp; a movement in flight's floor is the day it happened) --
            the cash clock.  Empty when the account has no plan.  **A card's
            worked-out payments are NOT here** (plan step ``credit_card:CC-6``):
            their price is worked out off this very running total, so valuing
            them with the rest would have the assembly ask itself.  **Nor are
            the rows in** :attr:`on_their_dates`.
        on_their_dates: A card's rows that count from their own dates, keyed by
            that date (ruling plan#2926; :func:`_day_it_counts_from`).  Apart
            from :attr:`by_day` because they are what the books hold rather than
            what is still to happen: they join the recorded steps
            (:func:`~._cash_fold._recorded_steps`), and a true-up includes them
            (:func:`~._assertions.assertion_corrections`).  Empty on every other
            account.
        worked_out: Those payments, on the card that holds them, each with its
            two days (:mod:`._card_payment` prices them).  They stay in
            :attr:`rows`, because the period view reads rows after the fold
            is assembled, when asking for a price is safe.
        basis: The READ PASS's
            :class:`~app.services.cash_ledger.AmountBasis` -- what every row is
            priced through -- carried here and threaded into every reduction so
            no reduction reaches for one of its own.  It was BUILT here, over
            this plan's rows, until plan step X-au-c2b: a basis is pinned to an
            owner and a scenario now rather than to a row set, so the pass hands
            one down instead and a second reader of the same request stops
            paying for a second paycheck-engine run (findings **N-268**,
            **N-269**).  It was a
            ``ProjectedBasis`` carrying the account's clearing rule beside this
            until plan step X-f3b: the entry reservation was what asked, and
            ruling **R-FM** made a purchase's posted-ness a fact about the
            PURCHASE, so the reservation reads the row in front of it and the
            clearing rule stayed with the WALK, which is the only thing that
            needs it.
    """

    rows: "list[_PlanItem]"
    by_day: "dict[date, list[_PlanItem]]"
    basis: AmountBasis
    worked_out: "list[_WorkedOutRow]"
    on_their_dates: "dict[date, list[_PlanItem]]"

    @cached_property
    def nets_on_their_dates(self) -> "dict[date, Decimal]":
        """``{day: net}`` for :attr:`on_their_dates`, valued once per plan."""
        return _nets_by_day(self.on_their_dates, self.basis)


def _cash_plan(
    account: Account,
    basis: AmountBasis,
    as_of: date,
    calendar: PayCalendar,
    walk: CashLedgerWalk,
) -> _CashPlan:
    """Load the account's plan and key each row onto the day it lands on.

    The PLANNED tier's load, and the reason it lives in this READER rather than
    in the clock-free leaf: a plan's effective date is a function of *as_of*
    (ruling R-G), exactly as the loan plan's is (plan step C6a).

    **Where a planned row lands.**  Its nominal day is the shared
    :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day` -- its
    ``due_date``, falling back to
    its pay period's ``start_date``, clamped into that period's span -- the SAME
    rule the calendar groups its day cells by, so a flow's cell and the balance
    line's step for it land on one day, but for a card's row whose own date falls
    outside its paycheck (:func:`_day_it_counts_from`).  That day is then clamped
    UP to ``as_of + 1``: a plan cannot have already happened, so an overdue bill
    moves forward rather than being absorbed by the next assertion's reset.
    Rejected at the ruling: landing it on its nominal date, which on real data
    (one re-anchor every 2.3 days on Checking) silently deletes nearly every
    unpaid past-due bill within days of its being entered.  Ruling plan#2926 takes
    that landing for a card, where a true-up is taken to hold what is dated by it.

    **A movement IN FLIGHT lands on ``max(the day it happened, as_of + 1)``**
    (ruling **R-BAL77**): money already spent and not yet seen to leave, with
    no due date and no period to clamp into, held from tomorrow and never
    from before it was made.  Its BUDGET column stays its parent's.

    **A transfer LEG lands where its PARENT is filed** (plan step **X-bi-6a**).
    The plan holds one :class:`~app.services.transfer_legs.TransferLeg`
    per still-projected transfer the account is on, and the row the calendar
    is asked to place is the parent ``Transfer`` -- its ``pay_period_id`` and
    ``due_date`` are the leg's, and
    :meth:`~app.services.pay_calendar.FiledRow.for_row` already takes a
    transfer.  Under Transfer Invariant 3 that was the day the shadow row this
    leg replaced landed on; where a pair had drifted, the parent's day won,
    which is ruling **R-JA**'s direction.

    **The SPAN it clamps against is DERIVED, since pay-calendar plan step
    C4-a-1**, which is why this function takes a calendar -- see
    :meth:`~app.services.pay_calendar.PayCalendar.require_period` -- the ONE
    statement of that rule for every caller placing a stored row.  It read
    ``txn.pay_period`` until then, so the fold
    clamped a projected row against a STORED end while :func:`~._cash_fold.period_balances`
    sampled the very same period at its derived one.

    **This is the EXPOSED order for balance finding N-358, and saying which
    order a caller has is the whole of its exposure.**  The calendar is derived
    before these rows are loaded, so under ``READ COMMITTED`` a payday appended
    between the two leaves rows filed in a period the calendar never saw -- and
    the rolling top-up appends exactly that way, mid-render, on ``/grid`` and
    ``/dashboard``.  What holds today is a MEASUREMENT rather than a
    guarantee: recording the request behind every :func:`~._cash_fold.assembled_fold` across
    the whole suite on 2026-08-27 gave **2,206 dispatched-request assemblies
    over 22 distinct endpoints, every one of them a GET**, and a GET holds one
    snapshot between :func:`~app.db_transaction.write_transaction` blocks.  Two
    limits on that, both real.  It is a census of what RAN, so it cannot say
    what does not run.  And it counted assemblies REACHED THROUGH A REQUEST --
    the suite calls this door directly too, and those are not in the figure --
    so the supporting fact for "no other caller" is a different one:
    ``grep -rl balance_at scripts/`` is empty, so no CLI door reaches the seam
    at all.  ``balance:X-i5`` is what turns the measurement into a guarantee.

    **The load is separate from the reduction, and that is plan step X-c1's
    doing.**  The rows are kept, not just their per-day totals, because the
    period view reduces the SAME rows a second way -- by the budget column they
    were attributed to -- and re-loading them for that would be two answers to
    "what is in this account's plan" a day apart from each other.

    **What it reads of the WALK is two dates, and only on a card.**  The walk was
    an argument once before, for a projected envelope's reservation, which had to
    know which of its purchases a declared balance already contained.  Ruling
    **R-FM** dissolved that (plan step X-f3b): a purchase carrying a recorded
    posting day is a cash movement of its OWN in the walk, so the reservation asks
    the purchase, and no row's WORTH reads the walk.  What a card's row needs is
    WHEN: the day the books opened, because a row dated by then is already inside
    the opening equity (ruling **R-HG**) and moves nothing, and the card's last
    true-up (:func:`_stated_through`).

    Args:
        account: The account whose plan to load.
        basis: The read pass's amount basis, carrying the scenario the rows live
            in and the derivations they are priced through.
        as_of: The reader's NOW -- the floor ruling R-G clamps a landing day up
            to.
        calendar: The OWNER's pay calendar, which each row's span is read
            from (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).
        walk: The account's walk, the same one the fold replays.

    Returns:
        The account's :class:`_CashPlan`; its ``rows`` and ``by_day`` are empty
        for an account with no plan, and it carries the pass's basis either way
        so the record is self-describing.

    Raises:
        RuntimeError: A row names a pay period *calendar* does not hold
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).
    """
    rows = planned_cash_rows(account.id, basis.scenario_id)
    if not rows:
        return _CashPlan(
            rows=[], by_day={}, basis=basis, worked_out=[], on_their_dates={},
        )

    not_before = as_of + _ONE_DAY
    stated_through = _stated_through(walk) if is_revolving(account) else None
    by_day: "dict[date, list[_PlanItem]]" = defaultdict(list)
    on_their_dates: "dict[date, list[_PlanItem]]" = defaultdict(list)
    worked_out: "list[_WorkedOutRow]" = []
    for item in rows:
        dated_on = _planned_day(item, calendar)
        lands_on = max(dated_on, not_before)
        if _is_worked_out(item):
            worked_out.append(_WorkedOutRow(item, dated_on, lands_on))
            continue
        own = None if stated_through is None else own_day_on_a_card(
            item, stated_through=stated_through, opened_on=walk.opening.opened_on,
        )
        if own is None:
            by_day[lands_on].append(item)
        elif own.moves:
            on_their_dates[own.day].append(item)
    return _CashPlan(
        rows=rows, by_day=dict(by_day), basis=basis, worked_out=worked_out,
        on_their_dates=dict(on_their_dates),
    )


def _stated_through(walk: CashLedgerWalk) -> date:
    """Return the last day a card's balance has been stated for.

    Its latest true-up, read off the walk's order
    (:func:`~app.services.cash_ledger.cash_anchor_facts` loads ascending by
    business date), or the day its books opened where it has none.

    Args:
        walk: The card's walk.

    Returns:
        That day.
    """
    if walk.anchor_facts:
        return walk.anchor_facts[-1].observed_on
    return walk.opening.opened_on


def _day_it_counts_from(
    item: "_PlanItem | TransactionEntry", stated_through: date,
) -> "date | None":
    """Return the day a plan item on a card counts from, or ``None`` for R-G's.

    Ruling plan#2926, item by item:

    * **A dated row counts from its own date**, never its paycheck's clamp
      (:meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day`), because
      the date decides the statement, the due date and the interest (ruling
      plan#2910).  Bought Jan 14 with the row in Jan 16's paycheck, the clamp would
      read Jan 16 and pay it a paycheck late.  The grid's two clocks already carry
      a row whose money falls outside its column (``period_timing``).
    * **A swipe or a refund in flight counts from the day it happened**, decided
      under R-BAL207: it has happened, and only the bank's posting is still to
      come.  The cost is a posting dated after a true-up that already held it: the
      posting comes on top, by the same date rule a recorded movement takes (ruling
      R-FL), until the true-up is restated.  The reconcile tick hands the stored
      purchase it is about to date (:func:`own_day_on_a_card`).
    * **An envelope's unspent budget keeps R-G**, decided under R-BAL207: money not
      yet spent has no day it was spent on, so a true-up cannot hold it.  Its swipes
      are the in-flight items above.  The cost: left open past its paycheck, the
      unspent budget lands tomorrow on each read, so it moves to a later statement
      and a later payment row until the envelope is closed.
    * **An undated row keeps R-G**: it has no date to count from, and its
      ``due_date`` of ``None`` says so.
    * **A transfer keeps R-G until a true-up is dated on or after its own date**,
      then counts from that date, so that true-up holds it (ruling plan#2963).
      Until then both its legs land together, so money is never off checking before
      it reaches the card or on the card before it leaves checking.  Once a balance
      read off the card's website is stated, that balance holds money sent by then,
      marked or not (ruling plan#2887), so landing the leg again would take it off
      twice: $800 owed, $200 sent Jan 5, $600 stated Jan 10, and the next payment is
      $600 rather than $400.  Checking's leg still lands tomorrow.  The cost: if the $200
      had not really gone, the owner states $800, the row reads $800 and checking
      reads $200 low until he marks the transfer with a later day than the true-up;
      a true-up closes its own day, so money moved later that same day is inside it.

    Args:
        item: A plan item on a card, not a worked-out payment, or a purchase
            in flight as it is stored.
        stated_through: The last day the card's balance was stated for.

    Returns:
        The day it counts from, or ``None`` where it lands by ruling R-G.
    """
    if isinstance(item, (InFlightMovement, TransactionEntry)):
        return item.purchased_on
    if item.due_date is None:
        return None
    if isinstance(item, TransferLeg):
        return item.due_date if item.due_date <= stated_through else None
    return None if item.tracks_purchases else item.due_date


@dataclass(frozen=True)
class OwnDay:
    """The day a card counts a plan item from by itself (ruling plan#2926).

    Attributes:
        day: That day (:func:`_day_it_counts_from`).
        moves: Whether the card's books hold it (``books_hold``).  ``False`` for
            a day by the books' opening, which already holds the money, so the
            item moves nothing.
    """

    day: date
    moves: bool


def own_day_on_a_card(
    item: "_PlanItem | TransactionEntry", *, stated_through: date, opened_on: date,
) -> "OwnDay | None":
    """Return the day a card counts *item* from by itself, or ``None`` for R-G's.

    The ONE answer, read by the fold (:func:`_cash_plan`) and by the reconcile
    tick (plan#2958): a tick on the card records the day the card already counts
    the money from, so ticking it moves nothing (ruling plan#2926, "marked or
    not").  It recorded the statement's day before, which moved $800 dated Jan 3
    off the Jan 5 statement once it was ticked on the Jan 20 one.

    Args:
        item: A plan item on a card, not a worked-out payment, or a purchase
            in flight as it is stored (the purchase arm's tick).
        stated_through: The last day the card's balance was stated for.
        opened_on: The day the card's books opened.

    Returns:
        The :class:`OwnDay`, or ``None`` where the card lands it by ruling R-G.
    """
    counts_from = _day_it_counts_from(item, stated_through)
    if counts_from is None:
        return None
    return OwnDay(counts_from, books_hold(opened_on, counts_from))


def _planned_day(item: _PlanItem, calendar: PayCalendar) -> date:
    """Return the day *item* is dated, before ruling R-G's clamp.

    The attribution day of the period its parent is filed in; a movement in
    flight has no due date and no attribution clamp, so it is dated the day it
    happened (ruling **R-BAL77**).  **One statement**, for the clamp above and
    for the card's rule, which needs a credit's date as well as its landing
    day (:mod:`._card_payment`).  A dated row on a card counts from its own date
    instead (:func:`_day_it_counts_from`).

    Args:
        item: A plan item.
        calendar: The owner's pay calendar.

    Returns:
        The day it is dated.

    Raises:
        RuntimeError: Its parent names a pay period *calendar* does not hold.
    """
    if isinstance(item, InFlightMovement):
        return item.purchased_on
    period = calendar.require_period(FiledRow.for_row(parent_of(item)))
    return period.attribution_day(item.due_date)


def _is_worked_out(item: _PlanItem) -> bool:
    """Return whether *item* is the card's side of its worked-out payment.

    Only the to-side: checking's leg is an ordinary plan item that asks the
    amount model, which asks the card's fold, so the one answer is the card's
    and the assembly of the card never asks itself.

    Args:
        item: A plan item.

    Returns:
        ``True`` for the to-side leg of a transfer the card rule prices.
    """
    return (
        isinstance(item, TransferLeg)
        and item.is_income
        and transfer_amount_rule(item.transfer) is AmountRule.CARD_PAYMENT
    )


def _planned_day_nets(plan: _CashPlan) -> "dict[date, Decimal]":
    """Return ``{day: net}`` for the plan -- what each landing day is WORTH.

    The PLANNED tier's reduction, split from its load (:func:`_cash_plan`) so the
    two readers share one valuation.

    **What a planned row is WORTH is not re-implemented here.**  Each day's group
    is reduced by the shared
    :func:`~app.services.cash_ledger.sum_projected` -- the same engine the
    shipping period walk and the grid's subtotal row both call, carrying the
    entries-aware reservation for an envelope expense and the live override for a
    salary paycheck or a derived loan debit.  Reducing per GROUP rather than per
    ROW is what keeps that one rule intact: ``sum_projected`` is additive over
    disjoint groups, so the days of a period sum to the period's net exactly --
    which is also why :func:`~._cash_periods._budget_legs` may reduce the same rows grouped by
    pay period and get an answer that reconciles with this one to the cent.

    **The reservation reads no clock, and that is the whole of finding N-39's
    resolution.**  This reduction once passed *as_of* as ``sum_projected``'s
    entry-date window, so an entry dated after the reader's now could not reduce
    the reservation early -- a deliberate pick between the two answers the three
    shipping producers gave (the scalar windowed, the grid and the daily ramp did
    not).  Ruling R-M closed the fork at the SOURCE rather than picking a winner:
    plan step X-c0 refuses ``entry_date > display_today()`` at both write doors,
    after which the window could drop nothing, and plan step X-c2c1 deleted it.
    So a planned row's WORTH is now a function of the row alone and *as_of*
    survives in this tier for exactly one job -- ruling R-G's clamp, which
    :func:`_cash_plan` applied when it keyed these groups by day.

    Args:
        plan: The account's :class:`_CashPlan`, already keyed onto the day each
            row lands on (ruling R-G's clamp, applied at load).

    Returns:
        ``{day: net}`` -- one entry per day carrying at least one planned row
        in :attr:`_CashPlan.by_day`, valued as signed income-minus-expense.
        ``{}`` for an account with no plan.  A card's rows on their own dates are
        not in them (:attr:`_CashPlan.nets_on_their_dates`), and its worked-out
        payments join afterwards (:func:`~._cash_fold._assemble`).
    """
    return _nets_by_day(plan.by_day, plan.basis)


def _nets_by_day(
    items_by_day: "dict[date, list[_PlanItem]]", basis: AmountBasis,
) -> "dict[date, Decimal]":
    """Return what each day's plan items are worth, income less expense.

    Args:
        items_by_day: The items, keyed by the day each counts on.
        basis: The read pass's amount basis.

    Returns:
        One net per key.
    """
    nets: "dict[date, Decimal]" = {}
    for day, items in items_by_day.items():
        income, expense = sum_projected(items, basis)
        nets[day] = income - expense
    return nets


def _rows_on_their_date(
    plan: _CashPlan, day: date,
) -> "list[tuple[Decimal, str | None]]":
    """Return each row counted from *day*, by what it moves and what it is called.

    Args:
        plan: The account's plan.
        day: The day.

    Returns:
        ``[(net, name), ...]`` summing to that day's net in
        :attr:`_CashPlan.nets_on_their_dates`, without rows worth nothing.  A
        transfer's name can be ``None``.
    """
    named: "list[tuple[Decimal, str | None]]" = []
    for item in plan.on_their_dates.get(day, []):
        income, expense = sum_projected([item], plan.basis)
        if income != expense:
            named.append((income - expense, item.name))
    return named
