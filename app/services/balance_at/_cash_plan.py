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

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.cash_ledger import (
    InFlightMovement,
    AmountBasis,
    planned_cash_rows,
    sum_projected,
)
from app.services.pay_calendar import FiledRow, PayCalendar
from app.services.transfer_legs import TransferLeg, parent_of

_PlanItem = Transaction | TransferLeg | InFlightMovement  # one plan item

# Ruling R-G's clamp floor: the earliest day a plan can still happen is the day
# AFTER the reader's as-of.  Named (and spelled the same way) as the loan twin's
# ``_plan._ONE_DAY``, because it is the same rule -- ruling D1 for loans, R-G for
# cash -- and the two are meant to read as one.
_ONE_DAY = timedelta(days=1)


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
            the cash clock.  Empty when the account has no plan.
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


def _cash_plan(
    account: Account, basis: AmountBasis, as_of: date, calendar: PayCalendar,
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
    line's step for it cannot land on different days.  That day is then clamped
    UP to ``as_of + 1``: a plan cannot have already happened, so an overdue bill
    moves forward rather than being absorbed by the next assertion's reset.
    Rejected at the ruling: landing it on its nominal date, which on real data
    (one re-anchor every 2.3 days on Checking) silently deletes nearly every
    unpaid past-due bill within days of its being entered.

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

    **It no longer takes the WALK, and plan step X-f3b is why.**  It did, for
    one reason: a projected envelope's reservation had to know which of its
    purchases a declared balance already contained, and that answer
    (:attr:`~app.services.cash_ledger.CashLedgerWalk.coverage`) was read off the
    facts :func:`~._cash_fold._assemble` had already loaded rather than by resolving the
    account's assertions a second time.  Ruling **R-FM** dissolved the question:
    a purchase carrying a recorded posting day is a cash movement of its OWN in
    the walk, so the reservation asks the purchase and the clearing rule stays
    where it is asked -- in the walk, about that movement.  One less argument,
    and no shape in which the plan could be valued against a different account's
    assertions than the walk replayed.

    Args:
        account: The account whose plan to load.
        basis: The read pass's amount basis, carrying the scenario the rows live
            in and the derivations they are priced through.
        as_of: The reader's NOW -- the floor ruling R-G clamps a landing day up
            to.
        calendar: The OWNER's pay calendar, which each row's span is read
            from (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).

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
        return _CashPlan(rows=[], by_day={}, basis=basis)

    not_before = as_of + _ONE_DAY
    by_day: "dict[date, list[_PlanItem]]" = defaultdict(list)
    for item in rows:
        if isinstance(item, InFlightMovement):
            # No due date and no attribution clamp: money already spent lands
            # no earlier than the day it happened (ruling **R-BAL77**).
            by_day[max(item.purchased_on, not_before)].append(item)
            continue
        period = calendar.require_period(FiledRow.for_row(parent_of(item)))
        nominal = period.attribution_day(item.due_date)
        by_day[max(nominal, not_before)].append(item)
    return _CashPlan(rows=rows, by_day=dict(by_day), basis=basis)


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
        ``{day: net}`` -- one entry per day carrying at least one planned row,
        valued as signed income-minus-expense.  ``{}`` for an account with no
        plan.
    """
    nets: "dict[date, Decimal]" = {}
    for day, txns in plan.by_day.items():
        income, expense = sum_projected(txns, plan.basis)
        nets[day] = income - expense
    return nets
