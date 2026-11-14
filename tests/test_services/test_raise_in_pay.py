"""What recorded pay already holds of a raise, and whether any paycheck gets it (task plan#3169).

:meth:`~app.services.payroll_basis.PayrollBasis.raise_in_pay`, the ONE producer
the raise doors' refusal and the Raises card's label read (ruling **R-SAL95**,
on the day an entry opens since ruling **plan#3091**; the words of rulings
**plan#3374** and **plan#3375**), each rule pinned on a made-up case
(R-BAL132):

* an application is HELD by the first entry whose paycheck opens on or after
  the day it lands, and one before the first entry by that entry (R-SAL59);
* it REACHES pay when it lands after the day the entry in force opens and some
  paycheck pays a weekday of it before the entry holding it opens (ruling
  plan#3089: a paycheck pays its pay period's weekdays);
* a raise none of whose applications reaches pay is refused in the shorter
  words, naming the entry holding its last application, and on the
  Retirement page's end-year Save in that page's words, naming the year saved
  and the same entry (ruling **plan#3492**);
* a yearly raise is saved while any year still reaches pay, and labelled with
  each run of years an entry holds.

**The calendar**: every 14 days from Thursday 2027-01-14, each paycheck paying
the Saturday-to-Friday period that ends six days before it, so an entry from
02-25 opens on 02-06, from 03-11 on 02-20, from 03-25 on 03-06, and from
Thursday 2028-03-23 on Saturday 2028-03-04.  A real
:class:`~app.services.pay_calendar.PayCalendar` and a duck-typed profile, the
raise a :class:`~app.services.salary_raises.RaiseTerms` value; nothing reads a
database or a clock.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import PayCalendar
from app.services.pay_rhythm import Era, FixedDays, Rhythm
from app.services.payroll_basis import PayrollBasis
from app.services.raise_in_pay import HeldYears, RaiseInPay
from app.services.salary_raises import RaiseTerms

#: The calendar's first payday, a Thursday.
_OPENS = date(2027, 1, 14)

#: Sixty paydays reach 2029-04-19, past every case's last application asked.
_PAYDAYS = 60


@dataclass(frozen=True)
class _Entry:
    """A pay entry as the walk reads one: a payday and an amount."""

    payday: date
    amount: Decimal


@dataclass
class _Profile:
    """The profile attributes the walk reads: its pay list, its lines and its pay periods."""

    pay_entries: list
    lines: list = field(default_factory=list)
    raises: list = field(default_factory=list)
    period_end_days_before_payday: int = 6


def _basis(*paydays, gap=6):
    """A basis on the biweekly calendar with one $2,000.00 entry from each of *paydays*."""
    calendar = PayCalendar.from_paydays(
        [(step + 1, _OPENS + timedelta(days=14 * step)) for step in range(_PAYDAYS)],
        (Era(
            effective_from=_OPENS,
            rhythm=Rhythm(FixedDays(14), BusinessDayShiftEnum.NONE),
        ),),
        user_id=1, history_opens_on=None,
    )
    return PayrollBasis(
        _Profile(
            [_Entry(day, Decimal("2000.00")) for day in paydays],
            period_end_days_before_payday=gap,
        ),
        calendar,
    )


def _once(day):
    """A one-time 3% merit raise from *day*."""
    return RaiseTerms(
        effective_date=day, is_recurring=False, percentage=Decimal("0.0300"),
        flat_amount=None, terminal_year=None, raise_type_name="merit",
    )


def _yearly(day, terminal_year=None):
    """A 3% merit raise from *day*, every year on its month and day through *terminal_year*."""
    return RaiseTerms(
        effective_date=day, is_recurring=True, percentage=Decimal("0.0300"),
        flat_amount=None, terminal_year=terminal_year, raise_type_name="merit",
    )


_MAR_25 = date(2027, 3, 25)
_MAR_6 = date(2027, 3, 6)
_FEB_25 = date(2027, 2, 25)
_FEB_6 = date(2027, 2, 6)


class TestAOneTimeRaise:
    """A one-time raise against one entry and against two."""

    def test_a_raise_dated_the_day_the_entry_opens_is_refused(self):
        """03-25's entry opens 03-06, so a raise from 03-06 is in it and no paycheck gets it.

        The words are ruling plan#3374's, the shorter option, with 03-06 for
        "the day that paycheck opens" and 03-25 for "its payday".
        """
        in_pay = _basis(_MAR_25).raise_in_pay(_once(_MAR_6))

        assert in_pay == RaiseInPay(
            False, (HeldYears(2027, 2027, _MAR_6, _MAR_25),), reaches_pay=False,
        )
        assert in_pay.refusal == (
            "A raise starting by 2027-03-06 is already in your pay from "
            "2027-03-25. Start it after 2027-03-06, or fix your pay."
        )
        assert in_pay.labels == ("In your pay from 2027-03-25",)

    def test_a_raise_the_day_after_the_entry_opens_is_saved_unlabelled(self):
        """A raise from Sunday 03-07 lands after 03-06: 03-25 pays it from Monday 03-08 on."""
        in_pay = _basis(_MAR_25).raise_in_pay(_once(date(2027, 3, 7)))

        assert in_pay == RaiseInPay(False, (), reaches_pay=True)
        assert in_pay.refusal is None
        assert in_pay.labels == ()

    def test_a_raise_before_the_first_entry_is_held_by_it(self):
        """A raise from 2027-01-01, before 03-25's entry opens: the paychecks before it use it.

        Ruling R-SAL59: paychecks before the first entry are priced from it.
        """
        in_pay = _basis(_MAR_25).raise_in_pay(_once(date(2027, 1, 1)))

        assert in_pay.held == (HeldYears(2027, 2027, _MAR_6, _MAR_25),)
        assert in_pay.refusal == (
            "A raise starting by 2027-03-06 is already in your pay from "
            "2027-03-25. Start it after 2027-03-06, or fix your pay."
        )

    def test_a_raise_between_two_entries_is_saved_and_labelled(self):
        """Entries from 02-25 and 03-25: a raise from Monday 03-01 reaches 03-11, 03-25 holds it.

        03-11's paycheck pays 02-20..03-05 from 02-25's entry, so it pays
        03-01..03-05 at the raised pay; 03-25's entry opens 03-06 and holds
        it.  The decision of the 2026-11-10 claim: saved, labelled with the
        later entry's payday.
        """
        in_pay = _basis(_FEB_25, _MAR_25).raise_in_pay(_once(date(2027, 3, 1)))

        assert in_pay == RaiseInPay(
            False, (HeldYears(2027, 2027, _MAR_6, _MAR_25),), reaches_pay=True,
        )
        assert in_pay.refusal is None
        assert in_pay.labels == ("In your pay from 2027-03-25",)

    def test_a_raise_on_an_earlier_entry_opening_names_that_entry(self):
        """A raise from 02-06, the day 02-25's entry opens, is held by 02-25 and not 03-25."""
        in_pay = _basis(_FEB_25, _MAR_25).raise_in_pay(_once(_FEB_6))

        assert in_pay.held == (HeldYears(2027, 2027, _FEB_6, _FEB_25),)
        assert in_pay.refusal == (
            "A raise starting by 2027-02-06 is already in your pay from "
            "2027-02-25. Start it after 2027-02-06, or fix your pay."
        )

    def test_a_raise_on_the_later_entry_opening_is_refused(self):
        """A raise from 03-06 with both entries: 03-25's opens that day, so it reaches nothing."""
        in_pay = _basis(_FEB_25, _MAR_25).raise_in_pay(_once(_MAR_6))

        assert in_pay == RaiseInPay(
            False, (HeldYears(2027, 2027, _MAR_6, _MAR_25),), reaches_pay=False,
        )

    def test_a_raise_long_after_the_last_entry_reaches_pay(self):
        """A raise from 2030-06-01, years after the last entry opens, reaches pay.

        It reaches every paycheck from then.
        """
        in_pay = _basis(_MAR_25).raise_in_pay(_once(date(2030, 6, 1)))

        assert in_pay == RaiseInPay(False, (), reaches_pay=True)


class TestAWeekendDay:
    """A weekend day is not a day paid (ruling plan#3089).

    Periods ending five days before a Thursday payday run Sunday to Saturday:
    02-25's entry opens Sunday 02-07, 03-11's paycheck pays 02-21..03-06, and
    03-25's entry opens Sunday 03-07.
    """

    def test_a_raise_on_the_saturday_before_an_entry_opens_is_refused(self):
        """Saturday 03-06 is 03-11's last day, no weekday, and 03-25's entry holds it from 03-07.

        The walk applies it to 03-11's RATE, the pay in force on its last day,
        but no paycheck's pay is priced over a day of it, so it is refused,
        naming 03-25's entry and the day it opens.
        """
        in_pay = _basis(_FEB_25, _MAR_25, gap=5).raise_in_pay(_once(_MAR_6))

        assert in_pay.reaches_pay is False
        assert in_pay.refusal == (
            "A raise starting by 2027-03-07 is already in your pay from "
            "2027-03-25. Start it after 2027-03-07, or fix your pay."
        )

    def test_a_raise_on_the_friday_before_reaches_pay(self):
        """Friday 03-05 is a weekday 03-11 pays, so the raise is saved, labelled with 03-25."""
        in_pay = _basis(_FEB_25, _MAR_25, gap=5).raise_in_pay(_once(date(2027, 3, 5)))

        assert in_pay == RaiseInPay(
            False, (HeldYears(2027, 2027, date(2027, 3, 7), _MAR_25),), reaches_pay=True,
        )
        assert in_pay.labels == ("In your pay from 2027-03-25",)


class TestAYearlyRaise:
    """A yearly raise is saved while any year reaches pay, its held years labelled.

    Ruling plan#3375: the card names each run of years an entry holds.
    """

    def test_the_years_an_entry_holds_are_labelled_and_the_raise_saved(self):
        """3% every 03-01 from 2026: 2026 and 2027 land by 03-06, and 2028 on reaches pay."""
        in_pay = _basis(_MAR_25).raise_in_pay(_yearly(date(2026, 3, 1)))

        assert in_pay == RaiseInPay(
            True, (HeldYears(2026, 2027, _MAR_6, _MAR_25),), reaches_pay=True,
        )
        assert in_pay.refusal is None
        assert in_pay.end_year_refusal is None
        assert in_pay.labels == ("2026-2027 in your pay from 2027-03-25",)

    def test_one_held_year_is_named_alone(self):
        """3% every 03-01 from 2027: only 2027 lands by 03-06, ruling plan#3375's own shape."""
        in_pay = _basis(_MAR_25).raise_in_pay(_yearly(date(2027, 3, 1)))

        assert in_pay.labels == ("2027 in your pay from 2027-03-25",)

    def test_a_yearly_raise_every_year_of_which_is_held_is_refused(self):
        """3% every 03-01 from 2026 through 2027: both years land by 03-06, so none reaches pay."""
        in_pay = _basis(_MAR_25).raise_in_pay(_yearly(date(2026, 3, 1), terminal_year=2027))

        assert in_pay.reaches_pay is False
        assert in_pay.refusal == (
            "A raise starting by 2027-03-06 is already in your pay from "
            "2027-03-25. Start it after 2027-03-06, or fix your pay."
        )

    def test_a_year_landing_the_year_after_the_last_opening_reaches_pay(self):
        """Through 2028: 2028-03-01 lands in the year after 03-25's entry opens, and reaches pay.

        The producer asks the walk for applications through the end of the
        year after the last entry opens; a horizon ending with the opening's
        own year would miss this one and refuse a raise a paycheck gets.
        """
        in_pay = _basis(_MAR_25).raise_in_pay(_yearly(date(2026, 3, 1), terminal_year=2028))

        assert in_pay.reaches_pay is True
        assert in_pay.end_year_refusal is None
        assert in_pay.labels == ("2026-2027 in your pay from 2027-03-25",)

    def test_the_end_year_words_name_the_year_saved_and_its_entry(self):
        """The Retirement page's words (ruling plan#3492): every 03-04 from 2027 through 2028.

        Entries from 2027-03-25 (opening 03-06) and 2028-03-23 (opening
        Saturday 2028-03-04): 2027-03-04 is held by the first, and 2028-03-04,
        the day the second opens, by the second, so no year reaches pay.  The
        words name 2028, the year saved, and 2028-03-23, the entry holding the
        last application; the first entry, which holds 2027, is not named.
        """
        in_pay = _basis(_MAR_25, date(2028, 3, 23)).raise_in_pay(
            _yearly(date(2027, 3, 4), terminal_year=2028),
        )

        assert in_pay == RaiseInPay(
            True,
            (
                HeldYears(2027, 2027, _MAR_6, _MAR_25),
                HeldYears(2028, 2028, date(2028, 3, 4), date(2028, 3, 23)),
            ),
            reaches_pay=False,
        )
        assert in_pay.end_year_refusal == (
            "Every year of this raise through 2028 is already in your pay from "
            "2028-03-23, so it changes no paycheck. Remove it on the salary page instead."
        )

    def test_each_entry_holding_years_is_named(self):
        """Entries from 2027-03-25 and 2028-03-23: 2026-2027 are the first's, 2028 the second's.

        2028-03-01 lands after 2027-03-25's entry opens and 2028-03-09's
        paycheck pays 2028-02-19..03-03, its weekdays from 03-01 at the raised
        pay, before 2028-03-23's entry opens on 03-04 and holds it.
        """
        in_pay = _basis(_MAR_25, date(2028, 3, 23)).raise_in_pay(_yearly(date(2026, 3, 1)))

        assert in_pay == RaiseInPay(
            True,
            (
                HeldYears(2026, 2027, _MAR_6, _MAR_25),
                HeldYears(2028, 2028, date(2028, 3, 4), date(2028, 3, 23)),
            ),
            reaches_pay=True,
        )
        assert in_pay.labels == (
            "2026-2027 in your pay from 2027-03-25",
            "2028 in your pay from 2028-03-23",
        )
