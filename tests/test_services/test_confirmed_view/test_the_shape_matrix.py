"""The nine shapes plan step E1c proved, each re-anchored on hand arithmetic."""

from datetime import date
from decimal import Decimal

from app.models.loan_features import RateHistory
from tests._test_helpers import create_loan_account, insert_tracking_start_event

from ._helpers import (
    _ORIGINATION_PRINCIPAL,
    _ORIGINATION_DATE,
    _RATE,
    _ANCHOR_BALANCE,
    _ANCHOR_DATE,
    _P1,
    _P2,
    _P3,
    _loan_params,
    _make_loan,
    _settle,
    _view,
    _economics,
)


# The origination a case takes when a payment is walked BEFORE its true-up: the
# month before P1's 02-01 installment (plan step recurrence:R16-c-2, ruling
# R-R101).  Every contractual installment from origination is charged now, so
# SPLIT_LOAN's 2025-01-01 would have that payment clear twelve unpaid months
# first; restated, each figure stays what these cases were written to pin.
_ORIGINATION_BEFORE_P1 = date(2026, 1, 1)


class TestConfirmedViewShapeMatrix:
    """The nine shapes plan step E1c proved, each re-anchored on hand arithmetic."""

    def test_escrow_leaves_the_payment_before_it_reaches_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $3,600/yr escrow takes $300 a month off the cash before principal.

        Each $1,000 payment splits interest + $300 escrow + principal, and the
        escrow posts OFF the liability ledger, so the balance moves by principal
        only:

          due 02-01: interest round(100000 x 0.005) = 500.00, principal
            1000 - 500 - 300 = 200.00 -> 99,800.00
          due 03-01: interest round(99800 x 0.005) = 499.00, principal
            201.00 -> 99,599.00
          due 04-01: interest round(99599 x 0.005) = round(497.995) = 498.00,
            principal 202.00 -> 99,397.00

        The row's ``payment`` is the P&I portion (200.00 + 500.00 = 700.00), NOT
        the $1,000 of cash: escrow is not debt service.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, escrow_annual=Decimal("3600.00"),
            )
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle(seed_user, loan, period)
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("200.00"), Decimal("99800.00")),
                (date(2026, 3, 1), Decimal("499.00"),
                 Decimal("201.00"), Decimal("99599.00")),
                (date(2026, 4, 1), Decimal("498.00"),
                 Decimal("202.00"), Decimal("99397.00")),
            ]
            assert [row.payment for row in view.history_rows] == [
                Decimal("700.00"), Decimal("700.00"), Decimal("700.00"),
            ]
            assert view.balance == Decimal("99397.00")

    def test_an_arm_rate_step_lifts_the_interest_and_the_rows_rate(
        self, app, db, seed_user, seed_periods,
    ):
        """A rate change to 9% governs every payment DUE on or after it.

        The rate steps to 9% (monthly 0.0075) effective 2026-02-01.  The split
        resolves each payment's rate period on its DUE date -- contract time,
        ruling D5 (see ``test_what_the_walk_reads.py``'s
        :class:`TestSplitInputsKeyOnTheDueDate`) -- so all three
        installments, due 02-01 / 03-01 / 04-01, accrue at 9%:

          due 02-01 at 9%:  interest round(100000 x 0.0075) = 750.00, principal
            1000 - 750 = 250.00 -> 99,750.00
          due 03-01 at 9%:  interest round(99750 x 0.0075) = round(748.125)
            = 748.13, principal 251.87 -> 99,498.13
          due 04-01 at 9%:  interest round(99498.13 x 0.0075) = round(746.2360)
            = 746.24, principal 253.76 -> 99,244.37

        The FIRST row is the one that moves with the keying: period 1 starts
        2026-01-16, before the change, while its installment falls on the very
        day the new rate takes effect.  Each row also CARRIES its governing
        rate, so a row's displayed rate is provably the rate its interest
        accrued at (plan step E1c's ruling Q3) -- which is exactly what makes
        the keying visible here rather than silent.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.add(RateHistory(
                account_id=loan.id, effective_date=date(2026, 2, 1),
                interest_rate=Decimal("0.09"),
            ))
            db.session.commit()
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle(seed_user, loan, period)
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("750.00"),
                 Decimal("250.00"), Decimal("99750.00")),
                (date(2026, 3, 1), Decimal("748.13"),
                 Decimal("251.87"), Decimal("99498.13")),
                (date(2026, 4, 1), Decimal("746.24"),
                 Decimal("253.76"), Decimal("99244.37")),
            ]
            assert [row.interest_rate for row in view.history_rows] == [
                Decimal("0.09"), Decimal("0.09"), Decimal("0.09"),
            ]

    def test_a_severe_underpayment_grows_the_balance_every_month(
        self, app, db, seed_user, seed_periods,
    ):
        """Two $200 payments against ~$500 interest leave the debt LARGER (D5).

          due 02-01: interest 500.00, principal 200 - 500 = -300.00 ->
            100,300.00
          due 03-01: interest round(100300 x 0.005) = 501.50, principal
            -301.50 -> 100,601.50

        Delinquency reads honestly: the balance rises, the rows say why, and
        nothing is clamped to zero.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period, Decimal("200.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("-300.00"), Decimal("100300.00")),
                (date(2026, 3, 1), Decimal("501.50"),
                 Decimal("-301.50"), Decimal("100601.50")),
            ]
            assert view.balance == Decimal("100601.50")

    def test_a_late_settled_payment_is_dated_at_its_installment_but_visible_late(
        self, app, db, seed_user, seed_periods,
    ):
        """The split keys on the DUE date; visibility keys on the SETTLED date.

        Both payments satisfy the SAME 2026-02-01 installment (periods 1 and 2
        start 01-16 and 01-30, and payment_day 1 makes both due 02-01), so since
        plan step X-au-g-2c-3b-2 that month is charged ONCE and the walk runs
        them in pay-period order:

          charge 02-01: interest round(100,000.00 x 0.005) = 500.00
          first (period 1): clears it -> principal 500.00 -> 99,500.00
          second (period 2): nothing stands -> principal 1,000.00 -> 98,500.00

        Period 1's payment is settled LATE, on 2026-02-13; period 2's settles on
        its own 01-30 start.  From 02-13 both are visible and the balance is
        98,500.00, with the late payment still dated at the 02-01 installment
        it paid.

        **Read on 2026-02-01, the pass has seen only the SECOND payment, and it
        replays the facts it has seen** (plan step recurrence:R16-c-1, ruling
        **R-R91**, developer 2026-09-19: "a pass's stream holds the facts
        visible by its as_of ... projections follow").  With no other payment
        in the stream, the second OPENS the accrual period: it clears the
        500.00 charge and pays 500.00 of principal, 99,500.00 -- what the
        ledger itself showed on 02-01, before the late payment's settlement
        on 02-13 re-split it.  *Until that ruling the mid read pinned 0.00 /
        1,000.00 / 99,000.00*: the split each payment carried was a fact of
        the WHOLE walk (the first payment ahead of the second, though it had
        not settled), and the view re-accumulated the visible subset of those
        splits -- a figure no ledger displayed on any day.  The old values are
        recorded here rather than deleted because rule 5 forbids moving a
        pinned value without the developer's ruling, and this is the ruling.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[1], settled_on=date(2026, 2, 13),
            )
            _settle(seed_user, loan, seed_periods[2])
            db.session.commit()

            mid = _view(loan, seed_user, date(2026, 2, 1))
            assert _economics(mid) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
            ]
            assert mid.balance == Decimal("99500.00")

            after = _view(loan, seed_user, date(2026, 2, 13))
            assert _economics(after) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 2, 1), Decimal("0.00"),
                 Decimal("1000.00"), Decimal("98500.00")),
            ]
            assert after.balance == Decimal("98500.00")

    def test_two_payments_in_one_due_month_show_as_two_rows_in_that_month(
        self, app, db, seed_user, seed_periods,
    ):
        """A biweekly collision keeps BOTH rows at the true due date, and the
        month is charged ONCE.

        Periods 3 (02-13..) and 4 (02-27..) both start in February, so payments
        budgeted to them satisfy the SAME 2026-03-01 installment.  The confirmed
        view keeps the true due date -- two rows in one month, more truthful --
        where the resolver's DISPLAY redistribution shifts the second to March.

        **Since plan step X-au-g-2c-3b-2 the second payment clears no fresh
        charge** (developer-ruled 2026-09-02: interest is the price of time, not
        of a transaction), so it pays pure principal:

          charge 03-01: interest round(100,000.00 x 0.005) = 500.00
          first: interest 500.00, principal 500.00 -> 99,500.00
          second: interest 0.00, principal 1,000.00 -> 98,500.00

        Per PAYMENT -- the rule this replaced -- the second accrued
        round(99,500.00 x 0.005) = 497.50 and paid only 502.50 down, leaving
        98,997.50.  The owner is 497.50 better off, and that difference is the
        whole of what this step moves.

        The true-up is dated 2026-02-10, after the 02-01 installment no payment
        here pays: every contractual installment is charged since plan step
        recurrence:R16-c-2, so the SPLIT_LOAN true-up's 2026-01-10 would leave
        February standing for the first March payment to clear (the fixture is
        restated so nothing is left unpaid, ruling R-R103).
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 10))
            _settle(seed_user, loan, seed_periods[3])
            _settle(seed_user, loan, seed_periods[4])
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 3, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("0.00"),
                 Decimal("1000.00"), Decimal("98500.00")),
            ]

    def test_a_pre_anchor_payment_accrues_on_origination_then_is_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due BEFORE the latest anchor is subsumed by that anchor's reset.

        The true-up is dated 2026-03-15; the payment is due 2026-02-01, so the
        walk splits it on the ORIGINATION balance and the true-up then resets:

          due 02-01 on $250,000: interest round(250000 x 0.005) = 1250.00,
            principal 2000 - 1250 = 750.00 -> 249,250.00
          true-up 03-15: resets to $100,000 (emits no row)

        So the row keeps its real economics -- from-origination history the
        resolver's deleted replay hid, because it started at the latest anchor
        -- while the view's balance is the asserted 100,000.00.  Its actual P&I
        (2000.00) exceeds the contractual 1498.88, so extra = 501.12.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 3, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle(seed_user, loan, seed_periods[_P1], Decimal("2000.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("1250.00"),
                 Decimal("750.00"), Decimal("249250.00")),
            ]
            (row,) = view.history_rows
            assert row.extra_payment == Decimal("501.12")
            assert row.payment == Decimal("1498.88")
            assert view.balance == Decimal("100000.00")

    def test_a_tracking_start_import_holds_the_opening_flat_then_resets(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-life import opens at ORIGINATION and resets at its tracking start.

        The ledger opens at origination (plan step C1), so the window between
        origination and the tracking start reads the opening principal held FLAT
        -- the honest pre-tracking plateau (finding B-11), never a false $0.00 --
        and the tracking-start assertion resets the balance at its own date like
        any true-up:

          2025-06-01 (pre-tracking): 250,000.00, no rows
          after the 2026-01-10 assertion, due 02-01: interest 500.00, principal
            500.00 -> 99,500.00
          due 03-01: interest 497.50, principal 502.50 -> 98,997.50
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Import View Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                _loan_params(loan), _ANCHOR_BALANCE, _ANCHOR_DATE,
            )
            db.session.commit()
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period)
            db.session.commit()

            plateau = _view(loan, seed_user, date(2025, 6, 1))
            assert plateau.balance == _ORIGINATION_PRINCIPAL
            assert plateau.history_rows == []

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("497.50"),
                 Decimal("502.50"), Decimal("98997.50")),
            ]
            assert view.balance == Decimal("98997.50")

    def test_a_payoff_and_a_payment_after_it_both_read_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Cash arriving after payoff is a Refund, not a negative balance.

        Trued to $1,000, two $1,500 payments:

          due 02-01: interest round(1000 x 0.005) = 5.00, principal capped at
            the 1000.00 that closes the loan (the 495.00 surplus is a Refund),
            balance 0.00; actual P&I 1005.00 is under the contractual 1498.88,
            so extra 0.00 and payment 1005.00
          due 03-01: interest round(0 x 0.005) = 0.00, principal 0.00 (the whole
            $1,500 is Refund), balance still 0.00

        The balance never goes negative and the loan does not resurrect.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("1000.00"))
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period, Decimal("1500.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("5.00"),
                 Decimal("1000.00"), Decimal("0.00")),
                (date(2026, 3, 1), Decimal("0.00"),
                 Decimal("0.00"), Decimal("0.00")),
            ]
            assert [
                (row.payment, row.extra_payment) for row in view.history_rows
            ] == [
                (Decimal("1005.00"), Decimal("0.00")),
                (Decimal("0.00"), Decimal("0.00")),
            ]
            assert view.balance == Decimal("0.00")

    def test_rows_are_numbered_continuously_from_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """A row's ``month`` counts installments from ORIGINATION, not from today.

        Origination 2025-01-01 makes the first contractual installment
        2025-02-01 payment 1, so 2026-02-01 is payment **13** and 2026-03-01 is
        payment 14.  Numbering from origination is what lets a mid-life loan's
        table read "payment 13 of 360" rather than restarting at 1 wherever the
        record happens to begin.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period)
            db.session.commit()

            assert [
                row.month for row in _view(loan, seed_user).history_rows
            ] == [13, 14]
