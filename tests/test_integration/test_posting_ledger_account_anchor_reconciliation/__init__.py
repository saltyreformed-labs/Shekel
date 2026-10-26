"""The account-anchor reconciliation oracle (Build-Order Step 5, Commit 8).

The correctness gate for the WRITE side of Step 5: every NON-loan account
(checking, savings, investment, property, interest-bearing, non-loan liability)
posts an OPENING equity correction for its earliest ``AccountAnchorHistory`` row
and a TRUE-UP correction per later row, mirroring the shipped loan genesis
pattern.  After this the trial balance closes app-wide and every linked ledger
sums to an ABSOLUTE balance.  This oracle validates the POSTED ledger against an
independent second opinion re-derived from the SOURCE tables
(``budget.transactions`` + ``budget.account_anchor_history``), never against a
displayed balance and never against the walk / reconcile that produced it.

The absolute invariant, per non-loan account A in scenario S::

    linked_ledger(A, S) == latest_anchor(A)
                           + SUM(net of A's settled sources in S whose
                                 SETTLE DAY is STRICTLY AFTER the latest
                                 assertion's BUSINESS DAY)

An anchor is a CLOSING-BALANCE fact for its own civil day (ruling **R-DH (a)**):
the engine excludes settled items from every period's sum because the anchor
already reflects the settled activity of the day it is about
(``apply_anchor_true_up``: "the user is declaring 'my real checking is now $X'
-- every past-dated debit purchase is already in that number").  So sources
dated ON OR BEFORE the latest assertion's day are ABSORBED into the opening /
true-up deltas and only a strictly LATER day rides on top.

**Both sides of that comparison are stored DAYS, and this oracle was written in
INSTANTS end to end until plan step X-f1** (ruling **R-EC**).  The source side
is ``transactions.settled_on`` -- a transfer side's read off that side's RECORD
(``transaction_entries.settled_on``; off its shadow until plan step
``balance:X-bi-6-4d-2``, ruling R-BAL167 class 4) -- with NO fallback: an undated
settled row is a broken invariant the engine refuses, and this oracle asserts
the same rather than substituting a pay-period start.  The assertion side is
``account_anchor_history.observed_on``, and "latest" orders on
``(observed_on, created_at, id)``: the BUSINESS day first, because
``observed_on`` has been user-supplied since plan step 2, so a balance asserted
for an earlier day but typed later is not the current one.  A rename would have
left this oracle restating the OLD rule against the NEW engine, which is worse
than leaving it broken; each rule is re-derived instead.

A period-granular reading would mis-state the balance sheet by every pre-true-up
settle in the anchor period (the plan review's CRITICAL-1); the
``TestAbsoluteInvariantPerAccount`` fixture pins that exact regression.

**Non-tautological by construction**, the same three independent ways the Step-3
cash oracle is (the ``test_posting_ledger_cash_reconciliation`` package):

  * **hand-computed literals** -- the expected ledger totals and correction
    deltas are the test author's arithmetic over the seeded anchors and
    amounts (e.g. anchor 500, spend 200 pre-true-up, true-up to 350, spend 100
    post-true-up => linked ledger 250.00), owing nothing to any producer;
  * **independent cross-table queries** -- the ledger side
    (``_independent_linked_ledger_sum`` / ``_linked_ledger_sum_as_of``) reads
    ``account_postings`` and the source side
    (``_independent_post_assertion_source_effect``) reads ``transactions`` and
    the transfers' side records with
    independently-written Python / SQL, so asserting the two reconcile checks
    what the producers WROTE against the transaction source of truth;
  * **the production service helpers** -- ``account_posting_total`` and
    ``settled_transfer_effect`` / ``settled_transaction_effect`` must match the
    hand-computed literals too.

Every account and every settle is produced through the REAL go-forward
primitives -- ``create_account`` (fires the C6 opening sync),
``create_settled_cash_transaction`` / ``create_settled_transfer`` (the status
seam + posting builder), ``apply_anchor_true_up`` (the true-up chokepoint) --
so every reconciled row was produced exactly as production produces it.  The one
non-production affordance is pinning an anchor row's ``created_at`` (via
:func:`_assert_balance_at`) so the civil-day partition under test is deterministic
regardless of the test clock or timezone -- the same technique the C5 unit suite
uses.  Assertion instants are always built RELATIVE to the factory origination
row's stored ``created_at`` (the one instant the test cannot choose).

Two adversarial cases prove the oracle is not vacuous: tampering the latest
anchor balance makes the per-account reconciliation FAIL under the real sweep
helper, and injecting one extra leg makes the trial balance go non-zero.  All
money is ``Decimal`` from strings, with the arithmetic shown per the testing
standard.
"""
