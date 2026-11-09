"""Plan step ``credit_card:CC-5-3``: the settle-with-tender door.

A bill BUDGETED on checking and PAID WITH THE CARD is settled with its
covering movement on the card (ruling **R-CC15**: the row never moves; the
movement's account is where its money moved, **R-BAL75**).  What is graded
here is the verb's own rule -- which account a payment may name, whose it
must be, what the seam books on when none is named (**R-CC42**: the kept
record's account, else the row's, so a revert keeps where the money moved as
it keeps a stated figure), what a "Paid from" correction on a settled row
does to the movement and to both clearing links, and what a statement-side
reader now prices a settled row at (**R-CC40**, half 1: what its movement
moves ON THE ACCOUNT ASKED ABOUT, so a card-tendered bill leaves checking's
offer).

Every refusal is graded by the state it leaves, re-read from the database:
the verb composes its refusals ahead of any mutation, so a refused settle
leaves the row Projected with no movement and a refused correction leaves the
movement where it was.

The worked example throughout is the developer's own (2026-09-21): a hotel
bill on Checking, charged to the card.
"""
