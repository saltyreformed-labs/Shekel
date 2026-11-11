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
accounts, the "Paid from" list (:func:`paid_from_accounts`), and it is
spelled ONCE here so they cannot part: a door written against another door's
body inherits its write and none of its refusals
([[feedback_a_copied_write_inherits_no_refusal]]).
The gate lived inside the purchase door through ``CC-5-2``; ``CC-5-3`` moved
the leaf rather than copying it (rule 14: where a layer puts the shared leaf
out of reach, move the leaf).

**What "Paid from" may name is the picker's predicate** (ruling plan#3227,
developer 2026-11-10, which replaces rulings **R-CC37** and **R-CC39**): the
row's own account, then every active account of the ROW OWNER's that holds
cash -- the types ``ref.account_types.is_liquid`` marks (Checking, Savings,
HYSA, Money Market) -- and never a card other than the row's own
(:func:`paid_from_accounts`, the one tuple the popover's box and the
add-purchase form's render).  So a payment or a purchase "Paid from" a
401(k), an IRA, a house or a loan is UNWRITABLE rather than merely unoffered,
because a movement filed on an account whose balance never folds movements
would drop the row's hold on checking by its figure and land where no screen
reads it; a row that LIVES on such an account keeps its movements there, its
own account being the tuple's first member.  Every account the list adds
folds its movements on its own account (ruling **R-BAL75**): measured for
Savings and an HYSA at plan task plan#3232
(``test_a_payment_from_cash_counts_once``).  The list was the row's own
account plus the owner's cash-flow set (checking and its cards) until that
task.

**On money coming in the box is "Received into", and it lists the cards too**
(ruling plan#3310, developer 2026-11-10, answer A): after the cash, every
active card of the owner's, because Credit refuses money coming in
(``transaction_service._credit``) and the box is then the only way to say a
refund landed on a card.  Checking $1,000.00, the Visa owing $800.00, and a
$25.00 Visa refund planned on Checking, marked Received into the Visa: the
Visa owes $775.00 and Checking stays $1,000.00
(``test_a_refund_received_into_the_card``).  A bill's "Paid from" stays cash
only.

**The gate is against the ROW's owner, never the caller** (design 3.2,
ruling **R-CC11**): a companion reaches the row through the owner's
accessible-transaction path and owns no account at all, so a gate on the
caller's ``user_id`` would refuse every companion swipe and every companion
Mark Paid.  ``txn.user_id`` is the owner, and
``fk_transaction_entries_owner_account`` holds the written movement to the
same fact -- this gate is what turns the key's ``IntegrityError`` into the
security response rule's 404, one answer for "no such account" and "not the
owner's" alike, raised before the account's name is read.

The refusals below the 404 are the predicate's consequences, each given the
sentence that names its remedy: an ARCHIVED account is not an active one (the
row doors admit an archived account, a pre-existing admission ruling
**R-CC31**'s review named and a new door does not copy); a CARD is named by
Credit or the CC tick, not "Paid from" (ruling plan#3225: Paid means the bill
was paid, Credit that it went on a card), so a hand-built post naming one is
refused rather than settled on the card; an account that is neither the
row's nor one holding cash -- a loan, a 401(k) -- is not one money can be
recorded as paid from, nor, on money coming in, received into unless it is a
card.

**The two acts that put money on a card gate their card themselves**
(:func:`the_card`, plan task plan#3232): Credit settles through the same
verb as Paid (``transaction_service._settle``), so the card refusal cannot
sit in that verb for every caller; the verb forks at its tender gate, Paid's
entry asking this gate and Credit's (``settle_onto_card``) taking the card
:func:`the_card` answered.  :class:`OwnerAccounts` holds both halves, read
once per request: the cash "Paid from" lists and the cards Credit and the
tick offer.

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
leaf).  :func:`purchase_card` is what the tick reads a purchase as, and
:func:`on_a_card` what every screen marks CC (plan task plan#3143).

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
the gate and the card answers read the named account and the owner's accounts
and write nothing; :func:`re_point_movement` mutates the movement in place, takes
it out of its matches (which flushes) and never commits.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import contains_eager

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import account_service, match_withdrawal
from app.services.account_projection import is_revolving
from app.services.cash_ledger import reject_movement_before_books_open
from app.services.match_press import Press


@dataclass(frozen=True)
class OwnerAccounts:
    """The accounts a movement under one owner's rows may name, by the act that names them.

    Read once per request (:func:`owner_accounts`) for every row the page
    draws, so the "Paid from" boxes, the Credit press and the CC tick are
    drawn from one read of the owner's accounts, and the doors' gates read
    the same producer for the one row they write.

    Attributes:
        cash: The owner's ACTIVE accounts that hold cash, the types
            ``ref.account_types.is_liquid`` marks, in account order
            (``sort_order``, then ``id``): what "Paid from" lists after the
            row's own account (ruling plan#3227).  Never a card.
        cards: The owner's ACTIVE credit cards, in the same order: what
            Credit and the CC tick may put money on (:func:`credit_cards`),
            and what "Received into" lists after the cash on money coming in
            (ruling plan#3310).
    """

    cash: tuple[Account, ...]
    cards: tuple[Account, ...]


def owner_accounts(owner_id: int) -> OwnerAccounts:
    """Return the ROW OWNER's :class:`OwnerAccounts`, in one query.

    Resolved for the row's OWNER whoever is looking (ruling **R-CC11**): a
    companion records the owner's purchase on the owner's accounts.  A
    liquid type is an Asset's (the account-type form refuses one otherwise)
    and a revolving one a Liability's, so the two halves cannot share an
    account; the cash half is partitioned off the cards anyway, so "never a
    card" holds by construction rather than by the form.

    Args:
        owner_id: ``auth.users.id`` of the rows' owner.

    Returns:
        The owner's cash accounts and cards, each in account order; both
        empty for an owner with neither.
    """
    accounts = (
        account_service.active_accounts_query(owner_id, amortizing=False)
        .filter(or_(
            AccountType.is_liquid.is_(True),
            AccountType.has_revolving_credit.is_(True),
        ))
        .options(contains_eager(Account.account_type))
        .order_by(Account.sort_order, Account.id)
        .all()
    )
    return OwnerAccounts(
        cash=tuple(account for account in accounts if not is_revolving(account)),
        cards=tuple(account for account in accounts if is_revolving(account)),
    )


def paid_from_accounts(txn: Transaction, owner: OwnerAccounts) -> tuple[Account, ...]:
    """Return the accounts "Paid from" lists for *txn*, in picker order.

    **The ONE spelling of "which accounts may this row's money have moved
    through"** (ruling plan#3227, plan task plan#3232): the row's own account,
    then every account of the owner's that holds cash, never a card other
    than the row's own; and on money coming in, where the box reads
    "Received into", every other active card of the owner's after the cash
    (ruling plan#3310).  Read by the full-edit popover's box
    (``routes.transactions.forms._tender_picker``), the add-purchase form's
    (:func:`purchase_choices`), and the gate both doors ask
    (:func:`admitted_movement_account_id`), so the box and the gate cannot
    part.  The first member is the forms' default: a payment or a purchase
    naming no account is on its row's.  A purchase is never under money
    coming in (``entry_service._doors`` refuses one), so the add-purchase
    form's list is always the cash one.

    Args:
        txn: The row, its ``account`` a joined load, so this reads no query.
        owner: The row OWNER's :class:`OwnerAccounts`.

    Returns:
        The row's own account first, then the owner's cash accounts in account
        order, then, on money coming in, the owner's cards in account order;
        never empty.
    """
    others = owner.cash + owner.cards if txn.is_income else owner.cash
    return (
        txn.account,
        *[account for account in others if account.id != txn.account_id],
    )


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
        ValidationError: The account is archived, is a credit card other than
            the row's own under money going out, or is neither the row's own
            nor one :func:`paid_from_accounts` lists.
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
    if is_revolving(account) and not txn.is_income:
        raise ValidationError(
            f"'{account.name}' is a credit card, so a {movement} cannot be "
            "paid from it: Credit puts a bill on a card, and CC puts a "
            "purchase on one."
        )
    admitted = paid_from_accounts(txn, owner_accounts(txn.user_id))
    if account.id in {a.id for a in admitted}:
        return account.id
    if txn.is_income:
        raise ValidationError(
            f"'{account.name}' is not an account money can be received "
            "into: pick this row's own account, one that holds cash "
            "(checking, savings, an HYSA or a money market account), or a "
            "credit card."
        )
    raise ValidationError(
        f"'{account.name}' is not an account a {movement} can be paid "
        "from: pick this row's own account, or one that holds cash "
        "(checking, savings, an HYSA or a money market account)."
    )


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


def credit_cards(txn: Transaction, owner: OwnerAccounts) -> tuple[Account, ...]:
    """Return the cards a Credit press or a CC tick naming none could put *txn*'s money on.

    **The one answer to "which card does 'on the card' mean"** (plan tasks
    plan#3056 and plan#2930): the Credit press asks it when nothing is named
    (:func:`the_card`), and so does a purchase ticked CC under *txn*; the
    popover draws from it (one card named, two or more to pick from, none and
    an ask to add one), the C key and the palette press at once only when it
    holds one (plan task plan#3114), and the purchase list's tick is drawn
    from it (:func:`tick_cards`).  The owner's active cards
    (:attr:`OwnerAccounts.cards`), **less the row's own account** (plan task
    plan#3234, ruling plan#3225): on a bill that lives on a card, Paid with
    its card picked records that it went on that card, so Credit there means
    another card.  With the Visa and the Amex, a bill living on the Amex
    offers the Visa alone; on the owner's only card it offers none, and
    Credit is not drawn (``transaction_service.credit_offered``).  It
    answered the row's own card alone there until that task (decided under
    R-BAL207 at plan task plan#2929), and read the cards among the "Paid
    from" list until plan task plan#3232, when that list stopped holding
    cards (ruling plan#3227).

    Args:
        txn: The row.
        owner: The row OWNER's :class:`OwnerAccounts`.

    Returns:
        The owner's cards in account order but the row's own account, which
        may be none.
    """
    return tuple(card for card in owner.cards if card.id != txn.account_id)


def tick_cards(txn: Transaction, owner: OwnerAccounts) -> tuple[Account, ...]:
    """Return the cards the CC tick on a purchase under *txn* offers; none where it moves nothing.

    The purchase list draws its tick from this (plan task plan#2930, ruling
    plan#2888: "the tick stays the one control, with a pick only for two or
    more cards"): no tick where it is empty, the tick alone for one card, and
    the tick with a pick for two or more.  :func:`credit_cards` less one
    case: a row that lives on a card files every purchase on that card
    already, so its tick is not drawn.  It was drawn nowhere there because it
    could only name the account the purchase is on, and a control that can
    change nothing is not drawn (ruling **R-CC34**'s reason for the "Paid
    from" picker); since plan task plan#3234 :func:`credit_cards` leaves out
    the row's own card there, so the tick could name another, and whether it
    should be drawn so, as Credit is, is question plan#3411.  With no card
    the tick is not drawn either: the door would refuse it in words
    (:func:`the_card`), after the owner had typed the purchase.

    Args:
        txn: The purchase's row, its ``account`` loaded.
        owner: The row OWNER's :class:`OwnerAccounts`.

    Returns:
        The cards, in account order, or ``()``.
    """
    if is_revolving(txn.account):
        return ()
    return credit_cards(txn, owner)


def purchase_card(entry: TransactionEntry) -> Account | None:
    """Return the card a recorded purchase is ON, or ``None``: what its CC tick reads.

    The one answer to "is this purchase on a card" for the edit form's tick
    (plan task plan#3142): ticked, and its "Which card" pick naming this
    card, when the purchase's money moved on a card other than the account
    its row lives on -- a swipe ticked CC, or one picked under "Paid from" on
    the add form before plan task plan#3232, when that list stopped offering
    a card (ruling plan#3227).  The edit door reads it too, so an untouched Save posts back
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


def on_a_card(entry: TransactionEntry) -> bool:
    """Return whether a recorded purchase's money went on a credit card: what the screen marks CC.

    **The one predicate the screens mark a purchase CC by** (plan task
    plan#3143): the grid cell's and the phone card's "includes $X on CC"
    (``entry_service.compute_entry_sums``), and the purchase list's CC badge
    and its edit tick's checked state (``entry_service.entry_list_view``),
    so the cell's figure is the sum of the purchases the list badges.  Until
    then the screen had two spellings: the sum and the badge read the old
    flag alone, so a purchase the CC tick put on the card (plan tasks
    plan#2930, plan#3142), which carries no flag, read as a checking
    purchase beside an edit tick drawn ticked.

    Two arms, as the edit tick reads a purchase (``entry_service._details.
    tick_edit``): the old CC flag, the cheat's spelling for the lines that
    carry it until plan#1034's re-plan converts them, or the card the
    purchase is on (:func:`purchase_card`).

    "On a card" is the tick's question, and the tick reads
    :func:`purchase_card`, which settles two neighbouring readings:

    * **it is NARROWER than the money side's "not on its row's account"**
      (``cash_ledger._cash_leg._moves_elsewhere``, the flag or an account
      other than the row's) by every purchase on an account other than its
      row's that is not a card: one paid from Checking under a row that
      lives on a card, or under a row on Savings (ruling plan#3227: every
      row's "Paid from" offers its own account and every account that holds
      cash).  It is on no card, and badging it CC would say the opposite of its account
      chip.  The reconcile panel's cash figure partitions on
      ``_moves_elsewhere`` and not on this, rightly: it says what the
      panel's own statement will not show, wherever the rest went (its
      caption calls all of that "on a card", which plan#3150 rewords);
    * **and it is not "on a revolving account"**, which would add a purchase
      on the card a row lives on.  That purchase is on its row's account,
      which ``_moves_elsewhere`` does not count either: the row is the
      card's, its chip says so once, and its edit form draws no tick
      (:func:`tick_cards`), so a CC badge would mark a purchase its own form
      cannot un-tick.  A purchase on ANOTHER card under that row is on a
      card (its chip names that card, and its tick, un-ticked, moves it onto
      the row's), so the row's cell counts it, and only it, on CC.

    **What it does not decide: the posted indicator.**  That says whether a
    purchase's posting day is recorded, which the balance and the reconcile
    panel of the account it is on read for every purchase but an old
    flagged line (which is on no account's books).  A purchase on a card is
    in flight on the card until then (``cash_ledger._movement_scope``), so
    the list draws the indicator for every purchase but a flagged one.

    No money reader asks this: the CC payback repays the flagged lines alone
    (``app.utils.entry_partition``), and each cash leg is its own account's
    (``cash_ledger._cash_leg``).

    Args:
        entry: The purchase, its ``transaction`` and ``account`` loadable.

    Returns:
        ``True`` when the purchase carries the old flag or is on a card other
        than its row's account.
    """
    return entry.is_credit or purchase_card(entry) is not None


def the_card(
    txn: Transaction, card_account_id: int | None, *, what: str, again: str,
) -> Account:
    """Return the card a Credit press on *txn*, or a CC tick on a purchase under it, puts money on.

    **The gate of the two acts that put money on a card** (plan task
    plan#3232): a NAMED card is :func:`_named_card`'s, a gate of its own
    rather than "Paid from"'s (:func:`admitted_movement_account_id`), because
    the two acts admit different accounts (ruling plan#3227: "Paid from"
    lists the cash a bill was paid with, and Credit and the CC tick name the
    card it went on).  With none named, the choices are :func:`credit_cards`:
    the one card when there is one; with none there is nothing to put it on,
    and with two or more the owner must say which.  Moved here from
    ``transaction_service._credit`` at plan task plan#2930, when the purchase
    doors became its second caller.

    **On a row that lives on a card the choices are the OTHER cards** (plan
    task plan#3234, ruling plan#3225), so its refusals say so: on the
    owner's only card there is no other to put it on (Credit is not drawn
    there, ``transaction_service.credit_offered``), and with two or more
    others the count is of those.  No screen's tick there reaches this: the
    add form draws none (:func:`tick_cards`, question plan#3411), and the
    edit form draws one only on a purchase already on another card or an old
    flagged line, which ``entry_service._details.tick_edit`` answers before
    asking it.  Posted by hand, a tick there reads the same choices.

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
        ValidationError: The named account is archived (and not the row's
            own) or not a card; or none was named and none, or more than
            one, could be the one meant.
    """
    if card_account_id is not None:
        return _named_card(txn, card_account_id, what=what)
    cards = credit_cards(txn, owner_accounts(txn.user_id))
    if len(cards) == 1:
        return cards[0]
    lives_on_a_card = is_revolving(txn.account)
    if not cards and lives_on_a_card:
        raise ValidationError(
            f"{what} cannot go on another credit card: {txn.name} lives on "
            f"{txn.account.name}, your only card. Add the card it went on "
            f"under Accounts, then {again} again."
        )
    if not cards:
        raise ValidationError(
            f"{what} cannot go on a credit card: there is no credit card in "
            f"the app yet. Add your card under Accounts, then {again} again."
        )
    if lives_on_a_card:
        raise ValidationError(
            f"{txn.name} lives on {txn.account.name}, and you have "
            f"{len(cards)} other credit cards: pick the one {what} went on."
        )
    raise ValidationError(
        f"You have {len(cards)} credit cards: pick the one {what} went on."
    )


def _named_card(txn: Transaction, card_account_id: int, *, what: str) -> Account:
    """Return the card *card_account_id* names, once a Credit press or a CC tick may name it.

    :func:`the_card`'s named arm, the cards those two acts admit: the row's
    own account when it is a card, archived or not (the money is on it: no
    screen names it since plan task plan#3234, :func:`credit_cards` leaving
    it out, and a Credit naming it anyway records what Paid with that card
    picked records, on the same day unless the press posts a day of its
    own), or an ACTIVE card of the ROW's
    owner, never the caller's (a companion's tick names the owner's card,
    ruling **R-CC11**).  The refusals are "Paid from"'s, in its order: one
    answer for "no such account" and "not the owner's", raised before the
    account's name is read (the security response rule), then an archived
    account in words; then an account that is not a card.  Until plan task
    plan#3232 this asked :func:`admitted_movement_account_id` and then
    whether the account was a card, which admitted the same cards while
    "Paid from" offered every card too.

    Args:
        txn: The row pressed, or the purchase's row, already proven the
            caller's.
        card_account_id: The card the owner named.
        what: What goes on the card, for the refusals' sentences.

    Returns:
        The card's :class:`~app.models.account.Account`.

    Raises:
        NotFoundError: The account does not exist or is not the ROW's owner's.
        ValidationError: The account is archived (and not the row's own), or
            is not a credit card.
    """
    account = db.session.get(Account, card_account_id)
    if account is None or account.user_id != txn.user_id:
        raise NotFoundError("Account not found.")
    if not account.is_active and account.id != txn.account_id:
        raise ValidationError(
            f"'{account.name}' is archived, so a purchase cannot be filed "
            "on it. Unarchive the account first, or pick another."
        )
    if not is_revolving(account):
        raise ValidationError(
            f"'{account.name}' is not a credit card, so {what} cannot go "
            "on it: pick one of your cards."
        )
    return account


@dataclass(frozen=True)
class PurchaseChoices:
    """What a purchase list's forms offer for a purchase under one row.

    One value for its two readers (the grid's every envelope and the purchase
    list's fragment, through ``entry_service.entry_list_view``), so neither
    can draw the tick from one set and "Paid from" from another.

    Attributes:
        accounts: The accounts "Paid from" offers, the row's own first
            (:func:`paid_from_accounts`, the set
            :func:`admitted_movement_account_id` admits); the picker is drawn
            only when there are two or more (ruling **R-CC34**).
        cards: The cards the CC tick offers (:func:`tick_cards`): no tick
            when empty, a pick beside it when two or more.
    """

    accounts: tuple[Account, ...]
    cards: tuple[Account, ...]


def purchase_choices(txn: Transaction, owner: OwnerAccounts) -> PurchaseChoices:
    """Return what the purchase forms under *txn* offer, from the OWNER's accounts.

    Args:
        txn: The purchase's row, its ``account`` loaded.
        owner: The row OWNER's :class:`OwnerAccounts`.

    Returns:
        The :class:`PurchaseChoices`.
    """
    return PurchaseChoices(
        accounts=paid_from_accounts(txn, owner), cards=tick_cards(txn, owner),
    )
