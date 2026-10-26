"""Mutating carry-forward execution.

``carry_forward_unpaid`` applies the three-way partition's semantics --
settle-and-roll for a definition's envelope rows (recurring or rule-less,
ruling **R-BAL44**), move-whole for discrete rows, and
``transfer_service.update_transfer`` for transfers -- as one atomic batch.
The caller owns the surrounding commit; a ``ValidationError`` from the
envelope branch must roll the whole batch back.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.services import posting_service, transfer_service
from app.services.amount_ownership import state_own_amount
from app.services.match_press import NOTHING_SHOWN, Press, Shown
from app.services.cash_ledger import resolve_transaction_amount
from app.services.one_off import (
    due_date_after_move,
    due_date_for,
    holds_a_row_in,
    place_row_of,
)
from app.services.recurrence import compute_due_date
from app.services.row_valuation import purchases_total
from app.utils.balance_predicates import is_projected_clause
from app.utils.log_events import BUSINESS, EVT_CARRY_FORWARD, log_event

from ._context import (
    _build_carry_forward_context,
    _classify_leftover_target,
    _TargetKind,
)

logger = logging.getLogger(__name__)


def carry_forward_unpaid(source_period_id, target_period_id, scenario_id,
                         *, balance_ctx, shown: Shown = NOTHING_SHOWN):
    """Carry forward all projected items from source to target period.

    Steps:
      1. Verify both periods are in the owner's pay calendar.
      2. Find every non-deleted, projected transaction and transfer in the
         source period that belongs to the specified scenario.
      3. Partition into transfer / envelope / discrete buckets.
      4. Apply each bucket's semantic (settle-and-roll for envelope,
         move-whole for discrete, ``transfer_service`` for transfers).
      5. Return the count of carried items (envelope settle counts
         as 1; discrete move counts as 1; each transfer counts as 1).

    The caller (typically the carry-forward route) is responsible for
    committing the surrounding transaction.  This service does not
    commit.  It does flush at the end so the route can immediately
    issue follow-up queries.

    Args:
        source_period_id: The pay_period.id to carry forward FROM.
        target_period_id: The pay_period.id to carry forward TO.
            Typically the user's current period.
        scenario_id: The scenario to carry forward within.  Prevents
            cross-scenario data corruption when multiple scenarios
            exist for the same user.
        balance_ctx: The request's
            :class:`~app.services.balance_at.BalanceContext`, opened once by
            the route and threaded down (pay-calendar plan step C2-f3c; plan
            step R7d-c-1 moved it from the calendar to the pass that derives
            one).  It names the owner, so no ``user_id`` rides beside it, and
            a period id that is not in its calendar is not this owner's --
            which is how both periods are ownership-checked.
        shown: The bank lines the confirmation NAMED before the press, as
            its Confirm posts them back
            (:attr:`~._preview.CarryForwardPreview.named_line_ids`, plan step
            ``credit_card:CC-5-4a-5``, leaf 5c-2c-2; rulings **R-CC76**,
            **R-CC127**, **R-CC135**).  The batch is ONE save, so its one
            press compares everything it frees with this, whole, and refuses
            with ``PageOutOfDate`` where they differ -- a match made or
            undone in another tab since the modal was drawn.  The default is
            a page that named nothing (*"A button with no warning sends
            nothing"*), so a caller that says nothing withdraws no match.

    Returns:
        int -- the number of carried items (1 per source row processed).

    Raises:
        NotFoundError: If either period is not in *balance_ctx*'s calendar --
            it does not exist, or it is not this owner's.
        ValidationError: On four conditions, any of which fails the WHOLE
            batch -- the caller must rollback the session before issuing any
            follow-up writes.  (a) The envelope branch's ``AMBIGUOUS`` guard: a
            destination period with more than one mutable row for the same
            (template, scenario), a corrupt pre-existing state.  All other
            former block conditions (inactive template, finalised or
            soft-deleted destination) now create a fresh override row instead.
            (c) The envelope branch's ``CLOSED`` guard and (d) the discrete
            branch's twin of it (ruling **R-BAL44**, leaf 7b-3): a rule-less
            definition's row carried into a paycheck that already holds a
            row of it, or whose leftover would answer a day a row of it
            answers.
            (b) Since plan step C9b, a carried TRANSFER that is a loan payment
            whose destination period would place its installment at or before
            the loan's origination (``transfer_service.update_transfer``
            enforces ruling R-C on the period move).  The refusal itself is
            correct -- the moved payment would be erased by the fold -- but it
            costs the rest of the batch, which is recorded as a finding rather
            than fixed here: making it skip-and-report is a behaviour change to
            carry-forward's batch semantics, not to the guard.
        PageOutOfDate: When what the batch frees differs from what *shown*
            named (the batch's one press).  A ``ValidationError`` too, so the
            caller's rollback above covers it.
    """
    # ONE press for the whole batch (plan step ``credit_card:CC-5-4a-5``,
    # ruling R-CC135, "One check per save"): every envelope's settle and
    # every transfer's move is one call of it, and the withdrawals it makes
    # are logged at its close, so a batch its press refuses logs none.
    # PROMISED (leaf 5c-2c-2): the modal names exactly what the batch frees,
    # with no kind of press to choose between, so a batch that reaches NO
    # match step is compared too -- an envelope another tab closed since the
    # modal was drawn refuses the page that named its line.  (A line another
    # tab merely unmatched is refused without it: the envelope's kept payment
    # still reaches the match step, which engages the press.)
    with Press(shown, promised=True) as press:
        count, carried = _carry_forward(
            source_period_id, target_period_id, scenario_id, balance_ctx,
            press,
        )
    # The batch's own event AFTER the press has closed, so a batch its close
    # refuses does not report a carry it never made (the leaf's review, M1).
    # It still precedes the route's commit, as every business event of a save
    # does (ledger row balance:BAL-599).
    if carried is not None:
        log_event(logger, logging.INFO, EVT_CARRY_FORWARD, BUSINESS,
                  "Carried forward unpaid items",
                  user_id=carried.user_id,
                  count=count, from_period_id=source_period_id,
                  to_period_id=target_period_id,
                  envelope_count=len(carried.envelope_txns),
                  discrete_count=len(carried.discrete_txns),
                  transfer_count=len(carried.transfers))
    return count


def _carry_forward(source_period_id, target_period_id, scenario_id,
                   balance_ctx, press):
    """Run :func:`carry_forward_unpaid` inside its press, which it threads.

    Returns:
        ``(count, ctx)`` -- the carried count and the batch's context, whose
        partition the caller's event reports; ``(0, None)`` when the source
        holds nothing to carry, which reports nothing.
    """
    ctx = _build_carry_forward_context(
        source_period_id, target_period_id, scenario_id, balance_ctx,
    )
    user_id = ctx.user_id

    if (not ctx.transfers
            and not ctx.envelope_txns
            and not ctx.discrete_txns):
        # Includes the same-period short-circuit and the
        # genuinely-nothing-to-carry case.  No flush needed.
        return 0, None

    count = 0

    # The discrete and envelope branches both run inside a no_autoflush
    # block so a partially-mutated row (is_override flipped, pay_period
    # not yet flipped, etc.) cannot trigger an autoflush mid-iteration
    # via a downstream lazy-load query.  An autoflush at the wrong
    # moment violates the partial unique generation index (plan step R17
    # split it in two: idx_transactions_template_scenario_occurrence for a
    # row that answers an occurrence, ..._undated for one that does not),
    # even though the FINAL state is index-safe.  See the original 33cd21e fix and
    # docs/carry-forward-aftermath-implementation-plan.md Phase 4.
    with db.session.no_autoflush:
        # ── Discrete branch ────────────────────────────────────────
        # Conditional bulk UPDATE rather than per-row ORM mutation.
        # The Projected predicate in the WHERE clause closes the F-049
        # race: between the SELECT in ``_build_carry_forward_context``
        # and the flush, a concurrent ``mark_done`` (or ``mark_credit``
        # / ``cancel``) request can transition a row out of Projected,
        # and a per-row ``setattr(...)`` followed by a flush would
        # carry a settled row into the target period -- erasing the
        # user's prior status decision.  The bulk UPDATE atomically
        # re-checks the status as a SQL precondition, so race-loser
        # rows are silently left in place (still Paid, still in the
        # source period, untouched by this batch) and the count
        # reflects only the rows that actually moved.  Audit reference:
        # F-049 / commit C-22 of the 2026-04-15 security remediation
        # plan.  Routed through the centralized ``is_projected_clause``
        # (D6-09 / MED-02) so this re-check shares one definition with
        # the source-period SELECT above.
        #
        # THREE passes, because the rows differ in what the move writes.  A
        # row of a RECURRING definition must flip ``is_override = TRUE`` as
        # part of the same SQL UPDATE: the flag is what keeps the maintain
        # and generate passes off a row the owner placed elsewhere, and
        # flipping it with the period leaves no transient state for the
        # undated generation index (which excludes override rows) to collide
        # on with the rule's own row in the target.  A row NO RULE generated
        # -- ad-hoc, or a rule-less definition's -- takes no flag: no pass
        # will ever write over it, and a flag there would hide it from
        # ``recurrence_engine.propagate_to_unruled_definition`` (the twin's
        # defect **BAL-493** on this table) and from a rule added later
        # (R-BAL25).  **The split is ``recurs`` since plan step
        # balance:X-bi-7a**, not the link (ruling R-BAL20).
        #
        # The ``Transaction.version_id: + 1`` assignment honors the
        # optimistic-lock contract from C-17 / F-009: every UPDATE
        # bumps the counter so any concurrent ORM-level flush against
        # the same row fails its ``WHERE version_id = ?`` and surfaces
        # as ``StaleDataError`` rather than silently overwriting our
        # batch.  ``synchronize_session="fetch"`` issues a SELECT
        # before the UPDATE to identify affected rows and expires
        # those instances in the session so any later access reads
        # fresh values from the database -- preserving the
        # ``no_autoflush`` invariant that the in-memory state never
        # diverges from the database while the loop runs.
        if ctx.discrete_txns:
            moved = _partition_discrete(ctx, target_period_id)

            if moved.recurring_ids:
                count += (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.id.in_(moved.recurring_ids),
                        is_projected_clause(Transaction),
                        Transaction.is_deleted.is_(False),
                    )
                    .update(
                        {
                            Transaction.pay_period_id: target_period_id,
                            Transaction.is_override: True,
                            Transaction.version_id: Transaction.version_id + 1,
                        },
                        synchronize_session="fetch",
                    )
                )

            if moved.re_placed_ids:
                count += (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.id.in_(moved.re_placed_ids),
                        is_projected_clause(Transaction),
                        Transaction.is_deleted.is_(False),
                    )
                    .update(
                        {
                            Transaction.pay_period_id: target_period_id,
                            Transaction.due_date: moved.re_placed_due,
                            Transaction.occurs_on: moved.re_placed_due,
                            Transaction.version_id: Transaction.version_id + 1,
                        },
                        synchronize_session="fetch",
                    )
                )

            if moved.unruled_ids:
                count += (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.id.in_(moved.unruled_ids),
                        is_projected_clause(Transaction),
                        Transaction.is_deleted.is_(False),
                    )
                    .update(
                        {
                            Transaction.pay_period_id: target_period_id,
                            Transaction.version_id: Transaction.version_id + 1,
                        },
                        synchronize_session="fetch",
                    )
                )

        # Envelope branch.  Each iteration settles the source row and
        # rolls the unspent leftover into the destination -- bumping an
        # existing mutable row, generating the canonical, or creating a
        # fresh override row when none exists.  The helper raises
        # ValidationError only on the AMBIGUOUS guard (>1 mutable
        # destination row); the route catches that and rolls back the
        # session for batch atomicity.
        for txn in ctx.envelope_txns:
            _settle_source_and_roll_leftover(
                txn, ctx.target_period, ctx.basis, ctx.schedule, press,
            )
            count += 1

    # Move transfers via the service, one call per TRANSFER: the context
    # walks ``budget.transfers`` since plan step ``balance:X-bi-6-4c-2``, where
    # this loop walked both SHADOWS of each and de-duplicated by
    # ``transfer_id``.  Each transfer counts as 1 carried-forward item.
    #
    # **The flag is a RECURRING definition's transfer's alone** (plan step
    # ``balance:X-ci-1``, ruling **R-BAL93**; this arm was finding
    # **BAL-493**'s third writer, flipping ``is_override`` on every transfer
    # it moved): it is what keeps the maintain and generate passes off a row
    # the owner placed elsewhere, exactly as the discrete branch's first
    # pass above flips it for a recurring definition's row.  A ONE-TIME
    # transfer -- a rule-less definition's -- is never overridden against a
    # cadence it does not have, and a flag there only hid it from
    # ``transfer_recurrence.propagate_to_unruled_template`` for good; its
    # move RE-PLACES its due date and carries ``occurs_on`` with it INSIDE
    # ``update_transfer`` (**R-BAL96**), so this door states the period and
    # nothing else, as the ``re_placed_ids`` pass states the same rule in
    # SQL for a transaction.  An ad-hoc transfer takes no flag either: no
    # pass will ever write over it.
    for transfer in ctx.transfers:
        # The service moves the transfer to the target period, and both of
        # its legs read the transfer's period (``transfer_legs``), so no
        # second row holds a period that could disagree with it.  It moved
        # the two twin rows with it until plan step ``balance:X-bi-6-4d-2``
        # deleted them.
        moved = {"pay_period_id": target_period_id}
        if transfer.recurs:
            moved["is_override"] = True
        # The batch's press, so the batch stays ONE save; a period move
        # reaches no settle and no match step today (``_update.
        # _apply_remaining_fields`` writes the period on the transfer alone).
        transfer_service.update_transfer(
            transfer.id, user_id, press=press, **moved,
        )
        count += 1

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 3) ──────────────
    # Each envelope source was settled at sum(entries) inside the loop above;
    # post its confirmed cash effect to the double-entry ledger.  Done AFTER the
    # no_autoflush block and its flush -- NOT inside settle_from_entries (which
    # runs inside that block) -- so _emit_balanced_entry's flush lands on the
    # batch's index-safe final state, never mid-loop where a partially-mutated
    # partially-mutated row could violate a generation index.  The reconcile is
    # idempotent and a no-op for the common empty-envelope rollover and for a
    # source whose purchases are all still in flight: a source row books
    # nothing of its own (plan step ``balance:X-bi-4a``, ruling **R-BAL80**),
    # and its DATED purchases were posted when they were dated.  Only envelope
    # sources need a reconcile here: carry-forward moves only Projected rows,
    # so the transfers relocated above are unsettled and
    # transfer_service.update_transfer posted nothing for them.
    for source_txn in ctx.envelope_txns:
        posting_service.sync_transaction_postings(source_txn)
    # **The DISCRETE rows need one too, since plan step X-f3b** (ruling
    # **R-FM**).  They are RELOCATED rather than settled -- the bulk UPDATEs
    # above set ``pay_period_id`` to the target -- and a posting carries the
    # BUDGET column its source row is attributed to, so a link-less
    # envelope (which ``_context`` routed here, moving whole with its
    # entries, until the family's cutover ``X-bi-7d-2`` minted every one a
    # definition) left its purchases' legs filed under the period it left.
    # The discrete rows are still relocated, so the reconcile stays; what
    # the cutover retired is the row that carried purchases into it.  The comment above used to
    # justify skipping them with "carry-forward moves only Projected rows",
    # which was sound while only a settled row held postings and is the same
    # premise ``routes/transactions/mutations`` re-listed ``pay_period_id``
    # for.  Empty-handed for every row whose family never posted, which is every
    # bill and every plain expense in the batch.
    for moved_txn in ctx.discrete_txns:
        posting_service.sync_transaction_postings(moved_txn)

    return count, ctx


@dataclass(frozen=True)
class _DiscretePartition:
    """The discrete branch's three passes, and the date the re-placed take.

    Attributes:
        recurring_ids: Rows of a RECURRING definition -- moved with
            ``is_override`` flipped.
        re_placed_ids: PLACED rows whose due date the move changes -- moved
            with ``due_date`` and ``occurs_on`` re-placed on
            :attr:`re_placed_due`.
        re_placed_due: The target paycheck's start (ruling **R-BAL22**).
        unruled_ids: Every other row -- moved, and nothing else written.
    """

    recurring_ids: "list[int]"
    re_placed_ids: "list[int]"
    re_placed_due: date
    unruled_ids: "list[int]"


def _partition_discrete(ctx, target_period_id: int) -> _DiscretePartition:
    """Sort the discrete rows into the three UPDATEs the move writes.

    **A PLACED row -- a rule-less definition's -- is RE-PLACED by the
    move** (ruling **R-BAL33**, plan step balance:X-bi-7b): due on
    its placed paycheck's start unless the owner stated a day
    (R-BAL22), so the default follows the placement and an
    owner-stated day, read by position, stays.  ``one_off.
    due_date_after_move`` is the one statement of that rule for both
    doors that move a row; here the rows whose answer moved are
    collected under it and written in one UPDATE, ``occurs_on``
    beside ``due_date`` because a placed row answers its own due date
    (R-BAL25).  The function's only other answer is the row's own
    date, so every re-placed row takes the target's start.  Found by
    7b-1's adversarial review: a one-off is dated at birth since that
    leaf, and this arm moved the period alone, so a one-off rolled to
    the next paycheck read OVERDUE on the dashboard pulse and late in
    ``payment_timeliness`` where the undated row it replaced sat on
    the "anytime this period" shelf.  The balance is identical under
    either date (a one-off's series is flat, R-BAL21).

    A NON-ENVELOPE placed row reaches this branch since ruling
    **R-BAL44** (an envelope of a definition takes the rollover), and
    its definition holds ONE row in almost every case -- the grid's
    one-off, ``mint_uncategorized``'s row -- but not every: an owner
    who unticks *Track individual purchases* on a bank-born envelope
    (the definition's flag, R-BAL36) leaves its rows plain, one per
    paycheck, and one carried INTO a paycheck holding its sibling met
    the occurrence index (found by 7b-3's adversarial review; finding
    **BAL-496**'s last case).  So the re-placing REFUSES, as the
    envelope branch's CLOSED does, where the target already holds a row
    of the definition (``one_off.holds_a_row_in``).  A linked row
    always carries a due date
    (``ck_transactions_template_row_needs_due_date``), so the rule
    above always has one to read; the 6 pre-R17 rows the ``occurs_on``
    backfill left NULL (production 2026-09-13) are undated in THAT
    column alone, all immutable, so none reaches this branch, and
    ``recurrence:R19-b`` (``occurs_on`` NOT NULL) deletes the shape.

    Args:
        ctx: The batch's context (:func:`_build_carry_forward_context`).
        target_period_id: The pay_period.id the batch carries INTO.

    Returns:
        The three id lists and the re-placed date.

    Raises:
        ValidationError: A placed row carried into a paycheck that already
            holds a row of its definition.
    """
    re_placed_due = due_date_for(None, ctx.target_period)
    re_placed_ids = [
        t.id for t in ctx.discrete_txns
        if t.is_placed and due_date_after_move(
            t.due_date,
            source_start=ctx.source_period.start_date,
            target=ctx.target_period,
        ) != t.due_date
    ]
    for t in ctx.discrete_txns:
        if t.is_placed and holds_a_row_in(
            t.template_id, t.scenario_id, target_period_id,
            except_row_id=t.id,
        ):
            raise ValidationError(
                f"Carry forward refused for source transaction "
                f"{t.id} ('{t.name}'): the next paycheck already "
                f"holds a row of that item, and an item holds one "
                f"row per paycheck."
            )
    return _DiscretePartition(
        recurring_ids=[t.id for t in ctx.discrete_txns if t.recurs],
        re_placed_ids=re_placed_ids,
        re_placed_due=re_placed_due,
        unruled_ids=[
            t.id for t in ctx.discrete_txns
            if not t.recurs and t.id not in re_placed_ids
        ],
    )


def _settle_source_and_roll_leftover(source_txn, target_period, basis,
                                    schedule, press):
    """Settle an envelope source row and roll its leftover into the target.

    Implements the envelope branch of Option F (see
    ``docs/carry-forward-aftermath-design.md``):

      1. Compute ``entries_sum`` as ``sum(e.amount for e in
         source.purchases)`` -- the row's purchases, never a covering
         movement a revert kept (ruling **R-BAL68**).  No purchases ->
         ``Decimal("0")`` so the full estimated amount rolls forward.
      2. Compute ``leftover = max(Decimal("0"), <the source's RESOLVED
         amount> - entries_sum)``.  Overspend (``entries_sum > budget``)
         clamps to zero -- the actual overspend is recorded on the
         settled source row's settlement record and on its entries.
      3. If ``leftover > 0``, resolve the destination row via
         ``_resolve_or_create_target_row``, which (a) bumps the single
         mutable (Projected) row for ``(template_id, target period,
         scenario_id)`` when one exists, (b) lets
         ``recurrence_engine.generate_for_template`` create the canonical
         when the destination is empty and the template is active there,
         or (c) creates a fresh ``is_override`` row carrying the leftover
         when neither applies (an inactive template, or a destination
         whose only row is finalised or soft-deleted).
         Then bump the resolved row: its own resolved amount plus the
         leftover becomes the row's OWNERSHIP through
         ``amount_ownership.state_own_amount`` -- one act over one attribute
         since plan step X-au-k, where it was a figure write and a separate
         source clear whose PAIRING the CHECK had to catch -- and
         ``is_override`` flips ``True``.  The flip blocks future
         recurrence-engine passes from regenerating the row (verified by
         the ``is_override`` skip clause in
         ``app/services/recurrence_engine.py``).  A freshly created row
         starts at ``Decimal("0")`` so the bump lands it on exactly the
         leftover.  The only refusal is the ``AMBIGUOUS`` guard -- more
         than one mutable destination row, a corrupt pre-existing state.
      4. Settle the source row via
         ``transaction_service.settle_from_entries``.  The helper
         enforces its own preconditions (envelope template,
         non-deleted, mutable status, no transfer_id), all of which
         are already guaranteed by the partitioning in
         ``carry_forward_unpaid``.

    Mutations land on ``source_txn`` and the target row in place; the
    caller owns the session/commit lifecycle.  Two flushes can happen here:
    ``recurrence_engine.generate_for_template``'s, when it has to create a
    canonical, and ``one_off.place_row_of``'s, when a rule-less
    definition's leftover row is placed (ruling **R-BAL44**) -- the
    recurring ``CREATE`` branch only ``db.session.add``s.  Both are
    acceptable inside the surrounding ``no_autoflush`` block: an
    ``is_override`` row is index-safe in every intermediate state, and a
    placed row is written only after ``CLOSED`` has refused the states it
    could collide with.  A THIRD since plan step ``credit_card:CC-5-4a-3``,
    and only for a source holding a kept payment an accepted match still
    names: the settle below takes that payment off the books
    (``movement_removal.remove_movements``, ruling **R-CC54**), whose match
    step flushes the withdrawn act, or the member taken out of a surviving
    group act, before the payment goes.  An
    unmatched kept payment flushes nothing, as before that step; a matched
    one was measured on no production row (2026-09-22: 0 matched movements
    under a non-settled row).  That this third flush is index-safe in every
    intermediate state is READ, not measured: that leaf's review traced every
    branch and found none it could break.

    Args:
        source_txn: A Projected, non-deleted, envelope-tracked transaction of
            a DEFINITION (recurring, or rule-less since ruling **R-BAL44**) in
            the source period.  Partitioning in ``carry_forward_unpaid``
            guarantees the preconditions.
        target_period: The target
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        schedule: The request's
            :class:`~app.services.generation_schedule.GenerationSchedule`
            (``ctx.schedule``), passed through to the target-row resolution.
            Passed pre-fetched to avoid a redundant lookup; the
            caller already validated ownership.
        basis: The request's :class:`~app.services.cash_ledger.AmountBasis`
            (``ctx.basis``).  Its ``scenario_id`` scopes the target-row lookup
            and the recurrence-engine call so cross-scenario data is never
            touched, and it prices both ends of the rollover.
        press: The batch's one :class:`~app.services.match_press.Press`
            (:func:`carry_forward_unpaid`).

    Raises:
        ValidationError: On the ``AMBIGUOUS`` guard -- more than one mutable
            destination row for ``(template, period, scenario)``, a corrupt
            pre-existing state the user must resolve manually -- and on
            ``CLOSED`` (a rule-less definition whose target already answers
            the occurrence, ruling **R-BAL44**).  Each names the source row.
    """
    # Pylint: ``import-outside-toplevel`` -- defer the recurrence-engine
    # import to avoid a circular dependency at module load time:
    # recurrence_engine is service-layer code and this module is
    # service-layer code; importing at top level works in current code
    # but the deferred form documents the intentional one-way dependency
    # (carry-forward depends on recurrence-engine, never the reverse).
    from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel
    # Pylint: ``import-outside-toplevel`` -- same deferred-import
    # rationale: transaction_service is service-layer code reached only
    # from inside this function, so the local import keeps the module's
    # top-level dependency graph free of the service-to-service cycle.
    from app.services import transaction_service  # pylint: disable=import-outside-toplevel

    # Compute leftover BEFORE looking at the target so a downstream
    # validation failure leaves source.entries (and any pending
    # mutations on this row) untouched.  Reading entries triggers a
    # lazy-load SELECT inside no_autoflush, which is safe because
    # this function never mutates entries.  The PURCHASES, never the
    # family (ruling R-BAL68): a reverted manual close's kept movement is
    # not spend, so the whole budget rolls -- and the settle below then
    # withdraws that movement (a ``purchases`` record covers nothing).
    entries_sum = purchases_total(source_txn.purchases)
    # The source's BUDGET, resolved rather than read off the column (plan step
    # X-au-c2b): ruling E-21 fixes an envelope's base on its own amount
    # unconditionally, and a derived row stores none.
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
    leftover = max(
        Decimal("0"),
        resolve_transaction_amount(source_txn, basis) - entries_sum,
    )

    # Bump the target only when there is unspent leftover.  Overspend
    # and exact-spend cases (leftover == 0) settle the source without
    # touching the target -- the target's own canonical (or its
    # absence) is irrelevant to a zero rollover, and validating it
    # would fail surprises like a fully-paid target period that the
    # user does not need to mutate.
    if leftover > 0:
        target_row = _resolve_or_create_target_row(
            source_txn, target_period, basis, recurrence_engine,
            schedule,
        )
        # Resolve BEFORE writing: a topped-up row states its own figure from
        # then on, and reading the column instead of resolving it would meet a
        # ``None`` on a derived target.
        #
        # **Releasing the relation is no longer a second statement** (plan step
        # X-au-k).  This read "write the column, then clear the source", two
        # lines whose ORDER did not matter but whose PAIRING did, and a caller
        # that wrote the first without the second was an ``IntegrityError`` at
        # flush.  ``state_own_amount`` assigns one attribute holding one of two
        # shapes, so the release IS the write.
        state_own_amount(
            target_row, resolve_transaction_amount(target_row, basis) + leftover,
        )
        target_row.is_override = True

    # Its ``purchases`` record takes a kept payment off the books, and any
    # match left naming no row with it: the confirmation named those lines
    # first (``_preview._what_each_close_frees``, ruling **R-CC76**), and the
    # batch's press compares (:func:`carry_forward_unpaid`).
    transaction_service.settle_from_entries(source_txn, press=press)


def _resolve_or_create_target_row(source_txn, target_period,
                                  basis, recurrence_engine, schedule):
    """Return the destination row that receives *source_txn*'s leftover.

    Thin switch over ``_classify_leftover_target`` (the single source of
    truth shared with the preview):

      * ``TOP_UP`` -- exactly one mutable row already exists; return it
        for the caller to bump.
      * ``GENERATE`` -- the destination is empty and the template is
        active there; ask the recurrence engine to create the canonical
        and return it.  A race that leaves nothing falls through to
        ``CREATE`` rather than failing the batch.
      * ``CREATE`` -- no usable row and the engine will not generate one
        (inactive template, or a destination whose only row is finalised
        or soft-deleted); create a fresh override row -- or, for a
        rule-less definition, place a row of it (ruling **R-BAL44**).
      * ``CLOSED`` -- a rule-less definition whose row in the target has
        finalised; refused, since a second row there would answer the same
        occurrence.
      * ``AMBIGUOUS`` -- more than one mutable row for the same
        ``(template, period, scenario)`` that ``_leftover_recipient``
        cannot choose between: they answer the same occurrence, or one
        answers none.  Since plan step R17 a paycheck may legitimately hold
        several rows of one template (a cadence that names it more than
        once), and those are TOP_UP on the earliest occurrence rather than
        a refusal.  The route catches the ``ValidationError`` and rolls the
        whole batch back.

    The returned row is the caller's to bump (its resolved amount plus the
    leftover, with its ``amount_source_id`` cleared); a freshly created row
    starts at ``Decimal("0")`` so the bump lands it on exactly the leftover.

    Args:
        source_txn: The envelope source row being carried forward.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        basis: The request's amount basis; its ``scenario_id`` is the scenario
            the rollover stays within.
        recurrence_engine: The recurrence-engine module, used for the
            ``GENERATE`` branch's ``generate_for_template`` call.  It was
            threaded "to avoid a circular import at module top", and **that
            reason no longer holds**: this module now imports
            ``compute_due_date`` from this same package at module top and
            every import order resolves -- ``recurrence_engine`` imports
            nothing from ``carry_forward_service``, only mentions it in a
            comment.  Left threaded rather than deleted because dropping a
            parameter is a signature change this step did not scope; doing so
            is the cleanup this note exists to name.
        schedule: The request's
            :class:`~app.services.generation_schedule.GenerationSchedule`
            (``ctx.schedule``) -- the owner's whole pay-period schedule with
            its write window narrowed to *target_period*.  Threaded rather
            than built here because this runs once per envelope row.

    Returns:
        The Transaction row to bump.

    Raises:
        ValidationError: On the ``AMBIGUOUS`` corrupt-state guard, and on
            ``CLOSED``.
    """
    resolution = _classify_leftover_target(
        source_txn, target_period, basis, schedule,
    )

    if resolution.kind is _TargetKind.AMBIGUOUS:
        raise ValidationError(
            f"Carry forward refused for source transaction "
            f"{source_txn.id} ('{source_txn.name}'): target period "
            f"{target_period.period_id} has more than one open row for "
            f"template {source_txn.template_id}.  Resolve the duplicate "
            f"rows manually before retrying."
        )
    if resolution.kind is _TargetKind.CLOSED:
        # A rule-less definition's one row in the target has closed, or a
        # row of it elsewhere -- the source itself, dated by its owner into
        # the target -- already answers the target's start; a placed row
        # would answer the same occurrence (ruling **R-BAL44**'s one
        # refusal; ``_TargetKind.CLOSED`` carries the argument, and its
        # ``row`` says which arm).
        if resolution.row is not None:
            raise ValidationError(
                f"Carry forward refused for source transaction "
                f"{source_txn.id} ('{source_txn.name}'): its envelope in the "
                f"next paycheck has already closed, so the unspent "
                f"{source_txn.name} budget has nowhere to roll.  Add the "
                f"unspent amount to a later paycheck's envelope yourself."
            )
        raise ValidationError(
            f"Carry forward refused for source transaction "
            f"{source_txn.id} ('{source_txn.name}'): a row of "
            f"{source_txn.name} is already due on "
            f"{due_date_for(None, target_period).isoformat()}, the day the "
            f"unspent budget would be placed on, so it has nowhere to roll.  "
            f"Move that row's due date, or add the unspent amount to a later "
            f"paycheck's envelope yourself."
        )

    if resolution.kind is _TargetKind.TOP_UP:
        return resolution.row

    if resolution.kind is _TargetKind.GENERATE:
        # The window is this ONE period; the schedule the rule is read
        # against is the owner's whole one (plan step R4b-1).  Handing the
        # single period over as both -- which this did until R4b-1 -- made a
        # ``Monthly First`` rule see one month holding one payday and answer
        # "fires here" for any period at all, so the row this branch created
        # was a duplicate the correct engine never names.
        created = recurrence_engine.generate_for_template(
            source_txn.template, schedule, basis.scenario_id,
        )
        generated = next(
            (t for t in created
             if t.pay_period_id == target_period.period_id),
            None,
        )
        if generated is not None:
            return generated

    # CREATE (or a GENERATE race that produced nothing).  A RULE-LESS
    # definition's row is PLACED, through the one producer of such a row
    # (ruling **R-BAL44**): dated at the target paycheck's start and
    # answering that day.  **It starts at ``Decimal("0")``, OWN, exactly as
    # the override row does**, so the caller's bump lands it on the leftover
    # alone (**R-BAL43**: one occurrence among many): no rule budgets this
    # definition in the target, so the row exists only to carry what rolled
    # -- the CREATE arm's ``base`` the preview already promises.  Left
    # priced by the definition, the bump read its standing price and wrote
    # price PLUS leftover, `$170.00` where the preview said `$70.00`
    # (measured by this leaf's own test before this line existed).  It
    # flushes, and that is index-safe here because CLOSED above refused
    # both states a placed row could collide with: a live row of the
    # definition in the target, and a row of it -- the source dated there
    # by its owner, or any sibling -- answering the target's start.  A
    # RECURRING definition's row is the fresh override row it always was.
    if not source_txn.recurs:
        placed = place_row_of(
            source_txn.template, target_period,
            scenario_id=basis.scenario_id,
        )
        state_own_amount(placed, Decimal("0"))
        return placed
    # The occurrences the rule names in the target, read off the pass's
    # memoised walk (the one the classifier's prediction reads when it asks),
    # so the override row is dated from what the rule says about THIS
    # paycheck (ruling R-R97) rather than from a guess about it.
    return _create_target_override_row(
        source_txn, target_period, basis.scenario_id,
        recurrence_engine.occurrences_in_period(
            source_txn.template, target_period.period_id, basis.scenario_id,
            schedule=schedule,
        ),
    )


def _leftover_due_date(template, target_period, occurrences) -> date:
    """Return the day a leftover row of *template* is due in *target_period*.

    **Ruling R-R97** (developer, 2026-09-23, refining **balance:R-BAL6**; plan
    step recurrence:R5-a): where the definition's rule fires inside the target
    paycheck, the row is dated as the definition's own row answering that
    occurrence is -- ``compute_due_date`` over it, so the occurrence itself
    for a rule naming a day and the payday for one naming none -- taking the
    EARLIEST where it fires there twice, the one
    ``_context._leftover_recipient`` bumps; where it fires nowhere in it, the
    paycheck's payday.  The first arm is
    R-BAL6's rule, "the date the definition's own rule gives for that
    paycheck", kept wherever its premise holds: the leftover is dated exactly
    as the definition's own row there is, so a hand-back prices it on the
    same day that row prices.  The second is the date ruling **R-BAL22**
    gives any row answering no occurrence and ruling **R-R95** gives a rule
    naming no day, stated through R-BAL22's producer
    (:func:`~app.services.one_off.due_date_for`).  A definition that no longer
    recurs (archived: ``recurrence_engine.definition_recurs``) names no
    occurrence anywhere, so a row of one reaching here -- a settled row
    reverted after the archive -- takes the payday too.

    **What the second arm replaced was a counterfactual.**
    ``compute_due_date`` was a pure function of ``(rule, period)`` until
    R5-a and answered for a paycheck the rule never names -- the yearly
    Father's Day envelope rolling into an off-anniversary paycheck, the case
    ``_create_target_override_row`` exists for -- with the rule's day of the
    month in the month the paycheck opens in.  That could land BEFORE the
    paycheck opened, so the row read overdue on the dashboard the moment it
    was created.  R5-a dates a row from its OCCURRENCE (ruling **R-R94**),
    and here there is none; R-R97 is the developer's answer for that case,
    over "always the payday" (R-BAL6's rejected option, which would re-date
    the first arm too), keeping the guess, and the rule's next occurrence (a
    September leftover dated next June).

    The first arm goes through
    :func:`~app.services.recurrence.compute_due_date`, the one
    producer of "what date does a row of this definition in this period carry"
    -- shared with the transaction engine (``_amounts._derive_row_fields``)
    and the transfer engine (``transfer_recurrence``), the two that outlive
    this step.  *Two more callers were claimed here and never were*:
    ``routes/transfers/_instances`` stopped calling it at plan step R2e-3, and
    migration ``48e2c7ee593d`` froze a COPY of it rather than importing it.
    The one-time ``occurs_on`` backfill script was a third caller as this was
    written and is deliberately not listed: it is retired (2026-09-06) by the
    step that merges immediately BEFORE this one, precisely BECAUSE this change
    breaks the provenance filter it read a leftover row's missing date as.

    **Why the row is dated at all**, where it carried ``None`` until this step:
    a row that names a recurring definition can be handed BACK to it -- that is
    what ``recurrence_engine.resolve_conflicts``'s "use the template's amount"
    does -- and a row priced by its definition resolves the series on its OWN
    due date (amount rule 3).  An undated one is therefore unpriceable the
    moment the owner presses that button, and ``AmountUnresolvable`` has no
    handler on the grid, dashboard or companion path.  Reproduced against the
    pre-fix producer, which is what
    ``TestACarriedForwardLeftoverRowIsDated`` grades.

    **The defect was live and UNREALISED**, measured 2026-09-06 rather than
    assumed: zero rows carried ``template_id`` with a NULL ``due_date`` on the
    dev database or on the 1034-row production clone, so nothing in the data
    needed repairing and this step owes no backfill.

    *The reason the old ``None`` gave does not carry over.*  It was that
    copying the SOURCE row's date -- a past period's -- would render the new
    row overdue.  True, and this is not that: both arms answer a day INSIDE
    the TARGET paycheck -- an occurrence the rule seats there, or its
    payday.

    **WHAT MOVED, traced rather than assumed.**  No figure changes and no
    period total moves, because every consumer that places a row on a DAY goes
    through ``DerivedPeriod.attribution_day``, which falls back to the period's
    start for a dateless row and clamps a dated one into the period -- so the
    two cases meet at the same day whenever the computed date lands outside.
    Four surfaces do read the date and now behave differently for these rows,
    and each is the leftover behaving like the canonical it stands in for:
    ``reconcile_service._rows`` offers it from its due day rather than from the
    period's start; ``balance_at._cash_fold`` steps the intra-period daily ramp
    on that day; ``dashboard_service._pulse`` gives it a day on the street axis
    instead of the "anytime this period" shelf; and
    ``grid_view_service.due_captions_by_key`` renders a caption where it
    rendered none.

    **It reads the definition's rule with no ``None`` arm since plan step
    balance:X-bi-7a.**  An arm dated a CLEARED cadence's leftover row from the
    paycheck's start, because such a definition's rows used to take the
    rollover; the context routes a row here only when its definition
    ``recurs`` now (ruling **R-BAL20**), so a rule-less definition's row moves
    whole and never reaches this function.  The arm was unreachable, and an
    unreachable arm with a reason attached is a sentence the code contradicts.

    Args:
        template: The envelope's
            :class:`~app.models.transaction_template.TransactionTemplate`.
            Never ``None`` and never rule-less -- ``_build_carry_forward_context``
            routes a row into ``envelope_txns`` only when it ``recurs`` and
            ``tracks_purchases``.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        occurrences: The occurrences the rule names in *target_period*,
            ascending (``recurrence_engine.occurrences_in_period``); empty
            where it names none.

    Returns:
        The ``date`` the leftover row is due on.

    Raises:
        RecurrenceResolutionError: From ``compute_due_date`` -- or earlier,
            from ``occurrences_in_period``'s read of the rule -- when the rule
            names a unit or a placement this application does not model.  It
            propagates rather than being absorbed, which is the refusal every
            other reader of that rule already makes.
    """
    if not occurrences:
        return due_date_for(None, target_period)
    return compute_due_date(
        template.recurrence_rule, occurrences[0], target_period,
    )


def _create_target_override_row(
    source_txn, target_period, scenario_id, occurrences,
):
    """Create a fresh override row in *target_period* for the leftover.

    Used for a RECURRING definition when no mutable destination row exists
    to top up and the recurrence engine will not generate one -- e.g. a
    yearly Father's Day envelope rolling into an off-anniversary period, or
    a destination whose only row is finalised or soft-deleted (a RULE-LESS
    definition's leftover row is placed by ``one_off.place_row_of`` instead,
    ruling **R-BAL44**).  The row is created at
    ``Decimal("0")``; the caller folds the leftover on top via the same
    bump every branch uses, so the row ends at exactly the leftover
    amount.

    The row copies its identity (account, template, name, category, type)
    from *source_txn* and is flagged ``is_override = True`` so (a) it is
    excluded from both partial unique generation indexes and never
    collides with a canonical or soft-deleted sibling, and (b) the recurrence engine
    skips it on later passes (``_recurrence_common.OccurrenceClaims`` -- a row
    answering no OCCURRENCE claims its whole paycheck).  ``template_id`` is
    copied verbatim, and ``due_date`` is :func:`_leftover_due_date` -- the
    occurrence the definition's rule seats in *target_period*, else that
    paycheck's payday -- which is what makes
    the copied link safe to hand back to (see that function).  It was ``None``
    until the developer's ruling of 2026-09-06 (**R-BAL6**), and is ruling
    **R-R97**'s since plan step recurrence:R5-a.

    **``occurs_on`` and ``due_date`` are different facts and only the second
    moved.**  The occurrence is which firing of the cadence a row answers, and
    this constructor still leaves it unset; the due date is the day the money
    is owed, and amount rule 3 resolves the definition's price series on it.
    Both partial unique generation indexes are keyed on ``occurs_on``, and the
    undated one excludes ``is_override`` rows besides, so no UNIQUE index
    reads ``due_date`` and dating the row can collide with nothing.  The one
    CHECK that reads it, ``ck_transactions_template_row_needs_due_date`` (plan
    step X-bv-2), REQUIRES a linked row to be dated, which is what this
    constructor now does; it is the reason an undated leftover cannot be
    written any more, not a collision.  It is not true that the column is in
    no index at all -- these rows now enter ``idx_transactions_due_date``,
    which is non-unique and exists to serve range reads.

    No flush: the caller runs inside ``carry_forward_unpaid``'s
    ``no_autoflush`` block and an ``is_override`` row is index-safe in
    every intermediate state.

    Args:
        source_txn: The envelope source row whose identity is copied.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        scenario_id: Scenario the new row belongs to.
        occurrences: The occurrences the rule names in *target_period*,
            ascending -- what :func:`_leftover_due_date` dates the row from.

    Returns:
        The newly added (unflushed) Transaction.
    """
    row = Transaction(
        # A carried-forward row is the SOURCE row's, moved (plan step
        # ``pay_calendar:C13-a``): the owner travels with the identity this
        # constructor copies, exactly as the account and template do.
        user_id=source_txn.user_id,
        account_id=source_txn.account_id,
        template_id=source_txn.template_id,
        pay_period_id=target_period.period_id,
        scenario_id=scenario_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=source_txn.name,
        category_id=source_txn.category_id,
        transaction_type_id=source_txn.transaction_type_id,
        amount_ownership=AmountOwnership.own(Decimal("0")),
        due_date=_leftover_due_date(
            source_txn.template, target_period, occurrences,
        ),
        is_override=True,
        is_deleted=False,
    )
    db.session.add(row)
    return row
