"""
Shekel Budget App -- The outstanding set: reader, writer, and their ONE scope

Tests for :mod:`app.services.reconcile_service` -- the reconcile step's offer
set (ruling R-DH (d) / the R-M re-ruling, shipped at plan step S1-c) and the
writer that records a tick against it.

**Moved here from ``test_entry_service`` at plan step X-f2-c1** with the code
it grades, and not one assertion changed on the way: the reader's name and
return SHAPE did (``outstanding_purchases`` -> ``outstanding_set``, grouped by
envelope per ruling R-EW), so ``_listed`` flattens the groups back to the id
list every case below was written against.  Grading the same ids through a new
shape is what makes the move provably behaviour-preserving.
"""
