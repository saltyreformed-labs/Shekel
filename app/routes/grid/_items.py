"""
Shekel Budget App -- Grid route package: what the page DRAWS, and its prices.

The grid's window and the three per-cell amount maps over it, moved whole
out of :mod:`~app.routes.grid.page` at plan step ``balance:X-bi-6-1`` (ruling
**R-BAL87**), the leaf that widened the window from one shape to two: the
cash-flow set's OWN plan rows, and one
:class:`~app.services.transfer_legs.TransferLeg` per transfer the set shows,
read off ``budget.transfers`` rather than off a shadow row.  The page module
crossed pylint's 1000-line ceiling under that widening, and this is the
responsibility that grew -- so it is the one that moved, by the sibling-split
convention rather than by shaving prose.

Two producers and one value type: :func:`load_grid_items` answers "what
does this render draw" -- a :class:`~app.services.cash_flow_set.PlanItems`,
the shape every reader of the set's plan items answers since leaf
``X-bi-6-1b`` -- and :func:`build_amount_maps` answers "what does each
item's cell say its amount IS, what its money DID, and what a tick WOULD
book" -- each map holding both shapes under
:func:`~app.services.transfer_legs.cell_key`.  Only
:func:`~app.routes.grid.page.index` calls them; the partials read the balance
seam, not the window.
"""

from typing import NamedTuple

from app.extensions import db
from app.models.transaction import Transaction
from app.services.cash_flow_set import (
    PlanItems,
    own_rows_clause,
    set_transfer_legs_in_periods,
)
from app.services.cash_ledger import (
    amounts_by_id,
    leg_amounts_by_key,
    settled_amounts_by_key,
)
from app.services.transaction_service import (
    leg_retained_amounts_by_key,
    retained_settle_amounts_by_id,
)
from app.services.transfer_legs import TransferLeg
from app.utils.amount_relationships import valuation_load_options


def load_grid_items(cash_flow, balance_ctx, all_periods) -> PlanItems:
    """Load the set's own rows and the legs of every transfer it shows.

    **The rows are the PAYCHECK's across the set -- checking and its cards --
    not one account's** (developer ruling ``credit_card:R-CC16``, plan step
    CC-4-1), and since leaf ``X-bi-6-1`` (ruling **R-BAL87**) a transfer is
    not among them as a row at all: the set's OWN rows are loaded through
    :func:`~app.services.cash_flow_set.own_rows_clause` -- every member's
    plan rows, no shadow -- and each transfer the set touches is drawn as a
    :class:`~app.services.transfer_legs.TransferLeg` read off the parent in
    ``budget.transfers``, from the side
    :func:`~app.services.cash_flow_set.leg_accounts_shown` names (ruling
    ``credit_card:R-CC23``: once, from the balance line's side, when both
    endpoints are members).  It was ``Transaction.account_id == account.id``,
    then the paycheck-rows clause that dropped only the far leg; the two
    loads below keep both properties (a balance line outside the set is a set
    of one) and stop reading the shadow rows.  ``cash_flow=None`` (the
    user-with-zero-accounts edge case) omits the account filter and loads no
    legs, so the result is naturally empty.

    **Two statements where there was one, both windowed by the same period
    ids**: the rows' ``pay_period_id IN (...)`` here and the transfers' in
    :func:`~app.services.cash_flow_set.set_transfer_legs_in_periods`, the
    one period-windowed transfer load every display reader shares since
    leaf ``X-bi-6-1b``.  The legs' records (a settled leg's covering
    movement) are one more load inside it.

    ``all_periods`` is the pass's reported window, every member of which is
    MATERIALISED -- so ``period_id`` is never ``None`` and the ``IN`` clause
    cannot be silently scoped by a null.

    Eager-loads the rows' ``entries`` (for entry-sum rendering) and
    ``template`` (for row-key generation); the transfers carry their pricing
    chain, and their endpoints (a leg's label and account chip read them)
    are ``lazy="joined"`` -- all read in the row-data helper and the cell
    template, so the loads avoid per-item N+1 queries in the grid render
    loop.

    Returns:
        The :class:`~app.services.cash_flow_set.PlanItems` for this render.
    """
    period_ids = [p.period_id for p in all_periods]
    txn_filters = [
        Transaction.pay_period_id.in_(period_ids),
        Transaction.scenario_id == balance_ctx.scenario_id,
        Transaction.is_deleted.is_(False),
    ]
    if cash_flow is not None:
        txn_filters.append(own_rows_clause(cash_flow))
    rows = (
        db.session.query(Transaction)
        # What a CONTRIBUTION pass reads, stated by the valuation rather than
        # copied here (plan step X-au-g-2c-2).  It subsumes the bare
        # ``selectinload(Transaction.template)`` this replaces.
        .options(*valuation_load_options())
        .filter(*txn_filters)
        .all()
    )
    legs: list[TransferLeg] = []
    if cash_flow is not None:
        legs = set_transfer_legs_in_periods(
            cash_flow, balance_ctx.scenario_id, period_ids,
        )
    return PlanItems.of(rows, legs)


class GridAmountMaps(NamedTuple):
    """The three per-cell amount maps a grid page publishes.

    Each holds every row by ``id`` and every leg by its ``cell_key`` (leaf
    ``X-bi-6-1``): one map per question, two shapes per map, and the
    template subscripts both with ``cell_key(t)``.

    Attributes:
        budgets: What each item's amount IS (its plan).
        settled: What each item's money DID, ``None`` until it has.
        retained: What a tick WOULD book where that differs from both.
    """

    budgets: dict
    settled: dict
    retained: dict


def build_amount_maps(items: PlanItems, basis) -> GridAmountMaps:
    """Build the three amount maps over the rows AND the legs.

    The ``settled`` map is ONE call over every item, each valued by its
    parent (ruling **plan#1983**); ``budgets`` and ``retained`` are each
    their row producer's answer merged with its leg producer's -- the twins
    ``cash_ledger`` and ``transaction_service`` publish side by side -- so a
    leg's cell and a row's read one context key each.  The keys cannot
    collide (an ``int`` and a tuple), which is what lets one dict hold both.

    Args:
        items: The page's :class:`~app.services.cash_flow_set.PlanItems`.
        basis: The read pass's amount basis (``balance_ctx.amounts()``).

    Returns:
        The :class:`GridAmountMaps` for this render.
    """
    return GridAmountMaps(
        budgets={
            **amounts_by_id(items.rows, basis),
            **leg_amounts_by_key(items.legs, basis),
        },
        settled=settled_amounts_by_key(items.items),
        retained={
            **retained_settle_amounts_by_id(items.rows),
            **leg_retained_amounts_by_key(items.legs),
        },
    )
