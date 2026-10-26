"""
Shekel Budget App -- Retirement Readiness Producer Tests (P1c)

Covers ``retirement_readiness.readiness_from_picture`` and its pure
helpers: the net-frame funded verdict (ruling 2), the F1 missing-tax-rate
flag, the downsampled flight-path chart series, the countdown facts, and
the per-account contribution facts.

Every seeded case goes through :func:`_readiness`, which runs the ROUTE's own
sequence -- load the render's inputs once, derive the picture at a plan point,
shape the readiness dict from it -- so a test can never exercise a path the
page does not (plan step C2-f2d-2).
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, RaiseTypeEnum
from app.models.investment_params import InvestmentParams
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.user import UserSettings
from app.services import account_service, retirement_readiness
from app.services.balance_at import BalanceContext
from app.services.retirement_gap_calculator import (
    RetirementGapAnalysis,
    funded_ratio_state,
)
from app.services.pension_calculator import PensionBenefit
from app.services.retirement_plan import load_retirement_inputs, picture_at
from app.services.retirement_readiness import (
    _build_countdown,
    _build_income_meter,
    _build_pension_lines,
    _downsample_indices,
)
from app.services.pay_calendar import PeriodWindow
from app.utils.money import round_money
from app.utils.dates import add_months

from tests._test_helpers import derived_window, start_test_pay_list


def _readiness(user_id, **whatif):
    """The readiness dict a /retirement render publishes at *point*.

    The route's own three steps, in one place: build the render's inputs from
    its read pass, derive the picture at the plan point, shape the dict.  A
    test that spelled those out per case would be free to drift from what the
    route does -- and the class of defect this arc removes is exactly two
    spellings of one sequence.

    Args:
        user_id: The owner to render for.
        whatif: Any what-if overrides, resolved through ``plan_with`` exactly
            as the route resolves them.

    Returns:
        The readiness dict.
    """
    inputs = load_retirement_inputs(BalanceContext.build(user_id))
    return retirement_readiness.readiness_from_picture(
        picture_at(inputs, inputs.plan_with(**whatif)),
    )


def _gap_analysis(*, required, after_tax_projected):
    """Build a minimal net-frame gap analysis for the funded_ratio_state unit tests."""
    return RetirementGapAnalysis(
        pre_retirement_net_monthly=Decimal("5000.00"),
        monthly_pension_income=Decimal("2000.00"),
        after_tax_monthly_pension=Decimal("2000.00"),
        monthly_income_gap=Decimal("3000.00"),
        required_retirement_savings=required,
        projected_total_savings=after_tax_projected,
        savings_surplus_or_shortfall=after_tax_projected - required,
        safe_withdrawal_rate=Decimal("0.0400"),
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=after_tax_projected - required,
    )


class TestFundedRatio:
    """The funded-ratio guard (ruling 2 / fork F1's division guard)."""

    def test_positive_requirement_divides(self):
        """funded = after-tax projected / required, quantized to 0.0001.

        100,000 / 200,000 = 0.5000 exactly.
        """
        ratio, no_needed = funded_ratio_state(
            _gap_analysis(
                required=Decimal("200000.00"),
                after_tax_projected=Decimal("100000.00"),
            )
        )
        assert no_needed is False
        assert ratio == Decimal("0.5000")

    def test_zero_requirement_is_no_savings_needed(self):
        """A zero requirement reports the distinct state, not a division.

        required == 0 (pension fully covers the gap) -> funded_ratio None,
        no_savings_needed True (never a divide-by-zero).
        """
        ratio, no_needed = funded_ratio_state(
            _gap_analysis(
                required=Decimal("0"),
                after_tax_projected=Decimal("100000.00"),
            )
        )
        assert no_needed is True
        assert ratio is None


class TestDownsampleIndices:
    """The chart downsampler (<= 48 points, first and last always kept)."""

    def test_short_series_returns_all(self):
        # 3 <= 48 -> every index kept, in order.
        assert _downsample_indices(3) == [0, 1, 2]

    def test_empty_series(self):
        assert _downsample_indices(0) == []

    def test_long_series_is_bounded_and_ends_inclusive(self):
        """520 periods downsample to <= 48 points, first=0 and last=519.

        The indices are strictly increasing (de-duplicated) so no plotted
        point repeats.
        """
        indices = _downsample_indices(520)
        assert len(indices) <= 48
        assert indices[0] == 0            # first always included
        assert indices[-1] == 519         # last always included
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)   # no duplicates


class TestBuildCountdown:
    """The countdown facts."""

    def test_no_horizon(self):
        """No retirement date -> zeroed countdown, no date."""
        assert _build_countdown(
            None, PeriodWindow(periods=()), date.today(),
        ) == {
            "periods_remaining": 0,
            "years_remaining": Decimal("0.0"),
            "retirement_date": None,
        }

    def test_years_and_periods(self):
        """periods_remaining is the AXIS length; years is days/365.25.

        A retirement date 365 days out gives 365 / 365.25 = 0.9993... ->
        1.0 (one decimal, round-half-up); the axis's period count passes
        straight through.

        **The count is the owner's own cadence** since plan step C2-e -- the
        axis is derived from their paydays, where it used to be a hardcoded
        14-day rhythm (ledger row **P20**).  Seven weekly periods here, which
        the old producer could not have expressed at all.
        """
        as_of = date.today()
        planned = add_months(as_of, 12)
        days = (planned - as_of).days
        axis = derived_window(
            [as_of + timedelta(days=7 * step) for step in range(7)], 7,
        )
        result = _build_countdown(planned, axis, as_of)
        assert result["periods_remaining"] == 7
        assert result["retirement_date"] == planned
        expected_years = (
            Decimal(days) / Decimal("365.25")
        ).quantize(Decimal("0.1"))
        assert result["years_remaining"] == expected_years


def _build_scenario(db, seed_user, *, tax_rate=None, with_pension=True):
    """Seed a salary profile, a pension with a horizon, and a 401(k).

    Returns the created account.  ``account_service.create_account`` anchors
    against the current pay period (the ``seed_periods_today`` fixture makes
    one exist), so the projection has a live current period.  With
    ``with_pension=False`` the horizon comes from the settings row instead
    (the meter-reconciliation test wants a zero-pension income mix).
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    filing = db.session.query(FilingStatus).first()

    profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing.id,
        name="Day Job",
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("3076.92"))  # $80,000.00 a year / 26
    db.session.flush()

    if with_pension:
        db.session.add(PensionProfile(
            user_id=user.id,
            salary_profile_id=profile.id,
            name="State Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2010, 1, 1),
            planned_retirement_date=date(date.today().year + 20, 6, 1),
            is_active=True,
        ))
    else:
        settings = (
            db.session.query(UserSettings)
            .filter_by(user_id=user.id)
            .one()
        )
        settings.planned_retirement_date = date(
            date.today().year + 20, 6, 1,
        )

    inv_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=inv_type.id,
            name="401k",
            anchor_balance=Decimal("50000.00"),
        ),
    )
    db.session.flush()
    db.session.add(InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE
        ),
    ))

    if tax_rate is not None:
        settings = (
            db.session.query(UserSettings)
            .filter_by(user_id=user.id)
            .one()
        )
        settings.estimated_retirement_tax_rate = tax_rate
    db.session.commit()
    return acct


class TestComputeReadinessData:
    """The full readiness producer against a seeded scenario."""

    def test_tax_rate_missing_net_frame_and_chart(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Unset tax rate -> F1 explicit-0% frame, and the chart invariants.

        With no estimated tax rate: ``tax_rate_missing`` True, the effective
        rate is Decimal("0"), so net pension == gross pension and after-tax
        projected == pre-tax projected (0% tax on the traditional 401(k)).
        The funded ratio matches its own components; the chart series share
        one downsampled axis whose endpoints equal the producer's own
        totals; the countdown reports the pension horizon.
        """
        with app.app_context():
            acct = _build_scenario(db, seed_user)
            data = _readiness(seed_user["user"].id)

            # F1: missing rate is an explicit 0% with the surfaced flag.
            assert data["tax_rate_missing"] is True
            assert data["estimated_tax_rate"] == Decimal("0")
            # 0% tax -> net == gross and after-tax == pre-tax.
            assert data["pension_net_monthly"] == data["pension_gross_monthly"]
            assert (
                data["projected_savings_after_tax"]
                == data["projected_savings_pretax"]
            )

            # Funded ratio is internally consistent with its components.
            assert data["required_savings"] > Decimal("0")
            assert data["funded_ratio"] == (
                data["projected_savings_after_tax"] / data["required_savings"]
            ).quantize(Decimal("0.0001"))
            assert data["no_savings_needed"] is False
            assert data["surplus_or_shortfall_after_tax"] == (
                data["projected_savings_after_tax"] - data["required_savings"]
            )

            # Chart: both series + dates share one <= 48-point axis, all
            # string Decimals; the endpoints equal the producer's totals.
            chart = data["chart"]
            count = len(chart["your_path"])
            assert count == len(chart["needed_path"]) == len(chart["dates"])
            assert 2 <= count <= 48
            for encoded in chart["your_path"] + chart["needed_path"]:
                Decimal(encoded)  # must parse (string-Decimal encoding)
            # "your path" is AFTER-TAX (ruling 2): it ends at the after-tax
            # projected total -- the funded ratio's numerator -- so the
            # chart endpoint and the hero verdict agree.  With the F1
            # explicit-0% rate, after-tax == pre-tax (x * (1 - 0) = x), so
            # both equalities hold on untaxed data.
            assert Decimal(chart["your_path"][-1]) == (
                data["projected_savings_after_tax"]
            )
            assert Decimal(chart["your_path"][-1]) == (
                data["projected_savings_pretax"]
            )
            # "needed path" ends at the required target (reverse anchor).
            assert Decimal(chart["needed_path"][-1]) == data["required_savings"]

            # Countdown: the pension horizon drives the facts.
            assert data["retirement_date"] == date(date.today().year + 20, 6, 1)
            assert data["periods_remaining"] > 0
            assert data["years_remaining"] > Decimal("0")

            # Per-account facts: one row, none linked (no deduction/transfer).
            assert len(data["account_contributions"]) == 1
            fact = data["account_contributions"][0]
            assert fact["account"].id == acct.id
            assert fact["none_linked"] is True

    def test_all_cancelled_contributions_still_read_as_LINKED(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account funded only by CANCELLED transfers is not "none linked".

        The regression an adversarial review caught at plan step X-au-c2.
        ``none_linked`` is a PRESENCE question -- *is anything wired up to fund
        this account?* -- and it used to read the length of the contribution
        list the loader returned.  Moving the ``status_contributes_to_balance``
        screen to that loader emptied the list for an account whose every
        contribution is Cancelled, which would flip this row from
        ``you $0.00 / employer $0.00`` to a "link a contribution" call-to-action
        against an account that HAS one.

        Nothing graded it: the case above covers an account with no
        contribution at all, where both readings agree.  This one is the case
        where they disagree, so it is the one that pins the fix.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum
        from tests._test_helpers import create_transfer

        with app.app_context():
            acct = _build_scenario(db, seed_user)
            period = seed_periods_today[0]
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], acct, period,
                amount=Decimal("400.00"),
            )
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            transfer.status_id = cancelled_id
            db.session.commit()

            data = _readiness(seed_user["user"].id)

            fact = next(
                f for f in data["account_contributions"]
                if f["account"].id == acct.id
            )
            # It contributes NOTHING ...
            assert fact["employee_per_period"] == Decimal("0")
            # ... but something is linked, so the prompt stays off.
            assert fact["none_linked"] is False

    def test_tax_rate_applied_reduces_pension_and_traditional(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A stored 20% rate nets the pension and taxes the traditional 401(k).

        pension_net == round(pension_gross * (1 - 0.20)); the traditional
        401(k)'s after-tax projection is strictly below its pre-tax value;
        and the chart's "your path" endpoint equals the AFTER-TAX projected
        total (ruling 2: the chart plots the same frame as the verdict).
        The scenario's only account is a traditional 401(k), so the
        endpoint also equals round(pretax * (1 - 0.20)) exactly.
        """
        with app.app_context():
            _build_scenario(db, seed_user, tax_rate=Decimal("0.2000"))
            data = _readiness(seed_user["user"].id)

            assert data["tax_rate_missing"] is False
            assert data["estimated_tax_rate"] == Decimal("0.2000")
            # pension net = gross * (1 - 0.20).
            assert data["pension_net_monthly"] == round_money(
                data["pension_gross_monthly"] * Decimal("0.80")
            )
            # Chart endpoint == after-tax projected == funded numerator.
            chart = data["chart"]
            assert Decimal(chart["your_path"][-1]) == (
                data["projected_savings_after_tax"]
            )
            # All-traditional portfolio: after-tax = round(pretax * 0.80).
            assert data["projected_savings_after_tax"] == round_money(
                data["projected_savings_pretax"] * Decimal("0.80")
            )
            # And the endpoint therefore matches the funded ratio numerator:
            # funded = after_tax / required (quantized 0.0001).
            assert data["funded_ratio"] == (
                Decimal(chart["your_path"][-1]) / data["required_savings"]
            ).quantize(Decimal("0.0001"))
            # The 401(k) is pre-tax, so after-tax projected < pre-tax.
            assert (
                data["projected_savings_after_tax"]
                < data["projected_savings_pretax"]
            )


class TestExplicitZeroTaxRate:
    """L1: a saved 0% rate is a real estimate, never "missing"."""

    def test_stored_zero_is_set_not_missing(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An explicit Decimal("0.0000") rate clears the F1 missing flag.

        Zero is a value (E-12): tax_rate_missing is True ONLY for NULL.
        The arithmetic is the F1 identity -- at a real 0% rate the net
        pension equals the gross (x * (1 - 0) = x) and after-tax
        projected equals pre-tax -- but the FLAG must read "set".
        Pre-fix, resolve_estimated_tax_rate's truthiness collapsed the
        stored zero into None, rendering "Not set -- 0% assumed" forever.
        """
        with app.app_context():
            _build_scenario(db, seed_user, tax_rate=Decimal("0.0000"))
            data = _readiness(seed_user["user"].id)
            assert data["tax_rate_missing"] is False
            assert data["estimated_tax_rate"] == Decimal("0.0000")
            # x * (1 - 0) = x on both after-tax figures.
            assert data["pension_net_monthly"] == data["pension_gross_monthly"]
            assert (
                data["projected_savings_after_tax"]
                == data["projected_savings_pretax"]
            )


class TestComputeReadinessWhatif:
    """The P3a what-if wrapper: baseline, override state, and deltas."""

    def test_no_overrides_returns_baseline_without_deltas(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No overrides: the displayed state IS the baseline, deltas None."""
        with app.app_context():
            _build_scenario(db, seed_user)
            result = retirement_readiness.compute_readiness_whatif(
                load_retirement_inputs(
                    BalanceContext.build(seed_user["user"].id),
                ),
            )
            assert result["deltas"] is None
            assert result["readiness"] is result["baseline"]

    def test_swr_override_deltas(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A 2% SWR what-if (vs the stored 4%) doubles the requirement.

        required = round(monthly_gap * 12 / swr) and the monthly gap does
        not depend on the SWR, so the override requirement is exactly
        round(monthly_gap * 12 / 0.02).  Funded drops (same after-tax
        projection over a larger requirement), so the points delta is
        negative and equals (override - baseline) * 100 quantized to 0.1;
        the dollars delta is the surplus difference and is negative (the
        position worsens by the extra requirement).
        """
        with app.app_context():
            _build_scenario(db, seed_user)
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            result = retirement_readiness.compute_readiness_whatif(
                inputs, inputs.plan_with(swr_override=Decimal("0.02")),
            )
            baseline = result["baseline"]
            override = result["readiness"]
            deltas = result["deltas"]

            # The gap itself is SWR-independent...
            assert override["monthly_gap_net"] == baseline["monthly_gap_net"]
            # ...so required_override = round(gap * 12 / 0.02) exactly.
            assert override["required_savings"] == round_money(
                baseline["monthly_gap_net"] * 12 / Decimal("0.02")
            )
            assert deltas["funded_ratio_points"] == (
                (override["funded_ratio"] - baseline["funded_ratio"])
                * Decimal("100")
            ).quantize(Decimal("0.1"))
            assert deltas["funded_ratio_points"] < 0
            assert deltas["shortfall_dollars"] == (
                override["surplus_or_shortfall_after_tax"]
                - baseline["surplus_or_shortfall_after_tax"]
            )
            assert deltas["shortfall_dollars"] < 0

    def test_a_stored_end_year_moves_the_target(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A raise's OWN end year reaches the readiness verdict end to end.

        **The wire this cutover has to prove** (plan step salary:S3-c,
        ruling **R-SAL11**).  A 5% recurring January raise is stored twice
        over: once believed indefinitely, once ending in its own effective
        year.  Believed indefinitely it compounds every projected year, so
        the final-year salary is larger, the net income target is larger,
        and the requirement is larger; ending immediately it applies once
        and stops.  A SMALLER requirement is therefore the ended one, and
        the inequality pins the column against the verdict rather than
        against the walk -- through ``project_profile_salaries``,
        ``compute_pension_summary``, ``compute_gap_net_biweekly`` and the
        gap calculator, none of which this test names.

        *It was ``test_merit_horizon_override_moves_the_target`` and drove
        the same figures through ``plan_with(merit_horizon_override=0)``.
        That axis was a SETTING on the owner, and S3-c left no plan-point
        axis over the salary path, so the case drives the stored fact instead
        -- which is the thing S3-c actually changed, and the what-if was never
        the subject.  Plan step salary:S3-f-2b added the per-raise probe
        (``plan_with(raise_probes=...)``); the stored fact is still what THIS
        case grades, and ``tests/test_services/test_retirement_plan.py``
        grades the probe.*
        """
        with app.app_context():
            _build_scenario(db, seed_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            effective_year = date.today().year
            raise_row = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
                effective_month=1,
                effective_year=effective_year,
                percentage=Decimal("0.0500"),
                is_recurring=True,
            )
            db.session.add(raise_row)
            db.session.commit()

            believed_forever = retirement_readiness.compute_readiness_whatif(
                load_retirement_inputs(
                    BalanceContext.build(seed_user["user"].id),
                ),
            )["baseline"]

            # The SAME raise, now believed only through the year it starts.
            raise_row.terminal_year = effective_year
            db.session.commit()

            ends_at_once = retirement_readiness.compute_readiness_whatif(
                load_retirement_inputs(
                    BalanceContext.build(seed_user["user"].id),
                ),
            )["baseline"]

            assert (
                ends_at_once["required_savings"]
                < believed_forever["required_savings"]
            ), (
                "the stored terminal_year did not reach the readiness "
                "verdict: a raise believed only through "
                f"{effective_year} produced the same requirement as one "
                "believed forever, so some producer on the path is still "
                "projecting a salary the raise rows do not describe"
            )
            assert (
                ends_at_once["funded_ratio"]
                > believed_forever["funded_ratio"]
            )


def _net_for_meter(*, target, pension_net, after_tax_projected):
    """Build a net-frame gap analysis carrying only the meter's inputs."""
    return RetirementGapAnalysis(
        pre_retirement_net_monthly=target,
        monthly_pension_income=pension_net,
        after_tax_monthly_pension=pension_net,
        monthly_income_gap=max(target - pension_net, Decimal("0")),
        required_retirement_savings=Decimal("0"),
        projected_total_savings=after_tax_projected,
        savings_surplus_or_shortfall=Decimal("0"),
        safe_withdrawal_rate=Decimal("0.0400"),
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=Decimal("0"),
    )


class TestBuildIncomeMeter:
    """P3c review pins for the P3b income-meter producer.

    The meter must reconcile with the gap calculator's own figures:
    net pension + withdrawals + uncovered == income target (exact -- the
    uncovered remainder is DEFINED as that residual, floored at zero),
    and the two segment percentages can never sum past 100.
    """

    def test_reconciliation_identity_and_withdrawal_formula(self):
        """Under-covered case: the four figures reconcile exactly.

        target 5,000.00; net pension 2,000.00; after-tax projected
        600,000.00 at 4% SWR:
          withdrawals = round(600000 * 0.04 / 12) = round(2000) = 2000.00
          uncovered   = 5000 - 2000 - 2000 = 1000.00
          identity: 2000 + 2000 + 1000 == 5000 (exact, no rounding slack)
          pension_pct = 2000/5000*100 = 40.0; withdrawals_pct = 40.0.
        """
        net = _net_for_meter(
            target=Decimal("5000.00"),
            pension_net=Decimal("2000.00"),
            after_tax_projected=Decimal("600000.00"),
        )
        meter = _build_income_meter(net, Decimal("0.0400"))
        assert meter["withdrawals_net_monthly"] == Decimal("2000.00")
        assert meter["uncovered_monthly"] == Decimal("1000.00")
        assert (
            net.after_tax_monthly_pension
            + meter["withdrawals_net_monthly"]
            + meter["uncovered_monthly"]
        ) == net.pre_retirement_net_monthly
        assert meter["pension_pct"] == Decimal("40.0")
        assert meter["withdrawals_pct"] == Decimal("40.0")

    def test_over_covered_segments_clamp_to_100(self):
        """Over-covered case: uncovered floors at 0, segments cap at 100.

        target 3,000.00; net pension 2,500.00; withdrawals 2,000.00
        (600,000 * 0.04 / 12):
          pension_pct    = min(100, 2500/3000*100 = 83.333...) -> 83.3
          withdrawals_pct = min(100 - 83.3 = 16.7, 66.666...) -> 16.7
          sum = 100.0 exactly; uncovered = max(0, 3000-2500-2000) = 0.00.
        """
        net = _net_for_meter(
            target=Decimal("3000.00"),
            pension_net=Decimal("2500.00"),
            after_tax_projected=Decimal("600000.00"),
        )
        meter = _build_income_meter(net, Decimal("0.0400"))
        assert meter["uncovered_monthly"] == Decimal("0.00")
        assert meter["pension_pct"] == Decimal("83.3")
        assert meter["withdrawals_pct"] == Decimal("16.7")
        assert meter["pension_pct"] + meter["withdrawals_pct"] == Decimal(
            "100.0"
        )

    def test_zero_target_zeroes_the_meter(self):
        """No income target: both segment widths are zero, no division."""
        net = _net_for_meter(
            target=Decimal("0.00"),
            pension_net=Decimal("1000.00"),
            after_tax_projected=Decimal("100000.00"),
        )
        meter = _build_income_meter(net, Decimal("0.0400"))
        assert meter["pension_pct"] == Decimal("0.0")
        assert meter["withdrawals_pct"] == Decimal("0.0")
        assert meter["uncovered_monthly"] == Decimal("0.00")

    def test_seeded_meter_reconciles_with_the_verdict_frame(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Against real engine output the identity still holds exactly.

        A no-pension scenario guarantees an uncovered remainder (net
        pension 0.00; a $50k account cannot cover an ~$80k-salary income
        target), so pension_net + withdrawals + uncovered == target with
        zero rounding slack, and the withdrawal figure re-derives from
        the producer's own after-tax projection at its own SWR.
        """
        with app.app_context():
            _build_scenario(db, seed_user, with_pension=False)
            data = _readiness(seed_user["user"].id)
            meter = data["income_meter"]
            assert data["pension_net_monthly"] == Decimal("0.00")
            assert meter["uncovered_monthly"] > Decimal("0")
            # Identity: pension_net + withdrawals + uncovered == target.
            assert (
                data["pension_net_monthly"]
                + meter["withdrawals_net_monthly"]
                + meter["uncovered_monthly"]
            ) == data["income_target_net_monthly"]
            # Withdrawals = round(after-tax projected * SWR / 12), from
            # the producer's own figures.
            assert meter["withdrawals_net_monthly"] == round_money(
                data["projected_savings_after_tax"]
                * data["safe_withdrawal_rate"] / 12
            )
            assert (
                meter["pension_pct"] + meter["withdrawals_pct"]
            ) <= Decimal("100")


class TestBuildPensionLines:
    """P3c review pins for the P3b per-pension derivation lines (D6)."""

    @staticmethod
    def _entry(name, monthly, window_years):
        """One per_pension entry with a hand-built benefit."""
        return {
            "name": name,
            "benefit_multiplier": Decimal("0.01850"),
            "consecutive_high_years": 4,
            "benefit": PensionBenefit(
                years_of_service=Decimal("30.00"),
                high_salary_average=Decimal("100000.00"),
                annual_benefit=monthly * 12,
                monthly_benefit=monthly,
                high_salary_years=[
                    (year, Decimal("100000.00")) for year in window_years
                ],
            ),
        }

    def test_line_facts_and_net_keep_fraction(self):
        """One line per pension; net/mo = round(gross * (1 - rate)).

        gross 4,625.00/mo at a 20% estimated tax:
          net = round(4625.00 * 0.80) = 3,700.00
        window years [2043..2046] -> window_start 2043, window_end 2046.
        """
        lines = _build_pension_lines(
            [self._entry("State", Decimal("4625.00"), [2043, 2044, 2045, 2046])],
            Decimal("0.2000"),
        )
        assert len(lines) == 1
        line = lines[0]
        assert line["gross_monthly"] == Decimal("4625.00")
        assert line["net_monthly"] == Decimal("3700.00")
        assert line["window_start"] == 2043
        assert line["window_end"] == 2046
        assert line["years_of_service"] == Decimal("30.00")
        assert line["high_salary_average"] == Decimal("100000.00")

    def test_two_pensions_two_lines_gross_sums_to_gap_row(self):
        """D6: every pension gets a line; grosses sum to the summed row.

        Two benefits 4,625.00 and 1,000.00 -> two named lines whose gross
        total (5,625.00) equals what the gap row sums -- the old card's
        last-pension-only rendering could never satisfy this.
        """
        lines = _build_pension_lines(
            [
                self._entry("State", Decimal("4625.00"), [2043]),
                self._entry("County", Decimal("1000.00"), [2046]),
            ],
            Decimal("0"),
        )
        assert [line["name"] for line in lines] == ["State", "County"]
        assert sum(
            (line["gross_monthly"] for line in lines), Decimal("0")
        ) == Decimal("5625.00")
        # 0% keep-fraction identity: net == gross at the F1 explicit zero.
        assert all(
            line["net_monthly"] == line["gross_monthly"] for line in lines
        )

    def test_seeded_two_pension_lines_match_summed_income(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Seeded D6 case: two real pensions -> two lines, exact gross sum."""
        with app.app_context():
            _build_scenario(db, seed_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.add(PensionProfile(
                user_id=seed_user["user"].id,
                salary_profile_id=profile.id,
                name="County Pension",
                benefit_multiplier=Decimal("0.01000"),
                consecutive_high_years=4,
                hire_date=date(2015, 1, 1),
                planned_retirement_date=date(date.today().year + 20, 6, 1),
                is_active=True,
            ))
            db.session.commit()

            data = _readiness(seed_user["user"].id)
            lines = data["pension_lines"]
            assert len(lines) == 2
            # The per-line grosses sum EXACTLY to the summed pension row
            # (both are the same monthly_benefit Decimals).
            assert sum(
                (line["gross_monthly"] for line in lines), Decimal("0")
            ) == data["pension_gross_monthly"]
