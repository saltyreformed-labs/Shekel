"""Loan route tests: the amortization schedule and the schedule route.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum, InterestAccrualConventionEnum, StatusEnum
from app.models.loan_features import RateHistory
from app.models.ref import AccountType
from app.services import account_service

from tests._test_helpers import make_loan_payment_template
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import (
    _create_loan_account,
    _create_mortgage,
    _create_other_loan,
    _create_transfer_to_loan,
    _create_fresh_mortgage,
    _create_loan_account_exact,
)


class TestAmortizationSchedule:
    """Tests for the full amortization schedule at the standalone /loan/schedule route.

    Verifies that the schedule table renders correctly with the right
    number of rows, confirmed/projected distinction, currency formatting,
    totals row, and conditional Rate column for ARM loans.  (Loop B demoted
    the schedule off the detail page into its own route; the table content is
    unchanged.)
    """

    def test_schedule_route_renders(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-1: the standalone schedule route renders the full schedule.

        GET /loan/schedule for a mortgage with params.  Assert the page heading
        and the month-by-month table are both present.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Amortization Schedule" in html
        assert "Month-by-Month Schedule" in html

    def test_schedule_has_correct_row_count(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-2: 30-year mortgage produces the correct number of data rows.

        Re-pinned for Commit 5 of the amortization engine split
        (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``).
        Pre-Commit-5 the dashboard called ``generate_schedule``
        directly, which iterated up to ``max_months = remaining_months
        + term_months`` and emitted a 361st row absorbing the
        sub-penny rounding residue.  Post-Commit-5 the schedule routed
        through ``compute_payoff_scenarios`` -> ``project_forward``, which
        terminates cleanly at ``month_num == remaining_months``, absorbing
        the residue in the final scheduled month; since plan step R7d-g-3
        (ruling **R-R88**) it lists the balance seam's forward plan
        (``balance_at.loan_installments``) through the payoff, and a plan
        with no recurring payment folds the contract's own installments.
        The architecturally correct row count for a 30-year mortgage with
        nothing due yet is therefore ``term_months == 360`` -- one row per
        scheduled month, no residue artifact.  Hand-derivation:
        ``len(history_rows) == 0`` (no confirmed payments) + 360
        contractual installments, every one in the plan's future.

        The loan originates on 2026-03-01 so its first installment (Apr 1)
        is after the frozen 2026-03-20 today.  Originated a month earlier,
        the Mar 1 installment would be due and unpaid, and an overdue
        installment is NOT in the plan (ruling D1 / finding B-9): the fold
        then clears the loan in the post-contractual extension and the
        table honestly runs past 360 --
        ``test_schedule_lists_the_plan_when_an_installment_is_overdue``.
        """
        expected_count = 360

        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Count data rows: each has exactly one Projected or Confirmed badge.
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows == expected_count, (
            f"Expected {expected_count} data rows, got {total_rows} "
            f"({projected} projected, {confirmed} confirmed)"
        )

    def test_schedule_confirmed_rows_marked(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-3: Confirmed payment rows are visually distinguished.

        Creates a mortgage and two confirmed transfers in months
        that have already passed relative to the autouse-frozen
        today (2026-03-20).  Asserts confirmed rows get a distinct
        badge and the rest are Projected.

        Re-pinned for Commit 5 of the amortization engine split
        (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``).
        Pre-Commit-5 the dashboard called ``generate_schedule``,
        which had no ``as_of`` concept and marked any payment record
        as Confirmed based solely on the ``is_confirmed`` field.
        Post-Commit-5 the dashboard routes through
        ``compute_payoff_scenarios``, whose replay only consumes
        confirmed payments with ``payment_date <= as_of``; confirmed
        payments dated AFTER today are data-hygiene cases and are
        the plan's to price (Projected badge) -- the new
        architecture's stricter semantic for the Confirmed badge.
        The previous fixture used April/May 2026 seed_periods (after
        the frozen today), which exercised the data-hygiene path,
        not the realistic "DONE payment in history" path.  This
        rewrite uses February/March seed_periods (before today) so
        the DONE payments fall in the replay window and produce
        Confirmed badges.
        """
        # Origination 2026-01-01 -> first scheduled payment month
        # is February 2026 (origination + 1 month).  seed_periods[3]
        # starts 2026-02-13 (Feb 2026) -> schedule month 1.
        # seed_periods[5] starts 2026-03-13 (Mar 2026) -> schedule
        # month 2.  Both before frozen today 2026-03-20, so the
        # composer's replay consumes them and produces history rows
        # with is_confirmed=True.
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[5], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        assert confirmed == 2, (
            f"Expected 2 confirmed rows, got {confirmed}"
        )
        assert projected > 0, "Expected some projected rows"
        # Total should still be a full schedule.
        assert confirmed + projected > 300

    def test_schedule_first_last_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-4: First and last rows have correct values for known loan params.

        Loan: $250,000 at 6.5% for 360 months.
        Hand calculation:
          M = P * r(1+r)^n / [(1+r)^n - 1]
          r = 0.065/12 = 0.00541666...
          (1+r)^360 ~ 6.9920
          M = 250000 * (0.00541666 * 6.9920) / (6.9920 - 1)
          M = 250000 * 0.037878 / 5.9920
          M ~ $1,580.17

        First month:
          Interest = 250000 * 0.065/12 = $1,354.17
          Principal = 1580.17 - 1354.17 = $226.00

        Last row: remaining_balance = $0.00

        Originated 2026-03-01 so the first installment (Apr 1 2026) is the
        plan's first row on the untouched $250,000 (see
        ``test_schedule_has_correct_row_count`` for why a loan a month
        older would list a different first row).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # First row: month 1, expected payment of $1,580.17.
        assert "$1,580.17" in html
        # First month interest: $250,000 * 0.065/12 = $1,354.17.
        assert "$1,354.17" in html
        # First month principal: $1,580.17 - $1,354.17 = $226.00.
        assert "$226.00" in html
        # Last row balance must be $0.00.
        # The totals row does not have a balance cell, so "$0.00" comes
        # from the last data row's remaining_balance.
        assert "$0.00" in html

    def test_schedule_numbering_continuous_from_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The # column counts installments from origination, not 1..N per slice.

        Regression for the user request: a mid-life loan's schedule must
        number rows by the installment's place in the contract -- a loan in
        its 26th month shows #25 for its Feb 1 2026 payment -- and the
        projected slice must keep counting up (#26, #27, ...) instead of
        restarting at 1 (the projected slice's pre-fix
        project_forward-local numbering).
        """
        # Origination 2024-01-01 -> the Feb 1 2026 payment is the 25th
        # (25 whole months after origination) at the frozen today
        # (2026-03-20).  The loan is asserted at $250,000 on 2026-01-01
        # (``_create_loan_account``: the balance when the owner began
        # tracking it, rulings R-R71 / R-R72), so the 24 installments before
        # that are the assertion's business and not the plan's.
        # seed_periods[2] (2026-01-30 .. 2026-02-12) contains 2/1, so its
        # confirmed payment IS the Feb 1 payment; seed_periods[3] (2026-02-13
        # .. 2026-02-26) contains no 1st, so its payment is due 3/1 -- the
        # 26th, paid ahead of its due date.  Both installments due since the
        # assertion are paid, so the plan's first row is the 27th and the
        # numbering runs continuously across the boundary.  (An installment
        # left unpaid past its due date is absent from the plan, and the
        # numbering then honestly skips it:
        # ``test_schedule_lists_the_plan_when_an_installment_is_overdue``.)
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Mid-life Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2024, 1, 1), 1,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1611.77"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1611.77"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first <td> of each schedule data row is the payment number;
        # year-header rows use <td colspan> and the totals row a label, so
        # this matches only data rows.
        row_numbers = [
            int(n) for n in re.findall(
                r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>', html
            )
        ]
        assert row_numbers, "No schedule data rows parsed from the table"
        # Starts at the true payment number (25), NOT 1.
        assert row_numbers[0] == 25, (
            f"Expected first schedule row #25 (payments from origination), "
            f"got {row_numbers[0]}"
        )
        # Continuous +1 across the confirmed/projected boundary -- the
        # projected slice does not restart at 1.
        for i in range(1, len(row_numbers)):
            assert row_numbers[i] == row_numbers[i - 1] + 1, (
                f"Numbering restarted or jumped at index {i}: "
                f"{row_numbers[i - 1]} -> {row_numbers[i]}"
            )

    def test_schedule_lists_the_plan_when_an_installment_is_overdue(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An overdue, unpaid installment is absent; the plan runs past the contract.

        The schedule lists what the loan is projected to PAY -- the balance
        seam's forward plan (plan step R7d-g-3, ruling **R-R88**) -- and an
        installment due before today that no payment answers is not in it
        (ruling D1 / finding B-9: a plan cannot have already happened).  A
        $250k / 6.5% / 360-month mortgage originated 2026-01-01 with its Feb
        1 2026 payment (#1) confirmed and its Mar 1 2026 payment (#2) unpaid
        at the frozen 2026-03-20 today: the plan's first row is #3 (Apr 1
        2026), the numbering runs continuously from there, and because the
        missed $1,580.17 is never paid it rides the balance to the contract's
        end -- ``1580.17 * 1.0054167 ** 358 ~= $10,930`` above the
        contractual track at #360 (Jan 2056) -- which the post-contractual
        extension clears at the level P&I (~$1,521 of principal a month
        against the residue's dwindling interest) in 8 more installments:
        the last row is #368 (Sep 2056) and reaches $0.00.  Until R7d-g-3
        the composer's committed slice listed a contractual row for the
        missed month as if it would be paid, and stopped at #360 with the
        residue absorbed into a phantom final payment.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        row_numbers = [
            int(n) for n in re.findall(
                r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>', html
            )
        ]
        assert html.count('badge bg-success">Confirmed</span>') == 1
        assert row_numbers[0] == 1
        assert 2 not in row_numbers, (
            "the overdue, unpaid Mar 1 installment is not in the plan"
        )
        planned = row_numbers[1:]
        assert planned[0] == 3
        assert planned == list(range(3, 3 + len(planned)))
        assert planned[-1] == 368, (
            "the missed installment's residue takes 8 extension installments "
            f"to clear: the last row is #368, got #{planned[-1]}"
        )
        # The last data row's balance cell -- the money cell right before the
        # status badge on a fixed-rate loan's row -- reaches $0.00.
        balances = re.findall(
            r'<td class="text-end font-mono">\$([0-9,]+\.\d\d)</td>\s*<td>\s*'
            r'<span class="badge',
            html,
        )
        assert len(balances) == len(row_numbers), "no balance cells parsed"
        assert balances[-1] == "0.00"

    def test_schedule_rows_are_months_summing_every_payment_in_them(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A planned row is a MONTH: every payment the plan makes in it, summed.

        The same loan as
        ``TestPaymentBreakdown.test_breakdown_sums_the_coming_months_payments_and_skips_catch_ups``
        -- $250k / 6.5% / 360 months originated 2026-03-01, a tracking
        payment with a $125 extra due the 1st and a $50 sweep due the 15th
        whose Mar 15 occurrence is a catch-up paid the day after the frozen
        2026-03-20 today -- so the table is month-by-month, not
        payment-by-payment: the catch-up is a row of its own dated the day
        it is paid (Mar 21, $50.00, all principal, balance $249,950.00),
        and April is ONE row (Apr 1: $1,755.17 paid, $1,353.90 interest,
        $401.27 principal, balance $249,548.73) with no separate Apr 15 row.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        tracking = make_loan_payment_template(
            db.session, seed_user, acct, extra_principal="125.00",
        )
        tracking.name = "Mortgage payment"
        db.session.flush()
        make_loan_payment_template(
            db.session, seed_user, acct, amount="50.00",
            derive_from_loan=False, cadence=MONTHLY, fires_on_day=15,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        row_dates = re.findall(
            r'<tr class="(?:table-success)?">\s*<td>\d+</td>\s*<td>([^<]+)</td>',
            html,
        )
        assert row_dates[:2] == ["Mar 21, 2026", "Apr 1, 2026"], row_dates[:3]
        assert "Apr 15, 2026" not in row_dates
        assert "$249,950.00" in html
        assert "$1,755.17" in html
        assert "$1,353.90" in html
        assert "$401.27" in html
        assert "$249,548.73" in html

    def test_schedule_early_payoff_fewer_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-5: Loan with short term pays off early (fewer than 360 rows).

        Creates a loan with a 12-month term.  The schedule from
        origination runs 12 months, well under 360.  Verifies
        the schedule table reflects the actual term, not a fixed
        30-year assumption.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN, "Short Loan",
            Decimal("5000.00"),
            Decimal("0.06500"), 12, date(2026, 3, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows < 360, (
            f"Expected fewer than 360 rows for short-term loan, got {total_rows}"
        )
        # 12 months + possibly 1 extra for sub-penny rounding residue.
        assert total_rows <= 13, (
            f"Expected ~12 rows for 12-month loan, got {total_rows}"
        )
        # Last row should still reach $0.00.
        assert "$0.00" in html

    def test_schedule_hidden_no_params(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-6: Loan without LoanParams renders setup page, not schedule tab.

        When no LoanParams exist, the route renders setup.html, which
        does not include the dashboard tabs at all.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Amortization Schedule" not in resp.data
        assert b"tab-schedule" not in resp.data

    def test_schedule_hidden_empty_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-7: Paid-off loan shows short schedule ending at $0.

        A loan fully retired via a confirmed payment shows a very
        short schedule (1 row) with the final balance at $0.00 and
        the row marked as confirmed.
        """
        # Small loan: $1000 at 5% for 12 months, origination Jan 2026.
        # First payment month: Feb 2026 (seed_periods[3] = Feb 13).
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN, "Paid Off",
            Decimal("1000.00"),
            Decimal("0.05000"), 12, date(2026, 1, 1), 1,
        )
        # Large confirmed payment in Feb covers the full balance.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1100.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Schedule table exists with the payoff row.
        assert "Month-by-Month Schedule" in html
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed == 1, (
            f"Expected 1 confirmed row for paid-off loan, got {confirmed}"
        )
        assert "$0.00" in html

    def test_schedule_arm_rate_column_shown(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-8: ARM mortgage shows Rate column in the schedule table.

        Creates an ARM mortgage with a rate history entry. The Rate
        column header and rate values should appear in the schedule.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Schedule",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header.
        assert ">Rate</th>" in html or ">Rate<" in html
        # At least one rate percentage value.
        assert "7.000%" in html or "5.000%" in html

    def test_schedule_fixed_rate_no_rate_column(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-9: Non-ARM mortgage does NOT show Rate column.

        Fixed-rate loans have the same rate for every row. Showing it
        360 times is noise, so the column is omitted.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header must not exist in the schedule table.
        # (The overview tab shows the rate, but not as a <th>.)
        assert ">Rate</th>" not in html

    def test_schedule_totals_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-10: Schedule table includes a totals footer row.

        The <tfoot> row shows summed payment, principal, interest, and
        extra columns.  Verify the footer exists and contains currency
        values.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Footer row present.
        assert "<tfoot" in html
        assert ">Total</td>" in html or ">Total<" in html
        # Footer contains currency values (dollar amounts).
        # The total interest for a $250K/6.5%/30yr loan is significant.
        assert "$" in html

    def test_schedule_totals_match_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-11: Totals row values match the sum of individual data rows.

        Computes expected totals from the amortization engine directly
        and verifies they appear in the rendered HTML.

        For $250K at 6.5% for 360 months with no escrow, no extra:
        Total interest = sum of all monthly interest values over the
        full schedule.  The engine computes this precisely using Decimal.
        No escrow means Payment total = P&I total.
        """
        # Compute expected totals from the engine via
        # ``project_forward`` (Commit 9 of the amortization-engine
        # split removed ``generate_schedule``).  The fixture loan has
        # no payments and no overrides, so a pure contractual
        # projection over the full term replicates the legacy
        # surface's output exactly.
        from app.services.amortization_engine import (  # pylint: disable=import-outside-toplevel
            PeriodTerms,
            ProjectionInputs,
            calculate_monthly_payment,
            project_forward,
        )
        from app.services.installment_calendar import (  # pylint: disable=import-outside-toplevel
            first_installment_date,
        )

        principal = Decimal("250000.00")
        rate = Decimal("0.06500")
        term = 360
        # Originated on the first of the frozen today's month (2026-03-01)
        # so the first installment (Apr 1) is after today and the plan is
        # the whole contract: 360 installments, none overdue (see
        # ``test_schedule_has_correct_row_count``).
        origination_date = date(2026, 3, 1)
        starting_date = first_installment_date(origination_date, 1)
        contractual = calculate_monthly_payment(principal, rate, term)

        schedule = project_forward(
            ProjectionInputs(
                starting_balance=principal,
                starting_date=starting_date,
                remaining_months=term,
                payment_day=1,
                terms_schedule=[PeriodTerms(
                    start_date=starting_date,
                    annual_rate=rate,
                    monthly_pi=contractual,
                    convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                )],
            ),
        )
        expected_interest = sum(
            (row.interest for row in schedule), Decimal("0.00"),
        )
        # No escrow on this test loan, so Payment = P&I total.
        expected_payment = sum(
            (row.payment for row in schedule), Decimal("0.00"),
        )
        formatted_interest = f"${expected_interest:,.2f}"
        formatted_payment = f"${expected_payment:,.2f}"

        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=origination_date,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert formatted_interest in html, (
            f"Expected total interest {formatted_interest} not found"
        )
        assert formatted_payment in html, (
            f"Expected total payment {formatted_payment} not found"
        )

    def test_schedule_overpayment_shows_the_actual_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-12 (re-pinned again, step E1a): a POSTED overpayment shows its real extra.

        The prior pin ("the cash overage is ignored") dated from the
        contractual-schedule balance model, which the balance arc
        SUPERSEDED: since the read switch (PR #52) a confirmed schedule
        row is read from the ledger and carries the payment's ACTUAL
        economics -- ``extra_payment`` is the real excess above the
        governing period's contractual P&I ("off-schedule the rows show
        what actually happened", plan step C3).  The old pin stayed
        green only because a transfer created ALREADY settled never
        posted (the unposted-ledger hole step E1a closed): its cash
        never reached the ledger, so the row had no economics to show.
        With the payment genuinely posted, the $2,080.17 cash against
        the $1,580.17 contractual P&I is interest + principal with a
        real $500.00 excess, and hiding it would misreport what the
        borrower actually paid.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # A DONE transfer above the contractual P&I ($2080.17 vs $1580.17):
        # the $500.00 overage is the payment's ACTUAL extra.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("2080.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The ledger-derived confirmed row carries the real excess, so the
        # Extra column renders and shows it.  The ``$`` anchor keeps the
        # amount from false-matching a thousands figure like $1,500.00.
        assert ">Extra</th>" in html, (
            "A posted overpayment's actual extra must render on the schedule"
        )
        assert "$500.00" in html, (
            "The confirmed row must carry the payment's real $500.00 extra"
        )

    def test_schedule_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-13: Schedule uses committed data when payments exist.

        Creates a mortgage with a confirmed transfer.  If the schedule
        used only the original (no-payments) schedule, no rows would
        be marked Confirmed.  Presence of Confirmed badges proves the
        committed schedule (via the composer's history_rows) is used.

        Re-pinned for Commit 5 of the amortization engine split.
        See ``test_schedule_confirmed_rows_marked`` for the full
        rationale (same architectural change: composer's replay only
        consumes confirmed payments with ``payment_date <= as_of``;
        a future-dated DONE is the plan's and renders as Projected).
        Previous fixture used April 2026 seed_periods after the frozen
        today (2026-03-20); this rewrite uses February 2026 so the DONE
        payment lands in
        replay.
        """
        # Origination 2026-01-01 -> first scheduled payment month
        # is February 2026.  seed_periods[3] (2026-02-13) falls in
        # the replay window before today (2026-03-20).
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed >= 1, (
            "Expected at least 1 confirmed row from committed schedule"
        )

    def test_schedule_currency_formatting_consistent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-14: Currency values in the schedule use consistent formatting.

        All monetary values should match the $X,XXX.XX pattern with
        exactly 2 decimal places.  Parses several values from the
        schedule and validates the format.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract dollar amounts from the schedule.  The pattern
        # matches $X.XX through $XXX,XXX,XXX.XX.
        amounts = re.findall(r'\$[\d,]+\.\d{2}', html)
        assert len(amounts) > 100, (
            f"Expected many currency values in schedule, found {len(amounts)}"
        )
        # Every matched amount should have exactly 2 decimal places.
        for amount in amounts[:20]:  # Spot-check first 20.
            assert re.match(r'^\$[\d,]+\.\d{2}$', amount), (
                f"Currency format mismatch: {amount}"
            )


class TestLoanScheduleRoute:
    """The standalone amortization-schedule page (demoted off the detail page)."""

    def test_schedule_page_renders_the_table(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET /loan/schedule renders the month-by-month schedule for the loan.

        The full statement table lives on its own page now; it renders the
        planned schedule (the same LoanState the detail card reads), so the
        Month-by-Month heading, the Balance column, and a Confirmed/Projected
        status badge are all present.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        assert b"Amortization Schedule" in resp.data
        assert b"Month-by-Month Schedule" in resp.data
        assert b"Balance" in resp.data
        assert b"Projected" in resp.data

    def test_schedule_page_404_for_other_users_loan(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """A cross-owner loan's schedule 404s (404 for not-found and not-yours)."""
        other = _create_other_loan(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other.id}/loan/schedule")
        assert resp.status_code == 404

    def test_schedule_page_redirects_when_unconfigured(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An amortizing account with no LoanParams redirects to the detail page.

        There is no schedule to show until the loan is set up; the shared
        configured-loan guard redirects to the detail page (the setup surface)
        instead of 500-ing.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Unconfigured Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan/schedule")
        assert resp.status_code == 302
        assert f"/accounts/{account.id}/loan" in resp.headers.get("Location", "")
