"""
Shekel Budget App -- Shared Recurrence-Engine Helpers

Centralises the logic the transaction recurrence engine
(``app/services/recurrence_engine.py``) and the transfer recurrence
engine (``app/services/transfer_recurrence.py``) ran in byte-identical
form before each generated its own model-specific rows.  The two
engines are deliberate parallels (Transaction vs Transfer), so every
block that does not actually touch the model belongs here, in one
place, where the two cannot drift:

  - the cross-user ownership defense (:func:`check_scenario_ownership`),
  - what a template's rows already CLAIM (:class:`OccurrenceClaims`), the one
    query that reads it (:func:`rows_claiming`) and the generate decision
    both engines and the read-only predictor share
    (:func:`occurrences_to_write`),
  - the regenerate pass's whole DECISION -- which rows are the rule's to
    rewrite (:func:`owner_hold_on` / :func:`is_maintainable`), what one pass
    must do to each (:func:`classify_maintain_work` into
    :class:`MaintainWork`) and what it then did (:class:`MaintainOutcome`),
  - the regenerate row fetch (:func:`rows_this_pass_may_maintain`),
  - the regenerate pass's period-set query (:func:`_rows_in_periods`),
  - the cross-user audit ``log_event(...)`` blocks (the ``log_*`` helpers
    below).

**``partition_regeneration_rows`` is gone** (plan step R10-b).  It classified a
row for the DELETE-and-recreate regenerate machine -- conflict, immutable, or
"safe to delete and regenerate" -- and that third class was a premise both
engines have now dropped.  Plan step R10-a replaced it on the transaction side
(``transaction_entries`` CASCADED then, so deleting a projected envelope destroyed
the owner's purchases); R10-b replaces it on the transfer side, where a delete took
the row's ``notes``, its id and any settlement record it had retained through a
revert.  What stands in its place is :func:`classify_maintain_work`, which both
engines share -- so the shared helper is the decision that MAINTAINS rather than
the one that partitioned them for destruction.

**``regeneration_bound`` is gone** (pay-calendar plan step C2-f3c).  It existed
because the regenerate sweep was an SQL bound on ``pay_periods.end_date``, so
"no lower bound" had to be turned into a concrete date before it could be
compared against a column -- and the date it had to become was the WRITE
WINDOW's opening, or the sweep reached rows the pass would not rewrite.  The
sweep now selects on a period-ID SET taken from the pass's own window, so
``None`` needs no translation and the domain cannot exceed the window in the
first place.  The asymmetry that helper closed by arithmetic is closed by the
shape.

What both engines TAKE rather than share -- the owner's pay calendar
and the window one pass writes into -- is
:class:`~app.services.generation_schedule.GenerationSchedule`, and it lives in
its own public module because the route layer constructs it too.  This module
is package-private (``shekel-private-module-import``), so a type a route must
name cannot live here.

The model-specific halves -- constructing a ``Transaction`` vs routing
a ``Transfer`` through ``transfer_service`` for shadow atomicity -- stay
in their respective engines.  The occurrence-matching preamble
(``resolve_generation_plan``) lives in ``recurrence_engine`` too, since
hoisting it here would create an import cycle -- the transfer engine imports
that module for it.

Keeping the audit-trail event names, message strings, and keyword
fields in one place is load-bearing for two reasons:

  1. **Forensic coherence.**  The Phase-6 audit's cross-user defense
     evidence trail depends on both engines emitting structurally
     identical events when an IDOR probe is blocked.  Drift between the
     two engines would show up as a missing or differently-shaped
     event in the SOC's alerting pipeline, which is exactly the
     regression class the testing-standards "Zero Tolerance" rule is
     designed to prevent.
  2. **DRY.**  Pylint R0801 flagged the duplicate blocks (one VERBATIM,
     one near-verbatim with only the literal message differing); a
     fix in one copy that forgets the other is the bug class Issue 1 of
     ``shekel-audits/security-2026-04-15/c-38-followups.md`` calls out.

These helpers are deliberately thin wrappers around
``app.utils.log_events.log_event`` -- they exist to lock in the
event constant, category, and keyword shape, not to add behaviour.
"""

import logging
from datetime import date
from typing import NamedTuple

from app.extensions import db
from app.models.scenario import Scenario
from app.utils.log_events import (
    ACCESS,
    BUSINESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_CROSS_USER_BLOCKED,
    log_event,
)


def log_template_cross_user_blocked(
    logger: logging.Logger,
    *,
    message: str,
    template_id: int,
    template_user_id: int,
    scenario_id: int,
) -> None:
    """Emit ``EVT_CROSS_USER_BLOCKED`` for a template/scenario mismatch.

    The transaction and transfer recurrence engines both run a
    defense-in-depth ownership check at the top of ``generate_for_template``
    and ``regenerate_for_template``: the scenario being targeted must
    belong to the same user as the template.  A mismatch indicates a
    route-layer hole or an IDOR probe and is logged at WARNING for
    SOC alerting.

    Each of the four historical call sites (two per engine) shares the
    same keyword shape but used a slightly different human-readable
    message; ``message`` is parameterised so the engines can keep
    distinguishing the *generate* vs *regenerate* paths in log output.

    Args:
        logger: Caller's module-level logger.
        message: Human-readable description.  Pass a literal so the
            ``generate_for_template`` and ``regenerate_for_template``
            paths remain distinguishable in log search.
        template_id: The (Transaction|Transfer)Template primary key
            whose ownership did not match the scenario's owner.
        template_user_id: The template's owning user id (the value
            that should have matched the scenario's user).
        scenario_id: The Scenario primary key whose owner did not
            match the template's owner.
    """
    log_event(
        logger, logging.WARNING, EVT_CROSS_USER_BLOCKED, BUSINESS,
        message,
        template_id=template_id,
        template_user_id=template_user_id,
        scenario_id=scenario_id,
    )


def log_resource_access_denied(
    logger: logging.Logger,
    *,
    user_id: int,
    model: str,
    pk: int,
    owner_id: int,
) -> None:
    """Emit ``EVT_ACCESS_DENIED_CROSS_USER`` for a row ownership violation.

    Used by both recurrence engines' ``resolve_conflicts`` paths when a
    row id arrives whose owner does not match the requesting user.  The
    event is part of the F-144 access-denied evidence trail and is
    expected to be SOC-alertable, so the keyword shape is fixed.

    Args:
        logger: Caller's module-level logger.
        user_id: The requesting user (NOT the row owner).
        model: Display name of the model that was probed -- pass
            ``"Transaction"`` or ``"Transfer"`` so the SOC can group
            events by resource family.
        pk: The primary key of the row whose ownership failed the check.
        owner_id: The actual owner of the row (the user the requester
            tried to access across).
    """
    log_event(
        logger, logging.WARNING,
        EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
        "Cross-user resource access blocked",
        user_id=user_id,
        model=model,
        pk=pk,
        owner_id=owner_id,
    )


def check_scenario_ownership(
    logger: logging.Logger,
    template,
    scenario_id: int,
    *,
    block_message: str,
) -> bool:
    """Verify the target scenario belongs to the template's owner.

    Defense-in-depth ownership check run at the top of both recurrence
    engines' ``generate_for_template`` and ``regenerate_for_template``.
    The route layer already enforces this, but a mismatch here would
    silently write rows into another user's scenario (IDOR).  On a
    mismatch the block is logged at WARNING for SOC alerting via
    :func:`log_template_cross_user_blocked` and the caller aborts.

    Args:
        logger: The calling engine's module logger, so the emitted
            event is attributed to ``app.services.recurrence_engine`` or
            ``app.services.transfer_recurrence`` exactly as before.
        template: The (Transaction|Transfer)Template being generated.
        scenario_id: The scenario primary key to write into.
        block_message: Human-readable description distinguishing the
            generate vs regenerate path in log output.

    Returns:
        True when the scenario exists and is owned by the template's
        user; False (after logging the block) otherwise.
    """
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != template.user_id:
        log_template_cross_user_blocked(
            logger,
            message=block_message,
            template_id=template.id,
            template_user_id=template.user_id,
            scenario_id=scenario_id,
        )
        return False
    return True


class OccurrenceClaims(NamedTuple):
    """WHICH of a template's occurrences its existing rows already answer.

    **The one statement of what blocks a write, for all three readers**, and
    the whole of plan step **R17**'s second leaf.  Both engines' generate loops
    and the maintain pass's create arm ask the identical question -- "is this
    occurrence already answered?" -- and until this type they each asked a
    DIFFERENT one, about the pay PERIOD.  That is ledger row **D57**: a
    generated row the owner moves to a neighbouring paycheck vacates the period
    its occurrence names, so the next whole-schedule pass writes a second row
    for an occurrence that is already answered.  Measured on a production clone
    (2026-08-28): 8 rows, ``$1,482.93``, six of them duplicating a due date a
    ``Paid`` row already covered.

    **A row claims the occurrence in its ``occurs_on``, and a row whose
    ``occurs_on`` is NULL claims its PAY PERIOD instead.**  The second half is
    not a fallback or a fence -- it is the honest reading of a column whose NULL
    means "this row answers no occurrence" (see ``Transaction.occurs_on``).
    Such a row cannot be compared against an occurrence at all, so the only
    claim it can make is the pre-R17 one: it holds the paycheck it sits in.
    Two live writers create them -- ``carry_forward_service._execute`` rolls an
    unspent envelope forward, and the one-time branch of
    ``routes/transfers/_instances.py`` materialises a transfer whose template
    has no rule -- and the backfill deliberately leaves NULL every row no
    occurrence claims.

    **Letting a NULL row claim NOTHING was measured, and it moves money.**  The
    unarchive door restores a template's soft-deleted rows and then generates.
    On the developer's own archived ``Emergency Fund`` transfer template (51
    soft-deleted rows, all NULL because the backfill does not walk archived
    templates), a NULL-claims-nothing rule creates **52 rows / ``$26,000``**
    where today's period rule creates 11 / ``$5,500`` -- 41 phantom transfers
    worth ``$20,500``, every one of them beside a row the owner had deleted.
    Claiming the period creates exactly the 11, which is today's answer.

    Attributes:
        answered: The ``occurs_on`` values this template's rows already hold.
        held_undated: The ``budget.pay_periods.id`` values held by rows that
            answer no occurrence, which therefore claim their paycheck whole.
    """

    answered: frozenset
    held_undated: frozenset

    @classmethod
    def over(cls, rows) -> "OccurrenceClaims":
        """Read what *rows* claim, in one pass over them.

        Args:
            rows: The (Transaction|Transfer) rows to read.  EVERY state counts
                -- immutable, overridden and soft-deleted alike -- which is the
                long-standing rule :func:`owner_hold_on` states for the
                maintain pass and which this predicate has always applied on
                the generate path: a row the owner removed must not be
                resurrected, and a row they overrode must not be written beside.

        Returns:
            The claims those rows make.
        """
        return cls(
            answered=frozenset(
                row.occurs_on for row in rows if row.occurs_on is not None
            ),
            held_undated=frozenset(
                row.pay_period_id for row in rows if row.occurs_on is None
            ),
        )

    def blocks(self, placement) -> bool:
        """Return True when *placement* is already answered and must be skipped.

        Args:
            placement: A ``recurrence_engine.PlannedOccurrence`` -- duck-typed
                on ``.occurrence`` and ``.period.period_id`` exactly as the rest
                of this module does, because importing that type here would
                close a cycle (``_plan`` imports this module).

        Returns:
            True when some existing row already answers this occurrence, or
            holds its paycheck without answering any occurrence.
        """
        return (
            placement.occurrence in self.answered
            or placement.period.period_id in self.held_undated
        )


class TemplateRowSelector(NamedTuple):
    """WHICH rows a recurrence pass is asking about.

    **The one place the two engines' difference is written down.**  A
    transaction pass and a transfer pass run identical queries against
    different tables, so every shared fetch here took the mapped class and the
    template foreign-key column as a pair of arguments beside the template and
    the scenario -- four parameters repeated at six call sites, and enough of
    them to trip ``too-many-arguments`` the moment a fetch needed one more.
    Naming the four as one value says what they are: this pass's subject.

    Built ONCE per pass by each engine, which is also what stops a pass mixing
    one model's column with another's.

    Attributes:
        model: The mapped class to query (``Transaction`` or ``Transfer``).
        template_fk_col: That model's template foreign-key column object
            (``Transaction.template_id`` or ``Transfer.transfer_template_id``).
        template: The (Transaction|Transfer)Template being generated from.
            The whole row rather than its id: every reader here wants
            ``template.id``, and carrying both the row and the id would be two
            spellings of one fact.  It carried the row for its NAME while the
            D19 refusal existed to put that name in a message; plan step R17
            deleted the refusal and the row is what the callers already hold.
        scenario_id: The scenario primary key every row must match.
    """

    model: type
    template_fk_col: object
    template: object
    scenario_id: int


# What the owner can own about a generated row, and therefore the three ways a
# row stops being the rule's to rewrite: its PERMANENCE (an immutable status),
# its AMOUNT (``is_override``) and its EXISTENCE (``is_deleted``).  Named
# because :func:`classify_maintain_work` must tell them apart -- each routes to
# a different conflict list -- while :func:`is_maintainable` only asks whether
# there is one.
#
# Both engines' rows carry all three facts under the same names (a status with
# ``is_immutable``, ``is_override``, ``is_deleted``), which is why this reads
# neither model and lives here rather than in either engine (plan step R10-b).
BLOCK_IMMUTABLE = "immutable"
BLOCK_OVERRIDE = "override"
BLOCK_DELETED = "deleted"


def owner_hold_on(row) -> "str | None":
    """Return which owner-held fact stops *row* being the rule's to rewrite.

    **The one statement of the three FOR THE MAINTAIN PASS, so its two readers
    cannot drift.**  The classifier needs to tell them apart (each is a
    different conflict list) and the repeat refusal only needs to know whether
    there is one, so a bare boolean could not serve both, and two copies of the
    chain is what an adversarial review of plan step R10-a actually found here.
    The order is load-bearing: an immutable row is never touched whatever else
    is true of it, which is what keeps a settled row out of every list.

    **It IS the only place the three conditions appear, since plan step R17.**
    ``should_skip_period`` spelled all three out again a hundred lines up, and
    its own docstring explained why the duplication was deliberate: every
    branch returned the same answer, so the enumeration was commentary on a
    predicate that degenerated to ``bool(existing_rows)``.  That predicate is
    gone.  :class:`OccurrenceClaims` replaced it and asks nothing about a row's
    STATE at all -- every row claims what it answers, whatever status it is in
    -- so the three conditions now have exactly one statement, which is this
    one.

    Args:
        row: The Transaction or Transfer to classify.

    Returns:
        :data:`BLOCK_IMMUTABLE`, :data:`BLOCK_OVERRIDE` or
        :data:`BLOCK_DELETED`, or ``None`` when the row is the rule's own --
        auto-generated, live and still mutable.
    """
    if row.status and row.status.is_immutable:
        return BLOCK_IMMUTABLE
    if row.is_override:
        return BLOCK_OVERRIDE
    if row.is_deleted:
        return BLOCK_DELETED
    return None


def is_maintainable(row) -> bool:
    """Return True when *row* is the RULE's own row, free to be maintained.

    The boolean face of :func:`owner_hold_on`, for the callers that do not care
    WHICH hold applies.

    Args:
        row: The Transaction or Transfer to classify.

    Returns:
        True when the row is auto-generated, live and still mutable.
    """
    return owner_hold_on(row) is None


class PlacedRow(NamedTuple):
    """A row a pass will CREATE: which paycheck funds it, what it answers.

    The ID-LEVEL twin of
    :class:`~app.services.recurrence_engine._plan.PlannedOccurrence`, for the
    write arms that hold a ``pay_periods.id`` rather than a whole derived
    period.  Both engines' create paths take one, so "which paycheck, which
    occurrence" is one value rather than two arguments that can be paired
    wrongly.

    ``create_in`` held bare ``pay_periods.id`` values until plan step **R17**,
    which is the whole of ledger row **D57**: a generated row now records WHICH
    OCCURRENCE of its template's cadence it answers (``occurs_on``), and the
    create arm cannot state that from a period id alone -- at a pay cadence of
    30 days or more one paycheck can host a definition more than once, so the
    period is not a name for the occurrence and never was.

    **It is a pair rather than a second map beside ``derived``.**  The applier
    already takes ``{period_id: DerivedRowFields}``; threading the occurrence as
    another dict keyed the same way would be two structures that have to agree,
    which is the shape this module exists to remove.  Carrying it on the
    decision itself means the classifier states it once and the applier reads
    it, and neither engine can pair a period with another period's occurrence.

    Attributes:
        period_id: The ``budget.pay_periods.id`` the new row lands in.
        occurs_on: The date this template's cadence names for it -- the value
            :class:`~app.services.recurrence_engine._plan.PlannedOccurrence`
            carries and both write loops discarded before R17.
    """

    period_id: int
    occurs_on: date


class MaintainWork(NamedTuple):
    """What a maintain pass will DO, decided before anything is written.

    :func:`classify_maintain_work` fills this by reading rows only, and each
    engine's own applier is the only thing that writes -- so what a
    regeneration decides can be asserted without a database write, and a change
    to the decision cannot hide inside a change to the write.

    Attributes:
        update: Rule-generated rows the definition still names, to be brought
            into line with what it derives for their period.
        create_in: One :class:`PlacedRow` per row to write -- one per
            occurrence the rule names that NOTHING already answers, each with
            the occurrence it answers.  An occurrence answered by any row --
            immutable, overridden or soft-deleted, in whatever paycheck that
            row now sits in -- is absent (:class:`OccurrenceClaims`).  **Two
            entries may share a ``period_id``** since plan step R17: a cadence
            that names one paycheck twice writes both rows, which the re-keyed
            index stores and the paycheck-keyed one refused.
        retire: Rows the rule no longer names that carry nothing of the
            owner's, to be deleted.
        overridden_ids: Conflicts -- the owner set this row's amount by hand.
        deleted_ids: Conflicts -- the owner removed this row.
        retained_ids: Conflicts -- the row carries the owner's own records, and
            applying the definition change would have destroyed or
            re-attributed them (finding **N-292**).
    """

    update: list
    create_in: "list[PlacedRow]"
    retire: list
    overridden_ids: "list[int]"
    deleted_ids: "list[int]"
    retained_ids: "list[int]"


class UnruledWork(NamedTuple):
    """What a RULE-LESS definition's edit will do to its rows, decided first.

    :func:`classify_unruled_work` fills this by reading only, and each
    engine's twin writes -- the shape :class:`MaintainWork` gives the
    regular pass, for the one definition kind that does not regenerate.

    Attributes:
        update: The rows the definition speaks to: every row handed in except
            those retained.
        retained_ids: Conflicts -- the definition moved the account the row's
            records are attributed to (finding **N-292**), so the row is left
            exactly as found and the owner is told.
    """

    update: list
    retained_ids: "list[int]"


def classify_unruled_work(rows, *, with_records, reattributed) -> UnruledWork:
    """Decide what a rule-less definition's edit does to each row, WITHOUT writing.

    **The maintain pass's ONE retention rule, for the shape that has no
    occurrences to name or drop**: a rule-less definition places its rows by
    hand and no regeneration considers them, so the only refusal left is
    :func:`classify_maintain_work`'s account arm -- a row whose account the
    definition moved and which holds the owner's records is retained; every
    other row takes the definition whole.  Both engines' twins
    (``transfer_recurrence.propagate_to_unruled_template``,
    ``recurrence_engine.propagate_to_unruled_definition``) spelled this inline
    beside their own write until plan step ``balance:X-bi-7a``; it lives here
    so the two cannot part, for the reason the regular pass's decision does.
    The TRANSFER twin is its one caller since ``credit_card:CC-5-2`` (ruling
    **R-CC36**: a transaction row's account move re-files nothing).

    Args:
        rows: The definition's live, rule-owned rows, as the route selected
            them (Projected, not overridden, not soft-deleted).
        with_records: The ids among *rows* carrying the owner's own records.
        reattributed: The ids among *rows* an account move would re-file.

    Returns:
        The :class:`UnruledWork`, rows in the order given.
    """
    retained = [row.id for row in rows if row.id in reattributed and row.id in with_records]
    held = set(retained)
    return UnruledWork(
        update=[row for row in rows if row.id not in held],
        retained_ids=retained,
    )


class MaintainOutcome(NamedTuple):
    """What one maintain pass actually did, for the audit event and the raise.

    Attributes:
        created: Rows created this pass -- the value each engine's
            ``regenerate_for_template`` returns, which keeps the meaning every
            caller already reads it with.
        updated: Rows brought into line in place.  Before plan steps R10-a and
            R10-b these were deleted and recreated, so they appeared in
            *created*.
        removed: Rows the rule no longer names that carried nothing.
        overridden_ids: See :class:`MaintainWork`.
        deleted_ids: See :class:`MaintainWork`.
        retained_ids: See :class:`MaintainWork`.
    """

    created: list
    updated: list
    removed: list
    overridden_ids: "list[int]"
    deleted_ids: "list[int]"
    retained_ids: "list[int]"

    @classmethod
    def after(cls, work: "MaintainWork", created: list, updated: list):
        """Return the outcome of applying *work*, given what the write did.

        **Four of the six fields are the DECISION read back**, and only
        *created* and *updated* are news: a row the pass retired is
        ``work.retire``, and the three conflict lists were settled before
        anything was written.  Stating that mapping once is what stops the two
        engines each spelling it out -- pylint's ``duplicate-code`` measured
        exactly this ten-line tail the moment the transfer engine grew its
        maintain pass (plan step R10-b), and a one-sided disable would have
        preserved a copy rather than removed one.

        Args:
            work: The classified :class:`MaintainWork` this pass applied.
            created: The rows the write added.
            updated: The rows it brought into line.

        Returns:
            The :class:`MaintainOutcome` for the audit event and the raise.
        """
        return cls(
            created=created,
            updated=updated,
            removed=work.retire,
            overridden_ids=work.overridden_ids,
            deleted_ids=work.deleted_ids,
            retained_ids=work.retained_ids,
        )


def classify_maintain_work(
    selector, existing, placements, *, with_records, reattributed,
) -> MaintainWork:
    """Decide what a maintain pass must do to each row, WITHOUT writing.

    The whole decision of ruling **R-R19**: a row the rule still names is
    maintained, a row it no longer names is retired, and either becomes a
    conflict the moment the owner's own records are in the way.  It READS --
    its own claimants among them -- and writes nothing, so what a regeneration
    decides can still be asserted without a database write.

    **A row is retained rather than changed in exactly two shapes**, and both
    are finding **N-292**: the rule no longer fires for this row's occurrence,
    or the definition has moved the ACCOUNTS the row's records are attributed
    to.  Neither is safe to apply silently, so the pass leaves the row exactly
    as it found it and asks.  The second shape is the engine's to claim.

    **"Still names it" is asked of the row's OCCURRENCE since plan step R17**,
    where it was asked of the row's pay PERIOD.  That is the same re-keying
    ledger row **D57** forced on the generate path, made here for the same
    reason: the period a row sits in is where its money lands, not what the row
    answers, and the owner can move it.  The developer ruled the consequence on
    2026-08-28 -- a row whose occurrence the rule has dropped is NOT named, so
    it retires when it carries nothing and is held back as a conflict when it
    carries the owner's records.  It is never silently re-pointed at whatever
    occurrence is left over in its paycheck: that is a deduction only if every
    row answers some occurrence, and a NULL ``occurs_on`` denies it.  Ruling
    **recurrence:R-R49** is that lesson's home and carries what it cost, an
    adversarial review having cut the same inference from the one-time
    ``occurs_on`` backfill -- where it paired a ``$12.34`` envelope
    roll-forward with a car payment nine paychecks away.  That backfill script
    was RETIRED 2026-09-06, so the ruling is the citation that still resolves.

    **A NULL ``occurs_on`` row answers no occurrence, so it is never named --
    and this pass therefore RETAINS it as a conflict** (finding **REC-516**,
    developer ruling 2026-09-08).  A record-free one used to RETIRE, which is
    a HARD DELETE at ``_maintain`` and at ``transfer_recurrence``, on the
    reading that a row no rule claims is a row the rule dropped; one that
    carried the owner's records was already retained.  Those are different
    facts: *answers no occurrence* is a property of the ROW, *the rule stopped
    naming this occurrence* is a property of the RULE, and only the second
    licenses a deletion.

    **Retaining rather than skipping silently is the developer's ruling, and
    the figure is why.**  The definition has moved past such a row and cannot
    be applied to it: measured on a template repriced ``$100.00 -> $250.00``
    and renamed, the undated row keeps ``$100.00`` and its old name for good
    while its dated sibling moves, and no reader ever learns.  So the owner is
    told, through the word this pass already had for it.

    **The delete was silent and no count could see it**, which is why it stood.
    Reproduced 2026-09-08 on a planted row: the pass deleted it and the create
    arm immediately answered the freed occurrence, so the engine reported
    ``deleted_count: 1, created_count: 1`` with every conflict count at zero
    and the table the same size before and after.

    *How many rows this reaches is a property of the DATABASE, not of this
    code, and the two the developer runs disagree.*  Measured 2026-09-08:
    PRODUCTION holds six undated transactions, all immutable, and 54 undated
    transfers of which three are immutable and 51 soft-deleted -- so none was
    reachable, though restoring any of those 51 would have armed one.  The DEV
    database was cloned from a pre-R17 production and holds 622 undated
    transactions and 175 undated transfers, **598 of them mutable, carrying no
    owner records, and reachable by this branch before it became a skip**.
    A count measured on one database is not a property of the predicate; what
    is safe to rely on is the branch's own rule, not the population it happens
    to find.

    **A retained undated row also SUPPRESSES the occurrence its paycheck
    holds**, and that is the cost of the claim above rather than a second
    defect: the row holds its whole pay period, so an occurrence the rule
    NAMES there is blocked and no row is written for it -- measured at one
    unanswered occurrence on a single moved row.  Retiring the row used to
    free the paycheck and let the create arm answer it.  This pass now agrees
    with the GENERATE path, which has held a paycheck this way since **R17**,
    so the two stopped disagreeing about what an undated row claims.

    **This branch is scaffolding and says so.**  The root cause is that a
    template-linked, non-override row can exist without recording the
    occurrence it answers -- ``carry_forward_service`` already avoids it by
    flagging its rows ``is_override``, and the one-time transfer branch
    (``routes/transfers/_instances._materialize_one_time_transfer``) does not.
    When every such row records an occurrence this branch stops being
    reachable and is deleted rather than kept quiet.

    **The claims that decide CREATE come from *claimants*, NOT from *existing*,
    and that distinction is ledger row D57 on this path.**  *existing* is a
    PERIOD set -- the pass's write window, bounded by its ``effective_from`` --
    and the row that answers an occurrence need not be in it: the owner may
    have moved that row to a paycheck the window does not reach.  An
    adversarial review of this leaf measured the consequence at the service
    seam, through the salary door (``salary_regeneration`` regenerates with
    ``effective_from=date.today()``): a row moved back one paycheck left
    ``existing`` while its occurrence stayed named, and the create arm answered
    that occurrence a SECOND time -- silently where the moved row is still an
    override, and as an unhandled ``IntegrityError`` once the conflict chooser
    has cleared that flag.  :func:`rows_claiming` is period-unscoped for
    exactly this reason, and the generate path has consumed it from the start.

    **The retired rows are removed from the claimants first, and since finding
    REC-516 that subtraction can remove nothing.**  It was load-bearing while
    an undated row could retire: such a row holds its whole paycheck
    (:class:`OccurrenceClaims`), so classifying after the claims read let one
    row both block a write and be deleted in the same pass, leaving a period
    the rule names with no row at all.  Now that an undated row is retained,
    the two sets are DISJOINT by construction -- every row in ``work.retire``
    has an ``occurs_on`` that is not ``None`` and not in ``named``, while
    :func:`rows_claiming` returns only rows whose ``occurs_on`` is in ``named``
    or is ``None``, both derived from the same ``placements``.  **It is kept as
    a FORWARD GUARD, not because its old reason still holds**: it costs one set
    build and it is what stops a future arm that routes an undated row back to
    ``retire`` from silently re-creating the pairing.

    **Shared by both engines since plan step R10-b, over two ID SETS rather
    than a model.**  Every question this asks about a row is one both a
    Transaction and a Transfer answer identically, and the two that are NOT
    (what "records" means, and what "the definition moved the accounts" means,
    since a transfer has two of them) arrive already resolved as sets of ids.

    Args:
        selector: This pass's :class:`TemplateRowSelector` -- what
            :func:`rows_claiming` is asked about.  Taken rather than handed the
            claimants themselves, because "the rows that already answer these
            occurrences" and "the rows this pass may maintain" are two
            DIFFERENT reads that a caller could pair wrongly, and two values
            that have to agree is the shape this module exists to remove.
        existing: Every row of this template in the pass's WRITE WINDOW at
            or after its bound.  The window half is the load-bearing one:
            it is what keeps this domain a superset of the plan's, and so
            what makes the RETIRE branch reachable.  It decides CLASSIFICATION
            -- update, retire, retain, and retain-as-undated -- and
            deliberately not creation.
        placements: The occurrences the rule names now -- this pass's
            ``recurrence_engine.PlannedOccurrence`` values, duck-typed on
            ``.occurrence`` and ``.period.period_id`` as everything else in
            this module is.  Empty for a template whose recurrence was
            CLEARED, which correctly makes every row an orphan.  It was a
            ``{period_id: occurs_on}`` map for one step, between plan step R17's
            two leaves; a map keyed by period cannot state two occurrences in
            one paycheck, which is exactly what the re-keyed index now stores.
        with_records: Ids of rows carrying the owner's own records, resolved by
            the engine from its own table -- purchases, a note, a settlement
            record or a statement link.
        reattributed: Ids of rows an ACCOUNT move would re-file (**R-CC36**).

    Returns:
        The :class:`MaintainWork` this pass should apply.
    """
    work = MaintainWork([], [], [], [], [], [])
    named = {placement.occurrence for placement in placements}
    for row in existing:
        hold = owner_hold_on(row)
        if hold == BLOCK_IMMUTABLE:
            continue
        if hold == BLOCK_OVERRIDE:
            work.overridden_ids.append(row.id)
            continue
        if hold == BLOCK_DELETED:
            work.deleted_ids.append(row.id)
            continue
        # An UNDATED row is RETAINED, never retired (finding **REC-516**,
        # developer ruling 2026-09-08).  It answers no occurrence, so it is
        # not the rule's row to rewrite; but "answers no occurrence" is not
        # "the rule dropped this occurrence", and only the second is a reason
        # to delete.  It still claims its PAY PERIOD through
        # :class:`OccurrenceClaims`, so leaving it here cannot let the create
        # arm answer that paycheck twice.
        #
        # It is RETAINED rather than skipped silently because the definition
        # has moved past it and cannot be applied to it: a template repriced
        # $100.00 -> $250.00 leaves this row at $100.00 for good, and it
        # suppresses the correctly-priced row its paycheck would otherwise
        # receive.  ``retained_ids`` is this pass's existing word for exactly
        # that -- *the definition moved and I refused to apply it* -- and the
        # pre-REC-516 code already raised it for the undated rows that carried
        # the owner's records, so this restores that prompt rather than
        # inventing one.  It reports without blocking: ``_pass`` writes the
        # creates and updates, then raises.
        if row.occurs_on is None:
            work.retained_ids.append(row.id)
            continue
        if row.occurs_on not in named:
            if row.id in with_records:
                work.retained_ids.append(row.id)
            else:
                work.retire.append(row)
            continue
        if row.id in reattributed and row.id in with_records:
            work.retained_ids.append(row.id)
            continue
        work.update.append(row)

    retiring = {row.id for row in work.retire}
    claims = OccurrenceClaims.over([
        row for row in rows_claiming(selector, placements)
        if row.id not in retiring
    ])
    work.create_in.extend(
        PlacedRow(placement.period.period_id, placement.occurrence)
        for placement in placements
        if not claims.blocks(placement)
    )
    return work


def rows_claiming(selector, placements) -> list:
    """Read what already answers this pass's occurrences, in ONE query.

    The generate path's whole read, shared by both engines.  It replaced
    the generate path's two period-scoped fetches at plan step **R17**'s
    second leaf, and the change is not a reshaping of the same
    fetch: those selected on the PLAN's pay periods, and the row that answers
    an occurrence is not necessarily in the period the plan names for it.  That
    is ledger row **D57** in one sentence -- the owner MOVES a generated row to
    a neighbouring paycheck, so the row that already answers the occurrence
    sits in a period this pass may never look at.  Measured on the developer's
    own data (2026-08-28): row 2447 answers ``2026-04-21`` and sits in period
    3, while the plan names period 2 for that occurrence.  A period-scoped
    fetch cannot see it, so no predicate built on top of one could have closed
    D57 however it was keyed.

    **The query IS the claim rule** (:class:`OccurrenceClaims`), rather than a
    wider fetch the caller then filters: a row counts when it answers one of
    these occurrences, or when it answers NO occurrence and holds one of the
    paychecks this pass would write into.  Pushing both arms into SQL is what
    keeps this a bounded read on the carry-forward hot path, where
    ``can_generate_in_period`` runs once per envelope row being rolled forward
    -- an unfiltered "every row of this template" fetch would grow with the
    owner's whole schedule at every one of those calls.

    Args:
        selector: This pass's :class:`TemplateRowSelector`.
        placements: This pass's ``recurrence_engine.PlannedOccurrence`` values,
            one per occurrence, whose ``period`` may repeat.  Empty
            short-circuits without a query.

    Returns:
        Every row that claims one of these occurrences -- to be read through
        :meth:`OccurrenceClaims.over`, which is the one statement of what a
        claim IS.  **Rows rather than the claims themselves**, because the
        maintain pass subtracts what it is about to retire before reading them,
        and subtracting a claim is not well defined where two rows could make
        the same one.  Since finding **REC-516** that subtraction removes
        nothing -- an undated row is retained rather than retired, so the two
        sets are disjoint -- and the return type is now shaped by the forward
        guard rather than by a live need; :func:`classify_maintain_work` states
        why the guard is kept.
    """
    occurrences = {placement.occurrence for placement in placements}
    period_ids = {placement.period.period_id for placement in placements}
    if not occurrences:
        return []
    model = selector.model
    rows = (
        db.session.query(model)
        .filter(
            selector.template_fk_col == selector.template.id,
            model.scenario_id == selector.scenario_id,
            db.or_(
                model.occurs_on.in_(occurrences),
                db.and_(
                    model.occurs_on.is_(None),
                    model.pay_period_id.in_(period_ids),
                ),
            ),
        )
        .all()
    )
    return rows


def occurrences_to_write(selector, placements) -> list:
    """The occurrences this pass must CREATE a row for, in walk order.

    **The generate path's whole decision, for both engines and for the
    read-only predictor.**  What is left at each call site is the part that is
    genuinely about one table: constructing a ``Transaction`` versus routing a
    ``Transfer`` through ``transfer_service`` for shadow atomicity.

    **Sharing it is what makes ``can_generate_in_period`` an exact mirror
    rather than a second opinion**, which plan step R4b paid for once already:
    that predicate was a hand-written copy of the engine's gating and it
    DISAGREED -- on a production clone it said the engine would generate in 32
    of 61 periods where the real answer was each month's first paycheck only,
    and because the carry-forward executor acts on the prediction and then
    calls generation, the two agreed with each other and wrote a spurious row.
    A prediction that calls this function cannot drift from what the write
    does, because it is the same answer.

    A row is created for an occurrence NOTHING already answers
    (:class:`OccurrenceClaims`): not a live row, not an overridden one, not a
    soft-deleted tombstone, and in whatever paycheck that row now sits in.  A
    row that answers no occurrence at all holds its whole paycheck instead.

    **A pay period may appear more than once in the answer.**  At a pay cadence
    of 30 days or more a monthly bill legitimately falls inside one paycheck
    several times, and since plan step **R17** re-keyed the unique index onto
    ``(template, scenario, occurs_on)`` both rows STORE.  Until then the pass
    was REFUSED outright (plan ledger row D19,
    ``RecurrenceCadenceUnsupported``), because the index held one row per
    paycheck; the refusal and its error handler went with the re-key.

    Args:
        selector: This pass's :class:`TemplateRowSelector`.
        placements: The occurrences the rule names inside this pass's window --
            ``recurrence_engine.PlannedOccurrence`` values.

    Returns:
        The subset to write, in the order the walk produced them.
    """
    claims = OccurrenceClaims.over(rows_claiming(selector, placements))
    return [
        placement for placement in placements
        if not claims.blocks(placement)
    ]


def rows_this_pass_may_maintain(selector, schedule, effective_from) -> list:
    """Fetch the template-linked rows a regenerate pass is allowed to act on.

    Shared by both recurrence engines' ``regenerate_for_template`` to collect
    the rows eligible for the maintain / retire decision.  The only per-engine
    differences are the model class and the template foreign-key column, so
    both are parameters.

    **It selects on a period-ID SET, and the set is the pass's own write
    window** (pay-calendar plan step C2-f3c).  It was
    ``query_rows_from_effective_date``: a ``JOIN budget.pay_periods ON ... WHERE
    pay_periods.end_date >= :effective_from``.

    **This is a CUTOVER, not the repair of a divergence, and an adversarial
    review of C2-f3c is why that is said plainly.**  A first draft of this
    paragraph claimed the old select read the STORED end while
    ``resolve_generation_plan`` filtered the DERIVED one, and it was false:
    that function resolved the ORM row out of the write window BEFORE applying
    the bound, deliberately and with a comment saying so, precisely so both
    halves read the same stored column.  They agreed.  What C2-f3c does is move
    them BOTH onto the derived end, ahead of plan step **C4-c** dropping the
    column they agreed on -- and the reason that was safe is a separate
    invariant, not a bug being fixed: ``pay_period_write`` was the only writer
    of ``end_date`` in ``app/`` and rewrote every stored end from the derivation
    on every pass.  Measured 2026-08-19 on production: 62 periods, zero rows
    where the two differ.  C4-c then removed the column, so there is one end
    and this paragraph records why the swap moved no figure rather than a
    choice anything still has.

    **The domain is the WINDOW, and it must be a SUPERSET of the plan's named
    set** or ``_maintain``'s RETIRE branch could never fire: a row is retired
    precisely because the rule NO LONGER names its period, so the rows offered
    here have to include the periods the plan does not.  They do, by
    construction -- the plan is this same window intersected with the periods
    the rule names, under the same bound.  A superset rather than a strict one:
    where the rule names every period of the window the two sets are EQUAL,
    which is the ordinary case for an every-paycheck template and the case in
    which nothing is retired.  Both live callers pass a whole-schedule window,
    so the set is every one of the owner's periods.

    **Bounding by the window is also what let ``regeneration_bound`` go.** That
    helper turned "no lower bound" into the window's opening date, because a
    sweep bounded only by the SCHEDULE's opening would retire rows from the
    owner's first payday forward while regenerating inside the window alone.
    A window-shaped domain cannot do that whatever the bound is, so ``None``
    once again plainly means "no lower bound".

    Args:
        selector: This pass's :class:`TemplateRowSelector`.
        schedule: The pass's
            :class:`~app.services.generation_schedule.GenerationSchedule` --
            the owner's calendar plus the periods this pass may write into.
        effective_from: Only rows whose pay period ends on or after this date
            are returned, so the current period is included when the date falls
            mid-period.  ``None`` applies no lower bound.

    Returns:
        A list of matching model instances, including soft-deleted and
        immutable rows -- the caller classifies them.
    """
    window = schedule.write_period_ids
    period_ids = [
        period.period_id
        for period in schedule.calendar.saved()
        if period.period_id in window
        and (effective_from is None or period.end_date >= effective_from)
    ]
    return _rows_in_periods(selector, period_ids)


def _rows_in_periods(selector, period_ids) -> list:
    """Return this template's rows, in this scenario, in *period_ids*.

    **THE statement of the query both fetches run.**
    :func:`rows_this_pass_may_maintain` is its only caller since plan step
    **R17** deleted the generate path's period-scoped fetch -- that path now
    selects on the CLAIM (:func:`rows_claiming`), because the row answering an
    occurrence need not sit in the period the plan names for it.  Kept as its
    own function because the maintain domain is genuinely a period SET (the
    pass's write window) and stating that query once is what this module is.

    Args:
        selector: This pass's :class:`TemplateRowSelector`.
        period_ids: The ``budget.pay_periods.id`` values to look in.  Empty
            short-circuits without a query -- ``IN ()`` is not valid SQL and a
            pass with no periods has nothing to find.

    Returns:
        Every matching row, in no particular order.
    """
    ids = list(period_ids)
    if not ids:
        return []
    return (
        db.session.query(selector.model)
        .filter(
            selector.template_fk_col == selector.template.id,
            selector.model.scenario_id == selector.scenario_id,
            selector.model.pay_period_id.in_(ids),
        )
        .all()
    )
