"""Read-only carry-forward preview: plan vocabulary, DTOs, and builders.

``preview_carry_forward`` mirrors ``carry_forward_unpaid``'s decision
tree without mutating a single row, returning one ``CarryForwardPlan``
per source row so the carry-forward modal can show the user exactly what
would happen before any database writes -- including the bank lines the
batch would leave unexplained again (plan step ``credit_card:CC-5-4a-5``,
leaf 5c-2c-2, rulings **R-CC76** and **R-CC135**), which the modal's Confirm
posts back for the batch's one check.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import List, Optional

from app.models.transaction import Transaction
from app.services.cash_ledger import (
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from app.services.match_withdrawal import (
    MatchWithdrawal,
    pending_alone_and_together,
)
from app.services.one_off import due_date_for
from app.services.row_valuation import purchases_total
from app.services.transfer_legs import PlanItem, leg_of

from ._context import (
    _build_carry_forward_context,
    _classify_leftover_target,
    _TargetKind,
)


# ── Plan kinds ───────────────────────────────────────────────────────

# String constants for ``CarryForwardPlan.kind``.  Defined as module
# constants (rather than an Enum) so the route layer can compare
# directly against ``plan.kind`` without an extra import in the
# template's macro context.
PLAN_KIND_ENVELOPE = "envelope"
PLAN_KIND_DISCRETE = "discrete"
PLAN_KIND_TRANSFER = "transfer"

# String constants for ``CarryForwardPlan.block_reason_code``.  The AMBIGUOUS
# guard is a destination period with more than one mutable row for the same
# (template, scenario), a corrupt pre-existing state.  Using a code (not a raw
# string) lets tests assert the failure type without coupling to wording.
BLOCK_AMBIGUOUS_TARGETS = "ambiguous_targets"
# A rule-less definition's row in the target has finalised, or a row of it
# already answers the target's start, so the leftover has no row to roll into
# and none may be placed (ruling **R-BAL44**; ``_context._TargetKind.CLOSED``
# carries the argument).
BLOCK_CLOSED_TARGET = "closed_target"


# ── Plan dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SharedClose:
    """One accepted match that only several envelopes' closes, made together, withdraw.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-2), ruling **R-CC135**
    (developer 2026-10-04, "One check per save"): *"Carry-forward's Confirm
    is one save of every envelope and names each match once."*  A match
    naming the kept payments of two envelopes in the batch is emptied only
    when both close, so neither close frees its line alone
    (``match_withdrawal.RemovalWithdrawals.alone``); every envelope closes in
    the one save, so the match is withdrawn, and the modal names it ONCE, in
    the panel's sentence (``_withdrawal_macros.shared_match``: *"Matched with
    Dining to one bank line: closing both from their purchases withdraws that
    match, so ... is unexplained again on your statement screen."*), under
    the first of its envelopes the modal lists.

    Attributes:
        withdrawal: What withdrawing the match frees -- one act.
        partners: The names of the OTHER envelopes it needs, in the order the
            modal lists them.  A name alone, with no paycheck beside it:
            every envelope in the batch is in the one paycheck the modal is
            titled with.
    """

    withdrawal: MatchWithdrawal
    partners: "tuple[str, ...]"


@dataclass(frozen=True)
class CarryForwardPlan:  # pylint: disable=too-many-instance-attributes
    """One row's planned action under ``carry_forward_unpaid``.

    Attributes:
        item: What the plan moves, held by reference so the modal renders its
            ``name`` without a re-query: the source ROW for an envelope or
            discrete plan, and for a transfer plan the transfer's FROM-side
            :class:`~app.services.transfer_legs.TransferLeg` -- its label
            ("Transfer to <account>", composed from the endpoints' current
            names) is the plan's, and its ``transfer`` is what moves.  It was
            ``transaction``, holding whichever of the transfer's two SHADOW
            rows an unordered query returned first, until plan step
            balance:X-bi-6-4c-2 (finding **BAL-546**: a transfer plan read
            "Transfer to Savings" or "Transfer from Checking" by chance).
        budget: What the row's amount RESOLVES to, carried for EVERY kind
            because all three modal sentences name it.  The template read
            ``plan.transaction.estimated_amount`` until plan step X-au-c2b --
            the COLUMN, which a derived row does not carry, and which the
            preview would then render blank while the execution rolled a real
            figure.  Resolved once per request against ``ctx.basis``.
        kind: One of ``PLAN_KIND_ENVELOPE`` / ``PLAN_KIND_DISCRETE`` /
            ``PLAN_KIND_TRANSFER``.  Drives the action-label rendering
            and is the partition the mutating service uses too.
        blocked: True iff ``carry_forward_unpaid`` would refuse this
            row (raising ``ValidationError`` and aborting the batch).
            Only envelope plans can be blocked; discrete and transfer
            plans are always actionable.
        block_reason_code: Stable identifier for the block category
            when ``blocked`` is True; ``None`` otherwise.  See the
            ``BLOCK_*`` module constants.
        block_reason: Human-readable explanation of the block, naming
            the source row and target period; ``None`` otherwise.
        entries_sum: Envelope-only.  ``sum(entries)`` of the source row
            -- the actual amount the source will settle at.  ``None``
            for non-envelope plans.
        leftover: Envelope-only.  ``max(0, estimated - entries_sum)``
            -- the amount that would roll forward.  ``None`` for
            non-envelope plans.
        target_estimated_before: Envelope-only.  The target row's
            estimated amount (or the engine-generated default) BEFORE
            the bump.  ``None`` when blocked or when no rollover is
            needed (``leftover == 0``).
        target_estimated_after: Envelope-only.  The target row's
            estimated amount AFTER the bump.  ``None`` when blocked
            or when no rollover is needed.
        target_will_be_generated: Envelope-only.  True when a new
            destination row will appear -- either the recurrence engine
            generates the canonical (empty, active-template period) or a
            fresh override row is created to carry the leftover (inactive
            template, finalised-only or soft-deleted-only destination).
            ``False`` when an existing mutable row is bumped in place.
        withdraws: Envelope-only.  The accepted matches this envelope's
            close withdraws BY ITSELF, and the bank lines that leaves
            unexplained again (plan step ``credit_card:CC-5-4a-5``, leaf
            5c-2c-2, ruling **R-CC76**: *"carry forward's confirmation for
            each envelope it would settle"*): the close takes the payment a
            revert kept off the books (``status_seam._covering._withdraw``),
            and any match left naming no row goes with it.  ``None`` for an
            envelope holding no such payment, and for every other kind.
        shared: Envelope-only.  The matches only this envelope's close and
            others' in the batch withdraw together, named here because this
            is the first of their envelopes the modal lists (ruling
            **R-CC135**, :class:`SharedClose`); empty everywhere else.

    Pylint: ``too-many-instance-attributes`` (13/7) -- this is a cohesive
    value record -- one source row's planned carry-forward action -- read
    flat by its sole consumer, the carry-forward preview modal, which
    iterates ``preview.plans`` and renders one list item per plan.  The
    block metadata and the envelope rollover numbers are not read as
    separable units: the modal interleaves the rollover figures within a
    single rendered sentence and gates list-item styling on ``blocked``
    apart from rendering ``block_reason``, and prints the two withdrawal
    fields under the same item.  Every field is an irreducible column of the
    row; splitting it would fragment one domain concept for no design gain.
    """

    item: PlanItem
    kind: str
    budget: Decimal
    blocked: bool = False
    block_reason_code: Optional[str] = None
    block_reason: Optional[str] = None
    entries_sum: Optional[Decimal] = None
    leftover: Optional[Decimal] = None
    target_estimated_before: Optional[Decimal] = None
    target_estimated_after: Optional[Decimal] = None
    target_will_be_generated: bool = False
    withdraws: Optional[MatchWithdrawal] = None
    shared: "tuple[SharedClose, ...]" = ()


@dataclass(frozen=True)
class CarryForwardPreview:
    """Aggregate view of a carry-forward batch as it would execute.

    Read-only output of ``preview_carry_forward``.  ``plans`` ordering
    is stable and matches the partition order the mutating service
    uses internally (envelope -> discrete -> transfer) so the UI
    presents the most consequential rows first (envelope rollovers
    are the only ones that can block the batch).
    """

    source_period: object  # DerivedPeriod -- forward-declared, no import cycle.
    target_period: object  # DerivedPeriod
    plans: List[CarryForwardPlan]

    @property
    def any_blocked(self) -> bool:
        """True if any plan would refuse the batch.

        The mutating service is atomic on a per-batch basis: a single
        blocked envelope plan rolls back every other plan's
        mutations.  The modal disables Confirm whenever this is True
        so the user fixes the offending row first.
        """
        return any(p.blocked for p in self.plans)

    @property
    def envelope_count(self) -> int:
        """Number of actionable (non-blocked) envelope plans."""
        return sum(
            1 for p in self.plans
            if p.kind == PLAN_KIND_ENVELOPE and not p.blocked
        )

    @property
    def discrete_count(self) -> int:
        """Number of actionable (non-blocked) discrete plans."""
        return sum(
            1 for p in self.plans
            if p.kind == PLAN_KIND_DISCRETE and not p.blocked
        )

    @property
    def transfer_count(self) -> int:
        """Number of actionable (non-blocked) transfer plans."""
        return sum(
            1 for p in self.plans
            if p.kind == PLAN_KIND_TRANSFER and not p.blocked
        )

    @property
    def blocked_count(self) -> int:
        """Total number of blocked plans (always envelope rows)."""
        return sum(1 for p in self.plans if p.blocked)

    @property
    def named_line_ids(self) -> "frozenset[int]":
        """Every bank line the modal names, which its Confirm posts back.

        Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-2), rulings
        **R-CC127** and **R-CC135**: the batch is ONE save of every envelope,
        so what the page named is one set -- each envelope's own lines and
        each shared match's -- and the save's press compares it, whole, with
        what the batch frees (``carry_forward_unpaid``).  Empty when no close
        frees a line, which the Confirm posts as a page that named nothing.
        """
        return frozenset().union(
            *(p.withdraws.line_ids for p in self.plans if p.withdraws),
            *(
                shared.withdrawal.line_ids
                for p in self.plans for shared in p.shared
            ),
        )


def preview_carry_forward(
    source_period_id: int,
    target_period_id: int,
    scenario_id: int,
    *,
    balance_ctx,
) -> CarryForwardPreview:
    """Return a read-only preview of a planned carry-forward batch.

    Mirrors ``carry_forward_unpaid``'s decision tree without mutating
    a single row -- the carry-forward modal route renders the result
    so the user confirms before any database writes happen (Phase 5
    of ``docs/carry-forward-aftermath-implementation-plan.md``).

    Returns one ``CarryForwardPlan`` per source row or transfer, partitioned
    and ordered the same way ``carry_forward_unpaid`` would process them
    (envelope rollovers first because they are the only ones that
    can block the batch; then discrete defers; then transfer moves, one per
    transfer in id order).  Each plan carries enough
    structured data (entries_sum, leftover, target_estimated_after,
    block_reason_code) for the modal template to render the action
    label without re-deriving any business logic.

    The preview's ``any_blocked`` flag drives the Confirm button in
    the modal: when True, the carry-forward POST would refuse, so the
    Confirm button is disabled until the user resolves the offending
    rows.

    Args:
        source_period_id: pay_period.id to carry forward FROM.
        target_period_id: pay_period.id to carry forward TO.
            Typically the user's current period; the route resolves
            it the same way the mutating route does.
        scenario_id: scenario filter; mirrors the mutating path so
            preview and execution see the same set of rows.
        balance_ctx: The request's
            :class:`~app.services.balance_at.BalanceContext`, opened once by
            the route (pay-calendar plan step C2-f3c; plan step R7d-c-1 moved
            it from the calendar to the pass that derives one).  It names the
            owner and its calendar answers both period lookups, so a period
            that is not this owner's is simply not found.

    Returns:
        CarryForwardPreview.  Empty plans list when source == target
        or there are no projected rows in the source period.

    Raises:
        NotFoundError: if either period is not in *balance_ctx*'s calendar
            -- it is missing, or it is not this owner's.

    Side effects:
        None.  All database access is read-only and no session
        mutations are made -- the modal route can re-issue the
        preview as many times as the user clicks the button without
        any persisted side effects.
    """
    ctx = _build_carry_forward_context(
        source_period_id, target_period_id, scenario_id, balance_ctx,
    )

    plans: List[CarryForwardPlan] = []

    # Envelope rollovers first: they are the only kind that can block
    # the batch, so showing them at the top of the modal puts the
    # actionable failure cases in front of the user.  Each says what its
    # close frees, from ONE read for the batch (leaf 5c-2c-2).
    frees = _what_each_close_frees(ctx.envelope_txns)
    for txn in ctx.envelope_txns:
        withdraws, shared = frees.get(txn.id, (None, ()))
        plans.append(replace(
            _build_envelope_plan(
                txn, ctx.target_period, ctx.basis, ctx.schedule,
            ),
            withdraws=withdraws, shared=shared,
        ))

    for txn in ctx.discrete_txns:
        plans.append(_build_discrete_plan(txn, ctx.basis))

    for transfer in ctx.transfers:
        plans.append(_build_transfer_plan(transfer, ctx.basis))

    return CarryForwardPreview(
        source_period=ctx.source_period,
        target_period=ctx.target_period,
        plans=plans,
    )


def _what_each_close_frees(
    envelope_txns: "list[Transaction]",
) -> "dict[int, tuple[MatchWithdrawal, tuple[SharedClose, ...]]]":
    """Return what each envelope's close withdraws, as plan fields, from ONE read.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-2), rulings **R-CC76**
    and **R-CC135**.  Every envelope the batch carries settles from its
    purchases (``transaction_service.settle_from_entries``), and that
    ``purchases`` record takes off the books whatever payment a revert kept
    (``status_seam._covering._withdraw``, over
    :attr:`~app.models.transaction.Transaction.covering_movements`) -- so
    each close's removal is exactly those movements, read through the act's
    own twin, ``match_withdrawal.pending_alone_and_together``, once for the
    batch: one member query, and none at all when no envelope holds such a
    payment (production held 0 un-dated covering movements under a
    Projected row on 2026-09-30, the census behind ruling **R-CC125**).

    A match one close empties by itself is that envelope's ``withdraws``.
    A match only several closes empty together is ONE :class:`SharedClose`
    under the first of them in *envelope_txns*' order -- the modal's -- since
    every envelope closes in the one save (*"names each match once"*).  A
    match naming a payment no close takes -- a discrete row's, which moves
    whole, or a row outside the batch -- is neither: the batch only takes
    its envelopes' payments out of it, and it stays.

    Args:
        envelope_txns: The batch's envelope rows, in the order the modal
            lists them (``_build_carry_forward_context``).

    Returns:
        ``{transaction id: (withdraws, shared)}`` -- the plan's two fields --
        for each envelope holding a kept payment; an envelope absent here
        frees nothing and keeps the plan's defaults.
    """
    pending = pending_alone_and_together({
        txn.id: txn.covering_movements
        for txn in envelope_txns if txn.covering_movements
    })
    order = [txn.id for txn in envelope_txns]
    names = {txn.id: txn.name for txn in envelope_txns}
    frees = {}
    for txn_id, each in pending.items():
        frees[txn_id] = (each.alone, tuple(
            SharedClose(
                withdrawal=shared.withdrawal,
                partners=tuple(
                    names[key] for key in order
                    if key in shared.keys and key != txn_id
                ),
            )
            for shared in each.shared
            if min(shared.keys, key=order.index) == txn_id
        ))
    return frees


def _build_envelope_plan(source_txn, target_period, basis, schedule):
    """Compute the envelope-rollover plan for *source_txn*.

    Shares the destination decision with the mutating path through
    ``_classify_leftover_target``: the single ``AMBIGUOUS`` outcome
    surfaces here as ``blocked=True`` with a stable ``block_reason_code``,
    and every other outcome produces an actionable plan with the matching
    ``target_estimated_after`` the bump would land on.

    Read-only -- the shared classifier queries the database for target
    rows but never mutates and never invokes
    ``recurrence_engine.generate_for_template`` (which would create rows);
    it uses ``recurrence_engine.can_generate_in_period`` instead to
    predict the engine's create-or-skip decision.

    The destination decision lives in
    ``_resolve_envelope_target_fields`` so this function has a
    single return point and the dataclass is constructed exactly
    once -- making it harder for a future edit to forget a field
    on one branch.
    """
    # The PURCHASES, never the family (ruling R-BAL68): a reverted manual
    # close's kept movement is not spend, so the whole budget rolls.
    entries_sum = purchases_total(source_txn.purchases)
    # The source's BUDGET, resolved rather than read off the column, and the
    # SAME expression the mutating path uses (plan step X-au-c2b): a preview
    # that predicted a leftover from a different base than the execution rolls
    # would be exactly the drift ``_classify_leftover_target`` exists to
    # prevent, one term further out.
    budget = resolve_transaction_amount(source_txn, basis)
    # **The floor is against an OVERSPENT envelope and never against a
    # refunded one** (developer ruling **bank_import:R-IK**, 2026-09-01, plan step
    # ``bank_import:X-gj-2b-3``).  Ruling **bank_import:R-II** made a merchant
    # credit a NEGATIVE purchase, so ``entries_sum`` can be negative and this
    # leftover can EXCEED the budget: an envelope budgeting `$100.00` holding
    # one `-$50.00` refund rolls `$150.00` forward and settles at `-$50.00`,
    # which the two together conserve -- `$100.00` of plan across the two
    # periods.  That is the ruled NET basis and it is what
    # ``entry_service.compute_remaining`` answers for the same row on screen;
    # capping it at the budget was put to the developer with those numbers and
    # refused, because it would make the rollover disagree with the figure the
    # owner reads beside it.
    leftover = max(Decimal("0"), budget - entries_sum)
    target_fields = _resolve_envelope_target_fields(
        source_txn, target_period, basis, leftover, schedule,
    )
    return CarryForwardPlan(
        item=source_txn,
        kind=PLAN_KIND_ENVELOPE,
        budget=budget,
        entries_sum=entries_sum,
        leftover=leftover,
        **target_fields,
    )


def _resolve_envelope_target_fields(source_txn, target_period,
                                    basis, leftover, schedule):
    """Decide the target-row half of an envelope plan.

    Returns a dict of ``CarryForwardPlan`` fields covering the
    target-row decisions only -- ``blocked``, ``block_reason_code``,
    ``block_reason``, ``target_estimated_before``,
    ``target_estimated_after``, ``target_will_be_generated``.  The
    caller (``_build_envelope_plan``) supplies the remaining fields
    (``item``, ``kind``, ``entries_sum``, ``leftover``).

    A thin switch over ``_classify_leftover_target`` -- the same
    read-only decision the mutating path acts on -- so the preview can
    never predict an outcome execution would not take:

      * ``leftover == 0`` -> empty dict (the mutating path settles the
        source and never touches the destination).
      * ``AMBIGUOUS`` -> blocked; more than one mutable destination row
        is a corrupt state the user resolves manually.
      * ``TOP_UP`` / ``GENERATE`` / ``CREATE`` -> actionable.  The
        destination starts at ``resolution.base`` (the existing row's
        estimate, the template default the engine would generate, or
        ``Decimal("0")`` for a freshly created row) and the rollover
        bumps it to ``base + leftover``.  ``target_will_be_generated``
        flags the three cases where a new row appears (engine-generated
        canonical, fresh override row, or a rule-less definition's row
        placed in the target, ruling **R-BAL44**) so the modal can word it
        as a creation rather than a bump.
      * ``CLOSED`` -> blocked; a rule-less definition's row in the target
        has finalised, or a row of it already answers the target's start.
    """
    if leftover == Decimal("0"):
        # Overspend / exact-spend: the mutating path settles source
        # and never touches the target.  No target row inspection
        # needed; fall through with all target_* fields at default.
        return {}

    resolution = _classify_leftover_target(
        source_txn, target_period, basis, schedule,
    )

    if resolution.kind is _TargetKind.AMBIGUOUS:
        return {
            "blocked": True,
            "block_reason_code": BLOCK_AMBIGUOUS_TARGETS,
            "block_reason": (
                f"Target period {target_period.label} has more than one "
                f"open row for this template.  Resolve the duplicate "
                f"rows manually before retrying."
            ),
        }
    if resolution.kind is _TargetKind.CLOSED:
        return {
            "blocked": True,
            "block_reason_code": BLOCK_CLOSED_TARGET,
            "block_reason": (
                f"This envelope's row in {target_period.label} has already "
                f"closed, so its unspent budget has nowhere to roll.  Add "
                f"the unspent amount to a later paycheck's envelope yourself."
            ) if resolution.row is not None else (
                f"A row of this envelope is already due on "
                f"{due_date_for(None, target_period).isoformat()}, the day "
                f"its unspent budget would be placed on, so it has nowhere "
                f"to roll.  Move that row's due date, or add the unspent "
                f"amount to a later paycheck's envelope yourself."
            ),
        }

    return {
        "target_estimated_before": resolution.base,
        "target_estimated_after": resolution.base + leftover,
        "target_will_be_generated": resolution.kind in (
            _TargetKind.GENERATE, _TargetKind.CREATE,
        ),
    }


def _build_discrete_plan(source_txn, basis):
    """Plan for a discrete (non-envelope) row: defer whole.

    Discrete carry-forward never blocks: the relaxed partial unique
    index allows the moved row to coexist with any rule-generated
    sibling in the target period, and ad-hoc rows have no template
    constraint at all.

    Args:
        source_txn: The row that would move whole.
        basis: The request's amount basis, for the figure the modal names.
    """
    return CarryForwardPlan(
        item=source_txn,
        kind=PLAN_KIND_DISCRETE,
        budget=resolve_transaction_amount(source_txn, basis),
        blocked=False,
    )


def _build_transfer_plan(transfer, basis):
    """Plan for a transfer: move whole.

    The mutating path delegates to ``transfer_service.update_transfer``
    which moves the parent and both legs together.  No block
    conditions exist in the carry-forward usage of that service
    (target period ownership and is_override are both already
    validated upstream).

    **The subject is the TRANSFER since plan step balance:X-bi-6-4c-2**, and
    two things follow.  Its figure is ``resolve_transfer_amount`` over the
    parent, which is what ``resolve_transaction_amount`` answered for either
    shadow on every door-written state: a derived shadow delegated to exactly
    that (``cash_ledger``'s amount rule 5), and an owner-priced one held an
    OWN copy of the parent's figure that ``transfer_service._amount`` kept
    equal (Transfer Invariant 3) -- so the figure did not move (the
    2026-09-25 clone grade replayed 95 transfer plans: every figure equal).
    Its label is its FROM side's
    (:attr:`~app.services.transfer_legs.TransferLeg.name`, "Transfer to
    <account>"), stated here where it was whichever shadow the context's
    unordered query returned first (finding **BAL-546**, a DECLARED display
    change): money leaving is what a carry-forward confirms, as the
    dashboard's bills and the Spending report draw a transfer from the side
    its money leaves (``transfer_legs.expense_legs``).

    Args:
        transfer: The transfer that would move.
        basis: The request's amount basis, for the figure the modal names.
    """
    return CarryForwardPlan(
        item=leg_of(transfer, transfer.from_account_id),
        kind=PLAN_KIND_TRANSFER,
        budget=resolve_transfer_amount(transfer, basis),
        blocked=False,
    )
