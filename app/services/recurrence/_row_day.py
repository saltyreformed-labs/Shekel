"""
Shekel Budget App -- The DAY a generated row carries, from a cadence's own coordinates

The pure core behind :func:`~app.services.recurrence.compute_due_date` and
:func:`~app.services.recurrence.scheduling_day_of_month`: which day of the
month a cadence schedules its rows on (:func:`cadence_scheduled_day`), and the
calendar day a row answering an occurrence is then due (:func:`date_row`).

**Split out at plan step ``pay_calendar:C18-a`` (ruling R-PC86) so the
occurrence WALK can date a placement exactly as the row would be dated.**
Ruling **R-PC85** made the walk drop an occurrence whose row would land on or
before the books of an account the definition moves money in, and R-PC86 chose
the row's CASH day -- its due day -- as the day compared.  The walk
(``_placement._placements``) holds a :class:`~._resolution.ResolvedRecurrence`,
not a rule row, and could not reach the one producer of that day: it lived in
``_row_date.compute_due_date``, which reads the rule through ``_reading``, and
``_reading`` imports the walk.  Rather than state the dating a second time
over the resolved value, the body MOVED here, below both: ``compute_due_date``
and ``scheduling_day_of_month`` feed it from a rule, the resolved value feeds
it from its own fields, and it is written once (``CLAUDE.md`` rule 14's
placement clause -- move the leaf, not the logic).

**:func:`date_row` reads the OCCURRENCE since plan step recurrence:R5-a**
(rulings **R-R94** / **R-R95**).  It moved here unchanged, defect included:
it picked the base month from the period's two endpoint months, so at a pay
cadence where the firing month is neither endpoint the row was dated in the
wrong month, and two occurrences seated in one paycheck of 30 days or more
shared one date (plan ledger row **D18**).  Both callers hold the placement
the walk seated, so the day the cadence names is in hand and the scan is
deleted rather than fixed; the due-day arm went with the rule's
``due_day_of_month`` column (ruling **R-R96**).

**:func:`date_row` takes the cadence's UNIT and PLACEMENT since plan step
recurrence:R8-b**, rather than a ``from_paycheck`` flag each caller computed
as "the cadence schedules on no day of the month".  That flag was right for
every unit but one: a weekly occurrence names no day of the month, so it read
as "date this from the paycheck" and every weekly row would have carried its
funding PAYDAY with the authored weekday discarded, which is why the ``WEEK``
unit was withheld from the offer set until this step.  Which source a row is
dated from is a fact about the unit and the placement, so it is stated once,
here, over them.

Pure: no Flask, no ORM, no clock, no database.  It imports only the leaves
``_resolution`` already imports, so the walk can import it with no cycle.
"""
from datetime import date

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence._nominal_day import cadence_day_of_month
from app.services.recurrence._offer import (
    emits_period_starts,
    fires_on_day_of_month,
)


def cadence_scheduled_day(
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    starts_on: date,
    nominal_day: int | None,
) -> int | None:
    """Return the day of the month a cadence's generated rows are SCHEDULED on.

    **The gate and the join, stated once** -- the body
    :func:`~app.services.recurrence.scheduling_day_of_month` carried until
    plan step ``pay_calendar:C18-a`` moved it here so a resolved value can ask
    it too.  The day the cadence fires on is
    :func:`~app.services.recurrence._nominal_day.cadence_day_of_month` (the
    join of *starts_on*'s day with *nominal_day*, the same join
    :attr:`~app.services.recurrence.ResolvedRecurrence.day_of_month` reads),
    gated on the ``(unit, placement)`` PAIR by
    :func:`~app.services.recurrence._offer.fires_on_day_of_month`: a MONTH
    rule funded from a month's first paycheck fires on a day of the month but
    its ROW is dated from the paycheck, which is what ``None`` here means.
    See ``scheduling_day_of_month`` for why the gate is that predicate and not
    ``has_day_of_month_coordinate``.

    **``None`` means "no day of the month", and no longer "dated from the
    paycheck"** (plan step recurrence:R8-b).  A weekly rule answers ``None``
    and its rows are dated on their occurrences; which day a row is due is
    :func:`date_row`'s question, asked of the unit and the placement.

    Args:
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.
        starts_on: The rule's first occurrence.
        nominal_day: The day the rule means when *starts_on*'s month was too
            short to hold it, or ``None``.

    Returns:
        The day 1-31 the cadence's rows are scheduled on, or ``None`` for a
        cadence that schedules on no day of the month: a pay-period or weekly
        cadence, or a calendar one funded from a later paycheck.
    """
    if not fires_on_day_of_month(unit, placement):
        return None
    return cadence_day_of_month(unit, starts_on, nominal_day)


def date_row(
    occurrence: date,
    period: DerivedPeriod,
    *,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> date:
    """Return the calendar day a row answering *occurrence* in *period* is due.

    **The body of** :func:`~app.services.recurrence.compute_due_date`,
    **moved rather than restated** (plan step ``pay_calendar:C18-a``).  That
    function reads a rule's coordinates and hands the answer here; the
    occurrence walk reads a resolved value's and hands it here too, so the day
    a row carries and the day the walk bounds by the books (ruling
    **R-PC86**) cannot come apart.

    **Ruling R-R94's formula over the placed occurrence** (plan step
    recurrence:R5-a), **covering weeks since plan step R8-b**: a CALENDAR
    cadence -- weeks, months, years, and a card's statement due dates since
    plan step credit_card:CC-6 -- funded by the paycheck CONTAINING its date
    is due ON the occurrence.  Every paycheck cadence, and every cadence
    funded from a LATER paycheck, is due on its FUNDING paycheck's payday
    (ruling **R-R95**, the answer R-BAL22 gives a one-off).

    The paycheck unit is named on the payday side although its occurrence IS
    that payday (:func:`~app.services.recurrence._offer.emits_period_starts`),
    so either side would answer the same day: the rule says what a paycheck
    cadence is dated FROM, and the two coinciding is a fact about its walk.

    Args:
        occurrence: The date the cadence names for this row, off the
            placement that seated it.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` that
            placement seated it in -- saved, or PROJECTED past the horizon.
            Read for its payday alone.
        unit: The cadence unit.
        placement: Which pay period funds the occurrence.

    Returns:
        The due day.
    """
    if emits_period_starts(unit):
        return period.start_date
    if placement is not PeriodPlacementEnum.CONTAINING_DATE:
        return period.start_date
    return occurrence
