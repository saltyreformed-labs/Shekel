"""The cash-transaction reconciliation oracle (Build-Order Step 3, Commit 8).

The correctness gate for the ordinary-transaction half of the double-entry
posting ledger.  Build-Order Step 2 posts settled **transfer** shadows; Step 3
backfills and posts settled **cash** (ordinary, non-transfer) transactions, so a
real account's linked ledger now accumulates BOTH sources at once.  Reads stay on
the ``balance_at`` seam over ``budget.transactions`` (Step 3 changes no read
path), so -- exactly as in Step 2 -- the ledger is validated against the SOURCE
transaction rows, never against a displayed balance.  The invariants below are
plan Section 6:

  1. **Per linked account (cash side).**  For each real account A (its linked
     ledger account), the net of A's posting legs equals
     ``settled_transfer_effect(A) + posted_purchase_effect(A)`` -- the
     combined effect of A's sides of settled, non-deleted transfers (each
     side's record, off the transfer since plan step ``balance:X-bi-6-4d-2``)
     AND every
     dated movement of an ordinary transaction (plan step ``balance:X-bi-4a``,
     ruling **R-BAL80**: a plan row posts nothing of its own).  Through
     ``X-bi-3e`` the second term was ``settled_transaction_effect``, the signed
     ``effective - Sigma(credit entries)`` (``+`` income / ``-`` expense), where
     ``effective`` was what the row RECORDED as having moved
     (``posting_reads.settled_figure_clause``) -- it was
     ``COALESCE(actual_amount, estimated_amount)`` until plan step X-au-c3 made
     a settled row's figure its own record rather than a fallback to its plan.
  2. **Per counter account (category / fallback / orphan).**  For each non-linked
     (Income/Expense) ledger account CA, ``SUM(postings on CA)`` equals the
     negation of the signed effects of the transactions whose legs CURRENTLY
     reside on CA -- identified by the ``journal_entries.transaction_id``
     **linkage**, NOT by ``category_id`` matching.  The linkage formulation is
     load-bearing for an **orphan** (a deleted category's former ledger account):
     the transactions that posted to it now read ``category_id IS NULL`` -- so a
     ``category_id`` match could no longer find them, while the
     ``transaction_id`` linkage reconciles the orphan against exactly the
     transactions whose legs landed on it (see the ``ledger_account.py``
     "Reconciliation of orphans" note).  It is equally load-bearing through a
     **recategorize** (A -> B): the transaction's reversal nets it to zero on A
     (excluded by the non-zero-net guard) and posts its effect to B.
  3. **Per-entry balance.**  Every journal entry's legs ``SUM(amount) = 0`` and
     ``COUNT(*) >= 2`` (also DB-enforced by ``ck_account_postings_balanced``).
  4. **Trial balance.**  ``SUM(account_postings.amount) = 0`` across the whole
     ledger (follows from 3, asserted directly as a cheap self-check).
  5. **Per-transaction completeness.**  Every settled, non-deleted, non-transfer
     transaction whose family holds a NONZERO dated cash movement has at least
     one journal entry -- no settled cash transaction is silently unposted.  A
     zero-effect row (an envelope closed from card purchases alone) is
     correctly NOT required to post; so is a settled row the seam wrote no
     mirror for, whose family is empty (that the seam writes one is
     the ``test_covering_movement`` package's pin, not this file's).
  6. **Multi-scenario isolation** and **owner isolation** (via
     ``journal_entry.user_id``) -- a posting carries no ``user_id``; its owner is
     reached only through its journal entry, and one owner's / scenario's
     reconciliation never picks up another's.
  7. **Revert-and-recategorize reconciles (the plan Section 2.8 CRITICAL
     regression lock)** -- driven through the real PATCH route, then swept.

**A "backfill == go-forward" invariant was listed here and its case is DELETED**
(plan step X-f1; see the note above ``TestRevertAndRecategorizeReconciles``).  It
compared the ``posting_service`` Python builder against the raw-SQL Commit-7
migration backfill on one transaction.  **Losing it lost something real and
saying so is the point**: it was the only check that an INDEPENDENT
implementation of the sign / amount / date rules agreed with the go-forward
builder, and the two surviving "backfill == go-forward" tests elsewhere
(``test_posting_ledger_account_backfill.py``, ``test_loan_posting_backfill.py``)
reuse the go-forward builder, so they are true by construction.  It went because
the SQL it drove reads a column the head revision drops, not because it was
redundant.

Two adversarial cases prove the oracle is not vacuous: tampering a settled
transaction's covering movement makes the per-account reconciliation FAIL --
driven through the real ``_assert_full_reconciliation`` sweep helper under
``pytest.raises``, so a regression in the helper itself is caught, not only in an
inline re-derivation (a real ledger drift would be caught) -- and injecting one
extra leg makes the trial balance go non-zero (the ``= 0`` assertion is a real
check, not one the per-entry trigger makes unconditionally true).  A reverted
transaction reconciles at zero (original + reversal net to zero; the source-side
query drops its kept movement once the revert UN-DATES it), proving the
append-only correction discipline end to end.

**Non-tautological by construction**, the same three independent ways as Step 2:

  * **hand-computed literals** -- the expected ledger sums are the test author's
    arithmetic over the seeded amounts (e.g. Checking pays $50 and receives
    $2,000 of cash plus sends $350 of transfers, so its ledger MUST be exactly
    ``+1600.00``), owing nothing to either producer or any service helper;
  * **independent cross-table queries** -- the ledger side
    (``_independent_ledger_sum`` / ``_ledger_account_sum``) reads
    ``account_postings`` through a different join shape than the
    ``posting_service`` readers, and the source side
    (``_independent_combined_source_effect`` / ``_signed_cash_effect``) reads the
    ``transactions`` and ``transaction_entries`` tables; asserting the two equal
    reconciles what the producers WROTE against the source of truth;
  * **the production service helpers** -- ``account_posting_total``,
    ``settled_transfer_effect``, and ``posted_purchase_effect`` (the readers
    Steps 4-5 will switch balances onto) must match the hand-computed literals
    too.

Cash transactions are settled through the real go-forward primitives -- the
status seam plus the posting builder -- via
``create_settled_cash_transaction`` (the cash analog of the Step-2
``create_settled_transfer``), so every reconciled row was produced exactly as the
mark-done route produces it.  All money is ``Decimal`` from strings, with the
arithmetic shown per the testing standard.
"""
