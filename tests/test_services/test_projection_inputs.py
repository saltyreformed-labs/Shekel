"""
Tests for the shared projection-inputs helpers (F-22 / Commit 18).

Two flavours of test:

- Equivalence lock (C18-1): the new
  :func:`build_investment_projection_inputs` returns the same
  :class:`InvestmentInputs` as the previous inline
  :func:`calculate_investment_inputs` kwargs splat that lived in the
  three dashboard services.  If a future change drifts the helper
  away from the engine call, this test fails loud.

- Query-builder shape: the deduction-loader helpers return rows that
  match the filter contract the four consumers depend on (active
  profile, active deduction, target_account_id membership).  Uses
  the live test DB.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import (
    CalcMethodEnum,
    PaycheckLineKindEnum,
    EmployerContributionTypeEnum,
    RaiseTypeEnum,
)
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services.investment_projection import (
    AccountPayrollFeed,
    InvestmentInputs,
    PricedContribution,
    build_contribution_timeline,
    calculate_investment_inputs,
)
from app.services.pay_calendar import calendar_for
from app.services.salary_raises import RaiseTerms, terms_of
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    build_payroll_feeds,
    load_active_deductions_for_account,
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
    load_payroll_feeds,
    load_payroll_wiring,
    price_payroll_feeds,
)
from app.services.recorded_contributions import (
    load_transfer_contributions_for_account,
)
from tests._test_helpers import (
    an_entered_day,
    basis_for,
    make_line_cadence_rule,
    pricing_over,
    start_test_pay_list,
)


def _flat_id():
    return ref_cache.calc_method_id(CalcMethodEnum.FLAT)


def _feed(paydays, *, employee=None, gross=None):
    """Build the :class:`AccountPayrollFeed` a caller hands the wrapper.

    **The input since plan step salary:R14-b** (ruling **R-SAL2**), in place
    of the ``_FakeDeduction`` this file built: the paycheck engine prices a
    deduction when it prices the paycheck, and :func:`load_payroll_feeds`
    hands this value a resolver over that pricer.  ``TestLoadPayrollFeeds``
    below grades the real resolvers against real rows; the cases here only
    need a feed that answers, so each resolver is a table over *paydays*
    keyed by the period's ``start_date`` -- the one thing a fake period here
    carries.  A ``None`` figure means no resolver at all, which is the state
    :attr:`AccountPayrollFeed.is_payroll_linked` / :attr:`funds_employer`
    read ``False`` for (plan step salary:S3-e-2).
    """
    def _table(figure):
        if figure is None:
            return None
        by_payday = {day: Decimal(str(figure)) for day in paydays}
        return lambda period: by_payday[period.start_date]

    return AccountPayrollFeed(employee=_table(employee), gross=_table(gross))


@dataclass
class _FakePeriod:
    """The one thing ``calculate_investment_inputs`` reads off a period.

    A ``start_date`` and nothing else since plan step C2-f2c, which is the
    whole surface that function has left: the period LIST it took to translate
    a contribution's ``pay_period_id`` into a payday is gone, because the
    loader dates each contribution at the boundary.  Both real period types --
    :class:`~app.models.pay_period.PayPeriod` and
    :class:`~app.services.pay_calendar.DerivedPeriod` -- satisfy exactly this.
    """

    start_date: date


@dataclass
class _FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type_id: int
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


class TestBuildInvestmentProjectionInputsEquivalence:
    """C18-1: lock the helper to the engine's exact result.

    One flat deduction + one settled contribution + employer flat
    percentage produces a fully-populated :class:`InvestmentInputs`.
    Both call paths (direct ``calculate_investment_inputs`` kwargs
    splat AND the new ``build_investment_projection_inputs`` wrapper)
    are exercised on the SAME input objects; their outputs must be
    byte-identical.

    Hand-computed expectations (gross = 100000 / 26 = 3846.15;
    per-period deduction = 500.00; contribution per period =
    400 / 2 = 200.00; total per period = 700.00; YTD over both
    contribution periods = 200 + 200 = 400.00; employer flat 5%
    of gross 3846.15 = 192.31 -- carried as flat_percentage in
    employer_params, not directly computed by this helper):
    """

    @staticmethod
    def _fixture_inputs():
        params = _FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        periods = [
            _FakePeriod(start_date=date(2026, 1, 2)),
            _FakePeriod(start_date=date(2026, 1, 16)),
        ]
        feed = _feed(
            [period.start_date for period in periods],
            employee="500.00", gross="3846.15",
        )
        contributions = [
            PricedContribution(
                account_id=1, payday=periods[0].start_date,
                amount=Decimal("200"), is_confirmed=True,
            ),
            PricedContribution(
                account_id=1, payday=periods[1].start_date,
                amount=Decimal("200"), is_confirmed=True,
            ),
        ]
        return params, feed, contributions, periods

    def test_helper_matches_inline_kwargs_splat(self):
        """build_investment_projection_inputs == calculate_investment_inputs kwargs splat.

        The pre-Commit-18 call shape (direct kwargs splat) and the
        post-Commit-18 helper invocation must produce identical
        :class:`InvestmentInputs` for the same input objects.
        """
        params, feed, contributions, periods = self._fixture_inputs()

        # The pre-Commit-18 inline kwargs splat that lived in each
        # dashboard service.  Reproduced here exactly so a future
        # divergence between the wrapper and the engine surfaces.
        inline_result = calculate_investment_inputs(
            investment_params=params,
            feed=feed,
            all_contributions=contributions,
            current_period=periods[1],
        )

        helper_result = build_investment_projection_inputs(
            params, feed, contributions, periods[1],
        )

        assert isinstance(helper_result, InvestmentInputs)
        assert helper_result.periodic_contribution == inline_result.periodic_contribution
        assert helper_result.employer_params == inline_result.employer_params
        assert (
            helper_result.annual_contribution_limit
            == inline_result.annual_contribution_limit
        )
        assert helper_result.ytd_contributions == inline_result.ytd_contributions
        assert (
            helper_result.ytd_contributions_seed
            == inline_result.ytd_contributions_seed
        )

    def test_helper_returns_expected_decimal_values(self):
        """Hand-computed Decimal arithmetic locks the fixture's expected values.

        - periodic_contribution = 500.00 (the CURRENT payday's payroll figure,
          since plan step salary:R14-b) + (200+200)/2 (avg) = 700.00
        - ytd_contributions = 200 + 200 = 400 (both contributions in
          2026 up to current_period=periods[1])
        - annual_contribution_limit = 23500
        - employer_params.flat_percentage = Decimal("0.05")
        - the employer GROSS is no longer in the dict: it is a fact about a
          payday, so the feed answers it per period (salary:R14-b).
        """
        params, feed, contributions, periods = self._fixture_inputs()
        result = build_investment_projection_inputs(
            params, feed, contributions, periods[1],
        )
        assert result.periodic_contribution == Decimal("700.00")
        assert result.ytd_contributions == Decimal("400")
        assert result.annual_contribution_limit == Decimal("23500")
        assert result.employer_params is not None
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        assert "gross_biweekly" not in result.employer_params
        assert feed.gross_at(periods[1]) == Decimal("3846.15")


def _seed_deductions_fixture(app, db, seed_user, seed_second_user):
    """Seed two investment accounts + active and inactive deductions.

    Acct A has one active deduction (worth 500).  Acct B has one
    active deduction (worth 250).  Other user has an active
    deduction on their own account.  An additional inactive
    deduction on Acct A and a deduction on an inactive salary
    profile must NOT appear in the loader's results.

    Returns a dict of ids the caller's assertions consume.  Must run
    inside an active ``app.app_context()`` so the inserts share the
    test's session.
    """
    from app.enums import AcctTypeEnum
    from app.models.account import Account
    from app.models.ref import FilingStatus

    user_id = seed_user["user"].id
    other_user_id = seed_second_user["user"].id
    scenario_id = seed_user["scenario"].id
    other_scenario_id = seed_second_user["scenario"].id

    retire_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
    flat_id = ref_cache.calc_method_id(CalcMethodEnum.FLAT)
    timing_id = ref_cache.paycheck_line_kind_id(
        PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    )
    filing_status_id = (
        db.session.query(FilingStatus).filter_by(name="single").one().id
    )

    acct_a = Account(
        user_id=user_id, name="Acct A", account_type_id=retire_type_id,
    )
    acct_b = Account(
        user_id=user_id, name="Acct B", account_type_id=retire_type_id,
    )
    other_acct = Account(
        user_id=other_user_id, name="Other Acct",
        account_type_id=retire_type_id,
    )
    db.session.add_all([acct_a, acct_b, other_acct])
    db.session.flush()

    active_profile = SalaryProfile(
        user_id=user_id, scenario_id=scenario_id,
        name="Active",
        state_code="NC",
        filing_status_id=filing_status_id, is_active=True,
    )
    inactive_profile = SalaryProfile(
        user_id=user_id, scenario_id=scenario_id,
        name="Inactive",
        state_code="NC",
        filing_status_id=filing_status_id, is_active=False,
    )
    other_profile = SalaryProfile(
        user_id=other_user_id, scenario_id=other_scenario_id,
        name="Other",
        state_code="NC",
        filing_status_id=filing_status_id, is_active=True,
    )
    db.session.add_all([active_profile, inactive_profile, other_profile])
    start_test_pay_list(active_profile, Decimal("3846.15"))  # $100,000.00 a year / 26
    start_test_pay_list(inactive_profile, Decimal("3076.92"))  # $80,000.00 a year / 26
    start_test_pay_list(other_profile, Decimal("3846.15"))  # $100,000.00 a year / 26
    db.session.flush()

    active_a = PaycheckLine(
        salary_profile_id=active_profile.id, target_account_id=acct_a.id,
        name="A", amount=Decimal("500"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    active_b = PaycheckLine(
        salary_profile_id=active_profile.id, target_account_id=acct_b.id,
        name="B", amount=Decimal("250"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    inactive_dedn = PaycheckLine(
        salary_profile_id=active_profile.id, target_account_id=acct_a.id,
        name="A-inactive", amount=Decimal("999"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=False,
    )
    inactive_profile_dedn = PaycheckLine(
        salary_profile_id=inactive_profile.id,
        target_account_id=acct_a.id, name="A-inactive-profile",
        amount=Decimal("888"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    other_user_dedn = PaycheckLine(
        salary_profile_id=other_profile.id,
        target_account_id=other_acct.id, name="Other",
        amount=Decimal("777"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    db.session.add_all([
        active_a, active_b, inactive_dedn,
        inactive_profile_dedn, other_user_dedn,
    ])
    db.session.flush()
    return {
        "user_id": user_id,
        "other_user_id": other_user_id,
        "acct_a_id": acct_a.id,
        "acct_b_id": acct_b.id,
        "other_acct_id": other_acct.id,
    }


class TestLoadActiveDeductionsHelpers:
    """Query-shape tests for the new deduction loader helpers.

    Uses the live test DB with a small fixture seeded inside each
    test's ``app.app_context()`` so the inserts share the test's
    session: two investment accounts owned by one user, one active
    deduction per account, one inactive deduction (must be
    excluded), one deduction on an inactive salary profile (must be
    excluded), and one deduction owned by another user (must be
    excluded).
    """

    def test_single_account_loader_returns_only_active_owned_rows(
        self, app, db, seed_user, seed_second_user,
    ):
        """Single-account loader filters by user, active profile, active deduction."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_account(
                ctx["user_id"], ctx["acct_a_id"],
            )
            amounts = sorted(d.amount for d in result)
            # Only the active deduction on the active profile.
            # 999 (inactive) and 888 (inactive profile) excluded.
            assert amounts == [Decimal("500")]

    def test_single_account_loader_rejects_other_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """Single-account loader does not bleed across users."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_account(
                ctx["user_id"], ctx["other_acct_id"],
            )
            assert result == []

    def test_batch_loader_groups_by_target_account_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Batch loader returns dict keyed by target_account_id."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_accounts(
                ctx["user_id"], [ctx["acct_a_id"], ctx["acct_b_id"]],
            )
            assert set(result.keys()) == {ctx["acct_a_id"], ctx["acct_b_id"]}
            # Acct A: only the active 500 deduction (999 + 888 filtered).
            assert (
                [d.amount for d in result[ctx["acct_a_id"]]]
                == [Decimal("500")]
            )
            # Acct B: only the active 250 deduction.
            assert (
                [d.amount for d in result[ctx["acct_b_id"]]]
                == [Decimal("250")]
            )

    def test_batch_loader_empty_account_ids(
        self, app, db, seed_user, seed_second_user,
    ):
        """Batch loader returns {} for empty account_ids without an IN () query."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_accounts(
                ctx["user_id"], [],
            )
            assert result == {}

    def test_batch_loader_omits_accounts_with_no_deductions(
        self, app, db, seed_user, seed_second_user,
    ):
        """Accounts without active deductions are absent from the dict."""
        from app.enums import AcctTypeEnum
        from app.models.account import Account
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            retire_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
            bare_acct = Account(
                user_id=ctx["user_id"], name="Bare",
                account_type_id=retire_type_id,
            )
            db.session.add(bare_acct)
            db.session.flush()
            result = load_active_deductions_for_accounts(
                ctx["user_id"],
                [ctx["acct_a_id"], bare_acct.id],
            )
            assert ctx["acct_a_id"] in result
            assert bare_acct.id not in result


class TestLoadPayrollFeeds:
    """The producer plan step **salary:R14-b** puts in place of the feed's own
    arithmetic (ruling **R-SAL2**), graded against REAL rows.

    Two questions, and the point of the step is that neither is answered here:
    what a deduction takes from a paycheck, and what gross funds an employer
    contribution, are both established by
    :class:`~app.services.income_service.ProfilePaychecks` when it prices the
    paycheck.  What this loader owns is the FOLD -- which lines belong to which
    account, and which profile may answer at all -- so that is what these cases
    grade.

    They reuse :func:`_seed_deductions_fixture`, whose five deductions were
    built for the loader beneath this one: an active $500 flat on Acct A, an
    active $250 on Acct B, an INACTIVE deduction worth $999 on Acct A, one
    worth $888 on an ARCHIVED profile, and another owner's worth $777.  Three
    of the five must contribute nothing, and each does so for its own reason.
    """

    @staticmethod
    def _params_for(account_id, salary_profile_id=None):
        """Attach an :class:`InvestmentParams` row and return the map entry."""
        from app.models.investment_params import InvestmentParams
        params = InvestmentParams(
            account_id=account_id,
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=(
                ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.FLAT_PERCENTAGE,
                )
            ),
            employer_flat_percentage=Decimal("0.05"),
            salary_profile_id=salary_profile_id,
        )
        db.session.add(params)
        db.session.flush()
        return params

    def test_the_fold_keys_each_line_by_its_target_account(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Each account receives the deduction lines that NAME it, and no others.

        Acct A's $500 and Acct B's $250 come off the engine's own
        ``DeductionLine.target_account_id``, which is the hook ruling R-SAL2
        says this consumption finishes rather than adds: the column was
        populated so the two surfaces would agree and nothing ever read it.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feeds = load_payroll_feeds(
                pricing,
                [ids["acct_a_id"], ids["acct_b_id"]], {},
            )
            assert set(feeds) == {ids["acct_a_id"], ids["acct_b_id"]}
            for period in calendar.saved():
                assert feeds[ids["acct_a_id"]].employee_at(period) == Decimal("500")
                assert feeds[ids["acct_b_id"]].employee_at(period) == Decimal("250")

    def test_two_deductions_on_ONE_profile_are_summed_ONCE(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """An account funded twice by one job reads the SUM, not the double.

        The fold reads each funding PROFILE's breakdown, and one breakdown
        already holds every line that profile takes -- so an account named by
        two of one profile's deductions must read that profile once.  Reading
        it per DEDUCTION instead counts every line as many times as the
        account has deductions, which is $1,500 here against a true $750, and
        no other case in this class can tell the two apart: every one of them
        has a single active line per account.

        Acct A already carries the fixture's active $500; this adds a $250
        second line on the same profile, so the account's payday is $750.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            db.session.add(PaycheckLine(
                salary_profile_id=profile.id,
                target_account_id=ids["acct_a_id"],
                name="A-second", amount=Decimal("250"),
                calc_method_id=_flat_id(),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            ))
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]], {},
            )[ids["acct_a_id"]]
            assert feed.employee_at(calendar.saved()[0]) == Decimal("750")

    @staticmethod
    def _projected(calendar, years_out):
        """Return a projected period about *years_out* past the horizon.

        Off :meth:`~app.services.pay_calendar.PayCalendar.projection_axis`,
        the producer every projecting surface uses, so the period is the
        shape production hands the feed and not one assembled by hand.
        """
        far = calendar.horizon() + timedelta(days=365 * years_out)
        axis = calendar.projection_axis(calendar.opening_bound(), far)
        return next(p for p in axis if p.start_date > far - timedelta(days=30))

    def test_a_period_PAST_the_horizon_is_priced_not_held(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Both resolvers answer a projected period off the engine.

        **The capability plan step salary:S3-e-2 exists for** (ruling
        **R-SAL15**): the feed used to be two dictionaries over the saved
        window and HELD an invented figure past it.  A period ten years past
        the horizon carries ``period_id = None`` and says so through
        :attr:`~app.services.pay_calendar.DerivedPeriod.is_projected`; the
        feed prices it through the same pricer as a saved one, so a flat
        deduction and a raise-free profile read the same figures there as
        on the first saved payday -- and both are hand-computed, so the case
        still grades something if feed and pricer were to move together.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], profile.id)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            feed = load_payroll_feeds(
                pricing_over(calendar), [ids["acct_a_id"]],
                {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            projected = self._projected(calendar, 10)
            assert projected.is_projected is True
            assert projected.start_date > calendar.horizon()
            assert feed.employee_at(projected) == Decimal("500")
            assert feed.gross_at(projected) == Decimal("3846.15")

    def test_a_cadence_skip_PAST_the_horizon_prices_zero_not_the_previous_payday(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A 24-per-year line is ``$0.00`` on a projected month's third payday.

        The rule the deleted dictionaries had to be TOTAL for -- a skipped
        payday is an explicit zero, never a gap the hold would read the
        previous payday's figure over -- and the hold's own docstring got the
        cadence wrong about it (**N-544**).  On a resolver the engine answers
        it directly, and this grades that on a PROJECTED period: the third
        payday of a month found by counting the axis's own paydays per month,
        an oracle independent of the engine's ``is_third_paycheck`` flag.  11
        of the developer's 12 live deductions carry this cadence.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            twice_monthly = PaycheckLine(
                salary_profile_id=profile.id,
                target_account_id=ids["acct_b_id"],
                name="B-twice-monthly", amount=Decimal("100"),
                calc_method_id=_flat_id(),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            )
            db.session.add(twice_monthly)
            db.session.flush()
            # The 24-per-year shape (plan step salary:R15-b): every paycheck,
            # at most 2 a month.
            make_line_cadence_rule(db.session, twice_monthly, 24)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            feed = load_payroll_feeds(
                pricing_over(calendar), [ids["acct_b_id"]], {},
            )[ids["acct_b_id"]]
            far = calendar.horizon() + timedelta(days=365 * 2)
            axis = calendar.projection_axis(calendar.opening_bound(), far)
            by_month: "dict[tuple[int, int], list]" = {}
            for period in axis:
                if period.is_projected:
                    by_month.setdefault(
                        (period.start_date.year, period.start_date.month), [],
                    ).append(period)
            three = next(
                paydays for paydays in by_month.values() if len(paydays) == 3
            )
            # The fixture's own $250 line pays every payday; the 24-per-year
            # line skips the third, so the sum drops by exactly its amount.
            assert feed.employee_at(three[0]) == Decimal("350")
            assert feed.employee_at(three[1]) == Decimal("350")
            assert feed.employee_at(three[2]) == Decimal("250")

    def test_a_resolver_fired_past_the_loader_issues_NO_query(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Walking a feed over projected periods touches the database not at all.

        The loader hands :mod:`app.services.investment_projection` callables
        that run inside its *no database access* contract, and ruling
        **R-SAL15** accepted that design on the claim that no lazy ORM load
        fires mid-walk.  This is the measurement behind the claim: every
        relationship the engine reads is loaded before the walk, so a hundred
        projected periods -- pricing, not memo hits -- issue zero statements.
        Counted at the cursor rather than asserted from the relationship
        definitions, because a ``lazy="joined"`` that is later loosened would
        leave those definitions looking exactly as they do today.

        **The profile carries a RECURRING RAISE whose month the walk crosses
        every year**, because an adversarial review of this step found the
        fixture blind to the one relationship the engine read only on a
        raise's own month: ``get_raise_event`` read ``raise_obj.raise_type``
        (``lazy="joined"`` on ``SalaryRaise``), and a raise-free profile
        never reached that line.  *Since plan step salary:S3-f-1 the engine
        prices ``RaiseTerms`` values and the type's name is resolved from the
        FK through the ref cache at ``for_profile`` -- inside the loader,
        before the count opens -- so no relationship is read on the walk at
        all; the fixture is kept so this case still walks a raise month and
        would see a read that returned there.*  Asserted non-vacuously below:
        at least one walked period prices a paycheck labelled with the raise.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], profile.id)
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
                effective_month=7, effective_year=2026,
                percentage=Decimal("0.0300"), is_recurring=True,
                terminal_year=None,
            ))
            # And a line WITH a cadence rule (plan step salary:R15-b): the
            # engine reads it through the rule's owner and the owner's
            # profile, so the walk must be query-free on THAT path too -- an
            # adversarial review found the fixture blind to it.
            ruled = PaycheckLine(
                salary_profile_id=profile.id,
                target_account_id=ids["acct_a_id"],
                name="A-twice-monthly", amount=Decimal("40"),
                calc_method_id=_flat_id(),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            )
            db.session.add(ruled)
            db.session.flush()
            make_line_cadence_rule(db.session, ruled, 24)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]], {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            far = calendar.horizon() + timedelta(days=365 * 4)
            axis = calendar.projection_axis(calendar.opening_bound(), far)
            projected = [p for p in axis if p.is_projected]
            assert len(projected) >= 100

            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            event.listen(db.engine, "before_cursor_execute", _count)
            try:
                for period in projected:
                    feed.employee_at(period)
                    feed.gross_at(period)
            finally:
                event.remove(db.engine, "before_cursor_execute", _count)
            assert statements == []
            # Non-vacuity for the raise path: the pricer the feed read priced
            # at least one walked July with the raise's own label, so the
            # raise-month branch of ``get_raise_event`` was on the path.
            assert any(
                pricing.for_profile(profile).at(period).period.raise_event
                for period in projected
            )
            # Non-vacuity for the cadence path: the ruled line was priced on
            # the walk and skipped on at least one projected third paycheck,
            # so its rule was resolved and walked inside the count.
            taken = [
                any(line.name == "A-twice-monthly"
                    for line in pricing.for_profile(profile).at(period).deductions.pre_tax)
                for period in projected
            ]
            assert any(taken) and not all(taken)

    def test_an_inactive_or_ARCHIVED_line_contributes_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Acct A reads $500, not $500 + $999 + $888.

        Three deductions name Acct A: the active one, an INACTIVE one worth
        $999, and one worth $888 on a profile the owner has ARCHIVED.  The
        second is refused by the deduction loader's own filter and the third
        by :func:`_load_funding_profiles` -- which matters because archiving a
        profile does NOT deactivate its deductions
        (``routes/salary/profiles.delete_profile`` sets ``is_active`` on the
        profile and its template and touches the deductions not at all), so
        the deduction's own flag cannot answer this.  That was a measured
        defect in ``salary:R14-a``'s backfill before its review caught it.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]], {},
            )[ids["acct_a_id"]]
            assert feed.employee_at(calendar.saved()[0]) == Decimal("500")

    def test_a_gross_is_priced_only_where_a_funding_profile_is_NAMED(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """``salary_profile_id`` set -> a gross per payday; unset -> none at all.

        Ruling **R-SAL5** and the developer's 2026-09-04 ruling together: the
        employer contribution's basis is the NAMED profile's own paycheck, and
        an account that names none models no employer money rather than
        borrowing whichever profile a reader resolved.  The gross asserted is
        the engine's own for that payday, read back through
        :class:`~app.services.income_service.ProfilePaychecks` -- so this
        grades the FOLD and cannot pass by re-deriving the arithmetic it is
        checking.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            named = self._params_for(ids["acct_a_id"], profile.id)
            unnamed = self._params_for(ids["acct_b_id"], None)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feeds = load_payroll_feeds(
                pricing,
                [ids["acct_a_id"], ids["acct_b_id"]],
                {ids["acct_a_id"]: named, ids["acct_b_id"]: unnamed},
            )
            first = calendar.saved()[0]
            # Keyed on the BREAKDOWN's OWN payday, never paired by
            # position: an equality whose two sides share one producer
            # measures nothing, and a ``zip`` here would have shared the exact
            # expression the loader used.  This read the payday out of a
            # ``{period_id: start_date}`` table until plan step salary:S3-d
            # put the payday on the paycheck.  A SECOND pricer, deliberately:
            # the feed's resolver reads the pass's pricer, and reading the
            # same memo back would share the producer under test.
            expected = {
                breakdown.period.payday: breakdown.earnings.gross_biweekly
                for breakdown in pricing_over(calendar).for_profile(
                    profile,
                ).over(calendar.saved())
            }[first.start_date]
            # And the figure itself, hand-computed, so the case still grades
            # something if BOTH sides were to move together: $100,000 over 26
            # paychecks is $3,846.15, and this fixture's profile carries no
            # raise, so every payday in the window prices the same.
            assert expected == Decimal("3846.15")

            assert feeds[ids["acct_a_id"]].funds_employer is True
            assert feeds[ids["acct_a_id"]].gross_at(first) == expected
            assert feeds[ids["acct_b_id"]].funds_employer is False
            assert feeds[ids["acct_b_id"]].gross_at(first) is None

    def test_the_employer_basis_is_base_pay_not_the_gross_a_taxable_earning_joins(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Beside a $45 taxable earning the funding profile's basis stays $3,846.15, not $3,891.15.

        Ruling **R-SAL38** (3), plan step salary:R18-b: a percentage figure
        in payroll is a percentage of BASE PAY, and the employer-match
        resolver reads that base.  Until R18-b ``gross_biweekly`` WAS the
        base, so no case here could tell the two apart; this one seeds a
        taxable earning line on the funding profile so they differ by exactly
        the line, and asserts the feed reads the base.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            db.session.add(PaycheckLine(
                salary_profile_id=profile.id, name="Phone Allowance",
                amount=Decimal("45"),
                calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.TAXABLE_EARNING,
                ),
                is_active=True,
            ))
            named = self._params_for(ids["acct_a_id"], profile.id)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            feeds = load_payroll_feeds(
                pricing_over(calendar), [ids["acct_a_id"]], {ids["acct_a_id"]: named},
            )
            first = calendar.saved()[0]
            priced = pricing_over(calendar).for_profile(profile).at(first)
            # The line is on the paycheck: gross carries it, base does not.
            assert priced.earnings.base_biweekly == Decimal("3846.15")
            assert priced.earnings.gross_biweekly == Decimal("3891.15")
            assert feeds[ids["acct_a_id"]].gross_at(first) == Decimal("3846.15")

    def test_an_ARCHIVED_funding_profile_models_no_employer_money(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A job the owner has left does not fund an employer contribution.

        ``ondelete="RESTRICT"`` keeps the row, and a profile is archived
        rather than deleted here, so the column can outlive the job it names.
        The read refuses it, which is the same answer a ``NULL`` gets.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            archived = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Inactive")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], archived.id)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]],
                {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            assert feed.funds_employer is False

    def test_ANOTHER_OWNERS_profile_prices_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The read scopes by owner, not only the write door.

        ``salary:R14-a`` closed the forged FK at the deduction door and its
        migration had to defend against the same shape in SQL -- measured
        writing a STRANGER's ``salary_profile_id`` onto this owner's row
        before its review caught it.  A read that trusts the column's
        ownership is a read that can be made to price a stranger's salary, so
        it is scoped here too.  The row is written directly, bypassing the
        route guard, precisely because the point is what the READ does with a
        value the door would have refused.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            stranger = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["other_user_id"], name="Other")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], stranger.id)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]],
                {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            assert feed.funds_employer is False

    def test_the_result_is_total_over_the_accounts_asked_for(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """An account no payroll funds maps to the EMPTY feed, not to nothing.

        A caller indexes rather than defaulting, so a missing key is a defect
        instead of a silently unfunded account -- the contract
        ``ContributionInputs.absent`` keeps one tier up.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feeds = load_payroll_feeds(
                pricing,
                [ids["acct_a_id"], ids["other_acct_id"]], {},
            )
            assert set(feeds) == {ids["acct_a_id"], ids["other_acct_id"]}
            unfunded = feeds[ids["other_acct_id"]]
            assert unfunded == AccountPayrollFeed.absent()
            assert unfunded.is_payroll_linked is False
            assert unfunded.funds_employer is False

    def test_no_accounts_issues_no_query(self, app, db, seed_user):
        """An empty account list answers the empty map without a query.

        MEASURED, not inferred from the empty answer (plan step salary:S3-f-2b
        deleted the door's own early return, so the claim now rests on the
        wiring loader's guards): the statements issued between the pricer's
        construction and the answer are counted, and there are none.
        """
        with app.app_context():
            pricing = pricing_over(calendar_for(seed_user["user"].id))
            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            event.listen(db.engine, "before_cursor_execute", _count)
            try:
                feeds = load_payroll_feeds(pricing, [], {})
            finally:
                event.remove(db.engine, "before_cursor_execute", _count)
            assert feeds == {}
            assert statements == [], (
                "an empty account list issued a query: " + "; ".join(statements)
            )


class TestLoadInvestmentParamsForAccounts:
    """Query-shape tests for the investment-params batch loader.

    Level 1 balance-seam prep (DRY): this loader is the single home for
    the "which accounts get an :class:`InvestmentParams` row?" decision,
    scoped by the canonical classifier rather than by elimination.  Uses
    the live test DB with a mixed account set seeded inside the test's
    ``app.app_context()`` so the inserts share the test's session.
    """

    def test_returns_only_investment_accounts_with_a_params_row(
        self, app, db, seed_user,
    ):
        """Map holds exactly the INVESTMENT account that has a params row.

        Locks the classifier predicate
        (``classify_account(a) is AccountProjectionKind.INVESTMENT``):
        the loader must scope membership by the classifier, NOT by "does
        this account happen to have an InvestmentParams row?".  Mixed
        input set, all owned by ``seed_user``:

        - a 401(k) (classifier -> INVESTMENT) WITH an
          :class:`InvestmentParams` row -> present, keyed by account_id.
        - a Checking account (classifier -> PLAIN) that ALSO has an
          InvestmentParams row -> still excluded, because the classifier
          filters it out before the query runs.  If the predicate were
          dropped, the widened query would find this row and leak it
          into the result, failing the keys assertion below -- that is
          why the excluded account is deliberately given a row.
        - a second 401(k) (INVESTMENT) with NO params row -> absent,
          because the query finds no matching row for it.
        """
        from app.enums import AcctTypeEnum
        from app.models.account import Account
        from app.models.investment_params import InvestmentParams
        with app.app_context():
            user_id = seed_user["user"].id
            k401_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
            checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

            inv_with_params = Account(
                user_id=user_id, name="401k With Params",
                account_type_id=k401_type_id,
            )
            checking = Account(
                user_id=user_id, name="Everyday Checking",
                account_type_id=checking_type_id,
            )
            inv_without_params = Account(
                user_id=user_id, name="401k No Params",
                account_type_id=k401_type_id,
            )
            db.session.add_all(
                [inv_with_params, checking, inv_without_params],
            )
            db.session.flush()

            none_type_id = ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.NONE,
            )
            # Seed a params row on BOTH the INVESTMENT account and the
            # classifier-excluded Checking account.  The checking row is
            # the predicate trip-wire: only the classifier filter keeps
            # it out of the result, so dropping that filter would widen
            # the query, leak the checking row in, and fail the keys
            # assertion below.
            db.session.add_all([
                InvestmentParams(
                    account_id=inv_with_params.id,
                    assumed_annual_return=Decimal("0.07000"),
                    employer_contribution_type_id=none_type_id,
                ),
                InvestmentParams(
                    account_id=checking.id,
                    assumed_annual_return=Decimal("0.07000"),
                    employer_contribution_type_id=none_type_id,
                ),
            ])
            db.session.flush()

            result = load_investment_params_for_accounts(
                [inv_with_params, checking, inv_without_params],
            )

            # Exactly the investment-with-params account, keyed by id;
            # the Checking account is filtered by the classifier (despite
            # having a row) and the params-less 401(k) has no row.
            assert set(result.keys()) == {inv_with_params.id}
            assert result[inv_with_params.id].account_id == inv_with_params.id
            assert (
                result[inv_with_params.id].assumed_annual_return
                == Decimal("0.07000")
            )

    def test_empty_accounts_returns_empty_dict(self, app):
        """Empty input returns {} without issuing an IN () query."""
        with app.app_context():
            assert load_investment_params_for_accounts([]) == {}


class TestAContributionIsValuedAndScreenedAtTheBoundary:
    """The BOUNDARY where a contribution is valued and screened.

    ``load_transfer_contributions_for_account(s)`` became the one place a
    contribution's dollar is decided at plan step X-au-c2: it prices the whole
    row set through :func:`app.services.cash_ledger.contributions_by_key` and
    hands :mod:`app.services.investment_projection` frozen
    :class:`~app.services.investment_projection.PricedContribution` records.

    **These four cases are deep-quality-hunt #11, moved here whole with their
    hand-computed figures** from ``test_investment_projection`` .
    ``TestEstimatedVsEffectiveAlignment``.  A contribution is normally worth
    its transfer's plan, but a settle with a typed figure records that figure
    as what moved.  Once that happens the realized figure diverges
    from the estimate, and every feed the projection builds -- the averaged
    periodic contribution, the YTD display, the engine seed, and the per-period
    timeline -- has to read the realized one, or the cap/limit accounting
    charges a different dollar than the growth engine actually applies.  The
    realized figure is each side's payment RECORD, hanging off the transfer,
    since plan step ``balance:X-bi-6-4d-2``, and a transfer is ONE row since
    plan step ``balance:X-bi-6-4d-2`` (ruling R-BAL258): it had two shadow
    rows, which carried the realized figure until the first step and nothing
    after it.  :meth:`_contribution` settles through the transfer's door and
    writes no shadow (ruling R-BAL167 class 4).

    They live at this tier now because it is the only tier that can still fail:
    the projection module consumes a record carrying one ``amount`` field, so
    asserting the rule there would assert that $400 averages to $400.  Here the
    rule is exercised against real rows.
    """

    @staticmethod
    def _contribution(
        seed_user, db_session, period, estimated, actual=None, *,
        settled=False, cancelled=False, account=None,
    ):
        """Seed a contribution transfer into an investment account; return the account.

        The contribution is the transfer's TO-side leg, landing in the
        investment account, which is the leg the loader prices.  The helper
        was ``_contribution_shadow`` and returned ``(account,
        income_shadow)``, the shadow row the loader once selected, until plan
        step ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        R-BAL258).  No case read that row, so it returns the account alone
        (ruling R-BAL167 class 4, every assertion unchanged).

        Pass ``account`` to feed an EXISTING account a second contribution --
        the multi-period cases need one account across several periods, because
        the per-account arithmetic they grade (the average's distinct-period
        denominator, the two YTD windows) is defined over one account's rows.

        A *settled* contribution is settled through the transfer's own door
        since plan step ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 4): the
        TRANSFER's status is the one status and each side's payment record
        hangs off the transfer, where this helper wrote the status and day
        onto both twins and the parent and a covering movement under each
        twin until then.  Nothing reads a twin now, so those plants left the
        transfer settled with no side record -- a ``$0.00`` close -- and the
        loader rightly valued it at ``$0``.

        A *cancelled* contribution is cancelled through the same door, the
        transfer's status being the one status.  The helper wrote Cancelled
        onto both shadows and the transfer directly until they went; measured
        10-15, the door's cancel and a direct write of the transfer's status
        leave every ``budget.transfers`` column equal but the identity,
        figure, name and timestamps, and neither a side record, so the door
        changes no state a case reads and writes past no seam.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum
        from app.services import transfer_service
        from tests._test_helpers import (
            basis_for,
            create_transfer,
            make_investment_account,
            on_both_sides,
            typed,
        )

        if account is None:
            account = make_investment_account(
                seed_user, db_session, period, Decimal("1000.00"),
                name=f"401k-{period.id}-{estimated}",
            )
        transfer = create_transfer(
            seed_user, db_session, seed_user["account"], account, period,
            amount=Decimal(str(estimated)),
        )
        if settled:
            # The transfer and both sides' records, through the named verb:
            # the day entered on both sides, the record's figure the
            # transfer's own plan (``resolved``) or *actual*, a figure a HUMAN
            # typed (``typed``).  The verb settles a transfer at Paid (DONE);
            # this planted Received, a status no transfer door writes.
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                submitted=None if actual is None else typed(
                    Decimal(str(actual)),
                ),
                side_days=on_both_sides(
                    transfer.from_account_id, transfer.to_account_id,
                    an_entered_day(period.start_date),
                ),
            )
        elif actual is not None:
            raise AssertionError(
                "A figure RECORDS a settle (plan step X-au-c3), so an "
                "unsettled row cannot carry one -- the seam refuses it "
                "(reject_settlement_without_settled_status). Pass "
                "settled=True beside actual, or drop actual."
            )
        if cancelled:
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
        db_session.flush()
        return account

    @staticmethod
    def _params(limit=None):
        return _FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=limit,
            employer_contribution_type_id=ref_cache
            .employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
        )

    def test_settled_contribution_is_priced_at_its_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """estimated $500 / actual $400 -> the record carries $400.

        The rule itself, at the tier that decides it: a figure a human read off
        a statement is a fact and the row's own amount is an inference, so the
        actual wins.  Summing ``estimated_amount`` (the pre-#11 bug) would
        carry $500.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id],
            ).records

            assert len(records) == 1
            assert records[0].amount == Decimal("400")
            assert records[0].is_confirmed is True
            assert records[0].account_id == account.id
            assert records[0].payday == seed_periods[0].start_date

    def test_periodic_contribution_uses_the_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """One settled $500-estimated / $400-actual shadow -> average $400.

        The averaged periodic contribution over one period is the realized
        $400, NOT the estimated $500.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()
            # The pass's ``DerivedPeriod`` for that row (ruling R-SAL19).
            period = calendar_for(seed_user["user"].id).saved()[0]

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [period.period_id],
            ).records
            result = calculate_investment_inputs(
                investment_params=self._params(), feed=AccountPayrollFeed.absent(),
                all_contributions=records, current_period=period,
            )

            # effective $400 / 1 period = $400 (NOT estimated $500).
            assert result.periodic_contribution == Decimal("400")

    def test_over_contribution_actual_above_estimate_is_honored(
        self, app, db, seed_user, seed_periods,
    ):
        """estimated $400 / actual $500 -> $500, the direction the cap must catch.

        Symmetric to the under-contribution case: a settled shadow that came in
        HIGHER than planned contributes its realized $500.  Summing
        ``estimated_amount`` would under-count it at $400 and let the annual
        limit accounting miss an over-contribution.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="400", actual="500", settled=True,
            )
            db.session.commit()
            # The pass's ``DerivedPeriod`` for that row (ruling R-SAL19).
            period = calendar_for(seed_user["user"].id).saved()[0]

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [period.period_id],
            ).records
            result = calculate_investment_inputs(
                investment_params=self._params(limit=Decimal("23500")),
                feed=AccountPayrollFeed.absent(), all_contributions=records,
                current_period=period,
            )

            assert result.periodic_contribution == Decimal("500")
            # The displayed YTD (<= current) also reflects the realized $500.
            assert result.ytd_contributions == Decimal("500")

    def test_ytd_and_seed_both_read_the_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Three settled $500-estimated / $400-actual shadows across 3 periods.

        ``ytd_contributions`` (<= current) = 3 x $400 = $1,200;
        ``ytd_contributions_seed`` (< current) = 2 x $400 = $800.  Summing
        ``estimated_amount`` would give $1,500 / $1,000.
        """
        with app.app_context():
            periods = seed_periods[:3]
            account = None
            for period in periods:
                account = self._contribution(
                    seed_user, db.session, period,
                    estimated="500", actual="400", settled=True,
                    account=account,
                )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [p.id for p in periods],
            ).records
            assert len(records) == 3

            result = calculate_investment_inputs(
                investment_params=self._params(limit=Decimal("23500")),
                feed=AccountPayrollFeed.absent(), all_contributions=records,
                # The pass's ``DerivedPeriod`` for the third row (R-SAL19).
                current_period=calendar_for(seed_user["user"].id).saved()[2],
            )

            assert result.ytd_contributions == Decimal("1200")
            assert result.ytd_contributions_seed == Decimal("800")

    def test_inputs_average_and_timeline_agree(
        self, app, db, seed_user, seed_periods,
    ):
        """Both feeds read the SAME record, so they cannot price the row twice.

        The whole point of #11: the timeline applies a figure per period and the
        periodic-average / YTD-seed feed must apply the same one, or the
        limit/cap accounting reads a different number than the engine does.
        Since X-au-c2 both read one ``PricedContribution.amount``, so the
        agreement is structural -- this pins it end to end anyway, because the
        two feeds are what a future edit could re-split.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()
            # The pass's own period, not the ORM row: both readers take a
            # ``DerivedPeriod`` since plan step salary:S3-e-2 (ruling
            # **R-SAL19**), and this case handed them ``seed_periods[0]``
            # until then -- which passed only because an absent feed never
            # reads the period at all, so it was the standing counterexample
            # to the contract rather than a caller of it.
            period = calendar_for(seed_user["user"].id).saved()[0]
            assert period.period_id == seed_periods[0].id

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [period.period_id],
            ).records
            inputs = calculate_investment_inputs(
                investment_params=self._params(), feed=AccountPayrollFeed.absent(),
                all_contributions=records, current_period=period,
            )
            timeline = build_contribution_timeline(
                feed=AccountPayrollFeed.absent(), contribution_transactions=records,
                periods=[period], as_of=period.start_date,
            )

            assert inputs.periodic_contribution == Decimal("400")
            assert len(timeline) == 1
            assert timeline[0].amount == Decimal("400")
            assert inputs.periodic_contribution == timeline[0].amount

    def test_excluded_status_rows_are_dropped_not_zeroed(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled shadow is ABSENT from the records, not present at $0.00.

        The ``status_contributes_to_balance`` screen the projection module used
        to apply four times now runs once, here.  Dropping and zeroing are NOT
        interchangeable and this is the case that proves it:
        ``_inputs._average_transfer_contribution`` divides by the number of
        DISTINCT
        pay periods it sees, so a Cancelled contribution carried through at
        ``$0.00`` in its own period would make the denominator 2 and halve the
        average -- $400 / 2 periods = $200 against the correct $400.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="400", settled=True,
            )
            # The SAME account, a later period: the Cancelled row has to be
            # inside the queried set for its absence to mean anything.
            self._contribution(
                seed_user, db.session, seed_periods[1],
                estimated="999", cancelled=True, account=account,
            )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id, seed_periods[1].id],
            ).records

            # The Cancelled row is gone entirely -- not present as a zero.
            assert len(records) == 1
            assert records[0].amount == Decimal("400")
            assert records[0].payday == seed_periods[0].start_date

            result = calculate_investment_inputs(
                investment_params=self._params(), feed=AccountPayrollFeed.absent(),
                all_contributions=records, current_period=seed_periods[1],
            )
            # ONE period contributed, so the average is the full $400.  A
            # zero-carrying record would have made this $200.
            assert result.periodic_contribution == Decimal("400")

    def test_projected_contribution_is_not_confirmed(
        self, app, db, seed_user, seed_periods,
    ):
        """A still-Projected contribution is priced but NOT confirmed.

        The ``status.is_settled -> ContributionRecord.is_confirmed`` mapping
        moved to this boundary at plan step X-au-c2, and an adversarial review
        found the False half had lost its only grader: converting the old
        module-level test left it asserting a field the test itself supplied.
        A loader that wrote ``is_confirmed=True`` unconditionally would pass
        every other case here, and the growth engine would treat every future
        contribution as money already in the account.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0], estimated="250",
            )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id],
            ).records

            assert len(records) == 1
            assert records[0].amount == Decimal("250")
            assert records[0].is_confirmed is False

    def test_cancelled_contribution_still_counts_as_LINKED(
        self, app, db, seed_user, seed_periods,
    ):
        """A screened-out row still reports its account as linked.

        The companion to
        :meth:`test_excluded_status_rows_are_dropped_not_zeroed`, and the reason
        the loader returns two facts rather than one.  ``retirement_projection``
        asks whether ANYTHING funds an account to decide between rendering
        ``$0.00`` and a "link a contribution" call-to-action; a Cancelled
        contribution counts nothing but is still a link, so screening it out of
        the records must not take the account out of that set.  An adversarial
        review caught this flipping when the screen moved here.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="600", cancelled=True,
            )
            db.session.commit()

            loaded = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id],
            )

            # Nothing COUNTS ...
            assert loaded.records == []
            # ... but something is LINKED.
            assert account.id in loaded.linked_account_ids


# ── salary:S3-f-1: the feed is WIRING loaded once, priced per raise set ──


class TestTheFeedIsBuiltOverTheWiring:
    """``load_payroll_feeds`` is two halves, and the pure half re-prices.

    Plan step **salary:S3-f-1** (ruling **R-SAL20**).  The loader issued its
    queries and built its resolvers in one body, so pricing the same accounts
    under another raise set -- the ``/retirement`` rail's per-raise probe,
    plan step salary:S3-f -- meant either re-issuing every query per probe or
    reaching into the pass's memo for paychecks priced under the rows.
    :func:`load_payroll_wiring` is the queries; :func:`build_payroll_feeds`
    is the resolvers over whatever pricers it is handed.
    """

    @staticmethod
    def _wired(app, db, seed_user, seed_second_user):
        """Acct A funded by the Active profile, which carries a forever raise.

        A recurring 5% July raise from 2026 with no end year, so a 2028 payday
        prices ``$100,000 x 1.05^3 / 26 = $4,452.40`` under the rows and
        ``$100,000 x 1.05 / 26 = $4,038.46`` under terms believed through
        2026.
        """
        ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=ids["user_id"], name="Active")
            .one()
        )
        params = TestLoadPayrollFeeds._params_for(ids["acct_a_id"], profile.id)
        row = SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
            effective_month=7, effective_year=2026,
            percentage=Decimal("0.0500"), is_recurring=True,
            terminal_year=None,
        )
        db.session.add(row)
        db.session.commit()
        db.session.refresh(profile)
        terms = (replace(RaiseTerms.of(row), terminal_year=2026),)
        return ids, profile, {ids["acct_a_id"]: params}, terms

    @staticmethod
    def _payday_in(calendar, year):
        """A projected payday in *year* on or after September."""
        axis = calendar.axis(calendar.opening_bound(), date(year, 12, 31))
        return next(p for p in axis if p.start_date.year == year
                    and p.start_date.month >= 9)

    def test_the_composed_door_is_the_two_halves(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """What the callers still call answers what the halves answer."""
        with app.app_context():
            ids, profile, params_by_account, _ = self._wired(
                app, db, seed_user, seed_second_user,
            )
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            account_ids = [ids["acct_a_id"], ids["acct_b_id"]]

            composed = load_payroll_feeds(
                pricing, account_ids, params_by_account,
            )
            wiring = load_payroll_wiring(
                ids["user_id"], account_ids, params_by_account,
            )
            halves = build_payroll_feeds(wiring, {
                pid: pricing.for_profile(p) for pid, p in wiring.profiles.items()
            })

            assert wiring.account_ids == tuple(account_ids)
            assert set(wiring.profiles) == {profile.id}
            assert set(composed) == set(halves) == set(account_ids)
            first = calendar.saved()[0]
            for account_id in account_ids:
                assert (
                    composed[account_id].employee_at(first)
                    == halves[account_id].employee_at(first)
                )
                assert (
                    composed[account_id].gross_at(first)
                    == halves[account_id].gross_at(first)
                )
            # And a figure of its own, so the equality above is not two
            # sides of one producer: Acct A's $500 flat deduction, and its
            # employer half off a $100,000 salary in 2026, before the raise.
            assert halves[ids["acct_a_id"]].employee_at(first) == Decimal("500")
            assert halves[ids["acct_a_id"]].gross_at(first) == Decimal("3846.15")

    def test_one_wiring_is_priced_under_two_raise_sets_with_no_query(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The purpose of the split: rebuild the resolvers, re-issue nothing.

        The wiring is loaded once; two builds over it -- the pass's pricer
        under the rows and a pricer under terms believed through 2026 --
        issue zero statements between them, and the two feeds answer the
        September 2028 employer gross as ``$4,452.40`` and ``$4,038.46``.
        """
        with app.app_context():
            ids, profile, params_by_account, terms = self._wired(
                app, db, seed_user, seed_second_user,
            )
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            wiring = load_payroll_wiring(
                ids["user_id"], [ids["acct_a_id"]], params_by_account,
            )
            # Both pricers are built BEFORE the count opens (their construction
            # loaded a tax series until plan step salary:X-at-1): the claim is
            # about the builds.
            stored_pricers = {profile.id: pricing.for_profile(profile)}
            believed_pricers = {
                profile.id: pricing.for_profile(profile, terms),
            }
            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            event.listen(db.engine, "before_cursor_execute", _count)
            try:
                stored = build_payroll_feeds(wiring, stored_pricers)
                believed = build_payroll_feeds(wiring, believed_pricers)
            finally:
                event.remove(db.engine, "before_cursor_execute", _count)
            assert statements == [], (
                "build_payroll_feeds issued a query; the queries belong to "
                "load_payroll_wiring alone"
            )

            payday = self._payday_in(calendar, 2028)
            acct = ids["acct_a_id"]
            assert stored[acct].gross_at(payday) == Decimal("4452.40")
            assert believed[acct].gross_at(payday) == Decimal("4038.46"), (
                "a feed built over pricers keyed on the terms answered the "
                "rows' gross"
            )

    def test_price_payroll_feeds_prices_each_profile_under_ITS_OWN_terms(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The one pricer-map spelling dispatches ``terms_for`` per profile.

        Plan step **salary:S3-f-2b**: :func:`price_payroll_feeds` is what the
        composed door, the batch loader and the picture at each plan point all
        build their pricer maps through, so a wiring with TWO funding profiles
        and a belief that names a raise on only ONE of them must price Acct A
        (the Active profile, its raise believed through 2026) at the believed
        ``$4,038.46`` and Acct B (a second profile, no raises, $52,000) at its
        own ``$52,000 / 26 = $2,000.00`` -- and hand the second profile the
        SAME pricer the rows made, because its believed set equals its rows.
        A dispatch that priced every profile under the first profile's terms,
        or built a fresh pricer for an unchanged set, fails one of the two.
        """
        with app.app_context():
            ids, active, params_by_account, terms = self._wired(
                app, db, seed_user, seed_second_user,
            )
            second = SalaryProfile(
                user_id=ids["user_id"], scenario_id=seed_user["scenario"].id,
                name="Second",
                state_code="NC", is_active=True,
                filing_status_id=active.filing_status_id,
            )
            db.session.add(second)
            start_test_pay_list(second, Decimal("2000.00"))  # $52,000.00 a year / 26
            db.session.flush()
            params_by_account[ids["acct_b_id"]] = (
                TestLoadPayrollFeeds._params_for(ids["acct_b_id"], second.id)
            )
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            wiring = load_payroll_wiring(
                ids["user_id"], [ids["acct_a_id"], ids["acct_b_id"]],
                params_by_account,
            )
            assert set(wiring.profiles) == {active.id, second.id}

            def terms_for(profile):
                """Believe the Active raise through 2026; every other row as is."""
                return terms if profile.id == active.id else terms_of(profile.raises)

            feeds = price_payroll_feeds(wiring, pricing, terms_for)

            payday = self._payday_in(calendar, 2028)
            assert feeds[ids["acct_a_id"]].gross_at(payday) == Decimal("4038.46")
            assert feeds[ids["acct_b_id"]].gross_at(payday) == Decimal("2000.00")
            # The unchanged profile's pricer is the rows' own object; the
            # believed one is a different pricer, keyed on its terms.
            assert (
                pricing.for_profile(second, terms_for(second))
                is pricing.for_profile(second)
            )
            assert (
                pricing.for_profile(active, terms_for(active))
                is not pricing.for_profile(active)
            )

    def test_a_pricer_map_missing_a_wired_profile_is_REFUSED(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A public door's precondition, refused once by name.

        Without it the two resolvers answer the same mistake two ways: the
        employer half reads ``pricers.get`` and reports the account as funding
        no employer money -- the answer reserved for an absent, archived or
        foreign profile, which the wiring has already filtered out -- while
        the employee half ``KeyError``s on the first period asked.
        """
        with app.app_context():
            ids, profile, params_by_account, _ = self._wired(
                app, db, seed_user, seed_second_user,
            )
            wiring = load_payroll_wiring(
                ids["user_id"], [ids["acct_a_id"]], params_by_account,
            )
            assert profile.id in wiring.profiles

            with pytest.raises(ValueError, match=f"{profile.id}"):
                build_payroll_feeds(wiring, {})

