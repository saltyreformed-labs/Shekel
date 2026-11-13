"""
Shekel Budget App -- Income Service Tests (C17 / F-20 / MED-06 / F-032).

Grades what ``app.services.income_service`` says a salary profile pays:
the paycheck engine's own figure, priced per payday with its raises
applied and under its own year's tax rules, and the one figure every
consumer of it reads.  Each part's module docstring names which of
these it grades.

C17 wrote this file for ``income_service.get_current_gross_biweekly``,
the raise-aware gross helper, and plan step salary:R14-b deleted the
helper: one figure read at one moment cannot show a raise landing
between two paychecks.  ``TestThePerPeriodGrossIsTheENGINES``, in
``test_the_engines_gross.py``, grades the property C17 protected on the
feed that replaced it.

Test fixture math (hand-computed):

- one pay entry of ``$4,000.00`` a paycheck (``$104,000`` a year over 26)
  + 3% one-time raise effective 2026-03
- Post-raise pay a paycheck: ``4000.00 * 1.03 = 4,120.00`` ->
  ``Decimal("4120.00")``, the raise rounding to the cent (ruling R-SAL60),
  on a paycheck whose pay period opens on or after 2026-03-01
- Pre-fix (no raise applied): ``4,000.00`` -> the pre-Commit-17 value the
  off-engine sites returned.
"""
