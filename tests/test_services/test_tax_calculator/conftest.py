"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_tax_calculator.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

import pytest
from decimal import Decimal

from ._helpers import FakeFederalRules, _single_brackets


@pytest.fixture
def single_bracket_set():
    """Single filer bracket set with $15,000 standard deduction."""
    return FakeFederalRules(
        standard_deduction=Decimal("15000"),
        child_credit_amount=Decimal("2000"),
        other_dependent_credit_amount=Decimal("500"),
        brackets=_single_brackets(),
    )
