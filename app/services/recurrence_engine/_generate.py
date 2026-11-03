"""
Shekel Budget App -- Recurrence Engine: filling periods that hold no row

:func:`generate_for_template`, the entry point that CREATES, and
:func:`rows_to_generate_in_period`, its read-only mirror for callers that need
to predict it without mutating -- what each row it would create in one period
carries, which carry-forward's preview prices (plan step X-bp) -- with
:func:`can_generate_in_period`, whether that answer names any row, and
:func:`occurrences_in_period`, the occurrences the mirror reads, for a caller
that has to date a row the engine will not write (carry-forward's override row,
ruling **R-R97**).

**It never touches a row that already exists** -- any existing row, in any
state, makes its paycheck skipped -- which is the difference between this leaf
and ``_maintain``: generating fills gaps, maintaining brings existing rows back
into line.  Both take their derived columns from the same
:class:`~app.services.recurrence_engine._amounts.DerivedRowFields`, so a new
one is written on a created row and kept current on a maintained one from the
same edit.
"""
import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.services._recurrence_common import (
    TemplateRowSelector,
    occurrences_to_write,
)
from app.services.recurrence_engine._pass import create_for_unclaimed_occurrences
from app.services.recurrence_engine._amounts import _derive_row_fields
from app.services.recurrence_engine._plan import resolve_generation_plan
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_GENERATED,
    log_event,
)

logger = logging.getLogger(__name__)



def generate_for_template(template, schedule, scenario_id, effective_from=None):
    """Generate transactions for a template across a pay-period window.

    This is the main entry point.  It:
      1. Determines which occurrences the rule names and which pay period each
         lands in, resolved against the OWNER's whole schedule and narrowed to
         the pass's window.
      2. Skips periods that already have an overridden, deleted, or immutable entry.
      3. Creates new auto-generated transactions for applicable periods.

    Args:
        template:       A TransactionTemplate with a loaded recurrence_rule.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The scenario to generate into.
        effective_from: Optional date -- only generate for periods ending on or
                        after this date.  ``None`` applies no lower bound.

    Returns:
        List of newly created Transaction objects.
    """
    # Resolve the shared gating + occurrence-matching preamble: cross-user
    # defense, rule-present gating, and the occurrence walk narrowed to the
    # window.  A None result means generate nothing (ownership failed, or no
    # rule).  See resolve_generation_plan.
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user recurrence generation",
    )
    if plan is None:
        return []

    # WHICH occurrences still need a row is the shared decision
    # (``recurrence_engine._pass.create_for_unclaimed_occurrences``); what a row IS
    # is this engine's, and that split is the whole of it since plan step
    # balance:X-au-d.
    def _new_row(period, occurrence):
        """Build and stage one generated transaction for *occurrence*.

        Every derived column comes from the ONE statement of them
        (:class:`~._amounts.DerivedRowFields`); the five below say what this
        row IS rather than what the template says.  ``occurs_on`` is
        deliberately not among the derived: it is WHICH occurrence this row
        answers, so a maintain pass may not rewrite it.

        Args:
            period: The :class:`~app.services.pay_calendar.DerivedPeriod` the
                row is funded in.
            occurrence: The date this row answers.

        Returns:
            The staged :class:`~app.models.transaction.Transaction`.
        """
        txn = Transaction(
            **_derive_row_fields(
                template, plan.rule, occurrence, period,
            )._asdict(),
            # The OWNER sits here beside the other four rather than inside
            # ``DerivedRowFields`` (plan step ``pay_calendar:C13-a``), for the
            # reason stated above about ``occurs_on``: it is what the row IS,
            # not something its definition re-derives per pass.  A maintain
            # pass ``setattr``s every derived field onto an existing row, and a
            # row does not change hands because its template was edited.
            user_id=template.user_id,
            template_id=template.id,
            pay_period_id=period.period_id,
            occurs_on=occurrence,
            scenario_id=scenario_id,
            status_id=plan.projected_id,
            is_override=False,
            is_deleted=False,
        )
        db.session.add(txn)
        return txn

    created = create_for_unclaimed_occurrences(
        _selector(template, scenario_id), plan, _new_row,
    )

    db.session.flush()
    log_event(logger, logging.INFO, EVT_RECURRENCE_GENERATED, BUSINESS,
              "Transactions generated from template",
              user_id=template.user_id,
              template_id=template.id,
              scenario_id=scenario_id,
              count=len(created))
    return created




def can_generate_in_period(template, period_id, scenario_id, *, schedule):
    """Return True iff ``generate_for_template`` would create a row there.

    Read-only mirror of ``generate_for_template``'s gating logic: whether
    :func:`rows_to_generate_in_period` names any row.  Carry-forward's
    classifier asked this until plan step X-bp and asks that function
    instead, because its preview prices the row it predicts
    (``carry_forward_service._context._classify_leftover_target``); what
    still asks this is the suite grading the gating and
    ``tests/manual/verify_generation_pass.py``.

    The decision applies the same gates as ``generate_for_template``:

      1. Cross-user defense: scenario must belong to the template's
         user.
      2. Template must have a recurrence rule.
      3. The period must be one the rule fires in.
      4. At least one occurrence the rule names in that period must be
         UNANSWERED -- by any row, in any state, in whatever paycheck that
         row now sits in.  A soft-deleted or overridden row answering the
         occurrence blocks it exactly as a live one does.

    **Step 4 asks about the OCCURRENCE since plan step R17**, where it asked
    whether the period held any row at all.  The two differ in both
    directions, which is the whole of ledger row **D57**: a row the owner
    MOVED out of this period still answers its occurrence and must still
    block, and a paycheck a rule names TWICE has a second occurrence to
    answer even once the first is taken.

    **Steps 1-3 are now literally ``resolve_generation_plan``, and until plan
    step R4b they were a hand-written MIRROR of it that disagreed.**  The
    mirror passed ``[period]`` as both the schedule and the window, so the
    rule was resolved against a ONE-period schedule: the anchor was measured
    from that period's own start rather than the owner's schedule opening, a
    rule's chosen start period could not be found at all, and ``Monthly
    First`` -- whose anchor asks which month a payday falls in -- saw one
    month with one payday and answered "yes" for every period.  Measured on a
    clone of production, 2026-08-08: for the live ``Phone Allowance`` rule the
    mirror said the engine would generate in **32 of 61 periods** where the
    real answer is each month's FIRST paycheck only.  Because the carry-forward
    executor acts on this prediction and then calls generation with the same
    one-period window, the two agreed with each other and produced a spurious
    row.  Sharing the call removes the second opinion rather than correcting
    it.

    Step 4 is the write path's own :func:`occurrences_to_write`, which
    :func:`rows_to_generate_in_period` calls over the one period asked about.

    **The schedule is threaded in rather than built here**, and that is a cost
    decision the carry-forward path forces.  The mirror runs ONCE PER
    ENVELOPE ROW being rolled forward (``_classify_leftover_target``), and
    building a schedule per call would derive the owner's pay calendar -- two
    queries -- plus one full forward occurrence walk, per row.  That is the
    redundant-producer shape ``period_population`` documents avoiding three
    modules away.  The caller resolves one schedule for the request and passes
    it down.

    **It takes an ID rather than a pay period** (pay-calendar plan step
    C2-f3c).  It read exactly one thing off the row it used to take -- ``.id``
    -- so taking the row made its one caller hold an ORM object for an integer,
    and made "which type of period is this" a question at a call site that does
    not care.

    Args:
        template: The TransactionTemplate to check.  Must have its
            ``recurrence_rule`` relationship loaded (the same
            assumption ``generate_for_template`` makes).
        period_id: The ``budget.pay_periods.id`` the canonical would land in.
            Its membership in the engine's answer is the question, so it does
            NOT have to be the whole of *schedule*'s write window -- a caller
            threading a whole-schedule value and asking about one period is as
            valid as the carry-forward context's narrowed one.  It must be IN
            that window, because the engine's answer is narrowed to it.
        scenario_id: The scenario that would receive the canonical.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.

    Returns:
        bool -- True when the engine would create a row, False when
        any of the gating conditions would skip it.
    """
    return bool(rows_to_generate_in_period(
        template, period_id, scenario_id, schedule=schedule,
    ))


def rows_to_generate_in_period(template, period_id, scenario_id, *, schedule):
    """Return what each row ``generate_for_template`` would create in ONE period carries.

    The read-only mirror :func:`can_generate_in_period` answers from, widened
    from "would it create one" to "what would each carry" (plan step X-bp,
    plan#2700): carry-forward's preview names the figure the row it bumps
    will start at, and that row's figure is its definition's price on the
    row's OWN due date (amount rule 3), which only the row's derivation
    states.  So the answer is the engine's own :class:`~._amounts.DerivedRowFields`,
    built by the producer the write path builds them with, for the
    occurrences the write path would write, in the order it writes them.

    **The write path's two decisions, called rather than restated.**  Which
    occurrences still need a row is :func:`occurrences_to_write`, the function
    ``create_for_unclaimed_occurrences`` calls, over this period's placements
    alone -- so the prediction cannot drift from what generation does, and the
    read stays bounded on the carry-forward hot path (this runs once per
    envelope row being rolled forward).  What each row carries is
    :func:`~._amounts._derive_row_fields`, the one statement ``_new_row``
    splats.

    Args:
        template: The TransactionTemplate, its ``recurrence_rule``
            relationship loaded (as :func:`can_generate_in_period` assumes).
        period_id: The ``budget.pay_periods.id`` asked about; it must be in
            *schedule*'s window, because the engine's answer is narrowed to it.
        scenario_id: The scenario the rows would belong to.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.

    Returns:
        One :class:`~._amounts.DerivedRowFields` per row the engine would
        create there, in creation order (ascending by occurrence), or ``()``
        when it would create none.
    """
    plan = _plan_in_period(template, period_id, scenario_id, schedule)
    if plan is None or not plan.placements:
        return ()
    return tuple(
        _derive_row_fields(
            template, plan.rule, placement.occurrence, placement.period,
        )
        for placement in occurrences_to_write(
            _selector(template, scenario_id), plan.placements,
        )
    )




def occurrences_in_period(template, period_id, scenario_id, *, schedule):
    """Return the occurrences the engine names in ONE period, ascending.

    What :func:`can_generate_in_period` reads before it asks whether a row
    still needs writing -- the same plan, narrowed to the same period by the
    same filter -- so a caller dating a row in that paycheck and the
    prediction that routed it there cannot disagree about which occurrences
    the rule names.  Its caller is carry-forward's override row (ruling
    **R-R97**, plan step recurrence:R5-a): a leftover rolled into a paycheck
    where the rule fires is dated on that occurrence, the earliest if it
    fires twice, and one rolled where it fires nowhere is dated on the
    payday.  The rule's walk is the pass's memo, so asking this beside the
    prediction reads it rather than walking it again.

    Args:
        template: The TransactionTemplate, its ``recurrence_rule``
            relationship loaded (as :func:`can_generate_in_period` assumes).
        period_id: The ``budget.pay_periods.id`` asked about; it must be in
            *schedule*'s window, because the engine's answer is narrowed to it.
        scenario_id: The scenario the row belongs to.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.

    Returns:
        The occurrence dates, ascending, or ``()`` when the rule names none
        there or the definition does not recur.
    """
    plan = _plan_in_period(template, period_id, scenario_id, schedule)
    if plan is None:
        return ()
    return tuple(placement.occurrence for placement in plan.placements)


def _plan_in_period(template, period_id, scenario_id, schedule):
    """Return the generation plan narrowed to *period_id*'s placements, or ``None``.

    The one narrowing :func:`rows_to_generate_in_period` and
    :func:`occurrences_in_period` share.  The whole plan rather than its
    placements alone since plan step X-bp, because a row's derivation reads
    the plan's rule beside each placement.

    Args:
        template: The TransactionTemplate.
        period_id: The ``budget.pay_periods.id`` asked about.
        scenario_id: The scenario asked about.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.

    Returns:
        The :class:`~._plan.GenerationPlan` whose placements are the ones
        seated in that period, ascending by occurrence (possibly none), or
        ``None`` when the plan resolves to nothing.
    """
    plan = resolve_generation_plan(
        template, schedule, scenario_id, None,
        block_message="Blocked cross-user recurrence generation prediction",
    )
    if plan is None:
        return None
    # Membership rather than emptiness: *schedule*'s window may be wider than
    # the one period asked about (the carry-forward context narrows it to the
    # target, but a caller threading a whole-schedule value is equally valid),
    # so the answer is "does the engine name THIS period".
    return plan._replace(placements=tuple(
        placement for placement in plan.placements
        if placement.period.period_id == period_id
    ))


def _selector(template, scenario_id):
    """Name what this engine's row fetches are asking about.

    A one-line binding of :class:`_recurrence_common.TemplateRowSelector` to
    this engine's model, so ``Transaction`` and ``Transaction.template_id`` are
    paired in ONE place and the two engines' generate paths run the identical
    query.  Plan step R4b-2 hoisted the query itself; pay-calendar plan step
    C2-f3c named the four facts it takes, which is what let the pair stop being
    repeated at each call.

    Args:
        template: The TransactionTemplate this pass is generating from.
        scenario_id: The scenario being written into.

    Returns:
        The :class:`~app.services._recurrence_common.TemplateRowSelector`.
    """
    return TemplateRowSelector(
        Transaction, Transaction.template_id, template, scenario_id,
    )
