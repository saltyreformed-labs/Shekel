"""
Shekel Budget App -- the ONE gate on which account a movement under a row may name.

A movement's ``account_id`` is the account its money moved through (design
``docs/design/credit_card_from_scratch.md`` 3.2, rulings **R-CC15** and
**R-BAL75**), and three writers put one onto an account other than its
row's: the purchase door (``entry_service._doors.create_entry``, plan step
``credit_card:CC-5-2``), the settle verb
(``transaction_service._settle.settle_transaction``, plan step ``CC-5-3``,
where the covering movement takes the TENDER the owner names) and the
settled row's "Paid from" correction
(``transaction_service._door._correction_for_status``, the identity arm
that re-points the kept movement).  All three admit exactly one set of
accounts, and it is spelled ONCE here so they cannot part: a door written
against another door's body inherits its write and none of its refusals
([[feedback_a_copied_write_inherits_no_refusal]]).
The gate lived inside the purchase door through ``CC-5-2``; ``CC-5-3`` moved
the leaf rather than copying it (rule 14: where a layer puts the shared leaf
out of reach, move the leaf).

**What a movement may name is the picker's predicate** (rulings **R-CC37**
and **R-CC39**): the row's own account, or a member of the ROW OWNER's
cash-flow set -- the primary grid account plus the active cards
(:func:`~app.services.account_resolver.resolve_owner_cash_flow_set`, no
override), the tuple :func:`~app.services.cash_flow_set.purchase_accounts`
renders every picker from.  So a swipe or a payment on a 401(k), an IRA, a
house, a loan or a savings account is UNWRITABLE rather than merely
unoffered, because a movement filed on an account whose balance never folds
movements would drop the row's hold on checking by its figure and land where
no screen reads it; a row that LIVES on such an account keeps its movements
there, its own account being the tuple's first member.

**The gate is against the ROW's owner, never the caller** (design 3.2,
ruling **R-CC11**): a companion reaches the row through the owner's
accessible-transaction path and owns no account at all, so a gate on the
caller's ``user_id`` would refuse every companion swipe and every companion
Mark Paid.  ``txn.user_id`` is the owner, and
``fk_transaction_entries_owner_account`` holds the written movement to the
same fact -- this gate is what turns the key's ``IntegrityError`` into the
security response rule's 404, one answer for "no such account" and "not the
owner's" alike, raised before the account's name is read.

The two refusals below the 404 are the predicate's consequences, each given
the sentence that names its remedy: an ARCHIVED card is not an active member
(the row doors admit an archived account, a pre-existing admission ruling
**R-CC31**'s review named and a new door does not copy); an account that is
neither the row's nor a member -- a loan, a 401(k), savings -- is not one
money can be recorded as moving through.

**Which card "on the card" means is answered here too** (plan task plan#2930,
ruling plan#2888): the row's Credit press and the purchase list's CC tick say
the same thing, that this money went on a card, and neither names an account
unless the owner has two or more cards to pick from.  The tick on a RECORDED
purchase moves it (plan task plan#3142): onto the card, back to its row's
account, or between cards, a fourth writer of a movement onto another
account than its row's, through :func:`re_point_movement`.  :func:`credit_cards` is
the cards either could mean and :func:`the_card` the one it does, each moved
here from ``transaction_service._credit`` when the purchase doors became the
second caller: the entry doors cannot reach that package's private module,
and two spellings of "the owner's one card" would part the first time one of
them learned an exception (rule 14: move the leaf).  :func:`tick_cards` is
what the purchase list draws its tick from, beside the "Paid from" accounts
(:func:`purchase_choices`).

The books boundary on the card's OPENING is not asked by the gate, and by
construction: the day a movement takes is written by
``settle_day.record_settle_day``, which reads the MOVEMENT's ``account_id``
-- so a movement dated on or before its account's opening is refused there,
whatever account its row names.  A movement MOVED to another account keeping
its day writes none, so :func:`re_point_movement` asks it itself.

**And the one write of a movement's account is here** (plan task plan#3142):
:func:`re_point_movement`, which a settled row's payment takes when its tender
changes (``status_seam._covering._re_point``) and a recorded purchase takes
when its CC tick changes (``entry_service._doors.update_entry``).  It was the
body of ``_re_point`` until the purchase door became its second caller; the
entry doors cannot reach that package's private module, and a second spelling
of "out of its matches, unlinked, on the new account" would part the first
time one of them learned a consequence the other did not (rule 14: move the
leaf).  :func:`purchase_card` is what the tick reads a purchase as.

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
the gate and the card answers read the named account and the owner's set and
write nothing; :func:`re_point_movement` mutates the movement in place, takes
it out of its matches (which flushes) and never commits.
"""

from dataclasses import dataclass
from datetime import date

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import match_withdrawal
from app.services.account_projection import is_revolving
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.cash_flow_set import CashFlowSet, purchase_accounts
from app.services.cash_ledger import reject_movement_before_books_open
from app.services.match_press import Press


def admitted_movement_account_id(
    txn: Transaction, account_id: int, *, movement: str,
) -> int:
    """Return *account_id* once it is one a movement under *txn* may name.

    Args:
        txn: The parent row, already proven the caller's.
        account_id: The account the door was asked to write the movement on.
        movement: What the door calls the movement in its refusal --
            ``"purchase"`` at the purchase door, ``"payment"`` at the settle
            verb -- so the sentence names the act the owner performed.

    Returns:
        The ``budget.accounts.id`` to write onto the movement: *account_id*.

    Raises:
        NotFoundError: The account does not exist or is not the ROW's owner's.
        ValidationError: The account is archived, or is neither the row's own
            nor a member of the owner's cash-flow set.
    """
    if account_id == txn.account_id:
        return txn.account_id
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != txn.user_id:
        raise NotFoundError("Account not found.")
    if not account.is_active:
        raise ValidationError(
            f"'{account.name}' is archived, so a {movement} cannot be filed "
            "on it. Unarchive the account first, or pick another."
        )
    cash_flow = resolve_owner_cash_flow_set(txn.user_id)
    if account.id not in {a.id for a in purchase_accounts(cash_flow, txn)}:
        raise ValidationError(
            f"'{account.name}' is not an account a {movement} can be paid "
            "from: pick this row's own account, your checking account, or "
            "one of your credit cards."
        )
    return account.id


def re_point_movement(
    movement: TransactionEntry, account_id: int, *,
    day: date | None, press: Press | None,
) -> bool:
    """Move *movement* onto *account_id* if it is not there; say whether it moved.

    **The one write of a movement's account** (plan task plan#3142; module
    docstring), for its two callers: a settled row's payment when its tender
    changes (``status_seam._covering._re_point``, plan step
    ``credit_card:CC-5-3``: a re-settle naming another account, a "Paid
    from" correction, Credit, Undo CC) and a recorded purchase when its CC
    tick changes (``entry_service._doors.update_entry``).  Four
    consequences, in this order:

    * **the day the movement will END with is graded against the new
      account's books boundary FIRST** (``cash_ledger.
      reject_movement_before_books_open``, the producer
      ``settle_day.record_settle_day`` asks), so a refused move writes
      nothing here.  The caller names the day, because each knows what the
      act leaves: the row's day, which the seam wrote before it reached the
      payment and mirrors onto it next (a settle-day move and a tender arrive
      in ONE popover Save, so the movement's old day is not what it ends up
      dated); a purchase's posting day as the same save leaves it, which the
      move voids unless the owner typed it (``_doors``).  ``None`` asks
      nothing: an un-dated movement holds no day for the books to refuse;
    * **every MATCH naming the movement is withdrawn** (plan step
      ``credit_card:CC-5-4a-1``, ruling **R-CC46**, developer 2026-09-21): a
      match asserts that a named account's bank line IS this movement, and
      money that moved on another account was shown by no such line.  Every
      accepted match names the movement (ruling **R-CC43**) through a member
      the key ``fk_statement_match_members_entry_account`` holds to the
      movement's account, so the assertion is both false and unstorable once
      the account changes; ``match_withdrawal.withdraw_for_moved_movement``
      takes the member as a delete would, withdraws an act left naming no app
      row, and FLUSHES before the assignment below -- the unit of work orders
      a DELETE after an UPDATE, and the key is checked at the flush.  Under
      *press*: the screen that offers the move names those lines first
      (``match_withdrawal.pending_for_movements``: the popover's "Paid from"
      and Credit captions, the purchase list's tick caption) and posts them
      back, and a line it did not name refuses the save (ruling **R-CC127**);
    * **the movement's clearing link is RELEASED**: a link records that a
      named statement of a named account showed this money, and money that
      moved on another account was shown by no such statement (the rule a
      settle-day move applies, ruling **R-FL**).  The storage tier would
      refuse it anyway (``fk_transaction_entries_reconciled_by`` holds the
      link to the movement's account).  A caller whose ROW carries a link of
      its own releases that itself (``_re_point``): this writes the movement;
    * the account is assigned.

    Args:
        movement: The movement, already proven the caller's by its door.
        account_id: The account it moves to, already gated
            (:func:`admitted_movement_account_id`, :func:`the_card`, or the
            row's own).
        day: The day the movement ends the act with, or ``None``.
        press: The save's press, or ``None`` when its door named nothing
            (``match_withdrawal.take_out_of_matches``).

    Returns:
        Whether the movement's account changed.

    Raises:
        ValidationError: When *day* is on or before the new account's opening,
            or a companion's press would free a line (ruling **R-CC130**).
        PageOutOfDate: When the matches the move withdraws free other lines
            than *press*'s page named (ruling **R-CC127**).
    """
    if movement.account_id == account_id:
        return False
    if day is not None:
        reject_movement_before_books_open(account_id, day)
    match_withdrawal.withdraw_for_moved_movement(
        movement, movement.owner_id, press=press,
    )
    movement.reconciled_by_id = None
    movement.account_id = account_id
    return True


def credit_cards(txn: Transaction, cash_flow: CashFlowSet | None) -> tuple[Account, ...]:
    """Return the cards a Credit press or a CC tick naming none could put *txn*'s money on.

    **The one answer to "which card does 'on the card' mean"** (plan tasks
    plan#3056 and plan#2930): the Credit press asks it when nothing is named
    (:func:`the_card`), and so does a purchase ticked CC under *txn*; the
    popover draws from it (one card named, two or more to pick from, none and
    an ask to add one), the C key and the palette press at once only when it
    holds one (plan task plan#3114), and the purchase list's tick is drawn
    from it (:func:`tick_cards`).  The cards among the accounts the row's
    money may move through (``cash_flow_set.purchase_accounts``, the set
    :func:`admitted_movement_account_id` admits: the row's own account first,
    then the owner's other members).  A row that lives on a card means that
    card, however many the owner has (decided under R-BAL207 at plan task
    plan#2929: a bill on the card says "this went on this card", ruling
    plan#2926, and asking which would ask what the row already says).

    Args:
        txn: The row, its ``account`` loaded (a joined load).
        cash_flow: The row OWNER's cash-flow set with no override
            (``account_resolver.resolve_owner_cash_flow_set``), or ``None``
            for an owner with no grid-eligible account.

    Returns:
        The row's own card alone when it lives on one; else the owner's cards
        in member order, which may be none.
    """
    return _cards_among(txn, purchase_accounts(cash_flow, txn))


def _cards_among(txn: Transaction, accounts: tuple[Account, ...]) -> tuple[Account, ...]:
    """Return :func:`credit_cards`' answer over *txn*'s ``purchase_accounts`` tuple, already read.

    The one body for :func:`credit_cards`, which reads the tuple, and
    :func:`purchase_choices`, which has read it for "Paid from" already, so
    one render reads it once per row (the review of plan#2930, L8).
    """
    cards = tuple(account for account in accounts if is_revolving(account))
    if is_revolving(txn.account):
        return cards[:1]
    return cards


def tick_cards(txn: Transaction, accounts: tuple[Account, ...]) -> tuple[Account, ...]:
    """Return the cards the CC tick on a purchase under *txn* offers; none where it moves nothing.

    The purchase list draws its tick from this (plan task plan#2930, ruling
    plan#2888: "the tick stays the one control, with a pick only for two or
    more cards"): no tick where it is empty, the tick alone for one card, and
    the tick with a pick for two or more.  :func:`credit_cards` less one
    case: a row that lives on a card files every purchase on that card
    already, so a tick there could only name the account the purchase is on,
    and a control that can change nothing is not drawn (ruling **R-CC34**'s
    reason for the "Paid from" picker).  With no card the tick is not drawn
    either: the door would refuse it in words (:func:`the_card`), after the
    owner had typed the purchase.

    Args:
        txn: The purchase's row, its ``account`` loaded.
        accounts: The row's ``cash_flow_set.purchase_accounts`` over the
            OWNER's cash-flow set, as :func:`purchase_choices` reads it.

    Returns:
        The cards, in member order, or ``()``.
    """
    if is_revolving(txn.account):
        return ()
    return _cards_among(txn, accounts)


def purchase_card(entry: TransactionEntry) -> Account | None:
    """Return the card a recorded purchase is ON, or ``None``: what its CC tick reads.

    The one answer to "is this purchase on a card" for the edit form's tick
    (plan task plan#3142): ticked, and its "Which card" pick naming this
    card, when the purchase's money moved on a card other than the account
    its row lives on -- a swipe ticked on, or picked under "Paid from" on the
    add form.  The edit door reads it too, so an untouched Save posts back
    what this said and moves nothing.  A purchase on the row's own account is
    on no card here even where that account IS a card: a row that lives on a
    card files every purchase there, and the tick is not drawn on it
    (:func:`tick_cards`).  An archived card is still the card the purchase
    is on, though no tick offers it.  The old CC flag is not read: it is the
    flagged line's own state, which the door and the form read beside this.

    Args:
        entry: The purchase, its ``transaction`` and ``account`` loadable.

    Returns:
        The card's :class:`~app.models.account.Account`, or ``None``.
    """
    if entry.account_id == entry.transaction.account_id:
        return None
    return entry.account if is_revolving(entry.account) else None


def the_card(
    txn: Transaction, card_account_id: int | None, *, what: str, again: str,
) -> Account:
    """Return the card a Credit press on *txn*, or a CC tick on a purchase under it, puts money on.

    A NAMED card is gated as "Paid from" gates one, by
    :func:`admitted_movement_account_id` (another owner's account is not
    found, an archived one is refused in words, one outside the owner's
    cash-flow set is refused), and must then be a card.  With none named,
    the choices are :func:`credit_cards`: the one card when there is one;
    with none there is nothing to put it on, and with two or more the owner
    must say which.  Moved here from ``transaction_service._credit`` at plan
    task plan#2930, when the purchase doors became its second caller.

    Args:
        txn: The row pressed, or the purchase's row.
        card_account_id: The card the owner named, or ``None``.
        what: What goes on the card, for the refusals' sentences: the row's
            name for a press, the purchase's description for a tick.
        again: The act the owner repeats once a card is added, ending the
            no-card sentence: ``"press Credit"`` or ``"tick CC"``.

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
            Account,
            admitted_movement_account_id(txn, card_account_id, movement="purchase"),
        )
        if not is_revolving(account):
            raise ValidationError(
                f"'{account.name}' is not a credit card, so {what} cannot go "
                "on it: pick one of your cards."
            )
        return account
    cards = credit_cards(txn, resolve_owner_cash_flow_set(txn.user_id))
    if len(cards) == 1:
        return cards[0]
    if not cards:
        raise ValidationError(
            f"{what} cannot go on a credit card: there is no credit card in "
            f"the app yet. Add your card under Accounts, then {again} again."
        )
    raise ValidationError(
        f"You have {len(cards)} credit cards: pick the one {what} went on."
    )


@dataclass(frozen=True)
class PurchaseChoices:
    """What a purchase list's forms offer for a purchase under one row.

    One value for its two readers (the grid's every envelope and the purchase
    list's fragment, through ``entry_service.entry_list_view``), so neither
    can draw the tick from one set and "Paid from" from another.

    Attributes:
        accounts: The accounts "Paid from" offers, the row's own first
            (``cash_flow_set.purchase_accounts``, the set
            :func:`admitted_movement_account_id` admits); the picker is drawn
            only when there are two or more (ruling **R-CC34**).
        cards: The cards the CC tick offers (:func:`tick_cards`): no tick
            when empty, a pick beside it when two or more.
    """

    accounts: tuple[Account, ...]
    cards: tuple[Account, ...]


def purchase_choices(txn: Transaction, cash_flow: CashFlowSet | None) -> PurchaseChoices:
    """Return what the purchase forms under *txn* offer, from the OWNER's cash-flow set.

    Args:
        txn: The purchase's row, its ``account`` loaded.
        cash_flow: The row OWNER's cash-flow set with no override, or
            ``None`` (:func:`credit_cards`' argument).

    Returns:
        The :class:`PurchaseChoices`.
    """
    accounts = purchase_accounts(cash_flow, txn)
    return PurchaseChoices(accounts=accounts, cards=tick_cards(txn, accounts))
