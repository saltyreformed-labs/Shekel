"""
Shekel Budget App -- Transaction Service

Cross-cutting transaction state-change helpers used by multiple
routes and services.  Each function mutates a Transaction in place
and leaves the session/commit lifecycle to the caller, matching the
pattern in ``app/services/entry_service.py``.

**Two settle entry points, and the difference is deliberate.**
:func:`settle_transaction` is ruling **R-FA**'s verb -- what settling a row
MEANS, amount and status and ledger together.  :func:`settle_from_entries` is
the envelope PRIMITIVE underneath it, and it stays public for
``carry_forward_service``, which settles a BATCH and must reconcile the ledger
after its ``no_autoflush`` block, so it owns that act itself.

**Beside them sits :func:`apply_requested_status`, which is not a settle entry
point but the route layer's ONE status entry point.**  A door states the status
the user asked for; that function decides what applying it means and dispatches
a settle to the verb.  It exists because a route was making that decision, and
making it wrong -- see finding **N-219**.

**THREE doors settle a transaction and all three are on the verb** (plan
step X-ap, finding **N-219**).  The grid's Mark Paid calls it, the reconcile
panel's tick calls it since plan step X-f2-c2, and the Status dropdown on the
full-edit popover reaches it through :func:`apply_requested_status` -- which it
did not, until X-ap.  That third door flipped the status through the status
seam and reconciled the ledger, but never consulted the entries, so an
envelope-tracked row with a `$25` purchase against a `$400` estimate booked
`$25` through Mark Paid and **`$400`** through the dropdown, from two controls
in the same card.  Ruling **R-FA** named "two route branches" and there were
three; the census is now closed and stated at :func:`apply_requested_status`.

**The rule this package holds is that a door states an INTENT and the service
decides what it costs.**  Every public function here is one of those decisions,
and none of them is reachable from a template or a form field.

**Why it is a PACKAGE, and it is the SAME finding as `transfer_service`'s.**
This was one module and it stood at 997 of pylint's 1000-line ceiling -- the
FIFTH module in this codebase to reach it after `transfer_service` (**N-152**),
the split that made `pay_period_locks` (**N-156**), `anchor_service`
(**N-201**) and `pay_period_write` (pay-calendar **P31**).  Every one of those
rows says the same thing: three lines of headroom is not a design, and the
structural answer is a package with one private leaf per verb.  Plan step
X-f2-c3 is the change that had to buy the room -- it publishes
:func:`settle_amount`, the figure a tick will book, so the panel and the verb
cannot show and book two different numbers (**N-231**) -- so it
makes the shape rather than shaving prose a fifth time.

FIVE leaves, cut by what each DECIDES rather than by size:

* :mod:`._status_rules` -- which settled status a row's TYPE takes, and what a
  dropdown may therefore offer;
* :mod:`._row_rules` -- what SHAPE a row is and what that shape FORBIDS: the
  two predicates a screen reads before rendering an amount box, and the guard
  every settle door shares.  Split off at plan step X-au-j, when the CC-payback
  refusal pushed ``_settle`` past the cap and this docstring's own sentence
  about headroom decided how;
* :mod:`._settle` -- what settling a row means: the amount, the verb, and the
  envelope primitive.  The amount and the echo rule are asked of a PARENT,
  a row or a transfer (ruling **plan#1983**), so the transfer service's
  settle books and offers through them;
* :mod:`._door` -- what a DOOR's requested status means, which is a settle for
  one direction and the mechanics alone for every other;
* :mod:`._delete` -- what REMOVING a row means: the four-step sequence and its
  order, which existed twice until plan step ``bank_import:X-gb`` gave the
  delete route and ``statement_match``'s undo one verb to share.

**The public surface is this module and ``__all__`` is it.**
"""

from app.services.transaction_service._delete import (
    RowDeletion,
    delete_transaction,
    preview_deletion,
)
from app.services.transaction_service._door import (
    NOTHING_STATED,
    StatedRecord,
    apply_requested_status,
)
from app.services.transaction_service._row_rules import (
    deletion_refusal,
    reject_unsettleable,
    repays_card_spend,
    repays_tracked_purchases,
    settles_from_entries,
)
from app.services.transaction_service._settle import (
    fixed_settle_amount,
    is_correction,
    retained_amount,
    retained_amounts_by_key,
    settle_amount,
    settle_from_entries,
    settle_transaction,
)
from app.services.transaction_service._status_rules import (
    offerable_status_ids,
    reject_mismatched_settled_status,
    settled_status_id,
    settled_status_member,
)

__all__ = [
    "NOTHING_STATED",
    "RowDeletion",
    "StatedRecord",
    "apply_requested_status",
    "delete_transaction",
    "deletion_refusal",
    "preview_deletion",
    "fixed_settle_amount",
    "is_correction",
    "offerable_status_ids",
    "reject_mismatched_settled_status",
    "reject_unsettleable",
    "retained_amount",
    "retained_amounts_by_key",
    "settle_amount",
    "settle_from_entries",
    "settle_transaction",
    "settled_status_id",
    "settled_status_member",
    "repays_card_spend",
    "repays_tracked_purchases",
    "settles_from_entries",
]
