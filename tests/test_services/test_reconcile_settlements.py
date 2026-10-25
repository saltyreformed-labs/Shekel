"""
Shekel Budget App -- The card's reconcile panel lists the bill it paid (credit_card:CC-5-4b)

Ruling **R-CC44**: the reconcile panel gains a list of UN-DATED payments on
the statement's account whose row is planned on ANOTHER account -- a bill
planned on Checking, marked paid from the card and then reopened, its payment
KEPT on the card with no date (rulings **R-CC42**, **R-BAL61**).  It is the
transaction arm's second scope (``reconcile_service._transactions``), headed
"Paid from this account" (**R-CC111**), and every rule it answers to is
graded here:

* the scope, a case per direction of each clause: the row on another account
  (not this one), a payment un-dated (not dated) and on THIS account (not a
  third), no purchase held (**R-CC113**), Projected, and the bill's own
  landing day (**R-CC118**).  The scope's clause leaving out a transfer's
  shadow has had no case since plan step ``balance:X-bi-6-4d-2`` (ruling
  **R-BAL313**): it planted a payment under a shadow, which cannot be stored
  since revision ``82f7b7ed4332`` deleted them (ruling **R-BAL258**);
* the offer: priced, boxed (**R-CC110**) and posted (**R-CC116**) exactly as
  the row's own list does, worded per **R-CC111** / **R-CC117**, and an
  envelope's tick closing it (**R-CC119**);
* the tick: the row settles on the statement's day with its payment where it
  is, and the statement's link reaches the payment and NOT the row --
  ``status_seam.record_clearing``'s one rule, a statement of account X links
  every fact on X.

The worked example throughout is the rulings' own: Groceries `$120.00`
planned on Checking, paid from the card, reopened.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    PostingSourceEnum,
    SettledDayBasisEnum,
    StatusEnum,
)
from app.exceptions import ValidationError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.services import (
    anchor_service,
    cash_ledger,
    entry_service,
    reconcile_service,
    status_seam,
    transaction_service,
)
from app.services.cash_ledger import settled_cash_facts
from app.services.pay_calendar import calendar_for
from app.services.transaction_service import settle_transaction
from app.utils.log_events import (
    EVT_SETTLEMENTS_RECONCILED,
    EVT_TRANSACTIONS_RECONCILED,
)
from tests._test_helpers import (
    create_account_of_type,
    generate_row_of,
    ledger_net,
    linked_ledger_account,
    make_expense_template,
    make_income_template,
    state_template_price,
    typed,
)

#: The civil day every statement here is presented for; a period-0 row due on
#: its payday (2026-01-02) lands on or before it.
_OBSERVED_ON = date(2026, 1, 10)

_GROCERIES = Decimal("120.00")


def _card(seed_user, name="Rewards Card"):
    """Create an active Credit Card account whose books open before any day used here."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _statement(seed_user, account_id, observed_on=_OBSERVED_ON):
    """Return *account_id*'s Statement: its REAL governing assertion, presented for *observed_on*.

    The id must exist -- the clearing links' composite keys refuse one that
    does not -- and the day is the test's.
    """
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account_id,
        replace(
            cash_ledger.governing_anchor(account_id), observed_on=observed_on,
        ),
    )


def _row(seed_user, period, *, account=None, name="Groceries",
         amount=_GROCERIES, is_envelope=False, income=False):
    """Return the engine's one row of a new every-paycheck definition in *period*.

    Planned on *account* (the seed Checking by default) and due on the
    paycheck's own start, so it lands on or before :data:`_OBSERVED_ON` in
    period 0.
    """
    builder = make_income_template if income else make_expense_template
    template = builder(
        db.session, seed_user, amount=str(amount), name=name,
        category_key="Groceries", is_envelope=is_envelope, account=account,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _revert(txn):
    """Reopen *txn* through the door production reverts by (the payment is KEPT, un-dated)."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _paid_from_and_reopened(txn, account, submitted=None):
    """Mark *txn* paid from *account*, then reopen it: the state R-CC44 lists."""
    settle_transaction(txn, submitted=submitted, tender_account_id=account.id)
    db.session.commit()
    _revert(txn)
    return txn


def _payment(txn):
    """Return *txn*'s one covering movement, re-read from the database."""
    db.session.expire_all()
    movements = db.session.get(Transaction, txn.id).covering_movements
    assert len(movements) == 1, f"expected one covering movement, got {len(movements)}"
    return movements[0]


def _groups(seed_user, account_id, observed_on=_OBSERVED_ON):
    """Return ``{block key: block}`` of *account_id*'s panel."""
    return {
        group.key: group
        for group in reconcile_service.outstanding_set(
            _statement(seed_user, account_id, observed_on),
        ).groups
    }


def _settlement_keys(seed_user, account_id, observed_on=_OBSERVED_ON):
    """Return the keys of the blocks *account_id*'s panel lists as "Paid from this account"."""
    return {
        key for key, group in _groups(seed_user, account_id, observed_on).items()
        if group.kind is reconcile_service.OfferKind.SETTLEMENT
    }


def _tick(seed_user, account_id, transaction_ids, corrections=None,
          observed_on=_OBSERVED_ON):
    """Run the write union with *transaction_ids* ticked under the ROW field, and commit."""
    recorded = reconcile_service.record_reconciliation(
        reconcile_service.ReconcileSubmission(
            statement=_statement(seed_user, account_id, observed_on),
            entry_ids=set(),
            transaction_ids=set(transaction_ids),
            corrections=corrections or {},
            transfer_ids=set(),
            transfer_corrections={},
        ),
    )
    db.session.commit()
    return recorded


class TestTheListOffersAReopenedPaymentFromHere:
    """The scope: WHICH rows, one case per direction of each clause."""

    def test_a_bill_paid_from_the_card_and_reopened_is_offered_on_the_card(
        self, app, seed_user, seed_periods,
    ):
        """The ruling's own case, offered under its own section and the ROW's tick form."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)

            group = _groups(seed_user, card.id)[txn.id]

            offer = group.settle
            assert offer.kind is reconcile_service.OfferKind.SETTLEMENT
            assert offer.key == txn.id
            assert offer.tick_form is reconcile_service.TickForm.ROW
            assert offer.amount == _GROCERIES
            assert offer.is_correctable is True
            assert offer.is_income is False
            assert offer.closes_envelope is False
            assert offer.cash_amount is None
            assert group.purchases == ()
            assert group.period.period_id == txn.pay_period_id
            assert group.section == reconcile_service.Section(
                label="Paid from this account",
                note=(
                    "Recorded on this account, then reopened. Ticking one "
                    "records it here on your statement date."
                ),
            )

    def test_the_rows_own_account_leaves_it_out(
        self, app, seed_user, seed_periods,
    ):
        """R-CC126 ("Only the card offers it"): Checking's list leaves it out; the card offers it.

        Until plan step credit_card:CC-5-4a-5c-1 Checking offered the same
        row as a BILL, and its tick re-pointed the card's payment onto
        Checking with no caption (finding CC-378).  The developer ruled the
        behaviour changed (R-CC126, 2026-09-30): "Checking's list leaves it
        out ... Each list offers only money that moved on its own account".
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)

            assert txn.id not in _groups(seed_user, seed_user["account"].id)
            assert txn.id in _settlement_keys(seed_user, card.id)

    def test_a_row_planned_ON_this_account_is_a_bill_here_not_a_settlement(
        self, app, seed_user, seed_periods,
    ):
        """The account clause's other direction: a card's own bill is its bill list's."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], account=card, name="Phone"),
                card,
            )

            groups = _groups(seed_user, card.id)
            assert groups[txn.id].kind is reconcile_service.OfferKind.BILL

    def test_a_payment_on_a_THIRD_account_is_not_offered(
        self, app, seed_user, seed_periods,
    ):
        """Paid from card A: card B's statement cannot show it."""
        with app.app_context():
            card_a = _card(seed_user, "Card A")
            card_b = _card(seed_user, "Card B")
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card_a)

            assert txn.id in _settlement_keys(seed_user, card_a.id)
            assert txn.id not in _groups(seed_user, card_b.id)
            # And a tick of it posted to card B's form settles nothing.
            assert _tick(seed_user, card_b.id, {txn.id}) == 0
            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )

    def test_a_DATED_payment_under_a_projected_row_is_not_offered(
        self, app, seed_user, seed_periods,
    ):
        """Un-dated is load-bearing: R-CC44 lists un-dated payments; a dated one is in the books.

        A dated payment posts and folds on its own day, so it is not
        outstanding on this statement.  The seam keeps a covering movement's
        day in step with its row, so this state is PLANTED: the reopened row's
        payment given back a day.
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)
            assert txn.id in _settlement_keys(seed_user, card.id)
            payment = _payment(txn)
            payment.settled_on = payment.purchased_on
            payment.settled_day_basis_id = ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            assert txn.id not in _groups(seed_user, card.id)

    def test_a_reopened_envelope_holding_a_PURCHASE_is_hidden(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-CC113 ("Hide it"): its purchases are its figure now."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], is_envelope=True, amount="300.00"),
                card, submitted=typed(_GROCERIES),
            )
            assert txn.id in _settlement_keys(seed_user, card.id)

            entry_service.create_entry(
                txn.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("40.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.commit()

            assert txn.id not in _groups(seed_user, card.id)

    def test_a_CANCELLED_row_is_not_offered(self, app, seed_user, seed_periods):
        """Only a Projected row is money the projection still holds."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)
            assert txn.id in _settlement_keys(seed_user, card.id)
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()

            assert txn.id not in _groups(seed_user, card.id)

    def test_it_is_offered_once_the_bill_is_DUE(self, app, seed_user, seed_periods):
        """R-CC118 ("Once the bill is due"): the row's own landing day, as Checking's list asks.

        A bill due on the 14th of a paycheck that started on the 2nd: not on
        the 10th's statement, and on the 14th's.
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _row(seed_user, seed_periods[0])
            txn.due_date = date(2026, 1, 14)
            db.session.commit()
            _paid_from_and_reopened(txn, card)

            assert txn.id not in _groups(seed_user, card.id, date(2026, 1, 10))
            assert txn.id not in _groups(
                seed_user, seed_user["account"].id, date(2026, 1, 10),
            )
            assert txn.id in _settlement_keys(seed_user, card.id, date(2026, 1, 14))


class TestTheOffersWording:
    """Rulings R-CC111, R-CC117 and R-CC119: how a row reads."""

    def test_a_card_reads_paid_from_this_card(self, app, seed_user, seed_periods):
        """R-CC111's row, verbatim in shape."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)

            assert _groups(seed_user, card.id)[txn.id].name == (
                "Groceries (Checking's plan, paid from this card)"
            )

    def test_a_non_card_account_reads_paid_from_this_account(
        self, app, seed_user, seed_periods,
    ):
        """R-CC117: a Savings bill paid from Checking reads 'paid from this account' there."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("100.00"),
            )
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], account=savings,
                     name="Car insurance", amount="90.00"),
                seed_user["account"],
            )

            group = _groups(seed_user, seed_user["account"].id)[txn.id]
            assert group.kind is reconcile_service.OfferKind.SETTLEMENT
            assert group.name == (
                "Car insurance (Savings's plan, paid from this account)"
            )
            assert group.settle.amount == Decimal("90.00")

    def test_a_deposit_reads_received_into_this_account_and_counts_as_one(
        self, app, seed_user, seed_periods,
    ):
        """R-CC111's deposit wording, under the same heading, tallied as a deposit."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], name="Refund",
                     amount="45.00", income=True),
                card,
            )

            outstanding = reconcile_service.outstanding_set(
                _statement(seed_user, card.id),
            )
            group = {g.key: g for g in outstanding.groups}[txn.id]
            assert group.name == (
                "Refund (Checking's plan, received into this account)"
            )
            assert group.settle.is_income is True
            assert outstanding.deposit_count == 1
            assert outstanding.deposit_total == Decimal("45.00")

    def test_an_empty_reopened_envelope_is_offered_as_a_CLOSE(
        self, app, seed_user, seed_periods,
    ):
        """R-CC119 ("Add 'Close'"): the tick closes the envelope, and the block says so."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], is_envelope=True, amount="300.00"),
                card, submitted=typed(_GROCERIES),
            )

            group = _groups(seed_user, card.id)[txn.id]
            assert group.kind is reconcile_service.OfferKind.SETTLEMENT
            assert group.settle.closes_envelope is True
            assert group.settle_closes_an_envelope is True
            assert group.settle.is_correctable is True
            # The kept STATED figure is honoured, not the plan's $300.00.
            assert group.settle.amount == _GROCERIES


class TestTheTick:
    """What a tick does: the bill's own settle, dated by the statement, linked on the payment."""

    def test_a_tick_settles_the_row_from_here_and_links_the_payment_not_the_row(
        self, app, seed_user, seed_periods,
    ):
        """The tick settles the row on the card and links the payment, not the row.

        The gap itself is graded on a real statement by
        :class:`TestTheCardsGapClosesByThePayment`; this case presents the
        card's opening assertion for an earlier day, so it grades the writes
        and the cash facts only.
        """
        with app.app_context():
            card = _card(seed_user)
            checking_id = seed_user["account"].id
            scenario_id = seed_user["scenario"].id
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)
            checking_before = settled_cash_facts(checking_id, scenario_id)

            assert _tick(seed_user, card.id, {txn.id}) == 1

            db.session.expire_all()
            row = db.session.get(Transaction, txn.id)
            payment = _payment(txn)
            anchor_id = cash_ledger.governing_anchor(card.id).anchor_id
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert row.settled_on == _OBSERVED_ON
            assert row.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ASSERTED,
            )
            assert row.reconciled_by_id is None
            assert payment.account_id == card.id
            assert payment.settled_on == _OBSERVED_ON
            assert payment.amount == _GROCERIES
            assert payment.reconciled_by_id == anchor_id
            on_the_day = sum(
                (
                    fact.delta
                    for fact in settled_cash_facts(card.id, scenario_id)
                    if fact.settled_on == _OBSERVED_ON
                ),
                Decimal("0"),
            )
            assert on_the_day == -_GROCERIES
            assert settled_cash_facts(checking_id, scenario_id) == checking_before

    def test_a_typed_figure_is_booked_as_a_correction(
        self, app, seed_user, seed_periods,
    ):
        """R-CC110: the amount box books what the statement shows."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)

            assert _tick(
                seed_user, card.id, {txn.id}, {txn.id: Decimal("118.50")},
            ) == 1

            payment = _payment(txn)
            assert payment.amount == Decimal("118.50")
            assert payment.figure_source_id == ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )

    def test_an_untyped_figure_is_repriced_and_the_box_books_the_statements(
        self, app, seed_user, seed_periods,
    ):
        """R-CC110's worked case: offered at the re-priced $125, typed 120, paid $120.00."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)
            state_template_price(
                txn.template, Decimal("125.00"),
                effective_on=seed_periods[0].start_date,
            )
            db.session.commit()

            assert _groups(seed_user, card.id)[txn.id].settle.amount == (
                Decimal("125.00")
            )
            _tick(seed_user, card.id, {txn.id}, {txn.id: _GROCERIES})

            assert _payment(txn).amount == _GROCERIES

    def test_the_bill_list_and_this_list_share_ONE_field(
        self, app, seed_user, seed_periods,
    ):
        """R-CC116: a card's own bill and a Checking bill paid from it land in one submission."""
        with app.app_context():
            card = _card(seed_user)
            own = _row(seed_user, seed_periods[0], account=card, name="Phone",
                       amount="45.00")
            elsewhere = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0]), card,
            )

            assert _tick(seed_user, card.id, {own.id, elsewhere.id}) == 2

            done = ref_cache.status_id(StatusEnum.DONE)
            db.session.expire_all()
            assert db.session.get(Transaction, own.id).status_id == done
            assert db.session.get(Transaction, elsewhere.id).status_id == done

    def test_a_row_neither_scope_offers_settles_nothing(
        self, app, seed_user, seed_periods,
    ):
        """A Checking bill with no card payment, forged onto the card: 0, still Projected."""
        with app.app_context():
            card = _card(seed_user)
            checking_bill = _row(seed_user, seed_periods[0], name="Electricity")

            assert _tick(seed_user, card.id, {checking_bill.id}) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, checking_bill.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )

    def test_once_settled_here_it_leaves_the_rows_own_list_too(
        self, app, seed_user, seed_periods,
    ):
        """Whichever tick lands first leaves the other list nothing to settle."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)
            _tick(seed_user, card.id, {txn.id})

            assert txn.id not in _groups(seed_user, seed_user["account"].id)
            assert _tick(seed_user, seed_user["account"].id, {txn.id}) == 0

    def test_a_ticked_deposit_is_received_into_this_account(
        self, app, seed_user, seed_periods,
    ):
        """A deposit settles Received, and its money arrives on the card on the statement's day."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], name="Refund",
                     amount="45.00", income=True),
                card,
            )

            assert _tick(seed_user, card.id, {txn.id}) == 1

            db.session.expire_all()
            row = db.session.get(Transaction, txn.id)
            payment = _payment(txn)
            assert row.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
            assert payment.account_id == card.id
            assert payment.settled_on == _OBSERVED_ON
            on_the_day = sum(
                (
                    fact.delta
                    for fact in settled_cash_facts(card.id, seed_user["scenario"].id)
                    if fact.settled_on == _OBSERVED_ON
                ),
                Decimal("0"),
            )
            assert on_the_day == Decimal("45.00")

    def test_a_ticked_empty_envelope_closes_at_its_kept_figure(
        self, app, seed_user, seed_periods,
    ):
        """R-CC119's act: the tick closes the envelope at the kept typed $120, on the card."""
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0], is_envelope=True, amount="300.00"),
                card, submitted=typed(_GROCERIES),
            )

            assert _tick(seed_user, card.id, {txn.id}) == 1

            db.session.expire_all()
            payment = _payment(txn)
            assert db.session.get(Transaction, txn.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )
            assert payment.account_id == card.id
            assert payment.amount == _GROCERIES
            assert payment.settled_on == _OBSERVED_ON

    def test_a_zero_tick_is_refused_and_changes_nothing(
        self, app, seed_user, seed_periods,
    ):
        """R-CC125 ("Refuse it now"): a typed $0.00 is refused; the row and its payment stay.

        Until plan step credit_card:CC-5-4a-5c-1 a typed $0.00 settled the
        row with no payment at all.  The developer ruled the behaviour
        changed (R-CC125, 2026-09-30): "The panel refuses a $0.00 payment
        today ... Nothing changes".
        """
        with app.app_context():
            card = _card(seed_user)
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)

            with pytest.raises(ValidationError) as refused:
                _tick(seed_user, card.id, {txn.id}, {txn.id: Decimal("0.00")})
            db.session.rollback()

            assert str(refused.value) == (
                "Groceries: a payment can't be $0.00. If it wasn't paid, "
                "leave it unticked or cancel it on the grid."
            )
            payment = _payment(txn)
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert payment.account_id == card.id
            assert payment.amount == _GROCERIES
            assert payment.settled_on is None
            assert row.reconciled_by_id is None


class TestTheTwoRowScopesLogOneFieldsTicks:
    """Both row scopes read the ROW field (R-CC116), and each event says so in its counts."""

    def test_each_event_counts_its_own_settles_and_the_whole_row_field(
        self, app, seed_user, seed_periods, caplog,
    ):
        """A card bill, a card-paid Checking bill (typed 118.50), an id no scope offers: 2 land.

        Each scope's event reports what IT settled and corrected, and the
        ROW field's whole posted set as ``requested_count`` -- the documented
        meaning since the field became shared; the route's own notice
        compares the submission with what landed across both.
        """
        with app.app_context():
            card = _card(seed_user)
            own = _row(seed_user, seed_periods[0], account=card, name="Phone",
                       amount="45.00")
            elsewhere = _paid_from_and_reopened(
                _row(seed_user, seed_periods[0]), card,
            )
            neither = _row(seed_user, seed_periods[0], name="Electricity")

            with caplog.at_level("INFO"):
                recorded = _tick(
                    seed_user, card.id, {own.id, elsewhere.id, neither.id},
                    {elsewhere.id: Decimal("118.50")},
                )

            def counts(event):
                records = [
                    record for record in caplog.records
                    if getattr(record, "event", None) == event
                ]
                assert len(records) == 1, f"{event}: {len(records)} records"
                record = records[0]
                return (
                    record.settled_count, record.requested_count,
                    record.corrected_count,
                )

            # Derived from the code: both scopes are handed all 3 row ids
            # (requested 3 each); ARM loads only `own` (on the card) and books
            # it untyped (0 corrected); SETTLEMENT_ARM loads only `elsewhere`
            # (its un-dated payment is on the card) and books the typed 118.50
            # against the $120.00 it would book (1 corrected); `neither` has no
            # payment on the card, so no scope loads it.
            assert recorded == 2
            assert counts(EVT_TRANSACTIONS_RECONCILED) == (1, 3, 0)
            assert counts(EVT_SETTLEMENTS_RECONCILED) == (1, 3, 1)


class TestAStatementLinksEveryFactOnItsOwnAccount:
    """``status_seam.record_clearing``'s one rule, called bare."""

    def test_a_card_statement_links_the_card_payment_and_not_the_row(
        self, app, seed_user, seed_periods,
    ):
        """The shape the old body got wrong: it linked the row, which the row's key refuses."""
        with app.app_context():
            card = _card(seed_user)
            txn = _row(seed_user, seed_periods[0])
            settle_transaction(txn, tender_account_id=card.id)
            anchor_id = cash_ledger.governing_anchor(card.id).anchor_id

            status_seam.record_clearing(txn, anchor_id)
            db.session.commit()

            payment = _payment(txn)
            assert payment.reconciled_by_id == anchor_id
            assert db.session.get(Transaction, txn.id).reconciled_by_id is None

    def test_a_statement_of_the_rows_account_links_the_row_and_its_payment_there(
        self, app, seed_user, seed_periods,
    ):
        """The shape every caller before CC-5-4b passed, written as before."""
        with app.app_context():
            txn = _row(seed_user, seed_periods[0])
            settle_transaction(txn)
            anchor_id = cash_ledger.governing_anchor(
                seed_user["account"].id,
            ).anchor_id

            status_seam.record_clearing(txn, anchor_id)
            db.session.commit()

            assert _payment(txn).reconciled_by_id == anchor_id
            assert db.session.get(Transaction, txn.id).reconciled_by_id == anchor_id


class TestTheCardsGapClosesByThePayment:
    """R-CC44's worked result on a real statement: the books-vs-bank difference closes by $120."""

    def test_the_statements_correction_goes_to_zero_and_checking_does_not_move(
        self, app, seed_user, seed_periods,
    ):
        """Card asserted -$500 on 1/5 and -$620 on 1/10: the reopened $120 is the 1/10 gap.

        The statement is the 1/10 ASSERTION itself (its own day, not a later
        assertion presented for an earlier one), so that assertion's own
        posted correction -- its true-up legs dated 1/10, not the pay period's
        net, which would net the 1/5 origination assertion's in too -- is the
        books-vs-bank difference: -$120.00 before the tick, $0.00 after.
        Checking's cash facts and posted ledger do not move -- the money was
        never Checking's.  (The per-day read is inline, as
        ``test_anchor_settle_partition`` reads one: ``_test_helpers`` has the
        per-PERIOD one only, and ``linked_net_by_date`` would net the
        payment's own leg in.)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking_id = seed_user["account"].id
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"), observed_on=date(2026, 1, 5),
            )
            db.session.commit()
            txn = _paid_from_and_reopened(_row(seed_user, seed_periods[0]), card)
            anchor_service.apply_anchor_true_up(
                account=card, new_balance=Decimal("-620.00"),
                observed_on=_OBSERVED_ON,
            )
            statement = reconcile_service.Statement(
                calendar_for(seed_user["user"].id), card.id,
                cash_ledger.governing_anchor(card.id),
            )
            assert statement.observed_on == _OBSERVED_ON
            card_ledger = linked_ledger_account(db.session, card.id).id
            checking_ledger = linked_ledger_account(db.session, checking_id).id

            def correction():
                """The 1/10 assertion's OWN posted correction: its true-up legs dated 1/10."""
                return (
                    db.session.query(
                        db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00")),
                    )
                    .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                    .filter(
                        Posting.ledger_account_id == card_ledger,
                        JournalEntry.scenario_id == scenario_id,
                        JournalEntry.entry_date == _OBSERVED_ON,
                        JournalEntry.source_kind_id == ref_cache.posting_source_id(
                            PostingSourceEnum.ACCOUNT_TRUEUP,
                        ),
                    )
                    .scalar()
                )

            checking_facts = settled_cash_facts(checking_id, scenario_id)
            checking_net = ledger_net(db.session, checking_ledger, scenario_id)
            assert correction() == Decimal("-120.00")

            recorded = reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    statement=statement, entry_ids=set(),
                    transaction_ids={txn.id}, corrections={},
                    transfer_ids=set(), transfer_corrections={},
                ),
            )
            db.session.commit()

            assert recorded == 1
            assert correction() == Decimal("0.00")
            payment = _payment(txn)
            assert payment.account_id == card.id
            assert payment.settled_on == _OBSERVED_ON
            assert payment.reconciled_by_id == statement.anchor.anchor_id
            assert settled_cash_facts(checking_id, scenario_id) == checking_facts
            assert ledger_net(db.session, checking_ledger, scenario_id) == checking_net
