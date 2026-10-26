"""A re-point or a deleted bill withdraws the match naming the payment, and the bank agreement
reads a payment's claim through the movement.
"""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import bank_agreement, match_press, match_withdrawal, transaction_service
from app.services.balance_at import BalanceContext
from app.services.statement_match import RowKind, matched_subjects
from app.services.transaction_service import settle_transaction
from tests._test_helpers import generate_row_of, make_expense_template
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_transaction,
    accepted_acts,
    an_import,
)

from ._helpers import (
    _HOTEL,
    _card,
    _hotel_bill,
    _movement,
    _correct_tender,
    _entered,
    _first_day,
    _offered_for,
    _accept,
    _members_of,
)


class TestARePointWithdrawsTheMatchNamingThePayment:
    """Ruling **R-CC46**: the correction goes through; the match it falsifies goes."""

    def test_a_paid_from_correction_withdraws_the_match_and_frees_the_line(
        self, app, seed_user,
    ):
        """The disclosure before, the withdrawal at the seam, the line free after."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()
            _accept(seed_user, checking, [line], [txn])
            movement = _movement(txn)

            pending = match_withdrawal.pending_for_movements([movement])
            assert pending.matches == 1
            assert [freed.line_id for freed in pending.lines] == [line.id]
            assert pending.lines[0].description == "GROCERIES"
            assert pending.lines[0].amount == Decimal("-120.00")
            assert db.session.query(StatementMatch).count() == 1

            _correct_tender(txn, card.id)

            assert _movement(txn).account_id == card.id
            assert db.session.query(StatementMatch).count() == 0
            assert db.session.query(StatementMatchMember).count() == 0
            assert matched_subjects(checking.id).lines == set()
            # ...and the payment is the CARD screen's candidate now.
            (on_card,) = _offered_for(seed_user, card, txn)
            assert on_card.kind is RowKind.SETTLEMENT
            assert _offered_for(seed_user, checking, txn) == []

    def test_a_group_act_keeps_standing_without_the_moved_member(
        self, app, seed_user,
    ):
        """The narrowest condition: an act keeping another row stays, flagged."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            hotel = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            other = generate_row_of(
                make_expense_template(
                    db.session, seed_user, amount="80.00",
                    name="Parking", category_key="Rent",
                ),
                seed_user["bootstrap_period"],
            )
            db.session.commit()
            for row in (hotel, other):
                settle_transaction(row, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-200.00", posted_on=bank_day,
            )
            db.session.commit()
            accepted = _accept(seed_user, checking, [line], [hotel, other])
            assert len(_members_of(accepted.match_id)) == 3

            pending = match_withdrawal.pending_for_movements([_movement(hotel)])
            assert pending.matches == 0 and pending.lines == ()

            _correct_tender(hotel, card.id)

            assert db.session.query(StatementMatch).count() == 1
            members = _members_of(accepted.match_id)
            assert {m.transaction_entry_id for m in members} == {
                None, _movement(other).id,
            }
            (group,) = accepted_acts(seed_user, checking)
            assert group.agrees is False
            assert matched_subjects(checking.id).lines == {line.id}

    def test_an_unmatched_payments_re_point_withdraws_nothing(
        self, app, seed_user,
    ):
        """The ordinary case, so the door's cost is a query and a no-op."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(_first_day(seed_user)))
            db.session.commit()

            pending = match_withdrawal.pending_for_movements([_movement(txn)])
            assert pending.matches == 0 and not pending.frees_a_line
            _correct_tender(txn, card.id)

            assert _movement(txn).account_id == card.id


class TestADeletedBillWithdrawsTheActNamingItsPaymentOnAnotherAccount:
    """The withdrawal's scope is the going SUBJECTS, not the going rows' account."""

    def test_deleting_a_checking_bill_matched_on_the_card_withdraws_the_cards_act(
        self, app, seed_user,
    ):
        """An AD-HOC bill (a hard delete; a soft one withdraws nothing by design)."""
        with app.app_context():
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = a_transaction(
                seed_user, name="Hotel", amount="120.00", template=False,
            )
            db.session.commit()
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()
            _accept(seed_user, card, [line], [txn])

            pending = match_withdrawal.pending_for_rows([txn])
            assert pending.matches == 1
            assert [freed.line_id for freed in pending.lines] == [line.id]

            with match_press.Press(match_press.Shown(
                    transaction_service.preview_deletion(txn).withdrawn.line_ids,
                )) as press:
                outcome = transaction_service.delete_transaction(
                    txn, seed_user["user"].id,
                    press=press,
                )
            db.session.commit()

            assert outcome.soft is False
            assert outcome.withdrawn.matches == 1
            assert db.session.query(StatementMatch).count() == 0
            assert matched_subjects(card.id).lines == set()


class TestTheBankAgreementReadsAPaymentsClaimThroughTheMovement:
    """The day detail's match state for a covering fact, under both member homes."""

    def test_a_matched_payment_reads_as_matched_on_the_cards_day(
        self, app, seed_user,
    ):
        with app.app_context():
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            before = bank_agreement.day_detail(card, ctx, bank_day)
            assert [(row.amount, row.matched) for row in before.rows] == [
                (-_HOTEL, False),
            ]

            _accept(seed_user, card, [line], [txn])

            after = bank_agreement.day_detail(
                card, BalanceContext.build(seed_user["user"].id), bank_day,
            )
            assert [(row.amount, row.matched) for row in after.rows] == [
                (-_HOTEL, True),
            ]
            assert [ln.matched for ln in after.lines] == [True]
