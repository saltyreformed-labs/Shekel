"""
Shekel Budget App -- Pay Period Lock Classifier

**The one place that decides whether a pay period may be deleted or
rebuilt**, and nothing here deletes or rebuilds anything.  Truncate and
regenerate consult it before touching a row; the settings UI renders its
result as a per-period lock badge.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``, and issues no write of any
kind.

**It also WORDS a lock that holds money, since plan step
``pay_calendar:C22``** (ruling **R-PC116**): :func:`movements_held_in` names
every payment and purchase a paycheck holds, read through the same queries
the classifier locks by, and :func:`locked_refusal` is truncate and
regenerate's sentence over them -- beside the reader so the lock and the
words cannot disagree, and here rather than in ``pay_period_gates`` because
that module sits at pylint's 1,000-line ceiling and imports this one.

**It lived inside ``pay_period_admin`` until plan step C3-a** (developer
ruling, 2026-08-10), which is where its own docstring called it "the module's
foundation" while that module's other job was the four destructive writers
built ON the foundation.  Two concerns, and the seam is sharp: everything here
answers a read-only question about a period's state, everything there acts on
the answer.  The line count is what reported it -- ``pay_period_admin`` reached
pylint's 1000-line ceiling -- but the ceiling was the symptom.

The precedence lives in :func:`_resolve_lock`, which
:func:`classify_schedule_locks` is the only caller of.  *That paragraph said
the precedence was "shared by the single-period and bulk classifiers, so the two
query strategies (scalar EXISTS vs. set membership) cannot disagree" until plan
step C2-f3b re-read it: there had been ONE query strategy since the
single-period door became a delegating wrapper (``0c7bb2a``), so the property it
claimed had no subject, and that door -- which no module under ``app/`` had
called since -- is DELETED.*

**It classifies a whole PAY CALENDAR since plan step C2-f3b**, not a list of
ORM rows.  Two things moved with that, and the second is what the first is for.

**The HISTORICAL test reads the DERIVED end.**  It compared
``budget.pay_periods.end_date`` -- a stored copy of ``lead(start_date) - 1``
that plan step **C4-c** dropped and that nothing reconciles against the paydays it
derives from.  A period this classifier calls historical is HARD-LOCKED, so a
stale column was a paycheck the app either protected or offered to delete for
the wrong reason.  Reading it off the derivation means the lock decision and
every other "which paycheck" answer in the application come from one rule, and
that this module survives C4 untouched.

**And the DOOR takes the calendar rather than a period set, which is what makes
a wrong input unconstructible.**  A first cut of this step took an iterable of
:class:`~app.services.pay_calendar.DerivedPeriod` and REFUSED one carrying no
``period_id`` -- a projection past the owner's horizon, which would key every
such period in a call under the same ``None``.  That refusal was a fence, and
all three ``app/`` callers were passing exactly one value:
:meth:`~app.services.pay_calendar.PayCalendar.saved`.  **An argument a caller
can get wrong is a defect rather than a contract** -- the sentence
:func:`~app.services.pay_calendar._views.saved_window` already makes one layer
down -- so the argument is gone and the door reads the window itself.  There is
nothing left to refuse (adversarial design review, 2026-08-19).

**``as_of`` is REQUIRED, which is a rule about clocks rather than about
defaults.**  It defaulted to ``date.today()``, so ``regenerate_pay_periods``
read the wall clock THREE times for one decision -- benign only because a
period cannot become historical between two statements of one transaction, an
argument that holds by timing rather than by construction.  A caller now
resolves the owner's civil day ONCE and hands it down.  The value every
``app/`` caller supplies is :func:`app.utils.dates.display_today`, ruled
2026-08-19 by the developer: this decides something against the OWNER's
calendar, and the process clock is the container's (finding **balance:N-191**,
which named this function as one of the two sites that needed the ruling).
"""

import enum
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Query
from sqlalchemy.sql.expression import ColumnElement

from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import transfer_legs
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.utils import archive_helpers
from app.utils.balance_predicates import settled_status_ids

logger = logging.getLogger(__name__)


class PeriodLockReason(enum.Enum):
    """Why a pay period may not be deleted or rebuilt.

    A non-``None`` reason is a HARD lock: the period either is historical
    or holds irreplaceable state (settled money, posted ledger entries), and
    no operation may delete or rebuild it --
    not even with ``confirm_discard``.  **An account's balance ASSERTION is no
    longer among them, and it did not become deletable**: ruling R-EO moved the
    assertion off the pay period entirely, so a schedule operation cannot reach
    it at all (see :func:`_resolve_lock`).  ``None`` means the
    period is the mutable payload truncate / regenerate may rewrite; its
    projected and ad-hoc rows are guarded separately by the overridable
    discard gate.

    The members are ordered by precedence.  The classifier returns the
    FIRST applicable reason, so a historical period that also holds a
    settled transaction reports ``HISTORICAL``.

    **``HOLDS_MOVEMENT`` joined at plan step ``credit_card:CC-5-4a-4``**
    (rulings **R-CC54**: "truncate/regenerate lock its period", and
    **R-CC66**'s badge): a period holding a row or a transfer -- any status,
    soft-deleted or not -- that holds a payment or a purchase, dated or not.
    A movement is money that moved, so an item holding one is history, and
    deleting the period deleted it through the ``pay_period_id`` cascades;
    the movement's key refuses that now, and this makes the refusal a
    designed one.  It outranks ``LEDGER_POSTINGS`` because it is
    the truer sentence where both hold: a dated purchase's own legs are
    what make a period's ledger non-zero.

    **A TRANSFER answers ``SETTLED_TXN`` and ``HOLDS_MOVEMENT`` for itself
    since plan step ``balance:X-bi-6-4a-3``** (rulings **R-BAL125**,
    **R-BAL157**): by its own status, and by the one ``transfer_legs``
    question, never through its shadow rows (:func:`settled_items`,
    :func:`items_holding_a_movement`).
    """

    HISTORICAL = "historical"
    SETTLED_TXN = "settled"
    HOLDS_MOVEMENT = "holds_movement"
    LEDGER_POSTINGS = "ledger_postings"


def _resolve_lock(
    *, is_historical: bool, has_settled: bool, holds_movement: bool,
    has_unbalanced_ledger: bool,
) -> PeriodLockReason | None:
    """Apply the lock-reason precedence to four already-computed booleans.

    The single source of truth for the ordering; :func:`classify_schedule_locks`
    is its only caller (the module docstring says why the single-period
    classifier this once also served is gone).

    **``ACCOUNT_ANCHOR`` left this set at plan step X-f1c3c** (ruling R-EO),
    and it left by becoming unreachable rather than by being relaxed.  It
    refused a period an account's ``current_anchor_period_id`` pointed at; that
    column is deleted, and a balance ASSERTION no longer references a pay
    period either, so no period delete can take one.  What is still worth
    protecting is the period's POSTED state, and ``LEDGER_POSTINGS`` -- which
    outranked ``ACCOUNT_ANCHOR`` anyway -- covers it: measured on the
    developer's production data, all 10 periods holding an assertion carry an
    unbalanced ledger account, so the deleted reason was refusing nothing that
    survives without it.

    **``RECURRENCE_ANCHOR`` left the same way at plan step R7b-4.**  It
    refused a period some recurrence rule's ``start_period_id`` pointed at,
    and the hazard was real while it stood: that FK is ``ON DELETE SET NULL``,
    so deleting the period silently erased the rule's opening bound.  R7b-4
    folded the FK into ``recurrence_rules.start_date`` -- a DATE, which no
    schedule operation can cascade -- so a rule's opening bound now survives
    the deletion of any period.  The lock was protecting a bound that can no
    longer be lost, which makes it unreachable rather than relaxed.

    Args:
        is_historical: The period has already ended (``end_date`` is
            before the reference date).
        has_settled: The period holds a non-deleted settled row or transfer
            (see :func:`settled_items`).
        holds_movement: A row or a transfer in the period holds a payment or
            a purchase (see :func:`items_holding_a_movement`).
        has_unbalanced_ledger: The period's journal entries do NOT net to
            zero per ledger account -- posted financial state a CASCADE
            delete would mis-state (see
            :func:`_period_ids_with_unbalanced_ledger`).

    Returns:
        The first applicable :class:`PeriodLockReason`, or ``None`` when
        the period is mutable.
    """
    if is_historical:
        return PeriodLockReason.HISTORICAL
    if has_settled:
        return PeriodLockReason.SETTLED_TXN
    if holds_movement:
        return PeriodLockReason.HOLDS_MOVEMENT
    if has_unbalanced_ledger:
        return PeriodLockReason.LEDGER_POSTINGS
    return None


def classify_schedule_locks(
    calendar: PayCalendar, *, as_of: date,
) -> "dict[int, PeriodLockReason | None]":
    """Return ``{period_id: reason | None}`` for every SAVED period of *calendar*.

    Three set queries plus an in-memory date check -- the no-N+1 path the truncate
    and regenerate doors run before they delete anything, and the settings page
    renders as a per-period badge.

    **It takes the CALENDAR, not a period set** (plan step C2-f3b).  The result
    is keyed by ``budget.pay_periods.id``, so an unmaterialised period -- a
    projection past the owner's horizon, which carries ``period_id = None`` --
    would key every such period in one call under the same entry and collapse
    them onto each other (ledger row **P21**'s shape).  Reading
    :meth:`~app.services.pay_calendar.PayCalendar.saved` here rather than taking
    its result means no caller can supply that set at all: the door's one
    argument is the owner's whole schedule, and every value it admits is one the
    derivation produced.  The refusal a first cut of this step carried has no
    subject.

    **The HISTORICAL test reads the DERIVED end**: a period has ended when the
    day before its successor's payday is behind *as_of*.  That is the same
    figure ``budget.pay_periods.end_date`` used to store, read from the
    derivation instead so this decision could not be one a stale column moved
    -- and so plan step **C4-c**, which dropped the column, reached this
    module with nothing to change.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
            Only its SAVED periods are classified; a projection names no row to
            answer about.
        as_of: The owner's civil day, for the historical test: the period
            containing *as_of* and every later one is not historical.
            **Keyword-only and REQUIRED.**  It defaulted to ``date.today()``
            until plan step C2-f3b, which is how ``regenerate_pay_periods`` came
            to read the wall clock three times for one decision; every ``app/``
            caller now resolves :func:`app.utils.dates.display_today` once and
            threads it, which is the ruling of 2026-08-19 on the two sites
            finding **balance:N-191** named.

    Returns:
        A dict mapping each saved period's ``period_id`` to its lock reason (or
        ``None``).  Empty for an owner who has never generated a schedule -- no
        periods, no queries.

    Raises:
        PayCalendarError: *calendar*'s saved periods do not cover an unbroken
            span, which :meth:`~app.services.pay_calendar.PayCalendar.saved`
            refuses.  Unreachable through
            :func:`~app.services.pay_calendar.calendar_for`, which reads saved
            rows only.
    """
    saved = calendar.saved()
    period_ids = [period.period_id for period in saved]
    if not period_ids:
        return {}

    settled = _period_ids_of(settled_items(period_ids))
    holding = _period_ids_of(items_holding_a_movement(period_ids))
    unbalanced = _period_ids_with_unbalanced_ledger(period_ids)

    return {
        period.period_id: _resolve_lock(
            is_historical=period.end_date < as_of,
            has_settled=period.period_id in settled,
            holds_movement=period.period_id in holding,
            has_unbalanced_ledger=period.period_id in unbalanced,
        )
        for period in saved
    }


def items_holding_a_movement(periods: "list[int] | Query") -> "tuple[Query, Query]":
    """Return the ITEMS filed in *periods* that hold a payment or purchase.

    **Every item, soft-deleted or not, and every status** (plan step
    ``credit_card:CC-5-4a-4``, ruling **R-CC54**): the period's delete takes
    every row and transfer filed under it through the ``pay_period_id``
    cascades, hidden ones included, so any of them holding a movement is
    one the database now refuses to lose.  Dated or not: an un-dated
    purchase is money in flight, as recorded as a dated one.  A transfer is
    asked the ONE ``transfer_legs`` question (rulings **R-BAL125**,
    **R-BAL157**), which reads each side's records off the TRANSFER by its
    side links (plan step ``balance:X-bi-6-4d-2``); a hidden transfer holds
    none (ruling **credit_card:R-CC75**, finding **BAL-532**).

    The pay-period doors' one reading of it: the lock classifier's
    ``HOLDS_MOVEMENT`` (per period), the reset gate's second count
    (``pay_period_gates.movement_holding_row_count``), and -- through
    :func:`movements_held_in`, these queries with the movements joined on --
    truncate and regenerate's refusal naming each movement (plan step
    ``pay_calendar:C22``, ruling **R-PC116**) and "Remove earlier
    paychecks"' naming each item (ruling **R-PC115**).

    Args:
        periods: The pay-period ids to look in -- a list, or a query of
            ids (the reset gate's: every period of one owner).

    Returns:
        :func:`_items`' pair of queries.
    """
    return _items(
        periods,
        rows=(archive_helpers.holds_a_movement(),),
        transfers=(transfer_legs.transfer_holds_a_movement(),),
    )


#: How an item with no name of its own is named: ``budget.transfers.name``
#: admits none (every door writes one; a legacy row may not), and every
#: refusal that names an ITEM -- the row or transfer the owner sees -- says
#: this.  A refusal naming a transfer's SIDE names its account instead.
_UNNAMED_TRANSFER = "a transfer"


def item_of(
    model: type, item_id: int, name: "str | None",
) -> "tuple[tuple[str, int], str]":
    """Return how the pay-period refusals key and name one ITEM.

    An item is what the owner sees in a paycheck: a plan row, or a transfer
    once for its two legs.  Its key, ``("row", id)`` or ``("transfer", id)``,
    is what lets a refusal name an item held for two reasons once; its name
    is the row's, or the transfer's (:data:`_UNNAMED_TRANSFER` when it has
    none).  The ONE spelling of both (plan step ``pay_calendar:C22``):
    :func:`movements_held_in` and "Remove earlier paychecks"' own item walk
    (``pay_period_gates._reject_held_rows``) each call it, so the head
    refusal's "named once" cannot come to depend on two spellings agreeing.

    Args:
        model: ``Transaction`` or ``Transfer``.
        item_id: The row's or the transfer's id.
        name: Its ``name`` column.

    Returns:
        ``(key, name)``.
    """
    if model is Transfer:
        return ("transfer", item_id), name or _UNNAMED_TRANSFER
    return ("row", item_id), name


@dataclass(frozen=True)
class HeldMovement:
    """One payment or purchase an item filed in a pay period holds, as a refusal names it.

    The unit of :func:`movements_held_in`.  **A PAYMENT** is a settle's
    covering movement (``covers_settlement``), kept un-dated across a revert
    (ruling **R-BAL61**) -- a refusal names it by its item; **a PURCHASE** is
    every other movement, named by its own description.  The same split
    ``archive_helpers.HeldMovements`` aggregates.

    Attributes:
        period_id: The pay period the holding item is filed in.
        item: The item as the owner sees it, keyed by :func:`item_of`: a
            transfer once for its two legs.
        item_name: What the owner calls the item (:func:`item_of`).
        is_payment: The movement is a payment rather than a purchase.
        description: The movement's own description (a purchase's store or
            note; a covering movement carries its row's name at the settle).
        amount: The movement's figure, signed: a refund is negative (ruling
            ``bank_import:R-II``).
    """

    period_id: int
    item: "tuple[str, int]"
    item_name: str
    is_payment: bool
    description: str
    amount: Decimal


def movements_held_in(periods: "list[int] | Query") -> "list[HeldMovement]":
    """Return every payment and purchase the items filed in *periods* hold, named.

    **The pay-period doors' one NAMED reading of what a paycheck holds**
    (plan step ``pay_calendar:C22``, ruling **R-PC116**): truncate and
    regenerate name each movement in their refusal, and "Remove earlier
    paychecks" names each holding item (ruling **R-PC115**).  It is
    :func:`items_holding_a_movement`'s two queries with each item's
    movements joined on, so it reaches exactly the items that question
    reaches -- every status, soft-deleted or not, a transfer through the
    one ``transfer_legs`` join -- and a period the lock calls
    ``HOLDS_MOVEMENT`` is one this names.

    **A transfer's payment is named ONCE**: each side keeps a record of it
    (one covering movement per side) and the transfer stores its one figure
    (plan step ``balance:X-bi-6-4d-2``), and the owner sees one transfer and
    one payment.  The two records are collapsed by transfer -- the sides'
    descriptions are their legs' labels (``status_seam._side``,
    ``TransferLeg.name``) and differ, and both name the transfer's figure --
    and a row's movements are never collapsed: two equal purchases are two
    purchases.

    Args:
        periods: The pay-period ids to look in -- a list or a query of ids.

    Returns:
        The movements, ordered by period, then item name, then item, then
        the order they were recorded in.
    """
    held = []
    copies_named = set()
    for model, query in _movement_arms(periods):
        for period_id, item_id, name, covers, description, amount in (
            query.order_by(TransactionEntry.id)
        ):
            item, item_name = item_of(model, item_id, name)
            if model is Transfer and covers:
                if item in copies_named:
                    continue
                copies_named.add(item)
            held.append(HeldMovement(
                period_id, item, item_name, covers, description, amount,
            ))
    # Stable, so the recorded order survives inside one item.
    return sorted(
        held, key=lambda each: (each.period_id, each.item_name, each.item),
    )


def _movement_arms(periods: "list[int] | Query") -> "tuple[tuple[type, Query], ...]":
    """Return :func:`movements_held_in`'s two queries, one per KIND of item.

    :func:`items_holding_a_movement`'s pair with each item's movements joined
    on: a row's through its ``entries``, a transfer's through the one
    ``transfer_legs`` join, scoped to the transfers that pair's query
    selects.  Each yields ``(period id, item id, item name, is a payment,
    description, amount)``: a row's movement states its own amount, and a
    transfer side's record names its TRANSFER's one settled figure (plan
    step ``balance:X-bi-6-4d-2``).

    Args:
        periods: As :func:`movements_held_in`.

    Returns:
        ``((Transaction, row query), (Transfer, transfer query))``, unexecuted.
    """
    rows, transfers = items_holding_a_movement(periods)
    movement = (TransactionEntry.covers_settlement, TransactionEntry.description)
    return (
        (Transaction, rows.join(Transaction.entries).with_entities(
            Transaction.pay_period_id, Transaction.id, Transaction.name,
            *movement, TransactionEntry.amount,
        )),
        (Transfer, transfer_legs.held_transfer_entries(
            Transfer.id.in_(transfers.with_entities(Transfer.id)),
        ).with_entities(
            Transfer.pay_period_id, Transfer.id, Transfer.name, *movement,
            Transfer.settled_figure,
        )),
    )


def paycheck_name(period: DerivedPeriod) -> str:
    """Return how a pay-period refusal names *period*: ``"The 2026-03-12 paycheck"``.

    The ONE spelling every refusal naming a paycheck uses -- "Remove
    earlier paychecks"' (``pay_period_gates``, plan step
    ``pay_calendar:C21``) and truncate and regenerate's
    (:func:`locked_refusal`, plan step ``C22``).  It lived in
    ``pay_period_gates`` until C22, which cannot be imported from here.
    """
    return f"The {period.start_date.isoformat()} paycheck"


def locked_refusal(
    to_delete: "list[DerivedPeriod]",
    blocking: "dict[int, PeriodLockReason]",
) -> str:
    """Return truncate and regenerate's refusal for the *blocking* periods.

    **Ruling R-PC116** (plan step ``pay_calendar:C22``, replacing ruling
    **R-CC66**'s sentence, which named the period alone): one sentence per
    paycheck locked ``HOLDS_MOVEMENT`` naming each payment and purchase it
    holds, with its amount and its row (:func:`_holds_sentence`), then the
    one remedy, "Remove or move it first."; then the count of the periods
    locked for any other reason, the sentence the refusal has always ended
    with.  ``pay_period_gates.gate_deletable_tail`` raises it.

    **The movements are :func:`movements_held_in`'s** -- the named reading of
    the very queries :func:`classify_schedule_locks` locked those periods by
    -- so a paycheck locked for holding one is a paycheck this names.  Were
    it ever not, it would land in the count rather than drop out of the
    message, so the refusal is never silent about a period it refuses.

    Args:
        to_delete: The periods the door would delete, payday ascending.
        blocking: The hard-locked ones among them, keyed by period id.

    Returns:
        The refusal's message.
    """
    held: "dict[int, list[HeldMovement]]" = {}
    for movement in movements_held_in([
        period.period_id for period in to_delete
        if blocking.get(period.period_id) is PeriodLockReason.HOLDS_MOVEMENT
    ]):
        held.setdefault(movement.period_id, []).append(movement)
    sentences = [
        _holds_sentence(period, held[period.period_id])
        for period in to_delete if period.period_id in held
    ]
    if sentences:
        named = sum(len(movements) for movements in held.values())
        sentences.append(
            "Remove or move it first." if named == 1
            else "Remove or move them first.",
        )
    others = len(blocking) - len(held)
    if others:
        sentences.append(
            f"Operation refused: {others} pay period(s) are locked (past, "
            f"settled, or holding posted ledger entries) and cannot be "
            f"deleted or rebuilt."
        )
    return " ".join(sentences)


def _holds_sentence(period: DerivedPeriod, movements: "list[HeldMovement]") -> str:
    """Return what one locked paycheck holds, in ruling R-PC116's words.

    "The 2026-11-16 paycheck holds 2 payments or purchases you entered
    (Kroger, $87.43, in Groceries; Rent's payment, $1,200.00)."  The noun
    counts the kinds it names: "purchase(s)", "payment(s)", or, holding
    both, "payments or purchases".

    Args:
        period: The locked paycheck.
        movements: What it holds -- at least one -- in the reader's order.

    Returns:
        The sentence, with its full stop.
    """
    payments = sum(1 for movement in movements if movement.is_payment)
    if 0 < payments < len(movements):
        noun = "payments or purchases"
    else:
        noun = "payment" if payments else "purchase"
        if len(movements) != 1:
            noun += "s"
    names = "; ".join(_named(movement) for movement in movements)
    return (
        f"{paycheck_name(period)} holds {len(movements)} {noun} you entered "
        f"({names})."
    )


def _named(movement: HeldMovement) -> str:
    """Return how ruling R-PC116 names one movement.

    A purchase by its own description, its amount and the row it sits in
    ("Kroger, $87.43, in Groceries"): the grid shows rows rather than
    purchases, so the row says where to look.  A payment by its item
    ("Rent's payment, $1,200.00"), since its own description is only that
    row's name again.

    Args:
        movement: One of :func:`movements_held_in`'s.

    Returns:
        The clause.
    """
    figure = _money(movement.amount)
    if movement.is_payment:
        return f"{movement.item_name}'s payment, {figure}"
    return f"{movement.description}, {figure}, in {movement.item_name}"


def _money(amount: Decimal) -> str:
    """Return *amount* as the grid shows a purchase: ``$87.43``, a refund ``-$12.00``.

    The ``money`` macro's spelling (``templates/_money_macros.html``), so a
    refusal names the figure the owner sees in the row's purchase list.
    """
    if amount < 0:
        return f"-${-amount:,.2f}"
    return f"${amount:,.2f}"


def settled_items(periods: "list[int] | Query") -> "tuple[Query, Query]":
    """Return the non-deleted SETTLED items filed in *periods*.

    "Settled" is the canonical ``balance_predicates.settled_status_ids``
    (Paid or Received), asked of a row's status and of a transfer's OWN
    status (plan step ``balance:X-bi-6-4a-3``): a transfer's legs carry its
    status (Transfer Invariant 3), and ``X-bi-6``'s end is status in one
    row.  The lock classifier's ``SETTLED_TXN`` and the reset gate's first
    count (``pay_period_gates.settled_transaction_count``) both read this,
    so a row that does not lock a period cannot block a reset.

    **The parent decides, and since plan step ``balance:X-bi-6-4d-2`` it is
    the only status read here**: a settle STORED its twins Projected under a
    Paid transfer until ``balance:X-bi-6-4d-2`` deleted the twins, but the
    row query excluded every twin (``transfer_id IS NULL``) and the transfer
    query asks the transfer, so the drift ruling **R-JM** settled here (a
    parent over a shadow whose status differed) was stored and never read.
    No row names a transfer now (``ck_transactions_names_no_transfer``).  A
    movement either side holds still holds its period, whatever the
    status, through :func:`items_holding_a_movement`.

    Args:
        periods: As :func:`items_holding_a_movement`.

    Returns:
        :func:`_items`' pair of queries.
    """
    settled = settled_status_ids()
    return _items(
        periods,
        rows=(
            Transaction.status_id.in_(settled),
            Transaction.is_deleted.is_(False),
        ),
        transfers=(
            Transfer.status_id.in_(settled),
            Transfer.is_deleted.is_(False),
        ),
    )


def _items(
    periods: "list[int] | Query",
    *,
    rows: "tuple[ColumnElement, ...]",
    transfers: "tuple[ColumnElement, ...]",
) -> "tuple[Query, Query]":
    """Return ``(rows, transfers)``: one query per KIND of item, each yielding its period.

    **An item is what the owner sees in a pay period**: a plan row, or a
    TRANSFER once for its two legs, which the transfer query asks.  The row
    query's ``transfer_id IS NULL`` (the discard gate's idiom) kept a
    transfer's shadow rows out until plan step ``balance:X-bi-6-4d-2``
    deleted them; it excludes nothing since, and goes with the column at
    plan step ``balance:X-bi-6-5``.
    Each query yields one ``(pay_period_id,)`` per item, so a caller reads
    the set of periods (:func:`_period_ids_of`) or counts the items
    (``Query.count``); one that needs more of each item swaps the columns
    with ``with_entities``, keeping the scope.

    **One premise the DOORS held and no key did until plan step
    ``balance:X-bi-6-4d-2``** (the reason :func:`settled_items` gives for
    status drift, for the period a shadow mirrored).  A shadow's
    ``pay_period_id`` was its transfer's (``transfer_service`` moved both
    together, a restore re-aligned them, carry-forward's bulk moves excluded
    shadows), so the row query's ``transfer_id IS NULL`` dropped nothing the
    transfer query did not ask.  On the PERIOD drift no door wrote -- a
    shadow filed in period B while its transfer was in period A -- each read
    missed one of the two (0 of 358 shadows on the 2026-09-30 00:11
    production dump).  That step deleted the shadows, and
    ``ck_transactions_names_no_transfer`` refuses a new one: no transaction
    row names a transfer, so none can sit in a period its transfer is not in.

    Args:
        periods: The pay-period ids to look in -- a list or a query of ids.
        rows: Clauses over ``Transaction`` selecting the row items.
        transfers: Clauses over ``Transfer`` selecting the transfer items.

    Returns:
        ``(row query, transfer query)``, unexecuted.
    """
    return (
        db.session.query(Transaction.pay_period_id).filter(
            Transaction.pay_period_id.in_(periods),
            Transaction.transfer_id.is_(None),
            *rows,
        ),
        db.session.query(Transfer.pay_period_id).filter(
            Transfer.pay_period_id.in_(periods), *transfers,
        ),
    )


def _period_ids_of(items: "tuple[Query, Query]") -> set[int]:
    """Return the periods holding any of *items* (a pair from :func:`_items`)."""
    rows, transfers = items
    return {period_id for (period_id,) in rows.union(transfers)}


def _period_ids_with_unbalanced_ledger(period_ids: list[int]) -> set[int]:
    """Return the ``period_ids`` whose entries do NOT net to zero per ledger.

    The double-entry gate of the lock classifier (the 2026-07-02 adversarial
    review's R2 defense-in-depth): ``journal_entries.pay_period_id`` is
    ``ON DELETE CASCADE``, so deleting a period disposes its entries and legs
    at the DB tier -- outside the ORM, where the balanced-journal trigger
    never fires on DELETE.  That disposal is safe ONLY when the period's
    postings net to zero per ledger account (e.g. an original + its reversal,
    which the R2 attribution rule keeps in one period): the cascade then
    removes a self-cancelling pair and no account's sum moves.  A period
    whose postings carry a NON-zero per-account net -- a loan opening /
    true-up correction, or any attribution drift -- holds posted financial
    state a cascade would silently mis-state, so it hard-locks.

    A period holding a settled transaction is already locked upstream
    (``SETTLED_TXN`` precedence); this catches the posted state settled-row
    counting cannot see.

    Args:
        period_ids: The pay-period ids being classified.

    Returns:
        The subset whose postings have a non-zero net on any ledger account.
    """
    rows = (
        db.session.query(JournalEntry.pay_period_id)
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(JournalEntry.pay_period_id.in_(period_ids))
        .group_by(JournalEntry.pay_period_id, Posting.ledger_account_id)
        .having(db.func.sum(Posting.amount) != 0)
        .all()
    )
    return {row[0] for row in rows}


def posted_totals(user_id: int) -> "dict[tuple[int, int], Decimal]":
    """Return the owner's posted total per ``(scenario_id, ledger_account_id)``.

    **What "Remove earlier paychecks" must leave unchanged** (plan step
    ``pay_calendar:C21``, ruling **R-PC114**, amending **R-PC109**).  That
    door deletes paychecks whose journal entries the ``CASCADE`` takes, then
    re-files what the posted ledger rebuilds from the owner's records (loan
    and account openings, true-ups) through Reset's two re-syncs; it asks
    this before and after, and is refused -- rolled back -- if any total
    moved.  Grouped by SCENARIO as well as ledger account, because journal
    entries are scenario-scoped and a move in one scenario must not be
    cancelled by another's.  It lives here because this is where a period's
    ledger is read: the ledger-model fence (W9908) admits this module and
    not the gates.

    Args:
        user_id: The owning user's id.

    Returns:
        The summed ``account_postings.amount`` per key, for every key the
        owner's entries touch.
    """
    return {
        (scenario_id, ledger_account_id): total
        for scenario_id, ledger_account_id, total in (
            db.session.query(
                JournalEntry.scenario_id, Posting.ledger_account_id,
                db.func.sum(Posting.amount),
            )
            .join(Posting, Posting.journal_entry_id == JournalEntry.id)
            .filter(JournalEntry.user_id == user_id)
            .group_by(JournalEntry.scenario_id, Posting.ledger_account_id)
            .all()
        )
    }


def ledger_account_names(ledger_account_ids) -> "list[str]":
    """Return the names a refusal gives *ledger_account_ids*, distinct and sorted.

    A ledger account paired with one of the owner's accounts is named as that
    account, because the owner knows "Checking" and not its chart rows; one
    paired with none is named as itself.

    Args:
        ledger_account_ids: ``budget.ledger_accounts.id`` values.

    Returns:
        The names, each once, alphabetical.
    """
    rows = (
        db.session.query(LedgerAccount.name, Account.name)
        .outerjoin(Account, Account.id == LedgerAccount.account_id)
        .filter(LedgerAccount.id.in_(list(ledger_account_ids)))
        .all()
    )
    return sorted({account or ledger for ledger, account in rows if account or ledger})
