"""
Tests for the investment projection helper.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.services.investment_projection import (
    AccountPayrollFeed,
    build_contribution_timeline,
    calculate_investment_inputs,
    InvestmentInputs,
    PricedContribution,
)
from app.services.pay_calendar import PayCalendar

from tests._test_helpers import (
    eras_of,
)

#: The read pass's clock for every timeline case here.  It is an ARGUMENT since
#: plan step C2-f2c -- ``build_contribution_timeline`` read ``date.today()``
#: until then -- so the confirmed / projected split below is decided by a
#: literal rather than by when the suite happens to run.
_AS_OF = date(2026, 6, 15)


def _priced(amount, payday, *, is_confirmed=False, account_id=1):
    """Build one :class:`PricedContribution`, the module's real input type.

    Since plan step X-au-c2 this module consumes records that were VALUED and
    SCREENED at the boundary (``recorded_contributions.load_shadow_income_
    contributions_*``), not ORM rows -- so the two hand-rolled transaction
    fakes that used to mirror ``effective_amount`` and carry a status are gone,
    and with them the risk of a fake drifting from the model it imitated.

    The rules those fakes exercised did not disappear with them; they MOVED,
    and their tests moved with them to ``test_projection_inputs.py``, where the
    boundary is exercised against real rows: a settled shadow whose actual
    differs from its estimate is priced at the actual, and a Cancelled or
    Credit row is DROPPED rather than carried at zero.

    **The period key is the PAYDAY since plan step C2-f2c**, resolved at that
    same boundary.  It was the ``pay_period_id``, which every reader here then
    had to translate into a date by looking it up in a period list the caller
    supplied -- see the module under test.
    """
    return PricedContribution(
        account_id=account_id,
        payday=payday,
        amount=Decimal(str(amount)),
        is_confirmed=is_confirmed,
    )


def _periods(*paydays, cadence=14):
    """Return REAL :class:`DerivedPeriod`s opening on *paydays*.

    The ``FakePeriod`` these replaced carried an ``id`` this module no longer
    reads and a ``period_index`` it never read, and nothing held it to the
    shape of a period the application can actually produce -- one case built
    three periods that all opened on the same day, which
    :func:`~app.services.pay_calendar.derive_periods` refuses outright.  A real
    calendar's own window is the type ``/investment`` supplies since plan step
    C2-f2c and is structurally what ``/retirement``'s ORM rows are, so a case
    written over it grades both callers.
    """
    pairs = [(index, payday) for index, payday in enumerate(paydays, start=1)]
    return PayCalendar.from_paydays(
        pairs, eras_of(pairs, cadence), user_id=1, history_opens_on=None,
    ).saved()


def _axis(*paydays, cadence=14, projected=0):
    """Return REAL periods: the saved ones on *paydays*, then *projected* more.

    The saved half is what :func:`_periods` builds; the projected half comes
    off :meth:`~app.services.pay_calendar.PayCalendar.projection_axis`, the
    producer every projecting surface uses, so each projected period carries
    ``period_id = None`` and answers
    :attr:`~app.services.pay_calendar.DerivedPeriod.is_projected` ``True``
    the way production's do.  That accessor is the timeline's boundary since
    plan step **salary:S3-e-2** (ruling **R-SAL18**), so a case grading the
    boundary needs periods on both sides of it that the app itself can
    produce -- a fake with the flag set by hand would grade the flag's
    spelling and not the derivation.
    """
    pairs = [(index, payday) for index, payday in enumerate(paydays, start=1)]
    calendar = PayCalendar.from_paydays(
        pairs, eras_of(pairs, cadence), user_id=1, history_opens_on=None,
    )
    last = calendar.horizon() + timedelta(days=cadence * projected)
    window = calendar.projection_axis(calendar.opening_bound(), last)
    assert sum(1 for period in window if period.is_projected) == projected
    return window


def _feed(periods=(), *, employee=None, gross=None):
    """Build an :class:`AccountPayrollFeed` answering over *periods*' paydays.

    **The input type since plan step salary:R14-b** (ruling **R-SAL2**), in
    place of the ``FakeDeduction`` this file built for every case.  That fake
    carried ``(amount, calc_method_id, annual_salary, periods_per_year,
    annual_cap)`` -- the five fields ``adapt_deductions`` flattened a real
    deduction into -- and every case then asserted what THIS module derived
    from them.  It derives nothing now: the paycheck engine prices a
    deduction when it prices the paycheck, and the feed asks the engine's
    pricer per period through a resolver the loader built (plan step
    **salary:S3-e-2**, ruling **R-SAL15**).  So each resolver here is a
    TABLE keyed by the period's ``start_date`` -- the same figures the real
    one would read off the engine, without the engine -- and a figure given
    as a scalar answers every payday of *periods*.  The loader's real
    resolvers are graded against real rows in ``test_projection_inputs.py``.

    Args:
        periods: The periods whose paydays the tables cover.
        employee: What payroll puts in per payday -- one figure for every
            payday, or a ``{payday: amount}`` map.  ``None`` means NO
            employee resolver, which is what
            :attr:`AccountPayrollFeed.is_payroll_linked` reads ``False``
            for; a table of zeros is a LINKED deduction pricing ``$0.00``,
            and the two behave differently in the timeline.
        gross: The funding profile's gross per payday, same two forms.
            ``None`` means no funding profile is known, which is what
            :attr:`AccountPayrollFeed.funds_employer` reports ``False`` for.

    Returns:
        The :class:`AccountPayrollFeed`.
    """
    paydays = [period.start_date for period in periods]

    def _resolver(value):
        if value is None:
            return None
        if isinstance(value, dict):
            table = {day: Decimal(str(amount)) for day, amount in value.items()}
        else:
            table = {day: Decimal(str(value)) for day in paydays}
        return lambda period: table[period.start_date]

    return AccountPayrollFeed(employee=_resolver(employee), gross=_resolver(gross))

def _emp_type_id(member):
    """Resolve an EmployerContributionTypeEnum member to its ref-table id (#38)."""
    return ref_cache.employer_contribution_type_id(member)


#: The per-period gross the employer cases size a percentage off.  It was
#: ``$100,000 / 26`` derived from a fake deduction's salary until plan step
#: salary:R14-b; it is stated on the feed now, because a gross is a fact about
#: a PAYDAY and no longer something this module divides for itself.
_GROSS_BIWEEKLY = Decimal("3846.15")


@dataclass
class FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type_id: int
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


class TestAccountPayrollFeed:
    """The feed's own rules: two resolvers, and the two presence facts they carry.

    Plan steps **salary:R14-b** and **S3-e-2**.  What each figure IS -- the
    raise, the inflation escalation, the cadence, the calendar-year cap -- is
    the paycheck engine's, graded in the ``test_paycheck_calculator/`` package; the
    resolvers the loader builds over the engine's pricer are graded in
    ``test_projection_inputs.py`` against real rows, including the one thing
    this step exists for, a period PAST the saved schedule priced rather
    than held.  What is left for the value itself is small and stated here:
    it hands a resolver the PERIOD, and it derives both presence facts from
    the resolvers' presence.
    """

    def test_each_period_answers_with_its_OWN_figure(self):
        """The feed is a SERIES, which is finding D45's whole remedy.

        The feed it replaced was one scalar for every period, so an owner with
        a raise had every projected paycheck priced at one paycheck's answer.
        The figures here are the developer's own measured pair (ledger row
        **D45**): a gross of ``$3,525.96`` before the 2026-07 raise and
        ``$3,631.74`` after it.
        """
        periods = _periods(date(2026, 6, 4), date(2026, 7, 2))
        feed = _feed(periods, gross={
            periods[0].start_date: Decimal("3525.96"),
            periods[1].start_date: Decimal("3631.74"),
        })
        assert feed.gross_at(periods[0]) == Decimal("3525.96")
        assert feed.gross_at(periods[1]) == Decimal("3631.74")

    def test_a_resolver_is_handed_the_PERIOD_itself(self):
        """Both accessors pass the period through, not its payday (R-SAL19).

        The engine prices a :class:`~app.services.pay_calendar.DerivedPeriod`
        and reads its ``period_id`` as well as its ``start_date``, so a feed
        that peeled the date off and re-derived the period inside would be a
        round trip plus a fence.  The resolvers receive the very object the
        caller held; a projected one arrives with ``is_projected`` intact.
        """
        seen = []
        saved, projected = _axis(date(2026, 1, 2), projected=1)
        feed = AccountPayrollFeed(
            employee=lambda period: seen.append(period) or Decimal("1"),
            gross=lambda period: seen.append(period) or Decimal("2"),
        )
        assert feed.employee_at(saved) == Decimal("1")
        assert feed.gross_at(projected) == Decimal("2")
        assert seen[0] is saved
        assert seen[1] is projected
        assert seen[1].is_projected is True

    def test_presence_is_the_resolvers_presence(self):
        """Linked and funded are DERIVED, one home each, never carried.

        Plan step salary:S3-e-1 made ``is_payroll_linked`` a required carried
        field because two dictionaries could disagree with it; S3-e-2 makes
        it the employee resolver's presence, and ``funds_employer`` the gross
        resolver's, so no constructor can hand the value a wrong answer.
        A linked deduction pricing ``$0.00`` is a resolver that answers zero,
        which is a different value from no resolver at all.
        """
        periods = _periods(date(2026, 1, 2))
        both = _feed(periods, employee=Decimal("0"), gross=Decimal("3846.15"))
        assert both.is_payroll_linked is True
        assert both.funds_employer is True
        employee_only = _feed(periods, employee=Decimal("500"))
        assert employee_only.is_payroll_linked is True
        assert employee_only.funds_employer is False
        employer_only = _feed(periods, gross=Decimal("3846.15"))
        assert employer_only.is_payroll_linked is False
        assert employer_only.funds_employer is True

    def test_no_funding_profile_refuses_a_gross_rather_than_answering_zero(self):
        """An unknown funding job answers ``None``, so no caller can spend it.

        The developer's 2026-09-04 ruling: an employer contribution whose
        funding job is unrecorded models NO money.  ``None`` rather than
        ``$0.00`` because a zero is a basis a percentage can be taken of, and
        the point is that there is no basis.
        """
        periods = _periods(date(2026, 1, 2))
        feed = _feed(periods, employee=Decimal("500"))
        assert feed.funds_employer is False
        assert feed.gross_at(periods[0]) is None

    def test_an_unlinked_account_answers_zero_without_a_resolver(self):
        """No employee resolver prices ``$0.00``; it does not raise or hold."""
        periods = _periods(date(2026, 1, 2))
        feed = _feed(periods, gross=Decimal("3846.15"))
        assert feed.employee_at(periods[0]) == Decimal("0")

    def test_absent_models_neither_half(self):
        """The explicit token for an account no payroll funds."""
        period = _periods(date(2026, 1, 2))[0]
        feed = AccountPayrollFeed.absent()
        assert feed.is_payroll_linked is False
        assert feed.funds_employer is False
        assert feed.employee_at(period) == Decimal("0")
        assert feed.gross_at(period) is None
        assert feed == AccountPayrollFeed(employee=None, gross=None)

class TestCalculateInvestmentInputs:
    """What the two dashboards' per-period CARDS read.

    **This is no longer the forward walk's input** (plan step
    **salary:R14-b**): ``periodic_contribution`` was one raise-blind scalar the
    whole projection ran on, and the walk reads a dated record per period now
    (:func:`build_contribution_timeline`).  What survives here is the current
    paycheck's figure, the two YTD windows and the employer-params shape.

    **The cases that priced a deduction went with the arithmetic.**  Flat
    versus percentage, the half-cent rounding, a two-job owner's per-profile
    salary, a weekly owner's 52 paychecks, and the calendar-year cap's even
    spread were all this module deriving what the paycheck engine derives.
    The engine's own suites grade it: the ``test_paycheck_calculator/``
    package grades a paycheck priced from the pay, raises and lines a profile
    carries, the raise and the inflation escalation this module could not see
    among them, and ``test_paycheck_count_derivation.py`` grades the
    paycheck count at every rhythm, a weekly owner's 52 among them.  The even
    spread has no successor anywhere and is not meant to
    have one: it was the third of four spellings of one cap, and the engine's
    front-loaded clamp is the one that survives (ruling **R-SAL2**).
    """

    def test_no_feed_no_transfers(self):
        """No payroll feed or transfers -> zero contributions and zero YTD."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=[], current_period=current_period,
        )
        assert result.periodic_contribution == Decimal("0")
        assert result.employer_params is None
        assert result.ytd_contributions == Decimal("0")
        assert result.annual_contribution_limit == Decimal("23500")

    def test_the_card_reads_the_CURRENT_periods_figure(self):
        """The per-period card is the paycheck the owner is being paid.

        It was one figure for all time, so a card rendered in July showed the
        March paycheck's deduction.  The two paydays here differ by the
        developer's own 2026-07 raise applied to a 6% deduction.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(date(2026, 6, 4), date(2026, 7, 2))
        # 6% of $3,525.96 and of $3,631.74, as the engine rounds each.
        feed = _feed(periods, employee={
            periods[0].start_date: Decimal("211.56"),
            periods[1].start_date: Decimal("217.90"),
        })
        before = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=periods[0],
        )
        after = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=periods[1],
        )
        assert before.periodic_contribution == Decimal("211.56")
        assert after.periodic_contribution == Decimal("217.90")

    def test_transfer_contributions_averaged(self):
        """Transfer contributions averaged across distinct periods with transfers."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=None,
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("200"), periods[1].start_date),
            _priced(Decimal("300"), periods[2].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=periods[0],
        )
        # ($200 + $200 + $300) over THREE distinct paydays = $233.33.
        assert result.periodic_contribution == Decimal("233.33")

    def test_employer_flat_percentage(self):
        """Employer flat_percentage populates employer_params with correct values.

        **The dict no longer carries a gross** (plan step salary:R14-b): one
        figure sizing every period's employer contribution is what froze a 5%
        match at today's paycheck for the life of a projection.  The period's
        own comes off the feed, which
        :class:`TestAccountPayrollFeed` grades.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.FLAT_PERCENTAGE,
        )
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        assert "gross_biweekly" not in result.employer_params

    def test_employer_match(self):
        """Employer match type populates match_percentage and cap fields."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.MATCH,
            ),
            employer_match_percentage=Decimal("1.0"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.employer_params["type_id"] == _emp_type_id(
            EmployerContributionTypeEnum.MATCH,
        )
        assert result.employer_params["match_percentage"] == Decimal("1.0")
        assert result.employer_params["match_cap_percentage"] == Decimal("0.06")

    def test_a_configured_employer_contribution_is_WITHHELD_when_unfunded(self):
        """No known funding job models no employer money (developer, 2026-09-04).

        The params are configured -- a 5% flat contribution -- and the feed
        priced no gross because ``investment_params.salary_profile_id`` names
        no active profile of this owner's.  The dict is withheld rather than
        paired with a basis of zero, so the growth engine's employer arm
        cannot run at all and the surface can say WHY (the two states stay
        distinguishable: ``employer_params is None`` says no money,
        ``feed.funds_employer`` says which reason).
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], employee=Decimal("500"))
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert feed.funds_employer is False
        assert result.employer_params is None
        # The EMPLOYEE half is untouched: the ruling is about the employer's
        # money, and the owner's own deduction happened whatever the app knows
        # about which job funds the match.
        assert result.periodic_contribution == Decimal("500")

    def test_ytd_contributions_from_transfers(self):
        """YTD contributions sum only current-year contributions up to current period."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(
            date(2025, 12, 19), date(2026, 1, 2), date(2026, 1, 16),
            date(2026, 1, 30), date(2026, 2, 13),
        )
        contributions = [
            _priced(Decimal("500"), period.start_date) for period in periods
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=periods[3],
        )
        # The 2025 payday is a different calendar year and 2026-02-13 is past
        # the current period, so three of the five count.
        assert result.ytd_contributions == Decimal("1500")

    def test_ytd_contributions_seed_excludes_current_period(self):
        """deep-hunt #10: the engine seed YTD is STRICTLY BEFORE the current period.

        Same setup as ``test_ytd_contributions_from_transfers``: five $500
        contributions, current = periods[3] (start 2026-01-30).
        Period 1 is in 2025 (different calendar year); periods 2-4 are in
        2026 up to and including the current period.

        * ``ytd_contributions`` (the displayed limit-card value, ``<=``)
          sums periods 2, 3, 4 = $1,500 (unchanged).
        * ``ytd_contributions_seed`` (the engine seed, ``<``) sums periods
          2 and 3 only = $1,000 -- the current period's $500 is excluded
          because the growth engine's own walk applies and counts it.
          Seeding $1,500 instead would charge the current period against
          the annual limit twice.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(
            date(2025, 12, 19), date(2026, 1, 2), date(2026, 1, 16),
            date(2026, 1, 30), date(2026, 2, 13),
        )
        contributions = [
            _priced(Decimal("500"), period.start_date) for period in periods
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=periods[3],
        )
        assert result.ytd_contributions == Decimal("1500")          # <= current (display)
        assert result.ytd_contributions_seed == Decimal("1000")     # < current (engine seed)

    def test_ytd_contributions_seed_none_current_period(self):
        """deep-hunt #10: a None current period yields a ZERO engine seed."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        contributions = [_priced(Decimal("500"), date(2026, 1, 2))]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=None,
        )
        assert result.ytd_contributions_seed == Decimal("0")

    def test_combined_payroll_and_transfers(self):
        """The payroll feed and the transfer average both reach the card."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        feed = _feed(periods, employee=Decimal("500.00"))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("200"), periods[1].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=contributions, current_period=periods[0],
        )
        assert result.periodic_contribution == Decimal("700.00")

    def test_employer_params_stand_without_an_employee_feed(self):
        """An employer FLAT percentage models money with a zero employee feed.

        The real Empower 401(k) shape: no deduction names the account, so the
        employee half is absent, and the employer half is priced off the
        funding profile's own paycheck all the same.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.05"),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is not None
        assert result.periodic_contribution == Decimal("0")
        assert feed.gross_at(current_period) == _GROSS_BIWEEKLY

    def test_no_employer_when_type_none(self):
        """Employer type 'none' produces employer_params=None."""
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"), annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        current_period = _periods(date(2026, 3, 5))[0]
        feed = _feed([current_period], gross=_GROSS_BIWEEKLY)
        result = calculate_investment_inputs(
            investment_params=params, feed=feed,
            all_contributions=[], current_period=current_period,
        )
        assert result.employer_params is None

    def test_no_current_period_and_nothing_loaded_does_not_crash(self):
        """A fresh user -- no periods, so no current period -- still answers.

        The function returns a valid InvestmentInputs with zero contributions
        and zero YTD.  Named for ``current_period`` alone since plan step
        C2-f2c: the empty period LIST this also passed is no longer an
        argument, so a name promising to vary it would promise coverage the
        case cannot give.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=[], current_period=None,
        )
        assert isinstance(result, InvestmentInputs)
        assert result.periodic_contribution == Decimal("0")
        assert result.ytd_contributions == Decimal("0")
        assert result.employer_params is None

    def test_pre_filtered_contributions_only(self):
        """Only non-deleted contributions for this account are passed in.

        The caller pre-filters deleted contributions and contributions for
        other accounts before calling calculate_investment_inputs.  This test
        verifies that a single valid contribution produces the correct
        periodic and YTD values.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=periods[0],
        )
        # 1 contribution across 1 period -- periodic = $200
        assert result.periodic_contribution == Decimal("200")
        # YTD only includes current_period=periods[0], which has the $200 contribution
        assert result.ytd_contributions == Decimal("200")

    def test_none_current_period_with_contributions(self):
        """None current_period skips YTD calculation but still averages contributions.

        When current_period is None (e.g., no period is current), the
        function should still compute periodic_contribution from contributions
        but set ytd_contributions to 0.
        """
        params = FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=_emp_type_id(EmployerContributionTypeEnum.NONE),
        )
        periods = _periods(date(2026, 1, 2), date(2026, 1, 16))
        contributions = [
            _priced(Decimal("200"), periods[0].start_date),
            _priced(Decimal("400"), periods[1].start_date),
        ]
        result = calculate_investment_inputs(
            investment_params=params, feed=AccountPayrollFeed.absent(),
            all_contributions=contributions, current_period=None,
        )
        # (200 + 400) / 2 periods = 300
        assert result.periodic_contribution == Decimal("300")
        assert result.ytd_contributions == Decimal("0")


# The ESTIMATED-vs-EFFECTIVE alignment class (deep-quality-hunt #11) lived
# here and MOVED WHOLE to ``test_projection_inputs.TestShadowContributionBoundary``
# at plan step X-au-c2, with its four cases and their hand-computed figures
# intact.  It pinned that a settled shadow whose ``actual_amount`` differs from
# its ``estimated_amount`` is read at the ACTUAL by the averaged inputs feed,
# by the YTD/limit accounting, and by the per-period timeline -- all three, so
# the cap math cannot read a different dollar than the growth engine applies.
#
# That rule did not weaken; it moved down a tier.  This module no longer values
# anything: it consumes :class:`PricedContribution` records that were valued at
# the boundary, so asserting the rule HERE would only assert that a record
# carrying $400 averages to $400.  The boundary tests grade it against real
# rows, which is where it can still fail.  What survives here, structurally, is
# the AGREEMENT half: every feed in this module reads one ``amount`` field off
# one record, so two of them pricing a row differently is no longer expressible.


class TestBuildContributionTimeline:
    """Tests for build_contribution_timeline().

    Verifies that the function correctly combines the payroll feed and the
    transfer-based contributions into a unified ContributionRecord list,
    with correct amounts, is_confirmed semantics, and sorting.

    **Path 1 computes nothing since plan step salary:R14-b**: it reads the
    feed's per-payday figure, which the paycheck engine priced.  The cases
    that graded its arithmetic -- a flat amount, a percentage of gross, two
    deductions summed, and the whole ``TestBuildContributionTimelineAnnualCap``
    class -- went with it.  The cap is the one to be explicit about: it was a
    private year-state walk here (``_period_capped_total``) reproducing what
    ``paycheck_calculator._calculate_deductions`` applies through the SAME
    ``cap_period_amount``, and the ``test_paycheck_calculator/`` package grades
    that one: ``test_deductions.py``'s ``TestDeductionAnnualCap``, and in
    ``test_raises.py`` a capped deduction whose year to date is priced through
    a raise this one could not see.
    """

    def test_payroll_only(self):
        """A payroll feed with no transfers: one record per period."""
        periods = _periods(
            date(2020, 1, 2), date(2020, 1, 16), date(2020, 1, 30),
        )
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500.00")),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 3
        for record in result:
            assert record.amount == Decimal("500.00")
            assert isinstance(record, ContributionRecord)

    def test_a_record_is_emitted_for_every_period_including_a_zero(self):
        """A ``$0`` period is an explicit record, not a missing one.

        The difference is load bearing: a MISSING record is what makes
        ``growth_engine.project_balance`` fall back to
        ``periodic_contribution``, so a cadence-skipped payday emitted as a
        gap would be paid the current period's amount instead of nothing.
        """
        periods = _periods(
            date(2020, 1, 2), date(2020, 1, 16), date(2020, 1, 30),
        )
        feed = _feed(periods, employee={
            periods[0].start_date: Decimal("500"),
            periods[1].start_date: Decimal("500"),
            periods[2].start_date: Decimal("0"),
        })
        result = build_contribution_timeline(
            feed=feed, contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 3
        assert result[2].amount == Decimal("0")

    def test_a_period_PAST_the_schedule_is_priced_by_the_feed(self):
        """The timeline's domain may run past the owner's saved schedule.

        The 40-year chart's axis does, which is why the timeline is assembled
        where the axis is known (plan step salary:R14-b).  Every projected
        period gets a record, so the raise-blind ``periodic_contribution``
        fallback that step deleted has nothing left to answer -- and since
        plan step **salary:S3-e-2** the record carries what the feed PRICES
        for that period, not a figure held from the last saved one.
        """
        axis = _axis(date(2020, 1, 3), date(2020, 1, 17), projected=1)
        feed = _feed(axis, employee={
            axis[0].start_date: Decimal("500.00"),
            axis[1].start_date: Decimal("500.00"),
            # The projected period prices its OWN figure -- a raise landed.
            axis[2].start_date: Decimal("525.00"),
        })
        result = build_contribution_timeline(
            feed=feed, contribution_transactions=[],
            periods=axis, as_of=_AS_OF,
        )
        assert len(result) == 3
        assert result[2].contribution_date == axis[2].start_date
        assert result[2].amount == Decimal("525.00")

    def test_the_transfer_average_boundary_is_the_PERIODS_own(self):
        """The average is added on a PROJECTED period and on no saved one.

        **Ruling R-SAL18, and the control plan step salary:S3-e-2 installs
        in place of the one S3-e-1 did.**  The term was gated on
        ``feed.prices(payday)`` -- *did the engine price this day* -- which
        answered the same days as *has the schedule reached this day* only
        while the feed was built over ``calendar.saved()``; S3-e-1 moved the
        boundary to a date the CALLER read off a calendar, fenced by an AST
        census over the two call sites.  Now the feed answers every period
        of the axis and the period itself says which side of the schedule it
        is on, so the case makes the feed and the schedule DISAGREE on
        purpose -- every period is priced, two are projected -- and asserts
        the period decides.

        **Graded against two mutations 2026-09-11.**  Adding the average on
        every period (the pre-S3-e-1 hazard, a feed that "prices" everything)
        fails the two saved assertions; adding it on none fails the two
        projected ones.  So the case sees both directions of the boundary.
        """
        axis = _axis(date(2020, 1, 3), date(2020, 1, 17), projected=2)
        assert [period.is_projected for period in axis] == [
            False, False, True, True,
        ]
        # PRICED over the whole axis, projected periods included.
        feed = _feed(axis, employee=Decimal("500.00"))
        # $300 and $100 over two distinct paydays averages $200.
        txns = [
            _priced(Decimal("300"), axis[0].start_date, is_confirmed=True),
            _priced(Decimal("100"), axis[1].start_date, is_confirmed=True),
        ]
        result = build_contribution_timeline(
            feed=feed, contribution_transactions=txns,
            periods=axis, as_of=_AS_OF,
        )
        payroll = {
            record.contribution_date: record.amount
            for record in result
            if record.amount in (Decimal("500.00"), Decimal("700.00"))
        }
        assert payroll[axis[0].start_date] == Decimal("500.00"), (
            "saved: deduction alone"
        )
        assert payroll[axis[1].start_date] == Decimal("500.00"), (
            "saved: deduction alone"
        )
        assert payroll[axis[2].start_date] == Decimal("700.00"), (
            "projected: the $200 transfer average is added, even though the "
            "feed priced this period"
        )
        assert payroll[axis[3].start_date] == Decimal("700.00")
    def test_transfer_only(self):
        """Shadow income transactions with no payroll: one record per transaction."""
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        txns = [
            _priced(Decimal("200"), periods[0].start_date, is_confirmed=True),
            _priced(Decimal("300"), periods[1].start_date, is_confirmed=True),
        ]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 2
        assert result[0].amount == Decimal("200")
        assert result[1].amount == Decimal("300")

    def test_an_UNLINKED_account_emits_NO_path_one_records(self):
        """No deduction wired up: the fallback stays the answer.

        ``is_payroll_linked`` is the gate, and it reads ``False`` here, so the
        transfer average answers a period with no transfer -- which is the
        behaviour an account funded only by transfers relies on.
        """
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        result = build_contribution_timeline(
            feed=_feed(periods),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert result == []

    def test_a_LINKED_feed_pricing_zero_still_emits_its_zeros(self):
        """A wired-up deduction that prices $0.00 suppresses the fallback.

        The gate is PRESENCE and not price, which an adversarial review of
        this step corrected.  A deduction fully consumed by its ``annual_cap``
        across the whole priced window prices ``$0.00`` on every payday while
        being genuinely configured; gating on the price emitted no records,
        and the engine then applied the TRANSFER AVERAGE to periods that
        should contribute nothing.  The explicit zeros are what stop it.
        """
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        # A resolver that answers zero IS a linked deduction (plan step
        # salary:S3-e-2 derives the flag from the resolver's presence).
        feed = _feed(periods, employee=Decimal("0"))
        result = build_contribution_timeline(
            feed=feed,
            contribution_transactions=[
                _priced(Decimal("300"), periods[0].start_date),
            ],
            periods=periods, as_of=_AS_OF,
        )
        payroll = [r for r in result if r.amount == Decimal("0")]
        assert len(payroll) == 2, (
            "both priced paydays must carry an explicit $0 record"
        )

    def test_both_paths_summed(self):
        """Payroll and transfer on the same period produce separate records.

        The growth engine's lookup dict aggregates same-date records.
        $500 payroll + $200 transfer on period 1.
        """
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500.00")),
            contribution_transactions=txns, periods=periods, as_of=_AS_OF,
        )
        # One record from payroll, one from the transfer, same date.
        assert len(result) == 2
        total = sum(r.amount for r in result)
        assert total == Decimal("700.00")

    def test_is_confirmed_payroll_past(self):
        """Payroll for a past period: is_confirmed=True."""
        # Before the pass's clock, by a literal rather than by when this runs.
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500")),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_payroll_future(self):
        """Payroll for a future period: is_confirmed=False."""
        # After the pass's clock, by a literal rather than by when this runs.
        periods = _periods(date(2099, 1, 2))
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500")),
            contribution_transactions=[], periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_transfer_settled(self):
        """Settled shadow transaction: is_confirmed=True."""
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is True

    def test_is_confirmed_transfer_projected(self):
        """Projected shadow transaction: is_confirmed=False."""
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=False)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].is_confirmed is False

    def test_is_confirmed_mixed_same_date(self):
        """Confirmed payroll + projected transfer on same date.

        Both produce records for the same date.  The growth engine's
        lookup dict applies the conservative rule (all must be confirmed).
        Here we verify both records are produced -- one True, one False.
        """
        # Past date so the payroll record is confirmed.
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), periods[0].start_date, is_confirmed=False)]
        result = build_contribution_timeline(
            feed=_feed(periods, employee=Decimal("500")),
            contribution_transactions=txns, periods=periods, as_of=_AS_OF,
        )
        assert len(result) == 2
        confirmed_flags = {r.is_confirmed for r in result}
        assert True in confirmed_flags   # Payroll (past).
        assert False in confirmed_flags  # Transfer (projected).

    def test_empty_both(self):
        """No payroll feed and no transactions: empty list returned."""
        periods = _periods(date(2020, 1, 2))
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=[],
            periods=periods, as_of=_AS_OF,
        )
        assert result == []

    def test_sorted_output(self):
        """Output is sorted by contribution_date regardless of input order."""
        periods = _periods(date(2020, 1, 2), date(2020, 1, 16))
        txns = [
            _priced(Decimal("300"), periods[1].start_date, is_confirmed=True),
            _priced(Decimal("100"), periods[0].start_date, is_confirmed=True),
        ]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        dates = [r.contribution_date for r in result]
        assert dates == sorted(dates)

    def test_emits_the_record_amount_untransformed(self):
        """A record's priced amount reaches its ContributionRecord unchanged.

        Renamed at plan step X-au-c2: it graded ``effective_amount``, an
        accessor this module no longer touches.  What it can still pin is that
        the timeline does not round, scale or re-derive the figure the boundary
        priced -- and since plan step salary:R14-b that is true of BOTH paths,
        because path 1 stopped transforming anything too.
        """
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("999.99"), periods[0].start_date, is_confirmed=True)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result[0].amount == Decimal("999.99")

    # The Cancelled / Credit skip that used to be pinned here moved with the
    # rule (plan step X-au-c2): this module no longer screens by status, the
    # boundary loader does, and it DROPS such a row rather than pricing it at
    # zero.  Its replacement is
    # ``test_projection_inputs.TestShadowContributionBoundary
    # .test_excluded_status_rows_are_dropped_not_zeroed``, which grades the
    # same rule against real rows and additionally pins why dropping and
    # zeroing are not interchangeable here.

    def test_transaction_outside_period_range_skipped(self):
        """A contribution dated outside the timeline's DOMAIN is skipped.

        The predicate reads the record's own payday since plan step C2-f2c --
        it matched a ``pay_period_id`` against an id-keyed map of *periods*
        until then -- so the case supplies a payday no period here opens on.
        """
        periods = _periods(date(2020, 1, 2))
        txns = [_priced(Decimal("200"), date(2020, 3, 5), is_confirmed=True)]
        result = build_contribution_timeline(
            feed=AccountPayrollFeed.absent(), contribution_transactions=txns,
            periods=periods, as_of=_AS_OF,
        )
        assert result == []
