"""The books boundary is the tender's account's, and the row's clearing link stays on the row's
account.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import (
    cash_ledger,
    pay_calendar,
    reconcile_service,
    status_seam,
    transaction_service,
)
from app.services.cash_ledger import account_opening_fact
from app.services.settle_day import SettleDay
from app.services.transaction_service import settle_transaction

from ._helpers import (
    _ONE_DAY,
    _HOTEL,
    _card,
    _hotel_bill,
    _movement,
    _revert,
    _per_day,
    _latest_anchor,
)


class TestTheBooksBoundaryIsTheTendersAccounts:
    """A payment is dated against the account it books on, at the settle and at a re-point."""

    def test_a_settle_dated_on_the_cards_opening_day_is_refused_for_the_card_alone(
        self, app, seed_user, seed_periods,
    ):
        """The same day settles on checking and is refused on the card, by name, before any write."""
        with app.app_context():
            card = _card(seed_user)
            cards_opening = account_opening_fact(card.id).opened_on
            assert cards_opening > account_opening_fact(seed_user["account"].id).opened_on
            that_day = SettleDay(day=cards_opening, basis=SettledDayBasisEnum.ENTERED)

            on_checking = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(on_checking, settle_day=that_day)
            db.session.commit()
            assert _movement(on_checking).settled_on == cards_opening

            on_the_card = _hotel_bill(seed_user, seed_periods[0])
            with pytest.raises(ValidationError, match=r"books open on"):
                settle_transaction(
                    on_the_card, settle_day=that_day, tender_account_id=card.id,
                )
            db.session.rollback()
            db.session.expire_all()
            fresh = db.session.get(Transaction, on_the_card.id)
            assert fresh.settled_on is None
            assert fresh.covering_movements == []

    def test_a_correction_cannot_move_a_dated_payment_inside_the_cards_opening(
        self, app, seed_user, seed_periods,
    ):
        """Equal days ask no day writer, so the re-point asks the boundary itself; nothing moves."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            cards_opening = account_opening_fact(card.id).opened_on
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(
                txn, settle_day=SettleDay(
                    day=cards_opening, basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            status_seam.record_clearing(txn, _latest_anchor(checking.id).id)
            db.session.commit()
            anchor_id = _movement(txn).reconciled_by_id
            assert anchor_id is not None

            with pytest.raises(ValidationError, match=r"books open on"):
                transaction_service.apply_requested_status(
                    txn, txn.status_id,
                    stated=transaction_service.StatedRecord(tender_account_id=card.id),
                )
            db.session.rollback()
            db.session.expire_all()
            movement = _movement(db.session.get(Transaction, txn.id))
            assert movement.account_id == checking.id
            assert movement.reconciled_by_id == anchor_id
            assert db.session.get(Transaction, txn.id).reconciled_by_id == anchor_id

    def test_a_day_move_and_a_tender_in_one_save_are_graded_on_the_day_the_payment_ends_with(
        self, app, seed_user, seed_periods,
    ):
        """The popover posts both; the re-point grades the ROW's new day, never the old one.

        A payment dated on the card's opening day (valid on checking),
        corrected in ONE Save to the day after AND the card: the end state
        is valid, so it lands -- the first cut graded the movement's OLD day
        against the card and refused a day the payment was leaving (the
        review of this leaf).  And the reverse: a payment dated after the
        opening, moved in one Save ONTO the opening day and the card, is
        refused, so the new day is what is graded.
        """
        with app.app_context():
            card = _card(seed_user)
            cards_opening = account_opening_fact(card.id).opened_on
            on_the_opening = SettleDay(day=cards_opening, basis=SettledDayBasisEnum.ENTERED)
            the_day_after = SettleDay(
                day=cards_opening + _ONE_DAY, basis=SettledDayBasisEnum.ENTERED,
            )

            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, settle_day=on_the_opening)
            db.session.commit()
            transaction_service.apply_requested_status(
                txn, txn.status_id, settle_day=the_day_after,
                stated=transaction_service.StatedRecord(tender_account_id=card.id),
            )
            db.session.commit()
            movement = _movement(txn)
            assert movement.account_id == card.id
            assert movement.settled_on == txn.settled_on == the_day_after.day

            other = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(other, settle_day=the_day_after)
            db.session.commit()
            with pytest.raises(ValidationError, match=r"books open on"):
                transaction_service.apply_requested_status(
                    other, other.status_id, settle_day=on_the_opening,
                    stated=transaction_service.StatedRecord(tender_account_id=card.id),
                )
            db.session.rollback()
            db.session.expire_all()
            movement = _movement(db.session.get(Transaction, other.id))
            assert movement.account_id == other.account_id
            assert movement.settled_on == the_day_after.day


class TestTheRowsClearingLinkStaysOnTheRowsAccount:
    """A movement on another account carries its own link; the row's never reaches it."""

    def test_record_clearing_links_the_row_and_not_a_card_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            anchor_id = _latest_anchor(checking.id).id
            status_seam.record_clearing(txn, anchor_id)
            db.session.commit()
            assert txn.reconciled_by_id == anchor_id
            assert _movement(txn).reconciled_by_id is None

    def test_a_re_settle_does_not_copy_the_rows_link_onto_a_card_movement(
        self, app, seed_user, seed_periods,
    ):
        """The mirror's differing-day arm, on a kept card movement re-dated by a re-settle."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            _revert(txn)
            settle_transaction(txn)
            db.session.commit()
            movement = _movement(txn)
            assert movement.account_id == card.id
            assert movement.settled_on == txn.settled_on
            assert movement.reconciled_by_id is None

    def test_a_basis_rise_on_a_linked_row_does_not_copy_its_link_onto_a_card_movement(
        self, app, seed_user, seed_periods,
    ):
        """The mirror's EQUAL-days arm, the one a linked row can reach.

        The bank confirms the day checking's panel had bounded, the row's
        basis rises to ``observed`` and its link STANDS (the day did not
        move); the card movement's basis rises with it and it takes NO link
        -- the row's names checking's statement, and the movement's own key
        holds a link to the movement's account.  Under the copy this arm
        made through ``CC-5-2`` the flush would meet
        ``fk_transaction_entries_reconciled_by``.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            anchor_id = _latest_anchor(checking.id).id
            status_seam.record_clearing(txn, anchor_id)
            db.session.commit()
            observed = ref_cache.settled_day_basis_id(SettledDayBasisEnum.OBSERVED)
            assert _movement(txn).settled_day_basis_id != observed

            transaction_service.apply_requested_status(
                txn, txn.status_id,
                settle_day=SettleDay(
                    day=txn.settled_on, basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.commit()

            assert txn.reconciled_by_id == anchor_id
            movement = _movement(txn)
            assert movement.settled_day_basis_id == observed
            assert movement.reconciled_by_id is None

    def test_a_revert_withdraws_a_card_movements_own_link(
        self, app, seed_user, seed_periods,
    ):
        """A day move withdraws the movement's observation whichever account it is on.

        The card's statement will write a card movement's own link (leaf
        ``CC-5-4``); this leaf built the gate that keeps the ROW's link off
        it, and the first cut gated the RELEASE with the copy -- so a revert
        would have un-dated the movement with its link standing, which
        ``ck_transaction_entries_cleared_needs_settle_day`` refuses at the
        flush (the review of this leaf).  The link is planted here as that
        writer will plant it, on the card's own assertion.
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            movement = _movement(txn)
            movement.reconciled_by_id = _latest_anchor(card.id).id
            db.session.commit()
            assert _movement(txn).reconciled_by_id is not None

            _revert(txn)

            kept = _movement(txn)
            assert kept.settled_on is None
            assert kept.reconciled_by_id is None
            assert kept.account_id == card.id

    def test_an_echo_of_a_since_archived_tender_is_not_gated(
        self, app, seed_user, seed_periods,
    ):
        """The popover shows the recorded tender even when it is no longer nameable.

        A bill paid from a card the owner has since ARCHIVED: the picker
        preselects the archived card (the route's ``_tender_picker`` appends
        it), so the Paid button on a reverted such bill posts it -- an ECHO of
        the record, dropped before the gate's archived refusal is asked
        (ruling **R-CC42**: where the money moved is retained).  The first
        cut gated it and refused a tender nobody changed (the review of this
        leaf).  Naming the archived card for a row that does NOT record it is
        still refused.
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            _revert(txn)
            card.is_active = False
            db.session.commit()

            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            assert _movement(txn).account_id == card.id

            other = _hotel_bill(seed_user, seed_periods[0])
            with pytest.raises(ValidationError, match=r"archived, so a payment"):
                settle_transaction(other, tender_account_id=card.id)

    def test_the_matcher_never_moves_a_card_payment(
        self, app, seed_user,
    ):
        """A bill reverted out of a card settle: checking's screen withholds it, the card's matches it.

        Rewritten under ruling **R-CC137** (developer 2026-10-04, "Only Paid
        from, say why"; plan step ``credit_card:CC-5-4a-5``, leaf 5c-2a), the
        rule-5 exception for a ruled change of behaviour: *"Checking's
        statement screen does not offer Hotel while its payment is recorded on
        the Visa (the reconcile panel's test, R-CC126), and says so on that
        screen ... Matching on the Visa's screen instead dates it there, and
        the Visa owes $120.00 more. A bill's payment then changes account only
        through 'Paid from' ... and a match from a page drawn before the
        payment moved is refused. Amends R-CC43."*  It was
        ``test_the_matchers_accept_forces_the_statements_account``, which
        pinned the matcher's transaction arm naming the pass's account as the
        tender (ruling **R-CC15**) and re-pointing the kept card movement onto
        checking.  Same staging: the bill settled from the card, reverted, a
        checking line and now a card line beside it.
        """
        # Pylint: ``import-outside-toplevel`` -- the matcher's own builders,
        # imported where used, since this is the one case in this module that
        # reads them.
        # pylint: disable=import-outside-toplevel
        from app.services import statement_match
        from tests.test_services.test_statement_match._builders import (
            a_bank_line, a_scope, a_submission, a_transaction, an_import,
        )
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            db.session.commit()
            bank_day = seed_user["bootstrap_period"].start_date
            checking_line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00",
                posted_on=bank_day,
            )
            card_line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day, description="HOTEL",
            )
            txn = a_transaction(seed_user, name="Hotel", amount="120.00")
            db.session.commit()
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            _revert(txn)
            assert _movement(txn).account_id == card.id

            on_checking = a_scope(seed_user)
            assert [
                row for row in on_checking.candidates.rows
                if row.transaction_id == txn.id
            ] == []
            (held,) = on_checking.candidates.held_elsewhere
            assert (held.name, held.figure, held.recorded_on, held.is_income) == (
                "Hotel", _HOTEL, card.name, False,
            )
            with pytest.raises(ValidationError, match="no longer available"):
                statement_match.accept_match(
                    a_submission(
                        on_checking, lines=[checking_line], transactions=[txn],
                    ),
                    on_checking,
                )
            db.session.rollback()
            movement = _movement(txn)
            assert movement.account_id == card.id
            assert movement.settled_on is None
            scenario_id = seed_user["scenario"].id
            assert bank_day not in _per_day(card.id, scenario_id)

            on_card = a_scope(seed_user, card)
            accepted = statement_match.accept_match(
                a_submission(on_card, lines=[card_line], transactions=[txn]),
                on_card,
            )
            db.session.commit()

            assert accepted.settled_count == 1
            movement = _movement(txn)
            assert movement.account_id == card.id
            assert movement.settled_on == bank_day
            assert txn.settled_on == bank_day
            # "the Visa owes $120.00 more": the card's fold carries the
            # payment on the bank's day, and Checking's carries nothing.
            assert _per_day(card.id, scenario_id).get(bank_day) == -_HOTEL
            assert _per_day(checking.id, scenario_id) == {}

    def test_the_checking_panel_no_longer_moves_a_card_payment(
        self, app, seed_user, seed_periods,
    ):
        """A bill reverted out of a card settle, ticked on CHECKING's panel: nothing lands, nothing moves.

        Ruling **R-CC126** (developer 2026-09-30, "Only the card offers it"):
        *"Checking's list leaves it out ... Each list offers only money that
        moved on its own account, so reconciling never moves a payment."*
        Until plan step ``credit_card:CC-5-4a-5c-1`` this test pinned the
        opposite -- the tick named Checking as the tender (ruling **R-CC15**)
        and re-pointed the card's payment onto checking, withdrawing any
        match on it unannounced (finding **CC-378**); the developer ruled
        that behaviour changed.  The row is the card's "Paid from this
        account" offer now, and a tick there books it where it is
        (``test_reconcile_settlements``).
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            _revert(txn)
            assert _movement(txn).account_id == card.id

            observed_on = seed_periods[0].start_date + timedelta(days=3)
            statement = reconcile_service.Statement(
                pay_calendar.calendar_for(seed_user["user"].id), checking.id,
                replace(cash_ledger.governing_anchor(checking.id), observed_on=observed_on),
            )
            settled = reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    statement=statement, entry_ids=set(),
                    transaction_ids={txn.id}, corrections={},
                    transfer_ids=set(), transfer_corrections={},
                ),
            )
            db.session.commit()
            assert settled == 0
            movement = _movement(txn)
            assert movement.account_id == card.id
            assert movement.settled_on is None
            assert movement.reconciled_by_id is None
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.reconciled_by_id is None
