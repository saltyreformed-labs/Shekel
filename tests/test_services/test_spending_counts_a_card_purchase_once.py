"""
Shekel Budget App -- the Spending report counts a purchase on the card once

Plan task plan#2932, developer ruling plan#2926 (2026-11-05): "the Spending
report counts a purchase once, as what was bought".  A purchase on the card is a
settled row on the card, a member of the owner's cash-flow set, so the report
reads it at what was bought and in its own category; the card's payment out of
checking pays it off, and its expense leg is not spending.  Plan#2890's review
measured the report counting both, ``$1,600`` for ``$800`` of groceries (its R8);
today's Credit-and-payback rows read ``$800`` as "Credit Card: Payback" (its R9).

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16 and Jan 30 2026
onward.  Every window is January 2026, which holds both paychecks' rows by the
COALESCE(due_date, period start) attribution rule.
"""

from datetime import date
from decimal import Decimal

from app.services.balance_at import BalanceContext
from app.services.spending_report_service import (
    SpendingWindow,
    compute_spending_report,
)
from tests._cards import add_card
from tests._test_helpers import create_savings_account, create_settled_transfer
from tests.test_services.test_spending_report_service import _txn

_JANUARY = SpendingWindow(window_type="month", month=1, year=2026)
_JAN_3 = date(2026, 1, 3)
_JAN_16 = date(2026, 1, 16)


def _bought_and_paid_off(db, seed_user, seed_periods):
    """$800 of groceries on the card Jan 3, and the $800 payment that paid it off Jan 16.

    The purchase is a Paid row on the card in the Jan 2 paycheck; the payment
    a settled checking -> card transfer in the Jan 16 paycheck, due and paid
    Jan 16, so it is a timed item the payment timing could count.

    Returns:
        The card.
    """
    card = add_card(seed_user)
    _txn(
        db, seed_user, seed_periods[0], "Groceries", "Groceries", "800.00",
        account=card, due_date=_JAN_3, settled_on=_JAN_3,
    )
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], card, seed_periods[1],
        amount=Decimal("800.00"), name="Pay the Visa",
        due_date=_JAN_16, settled_on=_JAN_16,
    )
    db.session.commit()
    return card


def _report(seed_user):
    """January 2026's report, off a fresh read pass."""
    return compute_spending_report(
        BalanceContext.build(seed_user["user"].id), _JANUARY, user_settings=None,
    )


class TestAPurchaseOnTheCardCountsOnce:
    """What was bought counts; the payment into the card does not."""

    def test_the_groceries_count_and_the_payment_does_not(
        self, app, db, seed_user, seed_periods,
    ):
        """$800 of groceries and the $800 payment: $800 spent, all of it Groceries.

        Counting the payment too read 800.00 + 800.00 = 1,600.00.
        """
        with app.app_context():
            _bought_and_paid_off(db, seed_user, seed_periods)

            report = _report(seed_user)

            assert report.hero.spent_total == Decimal("800.00")
            assert [
                (group.group_name, [(i.item_name, i.amount) for i in group.items])
                for group in report.breakdown
            ] == [("Family", [("Groceries", Decimal("800.00"))])]

    def test_the_payment_timing_counts_the_purchase_as_its_bill(
        self, app, db, seed_user, seed_periods,
    ):
        """One timed bill, the purchase: the payment is not a spending item.

        Both items carry a due date and a settle day; with the payment counted
        the chip read two bills paid.
        """
        with app.app_context():
            _bought_and_paid_off(db, seed_user, seed_periods)

            timing = _report(seed_user).hero.payment_timing

            assert timing["total_bills_paid"] == 1
            assert timing["paid_on_time"] == 1


class TestATransferToAnotherAccountReadsAsItDid:
    """Only a payment INTO A CARD leaves the report: the far side decides."""

    def test_a_transfer_to_savings_is_still_spending(
        self, app, db, seed_user, seed_periods,
    ):
        """$200 to savings beside the card's shape: 800.00 + 200.00 = 1,000.00.

        The savings transfer's far side is not revolving, so it reads as it
        did before plan task plan#2932; the card payment beside it does not.
        """
        with app.app_context():
            _bought_and_paid_off(db, seed_user, seed_periods)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1], amount=Decimal("200.00"), name="To savings",
            )
            db.session.commit()

            assert _report(seed_user).hero.spent_total == Decimal("1000.00")

    def test_a_transfer_to_savings_alone_reads_its_whole_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """No card at all: the $200 to savings is $200 spent, as before."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1], amount=Decimal("200.00"), name="To savings",
            )
            db.session.commit()

            assert _report(seed_user).hero.spent_total == Decimal("200.00")
