"""The report's scope and contracts, the owner's cash-flow set it reads, and a settled transfer
drawn as a leg of its parent.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import ref_cache
from app.exceptions import AmountUnresolvable
from app.enums import StatusEnum
from app.services import spending_analysis, transfer_service
from app.services.cash_flow_set import AttributionSpan, CashFlowSet, Paychecks
from app.services.cash_ledger import derived_amount_basis, resolve_amount
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import cell_key, leg_of, parent_of
from app.services.spending_report_service import SpendingWindow, compute_spending_report
from app.services.spending_report_service._surprises import _build_surprises
from app.services.spending_report_service._window import _CHART_WINDOW_COUNT
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    last_covered_day,
    set_default_grid_account,
)

from ._helpers import _txn, _pp_window


# ── Scope facts, empty window, and the None contract ────────────────


class TestScopeAndContracts:
    """Scope labels, the empty-window shape, and the no-account None."""

    def test_scope_facts(self, app, seed_user, seed_periods, db):
        """The report carries the checking account label and settled basis."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.scope.account_id == seed_user["account"].id
            assert report.scope.account_name == "Checking"
            assert report.scope.settled_only is True
            assert report.scope.window_label  # non-empty pay-period label

    def test_empty_window_shape(self, app, seed_user, seed_periods, db):
        """A resolvable window with no settled spend is a zeroed report.

        Not None: an empty breakdown, empty change rows, a zero spent total,
        an empty surprises list with a zero net, no payment timing, and a
        full-length series whose viewed point is a real zero (the window
        exists; it just has no settled spend).
        """
        with app.app_context():
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report is not None
            assert report.breakdown == []
            assert report.changes == []
            assert report.hero.spent_total == Decimal("0")
            assert report.surprises.rows == []
            assert report.surprises.net == Decimal("0")
            assert report.hero.payment_timing is None
            assert len(report.series) == (
                _CHART_WINDOW_COUNT
            )
            assert report.series[-1].total == Decimal("0")

    def test_none_when_no_active_checking(self, app, seed_user, seed_periods):
        """No resolvable checking account -> None (empty state, not a crash)."""
        with app.app_context():
            with patch(
                "app.services.spending_report_service.resolve_cash_flow_set",
                return_value=None,
            ):
                report = compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[0]),
                    user_settings=None,
                )
            assert report is None

    def test_none_when_no_baseline_scenario(self, app, seed_user, seed_periods):
        """No baseline scenario -> None (empty state, not a crash)."""
        with app.app_context():
            with patch(
                "app.services.spending_report_service.get_baseline_scenario",
                return_value=None,
            ):
                report = compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[0]),
                    user_settings=None,
                )
            assert report is None

    def test_invalid_window_raises(self, app, seed_user):
        """An unknown window type raises ValueError (shared validator)."""
        import pytest
        with app.app_context():
            with pytest.raises(ValueError, match="Invalid window_type"):
                compute_spending_report(
                    seed_user["user"].id,
                    SpendingWindow(window_type="bogus"),
                    user_settings=None,
                )


class TestTheReportReadsTheCashFlowSet:
    """Where the money went is the paycheck's across the set (plan step CC-4-3).

    Developer ruling ``credit_card:R-CC16``: the phone bill that is always
    paid by card is a row ON the card, so a report that read one account's
    settled rows left it out of where the money went.  Both window queries
    read the owner's cash-flow set now, through the one clause
    (:func:`~app.services.cash_flow_set.own_rows_clause` for the rows and
    :func:`~app.services.cash_flow_set.leg_accounts_shown` for a transfer's leg,
    since leaf ``balance:X-bi-6-1b``; it was ``paycheck_rows_clause``) -- graded
    once per query, a pay-period window and a month window -- and the scope
    names the BALANCE account, the primary.  Every case plants a card,
    because an owner with none is a set of one and cannot tell the clause
    from the filter it replaced.
    """

    @staticmethod
    def _card(seed_user, db_session):
        """Create an active Credit Card account for *seed_user*."""
        return create_account_of_type(
            seed_user, db_session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )

    def test_a_settled_bill_on_the_card_is_where_the_money_went(
        self, app, seed_user, seed_periods, db,
    ):
        """A pay-period window: Rent 500.00 on checking + Phone 45.00 on the card.

        spent_total = 500.00 + 45.00 = 545.00, both categories in the
        breakdown, and the scope still names Checking -- the rows are the
        set's, the name one member's.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "500.00")
            _txn(
                db, seed_user, seed_periods[0], "Phone", "Groceries", "45.00",
                account=card,
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("545.00")
            assert {g.group_name for g in report.breakdown} == {"Home", "Family"}
            assert report.scope.account_id == seed_user["account"].id
            assert report.scope.account_name == "Checking"

    def test_the_month_window_reads_the_same_set(
        self, app, seed_user, seed_periods, db,
    ):
        """The span query too: January 2026 holds both rows, 545.00.

        ``seed_periods[0]`` starts 2026-01-02, so both rows attribute to
        January by the COALESCE rule; the sibling query above is the
        pay-period arm, this is the one every live render runs.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "500.00")
            _txn(
                db, seed_user, seed_periods[0], "Phone", "Groceries", "45.00",
                account=card,
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("545.00")

    def test_a_transfer_between_members_is_counted_once_from_the_lines_side(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-CC23 on the report: the far leg is not spending.

        A settled $165.00 checking -> card payment is one settled expense,
        checking's expense leg (the card's is its income leg and the far
        leg).  A settled $40.00 card -> checking transfer is NO spending here:
        checking's leg is income and the card's expense leg is the far leg.
        spent_total = 165.00; without the far-leg arm the card's 40.00 leg
        would count too, 205.00.

        The transfer doors refuse a transfer OUT of a card (plan step CC-10),
        so the $40.00 card -> checking transfer is the representable-but-
        refused shape and its state is PLANTED, the way the legacy-source
        tests plant theirs: created Projected from a helper Savings account
        INTO checking (allowed), its source re-pointed onto the card by
        assignment, and only THEN settled through ``update_transfer`` -- the
        settle half of ``create_settled_transfer``, split out because the
        order decided where the POSTINGS landed.  A side's payment record
        follows its endpoint whenever the endpoint moves
        (``fk_transaction_entries_expense_side`` is ON UPDATE CASCADE, ruling
        R-BAL168), and until plan task plan#3075 the settle also booked each
        record's postings on that record's account as it stood at the settle
        (``_posting_purchases``'s ``_ledger_account_for(entry.account_id)``),
        which a re-point by assignment never booked again: settled first, the
        card side's record followed the source onto the card while its postings
        stayed on the helper's ledger.  Measured 10-15 both ways; in this order
        the card side's record and its postings were on the card, checking's on
        checking, and the helper held none.  Neither reader here reads postings,
        and the worked-out ledger reads each record where it stands, so the
        order is kept as it was.  The re-point moved the transfer's expense
        shadow row too until plan step balance:X-bi-6-4d-2 made a transfer
        one row (ruling R-BAL258); the transfer is the whole plant now
        (ruling R-BAL167 class 1).  The savings account is not a member of
        the set.  The plant carries its own tell, the transfer's endpoints,
        because an UN-planted fixture (helper -> checking, never re-pointed)
        reads the same $165.00 with or without the far-leg arm and would
        grade nothing.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], card,
                seed_periods[0], amount=Decimal("165.00"),
            )
            helper = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            out_of_card = create_transfer(
                seed_user, helper, seed_user["account"],
                seed_periods[0], amount=Decimal("40.00"),
            )
            db.session.flush()
            out_of_card.from_account = card
            db.session.flush()
            transfer_service.update_transfer(
                out_of_card.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            assert (out_of_card.from_account_id, out_of_card.to_account_id) == (
                card.id, seed_user["account"].id,
            )

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("165.00")

    def test_the_default_reads_the_saved_default_grid_account(
        self, app, seed_user, seed_periods, db,
    ):
        """The scope is the set's PRIMARY, read off the settings row passed.

        ``resolve_analytics_account`` read "first active checking" and no
        settings row.  With the row passed, an HYSA saved as the default is
        the scope; with ``None`` the chain runs without that layer and
        checking answers.
        """
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Rainy Day",
                anchor_balance=Decimal("1000.00"),
            )
            settings = set_default_grid_account(
                db.session, seed_user["user"].id, hysa.id,
            )
            db.session.commit()

            with_layer = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=settings,
            )
            without = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert with_layer.scope.account_name == "Rainy Day"
            assert without.scope.account_name == "Checking"


class TestATransferIsALegOfItsParent:
    """The report draws a settled transfer as a LEG read off its parent (leaf X-bi-6-1b).

    Both window loads answer the set's OWN rows -- no shadow -- beside the
    EXPENSE leg of every settled transfer the set touches, and the package's
    kernels (``settled_contribution`` and ``resolve_amount`` of a leg's
    parent, and ``resolved_actual_amount`` over it, ruling plan#1983) price
    a leg as its transfer.  The money case above (R-CC23) already
    grades what a transfer is WORTH here; these grade what it IS, and every
    figure the report derives from a leg.
    """

    def test_both_loaders_answer_own_rows_and_settled_expense_legs(
        self, app, seed_user, seed_periods, db,
    ):
        """$100.00 Rent and three transfers: settled out, planned out, settled in.

        Both settled-spend loaders (by period and by span) answer Rent as the
        one row and the settled $250.00 checking -> Savings transfer as its
        one expense leg; the planned $75.00 one is not settled and the
        settled $60.00 one INTO checking is income here, so neither is spend.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            rent = _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "100.00")
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"),
            )
            # A still-planned transfer is not spend; a settled transfer INTO
            # checking is its income leg on this set, not spend either.
            create_transfer(
                seed_user, checking, savings, seed_periods[0],
                amount=Decimal("75.00"),
            )
            create_settled_transfer(
                seed_user, db.session, savings, checking, seed_periods[0],
                amount=Decimal("60.00"),
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            by_period = spending_analysis.query_settled_expenses(
                cash_flow, seed_user["scenario"].id,
                Paychecks((seed_periods[0].id,)),
            )
            by_span = spending_analysis.query_settled_expenses(
                cash_flow, seed_user["scenario"].id,
                AttributionSpan(
                    seed_periods[0].start_date,
                    last_covered_day(seed_periods[0]),
                ),
            )
            # "No shadow" is the database's since plan step
            # balance:X-bi-6-4d-2: a row naming a transfer is refused at the
            # write (ruling R-BAL258), so a per-row ``transfer_id is None``
            # check here passed with ``own_rows_clause``'s ``IS NULL`` term
            # deleted (measured 10-15) and is gone.  Each load is one list,
            # checked by ``cell_key`` (ruling plan#2218, where it read the two
            # halves of ``PlanItems``): the same items, in the same order.
            for items in (by_period, by_span):
                assert [cell_key(item) for item in items] == [
                    rent.id, (settled.id, checking.id),
                ]
                assert items[0].name == "Rent" and items[1].is_expense

    def test_every_figure_the_report_derives_from_a_leg(
        self, app, seed_user, seed_periods, db,
    ):
        """$250.00 planned, $240.00 moved, due the day after it settled.

        spent_total 240.00 (the record, not the plan); the Uncategorized
        bucket holds it; the surprise is keyed by the pair with delta
        -10.00; timing counts one bill paid 1 day early.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
                settled_on=period.start_date,
                due_date=period.start_date + timedelta(days=1),
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.spent_total == Decimal("240.00")
            [group] = report.breakdown
            assert group.group_name == "Uncategorized"
            assert group.amount == Decimal("240.00")
            [surprise] = report.surprises.rows
            assert surprise.item_key == (settled.id, checking.id)
            assert surprise.name == "Transfer to Savings"
            assert (surprise.estimated, surprise.actual, surprise.delta) == (
                Decimal("250.00"), Decimal("240.00"), Decimal("-10.00"),
            )
            assert report.hero.payment_timing == {
                "total_bills_paid": 1,
                "paid_on_time": 1,
                "paid_late": 0,
                "avg_days_before_due": Decimal("1.00"),
            }

    def test_a_leg_reads_its_record_when_settled_else_its_plan(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled $250.00 transfer that moved $240.00 and a planned $75.00 one.

        Each is read through its checking leg's parent.  The settled one
        contributes its $240.00 record against its $250.00 plan and reads
        back $240.00 spent; the planned one reads back its $75.00 plan (zero
        variance), and ``settled_contribution`` refuses it: it recorded
        nothing.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
            )
            planned = create_transfer(
                seed_user, checking, savings, seed_periods[1],
                amount=Decimal("75.00"),
            )
            db.session.commit()
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            settled_leg = leg_of(settled, checking.id)
            planned_leg = leg_of(planned, checking.id)

            assert settled_contribution(parent_of(settled_leg)) == Decimal("240.00")
            assert resolve_amount(parent_of(settled_leg), basis) == Decimal("250.00")
            assert spending_analysis.resolved_actual_amount(parent_of(settled_leg), basis) == Decimal("240.00")
            # An unsettled leg reads back its plan, so its variance is zero
            # by construction -- and it recorded nothing to spend.
            assert spending_analysis.resolved_actual_amount(parent_of(planned_leg), basis) == Decimal("75.00")
            with pytest.raises(AmountUnresolvable):
                settled_contribution(parent_of(planned_leg))

    def test_a_soft_deleted_settled_transfer_is_no_spend_on_either_arm(
        self, app, seed_user, seed_periods, db,
    ):
        """Both loaders' ``Transfer.is_deleted`` term (the review of leaf
        X-bi-6-1b): nothing here re-checks in Python, so a deleted settled
        transfer's leg would otherwise publish with its name on the list (a
        $0.00-versus-plan surprise while shadows were born: the record join
        excluded its shadow's movement, so the leg read the $0.00 record)."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            deleted = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                deleted.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            by_period = spending_analysis.query_settled_expenses(
                cash_flow, seed_user["scenario"].id, Paychecks((period.id,)),
            )
            by_span = spending_analysis.query_settled_expenses(
                cash_flow, seed_user["scenario"].id,
                AttributionSpan(period.start_date, last_covered_day(period)),
            )
            # No row is seeded, so the one list is the legs (ruling plan#2218).
            assert by_period == [] and by_span == []
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.spent_total == Decimal("0")
            assert report.surprises.rows == []

    def test_a_zero_record_transfer_is_spend_of_nothing_and_not_a_timed_bill(
        self, app, seed_user, seed_periods, db,
    ):
        """A transfer settled at $0.00 (ruling R-BAL82: a close with no
        movement) is $0.00 of spend, and its leg has no settle DAY --
        ``TransferLeg.settled_on`` is its movement's and it has none -- so it
        is not a timed bill.  The STANDING answer since ruling R-BAL90 (finding
        BAL-528, leaf X-bi-6-4a), which ruled the interval control this was
        (the review of leaf X-bi-6-1b, finding M1) permanent and renamed it;
        its row twin is the next test."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            zero = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("0.00"),
                settled_on=period.start_date,
                due_date=period.start_date + timedelta(days=1),
            )
            db.session.commit()

            # No row is seeded, so the one list is the legs (ruling plan#2218).
            [leg] = spending_analysis.query_settled_expenses(
                CashFlowSet.single(checking), seed_user["scenario"].id,
                Paychecks((period.id,)),
            )
            assert leg.transfer.id == zero.id and leg.record is None
            assert settled_contribution(parent_of(leg)) == Decimal("0")
            assert leg.settled_on is None and leg.days_paid_before_due is None
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.spent_total == Decimal("0")
            assert report.hero.payment_timing is None

    def test_a_zero_record_row_is_not_a_timed_bill_and_a_recorded_one_is(
        self, app, seed_user, seed_periods, db,
    ):
        """R-BAL90's row half (finding BAL-528, leaf X-bi-6-4a): a ROW closed
        at $0.00 holds no entry (ruling R-BAL82) and is not a timed bill,
        though it keeps its own ``settled_on`` -- the metric reads the RECORD
        of a row as of a leg.  The same row with a record is timed, which
        is what shows the gate is the record and not the day.  Through leaf
        X-bi-6-1b the $0.00 row counted, one day early."""
        with app.app_context():
            period = seed_periods[0]
            due = period.start_date + timedelta(days=1)
            _txn(
                db, seed_user, period, "Water", "Rent", "45.00",
                actual="0.00", due_date=due, settled_on=period.start_date,
            )
            db.session.commit()
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.payment_timing is None

            _txn(
                db, seed_user, period, "Power", "Rent", "60.00",
                due_date=due, settled_on=period.start_date,
            )
            db.session.commit()
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.payment_timing == {
                "total_bills_paid": 1, "paid_on_time": 1, "paid_late": 0,
                "avg_days_before_due": Decimal("1.00"),
            }

    def test_a_zero_record_close_moves_the_on_time_count_and_the_average_too(
        self, app, seed_user, seed_periods, db,
    ):
        """R-BAL90's declared change on a MIXED window (the leaf's review, M4).

        Both bills are due on the period's third day.  Water closes at
        ``$0.00`` on the first day (2 days early, no record); Power pays
        ``$60.00`` on the fifth (2 days late, recorded).  Through leaf
        X-bi-6-1b both were timed: 2 bills, 1 on time, 1 late, average
        ``(2 + -2) / 2 = 0.00``.  With Water out, Power alone is timed: 1 bill,
        0 on time, 1 late, average ``-2 / 1 = -2.00``.  So the hero's "Bills
        on time" chip reads ``0 / 1`` where it read ``1 / 2``, and its caption
        "avg 2.0 days late" where it read "avg 0.0 days early": the close moved
        the total, the on-time count it fell in, and the average, which it
        moves here (its +2 days against the rest's -2).  ``paid_late`` stays
        1.  What this pins is HOW MUCH the declared change moves the
        hero on a window holding a late bill; the gate itself is killed by
        the row twin above as well (``_holds_a_record`` forced ``True`` fails
        both).
        """
        with app.app_context():
            period = seed_periods[0]
            due = period.start_date + timedelta(days=2)
            _txn(
                db, seed_user, period, "Water", "Rent", "45.00",
                actual="0.00", due_date=due, settled_on=period.start_date,
            )
            _txn(
                db, seed_user, period, "Power", "Rent", "60.00",
                due_date=due, settled_on=period.start_date + timedelta(days=4),
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )

            assert report.hero.payment_timing == {
                "total_bills_paid": 1, "paid_on_time": 0, "paid_late": 1,
                "avg_days_before_due": Decimal("-2.00"),
            }

    def test_tied_surprises_rank_rows_before_legs_by_a_total_order(
        self, app, seed_user, seed_periods, db,
    ):
        """P74's total key over both shapes: a row's id and a leg's pair
        cannot be compared, so the tiebreak is ``key_order``."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
            )
            row = _txn(
                db, seed_user, period, "Rent", "Rent", "100.00", actual="90.00",
            )
            db.session.commit()
            items = spending_analysis.query_settled_expenses(
                CashFlowSet.single(checking), seed_user["scenario"].id,
                Paychecks((period.id,)),
            )
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            surprises = _build_surprises(list(reversed(items)), basis)
            assert [s.delta for s in surprises.rows] == [
                Decimal("-10.00"), Decimal("-10.00"),
            ]
            assert [s.item_key for s in surprises.rows] == [
                row.id, (settled.id, checking.id),
            ]
