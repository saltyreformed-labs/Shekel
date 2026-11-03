"""
Shekel Budget App -- Commit 1 Schema Tests

Tests for the data model additions in Section 9 Commit 1:
  - TransactionEntry model (table, constraints, relationships, cascades)
  - TransactionTemplate flag columns (is_envelope, companion_visible)
  - User role columns (role_id, linked_owner_id)
  - UserRole ref table and ref_cache integration
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app.extensions import db
from app.models.ref import TransactionType, UserRole
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User
from app.services.auth_service import hash_password
from tests._test_helpers import figure_source_columns, load_migration_module, one_off_row_of

_REFUND_MIGRATION = load_migration_module(
    "b8e4c1f7a903_a_refund_is_a_negative_purchase.py",
)


# ── Helpers ────────────────────────────────────────────────────────────


def _make_entry(txn, user, amount, description, **kwargs):
    """Create and flush a TransactionEntry with the given fields."""
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=user.id,
        amount=amount,
        description=description,
        **kwargs,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _make_txn(seed_user, seed_periods, estimated_amount=Decimal("500.00")):
    """Create a projected expense transaction in the first period."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    txn = one_off_row_of(
        seed_periods[0],
        name="Test Expense",
        amount=estimated_amount,
        user_id=seed_periods[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.flush()
    return txn


# ── TransactionEntry Tests ─────────────────────────────────────────────


class TestTransactionEntryRowDeleteIsRefused:
    """Verify the transaction_id FK REFUSES a row's delete while it holds entries.

    It CASCADED until plan step ``credit_card:CC-5-4a-4`` (migration
    ``c4a4e7d1b9f2``, rulings **R-CC54** / **R-CC64**): a row's delete took
    its purchases with it, and the template, account and pay-period doors
    destroyed recorded money that way (finding **CC-363**).  Re-expressed from
    the cascade's own test under rule 5, developer-confirmed 2026-09-23.
    """

    def test_a_row_holding_entries_cannot_be_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting a transaction holding purchases is refused; both purchases survive.

        ``fk_transaction_entries_transaction_id`` is NO ACTION and the
        ``entries`` relationship carries no delete cascade, so the parent's
        own ``DELETE`` meets the key.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            user = seed_user["user"]
            _make_entry(txn, user, Decimal("25.00"), "Store A")
            _make_entry(txn, user, Decimal("30.00"), "Store B")
            db.session.commit()

            txn_id = txn.id
            # Verify entries exist before delete.
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id
            ).count() == 2

            db.session.delete(txn)
            with pytest.raises(sqlalchemy.exc.IntegrityError) as refused:
                db.session.commit()
            db.session.rollback()

            assert "fk_transaction_entries_transaction_id" in str(refused.value)
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id
            ).count() == 2


class TestTransactionEntryAmountCheck:
    """Verify the CHECK(amount <> 0) constraint on transaction_entries.

    **It was ``amount > 0`` until ruling bank_import:R-II** (migration
    ``b8e4c1f7a903``, developer 2026-08-31).  A merchant credit files as a
    NEGATIVE purchase against the envelope its merchant rule names, so the
    negative case below asserts ACCEPTANCE where it used to assert refusal.
    The zero case is unchanged and is the surviving half: a purchase worth
    nothing is not a purchase.
    """

    def test_transaction_entry_amount_zero_rejected(self, app, db, seed_user, seed_periods):
        """Creating an entry with amount=0 raises IntegrityError.

        The ck_transaction_entries_positive_amount constraint requires
        amount <> 0, so zero is invalid.  **This half of the rule SURVIVED
        R-II** -- what a purchase may not be is nothing, and the widening to
        ``<> 0`` did not touch it.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("0.00"),
                description="Zero amount",
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_transaction_entry_amount_negative_accepted(self, app, db, seed_user, seed_periods):
        """A NEGATIVE purchase is a refund, and the table accepts it.

        Ruling **bank_import:R-II**: a merchant credit files as a negative
        purchase against the envelope its merchant rule names, rather than as
        income under a spending category.  **This test asserted the opposite
        until 2026-08-31**, and inverting it is the developer's confirmed
        change of behaviour rather than a test bent to fit code.

        Asserts the stored value round-trips, not merely that the flush
        survived: a constraint relaxed to ``<> 0`` that silently coerced the
        sign would pass a bare "no IntegrityError" assertion.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("-28.29"),
                description="Amazon refund",
            )
            db.session.add(entry)
            db.session.flush()
            db.session.commit()

            assert entry.id is not None
            stored = db.session.get(TransactionEntry, entry.id)
            assert stored.amount == Decimal("-28.29")

    def test_transaction_entry_amount_positive_accepted(self, app, db, seed_user, seed_periods):
        """Creating an entry with a small positive amount succeeds.

        amount=0.01 is the smallest valid Numeric(12,2) value above zero.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"], Decimal("0.01"), "Penny purchase",
            )
            db.session.commit()
            assert entry.id is not None
            assert entry.amount == Decimal("0.01")


class TestTheDowngradeRefusesANegativePurchase:
    """Migration ``b8e4c1f7a903``'s only non-DDL logic, driven directly.

    ``refuse_negative_purchases`` is module-level so a test can drive it, which
    is the pattern this chain's guarded revisions use (``e4b8a71c0f36``,
    ``a9d3c15e7f42``) and the rule *a guard nothing exercises is a guard nobody
    has seen work*.  Both precedents HAVE such a test; this one had none until
    plan step ``bank_import:X-gj-2b``'s own adversarial review said so.

    **What it protects is a REFUND.**  Restoring ``amount > 0`` makes a
    negative purchase unrepresentable, so the downgrade is value-lossless only
    where none exists.  Left to PostgreSQL the same state arrives as a bare
    constraint violation naming no row; the guard names the rows and the
    diagnostic SELECT, which is the whole difference for an operator running it
    mid-deploy.

    Definition of Done item 7 asks for both directions.  The DDL halves run on
    every test-template rebuild -- ``scripts/build_test_template.py`` replays
    the whole Alembic chain rather than calling ``create_all`` -- so what no
    rebuild can reach is the REFUSAL, because a chain that has just widened the
    CHECK has no negative row in it yet.
    """

    def test_it_passes_when_no_purchase_is_negative(
        self, app, db, seed_user, seed_periods,
    ):
        """The state an ordinary revert is run in, so it is not refused.

        Returns ``None`` rather than raising: the assertion is the ABSENCE of a
        refusal, and the case below is what gives that meaning -- a guard that
        refused everything would pass this one alone.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            _make_entry(
                txn, seed_user["user"], Decimal("42.00"), "An ordinary swipe",
            )

            assert _REFUND_MIGRATION.refuse_negative_purchases(
                db.session.connection(),
            ) is None

    def test_it_REFUSES_while_a_refund_is_stored_and_NAMES_it(
        self, app, db, seed_user, seed_periods,
    ):
        """The arm that keeps *no such rows exist* measured rather than assumed.

        Asserts the row's OWN id is in the message, not merely that something
        raised: an operator hitting this has only the message to work from, and
        a refusal that named no row would leave them exactly where
        PostgreSQL's own violation would have.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            refund = _make_entry(
                txn, seed_user["user"], Decimal("-28.29"), "Amazon refund",
            )

            with pytest.raises(RuntimeError, match=str(refund.id)):
                _REFUND_MIGRATION.refuse_negative_purchases(
                    db.session.connection(),
                )


# ── Template Flag Tests ────────────────────────────────────────────────


class TestTemplateFlagsDefault:
    """Verify default values for the new template boolean columns."""

    def test_template_flags_default_false(self, app, db, seed_user):
        """New templates default to is_envelope=False and companion_visible=False.

        Both columns have server_default='false' and Python default=False,
        so templates created without explicit values must have both False.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Default Template",
            )
            db.session.add(template)
            db.session.commit()

            # Re-read from database to verify server_default took effect.
            db.session.expire(template)
            assert template.is_envelope is False
            assert template.companion_visible is False

    def test_template_flags_explicit_true(self, app, db, seed_user):
        """Templates can be created with tracking and visibility flags set to True.

        Verifies the columns accept True values and persist them correctly.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Tracking Template",
                is_envelope=True,
                companion_visible=True,
            )
            db.session.add(template)
            db.session.commit()

            db.session.expire(template)
            assert template.is_envelope is True
            assert template.companion_visible is True


# ── User Role Tests ────────────────────────────────────────────────────


class TestUserRoleDefault:
    """Verify user role_id defaults and linked_owner_id behavior."""

    def test_user_role_default_owner(self, app, db, seed_user):
        """Users created without explicit role_id default to the owner role.

        The server_default='1' maps to the 'owner' row in ref.user_roles.
        The seed_user fixture creates a User without specifying role_id.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Re-query from database to pick up server_default.
            user = db.session.get(User, seed_user["user"].id)
            assert user.role_id == ref_cache.role_id(RoleEnum.OWNER)

    def test_user_linked_owner_nullable(self, app, db, seed_user):
        """Owner users have linked_owner_id=None by default.

        The linked_owner_id column is nullable and has no default, so
        regular owner users should have None.
        """
        with app.app_context():
            # Re-query from database to verify persisted value.
            user = db.session.get(User, seed_user["user"].id)
            assert user.linked_owner_id is None

    def test_companion_linked_to_owner(self, app, db, seed_companion, seed_user):
        """Companion users have linked_owner_id pointing to the owner.

        The seed_companion fixture creates a companion with role_id=2
        and linked_owner_id=seed_user.id.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            companion = seed_companion["user"]
            assert companion.role_id == ref_cache.role_id(RoleEnum.COMPANION)
            assert companion.linked_owner_id == seed_user["user"].id


class TestUserRoleForeignKey:
    """Verify the role_id FK constraint enforcement."""

    def test_user_role_fk_constraint(self, app, db):
        """Setting role_id to a nonexistent value raises IntegrityError.

        The FK from auth.users.role_id to ref.user_roles.id must reject
        invalid references.
        """
        with app.app_context():
            user = User(
                email="badrole@shekel.local",
                password_hash=hash_password("testpass"),
                display_name="Bad Role User",
                role_id=999,
            )
            db.session.add(user)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.flush()
            db.session.rollback()


# ── Ref Table and Cache Tests ──────────────────────────────────────────


class TestUserRoleRefTable:
    """Verify the user_roles ref table is properly seeded."""

    def test_user_role_ref_table_seeded(self, app, db):
        """The ref.user_roles table contains exactly 2 rows: owner and companion.

        These are seeded at app startup and must be present for
        ref_cache.role_id() to function.
        """
        with app.app_context():
            roles = db.session.query(UserRole).order_by(UserRole.id).all()
            assert len(roles) == 2
            assert roles[0].name == "owner"
            assert roles[0].id == 1
            assert roles[1].name == "companion"
            assert roles[1].id == 2

    def test_role_enum_cache_loaded(self, app, db):
        """ref_cache.role_id() returns integer IDs for both RoleEnum members.

        The cache is initialized during app setup.  Both OWNER and
        COMPANION must resolve to valid integer PKs from ref.user_roles.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            owner_id = ref_cache.role_id(RoleEnum.OWNER)
            companion_id = ref_cache.role_id(RoleEnum.COMPANION)
            assert isinstance(owner_id, int)
            assert isinstance(companion_id, int)
            assert owner_id != companion_id


# ── Entry-User Cascade Tests ──────────────────────────────────────────


class TestEntryUserCascadeDelete:
    """Verify CASCADE behavior on the user_id FK."""

    def test_entry_user_cascade_delete(self, app, db, seed_user, seed_periods):
        """Deleting the user who created an entry cascades to delete the entry.

        The transaction_entries.user_id FK is ON DELETE CASCADE.  When
        a user is deleted, all their entries must be removed.

        We create a second user to own the entry so that deleting them
        does not also cascade-delete the parent transaction (which belongs
        to seed_user).
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)

            # Create a separate user who will own the entry.
            entry_user = User(
                email="entryuser@shekel.local",
                password_hash=hash_password("entrypass"),
                display_name="Entry User",
            )
            db.session.add(entry_user)
            db.session.flush()

            entry = _make_entry(
                txn, entry_user, Decimal("15.00"), "Entry by doomed user",
            )
            db.session.commit()

            entry_id = entry.id
            assert db.session.get(TransactionEntry, entry_id) is not None

            db.session.delete(entry_user)
            db.session.commit()

            # Entry must be gone after user deletion.
            assert db.session.get(TransactionEntry, entry_id) is None


# ── Transaction-Entries Relationship Tests ─────────────────────────────


class TestTransactionEntriesRelationship:
    """Verify the Transaction.entries relationship behavior."""

    def test_transaction_entries_relationship(self, app, db, seed_user, seed_periods):
        """Accessing txn.entries returns entries ordered by purchased_on.

        Creates 3 entries with different dates on the same transaction.
        The relationship's order_by=TransactionEntry.purchased_on must
        return them in chronological order.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            user = seed_user["user"]

            # Create entries out of chronological order.
            _make_entry(
                txn, user, Decimal("30.00"), "Middle",
                purchased_on=date(2026, 1, 5),
            )
            _make_entry(
                txn, user, Decimal("10.00"), "First",
                purchased_on=date(2026, 1, 3),
            )
            _make_entry(
                txn, user, Decimal("20.00"), "Last",
                purchased_on=date(2026, 1, 7),
            )
            db.session.commit()

            # Expire to force reload from database.
            db.session.expire(txn)
            entries = txn.entries

            assert len(entries) == 3
            assert entries[0].description == "First"
            assert entries[1].description == "Middle"
            assert entries[2].description == "Last"
            assert entries[0].purchased_on < entries[1].purchased_on < entries[2].purchased_on

    def test_transaction_entries_empty_by_default(self, app, db, seed_user, seed_periods):
        """A transaction with no entries returns an empty list via the relationship.

        Verifies that accessing txn.entries does not error when no
        child entries exist.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            db.session.commit()
            db.session.expire(txn)
            assert txn.entries == []


# ── Edge Case Tests ────────────────────────────────────────────────────


class TestTransactionEntryEdgeCases:
    """Edge case tests beyond the plan baseline."""

    def test_entry_large_amount_accepted(self, app, db, seed_user, seed_periods):
        """Numeric(12,2) supports amounts up to 9999999999.99.

        The maximum value for Numeric(12,2) is 10 digits before the
        decimal + 2 after.  Verify the upper bound is accepted.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"],
                Decimal("9999999999.99"), "Big purchase",
            )
            db.session.commit()
            db.session.expire(entry)
            assert entry.amount == Decimal("9999999999.99")

    def test_entry_description_max_length(self, app, db, seed_user, seed_periods):
        """A 200-character description is accepted (VARCHAR(200) limit).

        Verifies the column stores the full 200-character string.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            desc = "A" * 200
            entry = _make_entry(
                txn, seed_user["user"], Decimal("5.00"), desc,
            )
            db.session.commit()
            db.session.expire(entry)
            assert len(entry.description) == 200

    def test_entry_description_over_max_rejected(self, app, db, seed_user, seed_periods):
        """A 201-character description is rejected by the database.

        VARCHAR(200) enforces a maximum length at the database level.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("5.00"),
                description="A" * 201,
            )
            db.session.add(entry)
            with pytest.raises(sqlalchemy.exc.DataError):
                db.session.flush()
            db.session.rollback()

    def test_entry_is_credit_default_false(self, app, db, seed_user, seed_periods):
        """Entries default to is_credit=False when not specified.

        Both the Python default and server_default are False.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"], Decimal("10.00"), "Cash purchase",
            )
            db.session.commit()
            db.session.expire(entry)
            assert entry.is_credit is False

    def test_entry_credit_payback_set_null_on_delete(self, app, db, seed_user, seed_periods):
        """Deleting the payback transaction sets credit_payback_id to NULL.

        The FK on credit_payback_id is ON DELETE SET NULL.  When the
        referenced payback transaction is deleted, the entry's
        credit_payback_id must become NULL rather than cascading.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            payback_txn = _make_txn(seed_user, seed_periods, Decimal("10.00"))

            entry = _make_entry(
                txn, seed_user["user"], Decimal("10.00"), "CC purchase",
                is_credit=True, credit_payback_id=payback_txn.id,
            )
            db.session.commit()

            entry_id = entry.id
            assert entry.credit_payback_id == payback_txn.id

            db.session.delete(payback_txn)
            db.session.commit()

            refreshed = db.session.get(TransactionEntry, entry_id)
            assert refreshed is not None
            assert refreshed.credit_payback_id is None

    @pytest.mark.server_clock
    def test_purchased_on_server_default(self, app, db, seed_user, seed_periods):
        """Entry date defaults to CURRENT_DATE when not explicitly set.

        The server_default=CURRENT_DATE uses the database server's
        timezone, so we verify the date is within 1 day of Python's
        date.today() to account for UTC/local timezone differences.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("12.50"),
                description="No date specified",
            )
            db.session.add(entry)
            db.session.commit()

            # Re-query to pick up server_default.
            refreshed = db.session.get(TransactionEntry, entry.id)
            assert isinstance(refreshed.purchased_on, date)
            delta = abs((refreshed.purchased_on - date.today()).days)
            assert delta <= 1, (
                f"purchased_on {refreshed.purchased_on} is more than 1 day "
                f"from today {date.today()}"
            )

    def test_multiple_entries_on_same_transaction(self, app, db, seed_user, seed_periods):
        """Multiple entries can be created on the same transaction.

        Verifies no unique constraint prevents multiple entries per
        transaction, and the relationship correctly returns all of them.
        """
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            user = seed_user["user"]

            for i in range(5):
                _make_entry(
                    txn, user, Decimal("10.00"), f"Entry {i}",
                    purchased_on=date(2026, 1, 3 + i),
                )
            db.session.commit()

            db.session.expire(txn)
            assert len(txn.entries) == 5

    def test_user_role_relationship_joined(self, app, db, seed_user):
        """User.role relationship loads the UserRole via lazy='joined'.

        Verifies that user.role returns the correct UserRole object
        with the expected name attribute.
        """
        with app.app_context():
            # Re-query to get a fresh instance attached to the session.
            user = db.session.get(User, seed_user["user"].id)
            assert user.role is not None
            assert user.role.name == "owner"

    def test_entry_repr(self, app, db, seed_user, seed_periods):
        """TransactionEntry.__repr__ includes description, amount, and id."""
        with app.app_context():
            txn = _make_txn(seed_user, seed_periods)
            entry = _make_entry(
                txn, seed_user["user"], Decimal("42.50"), "Kroger",
            )
            db.session.commit()
            result = repr(entry)
            assert "Kroger" in result
            assert "42.50" in result
