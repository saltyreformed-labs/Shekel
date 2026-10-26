"""
Shekel Budget App -- Loan Data Loaders (the loan services' leaf layer)

The pure data-loading functions every loan consumer shares, in two leaves:

* :mod:`._terms` -- a loan's CONTRACTUAL facts: the
  :class:`~app.models.loan_params.LoanParams` /
  :class:`~app.models.loan_anchor_event.LoanAnchorEvent` /
  :class:`~app.models.loan_features.RateHistory` /
  :class:`~app.models.escrow_line.EscrowLine` loaders, the synthesized
  origination anchor, and the contract-time due date each loan payment
  satisfies.
* :mod:`._legs` -- WHICH transfers are an account's payments and which of
  them have HAPPENED: the single settled/projected partition every
  settled-payment consumer reads.

The graph is a line -- ``_terms`` imports ``_legs`` for the settled set the
escrow forward-only guard bounds on, and nothing there reads back.

**It was one module until plan step balance:X-bl-2a**, which gave the shadow
half a partition producer and moved the amount model's eager-load set out of the
query and onto the caller, pushing the file to 1,054 lines past pylint's 1,000
ceiling.  The cut is the seam that step created rather than the line count, and
it is a PACKAGE rather than a second module so every
``app.services.loan_loaders.<name>`` path a caller or a docstring already spells
stays true.

Extracted from :mod:`app.services.loan_payment_service` (the read switch's
final arc) so the loan POSTING package and the loan PAYMENT service both
depend on one leaf instead of on each other: the posting package's
walk and reader need these loaders, while ``loan_payment_service`` hosts the
read-switch seam that imports the posting package's reader -- loading through
a shared leaf is what keeps that dependency one-directional (no import
cycle), rather than a lazy-import workaround.

This package is a LEAF: it imports models, the pure engine primitives
(:class:`~app.services.amortization_engine.RateChangeRecord`,
:func:`~app.services.installment_calendar.monthly_due_date`), the shared
balance predicates and the transfer-leg leaf
(:mod:`app.services.transfer_legs`, itself a leaf: models, the session, the
reference cache and the shared predicates, and no service) -- never another
loan service.  Flask-isolated, reads only, no commits.

Both halves query ``budget.transfers``.  The PROJECTED half has since plan step
balance:X-bi-6a (the plan, as legs derived from the parent; ruling R-BAL13);
the SETTLED half has since plan step balance:X-bi-6-4b (ruling R-BAL140):
the transfers into the account that are no longer Projected or whose to-side
money has moved, each leg carrying its covering movement -- the record of a
payment that moved -- through the one join in :mod:`app.services.transfer_legs`.
No amount is read off a transfer for a payment that has settled.
"""

from ._legs import (
    IncomeLegs,
    income_legs,
    projected_income_legs,
    settled_income_legs,
)
from ._terms import (
    LoanAnchorFact,
    _rate_change_records_from,
    installment_for,
    precedes_origination,
    latest_settled_payment_due_date,
    load_all_loan_account_ids,
    load_escrow_lines,
    load_loan_account_ids_for_user,
    load_loan_anchor_facts,
    load_loan_params,
    load_rate_changes,
    load_rate_history,
    load_standing_loan_assertions,
    loan_payment_due_date,
    synthesize_origination_anchor,
)

__all__ = [
    "IncomeLegs",
    "LoanAnchorFact",
    "income_legs",
    "installment_for",
    "precedes_origination",
    "latest_settled_payment_due_date",
    "load_all_loan_account_ids",
    "load_escrow_lines",
    "load_loan_account_ids_for_user",
    "load_loan_anchor_facts",
    "load_loan_params",
    "load_rate_changes",
    "load_rate_history",
    "load_standing_loan_assertions",
    "loan_payment_due_date",
    "projected_income_legs",
    "settled_income_legs",
    "synthesize_origination_anchor",
]
