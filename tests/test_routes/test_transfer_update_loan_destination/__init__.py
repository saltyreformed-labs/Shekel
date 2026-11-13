"""The transfer UPDATE door takes the create door's two loan-destination rules.

Plan step **R7d-f-4**, plan ledger row **REC-521**, rulings **R-R76** and
**R-R77**.  The create door derives a loan destination's first occurrence and
refuses a stop stated where the loan holds no active payment; the update door
enforced neither, in two shapes: a "does not repeat" transfer into a
payment-less loan given a cadence (no rule, so ``is_standing_loan_payment``
answered ``False`` and the authoring branch wrote the owner's typed start and
stop), and a recurring transfer into savings MOVED onto a payment-less loan
(the identity was judged against savings, then the field loop moved the
column).  Either way the row became the loan's standing payment with an
owner's word in both bound columns.  ``settle_destination_for_update`` now
runs ahead of the recurrence step and settles the definition the edit LEAVES.

**Re-cut at plan step R7d-g-2 under ruling R-R81**: the door derives the
start ONLY where the destination loan holds no active payment; a SECOND
transfer's start is its owner's, required and refused at or before the
loan's origination (``SECOND_TRANSFER_STARTS_AFTER_ORIGINATION``), and its
stop is its owner's on the update door's ordinary reading.  The paid-loan
cases below moved with that ruling and say so.

**Every case reads the stored rule back**, never the redirect alone: a door
that flashed the right sentence and saved anyway, or saved the typed start
beside the right stop, passes a status-code assertion.  Every refusal asserts
the destination column and the rule are exactly as they were.  The dates are
the create door's own (``test_transfer_create_derived_stop``): a mortgage
originating 2026-04-15 with payment day 1 derives its first installment on
2026-05-01, and today is frozen ahead of both.
"""
