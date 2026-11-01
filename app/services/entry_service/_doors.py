"""The WRITE doors: what a user may do to one purchase, and what follows.

``entry_service``'s CRUD half -- the guards a mutation must pass, the three
doors themselves (create, update, delete), the owner resolution they share, and
the re-derivation every one of them triggers.  What a set of purchases ADDS UP
TO, and the screen contexts built from those sums, is the sibling leaf
(:mod:`._sums`); the arrow runs one way -- this module reads
the reductions in :mod:`._sums`, and that module reads nothing here.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging
from dataclasses import dataclass
from datetime import date

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User
from app import ref_cache
from app.exceptions import NotFoundError, ValidationError
from app.services import match_press, match_withdrawal, movement_removal, posting_service
from app.services.entry_credit_workflow import (
    payback_a_removal_deletes,
    sync_entry_payback,
    x_press_for_payback,
)
from app.services.movement_account import admitted_movement_account_id
from app.services.owned_transaction import load_owned_transaction
from app.services.settle_day import (
    SettleDay,
    record_settle_day,
    recorded_settle_day,
)
from app.services.stated_figure import StatedFigure
from app.services.entry_service._sums import credit_total_moves
from app.services.entry_service._refusals import (
    _reject_flag_beside_another_account,
    _reject_future_posting_day,
    _reject_future_purchase_date,
    _reject_zero_amount,
    _reject_settled_addition,
    _reject_settled_before_purchase,
    _reject_settled_parent,
    _reject_settled_removal,
    _reject_settlement_record,
    cost_fields_changing,
    deleted_row_purchase_refusal,
)
from app.utils.balance_predicates import is_cancelled
from app.utils.hidden_row import HiddenRow
# ``is_credit`` from balance_predicates collides with the
# ``is_credit: bool`` keyword argument on this module's
# ``create_entry`` / ``update_entry`` functions.  Aliasing the
# predicate keeps both the helper accessible and the public
# function signatures stable.
from app.utils.balance_predicates import is_credit as txn_is_credit
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_UPDATED,
    log_event,
)


logger = logging.getLogger(__name__)

# Fields that can be updated on an entry via update_entry().  Two of them are
# VALUES rather than columns -- ``figure`` (the amount and who wrote it) and
# ``settle_day`` (the day and how it is known) -- and the door writes each
# through the one writer of its pair.
_UPDATABLE_FIELDS = frozenset({
    "figure", "description", "purchased_on", "settle_day", "is_credit",
})


def _resync_after_entry_change(txn: Transaction) -> None:
    """Reconcile an envelope's postings after an entry change.

    **It writes no figure, and losing that half is plan step X-au-c3's doing.**
    It re-derived ``actual_amount = sum(entries)`` for a settled envelope, and a
    settled row stores no figure at all -- its amount IS the sum of its
    entries, answered on read by ``row_valuation.settled_figure`` (the
    envelope's since X-au-c3, every settled row's since ``balance:X-bi-4b-1``,
    and the row's own figure columns gone at ``X-bi-4b-2``).  A stored copy is
    what needed a reconciler; with the copy gone the reconciler has nothing
    left to reconcile, and the entries and the figure they add up to cannot
    drift because there is only one of them.

    **Its whole gate was ``if settled and txn.entries``, and BOTH halves of that
    gate are gone.**  The figure it would have written no longer exists to
    write, and the ``settled`` half stopped being a question this function may
    ask: a settled parent DOES reach here, through the one entry edit
    :func:`_reject_settled_parent` admits on such a row -- recording the day
    the bank took a purchase -- and re-dating that purchase's cash is the whole
    point of the reconcile below.  Two defects went with the deleted arm:

      * deleting the LAST purchase from a settled envelope left the previous
        figure standing -- deliberately, because rewriting the close to ``$0.00``
        looked worse than a stale number.  That state is unreachable now for a
        different reason than when this was written: it said *the delete is
        refused*, and plan step ``bank_import:X-f6f`` admits one where removing
        the purchase cannot change what the row's own close booked.  What makes
        the stale figure impossible is that there is no stored figure left to go
        stale -- a settled row answers ``Sigma(entries)``, so one closed
        empty answers ``$0.00`` because that is what its records say;
      * adding a purchase to a settled row whose figure was a HUMAN's correction
        overwrote that correction with the entry sum.  That cannot happen now
        for two independent reasons: the hook is gone, and
        :func:`_reject_settled_addition` admits an add only on a settled row
        holding no covering movement -- one whose entries ARE its record, so
        there is no stated figure for a human to have corrected.  Ruling
        **R-FB**'s rule -- a figure somebody read off a statement is a fact --
        finally holds on this path too.

    What remains is the ledger reconcile (Build-Order Step 3).  An entry mutation
    changes what the row's family has posted: recording a posting day makes
    that purchase a dated leg of its own, clearing one reverses it, flipping
    an entry to or from credit posts or reverses its leg, and an amount edit
    re-prices a dated one.  The row itself books nothing (plan step
    ``balance:X-bi-4a``, ruling **R-BAL80**), so a purchase with no day moves
    nothing in the ledger -- it is in flight in the projection instead.

    **It is UNGATED since plan step X-f3b, and that is ruling R-FM.**  It ran
    only on the settled band, on the premise that "a Projected envelope has no
    postings" -- false once a purchase carrying a recorded bank posting day
    books its own leg whatever its envelope's status is.  One call reconciles
    the whole family, so this door needs no second list of what changed; on a
    Projected row with no posted purchases it reads an empty ledger and writes
    nothing.

    Does NOT commit -- the calling service function owns the session boundary
    (the reconcile flushes but does not commit, matching this module's
    contract).

    **The reconcile takes no ``settled`` flag since plan step
    ``balance:X-bi-4a``.**  It took one through ``X-bi-3e``, read off the ROW
    rather than hardcoded ``False`` -- a choice that paid for itself when the
    developer's 2026-08-17 ruling widened this door to admit a posting-day
    edit on a settled row, whose own leg a hardcoded ``False`` would have
    silently stopped booking.  The flag chose the row's OWN target, and a row
    has none now: a movement posts iff it is dated under a contributing
    parent, which the reconcile reads off each movement.

    Args:
        txn: The parent envelope transaction whose entries changed.
    """
    posting_service.sync_transaction_postings(txn)


def resolve_owner_id(user_id: int) -> int:
    """Return the data-owning user_id.

    For owner accounts, returns user_id unchanged.  For companion
    accounts, returns the linked_owner_id (the owner whose budget
    data the companion has access to).

    Args:
        user_id: The ID of the requesting user.

    Returns:
        int -- the ID of the user who owns the budget data.

    Raises:
        NotFoundError: If user_id does not correspond to an existing user.
        ValidationError: If a companion user has no linked_owner_id
            (indicates a data integrity issue).
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    owner_id = user.data_owner_id
    if owner_id is None:
        raise ValidationError(
            f"Companion user {user_id} has no linked owner. "
            "This is a data integrity issue -- contact the administrator."
        )
    return owner_id


# Backward-compatible alias -- existing tests reference the private name.
_resolve_owner_id = resolve_owner_id


@dataclass(frozen=True)
class EntryDetails:
    """The content of a purchase entry -- the add-purchase form's inputs.

    The user-supplied fields of a :class:`TransactionEntry` (what was
    bought, how much, when, and whether paid by credit card), bundled so
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
            :func:`_reject_future_purchase_date`).
        is_credit:   Whether this was paid with a credit card -- the CHEAT's
            flag, kept until ``CC-7`` for the legacy lines that carry it.  A
            purchase that names an ``account_id`` other than its row's says
            the same thing properly and may not carry the flag too
            (:func:`~._refusals._reject_flag_beside_another_account`).

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
            :func:`_purchase_account_id`).

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
    is_credit: bool = False
    settle_day: SettleDay | None = None
    account_id: int | None = None


def _purchase_account_id(txn: Transaction, details: EntryDetails) -> int:
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
    the ``None`` arm.  The review of this leaf's first cut found the door
    admitting every non-loan account under a docstring that claimed parity
    with the picker; the grid half (the second commit of CC-5-2) made the
    predicate one function rather than two spellings that agreed, and
    ``CC-5-3`` moved it out of this module rather than copy it for the
    second door.

    Args:
        txn: The parent row, already proven the caller's.
        details: The submission; ``details.account_id`` is what is gated.

    Returns:
        The ``budget.accounts.id`` to write onto the purchase.

    Raises:
        NotFoundError: The account does not exist or is not the ROW's owner's.
        ValidationError: The account is archived, or is neither the row's own
            nor a member of the owner's cash-flow set.
    """
    if details.account_id is None:
        return txn.account_id
    return admitted_movement_account_id(
        txn, details.account_id, movement="purchase",
    )


def create_entry(
    transaction_id: int,
    user_id: int,
    details: EntryDetails,
    *,
    press: match_press.Press | None = None,
) -> TransactionEntry:
    """Create a new purchase entry against a transaction.

    Validates ownership (including companion resolution), entry
    capability, transfer guard, expense-only guard, and status guard
    before creating the entry.

    Args:
        transaction_id: Parent transaction ID.
        user_id: The creating user's ID (owner or companion).
        details: :class:`EntryDetails` -- the purchase content (the figure
            with who wrote it, description, purchased_on, is_credit, and the
            posting day -- with the basis that says how it is known -- where
            the caller already has one).
        press: The save's press, for the payback sync, the one place an add
            frees a line: an owner's over nothing named (finding **CC-381**),
            a companion's ``OwnerOnly`` (ruling **R-CC132**, plan step
            CC-5-4a-5).

    Returns:
        The newly created TransactionEntry (flushed, id available).

    Raises:
        NotFoundError: Transaction not found or not accessible by this
            user; or ``details.account_id`` names no account of the ROW's
            owner (:func:`_purchase_account_id`).
        ValidationError: Transaction deleted, not entry-capable, is a
            transfer, is income, or has a blocked status (Cancelled, Credit, the archive, or
            a settled row whose figure is not its purchases -- see
            :func:`_reject_settled_addition`); the account is archived or a
            loan, or the ``CC`` flag is set beside an account other than the
            row's; or a purchase day in the future, a posting day in the
            future, or a posting day before the purchase day.
    """
    owner_id = resolve_owner_id(user_id)

    # **Every refusal below reads the row as it stands after the request's
    # owner lock** (plan step ``balance:X-bn``, ruling **R-CC106**, which ended
    # the class ruling **R-CC96** closed door by door: "whichever click lands
    # second gets a sentence").  A purchase added while the same row's delete
    # was open read the row as live, waited at the payback sync's row lock
    # until the delete committed, and then committed under the hidden row --
    # measured by plan step ``credit_card:CC-5-4a-4``'s fourth review.  The
    # request's transaction now takes its owner's write lock before it reads
    # any of the owner's data (:mod:`app.db_transaction`), so another tab's
    # Delete either committed before this read or waits for this purchase to
    # commit, and a Delete that came first meets the refusal below in words.
    # Ownership is the row's own owner column (security response rule: 404).
    txn = load_owned_transaction(transaction_id, owner_id)

    # **A DELETED row takes no purchase** (plan step ``credit_card:CC-5-4a-4``,
    # its second review, H1).  Deleting a recurring occurrence empties it and
    # keeps it as a tombstone (ruling **R-CC75**: "A hidden row then never
    # holds money"), and ``get_accessible_transaction`` did not filter
    # ``is_deleted`` -- so a stale grid (a companion's open page) posting here
    # put a purchase back under a row no screen shows, which locked its pay
    # period with no row to delete it from.  The settle doors' own refusal of
    # the same row (``transaction_service._row_rules.reject_unsettleable``).
    # One of the three layers of ruling **R-CC89** ("a deleted row takes no
    # money"): the ownership doors now answer a deleted row "not found", and
    # since plan step ``balance:X-bn`` no Delete can commit between their read
    # and this one (both follow the request's owner lock), so a route that
    # passed them hands this line a live row; a service caller that skipped
    # them still could hand it a deleted one, and
    # :mod:`app.deleted_row_infrastructure` refuses the write in the database
    # for one that skips this line too.
    if txn.is_deleted:
        # Named, never numbered (ruling **R-CC98**), and "was archived" where
        # its recurring item is (ruling **R-CC107**); the one sentence the
        # add-purchase route also shows for a row that is gone.
        raise ValidationError(deleted_row_purchase_refusal(HiddenRow.of(txn)))

    # Entry-capable: purchase tracking must be enabled on the row's
    # DEFINITION (its ``is_envelope``).  Resolved by
    # Transaction.tracks_purchases -- which also refuses a transfer shadow
    # and a CC payback, since neither names a definition (ruling **R-BAL73**,
    # plan step ``balance:X-bi-7d-2``).  A second guard on ``transfer_id``
    # stood behind this one until that step made it unreachable: a shadow
    # cannot track purchases, so it never got past this line.
    if not txn.tracks_purchases:
        raise ValidationError(
            "This transaction does not support individual purchase tracking. "
            "Enable 'Track individual purchases' on the transaction "
            "or its template first."
        )

    # Expense-only guard.
    if txn.is_income:
        raise ValidationError(
            "Cannot add purchase entries to income transactions."
        )

    # Status guard: CANCELLED and CREDIT transactions cannot accept entries.
    # CANCELLED is excluded from balance -- adding entries makes no sense.
    # CREDIT is blocked for entry-capable templates (OQ-10) -- credit
    # handling happens at the entry level, not the transaction level.
    # Routed through the centralized per-status predicates
    # (D6-09 / MED-02) so the two guards share one definition with
    # every other ``status == cancelled`` / ``status == credit``
    # comparison in the project.
    if is_cancelled(txn):
        raise ValidationError(
            "Cannot add entries to a cancelled transaction."
        )
    if txn_is_credit(txn):
        raise ValidationError(
            "Cannot add entries to a transaction with Credit status. "
            "Entry-capable transactions handle credit at the entry level."
        )
    # The DAY the pair states, unwrapped once for the three refusals that are
    # about the day and not about how it is known (plan step **X-az**).  They
    # take a ``date`` rather than the pair because none of them reads the basis:
    # a day in the future is not a fact whatever named it, and money cannot
    # leave before it was spent whoever says when it left.
    posting_day = (
        details.settle_day.day if details.settle_day is not None else None
    )

    # A SETTLED parent is the third refusal, and it is finding **N-229**: an
    # entry on such a row used to be accepted, persisted, and silently inert --
    # the actual was not recomputed (that half graded ``is_done``) while the
    # postings were reconciled anyway (that half graded the settled BAND).
    # **Its own rule since plan step X-f6a-3b**: a new purchase against a row
    # whose figure IS its purchases is what a bank statement evidences, where a
    # row storing a fixed figure cannot record one at all.
    _reject_settled_addition(txn)

    # WHICH ACCOUNT the money moved through, gated against the ROW's owner
    # (plan step ``credit_card:CC-5-2``; the rule and its refusals are
    # :func:`_purchase_account_id`'s).  After the row guards, so the row's
    # own refusals keep their order, and before the content guards, because a
    # foreign account is a bad REFERENCE and answers 404 where the content
    # rules answer with a message.  The flag beside it is refused by the same
    # test both purchase doors apply: a purchase on another account already
    # says how it was paid.
    account_id = _purchase_account_id(txn, details)
    _reject_flag_beside_another_account(
        details.is_credit, account_id, txn.account_id,
    )

    # Content guard, after the ownership and transaction guards so a
    # non-owner still gets the 404 rather than a validation message that
    # confirms the row exists (ruling R-M; see
    # _reject_future_purchase_date).
    _reject_zero_amount(details.figure.amount)
    _reject_future_purchase_date(details.purchased_on)
    # The posting day's two bounds, the SAME pair :func:`update_entry` applies
    # and for the same reasons -- a day the bank has not reached yet (ruling
    # **R-FM**), and a day before the purchase it belongs to
    # (``ck_transaction_entries_settled_not_before_purchase``).  They arrived
    # here with ``EntryDetails``' posting day at plan step X-f6a-3b: a door that
    # accepts a field and leaves its rules to the OTHER door is a boundary that
    # holds on one and not the other, which is exactly what
    # :func:`_reject_future_purchase_date`'s own docstring warns against.  Both
    # are no-ops for the ``None`` every hand-typed purchase is born with.
    _reject_future_posting_day(posting_day)
    _reject_settled_before_purchase(details.purchased_on, posting_day)

    entry = TransactionEntry(
        transaction_id=transaction_id,
        # The account the money MOVED THROUGH -- the caller's, gated above,
        # else the row's own (plan step ``credit_card:CC-5-2``: a card swipe
        # in a checking envelope is a movement ON the card, ruling **R-CC15**,
        # and folds there, **R-BAL75**).  Written explicitly rather than
        # derived at flush time, so this line cannot be silently wrong --
        # only absent, which is a NOT NULL violation.
        account_id=account_id,
        # The ROW's owner, never the caller (``user_id`` below is the AUTHOR,
        # a companion's own id when a companion records the purchase).
        # ``fk_transaction_entries_owner_transaction`` refuses any other value
        # (plan step ``credit_card:CC-5-1``, ruling **R-BAL76**).
        owner_id=txn.user_id,
        user_id=user_id,
        amount=details.figure.amount,
        description=details.description,
        purchased_on=details.purchased_on,
        is_credit=details.is_credit,
        # WHO WROTE the figure, stated by the caller with the figure itself
        # (plan step **X-bi-3e-1**, ruling **R-BAL61**): the form says a
        # person did, the born-purchase builder says the bank did.  It was
        # inferred from the day beside the figure until that step.  NOT NULL
        # and no default on the column, so a writer that says nothing is
        # refused at flush rather than guessed for.
        figure_source_id=ref_cache.movement_figure_source_id(
            details.figure.source,
        ),
    )
    # The posting day and the basis that says how it is known, written as
    # ONE pair (plan step **X-az**).  Assigned through the shared writer
    # rather than as two constructor kwargs so this door and
    # :func:`update_entry` cannot come to disagree about which columns the
    # pair is -- and so the constructor cannot state half of it.
    record_settle_day(entry, details.settle_day)
    db.session.add(entry)
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_CREATED, BUSINESS,
        "Transaction entry created",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=transaction_id,
        entry_id=entry.id,
        amount=str(details.figure.amount),
        is_credit=details.is_credit,
        # WHICH account the money moved through (plan step CC-5-2): a receipt
        # naming the row alone cannot tell a checking debit from a card
        # swipe, and the two move different balances.
        account_id=account_id,
        # The posting day is logged because a purchase BORN with one has
        # already moved money: it books its own dated cash leg at the reconcile
        # below, where a purchase born without one holds its envelope's budget
        # back instead.  A receipt that named only the amount could not tell
        # the two apart.
        settled_on=(
            posting_day.isoformat() if posting_day is not None else None
        ),
        # WHICH KIND of day it is, beside the day itself (plan step X-az):
        # a receipt naming only the day cannot tell a bank observation from
        # a bound, and the two mean different things about this purchase.
        settled_day_basis=(
            details.settle_day.basis.value
            if details.settle_day is not None else None
        ),
    )

    # Only a CREDIT purchase carrying money moves the envelope's credit sum
    # (finding **N-323**); a debit purchase, or a zero-amount one, cannot.
    sync_entry_payback(
        transaction_id, owner_id,
        moves_credit_total=bool(details.is_credit and details.figure.amount),
        press=press,
    )
    _resync_after_entry_change(txn)

    return entry


def _posting_day_after(
    entry: TransactionEntry, valid_updates: dict,
) -> "date | None":
    """Return the posting day *entry* would carry once *valid_updates* is applied.

    The RESULT the update door's two day rules are checked on: the submitted
    pair's day where the submission carries ``settle_day``, else the stored
    pair's (:func:`~app.services.settle_day.recorded_settle_day`), and
    ``None`` where the result carries no day at all -- the outstanding state.

    Args:
        entry: The purchase being updated, as stored.
        valid_updates: The submission, already narrowed to updatable fields.

    Returns:
        The civil day, or ``None``.
    """
    resulting = (
        valid_updates["settle_day"] if "settle_day" in valid_updates
        else recorded_settle_day(entry)
    )
    return resulting.day if resulting is not None else None


def update_entry(
    entry_id: int, user_id: int, *,
    press: match_press.Press | None = None,
    **kwargs,
) -> TransactionEntry:
    """Update an existing entry.

    Allowed fields: figure, description, purchased_on, settle_day, is_credit.
    Re-validates ownership on the entry's own owner column.

    **``figure`` is the figure AND who wrote it** (plan step **X-bi-3e-1**,
    ruling **R-BAL69**): a :class:`~app.services.stated_figure.StatedFigure`,
    written to ``amount`` and ``figure_source_id`` together.  Two callers
    write one -- the entry PATCH says a person did (``typed``), the statement
    matcher says the bank's line did (``observed``) -- and the source is the
    caller's word, never read off the day beside it (ruling **R-BAL61**).

    **``settle_day`` is the PAIR, not the column** (plan step **X-az**): a
    :class:`app.services.settle_day.SettleDay` carrying the day AND how that day
    is known, written to ``settled_on`` and ``settled_day_basis_id`` together by
    :func:`app.services.settle_day.record_settle_day`.  The key is not a column
    name because the value is not one column, and the three callers each state a
    different basis -- the entry PATCH ``entered``, the statement matcher
    ``observed``, and the reconcile panel writes its own ``asserted`` days
    through its bulk ``UPDATE`` rather than through this door.

    The day it carries records when the bank took the money.  For a hand-typed
    purchase that is an observation the user makes LATER -- off a statement, or
    by ticking the purchase at a balance true-up through
    :func:`app.services.reconcile_service.record_settled_days` -- which is why
    this door is where it usually arrives.  **It is no longer only this door**
    (plan step ``bank_import:X-f6a-3b``): a purchase created FROM a bank
    statement line is born carrying it, because there the observation is what
    caused the record to exist.  Passing ``None`` here clears it, putting the
    purchase back among the outstanding ones.

    Args:
        entry_id: The entry to update.
        user_id: The requesting user's ID (owner or companion).
        press: The save's press over the lines the edit form's CC caption
            named (plan step
            ``credit_card:CC-5-4a-5``, ruling **R-CC80**), or a companion's
            ``OwnerOnly`` (ruling **R-CC132**), for the payback sync -- the
            one place this door can free a line.
        **kwargs: Fields to update (must be a subset of allowed fields).

    Returns:
        The updated TransactionEntry.

    Raises:
        NotFoundError: Entry not found or not accessible.
        ValidationError: If no valid fields provided, unknown fields are
            passed, the entry is a row's or a transfer's payment record
            rather than a purchase (:func:`~._refusals._reject_settlement_record`,
            asked first), or the parent row has SETTLED and this update touches
            anything that changes what the row cost
            (:func:`_reject_settled_parent`).  An update touching only
            ``settle_day`` is admitted on a settled parent: it records when
            the bank took the purchase, not how much of it moved.  Flipping
            ``is_credit`` ON for a purchase whose account is not its row's is
            refused (:func:`~._refusals._reject_flag_beside_another_account`).
    """
    unknown = set(kwargs) - _UPDATABLE_FIELDS
    if unknown:
        raise ValidationError(
            f"Cannot update fields: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(_UPDATABLE_FIELDS))}."
        )

    valid_updates = {k: v for k, v in kwargs.items() if k in _UPDATABLE_FIELDS}
    if not valid_updates:
        raise ValidationError("No fields to update.")

    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership on the MOVEMENT's own owner column (plan step
    # ``balance:X-bi-6-4c-4``).  It read the parent row's ``user_id`` from
    # plan step ``pay_calendar:C13-b``, and walked
    # ``entry.transaction.pay_period.user_id`` before that; the two columns
    # are one value by key (``fk_transaction_entries_owner_transaction``), and
    # the movement's is the one a transfer's movement keeps once
    # ``X-bi-6-4d`` re-parents it onto the transfer and it has no row.
    owner_id = resolve_owner_id(user_id)
    if entry.owner_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # The row's own payment record is the status seam's to write (plan step
    # **X-bi-3a**); checked after ownership for the 404 reason below, and
    # FIRST among the refusals, as the delete door always has, so the refusal
    # names the act that owns it (plan step ``balance:X-bi-6-4c-4``, ruling
    # **R-BAL160**).  It stood after the settled-row refusal until then, so a
    # crafted request re-pricing a Paid row's payment record was told the
    # row's purchases are closed -- and that refusal reads the parent ROW,
    # which a transfer's payment record will not have.
    _reject_settlement_record(entry)
    # A settled row's purchases are closed to RE-PRICING (finding **N-229**),
    # and this is the door that passes what it was actually asked to write:
    # a submission touching only ``settle_day`` records when the bank took the
    # purchase and is admitted, where anything cost-bearing is refused.
    # Checked after ownership so a non-owner still gets the 404 rather than a
    # message confirming the row exists, exactly as the create door orders its
    # guards.  Only a PURCHASE reaches it, and the one door that writes a
    # purchase (:func:`create_entry`) writes it under a row, never a shadow.
    # **Ruling R-GE**: a statement's evidence may re-cost a settled
    # purchase, and what bounds the permission is the FIGURE's own stated
    # source rather than a flag (it was the settle day's basis beside the
    # figure until plan step X-bi-3e-1) -- stated once, beside the constant
    # it narrows (:func:`cost_fields_changing`).
    _reject_settled_parent(
        entry.transaction, cost_fields_changing(valid_updates),
    )
    # **Does this write flip the flag ON?**  Stated ONCE for its two readers
    # (the refusal here and ``releases_the_link`` below): a submission that
    # sets ``is_credit`` True on a purchase that did not carry it.
    flips_the_flag_on = bool(
        valid_updates.get("is_credit", False) and not entry.is_credit
    )
    # The same test the create door applies, on the ACT: a purchase on another
    # account than its row's already says how it was paid, so the flag may not
    # be flipped ON beside it (plan step ``credit_card:CC-5-2``).  The account
    # itself is not an updatable field, so only the flag can move the pair
    # here -- and only a flip to True is the act that creates it.  A stored
    # flag beside a differing account is NOT refused: a legacy flagged line
    # sits on the account its row named when it was recorded, and since ruling
    # **R-CC36** the row may move on without it, so the edit form (which posts
    # the flag on every submit) must still re-describe or re-date that line.
    _reject_flag_beside_another_account(
        flips_the_flag_on, entry.account_id, entry.transaction.account_id,
    )

    # The same boundary the create door applies, and only when the caller is
    # actually moving the date -- a partial update that leaves ``purchased_on``
    # alone must not be refused for a value it is not setting (ruling R-M).
    # Narrowed to a submission that actually SETS the figure, for the reason
    # the date rule below is: a partial update leaving ``figure`` alone must
    # not be refused for a value it is not writing.
    if "figure" in valid_updates:
        _reject_zero_amount(valid_updates["figure"].amount)
    if "purchased_on" in valid_updates:
        _reject_future_purchase_date(valid_updates["purchased_on"])
    # Both date rules are checked on the RESULT, not the submission: a
    # partial update moves one side against a stored other side, and both
    # directions can break the pair invariant.
    resulting_posting_day = _posting_day_after(entry, valid_updates)
    _reject_settled_before_purchase(
        valid_updates.get("purchased_on", entry.purchased_on),
        resulting_posting_day,
    )
    # The posting day's own bound (ruling **R-FM**, plan step X-f3b): a day the
    # bank has not reached yet would release this purchase's reservation now and
    # book its cash later.  On the RESULT for the same reason as the pair above,
    # and it therefore also re-refuses a stored forward day a partial update
    # would otherwise carry through -- of which production has none.
    _reject_future_posting_day(resulting_posting_day)

    # The two edits that make a recorded clearing fact FALSE.  Both release it
    # below; see the comment there for why releasing rather than refusing.
    releases_the_link = (
        "settle_day" in valid_updates
        and resulting_posting_day != entry.settled_on
    ) or flips_the_flag_on
    # Read BEFORE the loop below, because the loop is what makes ``entry`` the
    # after-state.
    moves_credit_total = credit_total_moves(entry, valid_updates)
    for field, value in valid_updates.items():
        # Two keys are VALUES rather than columns, and each is written through
        # the one writer of its pair.  ``settle_day`` is the day AND the basis
        # that says how the day is known, and
        # :func:`app.services.settle_day.record_settle_day` writes both (plan
        # step **X-az**).  A ``setattr`` here would put a
        # :class:`~app.services.settle_day.SettleDay` into ``settled_on`` and
        # leave the basis unwritten, which the table's own
        # ``ck_transaction_entries_settle_day_basis_pairing`` would then refuse
        # -- but the point of the branch is that the pair has exactly one writer,
        # not that the constraint would catch a second one.
        if field == "settle_day":
            record_settle_day(entry, value)
            continue
        # ``figure`` is the amount AND who wrote it (plan step **X-bi-3e-1**,
        # ruling **R-BAL61**): the source moves exactly when the figure is
        # written, and it is the caller's statement -- the PATCH's ``typed``,
        # the matcher's ``observed``.  This door used to RAISE a typed figure
        # to ``observed`` whenever a submission carried an ``observed`` day,
        # figure written or not; ruling R-BAL61 measured that premise false
        # (every bank-observed day ever written onto a settled row was a
        # day-only confirmation), so a day-only edit -- on the owner's word or
        # the bank's -- leaves the source alone: nothing about who wrote the
        # figure changed.
        if field == "figure":
            entry.amount = value.amount
            entry.figure_source_id = ref_cache.movement_figure_source_id(
                value.source,
            )
            continue
        setattr(entry, field, value)
    # **Moving the posting day RELEASES the clearing fact** (plan step X-f3a-1,
    # ruling **R-FL**).  ``reconciled_by_id`` records that a named statement was
    # seen to show this purchase ON that day; a user moving the day is
    # correcting the observation, not confirming it, and the two facts must not
    # be left to disagree.
    #
    # **Releasing rather than refusing is deliberate, and the alternative was
    # measured.**  A link whose day the date rule would not pick is
    # UNRENDERABLE while an assertion resets the ledger -- the fold emits the
    # purchase on its settle day and the correction on the statement's, so the
    # balance stops equalling what the user asserted (see
    # ``StatementCoverage._recorded_anchor_id`` for the theorem and the
    # production figure).  Refusing the edit would trap a user who is doing
    # exactly what the panel's own copy asks -- *"correct it if your statement
    # shows a different day"* -- so the day wins and the observation is dropped
    # back to UNKNOWN, where the date rule answers it exactly as it did before
    # any of this shipped.  Re-ticking on the next statement records it again.
    #
    # It fires on ANY move of the DAY, including to ``None``, which is also what
    # ``ck_transaction_entries_cleared_needs_settle_day`` requires.
    #
    # **It does NOT fire when only the BASIS moves** (plan step **X-az**).  The
    # link records that a named statement was seen to show this purchase ON a
    # named day; re-stating the same day on a better-known basis agrees with
    # that observation rather than contradicting it.  That is the case the
    # statement matcher produces when a bank line CONFIRMS a day the reconcile
    # panel had recorded as an upper bound -- the day is unchanged, the basis
    # rises from ``asserted`` to ``observed``, and releasing the link there
    # would drop a true observation for a write that strengthened it.  The
    # predicate above compares ``resulting_posting_day`` with the stored day for
    # exactly that reason, where a naive ``valid_updates["settle_day"] !=
    # <the stored pair>`` would compare the BASIS too and release on a
    # confirmation.
    #
    # **Flipping a purchase to CARD releases it too, and that arm is a 500 fix**
    # (found by X-f3b's trace, 2026-08-15).  A card purchase never touches this
    # account, so ``ck_transaction_entries_card_purchase_clears_nowhere``
    # refuses the pair -- and this door wrote the flag without touching the
    # link, so PATCHing ``is_credit`` on a purchase the reconcile panel had
    # ticked raised an unhandled ``IntegrityError``.  Reproduced on a production
    # clone against entry 87.  Releasing is the same answer for the same reason:
    # the user is correcting the observation, not confirming it, and the two
    # facts must not be left to disagree -- here they could not even be stored.
    if releases_the_link:
        entry.reconciled_by_id = None
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_UPDATED, BUSINESS,
        "Transaction entry updated",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=entry.transaction_id,
        entry_id=entry_id,
        # Sorting fields_changed keeps the structured log deterministic
        # so dashboards can group by it without ordering noise.
        fields_changed=sorted(valid_updates.keys()),
    )

    sync_entry_payback(
        entry.transaction_id, owner_id,
        moves_credit_total=moves_credit_total,
        press=press,
    )
    _resync_after_entry_change(entry.transaction)

    return entry


def delete_entry(
    entry_id: int, user_id: int, *,
    press: match_press.Press | None = None,
) -> int:
    """Hard-delete an entry.

    Re-validates ownership before deleting.  Returns the parent
    transaction_id so the caller (e.g. entry credit workflow in
    Commit 4) can sync the CC Payback amount.

    Args:
        entry_id: The entry to delete.
        user_id: The requesting user's ID (owner or companion).
        press: The save's press over the lines the X's confirmation named
            (plan step ``credit_card:CC-5-4a-5``, rulings **R-CC80** /
            **R-CC127**) -- the purchase's own and, on the last card
            purchase, its payback's -- or a companion's ``OwnerOnly`` (ruling
            **R-CC132**).  The default, ``None``, is the match Undo's:
            ``statement_match._release`` removes a purchase its act CREATED
            after withdrawing that act.

    Returns:
        int -- the parent transaction_id.

    Raises:
        NotFoundError: Entry not found or not accessible.
        ValidationError: If removing this purchase would change what a settled
            parent's own close BOOKED (:func:`_reject_settled_removal`) -- an
            undated debit under a settled row, a row recording a stored figure,
            or an archived one -- or a companion's press would free a line
            (ruling **R-CC132**).
        PageOutOfDate: When the press would free other bank lines than
            *press*'s page named.
    """
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # Re-validate ownership on the MOVEMENT's own owner column, for the
    # reason :func:`update_entry` gives (plan step ``balance:X-bi-6-4c-4``).
    owner_id = resolve_owner_id(user_id)
    if entry.owner_id != owner_id:
        raise NotFoundError(f"Entry {entry_id} not found.")

    # **A settled row's close may not be re-priced by a removal, and whether
    # this removal would re-price it is arithmetic** (plan step
    # ``bank_import:X-f6f``, ruling **R-GG**).  This passed
    # ``_COST_BEARING_FIELDS`` to ``_reject_settled_parent`` until then, which
    # refused EVERY removal from a settled row -- the exact inverse of an
    # addition ruling **R-FX** admits on the same row, and the reason 103
    # purchases a statement pass created in error had no door that removes
    # one (finding **N-333**).  ``_reject_settled_removal`` weighs what the
    # close actually booked instead.
    # The row's own payment record is withdrawn by a REVERT, never here (plan
    # step **X-bi-3a**); named first, so the refusal says which act owns it.
    _reject_settlement_record(entry)
    _reject_settled_removal(entry.transaction)

    txn = entry.transaction
    transaction_id = entry.transaction_id
    # What this delete removes from the envelope's credit sum (finding
    # **N-323**), read while the row still exists: only a CREDIT purchase
    # carrying money was ever IN that sum, so deleting a debit one cannot move
    # it.
    removed_credit = bool(entry.is_credit and entry.amount)
    # Off the books through the ONE act every door that removes a movement
    # calls (plan step ``credit_card:CC-5-4a-3``, ruling **R-CC54**): its OWN
    # cash leg reversed while ``journal_entries.transaction_entry_id`` still
    # links it (ruling **R-FM**, plan step X-f3b; that key is ON DELETE SET
    # NULL), then out of the matches naming it -- a bank line matched to this
    # purchase is no longer explained by it once it goes, so an act left
    # naming no app row is withdrawn and the line is unexplained again
    # (developer ruling 2026-08-25, plan step ``bank_import:X-gb``) -- then
    # deleted out of its envelope's ``entries``, and the anchor corrections
    # re-checked once it is gone (ruling **plan#2564**).  Its PARENT is untouched:
    # removing one purchase leaves the envelope and every other purchase in it
    # asserting exactly what they did.  The X names the line first since plan
    # step ``credit_card:CC-5-4a-5`` (ruling **R-CC80**, closing finding
    # **CC-367**), and the act compares what it named (ruling **R-CC127**).
    #
    # **The payback this press deletes leaves in the SAME act** (plan step
    # ``credit_card:CC-5-4a-5``): the act grades "the WHOLE set one press
    # removes" against what the X named, and on the envelope's account a
    # second act would meet the first's line and refuse the press as out of
    # date.  The row delete takes its payback chain so; the act reverses the
    # payback's movements with the purchase and re-checks the anchors once
    # after both are gone (ruling **plan#2564**), and the sync below deletes
    # the payback ROW, by the same arithmetic.
    doomed = payback_a_removal_deletes(txn, {entry.id})
    movements, leaving = [entry], ()
    if doomed is not None:
        movements, leaving = [entry, *doomed.entries], (doomed,)
        press = x_press_for_payback(press, txn, entry)
    movement_removal.remove_movements(
        movements, owner_id, because=match_withdrawal.LEFT_THE_BOOKS,
        press=press, rows_leaving=leaving,
    )
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_DELETED, BUSINESS,
        "Transaction entry deleted",
        user_id=user_id,
        owner_id=owner_id,
        transaction_id=transaction_id,
        entry_id=entry_id,
    )

    sync_entry_payback(
        transaction_id, owner_id, moves_credit_total=removed_credit,
        press=press,
    )
    _resync_after_entry_change(txn)

    return transaction_id


def get_entries_for_transaction(
    transaction_id: int, user_id: int,
) -> list[TransactionEntry]:
    """Return a transaction's PURCHASES, ordered by purchased_on ASC.

    Validates ownership before returning them.  The purchases and never the
    whole family (:attr:`~app.models.transaction.Transaction.purchases`,
    ruling **R-BAL68**): the status seam's covering movement is an entry the
    seam wrote for the row's own close, and the list this feeds
    (``grid/_transaction_entries.html`` through the HTMX refresh) is what
    the owner reads back as what they spent.

    Args:
        transaction_id: The parent transaction ID.
        user_id: The requesting user's ID (owner or companion).

    Returns:
        List of TransactionEntry objects ordered by purchased_on ASC.

    Raises:
        NotFoundError: Transaction not found or not accessible.
    """
    owner_id = resolve_owner_id(user_id)

    txn = load_owned_transaction(transaction_id, owner_id)

    # The entries relationship is ordered by ``purchased_on`` via the
    # ``order_by`` on ``Transaction.entries`` -- the BUDGET clock, which is
    # the order a user reads their purchases in -- and ``purchases`` keeps it.
    return txn.purchases
