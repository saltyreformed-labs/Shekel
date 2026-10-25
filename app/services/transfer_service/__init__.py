"""
Shekel Budget App -- Transfer Service

The single point of enforcement for all transfer mutations.  Every code
path that creates, updates, or deletes a transfer MUST go through this
package.  Direct ORM manipulation of budget.transfers is forbidden
outside it and the transfer recurrence engine (which delegates
to this service for the final insert step).

The transfer rules are CLAUDE.md's (the end of its Architecture section).  A
transfer is one row, which ``ck_transactions_names_no_transfer`` holds by
refusing a transaction that names one, and the database refuses the states its
sides' payment records may not reach (the side keys, and the deferred rules in
``app/side_band_infrastructure.py`` and
``app/deleted_row_infrastructure.py``).  A settled transfer's amount is
stored once, on the transfer (``settled_figure``, plan step
``balance:X-bi-6-4d-2``), and the side-band rule's figure arm ties it to the
records and the status at commit.  The one rule the database cannot refuse is
kept here: every write of a transfer goes through this package.  Plan step
``balance:X-eu`` makes it structural.

**Why it is a PACKAGE, and it is findings N-152 / N-156 ANSWERED rather
than deferred again.**  This was one module and four ``_transfer_*``
siblings beside it in ``app/services/``.  The module reached pylint's
1000-line ceiling at plan step X-aj1 (**N-145**), was taken to 987 by
extracting the status appliers, and **N-152** recorded that 13 lines of
headroom is not a solution -- naming the structural answer in the same
sentence: *a PACKAGE, one private leaf per verb*.  **N-156** records the
same class for the second module the ceiling split, and **N-201** / the
pay-calendar arc's **P31** record it for a third and a fourth.  Plan step
X-f2-c3 is the change that had to buy the room -- a transfer's settle rule
becomes structural here -- so it makes the shape rather than shaving prose a
fifth time.

Two things follow from the package that a flat module could not have:

* **The four siblings are now genuinely private.**  As
  ``app.services._transfer_status`` and friends they were private to
  ``app.services``, so any module in that package could import them and the
  ``shekel-private-module-import`` checker (W9910) would say nothing.  Inside
  this package the boundary is the one the name always claimed.
* **The W9907 status allowlist NARROWED from a module to a leaf.**  It read
  ``app.services.transfer_service``, and :func:`_module_in_allowlist` matches a
  package prefix, so leaving it would have silently widened the fence over
  eight modules.  It now names :mod:`._create` alone -- the only leaf holding
  the CONSTRUCTOR write (``create_transfer``'s; the second, the shadow
  constructor, went with the shadows at plan step X-bi-6-4d-2) that holds it
  open, which plan step X-aj2 replaces.

**A transfer's SETTLE is a rule of this package, not of the doors that ask for
one, and it has a NAME** (plan step X-f2-c3, ruling **R-FA**).  FOUR doors can
move a transfer into the settled band and exactly one of them froze an
auto-derived loan payment's live cash -- so the same payment booked one figure
through the grid and another through the transfers page, which is finding
**N-219**'s shape on this table.  Two entry points answer it, and the split is
``transaction_service``'s exactly:

* :func:`settle_transfer` is the VERB -- "this transfer reached the bank" --
  and it is what a door that means only that calls.  Its rules are
  :mod:`._settle`'s: the amount, each side's settle day, and whether a submitted
  figure is a human's CORRECTION or the panel's own prefill echoed back.
* :func:`update_transfer` takes an arbitrary field bag and DISPATCHES to the
  same act when one of those fields settles the row, so a fifth door cannot be
  written without the rule.

:func:`leg_settle_amount` publishes what a tick will book on a transfer LEG,
for the screens that must display the figure the verb is about to record (the
reconcile panel's offer and the statement matcher's), as
:func:`record_leg_clearing` records which statement showed one; the verb
itself answers whether a human's figure was booked, so the panel's count and
the column's contents come from one act rather than from two askings.

**The public surface is this module and ``__all__`` is it.**  A leaf is
private; a caller outside the package depends on the names below.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

from app.services.transfer_service._create import (
    TransferSpec,
    create_transfer,
)
from app.services.transfer_service._delete import delete_transfer
from app.services.transfer_service._restore import restore_transfer
from app.services.transfer_service._side_days import SideDay
from app.services.transfer_service._settle import (
    leg_settle_amount,
    record_leg_clearing,
)
from app.services.transfer_service._update import (
    settle_transfer,
    update_transfer,
)

__all__ = [
    "SideDay",
    "TransferSpec",
    "create_transfer",
    "delete_transfer",
    "leg_settle_amount",
    "restore_transfer",
    "record_leg_clearing",
    "settle_transfer",
    "update_transfer",
]
