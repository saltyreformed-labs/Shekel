"""Loan route tests: the dashboard's payoff consistency and its chart composer.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.enums import AcctTypeEnum, StatusEnum

from tests._test_helpers import freeze_today
from tests._seed_owner_fixtures import log_in_seed_user

from ._helpers import (
    _create_loan_account,
    _parse_chart_array,
    _parse_band_chart,
    _create_transfer_to_loan,
    _add_escrow,
    _create_fresh_mortgage,
)


# -- Dashboard / Payoff Calculator Consistency Tests -------------------


class TestDashboardPayoffConsistency:
    """Verify the dashboard and payoff calculator use the same data pipeline.

    Both routes must produce identical amortization calculations from
    the same loan.  These tests catch mismatches caused by one route
    applying payment preparation (escrow subtraction, biweekly
    redistribution) while the other uses raw data.
    """

    def test_payoff_committed_matches_dashboard_chart(
        self, auth_client, seed_user, db, monkeypatch, seed_periods,
    ):
        """Payoff committed chart data matches dashboard committed chart.

        Both routes render committed balance data for Chart.js.  Since
        both use _load_route_context for payment preparation, the
        committed balance arrays must be identical.

        Origination is pinned to March 1, 2026 so seed_periods[7]
        (April 10, 2026) matches the schedule's first payment month
        (April 2026).  Without the pin, ``_create_fresh_mortgage``
        derives origination from ``date.today()``; once today moves
        past April, the April transfer no longer matches any schedule
        month, both routes produce empty committed arrays, and the
        equality assertion passes trivially without exercising the
        integration the test was written to verify.
        **Today is moved past that payment**, overriding the module freeze at
        2026-03-20.  A settled payment in a period that STARTS after today is a
        settle in its own future, which ruling R-EJ refuses at the write door --
        a settled row asserts that money has already moved.  The clock is moved
        rather than the period, because the period is load-bearing here: the
        docstring above explains why seed_periods[7] specifically must line up
        with the schedule's first payment month, and moving it would make the
        assertion vacuous in exactly the way that paragraph warns about.
        """
        freeze_today(monkeypatch, seed_periods[7].start_date + timedelta(days=5))
        # Sign in again on the moved date: ``auth_client`` signed in under the
        # module freeze, and the idle timeout measures against the clock this
        # freeze moves, so the old session is expired here as a real one would be.
        log_in_seed_user(auth_client)
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        # Dashboard: the band's balance line is the committed trajectory.
        dash_resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert dash_resp.status_code == 200
        band = _parse_band_chart(dash_resp.data.decode())
        assert band is not None, "Dashboard missing band chart data"

        # Payoff with 0 extra: the accelerated overlay equals the committed
        # trajectory (there is no extra to accelerate), forward-only.
        payoff_resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert payoff_resp.status_code == 200
        overlay = _parse_chart_array(payoff_resp.data.decode(), "overlay")
        assert overlay is not None, "Payoff missing overlay chart data"

        # Both derive from _load_route_context -> compute_payoff_scenarios, so the
        # overlay's forward slice must byte-match the band's committed forward
        # slice; the overlay is null across the confirmed-history region.
        current_index = band["current_index"]
        assert overlay[current_index:] == band["balance"][current_index:], (
            "Dashboard band and payoff overlay forward slices differ -- "
            "data pipeline mismatch"
        )
        assert all(v is None for v in overlay[:current_index])

    def test_payoff_with_payments_no_crash(
        self, auth_client, seed_user, db, monkeypatch, seed_periods,
    ):
        """Payoff calculator with prepared payments does not crash.

        After the DRY refactor, both routes use _load_route_context.
        Verify the payoff calculator handles prepared payments correctly
        in both extra_payment and target_date modes.

        **Today is moved past the settled payment**, overriding the module
        freeze at 2026-03-20: a settled payment whose period STARTS after today
        is a settle in its own future, which ruling R-EJ refuses at the write
        door.  The PROJECTED payment below stays where it is -- a projected row
        carries no settle day, so it is legitimately still ahead.
        """
        freeze_today(monkeypatch, seed_periods[7].start_date + timedelta(days=5))
        # Sign in again on the moved date -- see the case above.
        log_in_seed_user(auth_client)
        acct = _create_fresh_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Extra payment mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200

        # Target date mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_escrow_subtracted_consistently(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow-inclusive transfers do not inflate payoff savings.

        When transfers include escrow, both routes must subtract it
        before passing to the engine.  Without this, the payoff
        calculator would report inflated interest savings because the
        engine would count escrow as extra principal.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # Add escrow: $600/month.
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        db.session.commit()

        # Transfer includes P&I (~$1,580) + escrow ($600) = ~$2,180.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("2180.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard should NOT show the escrow as "extra" in schedule.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The Escrow column should show $600 (7200/12).
        assert "Escrow" in html
        assert "$600.00" in html

        # Payoff calculator should also work with prepared payments.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200

    def test_biweekly_overlap_handled_in_payoff(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments in the same month are redistributed in payoff too.

        Creates two transfers in the same calendar month (biweekly
        overlap).  Both the dashboard and payoff calculator must
        distribute them across two schedule months.

        Origination is pinned to March 1, 2026 so seed_periods[7]
        (April 10) and seed_periods[8] (April 24) BOTH fall in the
        schedule's first payment month (April 2026), exercising the
        biweekly redistribution code path.  Without the pin, the
        schedule's first month would shift past April and the
        transfers would not match any schedule month -- the
        ``"$3,160" not in html`` assertion would then pass trivially
        even if the biweekly fix were broken.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        # seed_periods[7] (April 10) and [8] (April 24) are both in
        # April 2026.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[8], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard schedule: no month should show double payment.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # $3,160 (2x $1,580.17) should NOT appear as a single payment.
        assert "$3,160" not in html, (
            "Dashboard shows double payment -- biweekly fix not applied"
        )

        # Payoff calculator: same -- should not crash or double-count.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200


# -- Dashboard Chart Composer Tests (Commit 5) -----------------------------


class TestDashboardChartComposer:
    """Lock the dashboard's projected figures to ONE forward walk.

    Commit 5 of the amortization engine split
    (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``)
    replaced the dashboard's three direct ``generate_schedule`` calls
    (planned, original, floor) with two composer calls; plan step R7d-g-3
    (ruling **R-R88**) then re-cut every projected figure -- the band
    chart's line, the schedule's planned rows, the breakdown's next payment
    -- onto the balance seam's plan fold (``balance_at.positions`` /
    ``loan_installments``), the walk the "Projected payoff" chip already
    read, so the composer keeps only the confirmed history and the
    contract's grid.  These tests lock the resulting behavior:

    * C5-1..C5-4 and C5-8 are "assert-unchanged" -- they pin the
      dashboard output against hand-computed expectations (one point per
      scheduled month, no ``generate_schedule`` 361-row residue artifact).
    * C5-5 is a static grep guard: the dashboard body MUST NOT call
      ``generate_schedule`` directly.
    * C5-6 / C5-7 lock the floor's "projections cancelled" semantic.

    Helper notes: ``_create_fresh_mortgage`` with
    ``origination_date=date(2026, 1, 1)`` produces a 30-year
    $250,000 / 6.5% mortgage whose first scheduled payment month is
    February 2026.  ``seed_periods[2]`` (2026-01-30 .. 2026-02-12) contains
    2/1 and ``seed_periods[3]`` (2026-02-13 .. 2026-02-26) contains no 1st,
    so a payment in the former is due Feb 1 and one in the latter Mar 1;
    both settle before the autouse-frozen today (2026-03-20) and land in the
    composer's replay window.  **Every installment due by today is paid in
    these fixtures**: an installment left unpaid past its due date is billed
    the day after the read on a loan with no recurring payment (rulings
    plan#3982, plan#3983), and one whose row the owner deleted pays nothing,
    its accrued interest standing against the next payment until the loan
    clears past its contract -- the shape
    ``TestAmortizationSchedule.test_schedule_lists_the_plan_when_an_installment_is_overdue``
    pins, and not what "unchanged" means here.
    """

    def test_dashboard_chart_values_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-1: the band chart's line is the seam's balance on the contractual grid.

        Fixture: 30-yr / $250k / 6.5% mortgage originated 2026-01-01, its
        two installments due by today paid -- Feb 1 2026 (seed_periods[2],
        the pay period that CONTAINS 2/1) and Mar 1 2026 (seed_periods[3],
        which contains no 1st, so its payment is due 3/1) -- and one
        projected payment for Jun 1 2026 (seed_periods[9]), which the plan
        takes as that month's occurrence at its own cash.

        Asserts the band's balance line is the seam's:
          * Length equals term_months (360) -- one point per scheduled month
            (the confirmed history's two, the contract's grid for the rest),
            no residue artifact and no extension, because nothing is overdue.
          * current_index is 2 (the two grid dates at or before today; the
            solid segment reads the ledger).
          * The line is monotonically non-increasing (positive amortization
            with no overpayment) and ends at $0.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # Confirmed Feb 1 and Mar 1 2026 (both settled before today=2026-03-20)
        # -- the replay's history_rows.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        # Projected Jun 1 2026 (after today) -- an occurrence in the plan.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        balance = band["balance"]

        # 360 months for a 30-yr mortgage with no overpayment (one point per
        # scheduled month; no pre-Commit-5 residue artifact).
        assert len(balance) == 360, (
            f"Expected 360 points on the grid, got {len(balance)}"
        )
        assert band["current_index"] == 2, (
            f"Expected two grid dates at or before today, got {band['current_index']}"
        )
        # The line never increases month-over-month (positive amortization)
        # and pays off at $0 at term.
        for i in range(1, len(balance)):
            assert balance[i] <= balance[i - 1] + 0.01, (
                f"Balance increased at index {i}: "
                f"{balance[i - 1]} -> {balance[i]}"
            )
        assert balance[-1] == 0.0

    def test_amortization_schedule_rows_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-2: the schedule route renders term_months rows for a full loan.

        Uses the same fixture as C5-1.  The schedule renders the composer's
        confirmed ``history_rows`` followed by the seam's forward plan
        (``balance_at.loan_installments``) through the payoff.  History
        contributes two rows (the Feb 1 and Mar 1 2026 confirmed payments);
        the plan contributes the 358 remaining installments.  Total: 360
        rows.  Re-pinned for Commit 5 (one row per remaining month, no
        residue artifact), the Loop B schedule demotion (its own route) and
        R7d-g-3 (the plan's rows).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        total = confirmed + projected
        # 2 confirmed (Feb and Mar 2026 in history) + 358 planned = 360.
        assert confirmed == 2, f"Expected 2 confirmed rows, got {confirmed}"
        assert total == 360, (
            f"Expected 360 total schedule rows, got {total} "
            f"({confirmed} confirmed + {projected} projected)"
        )

    def test_payment_breakdown_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-3: Payment breakdown sums to total and percentages sum to 100.

        The breakdown card derives from the seam's NEXT planned installment
        (``balance_at.loan_installments(...)[0]``, its allocation already
        split by the fold; plan step R7d-g-3).  With both installments due
        by today paid, the Apr 1 2026 payment pays interest and principal
        and no escrow, so two segments render and the
        truncate-then-distribute percentages MUST still sum to exactly
        100.0% (the dashboard rendering invariant).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract the breakdown card's percentages via the
        # ``data-progress-pct`` attribute, which only appears on the
        # principal/interest/escrow progress bars and not elsewhere
        # on the page.  This isolates the truncate-then-distribute
        # output from unrelated percent strings (e.g., the interest
        # rate display).
        pct_strings = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert len(pct_strings) >= 2, (
            f"Expected >= 2 breakdown percent attributes, "
            f"found {pct_strings}"
        )
        breakdown_pcts = [Decimal(p) for p in pct_strings]
        total_pct = sum(breakdown_pcts, Decimal("0.0"))
        assert total_pct == Decimal("100.0"), (
            f"Breakdown percentages sum to {total_pct}, expected 100.0"
        )

    def test_recurrence_start_sync_is_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The opening-bound sync writes recurrence_rules only on a change.

        Creating the recurring transfer writes the rule's start once and its
        stop never (plan step R7d-g: the column stays NULL).  A follow-on
        params re-save with the same 6.5% / 360-month / day-1 terms derives
        the same first installment, so the sync skips the write -- no new
        ``recurrence_rules`` UPDATE lands in the audit log.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        # Creating the recurring transfer via the route sets end_date once.
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )

        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        template = db.session.query(TransferTemplate).filter_by(
            to_account_id=acct.id,
        ).one()
        first_start = template.recurrence_rule.starts_on
        assert template.recurrence_rule.end_date is None

        # The guard short-circuits when the re-derived start equals the
        # current one; the system.audit_log row count must NOT increase after a
        # no-op params re-save.
        audit_count_sql = sa.text(
            "SELECT COUNT(*) FROM system.audit_log "
            "WHERE table_name = 'recurrence_rules' AND operation = 'UPDATE'"
        )
        audit_before = db.session.execute(audit_count_sql).scalar()

        # A params re-save with unchanged terms derives the SAME start.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "1",
                "term_months": "360",
            },
        )
        assert resp.status_code == 302
        db.session.expire_all()
        rule = db.session.query(RecurrenceRule).filter_by(
            id=template.recurrence_rule.id,
        ).one()
        assert rule.starts_on == first_start
        assert rule.end_date is None
        audit_after = db.session.execute(audit_count_sql).scalar()
        assert audit_after == audit_before, (
            "A no-op params mutation wrote a new recurrence_rule UPDATE row "
            f"to system.audit_log ({audit_before} -> {audit_after}) -- "
            "the start sync's idempotency guard failed"
        )

    def test_no_direct_generate_schedule_in_dashboard(self):
        """C5-5: the dashboard surface must not call generate_schedule directly.

        Static grep against ``app/routes/loan/dashboard.py`` -- the
        dashboard route plus its context-building helpers
        (``_build_dashboard_scenarios`` etc.) after the Phase 3
        pylint-cleanup split + decomposition -- confirming the dashboard
        was migrated to the ``compute_payoff_scenarios`` composer.  The
        bare word ``generate_schedule`` may still appear in a comment /
        docstring, but never as a direct ``amortization_engine.``
        engine call from the dashboard surface.
        """
        import pathlib  # pylint: disable=import-outside-toplevel
        source = pathlib.Path("app/routes/loan/dashboard.py").read_text()
        assert "amortization_engine.generate_schedule" not in source, (
            "Dashboard surface still calls "
            "amortization_engine.generate_schedule directly -- Commit 5 "
            "migration incomplete"
        )

    def test_arm_dashboard_chart_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-8: ARM dashboard band chart values come from the seam's fold.

        Creates an ARM mortgage in its fixed-rate window with no payments,
        asserted at $100,000 on 2026-01-01 (``_create_loan_account``).  The
        grid's Feb 1 and Mar 1 2026 fall at or before the frozen 2026-03-20
        today and read the LEDGER -- the asserted balance, flat, nothing
        paid.  The loan has no recurring payment, so its plan is its contract
        line (ruling plan#3982): those two installments, due and unpaid, are
        billed the day after the read and split as if paid on their due dates
        (rulings plan#3983, plan#4029), so by the Apr 1 point three
        installments have paid it down and the projected line's first point
        sits BELOW the ledger's, then amortizes.  Asserts the line is the
        seam's: the two ledger points flat at $100,000, non-increasing from
        there on (the rate cannot rise in the fixed-rate window), and ending
        at $0, where the term's last installment closes the loan.  Until plan
        step recurrence:R16-f the plan billed neither missed installment (B-9),
        so the line started ABOVE the ledger's and ended in the sixty-payment
        extension past the term.

        (The former C5-6 / C5-7 "floor" route tests were retired with the Loop B
        band rebuild: the locked band anatomy plots only the plan's line plus
        the lever's accelerated preview, so the dashboard no longer computes or
        serializes a floor series.)
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM 5/1",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Fixed-rate window: arm_first_adjustment_months defaults to
        # None in this fixture, so the ARM behaves like fixed-rate
        # for the resolver outside the window.  Either way the
        # fold's behavior is locked.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        balance = band["balance"]

        assert len(balance) > 2
        # The two grid dates at or before today are the ledger's: the
        # asserted balance, unpaid.
        assert band["current_index"] == 2
        assert balance[:2] == [100000.0, 100000.0]
        # The missed installments count from the day after the read, so the
        # projected line starts below the ledger's point.
        assert balance[2] < 100000.0
        # Last entry reaches $0 (the term's last installment closes it).
        assert balance[-1] == 0.0
        # Non-increasing across the fixed-rate window from the ledger's last
        # point on.
        for i in range(2, len(balance)):
            assert balance[i] <= balance[i - 1] + 0.01, (
                f"ARM band balance increased at index {i}: "
                f"{balance[i - 1]} -> {balance[i]}"
            )
