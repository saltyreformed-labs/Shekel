"""What removing each purchase would WITHDRAW -- the purchase list's captions.

Plan step ``credit_card:CC-5-4a-5``, rulings **R-CC80** (developer
2026-09-23, "Every door warns": *"The purchase X and all three card-payback
buttons name the bank line before you press, using the same data the button
acts on, as the popovers do"*), **R-CC127** (the press sends back what its
page named) and **R-CC132** (developer 2026-10-04, "Refuse, shown first": *"On
the companion page a matched purchase has no X"*).  Two controls on an
envelope's purchase list take a movement off the books and can leave a bank
line unexplained: the X, which deletes the purchase -- and, on the envelope's
last card purchase, the CC payback with it -- and the edit form's CC un-tick,
which deletes that payback alone
(``entry_credit_workflow.sync_entry_payback``).  The list names what each
would free before the press, from this read.

**The package's third leaf, and the only one that READS the matches.**
:mod:`._doors` writes and :mod:`._sums`' reductions read no table; this asks
the statement matches and the live paybacks, ONCE per screen however many
purchase lists the screen draws (the grid draws every envelope's, through
:func:`~app.services.entry_service._sums.build_entry_lists_dict`), by
:func:`app.services.match_withdrawal.pending_for_each`.

**The same derivation the press makes.**  Each removal is the movements the
door hands the removal act -- the purchase, and the payback's movements where
the payback goes -- with the rows the press deletes, and whether the payback
goes is :func:`app.services.entry_credit_workflow.payback_deleted_by`, the
read twin of the sync's own delete arm, over the live payback
:func:`app.services.credit_workflow.active_paybacks` names exactly as the
sync's :func:`~app.services.credit_workflow.get_active_payback` does.

Architecture:
  - No Flask imports.
  - Reads; never writes.
"""

from dataclasses import dataclass

from app.models.transaction import Transaction
from app.services import credit_workflow, entry_credit_workflow, match_withdrawal
from app.services.match_withdrawal import MatchWithdrawal


@dataclass(frozen=True)
class PurchaseRemoval:
    """What the purchase list's two removing controls would withdraw for one purchase.

    Attributes:
        own: What taking the purchase ALONE out of its matches frees.  A
            companion's list withholds the X where this frees a line (ruling
            **R-CC132**): the line is the purchase's own, so the list can say
            so before the press.  It is also what the CC tick's MOVE frees
            (plan task plan#3142: ``movement_account.re_point_movement``
            takes the purchase alone out of its matches), so the owner's edit
            form names these lines under the tick and posts them back.
        delete: What the X frees -- the purchase's own matches and, where it
            is the envelope's last card purchase, its payback's, which the
            same press deletes.  ONE read over both removals, so a match
            naming the purchase and the payback together is counted once,
            as the press withdraws it once.  The owner's X names these lines
            (ruling **R-CC80**) and sends them back (ruling **R-CC127**).
        uncredit: What un-ticking CC frees -- the payback's matches alone;
            all zeroes when the un-tick deletes no payback.
    """

    own: MatchWithdrawal
    delete: MatchWithdrawal
    uncredit: MatchWithdrawal


@dataclass(frozen=True)
class PurchaseControls:
    """What one purchase list's removing controls may say, and to whom.

    Attributes:
        removals: :func:`purchase_removals` over at least the list's
            purchases -- the screen's one read, shared by every list on it.
        owner_viewing: Whether the person the list is drawn for owns the row
            (rulings **R-CC130** / **R-CC132**): only the owner may be shown
            the owner's bank lines, so only the owner's X and CC un-tick name
            them, and a companion's X is withheld over a purchase whose own
            line it would free.  Decided from the SESSION's user by the
            route, never from a posted field: the phone card's ``can_edit``
            comes back from the browser, and a crafted one must not draw the
            owner's statement for a companion.
    """

    removals: "dict[int, PurchaseRemoval]"
    owner_viewing: bool


def purchase_controls(
    rows: "list[Transaction]", viewer_id: int,
) -> "dict[int, PurchaseControls]":
    """Return each envelope's :class:`PurchaseControls`, over ONE read.

    The one place the purchase lists learn both what each control frees and
    who is looking -- for the grid's every envelope
    (:func:`~app.services.entry_service._sums.build_entry_lists_dict`) and
    the fragment's one (``routes.entries._render_entry_list``) alike.

    Args:
        rows: The rows a screen draws (:func:`purchase_removals`' reason for
            taking every one).
        viewer_id: The id of the user the screen is drawn for -- the row's
            owner, or a companion (ruling **R-CC11**).

    Returns:
        ``{row id: PurchaseControls}``.
    """
    rows = list(rows)
    removals = purchase_removals(rows)
    return {
        row.id: PurchaseControls(
            removals=removals, owner_viewing=row.user_id == viewer_id,
        )
        for row in rows
    }


def purchase_removals(
    rows: "list[Transaction]",
) -> "dict[int, PurchaseRemoval]":
    """Return what removing each purchase of *rows* would withdraw, in ONE read.

    Args:
        rows: The rows a screen draws, ``purchases`` accessible.  Every row,
            not only those that track purchases: the purchase list also
            draws a refusal on a row that tracks none (the add form refused,
            a crafted id), and its purchases -- none, nearly always -- are
            answered like any others.

    Returns:
        ``{purchase id: PurchaseRemoval}`` for every purchase of every row
        in *rows*.
    """
    rows = list(rows)
    paybacks = credit_workflow.active_paybacks([row.id for row in rows])
    removals, leaving, doomed_ids = {}, {}, set()
    purchase_ids = []
    for row in rows:
        for entry in row.purchases:
            purchase_ids.append(entry.id)
            removals[(entry.id, "own")] = [entry]
            doomed = entry_credit_workflow.payback_deleted_by(
                row, {entry.id}, paybacks.get(row.id),
            )
            if doomed is None:
                continue
            doomed_ids.add(entry.id)
            removals[(entry.id, "delete")] = [entry, *doomed.entries]
            removals[(entry.id, "uncredit")] = list(doomed.entries)
            leaving[(entry.id, "delete")] = [doomed]
            leaving[(entry.id, "uncredit")] = [doomed]
    pending = match_withdrawal.pending_for_each(removals, leaving)
    nothing = MatchWithdrawal(matches=0, lines=(), kept_rows=0)
    return {
        entry_id: PurchaseRemoval(
            own=pending[(entry_id, "own")],
            # A press that deletes no payback removes the purchase alone, so
            # its X frees exactly the purchase's own matches.
            delete=(
                pending[(entry_id, "delete")] if entry_id in doomed_ids
                else pending[(entry_id, "own")]
            ),
            uncredit=(
                pending[(entry_id, "uncredit")] if entry_id in doomed_ids
                else nothing
            ),
        )
        for entry_id in purchase_ids
    }
