"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_models/test_ledger_account.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from app import ref_cache


def _class_id(member):
    """Resolve a LedgerAccountClassEnum member to its integer PK."""
    return ref_cache.ledger_account_class_id(member)


def _kind_id(member):
    """Resolve a LedgerAccountKindEnum member to its integer PK."""
    return ref_cache.ledger_account_kind_id(member)
