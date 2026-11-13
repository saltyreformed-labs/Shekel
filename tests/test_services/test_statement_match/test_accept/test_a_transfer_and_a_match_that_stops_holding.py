"""A transfer is matched through its own service, and an accepted match stops agreeing when it
stops holding, as when a purchase leaves the account.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.services import transaction_service

from tests._test_helpers import an_entered_day, an_observed_day, on_both_sides

from tests.test_services.test_statement_match._builders import (
    accepted_acts,
    a_bank_line,
    a_purchase,
    a_transaction,
    an_import,
)
from app.services.settle_day import SettleDay, recorded_settle_day

from ._helpers import _submit


class TestATransferIsMatchedThroughItsService:
    """The kind the bank shows most, and the one that must not be touched directly.

    **13 of 22 non-swipe one-to-one matches on the developer's own 2026-08-16
    statement export were transfer shadows** -- the mortgage transfer, the Van
    Loan payment, the money market moves -- and 9 of those carried a day the
    bank corrects.  So this is the ordinary case rather than an exotic one, and
    it must go through ``transfer_service``, as every write of a transfer does
    (``CLAUDE.md``); ``transaction_service.settle_transaction`` REFUSED a shadow
    until plan step balance:X-bi-6-5 (the shadows went at balance:X-bi-6-4d-2).
    """

    @staticmethod
    def _a_transfer(db, seed_user, *, amount="75.00", settled=False):
        """Return a transfer off the seeded checking account into a new Savings.

        Built through ``transfer_service.create_transfer``, the sole creation
        chokepoint, so the pair and its invariants are the real ones.  It
        returned the transfer's Checking SHADOW until plan step
        ``balance:X-bi-6-4d-2``, when each side's record moved off its shadow
        onto the transfer (ruling **R-BAL88**): the class reads a side as its
        record (:meth:`_record`) and ticks it as the transfer (ruling
        **R-BAL167** classes 1 and 4).

        Args:
            db: The session fixture.
            seed_user: The seeded user bundle.
            amount: The transfer amount.
            settled: Whether to settle the pair first, so the match CORRECTS a
                day rather than settling one.

        Returns:
            The :class:`~app.models.transfer.Transfer`.
        """
        from app.models.account import Account  # local: this class only
        from app.services import account_service, transfer_service
        from tests._test_helpers import (
            create_transfer,
            open_books_before_the_first_assertion,
        )

        destination = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=seed_user["account"].account_type_id,
                name="Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
        )
        db.session.flush()
        # **Its BOOKS open before the bootstrap period** (plan step X-f3c-2b,
        # ruling **R-HG**).  ``observed_on`` above puts the origination
        # ASSERTION on the period's first day, which is right -- and it puts
        # the books there too, while the transfers this class matches settle on
        # that same day.  An opening equity is its own day's CLOSING balance,
        # so those settles are inside it.
        open_books_before_the_first_assertion(db.session, destination)
        transfer = create_transfer(
            seed_user, seed_user["account"], destination,
            seed_user["bootstrap_period"], amount=Decimal(amount),
        )
        if settled:
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    transfer.from_account_id, transfer.to_account_id,
                    an_entered_day(
                        seed_user["bootstrap_period"].start_date + timedelta(days=5),
                    ),
                ),
            )
        db.session.flush()
        assert isinstance(destination, Account)
        return transfer

    @staticmethod
    def _record(db, transfer, account_id):
        """Return *transfer*'s side record on *account_id*, or ``None``.

        Read by this class's own SQL over the side links (ruling **R-BAL167**
        class 4: never through the producer under test).
        """
        from app.models.transaction_entry import TransactionEntry  # local

        return (
            db.session.query(TransactionEntry)
            .filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
                TransactionEntry.account_id == account_id,
            )
            .one_or_none()
        )

    def test_a_projected_transfer_settles_through_the_transfer_service(
        self, app, db, seed_user,
    ):
        """One call settles the transfer and dates both its sides' records.

        Each side is its RECORD since plan step ``balance:X-bi-6-4d-2`` (ruling
        **R-BAL167** class 1): the transfer says Paid and each side's record
        is dated, where each shadow said Paid until then.
        """
        transfer = self._a_transfer(db, seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transfers=[transfer])

        assert accepted.settled_count == 1
        assert self._record(
            db, transfer, transfer.from_account_id,
        ).settled_on == bank_day
        assert transfer.status.is_settled
        assert self._record(
            db, transfer, transfer.to_account_id,
        ).settled_on is not None

    def test_a_settled_transfer_sides_day_is_CORRECTED(self, app, db, seed_user):
        """The majority case on real data, and the one a settle verb cannot do.

        ``transfer_service.settle_transfer`` is an idempotent no-op on a
        transfer already in the settled band -- it writes nothing and reports
        False -- so a door that only knew that verb would silently fail to
        correct 9 of the developer's own 13 shadow matches.  The dispatch takes
        ``update_transfer`` here instead, and this is the control over it.
        """
        transfer = self._a_transfer(db, seed_user, settled=True)
        record = self._record(db, transfer, transfer.from_account_id)
        bank_day = seed_user["bootstrap_period"].start_date
        assert record.settled_on != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transfers=[transfer])

        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert record.settled_on == bank_day

    def test_a_match_states_ITS_side_and_the_far_side_borrows(
        self, app, db, seed_user,
    ):
        """The bank showed Checking; Savings' side borrows that day (ruling R-BAL89).

        Plan step ``balance:X-bi-6-4c-3`` (design D5): the matcher states
        ``observed`` for the side on the statement's account only.  It stated it
        for BOTH until then, which is how every far side of a matched transfer
        came to claim a bank line it never had.
        """
        transfer = self._a_transfer(db, seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], transfers=[transfer])

        assert recorded_settle_day(
            self._record(db, transfer, transfer.from_account_id),
        ) == an_observed_day(bank_day)
        assert recorded_settle_day(
            self._record(db, transfer, transfer.to_account_id),
        ) == SettleDay(day=bank_day, basis=SettledDayBasisEnum.BORROWED)

    def test_a_correction_moves_a_side_that_only_borrowed_its_day(
        self, app, db, seed_user,
    ):
        """Paid pressed, then the statement: BOTH sides land on the bank's day.

        Ruling **R-BAL163**'s worked example: a Paid press leaves both sides
        borrowing the press day, so a Checking statement showing another day
        moves Checking (its own now) and Savings (still borrowing) together.
        """
        from app.services import transfer_service  # local: this class only

        transfer = self._a_transfer(db, seed_user)
        transfer_service.settle_transfer(transfer.id, seed_user["user"].id)
        db.session.flush()
        record = self._record(db, transfer, transfer.from_account_id)
        bank_day = seed_user["bootstrap_period"].start_date
        assert record.settled_on != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transfers=[transfer])

        assert accepted.corrected_count == 1
        assert recorded_settle_day(record) == an_observed_day(bank_day)
        assert recorded_settle_day(
            self._record(db, transfer, transfer.to_account_id),
        ) == SettleDay(day=bank_day, basis=SettledDayBasisEnum.BORROWED)

    def test_a_correction_leaves_a_far_side_with_its_own_day(
        self, app, db, seed_user,
    ):
        """A far side the owner TYPED keeps its day when Checking's statement moves Checking.

        Declared change 2 of plan step ``balance:X-bi-6-4c-3``: a statement
        re-dates only its own side, and a side with evidence of its own does not
        follow (ruling **R-BAL142**).  The ``settled`` fixture states one day
        typed on both sides, the one-box popover's meaning (ruling R-BAL165).
        """
        transfer = self._a_transfer(db, seed_user, settled=True)
        far_side = self._record(db, transfer, transfer.to_account_id)
        typed_day = recorded_settle_day(far_side)
        assert typed_day.basis is SettledDayBasisEnum.ENTERED
        bank_day = seed_user["bootstrap_period"].start_date
        assert typed_day.day != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], transfers=[transfer])

        assert recorded_settle_day(
            self._record(db, transfer, transfer.from_account_id),
        ) == an_observed_day(bank_day)
        assert recorded_settle_day(far_side) == typed_day

    def test_a_soft_deleted_TRANSFER_is_not_matchable(
        self, app, db, seed_user,
    ):
        """Not money this account owes, and pricing one asks about a gone row.

        The delete flag is set on the transfer directly rather than through
        ``delete_transfer(soft=True)``, so the flag is the only thing that
        differs from a live transfer.  It planted a live shadow under a
        hidden transfer, a state no door wrote, until plan step
        ``balance:X-bi-6-4d-2`` made a transfer one row (ruling **R-BAL258**).
        """
        from app.models.transfer import Transfer  # local: this test only

        transfer = self._a_transfer(db, seed_user)
        parent = db.session.get(Transfer, transfer.id)
        parent.is_deleted = True
        db.session.flush()
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transfers=[parent])


class TestAnAcceptedMatchStopsAgreeingWhenItStopsHolding:
    """``AcceptedGroup.agrees`` asks three questions, and a first draft asked one.

    **A CASCADE can falsify a match without touching a single day**, which is
    what an adversarial design review measured on 2026-08-17: deleting a
    purchase or destroying a pay period removes that member silently, and a
    day-only test then reports a group explaining less than it claims -- or,
    when every row goes, nothing at all -- as still agreeing with the bank.
    A SOFT delete was worse, because it does not cascade at all: the row kept
    its ``settled_on`` and contributed zero to every balance, so only the SUM
    could see it had gone.  No item can be hidden holding its payment since
    plan step ``balance:X-bi-6-4d-2`` (ruling **credit_card:R-CC75**, "Same
    as a one-off", for transfers too: the delete takes the payments off and
    withdraws the match it empties), so that case's test was retired
    (developer, rule 5, 2026-10-09); the SUM still answers for a purchase
    moved to a card (``TestAMatchStopsHoldingWhenAPURCHASELEAVESTHEACCOUNT``).
    """

    @staticmethod
    def _accepted_pair(db, seed_user):
        """Accept a two-row group and return its rows and the review set."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        _submit(seed_user, lines=[line], transactions=[salary, allowance])
        return salary, allowance

    def _groups(self, seed_user):
        """Return the accepted groups the screen would render."""
        return accepted_acts(seed_user)

    def test_it_agrees_while_it_holds(self, app, db, seed_user):
        """The control, without which every arm below could pass vacuously."""
        self._accepted_pair(db, seed_user)

        groups = self._groups(seed_user)

        assert len(groups) == 1
        assert groups[0].agrees is True

    def test_an_EXPENSE_row_agrees_while_it_holds(self, app, db, seed_user):
        """The same control on the kind plan step X-bi-3a covers.

        A settled bill's covering movement carries the money since that leaf,
        and the register read the row's own leg alone (``settled_cash_leg``,
        zero for a covered bill), so every accepted bill match reported
        itself as no longer holding, while the income control above stayed
        green (adversarial review, 2026-09-16).  The register prices the row
        at its covering movement now (``covered_cash_leg``, ruling
        **R-BAL81**), as the offer and the post-apply check do.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-148.32", posted_on=bank_day,
        )
        bill = a_transaction(seed_user, name="Electric", amount="148.32")
        _submit(seed_user, lines=[line], transactions=[bill])

        groups = self._groups(seed_user)

        assert len(groups) == 1
        assert groups[0].agrees is True

    def test_a_hand_moved_day_stops_it_agreeing(self, app, db, seed_user):
        """The owner contradicted the bank, and the screen says so.

        Moved through the row's own door, which mirrors the day onto the
        covering movement the act names (plan step ``credit_card:CC-5-4a-1``,
        ruling **R-CC43**); this wrote the row's day pair directly through
        ``CC-5-3``, when the act named the row.
        Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        under R-CC43 -- the member names the payment; a day moves through the seam that mirrors it;
        a settled row is ticked as its payment."
        """
        salary, _ = self._accepted_pair(db, seed_user)
        transaction_service.apply_requested_status(
            salary, salary.status_id,
            settle_day=an_entered_day(salary.settled_on + timedelta(days=1)),
        )
        db.session.flush()

        assert self._groups(seed_user)[0].agrees is False


class TestAMatchStopsHoldingWhenAPURCHASELEAVESTHEACCOUNT:
    """A purchase flipped to CARD moves no cash through this account at all."""

    def test_flipping_a_matched_purchase_to_card_breaks_agreement(
        self, app, db, seed_user,
    ):
        """It keeps its day, so only the SUM can see it has gone.

        ``update_entry`` supports the flip and releases the clearing link for
        it; the purchase keeps ``settled_on`` throughout.  A valuation reading
        the magnitude alone therefore reported the match as still explaining
        the bank's figure while the money had left through the card instead.
        Found by adversarial financial review 2026-08-17.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], entries=[purchase])
        before = accepted_acts(seed_user)
        assert before[0].agrees is True

        purchase.is_credit = True
        db.session.flush()

        after = accepted_acts(seed_user)
        assert after[0].agrees is False
        assert after[0].rows[0].settled_on == bank_day, (
            "the day is untouched -- which is why a day-only test was blind"
        )
