"""
Shekel Budget App -- Transaction Service: the day money put on a CARD is dated

**One function says the day, and Credit and Paid both read it** (plan task
plan#3234, ruling plan#3225).  On a card the date decides the statement, the
due date and the interest (ruling plan#2910), and a dated bill on the card
counts from its date, marked or not (ruling plan#2926), so marking it must
not move it.  :func:`credit_day` is the day: the row's own date, or today
while that date is still ahead.  A row with no date takes its paycheck's
first day, where the card's fold lands it by ruling R-G until it is marked,
so marking an undated row does move it (plan task plan#3453 traces that
split).  Credit puts a row on the card on it
(``._credit.mark_as_credit``), and a settle naming no day whose payment lands
on a card records it too (:func:`settle_day_on_a_card`, asked by the settle
verb, ``._settle._settle``), so Paid on a bill that lives on the card, with
its card picked, dates it as Credit would.  The example: an $800 bill dated
Jan 3 on a Visa closing the 5th, pressed Paid on Jan 6, is on the statement
due Jan 27, where today's date put it on the one due Feb 27.

It is a leaf of its own because both of its readers need it and one imports
the other: ``._credit`` enters the settle verb (``._settle.settle_onto_card``),
so the verb cannot reach a day that lived in ``._credit`` (rule 14: where a
layer puts the shared leaf out of reach, move the leaf).

Architecture: no Flask imports; reads the row, the account its payment lands
on and, for a row with no date, the owner's pay calendar; writes nothing.
"""

from __future__ import annotations

from datetime import date

from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services.account_projection import is_revolving
from app.services.pay_calendar import FiledRow, PayCalendar, calendar_for
from app.services.settle_day import SettleDay
from app.services.status_seam import booked_tender_id
from app.utils.dates import display_today


def credit_day(txn: Transaction, calendar: PayCalendar | None = None) -> date:
    """Return the day money *txn* puts on a card is dated: by Credit, or Paid onto the card.

    The row's own date, because the date decides the statement, the due date
    and the interest (ruling plan#2910), and the owner presses Credit after
    the purchase: 20 of the 21 Credit rows on the 2026-11-04 production dump
    were pressed 4 to 22 days after their date.  A date that has not come is
    today, because a settle day cannot be in the future (ruling R-EJ; S10 of
    the trace).  A row with no date reads its paycheck's first day, today if
    that has not come either: it has no day of its own, and its paycheck's
    first day is where its money is budgeted.  A settle whose payment lands
    on a card reads it too (:func:`settle_day_on_a_card`, ruling plan#3225).

    The paycheck is read off the owner's one calendar after the row (the read
    order ``require_period`` asks its callers to state); no destructive pay
    period door can commit between the two reads, both following the
    request's owner lock (plan step ``balance:X-bn``).  A caller that has
    already derived it passes it in (the popover, which shows this day in its
    "Money moved on" box, plan task plan#3056), so one request derives it
    once.

    Args:
        txn: The row whose money is about to go on the card.
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


def credit_settle_day(txn: Transaction) -> SettleDay:
    """Return the :class:`~app.services.settle_day.SettleDay` money *txn* puts on a card records.

    :func:`credit_day` on the ``entered`` basis: the press is the owner's
    statement that the money went on the card.  The one producer of the pair
    for its two readers, a Credit press naming no day
    (``._credit.mark_as_credit``) and a settle naming none whose payment
    lands on a card (:func:`settle_day_on_a_card`).

    Args:
        txn: The row whose money is about to go on the card.

    Returns:
        The day and its basis.
    """
    return SettleDay(day=credit_day(txn), basis=SettledDayBasisEnum.ENTERED)


def settle_day_on_a_card(txn: Transaction, tender: int | None) -> SettleDay | None:
    """Return the day a settle naming none records where *txn*'s payment lands on a card.

    **Paid on a bill that lives on the card dates it like Credit** (plan task
    plan#3234, ruling plan#3225): with its card picked, or picked by default,
    the payment is a charge on the card, so it takes :func:`credit_settle_day`.
    Every Paid door reaches the settle verb naming no day -- the popover's
    Paid, the cell's check (which the keyboard and the palette click), the
    phone's Mark Paid, the Status dropdown, the companion page -- so the verb
    asks this once for all of them; the doors that state a day of their own
    (the reconcile tick, the statement matcher) never reach it.  With a cash
    account picked the payment leaves that account, and the seam's own rule
    stands (``status_seam.apply_status_change``: the owner's today), as for
    any row paid from checking.

    **Not an envelope's** (the review of plan#3234): a row tracking its
    purchases settles here only while it holds none, booking its unspent
    budget, which has no date of its own on a card (``balance_at._cash_plan.
    _own_date`` answers ``None`` and the fold lands it by ruling R-G).  Dated
    by its due date, a $200.00 envelope dated Jan 2 on a Visa trued up to
    $0.00 on Jan 2 would be absorbed into that true-up and vanish from the
    card; it keeps the seam's today.  Credit refuses such a row outright.

    **Money coming in takes it too**, decided under R-BAL207 (an edge of a
    path not yet released): a refund living on the card counts there from
    its date whatever its kind (``balance_at._cash_plan``), so a mark dated
    today would move a $25.00 refund dated Jan 3 off the Jan 5 statement as
    it moved the bill; one planned on checking and Received into the card
    (ruling plan#3310) takes the same day, as Credit dates a bill it moves
    onto the card.

    The account the payment lands on is the one the seam books it on
    (``status_seam.booked_tender_id``: the tender the door named, already
    gated, else the kept payment's account, else the row's own).  The day is
    bounded as Credit's is: not in the future (:func:`credit_day`), and not
    on or before the card's books open (``settle_day.record_settle_day``,
    which refuses it in words: such a bill is inside the card's opening
    balance already).

    Args:
        txn: The row about to settle, entering the settled band, whose figure
            is its own (not its purchases', whose record writes no payment).
        tender: The account the door named and the verb admitted, or ``None``
            for the seam's default.

    Returns:
        The :class:`~app.services.settle_day.SettleDay` when the payment
        lands on a credit card and the row is not an envelope; ``None``
        otherwise, which leaves the seam's rule in force.

    Raises:
        ValueError: From ``status_seam.booked_tender_id``, on a row holding
            two covering movements.
    """
    if txn.tracks_purchases:
        return None
    if not is_revolving(db.session.get(Account, booked_tender_id(txn, tender))):
        return None
    return credit_settle_day(txn)
