"""The create door, and what a companion user may and may not do through the entry doors."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from app.services import entry_service
from app.services.auth_service import hash_password
from app.exceptions import NotFoundError, ValidationError
from app import ref_cache
from app.enums import RoleEnum
from app.services import status_seam
from tests._test_helpers import (
    typed,
    generate_row_of,
    make_expense_template,
    make_income_template,
    purchases_of,
    record_paydays_across_a_hole,
    rhythm_of,
    settlement_if_settling,
)


# ── CRUD Tests ────────────────────────────────────────────────────


class TestCreateEntry:
    """Tests for entry_service.create_entry()."""

    def test_create_entry_basic(self, app, db, seed_user, seed_entry_template):
        """Create a debit entry with valid inputs. Verifies all fields persisted."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("50.00")),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            assert entry.id is not None
            assert entry.transaction_id == txn.id
            assert entry.user_id == user.id
            assert entry.amount == Decimal("50.00")
            assert entry.description == "Kroger"
            assert entry.purchased_on == date(2026, 1, 5)
            assert entry.is_credit is False

    def test_create_entry_credit(self, app, db, seed_user, seed_entry_template):
        """Create a credit entry. is_credit flag is persisted."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("75.00")),
                    description="Amazon order",
                    purchased_on=date(2026, 1, 6),
                    is_credit=True,
                ),
            )

            assert entry.is_credit is True
            assert entry.amount == Decimal("75.00")

    def test_create_entry_returns_flushed_id(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Created entry has a database-assigned id immediately (flushed)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("10.00")),
                    description="Test",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            assert isinstance(entry.id, int)
            assert entry.id > 0

    def test_create_entry_rejects_non_tracking_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Reject entry on a transaction whose template has track=False."""
        with app.app_context():
            # A definition with tracking disabled, and its row.
            template = make_expense_template(
                db.session, seed_user, amount="1500.00",
                name="Rent", category_key="Rent", is_envelope=False,
            )
            txn = generate_row_of(template, seed_periods[0])

            with pytest.raises(ValidationError, match="does not support"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    # ``test_create_entry_rejects_a_transfer_shadow`` stood here until plan
    # step balance:X-bi-6-4d-2 (ruling **R-BAL300**, developer 2026-10-15:
    # "Delete all 27").  It added a purchase to a transfer's expense SHADOW
    # row, which names no definition, so ``tracks_purchases`` answered
    # ``False`` (ruling R-BAL73) and the envelope guard refused it.  No shadow
    # can exist now: no transaction has a transfer column since plan step
    # X-bi-6-5, and ``ck_transactions_names_no_transfer`` refused a row naming
    # one until then (ruling R-BAL258, pinned once, below that drop, by
    # ``TestTheUpgrade::test_the_database_refuses_a_twin`` in
    # ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``).
    # The envelope guard's refusal of a row that tracks no purchases is the
    # case above's, and ``tracks_purchases`` still answers ``False`` for the
    # one link-less row left, a card payback.

    def test_create_entry_rejects_income(
        self, app, db, seed_user, seed_periods,
    ):
        """Reject entry on an income transaction (even with tracking enabled)."""
        with app.app_context():
            # An INCOME definition flagged for tracking (the shape the form
            # refuses and the model admits), and its row.
            template = make_income_template(
                db.session, seed_user, amount="3000.00",
                name="Salary", category_key="Salary", is_envelope=True,
            )
            txn = generate_row_of(template, seed_periods[0])

            with pytest.raises(ValidationError, match="income"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_create_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user,
        seed_entry_template,
    ):
        """Reject entry when user does not own the transaction (NotFoundError, not 403)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            other_user = seed_second_user["user"]

            with pytest.raises(NotFoundError):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=other_user.id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_create_entry_rejects_nonexistent_transaction(
        self, app, db, seed_user,
    ):
        """NotFoundError when transaction_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.create_entry(
                    transaction_id=999999,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_create_entry_rejects_cancelled_transaction(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Cannot add entries to a cancelled transaction."""
        with app.app_context():
            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)
            cancelled = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            txn.status_id = cancelled.id
            db.session.flush()

            with pytest.raises(ValidationError, match="cancelled"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=user_id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_create_entry_rejects_credit_status_transaction(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Cannot add entries to a transaction with legacy Credit status.

        Entry-capable transactions handle credit at the entry level (OQ-10).
        If Credit status is somehow set, entries should be blocked.
        """
        with app.app_context():
            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)
            credit = db.session.query(Status).filter_by(name="Credit").one()
            txn.status_id = credit.id
            db.session.flush()

            with pytest.raises(ValidationError, match="Credit status"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=user_id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Test",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_create_entry_on_done_transaction_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """A Paid (DONE) transaction refuses a late-posting purchase.

        **This asserted the opposite until plan step X-au-c3**, on scope doc
        section 4.2: *"If entries are added to a transaction already in Paid
        status, the actual amount should update to reflect the new sum."*  That
        rule is withdrawn (developer ruling, 2026-08-17).  It re-priced a
        settled row from a PARTIAL record of its purchases -- a single
        back-filled ``$42.50`` against a row closed at its estimate -- which
        moves money in the optimistic direction with no human act, and
        double-counts against the leftover carry-forward has already rolled into
        the next period.  A forgotten purchase belongs in the period that now
        holds the money, or in this row once it is put back to Projected.
        """
        with app.app_context():
            txn_id = seed_entry_template["transaction"].id
            user_id = seed_user["user"].id

            # Reload transaction in this session context.
            txn = db.session.get(Transaction, txn_id)
            done = db.session.query(Status).filter_by(name="Paid").one()
            # Through the seam, which writes the settle day in the same call --
            # a bare status assign leaves the row settled-but-undated, which
            # every reader now refuses (plan step X-f1).
            status_seam.apply_status_change(txn, done.id, settlement=settlement_if_settling(txn, done.id))
            db.session.flush()

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=user_id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("42.50")),
                        description="Late posting purchase",
                        purchased_on=date(2026, 1, 10),
                    ),
                )

            # No PURCHASE was recorded; the row keeps only the seam's covering movement (X-bi-3a).
            assert purchases_of(txn_id) == []

    def test_create_entry_boundary_minimum_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Minimum valid amount (Decimal('0.01')) succeeds."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=seed_entry_template["transaction"].id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("0.01")),
                    description="Penny item",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            assert entry.amount == Decimal("0.01")

    def test_create_entry_boundary_large_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Large amount within Numeric(12,2) precision succeeds."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=seed_entry_template["transaction"].id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("9999999999.99")),
                    description="Expensive item",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            assert entry.amount == Decimal("9999999999.99")

    def test_create_entry_description_at_max_length(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Description at exactly 200 characters succeeds."""
        with app.app_context():
            desc = "A" * 200
            entry = entry_service.create_entry(
                transaction_id=seed_entry_template["transaction"].id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("10.00")),
                    description=desc,
                    purchased_on=date(2026, 1, 5),
                ),
            )
            assert len(entry.description) == 200


# ── Companion Ownership Tests ─────────────────────────────────────


class TestCompanionAccess:
    """Tests for companion user access to entry operations."""

    def test_create_entry_as_companion_on_linked_owner(
        self, app, db, seed_user, seed_entry_template, seed_companion,
    ):
        """Companion can create entries on their linked owner's transactions."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            companion = seed_companion["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=companion.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("35.00")),
                    description="Companion purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            assert entry.id is not None
            assert entry.user_id == companion.id

    def test_create_entry_as_companion_on_different_owner(
        self, app, db, seed_second_user, seed_entry_template, seed_companion,
    ):
        """Companion cannot create entries on transactions owned by a different user."""
        with app.app_context():
            # seed_entry_template belongs to seed_user.
            # seed_companion is linked to seed_user.
            # seed_second_user owns different data.
            # A row owned by seed_second_user: their definition, on their
            # own calendar, written by the engine.
            periods = record_paydays_across_a_hole(
                user_id=seed_second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            template = make_expense_template(
                db.session, seed_second_user, amount="400.00",
                name="Other Groceries", category_key="Groceries",
                is_envelope=True,
            )
            txn = generate_row_of(template, periods[0])

            companion = seed_companion["user"]
            with pytest.raises(NotFoundError):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=companion.id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Unauthorized",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

    def test_resolve_owner_id_for_owner(self, app, db, seed_user):
        """Owner user resolves to their own id."""
        with app.app_context():
            owner = seed_user["user"]
            result = entry_service._resolve_owner_id(owner.id)
            assert result == owner.id

    def test_resolve_owner_id_for_companion(
        self, app, db, seed_user, seed_companion,
    ):
        """Companion user resolves to linked_owner_id."""
        with app.app_context():
            companion = seed_companion["user"]
            owner = seed_user["user"]
            result = entry_service._resolve_owner_id(companion.id)
            assert result == owner.id

    def test_resolve_owner_id_nonexistent_user(self, app, db):
        """NotFoundError for a user_id that does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError, match="User not found"):
                entry_service._resolve_owner_id(999999)

    def test_resolve_owner_id_companion_no_linked_owner(
        self, app, db, seed_user,
    ):
        """ValidationError when companion has linked_owner_id=None.

        This is a data integrity issue -- companion accounts must always
        have a linked owner.
        """
        with app.app_context():
            companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
            broken_companion = User(
                email="broken@shekel.local",
                password_hash=hash_password("test"),
                display_name="Broken Companion",
                role_id=companion_role_id,
                linked_owner_id=None,
            )
            db.session.add(broken_companion)
            db.session.flush()
            settings = UserSettings(user_id=broken_companion.id)
            db.session.add(settings)
            db.session.flush()

            with pytest.raises(ValidationError, match="no linked owner"):
                entry_service._resolve_owner_id(broken_companion.id)
