"""Plan step ``credit_card:CC-5-3``: the settle-with-tender door.

A bill BUDGETED on checking and PAID WITH THE CARD is settled with its
covering movement on the card (ruling **R-CC15**: the row never moves; the
movement's account is where its money moved, **R-BAL75**).  What is graded
here is the verb's own rule -- which account a payment may name, whose it
must be, what the seam books on when none is named (**R-CC42**: the kept
record's account, else the row's, so a revert keeps where the money moved as
it keeps a stated figure), what a "Paid from" correction on a settled row
does to the movement and to both clearing links, and what a statement-side
reader now prices a settled row at (**R-CC40**, half 1: what its movement
moves ON THE ACCOUNT ASKED ABOUT, so a card-tendered bill leaves checking's
offer).

Every refusal is graded by the state it leaves, re-read from the database:
the verb composes its refusals ahead of any mutation, so a refused settle
leaves the row Projected with no movement and a refused correction leaves the
movement where it was.

The worked example throughout is the developer's own (2026-09-21): a hotel
bill on Checking, charged to the card.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.services import (
    cash_ledger,
    pay_calendar,
    reconcile_service,
    status_seam,
    transaction_service,
)
from app.services.cash_ledger import (
    account_opening_fact,
    derived_amount_basis,
    settled_cash_facts,
)
from app.services.row_valuation import settled_figure
from app.services.settle_day import SettleDay
from app.services.statement_match import candidates_for
from app.services.statement_match import RowKind, _accepted_view, _valuation
from app.services.transaction_service import settle_transaction
from app.services.ledger_lines import linked_key
from tests._ledger_reads import ledger_net
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    generate_row_of,
    make_expense_template,
    typed,
)

_ONE_DAY = timedelta(days=1)
_HOTEL = Decimal("120.00")


def _card(seed_user, name="Rewards Card"):
    """Create an active Credit Card account for *seed_user*."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _hotel_bill(seed_user, period, *, account=None, is_envelope=False):
    """One engine-generated `$120.00` Hotel bill on checking (or *account*)."""
    template = make_expense_template(
        db.session, seed_user, amount=str(_HOTEL),
        name="Hotel", category_key="Rent", is_envelope=is_envelope,
        account=account,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _movement(txn):
    """Return the row's one covering movement, re-read from the database."""
    db.session.expire(txn)
    movements = txn.covering_movements
    assert len(movements) == 1, f"expected one covering movement, got {len(movements)}"
    return movements[0]


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _per_day(account_id, scenario_id):
    """Sum the settled cash facts on *account_id* per settle day."""
    sums = {}
    for fact in settled_cash_facts(account_id, scenario_id):
        sums[fact.settled_on] = sums.get(fact.settled_on, Decimal("0")) + fact.delta
    return sums


def _latest_anchor(account_id):
    """The account's latest balance assertion, which the fixtures seed."""
    anchor = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.id.desc())
        .first()
    )
    assert anchor is not None, "the fixture account carries no anchor"
    return anchor


def _correct_tender(txn, account_id):
    """The full-edit popover's identity Save naming another 'Paid from' account."""
    transaction_service.apply_requested_status(
        txn, txn.status_id, stated=transaction_service.StatedRecord(tender_account_id=account_id),
    )
    db.session.commit()


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
        CARD's ledger account and nothing against checking's; and the row is
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
        overwriting the correction; and the POSTED ledger follows the fold
        -- the door's reconcile after the seam reverses checking's leg and
        posts the card's, so checking's ledger account is back where it was
        and the card's carries the payment.
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
        # imported where used, as this module's other matcher case does.
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
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            assert status_seam.covered_cash_leg(txn, card.id) == -_HOTEL
            assert status_seam.covered_cash_leg(txn, checking.id) == Decimal("0")

    def test_a_card_tendered_bill_leaves_the_checking_offer(
        self, app, seed_user, seed_periods,
    ):
        """Checking's feed never shows the money, so checking's screen does not offer it.

        Before ``CC-5-3`` the row was offered there at `-120.00` and a stray
        `$120` line could be paired with it in one click.  **The CARD's
        screen offers the PAYMENT** (plan step ``credit_card:CC-5-4a-1``,
        ruling **R-CC43**, half 2 of **R-CC40**): the covering movement, as a
        SETTLEMENT on the row's terms -- the bill's figure, the bill's
        paycheck, labelled with the account the bill is budgeted on.  This
        pinned the interim (nothing on the card's screen either) through
        ``CC-5-3``, saying the change would be explicit; this is it.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn)
            db.session.commit()
            offered_before = self._offered_for(seed_user, checking.id, txn)
            assert [row.cash_amount for row in offered_before] == [-_HOTEL]
            assert [row.kind for row in offered_before] == [RowKind.SETTLEMENT]

            _correct_tender(txn, card.id)

            assert self._offered_for(seed_user, checking.id, txn) == []
            (on_card,) = self._offered_for(seed_user, card.id, txn)
            (movement,) = txn.covering_movements
            assert on_card.kind is RowKind.SETTLEMENT
            assert on_card.row_id == movement.id
            assert on_card.cash_amount == -_HOTEL
            assert on_card.is_settled is True
            assert on_card.settled_on == txn.settled_on
            assert on_card.label == f"{txn.name} (budgeted on {checking.name})"
            period = pay_calendar.calendar_for(
                seed_user["user"].id,
            ).period_by_id(txn.pay_period_id)
            assert on_card.period == period

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
        candidate, priced as a movement): asked for the CARD's screen, a
        checking bill's payment on the card is a candidate worth its figure;
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
            card = _card(seed_user)
            txn = _hotel_bill(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            db.session.commit()
            (movement,) = txn.covering_movements
            basis = derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)
            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            assert _valuation.settlement_price(movement, basis) == -_HOTEL
            on_card = _valuation.settlement_candidate(
                movement, calendar, -_HOTEL, card.id,
            )
            assert on_card is not None and on_card.cash_amount == -_HOTEL
            assert _valuation.settlement_candidate(
                movement, calendar, -_HOTEL, checking.id,
            ) is None
            assert _valuation.repriced(on_card, calendar, basis, card.id) == on_card
            assert _valuation.repriced(on_card, calendar, basis, checking.id) is None
            register = _accepted_view._accepted_row  # pylint: disable=protected-access
            assert register(movement, txn.settled_on, card.id).cash_amount == -_HOTEL
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
            card = _card(seed_user)
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

            _correct_tender(txn, card.id)

            assert register(movement, txn.settled_on, checking.id).cash_amount == Decimal("0")
            assert _valuation.repriced(candidate, calendar, basis, checking.id) is None
