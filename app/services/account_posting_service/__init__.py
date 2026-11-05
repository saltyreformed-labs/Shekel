"""
Shekel Budget App -- Account Posting Service (Build-Order Step 5)

Posts every NON-loan account's anchor assertions into the append-only
double-entry ledger as balanced OPENING / TRUE-UP corrections, mirroring the
shipped loan genesis pattern (:mod:`app.services.loan_posting_service`).
After Step 5, every linked ledger sums to an ABSOLUTE balance and the trial
balance closes app-wide::

    confirmed balance = latest anchor assertion
                        + settled facts recorded after that assertion moment

An anchor assertion is a FACT; modeled growth / appreciation / interest
between assertions is a derivation and is never posted (Option D's
fact-versus-derivation line).  A true-up whose delta is $0 means the app
tracked reality perfectly; a non-zero delta is made visible as an explicit
correction whose COUNTER leg names what the difference was -- interest income,
a change in value, or the equity opening (ruling **R-FO**, plan step X-f3d).

## Package layout

Mirrors :mod:`app.services.loan_posting_service` (split by concern):

* :mod:`._anchors` -- the opening + true-up reconcile: posted-leg read-back
  against the worked-out ledger's targets, one balanced delta per key that
  differs.  The walk that frames each correction
  (:func:`app.services.ledger_lines.walk_account_ledger`, the read fold's own
  assertion replay over the account's settled records, plan task plan#2525)
  and the per-(source kind, pay period, civil date) targets
  (:func:`app.services.ledger_lines.account_correction_targets`) were this
  package's ``_walk`` and ``_anchors`` until plan task plan#2526 moved them
  into the worked-out ledger, which sums the same targets.
* :mod:`._sync` -- the entry points: per-scenario, all-scenarios (baseline
  UNION scenarios with postings on the linked ledger), and per-user.

## Shared infrastructure and isolation

Books through :mod:`app.services.posting_service`'s shared balanced-write
path and the reconcile primitives in
:mod:`app.services._posting_reconcile` (``posted_correction_legs`` /
``emit_correction_deltas``, which owns the union-the-keys loop and the
``delta_legs`` / ``emit_correction_entry`` pair inside it -- all shared
with the loan package, so the two correction families can never drift on the
delta math, the correction-entry shape, or the reconcile loop itself).  Ledger
rows are minted only via
:func:`app.services.ledger_account_service.get_or_create_account_counter_account`,
whose non-loan guard keeps the loan and account correction families on
disjoint charts, and WHICH counter row a correction books into is ruling
**R-FO**'s total dispatch over the account's projection kind
(:func:`app.services.ledger_lines.anchor_correction_counter_kind`).
Flask-isolated: plain data in, plain values out; flushes but never commits
(the caller owns the transaction boundary).

**Write status.**  WIRED as of C6, at eight lifecycle chokepoints: account
create (``account_service.create_account``), the anchor true-up
(``anchor_service.apply_anchor_true_up``), the account-type change
(``routes.accounts.crud.update_account`` -- which was the *direct anchor edit*
until plan step X-f1e deleted that door, and now reaches this package only when
an account is re-typed), the pay-period reset
(``pay_period_admin.reset_pay_periods``) and, since plan step
``pay_calendar:C21``, "Remove earlier paychecks"
(``pay_period_admin.remove_earlier_pay_periods``), both through
``pay_period_admin._refile_ledger``, the effect-time self-heal at the
``posting_service`` sync tails (:func:`self_heal_anchor_corrections`), the
``create_baseline`` recovery path, and the account-type boundary changes
(the crud/type routes re-sync an allowed crossing; the validation guard
refuses one across the loan boundary on an account holding a line).  C7 adds
the deploy-wide historical
backfill (:func:`backfill_all_account_anchor_postings`), reusing the
identical go-forward sync so a backfilled correction is identical to a
go-forward one.
"""

from ._anchors import reconcile_account_anchor_corrections
from ._sync import (
    backfill_all_account_anchor_postings,
    resync_user_account_anchor_postings,
    self_heal_anchor_corrections,
    sync_account_anchor_postings,
    sync_account_anchor_postings_all_scenarios,
)

__all__ = [
    "backfill_all_account_anchor_postings",
    "reconcile_account_anchor_corrections",
    "resync_user_account_anchor_postings",
    "self_heal_anchor_corrections",
    "sync_account_anchor_postings",
    "sync_account_anchor_postings_all_scenarios",
]
