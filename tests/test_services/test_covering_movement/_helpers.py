"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_covering_movement.py``, moved verbatim by ``tools/split``.
"""

from collections import defaultdict
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services import transaction_service
from app.services.transfer_legs import TransferLeg
from tests._test_helpers import (
    create_savings_account,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
)


def _source(member):
    """The stored id of a ``MovementFigureSourceEnum`` member."""
    return ref_cache.movement_figure_source_id(member)


def _bill(seed_user, period, amount="148.32", *, is_envelope=False):
    """One engine-generated expense row of a fresh definition."""
    template = make_expense_template(
        db.session, seed_user, amount=amount,
        name="Electric", category_key="Rent", is_envelope=is_envelope,
    )
    return generate_row_of(template, period)


def _settle(txn, *, submitted=None, settle_day=None):
    """Settle *txn* through the verb."""
    return transaction_service.settle_transaction(
        txn, submitted=submitted, settle_day=settle_day,
    )


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by.

    ``transaction_service.apply_requested_status`` is what every revert of a
    transaction reaches (the popover's PATCH and the cancel route; the
    matcher never reverts) -- the seam plus the family reconcile that
    reverses the survivor's legs.  The seam alone is driven where the claim
    is the seam's (:class:`TestTheBareSeamUnDatesAndKeeps`).
    """
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _only_movement(txn):
    """Return the row's one covering movement, asserting there is exactly one.

    *txn* is a plan row, or a transfer's leg as :func:`_legs_of` reads it,
    whose ``covering_movements`` is the side's record.
    """
    movements = txn.covering_movements
    assert len(movements) == 1, f"expected one covering movement, got {len(movements)}"
    return movements[0]


def _per_day(facts):
    """Sum the facts' deltas per settle day."""
    sums = defaultdict(Decimal)
    for fact in facts:
        sums[fact.settled_on] += fact.delta
    return dict(sums)


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


def _side_record(xfer, *, is_income):
    """Return the record *xfer*'s side holds, or ``None``, by its side link.

    This file's own SQL, never the producer under test (ruling R-BAL167
    class 4, plan step balance:X-bi-6-4d-2): a side's record hangs off the
    TRANSFER since that step, where it hung off the side's shadow row.
    """
    link = (
        TransactionEntry.income_transfer_id if is_income
        else TransactionEntry.expense_transfer_id
    )
    return db.session.query(TransactionEntry).filter(
        link == xfer.id,
    ).one_or_none()


def _legs_of(xfer):
    """Return a transfer's ``(expense leg, income leg)``, freshly read.

    Each a :class:`~app.services.transfer_legs.TransferLeg` holding its side's
    record as :func:`_side_record` reads it, so its status is the TRANSFER's
    and its day its record's.  They were the transfer's two shadow rows, by
    TYPE, until plan step balance:X-bi-6-4d-2 stopped keeping a shadow's
    status and day (ruling R-BAL167 class 4).  A leg holds the record it was
    read with, so a case reads the legs again after a door.
    """
    return tuple(
        TransferLeg(
            transfer=xfer,
            account_id=xfer.to_account_id if is_income else xfer.from_account_id,
            is_income=is_income,
            record=_side_record(xfer, is_income=is_income),
        )
        for is_income in (False, True)
    )


def _settled_pair(seed_user, period, *, to_account=None, amount="500.00"):
    """A settled ``Checking -> to_account`` transfer with its two covered legs.

    *to_account* defaults to a fresh Savings account opened at ``$0.00``.
    Returns ``(transfer, expense leg, income leg)``.
    """
    if to_account is None:
        to_account = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], to_account,
        period, amount=Decimal(amount),
    )
    db.session.flush()
    return (xfer, *_legs_of(xfer))
