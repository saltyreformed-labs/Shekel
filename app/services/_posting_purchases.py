"""The MOVEMENT posting sources: a row's purchases and covering movements as cash.

Ruling **R-FM** (plan step X-f3b), whose whole content is one sentence: *a
purchase that has cleared the bank is a cash posting, and its envelope's close
books only what its purchases did not*.  This module is that source's write
half: the reconcile-to-target emission for one movement.  What makes a movement
postable and what its two legs are -- the movement RULE -- moved into the
worked-out ledger at plan task plan#2526
(:mod:`app.services.ledger_lines`: ``purchase_posts``, ``purchase_target``,
``purchase_source``), which the read sums and this emission posts.  Since plan step
``balance:X-bi-3b`` a bill's or a paycheck's covering movement posts through
it too (a movement's legs are its parent's, ruling **R-BAL35**), and since
``balance:X-bi-6-3`` so does a transfer's (ruling **R-BAL101**): ONE
movement writer, the COUNTER LEG dispatched by the parent's shape.  A row's
movement books against the row's category under the ``purchase`` source; a
transfer LEG's books against the owner's Transfers-in-transit account under
the ``transfer_movement`` source with the ``transfer`` leg kind (ruling
**R-BAL45**'s shape C: two entries per settled transfer, each side on its own
bank day, the transit account netting to zero once both have cleared).  The
parent is what :func:`app.services.transfer_legs.movement_parent` resolves
(leaf ``X-bi-6-4a``): a plan row, or a transfer's leg, never the shadow row
a transfer movement still hangs off.

**Why a module of its own rather than more of :mod:`app.services.posting_service`.**
The purchase was a THIRD posting source beside the transfer and the transaction,
with its own concrete linkage (``journal_entries.transaction_entry_id``), its
own source kind (``ref.posting_sources`` ``purchase``), its own target rule and
its own day; the transfer movement shares the linkage, the rule and the day and
differs in the counter leg, the source kind and the leg kind alone.  Adding
the purchase inline took the writer module 319 lines past pylint's 1,000-line
ceiling; growing past a gate is a signal, and the seam the ceiling was
measuring is exactly this one.  The split follows the sibling-split
convention ``posting_reads`` was created by.

**It holds no public door, deliberately.**  The two doors a caller reaches --
``posting_service.sync_transaction_postings`` (the family reconcile, and since
plan step ``balance:X-bi-4a`` the whole of what an ordinary transaction posts)
and ``posting_service.reverse_leaving_movements`` (the one removal act's
reversal, ruling **plan#2564**) -- stay in the writer module, because both
feed the account anchor self-heal that module owns, and because
``posting_service`` remains the ledger's ONE public surface.
What lives here is what those doors are made of.  (A per-purchase door,
``sync_purchase_postings``, stood beside them with zero callers until that
step deleted it -- ledger row **BAL-507**.)

**The dependency runs one way**: this module imports the balanced-write leaf
(:mod:`app.services._posting_write`) and the worked-out chart
(:mod:`app.services.ledger_lines`), and never ``posting_service`` itself --
the same direction, and for the same cycle reason, that
:mod:`app.services._posting_reconcile` keeps.  Its target rule names chart
KEYS and touches no stored chart row; the balanced-write leaf resolves the
keys to rows at the emit step (plan task plan#2524), and the rule itself is the
worked-out ledger's since plan task plan#2526.

Flask-isolated and commit-free like its consumers: flushes so the caller sees
assigned ids; the caller owns the transaction boundary.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services._posting_write import emit_typed_source_deltas
from app.services.ledger_lines import ChartKey, purchase_source, purchase_target
from app.services.transfer_legs import PlanItem


def posted_purchase_exists_clause():
    """Return the SQL form of "this transaction holds a posted purchase".

    :func:`app.services.ledger_lines.purchase_posts` asks the question of ONE
    loaded row; this asks it of
    a query, as a correlated ``EXISTS`` over ``budget.transaction_entries``.  It
    is the same two narrowings that predicate makes about the purchase itself --
    a recorded posting day, and a DEBIT -- and deliberately not the third: what
    the PARENT contributes is the caller's own filter, because the two callers
    want opposite answers.  ``resync_all_cash_postings`` wants every row that
    could hold a leg; a bulk archive wants the rows it is about to stop
    contributing.

    **It exists because the clause was written twice** (caught by pylint's
    ``duplicate-code`` at plan step X-f3b, which is what that checker is for):
    once in the deploy resync's WHERE and once in the template routes' bulk
    guard.  Two spellings of "can this row hold postings" is the shape ruling
    R-FM's whole family of fixes exists to prevent, one tier up.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transaction``, for use
        in any query rooted there.
    """
    return (
        db.session.query(TransactionEntry.id)
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .exists()
    )


def emit_purchase_deltas(
    entry, parent: PlanItem, *, posted: bool,
) -> "list[JournalEntry]":
    """Emit the delta entries for ONE purchase's own cash leg -- ruling **R-FM**.

    The purchase analog of the row's own-leg reconcile
    (``posting_service._emit_transaction_deltas``, which plan#1845 deleted with
    the source), and deliberately the SAME shape: reconcile-to-target over the
    ``(pay period, entry date)``
    keys the purchase has already posted to, unioned with the one it should
    hold.  That is what makes every lifecycle act on a purchase one call -- a
    posting day recorded, corrected, or cleared; a parent cancelled or
    soft-deleted; an amount edited; a credit flag flipped -- and a repeat sync a
    no-op.  A ``settled_on`` MOVE reconciles as two keys, the old date reversing
    to zero and the new one posting fresh, exactly as finding **N-13**'s
    transaction twin does.

    **The PERIOD is the parent's and the DATE is the purchase's**, which is the
    same two-clock split every cash source keeps: the budget column a purchase
    spends is its envelope's, and the day its money moved is its own.  A
    transfer leg's movement keeps both: the leg's period is its transfer's
    (read off the TRANSFER since leaf ``X-bi-6-4a``, the shadow's through
    ``X-bi-6-3``, equal under Transfer Invariant 3), and the day is the
    side's own bank day (ruling **R-BAL45**).

    Args:
        entry: The movement.
        parent: Its parent as
            :func:`app.services.transfer_legs.movement_parent` resolves it: a
            plan row, or a transfer leg.
        posted: Whether the ledger should hold a cash leg for it
            (:func:`app.services.ledger_lines.purchase_posts`; ``False``
            also means "reverse it", which is what the teardown doors pass).

    Returns:
        The emitted delta entries; ``[]`` when the ledger is already at target.
    """
    targets: "dict[tuple[int, date], dict[ChartKey, Decimal]]" = {}
    if posted:
        targets[(parent.pay_period_id, entry.settled_on)] = purchase_target(
            entry, parent,
        )
    # The PARENT types the legs (plan step X-bi-3b): ``expense`` under an
    # envelope or a bill, ``income`` under a paycheck, ``transfer`` under a
    # transfer leg -- and names the SOURCE the header carries and the
    # reconcile filters on (plan step ``balance:X-bi-6-3``).
    source = purchase_source(parent)
    return emit_typed_source_deltas(
        parent,
        entry,
        targets=targets,
        source=source,
        log_label=f"{source.value} {entry.id} (posted={posted})",
    )
