"""R4b-1: a generate pass reads the OWNER's schedule, not the caller's window.

Both recurrence engines took ONE ``periods`` list and used it for two unrelated
jobs -- the schedule each rule was RESOLVED against, and the set of periods the
pass could WRITE into.  ``period_population`` hands them only the newly created
periods, so every schedule extend re-read every rule as though the owner's pay
history began at the new batch.

Three measured consequences, all live on production (2026-08-08, against a
streamed clone of ``shekel-prod-db``), one class each below:

* **D22** -- a ``Monthly First`` rule re-fired in a month it had already
  covered: 3 spurious ``Phone Allowance`` rows, $118.62, one per extend landing
  a new period in a covered month.
* **D25** -- ``calculate_paycheck`` received the same truncated list as its
  ``all_periods``, so third-paycheck detection read 1-3 periods instead of 61.
  One salary row was STORED $502.45 below its true net pay.  The read-time
  recompute (the read-time repair X-au-d deleted) kept that figure off every
  surface, so what it endangered was the stored cache the grid's inline editor
  pre-fills from -- see the migration's docstring for the measurement.
* **D2** -- a rule's chosen start period was not in the window, so the opening
  bound it states was dropped.

**What was shown RED against the pre-R4b-1 code, and by what means.**  The
authority is the production path, not a ported test file: a ``git worktree`` at
``HEAD`` driving ``pay_period_admin.extend_pay_periods`` six times over a
streamed clone of ``shekel-prod-db``, against the same six on this code.

```text
                              HEAD          this code
duplicate Monthly First months   3 NEW           0
salary rows disagreeing with a
  whole-schedule recompute       1 ($502.45)     0
```

Two of the classes below were additionally ported back onto the old
``periods``-list signature and run at ``HEAD``, where they fail on their own
assertions: ``TestTheStartPeriodBoundSurvivesTheWindow`` generates two rows
into paychecks the rule's start period excludes, and
``TestThePredictionIsTheGenerationCall``'s predictor names all ten periods
where the engine fires in five.

**A textual port is lossy, and saying so is the point.**  An earlier draft of
this docstring claimed all four defects had been shown red that way.  Two of
them had not: the port's own rewrite had left a ``NameError``, and a test that
fails to import is not a test that fails.  An adversarial review caught it by
redoing the port.  The classes with no ``HEAD`` counterpart at all --
``TestTheWindowStillNarrowsWhatIsWritten``, ``TestAWindowMustBelongToTheOwner``,
``TestOneScheduleReadServesTheWholeRequest``,
``TestRegenerateSweepsOnlyWhatItRewrites`` and
``TestABoundedRuleDoesNotRestartItsCount`` -- are forward guards over shapes
this step created or an adversarial review named, not reproductions of a
measured defect, and they are labelled as such rather than counted as
evidence.

All money is ``Decimal`` from strings.
"""
