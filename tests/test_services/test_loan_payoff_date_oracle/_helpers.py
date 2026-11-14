"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_loan_payoff_date_oracle.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.services.balance_at._plan import PlannedPayment
from tests.oracles.loan_forward_fold import HandPlan
from tests._test_helpers import (
    bind_rule_to_loan,
    contract_forward_references,
    create_loan_account,
    last_covered_day,
    loan_params_for,
    make_cadence_rule,
)
from tests.oracles.loan_monthly_composition import accrual_charge
from tests.oracles.recurrence_baseline import MONTHLY


_PRINCIPAL = Decimal("300000.00")


_RATE = Decimal("0.06000")


_TERM = 360


def _payment(due, cash, *, effective=None):
    """Build one :class:`PlannedPayment` for the pure-fold unit tests."""
    return PlannedPayment(
        due_date=due,
        effective_date=effective if effective is not None else due,
        cash=Decimal(cash),
        is_estimated=False,
    )


def _plan(payments, *, rate="0.00", escrow="0.00"):
    """Bundle *payments* with one CHARGE per accrual period they occupy.

    Stated by hand rather than taken from the leaf's calendar
    (``loan_ledger.contract_charges``, ruling R-R100), so these tests
    grade the FOLD against arithmetic anyone can check rather than against the
    derivation that feeds it.  One charge per ``(year, month)`` the payments
    occupy, dated at the earliest due in it -- which for the one-payment-a-month
    plans below is each payment's own due date.
    """
    opens = {}
    for payment in payments:
        slot = (payment.due_date.year, payment.due_date.month)
        if payment.due_date < opens.get(slot, date.max):
            opens[slot] = payment.due_date
    return HandPlan(
        payments=list(payments),
        charges=[
            accrual_charge(on_date, Decimal(rate), Decimal(escrow))
            for on_date in sorted(opens.values())
        ],
        # Every payment here has a charge standing over it (one per slot,
        # dated at its earliest due), so the fold never asks for a calendar.
    )


def _create_loan(seed_user, period, origination_date, *, name):
    """Create a fixed mortgage originating on *origination_date*, anchored to *period*."""
    account = create_loan_account(
        seed_user, db.session, name=name,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=origination_date, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    return account, loan_params_for(db.session, account.id)


def _attach_derive_extra(seed_user, loan_account, extra):
    """Attach a derive-from-loan recurring payment carrying a standing extra.

    **Bound to the loan through the loan door's own sync since plan step
    R16-b-2.**  ``fires_on_day=1`` alone starts the rule on the first 1st the
    fixture schedule reaches, which is one or two months BEFORE the loan
    originates; the loan door never leaves a payment there
    (``bind_rule_to_loan`` writes ``starts_on`` as the first contractual
    installment, plan step C9a).  Until R16-b-2 the fold never priced an
    occurrence dated before ``as_of``, so those pre-origination occurrences
    were invisible; ruling **R-R64** prices every occurrence the schedule
    places that no row answers -- exactly as generation would write them --
    so a definition authored ahead of its loan now pays for months the loan
    did not exist, the shape the door exists to refuse.  Authoring as the door
    does is what keeps the fixture a loan payment.
    """
    user = seed_user["user"]
    # Authored through the write door (plan step R7c-b): the day the rule
    # fires on is its first occurrence's own day, so "the 1st" is stated as a
    # DATE the fixture schedule reaches rather than as a separate column.
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        name="Mortgage Payment",
    )
    template.settings = LoanPaymentSettings(
        derive_from_loan=True, extra_principal=extra,
    )
    db.session.add(template)
    db.session.commit()
    # The definition first, then the cadence onto it (plan step R-F6), then
    # the loan's own start onto the cadence, as ``routes/loan/payment_transfer``
    # does.
    rule = make_cadence_rule(
        template, MONTHLY, fires_on_day=1,
    )
    bind_rule_to_loan(rule, loan_account.id)
    db.session.commit()


def _reference_payoffs(loan_params, scenario_id, as_of, extra):
    """Return (payoff with *extra* a month, pure-contractual payoff) -- the reference.

    The independent reference (:func:`tests._test_helpers.contract_forward_references`):
    ``project_forward`` over the CONTRACT from the replayed starting state, a
    walk that amortizes month by month and consumes no payment records at all
    -- a different code path from the seam's fold, so agreement is meaningful,
    not tautological.  *It was the composer's ACCELERATED slice until plan
    step R7d-g-3 deleted the composer's planned slices (ruling **R-R88**).*
    """
    scenarios, with_extra = contract_forward_references(
        loan_params, scenario_id, as_of, extra,
    )
    return with_extra[-1].payment_date, scenarios.original_forward[-1].payment_date


def _current_period(periods, today):
    """The seeded pay period containing *today*."""
    return next(
        period for period in periods
        if period.start_date <= today <= last_covered_day(period)
    )
