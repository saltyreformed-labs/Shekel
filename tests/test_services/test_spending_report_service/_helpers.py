"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_spending_report_service.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services.spending_report_service import SpendingWindow
from tests._test_helpers import (
    cover_bare_settled_row,
    default_settle_day,
    one_off_row_of,
    settle_day_columns,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db, seed_user, period, name, category_key, estimated,
    *, actual=None, status_enum=StatusEnum.DONE, is_income=False,
    is_deleted=False, due_date=None, settled_on=None, account=None,
):
    """Create one transaction for report testing (settled expense by default).

    *account* places the row on another of the owner's accounts (a card, a
    savings account); the seed's checking account by default.

    A row in a SETTLED status carries the whole record -- the day, and the
    figure with who wrote it -- resolved through the one door a bare-built
    fixture uses (``settle_day_columns`` for the row's day pair, plan step
    X-az, and ``cover_bare_settled_row`` for the COVERING MOVEMENT that IS
    the record since plan step ``balance:X-bi-4b-2``).  *actual* is a
    figure a HUMAN typed (``typed``); with none the record is the row's own
    plan (``resolved``), which is what a settle with nothing to correct
    books.  The settle DAY defaults to the period's start where the caller
    names none, because a settled row carries one.
    """
    cat_id = (
        seed_user["categories"][category_key].id if category_key else None
    )
    type_enum = TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    planned = Decimal(str(estimated))
    settled_day = settled_on or default_settle_day(
        period, ref_cache.status_id(status_enum),
    )
    txn = one_off_row_of(
        period,
        name=name,
        amount=planned,
        user_id=period.user_id,
        account_id=(account or seed_user["account"]).id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        category_id=cat_id,
        due_date=due_date or period.start_date,
    )
    txn.status_id = ref_cache.status_id(status_enum)
    txn.is_deleted = is_deleted
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(settled_day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    if settled_day is not None:
        cover_bare_settled_row(db.session, txn, planned, actual)
    return txn


def _pp_window(period):
    """Return a pay-period SpendingWindow for a period."""
    return SpendingWindow(window_type="pay_period", period_id=period.id)


def _group(report, group_name):
    """Return the named breakdown group row, or None."""
    return next(
        (g for g in report.breakdown if g.group_name == group_name), None,
    )
