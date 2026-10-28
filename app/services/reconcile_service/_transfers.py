"""
Shekel Budget App -- The TRANSFER arm of the outstanding set

The last of the package's three arms (see :mod:`app.services.reconcile_service`
for what an arm is): a TRANSFER's leg on the account whose balance was
asserted.  Money moving between two of the owner's own accounts still leaves
one of them, so a checking statement shows it exactly as it shows a bill -- and
until plan step X-f2-c3 the panel could not settle one.  **Replayed through
this producer over all 53 of production's Checking assertion DAYS** -- on a
throwaway clone, with each day's transfer shadows restored to the status they
held then -- **8 days would have carried an offer, 8 rows worth `$5,442.89`**:
six `$500.00` savings sweeps, one `$1,910.95` Mortgage payment and one
`$531.94` Van Loan payment, every one of them an expense leg.  That reproduces
ruling **R-FA**'s own OF-WHICH figure to the cent.

**53 DAYS, not 57**: the account carries 57 assertion ROWS over 53 distinct
days, and X-f2-c's own text records that confusing the two is how a figure in
this arc went wrong before.  At the then-LATEST assertion (2026-08-06) this arm
offered NOTHING, because every projected shadow started 2026-08-13 -- so the
panel was unchanged on production when plan step X-f2-c3 shipped.

**It offers the LEG, off ``budget.transfers``, since leaf
``balance:X-bi-6-4c-2``** (the one-place rule leaves ``X-bi-6-4a`` / ``6-4b``
followed: a reader reached a shadow only through a leg-shaped call in
``transfer_legs`` or ``transfer_service``, until ``X-bi-6-4d-2`` deleted the
shadows): a
:class:`~app.services.transfer_legs.TransferLeg` from
``transfer_legs.offerable_transfer_legs``, keyed by
``transfer_legs.cell_key`` -- ``(transfer id, account id)``, ruling
**R-BAL87** -- priced at its transfer's ``transaction_service.settle_amount``
(ruling **plan#1983**: a leg is offered at its parent's answer) and cleared
through the leg-shaped door ``transfer_service.record_leg_clearing``, and
ticked under its own form fields (ruling **R-BAL145**, :class:`~._offers.TickForm`).
Until then it offered the transfer's SHADOW row on this account, keyed by the
shadow's id in one map with the transaction arm's rows, which only the
table's partition on ``transfer_id`` kept from colliding.  So this arm reached
no shadow itself; the two doors did, through the interval, until plan step
``balance:X-bi-6-4d-2`` re-bodied them onto the transfer and its sides' records
and ``X-bi-6-4d-2`` deleted the shadows.  **What can move on screen**, all of it
declared: a transfer's block is headed by its leg's label, composed from the
endpoints' CURRENT names, where it printed the shadow's stored copy (leaf
``X-bi-6-1``'s same change on the grid) -- 0 of 354 shadows disagreed on the
2026-09-24 production dump; two same-day transfer blocks order by transfer id
where they ordered by shadow id (``_assemble._block_order``); a transfer whose
shadow pair is broken was warned about rather than offered (ruling
**R-BAL148**) until plan step ``balance:X-bi-6-4d-2`` priced a leg off its
transfer alone and ruling **R-BAL235** offered it like any other; and a
shadow drifted from its parent was offered by the parent's state
(``transfer_legs.offerable_transfer_legs``) until ``X-bi-6-4d-2`` deleted the
shadows.

**Its settle is ``transfer_service.settle_transfer``, and that is the whole
reason it is a separate arm** (ruling **R-FA**).  A transfer is ONE row since
plan step ``balance:X-bi-6-4d-2`` (ruling **R-BAL166**; it was a parent and two
shadows until then), its status is the transfer's, and every write of it goes
through ``transfer_service`` (``CLAUDE.md``, Architecture).  Ticking one here
therefore settles the leg on the OTHER account too, which is a fact about the
act rather than about any row and is why the panel prints it once under the
section heading (:attr:`~app.services.reconcile_service.OfferKind.section_note`).

**Nothing here decides what a tick BOOKS, which status the rows take, or
whether a submitted figure is a CORRECTION.**  All three are the transfer
service's.  What a tick will book is ``transaction_service.settle_amount`` of
the leg's transfer, the one composition the transfer's settle books through
(ruling **plan#1983**), so the panel can render it; whether a figure was a
human's is the VERB's own answer, returned by the settle rather than
asked of a predicate beforehand.  The loan-payment FREEZE the step
specification names is inside both: an auto-derived loan payment books its live
payment-date figure rather than the creation-time escrow its estimate carries,
and because the panel's figure and the booked figure come from one expression
they cannot drift.

**Its bound and writer are :mod:`._rows`'**, shared with the transaction arm;
its scope is the leg loader's.  What stays here is which legs are this arm's,
what one is worth, and what a tick means.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

from sqlalchemy.orm import selectinload

from app.models.transfer import Transfer
from app.services import transaction_service, transfer_legs, transfer_service
from app.services.cash_ledger import AmountBasis
from app.services.match_press import Press
from app.services.reconcile_service import _rows
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingGroup,
    OutstandingTransaction,
)
from app.services.stated_figure import StatedFigure
from app.services.transfer_legs import TransferLeg
from app.utils.amount_relationships import transfer_pricing_load_options
from app.utils.log_events import EVT_TRANSFERS_RECONCILED


def _settle_one(
    leg: TransferLeg,
    submitted: StatedFigure | None,
    statement: _rows.Statement,
    press: Press,
) -> bool:
    """Settle one leg's transfer through the service; say if a human's figure won.

    This arm's settle, named by :data:`ARM`.
    **Both legs move in this one call**: a transfer is one row whose status
    is the transfer's (plan step ``balance:X-bi-6-4d-2``, ruling
    **R-BAL166**), so the structure holds that rather than this function
    remembering it, and it is why a tick here settles the matching leg on the
    other account (the section note says so on the panel).

    The submitted figure and the statement's day are handed straight through:
    what a tick BOOKS, whether the figure is written at all, and which status
    the transfer takes are the transfer service's rules and are not restated
    here.

    Args:
        leg: The leg on this account, its transfer still Projected.
        submitted: The figure the panel's amount box posted, or ``None``.
        statement: The statement being reconciled; its day is what THIS leg
            records the money as having moved on, on the ``asserted`` basis --
            the owner asserted a BALANCE for that day, so the day bounds the
            movement from above rather than naming it (plan step **X-az**).
            The leg on the other account borrows it unless it holds evidence
            of its own (ruling **R-BAL142**; it was stated for both legs until
            plan step ``balance:X-bi-6-4c-3``, like the link below never was).
        press: The panel save's press (``_assemble.record_reconciliation``,
            ruling **R-CC135**), over the lines named under the ROWS ticked:
            the panel captions no leg (plan step ``credit_card:CC-5-4a-5``).
            A ``$0.00`` figure is the one
            transfer settle that takes a kept payment out of its matches, a
            transfer holding no purchase to settle from; ruling **R-CC125**
            refuses a typed one before this runs, and any other figure books
            each side's payment where it already is.  A leg whose own price
            resolved to ``$0.00`` ticked with its box CLEARED is refused
            too, since plan step ``balance:X-bi-6-4d-2``: the tick states the
            statement's day for a settle that records no money, and ruling
            **R-BAL230** refuses a day on a ``$0.00`` close
            (``transfer_service._status.reject_stated_day_on_a_zero_close``)
            -- a plain transfer's amount is positive
            (``ck_transfers_positive_amount``) and so is a recurring
            series' (``ck_template_amount_versions_transfer_positive_amount``);
            whether a derive-mode loan payment's price can reach ``$0.00`` is
            not established -- which is ledger row **BAL-596**'s hole,
            balance:X-db's.  A leg's tick that would free a line no row's
            caption named is refused rather than freeing it unannounced, so
            the arm needs no caption and no named silence.

    Returns:
        Whether the verb booked *submitted* as a human's correction -- the
        verb's OWN answer about what it just did, which is finding **N-231**'s
        rule (a count read off the column afterwards cannot tell a human's
        figure from a machine's).  Taking it from the act rather than from a
        predicate asked beforehand is also what stops the loan freeze being
        resolved twice for one tick.
    """
    corrected = transfer_service.settle_transfer(
        leg.transfer.id, statement.owner_id,
        submitted=submitted,
        side_days=(
            transfer_service.SideDay(leg.account_id, statement.settle_day),
        ),
        press=press,
    )
    # WHICH statement showed THIS LEG (ruling **R-FL**), through the transfer
    # service, the one door onto a side's payment record (``CLAUDE.md``'s
    # transfer invariant 4).  Only this leg takes it, even though the settle
    # above moved both: the other leg is on another account, whose own
    # statement nobody read in this act.  ``status_seam.record_side_clearing``
    # carries why that asymmetry is correct.
    transfer_service.record_leg_clearing(leg, statement.anchor.anchor_id)
    return corrected


def _load(
    statement: _rows.Statement, transfer_ids: "set[int] | None",
) -> "dict[int, TransferLeg]":
    """Return this arm's legs, ``{transfer id: leg}``, for :data:`ARM`'s ``load``.

    The owner's live, still-Projected transfers with a leg on this account in
    a period that had started by the statement's day
    (``transfer_legs.offerable_transfer_legs``, over
    :attr:`~._rows.Statement.offerable_period_ids` -- the scope the
    transaction arm's rows take), narrowed by the shared landing-day bound
    over each leg's TRANSFER (:func:`~._rows.lands_on_or_before`: a leg is
    dated by its parent).  Keyed by the id a transfer's tick posts -- its own
    -- and ordered by landing day, then transfer id.

    **The owner and the parent's soft-delete are the LOADER's clauses**, where
    they were a semi-join this arm added over its shadows: a leg whose parent
    has gone is not money this account owes, and offering one would send
    ``settle_transfer`` looking for a row it treats as absent -- a
    ``NotFoundError`` this route has no handler for, i.e. a 500 on a money
    door.

    The loads are what the arm reads: the pricing chain the leg price resolves
    through (``transfer_pricing_load_options``) and both endpoints, which
    :attr:`~app.services.transfer_legs.TransferLeg.name` composes the block's
    heading from.

    Args:
        statement: The statement being reconciled.
        transfer_ids: The writer's narrowing, or ``None`` for the reader.

    Returns:
        The legs, keyed by transfer id.  One per transfer: a transfer's two
        endpoints differ (``ck_transfers_different_accounts``), so at most one
        of its legs is on this account.
    """
    legs = [
        leg for leg in transfer_legs.offerable_transfer_legs(
            statement.account_id,
            statement.owner_id,
            statement.offerable_period_ids,
            options=(
                *transfer_pricing_load_options(),
                selectinload(Transfer.from_account),
                selectinload(Transfer.to_account),
            ),
            transfer_ids=transfer_ids,
        )
        if _rows.lands_on_or_before(statement, leg.transfer)
    ]
    legs.sort(key=lambda leg: (
        _rows.attributed_on(statement, leg.transfer), leg.transfer.id,
    ))
    return {leg.transfer.id: leg for leg in legs}


#: What this arm IS (:class:`app.services.reconcile_service._rows.Arm`): what it
#: loads, how a leg settles, and what it calls the act in the log.  A module
#: constant since leaf ``balance:X-bi-6-4c-2``: it was built per call while its
#: membership was a semi-join over ``budget.transfers`` needing the owner, and
#: its loader reads the owner off the statement now.
ARM = _rows.Arm(
    load=_load,
    settle=_settle_one,
    event=EVT_TRANSFERS_RECONCILED,
)


def outstanding_transfers(
    statement: _rows.Statement, basis: "AmountBasis",
) -> "list[OutstandingGroup]":
    """Return this arm's offers, one childless block per leg.

    The transfers this account is still holding forward on the day the balance
    was asserted -- a savings sweep the statement shows leaving, a loan payment
    it shows going out, or money arriving from another account.  Ticking one
    records that the bank moved it by that day
    (:func:`app.services.reconcile_service._rows.record_settled`).

    **It returns BLOCKS, not a map for the assembler to key**, since leaf
    ``balance:X-bi-6-4c-2``.  A leg holds no purchase, so its block is always
    childless, and its heading is its OWN -- the leg's label and its transfer's
    period -- so there is nothing to union and no parent to look up: the
    assembler's row-keyed map and its heading query are the transaction and
    purchase arms', and a leg's key (a pair) never enters them.  Until then this
    arm returned ``{shadow id: offer}`` into that map, where only the table's
    partition on ``transfer_id`` kept a shadow id from overwriting a bill's.

    **Every offer is CORRECTABLE, and that follows from ruling R-FF rather than
    being a choice made here.**  A tick is correctable exactly when the settle
    verb takes its MANUAL branch, and a transfer has no other branch to take: a
    leg is never purchase-tracked
    (:attr:`~app.services.transfer_legs.TransferLeg.tracks_purchases`) and
    there are no purchases for a figure to be derived from (measured on
    production 2026-09-15: 342 shadows, 0 entries; since plan step
    ``balance:X-bi-3c`` a SETTLED leg holds the seam's covering movement, which
    is the settle's own record and never a purchase to derive from).  The box
    is PREFILLED with what the tick would book, so an untouched tick is an echo
    and writes nothing -- ``transfer_service.settle_transfer`` tells the two
    apart as it settles, and says which it was in its return value.

    **The tally follows the LEG and the section follows the ACT.**
    ``is_income`` puts the from-side among the payments and the to-side among
    the deposits, because that is what a statement shows; ``kind`` is
    ``TRANSFER`` for both, because a transfer is one act and reads as one
    section (ruling **R-FC**).

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled -- whose
            calendar, which account, which assertion.  **Built ONCE by
            :func:`~._assemble.outstanding_set` and threaded**; see
            :func:`~._transactions.outstanding_transactions` for why one value
            rather than three arguments, which is pay-calendar plan step
            C4-a-2's doing.
        basis: The PANEL's :class:`~app.services.cash_ledger.AmountBasis`,
            built ONCE by :func:`~._assemble.outstanding_set` and threaded
            (plan step X-au-j, finding **N-295**).  This is the EXPENSIVE half of that
            finding: each offered leg built its own basis and so paid the
            scenario-wide loan-config join, plus a full loan resolve for every
            derive-mode payment -- finding **N-269** reintroduced one tier up,
            exactly as N-295's impact column predicted.

    **Every leg is offered** (ruling **R-BAL235**, developer 2026-10-08,
    "Offer and settle it", retiring **R-BAL148**'s warning): a leg is priced
    off its transfer (``transaction_service.settle_amount`` of
    ``transfer_legs.parent_of(leg)``, ruling **plan#1983**), never its twin
    rows, so a transfer whose twin was
    deleted behind the app's back is priced and ticked like any other.  The
    warning the panel printed for one went with the refusal it caught (plan
    step ``balance:X-bi-6-4d-2``).

    Returns:
        One :class:`~._offers.OutstandingGroup` per offered leg, in
        landing-day order, each keyed by its leg's ``(transfer id, account
        id)``; empty for an account holding no overdue transfer.
    """
    groups = []
    for leg in ARM.load(statement, None).values():
        offer = OutstandingTransaction(
            key=leg.cell_key,
            attributed_on=_rows.attributed_on(statement, leg.transfer),
            amount=transaction_service.settle_amount(
                transfer_legs.parent_of(leg), basis,
            ),
            # Always the whole figure: a leg can hold no PURCHASE (its one
            # possible entry is the seam's covering movement, written when it
            # settles and kept un-dated across a revert), so there is no card
            # half for the statement to disagree with (N-226).
            cash_amount=None,
            is_correctable=True,
            is_income=leg.is_income,
            kind=OfferKind.TRANSFER,
            # A leg is never purchase-tracked, so no tick of one closes an
            # envelope.
            closes_envelope=False,
            # No transfer tick takes a payment out of its matches once a
            # ``$0.00`` box is refused (ruling R-CC125; ``_settle_one``), so
            # the offer keeps its default, naming no line.
        )
        groups.append(OutstandingGroup(
            key=leg.cell_key,
            name=leg.name,
            period=_rows.filed_period(statement, leg.transfer),
            purchases=(),
            settle=offer,
            # Resolved by the assembler once the order is known.
            section=None,
        ))
    return groups
