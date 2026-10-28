"""
Shekel Budget App -- Transfer Service: WHICH TWO ACCOUNTS a transfer is on

:class:`_Endpoints`, its resolver and its applier -- the leaf that answers
"which account does this transfer's money leave, and which does it arrive at",
and that MOVES a transfer between two pairs of them.

**It exists because a recurring definition states two accounts the write door
could not** (plan step R10-b).  A generated transfer derives six columns from
its template; :func:`app.services.transfer_service.update_transfer` could write
four, so the recurrence engine applied a template's account change by DELETING
every row the definition had generated and re-creating it -- taking each row's
id, its ``notes`` and any settlement record it had retained through a revert
along with it.  Measured on a production clone, one edit of one live template:
51 transfers and 102 shadow rows destroyed and rebuilt to write values identical
to the ones already there, with 51 income legs re-pointed purely as a side
effect.  The same gap made a NON-repeating transfer refuse an account change
outright, so one edit meant two different things depending on whether the
transfer repeated.

Split out of :mod:`._update` at the same step, on the ground every earlier split
from this package used.  Written inline, that module measured 1,018 lines
against pylint's 1,000 cap (it was 805 before this step) -- and finding
**N-152** records that shaving prose is not the answer to that: a package, one
private leaf per responsibility, is.  The seam is the one the responsibility already
draws: :mod:`._update` owns applying an arbitrary field bag and reconciling the
ledger afterwards; which two accounts the pair sits on, and what a move between
them is refused for, is one question and it is this module's.

Flask-isolated like the rest of the package: plain data and ORM rows in,
mutations applied in place, no ``request`` / ``session`` imports, no flush or
commit of its own but the record move's (``status_seam.free_moving_side_record``
flushes a freed record before the transfer's accounts move) -- the caller owns
the session boundary.  (Reading a side's record is a query, which may
autoflush pending writes.)  Every GATE in the update runs before this module
writes; the record move's own refusals can fire after its first write, and the
settle's and the status writer's after the move, so a refused update leaves a
partial write for the caller's rollback to discard.
"""

from typing import NamedTuple

from app.exceptions import ValidationError
from app.models.account import Account
from app.services import status_seam
from app.services.match_press import Press
from app.services.settle_day import recorded_settle_day
from app.services.transfer_legs import TransferLeg, transfer_side_leg
from app.services.transfer_service._side_days import (
    NO_DAYS,
    PairDays,
    pair_fallback_day,
    resolve_pair_days,
)
from app.services.transfer_service._ownership import _get_owned_account
from app.services.transfer_service._validation import (
    TransferRows,
    _reject_unmodeled_source,
)
from app.utils.dates import display_today


class _Endpoints(NamedTuple):
    """The two accounts an update LEAVES a transfer on, and the ones it vacates.

    **A transfer's endpoints move together or not at all**, because
    ``ck_transfers_different_accounts`` is a rule about the PAIR and because
    each leg's label is composed from BOTH accounts
    (:func:`app.services.transfer_legs.leg_label`).  Passing them as one value
    is what stops a call site pairing a resolved source with an unresolved
    destination.

    Attributes:
        from_account: The account the transfer's money leaves once this update
            lands -- the EXPENSE leg's account.  The transfer's current source
            when the update names none.
        to_account: The account it arrives at -- the INCOME leg's account.
        vacated_source_id: The account the money used to LEAVE, when this
            update moves it, else ``None``.
        vacated_destination_id: The account it used to ARRIVE at, when this
            update moves it, else ``None``.  Named apart from the source
            because only the DESTINATION can be a loan, and a loan a payment
            just left is re-reconciled after the move
            (:func:`._loan_posting._resync_vacated_loan`).
    """

    from_account: Account
    to_account: Account
    vacated_source_id: "int | None"
    vacated_destination_id: "int | None"

    @property
    def vacated(self) -> "tuple[int, ...]":
        """Return the account IDs this update moves the transfer OFF.

        Empty when neither endpoint moves, which is every update that names no
        account.  It is what the ledger tail re-derives afterwards, and it is
        deliberately not a boolean: a reconcile has to NAME the accounts it
        walks, and "did something move" cannot.
        """
        return tuple(
            account_id
            for account_id in (
                self.vacated_source_id, self.vacated_destination_id,
            )
            if account_id is not None
        )


def _resolve_endpoints(
    rows: TransferRows, user_id: int, updates: "dict[str, object]",
) -> _Endpoints:
    """Resolve and REFUSE the endpoints this update leaves the transfer with.

    **Plan step R10-b, and it exists because the definition states two accounts
    the write door could not.**  A generated transfer derives six columns from
    its template and :func:`update_transfer` could write four; the recurrence
    engine applied the other two -- a template's source and destination -- by
    DELETING every row the definition had generated and building replacements,
    which took each row's id, its notes and any settlement record it had
    retained through a revert with it.  Measured on a production clone: a single
    edit of one live template destroyed 51 transfers and 102 shadows to write
    values identical to the ones already there, and moved 51 income legs from
    one account to another purely as a side effect of the rebuild.

    Everything a MOVE must be refused for is asked here, and this function
    WRITES NOTHING -- so a move refused for its own reason leaves the transfer
    and its sides' records exactly as it found them.  **That is not the same
    as saying the whole update is atomic, and an adversarial review of R10-b
    is why the difference is spelled out**: :func:`._update.update_transfer`
    applies the caller-stated facts (the override flag, the endpoints, the
    amount) and only then dispatches the SETTLE, which can still raise -- on
    an illegal transition, an unresolvable figure, or one of the seam's
    settle-day refusals.  So "no write precedes a refusal" holds for every
    gate, and a raise from the settle still leaves a partially-applied
    transfer in the session for the caller's rollback to discard.  The
    amount's own refusal was hoisted into that gate block at this step for
    exactly this reason; the settle's cannot be, because they are
    answers the settle computes.  Nothing is re-asked when neither
    endpoint moves, which is the same discipline
    :func:`_reject_installment_move_before_loan` states for the installment
    fields: a pre-existing arrangement stays editable in every OTHER respect
    rather than being re-graded by an unrelated edit.

    The PARENT's name is not touched here: a transfer's own ``name`` is the
    caller's, and every template-linked transfer carries its definition's, so
    re-deriving it on a move would overwrite a stated fact with a default.
    **For an AD-HOC transfer created without a name that leaves a stale label**
    -- ``create_transfer`` defaults the parent's name from the endpoints, and
    nothing afterwards distinguishes that default from a name the owner typed.
    It is latent rather than live: an ad-hoc transfer reaches this arm from no
    route, and both callers that do reach it are template-linked and state the
    definition's name alongside the accounts.  A LEG's label is not stored at
    all: it is composed from the endpoints' current names at render time
    (:func:`app.services.transfer_legs.leg_label`), so a move re-labels both
    legs with nothing written.

    Args:
        rows: The transfer, at its pre-update endpoints.
        user_id: The expected owner -- both resulting accounts are re-checked
            against it, so a caller that skips the route cannot re-point a
            transfer at an account across an ownership line.
        updates: The update kwargs as submitted.

    Returns:
        The :class:`_Endpoints` this update leaves the transfer with.

    Raises:
        NotFoundError: If a submitted account is not *user_id*'s.  The security
            response rule collapses "not found" and "not yours" to one answer.
        ValidationError: If the move would leave the two endpoints equal, or
            if it MOVES the source onto an account no engine models a transfer
            OUT of (:func:`._validation._reject_unmodeled_source`, the ONE set
            both doors call): an amortizing loan -- a disbursement
            (:func:`._loan_posting._reject_transfer_out_of_loan`) -- or, since
            plan step ``credit_card:CC-10``, a credit card -- a cash advance or
            balance transfer (:func:`._validation._reject_transfer_out_of_revolving`).
            A move that leaves the source where it is does not re-ask, for the
            reason the comment at that call states.
    """
    xfer = rows.transfer
    from_id = updates.get("from_account_id", xfer.from_account_id)
    to_id = updates.get("to_account_id", xfer.to_account_id)
    vacated_source = (
        xfer.from_account_id if from_id != xfer.from_account_id else None
    )
    vacated_destination = (
        xfer.to_account_id if to_id != xfer.to_account_id else None
    )
    if vacated_source is None and vacated_destination is None:
        return _Endpoints(xfer.from_account, xfer.to_account, None, None)
    if from_id == to_id:
        raise ValidationError(
            "Source and destination accounts must be different."
        )
    from_account = _get_owned_account(from_id, user_id, label="Source account")
    to_account = _get_owned_account(
        to_id, user_id, label="Destination account",
    )
    # **Asked only when the SOURCE moves**, which an adversarial review of
    # plan step R10-b corrected (the loan refusal stood here alone then; the
    # composed set inherits its placement).  Asked on any endpoint move, it
    # re-graded an arrangement the edit does not touch: a legacy transfer
    # whose source is an amortizing loan -- written before ``create_transfer``
    # guarded it -- could not move its DESTINATION either, which is the freezing
    # :func:`._loan_posting._reject_installment_move_before_loan` states the
    # discipline against three paragraphs down its own docstring.  It also made
    # the vacated-source arm of :func:`._loan_posting._resync_vacated_loan`
    # unreachable, so that function's stated reason for taking both endpoints
    # was false.  Narrowing it restores both.
    if vacated_source is not None:
        _reject_unmodeled_source(from_account)
    return _Endpoints(
        from_account, to_account, vacated_source, vacated_destination,
    )


def _apply_endpoint_move(
    rows: TransferRows, endpoints: _Endpoints, press: Press | None,
) -> None:
    """Move a transfer and both legs onto *endpoints*, carrying each side's record.

    The parent names the pair of accounts and each side's record LIVES on one
    of them (the side key), so the transfer and its records move in one act.
    The twin rows followed by their own TYPE (an expense twin the source, an
    income twin the destination), re-named from both accounts, until plan
    step ``balance:X-bi-6-4d-2`` deleted them; its leaf 3b deleted that loop.
    Each leg is derived from the transfer's endpoints now
    (:mod:`app.services.transfer_legs`), so it moves with them.

    **Applied with the caller-stated facts, ahead of the settle dispatch**, for
    the reason the ``is_override`` arm states: everything between those two
    points is a fact the CALLER stated, and the derivation comes after all of
    them.  The settle builds its own
    :class:`~app.services.cash_ledger.AmountBasis` from the database
    (``._settle.settle``), and that basis resolves an auto-derived loan
    payment's destination loan -- so an update that re-points a payment and
    settles it in one call must have stated the destination first.

    **That is the placement's PRINCIPLE, not a measured defect, and the
    difference is stated rather than blurred.**  Moving the apply after the
    dispatch was tried and the suite stayed green: the only reader that would
    answer differently is ``cash_ledger.LoanPricing.live_cash``, and
    reaching it needs a TEMPLATE-linked derive-mode loan payment re-pointed
    between two loans while settling in the same call -- which no route sends
    and the recurrence engine's maintain pass never does, because it settles
    nothing.  The order is kept because a caller-stated fact belongs before the
    derivation that reads it, and this paragraph is what stops the next reader
    concluding it was measured.

    **It assigns the RELATIONSHIP, not the foreign key, and the difference is a
    measured defect.**  SQLAlchemy does not refresh ``Transfer.to_account`` when
    only ``to_account_id`` moves -- not even across the flush that follows -- so
    every reader that asks the ACCOUNT rather than its id saw the account the
    transfer had before the move.  Measured: after re-pointing a Mortgage
    payment at savings, ``xfer.to_account.name`` still answered ``Mortgage``.
    Two readers depend on it and each fails in a different direction:
    ``_loan_posting._sync_loan_postings_if_loan`` re-derives the genesis of
    whichever loan ``to_account`` names, so a move ONTO a loan would have left
    the NEW loan's ledger unreconciled while a move OFF one reconciled the old
    one by accident; and ``._settle.settle``'s auto-derived loan freeze asks the
    same question, so a combined move-and-settle would have priced the payment
    against the wrong loan.  Assigning the relationship writes both halves at
    once and leaves nothing to remember.

    **A side's payment record moves with its side** (ruling **R-BAL168**, plan
    step ``balance:X-bi-6-4d-2``, design D5; the carry is rulings **R-BAL46** /
    **R-BAL72**'s, for every record, dated or kept un-dated across a revert).
    The record hangs off the TRANSFER, keyed with its account onto the side's
    endpoint, and the side key is ``ON UPDATE CASCADE``: the transfer's own
    ``UPDATE`` carries the record's account in the database.  In order:

    1. **Each moving side's record is freed** (``status_seam.
       free_moving_side_record``): out of every match naming it -- a line is
       freed only where the save's press NAMED it, else nothing saves (ruling
       **R-BAL229**: no screen reaches this act today, so every caller is
       refused for a matched record and an unmatched one moves freely) -- its
       statement link released, and both FLUSHED, because the cascade fires
       inside the transfer's ``UPDATE`` and the keys holding a member and a
       link to the old account would refuse it.
    2. **The transfer's accounts are assigned.**
    3. **Each record lands on its new endpoint** (``status_seam.
       land_moved_side_record``): assigned in the session too, because the ORM
       never learns what a cascade writes; and a DATED record's day becomes
       ``borrowed`` (its evidence was about the old account), derived by the
       one rule (``_side_days.resolve_pair_days``, the moved side's evidence
       dropped) and graded against the NEW account's books by name before any
       flush (``record_settle_day``).

    Args:
        rows: The transfer.
        endpoints: The resolved :class:`_Endpoints`; a no-op when its *vacated*
            is empty, which is every update that names no account.
        press: The save's :class:`~app.services.match_press.Press`, or ``None``
            when its door named nothing (ruling **R-BAL229**).

    Raises:
        ValidationError: When a moving record's match would free a line the
            press did not name, or its day is one the new account's books do
            not reach.
    """
    if not endpoints.vacated:
        return
    transfer = rows.transfer
    moving = tuple(
        (leg, account)
        for leg, account in zip(
            (
                transfer_side_leg(transfer, is_income=False),
                transfer_side_leg(transfer, is_income=True),
            ),
            (endpoints.from_account, endpoints.to_account),
        )
    )
    days = _moved_days(moving)
    # Each side's record as the act FOUND it, read once before the first
    # flush: a leg reads its record live off the transfer's list (ruling
    # plan#1984), and a read after the accounts are assigned that had to load
    # the list would autoflush the move before step 3 lands the records.
    records = tuple(leg.record for leg, _account in moving)
    for (leg, account), record in zip(moving, records):
        if record is not None and leg.account_id != account.id:
            status_seam.free_moving_side_record(
                record, transfer.user_id, press,
            )
    transfer.from_account = endpoints.from_account
    transfer.to_account = endpoints.to_account
    for (leg, account), record, day in zip(moving, records, days):
        if record is not None and leg.account_id != account.id:
            status_seam.land_moved_side_record(record, account, day)


def _moved_days(
    moving: "tuple[tuple[TransferLeg, Account], ...]",
) -> PairDays:
    """Return each side's day after the move: a moved side's day, ``borrowed``.

    The one derivation (``_side_days.resolve_pair_days``) over each side's
    record day, with a MOVING side's evidence dropped (ruling **R-BAL168**:
    the moved payment's date becomes a guess, since the old account's
    statement says nothing about the new one): a moved side borrows a staying
    side's evidence.  **When neither side holds evidence after the move --
    both sides moved, or the staying side's day was itself borrowed -- both
    take ONE day, the first recorded day in expense-then-income order**
    (``pair_fallback_day``: the EXPENSE side's when it records one, else the
    income side's), so a to-side that recorded 01-08 beside a from-side's
    01-06 lands on 01-06, two days earlier.  That is ruling **R-BAL142**'s rule that two
    borrowed sides share one day, and it is the only stable answer: two
    borrowed sides kept on different days would be pulled onto one by the
    next save that re-derives them.  Decided under ruling **R-BAL207** (the
    cp2a review's p1): no door moves a settled transfer's accounts (the
    definition edit skips a transfer holding a record, R-BAL168's own
    premise).  Only a DATED record's day is ever written from this; an
    un-dated one (a revert kept it) moves with no day.

    Args:
        moving: Each side's ``(leg with its record, account after the move)``,
            ``(expense, income)``.

    Returns:
        Both sides' days after the move.
    """
    held = tuple(
        None if leg.record is None else recorded_settle_day(leg.record)
        for leg, _account in moving
    )
    kept = tuple(
        None if leg.account_id != account.id else day
        for (leg, account), day in zip(moving, held)
    )
    if held == (None, None):
        return NO_DAYS
    days = resolve_pair_days(
        PairDays(expense=kept[0], income=kept[1]), NO_DAYS,
        pair_fallback_day(held, display_today()),
    )
    return PairDays(
        expense=days.expense if held[0] is not None else None,
        income=days.income if held[1] is not None else None,
    )
