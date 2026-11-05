"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_posting_service.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service, transaction_service
from app.services.settle_day import SettleDay
from tests._ledger_reads import (
    family_movement_ids,
    period_nets_of_movements,
    transfer_family_movement_ids,
)
from tests._stored_ledger import (
    family_journal_filter,
    linked_ledger_account,
    transfer_family_journal_filter,
    transit_ledger_account,
)
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    transfer_side_record,
)

# Inside seed_periods' last paycheck, so every day a case names is a day the
# owner's calendar files; each new account opens the day before.  A module
# whose cases open accounts pins it (``freeze_today``) in an autouse fixture.
_TODAY = date(2026, 5, 15)


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


def _period_nets_for_transaction(transaction_id, scenario_id):
    """Per-period, per-key nets over the lines of a transaction's family."""
    return period_nets_of_movements(family_movement_ids(transaction_id), scenario_id)


def _period_nets_for_transfer(transfer_id, scenario_id):
    """Per-period, per-key nets over the lines of both sides' records."""
    return period_nets_of_movements(
        transfer_family_movement_ids(transfer_id), scenario_id,
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


def _opening_day(account) -> date:
    """Return the day *account*'s one balance assertion, its opening, observes."""
    return (
        _db.session.query(AccountAnchorHistory.observed_on)
        .filter_by(account_id=account.id)
        .one()
        .observed_on
    )


def _bill(seed_user, period, *, account=None):
    """Return a Projected $120.00 bill in *period*, committed.

    Its own account is *account*, the seed's Checking when ``None``.
    """
    template = make_expense_template(
        _db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent", account=account,
    )
    row = generate_row_of(template, period)
    _db.session.commit()
    return row


def _settle_paid_from(row, tender, day) -> None:
    """Settle *row* from *tender* on *day*, committing nothing.

    A payment may be paid from its row's own account, the owner's checking
    account or a credit card.
    """
    transaction_service.settle_transaction(
        row,
        settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED),
        tender_account_id=tender.id,
    )


def _settled_bill_paid_from(seed_user, period, tender, day, *, account=None):
    """Return a $120.00 bill settled from *tender* on *day*, committed.

    The bill's own account is *account*, the seed's Checking when ``None``
    (:func:`_bill`, :func:`_settle_paid_from`).
    """
    row = _bill(seed_user, period, account=account)
    _settle_paid_from(row, tender, day)
    _db.session.commit()
    return row
