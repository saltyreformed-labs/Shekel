"""
Shekel Budget App -- The TRANSACTION arm of the outstanding set

One of the package's arms (see :mod:`app.services.reconcile_service` for what
an arm is and how many there are): the SOURCE ROWS a statement can still
settle -- an envelope's own close, and a bill -- as opposed to the purchase
entries recorded against one, or a transfer's leg.  It owns the three things
an arm owns, its SCOPE, its READ and its WRITE.

**Its scope, bound and loader are :mod:`._rows`', and that is finding N-225.**
The shared half lives once, and the reader and the writer here share it
literally through :data:`ARM`'s loader, which is the security property.  What
stays here is what is genuinely this arm's: WHICH rows (``transfer_id IS
NULL``), what one is WORTH, and what a tick MEANS for it.

**It answers TWO scopes since plan step ``credit_card:CC-5-4b``, one per
account relation** (ruling **R-CC44**).  :data:`ARM` offers the rows ON this
account.  :data:`SETTLEMENT_ARM` offers a row planned on ANOTHER account whose
payment was recorded on this one and then reopened -- a bill planned on
Checking, marked paid from the card, reopened to edit, its payment KEPT on the
card with no date (rulings **R-CC42**, **R-BAL61**) -- under the panel's "Paid
from this account" section (ruling **R-CC111**).  They share this module
because the package cuts by SETTLE VERB and they have one: the same row kind,
ticked through the same :func:`_settle_one`, priced by the same
``settle_amount``, with the same amount box (ruling **R-CC110**) and the same
form fields (ruling **R-CC116**).  What differs is WHICH rows, how a row
READS (its label, :func:`_settlement_label`), and which statement a tick's
link reaches -- the payment and not the row, which ``status_seam.
record_clearing`` works out from the statement's own account.

**Its settle is a service verb, and that is the difference from the purchase
arm.**  A purchase settles by stamping one column and moves no status, so that
arm's writer is a bulk ``UPDATE``.  A transaction settles through the status
seam, an amount rule and a posting reconcile -- so this writer dispatches to
``transaction_service.settle_transaction`` per row, which is the verb the
grid's Mark Paid calls (ruling **R-FA**).  Two doors restating one money rule
is this arc's own root cause 1.

**Nothing here decides what a tick BOOKS, whether the panel may offer a box for
it, or whether a submitted figure is a CORRECTION.**  All three are the verb's,
published as ``transaction_service.settle_amount`` and
``transaction_service.settles_from_entries``, and ANSWERED BY THE ACT for the
third: ``settle_transaction`` returns whether it booked a human's figure, so the
count and the write cannot disagree.  A panel showing a
figure the verb would not book, an input for a value the verb would ignore, or
a telemetry count of corrections the verb never made, are the same defect one
tier up.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writer mutates and does NOT commit -- the caller owns the session
    boundary.
"""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    cash_ledger,
    match_withdrawal,
    status_seam,
    transaction_service,
)
from app.services.account_projection import is_revolving
from app.services.cash_ledger import AmountBasis
from app.services.match_press import Press
from app.services.match_withdrawal import SharedWithdrawal
from app.services.reconcile_service import _rows
from app.services.reconcile_service._named import SharedMatch, SharedPartner
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingGroup,
    OutstandingTransaction,
)
from app.services.stated_figure import StatedFigure
from app.utils.log_events import (
    EVT_SETTLEMENTS_RECONCILED,
    EVT_TRANSACTIONS_RECONCILED,
)


def _cash_amount(txn: Transaction, booked: Decimal) -> "Decimal | None":
    """Return what the STATEMENT shows for *txn*, or ``None`` when it is *booked*.

    Finding **N-226**, widened by ruling **R-FM**.  An envelope settles at
    ``sum(entries)`` over EVERY entry it holds, and THREE kinds of those never
    leave checking at the tick:

    * a CARD purchase marked by the flag, which leaves later through its own
      CC Payback sibling -- which is exactly why the purchase arm refuses to
      OFFER one;
    * a purchase whose money moved through ANOTHER account than the row's
      (plan step ``credit_card:CC-5-2``, ruling **R-CC15**) -- the card's
      statement shows it and the purchase arm offers it THERE; and
    * a purchase on this account that has ALREADY POSTED, whose cash left on
      its own recorded day and is already a movement of its own in the ledger
      (plan step X-f3b).

    So the figure a tick books and the figure the bank shows for it now are two
    different numbers for one row, and this screen is the one read beside a
    paper statement.

    **It prints both rather than changing what a tick books**, which is the
    only correct direction: ``actual_amount`` legitimately IS total spend, the
    posted ledger already leaves both terms out (a card purchase is a movement
    that moves nothing and a dated purchase posts on its own day,
    ``cash_ledger.movement_cash_leg``), and moving the booked figure would make
    the panel disagree with the grid and the analytics.

    Both terms are the cash ledger's own, taken as ONE published sum
    (``cash_ledger.off_statement_sum``) rather than as two additions restated
    here: one rule, one statement, so a change to what either means cannot
    leave the panel saying the old thing.  Its other caller is the statement
    matcher's corrected figure for a line (``_landing.corrected_figure``),
    which asks the same question of a bank line.

    Args:
        txn: The row being offered, with ``entries`` loaded.
        booked: What a tick would book (``transaction_service.settle_amount``).

    Returns:
        ``booked`` minus the card entries, the purchases whose money moved
        through another account (plan step ``credit_card:CC-5-2``: a card
        swipe recorded in a checking envelope) and the already-posted
        purchases on this account, when the row holds any, else ``None`` --
        which is every bill, every deposit and every envelope whose purchases
        are all debits on its own account and all outstanding.
        Production carries 18 card entries in history and ZERO on a Projected
        envelope, so the card term is latent; the posted term is LIVE, on 2 of
        the 9 posted purchases (`$45.85`) measured 2026-08-14.
    """
    not_on_this_statement = cash_ledger.off_statement_sum(txn)
    if not not_on_this_statement:
        return None
    return booked - not_on_this_statement


def _offer_kind(txn: Transaction) -> OfferKind:
    """Return the section tag this arm puts on *txn*'s offer.

    **The arm TAGS; nothing downstream derives** (see :class:`OfferKind` for
    the two defects deriving it caused, one of them a live mis-captioning of
    production's `$1,958.87` FSA reimbursement).

    Three arms of one rule, in this order:

    * INCOME is a ``DEPOSIT`` -- money arriving, which ruling **R-FD** counts
      apart from payments because a deposit and a bill do not sum to anything a
      reader wants.  Tested FIRST because an income row is never
      purchase-tracked anyway (both entry write doors are expense-only), so the
      order costs nothing and states the priority.
    * A purchase-tracked row is an ``ENVELOPE``, whether or not it currently
      holds anything.  Production's `Kayla's Spending Money` carries zero
      entries and is still an envelope; calling it a bill because it happens to
      be correctable was the renderer's proxy talking.
    * Everything else is a ``BILL``.

    Args:
        txn: The row being offered.

    Returns:
        Its :class:`OfferKind`.
    """
    if txn.is_income:
        return OfferKind.DEPOSIT
    if txn.tracks_purchases:
        return OfferKind.ENVELOPE
    return OfferKind.BILL


def _settle_one(
    txn: Transaction,
    submitted: StatedFigure | None,
    statement: _rows.Statement,
    press: Press,
) -> bool:
    """Settle one row through the grid's own verb; say if a human's figure won.

    This arm's settle, named by :data:`ARM` and by :data:`SETTLEMENT_ARM`:
    the "Paid from this account" list ticks a row through the bill's OWN door
    (ruling **R-CC44**), so both scopes share this one function and nothing
    here asks which list the row came from.
    The submitted figure is handed STRAIGHT to the verb: **this function holds
    no money rule at all**, and a first draft's two -- "read it only where the
    panel offered a box" and "only when it differs from what the row would
    otherwise book" -- were both the verb's, restated.  A review measured the
    first: deleting it left every test green, because ``settle_transaction``
    routes an entries-derived row to a branch that ignores ``actual_amount``
    outright.  A guard nothing can observe is not a guard, and two doors
    deciding one column's meaning separately is the shape this whole arc exists
    to remove.

    Args:
        txn: The row to settle, still Projected.
        submitted: The figure the panel's amount box posted, or ``None``.
        statement: The statement being reconciled; its day is what the settle
            records the money as having moved on, rather than the seam's
            default of the user's today -- and on the ``asserted`` basis, because
            what the owner asserted is a BALANCE for that day and the money was
            inside it (plan step **X-az**).  **Its ACCOUNT is where the tick
            books, by the scopes' construction** (ruling **R-CC15**: a
            statement-driven settle books on the statement's own account).
            The tick names NO tender since plan step
            ``credit_card:CC-5-4a-5c-1``; it named the statement's account
            until then, which re-pointed a kept payment from the card onto
            Checking (finding **CC-378**).  Since ruling **R-CC126** the name
            changes nothing a tick BOOKS, so it was deleted (the
            coordinator's application of R-CC126, 2026-10-04): a row that
            settles from its figure and is offered here holds no payment on
            another account, so the seam's own default
            (``status_seam.tender_account_id_of``: the kept payment's
            account, else the row's) is this account; a row that settles FROM
            its purchases books no movement of its own, and the verb's
            purchases branch never reads a tender (on such a row with a card
            payment the name differed from that default, and changed
            nothing).  So on :data:`ARM`'s scope a row settling from its
            figure books here, and a row settling from its purchases takes its
            kept payment off wherever it is; on :data:`SETTLEMENT_ARM`'s the
            kept payment is on this account (that scope's own clause), so it
            books where it is: the money is not moving, only being dated.
            The booked account is pinned for Checking's own list and the
            card's "Paid from this account" list
            (``test_cc5_4a5c_reconcile_panel::TestEachListBooksOnTheStatementsAccount``).
        press: The panel save's press, over the bank lines the panel named
            for the rows ticked (``NamedLines.for_ticks``: a shared match's
            only when all its rows are; plan step ``credit_card:CC-5-4a-5``,
            rulings **R-CC76**, **R-CC127**, **R-CC135**;
            ``_assemble.record_reconciliation``), for the act that takes the
            row's kept payment out of its matches.  After rulings
            **R-CC125** (a typed ``$0.00`` box is refused before this runs)
            and **R-CC126** (no tick here moves a payment between accounts)
            the tick the panel captions is a row
            holding purchases, which settles FROM them: that ``purchases``
            record takes the payment a revert kept
            (``status_seam._covering._withdraw``), and :data:`ARM`'s reader
            names its lines first (:func:`outstanding_transactions`) -- under
            the row alone, or, for a match naming several rows' payments,
            under each of them with the rows that must all close (leaf
            5c-2c-1, finding **CC-384**).  One tick takes a payment off with
            NO caption: a row whose own figure is ``$0.00`` ticked with its
            box CLEARED records ``$0.00`` -- refused as out of date when its
            kept payment is matched (its page named nothing), and when it is
            not, saved with that payment taken off unannounced (ledger row
            **BAL-596**, balance:X-db's).

    Returns:
        Whether the verb booked *submitted* as a correction -- **answered by the
        verb itself** (developer ruling, 2026-08-17), which is the shape
        ``transfer_service._settle.settle`` already had.  This asked
        ``transaction_service.is_correction`` separately (now private), and it
        re-resolved the row's amount to make its comparison, so one ticked row
        paid for the profile lookup, the loan resolve and the paycheck engine
        twice -- and the count and the write were two answers to one question
        (finding **N-231**), which is the shape they can no longer be.
    """
    corrected = transaction_service.settle_transaction(
        txn, submitted=submitted, settle_day=statement.settle_day, press=press,
    )
    # WHICH statement showed this row (ruling **R-FL**), recorded HERE rather
    # than inside ``settle_transaction`` -- and that placement is the rule.  The
    # verb is shared with the grid's Mark Paid, which settles a row without any
    # statement having shown it, so a link written there would record an
    # observation nobody made.  Ticking a row on this panel IS the observation.
    #
    # It follows the settle, which RELEASES any prior link as it stamps the day
    # (``status_seam``): the release is about the day that moved, and this is
    # the new day's own fact.  Through ``status_seam.record_clearing`` since
    # plan step **X-bi-3a**: the settle mirrored the row's money onto its
    # covering movement, and the link has to reach the fact that carries it.
    # That door links each fact ON THIS STATEMENT'S ACCOUNT (plan step
    # credit_card:CC-5-4b, ruling R-CC44): the row and its payment for a row
    # on this account, the payment alone for a row planned on another.
    status_seam.record_clearing(txn, statement.anchor.anchor_id)
    return corrected


def _own_clauses(statement: _rows.Statement) -> tuple:
    """Return the rows-ON-this-account scope's membership clauses, for :data:`ARM`.

    The row is on THIS account -- the account clause
    :func:`~._rows.outstanding_scope` carried as its own first clause until
    plan step ``credit_card:CC-5-4b``, which made it each scope's; its
    complement is :func:`_settlement_clauses`' first.

    A transfer is one row and settles through
    ``transfer_service.settle_transfer`` (the transfer paragraph of
    ``CLAUDE.md``'s Architecture): it is the transfer arm's
    (:mod:`._transfers`), which offers its LEG off ``budget.transfers`` since
    leaf ``balance:X-bi-6-4c-2``.  Until then a ``transfer_id IS NULL``
    clause here was that arm's complement over this table; it went with the
    column at plan step ``balance:X-bi-6-5``, since no transaction row names
    a transfer after ``balance:X-bi-6-4d-2``.

    **NOT a row whose payment moved on ANOTHER account** (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC126**, developer 2026-09-30,
    "Only the card offers it"): *"Checking's list leaves it out ... Each list
    offers only money that moved on its own account, so reconciling never
    moves a payment. A bill paid by its own purchases stays on its own
    list."*  A row holding no purchase settles from its figure, and until
    this step its tick named this account as the tender
    (:func:`_settle_one`), so ticking one whose kept payment is on the card
    RE-POINTED that payment onto Checking (``status_seam._covering.
    _re_point``) and withdrew the card's match unannounced (finding
    **CC-378**).  Left out here, it is offered by :data:`SETTLEMENT_ARM` on
    the payment's own account, which books it where it is.  A row holding
    purchases stays: it settles FROM them and never re-points.  The excluded
    payment is ANY covering movement on another account, dated or not --
    the ruling's own words are about where the money moved, not when; the
    question's example was un-dated because that is the shape a revert
    leaves, and a DATED one under a Projected row is a drift no door writes
    which this list must not re-point either (production held 0 of either
    on 2026-09-30).  So the panel reaches ``_re_point`` from neither scope.
    The clause is ``status_seam.payment_recorded_elsewhere_clause``, the ONE
    predicate the statement matcher's offer asks too since leaf 5c-2a (ruling
    **R-CC137**: *"Checking's statement screen does not offer Hotel while its
    payment is recorded on the Visa (the reconcile panel's test, R-CC126)"*).

    Args:
        statement: The statement being reconciled.

    Returns:
        The clauses, for :func:`_scope_loader`.
    """
    return (
        Transaction.account_id == statement.account_id,
        ~status_seam.payment_recorded_elsewhere_clause(statement.account_id),
    )


def _scope_loader(
    clauses: "Callable[[_rows.Statement], tuple]",
) -> "Callable[[_rows.Statement, set[int] | None], dict[int, Transaction]]":
    """Return the ``load`` of one of this arm's scopes, over *clauses*.

    Both scopes load the same way -- :func:`~._rows.outstanding_rows` over the
    scope's clauses, keyed by the id a row's tick posts (its own, under the
    SAME field for both scopes, ruling **R-CC116**) -- and differ only in
    WHICH rows, so the body is written once and each scope hands it its
    clauses (plan step ``credit_card:CC-5-4b``, whose review measured the two
    bodies identical but for the clauses).

    ``template`` is loaded here and not in the shared loader because only this
    arm reads ``tracks_purchases``, which lazy-loads a template per row
    otherwise -- an N+1 on a list the user is about to read.  The row's
    ``account``, whose name the second scope's label reads, is a joined load
    on the model.

    Args:
        clauses: ``statement -> scope clauses`` -- :func:`_own_clauses` or
            :func:`_settlement_clauses`.

    Returns:
        ``(statement, transaction_ids) -> {row id: row}``, in landing-day
        order; *transaction_ids* is the writer's narrowing, ``None`` the
        reader's "everything in scope".
    """
    def load(
        statement: _rows.Statement, transaction_ids: "set[int] | None",
    ) -> "dict[int, Transaction]":
        return {
            txn.id: txn
            for txn in _rows.outstanding_rows(
                statement,
                scope_clauses=clauses(statement),
                load_options=(selectinload(Transaction.template),),
                transaction_ids=transaction_ids,
            )
        }
    return load


#: What this arm IS (:class:`app.services.reconcile_service._rows.Arm`): what it
#: loads, how a row settles, and what it calls the act in the log.
#:
#: PUBLIC within the package: the reader below and
#: :func:`app.services.reconcile_service._assemble.record_reconciliation` both
#: name it, and it being ONE value is what stops them scoping differently.
ARM = _rows.Arm(
    load=_scope_loader(_own_clauses),
    settle=_settle_one,
    event=EVT_TRANSACTIONS_RECONCILED,
)


def _settlement_clauses(statement: _rows.Statement) -> tuple:
    """Return the "Paid from this account" scope's membership clauses.

    Ruling **R-CC44**: an UN-DATED payment on this account whose row is
    planned on ANOTHER.  :func:`~._rows.outstanding_scope` adds what every
    row scope shares -- Projected, contributing, an offerable period -- and
    :func:`~._rows.outstanding_rows` the landing-day bound, which is the one
    Checking's own list applies to the same row (ruling **R-CC118**, "Once the
    bill is due").  Three clauses of its own, each load-bearing:

    * the row is on ANOTHER account -- the complement of :data:`ARM`'s first
      clause, so the two scopes partition every row and one posted id loads
      in at most one (ruling **R-CC116**).  The row's own account's list
      leaves it out (ruling **R-CC126**, :func:`_own_clauses`), so it is
      offered here alone.
    * it holds an UN-DATED covering movement ON THIS ACCOUNT -- the payment
      the statement may show.  Un-dated, because that is the payment R-CC44
      lists: a dated one already posts and folds on its own day, so it is in
      this account's books and is not outstanding (a Projected row over a
      dated payment is a drift the seam does not write, and ticking it would
      only re-date that one movement); on this account, because a statement
      can show only this account's
      money (the tick then books where the payment already is,
      :func:`_settle_one`).
    * it holds NO purchase -- ruling **R-CC113** ("Hide it"): a row holding
      purchases settles FROM them (``transaction_service.
      settles_from_entries``), ignoring the tender and withdrawing the very
      payment this list would show; its purchases are its figure, and the
      purchase arm offers a card swipe on the card.  Stated as the verb's own
      predicate in SQL (``status_seam.holds_no_purchase_clause``, the one
      spelling :data:`ARM`'s R-CC126 clause and the statement matcher's
      R-CC137 cut are built on).

    Args:
        statement: The statement being reconciled.

    Returns:
        The clauses, for :func:`_scope_loader`.
    """
    return (
        Transaction.account_id != statement.account_id,
        Transaction.entries.any(and_(
            status_seam.covering_clause(),
            TransactionEntry.settled_on.is_(None),
            TransactionEntry.account_id == statement.account_id,
        )),
        status_seam.holds_no_purchase_clause(),
    )


#: The transaction arm's SECOND scope (plan step ``credit_card:CC-5-4b``): the
#: rows planned on another account whose kept payment is on this one.  Its own
#: :class:`~app.services.reconcile_service._rows.Arm` because it loads
#: differently and logs under its own event -- the row it settles is planned on
#: a SECOND account, so its settles are counted apart from this account's own
#: bills -- while its settle is :data:`ARM`'s own.  PUBLIC within the package
#: for :data:`ARM`'s reason.
SETTLEMENT_ARM = _rows.Arm(
    load=_scope_loader(_settlement_clauses),
    settle=_settle_one,
    event=EVT_SETTLEMENTS_RECONCILED,
)


def outstanding_transactions(
    statement: _rows.Statement, basis: "AmountBasis",
) -> "dict[int, OutstandingTransaction]":
    """Return this arm's offers, ``{transaction id: offer}``.

    The source rows this account is still holding forward on the day the
    balance was asserted: an envelope whose own close has not been ticked, and
    a bill the projection is still carrying.  Ticking one records that the bank
    moved the money by that day
    (:func:`app.services.reconcile_service._rows.record_settled`).

    **It returns a MAP keyed on the PARENT, which is what lets the assembler
    union it with the purchase arm** -- that arm keys its purchases on the same
    id, so an envelope with outstanding purchases AND an offerable close is ONE
    block carrying both, which is ruling **R-EW**'s shape.

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled -- whose
            calendar, which account, which assertion.  **Built ONCE by
            :func:`~._assemble.outstanding_set` and threaded**, rather than
            assembled here from three arguments: it gained the owner's calendar
            at pay-calendar plan step C4-a-2 (every offered row's span is
            derived from it), and three constructions of one value would be
            three chances to date one owner's rows against another's paydays.
            The same reason *basis* below is threaded, one tier down.
        basis: The PANEL's :class:`~app.services.cash_ledger.AmountBasis`,
            built ONCE by :func:`~._assemble.outstanding_set` and threaded
            (plan step X-au-j, finding **N-295**).  Every offer built its own until
            then, so K offered PAYCHECKS ran the paycheck engine K times over
            the owner's whole pay-period set -- which is finding **N-228** one
            tier up, named by ``amount_basis``'s own docstring.

    Returns:
        ``{transaction_id: OutstandingTransaction}``, insertion-ordered by
        landing day then id.  Empty for an account holding nothing overdue --
        **which is NOT the steady state, and the purchase arm's twin of this
        sentence is now wrong about the panel as a whole.**  Replayed over all
        53 Checking assertion days on production, 46 would have carried at
        least one offer, because an envelope's close is offerable for the whole
        of its own period and only closing it clears it.  Finding **N-227**
        owns whether that bound is right.

    **Each offer says what its tick would WITHDRAW** (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC76**, developer 2026-09-23:
    *"the reconcile panel on a row whose kept payment is matched ... the panel
    row says 9/24 HOTEL -$120.00 would be unexplained again before you tick
    it"*).  The one tick here that takes a payment out of its matches is a
    row that settles FROM its purchases while holding the payment a revert
    kept (:func:`_settle_one`), so those rows' payments are read through the
    act's own twin, ``match_withdrawal.pending_alone_and_together``, over the movements
    the seam's ``purchases`` record removes
    (:attr:`~app.models.transaction.Transaction.covering_movements`, which
    ``status_seam._covering._withdraw`` takes) and the verb's own branch
    predicate (``transaction_service.settles_from_entries``) -- ONE read for
    the whole list, and no query at all when no row qualifies (production
    held 0 un-dated covering movements under a Projected row on 2026-09-30,
    the census behind ruling **R-CC125**'s question).  **A match is read
    under every row it names** (leaf 5c-2c-1, ruling **R-CC135**): one this
    row's tick empties by itself is its :attr:`~._offers.OutstandingTransaction
    .withdraws`, and one naming the payments of several rows here, which only
    their ticks together empty, is a :class:`~._named.SharedMatch` under
    each of them (``match_withdrawal.pending_alone_and_together``).  Read
    alone, two rows matched together to one bank line each read as freeing
    nothing, and ticking both in one press was refused (finding **CC-384**).
    """
    rows = ARM.load(statement, None)
    pending = match_withdrawal.pending_alone_and_together({
        txn_id: txn.covering_movements
        for txn_id, txn in rows.items()
        if transaction_service.settles_from_entries(txn)
        and txn.covering_movements
    })
    offers = {
        txn_id: _offer(statement, txn, basis, kind=_offer_kind(txn))
        for txn_id, txn in rows.items()
    }
    for txn_id, each in pending.items():
        offers[txn_id] = replace(
            offers[txn_id],
            withdraws=each.alone,
            shared=_shared_matches(statement, rows, txn_id, each.shared),
        )
    return offers


def _shared_matches(
    statement: _rows.Statement,
    rows: "dict[int, Transaction]",
    txn_id: int,
    shared: "tuple[SharedWithdrawal, ...]",
) -> "tuple[SharedMatch, ...]":
    """Return the matches row *txn_id*'s tick withdraws only with other rows ticked.

    Each names the OTHER rows it needs by name, as ruling **R-CC135**'s
    warning does (*"Matched with Dining to one bank line"*), with the pay
    period beside a name another row on this list also carries -- one
    envelope in two paychecks -- since the name alone would not say which
    (the lane's application of ruling **balance:R-BAL207**, leaf 5c-2c-1,
    widened from the warning to the list at its review).

    Args:
        statement: The statement being reconciled, whose calendar files each
            row's period.
        rows: The scope's rows, in the order the panel lists them.
        txn_id: The row whose tick this is.
        shared: Its :attr:`~app.services.match_withdrawal.RemovalWithdrawals
            .shared`, whose keys are row ids of *rows*.

    Returns:
        One :class:`~._named.SharedMatch` per act, in *shared*'s order.
    """
    names = [txn.name for txn in rows.values()]
    matches = []
    for each in shared:
        matches.append(SharedMatch(
            withdrawal=each.withdrawal,
            partners=tuple(
                SharedPartner(
                    name=txn.name,
                    period=_rows.filed_period(statement, txn),
                    needs_period=names.count(txn.name) > 1,
                )
                for key, txn in rows.items()
                if key in each.keys and key != txn_id
            ),
            row_ids=frozenset(each.keys),
        ))
    return tuple(matches)


def outstanding_settlements(
    statement: _rows.Statement, basis: "AmountBasis",
) -> "list[OutstandingGroup]":
    """Return the "Paid from this account" offers, one childless block per row.

    The second scope's reader (plan step ``credit_card:CC-5-4b``, ruling
    **R-CC44**): each row planned on another account whose payment was
    recorded here and reopened, headed by its own label and listed in its
    own paycheck block -- :func:`_settlement_label`, over
    :func:`~._rows.filed_period` -- and priced, boxed and settled exactly as
    the row's own list would price, box and settle it (:func:`_offer`).

    **It returns finished BLOCKS, as the transfer arm does**, because a row
    here is always childless (the scope admits no row holding a purchase,
    ruling **R-CC113**) and headed by its own label -- so it has nothing to
    union with the purchase arm and no parent to look up.  Its key is still
    the row's id, and the row-keyed map could have held it (its scope is
    disjoint from :data:`ARM`'s by the row's account); the label is what
    that map's heading query cannot compose.

    **Whether this account is a CARD is asked once, of the account's type**
    (``account_projection.is_revolving``, the one card predicate, which reads
    the type's ``has_revolving_credit`` flag -- never a name), and only when
    there is a row to label: a card's rows read "paid from this card", every
    other account's "paid from this account" (rulings **R-CC111** /
    **R-CC117**).

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~._rows.Statement` being reconciled, built once
            by :func:`~._assemble.outstanding_set` and threaded.
        basis: The PANEL's :class:`~app.services.cash_ledger.AmountBasis`,
            built once by :func:`~._assemble.outstanding_set` and threaded,
            for :func:`outstanding_transactions`' reason.

    Returns:
        One :class:`~._offers.OutstandingGroup` per row, keyed by the row's
        id, in landing-day order; empty for an account holding none.
    """
    rows = SETTLEMENT_ARM.load(statement, None)
    if not rows:
        return []
    on_card = is_revolving(db.session.get(Account, statement.account_id))
    return [
        OutstandingGroup(
            key=txn.id,
            name=_settlement_label(txn, on_card=on_card),
            period=_rows.filed_period(statement, txn),
            purchases=(),
            # This scope admits no row holding a purchase (ruling
            # **R-CC113**) and books on the payment's own account, so no
            # tick here takes a payment out of its matches: the offer keeps
            # its default, naming no line.
            settle=_offer(statement, txn, basis, kind=OfferKind.SETTLEMENT),
            # Resolved by the assembler once the order is known.
            section=None,
        )
        for txn in rows.values()
    ]


def _settlement_label(txn: Transaction, *, on_card: bool) -> str:
    """Return a "Paid from this account" row's heading.

    The developer's wording, verbatim in shape: ruling **R-CC111** ("Groceries
    (Checking's plan, paid from this card)", and "(Checking's plan, received
    into this account)" for a deposit), and ruling **R-CC117** for an account
    that is not a card ("paid from this account", since "this card" would be
    false).  An envelope's tick is PREFIXED "Close " by the template, off
    :attr:`~._offers.OutstandingGroup.settle_closes_an_envelope` (ruling
    **R-CC119**), so this composes the row and never the act.

    The planned account's name is its CURRENT one, read through the row's
    joined ``account``, as a transfer leg's label reads its endpoints'.

    Args:
        txn: A row in the second scope, with ``account`` loaded.
        on_card: Whether the statement's account is a card.

    Returns:
        The label.
    """
    if txn.is_income:
        moved = "received into this account"
    elif on_card:
        moved = "paid from this card"
    else:
        moved = "paid from this account"
    return f"{txn.name} ({txn.account.name}'s plan, {moved})"


def _offer(
    statement: _rows.Statement, txn: Transaction, basis: AmountBasis,
    *, kind: OfferKind,
) -> OutstandingTransaction:
    """Return the offer this arm makes for one row, in either scope.

    Args:
        statement: The statement being reconciled; its calendar is what dates
            the offer (:func:`~._rows.attributed_on`).
        txn: A row in scope, with ``entries``, ``pay_period`` and ``template``
            loaded.
        basis: The panel's :class:`~app.services.cash_ledger.AmountBasis`,
            threaded from :func:`outstanding_transactions` (plan step X-au-j).
        kind: The SECTION the scope puts it in -- :func:`_offer_kind` on
            :data:`ARM`'s scope, ``SETTLEMENT`` on :data:`SETTLEMENT_ARM`'s
            (plan step ``credit_card:CC-5-4b``).  Everything else is the row's
            and is read the same way in both, so a row is worth, boxed and
            settled the same whichever list offers it.

    Returns:
        Its :class:`OutstandingTransaction`, naming no line its tick frees:
        that is the reader's to add (:func:`outstanding_transactions`, which
        reads every row's in one pass).  ``amount`` is resolved once and
        passed to :func:`_cash_amount` rather than resolved twice: the two
        figures are the same number seen two ways, and asking the verb again
        would be a second answer to one money question.  ``closes_envelope``
        is :func:`_offer_kind`'s own answer, so on :data:`ARM`'s scope it
        and ``kind`` are one classification read once each.
    """
    booked = transaction_service.settle_amount(txn, basis)
    return OutstandingTransaction(
        key=txn.id,
        attributed_on=_rows.attributed_on(statement, txn),
        amount=booked,
        cash_amount=_cash_amount(txn, booked),
        is_correctable=not transaction_service.settles_from_entries(txn),
        is_income=txn.is_income,
        kind=kind,
        closes_envelope=_offer_kind(txn) is OfferKind.ENVELOPE,
    )
