"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_cc5_3_settle_tender.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.services import transaction_service
from app.services.cash_ledger import settled_cash_facts
from tests._test_helpers import create_account_of_type, generate_row_of, make_expense_template


_ONE_DAY = timedelta(days=1)


_HOTEL = Decimal("120.00")


def _card(seed_user, name="Rewards Card"):
    """Create an active Credit Card account for *seed_user*."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _hotel_bill(seed_user, period, *, account=None, is_envelope=False):
    """One engine-generated `$120.00` Hotel bill on checking (or *account*)."""
    template = make_expense_template(
        db.session, seed_user, amount=str(_HOTEL),
        name="Hotel", category_key="Rent", is_envelope=is_envelope,
        account=account,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _movement(txn):
    """Return the row's one covering movement, re-read from the database."""
    db.session.expire(txn)
    movements = txn.covering_movements
    assert len(movements) == 1, f"expected one covering movement, got {len(movements)}"
    return movements[0]


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _per_day(account_id, scenario_id):
    """Sum the settled cash facts on *account_id* per settle day."""
    sums = {}
    for fact in settled_cash_facts(account_id, scenario_id):
        sums[fact.settled_on] = sums.get(fact.settled_on, Decimal("0")) + fact.delta
    return sums


def _latest_anchor(account_id):
    """The account's latest balance assertion, which the fixtures seed."""
    anchor = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.id.desc())
        .first()
    )
    assert anchor is not None, "the fixture account carries no anchor"
    return anchor


def _correct_tender(txn, account_id):
    """The full-edit popover's identity Save naming another 'Paid from' account."""
    transaction_service.apply_requested_status(
        txn, txn.status_id, stated=transaction_service.StatedRecord(tender_account_id=account_id),
    )
    db.session.commit()
