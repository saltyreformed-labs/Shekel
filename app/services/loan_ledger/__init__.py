"""
Shekel Budget App -- the loan ledger: a loan's FACTS and the walk over them.

**A loan's balance is a fold over its event stream.**  This package owns the WALK
half of that -- the event stream, the split, and the running-balance replay over
them -- as the LEAF the rest of the loan architecture derives from.  It yields
FACTS (per-payment splits, per-anchor corrections in contract-time order); turning
those facts into a balance owed on a DATE -- the FOLD -- lives in the balance seam
(:mod:`app.services.balance_at._fold`) as of plan step **D-fold**, above this leaf.
*A fold is a balance; a walk is a fact*, and the two live on opposite sides of the
seam boundary.

```text
LoanEvent = (event_date, kind, payload)

kind = CHARGE      standing += (interest, escrow)    (one per accrual period)
     | ASSERTION   balance := anchor_balance         (the opening + every true-up;
                                                      clears what stands)
     | PAYMENT     balance -= split(cash).principal  (settled transfer shadows,
                                                      then the plan's projections)

walk_loan_ledger(loan, scenario) = replay(facts, seeded at 0.00)
replay_loan_stream(facts + projections) = the same replay, the whole timeline
```

**Since plan step recurrence:R16-c-1 the stream is ONE list, past and future**
(ruling **R-R90**): the seam appends a loan's forward plan -- its projected
rows and estimated occurrences -- to a copy of the recorded stream, behind
every fact, and :func:`replay_loan_stream` replays it from the same seed the
posted ledger's walk uses, the origination.  A projection cannot be placed
ahead of a fact (:func:`projection_boundary`), so the fact prefix of that
replay IS :func:`walk_loan_ledger`'s output and the two cannot disagree on a
recorded payment.

## Why it is a leaf, and what depends on it

The posting ledger (:mod:`app.services.loan_posting_service`) is the GENERAL
ledger -- the balance sheet, the statements, the attribution.  It is NOT the
answer to "what do I owe".  It projects THIS walk into the balanced corrections
it reconciles onto the chart of accounts, which is what makes the posted rows a
re-derivable projection of the loan's facts rather than a second opinion about
them.  The dependency runs one way::

    loan_posting_service (the general ledger)  ->  loan_ledger (the walk)
    balance_at (the read seam)                 ->  loan_ledger (the walk)

The walk lived INSIDE the posting package until step B0, which put the general
ledger in the position of owning the answer to "what do I owe" and left every
other consumer reaching through its privates for the split.  Moving it here is
what lets the ledger become a CHECKED PROJECTION of the walk
(``sum(postings) == fold(ACTUAL events)``, plan step E1) instead of the source of
truth it was mistaken for.  A consumer holding a walk cannot reach a balance from
any public name here -- the fold that would is seam-private -- which is why the
walk needs no fence (plan step D-fold).

## The modules

* :mod:`._charges` -- what an accrual period COSTS: one
  :class:`AccrualCharge` per CONTRACTUAL installment from origination, built
  from the loan's :class:`LoanCalendar` (its contract terms), carrying the rate
  period governing it and the escrow in force on its date.  Pure.  Since plan
  step recurrence:R16-c-2 (ruling **R-R100**) every walk's charges come from it
  through :func:`with_contract_charges`.
* :mod:`._replay` -- the ONE replay: charges accumulate, a payment allocates
  against what stands, an assertion resets the balance.  Pure.  **It is a shared
  primitive since plan step X-au-g-2c-3b-2**, when the settled walk here and the
  forward fold in ``balance_at`` were found to be two statements of one rule --
  the third time this arc has found a rule duplicated because it sat on the wrong
  side of the import graph.  The CHARGE and the ALLOCATION are separate values
  since plan step R16-a, because fusing them made the payment COUNT the clock:
  charging a month's interest inside the per-payment step means N payments charge
  N months however far apart they fall.
* ``_split`` -- DELETED at plan step recurrence:R16-c-1.  It copied the
  replay's :class:`PaymentOutcome` field for field into a ``LoanPaymentSplit``;
  the outcome is the record now, for a settled and a projected payment alike.
* :mod:`._events` -- the event stream: which of the loan's rows are facts, which
  date governs each, and what figure each carries.  Reads no clock, and decides
  no order -- that is :mod:`._replay`'s.
* :mod:`._walk` -- the running-balance replay over that stream (the loan's FACTS:
  per-payment splits and per-anchor corrections), the payment-split view of it,
  and the visible-date re-key of its events (:func:`dated_deltas`, step E1a --
  shared by the seam's fold and the posting writer's checked-projection assert
  so neither carries its own copy of the one clock).  Renamed from ``_fold`` at
  step D-fold, when the fold (the balance) left for the seam and only the walk
  (the facts) remained.
* :mod:`._visible` -- WHEN each fact becomes countable (the ONE clock).
  Chronology only: every name here returns a ``date``, never a figure.  It held
  the owner's CALENDAR and the date-to-period locator until plan step **C2-d**,
  which moved that question to :mod:`app.services.pay_calendar` -- see that
  module's own header for why all three names went at once.
* :mod:`._installments` -- WHICH contractual payment each of a loan's rows
  satisfies (plan step **balance:X-bl-2a**), and which monthly schedule slot it
  consumes when two collide.  The payment feed's DATE half: the amount-free
  producer :func:`app.services.loan_payment_service.get_payment_history` is
  built on, so a consumer that needs only the chronology -- the schedule
  replay's confirmed feed is the whole of it -- stops loading the amount model
  to get it (finding **N-432**).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes, no commits, and
-- since plan step **C2-d** -- **no database session of its own**.
``_visible.owner_pay_periods`` was the one query this package ever issued, and
C2-d deleted it.  *That is narrower than "the package is pure", and the
narrower claim is the true one*: :func:`walk_loan_ledger` still takes ids and
reaches :mod:`app.services.loan_loaders`, which holds the session, and
:func:`payment_visible_on` still takes an ORM row.  What changed is that no
name here OWNS a query and no public name returns an ORM
:class:`~app.models.pay_period.PayPeriod` -- which is what retires the fence
caveat :mod:`._visible` records.  All money is
:class:`~decimal.Decimal`.  Depends on the models, the money and date utilities
(:mod:`app.utils.money` / :mod:`app.utils.dates`), the loan LOADERS, and the pure
engines (``loan_resolver`` / ``escrow_calculator`` / ``rate_period_engine``) --
never on a consumer package, so nothing it needs can import it back.  Two
dependencies this list used to name are gone: ``account_projection`` at plan
step D1b (the leaf had been importing a kind CLASSIFIER to reach its own
chronology primitive) and :mod:`app.extensions` at C2-d, with the primitive
itself.

Plan step **B1** (shipped at ``e227de08``).
"""

from ._events import (
    confirmed_shadows_through,
    loan_event_stream,
)
from ._installments import (
    PaymentInstallment,
    payment_installments,
)
from ._walk import (
    LoanAnchorCorrection,
    LoanLedgerWalk,
    compute_loan_payment_splits,
    dated_deltas,
    load_loan_stream,
    replay_loan_stream,
    walk_loan_ledger,
)
from ._charges import AccrualCharge, LoanCalendar
from ._calendars import LoanCalendars
from ._replay import (
    LoanCashEvent,
    LoanEventStream,
    LoanReplay,
    LoanResetEvent,
    PaymentOutcome,
    ResetOutcome,
    projection_boundary,
    replay_loan_events,
    with_contract_charges,
)
from ._visible import (
    anchor_visible_on,
    payment_visible_on,
)

__all__ = [
    "AccrualCharge",
    "LoanAnchorCorrection",
    "LoanCalendar",
    "LoanCalendars",
    "LoanCashEvent",
    "LoanEventStream",
    "LoanLedgerWalk",
    "LoanReplay",
    "LoanResetEvent",
    "PaymentInstallment",
    "PaymentOutcome",
    "ResetOutcome",
    "anchor_visible_on",
    "compute_loan_payment_splits",
    "confirmed_shadows_through",
    "dated_deltas",
    "load_loan_stream",
    "loan_event_stream",
    "payment_installments",
    "payment_visible_on",
    "projection_boundary",
    "replay_loan_events",
    "replay_loan_stream",
    "walk_loan_ledger",
    "with_contract_charges",
]
