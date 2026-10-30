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
does this render draw" -- one list of plan items, rows then transfer sides,
from the one loader every reader of the set's plan items calls since plan
step **plan#2130** -- and :func:`build_amount_maps` answers "what does each
item's cell say its amount IS, what its money DID, and what a tick WOULD
book" -- each map holding both shapes under
:func:`~app.services.transfer_legs.cell_key`.  Only
:func:`~app.routes.grid.page.index` calls them; the partials read the balance
seam, not the window.
"""

from typing import NamedTuple

from app.services.cash_flow_set import (
    Paychecks,
    PlanScope,
    StatusBand,
    load_plan_items,
)
from app.services.cash_ledger import (
    amounts_by_key,
    settled_amounts_by_key,
)
from app.services.transaction_service import retained_amounts_by_key
from app.services.transfer_legs import PlanItem
from app.utils.amount_relationships import valuation_load_options


def load_grid_items(cash_flow, balance_ctx, all_periods) -> list[PlanItem]:
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
    then the paycheck-rows clause that dropped only the far leg; the loader
    keeps both properties (a balance line outside the set is a set of one)
    and reads no shadow row.

    **The grid's scope, stated once** (plan step **plan#2130**): the set, the
    pass's scenario, the paychecks of ``all_periods``, every status (a cell
    decides what it shows, a cancelled row's included) and every type, which
    :func:`~app.services.cash_flow_set.load_plan_items` turns into the rows'
    query and the transfers' alike.  The rows come in id order then the
    sides (finding **plan#2186**): a cell holding two rows -- a
    carried-forward row beside its period's own -- lists them in that order
    on every render, where it listed them in whatever order the planner
    returned.  ``cash_flow=None`` (the user-with-zero-accounts edge case)
    loads nothing: it ran the row query with no account filter at all until
    that step, an owner scope missing from a query, which answered nothing
    only because such an owner can hold no row.

    ``all_periods`` is the pass's reported window, every member of which is
    MATERIALISED -- so ``period_id`` is never ``None`` and the ``IN`` clause
    cannot be silently scoped by a null.

    Eager-loads what a CONTRIBUTION pass reads, stated by the valuation
    rather than copied here (plan step X-au-g-2c-2): the rows' ``entries``
    (for entry-sum rendering) and pricing chain, ``template`` among it (for
    row-key generation); the transfers carry their pricing chain, and their
    endpoints (a leg's label and account chip read them) are
    ``lazy="joined"`` -- all read in the row-data helper and the cell
    template, so the loads avoid per-item N+1 queries in the grid render
    loop.

    Returns:
        This render's plan items: the rows, then the transfer sides.
    """
    # Read before the no-account answer, so a missing baseline raises here
    # for every owner (``_resolve_grid_context`` names this as where it does).
    scenario_id = balance_ctx.scenario_id
    if cash_flow is None:
        return []
    return load_plan_items(
        PlanScope(
            cash_flow=cash_flow,
            scenario_id=scenario_id,
            window=Paychecks(tuple(p.period_id for p in all_periods)),
            band=StatusBand.EVERY,
            expenses_only=False,
        ),
        valuation_load_options(),
    )


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


def build_amount_maps(items: list[PlanItem], basis) -> GridAmountMaps:
    """Build the three amount maps over the rows AND the legs.

    Each map is ONE call over every item, keyed by the side and valued by
    its parent (ruling **plan#1983**), so a leg's cell and a row's read one
    context key each and both legs of one transfer read one figure.  The
    keys cannot collide (an ``int`` and a tuple), which is what lets one
    dict hold both.  ``retained`` merged a row producer's answer with its
    leg twin's until plan#1987 folded the two.

    Args:
        items: The page's plan items, :func:`load_grid_items`'s list.
        basis: The read pass's amount basis (``balance_ctx.amounts()``).

    Returns:
        The :class:`GridAmountMaps` for this render.
    """
    return GridAmountMaps(
        budgets=amounts_by_key(items, basis),
        settled=settled_amounts_by_key(items),
        retained=retained_amounts_by_key(items),
    )
