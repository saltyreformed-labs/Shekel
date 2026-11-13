"""
Shekel Budget App -- Pay Calendar Derivation Tests (plan steps C1 and C4-c)

``pay_calendar.derive_periods`` is the ONE answer to "which paycheck, and how
far does it run", and since plan step ``pay_calendar:C4-c`` it is the only one:
that step dropped ``budget.pay_periods.end_date`` and ``period_index``, so
nothing stores a second opinion.  This suite is what holds the derivation to
its hand-computed values.

*It was the suite half of a two-dataset proof until then.*  C1 required the
derivation to reproduce the stored columns before anything read it, wrote it,
or dropped them, so ``tests/manual/verify_pay_calendar_derivation.py`` drove
the shared oracle over a clone of production while this file drove it over
schedules the live data cannot supply.  The comparator, its payday control and
its verdict went with the columns, and so did that script; the proof they were
is in migration ``b7a41e2c9d63``'s docstring, measured on production itself.

What is left is the half that was never about the columns:

* the shapes the live data does not supply -- a one-day period, a 90-day
  cadence, a payday jump, a thirteen-day period, a single-payday schedule,
  paydays handed in out of order, a mid-schedule cadence change, and the empty
  calendar (``IRREGULAR_SHAPES``, each with hand-computed expected values);
* what the derivation REFUSES, which a clean database never exercises;
* the writer's output at seven cadences spanning the whole storable range (1,
  2, 7, 14, 30, 90, 365 -- ``ck_pay_schedule_cadence_range`` permits 1..365 and
  C4-c removed the stored-column floor that excluded 1), each pinned to a
  hand-computed end on BOTH branches;
* **re-derivation stability** -- whether yesterday's answer still holds after
  another payday is written.  That is the axis on which a derived end behaves
  differently from the stored column it replaced, and no other test here asks
  about it;
* the CADENCE control, which measures the one branch a regular schedule cannot
  show;
* **the boundary arithmetic plan step ``C14-c`` corrected**, which needs a
  payday that MOVES.  **Those cases used to SUBSTITUTE the producer and now
  state a displacing convention on the rhythm they pass** -- plan step
  ``C14-e-3`` shipped the displacement, so ``rhythm_of(cadence, shift)``
  reaches the same mechanism through the real function and the double it
  replaced is DELETED.  A substitution that simulates a shipped producer is a
  fence with a subject, and the subject went when the producer landed.  The
  candidate window, the end rule and the selector are still what is graded,
  rather than the arithmetic estimate alone.  Their control is
  ``test_the_OLD_end_rule_would_NOT_have_tiled``: the deleted
  ``start + cadence - 1`` agrees with the surviving rule everywhere no payday
  moves, so a suite that never displaced one would pass against the defect --
  which is what made ``C2-a``'s first ``P14`` test vacuous.

**No date here is read from a clock.**  Every date is a literal or is derived
from one by explicit ``timedelta`` arithmetic; nothing calls ``date.today()``
or ``display_today()``, so these pass identically under
``TZ=Pacific/Kiritimati`` and under the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).  The derivation has no clock, and a test that
gave it one would be testing the fixture.

**The boundary-arithmetic cases reach a package-PRIVATE module on purpose.**
``covering_projection`` and ``project_period_after`` live in
``pay_calendar._projection`` and have no caller outside the PACKAGE, so
exporting them would widen its curated surface for a test's convenience.
The predicate that surface is drawn on is the package boundary, which is also
what the W9910 gate (``shekel-private-module-import``) enforces; that gate
runs over ``app/``, ``scripts/`` and ``tools/pylint/shekel_checkers/``, not
over ``tests/`` (``tests/manual/`` is held to E and F alone), and three parts
of the ``tests/test_services/test_spending_report_service/`` package reach a
sibling package's ``_window`` the same way.  ``projected_payday`` was
package-private too until plan step ``C14-d`` gave it an application caller;
it is imported publicly, and ``pay_period_batch.requested_paydays`` and
``registration_service._reject_impossible_first_payday`` call it.
"""
