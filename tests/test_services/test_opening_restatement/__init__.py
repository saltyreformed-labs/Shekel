"""An owner can say when the books opened, and with how much (plan step X-f3c-2b-2a).

**The rule, in one sentence.** ``budget.account_openings`` is append-only and
its latest recorded row governs, so correcting what an account's books opened
with is a NEW row -- and every balance the app renders for that account is
stacked on the figure it names.

**What each class here covers, and why the split is where it is.**

* :class:`TestTheWriter` -- the ONE writer, which both events reach: an
  origination (``account_service.create_account``) and a restatement.  Ruling
  **R-ES** applied one table over, so the owner's write lock, ruling **R-EQ**'s
  did-this-change compare and the audit line are properties of the TABLE.
* :class:`TestTheDaysItRefuses` -- the two bounds a restatement has, and the
  one it deliberately does NOT have.  The movement bound is where this suite
  earns its keep: the service has to refuse EXACTLY what
  ``budget.assert_account_books_hold_its_movements`` refuses, or a submission
  passes the door and aborts at COMMIT with a driver message no surface
  can render.
* :class:`TestTheTwoTiersAgree` -- that equality, graded rather than asserted
  in prose.  Both tiers read ONE SQL statement
  (:data:`app.opening_infrastructure.SETTLED_MOVEMENTS_SQL`); these cases plant
  the rows the two could most plausibly disagree about -- a SOFT-DELETED
  movement (which the balance fold ignores and the constraint counts) and a
  posted PURCHASE (the other half of the movement UNION) -- and pin that
  they do not.
* :class:`TestThePostedLedgerFollows` -- the half a reader would assume and
  should not.  The opening's ledger line is DATED on ``opened_on``, so moving
  the day moves it: the ledger holds it on the new day and nothing on the old.

**Why the movement row set is graded from the WIDE side.** A predicate that
narrowed to the fold's contributing rows would look right and be wrong: a
soft-deleted movement is restored by an ``UPDATE`` of ``is_deleted`` alone, and
the movement trigger fires ``UPDATE OF settled_on, account_id``, so a restored
pre-books row would pass every tier untouched.  The cost is over-refusal, which
is the safe direction, and it is only safe if both tiers over-refuse the SAME
rows.
"""
