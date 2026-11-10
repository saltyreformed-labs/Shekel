"""A loan payment's split on the worked-out read: one balanced correction, keyed by paycheck and day."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.services import (
    loan_ledger,
    loan_walk_reads,
    transaction_service,
    transfer_service,
)
from app.services.ledger_lines import linked_key
from app.utils.dates import display_today
from tests._ledger_reads import (
    account_net,
    posted_loan_balance_at,
    posted_loan_balance_map,
    scenario_lines,
)
from tests._test_helpers import (
    create_loan_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    insert_trueup_event,
    transfer_side_record,
    typed,
)

from ._helpers import (
    _ORIGINATION_DATE,
    _ORIGINATION_PRINCIPAL,
    _P1,
    _P2,
    _P3,
    _RATE,
    _a_split,
    _interest_net,
    _ledger_net,
    _loan_params,
    _loan_side_movement,
    _make_loan,
    _settle_payment,
    _split_lines,
    _split_lines_at,
)


class TestASkippedMonthIsTheNextPaymentsArrears:
    """The read charges every installment, and a payment clears its arrears first.

    Plan step recurrence:R16-c-2 (rulings **R-R72** part (1) and **R-R100**;
    finding **D53**'s past half): the settled walk charged one period per
    month its PAYMENTS occupied until that step, so a month nobody paid was
    never charged and its interest vanished from the ledger.  Every
    contractual installment from origination is charged now, and the next
    settled payment clears the months standing before it reaches principal --
    what a servicer's books say.  This is the catch-up ruling R-R101 promised
    a hand-computed test of when it restated the fixtures that had this shape
    by accident: SPLIT_LOAN's loan with no true-up, originated 2025-01-01.
    """

    def test_the_first_payment_clears_thirteen_months_and_books_them(
        self, app, db, seed_user, seed_periods,
    ):
        """$250,000 at 6% originated 2025-01-01, first payment $1,000 due 2026-02-01.

        Thirteen installments (2025-02-01 .. 2026-02-01) fall before the
        payment and nothing pays them, each charging round(250000 * 0.005) =
        1,250.00 on the unreduced balance: 16,250.00 of interest.  The $1,000
        payment clears what it can -- the whole allocation is interest, and
        principal is 1,000.00 - 16,250.00 = -15,250.00 (the balance GROWS, plan
        D5) -- so the loan owes 250,000.00 + 15,250.00 = 265,250.00.  The
        split books it: Loan -16,250.00 (backing the interest out of the
        cash the loan received) / Interest +16,250.00.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, _db.session, name="Never Paid",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE, term=360,
                origination_date=_ORIGINATION_DATE, payment_day=1,
            )
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()

            [split] = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert split.due_date == date(2026, 2, 1)
            assert (split.interest, split.escrow, split.principal) == (
                Decimal("16250.00"), Decimal("0.00"), Decimal("-15250.00"),
            )

            assert _split_lines(xfer, loan) == _a_split(
                loan, Decimal("-16250.00"), Decimal("16250.00"),
            )
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, date(2026, 3, 20),
            ) == Decimal("265250.00")


class TestAPaymentBooksItsSplit:
    """Each settled payment books one balanced split beside its cash line.

    The loan's own line carries the payment's cash (``transfer_movement``)
    and the split's loan leg (``loan_payment``), so its net is the FULL
    balance ``-(current balance)`` with the opening and true-up beside them.
    The split itself is read at the payment's key on the per-loan interest /
    escrow / refund lines, which the opening / true-up never touch.
    """

    def test_a_payment_books_one_balanced_split(
        self, app, db, seed_user, seed_periods,
    ):
        """The split is Loan -500 / Interest +500, summing to zero.

        Arithmetic: cash 1000, interest 500, principal 500.  The split's
        loan leg (-500) nets against the cash (+1000) to +500 of principal;
        the loan_interest line nets +500.00.  With the opening (-250000) and
        true-up (+150000), the loan's own line nets to -(current balance
        99500) = -99500.00.  A DERIVATION links no row (ruling R-BAL102): it
        is keyed by the payment's paycheck (the transfer's) and its loan
        side's day (the record's), which is where the read is asked, and the
        split books exactly the two legs -- no escrow leg on a loan with no
        escrow.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            assert _split_lines(xfer, loan) == _a_split(
                loan, Decimal("-500.00"), Decimal("500.00"),
            )
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert _interest_net(loan, scenario_id) == Decimal("500.00")

    def test_each_payment_of_a_multi_payment_loan_books_its_split(
        self, app, db, seed_user, seed_periods,
    ):
        """Three payments each book a split; the loan nets to -(final balance).

        Arithmetic (the running-balance walk from
        ``test_running_balance_across_payments``): interest 500.00, 497.50 and
        494.99, so principals 500.00 + 502.50 + 505.01 = 1507.51, and the three
        cash lines (+3000) plus the three split loan legs (-1492.49)
        contribute +1507.51 of principal.  With the opening (-250000) and
        true-up (+150000) the loan's own line nets -250000 + 150000 + 1507.51
        = -98492.49 == -(final balance 98492.49).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            payments = [
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
                for period in (
                    seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
                )
            ]
            db.session.commit()

            assert [_split_lines(xfer, loan) for xfer in payments] == [
                _a_split(loan, -interest, interest)
                for interest in (
                    Decimal("500.00"), Decimal("497.50"), Decimal("494.99"),
                )
            ]
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-98492.49")

    def test_a_payoff_books_a_refund_leg(self, app, db, seed_user, seed_periods):
        """A payoff overpayment books a refund-receivable leg.

        Arithmetic (anchor 300 @ 6%, cash 1000): interest 1.50, principal
        300.00, excess 698.50.  The loan_refund line nets +698.50; the payment
        contributes +300.00 of principal (cash 1000 - split 700).  With the
        opening (-250000) and true-up (+249700 = 250000 - 300) the loan's own
        line nets -300 + 300 = 0.00 -- the loan is paid off, -(balance 0).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            assert _ledger_net(
                LedgerAccountKindEnum.LOAN_REFUND, loan, scenario_id,
            ) == Decimal("698.50")
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("0.00")

    def test_the_split_is_apart_from_the_payments_cash_line(
        self, app, db, seed_user, seed_periods,
    ):
        """The loan's own line holds the cash and the split's leg as two lines.

        The CRITICAL invariant (plan Section 5): the split carries the
        ``loan_payment`` source kind and links NO row (ruling **R-BAL102**),
        so a read of the loan-side movement's lines (the ``transfer_movement``
        kind) sums only the cash (+1000), NOT the split (-500).  It was
        ``test_correction_is_invisible_to_the_cash_reader``, a stored cash
        reader's query, until plan step plan#2914 deleted the store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            movement_id = _loan_side_movement(xfer, loan).id
            own = linked_key(loan)

            on_the_loans_line = sorted((
                (line.source_kind_id, line.movement_id, line.amount)
                for line in scenario_lines(_db.session, scenario_id)
                if line.key == own and line.source_kind_id in (
                    ref_cache.posting_source_id(PostingSourceEnum.TRANSFER_MOVEMENT),
                    ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
                )
            ), key=str)
            assert on_the_loans_line == sorted([
                (
                    ref_cache.posting_source_id(PostingSourceEnum.TRANSFER_MOVEMENT),
                    movement_id, Decimal("1000.00"),
                ),
                (
                    ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
                    None, Decimal("-500.00"),
                ),
            ], key=str)
            # The full line (opening -250000 + true-up +150000 + cash 1000
            # + split -500) nets to -(current balance 99500) = -99500.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99500.00")

    def test_new_trueup_resplits_later_payments_and_keeps_earlier(
        self, app, db, seed_user, seed_periods,
    ):
        """A new trueup re-splits payments after it and KEEPS those before it.

        A payment before a new trueup keeps its split from the prior anchor,
        and only LATER payments re-split from the new value.

        P1 (period 1, due 02-01) and P2 (period 3, due 03-01) settle against
        the 100000 @ 01-10 trueup: interest 500.00 and round(99500 * 0.005) =
        497.50 = 997.50 total.  A NEW trueup of 90000 @ 02-15 falls BETWEEN
        them, so P1 keeps its 500.00 (it is due before the new trueup) and P2
        re-splits from 90000 to round(90000 * 0.005) = 450.00 -- the
        loan_interest line holds 500 + 450 = 950.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            p1_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            p2_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()
            assert _interest_net(loan, scenario_id) == Decimal("997.50")

            # New trueup at 90000 on 02-15 falls between P1 (due 02-01) and P2
            # (due 03-01): it re-bases P2 but leaves P1 untouched.
            insert_trueup_event(
                _loan_params(loan), Decimal("90000.00"), date(2026, 2, 15),
            )
            db.session.commit()

            # P1's 500 kept, P2 re-split 497.50 -> 450: interest line 950.
            assert _interest_net(loan, scenario_id) == Decimal("950.00")
            assert _split_lines(p1_xfer, loan) == _a_split(
                loan, Decimal("-500.00"), Decimal("500.00"),
            )
            assert _split_lines(p2_xfer, loan) == _a_split(
                loan, Decimal("-450.00"), Decimal("450.00"),
            )

    def test_an_ordinary_row_at_a_payments_key_leaves_its_split_whole(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary row on the loan at a payment's own key leaves its split whole.

        Through plan step ``balance:X-bi-6-1b`` this pinned a GUARD: the
        transaction path's reader was link-keyed and the correction carried
        the shadow's ``transaction_id``.  Leaf 1 of ``X-bi-6-3`` gave every
        typed reader a source-kind term (ruling **R-BAL101**); leaf 2 gave the
        split no row link at all (ruling **R-BAL102**).  So the property is
        structural, and this pins it as such.

        An ORDINARY row on the LOAN account, in the payment's own period and on
        its loan side's own settle day, settled, then reverted through the
        status door and deleted through the delete door.  After each step the
        split at the payment's key is still the whole ``500.00`` split, and the
        row's own ``25.00`` on the loan's line is read beside it, so every step
        is shown to have booked or dropped the row.  It was
        ``test_correction_is_disjoint_from_the_transaction_path``, with a
        repeat of the row's stored sync, until plan step plan#2914 deleted the
        store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            # The split: 100000 * 0.06 / 12 = 500.00 of interest.
            whole = _a_split(loan, Decimal("-500.00"), Decimal("500.00"))
            assert _split_lines(xfer, loan) == whole
            without_the_row = account_net(_db.session, loan.id, scenario_id)

            # The row settled: the loan line -25.00.
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[_P1], Decimal("25.00"),
                account=loan,
                settled_on=_loan_side_movement(xfer, loan).settled_on,
            )
            db.session.commit()
            assert _split_lines(xfer, loan) == whole
            assert account_net(_db.session, loan.id, scenario_id) == (
                without_the_row - Decimal("25.00")
            )

            # Reverted through the status door: the row books nothing.
            transaction_service.apply_requested_status(
                row, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert _split_lines(xfer, loan) == whole
            assert account_net(_db.session, loan.id, scenario_id) == without_the_row

            # Deleted through the delete door.
            transaction_service.delete_transaction(
                row, seed_user["user"].id,
                purchases_named=(
                    transaction_service.preview_deletion(row).purchase_ids
                ),
            )
            db.session.commit()
            assert _split_lines(xfer, loan) == whole
            assert account_net(_db.session, loan.id, scenario_id) == without_the_row

    def test_early_settled_payment_splits_at_settle(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled before its period begins books its split AND shows at settle.

        Two properties meet here.  The 2026-07-02 review's R1 (fixing H2): the
        cash line books the moment a payment settles, so the split must book
        in the SAME moment or the loan's line holds raw cash with no interest
        backout.  The C2 DISPLAY property: a payment is visible from its
        SETTLED date, so an EARLY settle shows immediately -- its REAL split,
        never the raw cash an unsplit ledger would have held.

        Frozen today 2026-02-10.  P1 is settled 2026-01-20 (inside its period); P2
        (budgeted to period 3, due 03-01) is settled EARLY on 2026-02-05 -- its
        PERIOD has not begun, but its settle has.  The split keys on the DUE date,
        so P1 (due 02-01) splits first -- interest 100000 * 0.005 = 500.00 -> 99500
        -- then P2 -- interest round(99500 * 0.005) = 497.50, principal 502.50 ->
        98997.50.  The early payment is P2's and not a later installment's since
        plan step recurrence:R16-c-2: every contractual installment is charged
        now, so an early payment for April would clear March's standing interest
        too (the fixture is restated so nothing is left unpaid, ruling R-R103).  So:

        * the P2 split is on the read once it settles, Loan -497.50 /
          Interest +497.50, filed in P2's period on its 02-05 settle day;
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
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            db.session.commit()
            # The premise: P2's PERIOD has not begun by the frozen today, but its
            # settle (2026-02-05) has -- an EARLY settle.
            assert seed_periods[_P2].start_date > frozen

            assert _split_lines_at(
                loan, scenario_id, seed_periods[_P2].id, date(2026, 2, 5),
            ) == _a_split(loan, Decimal("-497.50"), Decimal("497.50"))

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


class TestTheSplitIsADateKeyedCorrection:
    """The split links no row and is keyed (kind, period, day): ruling R-BAL102.

    Plan step ``balance:X-bi-6-3``.  The measured premise gap that re-ruled
    R-BAL100 and the hazards design_6_3.md s.10 names, each with its control:
    a payment of ``$0.00`` (no movement, a nonzero split), two payments at one
    key (one merged split), and a settled figure re-recorded to ``$0.00`` (the
    split moves to its installment, its size unchanged).  Every figure is
    hand-computed on the SPLIT_LOAN fixture: ``$100,000`` @ 6% charges
    ``round(100000 * 0.005) = 500.00`` for P1.
    """

    def test_a_zero_dollar_payment_books_its_charge_with_no_movement(
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
        it books linking no row -- the INSTALLMENT's day since ruling
        **R-BAL139**, where a payment that moved nothing is dated: the loan's
        own line nets opening (-250000) + true-up (+150000) + principal (-500)
        = -100500.00 and the interest line 500.00.

        **Paid is pressed on a day the test states, and the loan side is read
        off the transfer, since plan step ``balance:X-bi-6-4d-2``** (ruling
        **R-BAL167** class 1).  A ``$0.00`` close keeps no record (ruling
        **R-BAL141**), so the day it was pressed on is held nowhere but here.
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
            assert _split_lines_at(
                loan, scenario_id, seed_periods[_P1].id, pressed_on,
            ) == {}
            assert _split_lines_at(
                loan, scenario_id, seed_periods[_P1].id, date(2026, 2, 1),
            ) == _a_split(loan, Decimal("-500.00"), Decimal("500.00"))
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100500.00")
            assert _interest_net(loan, scenario_id) == Decimal("500.00")

    def test_two_payments_at_one_key_merge_into_one_target(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one period on one day share ONE key and its split.

        Both settle in P1 on the same day against P1's installment and the
        next: the first (``$1,000``) clears the period's ``500.00`` charge and
        the second (``$800``, due 03-01, balance 99500) clears ``round(99500
        * 0.005) = 497.50`` and pays 302.50 down.  Merged at the key
        ``(loan_payment, P1, the day)`` the split is Loan -997.50 / Interest
        +997.50.  The payment-history table still shows two rows, 500.00 and
        497.50, because it reads the walk; the ledger cannot say which payment
        paid what, and does not need to.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            day = seed_periods[_P1].start_date
            first_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=day,
            )
            second_xfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("800.00"), settled_on=day,
                due_date=date(2026, 3, 1),
            )
            db.session.commit()
            # One key: each payment's period off its transfer and its day off
            # its loan side's record.  Both were read off the income shadows
            # until plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
            assert (
                first_xfer.pay_period_id,
                _loan_side_movement(first_xfer, loan).settled_on,
            ) == (
                second_xfer.pay_period_id,
                _loan_side_movement(second_xfer, loan).settled_on,
            )

            assert _split_lines(first_xfer, loan) == _a_split(
                loan, Decimal("-997.50"), Decimal("997.50"),
            )
            assert _interest_net(loan, scenario_id) == Decimal("997.50")

            rows = loan_walk_reads.confirmed_loan_payment_history(
                loan.id, scenario_id, display_today(),
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
        nothing), so its cash line goes.  The split's FIGURES are unchanged
        because the interest charged does not depend on the cash --
        ``apply_payment_cash`` echoes the standing charge -- but its DAY moves:
        a payment that moved nothing is dated by the installment it skips
        (ruling **R-BAL139**), so the settle day's key holds nothing and the
        same split books at 2026-02-01.  Under a movement key this correction
        would have stranded the split.  Arithmetic: -99500.00 (cash +1000,
        split -500 on the anchors' -100000) becomes -100500.00 (no cash, the
        same split); the interest line holds 500.00 throughout.
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
            split = _a_split(loan, Decimal("-500.00"), Decimal("500.00"))
            assert _split_lines(xfer, loan) == split
            # The settle day's key, captured before the act: the record that
            # carries the day is withdrawn by it.  The day and the record are
            # the loan SIDE's, off the transfer, since plan step
            # balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
            settle_day = _loan_side_movement(xfer, loan).settled_on

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("0.00")),
            )
            db.session.commit()

            assert transfer_side_record(db.session, xfer.id, loan.id) is None
            assert _split_lines_at(
                loan, scenario_id, seed_periods[_P1].id, settle_day,
            ) == {}
            assert _split_lines_at(
                loan, scenario_id, seed_periods[_P1].id, date(2026, 2, 1),
            ) == split
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100500.00")
            assert _interest_net(loan, scenario_id) == Decimal("500.00")
