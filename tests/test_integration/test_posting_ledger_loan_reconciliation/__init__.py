"""The loan-payment reconciliation oracle (Build-Order Step 4, Commit 7).

The correctness gate for the loan half of the double-entry posting ledger.  Steps
2 / 3 posted settled transfer and cash movements; Step 4 layers a balanced
CORRECTION on each confirmed loan payment's Step-2 cash entry that backs the
interest / escrow / (payoff) refund off the loan, so the loan-linked ledger nets
to the REAL principal paid.  Reads still flow through the resolver / ``balance_at``
seam (Step 4 changes no read path), so -- exactly as in Steps 2 / 3 -- the ledger
is validated against the SOURCE (the payments' cash records and the anchor), never
against a displayed balance.  The invariants below are plan Section 8.

  1. **Parallel run vs the resolver (the headline, plan 8.2).**  The ledger's view
     of the current balance is the genesis ``-(sum of the loan's linked-ledger
     postings)`` -- the opening (-original_principal), every true-up, and every
     payment's principal (Step-2 cash plus the Step-4 correction).  The resolver
     derives its balance INDEPENDENTLY -- it
     never reads the ledger; it replays the SCHEDULED payment
     (``principal = period_pi - interest``) forward from the same latest anchor
     and discards the cash entirely (``rate_period_engine.replay_schedule``).  So
     on an ON-SCHEDULE payment (cash == the resolver's ``monthly_payment``, no
     escrow) the two must AGREE to the penny; on an OFF-SCHEDULE payment they
     must DIVERGE by exactly the extra / short principal, and the ledger equals
     the hand-computed REAL balance -- proving the ledger is the more-correct
     record the read switch will move onto (plan Section 10), where the resolver
     needs an anchor true-up.
  2. **Supersedes the cash per-account invariant for loans (plan 8.7).**  The
     Step-2 / Step-3 oracle asserts ``account_posting_total(A) ==
     settled_transfer_effect(A) + settled_transaction_effect(A)``.  That BREAKS
     for a loan once corrections exist: the linked ledger nets to -(current
     balance), not to the cash.  The loan-aware invariant is
     ``account_posting_total(loan) == settled_transfer_effect(loan) -
     per_loan_correction_net`` where ``per_loan_correction_net`` is the sum of the
     per-loan legs -- interest / escrow / excess PLUS the opening-equity legs (the
     negatives of the opening + true-up).  One test drives this where the cash
     invariant provably fails.
  3. **Completeness over the full post-anchor set (plan 8.3).**  Every eligible
     confirmed payment whose split has a non-zero non-principal part carries a
     correction -- no Step-2 cash entry on a loan ledger is left uncorrected; an
     all-principal payment legitimately carries none.  Future-dated settled
     payments (none in these fixtures) are asserted absent, not silently passed
     (they are a read-switch concern).
  4. **Per-entry balance and trial balance (plan 8.4).**  Every journal entry's
     legs ``SUM(amount) = 0`` with ``COUNT >= 2`` (also DB-enforced by
     ``ck_account_postings_balanced``), and the whole ledger -- corrections
     included -- sums to zero.
  5. **Scenario and owner isolation (plan 8.8).**  A correction carries no owner
     of its own; its owner is its journal entry's, and one scenario's / owner's
     loan reconciliation never picks up another's.
  6. **Backfill == go-forward (plan 8.8).**  The historical backfill and the
     go-forward wiring post identical corrections, so a ledger rebuilt by the
     backfill reconciles identically to the go-forward one.
  7. **The DATED posting window parallel run (plan 4-commit-6 / 8.2).**
     Invariants 1-6 pin the posted LEDGER against the resolver via the test's OWN
     unbounded ``-(sum of linked postings)`` query (``_ledger_balance``); this
     pins the ``entry_date``-BOUNDED read of the same postings -- at a point in
     time (``_posted_balance``) and at every period boundary
     (``_posted_period_map``) -- as a THIRD derivation, proven == the resolver
     on-schedule and divergent by exactly the extra / short principal
     off-schedule, including a pre-true-up payment, a mid-life true-up, a
     calendar-year boundary, two scenarios, and the unconfigured -> ``None`` route.
     Both windows are the test suite's own (``tests/_test_helpers.py``): plan step
     E1e deleted the production readers, whose only remaining job was to be graded
     here.
     ``TestPostedLoanBalanceSums`` pins the same posted sums against
     hand-computed literals; this pins them against the independent resolver, so a
     ledger bug a literal happened to share is still caught (the ``+$10`` injection
     below fails these too).

Three adversarial cases prove the oracle is not vacuous: tampering a settled
payment's ``actual_amount`` makes the loan-aware invariant FAIL and the real
sweep helper raise (a real ledger drift would be caught), injecting one extra leg
makes the trial balance go non-zero (the ``= 0`` assertion is a real check, not
one the per-entry trigger makes unconditionally true), and injecting $10 of
phantom interest into the walk fails the parallel-run VALUE checks while the
structural sweep -- an accounting identity -- correctly survives (the executable
form of the "+$10 injection failed 9 of 11 tests" evidence).  A companion guard
(``TestResolverIsLedgerFree``) proves the resolver reference reads none of the
posted ledger, so the parallel run cannot silently become a tautology.

**Non-tautological by construction**, three independent ways -- the same discipline
as the Step-2 / Step-3 oracles.  The SPLIT VALUES are pinned by the first and third
(hand-computed literals and the resolver); the second pins the READERS:

  * **hand-computed literals** -- a $100,000 balance at 6% accrues exactly $500.00
    the first month (``100000 * 0.06 / 12``); the trueup anchor ($100,000) is
    distinct from origination ($250,000), so a correct interest figure also
    proves the walk seeds from the trueup anchor.  These, and the resolver below,
    are what pin the posted interest / principal;
  * **the resolver as an independent oracle** -- the parallel run pits the posted
    ledger against a producer that shares none of its code path and never reads
    the ledger, so an on-schedule agreement / off-schedule divergence pins the
    split VALUE that the internal reconciliation identities (structural, see
    ``_assert_loan_reconciles``) cannot; and
  * **independent cross-table queries** -- the ledger side
    (``_independent_loan_linked_net`` / ``_per_loan_correction_net``) reads
    ``account_postings`` through a different join shape than the
    ``posting_service`` readers, and the source side
    (``_independent_settled_income_cash``) reads ``transfers`` and their loan-side
    records in ``transaction_entries``.  This pins the
    READERS (a scenario-scope or ledger-resolution bug in ``account_posting_total``
    is caught here), NOT the split value -- which the sweep's identities hold
    regardless of.

**Scope (deliberately non-duplicative).**  The per-payment split VALUES
(on-schedule / extra / short / payoff-refund / ARM / escrow-effective-dating) are
hand-computed at the unit level in
``tests/test_services/test_loan_posting_service.py``; the lifecycle wiring
(settle / revert / delete / restore / true-up / rate / N1 back-post) and the
full-cash-reversal CRITICAL regression are driven through their real chokepoints
in ``tests/test_integration/test_loan_posting_wiring.py``; the backfill's
idempotency / coverage / deploy-hook contract in
``tests/test_integration/test_loan_posting_backfill.py``.  This oracle does NOT
re-assert those; it adds the reconciliation-level checks those suites do not make
-- the parallel run against the resolver, the production-wide superseding
invariant + completeness + trial-balance sweep, scenario / owner isolation of the
whole sweep, and the two non-vacuity proofs -- exactly as the Step-3 cash oracle
sits above the cash lifecycle / backfill suites.

Loans and payments are built through the SAME production primitives the other
suites use: ``create_loan_with_trueup`` (the canonical account factory + a
controlled latest anchor) and ``create_settled_transfer`` (the sole transfer
writer, which auto-posts the Step-2 cash entry AND -- via the Commit-5 wiring --
the Step-4 correction).  So every reconciled row was produced exactly as marking a
loan payment Paid produces it.  "today" is frozen to 2026-05-15 (after every
payment period used) so the wiring's and the resolver's ``date.today()`` as-of is
deterministic and every settled payment is historical.  All money is ``Decimal``
from strings, with the arithmetic shown per the testing standard.
"""
