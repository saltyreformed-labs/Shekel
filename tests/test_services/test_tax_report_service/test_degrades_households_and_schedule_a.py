"""The report with no profile or no periods, two profiles summed under the primary's filing inputs,
and the Schedule A mortgage interest term that reuses the year-end hybrid.
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.services import balance_at
from app.services.tax_report_service import compute_tax_report
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    SPLIT_LOAN,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
)

from ._helpers import ZERO, _make_profile, _committed_profile, _make_full_year_periods


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
