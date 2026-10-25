"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_transfer_legs.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from app.services.cash_ledger import leg_amounts_by_key
from app.services.transfer_legs import grid_transfer_leg, transfer_side_leg
from app.services.transfer_service import (
    TransferSpec,
    create_transfer as create_transfer_from_spec,
)
from tests._test_helpers import create_savings_account, read_pass


#: The transfer every DB case below moves: checking -> savings.
_AMOUNT = Decimal("250.00")


#: A read pinned BEFORE every seeded period (the schedule opens 2026-01-02),
#: so ruling R-G's clamp -- a plan lands no earlier than ``as_of + 1`` --
#: never moves a landing day and a leg lands on its nominal day.
_AS_OF = date(2026, 1, 1)


def _savings(seed_user, name="Savings"):
    """A savings account of the seeded owner, anchored at zero."""
    return create_savings_account(
        seed_user, db.session, name, Decimal("0.00"),
    )


def _cancelled_transfer(seed_user, from_account, to_account, period):
    """Create a CANCELLED transfer through the creation door.

    It wrote the parent and both shadows until plan step
    ``balance:X-bi-6-4d-2`` made a transfer one row (ruling **R-BAL258**).
    """
    return create_transfer_from_spec(TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount_ownership=AmountOwnership.own(_AMOUNT),
        status_id=ref_cache.status_id(StatusEnum.CANCELLED),
        category_id=seed_user["categories"]["Rent"].id,
    ))


def _balance_on(account, scenario, day):
    """Fold *account* through a FRESH read pass and sample it on *day*."""
    ctx = read_pass(account, scenario, _AS_OF)
    return balances_at(assembled_fold(account, ctx), [day])[day]


def _shown(leg, basis):
    """What *leg* shows of its transfer: the projection table's one row."""
    return {
        "account_id": leg.account_id,
        "is_income": leg.is_income,
        "amount": leg_amounts_by_key([leg], basis)[leg.cell_key],
        "status_id": leg.status_id,
        "status": leg.status,
        "pay_period_id": leg.pay_period_id,
        "pay_period": leg.pay_period,
        "due_date": leg.due_date,
        "category_id": leg.category_id,
        "category": leg.category,
        "is_override": leg.is_override,
        "notes": leg.notes,
        "template_id": leg.template_id,
    }


def _held(stored, is_income):
    """What the side *is_income* names of the *stored* row, as :func:`_shown` keys it.

    *stored* is the row read by SQL, so its values are the database's own;
    the status, period and category ROWS are the ones its id columns name,
    and a row filed under no category names no category row.  A side names
    no transaction definition, so its ``template_id`` is ``None`` whatever
    the row holds.
    """
    return {
        "account_id": stored["to_account_id" if is_income else "from_account_id"],
        "is_income": is_income,
        "amount": stored["amount"],
        "status_id": stored["status_id"],
        "status": db.session.get(Status, stored["status_id"]),
        "pay_period_id": stored["pay_period_id"],
        "pay_period": db.session.get(PayPeriod, stored["pay_period_id"]),
        "due_date": stored["due_date"],
        "category_id": stored["category_id"],
        "category": (
            None if stored["category_id"] is None
            else db.session.get(Category, stored["category_id"])
        ),
        "is_override": stored["is_override"],
        "notes": stored["notes"],
        "template_id": None,
    }


def _reread_sides(transfer_id):
    """Commit, expire every loaded object, and return the transfer's row re-read and its sides.

    Four sides: the two, each read through two doors to a leg, by ACCOUNT
    (``grid_transfer_leg``, the grid's) and by SIDE (``transfer_side_leg``,
    the transfer service's), the expense side first in each.
    """
    db.session.commit()
    db.session.expire_all()
    row = db.session.get(Transfer, transfer_id)
    return row, [
        grid_transfer_leg(row, row.from_account_id),
        grid_transfer_leg(row, row.to_account_id),
        transfer_side_leg(row, is_income=False),
        transfer_side_leg(row, is_income=True),
    ]
