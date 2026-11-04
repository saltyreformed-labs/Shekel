"""The per-LOAN chart rows: interest, escrow, refund, and the opening equity.

Build-Order Step 4's half of the chart of accounts, plus the ``equity_opening``
row the loan read switch added.  A confirmed loan payment's real-split
correction books its accrued interest, its configured escrow and any payoff
overpayment into three per-loan accounts, and the once-per-loan opening entry
books the origination balance into a fourth.  All four carry
``loan_account_id`` and are keyed ``(user, loan, kind)`` by
``uq_ledger_accounts_loan``.  Each kind's class and label are the worked-out
chart's (:func:`app.services.ledger_lines.loan_key`,
:func:`~app.services.ledger_lines.chart_label`; plan task plan#2523).

Flask-isolated and commit-free: plain data in, ORM objects out; the caller owns
the transaction boundary.
"""

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.account import Account
from app.models.ledger_account import LedgerAccount
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.ledger_lines import chart_label, loan_key

from ._common import ChartRowLink, get_or_create_chart_row, load_owned_account

def _load_amortizing_loan_account(user_id: int, loan_account_id: int) -> Account:
    """Load and validate the loan account a per-loan ledger row will link.

    Resolves the ``budget.accounts`` row through the shared tenancy-filtered
    loader (:func:`._common.load_owned_account`), then guards that the account
    is an amortizing loan (``classify_account == AMORTIZING``, which reads the
    ``has_amortization`` boolean -- never a type name string).  This is the
    load-bearing companion to the kind guard in
    :func:`get_or_create_loan_ledger_account`: ``ck_ledger_accounts_loan_shape``
    polices only a per-loan row's column shape, so nothing at the storage tier
    stops a ``loan_account_id`` pointing at a Checking or Credit Card account.
    The resolver is the sole writer and therefore the only guarantee that a
    per-loan ledger row links a real loan.

    Args:
        user_id: The owning user's id (the loan must belong to them).
        loan_account_id: The loan ``budget.accounts`` id (non-NULL).

    Returns:
        The validated :class:`~app.models.account.Account` (an amortizing
        loan owned by ``user_id``), with ``account_type`` eager-loaded.

    Raises:
        ValueError: If no account with that id is owned by ``user_id`` (from
            the shared loader), or if the account is not an amortizing loan.
            A live caller (the Step-4 poster) only ever resolves a settled
            loan payment's loan account, so a miss or a non-loan account
            signals a caller bug; fail loud with the offending id and the
            account's actual projection kind.
    """
    loan = load_owned_account(user_id, loan_account_id, "a loan")
    projection_kind = classify_account(loan)
    if projection_kind is not AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot create a loan ledger account: account id={loan_account_id} "
            f"is not an amortizing loan (classifies as {projection_kind.value!r})"
        )
    return loan


def get_or_create_loan_ledger_account(
    user_id: int,
    loan_account_id: int,
    kind: LedgerAccountKindEnum,
) -> LedgerAccount:
    """Ensure a loan's per-kind interest / escrow / refund / opening account exists.

    The Build-Order Step 4 chart resolver: a confirmed loan payment's real-split
    correction books its accrued interest into the loan's ``loan_interest``
    Expense account, its configured escrow into the ``loan_escrow`` Expense
    account, and any payoff overpayment into the ``loan_refund`` Asset account;
    the loan read switch (Step 4, second half) additionally books the loan's
    origination balance into its ``equity_opening`` Equity account (the credit
    counter-leg of the once-per-loan opening entry).  This lazily materialises
    (and thereafter reuses) the requested one.

    The accounting class is derived from ``kind`` (``loan_interest`` /
    ``loan_escrow`` -> Expense; ``loan_refund`` -> Asset; ``equity_opening`` ->
    Equity) by the worked-out chart (:func:`app.services.ledger_lines.loan_key`)
    -- the caller passes only the kind, so the class can never be set
    inconsistently with it.  ``kind`` MUST be one of
    the four loan kinds; any other (``linked`` / ``category`` / ``fallback`` /
    ``orphan``) is rejected before any write.  That guard, and the
    amortizing-loan guard in :func:`_load_amortizing_loan_account`, are
    load-bearing rather than belt-and-suspenders: the shipped
    ``ck_ledger_accounts_loan_shape`` CHECK is columns-only (it cannot pin
    ``kind_id`` without subquerying ``ref`` or hardcoding its IDs), so this
    resolver is the only thing keeping a per-loan row's kind a loan kind and
    its ``loan_account_id`` a real loan (the same un-CHECKed trust contract
    ``class_id`` already carries).

    Idempotent: an existing row for the ``(user, loan, kind)`` natural key is
    returned unchanged (the ``uq_ledger_accounts_loan`` partial unique would
    otherwise reject a duplicate).  The created row sets ``loan_account_id``,
    leaves ``account_id`` / ``category_id`` NULL and ``is_owner_bucket`` False (the
    per-loan column shape ``ck_ledger_accounts_loan_shape`` requires), and
    snapshots a display ``name`` (``"<loan name> -- Interest|Escrow|Refund|Opening"``,
    the worked-out chart's label) clipped to the column width -- like a
    category row the snapshot is frozen at creation, so renaming the loan does
    not relabel the stored row, where the worked-out label follows the rename
    (ruling plan#2518; and unlike a linked row, a per-loan row has
    ``account_id`` NULL, so ``ck_ledger_accounts_name_present`` requires the
    stored ``name``).

    Flushes so the new row's ``id`` is assigned, but does NOT commit -- the
    caller (the Step-4 ``posting_service``) owns the transaction boundary.

    Args:
        user_id: The owning user's id.
        loan_account_id: The loan ``budget.accounts`` id whose payment split
            this account books.  Must be an amortizing loan owned by
            ``user_id`` (validated when the row is first created).
        kind: The per-loan kind to resolve, a
            :class:`~app.enums.LedgerAccountKindEnum` member that MUST be
            ``LOAN_INTEREST``, ``LOAN_ESCROW``, ``LOAN_REFUND``, or
            ``EQUITY_OPENING``.

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` for the
        ``(user, loan, kind)`` key (existing, or newly created and flushed).

    Raises:
        ValueError: If ``kind`` is not one of the four loan kinds, or (on
            first creation) if ``loan_account_id`` names no amortizing loan
            owned by ``user_id`` (see :func:`_load_amortizing_loan_account`).
            No database CHECK enforces either, so these guards are the sole
            defense against a malformed per-loan chart entry.
    """
    key = loan_key(kind, loan_account_id)
    return get_or_create_chart_row(
        user_id,
        ref_cache.ledger_account_kind_id(key.kind),
        ref_cache.ledger_account_class_id(key.ledger_class),
        ChartRowLink(LedgerAccount.loan_account_id, loan_account_id),
        # Called ONLY when a row is being created, which is what keeps the
        # amortizing-loan guard off the hit path: an existing row resolves
        # without loading (or re-validating) the loan.
        lambda: chart_label(
            key, _load_amortizing_loan_account(user_id, loan_account_id),
        ),
    )


def dispose_loan_chart_rows(loan_account_id: int) -> None:
    """Delete a loan's per-loan chart rows, ahead of the loan's own delete.

    ``fk_ledger_accounts_loan_account_id`` is RESTRICT, so a permanent delete
    of a loan has to clear these first (ruling plan#2784); an account's linked
    and counter rows go by their CASCADE instead.  Each row's legs go by
    ``fk_account_postings_ledger_account_id``'s CASCADE, which PostgreSQL runs
    as the table owner: the running app holds no DELETE on the journal
    (:func:`app.posting_infrastructure.apply_ledger_append_only_privileges`),
    so this deletes the chart rows and never an entry.  Does not commit.

    Args:
        loan_account_id: The loan about to be deleted.
    """
    db.session.query(LedgerAccount).filter_by(
        loan_account_id=loan_account_id,
    ).delete(synchronize_session="fetch")
