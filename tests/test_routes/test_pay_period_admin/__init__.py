"""Route tests for the pay-period management actions (slice f).

Exercises the extend / truncate / regenerate routes end to end through
the HTTP layer: success redirects + DB effects, schema rejection, the
hard-lock and discard-confirm responses, the cadence-persist on generate,
owner-only access (a companion gets 404), and that the settings
"pay-periods" section renders the manage UI.  ``today`` is pinned so the
future-period setup is deterministic.  The slices are named in
``docs/plans/historical/implementation_plan_pay_period_crud.md``, a
historical record that governs nothing.
"""
