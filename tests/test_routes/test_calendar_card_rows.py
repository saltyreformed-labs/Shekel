"""
Shekel Budget App -- the calendar draws a row on the card, chipped, in no total

Ruling plan#3526 (plan task plan#3529): the analytics calendar's day, month and
year figures count what leaves the balance account, and a row on another
member of the set is drawn with that account's chip, the one the grid and the
dashboard draw (``grid/_grid_row_macros.account_chip``).  The service-level
figures are :mod:`tests.test_services.test_the_calendar_counts_a_card_purchase_once`;
this grades what the page renders.

January 2026 is ``seed_periods``' first paycheck's, and the day is held at
2026-03-20, so January is wholly past: its So far chip is the full month.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.extensions import db
from tests._test_helpers import add_txn, create_account_of_type, freeze_today

_CHIP = (
    '<span class="flag-chip flag-chip--neutral ms-1" '
    'title="On Rewards Card">Rewards Card</span>'
)


def _january(seed_user, seed_periods):
    """Rent $300.00 on checking Jan 6 and the phone $45.00 on the card Jan 7, projected."""
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Rewards Card",
        anchor_balance=Decimal("-500.00"),
    )
    add_txn(
        db.session, seed_user, seed_periods[0], "Rent", Decimal("300.00"),
        due_date=date(2026, 1, 6),
    )
    add_txn(
        db.session, seed_user, seed_periods[0], "Phone", Decimal("45.00"),
        due_date=date(2026, 1, 7), account=card,
    )
    db.session.commit()


def test_the_month_draws_the_cards_row_chipped_and_counts_only_the_checking_rows(
    app, auth_client, seed_user, seed_periods, monkeypatch,
):
    """The So far chip reads Expenses $300.00, never $345.00; Phone carries the chip twice.

    Once on its Jan 7 day cell, between its name and its amount, and once in
    that day's detail table; Rent, on checking, carries none.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
    with app.app_context():
        _january(seed_user, seed_periods)

        resp = auth_client.get(
            "/analytics/calendar?view=month&year=2026&month=1",
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        html = resp.data.decode()
        # So far, the month's first chip pair: Income 0.00, Expenses 300.00.
        assert re.findall(
            r'chip-pair-label">Expenses</span>\s*<span class="calendar-day-expense">([^<]+)<',
            html,
        ) == ["$300.00"]
        assert "$345.00" not in html
        assert html.count(_CHIP) == 2
        cell_name = html.index("Phone\n        </span>")
        assert cell_name < html.index(_CHIP) < html.index("$45", cell_name)
        detail = html.index('<template data-detail-day="7">')
        assert html.index("Phone" + _CHIP, detail) < html.index("$45.00", detail)
        assert "Rent" + _CHIP not in html


def test_the_year_counts_only_the_checking_rows(
    app, auth_client, seed_user, seed_periods, monkeypatch,
):
    """January's card and the year's Annual Expenses read $300, the phone in neither."""
    freeze_today(monkeypatch, date(2026, 3, 20))
    with app.app_context():
        _january(seed_user, seed_periods)

        resp = auth_client.get(
            "/analytics/calendar?view=year&year=2026",
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        html = resp.data.decode()
        # January's card (whole dollars) and the year's line, Rent's 300 alone;
        # the other eleven months' cards each read $0.
        expenses = re.findall(r'calendar-day-expense">(\$[\d,.]+)<', html)
        assert [e for e in expenses if e != "$0"] == ["$300", "$300.00"]
        assert len(expenses) == 13
        assert "$345" not in html


def test_the_calendar_spells_no_chip_of_its_own(app):
    """Source census: the month view draws the grid's one chip macro, never a copy.

    ``test_cc5_2_purchase_picker``'s census holds the markup to the grid's
    macro file; this holds the calendar's template to importing it, for the
    day cell and the day-detail table.
    """
    month = (
        Path(app.root_path) / "templates/analytics/_calendar_month.html"
    ).read_text(encoding="utf-8")
    assert "flag-chip--neutral" not in month
    assert '{% from "grid/_grid_row_macros.html" import account_chip %}' in month
    assert month.count("account_chip(entry.other_member.name)") == 2
