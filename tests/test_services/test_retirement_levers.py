"""
Shekel Budget App -- Retirement Lever Solver Tests (P2a / P2b)

Covers the contribution solver's annuity-factor math (against an engine
replay oracle), the headroom facts, the retire-later binary search and its
degenerate states, and the producer's consistency with the readiness
picture, which plan step C2-f2d-2 made an IDENTITY rather than an equality.

**Every hand-computed figure assumes the BIWEEKLY cadence** (:data:`_BIWEEKLY`),
which plan step R7a-2a made an explicit input to the headroom division where it
was a hardcoded 26.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.models.investment_params import InvestmentParams
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import account_service, retirement_levers, retirement_readiness
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, PeriodWindow
from app.services.growth_engine import project_balance
from app.services.pay_rhythm import FixedDays
from app.services.retirement_gap_calculator import RetirementGapAnalysis
from app.services.retirement_levers import (
    _annuity_factor,
    _contribution_outcome,
    _headroom_per_period,
)
from app.services.retirement_plan import load_retirement_inputs, picture_at
from app.utils.dates import add_months
from app.utils.money import round_money

from tests._test_helpers import biweekly_window, start_test_pay_list

#: 14 days between paydays, 26 a year -- the cadence the seeded scenarios
#: build and every hand-computed figure here assumes.
_BIWEEKLY = PayCadence(FixedDays(14))


def _delayed(inputs, months):
    """The picture at the owner's stored plan, delayed *months* whole months.

    What the retire-later lever's own ``probe_at`` does, spelled once here so a
    case grades the solver rather than a second spelling of its search axis.

    Args:
        inputs: The render's ``RetirementInputs``.
        months: The delay in whole months.

    Returns:
        The ``RetirementPicture`` at that delay.
    """
    return picture_at(
        inputs, replace(inputs.stored_plan, month_offset=months),
    )


# ── Fakes ────────────────────────────────────────────────────────


def _fake_baseline(*, required, after_tax_projected):
    """A minimal net-frame analysis for the pure outcome-math tests.

    ``_contribution_outcome`` reads exactly the two figures named here, which
    is why it takes the ANALYSIS rather than the whole retirement picture
    (plan step C2-f2d-2): a double for the picture would have to carry an
    axis, a projection list and a pension summary that nothing under test
    looks at, and every one of those would be a chance to state something
    false about the case.
    """
    return RetirementGapAnalysis(
        pre_retirement_net_monthly=Decimal("0"),
        monthly_pension_income=Decimal("0"),
        after_tax_monthly_pension=Decimal("0"),
        monthly_income_gap=Decimal("0"),
        required_retirement_savings=required,
        projected_total_savings=after_tax_projected,
        savings_surplus_or_shortfall=after_tax_projected - required,
        safe_withdrawal_rate=Decimal("0.04"),
        after_tax_projected_savings=after_tax_projected,
        after_tax_surplus_or_shortfall=after_tax_projected - required,
    )


class _FakeLimitedProjection(dict):
    """Per-account projection dict carrying only the headroom inputs."""

    def __init__(self, limit, employee_per_period):
        super().__init__(
            annual_contribution_limit=limit,
            employee_per_period=employee_per_period,
        )


# ── P2a: annuity factor ──────────────────────────────────────────


class TestAnnuityFactor:
    def test_zero_return_is_period_count(self):
        """At 0% return AF is exactly the period count.

        Every compound factor is (1 + 0)^k = 1, so a $1-per-period stream
        is worth $1 * n at the horizon: AF = n = 5.
        """
        periods = biweekly_window(date(2030, 1, 1), 5)
        assert len(periods) == 5
        assert _annuity_factor(periods, Decimal("0")) == Decimal("5")

    def test_empty_axis_is_zero(self):
        # No periods -> a contribution stream has nowhere to land: AF = 0.
        assert _annuity_factor(
            PeriodWindow(periods=()), Decimal("0.07"),
        ) == Decimal("0")

    def test_matches_engine_replay_within_rounding(self):
        """AF * C reproduces an engine run of C per period from a zero start.

        The engine applies growth to the OPENING balance then adds the
        contribution (end = start * (1+r) + C), which is exactly the
        AF = sum over p of prod_{q>p}(1+r_q) timing.  The only difference
        is rounding: the engine rounds growth to the cent each period (max
        half a cent per period), so over n=26 periods the drift is bounded
        by 26 * $0.005 = $0.13.  Assert within double that bound.
        """
        periods = biweekly_window(date(2030, 1, 2), 26)
        assert len(periods) == 26
        annual_return = Decimal("0.07")
        contribution = Decimal("100.00")

        af = _annuity_factor(periods, annual_return)
        closed_form = round_money(contribution * af)

        engine_rows = project_balance(
            current_balance=Decimal("0"),
            assumed_annual_return=annual_return,
            periods=periods,
            periodic_contribution=contribution,
        )
        engine_end = engine_rows[-1].end_balance
        # |engine - closed form| <= 2 * (26 periods * $0.005) = $0.26.
        assert abs(engine_end - closed_form) <= Decimal("0.26"), (
            f"engine {engine_end} vs closed form {closed_form}"
        )


# ── P2a: outcome math and headroom ───────────────────────────────


class TestContributionOutcome:
    def test_solved_amount_reaches_full_funding(self):
        """round(shortfall / AF) funds the plan to 1.0000.

        required 1,000,000.00; after-tax projected 700,000.00 ->
        shortfall 300,000.00.  AF = 520 (zero-return 520-period stream):
        solved = round(300000 / 520) = round(576.9230...) = 576.92.
        Outcome: extra = round(576.92 * 520) = 299,998.40;
        projected = 700,000 + 299,998.40 = 999,998.40;
        funded = 999998.40 / 1000000 = 0.99999840 -> quantize 0.0001 ->
        1.0000 (the half-cent rounding loss of $1.60 is 1.6e-6 of the
        requirement, far below the ratio quantum);
        surplus = 999,998.40 - 1,000,000.00 = -1.60.
        """
        baseline = _fake_baseline(
            required=Decimal("1000000.00"),
            after_tax_projected=Decimal("700000.00"),
        )
        af = Decimal("520")
        solved = round_money(
            (baseline.required_retirement_savings
             - baseline.after_tax_projected_savings) / af
        )
        assert solved == Decimal("576.92")

        outcome = _contribution_outcome(baseline, af, solved)
        assert outcome["projected_after_tax"] == Decimal("999998.40")
        assert outcome["funded_ratio"] == Decimal("1.0000")
        assert outcome["no_savings_needed"] is False
        assert outcome["surplus_or_shortfall"] == Decimal("-1.60")

    def test_zero_amount_keeps_baseline_funding(self):
        """A $0 stepper value reproduces the baseline funded ratio.

        extra = round(0 * AF) = 0 -> projected unchanged at 700,000 ->
        funded = 700000 / 1000000 = 0.7000.
        """
        baseline = _fake_baseline(
            required=Decimal("1000000.00"),
            after_tax_projected=Decimal("700000.00"),
        )
        outcome = _contribution_outcome(
            baseline, Decimal("520"), Decimal("0"),
        )
        assert outcome["funded_ratio"] == Decimal("0.7000")
        assert outcome["projected_after_tax"] == Decimal("700000.00")

    def test_zero_requirement_reports_no_savings_needed(self):
        """required == 0 -> the distinct state, never a division."""
        baseline = _fake_baseline(
            required=Decimal("0"),
            after_tax_projected=Decimal("50000.00"),
        )
        outcome = _contribution_outcome(
            baseline, Decimal("520"), Decimal("100.00"),
        )
        assert outcome["no_savings_needed"] is True
        assert outcome["funded_ratio"] is None


class TestHeadroomPerPeriod:
    """The per-period room an annual contribution limit leaves.

    Every limit below is divisible by 26 so the biweekly arithmetic is exact;
    :data:`_BIWEEKLY` states the cadence those figures were computed at, which
    plan step R7a-2a made an argument where it was a hardcoded 26.
    """

    def test_a_weekly_owner_has_half_the_room_per_paycheck(self):
        """The same $5,200 cap is $200 a paycheck biweekly and $100 weekly.

        Hand-computed: 5200 / 26 = 200.00 and 5200 / 52 = 100.00, with no
        current employee contribution.  The divisor has to match how often the
        contribution actually FIRES: telling a weekly owner they have $200 of
        room per paycheck would have them contribute $10,400 against a $5,200
        cap over the year.  The pair is the control -- one assertion alone
        would pass against either hardcoded number.
        """
        projections = [_FakeLimitedProjection(Decimal("5200"), Decimal("0"))]
        assert _headroom_per_period(
            projections, _BIWEEKLY,
        ) == Decimal("200.00")
        assert _headroom_per_period(
            projections, PayCadence(FixedDays(7)),
        ) == Decimal("100.00")

    def test_hand_computed_aggregate(self):
        """Two capped accounts sum their per-period room.

        limits 2600 and 5200 (chosen divisible by 26): per-period caps
        2600/26 = 100.00 and 5200/26 = 200.00; current employee 50.00 and
        100.00 -> rooms 50.00 and 100.00 -> aggregate 150.00.
        """
        projections = [
            _FakeLimitedProjection(Decimal("2600"), Decimal("50.00")),
            _FakeLimitedProjection(Decimal("5200"), Decimal("100.00")),
        ]
        assert _headroom_per_period(projections, _BIWEEKLY) == Decimal("150.00")

    def test_over_contributed_account_floors_at_zero(self):
        """An account already past its per-period cap contributes no room.

        limit 2600 -> cap 100.00/period; employee 150.00 -> room
        max(100 - 150, 0) = 0; second account limit 5200 -> cap 200.00,
        employee 0 -> room 200.00; aggregate 200.00.
        """
        projections = [
            _FakeLimitedProjection(Decimal("2600"), Decimal("150.00")),
            _FakeLimitedProjection(Decimal("5200"), Decimal("0")),
        ]
        assert _headroom_per_period(projections, _BIWEEKLY) == Decimal("200.00")

    def test_any_unlimited_account_makes_headroom_unbounded(self):
        """One account without a known limit -> None (no honest finite cap)."""
        projections = [
            _FakeLimitedProjection(Decimal("2600"), Decimal("50.00")),
            _FakeLimitedProjection(None, Decimal("0")),
        ]
        assert _headroom_per_period(projections, _BIWEEKLY) is None


# ── Seeded scenarios ─────────────────────────────────────────────


def _seed_scenario(db, seed_user, *, balance, annual_return,
                   pension_multiplier=None, months_out=240):
    """Seed a salary profile, optional pension, settings date, and a 401(k).

    The retirement date lives on the settings row (add_months(today,
    months_out)); an optional pension adds a benefit against the same
    date.  Returns the created account.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    filing = db.session.query(FilingStatus).first()
    retirement_date = add_months(date.today(), months_out)

    profile = SalaryProfile(
        period_end_days_before_payday=6,
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

    settings = (
        db.session.query(UserSettings).filter_by(user_id=user.id).one()
    )
    settings.planned_retirement_date = retirement_date

    if pension_multiplier is not None:
        db.session.add(PensionProfile(
            user_id=user.id,
            salary_profile_id=profile.id,
            name="State Pension",
            benefit_multiplier=pension_multiplier,
            consecutive_high_years=4,
            hire_date=date(2010, 1, 1),
            planned_retirement_date=retirement_date,
            is_active=True,
        ))

    inv_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=inv_type.id,
            name="401k",
            anchor_balance=balance,
        ),
    )
    db.session.flush()
    db.session.add(InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=annual_return,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE
        ),
    ))
    db.session.commit()
    return acct


class TestComputeLeverData:
    """The full producer against seeded scenarios."""

    def test_no_horizon_state(self, app, db, seed_user, seed_periods_today):
        """No pension date and no settings date -> the no_horizon state."""
        with app.app_context():
            data = retirement_levers.compute_lever_data(
                load_retirement_inputs(
                    BalanceContext.build(seed_user["user"].id),
                ),
            )
            assert data["no_horizon"] is True
            assert "contribution" not in data

    def test_underfunded_solvable_scenario(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The real-shaped underfunded case: both levers solve.

        $100k 401(k) at 10.5%, no pension, retirement in 240 months, no
        contributions.  The projected balance (~$100k * 1.105^19.9 ~ $730k)
        falls short of the ~$1.5M requirement (funded well under 1), but
        10.5% growth over the extra window crosses 100% within +180 months
        (100k reaches 15x after ~27 years ~ +86 months), so the
        retire-later lever must solve strictly inside the cap.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_scenario(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            data = retirement_levers.compute_lever_data(
                load_retirement_inputs(BalanceContext.build(user_id)),
            )
            assert data["no_horizon"] is False

            baseline = data["baseline"]
            # Deeply underfunded at the plan date (margin argument in the
            # docstring: ~730k projected vs ~$1.2-1.5M required).
            assert baseline["no_savings_needed"] is False
            assert baseline["funded_ratio"] < Decimal("1")

            # The lever baseline states the readiness picture's own figures.
            # It is worth saying plainly what this assertion IS since plan step
            # C2-f2d-2: the two used to be independent derivations and this
            # compared them, which is a real consistency check; they are now
            # one memoized object, so equality here holds by construction.
            # ``TestOnePicturePerPlan`` below asserts the identity that makes it
            # so -- the check with teeth -- and this stays as the statement of
            # what the page shows.
            page_inputs = load_retirement_inputs(
                BalanceContext.build(user_id),
            )
            readiness = retirement_readiness.readiness_from_picture(
                picture_at(page_inputs, page_inputs.stored_plan),
            )
            assert baseline["funded_ratio"] == readiness["funded_ratio"]
            assert baseline["required_savings"] == readiness["required_savings"]
            assert baseline["projected_after_tax"] == (
                readiness["projected_savings_after_tax"]
            )

            # P2a: solved, positive amount, funded 1.0000 at the solution.
            # Bound: the solved amount is rounded to the cent (max half-cent
            # error), so |amount * AF - shortfall| <= 0.005 * AF (~0.005 *
            # 900 ~ $4.50) -- under 1e-5 of a ~$1M requirement, which the
            # 0.0001 ratio quantum absorbs to exactly 1.0000.
            contribution = data["contribution"]
            assert contribution["state"] == "solved"
            assert contribution["solved_amount"] > Decimal("0")
            assert contribution["amount"] == contribution["solved_amount"]
            assert contribution["funded_ratio"] == Decimal("1.0000")
            # The scenario's params row has no annual_contribution_limit ->
            # headroom unbounded -> never flagged.
            assert contribution["headroom_per_period"] is None
            assert contribution["exceeds_headroom"] is False

            # P2b: solved strictly inside the cap, and minimal: funded at
            # the solved offset, NOT funded one month earlier.
            retire_later = data["retire_later"]
            assert retire_later["state"] == "solved"
            solved_months = retire_later["solved_months"]
            assert 1 <= solved_months <= 180
            assert retire_later["months"] == solved_months
            assert retire_later["funded_ratio"] >= Decimal("1")
            inputs = load_retirement_inputs(BalanceContext.build(user_id))
            assert _delayed(inputs, solved_months).is_funded
            assert not _delayed(inputs, solved_months - 1).is_funded
            # The displayed date is the stored plan shifted by the offset.
            assert retire_later["retirement_date"] == add_months(
                inputs.base_date, solved_months,
            )

    def test_stepper_overrides(self, app, db, seed_user, seed_periods_today):
        """Overrides drive the displayed outcome without re-solving.

        contribution_override=0 must reproduce the baseline funded ratio
        (extra = round(0 * AF) = 0); months_override=24 must report the
        probe(24) picture and the plan date shifted 24 months.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_scenario(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(BalanceContext.build(user_id))
            data = retirement_levers.compute_lever_data(
                inputs,
                contribution_override=Decimal("0"),
                months_override=24,
            )

            contribution = data["contribution"]
            assert contribution["amount"] == Decimal("0")
            # $0 extra changes nothing: funded == baseline funded.
            assert contribution["funded_ratio"] == (
                data["baseline"]["funded_ratio"]
            )
            # The solved default is still reported for the caption.
            assert contribution["solved_amount"] > Decimal("0")

            retire_later = data["retire_later"]
            assert retire_later["months"] == 24
            probe_24 = _delayed(inputs, 24)
            assert retire_later["funded_ratio"] == probe_24.funded_state[0]
            assert retire_later["retirement_date"] == probe_24.retirement_date

    def test_already_funded_scenario(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A $5M balance is already funded: both levers report it.

        $5M at 10.5% projects to ~$36M by the plan date against a ~$1.5M
        requirement -> funded >> 1: contribution solved_amount 0.00 and
        retire-later offset 0.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_scenario(
                db, seed_user,
                balance=Decimal("5000000.00"),
                annual_return=Decimal("0.10500"),
            )
            data = retirement_levers.compute_lever_data(
                load_retirement_inputs(BalanceContext.build(user_id)),
            )
            assert data["baseline"]["funded_ratio"] >= Decimal("1")
            assert data["contribution"]["state"] == "already_funded"
            assert data["contribution"]["solved_amount"] == Decimal("0.00")
            assert data["retire_later"]["state"] == "already_funded"
            assert data["retire_later"]["solved_months"] == 0
            assert data["retire_later"]["months"] == 0

    def test_not_within_cap_scenario(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A hopeless plan reports not_within_cap with the at-cap facts.

        A $5,000 balance at 0.5% return against an ~80k-salary income
        target with a token 0.1% pension: the requirement stays around
        $1.4M while the balance barely moves ($5k * 1.005^35 < $6k), so
        funded stays far below 1 even at +180 months -- the honest
        degenerate state, no silent clamp.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_scenario(
                db, seed_user,
                balance=Decimal("5000.00"),
                annual_return=Decimal("0.00500"),
                pension_multiplier=Decimal("0.00100"),
            )
            data = retirement_levers.compute_lever_data(
                load_retirement_inputs(BalanceContext.build(user_id)),
            )
            retire_later = data["retire_later"]
            assert retire_later["state"] == "not_within_cap"
            assert retire_later["solved_months"] is None
            assert retire_later["months"] is None
            # The at-cap facts are surfaced for the caption.
            assert retire_later["funded_ratio"] < Decimal("1")
            assert retire_later["retirement_date"] == add_months(
                add_months(date.today(), 240), 180,
            )
            # The contribution lever still solves (money always closes it).
            assert data["contribution"]["state"] == "solved"
            assert data["contribution"]["solved_amount"] > Decimal("0")


class TestPastHorizon:
    """M1: a positive shortfall with zero remaining periods is honest."""

    def test_past_date_reports_past_horizon_not_funded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A stored past planned date yields the past_horizon state.

        The hero's picture stays a shortfall (funded < 1: the $10k
        account cannot cover an ~$80k-salary income target), so the
        pre-fix "already_funded" collapse was a direct contradiction.
        With zero periods no per-period solution exists: solved_amount
        and amount are None, the outcome facts are the baseline picture
        ($0 extra applied), and no headroom flag can fire on a None
        amount.
        """
        from datetime import timedelta

        with app.app_context():
            user_id = seed_user["user"].id
            _seed_scenario(
                db, seed_user,
                balance=Decimal("10000.00"),
                annual_return=Decimal("0.07000"),
            )
            settings = db.session.query(UserSettings).filter_by(
                user_id=user_id,
            ).one()
            settings.planned_retirement_date = (
                date.today() - timedelta(days=30)
            )
            db.session.commit()

            data = retirement_levers.compute_lever_data(
                load_retirement_inputs(BalanceContext.build(user_id)),
            )
            assert data["no_horizon"] is False
            # The hero side: still a shortfall.
            assert data["baseline"]["funded_ratio"] < Decimal("1")

            contribution = data["contribution"]
            assert contribution["state"] == "past_horizon"
            assert contribution["solved_amount"] is None
            assert contribution["amount"] is None
            assert contribution["exceeds_headroom"] is False
            # $0 extra over zero periods leaves the baseline funded ratio.
            assert contribution["funded_ratio"] == (
                data["baseline"]["funded_ratio"]
            )
