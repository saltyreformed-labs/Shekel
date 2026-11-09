"""
Shekel Budget App -- which days a paycheck pays for (plan#3048, task plan#3099).

Ruling **plan#3096**: a job keeps how many days each pay period ends before
the payday payroll SCHEDULED, and a paycheck pays from the day after the
previous paycheck's period end through its own.  These cases grade the pure
producer, :mod:`app.services.pay_calendar._paid_days`, with no database:

* :class:`TestTheDevelopersJuly` -- the card's own table: the 07-02, 07-16 and
  07-30 paychecks of 2026 pay 06-13..06-26, 06-27..07-10 and 07-11..07-24, as
  the stubs print them;
* :class:`TestAHolidayPayday` -- a payday a convention moves keeps the period
  it was scheduled for, under ``prior`` (paid early) and ``next`` (paid late);
* :class:`TestTheOpeningPaycheck` -- the record's first paycheck reads its
  previous one off the plan below the record;
* :class:`TestThePeriodsTile` -- over two eras and a seam, each paycheck's
  first day is the day after the previous one's last, and each last day is
  the scheduled payday less the gap.

**No date here is read from a clock**: every date is a literal or derived from
one by explicit arithmetic.
"""

from datetime import date, timedelta
from itertools import islice, pairwise

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import (
    PaidDays,
    PayCalendar,
    days_paid_for,
    paychecks_from,
    scheduled_payday,
)
from tests._test_helpers import era_of

PRIOR, NEXT = BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT

#: The developer's rhythm: every 14 days from Thursday 2026-03-26, the day his
#: record opens.  07-02 is 03-26 + 7 fortnights.
DEVELOPER_ERA = era_of(date(2026, 3, 26), 14)

#: His periods end six days before payday (the 07-16 stub: Period End 07-10).
GAP = 6

#: 2030-11-28 is Thanksgiving, on this grid: 2030-01-10 + 23 fortnights.
THANKSGIVING = date(2030, 11, 28)


def _calendar(paydays, eras):
    """Build owner 1's calendar over *paydays* (ISO-ordered dates) and *eras*."""
    return PayCalendar.from_paydays(
        [(index + 1, day) for index, day in enumerate(paydays)], eras,
        user_id=1, history_opens_on=None,
    )


def _fortnights(first: date, count: int) -> list[date]:
    """*count* paydays every 14 days from *first*."""
    return [first + timedelta(days=14 * step) for step in range(count)]


def _paycheck(calendar, payday):
    """The calendar's paycheck paid on *payday*, saved or projected."""
    paycheck = calendar.span_containing(payday)
    assert paycheck.start_date == payday, f"{payday} is not a payday of the calendar"
    return paycheck


class TestTheDevelopersJuly:
    """The three paychecks the card's table was read from, 2026-07."""

    CALENDAR = _calendar(_fortnights(date(2026, 3, 26), 10), (DEVELOPER_ERA,))

    def test_each_pays_the_period_its_stub_prints(self):
        """06-13..06-26, 06-27..07-10, 07-11..07-24: each ends 6 days before payday.

        07-16 - 6 = 07-10, and it starts the day after 07-02 - 6 = 06-26.
        """
        assert [
            days_paid_for(self.CALENDAR, _paycheck(self.CALENDAR, payday).start_date, GAP)
            for payday in (date(2026, 7, 2), date(2026, 7, 16), date(2026, 7, 30))
        ] == [
            PaidDays(first=date(2026, 6, 13), last=date(2026, 6, 26)),
            PaidDays(first=date(2026, 6, 27), last=date(2026, 7, 10)),
            PaidDays(first=date(2026, 7, 11), last=date(2026, 7, 24)),
        ]

    def test_a_projected_paycheck_is_answered_too(self):
        """2026-09-10, past the ten saved paydays: 08-22..09-04.

        The record ends 07-30 (03-26 + 9 fortnights); 09-10 is three projected
        paychecks on.  09-10 - 6 = 09-04; 08-27 - 6 + 1 = 08-22.
        """
        paycheck = _paycheck(self.CALENDAR, date(2026, 9, 10))
        assert paycheck.period_id is None
        assert days_paid_for(self.CALENDAR, paycheck.start_date, GAP) == PaidDays(
            first=date(2026, 8, 22), last=date(2026, 9, 4),
        )

    def test_a_payday_nothing_moved_is_its_own_scheduled_payday(self):
        """07-16 is a Thursday, no holiday: scheduled and paid are one day."""
        assert scheduled_payday(self.CALENDAR, date(2026, 7, 16)) == date(2026, 7, 16)


class TestAHolidayPayday:
    """A payday a convention moves keeps the period it was scheduled for."""

    def test_paid_EARLY_it_pays_the_scheduled_period(self):
        """Thanksgiving 2030 under ``prior``: paid Wed 11-27, pays Sat 11-09 .. Fri 11-22.

        Scheduled 11-28: 11-28 - 6 = 11-22, and the day after 11-14 - 6.  Read
        off the PAID day it would be 11-08 .. 11-21, a day early.
        """
        calendar = _calendar(
            _fortnights(date(2030, 1, 10), 2), (era_of(date(2030, 1, 10), 14, PRIOR),),
        )
        paid = THANKSGIVING - timedelta(days=1)
        assert scheduled_payday(calendar, paid) == THANKSGIVING
        assert days_paid_for(calendar, _paycheck(calendar, paid).start_date, GAP) == PaidDays(
            first=date(2030, 11, 9), last=date(2030, 11, 22),
        )

    def test_the_paycheck_after_it_starts_the_day_after(self):
        """12-12 pays 11-23 .. 12-06: the holiday moved neither neighbour."""
        calendar = _calendar(
            _fortnights(date(2030, 1, 10), 2), (era_of(date(2030, 1, 10), 14, PRIOR),),
        )
        assert days_paid_for(
            calendar, _paycheck(calendar, date(2030, 12, 12)).start_date, GAP,
        ) == PaidDays(first=date(2030, 11, 23), last=date(2030, 12, 6))

    def test_paid_LATE_it_pays_the_scheduled_period(self):
        """Under ``next`` Thanksgiving is paid Fri 11-29, and still pays 11-09 .. 11-22."""
        calendar = _calendar(
            _fortnights(date(2030, 1, 10), 2), (era_of(date(2030, 1, 10), 14, NEXT),),
        )
        paid = THANKSGIVING + timedelta(days=1)
        assert scheduled_payday(calendar, paid) == THANKSGIVING
        assert days_paid_for(calendar, _paycheck(calendar, paid).start_date, GAP) == PaidDays(
            first=date(2030, 11, 9), last=date(2030, 11, 22),
        )


class TestTheOpeningPaycheck:
    """The record's first paycheck has no previous one in the calendar."""

    def test_its_previous_is_the_planned_payday_below_the_record(self):
        """03-26 opens the record: it pays 03-07 .. 03-20.

        The plan's payday below it is 03-12: 03-12 - 6 + 1 = 03-07, and
        03-26 - 6 = 03-20 -- the same fortnight every other paycheck pays.
        """
        calendar = _calendar(_fortnights(date(2026, 3, 26), 3), (DEVELOPER_ERA,))
        assert days_paid_for(
            calendar, _paycheck(calendar, date(2026, 3, 26)).start_date, GAP,
        ) == PaidDays(first=date(2026, 3, 7), last=date(2026, 3, 20))


class TestThePeriodsTile:
    """No day is paid twice, and none is left unpaid between two paychecks."""

    #: Every 14 days from Friday 2026-01-02, then every 7 from Wednesday
    #: 2026-07-01 (``test_pay_calendar_eras``' worked example): A's last
    #: paycheck is 06-05, and 07-01 replaces the 06-19 the seam deleted.
    ERAS = (era_of(date(2026, 1, 2), 14), era_of(date(2026, 7, 1), 7))
    CALENDAR = _calendar([date(2026, 1, 2), date(2026, 3, 13)], ERAS)

    def _walk(self, count):
        """The first *count* paychecks with the days each pays for."""
        return [
            (paycheck.start_date, days_paid_for(self.CALENDAR, paycheck.start_date, GAP))
            for paycheck in islice(paychecks_from(self.CALENDAR, date(2026, 1, 2)), count)
        ]

    def test_each_first_day_follows_the_previous_last_day(self):
        """Forty paychecks over the seam: each period opens the day after the last closed."""
        walked = self._walk(40)
        for (_, earlier), (payday, later) in pairwise(walked):
            assert later.first == earlier.last + timedelta(days=1), payday

    def test_each_period_ends_the_gap_before_its_payday(self):
        """With ``none`` nothing is moved, so every period ends 6 days before its payday."""
        for payday, paid in self._walk(40):
            assert paid.last == payday - timedelta(days=GAP), payday

    def test_the_seam_paycheck_pays_what_the_seam_left(self):
        """07-01 pays 05-31 .. 06-25: the 06-05 paycheck's period closed 05-30.

        The 06-19 paycheck is gone (R-PC75), so 07-01 pays the days it would
        have paid as well as its own week: 26 days.
        """
        walked = dict(self._walk(40))
        assert walked[date(2026, 6, 5)] == PaidDays(
            first=date(2026, 5, 17), last=date(2026, 5, 30),
        )
        assert walked[date(2026, 7, 1)] == PaidDays(
            first=date(2026, 5, 31), last=date(2026, 6, 25),
        )
        assert walked[date(2026, 7, 8)] == PaidDays(
            first=date(2026, 6, 26), last=date(2026, 7, 2),
        )
