"""sync_loan_postings posts one balanced split correction per payment."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum, StatusEnum
from app.extensions import db as _db
from app.services import loan_posting_service, posting_service, transaction_service
from tests._ledger_reads import account_net, posted_loan_balance_at, posted_loan_balance_map
from tests._test_helpers import create_settled_cash_transaction, freeze_today, insert_trueup_event

from ._helpers import (
    _P1,
    _P2,
    _P3,
    _loan_params,
    _make_loan,
    _settle_payment,
    _linked_ledger_id,
    _find_loan_ledger,
    _ledger_net,
    _interest_net,
    _loan_side_movement,
    _cash_reader_loan_net,
    _correction_entries,
    _split_and_interest,
    _entry_legs,
)


class TestSyncLoanPaymentPostings:
    """Syncing posts one balanced correction per payment.

    The setup settles through the transfer service, which fires the go-forward
    wiring and posts the loan's opening + true-up corrections alongside the
    payment splits, so ``account_posting_total(loan)`` is the FULL genesis balance
    ``-(current balance)``.  The payment split itself is pinned by the correction
    entry and the per-loan interest / escrow / refund legs, which the opening /
    true-up corrections never touch.  Every sync here is the ONE
    ``sync_loan_postings`` (plan step ``balance:X-bi-6-3``): the payment-only
    door it drove is gone with the split's row key, and the anchor half it
    runs beside was already posted by the wiring, so nothing a test pins moves.
    """

    def test_sync_posts_one_balanced_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """The correction is Loan -500 / Interest +500, summing to zero.

        Arithmetic: cash 1000, interest 500, principal 500.  The correction's
        loan leg (-500) nets against the Step-2 cash (+1000) to +500 of principal;
        the loan_interest ledger nets +500.00.  With the wiring's opening (-250000)
        and true-up (+150000) also posted, the loan-linked ledger nets to
        -(current balance 99500) = -99500.00.
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

            entries = _correction_entries(xfer, loan)
            assert len(entries) == 1
            entry = entries[0]
            # A DERIVATION links no row (ruling R-BAL102): it is keyed by the
            # payment's period and visible day alone.  It carried
            # ``transaction_id == shadow.id`` through plan step
            # ``balance:X-bi-6-1b`` (a rule-5 class (a) re-expression).  The
            # day is the loan side's RECORD's since plan step
            # balance:X-bi-6-4d-2; it read the income shadow's own day until
            # then (ruling R-BAL167 class 1).  The period is the transfer's
            # since plan step balance:X-bi-6-4d-2; it read the shadow's
            # mirrored copy until then (ruling R-BAL167 class 1).
            assert entry.transaction_entry_id is None
            assert entry.pay_period_id == xfer.pay_period_id
            assert entry.entry_date == _loan_side_movement(xfer, loan).settled_on

            loan_ledger = _linked_ledger_id(loan)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entry.id)
            assert legs[loan_ledger] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500)
            # = -(current balance 99500); interest ledger holds the +500.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert _interest_net(loan, scenario_id) == Decimal("500.00")

    def test_sync_posts_each_payment_in_a_multi_payment_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """Three payments each get a correction; the loan nets to -(final balance).

        Arithmetic (the running-balance walk from
        ``test_running_balance_across_payments``): principals 500.00 + 502.50 +
        505.01 = 1507.51, so the three Step-2 cash legs (+3000) plus the three
        correction loan legs (-1492.49) contribute +1507.51 of principal.  With
        the opening (-250000) and true-up (+150000) the loan-linked ledger nets
        -250000 + 150000 + 1507.51 = -98492.49 == -(anchor 100000 - 1507.51) =
        -(final balance 98492.49).  A second whole-loan sync writes nothing
        (idempotent across every payment).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            payments = []
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                payments.append(_settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                ))
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert all(
                len(_correction_entries(xfer, loan)) == 1 for xfer in payments
            )
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-98492.49")

            # Idempotent across the whole loan: a re-sync adds no entries.
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert all(
                len(_correction_entries(xfer, loan)) == 1 for xfer in payments
            )

    def test_resync_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same target writes no new entry."""
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
            assert len(_correction_entries(xfer, loan)) == 1

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert len(_correction_entries(xfer, loan)) == 1

    def test_no_escrow_loan_drops_the_escrow_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with no escrow components creates no loan_escrow ledger."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # no escrow component
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_ESCROW,
            ) is None

    def test_payoff_posts_a_refund_leg(self, app, db, seed_user, seed_periods):
        """A payoff overpayment books a refund-receivable leg.

        Arithmetic (anchor 300 @ 6%, cash 1000): interest 1.50, principal
        300.00, excess 698.50.  The loan_refund ledger nets +698.50; the payment
        contributes +300.00 of principal (cash 1000 - correction 700).  With the
        opening (-250000) and true-up (+249700 = 250000 - 300) the loan-linked
        ledger nets -300 + 300 = 0.00 -- the loan is paid off, -(balance 0).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            refund_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert refund_ledger is not None
            assert _ledger_net(
                LedgerAccountKindEnum.LOAN_REFUND, loan, scenario_id,
            ) == Decimal("698.50")
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("0.00")

    def test_sync_never_touches_checking(
        self, app, db, seed_user, seed_periods,
    ):
        """The loan sync moves only loan ledgers -- Checking is unchanged.

        The Step-2 cash entry already moved Checking (its $1000.00 Step-5
        opening - 1000 = 0.00); the loan correction must not move it
        further, so Checking's posted total is identical before and after
        the sync.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            checking_before = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            checking_after = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            assert checking_before == Decimal("0.00")
            assert checking_after == checking_before

    def test_correction_is_invisible_to_the_cash_reader(
        self, app, db, seed_user, seed_periods,
    ):
        """The cash reader (movement-keyed, source-filtered) never sees the correction.

        The CRITICAL invariant (plan Section 5): the correction carries the
        ``loan_payment`` source kind and links NO row (ruling **R-BAL102**),
        so a reader filtering the loan-side movement's link under the
        ``transfer_movement`` kind on the loan ledger sums only the cash
        (+1000), NOT the correction (-500) -- which is what keeps the cash
        path's reversals correct.  It read ``transfer_id == xfer.id`` while
        the cash was one entry per transfer (re-expressed at plan step
        ``balance:X-bi-6-3``); the correction is structurally invisible to a
        link-keyed reader now, and the SOURCE KIND term stays as the reader's
        own statement of which source is cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_ledger = _linked_ledger_id(loan)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            # The cash reader sees only the cash leg -- neither the payment
            # correction nor the opening / true-up (other source kinds).
            assert _cash_reader_loan_net(
                xfer, loan, loan_ledger,
            ) == Decimal("1000.00")
            # But the full ledger (opening -250000 + true-up +150000 + cash 1000
            # + correction -500) nets to -(current balance 99500) = -99500.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99500.00")

    def test_new_trueup_resplits_later_payments_and_keeps_earlier(
        self, app, db, seed_user, seed_periods,
    ):
        """A new trueup re-splits payments after it and KEEPS those before it.

        Genesis retires the Step-4 "pushed behind anchor" reversal: a payment
        before a new trueup is NOT reversed -- it keeps its split from the prior
        anchor, and only LATER payments re-split from the new value.

        P1 (period 1, due 02-01) and P2 (period 3, due 03-01) settle and sync
        against the 100000 @ 01-10 trueup: interest 500.00 and
        round(99500 * 0.005) = 497.50 = 997.50 total.  A NEW trueup of 90000 @
        02-15 falls BETWEEN them, so after re-sync P1 keeps its 500.00 (it is due
        before the new trueup) and P2 re-splits from 90000 to
        round(90000 * 0.005) = 450.00 -- the loan_interest ledger holds
        500 + 450 = 950.00, and P1's correction is NOT reversed (one entry, where
        Step 4 left an original-plus-reversal pair).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            p1_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert _interest_net(loan, scenario_id) == Decimal("997.50")

            # New trueup at 90000 on 02-15 falls between P1 (due 02-01) and P2
            # (due 03-01): it re-bases P2 but leaves P1 untouched.
            insert_trueup_event(
                _loan_params(loan), Decimal("90000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            # P1's 500 kept, P2 re-split 497.50 -> 450: interest ledger 950.
            assert _interest_net(loan, scenario_id) == Decimal("950.00")
            # P1's correction is NOT reversed -- genesis keeps pre-trueup splits.
            assert len(_correction_entries(p1_xfer, loan)) == 1

    def test_correction_is_disjoint_from_the_transaction_path(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary row posted on the loan at a payment's own key leaves its split whole.

        Through plan step ``balance:X-bi-6-1b`` this pinned a GUARD: the
        transaction path's reader was link-keyed and the correction carried
        the shadow's ``transaction_id``, so the only thing keeping the split
        out of that reader's reversal was ``if txn.transfer_id is not None:
        return None``.  Leaf 1 of ``X-bi-6-3`` deleted the guard and gave
        every typed reader a source-kind term (ruling **R-BAL101**); leaf 2
        gave the split no row link at all (ruling **R-BAL102**).  So the
        property is structural now, and this pins it as such.

        It drove the poster on the payment's loan-side income shadow until
        plan step ``balance:X-bi-6-4d-2`` made a transfer one row: no
        transaction row can name the payment now (ruling **R-BAL258**).
        Re-expressed under ruling **R-BAL167** class 1 (the cp2a review's
        L-2, its RV-B3), an ordinary row standing where the shadow stood: an
        ORDINARY row on the LOAN account, in the payment's own period and on
        its loan side's own settle day, settled through the real go-forward
        primitives (which post it) and then driven through
        ``sync_transaction_postings`` again, then reverted through the status
        door and deleted through the delete door.  What is read is the
        split's EFFECT after every step of the row's life: its settle (its
        first post, the cp2b-0 review's M-1) and the repeat, its revert and
        its delete (the cp2b-1 review's L-1).  After each, the split is
        still one entry and the loan_interest ledger still nets its
        ``500.00``.  A reach into the split, by its link or by its key and
        through whichever source's reader reached it, would reverse it at
        the step that ran that reader, so every step is read; the row's own
        ``25.00`` on the loan's linked ledger is read beside it, so every
        step is shown to have posted or reversed the row.  The repeat
        writes nothing.
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
            # The split: one entry, 100000 * 0.06 / 12 = 500.00 of interest.
            whole = (1, Decimal("500.00"))
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            without_the_row = account_net(_db.session, loan.id, scenario_id)

            # Settling the row posts it: its FIRST post, the loan leg -25.00.
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[_P1], Decimal("25.00"),
                account=loan,
                settled_on=_loan_side_movement(xfer, loan).settled_on,
            )
            db.session.commit()
            # A repeat writes nothing: no entry returned.
            assert not posting_service.sync_transaction_postings(row)
            db.session.commit()
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            assert account_net(_db.session, loan.id, scenario_id) == (
                without_the_row - Decimal("25.00")
            )

            # Reverted through the status door: the row's posting reversed.
            transaction_service.apply_requested_status(
                row, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            assert account_net(_db.session, loan.id, scenario_id) == without_the_row

            # Deleted through the delete door: the row's teardown.
            transaction_service.delete_transaction(
                row, seed_user["user"].id,
                purchases_named=(
                    transaction_service.preview_deletion(row).purchase_ids
                ),
            )
            db.session.commit()
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            assert account_net(_db.session, loan.id, scenario_id) == without_the_row

    def test_early_settled_payment_splits_at_settle(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled before its period begins posts its split AND shows at settle.

        Two properties meet here.  The WRITE property (the 2026-07-02 review's R1,
        fixing H2): the Step-2 cash entry posts the moment a payment settles, so the
        split correction must post in the SAME moment or the loan-linked ledger
        holds raw cash with no interest backout.  The C2 DISPLAY property: a payment
        is visible from its SETTLED date, so an EARLY settle shows immediately -- its
        REAL split, never the raw cash the pre-split ledger would have held.

        Frozen today 2026-02-10.  P1 is settled 2026-01-20 (inside its period); P2
        (budgeted to period 3, due 03-01) is settled EARLY on 2026-02-05 -- its
        PERIOD has not begun, but its settle has.  The split keys on the DUE date,
        so P1 (due 02-01) splits first -- interest 100000 * 0.005 = 500.00 -> 99500
        -- then P2 -- interest round(99500 * 0.005) = 497.50, principal 502.50 ->
        98997.50.  The early payment is P2's and not a later installment's since
        plan step recurrence:R16-c-2: every contractual installment is charged
        now, so an early payment for April would clear March's standing interest
        too (the fixture is restated so nothing is left unpaid, ruling R-R103).  So:

        * the P2 correction exists AT SETTLE (no manual sync), legs
          Loan -497.50 / Interest +497.50, attributed to P2's period;
        * BOTH settled dates (01-20, 02-05) are on or before the frozen today, so
          the scalar reads the REAL 98997.50 -- never the raw cash
          99500 - 1000 = 98500.00 the unsplit ledger would show (H2).
        """
        with app.app_context():
            frozen = date(2026, 2, 10)
            freeze_today(monkeypatch, frozen)
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            early_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            db.session.commit()
            # The premise: P2's PERIOD has not begun by the frozen today, but its
            # settle (2026-02-05) has -- an EARLY settle.
            assert seed_periods[_P2].start_date > frozen

            # The correction posted at settle, through the transfer wiring --
            # no manual sync call -- attributed to the payment's own period.
            entries = _correction_entries(early_xfer, loan)
            assert len(entries) == 1
            assert entries[0].pay_period_id == seed_periods[_P2].id
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entries[0].id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("-497.50"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("497.50"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )

            # C2: both payments are visible from their settled dates (<= frozen),
            # so the scalar shows the REAL split -- never the raw cash 98500.00.
            assert posted_loan_balance_at(
                loan.id, scenario_id, frozen,
            ) == Decimal("98997.50")

            # The map at P2's period agrees (period-END keyed).
            balance_map = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert balance_map[seed_periods[_P2].id] == Decimal("98997.50")
