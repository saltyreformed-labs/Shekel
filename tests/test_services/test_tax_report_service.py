"""
Shekel Budget App -- Analytics Taxes Report Service Tests (T-P3)

Hand-confirmed assertions for ``tax_report_service.compute_tax_report``: the
producer that fuses the T-P1 annual liability and the T-P2
withholding-to-date into the refund, hero chips, hybrid W-2 preview, and
Schedule A check the analytics Taxes tab (T-P4) renders.

The law is the shipped one, the default every test prices under (ruling
salary:R-SAL80) -- the same figures the signup seed copied to every user until
plan step salary:X-at-1 -- so every figure anchors on 2026's (single, NC,
standard deduction 16,100; brackets 10/12/22/24/32/35/37; NC flat 3.99%, std
ded 12,750; FICA ss_wage_base 184,500).

Where the withholding total is delegated to ``project_salary`` (Pub 15-T
per-period, not hand-summable), the tests pin it against an INDEPENDENT
``project_salary`` oracle over the same periods -- the T-P2 pattern -- and
hand-compute everything the producer itself derives (the annual liability,
the refund identity, the rate chips, the W-2 boxes, the next-payday hint).

Periods are 26 biweekly paydays starting 2026-01-02 (indices 1..26), built
directly (the seed_user bootstrap sits in 2024 and is out of the 2026
year-periods query the producer runs):

    i=0 01-02  i=1 01-16  i=2 01-30 ... i=12 06-19  i=13 07-03 ... i=25 12-18
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, PaycheckLineKindEnum, WithholdingKindEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services import balance_at, paycheck_calculator
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.tax_report_service import (
    TaxReport,
    compute_tax_report,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    SPLIT_LOAN,
    payroll_basis,
    create_loan_with_trueup,
    create_settled_transfer,
    add_test_pay_stub,
    freeze_today,
    start_test_pay_list,
)

ZERO = Decimal("0")


# ── Helpers ───────────────────────────────────────────────────────


def _make_profile(
    seed_user,
    *,
    name="Tax Report Profile",
    filing_status_name="single",
    state_code="NC",
    pay="5000.00",  # $130,000.00 a year / 26
    additional_income="0.00",
    qualifying_children=0,
    other_dependents=0,
    sort_order=0,
):
    """Build and flush an active salary profile for the seeded user.

    Mirrors the liability/withholding test profile helpers.  Salary
    defaults to 130,000 (5,000.00/period at 26, no residue).  ``name`` is a
    parameter because ``uq_salary_profiles_user_scenario_name`` forbids two
    same-named profiles for one user + scenario.
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
        additional_income=Decimal(additional_income),
        additional_deductions=Decimal("0.00"),
        qualifying_children=qualifying_children,
        other_dependents=other_dependents,
        sort_order=sort_order,
    )
    _db.session.add(profile)
    # From the 2026 calendar's first payday: every caller builds its profile
    # BEFORE ``_make_full_year_periods``, when that day is still a PROJECTED
    # payday off the seed owner's 2024 bootstrap (52 x 14 days on).
    start_test_pay_list(profile, Decimal(pay), date(2026, 1, 2))
    _db.session.flush()
    return profile


def _committed_profile(seed_user, **kwargs):
    """Build and flush a profile; it prices under the shipped law."""
    profile = _make_profile(seed_user, **kwargs)
    _db.session.flush()
    return profile


def _make_full_year_periods(user, count=26, start=date(2026, 1, 2)):
    """Create *count* biweekly 2026 pay periods directly (period_index 1..N).

    Built directly rather than via ``seed_periods`` (10 only) so the full
    26-pay 2026 year exists; the producer's 2026 year-periods query returns
    exactly these (the seed_user bootstrap is a 2024 row).
    """
    periods = []
    for i in range(count):
        start_date = start + timedelta(days=14 * i)
        period = PayPeriod(user_id=user.id, start_date=start_date)
        _db.session.add(period)
        periods.append(period)
    _db.session.flush()
    return periods


def _add_pretax_deduction(
    profile, amount, name="401k", kind=PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
):
    """Add a flat pre-tax deduction taken every period (26/year).

    ``kind`` is which of the two pre-tax kinds the line is (plan step
    salary:S11-e): the default, "Pre-tax, income tax only" (a 401k), leaves
    the income-tax base alone; ``PRE_FICA_DEDUCTION``, "Pre-tax, all payroll
    taxes" (a Health premium), leaves the Social Security and Medicare wages
    as well.
    """
    flat = _db.session.query(CalcMethod).filter_by(name="flat").one()
    ded = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(kind),
        calc_method_id=flat.id,
        name=name,
        amount=Decimal(amount),
    )
    _db.session.add(ded)
    _db.session.flush()



def _derived(user_id, year=2026):
    """The year's pay periods AS THE PRODUCER SEES THEM.

    ``_load_year_periods`` reads the owner's DERIVED calendar and filters it to
    the tax year since pay-calendar plan step C2-f2d-3, so the oracle below is
    handed the same shape rather than the ORM rows the fixtures create.
    """
    return tuple(
        period for period in calendar_for(user_id).saved()
        if period.start_date.year == year
    )


def _project_sum(profile, year, periods):
    """Independent oracle: sum ``project_salary`` over *periods*.

    Same configs SSOT and engine as the producer (one year's law: no case
    here seeds a pay stub, the only thing that reads another year's since
    plan step salary:S11-c-2c, ruling **R-SAL77**).  For the
    flat, sub-cap, no-deduction scenarios here the full-context and
    subset-restart projections coincide (all figures below the 184,500 SS
    wage base and 200,000 Medicare surtax threshold), so this stays a
    genuinely independent check of the producer's dollar values.

    **Since plan step balance:X-bh-1 there is no "subset restart" to
    coincide with**: the engine reads the year's paydays off the owner's
    CALENDAR, which this oracle is now handed too, so both sides carry the
    same year-to-date context and what stays independent is the period
    SUBSET each is asked to sum.
    """
    configs = load_tax_configs_for_year(profile, year)
    breakdowns = paycheck_calculator.project_salary(
        payroll_basis(profile, periods), periods, configs,
    )
    return {
        "gross": sum((b.earnings.gross_biweekly for b in breakdowns), ZERO),
        "federal": sum((b.taxes.federal for b in breakdowns), ZERO),
        "state": sum((b.taxes.state for b in breakdowns), ZERO),
        "ss": sum((b.taxes.social_security for b in breakdowns), ZERO),
        "medicare": sum((b.taxes.medicare for b in breakdowns), ZERO),
    }


# ── Withholding basis: what priced the modeled paychecks ──────────


class TestWithholdingBasis:
    """The assumptions card names the stubs that priced the ESTIMATED paychecks.

    Plan step salary:S11-c-2c, ruling **R-SAL100** (its words **R-SAL121**):
    the basis is read off the engine's breakdowns of the modeled remainder
    (the paydays after the checkpoint), per job, so it names a stub exactly
    when the engine priced one of that job's modeled paychecks from it.  The 26 paydays run
    01-02 .. 12-18 every 14 days; the stubs' figures are made up and no
    figure is asserted, only which basis priced which paycheck.
    """

    @staticmethod
    def _stub(profile, payday):
        """Record a switched-on stub of *profile* on *payday* (made-up taxes)."""
        add_test_pay_stub(
            profile, payday, "5000.00",
            taxes={
                WithholdingKindEnum.FEDERAL_INCOME: "600.00",
                WithholdingKindEnum.STATE_INCOME: "180.00",
                WithholdingKindEnum.SOCIAL_SECURITY: "290.00",
                WithholdingKindEnum.MEDICARE: "70.00",
            },
        )

    @staticmethod
    def _checkpoint(profile, as_of):
        """Record a checkpoint dated *as_of* (made-up figures; none is read here)."""
        _db.session.add(YtdTaxCheckpoint(
            salary_profile_id=profile.id, as_of_date=as_of,
            ytd_gross=Decimal("1000.00"), ytd_federal=Decimal("100.00"),
            ytd_state=Decimal("40.00"), ytd_social_security=Decimal("60.00"),
            ytd_medicare=Decimal("15.00"),
        ))

    def _bases(self, seed_user):
        """The report's per-job withholding bases for 2026, today 2026-03-01."""
        _db.session.commit()
        report = compute_tax_report(seed_user["user"].id, 2026, date(2026, 3, 1))
        return report.withholding.bases

    def test_the_formulas_then_the_stub(self, app, db, seed_user):
        """A 03-13 stub: the five earlier paydays take the formulas, the rest the stub."""
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        self._stub(profile, date(2026, 3, 13))

        (basis,) = self._bases(seed_user)

        assert basis.stub_paydays == (date(2026, 3, 13),)
        assert basis.formulas_priced is True

    def test_every_pricing_stub_is_named_in_payday_order(self, app, db, seed_user):
        """Two stubs: each prices the paychecks from its payday on, both are named."""
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        self._stub(profile, date(2026, 6, 19))
        self._stub(profile, date(2026, 1, 2))

        (basis,) = self._bases(seed_user)

        assert basis.stub_paydays == (date(2026, 1, 2), date(2026, 6, 19))
        assert basis.formulas_priced is False

    def test_a_checkpoint_after_the_stub_leaves_only_the_stub(self, app, db, seed_user):
        """The measured paydays are not estimated, so the formulas' paychecks drop out."""
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        self._stub(profile, date(2026, 3, 13))
        self._checkpoint(profile, date(2026, 6, 19))

        (basis,) = self._bases(seed_user)

        assert basis.stub_paydays == (date(2026, 3, 13),)
        assert basis.formulas_priced is False

    def test_a_switched_off_stub_is_not_named(self, app, db, seed_user):
        """The engine skips a stub switched off, so the card does not name it."""
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        self._stub(profile, date(2026, 3, 13))
        profile.pay_stubs[0].use_for_pricing = False

        (basis,) = self._bases(seed_user)

        assert basis.stub_paydays == ()
        assert basis.formulas_priced is True

    def test_each_job_is_named_with_its_own_stubs(self, app, db, seed_user):
        """Two jobs: the stub prices the first job's paychecks only (the review's MED-1).

        The first job's 03-13 stub leaves its five earlier paydays to the
        formulas; the second job has no stub, so the formulas price its whole
        year.  A union of the two would read "the tax formulas, then your
        03-13 stub" for a filer whose second job no stub ever priced.
        """
        first = _committed_profile(seed_user, name="First Job", sort_order=0)
        _committed_profile(seed_user, name="Second Job", sort_order=1)
        _make_full_year_periods(seed_user["user"])
        self._stub(first, date(2026, 3, 13))

        bases = self._bases(seed_user)

        assert [(b.job, b.stub_paydays, b.formulas_priced) for b in bases] == [
            ("First Job", (date(2026, 3, 13),), True),
            ("Second Job", (), True),
        ]

    def test_a_checkpoint_covering_the_year_leaves_nothing_estimated(
        self, app, db, seed_user,
    ):
        """Every payday measured: no basis to name, and no formulas either."""
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        self._stub(profile, date(2026, 3, 13))
        self._checkpoint(profile, date(2026, 12, 31))

        assert self._bases(seed_user) == ()


# ── Anchor: single profile, fully modeled ─────────────────────────


class TestSingleProfileFullyModeled:
    """130,000 single/NC, no checkpoint, no pay stub, 26 periods."""

    def test_end_to_end_anchor(self, app, db, seed_user):
        """Refund, chips, W-2, and Schedule A on the locked anchor.

        Withholding is fully modeled (no checkpoint); gross is exact
        (130,000 / 26 = 5,000.00 * 26 = 130,000.00) and the four withholding
        lines match the project_salary oracle.

        Federal liability (2026 single, std ded 16,100):
          taxable = 130,000 - 0 - 16,100 = 113,900.00
          10%: 12,400 * 0.10               =  1,240.00
          12%: (50,400-12,400) * 0.12      =  4,560.00
          22%: (105,700-50,400) * 0.22     = 12,166.00
          24%: (113,900-105,700) * 0.24    =  1,968.00
          liability                        = 19,934.00 (0 credits)
        NC state (flat 3.99%, std ded 12,750):
          taxable = 130,000 - 12,750 = 117,250.00
          raw = 117,250 * 0.0399 = 4,678.2750 -> 4,678.28 (ROUND_HALF_UP)
        Effective rate = (19,934.00 + 4,678.28) / 130,000
                       = 24,612.28 / 130,000 = 0.1893252... -> 0.1893
        Marginal = 24% (113,900 in (105,700, 201,775]).
        next_stub with today 2026-03-01: first payday > 03-01 is 2026-03-13.
        """
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        periods = _derived(seed_user["user"].id)
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )
        oracle = _project_sum(profile, 2026, periods)

        assert isinstance(report, TaxReport)
        # Withholding: gross exact, lines == oracle, fully modeled.
        assert report.withholding.total.gross == Decimal("130000.00")
        assert report.withholding.total.federal == oracle["federal"]
        assert report.withholding.total.state == oracle["state"]
        assert report.withholding.measured_through is None
        assert report.withholding.has_checkpoint is False

        # Liability (hand-computed).
        assert report.liability.annual_wage_income == Decimal("130000.00")
        assert report.liability.annual_pretax == ZERO
        assert report.liability.federal.taxable == Decimal("113900.00")
        assert report.liability.federal.liability == Decimal("19934.00")
        assert report.liability.state.liability == Decimal("4678.28")

        # Refund = withheld - liability, federal + state.
        assert report.refund.federal_refund == (
            report.withholding.total.federal - Decimal("19934.00")
        )
        assert report.refund.state_refund == (
            report.withholding.total.state - Decimal("4678.28")
        )
        assert report.refund.total_refund == (
            report.refund.federal_refund + report.refund.state_refund
        )

        # Chips.
        assert report.chips.effective_rate == Decimal("0.1893")
        assert report.chips.marginal_rate == Decimal("0.2400")
        assert report.chips.next_stub == date(2026, 3, 13)

        # W-2 preview (no pre-tax -> box1 == gross; SS uncapped at 130k).
        assert report.w2_preview.wages.box1_wages == Decimal("130000.00")
        assert report.w2_preview.wages.box3_ss_wages == Decimal("130000.00")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("130000.00")
        assert report.w2_preview.wages.box16_state_wages == Decimal("130000.00")
        assert report.w2_preview.withheld.box2_federal == (
            report.withholding.total.federal
        )
        assert report.w2_preview.withheld.box17_state_withheld == (
            report.withholding.total.state
        )
        assert report.w2_preview.measured_through is None

        # Assumptions (single profile).
        assert report.assumptions.filing.filing_status_name == "single"
        assert report.assumptions.filing.state_code == "NC"
        assert report.assumptions.active_profile_count == 1
        assert report.assumptions.filing_inputs_from is None
        # No pay stub: the tax formulas priced every modeled paycheck
        # (R-SAL100; the card read the calibration's state until plan step
        # salary:S11-c-2c deleted it).
        assert [(b.stub_paydays, b.formulas_priced) for b in report.withholding.bases] == [
            ((), True),
        ]
        assert report.assumptions.disclosures.checkpoint_as_of_date is None
        assert report.assumptions.disclosures.pretax_modeled_for_elapsed is False
        # T-P5: nonrefundable_credit_clamp retired in favour of the honest
        # ACTC disclosures (the refundable credit IS now modeled).
        assert report.assumptions.disclosures.actc_modeled is True
        assert report.assumptions.disclosures.phase_out_not_modeled is True

        # Schedule A (no loans -> mortgage 0; state component == withheld).
        assert report.schedule_a.components.mortgage_interest == ZERO
        assert report.schedule_a.components.state_income_tax == (
            report.withholding.total.state
        )
        assert report.schedule_a.components.property_tax is None
        assert report.schedule_a.property_tax_included is False
        assert report.schedule_a.salt_cap_not_applied is True
        assert report.schedule_a.standard_deduction == Decimal("16100.00")
        assert report.schedule_a.itemized_estimate == (
            report.withholding.total.state
        )
        assert report.schedule_a.margin == (
            report.schedule_a.itemized_estimate - Decimal("16100.00")
        )


class TestActcDrivesFederalRefund:
    """T-P5: a CTC-heavy household's federal refund IS the refundable ACTC."""

    def test_actc_added_to_federal_refund(self, app, db, seed_user):
        """MFJ, 4 children, 78,000 (3,000/period): liability 0, ACTC drives refund.

        Federal (2026 MFJ, std ded 32,200; CTC 2,200; refundable cap 1,700):
          taxable  = 78000 - 32200 = 45,800.00
          brackets = 24800*0.10 + (45800-24800)*0.12 = 2480 + 2520 = 5,000.00
          credits  = 4 * 2200 = 8,800.00  ->  liability max(0, 5000-8800) = 0
          ACTC     = min(unused 3800, cap 6800, earned 11325) = 3,800.00
        So federal_refund = withheld - 0 + 3,800.00 -- the ACTC is the refund
        the old nonrefundable-clamp model (liability 0, refund = withheld)
        would have missed entirely.
        """
        _committed_profile(
            seed_user, filing_status_name="married_jointly",
            pay="3000.00", qualifying_children=4,  # $78,000.00 a year / 26
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.liability.federal.liability == Decimal("0.00")
        assert report.liability.federal.refundable_actc == Decimal("3800.00")
        # The ACTC is added on top of (withheld - liability).
        assert report.refund.federal_refund == (
            report.withholding.total.federal + Decimal("3800.00")
        )


# ── Checkpoint: measured differs from modeled ─────────────────────


class TestCheckpointMovesRefundByDelta:
    """A checkpoint whose measured federal differs moves the refund by it."""

    def test_federal_delta_and_box_tieouts(self, app, db, seed_user):
        """Elapsed-gross-matched checkpoint, +1,000 injected on federal.

        A checkpoint dated 2026-06-30 covers paydays i=0..12 (start
        <= 06-30; 06-19 is i=12).  Its ytd_gross is set to the modeled
        elapsed gross (5,000 * 13 = 65,000) so the ANNUAL wage income --
        and therefore the liability -- is unchanged from the fully-modeled
        case; ytd_state / SS / Medicare are set to their modeled elapsed
        values so only FEDERAL is perturbed, by exactly +1,000.

        Then:
          total.federal = checkpoint.federal + modeled_remainder.federal
                        = (elapsed_modeled.federal + 1,000) + remainder
                        = fully_modeled.federal + 1,000
        and (liability unchanged) refund.federal_refund moves by +1,000.
        Box 2 == total.federal == measured.federal + modeled.federal.
        """
        profile = _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        # Fully-modeled baseline (no checkpoint yet).
        base = compute_tax_report(seed_user["user"].id, 2026, date(2026, 8, 1))

        # Independent oracle over the elapsed subset (i=0..12).
        elapsed = [
            p for p in _derived(seed_user["user"].id)
            if p.start_date <= date(2026, 6, 30)
        ]
        assert len(elapsed) == 13  # 06-19 is the last elapsed payday
        elapsed_oracle = _project_sum(
            profile, 2026, elapsed,
        )
        assert elapsed_oracle["gross"] == Decimal("65000.00")

        cp = YtdTaxCheckpoint(
            salary_profile_id=profile.id,
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("65000.00"),
            ytd_federal=elapsed_oracle["federal"] + Decimal("1000.00"),
            ytd_state=elapsed_oracle["state"],
            ytd_social_security=elapsed_oracle["ss"],
            ytd_medicare=elapsed_oracle["medicare"],
        )
        db.session.add(cp)
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 8, 1),
        )

        # Wage income (and thus liability) unchanged: same 130,000.
        assert report.withholding.total.gross == Decimal("130000.00")
        assert report.liability.federal.liability == (
            base.liability.federal.liability
        )
        # Federal withholding moved by exactly the injected +1,000.
        assert report.withholding.total.federal == (
            base.withholding.total.federal + Decimal("1000.00")
        )
        # Refund moved by exactly the delta (liability held constant).
        assert report.refund.federal_refund == (
            base.refund.federal_refund + Decimal("1000.00")
        )
        # Split identity + measured verbatim + box tie-out.
        assert report.withholding.measured.federal == (
            elapsed_oracle["federal"] + Decimal("1000.00")
        )
        assert report.withholding.total.federal == (
            report.withholding.measured.federal
            + report.withholding.modeled.federal
        )
        assert report.w2_preview.withheld.box2_federal == (
            report.withholding.total.federal
        )
        assert report.w2_preview.measured_through == date(2026, 6, 30)
        assert report.assumptions.disclosures.checkpoint_as_of_date == (
            date(2026, 6, 30)
        )
        assert report.assumptions.disclosures.pretax_modeled_for_elapsed is True


# ── Pre-tax deductions ────────────────────────────────────────────


class TestPreTaxDeductions:
    """A 401k pre-tax line reduces box 1 and the liability taxable base."""

    def test_box1_and_taxable_use_modeled_pretax(self, app, db, seed_user):
        """500/period pre-tax (26/year) = 13,000 modeled annual pre-tax.

        gross stays 130,000; box 1 = 130,000 - 13,000 = 117,000.
        Federal taxable = 130,000 - 13,000 - 16,100 = 100,900.00:
          10%: 1,240.00 + 12%: 4,560.00 + 22%: (100,900-50,400)*0.22
             = 11,110.00  -> liability 16,910.00 (0 credits).
        Marginal = 22% (100,900 in (50,400, 105,700]).
        Schedule A state component == the hybrid state withholding.
        """
        profile = _committed_profile(seed_user)
        _add_pretax_deduction(profile, "500.0000")
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.liability.annual_pretax == Decimal("13000.00")
        assert report.liability.federal.taxable == Decimal("100900.00")
        assert report.liability.federal.liability == Decimal("16910.00")
        assert report.chips.marginal_rate == Decimal("0.2200")
        # Box 1 / 16 net of the modeled pre-tax; an income-tax-only line
        # leaves box 5 at the gross.
        assert report.w2_preview.wages.box1_wages == Decimal("117000.00")
        assert report.w2_preview.wages.box16_state_wages == Decimal("117000.00")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("130000.00")
        # Schedule A state component tie-out.
        assert report.schedule_a.components.state_income_tax == (
            report.withholding.total.state
        )


class TestAllPayrollTaxesLines:
    """An all-payroll-taxes line leaves W-2 boxes 3 and 5 as well as box 1.

    Plan step salary:S11-e (ruling plan#2639): a "Pre-tax, all payroll taxes"
    line (a Section 125 Health premium) leaves the Social Security and
    Medicare wages the employer's W-2 reports; a "Pre-tax, income tax only"
    line (a 401k) leaves box 1 alone.  Boxes 4 and 6 are the engine's own
    withholding, so each case also ties box 4 to box 3 x 6.2% and box 6 to
    box 5 x 1.45%: the preview's wages and its withholding are one walk.
    """

    def test_a_health_line_leaves_boxes_3_and_5(self, app, db, seed_user):
        """200/period Health (26/year) = 5,200 off every wage box.

        gross = 5,000.00 x 26 = 130,000.00.
        Box 1 = box 16 = 130,000 - 5,200 = 124,800 (both kinds leave it).
        Box 5 = 130,000 - 5,200 = 124,800.
        Box 3 = min(124,800, 184,500) = 124,800.
        Box 4 = 26 x (4,800.00 x 6.2%  = 297.60) = 7,737.60 = 124,800 x 6.2%.
        Box 6 = 26 x (4,800.00 x 1.45% =  69.60) = 1,809.60 = 124,800 x 1.45%.
        The line leaves the liability's taxable base too: annual pre-tax 5,200.
        """
        profile = _committed_profile(seed_user)
        _add_pretax_deduction(
            profile, "200.0000", name="Health",
            kind=PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("130000.00")
        assert report.liability.annual_pretax == Decimal("5200.00")
        wages = report.w2_preview.wages
        assert wages.box1_wages == Decimal("124800.00")
        assert wages.box16_state_wages == Decimal("124800.00")
        assert wages.box3_ss_wages == Decimal("124800.00")
        assert wages.box5_medicare_wages == Decimal("124800.00")
        assert report.w2_preview.withheld.box4_ss_withheld == Decimal("7737.60")
        assert report.w2_preview.withheld.box6_medicare_withheld == Decimal("1809.60")

    def test_both_kinds_part_at_boxes_3_and_5(self, app, db, seed_user):
        """500/period 401k + 200/period Health: box 1 drops both, boxes 3 / 5 only Health.

        Annual pre-tax = (500 + 200) x 26 = 18,200; all-payroll-taxes = 5,200.
        Box 1 = box 16 = 130,000 - 18,200 = 111,800.
        Box 3 = box 5 = 130,000 - 5,200 = 124,800 (the 401k stays in).
        Box 4 = 7,737.60 and box 6 = 1,809.60: the 401k moves neither.
        """
        profile = _committed_profile(seed_user)
        _add_pretax_deduction(profile, "500.0000")
        _add_pretax_deduction(
            profile, "200.0000", name="Health",
            kind=PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.liability.annual_pretax == Decimal("18200.00")
        wages = report.w2_preview.wages
        assert wages.box1_wages == Decimal("111800.00")
        assert wages.box16_state_wages == Decimal("111800.00")
        assert wages.box3_ss_wages == Decimal("124800.00")
        assert wages.box5_medicare_wages == Decimal("124800.00")
        assert report.w2_preview.withheld.box4_ss_withheld == Decimal("7737.60")
        assert report.w2_preview.withheld.box6_medicare_withheld == Decimal("1809.60")


# ── SS wage-base cap on box 3 ─────────────────────────────────────


class TestSocialSecurityWageCap:
    """Box 3 caps the gross at the year's SS wage base."""

    def test_box3_capped_above_wage_base(self, app, db, seed_user):
        """260,000 salary (> 184,500 base) -> box 3 = 184,500; box 5 raw.

        gross = 10,000.00 a paycheck (260,000 / 26) * 26 = 260,000.00.
        Box 3 (SS wages) = min(260,000, 184,500) = 184,500.
        Box 5 (Medicare wages) = 260,000 (raw gross, uncapped).
        Box 1 = 260,000 (no pre-tax).
        """
        profile = _committed_profile(
            seed_user, name="High Earner", pay="10000.00",  # $260,000.00 a year / 26
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("260000.00")
        assert report.w2_preview.wages.box3_ss_wages == Decimal("184500")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("260000.00")
        assert report.w2_preview.wages.box1_wages == Decimal("260000.00")
        # The salary the gross is 26 paychecks of: the profile's one pay
        # entry (plan step salary:X-av-3a replaced the yearly column).
        assert [entry.amount for entry in profile.pay_entries] == [
            Decimal("10000.00"),
        ]

    def test_the_lines_leave_box_3_before_the_cap(self, app, db, seed_user):
        """Gross above the wage base, Social Security wages below it: box 3 is not capped.

        gross = 7,200.00 x 26 = 187,200.00 (> 184,500); Health 200 x 26 = 5,200.
        Box 5 = 187,200 - 5,200 = 182,000.
        Box 3 = min(182,000, 184,500) = 182,000 -- the line leaves the wages
        BEFORE the cap: capping the gross first would read 184,500 (the
        preview before plan step salary:S11-e) or 184,500 - 5,200 = 179,300.
        Box 4 = 26 x (7,000.00 x 6.2% = 434.00) = 11,284.00 = 182,000 x 6.2%:
        the year never reaches the base, so no paycheck is capped.
        """
        profile = _committed_profile(
            seed_user, name="Near The Base", pay="7200.00",  # $187,200.00 a year / 26
        )
        _add_pretax_deduction(
            profile, "200.0000", name="Health",
            kind=PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("187200.00")
        assert report.w2_preview.wages.box5_medicare_wages == Decimal("182000.00")
        assert report.w2_preview.wages.box3_ss_wages == Decimal("182000.00")
        assert report.w2_preview.withheld.box4_ss_withheld == Decimal("11284.00")

    def test_box3_capped_after_the_lines_leave(self, app, db, seed_user):
        """260,000 gross less 5,200 of Health: box 3 caps at the base, box 5 does not.

        Box 5 = 260,000 - 5,200 = 254,800.
        Box 3 = min(254,800, 184,500) = 184,500.
        Box 4 = the statutory maximum, 184,500 x 6.2% = 11,439.00: 18
        paychecks of 9,800.00 x 6.2% = 607.60 (10,936.80 on 176,400 of
        wages), then the 19th taxed only to the base, 11,439.00 - 10,936.80 =
        502.20, then nothing.
        """
        profile = _committed_profile(
            seed_user, name="High Earner", pay="10000.00",  # $260,000.00 a year / 26
        )
        _add_pretax_deduction(
            profile, "200.0000", name="Health",
            kind=PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.w2_preview.wages.box5_medicare_wages == Decimal("254800.00")
        assert report.w2_preview.wages.box3_ss_wages == Decimal("184500")
        assert report.w2_preview.withheld.box4_ss_withheld == Decimal("11439.00")


# ── Degrade cases ─────────────────────────────────────────────────


class TestDegradeCases:
    """No profile -> None; no periods -> all-modeled zero report."""

    def test_no_active_profile_returns_none(self, app, db, seed_user):
        """A user with a baseline scenario but no active profile -> None."""
        db.session.commit()
        assert compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        ) is None

    def test_zero_period_year_degrades_to_zero(self, app, db, seed_user):
        """Profile but no 2026 periods -> zeros, no crash, effective None.

        Withholding is all zero (empty periods, no checkpoint); the
        liability is computed on wage 0 (taxable clamps to 0 -> 0), so both
        refunds are 0.  box 1 is 0 -> effective_rate None; no periods ->
        next_stub None.
        """
        _committed_profile(seed_user)
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report is not None
        assert report.withholding.total.gross == ZERO
        assert report.liability.annual_wage_income == ZERO
        assert report.liability.federal.taxable == ZERO
        assert report.liability.federal.liability == ZERO
        assert report.liability.state.liability == ZERO
        assert report.refund.federal_refund == ZERO
        assert report.refund.state_refund == ZERO
        assert report.refund.total_refund == ZERO
        assert report.chips.effective_rate is None
        assert report.chips.next_stub is None
        assert report.w2_preview.wages.box1_wages == ZERO
        assert report.w2_preview.wages.box3_ss_wages == ZERO


# ── Multi-profile (single filer, multiple jobs) ───────────────────


class TestMultiProfileSum:
    """Two active profiles sum; filing inputs come from the primary."""

    def test_sum_wages_primary_filing_inputs(self, app, db, seed_user):
        """Primary (single, 2 kids, 130k) + secondary (MFJ, 100k), summed.

        **The report's wages are what the 26 MODELLED PAYCHECKS pay, not the
        contract salaries**, and since plan step balance:X-aw those differ:
          $130,000 / 26 = $5,000.00 exactly  -> 26 x 5,000.00 = 130,000.00
          $100,000 / 26 = $3,846.1538... -> $3,846.15
                                          -> 26 x 3,846.15 =  99,999.90
          wages sum = 229,999.90, ten cents under the $230,000 of salary.
        That ten cents is the cost ruling **balance:R-HW** accepts; MED-05 /
        PA-07 had bought the round figure by giving the year's earliest
        paychecks an extra cent, which is finding N-239.  **Nothing here is
        inconsistent**: ``compute_annual_liability`` is handed
        ``withholding.total.gross``, so the liability is computed on the same
        wages the paychecks pay rather than on a salary nobody is paid.

        The liability uses the SUMMED wages with the PRIMARY's filing status
        (single) and 2 qualifying children:
          taxable = 229,999.90 - 0 - 16,100 = 213,899.90
          10%: 1,240.00 + 12%: 4,560.00 + 22%: 12,166.00
             + 24%: (201,775-105,700)*0.24 = 23,058.00
             + 32%: (213,899.90-201,775)*0.32 = 12,124.90*0.32 = 3,879.968
          before credits = 44,903.968 -> 44,903.97
          credits = 2 * 2,200 = 4,400
          liability = 40,503.97  (CTC $2,200 per OBBBA)
        Credit fully absorbed (44,903.97 > 4,400) so no ACTC.
        Marginal = 32% (213,899.90 in (201,775, 256,225]).
        Filing inputs disclosed as coming from the primary profile.
        """
        primary = _make_profile(
            seed_user, name="Primary Job", filing_status_name="single",
            pay="5000.00", qualifying_children=2, sort_order=0,  # $130,000.00 a year / 26
        )
        _make_profile(
            seed_user, name="Second Job",
            filing_status_name="married_jointly",
            pay="3846.15", sort_order=1,  # $100,000.00 a year / 26
        )
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("229999.90")
        assert report.liability.annual_wage_income == Decimal("229999.90")
        assert report.liability.federal.taxable == Decimal("213899.90")
        assert report.liability.federal.qualifying_children == 2
        assert report.liability.federal.liability == Decimal("40503.97")
        assert report.liability.federal.refundable_actc == Decimal("0.00")
        assert report.chips.marginal_rate == Decimal("0.3200")
        # Filing inputs disclosed from the primary.
        assert report.assumptions.filing.filing_status_name == "single"
        assert report.assumptions.active_profile_count == 2
        assert report.assumptions.filing_inputs_from == primary.name


# ── Schedule A mortgage-interest consistency oracle ───────────────


class TestScheduleAMortgageInterest:
    """The Schedule A mortgage term REUSES the year-end hybrid exactly.

    Three tests with deliberately different reach, because no one of them can do
    another's job:

    * :meth:`test_mortgage_interest_matches_year_end_hybrid` proves the WIRING --
      that the Taxes tab spends the seam's figure and ties it into
      ``itemized_estimate``.  Its oracle CALLS ``balance_at.loan_interest_in_year``,
      so it is a consistency check: it holds however wrong that function is.
    * :meth:`test_schedule_a_deducts_the_hand_computed_interest_in_the_year_paid`
      proves the VALUE, against arithmetic done by hand.  It is the only thing
      standing under this number, and it is deliberately on the LIVE path so it
      outlives the year-end summary service's deletion (plan F2 / R-D).
    * :meth:`test_a_car_loans_interest_is_not_home_mortgage_interest` proves the
      DOMAIN -- whose interest may enter the figure at all (N-9).

    All three seed the loan's KIND explicitly.  The wiring test did not, and
    silently asserted a nonzero home-mortgage deduction built from a car loan.
    """

    def test_mortgage_interest_matches_year_end_hybrid(
        self, app, db, seed_user, monkeypatch,
    ):
        """A seeded mortgage gives nonzero 2026 interest == the seam figure.

        Standing up a fresh liability/withholding fixture plus a full loan
        is disproportionate, so the mortgage term is pinned here as a
        CONSISTENCY ORACLE: the producer's
        ``schedule_a.components.mortgage_interest`` must equal
        ``balance_at.loan_interest_in_year`` for the same loan and context the
        orchestrator uses (both resolve the loan at the frozen 2026-06-01 clock),
        and be strictly positive (non-vacuous).  ``itemized_estimate`` then ties
        out to mortgage + hybrid state withholding.

        **This proves the wiring, NOT the number.**  The oracle below is the very
        function under test, so a defect inside it moves both sides together and
        this test stays green -- the shape the plan's Section 7.2 forbids relying
        on alone.  The value is pinned by the hand-computed test beneath.
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        # A MORTGAGE, which is what this test's name and docstring always
        # claimed: the fixture took ``create_loan_with_trueup``'s AUTO_LOAN
        # default, so before ``_load_mortgage_accounts`` existed this asserted
        # a nonzero HOME-MORTGAGE deduction built from a car loan.
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=SPLIT_LOAN.origination_principal,
            anchor_balance=SPLIT_LOAN.anchor_balance,
            anchor_date=SPLIT_LOAN.anchor_date,
            rate=SPLIT_LOAN.rate,
            origination_date=SPLIT_LOAN.origination_date,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 6, 1),
        )

        # Consistency oracle: the same seam producer for the same loan.  NOT an
        # independent check of the number -- see the class docstring.  With one
        # mortgage, _build_schedule_a's sum over mortgage accounts is exactly this.
        bctx = BalanceContext.build(seed_user["user"].id)
        oracle_interest = balance_at.loan_interest_in_year(loan, bctx, 2026)

        assert oracle_interest > ZERO  # non-vacuous
        assert report.schedule_a.components.mortgage_interest == oracle_interest
        assert report.schedule_a.itemized_estimate == (
            oracle_interest + report.withholding.total.state
        )
        assert report.schedule_a.standard_deduction == Decimal("16100.00")
        assert report.schedule_a.margin == (
            report.schedule_a.itemized_estimate - Decimal("16100.00")
        )
        assert report.schedule_a.components.property_tax is None

    def test_schedule_a_deducts_the_hand_computed_interest_in_the_year_paid(
        self, app, db, seed_user, monkeypatch,
    ):
        """THE VALUE: $500.00 of interest, PAID in 2025, deducts in 2025.

        The Taxes tab is the only live consumer of the mortgage-interest hybrid,
        and nothing pinned its NUMBER -- the test above spends the function under
        test as its own oracle.  So this hand-computes the figure end to end, on
        the live ``compute_tax_report`` path.

        The fixture is chosen so the answer is exactly derivable by hand rather
        than by re-running the amortization:

        * ``SPLIT_LOAN`` trues the loan up to $100,000.00 on 2026-01-10, so the
          resolver's schedule runs FORWARD from that anchor and its first row is
          2026-02-01.  **2025 therefore contains no amortization rows at all**
          (asserted below), which makes the year's figure PURE LEDGER -- the
          projected term is structurally zero, not incidentally zero.
        * The one payment splits against the balance in force at its due date:
          interest = 100000.00 * (0.06000 / 12) = **500.00**.
        * It is scheduled for 2026-02-01 but SETTLED 2025-12-20.  Mortgage
          interest deducts in the year PAID, so it belongs to 2025.

        The negative control is the year itself: a payment-DATE basis reports
        0.00 here (the payment's installment is a 2026 row), so the assertion can
        only pass on the paid-date basis the deduction actually requires.

        The loan is a MORTGAGE because :func:`_load_mortgage_accounts` selects
        only mortgages: an AUTO_LOAN here would contribute nothing and this
        oracle would vacuously pin $0.00.  ``create_loan_with_trueup`` defaults
        to AUTO_LOAN, and taking that default is what hid N-9 in the first place.
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        _committed_profile(seed_user)
        # The ORM rows: this case WRITES a transfer into one of them, which
        # needs the row a ``pay_period_id`` points at.
        periods = _make_full_year_periods(seed_user["user"])
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=SPLIT_LOAN.origination_principal,
            anchor_balance=SPLIT_LOAN.anchor_balance,
            anchor_date=SPLIT_LOAN.anchor_date,
            rate=SPLIT_LOAN.rate,
            origination_date=SPLIT_LOAN.origination_date,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        # Due 2026-02-01 (period index 1, payment_day 1); settled 2025-12-20.
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            periods[1], amount=Decimal("1000.00"),
            settled_on=date(2025, 12, 20),
        )
        db.session.commit()

        # The premise, asserted rather than assumed: 2025 carries no schedule
        # rows, so the figure below cannot be coming from the projection.
        bctx = BalanceContext.build(seed_user["user"].id)
        rows_2025 = [
            row
            for row in resolved_loan(loan, bctx).state.schedule
            if row.payment_date.year == 2025
        ]
        assert rows_2025 == []

        report = compute_tax_report(
            seed_user["user"].id, 2025, date(2026, 6, 1),
        )

        # 100000.00 * 0.06000 / 12 = 500.00, deducted in the year PAID.
        assert report.schedule_a.components.mortgage_interest == Decimal("500.00")
        assert report.schedule_a.itemized_estimate == (
            Decimal("500.00") + report.withholding.total.state
        )

    def test_a_car_loans_interest_is_not_home_mortgage_interest(
        self, app, db, seed_user, monkeypatch,
    ):
        """An AUTO_LOAN contributes NOTHING to the Schedule A mortgage term.

        Personal interest is not deductible.  The pre-fix ``_load_debt_accounts``
        selected on ``has_amortization`` alone -- which is set on AUTO_LOAN,
        STUDENT_LOAN, PERSONAL_LOAN and HELOC just as it is on MORTGAGE -- so
        this exact fixture reported **$5,221.16** of a car loan's interest as
        home mortgage interest (measured 2026-07-16 by restoring the old
        selection).

        The loan here is otherwise identical to the mortgage the sibling tests
        seed -- same principal, rate, dates, and true-up -- so the ONLY thing
        producing the zero is the account's KIND.

        Both assertions discriminate: restore the amortization-only selection and
        the first reads $5,221.16 and the second is off by the same.  ``margin``
        is deliberately NOT asserted -- the standard deduction happens to win
        either way here, so it would pass while the number was wrong, and this
        file has enough of those already.  The harm is not that this fixture
        flips the election; it is that the deduction is overstated by $5,221.16,
        which flips it for anyone near the threshold.
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        _committed_profile(seed_user)
        _make_full_year_periods(seed_user["user"])
        create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=SPLIT_LOAN.origination_principal,
            anchor_balance=SPLIT_LOAN.anchor_balance,
            anchor_date=SPLIT_LOAN.anchor_date,
            rate=SPLIT_LOAN.rate,
            origination_date=SPLIT_LOAN.origination_date,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 6, 1),
        )

        assert report.schedule_a.components.mortgage_interest == ZERO
        # Nothing but the state withholding is left to itemize.
        assert report.schedule_a.itemized_estimate == report.withholding.total.state
