"""
Shekel Budget App -- the Recurring page counts a bill on the card once

Ruling plan#3527 (plan task plan#3530): the Recurring page's totals count a bill
on a card and not a payment into a card.  The card's payment is listed with
its Amount and monthly figure, takes no share and adds nothing to the
Transfers subtotal or the band.  Plan task plan#2932 measured the double count
on 2026-11-11: a $100 phone bill every paycheck on the card, paid off by the
card's payment each payday, read Expenses $216.67, Transfers $216.67 and Net
-$433.34, where $216.67 a month leaves checking.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16, Jan 30 2026 and
fortnightly on, 26 a year, so $100 a paycheck is 100 * 26 / 12 = $216.67 a
month.  The card (``Visa``) is :mod:`tests.test_services.test_card_payment_price`'s,
its books open on Jan 2, so a row dated Jan 2 is inside its opening.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import CardPaymentModeEnum, StatusEnum
from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.services import recurring_view, transfer_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine import generate_for_template
from tests._test_helpers import (
    create_savings_account,
    freeze_today,
    make_cadence_rule,
    make_expense_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from tests.test_services.test_card_payment_price import _card, _row, _works_out

_JAN_20 = date(2026, 1, 20)
_ZERO = Decimal("0.00")


def _phone_on_the_card(seed_user, card):
    """A $100 phone bill every paycheck on *card*, its rows generated and committed."""
    template = make_expense_template(
        db.session, seed_user, "100.00", name="Phone", account=card,
    )
    db.session.commit()
    generate_for_template(
        template,
        GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
        seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _payment_paying_jan_16s_phone(seed_user, card):
    """The card's whole balance each payday, with Jan 30's row the next, $100.00.

    The Jan 2 and Jan 16 rows work out $0.00 (the Jan 2 phone is inside the
    card's opening) and are cancelled, as a $0.00 worked-out row cannot be
    marked Paid, so the definition's price is Jan 30's row: the Jan 16 phone's
    $100 the card owes on Jan 29.
    """
    payment = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
    for due in (date(2026, 1, 2), date(2026, 1, 16)):
        row = _row(payment, due)
        transfer_service.update_transfer(
            row.id, row.user_id,
            status_id=ref_cache.status_id(StatusEnum.CANCELLED),
        )
        db.session.commit()
    return payment


def _plain_transfer(seed_user, to_account, amount, cadence, name, **rule):
    """A recurring transfer of the owner's own *amount* from checking, committed."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal(amount))
    make_cadence_rule(template, cadence, **rule)
    db.session.commit()
    return template


def _view(seed_user, expenses, transfers):
    """The Recurring page's model on Jan 20."""
    return recurring_view.build_view(
        [], expenses, transfers,
        BalanceContext.build(seed_user["user"].id, as_of=_JAN_20),
    )


@pytest.mark.usefixtures("seed_periods")
def test_the_phone_counts_and_the_payment_that_pays_it_off_does_not(seed_user, monkeypatch):
    """Expenses $216.67, Transfers $0.00, Net -$216.67; per paycheck $100, $0, -$100.

    The payment is listed at $100.00 and $216.67 a month, flagged, with no
    share; the phone, on the card, is the whole Expenses section.  It read
    Transfers $216.67 and Net -$433.34 before the ruling.
    """
    freeze_today(monkeypatch, _JAN_20)
    card = _card(seed_user)
    phone = _phone_on_the_card(seed_user, card)
    payment = _payment_paying_jan_16s_phone(seed_user, card)

    view = _view(seed_user, [phone], [payment])

    band = view.band
    assert (band.expenses.monthly, band.transfers_out.monthly, band.net.monthly) == (
        Decimal("216.67"), _ZERO, Decimal("-216.67"),
    )
    assert (band.expenses.per_paycheck, band.transfers_out.per_paycheck,
            band.net.per_paycheck) == (Decimal("100.00"), _ZERO, Decimal("-100.00"))
    [row] = view.transfers.rows
    assert (row.amount, row.equivalent.monthly, row.share_pct, row.pays_a_card) == (
        Decimal("100.00"), Decimal("216.67"), None, True,
    )
    assert view.transfers.subtotal.monthly == _ZERO
    [bill] = view.expenses.rows
    assert (bill.share_pct, bill.pays_a_card) == (Decimal("100.0"), False)


@pytest.mark.usefixtures("seed_periods")
def test_a_transfer_to_savings_still_counts_beside_it(seed_user, monkeypatch):
    """The control: $50 a paycheck to savings is the Transfers section, 50 * 26 / 12 = $108.33.

    It takes the whole share (100.0%) and the card's payment beside it none.
    """
    freeze_today(monkeypatch, _JAN_20)
    card = _card(seed_user)
    phone = _phone_on_the_card(seed_user, card)
    payment = _payment_paying_jan_16s_phone(seed_user, card)
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("500.00"),
    )
    to_savings = _plain_transfer(
        seed_user, savings, "50.00", EVERY_PERIOD, "To savings",
    )

    view = _view(seed_user, [phone], [payment, to_savings])

    assert view.transfers.subtotal.monthly == Decimal("108.33")
    assert view.band.net.monthly == Decimal("-325.00")  # 0 - 216.67 - 108.33
    shares = {r.template.name: (r.share_pct, r.pays_a_card) for r in view.transfers.rows}
    assert shares == {
        "To savings": (Decimal("100.0"), False),
        "Pay the Visa": (None, True),
    }


@pytest.mark.usefixtures("seed_periods")
def test_a_fixed_amount_sent_to_the_card_is_left_out_too(seed_user, monkeypatch):
    """$500 a month on the 15th into the card, a plain transfer: listed at $500.00, in no total.

    The ruling's stated cost: a fixed extra beyond the card's bills stops
    counting as well, as only the bills are counted.
    """
    freeze_today(monkeypatch, _JAN_20)
    card = _card(seed_user)
    fixed = _plain_transfer(
        seed_user, card, "500.00", MONTHLY, "Extra to the Visa", fires_on_day=15,
    )

    view = _view(seed_user, [], [fixed])

    [row] = view.transfers.rows
    assert (row.amount, row.equivalent.monthly, row.pays_a_card) == (
        Decimal("500.00"), Decimal("500.00"), True,
    )
    assert (view.transfers.subtotal.monthly, view.band.net.monthly) == (_ZERO, _ZERO)
