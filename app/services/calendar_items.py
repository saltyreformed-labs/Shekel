"""Shekel Budget App -- what a calendar SPAN holds, loaded once per build.

The analytics calendar builds a month card, or twelve of them for the year
overview, from one span's items: the saved paychecks the span touches, the
plan rows and transfer legs filed under them, what each is worth, and, past
the owner's last saved paycheck, the occurrences the cash fold estimates
there (plan step ``pay_calendar:C9``, task plan#3945, closing ledger row
**N-394**).  :func:`span_items` loads them once and :class:`SpanItems`
carries them, so every month summary reads one answer.

**Its own module because the ceiling said so** (``calendar_service`` stood at
1,049 lines with the estimated occurrences added, past pylint's 1,000): the
loading of a span's items is the one part of that module that is about WHICH
items a span holds rather than how a month is drawn from them.

Owner scoping is the caller's precondition, stated on
:func:`query_transactions_for_range`.  Pure-function service: no Flask
imports, no database writes.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import joinedload

from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import balance_at, cash_ledger
from app.services.balance_at import BalanceContext, EstimatedFlow
from app.services.cash_flow_set import (
    CashFlowSet,
    Paychecks,
    PlanScope,
    StatusBand,
    leg_accounts_shown,
    load_plan_items,
)
from app.services.pay_calendar import PeriodWindow
from app.services.transfer_legs import PlanItem


@dataclass(frozen=True)
class SpanItems:
    """Everything one calendar span holds, resolved once for the whole build.

    The year overview builds twelve summaries from one span, and the month
    view one, so these are loaded once and read by every summary.

    Attributes:
        periods: The SAVED pay periods the span touches -- ONE resolution of
            "which paychecks does this span touch" (plan step C2-f1), the
            window the rows are selected by and placed through.  The rows'
            load and the period strip each issued the same
            ``get_overlapping_periods`` call before that step, so one request
            held two answers to one question, ledger row **P36**'s shape.
        transactions: The plan ITEMS filed under those periods
            (:func:`query_transactions_for_range`) -- rows and, since leaf
            ``balance:X-bi-6-1b``, transfer legs.
        contributions: What each item is WORTH, keyed by
            :func:`~app.services.transfer_legs.cell_key` and resolved ONCE
            (plan step X-au-c2).  Beside the rows rather than recomputed per
            month because the salary producer behind it runs the paycheck
            engine over the owner's entire pay-period set (finding **N-228**),
            so a per-month valuation would run it twelve times for one answer;
            in one record with them because the rows and their prices are one
            fact, and two arguments are two things a caller can mismatch
            (:class:`~app.services.cash_ledger.AmountBasis`).
        estimated: The ESTIMATED occurrences past the last saved paycheck,
            each drawn from the side the set shows (:func:`_estimated_flows`),
            the items no row exists for there; empty for a span inside the
            saved schedule.
    """

    periods: PeriodWindow
    transactions: list[PlanItem]
    contributions: dict
    estimated: list[EstimatedFlow]


def span_items(
    cash_flow: CashFlowSet,
    balance_ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> SpanItems:
    """Load everything the span ``[first_day, last_day]`` holds for *cash_flow*.

    Args:
        cash_flow: The owner's cash-flow set; the CALLER owns its ownership
            check (:func:`query_transactions_for_range`).
        balance_ctx: The read pass for that same owner: its calendar answers
            which paychecks the span touches, its scenario scopes the rows,
            its amount basis prices them and its cash folds hold the estimate.
        first_day: Inclusive first day of the span.
        last_day: Inclusive last day of the span.

    Returns:
        The span's :class:`SpanItems`.
    """
    periods = balance_ctx.calendar().overlapping(first_day, last_day)
    transactions = query_transactions_for_range(
        cash_flow, balance_ctx.scenario_id, periods,
    )
    return SpanItems(
        periods=periods,
        transactions=transactions,
        contributions=_contributions(transactions, balance_ctx),
        estimated=_estimated_flows(cash_flow, balance_ctx, first_day, last_day),
    )


def _contributions(items: list[PlanItem], balance_ctx: BalanceContext) -> dict:
    """Return ``{cell_key: what the item is WORTH}`` over a build's items.

    ONE :func:`~app.services.cash_ledger.contributions_by_key` call over
    every item, keyed by :func:`~app.services.transfer_legs.cell_key` and
    valued by each item's parent (ruling **plan#1983**) -- an ``int`` and a
    tuple cannot collide, so one dict serves both shapes and the day fold
    indexes it with one expression.  It merged a row map with its leg twin's
    (leaf ``balance:X-bi-6-1b``) until that ruling.  It reads the pass's ONE
    basis (plan step X-au-c2b).

    Args:
        items: The build's plan items, :func:`query_transactions_for_range`'s
            list.
        balance_ctx: The read pass.

    Returns:
        The map, covering every item.
    """
    return cash_ledger.contributions_by_key(items, balance_ctx.amounts())


def query_transactions_for_range(
    cash_flow: CashFlowSet,
    scenario_id: int,
    periods: PeriodWindow,
) -> list[PlanItem]:
    """Load the plan items filed under every period of *periods*.

    **The items are the PAYCHECK's across the owner's cash-flow set --
    checking and its cards -- not one account's** (developer ruling
    ``credit_card:R-CC16``, plan step CC-4-3), and since leaf
    ``balance:X-bi-6-1b`` (ruling **R-BAL86**) they are the set's OWN rows
    plus one LEG per transfer, read off the parent in ``budget.transfers``
    rather than off a shadow row: :func:`~app.services.cash_flow_set
    .own_rows_clause` selects every member's plan rows and no shadow, and
    each transfer the set touches is drawn from the side
    :func:`~app.services.cash_flow_set.leg_accounts_shown` names (ruling
    ``R-CC23``: once, from the balance line's side, when both endpoints are
    members) -- an expense leg on checking's calendar, an income leg on the
    card's, exactly the two shadow rows' types.  It was
    ``Transaction.account_id == account_id``, which dropped the phone bill
    that lives on the card from the month it is due in; then the
    paycheck-rows clause, which carried the near-side shadow in as a row.

    **It TAKES the window rather than resolving one** (plan step C2-f1).  Its
    caller (:func:`span_items`) already holds the answer -- the calendar
    renders it as the period strip -- and the calendar's two entries each
    recomputed it here from the same three arguments, so a render carried two
    answers to "which paychecks does this span touch" with nothing holding
    them equal: ledger row **P36**'s shape inside one module, answered as
    ruling R-Q and plan step C2-c answered it for the balance seam.

    Fetches by PERIOD MEMBERSHIP -- all balance-contributing rows whose
    ``pay_period_id`` is one of *periods* -- NOT by raw ``due_date``.  This is
    the basis the clamped
    :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day` display
    rule and the daily balance producer both use: a transaction is attributed
    to a day inside its own pay period, so the day cell that renders it and the
    balance line that steps for it share one period-anchored day.  A ``due_date`` that
    strays outside its period (the reason the prior query needed a second
    due-date-in-range path) is pulled back to the period boundary by the
    clamp; fetching by membership means such a row is still loaded for the
    month its period lands in and is never dropped.  ``_get_display_day``
    then filters each loaded row to the single month its clamped attribution
    date falls in, so a period straddling two months splits its rows across
    them without double-counting.

    **The calendar's scope, stated once** (plan step **plan#2130**): the set,
    the scenario, these paychecks, balance-contributing, every type -- which
    :func:`~app.services.cash_flow_set.load_plan_items` turns into the rows'
    query and the transfers' alike, the rows in id order then the sides.  The
    row query carried no ``ORDER BY`` until that step, so two ROWS of one
    type and one magnitude on one day, which
    :func:`~app.services.calendar_day_flows.order_for_display` does not tell
    apart, were listed in whatever order the planner returned them.

    Eager-loads category, status, template -> recurrence_rule, and
    pay_period on the rows to prevent N+1 queries downstream; the transfers
    carry the loader's own.

    Per F-3 / HIGH-02 / W-065, the item set is constrained by
    :func:`~app.utils.balance_predicates.balance_contributing_clause`
    (``is_deleted=False AND status_id NOT IN (Credit, Cancelled)``), the
    :attr:`~app.services.cash_flow_set.StatusBand.BALANCE_CONTRIBUTING` band
    the loader states once over ``Transaction`` for the rows and ``Transfer``
    for the legs' parents, rather than the prior inline
    ``is_deleted=False``-only gate.  This is the locked Choice-2 semantic
    from ``remediation_follow_up_plan.md`` Section 2: calendar day cells
    display realized payments at their settled date, so the predicate
    is "balance-contributing" (Projected + Settled, excludes Credit and
    Cancelled) -- intentionally wider than the grid period subtotal's
    Projected-only predicate.  The two surfaces diverge by design.

    **``user_id`` is gone, so OWNER SCOPING is a precondition on the caller
    rather than a filter here** -- stated because an unstated one is how a
    scoping check goes missing.  Both calendar entries take *cash_flow* from
    ``resolve_analytics_cash_flow_set(user_id, ...)``, whose every member
    passed the owner's admission test, and *periods* off a
    ``BalanceContext`` built for that same owner, so both keys are already
    theirs.

    Args:
        cash_flow: The owner's cash-flow set, whose members' items to load;
            the CALLER owns its ownership check.
        scenario_id: The budget scenario the items live in.
        periods: The pay periods the span touches, resolved once by the caller
            off its own ``BalanceContext``, which is what scopes them.

    Returns:
        The plan items: the matching
        :class:`~app.models.transaction.Transaction` rows by id, then the
        legs of the matching transfers, their records not pre-loaded
        (ruling **plan#2136**).
    """
    return load_plan_items(
        PlanScope(
            cash_flow=cash_flow,
            scenario_id=scenario_id,
            window=Paychecks(tuple(p.period_id for p in periods)),
            band=StatusBand.BALANCE_CONTRIBUTING,
            expenses_only=False,
        ),
        (
            joinedload(Transaction.category),
            joinedload(Transaction.status),
            joinedload(Transaction.template).joinedload(
                TransactionTemplate.recurrence_rule,
            ),
            joinedload(Transaction.pay_period),
        ),
    )


def _estimated_flows(
    cash_flow: CashFlowSet,
    balance_ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> list[EstimatedFlow]:
    """Return the set's estimated occurrences in the span, each from the side it shows.

    **The rows' twin past the last saved paycheck** (plan step
    ``pay_calendar:C9``, task plan#3945, closing ledger row **N-394**).  No
    row exists there for :func:`query_transactions_for_range` to load, and
    the balance line under the cells folds each member's ESTIMATED tier, so
    the cells read that same list
    (:func:`~app.services.balance_at.cash_estimated_flows`) and a projected
    month's entries, totals and balance reconcile as a saved month's do.  The
    scope is the rows': a transaction template's occurrence is on the member
    its rows live on, and a transfer template's is drawn from the side
    :func:`~app.services.cash_flow_set.leg_accounts_shown` names for a written
    transfer (ruling ``credit_card:R-CC23``).

    Args:
        cash_flow: The owner's cash-flow set; the CALLER owns its ownership
            check, as for the rows.
        balance_ctx: The read pass whose cash folds the balance line reads.
        first_day: Inclusive first day of the span.
        last_day: Inclusive last day of the span.

    Returns:
        The flows, member by member in the set's order; empty for a span
        inside the saved schedule.
    """
    shown: list[EstimatedFlow] = []
    for member in cash_flow.members:
        for flow in balance_at.cash_estimated_flows(
            member, balance_ctx, first_day, last_day,
        ):
            if isinstance(flow.template, TransferTemplate) and (
                member.id not in leg_accounts_shown(cash_flow, flow.template)
            ):
                continue
            shown.append(flow)
    return shown


__all__ = ["SpanItems", "query_transactions_for_range", "span_items"]
