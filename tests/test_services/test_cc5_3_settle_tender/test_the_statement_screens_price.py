"""A statement screen prices a row where its money moved (R-CC40, half 1).

The money moved on Savings, paid from under a Checking bill: since ruling
plan#3227 "Paid from" names a cash account, never a card, and nothing priced
here is a card's alone.  It moved on the card until then.
"""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.services import pay_calendar, status_seam
from app.services.cash_ledger import derived_amount_basis
from app.services.statement_match import candidates_for
from app.services.statement_match import RowKind, _accepted_view, _valuation
from app.services.transaction_service import settle_transaction

from tests._cards import add_savings

from ._helpers import _HOTEL, _hotel_bill, _correct_tender


class TestAStatementScreenPricesARowWhereItsMoneyMoved:
    """Ruling **R-CC40**, half 1: a settled row is worth its movement's cash ON THE ACCOUNT ASKED."""

    @staticmethod
    def _offered_for(seed_user, account_id, txn):
        """Return what *account_id*'s screen offers FOR *txn*, by either subject.

        A Projected row is offered as itself and a settled row as its
        payment (plan step ``credit_card:CC-5-4a-1``, ruling **R-CC43**);
        :attr:`~app.services.statement_match.CandidateRow.transaction_id`
        names the row under both kinds.
        """
        rows = candidates_for(
            account_id, pay_calendar.calendar_for(seed_user["user"].id),
            derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id),
        ).rows
        return [row for row in rows if row.transaction_id == txn.id]

    def test_covered_cash_leg_is_the_movements_account_alone(
        self, app, seed_user, seed_periods,
    ):
        """A Checking bill paid from Savings: `-120.00` on Savings, nothing on Checking."""
        with app.app_context():
            checking = seed_user["account"]
            savings = add_savings(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=savings.id)
            db.session.commit()
            assert status_seam.covered_cash_leg(txn, savings.id) == -_HOTEL
            assert status_seam.covered_cash_leg(txn, checking.id) == Decimal("0")

    def test_a_bill_paid_elsewhere_leaves_the_checking_offer(
        self, app, seed_user, seed_periods,
    ):
        """Checking's feed never shows the money, so checking's screen does not offer it.

        Before ``CC-5-3`` the row was offered there at `-120.00` and a stray
        `$120` line could be paired with it in one click.  **The paying
        account's screen offers the PAYMENT** (plan step
        ``credit_card:CC-5-4a-1``, ruling **R-CC43**, half 2 of **R-CC40**):
        the covering movement, as a SETTLEMENT on the row's terms -- the
        bill's figure, the bill's paycheck, labelled with the account the
        bill is budgeted on.  This pinned the interim (nothing on the paying
        account's screen either) through ``CC-5-3``, saying the change would
        be explicit; this is it.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = add_savings(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            db.session.commit()
            offered_before = self._offered_for(seed_user, checking.id, txn)
            assert [row.cash_amount for row in offered_before] == [-_HOTEL]
            assert [row.kind for row in offered_before] == [RowKind.SETTLEMENT]

            _correct_tender(txn, savings.id)

            assert self._offered_for(seed_user, checking.id, txn) == []
            (on_savings,) = self._offered_for(seed_user, savings.id, txn)
            (movement,) = txn.covering_movements
            assert on_savings.kind is RowKind.SETTLEMENT
            assert on_savings.row_id == movement.id
            assert on_savings.cash_amount == -_HOTEL
            assert on_savings.is_settled is True
            assert on_savings.settled_on == txn.settled_on
            assert on_savings.label == f"{txn.name} (budgeted on {checking.name})"
            period = pay_calendar.calendar_for(
                seed_user["user"].id,
            ).period_by_id(txn.pay_period_id)
            assert on_savings.period == period

    def test_the_matchers_price_asks_the_screens_account_not_the_rows(
        self, app, seed_user, seed_periods,
    ):
        """The producer's contract, graded where the offer set cannot reach it.

        Every candidate the offer set builds is on the screen's account by
        the scope's own clause, so a constructor that never asked would
        agree with one that did on every reachable path -- two spellings
        that agree by a clause elsewhere, which is rule 14's tell and one
        refactor of the scope from parting.  So the contract is pinned
        directly on the SETTLEMENT constructor (plan step
        ``credit_card:CC-5-4a-1``, ruling **R-CC43**: the payment is the
        candidate, priced as a movement): asked for Savings' screen, a
        checking bill's payment on Savings is a candidate worth its figure;
        asked for checking's, nothing -- which is also what the accept
        door's re-price answers for a payment re-pointed elsewhere since the
        screen offered it, so that act is refused rather than written
        against the member key.  The accepted register's reader carries the
        account for the same reason, for a member naming the payment -- the
        one member shape since ``CC-5-4a-2`` (ruling **R-CC45**), which
        deleted the row-member lines this case asserted beside it.  Through
        ``CC-5-3`` this pinned the row-pricer's settled arm, which R-CC43
        deleted.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."  And, for the four register(txn, ...) row-valuation
        lines this deleted: "Confirm the 4 row-valuation lines -- deleting the 4 register(txn, ...)
        lines is a rule-5 re-expression under R-CC45. The paired register(movement, ...) lines,
        with the same -$120.00 / $0.00 figures, carry the assertion."
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = add_savings(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=savings.id)
            db.session.commit()
            (movement,) = txn.covering_movements
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            assert _valuation.settlement_price(movement, basis) == -_HOTEL
            on_savings = _valuation.settlement_candidate(
                movement, calendar, -_HOTEL, savings.id,
            )
            assert on_savings is not None and on_savings.cash_amount == -_HOTEL
            assert _valuation.settlement_candidate(
                movement, calendar, -_HOTEL, checking.id,
            ) is None
            assert _valuation.repriced(on_savings, calendar, basis, savings.id) == on_savings
            assert _valuation.repriced(on_savings, calendar, basis, checking.id) is None
            register = _accepted_view._accepted_row  # pylint: disable=protected-access
            assert register(movement, txn.settled_on, savings.id).cash_amount == -_HOTEL
            assert register(movement, txn.settled_on, checking.id).cash_amount == Decimal("0")

    def test_a_member_whose_tender_moved_reads_zero_on_the_register_and_at_re_pricing(
        self, app, seed_user, seed_periods,
    ):
        """The accepted register and the accept door's re-price agree: the match stops holding.

        Graded on the member shape every act records since plan step
        ``credit_card:CC-5-4a-1`` (ruling **R-CC43**: the payment) -- the one
        shape since ``CC-5-4a-2`` re-keyed the acts before it and dropped the
        row-member lines this case asserted beside it (ruling **R-CC45**).
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."  And, for the four register(txn, ...) row-valuation
        lines this deleted: "Confirm the 4 row-valuation lines -- deleting the 4 register(txn, ...)
        lines is a rule-5 re-expression under R-CC45. The paired register(movement, ...) lines,
        with the same -$120.00 / $0.00 figures, carry the assertion."
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = add_savings(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            db.session.commit()
            (movement,) = txn.covering_movements
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            candidate = _valuation.settlement_candidate(
                movement, calendar, -_HOTEL, checking.id,
            )
            assert candidate is not None
            register = _accepted_view._accepted_row  # pylint: disable=protected-access
            assert register(movement, txn.settled_on, checking.id).cash_amount == -_HOTEL
            assert _valuation.repriced(candidate, calendar, basis, checking.id) is not None

            _correct_tender(txn, savings.id)

            assert register(movement, txn.settled_on, checking.id).cash_amount == Decimal("0")
            assert _valuation.repriced(candidate, calendar, basis, checking.id) is None
