"""The full-edit popover settles like every other door (plan step X-ap).

**Finding N-219: a transaction had THREE settle doors and one booked a
different figure.**  The grid's Mark Paid button and the reconcile panel's tick
both call ``transaction_service.settle_transaction``, which settles an
envelope-tracked row at the sum of the purchases recorded against it.  The
Status dropdown on the full-edit popover did not: it flipped the status through
the status SEAM -- the mechanics primitive -- and reconciled the ledger, never
consulting the entries, so the row booked ``COALESCE(actual, estimated)``: its
BUDGET.

Measured on the production clone this suite's figures are taken from: row 2231
*Gas*, budget ``$80.00``, one logged purchase of ``$48.98``.  Mark Paid booked
``$48.98`` and returned ``$31.02`` of unspent budget to the projection; the
dropdown booked ``$80.00`` and returned nothing.  ``$31.02`` of spend that never
happened, from two controls in the same card, and the double-entry ledger
followed whichever was used.

What these pin, in the order the request runs them:

  * the dropdown books the ENTRY SUM, and the ledger agrees (the money control);
  * the two doors agree, asserted by settling the same shape through each;
  * a row already INSIDE the settled band is not re-settled -- ``Paid ->
    Settled`` is an archive of a row whose amount is already a fact;
  * a settled status that contradicts the row's TYPE is refused rather than
    silently substituted, and the dropdown does not offer it;
  * the Actual box is rendered exactly when the settle would honour a figure
    typed into it (ruling **R-FF**), and a submitted one is refused otherwise;
  * the settle-DAY box beside it is rendered exactly when there is a day to
    state, which is what makes a settled row's day correctable in place.
"""
