"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_posting_service.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service
from tests._test_helpers import (
    family_journal_filter,
    transfer_family_journal_filter,
    transfer_side_record,
    transit_ledger_account,
    figure_source_columns,
    linked_ledger_account,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger_id(account):
    """Return the LINKED ledger account id for *account* (never its twin)."""
    return linked_ledger_account(_db.session, account.id).id


def _entries_for_transfer(transfer_id):
    """Return every journal entry of the transfer's FAMILY, oldest first.

    The two per-movement entries a settle writes since plan step
    ``balance:X-bi-6-3`` (each linked by ``transaction_entry_id`` to one
    side's covering movement), plus anything the legacy one-entry source still
    links by ``transfer_id`` (``transfer_family_journal_filter``).
    """
    return (
        _db.session.query(JournalEntry)
        .filter(transfer_family_journal_filter(transfer_id))
        .order_by(JournalEntry.id)
        .all()
    )


def _transit_ledger_id(seed_user):
    """Return the owner's Transfers-in-transit ledger account id (a lookup)."""
    return transit_ledger_account(_db.session, seed_user["user"].id).id


def _side_entries(transfer_id):
    """Return ``{account_id: [entries]}`` of a transfer's family by SIDE.

    Each per-movement entry links its side's covering movement, whose
    ``account_id`` is the side's; the legacy ``transfer_id``-linked entries
    (none in a go-forward suite) would carry no movement and are keyed
    ``None``.
    """
    by_side: dict = {}
    for entry in _entries_for_transfer(transfer_id):
        movement = (
            _db.session.get(TransactionEntry, entry.transaction_entry_id)
            if entry.transaction_entry_id is not None else None
        )
        key = movement.account_id if movement is not None else None
        by_side.setdefault(key, []).append(entry)
    return by_side


def _covering_movement_of_side(transfer_id, account_id):
    """Return the record of the transfer's side on *account_id*, refusing none.

    Off the TRANSFER by its side link since plan step balance:X-bi-6-4d-2
    (``transfer_side_record``, independent SQL; ruling R-BAL167 class 4),
    where it was the covering movement under the side's live shadow.
    """
    record = transfer_side_record(_db.session, transfer_id, account_id)
    assert record is not None, (
        f"transfer {transfer_id}'s side on account {account_id} holds no record"
    )
    return record


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for one entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


def _scenario_id(seed_user):
    """Return the seed user's baseline scenario id."""
    return seed_user["scenario"].id


def _entries_for_transaction(transaction_id):
    """Return every journal entry for the row's FAMILY, oldest first.

    The row and its covering movement (plan step **X-bi-3a**): a settle
    through the seam mirrors the row's money onto a movement whose entries
    link by ``transaction_entry_id``, so a read keyed on ``transaction_id``
    alone misses the money.  Every figure asserted through this is unchanged.
    """
    return (
        _db.session.query(JournalEntry)
        .filter(family_journal_filter(transaction_id))
        .order_by(JournalEntry.id)
        .all()
    )


def _period_nets(entries):
    """Return ``{pay_period_id: {ledger_account_id: net}}`` over *entries*.

    The per-period reconciliation view the R2 attribution tests assert on:
    an INDEPENDENT re-grouping of the entries' legs by the entry's pay period
    and the leg's ledger account (never the service's own
    ``_posting_write.posted_by_period``), so the tests re-derive the nets the production
    reconcile must have produced.
    """
    nets: dict[int, dict] = {}
    for entry in entries:
        per_ledger = nets.setdefault(entry.pay_period_id, {})
        for leg in (
            _db.session.query(Posting)
            .filter_by(journal_entry_id=entry.id)
            .all()
        ):
            per_ledger[leg.ledger_account_id] = (
                per_ledger.get(leg.ledger_account_id, Decimal("0.00"))
                + leg.amount
            )
    return nets


def _period_nets_for_transaction(transaction_id):
    """Per-period, per-ledger nets over a transaction's journal entries."""
    return _period_nets(_entries_for_transaction(transaction_id))


def _period_nets_for_transfer(transfer_id):
    """Per-period, per-ledger nets over a transfer's journal entries."""
    return _period_nets(_entries_for_transfer(transfer_id))


def _ledger_total(ledger_account_id):
    """Return the net of all posting legs on one ledger account.

    Sums ``account_postings.amount`` directly by ledger account id -- the
    counter-account (category / fallback) analog of
    ``posting_service.account_posting_total``, which keys off a REAL account's
    linked ledger and so cannot be pointed at a category ledger account.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .filter(Posting.ledger_account_id == ledger_account_id)
        .scalar()
    )


def _add_txn_entry(seed_user, txn, amount, *, is_credit):
    """Attach one purchase entry (debit or credit) to *txn* and flush.

    The shared ``add_entry`` helper only builds debit entries; the envelope
    tests need explicit ``is_credit`` control to exercise the
    ``effective - Sigma(credit)`` formula, so this sets it directly.
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


def _resolve_category_ledger(seed_user, category_key, ledger_class):
    """Return the category ledger account for a seed category and class.

    Idempotent: returns the row ``sync_transaction_postings`` created during
    the test (the resolver respects the partial unique), so a leg's
    ``ledger_account_id`` can be asserted against it.  Passing
    ``category_key=None`` resolves the per-(owner, class) Uncategorized
    fallback.
    """
    category_id = (
        None if category_key is None
        else seed_user["categories"][category_key].id
    )
    return ledger_account_service.get_or_create_category_ledger_account(
        seed_user["user"].id, category_id, ledger_class,
    )
