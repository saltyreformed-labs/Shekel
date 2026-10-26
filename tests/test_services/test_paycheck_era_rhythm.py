"""
Shekel Budget App -- a paycheck is priced at the rhythm IN FORCE on its payday.

Plan step **salary:X-av-2** (ruling **R-SAL66**; ledger row **SAL-569**).  An
owner's pay history is a list of ERAS -- "paid monthly from January, then
biweekly from July" -- and until this step the paycheck engine read the LATEST
era's paychecks-a-year for every payday: a paycheck paid under the monthly era
was divided by 26, and its withholding annualised by 26, as though it had been
a biweekly one.  The count is now read per payday through
:func:`~app.services.pay_calendar.cadence_on`, and the engine reads base pay
through ONE door, :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`,
for the paycheck and for both year-to-date replays.

Every figure here is MADE UP (ruling **balance:R-BAL132**): a $60,000 salary,
no raises, and the engine suite's own two-bracket tax fakes, so each expected
value is hand-computed in the case that asserts it.

Two schedules, both stated in full so the paydays are read rather than
derived by the code under test:

* **Monthly then biweekly** -- the 1st of each month January-June 2026, then
  every 14 days from Thursday 2026-07-02.  The biweekly era's first payday
  replaces the monthly era's 2026-07-01 (ruling **R-PC75**).
* **The displaced first payday** -- monthly January-August 2026; biweekly under
  the ``prior`` convention from Monday 2026-09-07, which is Labor Day, so that
  era's first paycheck is paid on Friday 2026-09-04, BEFORE its own
  ``effective_from``; then weekly from 2027-01-11.  Three eras so that the old
  reader (the latest era, weekly), the nominal-day reader
  (:func:`~app.services.pay_rhythm.era_covering`, monthly) and the cash-day
  reader this step uses (biweekly) give three different answers.
* **A record paid early before a seam** -- 14 days from 2026-01-02, then 7 days
  from 2026-02-02, the record's last payday 2026-02-01: the 02-02 paycheck
  paid a day early, which the calendar closes at the WEEKLY rhythm.

**The paycheck also CARRIES the rhythm it was priced at** (ruling
**R-SAL70**): the savings page's debt-to-income denominator and months-of-income
goals, the retirement gap's current-pay fallback and the salary cockpit's
third-paycheck chip all turned today's paycheck into a month at the LATEST
era's count, which agreed with the engine only while it divided every payday
by that count.  :class:`TestTodaysPaycheckBecomesAMonthAtItsOwnRhythm` and
:class:`TestTheRecurringSalaryRow` grade each surface for an owner paid
biweekly today with a weekly rhythm recorded to start later (rulings
**R-SAL71** and **R-SAL73** for the Recurring page's salary row, which reads
today's priced paycheck rather than the template's stored copy).
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app import ref_cache
from app.enums import BusinessDayShiftEnum, GoalModeEnum, IncomeUnitEnum
from app.models.savings_goal import SavingsGoal
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import (
    pay_era_write,
    recurring_view,
    salary_cockpit_service,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import (
    PayCadence,
    PayCalendar,
    cadence_on,
    calendar_for,
    era_index_at,
    first_payday_of,
)
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, era_covering
from app.services.paycheck_calculator import calculate_paycheck
from app.services.paycheck_calculator._calendar_questions import (
    _get_cumulative_wages,
)
from app.services.payroll_basis import PayrollBasis
from app.utils.dates import display_today
from tests._test_helpers import (
    era_of,
    fica_only_law,
    make_recurring_raise,
    make_salary_profile,
)
from tests.test_services.test_paycheck_calculator._helpers import (
    FakeBracket,
    FakeBracketSet,
    FakeDeduction,
    FakeFicaConfig,
    FakeProfile,
    FakeStateTaxConfig,
)
from tests.test_services.test_retirement_dashboard_service import _picture
from tests.test_services.test_salary_cockpit_service import _pair
from tests.test_services.test_savings_dashboard_service._helpers import _create_small_loan

_NONE = BusinessDayShiftEnum.NONE

#: The monthly era: the 1st of every month, from 2026-01-01.
_MONTHLY = Era(effective_from=date(2026, 1, 1), rhythm=Rhythm(Monthly(1), _NONE))

#: The biweekly era that follows it, from Thursday 2026-07-02.
_BIWEEKLY = Era(
    effective_from=date(2026, 7, 2), rhythm=Rhythm(FixedDays(14), _NONE),
)

#: A biweekly era taking effect on Labor Day 2026 under ``prior``: its first
#: paycheck is paid the business day before, Friday 2026-09-04.
_BIWEEKLY_FROM_LABOR_DAY = Era(
    effective_from=date(2026, 9, 7),
    rhythm=Rhythm(FixedDays(14), BusinessDayShiftEnum.PRIOR),
)

#: A weekly era after it, on the biweekly era's own grid day 2027-01-11.
_WEEKLY = Era(effective_from=date(2027, 1, 11), rhythm=Rhythm(FixedDays(7), _NONE))


def _calendar(paydays, eras):
    """A calendar over exactly *paydays*, numbered 1.. in order."""
    return PayCalendar.from_paydays(
        [(index + 1, payday) for index, payday in enumerate(paydays)],
        eras,
        user_id=1,
        history_opens_on=None,
    )


def _monthly_then_biweekly():
    """Six monthly paydays, then thirteen biweekly ones through 2026-12-17."""
    return _calendar(
        [date(2026, month, 1) for month in range(1, 7)]
        + [date(2026, 7, 2) + timedelta(days=14 * step) for step in range(13)],
        (_MONTHLY, _BIWEEKLY),
    )


def _with_a_displaced_first_payday():
    """Monthly through August, biweekly from 2026-09-04, weekly from 2027-01-11."""
    return _calendar(
        [date(2026, month, 1) for month in range(1, 9)]
        + [date(2026, 9, 4)]
        + [date(2026, 9, 21) + timedelta(days=14 * step) for step in range(8)]
        + [date(2027, 1, 11) + timedelta(days=7 * step) for step in range(3)],
        (_MONTHLY, _BIWEEKLY_FROM_LABOR_DAY, _WEEKLY),
    )


def _period_on(calendar, payday):
    """The calendar's own period opening on *payday*."""
    matches = [period for period in calendar.periods if period.start_date == payday]
    assert len(matches) == 1, f"{payday} is not a payday of this calendar"
    return matches[0]


def _tax_configs(ss_wage_base="168600"):
    """The engine suite's two-bracket fakes: every figure below is hand-computable.

    Federal: a $15,000 standard deduction, 10% to $50,000, 22% above.  State:
    a flat 4.5% with no deduction.  FICA: 6.2% Social Security to
    *ss_wage_base*, 1.45% Medicare, the surtax far above every figure here.
    """
    return {
        "bracket_set": FakeBracketSet(
            standard_deduction=Decimal("15000"),
            brackets=[
                FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
                FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
            ],
        ),
        "state_config": FakeStateTaxConfig(flat_rate="0.045"),
        "fica_config": FakeFicaConfig(ss_wage_base=ss_wage_base),
    }


def _profile(
    lines=None, pay=Decimal("5000.00"), pay_from=date(2026, 1, 1),
):
    """A made-up $60,000 salary with no raises.

    Its one pay entry sits on the first payday of the calendar a case prices,
    at that payday's rhythm: by default the MONTHLY 2026-01-01 that
    :func:`_monthly_then_biweekly` and :func:`_with_a_displaced_first_payday`
    both open on -- $60,000.00 a year / 12.
    """
    return FakeProfile(
        pay=pay, pay_from=pay_from, lines=lines, created_at=date(2026, 1, 1),
    )


class TestCadenceOn:
    """The pay calendar answers the rhythm in force ON A DAY, not only the latest."""

    def test_one_era_answers_its_own_cadence_on_every_day(self):
        """An owner with one era reads the same count everywhere.

        Input: a single biweekly era; days below the record, on it, and
        projected two years past it.
        Expected: the latest-era cadence, at every day.
        Why: this is the property that makes the step change nothing for an
        owner who has never changed rhythm -- production's case.  The
        covering era IS the latest era, so the engine's divisor is the one it
        always was.
        """
        calendar = _calendar(
            [date(2026, 1, 2) + timedelta(days=14 * step) for step in range(26)],
            (Era(effective_from=date(2026, 1, 2), rhythm=Rhythm(FixedDays(14), _NONE)),),
        )
        for day in (date(2025, 6, 6), date(2026, 1, 2), date(2026, 7, 3),
                    date(2028, 12, 29)):
            assert cadence_on(calendar, day) == calendar.cadence, day
            assert cadence_on(calendar, day).periods_per_year == Decimal("26")

    def test_each_payday_reads_its_own_eras_count(self):
        """A monthly-era payday answers 12 under a later biweekly era.

        Input: monthly January-June 2026, biweekly from 2026-07-02.
        Expected: 12 on every monthly payday and on a day below the record
        (the earliest era runs backward, R-PC66); 26 on the biweekly era's
        first payday, on a later one, and on a projected day past the record.
        Why: the latest era's count -- what ``PayCalendar.cadence`` answers,
        asserted here as the control -- is 26, so a reader that ignored the
        day would answer 26 on the monthly paydays and fail this case.
        """
        calendar = _monthly_then_biweekly()
        assert calendar.cadence.periods_per_year == Decimal("26")

        expected = {
            date(2025, 12, 1): Decimal("12"),
            date(2026, 3, 1): Decimal("12"),
            date(2026, 6, 1): Decimal("12"),
            date(2026, 7, 2): Decimal("26"),
            date(2026, 12, 17): Decimal("26"),
            date(2027, 6, 3): Decimal("26"),
        }
        assert {
            day: cadence_on(calendar, day).periods_per_year for day in expected
        } == expected

    def test_a_displaced_first_payday_belongs_to_the_era_it_opens(self):
        """The paycheck an era pays BEFORE its effective day is that era's.

        Input: the three-era schedule; its biweekly era takes effect on Labor
        Day 2026-09-07 under ``prior``, so its first paycheck is 2026-09-04.
        Expected: 26 on 2026-09-04.
        Why: the calendar derives that payday under the biweekly era (its
        period runs 09-04 to 09-20), so its pay is a biweekly paycheck.  The
        two other readers are asserted beside it as the controls: the
        nominal-day reader ``era_covering`` answers the MONTHLY era for that
        day, and the latest era is WEEKLY -- so reading either one fails this
        case, and neither can pass it by coinciding with the right answer.
        """
        calendar = _with_a_displaced_first_payday()
        payday = date(2026, 9, 4)
        assert first_payday_of(_BIWEEKLY_FROM_LABOR_DAY) == payday

        assert cadence_on(calendar, payday).periods_per_year == Decimal("26")
        assert era_covering(calendar.eras, payday) == _MONTHLY
        assert calendar.cadence.periods_per_year == Decimal("52")


class TestAnEarlierRhythmsPaycheck:
    """The engine prices a payday at its OWN era's paychecks a year."""

    def test_a_monthly_paycheck_divides_and_annualises_by_twelve(self):
        """The monthly-era paycheck, line by line.

        Input: $60,000, the 2026-03-01 payday of the monthly-then-biweekly
        schedule, no lines.
        Expected, by hand:

        * base = gross = 60,000 / 12 = $5,000.00;
        * federal (Pub 15-T): 5,000 x 12 = 60,000, less the 15,000 standard
          deduction = 45,000 at 10% = 4,500 a year, / 12 = $375.00;
        * state: 5,000 x 12 x 4.5% = 2,700 a year, / 12 = $225.00;
        * Social Security 5,000 x 6.2% = $310.00; Medicare 1.45% = $72.50;
        * net 5,000 - 375 - 225 - 310 - 72.50 = $4,017.50.

        Why: the base alone would not catch a withholding path left on the
        latest count.  Annualised by 26 the same $5,000.00 is 130,000 a year,
        115,000 taxable, 50,000 x 10% + 65,000 x 22% = 19,300, / 26 =
        $742.31 federal -- so ``$375.00`` pins the annualiser independently
        of the divisor.  The state line is asserted but cannot tell the
        counts apart: a flat rate annualised and divided back by the same
        count is the same figure at any count.
        """
        calendar = _monthly_then_biweekly()
        paycheck = calculate_paycheck(
            PayrollBasis(_profile(), calendar),
            _period_on(calendar, date(2026, 3, 1)),
            _tax_configs(),
        )

        assert paycheck.earnings.annual_salary == Decimal("60000")
        assert paycheck.earnings.base_biweekly == Decimal("5000.00")
        assert paycheck.earnings.gross_biweekly == Decimal("5000.00")
        assert paycheck.taxes.federal == Decimal("375.00")
        assert paycheck.taxes.state == Decimal("225.00")
        assert paycheck.taxes.social_security == Decimal("310.00")
        assert paycheck.taxes.medicare == Decimal("72.50")
        assert paycheck.earnings.net_pay == Decimal("4017.50")

    def test_the_later_rhythms_paycheck_is_the_biweekly_one(self):
        """The biweekly era's paycheck is priced at 26, as it always was.

        Input: $60,000, the 2026-07-16 payday.
        Expected: base 60,000 / 26 = 2,307.6923 -> $2,307.69; federal
        2,307.69 x 26 = 59,999.94, less 15,000 = 44,999.94 at 10% =
        4,499.994, / 26 = 173.0767 -> $173.08.
        Why: the other half of the firing pair -- the monthly case above
        would pass for an engine that divided EVERY payday by 12.
        """
        calendar = _monthly_then_biweekly()
        paycheck = calculate_paycheck(
            PayrollBasis(_profile(), calendar),
            _period_on(calendar, date(2026, 7, 16)),
            _tax_configs(),
        )

        assert paycheck.earnings.base_biweekly == Decimal("2307.69")
        assert paycheck.taxes.federal == Decimal("173.08")

    def test_the_wage_cumulative_sums_each_payday_at_its_own_pay(self):
        """The FICA year-to-date counts the monthly paychecks at $5,000.00.

        Input: $60,000; the year-to-date before 2026-07-16, and the Social
        Security on 2026-07-02 under a made-up $31,000 wage base.
        Expected: six monthly paychecks at $5,000.00 plus the 2026-07-02 one
        at $2,307.69 = $32,307.69.  On 2026-07-02 the year-to-date is
        $30,000.00, so $1,000.00 of wage base remains and the tax is
        1,000 x 6.2% = $62.00, not 2,307.69 x 6.2% = $143.08.
        Why: the year-to-date REPLAYS each earlier payday's base, and the
        replay is its own reader of the base: at the latest count it summed
        seven paychecks at $2,307.69 = $16,153.83 and reached the wage base
        months late, which overstates net.
        """
        calendar = _monthly_then_biweekly()
        basis = PayrollBasis(_profile(), calendar)

        assert _get_cumulative_wages(
            basis, date(2026, 7, 16),
        ) == Decimal("32307.69")

        paycheck = calculate_paycheck(
            basis, _period_on(calendar, date(2026, 7, 2)),
            _tax_configs(ss_wage_base="31000"),
        )
        assert paycheck.taxes.social_security == Decimal("62.00")

    def test_a_capped_lines_replay_counts_each_payday_at_its_own_pay(self):
        """A percentage line's cap is reached on the paychecks that reached it.

        Input: $60,000 with a 10% pre-tax line capped at a made-up $3,000 a
        year.
        Expected: $500.00 on each monthly paycheck (10% of $5,000.00), so the
        June paycheck brings the year to exactly $3,000.00 and the 2026-07-02
        line is $0.00.
        Why: the cap's year-to-date replays each earlier payday's line
        through its own read of the base.  At the latest count it replayed
        six paychecks of 10% x $2,307.69 = $230.77, $1,384.62 in all, and
        charged the line again in July -- $230.77 over a cap the owner had
        already met.
        """
        line = FakeDeduction(
            name="Retirement", amount="0.10", calc_method="percentage",
            annual_cap="3000",
        )
        calendar = _monthly_then_biweekly()
        basis = PayrollBasis(_profile(lines=[line]), calendar)

        june = calculate_paycheck(
            basis, _period_on(calendar, date(2026, 6, 1)), _tax_configs(),
        )
        july = calculate_paycheck(
            basis, _period_on(calendar, date(2026, 7, 2)), _tax_configs(),
        )

        assert [d.amount for d in june.deductions.pre_tax] == [Decimal("500.00")]
        assert [d.amount for d in july.deductions.pre_tax] == [Decimal("0.00")]

    def test_a_displaced_first_payday_is_priced_at_its_new_rhythm(self):
        """The paycheck an era pays before its effective day is a paycheck of that era.

        Input: $60,000 on the three-era schedule.
        Expected: 2026-08-01 (monthly) $5,000.00; 2026-09-04 (the biweekly
        era's displaced first payday) 60,000 / 26 = $2,307.69; 2027-01-11
        (weekly) 60,000 / 52 = 1,153.846 -> $1,153.85.
        Why: 2026-09-04 is where the three readers part (see
        :class:`TestCadenceOn`): the nominal-day reader would price it at
        $5,000.00 and the latest era at $1,153.85.
        """
        calendar = _with_a_displaced_first_payday()
        basis = PayrollBasis(_profile(), calendar)

        bases = {
            payday: calculate_paycheck(
                basis, _period_on(calendar, payday), _tax_configs(),
            ).earnings.base_biweekly
            for payday in (date(2026, 8, 1), date(2026, 9, 4), date(2027, 1, 11))
        }
        assert bases == {
            date(2026, 8, 1): Decimal("5000.00"),
            date(2026, 9, 4): Decimal("2307.69"),
            date(2027, 1, 11): Decimal("1153.85"),
        }

    def test_the_state_line_annualises_by_the_paydays_own_count(self):
        """A state standard deduction makes the state line see the count.

        Input: $60,000, the 2026-03-01 monthly payday, the flat 4.5% state
        config given a made-up $12,750 standard deduction.
        Expected: (5,000 x 12 - 12,750) x 4.5% = 2,126.25 a year, / 12 =
        177.1875 -> $177.19.
        Why: with no deduction a flat rate annualised and divided back by one
        count is the same at any count, so the monthly case above cannot tell
        the state annualiser's count apart; with a deduction the wrong count
        reads (5,000 x 26 - 12,750) x 4.5% = 5,276.25, / 26 = $202.93.  An
        adversarial review of this step moved only the state line onto the
        latest count and found the other cases green.
        """
        calendar = _monthly_then_biweekly()
        configs = _tax_configs()
        configs["state_config"].standard_deduction = Decimal("12750")
        paycheck = calculate_paycheck(
            PayrollBasis(_profile(), calendar),
            _period_on(calendar, date(2026, 3, 1)),
            configs,
        )

        assert paycheck.taxes.state == Decimal("177.19")

    def test_the_paycheck_carries_the_rhythm_it_was_priced_at(self):
        """``PeriodInfo.cadence`` is the era in force on the payday (R-SAL70).

        Input: the monthly-then-biweekly schedule, one payday in each era.
        Expected: monthly on 2026-03-01, biweekly on 2026-07-16.
        Why: it is the count every reader converts this paycheck to a month
        with; carried on the paycheck, it cannot be read off the calendar at
        another day or at the latest era.
        """
        calendar = _monthly_then_biweekly()
        basis = PayrollBasis(_profile(), calendar)

        assert calculate_paycheck(
            basis, _period_on(calendar, date(2026, 3, 1)), _tax_configs(),
        ).period.cadence == PayCadence(Monthly(1))
        assert calculate_paycheck(
            basis, _period_on(calendar, date(2026, 7, 16)), _tax_configs(),
        ).period.cadence == PayCadence(FixedDays(14))


def _with_a_record_paid_early_before_a_seam():
    """14 days from 2026-01-02, 7 days from 2026-02-02; the record ends 02-01."""
    return _calendar(
        [date(2026, 1, 2), date(2026, 1, 16), date(2026, 2, 1)],
        (
            Era(effective_from=date(2026, 1, 2), rhythm=Rhythm(FixedDays(14), _NONE)),
            Era(effective_from=date(2026, 2, 2), rhythm=Rhythm(FixedDays(7), _NONE)),
        ),
    )


class TestARecordPaidEarlyBeforeASeam:
    """A recorded payday belongs to the era of the planned payday it stands for."""

    def test_it_is_the_next_eras_paycheck(self):
        """2026-02-01 is the weekly era's 02-02 paycheck, paid a day early.

        Input: the record 01-02, 01-16, 02-01 under a 14-day era and a 7-day
        era from 02-02.
        Expected: the calendar closes 02-01 on 02-08 (the next payday is the
        weekly 02-09), ``cadence_on`` answers 52, and the engine divides by
        it: 60,000 / 52 = 1,153.846 -> $1,153.85.
        Why: ``era_index_at`` alone -- the first draft of ``cadence_on`` --
        places 02-01 in the 14-day era (asserted as the control) and priced
        it at 60,000 / 26 = $2,307.69, a biweekly paycheck on a period the
        calendar closes weekly.  No door writes such a record today, since
        every door records displaced grid days; it is built here by hand, and
        the adversarial review of this step measured it.
        """
        calendar = _with_a_record_paid_early_before_a_seam()
        payday = date(2026, 2, 1)
        assert _period_on(calendar, payday).end_date == date(2026, 2, 8)
        assert era_index_at(calendar.eras, payday) == 0

        assert cadence_on(calendar, payday).periods_per_year == Decimal("52")
        assert calculate_paycheck(
            PayrollBasis(
                _profile(
                    # This calendar opens BIWEEKLY on 2026-01-02:
                    # $60,000.00 a year / 26.
                    pay=Decimal("2307.69"), pay_from=date(2026, 1, 2),
                ),
                calendar,
            ),
            _period_on(calendar, payday),
            _tax_configs(),
        ).earnings.base_biweekly == Decimal("1153.85")


class TestTodaysPaycheckBecomesAMonthAtItsOwnRhythm:
    """Every reader turning today's paycheck into a month converts at its count.

    Ruling **R-SAL70**.  The owner is the savings page's own current-pay
    owner (``TestTheCurrentPayIsThePassPricersStubPricedAndSummed``): a
    made-up $52,000.00 profile paid every 14 days, no deductions, priced under
    the made-up FICA-only law installed for tax year 2026 (federal and state
    tax $0.00), so the current paycheck is::

        gross   52,000.00 / 26          = 2,000.00
        net     2,000.00 - 124.00 - 29.00 = 1,847.00

    -- and a WEEKLY era is recorded to take effect four weeks after the saved
    record ends, so the calendar's LATEST rhythm pays 52 a year while today's
    paycheck was priced at 26.  Every surface below read the latest count
    until this step, and each expected value is paired with what that read.
    """

    @pytest.fixture(autouse=True)
    def _fica_and_nothing_else(self, tax_law):
        """Install the owner's law: the made-up FICA-only law, for tax year 2026."""
        tax_law(fica_only_law())

    @staticmethod
    def _seed(db, seed_user, periods, *, goal_unit=None):
        """The owner above, the later weekly era, and optionally a 3x income goal."""
        user_id = seed_user["user"].id
        make_salary_profile(
            seed_user, db.session, pay=Decimal("2000.00"),  # $52,000.00 / 26
        )
        db.session.flush()
        last_payday = max(period.start_date for period in periods)
        pay_era_write.mint_era(
            user_id, era_of(last_payday + timedelta(days=28), 7),
        )
        if goal_unit is not None:
            db.session.add(SavingsGoal(
                user_id=user_id,
                account_id=seed_user["account"].id,
                name="Months of salary",
                goal_mode_id=ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE),
                income_unit_id=ref_cache.income_unit_id(goal_unit),
                income_multiplier=Decimal("3.00"),
                is_active=True,
            ))
        db.session.commit()

        # The premise, asserted: the latest rhythm is weekly, and today's
        # paycheck was priced biweekly -- the two counts the readers could use.
        calendar = calendar_for(user_id)
        assert calendar.cadence.periods_per_year == Decimal("52")
        assert cadence_on(
            calendar, calendar.period_containing(display_today()).start_date,
        ).periods_per_year == Decimal("26")

    def test_a_months_of_income_goal(self, app, db, seed_user, seed_periods_today):
        """3 months of a $1,847.00 net: 3 x 1,847 x 26 / 12 = $12,005.50.

        At the latest count it read 3 x 1,847 x 52 / 12 = $24,011.00.
        """
        with app.app_context():
            self._seed(db, seed_user, seed_periods_today, goal_unit=IncomeUnitEnum.MONTHS)
            goals = savings_dashboard_service.compute_goal_progress(
                BalanceContext.build(seed_user["user"].id),
            )
            assert [goal.resolved_target for goal in goals] == [Decimal("12005.50")]

    def test_the_debt_to_income_denominator(self, app, db, seed_user, seed_periods_today):
        """A month of a $2,000.00 gross: 2,000 x 26 / 12 = $4,333.33.

        At the latest count it read 2,000 x 52 / 12 = $8,666.67, which halves
        the ratio.  The numerator is the debt summary's own payment total
        (not under test), so the ratio is asserted over the denominator the
        way the savings page's own DTI cases pin it.  **The loan is made-up
        $50,000**, so the ratio sits near 50% and its one-decimal quantum
        resolves the denominator to about 0.2% (roughly $4 either side); an
        adversarial review of this step found the first draft's $1,000 loan
        put the ratio at 1.0%, where any denominator within about 5% read
        the same.
        """
        with app.app_context():
            self._seed(db, seed_user, seed_periods_today)
            _create_small_loan(
                seed_user, db.session, principal=Decimal("50000.00"),
            )
            db.session.commit()
            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            assert summary is not None and summary.dti is not None
            assert summary.total_monthly_payments > Decimal("0.00")

            def ratio_over(gross_monthly):
                return (
                    summary.total_monthly_payments / gross_monthly * Decimal("100")
                ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

            assert summary.dti.ratio == ratio_over(Decimal("4333.33"))
            assert summary.dti.ratio > Decimal("20.0"), (
                "the ratio is too coarse to grade the denominator"
            )
            assert summary.dti.ratio != ratio_over(Decimal("8666.67"))

    def test_the_retirement_gaps_current_pay_fallback(
        self, app, db, seed_user, seed_periods_today,
    ):
        """With no retirement date the gap reads today's net: 1,847 x 26 / 12.

        Expected: pre-retirement net $4,001.83 a month.  At the latest count
        it read 1,847 x 52 / 12 = $8,003.67.  The fallback is the branch the
        projection cannot serve -- no pension and no stated retirement date
        -- asserted as the premise.
        """
        with app.app_context():
            self._seed(db, seed_user, seed_periods_today)
            picture = _picture(seed_user["user"].id)
            assert picture.retirement_date is None
            assert picture.net.pre_retirement_net_monthly == Decimal("4001.83")


class TestTheRecurringSalaryRow:
    """The Recurring page's salary row is TODAY's priced paycheck (R-SAL71, R-SAL73).

    The owner is :class:`TestTodaysPaycheckBecomesAMonthAtItsOwnRhythm`'s --
    $52,000.00 paid every 14 days under the made-up FICA-only law installed for
    tax year 2026, a weekly era recorded to take effect after the saved
    record -- with the profile created through the salary form, so its
    template is the real one (``POST /salary``).  The template's stored
    ``default_amount`` is then overwritten with a made-up
    STALE $1.00, which is what a stored copy becomes when a raise date passes
    between saves: the row must not read it.
    """

    @pytest.fixture(autouse=True)
    def _fica_and_nothing_else(self, tax_law):
        """Install the owner's law: the made-up FICA-only law, for tax year 2026."""
        tax_law(fica_only_law())

    def test_amount_monthly_and_the_forward_per_paycheck_unit(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Amount $1,847.00; Monthly 1,847 x 26 / 12 = $4,001.83; per paycheck $923.50.

        The per-paycheck toggle keeps the page's one unit, a paycheck at the
        LATEST rhythm (R-SAL73): 4,001.83... x 12 / 52 = $923.50.  Read off the
        stored copy at the latest count, the row showed $1.00 and
        1.00 x 52 / 12 = $4.33 a month -- and $8,003.67 with a copy freshly
        saved at today's $1,847.00.
        """
        with app.app_context():
            template = self._seed_with_a_stale_copy(
                db, auth_client, seed_user, seed_periods_today,
            )
            view = recurring_view.build_view(
                [template], [], [], BalanceContext.build(seed_user["user"].id),
            )
            (row,) = view.income.rows
            assert row.amount == Decimal("1847.00")
            assert row.equivalent.monthly == Decimal("4001.83")
            assert row.equivalent.per_paycheck == Decimal("923.50")

    @staticmethod
    def _seed_with_a_stale_copy(db, auth_client, seed_user, periods):
        """The owner above through the salary form, then its copy made stale."""
        user_id = seed_user["user"].id
        last_payday = max(period.start_date for period in periods)
        pay_era_write.mint_era(
            user_id, era_of(last_payday + timedelta(days=28), 7),
        )
        db.session.commit()
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        auth_client.post("/salary", data={
            "name": "Main Job",
            "pay_amount": "2000.00",  # $52,000.00 a year / 26
            "pay_payday": min(
                period.start_date for period in periods
            ).isoformat(),
            "filing_status_id": filing.id,
            "state_code": "NC",
        }, follow_redirects=True)
        template = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=user_id, name="Main Job").one().template
        )
        template.default_amount = Decimal("1.00")
        db.session.commit()
        return template

    def test_the_rendered_page_shows_the_priced_paycheck(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /templates: the salary row's Amount cell and sort key read $1,847.00.

        Why: the case above grades :attr:`RecurringRow.amount` and never
        renders the page, so the TEMPLATE could go on reading the stored copy
        with the suite green -- an adversarial review of this step reverted
        both template reads to ``default_amount`` and found every test that
        loads the Recurring page passing.  This renders it.
        """
        with app.app_context():
            self._seed_with_a_stale_copy(
                db, auth_client, seed_user, seed_periods_today,
            )
            html = auth_client.get("/templates").get_data(as_text=True)

            start = html.index('data-sort-name="main job"')
            row = html[start:html.index("</tr>", start)]
            assert 'data-sort-amount="1847.00"' in row
            assert "$1,847.00" in row
            assert "$1.00" not in row

    def test_before_the_first_payday_the_first_paycheck(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A day before the first saved payday prices THAT paycheck (R-SAL79).

        Input: the owner above, with a made-up 10% raise from the month after
        the first saved payday, read on the day before that payday.
        Expected: Amount $1,847.00 and Monthly 1,847 x 26 / 12 = $4,001.83 --
        the first paycheck, the only one before the raise.  Every later
        SAVED paycheck is 57,200 / 26 = 2,200.00 gross less 168.30 FICA =
        $2,031.70, asserted for the SECOND and the LAST as the control so the
        case tells the FIRST paycheck from any other (an adversarial review of
        this step priced the LAST saved paycheck instead and found the first
        draft green).  The second is asserted because the construction does
        not guarantee it: the raise takes the month after the first payday,
        so a first payday early in its month puts the second paycheck before
        the raise too, and the case would stop telling the two apart.
        Why: there is no paycheck today, and until R-SAL79 the row fell back
        to the stored copy at the LATEST (weekly) count: $1.00 and
        1.00 x 52 / 12 = $4.33 here.
        """
        with app.app_context():
            template = self._seed_with_a_stale_copy(
                db, auth_client, seed_user, seed_periods_today,
            )
            user_id = seed_user["user"].id
            first_payday = min(period.start_date for period in seed_periods_today)
            raise_from = date(
                first_payday.year + first_payday.month // 12,
                first_payday.month % 12 + 1, 1,
            )
            make_recurring_raise(
                template.salary_profiles[0].id, db.session,
                effective_year=raise_from.year,
                effective_month=raise_from.month,
                percentage=Decimal("0.10"),
            )
            db.session.commit()
            ctx = BalanceContext.build(
                user_id, as_of=first_payday - timedelta(days=1),
            )
            calendar = ctx.calendar()
            assert calendar.span_containing(ctx.as_of) is None
            profile = template.salary_profiles[0]
            for later in (calendar.periods[1], calendar.periods[-1]):
                assert ctx.paychecks().for_profile(profile).at(
                    later,
                ).earnings.net_pay == Decimal("2031.70"), later.start_date

            (row,) = recurring_view.build_view([template], [], [], ctx).income.rows
            assert row.amount == Decimal("1847.00")
            assert row.equivalent.monthly == Decimal("4001.83")


class TestTheCockpitsThirdPaycheckChip:
    """The chip compares a third paycheck to a regular one at the same BASE pay."""

    def test_a_change_of_rhythm_is_not_a_third_paycheck_bonus(self):
        """Weekly June paychecks, then a biweekly era from 06-19.

        Input: one $52,000 salary throughout -- weekly 06-05 and 06-12 at a
        $1,000.00 base and $800.00 net, then biweekly from 06-19 (June's third
        paycheck) at a $2,000.00 base and $1,700.00 net, and the regular
        biweekly 07-03 at $1,600.00 net.
        Expected: the chip's regular net for 06-19 is $1,600.00, a $100.00
        third-paycheck delta.
        Why: matched on the annual salary, the nearest regular paycheck was
        the WEEKLY 06-12 at $800.00, and the chip showed the change of rhythm
        as a $900.00 third-paycheck bonus.
        """
        pairs = [
            _pair(1, date(2026, 6, 5), date(2026, 6, 11), "52000", "1000", "800",
                  cadence=PayCadence(FixedDays(7))),
            _pair(2, date(2026, 6, 12), date(2026, 6, 18), "52000", "1000", "800",
                  cadence=PayCadence(FixedDays(7))),
            _pair(3, date(2026, 6, 19), date(2026, 7, 2), "52000", "2000", "1700",
                  is_third=True),
            _pair(4, date(2026, 7, 3), date(2026, 7, 16), "52000", "2000", "1600"),
        ]

        assert salary_cockpit_service.base_regular_net(pairs, 2) == Decimal("1600")
