"""The RETIRED one-payment-per-month loan composition, kept as an ORACLE.

Plan step **X-au-g-2c-3b-2**, which deleted ``loan_ledger.split_payment_cash``
from the application.  That function charged a month's interest INSIDE the
per-payment step, so it was correct exactly while a loan took one payment per
accrual period -- the assumption the step exists to remove.  Production now
charges once per accrual PERIOD, as its own event, and allocates each payment's
cash against whatever stands
(:func:`~app.services.loan_ledger.replay_loan_events`).

**It is deleted from ``app/`` and kept HERE because it is not only a producer: it
is the second opinion that makes the equivalence claim measurable.**  Two suites
re-fold a plan through it deliberately --
``test_loan_payoff_date_oracle._clears_within`` and
``test_loan_interest_in_year._plan_projected_interest`` -- over plans that put
exactly ONE payment in every accrual period.  On that shape the two compositions
must agree to the cent, and *their agreeing IS the equivalence claim, measured
rather than asserted*.  Deleting the composition outright would have left the
producer grading itself, which is the "green gate measuring nothing" shape this
project has paid for repeatedly.  A plan with two payments in one period is NOT
gradeable here -- it is the case the two compositions are supposed to disagree on
-- and is pinned directly instead
(``test_loan_payoff_date_oracle.TestChargePerPeriodNotPerPayment``).

**It still calls the production PRIMITIVES, and that is deliberate too.**  The
oracle owns the COMPOSITION and nothing else: spelling the interest of
:func:`~app.utils.money.installment_interest` inline here would re-create the
``(b*r)/12`` against ``b*(r/12)`` association plan step X-au-g-2c-3c measured
apart -- ``$565.37`` against ``$565.36`` over 500,000 randomised draws -- and the
oracle would then be grading the producer on a formula neither of them uses.  So
this is MORE independent than the pre-deletion arrangement (where the whole
composition was a production call), not less.  What it does NOT take from
production is the WINDOW: the composition charged one month by definition, so
it names one (:data:`_ONE_MONTH`) rather than reading the window the producer
derives (plan step recurrence:R16-d).
"""

from datetime import date
from decimal import Decimal

from app.enums import InterestAccrualConventionEnum
from app.services.installment_calendar import accrual_window
from app.services.loan_ledger import AccrualCharge
from app.services.rate_period_engine import RatePeriod
from app.utils.money import (
    PaymentCashSplit,
    apply_payment_cash,
    installment_interest,
)

_ZERO_MONEY = Decimal("0.00")

#: The one month the retired composition charged per payment, as the window
#: the one interest producer takes.  The composition was the ``MONTHLY_1_12``
#: rule by construction (``balance * (rate / 12)`` a payment), and that
#: convention counts the calendar months a window spans, so which month is
#: named is immaterial: any one month charges the same.  Named here rather
#: than read off a charge (``AccrualCharge.opens_on``) so the oracle prices
#: no window the producer derived.
_ONE_MONTH = (date(2000, 1, 1), date(2000, 2, 1))

#: The synthetic term a hand-built :func:`rate_period` amortizes over.  Nothing
#: the loan FOLD does reads it -- the fold takes the rate and the escrow -- so it
#: exists only to make the value well formed.
_SYNTHETIC_TERM_MONTHS = 360


def rate_period(
    annual_rate: Decimal,
    *,
    start_date: date = date(1900, 1, 1),
    period_pi: Decimal = _ZERO_MONEY,
) -> RatePeriod:
    """Build a hand-stated :class:`RatePeriod` for a test charge.

    An :class:`~app.services.loan_ledger.AccrualCharge` carries the whole
    governing rate period since plan step X-au-g-2c-3b-2, so a hand-built plan
    needs one.  ``start_date`` defaults far enough back that
    :func:`~app.services.rate_period_engine.period_for_date` would place any test
    date inside it, and ``period_pi`` defaults to ``0.00`` because no loan FOLD
    reads it -- only the CONFIRMED schedule row does, and a hand-built forward
    plan builds none.  A test that asserts on a displayed contractual P&I states
    it explicitly.

    Args:
        annual_rate: The period's annual rate as a decimal fraction.
        start_date: The period's first day.
        period_pi: The period's level P&I.

    Returns:
        The :class:`RatePeriod`.
    """
    return RatePeriod(
        index=0,
        start_date=start_date,
        annual_rate=annual_rate,
        period_pi=period_pi,
        start_month_index=0,
        term_months_at_start=_SYNTHETIC_TERM_MONTHS,
        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
    )


def accrual_charge(
    on_date: date, annual_rate: Decimal, escrow: Decimal,
) -> AccrualCharge:
    """Build one hand-stated :class:`~app.services.loan_ledger.AccrualCharge`.

    The charge a test plan states for one accrual period, wrapping *annual_rate*
    in the :class:`RatePeriod` the value carries.  Stated by hand rather than
    taken from the leaf's calendar (``loan_ledger.contract_charges``, ruling
    R-R100), so a fold graded against it is graded against arithmetic rather
    than against the derivation that feeds it.

    Args:
        on_date: The date the period's charge falls.
        annual_rate: The rate the period accrues at.
        escrow: The escrow the period impounds.

    Its window is the month before *on_date*, on *on_date*'s own day
    (:func:`~app.services.installment_calendar.accrual_window`): a hand-stated
    charge falls on the day it means, and the monthly convention charges that
    one month.

    Returns:
        The :class:`~app.services.loan_ledger.AccrualCharge`.
    """
    return AccrualCharge(
        on_date=on_date,
        period=rate_period(annual_rate, start_date=on_date),
        escrow=escrow,
        opens_on=accrual_window(on_date, on_date.day)[0],
    )


def charge_then_allocate(
    cash: Decimal,
    balance: Decimal,
    annual_rate: Decimal,
    monthly_escrow: Decimal,
) -> PaymentCashSplit:
    """Charge ONE month, then divide this payment's *cash* against it.

    The retired composition, verbatim in behaviour (see the module docstring for
    why it lives here).  ``balance`` is the outstanding balance BEFORE this
    payment.

    Two regimes:

    * **Loan already closed** (``balance <= 0``): no interest accrues and no
      escrow is due, so the entire cash is an overpayment routed to ``excess``.
    * **Open loan**: ``interest = round_money(balance * (annual_rate / 12))``,
      one month under ``MONTHLY_1_12`` (:data:`_ONE_MONTH`);
      ``principal = cash - interest - monthly_escrow``; a principal that would
      overrun the balance caps to it, the remainder going to ``excess``.

    Args:
        cash: The cash this payment moved.
        balance: The outstanding balance before this payment.
        annual_rate: The annual rate governing this payment's installment.
        monthly_escrow: The monthly escrow in effect (``0.00`` when none).

    Returns:
        The :class:`~app.utils.money.PaymentCashSplit` for this payment.
    """
    if balance <= _ZERO_MONEY:
        return apply_payment_cash(cash, balance, _ZERO_MONEY, _ZERO_MONEY)
    return apply_payment_cash(
        cash,
        balance,
        installment_interest(
            balance, annual_rate, *_ONE_MONTH,
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
        monthly_escrow,
    )
