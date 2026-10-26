"""
Shekel Budget App -- Pay Period Gates

**The predicates that decide whether a destructive schedule change MAY
proceed**, split out of :mod:`app.services.pay_period_admin` at plan step
``pay_calendar:C14-f`` (developer ruling 2026-09-07, closing the pressure
ledger row **PC-498** recorded).

The split is the module it left already argued for, one level down.  Plan step
C3-a moved the read-only lock classifier into
:mod:`app.services.pay_period_locks` "because a read-predicate and four
destructive writers are two concerns"; the same sentence separates *deciding*
that a schedule may change from *orchestrating* the change.  What stays in
``pay_period_admin`` is the doors -- extend, add-earlier, remove-earlier,
truncate, regenerate, reset; what lives here is every gate they consult before
touching a row.  Remove-earlier's (:func:`gate_removable_head`, plan step
``pay_calendar:C21``) is the one that asks what a paycheck HOLDS rather than
how it is locked.

**Why it happened when it did, stated rather than left to git blame.**
``pay_period_admin`` stood at 996 of pylint's 1,000-line ceiling, and
``C14-f``'s gap confirmation needed eleven lines of it -- a parameter, its
threading and the ``Args`` entry ``W9015`` requires.  **PC-498 named that
remedy a SPLIT and not a trim**, because answering a ceiling by deleting
argument is how a module loses the reasoning that keeps it correct, and it
named the PLACEMENT the developer's rather than the next session's to assume
(the precedent is **R-PC60**).  He placed it here.  *That confirmation --
``reject_unconfirmed_gap``, ``Confirmations.gap``, ``PayPeriodGapRequired``
and the banner -- was DELETED whole at plan step ``pay_calendar:C17-c-2a``
(ruling **R-PC67**): a hole in the schedule is refused by
``pay_period_batch.reject_skipped_paycheck``, beside the floor, rather than
confirmed.  The one overridable gate left is the discard one, and the doors
take it as ``confirm_discard``.*

**These names are PUBLIC and were private before the move.**  A function
another module calls is part of this module's surface; keeping the underscore
would have made every call site read as a privacy breach and taught the next
reader that the boundary does not mean anything.  The behaviour is unchanged
-- this is a move and a rename, and nothing here computes differently than it
did inside ``pay_period_admin``.

**The dependency runs ONE WAY**: this module imports nothing from
``pay_period_admin``, and that is a property rather than an accident -- a gate
that called a door would be the cycle the C3-a split exists to prevent.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodRemovalRefused,
)
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.pay_stub import PayStub
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import (
    pay_period_locks,
    pay_period_service,
    status_seam,
    transfer_legs,
)
from app.services._recurrence_common import log_resource_access_denied
from app.services.loan_loaders import load_standing_loan_assertions
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from app.services.pay_period_locks import PeriodLockReason
from app.utils.balance_predicates import is_projected
from app.utils.log_events import ACCESS, EVT_RESOURCE_NOT_FOUND, log_event

logger = logging.getLogger(__name__)

#: A ledger total a key does not reach: no posting, no money.
_NO_MONEY = Decimal("0")


def log_unresolved_period(user_id: int, period_id: int) -> None:
    """Emit the F-144 access-denied trail for an id that resolved to nothing.

    **A new ownership-failure branch owes an ACCESS event**, which is the
    invariant ``utils.auth_helpers`` states for its own doors ("every
    ownership-failure branch in this module emits a structured ``log_event`` so
    probing patterns surface in dashboards and SOC alerting") and which plan
    step C3-a's first cut left silent: an authenticated owner sweeping ids
    through ``POST /pay-periods/truncate`` was correctly refused every time and
    recorded nowhere.

    The two branches ``get_or_404`` distinguishes are distinguished HERE too,
    and for its reasons: a missing pk is common in normal use -- the
    discard-confirm panel re-posts an id a concurrent truncate has since
    deleted -- so it is INFO, while a pk owned by somebody else is the IDOR
    signal and is WARNING.  **Splitting the LOG is not an oracle**: the caller
    raises one message for both, and an event goes to the log rather than to
    the response.

    ``get_or_404`` itself cannot be reused: it reads ``current_user`` and
    ``request.path``, and this module may not import Flask.  The cross-user
    half therefore goes through ``_recurrence_common.log_resource_access_denied``,
    the Flask-free helper built for exactly this shape, rather than through a
    second copy of its fixed keyword set.

    Args:
        user_id: The requesting owner -- never the row's owner.
        period_id: The submitted ``budget.pay_periods.id`` that resolved to
            none of *user_id*'s periods.
    """
    owner_id = (
        db.session.query(PayPeriod.user_id)
        .filter(PayPeriod.id == period_id)
        .scalar()
    )
    if owner_id is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "A pay-period form named a non-existent primary key",
            user_id=user_id, model="PayPeriod", pk=period_id,
        )
        return
    log_resource_access_denied(
        logger, user_id=user_id, model="PayPeriod", pk=period_id,
        owner_id=owner_id,
    )



def gate_deletable_tail(
    periods: PeriodWindow,
    kept: "DerivedPeriod | None",
    confirm_discard: bool,
    locks: "dict[int, PeriodLockReason | None]",
) -> "list[DerivedPeriod]":
    """Return the periods after *kept*, having refused if any may not go.

    The shared gate of truncate: :func:`truncate_pay_periods` reaches it with
    a period resolved from a submitted id, and :func:`regenerate_pay_periods`
    with one it computed itself.  Both refusals live here so the two callers
    cannot drift on which rows a truncate protects -- the split is plan step
    C3-a's, made because only ONE of them takes user input and so only one has
    an id to refuse.

    **It DECIDES; it does not delete** (plan step C3-b).  The removal is
    ``pay_period_write``'s, which carries it out beside whatever the same
    operation records.  That split is what lets regenerate be ONE write: the
    gate runs here, and the writer sees the operation's final payday set rather
    than the half-applied one an adversarial review caught it refusing against.

    *The composition had a SECOND reason until plan step ``pay_calendar:C4-c``,
    and it is gone rather than merely unmentioned.*  While the two derived
    columns were stored, a tail delete left the new last period holding its old
    successor's end -- paydays ``[Jan 2, Jan 16, Feb 11]`` truncated through
    Jan 16 kept a stored end of Feb 10 where the derivation says Jan 29 -- so
    the writer re-materialised what survived, and getting that for free was
    half the argument for one call.  C4-c dropped the columns: a delete removes
    rows and moves no value anywhere (``retire_paydays``' own docstring states
    it), so what is left is the refusals, and they are enough.

    **The tail is defined by PAYDAY, not by ordinal**, and that is the same
    normalization the arc is about: ``start_date`` is the only fact in the row,
    ``period_index`` is derived from it, and plan step C4-c dropped the ordinal
    altogether.  Since plan step C3-b the two select the same rows by
    construction rather than by data: ``pay_period_write`` is the only writer
    of this table and reads the ordinal off the derivation, where it IS the
    position in payday order (0 disagreements on 61 production rows,
    2026-08-10).

    **But this function does not RELY on that, and an adversarial review of
    C3-a is why.**  Its first cut took the boundary from
    :func:`regenerate_keep_through_period`, which picked by LIST POSITION over
    a list ``get_all_periods`` ordered by ``period_index`` -- so the boundary
    was chosen in ordinal space and applied in payday space, and the two
    agreeing was an unfenced assumption about data rather than a property of
    the code.  That helper sorted by payday to close it, and plan step C2-f3b
    deleted even the sort: a :class:`~app.services.pay_calendar.PeriodWindow`
    is ordered at construction, so neither function can be handed an order to
    get wrong.  This one is a filter and reads no order at all.

    **The LOCKS arrive as an argument** (plan step C2-f3b), and that is what
    lets a door read them once.  This function classified its own tail while
    ``regenerate_pay_periods`` had already classified the whole schedule to
    find where that tail opens -- two classifies and two independent
    ``date.today()`` reads for one decision.  Taking the map means the caller
    resolves the owner's day once and both consumers read the same answer.

    Args:
        periods: The owner's saved periods as one window, read under the
            owner's write lock the signed-in request's command transaction
            holds (plan step ``balance:X-bn``).
        kept: The last period to KEEP, or ``None`` to delete every period in
            *periods*.  ``None`` is reachable only from regenerate, whose
            rebuildable tail can start at the very first period; it then
            generates a fresh schedule inside the same transaction, so no
            committed state is ever period-less.
        confirm_discard: When True, proceed past the discard gate.
        locks: The caller's lock classification, covering at least *periods*.
            Indexed rather than ``.get``-ed below: a to-delete period missing
            from it is a caller that classified a different set, and treating
            the miss as "unlocked" is the direction that deletes settled money.

    Returns:
        The periods that may be deleted, empty when *kept* is already the last.

    Raises:
        PayPeriodLocked: A to-delete period is hard-locked; one locked for
            holding a payment or purchase is named with what it holds
            (``pay_period_locks.locked_refusal``).
        PayPeriodDiscardRequired: A to-delete period holds unrecoverable
            rows and ``confirm_discard`` is False.
    """
    if kept is None:
        to_delete = list(periods)
    else:
        to_delete = [p for p in periods if p.start_date > kept.start_date]
    if not to_delete:
        return []

    blocking = {
        period.period_id: locks[period.period_id]
        for period in to_delete
        if locks[period.period_id] is not None
    }
    if blocking:
        # Posting-ledger protection, two layers: a period holding settled
        # (posted) transactions classifies SETTLED_TXN, and a period whose
        # journal entries do not net to zero per ledger account (a loan
        # opening / true-up correction, or attribution drift) classifies
        # LEDGER_POSTINGS -- so truncate refuses both.
        # journal_entries.pay_period_id is ON DELETE CASCADE, so deleting a
        # posted period disposes its ledger entries + legs at the DB tier
        # (outside the ORM, where the balanced-journal trigger never fires on
        # DELETE); that is safe only for a period whose postings net to zero
        # per account (a self-cancelling original + reversal pair).  Whoever
        # relaxes these locks MUST first reverse the postings
        # (posting_service.reverse_postings_before_delete / the loan sync).
        # A third layer since plan step credit_card:CC-5-4a-4: a period
        # holding a row or transfer that holds a payment or purchase
        # classifies HOLDS_MOVEMENT, whatever ``_regenerable`` says, and
        # BEFORE the discard count below -- so no confirmation reaches it.
        # Its refusal names every payment and purchase, its amount and its
        # row (plan step pay_calendar:C22, ruling R-PC116).
        raise PayPeriodLocked(
            blocking, pay_period_locks.locked_refusal(to_delete, blocking),
        )

    if not confirm_discard:
        discardable = count_discardable_items(
            [period.period_id for period in to_delete],
        )
        if discardable > 0:
            raise PayPeriodDiscardRequired(discardable)

    return to_delete


def gate_removable_head(
    user_id: int,
    periods: PeriodWindow,
    first_kept: DerivedPeriod,
    as_of: date,
) -> "list[DerivedPeriod]":
    """Return the periods before *first_kept*, having refused if any holds money.

    **The gate of "Remove earlier paychecks"** (plan step ``pay_calendar:C21``,
    ruling **R-PC109**): :func:`gate_deletable_tail`'s mirror at the record's
    other end, and like it a gate that DECIDES and leaves the delete to
    ``pay_period_write``.  It cannot BE that gate, and the lock classifier is
    why: ``pay_period_locks`` calls every period that has ended HISTORICAL
    and ranks that reason first, so every paycheck "Add earlier paychecks"
    records -- all of them past -- reads as locked and the reasons under it
    are masked.  The rule here is the ruling's own: a paycheck may go when
    it holds NO MONEY.

    **What GOES with the paycheck**: an unpaid row its template made that the
    owner never changed (:func:`_regenerable`, the discard gate's own rule,
    and holding no payment or purchase), and a transfer the same way, one row
    since plan step ``balance:X-bi-6-4d-2`` deleted its twins.  They are what
    populating the paycheck wrote when it was added, so removing it takes
    back exactly that.

    **What STOPS it**, each asked of the whole head and named, in this order,
    the first that finds anything raising:

    1. A row the owner typed, changed, paid, or marked Credit or Cancelled;
       any row or transfer the delete would take -- a hidden one included
       -- that holds a payment or purchase
       (:func:`app.services.pay_period_locks.items_holding_a_movement`, the
       one question every pay-period door asks, a transfer through the one
       ``transfer_legs`` clause since ruling **R-BAL157**); a transfer made
       by hand -- "items you entered or changed", the ruling's wording,
       naming each.  **A row paid
       and set back to Projected is refused too** (ruling **R-PC115**): the
       revert keeps its payment, undated, which is history no bulk door
       deletes (**R-CC54**, **R-CC65**), and the database refuses to delete it
       with its row.  A transfer set back this way can be cleared by
       deleting it since plan step ``balance:X-bi-6-4d-2``, whose delete
       takes its sides' payments off on either arm (finding **BAL-532**),
       unless that frees a bank line, which no page captions (ruling
       **R-BAL229**); until then its delete hid the legs and kept their
       payments, a cost the ruling accepted.
    2. A pay stub dated on the paycheck's payday: a stub sits on a paycheck
       the app holds (**R-SAL49**), and this would leave it on none.
    3. Money DATED inside the removed paychecks anywhere in the budget -- a
       settle day on a plain row, on a transfer side's own record (its
       covering movement, ruling **R-BAL142**) or on a purchase, or a
       balance recorded for an account or a loan -- because the removal
       raises the recordable floor (``pay_period_service.recordable_floor``)
       over it.  A settle day under that floor is refused on every save that
       keeps its row paid (``status_seam``), and an assertion under it is the
       back-dated state ``anchor_service.resolve_observation_day`` exists to
       refuse.  Rows filed in the head are refused by (1) first whatever they
       are dated.

    **What the posted ledger booked in the head is NOT asked here** (ruling
    **R-PC114**, amending R-PC109's "a balance entry the app booked").  The
    ledger files every CORRECTION dated before the first paycheck in the
    EARLIEST one (``PayCalendar.filing_period``, **R-PC53**; a cash movement
    is filed in its row's own period), and every re-sync re-files there --
    so after "Add earlier paychecks" a loan's opening dated years back sits
    in an added paycheck, and refusing on it made the
    undo die at the first loan payment (review 1 of C21, measured on the
    developer's data).  The door re-files those entries through Reset's two
    re-syncs instead and asks :func:`reject_moved_ledger` of the result: the
    ledger may lose nothing.  A row the head holds that still holds a payment
    or purchase is refused by (1) whatever its entries net to: a
    paid-then-unpaid pair nets to zero, but its row keeps the payment
    (**R-PC115**).

    **No confirmation step**: the ruling refused one, because a Credit row is
    a real card charge and a confirmation could delete real spending.  And
    no HISTORICAL test: every paycheck this door exists to remove is past.

    Args:
        user_id: The owning user's id -- the dated money of (3) is the
            owner's anywhere, not only in the head.
        periods: The owner's saved periods as one window, read under the
            owner's write lock the signed-in request's command transaction
            holds (plan step ``balance:X-bn``).
        first_kept: The period the owner chose to START FROM (ruling
            **R-PC111**); it and every later period stay.
        as_of: The owner's civil day, resolved once by the caller.

    Returns:
        The periods before *first_kept*, payday ascending; empty when it is
        already the first.

    Raises:
        PayPeriodRemovalRefused: A period in the head holds a row the owner
            made or a pay stub, or money is dated inside the head.  Nothing
            is written.
    """
    head = [period for period in periods if period.start_date < first_kept.start_date]
    if not head:
        return []
    _reject_held_rows(head)
    _reject_stubs(user_id, head)
    _reject_dated_money(user_id, head, first_kept, as_of)
    return head


def _reject_held_rows(head: "list[DerivedPeriod]") -> None:
    """Refuse a head holding a row that is not an untouched template row.

    Refusal 1 of :func:`gate_removable_head`, in the ruling's wording: one
    sentence per paycheck naming its items, then the one remedy.  An item is
    named once however many reasons hold it: a changed row that also holds a
    purchase, or a transfer whose two sides each keep a record of its one
    payment.

    Args:
        head: The periods the removal would take.

    Raises:
        PayPeriodRemovalRefused: A row or transfer in *head* is one the
            owner entered or changed, or a row or transfer there -- a hidden
            one included -- holds a payment or purchase (ruling
            **R-PC115**).
    """
    period_ids = [period.period_id for period in head]
    # Keyed per paycheck by what the owner sees -- a row, or a transfer once
    # for both its legs -- so an item held for two reasons is named once.
    held: "dict[int, dict[tuple[str, int], str]]" = {}
    for model, scope in (
        (Transaction, (Transaction.transfer_id.is_(None),)),
        (Transfer, ()),
    ):
        for row in _live_rows_of(model, model.template, period_ids, *scope):
            if not _regenerable(row):
                item, name = pay_period_locks.item_of(model, row.id, row.name)
                held.setdefault(row.pay_period_id, {})[item] = name
    # Ruling R-PC115's half: every item the delete would take -- hidden ones
    # included -- that holds a payment or purchase, so a paid row set back to
    # Projected (its revert keeps its payment, undated) is one of them.  A
    # transfer is asked for itself (ruling R-BAL157), never through its legs.
    # Read through the pay-period doors' one NAMED reading of what a paycheck
    # holds (plan step C22), which truncate and regenerate name it by too.
    for movement in pay_period_locks.movements_held_in(period_ids):
        held.setdefault(movement.period_id, {})[movement.item] = (
            movement.item_name
        )
    if not held:
        return
    sentences = []
    for period in head:
        names = sorted(held.get(period.period_id, {}).values())
        if names:
            noun = "item" if len(names) == 1 else "items"
            sentences.append(
                f"{pay_period_locks.paycheck_name(period)} holds {len(names)} "
                f"{noun} you entered or changed ({', '.join(names)})."
            )
    one = sum(len(names) for names in held.values()) == 1
    sentences.append(
        "Delete or move it first." if one else "Delete or move them first.",
    )
    raise PayPeriodRemovalRefused(" ".join(sentences))


def reject_moved_ledger(
    before: "dict[tuple[int, int], Decimal]",
    after: "dict[tuple[int, int], Decimal]",
) -> None:
    """Refuse a removal that changed any posted total (ruling **R-PC114**).

    "Remove earlier paychecks"' ledger half, asked AFTER its write:
    ``pay_period_admin.remove_earlier_pay_periods`` reads
    :func:`~app.services.pay_period_locks.posted_totals` as they stand,
    retires the head (whose journal entries the ``CASCADE`` takes), re-syncs so
    every entry rebuilt from a surviving record is re-filed onto the kept
    paychecks, and reads the totals again -- the ruling's words, "refused
    iff a posted total would change", with no re-synced counterfactual in
    front (review 2 of C21 measured that one refusing falsely).  Equal totals mean the removal
    lost nothing booked: whatever the head held was rebuilt onto the kept
    paychecks (an opening, a true-up correction) or netted to zero with it
    (the pair of a row paid, set back and then deleted; a row still holding
    its payment never reaches here, ruling **R-PC115**); a total that moved
    is an entry no re-sync
    rebuilds, and the door refuses.  It is asked after the write because only the
    re-syncs can say what they rebuild -- a list of rebuildable entry kinds
    here would be a second statement of the posting modules' own rules --
    and the refusal leaves nothing behind because the door rolls back the
    savepoint it made the write in.

    Args:
        before: The totals before the removal, as they stood.
        after: The totals after the removal and its re-sync.

    Raises:
        PayPeriodRemovalRefused: A total differs; the message is the ruled
            one, naming the account(s) whose booked balance moved.
    """
    moved = {
        key[1] for key in set(before) | set(after)
        if before.get(key, _NO_MONEY) != after.get(key, _NO_MONEY)
    }
    if not moved:
        return
    raise PayPeriodRemovalRefused(
        f"Removing these paychecks would change the balance the app has "
        f"booked for {', '.join(pay_period_locks.ledger_account_names(moved))}, "
        f"so nothing was removed. Start from an earlier paycheck."
    )


def _reject_stubs(user_id: int, head: "list[DerivedPeriod]") -> None:
    """Refuse a head whose payday carries one of the owner's pay stubs.

    Refusal 2 of :func:`gate_removable_head`.  Every stubbed payday is named;
    the owner can delete a stub, so the remedy offers that first.

    Args:
        user_id: The owning user's id.
        head: The periods the removal would take.

    Raises:
        PayPeriodRemovalRefused: A pay stub is dated on a payday in *head*.
    """
    stubbed = sorted(
        row[0]
        for row in db.session.query(PayStub.payday)
        .join(SalaryProfile, PayStub.salary_profile_id == SalaryProfile.id)
        .filter(
            SalaryProfile.user_id == user_id,
            PayStub.payday.in_([period.start_date for period in head]),
        )
        .distinct()
        .all()
    )
    if not stubbed:
        return
    days = ", ".join(day.isoformat() for day in stubbed)
    saved, pronoun = (
        ("A pay stub is", "it") if len(stubbed) == 1 else ("Pay stubs are", "them")
    )
    raise PayPeriodRemovalRefused(
        f"{saved} saved for {days}. Delete {pronoun} first, or start from "
        f"{stubbed[0].isoformat()} or earlier."
    )


def _reject_dated_money(
    user_id: int,
    head: "list[DerivedPeriod]",
    first_kept: DerivedPeriod,
    as_of: date,
) -> None:
    """Refuse a removal that would leave recorded money below the floor.

    Refusal 3 of :func:`gate_removable_head`.  The span is what the removal
    moves the recordable floor over: from where it stands to where it would
    stand, both read through ``pay_period_service.recordable_floor``.  Money
    already below today's floor is a state the removal did not make, so it
    does not refuse it.  Named on the EARLIEST such day, and the remedy is
    the paycheck holding it.

    Args:
        user_id: The owning user's id.
        head: The periods the removal would take.
        first_kept: The period the removal starts from.
        as_of: The owner's civil day.

    Raises:
        PayPeriodRemovalRefused: A settle day or a recorded balance falls in
            the span.
    """
    low = pay_period_service.recordable_floor(head[0].start_date, as_of)
    high = pay_period_service.recordable_floor(first_kept.start_date, as_of)
    if low >= high:
        return
    dated = [
        found for found in (ask(user_id, low, high) for ask in _DATED_MONEY)
        if found is not None
    ]
    if not dated:
        return
    # The earliest day; on a tie, the first arm asked, so the naming never
    # turns on how two descriptions happen to sort.
    day, what = min(dated, key=lambda found: found[0])
    period = next(p for p in head if p.covers(day))
    raise PayPeriodRemovalRefused(
        f"{what} {day.isoformat()}, inside the paychecks you would remove, and "
        f"money can't be dated before your schedule starts. Start from "
        f"{period.start_date.isoformat()} or earlier."
    )


def _earliest(query, describe) -> "tuple[date, str] | None":
    """Return ``(day, description)`` for the first row of *query*, or ``None``.

    Args:
        query: A query yielding ``(day, name)`` rows, earliest day first.
        describe: How the refusal words a row's name.

    Returns:
        The earliest day and its description, or ``None`` for no row.
    """
    row = query.first()
    return None if row is None else (row[0], describe(row[1]))


def _settled_transaction(user_id, low, high):
    """The earliest settle day in ``[low, high)`` on a live row of the owner's.

    Plain rows only: a transfer's sides are :func:`_settled_transfer_side`'s.
    """
    return _earliest(
        db.session.query(Transaction.settled_on, Transaction.name)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.settled_on >= low,
            Transaction.settled_on < high,
        )
        .order_by(Transaction.settled_on),
        lambda name: f"{name} is marked paid on",
    )


def _settled_transfer_side(user_id, low, high):
    """The earliest day in ``[low, high)`` a live transfer's side RECORDS.

    Read off each side's own record -- its covering movement, through
    ``transfer_legs``' one loader -- since plan step ``balance:X-bi-6-4c-3``
    (ledger row **BAL-568**), where the two sides' days part (ruling
    **R-BAL142**).  It read the shadow rows until then.  The one difference
    on door-written data is a transfer closed at ``$0.00``: it records no
    movement and so no day (ruling **R-BAL90**), and it no longer holds a
    removal back (R-PC109 names money dated inside the span).  The loader's
    own ``ORDER BY`` (the movement's id) is cleared first, since SQLAlchemy
    APPENDS a second, and restated as the tiebreak: a transfer's two sides
    usually share their day, and the refusal must name the same side every
    time -- the gate's own rule for a tie between its arms.
    """
    row = (
        transfer_legs.transfer_movement_rows(
            Transfer.user_id == user_id,
            Transfer.is_deleted.is_(False),
            TransactionEntry.settled_on >= low,
            TransactionEntry.settled_on < high,
        )
        .order_by(None)
        .order_by(TransactionEntry.settled_on, TransactionEntry.id)
        .first()
    )
    if row is None:
        return None
    movement, transfer, is_income = row
    name = transfer_legs.leg_label(transfer.from_account, transfer.to_account)[
        1 if is_income else 0
    ]
    return movement.settled_on, f"{name} is marked paid on"


def _settled_purchase(user_id, low, high):
    """The earliest settle day in ``[low, high)`` on a purchase under a live row.

    Purchases only (``status_seam.covering_clause``, negated): a settled
    plain row's covering movement carries its row's settle day, which
    :func:`_settled_transaction` already asks, and a transfer side's is
    :func:`_settled_transfer_side`'s own read.  Scoped by the purchase's
    OWNER, never ``user_id`` -- that is its AUTHOR, a companion's id when a
    companion recorded it (review 1 of C21 measured one slipping through).
    """
    return _earliest(
        db.session.query(TransactionEntry.settled_on, TransactionEntry.description)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            TransactionEntry.owner_id == user_id,
            ~status_seam.covering_clause(),
            Transaction.is_deleted.is_(False),
            TransactionEntry.settled_on >= low,
            TransactionEntry.settled_on < high,
        )
        .order_by(TransactionEntry.settled_on),
        lambda description: f"The purchase {description} is marked paid on",
    )


def _recorded_balance(user_id, low, high):
    """The earliest balance recorded for one of the owner's accounts in ``[low, high)``."""
    return _earliest(
        db.session.query(AccountAnchorHistory.observed_on, Account.name)
        .join(Account, AccountAnchorHistory.account_id == Account.id)
        .filter(
            Account.user_id == user_id,
            AccountAnchorHistory.observed_on >= low,
            AccountAnchorHistory.observed_on < high,
        )
        .order_by(AccountAnchorHistory.observed_on),
        lambda name: f"{name}'s balance is recorded for",
    )


def _recorded_loan_balance(user_id, low, high):
    """The earliest STANDING loan statement in ``[low, high)``: a recorded loan balance.

    Read through ``loan_loaders.load_standing_loan_assertions``, the loans'
    one reader of their statements (ruling ``recurrence:R-R98``): the
    ``tracking_start`` balance stated at setup and every ``user_trueup``,
    less any the owner withdrew -- a withdrawn statement describes nothing.
    The origination is not among them: it is the loan's opening, legal
    before any schedule.  Per configured loan, since that reader is.
    """
    found = []
    for account_id, name in (
        db.session.query(Account.id, Account.name)
        .join(LoanParams, LoanParams.account_id == Account.id)
        .filter(Account.user_id == user_id)
    ):
        found.extend(
            (fact.anchor_date, f"{name}'s balance is recorded for")
            for fact in load_standing_loan_assertions(account_id)
            if low <= fact.anchor_date < high
        )
    return min(found, key=lambda each: each[0]) if found else None


#: The kinds of money a day is recorded on that the recordable floor bounds
#: or the ruling names (R-PC109: "a settle day on any row ... or a balance
#: recorded for a day inside them"), each asked for its earliest day in the
#: span.  An account's recorded balances are every row of
#: ``account_anchor_history`` -- a bank level a later import RELEASED
#: included, the conservative side: whether a withdrawn level may sit below
#: the floor is the bank-import arc's question, and its one narrowing
#: (``statement_import._balance.standing_bank_levels``) is not exported.  A
#: transfer is here through each side's own record, which carries that side's
#: day (plan step ``balance:X-bi-6-4c-3``; it was the shadow rows, ledger row
#: BAL-568).  A loan's ORIGINATION is not here: it is the loan's opening,
#: legal before any schedule, as an account's books opening is.
_DATED_MONEY = (
    _settled_transaction,
    _settled_transfer_side,
    _settled_purchase,
    _recorded_balance,
    _recorded_loan_balance,
)



def can_reset_pay_periods(user_id: int) -> bool:
    """Return whether a full reset is currently offered to the user.

    The read-only UI predicate: reset is offered only when the user has no
    settled, non-deleted row or transfer and none holding a payment or
    purchase, the same bounds :func:`reset_pay_periods` enforces.  The
    settings page calls this to show or hide the reset control; the
    service's own gate (which raises
    :class:`~app.exceptions.PayPeriodResetBlocked`) remains the
    authoritative defense, so a stale page that posts anyway is still
    refused.

    Args:
        user_id: The owning user's id.

    Returns:
        ``True`` when the user has zero settled (non-deleted) rows and
        transfers and zero holding a movement, else ``False``.
    """
    return (
        settled_transaction_count(user_id) == 0
        and movement_holding_row_count(user_id) == 0
    )



def regenerate_keep_through_period(
    periods: PeriodWindow,
    locks: "dict[int, PeriodLockReason | None]",
    as_of: date,
) -> "DerivedPeriod | None":
    """Return the last period regenerate KEEPS, or ``None`` to keep none.

    Everything up to and including the last period that has already started or
    is locked is kept; the first NOT-YET-STARTED AND unlocked period is where
    the rebuildable tail begins, so this returns the period BEFORE it.  "Not
    yet started" is ``start_date > as_of`` STRICTLY: a period whose
    ``start_date == as_of`` is the current in-progress period -- the same
    inclusive bound :meth:`~app.services.pay_calendar.PayCalendar
    .period_containing` applies, and which ``get_current_period`` applied in
    SQL until plan step C2-f3a deleted it -- so on a payday it is kept, not
    rebuilt.  When there is no rebuildable future tail
    (every period has started or is locked), it returns the LAST period -- the
    truncate is then a no-op and regenerate degrades to an append from
    ``new_start_date``.

    **It returns a PERIOD rather than an ordinal, and ``None`` rather than
    ``-1``** (plan step C3-a).  The sentinel had to be a number below every
    real ``period_index`` because the truncate it fed compared ordinals; with
    :func:`gate_deletable_tail` comparing PAYDAYS there is no "one before the
    first payday" to name, and inventing one would be an ordinal surviving in
    a function whose whole point is that ordinals do not.

    **It walked in PAYDAY order because an adversarial review of C3-a found it
    walking in ORDINAL order; plan step C2-f3b deleted the sort that fixed
    that.**  The first cut walked ``periods`` as handed over -- which
    ``pay_period_service.get_all_periods`` ordered by ``period_index`` -- and
    returned ``periods[position - 1]``, so the boundary was chosen in ordinal
    space while the delete that consumes it selects in payday space.  C3-a
    sorted here, which removed the assumption by asserting the order at one
    site; a :class:`~app.services.pay_calendar.PeriodWindow` is sorted at
    construction, so there is no order left for a caller to state and the sort
    had nothing to correct.  A fence deleted by making its subject
    unconstructible, which is what this arc is for.

    Args:
        periods: The owner's saved periods as one window, read under the
            owner's write lock the signed-in request's command transaction
            holds (plan step ``balance:X-bn``).  Taken as an argument rather
            than re-queried so the boundary and the delete that consumes it
            see one snapshot.
        locks: The caller's lock classification, covering *periods*.  Taken for
            the reason :func:`gate_deletable_tail` takes it: this function and
            that one used to classify separately, against two independently
            read clocks.  **Only two of its three reasons are reachable here**,
            and the third is unreachable by proof rather than by data: a
            candidate satisfies ``start_date > as_of``, a derived period always
            has ``end_date >= start_date``, so no candidate can satisfy
            ``end_date < as_of``.  What can keep a not-yet-started period out of
            the rebuildable tail is settled money or posted ledger entries,
            never HISTORICAL.
        as_of: The owner's civil day, resolved once by the caller.

    Returns:
        The last :class:`~app.services.pay_calendar.DerivedPeriod` to keep, or
        ``None`` when the rebuildable tail starts at the very first period (or
        the owner has no periods at all) -- both meaning "keep none of them".
    """
    if not periods:
        return None
    for position, period in enumerate(periods):
        if period.start_date > as_of and locks[period.period_id] is None:
            return periods[position - 1] if position > 0 else None
    return periods[-1]



def _live_rows_of(model, definition, period_ids, *scope):
    """Load *model*'s live rows in *period_ids*, each with its definition.

    The ONE query shape both arms of :func:`count_discardable_items` load
    through, so the two cannot come to scope a period differently: the
    period set, the live filter and the eager load of the definition are
    stated here, and each arm adds only what is its own (the transaction
    arm's ``transfer_id IS NULL``, which excludes nothing since plan step
    ``balance:X-bi-6-4d-2`` deleted the transfer twins and goes with the
    column at ``X-bi-6-5``).  ``selectinload`` fetches every row's
    definition in one query rather than one per row, since this runs over
    every period a truncate would drop.

    Args:
        model: ``Transaction`` or ``Transfer``.
        definition: The relationship to the row's definition
            (``Transaction.template`` / ``Transfer.template``).
        period_ids: The pay-period ids being deleted.
        *scope: Any further filter clauses the arm needs.

    Returns:
        The rows, definitions loaded.
    """
    return (
        db.session.query(model)
        .options(selectinload(definition))
        .filter(
            model.pay_period_id.in_(period_ids),
            model.is_deleted.is_(False),
            *scope,
        )
        .all()
    )


def count_discardable_items(period_ids):
    """Count rows in the periods that regeneration cannot reproduce.

    A row needs the user's confirmation before truncate / regenerate
    wipes it when no rule would write it back (``recurs`` is ``False``: a
    hand-entered row, or a row of a definition with no cadence), when it is
    a manual override, or when it carries a deliberate non-Projected status
    (Credit / Cancelled -- settled rows, and any row or transfer holding a
    payment or purchase, are already hard-locked upstream, so they never
    reach here).  Transfers are counted once on their own table by the same
    three questions (the transaction scan's ``transfer_id IS NULL`` kept
    their twin rows out until plan step ``balance:X-bi-6-4d-2`` deleted
    them).  That way a recurring transfer (regenerable) does not falsely
    trip the gate while a one-time or ad-hoc transfer does.  The
    not-Projected test routes through ``balance_predicates.is_projected``
    (negated) so no inline status-id comparison lives here (D6-09).

    **Both arms LOAD the rows and ask each one** -- the transaction arm
    since plan step ``balance:X-bi-7a`` and the transfer arm since
    ``balance:X-ci-1``, the shape ruling **R-BAL19** gave the companion
    query for finding **BAL-482**: "no rule would write it back" is
    ``recurs``'s rule, and a ``WHERE`` restating it -- the ``template_id IS
    NULL`` / ``transfer_template_id IS NULL`` each arm read until then --
    was that rule spelled a second time in another language, and wrong in
    it: a rule-less definition's row is template-linked, so the SQL counted
    it REGENERABLE and truncate promised a row back that no rule will write
    (finding **BAL-492**, both halves).  The queries keep the period and
    live scope (:func:`_live_rows_of`); the three owner-held facts
    are asked in Python, and the two arms ask them through one predicate so
    a transfer and a transaction cannot be graded by different rules.

    Args:
        period_ids: The pay-period ids being deleted.

    Returns:
        The number of unrecoverable rows (transactions plus transfers; a
        transfer counts once).
    """
    transactions = _live_rows_of(
        Transaction, Transaction.template, period_ids,
        Transaction.transfer_id.is_(None),
    )
    transfers = _live_rows_of(Transfer, Transfer.template, period_ids)
    return (
        sum(1 for row in transactions if not _regenerable(row))
        + sum(1 for row in transfers if not _regenerable(row))
    )


def _regenerable(row) -> bool:
    """Return whether regeneration would write *row* back as it stands.

    A Projected row of a repeating definition that is not an override: the
    rule both destructive gates ask, stated once.  It was a nested function
    of :func:`count_discardable_items` until plan step ``pay_calendar:C21``
    lifted it, unchanged, for :func:`gate_removable_head`, which asks the
    same question of the head.  **It does not see a payment or purchase,
    and no gate relies on it to**: truncate and regenerate refuse a period
    holding one upstream of their discard count (the lock classifier's
    ``HOLDS_MOVEMENT``, plan step ``credit_card:CC-5-4a-4``), and the head
    gate asks
    :func:`app.services.pay_period_locks.movements_held_in` beside it
    (:func:`_reject_held_rows`, ruling **R-PC115**).  Ledger row **PC-524**
    -- a purchase deleted with an untouched template row this calls
    regenerable, or after Confirm & discard -- was re-measured in its seven
    cases at plan step ``pay_calendar:C22`` and every one is refused.

    Args:
        row: A live ``Transaction`` or ``Transfer``.

    Returns:
        ``True`` when a rule would write the row back exactly.
    """
    return row.recurs and not row.is_override and is_projected(row)



def settled_transaction_count(user_id: int) -> int:
    """Count the user's non-deleted settled items (the reset gate).

    The lock classifier's own items
    (:func:`~app.services.pay_period_locks.settled_items`) over every period
    the owner has, so a row that does not lock a period cannot block a
    reset: one definition, read twice.  **A settled transfer counts ONCE**
    (ruling **R-BAL126**, plan step ``balance:X-bi-6-4a-3``), by its own
    status; it counted twice until then, once per settled shadow, and the
    refusal's "you have N settled transaction(s)" said 254 where the owner
    has 214 settled rows and 20 settled transfers (the 2026-09-30 00:11
    production dump: 234 now).  The discard gate
    (:func:`count_discardable_items`) and the archive receipts count a
    transfer once already.

    Args:
        user_id: The owning user's id.

    Returns:
        The number of settled, non-deleted rows and transfers the user has.
    """
    return _count(pay_period_locks.settled_items(_owner_periods(user_id)))


def movement_holding_row_count(user_id: int) -> int:
    """Count the user's items holding a payment or purchase (the reset's second gate).

    Plan step ``credit_card:CC-5-4a-4``, ruling **R-CC65** ("Reset refuses
    while any row holds a payment or purchase (dated or not)").  A reset
    deletes EVERY pay period, and with it every row and transfer through the
    ``pay_period_id`` cascades -- so any item holding a movement, whatever
    its status and whether or not it is soft-deleted, is one the reset
    would destroy and the movement's key now refuses to lose.  The lock
    classifier's ``HOLDS_MOVEMENT`` reads the same items per period
    (:func:`~app.services.pay_period_locks.items_holding_a_movement`); this
    counts them over the whole schedule, a transfer ONCE (ruling
    **R-BAL126**) where it counted once per holding shadow until plan step
    ``balance:X-bi-6-4a-3``.

    Args:
        user_id: The owning user's id.

    Returns:
        The number of the user's rows and transfers holding at least one
        movement.
    """
    return _count(
        pay_period_locks.items_holding_a_movement(_owner_periods(user_id)),
    )


def _owner_periods(user_id: int):
    """Return the query of *user_id*'s pay-period ids: the reset's scope.

    A reset deletes every period the owner has, and every item filed in
    them goes with it, so the items it asks about are the ones filed there:
    the lock classifier's scope, over the whole schedule.
    """
    return db.session.query(PayPeriod.id).filter(PayPeriod.user_id == user_id)


def _count(items) -> int:
    """Count *items*, a ``(rows, transfers)`` pair from ``pay_period_locks``."""
    return sum(query.count() for query in items)
