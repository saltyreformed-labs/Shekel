"""The card's screen offers the payment movement as its candidate, and a Projected row is offered
once while its kept payment is offered where the row is not.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.services import pay_calendar, status_seam
from app.services.cash_ledger import derived_amount_basis
from app.services.statement_match import RowKind, _valuation, accept_match, matched_subjects
from app.services.transaction_service import settle_transaction
from tests._test_helpers import ledger_net, linked_ledger_account, state_template_price, typed
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    accepted_acts,
    an_import,
)

from ._helpers import (
    _HOTEL,
    _card,
    _hotel_bill,
    _movement,
    _revert,
    _entered,
    _first_day,
    _offered_for,
    _per_day,
    _accept,
    _members_of,
)


class TestThePaymentIsTheCardScreensCandidate:
    """R-CC40's second half, under R-CC43: the card's screen offers the payment."""

    def test_the_card_screen_offers_the_bill_as_its_payment_and_checking_does_not(
        self, app, seed_user,
    ):
        """One subject for the bill, on the account its money moved through."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            movement = _movement(txn)

            assert _offered_for(seed_user, checking, txn) == []
            (offered,) = _offered_for(seed_user, card, txn)
            assert offered.kind is RowKind.SETTLEMENT
            assert offered.row_id == movement.id
            assert offered.parent_id == txn.id
            assert offered.cash_amount == -_HOTEL
            assert offered.is_settled is True
            assert offered.settled_on == txn.settled_on == movement.settled_on
            assert offered.settle_day_basis is SettledDayBasisEnum.ENTERED
            assert offered.label == f"Hotel (budgeted on {checking.name})"
            # Both homes' counters (the review's L2: a reverted row can move
            # paychecks or be renamed without its kept movement changing).
            assert offered.version_id == movement.version_id + txn.version_id
            assert offered.purchased_on is None
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            assert offered.period == calendar.period_by_id(txn.pay_period_id)
            # The window is the ROW's: a point at its settle day, as the row's
            # own candidate carried through ``CC-5-3``.
            assert offered.expected_window == (txn.settled_on, txn.settled_on)

    def test_accepting_it_on_the_card_dates_the_bill_by_the_cards_line(
        self, app, seed_user,
    ):
        """The developer's worked case: accept dates the bill; nothing counted twice."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            scenario_id = seed_user["scenario"].id
            card_ledger = linked_ledger_account(db.session, card.id)
            checking_ledger = linked_ledger_account(db.session, checking.id)
            # The two ledgers BEFORE the bill exists: each holds its opening.
            card_before = ledger_net(db.session, card_ledger.id, scenario_id)
            checking_before = ledger_net(db.session, checking_ledger.id, scenario_id)
            settled_day = _first_day(seed_user)
            bank_day = settled_day + timedelta(days=2)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(settled_day), tender_account_id=card.id,
            )
            db.session.commit()
            movement_id = _movement(txn).id
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()

            accepted = _accept(seed_user, card, [line], [txn])

            db.session.expire_all()
            txn = db.session.get(type(txn), txn.id)
            movement = _movement(txn)
            assert accepted.corrected_count == 1 and accepted.settled_count == 0
            assert txn.settled_on == bank_day
            assert txn.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            # The movement FOLLOWED the row: same id, the bank's day, still on
            # the card, no clearing link written by the matcher (ruling R-FV).
            assert movement.id == movement_id
            assert movement.settled_on == bank_day
            assert movement.account_id == card.id
            assert movement.reconciled_by_id is None and txn.reconciled_by_id is None
            # The card's actual equals the bank; checking never moved.
            assert status_seam.covered_cash_leg(txn, card.id) == -_HOTEL
            assert status_seam.covered_cash_leg(txn, checking.id) == Decimal("0")
            assert _per_day(card.id, scenario_id).get(bank_day) == -_HOTEL
            assert settled_day not in _per_day(card.id, scenario_id)
            assert _per_day(checking.id, scenario_id) == {}
            # The posted ledger: the card carries the payment ONCE, checking
            # nothing -- the accept re-dated the one posting, it added none.
            assert ledger_net(
                db.session, card_ledger.id, scenario_id,
            ) == card_before - _HOTEL
            assert ledger_net(
                db.session, checking_ledger.id, scenario_id,
            ) == checking_before

    def test_the_door_settles_the_payments_row_and_not_the_row_sharing_its_id(
        self, app, seed_user,
    ):
        """A SETTLEMENT's ``row_id`` is a MOVEMENT id; the door reads the row through it.

        **A Projected row is BUILT to share the payment movement's id**, the
        state production reaches wherever the two tables' counters cross.  A
        door that looked a row up by the movement's id then FINDS a row here,
        where elsewhere in this package it finds none (eight other cases fail
        on that).  Here it is asked to settle that Projected row with a settle
        day, and the accept refuses with a ``ValidationError`` -- measured by
        the review of ruling ``balance:R-BAL356``, with ``_moving.py`` reading
        ``row.row_id`` where it reads ``row.transaction_id``.  The restart is
        needed because rows and movements are numbered from ranges of their
        own in the test database (that ruling): no row holds a movement's id
        unless a case makes one.  Before the ranges both tables started at 1,
        the first movement and the first row coincided, and the same mutation
        settled the right row by coincidence -- measured: it survived every
        case in this module until this one.  The tail reads that neither the
        sharing row nor the three decoys (which made the ids diverge then) was
        settled; no mutant is known to fail those reads.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            decoys = [
                a_transaction(seed_user, name=f"Decoy {n}", amount="10.00")
                for n in range(3)
            ]
            db.session.commit()
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            movement = _movement(txn)
            assert movement.id != txn.id
            # The next row takes the movement's id (``setval`` with
            # ``is_called`` false hands out the given value next).
            db.session.execute(
                db.text("SELECT setval(CAST(:seq AS regclass), :v, false)"),
                {"seq": "budget.transactions_id_seq", "v": movement.id},
            )
            sharing = a_transaction(seed_user, name="Sharing", amount="10.00")
            db.session.commit()
            assert sharing.id == movement.id and sharing.id != txn.id
            assert sharing.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day + timedelta(days=1),
            )
            db.session.commit()

            _accept(seed_user, card, [line], [txn])

            db.session.expire_all()
            assert db.session.get(type(txn), txn.id).settled_on == bank_day + timedelta(days=1)
            sharing = db.session.get(type(txn), movement.id)
            assert sharing.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert sharing.settled_on is None
            assert all(
                db.session.get(type(txn), decoy.id).settled_on is None
                for decoy in decoys
            )
            assert _offered_for(seed_user, checking, txn) == []

    def test_the_member_names_the_movement_on_the_cards_account(
        self, app, seed_user,
    ):
        """What the act records: the movement, held to the card by its key.

        Since ``CC-5-4a-2`` the member table has no row column, so "no member
        names the row" is the schema's; the assertion that said so went with it.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=_first_day(seed_user),
            )
            db.session.commit()

            accepted = _accept(seed_user, card, [line], [txn])

            members = _members_of(accepted.match_id)
            assert {m.account_id for m in members} == {card.id}
            assert {m.transaction_entry_id for m in members} == {
                None, _movement(txn).id,
            }
            assert {m.bank_statement_line_id for m in members} == {None, line.id}
            claims = matched_subjects(card.id)
            assert claims.entries == {_movement(txn).id}
            assert claims.transactions == {txn.id}, (
                "a row is claimed through its payment"
            )
            assert claims.lines == {line.id}

    def test_the_register_reads_it_as_the_bill_and_stops_holding_on_a_revert(
        self, app, seed_user,
    ):
        """The accepted list on the card: the bill's name, its figure; a revert reads 0."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            bank_day = _first_day(seed_user)
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            _accept(seed_user, card, [line], [txn])

            (group,) = accepted_acts(seed_user, card)
            (row,) = group.rows
            assert row.label == "Hotel"
            assert row.cash_amount == -_HOTEL
            assert row.settled_on == bank_day
            assert group.agrees is True
            assert group.created_every_row is False

            _revert(txn)

            (group,) = accepted_acts(seed_user, card)
            (row,) = group.rows
            assert row.cash_amount == Decimal("0")
            assert row.settled_on is None
            assert group.agrees is False


class TestAProjectedRowIsOfferedOnceAndItsKeptPaymentWhereTheRowIsNot:
    """The two arms partition on one predicate; the reverted card bill is the case."""

    def test_the_reverted_card_bill_is_named_on_checking_and_offered_on_the_card(
        self, app, seed_user,
    ):
        """Checking withholds the Projected row and names it; the card offers its kept payment.

        Rewritten under ruling **R-CC137** (developer 2026-10-04, "Only Paid
        from, say why", amending R-CC43): *"Checking's statement screen does
        not offer Hotel while its payment is recorded on the Visa (the
        reconcile panel's test, R-CC126), and says so on that screen"* -- in
        ruling **R-CC140**'s words.  It was
        ``test_the_reverted_card_bill_is_offered_on_both_screens_as_two_subjects``,
        which pinned Checking offering the row as a TRANSACTION beside the
        card's SETTLEMENT.  Developer confirmation 2026-10-04 (rule 5, ruling
        **R-CC138**): "Make the tests match the code".
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            movement = _movement(txn)
            assert movement.settled_on is None and movement.account_id == card.id

            assert _offered_for(seed_user, checking, txn) == []
            (held,) = a_scope(seed_user, checking).candidates.held_elsewhere
            assert held.said == (
                f"Hotel $120.00 is not listed here because the app has it as "
                f"paid from {card.name}. To change that, edit Hotel on the grid."
            )

            (on_card,) = _offered_for(seed_user, card, txn)
            assert on_card.kind is RowKind.SETTLEMENT
            assert on_card.row_id == movement.id
            assert on_card.cash_amount == -_HOTEL
            assert on_card.is_settled is False
            assert on_card.settled_on is None
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            period = calendar.period_by_id(txn.pay_period_id)
            assert on_card.expected_window == (period.start_date, period.end_date)

    def test_a_checking_page_drawn_before_the_card_paid_it_is_refused(
        self, app, seed_user,
    ):
        """Drawn while plain Projected, posted after a card-pay and revert: nothing moves.

        Rewritten under ruling **R-CC137** (developer 2026-10-04, "Only Paid
        from, say why", amending R-CC43): *"A bill's payment then changes
        account only through 'Paid from' ... and a match from a page drawn
        before the payment moved is refused."*  It was
        ``test_the_reverse_order_settles_it_on_checking_and_refuses_the_card``,
        which pinned Checking's accept naming its own account as the tender
        (ruling **R-CC15**) and re-pointing the card's kept payment onto
        Checking.  Two guards now keep the payment where it is, each enough
        alone: Checking withholds the row, and the matcher names no tender.
        This test grades the first, by its REASON: the fresh scope no longer
        holds the row, so the page is refused by name.  Without
        the withholding this stale page would meet the version check
        instead ("reviewed against different figures"), and a fresh one
        would fail after its own settle, when the accept reads back a
        payment that is on the card (measured by the leaf's review).  Developer
        confirmation 2026-10-04 (rule 5, ruling **R-CC138**): "Make the
        tests match the code".  The race it was paired with
        (``test_whichever_screen_accepts_first_wins_and_the_other_is_refused``)
        is DELETED under the same confirmation: Checking never offers the
        reverted bill, so two screens cannot both offer it, and its card-side
        assertions live in ``test_cc5_3_settle_tender``'s
        ``test_the_matcher_never_moves_a_card_payment``.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            checking_line = a_bank_line(
                seed_user, an_import(seed_user, checking), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            # Checking's page, drawn while the bill was Projected with no
            # payment anywhere: it offers the row.
            stale = a_submission(
                a_scope(seed_user, checking), lines=[checking_line],
                transactions=[txn],
            )
            assert {row.kind for row in stale.rows} == {RowKind.TRANSACTION}
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            kept = _movement(txn)
            before = (kept.id, kept.account_id, kept.settled_on, kept.version_id)
            assert kept.account_id == card.id and kept.settled_on is None

            with pytest.raises(ValidationError, match="no longer available"):
                accept_match(stale, a_scope(seed_user, checking))
            db.session.rollback()

            assert db.session.query(StatementMatch).count() == 0
            kept = _movement(txn)
            assert (
                kept.id, kept.account_id, kept.settled_on, kept.version_id,
            ) == before
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert matched_subjects(checking.id).lines == set()

    def test_a_kept_payment_is_priced_at_what_its_re_settle_books(
        self, app, seed_user,
    ):
        """The un-dated arm reads the ROW's price, not the movement's stale column."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            movement = _movement(txn)
            assert movement.amount == _HOTEL
            # The definition is re-priced meanwhile; a resolved record
            # re-prices at its re-settle (``test_covering_movement``'s case).
            state_template_price(txn.template, Decimal("160.00"))
            db.session.commit()

            (on_card,) = _offered_for(seed_user, card, txn)

            assert on_card.cash_amount == Decimal("-160.00")
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            assert _valuation.settlement_price(movement, basis) == Decimal("-160.00")
            assert movement.amount == _HOTEL, "the column is the stale figure"

    def test_a_kept_payments_form_is_stale_once_its_row_is_renamed(
        self, app, seed_user,
    ):
        """The row's counter rides the settlement's revision (the review's L2)."""
        with app.app_context():
            card = _card(seed_user)
            bank_day = _first_day(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, settle_day=_entered(bank_day), tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=bank_day,
            )
            db.session.commit()
            scope = a_scope(seed_user, card)
            stale = a_submission(scope, lines=[line], transactions=[txn])
            assert {row.kind for row in stale.rows} == {RowKind.SETTLEMENT}
            movement_version = _movement(txn).version_id

            txn.name = "Hotel (renamed)"
            db.session.commit()

            assert _movement(txn).version_id == movement_version, (
                "the rename touched the row alone; the movement did not move"
            )
            with pytest.raises(ValidationError, match="reviewed against different"):
                accept_match(stale, a_scope(seed_user, card))
            db.session.rollback()
            assert db.session.query(StatementMatch).count() == 0

    def test_a_kept_STATED_payment_is_priced_at_the_honoured_figure(
        self, app, seed_user,
    ):
        """A typed figure survives the revert (R-BAL61) and the offer honours it."""
        with app.app_context():
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(
                txn, submitted=typed(Decimal("130.00")),
                settle_day=_entered(_first_day(seed_user)),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(txn)
            state_template_price(txn.template, Decimal("160.00"))
            db.session.commit()

            (on_card,) = _offered_for(seed_user, card, txn)

            assert on_card.cash_amount == Decimal("-130.00")

    def test_a_settled_row_on_its_own_screen_is_offered_as_its_payment_only(
        self, app, seed_user,
    ):
        """The universal subject: on checking too, a settled bill is its payment."""
        with app.app_context():
            checking = seed_user["account"]
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(_first_day(seed_user)))
            db.session.commit()

            (offered,) = _offered_for(seed_user, checking, txn)

            assert offered.kind is RowKind.SETTLEMENT
            assert offered.row_id == _movement(txn).id
            assert offered.label == "Hotel"
            assert offered.cash_amount == -_HOTEL

    def test_a_projected_row_with_a_kept_payment_on_its_own_screen_is_offered_as_itself_only(
        self, app, seed_user,
    ):
        """The partition's other half: never the row AND its kept payment."""
        with app.app_context():
            checking = seed_user["account"]
            txn = _hotel_bill(seed_user, seed_user["bootstrap_period"])
            settle_transaction(txn, settle_day=_entered(_first_day(seed_user)))
            db.session.commit()
            _revert(txn)
            assert _movement(txn).settled_on is None

            (offered,) = _offered_for(seed_user, checking, txn)

            assert offered.kind is RowKind.TRANSACTION
            assert offered.row_id == txn.id
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            assert _valuation.settlement_candidate(
                _movement(txn), calendar, -_HOTEL, checking.id,
            ) is None, "the constructor declines the kept payment here too"
