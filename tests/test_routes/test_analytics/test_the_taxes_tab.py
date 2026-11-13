"""The Taxes tab: its hero, chips, ledger, W-2 and Schedule A."""

import re
from datetime import date
from decimal import Decimal

from tests._test_helpers import start_test_pay_list

from ._helpers import _shell_autoload_target, _seed_taxes_profile


class TestTaxesTab:
    """Pins for the Taxes tab (T-P4): hero, chips, ledger, W-2, Schedule A.

    Hand-computed baseline (130k single/NC profile, seed_periods = 10
    paydays in 2026, no checkpoint, no pay stub, no deductions):

      per-period federal = round(19,934 / 26) = 766.69 -> x10 = 7,666.90
      per-period NC      = round(4,678.28 / 26) = 179.93 -> x10 = 1,799.30
      liability on hybrid gross 50,000:
        federal taxable 33,900 -> 1,240 + 21,500 x 0.12 = 3,820.00
        NC (50,000 - 12,750) x 0.0399 = 1,486.2750 -> 1,486.28
      federal refund 7,666.90 - 3,820.00 = 3,846.90
      NC refund      1,799.30 - 1,486.28 =   313.02
      total refund                       = 4,159.92
      effective (3,820 + 1,486.28) / 50,000 = 0.106126 -> 10.61%
      marginal: 33,900 sits in the 12,400-50,400 band -> 12.00%
    """

    def test_taxes_tab_hand_pinned_figures(self, app, auth_client, seed_user, seed_periods, db):
        """The full-modeled baseline renders every hand-computed figure."""
        with app.app_context():
            _seed_taxes_profile(seed_user, db)
            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Estimated refund" in html
            assert "$4,159.92" in html   # hero + ledger total
            assert "$3,846.90" in html   # federal refund chip + ledger
            assert "$313.02" in html     # NC refund chip + ledger
            assert "10.61%" in html      # effective rate chip
            assert "12.00%" in html      # marginal rate chip
            assert "modeled estimate" in html
            assert "withholding fully modeled" in html

            # Derivation ledger + W-2 tie-out: Box 2 == federal withheld.
            assert "How the estimate is derived" in html
            assert "$7,666.90" in html
            assert "$3,820.00" in html   # federal tax line
            assert "$1,486.28" in html   # NC tax line
            assert "W-2 preview" in html
            assert "$50,000.00" in html  # Box 1 / Box 3 / Box 5 wages
            assert "$1,799.30" in html   # Box 17 == NC withheld

            # Schedule A: no loans seeded -> itemized = state tax only,
            # standard deduction 16,100 wins by 16,100 - 1,799.30 = 14,300.70.
            assert "Schedule A check" in html
            assert "standard deduction wins by" in html
            assert "$14,300.70" in html

            # The YTD checkpoint card and assumptions card are present.
            assert "ytd-checkpoint-card" in html
            assert "Assumptions" in html
            # No pay stub seeded: the tax formulas priced every estimated
            # paycheck (R-SAL100, words R-SAL121; "bracket model" until plan
            # step salary:S11-c-2c deleted the calibration it stood against).
            text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
            assert "Withholding basis the tax formulas Measured through" in text

    def test_taxes_tab_names_the_formulas_then_each_pricing_stub(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The Withholding basis row lists what priced the estimated paychecks.

        Plan step salary:S11-c-2c, ruling R-SAL100 (its words R-SAL121).  Two
        made-up stubs on the fourth and seventh paydays and no checkpoint: the
        first three paydays take the tax formulas, the rest one stub or the
        other, so the row reads "the tax formulas, then your <4th> and <7th>
        pay stubs" -- the formulas first, the stubs in payday order.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        from app.enums import WithholdingKindEnum  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import add_test_pay_stub  # pylint: disable=import-outside-toplevel

        with app.app_context():
            profile = _seed_taxes_profile(seed_user, db)
            for period in (seed_periods[6], seed_periods[3]):
                add_test_pay_stub(
                    profile, period.start_date, "5000.00",
                    taxes={
                        WithholdingKindEnum.FEDERAL_INCOME: "600.00",
                        WithholdingKindEnum.STATE_INCOME: "180.00",
                        WithholdingKindEnum.SOCIAL_SECURITY: "290.00",
                        WithholdingKindEnum.MEDICARE: "70.00",
                    },
                )
            db.session.commit()
            first, second = (
                seed_periods[i].start_date.strftime("%b %-d, %Y") for i in (3, 6)
            )

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.data.decode()).split())

            assert resp.status_code == 200
            assert (
                f"Withholding basis the tax formulas, then your {first} and "
                f"{second} pay stubs Measured through"
            ) in text

    def test_taxes_tab_names_each_job_when_two_are_active(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """With two active jobs each line of the row starts with its job (R-BAL207).

        Plan step salary:S11-c-2c (the leaf review's MED-1): each job's
        paychecks are priced from its own stubs, so the row is one line per
        job, named only when more than one is active.  A made-up stub on the
        fourth payday of the first job; the second job has none.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        from app.enums import WithholdingKindEnum  # pylint: disable=import-outside-toplevel
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import add_test_pay_stub  # pylint: disable=import-outside-toplevel

        with app.app_context():
            first = _seed_taxes_profile(seed_user, db)
            second = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id, scenario_id=seed_user["scenario"].id,
                name="Second Job", filing_status_id=first.filing_status_id,
                state_code="NC", is_active=True, sort_order=1,
            )
            db.session.add(second)
            start_test_pay_list(second, Decimal("1000.00"))
            add_test_pay_stub(
                first, seed_periods[3].start_date, "5000.00",
                taxes={
                    WithholdingKindEnum.FEDERAL_INCOME: "600.00",
                    WithholdingKindEnum.STATE_INCOME: "180.00",
                    WithholdingKindEnum.SOCIAL_SECURITY: "290.00",
                    WithholdingKindEnum.MEDICARE: "70.00",
                },
            )
            db.session.commit()
            day = seed_periods[3].start_date.strftime("%b %-d, %Y")

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.data.decode()).split())

            assert resp.status_code == 200
            assert (
                f"Withholding basis Taxes Tab Profile: the tax formulas, then "
                f"your {day} pay stub Second Job: the tax formulas Measured through"
            ) in text

    def test_taxes_tab_says_nothing_is_left_to_estimate(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """A checkpoint covering every payday leaves no basis to name (R-SAL121).

        Plan step salary:S11-c-2c.  The checkpoint is dated 2026-12-31, after
        every seeded payday, so no paycheck is estimated and the row reads
        "nothing left to estimate".  Its figures are made up and none is read.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        from app.services import tax_withholding_service  # pylint: disable=import-outside-toplevel
        from app.services.tax_withholding_service import CheckpointFigures  # pylint: disable=import-outside-toplevel

        with app.app_context():
            profile = _seed_taxes_profile(seed_user, db)
            tax_withholding_service.save_checkpoint(
                profile.id,
                CheckpointFigures(
                    as_of_date=date(2026, 12, 31),
                    ytd_gross=Decimal("52341.18"),
                    ytd_federal=Decimal("4127.66"),
                    ytd_state=Decimal("1903.29"),
                    ytd_social_security=Decimal("3245.07"),
                    ytd_medicare=Decimal("761.14"),
                ),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.data.decode()).split())

            assert resp.status_code == 200
            assert "Withholding basis nothing left to estimate Measured through" in text

    def test_taxes_tab_checkpoint_reanchors_withholding(self, app, auth_client, seed_user, seed_periods, db):
        """A saved checkpoint re-anchors the measured side of the refund.

        Stub dated 2026-01-16 (the second payday) covers paydays 1-2;
        the remainder is the 8 later paydays.  Entered federal 1,600.00
        (vs 2 x 766.69 = 1,533.38 modeled), so:

          federal withheld = 1,600.00 + 8 x 766.69 = 7,733.52
          federal refund   = 7,733.52 - 3,820.00   = 3,913.52
        """
        with app.app_context():
            from datetime import date as date_cls
            from app.services import tax_withholding_service
            from app.services.tax_withholding_service import CheckpointFigures

            profile = _seed_taxes_profile(seed_user, db)
            tax_withholding_service.save_checkpoint(
                profile.id,
                CheckpointFigures(
                    as_of_date=date_cls(2026, 1, 16),
                    ytd_gross=Decimal("10000.00"),
                    ytd_federal=Decimal("1600.00"),
                    ytd_state=Decimal("360.00"),
                    ytd_social_security=Decimal("620.00"),
                    ytd_medicare=Decimal("145.00"),
                ),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$3,913.52" in html
            assert "measured through Jan 16" in html
            assert "Jan 16, 2026" in html   # assumptions "Measured through"

    def test_taxes_tab_actc_and_nc_child_deduction(self, app, auth_client, seed_user, seed_periods, db):
        """An MFJ 4-child household renders the ACTC row and NC child deduction.

        Hand-computed (130k MFJ, 4 qualifying children, seed_periods = 10
        paydays, no pre-tax, no checkpoint, 2026 seeds w/ OBBBA CTC 2,200 and
        ACTC cap 1,700):

          liability on hybrid gross 50,000:
            fed taxable 50,000 - 32,200 = 17,800 -> 10% band -> 1,780.00
            credits 4 x 2,200 = 8,800 -> liability clamps at 0
            unused 8,800 - 1,780 = 7,020
            ACTC = min(7,020, 4 x 1,700 = 6,800, 15% x 47,500 = 7,125)
                 = 6,800.00 (the CAP leg binds)
          fed withheld: annualized taxable 97,800 -> 11,240 - 8,800 = 2,440
            -> 93.85/period x 10 = 938.50
          fed refund = 938.50 - 0 + 6,800.00 = 7,738.50
          NC: base 50,000 -> tier 40-60k -> 2,500 x 4 = 10,000 child ded;
            taxable 50,000 - 25,500 - 10,000 = 14,500 x 0.0399 = 578.55
          NC withheld: (130,000 - 25,500) x 0.0399 = 4,169.55 -> 160.37 x 10
            = 1,603.70 -> NC refund 1,603.70 - 578.55 = 1,025.15
        """
        with app.app_context():
            from app.extensions import db as _db
            from app.models.ref import FilingStatus
            from app.models.salary_profile import SalaryProfile

            filing_status = (
                _db.session.query(FilingStatus)
                .filter_by(name="married_jointly").one()
            )
            profile = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                name="MFJ Four Kids",
                filing_status_id=filing_status.id,
                state_code="NC",
                is_active=True,
                qualifying_children=4,
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("5000.00"))  # $130,000.00 a year / 26
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Plus refundable child tax credit" in html
            assert "$6,800.00" in html          # the ACTC (cap leg binds)
            assert "$7,738.50" in html          # federal refund
            assert "$1,025.15" in html          # NC refund
            assert "$10,000 child deduction" in html
            assert "ACTC refundable cap" in html
            assert "$1,700 / child" in html
            assert "Refundable child tax credit (ACTC) modeled" in html
            assert "nonrefundable" not in html  # the stale caveat is gone

    def test_taxes_tab_empty_state_without_profile(self, app, auth_client, seed_user):
        """No active salary profile renders the empty state, not a crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/taxes",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No active salary profile" in html
            assert "Set up salary" in html

    def test_taxes_tab_non_htmx_renders_shell(self, app, auth_client, seed_user):
        """A non-HTMX GET renders the shell with Taxes active (D13)."""
        with app.app_context():
            resp = auth_client.get("/analytics/taxes")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/taxes"
