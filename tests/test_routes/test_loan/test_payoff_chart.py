"""Loan route tests: the payoff calculator, the payoff chart's shape, the band chart's baseline,
the multi-scenario visualization, and a corrected loan's chart months.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, StatusEnum
from app.models.loan_features import RateHistory
from app.routes import loan as loan_routes
from app.routes.loan._helpers import accelerated_overlay, build_band_chart
from app.services.balance_at import BalanceContext
from app.services.loan_loaders import load_loan_params
from app.utils.dates import months_between
from app.services import balance_at

from ._helpers import (
    _create_loan_account,
    _create_auto_loan,
    _create_mortgage,
    _create_other_loan,
    _parse_chart_array,
    _parse_band_chart,
    _create_transfer_to_loan,
    _create_fresh_mortgage,
)


# ── Payoff Calculator Tests ──────────────────────────────────────────


class TestPayoffCalculator:
    """Tests for the payoff calculator."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_payoff_extra_payment(self, auth_client, seed_user, db, seed_periods, create_fn):
        """POST extra payment mode returns results for any loan type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        assert b"Months Saved" in resp.data

    def test_payoff_target_date(self, auth_client, seed_user, db, seed_periods):
        """POST target date mode returns payment data."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200
        assert b"$" in resp.data

    def test_payoff_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid mode returns error."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data

    def test_payoff_idor(self, auth_client, second_user, db, seed_periods):
        """Payoff calc to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "100"},
        )
        assert resp.status_code == 404


class TestPayoffChartShape:
    """C4-1..C4-8: HTTP-level regression locks for the payoff-calculator chart.

    Today is frozen to 2026-03-20 by ``_freeze_today_inside_seed_range``,
    so confirmed payments dated in Jan/Feb 2026 are historical and
    chart points dated April 2026 onward are forward.  Each test
    pins one property of the composer's chart output as exposed by
    the route -- the architectural fix landed in Commit 4 must
    structurally satisfy them.
    """

    TODAY_MONTH = (2026, 3)

    def _create_loan_with_historical_confirmed(
        self, seed_user, db_session, periods,
    ):
        """Create a mortgage with two confirmed payments in Jan-Feb 2026.

        Returns the loan account.  The confirmed payments live in
        ``periods[1]`` (2026-01-16 window) and ``periods[3]``
        (2026-02-13 window), both strictly before today
        (2026-03-20).  ``_create_mortgage`` originates at
        2023-06-01, so 30+ months of gap separate origination from
        the first confirmed payment -- the temporal-gap shape that
        surfaces the buggy "extra applied to ghost historical
        months" behavior the architectural fix prevents.
        """
        acct = _create_mortgage(seed_user, db_session)
        _create_transfer_to_loan(
            seed_user, acct, periods[1], Decimal("1611.64"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, periods[3], Decimal("1611.64"),
            status_enum=StatusEnum.DONE,
        )
        db_session.commit()
        return acct

    def test_chart_lengths_equal(self, auth_client, seed_user, db, seed_periods):
        """C4-1: the payoff overlay aligns to the band chart's shared x-axis.

        The band chart (labels + committed balance line) and the payoff lever's
        accelerated overlay are both padded to the same contractual length, so
        loan_detail.js overlays the green preview against one set of labels
        without alignment gymnastics on the JS side.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None
        assert overlay is not None
        assert len(band["labels"]) == len(band["balance"]) == len(overlay)

    def test_accelerated_overlay_null_in_historical_region(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-2: HTTP-level regression lock for the user's reported visual bug.

        The reported bug was $500 of extra principal applied to ghost historical
        months, drawing an accelerated line diverging from committed back at
        month 1 (2023-07).  The band-chart preview overlay now begins at Today
        and never redraws the confirmed history: every overlay index before the
        confirmed/projected boundary (the band's ``current_index``) is null, and
        the boundary index is the first non-null forward point -- so extra can
        never be attributed to a historical month.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None and overlay is not None

        current_index = band["current_index"]
        assert current_index > 0, (
            "This fixture has two confirmed payments, so the band's "
            f"current_index must be > 0; got {current_index}"
        )
        for i in range(current_index):
            assert overlay[i] is None, (
                f"Overlay[{i}] ({overlay[i]!r}) is not null in the confirmed "
                "history region -- the preview must not redraw (or accelerate) "
                "historical months."
            )
        assert overlay[current_index] is not None, (
            "The overlay must begin (non-null) at the confirmed/projected "
            "boundary -- the first forward, accelerable month."
        )

    def test_accelerated_overlay_below_committed_post_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-3: the preview overlay drops strictly below the committed line.

        With ``extra_monthly=500`` and no projected override, every forward
        month receives the extra principal, so the accelerated overlay's balance
        is strictly below the band's committed line for at least one post-today
        index.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None and overlay is not None

        committed = band["balance"]
        forward = range(band["current_index"], len(committed))
        assert any(
            overlay[i] is not None and overlay[i] < committed[i]
            for i in forward
        ), (
            "Expected the accelerated overlay strictly below the committed line "
            "at some post-today index; extra_monthly was ignored on the "
            "projection side."
        )

    def test_summary_consistent_with_chart(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-4: Displayed Months Saved matches the overlay-vs-plan divergence.

        The lever's ``months_saved`` is the calendar months between the plan's
        payoff and the payoff with the extra, both the seam's fold
        (``routes/loan/calculators._build_payoff_summary``, plan step
        R7d-g-3).  Single-source-of-truth means the rendered ``Months Saved``
        label equals the count of chart indices where the plan's line still
        owes but the accelerated overlay has already reached $0 -- the line
        and the overlay are the same fold on the band's monthly grid, so they
        agree by construction.
        """
        # Pylint: ``import-outside-toplevel`` -- a module only this test reads.
        import re as _re  # pylint: disable=import-outside-toplevel

        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        overlay = _parse_chart_array(html, "overlay")
        assert band is not None and overlay is not None
        committed = band["balance"]

        # months_saved == count of months where Committed still owes but the
        # accelerated overlay has already paid off (0.0).  Both series pad with
        # 0.0 after their own payoff, so the count is exactly the difference in
        # payoff month index; the overlay is null across the history region and
        # so never counts there.
        chart_months_saved = sum(
            1 for i in range(len(committed))
            if committed[i] > 0.0 and overlay[i] == 0.0
        )

        # The template renders Months Saved as the first numeric value following
        # the "Months Saved" label; the negated class skips every non-digit
        # character (including hyphens in HTML class names) up to the integer.
        match = _re.search(
            r"Months Saved[^0-9]*(\d+)", html,
        )
        assert match is not None, (
            "Could not find Months Saved label in the rendered "
            "payoff results partial."
        )
        displayed_months_saved = int(match.group(1))
        assert displayed_months_saved == chart_months_saved, (
            f"Displayed Months Saved ({displayed_months_saved}) "
            f"!= chart divergence count ({chart_months_saved}); "
            "chart and summary must derive from the same forward "
            "slices."
        )

    def test_no_payment_history_chart_starts_at_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-5: Loan with zero confirmed payments still renders a full band chart.

        With no confirmed payments the band still splits at TODAY, not at a
        confirmed row: the grid runs from the first installment after the
        loan's latest assertion, and every grid date at or before the pass's
        as-of reads the LEDGER (plan step R7d-g-3, ruling **R-R88**: the
        band is the seam's ``positions`` on the contractual grid), which
        holds the asserted $250,000 flat because nothing was paid.  The
        overlay begins after today -- ``None`` on the ledger's points, a
        what-if balance on every point after -- and aligns to the same
        labels.
        """
        acct = _create_mortgage(seed_user, db.session)
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None and overlay is not None
        # First label is the month after the loan's LATEST assertion:
        # _create_mortgage's tracking-start is dated ``_TRACKED_FROM`` (2026-01-01)
        # at $250k, and with no confirmed payments the first row is the
        # contractual grid's first installment after it.  (It read "Jul 2023"
        # while the true-up was dated the day after origination.)  Feb 1 and
        # Mar 1 2026 are at or before the frozen 2026-03-20 today, so those
        # two points are the ledger's: the asserted balance, unpaid.
        assert band["labels"][:3] == ["Feb 2026", "Mar 2026", "Apr 2026"]
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]
        assert len(band["balance"]) == len(band["labels"]) == len(overlay) > 0
        # The overlay is null on the ledger's points and begins after today.
        assert overlay[:2] == [None, None]
        assert overlay[2] is not None

    def test_target_date_mode_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-6: target_date branch behavior unaffected by Commit 4.

        Commit 4 modifies only the extra_payment branch; the
        target_date branch migrates in Commit 7.  Verify
        target_date still returns the expected required-extra
        partial and does not regress on the helpers/imports the
        composer-collapse refactor touched.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Expected message strings from the target_date branch of
        # the template (existing behavior).
        assert (
            "Required Extra Monthly Payment" in html
            or "Your loan will be paid off" in html
            or "Target date is not achievable" in html
        )

    def test_no_direct_calculate_summary_call(self):
        """C4-7: production code no longer calls ``calculate_summary``.

        Static-source guard for the architectural invariant that
        the route surface routes through ``compute_payoff_scenarios``,
        not directly to the engine's now-deprecated summary helper.
        Lighter-weight than parsing AST; the grep matches any
        call-style use of the symbol on its
        ``amortization_engine.`` prefix.
        """
        # Pylint: ``import-outside-toplevel`` -- a module only this test reads.
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        # loan.py is now the app/routes/loan/ package (Phase 3 pylint
        # cleanup split); grep every sub-module so coverage is preserved.
        # The package is found from its own module, never from this test's
        # place in the tree, and the read must hold the module the payoff
        # route lives in: a "not in" over an empty read passes on nothing.
        loan_pkg = Path(inspect.getfile(loan_routes)).parent
        modules = sorted(loan_pkg.glob("*.py"))
        assert loan_pkg / "calculators.py" in modules, (
            f"read no loan route modules from {loan_pkg}: {modules}"
        )
        text = "\n".join(p.read_text(encoding="utf-8") for p in modules)
        assert "amortization_engine.calculate_summary" not in text, (
            "app/routes/loan/ still references "
            "amortization_engine.calculate_summary -- the Commit 4 "
            "migration should have removed the only production "
            "caller of this function."
        )

    def test_no_direct_generate_schedule_call_in_extra_payment_branch(self):
        """C4-8: the extra-payment payoff path makes no direct engine call.

        The extra-payment branch's computation now lives in the
        ``_payoff_extra_payment_result`` helper (Phase 3 pylint cleanup
        decomposed ``payoff_calculate``; the route just dispatches to
        it).  Slice that helper out of
        ``app/routes/loan/calculators.py`` and assert it routes through
        ``compute_payoff_scenarios``, never a direct
        ``generate_schedule`` / ``calculate_summary`` call.
        """
        # Pylint: ``import-outside-toplevel`` -- a module only this test reads.
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        # Found from the package's own module, never from this test's place
        # in the tree; a missing file raises and the marker assert below
        # fails on an empty read.
        calculators = Path(inspect.getfile(loan_routes)).parent / "calculators.py"
        text = calculators.read_text(encoding="utf-8")
        start_marker = "def _payoff_extra_payment_result("
        end_marker = "\ndef _payoff_target_date_result("
        start = text.find(start_marker)
        end = text.find(end_marker, start)
        assert start != -1 and end != -1 and end > start, (
            "Could not slice _payoff_extra_payment_result out of "
            "calculators.py -- marker strings have drifted."
        )
        branch_source = text[start:end]
        assert "amortization_engine.generate_schedule" not in branch_source, (
            "extra_payment computation still contains a direct "
            "amortization_engine.generate_schedule call -- the "
            "Commit 4 migration should have collapsed every direct "
            "engine call onto compute_payoff_scenarios."
        )
        assert "amortization_engine.calculate_summary" not in branch_source, (
            "extra_payment computation still contains a direct "
            "amortization_engine.calculate_summary call."
        )


def _band_inputs(seed_user, acct):
    """Return ``(balance_ctx, history_rows, contract, dates)`` as the dashboard builds them.

    ``_load_route_context`` reads the pass's owner off ``current_user``;
    outside a request the pass is built from the owner's id and the loan
    context loaded the same way it does.  The grid is
    ``band_chart_dates`` over the confirmed history, the contract line
    (``balance_at.loan_contract_installments``, plan step
    recurrence:R16-f) and the plan as it stands, as the dashboard hands
    it.  The contract of each loan here is behind or on its schedule, so
    none of its installments falls after the one that closes it.
    """
    # Pylint: import-outside-toplevel -- route-private helpers under test.
    from app.routes.loan._helpers import (  # pylint: disable=import-outside-toplevel
        _loan_inputs, band_chart_dates, build_baseline_scenarios,
    )
    # Pylint: ``import-outside-toplevel`` -- a module only this test reads.
    from app.services.loan_payment_service import (  # pylint: disable=import-outside-toplevel
        load_loan_context,
    )
    params = load_loan_params(acct.id)
    balance_ctx = BalanceContext.build(seed_user["user"].id)
    loan = load_loan_context(acct.id)
    history_rows = build_baseline_scenarios(
        _loan_inputs(params, loan), acct, balance_ctx,
    ).history_rows
    contract = balance_at.loan_contract_installments(acct, balance_ctx)
    dates = band_chart_dates(
        history_rows,
        contract,
        balance_at.loan_installments(acct, balance_ctx),
        params,
        balance_at.loan_terms(acct, balance_ctx).recorded_start,
    )
    return balance_ctx, history_rows, contract, dates


class TestBandChartLongestBaseline:
    """loan_audit deferred follow-up #2: the band x-axis runs to the plan's payoff.

    A plan slower than the contractual P&I -- here a loan two installments
    behind, whose missed principal the post-contractual extension clears --
    reaches zero AFTER the contract's last installment.  The band's grid is the
    contractual calendar extended month by month to the seam's derived payoff
    (``band_chart_dates``, plan step R7d-g-3), so the balance line never plots
    past the last labelled tick, and the lever's accelerated overlay is folded
    on that same grid so it stays aligned to the band's labels one-to-one.

    These read the route helpers directly (``build_band_chart`` /
    ``accelerated_overlay``) over a real loan: since R7d-g-3 both are the
    seam's fold on the band's grid, and the grid-selection defect lives in
    ``band_chart_dates``'s extension, which a loan whose payoff outruns its
    contract exercises directly.
    """

    @staticmethod
    def _two_installments_behind(seed_user, db_session):
        """A $250k / 6.5% / 30-year mortgage originated 2026-01-01, nothing paid.

        Its Feb 1 and Mar 1 2026 installments are due and unpaid at the frozen
        2026-03-20 today, so they are not in the plan (ruling D1 / finding
        B-9) and the fold clears the loan in the extension past Jan 2056.
        """
        acct = _create_fresh_mortgage(
            seed_user, db_session, origination_date=date(2026, 1, 1),
        )
        db_session.commit()
        return acct


    def test_band_labels_cover_the_plans_line(
        self, app, seed_user, db, seed_periods,
    ):
        """The band's balance line never runs past its labelled x-axis.

        The grid is the confirmed history's dates plus the contract's (none
        and 359 here: Feb 2026 .. Jan 2056), extended to the fold's payoff.
        So len(labels) == len(balance), the last label is LATER than the
        contract's last installment, the balance at the contract's last tick
        is still positive (the residue the extension clears) and the line
        ends at $0.  Before the fix (labels keyed off the contract alone) the
        line overran the labels by the extension.
        """
        acct = self._two_installments_behind(seed_user, db.session)
        with app.app_context():
            balance_ctx, history_rows, contract, dates = _band_inputs(
                seed_user, acct,
            )
            band = build_band_chart(acct, balance_ctx, dates)
            contract_end = balance_at.installments_payoff(contract)
            grid_end = balance_at.loan_payoff_date(acct, balance_ctx)
        assert not history_rows
        assert len(band["labels"]) == len(band["balance"])
        assert band["labels"][0] == "Feb 2026"
        assert band["labels"][len(contract) - 1] == "Jan 2056"
        assert contract_end == date(2056, 1, 1)
        assert grid_end is not None and grid_end > contract_end
        assert band["labels"][-1] == grid_end.strftime("%b %Y")
        assert band["balance"][len(contract) - 1] > 0.0
        assert band["balance"][-1] == 0.0
        # The two grid dates at or before today are the ledger's.
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]

    def test_a_month_end_loans_extension_keeps_the_months_end(
        self, app, seed_user, db, seed_periods,
    ):
        """The grid's extension steps from the contract's last date, not from itself.

        A loan due on the 31st: ``add_months`` clamps a 31st to a short
        month's end and, stepped AGAIN from that result, decays to the 28th
        for good, so an extension built one step at a time would put its
        dates before the plan's and run one tick past the payoff.  Since plan
        step recurrence:R16-c-2 the grid and the plan's extension both step
        the loan's ONE installment calendar
        (``installment_calendar.installment_dates``), which clamps the due day to each
        month afresh.  The grid's dates past the contract are the plan's own:
        the last label is the payoff's month and the point count past the
        contract is the month count to the payoff.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 31),
            payment_day=31,
        )
        db.session.commit()
        with app.app_context():
            balance_ctx, _history_rows, contract, dates = _band_inputs(
                seed_user, acct,
            )
            band = build_band_chart(acct, balance_ctx, dates)
            payoff = balance_at.loan_payoff_date(acct, balance_ctx)
            plan_dates = [
                installment.due_date
                for installment in balance_at.loan_installments(
                    acct, balance_ctx,
                )
            ]
        contract_end = balance_at.installments_payoff(contract)
        assert contract_end.day == 31
        assert payoff is not None and payoff > contract_end
        assert payoff in plan_dates
        past_contract = len(band["labels"]) - len(contract)
        assert past_contract == months_between(contract_end, payoff)
        assert band["labels"][-1] == payoff.strftime("%b %Y")
        assert band["balance"][-1] == 0.0
        assert band["balance"][-2] > 0.0

    def test_overlay_stays_aligned_to_the_band_labels(
        self, app, seed_user, db, seed_periods,
    ):
        """The accelerated overlay is folded on the band's own grid.

        ``accelerated_overlay`` reads ``band_chart_dates`` too, so the overlay
        has exactly the band's label count: ``None`` on the grid dates at or
        before today (the ledger's points), the what-if balance after.  With
        $500 a month on top it clears the loan before the plan does and pads
        $0.00 to the band's last tick -- aligned one-to-one, where a grid cut
        at the contract would land the overlay short of the plan's line.
        """
        acct = self._two_installments_behind(seed_user, db.session)
        with app.app_context():
            balance_ctx, _history_rows, _contract, dates = _band_inputs(
                seed_user, acct,
            )
            band = build_band_chart(acct, balance_ctx, dates)
            overlay = accelerated_overlay(
                acct, balance_ctx, dates, Decimal("500.00"),
            )
        assert len(overlay) == len(band["labels"])
        current_index = band["current_index"]
        assert current_index == 2
        assert overlay[:current_index] == [None, None]
        assert all(value is not None for value in overlay[current_index:])
        # Accelerates: reaches $0 strictly before the plan's line does, and
        # stays at $0 to the band's last tick.
        overlay_zero = overlay.index(0.0)
        plan_zero = band["balance"].index(0.0)
        assert overlay_zero < plan_zero
        assert set(overlay[overlay_zero:]) == {0.0}
        assert overlay[-1] == 0.0


# ── Multi-Scenario Visualization Tests (Commit 5.5-1) ──────────────


class TestMultiScenarioVisualization:
    """Tests for multi-scenario balance chart and payoff calculator.

    Verifies that the dashboard and payoff calculator correctly compute
    and display original, committed, floor, and accelerated scenarios.
    """

    def test_dashboard_chart_no_payments_shows_contractual(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers: the band splits at today, the past the ledger's.

        With no payments there is no confirmed history, yet the band still
        splits at TODAY (plan step R7d-g-3, ruling **R-R88**: the line is the
        seam's ``positions`` on the contractual grid -- a past date reads the
        ledger, a future one the plan fold).  ``_create_mortgage`` asserts
        $250,000 on 2026-01-01 with payment day 1, so the grid's Feb 1 and
        Mar 1 2026 fall at or before the frozen 2026-03-20 today: two
        ledger points at the asserted balance (nothing paid), then the
        projection.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]
        assert len(band["balance"]) > 2

    def test_dashboard_chart_with_projected_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with a projected transfer: band renders, no false history.

        A projected (not-yet-confirmed) transfer is the plan's, not the
        ledger's: the band's split stays at today (the same two ledger
        points as the no-transfer case, at the asserted $250,000 -- a
        projected row moves no past balance) and the balance line is
        present.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]
        assert len(band["balance"]) > 2

    def test_dashboard_chart_confirmed_payment_is_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with a confirmed transfer: the ledger's points carry it.

        A confirmed (Paid) transfer is the ledger's, so the band's solid
        segment -- the grid dates at or before today, read off the ledger --
        shows the balance it paid down: the Feb 1 2026 point (the payment is
        due Feb 1, settled Jan 16) sits BELOW the $250,000 asserted on
        2026-01-01, where the no-payment sibling's sits flat on it.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        assert band["current_index"] == 2
        assert band["labels"][0] == "Feb 2026"
        assert band["balance"][0] < 250000.0

    def test_payoff_results_committed_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator shows the committed-plan-vs-original comparison.

        When payments exist, the payoff results partial reports how the current
        committed plan compares to the original schedule and carries the
        accelerated overlay for the band chart.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Current plan vs. original" in html
        assert "data-overlay=" in html

    def test_payoff_what_if_overlay_and_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with extra renders the band overlay plus the saved-months metric.

        With payments and extra_monthly > 0, the result carries the accelerated
        overlay (the green preview) and the Months Saved figure.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-overlay=" in html
        assert "Months Saved" in html

    def test_payoff_no_transfer_degrades(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with no transfers still renders the overlay, without the plan comparison.

        With no payments the accelerated overlay still renders (from the
        contractual baseline) but the committed-plan-vs-original comparison line
        is omitted.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-overlay=" in html
        assert "Current plan vs. original" not in html

    def test_payoff_what_if_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """extra_monthly=0 renders without error and still carries the overlay."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-overlay=" in html

    def test_payoff_target_date_still_works(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target date mode is unaffected by multi-scenario changes.

        The existing target_date mode should continue to return
        required extra payment data without regressions.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_arm_band_renders(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM loan: the band renders its balance line and ARM tag.

        With no payments the band's past points are the ledger's (the
        $100,000 asserted on 2026-01-01, unpaid on the grid's Feb 1 and
        Mar 1) and the rest the plan fold's; the rate chip carries the ARM
        tag.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
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

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        band = _parse_band_chart(html)
        assert band is not None
        assert band["current_index"] == 2
        assert band["balance"][:2] == [100000.0, 100000.0]
        assert len(band["balance"]) > 2
        # The rate chip carries the ARM tag.
        assert "ARM" in html
        assert "Balance owed" in html


class TestTheLoanChartKeepsTheMonthsOfACorrectedLoan:
    """Ruling R-R114 on the loan chart: a corrected in-app loan charts from its first installment.

    Ruling R-R113's example: $20,000.00 at 6% for 24 months, kept in the app
    from 2025-12-22, due the 22nd, $2,000.00 paid on Jan 22, Feb 22 and Mar 22
    2026, then its balance corrected on 2026-04-01 to $15,000.00.  Since ruling
    R-R114 the tracking-start door refuses that date (payments are recorded
    before it), so the correction is a TRUE-UP, which starts nothing: the
    loan's record starts at its origination and the chart's grid runs from the
    first installment after it (Jan 22).  Recorded as a tracking start, the
    state the door now refuses, the record would start Apr 1 and the grid
    would open at Apr 22.  The seam's figures for those months are pinned in
    ``test_balance_at/test_loan_terms.py`` (``TestNoPaymentMovesTheRecordedStart``).
    """

    def test_the_grid_opens_at_the_first_installment_after_origination(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The band's first four points are Jan 22, Feb 22, Mar 22 and Apr 22 2026."""
        # Pylint: ``import-outside-toplevel`` -- the helpers this one test needs.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import (
            create_loan_account,
            create_settled_transfer,
            freeze_today,
            insert_trueup_event,
            loan_params_for,
        )
        freeze_today(monkeypatch, date(2026, 4, 20))
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Kept in the app",
                principal=Decimal("20000.00"), rate=Decimal("0.06000"), term=24,
                origination_date=date(2025, 12, 22), payment_day=22,
            )
            db.session.commit()
            for period_index, paid_on in (
                (1, date(2026, 1, 22)), (3, date(2026, 2, 22)),
                (5, date(2026, 3, 22)),
            ):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan,
                    seed_periods[period_index], amount=Decimal("2000.00"),
                    settled_on=paid_on, due_date=paid_on,
                )
            db.session.commit()
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("15000.00"),
                date(2026, 4, 1),
            )
            db.session.commit()

            dates = _band_inputs(seed_user, loan)[-1]

            assert dates[:4] == [
                date(2026, 1, 22), date(2026, 2, 22), date(2026, 3, 22),
                date(2026, 4, 22),
            ]
