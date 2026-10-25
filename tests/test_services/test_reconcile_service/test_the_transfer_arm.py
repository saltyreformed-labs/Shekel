"""The transfer arm of a reconcile, and what a tick books."""

from dataclasses import fields
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import account_service, reconcile_service, status_seam, transfer_service
from app.utils.log_events import EVT_TRANSFERS_RECONCILED
from tests._test_helpers import (
    linked_ledger_account,
    linked_net_by_date,
    open_books_before_the_first_assertion,
    resolved_amount,
    transfer_side_figure,
    transfer_side_record,
)
from tests._test_helpers import create_transfer
from app.services.row_valuation import settled_contribution, settled_figure

from ._helpers import (
    _make_entry,
    _OBSERVED_ON,
    _statement,
    _reconciled,
    _transaction_arm_bill,
    _transaction_arm_offered,
    _transaction_arm_settle,
)


class TestTheTransferArm:
    """Plan step **X-f2-c3**: the panel offers a TRANSFER's leg too.

    Money moving between two of the owner's own accounts still leaves one of
    them, so a checking statement shows it exactly as it shows a bill.
    Replayed over production's 57 Checking assertions (53 distinct days, as
    ``reconcile_service._transfers`` counts them), 8 days would have carried a
    transfer offer, `$5,442.89` -- six `$500.00` savings sweeps, one
    `$1,910.95` Mortgage payment and one `$531.94` Van Loan payment.

    **What makes it a separate arm is the SETTLE.**  Its verb is
    ``transfer_service.settle_transfer``, and one tick settles the WHOLE
    transfer: its one status, and a payment record on each side.  These grade
    that a tick settles the transfer and dates both sides' records by the
    statement -- which is the whole reason the panel prints a note saying a
    second account moves.  A transfer was three rows (a parent and two
    shadows) until plan step ``balance:X-bi-6-4d-2``, and a tick moved all
    three until plan step ``balance:X-bi-6-4d-2``.
    """

    @staticmethod
    def _savings(seed_user, name="Savings"):
        """Create a second cash account for the transfer's other leg.

        Its BOOKS open before anything this class dates (plan step X-f3c-2b,
        ruling **R-HG**): ``create_account`` opens them on the assertion's own
        day, which is today, and every transfer here settles on a statement day
        earlier than that.  It moves no figure -- the assertion still clears
        whatever settled on its own day.
        """
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                name=name,
                account_type_id=seed_user["account"].account_type_id,
                anchor_balance=Decimal("100.00"),
            ),
        )
        db.session.flush()
        open_books_before_the_first_assertion(db.session, account)
        return account

    @classmethod
    def _transfer_out(cls, seed_user, seed_periods, amount="75.00",
                      due_date=None):
        """Return a committed transfer from the seed account to a new savings account.

        It returned the transfer's expense shadow on the seed account beside
        it until plan step ``balance:X-bi-6-4d-2``: a transfer is one row
        since then (ruling R-BAL167 class 4).
        """
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"],
            cls._savings(seed_user), seed_periods[0],
            amount=Decimal(amount), due_date=due_date,
        )
        db.session.commit()
        return transfer

    @staticmethod
    def _side_records(transfer):
        """Return ``{account id: payment record}`` for each side that holds one.

        What a tick records on each side since plan step
        ``balance:X-bi-6-4d-2`` -- the day and the statement link -- hung off
        the TRANSFER by a side link, read by independent SQL (ruling R-BAL167
        class 4, plan step balance:X-bi-6-4d-2); the shadows carried it until
        then.  The figure and its source each record carried beside them are
        stored once on the transfer since that step's 4d-4 half.
        """
        records = {}
        for account_id in (transfer.from_account_id, transfer.to_account_id):
            record = transfer_side_record(db.session, transfer.id, account_id)
            if record is not None:
                records[account_id] = record
        return records

    @staticmethod
    def _assert_unsettled(transfer):
        """Assert a refused tick settled nothing: still Projected, and no side records a payment.

        A settle writes both halves -- the transfer's one status and each
        side's payment record -- so a refusal is graded on both, and on both
        sides, re-read after the caller's ``expire_all``.  It read the seed
        account's expense shadow's ``settled_on`` until plan step
        ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 1).
        """
        for account_id in (transfer.from_account_id, transfer.to_account_id):
            assert transfer_side_record(db.session, transfer.id, account_id) is None, (
                f"the refused tick recorded a payment on account {account_id}"
            )
        assert db.session.get(type(transfer), transfer.id).status_id == (
            ref_cache.status_id(StatusEnum.PROJECTED)
        )

    _offered = staticmethod(_transaction_arm_offered)
    _settle = staticmethod(_transaction_arm_settle)

    def test_a_tick_records_the_STATEMENT_on_this_accounts_leg_alone(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The clearing fact is NOT shared with the other side, though the tick dates both.

        Ruling **R-FL**, and the asymmetry is the point.  A transfer leaves one
        bank and arrives at another: the asserted account's statement showed its
        own leg, and the other account's statement is a document nobody has read
        in this act.  The tick dates BOTH sides' records by the statement
        (graded by the test below), each side's day being a fact about its own
        account since ruling R-BAL142; ``reconciled_by_id`` must not be
        shared, or ticking a savings sweep off a checking statement would
        record that the savings statement showed the money too.

        This is also what makes the posted walk's per-account read of a leg's
        statement link meaningful -- see
        ``test_account_posting_service.TestWalkAccountLedger``'s own case, which
        measures what reading the wrong leg's link costs.

        Each leg is its side's payment RECORD since plan step
        ``balance:X-bi-6-4d-2``, which carries the statement link the shadows
        carried until then (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)
            statement = _statement(seed_user["account"].id)

            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 1
            db.session.commit()
            db.session.expire_all()

            records = self._side_records(transfer)
            assert len(records) == 2
            recorded = {
                account_id: record.reconciled_by_id
                for account_id, record in records.items()
            }
            assert recorded[seed_user["account"].id] == statement.anchor_id
            other = next(
                account_id for account_id in recorded
                if account_id != seed_user["account"].id
            )
            assert recorded[other] is None, (
                "The other account's leg named a statement nobody read."
            )

    def test_a_tick_settles_the_parent_and_BOTH_legs_on_the_statement_day(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The arm's whole point: the transfer settles, both sides dated by the statement.

        The side on the OTHER account records the payment too -- which is what
        the panel's section note tells the user -- and both records carry the
        asserted day rather than the seam's default of the user's today.  A
        settle that recorded ONE side only does not reach these reads: the
        database's deferred trigger refuses it at commit.  One that recorded
        NEITHER commits (the trigger reads it as a `$0.00` close), and the
        two-record count below is what catches it.

        The transfer's status is the one status and each side's day is its
        payment RECORD's since plan step ``balance:X-bi-6-4d-2``; each shadow
        said Paid and carried the day until then (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)

            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 1
            db.session.commit()

            db.session.expire_all()
            records = self._side_records(transfer)
            assert len(records) == 2
            settled = ref_cache.status_id(StatusEnum.DONE)
            assert {
                record.settled_on for record in records.values()
            } == {_OBSERVED_ON}
            assert db.session.get(
                type(transfer), transfer.id,
            ).status_id == settled

    def test_it_is_offered_under_its_OWN_section_with_the_both_sides_note(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """TRANSFER sorts last and carries the sentence about the other account.

        The section is the ACT and the tally is the LEG (below), so this grades
        the half that decides what the screen says.  A transfer landing in the
        Bills section would read as an observation about one account, which is
        exactly what it is not.
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)

            groups = reconcile_service.outstanding_set(_reconciled(seed_user)).groups
            block = next(
                group for group in groups
                if group.key == (transfer.id, seed_user["account"].id)
            )
            assert block is groups[-1]
            assert block.section.label == "Transfers"
            assert "both sides" in block.section.note
            assert block.settle_closes_an_envelope is False
            # The figure the EXPENSE side's tick offers, the transfer's own
            # $75.00.  Moved here at plan step balance:X-bi-6-4d-2 from the
            # deleted test_a_transfer_shadow_belongs_to_the_OTHER_arm (ruling
            # R-BAL313): measured 2026-10-17 without this read, an extra $1.00
            # on the expense side's offer failed no PASSING test.
            assert block.settle.amount == Decimal("75.00")

    def test_the_expense_leg_counts_as_a_PAYMENT_and_the_income_leg_a_DEPOSIT(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """One act, one section -- but the statement shows a direction.

        Ruling **R-FD** counts deposits apart from payments because they do not
        sum to anything a reader wants, and a transfer is no exception: money
        leaving Checking is a payment on Checking's statement, and the same
        transfer's other leg is a deposit on the savings account's.  Both are
        graded, from the two panels they appear on.
        """
        with app.app_context():
            savings = self._savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            outgoing = reconcile_service.outstanding_set(_reconciled(seed_user))
            assert outgoing.payment_total >= Decimal("75.00")
            assert outgoing.deposit_count == 0

            incoming = reconcile_service.outstanding_set(
                _reconciled(seed_user, account_id=savings.id),
            )
            assert incoming.deposit_count == 1
            assert incoming.deposit_total == Decimal("75.00")
            assert incoming.payment_count == 0
            assert transfer.id  # the same transfer, offered on both panels

    def test_a_row_that_is_not_yet_overdue_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The shared bound applies to this arm too, from BOTH doors.

        The transfer's due date is the day after the statement, so nothing the
        bank showed can have included it.  Graded from the write side as well:
        a clause held on the read side only would let a forged id settle a
        transfer the panel never offered -- and settling one moves a SECOND
        account.
        """
        with app.app_context():
            transfer = self._transfer_out(
                seed_user, seed_periods, due_date=date(2026, 1, 11),
            )

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 0

            db.session.expire_all()
            self._assert_unsettled(transfer)

    def test_another_accounts_transfer_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A balance assertion declares ONE account's balance.

        The transfer runs between two accounts that are neither of them the one
        being reconciled, so neither of its sides is on this statement.
        Settling across accounts would record money against a statement that
        never showed it -- and here it would move two accounts at once.

        A scope that admitted this transfer fails here BEFORE the reads below,
        at ``leg_of``'s refusal to build a leg for an account on neither side.
        The record and status reads are the backstop for a scope defect and a
        leg defect together (measured 2026-10-17).
        """
        with app.app_context():
            source = self._savings(seed_user, name="Second Checking")
            target = self._savings(seed_user, name="Third Checking")
            transfer = create_transfer(
                seed_user, db.session, source, target, seed_periods[0],
                amount=Decimal("75.00"),
            )
            db.session.commit()

            # Under ANY key, not only this account's: a leg offered keyed by
            # the transfer's own account would still be a transfer on neither
            # side of this statement.
            assert transfer.id not in {
                key[0] for key in self._offered(seed_user) if isinstance(key, tuple)
            }, (
                f"transfer {transfer.id} was offered on account "
                f"{seed_user['account'].id}, which is on neither of its sides"
            )
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 0

            db.session.expire_all()
            self._assert_unsettled(transfer)

    def test_a_soft_deleted_transfer_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A deleted transfer is not money this account owes.

        Graded because the WRITER acts on the transfer the tick names (its own
        ``transfer_ids`` field, ruling R-BAL145): ``settle_transfer`` treats a
        soft-deleted transfer as absent and raises ``NotFoundError``, which
        this route has no handler for -- so a scope that admitted one would be
        a 500 on a money door rather than a silent skip.
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 0

    def test_a_settled_transfer_is_neither_offered_nor_re_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Already recorded is not outstanding, and a replay must not re-date.

        ``done -> done`` is a legal identity transition, so nothing downstream
        would refuse a second submission -- the SCOPE is what stops it.
        Without it a re-settle would record the later statement against this
        side (``transfer_service.record_leg_clearing``).  It could not re-date
        the records: the transfer service drops every stated day for a
        transfer already settled (``settle_only``), so the day read below
        grades that rule rather than the scope.  Each side's day is its record's since plan
        step ``balance:X-bi-6-4d-2``; the shadows carried it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 1
            db.session.commit()

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(
                seed_user, [], observed_on=date(2026, 1, 14), transfer_ids=[transfer.id],
            ) == 0

            db.session.expire_all()
            records = self._side_records(transfer)
            assert len(records) == 2
            assert {
                record.settled_on for record in records.values()
            } == {_OBSERVED_ON}

    def test_a_correction_books_on_both_legs_and_counts_as_one(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """A transfer's tick is correctable, and the figure lands on the pair.

        Ruling **R-FF**: correctable exactly when the settle takes its MANUAL
        branch, and a transfer has no other -- its leg is never purchase-tracked
        (``TransferLeg.tracks_purchases``; production: 342 shadows, 0 entries,
        measured while a transfer kept its shadow rows).  The corrected
        figure must reach BOTH legs or the two accounts disagree about how much
        money moved between them.  The figure is the TRANSFER's own since plan
        step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), so the read below asks that one figure once per side;
        each side's payment RECORD held it from that step until then, and the
        shadows before it (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)
            assert self._offered(seed_user)[(transfer.id, seed_user["account"].id)].is_correctable is True

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [], transfer_ids=[transfer.id],
                    transfer_corrections={transfer.id: Decimal("74.11")},
                ) == 1
            db.session.commit()

            db.session.expire_all()
            assert {
                transfer_side_figure(db.session, transfer.id, account_id)
                for account_id in (
                    transfer.from_account_id, transfer.to_account_id,
                )
            } == {Decimal("74.11")}
            # Each LEG on its own account's ledger, signed as the ledger signs
            # it (out of Checking negative, into Savings positive).  The read
            # above asks the transfer's one figure twice, so a leg that booked
            # the $75.00 plan instead still reads $74.11 there.  Hand
            # arithmetic on the statement day, where the tick dates both
            # sides: the correction books $74.11 in place of the $75.00 plan
            # (75.00 - 0.89 = 74.11), so Checking nets -74.11 and Savings
            # +74.11.  No other posting on either ledger is dated that day:
            # Checking's opening and origination are dated in 2024 (the
            # seed's bootstrap) and the seed's envelope row is Projected;
            # Savings' opening is dated on its books' day, 2026-01-01 (the day
            # before the first pay period), and its assertion's correction on
            # the suite's frozen today, 2026-03-20.  Not
            # ``account_posting_total``: an assertion absorbs a movement dated
            # before it, so Savings, asserted after the statement day, totals
            # its $100.00 whatever its leg booked.
            scenario_id = seed_user["scenario"].id
            for side, account_id, posted in (
                ("from-side", transfer.from_account_id, Decimal("-74.11")),
                ("to-side", transfer.to_account_id, Decimal("74.11")),
            ):
                assert linked_net_by_date(
                    db.session,
                    linked_ledger_account(db.session, account_id).id,
                    scenario_id,
                ).get(_OBSERVED_ON) == posted, (
                    f"the correction did not reach the {side}'s leg on "
                    f"account {account_id}"
                )

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == EVT_TRANSFERS_RECONCILED
            ]
            assert len(events) == 1
            assert events[0].corrected_count == 1

    def test_an_ECHOED_prefill_leaves_both_legs_NULL_and_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """An untouched box is not a correction, on this arm either.

        The panel prefills every correctable row, so a five-row submit posts
        five figures.  Recording an echo as a correction would destroy the only
        signal that says a human read one off a statement: the transfer's
        figure SOURCE, as the read below says (each side record's until plan
        step balance:X-bi-6-4d-2's 4d-4 half; it was the shadows'
        ``actual_amount``, NULL on all 17 settled transfer shadows in
        production when measured on 2026-08-12).
        Each leg's record is its
        side's, hung off the transfer, since plan step
        ``balance:X-bi-6-4d-2``; the shadows held it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            transfer = self._transfer_out(seed_user, seed_periods)

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [], transfer_ids=[transfer.id],
                    transfer_corrections={transfer.id: Decimal("75.00")},
                ) == 1
            db.session.commit()

            db.session.expire_all()
            # Both legs RECORD what the settle booked, on the ``resolved``
            # source -- an echoed prefill is not a correction, and since plan
            # step X-au-c3 "not a correction" is the figure's SOURCE rather
            # than a NULL figure, stored on each record's TRANSFER since plan
            # step balance:X-bi-6-4d-2's 4d-4 half.
            records = self._side_records(transfer)
            assert len(records) == 2
            assert {
                (
                    record.expense_transfer or record.income_transfer
                ).settled_figure_source_id
                for record in records.values()
            } == {
                ref_cache.movement_figure_source_id(
                    MovementFigureSourceEnum.RESOLVED,
                ),
            }
            assert {
                transfer_side_figure(db.session, transfer.id, account_id)
                for account_id in records
            } == {Decimal("75.00")}

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == EVT_TRANSFERS_RECONCILED
            ]
            assert events[0].corrected_count == 0


class TestWhatATickBooks:
    """Ruling **R-FA** / **R-FB** / **R-FF**: the amount, and who may change it.

    The panel must show the figure a tick will BOOK and offer a box exactly
    where the verb would read one.  Both come from ``transaction_service``
    rather than from a column here, and these grade that they agree.
    """

    _bill = staticmethod(_transaction_arm_bill)
    _offered = staticmethod(_transaction_arm_offered)
    _settle = staticmethod(_transaction_arm_settle)

    def test_an_envelope_with_entries_offers_sum_entries_and_no_box(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$40` + `$60` against a `$500` estimate offers `$100.00`, read-only.

        Ruling R-EW refuses an editable close: an envelope's ``actual_amount``
        is DERIVED from its entries, so a box would be a second writer of it.
        The offered figure is what the verb books, not the estimate.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(txn, seed_user["user"], amount="60.00")
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("100.00")
            assert offer.is_correctable is False

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("100.00")

    def test_an_envelope_with_NO_entries_offers_its_estimate_WITH_a_box(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-FF**: correctable exactly when the verb goes manual.

        Production's `Kayla's Spending Money` is envelope-tracked, budgeted
        `$100.00` and carries ZERO entries, so the verb already treats it as
        manual -- there is no derived value to protect and no reason to make
        the user leave the panel to correct it.  Offered at its estimate, with
        the box.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("500.00")
            assert offer.is_correctable is True

    def test_a_bill_ticked_untouched_leaves_actual_amount_NULL(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The prefill echoed back is not a correction.

        The panel renders the amount box PREFILLED, so an untouched tick
        submits the same figure the row would have booked anyway.  Writing it
        into ``actual_amount`` would populate a column that is NULL on every
        uncorrected row and destroy the only signal that says a human typed
        one -- the signal ruling R-FB's own production measurement is made of.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            db.session.commit()

            assert self._settle(
                seed_user, [bill.id], {bill.id: Decimal("180.00")},
            ) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, bill.id)
            # The tick RECORDS what it booked and says the source is
            # ``resolved`` -- nobody typed a different figure (plan step
            # X-au-c3).  It asserted ``actual_amount is None`` until that
            # step, because a NULL there was the only signal that no human
            # had corrected the row.
            assert status_seam.recorded_settlement(reloaded).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(reloaded) == Decimal("180.00")
            assert settled_contribution(reloaded) == Decimal("180.00")

    def test_a_bill_ticked_with_a_different_figure_books_it(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-FB**: a bill's tick MAY correct its amount.

        Production's shape, to the cent: Electricity estimated `$300.00`,
        statement says `$245.32`.  Settling at the estimate would leave
        `$54.68` for plan step X-f3's residual to book as Uncategorized Income
        -- at the one moment the user is holding the paper that says the true
        figure.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="300.00")
            db.session.commit()

            assert self._settle(
                seed_user, [bill.id], {bill.id: Decimal("245.32")},
            ) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, bill.id)
            assert settled_figure(reloaded) == Decimal("245.32")
            # The correction is recorded BESIDE the plan, never into it: the
            # bill is the engine's derived row, so its plan is what the amount
            # model answers, and that is still the definition's $300.00.
            assert resolved_amount(reloaded) == Decimal("300.00")

    def test_a_correction_on_a_DERIVED_row_is_ignored_not_applied(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A forged box for an envelope with entries changes nothing.

        The panel renders no input there, so the only way to submit one is by
        hand.  **What refuses it is the VERB, and a review proved that is the
        only thing that ever did**: the writer carried its own "read the box
        only where the panel offered one" guard, and deleting that guard left
        every test green, because ``settle_transaction`` routes an
        entries-derived row to a branch that ignores ``actual_amount`` outright.
        The guard is gone; this grades the rule that was doing the work.
        `$999.99` against `$40.00` of entries books `$40.00`.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            assert self._settle(
                seed_user, [txn.id], {txn.id: Decimal("999.99")},
            ) == 1

            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("40.00")

    def test_income_is_offered_as_a_DEPOSIT_and_settles_to_Received(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-FD**: a deposit you are waiting on is what a statement settles.

        Production's largest offered deposit was an FSA reimbursement of
        `$1,958.87`; it settles to Received, not Paid, and the set counts it
        apart from the payments because a deposit and a bill do not sum to
        anything a reader wants.
        """
        with app.app_context():
            deposit = self._bill(
                seed_user, seed_periods[0], name="FSA Reimbursement",
                amount="1958.87", income=True,
            )
            db.session.commit()

            result = reconcile_service.outstanding_set(_reconciled(seed_user))
            assert result.deposit_count == 1
            assert result.deposit_total == Decimal("1958.87")
            assert result.payment_count == 0
            assert self._offered(seed_user)[deposit.id].is_income is True

            assert self._settle(seed_user, [deposit.id]) == 1
            db.session.expire_all()
            assert db.session.get(Transaction, deposit.id).status_id == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

    def test_the_two_totals_are_never_summed_into_one(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The double-count ruling R-FA's own text warns about.

        An envelope with `$40` + `$60` of purchases and its own `$100` close
        offers BOTH -- and a single "total" would report `$200` against `$100`
        of money.  The set publishes the purchase total and the payment total
        separately and nothing adds them.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(txn, seed_user["user"], amount="60.00")
            db.session.commit()

            result = reconcile_service.outstanding_set(_reconciled(seed_user))
            assert result.purchase_count == 2
            assert result.purchase_total == Decimal("100.00")
            assert result.payment_count == 1
            assert result.payment_total == Decimal("100.00")
            # Asserted by NAMING the fields, not by ``hasattr``: a probe on a
            # dataclass passes for any typo (``status_seam``'s own X-aa
            # lesson), so it would report "there is no combined total" about a
            # field spelled anything at all.
            assert {field.name for field in fields(result)} == {
                "groups",
                "purchase_count", "purchase_total",
                "payment_count", "payment_total",
                "deposit_count", "deposit_total",
            }
