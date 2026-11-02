"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_ledger_lines/test_the_worked_out_read.py``, moved verbatim by
``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum, PostingSourceEnum
from app.services.ledger_lines import LedgerLine, linked_key, loan_key, worked_out_lines


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


def _loan_anchor_lines(loan, calendar):
    """Return the opening and true-up lines of the suite's trued-up loan.

    The loan ``create_loan_with_trueup`` builds from a $250,000.00 principal
    originated 2025-01-01 and trued up to $100,000.00 on 2026-01-10: the
    opening ``-250,000.00`` owed on its own line against ``+250,000.00`` on
    its opening equity, then the true-up ``250,000.00 - 100,000.00 =
    +150,000.00`` and ``-150,000.00`` (no payment falls before it), each filed
    by its day on *calendar* and from no movement.
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
        LedgerLine(
            own, calendar.filing_period(opened).period_id, opened,
            Decimal("-250000.00"), *opening, None,
        ),
        LedgerLine(
            equity, calendar.filing_period(opened).period_id, opened,
            Decimal("250000.00"), *opening, None,
        ),
        LedgerLine(
            own, calendar.filing_period(trued).period_id, trued,
            Decimal("150000.00"), *trueup, None,
        ),
        LedgerLine(
            equity, calendar.filing_period(trued).period_id, trued,
            Decimal("-150000.00"), *trueup, None,
        ),
    ]
