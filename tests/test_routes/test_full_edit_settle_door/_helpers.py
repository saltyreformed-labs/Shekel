"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_full_edit_settle_door.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.services.cash_ledger import contribution_of, resolve_transaction_amount
from app.utils.dates import display_today
from tests._ledger_reads import family_movement_ids, linked_key_of, movement_nets_by_key
from tests._test_helpers import add_entry, amount_basis_for, create_envelope_txn


def _plan_worth(txn):
    """What a REVERTED (Projected) row contributes -- its plan, resolved.

    Asked of :func:`~app.services.cash_ledger.contribution_of` because the row
    is out of the settled band: the two revert cases of
    ``test_the_dropdown_settle.py``'s ``TestARevertTakesBackWhatTheSettleDerived``
    assert that a row which has been reverted is worth its PLAN again, and
    since plan step X-bx
    ``row_valuation.settled_contribution`` refuses exactly that row rather than
    pricing its plan column by hand (finding **BAL-465**).  The figures those
    cases assert are unchanged, and so is the rule -- the STATUS decides which
    figure governs, not the columns.
    """
    return contribution_of(txn, amount_basis_for(txn))


def _gas_envelope(seed_user, period):
    """Build production row 2231's shape: an $80.00 budget, one $48.98 purchase.

    Returns the flushed, Projected, envelope-tracked transaction.
    """
    txn = create_envelope_txn(
        seed_user, db.session, period, "Gas", Decimal("80.00"),
    )
    add_entry(
        db.session, seed_user, txn, Decimal("48.98"), display_today(),
    )
    db.session.commit()
    return txn


def _full_edit_save(auth_client, txn, status_id, **overrides):
    """PATCH exactly what the full-edit popover submits on Save.

    The form posts the row's whole state, not a delta -- the estimated amount,
    the period, the notes, the due date and the status -- which is why the
    handler's field writes and its status work interact at all.  Keyword
    overrides replace or add a field.

    **A FINALISED row submits less, and modelling that is load-bearing.**  The
    template disables the money / period / due-date inputs on an immutable row
    (the #26 lock's UI half) and a disabled input is omitted from the POST, so
    a payload carrying them would be refused by ``_finalised_edit_response``
    before any of this suite's subject matter ran -- a helper that tested the
    lock instead of the settle.

    **A GENERATED row submits no due date at all, for the same reason one tier
    over** (plan step balance:X-au-e).  Its date is its DEFINITION's -- it is a
    member of ``DerivedRowFields`` and every regeneration rewrites it -- and
    since that step it is also what prices the row, so the popover renders it
    as TEXT rather than an input and a browser sends no ``due_date`` key.  A
    payload carrying one is refused by
    ``_gates._reject_generated_due_date_edit`` before this suite's subject
    matter runs, which is the same trap the finalised arm above exists to
    avoid: seven cases here failed on that refusal the moment the gate landed,
    all of them testing the settle and none of them testing the date.

    **The amount it posts is the one the popover RENDERS, which is the amount
    model's answer and not the raw column** (plan step balance:X-cf).  The
    template's box reads ``budgets[txn.id]`` --
    :func:`~app.services.cash_ledger.amounts_by_key`, the resolved figure --
    and a GENERATED row stores no figure of its own, so the column is ``None``
    there and a payload built from it posts the string ``"None"``, which the
    schema refuses with a 422 before the settle runs.  Every fixture this
    suite settles is generated now, so that is the browser this helper has to
    model; while the fixtures owned their figures the two spellings agreed
    and the wrong one went unnoticed.
    """
    payload = {
        "version_id": str(txn.version_id),
        "status_id": str(status_id),
        "notes": txn.notes or "",
    }
    if not txn.status.is_immutable:
        rendered = str(resolve_transaction_amount(txn, amount_basis_for(txn)))
        payload["estimated_amount"] = rendered
        # **The amount box's companion, carrying what the form RENDERED into
        # it** (ruling R-JR, plan step balance:X-au-h).  The popover posts both,
        # and equal values are what a browser sends when the user did not touch
        # the box -- which is the case every test using this helper models,
        # since each one is editing something else.
        #
        # Omitting it would not fail: a figure with no companion is treated as
        # AUTHORED, so every save through this helper would take ownership of
        # the row's amount and the suite would go on passing while modelling a
        # browser that does not exist.  That is the trap this docstring's other
        # two paragraphs already describe, on a third field.
        payload["estimated_amount_as_rendered"] = rendered
        payload["pay_period_id"] = str(txn.pay_period_id)
        if txn.template_id is None:
            payload["due_date"] = (
                txn.due_date.isoformat() if txn.due_date else ""
            )
    payload.update(overrides)
    return auth_client.patch(f"/transactions/{txn.id}", data=payload)


def _cash_leg(txn_id, account_id):
    """Return the net the row's movements book on the account's own line."""
    txn = db.session.get(Transaction, txn_id)
    return movement_nets_by_key(
        db.session, family_movement_ids(txn), txn.scenario_id,
    ).get(linked_key_of(db.session, account_id), Decimal("0.00"))
