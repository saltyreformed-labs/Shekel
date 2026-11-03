"""
Shekel Budget App -- a definition's price on the Recurring and savings pages is
TODAY's, by the rule that prices its rows (plan step X-bp, ruling plan#2693).

``obligations_aggregator.todays_price`` is the one producer of what a recurring
definition costs on the read pass's day, and every figure those two pages show
for a definition reads it: the Recurring page's Amount and monthly columns,
the Archived drawer's Amount, the emergency-fund floor and each goal's monthly
contribution.  Until X-bp every definition but a salary one was read off the
stored ``default_amount`` -- the NEWEST price the owner had stated -- so a rise
scheduled for later counted on both pages from the day it was entered, while
the grid rightly kept the old price until the rise's day.

**Every case is one figure on both sides of a scheduled rise**, read through
the producer that publishes it, with the precondition that the series' newest
version is the risen price -- the figure the column held and every one of
these readers answered.  A case that read only the day after the rise would
pass against the column.

The loan cases are the two arms of amount rule 4, on a ``$12,000.00`` loan at
5% over 24 months: a MANUAL payment counts its standing extra, because that is
what leaves checking; a payment that TRACKS the loan follows the contract.

Today is frozen inside ``seed_periods_52``'s schedule so the dates do not
drift with the calendar, and every pass is pinned to a day the case names.
"""
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.savings_goal import SavingsGoal
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    obligations_aggregator,
    recurring_view,
    savings_dashboard_service,
    template_amount_service,
)
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    create_savings_account,
    freeze_today,
    make_cadence_rule,
    make_loan_payment_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY

#: The frozen "today", inside ``seed_periods_52``'s schedule (2026-01-02 on).
_TODAY = date(2026, 7, 1)
#: The day a scheduled rise takes effect: a month after today.
_RISE = date(2026, 8, 1)
#: The day the opening price was stated: well before today.
_OPENED = date(2026, 1, 2)

#: The subscription shape the ruling was argued on: $15.49 a month, with
#: $17.99 entered for a later day.
_OLD_PRICE = Decimal("15.49")
_NEW_PRICE = Decimal("17.99")

#: The loan every loan case pays: $12,000.00 at 5% over 24 months, its first
#: installment 2026-08-01.  Its level payment, by hand: r = 0.05 / 12;
#: 12000 * r / (1 - (1 + r) ** -24) = 50 / (1 - 1/1.1049413) = 526.4569,
#: rounded to the cent.
_CONTRACT = Decimal("526.46")


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today so the series dates and the loan do not drift."""
    freeze_today(monkeypatch, _TODAY)


def _scheduled(template, old: Decimal, new: Decimal) -> None:
    """Give *template* a price of *old* from :data:`_OPENED`, rising to *new* on :data:`_RISE`.

    Both versions go through the one write door, as the edit form states a
    scheduled change, and the precondition every case rests on is asserted
    here: the newest stated price -- what ``default_amount`` held and what the
    retired readers answered -- is the RISEN one.

    Args:
        template: The flushed definition.
        old: The price in effect today.
        new: The price from :data:`_RISE`.
    """
    state_template_price(template, old, effective_on=_OPENED)
    state_template_price(template, new, effective_on=_RISE)
    db.session.flush()
    versions = template_amount_service.amount_versions(template)
    assert versions[-1].amount == new, (
        "precondition: the newest stated price is the scheduled rise"
    )


def _subscription(seed_user, *, is_active=True) -> TransactionTemplate:
    """Return a monthly expense on checking: $15.49, rising to $17.99 on Aug 1.

    Args:
        seed_user: The owner.
        is_active: ``False`` for the Archived drawer's case.

    Returns:
        The flushed template, its cadence authored.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Streaming",
        is_active=is_active,
    )
    db.session.add(template)
    db.session.flush()
    _scheduled(template, _OLD_PRICE, _NEW_PRICE)
    make_cadence_rule(template, MONTHLY, fires_on_day=15)
    db.session.commit()
    return template


def _pass(seed_user, as_of: date) -> BalanceContext:
    """Return the owner's read pass pinned at *as_of*."""
    return BalanceContext.build(seed_user["user"].id, as_of)


class TestAScheduledPriceCountsFromItsDay:
    """Each figure reads $15.49 before the rise's day and $17.99 from it."""

    def test_the_recurring_rows_amount_and_monthly_figure(
        self, app, seed_user, seed_periods_52,
    ):
        """The Amount column and the monthly column, one price for both.

        A monthly rule commits one occurrence a month, so the monthly figure
        is the price: ``15.49 * 12 / 12 = 15.49`` before Aug 1, and
        ``17.99 * 12 / 12 = 17.99`` from it.  The stored column read
        ``$17.99`` for both from the day the rise was entered.
        """
        with app.app_context():
            template = _subscription(seed_user)

            before = recurring_view.build_view(
                [], [template], [], _pass(seed_user, _TODAY),
            ).expenses.rows[0]
            after = recurring_view.build_view(
                [], [template], [], _pass(seed_user, _RISE),
            ).expenses.rows[0]

            assert before.amount == _OLD_PRICE
            assert before.equivalent.monthly == _OLD_PRICE
            assert after.amount == _NEW_PRICE
            assert after.equivalent.monthly == _NEW_PRICE

    def test_the_archived_drawers_amount(
        self, app, seed_user, seed_periods_52,
    ):
        """The drawer shows the price the active list would, on the same day."""
        with app.app_context():
            template = _subscription(seed_user, is_active=False)

            before = recurring_view.build_archived_rows(
                [template], _pass(seed_user, _TODAY),
            )[0]
            after = recurring_view.build_archived_rows(
                [template], _pass(seed_user, _RISE),
            )[0]

            assert before.amount == _OLD_PRICE
            assert after.amount == _NEW_PRICE

    def test_the_drawer_renders_that_amount_on_the_page(
        self, app, auth_client, seed_user, seed_periods_52,
    ):
        """The page's drawer cell is the row's figure, not the template's column.

        ``templates/list.html`` printed ``t.default_amount`` in both drawer
        cells until plan step X-bp; it prints the row's ``amount`` now.  The
        archived subscription is the page's only definition, so ``$17.99``
        appearing anywhere would be the stored newest price leaking back.
        """
        with app.app_context():
            _subscription(seed_user, is_active=False)

            response = auth_client.get("/templates")

            assert response.status_code == 200
            html = response.data.decode()
            assert "Archived (1)" in html
            assert "$15.49" in html
            assert "$17.99" not in html

    def test_the_emergency_fund_floor(self, app, seed_user, seed_periods_52):
        """The floor counts today's price, not the scheduled one.

        With nothing settled the floor IS the committed total (the higher of
        it and a zero history), and the subscription is the owner's one
        checking definition: ``$15.49`` a month before Aug 1, ``$17.99``
        from it.
        """
        with app.app_context():
            _subscription(seed_user)

            before = savings_dashboard_service.compute_dashboard_data(
                _pass(seed_user, _TODAY),
            )
            after = savings_dashboard_service.compute_dashboard_data(
                _pass(seed_user, _RISE),
            )

            assert before["avg_monthly_expenses"] == _OLD_PRICE
            assert after["avg_monthly_expenses"] == _NEW_PRICE

    def test_a_goals_monthly_contribution(
        self, app, seed_user, seed_periods_52,
    ):
        """A monthly transfer into a goal's account: $100.00, rising to $150.00.

        One occurrence a month, so the contribution is the price: ``$100.00``
        a month before Aug 1, ``$150.00`` from it.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Goal Savings", Decimal("0.00"),
            )
            transfer = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="To Goal",
            )
            db.session.add(transfer)
            db.session.flush()
            _scheduled(transfer, Decimal("100.00"), Decimal("150.00"))
            make_cadence_rule(transfer, MONTHLY, fires_on_day=15)
            db.session.add(SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Rainy Day",
                target_amount=Decimal("6000.00"),
                is_active=True,
            ))
            db.session.commit()

            before = savings_dashboard_service.compute_dashboard_data(
                _pass(seed_user, _TODAY),
            )["goal_data"][0]
            after = savings_dashboard_service.compute_dashboard_data(
                _pass(seed_user, _RISE),
            )["goal_data"][0]

            assert before.monthly_contribution == Decimal("100.00")
            assert after.monthly_contribution == Decimal("150.00")


def _loan(seed_user):
    """Return the $12,000.00 loan at 5% over 24 months, originating today."""
    return create_loan_account(
        seed_user, db.session, name="Car Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.05000"),
        term=24, origination_date=_TODAY, payment_day=1,
    )


class TestALoanPaymentIsPricedByRuleFour:
    """Both arms of amount rule 4 on the Recurring row and in the floor."""

    def test_a_manual_payments_standing_extra_is_counted(
        self, app, seed_user, seed_periods_52,
    ):
        """$500.00 stated plus a $100.00 standing extra: $600.00 leaves checking.

        Rule 4's manual arm is the stated series plus the extra
        (``500.00 + 100.00 = 600.00``), and the monthly rule commits one
        occurrence a month, so the row's Amount, its monthly figure and the
        emergency-fund floor all read ``$600.00``.  The stored column held
        the base alone, ``$500.00``: the extra lives on the settings row.
        """
        with app.app_context():
            loan = _loan(seed_user)
            template = make_loan_payment_template(
                db.session, seed_user, loan, amount="500.00",
                derive_from_loan=False, extra_principal="100.00",
            )
            db.session.commit()
            today = _pass(seed_user, _TODAY)

            row = recurring_view.build_view(
                [], [], [template], today,
            ).transfers.rows[0]

            assert row.amount == Decimal("600.00")
            assert row.equivalent.monthly == Decimal("600.00")
            assert savings_dashboard_service.compute_dashboard_data(
                today,
            )["avg_monthly_expenses"] == Decimal("600.00")

    def test_a_tracking_payment_follows_the_contract(
        self, app, seed_user, seed_periods_52,
    ):
        """A payment that tracks the loan is what the loan says, escrow and all.

        Its stored snapshot is ``$200.00`` (what the fixture created it with),
        and the column held that until the next Track.  Rule 4's derive arm
        prices today's installment instead: the level payment,
        :data:`_CONTRACT`.  An escrow line of ``$1,200.00`` a year then adds
        ``1200 / 12 = 100.00`` a month, so the same definition reads
        ``526.46 + 100.00 = 626.46`` on the next read with nothing re-saved.
        """
        with app.app_context():
            loan = _loan(seed_user)
            template = make_loan_payment_template(
                db.session, seed_user, loan, amount="200.00",
            )
            db.session.commit()

            price = obligations_aggregator.todays_price(
                template, _pass(seed_user, _TODAY),
            )
            assert price.amount == _CONTRACT
            assert obligations_aggregator.template_monthly_or_none(
                template, _pass(seed_user, _TODAY),
            ) == _CONTRACT

            add_escrow_line(
                db.session, loan.id, "Insurance", Decimal("1200.00"),
            )
            db.session.commit()

            row = recurring_view.build_view(
                [], [], [template], _pass(seed_user, _TODAY),
            ).transfers.rows[0]
            assert row.amount == Decimal("626.46")
            assert row.equivalent.monthly == Decimal("626.46")
