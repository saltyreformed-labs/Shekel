"""What a still-planned transfer LEG is WORTH on the screen of the account it is on.

The fourth subject of :class:`~._subjects.RowKind` (leaf
``balance:X-bi-6-4c-1``, rulings **R-BAL87**, **R-BAL106**, **R-BAL158**,
**R-BAL159**): one side of a TRANSFER whose money has not moved, offered as
the transfer itself -- keyed by the transfer's id on the screen's account --
where the matcher offered the transfer's SHADOW row until this leaf.  Its own
module because :mod:`._valuation` crossed the 1,000-line bound when this
subject joined it (ruling **balance:R-IR**: the session that breaks a module
splits it, by SUBJECT); the seam is the KIND.  What a leg is worth
(:func:`leg_price`), what it is called (:func:`leg_candidate_label`), the one
construction the offer set and the re-price share (:func:`leg_candidate`) and
the re-price's LEG arm (:func:`repriced_leg`) live here.  Which side is a LEG
is the loader's (``transfer_legs.offerable_transfer_legs``, ruling
**R-BAL79**: Projected, and this side's money not yet moved); a side whose
money has moved is its dated record, a SETTLEMENT (ruling **R-BAL80**).  A PAID leg is a
SETTLEMENT -- a payment, whose parent is a leg rather than a row -- so its
constructor stays with the other SETTLEMENT's in :mod:`._valuation`, and that
module imports this one, never the reverse.

**No function here reaches a transfer through its shadow row**: a leg arrives
as a ``transfer_legs.TransferLeg`` from ``transfer_legs``' loaders, and its
figure is asked of the leg (``transfer_service.leg_settle_amount``, which
prices it off its transfer and its side's record since plan step
``balance:X-bi-6-4d-2``; through the leg's shadow until then).

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.exceptions import AmountUnresolvable
from app.services import cash_ledger, transfer_legs, transfer_service
from app.services.transfer_legs import TransferLeg
from app.utils.amount_relationships import transfer_pricing_load_options

from ._subjects import CandidateRow, RowKind

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCalendar


def leg_price(
    leg: TransferLeg, basis: "cash_ledger.AmountBasis",
) -> "Decimal | None":
    """Return what settling *leg*'s transfer would book on its account, signed, or ``None``.

    :func:`~._valuation.transaction_price`'s LEG twin (leaf ``balance:X-bi-6-4c-1``): the
    figure is the transfer service's (``transfer_service.leg_settle_amount``,
    the leg price the reconcile panel offers from since leaf ``X-bi-6-4c-2``),
    signed by :func:`~app.services.cash_ledger.cash_leg_of`, which asks the
    leg its direction and contributing gate off its transfer and side.

    **One refusal leaves the candidate set, and it is not swallowed**:
    ``AmountUnresolvable`` is :func:`~._valuation.transaction_price`'s case,
    counted in the screen's "could not be priced" note.  A ``ValidationError``
    was a second, a DAMAGED transfer whose twin pair the leg price refused
    (ruling **R-BAL158**, "Skip it and say so"), until plan step
    ``balance:X-bi-6-4d-2`` priced a leg off its transfer and its side's
    record and the developer ruled such a transfer is offered and settled
    like any other (**R-BAL235**).  The leg price's one remaining refusal,
    a soft-deleted transfer, cannot reach it: the loaders offer live
    transfers only, so one arriving here is a defect to surface, not a row
    to count.

    Args:
        leg: The still-planned leg, its transfer loaded with
            :func:`leg_loads`.
        basis: The PASS's :class:`~app.services.cash_ledger.AmountBasis`,
            threaded for :func:`~._valuation.transaction_price`'s reason.

    Returns:
        Its signed cash effect on the leg's account, or ``None`` when it
        cannot be priced.
    """
    try:
        return cash_ledger.cash_leg_of(
            leg, transfer_service.leg_settle_amount(leg, basis),
        )
    except AmountUnresolvable:
        return None


def leg_loads() -> tuple:
    """Return the loader options a LEG candidate reads, rooted at ``Transfer``.

    What :func:`leg_price` walks (the amount model's chain,
    ``amount_relationships.transfer_pricing_load_options``); the endpoints the
    label composes from and the status ride the transfer row itself
    (``lazy="joined"``).  Stated once for the offer set's arm and the
    re-price, so the two cannot load different chains.

    Returns:
        A tuple of loader options.
    """
    return transfer_pricing_load_options()


def leg_candidate_label(leg: TransferLeg) -> str:
    """Return what to call a transfer *leg* on the review screen.

    The leg's own label -- "Transfer to <account>" on the from side, "Transfer
    from <account>" on the to side, composed from the endpoints' CURRENT
    names (``transfer_legs.leg_label``, ruling **R-BAL87**) -- with the side
    named: two accounts hold a leg each, so a reviewer reading a checking
    statement has to be told they are being offered one side of a transfer.
    Until leaf ``balance:X-bi-6-4c-1`` the name was the SHADOW's stored
    ``name``, written by the same composition when the transfer was made; a
    renamed account now re-labels the leg (the display change leaf
    ``X-bi-6-1`` made on the grid).

    Args:
        leg: The leg being offered, or whose payment is.

    Returns:
        Its display label.
    """
    return f"{leg.name} (transfer leg)"


def leg_candidate(
    leg: TransferLeg, calendar: "PayCalendar", amount: Decimal,
) -> "CandidateRow | None":
    """Return one still-planned transfer LEG as the candidate value every consumer shares.

    :func:`~._valuation.transaction_candidate`'s LEG twin (leaf ``balance:X-bi-6-4c-1``),
    and one constructor for the offer set and the re-price for that
    function's reason.  **The TRANSFER is the subject, keyed by its id on the
    screen's account** (rulings **R-BAL87**, **R-BAL159**): the figure is
    what settling the transfer books on this account (*amount*, from
    :func:`leg_price`), the window is the transfer's paycheck, the door is
    the transfer's (``transfer_id``, read by
    :func:`~._moving._apply_day`), and the REVISION is the transfer's
    counter -- the row a leg's period, status and figure are edited on.
    Until this leaf the candidate was the transfer's SHADOW row on this
    account, a TRANSACTION keyed by the shadow's id and revised by the
    shadow's counter; the shadow is written by the same doors in the same
    edits, so every figure, day and window reads the same.

    A leg states its own figure in the sense this package asks
    (``states_own_figure``): it is neither an envelope worth its purchases
    nor a payback worth another row's spend, so the bank is taken to show it
    alone (:attr:`~._subjects.CandidateRow.not_shown_alone`).  Whether the
    bank's figure may be WRITTEN to it is a different question, answered
    ``no`` by its ``transfer_id`` (transfer invariant 3:
    :attr:`~._subjects.CandidateRow.figure_is_correctable`).

    Args:
        leg: The still-planned leg (its transfer Projected, this side holding
            no dated movement -- ``transfer_legs.offerable_transfer_legs``).
        calendar: The pass's :class:`~app.services.pay_calendar.PayCalendar`.
        amount: Its signed cash effect, already resolved by :func:`leg_price`,
            taken for :func:`~._valuation.transaction_candidate`'s reason.

    Returns:
        Its :class:`~._subjects.CandidateRow`, or ``None`` when the leg is
        worth nothing or its transfer's pay period is not one this calendar
        carries -- neither is offerable, and neither is an error.
    """
    if not amount:
        return None
    period = calendar.period_by_id(leg.pay_period_id)
    if period is None:
        return None
    return CandidateRow(
        kind=RowKind.LEG,
        row_id=leg.transfer.id,
        label=leg_candidate_label(leg),
        cash_amount=amount,
        settled_on=leg.settled_on,
        is_settled=leg.status.is_settled,
        states_own_figure=True,
        transfer_id=leg.transfer.id,
        period=period,
        version_id=leg.transfer.version_id,
        settle_day_basis=None,
    )


def repriced_leg(
    row: CandidateRow, calendar: "PayCalendar",
    basis: "cash_ledger.AmountBasis", account_id: int,
) -> "CandidateRow | None":
    """Return :func:`~._valuation.repriced`'s LEG arm: the still-planned leg as it stands now.

    Re-read through the offer set's OWN loader narrowed to the one transfer
    (``transfer_legs.offerable_transfer_legs``, the owner and the saved
    periods the calendar carries), so a transfer that has settled, been
    deleted, moved to another pay period outside the calendar or had this
    account moved off either endpoint since the screen offered it answers
    ``None`` and the act naming it is refused as stale -- the scope asked
    again rather than a subset of it restated here.
    """
    legs = transfer_legs.offerable_transfer_legs(
        account_id, calendar.user_id, calendar.saved_by_id().keys(),
        options=leg_loads(), transfer_ids=(row.row_id,),
    )
    if not legs:
        return None
    (leg,) = legs
    amount = leg_price(leg, basis)
    if amount is None:
        return None
    return leg_candidate(leg, calendar, amount)
