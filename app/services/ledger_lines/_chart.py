"""The chart of accounts, worked out from the records (ruling plan#2518).

Every ledger line is booked on a chart account: a real account's own line, a
budget category in a class, one of an account's correction counters, one of a
loan's four components, or one of the owner's buckets.  Until balance:X-ew that
chart was STORED (``budget.ledger_accounts``): a row minted the first time
something posted to it, its class stamped and its name snapshotted at that
moment.  Ruling plan#2518 (developer, 2026-11-01) works it out instead, so a
line names what it is booked on with a :class:`ChartKey` -- its kind, its class
and the record behind it -- and no row of any table is created to name it.

**This module is the ONE home of the chart's rules**, and the stored chart's
writer reads them from here for as long as the store lives:

* **the class**, applied once, when a key is built: a real account's own line by
  its type's category (:func:`linked_ledger_class`); a category line by its
  source row's type (:func:`ledger_class_of`), the class being part of the
  category line's identity because a category is type-agnostic; and every
  counter, loan and bucket kind by the fixed class its kind carries;
* **the label** (:func:`chart_label`), from the LIVE record: the account's or
  category's name today, the canonical bucket labels, and the
  ``"<account> -- <component>"`` form the stored counter and loan rows
  snapshot.  The ruling's answer is "Statements show current names": a rename
  relabels past lines, where a stored snapshot kept the name it was minted
  with.
* **which counter an account's correction books on** (ruling **R-FO**,
  :func:`anchor_correction_counter_kind`): the opening on equity, a true-up on
  what its difference WAS by the account's projection kind.  It was
  ``ledger_account_service``'s until plan task plan#2526 moved it beside the
  correction rules that read it (:mod:`._account_anchors`).

``ledger_account_service`` stamps each stored row's class and snapshots its
name from these rules, so the row the writer stores and the key worked out here
cannot part.  X-ew's third leaf (plan#2516) deletes that writer and the table;
this module is what is left of the chart.

**A key is built through its constructor, never by hand.**  The constructors
are where the class rule runs and where a kind is refused a class or a record
it cannot carry -- the guards the stored resolvers held, and whose refusals
they raise unchanged.  The one place a key is spelled from columns directly is
the proof's mapping of a STORED row onto its key (plan#2527), which must read
the row as it stands rather than re-derive it.

**No key names an ``orphan``.**  That kind is a stored row's state after its
budget category was deleted (the ``category_id`` SET NULL), and app code never
writes it.  Worked out from the records, the deleted category's rows read
``category_id`` NULL, so their lines book on the owner's Uncategorized bucket
-- which is where today's writer re-books them at the next resync too.

Pure: reads the records it is handed and the cached reference ids, writes
nothing, and queries nothing.
"""

from dataclasses import dataclass

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
)
from app.models.account import Account
from app.models.category import Category
from app.services.account_projection import AccountProjectionKind

# Each per-ACCOUNT counter kind (the counter leg of a non-loan account's
# opening and true-ups, ruling R-FO) mapped to (its class, its label's
# component).  Spelled out rather than derived from the enum values so that
# renaming an identifier never relabels a statement.  ``ANCHOR_EQUITY``'s
# component stays "Opening": it is the label every stored twin carries.
_ACCOUNT_COUNTER_KINDS = {
    LedgerAccountKindEnum.ANCHOR_EQUITY: (
        LedgerAccountClassEnum.EQUITY, "Opening",
    ),
    LedgerAccountKindEnum.INTEREST_INCOME: (
        LedgerAccountClassEnum.INCOME, "Interest Income",
    ),
    LedgerAccountKindEnum.UNREALIZED_CHANGE: (
        LedgerAccountClassEnum.UNREALIZED, "Change in Value",
    ),
}

# Each per-LOAN kind mapped to (its class, its label's component).  A
# confirmed payment's accrued interest and configured escrow are Expense, a
# payoff overpayment is a refund receivable (Asset), and the origination
# balance's counter leg is Equity.  The record behind each is the loan, which
# is itself a ``budget.accounts`` row.
_LOAN_KINDS = {
    LedgerAccountKindEnum.LOAN_INTEREST: (
        LedgerAccountClassEnum.EXPENSE, "Interest",
    ),
    LedgerAccountKindEnum.LOAN_ESCROW: (
        LedgerAccountClassEnum.EXPENSE, "Escrow",
    ),
    LedgerAccountKindEnum.LOAN_REFUND: (
        LedgerAccountClassEnum.ASSET, "Refund",
    ),
    LedgerAccountKindEnum.EQUITY_OPENING: (
        LedgerAccountClassEnum.EQUITY, "Opening",
    ),
}

# Each OWNER BUCKET (ruling R-BAL99: no real account, category or loan behind
# it), keyed by the ``(kind, class)`` pair that IS its identity, mapped to its
# canonical label.  The map is also the only list of buckets there are: a pair
# outside it is refused, not built.
_OWNER_BUCKETS = {
    (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.INCOME):
        "Uncategorized Income",
    (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.EXPENSE):
        "Uncategorized Expense",
    (LedgerAccountKindEnum.TRANSIT, LedgerAccountClassEnum.ASSET):
        "Transfers in transit",
}

# The classes a category line may carry: exactly the classes the Uncategorized
# bucket is defined for, derived from the bucket map so the two cannot drift.
_CATEGORY_CLASSES = frozenset(
    ledger_class
    for kind, ledger_class in _OWNER_BUCKETS
    if kind is LedgerAccountKindEnum.FALLBACK
)

# The two families of kinds whose record is an ACCOUNT and that are not its own
# line: an account's correction counters and a loan's components.  Public
# because the stored chart's writer files the two under different columns
# (``account_id`` against ``loan_account_id``) and resolves each family's row
# through its own resolver; derived from the tables above so the sets and the
# classes cannot part.
ACCOUNT_COUNTER_KINDS = frozenset(_ACCOUNT_COUNTER_KINDS)
LOAN_KINDS = frozenset(_LOAN_KINDS)

# WHAT a TRUE-UP's difference MEANS, by the account's projection kind (ruling
# **R-FO**).  Total over the four NON-loan kinds
# ``app.services.account_projection.classify_account`` can return;
# ``AMORTIZING`` is deliberately absent, because a loan's corrections are the
# loan rules' (:mod:`._loan_anchors`), and the account walk refuses a loan
# (:func:`._account_walk.walk_account_ledger`).  A missing key is a
# loud refusal rather than a default, so ADDING a projection kind fails here
# instead of quietly booking the new kind's return to equity --
# ``TestTrueUpCounterKindIsTotal`` is what turns that into a test failure
# rather than a production surprise.
#
# ``PLAIN`` stays on equity on purpose and it is the only arm that is not
# final: classifying an ordinary cash account's unexplained difference
# automatically is exactly what ruling **R-FN** refuses, so it flips at plan
# step X-f3c, when that difference becomes a transaction the user accepts.
_TRUEUP_COUNTER_KINDS = {
    AccountProjectionKind.PLAIN: LedgerAccountKindEnum.ANCHOR_EQUITY,
    AccountProjectionKind.INTEREST: LedgerAccountKindEnum.INTEREST_INCOME,
    AccountProjectionKind.INVESTMENT: LedgerAccountKindEnum.UNREALIZED_CHANGE,
    AccountProjectionKind.APPRECIATING: LedgerAccountKindEnum.UNREALIZED_CHANGE,
}

# The label component of every kind whose label is "<account> -- <component>".
_COMPONENTS = {
    kind: component
    for kind, (_, component) in (
        *_ACCOUNT_COUNTER_KINDS.items(), *_LOAN_KINDS.items(),
    )
}


@dataclass(frozen=True)
class ChartKey:
    """What a ledger line is booked on, named without any stored chart row.

    Equal keys are one chart account, so a statement nets lines by key.  Build
    one through its constructor (:func:`linked_key`, :func:`category_key`,
    :func:`transit_key`, :func:`owner_bucket_key`,
    :func:`account_counter_key`, :func:`loan_key`), which is where its class
    is worked out and where an impossible kind and class pair is refused.

    Attributes:
        kind: The :class:`~app.enums.LedgerAccountKindEnum` member.
        ledger_class: The :class:`~app.enums.LedgerAccountClassEnum` member
            the class rule gives the kind and its record.
        record_id: The id of the record the line is booked on: the
            ``budget.accounts`` row for the linked kind, the three counter
            kinds and the four loan kinds (a loan is an account); the
            ``budget.categories`` row for the category kind; ``None`` for an
            owner bucket, which has no record behind it.
    """

    kind: LedgerAccountKindEnum
    ledger_class: LedgerAccountClassEnum
    record_id: int | None


def linked_ledger_class(category_id: int) -> LedgerAccountClassEnum:
    """Return the class a real account's own line carries, by its type's category.

    The Liability category (credit cards, loans) maps to the **Liability**
    class; every other category (Asset, Retirement, Investment) to the
    **Asset** class -- a retirement or brokerage balance is an asset on the
    books, only borrowed money is a liability.  It compares the category
    INTEGER id against the cached Liability id, never a ``name`` string.

    It was ``ledger_account_service.ledger_class_id_for_category`` until plan
    task plan#2523 moved it here, returning the member rather than its id.
    Public because the account-type guards
    (:mod:`app.utils.account_validation`) apply the SAME rule to a PROPOSED
    category, to decide whether a type change would flip an account's class.
    The Step-2 backfill migration spelled this mapping in SQL.  The question
    it asks is also asked by
    :func:`app.services.account_category.is_liability_category`, which is
    finding N-122 (plan#1333), owned by plan step X-ab (plan#1047).

    Args:
        category_id: The ``ref.account_categories`` id of an account type's
            category (current or proposed).

    Returns:
        :attr:`LedgerAccountClassEnum.LIABILITY` or ``.ASSET``.
    """
    if category_id == ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY):
        return LedgerAccountClassEnum.LIABILITY
    return LedgerAccountClassEnum.ASSET


def ledger_class_of(txn) -> LedgerAccountClassEnum:
    """Return the class a row's category line books in, by the row's TYPE.

    An income row's category line is Income class and an expense row's is
    Expense class.  A movement's line is its PARENT's (ruling R-BAL35), so the
    caller passes the plan row.  ``transaction_type_id`` is immutable, so the
    class is stable across the row's life.  It lived in
    ``app.services._posting_write`` until plan task plan#2523 moved it beside
    the rest of the class rule.

    Args:
        txn: The transaction, income or expense.

    Returns:
        :attr:`LedgerAccountClassEnum.INCOME` or ``.EXPENSE``.
    """
    return (
        LedgerAccountClassEnum.INCOME if txn.is_income
        else LedgerAccountClassEnum.EXPENSE
    )


def linked_key(account: Account) -> ChartKey:
    """Return the key of a real account's own line.

    Args:
        account: The account, its ``account_type`` loaded (the relationship
            is ``lazy="joined"``).

    Returns:
        The ``linked`` key, classed by :func:`linked_ledger_class`.
    """
    return ChartKey(
        LedgerAccountKindEnum.LINKED,
        linked_ledger_class(account.account_type.category_id),
        account.id,
    )


def owner_bucket_key(
    kind: LedgerAccountKindEnum, ledger_class: LedgerAccountClassEnum,
) -> ChartKey:
    """Return the key of the owner's ``(kind, class)`` bucket.

    Args:
        kind: The bucket kind, ``FALLBACK`` or ``TRANSIT``.
        ledger_class: The class, which with *kind* must name a bucket.

    Returns:
        The bucket's key, with no record behind it.

    Raises:
        ValueError: If ``(kind, ledger_class)`` is no bucket the chart names
            (an Asset-class fallback, a Liability-class transit).
    """
    if (kind, ledger_class) not in _OWNER_BUCKETS:
        raise ValueError(
            f"no owner bucket is defined for kind {kind!r} in class "
            f"{ledger_class!r}; the defined buckets are "
            f"{sorted((k.value, c.value) for k, c in _OWNER_BUCKETS)}"
        )
    return ChartKey(kind, ledger_class, None)


def category_key(
    category_id: int | None, ledger_class: LedgerAccountClassEnum,
) -> ChartKey:
    """Return the key of a category line, or the Uncategorized bucket's.

    The key a plan row's category line books on: its category in the class
    its type gives (:func:`ledger_class_of`), or, for a row with no category,
    the owner's Uncategorized bucket in that class.  A category used for both
    income and expense is two keys, one per class.

    Args:
        category_id: The budget category's id, or ``None``.
        ledger_class: ``INCOME`` or ``EXPENSE``.

    Returns:
        The ``category`` key, or the ``fallback`` bucket's for no category.

    Raises:
        ValueError: If *ledger_class* is not Income or Expense.
    """
    if ledger_class not in _CATEGORY_CLASSES:
        raise ValueError(
            f"category ledger account must be Income or Expense class, "
            f"got {ledger_class!r}"
        )
    if category_id is None:
        return owner_bucket_key(LedgerAccountKindEnum.FALLBACK, ledger_class)
    return ChartKey(LedgerAccountKindEnum.CATEGORY, ledger_class, category_id)


def transit_key() -> ChartKey:
    """Return the key of the owner's Transfers in transit account.

    The counter line of each side of a settled transfer, posted on that
    side's own bank day (rulings R-BAL45, R-BAL101), so it nets to zero once
    both sides have cleared.

    Returns:
        The ``transit`` bucket's key, Asset class.
    """
    return owner_bucket_key(
        LedgerAccountKindEnum.TRANSIT, LedgerAccountClassEnum.ASSET,
    )


def account_counter_key(
    kind: LedgerAccountKindEnum, account_id: int,
) -> ChartKey:
    """Return the key of a non-loan account's correction counter line.

    WHICH counter kind a correction books on is ruling R-FO's dispatch
    (:func:`anchor_correction_counter_kind`); this builds the key once the
    kind is known.

    Args:
        kind: ``ANCHOR_EQUITY``, ``INTEREST_INCOME`` or ``UNREALIZED_CHANGE``.
        account_id: The account whose corrections the line counters.

    Returns:
        The counter key, in the class its kind carries.

    Raises:
        ValueError: If *kind* is not one of the three counter kinds.
    """
    if kind not in _ACCOUNT_COUNTER_KINDS:
        raise ValueError(
            f"account counter ledger account kind must be one of "
            f"{sorted(member.value for member in _ACCOUNT_COUNTER_KINDS)}, "
            f"got {kind!r}"
        )
    ledger_class, _ = _ACCOUNT_COUNTER_KINDS[kind]
    return ChartKey(kind, ledger_class, account_id)


def loan_key(kind: LedgerAccountKindEnum, loan_account_id: int) -> ChartKey:
    """Return the key of one of a loan's four component lines.

    Args:
        kind: ``LOAN_INTEREST``, ``LOAN_ESCROW``, ``LOAN_REFUND`` or
            ``EQUITY_OPENING``.
        loan_account_id: The loan's ``budget.accounts`` id.

    Returns:
        The loan key, in the class its kind carries.

    Raises:
        ValueError: If *kind* is not one of the four loan kinds.
    """
    if kind not in _LOAN_KINDS:
        raise ValueError(
            f"loan ledger account kind must be one of "
            f"{sorted(member.value for member in _LOAN_KINDS)}, "
            f"got {kind!r}"
        )
    ledger_class, _ = _LOAN_KINDS[kind]
    return ChartKey(kind, ledger_class, loan_account_id)


def anchor_correction_counter_kind(
    projection_kind: AccountProjectionKind, *, is_opening: bool,
) -> LedgerAccountKindEnum:
    """Return the chart kind an anchor correction's COUNTER leg books into.

    Ruling **R-FO**'s rule, in one place and as a pure function: what a balance
    assertion's difference MEANS is a property of the ACCOUNT, so the counter
    leg is a total dispatch over
    :func:`app.services.account_projection.classify_account` -- interest income
    for an ``INTEREST`` account, an unrealized change in value for an ``INVESTMENT`` or
    ``APPRECIATING`` one, the equity opening for a ``PLAIN`` one.

    **An OPENING books to equity whatever the account is**, and that is not a
    carve-out in the dispatch -- it is a different question.  An opening is the
    balance brought ONTO the books, capital rather than something earned:
    booking a Property's ``$350,000.00`` opening as a change in value would say
    the house appreciated by its whole value on the day it was recorded, and a
    Roth IRA's ``$22,909.02`` opening would read as a day-one return.  R-FO's
    own measurement is the true-ups alone (``$10,653.91`` on a production clone
    2026-08-13, with every opening excluded), and the developer ruled it
    2026-08-14.

    Args:
        projection_kind: The account's
            :class:`~app.services.account_projection.AccountProjectionKind`.
        is_opening: Whether this correction books the account's EARLIEST
            assertion (``CashAnchorFact.is_opening``) rather than a later
            true-up.

    Returns:
        The :class:`~app.enums.LedgerAccountKindEnum` member the counter leg
        books into.

    Raises:
        ValueError: If *projection_kind* has no true-up rule -- today only
            ``AMORTIZING``, whose corrections are the loan rules' entirely,
            and any kind added to the enum without a rule here.  Fail loud
            rather than defaulting: a silent fallback to equity is the very
            defect this dispatch removes.
    """
    if is_opening:
        return LedgerAccountKindEnum.ANCHOR_EQUITY
    counter_kind = _TRUEUP_COUNTER_KINDS.get(projection_kind)
    if counter_kind is None:
        raise ValueError(
            f"no true-up counter rule for projection kind "
            f"{projection_kind.value!r}; an account's balance-assertion "
            f"difference must name what it WAS, and only the non-loan kinds "
            f"{sorted(kind.value for kind in _TRUEUP_COUNTER_KINDS)} book "
            f"an account's corrections"
        )
    return counter_kind


def chart_label(key: ChartKey, record: Account | Category | None) -> str:
    """Return the label a statement shows for *key*, from its LIVE record.

    A real account's own line reads the account's name; a category line the
    category's ``"Group: Item"``; a bucket its canonical label; a counter or
    loan line ``"<account name> -- <component>"``.  Every name is today's
    (ruling plan#2518): where a stored row snapshotted its name when minted,
    the label here follows a rename.  It is not clipped: the stored ``name``
    column's 100-character limit is the store's, and this label has no column.

    Args:
        key: The line's key.
        record: The record *key* names: the :class:`Account` (the linked,
            counter and loan kinds), the :class:`Category` (the category
            kind), or ``None`` for a bucket.

    Returns:
        The label.

    Raises:
        ValueError: If *record* is not the record *key* names -- the wrong
            type, or another id -- since its name would label the line
            wrongly; or if *key* is of a kind no constructor builds.
    """
    if (key.kind, key.ledger_class) in _OWNER_BUCKETS:
        _require_record(key, record, None)
        return _OWNER_BUCKETS[(key.kind, key.ledger_class)]
    if key.kind is LedgerAccountKindEnum.CATEGORY:
        _require_record(key, record, Category)
        return record.display_name
    if key.kind is LedgerAccountKindEnum.LINKED:
        _require_record(key, record, Account)
        return record.name
    if key.kind in _COMPONENTS:
        _require_record(key, record, Account)
        return f"{record.name} -- {_COMPONENTS[key.kind]}"
    raise ValueError(f"no chart label rule for the key {key!r}")


def _require_record(
    key: ChartKey, record: Account | Category | None, record_type: type | None,
) -> None:
    """Refuse a *record* that is not the one *key* names.

    Args:
        key: The key being labelled.
        record: The record the caller handed in.
        record_type: :class:`Account`, :class:`Category`, or ``None`` for a
            key with no record behind it.

    Raises:
        ValueError: If *record* is of another type, or carries another id.
    """
    if record_type is None:
        named = record is None
        expected = "no record"
    else:
        named = isinstance(record, record_type) and record.id == key.record_id
        expected = f"the {record_type.__name__} id={key.record_id}"
    if not named:
        raise ValueError(
            f"the chart key {key!r} names {expected}, and was handed "
            f"{record!r}"
        )
