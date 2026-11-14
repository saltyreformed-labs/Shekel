"""The money each section shows: a definition's monthly and per-paycheck equivalents, the section
subtotals against the canonical aggregator, the rule-less and expired rows shown blank and
counted in no total, the summary band of income against committed outflow, and each row's share
in the cost-descending default order.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, ANNUAL
from app.models.transaction_template import TransactionTemplate
from app.services import recurring_view
from app.services.obligations_aggregator import committed_monthly

from ._helpers import _ctx, _create_expense, _create_income, _create_savings, _create_transfer


# ── Both-units equivalents ───────────────────────────────────────────


class TestUnitEquivalents:
    """Monthly and per-paycheck equivalents for each cadence."""

    def test_biweekly_both_units(self, seed_user, seed_periods_today):
        """A $100 every-paycheck expense: monthly = 100 * 26 / 12 = $216.67,
        per-paycheck = that monthly re-expressed = exactly $100.00.
        """
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        # Hand-computed at 26 paychecks a year: 100 * 26 / 12 = 216.6667 ->
        # 216.67, and 216.6667 * 12 / 26 collapses back to the original 100.00.
        assert row.equivalent.monthly == Decimal("216.67")
        assert row.equivalent.per_paycheck == Decimal("100.00")

    def test_monthly_both_units(self, seed_user, seed_periods_today):
        """A $500 monthly expense: monthly = $500.00,
        per-paycheck = 500 * 12 / 26 = 230.7692... -> $230.77.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("500.00"), day_of_month=15)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        # Hand-computed: 500 * 12 / 26 = 230.7692... -> 230.77.
        assert row.equivalent.monthly == Decimal("500.00")
        assert row.equivalent.per_paycheck == Decimal("230.77")

    def test_annual_both_units(self, seed_user, seed_periods_today):
        """A $1,200 annual expense: monthly = 1200 / 12 = $100.00,
        per-paycheck = 1200 / 26 = 46.1538... -> $46.15.
        """
        tmpl = _create_expense(seed_user, ANNUAL, Decimal("1200.00"), day_of_month=1, month_of_year=6)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        # Hand-computed: 1200 / 12 = 100.00 a month; 100 * 12 / 26 =
        # 46.1538... -> 46.15 a paycheck.
        assert row.equivalent.monthly == Decimal("100.00")
        assert row.equivalent.per_paycheck == Decimal("46.15")


# ── Subtotals and the aggregator SSOT ────────────────────────────────


class TestSubtotals:
    """Section subtotals stay identical to the canonical aggregator."""

    def test_subtotal_matches_committed_monthly(
        self, seed_user, seed_periods_today,
    ):
        """The expense section's monthly subtotal equals
        ``committed_monthly`` for the same templates, so the unified surface
        and /savings can never disagree.

        $100 biweekly (216.67) + $500 monthly (500.00) = $716.67.
        """
        e1 = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="A")
        e2 = _create_expense(seed_user, MONTHLY, Decimal("500.00"), name="B", day_of_month=15)
        as_of = date.today()

        ctx = _ctx(seed_user, as_of)
        view = recurring_view.build_view([], [e1, e2], [], ctx)
        assert view.expenses.subtotal.monthly == Decimal("716.67")
        assert view.expenses.subtotal.monthly == committed_monthly(
            [e1, e2], ctx,
        )

    def test_subtotal_per_paycheck_derives_from_monthly(
        self, seed_user, seed_periods_today,
    ):
        """The per-paycheck subtotal is the full-precision monthly total
        re-expressed per paycheck.

        Full monthly total = 100*26/12 + 500 = 716.6667;
        per-paycheck = 716.6667 * 12 / 26 = 330.769... -> $330.77.
        """
        e1 = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="A")
        e2 = _create_expense(seed_user, MONTHLY, Decimal("500.00"), name="B", day_of_month=15)

        view = recurring_view.build_view(
            [], [e1, e2], [], _ctx(seed_user),
        )
        # Hand-computed: (100 * 26 / 12) + 500 = 716.6667 a month at full
        # precision, then * 12 / 26 = 330.7692... -> 330.77 a paycheck.
        assert view.expenses.subtotal.per_paycheck == Decimal("330.77")

    def test_empty_section_subtotal_is_zero(self, seed_user, seed_periods_today):
        """A section with no templates subtotals to $0.00 in both units."""
        view = recurring_view.build_view(
            [], [], [], _ctx(seed_user),
        )
        assert view.expenses.rows == ()
        assert view.expenses.subtotal.monthly == Decimal("0.00")
        assert view.expenses.subtotal.per_paycheck == Decimal("0.00")


# ── Non-recurring rows: present but blank, excluded from totals ───────


class TestNonRecurringRows:
    """Non-repeating / expired definitions show as manageable rows
    but contribute nothing to any total (the management surface shows all
    active definitions; the totals are the /obligations kernel).

    "Does not repeat" is ``recurrence_rule_id IS NULL`` on both template
    kinds since plan step R2e-3.  These cases named a ``Once``-PATTERN rule
    before it, which is the second spelling that step removed."""

    def test_non_repeating_row_present_but_blank(
        self, seed_user, seed_periods_today,
    ):
        """A rule-less expense appears as a row with a blank equivalent
        and no next date, and adds $0 to the subtotal.
        """
        once = _create_expense(seed_user, None, Decimal("999.00"), name="OneTime")
        real = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="Real")

        view = recurring_view.build_view(
            [], [once, real], [], _ctx(seed_user),
        )
        names = {row.template.name: row for row in view.expenses.rows}
        assert "OneTime" in names, "one-time definition must still be listed"
        once_row = names["OneTime"]
        assert once_row.equivalent.monthly is None
        assert once_row.equivalent.per_paycheck is None
        assert once_row.next_date is None
        assert once_row.share_pct is None
        # Only the real recurring expense counts toward the subtotal.
        # 100.00 * 26 / 12 = 216.666... -> 216.67
        assert view.expenses.subtotal.monthly == Decimal("216.67")

    def test_non_repeating_row_logs_no_unknown_pattern_warning(
        self, seed_user, seed_periods_today, caplog,
    ):
        """A rule-less definition emits no 'unknown pattern' warning.

        ``_next_occurrence`` returns on the rule-less branch before reaching
        the reverse matcher, which logged that warning for any pattern it had no
        branch for.  Until plan step R2e-3 a second guard was needed beside it
        for the ``Once`` pattern, which the matcher also had no branch
        for -- so a one-time definition logged it on EVERY render without one.
        """
        once = _create_expense(seed_user, None, Decimal("999.00"), name="OneTime")

        with caplog.at_level(logging.WARNING):
            recurring_view.build_view(
                [], [once], [], _ctx(seed_user),
            )
        assert "Unknown recurrence pattern" not in caplog.text

    def test_no_rule_row_present_but_blank(self, seed_user, seed_periods_today):
        """A template with no recurrence rule appears with a blank equivalent."""
        tmpl = _create_expense(seed_user, None, Decimal("42.00"), name="NoRule")

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        assert row.template.name == "NoRule"
        assert row.equivalent.monthly is None
        assert row.next_date is None
        assert view.expenses.subtotal.monthly == Decimal("0.00")

    def test_expired_row_present_but_blank(self, seed_user, seed_periods_today):
        """An active template whose rule.end_date is in the past appears as
        a manageable row with a blank equivalent and no next date, and adds
        nothing to the subtotal (it is no longer a future commitment).
        """
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("1500.00"), name="Expired", end_date=date.today() - timedelta(days=1))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        assert row.template.name == "Expired"
        assert row.equivalent.monthly is None
        assert row.next_date is None
        assert view.expenses.subtotal.monthly == Decimal("0.00")


# ── Summary band ─────────────────────────────────────────────────────


class TestSummaryBand:
    """The obligations-kernel band: income vs committed outflow."""

    def test_band_net_and_pct(self, seed_user, seed_periods_today):
        """Income $1,500 biweekly, expense $100 biweekly, transfer $500 monthly.

        income monthly   = 1500 * 26 / 12 = 3250.00
        expense monthly  = 100 * 26 / 12  = 216.67
        transfer monthly = 500.00
        net monthly      = 3250.00 - 216.67 - 500.00 = 2533.33
        expenses % income = 216.67 / 3250.00 * 100 = 6.6667 -> 6.7
        net per-paycheck = 1500.00 - 100.00 - 230.77 = 1169.23
        """
        savings = _create_savings(seed_user)
        income = _create_income(seed_user, EVERY_PERIOD, Decimal("1500.00"))
        expense = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"))
        transfer = _create_transfer(
            seed_user, MONTHLY, Decimal("500.00"), savings, day_of_month=1,
        )

        view = recurring_view.build_view(
            [income], [expense], [transfer], _ctx(seed_user),
        )
        band = view.band
        assert band.income.monthly == Decimal("3250.00")
        assert band.expenses.monthly == Decimal("216.67")
        assert band.transfers_out.monthly == Decimal("500.00")
        assert band.net.monthly == Decimal("2533.33")
        assert band.expenses_pct_of_income == Decimal("6.7")
        # per-paycheck net from the rounded subtotals: 1500 - 100 - 230.77.
        assert band.net.per_paycheck == Decimal("1169.23")

    def test_pct_of_income_none_without_income(
        self, seed_user, seed_periods_today,
    ):
        """With no income, the expenses-percent-of-income chip is None."""
        expense = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [expense], [], _ctx(seed_user),
        )
        assert view.band.expenses_pct_of_income is None
        assert view.band.income.monthly == Decimal("0.00")

    def test_empty_band(self, seed_user, seed_periods_today):
        """No definitions: every band figure is $0.00 and the pct is None."""
        view = recurring_view.build_view(
            [], [], [], _ctx(seed_user),
        )
        assert view.band.net.monthly == Decimal("0.00")
        assert view.band.net.per_paycheck == Decimal("0.00")
        assert view.band.expenses_pct_of_income is None


# ── Share of committed and default ordering ──────────────────────────


class TestSharesAndOrdering:
    """Per-row share bars and the cost-descending default order."""

    def test_share_pct(self, seed_user, seed_periods_today):
        """Two expenses: $100 biweekly (216.67) and $500 monthly (500.00),
        section total 716.6667.

        share A = 216.6667 / 716.6667 * 100 = 30.2326 -> 30.2
        share B = 500 / 716.6667 * 100      = 69.7674 -> 69.8
        """
        _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="Small")
        _create_expense(seed_user, MONTHLY, Decimal("500.00"), name="Big", day_of_month=15)

        view = recurring_view.build_view(
            [],
            _load_expenses(seed_user),
            [],
            _ctx(seed_user),
        )
        by_name = {row.template.name: row for row in view.expenses.rows}
        assert by_name["Small"].share_pct == Decimal("30.2")
        assert by_name["Big"].share_pct == Decimal("69.8")

    def test_rows_sorted_by_monthly_desc_then_nonrecurring_last(
        self, seed_user, seed_periods_today,
    ):
        """Rows land in monthly-cost-descending order, non-repeating last.

        The last row's amount (999.00) is the LARGEST, so ordering by amount
        would put it first; it sorts last because a rule-less definition has
        no monthly equivalent at all.
        """
        _create_expense(seed_user, MONTHLY, Decimal("300.00"), name="Mid", day_of_month=1)
        _create_expense(seed_user, MONTHLY, Decimal("900.00"), name="High", day_of_month=1)
        _create_expense(seed_user, MONTHLY, Decimal("100.00"), name="Low", day_of_month=1)
        _create_expense(seed_user, None, Decimal("999.00"), name="Once")

        view = recurring_view.build_view(
            [], _load_expenses(seed_user), [], _ctx(seed_user),
        )
        order = [row.template.name for row in view.expenses.rows]
        assert order == ["High", "Mid", "Low", "Once"]


# ── Module-local loader (mirrors the route's active-template load) ────


def _load_expenses(seed_user):
    """Return the seed user's active expense templates ordered like the route.

    The producer applies the cost-descending default itself, so the incoming
    order only fixes the tie-break among non-recurring rows; sort by name for
    a deterministic starting point.
    """
    expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    return (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == seed_user["user"].id,
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.transaction_type_id == expense_id,
        )
        .order_by(TransactionTemplate.name)
        .all()
    )
