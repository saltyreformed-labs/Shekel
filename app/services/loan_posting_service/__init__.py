"""
Shekel Budget App -- Loan Posting Service (Build-Order Step 4 + the read switch)

Posts a loan's confirmed history into the append-only double-entry ledger so its
balance is fully reconstructable as ``-(sum of its linked postings)`` -- the
genesis (opening-equity) design that lets the read switch retire the resolver's
read-time replay of confirmed history.  A loan's ledger is TWO kinds of balanced
correction, both PROJECTED from ONE deterministic running-balance walk
(:func:`app.services.loan_ledger.walk_loan_ledger`) and, since plan step
``balance:X-bi-6-3`` (ruling **R-BAL102**), reconciled by ONE loop
(:mod:`._corrections`) -- so the split and the anchor corrections agree on the
balance interest accrues on, never the drift separate walk implementations
would risk:

* **Payment splits** (:mod:`app.services.ledger_lines._loan_payments`, Step
  4): the real principal / interest / escrow / refund split of each confirmed
  payment, layered as a correction on top of the payment's cash movement (its
  loan-side covering movement's own entry,
  :mod:`app.services._posting_purchases`) so the loan nets to the real
  principal.  A DERIVATION keyed ``(loan_payment kind, the payment's pay
  period, its visible day)`` with NO row link.
* **Anchor corrections** (:mod:`app.services.ledger_lines._loan_anchors`, the
  read switch): the once-per-loan OPENING (``-original_principal`` vs. a
  per-loan opening-equity account) and every user balance TRUE-UP, so the
  from-origination sum-of-postings reproduces the resolver on a trued-up
  loan.  Keyed the same way.

## Where the walk lives, and why it is not here

**This package is the GENERAL ledger -- the balance sheet, the statements, the
attribution.  It is not the answer to "what do I owe."**  The walk every
correction is projected from is :mod:`app.services.loan_ledger`, a LEAF this
package depends on: a loan's balance is a fold over its event stream, and these
postings are a PROJECTION of that fold onto the chart of accounts.  The walk
lived here until plan step B1, which put the general ledger in the position of
owning the balance and left every other consumer reaching through this package's
privates for the split.  The direction now runs one way, which is what made
the posted rows a projection of the fold instead of the source of truth they
were mistaken for (asserted per visible date at every sync from plan step E1a
until plan task plan#3093, which deleted the assert ahead of the store it
graded).

## Package layout

Split by concern (the module outgrew the size limit; mirrors
:mod:`app.services.loan_resolver`).  The public surface is re-exported here so
``from app.services import loan_posting_service`` and ``loan_posting_service.X``
keep working unchanged:

* The TARGET legs -- the per-payment split's and the opening + true-up
  corrections' -- were this package's ``_payments`` and ``_anchors`` until
  plan task plan#2526 moved them into the worked-out ledger
  (:func:`app.services.ledger_lines.loan_correction_targets`), which the
  read sums and :mod:`._corrections` reconciles to.
* :mod:`._corrections` -- the ONE reconcile of both: one posted read over the
  three correction kinds, one delta loop (``reconcile_loan_corrections``).
* :mod:`._sync` -- the UNIFIED per-scenario sync (``sync_loan_postings``: one walk,
  one reconcile), the loan-GLOBAL all-scenarios sync, the duplicate translation,
  and the per-user re-sync (the historical backfill went with the deploy's
  hooks at plan task plan#3077).
* ``_display`` -- the loan-detail page's two surfaces, both read off the
  walk, until plan task plan#3133 moved them to
  :mod:`app.services.loan_walk_reads`, named for what they read.  This
  package answers no balance: the two sum-of-postings balance
  readers it once held (``confirmed_loan_balance_at`` /
  ``confirmed_loan_balance_map``) were DELETED at plan step E1e, having lost
  their last ``app/`` caller when the seam cut a loan's past onto the event
  FOLD (steps C3b1 / C3b3) and its confirmed schedule rows onto the walk (step
  E1d-b); their remaining job -- the independent window the fold and the
  resolver are graded against -- moved to the oracle's own side
  (``tests/_test_helpers.py``'s ``posted_loan_balance_at``), so no public
  balance producer exists outside the ``balance_at`` seam.  The per-payment
  ATTRIBUTION reader that sat beside them (``_reader``, summing each
  payment's posted legs back out of the ledger by shadow) went at plan step
  ``balance:X-bi-6-3``: the split links no row, and the walk the ledger is
  graded against already holds every per-payment figure.  The paid-in-year
  tax / chip figures folded off the postings onto the loan ledger at steps
  C3c / C6c (:mod:`app.services.balance_at`), so this package no longer reads
  them either.

## Shared infrastructure and isolation

Books through :mod:`app.services.posting_service`'s shared balanced-write path
(``_emit_balanced_entry``), leg DTO (``_PostingLeg``), and linked-ledger resolver
(``_ledger_account_for``), so an unbalanced entry can never be written and every
source shares one leg convention.  The reconcile primitives shared with the
Step-5 account anchor package (``delta_legs`` / ``summed_posting_legs`` /
``posted_correction_legs`` / ``merge_target_legs`` / ``emit_correction_deltas``,
which owns the union-the-keys loop and the ``emit_correction_entry`` call
inside it / the owner resolver) live in :mod:`app.services._posting_reconcile`,
so the two correction packages can never drift on the delta math, the
correction-entry shape, or the reconcile loop itself.  Reuses the resolver's OWN pure primitives
(``resolve_periods`` / ``monthly_due_date``), so the posted ledger and the
resolver can never drift on the rate path or the anchor boundary.  Flask-isolated:
plain data in, plain values out; flushes but never commits (the caller owns the
transaction boundary).

**Write status.**  Both halves are wired at the go-forward chokepoints via the
unified :func:`sync_loan_postings` (loan-params create / edit, the balance
true-up, the ARM rate / origination-rate change, and every transfer settle /
revert / edit / delete / restore / endpoint move), so a loan's opening,
true-ups, and confirmed payments are all posted as they happen.  A delete or
an endpoint move needs no reversal BEFORE it (the split links no row a
delete could sever): the sync the door runs AFTER finds the vacated key with
no target and reverses it.  None of them checks the posted ledger against the
fold any more: the step-E1a assert that did went at plan task plan#3093,
ahead of the doors plan#2914 stops writing the store from.

**Read status.**  No displayed balance reads these postings.  The read switch
that once pointed at them is retired: the balance seam folds a loan's past from
SOURCE events (steps C3b1 / C3b3) and its confirmed rows from the walk (step
E1d-b), so a cold or stale posting cache is a repairable inconsistency, never a
read outage.  Nothing reads them: the balance sheet and statements did until
plan task plan#2771 moved them onto the worked-out ledger.
"""

from ._sync import (
    resync_user_loan_postings,
    sync_all_scenarios_or_duplicate,
    sync_loan_postings,
    sync_loan_postings_all_scenarios,
)

__all__ = [
    "resync_user_loan_postings",
    "sync_all_scenarios_or_duplicate",
    "sync_loan_postings",
    "sync_loan_postings_all_scenarios",
]
