"""Plan step ``credit_card:CC-5-4a-1``: the payment MOVEMENT is the matcher's subject.

Ruling **R-CC43** (developer 2026-09-21): *the MOVEMENT is the subject of every
settled match on every screen; a row is a candidate only while Projected.*
A settled row's money is its covering movement (ruling **R-BAL80**), on
whichever account the money moved through -- the card, for a checking bill
paid FROM the card (``CC-5-3``) -- so the movement is what a statement shows,
what the screen offers (a SETTLEMENT, on the row's terms) and what an accepted
act records; a Projected row is offered as itself, and its kept payment (a
reverted row's, ruling **R-CC42**) where the row is not -- and since ruling
**R-CC137** (plan step ``credit_card:CC-5-4a-5``, leaf 5c-2a) a Projected row
whose kept payment is on another account is withheld on its own screen and
named there instead.  Ruling **R-CC46**
(the same day): a payment re-pointed onto another account leaves the acts
naming it, withdrawn and disclosed as a delete's are.

The worked example throughout is the developer's own: GROCERIES / a hotel bill
budgeted on Checking, paid with the card, shown on the card's feed.  Every
figure is asserted from the state re-read through the app's own readers --
the offer set, the accept door, the register, the fold, the ledger, the
claims -- never from the row the test wrote.

The private producers are graded DIRECTLY here -- ``_valuation``'s
constructor and pricer, and ``_candidates``' claims reader -- because the
offer set's own clauses would otherwise mask them (measured by mutation, the
class ``CC-5-3`` named).  The statement-match builders are the one way a
test stages a bank line and a scope as the app does.
"""
