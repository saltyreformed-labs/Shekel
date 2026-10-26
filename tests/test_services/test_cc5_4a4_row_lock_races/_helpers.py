"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_cc5_4a4_row_lock_races.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import text

from app.extensions import db as _db
from app.models.transaction import Transaction
from app.services import transaction_service
from app.utils.dates import display_today
from tests._test_helpers import generate_row_of, make_expense_template
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests.test_routes._statement_forms import ReconcileFormReader
from tests.test_routes.test_cc5_4a5_popover_presses import dialog_delete_values


#: How long a race waits for a click to reach its commit, or the second click
#: to block, before calling the harness broken.  The cluster's own
#: ``lock_timeout`` is 10 s.
_WAIT = 8.0


def _signed_in_clients(app, count=2):
    """*count* clients, each signed in as the seed user.

    Each sign-in's redirect is checked not to be the sign-in page, so a race
    cannot quietly run one client signed out.

    Returns:
        The signed-in clients, in order.
    """
    clients = []
    for _ in range(count):
        client = app.test_client()
        response = client.post("/login", data={
            "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
        })
        assert response.status_code == 302, response.status_code
        assert urlsplit(response.headers["Location"]).path != "/login"
        clients.append(client)
    return clients


def _state(row_id):
    """Return ``(is_deleted, movements held)`` for *row_id*, read fresh."""
    return (
        _db.session.execute(
            text("SELECT is_deleted FROM budget.transactions WHERE id = :i"),
            {"i": row_id},
        ).scalar_one(),
        _db.session.execute(
            text("SELECT count(*) FROM budget.transaction_entries "
                 "WHERE transaction_id = :i"),
            {"i": row_id},
        ).scalar_one(),
    )


def _groceries(seed_user):
    """A recurring $300.00 Groceries envelope occurrence, committed; returns ``(template, row_id)``."""
    template = make_expense_template(
        _db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    return template, row.id


def _hotel(seed_user):
    """A recurring $120.00 Hotel occurrence (no purchases), committed; returns ``(template, row_id)``."""
    template = make_expense_template(
        _db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent",
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    return template, row.id


def _dinner(seed_user, seed_periods):
    """A recurring $80.00 Dinner occurrence with a next period to repay in; returns its id."""
    template = make_expense_template(
        _db.session, seed_user, amount="80.00", name="Dinner",
        category_key="Rent",
    )
    row = generate_row_of(template, seed_periods[0])
    _db.session.commit()
    return row.id


def _paid_hotel(seed_user):
    """The $120.00 Hotel, settled and committed; returns its id."""
    _template, row_id = _hotel(seed_user)
    transaction_service.settle_transaction(_db.session.get(Transaction, row_id))
    _db.session.commit()
    return row_id


def _purchase(client, row_id):
    """Return the click: a $12.34 KROGER charge added to *row_id* through the add-purchase form."""
    return lambda: client.post(f"/transactions/{row_id}/entries", data={
        "amount": "12.34", "direction": "charge", "description": "KROGER",
        "purchased_on": display_today().isoformat(),
    })


def _delete(client, row_id):
    """Return the click: Delete on *row_id*, posting what its dialog names NOW.

    The popover is drawn as the click is made ready -- before the race -- so
    the dialog sends the bank lines and purchases the row held then (rulings
    R-CC127 / R-CC131), as a card opened before the other click does.
    """
    query = dialog_delete_values(client, row_id)
    return lambda: client.delete(f"/transactions/{row_id}", query_string=query)


def _mark_paid(client, row_id):
    """Return the click: Mark Paid on *row_id*'s desktop cell."""
    return lambda: client.post(f"/transactions/{row_id}/mark-done")


def _archive(client, template_id):
    """Return the click: Archive on the recurring item *template_id*."""
    return lambda: client.post(f"/templates/{template_id}/archive")


def _mark_credit(client, row_id):
    """Return the click: Mark Credit on *row_id*."""
    return lambda: client.post(f"/transactions/{row_id}/mark-credit")


def _save_an_actual(client, row_id, figure):
    """Return the click: the popover's Save on Paid *row_id* with its Actual typed as *figure*.

    The form is read off the popover as the page renders it, before the click
    (a browser submits every control it renders), so the Save carries the
    version the page was drawn at.
    """
    html = client.get(f"/transactions/{row_id}/full-edit").data.decode()
    start = html.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    payload = dict(reader.fields)
    payload["settled_amount"] = figure
    return lambda: client.patch(f"/transactions/{row_id}", data=payload)
