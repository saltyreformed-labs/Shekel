"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_bank_agreement.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatusEnum, TxnTypeEnum
from app.services import bank_agreement
from app.services.balance_at import BalanceContext
from tests._test_helpers import one_off_row_of, settle_day_columns, cover_bare_settled_row


_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN


_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED


_ZERO = Decimal("0.00")


def _settled(db, seed_user, period, name, amount, day, *, is_income=False):
    """Insert one SETTLED row whose cash moved on *day*."""
    status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
        ),
    )
    txn.status_id = status_id
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    cover_bare_settled_row(db.session, txn, amount, amount)
    return txn


def _agreement(seed_user):
    """Return the comparison for the seed user's account."""
    return bank_agreement.bank_agreement(
        seed_user["account"], BalanceContext.build(seed_user["user"].id),
    )


def _day(agreement, on):
    """Return the one :class:`AgreementDay` for *on*."""
    return next(row for row in agreement.days if row.day == on)
