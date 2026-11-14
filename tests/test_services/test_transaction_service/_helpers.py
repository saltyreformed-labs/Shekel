"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_transaction_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date

from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.cash_ledger import resolve_transaction_amount
from tests._test_helpers import (
    amount_basis_for,
    figure_source_columns,
    make_expense_template,
    make_income_template,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount, description, *,
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    The transaction service operates on already-loaded entries, so
    these tests construct entries with raw ORM access to keep the
    setup focused on the helper's contract.
    """
    # The parent, resolved here rather than taken as an argument: this
    # helper's whole point is that a caller passes IDS.  The entry takes its
    # account (the parent's, because no door here says otherwise -- it is the
    # movement's OWN since plan step credit_card:CC-5-1) and its owner (the
    # parent's, which ``fk_transaction_entries_owner_transaction`` refuses to
    # see any other way).
    parent = db.session.get(Transaction, txn_id)
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn_id,
        account_id=parent.account_id,
        owner_id=parent.user_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _make_template(
    seed_user, *, income=False, amount="500.00", is_envelope=True,
):
    """Create a PRICED, every-paycheck definition of the requested kind.

    The shared builders (:func:`make_expense_template` and its income twin),
    so the definition is one the engine can generate from and its rows are
    the engine's own (:func:`~tests._test_helpers.generate_row_of`, plan step
    balance:X-cf-3b).
    It was ``_make_envelope_template`` -- a hand-built, UNPRICED envelope
    template that half its callers then flipped to ``is_envelope = False`` --
    until that step; a case states the definition it means here instead.

    Args:
        seed_user: The seed user fixture dict.
        income: Build an income definition rather than an expense one.
            The income-side envelope branch is reachable only this way:
            Phase 2 rejects ``is_envelope=True`` on income at the schema
            layer, and the helper under test must stay correct for a row
            that reached that state by direct write.
        amount: The definition's stated price.
        is_envelope: Whether its rows track purchases.
    """
    builder = make_income_template if income else make_expense_template
    return builder(
        db.session, seed_user, amount=amount,
        name=f"Tracked {'Income' if income else 'Expense'}",
        category_key="Groceries", is_envelope=is_envelope,
    )


def _plan_of(txn):
    """Return what *txn* PLANS -- its amount as the amount model resolves it.

    A row of a definition stores no figure (plan step X-au-e), so "the plan is
    untouched" is a question to the resolver rather than to the
    ``estimated_amount`` column, which reads ``None`` on such a row before and
    after every settle.  A row the owner re-priced owns its figure and the
    column is that figure; the cases that grade a re-price read it there.
    """
    return resolve_transaction_amount(txn, amount_basis_for(txn))
