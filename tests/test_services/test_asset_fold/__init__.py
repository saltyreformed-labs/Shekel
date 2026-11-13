"""X-g1: the MODELLED fold, graded on a hand-computed oracle.

Plan step X-g1 (``docs/audits/balance_architecture/README.md``).  Grades
``app.services.balance_at._asset_fold`` -- the replay plan step X-g2 will point
the three modelled kinds at.  The fold is ADDITIVE here: no production surface
reads it yet, so nothing in this file can move a shipped balance.

**Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it.**  None is taken from a shipping producer, and that is not a style
preference: the shipping producers are WRONG about exactly the cases this file
exists for -- the retired three-source merge rendered $6,315.57 of
net-worth history that contradicts the user's own recorded assertions (findings
N-43 / N-74), and the three modelled kinds answer a DATE with a PERIOD (N-71).
Grading the replay against them would prove the defect rather than the fix
(plan Section 7.2, finding N-7).  The every-period parallel run against those
producers is a separate file (``test_asset_fold_parallel.py``), where DIVERGENCE
is the expected result and each one is classified.

Six rulings are graded here, each with a control a wrong implementation fails:

* **R-L / R-Y** -- ACCRUAL exists only from the LATEST assertion's own day
  forward, for ALL modelled kinds (:class:`TestTheAccrualWindow`).  R-Y is the
  half that is new: an INVESTMENT's or a Property's anchor PERIOD accrues today
  from nothing at all.
* **R-S** -- an ASSERTION always wins and there is no backward model
  (:class:`TestAnAssertionAlwaysWins`).
* **R-T** -- the grain is DAILY, so a balance moves inside a period and a date's
  answer never depends on which other dates were asked for
  (:class:`TestTheDailyGrain`).
* **R-X** -- the cent carry: full-precision accrual, whole-cent crediting
  (:class:`TestTheCentCarry`).  Its sharpest case is the one per-day rounding
  gets permanently wrong -- a sub-half-cent daily accrual.
* **R-R** -- a contribution is partitioned by SOURCE, so a recorded transfer is
  never modelled a second time (:class:`TestTheContributionTier`).
* **R-Z** -- a modelled contribution lands on its payday and stops STRICTLY
  before the latest assertion (:class:`TestTheContributionTier`).
"""
