"""The paycheck count derives from the owner's cadence, and from nothing else.

Plan step **R-F16**, closing finding **F-16**.  Until it,
``salary.salary_profiles.pay_periods_per_year`` was a SECOND stored answer to
"how often am I paid" beside ``budget.pay_schedule.cadence_days``, and no door
validated one against the other.  Measured with the real engine on the
developer's own ``$91,675`` salary, a profile reading 26 beside a 7-day cadence
modelled ``$15,279.20`` of monthly gross against a true ``$7,639.60`` -- the
year's paychecks summing to 200% of salary.

**What these tests hold, and why each is here rather than folded into another
file.**  The engine's own suite (the ``test_paycheck_calculator/`` package)
pins a paycheck's arithmetic: every case of it that prices more than one
paycheck walks the 14-day cadence, and its one case at another rhythm (the
once-a-year cadence in ``test_the_pipeline.py``) prices a single paycheck, so
none of it could see the defect.  The
property F-16 is about is a year-scale one -- the count the engine divides by
and the number of paychecks the owner actually receives are the same number --
and it is only visible across a whole year at more than one rhythm.  That is
the axis every case below varies, which is the shape
``docs/plans/lessons.md`` records as the one a baseline cannot see when no
case varies it.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.extensions import db as _db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.pay_rhythm import FixedDays
from app.services.balance_at import BalanceContext
from app.services.balance_at._inputs import _contribution_inputs_for_accounts
from app.services.pay_calendar import PayCadence, PayCalendar, PayCalendarError
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.tax_report_service import compute_tax_report

from tests._test_helpers import (
    era_of,
    start_test_pay_list,
    strip_owner_schedule,
)


def _strip_every_payday(db, user_id):
    """Leave *user_id* with no payday and no persisted cadence, committed.

    The ONLY state in which
    :func:`app.services.pay_schedule_service.resolve_cadence` answers ``None``,
    and since plan step ``pay_calendar:C4-b-2`` the two halves are one fact
    rather than two: ``fk_pay_periods_schedule`` holds a payday's owner to
    having a schedule row, so an owner with no row has no paydays either.
    Before that step the cadence was INFERRED from the last period, so a single
    surviving payday would still answer and the control would not fire.

    **The order is forced and is not stylistic.**  That key is
    ``ON DELETE RESTRICT``, so removing the schedule row while a payday still
    references it is refused by the database.  Children before parents, which
    is the order this helper always meant.
    """
    db.session.commit()
    strip_owner_schedule(db.session, user_id)
    db.session.commit()


def _investment_account_with_an_active_deduction(db, seed_user, name):
    """Return an investment account the seam must ADAPT a deduction for.

    **Written once because the two seam cases below must reach the calendar by
    the same route** -- and the route is NOT the one an earlier draft of this
    docstring named.  It said the deduction is what makes the refusal case
    non-vacuous, because ``_contribution_inputs_for_accounts`` resolved
    ``ctx.calendar()`` inside a comprehension over the deduction map.  An
    adversarial review measured that FALSE: with that cadence read replaced by
    a hardcoded ``PayCadence(14)`` the refusal case stayed GREEN, and it
    failed only when ``_inputs.py``'s OTHER calendar read was mutated too --
    and that one fired on ``investment_params_map`` alone.

    **Since plan step salary:R14-b there is exactly ONE calendar read**, and
    the ambiguity that review found goes with the second: the loader hands
    ``ctx.calendar()`` to ``projection_inputs.load_payroll_feeds``, scoped to
    the accounts that HAVE params, and nothing else here touches it.  So the
    ``InvestmentParams`` is still what reaches the calendar, and the deduction
    still earns its place for the SERVED case -- it is what gives that owner a
    payroll feed to price at all.  Both are built here so the pair differs in
    exactly one fact -- the schedule row -- and in nothing else.

    Args:
        db: The test database session holder.
        seed_user: The seeded owner fixture.
        name: A distinct account name, so two cases in one class do not
            collide.

    Returns:
        The flushed :class:`~app.models.account.Account`.
    """
    user_id = seed_user["user"].id
    account = Account(
        user_id=user_id, name=name, account_type_id=4, is_active=True,
    )
    db.session.add(account)
    db.session.flush()
    db.session.add(InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07"),
        employer_contribution_type_id=(
            ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.NONE,
            )
        ),
    ))
    profile = SalaryProfile(
        user_id=user_id, scenario_id=seed_user["scenario"].id,
        filing_status_id=1, name=f"{name} profile",
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("1923.08"))  # $50,000.00 a year / 26
    db.session.flush()
    db.session.add(PaycheckLine(
        salary_profile_id=profile.id, name=name,
        amount=Decimal("100.00"), calc_method_id=1,
        paycheck_line_kind_id=1, is_active=True,
        target_account_id=account.id,
    ))
    return account


def _strip_every_payday_keeping_the_schedule(db, user_id):
    """Leave *user_id* with no payday and their ``budget.pay_schedule`` row.

    Plan step ``pay_calendar:C4-d`` (ruling **R-PC45**) split what
    :func:`_strip_every_payday` used to leave behind into two states, and only
    this one is ordinary: an owner who HAS a recorded cadence and has recorded
    no payday under it.  ``pay_period_admin.reset_pay_periods`` passes through
    it, and so does any owner between their schedule row being written and
    their first batch landing.

    The other state -- no row at all -- is the companion, and since that step
    it has no pay calendar rather than an empty one.  The two are the subject
    of the two halves of
    :class:`TestWhichOwnerTheSeamSERVESAndWhichItREFUSES`.
    """
    db.session.commit()
    db.session.execute(text(
        "DELETE FROM budget.pay_periods WHERE user_id = :u"), {"u": user_id})
    db.session.commit()

#: Every cadence whose derived count the dropped dropdown could express, plus
#: the two it could not.  ``(cadence_days, paychecks_a_year)``; the second
#: value is ``round(365.2425 / cadence_days)`` stated independently, so a
#: change to the derivation fails here rather than agreeing with itself.
_CADENCES = [
    (7, 52),     # weekly
    (14, 26),    # biweekly -- production, and the old column's default
    (15, 24),    # the semi-monthly COUNT, on nominal paydays (the R-F16 ruling)
    (30, 12),    # monthly
    (365, 1),    # annual (a contractor)
]

#: The $91,675.00 salary's pay entry at each count above: what ONE paycheck
#: pays at that rhythm, rounded to the cent ROUND_HALF_UP (plan step
#: salary:X-av-3a stores the paycheck, not the year).
_PAY_OF_91675_AT = {
    52: Decimal("1762.98"),   # $91,675.00 a year / 52
    26: Decimal("3525.96"),   # $91,675.00 a year / 26
    24: Decimal("3819.79"),   # $91,675.00 a year / 24
    12: Decimal("7639.58"),   # $91,675.00 a year / 12
    1: Decimal("91675.00"),   # $91,675.00 a year / 1
}


def _calendar(cadence_days, count, user_id=1, first=date(2026, 1, 1)):
    """A derived calendar of *count* paydays spaced *cadence_days* apart."""
    return PayCalendar.from_paydays(
        paydays=[
            (i + 1, first + timedelta(days=cadence_days * i))
            for i in range(count)
        ],
        eras=(era_of(first, cadence_days),),
        user_id=user_id,
        history_opens_on=None,
    )


class TestTheCountIsTheSchedule:
    """The engine's divisor equals the paydays the owner actually receives."""

    @pytest.mark.parametrize("cadence_days,expected", _CADENCES)
    def test_the_derived_count_is_the_paydays_in_a_year(
        self, cadence_days, expected,
    ):
        """``periods_per_year`` equals the paydays a year of that cadence holds.

        Input: each authorable rhythm.
        Expected: the derived count.
        Why: this is the identity F-16 broke.  The engine divides an annual
        salary by the count and the schedule pays it out once per payday, so
        the two being the same number is what makes a year's paychecks add up
        to a year's salary.  It was two independently writable columns.
        """
        assert PayCadence(FixedDays(cadence_days)).periods_per_year == expected

    @pytest.mark.parametrize("cadence_days,count", _CADENCES)
    def test_a_years_paychecks_sum_to_a_years_salary(
        self, app, db, seed_user, cadence_days, count,
    ):
        """A year's grosses total the yearly figure the engine reports, at every rhythm.

        Input: a raise-free profile whose one pay entry is $91,675.00 a year's
        worth at the rhythm tested (91,675.00 / count, half-up), projected
        over one full year at each authorable cadence.
        Expected: exactly ``count`` paychecks, every one the same figure,
        summing EXACTLY to the yearly figure the engine reports
        (``Earnings.annual_salary``: the pay times the count it annualises
        by); and within half a cent per paycheck of $91,675.00.
        Why: **this is the money property finding F-16 destroyed**, and it is
        an identity rather than a figure, so it holds at every cadence without
        a per-cadence expected value to get wrong.  Before R-F16 the engine
        divided by a stored 26 while the schedule paid out ``count`` times, so
        the year totalled ``count / 26`` of the salary: 200% at a 7-day
        cadence, 46% at 30 days.  Measured on this exact salary at plan step
        R-F16.

        **Since plan step salary:X-av-3a the stored fact is the paycheck**
        (ruling **R-SAL59**) and nothing divides it, so the property F-16
        needs is that the COUNT the yearly figure is multiplied by is the
        number of paydays the calendar pays in a year: the exact equality
        grades that, and a count read anywhere but the calendar (hardcoded to
        26, say) fails it at every other rhythm.  The half-cent bound against
        $91,675.00 is the property as ruling **balance:R-HW** stated it for a
        divided salary; each fixture's pay is already that salary over its
        own count, so the bound now holds by construction and cannot fail on
        its own (an adversarial review of this step hardcoded the count to 26
        and found the bound alone passing).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = SalaryProfile(
                user_id=user_id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1,
                name=f"Cadence {cadence_days}",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, _PAY_OF_91675_AT[count])
            db.session.flush()

            calendar = _calendar(cadence_days, count, user_id=user_id)
            periods = list(calendar.saved())
            configs = load_tax_configs_for_year(profile, 2026)

            breakdowns = paycheck_calculator.project_salary(
                PayrollBasis(profile, calendar), periods, configs,
            )

            total = sum(b.earnings.gross_biweekly for b in breakdowns)
            bound = Decimal(count) * Decimal("0.005")
            assert abs(total - Decimal("91675.00")) <= bound, (
                f"a {cadence_days}-day cadence paid {total} of a "
                f"$91,675.00 salary over {len(breakdowns)} paychecks, "
                f"outside the {bound} rounding bound"
            )
            # And every paycheck is the SAME figure -- the rate contract
            # ruling R-HW states.  Without this the bound above would pass
            # for an engine that varied the gross period by period.
            assert len({b.earnings.gross_biweekly for b in breakdowns}) == 1
            # The identity F-16 needs on a pay list: a year's paydays sum
            # exactly to the reported yearly figure.  The first line is the
            # fixture's premise (it builds ``count`` paydays), asserted so the
            # sum is a year's; the second is the engine's property.
            assert len(breakdowns) == count
            assert total == breakdowns[0].earnings.annual_salary

    def test_the_same_profile_prices_differently_at_two_rhythms(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL: the cadence actually moves the answer.

        Input: ONE profile -- one pay entry of $3,525.96 a paycheck
        ($91,675.00 a year / 26, the owner's own 14-day rhythm) -- walked on a
        7-day calendar and on a 14-day one.
        Expected: the same $3,525.96 paycheck on both, counted 52 and 26 times
        a year, so the weekly calendar's yearly figure is twice the biweekly
        one's, exactly.
        Why: every assertion above would still pass if the engine ignored the
        cadence and hardcoded 26 for a 14-day-cadence fixture -- the suite
        would be green and the defect back.  This is the case that fails if
        the count stops being a function of the calendar argument, and it is
        the assertion the whole pre-R-F16 suite lacked: nothing varied this
        axis, so nothing could see a count that was not the schedule's.

        **Re-stated on the pay list's walk at plan step salary:X-av-3a.**  It
        priced one YEARLY salary on each calendar and asserted the paycheck
        was that salary over the calendar's count ($1,762.98 weekly against
        $3,525.96 biweekly).  The stored fact is one PAYCHECK now (ruling
        **R-SAL59**), so no calendar divides anything: what survives is that
        the count comes from the calendar the basis carries and the yearly
        figure is the paycheck times it.  Each calendar holds one era and the
        entry's payday (the owner's first saved payday, 2024-01-05) sits
        below both, where the earliest era runs backward, so the walk crosses
        no change of rhythm and carries nothing (R-SAL82).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = SalaryProfile(
                user_id=user_id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1,
                name="One profile, two rhythms",
                state_code="NC",
            )
            db.session.add(profile)
            # The owner's own rhythm on the entry's payday is 14 days.
            start_test_pay_list(profile, _PAY_OF_91675_AT[26])
            db.session.flush()

            weekly = _calendar(7, 52, user_id=user_id)
            biweekly = _calendar(14, 26, user_id=user_id)

            weekly_pay = PayrollBasis(profile, weekly).base_pay_on(
                weekly.periods[0].start_date,
            )
            biweekly_pay = PayrollBasis(profile, biweekly).base_pay_on(
                biweekly.periods[0].start_date,
            )

            # The paycheck is the entry on both calendars: nothing divides it.
            assert weekly_pay.per_paycheck == Decimal("3525.96")
            assert biweekly_pay.per_paycheck == Decimal("3525.96")
            # The count is each calendar's own.
            assert weekly_pay.periods_per_year == Decimal("52")
            assert biweekly_pay.periods_per_year == Decimal("26")
            # Hand-computed, exact (a product rounds nothing):
            # $3,525.96 x 52 = $183,349.92 and $3,525.96 x 26 = $91,674.96.
            assert weekly_pay.annual == Decimal("183349.92")
            assert biweekly_pay.annual == Decimal("91674.96")
            # Twice the paychecks, twice the year, to the cent.
            assert weekly_pay.annual == biweekly_pay.annual * 2


class TestWhichOwnerTheSeamSERVESAndWhichItREFUSES:
    """Deriving the count must not turn a rendering page into a 500.

    Plan step R-F16 put ``PayCalendar.cadence`` behind producers that
    previously needed no cadence at all, and two of them are read by pages that
    must render: the analytics Taxes tab, whose own contract documents
    degrading to an all-zero report "no crash", and the BALANCE SEAM, which the
    grid, /savings and /investments all read.  Both were measured raising
    during that step, and both were guarded with a
    ``cadence_days is not None`` test.

    **Plan step ``pay_calendar:C4-d`` (ruling R-PC45) DELETED both guards, and
    this class is the pair of controls that says what that cost and what it did
    not.**  It was ``TestAnOwnerWithNoCadenceIsSTILLSERVED``, and "no cadence"
    turned out to name two states that the guards could not tell apart:

    * **a schedule row and no PAYDAYS** -- ordinary, and the state
      ``compute_tax_report``'s contract actually names ("a user with profiles
      but no pay periods").  Both producers still serve it, and that is the
      guarantee R-F16 was protecting.  The old cases did not test it: they
      stripped the schedule row as well, so they measured the second state
      while quoting the first one's contract.
    * **no schedule row at all** -- the companion, production's user 2.  The
      developer ruled that this owner is REFUSED, once, at the calendar door,
      rather than served a different degraded render by each surface that
      happens to guard.  So the second half of this class asserts the refusal
      rather than the service.
    """

    def test_the_tax_report_still_degrades_to_zero_with_no_paydays(
        self, app, db, seed_user,
    ):
        """`/analytics` Taxes renders for an owner with a cadence and no paydays.

        Input: an active salary profile, then every pay period deleted and the
        ``budget.pay_schedule`` row LEFT IN PLACE.
        Expected: a report, not a ``PayCalendarError``.
        Why: ``compute_tax_report``'s docstring promises "a user with profiles
        but no pay periods degrades to an all-modeled zero report (no crash)",
        and R-F16 measured an unguarded ``calendar.cadence`` breaking exactly
        that promise.  This is the owner that contract names, and the promise
        is unchanged by plan step C4-d -- the cadence is READABLE for them, and
        there is simply no payday for the report to price.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = SalaryProfile(
                user_id=user_id, scenario_id=seed_user["scenario"].id,
                filing_status_id=1, name="No paydays",
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("1923.08"))  # $50,000.00 a year / 26
            _strip_every_payday_keeping_the_schedule(db, user_id)

            report = compute_tax_report(user_id, 2026, date(2026, 3, 1))

            assert report is not None
            assert report.withholding.total.gross == Decimal("0")

    def test_the_tax_report_REFUSES_an_owner_with_no_schedule_row(
        self, app, db, seed_user,
    ):
        """The state the ruling moved, asserted as the refusal it now is.

        This case inverted at plan step C4-d and is kept rather than deleted,
        because the inversion is the decision.  It asserted a report for an
        owner with no ``budget.pay_schedule`` row, which the deleted
        ``calendar.cadence if calendar.cadence_days is not None`` guard bought.
        That owner is refused now -- at ``calendar_for``, once, for every
        surface -- and the page they get is ``errors/no_pay_calendar.html``
        rather than an all-zero tax report that reads as a claim about their
        money.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = SalaryProfile(
                user_id=user_id, scenario_id=seed_user["scenario"].id,
                filing_status_id=1, name="No cadence",
                state_code="NC",
                is_active=True,
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("1923.08"))  # $50,000.00 a year / 26
            _strip_every_payday(db, user_id)

            with pytest.raises(PayCalendarError, match="no pay calendar"):
                compute_tax_report(user_id, 2026, date(2026, 3, 1))

    def test_the_balance_seam_serves_an_owner_with_a_cadence_and_no_paydays(
        self, app, db, seed_user, seed_periods,
    ):
        """The seam's contribution loader answers for an owner with no paydays.

        Input: an investment account with an ACTIVE deduction targeting it,
        then every pay period deleted and the schedule row LEFT IN PLACE.
        Expected: a :class:`ContributionInputs`, not a ``PayCalendarError``.
        Why: this loader is below the grid, which the grid, /savings and
        /investments all read.  R-F16 moved the deduction adaptation here --
        correctly, it is the ORM boundary -- and the adapter needs the paycheck
        count, so a producer that could not answer it would 500 all three.

        **The loader ANSWERS rather than raising, and that is the C4-d change**
        (ruling R-PC45).  The deleted guard was
        ``if ctx.calendar().cadence_days is not None else {}``, so this owner
        got an empty result only because the old fixture stripped their
        schedule row too; with the row in place the guard always passed and
        the loader always ran.

        **What is asserted changed at plan step salary:R14-b, and the old
        assertion has no successor.**  It read
        ``inputs.deductions[0].periods_per_year == Decimal("26")`` -- an EXACT
        number, because ``adapt_deductions`` stamped the owner's own paycheck
        count on every row and ``!= []`` had been measured blind by an
        adversarial review.  Nothing stamps a cadence now: the paycheck engine
        reads the owner's calendar itself, so there is no adapted copy of the
        count to check against.  What this case can still pin is the refusal
        it was written for -- that this owner is SERVED -- plus the shape of
        the answer: an owner with no payday has nothing to price, so the feed
        is empty rather than absent or raised.  It costs no FIGURE: with no
        payday there is no period for a contribution to be modelled over.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = _investment_account_with_an_active_deduction(
                db, seed_user, "401k",
            )
            _strip_every_payday_keeping_the_schedule(db, user_id)

            inputs = _contribution_inputs_for_accounts(
                [account], BalanceContext.build(user_id),
            )[account.id]

            # The owner is SERVED: a real bundle rather than a
            # ``PayCalendarError``, which is the whole subject of this case.
            #
            # **The feed assertions are shape, not non-vacuity, and saying so
            # is the point.**  A first draft claimed the params assertion
            # proved "the calendar read really happened"; it does not --
            # ``_inputs`` evaluates ``ctx.calendar()`` unconditionally as an
            # argument since plan step salary:R14-b, so no assertion here can
            # infer it, and ``employee_by_payday == {}`` (the assertion that
            # stood here until plan step salary:S3-e-2) was the ``!= []``
            # class an adversarial review already rejected on this exact
            # case.  What grades the refusal is the SIBLING case below, over
            # an owner with no schedule row at all.  A ``models_employee is
            # False`` line stood beside the map assertion until plan step
            # salary:S3-e-1 deleted that property; it asserted nothing the
            # empty map does not already imply.
            #
            # **The feed is LINKED, on both sides of salary:S3-e-2.**  The
            # fixture's deduction names this account; the flag was CARRIED
            # off the deduction rows before that step and is the employee
            # resolver's presence after it, and both read ``True`` here.
            # What the empty map asserted was that no PAYDAY existed to key
            # it by, which the resolver has no shape for; the sibling case
            # below pins the one presence fact an empty calendar DID move.
            assert inputs.investment_params is not None
            assert inputs.feed.is_payroll_linked is True

    def test_a_named_funding_profile_funds_the_employer_side_with_no_payday(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``funds_employer`` is the funding profile's presence, not a payday's.

        **The one presence fact an empty calendar moved at plan step
        salary:S3-e-2**, found by that step's adversarial review.  The feed
        answered ``bool(gross_by_payday)`` before it -- ``False`` for an
        owner who had NAMED an active funding profile but recorded no
        payday, because the map had no key to hold -- and answers the gross
        resolver's presence now, which the loader builds from the profile
        link alone.  Three readers turn on it: ``/investment``'s "funding
        job is not set" notice (``_cards._compute_employer_funding``) stops
        being shown to an owner whose job IS set, ``calculate_investment_
        inputs`` stops withholding ``employer_params``, and the balance seam
        builds a contribution plan for the account.  No figure moves -- with
        no payday there is no period for any of them to price -- so this
        pins the flag itself, on the fixture that can see it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = _investment_account_with_an_active_deduction(
                db, seed_user, "401k-employer",
            )
            params = (
                db.session.query(InvestmentParams)
                .filter_by(account_id=account.id).one()
            )
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=user_id, name="401k-employer profile")
                .one()
            )
            params.salary_profile_id = profile.id
            params.employer_contribution_type_id = (
                ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.FLAT_PERCENTAGE,
                )
            )
            params.employer_flat_percentage = Decimal("0.05")
            _strip_every_payday_keeping_the_schedule(db, user_id)

            ctx = BalanceContext.build(user_id)
            inputs = _contribution_inputs_for_accounts([account], ctx)[account.id]

            assert not ctx.reported_periods()
            assert inputs.feed.funds_employer is True
            assert inputs.feed.is_payroll_linked is True

    def test_the_balance_seam_REFUSES_an_owner_with_no_schedule_row(
        self, app, db, seed_user, seed_periods,
    ):
        """The state the ruling moved, asserted as the refusal it now is.

        This case inverted at plan step C4-d and is kept rather than deleted.
        It asserted ``inputs.deductions == []`` for an owner with no
        ``budget.pay_schedule`` row, which the deleted
        ``ctx.calendar().cadence_days is not None`` guard bought -- and the
        seam is exactly where that guard mattered, because the grid, /savings
        and /investments all read through it.

        The owner is refused now, once, at ``calendar_for``, which is what the
        developer ruled: one answer for one state instead of a different
        degraded render per surface.  Every route reaching this seam carries
        ``@require_owner``, and the only owner with no schedule row is the
        companion, whom that decorator 404s first -- so no live page changes.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = _investment_account_with_an_active_deduction(
                db, seed_user, "401k refused",
            )
            _strip_every_payday(db, user_id)

            with pytest.raises(PayCalendarError, match="no pay calendar"):
                _contribution_inputs_for_accounts(
                    [account], BalanceContext.build(user_id),
                )


class TestTheSecondCountIsGone:
    """No door can state a paycheck count beside the owner's cadence."""

    def test_the_column_does_not_exist(self, app):
        """``salary_profiles`` holds no ``pay_periods_per_year``.

        Input: the live test database's schema, which is built by running the
        migrations.
        Expected: the column is absent.
        Why: the ORM model is not the oracle here -- ``build_test_template.py``
        runs MIGRATIONS, so a model edit with no migration leaves the schema
        untouched and every ORM-driven test still passes.  This asks the
        database (recorded as a trap in this project's own operating notes).
        """
        with app.app_context():
            columns = {
                c["name"] for c in
                inspect(_db.engine).get_columns(
                    "salary_profiles", schema="salary",
                )
            }
            assert "pay_periods_per_year" not in columns
            # The census reached the table.  The witness was ``annual_salary``
            # until plan step salary:X-av-3a dropped that column for
            # ``salary.pay_entries``; ``filing_status_id`` is a live column of
            # the same table that no step in flight removes.
            assert "filing_status_id" in columns

    def test_the_columns_check_constraint_is_gone_too(self, app):
        """``ck_salary_profiles_positive_periods`` is dropped with its column.

        Why: a CHECK left behind on a dropped column is not merely untidy --
        it is the residue that makes a downgrade fail halfway, and the
        migration's own downgrade re-creates it by name.  Asked of
        ``pg_constraint`` so it is the database answering.
        """
        with app.app_context():
            found = _db.session.execute(text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname = 'ck_salary_profiles_positive_periods'"
            )).fetchall()
            assert found == []

    def test_a_basis_cannot_be_built_without_a_cadence(self):
        """:class:`PayrollBasis` REFUSES to exist without a rhythm.

        Input: the type constructed with a profile alone.
        Expected: ``TypeError`` -- the field has no default.
        Why: the count is undefaultable BY CONSTRUCTION, which is the whole
        claim the type makes. A defaulted cadence would model a weekly-paid
        owner's income at half its true value and say nothing, which is the
        same argument the read-pass ruling makes for ``BalanceContext``.

        **Asserted on the constructor rather than on the engine**, which is
        where an earlier draft put it: passing a bare ``SalaryProfile`` to
        ``calculate_paycheck`` raises ``AttributeError`` on ``basis.profile``,
        and that is a duck-typing accident -- it would stop testing anything
        the day ``SalaryProfile`` gained a ``profile`` attribute.
        """
        with pytest.raises(TypeError):
            PayrollBasis(object())  # pylint: disable=no-value-for-parameter
