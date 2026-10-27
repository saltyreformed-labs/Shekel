"""
Shekel Budget App -- Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, contribution, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.

Contributions are the to-side LEGS of transfers into the investment/retirement
account -- the loan loaders' one partition since plan step balance:X-bi-6-4b
(``recorded_contributions``), where they were shadow income transactions
(``transfer_id IS NOT NULL``).  The caller loads and prices them and passes
them in; this module has no database access.

**They arrive PRICED, as :class:`PricedContribution` records rather than ORM
rows** (plan step X-au-c2, a developer ruling of 2026-08-12).  Four readers here
used to ask each row for its ``effective_amount`` and screen it with
``status_contributes_to_balance`` -- a model property that cannot answer for a
row whose amount is DERIVED, since such a row stores no figure and resolving one
needs a database this module deliberately does not have.  Valuing at the
BOUNDARY instead (``recorded_contributions.load_transfer_contributions_*``)
resolves the whole row set ONCE, drops the rows that contribute nothing, and
retires all four copies of the status screen with them.  What is left here is
arithmetic over plain data, which is what the paragraph above always claimed.

**They arrive DATED too, since plan step C2-f2c**, and for the same reason one
tier down.  A contribution's pay period was carried here as an id, so the three
readers that needed to know WHEN it landed took the owner's whole period list
as an argument and looked the payday up in it -- a join table threaded through
a public signature to answer a question the loader can answer once, where the
session is.  ``calculate_investment_inputs`` and
:func:`build_contribution_timeline` are the readers; neither takes a period id
now, and the period list left the first of them outright.  It also ended a
shape collision this module could not have absorbed otherwise: it was shared
by ``/retirement``, which then held ORM rows spelling that key ``id``, and by
``/investment``, which since C2-f2c holds
:class:`~app.services.pay_calendar.DerivedPeriod`\\ s spelling it ``period_id``.
*Both hold* ``DerivedPeriod``\\ *s since C2-f2d-3, and since plan step
salary:S3-e-2 the feed hands the period to the paycheck engine, so that is
the one type these readers take* (ruling **R-SAL19**).

**And the DEDUCTION half arrives priced and dated since plan step
salary:R14-b**, which is the same move a third time and the one that finishes
it (ruling **R-SAL2**).  This module used to be handed the deduction ROWS,
flattened by an ``adapt_deductions`` adapter into ``(amount, calc_method_id,
annual_salary, periods_per_year, annual_cap)``, and work out what each took
from a paycheck itself -- dividing the profile's STORED annual salary by the
paycheck count.  That was one question answered twice, and the second answer
was worse on three independent axes at once: it was blind to every raise
(finding **D45**; ``$1,646.84`` is that row's own figure for a hypothetically
LINKED deduction over the developer's 63 saved paydays, where what this step
moves on his data AS IT STANDS is ``+$452.42`` of employer money through
``balance_at.grid_balance_view`` -- two windows and two feeds, so neither
figure substitutes for the other), blind to a deduction's inflation escalation
(**N-532**, which
``AdaptedDeduction`` could not even carry), and it spelled the calendar-year
cap twice more beside the engine's -- ``_annual_cap_averaged`` evenly and
``_period_capped_total`` front-loaded.  All four spellings are deleted here.
The engine's :class:`~app.services.paycheck_calculator.PricedLine` already
carries ``target_account_id``, so what one account's payroll puts in on one
payday is a fold of the breakdown the engine computes for it, and the
:class:`AccountPayrollFeed` the loader hands over is that fold -- asked per
period and priced on demand since plan step **salary:S3-e-2** (ruling
**R-SAL15**), where it was two dictionaries over the saved calendar that
HELD an invented figure past it.

The root cause behind all three divergences was ONE shape: an adapter that
flattens away everything varying PER PERIOD cannot answer a per-period
question, however many of its answers are patched.  That is why the remedy is
a deletion rather than a fourth fix.
"""

from app.services.investment_projection._feed import AccountPayrollFeed
from app.services.investment_projection._inputs import (
    InvestmentInputs,
    PricedContribution,
    TransferContributions,
    build_contribution_timeline,
    calculate_investment_inputs,
    employer_contribution_params,
)

__all__ = [
    "AccountPayrollFeed",
    "InvestmentInputs",
    "PricedContribution",
    "TransferContributions",
    "build_contribution_timeline",
    "calculate_investment_inputs",
    "employer_contribution_params",
]
