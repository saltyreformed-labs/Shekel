"""The shape CHECKs: a row carries a name or an account, never both an account and a category link,
and is_owner_bucket only on a row with neither.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.ledger_account import LedgerAccount
from tests._test_helpers import create_account_of_type

from ._helpers import _class_id, _kind_id


class TestNamePresentCheck:
    """``ck_ledger_accounts_name_present`` -- a row carries name or account."""

    def test_null_name_and_null_account_rejected(
        self, app, db, seed_user,
    ):
        """A row with neither ``name`` nor ``account_id`` trips the CHECK.

        The display rule COALESCE(account.name, ledger_account.name) would
        resolve to NULL for such a row, so the storage tier refuses it.
        """
        with app.app_context():
            with pytest.raises(IntegrityError):
                _db.session.add(LedgerAccount(
                    user_id=seed_user["user"].id,
                    class_id=_class_id(LedgerAccountClassEnum.EQUITY),
                    kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                    account_id=None,
                    name=None,
                ))
                _db.session.commit()
            _db.session.rollback()

    def test_category_row_without_name_rejected(self, app, db, seed_user):
        """A category row (account_id NULL) with NULL name still trips the CHECK.

        Setting ``category_id`` does NOT satisfy
        ``ck_ledger_accounts_name_present`` (which requires ``name`` or
        ``account_id``), so a category / fallback / orphan row must always
        carry the snapshot label the display rule reads.  Proves the CHECK is
        not bypassed by the new ``category_id`` link, and pins the constraint
        name so a future schema change can't let this pass for another reason.
        """
        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=seed_user["user"].id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                    account_id=None,
                    category_id=seed_user["categories"]["Groceries"].id,
                    name=None,
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_name_present" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()


class TestAccountOrCategoryExclusiveCheck:
    """``ck_ledger_accounts_account_or_category_null`` -- never both links.

    A ledger account is EITHER a linked real-account mirror OR a category
    bucket, never both (both-NULL is the legitimate uncategorized kind).
    The CHECK storage-enforces the linked/category/uncategorized partition
    so no writer bug can mint a both-set row -- one that would carry a
    non-NULL ``account_id`` and so fall OUTSIDE
    ``uq_ledger_accounts_category`` (whose predicate requires ``account_id
    IS NULL``), silently escaping the category uniqueness guarantee.
    """

    def test_both_account_and_category_set_rejected(self, app, db, seed_user):
        """A row with BOTH account_id and category_id set trips the CHECK.

        The account carries no linked row (no door pairs one since plan task
        plan#3133), so the only constraint a both-set row can violate is the
        partition CHECK, not ``uq_ledger_accounts_account_kind`` -- pinning
        the CHECK as the surface regardless of constraint-evaluation order.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Both Links",
                anchor_balance=Decimal("0.00"),
            )
            category = seed_user["categories"]["Groceries"]
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    kind_id=_kind_id(LedgerAccountKindEnum.LINKED),
                    account_id=account.id,
                    category_id=category.id,
                    name=category.display_name,
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_account_or_category_null" in str(
                excinfo.value
            ), str(excinfo.value)
            _db.session.rollback()


class TestFallbackShapeCheck:
    """``ck_ledger_accounts_owner_bucket_shape`` -- is_owner_bucket only on NULL/NULL.

    ``is_owner_bucket`` may be True only on a row with neither a real account nor
    a category (the genuine Uncategorized bucket).  Forbidding it on any other
    shape keeps the flag a true discriminator, so the fallback singleton index
    (``WHERE is_owner_bucket``) cannot be subverted by a linked or category row
    flagged ``is_owner_bucket``.
    """

    def test_fallback_with_category_id_rejected(self, app, db, seed_user):
        """``is_owner_bucket`` True on a row that ALSO sets category_id is refused."""
        with app.app_context():
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=seed_user["user"].id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                    account_id=None,
                    category_id=seed_user["categories"]["Groceries"].id,
                    is_owner_bucket=True,
                    name="bad fallback",
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_owner_bucket_shape" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_fallback_with_account_id_rejected(self, app, db, seed_user):
        """``is_owner_bucket`` True on a row that ALSO links a real account is refused.

        The row is kind ``fallback``, so ``uq_ledger_accounts_account_kind``
        (keyed ``(account_id, kind_id)``) cannot fire on it whatever else the
        account holds: the only constraint a (linked AND is_owner_bucket) row
        can violate is the fallback-shape CHECK.  *It deleted the account's
        linked row first until plan task plan#3133, for a collision that could
        not happen; no door pairs one since.*
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Fallback Account",
                anchor_balance=Decimal("0.00"),
            )
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=account.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.ASSET),
                    kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                    account_id=account.id,
                    category_id=None,
                    is_owner_bucket=True,
                    name=None,
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_owner_bucket_shape" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()
