"""
Shekel Budget App -- Status seam: a TRANSFER SIDE'S payment record (the Transfer arm).

Plan step ``balance:X-bi-6-4d-2`` (rulings **R-BAL88**, **R-BAL142**).  A
transfer's money moves on its two SIDES, and each side's payment record hangs
off the TRANSFER by a side link (``transaction_entries.expense_transfer_id`` /
``income_transfer_id``), keyed with the record's account onto that side's
endpoint.  Until this step each side's record hung off a hidden twin row and
this seam wrote it through the row's arm (:mod:`._covering`), one call per
twin, with the twin's own status, day and clearing link copied onto it.  The
twins carried nothing a side needs: a transfer's status is ONE column on the
transfer, and a side's day is its record's.  So this arm writes a side's record
DIRECTLY, and the transfer writer
(``transfer_service._status.apply_status_to_all_three``) calls it once per act
for both sides, under ONE press.

**What it writes, and the row arm's rule it follows for each fact:**

* the FIGURE and who wrote it, ONCE, on the TRANSFER (plan step
  ``balance:X-bi-6-4d-2``, rulings **R-BAL365**, **R-BAL366**:
  :attr:`~app.models.transfer.Transfer.settled_figure_pair`), ``0.00`` for a
  close of nothing; kept across a revert that keeps the records (ruling
  **R-BAL61**) and released by a revert that keeps none.  A side's record
  states NO figure (ruling **R-BAL366**,
  ``ck_transaction_entries_side_record_states_no_figure``): it carries the
  side's leg label as its name, and the band's figure arm
  (``app.side_band_infrastructure.FIGURE_ARM``) holds the figure to the
  records at commit;
* the DAY and how it is known -- the side's resolved day
  (``transfer_service._side_days``) through ``settle_day.record_settle_day``,
  so the books boundary refuses a day the account's books do not reach; a
  payment's purchase day IS its settle day (ruling **R-BAL39**), so
  ``purchased_on`` follows a day that moves;
* the STATEMENT LINK -- released whenever the day moves or the transfer
  leaves the band (ruling **R-FL**: a link records that a statement showed
  this money on that day); a day re-stated on the SAME day keeps it, and
  raises the basis by :func:`._covering.raises_basis`, never lowering a
  side's own evidence (``settle_day.is_evidence``);
* a REVERT un-dates the record and keeps it (ruling **R-BAL61**), and the next
  settle re-dates the same record;
* a close at ``$0.00`` keeps NO record on either side (rulings **R-BAL82**,
  **R-BAL141**): records a revert kept are taken off the books through the one
  removal act (:mod:`app.services.movement_removal`), both sides in ONE call
  under the save's press, so the dialog's figure and the receipt's are one
  derivation and a matched line is freed only where the page named it
  (rulings **R-CC127**, **R-CC135**).

**The transfer's optimistic-lock counter moves whenever its figure, or a
side's record or day, nets a change** (developer ruling 2026-08-18, the
``$214.37`` two-tab lost update); both popovers pin the transfer's
``version_id``.  A figure correction writes the transfer's own column, so the
counter moves with it.  A record is a row of another table, so a day
correction writes nothing of the transfer and its counter would stand still:
``flag_modified`` forces the transfer into the flush, exactly as the row
arm's ``_record_moved`` does for a row.  Until this step the transfer
service compared the twins' counters before and after the act
(``_update._bump_parent_version_if_a_leg_moved``), which saw a day move only
because the twin's own day column moved -- a side record's day is its own
now, and the arm that writes it is the one that knows.

**The tender is the side's endpoint**: a record's account is held to its
side's endpoint by the side key, so a transfer settle names no tender and a
new record books on the endpoint.

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
mutates in place and never commits; the removal act's posting reversal
FLUSHES, and the caller owns the session boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm.attributes import flag_modified

from app import ref_cache
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.settled_figure import SettledFigure
from app.models.transaction_entry import TransactionEntry
from app.services import match_withdrawal, movement_removal
from app.services.cash_ledger import reject_movement_before_books_open
from app.services.match_press import Press
from app.services.settle_day import (
    SettleDay,
    record_settle_day,
    recorded_settle_day,
)
from app.services.status_seam._covering import raises_basis
from app.services.status_seam._record import Settlement
from app.services.status_seam._refusals import (
    reject_future_settle_day,
    reject_settle_day_without_settled_status,
    reject_settlement_on_a_deleted_row,
    reject_settlement_without_settled_status,
)
from app.utils.balance_predicates import settled_status_ids

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.transfer_legs import TransferLeg


def sync_side_records(
    legs: "tuple[TransferLeg, TransferLeg]",
    new_status_id: int,
    *,
    days: "tuple[Optional[SettleDay], Optional[SettleDay]]",
    settlement: Optional[Settlement],
    press: Press | None,
) -> None:
    """Keep a transfer's two side records in step with the act its writer is applying.

    Called by the transfer writer BEFORE it writes the transfer's own status
    (``status_seam.apply_status_change``), so the transfer's ``status_id`` is
    still the status the act is LEAVING -- which is what "was settled" reads.
    Three cases, total over what the writer can ask:

    * **neither before nor after in the settled band** -- a cancel of a
      Projected transfer: nothing moves;
    * **in the band with a record** -- a settle, a re-settle after a revert or
      a figure correction: the transfer carries the figure and each side's
      record its own day (a new record when the side holds none), or, for a
      ``$0.00`` figure, neither side keeps one;
    * **otherwise each side's record follows its day** -- a revert un-dates
      it, a day correction re-dates it, an identity re-submit moves nothing.

    Every refusal runs before the first write, for the seam's reason: a
    refused act leaves both records untouched.  That includes the books
    boundary: each day this act will write is graded against its side's
    endpoint here (``cash_ledger.reject_movement_before_books_open``, the
    producer ``settle_day.record_settle_day`` asks again at the write), so a
    day the second side's books do not reach is refused before the first
    side is written (the cp2a review's L1, measured: a settle naming the
    from-side's day onto a to-side whose books open later left the new
    from-side record staged when the to-side was refused).  The removal act
    a ``$0.00`` close runs is the exception: it refuses a line the press did
    not name after reversing what the records posted, though before taking
    either off, and the transfer's ``0.00`` is written after it.

    Args:
        legs: The transfer's ``(expense, income)`` legs, each carrying the
            side's record as it stands (``transfer_legs.transfer_side_leg``),
            ``None`` when the side holds none.
        new_status_id: The ``ref.statuses.id`` the transfer is moving to --
            its current one for a correction.
        days: Each side's day once the act lands, ``(expense, income)``, as
            the writer resolved them (ruling **R-BAL142**); ``(None, None)``
            out of the band.
        settlement: WHAT moved, when the act records a settle; ``None``
            states no figure, so the transfer keeps the one it stores, save
            an act leaving the band with no record on either side, which
            releases it (:func:`_follow_figure`).
        press: The save's :class:`~app.services.match_press.Press`, or
            ``None`` when its door named nothing -- asked by the one removal
            act a ``$0.00`` close runs.

    Raises:
        ValidationError: When a day is stated beside a status that settles
            nothing, a day is in the future (ruling **R-EJ**) or on or before
            its side's books (graded here, before any write), a record is handed over
            beside a status that settles nothing, a record would be written
            under a deleted transfer (ruling **R-CC89**), or the removal act
            would free a line the press's page did not name.
        ValueError: When the transfer ENTERS the settled band with no
            *settlement* -- a programming error, as for a row.
    """
    transfer = legs[0].transfer
    for day in days:
        reject_settle_day_without_settled_status(new_status_id, day)
        reject_future_settle_day(day)
    reject_settlement_without_settled_status(new_status_id, settlement)
    reject_settlement_on_a_deleted_row(transfer, settlement)
    settled_ids = settled_status_ids()
    was_settled = transfer.status_id in settled_ids
    now_settled = new_status_id in settled_ids
    if now_settled and not was_settled and settlement is None:
        raise ValueError(
            f"Transfer {transfer.id} is entering the settled band with no "
            "settlement record. A settle states what moved as well as when: "
            "pass settlement=Settlement(...)."
        )
    if not was_settled and not now_settled:
        return
    covers = now_settled and settlement is not None and settlement.amount != 0
    withdraws = now_settled and settlement is not None and not covers
    # The sides this act writes a day onto, each with its day: every side
    # when the act records a figure, none when a ``$0.00`` close takes both
    # records off, else each side holding a record.  One list, read by the
    # books grade and by the writes, so the grade asks of exactly the days
    # the writes write.
    if covers:
        written = tuple(zip(legs, days))
    elif withdraws:
        written = ()
    else:
        written = tuple(
            (leg, day) for leg, day in zip(legs, days)
            if leg.record is not None
        )
    for leg, day in written:
        if day is not None and _writes_day(leg.record, day):
            reject_movement_before_books_open(_endpoint(leg).id, day.day)
    if covers:
        moved = [_cover_side(leg, day) for leg, day in written]
    elif withdraws:
        moved = [_withdraw_sides(legs, press)]
    else:
        moved = [_follow_day(leg.record, day) for leg, day in written]
    _follow_figure(
        transfer, settlement, now_settled=now_settled,
        holds_record=any(leg.record is not None for leg in legs),
    )
    if any(moved):
        # ``status_id`` for the row arm's reason (``_covering._record_moved``):
        # the one column every path here has loaded, re-written at its value.
        flag_modified(transfer, "status_id")


def _follow_figure(
    transfer, settlement: Optional[Settlement], *,
    now_settled: bool, holds_record: bool,
) -> None:
    """Bring *transfer*'s ONE settled figure to the act: stated, kept or released.

    * A settle, a re-settle or a correction (the act lands settled with a
      *settlement*): the figure and who wrote it, ``0.00`` for a close of
      nothing (ruling **R-BAL365**).
    * Out of the band with no record on either side (*holds_record* as the
      act found them): released -- the revert of a ``$0.00`` close, which
      ruling R-BAL365 says clears it so a re-settle re-prices, and the
      delete's return to plan after its removal act took the records off.
    * Otherwise kept: a revert keeps the records and the figure they moved
      (ruling **R-BAL61**), and an act stating no figure leaves it alone.
    """
    if now_settled and settlement is not None:
        _state_figure(transfer, settlement)
    elif not now_settled and not holds_record:
        release_side_figure(transfer)


def _state_figure(transfer, settlement: Settlement) -> None:
    """Store *settlement*'s figure and who wrote it on *transfer*: its one home.

    Assigned only when it differs, so an echo writes nothing and the
    transfer's version counter moves only for a figure that netted a change.
    """
    stated = SettledFigure(
        settlement.amount,
        ref_cache.movement_figure_source_id(settlement.source),
    )
    if transfer.settled_figure_pair != stated:
        transfer.settled_figure_pair = stated


def release_side_figure(transfer) -> None:
    """Clear *transfer*'s settled figure: no record is left to have moved it.

    The figure's lifetime is its records' (the build plan's O4): kept while a
    side holds a record, settled or reverted, and released when none is left
    out of the band -- the revert of a ``$0.00`` close, and the delete, whose
    removal act takes every record off (``transfer_service._delete``, which
    asks this for a transfer its writer does not move).  A ``$0.00`` close
    still SETTLED keeps its ``0.00``: it never held a record.

    Args:
        transfer: The :class:`~app.models.transfer.Transfer` whose figure goes.
    """
    if transfer.settled_figure_pair is not None:
        transfer.settled_figure_pair = None


def _cover_side(leg: "TransferLeg", day: SettleDay) -> bool:
    """Ensure *leg*'s side holds one record, named for the side, on *day*.

    The record states no figure: the act's figure is the transfer's
    (:func:`_state_figure`, ruling **R-BAL366**).  A side holding a record --
    kept un-dated across a revert, or dated -- takes the side's leg label
    as its name and is (re-)dated, so its id survives and a match or a log
    line naming it still names it.  A side holding none gets a new record,
    filed under the transfer by its side link, on the side's endpoint
    (:func:`_endpoint`), owned and authored by the transfer's owner, marked
    ``covers_settlement``.

    Returns:
        Whether the side's record -- its name, day or link -- netted a
        change.
    """
    record = leg.record
    if record is not None:
        renamed = record.description != leg.name
        record.description = leg.name
        return _follow_day(record, day) or renamed
    transfer = leg.transfer
    endpoint = _endpoint(leg)
    record = TransactionEntry(
        expense_transfer_id=None if leg.is_income else transfer.id,
        income_transfer_id=transfer.id if leg.is_income else None,
        account_id=endpoint.id,
        # The transfer's OWNER: the record's account is the owner's
        # (``fk_transaction_entries_owner_account``), the side key makes it
        # the endpoint, and the endpoint is the owner's (ruling R-BAL107).
        owner_id=transfer.user_id,
        # The AUTHOR: the seam records it on the owner's behalf.
        user_id=transfer.user_id,
        is_credit=False,
        covers_settlement=True,
        description=leg.name,
    )
    _follow_day(record, day)
    # Appended to the side's collection, the list the posting writer's pair
    # door and the one removal act read (``transfer_legs.parent_entries``).
    side = transfer.income_movements if leg.is_income else transfer.expense_movements
    side.append(record)
    db.session.add(record)
    return True


def _follow_day(record: TransactionEntry, day: Optional[SettleDay]) -> bool:
    """Bring *record*'s day pair and statement link to *day*; say whether it moved.

    * The civil day DIFFERS -- a settle, a re-settle, a correction, a revert
      (``None``): the record takes the pair, its purchase day follows a day
      that is set (ruling **R-BAL39**), and its statement link is released
      (ruling **R-FL**), which ``ck_transaction_entries_cleared_needs_settle_day``
      would demand of a revert anyway.
    * The day is the SAME: the pair is re-stated only where *day* raises the
      basis (:func:`~._covering.raises_basis`), and the link stands.

    Returns:
        Whether the record's day pair or link netted a change.
    """
    if not _writes_day(record, day):
        return False
    moves = record.settled_on != (None if day is None else day.day)
    if moves and day is not None:
        record.purchased_on = day.day
    record_settle_day(record, day)
    if moves:
        record.reconciled_by_id = None
    return True


def _writes_day(
    record: Optional[TransactionEntry], day: Optional[SettleDay],
) -> bool:
    """Return whether this arm writes *day*'s pair onto a side holding *record*.

    The one predicate :func:`_follow_day` writes by and
    :func:`sync_side_records`' books grade asks by, so the grade is of exactly
    the days the writes write.  A side holding no record takes *day* with
    the new record :func:`_cover_side` files; a record whose civil day
    differs takes it; on the SAME day, only a day that raises the record's
    basis (:func:`~._covering.raises_basis`) is written.

    Args:
        record: The side's record, or ``None`` when it holds none.
        day: The day the act resolved for the side, or ``None`` out of the
            band.

    Returns:
        Whether the record's day pair is (re-)written.
    """
    if record is None:
        return day is not None
    if record.settled_on != (None if day is None else day.day):
        return True
    return day is not None and raises_basis(day, recorded_settle_day(record))


def _endpoint(leg: "TransferLeg") -> Account:
    """Return the account *leg*'s side is on: the transfer's endpoint for it.

    Read off the transfer's account RELATIONSHIP rather than its id column,
    because an endpoint move earlier in the same act assigns the relationship
    and the column reads the old account until a flush.
    """
    transfer = leg.transfer
    return transfer.to_account if leg.is_income else transfer.from_account


def _withdraw_sides(legs: "tuple[TransferLeg, TransferLeg]", press: Press | None) -> bool:
    """Take every record the two sides hold off the books: a close that moved nothing.

    A ``$0.00`` close keeps no record (rulings **R-BAL82**, **R-BAL141**: no
    day and no link either), and the band's figure arm ties a stored
    ``0.00`` to no record on either side at commit (ruling **R-BAL365**).
    Both sides in ONE call of the one removal act, under the save's press.

    Returns:
        Whether any record was taken off.
    """
    records = [leg.record for leg in legs if leg.record is not None]
    if not records:
        return False
    movement_removal.remove_movements(
        records, legs[0].transfer.user_id,
        because=match_withdrawal.RE_RECORDED, press=press,
    )
    return True


def record_side_clearing(leg: "TransferLeg", anchor_id: int) -> None:
    """Record WHICH statement showed one side of a transfer (ruling **R-FL**).

    The side twin of :func:`._covering.record_clearing`, under its one rule:
    a statement of account X links every fact on X -- here the side's record,
    whose account is its endpoint.  Per SIDE and never mirrored to the other:
    a transfer leaves one bank and arrives at another, and the other
    account's statement is a document nobody read in this act.  A side
    holding no record -- a ``$0.00`` close -- keeps no link (ruling
    **R-BAL141**): there is no money on that account for the statement to
    have shown.  The caller settles first; a link on an un-dated record is
    unstorable (``ck_transaction_entries_cleared_needs_settle_day``).

    The account is read off the assertion the link names, never passed beside
    it, for the row arm's reason (rule 14).

    Args:
        leg: The side, carrying its record as it stands now.
        anchor_id: The ``budget.account_anchor_history`` row that was read.
    """
    record = leg.record
    if record is None:
        return
    statement_account_id = db.session.get(
        AccountAnchorHistory, anchor_id,
    ).account_id
    if record.account_id == statement_account_id:
        record.reconciled_by_id = anchor_id


def free_moving_side_record(
    record: TransactionEntry, owner_id: int, press: Press | None,
) -> None:
    """Free a side's record for its endpoint's move: out of its matches, unlinked, flushed.

    The first half of carrying a side's record to a new endpoint (ruling
    **R-BAL168**; design D5 of plan step ``balance:X-bi-6-4d-2``), called by
    ``transfer_service._endpoints`` BEFORE the transfer's accounts are
    assigned.  A match asserts that a named account's bank line IS this
    payment, and a statement link that a named account's statement showed it;
    money that moved on another account was shown by neither, and the
    composite keys holding each to the record's account
    (``fk_statement_match_members_entry_account``,
    ``fk_transaction_entries_reconciled_by``) would refuse the side key's
    ``ON UPDATE CASCADE`` while either stands.  So the record leaves its
    matches through ``match_withdrawal.withdraw_for_moved_movement`` -- which
    frees a line only where the save's press NAMED it, and otherwise refuses,
    so nothing saves (ruling **R-BAL229**: the page check governs the move,
    and no screen names one today) -- its link is released, and both are
    FLUSHED: the cascade fires inside the transfer's own ``UPDATE``, which the
    unit of work orders before this record's.

    Args:
        record: The moving side's record.
        owner_id: The transfer's owner.
        press: The save's press, or ``None`` when its door named nothing.

    Raises:
        ValidationError: When the withdrawal would free a line *press*'s page
            did not name.
    """
    match_withdrawal.withdraw_for_moved_movement(record, owner_id, press=press)
    record.reconciled_by_id = None
    db.session.flush()


def land_moved_side_record(
    record: TransactionEntry, account: Account, day: Optional[SettleDay],
) -> None:
    """Land a freed side record on its side's NEW endpoint, on its borrowed day.

    The second half of :func:`free_moving_side_record`, called as the
    transfer's accounts are assigned.  The account is assigned in the session
    as both the column and the relationship (ruling **R-BAL46**: the ORM never
    learns what a cascade writes, and the books boundary below reads the
    column).  A DATED record takes *day* -- the moved side's day, now
    ``borrowed`` (ruling **R-BAL168**: its evidence was about the old
    account) -- through ``settle_day.record_settle_day``, whose books boundary
    refuses, by the day's name, a day the NEW account's books do not reach
    before any flush, rather than meeting the deferred
    ``ck_movement_after_books_open`` at COMMIT.  This is the one place a
    side's evidence is LOWERED, because the move voids it; the arm's follow
    rule never lowers it.  An un-dated record (a revert kept it) moves with no
    day.

    Args:
        record: The freed record.
        account: The side's new endpoint (:class:`~app.models.account.Account`).
        day: The moved side's ``borrowed`` day, or ``None`` for an un-dated
            record.

    Raises:
        ValidationError: When *day* is on or before the new account's opening.
    """
    record.account_id = account.id
    record.account = account
    if day is None:
        return
    if record.settled_on != day.day:
        record.purchased_on = day.day
    record_settle_day(record, day)
