"""
Shekel Budget App -- Cross-Page Balance Equality Lock (HIGH-01, Commit 11)

The single regression test the developer's two worst reported symptoms
lacked.  Symptom #1 ($160 on the grid vs $114.29 on /savings for the
same Projected envelope expense with cleared entries) and symptom #5
(/accounts matches nowhere) had zero falsifying coverage before this
commit: the three audit-plan-mandated cross-page-equality greps over
the pre-commit ``tests/`` tree returned exit-1 (zero matches), and the
near-miss ``test_checking_detail_matches_grid_balance`` recomputes its
own entries-absent balance instead of rendering a second page (it
passes against the divergent code).

This module's contract: for one Account / Scenario / pay-period
configuration with a Projected envelope expense carrying any
combination of cleared / uncleared / credit entries, every balance-
rendering surface MUST return the identical Decimal:

  1. Grid                    -- ``GET /grid`` + the seam's cash-flow entry
                                ``balance_at.cash_balance_map`` (the FOLD since
                                plan step X-c2b2).
  2. /savings                 -- ``savings_dashboard_service`` +
                                ``GET /savings``.
  3. /accounts cash detail    -- the balance ``GET /accounts/<id>/details``
                                renders, read off its ``data-current-balance``
                                hook (the L6 route-render lock: the ONE surface
                                verified against real route output, not a
                                re-call of the seam beneath it).
  4. Dashboard               -- ``dashboard_service.compute_balance_section``
                                (the pulse hero) + ``GET /dashboard``.
  5. Calendar month-end      -- ``calendar_service.get_month_detail``'s
                                ``projected_end_balance`` at the
                                calendar month-end of the anchor
                                period (the C9-3 boundary invariant
                                guarantees equality with the
                                resolver's anchor-period balance).

Five parameter cases lock the formula, not one number: the F-009
worked example (PT-01 base), zero anchor (E-12 "zero is a value"),
negative overdraft balance, credit-only entries (reservation zeroed
by a same-period credit), and uncleared-floor (the
``max(estimated - cleared_debit - sum_credit, uncleared_debit)``
floor that no cleared/credit drift can squeeze below).

The subtotal-remainder assertion
(:class:`TestSubtotalReconciliation`) closes Q-10 / E-25's same-formula
invariant on ruling R-K's basis: for one ``GridColumn`` per period,
``balance[p] - balance[p-1] == net[p] + period_timing[p] + book_vs_bank[p]``
to the penny, with
the remainder pinned per case as the fixture's own true-up.  When this fails the
grid's subtotal rows and balance row have re-grown the F-002 Pair C / F-004
same-page divergence (the inline ``sum(... effective_amount ...)`` loop the
pre-Commit-10 grid had).

The seam-injection negative-control test
``test_invariant_fails_if_seam_reintroduced`` proves the lock is
load-bearing, not a coincidence: monkey-patching one consumer to
bypass the balance-at seam and report ``effective_amount`` directly
makes the cross-page equality assertion fail, which is the failure
mode the developer needs to see in CI before a regression ships.

Test IDs are C11-1..C11-6 mapping to the remediation plan's
Commit 11 specification.
"""
