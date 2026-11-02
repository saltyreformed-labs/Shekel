"""What the card's findings require of the worked-out read: a source that left the settled set has
no line (N-162), a transfer's effect has one rule (N-164), and a refund, a card purchase and an
over-posted envelope book what moved (N-318).
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum, PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services import credit_workflow, entry_service, transaction_service, transfer_service
from app.services.cash_ledger import settled_cash_facts
from app.services.ledger_lines import LedgerLine, category_key, linked_key, transit_key
from app.services.transfer_legs import transfer_family_movements
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
    typed,
)

from ._helpers import _TODAY, _read, _lines_of, _source, _kind


def _true_ups_of(lines, account):
    """Return the amounts of *account*'s own true-up lines, in the read's order."""
    return [
        line.amount for line in lines
        if line.key == linked_key(account)
        and line.source_kind_id == _source(PostingSourceEnum.ACCOUNT_TRUEUP)
    ]


def _bill(seed_user, period):
    """Return a fresh $148.32 Rent bill generated into *period*, Projected."""
    template = make_expense_template(
        _db.session, seed_user, amount="148.32", name="Electric",
        category_key="Rent",
    )
    return generate_row_of(template, period)


def _envelope(seed_user, period, amount):
    """Return a fresh Groceries envelope of *amount* generated into *period*."""
    template = make_expense_template(
        _db.session, seed_user, amount=amount, name="Groceries Run",
        category_key="Groceries", is_envelope=True,
    )
    return generate_row_of(template, period)


def _purchase(seed_user, txn, amount, *, is_credit=False):
    """Record a purchase of *amount* on *txn*, made and posted today."""
    return entry_service.create_entry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        details=entry_service.EntryDetails(
            figure=typed(Decimal(amount)),
            description="Market",
            purchased_on=_TODAY,
            is_credit=is_credit,
            settle_day=an_entered_day(_TODAY),
        ),
    )


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _revert_then_credit(txn):
    """Revert *txn*, then mark it Credit through its door."""
    _revert(txn)
    _db.session.flush()
    credit_workflow.mark_as_credit(txn.id, txn.user_id)


def _revert_then_cancel(txn):
    """Revert *txn*, then cancel it through the status door."""
    _revert(txn)
    _db.session.flush()
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.CANCELLED),
    )


def _delete(txn):
    """Delete *txn* through the one removal act (soft: its definition recurs)."""
    transaction_service.delete_transaction(txn, txn.user_id)


class TestASourceThatLeftHasNoLine:
    """Finding N-162 (plan#1356): a source that left the settled set drops out.

    A walk that re-derives one account cannot see a source that LEFT it,
    because the walk reads only what is still there; the stored ledger needs
    the reversal a door writes as the source leaves.  The worked-out read has
    no store to reverse: it is the records each time it is read, so a source
    that left reads as one that never settled -- its movement lines AND what
    it had done to every account's corrections.
    """

    @pytest.mark.parametrize(
        "leave",
        [_revert, _revert_then_credit, _revert_then_cancel, _delete],
        ids=["revert", "revert-then-credit", "revert-then-cancel", "delete"],
    )
    def test_a_bill_that_left_reads_as_if_it_never_settled(
        self, app, db, seed_user, seed_periods, leave,
    ):
        """A $148.32 bill settled, then taken out: the read is as before it settled.

        Checking opens at $1,000.00 and a $5,000.00 balance check is observed
        today, so before the settle its true-up is ``5,000.00 - 1,000.00 =
        4,000.00``.  Settled yesterday, the bill's covering movement books
        Checking ``-148.32`` and Rent ``+148.32``, and the check, the closing
        balance for a later day, absorbs it: the true-up becomes ``5,000.00
        - (1,000.00 - 148.32) = 4,148.32`` (the controls: the source entered
        the movement lines AND moved a correction).  Each exit takes the
        movement out of the dated set -- a revert un-dates it, Credit and
        Cancel follow a revert, a delete removes the row with its movements
        -- and the whole read, the true-up back at 4,000.00 included, is then
        the read from before the settle.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = _bill(seed_user, seed_periods[0])
            _db.session.add(AccountAnchorHistory(
                account_id=checking.id, anchor_balance=Decimal("5000.00"),
                observed_on=_TODAY, recorded_on=_TODAY,
            ))
            _db.session.flush()
            before = _read(seed_user)
            assert _true_ups_of(before, checking) == [Decimal("4000.00")]
            transaction_service.settle_transaction(
                txn, settle_day=an_entered_day(_TODAY - timedelta(days=1)),
            )
            _db.session.flush()
            (movement,) = txn.covering_movements
            settled = _read(seed_user)
            assert sorted(
                line.amount for line in _lines_of(settled, movement.id)
            ) == [Decimal("-148.32"), Decimal("148.32")]
            assert _true_ups_of(settled, checking) == [Decimal("4148.32")]

            leave(txn)
            _db.session.flush()

            assert _read(seed_user) == before

    def test_a_soft_deleted_transfer_reads_as_if_it_never_settled(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled $250.00 transfer soft-deleted: the read is as before it.

        Its soft delete takes both sides' records with it, so neither side's
        two lines stay and the read is the one taken before the transfer.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Left Savings",
            )
            _db.session.flush()
            before = _read(seed_user)
            xfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            assert len([
                line for line in _read(seed_user) if line.movement_id
            ]) == 4

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            _db.session.flush()

            assert _read(seed_user) == before


class TestATransferHasOneRule:
    """Finding N-164 (plan#1358): a transfer's effect is the movement rule's alone."""

    def test_each_side_is_its_own_movements_two_lines_valued_as_the_fold_values_it(
        self, app, db, seed_user, seed_periods,
    ):
        """$250.00 Checking -> Savings: two movements, four lines, one rule.

        From-side ``{Checking -250.00, Transit +250.00}``, to-side
        ``{Savings +250.00, Transit -250.00}``, each under the
        ``transfer_movement`` source and the ``transfer`` leg kind, in the
        transfer's paycheck on its own record's day; the transit line nets
        to zero.  No other line comes from a movement.  Each side's cash line
        is the cash fold's fact for the same movement (``settled_cash_facts``,
        through ``movement_cash_leg``), so the posted effect and the balance
        are one valuation, not two rules that agree.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "One Rule Savings",
            )
            xfer = create_settled_transfer(
                seed_user, _db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"),
            )
            movements = {
                movement.account_id: movement
                for movement, _leg in transfer_family_movements(xfer)
            }
            out, back = movements[checking.id], movements[savings.id]

            lines = [line for line in _read(seed_user) if line.movement_id]

            moved = _source(PostingSourceEnum.TRANSFER_MOVEMENT)
            transfer = _kind(PostingKindEnum.TRANSFER)
            period = xfer.pay_period_id
            assert lines == [
                LedgerLine(
                    linked_key(checking), period, out.settled_on,
                    Decimal("-250.00"), moved, transfer, out.id,
                ),
                LedgerLine(
                    transit_key(), period, out.settled_on, Decimal("250.00"),
                    moved, transfer, out.id,
                ),
                LedgerLine(
                    linked_key(savings), period, back.settled_on,
                    Decimal("250.00"), moved, transfer, back.id,
                ),
                LedgerLine(
                    transit_key(), period, back.settled_on,
                    Decimal("-250.00"), moved, transfer, back.id,
                ),
            ]
            for account, movement, line in (
                (checking, out, lines[0]), (savings, back, lines[2]),
            ):
                (fact,) = [
                    fact for fact in settled_cash_facts(
                        account.id, seed_user["scenario"].id,
                    )
                    if fact.entry_id == movement.id
                ]
                assert line.amount == fact.delta


class TestRefundsCardsAndOverPostedEnvelopes:
    """Finding N-318 (plan#1304), re-measured through the read, and its kin."""

    def test_a_refund_books_money_back_against_its_category(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``-$28.29`` refund under a Groceries envelope: Checking +28.29.

        Money coming back is the cash leg ``+28.29`` and a contra-expense
        ``-28.29`` on Groceries -- the rule is arithmetic, not a case
        analysis over the sign (ruling bank_import:R-II).
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "200.00")
            refund = _purchase(seed_user, envelope, "-28.29")

            lines = _lines_of(_read(seed_user), refund.id)

            groceries = seed_user["categories"]["Groceries"].id
            assert [(line.key, line.amount) for line in lines] == [
                (linked_key(seed_user["account"]), Decimal("28.29")),
                (
                    category_key(groceries, LedgerAccountClassEnum.EXPENSE),
                    Decimal("-28.29"),
                ),
            ]

    def test_a_card_purchase_books_no_line(
        self, app, db, seed_user, seed_periods,
    ):
        """A $60.00 card purchase posted today: no line.

        A card purchase never touches the account its envelope pays from; it
        leaves later through its own CC Payback, which is a row of its own.
        The control: a $15.00 debit purchase on the same envelope books its
        two lines.
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "200.00")
            card = _purchase(seed_user, envelope, "60.00", is_credit=True)
            debit = _purchase(seed_user, envelope, "15.00")

            lines = _read(seed_user)

            assert _lines_of(lines, card.id) == []
            assert sorted(
                line.amount for line in _lines_of(lines, debit.id)
            ) == [Decimal("-15.00"), Decimal("15.00")]

    def test_a_purchase_past_its_envelopes_figure_books_its_whole_figure_out(
        self, app, db, seed_user, seed_periods,
    ):
        """A $367.62 purchase on a $163.95 envelope, then closed: -367.62, never +203.67.

        Finding N-318's own figures: through ``X-bi-3e`` the cash helper
        netted a settled row's purchases against its recorded figure and
        negated the result, so an envelope recording ``$163.95`` with a
        ``$367.62`` purchase read ``-(163.95 - 367.62) = +203.67``, an
        expense row publishing an inflow.  Worked out from the records each
        purchase books its own figure in its parent's direction and the row
        books nothing (ruling R-BAL80), so the closed envelope's one
        movement is Checking ``-367.62`` against Groceries ``+367.62`` and no
        line nets anything against the envelope's figure.  Every line booked
        under the ``purchase`` source is read, with a movement or without,
        so a line netted against the row itself -- the shape N-318 was, which
        comes from no movement -- would show here as a line with none.
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "163.95")
            purchase = _purchase(seed_user, envelope, "367.62")
            transaction_service.settle_transaction(envelope)
            _db.session.flush()

            purchase_source = _source(PostingSourceEnum.PURCHASE)
            lines = [
                line for line in _read(seed_user)
                if line.source_kind_id == purchase_source
            ]

            groceries = seed_user["categories"]["Groceries"].id
            assert [
                (line.movement_id, line.key, line.amount) for line in lines
            ] == [
                (
                    purchase.id, linked_key(seed_user["account"]),
                    Decimal("-367.62"),
                ),
                (
                    purchase.id,
                    category_key(groceries, LedgerAccountClassEnum.EXPENSE),
                    Decimal("367.62"),
                ),
            ]
            assert _db.session.query(TransactionEntry).filter_by(
                transaction_id=envelope.id,
            ).count() == 1
