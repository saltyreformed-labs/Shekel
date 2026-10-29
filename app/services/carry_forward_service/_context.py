"""Shared setup and target-row helpers for the carry-forward service.

Both the mutating path (``carry_forward_unpaid`` in ``_execute``) and the
read-only path (``preview_carry_forward`` in ``_preview``) start from the
same validated periods and three-way-partitioned source items -- envelope
rows, discrete rows and TRANSFERS -- produced once here so the two paths can
never diverge.  The envelope target-row
lookup and the "finalised target" reasoning also live here so the
preview (which predicts) and the execution (which acts) reason about the
target canonical row from a single source of truth.
"""

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    AmountBasis,
    amount_basis,
    resolve_transaction_amount,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.loan_ledger import LoanCalendars
from app.services.one_off import another_row_answers, due_date_for
from app.services.pay_calendar import DerivedPeriod
from app.utils.amount_relationships import transfer_pricing_load_options
from app.utils.balance_predicates import is_projected_clause


@dataclass(frozen=True)
class _CarryForwardContext:  # pylint: disable=too-many-instance-attributes
    """Internal: validated periods + partitioned source rows.

    Built by ``_build_carry_forward_context``; consumed by both
    ``carry_forward_unpaid`` and ``preview_carry_forward`` so the two
    paths see exactly the same partition (DRY: the partition logic
    lives once).

    Pylint: ``too-many-instance-attributes`` (9/7) -- these nine ARE one
    carry-forward request: the two validated periods, who and which scenario
    they belong to, the three-way partition of what moves, the
    pay-period schedule the envelope branch resolves against, and the amount
    basis it prices through.  Splitting them
    would put one request's facts in two objects both paths must then keep in
    step, which is the coupling this value exists to remove.  Mirrors the
    :class:`~app.services.recurrence.ResolvedRecurrence` precedent.
    """

    # Typed since plan step balance:X-bi-7b, where they were ``object`` with
    # the type in a comment: the re-placing arm reads ``start_date`` off both.
    source_period: DerivedPeriod
    target_period: DerivedPeriod
    user_id: int
    scenario_id: int
    # The source period's still-Projected TRANSFERS, in id order (plan step
    # balance:X-bi-6-4c-2).  It was ``shadow_txns``, the transfers' SHADOW
    # rows, which the two paths de-duplicated back to transfers by
    # ``transfer_id`` -- walking the parents is ruling R-BAL86's own words.
    transfers: List[Transfer]
    # The source period's rows, partitioned, each in id order (leaf 5c-2c-2).
    envelope_txns: List[Transaction]
    discrete_txns: List[Transaction]
    # The owner's pay-period schedule, its write window narrowed to the ONE
    # target period, resolved once for the whole request (plan step R4b-1).
    # Every envelope row asks the recurrence engine the same question about
    # the same target, and building this per row would repeat the schedule
    # query -- and the forward occurrence walk -- once per row.
    schedule: object  # GenerationSchedule
    # The request's amount basis (plan step X-au-c2b), for the same reason and
    # on the same terms: a rollover's leftover is the source row's BUDGET minus
    # what was spent, and its top-up adds that to the target's, so both ends
    # read what the row's amount RESOLVES to rather than the column a derived
    # row does not carry.  Pinned to ``user_id`` / ``scenario_id`` above, so it
    # is those two facts in the form the amount model takes them.
    basis: AmountBasis


def _build_carry_forward_context(source_period_id, target_period_id,
                                 scenario_id, balance_ctx):
    """Validate periods, query projected source rows, three-way partition.

    Pure read-only setup shared by ``carry_forward_unpaid`` (mutating)
    and ``preview_carry_forward`` (read-only).  Raises ``NotFoundError``
    if either period is missing or not the calendar owner's -- both
    callers want the same security response (404 at the route layer).

    The same-period short-circuit (``source == target``) returns an
    empty partition so callers can no-op cleanly without special
    casing in the loops.

    **Both periods are ANSWERED BY THE CALENDAR, and that is what makes the
    ownership check structural** (pay-calendar plan step C2-f3c).  Each was a
    ``db.session.get(PayPeriod, ...)`` followed by a hand-written
    ``row.user_id != user_id`` comparison -- correct, but a comparison a later
    edit could drop, and one this module had to remember to write twice.  A
    calendar is one owner's whole schedule and nothing else, so a period id
    belonging to anybody else is simply not in it: "no such period" and "not
    yours" are the same answer here because they are the same question, which
    is the project's 404-for-both security rule expressed as a lookup rather
    than as a guard.  It also drops two queries from every carry-forward
    render.

    Args:
        source_period_id: pay_period.id to carry forward FROM.
        target_period_id: pay_period.id to carry forward TO.
        scenario_id: scenario filter (mirrors the mutating path).
        balance_ctx: The request's
            :class:`~app.services.balance_at.BalanceContext`, opened once by
            the route.  It carries the owner, so no ``user_id`` rides beside
            it -- two spellings of one fact with nothing reconciling them is
            the shape ruling P54 rejected -- and its calendar answers both
            period lookups.  **It took the calendar itself until plan step
            R7d-c-1**, which moved the derivation onto the pass so the value
            the recurrence prediction resolves against and the value that
            answers these lookups cannot be two different schedules.

            **The SCENARIO still rides beside it, and that is deliberate
            rather than an oversight the owner sentence above covers** -- an
            adversarial review of R7d-c-1 read the two claims together and was
            right to.  *scenario_id* is what this operation carries forward
            WITHIN; the pass pins the owner's BASELINE.  Today every caller
            passes ``balance_ctx.scenario_id`` and the two are the same value,
            so :meth:`~app.services.balance_at.BalanceContext.amounts` would
            answer the *basis* below identically -- but it would answer it for
            the BASELINE whatever this argument said, which is a different
            rule silently substituted.  The same pairing the recurrence
            engines already take (``scenario_id`` beside a
            ``GenerationSchedule``), and collapsing it is a decision about
            what a non-baseline carry-forward MEANS rather than a rename.

    Returns:
        _CarryForwardContext.

    Raises:
        NotFoundError: if either period is missing or not the calendar
            owner's.
    """
    user_id = balance_ctx.user_id
    calendar = balance_ctx.calendar()

    source = calendar.period_by_id(source_period_id)
    if source is None:
        raise NotFoundError(f"Source pay period {source_period_id} not found.")

    target = calendar.period_by_id(target_period_id)
    if target is None:
        raise NotFoundError(f"Target pay period {target_period_id} not found.")

    # ONE schedule for the whole request, its window narrowed to the target
    # period the envelope rollovers write into (plan step R4b-1).  Every
    # envelope row asks the recurrence engine the same question about the same
    # target; building this per row would repeat the forward occurrence walk
    # once per row.  It is built from the pass the route already opened, so
    # the request holds ONE derivation of the owner's schedule rather than
    # the two plan ledger row **P68** measured.
    schedule = GenerationSchedule.for_period_ids(
        balance_ctx, {target.period_id},
    )
    # ONE basis for the whole request, on the same terms as the schedule above:
    # every envelope row is priced against the same owner and scenario, and it
    # resolves nothing until the first row asks.  Built OVER the pass's pricer
    # (plan step salary:C12) under the CARRIED scenario, which is why it is not
    # ``balance_ctx.amounts()`` -- see the docstring on *scenario_id*.
    # Its loan calendars are its own, as its pricer's memo was before plan step
    # recurrence:R25: the pass's are private to it, with no accessor that
    # would hand out a memo keyed by a bare loan id (BalanceContext).
    basis = amount_basis(balance_ctx.paychecks(), scenario_id, LoanCalendars())

    if source_period_id == target_period_id:
        return _CarryForwardContext(
            source_period=source,
            target_period=target,
            user_id=user_id,
            scenario_id=scenario_id,
            transfers=[],
            envelope_txns=[],
            discrete_txns=[],
            schedule=schedule,
            basis=basis,
        )

    # Routed through ``is_projected_clause`` (D6-09 / MED-02) so the
    # source-period projected-only query and the two discrete bulk UPDATEs
    # (rows of a recurring definition, and the rest) share one definition of
    # the rule with every other Projected SQL filter.  A transfer is not a
    # row here: it is loaded below and moves whole, both sides with it.
    projected_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id == source_period_id,
            Transaction.scenario_id == scenario_id,
            is_projected_clause(Transaction),
            Transaction.is_deleted.is_(False),
        )
        # ORDERED, as the transfers below are: the modal lists the rows in
        # this order and names a match several envelopes share under the
        # FIRST of them (``_preview._what_each_close_frees``, plan step
        # ``credit_card:CC-5-4a-5``, leaf 5c-2c-2), so an unordered read could
        # move that sentence between the modal and its redraw (the leaf's
        # review, L5).  What the batch writes does not depend on it -- the
        # order was arbitrary until then -- only which row a refusal names
        # first.
        .order_by(Transaction.id)
        .all()
    )

    envelope_txns: List[Transaction] = []
    discrete_txns: List[Transaction] = []
    for txn in projected_txns:
        if txn.template_id is not None and txn.tracks_purchases:
            # Envelope ROLLOVER folds the unspent leftover into the
            # definition's next-period row: a RECURRING definition's canonical
            # (created via recurrence_engine.generate_for_template) or, where
            # the rule names no row there, a fresh override row; a RULE-LESS
            # definition's placed row there, or one PLACED for it through
            # ``one_off.place_row_of`` (ruling **R-BAL44**, plan step
            # balance:X-bi-7b leaf 7b-3).  Plan step X-bi-7a keyed this on
            # ``recurs`` -- a rule-less envelope moved WHOLE -- because a
            # second row of a rule-less definition contradicted R-BAL20's
            # *one placed occurrence*; R-BAL24 amended that to *its placed
            # rows*, one per paycheck, and a rule-less envelope carried whole
            # into a paycheck already holding its sibling collided on the
            # occurrence index (finding **BAL-496**).  So the rollover is
            # every ENVELOPE OF A DEFINITION's.  A LEGACY link-less envelope
            # fell through to the discrete branch and moved WHOLE with its
            # entries until the family's cutover (``X-bi-7d-2``) minted
            # every one a definition; the only link-less row that reaches
            # the discrete branch now is a CC payback, which tracks no
            # purchases.  The envelope half reaches the one accessor rather
            # than restating ``template.is_envelope`` beside it (plan step
            # balance:X-bi-1, ruling R-IZ); the link is read first, so a
            # link-less row costs no load.
            envelope_txns.append(txn)
        else:
            discrete_txns.append(txn)

    # The source period's TRANSFERS, walked as transfers (plan step
    # balance:X-bi-6-4c-2; ruling R-BAL86: carry-forward walks
    # ``budget.transfers``).  The same four clauses the rows take, asked of the
    # PARENT -- whose period, scenario, status and soft-delete a shadow only
    # copied (Transfer Invariant 3) -- plus the owner, which the period already
    # implies and a transfer states directly.  ORDERED, which the shadow query
    # this replaced was not: the modal listed the transfers in whatever order
    # it returned their shadows and labelled each by whichever shadow came
    # first (finding **BAL-546**).  The loads are what both paths read: the
    # pricing chain the preview's figure resolves through and the endpoints its
    # label is composed from.
    transfers = (
        db.session.query(Transfer)
        .options(
            *transfer_pricing_load_options(),
            selectinload(Transfer.from_account),
            selectinload(Transfer.to_account),
        )
        .filter(
            Transfer.user_id == user_id,
            Transfer.pay_period_id == source_period_id,
            Transfer.scenario_id == scenario_id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
        .order_by(Transfer.id)
        .all()
    )

    return _CarryForwardContext(
        source_period=source,
        target_period=target,
        user_id=user_id,
        scenario_id=scenario_id,
        transfers=transfers,
        envelope_txns=envelope_txns,
        discrete_txns=discrete_txns,
        schedule=schedule,
        basis=basis,
    )


def _target_canonical_rows(source_txn, target_period_id, scenario_id, *,
                           include_deleted):
    """Return the target period's rows for *source_txn*'s template+scenario.

    Single-sources the ``(template_id, pay_period_id, scenario_id)``
    target lookup so the preview and the execution can never query
    different rows.  The preview path passes ``include_deleted=True``
    (it must distinguish "no row at all" from "only soft-deleted rows",
    a known engine-skip condition); the mutating path passes
    ``include_deleted=False`` (it bumps a live row).

    Args:
        source_txn: The source transaction; its ``template_id`` is read.
        target_period_id: The ``budget.pay_periods.id`` the canonical lives
            in.  An id rather than a period since pay-calendar plan step
            C2-f3c: it is the only thing this ever read off one.
        scenario_id: Scenario filter for the lookup.
        include_deleted: When False, soft-deleted rows are excluded in
            SQL (the mutating path); when True, they are returned so the
            caller can inspect them (the preview path).

    Returns:
        A list of matching Transaction rows.
    """
    query = db.session.query(Transaction).filter(
        Transaction.template_id == source_txn.template_id,
        Transaction.pay_period_id == target_period_id,
        Transaction.scenario_id == scenario_id,
    )
    if not include_deleted:
        query = query.filter(Transaction.is_deleted.is_(False))
    return query.all()


def _is_finalised(target_row):
    """True if *target_row* cannot receive a rollover bump.

    A row is finalised when it has no status or an immutable one (Paid,
    Received, Credit, Cancelled).  Bumping it would silently
    override the user's prior status decision, so both the preview
    (blocks) and the mutating path (raises) gate on this one rule.
    """
    return target_row.status is None or target_row.status.is_immutable


def _leftover_recipient(mutable):
    """Return which of *mutable* receives the leftover, or None to refuse.

    **One paycheck may legitimately hold more than one generated row since plan
    step R17**, so "more than one mutable row" stopped being proof of
    corruption.  A row answers an OCCURRENCE of its template's cadence and the
    unique index is keyed on that
    (``idx_transactions_template_scenario_occurrence``); a cadence that names
    one paycheck twice -- a monthly bill at a pay cadence of 30 days or more, a
    weekly one once plan step R5 makes weekly rules authorable -- now stores
    both rows instead of being refused.  Measured on the developer's own
    templates: at ``cadence_days`` 30 two of them repeat a paycheck, at 31
    eleven of them do across 12 paychecks.

    **The earliest occurrence receives the leftover** (developer ruling,
    2026-08-28): the unspent money is rolling into the paycheck to meet the
    next obligation, and the next obligation is the first occurrence in it.

    **Genuine ambiguity still refuses**, and it is the state the guard was
    written for: two rows answering the SAME occurrence, or an undated row
    beside any other, are states no cadence produces and no pass should guess
    at.  Refusing there is what the old ``len(mutable) > 1`` test meant when
    the index made every multi-row paycheck corrupt.

    Args:
        mutable: The target period's rows that can still receive a bump --
            non-deleted and not finalised, as
            :func:`_classify_leftover_target` selects them.

    Returns:
        The row to top up, or ``None`` when there is none to choose (an empty
        period) or the choice cannot be made honestly.
    """
    if not mutable:
        return None
    if len(mutable) == 1:
        return mutable[0]
    # **An OVERRIDE row disqualifies the whole tie-break**, and an adversarial
    # review of plan step R17 is why this is stated rather than assumed.  The
    # developer ruled the earliest occurrence among rows a CADENCE names; a row
    # the owner moved into this paycheck through the PATCH door is not one of
    # those -- it carries ``is_override = True`` and its own earlier
    # ``occurs_on``, so an occurrence tie-break would select it over the
    # paycheck's own canonical and the caller would then write
    # ``estimated_amount = resolve + leftover`` and clear ``amount_source_id``
    # ON THE FIGURE THE OWNER TYPED.  The old ``len(mutable) > 1`` guard
    # refused that, and nothing in the 2026-08-28 ruling asked for it to stop.
    # Permitting that sibling is the entire reason both indexes are partial on
    # ``is_override = FALSE``, so it is the more reachable producer of a
    # multi-row target, not the rarer one.
    if any(row.is_override for row in mutable):
        return None
    occurrences = [row.occurs_on for row in mutable]
    if any(day is None for day in occurrences):
        return None
    if len(set(occurrences)) != len(occurrences):
        return None
    return min(mutable, key=lambda row: row.occurs_on)


class _TargetKind(enum.Enum):
    """Where an envelope rollover's unspent leftover lands in the target.

    Computed once by :func:`_classify_leftover_target` (read-only) and
    consumed by both the preview (to describe) and the execution (to
    act), so the destination decision lives in exactly one place and the
    two paths can never drift.
    """

    TOP_UP = "top_up"        # exactly one mutable row exists -> bump it
    GENERATE = "generate"    # empty + active template -> engine creates canonical
    CREATE = "create"        # no usable row -> a fresh override row (recurring)
    #                          or a row placed for the definition (rule-less)
    AMBIGUOUS = "ambiguous"  # >1 mutable row, unresolvable -> refuse
    CLOSED = "closed"        # a rule-less definition's one row here has closed


@dataclass(frozen=True)
class _TargetResolution:
    """The destination decision for one envelope leftover rollover.

    Read-only output of :func:`_classify_leftover_target`.

    Attributes:
        kind: One of the :class:`_TargetKind` outcomes.
        row: The row to bump for ``TOP_UP``; for ``CLOSED``, the target's
            finalised row of the definition where that is the arm that
            fired, and ``None`` where a row elsewhere answers the target's
            start (the callers word the refusal by which); ``None``
            otherwise.
        base: The destination row's pre-bump amount -- what the existing row's
            amount RESOLVES to for ``TOP_UP`` (plan step X-au-c2b; it was that
            row's ``estimated_amount`` COLUMN, which a derived row does not
            carry), the template's ``default_amount`` for ``GENERATE``, and
            ``Decimal("0")`` for ``CREATE`` (a fresh row starts empty and the
            caller's bump folds the leftover on top).  ``None`` for
            ``AMBIGUOUS``.
    """

    kind: _TargetKind
    row: Optional[Transaction] = None
    base: Optional[Decimal] = None


def _classify_leftover_target(source_txn, target_period, basis, schedule):
    """Decide where an envelope leftover lands in the destination period.

    Pure read-only classification shared by ``carry_forward_unpaid``
    (which acts on the result) and ``preview_carry_forward`` (which
    describes it), so the destination decision lives in one place and the
    two paths can never diverge.

    The rule -- find a *mutable* (still-Projected) destination row to top
    up; if none exists, create one:

      * ``AMBIGUOUS`` -- more than one mutable row matches ``(template,
        period, scenario)`` and :func:`_leftover_recipient` cannot choose
        between them honestly: they answer the same occurrence, or one of
        them answers none.  The caller refuses rather than guess which open
        row to credit.
      * ``TOP_UP`` -- one mutable row, or several answering DIFFERENT
        occurrences of a cadence that names this paycheck more than once, in
        which case the earliest occurrence is bumped
        (:func:`_leftover_recipient`).
      * ``GENERATE`` -- no rows at all and the template is active in the
        destination; the recurrence engine would create the canonical,
        which the caller then bumps.
      * ``CREATE`` -- otherwise (inactive template, only finalised rows,
        or only soft-deleted rows); the caller creates a fresh override
        row carrying the leftover -- or, for a RULE-LESS definition, PLACES
        a row of it in the target (ruling **R-BAL44**).
      * ``CLOSED`` -- a RULE-LESS definition whose row in the target has
        FINALISED, or a row of it -- the source itself, dated by its owner
        into the target, or any sibling -- that already ANSWERS the
        target's start (``one_off.another_row_answers``, the occurrence
        index's own predicate; the second arm found by 7b-3's adversarial
        review, which dated the source at the target's start and met the
        index).  Such a definition places ONE row per paycheck, answering
        the paycheck's start (rulings **R-BAL24** / **R-BAL25**), so a second
        row there would answer an occurrence a row already does; the
        leftover has nowhere to go and the caller refuses rather than guess.
        A recurring definition in the same state takes ``CREATE`` because
        its fresh row is an override answering no occurrence.

    Uses ``recurrence_engine.can_generate_in_period`` (a read-only
    predictor) rather than ``generate_for_template`` (which would create
    rows), so calling this never has a side effect.

    Args:
        source_txn: The envelope source row being carried forward; its
            ``template`` / ``template_id`` drive the lookup.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        basis: The request's :class:`~app.services.cash_ledger.AmountBasis`;
            its ``scenario_id`` filters the lookup and the recurrence-engine
            prediction, and it prices the TOP_UP row's pre-bump base.
        schedule: The request's
            :class:`~app.services.generation_schedule.GenerationSchedule`
            (``ctx.schedule``), threaded so the prediction resolves against the
            owner's schedule without re-reading it once per envelope row.

    Returns:
        _TargetResolution describing the destination decision.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred import:
    # recurrence_engine and carry_forward_service sit at the same service
    # layer; the deferred form documents the one-way dependency
    # (carry-forward depends on recurrence-engine, never the reverse).
    from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel

    all_rows = _target_canonical_rows(
        source_txn, target_period.period_id, basis.scenario_id,
        include_deleted=True,
    )
    non_deleted = [r for r in all_rows if not r.is_deleted]
    mutable = [r for r in non_deleted if not _is_finalised(r)]

    recipient = _leftover_recipient(mutable)
    if recipient is None and mutable:
        return _TargetResolution(_TargetKind.AMBIGUOUS)
    if recipient is not None:
        return _TargetResolution(
            _TargetKind.TOP_UP, row=recipient,
            base=resolve_transaction_amount(recipient, basis),
        )
    # ``source_txn.template`` is never ``None`` here: the context routes a row
    # into ``envelope_txns`` only when it names a DEFINITION (recurring or
    # rule-less since ruling R-BAL44; recurring alone from plan step
    # balance:X-bi-7a).  ``can_generate_in_period`` answers False for a
    # rule-less definition (its plan resolves to nothing), so such a
    # definition never takes GENERATE.
    if (not non_deleted
            and recurrence_engine.can_generate_in_period(
                source_txn.template, target_period.period_id,
                basis.scenario_id, schedule=schedule,
            )):
        return _TargetResolution(
            _TargetKind.GENERATE, base=source_txn.template.default_amount,
        )
    if not source_txn.recurs and non_deleted:
        return _TargetResolution(_TargetKind.CLOSED, row=non_deleted[0])
    if not source_txn.recurs and another_row_answers(
        source_txn.template_id, basis.scenario_id,
        due_date_for(None, target_period),
    ):
        return _TargetResolution(_TargetKind.CLOSED)
    return _TargetResolution(_TargetKind.CREATE, base=Decimal("0"))
