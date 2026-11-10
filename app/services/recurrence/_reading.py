"""
Shekel Budget App -- Reading a stored recurrence rule (plan step R4b-2)

The door a written recurrence is READ through, symmetric with
:mod:`app.services.recurrence._authoring`, which is the door one is written
through.  One composition and its projections:

* :func:`recurrence_spec` -- a rule row's authored state, back out as the
  :class:`~app.services.recurrence.RecurrenceSpec` that authored it.  The write
  door's partial-change idiom is built on it (read the spec, replace one fact
  with ``dataclasses.replace``, re-author the whole value), which is why it is
  a READ living beside the other reads rather than inside the writer.
* :func:`read_rule` -- **THE composition**: resolve the row against the
  owner's schedule, then walk and place its occurrences, keeping BOTH halves
  and the horizon the walk reached (plan step R7d-f-2).
* :func:`resolved_recurrence` -- the first half alone, for a caller that wants
  what the rule MEANS and not where its rows land.
* :func:`rule_occurrences` -- the second half alone, the shape three surfaces
  and the frozen baseline have always taken.
* :func:`placed_occurrences` -- the filter three surfaces take of that answer,
  and :func:`placed_periods`, its projection onto the period.

**One caller needs both halves, and that is why :func:`read_rule` exists**
(plan step R7a).  The Recurring surface resolves every rule to date its "Next"
column and now also to describe its cadence; composing the two steps at the
call site would put the resolve-then-place sequence in two places, so the
composition lives here once and the page takes the value whole.  Nothing is
computed and discarded: a caller that wants only the meaning
(:func:`resolved_recurrence`) never walks an occurrence.

Four surfaces asked :func:`rule_occurrences` the same question when it was
built, and they must not be able to disagree:

* ``recurrence_engine.resolve_generation_plan``, the generation seam both
  engines share, which turns the answer into rows -- since plan step R7d-c-2
  through ``recurring_definition.read_definition``, which is this
  composition with the destination's derived stop applied;
* ``recurring_view``, whose next-date column must name the date the grid cell
  it points at will carry -- through the same door since plan step R7d-d;
* ``routes._recurrence_preview``, the form's live "next five occurrences"
  fragment, which must show what saving would produce -- through the composed
  door since plan step R7d-f-2 (``recurring_definition.resolved_submission``),
  the same walk narrowed by the destination;
* ``tests.oracles.recurrence_baseline``, the frozen behaviour snapshot every
  step of the redesign is measured against.

The door is this module's composition plus one narrowing, so the two readers
that took it still answer from ONE walk; what they no longer share with the
two below is the un-narrowed value.

**This replaced ``recurrence_engine.match_periods``** (plan step R4b-2).  That
adapter answered in PERIODS and applied a caller's lower window bound itself,
so two facts were computed and thrown away: the occurrence DATE, which the
repeat refusal needed to name (plan ledger row D19), and WHY an occurrence had
no pay period (row D7).  Both are in the answer now.  The lower bound went back
to the callers that have one, because it is a display / regeneration boundary
rather than a property of the recurrence, and conflating those two is what
defect D2 was.

It lives here rather than in ``recurrence_engine`` because it writes nothing:
that module carries the session, the models and the row-creation state machine,
and three of the four callers above want none of it.  It lives here rather than
in ``_occurrence`` because it takes an ORM row, and ``_occurrence`` is pure by
contract.

Flask-isolated and read-only: it touches no session and issues no query -- the
owner's schedule arrives as a
:class:`~app.services.pay_calendar.PayCalendar` the caller already holds.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.services.recurrence._bounds import BoundReading, end_bound_from_columns
from app.services.recurrence._placement import (
    OccurrencePlacement,
    occurrence_placements,
)
from app.services.recurrence._frequency import (
    Cadence,
    CadenceReading,
    RecurrenceResolutionError,
    can_repeat_within_month,
    placement_member,
    unit_member,
)
from app.services.recurrence._row_day import cadence_scheduled_day
from app.services.recurrence._vocabulary import (
    modelled_placement,
    modelled_unit,
)
from app.services.recurrence._month_coordinate import (
    offerable_weekday_readings,
    weekday_reading_of,
)
from app.services.recurrence._nominal_day import is_offerable_nominal_day
from app.services.recurrence._resolution import (
    ResolvedRecurrence,
    resolve,
)
from app.services.recurrence._spec import RecurrenceSpec

@dataclass(frozen=True)
class RuleReading:
    """One stored rule, read against its owner's schedule.

    Both halves of :func:`read_rule`'s answer, held together so a caller that
    needs each of them asks once -- and, since plan step R7d-f-2, how far the
    walk that produced them reached.

    Attributes:
        resolved: What the rule MEANS on the two axes, or ``None`` when the
            owner has no pay periods -- see :func:`resolved_recurrence` for why
            that one refusal is answered rather than raised.
        placements: Every ``(occurrence, pay period)`` pair the rule names
            through the schedule's horizon whose row lands where the app keeps
            books (plan step ``pay_calendar:C18-a``); empty when *resolved* is
            ``None``, because a schedule with no periods can host nothing.
        below_the_books: The pairs the rule names that its books drop
            (:attr:`~app.services.recurrence.BooksWalk.below_the_books`) --
            never rows, read by :meth:`bound_reading` alone, because an
            occurrence inside the opening still HAPPENED and a count bound
            spends it (ruling **R-PC94**).  Empty by default: a reading with
            no books floor drops nothing.
        horizon: The last day the schedule the walk ran against reaches
            (:meth:`~app.services.pay_calendar.PayCalendar.horizon`), or
            ``None`` when it holds no pay periods.  **The fact that tells a
            finished definition from an unextended schedule**, carried ON the
            value the walk produced rather than beside it (plan step R7d-f-2,
            plan ledger row **N-514**): :func:`has_ended` took the calendar as
            a third argument until then, so a caller could pair a reading
            with another owner's or another day's calendar and be answered a
            confident "ended" about a live commitment.  Its one caller
            handed the pass's own calendar, so the pairing was kept by a
            contract --
            rule 14's tell -- and now there is nothing to keep: the walk's
            window end IS this value (``occurrence_placements`` walks through
            the calendar's horizon), and the value travels with the placements
            it bounded.
    """

    resolved: ResolvedRecurrence | None
    placements: tuple[OccurrencePlacement, ...]
    horizon: date | None
    below_the_books: tuple[OccurrencePlacement, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a value whose halves disagree.

        A rule that could not be resolved named no occurrence, so placements
        without a meaning is a value that contradicts itself; and a walk that
        placed anything ran against a schedule that reaches somewhere, so
        placements with no horizon is one too
        (``_placement._walk`` answers ``()`` before it walks when the
        horizon is ``None``).  Both counts are over EVERY placement the
        reading carries, the books' dropped half included.  Checks rather
        than docstring guarantees, for the reason
        :class:`~app.services.recurrence.OccurrencePlacement` records in its
        own: this project has been burned by an invariant the generated
        ``__init__`` did not enforce.

        Raises:
            RecurrenceResolutionError: When there are placements but no
                resolved meaning, or placements but no horizon.
        """
        named = len(self.placements) + len(self.below_the_books)
        if self.resolved is None and named:
            raise RecurrenceResolutionError(
                f"a rule reading carries {named} placement(s) "
                f"with no resolved meaning.  A recurrence that could not be "
                f"resolved names no occurrence, so the pair disagrees with "
                f"itself and a caller filtering on one field would read the "
                f"other."
            )
        if self.horizon is None and named:
            raise RecurrenceResolutionError(
                f"a rule reading carries {named} placement(s) "
                f"with no horizon.  A walk places nothing on a schedule that "
                f"reaches nowhere, so the placements were walked against some "
                f"other schedule than the one this value claims."
            )

    def bound_reading(self) -> BoundReading:
        """Return what a closing bound needs to know about this reading.

        The projection every :meth:`~app.services.recurrence.EndBound
        .has_closed` and :meth:`~app.services.recurrence.DerivedStop
        .has_closed` takes: the occurrence dates the walk emitted, and how far
        the schedule reached.  Stated once here rather than at each caller,
        so the two facts a :class:`~app.services.recurrence.BoundReading`
        pairs are read off ONE value and cannot be paired from two.

        **Every occurrence the rule names, the ones its books drop included**
        (ruling **R-PC94**): the books decide which occurrences become rows,
        never when the rule ends, so an "after N times" rule whose first
        occurrence falls inside the opening still finishes on its Nth.

        Returns:
            The :class:`~app.services.recurrence.BoundReading` -- empty
            occurrences and a ``None`` horizon for a reading of an owner with
            no pay periods, which every bounded shape reads as "still owes"
            (see :class:`~app.services.recurrence.BoundReading`).
        """
        return BoundReading(
            occurrences=tuple(sorted(
                placement.occurrence
                for placement in (*self.placements, *self.below_the_books)
            )),
            horizon=self.horizon,
        )


def stored_cadence(rule: RecurrenceRule) -> CadenceReading | None:
    """Return what a stored rule says on both authored axes, or ``None``.

    **The non-raising twin of the two lookups :func:`recurrence_spec` makes**,
    for the callers that must DISPOSE of an unreadable cadence rather than fail
    on it -- the same split
    :func:`~app.services.recurrence.is_authorable` records against the write
    door's cadence refusal and
    :func:`~app.services.recurrence.is_offerable_nominal_day` against its
    nominal-day one.  Built on the same ``modelled_*`` lookups, so the door
    that raises and the form that renders unset can never disagree about which
    rules this application can read.

    **Two callers, and them asking ONE question is the point** (plan step
    R7c-b): ``_recurrence_form_render.edit_form_cadence`` renders the cadence
    controls UNSET when the answer is ``None``, and
    ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` REFUSES the
    empty submission that unset state produces.  Rendering unset is what makes
    the repair reachable; refusing the empty save is what stops the repair
    deleting the rule it came to fix (see
    :data:`~app.routes._recurrence_form_refusals.UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`).
    While the two asked different questions -- the form derived unit AND
    placement from ``pattern_id`` while the refusal asked only about the
    pattern -- a rule readable on one and not the other would have rendered
    unset and then been deleted by an unchanged save.

    **All THREE values come off the row's own columns since plan step R7c-c.**
    The interval was the last one that did not: ``encode_cadence`` wrote ``1``
    for every pattern whose interval was baked into its NAME, so a Quarterly
    rule stored ``interval_n = 1`` and the reader had to recover the ``3``
    through ``stored_interval``.  That leaf re-points the column in the same
    migration that drops ``pattern_id``, so the column says what the cadence
    says and the pattern arm of this function goes with it.

    Args:
        rule: The rule to read.

    Returns:
        The :class:`~app.services.recurrence.CadenceReading`, or ``None`` when
        the row names a unit or a placement this application does not model --
        a ``ref`` row the enums have diverged from, a hand edit, or a partial
        restore.
    """
    unit = modelled_unit(rule.unit_id)
    placement = modelled_placement(rule.placement_id)
    if unit is None or placement is None:
        return None
    return CadenceReading(
        cadence=Cadence(
            interval_n=rule.interval_n, unit=unit,
            max_per_month=rule.max_per_month,
        ),
        placement=placement,
    )


def cadence_of(rule: RecurrenceRule) -> Cadence:
    """Return how often a stored rule fires, with NO schedule involved.

    The projection of :func:`recurrence_spec` taken by the two consumers that
    ask "how often" and never "when" or "which paycheck":
    ``obligations_aggregator``'s monthly equivalent and
    ``calendar_infrequency``'s infrequent-transaction badge, neither of which
    holds a :class:`~app.services.pay_calendar.PayCalendar`.  That absence is
    exactly why both read ``pattern_id`` through a hand-written switch until
    plan step R7a-2b.

    **It took ``(pattern_id, interval_n)`` and lived in ``_frequency`` until
    plan step R7c-c**, which is the move rather than an implementation detail.
    A cadence used to be recoverable from two integers with no row in hand, so
    the function belonged in the pure module; it is now two of the row's own
    columns, so it belongs at the READ DOOR beside every other question asked
    of a rule -- and both callers already held the rule.

    Args:
        rule: The stored recurrence rule.

    Returns:
        The :class:`~app.services.recurrence.Cadence`.

    Raises:
        RecurrenceResolutionError: When the row names a unit this application
            does not model.  Raised rather than answered for the reason
            :func:`~app.services.recurrence._frequency.unit_member` raises: a
            cadence read from a fabricated unit contributes a silently wrong
            figure to the emergency-fund baseline and mis-badges a bill's
            frequency.  A caller that must DISPOSE of an unreadable rule asks
            :func:`stored_cadence`.
    """
    return Cadence(
        interval_n=rule.interval_n, unit=unit_member(rule.unit_id),
        # The third value of "how often" (plan step salary:R15-a): a reader
        # asking the yearly count or the monthly equivalent of a ceilinged
        # rule reads 24 a year and not 26, and it reads it from the value
        # rather than from a second look at the row.
        max_per_month=rule.max_per_month,
    )


def scheduling_day_of_month(rule: RecurrenceRule) -> int | None:
    """Return the day of the month a rule's generated rows are SCHEDULED on.

    **What ``budget.recurrence_rules.day_of_month`` HELD, derived from the
    columns that survive** (plan step R7c-c, developer ruling 2026-08-16 on
    plan ledger row **D37**).  ``recurrence.compute_due_date`` dated every
    generated row from that day until plan step recurrence:R5-a, which dates a
    row from its OCCURRENCE and read this only for whether it was ``None``;
    plan step **R8-b** stopped that too, dating a row from the cadence's unit
    and placement (``_row_day.date_row``), so no generated row's date reads
    this any more.  R7c-c dropped the column ahead of both, so the reader
    reads the derivation the write door was encoding the column FROM rather
    than a column that is gone.

    It is ``_authoring._author``'s own expression, moved rather than restated:
    the day the cadence fires on
    (:attr:`~app.services.recurrence.ResolvedRecurrence.day_of_month`, the join
    of ``starts_on``'s day with ``nominal_day``, and no day for a rule read by
    its weekday), gated on the ``(unit, placement)`` PAIR rather than on the
    unit alone.  Measured on a 2026-08-16
    production clone: 0 of
    46 live rules disagree with the stored column, and the migration that drops
    it asserts the same equality in SQL before the ``ALTER TABLE``.

    **The gate is ``fires_on_day_of_month`` and NOT
    ``has_day_of_month_coordinate``, and the difference moved dates.**  The two
    answer differently for exactly one cadence: a MONTH-unit rule funded from a
    month's FIRST paycheck fires on days of the month, so the second predicate
    says yes -- but the column has always been NULL for it, and NULL is what
    made ``compute_due_date`` date the row from its PAYCHECK.  Answering the
    day here would have been plan ledger row **D26**'s fix arriving in the
    wrong step, measured there at 11 rows.  See
    :func:`~app.services.recurrence.has_day_of_month_coordinate` for the two
    wrong-money defects that came of reaching for the other predicate.

    Reads no calendar, because none of its inputs needs one.

    **``None`` means "no day of the month", and no longer "date this row from
    its PAYCHECK"** (plan step R8-b).  A weekly rule answers ``None`` and its
    rows are dated on their occurrences.  From plan step R8-a until R8-b this
    function REFUSED the ``WEEK`` unit instead
    (``_offer.require_row_date_coordinate``), because ``compute_due_date``
    read ``None`` as "date from the paycheck" and would have dated every
    weekly row on its funding payday; R8-b moved the dating onto the unit and
    placement and deleted the refusal.

    Args:
        rule: The stored (or transient) recurrence rule.

    Returns:
        The day 1-31 the rule's rows are scheduled on, or ``None`` for a
        cadence that schedules on no day of the month -- a pay-period or
        weekly cadence, a calendar one funded from a later paycheck, or one
        read by its weekday, whose day moves from month to month.

    Raises:
        RecurrenceResolutionError: When the row names a unit or a placement
            this application does not model (see :func:`cadence_of`).
    """
    unit = unit_member(rule.unit_id)
    placement = placement_member(rule.placement_id)
    # The gate and the join live in the pure leaf the occurrence walk asks
    # too (plan step pay_calendar:C18-a): one statement of "which day a
    # cadence schedules its rows on", from a rule here and from a resolved
    # value there.  A rule read by its weekday answers ``None``: its day moves
    # from month to month (plan step recurrence:R8-c).
    return cadence_scheduled_day(
        unit, placement, rule.starts_on, rule.nominal_day,
        weekday_reading=weekday_reading_of(rule.weekday_reading),
    )


def recurrence_spec(rule: RecurrenceRule) -> RecurrenceSpec:
    """Read a rule's authored state back out as a spec.

    The inverse of authoring, and what makes a partial change expressible
    without a partial write: a caller that owns ONE fact about a rule reads
    the spec, replaces that fact, and re-authors the whole value.

    **All THREE cadence values come off the row's own columns since plan step
    R7c-c.**  The unit and the placement became authored columns at R7c-b and
    are read through
    :func:`~app.services.recurrence._frequency.unit_member` /
    :func:`~app.services.recurrence._frequency.placement_member`, which refuse a
    stored id this application does not model.  The INTERVAL was the last value
    that still belonged to the closed pattern set -- ``encode_cadence`` wrote
    ``1`` for every pattern whose interval was in its NAME, so the four live
    Quarterly and Semi-Annual rules stored ``(interval_n = 1, unit_id = month)``
    and a reader taking the column at face value would have read them as MONTHLY,
    12 occurrences a year where 4 or 2 are owed.  R7c-c's migration re-points
    the column onto the two-axis interval, so the face value IS the cadence and
    ``stored_interval`` has nothing left to name.

    Args:
        rule: The rule to read.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec` that authored it.
        **Round-trips exactly**, and ``test_recurrence_frequency`` proves the
        cadence half over every authorable cadence rather than by argument.

    Raises:
        RecurrenceResolutionError: When the row names a unit or a placement
            this application does not model, or when it carries both
            closing-bound columns (see :func:`recurrence_spec_with_cadence`).
            A caller that is REPLACING the cadence must take
            :func:`recurrence_spec_with_cadence` instead, which reads no stored
            cadence and therefore cannot fail on one; a caller that must
            DISPOSE of an unreadable rule rather than fail on it asks
            :func:`stored_cadence`.
    """
    return recurrence_spec_with_cadence(
        rule,
        interval_n=rule.interval_n,
        unit=unit_member(rule.unit_id),
        placement=placement_member(rule.placement_id),
        max_per_month=rule.max_per_month,
    )


def recurrence_spec_with_cadence(
    rule: RecurrenceRule,
    *,
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    max_per_month: int | None,
) -> RecurrenceSpec:
    """Read a rule's authored state with a STATED cadence in place of its own.

    The read door for the one caller that OWNS the cadence: the edit form,
    whose whole job is to state one.  :func:`recurrence_spec` is this function
    plus a decode, so "everything about a rule except how often it repeats" has
    one implementation rather than two that agree.

    **Its existence is a defect the two-axis swap would otherwise have
    introduced, measured against ``origin/dev``.**  An edit form meeting a
    stored pattern the application no longer models tells the user to choose a
    cadence before saving (``UNREADABLE_CADENCE_MESSAGE``) -- so choosing one
    IS the documented repair.  Routing that repair through
    :func:`recurrence_spec` made it read the broken cadence on the way to
    replacing it, and the read raised: the one action the surface tells the
    user to take became a 500.  Reading no cadence is what makes the repair
    structural rather than excepted.  (Plan step R7b-2 changed HOW the form
    meets that state -- the controls render unset rather than keeping the
    stored pattern as a trailing option -- and left this function's job
    unchanged.)

    **``offset_periods`` and ``start_period_id`` stopped being read at plan
    step R7b-4**, because the spec stopped carrying them.  A recurrence has
    ONE opening bound; the cycle phase is derived from it rather than read back
    off the column the write door writes it to.  Reading a derived value back in
    is what let a rule state its cadence twice.

    **``day_of_month``, ``month_of_year`` and ``start_date`` stopped being read
    at plan step R7c-b, and they went the same way**: ``starts_on`` is the
    rule's first occurrence, so its day is the cycle's day, its month is the
    cycle's residue class, and it is the earliest thing the cadence produces
    (ruling **R-R16**).  Three columns saying one thing is what plan ledger row
    **D28** measured going wrong -- 18 of 24 live multi-month rules firing in
    the wrong months under the reading it replaced.  ``nominal_day`` rides
    beside it for the one shape a short month loses, and the pair is refused at
    construction if the two disagree.  All five were DROPPED at plan step
    R7c-c, which is what makes the paragraphs above history rather than a rule
    a reader still has to keep.

    Args:
        rule: The rule to read.  Its ``interval_n`` is NOT consulted -- the
            caller states the cadence, which is the whole point of this
            function.
        interval_n: The cadence interval to state.
        unit: The cadence unit to state.
        placement: The placement to state.
        max_per_month: The per-month ceiling to state, or ``None`` (plan step
            salary:R15-a).  The cadence's third value, so the caller that owns
            the cadence states it beside the other two rather than having the
            row's own read back under a unit that may not hold it.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec`.  Its
        ``nominal_day`` is the row's own only where the STATED cadence can hold
        it; see the inline comment on that field.  Its ``max_per_month`` is
        the STATED one, DROPPED where the stated unit cannot repeat within a
        month, for the reason the nominal day is, and its
        ``weekday_reading`` the row's own where the stated unit has a month
        coordinate to read (plan step recurrence:R8-c).

    Raises:
        RecurrenceResolutionError: The row carries BOTH closing-bound columns,
            which ``ck_recurrence_rules_single_end_bound`` refuses in the
            table -- see
            :func:`~app.services.recurrence.end_bound_from_columns` for why
            that is refused rather than resolved to one of the two.  The
            ``(starts_on, nominal_day)`` pair cannot raise from here: a day the
            stated cadence cannot hold is dropped rather than carried, which is
            what keeps the edit form's own cadence change off the 500 path.
            The weekday reading is dropped the same way.
    """
    reading = weekday_reading_of(rule.weekday_reading)
    return RecurrenceSpec(
        user_id=rule.user_id,
        unit=unit,
        starts_on=rule.starts_on,
        interval_n=interval_n,
        placement=placement,
        # DROPPED when the STATED cadence cannot hold it, which is a different
        # question from whether the STORED one could (plan step R7c-b).  The
        # nominal day records a day ``starts_on``'s month was too short for, so
        # it is a refinement of a DAY-OF-MONTH coordinate: a cadence that has no
        # such coordinate has nothing for it to refine, and carrying it across
        # would be a value contradicting the unit beside it.
        #
        # **It is the edit form's own repair path that reaches this**, and
        # leaving the pair alone made that path a 500: switching a "last day of
        # every month" definition to a paycheck cadence built this spec from the
        # SUBMITTED unit and the STORED ``(starts_on, nominal_day)``, so
        # ``RecurrenceSpec.__post_init__`` refused the pair before the caller's
        # ``replace`` could apply the submitted values -- and on a LOAN payment,
        # whose start control is locked and posts nothing, the final value was
        # contradictory too, not merely the intermediate one.  Dropping rather
        # than refusing is the same disposition ``_author`` already takes for
        # the legacy ``day_of_month`` column and ``loan_cadence_start`` for a
        # day-less loan payment: the coordinate is absent, so the refinement is.
        nominal_day=(
            rule.nominal_day
            if is_offerable_nominal_day(unit, rule.starts_on, rule.nominal_day)
            else None
        ),
        # The exclusive arc rejoined into the one value that authored it --
        # the inverse of ``_authoring._author``'s split, and the only other
        # place the two columns are seen apart (plan step R7b-3).
        end_bound=end_bound_from_columns(
            rule.end_date, rule.max_occurrences,
        ),
        # DROPPED where the STATED unit cannot hold it, exactly as the nominal
        # day above: the edit form's repair path states a calendar-month
        # cadence over a stored paycheck one, and carrying a ceiling across
        # would build the pair ``RecurrenceSpec`` refuses (plan step
        # salary:R15-a).
        max_per_month=(
            max_per_month if can_repeat_within_month(unit) else None
        ),
        # DROPPED where the STATED cadence cannot read the first occurrence
        # that way, as the nominal day above, for the same repair path (plan
        # step recurrence:R8-c): a paycheck cadence has no month coordinate.
        weekday_reading=(
            reading if reading in offerable_weekday_readings(
                unit, rule.starts_on,
            ) else None
        ),
    )


def resolved_spec(
    spec: RecurrenceSpec, calendar: PayCalendar,
) -> ResolvedRecurrence | None:
    """Return what *spec* MEANS against its owner's schedule, or ``None`` for none.

    **This is where the empty-schedule refusal is answered rather than
    raised**, and only this one.
    :func:`~app.services.recurrence.resolve` refuses an owner with no pay
    periods -- rightly, since registration bootstraps one and an owner with
    none is a broken invariant -- but the Recurring surface renders every
    definition a user has, and taking a whole page to a 500 for a state no
    rule of THIS rule's is wrong about would be the fence rather than the fix.
    The other four refusals ``resolve`` makes are about the rule ITSELF and
    must not be swallowed with it, which is why this is a guard on one
    condition rather than a short-circuit before the call.  Finding F-10's
    ruling is what closes the question of whether an empty schedule should
    exist at all.

    **Split out of :func:`resolved_recurrence` at plan step R16-b-2** so the
    read pass's resolution memo
    (:meth:`~app.services.balance_at.BalanceContext.resolved_recurrence_of`)
    can key an entry by the spec it reads ONCE and then resolve that same
    value, instead of reading the rule's columns a second time inside the
    resolver: a memo of a pure function is keyed by the function's inputs, and
    this function IS that pure function, with the one answered refusal above
    it.

    Args:
        spec: What the rule AUTHORS, as :func:`recurrence_spec` reads it.
        calendar: The OWNER's WHOLE pay-period schedule, which the spec's first
            occurrence is measured against.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`, or ``None``
        when the owner's schedule holds no pay periods.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved against
            *calendar* -- an unmodelled pattern, a non-positive interval, a
            day / month outside its column's domain, or a spec paired with
            another user's schedule.
    """
    if not calendar.periods:
        return None
    return resolve(spec, calendar)


def resolved_recurrence(
    rule: RecurrenceRule, calendar: PayCalendar,
) -> ResolvedRecurrence | None:
    """Return what *rule* MEANS against its owner's schedule.

    The first of :func:`read_rule`'s two steps, exposed on its own for the
    callers that want the cadence and not its rows -- the Recurring surface's
    archived drawer describes every archived definition and places none.

    Composes the two readers in the module docstring's order: the rule's
    authored state (:func:`recurrence_spec`, the same reader the write door's
    partial-change idiom uses), resolved against the owner's schedule by
    :func:`resolved_spec`, which is where the empty-schedule refusal is
    answered rather than raised.

    Args:
        rule: The stored (or transient) recurrence rule.
        calendar: The OWNER's WHOLE pay-period schedule, which the rule's first
            occurrence is measured against.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`, or ``None``
        when the owner's schedule holds no pay periods.

    Raises:
        RecurrenceResolutionError: When the rule cannot be resolved against
            *calendar* -- an unmodelled pattern, a non-positive interval, a
            day / month outside its column's domain, or a rule paired with
            another user's schedule -- or when its stored cadence names a unit
            or placement this application does not model
            (:func:`recurrence_spec`).  **The unreadable cadence is refused
            BEFORE the empty schedule is answered**, since R16-b-2 split the
            spec read out: it used to be answered ``None`` for an owner with
            no pay periods and refused only once they had some.  A refusal
            about the RULE does not depend on the schedule, so the order is
            the one ``TestItSwallowsNothingElse`` argues for, and it is now
            the same order the read pass's memo takes.
    """
    return resolved_spec(recurrence_spec(rule), calendar)


def read_rule(
    rule: RecurrenceRule, calendar: PayCalendar,
) -> RuleReading:
    """Read *rule* against its owner's schedule, keeping both halves.

    **The composition, held in one place** (plan step R7a): resolve, then walk
    the cadence forward and place each occurrence.  A caller that needs the
    meaning AND the placements -- the Recurring surface, which describes each
    definition's cadence and dates its next occurrence -- takes this rather
    than performing the two steps itself, so the sequence exists once.

    Args:
        rule: The stored (or transient) recurrence rule.
        calendar: The OWNER's WHOLE pay-period schedule.

    Returns:
        The :class:`RuleReading`.

    Raises:
        RecurrenceResolutionError: See :func:`resolved_recurrence`.
        RecurrenceGenerationError: See :func:`rule_occurrences`.
    """
    resolved = resolved_recurrence(rule, calendar)
    return RuleReading(
        resolved=resolved,
        placements=(
            () if resolved is None
            else occurrence_placements(resolved, calendar)
        ),
        horizon=calendar.horizon(),
    )


def rule_occurrences(
    rule: RecurrenceRule, calendar: PayCalendar,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence *rule* names, each with the pay period it lands in.

    The placement half of :func:`read_rule`, and the shape the frozen baseline
    has taken since plan step R4b-2 -- and the generation seam too, until plan
    step R7d-c-2 moved it onto the composed door, whose placements are this
    function's narrowed by the destination.  No production reader calls it
    since then (census 2026-09-11, re-read 2026-09-13): the form preview took
    the composed door too at plan step R7d-f-2
    (``recurring_definition.resolved_submission``) and walks the NARROWED
    value through ``occurrence_placements`` and ``placed_periods``; the oracle
    and the generation tests are what read this.

    **The rule's own window is applied, and a caller cannot bypass it.**
    ``start_date`` binds through the anchor
    :func:`~app.services.recurrence.resolve` derives and ``end_date`` through
    the occurrence engine's stopping bound (ruling R-R6), so a loan payment's
    ``start_date`` still guarantees no installment is generated before the loan
    originates (plan step C9a).

    **It applies the rule's own window and nothing else, which since plan step
    R7d-d is a narrower claim than it reads.**  A definition can also be
    stopped by its DESTINATION -- a transfer into a loan stops when the debt
    does -- and that stop is resolved against a read pass, which this function
    does not take.  ``recurring_definition.read_definition`` is the door that
    composes both; a caller asking THIS one about a loan payment gets the
    rule's answer alone, which is why generation stopped asking it at plan
    step R7d-c-2.  Before plan step R4a both bounds filtered
    candidate PERIODS instead -- ``end_date`` against a period's START -- which
    generated rows dated outside the window the user set (defect D5).

    **A period can appear TWICE, and that is the honest answer.**  At a pay
    cadence of 30 days or more a monthly bill legitimately occurs several times
    inside one paycheck; the reverse matcher walked PAYCHECKS and so silently
    emitted one row for three months of rent (defect D3).
    ``budget.transactions`` HOLDS the separate rows since plan step **R17**:
    ``idx_transactions_template_scenario_occurrence`` is unique over
    ``(template, scenario, occurs_on)``, so a paycheck a cadence names twice
    stores both rows and the WRITE path no longer refuses.  This function
    reports what the cadence NAMES; it does not decide what is storable, which
    is why it rendered the repeats even while they were refused.

    **An occurrence with no pay period is REPORTED, and since plan step C2-b2
    that means ONE thing**: the saved schedule does not reach it, which is every
    schedule's ordinary tail rather than a signal.  The other reading -- a
    schedule HOLE, plan ledger row D7 -- went unconstructible when the calendar
    began deriving each period's end from the next payday, and the
    ``PlacementOutcome`` that told the two apart went with it.

    Args:
        rule: The stored (or transient) recurrence rule.
        calendar: The OWNER's WHOLE pay-period schedule, which the rule's first
            occurrence is measured against.  A subset resolves the rule against
            a pay history the owner does not have -- plan ledger rows D22, D25
            and D2, all measured live on production, all closed at plan step
            R4b-1 by making the schedule a value
            (:class:`~app.services.generation_schedule.GenerationSchedule`).

    Returns:
        One :class:`~app.services.recurrence.OccurrencePlacement` per
        occurrence through the schedule's horizon, ascending by occurrence
        date.  The resolver's own value type rather than ORM rows: this is a
        pure question about a schedule, and the one caller that must WRITE a
        row already holds the owner's rows to map ``period_id`` back onto.

    Raises:
        RecurrenceResolutionError: When the rule cannot be resolved against
            *calendar* -- an unmodelled pattern, a non-positive interval, a
            day / month outside its column's domain, or a rule paired with
            another user's schedule.  The reverse matcher answered ``[]`` for
            the first of those and ``ValueError`` for the third; both now name
            the offending value.  **An EMPTY schedule is the one refusal this
            answers as an empty tuple** -- see :func:`resolved_recurrence`,
            which holds that guard.
        RecurrenceGenerationError: When the resolved value names something the
            occurrence engine cannot walk -- a business-day shift (plan step R8
            is its first author) or a placement with no rule.  Unreachable from
            any value ``resolve`` can produce today, and stated so a later step
            that makes it reachable finds the contract written down.
    """
    return read_rule(rule, calendar).placements


def has_ended(
    rule: RecurrenceRule, reading: RuleReading, *, on: date,
) -> bool:
    """Return whether everything that stops a definition had done so before *on*.

    "Is this still a FUTURE obligation" -- the question
    ``obligations_aggregator`` asks per recurring template to decide whether
    its monthly equivalent belongs in ``/obligations`` and the ``/savings``
    emergency-fund baseline.

    **It answers the COMPOSED closing, since plan step R7d-e**: the bound the
    owner authored AND the stop the definition's destination derives, held in
    one :class:`~app.services.recurrence.Closing` on the resolved value the
    caller hands in.  It read the rule's own two bound columns until then, so
    for a loan payment it read the chokepoints' CACHE of the payoff (ruling
    **R-R56**) and a retired loan's payment stayed in both totals until some
    chokepoint happened to rewrite that column.  Now the reading comes through
    the composed door
    (:func:`app.services.recurring_definition.read_definition`), and the
    Recurring surface's monthly column, its stop line and its next date are
    three readings of ONE narrowing.

    **It replaced a direct ``rule.end_date < as_of`` read, which had no answer
    for a count bound at all** (plan step R7b-3).  That read was correct while
    a date was the only bound anything wrote; the moment the "Ends" control
    could author "after N occurrences", a spent count would have gone on
    inflating both figures forever -- while the SAME row's "Next" column, which
    walks occurrences, showed blank.  One row disagreeing with itself about
    whether a commitment is over.

    **It answers the definition's stops, never the schedule's reach.**  A rule
    whose remaining occurrences fall past the materialised horizon has not
    ended; the schedule simply has not been extended to them, and answering
    "ended" there would silently drop a live commitment out of two money
    totals.  Each shape states its own test for telling those apart, from the
    horizon the reading carries WITH its occurrences.  **It took the calendar
    as a third argument for that horizon until plan step R7d-f-2** (plan
    ledger row **N-514**): the placements had been walked against whatever
    calendar the door was handed while this read ``calendar.horizon()`` off a
    parameter of its own, so a caller pairing a reading with another owner's
    or another day's calendar got a confidently wrong answer about whether a
    payment leaves the Recurring totals and the ``/savings`` floor.  Its one
    production caller (``obligations_aggregator.monthly_or_none``) handed the
    pass's memoised calendar, which is rule 14's tell -- a correspondence
    kept by contract.  The horizon now rides on
    :class:`RuleReading` (the value the walk produced knows how far it
    reached), so the pairing is unrepresentable rather than maintained.

    **Every bounded shape answers from whether the definition still owes an
    occurrence** (developer ruling 2026-08-13, plan ledger row **D33**, and
    ruling **R-R57** for the derived stop).  The date shape used to answer the
    narrower "has the bound date passed", so a yearly bill bounded at year end
    went on counting for eleven months after its last payment while the same
    schedule written as a count did not.

    **The reading is TAKEN, not made here.**  Until R7d-e this function
    resolved and walked the rule itself, lazily, whenever a bounded shape had
    to look past its cheap arm -- a second resolution of such a rule on the
    Recurring surface, which already reads each definition once through the
    door (measured on dev ``950f661a``: a loan payment with a synced column
    resolved twice per render, once with the column NULL).  The caller's
    placements ARE the walk the shapes need (every occurrence through the
    schedule's horizon, emitted under the composed closing -- the half its
    books keep beside the half they drop, ruling **R-PC94**), so the
    :class:`~app.services.recurrence.BoundReading` is built from them and a
    definition read once is walked once (``CLAUDE.md`` rule 14, ONE WALK).
    *Read once* was the caller's part until plan step R7d-f-2: the Recurring
    surface reads each definition once per build, while a ``/savings`` render
    reads a transfer from checking into a goal account in BOTH its committed
    floor and that goal's contribution, once per set -- two reads that
    predate R7d-e and that each cost a resolve and a walk where an unbounded
    rule used to cost neither (plan ledger row **N-513**).  The resolve
    became the pass's memo at R16-b-2 and the walk at R7d-f-2
    (``BalanceContext.placements_of``), so the second read is memo hits and
    "read once" is structural rather than a discipline of the caller's.

    **An owner with no pay periods is answered from the AUTHORED bound alone,
    exactly as before this step.**  The door resolves nothing for such an
    owner (``reading.resolved`` is ``None``), so there is no composed closing
    to ask and no derived stop to fold -- a loan's payoff is a fold over a
    schedule that does not exist.  What R-R45 still decides there is the
    bound the owner wrote: a date bound that has PASSED owes nothing whatever
    the schedule, and the other shapes read an empty walk as "still owes".
    That is the answer the lazy walk gave before this step, kept rather than
    collapsed to "still owes" for every shape, which would have counted an
    expired template.  The state is a broken invariant (registration
    bootstraps a period; finding **F-10** owns the writer that could empty a
    schedule), reachable in a test and refused by the Recurring surface's own
    describer; this producer feeds two money totals and takes the honest
    answer rather than a refusal because its callers sum over every
    definition and one refusal would blank both figures.

    Args:
        rule: The stored recurrence rule, read ONLY when *reading* resolved
            nothing, for the authored bound its two columns hold.  The same
            producer the composed value's authored half came from
            (:func:`~app.services.recurrence.end_bound_from_columns`), reached
            directly because there is no resolved value to carry it.
        reading: The definition read through the composed door.  Its
            ``resolved`` carries the closing; its ``placements`` and
            ``below_the_books`` are every occurrence the definition names
            through the schedule's horizon, the books' dropped half included
            (ruling **R-PC94**), and its ``horizon`` is that horizon -- the
            fact that tells a
            finished definition from an unextended schedule, read off the
            reading through :meth:`RuleReading.bound_reading`.
        on: The day being asked about, normally today.

    Returns:
        ``True`` when nothing the definition names falls on or after *on*
        because a stop -- authored or derived -- ended it.
    """
    resolved = reading.resolved
    if resolved is None:
        return end_bound_from_columns(
            rule.end_date, rule.max_occurrences,
        ).has_closed(on=on, reading=reading.bound_reading)
    return resolved.closing.has_closed(on=on, reading=reading.bound_reading)


def placed_occurrences(
    placements: Iterable[OccurrencePlacement],
    *,
    ending_on_or_after: date | None = None,
) -> list[OccurrencePlacement]:
    """Keep the *placements* a caller can show or write, occurrence and all.

    The filter every display surface takes of :func:`rule_occurrences` -- the
    Recurring surface's next-date column, the form's occurrence preview, and
    the frozen baseline oracle -- held once so they cannot come to filter
    differently.  **It keeps the whole placement since plan step
    recurrence:R5-a**, because a row is dated from its OCCURRENCE now
    (:func:`~app.services.recurrence.compute_due_date`, ruling **R-R94**) and
    a surface that states the date a row carries has to hold the occurrence
    to ask it; :func:`placed_periods` is the same filter projected onto the
    period alone, for a surface that asks nothing else.

    The generation seam does NOT use it: that path needs the write window
    too, so it walks the placements itself.

    Args:
        placements: The answer from :func:`rule_occurrences`.
        ending_on_or_after: Drop placements whose period ENDS before this
            date.  ``None`` (the default) applies no bound.  It is the
            CALLER's display or regeneration boundary and never the rule's
            own -- the rule's opening bound is its anchor, and conflating the
            two is what defect D2 was.  **The end it compares is the DERIVED
            one** (plan step C2-b2), so on a schedule whose stored column
            disagrees this can keep or drop a period the stored value would
            not -- see ``recurrence/_occurrence.py``'s module docstring for
            the three shapes.  Every caller of this filter is a DISPLAY
            surface; the generation seam applies its own bound to the ORM row
            it resolves, for exactly that reason (``recurrence_engine``'s
            ``resolve_generation_plan``), because that bound also has to
            agree with an SQL sweep over the stored column.

    Returns:
        The placed placements, ascending by occurrence date, every one with
        a period.
    """
    return [
        placement
        for placement in placements
        if placement.period is not None
        and (
            ending_on_or_after is None
            or placement.period.end_date >= ending_on_or_after
        )
    ]


def placed_periods(
    placements: Iterable[OccurrencePlacement],
    *,
    ending_on_or_after: date | None = None,
) -> list[DerivedPeriod]:
    """Project *placements* onto the pay periods a caller can show or write.

    :func:`placed_occurrences`' filter, keeping the period alone -- what the
    form's occurrence preview reads.  It is exactly what the retired
    ``match_periods`` adapter returned, which is also why the baseline blob
    did not move when the adapter went.

    Args:
        placements: The answer from :func:`rule_occurrences`.
        ending_on_or_after: See :func:`placed_occurrences`.

    Returns:
        The placed periods, ascending by occurrence date, one entry per
        occurrence and therefore possibly repeating.
    """
    return [
        placement.period
        for placement in placed_occurrences(
            placements, ending_on_or_after=ending_on_or_after,
        )
    ]


__all__ = [
    "RuleReading",
    "cadence_of",
    "has_ended",
    "placed_occurrences",
    "placed_periods",
    "read_rule",
    "recurrence_spec",
    "recurrence_spec_with_cadence",
    "resolved_recurrence",
    "resolved_spec",
    "rule_occurrences",
    "scheduling_day_of_month",
    "stored_cadence",
]
