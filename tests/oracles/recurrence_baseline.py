"""The recurrence engine's frozen behaviour baseline (plan step R1).

The gate every later step of ``docs/plans/implementation_plan_recurrence_redesign.md``
is measured against.  R3 built a new occurrence engine and R4a cut the
``match_periods`` adapter over to it; this module is what proved the cutover
moved nothing it did not mean to move, and what holds R4b onward to the same
standard.

**The snapshot was re-frozen at plan step R4a** (+122 / -4 lines over exactly
the 12 shapes ruling R-R6 and plan defect D3 predicted), so it now records the
FORWARD engine.  What it recorded before is in that commit's diff; a snapshot
is a record of what shipped, not of what was replaced.

**What it captures.**  For each rule SHAPE, the exact answer the engine gives
to the only two questions it is asked in production:

* :func:`app.services.recurrence.rule_occurrences` -- which pay periods does
  this rule fire in?  (Plan step R4b-2 replaced ``match_periods`` with it; the
  capture keeps taking the PERIOD half, and dropping an unplaced occurrence
  here is what the deleted adapter did for it, so the blob is unmoved.)
* :func:`app.services.recurrence.compute_due_date` -- what date does the
  generated row carry?  (In ``recurrence_engine`` until plan step R16-b-2
  moved it down beside the occurrence walk, ruling R-R69; asked of each
  placement's own occurrence since plan step R5-a.)

Both are their module's public surface (their docstrings say so) and both are
pure functions of a rule's columns plus a period list, so the baseline needs no
database rows -- only an app context, for ``ref_cache`` to resolve each cadence
axis to its integer id.

**Why the keys are ``period_index`` and a shape LABEL, never a row id.**  A
snapshot keyed on ``pay_periods.id`` or a ``ref`` table's id would churn every
time the test template was rebuilt, and a baseline that changes for reasons
unrelated to the code under test is a baseline nobody trusts.  Both keys here
are stable facts: ``period_index`` is the schedule's own ordinal, and a shape's
label is written beside the cadence it names.

**Why real model instances rather than stubs.**  ``build_shape_rule`` returns an
unsaved :class:`~app.models.recurrence_rule.RecurrenceRule` and
:func:`build_schedule` unsaved :class:`~app.models.pay_period.PayPeriod` rows,
for the reason finding B-17 records and
``tests/test_services/test_recurrence_engine/_helpers.py::build_rule`` documents at
length: a hand-rolled double that mirrors the model drifts from it silently.
When ``start_date`` was added to the rule, the stub of the day kept satisfying
every assertion.  An unsaved instance costs no session and no flush, and a
column the model does not have raises ``TypeError`` here instead of passing.

It does NOT carry SQLAlchemy's ``default=`` values (those apply at INSERT), so
``interval_n`` is always passed explicitly -- the same trap that module
documents.  ``offset_periods`` was passed for the same reason until plan step
R7b-4, which made the phase a DERIVATION of the opening bound rather than a
value a caller states; no code reads that column now, so the shapes state a
``start_date`` instead and the frozen blob is unmoved (see
:func:`_add_period_space_shapes`).

**The clock.**  Every date in this module is a literal.  Nothing reads
``date.today()`` or ``display_today()``, so the blob is identical under
``TZ=Pacific/Kiritimati`` and under the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).  The schedule deliberately opens on a leap
year so February 29 clamping is inside the covered span rather than argued
about.

**Coverage, stated rather than implied** (the plan's "no silent caps" rule).
The shape set is exhaustive on every axis the matcher branches on, except that
the three calendar patterns that take BOTH a month and a day
(``QUARTERLY`` / ``SEMI_ANNUAL`` / ``ANNUAL``) sweep all twelve months against
the six days that matter -- ``1`` and ``15`` for the ordinary cases and
``28``-``31`` for every month-length clamp -- rather than all 372 combinations.
Days 2-14 and 16-27 differ from 15 in no branch of the walk: it clamps with
``min(day, monthrange(...))``, which is the identity for every day below 28.  ``MONTHLY`` sweeps all 31 days anyway,
because there the day IS the whole rule.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceSpec,
    build_transient_rule,
    end_bound_from_columns,
)
# Both imported as MODULES, not as names, and the recurrence producer is
# imported from its DEFINITION site (``_reading``) rather than through the
# package's re-export.  ``from ... import rule_occurrences`` would bind the
# function at import time; reaching it through the package alias would make the
# firing control in ``tests/test_services/test_recurrence_baseline.py`` prove
# only that this module reads the attribute it reads.  Patching the definition
# site is the testing standards' preferred target and is what makes the control
# say something -- a harness that cannot SEE the code under test is the failure
# mode the balance arc's verification standard names (plan Section 7.2).
# ``_reading`` is package-private to ``app/`` (the W9910 checker runs on
# ``pylint app/``); a test naming it is naming the thing it is testing.
#
# **``compute_due_date`` follows the same rule since plan step R10-a**, and it
# had to start doing so at that step: ``recurrence_engine`` was a flat module
# until then, so the package attribute WAS the definition and this module
# already obeyed the paragraph above by accident.  The split made it a
# re-export, which silently turned the due-date firing control into the exact
# thing this comment says to avoid -- a proof that the oracle can read its own
# alias.  Caught by an adversarial review of that step.
from app.services.recurrence import _reading, _row_date
from app.services.recurrence._months import clamped_day, month_ordinal

from tests._test_helpers import (
    eras_of,
)

#: The baseline schedule's first payday.  A literal, and a LEAP year, so
#: February 29 clamping is covered rather than assumed.
SCHEDULE_START: date = date(2024, 1, 1)

#: Days between paydays.  14 is the cadence the developer runs and the
#: ``generate_pay_periods`` default; the matcher's behaviour at longer cadences
#: is defect D3 in the plan and is captured by its own shapes below.
SCHEDULE_CADENCE_DAYS: int = 14

#: Periods in the baseline schedule.  79 biweekly periods span just over three
#: calendar years (2024-01-01 to 2027-01-04), which puts three year boundaries
#: and one February 29 inside the window.
SCHEDULE_PERIOD_COUNT: int = 79

#: A second, deliberately LONG cadence.  The reverse matcher inspected only
#: the months of a period's two endpoints, so a period spanning more than two
#: months silently dropped the interior ones (plan defect D3).  ``cadence_days``
#: is user-selectable 1..365, so this was reachable configuration, not a
#: hypothetical -- and freezing the WRONG answer here was deliberate: plan step
#: R4a changed these lines and no others, which is what made the fix visible in
#: a diff instead of asserted in a message.
LONG_CADENCE_DAYS: int = 90

#: Periods in the long-cadence schedule -- twelve quarters, three years, so the
#: two schedules cover the same span.
LONG_CADENCE_PERIOD_COUNT: int = 12

#: Days swept for the patterns that take a month AND a day.  1 and 15 are the
#: ordinary cases; 28-31 are every distinct month-length clamp (February in a
#: common year, February in a leap year, the 30-day months, the 31-day months).
#: See the module docstring for why 2-14 and 16-27 add no branch.
_CLAMP_DAYS: tuple[int, ...] = (1, 15, 28, 29, 30, 31)


@dataclass(frozen=True)
class ShapeCadence:
    """The cadence one shape states, as ``budget.recurrence_rules`` holds it.

    **A shape stated a closed-set PATTERN until plan step R7c-c**, and the
    seven constants below carry the names those members had, so every call site
    reads as it always did and every LABEL in the blob is untouched.  What
    changed underneath is the vocabulary: ``pattern_id`` is dropped, and a rule
    states its cadence in ``interval_n`` / ``unit_id`` / ``placement_id``.

    That is the same substitution R7c-b made one leaf earlier for the anchor --
    a shape stated ``(day_of_month, month_of_year, start_date)`` and now states
    ``starts_on`` -- and it is made the same way, for the same reason: re-keying
    the labels would make the diff unreadable at exactly the step whose whole
    evidence is that the diff is EMPTY.

    **From plan step R9 this class IS the whole test-side cadence
    vocabulary.**  The suite passed a ``RecurrencePatternEnum`` member or its
    display string until then, resolved through a
    ``CADENCE_BY_LEGACY_NAME`` table here; R9 deletes that enum with
    ``ref.recurrence_patterns``, and the table went with it rather than
    outliving both as a third spelling.  A fixture states the two axes now, so
    a mistyped cadence is a ``NameError`` at import rather than a lookup that
    fails on the first call.

    Attributes:
        interval_n: The interval this cadence fixes, or ``None`` when the SHAPE
            states its own -- true of exactly the paycheck-space cadence the
            ``every_n_periods`` sweep varies.
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.
    """

    interval_n: int | None
    unit: RecurrenceUnitEnum
    placement: PeriodPlacementEnum

    @property
    def label(self) -> str:
        """Return a stable test id for this cadence, derived from its axes.

        The parametrize id for every sweep over :data:`BASELINE_CADENCES`.
        DERIVED rather than written down, so it cannot come to name a cadence
        other than the one it is attached to -- which a second table keyed by
        the same seven names could, and which is the drift that retired
        ``CADENCE_BY_LEGACY_NAME``.  ``every_n_periods`` is the one cadence
        fixing no interval, and it reads ``n`` where the others read a number.

        Returns:
            The id, e.g. ``every-3-month`` or
            ``every-1-month-period_starting_on_or_after``.
        """
        every = "n" if self.interval_n is None else str(self.interval_n)
        placed = (
            "" if self.placement is PeriodPlacementEnum.CONTAINING_DATE
            else f"-{self.placement.value}"
        )
        return f"every-{every}-{self.unit.value}{placed}"


#: The seven cadences the closed pattern set could name, under its own names.
#:
#: Verbatim what ``recurrence._frequency.PATTERN_DERIVATIONS`` held before plan
#: step R7c-c deleted it, which is why the blob does not move: each shape
#: resolves to the reading its pattern always decoded to.
EVERY_PERIOD = ShapeCadence(
    1, RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
)
EVERY_N_PERIODS = ShapeCadence(
    None, RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
)
MONTHLY = ShapeCadence(
    1, RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE,
)
MONTHLY_FIRST = ShapeCadence(
    1, RecurrenceUnitEnum.MONTH,
    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
)
QUARTERLY = ShapeCadence(
    3, RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE,
)
SEMI_ANNUAL = ShapeCadence(
    6, RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE,
)
ANNUAL = ShapeCadence(
    1, RecurrenceUnitEnum.YEAR, PeriodPlacementEnum.CONTAINING_DATE,
)

#: The seven above as ONE sweep space, in the order the closed set held them.
#:
#: **What every "each cadence" sweep in the suite parametrizes over**, so a
#: cadence added here is swept by all of them and one added to a list written
#: out in a test file is swept by that file alone.  Its ids come from
#: :attr:`ShapeCadence.label`.
#:
#: **It replaced ``CADENCE_BY_LEGACY_NAME`` at plan step R9**, a dict keyed by
#: the display names ``ref.recurrence_patterns`` carried, which the fixture
#: helpers took as shorthand.  That table was the last place a FIXTURE
#: resolved a closed-set name through, and R9 drops the table and the enum
#: those names came from -- so the sweep space is the cadences themselves now.
#: The names survive in exactly one place, legitimately:
#: ``test_closed_pattern_set_dies_migration.py`` grades
#: ``d9f5c1a48b73._pattern_for``, whose DOWNGRADE must keep speaking them.
#:
#: These seven are NOT the authorable space, which is wider and which
#: ``test_recurrence_frequency.TestTheOfferSetIsTotal`` sweeps: they are the
#: cadences the frozen blob's hand-checked answers were computed for.
BASELINE_CADENCES: tuple[ShapeCadence, ...] = (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)


@dataclass(frozen=True)
class RuleShape:
    """One rule configuration to freeze the engine's answer for.

    A shape is the rule's column values plus a stable label.  It is NOT a
    :class:`~app.models.recurrence_rule.RecurrenceRule`; the rule is built from
    it by :func:`build_shape_rule` at capture time, so the shape can be
    declared without an app context while the rule cannot.

    **The shape's own vocabulary moved at plan step R7c-b**, and every
    captured line survived it.  A shape stated ``(day_of_month, month_of_year,
    start_date)`` until then, three fields the resolver RECONSTRUCTED a first
    occurrence from on every read; ruling R-R16 made that date the authored
    value, so each shape states it directly.  The translation was computed on
    the pre-cutover tree by driving ``resolve`` over all 434 shapes, and the
    blob is what proves it landed: a shape that now means something else moves
    its own lines, and none did.

    Attributes:
        label: Stable, sortable identity for this shape in the blob.  Encodes
            every field that differs from the defaults, so a reader diffing the
            snapshot can reconstruct the rule without consulting this file.
            **Unchanged across the vocabulary move**, deliberately: a label
            keyed on ``moy11.dom01`` still names the shape it always named, and
            re-keying them would have made the diff unreadable at exactly the
            step whose whole evidence is that the diff is empty.
        cadence: What the rule's cadence columns hold, as one of the seven
            :class:`ShapeCadence` constants above.  Still keyed under the closed
            set's own names because every case here was hand-checked under one.
        interval_n: ``EVERY_N_PERIODS`` interval; 1 elsewhere.
        starts_on: The rule's FIRST OCCURRENCE (ruling R-R16) -- for a
            paycheck-space cadence a date anywhere in the paycheck it bills in,
            which the write door normalises onto that paycheck's payday.
        nominal_day: The day the rule MEANS when *starts_on*'s own month was
            too short to hold it, and ``None`` otherwise.
        end_date: The rule's closing validity bound.
        long_cadence: Capture this shape against the 90-day schedule instead
            of the biweekly one (the D3 shapes).
    """

    label: str
    cadence: ShapeCadence
    starts_on: date = SCHEDULE_START
    interval_n: int = 1
    nominal_day: int | None = None
    end_date: date | None = None
    long_cadence: bool = False


@dataclass
class _ShapeAccumulator:
    """Mutable shape list under construction, so each builder appends once."""

    shapes: list[RuleShape] = field(default_factory=list)

    def add(self, shape: RuleShape) -> None:
        """Append one shape."""
        self.shapes.append(shape)


def build_schedule(
    start: date, cadence_days: int, count: int,
) -> list[PayPeriod]:
    """Return ``count`` contiguous unflushed pay periods, each with an id.

    Mirrors ``pay_period_service.generate_pay_periods`` exactly: each period
    ends ``cadence_days - 1`` after it starts, and the next begins the
    following day, so the schedule is CONTIGUOUS and every calendar date falls
    in exactly one period.  That totality is the property the redesign's
    forward placement depends on, so the baseline must be built the same way
    the app builds one.

    **Each row carries an explicit ``id`` since pay-calendar plan step
    C2-f2b**, and the reason is fidelity rather than plumbing.  These rows are
    never flushed, so SQLAlchemy left ``id`` as ``None`` and
    :func:`build_shape_calendar` handed the resulting ``(None, payday)`` pairs
    to :meth:`PayCalendar.from_paydays` -- giving the oracle a calendar every
    one of whose periods is an UNSAVED CANDIDATE.  The application cannot
    produce one: ``calendar_for`` reads saved rows, so every period the
    recurrence engine actually places against has an id.  The baseline was
    therefore grading the engine on a schedule shape production never holds,
    which C2-f2b surfaced by making
    :meth:`~app.services.pay_calendar.PayCalendar.period_containing` filter to
    materialised periods -- the same filter its four sibling searches already
    apply, because this answer becomes ``transactions.pay_period_id`` and that
    column is ``NOT NULL``.

    The ids are positional and start at 1 so they cannot be confused with the
    0-based ``period_index`` beside them (ledger row **P13**'s confusion).  The
    430-shape snapshot records period INDICES and does not move.

    Args:
        start: The first payday.
        cadence_days: Days between paydays.
        count: How many periods to build.

    Returns:
        Unflushed :class:`~app.models.pay_period.PayPeriod` rows ordered by
        ``period_index``, which starts at 0 as the real generator's does, each
        carrying a 1-based ``id``.
    """
    periods = []
    for index in range(count):
        period_start = start + timedelta(days=cadence_days * index)
        periods.append(PayPeriod(id=index + 1, start_date=period_start))
    return periods


def period_last_covered_day(
    periods: "list[PayPeriod]", cadence_days: int, index: int,
) -> date:
    """Return the inclusive last day the period at *index* covers.

    **The span rule written out for THIS module's unsaved schedules**, which
    plan step ``pay_calendar:C4-c`` is why it has to be written out at all: a
    period's last covered day was a stored column until that step dropped it,
    and the rows :func:`build_schedule` returns are UNSAVED and carry no owner,
    so ``_test_helpers.last_covered_day`` cannot answer for them -- it resolves
    the span through ``calendar_for``, and no calendar holds a row that was
    never flushed.

    The rule is the application's, spelled independently rather than imported:
    the day before the NEXT payday, or -- for the last period, which has no
    successor -- the payday plus one cadence minus a day.  Writing it here is
    what keeps this module an ORACLE: a baseline that asked
    ``derive_periods`` for the answer would agree with the engine by
    construction.

    Pure, which is this module's own property: no session, no clock.

    Args:
        periods: A contiguous schedule from :func:`build_schedule`, non-empty.
        cadence_days: The cadence it was built at.
        index: The 0-based position whose span to answer, which is also that
            period's ordinal -- the schedules here are built in payday order.

    Returns:
        The inclusive last day of that period.
    """
    if index + 1 < len(periods):
        return periods[index + 1].start_date - timedelta(days=1)
    return periods[index].start_date + timedelta(days=cadence_days - 1)


def schedule_horizon(periods: "list[PayPeriod]", cadence_days: int) -> date:
    """Return the last day *periods* covers.

    :func:`period_last_covered_day` at the last index, kept as its own name
    because "how far does this schedule reach" is the question most callers
    ask and spelling the index at each of them is where an off-by-one lives.

    Args:
        periods: A contiguous schedule from :func:`build_schedule`, non-empty.
        cadence_days: The cadence it was built at.

    Returns:
        The inclusive last day of the last period.
    """
    return period_last_covered_day(periods, cadence_days, len(periods) - 1)


#: The owner every spec and calendar this module builds names.  The baseline's
#: pay periods are unsaved and carry no ``user_id``, and
#: ``app.services.recurrence.resolve`` REFUSES a spec paired with another
#: user's schedule -- so one constant is what keeps the two halves agreeing.
SHAPE_USER_ID: int = 1


def build_shape_rule(
    shape: RuleShape, calendar: PayCalendar,
) -> RecurrenceRule:
    """Return a real, unsaved rule for *shape*.

    Requires an app context: ``unit_id``, ``placement_id`` and ``shift_id``
    resolve through ``ref_cache``, the same lookup production uses, so a
    shape can never name a cadence axis the database does not carry.  It
    said ``pattern_id`` until plan step R9, four steps after the column it
    named was dropped.

    ``interval_n`` is passed explicitly because SQLAlchemy applies
    ``default=`` at INSERT and this row is never inserted -- an unflushed rule
    would otherwise carry ``None`` where production has 1, and the resolver
    would be exercised on a shape production never sees.  ``offset_periods``
    was passed for the same reason until plan step R7b-4 and no longer is:
    nothing reads that column, so a transient rule carrying ``None`` there
    differs from a production row in a field neither of them is read for.

    **The rule carries an unsaved OWNER from plan step R-F6**, built inside
    ``build_transient_rule`` from the spec's own ``user_id``: a recurrence rule
    belongs to exactly one definition, and ``RecurrenceRule.user_id`` reads
    through to it.  Nothing is added to a session, so the blob is still
    captured without touching the database -- and it stayed byte-identical
    across the step, which is what says the ownership move changed no
    occurrence and no due date.

    Args:
        shape: The configuration to build.
        calendar: The schedule the shape resolves against.

    Returns:
        An unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    return build_transient_rule(build_shape_spec(shape), calendar)


#: Parses one captured blob line back into its label, period index and due
#: date.  Written beside :func:`capture_shape`, which produces the format, so
#: the two cannot drift; :func:`parse_baseline_rows` is what plan step R3's
#: parallel run reads the committed snapshot with.
_BLOB_LINE = re.compile(
    r"^(?P<label>\S+) idx=(?P<index>\d+) "
    r"period=\S+ due=(?P<due>\d{4}-\d{2}-\d{2})$"
)

#: Matches the line a shape that fires nowhere emits.
_BLOB_NONE_LINE = re.compile(r"^(?P<label>\S+) \(none\)$")


def build_shape_spec(shape: "RuleShape") -> RecurrenceSpec:
    """Return the authored spec for *shape*.

    The two-axis sibling of :func:`build_shape_rule`: that one builds what the
    OLD engine reads (a :class:`~app.models.recurrence_rule.RecurrenceRule`),
    this one builds what the new engine's producer reads (a
    :class:`~app.services.recurrence.RecurrenceSpec`, which
    ``app.services.recurrence.resolve`` turns into the two-axis meaning).
    Both are built from the SAME shape, which is the whole point: a parallel
    run that fed the two engines differently would prove nothing.

    **A ``start_period_id`` was deliberately left unset here** until plan
    step R7b-4 deleted the field: the baseline captures with no lower window
    bound (``capture_shape``) and ``resolve`` reaches the schedule's opening
    through ``PayCalendar.opening_bound()``, so a start period would have added
    a bound the captured answers were never measured under.  ``start_date``
    IS set, for the shapes that state one, and it is the same reasoning read
    forward: it is now the rule's own bound rather than a window laid over it.

    **The shape's CADENCE is read off its own axes** (plan step R7b): the
    spec speaks the two-axis vocabulary now, and a shape is a set of stored
    COLUMN values.  Reading them here is what keeps the two builders on this
    page two views of one shape -- ``build_shape_rule`` writes the columns and
    this reads them back through the same seam the read door uses, so a shape
    cannot mean one thing to the engine and another to the resolver.

    Requires an app context: the cadence axes resolve through ``ref_cache``.

    Args:
        shape: The configuration to build.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec`.
    """
    return RecurrenceSpec(
        user_id=SHAPE_USER_ID,
        unit=shape.cadence.unit,
        starts_on=shape.starts_on,
        interval_n=(
            shape.interval_n if shape.cadence.interval_n is None
            else shape.cadence.interval_n
        ),
        placement=shape.cadence.placement,
        nominal_day=shape.nominal_day,
        # Read through the same column-to-bound seam the READ DOOR uses
        # (plan step R7b-3), for the reason the cadence is read off the
        # axes: a shape is a set of stored COLUMN values, so building the
        # spec's bound any other way would let a shape mean one thing to the
        # engine and another to the resolver.
        end_bound=end_bound_from_columns(shape.end_date, None),
    )


def build_shape_calendar(
    periods: list[PayPeriod], cadence_days: int,
) -> PayCalendar:
    """Return the resolver's view of one of this module's schedules.

    **Built from the PAYDAYS since plan step C2-b2**, which is what the
    application does: a period's ordinal and its last covered day are derived
    from the payday set and the owner's cadence, so handing over the stored
    columns would hand over a second answer.  Both schedules here are
    contiguous and generated at one cadence, so the derived ends reproduce the
    stored ones exactly -- which is why the frozen baseline blob does not move.

    Args:
        periods: A schedule from :func:`build_schedule`.
        cadence_days: The cadence that schedule was generated at.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for
        :data:`SHAPE_USER_ID`.
    """
    paydays = [(period.id, period.start_date) for period in periods]
    return PayCalendar.from_paydays(
        paydays=paydays,
        eras=eras_of(paydays, cadence_days),
        user_id=SHAPE_USER_ID,
        history_opens_on=None,
    )


def parse_baseline_rows(blob: str) -> dict[str, list[tuple[int, date]]]:
    """Return ``{shape label: [(period_index, due date), ...]}`` from a blob.

    The inverse of :func:`capture_shape`, for plan step R3's parallel run: the
    committed snapshot is what the OLD engine answers, and this is how the new
    engine's answer is compared against it.

    **The ``due=`` half is parsed even though R3 does not generate due dates**,
    and it earns its place: for a ``MONTHLY`` shape the due date IS the
    occurrence date, so it is what lets a test check that a row the forward
    engine stops generating was dated outside its own rule's window -- against
    the snapshot rather than against a literal somebody typed.

    A shape that fires nowhere parses to an EMPTY list rather than being
    absent, so "matched nothing" and "was never captured" stay distinguishable
    on this side of the format too.

    Args:
        blob: A baseline snapshot, from the committed file or from
            :func:`capture_baseline`.

    Returns:
        One entry per shape, its matched rows in captured order.

    Raises:
        ValueError: When a non-comment line matches neither form.  A blob this
            cannot read is a format change, and reading it partially would
            silently shrink the comparison.
    """
    parsed: dict[str, list[tuple[int, date]]] = {}
    for line in blob.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _BLOB_LINE.match(line)
        if match is not None:
            parsed.setdefault(match["label"], []).append(
                (int(match["index"]), date.fromisoformat(match["due"])),
            )
            continue
        none_match = _BLOB_NONE_LINE.match(line)
        if none_match is None:
            raise ValueError(
                f"unparseable baseline line {line!r}: it is neither an "
                f"'<label> idx=NNN period=... due=...' occurrence nor a "
                f"'<label> (none)'.  The blob format changed and "
                f"parse_baseline_rows was not updated."
            )
        parsed.setdefault(none_match["label"], [])
    return parsed


def parse_baseline(blob: str) -> dict[str, list[int]]:
    """Return ``{shape label: [period_index, ...]}`` from a captured blob.

    The period-index projection of :func:`parse_baseline_rows`, derived from
    it rather than parsed a second time so the two readings of one format
    cannot disagree.

    Args:
        blob: A baseline snapshot.

    Returns:
        One entry per shape, its matched period indices in captured order.
    """
    return {
        label: [index for index, _due in rows]
        for label, rows in parse_baseline_rows(blob).items()
    }



def _cycle_start(
    cycle_month: int, day: int, months_per_cycle: int,
) -> tuple[date, int | None]:
    """Return the first occurrence a ``(cycle_month, day)`` cadence names.

    **The translation plan step R7c-b's vocabulary move needed**, and it is a
    statement of the SHAPE rather than a copy of the resolver: a cycle that
    skips months fires in a RESIDUE CLASS of them, so "quarterly, cycle month
    November" first fires in whichever month of that class the schedule's
    opening month reaches first -- February 2024, not November.  Sweeping the
    twelve cycle months is what covers every residue class of every cycle
    length, and keeping the sweep keyed on the cycle month is what keeps each
    shape's label naming the shape it always named.

    The schedule opens in JANUARY of a year, so its month ordinal is divisible
    by 12 and therefore by 3, 6 and 12 -- which is what makes the aligned
    ordinal simply ``opening + (cycle_month - 1) % months_per_cycle``, with no
    walk.  That is checked rather than assumed: :func:`build_shapes`' own
    assertion below would fail on a schedule opening in any other month.

    Args:
        cycle_month: The cycle's start month, 1-12.
        day: The day of the month the rule means, before clamping.
        months_per_cycle: 3 for quarterly, 6 for semi-annual, 12 for annual.

    Returns:
        ``(starts_on, nominal_day)`` -- the clamped first occurrence, and the
        day it MEANT when that month was too short to hold it.
    """
    ordinal = (
        month_ordinal(SCHEDULE_START)
        + (cycle_month - 1) % months_per_cycle
    )
    starts_on = clamped_day(ordinal, day)
    return starts_on, (day if day > starts_on.day else None)


def _add_period_space_shapes(acc: _ShapeAccumulator) -> None:
    """Every shape of the two pay-period-space patterns.

    ``EVERY_PERIOD`` has no parameters.  ``EVERY_N_PERIODS`` is swept over
    every interval 1..8 crossed with every legal phase for that interval (a
    phase is only meaningful modulo ``interval_n``, so ``0..n-1`` is the
    complete space, not a sample).  ``MONTHLY_FIRST`` takes no parameters
    either.

    **The phase is REACHED through the opening bound, since plan step
    R7b-4**, and the captured blob does not move.  It was authored directly
    (``offset_periods=offset``) while the phase was a column a caller could
    state; it is derived from the paycheck the rule STARTS in now, so a shape
    phased at ``k`` is one whose ``start_date`` is period index ``k``'s own
    payday.  The two reach the identical residue class, which is why every
    line of the frozen snapshot survives the change unedited:

      * BEFORE -- the bound was the schedule opening (index 0) and the anchor
        advanced to the first period at or after it satisfying the stored
        phase, i.e. index ``k``;
      * AFTER -- the bound IS index ``k``'s payday, its own ordinal derives
        ``k % interval == k``, and the anchor is that bound.

    Both then walk ``k, k+n, k+2n, ...``.  Sweeping the bound rather than the
    phase is also the stronger sweep, because it exercises the DERIVATION over
    the whole space instead of assuming it.
    """
    acc.add(RuleShape("every_period", EVERY_PERIOD))
    acc.add(RuleShape("monthly_first", MONTHLY_FIRST))
    for interval in range(1, 9):
        for offset in range(interval):
            acc.add(RuleShape(
                f"every_n_periods.n{interval:02d}.off{offset:02d}",
                EVERY_N_PERIODS,
                interval_n=interval,
                starts_on=SCHEDULE_START + timedelta(
                    days=SCHEDULE_CADENCE_DAYS * offset,
                ),
            ))


def _add_monthly_shapes(acc: _ShapeAccumulator) -> None:
    """Every scheduling day for the monthly pattern, 1..31.

    Swept in full rather than at the clamp boundaries: for ``MONTHLY`` the day
    is the entire rule, and day 31 is also the app's only idiom for "last day
    of the month" (it clamps), so no day here is redundant with another.
    """
    for day in range(1, 32):
        # January holds every day 1-31, so a monthly shape's first occurrence
        # is that day of the schedule's opening month and no clamp arises.
        acc.add(RuleShape(
            f"monthly.dom{day:02d}",
            MONTHLY,
            starts_on=date(
                SCHEDULE_START.year, SCHEDULE_START.month, day,
            ),
        ))


def _add_calendar_cycle_shapes(acc: _ShapeAccumulator) -> None:
    """Every cycle-start month crossed with the clamp-relevant days.

    Covers ``QUARTERLY``, ``SEMI_ANNUAL`` and ``ANNUAL``.  The month axis is
    complete (1..12); the day axis is :data:`_CLAMP_DAYS`, which the module
    docstring justifies branch by branch.
    """
    cycles = (
        ("quarterly", QUARTERLY, 3),
        ("semi_annual", SEMI_ANNUAL, 6),
        ("annual", ANNUAL, 12),
    )
    for name, pattern, months_per_cycle in cycles:
        for month in range(1, 13):
            for day in _CLAMP_DAYS:
                starts_on, nominal_day = _cycle_start(
                    month, day, months_per_cycle,
                )
                acc.add(RuleShape(
                    f"{name}.moy{month:02d}.dom{day:02d}",
                    pattern,
                    starts_on=starts_on,
                    nominal_day=nominal_day,
                ))


def _add_bound_shapes(acc: _ShapeAccumulator) -> None:
    """The validity-window shapes.

Both bounds are UNBYPASSABLE by a caller's ``effective_from`` -- the property
    that lets a loan payment refuse to generate before origination.  Each is
    placed mid-period and on a period boundary, in both directions, because the
    reverse matcher's comparisons were ASYMMETRIC: ``start_date`` was tested
    against a period's END and ``end_date`` against its START, which is what
    let it generate a row dated outside the window (defect D5).  Plan step R4a
    moved both onto the occurrence and four of these eight shapes dropped a row
    each -- exactly the rows ruling R-R6 predicted.  Seven since plan step
    R7d-g; the comment on the tuple says which left and why.
    """
    # **Each ``starts_on`` is the date the OLD derivation answered for that
    # shape's opening bound**, computed on the pre-cutover tree over all 434
    # shapes and pinned here rather than recomputed: a day-15 monthly rule
    # bounded at 2024-06-05 first occurred 2024-06-15, one bounded at
    # 2024-06-16 first occurred 2024-07-15, and an unbounded one first occurred
    # 2024-01-15.  The blob is what proves the pinning: a wrong date here moves
    # that shape's own lines and no others.
    #
    # **``window.inverted`` LEFT this set at plan step R7d-g** (re-frozen: one
    # ``(none)`` line fewer, nothing else moved).  A stop before the first
    # occurrence is a pair the table refuses since that step
    # (``ck_recurrence_rules_valid_window``) and the write door refuses before
    # it -- ``build_transient_rule`` IS that door, and a shape it refuses to
    # build is not an engine behaviour production can reach.  The walk's
    # answer for an empty window is still graded, from the direction that
    # exists: ``recurrence.Empty`` (a loan cleared before its payment's first
    # installment) in ``test_recurrence_closing`` and
    # ``test_loan_bound_at_generation``.
    bounds = (
        ("start.midperiod", date(2024, 6, 15), None),
        ("start.on_period_start", date(2024, 6, 15), None),
        ("start.on_period_end", date(2024, 7, 15), None),
        ("end.midperiod", date(2024, 1, 15), date(2025, 6, 5)),
        ("end.on_period_start", date(2024, 1, 15), date(2025, 6, 2)),
        ("end.on_period_end", date(2024, 1, 15), date(2025, 6, 15)),
        ("window.both", date(2024, 6, 15), date(2025, 6, 5)),
    )
    for name, starts_on, end in bounds:
        acc.add(RuleShape(
            f"bounds.{name}",
            MONTHLY,
            starts_on=starts_on,
            end_date=end,
        ))


def _add_anchor_normalisation_shapes(acc: _ShapeAccumulator) -> None:
    """The shapes where the FIRST OCCURRENCE is not the opening bound.

    **An oracle hole this builder closes, measured 2026-08-15 while preparing
    plan step R7c-b**: over all 430 shapes the set held until then,
    ``anchor_date`` equalled ``first_occurrence`` in every single one -- so no
    line of the blob varied along the axis that step moves.  Two families were
    missing and each hides a different way of getting the first occurrence
    wrong:

    * **A pay-period-space rule bounded MID-PERIOD.**  Ruling R-R8 anchors such
      a rule on the bound itself, and the first occurrence is the payday of the
      paycheck that bound falls IN -- earlier than the bound (plan ledger row
      **D6**).  Every period-space shape above states a bound that is already a
      payday (``SCHEDULE_START + 14k``), where the two coincide, so the
      asymmetry was invisible.  ``n03`` carries it into the PHASE as well: the
      cycle is read off the paycheck the bound lands in, not off the bound.
    * **A calendar rule whose ANCHOR MONTH is too short to hold its day.**  The
      schedule opens 1 January, which holds a 31st, so ``monthly.dom31`` never
      clamped and no ``MONTHLY`` shape reached ``nominal_day`` at all -- the 22
      that do are all quarterly / semi-annual / annual.  A day-31 monthly bill
      first falling in February is the ordinary "last day of the month" case.

    Added BEFORE the cutover and captured against the engine as it stood, so
    the lines they contribute are frozen the same way every other line is.
    """
    # The mid-period date is stated VERBATIM rather than pre-normalised, which
    # is the whole point of these two: the write door answers the payday of the
    # paycheck 2024-06-05 falls in (2024-06-03), so a shape declaring the raw
    # date and one declaring the payday must capture identically.  That is the
    # axis no other shape varies along.
    for name, starts_on in (
        ("period.midperiod_bound", date(2024, 6, 5)),
        ("period.on_period_start", date(2024, 6, 3)),
    ):
        acc.add(RuleShape(
            f"anchor.{name}", EVERY_PERIOD,
            starts_on=starts_on,
        ))
    acc.add(RuleShape(
        "anchor.period.n03.midperiod_bound",
        EVERY_N_PERIODS,
        interval_n=3,
        starts_on=date(2024, 6, 5),
    ))
    acc.add(RuleShape(
        "anchor.monthly.dom31.short_anchor_month",
        MONTHLY,
        starts_on=date(2024, 2, 29),
        nominal_day=31,
    ))


def _add_horizon_bound_shapes(acc: _ShapeAccumulator) -> None:
    """``Monthly First`` rules whose opening bound is past the last payday.

    **Plan ledger row D10, added at plan step R4b-2.**  That pattern's anchor is
    derived by SCANNING the schedule's own months for the first one whose own
    first paycheck clears the bound; past the last payday there is no month left
    to inspect, so ``_resolution._first_of_month_anchor`` falls back to "the 1st
    of the month after the bound".  The fallback's answer is HORIZON-DEPENDENT:
    extending the schedule can move it a month earlier.  No shape reached that
    branch, so the re-freeze at plan step R4a gated an unmeasured code path --
    a hole in a baseline is not a smaller baseline, it is a green gate over
    behaviour nobody looked at.

    Both shapes capture ``(none)``, and that is all a blob keyed on PERIODS
    can say: a rule bounded past the last payday generates NOTHING.  It is
    deliberately NOT the gate on the anchor itself -- a neutral review measured
    two one-month mutants of the fallback leaving these lines unmoved, which is
    D10's own failure direction.  What gates the anchor is
    ``tests/test_services/test_recurrence_occurrence.py``'s
    ``_EXPECTED_UNPLACED``, which these shapes FEED and which names every
    unplaced occurrence date exactly.  The two are not redundant, because they
    reach ``(none)`` differently:

    * ``biweekly`` (bound 2027-01-05, last payday 2026-12-28, horizon
      2027-01-10) -- the fallback anchors 2027-02-01, past the horizon, so the
      walk emits no occurrence at all;
    * ``long_cadence`` (bound 2026-10-01, last payday 2026-09-17, horizon
      2026-12-15) -- the fallback anchors 2026-11-01, INSIDE the horizon, so
      two occurrences are emitted and both fail to place, because
      ``PERIOD_STARTING_ON_OR_AFTER`` needs a paycheck opening on or after them
      and the schedule has none.  It is the only shape whose lines are
      dropped ENTIRELY by ``placed_periods``; two others
      (``monthly_first``, ``long_cadence.monthly_first``) lose one occurrence
      each the same way.

    ``tests/test_services/test_recurrence_resolution.py`` carries the
    measurement of the horizon dependence itself, which a blob keyed on
    periods cannot express.
    """
    acc.add(RuleShape(
        "horizon_bound.monthly_first",
        MONTHLY_FIRST,
        starts_on=date(2027, 2, 1),
    ))
    acc.add(RuleShape(
        "horizon_bound.long_cadence.monthly_first",
        MONTHLY_FIRST,
        starts_on=date(2026, 11, 1),
        long_cadence=True,
    ))


def _add_long_cadence_shapes(acc: _ShapeAccumulator) -> None:
    """The shapes that expose defect D3, captured against a 90-day schedule.

    The reverse matcher read only a period's ``start_date`` and ``end_date``
    months, so a period spanning four months could match at most two of them.
    These shapes froze that behaviour -- WRONG but current -- so plan step
    R4a's forward-placement cutover changed exactly these lines and no others.
    They now freeze the answer that replaced it: every month a monthly bill is
    owed in, whatever the pay cadence.

    **Every pattern whose matcher reads endpoints is covered here, and that is
    a rule rather than a selection.**  The first cut of this builder covered
    ``MONTHLY`` and ``QUARTERLY`` only, and the gap was not free: plan step
    R3's parallel run reproduced 414 of 423 shapes and could say nothing at
    all about ``MONTHLY_FIRST``, ``SEMI_ANNUAL`` or ``ANNUAL`` at a long
    cadence, because no shape asked.  ``MONTHLY_FIRST`` turned out to be the
    one that matters -- its occurrences DEFER onto the next paycheck rather
    than landing in a containing one, so several months collapse onto one
    paycheck (see the plan's ledger).  A hole in a baseline is not a smaller
    baseline; it is a green gate over an unmeasured behaviour.

    ``EVERY_PERIOD`` is captured too, as the control: pay-period-space
    generation reads no months at all, so it must NOT move at any cadence.

    **The added shapes paid for themselves immediately.**  ``ANNUAL``'s period
    selection agreed at 90 days -- the reverse matcher deduped by YEAR, which
    is total at this cadence -- but its captured ``due=`` column did not:

    ```text
    long_cadence.annual.moy01.dom01 idx=004 ... due=2025-03-01
    ```

    A January annual rule dated 1 March.  ``compute_due_date`` picks its base
    month by asking which of a period's two ENDPOINT months contains the
    ``day_of_month`` target and never consults ``month_of_year``, so at a
    cadence where the firing month is neither endpoint it dates the row in the
    wrong month entirely.  Latent at 14 days, where the firing month always is
    an endpoint.  Frozen here as it behaved until plan step R5-a dated a row
    from its OCCURRENCE (plan ledger row D18, closed there), which moved this
    line onto the January occurrence the rule names.
    """
    for day in (1, 15, 31):
        acc.add(RuleShape(
            f"long_cadence.monthly.dom{day:02d}",
            MONTHLY,
            starts_on=date(SCHEDULE_START.year, SCHEDULE_START.month, day),
            long_cadence=True,
        ))
    cycles = (
        ("quarterly", QUARTERLY),
        ("semi_annual", SEMI_ANNUAL),
        ("annual", ANNUAL),
    )
    for name, pattern in cycles:
        for day in (1, 15):
            # Cycle month 1 IS the schedule's opening month, so every cycle
            # length aligns there and the first occurrence is that day of it.
            acc.add(RuleShape(
                f"long_cadence.{name}.moy01.dom{day:02d}",
                pattern,
                starts_on=date(
                    SCHEDULE_START.year, SCHEDULE_START.month, day,
                ),
                long_cadence=True,
            ))
    acc.add(RuleShape(
        "long_cadence.monthly_first",
        MONTHLY_FIRST,
        long_cadence=True,
    ))
    acc.add(RuleShape(
        "long_cadence.every_period",
        EVERY_PERIOD,
        long_cadence=True,
    ))


def build_shapes() -> list[RuleShape]:
    """Return every shape the baseline covers, in stable label order.

    Sorted by label so the blob's line order is a property of the shape set
    rather than of the order the builders happen to run in -- adding a builder
    must not reshuffle unrelated lines in the diff.

    Returns:
        The shape list, deduplicated by construction (each builder owns a
        disjoint label prefix).
    """
    acc = _ShapeAccumulator()
    _add_period_space_shapes(acc)
    _add_monthly_shapes(acc)
    _add_calendar_cycle_shapes(acc)
    _add_bound_shapes(acc)
    _add_anchor_normalisation_shapes(acc)
    _add_horizon_bound_shapes(acc)
    _add_long_cadence_shapes(acc)
    return sorted(acc.shapes, key=lambda shape: shape.label)


def capture_shape(
    shape: RuleShape, periods: list[PayPeriod], cadence_days: int,
) -> list[str]:
    """Return the blob lines for one shape.

    Calls the two public entry points exactly as ``generate_for_template``
    does: :func:`~app.services.recurrence.rule_occurrences` over the whole
    schedule with no lower window bound, then ``compute_due_date`` per placed
    occurrence.

    A shape that matches nothing emits one ``(none)`` line rather than
    disappearing.  A shape that vanished silently would be indistinguishable in
    the diff from a shape that was never captured, which is how a regression
    hides.

    **A period repeated in ``matched`` emits a repeated period**, and since
    plan step R4a that is reachable: at a cadence of 30 days or more several
    occurrences of one monthly bill land in one paycheck.  Until plan step
    R5-a the repeated lines were byte-identical, because ``compute_due_date``
    dated a row from its PERIOD and could not tell them apart -- plan ledger
    row D18 made visible.  It dates each from its own occurrence now, so the
    repeats differ in their ``due=`` column wherever the cadence names a day
    of the month.  The occurrence DATES themselves are pinned independently,
    by ``tests/test_services/test_recurrence_occurrence.py``'s day-by-day
    sweep.

    Args:
        shape: The configuration to capture.
        periods: The schedule to capture it against.
        cadence_days: The cadence that schedule was generated at, which the
            calendar reads for the last period's end (plan step C2-b2).

    Returns:
        One line per matched period, or a single ``(none)`` line.
    """
    calendar = build_shape_calendar(periods, cadence_days)
    rule = build_shape_rule(shape, calendar)
    # The whole schedule, as plan step R4b-1 made explicit: the baseline has
    # always captured against the FULL period list, so building the calendar
    # from the same list is the same measurement.  There is no lower window
    # bound -- the anchor's own floor is ``PayCalendar.opening_bound()``, so
    # no walk can emit an occurrence placed before it.
    #
    # **Unplaced occurrences are dropped by the placed filter since plan step
    # R4b-2** (``placed_occurrences`` since R5-a, which keeps the occurrence
    # the row is dated from), where the retired ``match_periods`` adapter dropped them, which
    # is why the blob did not move across that step.  They are NOT rare even on
    # the contiguous schedules this module builds: three shapes have one --
    # ``PERIOD_STARTING_ON_OR_AFTER`` cannot defer an occurrence dated after
    # the last payday onto anything.  A blob keyed on PERIODS cannot record
    # them, so ``tests/test_services/test_recurrence_occurrence.py``'s
    # ``_EXPECTED_UNPLACED`` names each one exactly and is what gates them.
    matched = _reading.placed_occurrences(
        _reading.rule_occurrences(rule, calendar),
    )
    if not matched:
        return [f"{shape.label} (none)"]
    lines = []
    for placement in matched:
        period = placement.period
        due = _row_date.compute_due_date(rule, placement.occurrence, period)
        lines.append(
            f"{shape.label} idx={period.period_index:03d} "
            f"period={period.start_date.isoformat()}..{period.end_date.isoformat()} "
            f"due={due.isoformat()}"
        )
    return lines


def capture_baseline() -> str:
    """Return the whole baseline blob.

    Requires an app context (``build_shape_rule`` resolves the cadence axes
    through ``ref_cache``).  Deterministic: same shapes, same schedules, same literal
    dates, no clock read anywhere.

    Returns:
        The blob, newline-terminated, with a header stating the schedules and
        the shape count so a reader can tell a truncated file from a shrunk
        one.
    """
    biweekly = build_schedule(
        SCHEDULE_START, SCHEDULE_CADENCE_DAYS, SCHEDULE_PERIOD_COUNT,
    )
    long_cadence = build_schedule(
        SCHEDULE_START, LONG_CADENCE_DAYS, LONG_CADENCE_PERIOD_COUNT,
    )
    shapes = build_shapes()
    lines = [
        "# recurrence engine behaviour baseline (plan step R1)",
        "# REGENERATE: SHEKEL_UPDATE_RECURRENCE_BASELINE=1 ./scripts/test.sh "
        "tests/test_services/test_recurrence_baseline.py",
        f"# biweekly schedule: {SCHEDULE_START.isoformat()} "
        f"cadence={SCHEDULE_CADENCE_DAYS} periods={SCHEDULE_PERIOD_COUNT} "
        f"({biweekly[0].start_date.isoformat()}"
        f"..{schedule_horizon(biweekly, SCHEDULE_CADENCE_DAYS).isoformat()})",
        f"# long schedule:     {SCHEDULE_START.isoformat()} "
        f"cadence={LONG_CADENCE_DAYS} periods={LONG_CADENCE_PERIOD_COUNT} "
        f"({long_cadence[0].start_date.isoformat()}"
        f"..{schedule_horizon(long_cadence, LONG_CADENCE_DAYS).isoformat()})",
        f"# shapes: {len(shapes)}",
    ]
    for shape in shapes:
        lines.extend(
            capture_shape(shape, long_cadence, LONG_CADENCE_DAYS)
            if shape.long_cadence
            else capture_shape(shape, biweekly, SCHEDULE_CADENCE_DAYS)
        )
    return "\n".join(lines) + "\n"
