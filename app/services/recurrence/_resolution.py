"""
Shekel Budget App -- Resolving an authored recurrence into its two-axis view

One pure function, :func:`resolve`, turns what a caller AUTHORS
(:class:`RecurrenceSpec` -- a cadence, a first occurrence and the bounds) into
what the recurrence MEANS (:class:`ResolvedRecurrence` -- an interval, a unit, a
first occurrence, a placement, a shift, the bounds, the 0-or-1 nominal day and
the 0-or-1 weekday reading).

**Since plan step R7c-b the FIRST OCCURRENCE is authored rather than derived**
(ruling **R-R16**, plan ledger row **D28**).  A caller states ``starts_on``; the
row stores it; this module reads it.  What used to live here was a set of
DERIVATIONS that reconstructed that date on every read from
``(start_date, day_of_month, month_of_year)`` plus the owner's schedule -- an
effective-start maximum, a month-ordinal residue walk, and a scan of the
schedule's own months for ``Monthly First``.  All three are deleted, and with
them three ledger rows:

* **D10** -- ``_first_of_month_anchor``'s fallback was HORIZON-DEPENDENT, so
  extending the schedule could move a ``Monthly First`` rule's first occurrence
  a month earlier.  A stored date does not move.
* **D21** / **D24** -- the ``PERIOD`` unit's cycle phase was read from the
  ``offset_periods`` COLUMN on one path and derived from the rule's start period
  on another.  It is now read off ``starts_on`` on every path, by
  :func:`_derive_offset_periods`, and plan step R7c-c DROPPED the column it
  used to be written to.  The value survives on
  :class:`ResolvedRecurrence` -- ``_occurrence``'s period walk is its one
  reader -- as a derivation rather than a stored fact.

**``starts_on`` has ONE meaning for every unit**, which is the whole content of
the ruling: the first date a calendar cadence fires on, and the payday of the
first paycheck a pay-period cadence bills in.  The asymmetry plan ledger row
**D6** records -- ``anchor_date`` meaning an occurrence for three units and an
opening BOUND for the fourth -- is gone with the field's old name.  A caller
may author any date for a pay-period cadence and :func:`_first_occurrence`
NORMALISES it onto the paycheck that hosts it, so the value this module returns
is a real occurrence by construction rather than by a second function agreeing
with the walk.

**The closed set owns nothing here from plan step R7c-c.**  ``interval_n`` was
the last value this module took through the storage encoding -- the write door
stored ``1`` for every pattern whose interval was baked into its NAME, so a
Quarterly rule's ``3`` was recovered from the pattern and never from the column
-- and migration ``d9f5c1a48b73`` re-points the column and deletes the encode /
decode pair.  What this module reads is what a caller authored.

**Every cadence this resolves NAMES A REAL RHYTHM, and a consumer may rely on
that.**  ``Once`` used to be the exception -- it meant "does not recur", so no
honest cadence existed for it, and it resolved to the same inert value as
"every paycheck" while four separate guards elsewhere did the real suppressing.
Plan step R2e-3 deleted it: "does not recur" is NO RULE naming the definition,
on either template kind, which never reaches this module at all.

No Flask, no ORM, no clock, and no database read: what a unit's reference row
states comes from the reference cache, loaded once per process (ruling
plan#2748).  Its two inputs are the
authored spec and the owner's
:class:`~app.services.pay_calendar.PayCalendar`, so every derivation below can
be exercised at exact dates.

The two derivations that are left
---------------------------------

1. **The first occurrence** (:func:`_first_occurrence`).  For a calendar
   cadence it IS ``spec.starts_on``: the caller named a date the cadence fires
   on, and nothing about the owner's schedule can move it.  For the ``PERIOD``
   unit the occurrences are PAYDAYS, so an authored date is normalised onto the
   payday of the first paycheck that has not ENDED before it -- which is
   :func:`app.services.recurrence._occurrence._period_walk`'s own first yield,
   restated as a direct search rather than as a second opinion about it.

2. **The cycle phase** (:func:`_derive_offset_periods`).  An
   ``Every N Periods`` rule fires on the paycheck its first occurrence falls in
   and every Nth after, so the phase is that paycheck's own ordinal modulo the
   interval.  Nobody authors it and no template has ever rendered an input for
   it; the column is written from this answer and read by nothing.

``end_date >= starts_on`` is NOT validated here.  It is a two-field comparison
over values the FORM collects, so it belongs to the submission's validator
(``schemas/validation/_helpers``) and to the CHECK of the same name that plan
step R7c-b puts on the table.  This module refuses what a CALLER states that
its own derivations cannot survive, which since that step is one column domain
(:func:`_require_authored_domains`) and three structural pairs
(:func:`~._nominal_day._require_nominal_day_pair`, which moved with the whole
nominal-day leaf to :mod:`._nominal_day` at plan step salary:R15-a,
:func:`~._month_coordinate._require_weekday_reading_pair`, born in its own
leaf at plan step recurrence:R8-c, and :func:`_require_month_ceiling_pair`).

**The bound's own shape needs no validation at all, since plan step R7b-3.**
"At most one closing bound" and "a count names at least one occurrence" are
carried by :class:`~app.services.recurrence.EndBound`, which cannot express
either violation, so this module holds no refusal for them and neither does
anything else.
"""
from dataclasses import dataclass
from datetime import date

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.services.recurrence._bounds import NEVER_ENDS, EndBound
from app.services.recurrence._closing import Closing
from app.services.recurrence._frequency import (
    Cadence,
    RecurrenceResolutionError,
    can_repeat_within_month,
    canonical_cadence,
)
from app.services.recurrence._month_coordinate import (
    MonthCoordinate,
    WeekdayReading,
    _require_weekday_reading_pair,
    month_coordinate,
)
from app.services.recurrence._nominal_day import _require_nominal_day_pair
from app.services.recurrence._offer import require_authorable_cadence
from app.services.recurrence._row_day import date_row
from app.utils.books_boundary import row_books_day
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN

#: The floor ``ck_recurrence_rules_positive_max_per_month`` bounds its column
#: to (plan step salary:R15-a).  A ceiling of zero would be a rule that never
#: fires spelled as a cadence, which is the ``Once`` shape plan step R2e-3
#: deleted; "no ceiling" is ``NULL`` and nothing else.
_MAX_PER_MONTH_MIN = 1

#: The window ``ck_recurrence_rules_starts_on_range`` bounds its column to, and
#: the one both template schemas bound their ``starts_on`` field with.  Read
#: from the application's own calendar vocabulary rather than restated, so the
#: door, the schemas and the CHECK cannot come to disagree about how far the
#: calendar reaches.  See :func:`_require_authored_start_window`.
_STARTS_ON_MIN = CALENDAR_DATE_MIN
_STARTS_ON_MAX = CALENDAR_DATE_MAX


def _require_month_ceiling_pair(
    unit: RecurrenceUnitEnum,
    max_per_month: int | None,
    *,
    where: str,
) -> None:
    """Refuse a ``(unit, max_per_month)`` pair the walk could never read.

    **The one statement of the invariant, held at CONSTRUCTION** exactly as
    :func:`_require_nominal_day_pair` holds its own (plan step salary:R15-a,
    ruling **R-SAL29**).  A per-month ceiling caps how many of a rule's own
    occurrences a calendar month admits, and a cadence measured in whole
    months fires at most once a month by construction -- so a ceiling beside
    a ``MONTH`` or ``YEAR`` unit is a value the occurrence walk applies and
    never changes an answer with.  Storing it would be the closed-set shape
    this package removes: a column holding a fact no reader can act on, which
    is how a value comes to be maintained by nobody and believed by someone.

    The actionable check in front of ``ck_recurrence_rules_max_per_month_unit``
    (plan step plan#2302), which holds a writer that never reaches this one.

    Membership is :func:`~app.services.recurrence.can_repeat_within_month`,
    and not a second list of units: that predicate is what the picker offers
    the control on and what the shared schema refuses against, so the three
    surfaces cannot come to disagree about which units admit a ceiling.

    Args:
        unit: The cadence unit.
        max_per_month: The ceiling, or ``None`` for none.
        where: What to name in the refusal, composed by the caller because only
            the caller knows which value is being built.

    Raises:
        RecurrenceResolutionError: When a ceiling is stated on a unit whose
            occurrences cannot repeat within a month.
    """
    if max_per_month is None or can_repeat_within_month(unit):
        return
    raise RecurrenceResolutionError(
        f"recurrence max_per_month {max_per_month} cannot sit on a {unit!r} "
        f"cadence for {where}: a cadence measured in whole months fires at "
        f"most once a month by construction, so the ceiling could never bind "
        f"and the value would be stored for no reader.  A per-month ceiling "
        f"belongs on a cadence whose occurrences can repeat within a month "
        f"(paychecks, weeks); state no ceiling here."
    )


@dataclass(frozen=True)
class ResolvedRecurrence:  # pylint: disable=too-many-instance-attributes
    """What a recurrence MEANS, on the two axes, against one schedule.

    A computed value, never a row: it is what plan step R3's forward occurrence
    engine consumes and what the display describer words.  Since plan step
    R7c-b the columns behind it are AUTHORED rather than derived, so this value
    is mostly a typed reading of them -- the two things it still computes are
    the pay-period normalisation and the cycle phase, both stated in the module
    docstring.

    Pylint: ``too-many-instance-attributes`` (12/7) -- the first ten are what
    one recurrence means, read as a flat unit by a single consumer, and the
    plan's END-state table (section 3) carries all but ``offset_periods``; the
    ninth, ``max_per_month``, joined at plan step salary:R15-a as the
    cadence's third value and is read flat for the reason ``interval_n`` and
    ``unit`` are, and the tenth, ``weekday_reading``, at plan step
    recurrence:R8-c as the third field the month coordinate is read from,
    flat beside ``nominal_day`` for the reason the next sentences give that
    pair.  The eleventh and twelfth,
    ``books_opened_on`` and ``is_envelope`` (rulings **R-PC85**, **R-PC89**),
    are facts about the DEFINITION the read pass attaches as one
    ``balance_at._definition_books.DefinitionBooks`` value and lays here
    flat, each read on its own by the walk -- the floor by the comparison,
    the flag by :meth:`books_day`.  Pairing
    ``starts_on`` with ``nominal_day`` was weighed and rejected: it would make
    every consumer unwrap a two-field object to ask for a date, and since this
    step the pair cannot disagree, so there is nothing for a wrapper to police.
    Mirrors the :class:`RecurrenceSpec` and ``transfer_service.TransferSpec``
    precedents.

    Carries ENUM members rather than ``ref`` table ids because nothing
    persists it.  The ids exist to put a value in a column; a consumer asking
    "is this monthly" should compare
    ``resolved.unit is RecurrenceUnitEnum.MONTH``, not two integers whose
    meaning depends on a seed.

    Attributes:
        offset_periods: The phase within the period cycle -- an
            ``Every N Periods`` rule fires where
            ``(period_index - offset_periods) % interval_n == 0``.  Derived
            here from :attr:`starts_on` so the write door writes it from the
            same call that produced the date, rather than running the
            derivation twice and hoping the two agree.  **Nothing reads the
            COLUMN it is written to** (plan ledger rows **D21** / **D24**);
            plan step R7c-c drops it.
        interval_n: How many *unit*\\ s pass between occurrences.  Always the
            two-axis reading: 3 for Quarterly, 6 for Semi-Annual, the authored
            count for ``Every N Periods``, 1 elsewhere.
        unit: The cadence unit *interval_n* counts.
        starts_on: The rule's FIRST OCCURRENCE, one meaning for every unit
            (ruling **R-R16**): the first date a calendar cadence fires on, and
            the payday of the first paycheck a pay-period cadence bills in.
            Occurrences are this date plus multiples of *interval_n* units, so
            its position in the cycle IS the rule's phase and nothing is
            generated before it.
        placement: How an occurrence DATE maps onto the pay period a row lives
            in.  The axis today's Monthly and Monthly First patterns differ on.
        shift: Weekend / holiday adjustment for the occurrence date.  Always
            ``NONE`` until plan step R8.
        closing: EVERYTHING that stops this definition -- the bound the owner
            authored, and any stop DERIVED from outside the rule, held as one
            value that answers for both (:class:`~app.services.recurrence.
            Closing`, plan step R7d-d).  It was the authored bound alone until
            that step, which is why a loan payment's derived payoff had to be
            CACHED into the authored bound's own column to reach the walk at
            all -- one column holding two facts, ``CLAUDE.md`` rule 14's
            stored-and-derived case.

            The authored half is still ONE value with three shapes, so "at
            most one closing bound"
            (``ck_recurrence_rules_single_end_bound``) is a state the type
            cannot express rather than one anything has to check; see
            :mod:`app.services.recurrence._bounds`.  The derived half is a
            separate closed set for the reason that module states -- nothing
            offers, posts or stores it -- and lives in
            :mod:`app.services.recurrence._closing`.

            **:func:`resolve` fills the authored half and leaves the derived
            one empty**, because deciding it means folding a destination's
            balance and this module is pure.  The composed read door
            (``recurring_definition.resolved_definition``) is what attaches it.
            A consumer never has to know which of the two it holds: the walk
            asks :meth:`~app.services.recurrence.Closing.admits` and the
            describer words the whole value.
        nominal_day: The day the rule MEANS when *starts_on*'s own month was
            too short to hold it -- April has no 31st, so a day-31 rule first
            occurring there carries ``starts_on = 2026-04-30`` and
            ``nominal_day = 31``.  ``None`` when the date holds the day itself,
            which is every rule whose day is 1-28 and every rule that does not
            fire on a day of the month at all.  Presence is the discriminator
            (ruling R-R3), and it is what stops a month-end rule from decaying
            to the 30th forever.  **Read it through :attr:`month_coordinate`**,
            never directly: the day a rule MEANS is the fields taken
            together, and open-coding that join is how a second answer starts.
        max_per_month: The most occurrences any one calendar month admits,
            or ``None`` for no ceiling (plan step salary:R15-a, ruling
            **R-SAL29**, amended 2026-09-13): a month's first N paydays on
            the owner's calendar for the paycheck unit, the rule's own first
            N of the month for the week unit.  The occurrence walk drops the
            rest, for every unit; it is refused at construction beside a unit
            whose occurrences cannot repeat within a month, so a value here is
            one the walk can act on.
        weekday_reading: How *starts_on* is read by its WEEKDAY -- "the
            third Tuesday" from the month's start, "the last Friday" from its
            end -- or ``None`` to read it by its day of the month (plan step
            recurrence:R8-c, rulings plan#2668, plan#2670).  The week number
            and the weekday come from *starts_on*, so this is the whole of
            what a rule states about them.  Exclusive with *nominal_day* and
            refused at construction where the date does not fit it
            (:func:`~._month_coordinate._require_weekday_reading_pair`).
            **Read it through :attr:`month_coordinate`**, as *nominal_day*.
            :func:`resolve` carries :class:`RecurrenceSpec`'s verbatim, and
            ``budget.recurrence_rules.weekday_reading`` stores it.
        books_opened_on: The latest day the books start across every
            account the definition moves money in, or ``None`` for no
            floor (ruling **R-PC85**, plan step ``pay_calendar:C18-a``).  **The
            opening end's mirror of :attr:`closing`**: the rule says when it
            fires and the ACCOUNT says where the app keeps books, so the walk
            names no occurrence whose row would land on or before this day
            (ruling **R-PC86**: the row's CASH day, :meth:`row_date`, under
            :func:`~app.utils.books_boundary.books_hold`'s strict reading --
            an opening is the CLOSE of its day, ruling R-HG).  Until that step
            nothing stated the bound at all: the owner's first payday stood in
            for it, because an occurrence with no paycheck to land in is
            skipped, and a paycheck recorded below the books let the rule fill
            it -- a Van Payment and a birthday dated before Checking's books,
            measured on a production clone.

            **:func:`resolve` leaves it ``None``**, for the reason it leaves
            the closing's derived half empty: it is pure, and the day is a
            fact about ACCOUNTS read from the database.  ONE composition
            attaches it (``balance_at.resolved_with_books``), with two
            callers: the read pass's memo (``BalanceContext.resolved_for``),
            which every reader of a STORED or unsaved definition's
            occurrences takes it from -- row creation, the forecast's loan
            estimate, the Recurring screens and the form's live preview --
            and the opening restatement's refusal, which asks it of books no
            account has yet (``planned_rows_books``).  **It is not a copy of
            the rule's start
            and must not become one**: an opening is restated (the owner's
            Van Loan moved 05-21 -> 04-22 on the data this was measured on),
            and a stored copy would need a reconciler (``CLAUDE.md`` rule 14).
        is_envelope: Whether the definition is an ENVELOPE -- a spending
            budget whose row takes purchases, the template's
            ``is_envelope`` -- so the walk compares its paycheck's LAST day
            with the books rather than its due day (ruling **R-PC89**, plan
            step ``pay_calendar:C18-a``; :meth:`books_day`).  Attached beside
            :attr:`books_opened_on` by the same composition
            (``balance_at.resolved_with_books``) and for the same reason
            :func:`resolve` leaves it ``False``: it is a fact about the
            DEFINITION, not the rule.  A transfer is never one.
    """

    offset_periods: int
    interval_n: int
    unit: RecurrenceUnitEnum
    starts_on: date
    placement: PeriodPlacementEnum
    shift: BusinessDayShiftEnum
    closing: Closing
    nominal_day: int | None
    max_per_month: int | None
    weekday_reading: WeekdayReading | None = None
    books_opened_on: date | None = None
    is_envelope: bool = False

    def __post_init__(self) -> None:
        """Refuse a value whose first occurrence and nominal day disagree.

        The same invariant :class:`RecurrenceSpec` holds, restated on the
        RESOLVED side because this value has writers the spec does not: a test
        building one directly, and any later step that composes one from
        columns.  Holding it here is what let plan step R7c-b delete
        ``_occurrence._require_generable``'s clamp branch -- a guard whose only
        reachability condition was that somebody built this pair by hand.

        **And the ``(unit, max_per_month)`` pair, for the same reason** (plan
        step salary:R15-a): a ceiling on a calendar-month cadence is a value
        the walk applies and never reads, so it is unconstructible here rather
        than filtered there.  **And the weekday reading** (plan step
        recurrence:R8-c), whose shape the walk would otherwise land in the
        wrong month or on a day its owner did not choose.

        Raises:
            RecurrenceResolutionError: See :func:`_require_nominal_day_pair`,
                :func:`~._month_coordinate._require_weekday_reading_pair` and
                :func:`_require_month_ceiling_pair`.
        """
        _require_nominal_day_pair(
            self.unit, self.starts_on, self.nominal_day,
            where="a resolved recurrence",
        )
        _require_weekday_reading_pair(
            self.unit, self.starts_on, self.nominal_day, self.weekday_reading,
            where="a resolved recurrence",
        )
        _require_month_ceiling_pair(
            self.unit, self.max_per_month, where="a resolved recurrence",
        )

    @property
    def month_coordinate(self) -> MonthCoordinate | None:
        """Return the day of each month this recurrence lands on.

        :func:`~._month_coordinate.month_coordinate` over this value's own
        fields -- see it for why the join has one implementation.  The
        occurrence walk walks it and the describer words it, each dispatching
        on its SHAPE (plan step recurrence:R8-c, plan ledger row **D29**).

        Returns:
            A day of the month, the nth weekday or the last weekday, or
            ``None`` for a unit with no month coordinate.
        """
        return month_coordinate(
            self.unit, self.starts_on, self.nominal_day, self.weekday_reading,
        )

    @property
    def day_of_month(self) -> int | None:
        """Return the one day of the month this recurrence fires on, if it has one.

        :attr:`month_coordinate`'s
        :attr:`~._month_coordinate.MonthCoordinate.day_of_month`: the meant
        day for a rule read by its day of the month, and ``None`` both for a
        weekday rule, whose day moves from month to month, and for a unit with
        no month coordinate.  **A reader that must tell those two apart asks
        :attr:`month_coordinate`**; this answers the narrower question "is
        there one fixed day", which the describer's "first paycheck" collapse
        asks.

        Returns:
            The day 1-31 the rule means, month-end clamped per month by the
            walk itself, or ``None``.

            **The UNIT decides, not the (unit, placement) pair**, and the
            difference is not an oversight.  ``fires_on_day_of_month`` answers
            whether a generated ROW is dated from that day, which is a
            different question: a MONTH rule funded from a month's FIRST
            paycheck still fires on a day of the month, and the describer names
            it -- "Monthly (day 15, first paycheck)" -- suppressing only day 1,
            which "first paycheck" already implies.  Asked through
            :func:`has_day_of_month_coordinate`, so a producer that has to make
            the same distinction has a name to reach for; see that function for
            the two wrong-money defects that came of reaching for the other one.
        """
        coordinate = self.month_coordinate
        if coordinate is None:
            return None
        return coordinate.day_of_month

    def row_date(self, occurrence: date, period: DerivedPeriod) -> date:
        """Return the day the row this recurrence generates for *occurrence* is due.

        **The day** :func:`~app.services.recurrence.compute_due_date`
        **stamps on that row, from this value's own coordinates** (plan step
        ``pay_calendar:C18-a``, ruling **R-PC86**).  Both hand one body --
        :func:`~app.services.recurrence._row_day.date_row` -- the placed
        occurrence and the cadence's unit and placement, so the day the walk
        bounds by the books and the day the written row carries are one
        derivation: the occurrence for a calendar cadence funded by the
        paycheck containing it, the funding payday for every other (plan
        steps recurrence:R5-a and R8-b, rulings **R-R94** / **R-R95**).

        Args:
            occurrence: The date the cadence names, off the placement.
            period: The pay period that placement seats it in, saved or
                projected.

        Returns:
            The due day.
        """
        return date_row(
            occurrence, period, unit=self.unit, placement=self.placement,
        )

    def books_day(self, occurrence: date, period: DerivedPeriod) -> date:
        """Return the day of the row for *occurrence* that the books are compared with.

        :func:`~app.utils.books_boundary.row_books_day` over this value's own
        row date and *period*'s last day (plan step ``pay_calendar:C18-a``):
        the due day for a bill (ruling **R-PC86**), the paycheck's last day
        for an envelope (ruling **R-PC89**).  The picker lives in that pure
        leaf beside :func:`~app.utils.books_boundary.books_hold`, the one
        comparison it feeds; the doors refusing to strand a planned row
        (``app.services.planned_rows_books``) reach both only through the
        walk that asks this.

        Args:
            occurrence: The date the cadence names, off the placement.
            period: The pay period that placement seats it in, saved or
                projected.

        Returns:
            The day the walk hands
            :func:`~app.utils.books_boundary.books_hold`.
        """
        return row_books_day(
            self.row_date(occurrence, period), period.end_date,
            is_envelope=self.is_envelope,
        )


@dataclass(frozen=True)
class RecurrenceSpec:  # pylint: disable=too-many-instance-attributes
    """What a caller AUTHORS about a recurrence.

    **The TWO-AXIS vocabulary since plan step R7b, and ONE DATE since plan step
    R7c-b.**  Three fields left at that step and they were one fact between
    them: ``start_date`` was the opening validity bound, ``day_of_month`` the
    cycle's day and ``month_of_year`` its month.  A recurrence's first
    occurrence carries all three -- it is the earliest thing the cadence
    produces, its day is the cycle's day, and its month is the cycle's residue
    class -- so ``starts_on`` is the whole of it and the other three are gone
    (ruling **R-R16**, plan ledger row **D28**).  What made that ruling
    necessary is that a BOUND cannot carry a month residue: measured on a
    production clone, 18 of the 24 live multi-month rules would have fired in
    the wrong months forever under the reading it replaced.

    Pylint: ``too-many-instance-attributes`` (9/7) -- these are the irreducible
    inputs of one authoring request, exactly the fields the recurrence form
    collects, read as a flat unit by the single consumer (:func:`resolve`);
    the eighth, ``max_per_month``, is the cadence's third value since plan
    step salary:R15-a (``due_day_of_month`` left at plan step
    recurrence:R5-a, ruling **R-R96**), and the ninth, ``weekday_reading``,
    is how ``starts_on`` is read since plan step recurrence:R8-c, flat beside
    ``nominal_day`` for the reason :class:`ResolvedRecurrence` gives that
    pair.  Mirrors the ``TransferSpec`` precedent.  Frozen so a constructed
    spec is an immutable record of one request.

    Attributes:
        user_id: The owning user.
        unit: The cadence unit -- what *interval_n* counts.
        starts_on: The rule's FIRST OCCURRENCE.  Required, and that is a money
            decision rather than a typing one: the create routes generate with
            no lower window bound, so an unbounded opening let a ``$2,000.00``
            rent template created today write five backdated rows into pay
            periods that had already closed.  A caller may state any date for
            a pay-period cadence -- :func:`resolve` NORMALISES it onto the
            paycheck that hosts it, so what is stored is always a real
            occurrence.
        interval_n: How many *unit*\\ s pass between occurrences.  Meaningful
            for EVERY unit since plan step R7b, which is the point: it was
            read only for ``Every N Periods`` while the other three cadences
            baked their interval into a pattern NAME, and that fusion is this
            arc's root cause.
        placement: Which pay period funds an occurrence.  **INERT under the
            ``PERIOD`` unit** -- a pay-period-space rule emits a paycheck's own
            ``start_date`` and both placements carry such a date back to that
            same period (proven in :mod:`._occurrence`) -- so it defaults to
            ``CONTAINING_DATE``, the reading under which the occurrence is
            funded by the paycheck it falls in.
        nominal_day: The day the rule means when *starts_on*'s own month was
            too short to hold it, and ``None`` -- which is every ordinary rule
            -- when the date holds the day.  The form collects it only where
            the chosen date leaves the question open: a date that is its
            month's last day in a month shorter than 31 days could be "the
            28th" or "the last day of the month", and those are different
            cadences from the following month on.
        end_bound: The rule's closing validity bound -- indefinite, a date, or
            a count of occurrences, as ONE value
            (:class:`~app.services.recurrence.EndBound`).  Replacing it is how
            a closing bound CHANGES, which is what makes
            ``replace(spec, end_bound=...)`` safe for the one writer that owns
            a bound it did not author: ``loan_recurrence_sync`` states the
            loan's payoff and the shape it replaces cannot leave a count
            behind.  Defaults to :data:`~app.services.recurrence.NEVER_ENDS`,
            which 41 of the 46 live rules carry.
        max_per_month: The most occurrences any one calendar month admits --
            a month's first N paydays on the owner's calendar for a paycheck
            cadence -- or ``None``, the default and every rule authored before
            plan step salary:R15-a, for no ceiling (ruling **R-SAL29**, amended
            2026-09-13).  "Every paycheck, at most 2 a month" is a payroll
            benefit taken on a month's first two paychecks, the cadence eleven
            of the developer's twelve deductions carry and the one ledger row
            **D59** measured the two-axis vocabulary unable to say.  Refused
            at construction beside a unit whose occurrences cannot repeat
            within a month (:func:`_require_month_ceiling_pair`), and below
            one by :func:`_require_authored_domains`, mirroring the column's
            CHECK.
        weekday_reading: How *starts_on* is read by its WEEKDAY -- "the
            third Tuesday" from the month's start, "the last Friday" from its
            end -- or ``None``, the default and every rule authored before
            plan step recurrence:R8-c, to read it by its day of the month
            (rulings plan#2668, plan#2670).  The week number and the weekday
            come from *starts_on*, so this is the whole of what a caller
            states about them.  Refused at construction beside a
            *nominal_day*, on a unit with no month coordinate, and where the
            date does not fit it
            (:func:`~._month_coordinate._require_weekday_reading_pair`), so
            ``ck_recurrence_rules_weekday_reading``,
            ``ck_recurrence_rules_weekday_reading_unit`` and
            ``ck_recurrence_rules_one_month_reading`` are never what refuses
            one.
    """

    user_id: int
    unit: RecurrenceUnitEnum
    starts_on: date
    interval_n: int = 1
    placement: PeriodPlacementEnum = PeriodPlacementEnum.CONTAINING_DATE
    nominal_day: int | None = None
    end_bound: EndBound = NEVER_ENDS
    max_per_month: int | None = None
    weekday_reading: WeekdayReading | None = None

    def __post_init__(self) -> None:
        """Refuse a spec whose first occurrence and nominal day disagree.

        Held at construction rather than inside :func:`resolve` so that no
        caller can build the contradiction and pass it on -- the idiom
        :class:`~app.services.recurrence.EndBound` and
        :class:`~app.services.recurrence.OccurrencePlacement` already use in
        this package.  The ``(unit, max_per_month)`` pair is refused here for
        the same reason (plan step salary:R15-a), and so is the weekday
        reading (plan step recurrence:R8-c), the one invariant
        :class:`ResolvedRecurrence` holds beside them.

        Raises:
            RecurrenceResolutionError: When *starts_on* is absent, or see
                :func:`_require_nominal_day_pair`,
                :func:`~._month_coordinate._require_weekday_reading_pair` and
                :func:`_require_month_ceiling_pair`.
        """
        # ``starts_on`` is typed ``date`` and the column is ``NOT NULL`` from
        # plan step R7c-b, so the only way to reach a ``None`` here is a rule
        # row written before that migration or a value built by hand.  Named
        # rather than left to fail three frames down in ``month_ordinal`` with a
        # bare ``AttributeError``: this is the field the whole cadence hangs
        # off, and "the rule has no first occurrence" is the actionable
        # sentence.
        if self.starts_on is None:
            raise RecurrenceResolutionError(
                f"a {self.unit!r} recurrence (user {self.user_id}) states no "
                f"starts_on.  It is the rule's FIRST OCCURRENCE and carries "
                f"the cadence's day, its cycle phase and its opening bound "
                f"(ruling R-R16), so there is nothing left to derive one from; "
                f"budget.recurrence_rules.starts_on is NOT NULL, and the form "
                f"collects it whenever a cadence is chosen."
            )
        _require_nominal_day_pair(
            self.unit, self.starts_on, self.nominal_day,
            where=f"a {self.unit!r} recurrence (user {self.user_id})",
        )
        _require_weekday_reading_pair(
            self.unit, self.starts_on, self.nominal_day, self.weekday_reading,
            where=f"a {self.unit!r} recurrence (user {self.user_id})",
        )
        _require_month_ceiling_pair(
            self.unit, self.max_per_month,
            where=f"a {self.unit!r} recurrence (user {self.user_id})",
        )


def _require_owner(spec: RecurrenceSpec, calendar: PayCalendar) -> None:
    """Refuse a spec resolved against somebody else's schedule.

    A pay-period cadence's first occurrence is normalised against a schedule
    and its phase is read off one, so pairing a rule with the wrong owner's
    calendar produces values that are silently WRONG rather than an error --
    and a call site could derive the calendar's owner from a different
    object than the rule's: ``loan_recurrence_sync.sync_recurring_payment_bounds``
    used ``account.user_id`` against a spec read from the rule until plan
    step R7d-g deleted it (its successor reads ``rule.user_id``).  Nothing
    else enforces the pairing, so checking it here makes the assumption a
    fact rather than a property of today's callers.

    Args:
        spec: The authored recurrence.
        calendar: The schedule it is being resolved against.

    Raises:
        RecurrenceResolutionError: When the two name different users.
    """
    if calendar.user_id != spec.user_id:
        raise RecurrenceResolutionError(
            f"recurrence for user {spec.user_id} cannot be resolved against "
            f"user {calendar.user_id}'s pay-period schedule.  A pay-period "
            f"cadence's first occurrence and its phase are both measured "
            f"against the OWNER's schedule, so the mismatched pair would "
            f"produce a plausible wrong date rather than an error."
        )


def _require_authored_domains(spec: RecurrenceSpec) -> None:
    """Refuse an authored value outside the domain its own column allows.

    ``app.services.recurrence._authoring._author`` writes an authored value
    verbatim into a column whose CHECK mirrors its domain, so an
    out-of-domain value reaches the flush as an unhandled ``IntegrityError``
    naming neither the field nor the value.  Refusing at the door names both.

    **The due day LEFT this function at plan step recurrence:R5-a** (ruling
    **R-R96**), by its column being dropped: a rule's own day is the day its
    rows are due, so there is no second day left to author.

    **Two domains LEFT this function at plan step R7c-b**, and they left by
    their columns ceasing to be authored rather than by the refusal being
    dropped.  ``day_of_month`` and ``month_of_year`` are now ENCODED by the
    write door from the resolved first occurrence (the same relationship
    ``pattern_id`` has had to the cadence since plan step R7b), so no caller
    states a value for a mirror to refuse.  ``ck_recurrence_rules_valid_offset``
    left one step earlier, the same way.  All three CHECKs stay on the table,
    where a restore or a hand edit can still reach them.

    **``starts_on`` JOINED it at plan step R7c-b**, and it is the same kind of
    refusal for the same kind of reason: a value the door writes verbatim into
    a column whose CHECK mirrors this bound.  See
    :func:`_require_authored_start_window`, which states why the window is the
    one it is.

    **``max_per_month`` JOINED it at plan step salary:R15-a**, the same kind
    of refusal for the same kind of reason: the door writes it verbatim into a
    column carrying ``ck_recurrence_rules_positive_max_per_month``, and a
    ceiling of zero would reach the flush as an ``IntegrityError`` -- or,
    unguarded, be a rule that never fires spelled as a cadence.

    Args:
        spec: The authored recurrence.

    Raises:
        RecurrenceResolutionError: When a STATED per-month ceiling is below
            one, or the first occurrence falls outside the calendar window the
            application reaches.  A ``None`` ceiling states nothing and
            passes.
    """
    _require_authored_start_window(spec)
    ceiling = spec.max_per_month
    if ceiling is not None and ceiling < _MAX_PER_MONTH_MIN:
        raise RecurrenceResolutionError(
            f"recurrence max_per_month must be NULL or at least "
            f"{_MAX_PER_MONTH_MIN}, got {ceiling} for a {spec.unit!r} "
            f"recurrence (user {spec.user_id}).  It is written to a column "
            f"carrying ck_recurrence_rules_positive_max_per_month, so letting "
            f"it through would raise an unhandled IntegrityError at the "
            f"flush; and a ceiling of zero would be a rule that never fires "
            f"spelled as a cadence, which is the Once shape plan step R2e-3 "
            f"deleted.  No ceiling is NULL."
        )


def _require_authored_start_window(spec: RecurrenceSpec) -> None:
    """Refuse a first occurrence outside the window this application reaches.

    **The bound plan step R7b-4's ``_MAX_START_DATE_YEAR`` used to carry, minus
    its retired rationale.**  That constant was derived from the anchor walk's
    month probes, and R7c-b deleted that walk -- but the hazard it named
    outlived it by a different route.  Measured on the preview endpoint, which
    reads this value straight from ``request.args``::

        GET /templates/preview-recurrence?recurrence_unit=<PERIOD>&starts_on=9999-12-31
        OverflowError: date value out of range
          app/services/pay_calendar/_calendar.py:998 in _projected_after

    Past the saved horizon the calendar PROJECTS the covering paycheck, and
    building its end adds ``cadence_days`` to a start already at
    ``date.max`` -- an exception from outside this package's hierarchy, so the
    endpoint's own handler does not catch it and any signed-in user gets a 500.

    **The window is the one the application already states**, not a new opinion
    invented here: :data:`~app.schemas.validation._helpers.EFFECTIVE_DATE_MIN`
    and ``_MAX`` bound how far its calendar reaches, the two template schemas
    bound ``starts_on`` with them, and
    ``ck_recurrence_rules_starts_on_range`` mirrors them on the column for a
    writer that never sees a schema.  Stating it HERE as well is what closes
    the door the schema does not stand in front of -- the preview reads query
    args, and its docstring's rule is that every bound lives on the column and
    its mirror in this module rather than a third time on the endpoint.

    Args:
        spec: The authored recurrence.

    Raises:
        RecurrenceResolutionError: The first occurrence lies outside
            ``[EFFECTIVE_DATE_MIN, EFFECTIVE_DATE_MAX]``.
    """
    if _STARTS_ON_MIN <= spec.starts_on <= _STARTS_ON_MAX:
        return
    raise RecurrenceResolutionError(
        f"recurrence starts_on must fall between {_STARTS_ON_MIN} and "
        f"{_STARTS_ON_MAX}, got {spec.starts_on} for a {spec.unit!r} "
        f"recurrence (user {spec.user_id}).  It is written to a column "
        f"carrying ck_recurrence_rules_starts_on_range, and a date near the "
        f"end of the representable range overflows the pay calendar's forward "
        f"projection -- an OverflowError from outside this package's error "
        f"hierarchy rather than a refusal naming the field."
    )


def _first_occurrence(spec: RecurrenceSpec, calendar: PayCalendar) -> date:
    """Return the date this recurrence FIRST fires on, for any unit.

    **The value ``budget.recurrence_rules.starts_on`` holds** (ruling
    **R-R16**), and for three of the four units it is simply what the caller
    authored: a calendar cadence names its own dates, so the first one is a
    fact about the rule rather than about the owner's schedule.

    **The ``PERIOD`` unit is the one that normalises, and that is what removes
    plan ledger row D6's asymmetry rather than restating it.**  Its occurrences
    are PAYDAYS, so an authored date that is not one does not name an
    occurrence at all.  The paycheck the rule bills in is the first one that
    has not ENDED before that date -- which is exactly
    :func:`app.services.recurrence._occurrence._period_walk`'s own admission
    test, so this is the walk's first yield stated as a direct search rather
    than a second opinion about it.  A date BELOW the owner's first payday
    names that first paycheck, because there is no earlier one to bill in.

    **What the normalisation does NOT keep is the authored date itself, and
    that is plan ledger row D39** (opened at plan step R7c-c, owned by R5).
    For a loan payment billed by PAYCHECK the authored date is the first
    CONTRACTUAL INSTALLMENT, and what this stores is the payday of the paycheck
    hosting it -- which selects the same paycheck and generates identically,
    but leaves the rule's opening bound up to a pay period BEFORE the loan
    exists, and leaves the installment date unrecoverable from the row.
    Narrow: both live loan payments fire on a day of the month, where the
    branch above returns the authored date untouched.  It is a different fact
    from the asymmetry this function removed, which is why it carries an id of
    its own rather than reopening **D6**.

    ``span_containing`` rather than ``period_containing``, so the answer is
    TOTAL: an authored date past the materialised horizon has no SAVED paycheck
    to take a payday from, and a ``NOT NULL`` column has no shape for the
    ``None`` that would leave.  The calendar projects the payday forward at the
    owner's own cadence, which is the same answer the schedule will hold once
    it extends.

    Args:
        spec: The authored recurrence.
        calendar: The owner's pay-period schedule.  Read only by the ``PERIOD``
            unit; a calendar-space cadence names its own first date.

    Returns:
        The first occurrence date.

    Raises:
        RecurrenceResolutionError: When the owner's schedule is empty, so a
            pay-period cadence has no paycheck to name.  Registration
            bootstraps a schedule (``registration_service.register_user``), so an empty
            one is a broken invariant rather than a state to paper over.
    """
    if spec.unit is not RecurrenceUnitEnum.PERIOD:
        return spec.starts_on
    opening = calendar.opening_bound()
    if opening is None:
        raise RecurrenceResolutionError(
            f"user {spec.user_id} has no pay periods, so a pay-period "
            f"recurrence has no first paycheck to name.  Registration "
            f"bootstraps a schedule (registration_service.register_user), so an empty "
            f"schedule here is a broken invariant rather than a state to "
            f"paper over."
        )
    # ``max`` rather than a refusal below the opening payday: a paycheck-space
    # rule whose authored date precedes every paycheck bills in the FIRST one,
    # because there is no earlier paycheck for it to bill in.  Above the
    # opening ``span_containing`` never answers ``None`` -- it does so only for
    # an empty calendar or a day below that bound, and both are excluded here.
    return calendar.span_containing(max(spec.starts_on, opening)).start_date


def _derive_offset_periods(
    cadence: Cadence, calendar: PayCalendar, starts_on: date,
) -> int:
    """Return the ``offset_periods`` phase a recurrence fires on.

    A TOTAL function of the first occurrence: an ``Every N Periods`` rule fires
    on the paycheck it starts in and every Nth paycheck after, so the phase is
    that paycheck's own ordinal modulo the interval.  It is not a fact anyone
    authors and it never was -- no template under ``app/templates/`` has ever
    rendered an input for it -- and since plan step R7c-b it is not read back
    off its column on any path either, which is plan ledger rows **D21** and
    **D24**.

    Applying the derivation on every write rather than only on create is what
    closes defect **D1** -- the update path wrote the schema default
    unconditionally, re-phasing every future occurrence of an
    every-N-paychecks rule on an amount-only edit.

    **A cadence of every ONE unit has phase 0 by construction**: every
    paycheck qualifies, so ``index % 1`` is 0 for all of them.  Stating that
    ahead of the derivation reproduces the closed-set rule this replaced
    exactly -- ``Every Period`` returned 0 unconditionally while
    ``Every N Periods`` with ``N = 1`` derived a 0, and the two-axis reading
    cannot tell those apart because they are the same cadence.

    ``span_containing`` cannot return ``None`` here: *starts_on* comes from
    :func:`_first_occurrence`, which for this unit answers a payday at or above
    the opening bound.

    Args:
        cadence: The recurrence's CANONICAL cadence, from
            :func:`~app.services.recurrence._frequency.canonical_cadence`.  The
            canonical one rather than the spec's own so that this phase and the
            walk that reads it are derived from one pair; the substitution
            never touches the ``PERIOD`` unit, so the value is the same either
            way, and taking it from the same place is what keeps it so.
        calendar: The owner's pay-period schedule.
        starts_on: The rule's first occurrence, from :func:`_first_occurrence`.

    Returns:
        The phase, always in ``0 .. interval_n - 1``.
    """
    if cadence.unit is not RecurrenceUnitEnum.PERIOD or cadence.interval_n == 1:
        # No other cadence reads the phase at all; 0 is its identity.
        return 0
    return (
        calendar.span_containing(starts_on).period_index % cadence.interval_n
    )


def resolve(spec: RecurrenceSpec, calendar: PayCalendar) -> ResolvedRecurrence:
    """Resolve an authored recurrence into its two-axis meaning.

    The single producer of that value.  Nothing persists what it returns --
    the columns behind it are authored, and this reads them back as one typed
    value -- so calling it twice for the same ``(spec, calendar)`` is the only
    way two readers could disagree, and it is a pure function, so they cannot.

    Args:
        spec: What the caller authored.
        calendar: The owner's pay-period schedule, which a pay-period cadence's
            first occurrence is normalised against and whose ordinals carry the
            cycle phase.

    Returns:
        The complete, internally-consistent :class:`ResolvedRecurrence`.

    Raises:
        RecurrenceResolutionError: When *spec* and *calendar* name different
            users, when the cadence is not one this application can AUTHOR
            (``interval_n`` not positive, or a ``(unit, placement)`` pair the
            form does not offer -- see
            :func:`~app.services.recurrence._offer.require_authorable_cadence`),
            when ``max_per_month`` or ``starts_on`` is
            outside its column's domain, or when a pay-period cadence is
            resolved against an owner with no pay periods.  All are broken
            invariants: a recurrence read with a fabricated cadence is worse
            than a refused read.  The ``(starts_on, nominal_day)`` and
            ``(unit, max_per_month)`` pairs and the weekday reading are
            refused by :class:`RecurrenceSpec` itself, before this function is
            reached.

            **The cadence refusal ARRIVED at plan step R7c-c**, and it arrived
            because one left.  ``_authoring._author`` called ``encode_cadence``
            before this function, which refused a cadence the closed pattern
            set could not name -- so every caller of the write door, the
            recurrence PREVIEW included, inherited a completeness check.
            Deleting the encoder took it with it, and the preview began listing
            dates for cadences the schema refuses.  Asking it HERE puts it in
            front of every consumer of a resolved value rather than only the
            ones that write.
    """
    _require_owner(spec, calendar)
    require_authorable_cadence(
        spec.interval_n, spec.unit, spec.placement,
        f"a {spec.unit!r} recurrence (user {spec.user_id})",
    )
    _require_authored_domains(spec)

    # ONE spelling per rhythm, before anything is derived from the pair
    # (ruling **R-R17**, plan step R7c-c).  Freeing the interval made
    # ``(12, MONTH)`` authorable, and it is ``(1, YEAR)``: same month stride,
    # same day-of-month coordinate, same yearly count.  Canonicalising HERE
    # rather than in the write door alone is what keeps the form's live
    # preview, the Recurring cell and the stored row from wording one rhythm
    # three ways -- the write door writes what this returns, so the ruling's
    # "at the write door" is satisfied by the one call every reader already
    # makes.  It takes no PLACEMENT from plan step R8-a: the guard that needed
    # one existed because a year-scale deferred cadence was unauthorable, and
    # that refusal was a fossil of a derivation R7c-b deleted.
    cadence = canonical_cadence(
        spec.interval_n, spec.unit, spec.max_per_month,
    )

    # The first occurrence first, because the phase is a function of it (the
    # ordinal of the paycheck it falls in).  Deriving the two from separate
    # inputs is what let a rule state two cadences before plan step R7b-4.
    starts_on = _first_occurrence(spec, calendar)
    return ResolvedRecurrence(
        offset_periods=_derive_offset_periods(
            cadence, calendar, starts_on,
        ),
        interval_n=cadence.interval_n,
        unit=cadence.unit,
        starts_on=starts_on,
        placement=spec.placement,
        shift=BusinessDayShiftEnum.NONE,
        # The AUTHORED half only: this function is pure, and deciding whether
        # anything outside the rule stops it means folding a destination's
        # balance.  The composed door
        # (:func:`app.services.recurring_definition.resolved_definition`)
        # replaces this field with the same authored bound beside the derived
        # stop it resolved, so every consumer reads ONE field either way.
        closing=Closing(authored=spec.end_bound),
        nominal_day=spec.nominal_day,
        # Off the canonical cadence rather than the spec, for the reason the
        # interval and unit are: one pair of values feeds the walk and the
        # count, and the ceiling is the third of them (plan step salary:R15-a).
        max_per_month=cadence.max_per_month,
        # VERBATIM, canonicalisation included: ``(12, MONTH)`` becomes
        # ``(1, YEAR)`` above and both units have a month coordinate, so a
        # fourth-Thursday rule every 12 months walks 2026-11-26, 2027-11-25,
        # 2028-11-23 under either spelling (plan step recurrence:R8-c).
        weekday_reading=spec.weekday_reading,
        # ``books_opened_on`` stays ``None`` and ``is_envelope`` ``False``:
        # facts about the DEFINITION and its ACCOUNTS, read by the read pass
        # that attaches both (``BalanceContext.resolved_for``), for the
        # reason the closing's derived half is left empty above.
    )
