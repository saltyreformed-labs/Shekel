"""
Shekel Budget App -- The DATE a generated row carries (plan step R16-b-2)

One function, :func:`compute_due_date`: the date every row a generate pass
writes is stamped with.  It lived in ``recurrence_engine._plan`` from the
engine's first day until plan step R16-b-2 moved it here, and the move is
ruling **R-R69**'s (developer, 2026-09-11): a loan's forward plan prices an
occurrence NO ROW answers yet from its definition, and that estimate has to
carry the date the row WOULD carry, or the payoff moves the moment the row is
written -- the defect class the R7d and R16 arcs exist to close.  The balance
seam cannot import the engine (an import closure of 124 modules at
``e6b0c263``, measured 2026-09-11 -- the session and the write state machine
among them) for one pure function, and the engine cannot be handed the seam's
question; so the leaf moves to the tier both can reach (``CLAUDE.md`` rule
14's placement clause).  Every caller was re-pointed in the same commit; the
engine re-exports nothing, so the function has ONE import path.

**It dates a row from the OCCURRENCE the row answers since plan step
recurrence:R5-a** (rulings **R-R94** / **R-R95**, developer 2026-09-23).
Until then it was a function of the rule and the PERIOD alone and scanned the
paycheck's two endpoint months for the rule's day, so at a pay cadence where
the firing month is neither endpoint it dated the row in the wrong month, and
two occurrences seated in one paycheck of 30 days or more carried one date
between them -- plan ledger row **D18**.  Every caller already holds the
placement that seated the row, so the day the cadence names is in hand; the
rule's ``due_day_of_month`` went in the same step (ruling **R-R96**).

**It reads the rule's UNIT and PLACEMENT since plan step recurrence:R8-b**,
and no longer whether :func:`~app.services.recurrence.scheduling_day_of_month`
answers ``None``.  That answer meant "date this row from its paycheck" for
every unit but the week, whose occurrences name no day of the month and yet
are dated on themselves; the week unit was withheld from the offer set until
the reading changed.

Pure: a rule, a date and a :class:`~app.services.pay_calendar.DerivedPeriod`
in, a date out.  No Flask, no ORM query, no clock.  A PROJECTED period (one
past the saved horizon, ``period_id`` ``None``) dates a row exactly as a saved
one does, which is what lets the seam date an occurrence the schedule has not
materialised yet.
"""
from datetime import date

from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.services.recurrence._frequency import placement_member, unit_member
from app.services.recurrence._placement import place
from app.services.recurrence._row_day import date_row


def compute_due_date(
    rule: RecurrenceRule, occurrence: date, period: DerivedPeriod,
) -> date:
    """Return the date a generated row answering *occurrence* is due on.

    Ruling **R-R94**'s formula over the PLACED occurrence (plan step
    recurrence:R5-a): the occurrence itself for a calendar cadence -- weeks
    since plan step R8-b, months, years -- funded by the paycheck containing
    it, and the FUNDING paycheck's payday for every other -- every paycheck,
    every N paychecks, and a calendar cadence funded from a later paycheck
    such as ``Monthly First`` (ruling **R-R95**, the answer R-BAL22 gives a
    one-off).  The transfer engine, the transaction
    engine, the Recurring surface, the carry-forward executor and the balance
    seam's ESTIMATED loan tier all date a row through this same pure helper,
    so it is deliberately part of this package's public surface (like
    :func:`~app.services.recurrence.rule_occurrences`).

    **The dating itself lives in**
    :func:`~app.services.recurrence._row_day.date_row` **since plan step
    ``pay_calendar:C18-a``**, so the occurrence walk dates a placement
    exactly as this dates the row (ruling **R-PC86** bounds generation by
    the row's CASH day); this function reads the rule's unit and placement
    and hands them there.

    Args:
        rule: The RecurrenceRule the row is generated from.
        occurrence: The date the rule's cadence names for this row -- the
            ``occurrence`` of the placement that seated it, and the row's
            ``occurs_on``.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` that
            placement seated it in -- saved, or PROJECTED past the horizon
            (the seam's estimate of a row not yet written, plan step
            R16-b-2).  Read for its payday alone, which is DERIVED from the
            owner's payday set since pay-calendar plan step C2-f3c.

    Returns:
        The date the row is due on.

    Raises:
        RecurrenceResolutionError: When the rule names a unit or a placement
            this application does not model (``unit_member`` /
            ``placement_member``).  A plausible wrong date on a generated row
            is worse than an error.
    """
    # The dating itself is the pure leaf the occurrence walk asks too (plan
    # step pay_calendar:C18-a, ruling R-PC86): one body for "the day a row
    # carries", whichever caller holds the rule.
    return date_row(
        occurrence, period,
        unit=unit_member(rule.unit_id),
        placement=placement_member(rule.placement_id),
    )


def period_of_occurrence(
    rule: RecurrenceRule, occurrence: date, calendar: PayCalendar,
) -> DerivedPeriod | None:
    """Return the pay period *rule* places *occurrence* in, read from a rule row.

    :func:`~app.services.recurrence._placement.place` with the rule's own placement,
    for a caller holding a row rather than a walk: which paycheck is a row's own, where
    the owner may have moved it (ruling **plan#2991**,
    ``card_today.date_left_by_a_move``).

    Args:
        rule: The RecurrenceRule.
        occurrence: The date its cadence named, a row's ``occurs_on``.
        calendar: The owner's pay calendar.

    Returns:
        The pay period, or ``None`` when the saved schedule does not reach *occurrence*.
    """
    return place(occurrence, calendar, placement_member(rule.placement_id))


__all__ = ["compute_due_date", "period_of_occurrence"]
