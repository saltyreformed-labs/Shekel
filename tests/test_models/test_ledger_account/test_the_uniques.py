"""The partial uniques: one row per (account, kind), and the Step-3 uniques that key the category
and fallback rows.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.ledger_account import LedgerAccount
from tests._stored_ledger import chart_row_or_plant
from tests._test_helpers import create_account_of_type

from ._helpers import _class_id, _kind_id


class TestPartialUnique:
    """``uq_ledger_accounts_account_kind`` -- one row per (account, kind).

    Re-keyed by Step 5 (migration ``b7d9f3a1c5e8``) from the original
    single-column ``uq_ledger_accounts_account``: the ``anchor_equity``
    twin shares the ``account_id`` column with the ``linked`` row, so the
    key gained ``kind_id`` -- one row of EACH account-linked kind per
    account, never two of either.
    """

    def test_second_linked_row_for_same_account_rejected(
        self, app, db, seed_user,
    ):
        """A second LINKED row for the same ``account_id`` trips the index.

        The test plants the account's one linked row (no door pairs one since
        plan task plan#3133); a second linked row with the same
        ``(account_id, kind_id)`` must raise on the partial unique index (the
        uniqueness applies per account-linked kind).
        """
        with app.app_context():
            account = create_account_of_type(seed_user, _db.session, "Checking", "Dup Checking")
            chart_row_or_plant(_db.session, account, "linked", "Asset")
            with pytest.raises(IntegrityError):
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.ASSET),
                    kind_id=_kind_id(LedgerAccountKindEnum.LINKED),
                    account_id=account.id,
                    name=None,
                ))
                _db.session.commit()
            _db.session.rollback()

    def test_anchor_equity_twin_coexists_but_never_duplicates(
        self, app, db, seed_user,
    ):
        """One ``anchor_equity`` twin per account: coexists, second rejected.

        The Step-5 widening this re-key exists for: an ``anchor_equity``
        row sharing the linked row's ``account_id`` INSERTs cleanly
        (different ``kind_id``), while a SECOND anchor-equity row for the
        same account trips the ``(account_id, kind_id)`` key exactly as a
        duplicate linked row would.
        """
        with app.app_context():
            # No door writes an account's chart rows (plan task plan#3133), so
            # this storage-tier test plants the linked row and the twin itself.
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Twin Checking",
                anchor_balance=Decimal("0.00"),
            )
            chart_row_or_plant(_db.session, account, "linked", "Asset")
            _db.session.add(LedgerAccount(
                user_id=account.user_id,
                class_id=_class_id(LedgerAccountClassEnum.EQUITY),
                kind_id=_kind_id(LedgerAccountKindEnum.ANCHOR_EQUITY),
                account_id=account.id,
                name=f"{account.name} -- Opening",
            ))
            _db.session.commit()
            rows = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=account.id)
                .count()
            )
            assert rows == 2  # the linked row + the equity twin

            with pytest.raises(IntegrityError):
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.EQUITY),
                    kind_id=_kind_id(LedgerAccountKindEnum.ANCHOR_EQUITY),
                    account_id=account.id,
                    name=f"{account.name} -- Opening",
                ))
                _db.session.commit()
            _db.session.rollback()

    def test_multiple_unlinked_rows_permitted(self, app, db, seed_user):
        """Multiple unlinked rows coexist -- the linked unique excludes them.

        ``uq_ledger_accounts_account_kind`` is partial (``WHERE account_id IS
        NOT NULL``), so unlinked rows (NULL ``account_id``) never collide on
        it.
        Two DISTINCT category rows (different ``category_id``, same class)
        demonstrate this: they fall outside the linked unique, are kept apart
        by ``uq_ledger_accounts_category``, and coexist.

        (Step 3 note: only two *fallback* rows -- same class, both
        ``is_owner_bucket`` True -- collide, on the
        ``uq_ledger_accounts_owner_bucket`` singleton; two NULL/NULL
        *orphans* (``is_owner_bucket`` False) instead coexist freely.  Both are
        asserted by ``TestCategoryFallbackUniques`` below; here we use
        distinct category rows to isolate the *linked* unique's exclusion of
        non-linked rows.)
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            groceries = seed_user["categories"]["Groceries"]
            rent = seed_user["categories"]["Rent"]
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=groceries.id,
                name=groceries.display_name,
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=rent.id,
                name=rent.display_name,
            ))
            _db.session.commit()
            unlinked = (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.account_id.is_(None))
                .count()
            )
            assert unlinked == 2


class TestCategoryFallbackUniques:
    """The Step-3 partial uniques key the category / fallback rows.

    ``uq_ledger_accounts_category`` (one row per owner+category+class) and
    ``uq_ledger_accounts_owner_bucket`` (one *fallback* per owner+class,
    keyed ``WHERE is_owner_bucket``) are the natural keys of the per-category
    chart of accounts.  Their predicates are disjoint from each other and
    from ``uq_ledger_accounts_account_kind``, and deleted-category *orphans*
    (``is_owner_bucket`` False, NULL/NULL) fall outside every unique, so all four
    row kinds coexist correctly.
    """

    def test_second_category_row_same_key_rejected(self, app, db, seed_user):
        """A duplicate (owner, category, class) category row trips the unique.

        ``uq_ledger_accounts_category`` permits one ledger account per owner,
        category, and accounting class; a second with the same key must raise
        on exactly that index.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=user_id, class_id=class_id,
                    kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                    account_id=None, category_id=category.id,
                    name=category.display_name,
                ))
                _db.session.commit()
            assert "uq_ledger_accounts_category" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_same_category_two_classes_permitted(self, app, db, seed_user):
        """One category yields TWO rows -- Income-class and Expense-class.

        ``uq_ledger_accounts_category`` keys on ``class_id`` too, so a
        type-agnostic category used for both an income and an expense
        transaction correctly gets one ledger account per class (the edge
        case the (category, class) natural key is designed for).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.INCOME),
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(category_id=category.id)
                .count() == 2
            )

    def test_second_fallback_row_same_class_rejected(
        self, app, db, seed_user,
    ):
        """A duplicate (owner, class) FALLBACK row trips the singleton.

        ``uq_ledger_accounts_owner_bucket`` (keyed ``WHERE is_owner_bucket``)
        enforces exactly one fallback per owner per class; a second
        ``is_owner_bucket`` row of the same class must raise on that index.  Only
        rows flagged ``is_owner_bucket`` are constrained -- orphans of the same
        class (tested separately) are not.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Expense",
            ))
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=user_id, class_id=class_id,
                    kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                    account_id=None, category_id=None, is_owner_bucket=True,
                    name="Uncategorized Expense",
                ))
                _db.session.commit()
            assert "uq_ledger_accounts_owner_bucket" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_fallback_two_classes_permitted(self, app, db, seed_user):
        """Uncategorized-Income and Uncategorized-Expense fallbacks coexist."""
        with app.app_context():
            user_id = seed_user["user"].id
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.INCOME),
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Income",
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Expense",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.is_owner_bucket.is_(True))
                .filter_by(user_id=user_id)
                .count() == 2
            )

    def test_second_transit_row_same_owner_rejected(self, app, db, seed_user):
        """A duplicate (owner, Asset, transit) row trips the same singleton.

        Ruling **R-BAL99** (plan step ``balance:X-bi-6-3``): the owner-bucket
        key ``(user_id, class_id, kind_id) WHERE is_owner_bucket`` holds the
        Transfers-in-transit account to one per owner exactly as it holds
        each Uncategorized fallback -- one index, a second bucket KIND, no
        schema of its own.  A second flagged ``transit`` row for the owner
        must raise on that index.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.ASSET)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.TRANSIT),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Transfers in transit",
            ))
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=user_id, class_id=class_id,
                    kind_id=_kind_id(LedgerAccountKindEnum.TRANSIT),
                    account_id=None, category_id=None, is_owner_bucket=True,
                    name="Transfers in transit",
                ))
                _db.session.commit()
            assert "uq_ledger_accounts_owner_bucket" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_transit_and_fallbacks_coexist_under_one_key(
        self, app, db, seed_user,
    ):
        """The transit row and both fallbacks coexist: three buckets, one owner.

        The key is per (owner, class, kind), so the Asset-class ``transit``
        bucket sits beside the Income- and Expense-class ``fallback`` buckets
        with no collision -- and an ORPHAN (unflagged, all-NULL) beside all
        three, outside the key as ever.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            for kind, ledger_class, name in (
                (LedgerAccountKindEnum.TRANSIT, LedgerAccountClassEnum.ASSET,
                 "Transfers in transit"),
                (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.INCOME,
                 "Uncategorized Income"),
                (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.EXPENSE,
                 "Uncategorized Expense"),
            ):
                _db.session.add(LedgerAccount(
                    user_id=user_id, class_id=_class_id(ledger_class),
                    kind_id=_kind_id(kind),
                    account_id=None, category_id=None, is_owner_bucket=True,
                    name=name,
                ))
            _db.session.add(LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Old: Gone",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.is_owner_bucket.is_(True))
                .filter_by(user_id=user_id)
                .count() == 3
            )

    def test_category_and_fallback_coexist(self, app, db, seed_user):
        """A category row and the fallback of the same class coexist.

        Proves the partial-unique predicates are disjoint (``category_id IS
        NOT NULL AND account_id IS NULL`` vs. ``is_owner_bucket``): a same
        (owner, class) pair lands in different indexes, so the category and
        fallback kinds never collide.  A single commit of both rows raising
        no IntegrityError is the proof.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=category.id,
                name=category.display_name,
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Expense",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(LedgerAccount.account_id.is_(None))
                .filter_by(user_id=user_id, class_id=class_id)
                .count() == 2
            )

    def test_fallback_and_orphan_coexist(self, app, db, seed_user):
        """The fallback and an orphan of the SAME class coexist (H1 unit lock).

        Both are ``(account_id NULL, category_id NULL)`` and same class; the
        ``is_owner_bucket`` flag (True for the fallback, False for the orphan) is
        the only difference, and it keeps the orphan outside the
        ``WHERE is_owner_bucket`` singleton.  This is the unit-level proof of the
        property the end-to-end ``test_category_delete_with_fallback_present_does_not_collide``
        relies on: a commit of both, raising no IntegrityError.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Expense",
            ))
            _db.session.add(LedgerAccount(  # the orphan
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Family: Groceries",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                )
                .filter_by(user_id=user_id, class_id=class_id)
                .count() == 2
            )

    def test_multiple_orphans_same_class_coexist(self, app, db, seed_user):
        """Two orphans of the same class coexist -- orphans carry no unique.

        A user can retire two expense categories that both had posted
        history; each leaves an orphan (``is_owner_bucket`` False, NULL/NULL).
        Nothing constrains them, so both persist -- the "second retired
        category of a class" case the old NULL/NULL singleton wrongly forbade.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Family: Groceries",
            ))
            _db.session.add(LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Home: Rent",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                    LedgerAccount.is_owner_bucket.is_(False),
                )
                .filter_by(user_id=user_id, class_id=class_id)
                .count() == 2
            )
