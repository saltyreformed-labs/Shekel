"""Account-anchor genesis walk: the DAY-granular correction producer.

The pure READ/COMPUTE half of the Build-Order Step 5 write side: one
chronological walk (:func:`walk_account_ledger`) replays a NON-loan account's
anchor assertions -- its :class:`~app.models.account.AccountAnchorHistory`
rows -- against the settled source facts already posted on its linked ledger,
and produces the balanced OPENING / TRUE-UP corrections that drive the
ledger's running total to each asserted balance at its assertion moment.  The
reconcile / posting of those corrections lives in the sibling module
(:mod:`._anchors`); this module only reads and computes (no writes, no
commit).

**A RECORDED clearing fact where there is one, and the civil DAY where there is
not** (ruling **R-FL**, plan step X-f3a-1).  A source names the
``account_anchor_history`` row whose statement showed it, and that assertion is
the one that absorbs it.  Where none is recorded, an anchor is the CLOSING
BALANCE for the civil day it was observed on, so it already reflects every
settled movement dated that day or earlier (``apply_anchor_true_up``: "the user
is declaring 'my real checking is now $X' -- every past-dated debit purchase is
already in that number").  Never by pay period -- a period-granular walk would
mis-state the ledger by every pre-true-up settle in the anchor period (the plan
review's CRITICAL-1) -- and never by instant, which decided the question by
which button the user pressed first and cost production ``$4,001.42`` on
2026-07-31 (ruling R-DH,
``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  The
DAY rule survives as the answer for an UNKNOWN source rather than as the rule,
because the developer's own bank exports measured it: only 33 of 110 matched
movements carry the day the bank posted them.

**One boundary for both anchor kinds**, opening and true-up alike (finding
N-133 / F1).  The OPENING was excepted for one day -- its boundary stepped back
a day so its own day's sources rode on top -- and the exception was reverted
once scored: it made the walk read ``$4,804.00`` for a day production's bank
showed ``$2,746.58``, and it forced the rule to be stated twice by hand, as a
date boundary here and as a sort position in the read fold.

**Source facts are read back from the ledger, never re-derived from
transaction rows.**  Grouping the linked ledger's own postings by source is
timing-proof against the reverse-before-delete discipline (a reverted source
nets to zero and drops out regardless of partition) and future-proof for new
source kinds (any movement-linked entry is attributed by its linkage, not by
an enumerated source-kind list).  A transfer's legs are its two covering
movements' since plan step ``balance:X-bi-6-3`` (ruling **R-BAL45**), read by
the movement loader like any movement's; that step deleted the
``transfer_id``-keyed loader the one-entry transfer shape was read by.  The
``transaction_id``-keyed loader a row's own leg was read by, and the
``transfer_id IS NULL`` guard every loader carried, went with plan step
``balance:X-bi-6-5``'s second leaf (card plan#1845), whose migration deletes
every entry those two sources posted (ruling plan#1867): all of them were
reversed to zero, each on its own key.

The loan analogue is :mod:`app.services.loan_ledger`; loans
never walk here (their anchors are dated ``LoanAnchorEvent`` facts replayed
with amortization, a different mechanism), and :func:`walk_account_ledger`
refuses an amortizing account outright.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod
from app.models.transaction_entry import TransactionEntry
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.posting_reads import PostingError, _ledger_account_for
from app.services.cash_ledger import (
    account_opening_fact,
    cash_anchor_facts,
    statement_coverage,
)

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class AccountAnchorCorrection:
    """One correction the posted ledger owes: the OPENING, or a true-up.

    The per-correction result of :func:`walk_account_ledger`.  The
    linked-ledger delta is ``target_balance - ledger_before`` (its counter leg
    the negative), so the two sum to zero and the linked ledger's total lands
    exactly on the target.  A correction whose ``ledger_before`` already equals
    its target books nothing (a fresh $0 account mints no entries and no equity
    row).

    **It carries no ``CashAnchorFact`` since plan step X-f3c-2a, and that is
    the point.**  Every field the reconcile needs is now stated ON the
    correction, because the FIRST correction is no longer an assertion at all:
    it is the account's stored OPENING EQUITY
    (``budget.account_openings``), which has an ``opened_on`` and an amount but
    no assertion behind it.  Reading ``correction.anchor.is_opening`` was how
    the posting path decided which correction opened the books, and that read
    was positional -- whichever assertion sorted first -- so a BACK-DATED
    assertion silently re-elected the opening AND re-dated its journal entry.
    The loan side has never had that problem for the same reason this no longer
    does: its origination is SYNTHESIZED from a stored fact
    (``loan_loaders.synthesize_origination_anchor``) rather than elected from
    the assertion series.

    Attributes:
        observed_on: The civil day this correction's journal entry is dated --
            the assertion's own ``observed_on``, or the opening's ``opened_on``.
        target_balance: What the linked ledger must read once this correction
            posts -- the asserted balance, or the opening equity.
        ledger_before: The walked ledger total JUST BEFORE this correction --
            the prior corrections' cumulative effect plus every source net
            attributed on or before this day.  ``Decimal("0.00")`` for the
            opening, which by definition precedes every posting.
        opens_the_books: ``True`` for the ONE correction that books the
            account's opening equity, ``False`` for every balance assertion.
            A stored fact read off ``budget.account_openings``, not a position
            in a list: it selects the ``account_opening`` journal source and
            the ``anchor_equity`` counter, where an assertion selects
            ``account_trueup`` and (for a modelled account, ruling R-FO)
            ``interest_income`` or ``unrealized_change``.
    """

    observed_on: date
    target_balance: Decimal
    ledger_before: Decimal
    opens_the_books: bool


@dataclass(frozen=True)
class _PostedSource:
    """One source's posted effect on a linked ledger, with what cleared it.

    The two source loaders' shared return shape, and it is a RECORD rather
    than the ``(day, net)`` tuple it replaced because the walk now needs a third
    fact: which statement was recorded as showing this money (ruling **R-FL**).
    It satisfies :class:`app.services.cash_ledger.ClearableLine`, so
    :class:`~app.services.cash_ledger.StatementCoverage` is asked of it directly
    -- the same rule, over the same two fields, that the read replay asks of a
    :class:`~app.services.cash_ledger.CashSourceFact`.

    Attributes:
        settled_on: The civil day this source's cash moved -- a MOVEMENT's
            stored ``settled_on`` (a purchase, a covering movement, a
            transfer's side), and for RESIDUE the period's ``start_date``.
        reconciled_by_id: The ``account_anchor_history`` row whose statement was
            recorded as showing it, or ``None``.  **Residue always answers
            ``None``, structurally**: its journal entries carry no movement
            link by construction, so there is no row to read a link from.  That
            is not a gap -- an unlinked source is UNKNOWN, and the clearing rule
            answers UNKNOWN from the date exactly as this walk always has.
            Measured on a 2026-08-14 production clone: every nonzero residue
            group belongs to a LOAN account, which
            :func:`walk_account_ledger` refuses outright, so the bucket is empty
            for every account this walk actually runs.
        net: The source's current posted net on the linked ledger.
    """

    settled_on: date
    reconciled_by_id: "int | None"
    net: Decimal


def _linked_net_rows(
    linked_ledger_id: int, scenario_id: int, group_column, extra_filters: list,
) -> list:
    """Return ``(group_key, net)`` sums over one linked ledger's postings.

    The shared query shape of the two source loaders: sum
    ``account_postings.amount`` over the linked ledger's legs in
    *scenario_id*, grouped by the caller's source-identity column
    (``transaction_entry_id``, or the residue's pay period).

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.
        group_column: The column to group (and select) by.
        extra_filters: The caller's linkage filters (and, for the residue
            loader, the correction-source exclusion).

    Returns:
        ``(group_key, net Decimal)`` rows (zero-net groups included; the
        callers drop them).
    """
    return (
        db.session.query(group_column, db.func.sum(Posting.amount))
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
            *extra_filters,
        )
        .group_by(group_column)
        .all()
    )


def _purchase_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return one :class:`_PostedSource` per movement-linked source.

    The movement partition (ruling **R-FM**, plan step X-f3b): a PURCHASE whose
    bank posting day the owner recorded books its own cash leg, so the walk must
    read that leg's own day and its own clearing link -- and since plan step
    ``balance:X-bi-3b`` a bill's or a paycheck's covering movement, and since
    ``balance:X-bi-6-3`` each side of a settled transfer (ruling **R-BAL45**:
    the ``transfer_movement`` source, keyed by the shadow's covering movement
    exactly as a purchase is keyed, so this account's side of a transfer is
    read off ITS movement's day and ITS movement's clearing link, which the
    seam mirrors from the shadow on this account).  Groups the linked ledger's
    postings under movement-linked journal entries by ``transaction_entry_id``
    and attributes each nonzero net to the STORED
    ``transaction_entries.settled_on``.

    **This is the loader the linkage exists for.**  Grouping a purchase's legs
    under its PARENT instead would date them at
    ``transactions.settled_on`` -- a day a still-projected envelope does not
    have at all -- and would read the PARENT's ``reconciled_by_id``, attributing
    whichever statement showed the envelope's close to money that left the bank
    days earlier.  Both facts are per-purchase, so the source is per-purchase.

    It reads no shared ``settled_day`` accessor: that one REFUSES a settled
    row carrying no day, because a settled transaction with no day is a broken
    invariant.  A purchase with no day is an ordinary outstanding purchase -- it
    simply posts nothing, so a nonzero net against one is the broken state, and
    it is refused below with the missing-linkage case rather than dated by any
    fallback.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources, unordered (the walk sorts the merged set).

    Raises:
        PostingError: If a nonzero net's ``transaction_entry_id`` resolves no
            purchase row, or resolves one carrying no ``settled_on``.  The FK is
            ON DELETE SET NULL, so a live id always resolves; a miss means the
            reverse-before-delete discipline was violated
            (``posting_service.reverse_purchase_postings_before_delete``), and a
            dayless resolution means a leg was posted for a purchase that has
            not been seen to move.  Either must fail loudly rather than silently
            mis-partition real money.
    """
    nets = {
        entry_id: net
        for entry_id, net in _linked_net_rows(
            linked_ledger_id, scenario_id, JournalEntry.transaction_entry_id,
            [
                JournalEntry.transaction_entry_id.isnot(None),
            ],
        )
        if net != 0
    }
    if not nets:
        return []
    # The clearing link comes off the SAME row the day does, in the same read:
    # two queries for two fields of one fact is how the two come to describe
    # different rows.
    dated = (
        db.session.query(
            TransactionEntry.id, TransactionEntry.settled_on,
            TransactionEntry.reconciled_by_id,
        )
        .filter(TransactionEntry.id.in_(nets))
        .all()
    )
    facts = {
        entry_id: (settled_on, reconciled_by_id)
        for entry_id, settled_on, reconciled_by_id in dated
        if settled_on is not None
    }
    missing = set(nets) - set(facts)
    if missing:
        raise PostingError(
            f"Ledger account {linked_ledger_id} holds a nonzero net for "
            f"purchase ids {sorted(missing)} that either no longer exist or "
            f"carry no recorded posting day; a purchase's legs are reversed "
            f"before it is deleted and are only ever written for a purchase "
            f"that has one, so the linkage invariant is broken."
        )
    return [
        _PostedSource(
            settled_on=facts[key][0],
            reconciled_by_id=facts[key][1],
            net=nets[key],
        )
        for key in nets
    ]


def _residue_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return one :class:`_PostedSource` for each unlinked non-correction group.

    The residue bucket: journal entries on the linked ledger with no movement
    link and a non-correction source kind -- in practice hard-delete residue,
    whose ``transaction_entry_id`` was SET-NULLed when the movement was
    deleted.  The movement link is the one concrete source FK an entry can
    carry since plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845)
    took the ``transfer_id`` and ``transaction_id`` links' readers, its
    migration the columns and every entry they named (ruling plan#1867).
    (A loan payment's split is sourceless by design since ruling
    **R-BAL102** and is not residue, but it books onto the loan's own chart
    rows and never onto a linked ledger this walk runs over.)
    Each
    period's residue is attributed at that period's ``start_date`` -- a civil
    date used AS a civil date, never routed through an instant, which is the
    same discipline the settled-source loaders keep (ruling R-DH):
    the reverse-before-delete discipline nets residue to zero per account
    AND per period (R2 stamps a reversal into the period of the postings
    it reverses), so every group here sums to zero and is dropped -- but
    reading it keeps the walk's ``ledger_before`` equal to the live linked
    total even if that discipline is ever violated, instead of silently
    diverging from what the true-up chokepoint reconciled against.

    The account's OWN ``account_opening`` / ``account_trueup`` corrections
    are excluded -- they are the walk's OUTPUT, not source facts (the loan
    correction kinds never appear on a non-loan linked ledger: the chart
    resolvers guarantee loan and non-loan corrections book onto disjoint
    accounts).

    **Residue can carry NO clearing link, structurally**, because its movement
    link is NULL by construction -- there is no row to read one from.  It is
    therefore always UNKNOWN, and the clearing rule answers an UNKNOWN source
    from its date, which is exactly what this loader has always supplied
    (ruling **R-FL**; the ruling's own amendment names this loader as the third
    reason a link could not simply REPLACE the date rule).  Measured on a
    2026-08-14 production clone: every nonzero residue group belongs to a LOAN
    account, which :func:`walk_account_ledger` refuses outright, so the bucket
    is empty for every account this walk actually runs.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources, unordered (the walk sorts the merged set); empty whenever
        the reverse-before-delete discipline held.
    """
    excluded_source_ids = [
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
    ]
    nets = {
        pay_period_id: net
        for pay_period_id, net in _linked_net_rows(
            linked_ledger_id, scenario_id, JournalEntry.pay_period_id,
            [
                JournalEntry.transaction_entry_id.is_(None),
                JournalEntry.source_kind_id.notin_(excluded_source_ids),
            ],
        )
        if net != 0
    }
    if not nets:
        return []
    # Two-step like the sibling loaders: group by the entry's own NOT NULL
    # pay_period_id (no extra join in the grouped query), then batch-load
    # the period starts -- the FK guarantees every id resolves.
    dated = (
        db.session.query(PayPeriod.id, PayPeriod.start_date)
        .filter(PayPeriod.id.in_(nets))
        .all()
    )
    return [
        _PostedSource(
            settled_on=start_date,
            reconciled_by_id=None,
            net=nets[pay_period_id],
        )
        for pay_period_id, start_date in dated
    ]


def _source_net_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return every source fact on one linked ledger, sorted by day.

    The union of the TWO source partitions -- movement-linked (ruling
    **R-FM**, plan step X-f3b; a transfer's sides since
    ``balance:X-bi-6-3``) and residue -- covering every posting on the
    linked ledger except the account's own anchor corrections, each as a
    :class:`_PostedSource`.
    Sorted ascending by day, which is no longer what decides which assertion
    absorbs what (that is the clearing rule's, ruling **R-FL**) but keeps the
    walk's own iteration reproducible.  It took the
    walked ``account_id`` while the transfer loader needed to know which of a
    transfer's two shadows was this ledger's; a movement is on one account.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources ascending by day (nets are nonzero).
    """
    sources = (
        _purchase_source_days(linked_ledger_id, scenario_id)
        + _residue_source_days(linked_ledger_id, scenario_id)
    )
    sources.sort(key=lambda source: source.settled_on)
    return sources


def walk_account_ledger(
    account_id: int, scenario_id: int,
) -> list[AccountAnchorCorrection]:
    """Replay a non-loan account's anchors into its genesis corrections.

    The single DAY-granular walk the account anchor ledger derives from.
    Seeds the running ledger total at zero and, per anchor fact in
    assertion order, absorbs every source that assertion CLEARED -- opening and
    true-up alike, one rule (finding N-133 / F1) -- records the correction with
    the total JUST BEFORE the assertion (``ledger_before``), then resets the
    running total to the asserted balance -- so the corrections' cumulative
    effect plus the cleared sources equals each asserted balance as of the day
    it is the closing balance for, and the account's ABSOLUTE ledger total
    equals the latest anchor plus the later source nets.

    **The partition is the READ FOLD's, and it is the same rule rather than
    a copy** (ruling R-DH, re-pointed at ruling R-FL).  Both ask
    :class:`~app.services.cash_ledger.StatementCoverage` which assertion cleared
    a source, so the posted ledger and the projection cannot disagree about
    which settles an assertion already covers -- the divergence Phase X exists
    to close.  Both were INSTANT-granular until 2026-07-31, which decided that
    question by click order and cost production ``$4,001.42``
    (``anchor_settle_partition.md``); both then compared two of the app's own
    DATES, which the developer's bank exports falsified on 70% of matched
    movements; both now read the RECORDED fact and fall back to the date rule
    only where none exists.  Moving one without the other is what would break
    the equality plan step X-a established, so they have moved together each
    time.

    Why this reproduces the go-forward chokepoint after the fact: at a
    true-up the fresh delta equals ``asserted - current live linked total``
    (every settled source's day is on or before the assertion's), and
    ``created_at`` and the posted ledger are immutable, so the pure walk
    re-derives the same deltas.  Pre-anchor settles in ANY period are
    absorbed by the opening / true-up deltas (the DAY partition, not a period
    one); later settles ride on top; a source reverted after a true-up nets
    to zero and self-heals to the engine's answer on the next reconcile.

    Reads only (no writes, no commit).  All-scenario anchors, per-scenario
    sources: anchor history is per-account, so the same facts walk in every
    scenario, against that scenario's own posted sources.

    Args:
        account_id: The non-loan account whose ledger to walk.
        scenario_id: The budget scenario whose posted sources to walk
            against.

    Returns:
        One :class:`AccountAnchorCorrection` per anchor fact,
        chronological.  Empty when the account row is missing or carries no
        anchor history (fixture-only states; production accounts always
        have both).

    Raises:
        ValueError: If the account is an amortizing loan.  Loans book their
            anchor corrections through the loan posting package onto their
            per-loan ``equity_opening`` account; walking one here would
            double-book its balance across two correction families (and
            break the loan oracle's bare-``account_id`` ledger helpers).
        PostingError: If the account has anchor history but no linked
            ledger account (a broken chart-of-accounts pairing), or a
            nonzero source net's linkage cannot be resolved (see the source
            loaders).
    """
    account = db.session.query(Account).filter_by(id=account_id).first()
    if account is None:
        return []
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot walk account anchors: account id={account_id} is an "
            f"amortizing loan (loans book their anchor corrections through "
            f"the loan posting package, never the account walk)"
        )
    # BUSINESS-DATE order, guaranteed by the loader rather than restated here
    # (finding N-133 / F6).  It is what ``statement_coverage`` bisects for a
    # source no statement has been recorded as showing, and what makes "the
    # FIRST is the opening" true.  That held for free while ``observed_on`` was
    # DERIVED from ``created_at``; plan step 2 made it user-supplied, and the
    # fix belongs in :func:`~app.services.cash_ledger.cash_anchor_facts` -- one
    # order, stated where the rows are read, rather than a re-sort per consumer.
    facts = cash_anchor_facts(account_id)
    if not facts:
        return []
    linked = _ledger_account_for(account_id)
    sources = _source_net_days(linked.id, scenario_id)

    # Which assertion CLEARED each source -- opening and true-up alike, because
    # an assertion is the CLOSING BALANCE for its day (ruling R-DH (a)) and a
    # line it was recorded as showing is inside it whatever the dates say
    # (ruling R-FL).  ``StatementCoverage`` is the ONE implementation of that
    # rule, and this grouping is deliberately the same shape as the read
    # replay's in :func:`app.services.cash_ledger.walk_cash_ledger`, over the
    # same assertions.  It was a MONOTONIC POINTER over day-sorted sources until
    # plan step X-f3a-1, which was correct only while "cleared by this
    # assertion" was monotone in the day -- a recorded clearing fact is not, and
    # a pointer meeting an out-of-order one halts and silently shorts every
    # later assertion.  Before that it restated the rule as a bare ``<=``, and
    # while the OPENING also carried an exception the two walks had to be
    # hand-mirrored and held in step by convention (finding N-133 / F1); a
    # posting walk that absorbs what the fold rides on top of is the exact drift
    # plan step X-a exists to make impossible.
    coverage = statement_coverage(facts)
    cleared: dict[int, Decimal] = {}
    for source in sources:
        anchor_id = coverage.clearing_anchor_id(source)
        if anchor_id is not None:
            cleared[anchor_id] = cleared.get(anchor_id, _ZERO_MONEY) + source.net

    # The OPENING EQUITY is the first correction and it is not an assertion
    # (plan step X-f3c-2a, ruling R-GX): what the account held before its
    # records begin, read from ``budget.account_openings``.  Booking it here --
    # rather than letting the earliest assertion's delta come out equal to it --
    # is what gives the quantity ONE definition.  It had two: this walk derived
    # it as ``asserted - ledger_before`` while the READ fold derived it as
    # ``asserted - balance_before``, and they agreed to the cent on all seven
    # production accounts only because both ran the same subtraction.  The
    # moment either is restated (finding N-275 plans exactly that, moving
    # account 1 by ``$436.05``) two derivations become two answers.
    opening = account_opening_fact(account_id)
    corrections: list[AccountAnchorCorrection] = [
        AccountAnchorCorrection(
            observed_on=opening.opened_on,
            target_balance=opening.opening_equity,
            ledger_before=_ZERO_MONEY,
            opens_the_books=True,
        )
    ]
    running = opening.opening_equity
    for fact in facts:
        running += cleared.get(fact.anchor_id, _ZERO_MONEY)
        corrections.append(
            AccountAnchorCorrection(
                observed_on=fact.observed_on,
                target_balance=fact.anchor_balance,
                ledger_before=running,
                opens_the_books=False,
            )
        )
        # The correction resets the walked total to the asserted balance
        # (the closing-balance reset, the account analogue of the loan
        # walk's anchor reset).
        running = fact.anchor_balance
    return corrections
