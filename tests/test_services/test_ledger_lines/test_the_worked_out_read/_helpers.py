"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_ledger_lines/test_the_worked_out_read.py``, which ``tools/split``
moved here, and the one builder of the loan their figures were computed from
(:func:`_trued_up_loan`).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.services.ledger_lines import LedgerLine, linked_key, loan_key, worked_out_lines
from tests._test_helpers import create_loan_with_trueup


# Inside seed_periods' last paycheck (2026-05-08 - 2026-05-21) and after every
# paycheck a case files a row in, so a row in an early paycheck settles on a
# day its own paycheck does not hold.
_TODAY = date(2026, 5, 15)


def _read(seed_user):
    """Return the seed owner's worked-out lines in the baseline scenario."""
    return worked_out_lines(seed_user["user"].id, seed_user["scenario"].id)


def _lines_of(lines, movement_id):
    """Return the lines booked from one movement, in the read's order."""
    return [line for line in lines if line.movement_id == movement_id]


def _source(member):
    """Return the stored id of a ``PostingSourceEnum`` member."""
    return ref_cache.posting_source_id(member)


def _kind(member):
    """Return the stored id of a ``PostingKindEnum`` member."""
    return ref_cache.posting_kind_id(member)


def _trued_up_loan(owner, name, *, escrow_annual=None):
    """Return a $250,000.00 6% loan from 2025-01-01, trued up to $100,000.00 on 2026-01-10.

    The one builder of the loan whose lines :func:`_loan_anchor_lines` writes
    out by hand and whose 6% payment split the lines' cases compute, so the
    loan those figures were computed from is built once.  *owner* is the
    fixture dict of the owner who holds it, and *escrow_annual* an optional
    yearly escrow line effective from origination.
    """
    return create_loan_with_trueup(
        owner, _db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("100000.00"),
        anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
        origination_date=date(2025, 1, 1), name=name,
        escrow_annual=escrow_annual,
    )


def _loan_anchor_lines(loan):
    """Return the opening and true-up lines of the suite's trued-up loan.

    The loan :func:`_trued_up_loan` builds, from a $250,000.00 principal
    originated 2025-01-01 and trued up to $100,000.00 on 2026-01-10: the
    opening ``-250,000.00`` owed on its own line against ``+250,000.00`` on
    its opening equity, then the true-up ``250,000.00 - 100,000.00 =
    +150,000.00`` and ``-150,000.00`` (no payment falls before it), each
    budgeted in no paycheck and from no movement.
    """
    own = linked_key(loan)
    equity = loan_key(LedgerAccountKindEnum.EQUITY_OPENING, loan.id)
    opened = date(2025, 1, 1)
    trued = date(2026, 1, 10)
    opening = (
        _source(PostingSourceEnum.LOAN_OPENING), _kind(PostingKindEnum.OPENING),
    )
    trueup = (
        _source(PostingSourceEnum.LOAN_TRUEUP), _kind(PostingKindEnum.TRUEUP),
    )
    return [
        LedgerLine(own, None, opened, Decimal("-250000.00"), *opening, None),
        LedgerLine(equity, None, opened, Decimal("250000.00"), *opening, None),
        LedgerLine(own, None, trued, Decimal("150000.00"), *trueup, None),
        LedgerLine(equity, None, trued, Decimal("-150000.00"), *trueup, None),
    ]
