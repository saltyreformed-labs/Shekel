"""
Shekel Budget App -- Undo CC takes the row off the card (plan task plan#3055)

Ruling **plan#2887**, "Paid means paid": a Credit press puts the row on the card
(plan#2929), stored Paid with its payment on the card and drawn Credit
(plan#3054).  Undo CC says the money did not go on the card, so it sets the row
back to Projected and the status seam moves the payment it keeps onto the row's
own account (``status_seam._covering._move_home``); a later Paid leaves
checking, and a later Credit puts it back on the card.  Setting Status back to
Projected is the same act (``transaction_service.apply_requested_status``, the
door ``undo_credit`` takes).  It amends ruling **R-CC42** for this state only.

**The one example**: $800 of groceries dated Jan 3, its row in the Jan 2
paycheck, on a card whose books open before Jan 2, closing on the 5th and due
on the 27th.  Checking opens at $1,000.00.  Each figure is worked in the test
that asserts it.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import cash_ledger, credit_workflow, status_seam, transaction_service
from app.services.ledger_lines import linked_key
from app.services.settle_day import SettleDay
from tests._ledger_parity import assert_ledger_parity
from tests._ledger_reads import ledger_net
from tests._legacy_credit import plant_old_credit
from tests._test_helpers import typed
from tests.test_services.test_card_payment_follows_what_is_owed import _balances
from tests.test_services.test_card_payment_price import _card
from tests.test_services.test_credit_puts_the_row_on_the_card import _groceries, _press

_ZERO = Decimal("0.00")
_EIGHT_HUNDRED = Decimal("800.00")
_JAN_3 = date(2026, 1, 3)
_JAN_10 = date(2026, 1, 10)


def _status(member):
    """The ``ref.statuses.id`` of *member*."""
    return ref_cache.status_id(member)


def _undo(row):
    """Press Undo CC on *row* and commit; return the row read fresh."""
    transaction_service.undo_credit(row)
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transaction, row.id)


def _paid_on(row, day):
    """Mark *row* Paid with nothing named, as the one-click tick does, on *day*."""
    transaction_service.settle_transaction(
        row, settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED),
    )
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transaction, row.id)


class TestUndoCcTakesTheRowOffTheCard:
    """The row goes back to Projected and its payment back to checking."""

    def test_the_row_is_projected_with_its_payment_waiting_on_checking(
        self, seed_user, seed_periods,
    ):
        """The same $800.00 movement (its id kept), un-dated, on checking; drawn Projected."""
        _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))
        movement_id = status_seam.covering_movement_of(row).id

        row = _undo(row)

        movement = status_seam.covering_movement_of(row)
        assert row.status_id == _status(StatusEnum.PROJECTED)
        assert row.settled_on is None
        assert (movement.id, movement.account_id, movement.amount, movement.settled_on) == (
            movement_id, seed_user["account"].id, _EIGHT_HUNDRED, None,
        )
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.PROJECTED)
        assert status_seam.tender_account_id_of(row) == seed_user["account"].id

    def test_the_card_no_longer_owes_it(self, seed_user, seed_periods):
        """Read Jan 10: the card -$800.00 on Jan 3 before Undo CC, 0 - 0 = $0.00 after."""
        card = _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))
        assert _balances(seed_user, card, _JAN_10, _JAN_3)[1] == Decimal("-800.00")

        _undo(row)

        assert _balances(seed_user, card, _JAN_10, _JAN_3)[1] == _ZERO

    def test_a_later_paid_leaves_checking(self, seed_user, seed_periods):
        """Undo CC, then Paid on Jan 10: checking 1,000 - 800 = $200.00, the card $0.00.

        The one-click tick names no account, so it books on the default
        (``tender_account_id_of``), which reads the payment where Undo CC put
        it.  Before this task it stayed on the card (trace S12): checking
        $1,000.00, the card -$800.00, and the row drawn Credit again.
        """
        card = _card(seed_user)
        row = _undo(_press(_groceries(seed_user, seed_periods[0])))

        row = _paid_on(row, _JAN_10)

        assert [m.account_id for m in row.entries] == [seed_user["account"].id]
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.DONE)
        assert _balances(seed_user, card, _JAN_10, _JAN_10) == (Decimal("200.00"), _ZERO)

    def test_a_later_credit_puts_it_back_on_the_card(self, seed_user, seed_periods):
        """Undo CC, then Credit: the same movement on the card from Jan 3 again, -$800.00."""
        card = _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))
        movement_id = status_seam.covering_movement_of(row).id

        row = _press(_undo(row))

        movement = status_seam.covering_movement_of(row)
        assert (movement.id, movement.account_id, movement.settled_on) == (
            movement_id, card.id, _JAN_3,
        )
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.CREDIT)
        assert _balances(seed_user, card, _JAN_10, _JAN_3) == (
            Decimal("1000.00"), Decimal("-800.00"),
        )

    def test_a_figure_typed_on_the_card_survives_undo_cc(self, seed_user, seed_periods):
        """$812.00 typed as what the card took, Undo CC, Paid: $812.00 leaves checking.

        A revert keeps what moved (ruling R-BAL61) and a re-settle honours a
        figure a person typed, so 1,000 - 812 = $188.00 on Jan 10.
        """
        card = _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))
        transaction_service.apply_requested_status(
            row, row.status_id,
            stated=transaction_service.StatedRecord(figure=typed(Decimal("812.00"))),
        )
        db.session.commit()

        row = _paid_on(_undo(row), _JAN_10)

        movement = status_seam.covering_movement_of(row)
        assert (movement.account_id, movement.amount) == (
            seed_user["account"].id, Decimal("812.00"),
        )
        assert movement.figure_source_id == ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.TYPED,
        )
        assert _balances(seed_user, card, _JAN_10, _JAN_10) == (Decimal("188.00"), _ZERO)

    def test_the_card_statements_link_is_released_as_it_leaves_the_card(
        self, seed_user, seed_periods,
    ):
        """A payment the card's statement cleared: Undo CC releases the link, then moves it home.

        A movement's link holds to its own account's assertion
        (``fk_transaction_entries_reconciled_by``), so a move with the link
        standing is unstorable: the revert releases it with the day
        (``_covering._follow_assertion``) before ``_move_home`` re-points the
        payment.  The link is planted as the card's reconcile panel writes
        it, on the card's governing assertion.
        """
        card = _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))
        movement = status_seam.covering_movement_of(row)
        movement.reconciled_by_id = cash_ledger.governing_anchor(card.id).anchor_id
        db.session.commit()
        assert status_seam.covering_movement_of(row).reconciled_by_id is not None

        row = _undo(row)

        movement = status_seam.covering_movement_of(row)
        assert (movement.account_id, movement.settled_on, movement.reconciled_by_id) == (
            seed_user["account"].id, None, None,
        )
        assert row.reconciled_by_id is None
        assert row.status_id == _status(StatusEnum.PROJECTED)

    def test_the_posted_ledger_follows_the_payment_home(self, seed_user, seed_periods):
        """Credit posts -800.00 on the card, Undo CC reverses it, a later Paid posts it on checking.

        Each account's line of the ledger, as a change from before the press:
        the card -800.00 after Credit, then -800 + 800 = $0.00 after Undo CC;
        checking $0.00 until Paid on Jan 10 posts -800.00 there, the card still
        $0.00.  The store agrees at each step, which the parity line grades.
        """
        card = _card(seed_user)
        scenario_id = seed_user["scenario"].id
        ledgers = (linked_key(seed_user["account"]), linked_key(card))
        before = [ledger_net(db.session, ledger, scenario_id) for ledger in ledgers]

        def moved():
            """(checking, card): each account's net change since *before*."""
            return tuple(
                ledger_net(db.session, ledger, scenario_id) - start
                for ledger, start in zip(ledgers, before)
            )

        row = _press(_groceries(seed_user, seed_periods[0]))
        assert moved() == (_ZERO, -_EIGHT_HUNDRED)
        assert_ledger_parity(db.session, scenario_id)
        row = _undo(row)
        assert moved() == (_ZERO, _ZERO)
        assert_ledger_parity(db.session, scenario_id)
        _paid_on(row, _JAN_10)
        assert moved() == (-_EIGHT_HUNDRED, _ZERO)
        assert_ledger_parity(db.session, scenario_id)

    def test_a_second_undo_cc_is_refused_and_changes_nothing(self, seed_user, seed_periods):
        """Undo CC on a row already off the card (a stale tab): refused in words, still on checking."""
        _card(seed_user)
        row = _undo(_press(_groceries(seed_user, seed_periods[0])))

        with pytest.raises(
            ValidationError, match="Cannot unmark credit on a 'Projected' transaction",
        ):
            transaction_service.undo_credit(row)

        db.session.rollback()
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        assert row.status_id == _status(StatusEnum.PROJECTED)
        assert [m.account_id for m in row.entries] == [seed_user["account"].id]

    def test_setting_status_back_to_projected_is_the_same_act(self, seed_user, seed_periods):
        """The Status dropdown's door: the payment on checking, the card back to $0.00."""
        card = _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))

        transaction_service.apply_requested_status(row, _status(StatusEnum.PROJECTED))
        db.session.commit()
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)

        assert row.status_id == _status(StatusEnum.PROJECTED)
        assert [m.account_id for m in row.entries] == [seed_user["account"].id]
        assert _balances(seed_user, card, _JAN_10, _JAN_3)[1] == _ZERO


class TestABillThatLivesOnTheCard:
    """A row whose own account is the card keeps its payment there."""

    def test_undo_cc_leaves_its_payment_on_the_card(self, seed_user, seed_periods):
        """Paid on the card (drawn Credit), Undo CC: Projected, the payment kept on the card.

        The card is the account the bill names (ruling plan#2926), so nothing
        moves home, and a bill on the card counts from its date marked or not
        (plan#2928): the card still reads 0 - 800 = -$800.00 on Jan 3.  Whether
        a later Paid on such a row should leave checking instead is plan#2931's,
        which stops offering Paid on it.
        """
        card = _card(seed_user)
        row = _groceries(seed_user, seed_periods[0], account=card)
        row = _paid_on(row, _JAN_3)
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.CREDIT)
        assert status_seam.revert_moves_payment_home(row) is False

        row = _undo(row)

        movement = status_seam.covering_movement_of(row)
        assert row.status_id == _status(StatusEnum.PROJECTED)
        assert (movement.account_id, movement.settled_on) == (card.id, None)
        assert _balances(seed_user, card, _JAN_10, _JAN_3)[1] == Decimal("-800.00")


class TestEveryOtherRowTakesTheOldPath:
    """``credit_workflow.unmark_credit``: an old Credit row's payback, and the refusal."""

    def test_an_old_credit_rows_payback_is_deleted(self, seed_user, seed_periods):
        """A Credit row holding its CC Payback: Projected, the payback gone."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])
        payback = plant_old_credit(row)
        db.session.commit()
        payback_id = payback.id

        row = _undo(row)

        assert row.status_id == _status(StatusEnum.PROJECTED)
        assert db.session.get(Transaction, payback_id) is None
        assert credit_workflow.get_active_payback(row.id) is None

    def test_a_row_paid_from_checking_is_refused(self, seed_user, seed_periods):
        """Paid on checking is not drawn Credit: refused in words, still Paid."""
        _card(seed_user)
        row = _paid_on(_groceries(seed_user, seed_periods[0]), _JAN_10)

        with pytest.raises(ValidationError, match="Only Credit transactions can be unmarked"):
            transaction_service.undo_credit(row)

        db.session.rollback()
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        assert row.status_id == _status(StatusEnum.DONE)
        assert [m.account_id for m in row.entries] == [seed_user["account"].id]
