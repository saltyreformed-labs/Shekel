"""
Shekel Budget App -- Amortization Engine

Pure-function package for loan amortization calculations.
Generates amortization schedules, summary metrics, and payoff analysis.
No database access -- operates only on values passed in.

It projects the CONTRACT forward from a known starting state; what a loan
actually paid is its walk's (``loan_ledger.replay_loan_events``), and no
function here takes a payment list.  Two projections come from the same
engine:

  1. Original schedule -- the contract, extra_monthly=0
  2. What-if schedule -- the contract plus extra_monthly=user input, the
     target-date search's contract-only answer

A COMMITTED schedule -- the contract with the owner's projected transfers
routed through a ``monthly_override`` map -- was the third until plan step
R7d-g-3 (ruling **R-R88**); the balance seam's plan fold
(``balance_at._plan_fold``) is the one walk of what a loan is projected to
PAY, and every loan surface reads it there.

Split along the primitives/question seam (the C0302 root fix, same
shape as the ``loan_resolver`` package) so existing imports from
``app.services.amortization_engine`` keep working unchanged:

* :mod:`._dates` -- :class:`PaymentDates`, a loan payment's three dates as
  ONE value.  The package's lowest leaf (stdlib only), so every tier that holds
  a payment names its dates from one home: :class:`PaymentRecord` composes it
  and so does the loader's
  :class:`~app.services.loan_ledger.PaymentInstallment` (plan step
  **balance:X-bl-2b**).
* :mod:`._projection` -- the value records, the standard payment
  formula, the schedule-slot helpers, and :func:`project_forward` itself.
  It writes no date arithmetic of its own since plan step recurrence:R25,
  which deleted its month step and clamp: a projected row's date is
  :func:`app.utils.dates.clamped_day`'s, and a slot's step past a contested
  month is :mod:`app.services.installment_calendar`'s.
  Per-month rate and contractual P&I come from the projection's
  :class:`PeriodTerms` schedule (mapped from the rate-period engine's
  periods), so projected rows pay the same single-source-of-truth
  figures the loan card displays.
* :mod:`._payoff` -- the payoff-by-date question layer
  (:class:`PayoffRequest`, :func:`required_extra_for_projection`,
  :func:`calculate_payoff_by_date`) built on the primitives.
"""

from ._dates import PaymentDates
from ._payoff import (
    PayoffRequest,
    calculate_payoff_by_date,
    required_extra_for_projection,
)
from ._projection import (
    AmortizationRow,
    AmortizationSummary,
    PaymentRecord,
    PeriodTerms,
    ProjectionInputs,
    RateChangeRecord,
    calculate_monthly_payment,
    calculate_remaining_months,
    project_forward,
    schedule_dates,
    slotted_dates,
)

__all__ = [
    "AmortizationRow",
    "AmortizationSummary",
    "PaymentDates",
    "PaymentRecord",
    "PayoffRequest",
    "PeriodTerms",
    "ProjectionInputs",
    "RateChangeRecord",
    "calculate_monthly_payment",
    "calculate_payoff_by_date",
    "calculate_remaining_months",
    "project_forward",
    "required_extra_for_projection",
    "schedule_dates",
    "slotted_dates",
]
