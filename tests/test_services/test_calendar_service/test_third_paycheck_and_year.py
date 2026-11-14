"""A month's paycheck count off the one producer the engine also reads, and the year overview's
aggregation.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from app.models.salary_profile import SalaryProfile

from app.services import calendar_service, paycheck_calculator
from tests._test_helpers import (
    era_of,
    eras_of,
    make_income_template,
    record_paydays_across_a_hole,
    rhythm_of,
    start_test_pay_list,
)
from app.services.payroll_basis import PayrollBasis
from tests._tax_configs import tax_configs_for
from app.services.pay_calendar import (
    PayCalendar,
    calendar_for,
    paydays_in_month_through,
    projection_paydays_in_month_through,
)

from ._helpers import _add_transaction


# ── Third Paycheck Tests ─────────────────────────────────────────────


class TestThirdPaycheckDetection:
    """A month's paycheck count, off the ONE producer the engine also reads.

    ``calendar_service._detect_third_paycheck_months`` counted paydays over the
    window a caller happened to hold, which was a SECOND implementation of the
    question ``paycheck_calculator`` asks to decide whether a 24-per-year
    deduction is skipped -- one rule, two scans, two period sets.  Plan step
    **balance:X-bh-1** deleted it for
    :func:`~app.services.pay_calendar.paydays_in_month_through`, so these cases
    grade that producer and the flag the year overview renders from it.
    """

    @staticmethod
    def _three_paycheck_months(calendar, year):
        """The months of *year* holding three or more paydays."""
        return {
            month for month in range(1, 13)
            if len(projection_paydays_in_month_through(
                calendar,
                date(year, month, monthrange(year, month)[1]),
            )) >= 3
        }

    def test_twenty_six_biweekly_paydays_give_two_third_paycheck_months(
        self, app, seed_user, db,
    ):
        """26 biweekly paydays from 2026-01-02 land three times in two months.

        Hand-computed: Jan 2 / 16 / 30 and Jul 3 / 17 / 31.  Every other 2026
        month holds exactly two.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            calendar = calendar_for(seed_user["user"].id)
            assert self._three_paycheck_months(calendar, 2026) == {1, 7}

    def test_an_empty_calendar_holds_no_paydays_in_any_month(self, app):
        """An owner with no payday has no three-paycheck month.

        A real answer rather than an error.  *This said "a companion holds no
        schedule, and production has one such user", which plan step
        ``pay_calendar:C4-d`` made the wrong owner to name: a companion holds
        no schedule ROW and therefore no calendar at all.  The owner this
        builds is the one who HAS a rhythm and has recorded no payday under
        it.*
        """
        with app.app_context():
            calendar = PayCalendar.from_paydays([], eras_of([], 14), user_id=1, history_opens_on=None,
            )
            assert self._three_paycheck_months(calendar, 2026) == set()

    def test_a_month_is_counted_in_its_own_year(self, app, seed_user, db):
        """January 2025 and January 2026 are counted separately.

        The schedule runs 2025-07-04 through 2027, so both Januaries hold
        paydays.  Hand-computed from a 14-day rhythm anchored on 2025-07-04:
        2026 opens Jan 2 / 16 / 30 -- three -- while 2025's own January holds
        none at all, because the schedule does not reach it.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2025, 7, 4),
                num_periods=40,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            calendar = calendar_for(seed_user["user"].id)
            assert 1 in self._three_paycheck_months(calendar, 2026)
            assert projection_paydays_in_month_through(
                calendar, date(2025, 1, 31),
            ) == ()

    def test_the_year_overview_flags_exactly_those_months(
        self, app, seed_user, db,
    ):
        """THE FIRING CONTROL: the rendered flag is the producer's answer.

        Every assertion above would still pass if ``MonthSummary`` were built
        from the old window scan, so this one drives the real surface and
        checks the twelve flags against the same producer the paycheck engine
        reads.  Without it the fold is asserted about a function nothing on a
        page calls.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            overview = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2026,
                user_settings=None,
            )
            flagged = {
                summary.month for summary in overview.months
                if summary.is_third_paycheck_month
            }
            assert flagged == {1, 7}

    def test_past_the_horizon_the_card_counts_the_paydays_it_estimates(
        self, app, seed_user, db,
    ):
        """Past the horizon the card counts the owner's rhythm, and its money is there too.

        The shared producer projects the owner's rhythm past the horizon,
        because the paycheck engine needs a total answer.  The CARD counted
        only SAVED paydays (**balance:R-IB**) while its money stopped at the
        horizon: measured 2026-08-30 on the developer's own data, **29 month
        cards**, 2028-08 through 2030-12, would have read "3 paychecks,
        income ``$0.00``, balance ``$9,539.92``".  Ledger row **N-394** ruled
        the bound off once the cash tier projects, which task plan#3941 built
        and task plan#3945 put on the card.

        This schedule runs 2026-01-02 to 2026-12-18, so 2027 lies wholly past
        its horizon.  January 2027 holds 01-01, 01-15 and 01-29, each a
        projected paycheck the cash tier estimates: a $2,000.00 paycheck on
        each makes the month's income 3 x $2,000.00 = $6,000.00.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            make_income_template(db.session, seed_user, amount="2000.00")
            db.session.commit()

            calendar = calendar_for(seed_user["user"].id)
            assert projection_paydays_in_month_through(
                calendar, date(2027, 1, 31),
            ) == paydays_in_month_through(calendar, date(2027, 1, 31)) == (
                date(2027, 1, 1), date(2027, 1, 15), date(2027, 1, 29),
            )

            detail = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2027, month=1,
                user_settings=None,
            )
            assert detail.paycheck_days == [1, 15, 29]
            assert detail.is_third_paycheck_month is True
            assert detail.total_income == Decimal("6000.00")
            assert detail.net == Decimal("6000.00")

            overview = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2027,
                user_settings=None,
            )
            assert overview.months[0].is_third_paycheck_month is True
            assert overview.months[0].total_income == Decimal("6000.00")

    def test_the_month_the_record_opens_in_counts_from_its_first_payday(self, app):
        """The rhythm before the first saved payday is not counted: nothing holds money there.

        Paydays every 14 days from 2026-01-16, with the owner's history
        stated from 2025-01-01: the engine's rhythm puts January's first
        payday on 01-02, a paycheck no row and no estimate holds (estimates
        fill only paychecks after the last saved one, ruling plan#3932).  The
        card's count, the one ``_build_month_summary`` reads, holds 01-16 and
        01-30 and so flags no third paycheck, where the total reads three.
        Built as a bare calendar because the seed owner's record opens on
        2024-01-05, and no January of it opens mid-month.
        """
        with app.app_context():
            opening = date(2026, 1, 16)
            calendar = PayCalendar.from_paydays(
                [(index + 1, opening + timedelta(days=14 * index))
                 for index in range(3)],
                (era_of(opening, 14),), user_id=1,
                history_opens_on=date(2025, 1, 1),
            )

            assert paydays_in_month_through(calendar, date(2026, 1, 31)) == (
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
            ), "the control: the engine's count reaches back to 01-02"
            assert projection_paydays_in_month_through(
                calendar, date(2026, 1, 31),
            ) == (date(2026, 1, 16), date(2026, 1, 30))

    def test_the_engine_and_the_year_overview_agree_on_a_month(
        self, app, seed_user, db,
    ):
        """The two consumers agree on every month the schedule covers.

        The paycheck engine skips a 24-per-year deduction on a month's third
        payday; the year overview flags the month.  They were two independent
        scans over two period sets before plan step **balance:X-bh-1**, and
        they are now two named SETS over one span search -- the engine's total
        and the card's forward projection (**balance:R-IB**).

        **So this is a real equality and not a tautology**, which an
        adversarial review of this step is why: while both surfaces called one
        function the equality could not fail, and the review said so.  They
        call different functions now, and the schedule below is fully saved,
        which is exactly the domain where the two must not diverge -- the case
        above pins where they deliberately do.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            user_id = seed_user["user"].id
            calendar = calendar_for(user_id)
            profile = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=user_id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1,
                name="Third-paycheck agreement",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(
                profile, Decimal("2307.69"),  # $60,000.00 a year / 26
                date(2026, 1, 2),
            )
            db.session.flush()

            configs = tax_configs_for(profile, 2026)
            breakdowns = paycheck_calculator.project_salary(
                PayrollBasis(profile, calendar),
                [p for p in calendar.saved() if p.start_date.year == 2026],
                configs,
            )
            engine_months = {
                period.start_date.month
                for period, breakdown in zip(
                    [p for p in calendar.saved() if p.start_date.year == 2026],
                    breakdowns,
                )
                if breakdown.period.is_third_paycheck
            }
            overview = calendar_service.get_year_overview(
                user_id=user_id, year=2026,
                user_settings=None,
            )
            flagged = {
                summary.month for summary in overview.months
                if summary.is_third_paycheck_month
            }
            assert engine_months == flagged == {1, 7}


# ── Year Overview Tests ──────────────────────────────────────────────


class TestYearOverview:
    """Tests for the year overview aggregation."""

    def test_year_overview_12_months(self, app, seed_user, seed_periods):
        """YearOverview has exactly 12 MonthSummary entries."""
        with app.app_context():
            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            assert len(result.months) == 12
            # Months are ordered January through December.
            for i, ms in enumerate(result.months):
                assert ms.month == i + 1
                assert ms.year == 2026

    def test_year_overview_marks_third_paycheck(self, app, seed_user, db):
        """Year overview with 26 periods marks exactly 2 third-paycheck months."""
        with app.app_context():
            # The BINDING went with the ``current_anchor_period_id`` line it
            # fed (ruling R-EH); the CALL is fixture setup and stays -- these
            # 26 periods ARE the third-paycheck year under test.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            third_paycheck_count = sum(
                1 for ms in result.months if ms.is_third_paycheck_month
            )
            assert third_paycheck_count == 2

    def test_year_overview_annual_totals(self, app, seed_user, seed_periods, db):
        """Annual totals equal the sum of all monthly totals."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Income", "5000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Expense", "1000.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            sum_income = sum(ms.total_income for ms in result.months)
            sum_expenses = sum(ms.total_expenses for ms in result.months)
            sum_net = sum(ms.net for ms in result.months)

            assert result.annual_income == sum_income
            assert result.annual_expenses == sum_expenses
            assert result.annual_net == sum_net

    def test_year_overview_empty_months_have_zeros(self, app, seed_user, seed_periods, db):
        """Months without data have zero totals and empty day_entries."""
        with app.app_context():
            # Only add data in January.
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Jan Item", "100.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            # April (index 3) should be empty.
            apr = result.months[3]
            assert apr.total_income == Decimal("0")
            assert apr.total_expenses == Decimal("0")
            assert apr.net == Decimal("0")
            assert apr.day_entries == {}

    def test_year_overview_no_double_counting(self, app, seed_user, seed_periods, db):
        """Sum of all month totals equals total across all unique transactions."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Inc", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Exp", "500.00",
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            # All transactions are in January -- no cross-month leakage.
            assert result.annual_income == Decimal("3000.00")
            assert result.annual_expenses == Decimal("500.00")
            assert result.annual_net == Decimal("2500.00")
