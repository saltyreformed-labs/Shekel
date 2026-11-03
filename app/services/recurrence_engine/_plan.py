"""
Shekel Budget App -- Recurrence Engine: WHICH periods a rule names

The gating and occurrence-matching preamble both write paths share
(:func:`resolve_generation_plan`) and the two value types it answers in.

Nothing here writes.  This leaf answers "where does this definition fire",
which is the question ``_generate`` and ``_maintain`` both have to ask before
they can act, and asking it in one place is what stops the two from drifting
on which periods a rule applies to.  **"On what day" left at plan step
R16-b-2**: the derivation of a generated row's own date from its placed
occurrence, ``compute_due_date``, is
:func:`app.services.recurrence.compute_due_date` now
(ruling **R-R69**), because the balance seam's ESTIMATED loan tier has to date
an occurrence no row answers yet exactly as the row would be dated, and the
seam cannot reach this package without importing the write state machine.

**"Where does this definition fire" is asked of the COMPOSED door since plan
step R7d-c-2** (:func:`app.services.recurring_definition.read_definition`):
what the rule authors AND what its destination allows, so a transfer paying a
loan is generated only while the loan owes.  Until then the seam walked the
rule alone (``recurrence.rule_occurrences``) and a loan payment's stop reached
it only as the payoff some chokepoint had last cached into
``budget.recurrence_rules.end_date`` -- plan ledger row **D35**, the stale
cache that dropped an installment.
"""
import logging
from datetime import date
from typing import NamedTuple

from app.models.recurrence_rule import RecurrenceRule
from app import ref_cache
from app.enums import StatusEnum
from app.services.pay_calendar import DerivedPeriod
from app.services.recurring_definition import read_definition
from app.services._recurrence_common import check_scenario_ownership

logger = logging.getLogger(__name__)



class PlannedOccurrence(NamedTuple):
    """One occurrence a generate pass WILL write, and the paycheck it lands in.

    The NARROWED twin of
    :class:`~app.services.recurrence.OccurrencePlacement`, and the narrowing is
    now its whole reason to exist.  The walk answers about every occurrence of
    a rule, so its ``period`` is optional -- ``None`` where the saved schedule
    does not reach that far.  This value is what survives
    :func:`resolve_generation_plan`'s three filters (a period exists, it is in
    the pass's write window, it is not before the caller's bound), so its
    ``period`` is never absent and no consumer needs to ask.  A type that says
    so is the only place that fact can be stated once.

    **It carried an ORM row until pay-calendar plan step C2-f3c**, and THAT was
    the difference between the two types before the narrowing became it: a
    ``Transaction`` is written against ``budget.pay_periods.id``, so the id was
    resolved back to a row here rather than in each engine's write loop.  The
    derived period carries the id, the payday and the last covered day -- every
    field the seam ever read off the row -- so the round trip through the ORM
    bought nothing and cost the seam a second read of the schedule.

    **The occurrence is carried rather than re-derived** (plan step R4b-2).
    Until this step generation answered in periods alone, so the two things
    that need the date -- the gap report (plan ledger row **D7**) and the
    repeat refusal's message (**D19**) -- either could not have it or would
    have had to walk the cadence a second time, which is the redundant-producer
    shape this arc removes everywhere else.

    Attributes:
        occurrence: The date the rule's cadence names.  For the ``PERIOD``
            unit this is the paycheck's own payday; see
            :mod:`app.services.recurrence._occurrence`.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` the
            generated record lives in.  Always inside the pass's write window,
            always materialised (so its ``period_id`` is a real
            ``budget.pay_periods.id``), and never ``None``.  The write loops
            read BOTH since plan step R5-a: the period is where the row is
            funded, and ``compute_due_date`` dates the row from *occurrence*
            (or from this period's payday, for a paycheck cadence or one
            funded from a later paycheck) --
            it read this alone until then, which is plan ledger row D18.
    """

    occurrence: date
    period: DerivedPeriod




class GenerationPlan(NamedTuple):
    """Resolved inputs a recurrence generate pass needs after gating.

    Returned by :func:`resolve_generation_plan` once the cross-user
    ownership check and the rule-present gating have passed, so the caller
    can proceed straight to model-specific row creation.  Public (no
    leading underscore) because it is the return contract of the public
    :func:`resolve_generation_plan`, which the transfer engine consumes.

    Attributes:
        rule: The template's recurrence rule, already confirmed present.
        placements: One :class:`PlannedOccurrence` per occurrence this pass
            may write, ascending by occurrence date.  **A pay period can
            appear more than once** -- at a pay cadence of 30 days or more a
            monthly bill legitimately falls inside one paycheck several times
            -- and since plan step **R17** re-keyed the unique index onto the
            occurrence, both rows STORE.  It was refused until then (plan
            ledger row D19), because the index held one row per paycheck.
        projected_id: The ``Projected`` status id every generated row carries.

    **It carried a fourth field until plan step C2-b2**, ``gaps`` -- the
    occurrence dates the owner's schedule had no pay period for, which the
    write path reported and the read-only predictor ignored.  A pay period's
    end is now DERIVED from the next payday, so the periods tile and a hole
    between two of them is not a state a READER can see; the field, the report
    and the ``PlacementOutcome`` that fed it went with it (plan ledger rows
    **D7** / **P27**, recurrence **R-F10**).  A legacy hole is ABSORBED by the
    preceding paycheck and reported by ``integrity_check`` **BA-07** instead --
    and it is one of THREE shapes where the derivation and the stored columns
    disagree, all of which move money and only one of which BA-07 sees.  The
    other two are the stored cadence against the last stored end (row **P28**)
    and a stored ordinal that is not ``0..n-1`` (row **P26**);
    ``recurrence/_occurrence.py``'s module docstring holds the full statement.
    """

    rule: RecurrenceRule
    placements: tuple[PlannedOccurrence, ...]
    projected_id: int




def definition_recurs(template) -> bool:
    """Return whether *template* names ANY occurrence: active, and carrying a rule.

    **The ONE predicate for "does this definition recur"**, read where a
    definition's plan is resolved (:func:`resolve_generation_plan`, so a
    definition that does not recur names no occurrence and the maintain
    pass considers every row it holds for retirement) and where the
    recurrence-conflict chooser asks whether an edit will regenerate rows at
    the new amount (``routes/_recurrence_conflict_chooser``).  Two readers
    of one fact, stated once.

    Two arms.  A definition with NO rule does not recur, the one way it has
    said so since plan step R2e-3 retired the ``Once`` pattern.  **An
    ARCHIVED definition does not recur either, since plan step R7d-g-3**
    (plan ledger row **REC-524**): the archive door soft-deletes every
    projected row and takes the definition out of every active set, and
    until this arm existed nothing on the regeneration path read the flag
    -- the update doors reach an archived template by URL.  A soft-deleted
    row still CLAIMS its occurrence
    (:class:`~app.services._recurrence_common.OccurrenceClaims`), so what a
    rename regenerated was a live row for every occurrence NO row answered:
    a definition archived before it generated (``0 -> 6`` rows measured), and
    every pay period populated AFTER the archive, since period population
    skips an archived template and leaves its later occurrences unclaimed --
    the Recurring surface still listing the template as archived.  Reading
    the flag HERE rather than at a door makes the defect unrepresentable: no
    pass, for either template kind, can write a row for a definition the
    owner has stopped.  The unarchive door flips the flag before its pass
    runs, so the restored definition recurs again by the same rule.  Two
    more readers inherit the same answer, by the same rule: the
    carry-forward's prediction (``_generate.can_generate_in_period``) says an
    archived envelope's definition would generate nothing, so a leftover is
    CREATED as its own row rather than generated from a stopped definition;
    and a salary regeneration (``salary_regeneration``) over a profile whose
    template was archived writes nothing where it revived rows.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate``.  Every
            production door flushes before it regenerates, so ``is_active``
            has been given its column default by the time this reads it; a
            fixture asking about an UNFLUSHED template reads ``None`` there,
            which is the default's value-to-be and not an archive, so it is
            read as active rather than answered "does not recur" silently.

    Returns:
        ``True`` iff the definition is not archived and carries a recurrence
        rule.
    """
    return template.is_active is not False and template.recurs


def resolve_generation_plan(
    template, schedule, scenario_id, effective_from, *, block_message,
):
    """Run the shared gating + occurrence-matching preamble for a generate pass.

    Both this module's ``generate_for_template`` and the transfer
    engine's identical preamble (``app/services/transfer_recurrence.py``)
    perform the same steps before their model-specific row creation: the
    cross-user ownership check, the rule-present gating, and the occurrence
    walk against the owner's schedule.  Centralising them guarantees the two
    engines cannot drift on which periods a rule applies to.  Public (no
    leading underscore) because the transfer engine calls it cross-module --
    the shared preamble is deliberately part of this module's public surface,
    like :func:`~app.services.recurrence.rule_occurrences`.

    **The walk is the composed door's, since plan step R7d-c-2.**  The
    placements come from
    :func:`~app.services.recurring_definition.read_definition` over the pass
    the schedule carries (``schedule.ctx``), so every occurrence is narrowed by
    the rule's authored bound AND by the stop its destination derives -- a
    transfer into a loan stops where the loan's balance folds to zero
    (:func:`~app.services.loan_recurrence_sync.loan_payment_window`).  It read
    ``rule_occurrences`` until then, which applies the rule's own bound alone,
    so a loan payment's stop reached generation only as the payoff some
    chokepoint had last written into the authored bound's column: a CACHE with
    no reconciler, measurably behind on live data (plan ledger row **D35**:
    ``2029-01-22`` stored against ``2029-02-22`` derived, one ``$531.94``
    installment never created).  Two things follow, and both are the door's
    rather than this function's.  For the loan's STANDING payment that column
    is read as the cache it is (ruling **R-R56**), so a stale date there --
    earlier OR later than the payoff -- binds nothing here.  And the loan is
    folded ONCE per pass however many definitions pay into it, because the
    door reads the pass's memoised resolution; a transaction template, which
    pays into no account, costs the door no query at all.  The rule is
    resolved once too: the pass memoises its resolution by the rule's SPEC
    (``BalanceContext.resolved_recurrence_of``, plan step R16-b-2), so the
    fold that sums the definition and the door that narrows it read one
    value (``CLAUDE.md`` rule 14, ONE WALK) -- and a rule re-authored on the
    pass is resolved afresh, which the merge of this step onto R16-b-2
    measured the id-keyed first cut of that memo could not do: a regenerate
    after an edit on one pass walked the pre-edit cadence.

    **What the bound is derived FROM is the loan's forward plan, and that
    plan is invariant under the rows this seam writes** (``balance_at._plan``:
    the PLANNED tier is the loan's projected transfer shadows, the ESTIMATED
    tier every occurrence a definition names that the schedule places and no
    row in any state answers, past or future -- ruling **R-R64** -- dated as
    its row would be, **R-R69**, and priced by the one function the written
    row is priced by, **R-R67**, for EVERY definition into the loan, plan step
    R16-b-2).  So a row this pass writes answers its occurrence at the cash
    and the date the estimate already carried, the payoff read before the
    write is the payoff read after it, and a second pass writes nothing: a
    FIXED POINT by construction.  It was not always one.  This step's first
    build (``de8d1a56``) landed on the tree before R16-b-2, where the
    estimate priced the standing payment alone and no slot behind ``as_of``,
    and its own review measured four shapes on which the tiers parted, one of
    them a REGRESSION: a pass opened in the reset door's hole read the payoff
    one installment late and wrote one row past a ``$1,200`` loan's life
    where the column, synced before the wipe, had bounded HEAD's at three
    (plan ledger row **D46**).  The developer ruled root cause first: R16-b-2
    shipped AHEAD of this step carrying R-R64 (ruling **R-R65**), and the
    step was re-cut on that tree.  Closed there: the past unanswered slot
    (D46), the second definition (**D47**) and the paycheck-dated row.  What
    remains is a cent, and it is stable rather than a break: on ``$3,000`` at
    5% over three months the amount model's derive arm bills the level
    ``$1,008.34`` where the contract bills ``$1,008.35`` on the last
    installment, both tiers agree on it, ``$0.01`` is left owing and the
    payoff reads one installment late before the rows exist and after them
    (plan ledger row **REC-517**, owned by R16-f; ``$0.00`` live).  Each of
    the three is pinned in
    ``tests/test_services/test_loan_bound_at_generation.py`` on the re-cut
    tree (2026-09-11): the reset door reads ``2026-04-15`` in its hole and
    writes three rows; two full definitions into one loan close it on
    ``2026-03-15`` before any row exists and generate to that stop; and the
    ``$3,000`` loan reads ``2026-05-15``, writes four rows on the first pass
    and none on the second.

    **It answers in ``(occurrence, period)`` pairs** (plan step R4b-2).  It
    used to answer in periods alone, so the date a row's cadence actually
    named was computed, used to select a paycheck, and then thrown away --
    leaving the repeat refusal of the day able to say only how MANY times a
    definition fell inside one paycheck, and leaving an occurrence in a
    schedule gap indistinguishable from one that was never generated.

    **The rule is resolved against the whole schedule and the answer is then
    NARROWED to the window** (plan step R4b), in that order.  Doing it the
    other way round is what defect D22 was: resolving against the window makes
    the window's own first payday look like the owner's, so a ``Monthly First``
    rule re-fires in a month it already covered.  Narrowing afterwards keeps
    the window a window -- without it an extend would re-walk every historical
    period and the pass would cost O(schedule) writes instead of O(new).

    **The two ``effective_from`` defaults this used to apply are gone**, and
    deleting them was a simplification rather than a behaviour change: it fell
    back to the rule's start period and then to the first candidate period,
    both already inside the anchor of the day (measured then: identical answers
    for all 46 live rules over all 61 production periods, and a byte-identical
    ``tests/oracles/recurrence_baseline.txt``).  ``None`` plainly means "no
    lower window bound".  *This paragraph credited that to
    ``_resolution._effective_start`` taking "the GREATEST of the schedule's
    opening payday, the rule's ``start_date`` and its start period's" until
    plan step ``pay_calendar:C18-a`` found it describing a function plan step
    R7c-b had deleted*: a rule's first occurrence is AUTHORED since then
    (``starts_on``, ruling **R-R16**), nothing takes a maximum with the
    schedule's opening payday, and an occurrence below the owner's first
    payday was skipped only because no paycheck existed to place it in.

    **Where a definition's occurrences START is two facts since plan step
    ``pay_calendar:C18-a``** (rulings **R-PC85**, **R-PC86**): the rule's own
    first occurrence, and the day after the books open on every account it
    moves money in.  The second rides on the resolved value the composed door
    returns (``ResolvedRecurrence.books_opened_on``, attached by the read pass)
    and the walk drops an occurrence whose row would land on or before it, so
    a paycheck recorded below an account's books is not filled with rows whose
    money is already inside that account's opening equity -- measured on a
    production clone as a Van Payment and a birthday before this step.
    Nothing here reads it: the placements already carry it.

    Args:
        template: The (Transaction|Transfer)Template to generate from.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule` --
            their whole pay calendar plus the ids of the periods this pass may
            write into.
        scenario_id: The scenario to generate into.
        effective_from: Optional lower bound on the window; occurrences whose
            placed period ENDS before it are dropped.  ``None`` applies no
            bound.  It is the CALLER's display / regeneration boundary and
            never the rule's own -- conflating the two is how defect D2
            happened.
        block_message: Cross-user-block log message distinguishing the
            calling engine.

    Returns:
        A :class:`GenerationPlan` when generation should proceed, or
        ``None`` when ownership fails or the definition does not recur --
        no rule, or archived (:func:`definition_recurs`); every caller
        returns an empty list in the None case.
    """
    if not check_scenario_ownership(
        logger, template, scenario_id, block_message=block_message,
    ):
        return None

    if not definition_recurs(template):
        # No recurrence rule, or an ARCHIVED definition -- nothing to
        # generate.  ONE predicate says "does not recur" (plan step R2e-3
        # retired the ``Once`` pattern that was a second way, and the guard
        # that read it); see :func:`definition_recurs` for the archived arm.
        return None
    rule = template.recurrence_rule

    # Narrow the walk's answer to what this pass may actually write.  Dropping
    # the window intersection would make a schedule extend re-walk every
    # historical period and cost O(schedule) writes instead of O(new).
    #
    # **The bound is applied to the DERIVED end, and so is the row select this
    # call runs beside** (pay-calendar plan step C2-f3c).  It used to be
    # applied to the ORM row's STORED ``end_date``, deliberately, because
    # ``_maintain.regenerate_for_template``'s sweep was SQL over that same
    # column -- and reading the bound off two different definitions of "when
    # does this paycheck end" is how a row gets selected for maintenance but
    # never NAMED by the rule, which since plan step R10-a means RETIRED rather
    # than merely recreated.  ``_recurrence_common.rows_this_pass_may_maintain``
    # now selects on a period-id set filtered by the same derived end this line
    # reads, off the same calendar, so the two halves are one predicate rather
    # than two that have to agree.  Plan step C4-c dropped the column both used to
    # read.
    window = schedule.write_period_ids
    placements = []
    # The composed door over the pass the schedule carries: its calendar is
    # the same memo ``schedule.calendar`` reads, so the schedule the rule is
    # resolved against and the pass its derived stop is folded in are one
    # value (plan step R7d-c-1 put the pass on the schedule for exactly this
    # read).  The reading's ``resolved`` is ``None`` for an owner with no pay
    # periods, where its placements are empty and the loop below writes
    # nothing -- the same answer ``rule_occurrences`` gave for that state.
    for placement in read_definition(template, schedule.ctx).placements:
        period = placement.period
        if period is None:
            # The saved schedule does not reach this occurrence.  Ordinary --
            # the next schedule extend places it -- and since plan step C2-b2
            # it is the only way to get a placement with no period.
            continue
        if period.period_id not in window:
            continue
        if effective_from is not None and period.end_date < effective_from:
            continue
        placements.append(PlannedOccurrence(placement.occurrence, period))
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    return GenerationPlan(rule, tuple(placements), projected_id)
