"""A linked row: its display name derives from the live account, and its foreign keys CASCADE on
account_id and RESTRICT on class_id.
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


class TestLinkedRowDisplayName:
    """A linked row's display name derives from the live account."""

    def test_name_null_and_derives_from_account_including_rename(
        self, app, db, seed_user,
    ):
        """``name`` is NULL; ``account.name`` supplies the label, live.

        The linked row stores no name of its own; the relationship reads
        ``account.name`` at render time.  After renaming the account, a
        fresh load of the ledger account reflects the new name -- proving
        the display label is never a stale snapshot.
        """
        with app.app_context():
            # The test plants the one linked row it reads with .one(): no
            # door pairs one since plan task plan#3133.
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Original Name",
                anchor_balance=Decimal("0.00"),
            )
            chart_row_or_plant(_db.session, account, "linked", "Asset")
            ledger_account = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=account.id)
                .one()
            )
            ledger_account_id = ledger_account.id
            assert ledger_account.name is None
            assert ledger_account.account.name == "Original Name"

            # Rename the real account, then reload the ledger account from
            # scratch so the relationship re-reads the live name.
            account.name = "Renamed Checking"
            _db.session.commit()
            _db.session.expire_all()

            reloaded = _db.session.get(LedgerAccount, ledger_account_id)
            assert reloaded.name is None
            assert reloaded.account.name == "Renamed Checking"


class TestForeignKeyActions:
    """CASCADE on ``account_id``; RESTRICT on ``class_id``."""

    def test_empty_account_delete_cascades_to_ledger_account(
        self, app, db, seed_user,
    ):
        """Deleting an empty account removes its paired ledger account.

        The ``account_id`` FK is ``ON DELETE CASCADE`` and the
        Account->LedgerAccount relationship is one-directional, so an ORM
        delete of the account emits the account DELETE and the database
        cascade removes the ledger row -- no orphan, no ORM SET-NULL
        attempt.  The delete door reaches the cascade for an account holding
        only balance records too (ruling plan#2784), not only an empty one.
        """
        with app.app_context():
            # $0 anchor: an account holding nothing.  A non-zero opening kept
            # an account off the hard-delete path until ruling plan#2784.
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Empty Savings",
                anchor_balance=Decimal("0.00"),
            )
            ledger_account_id = chart_row_or_plant(
                _db.session, account, "linked", "Asset",
            )

            _db.session.delete(account)
            _db.session.commit()

            assert _db.session.get(LedgerAccount, ledger_account_id) is None

    def test_referenced_class_delete_restricted(
        self, app, db, seed_user,
    ):
        """Deleting a referenced ``ledger_account_classes`` row is refused.

        The ``class_id`` FK is ``ON DELETE RESTRICT`` because the seeded
        classes are non-removable invariants: a successful delete would
        strand every ledger account in that class.  An unlinked Equity row
        references the Equity class; the raw DELETE on that class row must
        raise.
        """
        with app.app_context():
            equity_class_id = _class_id(LedgerAccountClassEnum.EQUITY)
            _db.session.add(LedgerAccount(
                user_id=seed_user["user"].id,
                class_id=equity_class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                account_id=None,
                name="Retained earnings (equity)",
            ))
            _db.session.commit()

            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "DELETE FROM ref.ledger_account_classes WHERE id = :c"
                ), {"c": equity_class_id})
                _db.session.commit()
            _db.session.rollback()

    def test_category_delete_sets_category_id_null_keeps_name(
        self, app, db, seed_user,
    ):
        """Deleting a category SET-NULLs the ledger row's ``category_id``.

        A category ledger account accumulates immutable postings, so it must
        survive its budgeting category's deletion: the ``category_id`` FK is
        ``ON DELETE SET NULL`` (mirroring ``transactions.category_id``) and
        the ``name`` snapshot is retained, leaving the row identifiable as an
        **orphan** -- ``is_owner_bucket`` stays False (it is NOT promoted to the
        Uncategorized fallback), which is what lets it coexist with the
        fallback (see ``test_category_delete_with_fallback_present_does_not_collide``).
        The ``category`` relationship then resolves to None.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            category = seed_user["categories"]["Groceries"]
            category_id = category.id
            snapshot = category.display_name  # "Family: Groceries"
            ledger_account = LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None,
                category_id=category_id,
                name=snapshot,
            )
            _db.session.add(ledger_account)
            _db.session.commit()
            ledger_account_id = ledger_account.id
            assert ledger_account.category is not None
            assert ledger_account.category.id == category_id

            # Delete the budgeting category at the storage tier, then reload
            # the ledger account from scratch.
            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": category_id})
            _db.session.commit()
            _db.session.expire_all()

            reloaded = _db.session.get(LedgerAccount, ledger_account_id)
            assert reloaded is not None, (
                "the posted-to ledger account must survive a category delete"
            )
            assert reloaded.category_id is None, (
                "the category back-link must be SET NULL, not cascaded"
            )
            assert reloaded.is_owner_bucket is False, (
                "a deleted-category row becomes an orphan, NOT the fallback"
            )
            assert reloaded.name == snapshot, (
                "the name snapshot must survive so the orphaned row stays "
                "identifiable"
            )
            assert reloaded.category is None

    def test_category_delete_with_fallback_present_does_not_collide(
        self, app, db, seed_user,
    ):
        """Deleting a category does NOT collide with an existing fallback (H1).

        Regression lock for the design defect the adversarial review caught:
        a category ledger account and the Uncategorized fallback of the same
        class both exist; deleting the budget category SET-NULLs the category
        row into the ``(account_id NULL, category_id NULL)`` space.  Because
        the orphan keeps ``is_owner_bucket`` False, it does NOT collide with the
        fallback (``is_owner_bucket`` True) on
        ``uq_ledger_accounts_owner_bucket`` -- so the category delete
        SUCCEEDS, the orphan and the fallback both survive, and the fallback
        singleton is untouched.  Without the ``is_owner_bucket`` discriminator
        this delete raised an IntegrityError (a 500 on the ordinary "delete a
        category that has posted history" action).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            class_id = _class_id(LedgerAccountClassEnum.EXPENSE)
            category = seed_user["categories"]["Groceries"]
            category_id = category.id
            # The Uncategorized-Expense fallback ...
            fallback = LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Expense",
            )
            # ... and a category ledger account of the SAME class.
            category_row = LedgerAccount(
                user_id=user_id, class_id=class_id,
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=category_id, is_owner_bucket=False,
                name=category.display_name,
            )
            _db.session.add_all([fallback, category_row])
            _db.session.commit()
            fallback_id, category_row_id = fallback.id, category_row.id

            # Delete the budget category -> SET NULL on the category row.
            # MUST NOT raise (this is the exact H1 crash).
            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": category_id})
            _db.session.commit()
            _db.session.expire_all()

            orphan = _db.session.get(LedgerAccount, category_row_id)
            assert orphan is not None and orphan.category_id is None
            assert orphan.is_owner_bucket is False
            surviving_fallback = _db.session.get(LedgerAccount, fallback_id)
            assert surviving_fallback is not None
            assert surviving_fallback.is_owner_bucket is True
            # Both coexist in the NULL/NULL space, told apart by the flag.
            null_null = (
                _db.session.query(LedgerAccount)
                .filter(
                    LedgerAccount.account_id.is_(None),
                    LedgerAccount.category_id.is_(None),
                )
                .filter_by(user_id=user_id, class_id=class_id)
                .count()
            )
            assert null_null == 2
