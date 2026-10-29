"""The shared read core both confirmed-ledger statements consume (Step 5).

Two statements read the append-only posting ledger: an income statement
(:mod:`._income_statement`) and a balance sheet (:mod:`._balance_sheet`).  They
share this module so their attribution -- WHICH source date each posting lands
on -- is defined ONCE, which is what makes the statements articulate
automatically (the income net over a window equals the equity delta between the
bounding balance sheets, with no reconciliation code).

**The attribution rule (reader-contract C-3).**  Every posting is attributed by
its SOURCE, in whole: a source's per-ledger-account net lands on the source's
CURRENT settle day -- a movement's stored ``settled_on``, which IS the user's
wall-clock civil day (the decided L9 rule -- tax-year and calendar figures follow
that clock, so an 8:05pm-Eastern Dec-31 settle attributes to Dec 31, not the
Jan 1 it becomes in UTC).  There is no fallback: a movement holding a posted net
and no day is refused rather than dated from its pay period, which is what the
derivation this replaced did:

* movement-linked entries (``transaction_entry_id`` set): by the MOVEMENT's
  own ``transaction_entries.settled_on`` -- a purchase
  (ruling **R-FM**, plan step X-f3b: a purchase whose bank posting day is
  recorded is a cash movement of its own, posted at its own day), a bill's or
  a paycheck's covering movement (``balance:X-bi-3b``), and since
  ``balance:X-bi-6-3`` each side of a settled transfer (the
  ``transfer_movement`` source, ruling **R-BAL45**: two entries per transfer,
  each on its own bank day against the owner's Transfers-in-transit account,
  which nets to zero once both have cleared and never reaches a statement's
  Income / Expense filter).  **This reader had no bucket for it until plan
  step X-bi-3a**, so every posted purchase's legs were dropped from both
  statements -- measured on the developer's 2026-09-06 snapshot at 148
  journal entries and `$6,224.21` of Expense-class net, a third of the
  recorded spending -- and the covering movements X-bi-3a writes for every
  settled bill would have taken the rest with them (finding **BAL-502**);
* sourceless corrections (``loan_opening`` / ``loan_trueup`` / ``loan_payment``
  / ``account_opening`` / ``account_trueup``, no movement link): by the
  stored ``entry_date``.  An anchor correction is a fact dated by the anchor's
  observed civil day; a loan payment's SPLIT (its cash re-classified into
  interest / escrow / refund against the loan) is a derivation of the loan
  walk dated by the payment's visible day, keyed by that day and its period
  with no row link since ruling **R-BAL102** (it linked the loan-side shadow
  before, and was read by that link, at the same day, in the bucket the
  transaction source's legs were read in until plan#1845);
* hard-delete residue (a ``purchase`` / ``transfer_movement`` source whose
  movement link was SET-NULLed): DROPPED, as whole entries -- each sums to
  zero (the reverse-before-delete discipline), so dropping it leaves the trial
  balance closed.  A ``loan_payment`` entry is never residue: it has no link
  to lose.

The movement link is the ONE concrete source FK an entry can carry, so the
buckets are a PARTITION of the live ledger by construction, and the same one
as the write-side walk's: an entry with the link is the movement bucket's, and
one without is a correction's by its kind or residue.  It was three FKs until
plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845), which deleted
the transaction-linked bucket (a row's own leg, legacy since plan step
``balance:X-bi-4a``) and the guards that fenced out the legacy one-entry
``transfer`` pairs; its migration deletes every entry either source posted,
all reversed to zero on their own keys, and both columns (ruling plan#1867).

**One civil date, shared, since ruling R-DH.**  This reader and the write-side
walk both attribute a movement to its stored ``settled_on`` -- the same day,
read off the same column (the ``settled_day`` accessor both read a row's own
leg through went with that bucket at plan#1845).  They were
deliberately different until 2026-07-31: the walk partitioned by UTC INSTANT
against each anchor's ``created_at`` while this side used the display-timezone
civil date for calendar windows, and the paragraph here justified an
independent restatement of the attribution rule on exactly that difference.
The difference is gone (the instant partition cost production ``$4,001.42``),
so what survives is a weaker and narrower reason: this package restates the
write side's LOADERS rather than importing a write-package internal, keeping
the reader decoupled from the writer the way the reconciliation oracles rely
on.  **That is a stance about package boundaries, not about the rule** -- the
DAY itself is one stored column on both sides, and plan step 3's one-predicate
sweep is where the two loaders stop being two.

**Flask-isolated** and read-only: plain ids in, plain data out; no writes.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction_entry import TransactionEntry
from app.services.posting_reads import PostingError

from ._types import StatementLine, StatementSection

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class StatementClassIds:
    """The accounting-class ref ids the statements section and sign by.

    Resolved once per statement call (:func:`statement_class_ids`) so the
    readers compare a ledger account's ``class_id`` against integer ids -- the
    IDs-for-logic invariant -- rather than reading the string class name.  Used
    to place each account in its section, to derive the Income + Expense set
    the retained-earnings line closes, and to derive the Unrealized set the
    accumulated-unrealized line closes.

    Attributes:
        asset: The Asset class ref id.
        liability: The Liability class ref id.
        income: The Income class ref id.
        expense: The Expense class ref id.
        equity: The Equity class ref id.
        unrealized: The Unrealized (other comprehensive income) class ref id
            (ruling **R-FO**).  Its accounts appear BELOW the net-income line
            on the income statement and are folded into one derived Equity line
            on the balance sheet, so a price movement nobody sold into cash is
            never counted as earnings and the trial balance still closes.
    """

    asset: int
    liability: int
    income: int
    expense: int
    equity: int
    unrealized: int


def statement_class_ids() -> StatementClassIds:
    """Return the accounting-class ref ids as a :class:`StatementClassIds`.

    A one-shot resolve of the cached class ids the statements branch on, so
    each reader resolves them once (not per account) and compares by id.

    Returns:
        The populated :class:`StatementClassIds`.

    Raises:
        RuntimeError: If the ref cache is not initialized.
    """
    return StatementClassIds(
        asset=ref_cache.ledger_account_class_id(LedgerAccountClassEnum.ASSET),
        liability=ref_cache.ledger_account_class_id(
            LedgerAccountClassEnum.LIABILITY,
        ),
        income=ref_cache.ledger_account_class_id(LedgerAccountClassEnum.INCOME),
        expense=ref_cache.ledger_account_class_id(
            LedgerAccountClassEnum.EXPENSE,
        ),
        equity=ref_cache.ledger_account_class_id(LedgerAccountClassEnum.EQUITY),
        unrealized=ref_cache.ledger_account_class_id(
            LedgerAccountClassEnum.UNREALIZED,
        ),
    )


def present_natural(class_id: int, debit_net: Decimal) -> Decimal:
    """Return a debit-positive posting net as its natural-balance value (C-4).

    The reader-contract C-4 presentation rule: a debit-normal class (Asset,
    Expense) presents its debit-positive net as-is; a credit-normal class
    (Liability, Income, Equity, Unrealized) presents the NEGATED net, so a
    revenue, liability, equity or unrealized-change line reads positive when the
    account holds its natural balance -- and an unrealized LOSS therefore reads
    negative, which is the honest rendering of that account's contra position.
    The ledger is presented FAITHFULLY -- there is no ``-abs``
    normalization, so an owed-as-negative non-loan liability renders as a
    positive Liabilities line while a positively-anchored one would render
    negative (the stated non-loan liability sign rule).

    ``_ZERO_MONEY - debit_net`` (not ``-debit_net``) so a zero net presents as
    ``0.00``, never ``-0.00``.

    Args:
        class_id: The ledger account's ``ref.ledger_account_classes`` id.
        debit_net: The account's debit-positive posting net.

    Returns:
        The natural-balance value as a ``Decimal``.

    Raises:
        RuntimeError: If the ref cache is not initialized.
        KeyError: If *class_id* is not a known ledger-account-class id.
    """
    if ref_cache.ledger_class_is_debit_normal(class_id):
        return debit_net
    return _ZERO_MONEY - debit_net


def load_chart(user_id: int) -> dict[int, LedgerAccount]:
    """Return a user's chart of accounts keyed by ledger account id.

    Every :class:`~app.models.ledger_account.LedgerAccount` the user owns, so a
    reader resolves each net's class / kind / label without a per-account query.
    ``account`` is eager on the model (the linked-row display reads it);
    ``category`` is joined here so a live category row's ``display_name``
    resolves without a lazy load per line.

    Args:
        user_id: The owner whose chart to load.

    Returns:
        ``{ledger_account_id: LedgerAccount}`` over the user's whole chart.
    """
    rows = (
        db.session.query(LedgerAccount)
        .options(joinedload(LedgerAccount.category))
        .filter(LedgerAccount.user_id == user_id)
        .all()
    )
    return {row.id: row for row in rows}


def ledger_account_label(ledger_account: LedgerAccount) -> str:
    """Return a chart account's display label, branching on ``kind_id``.

    The reader-contract display rule, resolved by KIND (never by which FK is
    NULL): a linked row reads the live ``account.name``; a category row reads
    the live ``category.display_name`` (so a rename reflects) while its category
    exists, falling back to its own ``name`` snapshot once the category is
    deleted (``category_id`` SET NULL leaves ``kind_id`` unchanged, so a
    deleted-category row is a category-kind row with no live category); every
    other kind (fallback, per-loan interest / escrow / refund / opening, and
    the three per-account counter kinds -- anchor-equity, interest-income,
    unrealized-change) reads its ``name`` snapshot.  The snapshot -- not
    ``account.name`` -- is used for a per-account counter row even though it
    carries an ``account_id``, because the COALESCE display rule is the
    linked-row rule only.  That is why the fall-through is the DEFAULT arm
    rather than an enumeration: ruling R-FO's two new kinds needed no edit
    here, and a future counter kind will need none either.

    Args:
        ledger_account: The chart row to label (its ``account`` / ``category``
            relationships are loaded by :func:`load_chart`).

    Returns:
        The display label string.

    Raises:
        RuntimeError: If the ref cache is not initialized.
    """
    kind_id = ledger_account.kind_id
    if kind_id == ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED):
        return ledger_account.account.name
    if kind_id == ref_cache.ledger_account_kind_id(
        LedgerAccountKindEnum.CATEGORY,
    ):
        if ledger_account.category is not None:
            return ledger_account.category.display_name
        return ledger_account.name
    return ledger_account.name


def section_lines(
    nets: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_id: int,
) -> list[StatementLine]:
    """Return the natural-signed, label-sorted lines for one accounting class.

    The presentation step both statements share (extracted so the income
    statement's sections and the balance sheet's sections cannot drift): for
    every account of *class_id* with a nonzero net, one :class:`StatementLine`
    labeled by :func:`ledger_account_label` and signed by
    :func:`present_natural`, sorted by label.  Zero-net accounts are dropped
    (an account with no position in the window), so no zero line is emitted.

    Args:
        nets: ``{ledger_account_id: debit_net}`` (any classes; this filters to
            *class_id*).
        chart: The user's chart (:func:`load_chart`), supplying each account's
            class and label.
        class_id: The accounting class to build lines for.

    Returns:
        The class's :class:`StatementLine` list, sorted by label.
    """
    lines = [
        StatementLine(
            label=ledger_account_label(chart[ledger_account_id]),
            amount=present_natural(class_id, debit_net),
            ledger_account_id=ledger_account_id,
        )
        for ledger_account_id, debit_net in nets.items()
        if chart[ledger_account_id].class_id == class_id and debit_net != 0
    ]
    lines.sort(key=lambda line: line.label)
    return lines


def build_section(
    nets: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_id: int,
) -> StatementSection:
    """Return one accounting class's lines and total as a :class:`StatementSection`.

    The section builder both statements share: the class's :func:`section_lines`
    paired with their summed total, so a section's lines and total are derived
    together, from the same set, and cannot drift.  The balance sheet's Equity
    section is instead built directly (it appends the derived retained-earnings
    line before totaling), so this covers Assets / Liabilities / Income /
    Expense.

    Args:
        nets: ``{ledger_account_id: debit_net}`` (any classes; filtered to
            *class_id*).
        chart: The user's chart (:func:`load_chart`).
        class_id: The accounting class to build the section for.

    Returns:
        The :class:`StatementSection` (label-sorted lines + total).
    """
    lines = section_lines(nets, chart, class_id)
    return StatementSection(
        lines=lines,
        total=sum((line.amount for line in lines), _ZERO_MONEY),
    )


def dated_account_nets(
    user_id: int, scenario_id: int,
) -> dict[tuple[int, date], Decimal]:
    """Return every posting's net keyed by (ledger account, attribution date).

    The shared attribution core: the union of the two source buckets
    (movement-linked and sourceless corrections; residue dropped), each
    posting's net placed on its source's attribution date per
    the module docstring's C-3 rule.  Sources landing on
    the same (ledger account, date) are summed, so a caller folds the map by a
    date bound (as-of for the balance sheet, a calendar window for the income
    statement) with no per-account query.  Every entry's legs share one
    attribution date (all of a
    source's legs attribute together), so folding by date always includes whole
    entries -- the reader-contract C-1 guarantee that keeps the trial balance
    closed over any window.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).

    Returns:
        ``{(ledger_account_id, attribution_date): net Decimal}``; empty when the
        scenario has no posted sources.  Nets are the signed, debit-positive
        posting sums.

    Raises:
        PostingError: If a movement-linked source with a nonzero net cannot
            resolve its date (a broken SET-NULL linkage that must fail loudly
            rather than mis-attribute real money).
    """
    contributions = (
        _purchase_dated_nets(user_id, scenario_id)
        + _correction_dated_nets(user_id, scenario_id)
    )
    nets: dict[tuple[int, date], Decimal] = defaultdict(lambda: _ZERO_MONEY)
    for ledger_account_id, attribution_date, net in contributions:
        nets[(ledger_account_id, attribution_date)] += net
    return dict(nets)


def _purchase_dated_nets(
    user_id: int, scenario_id: int,
) -> list[tuple[int, date, Decimal]]:
    """Return movement-linked nets, each dated by the movement's own posting day.

    The movement-linked bucket (module docstring; the reader's twin of the
    walk's ``_purchase_source_days``): entries carrying
    ``transaction_entry_id`` -- the ``purchase`` source (a purchase, a bill's
    or a paycheck's covering movement) and the ``transfer_movement`` source
    (each side of a settled transfer, plan step ``balance:X-bi-6-3``) --
    grouped by (ledger account, movement) and attributed to the movement's
    stored ``settled_on``.  Zero-net groups are dropped (a reverted or
    reversed-before-delete movement nets to zero and needs no date), so
    dates are resolved only for live sources; the ``(user_id, scenario_id)``
    filter uses ``idx_journal_entries_user_scenario_period``.  A movement's
    legs are NOT grouped under its PARENT, for the reason the walk states:
    that would date them at the parent's settle day, which a still-projected
    envelope does not have, and the day money left the bank is its own fact.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(ledger_account_id, attribution_date, net), ...]``; empty when no
        movement-linked source is posted.

    Raises:
        PostingError: If a nonzero net's ``transaction_entry_id`` resolves no
            movement, or one carrying no ``settled_on`` (a leg posted for a
            movement never seen to move) -- either must fail loudly rather
            than mis-attribute money.
    """
    rows = (
        db.session.query(
            Posting.ledger_account_id,
            JournalEntry.transaction_entry_id,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.transaction_entry_id.isnot(None),
        )
        .group_by(Posting.ledger_account_id, JournalEntry.transaction_entry_id)
        .all()
    )
    nets = [
        (ledger_account_id, entry_id, net)
        for ledger_account_id, entry_id, net in rows
        if net != 0
    ]
    if not nets:
        return []
    dates = _purchase_attribution_dates({entry_id for _, entry_id, _ in nets})
    return [
        (ledger_account_id, dates[entry_id], net)
        for ledger_account_id, entry_id, net in nets
    ]


def _purchase_attribution_dates(entry_ids: set[int]) -> dict[int, date]:
    """Return each purchase's stored posting day, keyed by id.

    One batched load of ``(id, settled_on)``.  No shared ``settled_day``
    accessor here, for the reason the walk gives: that one refuses a settled
    TRANSACTION with no day, while a purchase with no day is an ordinary
    outstanding purchase that simply posts nothing -- so a nonzero net against
    a dayless purchase is the broken state, refused below with the missing-
    linkage case.

    Args:
        entry_ids: The purchase ids whose posting days to resolve.

    Returns:
        ``{transaction_entry_id: attribution_date}`` over every id.

    Raises:
        PostingError: If any id resolves no purchase row, or one with no
            ``settled_on``.
    """
    rows = (
        db.session.query(TransactionEntry.id, TransactionEntry.settled_on)
        .filter(TransactionEntry.id.in_(entry_ids))
        .all()
    )
    dates = dict(rows)
    missing = entry_ids - set(dates)
    if missing:
        raise PostingError(
            f"Ledger holds a nonzero net for purchase ids {sorted(missing)} "
            f"but no such purchase rows exist; the reverse-before-delete "
            f"discipline was violated."
        )
    dayless = sorted(entry_id for entry_id, day in dates.items() if day is None)
    if dayless:
        raise PostingError(
            f"Ledger holds a nonzero net for purchase ids {dayless} that "
            f"carry no settled_on; a leg was posted for a purchase never seen "
            f"to move."
        )
    return dates


def _correction_dated_nets(
    user_id: int, scenario_id: int,
) -> list[tuple[int, date, Decimal]]:
    """Return sourceless-correction nets, each dated by the entry's ``entry_date``.

    The correction bucket: entries with no movement link and a
    correction source kind (``loan_opening`` / ``loan_trueup`` /
    ``loan_payment`` / ``account_opening`` / ``account_trueup``), grouped by
    (ledger account, ``entry_date``).  A correction is a DERIVATION with no
    row of its own -- an anchor's fact dated by the anchor's civil day, or a
    loan payment's split dated by the payment's visible day (ruling
    **R-BAL102**, plan step ``balance:X-bi-6-3``; the day it was dated by
    while it linked the loan-side shadow) -- so it is dated by its stored
    civil ``entry_date``.  The ``source_kind_id`` filter is what DROPS
    hard-delete residue: a residue entry has no movement link either, but
    carries a purchase / transfer_movement source kind, so it falls outside
    this bucket and is excluded whole.  Dropping residue is
    REQUIRED, not merely tidy: residue nets to zero per account
    (reverse-before-delete), but its original and reversal entries carry
    DIFFERENT ``entry_date`` s, so attributing them would land them on
    different calendar days and a windowed reader could see one without the
    other -- dropping the whole (zero-sum) pair keeps every window's tie-out
    closed.

    **Positive allowlist, updated in lock-step.**  This bucket KEEPS only the
    enumerated sourceless correction kinds; every other sourceless entry is
    treated as residue and dropped.  So a FUTURE sourceless correction kind must
    be added here or its (real, non-zero) legs are silently dropped -- an
    understated balance, not a broken tie-out (a dropped whole entry still sums
    to zero).  A future ROW-LINKED kind needs no change here (its residue is
    correctly dropped).  The write-side walk's residue loader uses the inverse
    (negative) list because it is per-non-loan-account and never sees loan
    corrections; this reader spans the whole ledger, so it lists all five.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(ledger_account_id, entry_date, net), ...]`` for every nonzero group;
        empty when no correction is posted.
    """
    correction_source_ids = [
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP),
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
    ]
    rows = (
        db.session.query(
            Posting.ledger_account_id,
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.transaction_entry_id.is_(None),
            JournalEntry.source_kind_id.in_(correction_source_ids),
        )
        .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
        .all()
    )
    return [
        (ledger_account_id, entry_date, net)
        for ledger_account_id, entry_date, net in rows
        if net != 0
    ]
