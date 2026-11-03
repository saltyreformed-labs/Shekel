"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_paycheck_stub_pricing.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app.enums import WithholdingKindEnum
from app.services.balance_at import BalanceContext
from app.services.salary_paydays import paycheck_on
from app.tax_law import TaxLaw
from tests._test_helpers import add_test_pay_stub, made_up_fica, made_up_state, made_up_year


_FEDERAL = WithholdingKindEnum.FEDERAL_INCOME


_STATE = WithholdingKindEnum.STATE_INCOME


_SS = WithholdingKindEnum.SOCIAL_SECURITY


_MEDICARE = WithholdingKindEnum.MEDICARE


def _taxes(federal, state, social_security, medicare):
    """A stub's four printed taxes, as :func:`add_test_pay_stub` takes them."""
    return {
        _FEDERAL: federal, _STATE: state, _SS: social_security, _MEDICARE: medicare,
    }


def _law():
    """2026 at 5% and 2027 at 4% North Carolina, made-up FICA, no federal tax."""
    return TaxLaw(years=tuple(
        made_up_year(
            year, fica=made_up_fica(),
            states={"NC": made_up_state(Decimal(rate), Decimal("0.00"))},
        )
        for year, rate in ((2026, "0.0500"), (2027, "0.0400"))
    ))


def _priced(owner, day):
    """The paycheck the read pass's pricer prices on *day*."""
    ctx = BalanceContext.build(owner["profile"].user_id)
    period = paycheck_on(ctx.calendar(), day)
    assert period is not None, f"{day} is not one of the owner's paydays"
    return ctx.paychecks().for_profile(owner["profile"]).at(period)


def _same_lines_stub(owner, payday, base_pay, taxes):
    """A stub of the owner's two lines at their app figures."""
    return add_test_pay_stub(
        owner["profile"], payday, base_pay, taxes,
        lines=((owner["health"], "200.00"), (owner["phone"], "60.00")),
    )


def _four(breakdown):
    """The four priced taxes, in withholding order."""
    taxes = breakdown.taxes
    return (taxes.federal, taxes.state, taxes.social_security, taxes.medicare)
