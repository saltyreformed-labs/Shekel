"""
Shekel Budget App -- Transfer Recurrence Engine

Parallel to the ``recurrence_engine`` package but generating Transfer records
instead of Transaction records.  The model-agnostic halves of the two engines
(the gating + occurrence-matching preamble via
``recurrence_engine.resolve_generation_plan``, the per-period skip predicate,
the regenerate fetch, the whole maintain DECISION, and the cross-user audit
logging) are shared through that package and
``app/services/_recurrence_common.py`` so the two cannot drift.

**A regeneration MAINTAINS the transfers it already generated** (plan step
R10-b, ruling **R-R19**), which is what the transaction engine started doing at
plan step R10-a.  Until then this engine hard-deleted every non-overridden
generated transfer in the window and built replacements, taking each one's two
shadow rows with it through ``transactions.transfer_id``'s CASCADE.

Measured on a production clone, one routine edit per live recurring template:
99 transfers and 198 shadow rows destroyed and rebuilt with new ids -- to write
values IDENTICAL to the ones already there, on every one of them.  What the
rebuild could not rebuild was the owner's:

  - a transfer's ``notes``, which ``create_transfer`` never receives;
  - a settlement RECORD retained through a revert.  ``status_seam`` releases the
    assertion (``settled_on``, ``reconciled_by_id``) and deliberately KEEPS what
    moved (each leg's covering movement, un-dated; the legs' own
    ``settled_amount`` / ``settled_basis_id`` until plan step
    ``balance:X-bi-4b-2`` deleted them), because the full-edit popover tells
    the owner to revert in order to edit -- so a reverted transfer is
    Projected, not overridden, and the sweep deleted it.  Reproduced through
    this code path: a `$321.45` figure read off a bank statement, gone with no
    prompt.

That is finding **N-292**'s shape on this table.  It is unreachable now: a row
the rule still names is never deleted, and a row it stops naming is RETAINED as
a conflict the moment the owner has records against it.

Key differences from transaction recurrence:
  - No salary linkage.
  - Single amount column (no estimated/actual split).
  - Simpler amount logic: nothing is priced from a paycheck, and the engine
    writes no figure on a transfer it generates -- it declares its TEMPLATE
    and its definition prices it (plan step X-au-f;
    :class:`DerivedTransferFields`).
  - EVERY write goes through ``transfer_service`` -- create, update and delete
    alike -- as ``CLAUDE.md`` (Architecture) requires of every write of a
    transfer: a write anywhere else skips its refusals and the copies it keeps
    in step, the posted ledger's among them.  That is also why the maintain pass
    sends only the fields that actually DIFFER: the update door reconciles the
    posting ledger and emits an audit row per call, so a pass that changes
    nothing must call it for nothing.
"""

import logging
from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.services.amount_ownership import derived_ownership
from app.services.balance_at import BalanceContext
from app.services.definition_unarchive import restored_by_use
from app.services._recurrence_common import (
    TemplateRowSelector,
    PlacedRow,
    classify_unruled_work,
    log_resource_access_denied,
)
from app.services.recurrence import compute_due_date
from app.services.recurrence_engine import (
    ConflictReporting,
    MaintainActs,
    PassReporting,
    RegenerationPreview,
    create_for_unclaimed_occurrences,
    log_use_resolved,
    preview_regeneration,
    regenerate_definition,
    resolve_generation_plan,
)
from app.services import transfer_legs, transfer_service
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_TRANSFER_RECURRENCE_GENERATED,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
    log_event,
)

logger = logging.getLogger(__name__)


def _selector(template, scenario_id):
    """Name what this engine's row fetches are asking about.

    The transfer engine's half of :class:`TemplateRowSelector`, mirroring
    ``recurrence_engine._generate._selector`` exactly: the two engines differ
    in the mapped class and the template foreign-key column and in nothing
    else, so each names its pair ONCE and every shared fetch takes the value.

    Args:
        template: The TransferTemplate this pass is generating from.
        scenario_id: The scenario being written into.

    Returns:
        The :class:`~app.services._recurrence_common.TemplateRowSelector`.
    """
    return TemplateRowSelector(
        Transfer, Transfer.transfer_template_id, template, scenario_id,
    )


class DerivedTransferFields(NamedTuple):
    """What a template and a pay period DERIVE on a generated transfer.

    **THE one statement of which columns a generated transfer takes from its
    DEFINITION rather than from its owner**, and the reason
    :func:`regenerate_for_template` no longer destroys the rows it maintains.
    The twin of ``recurrence_engine.DerivedRowFields`` one table over, and it
    exists for the identical reason (plan steps R10-a and R10-b).

    Both write paths consume it: :func:`generate_for_template` splats it into a
    :class:`~app.services.transfer_service.TransferSpec`, and the maintain pass
    diffs it against an existing row and sends what differs to
    :func:`app.services.transfer_service.update_transfer`.  So a column added
    here is written on a NEW transfer and kept current on an EXISTING one from
    the same edit.

    **All six fields are writable through ONE door, and two of them were not
    until plan step R10-b.**  ``update_transfer`` accepted amount, name,
    category and due date; the two ACCOUNTS it could not express, so a
    definition's account change reached its generated rows only by deleting and
    re-creating them -- and the same gap made the non-repeating propagation
    refuse an account change outright.  The door moves a transfer between
    accounts now (:mod:`app.services.transfer_service._endpoints`), which is
    what lets this class state the whole definition rather than the part that
    happened to be writable.

    Every field here is derived and none is the owner's, which is what makes
    overwriting one on an existing row safe.  The columns that decide whether
    the row is the RULE's at all -- ``is_override``, ``is_deleted`` and
    ``status_id`` -- are deliberately absent: they are the classification the
    caller applies BEFORE deciding to write, never something a write restates.
    ``notes`` is absent for the opposite reason: it is the owner's, no
    definition states one, and destroying it is half of what this step fixes.

    **THE UN-DERIVE THIS CLASS PREDICTED IS WHY THE FIELD IS AN OWNERSHIP**,
    and plan step **X-au-f** is where it came due.  The paragraph here read:
    ``ck_transfers_amount_ownership`` pairs ``amount_source_id`` with ``amount``
    -- exactly one of the two is ever set -- so once X-au-f empties ``amount``
    for a generated transfer this diff would see ``None != default_amount``,
    send the amount, and silently un-derive the row it had just converted.  That
    is exactly what it did when the conversion was first traced.  What resolves
    it is not a guard: the two columns are ONE mapped attribute
    (:class:`~app.models.amount_ownership.AmountOwnership`, plan step X-au-k),
    so the field this class carries IS the ownership, the diff compares
    ownership against ownership, and a migrated row reads equal to what its
    definition says rather than differing from it.  Ruling **R-BAL11** is the
    same statement at the write door.

    Attributes:
        from_account_id: The account the money leaves, from the template.
        to_account_id: The account it arrives at.  With *from_account_id* it is
            the one derived pair whose change is not always applicable: each
            side's payment record is filed against its endpoint, so
            :func:`_rows_the_definition_reattributes` routes a move on a
            record-holding row to the owner as a RETAINED conflict instead of
            applying it.
        name: The template's name.  The transfer's only; each leg's label is
            composed from the ENDPOINTS' names when it is drawn
            (``transfer_legs.leg_label``).
        category_id: The template's category, or ``None``.
        amount_ownership: WHERE this transfer's amount comes from, as ruling
            **R-FI**'s one attribute rather than a figure.  Since plan step
            X-au-f it states ONE shape for every generated transfer --
            ``derived`` naming :attr:`~app.enums.AmountSourceEnum.TEMPLATE` --
            so the row carries a declaration and no figure, and its
            definition's own effective-dated series prices it as of its due
            date.  It was ``amount: Decimal``, the template's
            ``default_amount``, copied onto every generated row; that copy is
            the stale cache this arc deletes, and the transaction twin lost the
            same field at plan step X-au-e.
        due_date: Derived from the rule and the placed occurrence by
            :func:`~app.services.recurrence.compute_due_date`, which always
            answers one (the occurrence for a cadence naming a day of the
            month, the funding payday for one naming none: plan step R5-a);
            for a RULE-LESS definition it is the transfer's own
            (:func:`_derive_unruled_fields`), which is a date too, because
            ``ck_transfers_template_row_needs_due_date`` refuses a linked
            transfer without one.  It was ``date | None`` until plan step
            **X-bv-2** bound that CHECK (ruling **R-BAL17**).
    """

    from_account_id: int
    to_account_id: int
    name: str
    category_id: int | None
    amount_ownership: AmountOwnership
    due_date: date


def _derive_row_fields(
    template, rule, occurrence, period,
) -> DerivedTransferFields:
    """Resolve what *template* derives on the transfer answering *occurrence*.

    The single producer of :class:`DerivedTransferFields`, so the create path
    and the maintain path cannot disagree about what a generated transfer's
    definition says.

    **It takes no salary profile and no calendar**, which is the whole of what
    it does not share with ``recurrence_engine._amounts._derive_row_fields``: a
    transfer moves a stated figure between two of the owner's own accounts, so
    nothing here is priced from a paycheck.

    Args:
        template: The
            :class:`~app.models.transfer_template.TransferTemplate` being
            generated from.
        rule: The template's recurrence rule, already confirmed present by
            :func:`~app.services.recurrence_engine.resolve_generation_plan`
            (``GenerationPlan.rule``).
        occurrence: The date the rule names for this row, straight off its
            ``PlannedOccurrence`` -- what the row is dated FROM since plan
            step R5-a (plan ledger row **D18**).
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` this row
            lives in, straight off the same ``PlannedOccurrence``.

    Returns:
        The :class:`DerivedTransferFields` for this (template, occurrence)
        pair.
    """
    return DerivedTransferFields(
        from_account_id=template.from_account_id,
        to_account_id=template.to_account_id,
        name=template.name,
        category_id=template.category_id,
        amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
        due_date=compute_due_date(rule, occurrence, period),
    )


def _derive_unruled_fields(template, xfer) -> DerivedTransferFields:
    """What a RULE-LESS template's definition says about the transfer it made.

    FIVE of the six columns.  A template with no recurrence places no
    occurrence, so it states no DUE DATE and the row keeps its own -- which is
    expressed by deriving that field FROM the row rather than by carrying a
    shorter tuple, so there is still exactly one statement of what a generated
    transfer's definition says.

    Args:
        template: The rule-less
            :class:`~app.models.transfer_template.TransferTemplate`.
        xfer: The transfer it materialised, which supplies the one field the
            definition does not state.

    Returns:
        The :class:`DerivedTransferFields` this definition says about *xfer*.
    """
    return DerivedTransferFields(
        from_account_id=template.from_account_id,
        to_account_id=template.to_account_id,
        name=template.name,
        category_id=template.category_id,
        amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
        due_date=xfer.due_date,
    )


def propagate_to_unruled_template(template, transfers) -> "list[int]":
    """Apply a RULE-LESS template's definition to the transfers it created.

    **The maintain pass's decision, for the one shape that does not
    regenerate.**  A transfer template with no recurrence rule still moves
    money exactly once, and its single Transfer is materialised at create time
    rather than generated -- so a regeneration would find no rule, name no
    period, and RETIRE the row (that is defect **D16**, and why the route gates
    the sweep on "the template IS or WAS recurring").  **Finding REC-516 made
    that deletion unreachable by a second route while the one-time transfer
    was created UNDATED**: it answered no occurrence (``occurs_on`` was
    ``None``), and an undated row is retained rather than retired.  Plan step
    ``balance:X-ci-1`` (ruling **R-BAL94**) creates it answering the occurrence
    its ``due_date`` names (the chosen paycheck's start), and a date MOVE since
    then gives an older one its occurrence too
    (``transfer_service.update_transfer``), so that second route now covers
    only rows still undated (one-time transfers born before X-ci-1 and a
    recurring definition's pre-R17 legacy rows among them, R-BAL94).  A dated
    row is protected by every door that regenerates a definition declining
    one that has no rule: the edit route's gate, which regenerates only when
    the definition has a rule or had one before the edit
    (:func:`~app.routes._recurrence_conflict_chooser.regenerate_or_conflict_chooser`;
    the edit that clears a cadence sweeps once, and no later edit does), and
    the unarchive door's and the loan-start sync's own ``template.recurs``
    checks (``routes/transfers/lifecycle.unarchive_transfer_template``,
    ``loan_recurrence_sync.sync_loan_payment_start``).
    The gate would be needed even for an undated row, because a retained row still
    raises a conflict the owner would have no reason to see.  What is left is
    this: the row is never retired, and everything else is the same rule.

    **It exists because that "everything else" was NOT the same rule, and an
    adversarial review of plan step R10-b measured the gap.**  The route applied
    the definition unconditionally, so once the two ACCOUNTS became propagable
    a non-repeating transfer holding a retained settlement record had its pair
    moved in silence -- while the identical edit on a RECURRING template was
    retained and reported.  That is the very inconsistency the step removed,
    re-created in the opposite direction.  Both paths ask
    :func:`_rows_holding_owner_records` and
    :func:`_rows_the_definition_reattributes` now, and the decision itself is
    ``_recurrence_common.classify_unruled_work`` since plan step
    ``balance:X-bi-7a``, shared with the transaction twin.

    **And only the fields that DIFFER are sent**, for the reason
    :func:`_apply_maintain_work` gives -- plus one this path has alone: sending
    ``to_account_id`` on every edit re-grades ruling **R-C** against the
    destination loan's origination, so a rename would start failing on a
    legacy pre-origination row that
    :func:`._loan_posting._reject_installment_move_before_loan` deliberately
    leaves editable in every other respect.

    Args:
        template: The updated rule-less TransferTemplate, its new field values
            already applied and flushed.
        transfers: Its live, rule-owned transfers -- projected, not overridden
            and not soft-deleted, which the caller selects.

    Returns:
        The ids this pass RETAINED: rows whose accounts the definition moved
        and which carry the owner's own records, left exactly as found.

    Raises:
        NotFoundError: From ``transfer_service.update_transfer``.
        ValidationError: From ``transfer_service.update_transfer``.
    """
    work = classify_unruled_work(
        transfers,
        with_records=_rows_holding_owner_records(transfers),
        reattributed=_rows_the_definition_reattributes(transfers, template),
    )
    for xfer in work.update:
        changed = {
            field: value
            for field, value in _derive_unruled_fields(
                template, xfer,
            )._asdict().items()
            if getattr(xfer, field) != value
        }
        if changed:
            # The DEFINITION is speaking, not a human.  It used to say so with
            # an ``amount_authored=False`` kwarg riding beside a figure; the
            # figure IS the statement now -- ``changed`` carries an
            # ``amount_ownership`` and nothing else can express authorship
            # (ruling **R-BAL11**, plan step X-au-f).  What the old kwarg
            # guarded against -- a caller meaning the opposite and silently
            # discarding an owner's figure -- has no shape left to take: there
            # is no figure here to be unauthored.
            transfer_service.update_transfer(
                xfer.id, template.user_id, **changed,
            )
    return work.retained_ids


def generate_for_template(template, schedule, scenario_id, effective_from=None):
    """Generate transfers for a template across a pay-period window.

    Args:
        template:       A TransferTemplate with a loaded recurrence_rule.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`
                        -- their whole pay-period schedule plus the window this
                        pass may write into.
        scenario_id:    The scenario to generate into.
        effective_from: Optional date -- only generate for periods ending on or
                        after this date.  ``None`` applies no lower bound.

    Returns:
        List of newly created Transfer objects.
    """
    # Resolve the shared gating + occurrence-matching preamble (cross-user
    # defense, rule-present gating, the occurrence walk against the OWNER's
    # schedule, and the narrowing to this pass's window) via the transaction
    # engine's helper -- the transfer engine is a deliberate parallel and must
    # apply the rule identically.  A None result means generate nothing.  See
    # recurrence_engine.resolve_generation_plan.
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user transfer recurrence generation",
    )
    if plan is None:
        return []

    # WHICH occurrences still need a transfer is the shared decision
    # (``recurrence_engine._pass.create_for_unclaimed_occurrences``); what a row IS
    # is this engine's, and that split is the whole of it since plan step
    # balance:X-au-d.
    def _new_row(period, occurrence):
        """Build one generated transfer for *occurrence*.

        Every derived column comes from the ONE statement of them
        (:class:`DerivedTransferFields`), which the maintain pass assigns onto
        an existing row from the same definition.  The four fields that are NOT
        in it say what this row IS rather than what the template says: whose it
        is, where it sits, that it is the rule's own row, and that it is not
        yet an actual event.

        The due date inside comes from ``recurrence.compute_due_date``,
        the same shared helper the transaction engine uses: a rule that
        schedules on a day of the month (monthly, quarterly, and -- via
        routes/loan/payment_transfer.py -- the mortgage payment, whose rule
        fires on the loan's payment day) is due on the occurrence itself, so
        the calendar/dashboard match the loan card's true monthly due date.
        Rules without one (every-paycheck, every-N) are due on the funding
        paycheck's payday (plan step R5-a, rulings R-R94 / R-R95).

        Args:
            period: The :class:`~app.services.pay_calendar.DerivedPeriod` the
                transfer is funded in.
            occurrence: The date this transfer answers.

        Returns:
            The created :class:`~app.models.transfer.Transfer`.
        """
        return _create_from_definition(
            _derive_row_fields(template, plan.rule, occurrence, period),
            template, PlacedRow(period.period_id, occurrence),
            scenario_id, plan.projected_id,
        )

    created = create_for_unclaimed_occurrences(
        _selector(template, scenario_id), plan, _new_row,
    )

    db.session.flush()
    log_event(
        logger, logging.INFO, EVT_TRANSFER_RECURRENCE_GENERATED, BUSINESS,
        "Transfers generated from template",
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        count=len(created),
    )
    return created


def regenerate_for_template(template, schedule, scenario_id, effective_from=None):
    """Bring a template's future transfers into line with its definition.

    Run when a recurring transfer's amount, accounts, schedule or fields change.
    **It MAINTAINS the rows the rule still names rather than destroying and
    rebuilding them** (plan step R10-b, ruling **R-R19**), which is what the
    transaction engine's twin has done since plan step R10-a.

    Four outcomes, one per period the pass considers:

      1. the rule names the period and an auto-generated transfer is there --
         the fields that DIFFER from :class:`DerivedTransferFields` are applied
         through ``transfer_service.update_transfer``, which reconciles the
         ledger;
      2. the rule names the period and nothing is there -- a transfer is
         created, exactly as :func:`generate_for_template` would;
      3. the rule NO LONGER names the period -- the transfer is removed through
         ``transfer_service.delete_transfer`` if it carries nothing, and
         RETAINED as a conflict if the owner has records against it;
      4. the transfer answers NO occurrence at all (``occurs_on`` is NULL) --
         it is left exactly as found and RETAINED as a conflict (finding
         **REC-516**).  This engine is where that matters most: its retire arm
         hard-deletes the transfer.  The app's last writer of an undated
         transfer a definition links was
         ``routes/transfers/_instances._materialize_one_time_transfer``,
         until plan step ``balance:X-ci-1`` dated its birth (ruling
         **R-BAL94**); its live writer of an undated row a definition links
         is a TRANSACTION one, carry-forward's rolled-forward ``is_override``
         row (``carry_forward_service._execute``).  Undated transfers still
         stand -- one-time transfers born before X-ci-1 and a recurring
         definition's pre-R17 legacy rows among them (R-BAL94) -- so the arm
         is live.

    Overridden and soft-deleted rows are conflicts wherever they sit, as
    before; immutable rows are never touched.

    **What the old sweep destroyed, and it was not only churn.**  It hard-deleted
    every non-overridden generated transfer in the window and re-created it.  A
    transfer holds no purchases -- ``entry_service`` refused its shadow rows --
    which is why this leaf came after the transaction one, but two owner-held
    facts still went with each deletion: the transfer's ``notes``, which
    ``create_transfer`` never receives, and a settlement RECORD retained through
    a revert (each leg's covering movement, kept un-dated by the status seam
    while the assertion is released, because the full-edit popover tells the
    owner to revert in order to edit).  Both were reproduced through this code
    path on a production clone, destroyed by a rename with no prompt and a
    conflict count of zero.

    **Maintaining is not a behaviour change for a transfer with nothing on it,
    and that is measured rather than hoped for.**  ``Projected`` is the ONLY
    non-immutable status in ``ref.statuses``, so this pass can reach nothing
    else, and an update writes exactly the values a re-creation would have
    written -- on a production clone all four live recurring templates came back
    with ZERO fields differing across 99 sweepable rows, which is 99 rows the
    old sweep destroyed and rebuilt to write what was already there.

    **Only the fields that actually differ are sent, and the pass is silent when
    none do.**  ``update_transfer`` is a heavier door than an ORM assignment: it
    reconciles the posting ledger, re-derives a loan's genesis when either
    endpoint is one, bumps the pair's optimistic-lock counter and emits an audit
    row.  On the two live loan-payment templates the old sweep spent 1.58 s and
    1.14 s doing that work twice per row -- once to reverse the deleted payment
    and once to post its replacement -- for an edit that moved no figure at all.

    Args:
        template:       The updated TransferTemplate.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The target scenario.
        effective_from: Date from which to maintain, or ``None`` for the whole
                        write window.  The row select and the rule take it
                        unchanged, and read it against the same derived end --
                        see ``recurrence_engine.regenerate_for_template``.

    Returns:
        List of newly created Transfer objects.  Rows this pass UPDATED are not
        in it, so the value keeps the meaning every caller already reads it
        with.

    Raises:
        RecurrenceConflict: When rows exist that this pass must not change
            unasked.  The caller should catch it, present the options, and call
            :func:`resolve_conflicts`.
    """
    return regenerate_definition(
        _PASS, template, schedule, scenario_id, effective_from,
    )


def preview_regeneration_for_template(
    template, schedule, scenario_id, effective_from=None,
) -> RegenerationPreview:
    """Return what :func:`regenerate_for_template` would do to existing transfers, unwritten.

    The pass's own decision, read and not applied
    (:func:`~app.services.recurrence_engine.preview_regeneration`): the
    transfers it would bring into line, with the fields it would send
    ``transfer_service.update_transfer`` for each, and those it would
    delete.  Read by the edit door's stranded-row refusal before the save
    regenerates (plan step ``pay_calendar:C18-a``).

    Args:
        template: The edited TransferTemplate.
        schedule: See :func:`regenerate_for_template`.
        scenario_id: The scenario the save would regenerate.
        effective_from: See :func:`regenerate_for_template`.

    Returns:
        The :class:`~app.services.recurrence_engine.RegenerationPreview`.
    """
    return preview_regeneration(
        _PASS, template, schedule, scenario_id, effective_from,
    )




def _rows_holding_owner_records(existing) -> "set[int]":
    """Return the ids of transfers carrying content their template did not put there.

    "The owner's own records" is what finding **N-292** is about, and the
    transfer table's answer differs from the transaction table's in both
    directions.  A transfer holds no PURCHASES -- ``entry_service.create_entry``
    refused its shadow rows outright, and a side link holds only a payment
    record (``ck_transaction_entries_side_link_is_a_record``) -- which is why
    this engine was not part of plan step R10-a.  What it does hold is:

      - ``notes`` on the parent: free text the owner typed, which no writer
        derives and which ``create_transfer`` is never handed, so every
        delete-and-recreate dropped it silently;
      - a SETTLEMENT RECORD on either leg.  ``status_seam.apply_status_change``
        releases the ASSERTION on the way out of the settled band
        (``settled_on``, ``reconciled_by_id``) and deliberately KEEPS what
        moved -- the leg's COVERING MOVEMENT, un-dated (plan step X-bi-3e-2)
        -- because the two are different facts with different lifetimes (plan
        step X-au-c3).  So a transfer the owner settled and then reverted in
        order to edit -- which is what the full-edit popover instructs -- is
        Projected, not overridden, and mutable to this sweep while still
        recording a figure read off a bank statement.  Reproduced on a
        production clone: `$321.45` on both legs, destroyed by a rename.  A
        transfer settled at ``$0.00`` keeps no movement and is retained by
        nothing across a revert (ruling **R-BAL82**).  The predicate read the
        legs' own ``settled_basis_id`` from plan step X-au-c3 until
        ``balance:X-bi-4b-2`` deleted that column (ruling **R-BAL80**): the
        same fact in two homes, and this was one of the readers keeping them
        in step.
    **A STATEMENT LINK is no arm of its own since plan step
    ``balance:X-bi-6-4d-2``.**  ``reconciled_by_id`` records which statement
    was seen to show a side's money, and it makes an ACCOUNT move unsafe
    (``fk_transaction_entries_reconciled_by`` scopes it BY ACCOUNT) -- but a
    statement links a side's payment RECORD
    (``transfer_service.record_leg_clearing``), so every linked side holds a
    record, which the record arm above already keeps, and a ``$0.00`` close
    holds neither (ruling **R-BAL141**).  The arm plan step
    ``balance:X-bi-4b-2`` put back read a link on a twin row, which
    ``X-bi-6-4d-2`` stopped storing; its test went with it (the developer's
    2026-10-09 rule-5 answer).  The DAY's basis
    needs no arm of its own: its pairing is a BICONDITIONAL over
    ``settled_on`` (``ck_transaction_entries_settle_day_basis_pairing``), so any row
    this predicate can see carries it, and a condition no row can satisfy
    alone is untestable by construction -- the kind of guard this project
    has repeatedly found sitting green over nothing.

    **Both legs are asked in ONE query, not one per row.**  A regeneration
    considers every future transfer of a template -- 62 of them on one live
    template on a production clone -- so reading each transfer's records in the
    classifier would issue a query per row on the hot path of every template
    edit.  The query is :func:`app.services.transfer_legs.transfers_holding_records`
    since leaf ``balance:X-bi-6-4c-2``, so the one join from a transfer to its
    sides' records answers it (plan step ``balance:X-bi-6-4d-2`` moved that
    join onto the side links); a side link holds only a payment record
    (``ck_transaction_entries_side_link_is_a_record``), so a record is every
    entry a side can hold.

    Args:
        existing: The transfers this pass is considering.

    Returns:
        The subset of their ids that hold a note, or a payment record on
        either side (which carries any statement link).
    """
    holding = transfer_legs.transfers_holding_records(
        xfer.id for xfer in existing
    )
    for xfer in existing:
        # ``notes`` is free text the owner typed and no writer derives; a
        # whitespace-only note is not a record worth blocking an edit over.
        if xfer.notes is not None and xfer.notes.strip():
            holding.add(xfer.id)
    return holding




def _rows_the_definition_reattributes(existing, template) -> "set[int]":
    """Return the ids of transfers whose ENDPOINTS this template has moved.

    The transfer half of what
    :func:`_recurrence_common.classify_maintain_work` needs and cannot ask for
    itself.  A transaction has ONE account; a transfer has two, and each side's
    record is filed against its own -- so "the definition moved where this
    row's records are filed" is a question about the PAIR.

    The move itself is applicable: ``transfer_service.update_transfer`` carries
    a transfer and both legs between accounts (plan step R10-b), and a transfer
    carrying nothing of the owner's follows its definition freely, which is the
    ordinary case and the behaviour the old sweep had.  What is NOT applicable
    is doing it to a row that RECORDS something: a settled leg's figure is what
    moved between the OLD two accounts, and its statement link is scoped by
    account (``fk_transaction_entries_reconciled_by``), so re-pointing the pair would
    re-file both against accounts nobody asserted them on.  Those rows are
    retained and reported instead.

    Args:
        existing: The transfers this pass is considering.
        template: The TransferTemplate, holding its endpoints NOW.

    Returns:
        The subset of their ids sitting on a different pair of accounts.
    """
    endpoints = (template.from_account_id, template.to_account_id)
    return {
        xfer.id for xfer in existing
        if (xfer.from_account_id, xfer.to_account_id) != endpoints
    }




def _create_from_definition(
    fields, template, placed, scenario_id, projected_id,
):
    """Create one transfer from *fields*.

    The ONE create in this engine, shared by :func:`generate_for_template` and
    the maintain pass's create arm so the two cannot come to build a generated
    transfer two ways.  Routed through ``transfer_service.create_transfer``,
    which every write of a transfer goes through (``CLAUDE.md``, Architecture).

    Args:
        fields: The :class:`DerivedTransferFields` for this (template, period)
            pair.
        template: The TransferTemplate the row is linked to.
        placed: The :class:`~app.services._recurrence_common.PlacedRow` -- which
            paycheck funds this row and which occurrence it answers.  It is ONE
            argument rather than two so neither call site can pair a period with
            another period's occurrence, and so this door stays inside pylint's
            five-argument ceiling (plan step **R17**).
        scenario_id: The scenario it is written into.
        projected_id: The ``Projected`` status id.

    Returns:
        The created :class:`~app.models.transfer.Transfer`.
    """
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=template.user_id,
            pay_period_id=placed.period_id,
            occurs_on=placed.occurs_on,
            scenario_id=scenario_id,
            status_id=projected_id,
            transfer_template_id=template.id,
            **fields._asdict(),
        ),
    )




def _apply_maintain_work(work, derived, template, scenario_id, projected_id):
    """Write one classified maintain pass, through the transfer service.

    The only writer in this engine's maintain path, and every one of its three
    arms goes through ``transfer_service``, which ``CLAUDE.md`` (Architecture)
    sends every write of a transfer through: a maintain pass that assigned to a
    transfer's columns would skip its refusals and the copies it keeps in step,
    the posted ledger's among them.  That is the difference from the
    transaction twin's applier, which assigns fields itself.

    **One field's derived value can disagree with what the CREATE path stored,
    and it is bounded rather than guarded** (adversarial review of R10-b).
    ``create_transfer`` defaults a row's name from the endpoints when the spec
    states none (``spec.name or ...``), so a template named ``""`` would produce
    a row named "A to B" and this diff would send ``name=""`` on every pass
    forever -- a full ``update_transfer`` per row, per edit, permanently.  Both
    transfer-template schemas bound ``name`` at ``Length(min=1)`` and the column
    is NOT NULL, so no door can author it; the column carries no CHECK, which is
    what would make it structural.

    **The update arm sends the fields that DIFFER, and skips a row entirely when
    none do.**  ``update_transfer`` reconciles the posting ledger, re-derives a
    loan's genesis when an endpoint is one, moves the pair's optimistic-lock
    counter and emits an audit row, so calling it for a row already equal to its
    definition is not a cheap no-op -- it is all of that work, per row, for
    nothing.  On a production clone every one of the 99 sweepable rows across
    the four live templates came back with zero fields differing.

    **A loan-payment update could move the rule's own closing bound mid-pass
    until plan step R7d-g, and that was a NOTE rather than a defect**
    (adversarial review of R10-b).  ``update_transfer`` on a loan payment
    reached ``loan_recurrence_sync.sync_recurring_payment_bounds``, which
    re-authored ``rule.end_date`` from the newly derived payoff -- while
    *work* and *derived* were frozen before the first write -- so raising a
    loan payment's amount (payoff moves earlier) let this pass finish against
    the bound it started with, self-healing on the next regeneration.  R7d-g
    deleted that writer: the closing bound is derived on every read and no
    row write moves a stored one, so *derived* is frozen against nothing that
    a pass can change.

    Args:
        work: The :class:`~app.services._recurrence_common.MaintainWork` from
            :func:`~app.services._recurrence_common.classify_maintain_work`.
        derived: ``{pay_period_id: DerivedTransferFields}`` for every period the
            rule names -- the single statement of what a generated transfer's
            definition says, consumed identically by the update and the create.
        template: The TransferTemplate being maintained; supplies the owner
            every service call is checked against, and the link every created
            row carries.
        scenario_id: The scenario every created row is written into.
        projected_id: The ``Projected`` status id for created rows.  ``None``
            only when the rule was cleared, in which case *work.create_in* is
            empty and it is never read.

    Returns:
        ``(created, updated)`` -- the rows this pass added and the rows it
        brought into line.
    """
    updated = []
    for xfer in work.update:
        changed = {
            field: value
            for field, value in derived[xfer.occurs_on]._asdict().items()
            if getattr(xfer, field) != value
        }
        if not changed:
            continue
        # The DEFINITION is speaking; see the propagate pass above.  The
        # statement rides IN ``changed`` since ruling **R-BAL11** -- an
        # ``amount_ownership`` naming TEMPLATE -- rather than beside it as an
        # authorship kwarg, which is what makes the diff and the write one
        # value instead of a figure plus a claim about it.
        transfer_service.update_transfer(
            xfer.id, template.user_id, **changed,
        )
        updated.append(xfer)

    created = [
        _create_from_definition(
            derived[create.occurs_on], template, create,
            scenario_id, projected_id,
        )
        for create in work.create_in
    ]

    # **This is the ONLY path here that deletes**, and it is reached only when
    # the rule stopped naming the row's OCCURRENCE (plan step R17) AND the
    # transfer carries nothing
    # of the owner's.  Routed through the canonical hard-delete path (Transfer
    # Invariant 4): ``delete_transfer`` reverses any posted cash effect while
    # the rows still exist to link against, re-syncs a loan the payment left
    # (its split links no row, ruling **R-BAL102**) and emits
    # ``EVT_TRANSFER_HARD_DELETED`` per deletion; its orphan self-check went
    # with the twins at plan step ``balance:X-bi-6-4d-2``.  See audit B6-03 / LOW-02.
    for xfer in work.retire:
        transfer_service.delete_transfer(xfer.id, template.user_id, soft=False)

    db.session.flush()
    return created, updated




#: The acts a regeneration performs that are THIS engine's own -- what
#: :class:`~app.services.recurrence_engine.MaintainActs` names, and everything
#: about a regeneration that a transfer and a transaction do not share.  The
#: body that performs them is
#: :func:`~app.services.recurrence_engine.regenerate_definition`, ONE function
#: for both engines since plan step balance:X-au-d -- which is where this
#: module's own copy of it went, along with the ``duplicate-code`` disable that
#: copy needed and the comment pointing at the transaction engine's twin.
_PASS = MaintainActs(
    PassReporting(
        logger, "Blocked cross-user transfer recurrence regeneration",
        EVT_TRANSFER_RECURRENCE_REGENERATED,
        "Transfer recurrence regenerated for template",
    ),
    _selector, _derive_row_fields, _rows_holding_owner_records,
    _rows_the_definition_reattributes, _apply_maintain_work,
)


#: How this engine's resolution announces itself.
_RESOLVED = ConflictReporting(
    logger, EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED,
    "Transfer recurrence conflicts resolved (update)",
)


def resolve_conflicts(
    transfer_ids: Sequence[int], action: str, ctx: BalanceContext,
) -> str | None:
    """Resolve override/delete conflicts after a regeneration.

    Routes all mutations through the transfer service, which every write
    of a transfer goes through (``CLAUDE.md``, Architecture).  Soft-deleted
    transfers are restored via ``transfer_service.restore_transfer`` before
    updating --
    only those the template's unarchive would restore (ruling
    **R-BAL253**): a deleted transfer its books hold stays deleted,
    untouched, and is named.

    Each transfer is ownership-checked via its direct ``user_id`` column
    before any modification -- transfers the pass's owner does not own are
    silently skipped (defense-in-depth against IDOR).

    Args:
        transfer_ids: List of Transfer IDs to resolve.
        action:       'update' -- hand the row back to its definition.
                      'keep' -- leave the transfer unchanged.
        ctx:          The edit's read pass; its ``user_id`` is the
                      requesting owner's, and transfers it does not own
                      are skipped.

    Returns:
        The sentence naming the picked transfers left deleted
        (:attr:`~app.services.definition_unarchive.UseRestore.notice`), or
        ``None`` when none is -- always ``None`` for "keep".

    **It took a ``new_amount`` until plan step X-au-f** (ruling **R-JD**), and
    the transaction twin lost the same parameter at X-au-e for the same reason.
    "Use the new value" handed each row the template's figure to STORE; a
    generated transfer stores no figure now, so the offer is *hand this row
    back to its definition* and the definition's own effective-dated series
    prices it as of the row's due date.  Passing a figure here would have
    re-declared the row OWN, which is the state this cutover deletes.

    **It takes the edit's READ PASS rather than an owner id**, as its twin
    does and for the same reason: which deleted rows come back is asked of
    the books, the rule and the calendar the save leaves
    (:func:`~app.services.definition_unarchive.restored_by_use`, the ONE rule
    both engines ask), and the owner is that pass's.
    """
    if action == "keep":
        log_event(
            logger, logging.INFO,
            EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Transfer recurrence conflicts kept (no mutation)",
            user_id=ctx.user_id, action=action,
            transfer_id_count=len(transfer_ids),
        )
        return None

    if action == "update":
        skipped_count = 0
        picked = []
        for xfer_id in transfer_ids:
            xfer = db.session.get(Transfer, xfer_id)
            if xfer is None:
                skipped_count += 1
                continue

            # Ownership check: Transfer has a direct user_id column.
            if xfer.user_id != ctx.user_id:
                log_resource_access_denied(
                    logger,
                    user_id=ctx.user_id,
                    model="Transfer",
                    pk=xfer_id,
                    owner_id=xfer.user_id,
                )
                skipped_count += 1
                continue
            picked.append(xfer)

        # **What the unarchive would leave deleted, "use" leaves deleted**
        # (ruling **R-BAL253**): asked once, of every picked transfer as it
        # stands, through the one rule the transaction twin asks too.  A
        # transfer the answer holds is left exactly as found, and so is one
        # that names no definition (ledger row **N-440**, which this resolver
        # lacked until the guard moved into the rule).
        use = restored_by_use(picked, ctx)
        for xfer in use.handed_back:
            # Soft-deleted transfers must be restored before they can
            # be updated.  restore_transfer sets is_deleted=False on the
            # transfer.
            if xfer.is_deleted:
                transfer_service.restore_transfer(xfer.id, ctx.user_id)

            # The owner is handing the pair BACK to its definition, and since
            # ruling **R-BAL11** that is ONE statement rather than a flag plus
            # a figure plus a claim about who authored it: the ownership names
            # TEMPLATE, so the transfer stores nothing and reads its series,
            # and both legs read the transfer.  ``is_override=False`` rides
            # with it still, because the flag means *this row is the OWNER's,
            # not the rule's* (plan step X-au-h) and this act clears exactly
            # that -- it no longer says anything about the amount.
            transfer_service.update_transfer(
                xfer.id, ctx.user_id, is_override=False,
                amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
            )

        db.session.flush()
        log_use_resolved(_RESOLVED, ctx, use, skipped_count)
        return use.notice
    return None
