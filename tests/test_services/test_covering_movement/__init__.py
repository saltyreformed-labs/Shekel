"""A settle writes the movement it records, and the movement follows the record.

Plan step **balance:X-bi-3a**, rulings **R-BAL39** and **R-BAL41**.  A bill
ticked Paid used to record what moved on the bill row alone; an envelope's
purchases were each a row of money that moved.  Since this leaf the MANUAL
branch's settlement is mirrored as ONE covering movement -- a
``transaction_entries`` row carrying the figure the settle booked, WHO wrote
it, the day and how the day is known, and the statement link -- written by the
status seam, the one writer of the settlement record.

**Every case here drives a DOOR** -- ``transaction_service.settle_transaction``
and ``status_seam.apply_status_change`` -- rather than assigning columns,
because the claim under test is what those doors do.  And every case is a
FIRING CONTROL (``docs/plans/verification.md`` standard 4): the balance
equality below is graded against the SAME fold with the movement removed, the
release cases assert the ledger's net moved back, and the gates assert that a
row of the excluded kind holds NO movement.

The shapes under test, and the real act each stands for:

* **the write** -- a bill's tick books its figure as a movement sourced
  ``resolved``, a typed correction as ``typed``, named after the plan, dated
  on the settle day, on the owner's account;
* **balance-neutral by construction** (ruling **R-FM**'s identity): the fold's
  per-day sums and the posted ledger's cash net are identical with and
  without the movement, because the parent's leg nets to zero and the
  movement carries the money;
* **the lifecycle mirrors the RECORD** (plan step X-bi-3e-2, ruling
  **R-BAL61**): a revert UN-DATES the movement and keeps it -- its day pair
  and link released with the row's, the door's family reconcile reversing
  its legs -- while both homes retain what moved; a re-settle re-dates the
  SAME row from the retained record -- ``typed`` for a honoured correction,
  the new price for a re-priced derivation; an envelope closed empty,
  reverted and later given real purchases sums the purchases alone, and the
  ``purchases`` record withdraws the survivor;
* **a kept movement is not a purchase** (ruling **R-BAL68**): every
  purchase-meaning reader -- the fold's reservation, the reconcile panel's
  offer and stamp, carry-forward's leftover, the grid's sums and list, the
  dashboard's progress, the settle's own offer and booking, the statement
  bound -- reads ``Transaction.purchases`` and sees nothing of a reverted
  manual close; the family readers keep ``entries``;
* **the mirror never lowers evidence**: an identity re-submit leaves a
  movement's bank-observed day and link standing; a settle-day correction
  moves the movement's day with the row's;
* **the income arm** (plan step X-bi-3b): a paycheck is covered in its own
  direction -- the fact, the ledger and the family all read ``+figure`` --
  which is the control the plan names for that leaf;
* **the transfer arm** (plan step X-bi-3c): both legs of a settled transfer
  are covered through ``transfer_service``, each in its own direction; the
  ledger books the pair whole and the movements nowhere (ruling **R-BAL45**),
  the reconcile tick's link reaches the leg's movement, a revert un-dates
  both, and an endpoint move carries them, settled or not (rulings
  **R-BAL46**, **R-BAL72**); a loan payment's loan-side movement moves no
  loan figure;
* **the purchase doors' source rule**: a hand-typed purchase is ``typed``, a
  bank-born one ``observed``, a human amount edit ``typed``, a bank
  confirmation ``observed``, a day-only edit unchanged.
"""
