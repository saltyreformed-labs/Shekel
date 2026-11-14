"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_loan_recurrence_start_bound.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from tests._test_helpers import create_loan_account


# The suite's frozen today is 2026-03-20, so a loan originating 2026-04-15 is
# configured BEFORE it closes -- the shape the bound exists for.
ORIGINATION = date(2026, 4, 15)


PRINCIPAL = Decimal("200000.00")


def _upcoming_mortgage(seed_user, db_session, periods, payment_day=1):
    """A mortgage that has not closed yet (origination after the frozen today)."""
    return create_loan_account(
        seed_user, db_session, name="Closing In April",
        principal=PRINCIPAL, rate=Decimal("0.05000"),
        term=360, origination_date=ORIGINATION, payment_day=payment_day,
        account_type=AcctTypeEnum.MORTGAGE,
    )
