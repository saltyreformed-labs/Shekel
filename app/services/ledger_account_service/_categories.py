"""The per-CATEGORY chart rows, and the door to the per-owner Uncategorized fallback.

Build-Order Step 3's half of the chart of accounts: the counter-leg side an
ordinary settled transaction's category leg books into.  One Income or Expense
ledger account per budget category per accounting class, plus the
per-(owner, class) ``Uncategorized`` bucket that catches a transaction whose
``category_id`` is NULL -- which since plan step ``balance:X-bi-6-3`` (ruling
**R-BAL99**) is one of the OWNER-BUCKET family :mod:`._buckets` resolves; this
module's door keeps its signature and delegates the fallback there.

Unlike a linked row a category row carries a ``category_id`` and a NULL
``account_id``, and snapshots a display ``name`` at creation.  The class guard
and the label are the worked-out chart's
(:func:`app.services.ledger_lines.category_key`,
:func:`~app.services.ledger_lines.chart_label`; plan task plan#2523).

Flask-isolated and commit-free: plain data in, ORM objects out; the caller owns
the transaction boundary.
"""

import logging

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.extensions import db
from app.models.category import Category
from app.models.ledger_account import LedgerAccount
from app.services.ledger_lines import ChartKey, category_key, chart_label

from ._buckets import get_or_create_owner_bucket
from ._common import LEDGER_ACCOUNT_NAME_MAX_LEN, resolve_chart_row

logger = logging.getLogger(__name__)


def _find_existing_category_ledger_account(
    user_id: int, class_id: int, category_id: int,
) -> LedgerAccount | None:
    """Return the existing category ledger account, or None.

    The idempotency lookup for
    :func:`get_or_create_category_ledger_account`'s category arm, keyed to
    match ``uq_ledger_accounts_category`` exactly: the **category** row for
    ``(owner, category, class)``, found among ``account_id``-NULL rows.  The
    fallback arm's lookup lives with the owner-bucket family
    (:func:`._buckets._find_existing_owner_bucket`) since plan step
    ``balance:X-bi-6-3``.

    Args:
        user_id: The owning user's id.
        class_id: The Income or Expense ledger-account-class PK.
        category_id: The budget category's id (non-NULL).

    Returns:
        The matching :class:`~app.models.ledger_account.LedgerAccount`, or
        None when none exists yet.
    """
    return (
        db.session.query(LedgerAccount)
        .filter_by(
            user_id=user_id, class_id=class_id,
            category_id=category_id, account_id=None,
        )
        .first()
    )


def _category_display_name(user_id: int, key: ChartKey) -> str:
    """Return a category line's label to snapshot into ``name``.

    The label is the worked-out chart's
    (:func:`app.services.ledger_lines.chart_label`: the category's
    ``"Group: Item"``), loaded fresh rather than navigated through a
    relationship because the snapshot is taken once, at ledger-account
    creation.  Truncated to :data:`._common.LEDGER_ACCOUNT_NAME_MAX_LEN` so a long
    "Group: Item" (up to ~202 chars) always fits the ``name`` column; the label
    is display-only, so the clip is lossless for logic, and the equivalent
    ``LEFT(group || ': ' || item, <len>)`` in the Step-7 backfill yields the
    identical string (the backfill==go-forward invariant).

    Filtered by the owning ``user_id``, not loaded by bare primary key: a
    ``Category`` is user-scoped data, so this honours the project rule that
    every query touching user data filters by ``user_id`` and matches the
    sibling :func:`_find_existing_category_ledger_account` lookup.  A
    ``category_id`` belonging to another user is therefore treated as "not
    found" rather than silently snapshotting a foreign label into this
    owner's ledger account.

    Args:
        user_id: The owning user's id (the category must belong to them).
        key: The category line's key (its ``record_id`` the budget category's
            id, non-NULL).

    Returns:
        str -- the category's ``"Group: Item"`` display label, clipped to the
        ``name`` column width.

    Raises:
        ValueError: If no category with that id is owned by ``user_id``.  A
            live transaction's ``category_id`` always references the owner's
            existing category (the FK SET-NULLs it on delete), so a miss
            signals a caller passing a stale, wrong, or foreign id -- fail
            loud with the offending values rather than raising an opaque
            ``AttributeError`` on ``None.display_name``.
    """
    category = (
        db.session.query(Category)
        .filter_by(id=key.record_id, user_id=user_id)
        .first()
    )
    if category is None:
        raise ValueError(
            f"cannot create a category ledger account: no budget category "
            f"with id={key.record_id} owned by user_id={user_id}"
        )
    return chart_label(key, category)[:LEDGER_ACCOUNT_NAME_MAX_LEN]


def get_or_create_category_ledger_account(
    user_id: int,
    category_id: int | None,
    ledger_class: LedgerAccountClassEnum,
) -> LedgerAccount:
    """Ensure the Income/Expense ledger account for a category exists.

    The Build-Order Step 3 counter-leg resolver: an ordinary settled
    transaction's category leg books into a per-category Income or Expense
    ledger account, and this lazily materialises (and thereafter reuses)
    that account.  A transaction with no category books into the
    per-(owner, class) ``Uncategorized`` fallback instead.

    Idempotent (:func:`._common.resolve_chart_row`): an existing row for the
    natural key is returned unchanged (the matching partial unique index --
    ``uq_ledger_accounts_category`` for a category row,
    ``uq_ledger_accounts_owner_bucket`` for a fallback -- would otherwise
    reject a duplicate).  A category used for both an
    income and an expense transaction correctly yields TWO rows, one per
    class, because the natural key includes ``class_id`` (a ``Category`` is
    type-agnostic).

    The created category row leaves ``account_id`` NULL (it is a counter
    account, not a real-account mirror), snapshots its display ``name`` (the
    category's ``"Group: Item"``) and stamps the ``category`` kind.  The
    FALLBACK arm is the owner-bucket family's since plan step
    ``balance:X-bi-6-3`` (ruling **R-BAL99**): this door keeps its signature
    -- every caller asks it for "the counter account of this row" without
    knowing whether the row has a category -- and delegates a ``None``
    category to :func:`._buckets.get_or_create_owner_bucket`, which stamps
    the ``fallback`` kind, ``is_owner_bucket`` True and the canonical
    ``"Uncategorized {Income|Expense}"`` label.  Neither arm ever creates or
    reuses an **orphan** (``is_owner_bucket`` False, ``category_id`` NULL):
    orphans arise only from a category delete's SET NULL and are left
    untouched (the bucket lookup keys on the flag, never on ``category_id IS
    NULL``).

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (``posting_service``, Step 3 Commit 4) owns the transaction
    boundary.

    Args:
        user_id: The owning user's id.  Sourced by the caller from
            ``txn.user_id``; it was ``txn.pay_period.user_id`` until plan step
            ``pay_calendar:C13-b`` moved it, one hydration cheaper for the
            same value.  A read that STAMPS rather than refuses, so it was
            never one of finding **P75**'s nineteen -- that census excludes it
            by name -- and what moved it is the rule that a row's owner has
            ONE home.
        category_id: The budget category's id, or None to resolve the
            per-(owner, class) Uncategorized fallback.
        ledger_class: The accounting class, a
            :class:`~app.enums.LedgerAccountClassEnum` member that MUST be
            ``INCOME`` or ``EXPENSE`` (the caller derives it from the
            transaction type).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        (user, category, class) key (existing, or newly created and
        flushed).

    Raises:
        ValueError: If ``ledger_class`` is not Income or Expense (from
            :func:`app.services.ledger_lines.category_key`; no database CHECK
            enforces this, so the guard is the sole defense against a
            malformed chart entry), or if a non-NULL ``category_id`` names
            no category owned by ``user_id``.
    """
    key = category_key(category_id, ledger_class)
    if category_id is None:
        return get_or_create_owner_bucket(user_id, key.kind, key.ledger_class)
    class_id = ref_cache.ledger_account_class_id(key.ledger_class)
    ledger_account = resolve_chart_row(
        lambda: _find_existing_category_ledger_account(
            user_id, class_id, category_id,
        ),
        lambda: LedgerAccount(
            user_id=user_id,
            class_id=class_id,
            kind_id=ref_cache.ledger_account_kind_id(key.kind),
            account_id=None,
            category_id=category_id,
            is_owner_bucket=False,
            name=_category_display_name(user_id, key),
        ),
    )
    # "Resolved", not "Created": :func:`._common.resolve_chart_row` returns
    # the row a concurrent request won when this one lost the natural-key
    # race, and that row is not one this call created.  ``add_or_reuse`` logs
    # the reuse itself, so the two lines together read correctly either way.
    logger.info(
        "Resolved category ledger account id=%d (user_id=%d, category_id=%d, "
        "class_id=%d)",
        ledger_account.id, user_id, category_id, class_id,
    )
    return ledger_account
