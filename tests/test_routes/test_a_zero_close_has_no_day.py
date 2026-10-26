"""Ruling ``balance:R-BAL230``: a day typed on a ``$0.00``-closed transfer's popover is refused.

Plan step ``balance:X-bi-6-4d-2`` checkpoint 2b (the cp2a review's M1).  A
transfer marked Paid at ``$0.00`` keeps no payment record on either side, so
it has no day (rulings **R-BAL82**, **R-BAL141**), and its full-edit popover
still renders the two day boxes empty.  A day typed into one and saved was
accepted and stored nothing.  The developer's answer (2026-10-08, "Refuse,
say why"): the Save is refused with *"A $0.00 close moved no money, so it has
no day. Type the amount the bank took to date it."*, and nothing changes.

The PATCH posts what the popover RENDERS (every control of its form, read
off the page), with one day box filled in, because a hand-picked payload can
pass a door no browser reaches.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import or_
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import transfer_service
from tests._test_helpers import create_account_of_type, create_transfer, typed
from tests.test_routes._statement_forms import form_fields

_SENTENCE = (
    "A $0.00 close moved no money, so it has no day. Type the amount the "
    "bank took to date it."
)


def _closed_at_zero(seed_user):
    """A $500.00 Checking -> Savings transfer marked Paid at $0.00, committed."""
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Zero Savings",
        anchor_balance=Decimal("2000.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], Decimal("500.00"),
    )
    db.session.commit()
    transfer_service.settle_transfer(
        xfer.id, seed_user["user"].id, submitted=typed(Decimal("0.00")),
    )
    db.session.commit()
    return xfer


def _records(xfer_id):
    """Every payment record either side of *xfer_id* holds, as stored."""
    db.session.expire_all()
    return db.session.query(TransactionEntry).filter(or_(
        TransactionEntry.expense_transfer_id == xfer_id,
        TransactionEntry.income_transfer_id == xfer_id,
    )).all()


def test_a_day_typed_into_the_popover_of_a_zero_close_is_refused(
    app, auth_client, seed_user,
):
    """Every rendered control posted, Checking's day box filled: 400, the sentence, nothing stored."""
    with app.app_context():
        xfer = _closed_at_zero(seed_user)
        url = f"/transfers/instance/{xfer.id}"
        before = (xfer.status_id, xfer.version_id)
        assert _records(xfer.id) == []

        page = auth_client.get(f"/transfers/{xfer.id}/full-edit")
        assert page.status_code == 200
        fields = form_fields(
            page.get_data(as_text=True), url, attribute="hx-patch",
        )
        boxes = [value for name, value in fields if name == "settled_on_from"]
        assert boxes == [""], (
            "the popover must render Checking's day box EMPTY for a $0.00 "
            f"close, or this posts nothing the owner could type: {fields}"
        )
        typed_day = (
            seed_user["bootstrap_period"].start_date + timedelta(days=1)
        ).isoformat()
        payload = [
            (name, typed_day if name == "settled_on_from" else value)
            for name, value in fields
        ]

        response = auth_client.patch(url, data=MultiDict(payload))

        assert response.status_code == 400, response.get_data(as_text=True)[:400]
        assert _SENTENCE in response.get_data(as_text=True)
        assert _records(xfer.id) == []
        stored = db.session.get(Transfer, xfer.id)
        assert (stored.status_id, stored.version_id) == before
