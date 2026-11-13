"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_amortization_engine.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import PeriodTerms


def _terms(
    start_date: date, annual_rate: Decimal, monthly_pi: Decimal,
) -> list[PeriodTerms]:
    """Single fixed-rate terms feed for a projection.

    The SSOT analogue of the old ``(annual_rate, contractual_payment)``
    scalar pair: one :class:`PeriodTerms` entry governing every month.
    Tests that exercise a recast pass a multi-entry list instead.
    """
    return [PeriodTerms(
        start_date=start_date,
        annual_rate=annual_rate,
        monthly_pi=monthly_pi,
        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
    )]
