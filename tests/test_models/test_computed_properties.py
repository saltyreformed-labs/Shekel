"""
Shekel Budget App -- Model Computed Property Tests

Tests for computed properties on models:
  - Transaction: what it contributes (the retired ``effective_amount``
    rules, now ``cash_ledger.contribution_of`` for a row that has not settled
    and ``row_valuation.settled_contribution`` for one that has), is_income,
    is_expense
  - Transfer: what its amount resolves to (``resolve_transfer_amount``)
  - Category: display_name
  - PaycheckBreakdown: total_pre_tax, total_post_tax, total_taxes
"""

from datetime import date, datetime, timezone

import pytest
from decimal import Decimal

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.mixins import reject_settle_instant
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.paycheck_calculator import (
    DeductionBreakdown,
    PricedLine,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)
from app.services.pay_calendar import PayCadence
from app.services.pay_rhythm import FixedDays
from app.services import account_service
from app.utils.dates import add_months
from app.services.cash_ledger import (
    derived_amount_basis,
    contribution_of,
    resolve_transfer_amount,
)
from app.services.row_valuation import settled_contribution
from tests._test_helpers import (
    amount_basis_for,
    cover_bare_settled_row,
    create_savings_account,
    default_settle_day,
    one_off_row_of,
    payback_row_of,
    settle_day_columns,
)
from app.models.amount_ownership import AmountOwnership


# ── What a TRANSACTION contributes ───────────────────────────────────


class TestTransactionEffectiveAmount:
    """What one transaction contributes, by the rules the property held.

    ``Transaction.effective_amount`` was deleted at plan step X-au-c2 -- a
    model property cannot resolve a DERIVED amount, being a pure in-memory read
    with no session -- and its four arms moved to
    ``row_valuation.settled_contribution`` for a row that owns its figure.  These
    cases moved with them unchanged, because the RULES did not change: zero for
    a soft-deleted or excluded row, what a SETTLED row recorded, else the row's
    own stored figure.

    **The second arm was ``actual_amount`` until plan step X-au-c3, and TWO
    cases went with the change rather than being retargeted.**  That column was
    reachable on an UNSETTLED row and OUTRANKED the plan there, so the class
    pinned "a Projected row with an actual prefers the actual" (case 5A.1) and
    its ``$0.00`` twin (E-12's projected half).  Both are refuted now, and by
    the STATUS rather than by the record: an unsettled row carrying a recorded
    figure is perfectly constructible -- it is the RETAINED state a revert
    leaves behind, the covering movement kept un-dated on purpose (plan step
    X-bi-3e-2) -- and ``row_valuation.settled_figure`` answers ``None`` for it
    whatever it still remembers, so such a row is worth its PLAN.  The
    preference those two cases asserted has no state left to hold in.

    The five production rows carrying a figure while unsettled were promoted
    into their PLAN by migration ``e4b8a71c0f36`` -- balance-neutrally, because
    the valuation was already answering the promoted figure.  So the correction
    5A.1 was about is made on ``estimated_amount`` now, which
    ``test_projected_returns_estimated`` grades, and E-12's rule survives on the
    settled half in ``test_done_with_zero_actual``.  That the retained state is
    STORABLE and worth nothing is graded in
    ``tests/test_models/test_settlement_record.py``.
    """

    def _make_txn(self, seed_user, seed_periods, status_name, estimated, actual=None):
        """Helper: create a transaction with given status and amounts.

        A row built in a SETTLED status carries the whole record -- the day, the
        figure and how the figure is known -- through the one door a bare-built
        fixture uses (``_test_helpers.cover_bare_settled_row``, which writes the
        covering movement; plan step balance:X-bi-4b-2).  *actual* is the
        figure a human typed, which makes the record a ``corrected`` one; with
        no *actual* the record is ``derived`` at the row's own plan, which is
        what a settle with nothing to correct records.
        """
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        settled_on = default_settle_day(seed_periods[0], status.id)
        txn = one_off_row_of(
            seed_periods[0],
            name="Test",
            amount=estimated,
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        txn.status_id = status.id
        for _column, _value in settle_day_columns(settled_on).items():
            setattr(txn, _column, _value)
        db.session.flush()
        if settled_on is not None:
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, estimated, actual)
        return txn

    def test_projected_returns_estimated(self, app, db, seed_user, seed_periods):
        """Projected transaction returns estimated_amount.

        **It asks the AMOUNT MODEL, and plan step X-bx is why.**  This graded
        ``row_valuation.owned_contribution`` until then -- an accessor whose
        readers are all settled-only, which reached a Projected row's plan only
        through the fall-through that step deleted (finding **BAL-465**).  The
        producer for what a row that has NOT settled contributes is
        :func:`~app.services.cash_ledger.contribution_of`, and it always was;
        the figure asserted is unchanged, and so is the rule under test.
        """
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "Projected", Decimal("150.00"))
            assert contribution_of(txn, amount_basis_for(txn)) == Decimal("150.00")

    # ── The case that arrived at plan step X-c2c2c ───────────────────
    #
    # It was asserted inside ``test_balance_calculator.py``, through the
    # balance walk, by two tests whose other six cases duplicated the ones
    # above.  The duplicates deleted; the survivors had no home here and are
    # the whole of what that move preserved.  The property is the MODEL's, so
    # it is graded against the model.  Two of the three went at plan step
    # X-au-c3 -- see the class docstring for the state they described and why
    # it no longer exists.

    def test_a_soft_deleted_row_is_worth_zero_whatever_its_status(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_deleted`` zeroes the row regardless of status or amounts.

        The last of the property's four branches, and the only one no test
        here covered: a soft-deleted row is worth nothing even when its status
        is a settled one and it recorded a figure.

        **The row is a transfer's leg because no other can be hidden still
        recording money** (ruling **R-CC92**): the database refuses a commit
        leaving any other row hidden while it holds its payment, and a row
        whose payment is gone records no figure for this branch to zero.
        This flagged a Paid $75.00 row hidden with its payment inside and
        never committed.  A settled transfer's soft delete hid its legs
        holding their payments (finding **balance:BAL-532**) until plan step
        ``balance:X-bi-6-4d-2``.  Re-expressed under rule 5,
        developer-confirmed 2026-09-23.

        **Since that step the state is STAGED inside the save and refused at
        its commit** (ruling **R-BAL167** class 2): the delete door takes the
        payments off (ruling **credit_card:R-CC75**) and the deleted-row
        rule's transfer arm refuses a transfer hidden holding one, so the
        transfer is hidden by SQL with the payments left on, the Checking
        leg -- its transfer and its side's record -- is valued inside the
        save (``settled_contribution`` of the leg's parent, ruling
        **plan#1983**), and then the commit is refused.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import text

        from app.models.transaction_entry import TransactionEntry
        from app.services.transfer_legs import TransferLeg, parent_of
        from tests._test_helpers import (
            create_settled_transfer,
            refused_by_database_rule,
        )

        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
                settled_amount=Decimal("75.00"),
            )
            db.session.commit()
            # Hidden the way the soft delete left it until the step: the
            # transfer flagged, the payments in place.
            db.session.execute(
                text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
                {"t": xfer.id},
            )
            db.session.expire_all()
            leg = TransferLeg(
                transfer=xfer, account_id=seed_user["account"].id,
                is_income=False,
            )
            # Read off the transfer's list (ruling plan#1984), checked by its
            # own SQL rather than handed to the leg.
            assert leg.record is db.session.query(TransactionEntry).filter(
                TransactionEntry.expense_transfer_id == xfer.id,
            ).one()
            assert leg.is_deleted and leg.status.is_settled and leg.covering_movements

            assert settled_contribution(parent_of(leg)) == Decimal("0")

            with refused_by_database_rule(
                "was deleted while it still holds a recorded payment",
            ) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {xfer.id} " in str(caught.value)

    def test_done_with_actual_returns_actual(self, app, db, seed_user, seed_periods):
        """A Paid row that recorded a human's CORRECTION is worth that figure."""
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Paid",
                Decimal("150.00"), actual=Decimal("145.00"),
            )
            assert settled_contribution(txn) == Decimal("145.00")

    def test_done_without_actual_returns_estimated(self, app, db, seed_user, seed_periods):
        """A Paid row that recorded no correction is worth what it DERIVED.

        The figure is the row's own plan, and the two are equal here -- but they
        are read from different columns since plan step X-au-c3, and that is the
        point.  This used to be the FALL-BACK case: a settled row stored no
        figure at all, so the valuation read its plan.  A settle now records the
        figure it booked, so the same ``$150.00`` is an answer about the money
        rather than about the forecast.
        """
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "Paid", Decimal("150.00"))
            assert settled_contribution(txn) == Decimal("150.00")

    def test_credit_status_returns_zero(self, app, db, seed_user, seed_periods):
        """Credit-status transaction contributes Decimal('0').

        Credit transactions are excluded from checking balance calculations
        because the charge is on a credit card, not the checking account.
        Expected: the contribution == Decimal('0') regardless of estimated_amount.
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Credit", Decimal("250.00"),
            )
            assert settled_contribution(txn) == Decimal("0")

    def test_cancelled_status_returns_zero(self, app, db, seed_user, seed_periods):
        """Cancelled transaction contributes Decimal('0').

        Cancelled transactions should not affect balance projections at all.
        Expected: the contribution == Decimal('0').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Cancelled", Decimal("500.00"),
            )
            assert settled_contribution(txn) == Decimal("0")

    def test_received_uses_estimated_when_no_actual(self, app, db, seed_user, seed_periods):
        """A Received row that recorded no correction is worth what it DERIVED.

        'Received' settles exactly as 'Paid' does: the record states the figure,
        and with nobody correcting it the figure is what the row was worth.
        Expected: the contribution == Decimal('150.00').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Received", Decimal("150.00"),
            )
            assert settled_contribution(txn) == Decimal("150.00")

    def test_received_uses_actual_when_set(self, app, db, seed_user, seed_periods):
        """A Received row that recorded a CORRECTION is worth that figure.

        The record and the plan are two different columns, and a settled row is
        worth what it recorded.
        Expected: the contribution == Decimal('145.00').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Received",
                Decimal("150.00"), actual=Decimal("145.00"),
            )
            assert settled_contribution(txn) == Decimal("145.00")

    def test_done_with_zero_actual(self, app, db, seed_user, seed_periods):
        """A Paid row that recorded ``$0.00`` is worth zero (e.g., a waived fee).

        Zero is a VALUE, not "missing" (E-12): a row whose record says nothing
        moved must not fall back to the ``$100.00`` it planned.
        Expected: the contribution == Decimal('0.00').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Paid",
                Decimal("100.00"), actual=Decimal("0.00"),
            )
            assert settled_contribution(txn) == Decimal("0.00")


# ── Transaction.is_income / is_expense ───────────────────────────────


class TestTransactionTypeProperties:
    """Tests for Transaction.is_income and is_expense properties."""

    def test_is_income(self, app, db, seed_user, seed_periods):
        """is_income returns True for income-type transactions."""
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            txn = one_off_row_of(
                seed_periods[0],
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            db.session.flush()

            assert txn.is_income is True
            assert txn.is_expense is False

    def test_is_expense(self, app, db, seed_user, seed_periods):
        """is_expense returns True for expense-type transactions."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            txn = one_off_row_of(
                seed_periods[0],
                name="Groceries",
                amount=Decimal("85.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            assert txn.is_expense is True
            assert txn.is_income is False


# ── What a TRANSFER's amount is ──────────────────────────────────────


class TestTransferAmountResolves:
    """What ``budget.transfers.amount`` resolves to, after the property went.

    ``Transfer.effective_amount`` was deleted at plan step X-au-c2 and these
    cases were retargeted onto :func:`cash_ledger.resolve_transfer_amount`, the
    rule that answers the same question under the amount model.  They are NOT
    equivalent and the difference is the point: the property zeroed a Cancelled
    transfer, the resolver answers what the row's amount IS and leaves "does
    this count" to the caller -- the same split the transaction side already
    has between ``resolve_transaction_amount`` and ``contributed_amount``.

    **The zeroing arm went with the property because it had no consumer.**  An
    AST census at X-au-c2 found ZERO reads of ``Transfer.effective_amount``
    anywhere in ``app/`` -- every one of the 24 sites was on ``Transaction`` --
    so the Cancelled case it graded was a rule nothing asked.  Its
    ``test_cancelled_returns_zero`` is therefore deleted rather than
    retargeted; the transfer status gate that DOES have consumers is
    ``balance_predicates.is_balance_contributing``, tested in
    ``test_utils/test_balance_predicates.py``.
    """

    def _make_transfer(self, seed_user, seed_periods, status_name, amount):
        """Helper: create a transfer with given status and amount."""
        from app.models.ref import AccountType

        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(savings)
        db.session.flush()

        status = db.session.query(Status).filter_by(name=status_name).one()
        xfer = Transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=status.id,
            name="Test Transfer",
            amount_ownership=AmountOwnership.own(amount),
        )
        db.session.add(xfer)
        db.session.flush()
        return xfer

    def test_projected_resolves_to_its_own_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected ad-hoc transfer owns its figure, so it resolves to it."""
        with app.app_context():
            xfer = self._make_transfer(
                seed_user, seed_periods, "Projected", Decimal("500.00"),
            )
            assert resolve_transfer_amount(
                xfer,
                derived_amount_basis(
                    seed_user["user"].id, seed_user["scenario"].id,
                ),
            ) == Decimal("500.00")

    def test_settled_resolves_to_its_own_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Leaving Projected does not change WHO owns the figure."""
        with app.app_context():
            xfer = self._make_transfer(
                seed_user, seed_periods, "Paid", Decimal("500.00"),
            )
            assert resolve_transfer_amount(
                xfer,
                derived_amount_basis(
                    seed_user["user"].id, seed_user["scenario"].id,
                ),
            ) == Decimal("500.00")

    def test_cancelled_still_resolves_to_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled transfer's AMOUNT is unchanged -- the gate is elsewhere.

        The deleted property answered ``$0.00`` here.  The resolver answers
        what the row's amount is and says nothing about whether it counts,
        which is the separation the amount model is built on: a status is a
        fact about CONTRIBUTION, not about ownership (see
        ``_amount_source.amount_rule`` on why soft deletion does not change the
        rule either).  Pinned so the difference is deliberate and visible
        rather than discovered.
        """
        with app.app_context():
            xfer = self._make_transfer(
                seed_user, seed_periods, "Cancelled", Decimal("500.00"),
            )
            assert resolve_transfer_amount(
                xfer,
                derived_amount_basis(
                    seed_user["user"].id, seed_user["scenario"].id,
                ),
            ) == Decimal("500.00")


# ── Category.display_name ────────────────────────────────────────────


class TestCategoryDisplayName:
    """Tests for Category.display_name property."""

    def test_display_name_format(self, app, db, seed_user):
        """display_name returns 'group: item' format."""
        with app.app_context():
            cat = seed_user["categories"]["Rent"]
            assert cat.display_name == "Home: Rent"


# ── PaycheckBreakdown computed totals ────────────────────────────────


class TestPaycheckBreakdownTotals:
    """Tests for the section totals on PaycheckBreakdown.

    ``deductions.total_pre_tax`` / ``deductions.total_post_tax`` and
    ``taxes.total`` (formerly the flat ``total_pre_tax`` / ``total_post_tax``
    / ``total_taxes`` properties on the breakdown itself).
    """

    def test_total_pre_tax(self):
        """total_pre_tax sums pre-tax deduction amounts."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                base_biweekly=Decimal("2884.62"),
                gross_biweekly=Decimal("2884.62"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    PricedLine(name="401k", amount=Decimal("250.00")),
                    PricedLine(name="HSA", amount=Decimal("100.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("350.00")

    def test_total_post_tax(self):
        """total_post_tax sums post-tax deduction amounts."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                base_biweekly=Decimal("2884.62"),
                gross_biweekly=Decimal("2884.62"),
            ),
            deductions=DeductionBreakdown(
                post_tax=[
                    PricedLine(name="Roth IRA", amount=Decimal("200.00")),
                    PricedLine(name="Life Insurance", amount=Decimal("25.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_post_tax == Decimal("225.00")

    def test_total_taxes(self):
        """total sums federal + state + ss + medicare."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                base_biweekly=Decimal("2884.62"),
                gross_biweekly=Decimal("2884.62"),
            ),
            taxes=TaxLines(
                federal=Decimal("300.00"),
                state=Decimal("130.00"),
                social_security=Decimal("178.85"),
                medicare=Decimal("41.83"),
            ),
        )
        assert breakdown.taxes.total == Decimal("650.68")

    def test_empty_deductions_return_zero(self):
        """Empty deduction lists produce Decimal('0') totals."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                base_biweekly=Decimal("2884.62"),
                gross_biweekly=Decimal("2884.62"),
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("0")
        assert breakdown.deductions.total_post_tax == Decimal("0")

    def test_net_pay_stored_value(self):
        """net_pay is a stored field, not a computed property.

        The Earnings section stores net_pay as set by calculate_paycheck.
        Verify that when constructed with an explicit net_pay value, it
        equals gross - pre_tax - taxes - post_tax.
        Expected: net_pay == Decimal('1607.69').
        """
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                base_biweekly=Decimal("2307.69"),
                gross_biweekly=Decimal("2307.69"),
                net_pay=Decimal("1607.69"),
            ),
            taxes=TaxLines(
                federal=Decimal("250.00"),
                state=Decimal("100.00"),
                social_security=Decimal("75.00"),
                medicare=Decimal("25.00"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    PricedLine(name="401k", amount=Decimal("200.00")),
                ],
                post_tax=[
                    PricedLine(name="Roth", amount=Decimal("50.00")),
                ],
            ),
        )
        # Verify net_pay stores the exact value we set.
        assert breakdown.earnings.net_pay == Decimal("1607.69")
        # Cross-check: gross - all deductions/taxes should equal net_pay.
        expected = (
            breakdown.earnings.gross_biweekly
            - breakdown.deductions.total_pre_tax
            - breakdown.taxes.total
            - breakdown.deductions.total_post_tax
        )
        assert expected == Decimal("1607.69")
        assert breakdown.earnings.net_pay == expected

    def test_net_pay_all_zeros(self):
        """net_pay with zero deductions and taxes equals gross.

        When all deductions and taxes are zero, the entire gross pay
        should pass through as net pay.
        Expected: net_pay == Decimal('2000.00').
        """
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("52000"),
                base_biweekly=Decimal("2000.00"),
                gross_biweekly=Decimal("2000.00"),
                net_pay=Decimal("2000.00"),
            ),
        )
        assert breakdown.earnings.net_pay == Decimal("2000.00")
        assert breakdown.deductions.total_pre_tax == Decimal("0")
        assert breakdown.taxes.total == Decimal("0")
        assert breakdown.deductions.total_post_tax == Decimal("0")

    def test_net_pay_negative_when_deductions_exceed_gross(self):
        """net_pay can be negative when deductions exceed gross.

        The Earnings section does not clamp net_pay to zero.  If
        calculate_paycheck produces a negative net_pay (which is a data
        configuration error), the section stores it as-is.
        Expected: net_pay == Decimal('-200.00').
        """
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("52000"),
                base_biweekly=Decimal("2000.00"),
                gross_biweekly=Decimal("2000.00"),
                net_pay=Decimal("-200.00"),
            ),
            taxes=TaxLines(
                federal=Decimal("500.00"),
                state=Decimal("200.00"),
                social_security=Decimal("0"),
                medicare=Decimal("0"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    PricedLine(name="401k", amount=Decimal("1500.00")),
                ],
            ),
        )
        assert breakdown.earnings.net_pay == Decimal("-200.00")
        # Verify the math: 2000 - 1500 - 700 = -200
        expected = (
            breakdown.earnings.gross_biweekly
            - breakdown.deductions.total_pre_tax
            - breakdown.taxes.total
            - breakdown.deductions.total_post_tax
        )
        assert expected == Decimal("-200.00")


# ── Transaction.days_until_due / days_paid_before_due ──────────────


class TestDaysUntilDue:
    """Tests for Transaction.days_until_due computed property."""

    def _make_txn(
        self, seed_user, seed_periods, status_name, due_date_val, is_envelope=False,
    ):
        """Helper: create a transaction with given status and due_date.

        *is_envelope* makes its definition track purchases, so a card
        purchase under it mints a payback.
        """
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        txn = one_off_row_of(
            seed_periods[0],
            name="Test Due",
            amount=Decimal("100.00"),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
            due_date=due_date_val,
            is_envelope=is_envelope,
        )
        txn.status_id = status.id
        db.session.flush()
        return txn

    def test_days_until_due_future(self, app, db, seed_user, seed_periods):
        """Projected transaction with future due_date returns positive days."""
        with app.app_context():
            future = add_months(date.today(), 12)
            txn = self._make_txn(seed_user, seed_periods, "Projected", future)
            assert txn.days_until_due is not None
            assert txn.days_until_due > 0

    def test_days_until_due_past(self, app, db, seed_user, seed_periods):
        """Projected transaction with past due_date returns negative days."""
        with app.app_context():
            past = date(2020, 1, 1)
            txn = self._make_txn(seed_user, seed_periods, "Projected", past)
            assert txn.days_until_due is not None
            assert txn.days_until_due < 0

    def test_days_until_due_settled(self, app, db, seed_user, seed_periods):
        """Settled transaction returns None -- no action needed."""
        with app.app_context():
            future = add_months(date.today(), 12)
            txn = self._make_txn(seed_user, seed_periods, "Paid", future)
            assert txn.days_until_due is None

    def test_days_until_due_no_due_date(self, app, db, seed_user, seed_periods):
        """An UNDATED row returns None.

        The undated row is a card payback, which its producer writes with no
        due date: a placed one-off is dated on its paycheck's start (R-BAL22)
        and the cutover (plan step balance:X-bi-7d-2) dated every legacy one
        the same way, so a row NO definition prices is what still reaches
        the ``None`` arm.  It was a transfer's shadow until plan step
        ``balance:X-bi-6-4d-2`` refused the shadow, and the card cutover
        (``credit_card:CC-7``, rulings **R-CC17** / **R-CC20**) deletes the
        payback, so this case goes with the arm.  The payback is Projected,
        so the settled arm, which also answers ``None``, cannot answer for
        it.
        """
        with app.app_context():
            envelope = self._make_txn(
                seed_user, seed_periods, "Projected",
                seed_periods[0].start_date, is_envelope=True,
            )
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("40.00"),
                seed_periods[0].start_date,
            )
            db.session.flush()
            assert payback.due_date is None
            assert payback.status.is_settled is False
            assert payback.days_until_due is None


class TestSettleDayRefusesAnInstant:
    """The COLUMN refuses a ``datetime``, on every ORM write path (N-179).

    ``datetime`` subclasses ``date``, so the type annotation catches nothing and
    the value reaches PostgreSQL, which coerces it into the ``DATE`` column on
    the SESSION clock -- UTC.  An instant at 2026-03-04 04:30 UTC is
    2026-03-03 23:30 Eastern, so the row would store 2026-03-04: one day late,
    silently, which is the split ruling R-DH (b) exists to delete.

    **These pin the VALIDATOR, and they exist because nothing did.**  The seam
    calls :func:`~app.models.mixins.reject_settle_instant` itself, ahead of
    any assignment, so ``test_status_seam.py``'s refusal test proves the SEAM
    and cannot reach the column hook at all -- it asserts the row was left
    untouched, i.e. that the assignment never ran.  Deleting the ``@validates``
    decorator therefore regressed nothing detectable, which is finding N-182's
    own shape (a guarantee with no pin) inside the fix for N-179.  The hook is
    what makes the claim "the column simply does not accept the type" true of a
    future fixture or service that writes the attribute directly.
    """

    def test_a_plain_assignment_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """``txn.settled_on = <datetime>`` raises before anything is stored."""
        with app.app_context():
            status = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            txn = one_off_row_of(
                seed_periods[0],
                name="Instant refusal",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            txn.status_id = status.id
            for _column, _value in settle_day_columns(seed_periods[0].start_date).items():
                setattr(txn, _column, _value)
            db.session.flush()
            cover_bare_settled_row(db.session, txn, "100.00")

            # The BARE column assignment, which is exactly the path the
            # validator exists for: ``SettleDay`` refuses an instant at
            # construction, so a caller going through the pair writer never
            # reaches the column at all (plan step X-az).  This is the fixture
            # or service that writes the attribute directly.
            with pytest.raises(TypeError, match="must be a date"):
                txn.settled_on = datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc)
            # The stored day is untouched: the validator runs before the set.
            assert txn.settled_on == seed_periods[0].start_date

    def test_a_constructor_kwarg_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """``Transaction(settled_on=<datetime>)`` raises at construction.

        The declarative constructor assigns through ``setattr``, so the same
        validator covers it -- which is the path a fixture is most likely to
        take, and the one the X-f1 conversion actually took 16 times.

        A bare ``Transaction(`` on purpose, and never added: the CONSTRUCTOR
        call is the subject (the sibling above grades the plain assignment),
        so no builder can stand in for it -- a stay of the same kind as
        ``test_state_machine``'s unsaved type token (plan step
        balance:X-bi-7c; 7d's CHECK never sees a row that is never flushed).
        """
        with app.app_context():
            status = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            with pytest.raises(TypeError, match="must be a date"):
                Transaction(
                    user_id=seed_periods[0].user_id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=status.id,
                    name="Instant refusal",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    settled_on=datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc),
                )

    def test_a_civil_date_and_none_both_pass_through(self):
        """The rule refuses instants only -- it is not a general type gate.

        Asserted on the shared function rather than through the ORM so the
        pass-through arms are stated where the refusal is, and so a future
        caller can see that ``None`` (an unsettled row) is legal.
        """
        assert reject_settle_instant(date(2026, 3, 4)) == date(2026, 3, 4)
        assert reject_settle_instant(None) is None


class TestDaysPaidBeforeDue:
    """Tests for Transaction.days_paid_before_due computed property."""

    def _make_txn(self, seed_user, seed_periods, due_date_val, settled_on_val):
        """Helper: create a transaction with given due_date and settle day."""
        status = db.session.query(Status).filter_by(name="Paid").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        txn = one_off_row_of(
            seed_periods[0],
            name="Test Paid Timing",
            amount=Decimal("100.00"),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
            due_date=due_date_val,
        )
        txn.status_id = status.id
        for _column, _value in settle_day_columns(settled_on_val).items():
            setattr(txn, _column, _value)
        db.session.flush()
        if settled_on_val is not None:
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, "100.00")
        return txn

    def test_days_paid_before_due_early(self, app, db, seed_user, seed_periods):
        """Paid 3 days before due returns 3 (positive = early)."""
        with app.app_context():
            due = date(2026, 3, 15)
            paid = date(2026, 3, 12)
            txn = self._make_txn(seed_user, seed_periods, due, paid)
            assert txn.days_paid_before_due == 3

    def test_days_paid_before_due_late(self, app, db, seed_user, seed_periods):
        """Paid 2 days after due returns -2 (negative = late)."""
        with app.app_context():
            due = date(2026, 3, 15)
            paid = date(2026, 3, 17)
            txn = self._make_txn(seed_user, seed_periods, due, paid)
            assert txn.days_paid_before_due == -2

    def test_days_paid_before_due_no_settle_day(self, app, db, seed_user, seed_periods):
        """No settle day returns None -- an unsettled row's timeliness is moot."""
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods,
                due_date_val=date(2026, 3, 15),
                settled_on_val=None,
            )
            assert txn.days_paid_before_due is None

    def test_days_paid_before_due_is_exact_civil_date_arithmetic(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle ON the due date is on time (0), with no timezone in it.

        **This test used to carry the F3 rule and no longer can, by
        construction.**  It pinned an 8:05pm-Eastern settle -- stored as
        2026-01-16 01:05 UTC -- reporting 0 against a 2026-01-15 due date,
        because the property converted the instant to the display timezone
        before truncating it; truncating in UTC reported -1, a day late by
        wall-clock drift.  Plan step X-f1 removed the conversion along with the
        instant: both operands are civil dates now and the subtraction is exact.

        **The rule did not disappear, it MOVED to the write door**, which is the
        only place an instant still becomes a day:
        ``test_status_seam.py::TestApplyStatusChangeSettleDay::``
        ``test_the_stamped_day_is_the_users_day_not_the_process_utc_day``
        freezes the clock at an evening-Eastern instant and asserts the seam
        records the Eastern day.  Naming that here matters: a reader who finds
        this test looking for F3's coverage must be sent to where it lives, not
        left thinking it was dropped.
        """
        with app.app_context():
            due = date(2026, 1, 15)
            txn = self._make_txn(seed_user, seed_periods, due, due)
            assert txn.days_paid_before_due == 0
