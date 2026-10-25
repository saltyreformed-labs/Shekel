"""The cash fold's EVENT STREAM: what happened to an account, when it happened.

A cash account's balance is a fold over its event stream, and this module builds
that stream.  Two kinds of fact enter it, and nothing else:

* an **ASSERTION** -- the user declaring "my real balance is now $X", one
  :class:`~app.models.account.AccountAnchorHistory` row, RESETTING the running
  balance at its assertion instant.  The first row is the account's OPENING (the
  origination row ``account_service.create_account`` appends); every later row is
  a TRUE-UP.
* an **ACTUAL** -- a DATED MOVEMENT of a balance-contributing plan row: the
  record that cash really moved, one ``budget.transaction_entries`` row on
  the account its money moved through (ruling **R-BAL75**).  A purchase the
  bank has been seen to take, the covering movement the status seam writes
  when a bill, a paycheck or a transfer leg settles -- each is one fact, and
  a plan row is never one (plan step ``balance:X-bi-4a``, ruling
  **R-BAL80**).  Through ``X-bi-3e`` the settled ROW was a fact too, worth
  its record less its own dated movements, and the two homes agreed by
  ruling R-FM's identity because the seam mirrored one record into both;
  the row's record is the stale cache ``X-bi-4b`` deletes.  A SETTLED
  transfer's effect arrives as each LEG's covering movement -- the RECORD
  half of Transfer Invariant 5 as restated at plan step X-bi-6a (ruling
  R-BAL13) -- read with its transfer and side
  (:func:`app.services.transfer_legs.recorded_transfer_legs`, leaf
  ``X-bi-6-4a``), so its budget column, direction and contributing gate are
  the TRANSFER's and nothing here reads the shadow row the movement still
  hangs off until ``X-bi-6-4d`` re-parents it onto ``budget.transfers``.
  A still-PROJECTED transfer's legs are derived from the parent
  (:mod:`app.services.transfer_legs`) by the plan half below.

**PLANNED (still-Projected) rows are deliberately NOT here** (ruling R-G).  A
plan cannot have already happened, so a projected row's effective date is
``max(its attribution date, as_of + 1 day)`` -- it depends on the READER's
as-of, and this leaf reads no clock.  The projected tier therefore lives in the
seam's fold, exactly as the loan plan's PLANNED tier lives in ``balance_at._plan``
rather than in ``loan_ledger`` (plan step C6a's ruling, restated for cash).
**Neither is a movement IN FLIGHT** -- a purchase recorded and not yet seen
to leave (ruling **R-BAL77**): this module LOADS it (:func:`in_flight_movements`,
the un-dated half of the one movement stream) and the plan half places it,
because where it lands is ``max(the day it happened, as_of + 1)``, a
function of the reader's as-of like every other plan item's.

**Every fact enters the stream, whatever its date, and nothing here reads the
clock.**  Deciding which facts have HAPPENED as of a date is a READER's job.  The
loan half learned this the expensive way: a walk that took an ``as_of`` made the
persisted ledger a function of the wall clock at the moment the sync happened to
run, which is a corruption generator rather than a cache (plan step A3,
``4e46a0a8``).

**ONE CIVIL DAY per fact, and it is the USER'S day** (ruling R-DH,
``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  A movement
carries :attr:`CashSourceFact.settled_on` and an assertion carries
:attr:`CashAnchorFact.observed_on`; both are resolved ONCE at construction, and
every consumer -- the partition, the fold's sampling, the period bucketing, the
posting writer's entry dating -- reads that one field.  Two independently-derived
keys for one question is finding N-34's shape (the loan split keyed its rate on
the pay period while its ordering keyed on the due date, and the two disagreed by
$500.00 on a single payment).  Here they cannot: there is one key.

**This module carried INSTANTS until 2026-07-31, and the change is ruling R-DH.**
An assertion is the CLOSING BALANCE for its civil day, so it absorbs every
movement dated that day whatever order the two were recorded in -- EVERY
assertion, with no exception for the opening (finding N-133 / F1, whose
one-day-old exception is recorded in ``anchor_settle_partition.md`` at R-DH (a)
along with the ``$2,057.42`` it cost).  The instant partition it
replaces decided that question by CLICK ORDER -- neither
``Transaction.paid_at`` (``db.func.now()`` at the click, deleted at plan step
X-f1) nor ``AccountAnchorHistory.created_at`` measures when
money moved -- and on production 2026-07-31 an ordinary bookkeeping session
(read the bank, enter the anchor, tick off what cleared) subtracted ``$4,001.42``
of already-cleared payments a second time, rendering the grid's projected end
balance at ``-$4,021.37`` against a true ``-$19.95``.  Measured over four months
of real data, the day partition cuts the correction the model must plug at its
53 true-ups from ``$40,554.34`` gross / ``-$6,998.90`` net to ``$15,367.94`` /
``-$940.06``, and it is the only rule under which the walk lands on the balance
the bank shows.

**The day is ``America/New_York``, not UTC, and that is ruling R-DH (b).**
``pay_periods.start_date`` / ``end_date`` and ``transactions.due_date`` are plain
``DATE`` columns meaning the USER's civil days, so deriving an event's day in UTC
and comparing it against them compares two different calendars.  Measured: 22 of
139 real settled rows land on a different day under UTC, 5 of them in a different
pay period (a ``$1,910.95`` mortgage payment twice), and two Eastern evenings had
ONE bookkeeping session split across two UTC days -- the shape that would defeat
the partition above.  Storage is unchanged; every instant is still stored UTC.

**Nothing here DERIVES a day any more, and that is plan step X-f1** (ruling
R-EC).  A movement stores the civil day its money moved in
``transaction_entries.settled_on``, so this module reads a fact where it used
to convert ``paid_at``'s instant into the display timezone and fall back to the
pay period's ``start_date`` when the instant was NULL.  That fallback was a
guess -- 8 live settled rows relied on it -- and it is gone with the
derivation: the settled stream's query admits no NULL day, and an un-dated
movement is a fact of a different kind (in flight) rather than one dated by
this module's opinion.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes.  All money is
:class:`~decimal.Decimal`.

Plan step **X-a** (shipped at ``929b3a72``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import contains_eager

from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.account_opening import AccountOpening
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.transfer_legs import recorded_transfer_legs
from app.utils.balance_predicates import (
    balance_contributing_clause,
    owner_declared_clause,
)
from app.utils.dates import utc_instant

from ._amounts import ReconciledThrough
from ._cash_leg import movement_cash_leg
from ._clearing import StatementCoverage, statement_coverage


@dataclass(frozen=True)
class CashOpeningFact:
    """What an account held BEFORE its records begin, as a loaded fact.

    The governing :class:`~app.models.account_opening.AccountOpening` row for
    one account (plan step **X-f3c-2a**, ruling **R-GX**), read once per walk
    and carried on :class:`~._walk.CashLedgerWalk` beside the assertions and the
    movements.  It is the LEVEL the fold's running total starts from: every
    balance the app renders is this figure plus what the records say happened
    since.

    **It replaces a derivation, and the model docstring lists the four defects
    that derivation caused.**  Until this step the same quantity was recomputed
    on every read as "the earliest assertion minus the movements dated at or
    before it" (ruling **R-I**), which made it move when an assertion was
    back-dated, differ between scenarios, impossible to correct, and derived a
    second time by the posted ledger.

    Attributes:
        opening_id: The ``budget.account_openings`` row's own id -- the
            restatement this fact is, so a reader can tell two apart.
        account_id: The account whose books these are.
        opened_on: The civil day the books opened.  The ``account_opening``
            journal entry is dated on it, which is what stops a back-dated
            assertion re-dating that entry.
        opening_equity: The capital the books opened with, LEDGER-NATIVE and
            in the same sign convention as
            :attr:`CashAnchorFact.anchor_balance`.
        source_id: ``ref.account_opening_sources.id`` -- whether a human stated
            this figure or the X-f3c-2a migration derived it.  Carried because
            a derived figure is the old inference frozen and may be WRONG
            (finding **N-275** measures one wrong by ``$436.05``), so a surface
            must be able to tell a guess from an observation.  The walk itself
            never branches on it: an opening is an opening whatever wrote it.
        recorded_at: The RECORDING instant, aware-UTC.  It is what ORDERS the
            restatements (see :func:`account_opening_fact`) and it dates
            nothing -- :attr:`opened_on` is the business date, the same
            two-clock split :attr:`CashAnchorFact.asserted_at` documents one
            table over.  Carried since plan step **X-f3c-2b** so the history
            card can caption a RESTATED opening the way it captions a
            back-dated assertion: books opening in March, recorded in August,
            is a fact the owner should be able to see rather than infer.
    """

    opening_id: int
    account_id: int
    opened_on: date
    opening_equity: Decimal
    source_id: int
    recorded_at: datetime


@dataclass(frozen=True)
class CashAnchorFact:
    """One assertion of an account's real balance, as a plain fact.

    Wraps one :class:`~app.models.account.AccountAnchorHistory` row for the walk
    to replay.  Rows are ordered by ``(observed_on, created_at, id)`` --
    BUSINESS date first, with the recording instant and then ``id`` breaking a
    same-day tie -- the same key :func:`app.services.cash_ledger.resolve_anchor`
    takes descending, and the FIRST is the account's OPENING.

    **That order is a CONTRACT two walks depend on, not a convenience.**  Both
    replays advance a monotonic pointer through day-sorted sources
    (:func:`app.services.cash_ledger.walk_cash_ledger` and
    :func:`app.services.account_posting_service.walk_account_ledger`), so a
    fact list not non-decreasing in :attr:`observed_on` makes the pointer skip
    sources it should absorb and mis-state a ``ledger_before`` the posting
    walk WRITES to the general ledger.  The read side used to re-sort and so
    self-healed it; since the one-partition step neither side does, because one
    ordering stated where the rows are read is what finding N-133 / R1 ruled.
    The key was ``(created_at, id)`` until plan step 2 made ``observed_on``
    user-supplied and the two orders could differ -- which is how a
    ``$1,307.66`` true-up once posted to the ledger tagged as the OPENING.

    **It carried the row's stored ``pay_period_id`` until plan step X-f1c3b**
    (ruling R-EO), which deleted the COLUMN.  Finding N-169 had already
    measured the field to have ZERO consumers in ``app/``; what the ruling
    added is that the column behind it was a cache of a derivation both
    posting reconciles make from ``observed_on``, was wrong on 2 of 78
    production rows, and carried an ``ON DELETE CASCADE`` that let a
    pay-period reset destroy the user's balance record.  A reader wanting
    "which period does this assertion book in" derives it from the day
    (:meth:`app.services.pay_calendar.PayCalendar.filing_period`).

    Attributes:
        anchor_id: The ``budget.account_anchor_history`` row's own id -- the
            value a cleared line NAMES (``reconciled_by_id``, ruling **R-FL**),
            so :class:`~._clearing.StatementCoverage` can say WHICH assertion
            cleared a movement rather than only that one did.  It is the row's
            identity and nothing more: no rule reads it as an ordering, which is
            :attr:`observed_on`'s job with :attr:`asserted_at` breaking a tie.
        account_id: The ``budget.accounts`` id the assertion belongs to.
        anchor_balance: The asserted balance, LEDGER-NATIVE sign: an
            owed-as-negative liability anchor stays negative.  The walk never
            branches on account class (ruling R-J), and neither does the fold
            above it; classifying asset vs liability belongs to the net-worth
            consumers.
        observed_on: The civil day this balance was TRUE (ruling R-DH) -- the
            business date the whole partition turns on.  A source whose
            :attr:`CashSourceFact.settled_on` is at or before it is already
            INSIDE the asserted balance, because an assertion is the CLOSING
            balance for its day.  **Read from the stored
            ``account_anchor_history.observed_on``, not derived** (plan step 2,
            the opening half): the user supplies it when creating an account,
            and a true-up defaults it to today in the display timezone.  It was
            ``created_at``'s display-timezone day until the column shipped, and
            the column was backfilled from exactly that derivation, so nothing
            moved.  ``LoanAnchorEvent.anchor_date`` has been the loan side's
            version since Commit 16; this is the cash half that never had one
            (finding X5), and its absence is why the partition compared two
            data-entry timestamps and cost production ``$4,001.42``.
        asserted_at: The RECORDING instant, aware-UTC.  It dates nothing and
            partitions nothing; its one job is to order two assertions that
            share an :attr:`observed_on`, so the LAST one recorded is the day's
            closing balance.  Keeping it is not a second clock: one is a
            business date and one is a tie-break over assertions about the same
            business date.  It was the partition key until ruling R-DH, which is
            what cost production ``$4,001.42`` (see the module docstring).
        recorded_on: The civil day the assertion was ENTERED, in the user's
            timezone.  **Read from the stored
            ``account_anchor_history.recorded_on``, not derived from
            :attr:`asserted_at`** (finding **N-299**, developer ruling
            2026-08-25).  It partitions nothing and orders nothing; its one
            reader is the balance-history card, which captions a row as
            back-dated when it differs from :attr:`observed_on`.  Deriving it
            put that comparison across two clocks -- this column is the
            APPLICATION's ``display_today()`` and :attr:`asserted_at` is
            PostgreSQL's ``now()`` -- so an ordinary same-day true-up read as
            back-dated wherever the two could disagree.  Nothing else in the
            walk may read it: an assertion's effect on a balance is a function
            of :attr:`observed_on` alone.
        is_opening: True for the account's first history row; False for a
            true-up.  **A LABEL, not a partition input** (finding N-133 / F1):
            the walk treats both kinds identically -- an assertion closes its
            civil day, whichever kind it is.  It was a partition input for one
            day and cost ``$2,057.42`` of period 0's remainder while it was.

            **NOTHING reads it any more, and plan step X-f3c-2a is why.**  Its
            two consumers were the posting source kind an assertion books under
            and ruling R-I's back-projection; the first reads
            ``AccountAnchorCorrection.opens_the_books`` now (a stored fact) and
            the second is deleted.  The balance-history card's "Opening" badge
            asks ``budget.account_openings`` which day the books opened rather
            than which assertion sorts first.  The field is kept because the
            walk's ORDERING contract is still load-bearing and this is the
            cheapest statement of it; it decides no figure.
    """

    anchor_id: int
    account_id: int
    anchor_balance: Decimal
    observed_on: date
    asserted_at: datetime
    recorded_on: date
    is_opening: bool

    @property
    def reconciled_through(self) -> ReconciledThrough:
        """Return the coverage boundary this assertion establishes.

        An assertion is the closing balance for its civil day, so it reconciles
        every movement dated on or before :attr:`observed_on` (ruling R-DH (a)).
        Both walks ask their sources through this -- the read replay in
        :func:`app.services.cash_ledger.walk_cash_ledger` and the posted
        ledger's in
        :func:`app.services.account_posting_service.walk_account_ledger` -- so
        the rule they apply is one implementation rather than two statements
        held in step by convention.

        Returns:
            The :class:`~app.services.cash_ledger.ReconciledThrough` for this
            assertion's own civil day.
        """
        return ReconciledThrough(self.observed_on)


@dataclass(frozen=True)
class CashSourceFact:  # pylint: disable=too-many-instance-attributes
    """One dated movement's signed effect on the account, when, and whose column.

    Pylint: ``too-many-instance-attributes`` (8/7) -- one movement, its two
    parent links (a plan row's, a transfer's) and what the fold reads of it.
    A leg's fact carries both until ``X-bi-6-4d`` (its shadow's id and its
    transfer's); from then the table takes an exactly-one-parent shape
    (ruling **R-BAL88**), and one tagged field would hide it.

    The ACTUAL half of the event stream: cash that really moved.  ONE kind of
    row produces one (plan step ``balance:X-bi-4a``, ruling **R-BAL80**): a
    ``budget.transaction_entries`` row carrying a ``settled_on`` -- a purchase
    the bank was seen to take (ruling **R-FM**, plan step X-f3b), or the
    covering movement the status seam writes when a bill, a paycheck or a
    transfer leg settles (rulings **R-BAL39**, **R-BAL41**).  Its delta is the
    SHARED :func:`app.services.cash_ledger.movement_cash_leg` -- its whole
    figure in its PARENT's direction (plan step X-bi-3b, ruling **R-BAL35**),
    the same figure the posting writer books for it
    (``_posting_purchases._purchase_target``) -- so the walk and the posted
    ledger value one movement identically by construction, not by two rules
    that happen to agree.

    **The settled ROW produced one of these through ``X-bi-3e``, and its
    absence is the ruling.**  That fact was worth ``settled_cash_leg`` -- the
    row's recorded figure less its own dated movements -- and it was ZERO for
    every covered bill and paycheck by R-FM's identity, and for a
    ``purchases``-basis envelope it was the envelope's un-dated purchases
    booked on the close day.  Ruling **R-BAL77** reads those as movements in
    flight (:class:`InFlightMovement`), so no row has a leg of its own and
    the stream is movements alone.  A transfer's two legs are its two
    covering movements, each on its own account and in its own direction,
    and since leaf ``X-bi-6-4a`` (ruling **R-BAL106**) each is read as a LEG
    of its transfer -- budget column, type and contributing gate the
    TRANSFER's and its side's, never the shadow row's -- through
    :func:`app.services.transfer_legs.recorded_transfer_legs`; the posted
    ledger books each of them as its own entry against the owner's transit
    account since plan step ``balance:X-bi-6-3``
    (``posting_service.sync_transfer_postings``, ruling **R-BAL45**), so the
    fold and the ledger read one movement each.

    **It carries TWO clocks, and the second one is not decoration** (plan step
    X-c1).  :attr:`settled_on` is the CASH clock -- the day the money moved,
    which is what a balance is folded on -- while :attr:`pay_period_id` is the
    BUDGET clock, the column the parent row was budgeted in.  They are the same
    period for most movements and different for the rest, and that difference
    IS the grid's Reconciliation row: a movement dated outside its parent's pay
    period moves the balance in one column while its income / expense subtotal
    sits in another.  Carrying both here is what lets ONE valued set be grouped
    on either clock, rather than a second load answering the second question
    (ruling R-K).  :attr:`is_income` is the leg the budget clock sorts the
    movement into, and it is the parent's TYPE rather than the sign of
    :attr:`delta` because the two can disagree: a REFUND (a negative purchase,
    ruling **bank_import:R-II**) under an expense row has a POSITIVE delta and
    is still an expense that came back, while
    :func:`app.services.cash_ledger.movement_cash_leg` derives the sign FROM
    the type in the first place -- reading it back off the sign is inverting a
    lossy function.

    Attributes:
        transaction_id: The movement's own ``transaction_id`` column: the
            plan row it records money for (ruling **R-BAL35**; never a fact
            of its own).  For a transfer leg it is the shadow the movement
            still hangs off, NULL from ``X-bi-6-4d``; the fold reads no
            parent through it.  Its one reader is the bank-agreement screen's
            name lookup (``bank_agreement._rows_on`` / ``_row_names``), which
            keys both sides by this column, so a NULL matches a NULL.  The
            sort's tie-break read it until leaf ``X-bi-6-4d-1`` re-keyed that
            on :attr:`entry_id`, because a ``None`` beside an ``int`` does
            not sort.
        transfer_id: The transfer the movement is a LEG of, ``None`` for a
            plan row's (leaf ``X-bi-6-4a``): what the far-leg exclusion
            (``balance_at._cash_periods._budget_legs``) asks.
        entry_id: The ``budget.transaction_entries`` row, and the fact's
            identity: one fact per movement, and an envelope's posted
            purchases are distinct movements sharing one parent.  The sort
            breaks a same-day tie with it.
        pay_period_id: The BUDGET clock -- the ``budget.pay_periods`` row the
            PARENT is attributed to (NOT NULL on its column).  A movement
            takes its parent's because it spends or receives in the parent's
            column and has none of its own; that is what keeps the budget-clock
            regrouping (``balance_at._cash_periods._budget_legs``) reading a
            partially-spent envelope at its whole cost -- the dated part as
            these facts, the un-dated part as movements in flight, the rest as
            the envelope's unspent budget.  Never used to date the event; the
            cash clock is :attr:`settled_on` alone.
        is_income: Whether the PARENT is an INCOME transaction (its
            ``transaction_type_id``), so a budget-clock reduction can split the
            income and expense legs by type rather than by the sign of
            :attr:`delta`.
        settled_on: The civil day this movement's cash MOVED -- the one date
            the assertion partition compares against, the fold samples on, and
            the period index buckets by.  Read from the stored
            ``transaction_entries.settled_on``, never derived (plan step X-f1,
            ruling R-EC); the query admits no NULL, so no fact is dated by a
            fallback.
        reconciled_by_id: WHICH statement was recorded as showing this
            movement -- the ``account_anchor_history`` id its
            ``reconciled_by_id`` names, or ``None`` when none has been (ruling
            **R-FL**).  It sits beside :attr:`settled_on` rather than replacing
            it because the two are different facts: one is when the money
            moved, the other is which statement was seen to show it, and a
            statement legitimately shows a line that moved days earlier.  What
            the walk does with the pair is
            :class:`~._clearing.StatementCoverage`'s rule and not this record's.
        delta: The signed cash effect
            (:func:`app.services.cash_ledger.movement_cash_leg`): ``-amount``
            for a purchase against an envelope or a bill's covering movement,
            ``+amount`` for a paycheck's or a transfer's incoming leg, and
            ``0.00`` for a card purchase (which leaves through its CC Payback
            sibling) or a movement under a non-contributing parent -- the two
            the producer is total over, stated there.

    **There is no instant on this record, and its absence is the ruling** (R-DH).
    It carried ``occurred_at`` -- ``paid_at`` normalized to UTC -- until
    2026-07-31, and every consumer that wanted a DAY re-derived one from it.  The
    instant was never a fact about the money: ``paid_at`` was stamped
    ``db.func.now()`` when the user clicked and the API refused any other
    value (it was ``dump_only``), so its sub-day precision described
    bookkeeping keystrokes and
    the partition that consumed it decided ``$4,001.42`` of real money by click
    order.  Storing only what is known keeps a consumer from reaching for
    precision the datum does not have.
    """

    transaction_id: int
    transfer_id: "int | None"
    entry_id: int
    pay_period_id: int
    is_income: bool
    settled_on: date
    reconciled_by_id: "int | None"
    delta: Decimal


def governing_account_opening(account_id: int) -> CashOpeningFact | None:
    """Return *account_id*'s governing opening record, or ``None`` for no row.

    **The WRITE door's question**, and the non-raising twin of
    :func:`account_opening_fact` exactly as
    :func:`~._facts.governing_anchor_on` is :func:`~._facts.resolve_anchor`'s
    (plan step **X-f3c-2b-2a**).  Before appending a restatement, the door has
    to know what already stands so it can decline a submission that changes
    nothing (ruling **R-EQ**'s rule, one table over) -- and
    ``account_service.create_account`` reaches the same writer with an account
    that by construction carries none.  "This account has no opening yet" is an
    honest answer to a writer where it is a broken invariant to a reader, which
    is why the two policies are two functions over ONE query rather than one
    function with a flag.

    **The order is ``id`` DESC alone, and the term it LOST is the interesting
    half.**  The positional read plan step X-f3c-2a deleted
    (``is_opening = index == 0``) ordered by ``observed_on``, a business date
    any owner may back-date, so an ordinary act silently re-elected the
    opening.  Its replacement led on ``created_at``, justified as "set by the
    database on INSERT and therefore monotone in recording order" -- **which is
    false, and plan step X-f3c-2b-2a's review measured what it cost.**
    :class:`app.models.mixins.CreatedAtMixin` defaults the column to
    ``db.func.now()``, and PostgreSQL's ``now()`` is ``transaction_timestamp()``
    -- the instant the transaction BEGAN.  Two restatements from two tabs can
    therefore commit in the opposite order to their instants, and the second
    one's row sorts BELOW the row it was meant to supersede: the owner is told
    "Books restated" and nothing moves.
    :func:`app.services.loan_anchor_service._governing_loan_anchor` already stated
    the ``now()`` fact for the loan twin; this door never carried it across.

    ``id`` is a sequence value allocated when the INSERT executes, and
    :func:`app.services.opening_service.stage_account_opening` runs under the
    owner's write lock across its compare-and-append wherever another
    transaction could reach the account (a signed-in request's transaction
    takes it before reading any of the owner's data, plan step
    ``balance:X-bn``; at registration the account is uncommitted and no
    other transaction can see it), so within one account
    the id order IS the order the owner made the statements.  The SQL tier
    orders identically from one stated constant
    (:data:`app.opening_infrastructure.GOVERNING_ORDER_SQL`), so the Python
    reader and the database constraint cannot disagree about which restatement
    is in force.

    Args:
        account_id: The account whose opening to load.

    Returns:
        The governing :class:`CashOpeningFact`, or ``None`` when the account
        carries no ``budget.account_openings`` row at all.
    """
    row = (
        db.session.query(AccountOpening)
        .filter_by(account_id=account_id)
        .order_by(AccountOpening.id.desc())
        .first()
    )
    if row is None:
        return None
    return CashOpeningFact(
        opening_id=row.id,
        account_id=account_id,
        opened_on=row.opened_on,
        opening_equity=Decimal(str(row.opening_equity)),
        source_id=row.source_id,
        # Normalised the same way ``cash_anchor_facts`` normalises an
        # assertion's instant: PostgreSQL hands back an aware value, and
        # ``utc_instant`` is where a naive one from a fixture is refused
        # rather than compared against an aware one further downstream.
        recorded_at=utc_instant(row.created_at),
    )


def account_opening_fact(account_id: int) -> CashOpeningFact:
    """Return *account_id*'s GOVERNING opening-equity record.

    The level a cash fold starts from (plan step **X-f3c-2a**).  The table is
    append-only, so an account may carry several rows -- each a restatement of
    what its books opened with -- and the one with the greatest ``id`` governs.
    Which row that is comes from :func:`governing_account_opening`, whose
    docstring states why the order lost its ``created_at`` term at plan step
    **X-f3c-2b-2a**; this adds the READER's policy for an account that carries
    none, and nothing else.

    **It RAISES on an account with no row, and that is reachable only through a
    broken invariant.**  Every account gets one at creation
    (``account_service.create_account``) and migration ``a7c41f9d2b60``
    backfilled every account that predated the table -- including the two
    amortizing loans, because ``balance_at.balance_at`` falls through to this
    fold for an amortizing account carrying no ``LoanParams``.  Answering a
    missing row with ``Decimal("0.00")`` was the alternative and it is exactly
    the fabrication that step exists to delete: it would silently move every
    balance on the account to a level nothing recorded.  The same fail-loud
    placement :func:`~._facts.resolve_anchor` documents for the assertion half.

    Args:
        account_id: The account whose opening to load.

    Returns:
        The account's governing :class:`CashOpeningFact`.

    Raises:
        RuntimeError: When the account carries no ``AccountOpening`` row --
            a broken invariant, not an empty state.
    """
    fact = governing_account_opening(account_id)
    if fact is None:
        raise RuntimeError(
            f"account_opening_fact: account id={account_id} has zero "
            "AccountOpening rows.  Every account carries one -- "
            "account_service.create_account writes it and migration "
            "a7c41f9d2b60 backfilled every account that predated the table -- "
            "so investigate any code path that constructed the Account row "
            "without routing through the canonical factory.  A balance cannot "
            "be folded without the level it starts from, and answering 0.00 "
            "would move every figure on this account silently."
        )
    return fact


def cash_anchor_facts(account_id: int) -> list[CashAnchorFact]:
    """Return an account's balance assertions as facts, in assertion order.

    Loads every :class:`~app.models.account.AccountAnchorHistory` row for the
    account ordered by ``(observed_on, created_at, id)`` -- BUSINESS date first,
    the recording instant breaking a same-day tie so the last one recorded is
    that day's closing balance, and ``id`` breaking a same-instant tie
    deterministically -- and marks the first as the OPENING.

    **The order is BUSINESS-DATE, and the flag is set on THIS list, because
    every consumer of both reads business-date order.**  The rows were loaded
    ``(created_at, id)`` while ``observed_on`` was DERIVED from ``created_at``
    and therefore monotone in it; plan step 2 made the column user-supplied and
    broke that, so the loader now states the order the partition actually uses.
    Getting this wrong is not cosmetic, though what it costs CHANGED at plan
    step X-f3c-2a.  It used to choose which correction books
    ``account_opening`` versus ``account_trueup`` and which one the fold's seed
    swallowed -- three consumers, one keyed on the flag and two on the
    position, which is why "the FIRST is the opening" had to be a single true
    statement rather than three that happen to agree.  All three read
    ``budget.account_openings`` now.  What still rests on this order is the
    REPLAY: :func:`app.services.balance_at._assertions.assertion_corrections`
    walks the assertions in it, and each correction is measured against the one
    before, so a mis-ordered pair still moves two corrections.

    Measured: a fixture that pinned a true-up's instant to an
    exact second while the origination carried the same second plus microseconds
    inverted the two and posted a ``$1,307.66`` true-up to the ledger as the
    account's OPENING.

    **Every assertion, not just the latest.**  Reading only the newest row is
    what makes today's projection fabricate the past: measured on production
    2026-07-25, Checking carries 52 assertions over 119 days and the shipping
    scalar answers ALL 8 pre-anchor periods with today's balance while the
    period map omits them entirely (finding B-18 / cash D3).  A fold over every
    assertion has no such state to invent.

    Args:
        account_id: The account whose assertion history to load.

    Returns:
        The account's :class:`CashAnchorFact` list, chronological.  Empty only
        for an account with no history rows -- unreachable in production
        (migration ``cfb15e782f86`` plus the ``account_service.create_account``
        factory guarantee one), and the state
        :func:`app.services.cash_ledger.resolve_anchor` raises on.
    """
    rows = (
        db.session.query(AccountAnchorHistory)
        .filter(
            AccountAnchorHistory.account_id == account_id,
            # THE RESET reads the owner's levels and nothing else, until the
            # flip (plan step ``balance:X-bj-1``, ruling **R-JN**): a bank
            # statement's placement sits in the same relation since that
            # step and is an observation that moves no balance (ruling
            # **R-IS**), so replaying it here would move money at a step
            # that moves none.  One spelling, deleted with the reset.
            owner_declared_clause(),
        )
        .order_by(
            AccountAnchorHistory.observed_on,
            AccountAnchorHistory.created_at,
            AccountAnchorHistory.id,
        )
        .all()
    )
    return [
        CashAnchorFact(
            anchor_id=row.id,
            account_id=account_id,
            anchor_balance=Decimal(str(row.anchor_balance)),
            # The business date the partition turns on, READ rather than
            # derived (ruling R-DH, plan step 2), beside the recording instant
            # that only breaks a same-day tie.  ``observed_on`` was
            # ``to_display_date(created_at)`` until the column existed, and the
            # backfill is that derivation verbatim -- so the switch moved no
            # figure and every row keeps the day the engine already gave it.
            observed_on=row.observed_on,
            asserted_at=utc_instant(row.created_at),
            # The day it was TYPED, stored rather than converted out of
            # ``created_at`` -- finding N-299.  See the attribute's docstring:
            # the caption that reads it compares against ``observed_on``, and
            # the two must come off one clock.
            recorded_on=row.recorded_on,
            is_opening=(index == 0),
        )
        for index, row in enumerate(rows)
    ]


def coverage_for(account_id: int) -> StatementCoverage:
    """Return *account_id*'s clearing rule, loading its assertions.

    The DATABASE twin of :func:`~._clearing.statement_coverage`, for the callers
    that do not already hold an account's facts -- the entry list's indicator,
    reached from the grid (``entry_service.build_entry_lists_dict``) and from
    the HTMX refresh (``routes/entries.py``).  Neither the entry RESERVATION nor
    the reconcile panel is one of them: ruling **R-FM** dissolved the
    reservation's question at plan step X-f3b, the panel takes the governing
    assertion itself (``cash_ledger.governing_anchor``), and since plan step
    X-f3c-1 the ONE reader of ``walk.coverage`` in ``app/`` is the fold's
    assertion replay (``balance_at._assertions.assertion_corrections``).  It exists for the
    reason :func:`~._facts.reconciled_through` exists beside
    :attr:`~._walk.CashLedgerWalk.reconciled_through` -- a caller holding the
    walk must not pay a query, and a caller rendering one template row must not
    walk an account -- and it is a WRAPPER rather than a second rule, so the two
    cannot come to disagree.

    **It loads ROWS where the boundary it replaces was one ``MAX``**, and that
    cost belongs to the fact rather than to this function: which statement
    cleared a line is a question about a PARTICULAR assertion, so an aggregate
    over the day column cannot answer it.  It is one indexed read per ACCOUNT
    (``idx_anchor_history_account`` leads on ``account_id``) and every caller
    already memoises per account -- ``entry_service.build_entry_lists_dict``
    because a grid render passes ~60 envelopes across ~6 accounts, and the
    reservation because its basis is built once per account per read pass.

    Args:
        account_id: The account whose clearing rule to build.

    Returns:
        Its :class:`~._clearing.StatementCoverage`.  An account with no
        assertion history yields one that clears nothing -- the same honest
        emptiness :func:`~._facts.reconciled_through` answers with a ``None``
        day.
    """
    return statement_coverage(cash_anchor_facts(account_id))


def _movements_of(account_id: int, scenario_id: int, *narrowing):
    """Return the account's PLAN-ROW movements WITH their parents, scoped once.

    The ONE statement of what the plan-row movement stream is scoped by --
    shared by the SETTLED tier (:func:`settled_cash_facts`, the dated
    movements) and the IN-FLIGHT tier (:func:`in_flight_movements`, the
    un-dated ones) so the two are a PARTITION of the same set rather than two
    filters that could disagree about which movements exist at all (plan step
    ``balance:X-bi-4a``).  Four clauses, each load-bearing:

    * ``TransactionEntry.account_id == account_id`` -- **the MOVEMENT's own
      account, never its parent's** (ruling **R-BAL75**): a movement is
      counted where its money moved, and its plan row's ``account_id`` is
      where the row was EXPECTED to be paid from.  The two were held equal
      by ``fk_transaction_entries_parent_account`` until plan step
      ``credit_card:CC-5-1`` dropped it (ruling **R-BAL76**) ahead of the
      card's doors, which write the first movements whose account is not
      their parent's; the posted ledger already attributes by the movement
      (``_posting_purchases._purchase_target``), so the fold, the ledger and
      the anchor self-heal read one predicate rather than agreeing by a key.
    * the parent's ``scenario_id`` -- a movement's scenario is its plan row's,
      read through ``transaction_id`` and never copied (ruling **R-BAL35**).
    * the parent is BALANCE-CONTRIBUTING -- the shared
      :func:`~app.utils.balance_predicates.balance_contributing_clause`, so a
      soft-deleted or Credit / Cancelled parent's movements are worth nothing
      here, as they post nothing (``_posting_purchases.purchase_posts``).
    * the parent is a PLAN ROW, not a transfer's shadow (leaf ``X-bi-6-4a``):
      a leg's movement is read with its transfer by :func:`settled_cash_facts`'
      other arm.  A shadow takes no purchase (``tracks_purchases`` is
      ``False``), so the in-flight tier loses nothing; the clause excludes
      nothing from ``X-bi-6-4d`` and goes with the shadows at ``X-bi-6-5``.

    Deliberately NOT narrowed by the parent's STATUS: a dated purchase
    against a still-Projected envelope has left the bank exactly as one
    against a closed envelope has, and an un-dated purchase against a closed
    envelope is in flight exactly as one against an open envelope is
    (ruling **R-BAL77**).

    The parent rides along through ``contains_eager`` on the join that
    already scopes the query, so no movement costs a second SELECT for its
    direction, budget column or type.  (It was ``movements_with_parents``,
    public, from plan step ``balance:X-bi-6-3`` -- the posting writer's
    transfer-family loader shared the join -- until leaf ``X-bi-6-4a`` moved
    that loader onto ``transfer_legs``' join, where a transfer movement's
    parent is its LEG, not its shadow row.)

    Args:
        account_id: The account the movements are ON.
        scenario_id: The budget scenario the parent rows live in.
        *narrowing: The tier's own clauses over ``TransactionEntry``.

    Returns:
        The matching ``TransactionEntry`` rows, each with ``.transaction``
        populated, unordered.
    """
    return (
        db.session.query(TransactionEntry)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .options(contains_eager(TransactionEntry.transaction))
        .filter(
            TransactionEntry.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            balance_contributing_clause(),
            Transaction.transfer_id.is_(None),
            TransactionEntry.is_credit.is_(False),
            *narrowing,
        )
        .all()
    )


def settled_cash_facts(
    account_id: int, scenario_id: int,
) -> list[CashSourceFact]:
    """Return an account's cash movements as dated facts.

    The ACTUAL events the walk folds: **every DATED movement on the account
    whose parent contributes, and nothing else** (plan step
    ``balance:X-bi-4a``, ruling **R-BAL80**).  A purchase the bank has been
    seen to take, a bill's or a paycheck's covering movement, a transfer
    leg's -- each is one fact, valued by
    :func:`~._cash_leg.movement_cash_leg` (its whole figure in its PARENT's
    direction, ruling **R-BAL35**), dated on its own ``settled_on``, filed
    under its parent's budget column and type, and linked to the statement
    that showed it.  ``opening + SUM(these)`` is the balance, and that
    identity is provable against the pre-state rather than a second balance
    semantics running beside the first.

    **The settled ROW is no longer a fact.**  Through ``X-bi-3e`` this stream
    carried one fact per settled row worth ``settled_cash_leg`` -- the row's
    recorded figure less what its own dated movements already carried -- and
    the two agreed by ruling **R-FM**'s identity because the status seam
    mirrored one record into both homes.  That leg was ZERO for every covered
    bill and paycheck, and for a ``purchases``-basis envelope it was the
    envelope's UN-DATED purchases, booked on the day the row closed.  Ruling
    **R-BAL77** says what those are: movements in flight, held in the
    projection and absent from the actual until the bank is seen to take
    them (:func:`in_flight_movements`).  So the row's own leg is nothing on
    every kind of row, and this stream reads movements alone.  The row's
    record columns are the stale cache ``X-bi-4b`` deletes.

    **Two arms** (leaf ``X-bi-6-4a``, ruling **R-BAL106**): a plan row's
    movements (:func:`_movements_of`), and each transfer LEG's
    (:func:`app.services.transfer_legs.recorded_transfer_legs`) under the
    same scope stated over the TRANSFER, its period and side read there and
    never off the shadow -- which Transfer Invariant 3 holds equal, so the
    arm moved no figure.

    **Three narrowings, each load-bearing** (:func:`_movements_of` holds the
    account, scenario and contributing gate):

    * ``settled_on IS NOT NULL`` -- the trigger itself (ruling R-FM as
      refined by **R-FR**): the day the bank was seen to take the money.
      Whether a STATEMENT was recorded as showing it is
      :class:`~._clearing.StatementCoverage`'s separate question.
    * ``is_credit IS FALSE`` -- a card purchase never touches this account;
      it leaves later through its own CC Payback sibling (the card arc
      retires the flag at CC-7, when a card purchase is a movement on the
      card).
    * the parent is BALANCE-CONTRIBUTING -- a soft-deleted or Credit /
      Cancelled parent's movements are worth nothing, as they post nothing.

    **It loads its own rows and takes no period window, deliberately.**  An
    argument a caller can get wrong is a defect, not a contract (plan Section
    8): the loan fold once TOOK the period list its visibility rule needed,
    and the grid passing a WINDOW moved a balance by $150,000.00 (plan step
    B1).  A fold over a windowed event stream is a fold over a different
    account.

    Args:
        account_id: The account whose movements to load.
        scenario_id: The budget scenario the parent rows live in.

    Returns:
        One :class:`CashSourceFact` per dated movement, ASCENDING by
        ``(settled_on, entry_id)`` -- the order the walk consumes them in,
        the movement's id breaking a same-day tie deterministically.
        Order WITHIN a day moves no figure: the walk only sums a day's
        sources before its assertions close it (ruling R-DH), and the fold
        reads a day's boundary after every step on it, so only the day's
        total enters a balance.  One reader SHOWS it: the bank-agreement
        screen lists a day's movements stable-sorted by size
        (``bank_agreement._rows_on``), so two of equal size on one day keep
        this order -- which the re-key on the movement id at leaf
        ``X-bi-6-4d-1`` may flip (a declared change; 0 listings moved on the
        2026-09-30 00:11 production dump).  The sort is total anyway,
        because a nondeterministic order in a financial replay is a
        reproducibility defect even where it is arithmetically inert.
    """
    facts = [
        _source_fact(entry, entry.transaction, transfer_id=None)
        for entry in _movements_of(
            account_id, scenario_id, TransactionEntry.settled_on.isnot(None),
        )
    ]
    facts.extend(
        _source_fact(leg.record, leg, transfer_id=leg.transfer.id)
        for leg in recorded_transfer_legs(
            TransactionEntry.account_id == account_id,
            Transfer.scenario_id == scenario_id,
            balance_contributing_clause(Transfer),
            TransactionEntry.is_credit.is_(False),
            TransactionEntry.settled_on.isnot(None),
        )
    )
    # The movement's own id breaks a same-day tie: it is the fact's identity
    # (a primary key) and the one id every fact carries, where a transfer
    # leg's ``transaction_id`` names the shadow it still hangs off and is
    # NULL once ``X-bi-6-4d`` re-parents it -- a ``None`` beside an ``int``
    # does not sort.
    facts.sort(key=lambda fact: (fact.settled_on, fact.entry_id))
    return facts


def _source_fact(entry, parent, *, transfer_id: "int | None") -> CashSourceFact:
    """Return ONE dated movement as a fact, its parent a plan row or a leg.

    The construction both arms of :func:`settled_cash_facts` share: the
    movement gives its day, clearing link and identity; the PARENT (a row, or
    a :class:`~app.services.transfer_legs.TransferLeg` answering off its
    transfer and side) gives the budget column, the type and, through
    :func:`movement_cash_leg`, the direction and gate (ruling **R-BAL35**).

    Args:
        entry: The dated movement.
        parent: Its plan row, or the transfer leg whose record it is.
        transfer_id: The leg's transfer, ``None`` for a plan row's movement.

    Returns:
        The :class:`CashSourceFact`.
    """
    return CashSourceFact(
        transaction_id=entry.transaction_id,
        transfer_id=transfer_id,
        entry_id=entry.id,
        pay_period_id=parent.pay_period_id,
        is_income=parent.is_income,
        settled_on=entry.settled_on,
        reconciled_by_id=entry.reconciled_by_id,
        delta=movement_cash_leg(parent, entry),
    )


@dataclass(frozen=True)
class InFlightMovement:
    """A movement the bank has not been seen to take: money on its way out.

    The PLANNED tier's third item kind (plan step ``balance:X-bi-4a``, ruling
    **R-BAL77**), beside a still-Projected row and a still-projected
    transfer leg: a purchase recorded against a contributing envelope whose
    ``settled_on`` is NULL.  It has HAPPENED (``purchased_on``, ruling R-M)
    and has not been OBSERVED to leave, so it is in neither the actual (the
    settled stream reads dated movements alone) nor the plan's unspent
    budget; the projection holds it back on its own, whatever its parent's
    status.  Through ``X-bi-3e`` a Projected envelope's reservation carried
    it inside ``max(estimated - dated - card, undated)`` and a CLOSED
    envelope's row leg booked it on the close day; the identity
    ``max(a, b) = b + max(a - b, 0)`` splits the first into this item plus
    the envelope's unspent budget (:func:`~._amounts._entry_checking_impact`),
    and the ruling deletes the second.

    Attributes:
        transaction_id: The envelope the purchase was recorded against.
        entry_id: The ``budget.transaction_entries`` row.
        pay_period_id: The BUDGET clock -- the parent's column, as every
            movement's is (:class:`CashSourceFact`).
        is_income: The parent's type, so a budget-clock reduction files it on
            the parent's leg.
        purchased_on: The day it happened, the floor the plan's landing day is
            clamped up from (``balance_at._cash_fold._cash_plan``).
        delta: :func:`~._cash_leg.movement_cash_leg` -- its whole figure in
            the parent's direction, signed as the settled stream would sign it
            the day it is dated.
    """

    transaction_id: int
    entry_id: int
    pay_period_id: int
    is_income: bool
    purchased_on: date
    delta: Decimal


def in_flight_movements(
    account_id: int, scenario_id: int,
) -> list[InFlightMovement]:
    """Return the account's movements in flight -- ruling **R-BAL77**.

    The un-dated half of the movement stream :func:`settled_cash_facts` holds
    the dated half of, scoped by the same :func:`_movements_of` so the two
    partition one set; what this half adds is ``settled_on IS NULL`` and
    ``NOT covers_settlement``: a row's own covering movement, kept un-dated
    across a revert (ruling **R-BAL61**), is a retained RECORD and not a
    purchase on its way out -- the ``purchases`` reading of ruling
    **R-BAL68**, stated here through ``status_seam.covering_clause``'s twin
    column rather than the model property because this is a query.

    Args:
        account_id: The account the movements are on.
        scenario_id: The budget scenario the parent rows live in.

    Returns:
        One :class:`InFlightMovement` per un-dated non-card purchase of a
        contributing parent, in ``entry_id`` order.
    """
    return [
        InFlightMovement(
            transaction_id=entry.transaction_id,
            entry_id=entry.id,
            pay_period_id=entry.transaction.pay_period_id,
            is_income=entry.transaction.is_income,
            purchased_on=entry.purchased_on,
            delta=movement_cash_leg(entry.transaction, entry),
        )
        for entry in sorted(
            _movements_of(
                account_id,
                scenario_id,
                TransactionEntry.settled_on.is_(None),
                TransactionEntry.covers_settlement.is_(False),
            ),
            key=lambda entry: entry.id,
        )
    ]
