"""Savings dashboard service tests: the debt summary, its principal-paid fraction, and its
membership rules.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.enums import AcctTypeEnum
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import balance_at, savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed

from tests._test_helpers import start_test_pay_list
from app.models.amount_ownership import AmountOwnership

from ._helpers import _create_small_loan


# ── Debt Summary Tests (Commit 5.12-1) ────────────────────────────────


class TestDebtSummary:
    """Tests for the debt summary computation in the dashboard service.

    Commit 5.12-1: aggregate debt metrics (total debt, monthly payments,
    weighted average rate, debt-free date) and debt-to-income ratio.
    """

    def test_debt_summary_none_when_no_loans(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-3: No loan accounts yields debt_summary=None."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert result["debt_summary"] is None

    def test_narrow_producer_matches_full_dashboard(
        self, app, db, seed_user, seed_periods,
    ):
        """#82: compute_debt_summary equals the full build's debt_summary.

        The equivalence contract behind the narrow producer: with a loan
        account, a salary profile, AND the seed user's non-loan accounts
        present, the loan-only projection run must produce exactly the
        :class:`~..._debt_summary.DebtSummary` the full ``compute_dashboard_data``
        build emits -- every money figure, the payoff outlook, the revolving
        caveat and the DTI block, since both route through the shared
        ``_debt_summary_with_dti``.  The salary makes the DTI leg
        non-vacuous: $78,000 / 26 = $3,000 gross biweekly -> $6,500
        gross monthly, so the block is a live value on both sides rather than
        None == None.  WHOLE-VALUE equality (not per-field spot checks) so a
        future field populated on one path but not the other fails here -- a
        frozen dataclass compares on every field, which is the property that
        made this assertion survive the dict's retirement at plan step X-s3.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Equivalence Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("3000.00"))  # $78,000.00 a year / 26
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            full = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["debt_summary"]
            narrow = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            assert full is not None
            # The DTI leg is live, not the vacuous None == None.  $78,000 / 26
            # = $3,000 biweekly -> $3,000 * 26 / 12 = $6,500 monthly, so the
            # ratio is the PITI total over $6,500 -- which pins the engine
            # denominator without the summary storing it (plan step X-s3).
            assert full.dti is not None
            assert full.dti.ratio == (
                full.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert narrow == full

    def test_narrow_producer_none_when_no_loans(
        self, app, db, seed_user, seed_periods,
    ):
        """#82: the narrow producer's no-loan early return yields None.

        Mirrors ``test_debt_summary_none_when_no_loans`` for the narrow
        path: with no LoanParams rows the producer returns ``None``
        before any per-account projection or breakdown computation runs
        (the same ``None`` the full build surfaces as ``debt_summary``).
        """
        with app.app_context():
            assert savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            ) is None

    def test_debt_summary_single_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-1: Single loan produces a valid debt summary.

        A $1,000 auto loan at 5% for 24 months.  The summary should
        reflect this single loan's metrics.
        """
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds.total_debt == Decimal("1000.00")
            # weighted_avg_rate = single loan's rate = 0.05000
            assert ds.weighted_avg_rate == Decimal("0.05000")
            assert ds.total_monthly_payments > Decimal("0.00")
            assert ds.payoff_outlook.all_clear_on is not None

    def test_debt_summary_multiple_loans_weighted_rate(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-2 / C-5.12-4: Two loans with hand-calculated weighted avg rate.

        Loan A: $200,000 at 6.5%
        Loan B: $25,000 at 4.9%
        weighted_avg = (200000*0.065 + 25000*0.049) / (200000+25000)
                     = (13000 + 1225) / 225000
                     = 14225 / 225000
                     = 0.06322...
        """
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"),
                rate=Decimal("0.06500"),  # DH-#56 origination rate
                term=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            create_loan_account(
                seed_user, db.session, name="Auto",
                principal=Decimal("25000.00"),
                rate=Decimal("0.04900"),  # DH-#56 origination rate
                term=60,
                origination_date=date(2024, 6, 1),
                payment_day=15,
                account_type=AcctTypeEnum.AUTO_LOAN,
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds.total_debt == Decimal("225000.00")
            # Hand-calc: (200000*0.065 + 25000*0.049) / 225000
            #          = 14225 / 225000 = 0.063222...
            assert ds.weighted_avg_rate == Decimal("0.06322")

    def test_debt_summary_excludes_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-8: Paid-off loan excluded from debt summary.

        One active loan ($1,000) plus one paid-off loan.  Total debt
        should equal only the active loan's principal.
        """
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import (
            TransferSpec, create_transfer, settle_transfer,
        )

        with app.app_context():
            active = _create_small_loan(
                seed_user, db.session, name="Active Loan",
                principal=Decimal("2000.00"),
            )
            paid_off = _create_small_loan(
                seed_user, db.session, name="Paid Off Loan",
            )
            # Pay off the second loan with a confirmed transfer.
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=paid_off.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()

            # Payoff is recorded as a balance true-up to $0 (the cash
            # lump sum no longer auto-pays-off under the contractual
            # schedule model).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(paid_off.loan_params, Decimal("0.00"))
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            # Only the active $2,000 loan contributes.
            assert ds.total_debt == Decimal("2000.00")

    def test_debt_summary_all_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-10: All loans paid off yields zero totals.

        Debt summary exists (not None) but all aggregates are zero
        and debt-free date is None.
        """
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import (
            TransferSpec, create_transfer, settle_transfer,
        )

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()

            # Payoff is recorded as a balance true-up to $0 (cash lump
            # sums no longer auto-pay-off under the contractual schedule).
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds.total_debt == Decimal("0.00")
            assert ds.total_monthly_payments == Decimal("0.00")
            assert ds.weighted_avg_rate == Decimal("0.00000")
            assert ds.payoff_outlook.all_clear_on is None

    def test_debt_summary_missing_params(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-11: Loan with no LoanParams is skipped, no crash.

        A loan account without params exists but another loan with
        params also exists.  The summary should only include the
        parameterized loan.
        """
        with app.app_context():
            # Loan with params
            _create_small_loan(seed_user, db.session, name="With Params")

            # Loan without params
            loan_type = (
                db.session.query(AccountType)
                .filter_by(name="Auto Loan").one()
            )
            no_params = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=loan_type.id,
                    name="No Params",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(no_params)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds is not None
            # Only the parameterized loan contributes.
            assert ds.total_debt == Decimal("1000.00")

    def test_debt_free_date_is_latest(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-12: Debt-free date is the latest payoff across loans.

        A short-term loan (24 months) and a long-term mortgage (360
        months).  The debt-free date should match the mortgage's payoff.
        """
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Short-term loan
            _create_small_loan(
                seed_user, db.session, name="Short Loan", term=24,
            )

            # Long-term mortgage, originated 2024 and owing its full $200,000
            # TODAY (stated at setup, the way the setup door records it --
            # plan step R20; the mid-life-import shape).  Without the
            # assertion the two unrecorded years since origination are charged
            # (plan step R16-b-2, ruling R-R71) and the loan never clears.
            create_loan_account(
                seed_user, db.session, name="Long Mortgage",
                principal=Decimal("200000.00"),
                rate=Decimal("0.06500"),  # DH-#56 origination rate
                term=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_balance=Decimal("0"),
                tracked_balance=Decimal("200000.00"), tracked_from=date.today(),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            # The mortgage payoff is decades away; auto loan is < 2 years.
            # Debt-free date should be the mortgage's later payoff.
            assert ds.payoff_outlook.all_clear_on is not None
            # The auto loan payoff is within ~21 months of origination
            # (Jan 2026 + 21 months ~ Oct 2027).  The mortgage owes its whole
            # principal from today's assertion, so its payoff is 360 months
            # out, ~2056.  Debt-free = mortgage.
            assert ds.payoff_outlook.all_clear_on.year > 2030

    def test_debt_summary_includes_escrow(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-9: Escrow components are included in monthly total.

        A mortgage with $7,200/year escrow ($600/month).  The monthly
        total must include P&I + escrow.
        """
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            add_escrow_line,
            create_loan_account,
            loan_params_for,
        )

        with app.app_context():
            mortgage = create_loan_account(
                seed_user, db.session, name="Escrow Mortgage",
                principal=Decimal("200000.00"),
                rate=Decimal("0.06500"),  # DH-#56 origination rate
                term=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_balance=Decimal("0"),
            )
            params = loan_params_for(db.session, mortgage.id)

            add_escrow_line(
                db.session, mortgage.id, "Property Tax", Decimal("7200.00"),
                effective_date=params.origination_date,
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            # P&I for a $200K, 6.5%, 360-month loan is ~$1,264.
            # With $600/month escrow, total should exceed $1,800.
            assert ds.total_monthly_payments > Decimal("1800.00")
            # Verify escrow is included: total > P&I alone.
            from app.services import amortization_engine as ae
            pi_only = ae.calculate_monthly_payment(
                Decimal("200000.00"), Decimal("0.06500"),
                ae.calculate_remaining_months(date(2024, 1, 1), 360),
            )
            assert ds.total_monthly_payments > pi_only


class TestPrincipalPaidFraction:
    """Tests for ``DebtSummary.principal_paid_fraction`` (Loop B B-1, X-u).

    The debt track marker's figure: the aggregate fraction of original loan
    principal paid down so far.  Per
    the 2026-06-12 ruling (``dashboard_card_audit.md`` Rebuild decisions
    item 4) it sums over ALL loans ever originated -- paid-off loans stay
    in both the numerator and the denominator -- so the fraction is
    monotonic, reaches exactly ``1`` at full payoff, and stays there.

    It was its own narrow producer (``compute_debt_principal_progress``) until
    plan step X-u, which folded it into the summary that the same consumer
    already reads: one dashboard render was running the whole load -> params ->
    project pipeline TWICE, once per debt producer (finding N-109).  These
    tests read the summary field, which is what the dashboard reads.

    ``original_principal`` is a NOT NULL, ``> 0`` column, so any ORIGINATED
    loan supplies the denominator.  "No loans at all" is not a case here --
    there is no summary to carry the field then, which
    ``TestDebtSummary::test_narrow_producer_none_when_no_loans`` already pins.
    """

    def test_fraction_none_when_every_loan_is_unborrowed(
        self, app, db, seed_user, seed_periods,
    ):
        """A configured-but-unclosed loan -> a summary whose fraction is None.

        The REACHABLE state in which the summary exists and the fraction does
        not, which the dashboard rail renders bare.  A mortgage originating
        2026-04-15 under this module's frozen 2026-03-20 today has not been
        borrowed: it owes ``$0.00``, its whole debt line is ahead of it, and
        ``_compute_principal_paid_fraction`` puts it in NEITHER sum -- so the
        denominator is zero and the honest answer is "no progress to report",
        not ``0%`` (which would say the borrower has repaid none of a debt they
        do not yet have) and not ``100%``.

        ``dashboard/_tracks.html`` claimed until plan step X-u that no-loans was
        the only reachable case for a bare rail; this is the case that claim
        missed, and the summary is non-``None`` here precisely because the loan
        DOES have a payoff date to caption.
        """
        from app.enums import AcctTypeEnum  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Closing In April",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )

            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            # The fixture really is in that state: a loan with a debt line
            # ahead of it that owes nothing today.
            assert summary is not None
            assert summary.total_debt == Decimal("0.00")
            assert summary.payoff_outlook.all_clear_on is not None
            assert summary.principal_paid_fraction is None

    def test_fraction_present_for_a_loan(self, app, db, seed_user, seed_periods):
        """A loan -> a Decimal fraction that reconciles with the debt summary.

        A $1,000.00 auto loan.  The fraction sums over the SAME loan set
        the debt summary's ``total_debt`` uses, so:
            fraction = (original - current) / original
                     = (1000.00 - total_debt) / 1000.00.
        ``original_principal`` is NOT NULL, so the data IS present and the
        fraction is a real Decimal in [0, 1], never None.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session)

            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            fraction = summary.principal_paid_fraction
            assert isinstance(fraction, Decimal)
            assert Decimal("0") <= fraction <= Decimal("1")
            # Reconcile against the debt summary's current balance.
            expected = (
                (Decimal("1000.00") - summary.total_debt) / Decimal("1000.00")
            )
            assert fraction == expected

    def test_fraction_zero_at_origination(self, app, db, seed_user, seed_periods):
        """A loan originated today (no payments yet) -> fraction exactly 0.

        ``_create_small_loan`` originates 2026-01-01 at term 24; under the
        frozen 2026-03-20 today some scheduled payments are confirmed, so
        to isolate the zero case we true-up the balance back UP to the
        original principal, leaving current == original:
            (1000.00 - 1000.00) / 1000.00 = 0.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import insert_trueup_event

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            # Assert the current balance equals the original principal.
            insert_trueup_event(acct.loan_params, Decimal("1000.00"))
            db.session.commit()

            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            assert summary.principal_paid_fraction == Decimal("0")

    def test_fraction_one_when_all_loans_paid_off(
        self, app, db, seed_user, seed_periods,
    ):
        """All loans paid off -> the fraction is exactly 1 (full payoff).

        A loan trued-up to $0 is paid off.  Under the all-loans-ever basis
        it stays in BOTH sums, contributing $0 to the current-balance sum
        and its full $1,000.00 original principal to the denominator, so:
            (1000.00 - 0.00) / 1000.00 = 1.
        The fraction reaches 1 at full payoff and is NOT None -- None is
        reserved for the no-loan-has-originated case.  The SAME summary
        still reports total_debt $0.00 (active-loans-only), so the two
        figures on ONE value object deliberately disagree on which loans count:
        that is the property plan step X-u had to preserve when it merged the
        two producers, and it is asserted on one object here rather than across
        two calls.
        """
        # pylint: disable=import-outside-toplevel
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import (
            TransferSpec, create_transfer, settle_transfer,
        )
        from tests._test_helpers import insert_trueup_event

        with app.app_context():
            acct = _create_small_loan(seed_user, db.session)
            # A confirmed payment so the loan reads as "ever paid off".
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            # The debt summary reports the active-loans-only total ($0.00) ...
            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            assert summary is not None
            assert summary.total_debt == Decimal("0.00")
            # ... but the principal-paid fraction on the SAME summary is
            # exactly 1: the paid-off loan keeps its full original principal in
            # both sums.  (1000.00 - 0.00) / 1000.00 = 1.
            assert summary.principal_paid_fraction == Decimal("1")

    def test_fraction_monotonic_one_paid_one_partial(
        self, app, db, seed_user, seed_periods,
    ):
        """One paid-off + one partial loan -> all-loans-ever fraction.

        Loan A: $1,000.00 original, paid off (trued-up to $0) -> stays in
        both sums, contributing $0 to the current-balance sum.
        Loan B: $1,000.00 original, trued-up to a known $400.00 balance ->
        contributes $400.00 to the current-balance sum.

        Under the all-loans-ever basis BOTH loans count, so the fraction
        does NOT jump (it would under the old active-only basis, which
        would have dropped Loan A entirely):
            (orig_A + orig_B - balance_B) / (orig_A + orig_B)
          = (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00)
          = 1600.00 / 2000.00
          = 0.8.
        """
        # pylint: disable=import-outside-toplevel
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import (
            TransferSpec, create_transfer, settle_transfer,
        )
        from tests._test_helpers import insert_trueup_event

        with app.app_context():
            paid_off = _create_small_loan(
                seed_user, db.session, name="Paid Off Loan",
            )
            partial = _create_small_loan(
                seed_user, db.session, name="Partial Loan",
            )
            # Confirmed payment so the first loan reads as "ever paid off".
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=paid_off.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()
            # Loan A: trued-up to $0 (paid off).
            insert_trueup_event(paid_off.loan_params, Decimal("0.00"))
            # Loan B: trued-up to a known $400.00 partial balance.
            insert_trueup_event(partial.loan_params, Decimal("400.00"))
            db.session.commit()

            fraction = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            ).principal_paid_fraction
            # (orig_A + orig_B - balance_B) / (orig_A + orig_B)
            # = (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00)
            # = 1600.00 / 2000.00 = 0.8.
            expected = (
                (Decimal("1000.00") + Decimal("1000.00") - Decimal("400.00"))
                / (Decimal("1000.00") + Decimal("1000.00"))
            )
            assert fraction == expected
            assert fraction == Decimal("0.8")


class TestDebtSummaryMembershipRules:
    """ONE summary, THREE loan sets -- the control plan step X-u needed.

    Ruling R-BI declined to merge the principal-paid producer into the debt
    summary inside plan step X-s3 on one ground: the two answer over DIFFERENT
    membership rules, and merging them re-opens the question ruling X-q settled
    at a measured cost of 19 years (finding N-98, where a debt-free caption
    derived over the owed-today set reported the date the OTHER loans finish).
    X-u merged them anyway, having first measured that both rules are REDUCERS
    over one projection rather than two loan sets -- and this is the fixture
    that would catch a merge that re-decided either one.

    Three loans, chosen so the three sets are pairwise DIFFERENT:

    * **A, owing** -- $1,000.00 originated 2026-01-01, trued up to a known
      $400.00.  In owed-today AND in all-loans-ever AND on the debt line.
    * **B, retired** -- $1,000.00 originated 2026-01-01, trued up to $0.00.
      OUT of owed-today (it owes nothing) and OFF the debt line (retired), but
      IN all-loans-ever, contributing its full principal as repaid.
    * **C, unborrowed** -- a $200,000.00 mortgage originating 2026-04-15,
      after this module's frozen 2026-03-20 today.  OUT of owed-today and OUT
      of all-loans-ever (nothing of it has been repaid because none of it has
      been borrowed), but ON the debt line, whose whole 30 years are ahead.

    So owed-today is ``{A}``, all-loans-ever is ``{A, B}``, and the debt line
    is ``{A, C}``.  No two rules can be swapped for each other without a
    figure below moving.
    """

    AUTO = Decimal("1000.00")
    MORTGAGE = Decimal("200000.00")
    OWING_BALANCE = Decimal("400.00")

    def _three_loans(self, seed_user, db_session, periods):
        """Build the owing / retired / unborrowed triple.  Returns all three."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account, insert_trueup_event

        owing = create_loan_account(
            seed_user, db_session, name="Owing Auto",
            principal=self.AUTO, rate=Decimal("0.05000"),
            term=24, origination_date=date(2026, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        retired = create_loan_account(
            seed_user, db_session, name="Retired Auto",
            principal=self.AUTO, rate=Decimal("0.05000"),
            term=24, origination_date=date(2026, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        unborrowed = create_loan_account(
            seed_user, db_session, name="Closing In April",
            principal=self.MORTGAGE, rate=Decimal("0.05000"),
            term=360, origination_date=date(2026, 4, 15), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        insert_trueup_event(owing.loan_params, self.OWING_BALANCE)
        insert_trueup_event(retired.loan_params, Decimal("0.00"))
        db_session.commit()
        return owing, retired, unborrowed

    def test_one_summary_answers_three_membership_rules(
        self, app, db, seed_user, seed_periods,
    ):
        """Each figure counts its own loans, and no two count the same ones.

        The arithmetic, all three rules on one :class:`DebtSummary`:

        * ``total_debt`` is owed-today ``{A}``: $400.00.  B owes nothing and
          C has not been borrowed, so both are skipped by
          ``_loan_ad_current_principal``.
        * ``total_monthly_payments`` is the SAME owed-today set, which is what
          makes it the discriminating figure here: A's payment on $1,000.00 is
          under $50/mo, while C's alone on $200,000.00 at 5% for 360 months is
          about $1,073/mo.  A rule that counted C would be off by an order of
          magnitude.
        * ``principal_paid_fraction`` is all-loans-ever ``{A, B}``:
          (1000.00 + 1000.00 - 400.00) / (1000.00 + 1000.00) = 0.8.  Counting C
          would read (202000.00 - 400.00) / 202000.00 = 0.998; counting only
          the owed-today set would read (1000.00 - 400.00) / 1000.00 = 0.6.
        * ``payoff_outlook`` is the debt line ``{A, C}``: the LATEST payoff, so
          past 2028 -- A's 24-month term from 2026-01-01 ends in 2027 even
          before its true-up shortens it, so only C can put the date there.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._debt_line import (
            debt_line_loans,
        )

        with app.app_context():
            owing, retired, unborrowed = self._three_loans(
                seed_user, db.session, seed_periods,
            )
            bctx = BalanceContext.build(seed_user["user"].id)

            # Premises about the FIXTURE, read from the seam directly and for
            # ALL THREE loans: a fixture that silently built three identical
            # loans would pass every assertion below for the wrong reason (the
            # X-t5 lesson -- a new fixture is a new control and it can be born
            # dead; this block claimed three loans and read one until X-u's own
            # adversarial review counted them).  The seam's balance is HELD
            # (negative when owed) since plan step credit_card:CC-5-5c, so each
            # premise reads what the loan OWES through ``owed``.
            owing_figures = balance_at.loan_figures(owing, bctx)
            assert owing_figures.terms.is_originated is True
            assert owing_figures.is_retired is False
            assert owed(balance_at.balance_at(
                owing, bctx, bctx.as_of,
            )) == self.OWING_BALANCE

            # B: originated and retired -- owes nothing, off the debt line.
            retired_figures = balance_at.loan_figures(retired, bctx)
            assert retired_figures.terms.is_originated is True
            assert retired_figures.is_retired is True
            assert owed(balance_at.balance_at(
                retired, bctx, bctx.as_of,
            )) == Decimal("0.00")

            # C: not borrowed -- owes nothing for the OTHER reason, and its
            # whole debt line is ahead of it.
            unborrowed_figures = balance_at.loan_figures(unborrowed, bctx)
            assert unborrowed_figures.terms.is_originated is False
            assert unborrowed_figures.is_retired is False
            assert owed(balance_at.balance_at(
                unborrowed, bctx, bctx.as_of,
            )) == Decimal("0.00")
            # The two zero balances are the same number for different reasons,
            # which is the whole hazard the three rules exist to separate.

            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            assert summary is not None

            # Owed-today {A}.
            assert summary.total_debt == self.OWING_BALANCE
            # Owed-today again, and the figure that separates it from the debt
            # line: only A's payment is in here.
            assert summary.total_monthly_payments == (
                owing_figures.terms.monthly_payment
            )
            assert summary.total_monthly_payments < Decimal("50.00")

            # All-loans-ever {A, B}: B's full principal counts as repaid, C is
            # in neither sum.
            expected_fraction = (
                (self.AUTO + self.AUTO - self.OWING_BALANCE)
                / (self.AUTO + self.AUTO)
            )
            assert summary.principal_paid_fraction == expected_fraction
            assert summary.principal_paid_fraction == Decimal("0.8")

            # The debt line {A, C}: two loans, and the date is C's.
            assert summary.payoff_outlook.never_clears is False
            assert summary.payoff_outlook.all_clear_on > date(2028, 1, 1)

            # And the set itself, so the assertion above cannot pass because
            # some OTHER loan happens to date that late.
            full = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["account_data"]
            assert {
                ad.account.name for ad in debt_line_loans(full)
            } == {"Owing Auto", "Closing In April"}
