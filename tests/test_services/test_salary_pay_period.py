"""
Shekel Budget App -- a job's pay periods as its form asks and shows them.

Plan step salary plan#3048 (task plan#3099), ruling **plan#3096**: the owner
copies one stub's Pay Date and Period End, and the job keeps how many days the
period ends before the payday payroll SCHEDULED.  These cases grade
:mod:`app.services.salary_pay_period` with no database:

* :class:`TestTwoDatesBecomeTheGap` -- the developer's 07-16 stub gives 6, and
  so does a stub of a payday paid early for a holiday, where counting from
  the paid day would give 5; a payday paid early by more days than the gap
  prints a Period End after its Pay Date, and its stub is taken (task
  plan#3124);
* :class:`TestTheRefusals` -- each refusal, pinned whole, both wordings of a
  Period End after the scheduled payday, and the range's two ends;
* :class:`TestTheSettingShownBack` -- the latest payday and its period end,
  which re-save to the same gap, an early-paid one after its Pay Date
  included, and the next paychecks with their days.

**No date here is read from a clock.**
"""

from datetime import date, timedelta

import pytest

from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.services.pay_calendar import PaidDays, PayCalendar
from app.services.salary_pay_period import (
    UpcomingPaycheck,
    days_before_payday,
    pay_period_view,
)
from tests._test_helpers import era_of

#: The developer's rhythm, every 14 days from Thursday 2026-03-26.
DEVELOPER_ERA = era_of(date(2026, 3, 26), 14)

#: Thanksgiving 2030, a scheduled Thursday on this grid.
THANKSGIVING = date(2030, 11, 28)


def _calendar(first: date, count: int, era) -> PayCalendar:
    """Owner 1's calendar: *count* fortnightly paydays from *first*, on *era*."""
    return PayCalendar.from_paydays(
        [(step + 1, first + timedelta(days=14 * step)) for step in range(count)],
        (era,), user_id=1, history_opens_on=None,
    )


DEVELOPER = _calendar(date(2026, 3, 26), 10, DEVELOPER_ERA)


def _thanksgiving(shift) -> PayCalendar:
    """A fortnightly Thursday rhythm through Thanksgiving 2030 under *shift*."""
    return _calendar(date(2030, 1, 10), 2, era_of(date(2030, 1, 10), 14, shift))


class TestTwoDatesBecomeTheGap:
    """The stub's two dates, through the scheduled payday, become one count."""

    def test_the_developers_stub_gives_six(self):
        """Pay Date 07-16, a scheduled Thursday, Period End 07-10: 16 - 10 = 6."""
        assert days_before_payday(DEVELOPER, date(2026, 7, 16), date(2026, 7, 10)) == 6

    def test_a_stub_paid_early_for_a_holiday_gives_the_same_gap(self):
        """Paid Wed 11-27 for Thanksgiving, Period End Fri 11-22: 28 - 22 = 6, not 27 - 22 = 5."""
        assert days_before_payday(
            _thanksgiving(BusinessDayShiftEnum.PRIOR),
            THANKSGIVING - timedelta(days=1), date(2030, 11, 22),
        ) == 6

    def test_a_period_end_after_a_pay_date_paid_early_is_taken(self):
        """Paid Wed 11-27 for Thanksgiving, Period End Thu 11-28: 28 - 28 = 0.

        A job whose periods end on their scheduled payday, paid a day early:
        its stub prints a Period End after the Pay Date, and the gap it states
        is the range's floor (task plan#3124).
        """
        assert days_before_payday(
            _thanksgiving(BusinessDayShiftEnum.PRIOR),
            THANKSGIVING - timedelta(days=1), THANKSGIVING,
        ) == 0

    def test_a_period_ending_on_its_payday_is_zero(self):
        """Period End on the Pay Date is the range's floor."""
        assert days_before_payday(DEVELOPER, date(2026, 7, 16), date(2026, 7, 16)) == 0

    def test_a_year_before_is_the_ceiling(self):
        """2025-07-16 to 2026-07-16 is 365 days, the most a period may end before."""
        assert days_before_payday(DEVELOPER, date(2026, 7, 16), date(2025, 7, 16)) == 365


class TestTheRefusals:
    """Each refusal in the form's own words, nothing stored."""

    @pytest.mark.parametrize("pay_date, period_end", [
        (date(2026, 7, 16), None), (None, date(2026, 7, 10)),
    ])
    def test_one_date_without_the_other(self, pay_date, period_end):
        """Either date alone states no gap."""
        with pytest.raises(ValidationError) as refused:
            days_before_payday(DEVELOPER, pay_date, period_end)
        assert str(refused.value) == (
            "Copy both the Pay Date and the Period End from one pay stub."
        )

    def test_a_stub_from_before_the_pay_record(self):
        """03-12, on the grid a fortnight before the 03-26 opening: no paycheck the app holds."""
        with pytest.raises(ValidationError) as refused:
            days_before_payday(DEVELOPER, date(2026, 3, 12), date(2026, 3, 6))
        assert str(refused.value) == (
            "The app holds no paycheck on 2026-03-12: your pay record starts 2026-03-26."
        )

    def test_a_calendar_with_no_payday(self):
        """An owner with no payday has no paycheck to copy a stub of: refused in words."""
        empty = PayCalendar.from_paydays(
            [], (DEVELOPER_ERA,), user_id=1, history_opens_on=None,
        )
        with pytest.raises(ValidationError) as refused:
            days_before_payday(empty, date(2026, 7, 16), date(2026, 7, 10))
        assert str(refused.value) == "2026-07-16 is not one of your paydays."

    def test_a_day_that_is_not_a_payday(self):
        """07-17 is the day after a payday: the salary doors' one rule and its words."""
        with pytest.raises(ValidationError) as refused:
            days_before_payday(DEVELOPER, date(2026, 7, 17), date(2026, 7, 10))
        assert str(refused.value) == "2026-07-17 is not one of your paydays."

    def test_a_period_end_after_the_pay_date(self):
        """Period End 07-17 after Pay Date 07-16, a payday nothing moved: the unmoved wording.

        07-16 is its own scheduled payday, so 07-17 is after it: a gap of
        minus one, a period scheduled to be paid before it ends.
        """
        with pytest.raises(ValidationError) as refused:
            days_before_payday(DEVELOPER, date(2026, 7, 16), date(2026, 7, 17))
        assert str(refused.value) == (
            "Period End 2026-07-17 is after the Pay Date 2026-07-16.  Copy both "
            "dates from one pay stub: a paycheck pays for days that have ended "
            "by its payday."
        )

    def test_a_period_end_after_the_payday_a_late_payment_was_scheduled_for(self):
        """Under ``next`` Thanksgiving is paid Fri 11-29; a Period End of 11-29 is after 11-28.

        On the Pay Date, not after it, and still a gap of minus one from the
        scheduled payday: the moved wording names that day.
        """
        paid = THANKSGIVING + timedelta(days=1)
        with pytest.raises(ValidationError) as refused:
            days_before_payday(_thanksgiving(BusinessDayShiftEnum.NEXT), paid, paid)
        assert str(refused.value) == (
            "Period End 2030-11-29 is after 2030-11-28, the day your 2030-11-29 "
            "paycheck was scheduled for before it moved to a business day.  Copy "
            "both dates from one pay stub."
        )

    def test_a_period_end_after_the_payday_an_early_payment_was_scheduled_for(self):
        """Under ``prior`` Thanksgiving is paid Wed 11-27; Period End Fri 11-29 is after 11-28.

        After the Pay Date by two days where the move was one: a gap of minus
        one from the scheduled payday, refused in the moved wording.
        """
        with pytest.raises(ValidationError) as refused:
            days_before_payday(
                _thanksgiving(BusinessDayShiftEnum.PRIOR),
                THANKSGIVING - timedelta(days=1), THANKSGIVING + timedelta(days=1),
            )
        assert str(refused.value) == (
            "Period End 2030-11-29 is after 2030-11-28, the day your 2030-11-27 "
            "paycheck was scheduled for before it moved to a business day.  Copy "
            "both dates from one pay stub."
        )

    def test_more_than_a_year_before(self):
        """Period End 2025-07-10 for Pay Date 2026-07-16 is 371 days: a mistyped year."""
        with pytest.raises(ValidationError) as refused:
            days_before_payday(DEVELOPER, date(2026, 7, 16), date(2025, 7, 10))
        assert str(refused.value) == (
            "Period End 2025-07-10 is 371 days before the 2026-07-16 payday, and "
            "a pay period ends at most 365 days before it.  Check the year on "
            "both dates."
        )

    def test_one_day_past_the_ceiling(self):
        """366 days is refused where 365 was taken."""
        with pytest.raises(ValidationError):
            days_before_payday(DEVELOPER, date(2026, 7, 16), date(2025, 7, 15))


class TestTheSettingShownBack:
    """The edit form shows the gap as dates, and the next paychecks' days."""

    def test_between_paydays(self):
        """On 07-20: the 07-16 payday and its 07-10 end, then 07-30, 08-13, 08-27.

        07-30 pays 07-11 .. 07-24 (07-30 - 6, and the day after 07-16 - 6);
        each next one a fortnight on.
        """
        view = pay_period_view(DEVELOPER, 6, date(2026, 7, 20))
        assert (view.pay_date, view.period_end) == (date(2026, 7, 16), date(2026, 7, 10))
        assert view.upcoming == (
            UpcomingPaycheck(date(2026, 7, 30), PaidDays(date(2026, 7, 11), date(2026, 7, 24))),
            UpcomingPaycheck(date(2026, 8, 13), PaidDays(date(2026, 7, 25), date(2026, 8, 7))),
            UpcomingPaycheck(date(2026, 8, 27), PaidDays(date(2026, 8, 8), date(2026, 8, 21))),
        )

    def test_on_a_payday_that_payday_is_both_the_latest_and_the_next(self):
        """On 07-30 the latest payday is 07-30, and the list opens on it."""
        view = pay_period_view(DEVELOPER, 6, date(2026, 7, 30))
        assert view.pay_date == date(2026, 7, 30)
        assert [paycheck.payday for paycheck in view.upcoming] == [
            date(2026, 7, 30), date(2026, 8, 13), date(2026, 8, 27),
        ]

    def test_before_the_record_it_shows_the_first_payday(self):
        """On 03-01, before the 03-26 opening: 03-26 and its 03-20 end."""
        view = pay_period_view(DEVELOPER, 6, date(2026, 3, 1))
        assert (view.pay_date, view.period_end) == (date(2026, 3, 26), date(2026, 3, 20))

    def test_a_holiday_payday_shown_back_re_saves_to_the_same_gap(self):
        """On 12-01-2030 the latest payday is the Wed 11-27 holiday one: 11-27 and 11-22 give 6."""
        calendar = _thanksgiving(BusinessDayShiftEnum.PRIOR)
        view = pay_period_view(calendar, 6, date(2030, 12, 1))
        assert (view.pay_date, view.period_end) == (date(2030, 11, 27), date(2030, 11, 22))
        assert days_before_payday(calendar, view.pay_date, view.period_end) == 6

    def test_a_gap_smaller_than_an_early_move_re_saves_to_the_same_gap(self):
        """On 12-01-2030 at a gap of 0: Wed 11-27 and Thu 11-28, a Period End after its Pay Date.

        11-28 - 0 = 11-28, the scheduled Thursday the holiday paid a day
        early; the pair re-saves to 0, where the form once refused it and
        with it every save of the job until the next payday (task plan#3124).
        """
        calendar = _thanksgiving(BusinessDayShiftEnum.PRIOR)
        view = pay_period_view(calendar, 0, date(2030, 12, 1))
        assert (view.pay_date, view.period_end) == (date(2030, 11, 27), date(2030, 11, 28))
        assert days_before_payday(calendar, view.pay_date, view.period_end) == 0

    def test_a_calendar_with_no_payday_shows_nothing(self):
        """No payday, no dates to show and no paycheck to list."""
        empty = PayCalendar.from_paydays(
            [], (DEVELOPER_ERA,), user_id=1, history_opens_on=None,
        )
        view = pay_period_view(empty, 6, date(2026, 7, 20))
        assert (view.pay_date, view.period_end, view.upcoming) == (None, None, ())
