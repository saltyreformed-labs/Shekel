"""
Shekel Budget App -- Status Seam: the settlement RECORD

WHAT a settle records -- the figure that moved, who wrote it, and the reads
over it -- as a value type plus the three questions asked about one.  The
STATUS mechanics that write it are :mod:`._seam`'s; the invariants that refuse
a malformed one are :mod:`._refusals`'.

Split out of the single ``status_seam`` module at plan step **X-au-c3**, on the
ground ``transfer_service`` and ``cash_ledger`` were split on before it: the
module reached the 1000-line ceiling, and the split is BY RESPONSIBILITY rather
than by line count.  The record is a different subject from the status: a status
says where a row IS and a record says what its money DID, and the second
outlives a change to the first (a revert releases the assertion and keeps what
moved).

**The record carries WHO WROTE the figure since plan step X-bi-3e-1** (rulings
**R-BAL61**, **R-BAL69**), and whether somebody STATED it -- the one reading
of "a correction" -- is derived from that rather than stated beside it
(:attr:`Settlement.stated`).  Two stated fields for one fact would be rule
14's two homes with a fence between them; one field and a property is the
value type holding the invariant by construction.  The row's own
``settled_basis_id`` (``derived`` / ``corrected`` / ``purchases``) was the
second home, written by the seam from this value until plan step
``balance:X-bi-4b-2`` deleted it (ruling **R-BAL80**).

**The record's third fact is the TENDER since plan step ``credit_card:CC-5-3``**
(rulings **R-CC15**, **R-CC42**): the account the covering movement books on,
a write-side field of :class:`Settlement` and, on a read, the movement's own
``account_id`` (:func:`tender_account_id_of`, the seam's one default).

Pure: reads a row's ``entries`` relationship (its covering movement) and the
ref cache, constructs values.  No query of its own, no session, no mutation,
no Flask.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.stated_figure import StatedFigure
from app.utils.balance_predicates import settled_status_ids

if TYPE_CHECKING:
    from app.services.transfer_legs import TransferLeg


@dataclass(frozen=True)
class Settlement:
    """WHAT a settle records: the figure that moved, who wrote it, and the tender.

    The value a settle door hands the seam (:func:`apply_status_change` for a
    row, the Transfer arm's ``_side.sync_side_records`` for a transfer) so the
    whole record -- the day, the figure and its source -- is written in ONE act
    (plan step **X-au-c3**).  Handing the seam one value is what keeps the
    figure and its provenance from being written apart -- ``Settlement.__post_init__``
    refuses a malformed pair, so a door cannot build one to hand over.

    **The constructor is where the record's invariants are enforced**, and
    that placement is the point: a rule that cannot be a constraint should be
    a CONSTRUCTOR invariant rather than prose -- a settle door cannot build a
    malformed record to hand over, so no door can write one.  Three rules
    live here.  A figure names its writer and a writer names a figure (the
    pairing ``ck_transactions_settled_amount_needs_basis`` backstopped while
    the row carried the columns).  A record with no figure is the one whose
    entries state it, an envelope's purchases or a close of nothing.  And
    **the figure is never negative** -- a settle verb takes a MAGNITUDE
    (:class:`~app.services.stated_figure.StatedFigure`), every form field
    that feeds one validates it non-negative, and for a row this is the one
    home of that rule since plan step ``balance:X-bi-4b-2`` deleted
    ``ck_transactions_settled_amount`` with its column: the movement's own
    CHECK is ``amount <> 0``, because a merchant credit is a negative
    PURCHASE (ruling ``bank_import:R-II``), so the storage tier cannot say
    it for a covering movement.  A transfer's stored figure says it again
    (:class:`~app.models.settled_figure.SettledFigure`,
    ``ck_transfers_settled_figure_non_negative``).

    **The SOURCE is the stated field** (plan step **X-bi-3e-1**, ruling
    **R-BAL69**): ``figure_source_id`` on the covering movement (a
    transfer's own ``settled_figure_source_id`` since plan step
    ``balance:X-bi-6-4d-2``) records who wrote the figure -- the settle's
    own pricing (``resolved``), a person (``typed``) or the bank's line
    (``observed``) -- and :attr:`stated` is
    the one reading derived from it.  The row's ``settled_basis_id``
    (``derived`` / ``corrected`` / ``purchases``) was a function of the same
    field, answered by a ``basis`` property here for the seam's column write
    alone through plan step ``balance:X-bi-4b-1``; column, property and
    catalogue went together at ``X-bi-4b-2`` (ruling **R-BAL80**).

    **The TENDER is the record's third fact** (plan step ``credit_card:CC-5-3``,
    rulings **R-CC15** and **R-CC42**): the account the money moved through,
    written as the covering movement's own ``account_id`` -- the card, for a
    bill the owner charged; the row's own account for every settle through
    ``CC-5-2``.  It is a WRITE-side field: ``None`` means the door named no
    tender and the seam books on its one default
    (:func:`tender_account_id_of`: the kept record's account, else the
    row's), so a re-settle after a revert keeps where the money moved exactly
    as it keeps a stated figure (R-CC42).  A record READ back
    (:func:`_movement_settlement`, :func:`recorded_leg_settlement`) leaves
    it ``None``, because a read carries no instruction: :meth:`from_settle`
    gives a re-settle the door's own *tender* whatever the retained record
    held.  No transfer door names a tender: each side's record books on its
    side's endpoint (:mod:`._side`).

    Attributes:
        amount: What moved.  ``None`` exactly when the row's own entries
            state the figure -- an envelope closed from its purchases, or a
            close of nothing (ruling **R-BAL82**) -- where a stored copy
            would need a reconciler.  Never negative.
        source: Who wrote it (:class:`app.enums.MovementFigureSourceEnum`).
            ``None`` exactly when *amount* is: a purchases record has no
            figure of its own to have been written.
        account_id: The TENDER -- the ``budget.accounts.id`` the covering
            movement books on -- when the door named one, already gated
            against the row's owner and the owner's cash-flow set
            (``movement_account.admitted_movement_account_id``); ``None``
            for the seam's default.  Never set on a ``purchases`` record,
            which writes no movement to book anywhere.
    """

    amount: Optional[Decimal]
    source: Optional[MovementFigureSourceEnum]
    account_id: Optional[int] = None

    def __post_init__(self) -> None:
        """Refuse a record whose figure, source and tender contradict each other.

        Raises:
            ValueError: When a figure arrives with no source, a source with
                no figure, a figure below zero, or a tender on a record that
                writes no movement.  A programming error at the call site
                rather than a user error, so it is not a ``ValidationError``:
                no form can express any of the four (every ``settled_amount``
                field validates non-negative; the settle verb's entries
                branch ignores a tender as it ignores a figure, and the
                edit door refuses one on such a row).
        """
        if self.amount is not None and self.amount < 0:
            raise ValueError(
                f"A settlement cannot record a negative figure ({self.amount}): "
                "a settle verb takes a magnitude, and the direction is the "
                "row's own (its transaction type).  A refund is a negative "
                "PURCHASE against the row, never a negative close."
            )
        if self.amount is not None and self.source is None:
            raise ValueError(
                f"A settlement stating the figure {self.amount} must say who "
                "wrote it: the settle's own pricing ('resolved'), a person "
                "('typed') or the bank's line ('observed'). Only a "
                "'purchases' record leaves both out, because there the row's "
                "own entries state the figure."
            )
        if self.amount is None and self.source is not None:
            raise ValueError(
                "A 'purchases' settlement stores no figure, so it names no "
                f"writer: {self.source.value!r} here would credit someone "
                "with a figure the row's own children state. Pass source=None "
                "with amount=None."
            )
        if self.amount is None and self.account_id is not None:
            raise ValueError(
                "A 'purchases' settlement writes no covering movement, so it "
                f"names no tender: account {self.account_id} here would have "
                "nothing to book on. Each purchase carries its own account."
            )

    @property
    def stated(self) -> bool:
        """Return whether somebody STATED this figure: a person or the bank.

        **The one reading of "a correction"** (plan step ``balance:X-bi-4b-1``),
        asked by :meth:`from_settle` to honour a retained record and by
        :func:`honoured_correction` to publish it: a figure the settle
        priced itself (``resolved``) is an inference about a moment that has
        passed, re-derived rather than reused; a figure a person typed or
        the bank's line stated is a FACT, and it outlives the settle that
        recorded it.  A record with no figure states nothing (its entries
        do).  It was read as ``basis is CORRECTED`` off the row's own
        ``settled_basis_id`` through ``X-bi-4a``; that catalogue had no
        member for the bank, and the movement's has.

        Returns:
            ``True`` for a ``typed`` or ``observed`` source; ``False`` for
            ``resolved`` or none.
        """
        return self.source not in (None, MovementFigureSourceEnum.RESOLVED)

    @classmethod
    def from_settle(
        cls,
        booked: Decimal,
        correction: "StatedFigure | None",
        retained: "Settlement | None" = None,
        tender: Optional[int] = None,
    ) -> "Settlement":
        """Return the record a settle makes, given every figure it may have.

        **A stated figure beats the app's, and the rule is stated ONCE here**
        because both settle verbs make the same choice -- the transaction's
        (``transaction_service._settle.settle_transaction``) and the transfer's
        (``transfer_service._settle.settle``).  Two spellings of one money rule
        is this arc's own root cause 1.

        A figure somebody read off a statement is a FACT; what the app resolved
        is an inference, however good.  So a correction wins, and the record says
        WHO stated it -- the person who typed it or the bank line the matcher
        matched (ruling **R-BAL61**) -- which is what makes "did somebody
        correct this, and who" a stored answer rather than one re-derived by
        comparing the figure against a recomputation that may since have moved.

        *correction* is already the ECHO rule's answer: a submitted figure equal
        to what the row would book anyway is not a correction, and both callers
        resolve that through their own echo predicate before they
        get here (finding **N-231**).  So a ``None`` here means "nobody stated
        a different number", not "nobody stated one".

        **A RETAINED correction outlives the settle that recorded it, and
        honouring it here is what makes a revert non-destructive** (plan step
        X-au-c3, developer 2026-08-17).  A revert releases the assertion --
        ``settled_on`` and the clearing link -- and keeps what moved, so a row
        the user reverted in order to edit still carries the figure they read
        off their statement.  Re-deriving over it would delete that figure one
        step later than the release used to, which is the same data loss with an
        extra hop: the popover TELLS the user to revert in order to edit, so the
        round trip has to be lossless or the instruction is a trap.  The
        retained record is returned WHOLE, its source with it: who wrote a
        figure does not change because the row was reverted.

        Only a STATED record is honoured (:attr:`stated`).  A ``resolved`` one
        is the app's own inference about a moment that has passed, and
        re-resolving it is strictly better than reusing it -- the plan may
        legitimately have been re-priced meanwhile.  A ``purchases`` record
        stores no figure at all, so there is nothing to retain: its entries
        still state it.  And a ``$0.00`` stated figure is retained by
        NOTHING (ruling **R-BAL82**): a movement of nothing is not one, so
        the revert keeps no movement and the re-settle re-prices; the owner
        re-types ``$0.00`` if that is still what the bank took.

        Args:
            booked: What the app resolved this row to be worth at the moment of
                the settle.
            correction: The figure a door stated and who wrote it
                (:class:`~app.services.stated_figure.StatedFigure`), when it
                differs from *booked*; ``None`` otherwise.
            retained: What the row already records, when it still carries a
                record from an earlier settle it has since been reverted out of
                (``status_seam.recorded_settlement``); ``None`` when it carries
                none.
            tender: The account the door named for the covering movement,
                already gated (plan step ``credit_card:CC-5-3``), or ``None``
                for the seam's default.  It rides every arm alike: the
                retained record is honoured WHOLE for its figure and source,
                and its tender is the seam's default anyway (the kept
                movement's own account, ruling **R-CC42**), so a named tender
                replaces nothing the retained record states.

        Returns:
            A ``corrected`` record for a figure stated now or stated before and
            not withdrawn, else a ``derived`` one on the ``resolved`` source;
            each carrying *tender*.
        """
        if correction is not None:
            return cls(
                amount=correction.amount, source=correction.source,
                account_id=tender,
            )
        if retained is not None and retained.stated:
            return replace(retained, account_id=tender)
        return cls(
            amount=booked, source=MovementFigureSourceEnum.RESOLVED,
            account_id=tender,
        )


def _source_named(figure_source_id: int) -> MovementFigureSourceEnum:
    """Return WHO wrote a figure, decoded from its ``ref.movement_figure_sources`` id.

    The one decoding, read for a row's covering movement
    (:func:`_movement_settlement`) and a transfer's settled figure
    (:func:`recorded_leg_settlement`).

    Raises:
        KeyError: When *figure_source_id* names no
            :class:`~app.enums.MovementFigureSourceEnum` member.  Unreachable
            through the FK, which admits only the seeded rows; it is how a
            member ADDED without this map being extended fails loudly.
    """
    return {
        ref_cache.movement_figure_source_id(member): member
        for member in MovementFigureSourceEnum
    }[figure_source_id]


def recorded_settlement(row: Transaction) -> Optional[Settlement]:
    """Return the settlement *row* already records, or ``None`` if it has none.

    The read half of the record, for the callers that must carry a row's
    record forward without inventing either term: the transaction settle
    verb honouring a RETAINED correction across a revert
    (:meth:`Settlement.from_settle`; :func:`honoured_correction` is the same
    read's projection), and ``transaction_service._door``'s tender-only
    correction, which re-points the row's record whole.  A transfer's record
    is :func:`recorded_leg_settlement`'s.

    **The record is read off the COVERING MOVEMENT** (plan step
    ``balance:X-bi-4b-1``, ruling **R-BAL80**): the row stores no writer --
    it never did -- and the movement the seam wrote for the record is where
    the figure and its source live (``amount``, ``figure_source_id``; the
    row's own ``settled_amount`` / ``settled_basis_id`` were a stale cache of
    it, read by nothing here since 4b-1 and deleted at ``X-bi-4b-2``).  A
    row reverted out of the band KEEPS that movement, un-dated, since plan
    step ``X-bi-3e-2`` (ruling **R-BAL61**,
    :attr:`~app.models.transaction.Transaction.covering_movements`), so a
    reverted row's retained record is read where it was written, whatever
    its status.  Three answers, total over what a row can hold:

    * a covering movement -- its figure and its source, dated or kept;
    * none, and the row is SETTLED -- ``Settlement(None, None)``: the row's
      entries ARE its record (an envelope's purchases; a close of nothing,
      ruling **R-BAL82**);
    * none, and the row is not settled -- ``None``: it records nothing a
      re-settle could honour.  A ``$0.00`` stated figure lands here after a
      revert, and is honoured by nothing (R-BAL82): a movement of nothing
      is not one, so nothing carried it across.

    Ruling **R-BAL70**'s cutover mapping -- a record with no movement read
    ``derived`` -> ``resolved``, ``corrected`` -> ``typed`` off the row's
    basis column -- answered the two states a movement could not carry
    through ``X-bi-4a``: the ``$0.00`` record and a row reverted on the
    3d-only tree before 3e-2 deployed.  Both are R-BAL82's now (0 of either
    on the 2026-09-19 production restore); the mapping retired at 4b-1 and
    the column at 4b-2.

    Args:
        row: The transaction to read, with ``entries`` loaded or loadable.

    Returns:
        The recorded :class:`Settlement`, or ``None``.

    Raises:
        KeyError: When the movement's ``figure_source_id`` names no member
            of its enum (:func:`_source_named`).
        ValueError: When the row holds more than one covering movement
            (:func:`covering_movement_of`).
    """
    return _recorded(covering_movement_of(row), row.status_id)


def recorded_leg_settlement(leg: "TransferLeg") -> Optional[Settlement]:
    """Return the settlement a transfer *leg*'s TRANSFER records, or ``None``.

    :func:`recorded_settlement`'s twin over a transfer leg (leaf
    ``balance:X-bi-6-4d-1``).  **A transfer's settled figure has ONE home,
    the transfer** (plan step ``balance:X-bi-6-4d-2``, rulings
    **R-BAL365**, **R-BAL366**:
    :attr:`~app.models.transfer.Transfer.settled_figure_pair`), so both legs
    answer the same record and the side the caller names cannot choose a
    different one.  Two answers, total over what a transfer can store:

    * a stored figure -- it and who wrote it, settled or kept across a revert
      (ruling **R-BAL61**); ``0.00`` for a close of nothing (ruling
      **R-BAL365**), where this answered ``Settlement(None, None)`` read off
      the missing records until this step;
    * none -- ``None``: the transfer records nothing a re-settle could honour
      (never settled; a reverted ``$0.00`` close, whose figure the revert
      cleared; or a soft-deleted transfer whose delete took its records off,
      and its figure with them).

    Asked by ``transfer_service._settle.settle`` for the pair's retained
    record, by ``transfer_service._settle.leg_settle_amount`` for the
    offered leg's and by
    ``transaction_service._settle.leg_retained_amounts_by_key`` for a grid
    leg's.

    Args:
        leg: A :class:`~app.services.transfer_legs.TransferLeg`.

    Returns:
        The recorded :class:`Settlement`, or ``None``.

    Raises:
        KeyError: When the stored source names no member of its enum
            (:func:`_source_named`).
    """
    stored = leg.transfer.settled_figure_pair
    if stored is None:
        return None
    return Settlement(
        amount=stored.figure, source=_source_named(stored.source_id),
    )


def _recorded(
    movement: Optional[TransactionEntry], status_id: int,
) -> Optional[Settlement]:
    """Return the record a row holding *movement* in *status_id* states.

    :func:`recorded_settlement`'s body: the covering movement's figure and
    source when there is one; ``Settlement(None, None)`` for a SETTLED row
    with none (its entries are its record, ruling **R-BAL82**); else
    ``None``.  A transfer's record is its own column pair
    (:func:`recorded_leg_settlement`) since plan step
    ``balance:X-bi-6-4d-2``.
    """
    if movement is not None:
        return _movement_settlement(movement)
    if status_id in settled_status_ids():
        return Settlement(amount=None, source=None)
    return None


def covering_movement_of(row: Transaction) -> Optional[TransactionEntry]:
    """Return the ONE covering movement *row* holds, dated or kept, or ``None``.

    The read every reader of the record shares -- :func:`recorded_settlement`
    for the figure and its source, :func:`tender_account_id_of` for the
    account, and the seam's own ``_covering._cover`` for the row it re-dates
    on a re-settle -- so the count invariant is stated once rather than at
    each of them.

    Args:
        row: The transaction to read, with ``entries`` loaded or loadable.

    Returns:
        The movement marked ``covers_settlement``, or ``None`` when the row
        holds none.

    Raises:
        ValueError: When the row holds more than one covering movement --
            unstorable under ``uq_transaction_entries_one_settlement_record``,
            so reaching it means a second was written around the index and
            the seam.
    """
    movements = row.covering_movements
    if not movements:
        return None
    movement, *extra = movements
    if extra:
        raise ValueError(
            f"Transaction {row.id} holds {len(movements)} covering "
            "movements; a settle writes exactly one, so a second can only "
            "have reached the table around the status seam."
        )
    return movement


def tender_account_id_of(row: Transaction) -> int:
    """Return the account a settle of *row* books on when no tender is named.

    **The ONE default for the tender**, read by the seam's writer
    (``_covering._cover``, for a :class:`Settlement` whose ``account_id`` is
    ``None``) and by the full-edit popover to preselect its "Paid from"
    picker -- so what the screen shows selected and what an untouched Save
    books cannot differ, and an untouched picker posts an ECHO the door
    drops (``_seam.tender_for_status``).

    **Where the money moved is retained across a revert** (plan step
    ``credit_card:CC-5-3``, ruling **R-CC42**, developer 2026-09-21), exactly
    as a stated figure is (:meth:`Settlement.from_settle`): a row holding a
    kept record answers that record's account, so the one-click re-settle of
    a reverted card-tendered bill keeps its ``$120`` on the card; a row
    holding none answers its own account -- where it is EXPECTED to be paid
    from (ruling **R-CC16**).  This AMENDS ruling **R-CC36**'s re-point
    clause: a Projected row whose definition moved accounts keeps its kept
    record where the money moved, and only a NAMED tender moves it (the
    refused alternative booked the reverted card bill on checking silently,
    the card ``$120`` low and checking ``$120`` high).

    Args:
        row: The transaction about to settle, re-settle or be drawn.

    Returns:
        A ``budget.accounts.id``: the kept covering movement's, else the
        row's own.

    Raises:
        ValueError: From :func:`covering_movement_of`, on a row holding two.
    """
    movement = covering_movement_of(row)
    return row.account_id if movement is None else movement.account_id


def _movement_settlement(movement) -> Settlement:
    """Return the :class:`Settlement` a row's covering movement states.

    The leaf :func:`recorded_settlement` reads through.  It was published
    from leaf ``X-bi-6-1`` for a transfer's leg, whose retained figure was
    read off its side's record; a transfer's figure is its own since plan
    step ``balance:X-bi-6-4d-2`` (:func:`recorded_leg_settlement`), so the
    one reader left is the row's.

    Args:
        movement: A :class:`~app.models.transaction_entry.TransactionEntry`
            with ``covers_settlement`` set.

    Returns:
        Its figure and who wrote it.

    Raises:
        KeyError: When the movement's ``figure_source_id`` names no member of
            its enum (:func:`_source_named`).
    """
    return Settlement(
        amount=movement.amount, source=_source_named(movement.figure_source_id),
    )


def honoured_correction(row: Transaction) -> Optional[Decimal]:
    """Return the figure a RETAINED correction still states, or ``None``.

    **The ONE statement of "a human's figure outlives the settle that recorded
    it"**, asked by :func:`settle_amount` -- what the reconcile panel OFFERS --
    and by :func:`settle_transaction` -- what a tick BOOKS -- so the two cannot
    answer differently (plan step X-au-c3, developer 2026-08-17).

    A revert releases the ASSERTION and keeps what moved
    (``status_seam.apply_status_change``), so a row the user reverted in order
    to edit still carries the figure they read off their statement.  This is
    what makes that figure AUTHORITATIVE rather than merely remembered: it is
    what a re-settle books, and therefore what the panel must show.

    **Both halves are load-bearing, and the first draft had only the second.**
    ``Settlement.from_settle`` honoured a retained correction while
    :func:`settle_amount` went on pricing the PLAN, so a reverted ``$500.00``
    bill that had been corrected to ``$245.32`` was OFFERED at ``$500.00`` and
    BOOKED at ``$245.32`` -- measured end to end through the panel.  Two
    consequences, and the second is worse than the drift: the figure a tick
    booked was one the screen never showed, and because a submitted figure
    counts as a correction only when it DIFFERS from the offer
    (:func:`_is_correction`), no input the user could give meant "book the
    plan".  Answering here fixes both -- the offer equals the booking, and
    typing any other number is a genuine correction that displaces this one.

    Only a STATED record is honoured (:attr:`Settlement.stated`): the
    retained movement's figure when a person typed it or the bank's line
    stated it.  A ``resolved`` one is the app's own inference about a moment
    that has passed, and re-resolving it is strictly better -- the plan may
    legitimately have been re-priced meanwhile.  A ``purchases`` record
    stores no figure, and this is not reached for such a row:
    :func:`settle_amount` takes the entries branch above it.

    **It reads the covering movement, as :func:`recorded_settlement` does,
    and is that read's one-line projection** (plan step ``balance:X-bi-4b-1``)
    -- it read the row's two columns through ``X-bi-4a`` "so a whole grid
    costs no ``entries`` load", and the grid loads them
    (``routes/grid/_items.load_grid_items`` through
    ``valuation_load_options``; ``companion_service`` likewise), as every
    batch reader of :func:`~app.services.row_valuation.settled_figure` does
    since the same step.  A whole grid asks it per unsettled row
    (``retained_settle_amounts_by_id``).

    Args:
        row: The row about to be offered or settled.

    Returns:
        The retained correction's figure, or ``None`` when the row holds no
        stated record.
    """
    return honoured_figure(recorded_settlement(row))


def honoured_figure(recorded: Optional[Settlement]) -> Optional[Decimal]:
    """Return the figure *recorded* still states, or ``None``.

    :func:`honoured_correction`'s rule over a record already read, so a
    reader holding a transfer leg rather than a row (a leg's grid cell, leaf
    ``X-bi-6-1``) applies the SAME rule to
    :func:`recorded_leg_settlement` rather than restating it: only a STATED
    record is honoured, for the reason the row-shaped reader gives.

    Args:
        recorded: What :func:`recorded_settlement` or
            :func:`recorded_leg_settlement` answered, or ``None``.

    Returns:
        The retained correction's figure, or ``None``.
    """
    if recorded is None or not recorded.stated:
        return None
    return recorded.amount


def correction_record(
    recorded: Optional[Decimal], submitted: StatedFigure,
) -> Optional[Settlement]:
    """Return the record a stated CORRECTION makes, or ``None`` for an echo.

    **The Actual box's rule, stated once for both tables** (developer ruling,
    2026-08-17): the estimate and the actual are two different facts about a
    row, so they get two boxes, and editing the actual states what the bank
    really took.  ``transaction_service._door`` asks it with a plain row's
    record and ``transfer_service._update`` with the figure a transfer
    stores, read through its expense leg; a second spelling of the echo
    comparison is how the two tables would come to disagree about what counts
    as a correction.  It takes the RECORDED FIGURE rather than the row since
    leaf ``balance:X-bi-6-4d-1``, so each table reads its own record its own
    way -- a row off its entries, a transfer off its own stored figure
    (:func:`~app.services.row_valuation.leg_settled_figure`) -- and the rule
    stays one.

    **An ECHO writes nothing.**  Both popovers PREFILL the box with what the row
    already records, so an untouched Save posts the same figure back; recording
    it would restamp a ``derived`` record as ``corrected`` and manufacture a
    correction nobody made -- destroying the only stored signal that says a
    human read a number off a statement, which ruling **R-FB**'s production
    measurement ("11 of 93 settled bills carry a hand-typed correction") is made
    of.  A real correction records WHO stated it (``typed`` from either
    popover; the source is the door's word, ruling **R-BAL61**), which is
    what tells a figure somebody stated from one the app resolved
    (:attr:`Settlement.stated`).

    **The comparison is against what the row RECORDS, not its plan**
    (:func:`app.services.row_valuation.settled_figure`, the sum of its
    entries, or :func:`~app.services.row_valuation.leg_settled_figure` for a
    leg -- the same maps the box was PREFILLED from).  The two differ
    whenever a correction stands, so comparing against the plan would read
    every re-save of a corrected row as a fresh correction of the same
    figure.  It read a TOTAL twin, ``recorded_figure``, through ``X-bi-4a``,
    whose one clause was ``None`` for a settled row that RECORDS NOTHING
    (finding **N-181**'s legacy shape, which the box existed to repair) where
    the counting read raised; a settled row with no entries is the ``$0.00``
    record since plan step ``balance:X-bi-4b-1`` (ruling **R-BAL82**), so the
    box shows ``0.00`` for it and any other figure typed there is a
    correction, which is the repair.

    Args:
        recorded: What the settled row or leg being corrected already records
            (the caller's :func:`~app.services.row_valuation.settled_figure`
            or :func:`~app.services.row_valuation.leg_settled_figure`).  The
            CALLER establishes that it is settled;
            :func:`reject_figure_without_settled_status` is that check.
        submitted: The figure the door stated and who wrote it
            (:class:`~app.services.stated_figure.StatedFigure`).

    Returns:
        A stated :class:`Settlement`, or ``None`` when *submitted*'s figure
        equals what the row already records.
    """
    if recorded == submitted.amount:
        return None
    return Settlement(amount=submitted.amount, source=submitted.source)
