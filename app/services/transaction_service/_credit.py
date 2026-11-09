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

**Old Credit rows keep the old path** until plan#1034's re-plan: a row in
status Credit holds its live CC Payback, and Undo CC on it still deletes that
payback (``credit_workflow.unmark_credit``).  A press on one changes nothing.

Architecture: no Flask imports; reads the row, the owner's cash-flow set and
the pay calendar; mutates through the settle verb only; does not commit.
"""

import logging
from datetime import date

from app.enums import SettledDayBasisEnum
from app.exceptions import NotFoundError, ValidationError
from app.models.account import Account
from app.models.transaction import Transaction
from app.services.account_projection import is_revolving
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.cash_flow_set import purchase_accounts
from app.services.match_press import Press
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.settle_day import SettleDay
from app.services.status_seam import covering_movement_of, settled_on_a_card
from app.services.transaction_service._settle import settle_transaction
from app.utils.balance_predicates import is_credit, is_projected
from app.utils.dates import display_today
from app.utils.log_events import BUSINESS, EVT_CREDIT_MARKED, log_event

logger = logging.getLogger(__name__)


def mark_as_credit(
    txn: Transaction, *, card_account_id: int | None = None,
    press: Press | None = None,
) -> None:
    """Put *txn* on a credit card: settle it with its money moved on the card.

    The row settles through the ONE settle verb with the card as its tender,
    on the day :func:`credit_day` gives it, so it records what a Paid press
    with "Paid from: the card" records and nothing else: one covering
    movement on the card carrying the row's figure, and no CC Payback row.

    Refused, before anything is written: an income row (nothing is bought
    with a paycheck), a row whose purchases are tracked (its purchases each
    name their own account, so Credit is per purchase), and a row that is
    not Projected (a stale press on a row another tab settled or cancelled).
    A row already on a card, or an old Credit row, is a press already made,
    so a double click changes nothing.  The settle verb adds its own
    refusals: a deleted row, a day before the card's books open (S11 of the
    trace), a figure it cannot price.

    Args:
        txn: The row pressed, already proven the caller's by the door.  A
            companion's press never reaches here: the door is the owner's
            (review J).
        card_account_id: The card the owner named, or ``None`` when the press
            named none (:func:`_the_card`).
        press: The save's :class:`~app.services.match_press.Press`, for the
            settle's removal act: putting a reverted row's kept payment on
            the card takes it out of any match naming it on its old account.

    Raises:
        ValidationError: On the refusals above, or when no card or more than
            one could be the one meant (:func:`_the_card`).
        NotFoundError: When *card_account_id* is not one of the owner's
            active cards (one answer for "not found" and "not yours").
    """
    if txn.is_income:
        raise ValidationError("Cannot mark income as credit.")
    if txn.tracks_purchases:
        raise ValidationError(
            "This transaction uses individual purchase tracking. "
            "Mark individual entries as credit instead of the whole transaction."
        )
    if is_credit(txn) or settled_on_a_card(txn):
        return
    if not is_projected(txn):
        raise ValidationError(
            f"Cannot mark a '{txn.status.name}' transaction as credit. "
            "Only projected transactions can be marked as credit."
        )
    card = _the_card(txn, card_account_id)
    settle_transaction(
        txn,
        settle_day=SettleDay(day=credit_day(txn), basis=SettledDayBasisEnum.ENTERED),
        tender_account_id=card.id,
        press=press,
    )
    log_event(
        logger, logging.INFO, EVT_CREDIT_MARKED, BUSINESS,
        "Transaction put on a credit card",
        user_id=txn.user_id,
        transaction_id=txn.id,
        card_account_id=card.id,
        settled_on=txn.settled_on.isoformat(),
        amount=str(covering_movement_of(txn).amount),
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

    The choices are the cards among the accounts the row's payment may move
    through (``cash_flow_set.purchase_accounts``, the settle verb's own gate:
    the row's own account, then the owner's active cards), so the choice and
    the gate cannot part.  A row that lives on a card goes on that card.
    Otherwise the owner's one card; with none there is nothing to put it on,
    and with two or more the press must say which.

    Args:
        txn: The row pressed.
        card_account_id: The card the owner named, or ``None``.

    Returns:
        The card's :class:`~app.models.account.Account`.

    Raises:
        NotFoundError: *card_account_id* names no card among the choices: not
            the owner's, archived, or not a card.  One answer, because a
            press offers only the choices, so anything else was crafted.
        ValidationError: No card was named and none, or more than one, could
            be the one meant.
    """
    cards = [
        account
        for account in purchase_accounts(resolve_owner_cash_flow_set(txn.user_id), txn)
        if is_revolving(account)
    ]
    if card_account_id is not None:
        named = next((card for card in cards if card.id == card_account_id), None)
        if named is None:
            raise NotFoundError("Card not found.")
        return named
    if is_revolving(txn.account) or len(cards) == 1:
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
