"""
Shekel Budget App -- Carry Forward Service

Processes the user's "Carry Forward Unpaid" action on a past pay period
(scope doc section 4.6).  Splits the source period's projected
transactions into three categories and applies the right semantic to
each:

  * **Envelope-tracked templates** (``template.is_envelope = True``) --
    settle the source row at ``sum(entries)`` and roll the unspent
    leftover (``estimated - entries_sum``) into the target period.  The
    leftover bumps an existing mutable row, the engine-generated
    canonical, or -- when neither exists (an inactive template like a
    yearly envelope, or a destination whose only row is finalised or
    soft-deleted) -- a freshly created ``is_override`` row carrying the
    leftover.  See ``docs/carry-forward-aftermath-design.md`` Option F
    for the rationale.

  * **Discrete rows** (everything that is not a recurring envelope) -- the
    pre-existing 33cd21e behaviour: relocate the row to the target period
    and set ``is_override = True`` if its definition RECURS, so the
    recurrence engine does not regenerate over the moved row.  Envelope rows
    no rule generated -- ad-hoc, or a rule-less definition's (plan step
    balance:X-bi-7a, ``Transaction.recurs``) -- land here too: they move
    whole and carry their entries, because there is no recurring canonical
    to roll an unspent leftover into.

  * **Transfers** (the source period's still-Projected ``budget.transfers``
    rows, walked as transfers since plan step balance:X-bi-6-4c-2, where the
    shadow rows were walked and de-duplicated) -- delegate to
    ``transfer_service.update_transfer``, which every write of a transfer
    goes through (CLAUDE.md, Architecture): the move writes the transfer's
    period, and each side's leg reads it there.

The whole batch is atomic.  The envelope branch raises a
``ValidationError`` only on the AMBIGUOUS guard -- a destination period
with more than one mutable row for the same (template, scenario), a
corrupt pre-existing state.  The route catches it and rolls back the
session, so no source is settled and no target is bumped on failure.

The module also exposes ``preview_carry_forward`` -- a read-only
inspection that returns one ``CarryForwardPlan`` per source row,
labelling each with the action that ``carry_forward_unpaid`` would
take (settle-and-roll, defer, transfer-move) or the reason it would
be blocked.  The carry-forward modal route renders this preview so
the user can confirm before any database writes happen (Phase 5 of
``docs/carry-forward-aftermath-implementation-plan.md``).

This package keeps the shared period-validate + three-way partition in
``_context``, the read-only preview vocabulary, DTOs, and plan builders
in ``_preview``, and the mutating execution in ``_execute``.  The public
surface (the two entry points, the ``CarryForwardPlan`` / ``CarryForwardPreview``
/ ``SharedClose`` DTOs, and the ``PLAN_KIND_*`` / ``BLOCK_*`` constants) is re-exported
here so ``from app.services import carry_forward_service`` and every
``carry_forward_service.X`` access keep working verbatim.
"""

from ._execute import carry_forward_unpaid
from ._preview import (
    BLOCK_AMBIGUOUS_TARGETS,
    BLOCK_CLOSED_TARGET,
    PLAN_KIND_DISCRETE,
    PLAN_KIND_ENVELOPE,
    PLAN_KIND_TRANSFER,
    CarryForwardPlan,
    CarryForwardPreview,
    SharedClose,
    preview_carry_forward,
)

__all__ = [
    "carry_forward_unpaid",
    "preview_carry_forward",
    "CarryForwardPlan",
    "CarryForwardPreview",
    "SharedClose",
    "PLAN_KIND_ENVELOPE",
    "PLAN_KIND_DISCRETE",
    "PLAN_KIND_TRANSFER",
    "BLOCK_AMBIGUOUS_TARGETS",
    "BLOCK_CLOSED_TARGET",
]
