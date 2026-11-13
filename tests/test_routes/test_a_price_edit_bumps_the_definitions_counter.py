"""A price-only save bumps the definition's optimistic-lock counter (plan step X-bp).

Both edit forms pin the definition's ``version_id`` and refuse a form whose
counter is stale.  A definition's price is its series, rows of another table,
so a save that changes only the price writes no column of the template's own
row; until plan step X-bp the write door also stored the ``default_amount``
copy there, and that UPDATE is what bumped the counter.  With the column gone,
``template_amount_service`` bumps it itself whenever the series changes.  Each
case renders the edit form ONCE, as two tabs would, and saves two different
prices from it: the second must be refused and the first price must stand.

Measured with the bump removed (2026-11-02): the TRANSACTION case fails (the
second save lands), and so does the placed row's card
(``test_one_off_row_doors.test_the_item.TestTheDefinitionsCounterGuardsTheCard``); the
TRANSFER case still passes, because that form's save also writes a column of
the template's own row, which bumps the counter by itself.  It stays as the
guard for that form, not as a grade of the bump.
"""

from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service
from app.utils.dates import display_today
from tests._test_helpers import (
    create_savings_account,
    make_expense_template,
    make_transfer_template,
)
from tests.test_routes._statement_forms import form_fields

_STALE = b"changed by another action"


def _with_amount(fields, amount):
    """The scraped form's pairs with its Amount box set to *amount*."""
    assert ("amount" in dict(fields)) and ("version_id" in dict(fields)), (
        "the scraped form is not the edit form"
    )
    return MultiDict([
        (name, amount if name == "amount" else value) for name, value in fields
    ])


def _two_tabs(auth_client, edit_url, action):
    """Two saves of one rendered form, $120.00 then $130.00; return both responses."""
    fields = form_fields(auth_client.get(edit_url).data.decode(), action)
    first = auth_client.post(
        action, data=_with_amount(fields, "120.00"), follow_redirects=True,
    )
    second = auth_client.post(
        action, data=_with_amount(fields, "130.00"), follow_redirects=True,
    )
    return first, second


@pytest.mark.usefixtures("seed_periods_today")
class TestAPriceEditBumpsTheDefinitionsCounter:
    """The second of two tabs saving only a price is refused, on both forms."""

    def test_a_transaction_definition(self, app, auth_client, seed_user):
        """$100.00, then $120.00 from one tab and $130.00 from a stale one: $120.00 stands."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Netflix",
            )
            db.session.commit()
            template_id = template.id

            first, second = _two_tabs(
                auth_client, f"/templates/{template_id}/edit",
                f"/templates/{template_id}",
            )

            assert first.status_code == 200
            assert _STALE not in first.data.lower()
            assert _STALE in second.data.lower()
            db.session.expire_all()
            assert template_amount_service.amount_as_of(
                db.session.get(TransactionTemplate, template_id),
                display_today(),
            ) == Decimal("120.00")

    def test_a_transfer_definition(self, app, auth_client, seed_user):
        """The transfer form: $200.00, then $120.00 and a stale $130.00: $120.00 stands."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            template_id = template.id

            first, second = _two_tabs(
                auth_client, f"/transfers/{template_id}/edit",
                f"/transfers/{template_id}",
            )

            assert first.status_code == 200
            assert _STALE not in first.data.lower()
            assert _STALE in second.data.lower()
            db.session.expire_all()
            assert template_amount_service.amount_as_of(
                db.session.get(TransferTemplate, template_id),
                display_today(),
            ) == Decimal("120.00")
