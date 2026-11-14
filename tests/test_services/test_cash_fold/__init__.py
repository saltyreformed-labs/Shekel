"""X-b / X-g4a: the cash FOLD, graded on a hand-computed oracle.

Plan steps X-b and X-g4a (``docs/audits/balance_architecture/README.md``).
Grades ``app.services.balance_at._cash_fold`` -- ``balances_at`` at day
grain, and since X-g4a ``period_balances`` over a 52-period horizon.  Both read
the pass's own ``assembled_fold`` since plan step X-i4.

**The fold is no longer ADDITIVE and this header used to say it was** (corrected
at X-g4a).  Plan step X-c2b2 pointed all three cash seam entries at it and
X-g3b the grid, so every figure here is a figure the app RENDERS: the cash-flow
family (``_cash_flow``), the grid's balance row (``_grid``), the modelled
replay's cash base (``_asset_fold``) and the net-worth kernel all read this
producer.  A test in this file that moves is a screen that moves.

**Every expected figure in this package is HAND-COMPUTED and written out in the test that
asserts it.**  None is taken from a shipping producer, and that is not a style
preference: the shipping projection is WRONG about exactly the cases this file
exists for (findings cash D1 / D3, and ruling R-G's absorbed past-due bill), so
grading the fold against it would prove the defect rather than the fix (plan
Section 7.2, finding N-7).  The every-day parallel run against those producers
is a separate file (``test_cash_fold_parallel.py``), where DIVERGENCE is the
expected result and each one is explained.

Three rulings are graded here, and each has a control that a wrong
implementation fails rather than a comment asserting it:

* **R-I** -- before the first assertion the fold BACK-PROJECTS over the records
  (:class:`TestTheOpeningMovesIntoTheSeed`), including both real production
  shapes with the ruling's own figures.  Its
  :meth:`~TestTheOpeningMovesIntoTheSeed.test_at_and_after_the_opening_it_equals_the_zero_seeded_walk`
  is the pin on the one re-derivation the fold performs.
* **R-G** -- a still-Projected row whose date has passed is CLAMPED FORWARD to
  ``as_of + 1``, never absorbed (``test_the_planned_tier.py``'s
  :class:`TestThePlannedTier`), with the ruling's worked Checking figures.
* the **instant partition** -- an assertion covers exactly the settles that
  PRECEDED it, to the second (:class:`TestSettledMoneyRidesOnTheAssertionItFollowed`),
  which is finding cash D1 and the defect the developer hit on a real Money
  Market transfer.
"""
