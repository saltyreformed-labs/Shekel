"""
Shekel Budget App -- the repeat on each statement's due date, in the engine

Plan step **credit_card:CC-6**, its task plan#2673 (ruling plan#2671).  The
recurrence package's half of the card payment's dates: a unit whose
occurrences are a card's statement due dates, handed to the walk on the
resolved value (the read pass attaches them; plan#2787) and never loaded by
the pure package.  Pinned here:

* ``starts_on`` is the first day the repeat covers, as authored, and the
  first occurrence is the first due date on or after it (plan#2673's measured
  reading: storing the first due date would skip a statement after a due-day
  correction);
* the cadence's other values are fixed: every 1, funded by the paycheck
  covering the due date, no ceiling, no nominal day;
* the walk yields the card's due dates, bounded by the closing and the
  caller's window, each placed in the paycheck covering it and its row dated
  on it; without the dates it refuses rather than answering none;
* its words and its yearly count.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.card_statement import DueDateSchedule
from app.services.pay_calendar import PayCadence
from app.services.pay_rhythm import FixedDays
from app.services.recurrence import (
    Cadence,
    Closing,
    EndsAfterOccurrences,
    EndsOnDate,
    RecurrenceGenerationError,
    RecurrenceResolutionError,
    RecurrenceSpec,
    ResolvedRecurrence,
    can_repeat_within_month,
    describe,
    has_day_of_month_coordinate,
    occurrence_placements,
    occurrences,
    resolve,
)
from tests.test_services.test_recurrence_resolution import build_calendar

_USER_ID = 1

#: A biweekly calendar paid from Fri Nov 20 2026: paydays Dec 4, Dec 18, Jan 1,
#: Jan 15, Jan 29, ... -- the Dec 4 paycheck covers Dec 4-17.
_CALENDAR = build_calendar(
    first_payday=date(2026, 11, 20), cadence_days=14, count=30,
    user_id=_USER_ID,
)

#: A card closing on the 20th, due on the 15th: due Dec 15, Jan 15, Feb 15 ...
_SCHEDULE = DueDateSchedule.of([SimpleNamespace(
    effective_date=date(2026, 1, 1), statement_close_day=20,
    payment_due_day=15,
)])

#: The day the paycheck in which the payment is set up began (plan#2669: a
#: card payment starts in the current paycheck).
_PAYCHECK_START = date(2026, 12, 4)


def _spec(**overrides):
    """A due-date repeat as a form would state it, starting Dec 4."""
    fields = {
        "user_id": _USER_ID,
        "unit": RecurrenceUnitEnum.DUE_DATE,
        "starts_on": _PAYCHECK_START,
    }
    fields.update(overrides)
    return RecurrenceSpec(**fields)


def _resolved_with_dates(**overrides):
    """The spec resolved and its card's schedule attached, as the read pass will."""
    resolved = resolve(_spec(**overrides), _CALENDAR)
    return ResolvedRecurrence(**{**resolved.__dict__, "due_dates": _SCHEDULE})


class TestTheRepeatResolves:
    """What ``resolve`` makes of a due-date repeat."""

    def test_starts_on_is_the_first_day_covered_as_authored(self):
        """Dec 4 stays Dec 4: not normalised onto a due date or a payday.

        The first due date is a day the card's terms decide and can move, so
        it is worked out on every walk and never stored (plan#2673).
        """
        resolved = resolve(_spec(), _CALENDAR)

        assert resolved.unit is RecurrenceUnitEnum.DUE_DATE
        assert resolved.starts_on == _PAYCHECK_START
        assert resolved.interval_n == 1
        assert resolved.placement is PeriodPlacementEnum.CONTAINING_DATE
        assert resolved.due_dates is None

    @pytest.mark.parametrize("interval_n", [2, 3])
    def test_every_other_statement_is_refused(self, interval_n):
        """Each due date is a deadline: skipping one leaves a statement unpaid."""
        with pytest.raises(RecurrenceResolutionError, match="deadline"):
            resolve(_spec(interval_n=interval_n), _CALENDAR)

    def test_funding_from_a_later_paycheck_is_refused(self):
        """The first paycheck on or after the due date pays it late."""
        with pytest.raises(RecurrenceResolutionError, match="deadline"):
            resolve(
                _spec(placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER),
                _CALENDAR,
            )

    def test_a_per_month_ceiling_is_refused(self):
        """Two due dates in a month come only from a transitional cycle."""
        with pytest.raises(RecurrenceResolutionError, match="max_per_month"):
            _spec(max_per_month=1)

    def test_a_nominal_day_is_refused(self):
        """The day is the card's, so the rule names none."""
        with pytest.raises(RecurrenceResolutionError):
            _spec(starts_on=date(2026, 4, 30), nominal_day=31)

    def test_due_dates_on_another_unit_are_refused(self):
        """A card's dates beside a monthly rule are dates no reader asks."""
        monthly = resolve(
            RecurrenceSpec(
                user_id=_USER_ID, unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 12, 15),
            ),
            _CALENDAR,
        )

        with pytest.raises(RecurrenceResolutionError, match="due dates"):
            ResolvedRecurrence(**{**monthly.__dict__, "due_dates": _SCHEDULE})


class TestTheWalk:
    """The due dates the walk yields, and where each lands."""

    def test_it_yields_each_due_date_on_or_after_the_start(self):
        """From Dec 4: Dec 15, Jan 15, Feb 15, Mar 15 through Mar 31."""
        assert list(occurrences(
            _resolved_with_dates(), _CALENDAR, through=date(2027, 3, 31),
        )) == [
            date(2026, 12, 15), date(2027, 1, 15), date(2027, 2, 15),
            date(2027, 3, 15),
        ]

    def test_nothing_before_the_start_is_generated(self):
        """From Dec 16 the Dec 15 statement is the owner's past: Jan 15 first."""
        assert next(occurrences(
            _resolved_with_dates(starts_on=date(2026, 12, 16)), _CALENDAR,
            through=date(2027, 3, 31),
        )) == date(2027, 1, 15)

    def test_an_authored_end_date_stops_it(self):
        """Ends Feb 1: Dec 15 and Jan 15 only."""
        assert list(occurrences(
            _resolved_with_dates(end_bound=EndsOnDate(date(2027, 2, 1))),
            _CALENDAR, through=date(2027, 6, 30),
        )) == [date(2026, 12, 15), date(2027, 1, 15)]

    def test_a_count_counts_due_dates(self):
        """Three occurrences: Dec 15, Jan 15, Feb 15."""
        resolved = _resolved_with_dates()
        resolved = ResolvedRecurrence(**{
            **resolved.__dict__,
            "closing": Closing(authored=EndsAfterOccurrences(3)),
        })

        assert list(occurrences(
            resolved, _CALENDAR, through=date(2027, 6, 30),
        )) == [date(2026, 12, 15), date(2027, 1, 15), date(2027, 2, 15)]

    def test_each_is_placed_in_the_paycheck_covering_it_and_due_on_it(self):
        """Dec 15 is in the Dec 4 paycheck; Jan 15 is the Jan 15 payday's own.

        The row a placement generates is dated on the due date
        (``ResolvedRecurrence.row_date``), not on its paycheck's payday.
        """
        resolved = _resolved_with_dates()

        placements = occurrence_placements(
            resolved, _CALENDAR, through=date(2027, 1, 31),
        )

        assert [
            (p.occurrence, p.period.start_date,
             resolved.row_date(p.occurrence, p.period))
            for p in placements
        ] == [
            (date(2026, 12, 15), date(2026, 12, 4), date(2026, 12, 15)),
            (date(2027, 1, 15), date(2027, 1, 15), date(2027, 1, 15)),
        ]

    def test_without_the_cards_dates_the_walk_refuses(self):
        """No dates is a refusal, never a payment that never falls due.

        Refused when the walk is asked for, not at its first ``next()``, so
        a caller passing the iterator on cannot meet it somewhere else.
        """
        bare = resolve(_spec(), _CALENDAR)

        with pytest.raises(RecurrenceGenerationError, match="due dates"):
            occurrences(bare, _CALENDAR, through=date(2027, 3, 31))


class TestItsWordsAndCount:
    """How the repeat reads and how often it falls."""

    def test_it_reads_on_each_statements_due_date(self):
        """The cell says the ruling's words, with no day: the day is the card's."""
        assert describe(_resolved_with_dates()).cadence == (
            "On each statement's due date"
        )

    def test_its_stop_reads_like_any_other(self):
        """An authored end date is worded as every repeat's is."""
        described = describe(
            _resolved_with_dates(end_bound=EndsOnDate(date(2027, 2, 1))),
        )

        assert described.stops == "until Feb 01, 2027"

    def test_it_falls_twelve_times_a_year(self):
        """One statement a month: $250.00 each is $250.00 a month.

        ``250.00 * 12 / (1 * 12) = 250.00``.
        """
        cadence = Cadence(interval_n=1, unit=RecurrenceUnitEnum.DUE_DATE)
        biweekly = PayCadence(FixedDays(14))

        assert cadence.occurrences_per_year(biweekly) == Decimal("12")
        assert cadence.monthly_equivalent(Decimal("250.00"), biweekly) == (
            Decimal("250.00")
        )

    def test_it_names_no_day_of_the_month_and_takes_no_ceiling(self):
        """Neither a nominal day nor a per-month ceiling applies."""
        assert has_day_of_month_coordinate(RecurrenceUnitEnum.DUE_DATE) is False
        assert can_repeat_within_month(RecurrenceUnitEnum.DUE_DATE) is False
