"""What one purchase SAYS, and which account that names -- the doors' input.

Split from :mod:`._doors` at plan task plan#2930, whose CC tick took that module
past pylint's 1,000-line ceiling: the purchase's content
(:class:`EntryDetails`) and the rule that turns what it says about its money
into an account (:func:`purchase_account_id`) are one value and its one reading,
and neither writes.  The edit door's twin is :func:`tick_edit` (plan task
plan#3142): what the CC tick on a recorded purchase asks of it.  :mod:`._doors`
reads all three, and nothing here reads back.

Architecture:
  - No Flask imports.
  - Reads the named account and the owner's accounts; never writes.
"""

from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.entry_service._refusals import (
    _reject_flag_beside_another_account,
)
from app.services import match_press
from app.services.movement_account import (
    admitted_movement_account_id,
    purchase_card,
    the_card,
)
from app.services.settle_day import SettleDay
from app.services.stated_figure import StatedFigure


@dataclass(frozen=True)
class EntryDetails:
    """The content of a purchase entry -- the add-purchase form's inputs.

    The user-supplied fields of a :class:`TransactionEntry` (what was
    bought, how much, when, and which account paid it), bundled so
    ``create_entry`` takes them as one cohesive argument distinct from the
    routing/ownership context (the parent transaction and the acting user).

    Fields:
        figure:      What the purchase cost and WHO WROTE the figure, as one
            :class:`~app.services.stated_figure.StatedFigure` (plan step
            **X-bi-3e-1**, ruling **R-BAL69**): ``typed`` from the add-purchase
            form, ``observed`` from ``statement_match._create._born_purchase``,
            where the bank line IS the figure.  Its amount is SIGNED --
            POSITIVE for a charge and NEGATIVE for a REFUND (ruling
            **bank_import:R-II**).  **It said *Positive* until plan step
            ``bank_import:X-gj-2b-3``, and its own caller contradicted it**:
            the born-purchase builder passes ``-Decimal(str(line.amount))``,
            which for a merchant credit is negative.  What refuses a negative
            is the hand-entry FORM (``EntryCreateSchema``'s ``Range(min=0.01)``),
            where a typed negative is a typo -- not this value, and not the
            table, whose only rule is ``<> 0``
            (:func:`~._refusals._reject_zero_amount`).
        description: Store name or brief note (1--200 chars).
        purchased_on: Date the purchase HAPPENED.  Backdating is ordinary; a
            date after the user's today is refused (ruling R-M, see
            :func:`~._refusals._reject_future_purchase_date`).
        on_card:     Whether the owner ticked CC: the purchase went on a
            credit card, and the door files it ON the card (plan task
            plan#2930, ruling plan#2888), dated ``purchased_on`` there as every
            swipe in flight is, and writes no CC Payback for it.  Which card is
            ``movement_account.the_card``'s: the owner's one card, or the one
            *card_account_id* names when there are two or more.  **The add
            door writes no CC flag any more** (``is_credit``): it was the
            cheat's spelling of "on the card" from before the card was an
            account, and the 26 lines that carry it keep it, and the payback
            arms that follow it, until plan#1034's re-plan converts them.  The
            edit door's tick writes none either since plan task plan#3142: it
            moves the purchase (:func:`tick_edit`).  A tick beside
            an ``account_id`` other than the row's is refused
            (:func:`~._refusals._reject_flag_beside_another_account`): the
            tick and "Paid from" would each say where the money went.
        card_account_id: The card the owner picked beside the tick, or
            ``None``: the form draws the pick only for two or more cards.  A
            pick without the tick is refused
            (:func:`purchase_account_id`).

        settle_day: The day the BANK took the money and HOW that day is
            known (:class:`app.services.settle_day.SettleDay`), when the caller
            already knows it, else ``None`` -- which is the state every
            hand-typed purchase is born in.  The one caller that supplies one
            is ``statement_match._create.create_purchase_from_line``, and its
            basis is always ``observed``: the bank line IS why the purchase
            exists.

        account_id: The account the purchase's money MOVED THROUGH (plan step
            ``credit_card:CC-5-2``, ruling **R-CC15**; the movement folds on
            its own account, **R-BAL75**), or ``None`` for its row's -- where
            the row is EXPECTED to be paid from, which is every purchase's
            account until the owner has another account that holds cash, and
            the one every caller but the add-purchase form states.  A
            purchase paid from Savings in a checking envelope names Savings
            here: the envelope's spend grows by the purchase whatever account
            it moved through, checking's fold sees nothing on that day, and
            Savings' fold sees the purchase.  The door gates it against the
            ROW's owner, never the caller (a companion records the purchase
            on the owner's account, ruling **R-CC11**), and admits exactly
            what the picker offers -- the row's own account, then every
            account of the owner's that holds cash, never a card other than
            the row's own (ruling plan#3227; see :func:`purchase_account_id`).
            A swipe on a card is *on_card*, the CC tick, never this field.

    **The posting day was deliberately NOT here until plan step
    ``bank_import:X-f6a-3b``, and the premise that kept it out was true only of
    the doors that existed then.**  It read: *the day the bank took the money is
    an observation, and at the moment a purchase is recorded there is nothing to
    have observed.*  That is exactly right for the add-purchase form, and false
    for a purchase created FROM a bank statement line -- there the observation is
    what caused the record to exist, and it is the more reliable of the two days
    (**R-FW**).

    Recording it here rather than through a follow-up :func:`update_entry` is
    the difference between one act and two: the second call would re-run
    ``sync_entry_payback`` and the posting reconcile against an intermediate
    state in which a purchase the bank has already taken looks outstanding, and
    it would leave that state committed if anything after it refused.
    """

    figure: StatedFigure
    description: str
    purchased_on: date
    on_card: bool = False
    card_account_id: int | None = None
    settle_day: SettleDay | None = None
    account_id: int | None = None


def purchase_account_id(txn: Transaction, details: EntryDetails) -> int:
    """Return the account the new purchase's money moved through, gated.

    Plan step ``credit_card:CC-5-2``, the FIRST door that writes a movement
    onto another account than its row's (ruling **R-BAL76** dropped the key
    that forbade it at ``CC-5-1``).  ``None`` is the row's own account --
    today's behaviour for every caller, and the add-purchase form's when the
    owner has no other account that holds cash (ruling **R-CC34**: the
    picker renders only when there is a choice).

    **The gate itself is**
    :func:`app.services.movement_account.admitted_movement_account_id`
    **since plan step ``CC-5-3``**: the ONE spelling of which accounts a
    movement under a row may name (ruling plan#3227, which replaced rulings
    **R-CC37** and **R-CC39** -- the row's own, then every account of the ROW
    OWNER's that holds cash, never a card other than the row's own, the
    tuple every picker renders from), shared with the settle verb's tender
    so the two doors cannot part.  Its argument -- the owner gate over
    ``txn.user_id`` (ruling **R-CC11**), the 404, the refusals and why the
    books boundary is not asked there -- is that module's docstring; this door's own half is
    the ``None`` arm and the tick's.  The review of this leaf's first cut found the door
    admitting every non-loan account under a docstring that claimed parity
    with the picker; the grid half (the second commit of CC-5-2) made the
    predicate one function rather than two spellings that agreed, and
    ``CC-5-3`` moved it out of this module rather than copy it for the
    second door.

    **A ticked purchase names the owner's card** (plan task plan#2930,
    ruling plan#2888): the card ``movement_account.the_card`` answers, the
    one answer the row's Credit press asks too, against the ROW's owner, so
    a companion's tick files on the owner's card (ruling **R-CC11**).  The
    tick beside a "Paid from" naming another account than the row's is
    refused first, by the test that refused the CC flag beside one: the two
    controls would each say where the money went.  **A card picked with the
    tick left off is refused too** (the review of plan#2930, M4): the pick
    names which card only for a purchase ticked onto one, so alone it says
    two things at once, and filing the purchase on checking would ignore
    what the owner chose.

    Args:
        txn: The parent row, already proven the caller's.
        details: The submission; ``details.account_id``, or the tick and its
            pick, are what is gated.

    Returns:
        The ``budget.accounts.id`` to write onto the purchase.

    Raises:
        NotFoundError: The account, or the picked card, does not exist or is
            not the ROW's owner's.
        ValidationError: The account is archived, is a credit card other
            than the row's own, or is neither the row's own nor one that
            holds cash; or, ticked, the owner
            has no card, picked an account that is not a card, has two or
            more cards and picked none, or set "Paid from" to another account
            too; or a card is picked with the tick left off.
    """
    _reject_pick_without_tick(
        details.description, details.on_card, details.card_account_id,
    )
    if details.on_card:
        _reject_flag_beside_another_account(
            True,
            txn.account_id if details.account_id is None else details.account_id,
            txn.account_id,
        )
        return the_card(
            txn, details.card_account_id,
            what=details.description, again="tick CC",
        ).id
    if details.account_id is None:
        return txn.account_id
    return admitted_movement_account_id(
        txn, details.account_id, movement="purchase",
    )


def _reject_pick_without_tick(
    what: str, on_card: bool, card_account_id: int | None,
) -> None:
    """Refuse a "Which card" pick posted with the CC tick left off, at either door.

    The review of plan#2930 (M4): the pick names which card only for a
    purchase ticked onto one, so alone it says two things at once, and
    filing the purchase where it stands would ignore what the owner chose.
    One sentence for the add door (:func:`purchase_account_id`) and the edit
    door (:func:`tick_edit`).

    Args:
        what: The purchase's description, for the sentence.
        on_card: Whether the tick was posted ticked.
        card_account_id: The card picked, or ``None``.

    Raises:
        ValidationError: When a card is picked and the tick is not.
    """
    if not on_card and card_account_id is not None:
        raise ValidationError(
            f"A card is picked for {what} but CC is not "
            "ticked: tick CC to put it on that card, or set 'Which card' back."
        )


@dataclass(frozen=True)
class TickEdit:
    """What the CC tick on a RECORDED purchase asks of it: one act, or none.

    Attributes:
        flag: The old CC flag the purchase carries after the save: what it
            carried, less an old flagged line's un-tick, whose flag comes off
            through the payback arms the 26 such lines still have (the sync
            deletes or re-prices the envelope's CC Payback) until plan#1034's
            re-plan converts them.  Never set where it was not.
        account_id: The account the tick MOVES the purchase to -- onto the
            card, back to its row's account, or to another card -- through
            ``movement_account.re_point_movement``; ``None`` where it stays.
    """

    flag: bool
    account_id: int | None = None


def tick_edit(
    entry: TransactionEntry, on_card: bool | None, card_account_id: int | None,
) -> TickEdit:
    """Return what the edit form's CC tick and its pick ask of *entry*.

    Plan task plan#3142, ruling plan#2888 ("the tick stays the one control,
    with a pick only for two or more cards"): the tick on a recorded
    purchase says where its money went, as the add form's does, so changing
    it MOVES the purchase.  It reads the purchase as the form drew it
    (``movement_account.purchase_card``), so an untouched Save moves nothing:

    * **on no card, ticked**: onto the card ``movement_account.the_card``
      answers -- the owner's one card, or the one the pick names -- against
      the ROW's owner, so a companion's tick moves it onto the owner's card
      (ruling **R-CC11**).  Its refusals are the add tick's: no card, two or
      more and none picked, a pick that is not the owner's (404) or not a
      card;
    * **on a card, un-ticked**: back to the account its row lives on;
    * **on a card, ticked**: where the pick names another card, onto it; with
      no pick (one card, or an archived card the pick does not offer), it
      stays;
    * **on no card, un-ticked**: it stays -- a purchase "Paid from" another
      account than a card keeps it, the edit form having no "Paid from";
    * **an old flagged line** keeps its flag while ticked and loses it when
      un-ticked (the payback arms it has, :class:`TickEdit`), and moves
      nowhere: converting one is plan#1034's.  A card picked beside its tick
      is refused, because the form draws no pick on one: it has no card to
      name, and putting it on one would be that conversion.

    A pick with the tick off is refused at either door
    (:func:`_reject_pick_without_tick`), except a pick naming the card the
    purchase is ON: the form draws the pick with that card selected, so a
    Save that changed only the tick posts it back.  ``on_card`` absent -- a
    partial update such as the statement matcher's -- asks nothing.

    Args:
        entry: The purchase as stored, its row and account loadable.
        on_card: The tick as posted, or ``None`` when not submitted.
        card_account_id: The card picked, or ``None``.

    Returns:
        The :class:`TickEdit`.

    Raises:
        NotFoundError: The picked card is not an account of the row's owner.
        ValidationError: On the refusals above.
    """
    card = None if entry.is_credit else purchase_card(entry)
    # The pick is drawn with the purchase's own card selected, so a Save that
    # changed only the tick posts that card back: the box untouched, not a
    # pick (``settle_day.submitted_settle_day``'s echo rule, one control over).
    echoed = card is not None and card_account_id == card.id
    _reject_pick_without_tick(
        entry.description, bool(on_card), None if echoed else card_account_id,
    )
    if on_card is None:
        return TickEdit(flag=entry.is_credit)
    if entry.is_credit:
        if card_account_id is not None:
            raise ValidationError(
                f"{entry.description} is still repaid by its CC Payback, so "
                "it cannot be put on a card by name yet: un-tick CC and "
                "save, then tick CC to put it on the card."
            )
        return TickEdit(flag=on_card)
    if not on_card:
        return TickEdit(
            flag=False,
            account_id=None if card is None else entry.transaction.account_id,
        )
    if card is not None and (card_account_id is None or echoed):
        return TickEdit(flag=False)
    target = the_card(
        entry.transaction, card_account_id,
        what=entry.description, again="tick CC",
    )
    return TickEdit(
        flag=False,
        account_id=None if target.id == entry.account_id else target.id,
    )


def settle_day_after_move(
    entry: TransactionEntry, valid_updates: dict,
) -> SettleDay | None:
    """Return the posting day a purchase the CC tick MOVES ends the save with.

    Decided under R-BAL207 at plan task plan#3142 (an edge of a path not yet
    released): **a posting day the save did not move is the OLD account's,
    and the move voids it**.  It records the day that account's bank took the
    money, so it is no fact about the new account, whatever its basis: a day
    a checking statement's line was observed on, or a checking true-up
    bounded, says nothing of when the card took it.  Kept, it would date the
    purchase on the card by checking's day, where a purchase on a card counts
    from the day it was bought, marked or not (rulings plan#2926, plan#2958):
    bought Jan 4 and taken from checking Jan 6, a Visa closing on the 5th
    would carry it on the Feb 5 statement, due Feb 27, not Jan 27's.  Voided,
    it is in flight on the new account from its own date, as every purchase
    put there is, until that account's statement shows it.  A day the save
    DID move is the owner's word for the new account, typed in the same Save,
    and is kept: the books boundary grades it there
    (``movement_account.re_point_movement``).  Read by the edit door
    (``_doors.update_entry``) before its two day rules, so they are checked on
    the day the save leaves.

    The edit form posts its Posted box on every Save, prefilled, so a day
    equal to the stored one is the box coming back untouched
    (``settle_day.submitted_settle_day``'s echo rule), not a restatement.

    Args:
        entry: The purchase as stored.
        valid_updates: The submission, already narrowed to updatable fields.

    Returns:
        The day the save typed, where it moved one; else ``None``.
    """
    if "settle_day" not in valid_updates:
        return None
    typed = valid_updates["settle_day"]
    if typed is None or typed.day == entry.settled_on:
        return None
    return typed


def move_press(
    press: match_press.Press | None, entry: TransactionEntry,
) -> match_press.Press | None:
    """Return the press the CC tick's move withdraws under.

    A companion's edit carries the PAYBACK's sentence
    (``entry_credit_workflow.payback_refusal``, the line an old flagged
    line's un-tick frees), and a move frees the purchase's OWN line, so its
    refusal names the purchase (ruling **R-CC132**: *"Kroger is matched to a
    line on the bank statement, so only the account owner can delete it"*),
    the same save reworded (``match_press.Press.reworded``).  Any other press
    is returned as it is.

    Args:
        press: The edit's save.
        entry: The purchase the tick moves.

    Returns:
        The press for the move.
    """
    if press is None or not isinstance(press.shown, match_press.OwnerOnly):
        return press
    return press.reworded(
        f"{entry.description} is matched to a line on the bank statement, "
        "so only the account owner can move it onto or off a card."
    )
