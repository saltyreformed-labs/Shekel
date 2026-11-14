"""
Shekel Budget App -- recurring_view producer tests (Recurring cluster Loop B, P1).

Locks the unified Recurring surface's display-model producer: the summary
band, the three kind-grouped sections with per-section subtotals, and per
row the defined amount, monthly + per-paycheck equivalents, engine-backed
next date, and share of section committed total.

The producer has exactly one monthly source of truth
(``obligations_aggregator.template_monthly_or_none``); the per-paycheck
value is DERIVED from it by
``PayCadence.monthly_to_per_paycheck`` -- the OWNER's cadence since plan step
R7a-2a, a hardcoded ``12 / 26`` before it.
Every expectation below is a LITERAL hand-computed at the biweekly cadence
with its arithmetic in a comment, never a re-derivation through the producer's
own value object -- an expectation computed with ``PayCadence`` would move with
a derivation bug instead of catching it.  Real ORM templates run against the
test DB so the relationship-driven attribute access and ``ref_cache`` lookups
are exercised end to end.

``app.services.recurrence._reading`` is imported as a MODULE, so the cases
that patch the read door (``_reading.resolve``,
``_reading.occurrence_placements``) replace the names its own functions look
up when they run.  Patching a name a test imports would leave the composition
calling the real one.
"""
