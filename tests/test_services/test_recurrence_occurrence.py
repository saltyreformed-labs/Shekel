"""The forward occurrence engine (plan steps R3 and R4a).

``app.services.recurrence.occurrences`` walks a rule's cadence forward and
``place`` carries each occurrence onto a pay period.  Plan step R3 built both
parallel and unread; plan step R4a made
``recurrence_engine.match_periods`` a thin adapter over them (plan step R4b-2
replaced it with ``recurrence.rule_occurrences``), so every pay
period the application generates a row into is now selected here.

**What :class:`TestTheParallelRun` asserts changed at R4a, and the change is
the step.**  It used to drive the NEW engine through
``tests/oracles/recurrence_baseline.py``'s shapes and compare against a
snapshot frozen from the OLD one, with 12 shapes declared to diverge.  The
cutover made the forward engine the engine, the snapshot was re-frozen from it
(+122 / -4 lines over exactly those 12 shapes), and the old-versus-new
comparison now lives in that commit's diff.  What the class asserts now:

* **the snapshot is what the engine answers, for all 430 shapes.**  Not a
  tautology -- the snapshot goes through the adapter (ORM-row mapping, the
  ``effective_from`` floor, occurrence ordering including repeats) and this
  side drives ``resolve`` / ``occurrence_placements`` directly, so equal lists
  prove the adapter reports the engine rather than reshaping it;
* **no ``bounds.*`` shape fires outside its own window** (ruling R-R6, plan
  defect D5), asserted over all 8 rather than the 4 that moved, plus the four
  rows R-R6 named checked from three directions so a stale declaration fails;
* **the ``long_cadence.*`` answers match an INDEPENDENT day-by-day sweep**
  (:func:`_day_sweep_occurrences`) placed by linear scan, because "the new
  number is bigger" is not a proof that it is right.  This is the one oracle
  in the file that is not the engine, the snapshot, or a re-frozen version of
  either, and it is what the R4a re-freeze rests on;
* **every emitted occurrence either places or is named in advance**
  (:data:`_EXPECTED_UNPLACED`).  The snapshot records PERIODS, so an
  occurrence with nowhere to live has no line in it: a neutral review built a
  mutant emitting one unplaceable occurrence per rule and left every other
  test green.

**Five of the eight long-cadence shapes did not exist before plan step R3.**
The R1 builder covered ``MONTHLY`` and ``QUARTERLY`` only, so
``MONTHLY_FIRST``, ``SEMI_ANNUAL`` and ``ANNUAL`` were unmeasured at any
cadence but the developer's -- and ``MONTHLY_FIRST`` turned out to be the one
that mattered
(:meth:`TestTheParallelRun.test_monthly_first_defers_several_months_onto_one_paycheck`).
``ANNUAL`` and ``long_cadence.every_period`` are the controls; both agreed at
90 days and neither moved.

The rest of the file exercises the engine directly, at exact dates against
hand-built schedules -- no database, no clock -- including the three things
``resolve`` cannot yet produce and only a hand-built
:class:`~app.services.recurrence.ResolvedRecurrence` can reach: the ``WEEK``
unit and a business-day shift.  A COUNT bound was a third until plan step
R7b-3 gave it a form control.
"""

import calendar as calendar_module
import functools
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    NEVER_ENDS,
    Closing,
    DayOfMonth,
    DerivedStop,
    EndsOnDate,
    RecurrenceGenerationError,
    RecurrenceResolutionError,
    ResolvedRecurrence,
    WeekdayReading,
    has_day_of_month_coordinate,
    occurrence_placements,
    occurrences,
    place,
    projected_occurrence_placements,
    resolve,
)
# Imported as a MODULE so the firing controls below can patch the functions the
# composition resolves at CALL time.  Patching this file's own imported names
# would leave ``occurrence_placements`` calling the real ones, making a control
# that passes prove nothing -- the same reasoning
# ``tests/oracles/recurrence_baseline.py`` records for the old engine.
from app.services.recurrence import _months, _occurrence, _placement, _resolution
from app.services.recurrence import EndBound, EndsAfterOccurrences

from tests._test_helpers import (
    era_of,
    eras_of,
)
from tests.oracles import recurrence_baseline
from tests.test_services.test_recurrence_resolution import build_calendar
#: The committed R1 snapshot the parallel run is measured against.
BASELINE_PATH = Path(recurrence_baseline.__file__).with_suffix(".txt")

#: The four ``bounds.*`` shapes ruling R-R6 predicted would move, each mapped
#: to the ONE ``(period_index, occurrence date)`` it drops.  Occurrence-bounded
#: generation refuses a row dated outside its own rule's window; every entry
#: here is such a row, and the dates are read off the committed blob's ``due=``
#: column, which for these day-15 rules IS the occurrence date.
_BOUND_DIVERGENCES: dict[str, tuple[int, date]] = {
    # start_date 2024-06-16, on period 11's last day: the June occurrence is
    # the 15th, one day BEFORE the rule begins.
    "bounds.start.on_period_end": (11, date(2024, 6, 15)),
    # end_date 2025-06-05, mid-period: the June occurrence is the 15th, ten
    # days AFTER the rule ends.
    "bounds.end.midperiod": (37, date(2025, 6, 15)),
    # end_date 2025-06-02, on period 37's first day: same June occurrence.
    "bounds.end.on_period_start": (37, date(2025, 6, 15)),
    # start 2024-06-05 + end 2025-06-05: only the end side moves.
    "bounds.window.both": (37, date(2025, 6, 15)),
}

#: The eight ``long_cadence.*`` shapes that move, each mapped to the calendar
#: cycle it names as ``(base_month, month_step, nominal_day, placement)`` --
#: read off the shape's own label, not off the engine.
#: :func:`_day_sweep_occurrences` rebuilds the occurrence list from these by
#: inspecting every calendar day, and the placement picks which independent
#: linear placer carries them onto a paycheck.
_LONG_CADENCE_DIVERGENCES: dict[
    str, tuple[int, int, int, PeriodPlacementEnum],
] = {
    "long_cadence.monthly.dom01": (1, 1, 1, PeriodPlacementEnum.CONTAINING_DATE),
    "long_cadence.monthly.dom15": (1, 1, 15, PeriodPlacementEnum.CONTAINING_DATE),
    "long_cadence.monthly.dom31": (1, 1, 31, PeriodPlacementEnum.CONTAINING_DATE),
    "long_cadence.quarterly.moy01.dom01": (
        1, 3, 1, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    "long_cadence.quarterly.moy01.dom15": (
        1, 3, 15, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    "long_cadence.semi_annual.moy01.dom01": (
        1, 6, 1, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    "long_cadence.semi_annual.moy01.dom15": (
        1, 6, 15, PeriodPlacementEnum.CONTAINING_DATE,
    ),
    # The one that DEFERS rather than contains: its occurrences are the 1st of
    # every month and each is carried onto the next paycheck, so at a 90-day
    # cadence three months land on one.  See
    # ``TestMonthlyFirstDefersOntoOnePaycheck``.
    "long_cadence.monthly_first": (
        1, 1, 1, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
    ),
}

#: The occurrences the schedule cannot host, per shape, and the ONLY ones.
#:
#: **Declared because the comparison is otherwise not total.**  The committed
#: snapshot records PERIODS, so an occurrence that places nowhere has no line
#: in it and simply vanishes from an index-to-index diff -- a neutral review
#: demonstrated a mutant that emits one unplaceable occurrence per rule and
#: left every test in this file green.  All THREE entries are real and all
#: three are ``PERIOD_STARTING_ON_OR_AFTER``: an occurrence dated after the last
#: PAYDAY has no paycheck to defer onto, even when it is still inside the
#: schedule's covered span.  The biweekly schedule's last payday is 2026-12-28
#: against a horizon of 2027-01-10, so January 2027's occurrence is emitted and
#: unplaced.
#:
#: **Every one of them is the ordinary tail rather than a schedule HOLE**, a
#: distinction plan step R4b-2 introduced as :class:`PlacementOutcome` and plan
#: step C2-b2 dissolved: derived periods tile their covered span, so a hole is
#: unconstructible and ``period is None`` has one meaning.  Both baseline
#: schedules were contiguous anyway; conflating the two is what made
#: generation's first draft report a healthy schedule as corrupt.
_EXPECTED_UNPLACED: dict[str, list[date]] = {
    "monthly_first": [date(2027, 1, 1)],
    "long_cadence.monthly_first": [
        date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1),
    ],
    # Plan step R4b-2's ledger row D10 shape: a ``Monthly First`` rule bounded
    # 2026-10-01, past the long schedule's last payday 2026-09-17.  The anchor
    # comes from ``_first_of_month_anchor``'s FALLBACK, which always answers a
    # date after the last payday -- so under
    # ``PERIOD_STARTING_ON_OR_AFTER`` no paycheck can host either occurrence,
    # and the horizon-dependent anchor cannot reach a generated row.  The
    # measurement is in
    # ``test_recurrence_resolution.TestTheHorizonDependentFirstOfMonthAnchor``.
    #
    # **This dict, not the two ``(none)`` blob lines, is what gates the D10
    # shapes.**  A blob keyed on PERIODS cannot record an occurrence that has
    # none, so ``(none)`` says only "generated nothing" and is insensitive to
    # the anchor moving by a month -- which is D10's own failure direction.
    # Measured by a neutral review: two one-month mutants of the fallback left
    # the blob unmoved and turned the assertion over this dict RED.
    "horizon_bound.long_cadence.monthly_first": [
        date(2026, 11, 1), date(2026, 12, 1),
    ],
}

#: The owner every calendar and resolved value in this file names.
_USER_ID = 1


def resolved_value(
    *,
    unit: RecurrenceUnitEnum,
    starts_on: date,
    interval_n: int = 1,
    offset_periods: int = 0,
    placement: PeriodPlacementEnum = PeriodPlacementEnum.CONTAINING_DATE,
    shift: BusinessDayShiftEnum = BusinessDayShiftEnum.NONE,
    end_bound: EndBound = NEVER_ENDS,
    derived: DerivedStop | None = None,
    nominal_day: int | None = None,
    max_per_month: int | None = None,
    weekday_reading: WeekdayReading | None = None,
) -> ResolvedRecurrence:
    """Return a two-axis value stated directly, bypassing ``resolve``.

    The engine consumes :class:`~app.services.recurrence.ResolvedRecurrence`,
    and two of its fields have no producer yet -- the ``WEEK`` unit and the
    business-day shift both wait on plan step R8.  Stating the value here is
    what lets those be tested before their author exists; the shapes
    ``resolve`` CAN produce are covered by the parallel run against every one
    of them.

    A COUNT bound was a third such field until plan step R7b-3, whose form
    control is its first author.

    Args:
        unit: The cadence unit.
        starts_on: The first occurrence, one meaning for every unit since
            plan step R7c-b -- so a ``PERIOD`` value stated here must
            already be a payday, which ``resolve`` would have normalised it
            onto.
        interval_n: Units between occurrences.
        offset_periods: Phase within the ``PERIOD`` cycle.
        placement: How an occurrence maps onto a pay period.
        shift: Weekend/holiday adjustment.
        end_bound: The bound the OWNER authored -- when the rule itself says
            it stops.
        derived: A stop the definition did NOT author, from something outside
            the rule (plan step R7d-d).  ``None`` is "nothing outside the rule
            bounds this", which is every definition whose destination is not a
            configured loan.
        nominal_day: The day the rule means when the first occurrence's
            month clamped it.
        max_per_month: The per-month ceiling (plan step salary:R15-a), or
            ``None`` for none.
        weekday_reading: How the first occurrence is read by its weekday
            (plan step R8-c), or ``None`` for its day of the month.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`.
    """
    return ResolvedRecurrence(
        offset_periods=offset_periods,
        interval_n=interval_n,
        unit=unit,
        starts_on=starts_on,
        placement=placement,
        shift=shift,
        closing=Closing(authored=end_bound, derived=derived),
        nominal_day=nominal_day,
        max_per_month=max_per_month,
        weekday_reading=weekday_reading,
    )


def dates_through(
    resolved: ResolvedRecurrence, calendar: PayCalendar, through: date,
) -> list[date]:
    """Return the rule's occurrence dates through *through*.

    Args:
        resolved: The two-axis value.
        calendar: The owner's schedule.
        through: The last day to generate through.

    Returns:
        The occurrence dates, ascending.
    """
    return list(occurrences(resolved, calendar, through=through))


def placed_indices(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    **kwargs,
) -> list[int]:
    """Return the period indices the rule's placed occurrences land in.

    Args:
        resolved: The two-axis value.
        calendar: The owner's schedule.
        **kwargs: Forwarded to
            :func:`~app.services.recurrence.occurrence_placements`.

    Returns:
        One index per PLACED occurrence, ascending, duplicates kept.
    """
    return [
        placement.period.period_index
        for placement in occurrence_placements(resolved, calendar, **kwargs)
        if placement.period is not None
    ]


def _day_sweep_occurrences(
    first_day: date, last_day: date, base_month: int, month_step: int,
    nominal_day: int,
) -> list[date]:
    """Return a calendar cycle's occurrences by INSPECTING every day.

    The independent oracle for the long-cadence divergences.  Deliberately a
    different algorithm from the engine's: the engine STRIDES over absolute
    month ordinals, this filters the calendar one day at a time.  Two
    implementations of one idea that agree are evidence; the engine agreeing
    with itself is not.

    Args:
        first_day: The first day of the span to sweep.
        last_day: The last day of the span to sweep.
        base_month: The cycle's start month, 1-12.
        month_step: Months between occurrences.
        nominal_day: The day of the month the rule means, before clamping.

    Returns:
        The occurrence dates in the span, ascending.
    """
    found = []
    day = first_day
    while day <= last_day:
        last_of_month = calendar_module.monthrange(day.year, day.month)[1]
        if (
            day.day == min(nominal_day, last_of_month)
            and (day.month - base_month) % month_step == 0
        ):
            found.append(day)
        day += timedelta(days=1)
    return found


def _linear_place(
    day: date, periods: list, placement: PeriodPlacementEnum,
    cadence_days: int,
) -> int | None:
    """Return the index of the period *day* places into, by linear scan.

    The independent oracle for placement: no bisect, no calendar object, and
    the two rules written out separately rather than shared with the engine.

    **The ordinal is the POSITION and the span is computed**, since plan step
    ``pay_calendar:C4-c`` dropped both columns this used to read off the row.
    That makes the oracle more independent rather than less: it had been
    reading values the application's own writer had materialised, and it now
    states the rule itself through
    ``recurrence_baseline.period_last_covered_day``.

    Args:
        day: The date to place.
        periods: Pay periods in payday order, which is ordinal order.
        placement: Which placement rule to apply.
        cadence_days: The cadence *periods* was built at, which is what the
            LAST period's end is projected from.

    Returns:
        The period's ordinal, or ``None`` when none qualifies.
    """
    if placement is PeriodPlacementEnum.CONTAINING_DATE:
        for index, period in enumerate(periods):
            end = recurrence_baseline.period_last_covered_day(
                periods, cadence_days, index,
            )
            if period.start_date <= day <= end:
                return index
        return None
    for index, period in enumerate(periods):
        if period.start_date >= day:
            return index
    return None


def _baseline_schedules() -> tuple[list, list]:
    """Return the baseline's two schedules, biweekly first.

    Returns:
        ``(biweekly periods, long-cadence periods)``.
    """
    return (
        recurrence_baseline.build_schedule(
            recurrence_baseline.SCHEDULE_START,
            recurrence_baseline.SCHEDULE_CADENCE_DAYS,
            recurrence_baseline.SCHEDULE_PERIOD_COUNT,
        ),
        recurrence_baseline.build_schedule(
            recurrence_baseline.SCHEDULE_START,
            recurrence_baseline.LONG_CADENCE_DAYS,
            recurrence_baseline.LONG_CADENCE_PERIOD_COUNT,
        ),
    )


def _empty_calendar() -> PayCalendar:
    """Return the calendar of an owner with no paydays at all.

    It carries a real ``cadence_days``, which plan step C4-d made required:
    this is an owner who HAS a ``budget.pay_schedule`` row and has recorded no
    payday under it -- the state ``pay_period_admin.reset_pay_periods`` passes
    through.  *It carried ``None`` until that step, on plan step C2-b1's rule
    that an absent cadence was legal beside an empty payday set and only there;
    the owner that stood for has no calendar at all now.*  Nothing here reads
    the cadence either way: with no last period there is no projected end for
    it to feed.

    Returns:
        The empty :class:`~app.services.pay_calendar.PayCalendar`.
    """
    return PayCalendar.from_paydays(
        paydays=(), eras=eras_of((), 14), user_id=_USER_ID,
        history_opens_on=None,
    )


def _baseline_calendars(biweekly, long_cadence):
    """Return ``{long_cadence flag: PayCalendar}`` for the two baseline schedules.

    Each calendar states the cadence its schedule was generated at, which plan
    step C2-b2 made an input: a period's last covered day is derived from the
    NEXT payday, and the last one's from the owner's cadence.

    Args:
        biweekly: The 14-day baseline schedule.
        long_cadence: The 90-day baseline schedule.

    Returns:
        The two calendars, keyed by ``RuleShape.long_cadence``.
    """
    return {
        False: recurrence_baseline.build_shape_calendar(
            biweekly, recurrence_baseline.SCHEDULE_CADENCE_DAYS,
        ),
        True: recurrence_baseline.build_shape_calendar(
            long_cadence, recurrence_baseline.LONG_CADENCE_DAYS,
        ),
    }


@functools.cache
def _cached_baseline_calendars() -> dict:
    """Return :func:`_baseline_calendars` built ONCE for the whole module.

    The uncached pair costs two schedule builds per call, which is invisible
    at a handful of call sites and is not at 432: plan step R7c-a's
    walk-agreement class is parametrised over every baseline shape, and
    rebuilding there took the class from seconds to minutes.  Cached rather
    than made a fixture because the value is a pure function of module
    constants -- there is nothing per-test about it.

    The returned calendars are frozen values (``PayCalendar`` is immutable and
    its periods are a tuple), so sharing them across tests cannot leak state.

    Returns:
        ``{long_cadence flag: PayCalendar}``.
    """
    biweekly, long_cadence = _baseline_schedules()
    return _baseline_calendars(biweekly, long_cadence)


def _new_engine_placements() -> dict[str, list[tuple[date, int | None]]]:
    """Drive the NEW engine through every baseline shape.

    Requires an app context: ``resolve`` reads ``ref_cache`` for the pattern
    id.  Each shape is resolved against the SAME schedule the R1 capture used,
    then generated and placed with the engine's default window (the schedule's
    horizon).

    **Every EMITTED occurrence is returned, placed or not.**  Filtering the
    unplaced ones out here is what made an earlier version of this harness
    blind to them; see :data:`_EXPECTED_UNPLACED`.

    Returns:
        ``{shape label: [(occurrence, period_index or None), ...]}``,
        duplicates kept and in occurrence order.
    """
    biweekly, long_cadence = _baseline_schedules()
    calendars = _baseline_calendars(biweekly, long_cadence)
    answers = {}
    for shape in recurrence_baseline.build_shapes():
        calendar = calendars[shape.long_cadence]
        resolved = resolve(
            recurrence_baseline.build_shape_spec(shape), calendar,
        )
        answers[shape.label] = [
            (
                item.occurrence,
                None if item.period is None else item.period.period_index,
            )
            for item in occurrence_placements(resolved, calendar)
        ]
    return answers


def _new_engine_indices() -> dict[str, list[int]]:
    """Return only the PLACED period indices, for the snapshot comparison.

    Returns:
        ``{shape label: [period_index, ...]}``, directly comparable to
        :func:`tests.oracles.recurrence_baseline.parse_baseline`'s output.
    """
    return {
        label: [index for _occurrence, index in pairs if index is not None]
        for label, pairs in _new_engine_placements().items()
    }


def _new_engine_unplaced() -> dict[str, list[date]]:
    """Return the occurrences the schedule could not host, per shape.

    Returns:
        ``{shape label: [occurrence, ...]}``, shapes with none omitted.
    """
    unplaced = {}
    for label, pairs in _new_engine_placements().items():
        dates = [occurrence for occurrence, index in pairs if index is None]
        if dates:
            unplaced[label] = dates
    return unplaced


def _committed_rows() -> dict[str, list[tuple[int, date]]]:
    """Return the committed R1 snapshot, parsed to ``(index, due date)`` rows.

    Returns:
        ``{shape label: [(period_index, due date), ...]}``.
    """
    return recurrence_baseline.parse_baseline_rows(
        BASELINE_PATH.read_text(encoding="utf-8"),
    )


def _committed_indices() -> dict[str, list[int]]:
    """Return the committed R1 snapshot, parsed to period indices.

    Returns:
        ``{shape label: [period_index, ...]}``.
    """
    return recurrence_baseline.parse_baseline(
        BASELINE_PATH.read_text(encoding="utf-8"),
    )


@pytest.mark.usefixtures("app")
class TestTheParallelRun:
    """The shipped engine against the snapshot and an independent oracle."""

    def test_the_snapshot_records_exactly_what_the_engine_answers(self):
        """Every shape: the committed blob IS the forward engine's answer.

        **This assertion changed meaning at plan step R4a, and the change is
        the step.**  It used to compare the new engine against a snapshot
        frozen from the OLD one, with 12 shapes declared to differ; the cutover
        made the forward engine the engine, so the snapshot was re-frozen from
        it and the old-versus-new comparison now lives in that commit's diff
        (+122 / -4 lines over exactly those 12 shapes) rather than in a running
        assertion.  A snapshot is a record of what shipped, not of what was
        replaced.

        What it asserts now is not tautological, and the difference is the
        adapter: the snapshot is captured through
        ``recurrence.rule_occurrences``, whose callers map ``DerivedPeriod``
        values back to the caller's own period rows, applies the per-call
        ``effective_from`` floor, and preserves occurrence order including
        repeats.  This side drives ``resolve`` and ``occurrence_placements``
        directly.  Equal lists prove the adapter REPORTS the engine rather
        than reshaping it -- which is the whole of what R4a claims.
        """
        new = _new_engine_indices()
        committed = _committed_indices()

        assert set(new) == set(committed), (
            "the shape set moved between the snapshot and this run"
        )
        # 434 until plan step R7d-g retired ``bounds.window.inverted`` (the
        # comment on the oracle's bound shapes says why); 433 until plan step
        # recurrence:R5-a removed the 124 ``due_sweep.`` shapes with the
        # column they swept (ruling R-R96).
        assert len(committed) == 309, f"{len(committed)} shapes captured"
        for label in sorted(committed):
            assert new[label] == committed[label], (
                f"{label}: forward engine answers {new[label]}, the committed "
                f"snapshot records {committed[label]}"
            )

    def test_no_occurrence_is_unplaceable_without_being_declared(self):
        """Every emitted occurrence either places or is named in advance.

        **This is what makes the comparison TOTAL**, and it was missing.  The
        committed snapshot records PERIODS, so an occurrence with nowhere to
        live has no line in it and disappears from an index-to-index diff: a
        neutral review built a mutant emitting one unplaceable occurrence per
        rule and left every other test in this file green.  Declaring the
        unplaced set exactly closes that -- a new one fails, and a declared
        one that stops happening fails too.
        """
        assert _new_engine_unplaced() == _EXPECTED_UNPLACED

    def test_every_bounds_shape_fires_only_inside_its_own_window(self):
        """No ``bounds.*`` shape emits an occurrence outside its own window.

        Ruling R-R6's property, stated over the whole family rather than over
        the four members that happened to move.  The reverse matcher used to
        bound PERIODS -- ``end_date`` against a period's START and
        ``start_date`` against its END -- so a period straddling the bound was
        admitted and generated a row whose own occurrence date lay outside the
        window the user set (plan defect D5).  Bounds now bind the occurrence,
        and this asserts it for every occurrence of every bounded shape, so a
        future change cannot re-open the defect on a shape this file does not
        name individually.
        """
        shapes = {
            shape.label: shape for shape in recurrence_baseline.build_shapes()
        }
        placements = _new_engine_placements()
        bounded = sorted(
            label for label in shapes if label.startswith("bounds.")
        )

        assert len(bounded) == 7, f"{len(bounded)} bounds shapes"
        for label in bounded:
            shape = shapes[label]
            for occurrence, _index in placements[label]:
                assert occurrence >= shape.starts_on, (
                    f"{label}: fired {occurrence}, before its first "
                    f"occurrence {shape.starts_on}"
                )
                assert (
                    shape.end_date is None or occurrence <= shape.end_date
                ), (
                    f"{label}: fired {occurrence}, after its end_date "
                    f"{shape.end_date}"
                )
        # An inverted window names no day at all, so it must fire nowhere --
        # stated because "every occurrence is inside the window" is vacuously
        # true of a shape that stopped emitting for an unrelated reason.
        # Built through the PURE resolver since plan step R7d-g, not through
        # the oracle's shape set: the write door refuses to build the pair
        # (``EmptyAuthoredWindowError``) and the table refuses to hold it
        # (``ck_recurrence_rules_valid_window``), but a stored pair can still
        # READ as inverted after a pay-schedule rebuild lifts its normalised
        # start past its stop, and the walk's answer for that -- zero
        # occurrences, never an error -- is what keeps that refusal out of
        # ``resolve`` (the error's own docstring).
        biweekly, _long_cadence = _baseline_schedules()
        inverted = resolve(
            recurrence_baseline.build_shape_spec(recurrence_baseline.RuleShape(
                "bounds.window.inverted", recurrence_baseline.MONTHLY,
                starts_on=date(2025, 6, 15), end_date=date(2024, 6, 5),
            )),
            _baseline_calendars(biweekly, _long_cadence)[False],
        )
        assert not occurrence_placements(inverted, _baseline_calendars(
            biweekly, _long_cadence,
        )[False]), "an inverted window fired"

    def test_each_declared_bound_row_is_gone_and_was_period_bounded(self):
        """The four rows ruling R-R6 named are absent, and each was real.

        The history half, kept because the +122 / -4 snapshot diff is the only
        other record of it and a diff is not re-checked on every run.  Each
        entry is asserted from three independent directions, so a stale
        declaration fails rather than passing silently:

        * the occurrence date is one the shape's own day-15 monthly cadence
          names, computed here rather than trusted;
        * the pay period at the declared index CONTAINS it, which is exactly
          why a period-bounded matcher generated the row;
        * and it is outside the rule's stated window, which is why an
          occurrence-bounded one does not.

        Asserting only "the index is absent from the snapshot" would pass for
        an index that was never there.
        """
        shapes = {
            shape.label: shape for shape in recurrence_baseline.build_shapes()
        }
        biweekly, _long = _baseline_schedules()
        # The ordinal is the POSITION in payday order: these rows are the
        # oracle's own unsaved ones, so no calendar holds them and there is no
        # stored ordinal to read since plan step ``pay_calendar:C4-c``.
        by_index = dict(enumerate(biweekly))
        committed = _committed_rows()

        for label, (index, occurrence) in _BOUND_DIVERGENCES.items():
            shape = shapes[label]
            assert shape.starts_on.day == occurrence.day, (
                f"{label}: {occurrence} is not a day-{shape.starts_on.day} "
                f"occurrence of its own cadence"
            )
            period = by_index[index]
            period_end = recurrence_baseline.period_last_covered_day(
                biweekly, recurrence_baseline.SCHEDULE_CADENCE_DAYS, index,
            )
            assert period.start_date <= occurrence <= period_end, (
                f"{label}: period {index} ({period.start_date}.."
                f"{period_end}) does not contain {occurrence}, so it is "
                f"not the row a period-bounded matcher would have generated"
            )
            # The opening side needs no ``is not None`` guard since plan step
            # R7c-b: a shape ALWAYS states its first occurrence, and a rule
            # never fires before it.
            outside_start = occurrence < shape.starts_on
            outside_end = (
                shape.end_date is not None and occurrence > shape.end_date
            )
            assert outside_start or outside_end, (
                f"{label}: {occurrence} lies INSIDE the rule's window "
                f"({shape.starts_on}..{shape.end_date}), so dropping it "
                f"would be a regression, not defect D5's fix"
            )
            assert (index, occurrence) not in committed[label], (
                f"{label}: the snapshot still records idx={index} dated "
                f"{occurrence}, which the rule's own window excludes"
            )

    def test_each_long_cadence_divergence_matches_an_independent_day_sweep(self):
        """The gained rows are the occurrences a day-by-day sweep finds.

        Plan defect D3.  The expectation is rebuilt from the shape's own cycle
        by inspecting every calendar day in the schedule's span and placing
        each hit by linear scan -- two algorithms unrelated to the engine's
        stride-and-bisect, so agreement is evidence rather than tautology.
        """
        _biweekly, long_periods = _baseline_schedules()
        calendar = recurrence_baseline.build_shape_calendar(
            long_periods, recurrence_baseline.LONG_CADENCE_DAYS,
        )
        placements = _new_engine_placements()
        first_day = long_periods[0].start_date
        last_day = calendar.horizon()

        for label, cycle in _LONG_CADENCE_DIVERGENCES.items():
            base_month, month_step, nominal_day, placement = cycle
            swept = _day_sweep_occurrences(
                first_day, last_day, base_month, month_step, nominal_day,
            )
            expected = [
                (day, _linear_place(
                    day, long_periods, placement,
                    recurrence_baseline.LONG_CADENCE_DAYS,
                ))
                for day in swept
            ]
            assert placements[label] == expected, (
                f"{label}: forward engine answers {placements[label]}, the "
                f"independent day sweep finds {expected}"
            )

    def test_the_long_cadence_shapes_no_longer_drop_months(self):
        """D3 named the loss in months; this asserts every month is owed.

        The 90-day schedule spans 2024-01-01..2026-12-15, thirty-six calendar
        months.  The counts the reverse matcher used to answer, from the
        snapshot plan step R1 froze and plan step R4a replaced, are named in
        the comments: they are what a PERIOD-scanning matcher can find when a
        period spans four months and it inspects two.
        """
        counts = {
            label: len(rows)
            for label, rows in _new_engine_indices().items()
        }

        # A monthly bill is owed in all 36 months.  Was 13 -- and 13 rows over
        # 12 distinct paychecks, so one period was named TWICE: the
        # IntegrityError D3 predicts, since a repeated period is two rows in
        # one (template, period, scenario).
        assert counts["long_cadence.monthly.dom01"] == 36
        # Quarterly starting January: 2024/2025/2026 x Jan/Apr/Jul/Oct.  Was 1.
        assert counts["long_cadence.quarterly.moy01.dom01"] == 12
        # Semi-annual starting January: 2024/2025/2026 x Jan/Jul.  Was 1 -- a
        # worse loss than quarterly's, and invisible until plan step R3 added
        # this shape to the oracle.
        assert counts["long_cadence.semi_annual.moy01.dom01"] == 6
        # The control: pay-period-space generation reads no months at all, so
        # it must not have moved at any cadence.  Asserted against the
        # snapshot as well, which is where a move would show.
        assert (
            _new_engine_indices()["long_cadence.every_period"]
            == _committed_indices()["long_cadence.every_period"]
            == list(range(12))
        )

    def test_monthly_first_defers_several_months_onto_one_paycheck(self):
        """A month with no payday still owes; its bill defers to the next one.

        The behaviour both neutral reviews of plan step R3 surfaced, pinned
        here so it is CHOSEN rather than discovered.  ``Monthly First``
        occurrences are the 1st of every month and the placement carries each
        onto the first paycheck on or after it, so at a 90-day cadence several
        months land on one paycheck.  The old matcher walked PAYCHECKS instead
        and emitted one row each, which silently dropped 24 of the 36 months a
        monthly bill is owed for -- defect D3 reaching a pattern D3 never
        named.

        **The rows stay separate; only the grid sums them** (developer ruling,
        2026-08-07).  Two months of rent funded by one paycheck are two
        obligations: summing at generation would lose which month is unpaid,
        break an amount that changes mid-group, and put one row in front of
        two events.  The paycheck-keyed index could not hold them, which is why
        generation REFUSED such a cadence outright; plan step **R17** re-keyed
        it onto ``(template, scenario, occurs_on)``, so both rows are now
        written and stored and this producer's answer is one a pass can act on.
        """
        _biweekly, long_periods = _baseline_schedules()
        placements = _new_engine_placements()["long_cadence.monthly_first"]
        committed = _committed_indices()["long_cadence.monthly_first"]

        # 36 months owed over 2024-01..2026-12; the last three have no paycheck
        # left to defer onto (the final payday is 2026-09-17).
        assert len(placements) == 36
        assert sum(1 for _o, index in placements if index is None) == 3
        # The 33 that DO place are the snapshot's rows.  The reverse matcher
        # answered once per paycheck -- list(range(12)), 12 rows for 36 months
        # -- which plan step R4a's re-freeze replaced.
        assert committed == [
            index for _occurrence, index in placements if index is not None
        ]
        assert len(committed) == 33
        # February's and March's bills both fund from the 2024-03-31 paycheck,
        # which opens period 1 -- neither month contains a payday of its own.
        assert placements[1] == (date(2024, 2, 1), 1)
        assert placements[2] == (date(2024, 3, 1), 1)
        assert long_periods[1].start_date == date(2024, 3, 31)

    def test_a_calendar_units_first_occurrence_is_the_date_it_states(self):
        """``resolve``'s date and the engine's first occurrence agree.

        The seam between the two halves of the model.  ``resolve`` answers a
        calendar cadence's ``starts_on`` unchanged and the engine walks from
        it; if the walk clamped differently, every rule would fire one day off
        in its first month and nothing else would say so.
        """
        biweekly, long_cadence = _baseline_schedules()
        calendars = _baseline_calendars(biweekly, long_cadence)
        checked = 0
        skipped = []
        for shape in recurrence_baseline.build_shapes():
            calendar = calendars[shape.long_cadence]
            resolved = resolve(
                recurrence_baseline.build_shape_spec(shape), calendar,
            )
            if resolved.unit is RecurrenceUnitEnum.PERIOD:
                continue
            emitted = occurrence_placements(resolved, calendar)
            if not emitted:
                # One shape, deliberate (two until plan step R7d-g retired
                # ``bounds.window.inverted``, whose end date preceded its
                # anchor).  ``horizon_bound.monthly_first`` is bounded past
                # the biweekly schedule's last payday, so the fallback anchor
                # (2027-02-01) lands past the horizon and the walk emits
                # nothing at all -- plan ledger row D10's shape on the SHORT
                # cadence, where its long-cadence twin instead emits two
                # occurrences that fail to place (:data:`_EXPECTED_UNPLACED`).
                # Counted, not skipped silently -- a threshold plus a silent
                # skip lets a shape that quietly stops emitting hide.
                skipped.append(shape.label)
                continue
            assert emitted[0].occurrence == resolved.starts_on, shape.label
            checked += 1
        assert skipped == ["horizon_bound.monthly_first"], skipped
        # 309 captured shapes less the 41 pay-period-space ones, less the one
        # above that fires nowhere.  Plan step R7c-b's four new
        # ``anchor.*`` shapes split 3 period-space to 1 calendar; plan step
        # recurrence:R5-a's removal of the 124 ``due_sweep.`` shapes (every
        # one a monthly, calendar-unit rule) took this from 391.
        assert checked == 267, f"{checked} calendar-unit shapes checked"

    def test_a_period_units_first_occurrence_is_a_payday(self):
        """A pay-period-space rule fires on paydays, not on its bound.

        The asymmetry the module docstring states: for the ``PERIOD`` unit the
        anchor is a BOUND (ruling R-R8) and the occurrence is the qualifying
        paycheck's own opening day.  Asserted over every period-space shape so
        a future change to the anchor cannot quietly make the emitted date
        something else.
        """
        biweekly, long_cadence = _baseline_schedules()
        starts = {
            False: {period.start_date for period in biweekly},
            True: {period.start_date for period in long_cadence},
        }
        calendars = _baseline_calendars(biweekly, long_cadence)
        checked = 0
        for shape in recurrence_baseline.build_shapes():
            calendar = calendars[shape.long_cadence]
            resolved = resolve(
                recurrence_baseline.build_shape_spec(shape), calendar,
            )
            if resolved.unit is not RecurrenceUnitEnum.PERIOD:
                continue
            for placement in occurrence_placements(resolved, calendar):
                assert placement.occurrence in starts[shape.long_cadence], (
                    f"{shape.label}: {placement.occurrence} is not a payday"
                )
                assert placement.period is not None
                assert placement.occurrence == placement.period.start_date
            checked += 1
        # 36 ``every_n_periods`` shapes (intervals 1-8 x every legal phase)
        # plus ``every_period`` and its long-cadence twin = 38, plus the three
        # period-space ``anchor.*`` shapes plan step R7c-b added = 41.
        # ``Monthly First`` is NOT period-space: it resolves to the MONTH unit.
        assert checked == 41, f"{checked} period-space shapes"


@pytest.mark.usefixtures("app")
class TestTheParallelRunFiringControls:
    """The gate must FAIL when the engine changes -- shown, not asserted.

    Verification standard: "every guard gets a negative control that is shown
    to fire", and "ask of every harness: can it SEE the code under test?"
    Both controls patch inside ``app.services.recurrence._placement`` -- the
    module that BINDS the two names ``_placements`` reads, since plan step
    salary:R15-a moved the placement half there; a patch on ``_occurrence``
    would leave this leaf's own binding untouched and fire nothing -- which
    is the only patch target that proves the composition resolves its parts at
    CALL time rather than having bound them at import.
    """

    def test_a_shifted_placement_turns_the_gate_red(self, monkeypatch):
        """Placing every occurrence one period later FAILS the snapshot test.

        Runs the gate's own assertion under the patch rather than comparing
        two captures: "the answers moved" proves only that the harness is
        sighted, and a perturbation confined to the 12 declared divergences
        would satisfy it while the gate stayed green.
        """
        real_search = _placement._placement_search

        def shifted(calendar, placement):
            inner = real_search(calendar, placement)

            def one_later(day):
                placed = inner(day)
                if placed is None:
                    return None
                return calendar.period_starting_on_or_after(
                    placed.end_date + timedelta(days=1),
                )

            return one_later

        # ``_placement_search`` and not ``place``: the composition resolves the
        # search ONCE per call rather than going through the public wrapper, so
        # patching ``place`` leaves the harness blind.  This control caught
        # exactly that when the eager-refusal restructure introduced it.
        monkeypatch.setattr(_placement, "_placement_search", shifted)

        with pytest.raises(AssertionError):
            TestTheParallelRun().\
                test_the_snapshot_records_exactly_what_the_engine_answers()

    def test_a_dropped_occurrence_turns_the_gate_red(self, monkeypatch):
        """Dropping the first occurrence of every rule FAILS the snapshot test."""
        real_occurrences = _placement.occurrences

        def one_fewer(resolved, calendar, *, through):
            emitted = list(real_occurrences(resolved, calendar, through=through))
            return iter(emitted[1:])

        monkeypatch.setattr(_placement, "occurrences", one_fewer)

        with pytest.raises(AssertionError):
            TestTheParallelRun().\
                test_the_snapshot_records_exactly_what_the_engine_answers()

    def test_an_extra_unplaceable_occurrence_turns_the_gate_red(
        self, monkeypatch,
    ):
        """The mutant that used to pass everything now fails the totality test.

        A neutral review built exactly this: emit one occurrence per rule that
        no paycheck can host.  It moved no PLACED index, so every
        snapshot-comparison test stayed green while the engine invented an
        occurrence.  :meth:`TestTheParallelRun.test_no_occurrence_is_unplaceable_without_being_declared`
        is what closes it, and this is the control proving it fires.
        """
        real_occurrences = _placement.occurrences

        def one_extra(resolved, calendar, *, through):
            emitted = list(real_occurrences(resolved, calendar, through=through))
            # A day past every payday but inside the covered span: emitted, and
            # placeable by neither rule.
            return iter(emitted + [calendar.horizon()])

        monkeypatch.setattr(_placement, "occurrences", one_extra)

        with pytest.raises(AssertionError):
            TestTheParallelRun().\
                test_no_occurrence_is_unplaceable_without_being_declared()


@pytest.mark.usefixtures("app")
class TestThePeriodUnit:
    """Pay-period-space occurrences: paychecks, not calendar dates.

    The developer's own schedule shape throughout (first payday 2026-03-26,
    14-day cadence, 61 periods), so period 0 covers 2026-03-26..2026-04-08 and
    period 1 opens 2026-04-09.
    """

    def test_every_paycheck_fires_on_every_payday(self):
        """Interval 1 emits one occurrence per period, each on its payday."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert len(emitted) == 61
        assert emitted[0] == date(2026, 3, 26)
        # 2026-03-26 + 14 days.
        assert emitted[1] == date(2026, 4, 9)
        assert placed_indices(value, calendar) == list(range(61))

    def test_a_mid_period_bound_fires_in_the_paycheck_it_falls_in(self):
        """A bound inside period 0 still bills period 0, on its payday.

        The loan case (plan step C9a): ``loan_recurrence_sync`` stamps the
        first contractual installment onto ``start_date``, and a loan whose
        first installment falls mid-period bills in THAT period, not the next.
        The reverse matcher got this right (``p.end_date >= bound``) and
        the forward engine has to keep it -- so the anchor is a BOUND here and
        the occurrence is the paycheck's own opening day, never the bound.
        """
        calendar = build_calendar()
        # 2026-04-01 sits inside period 0 (2026-03-26..2026-04-08).
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 4, 1),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert emitted[0] == date(2026, 3, 26)
        assert placed_indices(value, calendar)[0] == 0
        assert len(emitted) == 61

    def test_a_bound_on_a_payday_starts_that_paycheck(self):
        """A bound equal to a payday opens on that paycheck, not the previous."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 4, 9),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert emitted[0] == date(2026, 4, 9)
        # 61 periods less the one that ended before the bound.
        assert len(emitted) == 60
        assert placed_indices(value, calendar)[0] == 1

    def test_a_phased_rule_fires_on_every_nth_paycheck(self):
        """Every 3 paychecks phased at 2 fires on indices 2, 5, 8, ..., 59."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD,
            # ``_phased_period_anchor`` puts the anchor on period 2's payday.
            starts_on=date(2026, 3, 26) + timedelta(days=28),
            interval_n=3, offset_periods=2,
        )

        indices = placed_indices(value, calendar)

        # 2, 5, ... 59 -- (59 - 2) / 3 + 1 = 20 occurrences.
        assert indices == list(range(2, 61, 3))
        assert len(indices) == 20

    def test_placement_is_inert_under_the_period_unit(self):
        """Both placements answer identically for a pay-period-space rule.

        Every occurrence the unit emits is a period's own ``start_date``, and
        both rules carry such a date back to that same period.  The plan's
        section 3 claims otherwise; it reads the anchor as the emitted
        occurrence, which
        :meth:`TestThePeriodUnit.test_a_mid_period_bound_fires_in_the_paycheck_it_falls_in`
        is the decision not to do.
        """
        calendar = build_calendar()
        containing = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 4, 1),
            placement=PeriodPlacementEnum.CONTAINING_DATE,
        )
        on_or_after = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 4, 1),
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        )

        # 61 periods, every one of them, under either reading.  Asserted as
        # a VALUE and not merely as agreement: two empty lists are equal too.
        assert placed_indices(containing, calendar) == list(range(61))
        assert placed_indices(on_or_after, calendar) == list(range(61))

    def test_a_rule_starting_past_the_horizon_fires_on_projected_paydays(self):
        """The cadence names paydays the SAVED schedule has not reached.

        **The control for plan step R16-b-1**, and it asserted the opposite
        until then: ``_period_walk`` iterated the saved periods, so a rule
        anchored past the horizon answered ``[]`` and raised nothing.  Its
        premise -- "no paycheck exists past the schedule" -- is the thing that
        step measures false.  A saved schedule is where rows have been
        MATERIALISED; the owner goes on being paid, so the cadence goes on
        firing.

        The expected dates are computed from the schedule's OWN arithmetic
        (first payday plus 14n), never read off the engine: 61 saved periods
        run out at index 60 / ``2028-07-13``, the first paycheck not ending
        before ``2030-01-01`` is index 98 / ``2029-12-27``, and index 228 /
        ``2034-12-21`` is the last opening on or before ``2035-01-01``.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2030, 1, 1),
        )

        emitted = dates_through(value, calendar, date(2035, 1, 1))

        assert emitted == [
            date(2026, 3, 26) + timedelta(days=14 * index)
            for index in range(98, 229)
        ]
        assert len(emitted) == 131
        assert emitted[0] == date(2029, 12, 27)
        assert emitted[-1] == date(2034, 12, 21)

    def test_an_occurrence_past_the_horizon_places_nowhere(self):
        """Firing is a fact about the CADENCE; placing is one about the schedule.

        The half of the retired
        ``test_a_bound_past_the_horizon_fires_nowhere`` that is still true, and
        keeping the two apart is the whole distinction plan step R16-b-1 draws:
        a projected payday is not a row either placement search can return, so
        ``occurrence_placements`` still answers ``()`` while
        :meth:`test_a_rule_starting_past_the_horizon_fires_on_projected_paydays`
        gets 131 dates from the same value.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2030, 1, 1),
        )

        assert occurrence_placements(value, calendar) == ()
        # And with the window stated explicitly, every pair carries no period
        # rather than the composition dropping them.
        past_horizon = occurrence_placements(
            value, calendar, through=date(2030, 3, 1),
        )
        assert len(past_horizon) == 5
        assert all(pair.period is None for pair in past_horizon)

    def test_the_phase_keeps_stepping_across_the_saved_horizon(self):
        """A multi-period cadence does not restart or skip at the boundary.

        The projected paychecks continue the saved ``period_index`` sequence
        (:func:`app.services.pay_calendar._projection.project_period_after`), so
        ``(index - offset) % interval_n`` spans the boundary.  Index 60 is the
        last saved and IS in phase for a 3-period rule anchored at index 0, so
        a walk that restarted its count past the horizon would emit index 61 or
        62 next instead of 63.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=date(2026, 3, 26),
            interval_n=3,
        )

        emitted = dates_through(value, calendar, date(2028, 10, 1))

        assert emitted == [
            date(2026, 3, 26) + timedelta(days=14 * index)
            for index in range(0, 64, 3)
        ]
        # The saved run ends at index 60 (2028-07-13); the next in-phase
        # paycheck is 63, not 61 or 62.
        assert emitted[-2] == date(2028, 7, 13)
        assert emitted[-1] == date(2028, 8, 24)

    def test_a_count_bound_counts_paychecks_the_schedule_has_not_saved(self):
        """"Stop after 70" fires 70 times on a 61-period schedule.

        :class:`~app.services.recurrence.EndsAfterOccurrences` states that the
        count is of occurrences the CADENCE names, "including any the saved
        schedule does not reach and never places".  That was a documented
        contract this unit did not honour: the truncating walk ran out of saved
        periods at 61, so the bound was satisfied by the schedule's length
        rather than by the rule's own count.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=date(2026, 3, 26),
            end_bound=EndsAfterOccurrences(count=70),
        )

        emitted = dates_through(value, calendar, date(2040, 1, 1))

        assert len(emitted) == 70
        # The 70th occurrence is index 69, nine paychecks past the saved run.
        assert emitted[-1] == date(2028, 11, 16)

    def test_the_walk_stops_at_the_applications_last_calendar_day(self):
        """An unbounded rule asked past the calendar TERMINATES.

        :func:`_bounded` stops on the first occurrence past the caller's
        window, so a walk whose window lies beyond every date this application
        can express has to run out on its own.
        :func:`~app.services.pay_calendar.paychecks_from` bounds it
        at :data:`~app.utils.dates.CALENDAR_DATE_MAX` exactly as
        :func:`~app.services.recurrence._months.walk_months` does, so the
        sequence is finite and a consumer that forgets to stop pulling does not
        hang.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
        )

        emitted = dates_through(value, calendar, date(2200, 1, 1))

        # 2026-03-26 + 14 x 1950 = 2100-12-23; the next payday, 2101-01-06,
        # is past CALENDAR_DATE_MAX and is never named.
        assert emitted[-1] == date(2100, 12, 23)
        assert len(emitted) == 1951


@pytest.mark.usefixtures("app")
class TestTheCalendarUnits:
    """MONTH and YEAR, including the two cadences the old enum could not name."""

    def test_monthly_walks_one_month_at_a_time(self):
        """Interval 1 MONTH fires on the anchor's day every month."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
        )

        assert dates_through(value, calendar, date(2026, 8, 31)) == [
            date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15),
            date(2026, 7, 15), date(2026, 8, 15),
        ]

    def test_every_other_month_is_expressible(self):
        """Interval 2 MONTH -- one of the two cadences that had nowhere to live.

        The plan's root cause: Monthly / Quarterly / Semi-Annual / Annual are
        the same idea with the integer baked into the NAME, so "every other
        month" was inexpressible.  With the interval a value, it is one line.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            interval_n=2,
        )

        assert dates_through(value, calendar, date(2026, 8, 31)) == [
            date(2026, 4, 15), date(2026, 6, 15), date(2026, 8, 15),
        ]

    def test_quarterly_is_the_same_walk_at_interval_three(self):
        """Interval 3 MONTH reproduces the Quarterly pattern."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            interval_n=3,
        )

        assert dates_through(value, calendar, date(2027, 1, 31)) == [
            date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15),
            date(2027, 1, 15),
        ]

    def test_annual_walks_a_year_at_a_time(self):
        """Interval 1 YEAR is the twelve-month stride."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, starts_on=date(2026, 4, 15),
        )

        assert dates_through(value, calendar, date(2029, 1, 1)) == [
            date(2026, 4, 15), date(2027, 4, 15), date(2028, 4, 15),
        ]

    def test_every_two_years_is_expressible(self):
        """Interval 2 YEAR -- the second cadence the old enum could not name."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, starts_on=date(2026, 4, 15),
            interval_n=2,
        )

        assert dates_through(value, calendar, date(2031, 1, 1)) == [
            date(2026, 4, 15), date(2028, 4, 15), date(2030, 4, 15),
        ]

    def test_a_calendar_occurrence_lands_in_the_period_containing_it(self):
        """CONTAINING_DATE puts the row in the paycheck the date falls inside."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
        )

        placements = occurrence_placements(value, calendar)

        # 2026-04-15 falls inside period 1 (2026-04-09..2026-04-22).
        assert placements[0].occurrence == date(2026, 4, 15)
        assert placements[0].period.period_index == 1
        assert placements[0].period.start_date == date(2026, 4, 9)


@pytest.mark.usefixtures("app")
class TestTheWeekUnit:
    """Weeks: walked since plan step R3, authorable since plan step R8-b."""

    def test_weekly_strides_seven_days(self):
        """Interval 1 WEEK fires every seventh day from the anchor."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, starts_on=date(2026, 3, 26),
        )

        assert dates_through(value, calendar, date(2026, 4, 23)) == [
            date(2026, 3, 26), date(2026, 4, 2), date(2026, 4, 9),
            date(2026, 4, 16), date(2026, 4, 23),
        ]

    def test_biweekly_by_date_strides_fourteen_days(self):
        """Interval 2 WEEK is the biweekly-by-DATE bill the old set lacked."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, starts_on=date(2026, 3, 26),
            interval_n=2,
        )

        assert dates_through(value, calendar, date(2026, 4, 23)) == [
            date(2026, 3, 26), date(2026, 4, 9), date(2026, 4, 23),
        ]

    def test_the_walk_ends_on_the_applications_last_calendar_day(self):
        """A weekly walk asked past the calendar stops at CALENDAR_DATE_MAX.

        Bounded on day ordinals at plan step R8-b, as the month walk is on
        month ordinals, so the three walks stop at one day.  Thursday
        2100-12-02 weekly: 12-02, 12-09, 12-16, 12-23, 12-30; the next,
        2101-01-06, is past 2100-12-31 and is never named.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, starts_on=date(2100, 12, 2),
        )

        assert dates_through(value, calendar, date(2200, 1, 1)) == [
            date(2100, 12, 2), date(2100, 12, 9), date(2100, 12, 16),
            date(2100, 12, 23), date(2100, 12, 30),
        ]

    def test_a_walk_landing_exactly_on_the_last_day_names_it(self):
        """The bound is INCLUSIVE: 2100-12-24 + 7 days is 2100-12-31, named.

        The case that grades the bound's edge; a stop one day early would
        drop the calendar's own last day.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, starts_on=date(2100, 12, 24),
        )

        assert dates_through(value, calendar, date(2200, 1, 1)) == [
            date(2100, 12, 24), date(2100, 12, 31),
        ]

    @pytest.mark.parametrize(
        "interval_n",
        [
            # 70,000 days: 2217, past the calendar but a date ``date`` holds.
            10_000,
            # 3,150,000 days: past ``date.max``, which the old walk raised
            # OverflowError for at its second yield.
            450_000,
            # The interval column's ceiling: the old ``timedelta`` stride
            # raised OverflowError before yielding anything.
            2_147_483_647,
        ],
    )
    def test_a_huge_interval_fires_once_without_an_error(self, interval_n):
        """A stride whose second occurrence leaves the calendar fires ONCE.

        Every interval the form's box admits is authorable for the week unit
        since plan step R8-b, so none of these may raise: the honest answer is
        the first occurrence alone, as ``walk_months`` gives a month stride
        that leaves the calendar.  Asked through ``date.max``, so the caller's
        window cannot be what stops the 2217 date the old walk named.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, starts_on=date(2026, 3, 26),
            interval_n=interval_n,
        )

        assert dates_through(value, calendar, date.max) == [
            date(2026, 3, 26),
        ]


class TestThePerMonthCeiling:
    """The cadence's third value (plan step salary:R15-a, ruling R-SAL29).

    On the developer's own schedule shape (first payday 2026-03-26, 14-day
    cadence, 61 periods through 2028-07-13) a calendar month holds a THIRD
    payday five times: 2026-07-30, 2026-12-31, 2027-07-29, 2027-12-30 and
    2028-06-29 -- counted by an independent per-month tally of the paydays,
    not by the engine.  A payroll benefit taken on a month's first two
    paychecks skips exactly those five, and "every paycheck, at most 2 a
    month" is how the vocabulary now says so.
    """

    #: The five third paydays of the default schedule, from the tally above.
    _THIRD_PAYDAYS = (
        date(2026, 7, 30), date(2026, 12, 31), date(2027, 7, 29),
        date(2027, 12, 30), date(2028, 6, 29),
    )

    def test_at_most_two_a_month_skips_exactly_the_third_paychecks(self):
        """Ceiling 2 on every-paycheck admits 56 of 61 and drops the five thirds.

        The developer's eleven 24-per-year deductions and his Health
        Insurance Allowance, stated as a rule: what the paycheck engine's
        month-ordinal rule (``_deduction_applies_at``) skipped, this skips,
        at every phase the schedule reaches.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
            max_per_month=2,
        )
        every = dates_through(
            resolved_value(
                unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
            ),
            calendar, calendar.horizon(),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert len(every) == 61
        assert len(emitted) == 56
        assert set(every) - set(emitted) == set(self._THIRD_PAYDAYS)
        # Ascending and a strict subsequence: nothing re-ordered, nothing
        # invented.
        assert emitted == [day for day in every if day in set(emitted)]

    def test_at_most_one_a_month_is_the_months_first_paycheck(self):
        """Ceiling 1 on every-paycheck agrees with Monthly First on a biweekly owner.

        The two spellings of "the first paycheck of each month" -- a paycheck
        rule ceilinged at one, and a month rule on the 1st placed on the first
        paycheck on or after it -- land on the same paychecks wherever every
        month holds a payday, which a 14-day cadence guarantees.  Graded
        against the placed periods, since a month rule's occurrences are
        dates rather than paydays.
        """
        calendar = build_calendar()
        ceilinged = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
            max_per_month=1,
        )
        monthly_first = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 3, 1),
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        )

        assert placed_indices(ceilinged, calendar) == placed_indices(
            monthly_first, calendar,
        )
        # 29 calendar months hold a payday between 2026-03-26 and 2028-07-13.
        assert len(placed_indices(ceilinged, calendar)) == 29

    def test_a_paycheck_rule_counts_the_CALENDARS_paydays_not_its_own(self):
        """Every 2 paychecks at most 1 a month admits only in-phase MONTH-FIRST paydays.

        The developer's amendment to R-SAL29 (2026-09-13), pinned in the
        direction he ruled.  Phased on 2026-04-09, the in-phase paydays of
        2026 and their calendar ordinals, tallied independently: 04-09 (1),
        05-07 (1), 06-04 (1), 07-02 (1), 07-30 (3), 08-27 (2), 09-24 (2),
        10-22 (2), 11-19 (2), 12-17 (2).  Counting the rule's OWN occurrences
        would admit 08-27 and every later one -- each is the first the rule
        names in its month; counting the CALENDAR's paydays reads each as its
        month's second and drops it, so August through December get nothing.
        That is the cost the ruling accepts for a composition nothing
        authors, and 2026-08-27 is the payday that tells the two readings
        apart.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 4, 9),
            interval_n=2, offset_periods=1, max_per_month=1,
        )

        emitted = dates_through(value, calendar, date(2026, 12, 31))

        assert date(2026, 8, 27) not in emitted
        assert emitted == [
            date(2026, 4, 9), date(2026, 5, 7), date(2026, 6, 4),
            date(2026, 7, 2),
        ]

    def test_a_rule_started_on_a_months_second_payday_skips_that_months_third(self):
        """A benefit started 2026-07-16 is not taken on 2026-07-30, July's third payday.

        The case the adversarial review measured against the first build,
        which counted the rule's own occurrences and would have admitted
        2026-07-30 as the rule's second of July -- a ``$100.00`` allowance
        row the employer does not pay, ledger row **D59**'s defect for one
        month.  The employer's rule is the calendar's: July's first two
        paydays are the 2nd and the 16th, whatever day the election began.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 7, 16),
            max_per_month=2,
        )

        emitted = dates_through(value, calendar, date(2026, 8, 31))

        assert emitted == [
            date(2026, 7, 16), date(2026, 8, 13), date(2026, 8, 27),
        ]

    def test_the_ceiling_is_applied_BEFORE_the_count_bound(self):
        """At most 2 a month FOR 12 occurrences runs six months, not five and a third.

        A count bound counts occurrences the rule NAMES (ruling R-R6), and a
        paycheck the ceiling drops is one the rule does not name.  Composed
        the other way the twelfth occurrence would be the schedule's twelfth
        payday, 2026-08-27, with the July third already spent against the
        count; the twelfth ADMITTED payday is 2026-09-10.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
            max_per_month=2, end_bound=EndsAfterOccurrences(count=12),
        )

        emitted = dates_through(value, calendar, calendar.horizon())

        assert len(emitted) == 12
        assert date(2026, 7, 30) not in emitted
        assert emitted[-1] == date(2026, 9, 10)

    def test_the_week_unit_takes_a_ceiling_too(self):
        """The walk is total: weekly at most 2 a month keeps each month's first two.

        Reachable through the form since plan step R8-b opened the unit, and
        implemented before then rather than refused for the reason the WEEK
        walk itself was: a ceiling a unit silently ignored would be a wrong
        answer.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.WEEK, starts_on=date(2026, 3, 26),
            max_per_month=2,
        )

        assert dates_through(value, calendar, date(2026, 6, 30)) == [
            date(2026, 3, 26),
            date(2026, 4, 2), date(2026, 4, 9),
            date(2026, 5, 7), date(2026, 5, 14),
            date(2026, 6, 4), date(2026, 6, 11),
        ]

    def test_no_ceiling_admits_every_occurrence(self):
        """``None`` is the rule every existing definition means: nothing dropped.

        Stated beside the ceilinged cases rather than left to the parallel
        run, so the filter's pass-through arm is graded where its dropping arm
        is.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.PERIOD, starts_on=date(2026, 3, 26),
            max_per_month=None,
        )

        assert len(dates_through(value, calendar, calendar.horizon())) == 61


@pytest.mark.usefixtures("app")
class TestMonthEndClamping:
    """Ruling R-R3: the anchor's day must not decay as the walk advances."""

    def test_a_day_31_rule_takes_the_last_day_of_every_month(self):
        """Day 31 clamps per month and recovers the 31st where it exists."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 1, 31),
        )

        assert dates_through(value, calendar, date(2026, 7, 31)) == [
            # 2026 is not a leap year, so February clamps to the 28th.
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
            date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
            date(2026, 7, 31),
        ]

    def test_a_clamped_anchor_recovers_its_nominal_day(self):
        """``nominal_day`` is what stops a month-end rule decaying forever.

        Ruling R-R3's own measurement, made executable.  A day-31 rule whose
        first occurrence falls in April carries ``starts_on`` 2026-04-30 --
        April has no 31st -- and the nominal day in the
        ``recurrence_month_anchors`` subtype.  Without it the walk would take
        the STORED date's day, 30, and every later 31-day month would be wrong.
        """
        calendar = build_calendar()
        with_subtype = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 30),
            nominal_day=31,
        )
        without_subtype = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 30),
        )

        assert dates_through(with_subtype, calendar, date(2026, 7, 31)) == [
            date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
            date(2026, 7, 31),
        ]
        # The regression the subtype exists to prevent: 2 of these 4 are wrong.
        assert dates_through(without_subtype, calendar, date(2026, 7, 31)) == [
            date(2026, 4, 30), date(2026, 5, 30), date(2026, 6, 30),
            date(2026, 7, 30),
        ]

    def test_an_annual_leap_day_rule_clamps_only_in_common_years(self):
        """February 29 comes back in the next leap year, not once and never."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, starts_on=date(2024, 2, 29),
        )

        assert dates_through(value, calendar, date(2028, 12, 31)) == [
            date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28),
            date(2027, 2, 28), date(2028, 2, 29),
        ]

    def test_a_day_28_rule_is_never_clamped(self):
        """Every month holds a 28th, so the common case costs nothing."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 1, 28),
        )

        assert dates_through(value, calendar, date(2026, 4, 30)) == [
            date(2026, 1, 28), date(2026, 2, 28), date(2026, 3, 28),
            date(2026, 4, 28),
        ]


@pytest.mark.usefixtures("app")
class TestTheClosingBounds:
    """Ruling R-R6: the bounds are on the OCCURRENCE, not on its period."""

    def test_an_occurrence_past_the_end_date_is_never_emitted(self):
        """Defect D5 dies here: no row is dated outside its own window."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            end_bound=EndsOnDate(on=date(2026, 6, 14)),
        )

        assert dates_through(value, calendar, calendar.horizon()) == [
            date(2026, 4, 15), date(2026, 5, 15),
        ]

    def test_an_occurrence_on_the_end_date_is_kept(self):
        """The bound is inclusive -- a bill due the day the rule ends is due."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            end_bound=EndsOnDate(on=date(2026, 6, 15)),
        )

        assert dates_through(value, calendar, calendar.horizon()) == [
            date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15),
        ]

    def test_max_occurrences_emits_exactly_that_many(self):
        """The count bound, whose first author is plan step R8."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            end_bound=EndsAfterOccurrences(count=3),
        )

        assert dates_through(value, calendar, calendar.horizon()) == [
            date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15),
        ]

    def test_max_occurrences_counts_occurrences_not_placed_rows(self):
        """"Stop after N" is a property of the rule, not of the schedule.

        An occurrence the schedule cannot host still counts: the rule says the
        bill occurs, and whether the user's pay periods reach it is a different
        question.  Counting placed rows instead would silently extend a
        count-bounded rule past its own bound.

        **The unplaceable occurrence is one PAST THE HORIZON**, which since plan
        step C2-b2 is the only kind there is: this case used to be built on a
        schedule with a hole in it, and derived periods tile their covered span
        so a hole is unconstructible.  ``through`` is stated explicitly because
        the default stops at the horizon, where nothing unplaceable is emitted
        at all.
        """
        calendar = PayCalendar.from_paydays(
            paydays=[(1, date(2026, 1, 1)), (2, date(2026, 1, 15))],
            eras=(era_of(date(2026, 1, 1), 14),),
            user_id=_USER_ID,
            history_opens_on=None,
        )
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 1, 20),
            end_bound=EndsAfterOccurrences(count=2),
        )

        placements = occurrence_placements(
            value, calendar, through=date(2026, 6, 1),
        )

        assert calendar.horizon() == date(2026, 1, 28)
        assert [item.occurrence for item in placements] == [
            date(2026, 1, 20), date(2026, 2, 20),
        ]
        # The February occurrence is past the horizon and places nowhere, but
        # it still consumed one of the two -- and the bound stopped the walk
        # there rather than letting March through.
        assert placements[1].period is None
        assert placed_indices(
            value, calendar, through=date(2026, 6, 1),
        ) == [1]

    def test_a_window_ending_before_the_anchor_emits_nothing(self):
        """Nothing before the first occurrence can ever be generated."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
        )

        assert dates_through(value, calendar, date(2026, 4, 14)) == []


@pytest.mark.usefixtures("app")
class TestPlacement:
    """Carrying one occurrence DATE onto the pay period the row lives in."""

    def test_containing_date_finds_the_period_a_day_falls_inside(self):
        """The first, a middle and the last day of a period all place in it."""
        calendar = build_calendar()

        for day in (date(2026, 4, 9), date(2026, 4, 15), date(2026, 4, 22)):
            found = place(
                day, calendar, PeriodPlacementEnum.CONTAINING_DATE,
            )
            assert found is not None and found.period_index == 1, day

    def test_containing_date_answers_none_outside_the_schedule(self):
        """Before the first payday and after the horizon both answer None."""
        calendar = build_calendar()

        assert place(
            date(2026, 3, 25), calendar, PeriodPlacementEnum.CONTAINING_DATE,
        ) is None
        assert place(
            date(2028, 7, 27), calendar, PeriodPlacementEnum.CONTAINING_DATE,
        ) is None

    def test_starting_on_or_after_takes_the_next_payday(self):
        """A date ON a payday takes that one; a later date takes the next."""
        calendar = build_calendar()
        on_or_after = PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER

        exact = place(date(2026, 4, 9), calendar, on_or_after)
        one_day_later = place(date(2026, 4, 10), calendar, on_or_after)

        assert exact is not None and exact.period_index == 1
        assert one_day_later is not None and one_day_later.period_index == 2

    def test_the_composition_places_exactly_as_place_does(self):
        """The public ``place`` is not a second implementation.

        ``occurrence_placements`` resolves the search once per call instead of
        calling :func:`~app.services.recurrence.place` per occurrence, so the
        two could in principle drift.  They cannot -- both go through
        ``_placement_search`` -- and this asserts it over both placements
        rather than leaving it to a reader of the source.

        The window runs PAST the horizon so the comparison covers the unplaced
        answer as well as the placed one; stopping at the default would compare
        only the cases where both return a period.
        """
        calendar = build_calendar()
        for placement in PeriodPlacementEnum:
            value = resolved_value(
                unit=RecurrenceUnitEnum.MONTH, starts_on=date(2028, 5, 20),
                placement=placement,
            )
            items = occurrence_placements(
                value, calendar, through=date(2028, 10, 20),
            )
            assert any(item.period is None for item in items), placement
            for item in items:
                assert item.period == place(
                    item.occurrence, calendar, placement,
                ), (placement, item.occurrence)

    def test_starting_on_or_after_answers_none_past_the_last_payday(self):
        """"Not yet" rather than "never" -- the schedule will extend."""
        calendar = build_calendar()

        assert place(
            date(2028, 7, 14), calendar,
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        ) is None


@pytest.mark.usefixtures("app")
class TestProjectedPlacement:
    """:func:`~app.services.recurrence.projected_occurrence_placements`, plan step **R16-b-2**.

    The balance seam's ESTIMATED loan tier needs to know where a row WOULD
    live, saved or not, so it can date an occurrence no row answers yet
    exactly as generation would date the row (ruling **R-R69**).  These pin
    that the projecting composition agrees with the saved one wherever the
    saved one answers, keeps answering past the horizon where it does not, and
    refuses before the opening bound under ``CONTAINING_DATE`` -- the boundary
    ruling **R-R64** carries.
    """

    def test_it_agrees_with_the_saved_composition_inside_the_schedule(self):
        """Every occurrence the saved search places, this places identically."""
        calendar = build_calendar()
        for placement in PeriodPlacementEnum:
            value = resolved_value(
                unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 20),
                placement=placement,
            )
            saved = occurrence_placements(value, calendar)
            projected = projected_occurrence_placements(
                value, calendar, through=calendar.horizon(),
            )
            assert [item.occurrence for item in projected] == [
                item.occurrence for item in saved
            ], placement
            for saved_item, projected_item in zip(saved, projected):
                if saved_item.period is not None:
                    assert projected_item.period == saved_item.period, (
                        placement, saved_item.occurrence,
                    )

    def test_past_the_horizon_it_places_on_a_projected_paycheck(self):
        """Where the saved search says ``None``, this names the projected paycheck.

        Every occurrence past the horizon lands on a period with no id whose
        span holds the occurrence (``CONTAINING_DATE``) or opens on or after
        it (``PERIOD_STARTING_ON_OR_AFTER``) -- the same two rules, continued.
        """
        calendar = build_calendar()
        horizon = calendar.horizon()
        for placement in PeriodPlacementEnum:
            value = resolved_value(
                unit=RecurrenceUnitEnum.MONTH, starts_on=date(2028, 5, 20),
                placement=placement,
            )
            items = projected_occurrence_placements(
                value, calendar, through=date(2029, 5, 20),
            )
            beyond = [item for item in items if item.occurrence > horizon]
            assert len(beyond) >= 10, placement
            for item in beyond:
                assert item.period is not None, (placement, item.occurrence)
                assert item.period.period_id is None, (placement, item.occurrence)
                if placement is PeriodPlacementEnum.CONTAINING_DATE:
                    assert item.period.covers(item.occurrence), item
                else:
                    assert item.period.start_date >= item.occurrence, item
                    # ...and it is the FIRST such paycheck: the one before it
                    # opened earlier.
                    assert (
                        item.period.start_date - timedelta(days=calendar.cadence.cadence.days)
                        < item.occurrence
                    ), item

    def test_before_the_opening_bound_only_containing_date_is_unplaced(self):
        """Nothing is projected backwards, and the two placements differ there.

        ``CONTAINING_DATE`` answers ``None`` for an occurrence before the
        owner's first payday -- the R-R64 boundary: neither generated nor
        estimated.  ``PERIOD_STARTING_ON_OR_AFTER`` places it on the FIRST
        paycheck, which is exactly what generation does with it too.
        """
        calendar = build_calendar()
        opening = calendar.opening_bound()
        early = opening - timedelta(days=400)
        containing = projected_occurrence_placements(
            resolved_value(
                unit=RecurrenceUnitEnum.MONTH, starts_on=early,
                placement=PeriodPlacementEnum.CONTAINING_DATE,
            ),
            calendar, through=opening + timedelta(days=60),
        )
        pre_opening = [item for item in containing if item.occurrence < opening]
        assert len(pre_opening) >= 12
        assert all(item.period is None for item in pre_opening)
        assert any(item.period is not None for item in containing)

        on_or_after = projected_occurrence_placements(
            resolved_value(
                unit=RecurrenceUnitEnum.MONTH, starts_on=early,
                placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
            calendar, through=opening + timedelta(days=60),
        )
        first = calendar.period_containing(opening)
        assert all(
            item.period == first for item in on_or_after
            if item.occurrence < opening
        )

    def test_both_searches_are_total_over_the_placement_enum(self):
        """A member with a saved rule and no projecting one fails HERE, not silently.

        Both tables refuse an unknown member rather than defaulting; a member
        of the enum must have a rule in each, or the composition would answer
        ``None`` for an occurrence the owner's schedule can host.
        """
        calendar = build_calendar()
        for placement in PeriodPlacementEnum:
            _placement._placement_search(calendar, placement)
            _placement._span_search(calendar, placement)
        with pytest.raises(RecurrenceGenerationError):
            _placement._span_search(calendar, "not a placement")


@pytest.mark.usefixtures("app")
class TestTheGenerationWindowAndTheHorizon:
    """What the default generation window means, and what lies past it.

    **This class used to open on finding D7** -- an occurrence falling in a
    schedule HOLE, reported with ``period=None`` rather than dropped.  Plan step
    C2-b2 made that state unconstructible (derived periods tile their covered
    span), so the one remaining way to be unplaced is to lie past the horizon,
    which is what the rest of this class was already about.
    """

    def test_the_default_window_ends_at_the_schedules_horizon(self):
        """Past the last covered day no placement can succeed, so none is asked."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2028, 6, 15),
        )

        placements = occurrence_placements(value, calendar)

        # The schedule's last covered day is 2028-07-26, so July is the last
        # occurrence inside the default window and August is outside it.
        assert [item.occurrence for item in placements] == [
            date(2028, 6, 15), date(2028, 7, 15),
        ]

    def test_a_wider_window_reports_the_occurrences_beyond_the_horizon(self):
        """Explicitly asked for, they come back explicitly unplaced."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2028, 6, 15),
        )

        placements = occurrence_placements(
            value, calendar, through=date(2028, 9, 30),
        )

        assert [item.occurrence for item in placements] == [
            date(2028, 6, 15), date(2028, 7, 15), date(2028, 8, 15),
            date(2028, 9, 15),
        ]
        assert [item.period is None for item in placements] == [
            False, False, True, True,
        ]

    def test_an_empty_schedule_places_nothing(self):
        """No periods, no window, no placements -- and no exception.

        ``resolve`` already refuses an empty schedule, so a resolved value
        never pairs with one in the application; the engine answers rather
        than raising because "generate nothing" is what
        ``generate_for_template`` does today for an empty period list.
        """
        calendar = _empty_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
        )

        assert occurrence_placements(value, calendar) == ()


@pytest.mark.usefixtures("app")
class TestRefusals:
    """What the engine refuses rather than answering wrongly."""

    @pytest.mark.parametrize("shift", [
        BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT,
    ])
    def test_a_business_day_shift_is_refused_until_step_r8(self, shift):
        """Generating unshifted dates would silently ignore the user's choice.

        Refused EAGERLY -- the call itself raises, not the first ``next()`` --
        so a caller that builds the iterator and hands it on sees the failure
        where it was caused.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            shift=shift,
        )

        with pytest.raises(RecurrenceGenerationError, match="business-day"):
            occurrences(value, calendar, through=date(2026, 12, 31))

    @pytest.mark.parametrize("interval", [0, -1])
    def test_a_non_positive_interval_is_refused(self, interval):
        """A zero stride emits the same date forever; refuse, do not spin."""
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            interval_n=interval,
        )

        with pytest.raises(RecurrenceGenerationError, match="must be positive"):
            occurrences(value, calendar, through=date(2026, 12, 31))

    def test_a_unit_with_no_walk_is_refused(self):
        """A value outside the enum raises instead of firing nowhere.

        Unreachable through the type, and that is the point: an unrecognised
        unit answering "no occurrences" would read as a rule that never fires,
        which is a silently missing bill.
        """
        calendar = build_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
        )
        not_a_unit = ResolvedRecurrence(
            **{**vars(value), "unit": object()},
        )

        with pytest.raises(RecurrenceGenerationError, match="no occurrence walk"):
            occurrences(not_a_unit, calendar, through=date(2026, 12, 31))

    def test_a_unit_that_names_a_day_ALWAYS_has_a_stride(self):
        """The sibling refusal is gone, because its state is unconstructible.

        **This case used to manufacture the defect and assert the guard; plan
        step R7b-1 deleted both** (adversarial review, 2026-08-12).  There were
        two refusals -- "this unit names no day" and "this unit names a day but
        has no stride" -- and the second was reachable only when the
        day-of-month unit set and ``_months``' month-span table disagreed about
        which units are calendar units.  Two hand-written statements of one
        class, and the only way to exercise the guard between them was to
        monkeypatch them apart, which is what this case did.

        They are ONE statement: since plan step plan#2302 (ruling plan#2748)
        :func:`~app.services.recurrence.has_day_of_month_coordinate` reads the
        unit's ``ref.recurrence_units.has_day_of_month``, which the DATABASE
        generates from the same ``months_per_unit`` ``months_per_step`` reads.
        A unit that names a day of the month therefore has a stride by
        construction, and the guard that used to say so was a fence over an
        impossible state.

        **Each home it has had, this case followed.**  R7b-1 left
        ``_resolution._DAY_OF_MONTH_UNITS = MONTH_SPANNING_UNITS``, one code
        tuple wearing two names, whose identity this case asserted; R8-a
        deleted the alias; plan#2302 deleted the tuple and its month-span
        table for the reference row, so there is no second name left to
        compare and the property is asserted over the two READERS instead:
        for every unit, the flag says a day exactly where the stride answers.
        A separate list that agrees passes here; ``test_recurrence_unit_months``
        changes the row to grade each reader.

        The refusal itself is not untested -- ``months_per_step`` still refuses
        a unit with no month span, asserted on the function directly by
        :meth:`test_the_month_stride_refuses_a_unit_it_cannot_measure`.  What
        can no longer happen is REACHING it from either of this package's two
        walks.
        """
        naming_a_day = tuple(
            unit for unit in RecurrenceUnitEnum
            if has_day_of_month_coordinate(unit)
        )

        # Written out by hand: the units measured in months.  Without it a
        # flag answering False for every unit would make the loop below
        # vacuous on its first half.
        assert naming_a_day == (RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR)
        for unit in RecurrenceUnitEnum:
            if has_day_of_month_coordinate(unit):
                assert _months.months_per_step(unit, 1) >= 1, unit
            else:
                with pytest.raises(_months.MonthStepError):
                    _months.months_per_step(unit, 1)

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.PERIOD, RecurrenceUnitEnum.WEEK],
    )
    def test_the_month_stride_refuses_a_unit_it_cannot_measure(self, unit):
        """``months_per_step`` is partial over the enum and says so.

        Unreachable from either walk -- see the case above for why -- and
        asserted on the function itself because a THIRD caller that does not
        prove membership first would meet it.  A ``ShekelError`` since plan
        step R7b-1, so such a caller fails inside the hierarchy every other
        refusal in this package raises into rather than beside it.
        """
        with pytest.raises(_months.MonthStepError, match="no reading in months"):
            _months.months_per_step(unit, 1)

        assert isinstance(
            _months.MonthStepError("probe"), ShekelError,
        ), "a refusal outside ShekelError escapes every handler written for it"

    def test_the_two_walks_take_the_same_stride(self):
        """The anchor's month step and the occurrence walk's are one call.

        They were two spellings of one fact until plan step R7b-1 -- a
        ``month_step`` column on the pattern table for the anchor, and
        ``interval_n * MONTHS_PER_YEAR`` computed inline here for the walk --
        in the two functions whose own docstrings say they are "the SAME walk
        seeded differently".  They agreed; nothing made them.

        Asserted where it can actually fail: a YEAR cadence's first TWO
        occurrences must be twelve months apart, which is the case a second
        spelling gets wrong.  The anchor is the walk's first element by
        construction, so comparing the walk's own step to the anchor's is what
        a re-divergence would break.
        """
        calendar = build_calendar()
        yearly = resolved_value(
            unit=RecurrenceUnitEnum.YEAR, starts_on=date(2026, 4, 15),
            interval_n=2,
        )

        dates = list(occurrences(yearly, calendar, through=date(2032, 12, 31)))

        assert dates[:3] == [
            date(2026, 4, 15), date(2028, 4, 15), date(2030, 4, 15),
        ]
        assert _months.months_per_step(RecurrenceUnitEnum.YEAR, 2) == 24

    def test_a_placement_with_no_rule_is_refused(self):
        """Same reasoning on the placement axis."""
        calendar = build_calendar()

        with pytest.raises(RecurrenceGenerationError, match="has no rule"):
            place(date(2026, 4, 15), calendar, object())

    def test_a_nominal_day_that_disagrees_with_its_date_cannot_be_BUILT(self):
        """The walk cannot be handed the pair at all, so it need not check.

        ``nominal_day`` exists only because the first occurrence's MONTH was
        too short to hold the day the user meant (ruling R-R3), so
        ``min(nominal_day, days in that month)`` must be the date's own day.
        Walking from a disagreeing pair would fire on a day the date does not
        name, which nothing downstream would notice.

        **Until plan step R7c-b a walk-time guard caught this**, because the
        column CHECK admitted the pair -- it graded ``nominal_day BETWEEN 29
        AND 31`` and nothing else.  That step completed the constraint and put
        the same rule in ``__post_init__``, so the refusal is now at
        construction and the guard is deleted.  ``RecurrenceSpec``'s half of
        it is
        ``test_recurrence_resolution.TestTheNominalDayPair
        .test_a_contradictory_pair_cannot_be_CONSTRUCTED``; this is
        ``ResolvedRecurrence``'s, which is the value the walk actually reads.
        """
        # April HAS a 15th, so a day-31 rule could never START on the 15th.
        with pytest.raises(RecurrenceResolutionError, match="nominal_day 31"):
            resolved_value(
                unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
                nominal_day=31,
            )

    # ``{"nominal_day": 31}`` was a third case here until plan step R7c-b.
    # It is gone because it can no longer be CONSTRUCTED, which is a stronger
    # refusal than the one this test grades -- see the test just above.
    @pytest.mark.parametrize("broken", [
        {"shift": BusinessDayShiftEnum.PRIOR},
        {"interval_n": 0},
    ])
    def test_an_empty_schedule_does_not_excuse_a_broken_value(self, broken):
        """The composition refuses what ``occurrences`` refuses, always.

        ``occurrence_placements`` short-circuits to ``()`` for a schedule with
        no periods, and a neutral review measured that the short-circuit ran
        BEFORE the refusals -- so a business-day shift or a zero interval was
        silently accepted there and raised everywhere else.  The guards now run
        first.
        """
        empty = _empty_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            **broken,
        )

        with pytest.raises(RecurrenceGenerationError):
            occurrence_placements(value, empty)

    def test_an_empty_schedule_does_not_excuse_a_broken_placement(self):
        """Same, for the placement axis, which resolves before the walk."""
        empty = _empty_calendar()
        value = resolved_value(
            unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 15),
            placement=object(),
        )

        with pytest.raises(RecurrenceGenerationError, match="has no rule"):
            occurrence_placements(value, empty)


@pytest.mark.usefixtures("app")
class TestTheScheduleSearches:
    """The calendar surface the occurrence engine reads, and its tiling."""

    def test_the_horizon_is_the_last_covered_day(self):
        """The symmetric partner of ``opening_bound``."""
        calendar = build_calendar()

        assert calendar.opening_bound() == date(2026, 3, 26)
        # 61 periods x 14 days from 2026-03-26 ends 2028-07-26.
        assert calendar.horizon() == date(2028, 7, 26)

    def test_an_empty_schedule_has_no_horizon(self):
        """``None`` rather than a fabricated date."""
        assert _empty_calendar().horizon() is None

    def test_the_covered_span_has_no_hole_in_it(self):
        """A gapped, overlapping or reversed schedule is UNCONSTRUCTIBLE.

        **This replaces three tests plan step C2-b2 made unwritable.**  They
        built a calendar from stored spans -- one with a hole, one overlapping,
        one running backwards -- and asserted that the first was accepted and
        the other two raised ``RecurrenceScheduleError``.  A calendar now holds
        PAYDAYS and derives every end from the next one, so none of the three
        states can be expressed as an input at all, and the refusals were
        deleted with the class that raised them.  What is left to assert is the
        property they were policing, which is now a consequence of the
        construction: consecutive paydays tile ``[opening, horizon]``.

        The derivation's own refusals -- a duplicate payday, a bad cadence --
        are graded by ``tests/test_services/test_pay_calendar_value.py``, which
        also holds the tiling proof over every shape a calendar can take.  This
        is the recurrence engine's own stake in it: the searches BISECT, so a
        day inside the span that answered ``None`` would seat a bill in a
        plausible wrong paycheck.
        """
        # Paydays 21 days apart at a stored cadence of 14: the middle period
        # runs to the day before the NEXT payday, not to payday + 13, which is
        # exactly the shape that used to leave a week uncovered.
        calendar = PayCalendar.from_paydays(
            paydays=[
                (1, date(2026, 1, 1)),
                (2, date(2026, 1, 22)),
                (3, date(2026, 2, 12)),
            ],
            eras=(era_of(date(2026, 1, 1), 14),),
            user_id=_USER_ID,
            history_opens_on=None,
        )

        day = calendar.opening_bound()
        while day <= calendar.horizon():
            assert calendar.period_containing(day) is not None, (
                f"{day} is inside the covered span and seats in no period"
            )
            day += timedelta(days=1)
        assert calendar.horizon() == date(2026, 2, 25)


class TestTheFirstOccurrenceIsTheWalksFirstYield:
    """``resolve``'s ``starts_on`` against the WALK, which is its only oracle.

    ``ResolvedRecurrence.starts_on`` is the first date the rule fires on, and
    the property this class checks is that it is the walk's first yield
    wherever the walk yields at all.

    **An adversarial review of plan step R7c-a asked for this by name**, and
    the reason outlived the function it was asked about.  That step seeded the
    column with a ``first_occurrence`` helper and then asserted the column
    against that same helper -- a comparison no bug inside it could fail.
    ``docs/plans/verification.md`` standard 2: *never a producer as its own
    oracle*.  Step R7c-b deleted the helper by making ``resolve`` normalise the
    date itself, which removes the second implementation but NOT the need for
    an independent one to grade it.  :func:`occurrences` is that independent
    implementation -- a forward walk rather than a direct search -- so it
    remains the oracle, and the two hand-computed cases below are the ground
    truth neither of them can fabricate.
    """

    def test_it_equals_the_first_date_the_walk_emits(self):
        """Over every frozen baseline shape, the two answers agree.

        ONE case rather than 430 parametrised ones, which is this file's own
        idiom for a whole-baseline sweep (``test_the_baseline_has_not_moved``
        one module over): the per-case fixture cost dominated the assertion by
        an order of magnitude, and a sweep that reports every disagreement at
        once is more useful than 430 ids of which a handful are red.
        """
        calendars = _cached_baseline_calendars()
        disagreed: list[str] = []
        walked_at_all = 0
        for shape in recurrence_baseline.build_shapes():
            calendar = calendars[shape.long_cadence]
            resolved = resolve(
                recurrence_baseline.build_shape_spec(shape), calendar,
            )
            seeded = resolved.starts_on
            walked = list(
                occurrences(resolved, calendar, through=calendar.horizon()),
            )
            if not walked:
                # The walk reaches nothing inside the horizon -- a bound past
                # it, or a closing bound admitting none.  ``first_occurrence``
                # still answers, because the column it seeds cannot hold "no
                # answer"; what it must not do is CONTRADICT the walk, and
                # there is nothing here to contradict.
                assert seeded is not None, shape.label
                continue
            walked_at_all += 1
            if seeded != walked[0]:
                disagreed.append(
                    f"{shape.label}: seeded {seeded}, walk first {walked[0]}",
                )

        assert not disagreed, (
            f"{len(disagreed)} of {walked_at_all} shapes seed a starts_on "
            f"that is not the first date the rule fires on, so a stored first "
            f"occurrence and a generated row would disagree from the cutover "
            f"onward: {disagreed[:5]}"
        )
        assert walked_at_all > 300, (
            f"only {walked_at_all} baseline shapes reached the walk at all, "
            f"so this sweep is grading far less than the baseline holds"
        )

    def test_a_mid_period_bound_names_the_paycheck_that_pays_it(self):
        """A PERIOD rule bound mid-paycheck starts on THAT paycheck's payday.

        Hand-computed, and the case the function exists for: paydays every 14
        days from 2026-01-02, and a bound of 2026-01-20 falling inside the
        2026-01-16 paycheck (which covers 01-16..01-29).  The answer is
        **2026-01-16** -- the payday, not the bound -- because that is where
        the cash leaves, which is what lets a loan whose first installment
        falls mid-period bill in that period (plan step C9a).

        It is also the one date ``resolve`` still MOVES.  Until plan step
        R7c-b the stated 2026-01-20 was kept as an opening bound and a
        separate ``first_occurrence`` function converted it; the field now
        holds the converted answer, so a stored first occurrence and a
        generated row cannot disagree.
        """
        calendar = build_calendar(
            first_payday=date(2026, 1, 2), cadence_days=14, count=10,
        )
        resolved = resolve(
            _resolution.RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.PERIOD,
                starts_on=date(2026, 1, 20),
            ),
            calendar,
        )

        assert resolved.starts_on == date(2026, 1, 16)
        assert dates_through(
            resolved, calendar, date(2026, 3, 1),
        )[0] == date(2026, 1, 16)

    def test_a_calendar_rule_starts_on_its_own_firing_date(self):
        """A MONTH rule's first occurrence is the cadence's date, not a payday.

        The other half of the pair, hand-computed on the same schedule: a
        rule stating 2026-01-15, which is NOT a payday (they fall every 14
        days from 2026-01-02, so 01-02, 01-16, 01-30...).  The answer is
        **2026-01-15** unchanged, because the normalisation above applies to
        the paycheck unit and to nothing else -- a calendar cadence fires on
        the date it names.
        """
        calendar = build_calendar(
            first_payday=date(2026, 1, 2), cadence_days=14, count=10,
        )
        resolved = resolve(
            _resolution.RecurrenceSpec(
                user_id=_USER_ID,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 15),
            ),
            calendar,
        )

        assert resolved.starts_on == date(2026, 1, 15)
        assert dates_through(
            resolved, calendar, date(2026, 3, 1),
        )[0] == date(2026, 1, 15)


class TestTheMonthWalkReachesTheCalendarsLastMonth:
    """``_months.walk_months`` yields through the calendar's LAST month, then stops.

    Its stop is the month of the last grid day on or before
    ``CALENDAR_DATE_MAX`` (2100-12-31), read from the monthly-grid set beside
    the clamp since plan step recurrence:R25 (ruling R-R122).  A mutation that
    stopped the walk one month early survived the whole recurrence suite until
    these were written: no other case reaches December 2100.
    """

    def test_a_monthly_walk_ends_on_the_last_day_the_calendar_holds(self):
        """The 31st from January 2100: twelve dates, the last 2100-12-31."""
        walked = list(_months.walk_months(
            _months.month_ordinal(date(2100, 1, 1)), DayOfMonth(31).date_in, 1,
        ))
        assert len(walked) == 12
        assert walked[1] == date(2100, 2, 28)
        assert walked[-1] == date(2100, 12, 31)

    def test_a_mid_month_walk_from_the_last_month_yields_that_month_alone(self):
        """The 15th, monthly, from December 2100: exactly one date.

        The stop is the LAST grid day ON OR BEFORE 2100-12-31.  A stop read
        as the first ON OR AFTER would let this walk name 2101-01-15, past
        the calendar; the 31st cannot tell the two apart (both are Dec 31),
        so this is the case that grades the stop's direction.
        """
        assert list(_months.walk_months(
            _months.month_ordinal(date(2100, 12, 1)), DayOfMonth(15).date_in, 1,
        )) == [date(2100, 12, 15)]

    def test_a_stride_landing_past_the_calendar_fires_once(self):
        """Yearly from March 2100: March 2101 is past the calendar, so one date."""
        assert list(_months.walk_months(
            _months.month_ordinal(date(2100, 3, 1)), DayOfMonth(15).date_in, 12,
        )) == [date(2100, 3, 15)]
