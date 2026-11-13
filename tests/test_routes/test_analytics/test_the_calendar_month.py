"""The Calendar tab's month detail view and its flow strip."""

from tests._test_helpers import one_off_row_of

from ._helpers import _extract_flow_strip_payload


# ── Calendar Month View Tests ────────────────────────────────────────


class TestCalendarMonthView:
    """Tests for the calendar month detail view."""

    def test_calendar_month_renders(self, app, auth_client, seed_user, seed_periods):
        """Month view renders with current month name."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_navigation(self, app, auth_client, seed_user, seed_periods):
        """Month view for specific month/year contains the correct heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=3",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"March" in resp.data
            assert b"2026" in resp.data

    def test_calendar_month_has_day_cells(self, app, auth_client, seed_user, seed_periods):
        """Month view contains calendar-day elements."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day" in resp.data

    def test_calendar_paycheck_highlighting(self, app, auth_client, seed_user, seed_periods):
        """Paycheck days have the calendar-paycheck CSS class."""
        with app.app_context():
            # Request a month with known paycheck days (Jan 2026 has
            # periods starting Jan 2, Jan 16, Jan 30).
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-paycheck" in resp.data

    def test_calendar_month_empty(self, app, auth_client, seed_user, seed_periods):
        """Month with no transactions renders without crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=4",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_prev_next(self, app, auth_client, seed_user, seed_periods):
        """Month view has prev/next navigation buttons."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=6",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "month=5" in html  # prev button
            assert "month=7" in html  # next button

    def test_calendar_month_december_next_wraps(self, app, auth_client, seed_user, seed_periods):
        """December next button wraps to January of next year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=12",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2027" in html
            assert "month=1" in html

    def test_calendar_month_january_prev_wraps(self, app, auth_client, seed_user, seed_periods):
        """January prev button wraps to December of prior year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2025" in html
            assert "month=12" in html

    def test_calendar_month_year_overview_button(self, app, auth_client, seed_user, seed_periods):
        """Month view has a button to switch to year overview."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=year" in html

    def test_calendar_month_summary_strip_displayed(self, app, auth_client, seed_user, seed_periods, db):
        """Month view shows the cockpit band per the S5 recomposition (P-AN3).

        Redesign pin (analytics_audit.md Gate A locked the summary figures
        2026-07-04; ui_ux_polish_audit.md P-AN3 recomposed them as a cockpit
        in S5): one .nw-sky band carries a balance hero plus the summary
        chips.  January 2026 is wholly past relative to any display-tz today
        after 2026-01-31, so the hero is Month end (Balance today only
        renders inside the current month), the So far chip renders
        (captioned "full month"), and the future-only Remaining chip does
        not.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            one_off_row_of(
                seed_periods[0],
                name="Test Income",
                amount=Decimal("3000.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "nw-sky" in html
            assert "So far" in html
            assert "Income" in html
            assert "Expenses" in html
            assert "Month end" in html
            assert "Month trough" in html
            # Wholly past month: the projected-remainder chip is empty by
            # construction and hidden.
            assert "Remaining" not in html
            # So far pair: the seeded $3,000 income is the month's only flow.
            # elapsed_income = 3000.00, elapsed_expense = 0.00.
            assert "$3,000.00" in html
            assert "$0.00" in html

    def test_calendar_default_view_is_month(self, app, auth_client, seed_user, seed_periods):
        """No view param defaults to month view."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_invalid_month_handled(self, app, auth_client, seed_user, seed_periods):
        """Invalid month=13 clamped to valid range, no crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&month=13",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_calendar_today_highlighted(self, app, auth_client, seed_user, seed_periods):
        """Current month view contains today indicator class."""
        with app.app_context():
            from datetime import datetime, timezone
            from app.utils.dates import to_display_date
            # The route marks today in the display timezone, so the test must
            # ask for the display-tz month (not the server's UTC day) or it
            # flakes at the midnight-UTC month boundary.
            today = to_display_date(datetime.now(timezone.utc))
            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={today.year}&month={today.month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day--today" in resp.data

    def test_calendar_notation_legend(self, app, auth_client, seed_user, seed_periods):
        """Month view names its notation glyphs on screen (P-AN8).

        The PAY / asterisk / tilde / check glyphs previously relied on a
        cell tooltip for the asterisk's meaning, unreachable on touch; the
        legend under the grid states all four.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "calendar-legend" in html
            assert "payday" in html
            assert "includes an infrequent item" in html
            assert "projected (after today)" in html


class TestCalendarFlowStrip:
    """Pins for the month flow strip payload and the day-cell balance hero.

    Slice-1 rebuild behavior (analytics_audit.md, "Calendar rebuild
    decisions" + "Loop B P1 as-built"): the strip serializes the daily
    running-balance series with measured/projected split indices, the
    user's low-balance threshold, payday dots, and the labeled trough;
    day cells carry the projected end-of-day balance hero with the
    modeled-tilde and below-threshold danger treatments.
    """

    def test_flow_strip_payload_shape(self, app, auth_client, seed_user, seed_periods):
        """January 2026 with no transactions serializes a flat $1,000 series.

        Hand-computed: the seeded account anchors at $1,000.00 with no
        transactions, so every end-of-day balance is 1000.0.  January 2026
        is wholly past (display-tz today is after 2026-01-31), so
        current_index equals the day count (all measured).  Paydays are the
        seeded period starts Jan 2 / 16 / 30 (0-based indices 1, 15, 29).
        Weekly ticks are the 1st plus every Sunday: 2026-01-01 is a
        Thursday, so Sundays fall on Jan 4 / 11 / 18 / 25 (indices 3, 10,
        17, 24).  The default low-balance threshold is 500.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'id="calendar-flow-canvas"' in html

            payload = _extract_flow_strip_payload(html)
            assert len(payload["values"]) == 31
            assert all(value == 1000.0 for value in payload["values"])
            assert payload["current_index"] == 31
            assert payload["threshold"] == 500.0
            assert payload["payday_indices"] == [1, 15, 29]
            # Flat series: the trough is the earliest minimum, day 1.
            assert payload["trough_index"] == 0
            # $1,000.00 >= the $500 threshold: a healthy trough (D11,
            # P-AN6) -- "ok" state, and no state ink anywhere on the
            # board (being the trough alone no longer colors a figure).
            assert payload["trough_state"] == "ok"
            assert "calendar-day-balance--danger" not in html
            assert "calendar-day-balance--low" not in html
            assert "pulse-chip--danger" not in html
            assert "pulse-chip--warning" not in html
            assert payload["week_tick_indices"] == [0, 3, 10, 17, 24]
            assert len(payload["labels"]) == 31
            assert payload["labels"][0] == "Jan 1"

    def test_flow_strip_low_trough_warning_cells(self, app, auth_client, seed_user, seed_periods, db):
        """A low-but-positive trough renders the WARNING chip and cells.

        Hand-computed: anchor $1,000.00; one $600.00 expense SETTLED on Jan 5
        (inside period Jan 2-15).  End-of-day balances: Jan 1-4 = $1,000.00;
        Jan 5-31 = 1000 - 600 = $400.00.  The trough is Jan 5 (0-based index 4)
        at $400.00 -- below the default $500 threshold but positive, so under
        the D11 ruling (the calendar adopts the grid's thresholds: danger only
        for a NEGATIVE balance, the caution token for low-but-positive) the
        Month trough chip takes the warning variant, the below-threshold day
        cells take the low hero class, and neither danger class renders.
        January 2026 is wholly past, so no modeled tilde renders anywhere.

        **The row is SETTLED rather than projected, and that is the shape a
        past month has** (plan step X-c2b2).  A settled row moves the line from
        the day its money moved, which is finding cash D1 closed; a row still
        marked Projected in a past month has not happened, so ruling R-G lands
        it at ``as_of + 1`` and January stays flat -- the honest answer for a
        bill nobody paid, and one that pins no trough.
        """
        with app.app_context():
            from datetime import date
            from decimal import Decimal
            from tests._test_helpers import create_settled_cash_transaction

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("600.00"),
                name="Trough Expense",
                settled_on=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["trough_index"] == 4
            assert payload["trough_state"] == "low"
            assert payload["values"][3] == 1000.0
            assert payload["values"][4] == 400.0
            assert payload["values"][30] == 400.0

            assert "Month trough" in html
            assert "$400.00" in html
            assert "pulse-chip--warning" in html
            assert "calendar-day-balance--low" in html
            assert "pulse-chip--danger" not in html
            assert "calendar-day-balance--danger" not in html
            # Wholly past month: measured treatment only, no modeled tilde.
            assert "~$" not in html

    def test_flow_strip_negative_trough_danger_cells(self, app, auth_client, seed_user, seed_periods, db):
        """A negative trough renders the DANGER chip and cells.

        Hand-computed: anchor $1,000.00; one $1,600.00 expense SETTLED on
        Jan 5 (inside period Jan 2-15).  End-of-day balances: Jan 1-4
        = $1,000.00; Jan 5-31 = 1000 - 1600 = -$600.00.  The trough is
        Jan 5 (0-based index 4) at -$600.00 -- a true negative money
        state, so the Month trough chip takes the danger variant and the
        negative day cells take the danger hero class (D11: danger is
        reserved for negative).  Jan 1-4 stay healthy at $1,000.00, so
        the low/warning classes do not render.

        Settled rather than projected, for the reason the sibling test above
        documents: a past month's line moves on what actually happened.
        """
        with app.app_context():
            from datetime import date
            from decimal import Decimal
            from tests._test_helpers import create_settled_cash_transaction

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("1600.00"),
                name="Overdraft Expense",
                settled_on=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["trough_index"] == 4
            assert payload["trough_state"] == "negative"
            assert payload["values"][4] == -600.0

            assert "pulse-chip--danger" in html
            assert "calendar-day-balance--danger" in html
            assert "pulse-chip--warning" not in html
            assert "calendar-day-balance--low" not in html

    def test_future_month_modeled_treatment(self, app, auth_client, seed_user, seed_periods):
        """A wholly future month renders all-modeled heroes and no measured chips.

        Uses the display-timezone today (the route's split basis) to pick a
        month two months ahead, so the assert never straddles the boundary.
        With no transactions the series is flat at the $1,000.00 anchor:
        every day is after today, so every balance hero carries the modeled
        tilde + secondary-ink class, current_index is 0 (all projected),
        and the elapsed-side chips (Balance today / So far) do not render
        while Remaining does.
        """
        with app.app_context():
            from datetime import datetime, timezone
            from app.utils.dates import to_display_date

            today = to_display_date(datetime.now(timezone.utc))
            year = today.year + (1 if today.month >= 11 else 0)
            month = today.month - 10 if today.month >= 11 else today.month + 2

            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={year}&month={month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["current_index"] == 0

            assert "calendar-day-balance--modeled" in html
            assert "~$1,000" in html
            assert "Balance today" not in html
            assert "So far" not in html
            assert "Remaining" in html

    def test_day_cell_flow_lines_cap_and_overflow(self, app, auth_client, seed_user, seed_periods, db):
        """A five-flow day shows three named lines plus the +N more residual.

        Hand-computed: on Jan 5, one $3,000.00 income and four expenses
        ($1,200 / $300 / $100 / $50).  Cell order is income first then
        expenses by descending magnitude, capped at three named lines
        (Salary Chunk, Rent Big, Utility Mid), so exactly three
        calendar-day-flow lines render.  The hidden tail is Snack Small
        ($100) + Tiny Fee ($50): count 2, residual net -(100 + 50) =
        -$150, rendered whole-dollar as "-$150".
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            flows = [
                ("Salary Chunk", TxnTypeEnum.INCOME, Decimal("3000.00")),
                ("Rent Big", TxnTypeEnum.EXPENSE, Decimal("1200.00")),
                ("Utility Mid", TxnTypeEnum.EXPENSE, Decimal("300.00")),
                ("Snack Small", TxnTypeEnum.EXPENSE, Decimal("100.00")),
                ("Tiny Fee", TxnTypeEnum.EXPENSE, Decimal("50.00")),
            ]
            for name, txn_type, amount in flows:
                one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=amount,
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=ref_cache.txn_type_id(txn_type),
                    due_date=date(2026, 1, 5),
                )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Exactly three named flow lines on the one populated day.
            assert html.count('class="calendar-day-flow"') == 3
            assert "+2 more" in html
            assert "-$150" in html

    def test_paycheck_day_pay_tag(self, app, auth_client, seed_user, seed_periods):
        """Payday cells carry the PAY tag marker alongside the period tint."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "calendar-pay-tag" in html
            assert ">PAY</span>" in html
