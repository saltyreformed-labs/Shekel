"""The LINKED chart row: one Asset/Liability ledger account per real account.

Build-Order Step 2's half of the chart of accounts.  Pairs every
``budget.accounts`` row with exactly one ``linked``-kind ledger account so that
later steps' postings have an account to land in, and re-derives that row's
accounting class when an UNPOSTED account is re-typed across the
Asset/Liability boundary.

The class rule itself is the worked-out chart's
(:func:`app.services.ledger_lines.linked_ledger_class`, plan task plan#2523),
which the account-type boundary guards (:mod:`app.utils.account_validation`)
also apply to a PROPOSED category; this module stamps a row's class from it.

Flask-isolated and commit-free: plain data in, ORM objects out; the caller owns
the transaction boundary.
"""

import logging

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.account import Account
from app.models.ledger_account import LedgerAccount
from app.models.ref import AccountType
from app.services.ledger_lines import linked_key, linked_ledger_class
from app.utils import archive_helpers

logger = logging.getLogger(__name__)


def find_linked_ledger_account(account_id: int) -> LedgerAccount | None:
    """Return an account's ``linked``-kind chart row, or None.

    **THE definition of "which ledger row is this account's own", in one
    place.**  The ``linked`` filter is load-bearing since Build-Order Step 5:
    an account may ALSO carry per-account counter rows on the same
    ``account_id`` (:mod:`._counters`), so an unfiltered lookup could return
    one of those -- skipping the creation of the linked row, re-classing the
    wrong row, or (for a reader) raising ``MultipleResultsFound``.

    Public because the posting ledger's own pairing lookup
    (:func:`app.services.posting_reads._ledger_account_for`) is this query plus
    a fail-loud: a missing pairing is a broken chart invariant THERE, while
    here it is the state the create hook exists to fix and the state a
    re-class must skip.  Three copies of the query lived in two modules until
    plan step X-f3d; the ``duplicate-code`` gate is what surfaced the third.

    Args:
        account_id: The real account whose linked chart row to find.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`, or None
        when the account has no pairing yet.

    Raises:
        MultipleResultsFound: If an account carries more than one ``linked``
            row.  ``uq_ledger_accounts_account_kind`` makes that unreachable,
            so it is a fail-loud trap rather than a case any caller handles.
    """
    return (
        db.session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
        )
        .one_or_none()
    )


def create_ledger_account_for_account(account: Account) -> LedgerAccount:
    """Ensure a real account has its paired Asset/Liability ledger account.

    Idempotent: when a linked ledger account already exists for this
    account it is returned unchanged (the partial unique index
    ``uq_ledger_accounts_account_kind`` permits only one per
    ``(account_id, kind_id)``, so a second insert would raise); otherwise a
    new linked row is created with the derived class, the ``linked`` kind,
    ``name`` left NULL (the display label derives from ``account.name``),
    and the owning ``user_id`` copied from the account.  The lookup filters
    on the ``linked`` kind because Step 5's per-account counter rows share
    the ``account_id`` column -- an unfiltered lookup could return one of
    those and skip creating the linked row.

    Flushes so the new row's ``id`` is assigned, but does NOT commit --
    the caller (``account_service.create_account``) owns the transaction
    boundary.

    Args:
        account: The real :class:`~app.models.account.Account` to pair.
            Must already be flushed (``account.id`` non-NULL) so the FK
            and the idempotency lookup resolve.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`
        (existing or newly created and flushed).
    """
    existing = find_linked_ledger_account(account.id)
    if existing is not None:
        return existing

    ledger_account = LedgerAccount(
        user_id=account.user_id,
        class_id=ref_cache.ledger_account_class_id(
            linked_key(account).ledger_class,
        ),
        kind_id=ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED),
        account_id=account.id,
        name=None,
    )
    db.session.add(ledger_account)
    db.session.flush()
    logger.info(
        "Paired account %s (id=%d, user_id=%d) with ledger account id=%d "
        "(class_id=%d)",
        account.name, account.id, account.user_id,
        ledger_account.id, ledger_account.class_id,
    )
    return ledger_account


def sync_linked_ledger_class(account: Account) -> None:
    """Re-derive an UNPOSTED account's linked-ledger class after a type change.

    The class is snapshotted at pairing time
    (:func:`create_ledger_account_for_account`), so a later account-type
    change that crosses the Asset/Liability boundary leaves the linked row
    mis-classed -- future postings would land in the wrong balance-sheet
    section.  Re-snapshotting is exactly as safe as the original pairing
    while the ledger is empty, so this brings the class back in step for
    the boundary crossings the C6 validation guards ALLOW (an account with
    no postings); crossings on a posted account are refused upstream
    (:mod:`app.utils.account_validation`), because posted legs already
    carry the old class's economic meaning.

    A no-op when the account has no linked row (nothing paired yet) or the
    derived class already matches.  Flushes; does not commit.

    Args:
        account: The re-typed :class:`~app.models.account.Account` (its
            ``account_type`` relationship reflects the NEW type).

    Raises:
        ValueError: If the class would change on an account that HAS
            ledger postings.  The validation guards make that unreachable
            from the routes, so reaching it means a caller skipped the
            guard -- and silently re-classing posted history would
            mis-state the economic meaning of every prior leg, so it
            fails loudly instead.
    """
    linked = find_linked_ledger_account(account.id)
    if linked is None:
        return
    # Resolve the type by the FK COLUMN, not the ``account_type``
    # relationship: mid-update the caller has assigned a new
    # ``account_type_id`` whose relationship attribute is not refreshed
    # until the row expires, and deriving from the stale object would
    # silently skip the re-class.
    account_type = db.session.get(AccountType, account.account_type_id)
    new_class_id = ref_cache.ledger_account_class_id(
        linked_ledger_class(account_type.category_id),
    )
    if linked.class_id == new_class_id:
        return
    if archive_helpers.account_has_ledger_postings(account.id):
        raise ValueError(
            f"cannot re-class linked ledger account {linked.id}: account "
            f"id={account.id} has ledger postings, and the validation "
            f"guards must refuse a class-crossing type change first"
        )
    linked.class_id = new_class_id
    db.session.flush()
    logger.info(
        "Re-classed linked ledger account id=%d (account_id=%d) to "
        "class_id=%d after an account-type change",
        linked.id, account.id, new_class_id,
    )
