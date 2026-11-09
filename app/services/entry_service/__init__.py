"""
Shekel Budget App -- Transaction Entry Service

CRUD operations, validation, and computation for individual purchase
entries on entry-capable transactions.  This service is the foundation
consumed by the balance calculator (Commit 3), entry credit workflow
(Commit 4), mark-paid logic (Commit 5), and all entry UI (Commits
7, 8, 10).

**The OUTSTANDING SET left this module at plan step X-f2-c1** for
:mod:`app.services.reconcile_service`: "which of these had the bank taken by
the day you asserted a balance for" stopped being a question about ENTRIES
when ruling R-EW widened it to bills, envelope closes and transfer shadows.
That module carries the rule and the measurement that forced the cut.

**It became a PACKAGE at plan step X-f3b**, when ruling **R-FM**'s write-door
work took the module past pylint's 1,000-line ceiling.  That ceiling is a
forcing function rather than a budget (findings **N-152**, **N-201**,
**N-270**): the answer is a shape, not a fourth round of shaved prose.  The
shape was already there --

* :mod:`._doors` -- what a user may DO to one purchase: the guards a mutation
  must pass, create / update / delete, and the re-derivation every door
  triggers.  It writes;
* :mod:`._sums` -- what a SET of purchases adds up to, and the contexts a
  screen renders from those sums.  It does not;
* :mod:`._removals` -- what removing each purchase would withdraw from the
  statement matches, the purchase list's captions (plan step
  ``credit_card:CC-5-4a-5``).  It reads and never writes;
* :mod:`._details` -- what one new purchase says, and which account that
  names (the CC tick's card since plan task plan#2930).  It reads and never
  writes.

-- and the arrow runs ONE way, ``_doors`` reading the reductions in
:mod:`._sums` and ``_sums`` reading nothing back.  (It named
``_sums.compute_actual_from_entries`` until plan step X-au-c3, which deleted
that helper: the sum it computed is now the row's own ``purchases``-basis
settlement, stated once in
:func:`app.services.row_valuation.purchases_total`.)

**Every public name is re-exported here**, so the split moved no call site:
``entry_service.create_entry`` and ``entry_service.entry_list_view`` resolve
exactly as they did.  That is the property that makes a package the cheap
answer to the ceiling and a sibling module the expensive one.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

from ._details import EntryDetails
from ._doors import (
    _resolve_owner_id,
    create_entry,
    delete_entry,
    get_entries_for_transaction,
    resolve_owner_id,
    update_entry,
)
from ._refusals import deleted_row_purchase_refusal, removal_refusal
from ._removals import (
    PurchaseControls,
    PurchaseRemoval,
    purchase_controls,
    purchase_removals,
)
from ._sums import (
    build_entry_lists_dict,
    build_entry_sums_dict,
    compute_entry_sums,
    CHARGE,
    REFUND,
    compute_remaining,
    entry_list_view,
    pct_complete,
    purchase_amount,
)

__all__ = [
    "EntryDetails",
    "build_entry_lists_dict",
    "build_entry_sums_dict",
    "compute_entry_sums",
    "CHARGE",
    "REFUND",
    "compute_remaining",
    "create_entry",
    "delete_entry",
    "deleted_row_purchase_refusal",
    "entry_list_view",
    "get_entries_for_transaction",
    "pct_complete",
    "purchase_amount",
    "PurchaseControls",
    "PurchaseRemoval",
    "purchase_controls",
    "purchase_removals",
    "removal_refusal",
    "resolve_owner_id",
    "update_entry",
]
