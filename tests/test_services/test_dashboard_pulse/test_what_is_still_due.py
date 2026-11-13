"""What is still due: the totals on the locked B4 bases, and the upcoming bills they read, across
the owner's cash-flow set (plan step CC-4-3) and as legs read off a transfer's parent (leaf
X-bi-6-1b).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.ref import AccountType
from app.services import account_service, dashboard_service
from app.services.dashboard_service import _bills
from app.services import transfer_service
from app.services.transfer_legs import cell_key
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    last_covered_day,
    create_savings_account,
    dashboard_section,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _CURRENT_IDX, _TODAY, _add_expense, _add_tracked_expense, _add_entry


# ── Still due: locked B4 bases, current vs next separation ──────────


class TestPulseStillDue:
    """Still-due totals on the locked B4 bases (data-value pass)."""

    def test_untracked_row_contributes_effective_amount(
        self, app, seed_user, seed_periods, db,
    ):
        """An untracked projected expense contributes its effective amount.

        One $300.00 projected expense in the current period; still-due for
        the current period is $300.00, next period $0.00.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Rent", "300.00", due_date=date(2026, 3, 22),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            still = result["still_due"]
            assert still["current_period"] == Decimal("300.00")
            assert still["next_period"] == Decimal("0.00")

    def test_tracked_partial_entries_contributes_remaining(
        self, app, seed_user, seed_periods, db,
    ):
        """A tracked row with partial entries contributes remaining-after-entries.

        Envelope estimated $200.00, entries summing $80.00:
            remaining = 200.00 - 80.00 = 120.00 (floored at 0 -> 120.00).
        Still-due for the current period is $120.00.
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "200.00",
            )
            _add_entry(db.session, seed_user, txn, "50.00", date(2026, 3, 18))
            _add_entry(db.session, seed_user, txn, "30.00", date(2026, 3, 19))
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("120.00")

    def test_tracked_over_budget_floors_at_zero(
        self, app, seed_user, seed_periods, db,
    ):
        """An over-budget envelope contributes 0, never a negative.

        Envelope estimated $100.00, entries summing $130.00:
            remaining = 100.00 - 130.00 = -30.00 -> floored to 0.00.
        Still-due for the current period is $0.00 (not -$30.00).
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "100.00",
            )
            _add_entry(db.session, seed_user, txn, "70.00", date(2026, 3, 18))
            _add_entry(db.session, seed_user, txn, "60.00", date(2026, 3, 19))
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("0.00")

    def test_a_REFUNDED_envelope_contributes_MORE_than_its_budget(
        self, app, seed_user, seed_periods, db,
    ):
        """The floor is BELOW, and there is deliberately none above.

        Developer ruling 2026-09-01, ruling **bank_import:R-II**, plan step
        ``bank_import:X-gj-2b-3``.  Envelope `$100.00`, one `-$50.00` refund:

            remaining = 100.00 - (-50.00) = 150.00, not floored.

        Still-due reads `$150.00` against a `$100.00` plan, and that is the
        ruled NET basis: the plan is `$100.00` of net spending and `-$50.00` of
        it has happened, so `$150.00` may still be recorded.  Capping it at the
        budget was refused with these numbers, because the dashboard would then
        disagree with the reservation ``cash_ledger`` holds for the same row.

        Asserted BESIDE the over-budget case above, which is the one that
        makes the floor real: a producer with no floor at all would fail that
        one and a producer clamped to `[0, budget]` would fail this one.
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "100.00",
            )
            _add_entry(
                db.session, seed_user, txn, "-50.00", date(2026, 3, 18),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("150.00")

    def test_tracked_no_entries_contributes_full_estimate(
        self, app, seed_user, seed_periods, db,
    ):
        """A tracked row with no entries contributes its full estimate.

        Envelope estimated $150.00, no entries:
            remaining = 150.00 - 0 = 150.00.
        Still-due for the current period is $150.00.
        """
        with app.app_context():
            _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "150.00",
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("150.00")

    def test_projected_transfer_out_included_in_still_due(
        self, app, seed_user, seed_periods, db,
    ):
        """A projected transfer out of checking IS counted in still-due (B4b).

        Build a REAL projected transfer (through the transfer service) from
        checking to savings for $400.00 in the current period.  Its from-side
        leg lands on checking as a projected outflow and contributes its
        $400.00 to still-due.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Sweep Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[_CURRENT_IDX].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("400.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                    due_date=date(2026, 3, 24),
                ),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            # The transfer out is the only obligation: $400.00.
            assert result["still_due"]["current_period"] == Decimal("400.00")

    def test_current_and_next_period_separated(
        self, app, seed_user, seed_periods, db,
    ):
        """Still-due totals split correctly between current and next period.

        $300.00 in the current period (5) and $175.00 in the next period
        (6).  current_period total = 300.00, next_period total = 175.00.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "This period bill", "300.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX + 1],
                "Next period bill", "175.00", due_date=date(2026, 3, 30),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            still = result["still_due"]
            assert still["current_period"] == Decimal("300.00")
            assert still["next_period"] == Decimal("175.00")
            # The next period's date range accompanies its total so the
            # template can label "Next period (Mar 27 - Apr 9): $X".
            assert still["next_period_start"] == seed_periods[_CURRENT_IDX + 1].start_date
            assert still["next_period_start"] == date(2026, 3, 27)
            assert still["next_period_end"] == last_covered_day(seed_periods[_CURRENT_IDX + 1])
            assert still["next_period_end"] == date(2026, 4, 9)

    def test_still_due_next_period_dates_none_when_no_next(
        self, app, seed_user, db,
    ):
        """No next period -> next_period total 0.00 and date range None/None.

        Generate exactly 6 periods (indices 0..5) so the period containing
        the frozen 2026-03-20 (index 5) is the LAST one: there is no next
        period, so the still-due block reports next_period 0.00 and both
        next-period dates None (the template renders its generate-periods
        fallback line then).
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            still = result["still_due"]
            assert still["next_period"] == Decimal("0.00")
            assert still["next_period_start"] is None
            assert still["next_period_end"] is None

    def test_settled_row_excluded_from_still_due(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled (done) expense is not still due -- it is already paid.

        One $500.00 DONE expense in the current period contributes nothing
        (the still-due query is Projected-only).
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Already paid", "500.00",
                status_enum=StatusEnum.DONE, due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("0.00")


class TestTheBillsAreThePaychecksAcrossTheSet:
    """The upcoming bills read the owner's cash-flow set (plan step CC-4-3).

    Developer ruling ``credit_card:R-CC16``: a plan item's ``account_id`` is
    the account its money is expected to move through, so the phone bill
    that is always paid by card is a row ON the card -- and a dashboard
    that read one account's rows dropped it from what the paycheck still
    owes.  The bills are the set's now, through the one clause
    (:func:`~app.services.cash_flow_set.own_rows_clause` for the rows and
    :func:`~app.services.cash_flow_set.leg_accounts_shown` for a transfer's leg,
    since leaf ``balance:X-bi-6-1b``; it was ``paycheck_rows_clause``); the hero,
    the chart and the trough stay the BALANCE line's, the primary.  Every
    case below plants a card, because an owner with none is a set of one
    and cannot tell the clause from the filter it replaced.
    """

    @staticmethod
    def _card(seed_user, db_session):
        """Create an active Credit Card account for *seed_user*."""
        return create_account_of_type(
            seed_user, db_session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )

    @staticmethod
    def _expense_on(seed_user, period, account, name, amount, due_date):
        """Place a projected one-off EXPENSE row on *account* in *period*."""
        return one_off_row_of(
            period, name=name, amount=Decimal(amount),
            user_id=seed_user["user"].id, account_id=account.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            due_date=due_date,
        )

    def test_a_bill_on_the_card_is_a_bill_the_paycheck_owes(
        self, app, seed_user, seed_periods, db,
    ):
        """A projected expense ON the card is due-soon and still-due.

        Rent $300.00 on checking due 03-22 and the phone bill $45.00 on the
        card due 03-25, both in the current period: still-due for the
        current period is 300.00 + 45.00 = $345.00, the due-soon list holds
        both rows in due-date order, and the hero is still checking's --
        the balance line is one account's whatever the rows are.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            period = seed_periods[_CURRENT_IDX]
            self._expense_on(
                seed_user, period, seed_user["account"], "Rent", "300.00",
                date(2026, 3, 22),
            )
            self._expense_on(
                seed_user, period, card, "Phone", "45.00", date(2026, 3, 25),
            )
            db.session.commit()

            section = dashboard_section(seed_user["user"].id)
            assert section.cash_flow.member_ids == (
                seed_user["account"].id, card.id,
            )
            assert section.account.id == seed_user["account"].id

            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("345.00")
            assert [b["name"] for b in result["due_soon"]] == ["Rent", "Phone"]
            assert result["hero"]["account_id"] == seed_user["account"].id

    def test_a_transfer_between_members_is_one_bill_from_the_balance_lines_side(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-CC23 on the bills: the far leg is not a bill.

        A $165.00 checking -> card payment is ONE obligation, checking's
        expense leg (the Gate B4b transfer-out rule); the card's leg is its
        income leg and the far leg, twice excluded.  A $40.00 card ->
        checking transfer is NO bill: checking's leg is income and the card's
        expense leg is the far leg -- the second view of an act the balance
        line's side already shows.  Still-due is $165.00 and the due-soon
        list holds exactly the one transfer bill.  Without the far-leg arm the
        card's $40.00 leg would be a bill and the total $205.00.

        The transfer doors refuse a transfer OUT of a card (plan step CC-10),
        so the $40.00 card -> checking transfer is the representable-but-
        refused shape and its state is PLANTED, the way the legacy-source
        tests plant theirs: created from a helper Savings account INTO
        checking (allowed), then its source re-pointed onto the card by
        assignment, past the door.  The re-point moved the transfer's expense
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
            period = seed_periods[_CURRENT_IDX]
            payment = create_transfer(
                seed_user, seed_user["account"], card, period,
                amount=Decimal("165.00"), due_date=date(2026, 3, 24),
            )
            helper = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            out_of_card = create_transfer(
                seed_user, helper, seed_user["account"], period,
                amount=Decimal("40.00"), due_date=date(2026, 3, 21),
            )
            db.session.flush()
            out_of_card.from_account = card
            db.session.commit()
            assert (out_of_card.from_account_id, out_of_card.to_account_id) == (
                card.id, seed_user["account"].id,
            )

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("165.00")
            bills = result["due_soon"]
            assert len(bills) == 1
            assert bills[0]["id"] == (payment.id, seed_user["account"].id)
            assert bills[0]["amount"] == Decimal("165.00")

    def test_a_set_of_one_is_the_old_filter(
        self, app, seed_user, seed_periods, db,
    ):
        """With no card the bills are checking's alone, as before the step.

        The byte-identity claim in a unit test's shape: a $300.00 checking
        row and a $75.00 row on a SAVINGS account (an owned cash-flow
        account that is not a member) -- still-due reads $300.00 and the
        savings row is not a bill.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            period = seed_periods[_CURRENT_IDX]
            self._expense_on(
                seed_user, period, seed_user["account"], "Rent", "300.00",
                date(2026, 3, 22),
            )
            self._expense_on(
                seed_user, period, savings, "Fee", "75.00", date(2026, 3, 23),
            )
            db.session.commit()

            section = dashboard_section(seed_user["user"].id)
            assert section.cash_flow.member_ids == (seed_user["account"].id,)

            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("300.00")
            assert [b["name"] for b in result["due_soon"]] == ["Rent"]


class TestTheBillsAreLegsReadOffTheParent:
    """The bills draw a transfer as a LEG of its parent (leaf X-bi-6-1b, R-BAL86).

    ``_query_unpaid_expense_rows`` answers the set's OWN rows through
    ``own_rows_clause`` -- no shadow row -- beside the EXPENSE leg of every
    projected transfer the set touches, read off ``budget.transfers``; the
    still-due total and the due-soon list price both through one map keyed
    by ``cell_key``.  The money cases above (B4b, R-CC23) already grade what
    a transfer is WORTH here; these grade what it IS.
    """

    @staticmethod
    def _expense_on(seed_user, period, account, name, amount, due_date):
        """Place a projected one-off EXPENSE row on *account* in *period*."""
        return one_off_row_of(
            period, name=name, amount=Decimal(amount),
            user_id=seed_user["user"].id, account_id=account.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            due_date=due_date,
        )

    def test_the_loader_answers_own_rows_and_expense_legs(
        self, app, seed_user, seed_periods, db,
    ):
        """$300.00 Rent and a $400.00 checking -> Sweep Target transfer, one period.

        The unpaid loader answers one list: Rent, the one row, then the
        transfer's one EXPENSE leg on checking.
        """
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Sweep Target", Decimal("0.00"),
            )
            rent = self._expense_on(
                seed_user, period, checking, "Rent", "300.00", date(2026, 3, 22),
            )
            transfer = create_transfer(
                seed_user, checking, savings, period,
                amount=Decimal("400.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()
            section = dashboard_section(seed_user["user"].id)

            unpaid = _bills._query_unpaid_expense_rows(  # pylint: disable=protected-access
                section.cash_flow, seed_user["scenario"].id, [period.id],
            )

            # "No shadow" is the database's since plan step
            # balance:X-bi-6-4d-2: a row naming a transfer is refused at the
            # write (ruling R-BAL258), so a per-row ``transfer_id is None``
            # check here passed with ``own_rows_clause``'s ``IS NULL`` term
            # deleted (measured 10-15) and is gone.
            assert [cell_key(item) for item in unpaid] == [
                rent.id, (transfer.id, checking.id),
            ]
            assert unpaid[0].name == "Rent" and unpaid[1].is_expense

    def test_a_leg_bill_is_keyed_by_the_pair_and_named_from_its_endpoints(
        self, app, seed_user, seed_periods, db,
    ):
        """A $400.00 transfer to Sweep Target due 2026-03-24 is one due-soon bill.

        Keyed by its ``(transfer id, account id)`` pair, named from its
        endpoints ("Transfer to Sweep Target"), priced at its $400.00 plan
        with no tracked base, and counted in the period's $400.00 still due.
        """
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Sweep Target", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, checking, savings, period,
                amount=Decimal("400.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )

            assert result["still_due"]["current_period"] == Decimal("400.00")
            [bill] = result["due_soon"]
            assert bill["id"] == (transfer.id, checking.id)
            assert bill["name"] == "Transfer to Sweep Target"
            assert bill["amount"] == Decimal("400.00")
            assert bill["amount_base"] is None
            assert bill["is_tracked"] is False
            assert bill["days_until_due"] == (date(2026, 3, 24) - _TODAY).days

    def test_a_soft_deleted_transfer_is_no_bill(
        self, app, seed_user, seed_periods, db,
    ):
        """The loader's ``Transfer.is_deleted`` term, graded on the one reader
        with no Python re-check behind it (the review of leaf X-bi-6-1b):
        a soft-deleted projected transfer draws no leg and moves no total."""
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Sweep Target", Decimal("0.00"),
            )
            deleted = create_transfer(
                seed_user, checking, savings, period,
                amount=Decimal("400.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                deleted.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            section = dashboard_section(seed_user["user"].id)

            unpaid = _bills._query_unpaid_expense_rows(  # pylint: disable=protected-access
                section.cash_flow, seed_user["scenario"].id, [period.id],
            )
            assert unpaid == []
            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("0.00")
            assert result["due_soon"] == []

    def test_a_transfer_into_the_set_is_no_bill(
        self, app, seed_user, seed_periods, db,
    ):
        """The set shows a savings -> checking transfer from checking's side,
        where it is an INCOME leg -- money arriving, not a bill -- so the
        loader draws no leg for it and the totals ignore it, as the income
        shadow row's type did."""
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("900.00"),
            )
            create_transfer(
                seed_user, savings, checking, period,
                amount=Decimal("250.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()
            section = dashboard_section(seed_user["user"].id)

            unpaid = _bills._query_unpaid_expense_rows(  # pylint: disable=protected-access
                section.cash_flow, seed_user["scenario"].id, [period.id],
            )
            assert unpaid == []
            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("0.00")
            assert result["due_soon"] == []
