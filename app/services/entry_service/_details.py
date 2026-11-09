"""What one purchase SAYS, and which account that names -- the add door's input.

Split from :mod:`._doors` at plan task plan#2930, whose CC tick took that module
past pylint's 1,000-line ceiling: the purchase's content
(:class:`EntryDetails`) and the rule that turns what it says about its money
into an account (:func:`purchase_account_id`) are one value and its one reading,
and neither writes.  :mod:`._doors` reads both, and nothing here reads back.

Architecture:
  - No Flask imports.
  - Reads the named account and the owner's cash-flow set; never writes.
"""

from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services.entry_service._refusals import (
    _reject_flag_beside_another_account,
)
from app.services.movement_account import admitted_movement_account_id, the_card
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
            edit door's tick still flips it, until plan#3142 makes that tick
            move the purchase too.  A tick beside
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
            account until a card exists and the one every caller but the
            add-purchase form states.  A card swipe in a checking envelope
            names the card here: the envelope's spend grows by the purchase
            whatever account it moved through, checking's fold sees nothing
            on that day, and the card's fold sees the swipe.  The door gates
            it against the ROW's owner, never the caller (a companion records
            the purchase on the owner's card, ruling **R-CC11**), and admits
            exactly what the picker offers -- the row's own account or a
            member of the owner's cash-flow set (ruling **R-CC37**; see
            :func:`purchase_account_id`).

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
    owner has no card (ruling **R-CC34**: the picker renders only when there
    is a choice).

    **The gate itself is**
    :func:`app.services.movement_account.admitted_movement_account_id`
    **since plan step ``CC-5-3``**: the ONE spelling of which accounts a
    movement under a row may name (rulings **R-CC37** / **R-CC39** -- the
    row's own, or a member of the ROW OWNER's cash-flow set, the tuple every
    picker renders from), shared with the settle verb's tender so the two
    doors cannot part.  Its argument -- the owner gate over ``txn.user_id``
    (ruling **R-CC11**), the 404, the two refusals and why the books boundary
    is not asked there -- is that module's docstring; this door's own half is
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
        ValidationError: The account is archived, or is neither the row's own
            nor a member of the owner's cash-flow set; or, ticked, the owner
            has no card, picked an account that is not a card, has two or
            more cards and picked none, or set "Paid from" to another account
            too; or a card is picked with the tick left off.
    """
    if not details.on_card and details.card_account_id is not None:
        raise ValidationError(
            f"A card is picked for {details.description} but CC is not "
            "ticked: tick CC to put it on that card, or set 'Which card' back."
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
