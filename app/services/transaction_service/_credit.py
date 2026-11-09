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
:func:`credit_cards` for the choice and :func:`credit_moves_payment` for the
caption naming the bank lines the press frees, the one producer the C key and
the palette ask too (plan task plan#3114).

**Old Credit rows keep the old path** until plan#1034's re-plan: a row in
status Credit holds its live CC Payback, and Undo CC on it still deletes that
payback (``credit_workflow.unmark_credit``).  A press on one changes nothing.

Architecture: no Flask imports; reads the row, the owner's cash-flow set and
the pay calendar; mutates through the settle verb and the status door only;
does not commit.
"""

import logging
from datetime import date

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
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.settle_day import SettleDay
from app.services.status_seam import covering_movement_of, settled_on_a_card
from app.services.transaction_service._door import apply_requested_status
from app.services.transaction_service._settle import settle_transaction
from app.utils.balance_predicates import is_credit, is_projected
from app.utils.dates import display_today
from app.utils.log_events import BUSINESS, EVT_CREDIT_MARKED, log_event

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
    The settle verb adds its own refusals: a deleted row, a day that has not
    come (ruling **R-EJ**) or is before the owner's schedule (ruling
    **R-EL**), a day on or before the card's books open (S11 of the trace), a
    figure it cannot price.  A row whose settle records $0.00 is refused
    after it, because nothing moved on the card, so the row would be Paid
    and drawn so rather than Credit; the door's rollback undoes the settle.

    Args:
        txn: The row pressed, already proven the caller's by the door.  A
            companion's press never reaches here: the door is the owner's
            (review J).
        card_account_id: The card the owner named, or ``None`` when the press
            named none (:func:`_the_card`).
        day: The day the owner typed under "Money moved on" (plan task
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
    settle_transaction(
        txn,
        settle_day=SettleDay(
            day=credit_day(txn) if day is None else day,
            basis=SettledDayBasisEnum.ENTERED,
        ),
        tender_account_id=card.id,
        press=press,
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


def credit_day(txn: Transaction) -> date:
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
    request's owner lock (plan step ``balance:X-bn``).

    Args:
        txn: The row about to be put on the card.

    Returns:
        The civil day.
    """
    own = txn.due_date
    if own is None:
        own = calendar_for(txn.user_id).require_period(FiledRow.for_row(txn)).start_date
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
    """Return whether a Credit press onto one of *cards* moves *txn*'s kept payment.

    A row Paid, then set back to Projected, keeps its payment un-dated
    (ruling **R-BAL61**), and an accepted match may still name it.  Credit
    settles with the card as the tender, and the seam moves a kept payment
    that is on another account onto it, which takes it out of every match
    naming it (``status_seam._covering._re_point``, ruling **R-CC46**).  So
    this is the question a caption naming those lines asks (plan task
    plan#3056) and the one that sends the C key and the palette to the
    popover instead (ruling plan#3112, plan task plan#3114); what the move
    would free is ``match_withdrawal.pending_for_movements``' answer.

    Args:
        txn: The row, with ``entries`` loaded or loadable.
        cards: What :func:`credit_cards` answered for it.

    Returns:
        ``True`` when the row keeps a payment on an account none of *cards* is.

    Raises:
        ValueError: From ``status_seam.covering_movement_of``, on a row
            holding two covering movements.
    """
    movement = covering_movement_of(txn)
    return movement is not None and movement.account_id not in {
        card.id for card in cards
    }
