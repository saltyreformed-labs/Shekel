"""
Shekel Budget App -- Transfer Service: the UPDATE verb

The path that EDITS an existing transfer, and its two sides' payment records
with it (on a status move through the one writer,
``_status.apply_status_to_all_three``; an endpoint move carries them,
:mod:`._endpoints`); a delete takes the records off, through
:mod:`._delete`.  It
also mirrored the transfer's period, category, due date and override flag
onto two shadow :class:`~app.models.transaction.Transaction` rows, in this
module and nowhere else, until plan step ``balance:X-bi-6-4d-2`` deleted the
rows; that step's leaf 3b deleted the mirrors.  A shadow's status and day
were no longer kept from ``X-bi-6-4d-2``: nothing read them.

Its tail is the posting reconcile (:func:`_reconcile_postings_after_update`),
which brings the double-entry ledger back in step after the kwargs land -- once
the FINAL amount and status are in place rather than at the field that moved.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.services.transfer_service import _settle
from app.services.transfer_service._amount import (
    apply_amount_ownership,
    grade_amount_ownership,
)
from app.services.transfer_service._endpoints import (
    _apply_endpoint_move,
    _resolve_endpoints,
)
from app.services.transfer_service._loan_posting import (
    _POSTING_RELEVANT_INSTALLMENT_FIELDS,
    _reject_installment_move_before_loan,
)
from app.services.transfer_service._placed import (
    re_place_and_grade_the_day,
    restate_definition_price,
)
from app.services.transfer_service._posting_sync import (
    _reconcile_postings_after_update,
)
from app.services.transfer_service._ownership import (
    _get_owned_category,
    _get_owned_period,
)
from app.services.match_press import Press
from app.services.row_valuation import settled_figure
from app.services.status_seam import (
    correction_record,
    figure_for_status,
)
from app.services.stated_figure import StatedFigure
from app.services.transfer_service._side_days import (
    NO_DAYS,
    PairDays,
    SideDay,
    stated_by_side,
)
from app.services.transfer_service._status import (
    apply_status_to_all_three,
    reject_stated_days_without_settle,
)
from app.services.transfer_service._validation import (
    TransferRows,
    _get_transfer_or_raise,
)
from app.utils.balance_predicates import enters_settled_band
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)

#: The kwargs a SETTLE owns outright when one runs.  Each is an input to
#: :func:`app.services.transfer_service._settle.settle`, which writes all three
#: columns itself -- so leaving them in the field-application loop below would
#: write each of them a second time.  That is not hypothetical tidiness: it is
#: exactly what this module did until plan step X-f2-c3, where every reconcile
#: tick stamped the settle day with the derived one and then rewrote it with the
#: statement's through ruling **R-ED**'s CORRECTION door.  ``side_days``
#: replaced the pair's one ``settle_day`` at plan step ``balance:X-bi-6-4c-3``.
_SETTLE_OWNED_FIELDS = frozenset({"status_id", "figure", "side_days"})


def _fields_the_settle_left(
    updates: "dict[str, object]",
) -> "dict[str, object]":
    """Return the kwargs still owed an application after a settle ran.

    Every kwarg but :data:`_SETTLE_OWNED_FIELDS`, which the settle wrote as one
    act.  A ``settle_day=None`` (a request to CLEAR the day) needed an exception
    here until plan step ``balance:X-bi-6-4c-3``; a :class:`~._side_days.SideDay`
    cannot wrap ``None``, so that state has no spelling any more.

    Args:
        updates: The update kwargs as submitted.

    Returns:
        The kwargs the settle did not consume.
    """
    return {
        key: value for key, value in updates.items()
        if key not in _SETTLE_OWNED_FIELDS
    }


def _grade_submitted_figure(
    rows: TransferRows, updates: "dict[str, object]",
) -> None:
    """Read what a submitted FIGURE means, and refuse a real conflict.

    **Asked before any field is written**, which is the whole reason it is a
    separate gate rather than an arm among the writes: a refused request must
    leave the transfer and both sides' records untouched, and ``is_override`` and
    ``amount`` are already staged by the time :func:`_apply_remaining_fields`
    runs.  It is the transfer twin of the ordering
    ``transaction_service._door._correction_for_status`` keeps.

    The reading is :func:`app.services.status_seam.figure_for_status`, shared
    with the transaction door so the two tables cannot come to phrase one money
    rule two ways.  What is graded is the status this update LEAVES, not the one
    it starts from: a figure riding a settle is legal (the settle owns it), a
    figure on a pair that stays settled is a CORRECTION, and a figure on
    anything else is asking to record money that has not moved.

    **An ECHO is DROPPED rather than refused, and that is why this mutates
    *updates* instead of only raising.**  Both full-edit forms re-submit the
    whole row, and the documented way to unlock a finalised transfer is to set
    Status to Projected in that same form -- so a revert arrives carrying the
    Actual box's untouched contents.  Refusing that would break the unlock path
    on every settled transfer, which is the trap ruling **R-EG** removed for the
    settle day.  A figure the user CHANGED is a different thing and is refused.

    The record is the transfer's one settled figure, read off the transfer
    itself (``row_valuation.settled_figure`` of the parent, ruling
    **plan#1983**): both legs would answer it, so neither is asked.

    Args:
        rows: The transfer, at its pre-update status.
        updates: The update kwargs as submitted.  Mutated in place: an echoed
            ``figure`` has its key removed.

    Raises:
        ValidationError: When a ``figure`` DIFFERING from what the pair
            records arrives beside a status that settles nothing.
    """
    if updates.get("figure") is None:
        return
    figure = figure_for_status(
        rows.transfer,
        updates.get("status_id", rows.transfer.status_id),
        updates["figure"],
        settled_figure(rows.transfer),
    )
    if figure is None:
        del updates["figure"]


def _dispatch_settle(
    rows: TransferRows, updates: "dict[str, object]", stated: PairDays,
    press: Press | None,
) -> "bool | None":
    """Run the SETTLE when *updates* moves this transfer into the settled band.

    **The dispatch that makes a transfer's settle rule structural** (plan step
    X-f2-c3, ruling **R-FA**).  Moving a transfer into the settled band is not
    just a status change: an auto-derived loan payment books what it is LIVE
    worth rather than the creation-time escrow its estimate carries, each side
    is dated by whoever knows its day, and an echoed prefill is not recorded as
    a human's figure.  What those rules ARE
    is :mod:`._settle`'s; this decides WHEN they apply.

    **Why here and not at each door.**  Four doors could move a transfer into
    the settled band and exactly ONE of them froze -- the grid's shadow "Mark
    Paid", which called the loan service itself and passed the answer down.  The
    transfers page's "Mark Done", the transfer full-edit Status dropdown and a
    transaction PATCH landing on a shadow all booked the stored estimate.  That
    is finding **N-219**'s shape on the transfer table: a ROUTE holding a money
    rule, so one control books a different figure from another for the same
    payment.  Putting it at the one chokepoint every transfer mutation already
    passes through means a FIFTH door cannot be written without it -- and
    :func:`settle_transfer` gives the doors that mean only this a name to call
    instead of a kwargs bag to assemble.

    Args:
        rows: The transfer, at its pre-update status.  The
            caller-stated facts (``is_override``, ``amount``) are already
            applied, which is what lets the settle read the post-edit state.
        updates: The update kwargs as submitted.
        stated: The days stated by side, which a settle entering the band
            admits whole.
        press: The save's press, or ``None`` (:func:`update_transfer`).

    Returns:
        ``None`` when this update does not settle -- so the caller leaves every
        kwarg to the ordinary field application.  Otherwise whether the settle
        booked a HUMAN's figure.  The three states are distinct because the
        caller needs both answers from one question: DID a settle run (which
        decides who owns the three kwargs it consumes) and did it record a
        correction (which the reconcile writer counts).
    """
    if "status_id" not in updates:
        return None
    if not enters_settled_band(rows.transfer, updates["status_id"]):
        return None
    return _settle.settle(
        rows, updates["status_id"],
        submitted=updates.get("figure"), stated=stated, press=press,
    )


def _apply_remaining_fields(
    rows: TransferRows, updates: "dict[str, object]", *,
    stated: PairDays, date_moves: bool, press: Press | None,
) -> None:
    """Apply every field a SETTLE does not own.

    It also MIRRORED each field onto the twin rows, here and nowhere else,
    until plan step ``balance:X-bi-6-4d-2`` deleted them (its leaf 3b deleted
    the mirrors): a transfer is one row, each side's leg reads the transfer's
    period, category and due date (``transfer_legs``), and each side's
    record is the status seam's Transfer arm's (plan step
    ``balance:X-bi-6-4d-2``).

    :data:`_SETTLE_OWNED_FIELDS` reach it only when this update did NOT settle,
    because a settle writes all three as one act and they are dropped before
    this runs.  So a ``status_id``, a stated day or a ``figure`` reaching the
    arms below belongs to a non-settling change -- a revert, a cancel, or a
    CORRECTION to what a pair already recorded -- and each arm says what that
    means.

    Args:
        rows: The transfer.
        updates: The kwargs left for this function to apply.
        stated: The days by side this change states, as its verb admitted
            them -- empty after a settle, which consumed them.
        date_moves: Whether this update leaves a PLACED transfer on a
            different day than it has -- the one predicate under which its
            occurrence follows its date (**R-BAL94**), decided by the caller
            before its first write beside the sibling refusal that shares
            it, so the two cannot part.
        press: The save's press, or ``None`` (:func:`update_transfer`).

    Note:
        It takes no ``user_id``: the two ownership refusals it used to make now
        run before the first write (:func:`_reject_unowned_references` and the
        period resolution in :func:`_apply_transfer_updates`), so what is left
        here is assignment only.

    Raises:
        ValidationError: From an illegal transition or a day the seam refuses.
    """
    # ── status_id + the settlement RECORD ─────────────────────────
    # ONE seam pass carrying both, mirroring
    # :func:`app.services.transaction_service._door.apply_requested_status` one
    # table over -- a status change and a figure correction are INDEPENDENT
    # facts, and a door that treats them as alternatives drops whichever it
    # decides against.  That is not hypothetical: the transaction door returned
    # early after recording a figure, so a row moving Paid -> Settled while
    # carrying a corrected Actual recorded the figure and never archived.
    # (That was the terminal ARCHIVE status, deleted at plan step
    # **balance:X-am**.  The measurement is quoted as it was taken; what it
    # established -- two independent facts in one request are not alternatives
    # -- is about the DOOR and outlived the status it was found on.)
    #
    # The status half is a change that does NOT settle: a cancel, or a revert
    # out of the settled band.  Both transitions are verified before any
    # propagation, then applied
    # through the ONE status seam, which owns the F-048 defense-in-depth
    # ``settled_on`` synchronization and the ``status`` expire; see
    # :func:`app.services.transfer_service._status.apply_status_to_all_three`
    # for the full audit rationale.  The seam CLEARS the day on the way out of
    # the band, and there is no way in from here -- :func:`_dispatch_settle`
    # has already taken every such move.
    #
    # The DAY half is a CORRECTION (ruling **R-ED**, per side since plan step
    # ``balance:X-bi-6-4c-3``): a day stated for a side of a pair that stays
    # settled, applied in the same identity pass, where a borrowing sibling
    # follows (ruling **R-BAL142**).  It had a door of its own
    # (``apply_settle_day_correction``) until that step.
    #
    # The RECORD half is the Actual box's write door (developer ruling,
    # 2026-08-17).  **A transfer's DAY was correctable in place and its FIGURE
    # was not**, which is the exact asymmetry that ruling objects to: the day
    # travelled through its own correction door and the figure was
    # REFUSED outright, so the only way to restate what the bank took was to
    # revert the transfer, edit, and settle it again -- and a revert RETAINS
    # the recorded figure, so the re-settle silently re-booked the old number
    # over the re-planned one.  The lock produced a wrong figure, not friction.
    #
    # ``new_status_id`` defaults to the pair's CURRENT status, so a figure or a
    # day arriving alone reaches the seam as an identity transition, the
    # reason the seam stays the single writer of the settlement columns.
    #
    # The record goes to the TRANSFER through ``apply_status_to_all_three``,
    # whose arm (``status_seam.sync_side_records``) stores it once as the
    # transfer's settled figure: a side's record holds no figure
    # (``ck_transaction_entries_side_record_states_no_figure``).
    new_status_id = updates.get("status_id", rows.transfer.status_id)
    # The transfer's one settled figure, read off the transfer itself
    # (ruling plan#1983): both legs would answer it, so neither is asked.
    # A figure only ever reaches here as a CORRECTION -- a settling one is the
    # settle's (:data:`_SETTLE_OWNED_FIELDS`), and one on an unsettled pair was
    # refused before any field was written (:func:`_grade_submitted_figure`).
    submitted = updates.get("figure")
    correction = (
        None if submitted is None
        else correction_record(settled_figure(rows.transfer), submitted)
    )
    if "status_id" in updates or correction is not None or stated != NO_DAYS:
        apply_status_to_all_three(
            rows, new_status_id, stated=stated, settlement=correction, press=press,
        )

    # ── pay_period_id ──────────────────────────────────────────────
    # Ownership was refused before the first write
    # (:func:`_reject_unowned_references`); this arm only assigns.
    if "pay_period_id" in updates:
        rows.transfer.pay_period_id = updates["pay_period_id"]

    # ── category_id ────────────────────────────────────────────────
    # Both legs read the transfer's category, so the transfer appears under
    # the user-selected category in both account grids.
    if "category_id" in updates:
        rows.transfer.category_id = updates["category_id"]

    # ── name ───────────────────────────────────────────────────────
    # Name is display metadata on the transfer.  A leg's label is composed
    # from the endpoints' names (``transfer_legs.leg_label``), not from it.
    if "name" in updates:
        rows.transfer.name = updates["name"]

    # ── notes ──────────────────────────────────────────────────────
    # Notes live on the transfer.
    if "notes" in updates:
        rows.transfer.notes = updates["notes"]

    # ── due_date ──────────────────────────────────────────────────
    # **A PLACED transfer's occurrence follows its date** (plan step
    # ``balance:X-ci-1``, ruling **R-BAL94**: a one-time transfer records its
    # own due date as the occurrence it answers, the twin of
    # ``one_off.state_due_date``).  This arm is the ONE writer of a transfer's
    # date -- every door that moves one calls ``update_transfer`` (Transfer
    # Invariant 4) -- so writing ``occurs_on`` beside it here is what leaves
    # a MOVED transfer's two columns equal, whichever door moved the date and
    # whether it typed the day or the period move re-placed it
    # (``._placed.re_place_and_grade_the_day``).  Two placed shapes hold them
    # apart until their date first moves: a cleared cadence's survivor
    # answers the occurrence its cadence named (below), and a one-time
    # transfer born before X-ci-1 answers none until ``X-ci-3`` backfills
    # it.  **Only when the date MOVES**, which
    # is the twin's guard (``_field_updates._place_the_date``: ``if due ==
    # txn.due_date: return``) and X-ci-1's adversarial review found it
    # missing: the full-edit form posts ``due_date`` on EVERY save, so a
    # notes-only save of a cleared cadence's survivor -- whose occurrence is
    # the day its cadence named, not its due day (a monthly rule with a due
    # day; a first-of-month rule due on the payday) -- was silently re-keyed
    # onto its due date, and a cadence re-added later that names the old
    # day no longer adopted it.  A GENERATED transfer's ``occurs_on`` is the
    # occurrence its cadence named and a re-dating by the maintain pass does
    # not move it; an ad-hoc transfer answers no occurrence.
    if "due_date" in updates:
        new_due = updates["due_date"]
        rows.transfer.due_date = new_due
        if date_moves:
            rows.transfer.occurs_on = new_due


def _reject_unowned_references(
    user_id: int, updates: "dict[str, object]",
) -> None:
    """Refuse an unowned category BEFORE any field is written.

    The user-scoped FK :func:`update_transfer` accepts that no derivation
    answers, checked with the other pre-write gates rather than at the arm
    that assigns it.  It ran inside :func:`_apply_remaining_fields`, which is
    AFTER :func:`_dispatch_settle` has written the transfer's status and
    settled figure and its sides' records -- so an unowned id raised with the
    settlement already staged, and the route's ``NotFoundError`` exit returns
    404 without rolling back (unlike ``_error_transfer_response``, which does).
    Nothing persists today, because Flask-SQLAlchemy's teardown removes the
    session, but it made this module's own rule -- every refusal before the
    first write -- untrue of two of its refusals (neutral review, 2026-08-18).

    ``category_id`` is re-checked at the route boundary too (commit C-27 /
    F-043); this is the service tier's own, so a caller that skips the route
    cannot write across an ownership line.  **``pay_period_id`` is not here
    any more** (plan step ``balance:X-ci-1``): it was resolved off the owner's
    calendar HERE and, two gates earlier, inside
    ``._loan_posting._reject_installment_move_before_loan`` -- the second walk
    ``pay_calendar:C13-b`` measured and left -- and the re-placing rule that
    leaf adds needed the same paycheck a third time.  :func:`_apply_transfer_updates`
    resolves it ONCE now, ahead of every gate that reads it, and that
    resolution IS the period's ownership check (an id another owner holds is
    absent from the calendar, ruling **R-BAL96**).

    Args:
        user_id: The owner every referenced row must belong to.
        updates: The update kwargs as submitted.

    Raises:
        NotFoundError: If a submitted ``category_id`` is not *user_id*'s.
            The security response rule collapses "not found" and "not yours"
            to one answer.
    """
    if updates.get("category_id") is not None:
        _get_owned_category(updates["category_id"], user_id)


def _apply_transfer_updates(transfer_id, user_id, updates, *, settle_only=False, press):
    """Apply *kwargs* to a transfer and its sides' records; report the settle's answer.

    **The body both public doors share** -- :func:`update_transfer`, which takes
    an arbitrary field bag, and :func:`settle_transfer`, which takes a settle.
    Keeping one body is what stops the named verb from becoming a second
    implementation of the field application, the reconcile tail and the
    telemetry: two doors, one act.

    See :func:`update_transfer` for the accepted kwargs and the failure modes;
    they are that function's contract, stated once there.

    Args:
        transfer_id: The primary key of the transfer to update.
        user_id: The expected owner (defense-in-depth).
        updates: The fields to update, as a dict.
        settle_only: Whether the CALLER asked for a settle and nothing else
            (:func:`settle_transfer`).  It changes what happens when the
            transfer turns out to be settled ALREADY: a settle is idempotent, so
            there is nothing to do and the kwargs must not be applied as an
            ordinary field edit.  Without it the verb DEGRADED -- an adversarial
            review measured it writing ``actual_amount`` verbatim past the echo
            rule and re-dating both legs through the R-ED correction door, while
            returning ``False`` to say it had booked nothing.  A caller that
            genuinely means "edit these fields on a settled transfer" says so by
            calling :func:`update_transfer`.
        press: The save's press, or ``None`` (:func:`update_transfer`).
    """
    rows = TransferRows(_get_transfer_or_raise(transfer_id, user_id))
    # The transfer's SHAPE, read before any write for the reason the
    # transaction PATCH reads ``recurs`` before its field loop: the accessor
    # lazy-loads the definition, and a load after the first ``setattr`` would
    # autoflush a half-applied row (plan step ``balance:X-ci-1``).
    placed = rows.transfer.is_placed

    # The ENDPOINTS this update leaves the transfer with, resolved and refused
    # first because the guard below GRADES against the resulting destination:
    # re-pointing a payment at another loan moves which origination date it is
    # judged by without moving its own installment at all.  Resolving here is
    # also what ownership-checks both accounts before any refusal can name one
    # (plan step R10-b).
    endpoints = _resolve_endpoints(rows, user_id, updates)
    # The days a door STATED, by side, against the endpoints this update leaves
    # (the transfer's own account columns name the OLD accounts until the
    # flush after the move below).  Before any write, like every refusal here.
    stated = stated_by_side(
        updates.get("side_days", ()),
        endpoints.from_account.id, endpoints.to_account.id,
    )

    # The PAYCHECK this update leaves the transfer in, resolved ONCE off the
    # owner's calendar and threaded to every reader below (ruling **R-BAL96**,
    # plan step ``balance:X-ci-1``): the installment guard grades against its
    # start, a placed transfer's move re-places its date on it, and the
    # resolution is itself the submitted ``pay_period_id``'s ownership check
    # -- an id another owner holds is absent from the calendar, one answer for
    # "no such period" and "not yours".  Resolved exactly when one of the
    # three installment-relevant fields is named (which includes every move),
    # the guard's own trigger; ``None`` otherwise, and read by nothing.  Until
    # this leaf the guard and :func:`_reject_unowned_references` each derived
    # the calendar for it -- the second walk C13-b measured at two statements
    # and left; a third reader is what made one producer worth the change.
    period_after = (
        _get_owned_period(
            updates.get("pay_period_id", rows.transfer.pay_period_id), user_id,
        )
        if _POSTING_RELEVANT_INSTALLMENT_FIELDS & updates.keys()
        else None
    )

    # A PLACED transfer's move RE-PLACES its date (ruling **R-BAL93**) and the
    # day it is left with may not be one a sibling of its definition already
    # answers -- both before any write and before the installment guard, so a
    # one-time payment into a loan is graded on the installment the move
    # leaves it with.  The answer, whether the date MOVES, is the one
    # predicate the due-date arm below keys the occurrence on.  See
    # :mod:`._placed`.
    date_moves = re_place_and_grade_the_day(
        rows.transfer, updates, period_after, placed=placed,
    )

    # R-C: refuse an edit that would move a loan payment before its loan, before
    # any field is applied.  See :func:`_reject_installment_move_before_loan`.
    _reject_installment_move_before_loan(
        rows.transfer, updates, endpoints.to_account, period_after,
    )

    # The FIGURE's own gate, in the same place and for the same reason: a
    # refused request must leave the transfer untouched, and the first field
    # write is two statements below.  It also DROPS an echoed box here, so the
    # settle dispatch and the field arms below both see the payload the user
    # actually meant.
    _grade_submitted_figure(rows, updates)

    # The category's OWNERSHIP refusal, hoisted here for the same rule.  It ran
    # at the arm that assigns it, which is after the settle has already written
    # the transfer and its sides' records.  (The period's is the resolution
    # above.)
    _reject_unowned_references(user_id, updates)

    # A day beside a status that settles nothing states money that has not
    # moved: refused before the first write (it ran last, at its own door).
    reject_stated_days_without_settle(
        updates.get("status_id", rows.transfer.status_id), stated,
    )

    # A PLACED transfer's typed figure RESTATES its definition (ruling
    # **R-BAL92**; **R-BAL96** as amended): the first write of this update,
    # placed after every refusal and BEFORE the ownership arm and the settle
    # dispatch, so a figure typed beside a settling status is the price the
    # legs book.  It writes the ownership and the flag into ``updates`` for
    # the arms below to apply, and does nothing when no figure was typed.
    restate_definition_price(rows.transfer, updates, placed=placed)

    # The AMOUNT's own refusal, ahead of the first write
    # (:func:`~._amount.grade_amount_ownership`).
    grade_amount_ownership(updates)

    # ── is_override ────────────────────────────────────────────────
    # Applied FIRST, and the position is load-bearing rather than tidy: the
    # flag says WHO OWNS THIS TRANSFER'S AMOUNT, and the settle dispatch below
    # derives a figure only when the answer is "not the operator".  The
    # transfer edit route auto-sets it whenever a template-linked transfer's
    # amount moves, so a combined "retype the amount and mark it Paid" save
    # arrives carrying it -- and reading the PRE-edit flag there would freeze a
    # derived figure straight over the number the user had just typed.  Every
    # branch between here and the dispatch is a caller-stated FACT; the
    # derivation comes after all of them.
    if "is_override" in updates:
        rows.transfer.is_override = bool(updates["is_override"])

    # ── from_account_id / to_account_id ────────────────────────────
    # A caller-stated fact like the flag above, and applied with it for the
    # same reason: the settle dispatch below reads which account the transfer
    # is left pointing AT.  See :func:`_apply_endpoint_move`.
    #
    # A loan payment moved OFF a loan owes that loan a re-sync AFTER the move
    # (:func:`_reconcile_postings_after_update`) and nothing before it: its
    # split correction links no row and is keyed on the loan's own chart rows
    # (ruling **R-BAL102**), so the vacated loan's walk finds the departed
    # payment's key with no target and reverses it.  Through plan step
    # ``balance:X-bi-6-3`` the split was reversed HERE, first, because it was
    # keyed by the income shadow the move was about to hide from the loan's
    # reconcile (the R10-b `-$4.17` that measured it).
    _apply_endpoint_move(rows, endpoints, press)

    # ── WHO OWNS the transfer's figure ─────────────────────────────
    # Asked exactly when the caller STATED an ownership.  It used to fire on
    # ``"amount" in updates or "is_override" in updates`` -- two keys, because
    # clearing the flag was the conflict resolver's hand-back and neither key
    # alone covered both acts.  One key covers both since ruling **R-BAL11**,
    # and PRESENCE is what distinguishes *stated nothing* from *stated the
    # definition's* now that the second carries no figure to test for.
    if "amount_ownership" in updates:
        apply_amount_ownership(rows, updates["amount_ownership"])

    # ── the SETTLE ─────────────────────────────────────────────────
    # When this update moves the transfer into the settled band, ONE act writes
    # the transfer's status and settled figure and each side's record on its
    # own day (a ``$0.00`` close keeps none); the three kwargs it consumes are
    # then dropped so the field application below cannot write any of them a
    # second time.
    settled = _dispatch_settle(rows, updates, stated, press)
    if settled is not None:
        remaining = _fields_the_settle_left(updates)
        stated = NO_DAYS
    elif settle_only:
        # Already in the settled band, so there is no settle to run -- but the
        # STATUS still goes through, and dropping it too was a defect this
        # branch shipped for one test run: the transition is VERIFIED, so
        # marking a ``Cancelled`` transfer Done stays the designed 400 it has
        # always been.  What is dropped is what would DEGRADE: a figure that
        # would be written verbatim past the echo rule, and every stated day,
        # which would re-date money already recorded -- a settle is
        # idempotent over money already recorded.  The drifted-side repair
        # that honoured a day for a side OUT of the band (ledger row
        # **BAL-578**) went at plan step ``balance:X-bi-6-4d-2``: a side's
        # day is its record's, and a settled transfer holding an un-dated
        # record is unstorable there.
        remaining = {"status_id": updates["status_id"]}
        stated = NO_DAYS
    else:
        remaining = updates

    _apply_remaining_fields(
        rows, remaining, stated=stated, date_moves=date_moves, press=press,
    )

    # The aggregate's lock -- the transfer's counter moving when a side's
    # record or day moved and the transfer did not -- is the status seam's
    # Transfer arm's (``status_seam._side``) since plan step
    # ``balance:X-bi-6-4d-2``: it compared the twin rows' counters here
    # until then.
    db.session.flush()

    # The ORIGINAL updates, not *remaining*: what the caller ASKED to change is
    # what decides whether the ledger needs re-deriving, and a settle's
    # ``status_id`` is precisely the field that says it does.
    _reconcile_postings_after_update(
        rows.transfer, updates, endpoints.vacated,
        endpoints.vacated_destination_id,
    )

    log_event(
        logger, logging.INFO, EVT_TRANSFER_UPDATED, BUSINESS,
        "Transfer updated",
        user_id=user_id,
        transfer_id=transfer_id,
        # Sorting the field list keeps the structured log deterministic
        # so dashboards can group by ``fields_changed`` without spurious
        # cardinality from kwarg ordering.
        fields_changed=sorted(updates.keys()),
    )
    return rows.transfer, bool(settled)


def settle_transfer(
    transfer_id,
    user_id,
    *,
    submitted: StatedFigure | None = None,
    side_days: "tuple[SideDay, ...]" = (),
    press: Press | None = None,
) -> bool:
    """Settle a transfer: both legs and the parent, on the day the money moved.

    **The named verb, and the twin of ``transaction_service.settle_transaction``**
    (ruling **R-FA**).  A door that means "this transfer reached the bank" says
    so by calling this, rather than by assembling the status, the day and the
    amount into an :func:`update_transfer` bag and trusting three kwargs to add
    up to one act.  What settling MEANS is
    :func:`app.services.transfer_service._settle.settle`; this is the door onto
    it, and it exists because the act had no name: its rules were spread over
    four modules and its settle day was written twice per tick, the second
    time through the door ruling **R-ED** built for a user CORRECTING a day.

    **The transfer and both its sides move in this one call**: the transfer's
    status, and each side's payment record on its own day (a ``$0.00`` close
    writes none), by the ONE writer
    (``_status.apply_status_to_all_three``).  That is CLAUDE.md's rule
    that every write of a transfer goes through this package, kept here so no
    caller has to remember it.  **The
    status is DONE, never RECEIVED, though one side is an income**: the
    Paid/Received split is a display convention for ordinary rows and
    meaningless for a transfer, whose whole point is that one side is each.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        transfer_id: The transfer to settle.
        user_id: The expected owner (defense-in-depth).
        submitted: The figure a door stated and who wrote it
            (:class:`~app.services.stated_figure.StatedFigure`; the reconcile
            panel's amount box, ``typed``).
            ``None`` means nobody stated one, and the settle then books what
            the row is worth.
        side_days: The day the money moved on an ACCOUNT and how it is known
            (:class:`~app.services.transfer_service._side_days.SideDay`), for
            each side the caller knows -- the reconcile tick's statement day
            on ``asserted``, the matcher's bank day on ``observed``, for the
            leg on the statement's account; the other side borrows it.  Empty
            on a Paid press: both sides borrow the owner's today.
        press: The save's press, or ``None`` when its door named nothing: a
            ``$0.00`` settle takes each leg's kept payment off
            (:func:`update_transfer`).

    Returns:
        Whether the settle booked *submitted* as a human's CORRECTION --
        False when nobody typed one, and False when the figure was an echo of
        what the row would book anyway.  A transfer ALREADY in the settled band
        is an idempotent no-op that writes nothing and returns False: a settle
        records that money moved, and it has already been recorded.  (The
        repair that dated a twin which had DRIFTED out of the band, ledger
        row BAL-578, went at plan step ``balance:X-bi-6-4d-2``: a transfer
        has one status.)

    Raises:
        NotFoundError: If the transfer does not exist, does not belong to
            *user_id*, or is soft-deleted.
        ValidationError: On an illegal transition, an unresolvable
            derivation, or the seam's settle-day refusals.
        PostingError: From the ledger reconcile, on a broken invariant.
    """
    updates = {"status_id": ref_cache.status_id(StatusEnum.DONE)}
    if submitted is not None:
        updates["figure"] = submitted
    if side_days:
        updates["side_days"] = side_days
    _, corrected = _apply_transfer_updates(
        transfer_id, user_id, updates, settle_only=True, press=press,
    )
    return corrected


def update_transfer(transfer_id, user_id, *, press=None, **kwargs):
    """Update a transfer and its sides' records.

    A side's record is written by the one writer
    (``_status.apply_status_to_all_three``) on a status move, and carried by
    an endpoint move (:func:`_apply_endpoint_move`).  Each side's leg reads the
    transfer's period, category and due date (``transfer_legs``), so no copy
    of them is kept to hold equal: the twin rows that carried one went at
    plan step ``balance:X-bi-6-4d-2``, and their mirrors at its leaf 3b.  A
    planned leg reads its PRICE off the transfer too, and a settled
    transfer's figure is the TRANSFER's own since plan step
    ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
    **R-BAL366**: ``0.00`` for a close of nothing), which the one writer
    states; a side's record holds no figure
    (``ck_transaction_entries_side_record_states_no_figure``).

    **A change that moves the transfer INTO the settled band is a SETTLE**, and
    this hands that act whole to
    :func:`app.services.transfer_service._settle.settle` -- so a door may state
    a settling ``status_id`` among its other fields and still get the freeze,
    the pair's day and the correction rule.  A door that means ONLY that should
    call :func:`settle_transfer`, which says so.

    **A change that moves the transfer between ACCOUNTS moves both legs**, and
    that arm is plan step R10-b's: a transfer's endpoints are two of the six
    columns a recurring definition states, and until that step this door could
    write only four of them -- so a definition's account change reached its
    generated rows by DESTROYING and rebuilding every one of them, and a
    NON-repeating transfer refused the same edit outright because nothing could
    carry it.  Both are gone.  See :func:`_resolve_endpoints` for what a move
    is refused for and :func:`_apply_endpoint_move` for what it writes.

    Accepted kwargs (``amount`` is NOT one since plan step X-au-f: the plan's
    figure travels as ``amount_ownership`` below, and a bare ``amount`` is
    silently ignored like any other unlisted key):
        status_id      -- New status for the transfer; each side's record
                          follows it (dated, un-dated or taken off).
        pay_period_id  -- New period for the transfer.  On a
                          PLACED transfer (a rule-less definition's one-time
                          transfer) the move RE-PLACES its due date as well
                          (ruling **R-BAL93**, plan step X-ci-1): the target
                          paycheck's start unless the date was not the source
                          paycheck's start -- an owner-stated day, read by
                          position -- or a ``due_date`` arrives beside the
                          move, which is the owner's.  See :mod:`._placed`.
        from_account_id -- New SOURCE account for the transfer: its expense
                          side's account, and half of both legs' labels.
                          May not be an amortizing loan (a disbursement is not
                          modelled) and may not equal the destination.
        to_account_id  -- New DESTINATION for the transfer: its income side's
                          account, likewise.  Re-grades the payment
                          against the destination loan's origination (ruling
                          R-C) and re-reconciles the loan it left.
        category_id    -- New category for the transfer.
        name           -- New display name for the transfer.
        notes          -- New notes for the transfer.
        figure         -- What MOVED and WHO WROTE the figure, as one
                          :class:`~app.services.stated_figure.StatedFigure`
                          (``typed`` from the transfer popover's Actual box,
                          plan step X-bi-3e-1), stored once as the
                          transfer's settled figure: a side's payment record
                          holds none.
                          A figure arriving WITH a
                          settling ``status_id`` is the settle's own, subject to
                          its echo rule (:data:`_SETTLE_OWNED_FIELDS`).  One
                          arriving on a pair that is ALREADY settled is a
                          CORRECTION to what it recorded -- the Actual box --
                          and is applied in the same seam pass as the status,
                          because what the bank took is an observed fact and an
                          observation gets corrected when the statement
                          disagrees (developer ruling, 2026-08-17).  One
                          arriving on any other status is REFUSED: an amount
                          states what MOVED, and this pair's money has not.
        due_date       -- Due date for the transfer
                          (Date or None).  On a PLACED transfer the parent's
                          ``occurs_on`` follows it (ruling **R-BAL94**), and a
                          day another transfer of the same definition already
                          answers is refused (the occurrence index's rule,
                          as a designed ``ValidationError``).
        side_days      -- The day the money moved on an ACCOUNT, per side (a
                          tuple of :class:`~._side_days.SideDay`, at most one
                          per endpoint; ruling **R-BAL142**).  On a settled
                          pair it CORRECTS that side's day and a borrowing side
                          follows; beside a status that settles nothing it is
                          refused.  It replaced the pair's ``settle_day`` (plan
                          step ``balance:X-bi-6-4c-3``), now ignored.
        is_override    -- Override flag on the transfer.  It no
                          longer says anything about who owns the amount; since
                          plan step X-au-h it means exactly *this row is the
                          OWNER's, not the rule's*.
        definition_price -- The figure a HUMAN typed on a PLACED transfer's
                          popover (a rule-less definition's one-time transfer;
                          plan step X-ci-1, rulings **R-BAL92** and **R-BAL96**
                          as amended): the definition's price is RESTATED in
                          place at the transfer's final due date, the
                          transfer is declared priced by the definition, and the
                          flag comes off -- before the settle dispatch, so a
                          settling ``status_id`` in the same call books it.
                          Refused on any other transfer, and beside an
                          ``amount_ownership``.  A definition owning no price
                          series (derive-mode loan) is not this: its typed
                          figure is the pair's OWN, sent as ``amount_ownership``.
        amount_ownership -- WHAT PRICES this pair, as ONE value (ruling
                          **R-BAL11**, plan step X-au-f): an
                          :class:`~app.models.amount_ownership.AmountOwnership`
                          that either OWNS a figure a human authored or
                          DECLARES
                          :attr:`~app.enums.AmountSourceEnum.TEMPLATE`, its
                          definition's price; both legs read it off the
                          transfer.  OMITTING the key means this save says
                          nothing about the amount and leaves the transfer as
                          it stands; that is not the same as passing an empty
                          ownership, and the difference is the one that cost
                          ``$174.10`` twice.  It REPLACES ``amount`` and
                          ``amount_authored``, which were two spellings of the
                          same question, and the conflict resolver's hand-back,
                          which was a third.

    Args:
        transfer_id: The primary key of the transfer to update.
        user_id:     The expected owner (defense-in-depth).
        press:       The save's press over the bank lines the door's page
                     named, or ``None`` when it named nothing
                     (``match_press.Press``, R-CC127, R-CC135).
        **kwargs:    The fields to update; see "Accepted kwargs" above.
                     Any key not listed there is silently ignored.

    Returns:
        The updated Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If validation fails (non-positive amount,
            wrong period owner, data integrity issues).
    """
    xfer, _ = _apply_transfer_updates(transfer_id, user_id, kwargs, press=press)
    return xfer
