"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules of :mod:`app.services.cash_ledger._amounts`,
tested against that module directly.  The cash analog of the loan side's split
tests: given a single row, "how much of this hits the checking balance right
now?"

**These tests MOVED here at plan step X-c2c2a**
(``docs/audits/balance_architecture/README.md``), from
``test_balance_calculator_entries.py`` and ``test_balance_calculator.py``'s
``TestIncomeOverridesSeam``.  They always tested THIS rule; they reached it
through ``balance_at._calculator.calculate_balances``, a PRODUCER that deletes
at plan step X-c2c4, so every assertion read ``anchor - reservation`` and the
anchor arithmetic was scenery.  **Every hand-computed reservation figure is
preserved verbatim** -- the ``5000.00 -`` wrapper is what dropped, and each test
that had one still carries its original arithmetic comment so the two forms can
be diffed against each other.

What did NOT move, and why the split is not "the whole file":

* the STATUS gates (a settled / cancelled / Credit row contributes nothing) and
  the reductions over a SET of rows are :mod:`app.services.cash_ledger._flows`'
  question, and moved to ``test_cash_flows.py``;
* the two ANCHOR-PERIOD tests discriminate a ``_calculator`` branch -- which of
  its two arms calls the shared reduction -- so they stayed with that module and
  died with it at plan step X-g4b.

**Plan step X-g4b then moved C5-8 here too** --
:meth:`TestTheEntriesRelationshipIsNotASeam.test_the_silent_degrade_seam_is_absent_from_source`,
the STATIC guard that the CRIT-01 short-circuit has not been re-introduced.  It
lived in ``test_balance_resolver.py``, which died with ``balances_for``; its
home is the module holding the rule it guards, and its scan WIDENED with the
move (see the method for why that is the guard's own stated logic rather than
opportunism).

The plan's own one-liner said "``test_balance_calculator_entries.py`` (27 tests)
is the three-bucket reservation formula"; tracing measured 18, and the
correction is recorded at the step.

The reservation formula under test::

    posted_debit   = sum(amount where not is_credit and settled_on is not None)
    unposted_debit = sum(amount where not is_credit and settled_on is None)
    sum_credit     = sum(amount where is_credit)

    impact = max(estimated - posted_debit - sum_credit, unposted_debit)

**Which bucket a debit falls in is whether the BANK HAS TAKEN IT** (plan step
X-f3b, ruling **R-FM**), and its history is four answers deep -- each a
narrowing, and the last one a deletion.  It was a stored ``is_cleared``
boolean, carried in every fixture of this package's tests as a third bool in a triple: an
unconditional claim that a purchase was inside the anchor, which its own
account could not always support.  Ruling R-DH (d) replaced it with a DATE
comparison against the account's latest asserted day, and the triples took the
day the bank was seen to take the money.  The developer's bank exports
falsified that comparison (33 of 110 matched movements carry the day the bank
posted them), so ruling R-FL made it the RECORDED statement, with the date rule
as the fallback.

**R-FM then removed the question from this package entirely**, which is the
simplification rather than a fifth answer.  A purchase carrying a posting day
is now a cash movement of its OWN in the walk, so the reservation asks what it
always should have -- has this money left -- and *which statement cleared it*
is asked once, where the money is replayed (see the `_STATEMENT_DAY` comment
in `_helpers.py` for the two tests that grade it).  Two consequences are re-ruled test cases
rather than renames, and both are marked as such in `test_the_posting_day.py`: a purchase the
bank took AFTER the
balance was read now releases the reservation, and the account's assertion
history is not an input at all.  A NULL posting day is still OUTSTANDING, which
is the arm that never moved.
"""
