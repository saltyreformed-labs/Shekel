"""The month card past the last saved paycheck (plan step C9, task plan#3945; ledger row N-394).

Past the owner's last saved paycheck no row exists, so until this task the card
listed nothing there and counted no payday, while its balance line (task
plan#3941) moved by every estimated occurrence.  Each estimated occurrence now
sits on the card where its row would, from the same list the balance folds, so
a projected month's entries, totals and balance reconcile.

**The schedule every case reads**, the ``seed_periods`` fixture: ten saved
biweekly paychecks from 2026-01-02, so the saved horizon is 2026-05-21.  The
projected paydays continue at the same cadence: 05-22, 06-05, 06-19, 07-03,
07-17 and 07-31.  No row of any definition here is written, so every flow on
the card past the horizon is an estimate and none before it is.  The service
suite pins today at 2026-03-20 (``tests/test_services/conftest.py``), inside the
saved schedule.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.services import calendar_service
from app.services.balance_at import BalanceContext, cash_balance_at
from app.utils.dates import display_today
from tests._test_helpers import (
    bare_expense_template,
    create_account_of_type,
    create_savings_account,
    make_cadence_rule,
    make_income_template,
    make_transfer_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY, QUARTERLY


def _paycheck_rent_and_savings(seed_user):
    """Author a $2,000.00 paycheck, a $200.00 savings transfer each payday, and rent.

    The rent is $1,500.00 on the 1st of each month.

    Args:
        seed_user: The seed owner.
    """
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("0.00"),
    )
    make_income_template(db.session, seed_user, amount="2000.00")
    rent = bare_expense_template(db.session, seed_user, name="Rent")
    state_template_price(rent, Decimal("1500.00"))
    make_cadence_rule(rent, MONTHLY, fires_on_day=1)
    make_transfer_template(db.session, seed_user, savings, amount="200.00")
    db.session.commit()


def _flows(summary):
    """Return ``{day: [(name, amount, is_income), ...]}`` for every day the card lists an entry."""
    return {
        day: [(e.name, e.amount, e.is_income) for e in entries]
        for day, entries in summary.day_entries.items()
    }


class TestAProjectedMonthListsWhatItsBalanceFolds:
    """The card's entries past the horizon are the estimated occurrences."""

    def test_june_lists_each_estimate_and_its_net_is_the_balances_move(
        self, app, seed_user, seed_periods,
    ):
        """June 2026 lies wholly past the horizon, and its card reconciles.

        Hand count: paydays 06-05 and 06-19, each a $2,000.00 paycheck in and
        the $200.00 savings transfer out; rent on 06-01, in the paycheck
        05-22 .. 06-04.  Income 2 x $2,000.00 = $4,000.00; expenses $1,500.00
        + 2 x $200.00 = $1,900.00; net $2,100.00.  Checking's balance moves by
        exactly that from 05-31 to 06-30.  The card listed nothing here and
        read ``$0.00`` before this task, while the balance moved.
        """
        with app.app_context():
            _paycheck_rent_and_savings(seed_user)
            checking = seed_user["account"]

            june = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=6,
                user_settings=None, today=display_today(),
            )

            assert _flows(june) == {
                1: [("Rent", Decimal("1500.00"), False)],
                5: [
                    ("Paycheck", Decimal("2000.00"), True),
                    ("Transfer to Savings", Decimal("200.00"), False),
                ],
                19: [
                    ("Paycheck", Decimal("2000.00"), True),
                    ("Transfer to Savings", Decimal("200.00"), False),
                ],
            }
            assert june.paycheck_days == [5, 19]
            assert june.total_income == Decimal("4000.00")
            assert june.total_expenses == Decimal("1900.00")
            assert june.net == Decimal("2100.00")
            assert not any(
                entry.is_paid
                for entries in june.day_entries.values() for entry in entries
            )
            ctx = BalanceContext.build(checking.user_id)
            moved = (
                cash_balance_at(checking, ctx, date(2026, 6, 30))
                - cash_balance_at(checking, ctx, date(2026, 5, 31))
            )
            assert moved == june.net
            assert june.daily.daily_balances[30] - june.daily.daily_balances[1] == (
                june.net - june.day_totals[1][0] + june.day_totals[1][1]
            )

    def test_the_month_holding_the_horizon_lists_only_the_projected_paycheck(
        self, app, seed_user, seed_periods,
    ):
        """May 2026 straddles the horizon: its saved paycheck has no row and no estimate.

        The saved paycheck 05-08 holds no row, and an estimate fills only a
        paycheck past the last saved one (ruling plan#3932), so 05-08 and
        the 05-01 rent list nothing.  The projected paycheck 05-22 lists the
        $2,000.00 paycheck and the $200.00 transfer.  Both paydays are
        marked: the card holds 05-08's rows, here none, and 05-22's estimates.
        """
        with app.app_context():
            _paycheck_rent_and_savings(seed_user)

            may = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=5,
                user_settings=None,
            )

            assert _flows(may) == {
                22: [
                    ("Paycheck", Decimal("2000.00"), True),
                    ("Transfer to Savings", Decimal("200.00"), False),
                ],
            }
            assert may.paycheck_days == [8, 22]
            assert may.net == Decimal("1800.00")

    def test_the_year_overview_flags_a_projected_three_paycheck_month(
        self, app, seed_user, seed_periods,
    ):
        """July 2026's three projected paychecks are counted and their money is on the card.

        Paydays 07-03, 07-17 and 07-31: income 3 x $2,000.00 = $6,000.00;
        expenses rent $1,500.00 on 07-01 + 3 x $200.00 = $2,100.00; net
        $3,900.00.  The saved count flagged no projected month before this
        task.
        """
        with app.app_context():
            _paycheck_rent_and_savings(seed_user)

            overview = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2026, user_settings=None,
            )

            july = overview.months[6]
            assert july.is_third_paycheck_month is True
            assert july.paycheck_days == [3, 17, 31]
            assert july.total_income == Decimal("6000.00")
            assert july.total_expenses == Decimal("2100.00")
            assert july.net == Decimal("3900.00")


class TestTheSetsMembersPastTheHorizon:
    """A card member's estimates are on checking's card, each transfer once."""

    def test_a_card_payment_shows_once_and_the_cards_bill_shows_on_its_day(
        self, app, seed_user, seed_periods,
    ):
        """A $100.00 checking -> card payment each payday, and a $50.00 card bill on the 10th.

        Both endpoints of the payment are members of the set, so it is drawn
        once, from checking's side, the balance line's (ruling R-CC23): an
        expense "Transfer to Rewards Card" on 06-05 and 06-19.  The card's own
        income side of it is not drawn.  The phone bill lives on the card and
        is listed on 06-10.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("0.00"),
            )
            make_transfer_template(db.session, seed_user, card, amount="100.00")
            phone = bare_expense_template(
                db.session, seed_user, name="Phone", account=card,
            )
            state_template_price(phone, Decimal("50.00"))
            make_cadence_rule(phone, MONTHLY, fires_on_day=10)
            db.session.commit()

            june = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=6,
                user_settings=None,
            )

            assert _flows(june) == {
                5: [("Transfer to Rewards Card", Decimal("100.00"), False)],
                10: [("Phone", Decimal("50.00"), False)],
                19: [("Transfer to Rewards Card", Decimal("100.00"), False)],
            }
            assert june.total_expenses == Decimal("250.00")


class TestAnEstimateIsBadgedAsItsRowWouldBe:
    """The infrequent badge reads an estimate's definition."""

    def test_a_quarterly_bill_past_the_horizon_is_infrequent(
        self, app, seed_user, seed_periods,
    ):
        """A $300.00 bill every three months from 2026-03-15, estimated on 2026-06-15.

        The 03-15 occurrence sits in a saved paycheck and is not estimated;
        06-15 falls in the projected paycheck 06-05 .. 06-18.  Quarterly
        fires 4 times a year, fewer than 12, so its row would carry
        the badge.  June holds no row at all, so the build must still resolve
        the owner's cadence for the estimate: with nothing repeating among
        the rows it resolved none before this task.
        """
        with app.app_context():
            insurance = bare_expense_template(
                db.session, seed_user, name="Insurance",
            )
            state_template_price(insurance, Decimal("300.00"))
            make_cadence_rule(insurance, QUARTERLY, starts_on=date(2026, 3, 15))
            db.session.commit()

            june = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=6,
                user_settings=None,
            )

            assert _flows(june) == {
                15: [("Insurance", Decimal("300.00"), False)],
            }
            [entry] = june.day_entries[15]
            assert entry.is_infrequent is True
