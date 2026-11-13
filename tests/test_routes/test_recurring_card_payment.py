"""
Shekel Budget App -- the Recurring page lists a payment into a card outside its totals

Ruling plan#3527 (plan task plan#3530): a transfer definition into a card is
listed with its Amount and monthly figure and says it is not in the totals,
whose Transfers figure leaves it out.  The service-level figures are
:mod:`tests.test_services.test_the_recurring_page_counts_a_card_bill_once`;
this grades what the page renders.
"""

import re
from decimal import Decimal

import pytest

from app.extensions import db
from tests._test_helpers import create_account_of_type, create_savings_account
from tests.oracles.recurrence_baseline import MONTHLY
from tests.test_services.test_the_recurring_page_counts_a_card_bill_once import (
    _plain_transfer,
)

_NOTE = "Not in the totals: it pays the card's bills, which the totals count"


@pytest.mark.usefixtures("seed_periods_today")
def test_the_card_payment_says_it_is_not_in_the_totals(auth_client, seed_user):
    """$500 a month into the card and $75 a month to savings: Transfers reads $75.00.

    The card's row shows $500.00 and then the note, the page's only one, so
    the savings row carries none.
    """
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Rewards Card",
        anchor_balance=Decimal("-500.00"),
    )
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("500.00"),
    )
    _plain_transfer(seed_user, card, "500.00", MONTHLY, "Extra to the card", fires_on_day=15)
    _plain_transfer(seed_user, savings, "75.00", MONTHLY, "To savings", fires_on_day=15)

    html = auth_client.get("/templates").data.decode()

    banner = re.search(
        r'section-banner__title">Transfers</h6>\s*<span class="font-mono fw-bold">([^<]+)<',
        html,
    )
    assert banner.group(1) == "$75.00"
    assert html.count(_NOTE) == 1
    card_row = html.index("<td>Extra to the card</td>")
    assert card_row < html.index("$500.00", card_row) < html.index(_NOTE, card_row)
