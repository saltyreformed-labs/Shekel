"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_confirmed_view.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db as _db
from app.models.loan_params import LoanParams
from tests._test_helpers import (
    create_loan_with_trueup,
    create_settled_transfer,
    seam_confirmed_view,
    SPLIT_LOAN,
)


_ORIGINATION_PRINCIPAL = SPLIT_LOAN.origination_principal


_ORIGINATION_DATE = SPLIT_LOAN.origination_date


_RATE = SPLIT_LOAN.rate


_ANCHOR_BALANCE = SPLIT_LOAN.anchor_balance


_ANCHOR_DATE = SPLIT_LOAN.anchor_date


_P1 = SPLIT_LOAN.p1


_P2 = SPLIT_LOAN.p2


_P3 = SPLIT_LOAN.p3


# The evaluation date every "all payments visible" assertion reads at, and the
# frozen today after it (the view answered only as_of <= today until plan step
# recurrence:R16-e; a later read is pinned in ``test_the_contract_line_start``).
_AS_OF = date(2026, 12, 31)


_FROZEN_TODAY = date(2027, 1, 1)


def _loan_params(loan) -> LoanParams:
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


def _make_loan(
    seed_user, *, anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
    rate=_RATE, escrow_annual=None, name="Split Loan",
    origination_date=_ORIGINATION_DATE,
):
    """Create the SPLIT_LOAN shape, optionally overriding the anchor or rate.

    A case that walks a payment before its true-up passes
    :data:`_ORIGINATION_BEFORE_P1` as *origination_date* (plan step
    recurrence:R16-c-2, ruling R-R101).
    """
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=origination_date, escrow_annual=escrow_annual,
        name=name,
    )


def _settle(seed_user, loan, period, cash=Decimal("1000.00"), settled_on=None):
    """Settle a Checking -> loan payment through the production transfer path.

    ``settled_on`` pins the payment's settle day, which is what its
    VISIBILITY keys on (plan step C2's one clock); it defaults to the pay
    period's own start so every payment in a shape is visible from a known day.
    """
    return create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period,
        amount=cash,
        settled_on=period.start_date if settled_on is None else settled_on,
    )


def _view(loan, seed_user, as_of=_AS_OF):
    """Return the seam's confirmed view for *loan* at *as_of* (baseline scope)."""
    return seam_confirmed_view(loan.id, seed_user["scenario"].id, as_of)


def _economics(view):
    """Return each row as ``(date, interest, principal, balance)`` -- the pins."""
    return [
        (row.payment_date, row.interest, row.principal, row.remaining_balance)
        for row in view.history_rows
    ]
