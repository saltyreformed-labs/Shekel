"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_loan_settled_legs.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import create_loan_account, transfer_side_record


#: The loan's origination and contractual due day.
_ORIGINATION = date(2026, 2, 1)


_PAYMENT_DAY = 1


#: The installment every payment below satisfies, and the period holding it
#: (``seed_periods[4]`` runs 2026-02-27 .. 03-12).
_DUE = date(2026, 3, 1)


_PERIOD = 4


#: The day a close or a movement is stated on, four days after the installment.
_CLOSED_ON = date(2026, 3, 5)


def _loan(seed_user):
    """A $100,000.00 loan at 6% from 2026-02-01, due on the 1st."""
    loan = create_loan_account(
        seed_user, db.session, name="Settled Leg Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=_ORIGINATION, payment_day=_PAYMENT_DAY,
    )
    db.session.commit()
    return loan


_SETTLED_UNDATED = (
    r"is settled but its sides hold 1 dated and 1 un-dated payment record"
)


def _stage_an_undated_loan_side(transfer, account):
    """STAGE ruling R-BAL147's state, uncommitted: a Paid transfer whose loan side lost its day.

    The loan side's record -- on the TRANSFER since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where it hung off the
    income twin until then -- is un-dated by bulk update, its transfer left
    Paid: the shape a revert of the twin ALONE used to leave (the seam keeps
    a reverted record and releases its day, ruling **R-BAL61**).  No door
    writes it, and since that step the status-band rule refuses it at
    COMMIT, so it exists only inside this save (ruling **R-BAL167** class
    2): the caller commits and expects the refusal, and reads the halves
    only after the rollback.

    Returns:
        The loan side's record.  It returned the income twin beside it until
        plan step ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        **R-BAL258**; the helper rewritten under ruling **R-BAL167** class 4).
    """
    record = transfer_side_record(db.session, transfer.id, account.id)
    db.session.query(TransactionEntry).filter(
        TransactionEntry.id == record.id,
    ).update(
        {"settled_on": None, "settled_day_basis_id": None},
        synchronize_session=False,
    )
    db.session.flush()
    db.session.expire_all()
    return record
