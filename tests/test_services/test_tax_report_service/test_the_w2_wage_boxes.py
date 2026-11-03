"""The W-2 preview's wage boxes: a pre-tax line leaves box 1 and the liability's taxable base, an
all-payroll-taxes line leaves boxes 3 and 5 as well, and box 3 stops at the year's Social
Security wage base.
"""

from datetime import date
from decimal import Decimal

from app.enums import PaycheckLineKindEnum
from app.services.tax_report_service import compute_tax_report

from ._helpers import _committed_profile, _make_full_year_periods, _add_pretax_deduction


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

    def test_box1_floors_at_zero_when_pretax_exceeds_gross(self, app, db, seed_user):
        """6,000/period pre-tax against 5,000/period pay: box 1 is $0.00, never negative.

        Finding plan#2660: box 1 is the engine's income-tax wage rule,
        ``waterfall_taxable``, which floors at $0.00; it was spelled here by
        hand without the floor.
        gross = 5,000.00 x 26 = 130,000.00; pre-tax = 6,000.00 x 26 = 156,000.00.
        Box 1 = box 16 = max(130,000 - 156,000, 0) = 0.00 (the hand spelling
        gave -26,000.00).  The line is income-tax only, so box 5 stays at the
        gross, 130,000.00.  The effective rate stays None (box 1 not positive).
        """
        profile = _committed_profile(seed_user)
        _add_pretax_deduction(profile, "6000.0000")
        _make_full_year_periods(seed_user["user"])
        db.session.commit()

        report = compute_tax_report(
            seed_user["user"].id, 2026, date(2026, 3, 1),
        )

        assert report.withholding.total.gross == Decimal("130000.00")
        assert report.liability.annual_pretax == Decimal("156000.00")
        wages = report.w2_preview.wages
        assert wages.box1_wages == Decimal("0.00")
        assert wages.box16_state_wages == Decimal("0.00")
        assert wages.box5_medicare_wages == Decimal("130000.00")
        assert report.chips.effective_rate is None


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
