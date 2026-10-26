"""
Shekel Budget App -- Dashboard: the shared bill query and bill dict.

The ONE Projected-expense item load the dashboard's two bill surfaces
read (the still-due totals and the due-soon list, both in
:func:`~._pulse.compute_pulse_section`) -- the set's own rows and the
expense legs of its transfers since leaf ``balance:X-bi-6-1b`` -- plus the
render-ready bill dict those items become, with the E-21 single-base entry
progress.  Defined here once rather than per producer, so the item set, the
eager loads and the Projected / expense / not-deleted filter cannot drift
apart.

Pure aggregation -- no Flask imports, no database writes.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_flow_set import (
    CashFlowSet,
    PlanItems,
    own_rows_clause,
    set_transfer_legs_in_periods,
)
from app.services.entry_service import compute_entry_sums, compute_remaining
from app.services.transfer_legs import (
    PlanItem,
    TransferLeg,
    cell_key,
    expense_legs,
)
from app.utils.balance_predicates import is_projected_clause


# ── Shared bill query and render-ready bill dict ───────────────────


def _query_unpaid_expense_rows(
    cash_flow: CashFlowSet,
    scenario_id: int,
    period_ids: list[int],
) -> PlanItems:
    """Load the unpaid (Projected) expense items for a set of periods.

    The single load the dashboard's bill surfaces share -- the still-due
    totals and the due-soon list (both in
    :func:`~._pulse.compute_pulse_section`) -- so the item set, the
    eager-loads and the Projected / expense / not-deleted filter are defined
    exactly once rather than copied per producer (DRY).

    **The items are the PAYCHECK's across the owner's cash-flow set --
    checking and its cards -- not one account's** (developer ruling
    ``credit_card:R-CC16``, plan step CC-4-3), and since leaf
    ``balance:X-bi-6-1b`` (ruling **R-BAL86**) they are the set's OWN rows
    plus one LEG per transfer, read off the parent in ``budget.transfers``
    rather than off a shadow row: :func:`~app.services.cash_flow_set
    .own_rows_clause` selects every member's plan rows and no shadow, and
    :func:`~app.services.cash_flow_set.set_transfer_legs_in_periods` draws
    each transfer the set touches in these periods from the side
    :func:`~app.services.cash_flow_set.leg_accounts_shown` names (ruling
    ``R-CC23``: once, from the balance line's side, when both endpoints are
    members).  It was ``Transaction.account_id == account_id``, then the
    paycheck-rows clause that carried the near-side shadow in as an expense
    row; the two loads below keep both properties and neither selects a
    shadow row of its own (a settled leg's RECORD is reached through the one
    join in ``transfer_legs``, which walks the movement's side links since
    plan step ``balance:X-bi-6-4d-2`` re-parented it onto the transfer).

    **A transfer's EXPENSE leg is a bill; its income leg is not**
    (:func:`~app.services.transfer_legs.expense_legs`, the one spelling the
    Spending report shares).  The Gate B4b ruling read "a transfer-out shadow
    is an obligation the paycheck still owes on the member it leaves"; a leg
    states the same thing from its side rather than from a row's type: the
    from-side leg (money leaves) is kept, the to-side leg (money arrives)
    dropped.  A payment from the
    balance account to a card is therefore one obligation, on the balance
    line's side; a card -> checking transfer is none (the set shows it from
    the balance line, where it is an income leg); a transfer with one
    endpoint outside the set (card -> savings) shows from its member
    endpoint, as a checking -> savings transfer always has.

    selectinload(entries) + joinedload(template) avoid N+1 lookups when a
    consumer checks ``tracks_purchases`` or iterates purchases for the
    entries-aware still-due / progress computation; the transfer load
    states its own (the pricing chain, which includes the period a bill's
    ``period_start_date`` reads).  The Projected filter routes through the
    centralized ``is_projected_clause`` (D6-09 / MED-02) over BOTH tables so
    every SQL filter over Projected shares one definition with the Python
    ``is_projected`` predicate.

    Args:
        cash_flow: The owner's cash-flow set, whose members' items to load.
        scenario_id: The scenario the items belong to.
        period_ids: The pay period ids to load items for.  An empty list
            yields an empty result.

    Returns:
        The :class:`~app.services.cash_flow_set.PlanItems`: the matching
        :class:`Transaction` rows with ``category``, ``pay_period``,
        ``template`` and ``entries`` eager-loaded, and the expense legs of
        the matching transfers, records loaded.
    """
    if not period_ids:
        return PlanItems.of([], [])

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    rows = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            joinedload(Transaction.pay_period),
            joinedload(Transaction.template),
            selectinload(Transaction.entries),
        )
        .filter(
            own_rows_clause(cash_flow),
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            is_projected_clause(Transaction),
            Transaction.transaction_type_id == expense_type_id,
        )
        .all()
    )
    legs = expense_legs(set_transfer_legs_in_periods(
        cash_flow, scenario_id, period_ids, is_projected_clause(Transfer),
    ))
    return PlanItems.of(rows, legs)


# ``_is_entry_tracked`` used to live here and is DELETED (pay-calendar plan
# step C2-f2e).  It was ``return txn.tracks_purchases`` -- a pass-through in
# front of the model property whose OWN docstring calls itself "the single
# source of truth for the 'is this an envelope / entry-capable row?' question
# across services, routes, and templates".  Its stated purpose was to keep this
# module's two readers from re-deriving the check inline "and so drift apart",
# and that purpose was already false: ``_pulse._row_still_due`` prices the same
# rows and reads ``txn.tracks_purchases`` directly, so there were two spellings
# and the centralising one covered neither of the surfaces that could disagree.
# A second door in front of a single source of truth is not centralisation; it
# is one more thing that can be pointed somewhere else.  All three sites read
# the property now.


def txn_to_bill_dict(
    txn: PlanItem, today: date, contribution: Decimal, budget: Decimal,
) -> dict:
    """Build a bill dict for the dashboard bills template from a plan item.

    Used by :func:`~._pulse._due_soon` to produce one
    render-ready dict per due-soon bill.

    **The item is a row or a transfer leg** (leaf ``balance:X-bi-6-1b``),
    and every question below is one both shapes answer -- a leg reads its
    ``name``, ``due_date``, ``pay_period``, ``category`` and
    ``tracks_purchases`` off its parent, the last as ``False`` -- so there
    is no branch here on which it is.  Two keys are identity: ``id`` is
    :func:`~app.services.transfer_legs.cell_key` (a row's id, a leg's
    ``(transfer id, account id)``), and ``is_transfer`` says the item is a
    leg where it used to say the row carried a ``transfer_id``.

    Expects txn.template and txn.entries to be accessible -- callers
    dealing with collections should eager-load them via selectinload
    /joinedload to avoid N+1 queries.

    E-21 / MED-03 / F-028 / F-056: for entry-tracked (envelope) bills
    the ``amount`` field is set from the row's resolved BUDGET so it shares
    the same declared base as ``entry_remaining`` and
    ``entry_over_budget`` (built from the same figure in
    :func:`_entry_progress_fields`).  ``amount_base`` carries the
    label the template surfaces to the user ("budget") so the base is
    disclosed in the UI, not implicit.  Non-entry-tracked rows show what
    the row CONTRIBUTES; ``amount_base`` is None there so the template
    skips the label.

    **That contribution arrives as an ARGUMENT** (plan step X-au-c2).  It
    read ``txn.effective_amount``, a model property that could not answer
    for a row whose amount is DERIVED -- such a row stores no figure, and
    resolving a paycheck needs the owner's whole pay-period set, which no
    per-row property can hold.  The caller resolves the whole row set
    ONCE through :func:`app.services.cash_ledger.contributions_by_id` and
    indexes it here, so the paycheck engine runs once per read pass
    rather than once per bill (finding **N-228**).

    Args:
        txn: The row or transfer leg to convert.
        today: The reference date used to compute days_until_due.
        contribution: What this row contributes, from the caller's
            :func:`~app.services.cash_ledger.contributions_by_id` map --
            ``0`` for a row that contributes nothing, the entered
            figure a SETTLED row RECORDED as moved, else the row's resolved
            amount.  Read only for a non-entry-tracked row; an envelope
            answers on its E-21 budget base instead.
        budget: What the row's amount RESOLVES to, from the caller's
            :func:`~app.services.cash_ledger.amounts_by_id` map -- the E-21
            declared base.  An ARGUMENT rather than a read of
            ``txn.estimated_amount`` since plan step X-au-c2b, for the reason
            the contribution beside it is one: under the amount model a derived
            row stores no figure in that column, and this is the second of the
            two questions a caller must resolve for a whole row set at once.

    Returns:
        Dict matching the bills template contract, including the
        entry progress fields from _entry_progress_fields and the
        ``amount_base`` label that discloses which base the amount
        cell uses.
    """
    days_until = (txn.due_date - today).days if txn.due_date else None
    is_entry_tracked = txn.tracks_purchases
    if is_entry_tracked:
        amount = budget
        amount_base = "budget"
    else:
        amount = contribution
        amount_base = None
    bill = {
        "id": cell_key(txn),
        "name": txn.name,
        "amount": amount,
        "amount_base": amount_base,
        "due_date": txn.due_date,
        "period_start_date": txn.pay_period.start_date,
        "category_group": txn.category.group_name if txn.category else None,
        "category_item": txn.category.item_name if txn.category else None,
        "is_transfer": isinstance(txn, TransferLeg),
        "days_until_due": days_until,
    }
    bill.update(_entry_progress_fields(txn, budget))
    return bill


def _entry_progress_fields(txn: PlanItem, budget: Decimal) -> dict:
    """Build entry progress fields for a bill dict from a Transaction.

    Returns a dict with keys is_tracked, entry_total, entry_count,
    entry_remaining, and entry_over_budget.  When the transaction is
    not entry-capable (no template with is_envelope) or has no
    recorded entries, the progress fields are None/0/False and the
    dashboard template falls back to the standard amount display.
    Otherwise returns the debit+credit sum, the remaining budget,
    and a flag indicating whether the sum exceeds the estimated
    amount.

    Per E-21 / MED-03 / F-028 / F-056 the remaining and over-budget
    figures are computed against the row's RESOLVED amount -- the
    declared E-21 budget base -- so the row's three numbers (amount,
    remaining, over-budget) all share one base.  ``entry_remaining`` is the
    NET-basis figure :func:`~app.services.entry_service.compute_remaining`
    states, so it can EXCEED the base for a row whose refunds exceeded its
    purchases (developer ruling **bank_import:R-IK**, 2026-09-01); the reason is written once,
    there.  ``txn_to_bill_dict`` anchors the amount cell on the same base and
    passes it in; the template surfaces ``bill.amount_base`` to disclose it.

    Expects txn.template and txn.entries to already be loaded on the
    transaction object (eager-loaded by the caller).  Read over the row's
    PURCHASES (:attr:`~app.models.transaction.Transaction.purchases`, ruling
    **R-BAL68**), never the family: the seam's covering movement is not
    something the owner spent, and over ``entries`` a settled envelope
    closed at the door with no purchases read its own close as one.

    Args:
        txn: The row or transfer leg to inspect; a leg tracks no purchases.
        budget: The row's resolved amount -- the E-21 base, resolved once for
            the whole row set by the caller (plan step X-au-c2b).  It was read
            here as ``txn.estimated_amount``, the COLUMN a derived row does not
            carry.

    Returns:
        Dict with the five entry progress fields.
    """
    is_tracked = txn.tracks_purchases
    purchases = txn.purchases if is_tracked else []
    if not purchases:
        return {
            "is_tracked": is_tracked,
            "entry_total": None,
            "entry_count": 0,
            "entry_remaining": None,
            "entry_over_budget": False,
            "entry_over_budget_amount": None,
        }

    debit, credit = compute_entry_sums(purchases)
    total = debit + credit
    remaining = compute_remaining(budget, purchases)
    # **ONE spelling of over-budget** (plan step ``bank_import:X-gj-2b-3``).
    # It read ``total > budget`` while ``entry_service._sums`` asks
    # ``remaining < 0`` for the same row on the grid -- two statements of one
    # rule that agree only because ``remaining`` IS ``budget - total``.  Asking
    # the figure this function already computed makes them one rule rather than
    # two that reconcile by hand, which is what kept the grid cell and the
    # dashboard bill row in step by luck.
    over_budget = remaining < Decimal("0")
    # Templates display, never compute (coding-standards): the
    # over-budget overage is the positive dollar amount by which the
    # entries exceed the declared budget base.  Computing it here keeps
    # the ``|abs`` arithmetic out of the bill-row template, where it
    # previously lived.  ``None`` when the row is not over budget so the
    # template renders the "remaining" branch instead.
    over_budget_amount = total - budget if over_budget else None
    return {
        "is_tracked": True,
        "entry_total": total,
        "entry_count": len(purchases),
        "entry_remaining": remaining,
        "entry_over_budget": over_budget,
        "entry_over_budget_amount": over_budget_amount,
    }
