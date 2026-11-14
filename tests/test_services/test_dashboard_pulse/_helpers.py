"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_dashboard_pulse.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from tests._test_helpers import (
    create_transfer,
    one_off_row_of,
    add_entry,
    create_envelope_txn,
    create_savings_account,
    default_settle_day,
    settle_day_columns,
    cover_bare_settled_row,
)


_CURRENT_IDX = 5  # seed_periods index that contains the frozen today.


_TODAY = date(2026, 3, 20)


# ── Helpers ──────────────────────────────────────────────────────────


def _add_expense(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED, due_date=None, is_deleted=False,
):
    """Create a non-tracked expense transaction for testing -- a ONE-OFF.

    Placed through the producer since plan step balance:X-bi-7c, so
    ``due_date=None`` means the paycheck's start (ruling R-BAL22), never an
    undated row: the undated shape is the LEGACY one, whose builder
    ``_legacy_undated_expense`` went with plan step balance:X-bi-7d-2's
    cutover.  Returns the created Transaction (flushed).
    """
    status_id = ref_cache.status_id(status_enum)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        due_date=due_date,
    )
    txn.status_id = status_id
    txn.is_deleted = is_deleted
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    settled_on = default_settle_day(period, status_id)
    for _column, _value in settle_day_columns(settled_on).items():
        setattr(txn, _column, _value)
    db_session.flush()
    if settled_on is not None:
        cover_bare_settled_row(db_session, txn, Decimal(str(amount)))
    return txn


def _undated_transfer_out(db_session, seed_user, period, to_account_name, amount):
    """Create an UNDATED transfer out of the checking account.

    The pulse's "anytime this period" shelf exists for items with no due date.
    Every one-off is dated on its paycheck's start since plan step
    balance:X-bi-7b (R-BAL22) and the cutover (balance:X-bi-7d-2) dated
    production's 26 undated one-offs the same way, so what still feeds the
    shelf is an item NO definition prices: a transfer's expense leg (the
    unpaid-expense loader draws the expense leg of a projected transfer out of
    the set, the Gate B4b ruling) made without a due date, which the transfer
    form leaves optional.  The leg is named ``Transfer to <to_account_name>``
    off the transfer's endpoints.

    It returned the transfer's expense shadow row, guarded undated, until plan
    step balance:X-bi-6-4d-2 made a transfer one row (ruling R-BAL258); the
    guard reads the transfer's own due date now, the column the leg projects,
    and no caller read the return (ruling R-BAL167 class 4).
    """
    savings = create_savings_account(
        seed_user, db_session, to_account_name, Decimal("500.00"),
    )
    xfer = create_transfer(
        seed_user, seed_user["account"], savings, period,
        amount=Decimal(str(amount)),
    )
    db_session.flush()
    assert xfer.due_date is None
    return xfer


def _add_tracked_expense(db_session, seed_user, period, name, estimated):
    """Create an entry-tracked (is_envelope) projected expense.

    Thin wrapper over the shared ``create_envelope_txn`` builder that
    accepts a string ``estimated`` for call-site brevity.
    """
    return create_envelope_txn(
        seed_user, db_session, period, name, Decimal(str(estimated)),
    )


def _add_entry(db_session, seed_user, txn, amount, purchased_on):
    """Attach one debit entry of ``amount`` to ``txn`` (string-amount wrapper)."""
    add_entry(db_session, seed_user, txn, Decimal(str(amount)), purchased_on)
