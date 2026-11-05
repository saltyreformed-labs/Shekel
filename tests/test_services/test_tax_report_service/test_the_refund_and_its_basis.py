"""The refund and what it rests on: the assumptions card naming the stubs that priced the estimated
paychecks, one fully modeled profile, a CTC-heavy household's federal refund being its
refundable ACTC, and a checkpoint moving the refund by its difference.
"""

from datetime import date
from decimal import Decimal

from app.enums import WithholdingKindEnum
from app.extensions import db as _db
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services.tax_report_service import TaxReport, compute_tax_report
from tests._test_helpers import add_test_pay_stub

from ._helpers import ZERO, _committed_profile, _make_full_year_periods, _derived, _project_sum


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
