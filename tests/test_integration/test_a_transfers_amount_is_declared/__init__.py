"""Integration: a transfer shadow's amount is its parent's, structurally.

Plan step **X-au-g-2c-2**, rulings **R-FI**, **R-IN** and **R-IO**.

**Transfer Invariant 3 was MAINTAINED, and this step made it STRUCTURAL.**  A
shadow held a COPY of its parent's figure, and two hand-written repairs kept the
copy true -- ``transfer_service._update``'s propagation and ``_restore``'s drift
corrector, which logged a warning and rewrote the copies that got away.  From
X-au-g-2c-2 until plan step balance:X-bi-6-4d-2 deleted the shadows, a shadow
DECLARED ``PARENT_TRANSFER`` and stored no figure at all, so the two could not
disagree: ``ck_transactions_amount_ownership`` refused a shadow that held both,
and the value was READ rather than copied.

**Ruling R-IN (developer, 2026-09-01)** is why this covers EVERY transfer
shadow rather than loan payments alone: a loan payment's shadow IS a transfer
shadow and both declare the same relation, so the narrow version needed four
conditionals and a writer on the loan-settings routes that the wide one does
not need at all.

**Ruling R-IO (developer, 2026-09-01)** is the other half: *the figure the owner
types always wins*, and it must reach the P&I / escrow split as the cash, so an
under- or overpayment is allocated against what was really paid.

On production this moved ``$0.00``: measured 2026-09-01 at stamp
``a4c6f1d92b73``, all 350 shadows already equalled their parent to the cent and
``budget.loan_payment_settings`` held zero rows, so the loan arm is graded here
on a seeded mortgage.

**There are no shadows to ask since plan step balance:X-bi-6-4d-2.**  A
transfer is one row, its two legs are read off it (**R-BAL13**: each worth
``resolve_transfer_amount`` on the parent), and no row can name a transfer:
``ck_transactions_names_no_transfer`` refused one (ruling **R-BAL258**) until
plan step balance:X-bi-6-5 dropped the column itself.  So the step's end state
is the one this module argued for, with the copy deleted rather than derived: a
claim this package's cases asked of each shadow is asked of the transfer, or of
its legs through the producers that price them, with its figures unchanged
(ruling **R-BAL167** class 1).  The one case that planted a shadow -- a figure
on it -- asserted the database refused it (class 2) until that step deleted it
with the column (ruling plan#1850).  Three claims were not re-asked, and each
case says so:
the two AD-HOC cases' ownership reads -- the retype case's and the
owner-stated amount case's -- which the transfer's own CHECK makes infallible
on an ad-hoc row, a refusal replacing the first; the two FORM cases' per-leg
period reads, because a leg's period IS its transfer's (``leg_of``); and the
per-leg "stores nothing", because no leg row is left to store anything.
"""
