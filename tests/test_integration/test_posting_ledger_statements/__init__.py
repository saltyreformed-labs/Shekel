"""The confirmed-ledger statements oracle (Build-Order Step 5, Commit 13).

The correctness gate for the READ side of Step 5: the two confirmed-ledger
statements (:mod:`app.services.ledger_report_service`) -- an income statement
(pay-period AND calendar windows) and a balance sheet (as-of a date, with a
trial-balance tie-out) -- read against a second opinion re-derived from the
worked-out lines and the source tables.  It shares those lines with the readers,
so it is independent of their attribution and signing, not of the lines, which
the source tables and hand-computed figures grade.  This is the FOURTH
reconciliation oracle, the read-side
sibling of the account-anchor write oracle
(the ``test_posting_ledger_account_anchor_reconciliation`` package); together they pin the
Step-5 promise that the trial balance closes app-wide and ``A = L + E`` is
checkable end to end.

**Non-tautological by construction**, in the same three independent ways the
other posting-ledger oracles are:

  * **hand-computed literals** -- every expected statement line, section total,
    net income, and tie-out figure is the test author's arithmetic over the
    seeded anchors and settled amounts, owing nothing to the reader under test
    (e.g. a $1000 opening + $3000 salary - $400 groceries - $50 uncategorized -
    $150 transfer out => Checking asset 3400.00);
  * **an independent balance-sheet re-derivation** (:func:`_independent_bs`) --
    a genuine second opinion that groups the worked-out lines
    (``tests/_ledger_reads.py``, plan step plan#2913) by chart CLASS directly (a
    different path than the reader's source-bucket
    attribution), signs each class from a test-local debit-normal set (NOT the
    ref-cache flag the reader signs by), and derives retained earnings from the
    Income + Expense debit nets, so a classification / signing / retained-earnings
    bug in the reader cannot hide;
  * **structural invariants** the readers must satisfy regardless of any single
    figure -- the accounting identity ``A = L + E`` at multiple as-of dates,
    ARTICULATION (income net over a window + that window's equity corrections
    equals the equity delta between the bounding balance sheets), and
    period-vs-calendar agreement on an aligned fixture.

Every account and settle is produced through the REAL go-forward primitives --
``create_account`` (the C6 opening sync), ``create_settled_cash_transaction`` /
``create_settled_transfer`` (the status seam + posting builder),
``create_loan_with_trueup`` + a settled payment (the Step-4 loan split),
``_true_up_at`` (the anchor-true-up chokepoint's two steps) -- so every
reconciled statement was posted exactly as production posts it.  The one
non-production affordance is pinning an anchor row's ``created_at`` (via
:func:`_true_up_at`, the C8 technique) so a true-up's civil-day partition is
deterministic regardless of the test clock.  Sources are pinned to noon-UTC
paid dates so their storage (UTC) civil date and their display-timezone
attribution date coincide -- except the dedicated display-timezone case, which
pins an 8:05pm-Eastern Dec-31 settle precisely to prove the two diverge as L9
requires.

Every fixture asserts the read's trial balance is zero
(:func:`_assert_ledger_self_consistent`).  All money is ``Decimal`` from strings, with the arithmetic
shown per the testing standard.
"""
