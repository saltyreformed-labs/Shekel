"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_entry_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import figure_source_columns


# ── Helper ────────────────────────────────────────────────────────


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Used by tests that need pre-existing entries without re-testing
    the full create_entry validation chain.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=transaction.id, account_id=transaction.account_id, owner_id=transaction.user_id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _outstanding_debit(txn, seed_user, amount="50.00",
                       purchased_on=date(2026, 1, 5)):
    """Attach one debit purchase with NO recorded posting day.

    Kept here for the helper-guard class below when the outstanding-set tests
    moved to ``test_reconcile_service`` at plan step X-f2-c1: that class grades
    ``tests._test_helpers.mark_purchase_settled``, which is about a purchase
    with no settle day, and building one is all it needs from the reconcile
    subject.
    """
    return _make_entry(
        txn, seed_user["user"], amount=amount,
        description="Kroger", purchased_on=purchased_on,
    )
