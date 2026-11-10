"""A repeat's lookups: its unit, its place in its period, and how a closed day moves it.

Split out of the flat ``app/enums.py`` at its 1,000-line ceiling (see
:mod:`app.enums` for why).  Every class below stands exactly as it stood there.
"""

import enum


class RecurrenceUnitEnum(enum.Enum):
    """Recurrence cadence-unit values (recurrence redesign, step R2).

    The first of the two axes that replaced the closed pattern set's
    eight-name enum, ``RecurrencePatternEnum``, deleted with
    ``ref.recurrence_patterns`` at plan step **R9**: a recurrence is
    ``every <interval_n> <unit>``.
    Four of the old names -- Monthly, Quarterly, Semi-Annual, Annual -- were
    the same idea with a different integer baked into the NAME (every 1, 3, 6
    or 12 months), which is why "every other month" and "every two years" had
    nowhere to live.  With the interval in a column, they do.

        period -- the user's own pay-period cadence (the paycheck-space
                  family: today's Every Period / Every N Periods).
        week   -- calendar weeks; weekly and biweekly-by-date bills, which
                  the old set could not express at all.
        month  -- calendar months, month-end clamped (``interval_n`` 1 =
                  Monthly, 3 = Quarterly, 6 = Semi-Annual).
        year   -- calendar years (``interval_n`` 1 = Annual).

    Values match ``ref.recurrence_units.name``; application code resolves
    them via ``ref_cache.recurrence_unit_id`` and compares the integer ID,
    never the string ``name`` -- the project-wide IDs-for-logic invariant.
    """

    PERIOD = "period"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class PeriodPlacementEnum(enum.Enum):
    """How an occurrence date maps onto a pay period (redesign step R2).

    An occurrence is a calendar DATE; a Shekel row lives in a pay PERIOD.
    This is the rule that carries one to the other, and it is a real user
    choice rather than a derived detail: it is the axis today's Monthly and
    Monthly First patterns differ on.  (They differ on the anchor day too --
    Monthly anchors on its ``day_of_month``, Monthly First on the 1st -- so
    placement alone does not carry one to the other: Monthly on the 15th with
    the placement swapped is "the first paycheck starting on or after the
    15th", i.e. the SECOND of the month, not the first.)

        containing_date            -- the period whose date range contains
                                      the occurrence (today's Monthly,
                                      Quarterly, Semi-Annual, Annual).
        period_starting_on_or_after -- the first period whose start_date is
                                      on or after the occurrence (today's
                                      Monthly First: the first paycheck of
                                      each month).

    Well-defined for every unit rather than inert for some: under the
    ``period`` unit an occurrence date IS a period start, so both values
    resolve to the same period.

    Values match ``ref.period_placements.name``; resolved via
    ``ref_cache.period_placement_id`` and compared by integer ID.
    """

    CONTAINING_DATE = "containing_date"
    PERIOD_STARTING_ON_OR_AFTER = "period_starting_on_or_after"


class BusinessDayShiftEnum(enum.Enum):
    """Weekend/holiday adjustment for an occurrence date (redesign step R2).

    Seeded by step R2 with every rule set to ``none``; step R8 is the first
    step that lets a user choose another value, so the column turns
    behaviour ON rather than being added later.

        none  -- take the occurrence date as computed.
        prior -- move a non-business-day occurrence BACKWARD to the previous
                 business day (the usual choice for a bill: pay early rather
                 than late).
        next  -- move it FORWARD to the following business day.

    The shift applies to the CASH date only.  A bill contractually due Aug 1
    and paid on Friday July 30 because Aug 1 is a Sunday still satisfies the
    Aug 1 installment, so the due-date side of a rule is never shifted.

    Values match ``ref.business_day_shifts.name``; resolved via
    ``ref_cache.business_day_shift_id`` and compared by integer ID.
    """

    NONE = "none"
    PRIOR = "prior"
    NEXT = "next"
