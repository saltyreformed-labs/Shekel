"""The helpers every part of this package shares: a family's lines by paycheck, and a purchase."""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from tests._ledger_reads import (
    family_movement_ids,
    period_nets_of_movements,
    transfer_family_movement_ids,
)
from tests._test_helpers import figure_source_columns


def _scenario_id(seed_user):
    """Return the seed user's baseline scenario id."""
    return seed_user["scenario"].id


def _period_nets_for_transaction(transaction_id, scenario_id):
    """Per-period, per-key nets over the lines of a transaction's family.

    The row and its movements (its covering movement and its purchases), each
    of which books its own lines (ruling **R-BAL80**).
    """
    return period_nets_of_movements(family_movement_ids(transaction_id), scenario_id)


def _period_nets_for_transfer(transfer_id, scenario_id):
    """Per-period, per-key nets over the lines of both sides' records."""
    return period_nets_of_movements(
        transfer_family_movement_ids(transfer_id), scenario_id,
    )


def _add_txn_entry(seed_user, txn, amount, *, is_credit):
    """Attach one purchase entry (debit or credit) to *txn* and flush.

    The shared ``add_entry`` helper only builds debit entries; the envelope
    tests need explicit ``is_credit`` control, so this sets it directly.  The
    purchase is un-dated: it books nothing until a case records its day.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="purchase",
        purchased_on=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    _db.session.add(entry)
    _db.session.flush()
    return entry
