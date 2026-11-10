"""What the card's findings require of the worked-out read: a source that left the settled set has
no line (N-162), a moved settle day is read where it lands (N-310), a transfer's effect has one
rule (N-164), a refund, a card purchase, an over-posted envelope and a purchase beside a stated
close book what moved (N-318), and a loan's terms written by id mid-session are read (plan#2691).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.loan_params import LoanParams
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, transaction_service, transfer_service
from app.services.cash_ledger import settled_cash_facts
from app.services.ledger_lines import (
    LedgerLine,
    category_key,
    linked_key,
    loan_key,
    transit_key,
)
from app.services.settle_day import record_settle_day
from app.services.transfer_legs import transfer_family_movements
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    create_settled_transfer,
    figure_source_columns,
    generate_row_of,
    insert_origination_rate,
    make_expense_template,
    typed,
)

from ._helpers import _TODAY, _read, _lines_of, _source, _kind
from tests._legacy_credit import plant_old_credit, plant_old_flagged_purchase


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


def _purchase(seed_user, txn, amount, *, is_credit=False, posted_on=_TODAY):
    """Record a purchase of *amount* on *txn*, made and posted on *posted_on*.

    *is_credit* plants an old flagged line as the add door wrote it until plan
    task plan#2930, which files a ticked purchase on the card instead.
    """
    record = plant_old_flagged_purchase if is_credit else entry_service.create_entry
    return record(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        details=entry_service.EntryDetails(
            figure=typed(Decimal(amount)),
            description="Market",
            purchased_on=posted_on,
            settle_day=an_entered_day(posted_on),
        ),
    )


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _revert_then_credit(txn):
    """Revert *txn*, then put it in the old Credit shape, which leaves the plan.

    Planted (``tests._legacy_credit``): since plan task plan#2929 the press
    settles a row on its card, so it no longer takes a row out of the plan.
    """
    _revert(txn)
    _db.session.flush()
    plant_old_credit(txn)


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


def _soft_delete_transfer(xfer):
    """Soft-delete *xfer* through the transfer door."""
    transfer_service.delete_transfer(xfer.id, xfer.user_id, soft=True)


def _revert_transfer(xfer):
    """Put *xfer* back to Projected through the transfer door."""
    transfer_service.update_transfer(
        xfer.id, xfer.user_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _clear_bank_day(purchase):
    """Clear *purchase*'s bank posting day through the purchase door."""
    entry_service.update_entry(purchase.id, purchase.owner_id, settle_day=None)


def _delete_purchase(purchase):
    """Delete *purchase* through the purchase door."""
    entry_service.delete_entry(purchase.id, purchase.owner_id)


def _checked_today(checking):
    """Add a $5,000.00 balance check on *checking*, observed today; flush."""
    _db.session.add(AccountAnchorHistory(
        account_id=checking.id, anchor_balance=Decimal("5000.00"),
        observed_on=_TODAY, recorded_on=_TODAY,
    ))
    _db.session.flush()


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
            _checked_today(checking)
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

    @pytest.mark.parametrize(
        "leave", [_soft_delete_transfer, _revert_transfer],
        ids=["soft-delete", "revert"],
    )
    def test_a_transfer_that_left_reads_as_if_it_never_settled(
        self, app, db, seed_user, seed_periods, leave,
    ):
        """A settled $250.00 transfer soft-deleted or reverted: the read is as before it.

        A soft delete takes both sides' records with it; a revert keeps them
        and un-dates them, the most common way a transfer leaves.  Either
        way neither side's two lines stay, and the read is the one taken
        before the transfer.
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

            leave(xfer)
            _db.session.flush()

            assert _read(seed_user) == before

    @pytest.mark.parametrize(
        "leave", [_clear_bank_day, _delete_purchase],
        ids=["bank-day-cleared", "delete"],
    )
    def test_a_purchase_that_left_reads_as_if_it_never_posted(
        self, app, db, seed_user, seed_periods, leave,
    ):
        """A $40.00 purchase posted yesterday, then un-posted or deleted: as before it.

        Checking opens at $1,000.00 under today's $5,000.00 balance check, so
        before the purchase the true-up is ``4,000.00``.  Posted yesterday on
        an open Groceries envelope, the purchase books Checking ``-40.00`` and
        Groceries ``+40.00``, and the check absorbs it: ``5,000.00 -
        (1,000.00 - 40.00) = 4,040.00`` (the controls).  Clearing its bank
        day puts it back in flight, off every dated read, and deleting it
        removes it; either way the whole read is the one from before it.
        """
        with app.app_context():
            checking = seed_user["account"]
            envelope = _envelope(seed_user, seed_periods[0], "200.00")
            _checked_today(checking)
            before = _read(seed_user)
            assert _true_ups_of(before, checking) == [Decimal("4000.00")]
            purchase = _purchase(
                seed_user, envelope, "40.00",
                posted_on=_TODAY - timedelta(days=1),
            )
            posted = _read(seed_user)
            assert sorted(
                line.amount for line in _lines_of(posted, purchase.id)
            ) == [Decimal("-40.00"), Decimal("40.00")]
            assert _true_ups_of(posted, checking) == [Decimal("4040.00")]

            leave(purchase)
            _db.session.flush()

            assert _read(seed_user) == before


class TestAMovedSettleDayIsReadWhereItLands:
    """Finding N-310 (plan#1310): what a moved settle day owes the corrections.

    Nothing special: the read is the records each time, so a settle day
    moved across a balance check moves the movement's lines and the check's
    true-up with it, both ways.  ``TestASettleDayMoveAcrossAnAssertion``
    (``test_account_posting_service.py``) shows the stored half through the
    same door.
    """

    def test_a_day_moved_past_a_balance_check_and_back_moves_its_true_up(
        self, app, db, seed_user, seed_periods,
    ):
        """A $148.32 bill settled May 12, moved past a May 13 check to May 14, then back.

        Checking opens at $1,000.00 and a $5,000.00 balance check is observed
        May 13.  Settled May 12, the bill is before the check, which absorbs
        it: the true-up is ``5,000.00 - (1,000.00 - 148.32) = 4,148.32``.
        Moved to May 14 through the door a settle-day correction takes
        (``apply_requested_status`` at the row's own status), it rides on top
        of the check: ``5,000.00 - 1,000.00 = 4,000.00``, and its two lines
        are dated May 14.  Moved back, the read is the first one again: the
        second time the read is asked, after a true-up it must now restore.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = _bill(seed_user, seed_periods[0])
            _db.session.add(AccountAnchorHistory(
                account_id=checking.id, anchor_balance=Decimal("5000.00"),
                observed_on=date(2026, 5, 13), recorded_on=date(2026, 5, 13),
            ))
            transaction_service.settle_transaction(
                txn, settle_day=an_entered_day(date(2026, 5, 12)),
            )
            _db.session.flush()
            first = _read(seed_user)
            assert _true_ups_of(first, checking) == [Decimal("4148.32")]

            for moved_to, true_up in (
                (date(2026, 5, 14), Decimal("4000.00")),
                (date(2026, 5, 12), Decimal("4148.32")),
            ):
                transaction_service.apply_requested_status(
                    txn, txn.status_id, settle_day=an_entered_day(moved_to),
                )
                _db.session.flush()
                (movement,) = txn.covering_movements
                lines = _read(seed_user)
                assert {
                    line.entry_date for line in _lines_of(lines, movement.id)
                } == {moved_to}, f"moved to {moved_to}"
                assert _true_ups_of(lines, checking) == [true_up]

            assert lines == first


def _folded_cash(account, movement, scenario_id):
    """Return the cash fold's delta for *movement* on *account*: its one fact's.

    The fold's own producer (``settled_cash_facts``, through
    ``movement_cash_leg``), so a line equal to it is the balance's valuation
    of the same movement.  A movement the fold holds no fact for, or two,
    fails the unpacking.
    """
    (fact,) = [
        fact for fact in settled_cash_facts(account.id, scenario_id)
        if fact.entry_id == movement.id
    ]
    return fact.delta


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
            assert lines == [
                LedgerLine(
                    linked_key(checking), xfer.pay_period_id, out.settled_on,
                    Decimal("-250.00"), moved, transfer, out.id,
                ),
                LedgerLine(
                    transit_key(), xfer.pay_period_id, out.settled_on,
                    Decimal("250.00"), moved, transfer, out.id,
                ),
                LedgerLine(
                    linked_key(savings), xfer.pay_period_id, back.settled_on,
                    Decimal("250.00"), moved, transfer, back.id,
                ),
                LedgerLine(
                    transit_key(), xfer.pay_period_id, back.settled_on,
                    Decimal("-250.00"), moved, transfer, back.id,
                ),
            ]
            assert (lines[0].amount, lines[2].amount) == (
                _folded_cash(checking, out, seed_user["scenario"].id),
                _folded_cash(savings, back, seed_user["scenario"].id),
            )


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
        """A $367.62 purchase on a $163.95 envelope, then closed: -367.62 out.

        A row holding a purchase settles on its purchases (ruling R-BAL78),
        so the close writes no covering movement and records $367.62, past
        the envelope's budget.  Each purchase books its own figure in its
        parent's direction and the row books nothing (ruling R-BAL80): the
        one movement is Checking ``-367.62`` against Groceries ``+367.62``,
        and nothing is booked against the envelope's $163.95.  This is not
        N-318's state, which a close holding no purchase leaves (the next
        case).
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

    def test_a_purchase_beside_a_stated_close_books_each_movement_at_its_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """N-318's own state, written past the door: -163.95 and -367.62, never +203.67.

        A $163.95 Groceries envelope closed with nothing spent settles at its
        stated figure, so its close is ONE covering movement of $163.95.  The
        purchase door refuses a $367.62 purchase on it (the control,
        ``entry_service._reject_settled_addition``: the pair records the
        close twice, ruling R-BAL80), so the case writes it past the door,
        posted today.  Through ``X-bi-3e`` a settled row's own leg netted
        its posted purchases against its recorded figure and negated the
        result, ``-(163.95 - 367.62) = +203.67`` on Checking: an expense row
        publishing an inflow.  Worked out from the records each movement
        books its own figure in its parent's direction, so Checking's
        ``purchase`` lines are exactly ``-163.95`` and ``-367.62``, each
        against Groceries, and none is ``+203.67``.
        """
        with app.app_context():
            checking = seed_user["account"]
            envelope = _envelope(seed_user, seed_periods[0], "163.95")
            transaction_service.settle_transaction(envelope)
            _db.session.flush()
            (close,) = envelope.covering_movements
            assert close.amount == Decimal("163.95")
            with pytest.raises(ValidationError, match="records a fixed figure"):
                _purchase(seed_user, envelope, "367.62")
            past_the_door = TransactionEntry(
                **figure_source_columns(),
                transaction_id=envelope.id, account_id=checking.id,
                owner_id=envelope.user_id, user_id=envelope.user_id,
                amount=Decimal("367.62"), description="Past the door",
                purchased_on=_TODAY, is_credit=False,
            )
            record_settle_day(past_the_door, an_entered_day(_TODAY))
            _db.session.add(past_the_door)
            _db.session.flush()

            lines = _read(seed_user)

            groceries = category_key(
                seed_user["categories"]["Groceries"].id,
                LedgerAccountClassEnum.EXPENSE,
            )
            for movement, figure in (
                (close, Decimal("163.95")), (past_the_door, Decimal("367.62")),
            ):
                assert [
                    (line.key, line.amount)
                    for line in _lines_of(lines, movement.id)
                ] == [(linked_key(checking), -figure), (groceries, figure)]
            assert sorted(
                line.amount for line in lines
                if line.key == linked_key(checking)
                and line.source_kind_id == _source(PostingSourceEnum.PURCHASE)
            ) == [Decimal("-367.62"), Decimal("-163.95")]


class TestALoansTermsAreReadAsTheyStand:
    """Finding plan#2691: terms written by id after the read loaded the account are read."""

    def test_terms_written_by_account_id_in_the_session_are_walked(
        self, app, db, seed_user, seed_periods,
    ):
        """A $20,000.00 loan's terms written by id mid-session book its opening.

        The loan setup route's shape (``routes/loan/params.py``): the account
        exists, its terms are then written as ``LoanParams(account_id=...)``
        in the session that already loaded the account.  The first read
        loads it with ``Account.loan_params`` as ``None``, and a loaded
        relationship is not re-read by a later query; while the read took a
        loan's terms off it, the second read walked the loan to nothing and
        dropped its ``-20,000.00`` opening and ``+20,000.00`` opening equity.
        Read by the loans' ids, the second read is the one a fresh session
        reads after the commit, the opening in it.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Auto Loan", "Van Loan",
            )
            _db.session.commit()
            assert _read(seed_user) is not None
            assert loan.loan_params is None
            params = LoanParams(
                account_id=loan.id, original_principal=Decimal("20000.00"),
                term_months=60, origination_date=date(2026, 1, 15),
                payment_day=15,
            )
            _db.session.add(params)
            _db.session.flush()
            insert_origination_rate(params, Decimal("0.05000"))
            _db.session.flush()

            in_the_session = _read(seed_user)
            _db.session.commit()
            _db.session.expire_all()
            fresh = _read(seed_user)

            assert in_the_session == fresh
            opening = [
                (line.key, line.amount) for line in fresh
                if line.source_kind_id == _source(PostingSourceEnum.LOAN_OPENING)
            ]
            assert opening == [
                (linked_key(loan), Decimal("-20000.00")),
                (loan_key(LedgerAccountKindEnum.EQUITY_OPENING, loan.id),
                 Decimal("20000.00")),
            ]
