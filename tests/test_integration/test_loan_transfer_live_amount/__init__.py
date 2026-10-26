"""Integration: a derive-from-loan transfer's cash amount is DERIVED.

Commit 5 of the loan rate-period work.  A recurring loan-payment transfer
flagged ``derive_from_loan`` is worth the loan's current monthly payment (P&I +
escrow), and an escrow change reflows that amount WITHOUT regenerating the
transfer -- the stored ``Transfer.amount`` stays put.

**The MECHANISM changed at plan step X-au-g-2c-2 and the CLAIM did not**, which
is why the figures below are untouched.  A read-time OVERRIDE
(``LoanPricing.live_cash``) used to supersede each shadow's stored figure; a
shadow stores no figure at all now -- it declares ``PARENT_TRANSFER`` and is
priced by amount rule 4 -- so the same arithmetic arrives through the amount
model and there is no stale copy left for an override to beat.  Every case here
asks :func:`_derived_cash`, which is what a SCREEN reads for a transfer's legs
(``cash_ledger.leg_amounts_by_key``, the grid's since plan step
``balance:X-bi-6-1``).

**The rows priced are the payment's LEGS since plan step
balance:X-bi-6-4d-2** (ruling **R-BAL167**; each case's class is in the
commit that made it).  A transfer is one row since that step and no shadow is
born, so a case that priced the loan's shadow rows with
``cash_ledger.amounts_by_id`` prices the legs read off each transfer
(:func:`_loan_payment_legs`), each worth ``resolve_transfer_amount`` on its
parent (**R-BAL13**), and a case that found the loan-side shadow finds the
TRANSFER due that day.  No figure moved.

Every monetary expectation is hand-computed with the arithmetic shown.
"""
