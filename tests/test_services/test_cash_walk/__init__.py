"""Shekel Budget App -- the cash ledger WALK (plan step X-a).

Grades ``app.services.cash_ledger.walk_cash_ledger`` and its visible-day re-key
``dated_deltas``, and the fold-side assertion replay that left it at plan step
X-f3c-1 (``balance_at._assertions.assertion_corrections``) -- the facts a cash
account's balance folds over (step X-b)
and the posting writer will project (step X-d).  The walk is ADDITIVE at X-a: no
production surface reads it yet, so nothing here can move a shipped balance.

Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it.  None is taken from another producer: the whole point of the leaf is
that the shipping projection is WRONG about these cases (findings cash D1-D3), so
grading it against that projection would prove the defect rather than the fix
(plan Section 7.2, finding N-7).

The load-bearing case is :class:`TestTheInstantPartition`.  It reproduces the
shape measured on production 2026-07-25: an assertion at 12:57:08 UTC with two
expenses settled at 13:07:11 and 13:07:18 -- the SAME UTC civil day, ten minutes
later.  A date-keyed partition absorbs them into the assertion and loses their
$108.15 of confirmed cash effect; the instant-keyed walk rides them on top.  The
two are separated by a test that a date-keyed implementation cannot pass, not by
a comment.
"""
