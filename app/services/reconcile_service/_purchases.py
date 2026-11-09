"""
Shekel Budget App -- The PURCHASE arm of the outstanding set

One of the package's three arms (see :mod:`app.services.reconcile_service` for
what an arm is and why there are three): the entries recorded against an
envelope that the bank has not been seen to have taken.  It owns the three
things an arm owns -- its SCOPE, its READ and its WRITE -- and they live in one
module because the scope being literally shared between the read and the write
is the security property, not a convenience.

**Its settle is the odd one of the three, and that is why it is first.**
Settling a purchase writes THREE columns on ONE row (``settled_on``, the basis
that says the day is an upper BOUND, and the statement it names) and moves no
status, so this arm's writer is a single bulk ``UPDATE`` narrowed by the same
clauses the reader selected on.  The transaction
and transfer arms settle through a status seam, so their writers dispatch to a
service verb per row (ruling **R-FA**).  Nothing here should be generalised
into a shape those two can share until they exist.

**A purchase whose posting day is recorded is a cash movement of its own**
(ruling **R-FM**, plan step X-f3b), so stamping one moves its cash onto the
statement's day in the worked-out ledger.  The arm posted it to the stored
ledger too, per FAMILY after the bulk ``UPDATE``, until plan task plan#3097
deleted that with the stored ledger's transaction doors.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

import logging

from app import ref_cache
from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import status_seam
from app.services.cash_ledger import reject_movement_before_books_open
from app.services.reconcile_service import _rows
from app.services.reconcile_service._offers import OutstandingPurchase
from app.utils.balance_predicates import balance_contributing_clause
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRIES_SETTLED_DAY_RECORDED,
    log_event,
)

logger = logging.getLogger(__name__)


def _outstanding_scope(statement: "_rows.Statement"):
    """Return the filter clauses for "not yet seen on a statement".

    The ONE definition of the outstanding PURCHASE set, shared by this arm's
    reader (:func:`outstanding_purchases`) and its writer
    (:func:`record_settled_days`) so a purchase the panel does not OFFER can
    never be stamped by a forged id -- and so the two cannot drift about what
    "outstanding" means, which is the shape this whole step exists to end.

    **"Purchase" is load-bearing in that sentence.**  Plan step X-f2-c2 adds a
    TRANSACTION twin with its own bound (``attribution_day <= observed_on``,
    applied in Python over an SQL superset), so this becomes one of two scopes
    and the sharing property has to hold per scope rather than over the set as
    a whole.  Writing "the outstanding set" here would make that leaf falsify a
    security argument instead of extending it -- and the module split is what
    makes the per-arm reading the obvious one: this function is not visible
    outside its own arm.

    Seven clauses, each load-bearing:

    * ``settled_on IS NULL`` -- the definition itself.  A purchase whose
      posting day is already recorded is not outstanding, whatever that day is.
    * the PURCHASE is on THIS account -- ``TransactionEntry.account_id``, the
      account its money moved through (plan step ``credit_card:CC-5-2``,
      rulings **R-CC15** and **R-BAL75**).  It was the PARENT's account
      through ``CC-5-1``, inside the subquery below, and the two were equal
      on every row by the key that step dropped.  A card swipe recorded in a
      checking envelope is a movement ON the card: it is what the CARD's
      statement shows and what checking's never will, so it is offered on the
      card's statement and not on checking's, while its envelope row stays
      where it was filed.  ``_refresh_stamped_purchases`` re-scopes the STAMPED
      set by the movement's account already; the offer and the tick now ask
      one column.
    * ``NOT covers_settlement`` -- a PURCHASE, never the row's own payment
      record (plan step ``balance:X-bi-3e-2``, ruling **R-BAL68**).  The
      status seam's covering movement is a ``transaction_entries`` row too,
      and since that step a revert KEEPS it, un-dated, under a Projected
      parent -- every other clause here admits it.  Offered, it would be a
      phantom purchase of the row's whole close (a `$100.00` envelope
      reverted after a typed `$120.00` close would offer `$120.00`), and
      ticking it would date and post the seam's own mirror around the seam.
      Stated through ``status_seam.covering_clause()``, the query-side twin
      of :attr:`~app.models.transaction.Transaction.purchases`, so this
      scope and the fold's reservation exclude the mark by one spelling.
    * ``is_credit IS FALSE`` -- a credit-card purchase never touches checking;
      it leaves through its own CC Payback sibling, so it is not on this
      account's statement and reconciling it would mean nothing.  The CHEAT's
      spelling of the account clause above, for the legacy lines that carry
      the flag with no account of their own; ``CC-7`` deletes it.
    * ``purchased_on <= observed_on`` -- a purchase made AFTER the day the
      balance was read cannot be inside it.  Offering one would let the user
      record a posting day earlier than the purchase, which
      ``ck_transaction_entries_settled_not_before_purchase`` refuses at the
      database; filtering here means that constraint is a backstop rather than
      a reachable 500.
    * the parent is filed in one of :attr:`~._rows.Statement.owned_period_ids`
      -- a balance assertion declares the real balance of one account, and a
      user may hold more than one checking account (there is no per-type
      uniqueness), so WHICH account is the purchase's own clause above, and
      this one is the OWNER's: the parent sits in the owner's calendar.
      Reconciling across owners is unrepresentable by the movement's own
      owner key too; this clause is what keeps the parent inside the owner's
      calendar, which the labelling below reads.
      **The owner half came off a ``pay_periods.user_id`` join until
      pay-calendar plan step C4-a-2** and comes off the CALENDAR now, for the
      reason that property states: it is what makes
      :func:`~._assemble._block_headings`' span lookup total, and that function
      labels the parents THIS arm produces as well as the source-row arms'.
      Scoping two arms by the calendar and one by the table would have left the
      refusal reachable through the third.
      **The UNBOUNDED set, deliberately, and MEASURED rather than argued**:
      this arm's day bound is on the ENTRY's ``purchased_on``, never on the
      parent's period, so a purchase made today against NEXT period's envelope
      is offerable and :attr:`~._rows.Statement.offerable_period_ids` -- the
      narrower set the source-row arms take -- would silently drop it.  Planted
      on a prod-shape clone 2026-08-28: a `$61.20` purchase dated 2026-08-17
      against a Groceries envelope filed in the period OPENING 2026-08-27 is
      offered against the 2026-08-18 statement, on this tree and on the merge
      base alike.  Worth planting because the clone carries no outstanding
      purchase of its own, so the whole before/after would have diffed clean
      over an arm it never ran.
    * the parent is CONTRIBUTING -- a Credit or Cancelled parent's purchases
      are not money this account owes, and a soft-deleted one's are not money
      at all (``movement_cash_leg`` and ``_events.settled_cash_facts`` both
      zero the whole family for such a row).  Routed through the shared
      ``balance_contributing_clause`` so this filter and the plan loader cannot
      come to disagree about which parent rows exist at all -- the soft-delete
      half was a hand-written ``is_deleted.is_(False)`` until plan step X-f2-c2.

    **The parent is NOT required to be PROJECTED, and dropping that clause is
    the developer's 2026-08-17 ruling** (plan step X-au-c3).  It was here on the
    premise that "the entry reservation prices only projected rows
    (``cash_ledger._amounts._entry_aware_amount``), so an entry on a settled
    parent is inert" -- and ruling **R-FM** had already falsified it one step
    earlier: the row's leg subtracted every POSTED purchase from a settled
    row's close, so recording a posting day on such a purchase moved its cash
    out of the close's day and onto the bank's (since plan step
    ``balance:X-bi-4a`` the row books nothing and the same day moves the
    purchase from in flight onto the bank's day, ruling **R-BAL77**).  Nothing
    is created or destroyed by that -- the two terms always sum to the row's
    whole debit total -- but
    the DAY is what a statement reconciles against, which is this panel's entire
    subject.  Measured on the 2026-08-17 production dump: 28 closed envelopes
    hold 61 debit purchases with no posting day, ``$4,360.07``, none of which
    this panel would ever have offered.  ``entry_service._doors``'s field-aware
    refusal is the write side of the same ruling.

    Not scoped by scenario_id: transactions are scenario-scoped, but Phase 1 is
    baseline-only (every transaction lives in the single baseline scenario), so
    account_id fully isolates the set today.  When what-if scenarios land
    (Phase 3), the callers must thread an operating-scenario context in here
    too -- the same deferral ``clear_entries_for_anchor_true_up`` carried.

    Args:
        statement: The :class:`~._rows.Statement` being reconciled -- whose
            calendar, which account, which assertion.  It replaced the three
            loose values at pay-calendar plan step C4-a-2; see
            :func:`~._transactions.outstanding_transactions` for why one value.

    Returns:
        A list of SQLAlchemy filter clauses to apply to a
        :class:`~app.models.transaction_entry.TransactionEntry` query.
    """
    return [
        TransactionEntry.settled_on.is_(None),
        TransactionEntry.account_id == statement.account_id,
        ~status_seam.covering_clause(),
        TransactionEntry.is_credit.is_(False),
        TransactionEntry.purchased_on <= statement.observed_on,
        TransactionEntry.transaction_id.in_(
            db.session.query(Transaction.id).filter(
                Transaction.pay_period_id.in_(statement.owned_period_ids),
                balance_contributing_clause(),
            )
        ),
    ]


def outstanding_purchases(
    statement: "_rows.Statement",
) -> "dict[int, list[OutstandingPurchase]]":
    """Return this arm's offers, ``{parent transaction id: purchases}``.

    Debit purchases made on or before *observed_on* whose posting day has never
    been recorded, so the projection is still holding their whole envelope
    budget back.  Ticking one is what tells the app the bank has taken the
    money (:func:`record_settled_days`).

    **This is the question a stored ``is_cleared`` flag used to answer by
    guessing.**  The flag was written by a bulk UPDATE at every true-up over
    "every entry dated on or before the SERVER's today", so a purchase recorded
    after the true-up was never reconciled and one recorded before always was,
    whether or not the bank had taken either.  The list this returns is the
    same question asked of the user, who is holding the statement.

    **It returns a MAP rather than the assembled blocks, and the seam is where
    the arms meet.**  Labelling a parent, ordering the blocks and totalling the
    set are the same work whatever produced the offers, so they belong to
    :mod:`app.services.reconcile_service._assemble`; deciding what this account
    still owes in PURCHASES is this arm's alone.  Plan steps X-f2-c2 and
    X-f2-c3 add a sibling arm each, returning their own offers against the same
    parent key, and the assembler unions them -- which is the whole reason the
    key here is the PARENT's id and not the entry's.

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled, built
            ONCE by :func:`~._assemble.outstanding_set` and threaded to all
            three arms (pay-calendar plan step C4-a-2).  Its ``observed_on``
            is the civil day the balance was true for -- purchases made
            after it cannot be inside it and are not listed.

    Returns:
        ``{transaction_id: [OutstandingPurchase, ...]}``, INSERTION-ORDERED by
        each parent's oldest outstanding purchase and with each parent's list
        oldest first, the entry id breaking a same-day tie deterministically.
        Both orderings are the caller's contract and both come from the single
        ``ORDER BY`` below rather than from a second sort.  Empty for an
        account with nothing outstanding, which is THIS ARM's steady state for
        a user who reconciles at every true-up -- and is no longer the PANEL's,
        because plan step X-f2-c2 added an arm that offers source rows; see
        :func:`app.services.reconcile_service._transactions.outstanding_transactions`.
        No list is ever empty: a parent appears only because a purchase put it
        there.
    """
    rows = (
        db.session.query(TransactionEntry)
        .filter(*_outstanding_scope(statement))
        .order_by(TransactionEntry.purchased_on, TransactionEntry.id)
        .all()
    )

    # One pass, insertion-ordered: ``rows`` is already sorted oldest purchase
    # first, so the FIRST time a parent is seen is its oldest outstanding
    # purchase -- which is the block order the docstring promises, obtained
    # from the sort that is already there rather than from a second one.
    blocks: "dict[int, list[OutstandingPurchase]]" = {}
    for row in rows:
        blocks.setdefault(row.transaction_id, []).append(
            OutstandingPurchase(
                entry_id=row.id,
                purchased_on=row.purchased_on,
                description=row.description,
                amount=row.amount,
            )
        )
    return blocks


def _refresh_stamped_purchases(
    account_id: int, entry_ids: "set[int]", anchor_id: int,
) -> None:
    """Re-read every purchase the tick just stamped into the session.

    The bulk ``UPDATE`` runs with ``synchronize_session=False``, so a
    :class:`~app.models.transaction_entry.TransactionEntry` the session
    already holds keeps its pre-update ``settled_on``, basis and link;
    ``populate_existing()`` refreshes exactly the rows this act stamped, so
    every later reader in the act's session sees the day just written.  That
    is not hypothetical: measured 2026-11-09 over the 28 test files that reach
    this writer, 11 of 15 stamps found every stamped purchase already loaded.
    It also POSTED each stamped family's cash leg to the stored ledger until
    plan task plan#3097 deleted that half (ruling **R-FM**'s posting).

    The filter re-derives WHICH rows landed from the database rather than from
    *entry_ids*: an id the scope rejected was never stamped.
    ``reconciled_by_id == anchor_id`` identifies them exactly -- an entry inside
    :func:`_outstanding_scope` carries no ``settled_on``, and
    ``ck_transaction_entries_cleared_needs_settle_day`` makes a link without
    one unwritable, so every entry naming this statement now was stamped here.
    The account clause is redundant by the composite key
    ``fk_transaction_entries_reconciled_by`` and is written anyway: every read
    in this arm re-scopes on principle rather than on an argument.

    Args:
        account_id: The cash account the balance was asserted for.
        entry_ids: The ids the user ticked (the superset the ``UPDATE`` was
            scoped against).
        anchor_id: The governing assertion's id, just written onto the stamped
            rows.
    """
    (
        db.session.query(TransactionEntry)
        .populate_existing()
        .filter(
            TransactionEntry.id.in_(entry_ids),
            TransactionEntry.account_id == account_id,
            TransactionEntry.reconciled_by_id == anchor_id,
        )
        .all()
    )


def record_settled_days(
    statement: "_rows.Statement",
    entry_ids: "set[int]",
) -> int:
    """Record that *statement* showed *entry_ids*.

    This arm's writer: the user ticked these purchases off a statement, so each
    one records WHICH statement showed it (``reconciled_by_id``, ruling
    **R-FL**) and takes that statement's day as its ``settled_on``.

    **Three columns, three facts, and the second is why this step exists.**
    ``settled_on`` is an UPPER BOUND on the true posting day -- the purchase may
    have cleared a day or two earlier, and a user who wants the exact day edits
    the entry.  ``reconciled_by_id`` is not a bound at all: it is the
    observation, and it is what the clearing rule reads first.  The day alone
    could not carry it -- production holds three days on which Checking has more
    than one assertion, so no rule over ``settled_on`` can name which statement
    a tick was made against.

    **``settled_day_basis_id`` is the third, and it is plan step X-az** (finding
    **N-332**).  It records that this day is a BOUND, in the row itself, where
    the sentence above used to be the only statement of it and every reader had
    to re-derive the fact from ``reconciled_by_id`` being populated.  That
    inference was exact over the three writers of ``settled_on`` and blind to
    the third of them, and it is the shape **N-241** deleted one column over.

    **Every id is re-scoped through :func:`_outstanding_scope` rather than
    trusted.**  The ids arrive from a form, so an id belonging to another
    user, another account, a credit purchase, a NON-CONTRIBUTING parent
    (Credit, Cancelled, soft-deleted) or an already-reconciled entry simply
    does not match and is silently skipped.  A SETTLED parent's purchase is no
    longer in that list and that is the developer's 2026-08-17 ruling: it
    matches, and recording its day is the point.  Skipping is --
    the same "404 for both not-found and not-yours" posture the ownership
    helpers take, expressed as a filter because this is a set operation.
    The count returned is what actually changed, not what was asked for.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        statement: The :class:`~._rows.Statement` being reconciled -- whose
            purchases these must be, which account, and which assertion.
        entry_ids: The entry ids the user ticked.  An empty set is a no-op.
            Its anchor id is what each ticked purchase records, and its
            ``observed_on`` is
            the day each is recorded as having settled by.

    Returns:
        The number of entries actually stamped.

    Raises:
        ValidationError: When *statement*'s day is on or before the account's
            opening day (from
            :func:`app.services.cash_ledger.reject_movement_before_books_open`,
            plan step X-f3c-2b).  Refused whole rather than per entry: the day
            being stamped is the STATEMENT's, so either every tick lands inside
            the opening equity or none does.
    """
    if not entry_ids:
        return 0
    account_id = statement.account_id
    observed_on = statement.observed_on
    # **The boundary the bulk UPDATE below cannot inherit** (plan step
    # X-f3c-2b, finding **N-378**).  Every other writer of ``settled_on``
    # reaches the column through ``settle_day.record_settle_day``, which asks
    # this same function; this arm writes by ``query.update()`` and has no ORM
    # instance to hand it, so it asks for itself.  Reachable rather than
    # theoretical: an assertion's ``observed_on`` is bounded below only by
    # ``pay_period_service.earliest_recordable_day``, which on the developer's
    # own data is 2026-03-26 -- the very day Checking's books open -- so a
    # statement recorded for that day would stamp every ticked purchase inside
    # the opening equity.  Asked BEFORE the UPDATE so a refused act writes
    # nothing, and asked ONCE rather than per entry because the day is the
    # statement's, not the purchase's.
    reject_movement_before_books_open(account_id, observed_on)

    # ``synchronize_session=False``, which CLOSES finding **N-223**, and the
    # reasoning that briefly argued the other way is recorded because it was
    # wrong in an instructive way.
    #
    # The rationale this inherited from ``entry_service`` -- "later code in the
    # same request (the grid re-rendering its projection)" -- was FALSE: that
    # re-render is a SEPARATE request raised by ``HX-Trigger: balanceChanged``,
    # and the route's own panel re-render happens after a ``commit()`` that
    # expires the identity map anyway.
    #
    # Plan step X-f2-c2 then put a SECOND writer after this one in the same
    # session -- the transaction arm, which ``selectinload``s the same parents'
    # ``entries`` -- and it looked as though ``'fetch'`` had finally earned
    # itself.  MEASURED, it has not: that arm reads an entry's ``amount`` and
    # the relationship's truthiness and never its ``settled_on``, and a probe
    # of the identity map at this statement in the POST counted **ZERO**
    # ``TransactionEntry`` objects.  A service caller can hold some (measured
    # 2026-11-09 in the suite), which :func:`_refresh_stamped_purchases`
    # re-reads after the statement.
    # **THREE columns, and the third says what KIND of day the first is** (plan
    # step **X-az**, finding **N-332**).  ``asserted`` is not a nicety: this
    # writer's own docstring calls the day an UPPER BOUND, and until this step
    # the only way a reader could tell was to test whether ``reconciled_by_id``
    # was populated -- which is exactly the "infer a fact from a column being
    # populated" shape finding **N-241** deleted one column over (the row's
    # ``settled_basis_id``, itself retired at plan step ``balance:X-bi-4b-2``
    # for the movement's ``figure_source_id``).  The statement matcher is that
    # reader, and reading
    # the bound as an observation cost 50 duplicate purchases worth
    # ``$3,590.00`` before ``f633d46a``.
    #
    # The pair is written HERE rather than through
    # ``settle_day.record_settle_day`` because this arm's writer is a bulk
    # ``UPDATE`` by design (see the module docstring): there is no ORM instance
    # to hand that function.  ``ck_transaction_entries_settle_day_basis_pairing``
    # is what makes a statement that wrote one column and not the other fail
    # loudly rather than leave a day nobody can classify.
    asserted_basis_id = ref_cache.settled_day_basis_id(
        SettledDayBasisEnum.ASSERTED,
    )
    updated = (
        db.session.query(TransactionEntry)
        .filter(
            TransactionEntry.id.in_(entry_ids),
            *_outstanding_scope(statement),
        )
        .update(
            {
                TransactionEntry.settled_on: observed_on,
                TransactionEntry.settled_day_basis_id: asserted_basis_id,
                TransactionEntry.reconciled_by_id: statement.anchor.anchor_id,
            },
            synchronize_session=False,
        )
    )

    if updated:
        _refresh_stamped_purchases(
            account_id, entry_ids, statement.anchor.anchor_id,
        )
        log_event(
            logger, logging.INFO,
            EVT_ENTRIES_SETTLED_DAY_RECORDED, BUSINESS,
            "Outstanding purchases confirmed against a bank statement",
            user_id=statement.owner_id,
            account_id=account_id,
            observed_on=observed_on.isoformat(),
            recorded_count=updated,
            requested_count=len(entry_ids),
        )

    return updated
