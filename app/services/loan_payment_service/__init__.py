"""
Shekel Budget App -- Loan Payment Service.

Reads a debt account's payments and prepares them for the amortization engine.
Two leaves, one per verb, and the public surface is re-exported here so no
caller learns the split:

  * :mod:`._engine_prep` -- the corrections a recorded payment needs before the
    amortization engine can replay it (escrow subtraction, biweekly
    redistribution).  Imports no sibling.
  * :mod:`._context` -- the account's loaded context and its
    :class:`PaymentRecord` feed.  Sits above ``_engine_prep``.

**It held FOUR leaves until plan step X-au-g-2a, and the two that left are the
PRICING pair.**  ``_basis`` (what one installment costs) and ``_pricing``
(amount rule 4's read-pass derivation,
:class:`~app.services.cash_ledger.LoanPricing`) are now
``cash_ledger._loan_installment`` and ``cash_ledger._loan_pricing``.  They went
because rule 4's producer answers *what does this row's amount resolve to*,
which is the amount model's question rather than this module's -- and hosting
it here forced ``cash_ledger._amount_source`` to reach UP a tier for it, which
is what forced the split of :mod:`app.services.row_valuation`.  That reach is
deleted rather than routed around, so this package imports the amount model --
which plan step X-au-g-2c did: :func:`get_payment_history` prices its feed
through ``cash_ledger.contributions_by_id`` rather than through
``row_valuation.settled_contribution``, which closed finding **N-266**(a).  **The
cycle that reach created was real and pylint reported nothing about it**, which
is measured once, in ``cash_ledger._loan_installment``.

**Why it is STILL a package, stated honestly because its original reason is
gone.**  This was one module standing at 1009 of pylint's 1000-line ceiling,
the SIXTH in this codebase to reach that cap after ``transfer_service``
(**N-152**), ``pay_period_locks`` (**N-156**), ``anchor_service`` (**N-201**),
pay-calendar **P31** and ``transaction_service``.  The pricing pair's departure
leaves the two remaining leaves measuring **548 lines between them** (2026-08-31),
so a single collapsed module would sit well under that ceiling and the LINE
PRESSURE forces nothing.
What still does is the cut itself: the corrections a recorded payment needs
before an engine can replay it, and the loading of an account's context, are
two verbs, and the graph stays a line -- ``_engine_prep`` imports no sibling
and ``_context`` imports ``_engine_prep``, nothing upward.  Collapsing the two
back into one module would fit under the ceiling and is deliberately NOT taken
here: it is a second change with its own diff and its own review, and this step
is a MOVE whose gate is byte-identity.

This package reads the POSTED LEDGER NOWHERE, as of plan step E1d-b
(``docs/audits/balance_architecture/README.md``).  It used to host
``confirmed_loan_view``, the read switch's single injection point into the
genesis posting readers, which made it the one module whose resolver-feeding
loaders had to be fenced at FUNCTION granularity to keep the reconciliation
oracle's parallel run honest.  The loan resolver's confirmed slice now seeds
from the event WALK inside the balance seam (``balance_at.confirmed_view``), so
that allowlist is gone and this package is ledger-free whole.  The whole-loan
read that composes these loaders with the pure resolver lives in
``app.services.balance_at._resolution``; that module imports THIS one, and this
one imports nothing from the seam, so there is no cycle.

Shadow income transactions represent payments received by a debt account via
transfers.  When a user transfers money from checking to a mortgage account,
the transfer service creates two shadow transactions: an expense on checking
(money out) and an income on the mortgage (money in).  The payment feed reads
the income side (via the :mod:`app.services.loan_loaders` leaf, which owns the
row loaders and the shadow-income query this package used to host) to discover
all payments into a loan account.

**What queries what, and the history matters because the sentence has been
wrong here before.**  This file once asserted that the service "queries ONLY
budget.transactions (transfer invariant #5); it NEVER queries budget.transfers",
and that was FALSE where it mattered most -- at the top of the file, about a
critical invariant -- because the pricing leaf's
``_load_live_payment_configs`` queried ``budget.transfers``, joined through the
template to ``loan_payment_settings``, and had to: discovering WHICH transfers
are loan payments is a question about that table.

**That query left with the pricing pair at plan step X-au-g-2a, and the claim
was true again from then until plan step balance:X-bi-6a, when it became false
BY DESIGN and is restated as a MEASUREMENT.**  What is read here now:
:func:`get_payment_history` takes a loan's payments from
:func:`app.services.loan_ledger.payment_installments`, whose SETTLED half is
the transfers into the loan whose payment has happened (ruling
**R-BAL140**), as legs carrying their covering movement as their record
(:func:`app.services.loan_loaders.settled_income_legs`, since plan step
balance:X-bi-6-4b; it was the shadow rows in ``budget.transactions`` until
then) and whose PROJECTED half is the parent rows in ``budget.transfers`` as
derived legs
(:func:`app.services.loan_loaders.projected_income_legs`; ruling **R-BAL13**,
Transfer Invariant 5 as restated at that step), plus the loan's own params,
rate history and escrow lines.  No amount is read off a transfer for a
payment that has settled.  The paragraph above predicted that "the next name
added here is the one that could make it false again", and it was: this is
that sentence being kept honest rather than kept.

Shared by:
  - ``app/routes/loan/`` (dashboard and payoff calculator)
  - ``app/services/savings_dashboard_service.py`` (savings projections)
  - ``app/services/year_end_summary_service.py`` (annual aggregation)
  - ``app/routes/debt_strategy.py`` (debt payoff strategies)
"""

from ._context import LoanContext, get_payment_history, load_loan_context
from ._engine_prep import compute_contractual_pi, prepare_payments_for_engine

__all__ = [
    "LoanContext",
    "compute_contractual_pi",
    "get_payment_history",
    "load_loan_context",
    "prepare_payments_for_engine",
]
