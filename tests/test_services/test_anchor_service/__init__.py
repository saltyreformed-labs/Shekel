"""
Shekel Budget App -- Anchor Service Tests

Unit tests for :mod:`app.services.anchor_service` and its loan half,
:mod:`app.services.loan_anchor_service` (split out at plan step
``recurrence:R20``).  Pins both outcomes of :func:`apply_anchor_true_up` and
its loan twin, ruling **R-EQ**'s duplicate rule, the cash door's report of what
governs either side of its write (ruling **R-CC79**), the stager's one read of
the latest assertion that the report's before now is (ruling **R-CC85**), and
the contract that an unexpected ``IntegrityError`` propagates.

Pre-extraction these branches were covered indirectly by the grid
HTMX-route test suites (``TestTrueUpSameDayDuplicate`` and
``TestTrueUpStaleForm``).  The route suites still exercise the
wiring; these tests pin the helper's contract directly so a future
change to the route cannot accidentally drift the shared semantics.

**The idempotency tests graded a UNIQUE INDEX until plan step X-f1c4b** and
now grade the write-door rule that replaced it: an assertion is refused only
when it changes nothing.  The index answered the double-submit cases the same
way; it answered a re-assertion of a superseded balance WRONGLY, and no test
had asked it -- so each door gained one
(``test_reasserting_a_superseded_balance_is_recorded``).
"""
