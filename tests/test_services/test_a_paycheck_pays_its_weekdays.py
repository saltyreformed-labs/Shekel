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


def _biweekly():
    """Thirty paydays every 14 days from Thursday 2027-01-14."""
    return _calendar(
        [_OPENS + timedelta(days=14 * step) for step in range(30)], (_era(_OPENS, 14),),
    )


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
