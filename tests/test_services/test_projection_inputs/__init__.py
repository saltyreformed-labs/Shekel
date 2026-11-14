"""
Tests for the shared projection-inputs helpers (F-22 / Commit 18).

Two flavours of test:

- Equivalence lock (C18-1): the new
  :func:`build_investment_projection_inputs` returns the same
  :class:`InvestmentInputs` as the previous inline
  :func:`calculate_investment_inputs` kwargs splat that lived in the
  three dashboard services.  If a future change drifts the helper
  away from the engine call, this test fails loud.

- Query-builder shape: the deduction-loader helpers return rows that
  match the filter contract the four consumers depend on (active
  profile, active deduction, target_account_id membership).  Uses
  the live test DB.
"""
