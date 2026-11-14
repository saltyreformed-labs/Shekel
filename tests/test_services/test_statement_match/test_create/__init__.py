"""The CREATE door: a bank line the app has no row for BECOMES a purchase.

Plan step **bank_import:X-f6a-3b**, ruling **R-FS**'s third shape.  **It MOVES
MONEY, and differently from its sibling**: accepting a match re-dates a movement
the app already held, and recording a line here ADDS one it did not have at all.

Measured on the developer's own 2026-08-16 statement against a 2026-08-18
production clone: after every proposal the matcher offers, **91 unmatched
outflows** remain that no match can ever explain, 74 of them card swipes worth
`$3,383.49` -- the app records a
period's groceries as one envelope and the bank records every swipe.

**What the door promises, and what each class in this package pins:**

* the account's recorded outflow GROWS by exactly what the bank took;
* the purchase is born carrying BOTH of the bank's days (**R-FW**);
* the line stops being unexplained, through the SAME match door every other
  correspondence goes through (**R-FT**, **R-FV**);
* the destination set the screen offers is the set the door will accept -- an
  envelope it does not offer is refused, not silently accepted;
* a new envelope is CLOSED from its own purchases, because a projected row in a
  past period is carry-forward bait.

Every refusal in ``test_what_the_door_refuses.py`` is a FIRING CONTROL,
written to fail if the refusal were deleted.
"""
