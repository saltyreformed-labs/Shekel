"""A paycheck pays its pay period's weekdays, each at the pay in force that day (task plan#3100).

:meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on` under the rulings
plan#3048 owns, each pinned on a worked example, every figure made up
(R-BAL132):

* **plan#3089** -- a paycheck pays its pay period's weekdays (Monday to
  Friday, a holiday included), each at the pay in force that day; each pay's
  share of the weekdays rounds to the cent as a stub line does;
* **plan#3090** -- a raise starts on its own date, so the weekday it starts on
  moves the money;
* **plan#3091** -- recorded pay holds only the raises dated by the first day
  its paycheck pays for, and its banner compares pay with pay;
* **plan#3092** -- a raise's banner shows on the first paycheck it reaches,
  the one whose pay period holds its day, and nowhere else (task plan#3101);
* **plan#3096** -- the job's pay periods end a fixed number of days before
  payday, here six;
* and the task's own two rules: a change of rhythm takes effect from the first
  day its first paycheck pays for, and every yearly figure reads the pay RATE,
  the pay in force on the last day a paycheck pays for.

**The calendar**: every 14 days from Thursday 2027-01-14, so each paycheck pays
Saturday through the Friday six days before it -- 02-25 pays 02-06..02-19,
03-11 pays 02-20..03-05, 03-25 pays 03-06..03-19 and 07-15 pays
06-26..07-09.  Built on a real :class:`~app.services.pay_calendar.PayCalendar`
and a duck-typed profile, with the raise set handed in as
:class:`~app.services.salary_raises.RaiseTerms` values; the walk reads no
database.  **No date here is read from a clock.**
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import PayCalendar
from app.services.pay_list_service import pay_rows
from app.services.pay_rhythm import Era, FixedDays, Rhythm
from app.services.payroll_basis import PayrollBasis, RhythmChangeWithoutPay
from app.services.salary_raises import RaiseTerms

_NONE = BusinessDayShiftEnum.NONE

#: The calendar's first payday, a Thursday.
_OPENS = date(2027, 1, 14)


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


def _era(day, cadence_days):
    """A fixed-days era from *day*."""
    return Era(effective_from=day, rhythm=Rhythm(FixedDays(cadence_days), _NONE))


def _calendar(paydays, eras):
    """A calendar over exactly *paydays*, numbered 1.. in order."""
    return PayCalendar.from_paydays(
        [(index + 1, payday) for index, payday in enumerate(paydays)],
        eras, user_id=1, history_opens_on=None,
    )


def _biweekly(count=30):
    """*count* paydays every 14 days from Thursday 2027-01-14 (thirty reach 2028-02-24)."""
    return _calendar(
        [_OPENS + timedelta(days=14 * step) for step in range(count)], (_era(_OPENS, 14),),
    )


#: Sixty paydays reach 2029-04-19, past a July raise's second year.
_TWO_YEARS = 60


def _biweekly_then_weekly():
    """Every 14 days from 2027-01-14 through 03-25, then every 7 from Thursday 04-08.

    The weekly era's first payday, 04-08, is the biweekly grid's own next
    payday, which it replaces (R-PC75), so the biweekly era's last paycheck is
    03-25, paying 03-06..03-19, and the weekly era's first pays 03-20..04-02.
    """
    biweekly = [_OPENS + timedelta(days=14 * step) for step in range(6)]
    weekly = [date(2027, 4, 8) + timedelta(days=7 * step) for step in range(10)]
    return _calendar(biweekly + weekly, (_era(_OPENS, 14), _era(date(2027, 4, 8), 7)))


def _percent(day, pct):
    """A one-time merit raise of *pct* (a string) from *day*."""
    return RaiseTerms(
        effective_date=day, is_recurring=False, percentage=Decimal(pct),
        flat_amount=None, terminal_year=None, raise_type_name="merit",
    )


def _recurring(day, pct, terminal_year=None):
    """A merit raise of *pct* from *day*, every year on its month and day through *terminal_year*."""
    return RaiseTerms(
        effective_date=day, is_recurring=True, percentage=Decimal(pct),
        flat_amount=None, terminal_year=terminal_year, raise_type_name="merit",
    )


def _flat(day, yearly):
    """A one-time cola raise of *yearly* dollars a year (a string) from *day*."""
    return RaiseTerms(
        effective_date=day, is_recurring=False, percentage=None,
        flat_amount=Decimal(yearly), terminal_year=None, raise_type_name="cola",
    )


def _basis(calendar, entries, raises=(), gap=6):
    """A basis over *entries* (``(payday, amount string)`` pairs), *raises* and *gap*."""
    return PayrollBasis(
        _Profile(
            [_Entry(day, Decimal(amount)) for day, amount in entries],
            period_end_days_before_payday=gap,
        ),
        calendar,
        tuple(raises),
    )


def _paycheck(basis, payday):
    """The span *payday* opens, refusing a day that is not one of the calendar's paydays."""
    span = basis.calendar.span_containing(payday)
    assert span is not None and span.start_date == payday, (
        f"{payday} is not a payday on this case's calendar"
    )
    return span


def _pay(basis, payday):
    """What *payday*'s paycheck pays, its base."""
    _paycheck(basis, payday)
    return basis.base_pay_on(payday).per_paycheck


def _badge(basis, payday):
    """The banner of *payday*'s paycheck."""
    return basis.pay_event_on(_paycheck(basis, payday))


#: 5% from Wednesday 2027-03-03, inside the 03-11 paycheck's 02-20..03-05.
_WEDNESDAY_RAISE = (_percent(date(2027, 3, 3), "0.05"),)


class TestAStraddle:
    """A paycheck whose pay period a raise starts inside (plan#3089, plan#3090)."""

    def test_each_weekday_is_paid_at_its_pay(self):
        """$2,000.00, 5% from Wednesday 03-03: 03-11 pays $2,030.00.

        03-11 pays 02-20..03-05, ten weekdays: 02-22..02-26 and 03-01, 03-02
        before the raise (7), 03-03..03-05 after it (3).
        2,000.00 x 7/10 = 1,400.00; 2,100.00 x 3/10 = 630.00; 2,030.00.
        02-25 is wholly before the raise and 03-25 wholly after it.
        """
        basis = _basis(_biweekly(), [(_OPENS, "2000.00")], _WEDNESDAY_RAISE)
        assert _pay(basis, date(2027, 2, 25)) == Decimal("2000.00")
        assert _pay(basis, date(2027, 3, 11)) == Decimal("2030.00")
        assert _pay(basis, date(2027, 3, 25)) == Decimal("2100.00")

    def test_its_rate_is_the_pay_in_force_on_its_last_day(self):
        """03-11's rate is $2,100.00, the pay on 03-05, and its yearly figure $54,600.00.

        2,100.00 x 26 = 54,600.00, never its base times the count:
        2,030.00 x 26 = 52,780.00 is no one's salary.
        """
        base = _basis(
            _biweekly(), [(_OPENS, "2000.00")], _WEDNESDAY_RAISE,
        ).base_pay_on(date(2027, 3, 11))
        assert base.per_paycheck == Decimal("2030.00")
        assert base.rate.per_paycheck == Decimal("2100.00")
        assert base.periods_per_year == Decimal("26")
        assert base.annual == Decimal("54600.00")

    def test_the_weekday_a_raise_starts_on_moves_the_money(self):
        """From Monday 03-01 instead: 5 weekdays each side, $2,050.00, $20.00 more.

        2,000.00 x 5/10 = 1,000.00; 2,100.00 x 5/10 = 1,050.00.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00")], (_percent(date(2027, 3, 1), "0.05"),),
        )
        assert _pay(basis, date(2027, 3, 11)) == Decimal("2050.00")

    def test_each_share_rounds_to_the_cent_like_a_stub_line(self):
        """$2,000.01, 5% from Monday 03-01: each half rounds up, $2,050.02.

        2,000.01 x 1.05 = 2,100.0105 -> 2,100.01 (R-SAL60).  2,000.01 x 5/10
        = 1,000.005 -> 1,000.01 and 2,100.01 x 5/10 = 1,050.005 -> 1,050.01,
        summed 2,050.02; the unrounded blend, 2,050.01, is not what a stub
        prints.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.01")], (_percent(date(2027, 3, 1), "0.05"),),
        )
        assert _pay(basis, date(2027, 3, 11)) == Decimal("2050.02")

    def test_a_holiday_is_a_weekday(self):
        """10% from Thursday 07-01; 07-15 pays 06-26..07-09, holding Monday 07-05's holiday.

        Ten weekdays, the holiday among them: 06-28..06-30 before the raise
        (3), 07-01, 07-02 and 07-05..07-09 after it (7).
        2,000.00 x 3/10 = 600.00; 2,200.00 x 7/10 = 1,540.00; 2,140.00.
        Leaving the holiday out would share nine: 2,000.00 x 3/9 = 666.67 and
        2,200.00 x 6/9 = 1,466.67, 2,133.34.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00")], (_percent(date(2027, 7, 1), "0.10"),),
        )
        assert _pay(basis, date(2027, 7, 15)) == Decimal("2140.00")


class TestRecordedPayOpensOnItsPaychecksFirstDay:
    """An entry is the pay in force from the first day its paycheck pays for (plan#3091)."""

    def test_an_entry_from_the_last_paycheck_before_a_raise_keeps_the_raise(self):
        """$2,050.00 from 02-25, which opens 02-06: the 03-03 raise still reaches 03-11 and on.

        03-11: 2,050.00 x 7/10 = 1,435.00; 2,050.00 x 1.05 = 2,152.50, x 3/10
        = 645.75; 2,080.75.  03-25: 2,152.50.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00"), (date(2027, 2, 25), "2050.00")],
            _WEDNESDAY_RAISE,
        )
        assert _pay(basis, date(2027, 2, 25)) == Decimal("2050.00")
        assert _pay(basis, date(2027, 3, 11)) == Decimal("2080.75")
        assert _pay(basis, date(2027, 3, 25)) == Decimal("2152.50")

    def test_an_entry_opening_after_a_raise_holds_it(self):
        """$2,110.00 from 03-25, which opens 03-06: the 03-03 raise is inside it, applied once.

        03-11 is still the first entry's straddle, 2,030.00; 03-25 and 04-08
        pay 2,110.00, not 2,110.00 x 1.05.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00"), (date(2027, 3, 25), "2110.00")],
            _WEDNESDAY_RAISE,
        )
        assert _pay(basis, date(2027, 3, 11)) == Decimal("2030.00")
        assert _pay(basis, date(2027, 3, 25)) == Decimal("2110.00")
        assert _pay(basis, date(2027, 4, 8)) == Decimal("2110.00")

    def test_a_raise_on_the_day_an_entry_opens_is_inside_it(self):
        """On Saturday 03-06, 03-25's first day, a raise is inside the entry; on Sunday it is not.

        "Dated on or before the first day": from 03-06, 03-25 pays 2,110.00;
        from 03-07, every weekday 03-25 pays for is after it, so 2,110.00 x
        1.05 = 2,215.50.
        """
        entries = [(_OPENS, "2000.00"), (date(2027, 3, 25), "2110.00")]
        on = _basis(_biweekly(), entries, (_percent(date(2027, 3, 6), "0.05"),))
        after = _basis(_biweekly(), entries, (_percent(date(2027, 3, 7), "0.05"),))
        assert _pay(on, date(2027, 3, 25)) == Decimal("2110.00")
        assert _pay(after, date(2027, 3, 25)) == Decimal("2215.50")

    def test_a_payday_below_the_first_entry_uses_it(self):
        """The only entry, $2,100.00 from 03-25 (opens 03-06), holds the 03-03 raise.

        03-11 pays 02-20..03-05, every day before the entry opens, so it is
        priced from the entry and raised by nothing (R-SAL59): 2,100.00, not
        a share of 2,100.00 x 1.05.  01-14 the same.
        """
        basis = _basis(_biweekly(), [(date(2027, 3, 25), "2100.00")], _WEDNESDAY_RAISE)
        assert _pay(basis, _OPENS) == Decimal("2100.00")
        assert _pay(basis, date(2027, 3, 11)) == Decimal("2100.00")
        assert _pay(basis, date(2027, 3, 25)) == Decimal("2100.00")


class TestTheBannerComparesPayWithPay:
    """A recorded pay change's banner: the entry against the pay in force before it opens."""

    def test_the_raise_recorded_badges_nothing(self):
        """$2,100.00 from 03-25 against the $2,100.00 in force on 03-05: no change.

        Against the paycheck before it, 03-11's $2,030.00, it read
        'PAY +$70.00', a change of nothing.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00"), (date(2027, 3, 25), "2100.00")],
            _WEDNESDAY_RAISE,
        )
        assert _badge(basis, date(2027, 3, 25)) == ""

    def test_a_change_badges_against_the_pay_in_force(self):
        """$2,110.00 from 03-25: 'PAY +$10.00' against the $2,100.00, not +$80.00."""
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00"), (date(2027, 3, 25), "2110.00")],
            _WEDNESDAY_RAISE,
        )
        assert _badge(basis, date(2027, 3, 25)) == "PAY +$10.00"


def _badges(basis, *paydays):
    """The banner of each of *paydays*' paychecks, in order."""
    return [_badge(basis, payday) for payday in paydays]


class TestARaiseBadgesTheFirstPaycheckItReaches:
    """A raise's banner shows on the paycheck whose pay period holds its day, and nowhere else.

    Ruling plan#3092, task plan#3101.  Until then a raise badged every
    paycheck DATED in its month, a recurring one every year: the 03-03 raise
    badged 03-11 and 03-25 alike, and a raise from 03-29 badged those two and
    not 04-08, the only one of the three it reaches.
    """

    def test_it_badges_the_paycheck_its_day_falls_in(self):
        """5% from Wednesday 03-03, inside 03-11's 02-20..03-05: 03-11 alone.

        03-25, paying 03-06..03-19 wholly at the raise, is dated in March too
        and changes nothing.  The label is 0.05 x 100 = 5.00.
        """
        basis = _basis(_biweekly(), [(_OPENS, "2000.00")], _WEDNESDAY_RAISE)
        assert _badges(
            basis, date(2027, 2, 25), date(2027, 3, 11), date(2027, 3, 25),
        ) == ["", "MERIT +5.00%", ""]

    def test_on_a_pay_periods_first_day_it_badges_that_paycheck(self):
        """From Saturday 03-06, 03-25's first day: 03-25, not 03-11, which ends 03-05."""
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00")], (_percent(date(2027, 3, 6), "0.05"),),
        )
        assert _badges(basis, date(2027, 3, 11), date(2027, 3, 25)) == ["", "MERIT +5.00%"]

    def test_on_a_pay_periods_last_day_it_badges_that_paycheck(self):
        """From Friday 03-05, 03-11's last day: 03-11, one weekday at the raise, and not 03-25."""
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00")], (_percent(date(2027, 3, 5), "0.05"),),
        )
        assert _badges(basis, date(2027, 3, 11), date(2027, 3, 25)) == ["MERIT +5.00%", ""]

    def test_late_in_a_month_it_badges_a_paycheck_dated_the_next(self):
        """From Monday 03-29, inside 04-08's 03-20..04-02: April's paycheck, neither of March's."""
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00")], (_percent(date(2027, 3, 29), "0.05"),),
        )
        assert _badges(
            basis, date(2027, 3, 11), date(2027, 3, 25), date(2027, 4, 8),
        ) == ["", "", "MERIT +5.00%"]

    def test_a_recurring_raise_badges_its_first_paycheck_each_year(self):
        """3% every July 1 from 2027: 2027-07-15 and 2028-07-13, never a July 1 payday.

        2027: 07-01 pays 06-12..06-25, before the raise; 07-15 pays
        06-26..07-09, holding Thursday 07-01; 07-29 pays 07-10..07-23.
        2028: 06-29 pays 06-10..06-23; 07-13 pays 06-24..07-07, holding
        Saturday 07-01; 07-27 pays 07-08..07-21.
        """
        basis = _basis(
            _biweekly(_TWO_YEARS), [(_OPENS, "2000.00")],
            (_recurring(date(2027, 7, 1), "0.03"),),
        )
        assert _badges(
            basis, date(2027, 7, 1), date(2027, 7, 15), date(2027, 7, 29),
        ) == ["", "MERIT +3.00%", ""]
        assert _badges(
            basis, date(2028, 6, 29), date(2028, 7, 13), date(2028, 7, 27),
        ) == ["", "MERIT +3.00%", ""]

    def test_a_recurring_raise_badges_nothing_after_its_end_year(self):
        """Believed through 2027: 2027-07-15 badges it, 2028-07-13 does not.

        The pricing walk stops applying it after 2027 (``_is_believed_in``),
        so the banner, which names what the walk applied, stops with it.
        """
        basis = _basis(
            _biweekly(_TWO_YEARS), [(_OPENS, "2000.00")],
            (_recurring(date(2027, 7, 1), "0.03", terminal_year=2027),),
        )
        assert _badges(basis, date(2027, 7, 15), date(2028, 7, 13)) == ["MERIT +3.00%", ""]

    def test_a_one_time_raise_badges_once(self):
        """3% once from 2027-07-01: 2027-07-15, and nothing a year on."""
        basis = _basis(
            _biweekly(_TWO_YEARS), [(_OPENS, "2000.00")], (_percent(date(2027, 7, 1), "0.03"),),
        )
        assert _badges(basis, date(2027, 7, 15), date(2028, 7, 13)) == ["MERIT +3.00%", ""]

    def test_a_raise_its_entry_holds_badges_nowhere(self):
        """The only entry, $2,100.00 from 03-25 (opens 03-06), holds the 03-03 raise.

        03-11's pay period holds 03-03, but 03-11 is priced from the entry and
        raised by nothing (R-SAL59), so no paycheck of the thirty badges it.
        """
        basis = _basis(_biweekly(), [(date(2027, 3, 25), "2100.00")], _WEDNESDAY_RAISE)
        paydays = [_OPENS + timedelta(days=14 * step) for step in range(30)]
        assert [payday for payday in paydays if _badge(basis, payday)] == []

    def test_raises_reaching_one_paycheck_are_named_in_the_order_they_apply(self):
        """A cola from Thursday 03-04 given first, a merit from Wednesday 03-03 second: merit first.

        03-11 pays both for the first time; the merit lands a day earlier, so
        it compounds first and is named first, whatever order the set lists
        them in.
        """
        basis = _basis(
            _biweekly(), [(_OPENS, "2000.00")],
            (_flat(date(2027, 3, 4), "2600"), _percent(date(2027, 3, 3), "0.05")),
        )
        assert _badge(basis, date(2027, 3, 11)) == "MERIT +5.00%, COLA +$2,600.00"

    def test_a_paycheck_badges_exactly_where_its_rate_first_moves(self):
        """Two years of the July raise: the badged paydays are the paydays whose rate moves.

        The rate is 2,000.00 through 07-01, 2,000.00 x 1.03 = 2,060.00 from
        07-15, and 2,060.00 x 1.03 = 2,121.80 from 2028-07-13.  The banner and
        the figure beside it read one walk, so they move on the same paychecks.
        """
        basis = _basis(
            _biweekly(_TWO_YEARS), [(_OPENS, "2000.00")],
            (_recurring(date(2027, 7, 1), "0.03"),),
        )
        paydays = [_OPENS + timedelta(days=14 * step) for step in range(_TWO_YEARS)]
        moved = [
            later for earlier, later in zip(paydays, paydays[1:])
            if basis.base_pay_on(later).rate != basis.base_pay_on(earlier).rate
        ]
        badged = [payday for payday in paydays if _badge(basis, payday)]
        assert badged == moved == [date(2027, 7, 15), date(2028, 7, 13)]
        assert basis.base_pay_on(date(2028, 7, 13)).rate.per_paycheck == Decimal("2121.80")


class TestARhythmSeam:
    """A change of rhythm takes effect from the first day its first paycheck pays for."""

    def test_the_change_takes_effect_on_its_first_paychecks_first_day(self):
        """Biweekly $2,000.00 through Friday 03-19; weekly $1,000.00 from Saturday 03-20.

        2,000.00 x 26 / 52 = 1,000.00 (R-SAL82).
        """
        basis = _basis(_biweekly_then_weekly(), [(_OPENS, "2000.00")])
        before = basis.pay_rate_on(date(2027, 3, 19))
        after = basis.pay_rate_on(date(2027, 3, 20))
        assert (before.per_paycheck, before.periods_per_year) == (
            Decimal("2000.00"), Decimal("26"),
        )
        assert (after.per_paycheck, after.periods_per_year) == (
            Decimal("1000.00"), Decimal("52"),
        )

    def test_its_first_paycheck_pays_one_weekly_pay(self):
        """04-08 pays 03-20..04-02, ten weekdays all at the weekly pay: $1,000.00 at 52."""
        basis = _basis(_biweekly_then_weekly(), [(_OPENS, "2000.00")])
        assert _pay(basis, date(2027, 3, 25)) == Decimal("2000.00")
        assert _pay(basis, date(2027, 4, 8)) == Decimal("1000.00")
        assert basis.base_pay_on(date(2027, 4, 8)).periods_per_year == Decimal("52")

    def test_a_raise_inside_a_weekly_period(self):
        """5% from Wednesday 04-07; 04-15 pays 04-03..04-09: 2 weekdays before, 3 after.

        1,000.00 x 2/5 = 400.00; 1,050.00 x 3/5 = 630.00; 1,030.00.
        """
        basis = _basis(
            _biweekly_then_weekly(), [(_OPENS, "2000.00")],
            (_percent(date(2027, 4, 7), "0.05"),),
        )
        assert _pay(basis, date(2027, 4, 15)) == Decimal("1030.00")

    def test_the_salary_page_names_the_carry(self):
        """One change named: 04-08, priced $1,000.00 from the yearly $52,000.00."""
        basis = _basis(_biweekly_then_weekly(), [(_OPENS, "2000.00")])
        assert basis.rhythm_changes_without_pay() == [
            RhythmChangeWithoutPay(date(2027, 4, 8), Decimal("1000.00"), Decimal("52000.00")),
        ]

    def test_an_entry_at_the_seam_badges_yearly_pay_against_the_day_before(self):
        """$1,100.00 weekly from 04-08 (opens 03-20) against 03-19's $2,000.00 biweekly.

        1,100.00 x 52 = 57,200.00 against 2,000.00 x 26 = 52,000.00:
        'PAY +$5,200.00 a year', and nothing named, since pay is recorded
        from the change.
        """
        basis = _basis(
            _biweekly_then_weekly(),
            [(_OPENS, "2000.00"), (date(2027, 4, 8), "1100.00")],
        )
        assert _badge(basis, date(2027, 4, 8)) == "PAY +$5,200.00 a year"
        assert basis.rhythm_changes_without_pay() == []


class TestAPeriodHoldingNoWeekday:
    """A one-day rhythm's weekend paycheck has no weekday to share its pay over: its rate."""

    def test_a_saturday_paycheck_pays_the_pay_in_force_that_day(self):
        """Paid daily from Monday 2027-03-01, each paycheck its own day; 5% from Saturday 03-06.

        Friday 03-05 pays 2,000.00; Saturday 03-06 pays its rate, 2,100.00;
        Monday 03-08 pays 2,100.00.
        """
        monday = date(2027, 3, 1)
        basis = _basis(
            _calendar([monday + timedelta(days=step) for step in range(14)], (_era(monday, 1),)),
            [(monday, "2000.00")], (_percent(date(2027, 3, 6), "0.05"),), gap=0,
        )
        assert _pay(basis, date(2027, 3, 5)) == Decimal("2000.00")
        assert _pay(basis, date(2027, 3, 6)) == Decimal("2100.00")
        assert _pay(basis, date(2027, 3, 8)) == Decimal("2100.00")


class TestThePayListReadsTheRate:
    """The pay list's yearly column is the entry's paycheck's rate times the count."""

    def test_a_raise_inside_the_entry_paycheck_reaches_its_yearly_figure(self):
        """$2,000.00 from 03-11 (opens 02-20); 5% from 03-03, inside that paycheck.

        The rate on 03-05, 03-11's last day, is 2,100.00: 2,100.00 x 26 =
        54,600.00, where the paycheck's base, 2,030.00 x 26, would read
        52,780.00.
        """
        basis = _basis(_biweekly(), [(date(2027, 3, 11), "2000.00")], _WEDNESDAY_RAISE)
        (row,) = pay_rows(basis)
        assert row.paychecks_a_year == 26
        assert row.yearly == Decimal("54600.00")
