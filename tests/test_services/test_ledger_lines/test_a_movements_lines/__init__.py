"""Tests for a movement's lines on the worked-out ledger.

A dated movement books two lines (:func:`app.services.ledger_lines.purchase_target`,
read by :func:`app.services.ledger_lines.worked_out_lines`): a settled ordinary
transaction's covering movement or dated purchase books its account against
its category, ``+`` under an income parent and ``-`` under an expense, and each
side of a settled transfer books its own account against the owner's
Transfers-in-transit account, so transit nets to zero over the pair (rulings
**R-BAL45**, **R-BAL80**, **R-BAL101**).

These were the store's writer's unit tests (``posting_service``) until plan
step plan#2914 deleted the writer: each case whose figures were also read off
the worked-out ledger kept them, and lost the writer's call and the stored
entries it read; each whose subject was the writer itself (an entry's shape or
count, a repeat sync, a chart row's minting, a balanced-write refusal) went
with it.  What they pin, with hand-computed arithmetic:

  * **Sign** -- the cash line follows the parent's TYPE (income ``+``,
    expense ``-``) and a transfer's direction (from ``-``, to ``+``), never the
    account's class.
  * **The figure** -- a settled row's recorded actual and a transfer's
    settled figure, never the estimate or the nominal amount.
  * **What books** -- a dated DEBIT movement under a contributing parent; a
    card purchase leaves through its CC Payback; an un-dated purchase is in
    flight and books nothing.
  * **Where it books** -- a line is filed in the paycheck and on the day its
    movement records, so a revert-and-move leaves the old paycheck with no
    line and a re-settle books into the new one.
  * **The records oracle** -- the per-account figure the read states equals
    an independent query over the dated movements
    (``tests/_records_oracle.py``'s ``settled_transfer_effect`` /
    ``posted_purchase_effect``).

Every account's net is ABSOLUTE: its opening and true-ups are corrections on
the same read, so a case's account net is its opening (the seed Checking's
``$1000.00``, the package's Savings' ``$100.00``) plus what moved after it.
"""
