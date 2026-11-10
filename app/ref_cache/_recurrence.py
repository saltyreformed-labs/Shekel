"""The recurrence vocabulary's ids, its month spans and its day-of-month flag.

The three ``ref`` tables a recurrence rule names -- its unit, its placement and
its business-day shift -- and the two facts the unit's row states about itself
(plan step plan#2302, ruling plan#2748).  Moved verbatim out of
:mod:`._accessors` when the merge of ``dev`` into plan step credit_card:CC-6
took that module past pylint's 1,000-line ceiling: every function below stands
exactly as it stood there, and :mod:`app.ref_cache` re-exports all of them, so
no caller changed.  The cache is read through :func:`._state.cache` for the
reason :mod:`._accessors` gives.
"""

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)

from ._state import cache, require_init


def recurrence_unit_id(member):
    """Return the integer primary key for a RecurrenceUnitEnum member.

    The first axis of the two-axis recurrence model (redesign step R2): a
    rule recurs every ``budget.recurrence_rules.interval_n`` units of this
    kind.  Read by the occurrence engine to branch on the cadence unit via
    the integer ID, never the string ``name`` -- the project-wide
    IDs-for-logic invariant.

    Args:
        member: A ``RecurrenceUnitEnum`` member
                (e.g. ``RecurrenceUnitEnum.MONTH``).

    Returns:
        int -- the ``ref.recurrence_units.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RecurrenceUnitEnum member.
    """
    require_init()
    return cache().enum_ids[RecurrenceUnitEnum][member]


def recurrence_unit_months(member: RecurrenceUnitEnum) -> int | None:
    """Return how many months one *member* unit spans, or ``None``.

    The ONE statement of which recurrence units are measured in months, and
    how many (plan step plan#2302, ruling plan#2748): the unit's
    ``ref.recurrence_units.months_per_unit``.  The occurrence engine's month
    stride reads it (``recurrence._months.months_per_step``).

    **``None`` rather than a ``KeyError`` for a value no row names**, unlike
    :func:`ledger_class_is_debit_normal`, and that is the code table this
    replaced keeping its answer (``_MONTHS_PER_UNIT.get``): the stride refuses
    a unit with no span (``MonthStepError``), so an unmodelled value is
    refused INSIDE the recurrence package's error hierarchy, not by the cache.

    Args:
        member: A ``RecurrenceUnitEnum`` member.

    Returns:
        int | None -- 1 for a month, 12 for a year; ``None`` for the paycheck,
        week and due-date units, which are not measured in months at all.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    return cache().recurrence_unit_months.get(member)


def recurrence_unit_has_day_of_month(member: RecurrenceUnitEnum) -> bool:
    """Return whether a cadence measured in *member* fires on a day of the month.

    The unit's ``ref.recurrence_units.has_day_of_month``, which the DATABASE
    generates from :func:`recurrence_unit_months` (``months_per_unit IS NOT
    NULL``) -- so it is read here rather than re-derived, and the flag the
    engine asks and the one a rule's CHECKs hold it to are the same column.

    A MEMBERSHIP answer, as the code tuple it replaced gave
    (``unit in MONTH_SPANNING_UNITS``): ``False`` for a value no row names,
    so the occurrence engine reaches its own refusal of a unit it has no walk
    for (``RecurrenceGenerationError``) rather than a ``KeyError`` here.

    Args:
        member: A ``RecurrenceUnitEnum`` member.

    Returns:
        bool -- True for the month and year units.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    return member in cache().recurrence_day_of_month_units


def period_placement_id(member):
    """Return the integer primary key for a PeriodPlacementEnum member.

    The rule that carries an occurrence DATE onto the pay PERIOD a Shekel
    row lives in (redesign step R2) -- the axis today's ``Monthly`` and
    ``Monthly First`` patterns differ on (they differ on the anchor day as
    well; see :class:`~app.enums.PeriodPlacementEnum`).  Compared by integer
    ID, never by the string ``name``.

    Args:
        member: A ``PeriodPlacementEnum`` member
                (e.g. ``PeriodPlacementEnum.CONTAINING_DATE``).

    Returns:
        int -- the ``ref.period_placements.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid PeriodPlacementEnum member.
    """
    require_init()
    return cache().enum_ids[PeriodPlacementEnum][member]


def business_day_shift_id(member):
    """Return the integer primary key for a BusinessDayShiftEnum member.

    Whether an occurrence landing on a non-business day moves backward,
    forward, or stays put (redesign step R2 creates the vocabulary; step R8
    is the first step a user can choose a value other than ``none``).
    Compared by integer ID, never by the string ``name``.

    Args:
        member: A ``BusinessDayShiftEnum`` member
                (e.g. ``BusinessDayShiftEnum.PRIOR``).

    Returns:
        int -- the ``ref.business_day_shifts.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid BusinessDayShiftEnum member.
    """
    require_init()
    return cache().enum_ids[BusinessDayShiftEnum][member]


def business_day_shift_member(shift_id):
    """Return the BusinessDayShiftEnum member an id names, or ``None``.

    :func:`business_day_shift_id`'s INVERSE, and the only inverse this module
    carries.  It is here rather than in a domain module because this
    vocabulary is the one two arcs share (``pay_calendar:R-PC47``): the pay
    schedule's payday convention and a recurrence rule's cash-date shift are
    one question of one table, so a second implementation of "which member is
    this id" would be the drift that ruling exists to prevent.  The recurrence
    axes that are NOT shared keep their own lookups in
    ``app.services.recurrence._vocabulary``.

    A linear scan of three members rather than a stored inverse map, for
    ``modelled_unit``'s reason: the value is resolved when a schedule form is
    read or written, never per row of a grid.

    ``None`` rather than a raise, because both askers are reading something a
    user may repair -- a submitted id the form can refuse with a field error,
    and a stored id an owner can re-answer -- rather than asserting an
    invariant.

    Args:
        shift_id: A stored or submitted ``ref.business_day_shifts.id``.

    Returns:
        The matching ``BusinessDayShiftEnum`` member, or ``None`` -- either
        because the id is not a row at all, or because it is a row this
        application does not model.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    ids = cache().enum_ids[BusinessDayShiftEnum]
    for member in BusinessDayShiftEnum:
        if ids[member] == shift_id:
            return member
    return None
