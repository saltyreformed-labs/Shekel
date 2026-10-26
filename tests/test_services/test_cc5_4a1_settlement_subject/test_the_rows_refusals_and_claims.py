"""A settlement is the row's record: the row's refusals apply to its payment, and the claims an act
already names see the row through it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import transfer_service
from app.services.statement_match import RowKind, accept_match, matched_subjects, release_match
from app.services.statement_match._candidates import unmatched_rows
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    on_both_sides,
    payback_row_of,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    accepted_acts,
    an_import,
)

from ._helpers import (
    _card,
    _hotel_bill,
    _movement,
    _revert,
    _correct_tender,
    _entered,
    _first_day,
    _offered_for,
    _accept,
)


class TestTheRowsRefusalsApplyToItsPayment:
    """A SETTLEMENT is the row's record, so the row's refusals are its own."""

    def test_a_settled_paybacks_payment_refuses_the_banks_differing_figure(
        self, app, seed_user,
    ):
        """Finding N-252's class on the third kind: no figure of its own to state."""
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            envelope = a_transaction(
                seed_user, name="Card Spend", amount="300.00", is_envelope=True,
            )
            a_later_period(seed_user)
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("60.00"), bank_day,
            )
            settle_transaction(payback, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-55.00", posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            submission = a_submission(scope, lines=[line], transactions=[payback])
            assert {row.kind for row in submission.rows} == {RowKind.SETTLEMENT}

            with pytest.raises(ValidationError, match="no figure of its own"):
                accept_match(submission, scope)
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 0

    def test_a_settled_shadow_legs_payment_refuses_a_correction(
        self, app, seed_user,
    ):
        """Transfer invariant 3: the correction is to the TRANSFER, not this door.

        The Checking side's payment is ticked as the transfer's side RECORD
        (``a_submission``'s ``transfers``), which hangs off the transfer since
        plan step ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**); the case
        ticked the Checking shadow, whose ``covering_movements`` held it,
        until then (ruling **R-BAL167** class 4).
        """
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    transfer.from_account_id, transfer.to_account_id,
                    _entered(bank_day),
                ),
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-95.00", posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            submission = a_submission(scope, lines=[line], transfers=[transfer])
            (reviewed,) = submission.rows
            assert reviewed.kind is RowKind.SETTLEMENT

            with pytest.raises(ValidationError, match="one half of a transfer"):
                accept_match(submission, scope)
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 0


class TestTheClaimsSeeARowThroughItsPayment:
    """What an act already names, read through the row's payment."""

    def test_a_reverted_row_whose_payment_an_act_names_is_not_offered_again(
        self, app, seed_user,
    ):
        """The act shows on the register as no longer holding; the row waits on its Undo."""
        with app.app_context():
            checking = seed_user["account"]
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00", posted_on=bank_day,
            )
            db.session.commit()
            _accept(seed_user, checking, [line], [txn])
            _revert(txn)

            scope = a_scope(seed_user, checking)
            claims = matched_subjects(checking.id)
            assert claims.transactions == {txn.id}
            assert [
                row for row in unmatched_rows(scope.candidates, claims)
                if row.transaction_id == txn.id
            ] == []
            assert [
                row for row in scope.candidates.rows
                if row.transaction_id == txn.id
            ] != [], "the scope still holds it; the claims are what narrow"

    def test_a_row_whose_payment_the_CARDS_act_names_is_named_on_checking_and_freed_on_the_card(
        self, app, seed_user,
    ):
        """Checking withholds and names it before and after the Undo, which frees it on the card.

        Rewritten under ruling **R-CC137** (developer 2026-10-04, "Only Paid
        from, say why", amending R-CC43): *"Checking's statement screen does
        not offer Hotel while its payment is recorded on the Visa (the
        reconcile panel's test, R-CC126), and says so on that screen"*.  It
        was ``test_a_row_whose_payment_the_CARDS_act_names_is_claimed_on_checking_too``,
        which pinned the OWNER's claim (the review's M1) narrowing Checking's
        offer of the Projected row, and the card's Undo freeing the row for
        Checking as a TRANSACTION.  Since R-CC137 Checking offers no row
        whose payment is on another account, act or no act; the card's
        register still carries the act as no longer holding, with its Undo,
        and the Undo frees the payment on the card's screen only.  Developer
        confirmation 2026-10-04 (rule 5, ruling **R-CC138**): "Make the
        tests match the code".
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            card_line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            checking_line = a_bank_line(
                seed_user, an_import(seed_user, checking), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            accepted = _accept(seed_user, card, [card_line], [txn])
            _revert(txn)

            scope = a_scope(seed_user, checking)
            assert _offered_for(seed_user, checking, txn) == []
            (held,) = scope.candidates.held_elsewhere
            assert (held.name, held.recorded_on) == ("Hotel", card.name)
            stale = a_submission(scope, lines=[checking_line], transactions=[txn])
            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(stale, a_scope(seed_user, checking))
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 1
            assert _movement(txn).account_id == card.id
            (group,) = accepted_acts(seed_user, card)
            assert group.agrees is False
            # The card's act still names the kept payment, so the card's own
            # screen does not offer it again until the Undo.
            assert [
                row for row in unmatched_rows(
                    a_scope(seed_user, card).candidates,
                    matched_subjects(card.id),
                )
                if row.transaction_id == txn.id
            ] == []

            release_match(accepted.match_id, seed_user["user"].id, card.id)
            db.session.commit()

            (offered,) = [
                row for row in unmatched_rows(
                    a_scope(seed_user, card).candidates,
                    matched_subjects(card.id),
                )
                if row.transaction_id == txn.id
            ]
            assert offered.kind is RowKind.SETTLEMENT
            assert offered.row_id == _movement(txn).id
            assert _offered_for(seed_user, checking, txn) == []
            (held,) = a_scope(seed_user, checking).candidates.held_elsewhere
            assert (held.name, held.recorded_on) == ("Hotel", card.name)

    def test_a_payment_re_pointed_since_the_screen_offered_it_is_refused_not_written(
        self, app, seed_user,
    ):
        """A stale screen cannot name money that moved elsewhere.

        Two guards, and the first is what answers here: the re-price re-asks
        the account (``settlement_candidate`` declines a movement not on the
        screen's account, so the row is "no longer available"), and behind
        it the movement's revision -- bumped by the re-point -- would refuse
        the form as moved.  The constructor's account test is graded
        DIRECTLY in ``test_cc5_3_settle_tender`` because on this path the
        second guard masks it (measured by mutation).
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(bank_day))
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00", posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, checking)
            stale = a_submission(scope, lines=[line], transactions=[txn])
            assert {row.kind for row in stale.rows} == {RowKind.SETTLEMENT}

            _correct_tender(txn, card.id)

            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(stale, a_scope(seed_user, checking))
            db.session.rollback()
            assert db.session.query(StatementMatchMember).count() == 0
