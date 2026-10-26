"""Which of the app's rows a bank line could be or become, priced as it sees them.

The OFFER-SET half of the matcher.  Two questions, and they are here together
because they are the same question about two acts:

* :func:`candidates_for` -- *what has this account recorded that a statement
  could be showing*, over the four subjects the app holds a cash movement
  as (:class:`~._subjects.RowKind`), each priced with its SIGNED effect on the
  account so a comparison against ``bank_statement_lines.amount`` is a
  subtraction rather than a sign negotiation;
* :func:`~._destinations.destinations_for` -- *what budget line could a
  statement line BECOME a purchase against*, which is ruling **R-FS**'s third
  shape.  **In its own module since plan step ``balance:X-bi-7b``**, when
  this one crossed the 1,000-line bound (ruling **balance:R-IR**); the seam is
  this paragraph's, and the pass still derives the two together.

**Both are ONE scope shared by the screen that offers and the door that
writes**, which is the security property ``reconcile_service`` is built on: a
row these do not return cannot be reached by crafting a request, and a row they
do return cannot be refused by the write door for being out of scope.

**What is ALREADY SPOKEN FOR is not part of that scope, and separating the two
is what makes a batch safe** (plan step ``bank_import:X-f6a-3c-2``).  These two
producers answer what an account COULD offer, which does not change while a
review pass runs; :func:`matched_subjects` answers what a match has already
claimed, which is exactly what the pass changes.  So the pass derives the offer
sets ONCE -- 3.6 s on the developer's own account -- and every act inside it
re-reads the claims for itself and narrows through :func:`unmatched_rows`
(and the destinations through ``_destinations.current_destinations``, which
re-asks the rows themselves).  Stating the narrowing once, outside the
producers, is what stops a snapshot offering a row an earlier item in the same
pass has just matched.

**The MOVEMENT is the subject of every settled match, and a row is a
candidate only while it is Projected** (plan step ``credit_card:CC-5-4a-1``,
ruling **R-CC43**, developer 2026-09-21).  A settled row's money is its
covering movement (ruling **R-BAL80**: the fold reads movements and no row),
so the movement is what a statement shows and what a match names -- on
WHICHEVER account the movement is on, which since ``credit_card:CC-5-3`` may
be another account than the row's (a bill on checking paid FROM the card
holds its movement on the card).  Offering the ROW for it, as this module did
through ``CC-5-3``, left such a bill with no subject anywhere: the member key
held a row member to the row's account, and checking's feed never shows the
money.  So :func:`_settlement_candidates` offers every covering movement on
the screen's account, :func:`_transaction_candidates` offers only the
Projected rows, and the two arms PARTITION on one predicate
(:func:`~._valuation.row_is_offered_here`): a Projected row is offered as itself on its
own account, and its kept movement -- a reverted row's, un-dated (ruling
**R-CC42**) -- is offered where the row is not.  **Since plan step
``credit_card:CC-5-4a-5`` (leaf 5c-2a, ruling R-CC137) a Projected row whose
kept payment is on ANOTHER account is not offered as itself either**: its
payment is the payment's account's subject (offered there while no act
names it), and the row's own screen names it instead
(:class:`~._subjects.HeldElsewhere`) unless it is worth ``0.00`` or cannot
be priced.  Two guards each keep a match from MOVING a payment between
accounts: withholding the row (no offered member reaches the settle with
a payment elsewhere), and :func:`~._moving._apply_day` naming no tender --
the structural one, which no test can grade while the first stands,
since nothing offered reaches it.  Withholding is also what makes a stale
page refused by name ("no longer available") rather than by the version
check, and a fresh accept never fail after its own settle.  Every accepted act therefore
names movements: the acts recorded before this step named rows until plan
step ``credit_card:CC-5-4a-2`` re-keyed each onto its row's covering movement
and dropped the row column (ruling **R-CC45**, migration ``2eabfa596ee0``).

**A still-planned TRANSFER is offered as its LEG, and a paid one's side as its
leg's covering movement** (leaf ``balance:X-bi-6-4c-1``, rulings **R-BAL87**,
**R-BAL106**, **R-BAL159**): :func:`_leg_candidates` offers each Projected
transfer's side on this account from ``transfer_legs.offerable_transfer_legs``
(keyed by the transfer on this screen), and :func:`_leg_settlement_candidates`
each paid side's movement from ``transfer_legs.recorded_transfer_legs`` -- the
owner, live, status and period clauses read off the TRANSFER in both.  The two
row arms still carry ``transfer_id IS NULL``, which excludes nothing now that
no row can name a transfer (``ck_transactions_names_no_transfer``): through
leaf ``X-bi-6-4c-2`` a transfer was offered as its SHADOW row on this
account, a TRANSACTION, and its paid side as the shadow's movement joined to
the shadow, which ``X-bi-6-4d`` -- deleting the shadows -- would have left
offering nothing.  The two leg arms partition by the movement (ruling **R-BAL79**): a
side is a LEG while its transfer is Projected and its money has not moved,
and its DATED movement is the subject once it has (ruling **R-BAL80**).  Where
the row arms offer an un-dated kept movement (a row reverted after a
card-tendered settle, ruling **R-CC42**), the leg arms offer none: under a
Paid or Received transfer that record is a state no door writes, and an
Apply of it would date it on the wrong day
(:func:`~._valuation.leg_settlement_candidate`).

**What one candidate is WORTH is** :mod:`._valuation` **'s, in its own module
since plan step ``credit_card:CC-5-4a-1``** (this one crossed the 1,000-line
bound, ruling **balance:R-IR**; the seam is by subject, the one
:func:`~._valuation.repriced` had always stated -- the scope answers WHICH
rows an act may reach, the valuation what one is WORTH).  The five arms here
decide which rows exist and may be offered and build each through that
module's one constructor per kind (a LEG's in :mod:`._leg_valuation`); pricing
is the cash ledger's and the settle verbs', and is not restated in either.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import contains_eager, joinedload, selectinload

from app.extensions import db
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, status_seam, transfer_legs
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)

from ._leg_valuation import leg_candidate, leg_loads, leg_price
from ._subjects import CandidateRow, Candidates, HeldElsewhere, RowKind
from ._valuation import (
    held_elsewhere_of,
    leg_settlement_candidate,
    purchase_candidate,
    settlement_candidate,
    settlement_price,
    transaction_candidate,
    transaction_price,
)

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCalendar


@dataclass(frozen=True)
class MatchedSubjects:
    """What an account's accepted matches have already claimed.

    **The fact a review pass CHANGES, held apart from the facts it does not**
    (plan step ``bank_import:X-f6a-3c-2``).  The offer sets beside it are
    derived once for a whole pass because nothing in the pass can add a row to
    them; this one is re-read by every act, because every act adds to it.

    It is also ONE query where there were two: :func:`candidates_for` read the
    account's members to exclude what it had claimed, and the accept door read
    them again to see an envelope whose purchase another match names.  Those
    are the same three sets, so a caller reads them once and threads them.

    **A row is CLAIMED through its PAYMENT** (plan steps
    ``credit_card:CC-5-4a-1`` / ``CC-5-4a-2``, rulings **R-CC43**,
    **R-CC45**): every act names the row's covering movement rather than the
    row, so a Projected row whose kept movement an act still names is not
    offered again (the act shows on the register as no longer holding, with
    its Undo).  :attr:`transactions` holds the parents of the covering
    movements among :attr:`entries` -- the ACCOUNT's own acts, every set here
    the account's.  **It read the OWNER's acts until plan step
    ``credit_card:CC-5-4a-5``** (leaf 5c-2b, finding **CC-385**): a payment
    may sit on another account than its row since ``credit_card:CC-5-3``, and
    a reverted card-paid bill was once offered on checking while the card's
    act named its payment.  Since leaf 5c-2a (ruling **R-CC137**) such a bill
    is withheld on Checking whether or not an act names its payment
    (:func:`_transaction_candidates`), so every row a screen offers has its
    payment, if any, on that screen's account, where the account's own acts
    claim it; and the two purchase readers the owner-wide reach still decided
    refused an envelope whose lump an act names after the envelope was set
    back to Projected -- falsely, because the revert keeps that lump UN-DATED
    and it counts nothing (ruling **R-CC141**, and the developer's
    2026-10-05 answer allowing the match).  :attr:`lines` and :attr:`entries`
    are the account's: a line belongs to one account, and a movement is
    offered only where it is.

    **A transfer's LEG is claimed through its movement too** (leaf
    ``balance:X-bi-6-4c-1``): :attr:`legs` holds the transfers whose side on
    THIS account an act names through that side's covering movement -- a
    reverted transfer's kept, un-dated one -- so its still-planned leg is not
    offered again while the act stands.  The account's own acts suffice, as
    they do for a row's claims since finding **CC-385**: a transfer's
    movement is always on its own side's account (``transfer_service`` names
    no tender), and the member key holds a member to the act's account.
    Keyed by the transfer alone because the account is this set's (ruling
    **R-BAL159**).

    Attributes:
        lines: The ``bank_statement_lines`` ids a match already explains.
        transactions: The ``transactions`` ids whose covering movement a
            match on this account already names.
        entries: The ``transaction_entries`` ids a match already names, a
            purchase's or a covering movement's.
        legs: The ``transfers`` ids whose side on this account a match
            already names through that side's covering movement.
    """

    lines: "frozenset[int]"
    transactions: "frozenset[int]"
    entries: "frozenset[int]"
    legs: "frozenset[int]"


def matched_subjects(account_id: int) -> MatchedSubjects:
    """Return every subject *account_id* has already matched, by kind.

    One statement over ``statement_match_members`` rather than three: the
    table's rows are an exclusive arc, so a single scan of the account's
    members partitions itself.

    **Every membership is a claim, with no filter** (plan step
    ``credit_card:CC-5-4a-4``, ruling **R-CC54**: "The screens'
    leftover-match check is deleted").  An act that had lost its last
    movement kept its bank line alone, and this scan filtered such an act's
    members out (``act_still_names_a_row``) so the line would not read as
    explained forever.  That act is unrepresentable now rather than
    filtered: the member's movement key is NO ACTION
    (``fk_statement_match_members_entry_account``), so no statement can
    destroy a movement an act names, and the ONE act that takes a movement
    off the books (:mod:`app.services.movement_removal`) takes it out of its
    matches first, withdrawing an act it leaves naming no movement.  The
    migration that flipped the key (``c4a4e7d1b9f2``) refused to run over
    any act already in that state.

    Args:
        account_id: The account whose matches to read.

    Returns:
        Its :class:`MatchedSubjects`.
    """
    rows = (
        db.session.query(
            StatementMatchMember.bank_statement_line_id,
            StatementMatchMember.transaction_entry_id,
        )
        .filter(StatementMatchMember.account_id == account_id)
        .all()
    )
    entries = frozenset(row[1] for row in rows if row[1] is not None)
    return MatchedSubjects(
        lines=frozenset(row[0] for row in rows if row[0] is not None),
        transactions=_claimed_rows(entries),
        entries=entries,
        legs=_claimed_legs(entries),
    )


def _claimed_rows(entries: "frozenset[int]") -> "frozenset[int]":
    """Return the rows one of *entries* is the covering movement of.

    :attr:`MatchedSubjects.transactions`, read off the account's matched
    entries as :func:`_claimed_legs` reads the legs: a parent is a claim only
    where its entry is a payment record (ruling **R-CC43**; every member since
    migration ``2eabfa596ee0``, ruling **R-CC45**) -- a purchase member's
    parent is NOT a claim on the envelope, whose figure is its purchases.

    **A transfer side's payment names no row** -- it is filed under its
    transfer's side (plan step ``balance:X-bi-6-4d-2``, ruling **R-BAL88**),
    so its ``transaction_id`` is ``NULL`` and the query asks for a row
    parent -- and it is claimed through :attr:`MatchedSubjects.legs` and its
    movement's id instead (leaf ``balance:X-bi-6-4c-1``).  Until that step it
    hung off a shadow row and added the shadow's id here, which no
    candidate's :attr:`~._subjects.CandidateRow.transaction_id` could equal.

    Args:
        entries: The ``transaction_entries`` ids this account's acts name.

    Returns:
        The claimed ``transactions`` ids; empty without a query when
        *entries* is.
    """
    if not entries:
        return frozenset()
    rows = (
        db.session.query(TransactionEntry.transaction_id)
        .filter(
            TransactionEntry.id.in_(entries),
            TransactionEntry.transaction_id.isnot(None),
            status_seam.covering_clause(),
        )
        .all()
    )
    return frozenset(row[0] for row in rows)


def _claimed_legs(entries: "frozenset[int]") -> "frozenset[int]":
    """Return the transfers whose side one of *entries* is the recorded movement of.

    :attr:`MatchedSubjects.legs`, read through ``transfer_legs`` (leaf
    ``balance:X-bi-6-4c-1``): which of the account's matched entries is a
    transfer leg's record, and of which transfer.  The account needs no
    clause of its own -- every entry an act on this account names is on this
    account (the member key), and a transfer's movement is on its own side's.

    Args:
        entries: The ``transaction_entries`` ids this account's acts name.

    Returns:
        The ``transfers`` ids; empty without a query when *entries* is.
    """
    if not entries:
        return frozenset()
    return frozenset(
        leg.transfer.id
        for leg in transfer_legs.recorded_transfer_legs(
            TransactionEntry.id.in_(entries),
        )
    )


def unmatched_rows(
    candidates: Candidates, matched: MatchedSubjects,
) -> "list[CandidateRow]":
    """Return the candidate rows no accepted match has claimed.

    **The ONE statement of "an already-matched row is not offerable"**, applied
    by the screen against the claims it read and by each write door against the
    claims IT read.  ``uq_statement_match_members_*`` would refuse a second act
    on one anyway; narrowing here is what stops the screen offering a row whose
    acceptance is guaranteed to fail, and what stops a shared offer set handing
    a second act a row the first act in the same pass has just claimed.

    It is a filter over an already-derived set rather than a clause inside the
    query for exactly that reason: the query is run once per pass and the claims
    move within it.

    **A row is claimed through its PAYMENT** (plan step
    ``credit_card:CC-5-4a-1``, ruling **R-CC43**): a TRANSACTION candidate --
    a Projected row, perhaps a reverted one whose kept movement an act still
    names -- is claimed when its row is in
    :attr:`MatchedSubjects.transactions`, and a SETTLEMENT by its movement's
    own id.
    A PURCHASE is claimed by its own id alone: its envelope is a container,
    never named by it.  A LEG is claimed when an act names its side's
    covering movement (:attr:`MatchedSubjects.legs`), and a leg's payment by
    its movement's own id, as any SETTLEMENT is.

    Args:
        candidates: The pass's derived offer set.
        matched: The claims as of NOW.

    Returns:
        The rows still offerable, in *candidates*' own order.
    """
    return [
        row for row in candidates.rows
        if not _is_claimed(row, matched)
    ]


def _is_claimed(row: CandidateRow, matched: MatchedSubjects) -> bool:
    """Return whether an accepted act already names *row*, or its row's or leg's payment."""
    if row.kind.names_an_entry and row.row_id in matched.entries:
        return True
    if row.kind is RowKind.LEG:
        return row.row_id in matched.legs
    return (
        row.transaction_id is not None
        and row.transaction_id in matched.transactions
    )


def _transaction_candidates(
    account_id: int, calendar: "PayCalendar",
    period_ids: "Collection[int]",
    basis: "cash_ledger.AmountBasis",
) -> "tuple[list[CandidateRow], list[tuple[RowKind, int]], tuple[HeldElsewhere, ...]]":
    """Return the PROJECTED transactions on *account_id* a statement could be showing.

    Scope, and every clause is load-bearing:

    * the row is on THIS account -- a statement is one bank's record of one
      account, and matching across accounts would book money against a
      statement that never showed it;
    * it is PROJECTED (plan step ``credit_card:CC-5-4a-1``, ruling
      **R-CC43**): a settled row's money is its covering movement, offered by
      :func:`_settlement_candidates` on whichever account the money moved
      through, so the row itself is a candidate only while nothing has moved
      yet.  This clause and the account clause above it are the two halves
      of the partition :func:`~._valuation.row_is_offered_here` states in
      Python;
    * it CONTRIBUTES to a balance and is not soft-deleted
      (:func:`~app.utils.balance_predicates.balance_contributing_clause`) -- a
      Credit or Cancelled row is not money this account moved, and it is the
      shared gate every cash reader here narrows with rather than a filter
      written again;
    * its pay period is one of the OWNER'S -- ownership, reached through the
      paycheck; ``C13-b`` REFUSED ``Transaction.user_id`` here.  **The
      ids come from the CALENDAR rather than from a correlated subquery on
      ``pay_periods.user_id``, and that is what makes the window lookup below
      total**: a row this query returns names a period the calendar was built
      from, so :meth:`~app.services.pay_calendar.PayCalendar.period_by_id`
      cannot answer ``None`` for it.  Inside a COMMAND -- the three POST doors
      that build a scope -- the two reads are separate snapshots under READ
      COMMITTED, so a concurrent period INSERT between them is expressible,
      and scoping by the calendar's own ids means the query simply does not
      ask about a period the calendar has not got rather than returning a row
      nothing here can date.  **Plan step balance:X-i3 makes that
      inexpressible inside a QUERY and takes nothing away from this clause**,
      because the clause is ALSO the OWNERSHIP scope this bullet opens with:
      it is what keeps another owner's rows out of the answer, which holds for
      every request kind and for every CLI caller;
    * it is not a transfer's SHADOW (``transfer_id IS NULL``, leaf
      ``balance:X-bi-6-4c-1``): a still-planned transfer is offered as its
      LEG by :func:`_leg_candidates`, keyed by the transfer, whose owner,
      live, status and period clauses read off the TRANSFER
      (``transfer_legs.offerable_transfer_legs``).  Until that leaf the
      shadow was offered here, behind a clause admitting it only while its
      parent transfer stood (``_parent_transfer_stands``, the parent clause
      ``offerable_transfer_legs`` now carries); the exclusion survives
      ``X-bi-6-4d``, which deletes the shadows, as a clause that is true of
      every row;
    * its payment is NOT recorded on another account (plan step
      ``credit_card:CC-5-4a-5``, leaf 5c-2a, ruling **R-CC137**, developer
      2026-10-04: *"Checking's statement screen does not offer Hotel while
      its payment is recorded on the Visa (the reconcile panel's test,
      R-CC126), and says so on that screen"*).  A reopened bill planned here
      whose kept payment is on the card is offered on the CARD's screen
      (:func:`_settlement_candidates`), and matching it here would have moved
      that payment onto this account; the matcher names no tender since that
      leaf (``_moving._apply_day``), so 'Paid from' is the one door that moves
      a bill's payment between accounts.  **Split rather than filtered**:
      the clause (``status_seam.payment_recorded_elsewhere_clause``, the
      reconcile panel's own) is SELECTED beside each row, and a row it holds
      for is returned as :class:`~._subjects.HeldElsewhere` for the screen to
      say (ruling **R-CC140**) -- one query, one spelling, and nothing left
      silent.  It is priced as its PAYMENT is on the payment's own screen
      (ruling **R-CC139**, :func:`~._valuation.held_elsewhere_of`), and one
      that cannot be priced is reported among the unpriceable as any row
      here is.

    **What is ALREADY MATCHED is NOT a clause here** (plan step
    ``bank_import:X-f6a-3c-2``); it is :func:`unmatched_rows`, applied by each
    caller against the claims that caller read.  It used to be a filter in this
    loop, which is correct for a producer called once per act and wrong for one
    called once per PASS: a row matched by the pass's third item would still
    have been offered to its fourth.

    Not scoped by ``scenario_id``, for the same reason
    ``reconcile_service._rows.outstanding_scope`` is not: Phase 1 is
    baseline-only, so ``account_id`` fully isolates the set today, and when
    what-if scenarios land every arm must thread an operating scenario.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            which each unsettled row's window is read from
            (:attr:`~._subjects.CandidateRow.expected_window`).  The DERIVED
            span, never ``pay_periods.end_date``: that column is a stored copy
            of a derivable fact and plan step ``pay_calendar:C4-c`` dropped it.
        period_ids: The saved period ids of that same calendar
            (:meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`),
            resolved ONCE by :func:`candidates_for` and threaded rather than
            re-derived per arm.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`,
            threaded for exactly the reason ``period_ids`` above it is (plan
            step X-au-j): one derivation the whole pass shares, resolved once
            and never rebuilt under it.

    Returns:
        ``(candidates, unpriceable, held_elsewhere)`` -- one
        :class:`~._subjects.CandidateRow` per offerable row, by id (every row
        here is Projected and carries no day, so the id is the whole of the
        deterministic order the settled arm sorts its days ahead of; the
        proposals a screen shows must not depend on what the planner happened
        to return), the ``(kind, id)`` of the rows the amount model could
        not price, and one :class:`~._subjects.HeldElsewhere` per row whose
        payment is recorded on another account and worth something, by id.
    """
    rows = (
        db.session.query(
            Transaction,
            status_seam.payment_recorded_elsewhere_clause(account_id),
        )
        .options(
            selectinload(Transaction.entries),
            joinedload(Transaction.template),
        )
        .filter(
            Transaction.account_id == account_id,
            is_projected_clause(Transaction),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            Transaction.transfer_id.is_(None),
        )
        .order_by(Transaction.id)
        .all()
    )
    candidates = []
    unpriceable = []
    withheld = []
    for txn, recorded_elsewhere in rows:
        if recorded_elsewhere:
            # Its one payment (``uq_transaction_entries_one_settlement_record``),
            # priced as that payment's own screen prices it (ruling R-CC139).
            (payment,) = txn.covering_movements
            amount = settlement_price(payment, basis)
        else:
            payment = None
            amount = transaction_price(txn, basis)
        if amount is None:
            unpriceable.append((RowKind.TRANSACTION, txn.id))
        elif payment is not None:
            said = held_elsewhere_of(payment, amount)
            if said is not None:
                withheld.append(said)
        else:
            candidate = transaction_candidate(txn, calendar, amount)
            if candidate is not None:
                candidates.append(candidate)
    candidates.sort(key=lambda row: row.row_id)
    return candidates, unpriceable, tuple(withheld)


def _settlement_candidates(
    account_id: int, calendar: "PayCalendar",
    period_ids: "Collection[int]",
    basis: "cash_ledger.AmountBasis",
) -> "tuple[list[CandidateRow], list[int]]":
    """Return the ROWS' covering movements on *account_id* a statement could be showing.

    **The settled subject's arm** (plan step ``credit_card:CC-5-4a-1``,
    ruling **R-CC43**): a settled row's money IS its covering movement
    (ruling **R-BAL80**), on the account the money moved through -- the
    row's own for every settle through ``credit_card:CC-5-2``, and since
    ``CC-5-3`` the TENDER the door named, which for a bill charged to the
    card is the card.  Offering the row for it (the shape through
    ``CC-5-3``) put the subject on the row's account, where a card-paid
    bill's money never was and a member naming the row could not be written
    for the card's statement.

    Scope, each clause load-bearing:

    * the MOVEMENT is on THIS account (``TransactionEntry.account_id``,
      ruling **R-BAL75**) -- the one clause that differs from the row arm's,
      and the whole point;
    * it is a covering movement (:func:`status_seam.covering_clause`) --
      the seam's mirror of the row's record, never a person's purchase, which
      :func:`_purchase_candidates` offers on its own terms;
    * its ROW is NOT this screen's candidate as a row: the complement of
      :func:`_transaction_candidates`' first two clauses, so a Projected row
      on this account is offered once, as itself, and its kept movement (a
      reverted row's, ruling **R-CC42**) is offered only where the row is
      not -- :func:`~._valuation.row_is_offered_here` is the same partition
      in Python, re-asked by :func:`~._valuation.repriced`;
    * its row CONTRIBUTES and is not soft-deleted and sits in one of the
      OWNER's saved periods -- the row arm's clauses, applied to the parent
      for the same reasons;
    * its row is not a transfer's SHADOW: a paid transfer's side is offered
      by :func:`_leg_settlement_candidates` as its LEG's record (leaf
      ``balance:X-bi-6-4c-1``), the transfer's clauses read off the
      transfer, where this arm joined the movement to its shadow row until
      that leaf.

    **What is ALREADY MATCHED is NOT a clause here**; see
    :func:`_transaction_candidates` for why it moved to :func:`unmatched_rows`.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        period_ids: The owner's saved pay-period ids, threaded as every
            other arm takes them.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`, for
            the un-dated arm of :func:`~._valuation.settlement_price`.

    Returns:
        ``(candidates, unpriceable)`` -- one :class:`~._subjects.CandidateRow`
        per offerable movement, UNORDERED (:func:`candidates_for` sorts the
        row payments and the leg payments together, :func:`_by_recorded_day`),
        and the ``(kind, id)`` of the movements whose ROW the amount model
        could not price a re-settle for.
    """
    rows = (
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .options(
            contains_eager(TransactionEntry.transaction)
            .selectinload(Transaction.entries),
            contains_eager(TransactionEntry.transaction)
            .joinedload(Transaction.template),
            contains_eager(TransactionEntry.transaction)
            .joinedload(Transaction.account),
        )
        .filter(
            TransactionEntry.account_id == account_id,
            status_seam.covering_clause(),
            ~db.and_(
                is_projected_clause(Transaction),
                Transaction.account_id == account_id,
            ),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            Transaction.transfer_id.is_(None),
        )
        .all()
    )
    candidates = []
    unpriceable = []
    for entry in rows:
        amount = settlement_price(entry, basis)
        if amount is None:
            unpriceable.append((RowKind.SETTLEMENT, entry.id))
            continue
        candidate = settlement_candidate(entry, calendar, amount, account_id)
        if candidate is not None:
            candidates.append(candidate)
    return candidates, unpriceable


def _by_recorded_day(row: CandidateRow):
    """Return the settled arms' sort key: oldest recorded day first, undated last.

    The movement's id breaks ties.  ONE order for a row's payments and a
    leg's, which :func:`candidates_for` sorts TOGETHER (leaf
    ``balance:X-bi-6-4c-1``) -- until that leaf both were one arm's, a
    shadow's movement joined to its shadow, and a movement's id is the same
    either way, so the settled rows keep the order they had.
    """
    return (row.settled_on is None, row.settled_on, row.row_id)


def _leg_candidates(
    account_id: int, calendar: "PayCalendar",
    period_ids: "Collection[int]",
    basis: "cash_ledger.AmountBasis",
) -> "tuple[list[CandidateRow], list[tuple[RowKind, int]]]":
    """Return the still-planned transfer LEGS on *account_id* a statement could be showing.

    **The fourth subject's arm** (leaf ``balance:X-bi-6-4c-1``, rulings
    **R-BAL87**, **R-BAL106**, **R-BAL159**): one side of a Projected transfer
    whose money has not moved on this account, offered as the TRANSFER and
    keyed by it, where :func:`_transaction_candidates` offered the transfer's
    shadow row until that leaf.  The scope is
    ``transfer_legs.offerable_transfer_legs``' -- the reconcile panel's since
    leaf ``X-bi-6-4c-2`` -- and every clause is the row arm's, read off the
    TRANSFER:

    * this account is one of its endpoints (either side);
    * it is Projected and live, and THIS side holds no dated movement
      (ruling **R-BAL79**: the movement decides a side; ruling **R-JM**: a
      transfer leg reads its parent);
    * its pay period is one of the OWNER's saved periods -- the owner the
      calendar is, the scope's ownership clause.

    When it replaced the row arm's shadow offer, it offered exactly the
    transfers whose shadow that arm had offered on every door-written state
    (a Projected parent's two shadows were Projected, live and filed in its
    period -- Transfer Invariants 1, 3 and 4).  Priced by
    :func:`~._leg_valuation.leg_price`, off the transfer alone: a transfer
    whose twin pair was DAMAGED was offered like any other (ruling
    **R-BAL235**; it was counted among the unpriceable and not offered,
    ruling **R-BAL158**, until plan step ``balance:X-bi-6-4d-2``), and a
    transfer has no twin pair now.  Not scoped by scenario, for
    :func:`_transaction_candidates`' reason.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`
            -- whose ``user_id`` is the owner the loader scopes by.
        period_ids: The owner's saved pay-period ids.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`.

    Returns:
        ``(candidates, unpriceable)`` -- one :class:`~._subjects.CandidateRow`
        per offerable leg, by transfer id (every leg here carries no day), and
        the ``(kind, id)`` of the legs that could not be priced.
    """
    candidates = []
    unpriceable = []
    for leg in transfer_legs.offerable_transfer_legs(
        account_id, calendar.user_id, period_ids, options=leg_loads(),
    ):
        amount = leg_price(leg, basis)
        if amount is None:
            unpriceable.append((RowKind.LEG, leg.transfer.id))
            continue
        candidate = leg_candidate(leg, calendar, amount)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda row: row.row_id)
    return candidates, unpriceable


def _leg_settlement_candidates(
    account_id: int, calendar: "PayCalendar",
    period_ids: "Collection[int]",
) -> "list[CandidateRow]":
    """Return the paid transfer legs' covering movements on *account_id*.

    :func:`_settlement_candidates`' LEG twin (leaf ``balance:X-bi-6-4c-1``):
    a side whose money moved on this account is offered as its covering
    movement, the leg's RECORD (``transfer_legs.recorded_transfer_legs``,
    ruling **R-BAL80**), through the loader that reaches a movement's
    transfer and side by its side link.  Every clause is the row arm's, read
    off the TRANSFER:

    * the MOVEMENT is on this account (a transfer's is on its own side's);
    * the transfer CONTRIBUTES and is live, and is filed in one of the
      OWNER's saved periods;
    * the movement is DATED -- a settled leg is its dated covering movement
      (ruling **R-BAL80**), and a side whose money has not moved is on the
      plan (:func:`_leg_candidates`, ruling **R-BAL79**), so one side is
      offered once.  An un-dated record under a Paid or Received transfer --
      a state no door writes -- is offered by neither arm (under a Cancelled
      one the contributing clause already excludes it);
      :func:`~._valuation.leg_settlement_candidate` carries why, and re-asks
      the day in Python for :func:`~._valuation.repriced`'s sake.

    No leg payment is ever unpriceable: its figure is the one its transfer
    stores (``cash_ledger.movement_cash_leg`` reads it off the transfer),
    which no amount model can refuse.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        period_ids: The owner's saved pay-period ids.

    Returns:
        One :class:`~._subjects.CandidateRow` per offerable movement,
        UNORDERED (:func:`candidates_for` sorts it with the row payments).
    """
    candidates = []
    for leg in transfer_legs.recorded_transfer_legs(
        TransactionEntry.account_id == account_id,
        Transfer.user_id == calendar.user_id,
        balance_contributing_clause(Transfer),
        Transfer.pay_period_id.in_(period_ids),
        TransactionEntry.settled_on.isnot(None),
    ):
        candidate = leg_settlement_candidate(leg, calendar, account_id)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _purchase_candidates(
    account_id: int, calendar: "PayCalendar", period_ids: "Collection[int]",
) -> "list[CandidateRow]":
    """Return the purchases on *account_id* a statement could be showing.

    A purchase is a cash movement of its OWN since plan step
    ``balance:X-f3b``, so the bank shows it as a line in its own right -- which
    is what makes the 267 card-swipe lines on the developer's own statement
    matchable at all.

    Two clauses beyond the account and the owner:

    * NOT a card purchase.  A card purchase never touches this account -- it
      leaves later through its own CC Payback sibling -- so a checking
      statement cannot be showing one.  It is the same clause
      ``ck_transaction_entries_card_purchase_clears_nowhere`` makes structural
      for the clearing link;
    * its PARENT contributes.  A purchase under a soft-deleted, Credit or
      Cancelled row posts nothing (ruling **R-FM**), so offering one would
      propose to record a movement the ledger books at zero;
    **A THIRD clause stood here until plan step balance:X-am** (ruling
    **balance:R-HA**): the parent must not be ARCHIVED.  It was added by
    adversarial financial review 2026-08-17 because the terminal ``Settled``
    status was not excluded by ``balance_contributing_clause``, so the screen
    could offer a row whose acceptance raised MID-LOOP -- falsifying this
    package's claim that every refusal fires before anything is written.  The
    status is deleted, so no row can be in it and the clause can match nothing;
    it goes with its subject rather than standing as a filter over an id that
    no longer exists.

    **What is ALREADY MATCHED is NOT a clause here**; see
    :func:`_transaction_candidates` for why it moved to :func:`unmatched_rows`.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            which each purchase's envelope period is read from (plan step
            ``bank_import:X-gz``) -- threaded for the reason *period_ids* is.
        period_ids: The owner's saved pay-period ids -- the SAME scope
            :func:`_transaction_candidates` applies, written once and threaded
            so the two arms cannot drift about whose rows may be offered.

    Returns:
        One :class:`~._subjects.CandidateRow` per offerable purchase, ordered as
        :func:`_transaction_candidates` orders its own.
    """
    rows = (
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .options(contains_eager(TransactionEntry.transaction))
        .filter(
            TransactionEntry.account_id == account_id,
            TransactionEntry.is_credit.is_(False),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(period_ids),
            # NOT the row's own payment record (plan step **X-bi-3a**): a
            # covering movement is the settle's mirror of its parent's figure
            # and is :func:`_settlement_candidates`' subject, on the ROW's
            # terms (plan step ``credit_card:CC-5-4a-1``); this arm offers a
            # person's purchases on their own.  Offering it here too would put
            # one bank line against two candidates.
            ~status_seam.covering_clause(),
        )
        .all()
    )
    return sorted(
        (
            purchase_candidate(entry, calendar)
            for entry in rows if entry.amount
        ),
        key=lambda row: (row.settled_on is None, row.settled_on, row.row_id),
    )


def candidates_for(
    account_id: int, calendar: "PayCalendar",
    basis: "cash_ledger.AmountBasis",
) -> Candidates:
    """Return every row on *account_id* a statement could be showing.

    **The ONE entry point, and the reason it exists is that the arms share
    a read.**  Every arm scopes by the owner's saved period ids, and asking again in
    one request is a redundant producer call -- the shape this project treats
    as a DRY violation rather than as a cost.  It is resolved once here and
    threaded.

    **The CALENDAR is a parameter for the same reason and one tier up.**  A
    read pass holds one calendar and every producer under it takes it, exactly
    as a balance pass threads its ``BalanceContext``:
    :class:`~._scope.ReviewScope` builds one and hands it to this and to its own
    line placer, where a first version of this step had each of them ask
    ``calendar_for`` separately and a third site answer the same question with
    its own ``MIN(start_date)``.  Three reads of one fact in one request is the
    defect the paragraph above describes, and two of them can disagree: under
    READ COMMITTED a concurrent payday write between the two loads would place
    a line by one calendar and bound its candidates by another.  Found by
    adversarial financial review 2026-08-19.  **Since plan step balance:X-i3
    the disagreement is a COMMAND's alone** -- the three POST doors that build
    a scope, which are also the three that move money -- because a render's
    whole request is one snapshot.  The DRY half of the argument was never
    conditional on the isolation level and is what still makes the parameter
    required on every path.

    **It answers what the account COULD offer, and says nothing about what is
    already spoken for** (plan step ``bank_import:X-f6a-3c-2``) -- that is
    :func:`matched_subjects`, narrowed in by :func:`unmatched_rows`.  The split
    is what lets one derivation serve a whole review pass: this answer does not
    move while the pass runs, and the claims do.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, built by the read
            pass.  **It IS the ownership scope**, which is why no ``owner_id``
            sits beside it: the periods it carries are exactly that owner's, so
            a second parameter naming the owner would be a second statement of
            whose rows may be offered and the two could disagree.  Nothing here
            re-derives it -- a producer that rebuilt its caller's pass would be
            the copy this parameter exists to remove.
        basis: The pass's
            :class:`~app.services.cash_ledger.AmountBasis`, built by
            :meth:`~._scope.ReviewScope.build` (plan step X-au-j, finding
            **N-309**).  It is a parameter for exactly the reason stated one
            column up and it is REQUIRED for exactly that reason too: a
            producer that built its own would be the copy the parameter exists
            to remove, and defaulting it would leave the expensive shape as
            what a caller gets by saying nothing.

    Returns:
        A :class:`~._subjects.Candidates`.  Its ``rows`` are the settlements,
        the transactions, the legs and the purchases TOGETHER, each arm's own
        order preserved and in that order -- the settled records by their day
        (a row's payments and a leg's in ONE order, :func:`_by_recorded_day`),
        then the Projected rows, then the still-planned transfer legs, then
        the purchases -- a union rather than a tuple of lists, because every
        consumer asks the same question of every kind: a bank line does not
        know which table its counterpart lives in.  **A leg's place moved at
        leaf ``balance:X-bi-6-4c-1``**: it was a shadow row among the
        Projected rows, ordered by the shadow's id, and is its own run after
        them now, ordered by the transfer's.
    """
    # The owner's SAVED periods, which are every arm's ownership scope.  Asked
    # of the calendar ONCE here rather than in each arm, for the reason the
    # calendar itself is threaded: two asks in one request is this project's
    # DRY violation rather than a cost.  The ``period_id is not None`` filter
    # is :meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`'s since
    # pay-calendar plan step C4-a-4 -- one spelling of "is this period SAVED",
    # which this module used to write for itself.
    period_ids = calendar.saved_by_id().keys()
    settlements, unpriceable_settlements = _settlement_candidates(
        account_id, calendar, period_ids, basis,
    )
    leg_settlements = _leg_settlement_candidates(
        account_id, calendar, period_ids,
    )
    transactions, unpriceable_transactions, held_elsewhere = (
        _transaction_candidates(account_id, calendar, period_ids, basis)
    )
    legs, unpriceable_legs = _leg_candidates(
        account_id, calendar, period_ids, basis,
    )
    return Candidates(
        rows=(
            sorted(settlements + leg_settlements, key=_by_recorded_day)
            + transactions + legs
            + _purchase_candidates(account_id, calendar, period_ids)
        ),
        unpriceable=(
            *unpriceable_settlements, *unpriceable_transactions,
            *unpriceable_legs,
        ),
        held_elsewhere=held_elsewhere,
    )
