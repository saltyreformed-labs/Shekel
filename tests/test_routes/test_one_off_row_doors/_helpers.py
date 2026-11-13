"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_one_off_row_doors.py``, moved verbatim by ``tools/split``.
"""

import re
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.services.one_off import OneOffToPlace, place_one_off
from werkzeug.datastructures import MultiDict
from tests._test_helpers import derived_span, generate_row_of, make_expense_template
from tests.test_routes._statement_forms import form_fields


def _one_off(seed_user, seed_periods_today):
    """The engine's row of a definition whose cadence was then cleared.

    A plain helper rather than a fixture: the row has to be born inside the
    test's own ``app_context`` to be persistent in its session.
    """
    template = make_expense_template(
        db.session, seed_user, amount="162.25", name="Kayla's Kindle",
        category_key="Groceries",
    )
    row = generate_row_of(template, seed_periods_today[0])
    template.recurrence_rule = None
    db.session.commit()
    assert row.template_id == template.id
    assert row.recurs is False
    return row


def _placed(seed_user, period, *, due_date=None, name="Kayla's Kindle",
            amount="162.25", category_key="Groceries", **flags):
    """Place a one-off in *period* through the one producer (leaf 7b-1)."""
    row = place_one_off(
        OneOffToPlace(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name=name,
            amount=Decimal(amount),
            category_id=seed_user["categories"][category_key].id,
            **flags,
        ),
        derived_span(period),
        scenario_id=seed_user["scenario"].id,
        due_date=due_date,
    )
    db.session.commit()
    assert row.is_placed is True
    return row


def _card(auth_client, row):
    """Return the full-edit card for *row*, rendered."""
    resp = auth_client.get(f"/transactions/{row.id}/full-edit")
    assert resp.status_code == 200
    return resp.data.decode()


def _submit(auth_client, row, **changes):
    """PATCH *row* with what its card renders, *changes* typed over it.

    A browser submits every control the card renders at the value it
    renders, so the payload is READ off the card rather than written by
    hand; a changed field replaces every pair of that name (a checkbox and
    its hidden ``false`` twin are one control).
    """
    rendered = form_fields(
        _card(auth_client, row), f"/transactions/{row.id}",
        attribute="hx-patch",
    )
    payload = [pair for pair in rendered if pair[0] not in changes]
    payload.extend(changes.items())
    return auth_client.patch(
        f"/transactions/{row.id}", data=MultiDict(payload),
    )


def _recurring(seed_user, seed_periods_today):
    """The control: the engine's row of a definition that still repeats."""
    template = make_expense_template(
        db.session, seed_user, amount="100.00", name="Streaming",
        category_key="Groceries",
    )
    row = generate_row_of(template, seed_periods_today[0])
    db.session.commit()
    assert row.recurs is True
    return row


def _rendered_companion(auth_client, row):
    """Read the popover's rendered-figure companion, as a browser posts it."""
    edit = auth_client.get(f"/transactions/{row.id}/full-edit")
    assert edit.status_code == 200
    companion = re.search(
        r'name="estimated_amount_as_rendered"[^>]*value="([^"]*)"',
        edit.data.decode(),
    )
    assert companion is not None
    return companion.group(1)
