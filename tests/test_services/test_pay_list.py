"""The pay list's walk: what a payday is priced at (plan step salary:X-av-3a).

:meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on` prices each
weekday a paycheck pays for at the pay in force that day (task plan#3100,
ruling plan#3089): from the latest pay entry whose paycheck opens on or
before the day (else the first), carried across each change of rhythm at the
yearly figure, and raised by each forecast raise landing AFTER that entry
opens, rounding every step to the cent.  On this file's calendar every
paycheck pays Saturday through the Friday six days before it, so a raise
from July 1 reaches 07-16 for 8 of its 10 weekdays and 07-02 not at all.
Each class pins one ruling on its own worked example, every figure made up;
``test_a_paycheck_pays_its_weekdays`` pins the weekday rule itself:

* **R-SAL59** ("Dated pay list + forecasts") as plan#3091 amends it: a
  recorded entry replaces the forecast raises dated by the first day its
  paycheck pays for, later raises compound on it, a Fix moves only the
  paychecks its entry covers, and paychecks before the first entry use it;
* **R-SAL60** ("Each raise rounds"): ``$1,999.00`` then +3%, +2.5%, +3% is
  ``$2,058.97``, ``$2,110.44``, ``$2,173.75``;
* **R-SAL65** ("Yearly dollars, spread"): a flat raise is dollars a year over
  the paychecks a year in force where it lands;
* **R-SAL82** ("Yearly pay carries, said"): across a change of rhythm with no
  entry recorded, the pay is the yearly pay over the new count, and the
  salary page names it; on a day a raise also lands, the change comes first;
* **R-SAL84** / **R-SAL85** and **R-SAL89** ("Yearly pay across a
  seam"), as plan#3091 amends them: the paycheck where a recorded entry
  begins badges ``PAY +/-$X`` against the pay in force the day before it
  opens -- in yearly pay across a change of rhythm -- and a replaced forecast
  raise badges nothing.

Built on a real :class:`~app.services.pay_calendar.PayCalendar` and a
duck-typed profile, with the raise set handed in as
:class:`~app.services.salary_raises.RaiseTerms` values: the walk reads no
database, so none is used.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import PayCalendar
from app.services.pay_rhythm import Era, FixedDays, Rhythm
from app.services.payroll_basis import PayrollBasis, RhythmChangeWithoutPay
from app.services.salary_cockpit_service import is_pay_cut
from app.services.salary_raises import RaiseTerms

_NONE = BusinessDayShiftEnum.NONE


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
    #: The job's pay periods end six days before payday (ruling plan#3096),
    #: as every profile the suite builds states.
    period_end_days_before_payday: int = 6


def _calendar(paydays, eras):
    """A calendar over exactly *paydays*, numbered 1.. in order."""
    return PayCalendar.from_paydays(
        [(index + 1, payday) for index, payday in enumerate(paydays)],
        eras,
        user_id=1,
        history_opens_on=None,
    )


def _era(day, cadence_days):
    """A fixed-days era from *day*."""
    return Era(effective_from=day, rhythm=Rhythm(FixedDays(cadence_days), _NONE))


def _biweekly(count=70):
    """Every 14 days from 2026-01-01: 06-18, 07-02, 07-16, 2027-07-01 and 2028-07-13 among them."""
    start = date(2026, 1, 1)
    return _calendar(
        [start + timedelta(days=14 * step) for step in range(count)],
        (_era(start, 14),),
    )


def _biweekly_then_weekly():
    """Every 14 days from 2026-01-08 through 2026-12-24, then weekly from 2027-01-07."""
    biweekly = [date(2026, 1, 8) + timedelta(days=14 * step) for step in range(26)]
    weekly = [date(2027, 1, 7) + timedelta(days=7 * step) for step in range(40)]
    return _calendar(
        biweekly + weekly,
        (_era(date(2026, 1, 8), 14), _era(date(2027, 1, 7), 7)),
    )


def _percent(year, month, pct, *, recurring=False):
    """A merit raise of *pct* (a string) landing on the 1st of *month*."""
    return RaiseTerms(
        effective_date=date(year, month, 1), is_recurring=recurring,
        percentage=Decimal(pct), flat_amount=None, terminal_year=None,
        raise_type_name="merit",
    )


def _raise_on(day):
    """A one-time 3% merit raise from *day*, any day of its month (ruling plan#3090)."""
    return RaiseTerms(
        effective_date=day, is_recurring=False, percentage=Decimal("0.03"),
        flat_amount=None, terminal_year=None, raise_type_name="merit",
    )


def _flat(year, month, dollars_a_year):
    """A one-time flat raise of *dollars_a_year* landing on the 1st of *month*."""
    return RaiseTerms(
        effective_date=date(year, month, 1), is_recurring=False,
        percentage=None, flat_amount=Decimal(dollars_a_year), terminal_year=None,
        raise_type_name="custom",
    )


def _basis(calendar, entries, raises=()):
    """A basis over *entries* (``(payday, amount string)`` pairs) and *raises*."""
    return PayrollBasis(
        _Profile([_Entry(day, Decimal(amount)) for day, amount in entries]),
        calendar,
        tuple(raises),
    )


def _paycheck(basis, payday):
    """The span *payday* opens, refusing a day that is not one of the calendar's paydays.

    The walk prices any day it is handed, so a case dated off the payday grid
    would pass while testing a paycheck that does not exist -- the first
    draft of this module did exactly that for half its cases.
    """
    span = basis.calendar.span_containing(payday)
    assert span is not None and span.start_date == payday, (
        f"{payday} is not a payday on this case's calendar"
    )
    return span


def _pay(basis, payday):
    """What *payday* pays, per paycheck."""
    _paycheck(basis, payday)
    return basis.base_pay_on(payday).per_paycheck


def _badge(basis, payday):
    """The banner of *payday*'s paycheck."""
    return basis.pay_event_on(_paycheck(basis, payday))


_EVERY_JULY = (_percent(2026, 7, "0.03", recurring=True),)


class TestADatedPayList:
    """R-SAL59's worked example on this calendar's paydays: $2,000.00 from 2026-01-01, +3% every July."""

    def test_the_forecast_alone(self):
        """No recorded change: 2,000.00; x 1.03 = 2,060.00 from 07-01; x 1.03 = 2,121.80 a year on.

        07-02 pays 06-13..06-26, before the raise: 2,000.00 (it read 2,060.00
        until ruling plan#3089).  07-16 pays 06-27..07-10, 2 weekdays before
        and 8 after: 2,000.00 x 2/10 = 400.00 and 2,060.00 x 8/10 =
        1,648.00, 2,048.00.  07-30 is wholly after: 2,060.00.  2027-07-01
        pays 2027-06-12..06-25, before that year's raise: 2,060.00 (it read
        2,121.80); 2027-07-29 is wholly after it: 2,121.80.
        """
        basis = _basis(_biweekly(), [(date(2026, 1, 1), "2000.00")], _EVERY_JULY)
        assert _pay(basis, date(2026, 6, 18)) == Decimal("2000.00")
        assert _pay(basis, date(2026, 7, 2)) == Decimal("2000.00")
        assert _pay(basis, date(2026, 7, 16)) == Decimal("2048.00")
        assert _pay(basis, date(2026, 7, 30)) == Decimal("2060.00")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("2060.00")
        assert _pay(basis, date(2027, 7, 29)) == Decimal("2121.80")

    def test_an_entry_before_a_raise_keeps_the_raise(self):
        """From 07-02: 2,070.00, which opens 06-13 -- so July 2026's 3% still lands on it.

        Ruling plan#3091: recorded pay holds only the raises dated by the
        first day its paycheck pays for, and July 1 is after 06-13.  07-16
        pays 2,070.00 x 2/10 = 414.00 and 2,070.00 x 1.03 = 2,132.10 x 8/10
        = 1,705.68: 2,119.68 (it read 2,070.00 when an entry held every raise
        through its payday, R-SAL59).  2027-07-01 pays 2027-06-12..06-25 at
        2,132.10.
        """
        basis = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 2), "2070.00")],
            _EVERY_JULY,
        )
        assert _pay(basis, date(2026, 6, 18)) == Decimal("2000.00")
        assert _pay(basis, date(2026, 7, 2)) == Decimal("2070.00")
        assert _pay(basis, date(2026, 7, 16)) == Decimal("2119.68")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("2132.10")

    def test_a_recorded_raise_replaces_the_forecast_dated_by_the_day_it_opens(self):
        """From 07-30: 2,132.10, which opens 07-11 -- July 2026's 3% is inside it.

        07-30 and 08-13 pay 2,132.10, not 2,132.10 x 1.03; 2027-07-29, wholly
        past the next July, pays 2,132.10 x 1.03 = 2,196.063 -> 2,196.06.
        """
        basis = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 30), "2132.10")],
            _EVERY_JULY,
        )
        assert _pay(basis, date(2026, 7, 16)) == Decimal("2048.00")
        assert _pay(basis, date(2026, 7, 30)) == Decimal("2132.10")
        assert _pay(basis, date(2026, 8, 13)) == Decimal("2132.10")
        assert _pay(basis, date(2027, 7, 29)) == Decimal("2196.06")

    def test_a_fix_moves_only_the_paychecks_its_entry_covers(self):
        """The first entry fixed to 2,000.04: 01-01 .. 06-18 move; 07-02 on do not."""
        basis = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.04"), (date(2026, 7, 2), "2070.00")],
            _EVERY_JULY,
        )
        assert _pay(basis, date(2026, 1, 1)) == Decimal("2000.04")
        assert _pay(basis, date(2026, 6, 18)) == Decimal("2000.04")
        assert _pay(basis, date(2026, 7, 2)) == Decimal("2070.00")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("2132.10")

    def test_paychecks_before_the_first_entry_use_it(self):
        """Entry from 03-12, which opens 02-21; a February raise lands before it and is inside it.

        01-15 (before the entry) and 03-26 (after it) both pay 2,000.00: the
        raise landing 2026-02-01 is on or before the day the entry opens, so
        the entry already holds it.  02-12 pays 01-24..02-06, across the
        raise's day but before the entry opens: the entry, raised by
        nothing, 2,000.00.
        """
        basis = _basis(
            _biweekly(), [(date(2026, 3, 12), "2000.00")],
            (_percent(2026, 2, "0.03"),),
        )
        assert _pay(basis, date(2026, 1, 15)) == Decimal("2000.00")
        assert _pay(basis, date(2026, 2, 12)) == Decimal("2000.00")
        assert _pay(basis, date(2026, 3, 26)) == Decimal("2000.00")

    def test_a_raise_landing_ON_the_day_the_entry_opens_is_inside_it(self):
        """Entry from 01-29, which opens Saturday 01-10, and 3% landing 01-10: the entry holds it.

        "On or before the first day its paycheck pays for" (ruling
        plan#3091, amending R-SAL59's "on or before its payday"): 01-29 pays
        2,000.00, and no January paycheck (01-01, 01-15, 01-29) badges the
        raise the entry replaced.  The same raise from Sunday 01-11 is after
        the entry opens and before every weekday 01-29 pays for, so 01-29
        pays 2,000.00 x 1.03 = 2,060.00: this is the case that tells "on or
        before" from "before" (an adversarial review of plan step
        salary:X-av-3a: the boundary flipped passed 1,111 tests).  It was
        stated on an entry from 01-01 with the raise ON that payday until
        ruling plan#3091 moved the boundary to the day the entry opens.
        """
        entries = [(date(2026, 1, 29), "2000.00")]
        on = _basis(_biweekly(), entries, (_raise_on(date(2026, 1, 10)),))
        after = _basis(_biweekly(), entries, (_raise_on(date(2026, 1, 11)),))
        assert _pay(on, date(2026, 1, 15)) == Decimal("2000.00")
        assert _pay(on, date(2026, 1, 29)) == Decimal("2000.00")
        assert _badge(on, date(2026, 1, 1)) == ""
        assert _badge(on, date(2026, 1, 15)) == ""
        assert _badge(on, date(2026, 1, 29)) == ""
        assert _pay(after, date(2026, 1, 29)) == Decimal("2060.00")


class TestEachRaiseRounds:
    """R-SAL60's worked example."""

    def test_three_raises_each_rounded_to_the_cent(self):
        """1,999.00 x 1.03 = 2,058.97; x 1.025 = 2,110.44(425); x 1.03 = 2,173.75(32).

        Each is the pay in force from its raise's day, and the first paycheck
        paid wholly at it pays it: 2026-07-30, 2027-07-29, 2028-07-27.  The
        first payday after each raise read the same three figures until
        ruling plan#3089; each pays the days before its raise at the old pay
        now: 2026-07-02 pays 1,999.00 (06-13..06-26), 2027-07-01 pays
        2,058.97 (2027-06-12..06-25), and 2028-07-13 pays 2028-06-24..07-07,
        five weekdays each side, 2,110.44 x 5/10 = 1,055.22 and 2,173.75 x
        5/10 = 1,086.875 -> 1,086.88: 2,142.10.
        """
        basis = _basis(
            _biweekly(), [(date(2026, 1, 1), "1999.00")],
            (
                _percent(2026, 7, "0.03"),
                _percent(2027, 7, "0.025"),
                _percent(2028, 7, "0.03"),
            ),
        )
        assert basis.pay_rate_on(date(2026, 7, 1)).per_paycheck == Decimal("2058.97")
        assert basis.pay_rate_on(date(2027, 7, 1)).per_paycheck == Decimal("2110.44")
        assert basis.pay_rate_on(date(2028, 7, 1)).per_paycheck == Decimal("2173.75")
        assert _pay(basis, date(2026, 7, 30)) == Decimal("2058.97")
        assert _pay(basis, date(2027, 7, 29)) == Decimal("2110.44")
        assert _pay(basis, date(2028, 7, 27)) == Decimal("2173.75")
        assert _pay(basis, date(2026, 7, 2)) == Decimal("1999.00")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("2058.97")
        assert _pay(basis, date(2028, 7, 13)) == Decimal("2142.10")


class TestAFlatRaiseIsDollarsAYear:
    """R-SAL65's worked example: dollars a year over the paychecks a year where it lands."""

    def test_1300_a_year_is_50_a_paycheck(self):
        """1,300.00 / 26 = 50.00 -> 2,050.00 from 07-01, which 07-30 pays.

        07-02 pays 06-13..06-26, before it: 2,000.00 (it read 2,050.00 until
        ruling plan#3089).
        """
        basis = _basis(
            _biweekly(), [(date(2026, 1, 1), "2000.00")],
            (_flat(2026, 7, "1300.00"),),
        )
        assert _pay(basis, date(2026, 7, 30)) == Decimal("2050.00")
        assert _pay(basis, date(2026, 7, 2)) == Decimal("2000.00")

    def test_1000_a_year_rounds_to_38_46(self):
        """1,000.00 / 26 = 38.4615 -> 38.46 -> 2,038.46 from 07-01, which 07-30 pays.

        07-02, before it, pays 2,000.00 (it read 2,038.46 until ruling
        plan#3089).
        """
        basis = _basis(
            _biweekly(), [(date(2026, 1, 1), "2000.00")],
            (_flat(2026, 7, "1000.00"),),
        )
        assert _pay(basis, date(2026, 7, 30)) == Decimal("2038.46")
        assert _pay(basis, date(2026, 7, 2)) == Decimal("2000.00")

    def test_the_count_is_the_one_in_force_where_it_lands(self):
        """Weekly from 2027-01-07: 2,000 carried to 1,000.00, then 1,300 / 52 = 25.00 -> 1,025.00.

        The weekly paychecks pay Saturday through Friday six days before
        payday.  2027-07-15 pays 07-03..07-09, wholly after the raise:
        1,025.00.  07-01 pays 06-19..06-25, before it: 1,000.00 (it read
        1,025.00 until ruling plan#3089).  07-08 pays 06-26..07-02, 3
        weekdays before and 2 after: 1,000.00 x 3/5 = 600.00 and 1,025.00 x
        2/5 = 410.00, 1,010.00.
        """
        basis = _basis(
            _biweekly_then_weekly(), [(date(2026, 1, 8), "2000.00")],
            (_flat(2027, 7, "1300.00"),),
        )
        assert _pay(basis, date(2027, 7, 15)) == Decimal("1025.00")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("1000.00")
        assert _pay(basis, date(2027, 7, 8)) == Decimal("1010.00")


class TestYearlyPayCarries:
    """R-SAL82's worked example: biweekly to weekly on 2027-01-07."""

    def test_the_pay_is_carried_at_the_yearly_figure(self):
        """2,060.00 x 26 / 52 = 1,030.00; the 2027-07-01 raise: 1,030 x 1.03 = 1,060.90.

        The weekly rhythm takes effect from 2026-12-19, the first day its
        first paycheck (2027-01-07) pays for.  2027-07-15 is the first
        paycheck wholly past the 2027 raise: 1,060.90.  2027-07-01 pays
        06-19..06-25, before it: 1,030.00 (it read 1,060.90 until ruling
        plan#3089).
        """
        basis = _basis(
            _biweekly_then_weekly(), [(date(2026, 1, 8), "2000.00")], _EVERY_JULY,
        )
        assert _pay(basis, date(2026, 12, 24)) == Decimal("2060.00")
        assert _pay(basis, date(2027, 1, 7)) == Decimal("1030.00")
        assert basis.base_pay_on(date(2027, 1, 7)).periods_per_year == Decimal("52")
        assert basis.pay_rate_on(date(2026, 12, 18)).periods_per_year == Decimal("26")
        assert basis.pay_rate_on(date(2026, 12, 19)).per_paycheck == Decimal("1030.00")
        assert _pay(basis, date(2027, 7, 15)) == Decimal("1060.90")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("1030.00")

    def test_the_salary_page_names_the_carry(self):
        """One change named: 2027-01-07, priced 1,030.00 from the yearly 53,560.00."""
        basis = _basis(
            _biweekly_then_weekly(), [(date(2026, 1, 8), "2000.00")], _EVERY_JULY,
        )
        assert basis.rhythm_changes_without_pay() == [
            RhythmChangeWithoutPay(
                date(2027, 1, 7), Decimal("1030.00"), Decimal("53560.00"),
            ),
        ]

    def test_pay_recorded_at_the_switch_is_not_named(self):
        """An entry on 2027-01-07 prices it: nothing is carried, nothing named."""
        basis = _basis(
            _biweekly_then_weekly(),
            [(date(2026, 1, 8), "2000.00"), (date(2027, 1, 7), "1050.00")],
            _EVERY_JULY,
        )
        assert _pay(basis, date(2027, 1, 7)) == Decimal("1050.00")
        assert basis.rhythm_changes_without_pay() == []

    def test_one_rhythm_names_nothing(self):
        """A single era carries nothing."""
        basis = _basis(_biweekly(), [(date(2026, 1, 1), "2000.00")], _EVERY_JULY)
        assert basis.rhythm_changes_without_pay() == []


class TestThePayChangeBadge:
    """R-SAL84 / R-SAL85, and yearly pay across a seam."""

    def test_a_recorded_raise_badges_its_change_and_the_replaced_forecast_badges_nothing(self):
        """From 07-30 (opens 07-11): 'PAY +$72.10'; 08-13 nothing; 2027-07-01 the forecast.

        2,132.10 against the 2,060.00 in force on 07-10, the day before the
        entry opens (ruling plan#3091: pay with pay).  The entry holds July
        2026's raise, so no later paycheck badges it.  An entry from 07-02,
        which opens 06-13, holds no July raise and the raise goes on reaching
        07-16 (``test_an_entry_before_a_raise_keeps_the_raise``), so 07-16
        badges it: 'MERIT +3.00%'.  That case badged '' while an entry held
        every raise through its payday (R-SAL59).
        """
        basis = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 30), "2132.10")],
            _EVERY_JULY,
        )
        assert _badge(basis, date(2026, 7, 30)) == "PAY +$72.10"
        assert _badge(basis, date(2026, 8, 13)) == ""
        assert _badge(basis, date(2027, 7, 1)).startswith("MERIT +")
        before_the_raise = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 2), "2070.00")],
            _EVERY_JULY,
        )
        assert _badge(before_the_raise, date(2026, 7, 2)) == "PAY +$70.00"
        assert _badge(before_the_raise, date(2026, 7, 16)) == "MERIT +3.00%"

    def test_the_forecast_badges_where_no_entry_replaces_it(self):
        """No recorded change: July 2026 badges its merit raise."""
        basis = _basis(_biweekly(), [(date(2026, 1, 1), "2000.00")], _EVERY_JULY)
        assert _badge(basis, date(2026, 7, 2)).startswith("MERIT +")

    def test_a_recorded_cut_badges_a_minus_and_reads_as_a_cut(self):
        """2,000.00 then 1,930.00 from 07-02: 'PAY -$70.00', which the screens show amber."""
        basis = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 2), "1930.00")],
        )
        badge = _badge(basis, date(2026, 7, 2))
        assert badge == "PAY -$70.00"
        assert is_pay_cut(badge)

    def test_the_first_entry_and_an_unchanged_entry_badge_nothing(self):
        """The opening payday has nothing before it; 2,000.00 re-recorded moves nothing."""
        basis = _basis(
            _biweekly(),
            [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 2), "2000.00")],
        )
        assert _badge(basis, date(2026, 1, 1)) == ""
        assert _badge(basis, date(2026, 7, 2)) == ""

    def test_across_a_seam_the_same_yearly_pay_badges_nothing(self):
        """2,060.00 biweekly then 1,030.00 weekly: 53,560.00 a year either side.

        A per-paycheck comparison announced 'PAY -$1,030.00', a cut.
        """
        basis = _basis(
            _biweekly_then_weekly(),
            [(date(2026, 1, 8), "2060.00"), (date(2027, 1, 7), "1030.00")],
        )
        assert _badge(basis, date(2027, 1, 7)) == ""

    def test_across_a_seam_a_raise_badges_in_yearly_pay(self):
        """1,050.00 weekly = 54,600.00 against 53,560.00: 'PAY +$1,040.00 a year', not a cut."""
        basis = _basis(
            _biweekly_then_weekly(),
            [(date(2026, 1, 8), "2060.00"), (date(2027, 1, 7), "1050.00")],
        )
        badge = _badge(basis, date(2027, 1, 7))
        assert badge == "PAY +$1,040.00 a year"
        assert not is_pay_cut(badge)


    def test_a_first_entry_at_a_seam_badges_no_carry_rounding(self):
        """The ONLY entry, 4,333.33 on a 12-a-year rhythm's first payday after a 26-a-year one.

        The paycheck before it is that entry carried back, 4,333.33 x 12 / 26
        = 1,999.998 -> 2,000.00, so a yearly comparison read 51,999.96
        against 52,000.00 and badged 'PAY -$0.04 a year', an amber cut no one
        took (an adversarial review of this step): the first entry has no
        earlier recorded pay to compare with.
        """
        biweekly = [date(2026, 1, 8) + timedelta(days=14 * step) for step in range(26)]
        monthly = [date(2027, 1, 7) + timedelta(days=30 * step) for step in range(20)]
        basis = _basis(
            _calendar(
                biweekly + monthly,
                (_era(date(2026, 1, 8), 14), _era(date(2027, 1, 7), 30)),
            ),
            [(date(2027, 1, 7), "4333.33")],
        )
        assert _pay(basis, date(2026, 12, 24)) == Decimal("2000.00")
        assert basis.base_pay_on(date(2027, 1, 7)).periods_per_year == Decimal("12")
        assert _badge(basis, date(2027, 1, 7)) == ""


class TestOnOneDayTheRhythmChangesFirst:
    """A change of rhythm and a raise landing on one day: the change applies first (``_walk``)."""

    def test_the_raise_compounds_on_the_carried_pay(self):
        """2,001.01 biweekly; weekly from 2027-07-01, and 3% landing 2027-06-12.

        The weekly rhythm takes effect from 2027-06-12, the first day its
        first paycheck (07-01) pays for (task plan#3100), and the raise lands
        that day too.  Rhythm first: 2,001.01 x 26 / 52 = 1,000.505 ->
        1,000.51, x 1.03 = 1,030.5253 -> 1,030.53.  The raise first would
        read 2,001.01 x 1.03 = 2,061.0403 -> 2,061.04, x 26 / 52 = 1,030.52:
        the day is paid at the new rhythm, so the raise lands on the carried
        pay.  The raise was dated 2027-07-01, the weekly era's first PAYDAY,
        until the change of rhythm moved to its first paycheck's first day;
        from 07-01 it would reach 07-01's paycheck not at all (1,000.51).
        """
        biweekly = [date(2027, 1, 14) + timedelta(days=14 * step) for step in range(12)]
        weekly = [date(2027, 7, 1) + timedelta(days=7 * step) for step in range(10)]
        basis = _basis(
            _calendar(
                biweekly + weekly,
                (_era(date(2027, 1, 14), 14), _era(date(2027, 7, 1), 7)),
            ),
            [(date(2027, 1, 14), "2001.01")],
            (_raise_on(date(2027, 6, 12)),),
        )
        assert _pay(basis, date(2027, 6, 17)) == Decimal("2001.01")
        assert _pay(basis, date(2027, 7, 1)) == Decimal("1030.53")


class TestOneBasisPricesEachPaydayItsOwnWay:
    """Each payday is walked once per basis and REMEMBERED BY ITS OWN DAY.

    A basis memoises each payday's walk; these cases price two paydays of
    one month that differ, through ONE basis, in both orders, so a memo keyed
    coarser than the day (by month, as ``applications_between`` reads a
    raise) would hand one payday the other's answer.  An adversarial review
    of this step keyed it by month and found every other case passing,
    since each puts its entries and seams on a month's first payday.
    """

    def test_two_entries_in_one_month(self):
        """2,000.00 from 01-01, 2,070.00 from 07-16: 07-02 pays 2,000.00 and 07-16 2,070.00."""
        entries = [(date(2026, 1, 1), "2000.00"), (date(2026, 7, 16), "2070.00")]
        forward = _basis(_biweekly(), entries)
        assert _pay(forward, date(2026, 7, 2)) == Decimal("2000.00")
        assert _pay(forward, date(2026, 7, 16)) == Decimal("2070.00")
        backward = _basis(_biweekly(), entries)
        assert _pay(backward, date(2026, 7, 16)) == Decimal("2070.00")
        assert _pay(backward, date(2026, 7, 2)) == Decimal("2000.00")

    def test_a_seam_in_the_middle_of_a_month(self):
        """Biweekly through 2027-01-07, weekly from 01-21: 01-07 pays 2,000.00, 01-21 1,000.00."""
        biweekly = [date(2026, 1, 8) + timedelta(days=14 * step) for step in range(27)]
        weekly = [date(2027, 1, 21) + timedelta(days=7 * step) for step in range(10)]
        calendar = _calendar(
            biweekly + weekly,
            (_era(date(2026, 1, 8), 14), _era(date(2027, 1, 21), 7)),
        )
        entries = [(date(2026, 1, 8), "2000.00")]
        forward = _basis(calendar, entries)
        assert _pay(forward, date(2027, 1, 7)) == Decimal("2000.00")
        assert _pay(forward, date(2027, 1, 21)) == Decimal("1000.00")
        backward = _basis(calendar, entries)
        assert _pay(backward, date(2027, 1, 21)) == Decimal("1000.00")
        assert _pay(backward, date(2027, 1, 7)) == Decimal("2000.00")


class TestARecordPaidEarlyBeforeASeam:
    """A payday paid a day early stands for the next era's first paycheck."""

    def test_it_is_carried_to_the_next_eras_rhythm(self):
        """Record 02-01 stands for the weekly 02-02: 2,307.69 x 26 / 52 = 1,153.845 -> 1,153.85."""
        calendar = _calendar(
            [date(2026, 1, 2), date(2026, 1, 16), date(2026, 2, 1)],
            (_era(date(2026, 1, 2), 14), _era(date(2026, 2, 2), 7)),
        )
        basis = PayrollBasis(
            _Profile([_Entry(date(2026, 1, 2), Decimal("2307.69"))]), calendar, (),
        )
        assert _pay(basis, date(2026, 1, 16)) == Decimal("2307.69")
        assert _pay(basis, date(2026, 2, 1)) == Decimal("1153.85")
        assert basis.base_pay_on(date(2026, 2, 1)).periods_per_year == Decimal("52")


class TestAProfileWithoutPayIsRefusedByName:
    """No door leaves a profile without an entry; the walk names one that has none."""

    def test_an_empty_pay_list_raises(self):
        """A ``ValueError`` naming the missing entry, not an ``IndexError``."""
        basis = PayrollBasis(_Profile([]), _biweekly(), ())
        with pytest.raises(ValueError, match="holds no pay entry"):
            basis.base_pay_on(date(2026, 1, 1))
