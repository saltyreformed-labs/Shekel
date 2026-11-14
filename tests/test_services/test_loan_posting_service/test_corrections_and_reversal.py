"""The split is a date-keyed correction linking no row (ruling R-BAL102), and a correction reverses
at its own key while stale ones self-heal.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.services import loan_posting_service, posting_service, status_seam, transfer_service
from app.services._posting_write import _emit_balanced_entry, _PostingLeg
from app.services.transfer_legs import transfer_side_leg
from tests._ledger_reads import account_net, loan_correction_lines_at
from tests._stored_ledger import loan_correction_entries_at, stored_ledger_net
from tests._test_helpers import create_settled_transfer, transfer_side_record, typed
from app.models.amount_ownership import AmountOwnership

from ._helpers import (
    _P1,
    _P2,
    _P3,
    _make_loan,
    _settle_payment,
    _linked_ledger_id,
    _find_loan_ledger,
    _interest_net,
    _loan_side_movement,
    _correction_entries,
    _entry_legs,
    _source_entry_count,
)


# ---------------------------------------------------------------------------
# reverse + stale-correction reversal
# ---------------------------------------------------------------------------


class TestTheSplitIsADateKeyedCorrection:
    """The split links no row and is keyed (kind, period, day): ruling R-BAL102.

    Plan step ``balance:X-bi-6-3``.  The measured premise gap that re-ruled
    R-BAL100 and the hazards design_6_3.md s.10 names, each with its control:
    a payment of ``$0.00`` (no movement, a nonzero split), two payments at one
    key (one merged entry), a settled figure re-recorded to ``$0.00`` (the
    key's target unchanged), and residue at a key the walk no longer prices
    (self-heals).  Every figure is hand-computed on the SPLIT_LOAN fixture:
    ``$100,000`` @ 6% charges ``round(100000 * 0.005) = 500.00`` for P1.
    """

    def test_a_zero_dollar_payment_posts_its_charge_with_no_movement(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan payment recorded at $0.00 has no movement and a $500.00 split.

        The measured case (2026-09-21, before the ruling): the seam writes NO
        covering movement for a figure of nothing (``amount <> 0``), yet the
        walk still charges the installment -- ``apply_payment_cash(0, 100000,
        500, 0)`` gives principal ``-500.00`` -- so the split is ``{Loan
        -500.00 principal, Interest +500.00}`` and the debt grows by the
        interest not paid: the app's only way to accrue a missed installment.
        Keyed by the loan-side movement (R-BAL100) that split had nothing to
        hang on; keyed by ``(loan_payment, P1, its installment 2026-02-01)``
        it posts linking no row -- the INSTALLMENT's day since ruling
        **R-BAL139**, where a payment that moved nothing is dated (it was the
        settle day until plan step balance:X-bi-6-4b; rule-5 re-expression,
        developer approval 2026-09-24) -- and the checked-projection assert holds:
        the loan-linked ledger nets opening (-250000) + true-up (+150000) +
        principal (-500) = -100500.00 and the interest ledger 500.00.

        **Paid is pressed on a day the test states, and the loan side is read
        off the transfer, since plan step ``balance:X-bi-6-4d-2``** (ruling
        **R-BAL167** class 1): it asked the income shadow for its covering
        movements and its settle day, and both answers had been empty since
        plan step ``balance:X-bi-6-4d-2`` moved the record onto the transfer
        and stopped keeping a shadow's day, so the settle-day key it read was
        ``None``.  A ``$0.00`` close keeps no record (ruling **R-BAL141**), so
        the day it was pressed on is held nowhere but here.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            pressed_on = date(2026, 1, 20)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                actual=Decimal("0.00"), settled_on=pressed_on,
            )
            db.session.commit()

            assert transfer_side_record(db.session, xfer.id, loan.id) is None
            assert loan_correction_lines_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                pressed_on,
            ) == []
            entries = loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                date(2026, 2, 1),
            )
            assert len(entries) == 1
            entry = entries[0]
            assert entry.transaction_entry_id is None
            legs = _entry_legs(entry.id)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100500.00")
            # A repeat sync -- the assert inside it holding -- writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_two_payments_at_one_key_merge_into_one_target(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one period on one day share ONE key and its target.

        Both settle in P1 on the same day against P1's installment and the
        next: the first (``$1,000``) clears the period's ``500.00`` charge and
        the second (``$800``, due 03-01, balance 99500) clears ``round(99500
        * 0.005) = 497.50`` and pays 302.50 down.  Merged at the key
        ``(loan_payment, P1, the day)`` the target is Loan -997.50 / Interest
        +997.50, and the ledger reaches it append-only: the first settle's
        entry (-500 / +500) plus the DELTA the second settle emits at the same
        key (-497.50 / +497.50), two entries summing to the target.  The
        payment-history table still shows two rows, 500.00 and 497.50,
        because it reads the walk and not the ledger; the ledger cannot say
        which payment paid what, and does not need to.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            day = seed_periods[_P1].start_date
            first_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=day,
            )
            db.session.commit()
            second_xfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("800.00"), settled_on=day,
                due_date=date(2026, 3, 1),
            )
            db.session.commit()
            # One key: each payment's period off its transfer and its day off
            # its loan side's record.  Both were read off the income shadows
            # until plan step balance:X-bi-6-4d-2, whose day stopped being
            # kept at plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
            assert (
                first_xfer.pay_period_id,
                _loan_side_movement(first_xfer, loan).settled_on,
            ) == (
                second_xfer.pay_period_id,
                _loan_side_movement(second_xfer, loan).settled_on,
            )

            entries = _correction_entries(first_xfer, loan)
            assert len(entries) == 2
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            by_entry = [_entry_legs(entry.id) for entry in entries]
            assert [legs[interest_ledger.id][0] for legs in by_entry] == [
                Decimal("500.00"), Decimal("497.50"),
            ]
            assert sum(
                legs[_linked_ledger_id(loan)][0] for legs in by_entry
            ) == Decimal("-997.50")
            assert _interest_net(loan, scenario_id) == Decimal("997.50")

            rows = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, date.today(),
            )
            assert [(row.installment, row.interest, row.principal) for row in rows] == [
                (date(2026, 2, 1), Decimal("500.00"), Decimal("500.00")),
                (date(2026, 3, 1), Decimal("497.50"), Decimal("302.50")),
            ]

    def test_a_figure_re_recorded_to_zero_moves_the_split_to_its_installment(
        self, app, db, seed_user, seed_periods,
    ):
        """Correcting a settled $1,000 payment to $0.00 moves its split, not its size.

        The seam withdraws the covering movement (a ``$0.00`` record carries
        nothing) and its cash leg reverses through the movement door.  The
        split's FIGURES are unchanged because the interest charged does not
        depend on the cash -- ``apply_payment_cash`` echoes the standing
        charge -- but its DAY moves: a payment that moved nothing is dated by
        the installment it skips (ruling **R-BAL139**), so the settle day's
        correction reverses and the same split books at 2026-02-01.  *Until
        plan step balance:X-bi-6-4b the day was the shadow's settle day and the
        key did not move (rule-5 re-expression, developer approval
        2026-09-24).*
        Under a movement key this correction would have SET NULL the split's
        link on the withdrawal and stranded it.  Arithmetic: -99500.00 (cash
        +1000, split -500 on the anchors' -100000) becomes -100500.00 (no
        cash, the same split); the interest ledger holds 500.00 throughout.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert len(_correction_entries(xfer, loan)) == 1
            # The settle day's key, captured before the act: the record that
            # carries the day is withdrawn by it.  The day and the record are
            # the loan SIDE's, off the transfer, since plan step
            # balance:X-bi-6-4d-2; it read the income shadow's covering
            # movement and kept day until then (ruling R-BAL167 class 1).
            settle_day = _loan_side_movement(xfer, loan).settled_on

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("0.00")),
            )
            db.session.commit()

            assert transfer_side_record(db.session, xfer.id, loan.id) is None
            assert len(loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                settle_day,
            )) == 2, (
                "the settle day's correction and its reversal"
            )
            assert len(loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                date(2026, 2, 1),
            )) == 1
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100500.00")
            assert _interest_net(loan, scenario_id) == Decimal("500.00")

    def test_residue_at_a_key_the_walk_does_not_price_self_heals(
        self, app, db, seed_user, seed_periods,
    ):
        """A split posted at a key with no target is reversed by the next sync.

        Under the shadow key a ``loan_payment`` entry whose row was hard-deleted
        without the reversal-before was RESIDUE: its link SET NULL, no row to
        key a reversal by, the statements dropping it and the assert refusing
        the loan forever.  Under the date key every posted key is reconciled
        on every sync, so the same entry -- planted here by hand at P2 on a
        day the walk prices nothing -- is a posted key with no target and
        reverses in one pass: the key nets to zero, the interest ledger back
        to P1's 500.00, and the assert holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            stray_period = seed_periods[_P2]
            stray_day = stray_period.start_date
            stray = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=scenario_id,
                pay_period_id=stray_period.id,
                entry_date=stray_day,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.LOAN_PAYMENT,
                ),
                description="Loan payment split: stray",
            )
            _emit_balanced_entry(stray, [
                _PostingLeg(
                    _linked_ledger_id(loan), Decimal("-7.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
                _PostingLeg(
                    interest_ledger.id, Decimal("7.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
                ),
            ])
            db.session.commit()
            assert stored_ledger_net(
                _db.session, interest_ledger.id, scenario_id,
            ) == Decimal("507.00")

            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()

            at_key = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, stray_period.id, stray_day,
            )
            assert len(at_key) == 2
            assert _entry_legs(at_key[-1].id)[interest_ledger.id][0] == (
                Decimal("-7.00")
            )
            assert stored_ledger_net(
                _db.session, interest_ledger.id, scenario_id,
            ) == Decimal("500.00")
            assert len(_correction_entries(xfer, loan)) == 1


class TestReverseLoanPaymentPostings:
    """A correction reverses at its own key, and stale ones self-heal.

    The split is a date-keyed correction with no row link since plan step
    ``balance:X-bi-6-3`` (ruling **R-BAL102**), so nothing reverses it
    "before a delete" any more: a payment that leaves the walk -- reverted,
    deleted, moved -- leaves its key with no target, and the one reconcile
    reverses the key.  The two tests that drove the deleted
    ``reverse_loan_payment_postings_for_shadow`` directly are re-expressed
    on the doors that own the act (a rule-5 class (b) re-expression Josh
    confirmed 2026-09-21).  Two of this class's figures moved, -99000 ->
    -100000 in ``test_hard_delete_reverses_the_correction_at_its_own_key``
    and ``test_resync_reverses_an_unsettled_payment``: the direct door left
    the cash leg posted beside a reversed split, a state no production door
    produces, and the doors these tests drive now reverse both halves.
    """

    def test_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted-and-moved payment's correction reverses into its old period.

        The loan twin of the R2 attribution rule (the 2026-07-02 review's H1
        class): one ``update_transfer`` call reverts the payment to Projected
        AND moves it to a later period -- the shadows carry the NEW period by
        the time the loan wiring reconciles.  The stale correction must
        reverse into the period it was POSTED in (P1's), never the shadow's
        new one, so the correction pair nets P1's period to zero and the new
        period holds no loan_payment entries at all.  Arithmetic: the P1
        split was interest 500.00 / principal 500.00, so the reversal legs
        are Loan +500.00 / Interest -500.00 in P1's period, and the interest
        ledger nets back to zero.  Under the date key the rule is structural
        -- the posted key IS ``(P1, the original day)`` and a revert releases
        the loan side's day (its record's, off the transfer since plan step
        balance:X-bi-6-4d-2; the shadow's until then, ruling R-BAL167 class
        1), so the entries are read at the key the split was posted under,
        captured before the revert.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            original_period_id = seed_periods[_P1].id
            # The day is the loan side's record's (ruling R-BAL167 class 1,
            # plan step balance:X-bi-6-4d-2; the shadow's own until then).
            original_day = _loan_side_movement(xfer, loan).settled_on
            moved_to = seed_periods[_P3]

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            db.session.commit()

            entries = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, original_period_id,
                original_day,
            )
            assert len(entries) == 2
            reversal = entries[-1]
            # The R2 rule: the reversal carries the ORIGINAL period -- the
            # pre-fix code stamped it with the shadow's NEW period.
            assert reversal.pay_period_id == original_period_id
            assert all(
                entry.pay_period_id == original_period_id for entry in entries
            )
            # And the new period holds no split at all: the walk no longer
            # prices the payment, so no key of its own was posted there.
            assert not loan_correction_lines_at(
                _db.session, loan.id, scenario_id, moved_to.id, original_day,
            )
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(reversal.id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert _interest_net(loan, scenario_id) == Decimal("0.00")

    def test_hard_delete_reverses_the_correction_at_its_own_key(
        self, app, db, seed_user, seed_periods,
    ):
        """A hard-deleted payment's correction reverses at its key, leg for leg.

        The delete door reverses the cash leg BEFORE the row goes (the
        movement's link is what a hard delete severs) and re-syncs the loan
        AFTER: the walk no longer prices the payment, its key ``(P1, the
        settle day)`` has no target, and the reconcile emits the reversal
        there -- Loan +500.00 / Interest -500.00 against the original's
        Loan -500.00 / Interest +500.00, so the key nets to zero.  Nothing
        about the split needed the shadow to exist: it linked no row.  The
        loan-linked ledger returns to the opening (-250000) + true-up
        (+150000) = -100000.00 and the interest ledger to 0.00.  Re-expressed
        from a direct call to the deleted ``reverse_loan_payment_postings_
        for_shadow``, which produced a state no door produces (the split
        reversed with the cash still posted) and pinned -99000.00 for it;
        the figure is -100000.00 here because the delete door reverses the
        cash leg too -- the second of the two changed figures this module's
        docstring names.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            # The day is the loan side's record's (ruling R-BAL167 class 1,
            # plan step balance:X-bi-6-4d-2; the shadow's own until then).
            period_id = xfer.pay_period_id
            day = _loan_side_movement(xfer, loan).settled_on
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert len(
                loan_correction_entries_at(
                    _db.session, loan.id, scenario_id, period_id, day,
                )
            ) == 1

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            entries = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, period_id, day,
            )
            assert len(entries) == 2
            legs = _entry_legs(entries[-1].id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _interest_net(loan, scenario_id) == Decimal("0")

    def test_deleting_an_unposted_payment_writes_no_split(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting a payment that carries no posted correction writes nothing.

        Commit 5 wires the split-posting into the transfer chokepoints, so a
        SETTLED loan payment auto-posts its correction; a genuinely unposted
        payment is therefore a PROJECTED one (never settled -> never in the
        walk).  Deleting it must write no ``loan_payment`` entry at all: the
        loan's re-sync after the delete finds no posted key and no target.
        Re-expressed from a direct call to the deleted reversal door.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=seed_periods[_P1].id,
                    scenario_id=scenario_id,
                    amount_ownership=AmountOwnership.own(Decimal("1000.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            db.session.commit()

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()
            loan_payment_source = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )
            assert (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == seed_user["user"].id,
                    JournalEntry.source_kind_id == loan_payment_source,
                )
                .count()
            ) == 0

    def test_resync_reverses_an_unsettled_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that leaves the eligible set is reversed by the next sync.

        Settle + sync (one correction; loan-linked -99500 = opening -250000 +
        true-up +150000 + principal 500), then revert the pair through the
        status SEAM alone -- the transfer's status and its sides' records, with
        NO posting sync behind it, standing in for a door that forgot the
        ledger -- and run the one loan sync bare: the now-stale correction's
        key has no target and reverses, the lineage probe finds the cash leg
        posted for a movement the revert un-dated and reverses it through
        the pair's door, and the loan-linked ledger drops to the opening +
        true-up = -100000 with the checked-projection assert holding.

        **The figure was -99000 through plan step ``balance:X-bi-6-1b``, and
        the fixture was an impossible state**: the payment-only door this
        test drove (deleted at ``X-bi-6-3``, ruling R-BAL102) ran no assert,
        so the test could un-settle the shadows' STATUS by raw SQL, leave the
        covering movement DATED (a state no revert leaves, ruling R-BAL61),
        and pin the cash leg still posted beside a reversed split.  The one
        sync refuses that state, correctly (its assert fires: walk 0.00 vs
        posted 1000.00 at the settle day), so the fixture is the honest
        revert and the figure is what the ledger holds after it.  A rule-5
        class (b) re-expression Josh confirmed 2026-09-21.

        **The seam's TRANSFER arm since plan step ``balance:X-bi-6-4d-2``**
        (ruling R-BAL167 class 2): the sides' records hang off the transfer,
        so the revert un-dates them through ``status_seam.sync_side_records``
        -- the call the transfer's status writer makes beside
        ``apply_status_change`` -- where it applied the seam to each shadow
        row, whose covering movement held the record, until then.  Reverting
        the twins and the transfer's status alone now leaves both records
        dated under a Projected transfer, which the database refuses at
        commit, so the stale ledger is reached through the arm instead.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            period_id = xfer.pay_period_id
            day = _loan_side_movement(xfer, loan).settled_on

            # Revert through the seam alone (no posting sync): the status and
            # both sides' records move, each record is un-dated, the ledger is
            # left stale on BOTH halves.
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            status_seam.sync_side_records(
                (
                    transfer_side_leg(xfer, is_income=False),
                    transfer_side_leg(xfer, is_income=True),
                ),
                projected, days=(None, None), settlement=None, press=None,
            )
            status_seam.apply_status_change(xfer, projected)
            db.session.commit()
            assert _loan_side_movement(xfer, loan).settled_on is None
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            # The correction is reversed at its own key and the cash leg
            # through the pair's door; the loan holds the opening + true-up.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            entries = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, period_id, day,
            )
            assert len(entries) == 2
            assert posting_service.account_posting_total(
                seed_user["account"].id, scenario_id,
            ) == Decimal("1000.00")
