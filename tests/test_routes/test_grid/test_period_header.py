"""Grid route tests: the period header's date format, and the tooltip content."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import Status, TransactionType

from tests._test_helpers import (
    cover_bare_settled_row,
    current_pay_period,
    derived_span,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    settle_day_columns,
)


class TestPeriodHeaderDateFormat:
    """Tests for pay period date headers -- Commit #14.

    Headers show only the paycheck date (start_date), not the period range.
    Current-year periods omit the year suffix (e.g., '3/26').
    Non-current-year periods include 2-digit year (e.g., '3/26/27').
    """

    def _make_periods(self, db, seed_user, start_date, num_periods=6):
        """Helper: generate pay periods and set anchor to the first one."""
        periods = record_paydays_across_a_hole(
            user_id=seed_user["user"].id,
            first_payday=start_date,
            num_periods=num_periods,
            rhythm=rhythm_of(14),
        )
        db.session.flush()
        db.session.commit()
        return periods

    def test_period_header_compact_for_current_year(self, app, auth_client, seed_user):
        """Current-year periods display paycheck date without year suffix.

        The grid starts at the current period (the one containing today),
        so we must check the current period's header, not the first
        generated period.
        """
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            self._make_periods(db, seed_user, start)

            # The grid starts at the current period -- find it.
            current = current_pay_period(
                seed_user["user"].id
            )
            assert current is not None, "No period covers today"

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The current period's start_date should appear in compact
            # format (no year suffix) since it's in the current year.
            expected = current.start_date.strftime("%-m/%-d")
            assert expected in html
            # Should NOT contain a range separator IN THE HEADER.  The scope
            # matters and this assertion did not have it: the mobile
            # "this period" card renders ``period.label`` -- a real range --
            # by design (``grid/_mobile_this_period.html``), so searching the
            # whole document asserts something the app never promised.
            #
            # It passed anyway, by a FORMATTING COINCIDENCE.  ``label`` is
            # zero-padded (``%m/%d``) while this built the string unpadded
            # (``%-m/%-d``), and the two are identical only when the month and
            # the day are both >= 10 -- so the assertion fired for the first
            # time on 2026-11-30 and would fire on roughly sixty days a year,
            # every one of them in October, November or December.
            #
            # The class's contract is about the period HEADER, which is inside
            # the table head (``grid/grid.html`` thead) and renders
            # ``start_date`` alone.  Scope the search there and the assertion
            # says what it means.
            head = html[html.index("<thead"):html.index("</thead>")]
            end = current.start_date + timedelta(days=13)
            range_str = f"{current.start_date.strftime('%-m/%-d')} - {end.strftime('%-m/%-d')}"
            assert range_str not in head
            padded_range = (
                f"{current.start_date.strftime('%m/%d')} - "
                f"{end.strftime('%m/%d')}"
            )
            assert padded_range not in head

    def test_period_header_full_format_for_cross_year(self, app, auth_client, seed_user):
        """A period starting in a non-current year shows the year suffix."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            # Generate enough periods to REACH the next calendar year on every
            # day of the year, rather than assuming a fixed count does.  A flat
            # ``num_periods=28`` spans ~13 months, which crosses a year boundary
            # from most starting points but NOT from early January: on
            # 2027-01-01 the run ends 2027-12-30 and the guard below fires with
            # "Test requires a next-year period" -- a red suite on New Year's
            # Day.  Covering through mid-January of next year makes the
            # precondition hold whatever the calendar says.
            target = date(today.year + 1, 1, 15)
            num_periods = ((target - start).days // 14) + 2
            periods = self._make_periods(
                db, seed_user, start, num_periods=num_periods,
            )

            # Find a period whose start_date is in the next year.
            next_year_period = None
            for p in periods:
                if p.start_date.year > today.year:
                    next_year_period = p
                    break

            assert next_year_period is not None, "Test requires a next-year period"

            current_period = current_pay_period(
                seed_user["user"].id
            )
            offset = derived_span(next_year_period).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Expect paycheck date with year suffix.
            expected = next_year_period.start_date.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_full_format_for_past_year(self, app, auth_client, seed_user):
        """Periods in the previous year show the year suffix."""
        with app.app_context():
            today = date.today()
            past_start = date(today.year - 1, 6, 1)
            days_to_today = (today - past_start).days
            num = (days_to_today // 14) + 4
            periods = self._make_periods(db, seed_user, past_start, num_periods=num)

            current_period = current_pay_period(
                seed_user["user"].id
            )
            first_offset = derived_span(periods[0]).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=3&offset={first_offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = past_start.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_full_format_for_future_year(self, app, auth_client, seed_user):
        """Periods in the next year show the year suffix."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            days_to_next_year = (date(today.year + 1, 2, 1) - start).days
            num = (days_to_next_year // 14) + 2
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            future_period = None
            for p in periods:
                if p.start_date.year > today.year:
                    future_period = p
                    break

            assert future_period is not None, "Test requires a next-year period"

            current_period = current_pay_period(
                seed_user["user"].id
            )
            offset = derived_span(future_period).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = future_period.start_date.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_mixed_formats_same_page(self, app, auth_client, seed_user):
        """Current-year and non-current-year headers coexist on the same page."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            days_to_next_year = (date(today.year + 1, 2, 1) - start).days
            num = (days_to_next_year // 14) + 2
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            # Find the last current-year period and first next-year period.
            last_current = None
            first_next = None
            for p in periods:
                if p.start_date.year == today.year:
                    last_current = p
                if first_next is None and p.start_date.year > today.year:
                    first_next = p

            assert last_current is not None
            assert first_next is not None

            current_period = current_pay_period(
                seed_user["user"].id
            )
            offset = derived_span(last_current).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=6&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Current-year: paycheck date without year.
            compact = last_current.start_date.strftime("%-m/%-d")
            assert compact in html

            # Next-year: paycheck date with year suffix.
            full = first_next.start_date.strftime("%-m/%-d/%y")
            assert full in html

    def test_carry_forward_button_still_present_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Carry forward button renders correctly alongside the new date format."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=56)
            periods = self._make_periods(db, seed_user, start)

            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            first_cat = list(seed_user["categories"].values())[0]
            one_off_row_of(
                periods[0],
                name="Test Bill",
                amount=Decimal("100.00"),
                user_id=periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=first_cat.id,
            )
            db.session.commit()

            current_period = current_pay_period(
                seed_user["user"].id
            )
            offset = derived_span(periods[0]).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=6&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Carry forward button present.
            assert "carry-forward" in html
            # Paycheck date without year (current year period).
            expected = periods[0].start_date.strftime("%-m/%-d")
            assert expected in html

    def test_grid_renders_without_error_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Smoke test: grid renders with correct table structure after date format change."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=14)
            self._make_periods(db, seed_user, start)

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "<thead" in html
            assert "<tbody>" in html
            assert "Projected End Balance" in html

    def test_balance_row_still_works_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Balance row HTMX partial still renders after the thead date change."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=14)
            self._make_periods(db, seed_user, start)

            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 200
            assert b'id="grid-summary"' in resp.data

    def test_period_header_handles_january_1st(self, app, auth_client, seed_user):
        """A period starting January 1 of the current year uses compact format."""
        with app.app_context():
            today = date.today()
            jan1 = date(today.year, 1, 1)
            days_to_today = (today - jan1).days
            num = max((days_to_today // 14) + 4, 6)
            periods = self._make_periods(db, seed_user, jan1, num_periods=num)

            current_period = current_pay_period(
                seed_user["user"].id
            )
            offset = derived_span(periods[0]).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Jan 1 in current year -- no year suffix.
            assert "1/1" in html

    def test_period_header_handles_december_31st(self, app, auth_client, seed_user):
        """A late-December period in the current year uses compact format."""
        with app.app_context():
            today = date.today()
            dec18 = date(today.year, 12, 18)
            start = today - timedelta(days=14)
            days_to_dec18 = (dec18 - start).days
            num = (days_to_dec18 // 14) + 4
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            # Find the last period starting in the current year.
            dec_period = None
            for p in periods:
                if p.start_date.year == today.year:
                    dec_period = p

            if dec_period is None:
                pytest.skip("No period starting in late current year generated")

            current_period = current_pay_period(
                seed_user["user"].id
            )
            offset = derived_span(dec_period).period_index - derived_span(current_period).period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Current-year start date -- no year suffix.
            expected = dec_period.start_date.strftime("%-m/%-d")
            assert expected in html

            # The next period (if it exists and starts next year) shows year.
            next_periods = [
                p for p in periods
                if derived_span(p).period_index == derived_span(dec_period).period_index + 1
            ]
            if next_periods and next_periods[0].start_date.year > today.year:
                full = next_periods[0].start_date.strftime("%-m/%-d/%y")
                assert full in html


class TestTooltipContent:
    """Tests for Commit #16: enhanced transaction cell tooltips.

    The tooltip now shows full dollar amounts with cents, actual-vs-estimated
    comparison, status labels, and notes.  The transaction name is no longer
    in the tooltip (it moved to the row header in Commit #15).
    """

    def _get_current_period(self, seed_user):
        """Return the current period for the seed user."""
        return current_pay_period(seed_user["user"].id)

    @staticmethod
    def _extract_txn_titles(html):
        """Extract title attribute values from the grid's transaction cells.

        The C3 rebuild (2026-06-11, docs/design/grid_audit.md "Rebuild
        decisions") moved the tooltip from the old ``div.txn-cell``
        wrapper onto the chip's ``span.txn-open`` click target; the
        tooltip text itself is unchanged, so every assertion in this
        class still checks the same content.
        """
        import re
        return re.findall(
            r'<span class="txn-open"[^>]*title="([^"]*)"', html,
        )

    def test_tooltip_contains_full_amount_with_cents(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip shows the full dollar amount with two decimal places,
        including comma-separated thousands (e.g. $1,234.56).
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Test Tooltip Amount",
                amount=Decimal("1234.56"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$1,234.56" in t]
            assert matching, f"Expected tooltip with $1,234.56, got: {titles}"
            # Should NOT show the rounded amount as the primary tooltip content.
            assert not any(
                t.startswith("$1,235 ") or t == "$1,235" for t in titles
            ), "Tooltip should show cents, not rounded amount"

    def test_tooltip_shows_actual_vs_estimated_when_different(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When actual_amount differs from estimated_amount, the tooltip shows
        both: '$487.32 (est: $500.00)'.
        """
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Test Est Comparison",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = paid.id
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.flush()
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, "500.00", submitted="487.32")
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$487.32" in t and "(est: $500.00)" in t]
            assert matching, f"Expected '$487.32 (est: $500.00)', got: {titles}"

    def test_tooltip_hides_estimate_when_amounts_equal(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When actual_amount equals estimated_amount, the tooltip shows only
        the amount without the '(est: ...)' comparison.
        """
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Test Equal Amounts",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = paid.id
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.flush()
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, "500.00", submitted="500.00")
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$500.00" in t]
            assert matching, f"Expected tooltip with $500.00, got: {titles}"
            assert not any("(est:" in t for t in matching), (
                "Tooltip should not show '(est:' when amounts are equal"
            )

    def test_tooltip_includes_paid_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes '-- Paid' for transactions with Paid status."""
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Test Paid Status",
                amount=Decimal("100.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = paid.id
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.flush()
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, "100.00", submitted="100.00")
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Paid" in t]
            assert matching, f"Expected tooltip with '-- Paid', got: {titles}"

    def test_tooltip_includes_projected_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes '-- Projected' for projected transactions."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Test Projected Status",
                amount=Decimal("75.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Projected" in t]
            assert matching, f"Expected tooltip with '-- Projected', got: {titles}"

    def test_tooltip_includes_notes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes notes when present on the transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Test Notes Tooltip",
                amount=Decimal("50.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            txn.notes = "Auto-pay on the 15th"
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Auto-pay on the 15th" in t]
            assert matching, f"Expected notes in tooltip, got: {titles}"

    def test_tooltip_no_trailing_separator_when_no_notes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When notes are empty/None, the tooltip does not have a trailing
        '-- ' separator with nothing after it.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Test No Trailing Sep",
                amount=Decimal("200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$200.00" in t]
            assert matching, f"Expected tooltip with $200.00, got: {titles}"
            for title in matching:
                assert not title.endswith("-- "), (
                    f"Tooltip has trailing separator: '{title}'"
                )
                assert not title.endswith("--"), (
                    f"Tooltip has trailing separator: '{title}'"
                )

    def test_tooltip_handles_zero_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip renders $0.00 correctly for a zero-amount transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Test Zero Amount",
                amount=Decimal("0.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$0.00" in t]
            assert matching, f"Expected tooltip with $0.00, got: {titles}"

    def test_tooltip_handles_large_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip formats large amounts with comma-separated thousands."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Test Large Amount",
                amount=Decimal("12345.67"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$12,345.67" in t]
            assert matching, f"Expected tooltip with $12,345.67, got: {titles}"

    def test_tooltip_credit_transaction_shows_charged_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit transactions show the estimated (charged) amount in the
        tooltip, not $0.00 from effective_amount.  Also includes '-- Credit'.
        """
        with app.app_context():
            credit = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Test Credit Tooltip",
                amount=Decimal("200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            txn.status_id = credit.id
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$200.00" in t and "-- Credit" in t]
            assert matching, (
                f"Expected tooltip with $200.00 and '-- Credit', got: {titles}"
            )
            # Must NOT show $0.00 (which is what effective_amount returns).
            assert not any("$0.00" in t and "-- Credit" in t for t in titles), (
                "Credit tooltip should show charged amount, not $0.00"
            )

    def test_tooltip_survives_htmx_cell_update(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """After a PATCH update via quick edit, the re-rendered cell includes
        a title attribute with the updated amount (server-side rendering).
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Test HTMX Update",
                amount=Decimal("80.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # PATCH the amount.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "estimated_amount": "95.50",
                    "estimated_amount_as_rendered": "80.00",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # The re-rendered cell should have the updated amount in the title.
            assert "$95.50" in html, (
                f"Expected $95.50 in PATCH response title, got: {html[:500]}"
            )

    def test_tooltip_no_redundant_name(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The tooltip does NOT contain the transaction name (it moved to
        the row header in Commit #15).
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="State Farm",
                amount=Decimal("150.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$150.00" in t]
            assert matching, f"Expected tooltip with $150.00, got: {titles}"
            for title in matching:
                assert "State Farm" not in title, (
                    f"Tooltip should not contain transaction name, got: '{title}'"
                )
