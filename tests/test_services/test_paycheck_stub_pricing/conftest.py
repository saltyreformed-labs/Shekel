"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_paycheck_stub_pricing.py``, moved verbatim by ``tools/split``.  A
fixture here reaches exactly the tests that module held, at the scope it declared.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import PaycheckLineKindEnum
from tests._test_helpers import make_flat_paycheck_line, make_salary_profile

from ._helpers import _law


@pytest.fixture(name="owner")
def _owner(app, db, seed_user, seed_periods, tax_law):  # pylint: disable=unused-argument
    """The module docstring's owner, committed: ``{"profile", "health", "phone"}``.

    Pylint: ``unused-argument`` -- ``app`` and ``seed_periods`` are asked for
    their side effects, the app context and the owner's 2026 paydays.
    """
    tax_law(_law())
    profile = make_salary_profile(
        seed_user, db.session, name="Day Job", pay=Decimal("3000.00"),
        pay_from=date(2026, 1, 2),
    )
    db.session.flush()
    health = make_flat_paycheck_line(
        profile, "Health", "200.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    )
    phone = make_flat_paycheck_line(
        profile, "Phone", "60.00", PaycheckLineKindEnum.TAXABLE_EARNING,
    )
    db.session.commit()
    return {"profile": profile, "health": health, "phone": phone}
