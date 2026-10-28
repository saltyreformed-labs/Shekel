"""
Shekel Budget App -- Transfer Service status and settle-day writers

The writers that move a transfer and its two SIDES together: the status
applier (the transfer to its status through the one seam, and each side's
payment record, dated with ITS OWN side's day, through the seam's Transfer arm)
and the soft delete's return of a transfer to a plan (:func:`return_to_plan`,
ruling **R-BAL246**), which goes through the applier.  The born-settled
create's writer stood beside them until ruling **R-BAL379** (developer
2026-10-23) deleted the create's settled arm: a transfer is created unsettled
and enters the band only through the applier.

Extracted from ``transfer_service`` at plan step **X-f1b**, on the same ground
every earlier split from that module used -- it was at the 1000-line ceiling and
finding **N-152** recorded that the next change to it would hit the gate again.
This is that change.  The split is by responsibility rather than by line count:
these functions are the ONLY place a transfer's status is written, and each
side's record and settle day move with it here on every status move.  Three
acts that move no status write a side's record elsewhere: an endpoint move
relocates it and lowers its day (``_endpoints``, through
``status_seam.land_moved_side_record``), a delete takes it off the books
(``_delete``, through ``movement_removal.remove_movements``), and a reconcile
marks it cleared by a statement (``_settle.record_leg_clearing``, through
``status_seam.record_side_clearing``).  Plan step
X-f2-c3 then made ``transfer_service`` a PACKAGE (**N-152** / **N-156**) and
this module a leaf of it; the responsibility is unchanged and the file moved from
``app/services/_transfer_status.py``.

**The two sides' DAYS part here since plan step ``balance:X-bi-6-4c-3``**
(ruling **R-BAL142**): each side keeps its own day and basis, a side with no
evidence of its own borrows the other's, and the derivation is
:mod:`._side_days`' -- pure, and asked by the applier through
:func:`_resolved_days`.  The day CORRECTION door (``apply_settle_day_correction``,
ruling **R-ED**) is gone: a correction states a day for a side and goes through
:func:`apply_status_to_all_three` at the pair's current status, which is how a
figure correction already travelled.

**Neither function writes ``status_id`` and neither constructs a status-bearing
model**, so this module stays clear of the W9907 status fence: the status goes
through :func:`app.services.status_seam.apply_status_change`, which is the
sanctioned door.  **The package move is where that sentence could have quietly stopped
being true**: the fence's allowlist read ``app.services.transfer_service`` and
:func:`_module_in_allowlist` matches a package PREFIX, so this leaf would have
become exempt without anybody deciding it should be.  The entry now names
:mod:`app.services.transfer_service._create` -- the one leaf holding the
CONSTRUCTOR that holds it open (``create_transfer``'s; the shadow constructor
went with the shadows at plan step X-bi-6-4d-2), which plan step X-aj2
replaces.

Flask-isolated like the parent service: plain data and ORM rows in, mutations
applied in place, no ``request`` / ``session`` imports, no flush or commit of
its own -- the caller owns the session boundary.  (Each writer reads both
sides' records by a query, which may autoflush pending writes; the seam's
Transfer arm moves the transfer's counter itself when a record or day moves.)
"""

from datetime import date

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transfer import Transfer
from app.services import status_seam
from app.services.match_press import Press
from app.services.planned_rows_books import reject_revert_below_the_books
from app.services.settle_day import recorded_settle_day
from app.services.state_machine import verify_transition
from app.services.transfer_legs import TransferLeg, transfer_side_leg
from app.services.transfer_service._side_days import (
    NO_DAYS,
    PairDays,
    pair_fallback_day,
    resolve_pair_days,
)
from app.services.transfer_service._validation import TransferRows
from app.utils.balance_predicates import (
    delete_returns_to_plan,
    settled_status_ids,
)
from app.utils.dates import display_today


def _side_legs(transfer: Transfer) -> "tuple[TransferLeg, TransferLeg]":
    """Return the transfer's ``(expense, income)`` legs, each reading its record as it stands.

    A leg reads its side's record LIVE, off the transfer's list for that side
    (ruling **plan#1984**), so a record an earlier write of the same request
    appended is what the writer reads, and no leg built before a write can
    hold a stale one.  Until then the record was a field this filled by a
    query per side, and each writer below had to read the legs afresh.
    """
    return (
        transfer_side_leg(transfer, is_income=False),
        transfer_side_leg(transfer, is_income=True),
    )


def _resolved_days(
    legs: "tuple[TransferLeg, TransferLeg]", stated: PairDays, fallback_day: date,
) -> PairDays:
    """Return each side's day once this act lands -- the one derivation.

    Reads each side's CURRENT day off its RECORD (plan step
    ``balance:X-bi-6-4d-2``; the twin row's own day until then) and the day
    the pair shares when neither side has evidence
    (:func:`~._side_days.pair_fallback_day`), then hands both to
    :func:`~._side_days.resolve_pair_days`.  The applier
    (:func:`apply_status_to_all_three`) calls it, and the endpoint move asks
    the same resolver itself (``_endpoints._moved_days``), so a borrowed day
    has one derivation in ``app/`` (the REC-552 study's seam S6).  A record is
    dated exactly while its transfer is settled, so a
    side's current day is the record's day; a side holding no record -- a
    ``$0.00`` close, or a first settle -- has none.  That shared day reads the
    sides in the declared ``(expense, income)`` order: the twins' "a leg
    still in the band first" had two statuses to order, and a transfer has
    one.

    Args:
        legs: The ``(expense, income)`` legs with their records
            (:func:`_side_legs`).
        stated: What the act states, already admitted by its verb.
        fallback_day: The day both sides share when neither holds a day of
            its own (:func:`_fallback_day`).

    Returns:
        Both sides' days, for a pair in or entering the settled band.
    """
    held = tuple(
        None if leg.record is None else recorded_settle_day(leg.record)
        for leg in legs
    )
    current = PairDays(expense=held[0], income=held[1])
    return resolve_pair_days(
        current, stated, pair_fallback_day(held, fallback_day),
    )


def _fallback_day(transfer: Transfer) -> date:
    """Return the day a pair with no day on either side borrows.

    The day Paid was pressed -- the owner's today -- for a pair entering the
    band.  **A pair ALREADY settled with no day on either side is a ``$0.00``
    close** (ruling **R-BAL90**: it stores no day), and a later figure
    correction with no typed day dates it by the transfer's DUE DATE (ruling
    **R-BAL169**, the rule ruling **R-BAL139** gives a ``$0.00`` loan
    payment): the same answer whenever the correction is made -- **but never
    a day after today** (ruling **R-BAL231**, amending R-BAL169: a transfer
    already Paid whose due date is still ahead is dated today, where the
    future day was refused with the advice to leave it Projected, which is
    wrong for a transfer already Paid).  A transfer with no due date falls
    back to today; a due date the account's books no longer reach is refused
    by the arm's books grade, naming the day, so the owner types one.  Read
    before the act writes the transfer's status, so "already settled" is the
    status the act is leaving.

    **It reads the due date the transfer HAS when the act begins.**  An act
    that moves a ``$0.00`` pair's due date AND corrects its figure would date
    the pair by the old due date, because ``_update`` applies the status arm
    before the due-date arm.  No door sends both: the PATCH refuses a
    due-date edit on a settled transfer (``routes/transfers/mutations``'
    ``_LOCKED_EDIT_FIELDS``, unlocked only by a revert, which carries no
    figure), and the service's other callers edit Projected transfers or
    state only days (the cp2a review's p19, decided under ruling **R-BAL207**
    and not built: reordering the two arms would move which day ruling
    **R-PC97**'s revert refusal grades on a revert that moves the date).

    Args:
        transfer: The transfer being written.

    Returns:
        The fallback day.
    """
    today = display_today()
    if transfer.status_id in settled_status_ids() and transfer.due_date:
        return min(transfer.due_date, today)
    return today


def reject_stated_days_without_settle(status_id: int, stated: PairDays) -> None:
    """Refuse a stated day beside a status that settles nothing.

    No money has moved, so there is no day to record: the seam's own refusal
    (:func:`app.services.status_seam.reject_settle_day_without_settled_status`),
    asked of each side's stated day.  One rule at two moments -- the update
    before its first write, and this module's writer before either side is
    written.  (The create asked it a third time, of the days a born-settled
    create stated, until ruling **R-BAL379** deleted the create's settled arm
    and its days with it.)

    Args:
        status_id: The status the transfer is, or is about to be, in.
        stated: The days stated by side.

    Raises:
        ValidationError: When a day is stated and *status_id* is not settled.
    """
    for day in stated:
        status_seam.reject_settle_day_without_settled_status(status_id, day)


#: The refusal of a day stated on a pair the act leaves holding no record, in
#: the words the developer picked (ruling **R-BAL230**, 2026-10-08).
ZERO_CLOSE_HAS_NO_DAY = (
    "A $0.00 close moved no money, so it has no day. Type the amount the "
    "bank took to date it."
)


def reject_stated_day_on_a_zero_close(
    legs: "tuple[TransferLeg, TransferLeg]",
    stated: PairDays,
    settlement: "status_seam.Settlement | None",
) -> None:
    """Refuse a day stated for a pair this act leaves settled with no record.

    Ruling **R-BAL230** (developer 2026-10-08, "Refuse, say why"): a pair
    closed at ``$0.00`` keeps no record on either side and so no day (rulings
    **R-BAL82**, **R-BAL141**), and a day typed into its popover's empty
    day box and saved used to be accepted and dropped: the arm writes a day
    only onto a side holding a record (the cp2a review's M1).  The act
    leaves no record when it records ``$0.00`` (both sides' records come
    off), or when it records nothing on a pair already settled with none --
    the popover's Save, which posts the Actual box's untouched ``$0.00`` as
    an echo.  Either way a stated day has nowhere to go, and saying so beats
    a Save that answers OK and stores nothing.  A figure above ``$0.00`` in
    the same Save is what dates the pair, so that Save is not refused.

    **Wider than the question the developer was asked, deliberately, and
    fail-closed**: the rule is "a stated day on an act that records no
    money", so it also refuses a settle INTO the band at ``$0.00`` beside a
    stated day -- a typed ``$0.00`` with a typed day on a Projected pair,
    and a door-supplied day: the reconcile tick's statement day on a leg
    priced ``$0.00`` with its box cleared (ledger row **BAL-596**'s hole,
    which recorded the close and dropped the day).  The same day is dropped
    the same way in each, so one sentence answers all of them.

    Asked after :func:`reject_stated_days_without_settle`, so a stated day
    here sits beside a settled status.  A pair holding no record that is NOT
    yet settled is a move into the band with no settlement, which the arm
    refuses as a programming error, so this leaves it to the arm.

    Args:
        legs: The ``(expense, income)`` legs with their records as they stand.
        stated: The days the act states, by side.
        settlement: What the act records, or ``None`` when it records nothing.

    Raises:
        ValidationError: When a day is stated and the act leaves the pair
            settled holding no record.
    """
    if stated == NO_DAYS:
        return
    if settlement is not None:
        if settlement.amount != 0:
            return
    elif (
        any(leg.record is not None for leg in legs)
        or legs[0].transfer.status_id not in settled_status_ids()
    ):
        return
    raise ValidationError(ZERO_CLOSE_HAS_NO_DAY)


def apply_status_to_all_three(
    rows: TransferRows,
    new_status_id: int,
    *,
    stated: PairDays = NO_DAYS,
    settlement: "status_seam.Settlement | None" = None,
    press: Press | None = None,
) -> None:
    """Move a transfer to a status and both its sides' records with it, each on its own day.

    **The ONE writer of a transfer's status, and of each side's record and
    day on every status move** (plan steps ``balance:X-bi-6-4c-3`` and
    ``X-bi-6-4d-2``): a settle, a revert, a cancel, a figure correction and a
    day correction all come through here.  (An endpoint move and a delete
    write a side's record without moving the status: the module docstring
    names both.)  Whenever the pair is in or entering the settled band each
    side takes ITS OWN day (:func:`_resolved_days`, ruling **R-BAL142**).  A
    day correction is an identity status change carrying a stated day, and a
    Save that states nothing moves no side that holds evidence (finding
    **N-304**, closed).  The "three" are the transfer and its two sides.

    **Since plan step ``balance:X-bi-6-4d-2`` the sides are written by the
    status seam's Transfer arm** (``status_seam.sync_side_records``), which
    writes each side's record directly; the twin rows' status and day are no
    longer kept and no longer read.  Until then the seam was applied to each
    twin row in turn and copied the twin's day onto its record, which made a
    twin's status a second home of the transfer's (Transfer Invariant 3, rule
    14).  So the twins' transitions are no longer verified, and the "pair
    settlement repair" that read a drifted twin's sibling's record went with
    them: every move INTO the band carries its record (the settle verb, the
    one door into the band since ruling **R-BAL379** deleted the create's
    settled arm), and an identity move stating no figure keeps the transfer's
    one settled figure.

    **Verified in FULL before anything is assigned**: the transfer's own
    transition, the revert refusal, every stated day and a day stated on a
    pair the act leaves at ``$0.00`` (:func:`reject_stated_day_on_a_zero_close`)
    -- then the arm's own refusals, each side's books included, before its
    first write -- so an illegal request leaves the transfer and both records
    untouched (F-047 / commit C-21).

    Args:
        rows: The transfer; only it and its sides' records are written.
        new_status_id: The ``ref.statuses.id`` the transfer moves to.  The
            pair's CURRENT status for a correction.
        stated: The days the act STATES, by side
            (:class:`~app.services.transfer_service._side_days.PairDays`),
            already admitted by the calling verb.  Empty derives every day,
            which is what every door that knows none means.
        settlement: WHAT moved, when this change records a settle
            (:class:`app.services.status_seam.Settlement`) -- its figure stored
            once, on the transfer, while each side's record takes its day (a
            ``$0.00`` figure leaves neither side a record).  **A move INTO the
            settled band must carry one**, which the arm refuses otherwise.
            ``None`` on any other move keeps the transfer's figure, save a move
            out of the band with no record on either side, which releases it.
        press: The save's :class:`~app.services.match_press.Press` (ruling
            **R-CC135**), or ``None`` when its door named nothing.  A
            ``$0.00`` record takes both sides' kept payments off the books in
            ONE call of the removal act, which refuses a line the page did not
            name, and the door's close compares what the pair freed with what
            the page named, whole.

    Raises:
        ValidationError: If the transfer's transition is illegal (propagated
            from the state machine), if a day is stated beside a status that
            settles nothing or on a pair the act leaves at ``$0.00`` (ruling
            **R-BAL230**), if a day is refused (a future day, ruling R-EJ,
            or one the account's books do not reach), or if a revert to
            Projected lands inside the transfer's books
            (:func:`~app.services.planned_rows_books.reject_revert_below_the_books`,
            ruling **R-PC97**).
        ValueError: If the move enters the settled band with no *settlement*
            (propagated from the arm).
    """
    verify_transition(rows.transfer, new_status_id)
    # A revert to Projected that the books hold is refused here, the
    # transfer's one status door, before anything is assigned (ruling
    # **R-PC97**).
    reject_revert_below_the_books(rows.transfer, new_status_id)
    reject_stated_days_without_settle(new_status_id, stated)
    legs = _side_legs(rows.transfer)
    reject_stated_day_on_a_zero_close(legs, stated, settlement)
    # Each SIDE's day, resolved before either side is written; out of the
    # band the arm un-dates both, so nothing is resolved there.
    days = (
        _resolved_days(legs, stated, _fallback_day(rows.transfer))
        if new_status_id in settled_status_ids() else NO_DAYS
    )
    # The sides BEFORE the transfer's status, so the arm reads the status the
    # act is leaving.
    status_seam.sync_side_records(
        legs, new_status_id, days=tuple(days), settlement=settlement,
        press=press,
    )
    status_seam.apply_status_change(rows.transfer, new_status_id)


def return_to_plan(transfer: Transfer, *, took_off: bool) -> None:
    """Set a hidden transfer whose delete took its payments off back to Projected.

    **Ruling R-BAL246's act for a transfer** (developer 2026-10-09, "Back as
    a plan, now"), called by the soft delete AFTER the one removal act has
    taken both sides' records off (ruling **credit_card:R-CC75**) and AFTER
    the transfer is hidden.  Until then a deleted Paid transfer stayed Paid
    holding nothing -- a ``$0.00`` close, the bank took nothing -- so a
    restore brought back a payment a loan reads as a MISSED installment, its
    interest charged with no cash.  Whether the delete returns the transfer
    to a plan is :func:`~app.utils.balance_predicates.delete_returns_to_plan`,
    the rule's one statement, which the row delete is owed through its own
    status door (ledger row **credit_card:CC-387**): a ``$0.00`` close held
    nothing, so it keeps Paid.

    **Through the transfer's one status writer** (:func:`apply_status_to_all_three`),
    never a status write in the door: the writer's transition check and
    revert refusal stand.  The sides hold no record by now, so the writer
    writes the transfer's status alone.

    **The books' revert refusal does not reach a HIDDEN row** (ruling
    **R-BAL248**, developer 2026-10-09, "Plan, kept deleted", narrowing
    ruling **R-PC97**; the one condition is
    :func:`~app.services.planned_rows_books.reject_revert_below_the_books`'
    ``row.is_deleted``), which is why this act runs AFTER the hide.  A
    transfer due on or before its books but paid after them is set back
    like any other: measured before the ruling, the set-back through this
    writer was refused with R-PC97's "cannot be planned as unpaid", which
    would have refused a delete that went through.  A hidden row sits in
    no balance, so it is no plan; where one could come back the books are
    asked again -- the unarchive leaves deleted, and names, a hidden row its
    books hold (:class:`~app.services.definition_unarchive.UnarchiveScope`,
    rulings **R-PC95** and **R-PC99**), and the conflict chooser's "use
    the template" asks that scope (ruling **R-BAL253**).  The condition is
    written for any row, not for a transfer alone: no transaction door
    reaches a hidden row (``auth_helpers.is_not_found_to_transaction_doors``),
    so today this act is the one it serves, and the row set-back owed by
    ledger row **credit_card:CC-387** inherits it.

    Args:
        transfer: The transfer the soft delete has just hidden.
        took_off: Whether the removal act took at least one payment record
            off either side.
    """
    if not delete_returns_to_plan(transfer, took_off=took_off):
        return
    apply_status_to_all_three(
        TransferRows(transfer), ref_cache.status_id(StatusEnum.PROJECTED),
    )
