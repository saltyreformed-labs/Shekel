"""Which account a settle books on, which tender the door admits, the tender kept across a revert,
and a Paid from correction on a settled row.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.row_valuation import settled_figure
from app.services.transaction_service import settle_transaction
from app.services.ledger_lines import linked_key
from tests._ledger_reads import ledger_net
from tests._test_helpers import create_account_of_type, create_loan_account, typed

from ._helpers import (
    _HOTEL,
    _card,
    _hotel_bill,
    _movement,
    _revert,
    _per_day,
    _latest_anchor,
    _correct_tender,
)


class TestASettleBooksOnTheTender:
    """The covering movement's account is the tender's, else the row's."""

    def test_no_tender_named_books_on_the_rows_account(
        self, app, seed_user, seed_periods,
    ):
        """Every caller but the picker states nothing: the row's own account.

        The byte-identical default -- the cell's checkmark, the mobile card's
        Mark Paid, every settle through ``CC-5-2`` -- and the tuple's first
        member (ruling **R-CC39**).
        """
        with app.app_context():
            _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            db.session.commit()
            assert _movement(txn).account_id == txn.account_id

    def test_a_bill_charged_to_the_card_holds_its_movement_on_the_card(
        self, app, seed_user, seed_periods,
    ):
        """The developer's worked case, read by every reader that counts money.

        The row stays on Checking (its paycheck budgets it); its ONE movement
        is on the card, owned by the row's owner, dated the act's day on the
        ``entered`` basis; the card's fold reads `-120.00` that day and
        checking's reads nothing; the posted ledger books the leg against the
        CARD's linked ledger and nothing against checking's; and the row is
        worth `$120.00` to the grid exactly as a checking-settled bill is.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            scenario_id = txn.scenario_id
            checking_ledger = linked_key(checking)
            card_ledger = linked_key(card)
            checking_before = ledger_net(db.session, checking_ledger, scenario_id)
            card_before = ledger_net(db.session, card_ledger, scenario_id)

            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()

            movement = _movement(txn)
            assert txn.account_id == checking.id
            assert movement.account_id == card.id
            assert movement.owner_id == txn.user_id
            assert movement.amount == _HOTEL
            assert movement.settled_on == txn.settled_on
            assert txn.settled_on is not None
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ENTERED,
            )
            on_the_card = _per_day(card.id, scenario_id)
            on_checking = _per_day(checking.id, scenario_id)
            assert on_the_card[txn.settled_on] == -_HOTEL
            assert txn.settled_on not in on_checking
            assert ledger_net(db.session, card_ledger, scenario_id) - card_before == -_HOTEL
            assert ledger_net(db.session, checking_ledger, scenario_id) == checking_before
            assert settled_figure(txn) == _HOTEL

    def test_the_tender_may_be_the_rows_own_account_stated_outright(
        self, app, seed_user, seed_periods,
    ):
        """Naming the row's own account is the default, said aloud: admitted, same result."""
        with app.app_context():
            _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=txn.account_id)
            db.session.commit()
            assert _movement(txn).account_id == txn.account_id

    def test_a_row_living_outside_the_set_may_be_paid_from_the_card(
        self, app, seed_user, seed_periods,
    ):
        """A Savings bill paid from the card: the union (ruling **R-CC39**), one rule for every row."""
        with app.app_context():
            card = _card(seed_user)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            txn = _hotel_bill(seed_user, seed_periods[0], account=savings)
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            assert _movement(txn).account_id == card.id


class TestTheTenderGate:
    """Foreign -> 404; archived or outside the picker's set -> refused; nothing written."""

    @staticmethod
    def _still_projected(txn):
        """The refusal fired before ANY mutation: nothing staged, nothing stored.

        Read BEFORE the rollback -- a rollback cannot tell a refusal that
        mutated from one that did not -- and again from the database after
        it.  The gate runs ahead of the seam, so the session holds no new
        movement and no dirtied row at the raise.
        """
        assert not db.session.new
        assert not db.session.dirty
        assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        db.session.rollback()
        db.session.expire_all()
        fresh = db.session.get(Transaction, txn.id)
        assert fresh.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        assert fresh.settled_on is None
        assert fresh.covering_movements == []

    def test_another_owners_account_is_not_found(
        self, app, seed_user, seed_periods, second_user,
    ):
        """The ROW's owner gates, and a foreign account answers the security rule's 404."""
        with app.app_context():
            other_card = create_account_of_type(
                second_user, db.session, "Credit Card", "Their Card",
                anchor_balance=Decimal("-10.00"),
            )
            db.session.commit()
            txn = _hotel_bill(seed_user, seed_periods[0])
            with pytest.raises(NotFoundError, match=r"^Account not found\.$"):
                settle_transaction(txn, tender_account_id=other_card.id)
            self._still_projected(txn)

    def test_a_missing_account_is_not_found_the_same_way(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _hotel_bill(seed_user, seed_periods[0])
            with pytest.raises(NotFoundError, match=r"^Account not found\.$"):
                settle_transaction(txn, tender_account_id=999_999)
            self._still_projected(txn)

    def test_an_archived_card_is_refused(self, app, seed_user, seed_periods):
        with app.app_context():
            card = _card(seed_user)
            card.is_active = False
            db.session.commit()
            txn = _hotel_bill(seed_user, seed_periods[0])
            with pytest.raises(ValidationError, match=r"archived, so a payment"):
                settle_transaction(txn, tender_account_id=card.id)
            self._still_projected(txn)

    def test_an_account_outside_the_pickers_set_is_refused_per_kind(
        self, app, seed_user, seed_periods,
    ):
        """A loan, a 401(k), savings, a second checking: unwritable, one sentence (R-CC37 / R-CC39)."""
        with app.app_context():
            _card(seed_user)
            outsiders = [
                create_loan_account(seed_user, db.session, name="Car Loan"),
                create_account_of_type(seed_user, db.session, "401(k)", "Work 401k"),
                create_account_of_type(seed_user, db.session, "Savings", "Rainy Day"),
                create_account_of_type(
                    seed_user, db.session, "Checking", "Second Checking",
                ),
            ]
            db.session.commit()
            for account in outsiders:
                txn = _hotel_bill(seed_user, seed_periods[0])
                assert account.is_active and account.user_id == txn.user_id
                with pytest.raises(
                    ValidationError,
                    match=r"not an account a payment can be paid from",
                ):
                    settle_transaction(txn, tender_account_id=account.id)
                self._still_projected(txn)

    def test_a_foreign_tender_on_a_replayed_settle_is_still_a_404(
        self, app, seed_user, seed_periods, second_user,
    ):
        """A bad REFERENCE is refused whatever the row's state, never a silent no-op."""
        with app.app_context():
            other_card = create_account_of_type(
                second_user, db.session, "Credit Card", "Their Card",
                anchor_balance=Decimal("-10.00"),
            )
            db.session.commit()
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            db.session.commit()
            with pytest.raises(NotFoundError):
                settle_transaction(txn, tender_account_id=other_card.id)


class TestTheTenderIsRetainedAcrossARevert:
    """Ruling **R-CC42**: where the money moved survives a revert as a stated figure does."""

    def test_a_reverted_card_bill_re_settles_on_the_card_when_nothing_is_named(
        self, app, seed_user, seed_periods,
    ):
        """The developer's case: revert, then the one-click checkmark: still on the card.

        Refused alternative (R-CC36's letter): the re-settle would have booked
        the `$120` on Checking -- the card `$120` low, checking `$120` high,
        and the card's statement line with nothing to match.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            first_id = _movement(txn).id

            _revert(txn)
            kept = _movement(txn)
            assert kept.settled_on is None
            assert kept.account_id == card.id
            assert status_seam.tender_account_id_of(txn) == card.id

            settle_transaction(txn)
            db.session.commit()
            movement = _movement(txn)
            assert movement.id == first_id
            assert movement.account_id == card.id
            scenario_id = txn.scenario_id
            assert _per_day(card.id, scenario_id)[txn.settled_on] == -_HOTEL
            assert txn.settled_on not in _per_day(checking.id, scenario_id)

    def test_naming_a_tender_at_the_re_settle_moves_the_kept_movement(
        self, app, seed_user, seed_periods,
    ):
        """Only a NAMED tender moves it: the same movement, re-pointed onto checking."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            first_id = _movement(txn).id
            _revert(txn)

            settle_transaction(txn, tender_account_id=checking.id)
            db.session.commit()
            movement = _movement(txn)
            assert movement.id == first_id
            assert movement.account_id == checking.id
            scenario_id = txn.scenario_id
            assert _per_day(checking.id, scenario_id)[txn.settled_on] == -_HOTEL
            assert txn.settled_on not in _per_day(card.id, scenario_id)

    def test_a_row_holding_no_record_defaults_to_its_own_account(
        self, app, seed_user, seed_periods,
    ):
        """The default's other arm, read off the one producer the popover preselects from."""
        with app.app_context():
            txn = _hotel_bill(seed_user, seed_periods[0])
            assert status_seam.tender_account_id_of(txn) == txn.account_id


class TestATenderCorrectionOnASettledRow:
    """The popover's 'Paid from' on a settled row: the identity arm re-points the payment."""

    def test_the_movement_moves_and_both_clearing_links_are_released(
        self, app, seed_user, seed_periods,
    ):
        """A checking-settled bill, linked by checking's statement, corrected to the card.

        The same movement (id kept) books on the card; the link the row and
        its movement carried to checking's statement is withdrawn on both
        (a statement of checking never showed money that moved on the card,
        and the movement's key would refuse the pair); the row's version
        counter moves, so a second stale tab meets a 409 rather than
        overwriting the correction; and the ledger follows: checking's line
        is back where it was and the card's carries the payment.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            scenario_id = txn.scenario_id
            checking_ledger = linked_key(checking)
            card_ledger = linked_key(card)
            checking_before = ledger_net(db.session, checking_ledger, scenario_id)
            card_before = ledger_net(db.session, card_ledger, scenario_id)
            settle_transaction(txn)
            status_seam.record_clearing(txn, _latest_anchor(checking.id).id)
            db.session.commit()
            movement = _movement(txn)
            assert movement.reconciled_by_id is not None
            assert txn.reconciled_by_id is not None
            assert ledger_net(db.session, checking_ledger, scenario_id) - checking_before == -_HOTEL
            first_id, version_before = movement.id, txn.version_id

            _correct_tender(txn, card.id)

            movement = _movement(txn)
            assert movement.id == first_id
            assert movement.account_id == card.id
            assert movement.reconciled_by_id is None
            assert txn.reconciled_by_id is None
            assert txn.version_id == version_before + 1
            assert movement.settled_on == txn.settled_on
            assert _per_day(card.id, scenario_id)[txn.settled_on] == -_HOTEL
            assert txn.settled_on not in _per_day(checking.id, scenario_id)
            assert ledger_net(db.session, checking_ledger, scenario_id) == checking_before
            assert ledger_net(db.session, card_ledger, scenario_id) - card_before == -_HOTEL

    def test_an_echo_of_the_recorded_tender_writes_nothing(
        self, app, seed_user, seed_periods,
    ):
        """An untouched picker re-states the record: no re-point, no version bump, link kept."""
        with app.app_context():
            checking = seed_user["account"]
            _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            anchor_id = _latest_anchor(checking.id).id
            status_seam.record_clearing(txn, anchor_id)
            db.session.commit()
            version_before = txn.version_id

            _correct_tender(txn, checking.id)

            movement = _movement(txn)
            assert movement.account_id == checking.id
            assert movement.reconciled_by_id == anchor_id
            assert txn.reconciled_by_id == anchor_id
            assert txn.version_id == version_before

    def test_a_tender_beside_a_revert_is_refused_and_an_echo_beside_one_is_dropped(
        self, app, seed_user, seed_periods,
    ):
        """The figure's rule (ruling **R-EG**) applied to the account: refuse a change, drop an echo."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            db.session.commit()
            projected = ref_cache.status_id(StatusEnum.PROJECTED)

            with pytest.raises(ValidationError, match=r"is not settling, so a 'Paid from'"):
                transaction_service.apply_requested_status(
                    txn, projected,
                    stated=transaction_service.StatedRecord(tender_account_id=card.id),
                )
            db.session.rollback()
            db.session.expire_all()
            fresh = db.session.get(Transaction, txn.id)
            assert fresh.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert _movement(fresh).account_id == checking.id

            transaction_service.apply_requested_status(
                fresh, projected,
                stated=transaction_service.StatedRecord(tender_account_id=checking.id),
            )
            db.session.commit()
            assert fresh.status_id == projected
            assert _movement(fresh).settled_on is None

    def test_a_tender_on_a_row_settled_from_its_purchases_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """An envelope's purchases each carry their own account: no ONE payment to move."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0], is_envelope=True)
            from app.services import entry_service  # pylint: disable=import-outside-toplevel
            entry_service.create_entry(
                txn.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("40.00")), description="Deposit",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            settle_transaction(txn)
            db.session.commit()
            assert txn.covering_movements == []
            with pytest.raises(ValidationError, match=r"no single account its money moved through"):
                transaction_service.apply_requested_status(
                    txn, txn.status_id,
                    stated=transaction_service.StatedRecord(tender_account_id=card.id),
                )

    def test_a_tender_on_a_close_of_nothing_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """A `$0.00` close records no movement, so there is nothing to book elsewhere."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, submitted=typed(Decimal("0.00")))
            db.session.commit()
            assert txn.covering_movements == []
            with pytest.raises(ValidationError, match=r"records no payment movement"):
                transaction_service.apply_requested_status(
                    txn, txn.status_id,
                    stated=transaction_service.StatedRecord(tender_account_id=card.id),
                )

    def test_the_settle_verb_ignores_a_tender_on_the_entries_branch(
        self, app, seed_user, seed_periods,
    ):
        """An envelope's close takes no tender: a named one is dropped, as a stated figure is.

        No door names one here since plan step ``credit_card:CC-5-4a-5``:
        the reconcile panel's tick named the statement's account for every
        row until its leaf 5c-1 (ruling **R-CC126**), and the popover renders
        no 'Paid from' picker on a row settling from its purchases.
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0], is_envelope=True)
            from app.services import entry_service  # pylint: disable=import-outside-toplevel
            entry_service.create_entry(
                txn.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("40.00")), description="Deposit",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.covering_movements == []
            assert [e.account_id for e in txn.purchases] == [txn.account_id]
