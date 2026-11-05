"""Shekel Budget App -- Ledger Report Service (Build-Order Step 5).

Reads the owner's double-entry ledger into two financial statements -- an
income statement (:func:`compute_income_statement`, pay-period AND calendar
month/year windows) and a balance sheet (:func:`compute_balance_sheet`, as-of a
date, with a trial-balance tie-out).  The ledger they read is the one worked
out from the records (:func:`app.services.ledger_lines.worked_out_lines`,
balance:X-ew's second leaf, plan task plan#2771), never the stored journal.

## Package layout

* :mod:`._types` -- the frozen result shapes (``StatementWindow``,
  ``StatementLine``, ``TrialBalanceTieOut``, ``IncomeStatementReport``,
  ``BalanceSheetReport``); Decimal-only.
* :mod:`._attribution` -- the shared read core BOTH statements consume (the
  one read of the owner's lines and their chart's labels, the per-key net,
  the class sectioning and the natural-balance presentation), so the reader
  contract (M3) is defined once and the statements articulate automatically.
* :mod:`._income_statement` -- ``compute_income_statement``.
* :mod:`._balance_sheet` -- ``compute_balance_sheet``.

## The reader contract (M3, recorded durably)

Whole bookings in, never lone legs (C-1: a line's legs share its paycheck and
its day); pay-period windows take the lines filed in that paycheck (C-2);
calendar windows and as-of dates take the lines by their civil day, the
owner's (C-3); no residue to drop, a worked-out ledger holding none;
presentation by natural balance via ``ref_cache.ledger_class_is_debit_normal``,
no ``-abs`` normalization (C-4); retained earnings and accumulated change in
value derived, never booked (C-5).  Sections by each chart key's class and
labels from its live record (ruling plan#2518).  See :mod:`._attribution` for
the full rule.

## Balance-seam posture

The report functions are statement AGGREGATES over the WHOLE chart, not the
single-account balance-at-T the seam owns -- a caller that needs one account's
balance at a moment routes to ``balance_at``, never to a statement.  That is
why these fold the ledger's lines freely: a statement is the general ledger's
own presentation of what it books, and the sections articulate only because
they are read together (the trial balance ties).  Pulling ONE line out of a
balance sheet to answer "what is this account worth on date T" is the misuse
this posture exists to name -- that answer comes from the seam's fold, not
from a statement (plan Section 3).

**Not W9909-scoped, and that is a recorded gap, not a ruling** (finding N-35):
a new public function here answering balance-at-T would rate 10.00/10 with
every gate silent, exactly the N-28 shape the completeness registry closes for
the other ingredient packages.  Scoping it is its own step.

Reads only, Flask-isolated: plain ids in, frozen reports out; no writes, no
commit.
"""

from ._balance_sheet import compute_balance_sheet
from ._income_statement import compute_income_statement
from ._types import (
    BalanceSheetReport,
    IncomeStatementReport,
    StatementLine,
    StatementWindow,
    TrialBalanceTieOut,
)

__all__ = [
    "BalanceSheetReport",
    "IncomeStatementReport",
    "StatementLine",
    "StatementWindow",
    "TrialBalanceTieOut",
    "compute_balance_sheet",
    "compute_income_statement",
]
