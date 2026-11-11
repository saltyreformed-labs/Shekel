"""
Shekel Budget App -- the dashboard draws a bill on the card, chipped, outside "still due"

Ruling plan#3524 (plan task plan#3528): the due-soon street lists a bill on
the card with the card's account chip, the one the grid draws
(``grid/_grid_row_macros.account_chip``), and the still-due figures count only what
leaves the balance account.  The service-level figures are
:mod:`tests.test_services.test_the_dashboard_counts_a_card_purchase_once`;
this grades what the page renders.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app import ref_cache
from app.enums import TxnTypeEnum
from tests._test_helpers import (
    create_account_of_type,
    current_pay_period,
    one_off_row_of,
)


def _expense_on(seed_user, period, account, name, amount, due_date):
    """Place a projected one-off EXPENSE row on *account* in *period*."""
    return one_off_row_of(
        period, name=name, amount=Decimal(amount),
        user_id=seed_user["user"].id, account_id=account.id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        due_date=due_date,
    )


def test_a_bill_on_the_card_is_drawn_with_its_chip_and_left_out_of_still_due(
    app, auth_client, seed_user, seed_periods_today, db,
):  # pylint: disable=unused-argument
    """Rent $300.00 on checking and the phone $45.00 on the card, both this paycheck.

    The page states $300.00 still due and never $345.00, and the phone's
    station carries the card's chip, the rent's none.
    """
    with app.app_context():
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )
        period = current_pay_period(seed_user["user"].id)
        _expense_on(
            seed_user, period, seed_user["account"], "Rent", "300.00",
            period.start_date + timedelta(days=1),
        )
        _expense_on(
            seed_user, period, card, "Phone", "45.00",
            period.start_date + timedelta(days=2),
        )
        db.session.commit()

        resp = auth_client.get("/dashboard")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$300.00" in html
        assert "$345.00" not in html
        chip = (
            '<span class="flag-chip flag-chip--neutral ms-1" '
            'title="On Rewards Card">Rewards Card</span>'
        )
        assert html.count(chip) == 1
        phone_at = html.index('<span class="street__station-name">Phone</span>')
        assert html.index(chip) > phone_at
        assert html.index(chip) < html.index("$45.00", phone_at)


def test_the_dashboard_spells_no_chip_of_its_own(app):
    """Source census: the street draws the grid's one chip macro, never a copy.

    ``test_cc5_2_purchase_picker``'s census holds the markup to the grid's
    macro file; this holds the dashboard's template to importing it.
    """
    pulse = (
        Path(app.root_path) / "templates/dashboard/_pulse.html"
    ).read_text(encoding="utf-8")
    assert "flag-chip--neutral" not in pulse
    assert '{% from "grid/_grid_row_macros.html" import account_chip %}' in pulse
    assert pulse.count("account_chip(ev.on_account)") == 2
