"""The reconcile panel's figures and order: the cash figure beside the booked one, the count of
human corrections, the sections and their order, and one amount basis.
"""

from decimal import Decimal

from app.models.transaction import Transaction
from app.services import account_service, cash_ledger, reconcile_service, status_seam
from app.utils.log_events import EVT_TRANSACTIONS_RECONCILED
from tests._test_helpers import an_entered_day, count_amount_bases, create_account_of_type
from tests._test_helpers import create_transfer
from app.services.row_valuation import settled_figure
from app.services.settle_day import record_settle_day

from ._helpers import (
    _make_entry,
    _OBSERVED_ON,
    _reconciled,
    _transaction_arm_bill,
    _transaction_arm_offered,
    _transaction_arm_settle,
)


class TestTheCashFigureBesideTheBookedOne:
    """Finding **N-226**: what a tick BOOKS is not always what a statement shows.

    An envelope settles at ``sum(entries)`` over EVERY entry it holds, and a
    card purchase is one of those -- but a card purchase never touches
    checking, which is exactly why the purchase arm refuses to OFFER one.  So a
    `$40` debit plus a `$60` card purchase is offered at `$100.00` on a screen
    captioned "tick everything your statement shows", against a statement
    showing `$40`.

    **The LEDGER was right either way** -- a card purchase moves nothing
    through checking (``cash_ledger.movement_cash_leg``), as the row's own leg
    of the time subtracted the credit sum -- so the fix prints both figures
    rather than changing what a tick books: ``actual_amount`` legitimately IS
    total spend, and moving it would make the panel disagree with the grid
    and the analytics.

    Production carries 18 card entries in history and ZERO on a Projected
    envelope today, so this is latent rather than live.
    """

    _bill = staticmethod(_transaction_arm_bill)
    _offered = staticmethod(_transaction_arm_offered)

    def test_an_envelope_holding_a_CARD_purchase_publishes_both_figures(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$40` debit + `$60` card: booked `$100.00`, statement `$40.00`."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Amazon", is_credit=True,
            )
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("100.00")
            assert offer.cash_amount == Decimal("40.00")

    def test_a_swipe_on_the_CARD_is_off_the_checking_statement_and_counted_ONCE(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Plan step ``credit_card:CC-5-2``: the third term, and the partition.

        `$40` debit on checking + `$60` swipe ON the card (no flag) + `$25`
        swipe on the card ALREADY POSTED: booked `$125.00`; checking's
        statement shows `$40.00`.  The posted card swipe sits in exactly one
        term -- the account's, not the posted-purchase one -- or the cash
        figure would read `$15.00`, a purchase taken out twice.  Graded on the
        panel's own figure and on the three terms directly, so a term that
        stopped partitioning would fail here and not only downstream.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Target", account_id=card.id,
            )
            posted_on_the_card = _make_entry(
                txn, seed_user["user"], amount="25.00",
                description="Shell", account_id=card.id,
            )
            record_settle_day(posted_on_the_card, an_entered_day(_OBSERVED_ON))
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("125.00")
            assert offer.cash_amount == Decimal("40.00")

            assert cash_ledger.credit_entry_sum(txn) == Decimal("0")
            assert cash_ledger.elsewhere_purchase_sum(txn) == Decimal("85.00")
            assert cash_ledger.posted_purchase_sum(txn) == Decimal("0")
            assert cash_ledger.off_statement_sum(txn) == Decimal("85.00")

    def test_a_flagged_line_on_another_account_is_counted_ONCE_around_the_doors(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The partition holds for a row NO door writes: flag AND another account.

        Both purchase doors refuse the combination
        (``entry_service._refusals._reject_flag_beside_another_account``), so
        this plants it on the model directly -- the state the doors exist to
        keep out, graded here so the sum's partition rests on the terms
        themselves and not on the doors: `$60` flagged on the card is the
        FLAG's term alone, and `off_statement_sum` reads `$60.00`, never
        `$120.00`.  Deleting ``not entry.is_credit`` from
        ``elsewhere_purchase_sum`` doubles it.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Target", is_credit=True, account_id=card.id,
            )
            db.session.commit()

            assert cash_ledger.credit_entry_sum(txn) == Decimal("60.00")
            assert cash_ledger.elsewhere_purchase_sum(txn) == Decimal("0")
            assert cash_ledger.posted_purchase_sum(txn) == Decimal("0")
            assert cash_ledger.off_statement_sum(txn) == Decimal("60.00")
            assert self._offered(seed_user)[txn.id].cash_amount == Decimal("40.00")

    def test_an_envelope_of_DEBITS_publishes_no_second_figure(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The negative control, and it is what stops the caption being noise.

        Nothing on the card means the booked figure IS what the statement
        shows, so there is no second number to print.  Without this the case
        above passes for a panel that captions every envelope.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            assert self._offered(seed_user)[txn.id].cash_amount is None

    def test_a_bill_and_a_transfer_publish_no_second_figure(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A row that carries no entries has no card half to disagree with.

        Graded across BOTH source-row arms because the field is on the shared
        offer type: a bill can hold no entries, and a transfer's leg
        structurally holds no purchase (production: 342 shadows, 0 entries,
        measured while a transfer still kept its shadow rows; a transfer is
        one row since plan step ``balance:X-bi-6-4d-2``).
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Savings",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            offered = self._offered(seed_user)
            assert offered[bill.id].cash_amount is None
            assert offered[(transfer.id, seed_user["account"].id)].cash_amount is None

    def test_the_cash_figure_matches_what_the_LEDGER_will_post(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The panel's second figure IS the posted one, not a lookalike.

        Both come from ``cash_ledger.credit_entry_sum``, and this grades that
        by settling the row and comparing against what its family's
        movements move through this account -- ``movement_cash_leg``, the one
        valuation the ledger writer and the cash walk post through (ruling
        **R-BAL80**): the debit purchase its own `$40.00`, the card purchase
        nothing, and the row itself nothing (a ``purchases`` close has no
        covering movement, ruling **R-BAL81**).  Two numbers that agree by
        construction rather than by coincidence is the whole reason the term
        was published instead of re-summed.  (Through plan step
        ``balance:X-bi-4a``'s first cut this compared against the row's own
        leg, ``settled_cash_leg``, which R-BAL81 deleted.)
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Amazon", is_credit=True,
            )
            db.session.commit()

            offered_cash = self._offered(seed_user)[txn.id].cash_amount
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    statement=_reconciled(seed_user),
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    transfer_ids=set(), transfer_corrections={},
                ),
            ) == 1
            db.session.commit()

            db.session.expire_all()
            settled = db.session.get(Transaction, txn.id)
            assert settled.covering_movements == []
            assert status_seam.covered_cash_leg(settled, settled.account_id) == Decimal("0.00")
            assert sum(
                (
                    cash_ledger.movement_cash_leg(settled, entry)
                    for entry in settled.entries
                ),
                Decimal("0.00"),
            ) == -offered_cash


class TestTheCorrectionCountIsWhatAHumanTyped:
    """Finding **N-231**: the count says how many figures a HUMAN supplied.

    ``transactions_reconciled`` describes its rows as "some carrying a
    corrected amount", and ruling **R-FB**'s production measurement ("11 of 93
    settled bills carry a hand-typed correction") is made of this same signal.
    It was read off the COLUMN -- rows whose ``actual_amount`` changed -- and
    an envelope's close ALWAYS writes that column, so every envelope tick
    incremented it.  It now asks the verb's own published predicate
    (``transaction_service.is_correction``) before the settle.

    Four shapes, and the first two are the ones the column reading got wrong.
    """

    _bill = staticmethod(_transaction_arm_bill)
    _settle = staticmethod(_transaction_arm_settle)

    @staticmethod
    def _corrected_count(caplog):
        """Return ``corrected_count`` off the one reconcile event emitted."""
        events = [
            record for record in caplog.records
            if getattr(record, "event", None) == EVT_TRANSACTIONS_RECONCILED
        ]
        assert len(events) == 1, "expected exactly one reconcile event"
        return events[0].corrected_count

    def test_an_envelope_close_with_no_correction_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The defect, reproduced from the failing direction.

        An envelope carrying `$40.00` of purchases settles at `$40.00` and its
        ``actual_amount`` moves from NULL to `$40.00` -- a MACHINE write, in the
        same statement as the status.  Nobody typed anything, so the count is
        zero.  Measured before the fix on a probe of this exact shape: it
        logged ``corrected_count: 1``.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(seed_user, [txn.id]) == 1

            assert self._corrected_count(caplog) == 0
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("40.00")

    def test_a_forged_box_on_a_DERIVED_row_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """A figure the verb IGNORES was not a correction.

        The panel renders no box for an entries-derived row, so the only way to
        submit one is by hand -- and ``settle_transaction`` drops it.  A count
        that read the submission rather than the outcome would report a
        correction the ledger never made.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [txn.id], {txn.id: Decimal("999.99")},
                ) == 1

            assert self._corrected_count(caplog) == 0

    def test_a_bill_ticked_at_its_prefill_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The echo: an untouched box is not a correction.

        The panel PREFILLS the box, so every correctable row on the form posts
        a figure whether the user touched it or not.  Counting submissions
        would measure how many boxes the panel drew.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [bill.id], {bill.id: Decimal("180.00")},
                ) == 1

            assert self._corrected_count(caplog) == 0

    def test_a_bill_ticked_at_a_DIFFERENT_figure_counts_ONE(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The one shape that IS a correction, so the count is not inert.

        Production's Electricity, to the cent: estimated `$300.00`, statement
        `$245.32`.  Without this case the three zeros above are satisfied by a
        count that is always zero.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="300.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [bill.id], {bill.id: Decimal("245.32")},
                ) == 1

            assert self._corrected_count(caplog) == 1


class TestTheSectionsAndTheOrder:
    """Ruling **R-FC**'s three presentational rules, graded.

    **They shipped with ZERO tests and an adversarial review said so.**  Nothing
    referenced ``OfferKind``, ``section_label``, ``kind``, ``_block_order`` or
    ``rank`` -- the entire content of the ruling, and the whole reason the
    assembler took ownership of the block order, was ungraded.  That is the
    balance arc's live lesson of the time (a producer's new fields are the fields
    nobody was asserting) repeating one leaf later.

    The rules: the blocks arrive ordered by kind then by each block's OLDEST
    offer; a section label is emitted where the kind CHANGES and nowhere else;
    and a childless block is one the template prints inline.
    """

    _bill = staticmethod(_transaction_arm_bill)

    @staticmethod
    def _resolved(seed_user):
        return reconcile_service.outstanding_set(_reconciled(seed_user))

    def test_each_arm_TAGS_its_kind_and_income_is_a_DEPOSIT(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """An income row is a DEPOSIT, not a bill.

        **The defect this grades was live.**  The kind was DERIVED from the
        block's shape -- purchases meant envelope, correctable meant bill -- and
        an income row is never purchase-tracked, so production's `$1,958.87`
        FSA reimbursement rendered under a heading reading "Bills" three lines
        below a summary counting it as a deposit.  A figure and its caption
        disagreeing, on the one screen read beside a paper statement.

        Shown to FIRE: classifying by ``is_correctable`` puts the deposit in
        ``Bills``.
        """
        with app.app_context():
            envelope = seed_entry_template["transaction"]
            bill = self._bill(seed_user, seed_periods[0], name="Electricity")
            deposit = self._bill(
                seed_user, seed_periods[0], name="FSA Reimbursement",
                amount="1958.87", income=True,
            )
            db.session.commit()

            by_id = {
                group.key: group.kind
                for group in self._resolved(seed_user).groups
            }
            assert by_id[envelope.id] is reconcile_service.OfferKind.ENVELOPE
            assert by_id[bill.id] is reconcile_service.OfferKind.BILL
            assert by_id[deposit.id] is reconcile_service.OfferKind.DEPOSIT

    def test_the_blocks_are_ordered_by_KIND_then_by_their_oldest_offer(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Like sits with like, and within a section the oldest is first.

        The bills are created NEWEST-first and given due dates that reverse
        that, so a result matching insertion order would be indistinguishable
        from one matching the rule if they agreed.
        """
        with app.app_context():
            late_bill = self._bill(
                seed_user, seed_periods[0], name="Water", fires_on_day=9,
            )
            early_bill = self._bill(
                seed_user, seed_periods[0], name="Electricity",
                fires_on_day=3,
            )
            deposit = self._bill(
                seed_user, seed_periods[0], name="Refund",
                amount="20.00", income=True,
            )
            envelope = seed_entry_template["transaction"]
            db.session.commit()

            order = [
                group.key
                for group in self._resolved(seed_user).groups
            ]
            assert order == [
                envelope.id, early_bill.id, late_bill.id, deposit.id,
            ]

    def test_a_section_label_is_emitted_ONLY_where_the_kind_changes(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """One heading per run, and it names the kind that follows it.

        Two bills between an envelope and a deposit: the second bill continues
        its section and carries no label.  A per-block label would print
        "Bills" twice; a missing one would leave the deposit under the bills'
        heading, which is the mis-captioning this ruling's sections exist to
        prevent.
        """
        with app.app_context():
            self._bill(seed_user, seed_periods[0], name="Electricity",
                       fires_on_day=3)
            self._bill(seed_user, seed_periods[0], name="Water",
                       fires_on_day=9)
            self._bill(seed_user, seed_periods[0], name="Refund",
                       amount="20.00", income=True)
            db.session.commit()

            labels = [
                group.section.label if group.section else None
                for group in self._resolved(seed_user).groups
            ]
            assert labels == ["Envelopes", "Bills", None, "Deposits"]

    def test_a_block_with_purchases_is_NOT_childless_and_one_without_is(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The childless rule's own input, which the template branches on.

        R-FC's first presentational rule is the template's -- a block with no
        children prints inline -- so what the producer owes it is an honest
        ``purchases``.  Graded here rather than by scraping markup: the
        rendering is one ``{% if %}`` over this tuple.
        """
        with app.app_context():
            envelope = seed_entry_template["transaction"]
            _make_entry(envelope, seed_user["user"], amount="40.00")
            bill = self._bill(seed_user, seed_periods[0], name="Electricity")
            db.session.commit()

            by_id = {
                group.key: group
                for group in self._resolved(seed_user).groups
            }
            assert len(by_id[envelope.id].purchases) == 1
            assert by_id[bill.id].purchases == ()

    def test_every_kind_has_a_rank_and_a_label(self):
        """The section vocabulary is TOTAL over its own members.

        ``rank`` resolves through a map built from the class, so a member added
        without one is impossible rather than silently sorted to the wrong end
        -- which is the failure mode a hand-written rank map has, and the reason
        plan step X-f2-c3 can add ``TRANSFER`` by writing one line.
        """
        kinds = list(reconcile_service.OfferKind)
        assert sorted(kind.rank for kind in kinds) == list(range(len(kinds)))
        assert all(kind.section_label for kind in kinds)
        # And exactly these two carry a NOTE: Transfers, because a tick there
        # settles a second account too, and Paid from this account (ruling
        # R-CC111, plan step credit_card:CC-5-4b), because its rows were
        # recorded here and reopened and a tick records one here on the
        # statement's day.  Asserted as a set rather than as a count so a note
        # appearing on the wrong section fails here rather than on the screen:
        # each sentence is about what a TICK does, and printing "settles both
        # sides" over the Bills section would be a false promise about
        # somebody's money.
        assert {
            kind for kind in kinds if kind.section_note
        } == {
            reconcile_service.OfferKind.TRANSFER,
            reconcile_service.OfferKind.SETTLEMENT,
        }


class TestThePanelHoldsONEAmountBasis:
    """Plan step **X-au-j**, finding **N-295**: the derivations are the PANEL's.

    An :class:`~app.services.cash_ledger.AmountBasis` holds the owner's live
    derivations -- the paycheck engine run over the whole pay-period set, and
    each loan's P&I, payment day and escrow history.  ``amount_basis``'s own
    docstring says calling those per row is finding **N-228**, and N-295
    recorded the reconcile panel doing exactly that: both source-row arms
    priced every offered row through their own ``settle_amount``, and each of
    those built its own.

    It is ``test_statement_match``'s ``TestThePassHoldsONEAmountBasis`` one
    package over, and it fails the same way: a later change that let a producer
    build its own would restore the cost in SILENCE, because every figure would
    still be right.  Only the wall clock moves, which is why the count is
    asserted and the timing is not.
    """

    def test_one_basis_serves_every_row_the_panel_offers(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The firing control: ONE construction, however many rows are offered.

        **The rows are PROJECTED deliberately, and that is what makes this
        control sharp.**  A settled row is valued from its own record
        (``row_valuation.fixed_contribution``) and never reaches the resolver,
        so a panel of settled rows builds one basis whether or not this step
        shipped -- the assertion would hold over a broken tree.
        """
        with app.app_context():
            # Six offerable bills, plus the seed fixture's own envelope close.
            for index in range(6):
                _transaction_arm_bill(
                    seed_user, seed_periods[0],
                    name=f"Bill {index}", amount=f"{100 + index}.00",
                )
            db.session.commit()

            built = count_amount_bases(monkeypatch)

            offers = {
                group.settle.key: group.settle
                for group in reconcile_service.outstanding_set(_reconciled(seed_user)).groups
                if group.settle is not None
            }

            assert len(offers) >= 6, (
                "the panel must offer several rows -- otherwise one basis and "
                "one per row are the same number and this grades nothing"
            )
            assert len(built) == 1, (
                f"the panel built {len(built)} amount bases for "
                f"{len(offers)} offered rows; X-au-j makes it one"
            )
            # And it is the OWNER's own, not some other budget's: a basis
            # prices from a scenario's salary profiles and a scenario's loans,
            # so a foreign one answers a different figure with nothing to say
            # so (``resolve_transaction_amount`` refuses it).
            assert built == [
                (seed_user["user"].id, seed_user["scenario"].id),
            ]
