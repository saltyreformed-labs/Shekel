"""Each confirmed row carries the payment's actual economics, computed by hand."""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import transfer_service
from tests._test_helpers import create_settled_transfer, insert_trueup_event

from ._helpers import _P1, _P2, _P3, _loan_params, _make_loan, _settle, _view, _economics


# The loan's contractual P&I (see the module docstring's arithmetic): the
# threshold each row's actual P&I is split against into payment + extra.
_CONTRACTUAL_PI = Decimal("1498.88")


class TestConfirmedViewRowEconomics:
    """Each row carries the payment's ACTUAL economics, hand-computed.

    These pins moved here from the deleted posting reader's suite (plan step
    E1d-b): the row shaping is now emitted on the WALK path, and its independent
    value proof has to live where the production path is.  Every expected figure
    is derived in its docstring from the loan's terms alone.
    """

    def test_on_schedule_rows_match_the_contractual_amortization(
        self, app, db, seed_user, seed_periods,
    ):
        """Two exactly-contractual payments amortize the $100,000 anchor.

        Two settled payments of exactly the contractual P&I (1498.88):

          row 1 (due 02-01): interest round(100000 x 0.005) = 500.00,
            principal 1498.88 - 500.00 = 998.88, balance 99,001.12
          row 2 (due 03-01): interest round(99001.12 x 0.005)
            = round(495.0056) = 495.01, principal 1003.87, balance 97,997.25

        Neither exceeds the contractual payment, so ``extra_payment`` is 0.00 and
        ``payment`` is the full 1498.88 on both rows.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(seed_user, loan, period, _CONTRACTUAL_PI)
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("998.88"), Decimal("99001.12")),
                (date(2026, 3, 1), Decimal("495.01"),
                 Decimal("1003.87"), Decimal("97997.25")),
            ]
            assert [
                (row.payment, row.extra_payment) for row in view.history_rows
            ] == [
                (_CONTRACTUAL_PI, Decimal("0.00")),
                (_CONTRACTUAL_PI, Decimal("0.00")),
            ]
            assert all(row.is_confirmed for row in view.history_rows)
            # *A compatibility pin compared these rows, field by field, with the
            # resolver's contractual replay on this on-schedule shape.  It went
            # with the replay at plan step recurrence:R16-e (ruling plan#3382);
            # the hand-worked figures above are what grade the rows.*

    def test_extra_payment_row_shows_the_actual_split_and_extra(
        self, app, db, seed_user, seed_periods,
    ):
        """An off-schedule extra payment's row carries its real economics.

        A $2,000 payment on the $100,000 balance: interest round(100000 x
        0.005) = 500.00, principal 2000 - 500 = 1500.00, balance 98,500.00.
        Against the 1498.88 contractual P&I the actual P&I of 2000.00 carries
        extra 2000.00 - 1498.88 = 501.12, leaving payment 1498.88 -- the
        schedule-row invariant ``principal + interest == payment + extra``
        (1500.00 + 500.00 == 1498.88 + 501.12), the same algebra a projected row
        with extra uses, so the table's totals add up unchanged.  A contractual
        replay would show only the scheduled 998.88 principal; this row shows
        what the cash actually did.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("2000.00"))
            db.session.commit()

            (row,) = _view(loan, seed_user).history_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("1500.00")
            assert row.extra_payment == Decimal("501.12")
            assert row.payment == Decimal("1498.88")
            assert row.remaining_balance == Decimal("98500.00")
            assert row.is_confirmed is True

    def test_short_payment_row_shows_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An underpayment's row surfaces the real negative principal.

        A $400 payment against 500.00 accrued interest: principal
        400 - 500 = -100.00 (the balance GROWS to 100,100.00), payment 400.00
        (the actual P&I, under contractual so extra is 0.00).  Surfaced, never
        clamped -- the same ruling-D5 honesty the split walk pins.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("400.00"))
            db.session.commit()

            (row,) = _view(loan, seed_user).history_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("-100.00")
            assert row.extra_payment == Decimal("0.00")
            assert row.payment == Decimal("400.00")
            assert row.remaining_balance == Decimal("100100.00")

    def test_payoff_overpayment_row_caps_principal_and_excludes_the_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payoff overpayment's row ends at 0.00 with the refund excluded.

        A $150,000 payment on the $100,000 balance: interest 500.00, principal
        capped at the 100,000.00 that closes the loan, and the 49,500.00 surplus
        booked as a lender Refund -- NOT in the row (it is not P&I).  The actual
        P&I is 100,500.00, so extra = 100500.00 - 1498.88 = 99,001.12 and
        payment stays the contractual-shaped 1498.88 (principal + interest ==
        payment + extra holds: 100500.00 == 1498.88 + 99001.12).  The balance
        reads a clean 0.00, never -0.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("150000.00"))
            db.session.commit()

            view = _view(loan, seed_user)
            (row,) = view.history_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("100000.00")
            assert row.extra_payment == Decimal("99001.12")
            assert row.payment == Decimal("1498.88")
            assert row.remaining_balance == Decimal("0.00")
            assert not row.remaining_balance.is_signed()
            assert view.balance == Decimal("0.00")

    def test_trueup_between_payments_moves_the_next_row_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history true-up resets the balance the NEXT row reads.

        P1 ($1,000, due 02-01) splits 500.00 / 500.00 leaving 99,500.00; a
        true-up then asserts $95,000 on 02-15; P2 ($1,000, due 03-01) accrues on
        the VERIFIED balance: interest round(95000 x 0.005) = 475.00, principal
        525.00, balance 94,475.00.  The true-up itself emits no row (it is not a
        payment) but its reset moves the running balance between the rows -- the
        pre-true-up row keeps its actual interest (the arc's from-origination
        history), and the post-true-up row lands on the asserted trajectory.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            insert_trueup_event(
                _loan_params(loan), Decimal("95000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("475.00"),
                 Decimal("525.00"), Decimal("94475.00")),
            ]
            assert view.balance == Decimal("94475.00")

    def test_trueup_dated_on_a_due_date_applies_after_that_days_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up dated exactly on a due date subsumes that day's payment.

        The walk's tie-break (a payment sorts BEFORE a same-date anchor) must be
        mirrored by the row accumulation or the row would accrue on the wrong
        balance.  P1 leaves 99,500.00; a true-up asserts $95,000 dated exactly on
        P2's 03-01 due date; P2 is walked FIRST -- interest round(99500 x 0.005)
        = 497.50, principal 502.50, row balance 98,997.50 -- and the reset applies
        after it, so the VIEW's balance is the asserted 95,000.00.

        NEGATIVE CONTROL: an anchor-first order would show 475.00 interest on the
        already-reset balance, which is the mutation this kills.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            insert_trueup_event(
                _loan_params(loan), Decimal("95000.00"), date(2026, 3, 1),
            )
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("497.50"),
                 Decimal("502.50"), Decimal("98997.50")),
            ]
            assert view.balance == Decimal("95000.00")

    def test_a_reverted_payment_leaves_no_trace_in_the_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment reverted to Projected is not a settled fact, so it folds nowhere.

        The walk's payment set is the loan's SETTLED payments, so a
        reverted payment simply is not in it -- there is no residue to classify,
        which is the structural version of the drop the posting reader had to
        perform by recognising payment lineage across two entry dates.  The
        confirmed $1,100 payment reads its exact economics: interest 500.00,
        principal 1100 - 500 = 600.00, balance 100000 - 600 = 99,400.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], Decimal("1100.00"))
            reverted = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[2], amount=Decimal("1000.00"),
                settled_on=date(2026, 2, 20),
            )
            db.session.commit()
            transfer_service.update_transfer(
                reverted.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            view = _view(loan, seed_user)
            assert _economics(view) == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("600.00"), Decimal("99400.00")),
            ]
            assert view.balance == Decimal("99400.00")

    def test_the_last_row_balance_is_the_views_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """The rows and the balance are two derivations that must agree.

        Three $1,000 payments on the shrinking real balance:

          due 02-01: interest 500.00, principal 500.00 -> 99,500.00
          due 03-01: interest round(99500 x 0.005) = 497.50, principal
            502.50 -> 98,997.50
          due 04-01: interest round(98997.50 x 0.005) = round(494.9875)
            = 494.99, principal 505.01 -> 98,492.49

        The view's ``balance`` is the PREFIX SUM of the walk's dated deltas, keyed
        by VISIBLE date and rounded once at the end; each row's
        ``remaining_balance`` is a separate re-accumulation in CONTRACT order,
        in the opposite sign convention, rounded per payment.  They are not
        independent oracles -- both read the same walk -- but they are separate
        accumulations with a real cent-level degree of freedom between them, so
        their agreeing is a property rather than a restatement.  The
        hand-computed 98,492.49 is what grades BOTH.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle(seed_user, loan, period)
            db.session.commit()

            view = _view(loan, seed_user)
            assert [row.remaining_balance for row in view.history_rows] == [
                Decimal("99500.00"),
                Decimal("98997.50"),
                Decimal("98492.49"),
            ]
            assert view.balance == Decimal("98492.49")
            assert view.history_rows[-1].remaining_balance == view.balance
