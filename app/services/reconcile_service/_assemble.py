"""
Shekel Budget App -- Assembling the arms' offers into the panel's blocks

The work that is the SAME whatever produced an offer: labelling the parent a
block hangs under, ordering the blocks, sectioning them, and totalling the set.
The arms decide WHAT this account still owes; this module decides how the answer
READS.

**Why the split is here and not somewhere else.**  Labelling a parent, ordering
what the panel shows and totalling it are the same work whatever produced an
offer, so writing them once per arm would be this arc's own root cause 1
applied to the panel.

**Whether a BILL is a block or a flat row is RULED, and it is one collection**
(**R-FC**, 2026-08-10).  The developer picked the flat-bills panel on sight;
measuring why dissolved the fork, because the whole difference was RENDERING.
So: **one collection here, and three presentational rules** -- a block with no
children prints its name inline rather than above a one-item list (the
template's, off an empty ``purchases``), the ordering key gains a kind term so
like sits with like, and a section label is emitted where the kind changes.
Those reproduce the chosen panel byte-for-byte while X-f2-c2 and X-f2-c3 each
ADD an arm, so nothing here is rewritten inside a money commit (**R-EY**).

**The ORDER is this module's since X-f2-c2, and it had to become so.**  Until
then the block order WAS the purchase arm's insertion order, consumed rather
than re-derived (ruling R1's cash rule: the loader owns the order).  Two arms
return two maps keyed on the same parent, and the union of two dicts has no
meaningful insertion order -- so the seam where the arms MEET is the only place
that can own it.  :func:`_block_order` is written so the one-arm case is
unchanged: with purchases alone the key reduces to each block's oldest
purchase, entry id breaking a same-day tie, which is exactly the sequence the
arm's own ``ORDER BY`` produced.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - Reads AND the write union.  X-f2-c2 was to "decide then, with two arms to
    look at"; a first draft looked, said the writers "share their ORDER (the
    route's) and nothing else", and shipped that order as two statements in a
    route handler.  **That sentence concedes a shared body and files it under
    the wrong tier.**  The order was a rule ABOUT THE ARMS, and the rule it
    encoded has since been REPEALED -- see :func:`record_reconciliation`, which
    still owns the order and now says plainly that it is a convention rather
    than an invariant.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.services.cash_ledger import baseline_amount_basis
from app.services.match_press import Press, Shown
from app.services.pay_calendar import DerivedPeriod, FiledRow
from app.services.transfer_legs import key_order

from . import _purchases, _rows, _transactions, _transfers
from ._offers import (
    OutstandingGroup,
    ReconcileSubmission,
    OutstandingPurchase,
    OutstandingSet,
    OutstandingTransaction,
    Section,
)


def _block_headings(
    statement: _rows.Statement, transaction_ids: "set[int]",
) -> "dict[int, tuple[str, DerivedPeriod]]":
    """Return ``{transaction_id: (name, period)}``.

    The two things a block's heading needs, over the ids the arms have already
    established -- not a relationship walk.  See :func:`outstanding_set` for why
    hydrating the parent costs nine joins and 89 columns to fetch one name.

    **This is the one place in ``app/`` that builds a
    :class:`~app.services.pay_calendar.FiledRow` by hand**, and it is why that
    value's fields are keyword-only (plan step ``pay_calendar:C4-a-5``).  Every
    other caller holds the mapped row and uses
    :meth:`~app.services.pay_calendar.FiledRow.for_row`, which cannot pair one
    row's id with another's period; this one holds a query TUPLE, because
    selecting the entity to get a name back is what the paragraph above
    refuses.  Unpacking that tuple into named locals and naming both fields at
    the constructor is what replaces the guarantee ``for_row`` gives the
    others: the old spelling was ``require_period(row[2], row[0])``, two
    positional indexes into a three-column tuple, and crossing them returns the
    WRONG period rather than raising.

    It is keyed on the ids the caller HOLDS rather than re-deriving any arm's
    offers, so it cannot answer about a different set than the one being
    grouped.

    **The SPAN is DERIVED, and that is pay-calendar plan step C4-a-2's half of
    this function.**  It SELECTed ``pay_periods.start_date`` and ``end_date``
    -- the second of those is a stored copy of a derivable fact and C4-c dropped
    it, which was the one QUERY-position read of it plan finding **P70** had
    left in ``app/``.  The query now asks for the ``pay_period_id`` the row
    already carries and the owner's calendar answers the span, through
    :meth:`~app.services.pay_calendar.PayCalendar.require_period` -- the same
    lookup :func:`~._rows.attributed_on` makes for the same rows, so a block's
    heading and the offer inside it cannot describe two different paychecks.

    **It scopes to the OWNER anyway**, and the redundancy is deliberate.  Every
    id here comes from an arm that already scoped it, so the clause can never
    change an answer today -- which is exactly the argument that would let a
    future caller pass an unscoped set into the one query in a package whose
    stated security property is that scope is SHARED rather than remembered.
    **It is the SAME scope the arms used** -- :attr:`~._rows.Statement.owned_period_ids`,
    the calendar's own saved ids -- rather than a second spelling of it on
    ``pay_periods.user_id``, and that is what makes ``require_period`` below
    unable to refuse: every id reaching this came from a query narrowed by these
    ids, and this query is narrowed by them again.  The cost is one indexed
    predicate.

    Args:
        statement: The statement being reconciled -- its calendar is both who
            the parents must belong to and what dates them.
        transaction_ids: The parents to label.  Empty is answered with an empty
            map and issues no query -- ``IN ()`` is a statement with no rows to
            find.

    Returns:
        One entry per id.  Every id comes from a row this request just read
        inside one transaction, so a missing parent is not reachable; a caller
        that indexed a missing id would raise ``KeyError`` rather than render a
        block with no heading, which is the honest failure.

    Raises:
        RuntimeError: A parent names a pay period the statement's calendar does
            not hold.  Unconstructible: the clause above admits only that
            calendar's own period ids.  Kept as the raising twin a caller
            holding a stored ``pay_period_id`` is supposed to use, so a future
            caller reaching this from an unscoped set fails loudly rather than
            publishing a heading nobody can date
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).
    """
    if not transaction_ids:
        return {}
    rows = (
        db.session.query(
            Transaction.id,
            Transaction.name,
            Transaction.pay_period_id,
        )
        .filter(
            Transaction.id.in_(transaction_ids),
            Transaction.pay_period_id.in_(statement.owned_period_ids),
        )
        .all()
    )
    headings: "dict[int, tuple[str, DerivedPeriod]]" = {}
    for row_id, name, period_id in rows:
        headings[row_id] = (name, statement.calendar.require_period(FiledRow(
            table=Transaction.__table__.fullname,
            row_id=row_id,
            period_id=period_id,
        )))
    return headings


def _block_order(
    group: OutstandingGroup,
) -> "tuple[int, date, int, tuple[int, int, int]]":
    """Return a block's sort key: its section, then its OLDEST offer.

    One rule over every arm, and total: every offer contributes
    ``(day, arm rank, identity)`` and the block takes the minimum.  A purchase
    offers its purchase day, a settle its attribution day; the arm rank breaks
    a same-day tie between them so a block is never ordered by whichever arm
    happened to be read first, and the identity breaks the rest.

    **The identity goes through ``transfer_legs.key_order``** since leaf
    ``balance:X-bi-6-4c-2``: a settle's key is a row's int id or a transfer
    leg's ``(transfer id, account id)`` pair (ruling **R-BAL87**), and Python
    refuses to compare the two -- ``key_order`` is the one total order over
    both (every row before every leg, rows by id, legs by transfer then
    account).  A purchase's entry id rides the row arm of it, which orders it
    exactly as the bare id did.  Within one section every key has one shape
    today, so the order is unchanged for rows; a same-day pair of transfer
    blocks now orders by transfer id where it ordered by shadow id.

    **The one-arm case is unchanged, which is what keeps this out of R-EY's
    way.**  With purchases alone the key reduces to (oldest purchase day, 0,
    that purchase's entry id) -- the first appearance of each parent in a list
    the purchase arm already sorted ``(purchased_on, id)``, i.e. exactly the
    insertion order this module consumed before there was a second arm.

    Args:
        group: The block, before its section label is resolved.

    Returns:
        ``(section rank, day, arm rank, identity order)``.  A block always
        carries at least one offer -- :func:`outstanding_set` builds none
        otherwise -- so the minimum is always defined.
    """
    offers = [
        (purchase.purchased_on, 0, key_order(purchase.entry_id))
        for purchase in group.purchases
    ]
    if group.settle is not None:
        offers.append(
            (group.settle.attributed_on, 1, key_order(group.settle.key)),
        )
    day, arm, identity = min(offers)
    return (group.kind.rank, day, arm, identity)


def _sectioned(
    groups: "list[OutstandingGroup]",
) -> "tuple[OutstandingGroup, ...]":
    """Return *groups* with a section label on the first block of each kind.

    Ruling **R-FC**'s third presentational rule, applied where the ORDER is
    known.  A template deriving it would compare against the previous element
    by index, and index arithmetic over a sorted list is how a heading silently
    stops appearing -- on a screen whose sections are the only thing
    distinguishing a `$412.33` envelope from a `$412.33` bill.

    Args:
        groups: The blocks, already ordered by :func:`_block_order`.

    Returns:
        The same blocks, each carrying a :class:`Section` where its kind
        first appears and ``None`` elsewhere.
    """
    labelled = []
    previous = None
    for group in groups:
        starts_a_section = group.kind is not previous
        labelled.append(replace(
            group,
            section=Section(
                label=group.kind.section_label,
                note=group.kind.section_note,
            ) if starts_a_section else None,
        ))
        previous = group.kind
    return tuple(labelled)


def _tally(
    offers: "list[OutstandingPurchase | OutstandingTransaction]",
) -> "tuple[int, Decimal]":
    """Return ``(count, total)`` for one kind of offer.

    Stated once because the set publishes the same pair THREE times -- purchases,
    payments and deposits (rulings **R-FA** / **R-FD**) -- and a hand-written
    second sum is where one of them ends up counting another's rows.  It takes
    anything carrying an ``amount``, which is both offer types, because the
    reduction is about a list of money and not about which arm produced it.

    **The sum is SIGNED and it nets** (ruling **bank_import:R-II**, plan step
    ``bank_import:X-gj-2b-3``).  A refund is a negative purchase, so the
    purchase tally over `$120.00` and `-$45.00` is ``(2, 75.00)`` -- two rows to
    tick, and `$75.00` of net movement the envelope is still holding back.  The
    alternative -- summing magnitudes, or splitting the tally by direction --
    would make the figure disagree with the reservation the panel's own
    sentence is about, which is the defect that sentence was already corrected
    for once by `$488.16`.

    Args:
        offers: The offers of one kind, each carrying an ``amount``.

    Returns:
        How many, and what they are worth -- the count of ROWS and the SIGNED
        sum of their amounts.
    """
    return (
        len(offers),
        sum((offer.amount for offer in offers), Decimal("0.00")),
    )


def _summarise(groups: "tuple[OutstandingGroup, ...]") -> OutstandingSet:
    """Reduce the assembled blocks into the set the boundary publishes.

    **Every tally is read off the BLOCKS, so the three pairs describe exactly
    what the panel renders.**  The two settle tallies were reduced over the
    arms' own map until pay-calendar plan step C4-a-2 moved them here; a block
    carries the settle the map held, so the answer is the same and it now has
    one source instead of two.  Each pair goes through :func:`_tally` for that
    function's own reason: three sums of one set written three ways is where one
    of them ends up counting another's rows.

    Split from :func:`outstanding_set` because assembling the blocks and
    reducing them are two jobs, and holding both put sixteen names in one
    frame.

    Args:
        groups: The blocks, ordered and sectioned -- the value the set will
            publish, so nothing here can tally a set the caller does not ship.

    Returns:
        The :class:`~app.services.reconcile_service.OutstandingSet`.
    """
    settles = [
        group.settle for group in groups if group.settle is not None
    ]
    purchase_count, purchase_total = _tally(
        [purchase for group in groups for purchase in group.purchases],
    )
    payment_count, payment_total = _tally(
        [offer for offer in settles if not offer.is_income],
    )
    deposit_count, deposit_total = _tally(
        [offer for offer in settles if offer.is_income],
    )
    return OutstandingSet(
        groups=groups,
        purchase_count=purchase_count,
        purchase_total=purchase_total,
        payment_count=payment_count,
        payment_total=payment_total,
        deposit_count=deposit_count,
        deposit_total=deposit_total,
    )


def outstanding_set(statement: _rows.Statement) -> OutstandingSet:
    """Return what this account has not been seen to have paid for, grouped.

    The reconcile panel's list.  It asks each arm what it still owes against
    *observed_on*, labels every parent that came back, and reduces the result
    into the :class:`~app.services.reconcile_service.OutstandingSet` the
    boundary publishes.  THREE arms answer: purchases, the source rows
    themselves (plan step X-f2-c2) and transfer legs (plan step X-f2-c3,
    offered as LEGS since leaf ``balance:X-bi-6-4c-2``).  The source-row arm
    answers for TWO scopes since plan step ``credit_card:CC-5-4b``: the rows
    on this account, and the rows planned on another whose kept payment is on
    this one (the "Paid from this account" section, ruling **R-CC44**).

    **The two ROW arms are unioned on the PARENT's id**, which is why each
    keys its offers on it: an envelope with outstanding purchases AND an
    overdue close is ONE block carrying both, which is ruling **R-EW**'s
    shape, while a bill is a block with a close and no children.  **The
    transfer arm hands over finished BLOCKS** instead
    (:func:`~._transfers.outstanding_transfers`): a leg is always childless and
    headed by its own label and period, so it has nothing to union and no
    parent to look up, and its key -- a ``(transfer id, account id)`` pair --
    never enters the row-keyed map, where a transfer id could otherwise meet
    a bill's equal row id.  Until that leaf the arm offered the transfer's
    SHADOW row into this map, and only the table's partition on
    ``transfer_id`` kept the two apart.

    **The parents are read in ONE narrow statement, and that is a fix rather
    than a tidy-up.**  The flat reader this replaced returned bare
    :class:`~app.models.transaction_entry.TransactionEntry` rows and the
    template reached ``entry.transaction.name`` per line -- a SELECT per
    distinct parent on a ``lazy="select"`` relationship.  A ``joinedload`` of
    that relationship would fix the count and cost a statement carrying **12
    LEFT OUTER JOINs and 121 selected columns**, because ``Transaction``
    eager-joins its account, status, category and type; ``Account`` eager-joins
    its TYPE and three parameter tables; and one of those parameter tables
    eager-joins a fourth -- nine in all, on the entity alone, to fetch one
    name.  *The enumeration accounted for eight of the nine until an
    adversarial review summed it against the measurement:
    ``Account.account_type`` was the one it did not name.*

    Hydrating the parent DIRECTLY, which is what would let ``_block_headings``
    use :meth:`~app.services.pay_calendar.FiledRow.for_row`, is **9 joins and
    89 columns** against the current **0 and 3**.  The grouping needs two scalars
    and a period id per parent, so it asks for three columns.

    *Re-measured 2026-08-31 at plan step ``pay_calendar:C4-a-5`` by compiling
    both statements against the postgres dialect with no database, because this
    paragraph is quoted as a REASON and it had drifted: it said "13 LEFT OUTER
    JOINs and around a hundred columns" undated, where the mappers now produce
    12 and 121.  The conclusion is unchanged and the figure was not.*

    Reads only (no writes, no commit).

    Args:
        statement: The :class:`~app.services.reconcile_service.Statement` being
            reconciled -- the owner's pay calendar, the account whose balance
            was asserted, and the governing assertion.  **Built by the ROUTE and
            threaded to all three arms since pay-calendar plan step C4-a-2**,
            where it was three loose arguments each arm reassembled for itself.
            The calendar arrived because this panel DATES every row it offers
            and a pay period's span is derived; it stayed as the whole value
            because whose rows these are, which account, and which assertion are
            not independent facts, and the same value is what the WRITE half
            takes (:class:`ReconcileSubmission`) -- so the offer set and the
            tick cannot describe different statements.

    Raises:
        BaselineMissingError: From
            :func:`~app.services.scenario_resolver.require_baseline_scenario`,
            answered by the application-level handler that renders the
            setup-recovery page (ruling **R-BW**).  The RAISING form is right
            on that ruling's own criterion: the panel prices every offer from
            a scenario's salary profiles and a scenario's loans, so without one
            there is no honest figure to publish -- and this panel's figures are
            what a tick BOOKS.

    Returns:
        The :class:`~app.services.reconcile_service.OutstandingSet`, its
        ``groups`` ordered by section and then by each block's oldest offer
        (:func:`_block_order`), each block's purchases oldest first.  Empty for
        an account with nothing outstanding -- **which stopped being the steady
        state at plan step X-f2-c2**, because an envelope's close is offerable
        for the whole of its own period and only closing it clears one.
        Replayed over production's 53 Checking assertion DAYS (57 assertion
        ROWS -- the two figures answer different questions and one word was
        doing both jobs), 46 carry at least one offer.  This docstring said 48
        and 44 until plan step X-f2-c3 re-took the measurement; its two
        siblings said 53 and 46, so one package held two answers to one count.
        Finding **N-227** owns that bound.
    """
    blocks = _purchases.outstanding_purchases(statement)
    # **ONE amount basis for the whole panel** (plan step X-au-j, finding
    # **N-295**).  Both source-row arms price every offered row through their
    # own ``settle_amount``, and each of those built its own basis -- so K
    # offered paychecks ran the paycheck engine K times over the owner's whole
    # pay-period set, and K offered shadows each paid the scenario-wide
    # loan-config join plus a full loan resolve.  That is finding **N-228** one
    # tier up, which ``amount_basis``'s own docstring names, and N-295 records
    # it at these two call sites by name.
    #
    # The BASELINE pin and its Phase-1 deferral are stated ONCE, in
    # ``cash_ledger.baseline_amount_basis`` -- three surfaces make the same
    # pin and spelling it at each was three places to edit when what-if
    # scenarios land.  It matches the ground ``_rows.outstanding_scope`` gives
    # for not filtering the offer set on ``scenario_id``.
    #
    # **A foreign-scenario row would RAISE out of this panel**, where the
    # review pass one package over reports it instead (``_valuation.transaction_price``
    # catches ``AmountUnresolvable`` and drops the row into ``unpriceable``).
    # The two passes answer it differently on purpose and neither is reachable
    # today; stated so the next reader does not assume symmetry.
    basis = baseline_amount_basis(statement.owner_id)
    # The purchase and transaction arms union on the parent ROW's id: both key
    # a ``budget.transactions`` id, so an envelope's purchases and its close
    # meet in one block.
    settles = _transactions.outstanding_transactions(statement, basis)
    parents = set(blocks) | set(settles)
    headings = _block_headings(statement, parents)

    groups = [
        OutstandingGroup(
            key=row_id,
            name=headings[row_id][0],
            period=headings[row_id][1],
            purchases=tuple(blocks.get(row_id, ())),
            settle=settles.get(row_id),
            # Resolved by ``_sectioned`` once the order is known: a block
            # cannot know whether it STARTS a section before it knows what
            # precedes it.
            section=None,
        )
        for row_id in parents
    ]
    # The transfer arm's blocks are whole already -- childless, headed by
    # their own leg -- and keyed by a pair no row id can equal.
    groups.extend(_transfers.outstanding_transfers(statement, basis))
    # The "Paid from this account" blocks are whole too -- childless, headed
    # by their own label (plan step credit_card:CC-5-4b).  Their keys are row
    # ids, and none can equal a parent above: this scope takes rows planned
    # on ANOTHER account and holding no purchase, and every parent above is a
    # row ON this account or holds a purchase on it.
    groups.extend(_transactions.outstanding_settlements(statement, basis))
    groups.sort(key=_block_order)
    return _summarise(_sectioned(groups))


def record_reconciliation(submission: ReconcileSubmission) -> int:
    """Record everything a statement settled, in the ONE order that works.

    The write union.  Each arm still owns what a tick MEANS for its own rows
    (ruling **R-FA**); what lives here is the rule that spans them, and it is
    not an HTTP concern:

    **Purchases are stamped BEFORE the source rows settle, and the reason that
    was an INVARIANT has been repealed** (plan step X-au-c3, developer
    2026-08-17).  It held because the purchase arm's scope required a PROJECTED
    parent and settling an envelope's close took that parent out of it, so the
    reversed order silently skipped every purchase ticked on a block whose close
    was also ticked.  :func:`app.services.reconcile_service._purchases._outstanding_scope`
    no longer requires a projected parent, so that cannot happen: a settled
    parent's purchases stay in scope, which is the whole point of the widening.

    **The order is KEPT and is now a CONVENTION, and saying which it is is the
    point.**  Nothing this step could establish still depends on it -- an
    envelope's settled figure is the sum of ALL its entries whatever their
    posting days, and the ledger reconcile each arm runs is idempotent and
    order-free (``_post_stamped_purchases`` reconciles the whole family, so both
    orders converge on the same legs).  A rule whose reason has been deleted
    must not go on presenting itself as an invariant: the next reader would
    defend it for a cause that no longer exists, which is the shape this arc's
    own stale citations are made of.  If a later step finds a real dependency,
    it belongs here with its measurement.

    It was two statements in a route handler until an adversarial review named
    it: an order in a tier that owns neither arm, with nothing able to fail if a
    later edit swapped them.

    **The transfer arm's position is FREE and is fixed anyway.**  Its items
    are transfers' legs, which no other arm's scope admits -- the transaction
    arm's clause excludes every shadow row, and a leg holds no purchase: its
    one possible entry (the seam's covering movement, plan step
    ``balance:X-bi-3c``; un-dated under a reverted leg since
    ``balance:X-bi-3e-2``) is kept out of the purchase arm's scope by
    ``status_seam.covering_clause()`` whatever its day says -- so no ordering
    between it and either of them can change an outcome.  It runs last because
    a sequence with one hard rule in it should not also have an unstated
    arbitrary part: the order is written down here so a reader learns which
    half is which.

    **Each settle arm is handed its OWN TABLE's form field** (ruling
    **R-BAL145**, leaf ``balance:X-bi-6-4c-2``): the rows' ``transaction_ids``
    and the transfers' ``transfer_ids``, with their amount boxes, and each arm
    re-scopes what it is handed.  They shared ONE ``transaction_ids`` set until
    that leaf, when a transfer's tick carried its shadow's row id and the two
    scopes partitioned ``budget.transactions``; a transfer's tick carries the
    TRANSFER's id now, which can equal a row's, so one field could not say
    which a posted number meant.  **The rows' two scopes both read
    ``transaction_ids``** (ruling **R-CC116**, plan step
    ``credit_card:CC-5-4b``): the number names a row in both, and they
    partition on the row's account, so each posted id loads in at most one
    of them -- and the one that settles it first leaves it Projected in
    neither.  The second runs directly after the first because they read one
    field; nothing else about its position matters.

    **All three run in the caller's transaction and NONE commits.**  A
    statement is one act: four purchases, their envelope's close and the
    transfer beneath them mean all six or none, so a commit between the arms
    would leave the part that failed invisible behind a rendered success.

    Args:
        submission: The :class:`ReconcileSubmission` -- one statement's worth of
            ticks, already parsed and owner-scoped by the route.

    Returns:
        How many of the submitted ticks actually LANDED, across all three arms
        -- never what was asked for.  The caller compares it against what was
        submitted to tell a user their ticks landed on rows something else had
        already moved.  **One total rather than a tuple per arm**, because that
        is the only thing the caller does with them and a per-arm breakdown
        would be three numbers nobody adds up differently.

    Raises:
        ValidationError: A ticked ``$0.00`` box (ruling **R-CC125**), or
            propagated from a settle verb -- an illegal transition a stale
            panel can still submit, or a save freeing other bank lines than
            the panel named for the rows ticked (``NamedLines.for_ticks``:
            a shared match's lines only when all its rows are;
            ``PageOutOfDate``, ruling
            **R-CC127**: *"on the reconcile panel, one out-of-date row means
            nothing on it saves"*, and ruling **R-CC135**: compared ONCE, for
            the whole save, at its close -- so a tick whose settle reaches no
            match step, its purchase deleted in another tab, still refuses
            the save when its row's caption named a line, ledger row
            **BAL-597**).
        PostingError: Propagated from a verb's ledger reconcile.  Fails loud.
        RuntimeError: A ticked row names a pay period the submission's calendar
            does not hold
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`, via
            :func:`~._rows.attributed_on`'s share of the offer bound).
    """
    statement = submission.statement
    # ONE press for the panel's save (ruling R-CC135, "One check per save"):
    # what the page named is every line captioned under a row the owner
    # TICKED, and every line of a shared match whose rows were ALL ticked
    # (``NamedLines.for_ticks``), and what the whole save frees must equal it
    # at the close.  A
    # transfer's tick names no bank line -- a leg holds no purchase, so once
    # a typed ``$0.00`` box is refused (ruling R-CC125) the panel has no
    # transfer tick to caption -- so a leg's settle freeing a line no row
    # named is refused (``_transfers._settle_one``).
    # PROMISED: a row's caption names what ITS tick frees, so a ticked row
    # whose save went another way refuses the save (ledger row BAL-597).
    with Press(
        Shown(submission.named.for_ticks(submission.transaction_ids)),
        promised=True,
    ) as press:
        purchases = _purchases.record_settled_days(
            statement, submission.entry_ids,
        )
        source_rows = sum(
            _rows.record_settled(
                arm, statement, tick_ids, corrections, press=press,
            )
            for arm, tick_ids, corrections in (
                (
                    _transactions.ARM,
                    submission.transaction_ids, submission.corrections,
                ),
                (
                    _transactions.SETTLEMENT_ARM,
                    submission.transaction_ids, submission.corrections,
                ),
                (
                    _transfers.ARM,
                    submission.transfer_ids, submission.transfer_corrections,
                ),
            )
        )
    return purchases + source_rows
