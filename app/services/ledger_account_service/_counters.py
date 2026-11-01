"""The per-ACCOUNT chart rows that hold an anchor correction's counter leg.

Build-Order Step 5's half of the chart of accounts, extended by ruling
**R-FO** (plan step X-f3d).  A non-loan account's ``account_opening`` /
``account_trueup`` corrections are balanced two-leg entries: one leg moves the
account's own linked row, and the other -- the COUNTER leg -- lands here, which
is what makes every non-loan linked ledger sum to an ABSOLUTE balance and
closes the app-wide trial balance.

**WHICH row it lands in says what the difference WAS**, and that is a total
dispatch over the account's projection kind
(:func:`anchor_correction_counter_kind`):

* ``anchor_equity`` (Equity) -- an account's OPENING, whatever its kind, and
  every ``PLAIN`` account's true-up until plan step X-f3c makes that residual a
  recorded, user-accepted transaction (ruling **R-FN**).
* ``interest_income`` (Income) -- an ``INTEREST`` account's true-up.
* ``unrealized_change`` (``UNREALIZED``) -- an ``INVESTMENT`` or
  ``APPRECIATING`` account's true-up: other comprehensive income, reported
  below the net-income line so a revaluation is never read as earnings.

All three share one column shape (``account_id`` set, ``category_id`` /
``loan_account_id`` NULL, ``is_owner_bucket`` False) and one natural key,
``uq_ledger_accounts_account_kind``'s ``(account_id, kind_id)`` -- so they
coexist with the ``linked`` row and with each other under an index that already
existed, and no new index is needed.  Unlike a linked row each ALWAYS snapshots
a display ``name``: the COALESCE display rule is the LINKED-row rule, so
readers render this row's snapshot.

The loan analogue is :mod:`._loans`' ``equity_opening`` kind; the two families
never overlap, because :func:`_load_non_loan_account` rejects amortizing loans
here and ``_load_amortizing_loan_account`` rejects everything else there.

Flask-isolated and commit-free: plain data in, ORM objects out; the caller owns
the transaction boundary.
"""

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.models.account import Account
from app.models.ledger_account import LedgerAccount
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.ledger_lines import account_counter_key, chart_label

from ._common import ChartRowLink, get_or_create_chart_row, load_owned_account

# WHAT a TRUE-UP's difference MEANS, by the account's projection kind (ruling
# **R-FO**).  Total over the four NON-loan kinds
# ``app.services.account_projection.classify_account`` can return;
# ``AMORTIZING`` is deliberately absent, because a loan's corrections never
# reach this package at all (:func:`_load_non_loan_account` refuses one, and
# ``account_posting_service`` skips loans structurally).  A missing key is a
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
            ``AMORTIZING``, whose corrections belong to the loan posting
            package entirely, and any kind added to the enum without a rule
            here.  Fail loud rather than defaulting: a silent fallback to
            equity is the very defect this dispatch removes.
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
            f"through this package"
        )
    return counter_kind


def _load_non_loan_account(user_id: int, account_id: int) -> Account:
    """Load and validate the NON-loan account a counter row will link.

    The inverse companion of :func:`._loans._load_amortizing_loan_account`:
    resolves the ``budget.accounts`` row through the shared tenancy-filtered
    loader (:func:`._common.load_owned_account`) and guards that the account is
    NOT an amortizing loan.  Loans post their anchor corrections through the
    ``LoanAnchorEvent``-driven loan path onto their per-loan
    ``equity_opening`` account; minting a counter row for a loan would
    double-book its opening across two equity accounts.

    This guard is also what keeps the loan reconciliation oracle's
    bare-``account_id`` ledger helpers honest by construction: the account
    walk can never touch a loan's ``account_id``, so a loan's linked ledger
    never gains a counter row (recorded in the Step-5 plan's C6 checklist).

    Args:
        user_id: The owning user's id (the account must belong to them).
        account_id: The non-loan ``budget.accounts`` id (non-NULL).

    Returns:
        The validated :class:`~app.models.account.Account` (a non-amortizing
        account owned by ``user_id``), with ``account_type`` eager-loaded.

    Raises:
        ValueError: If no account with that id is owned by ``user_id`` (from
            the shared loader), or if the account IS an amortizing loan.  Fail
            loud with the offending id rather than minting a malformed chart
            entry -- no database CHECK pins a counter row's target, so this
            guard is the sole defense (the same trust contract the loan
            resolver carries).
    """
    account = load_owned_account(user_id, account_id, "an anchor-counter")
    projection_kind = classify_account(account)
    if projection_kind is AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot create an anchor-counter ledger account: account "
            f"id={account_id} is an amortizing loan (loans book their "
            f"anchor corrections onto their per-loan equity_opening "
            f"account, never a per-account counter row)"
        )
    return account


def get_or_create_account_counter_account(
    user_id: int, account_id: int, kind: LedgerAccountKindEnum,
) -> LedgerAccount:
    """Ensure a non-loan account's per-kind COUNTER ledger account exists.

    The chart resolver behind a non-loan account's ``account_opening`` /
    ``account_trueup`` corrections: each books its counter leg into the
    per-account row :func:`anchor_correction_counter_kind` names, and this
    lazily materialises (and thereafter reuses) it.  The loan analogue is
    :func:`._loans.get_or_create_loan_ledger_account`; the two never overlap,
    because :func:`_load_non_loan_account` rejects amortizing loans here and
    ``_load_amortizing_loan_account`` rejects everything else there.

    The accounting class is derived from ``kind`` (``anchor_equity`` ->
    Equity; ``interest_income`` -> Income; ``unrealized_change`` ->
    ``UNREALIZED``) by the worked-out chart
    (:func:`app.services.ledger_lines.account_counter_key`), so the caller
    passes only the kind and the class can never be set inconsistently with it.

    Idempotent: an existing row for the ``(account, kind)`` natural key is
    returned unchanged (the ``uq_ledger_accounts_account_kind`` partial unique
    would otherwise reject a duplicate).  The created row sets ``account_id``
    (sharing the column with the account's ``linked`` row and with its other
    counter kinds -- all coexist under that key), leaves ``category_id`` /
    ``loan_account_id`` NULL and ``is_owner_bucket`` False, and ALWAYS snapshots a
    display ``name`` (``"<account name> -- Opening|Interest Income|Change in
    Value"``, the worked-out chart's label, clipped to the column width): the
    COALESCE display rule is the LINKED-row rule, so readers branch on
    ``kind_id`` and render this snapshot (see
    :class:`app.models.ledger_account.LedgerAccount`).  Like every snapshot it
    is frozen at creation, so renaming the account does not relabel the stored
    row; the worked-out label follows the rename (ruling plan#2518).

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (``account_posting_service``) owns the transaction boundary.

    Args:
        user_id: The owning user's id.
        account_id: The non-loan ``budget.accounts`` id whose anchor
            corrections this account books.  Must be a non-amortizing
            account owned by ``user_id`` (validated when the row is first
            created).
        kind: The counter kind to resolve, a
            :class:`~app.enums.LedgerAccountKindEnum` member that MUST be
            ``ANCHOR_EQUITY``, ``INTEREST_INCOME`` or ``UNREALIZED_CHANGE``.

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        ``(account, kind)`` key (existing, or newly created and flushed).

    Raises:
        ValueError: If ``kind`` is not one of the three counter kinds, or (on
            first creation) if ``account_id`` names no account owned by
            ``user_id`` or names an amortizing loan (see
            :func:`_load_non_loan_account`).  No database CHECK enforces any of
            the three, so these guards are the sole defense against a malformed
            chart entry.
    """
    key = account_counter_key(kind, account_id)
    return get_or_create_chart_row(
        user_id,
        ref_cache.ledger_account_kind_id(key.kind),
        ref_cache.ledger_account_class_id(key.ledger_class),
        ChartRowLink(LedgerAccount.account_id, account_id),
        # Called ONLY when a row is being created, which is what keeps the
        # non-loan guard off the hit path: an existing row resolves without
        # loading (or re-validating) the account.
        lambda: chart_label(key, _load_non_loan_account(user_id, account_id)),
    )
