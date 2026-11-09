"""
Shekel Budget App -- Transaction Service: what pressing CREDIT means.

**Credit puts the row on the card** (rulings plan#2887 and plan#2888, developer
2026-11-04; the trace on plan#2890).  Credit stays as the way the owner says a
purchase went on the card, and what the card arc adds is the card itself: the
row's money moves ON the card from the row's own date, so the card owes it,
on that date's statement and due on that statement's due date, and checking
pays it later through the card's payoff row.  A Credit press is therefore the
settle verb with the card as its tender (:func:`._settle.settle_transaction`),
on the row's own day, and it writes no CC Payback row.  The payback was the
cheat's spelling of "checking pays this next paycheck" from before the card
was an account (``credit_workflow.mark_as_credit`` wrote one until this task),
and two spellings of one payment would pay the card twice.

**Stored as Paid with its movement on the card, drawn as Credit** (decided
under R-BAL207 in the trace, and graded there).  The movement's account is
the fact's one home: a row whose payment is on a card IS a purchase on the
card, whatever its status column says.  The alternative, status Credit
holding the movement, loses the money: Credit is outside the settled band
every balance predicate reads, so the card's fold drops the purchase and
reads $800 short (review B, ``cash_ledger._cash_leg``), and teaching those
predicates a fourth settled status would state the fact twice.

**Undo CC takes the row off the card** (plan task plan#3055, ruling
plan#2887: "Paid means paid"): :func:`undo_credit` sets it back to Projected
through the one status door Status -> Projected takes, and the status seam
moves its kept payment onto the row's own account as it reverts, so a later
Paid leaves checking and a later Credit puts it back on the card.

**The press says its day and asks which card** (plan task plan#3056).  The
popover shows the day :func:`credit_day` gives and lets the owner type the real
one, and asks which card when the owner has two or more; it reads
:func:`credit_cards` for the choice and :func:`credit_frees_a_line` for the
caption naming the bank lines the press frees.

**The C key and the palette open the popover where the press needs what only it asks**
(plan task plan#3114, ruling plan#3112).  They post no card, no day and no
line, so with no card, two or more, or a bank line the press would free, they
open the popover; on every other row they press at once
(:func:`credit_opens_the_popover_by_key`, over the two producers above).
Which rows offer Credit at all is :func:`credit_offered`, read by the popover
and the cell alike.

**Old Credit rows keep the old path** until plan#1034's re-plan: a row in
status Credit holds its live CC Payback, and Undo CC on it still deletes that
payback (``credit_workflow.unmark_credit``).  A press on one changes nothing.

Architecture: no Flask imports; reads the row, the owner's cash-flow set, the
pay calendar and what a moved payment's matches would withdraw; mutates
through the settle verb and the status door only; does not commit.
"""

from __future__ import annotations

import logging
from collections.abc import Hashable, Iterable
from datetime import date
from typing import TYPE_CHECKING

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services.account_projection import is_revolving
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.cash_flow_set import CashFlowSet, purchase_accounts
from app.services.credit_workflow import get_active_payback, unmark_credit
from app.services.match_press import Press
from app.services.movement_account import admitted_movement_account_id
from app.services.pay_calendar import FiledRow, PayCalendar, calendar_for
from app.services.settle_day import SettleDay, recorded_settle_day
from app.services.status_seam import (
    covering_movement_of,
    pending_payment_withdrawals,
    settle_day_for_status,
    settled_on_a_card,
)
from app.services.transaction_service._door import apply_requested_status
from app.services.transaction_service._settle import settle_transaction
from app.services.transaction_service._status_rules import settled_status_id
from app.services.transfer_legs import PlanItem, TransferLeg, cell_key
from app.utils.balance_predicates import is_credit, is_projected
from app.utils.dates import display_today
from app.utils.log_events import BUSINESS, EVT_CREDIT_MARKED, log_event

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.match_withdrawal import MatchWithdrawal

logger = logging.getLogger(__name__)


def mark_as_credit(
    txn: Transaction, *, card_account_id: int | None = None,
    day: date | None = None, press: Press | None = None,
) -> None:
    """Put *txn* on a credit card: settle it with its money moved on the card.

    The row settles through the ONE settle verb with the card as its tender,
    on the day the owner typed, else the day :func:`credit_day` gives it, so
    it records what a Paid press with "Paid from: the card" records and
    nothing else: one covering movement on the card carrying the row's
    figure, and no CC Payback row.

    Refused, before anything is written: an income row (nothing is bought
    with a paycheck), a row whose purchases are tracked (its purchases each
    name their own account, so Credit is per purchase), and a row that is
    not Projected (a stale press on a row another tab settled or cancelled).
    A row already on a card, or an old Credit row holding its live payback,
    is a press already made, so a double click changes nothing; a row in
    status Credit holding no payback (the Status dropdown's, finding
    plan#2133) is refused as not Projected, as the old press refused it.
    A day the owner typed over the one the popover showed is bounded as every
    typed settle day is, by ``status_seam.settle_day_for_status``: not before
    the owner's schedule starts (ruling **R-EL**, a door rule the settle verb
    does not hold).  The day the popover showed, posted back untouched, is the
    press's own and is not bounded there, so a row dated before the schedule
    goes on the card on its date as an unnamed press puts it.  The settle verb
    adds its own refusals: a deleted row, a day that has not come (ruling
    **R-EJ**), a day on or before the card's books open (S11 of the trace), a
    figure it cannot price.  A row whose settle records $0.00 is refused
    after it, because nothing moved on the card, so the row would be Paid
    and drawn so rather than Credit; the door's rollback undoes the settle.

    Args:
        txn: The row pressed, already proven the caller's by the door.  A
            companion's press never reaches here: the door is the owner's
            (review J).
        card_account_id: The card the owner named, or ``None`` when the press
            named none (:func:`_the_card`).
        day: The day under the popover's "Money moved on" box (plan task
            plan#3056), or ``None`` when the press named none: then
            :func:`credit_day`.  Entered either way, as the press is the
            owner's statement that the money went on the card.
        press: The save's :class:`~app.services.match_press.Press`, for the
            settle's removal act: putting a reverted row's kept payment on
            the card takes it out of any match naming it on its old account.

    Raises:
        ValidationError: On the refusals above, or when no card or more than
            one could be the one meant (:func:`_the_card`).  The settle
            verb's own refusals leave nothing written; the $0.00 one leaves
            the settle for the caller's rollback.
        NotFoundError: When *card_account_id* is not an account of the
            row's owner (one answer for "not found" and "not yours").
    """
    if txn.is_income:
        raise ValidationError("Cannot mark income as credit.")
    if txn.tracks_purchases:
        raise ValidationError(
            "This transaction uses individual purchase tracking. "
            "Mark individual entries as credit instead of the whole transaction."
        )
    if settled_on_a_card(txn) or (
        is_credit(txn) and get_active_payback(txn.id) is not None
    ):
        return
    if not is_projected(txn):
        raise ValidationError(
            f"Cannot mark a '{txn.status.name}' transaction as credit. "
            "Only projected transactions can be marked as credit."
        )
    card = _the_card(txn, card_account_id)
    own_day = credit_day(txn)
    settle_day = SettleDay(day=own_day, basis=SettledDayBasisEnum.ENTERED)
    if day is not None and day != own_day:
        settle_day = settle_day_for_status(
            txn.user_id, settled_status_id(txn), day, recorded_settle_day(txn),
        )
    settle_transaction(
        txn, settle_day=settle_day, tender_account_id=card.id, press=press,
    )
    movement = covering_movement_of(txn)
    if movement is None:
        raise ValidationError(
            f"{txn.name} is $0.00, so there is nothing to put on the card: "
            "set what it cost, then press Credit again."
        )
    log_event(
        logger, logging.INFO, EVT_CREDIT_MARKED, BUSINESS,
        "Transaction put on a credit card",
        user_id=txn.user_id,
        transaction_id=txn.id,
        card_account_id=card.id,
        settled_on=txn.settled_on.isoformat(),
        amount=str(movement.amount),
    )


def undo_credit(txn: Transaction, *, press: Press | None = None) -> None:
    """Undo CC: set *txn* back to Projected, off the card it was put on.

    **A row put on the card** (:func:`mark_as_credit`, drawn Credit) goes
    back through ``apply_requested_status``, the door the popover's Status
    -> Projected takes, so the two are one act: the status seam releases its
    day and moves its kept payment onto the row's own account
    (``status_seam._covering._move_home``), out of every match naming it on
    the card, under *press*.  The figure stays with the payment, as every
    revert keeps it (ruling **R-BAL61**), so a later Paid leaves checking at
    that figure and a later Credit puts it back on the card.  The seam logs
    the move (``credit_unmarked``), so the dropdown's door logs it too.  A
    bill that lives on the card keeps its payment there and logs nothing.

    **Every other row takes the old path** (``credit_workflow.unmark_credit``):
    an old Credit row's payback is deleted, and a row that is not drawn
    Credit is refused there in words.

    Args:
        txn: The row pressed, already proven the caller's by the door.
        press: The save's :class:`~app.services.match_press.Press`, over the
            bank lines the card's caption named.

    Raises:
        ValidationError: When *txn* is not drawn Credit, when the books hold
            the revert (ruling **R-PC97**), or when the press would free
            other lines than its page named (ruling **R-CC127**).
    """
    if not settled_on_a_card(txn):
        unmark_credit(txn.id, txn.user_id, press=press)
        return
    apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED), press=press,
    )


def credit_day(txn: Transaction, calendar: PayCalendar | None = None) -> date:
    """Return the day a Credit press puts *txn* on the card.

    The row's own date, because the date decides the statement, the due date
    and the interest (ruling plan#2910), and the owner presses Credit after
    the purchase: 20 of the 21 Credit rows on the 2026-11-04 production dump
    were pressed 4 to 22 days after their date.  A date that has not come is
    today, because a settle day cannot be in the future (ruling R-EJ; S10 of
    the trace).  A row with no date reads its paycheck's first day, today if
    that has not come either: it has no day of its own, and its paycheck's
    first day is where its money is budgeted.

    The paycheck is read off the owner's one calendar after the row (the read
    order ``require_period`` asks its callers to state); no destructive pay
    period door can commit between the two reads, both following the
    request's owner lock (plan step ``balance:X-bn``).  A caller that has
    already derived it passes it in (the popover, which shows this day in its
    "Money moved on" box, plan task plan#3056), so one request derives it
    once.

    Args:
        txn: The row about to be put on the card.
        calendar: The row OWNER's calendar, when the caller derived it after
            reading the row; ``None`` derives it here.

    Returns:
        The civil day.
    """
    own = txn.due_date
    if own is None:
        if calendar is None:
            calendar = calendar_for(txn.user_id)
        own = calendar.require_period(FiledRow.for_row(txn)).start_date
    return min(own, display_today())


def _the_card(txn: Transaction, card_account_id: int | None) -> Account:
    """Return the card a Credit press on *txn* puts it on.

    A NAMED card is gated as "Paid from" gates one -- the one gate on where a
    movement under a row may go (``movement_account.admitted_movement_account_id``:
    another owner's account is not found, an archived one is refused in
    words, one outside the owner's cash-flow set is refused) -- and must then
    be a card.  With none named, the choices are :func:`credit_cards`: the
    one card when there is one; with none there is nothing to put it on, and
    with two or more the press must say which.

    Args:
        txn: The row pressed.
        card_account_id: The card the owner named, or ``None``.

    Returns:
        The card's :class:`~app.models.account.Account`.

    Raises:
        NotFoundError: *card_account_id* is not an account of the row's owner.
        ValidationError: The named account is archived, outside the owner's
            cash-flow set, or not a card; or none was named and none, or more
            than one, could be the one meant.
    """
    if card_account_id is not None:
        account = db.session.get(
            Account, admitted_movement_account_id(txn, card_account_id, movement="purchase"),
        )
        if not is_revolving(account):
            raise ValidationError(
                f"'{account.name}' is not a credit card, so Credit cannot put "
                f"{txn.name} on it: pick one of your cards."
            )
        return account
    cards = credit_cards(txn, resolve_owner_cash_flow_set(txn.user_id))
    if len(cards) == 1:
        return cards[0]
    if not cards:
        raise ValidationError(
            f"{txn.name} cannot go on a credit card: there is no credit card "
            "in the app yet. Add your card under Accounts, then press Credit "
            "again."
        )
    raise ValidationError(
        f"You have {len(cards)} credit cards: pick the one {txn.name} went on."
    )


def credit_cards(txn: Transaction, cash_flow: CashFlowSet | None) -> tuple[Account, ...]:
    """Return the cards a Credit press naming none could put *txn* on.

    **The one answer to "which card does Credit mean"** (plan task
    plan#3056): the press asks it when nothing is named (:func:`_the_card`),
    the popover draws from it (one card named, two or more to pick from, none
    and an ask to add one), and the C key and the palette press at once only
    when it holds one (plan task plan#3114).  The cards among the accounts
    the row's payment may move through
    (``cash_flow_set.purchase_accounts``, the set the "Paid from" gate admits:
    the row's own account first, then the owner's other members).  A row
    that lives on a card means that card, however many the owner has
    (decided under R-BAL207 at plan task plan#2929: a bill on the card says
    "this went on this card", ruling plan#2926, and asking which would ask
    what the row already says).

    Args:
        txn: The row, its ``account`` loaded (a joined load).
        cash_flow: The row OWNER's cash-flow set with no override
            (``account_resolver.resolve_owner_cash_flow_set``), or ``None``
            for an owner with no grid-eligible account.

    Returns:
        The row's own card alone when it lives on one; else the owner's cards
        in member order, which may be none.
    """
    cards = tuple(
        account for account in purchase_accounts(cash_flow, txn)
        if is_revolving(account)
    )
    if is_revolving(txn.account):
        return cards[:1]
    return cards


def credit_moves_payment(txn: Transaction, cards: tuple[Account, ...]) -> bool:
    """Return whether a Credit press onto one of *cards* could move *txn*'s kept payment.

    A row Paid, then set back to Projected, keeps its payment un-dated
    (ruling **R-BAL61**), and an accepted match may still name it.  Credit
    settles with the card as the tender, and the seam moves a kept payment
    that is on another account onto it, which takes it out of every match
    naming it (``status_seam._covering._re_point``, ruling **R-CC46**).
    :func:`credit_frees_a_line` asks it.

    **"Could", across the cards the owner may pick**: True when some card the
    press could put it on is another account than the payment's, because
    the page names the lines before he picks.  A page naming a line the press
    then does not free is not refused (a save that reaches no match step is
    not graded, ``match_press.Press``); a press freeing one the page did not
    name is, on every try.  No card: no press, nothing moves.

    Args:
        txn: The row, with ``entries`` loaded or loadable.
        cards: What :func:`credit_cards` answered for it.

    Returns:
        ``True`` when the row keeps a payment and one of *cards* is another
        account than the one it is on.

    Raises:
        ValueError: From ``status_seam.covering_movement_of``, on a row
            holding two covering movements.
    """
    movement = covering_movement_of(txn)
    return movement is not None and any(
        card.id != movement.account_id for card in cards
    )


def credit_frees_a_line(
    txn: Transaction, cards: tuple[Account, ...],
    payment_withdraws: MatchWithdrawal | None,
) -> bool:
    """Return whether a Credit press on *txn* frees a bank line a match names.

    **The one answer to "does Credit unmatch a bank line"** (plan task
    plan#3056): the popover names the lines beside Credit and posts them
    when it is true (ruling **R-CC127**), and the C key and the palette open
    the popover instead of pressing when it is (ruling plan#3112, plan task
    plan#3114).  True when the press could move the payment the row keeps
    (:func:`credit_moves_payment`) and a match naming that payment frees a
    line.

    Args:
        txn: The row, with ``entries`` loaded or loadable.
        cards: What :func:`credit_cards` answered for it.
        payment_withdraws: What taking the row's kept payment out of its
            matches would withdraw (``status_seam.pending_payment_withdrawal``),
            or ``None`` when it keeps none.  Read by the caller, which may
            also draw those lines (the popover), so one read serves both.

    Returns:
        ``True`` when the press could move the payment and that frees a line.
    """
    return (
        payment_withdraws is not None
        and payment_withdraws.frees_a_line
        and credit_moves_payment(txn, cards)
    )


def credit_offered(txn: Transaction) -> bool:
    """Return whether Credit is offered on *txn*: the popover's button, the C key, the palette.

    **The one predicate for "can this row be put on the card from the grid"**
    (plan task plan#3114): a Projected expense whose purchases are not
    tracked.  An income row buys nothing, a row tracking its purchases names
    each purchase's account instead (Credit is per purchase there), and a row
    that is not Projected is settled, cancelled or an old Credit row.  Keyed
    on the STORED status, so a row already on the card (stored Paid, drawn
    Credit, plan task plan#3054) offers it no more than an old Credit row
    does.  :func:`mark_as_credit` refuses an income row, a row tracking its
    purchases and a row that is not Projected in words at the door, each with
    its own reason; a press on a row already on the card, or on an old Credit
    row holding its payback, it lets through as changing nothing.  The
    popover's template and the cell's each spelled this predicate until this
    task; both now draw the route's answer.

    Args:
        txn: The row.

    Returns:
        ``True`` when Credit is offered.
    """
    return txn.is_expense and is_projected(txn) and not txn.tracks_purchases


def credit_opens_the_popover_by_key(
    items: Iterable[PlanItem], cash_flow: CashFlowSet | None,
) -> dict[Hashable, bool | None]:
    """Return what the C key and the palette's Credit do on each of *items*, by cell key.

    **The one answer to "press at once, or open the popover"** (plan task
    plan#3114, ruling plan#3112, Josh 2026-11-09: "Open the popover").  A
    press from the C key or the palette posts no card, no day and no bank
    line, so the door reads the row's own day and its one card
    (:func:`_the_card`), and a press naming no line may free none (ruling
    **R-CC127**).  They open the popover where the press needs what only the
    popover asks: with no card it asks for one to be added, with two or more
    it asks which (:func:`credit_cards`), and where Credit would unmatch a
    bank line it names the line beside Credit first
    (:func:`credit_frees_a_line`).  On every other row they press at once,
    as the ruling says, a $0.00 row among them: the door refuses that press
    in words (:func:`mark_as_credit`), the sentence the popover prints beside
    its disabled Credit.

    **Both surfaces that draw the grid's cell publish this map** (the grid
    page and ``routes/_render_helpers.render_transaction_cell``), so a cell
    stamps the same answer on the page and in the fragment a click swaps in;
    the cell INDEXES it, so a surface that forgets to publish it raises rather
    than stamping no Credit (the rule ``due_captions`` states).

    **The matches are read once for the page**: only a row with one card
    whose press would move the payment it keeps onto that card
    (:func:`credit_moves_payment`: Paid, set back to Projected, its payment
    still on another account) needs them, and those rows are asked together
    (``status_seam.pending_payment_withdrawals``).  A page with none reads no
    match.

    Args:
        items: The items being drawn, rows and transfer sides, a row's
            ``entries`` loaded or loadable.
        cash_flow: The row OWNER's cash-flow set with no override, as
            :func:`credit_cards` takes it.

    Returns:
        One entry per item, keyed by ``transfer_legs.cell_key``: ``None``
        where Credit is not offered (a transfer side, whose status machine has
        no Credit, or :func:`credit_offered` false); ``True`` where the C key
        and the palette open the popover; ``False`` where they put the row on
        the card at once.
    """
    answers: dict[Hashable, bool | None] = {}
    moving: dict[Hashable, tuple[Transaction, tuple[Account, ...]]] = {}
    for item in items:
        key = cell_key(item)
        if isinstance(item, TransferLeg) or not credit_offered(item):
            answers[key] = None
            continue
        cards = credit_cards(item, cash_flow)
        if len(cards) != 1:
            answers[key] = True
        elif credit_moves_payment(item, cards):
            moving[key] = (item, cards)
        else:
            answers[key] = False
    withdrawals = pending_payment_withdrawals(
        {key: row for key, (row, _cards) in moving.items()},
    )
    for key, (row, cards) in moving.items():
        answers[key] = credit_frees_a_line(row, cards, withdrawals[key])
    return answers
