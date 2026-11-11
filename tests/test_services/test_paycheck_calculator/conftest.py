"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_paycheck_calculator.py``, moved verbatim by ``tools/split``.  A
fixture here reaches exactly the tests that module held, at the scope it declared.
"""

from datetime import date
from decimal import Decimal

import pytest

from tests._tax_configs import hand_built_law

from ._helpers import (
    _period,
    FakeBracket,
    FakeFederalRules,
    FakeStateTaxRules,
    FakeFicaRules,
    FakeProfile,
)


# ── Pytest Fixtures ──────────────────────────────────────────────


@pytest.fixture
def simple_bracket_set():
    """2-bracket progressive set: 10% up to $50k, 22% above."""
    return FakeFederalRules(
        standard_deduction=Decimal("15000"),
        brackets=[
            FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
            FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
        ],
    )


@pytest.fixture
def nc_state_config():
    """NC flat rate 4.5%."""
    return FakeStateTaxRules(flat_rate="0.045")


@pytest.fixture
def standard_fica():
    """Standard 2026 FICA rates."""
    return FakeFicaRules()


@pytest.fixture
def simple_tax_configs(simple_bracket_set, nc_state_config, standard_fica):
    """2026's combined tax config dict, shaped as the resolver hands one back.

    Most periods here are 2026's.  A case that hands a 2027 period this set
    (``test_fica_and_the_year.py``) prices it on 2026's rules, and its tax
    lines' ``rules`` say 2026, unsubstituted: the one-set mode prices and
    labels a paycheck on whatever set it is handed, which plan#3476 deletes.
    No such case reads the label.
    """
    return hand_built_law(
        2026,
        bracket_set=simple_bracket_set,
        state_config=nc_state_config,
        fica_config=standard_fica,
    )


@pytest.fixture
def base_profile():
    """$60k salary, 26 periods, no raises/deductions."""
    return FakeProfile(
        pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        created_at=date(2026, 1, 1),
    )


@pytest.fixture
def biweekly_periods():
    """26 derived pay periods for 2026, starting Jan 2."""
    start = date(2026, 1, 2)
    periods = []
    for i in range(26):
        d = date.fromordinal(start.toordinal() + i * 14)
        periods.append(_period(start_date=d, period_id=i + 1))
    return periods
