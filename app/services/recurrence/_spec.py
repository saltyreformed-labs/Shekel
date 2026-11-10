"""What a caller AUTHORS about a recurrence, and the ceiling pair it is held to.

:class:`RecurrenceSpec` is the input :func:`~._resolution.resolve` reads, and
:func:`_require_month_ceiling_pair` the invariant both it and
:class:`~._resolution.ResolvedRecurrence` hold at construction.  Moved verbatim
out of :mod:`._resolution` when the merge of ``dev`` into plan step
credit_card:CC-6 took that module past pylint's 1,000-line ceiling; the
package re-exports :class:`RecurrenceSpec` as it did.

**The dependency runs ONE way**: :mod:`._resolution` imports this module, and
this module imports nothing of it, as :mod:`._nominal_day` and
:mod:`._month_coordinate` stand.
"""
from dataclasses import dataclass
from datetime import date

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.recurrence._bounds import NEVER_ENDS, EndBound
from app.services.recurrence._frequency import (
    RecurrenceResolutionError,
    can_repeat_within_month,
)
from app.services.recurrence._month_coordinate import (
    WeekdayReading,
    _require_weekday_reading_pair,
)
from app.services.recurrence._nominal_day import _require_nominal_day_pair


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
    collects, read as a flat unit by the single consumer (:func:`~._resolution.resolve`);
    the eighth, ``max_per_month``, is the cadence's third value since plan
    step salary:R15-a (``due_day_of_month`` left at plan step
    recurrence:R5-a, ruling **R-R96**), and the ninth, ``weekday_reading``,
    is how ``starts_on`` is read since plan step recurrence:R8-c, flat beside
    ``nominal_day`` for the reason :class:`~._resolution.ResolvedRecurrence` gives that
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
            a pay-period cadence -- :func:`~._resolution.resolve` NORMALISES it onto the
            paycheck that hosts it, so what is stored is always a real
            occurrence.  For the card's due dates it is the first day the
            repeat covers (plan step credit_card:CC-6; the module
            docstring).
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
            one by :func:`~._resolution._require_authored_domains`, mirroring the column's
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

        Held at construction rather than inside :func:`~._resolution.resolve` so that no
        caller can build the contradiction and pass it on -- the idiom
        :class:`~app.services.recurrence.EndBound` and
        :class:`~app.services.recurrence.OccurrencePlacement` already use in
        this package.  The ``(unit, max_per_month)`` pair is refused here for
        the same reason (plan step salary:R15-a), and so is the weekday
        reading (plan step recurrence:R8-c), the one invariant
        :class:`~._resolution.ResolvedRecurrence` holds beside them.

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
