"""The per-day filter's balance-contributing predicate (F-3 / W-065), and the anchor-None contract
for an unresolvable account or scenario (F-2 / Commit 11).
"""

from datetime import date
from decimal import Decimal

from app.services.cash_flow_set import CashFlowSet
from app.exceptions import BaselineMissingError
from app.enums import StatusEnum

import pytest

from app.services import balance_at, calendar_service
from app.services.balance_at import BalanceContext
from app.services.balance_at import _context as resolution_context
from app.services.calendar_service import CalendarAccountNotResolvableError

from ._helpers import _add_transaction


# ── F-3 / W-065 balance-contributing predicate ─────────────────────


class TestBalanceContributingPredicate:
    """F-3 / HIGH-02 / W-065: calendar per-day filter via the locked
    Choice-2 ``balance-contributing`` predicate (Projected + settled,
    excludes Cancelled + Credit).

    Locks the post-Commit-10 (follow-up) behaviour so a future change
    that drops the predicate from the SQL filter in
    ``calendar_items.query_transactions_for_range`` fails loud with a concrete
    arithmetic divergence rather than a silent display regression.  C10-2
    sees that directly since ruling plan#2262 deleted the Python re-check in
    ``_assign_transactions_to_days``, which hid the SQL filter from it; C10-4,
    which stubbed both statements of the predicate to see it, went with the
    re-check, as did its leg twin in ``TestATransferIsALegOfItsParent``.
    """

    def test_c10_1_projected_and_settled_both_contribute(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-1: Projected $500 + Paid $200 -> day total $700.

        Hand arithmetic: 500 (Projected expense, effective = estimated)
        + 200 (Paid expense, effective = actual_amount) = 700.
        Both statuses are balance-contributing: Projected because it
        is not in the {Credit, Cancelled} exclusion set; Paid for
        the same reason -- the calendar's locked Choice-2 predicate
        intentionally includes realized payments at their settled date.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # F-3 / W-065: 500 + 200 = 700.00 (both contribute).
            assert result.total_expenses == Decimal("700.00")
            assert len(result.day_entries[5]) == 2
            names = sorted(e.name for e in result.day_entries[5])
            assert names == ["Paid Bill", "Projected Bill"]

    def test_c10_2_cancelled_and_credit_excluded(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-2: Cancelled + Credit excluded from day total.

        Same day as C10-1 plus a Cancelled $100 expense and a Credit
        $50 expense.  Hand arithmetic: 500 (Projected) + 200 (Paid)
        = 700.00; the Cancelled and Credit rows are filtered out by
        ``balance_contributing_clause`` (their status carries
        ``excludes_from_balance=True``) and never reach the day
        assignment, so they neither inflate totals nor appear as
        day entries.

        A pre-Commit-10-follow-up calendar would have included all
        four rows in ``day_entries[5]``; their amount contribution
        collapses to zero via ``effective_amount`` so totals stay at
        700.00, but the visible-entries regression is the user-facing
        defect F-3 names.  This test locks both the arithmetic AND
        the entry-count contract.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # F-3 / W-065: 500 + 200 = 700.00; Cancelled + Credit excluded.
            assert result.total_expenses == Decimal("700.00")
            # Day cell shows only the two contributing rows.
            assert len(result.day_entries[5]) == 2
            names = sorted(e.name for e in result.day_entries[5])
            assert names == ["Paid Bill", "Projected Bill"]

    def test_c10_3_grid_period_subtotal_excludes_cancelled_and_credit(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-3: Cancelled and Credit never reach the grid column.

        Same fixture as C10-2 (Projected $500 + Paid $200 +
        Cancelled $100 + Credit $50 on Jan 5).

        **Ruling R-K changed what a subtotal COUNTS, and this test's figure
        moved with it** (plan step X-c2b2; the read moved to the shipped
        ``GridColumn`` when plan step X-c2b3 deleted
        ``cash_ledger.period_subtotal``).  The retired subtotal was
        Projected-ONLY -- it gated every row through ``is_projected``, so a row
        that had actually been PAID contributed nothing and a past column read
        ``$0.00`` while thousands of dollars moved through it (finding N-41).
        The grid column now counts every row attributed to the period: a settled
        row at its confirmed cash leg, a projected row at its entries-aware
        reservation.

        Hand arithmetic on the new basis:

            Projected $500, no entries -> reservation      500.00
            Settled $200 (actual 200.00), no credit entries
              -> its covering movement                    200.00
            Cancelled $100 -> neither projected nor settled  0.00
            Credit $50     -> neither projected nor settled  0.00
                                                          -------
            expense                                        700.00

        So the exclusion this test locks is the CANCELLED / CREDIT pair, which
        R-K did not touch: they are excluded from the projected half by
        ``is_projected`` and from the settled half by the settled-status
        narrowing in SQL.  The old "locked Choice-2 divergence" between the
        calendar day total (700.00, C10-2) and this column is gone -- both count
        the settled row now.  That agreement is this FIXTURE's, not a general
        identity: every row here sits in one period on one day, while the
        calendar places a chip on its budget attribution date and steps its
        balance on the day the money moved (finding N-58).
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            column = balance_at.grid_balance_view(
                CashFlowSet.single(seed_user["account"]),
                BalanceContext.build(seed_user["user"].id),
            ).columns[p0.id]
            # 500.00 reservation + 200.00 confirmed cash leg; the Cancelled
            # $100 and the Credit $50 contribute nothing to either half.
            assert column.expense == Decimal("700.00")
            assert column.income == Decimal("0.00")


# ── Anchor-None contract (F-2 / Commit 11) ──────────────────────────


class TestUnresolvableAccountOrScenario:
    """Tests for the F-2 / Commit 11 contract.

    After Commits 3-8 of the main remediation locked the E-19 /
    CRIT-01 invariant, the calendar service must raise
    :class:`CalendarAccountNotResolvableError` when
    :func:`resolve_analytics_cash_flow_set` (``resolve_analytics_account``
    until plan step CC-4-3) returns ``None`` -- the pre-F-2
    behaviour of silently substituting a zeroed
    :class:`MonthSummary` / :class:`YearOverview` masked the
    upstream defect behind a ``$0.00`` calendar.

    **The no-BASELINE half of that contract moved at plan step X-v2** (ruling
    R-BW).  Both conditions used to raise this one exception, and the route
    turned both into a 404; they are two different problems (a deleted
    analytics account, versus a repairable missing baseline) and they get two
    different answers now.  The baseline half raises
    :class:`~app.exceptions.BaselineMissingError` from the seam, which one
    application-level handler answers with the repair card -- asserted below,
    and end-to-end in ``tests/test_routes/test_no_baseline_policy/``.
    """

    def test_month_detail_raises_when_account_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-1 (service): None account -> CalendarAccountNotResolvableError."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "resolve_analytics_cash_flow_set",
                lambda _user_id, _user_settings, _account_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id,
                    year=2026,
                    month=1,
                    user_settings=None,
                )

    def test_month_detail_raises_when_scenario_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """A None baseline raises the SEAM's named exception, not this module's."""
        with app.app_context():
            # The baseline scenario is now resolved inside the balance context,
            # so that is where an unresolvable baseline is simulated.
            monkeypatch.setattr(
                resolution_context, "get_baseline_scenario",
                lambda _user_id: None,
            )
            # The seam's own named exception, NOT the calendar's: a missing
            # baseline is answered by one application-level handler, and a
            # producer that translated it into its own error is what made this
            # state 404 here while it 500'd on the loan page (ruling R-BW).
            with pytest.raises(BaselineMissingError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id,
                    year=2026,
                    month=1,
                    user_settings=None,
                )

    def test_year_overview_raises_when_account_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-1 (service, year view): None account -> error."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "resolve_analytics_cash_flow_set",
                lambda _user_id, _user_settings, _account_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                    user_settings=None,
                )

    def test_year_overview_raises_when_scenario_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-2 (service, year view): None scenario -> error."""
        with app.app_context():
            # The baseline scenario is now resolved inside the balance context,
            # so that is where an unresolvable baseline is simulated.
            monkeypatch.setattr(
                resolution_context, "get_baseline_scenario",
                lambda _user_id: None,
            )
            # The seam's own named exception, NOT the calendar's: a missing
            # baseline is answered by one application-level handler, and a
            # producer that translated it into its own error is what made this
            # state 404 here while it 500'd on the loan page (ruling R-BW).
            with pytest.raises(BaselineMissingError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                    user_settings=None,
                )
