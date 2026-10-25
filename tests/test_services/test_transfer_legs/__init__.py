"""A projected transfer's legs are DERIVED from the parent, and the readers say so.

Plan step **balance:X-bi-6a**, ruling **R-BAL13** (developer ruling
**R-BAL38**).  Until this step every balance reader loaded a still-projected
transfer's two SHADOW rows and priced each through amount rule 5 (*a shadow is
worth its parent*).  Now the readers derive one
:class:`~app.services.transfer_legs.TransferLeg` per side from the parent
row in ``budget.transfers`` and no reader folding a projection reads a
projected shadow at all.

**The firing controls these cases held until plan step balance:X-bi-6-4d-2.**
Wherever Transfer Invariants 1-3 held, the fold's answer was the same whether it
read the shadow or the parent -- 0 of 350 shadow rows drifted on the
2026-09-15 production snapshot -- so a before/after diff of every figure the
seam answers could not show which relation is read.  Firing controls wrote past
Transfer Invariant 4 -- a shadow soft-deleted alone, a shadow's due date moved
alone -- and asserted the fold followed the PARENT, ruling **R-JA**'s direction
(the parent is the value's one home).  A transfer came with two shadow rows
until that step made it one row (ruling **R-BAL258**), so the parent is the one
relation left to read: the soft-delete control was deleted there (ruling
**R-BAL313**), and the due-date control keeps its transfer's day alone
(``test_the_plan_half.py``'s ``test_a_leg_lands_on_its_transfers_due_date``).
"""
