"""Estimate surprises: the settled rows whose actual differed from the plan.

The one real signal the retired Variance tab carried, reused here through the
shared :func:`app.services.spending_analysis.resolved_actual_amount` kernel so
"what did this row actually cost" has one definition across the analytics
surfaces.

Boundary discipline: no Flask import, no query -- it reduces rows the window
module already loaded.  All money is ``Decimal``.
"""

from app.services import spending_analysis
from app.services.cash_ledger import AmountBasis, resolve_amount
from app.services.transfer_legs import PlanItem, cell_key, key_order, parent_of
from app.utils.money import ZERO

from ._types import Surprise, Surprises

# Longest surprises list the rail shows, the ranked-rail top-N convention.
_MAX_SURPRISES = 5


def _build_surprises(
    txns: list[PlanItem], basis: AmountBasis,
) -> Surprises:
    """Build the estimate-surprises list and its net over the window.

    A surprise is a settled row whose resolved actual (via the shared
    :func:`spending_analysis.resolved_actual_amount` kernel) differs from its
    estimate.  The list is ranked by ``abs(delta)`` descending and capped at
    :data:`_MAX_SURPRISES`; the net sums EVERY surprise's delta so the
    headline reflects the whole window, not just the shown rows.

    Args:
        txns: The window's settled expense items -- rows and transfer legs
            (leaf ``balance:X-bi-6-1b``).
        basis: The read pass's :class:`~app.services.cash_ledger.AmountBasis`,
            built once by the caller.  It is REQUIRED rather than optional
            because a row whose plan is derived cannot be priced without one,
            and an optional basis would put the refusal back one branch later.
            **BOTH terms take it since plan step X-bu** -- the actual half as
            well as the estimate -- which is what makes an unsettled row's
            delta zero by construction rather than by two producers agreeing.

    Returns:
        The :class:`Surprises` (capped rows + full net).
    """
    surprises: list[Surprise] = []
    net = ZERO
    for txn in txns:
        # Both halves are questions about the item's PARENT (ruling
        # plan#1983): a row is its own, a transfer leg's is its transfer.
        parent = parent_of(txn)
        actual = spending_analysis.resolved_actual_amount(parent, basis)
        # The ESTIMATE half, through the amount model's ONE plan producer.
        # It read ``owned_amount``, the cheap accessor that answered a row's own
        # ``estimated_amount`` column and REFUSED a row whose plan is derived --
        # chosen deliberately as a tripwire, so that the first cutover to point
        # a derived row at this list would fail loudly rather than subtract a
        # ``None``.  That tripwire FIRED: the amount-source cutovers declared
        # 934 rows derived, 116 of them settled, and this list met one on real
        # data.  Routing the reader to ``resolve_transaction_amount`` is what
        # the tripwire was for.
        #
        # **THE ACCESSOR ITSELF IS GONE as of plan step X-bu**, and that is why
        # this comment is the record of a fixed defect rather than a warning
        # about a live one.  An earlier draft ended "the refusal stays where it
        # belongs, in ``owned_amount``, for the readers that genuinely own their
        # rows" -- true when written, and the reason finding **BAL-462** ruled
        # the remedy DELETE THE ACCESSOR: a public spelling of "what is this
        # row's plan" that answers from the column is a second producer whoever
        # reaches for it next.  The refusal survives inside rule 1's own arm as
        # ``cash_ledger._amount_source._own_figure``, which takes the column as
        # an ARGUMENT; plan step X-bx moved it there from ``row_valuation`` and
        # made it PRIVATE, once that step's deletion left both of its callers
        # inside the amount model.
        #
        # **THE LINE ABOVE TOOK THE SAME ROUTE AT THAT STEP**, and it is the
        # half this fix did not reach: ``resolved_actual_amount``'s fall-through
        # still read the column, so an UNSETTLED derived row raised there while
        # this line resolved -- BAL-462 on the other population, held off only
        # by ``query_settled_expenses``' status filter.  Two adversarial reviews
        # measured it and the developer superseded BAL-462's "that reader is
        # correct and stays" clause.  Both terms are now one call on one row
        # against one basis, so an unsettled row is skipped rather than
        # compared.
        #
        # It reproduces what the cutovers emptied rather than re-pricing.
        # Measured against the pre-cutover figures in ``system.audit_log`` on a
        # production clone, 2026-09-05, by
        # ``tests/manual/measure_settled_derived_plan_reproduction.py``: of the
        # 116 settled rows the cutovers declared, the 78 EXPENSE ones -- 59
        # template-priced and 19 transfer shadows -- ALL reproduce their stored
        # plan, with no refusals.  The seven that differ are salary INCOME,
        # which neither query feeding this list can return (both filter
        # ``transaction_type_id`` to the expense type), and they differ because
        # ``salary.calibration_overrides`` is REPLACED in place rather than
        # effective-dated, so a past paycheck re-derives under today's
        # calibration -- findings **N-441** and **N-535**, owned by salary:S1.
        # (Measured while the calibration priced paychecks; since plan step
        # salary:S11-c-2c the dated pay stubs do.)
        #
        # *The headline was WRONG in a first draft of this comment, which said
        # "109 of 109 settled EXPENSE rows".  109 was the count over every
        # settled declared row, income included, and stating it as an expense
        # denominator inflated the population this reader actually sees by
        # every income row that happened to agree.  An adversarial review
        # caught it by reconciling against the cutovers' own censuses.  The
        # probe now partitions by rule and by type, which is why it is a
        # committed artifact rather than a number.*
        #
        # A back-dated price version can still move an ALREADY-SETTLED row's
        # resolved plan -- the template form's "Amount effective from" accepts a
        # past date -- so this is a measurement of today's data, not an
        # invariant the schema holds.
        #
        # It is deliberately not ``settled_contribution``: that answers what
        # the parent RECORDED as having moved, which is the ACTUAL half above,
        # so reading it here would make every surprise's delta zero by
        # construction.  ``resolve_amount`` is the ONE branch on the parent's
        # table for a plan; ``planned_spend``, the router that sent a row and
        # a leg to their plan producers (leaf X-bi-6-1b), is deleted.
        estimated = resolve_amount(parent, basis)
        delta = actual - estimated
        if delta == ZERO:
            continue
        group_name, item_name = spending_analysis.category_names(txn)
        surprises.append(Surprise(
            item_key=cell_key(txn),
            name=txn.name,
            group_name=group_name,
            item_name=item_name,
            estimated=estimated,
            actual=actual,
            delta=delta,
        ))
        net += delta

    # **The worst of the three unstable ranks, because a CAP follows it**
    # (finding **P74**, developer ruling 2026-08-25).  Keyed on ``abs(delta)``
    # alone, two rows that missed their estimate by the same amount were
    # separated only by the order ``query_settled_expenses`` happened to
    # return them in -- which carries no ``ORDER BY`` -- so at the boundary the
    # database decided WHICH ROW IS ON THE SCREEN, not merely in what order.
    # ``item_key`` is the item's identity, so the five shown are now a
    # function of the data -- through ``key_order``, the one total order over
    # a row's id and a leg's pair, since a leg joined the list (X-bi-6-1b).
    # ``net`` is unaffected either way: it sums every surprise, not the shown
    # ones.
    surprises.sort(key=lambda s: (-abs(s.delta), key_order(s.item_key)))
    return Surprises(rows=surprises[:_MAX_SURPRISES], net=net)
