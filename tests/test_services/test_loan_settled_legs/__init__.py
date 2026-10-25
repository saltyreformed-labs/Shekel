"""A loan's SETTLED payments are legs of their transfers: plan step balance:X-bi-6-4b.

The loan family and the contribution readers stopped reading the transfer's
loan-side income SHADOW row at this step and read the to-side LEG of the
transfer instead, through the one join that attaches its covering movement as
its record.  Each case in this package is a state the byte-identical grade on the
2026-09-23 21:17 production dump could not reach, because production holds
none of them:

* **R-BAL139** -- a ``$0.00`` close moves no money, so its leg carries no
  record and no day of its own; it is dated by the installment it skips, and
  the ledger books the unpaid charge on that day.
* **R-R107** (amends R-BAL139, plan step recurrence:R16-c-2) -- that
  installment is the payment's INTERVAL's, so a close due off the loan's
  contractual day is dated by the installment before it; and the walk's
  visibility bound reads the loan's own calendar to place it.
* **R-BAL140** -- the settled half is the plan half's exact complement: a
  payment is PLANNED while its transfer is Projected and its side's money has
  not moved, and SETTLED otherwise.  A status drift no door writes (a twin row
  that disagrees with its transfer) is therefore counted exactly once -- by its
  money when its money moved -- in the loan walk AND in the contribution feed,
  where the old partition counted it twice or not at all.  Since plan step
  ``balance:X-bi-6-4d-2`` a side's money is its record on the TRANSFER and no
  reader reads a twin, and the drift whose money moved -- a DATED side record
  under a Projected transfer -- is refused by the database (ruling
  **R-BAL167** class 2).  Since plan step ``balance:X-bi-6-4d-2`` the twin row
  itself is refused at the write (ruling **R-BAL258**), so the drift whose
  money did NOT move has no row to be written on.
* The posting sync's lineage probe named the transfer of a movement that hung
  off a DEAD shadow, so that movement's postings reversed; since plan step
  ``balance:X-bi-6-4d-2`` no twin can be stored to hang one off, which the
  case asserts.

Every figure is made up (ruling R-BAL132).  The loan is ``$100,000.00`` at 6%
from 2026-02-01, due on the 1st: one month's charge is
``round(100000.00 * 0.06 / 12) = 500.00``.  It originates the month before
the 03-01 installment every payment in this package satisfies, so no earlier
installment stands unpaid: the walk charges every contractual installment
from origination (plan step recurrence:R16-c-2), and a loan from 2026-01-01
would owe 02-01's charge too.  The origination was 2026-01-01 until that
step's leaf restated it under ruling R-R101; every figure stayed.
"""
