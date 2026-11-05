"""Tests for ``ledger_account_service`` (Build-Order Step 2, Commit 2;
the category/fallback resolver added Step 3, Commit 3; the per-loan resolver
added Step 4, Commit 3).

The service is the sole go-forward writer of ``budget.ledger_accounts``.
Three entry points:

  * :func:`create_ledger_account_for_account` (Step 2) pairs every real
    account with exactly one Asset or Liability ledger account when the
    account is created.
  * :func:`get_or_create_category_ledger_account` (Step 3) lazily
    materialises the per-category Income/Expense counter accounts and the
    per-(owner, class) Uncategorized fallback an ordinary settled
    transaction's category leg books into.
  * :func:`get_or_create_loan_ledger_account` (Step 4) lazily materialises
    the per-loan accounts: the ``loan_interest`` and ``loan_escrow`` Expense
    accounts and the ``loan_refund`` Asset account a confirmed loan payment's
    real-split correction books into, plus the ``equity_opening`` Equity
    account the loan read switch books the origination balance into.

Both are idempotent (a repeat call is a no-op).  The Step-2 tests pin:

  * **Shape** -- one paired linked row per account, ``account_id`` set,
    ``name`` NULL (display derives from ``account.name``), ``user_id``
    copied from the account, and the explicit ``linked`` ``kind_id`` the
    writer stamps (no DB CHECK pins the kind to the shape, so the writer is
    the guarantee; the category/fallback tests pin ``category`` / ``fallback``
    likewise).
  * **Class derivation** -- a Liability-category account maps to the
    Liability ledger class; every other category (Asset, Retirement,
    Investment) maps to the Asset ledger class.  The derivation branches
    on the account-type category INTEGER ID, never the string name.
  * **Idempotency** -- a second sync returns the existing row, never a
    duplicate (the partial unique ``uq_ledger_accounts_account_kind`` would
    otherwise raise).

The Step-3 tests pin the resolver's behaviour (the storage-tier
constraints themselves are covered by ``test_models/test_ledger_account``):
correct category-row shape and name snapshot; idempotency; the
mixed-category two-class case; the Uncategorized fallback (creation,
naming, per-(owner, class) singleton, and the H1 property that its lookup
keys on ``is_owner_bucket`` so it never returns a deleted-category orphan); and
the input guards (class must be Income/Expense; a non-NULL category id must
exist).

The Step-4 tests likewise pin the loan resolver's behaviour (the per-loan
shape CHECK and per-(loan, kind) unique are covered by
``test_models/test_ledger_account``): correct per-loan row shape, class by
kind (interest/escrow Expense, refund Asset, equity_opening Equity), and name
snapshot; idempotency; the four kinds coexisting on one loan; independence
across loans; and the two load-bearing guards the columns-only shape CHECK
cannot enforce -- the kind must be a loan kind, and the account must be an
amortizing loan owned by the calling user.
"""
from __future__ import annotations

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.models.category import Category
from app.models.ledger_account import LedgerAccount
from app.services import ledger_account_service, ledger_lines, posting_reads
from app.services.account_projection import AccountProjectionKind
# The shared race handler is PACKAGE-PRIVATE.  Plan step X-f3d split the
# 962-line module into ``ledger_account_service/`` by the kind of chart row
# each resolver writes, and ``_add_or_reuse`` became ``_common.add_or_reuse``:
# one helper the four resolvers consume, not a name on the package's public
# surface.  :class:`TestAddOrReuseUnderALostRace` drives it DIRECTLY (its own
# docstring says why a resolver cannot reach the branch), so this file reaches
# the private module by name rather than asking for a re-export that would
# exist only for a test.
from app.services.ledger_account_service import _common as _chart_common
from app.services.ledger_account_service._buckets import get_or_create_owner_bucket
from tests._test_helpers import (
    create_account_of_type,
    ledger_accounts_for_account,
    linked_ledger_account,
)


def _rows_by_kind(account_id):
    """Return ``{kind_id: LedgerAccount}`` for an account's ledger rows.

    The kind-aware shape lookup (Step 5): a non-loan account created with the
    $100 ledger-suite sentinel carries TWO rows -- the ``linked`` pairing and
    the ``anchor_equity`` twin its opening correction minted -- so a shape
    assertion must say WHICH kinds it expects instead of counting rows.
    """
    return {
        row.kind_id: row
        for row in ledger_accounts_for_account(_db.session, account_id)
    }


class TestSyncHookShape:
    """The hook materialises exactly one correctly-shaped linked row."""

    def test_create_account_pairs_exactly_one_ledger_account(
        self, app, db, seed_user,
    ):
        """A new Checking account gets one Asset ledger account.

        Shape contract: exactly one linked row; ``account_id`` points at
        the new account; ``name`` is NULL (a linked row derives its
        display label from ``account.name``); ``user_id`` is copied from
        the account; the class is Asset (Checking is Asset-category); and
        ``kind_id`` is the ``linked`` kind (the writer stamps the explicit
        discriminator no DB CHECK enforces).
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "New Checking",
            )
            by_kind = _rows_by_kind(account.id)
            # Exactly the linked pairing plus the Step-5 anchor-equity twin
            # its $100 sentinel opening minted.
            assert set(by_kind) == {
                ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED),
                ref_cache.ledger_account_kind_id(
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                ),
            }
            ledger_account = by_kind[
                ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED)
            ]
            assert ledger_account.account_id == account.id
            assert ledger_account.name is None
            assert ledger_account.user_id == account.user_id
            assert ledger_account.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )
            assert ledger_account.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            )

    @pytest.mark.parametrize("type_name,expected_class", [
        ("Checking", LedgerAccountClassEnum.ASSET),
        ("HYSA", LedgerAccountClassEnum.ASSET),
        ("Property", LedgerAccountClassEnum.ASSET),
        ("Mortgage", LedgerAccountClassEnum.LIABILITY),
        ("Auto Loan", LedgerAccountClassEnum.LIABILITY),
        ("Credit Card", LedgerAccountClassEnum.LIABILITY),
        # Retirement and Investment categories are assets on the books --
        # only borrowed money is a liability.
        ("401(k)", LedgerAccountClassEnum.ASSET),
        ("Roth IRA", LedgerAccountClassEnum.ASSET),
        ("Brokerage", LedgerAccountClassEnum.ASSET),
    ])
    def test_class_derivation_by_category(
        self, app, db, seed_user, type_name, expected_class,
    ):
        """Each account type maps to the expected ledger class.

        Liability-category types (Mortgage, Auto Loan, Credit Card) ->
        Liability; every other category (Asset, Retirement, Investment) ->
        Asset.  Proves the category-ID branch end to end across all four
        categories.  The class under test is the LINKED row's; a non-loan
        type also carries the Step-5 anchor-equity twin (always Equity
        class), while an amortizing type never does.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, type_name, f"Test {type_name}",
            )
            linked = linked_ledger_account(_db.session, account.id)
            assert linked.class_id == ref_cache.ledger_account_class_id(
                expected_class,
            )


class TestSyncHookIdempotency:
    """A repeat sync never duplicates; a deleted pairing is restored."""

    def test_hook_idempotent_returns_existing_row(
        self, app, db, seed_user,
    ):
        """A second sync returns the existing row, count stays one.

        ``create_account`` already paired the account once; calling the
        hook again must short-circuit on the existing row (the partial
        unique would otherwise raise) and return the same PK.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Idem Savings",
            )
            first_id = linked_ledger_account(_db.session, account.id).id

            again = ledger_account_service.create_ledger_account_for_account(
                account,
            )
            assert again.id == first_id
            # Still exactly one LINKED row (the anchor-equity twin coexists
            # beside it and is not the hook's concern).
            assert linked_ledger_account(_db.session, account.id).id == first_id
            assert len(ledger_accounts_for_account(
                _db.session, account.id,
            )) == 2

    def test_hook_recreates_after_deletion(self, app, db, seed_user):
        """Deleting the pairing then re-syncing restores exactly one row.

        Proves the hook's create path standalone (independent of
        ``create_account``): with the auto-created ledger account removed,
        the next sync inserts a fresh linked row with the same class.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Re-sync Loan",
            )
            original = ledger_accounts_for_account(_db.session, account.id)[0]
            original_class_id = original.class_id

            # Remove the auto-created pairing via raw SQL so the next sync
            # sees an unpaired account.
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": account.id})
            _db.session.commit()
            assert ledger_accounts_for_account(_db.session, account.id) == []

            ledger_account_service.create_ledger_account_for_account(account)
            rows = ledger_accounts_for_account(_db.session, account.id)
            assert len(rows) == 1
            # Re-derived class matches the original (Mortgage -> Liability).
            assert rows[0].class_id == original_class_id
            assert rows[0].class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )


def _expense_class_id():
    """Resolve the Expense ledger-account-class PK (test convenience)."""
    return ref_cache.ledger_account_class_id(LedgerAccountClassEnum.EXPENSE)


def _asset_class_id():
    """Resolve the Asset ledger-account-class PK (test convenience)."""
    return ref_cache.ledger_account_class_id(LedgerAccountClassEnum.ASSET)


def _equity_class_id():
    """Resolve the Equity ledger-account-class PK (test convenience)."""
    return ref_cache.ledger_account_class_id(LedgerAccountClassEnum.EQUITY)


def _loan_ledger_rows(loan_account_id):
    """Return every per-loan ``LedgerAccount`` linked to *loan_account_id*.

    Filters on ``loan_account_id`` (NOT ``account_id``) so it returns only the
    Step-4 per-loan rows and excludes the loan's own auto-paired *linked* row
    (which carries ``account_id`` = the loan id and ``loan_account_id`` NULL).
    """
    return (
        _db.session.query(LedgerAccount)
        .filter_by(loan_account_id=loan_account_id)
        .all()
    )


class TestCategoryLedgerAccountResolver:
    """``get_or_create_category_ledger_account`` materialises category rows."""

    def test_creates_category_row_with_correct_shape(
        self, app, db, seed_user,
    ):
        """A first call for (category, Expense) creates one correctly-shaped row.

        Shape contract for a category ledger account: ``account_id`` NULL (it
        is a counter account, not a real-account mirror); ``category_id``
        points at the budget category; ``is_owner_bucket`` False; ``class_id`` is
        the Expense class; ``kind_id`` is the ``category`` kind; ``name``
        snapshots the category's display label ("Family: Groceries");
        ``user_id`` is the owner; the row is flushed (``id`` assigned).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
            )

            assert row.id is not None
            assert row.account_id is None
            assert row.category_id == groceries.id
            assert row.is_owner_bucket is False
            assert row.class_id == _expense_class_id()
            assert row.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.CATEGORY,
            )
            assert row.name == groceries.display_name  # "Family: Groceries"
            assert row.user_id == user_id

    def test_idempotent_returns_existing_row(self, app, db, seed_user):
        """A second call for the same key returns the same row, not a duplicate.

        ``uq_ledger_accounts_category`` permits one row per (owner, category,
        class); the resolver short-circuits on the existing row (a second
        insert would raise) and returns the same PK.  The category-row count
        stays exactly one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            first = ledger_account_service.get_or_create_category_ledger_account(
                user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
            )
            second = ledger_account_service.get_or_create_category_ledger_account(
                user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
            )

            assert second.id == first.id
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=groceries.id)
                .count() == 1
            )

    def test_same_category_two_classes_yields_two_rows(
        self, app, db, seed_user,
    ):
        """One category used as both income and expense yields two distinct rows.

        A ``Category`` is type-agnostic and double-entry needs one
        normal-balance side per account, so the same category resolves to an
        Income-class row AND an Expense-class row (the (category, class)
        natural key's edge case).  Both carry the same ``category_id``; their
        ``class_id`` and ``id`` differ.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            income_row = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, LedgerAccountClassEnum.INCOME,
                )
            )
            expense_row = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
                )
            )

            assert income_row.id != expense_row.id
            assert income_row.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.INCOME,
            )
            assert expense_row.class_id == _expense_class_id()
            assert income_row.category_id == groceries.id
            assert expense_row.category_id == groceries.id
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=groceries.id)
                .count() == 2
            )

    def test_category_row_becomes_orphan_on_category_delete(
        self, app, db, seed_user,
    ):
        """A resolver-created category row survives its category's deletion.

        The ``category_id`` FK is SET NULL, so deleting the budget category
        turns the resolver's row into an orphan: ``category_id`` goes NULL,
        ``is_owner_bucket`` stays False, and the ``name`` snapshot persists so the
        row stays identifiable in posted history.  Complements the model-level
        SET-NULL test by proving the RESOLVER's output (not a hand-built row)
        participates correctly.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]
            category_id = groceries.id
            snapshot = groceries.display_name

            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, category_id, LedgerAccountClassEnum.EXPENSE,
            )
            row_id = row.id
            _db.session.commit()

            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": category_id})
            _db.session.commit()
            _db.session.expire_all()

            orphan = _db.session.get(LedgerAccount, row_id)
            assert orphan is not None
            assert orphan.category_id is None
            assert orphan.is_owner_bucket is False
            assert orphan.name == snapshot

    def test_long_category_name_truncated_to_fit_column(
        self, app, db, seed_user,
    ):
        """A display_name wider than the name column is truncated, not rejected.

        ``group_name`` and ``item_name`` are each ``String(100)``, so
        ``display_name`` ("Group: Item") can reach 202 chars -- wider than the
        ``ledger_accounts.name`` ``String(100)`` column, which PostgreSQL
        rejects (not silently truncates) on insert.  The resolver clips the
        snapshot to the column width so the row inserts cleanly; ``name`` is
        display-only (the natural key is the (user, category, class) IDs), so
        the clip is lossless for logic.  Without the fix this resolve raised a
        ``DataError`` and 500'd the settling transaction.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            long_category = Category(
                user_id=user_id,
                group_name="G" * 100,
                item_name="I" * 100,
            )
            _db.session.add(long_category)
            _db.session.flush()
            assert len(long_category.display_name) == 202  # 100 + ": " + 100

            # The resolver flushes internally, so without the [:N] truncation
            # this call itself would raise DataError (name overflows
            # VARCHAR(100)).  Its returning at all proves the clip fired.
            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, long_category.id, LedgerAccountClassEnum.EXPENSE,
            )

            assert len(row.name) == 100
            assert row.name == long_category.display_name[:100]


class TestUncategorizedFallbackResolver:
    """The resolver materialises the per-(owner, class) Uncategorized fallback."""

    @pytest.mark.parametrize("ledger_class,expected_name", [
        (LedgerAccountClassEnum.INCOME, "Uncategorized Income"),
        (LedgerAccountClassEnum.EXPENSE, "Uncategorized Expense"),
    ])
    def test_null_category_creates_fallback(
        self, app, db, seed_user, ledger_class, expected_name,
    ):
        """A NULL category resolves to the fallback with ``is_owner_bucket`` True.

        Shape: ``account_id`` NULL, ``category_id`` NULL, ``is_owner_bucket``
        True, ``class_id`` the requested class, ``kind_id`` the ``fallback``
        kind, ``name`` the canonical "Uncategorized {Income|Expense}" label.
        Parametrized across both classes.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            row = ledger_account_service.get_or_create_category_ledger_account(
                user_id, None, ledger_class,
            )

            assert row.account_id is None
            assert row.category_id is None
            assert row.is_owner_bucket is True
            assert row.class_id == ref_cache.ledger_account_class_id(ledger_class)
            assert row.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.FALLBACK,
            )
            assert row.name == expected_name
            assert row.user_id == user_id

    def test_fallback_is_idempotent_singleton(self, app, db, seed_user):
        """Repeated NULL-category calls return the one fallback per (owner, class).

        ``uq_ledger_accounts_owner_bucket`` (``WHERE is_owner_bucket``) permits
        one fallback per owner per class; the resolver returns the same row on
        re-call, and the Expense-fallback count for the owner stays one.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            first = ledger_account_service.get_or_create_category_ledger_account(
                user_id, None, LedgerAccountClassEnum.EXPENSE,
            )
            second = ledger_account_service.get_or_create_category_ledger_account(
                user_id, None, LedgerAccountClassEnum.EXPENSE,
            )

            assert second.id == first.id
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(
                    user_id=user_id, is_owner_bucket=True,
                    class_id=_expense_class_id(),
                )
                .count() == 1
            )

    def test_fallback_lookup_ignores_preexisting_orphan(
        self, app, db, seed_user,
    ):
        """The fallback lookup never returns a deleted-category orphan (H1).

        An orphan (``is_owner_bucket`` False, ``account_id`` NULL, ``category_id``
        NULL) of the Expense class already exists -- the remnant of a deleted
        category.  A NULL-category resolve must NOT return that orphan (which
        would commingle this transaction's posting with the deleted category's
        history); it must create a fresh ``is_owner_bucket`` True fallback,
        distinct from the orphan.  Service-level proof that the lookup keys on
        ``is_owner_bucket``, not ``category_id IS NULL``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            expense_class_id = _expense_class_id()
            # Seed an orphan directly (the shape a category delete leaves).
            orphan = LedgerAccount(
                user_id=user_id, class_id=expense_class_id,
                kind_id=ref_cache.ledger_account_kind_id(
                    LedgerAccountKindEnum.ORPHAN,
                ),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Family: Groceries",
            )
            _db.session.add(orphan)
            _db.session.commit()
            orphan_id = orphan.id

            fallback = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, None, LedgerAccountClassEnum.EXPENSE,
                )
            )

            assert fallback.id != orphan_id
            assert fallback.is_owner_bucket is True
            assert fallback.name == "Uncategorized Expense"
            # The orphan is untouched; two NULL/NULL Expense rows now coexist.
            assert (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                )
                .filter_by(user_id=user_id, class_id=expense_class_id)
                .count() == 2
            )
            assert _db.session.get(LedgerAccount, orphan_id).is_owner_bucket is False

    def test_fallback_is_per_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """Each owner gets their own Expense fallback (user-scoped singleton).

        The fallback singleton is keyed (user_id, class_id) ``WHERE
        is_owner_bucket``, so two users each have their own.  The resolver's
        lookup filters by ``user_id``, so user B's resolve must NOT return
        user A's fallback -- it creates B's own, distinct row.  (Forgetting
        the ``user_id`` filter would leak A's fallback to B, a cross-tenant
        commingle.)
        """
        with app.app_context():
            user_a = seed_user["user"].id
            user_b = seed_second_user["user"].id

            fallback_a = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_a, None, LedgerAccountClassEnum.EXPENSE,
                )
            )
            fallback_b = (
                ledger_account_service.get_or_create_category_ledger_account(
                    user_b, None, LedgerAccountClassEnum.EXPENSE,
                )
            )

            assert fallback_a.id != fallback_b.id
            assert fallback_a.user_id == user_a
            assert fallback_b.user_id == user_b


class TestTransitLedgerAccountResolver:
    """``get_or_create_transit_ledger_account`` -- the owner's clearing account.

    Ruling **R-BAL99** (plan step ``balance:X-bi-6-3``): the Transfers-in-
    transit account is the second OWNER-BUCKET kind beside the fallbacks, one
    resolver keyed ``(user, class, kind)`` over the ``is_owner_bucket`` flag,
    so a bucket kind needs an enum member, a ref row and a label -- and no
    schema.  These pin the row's shape, the singleton, the orphan trap and the
    refusal of a ``(kind, class)`` pair no ruling named.
    """

    def test_creates_the_transit_bucket_with_the_asset_class(
        self, app, db, seed_user,
    ):
        """The transit row: all-NULL links, flagged, Asset, ``transit`` kind.

        Shape: ``account_id`` / ``category_id`` / ``loan_account_id`` NULL,
        ``is_owner_bucket`` True, ``class_id`` Asset (money in flight between
        the owner's own accounts is the owner's, and a debit balance while it
        is out), ``kind_id`` the ``transit`` kind, ``name`` the canonical
        "Transfers in transit" label.
        """
        with app.app_context():
            user_id = seed_user["user"].id

            row = ledger_account_service.get_or_create_transit_ledger_account(
                user_id,
            )

            assert row.account_id is None
            assert row.category_id is None
            assert row.loan_account_id is None
            assert row.is_owner_bucket is True
            assert row.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )
            assert row.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.TRANSIT,
            )
            assert row.name == "Transfers in transit"
            assert row.user_id == user_id

    def test_transit_is_an_idempotent_singleton_per_owner(
        self, app, db, seed_user, seed_second_user,
    ):
        """Repeated calls return the one transit row; a second owner gets its own.

        ``uq_ledger_accounts_owner_bucket`` permits one ``transit`` row per
        owner; the resolver returns the same row on re-call, and an owner's
        transit count stays one while another owner resolves a row of their
        own under the same key.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            other_id = seed_second_user["user"].id

            first = ledger_account_service.get_or_create_transit_ledger_account(
                user_id,
            )
            second = ledger_account_service.get_or_create_transit_ledger_account(
                user_id,
            )
            theirs = ledger_account_service.get_or_create_transit_ledger_account(
                other_id,
            )

            assert second.id == first.id
            assert theirs.id != first.id
            transit_kind = ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.TRANSIT,
            )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, kind_id=transit_kind)
                .count() == 1
            )

    def test_transit_lookup_ignores_a_preexisting_orphan(
        self, app, db, seed_user,
    ):
        """The transit lookup never returns a deleted-category orphan.

        An orphan (unflagged, all-NULL) of ANY class shares the transit row's
        column shape; the lookup keys on the flag and the kind, never on
        ``category_id IS NULL``, so the orphan is left alone and a fresh
        flagged ``transit`` row is created beside it -- the H1 trap, for the
        second bucket kind.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            orphan = LedgerAccount(
                user_id=user_id,
                class_id=ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.ASSET,
                ),
                kind_id=ref_cache.ledger_account_kind_id(
                    LedgerAccountKindEnum.ORPHAN,
                ),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Old: Gone",
            )
            _db.session.add(orphan)
            _db.session.commit()
            orphan_id = orphan.id

            transit = ledger_account_service.get_or_create_transit_ledger_account(
                user_id,
            )

            assert transit.id != orphan_id
            assert transit.is_owner_bucket is True
            assert _db.session.get(LedgerAccount, orphan_id).is_owner_bucket is False

    def test_the_bucket_resolver_refuses_an_unnamed_kind_class_pair(
        self, app, db, seed_user,
    ):
        """A ``(kind, class)`` pair the label map does not name is refused.

        No database CHECK pins a bucket's class to its kind, so the resolver's
        label map is the app's only guard against minting a malformed bucket
        -- a Liability-class transit, an Asset-class fallback.  Refused before
        any row is written.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            with pytest.raises(ValueError, match="no owner bucket is defined"):
                get_or_create_owner_bucket(
                    user_id, LedgerAccountKindEnum.TRANSIT,
                    LedgerAccountClassEnum.LIABILITY,
                )
            with pytest.raises(ValueError, match="no owner bucket is defined"):
                get_or_create_owner_bucket(
                    user_id, LedgerAccountKindEnum.FALLBACK,
                    LedgerAccountClassEnum.ASSET,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, is_owner_bucket=True)
                .count() == 0
            )


class TestCategoryResolverValidation:
    """The resolver guards its inputs before writing.

    The accounting class must be Income or Expense (no database CHECK
    constrains a category row's class, so this guard is the sole defense
    against a malformed chart entry), and a non-NULL ``category_id`` must
    name an existing category.
    """

    @pytest.mark.parametrize("bad_class", [
        LedgerAccountClassEnum.ASSET,
        LedgerAccountClassEnum.LIABILITY,
        LedgerAccountClassEnum.EQUITY,
    ])
    def test_rejects_non_income_expense_class(
        self, app, db, seed_user, bad_class,
    ):
        """A class other than Income/Expense raises ValueError, creating no row.

        All three non-counter classes (Asset, Liability, Equity) must be
        refused before any row is written -- an Asset-class "category" account
        would be a malformed chart entry the database would not catch.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]

            with pytest.raises(ValueError, match="Income or Expense"):
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, bad_class,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=groceries.id)
                .count() == 0
            )

    def test_missing_category_raises_value_error(self, app, db, seed_user):
        """A non-NULL category_id that names no category raises ValueError.

        A live transaction's ``category_id`` always references an existing
        category (the FK SET-NULLs it on delete), so a miss is a caller error;
        the resolver fails loud with the offending id rather than an opaque
        ``AttributeError`` on ``None.display_name``.  No row is created.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            missing_id = 999_999

            with pytest.raises(ValueError, match="999999"):
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, missing_id, LedgerAccountClassEnum.EXPENSE,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_id, category_id=missing_id)
                .count() == 0
            )

    def test_foreign_category_rejected(
        self, app, db, seed_user, seed_second_user,
    ):
        """A category_id owned by another user is treated as not-found (tenancy).

        The category snapshot load filters by ``user_id``, so passing user A's
        id with user B's category id finds nothing and raises -- the resolver
        never snapshots a foreign category's label into an A-owned ledger
        account, nor mints an A-owned row keyed to B's ``category_id``.
        Honours the project's "filter every user-data query by ``user_id``"
        rule and matches the idempotency lookup's scoping.
        """
        with app.app_context():
            user_a = seed_user["user"].id
            foreign_category = seed_second_user["categories"]["Groceries"]

            with pytest.raises(ValueError, match="owned by user_id"):
                ledger_account_service.get_or_create_category_ledger_account(
                    user_a, foreign_category.id, LedgerAccountClassEnum.EXPENSE,
                )
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(user_id=user_a, category_id=foreign_category.id)
                .count() == 0
            )


class TestLoanLedgerAccountResolver:
    """``get_or_create_loan_ledger_account`` materialises per-loan rows.

    Build-Order Step 4's chart resolver: one ``loan_interest`` and one
    ``loan_escrow`` Expense account, one ``loan_refund`` Asset account, and one
    ``equity_opening`` Equity account (the loan read switch's opening-balance
    account) per loan, lazily and idempotently.  These tests pin the resolver's
    behaviour (the storage-tier shape CHECK and per-(loan, kind) unique are
    covered by ``test_models/test_ledger_account.py::TestLoanLedgerShapeAndUnique``):
    correct shape / class / name snapshot per kind; idempotency; the four
    kinds coexisting for one loan; and independence across two loans.
    """

    @pytest.mark.parametrize("kind,expected_class,expected_suffix", [
        (LedgerAccountKindEnum.LOAN_INTEREST,
         LedgerAccountClassEnum.EXPENSE, "Interest"),
        (LedgerAccountKindEnum.LOAN_ESCROW,
         LedgerAccountClassEnum.EXPENSE, "Escrow"),
        (LedgerAccountKindEnum.LOAN_REFUND,
         LedgerAccountClassEnum.ASSET, "Refund"),
        (LedgerAccountKindEnum.EQUITY_OPENING,
         LedgerAccountClassEnum.EQUITY, "Opening"),
    ])
    def test_creates_loan_row_with_correct_shape_per_kind(
        self, app, db, seed_user, kind, expected_class, expected_suffix,
    ):
        """Each loan kind creates one correctly-shaped row with the right class.

        Shape contract for a per-loan ledger account: ``loan_account_id`` points
        at the loan; ``account_id`` / ``category_id`` NULL and ``is_owner_bucket``
        False (the per-loan column shape); ``kind_id`` the requested loan kind;
        ``class_id`` the class that kind implies (interest/escrow -> Expense,
        refund -> Asset, equity_opening -> Equity); ``name`` snapshots
        "<loan name> -- <suffix>"; ``user_id`` the owner; the row is flushed
        (``id`` assigned).  The ``equity_opening`` case is the loan read
        switch's opening-balance account -- a fourth per-loan kind that reuses
        the same resolver, guards, and ``uq_ledger_accounts_loan`` unique as the
        three correction kinds, differing only in its Equity class.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Home Loan",
            )

            row = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan.id, kind,
            )

            assert row.id is not None
            assert row.loan_account_id == loan.id
            assert row.account_id is None
            assert row.category_id is None
            assert row.is_owner_bucket is False
            assert row.kind_id == ref_cache.ledger_account_kind_id(kind)
            assert row.class_id == ref_cache.ledger_account_class_id(
                expected_class,
            )
            assert row.name == f"Home Loan -- {expected_suffix}"
            assert row.user_id == user_id

    def test_idempotent_returns_existing_row(self, app, db, seed_user):
        """A second call for the same (loan, kind) returns the same row.

        ``uq_ledger_accounts_loan`` permits one row per (owner, loan, kind); the
        resolver short-circuits on the existing row (a second insert would
        raise) and returns the same PK.  The interest-row count for the loan
        stays exactly one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Idem Loan",
            )

            first = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            second = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            assert second.id == first.id
            assert len(_loan_ledger_rows(loan.id)) == 1

    def test_four_kinds_one_loan_yield_four_distinct_rows(
        self, app, db, seed_user,
    ):
        """Resolving all four kinds for one loan yields four distinct rows.

        A loan has up to four per-loan accounts -- interest, escrow, refund, and
        the read switch's opening-balance equity account -- each a separate
        chart entry under the (loan, kind) key.  All four carry the same
        ``loan_account_id``; their ``kind_id`` / ``class_id`` / ``id`` differ
        (interest + escrow Expense, refund Asset, opening Equity).  This also
        proves ``equity_opening`` coexists with the three correction kinds
        rather than colliding on ``uq_ledger_accounts_loan`` (its distinct
        ``kind_id`` keeps it a separate row).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Quad Loan",
            )

            interest, escrow, refund, opening = (
                ledger_account_service.get_or_create_loan_ledger_account(
                    user_id, loan.id, kind,
                )
                for kind in (
                    LedgerAccountKindEnum.LOAN_INTEREST,
                    LedgerAccountKindEnum.LOAN_ESCROW,
                    LedgerAccountKindEnum.LOAN_REFUND,
                    LedgerAccountKindEnum.EQUITY_OPENING,
                )
            )

            assert len({interest.id, escrow.id, refund.id, opening.id}) == 4
            assert len(_loan_ledger_rows(loan.id)) == 4
            assert interest.class_id == _expense_class_id()
            assert escrow.class_id == _expense_class_id()
            assert refund.class_id == _asset_class_id()
            assert opening.class_id == _equity_class_id()

    def test_same_kind_two_loans_independent(self, app, db, seed_user):
        """The same kind on two loans yields two distinct rows.

        The natural key is (owner, loan, kind), so a ``loan_interest`` account
        for loan A is distinct from loan B's -- proving the idempotency lookup
        keys on ``loan_account_id``.  A missing loan filter would collapse all
        of a user's loans onto one shared interest account, commingling them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan_a = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Loan A",
            )
            loan_b = create_account_of_type(
                seed_user, _db.session, "Auto Loan", "Loan B",
            )

            row_a = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan_a.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            row_b = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan_b.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            assert row_a.id != row_b.id
            assert row_a.loan_account_id == loan_a.id
            assert row_b.loan_account_id == loan_b.id
            assert row_a.name == "Loan A -- Interest"
            assert row_b.name == "Loan B -- Interest"

    def test_archived_loan_still_resolves(self, app, db, seed_user):
        """An archived (``is_active`` False) loan still resolves its accounts.

        The loan load filters ``(id, user_id)`` but deliberately NOT
        ``is_active``: an archived loan that still carries settled payment
        history must keep resolving its interest / escrow / refund accounts so
        the immutable postings on them reconcile.  This locks that documented
        contract -- a future maintainer who "tightens" the load with
        ``.filter_by(is_active=True)`` would break archived-loan resolution
        (raising the misleading "no account ... owned by user_id" for a loan
        that exists), and this test would fail on the unhandled ``ValueError``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Archived Loan",
            )
            loan.is_active = False
            _db.session.flush()

            row = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            assert row.loan_account_id == loan.id
            assert row.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert row.name == "Archived Loan -- Interest"


class TestLoanLedgerResolverValidation:
    """The loan resolver guards its inputs before writing.

    Because ``ck_ledger_accounts_loan_shape`` is columns-only (it cannot pin
    ``kind_id`` to the loan kinds, nor verify ``loan_account_id`` references a
    loan), these guards are the sole defense against a malformed per-loan chart
    entry: the kind must be one of the four loan kinds, and the account must
    be an amortizing loan owned by the calling user.
    """

    @pytest.mark.parametrize("bad_kind", [
        LedgerAccountKindEnum.LINKED,
        LedgerAccountKindEnum.CATEGORY,
        LedgerAccountKindEnum.FALLBACK,
        LedgerAccountKindEnum.ORPHAN,
    ])
    def test_rejects_non_loan_kind(self, app, db, seed_user, bad_kind):
        """A non-loan kind raises ValueError, creating no row.

        All four non-loan kinds (linked, category, fallback, orphan) must be
        refused before any write -- a ``linked``-kinded ``loan_account_id`` row
        would be a malformed chart entry the columns-only CHECK would not catch.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Bad Kind Loan",
            )

            with pytest.raises(ValueError, match="loan ledger account kind"):
                ledger_account_service.get_or_create_loan_ledger_account(
                    user_id, loan.id, bad_kind,
                )
            assert _loan_ledger_rows(loan.id) == []

    @pytest.mark.parametrize("non_loan_type", [
        "Checking", "HYSA", "Credit Card", "Brokerage",
    ])
    def test_rejects_non_amortizing_account(
        self, app, db, seed_user, non_loan_type,
    ):
        """A non-amortizing account raises ValueError, creating no row.

        The amortizing-loan guard rejects every account that is not a loan: a
        Checking (PLAIN), a HYSA (INTEREST), a Credit Card (a Liability but NOT
        amortizing), and a Brokerage (INVESTMENT).  Each would otherwise mint a
        ``loan_interest`` row pointing at a non-loan account, which the
        columns-only shape CHECK cannot prevent.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, non_loan_type,
                f"Not A Loan {non_loan_type}",
            )

            with pytest.raises(ValueError, match="not an amortizing loan"):
                ledger_account_service.get_or_create_loan_ledger_account(
                    user_id, account.id, LedgerAccountKindEnum.LOAN_INTEREST,
                )
            assert _loan_ledger_rows(account.id) == []

    def test_missing_account_raises_value_error(self, app, db, seed_user):
        """A loan_account_id naming no account raises ValueError, no row.

        A live caller only resolves a settled payment's loan account, so a miss
        is a caller error; the resolver fails loud with the offending id rather
        than minting a per-loan row whose ``loan_account_id`` FK would dangle.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            missing_id = 999_999

            with pytest.raises(ValueError, match="999999"):
                ledger_account_service.get_or_create_loan_ledger_account(
                    user_id, missing_id, LedgerAccountKindEnum.LOAN_INTEREST,
                )
            assert _loan_ledger_rows(missing_id) == []

    def test_foreign_loan_rejected(self, app, db, seed_user, seed_second_user):
        """A loan owned by another user is treated as not-found (tenancy).

        The loan load filters by ``user_id``, so passing user A's id with user
        B's loan id finds nothing and raises -- the resolver never mints an
        A-owned per-loan row keyed to B's loan, nor snapshots B's loan name into
        A's chart.  Honours the project's "filter every user-data query by
        user_id" rule (an IDOR otherwise).
        """
        with app.app_context():
            user_a = seed_user["user"].id
            foreign_loan = create_account_of_type(
                seed_second_user, _db.session, "Mortgage", "B's Loan",
            )

            with pytest.raises(ValueError, match="owned by user_id"):
                ledger_account_service.get_or_create_loan_ledger_account(
                    user_a, foreign_loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
                )
            assert _loan_ledger_rows(foreign_loan.id) == []

    def test_long_loan_name_truncated_to_fit_column(self, app, db, seed_user):
        """A loan name + suffix wider than the name column is clipped, not rejected.

        ``accounts.name`` is ``String(100)``, so "<loan name> -- Interest" can
        reach 112 chars -- wider than the ``ledger_accounts.name`` ``String(100)``
        column, which PostgreSQL rejects (not silently truncates) on insert.
        The resolver clips the snapshot to the column width so the row inserts
        cleanly; ``name`` is display-only (the natural key is the (user, loan,
        kind) IDs), so the clip is lossless for logic.  Without the clip this
        resolve would raise ``DataError`` and 500 the settling payment.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            long_name = "L" * 95
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", long_name,
            )

            row = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            assert len(row.name) == 100
            assert row.name == f"{long_name} -- Interest"[:100]


class TestAccountCounterResolver:
    """``get_or_create_account_counter_account`` materialises the counter rows.

    Build-Order Step 5's chart resolver, widened by ruling **R-FO** (plan step
    X-f3d): a NON-loan account's ``account_opening`` / ``account_trueup``
    correction books its COUNTER leg into a per-account row, and there are now
    three kinds of that row -- ``anchor_equity`` (Equity), ``interest_income``
    (Income) and ``unrealized_change`` (Unrealized).  All three share the
    ``account_id`` column with the ``linked`` row and coexist under the re-keyed
    ``uq_ledger_accounts_account_kind`` unique, which is what lets the dispatch
    add kinds without adding an index.  These tests pin the resolver's behaviour
    (the index itself is covered by
    ``test_models/test_ledger_account.py::TestPartialUnique``): correct shape /
    class / name snapshot per kind; idempotency; coexistence with the linked row
    AND with each other; archived accounts still resolving; and the clip.
    """

    @pytest.mark.parametrize("kind,expected_class,expected_suffix", [
        (LedgerAccountKindEnum.ANCHOR_EQUITY,
         LedgerAccountClassEnum.EQUITY, "Opening"),
        (LedgerAccountKindEnum.INTEREST_INCOME,
         LedgerAccountClassEnum.INCOME, "Interest Income"),
        (LedgerAccountKindEnum.UNREALIZED_CHANGE,
         LedgerAccountClassEnum.UNREALIZED, "Change in Value"),
    ])
    def test_creates_counter_row_with_correct_shape_per_kind(
        self, app, db, seed_user, kind, expected_class, expected_suffix,
    ):
        """Each counter kind creates one correctly-shaped row with the right class.

        Shape contract: ``account_id`` points at the account (shared with the
        linked row); ``category_id`` / ``loan_account_id`` NULL and
        ``is_owner_bucket`` False; ``class_id`` the class that kind implies;
        ``kind_id`` the requested kind; ``name`` snapshots
        "<account> -- <suffix>" (unlike a linked row -- the COALESCE display
        rule is the LINKED-row rule, so readers render this snapshot);
        ``user_id`` the owner; flushed.

        The class-by-kind half is what a bare "it wrote a row" assertion would
        miss: no CHECK pins ``class_id`` to ``kind_id``, so booking an
        a value change into an Equity-class row would tie out perfectly and
        still leave the income statement silent.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Counter Shape",
            )

            row = ledger_account_service.get_or_create_account_counter_account(
                user_id, account.id, kind,
            )

            assert row.id is not None
            assert row.account_id == account.id
            assert row.category_id is None
            assert row.loan_account_id is None
            assert row.is_owner_bucket is False
            assert row.class_id == ref_cache.ledger_account_class_id(
                expected_class,
            )
            assert row.kind_id == ref_cache.ledger_account_kind_id(kind)
            assert row.name == f"Counter Shape -- {expected_suffix}"
            assert row.user_id == user_id

    def test_idempotent_returns_existing_row(self, app, db, seed_user):
        """A second call for the same (account, kind) returns the same row.

        ``uq_ledger_accounts_account_kind`` permits one row per
        ``(account, kind)``; the resolver short-circuits on the existing row
        and returns the same PK.  The account's total ledger-row count stays
        exactly two (the linked row + the one counter row).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "HYSA", "Idem Savings",
            )

            first = ledger_account_service.get_or_create_account_counter_account(
                user_id, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            second = ledger_account_service.get_or_create_account_counter_account(
                user_id, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )

            assert second.id == first.id
            assert len(ledger_accounts_for_account(_db.session, account.id)) == 2

    def test_three_kinds_one_account_yield_three_distinct_rows(
        self, app, db, seed_user,
    ):
        """All three counter kinds coexist on one account under the same unique.

        R-FO's dispatch adds kinds to a key that already carried the ``linked``
        row and the ``anchor_equity`` twin, so the three counter rows must
        coexist rather than collide on ``uq_ledger_accounts_account_kind``.
        This is what makes "no new index" a property rather than a hope -- and
        it is also the shape a RE-TYPED account reaches, where an old
        ``interest_income`` row and a new ``unrealized_change`` row both exist.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "Money Market", "Triple Counter",
            )

            rows = [
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, account.id, kind,
                )
                for kind in (
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                    LedgerAccountKindEnum.INTEREST_INCOME,
                    LedgerAccountKindEnum.UNREALIZED_CHANGE,
                )
            ]

            assert len({row.id for row in rows}) == 3
            # The linked pairing plus the three counter rows.
            assert len(ledger_accounts_for_account(_db.session, account.id)) == 4

    def test_counter_row_coexists_with_linked_row_and_hook_stays_idempotent(
        self, app, db, seed_user,
    ):
        """The counter row never displaces the linked pairing (the C3 hardening).

        After the counter row exists, ``create_ledger_account_for_account`` must
        still resolve the LINKED row (not the counter) -- the kind filter added
        in C3 -- and ``posting_reads._ledger_account_for`` must keep
        returning the linked row rather than raising ``MultipleResultsFound``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "Brokerage", "Coexist Brokerage",
            )
            linked_before = posting_reads._ledger_account_for(account.id)

            counter = (
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, account.id, LedgerAccountKindEnum.UNREALIZED_CHANGE,
                )
            )
            assert counter.id != linked_before.id

            # The hook's idempotency lookup still finds the LINKED row.
            hook_row = ledger_account_service.create_ledger_account_for_account(
                account,
            )
            assert hook_row.id == linked_before.id

            # The shared pairing lookup still resolves the linked row.
            assert posting_reads._ledger_account_for(account.id).id == (
                linked_before.id
            )

            # THE DISCRIMINATING PIN for the C3 kind filter (the counter row
            # alone cannot catch a reverted filter: the linked row wins both
            # heap and index order).  With the linked row GONE and only the
            # counter left, an unfiltered lookup would return the counter and
            # skip re-creating the pairing; the kind-filtered hook instead
            # mints a fresh LINKED row distinct from it.
            _db.session.delete(linked_before)
            _db.session.flush()
            recreated = ledger_account_service.create_ledger_account_for_account(
                account,
            )
            assert recreated.id != counter.id
            assert recreated.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            )

    def test_archived_account_still_resolves(self, app, db, seed_user):
        """An archived (inactive) account keeps resolving its counter row.

        Archiving disables new activity; it does not erase posted facts, so
        the corrections on an archived account must keep reconciling.  The
        loader deliberately does not filter ``is_active``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Archived Checking",
            )
            account.is_active = False
            _db.session.commit()

            row = ledger_account_service.get_or_create_account_counter_account(
                user_id, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert row.account_id == account.id

    def test_long_account_name_truncated_to_fit_column(
        self, app, db, seed_user,
    ):
        """A name + suffix wider than the column is clipped, not rejected.

        ``accounts.name`` is ``String(100)``, so "<name> -- Change in Value"
        can reach 118 chars -- wider than ``ledger_accounts.name``'s
        ``String(100)``, which PostgreSQL rejects on insert.  The resolver
        clips the snapshot; ``name`` is display-only, so the clip is lossless
        for logic.  The LONGEST suffix is the one under test: R-FO's new kinds
        made the widest label wider, and a clip sized for "-- Opening" alone
        would now overflow.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            long_name = "A" * 95
            account = create_account_of_type(
                seed_user, _db.session, "Checking", long_name,
            )

            row = ledger_account_service.get_or_create_account_counter_account(
                user_id, account.id, LedgerAccountKindEnum.UNREALIZED_CHANGE,
            )

            assert len(row.name) == 100
            assert row.name == f"{long_name} -- Change in Value"[:100]


class TestAccountCounterResolverValidation:
    """The load-bearing guards: known kinds, non-loan targets, tenancy-filtered.

    No database CHECK pins a counter row's kind, its class, or its target (the
    same columns-only trust contract the loan shape CHECK carries), so the
    resolver's guards are the app's sole defense -- and what keeps a loan's
    linked ledger free of counter rows (the loan oracle's bare-``account_id``
    helpers rely on that, per the Step-5 plan's C6 checklist).
    """

    @pytest.mark.parametrize("bad_kind", [
        LedgerAccountKindEnum.LINKED,
        LedgerAccountKindEnum.CATEGORY,
        LedgerAccountKindEnum.FALLBACK,
        LedgerAccountKindEnum.ORPHAN,
        LedgerAccountKindEnum.LOAN_INTEREST,
        LedgerAccountKindEnum.EQUITY_OPENING,
    ])
    def test_rejects_non_counter_kind(self, app, db, seed_user, bad_kind):
        """A kind outside the three counter kinds raises, creating no row.

        The map is what derives a row's ``class_id`` from its ``kind_id``, so a
        kind absent from it has no class to carry -- and the per-loan kinds are
        in the list because their column shape is ``loan_account_id``, which an
        ``account_id``-linked row would violate with only a CHECK that cannot
        see the kind.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Bad Kind Account",
            )
            # The rows the account ALREADY has: the linked pairing plus the
            # ``anchor_equity`` twin the factory's sentinel opening minted.  The
            # property under test is that the refusal ADDS none, which a
            # fixed expected set cannot state (``LINKED`` is both a refused
            # kind and a kind the account legitimately carries).
            before = {
                row.id
                for row in ledger_accounts_for_account(_db.session, account.id)
            }

            with pytest.raises(ValueError, match="counter ledger account kind"):
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, account.id, bad_kind,
                )
            assert {
                row.id
                for row in ledger_accounts_for_account(_db.session, account.id)
            } == before

    def test_rejects_amortizing_loan(self, app, db, seed_user):
        """A Mortgage target raises: loans book equity via equity_opening.

        Minting a per-account counter row for a loan would double-book its
        opening across two equity accounts (the loan path already posts onto
        the per-loan ``equity_opening`` account).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Guarded Loan",
            )
            with pytest.raises(ValueError, match="amortizing loan"):
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, loan.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
                )
            # No counter row was minted alongside the linked pairing.
            rows = ledger_accounts_for_account(_db.session, loan.id)
            assert [row.kind_id for row in rows] == [
                ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED),
            ]

    def test_missing_account_raises_value_error(self, app, db, seed_user):
        """A nonexistent account id fails loud with the offending id."""
        with app.app_context():
            user_id = seed_user["user"].id
            with pytest.raises(ValueError, match="no account with id=999999"):
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, 999999, LedgerAccountKindEnum.ANCHOR_EQUITY,
                )

    def test_foreign_account_rejected(
        self, app, db, seed_user, seed_second_user,
    ):
        """Another owner's account is treated as "not found" (tenancy)."""
        with app.app_context():
            user_a = seed_user["user"].id
            foreign_account = seed_second_user["account"]
            with pytest.raises(ValueError, match="no account with id="):
                ledger_account_service.get_or_create_account_counter_account(
                    user_a, foreign_account.id,
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                )
            # Only the owner's own rows survive (their linked pairing and
            # the counter row their $2000 opening minted) -- none owned by A.
            foreign_rows = ledger_accounts_for_account(
                _db.session, foreign_account.id,
            )
            assert len(foreign_rows) == 2
            assert all(
                row.user_id == seed_second_user["user"].id
                for row in foreign_rows
            )


class TestTrueUpCounterKindIsTotal:
    """``anchor_correction_counter_kind`` -- ruling R-FO's dispatch, TOTAL.

    The rule the whole step turns on: what a balance assertion's difference
    MEANS is a property of the ACCOUNT.  These cases pin both halves of it --
    the per-kind answers, and the totality that makes "a new projection kind
    silently books its return to equity" impossible.
    """

    @pytest.mark.parametrize("projection_kind,expected", [
        (AccountProjectionKind.PLAIN, LedgerAccountKindEnum.ANCHOR_EQUITY),
        (AccountProjectionKind.INTEREST, LedgerAccountKindEnum.INTEREST_INCOME),
        (AccountProjectionKind.INVESTMENT, LedgerAccountKindEnum.UNREALIZED_CHANGE),
        (AccountProjectionKind.APPRECIATING,
         LedgerAccountKindEnum.UNREALIZED_CHANGE),
    ])
    def test_a_true_up_names_what_the_difference_was(
        self, projection_kind, expected,
    ):
        """Each non-loan kind's true-up books to the counter R-FO names.

        Pinned by hand, one row per kind, because these four values ARE the
        ruling: an ``INVESTMENT`` true-up that resolved to ``ANCHOR_EQUITY``
        would tie out, pass every balance assertion, and leave ``$10,623.66`` of
        measured investment return off the income statement -- which is the
        defect this step exists to close.
        """
        assert ledger_lines.anchor_correction_counter_kind(
            projection_kind, is_opening=False,
        ) is expected

    @pytest.mark.parametrize("projection_kind", list(AccountProjectionKind))
    def test_an_opening_always_books_to_equity(self, projection_kind):
        """An OPENING books to the equity row for EVERY kind, loans included.

        An opening is capital brought onto the books, not something earned: a
        Property's $350,000.00 opening as a "change in value" would say the house
        appreciated by its whole value on the day it was recorded.  Parametrized
        over the WHOLE enum -- ``AMORTIZING`` included -- because the opening
        arm answers before the dispatch is consulted, and that is exactly the
        property worth pinning.
        """
        assert ledger_lines.anchor_correction_counter_kind(
            projection_kind, is_opening=True,
        ) is LedgerAccountKindEnum.ANCHOR_EQUITY

    def test_a_true_up_refuses_the_kind_that_books_elsewhere(self):
        """``AMORTIZING`` has no true-up rule here and says so loudly.

        A loan's corrections belong to the loan posting package entirely, so
        there is no honest answer to give -- and returning a default would put
        a loan's balance on two equity accounts.
        """
        with pytest.raises(ValueError, match="no true-up counter rule"):
            ledger_lines.anchor_correction_counter_kind(
                AccountProjectionKind.AMORTIZING, is_opening=False,
            )

    def test_the_dispatch_covers_every_kind_the_classifier_returns(self):
        """No projection kind is left without an answer or a refusal.

        THE totality guard.  ``classify_account`` returns one of five members;
        this asserts each is either dispatched or explicitly refused, so adding
        a sixth kind to the enum fails HERE -- at a named test -- instead of
        booking that kind's return to equity in silence.  A dict lookup cannot
        be made exhaustive by the language, so it is made exhaustive by a test.
        """
        answered, refused = [], []
        for projection_kind in AccountProjectionKind:
            try:
                ledger_lines.anchor_correction_counter_kind(
                    projection_kind, is_opening=False,
                )
            except ValueError:
                refused.append(projection_kind)
            else:
                answered.append(projection_kind)

        # The loop above puts every member in exactly one list, so asserting
        # their union is the enum would be a tautology; the refusal set is the
        # guard, and a kind added without a rule lands in it.
        assert refused == [AccountProjectionKind.AMORTIZING], (
            "every non-loan projection kind must name what its true-up WAS; "
            f"unruled kinds: {refused}"
        )



class TestAddOrReuseUnderALostRace:
    """``_common.add_or_reuse`` -- the race-safe insert every resolver consumes.

    **It exists because plan step X-f1c3c made a pre-existing race REACHABLE**
    (ruling R-EN).  Every ``get_or_create_*`` here is a check-then-INSERT: two
    requests can both find no row for a natural key and both try to create one,
    and the loser's flush hits a partial-unique index.  Until R-EN the C-17
    optimistic lock made one of two concurrent true-ups answer 409 before
    either reached the anchor-equity resolver, so the collision was masked on
    that path -- and never masked at all on the category and loan resolvers,
    which any two concurrent settles can reach.

    The loser's outcome must be SUCCESS, not a 500: the winner's row is exactly
    the row the loser wanted.  These simulate the lost race directly rather
    than through threads, because a thread race is a non-deterministic detector
    and the branch under test is deterministic -- insert the row a "concurrent
    request" already committed, and require the helper to hand back that row.
    """

    def _seed_conflicting_row(self, seed_user):
        """Insert the row a racing request would have committed first.

        Returns the anchor-equity row for ``seed_user``'s account, created
        through the resolver itself so the natural key is exactly the one the
        second call will collide on.
        """
        return ledger_account_service.get_or_create_account_counter_account(
            seed_user["user"].id, seed_user["account"].id,
            LedgerAccountKindEnum.ANCHOR_EQUITY,
        )

    def test_a_lost_race_returns_the_winners_row(self, app, db, seed_user):
        """The loser gets the winner's row back, not an IntegrityError.

        Drives ``add_or_reuse`` directly with a row whose natural key is
        already taken, which is precisely the state a lost race leaves.  A
        helper that let the ``IntegrityError`` escape -- the behaviour before
        this step -- fails here.
        """
        with app.app_context():
            winner = self._seed_conflicting_row(seed_user)
            _db.session.commit()

            duplicate = LedgerAccount(
                user_id=seed_user["user"].id,
                class_id=ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.EQUITY,
                ),
                kind_id=ref_cache.ledger_account_kind_id(
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                ),
                account_id=seed_user["account"].id,
                name="Checking -- Opening",
            )
            resolved = _chart_common.add_or_reuse(
                duplicate,
                lambda: _db.session.query(LedgerAccount).filter_by(
                    user_id=seed_user["user"].id,
                    account_id=seed_user["account"].id,
                    kind_id=ref_cache.ledger_account_kind_id(
                        LedgerAccountKindEnum.ANCHOR_EQUITY,
                    ),
                ).first(),
            )

            assert resolved.id == winner.id

    def test_the_session_survives_a_lost_race(self, app, db, seed_user):
        """A lost race leaves the caller's transaction usable.

        The SAVEPOINT is the whole point: without it the failed flush poisons
        the session and the caller's next statement raises
        ``PendingRollbackError``, so the resolver would "succeed" and the
        request would still die.  Committing real work afterwards is what
        proves the savepoint was rolled back rather than the transaction.
        """
        with app.app_context():
            winner = self._seed_conflicting_row(seed_user)
            _db.session.commit()

            # Driven through ``add_or_reuse`` DIRECTLY, not through the
            # resolver: the resolver's own check-first lookup finds the winner
            # and never reaches the helper, so routing through it would make
            # this case unable to fail.  Caught by planting the race-unsafe
            # original -- the mutant killed the sibling case and left this one
            # green.
            duplicate = LedgerAccount(
                user_id=seed_user["user"].id,
                class_id=ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.EQUITY,
                ),
                kind_id=ref_cache.ledger_account_kind_id(
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                ),
                account_id=seed_user["account"].id,
                name="Checking -- Opening",
            )
            assert _chart_common.add_or_reuse(
                duplicate, lambda: winner,
            ) is winner
            # Real work, after the reuse path, in the same transaction.
            category = Category(
                user_id=seed_user["user"].id,
                group_name="After The Race",
                item_name="Proof The Session Lives",
            )
            _db.session.add(category)
            _db.session.commit()

            assert category.id is not None

    def test_an_unrelated_integrity_error_propagates(self, app, db, seed_user):
        """A violation that is NOT a natural key is re-raised, never swallowed.

        The guard that keeps this helper from becoming a blanket
        ``except IntegrityError``.  A NULL ``user_id`` trips a NOT NULL rather
        than one of the four named unique indexes, so it must surface.
        """
        with app.app_context():
            orphan = LedgerAccount(
                user_id=None,
                class_id=ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.EQUITY,
                ),
                kind_id=ref_cache.ledger_account_kind_id(
                    LedgerAccountKindEnum.ANCHOR_EQUITY,
                ),
                account_id=seed_user["account"].id,
                name="No Owner",
            )
            with pytest.raises(IntegrityError):
                _chart_common.add_or_reuse(orphan, lambda: None)
            _db.session.rollback()

    def test_the_natural_key_names_are_real_indexes(self, app, db):
        """Every name in the allowlist exists in the live catalog.

        The allowlist is what tells a lost race from an unrelated failure, so a
        name that does not exist silently turns the race back into a 500 with
        the suite green.  **This case exists because one of the four names was
        INVENTED in the first draft** (``uq_ledger_accounts_user_class_category``,
        which has never existed) and was caught by reading ``pg_indexes`` rather
        than by any test.
        """
        with app.app_context():
            live = {
                row[0] for row in _db.session.execute(_db.text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'budget' AND tablename = 'ledger_accounts'"
                ))
            }
            missing = set(
                _chart_common._LEDGER_ACCOUNT_NATURAL_KEYS,
            ) - live
            assert not missing, (
                f"allowlisted index names that do not exist: {missing}"
            )
