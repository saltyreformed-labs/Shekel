"""
Shekel Budget App -- Income Service Tests (C17 / F-20 / MED-06 / F-032).

Pins the raise-aware paycheck-engine producer contract:

- The helper returns ``Decimal("0")`` when no active SalaryProfile exists.
- The helper returns the profile's pay a paycheck -- its pay list's entry
  since plan step salary:X-av-3a (ruling R-SAL59) -- byte-identical to the
  engine for a no-raise profile.
- The helper APPLIES applicable ``SalaryRaise`` rows so the post-raise
  per-period gross is returned -- the F-032 worked example: $4,000.00 a
  paycheck ($104,000 a year over 26) with a 3% raise effective in the as-of
  period yields $4,120.00 per period, not the pre-Commit-17 off-engine
  $4,000.00.
- Every downstream consumer (savings, year-end, retirement, investment)
  reads the same engine-derived value through the helper for a
  raise-applicable user.

Test fixture math (hand-computed):

- one pay entry of ``$4,000.00`` a paycheck (``$104,000`` a year over 26)
  + 3% one-time raise effective 2026-03
- Post-raise pay a paycheck: ``4000.00 * 1.03 = 4,120.00`` ->
  ``Decimal("4120.00")``, the raise rounding to the cent (ruling R-SAL60)
- Pre-fix (no raise applied): ``4,000.00`` -> the pre-Commit-17 value the
  off-engine sites returned.
"""
