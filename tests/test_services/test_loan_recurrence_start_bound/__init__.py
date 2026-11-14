"""C9a: a recurring loan payment cannot start before the loan does.

``RecurrenceRule.starts_on`` is the opening half of a recurrence's validity
window, derived by ``loan_recurrence_sync.sync_loan_payment_start`` (the
opening-only sync plan step R7d-g left of ``sync_recurring_payment_bounds``)
from the loan's FIRST CONTRACTUAL INSTALLMENT.  It was ``start_date`` until plan
step R7c-b, which made the column the rule's FIRST OCCURRENCE rather than a
bound the occurrences were filtered against (ruling R-R16); for a loan payment
billing on a day of the month the two are the same date, because the first
installment IS the first occurrence.

**What it prevents, measured.** Before this bound, ``create_payment_transfer``
built its rule with no start at all and generated across every materialized pay
period, so a mortgage closing 2026-04-15 got payments due 2026-02-01,
2026-03-01 and 2026-04-01 -- three installments for a loan that did not exist.
Each debited the cash projection immediately ($3,220.92 total on the fixture's
10-period window; production materializes ~52), and each became an FU-5 money
erasure the moment it settled: the fold splits a pre-origination payment against
a ZERO balance, so it books $0.00 principal and routes the whole payment to a
Refund Receivable, then the origination anchor resets the balance over it.

All money is ``Decimal`` from strings.
"""
