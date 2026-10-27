"""
Shekel Budget App -- Transaction Entry Service Tests

Comprehensive tests for the entry service CRUD operations,
ownership validation, computation functions, and edge cases.
Each test verifies exact Decimal values for financial correctness.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from marshmallow import ValidationError as MarshmallowValidationError

from app.extensions import db
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import entry_service, pay_period_write
from app.services.auth_service import hash_password
from app.schemas.validation import EntryCreateSchema, EntryUpdateSchema
from app.exceptions import NotFoundError, ValidationError
from app import ref_cache
from app.enums import RoleEnum, StatusEnum
from app.services import (
    cash_ledger,
    status_seam,
    transaction_service,
)
from app.services.row_valuation import purchases_total, settled_figure
from app.utils.dates import display_today
from tests._test_helpers import (
    typed,
    account_never_asserted,
    an_entered_day,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    make_income_template,
    purchases_of,
    reassert_balance_on,
    record_paydays_across_a_hole,
    rhythm_of,
    settle_day_columns,
    settle_instant_on,
    settlement_if_settling,
)
from tests._test_helpers import mark_purchase_settled


# ── Helper ────────────────────────────────────────────────────────


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Used by tests that need pre-existing entries without re-testing
    the full create_entry validation chain.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=transaction.id, account_id=transaction.account_id, owner_id=transaction.user_id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


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


# ── Update Tests ──────────────────────────────────────────────────


class TestUpdateEntry:
    """Tests for entry_service.update_entry()."""

    def test_update_entry_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update amount on an existing entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("75.00")),
            )

            assert updated.amount == Decimal("75.00")
            assert updated.id == entry.id

    def test_update_entry_credit_toggle(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Toggle is_credit from False to True."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], is_credit=False)

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, is_credit=True,
            )

            assert updated.is_credit is True

    def test_update_entry_description(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update description field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], description="Old")

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, description="New store",
            )

            assert updated.description == "New store"

    def test_update_entry_date(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update purchased_on field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            new_date = date(2026, 1, 10)
            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=new_date,
            )

            assert updated.purchased_on == new_date

    def test_update_entry_multiple_fields(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update multiple fields in one call."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id,
                figure=typed(Decimal("99.99")),
                description="Updated",
                is_credit=True,
            )

            assert updated.amount == Decimal("99.99")
            assert updated.description == "Updated"
            assert updated.is_credit is True

    def test_update_entry_empty_kwargs(
        self, app, db, seed_user, seed_entry_template,
    ):
        """ValidationError when no fields to update are provided."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(ValidationError, match="No fields to update"):
                entry_service.update_entry(entry.id, seed_user["user"].id)

    def test_update_entry_unknown_kwargs(
        self, app, db, seed_user, seed_entry_template,
    ):
        """ValidationError when unknown field names are passed."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(ValidationError, match="Cannot update fields"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    transaction_id=999,
                )

    def test_update_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when updating an entry the user does not own."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    entry.id, seed_second_user["user"].id,
                    figure=typed(Decimal("99.00")),
                )

    def test_update_entry_nonexistent(self, app, db, seed_user):
        """NotFoundError when entry_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    999999, seed_user["user"].id, figure=typed(Decimal("10.00")),
                )


# ── Future-date refusal (plan step X-c0, ruling R-M) ──────────────


class TestAFutureEntryDateIsRefused:
    """Both write doors refuse an entry dated after the user's today.

    Plan step **X-c0** (``docs/audits/balance_architecture/README.md``), ruling
    R-M.  An entry records a purchase that HAPPENED; a purchase not yet made is
    what the row's remaining budget already models.  The guard is what lets the
    reservation's ``as_of`` window be DELETED at plan step X-c2 instead of
    ruled, so these tests are the control that window's deletion rests on: if
    the refusal stops firing, an entry can be dated past a reader's now again
    and the two shipping surfaces (the calendar windows, the grid does not)
    have something to disagree about once more.

    The boundary is :func:`~app.utils.dates.display_today` -- the user's civil
    date -- so these tests derive their dates from it rather than from
    ``date.today()``, which is the server's UTC day and differs from it for
    part of every evening (CI runs UTC, the dev host runs Eastern).
    """

    def test_the_create_door_refuses_tomorrow(
        self, app, db, seed_user, seed_entry_template,
    ):
        """create_entry raises on a date one day past the user's today."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError) as exc_info:
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("150.00")),
                        description="Costco run I have not made",
                        purchased_on=tomorrow,
                    ),
                )

            # The message names BOTH dates: what was rejected and what the
            # boundary was, so the surface can show the user why.
            message = str(exc_info.value)
            assert tomorrow.isoformat() in message
            assert display_today().isoformat() in message

            # Nothing was written -- the refusal precedes the INSERT.
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_the_create_door_accepts_today(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The boundary is exclusive: the user's own today is allowed.

        The add form posts exactly this value, so a refusal here would make the
        app reject its own form.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            today = display_today()

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("42.87")),
                    description="Walmart",
                    purchased_on=today,
                ),
            )

            assert entry.purchased_on == today

    def test_the_create_door_accepts_a_backdated_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Backdating stays allowed -- the real data uses it.

        A purchase logged days after it happened, or dated into the previous
        pay period, is ordinary; only the FUTURE is refused.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            long_ago = display_today() - timedelta(days=45)

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("101.06")),
                    description="Walmart, logged late",
                    purchased_on=long_ago,
                ),
            )

            assert entry.purchased_on == long_ago

    def test_the_update_door_refuses_moving_a_date_forward(
        self, app, db, seed_user, seed_entry_template,
    ):
        """update_entry raises when the new date is past the user's today.

        This is the door that could actually produce one: the add form posts a
        hidden date fixed to today, while the edit form offers a free picker.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"],
                purchased_on=display_today() - timedelta(days=2),
            )
            original = entry.purchased_on
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id, purchased_on=tomorrow,
                )

            # The refusal precedes the setattr loop, so the in-session object
            # is untouched -- asserted unconditionally rather than behind a
            # reload that could vacuously skip.
            assert entry.purchased_on == original

    def test_the_update_door_accepts_a_backdated_move(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Correcting a date backwards -- the intended use of the picker."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])
            corrected = display_today() - timedelta(days=3)

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=corrected,
            )

            assert updated.purchased_on == corrected

    def test_a_partial_update_that_omits_the_date_is_unaffected(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard fires on the value being SET, never on the stored one.

        An amount-only edit must not be refused for a date it is not touching.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00",
                purchased_on=display_today(),
            )

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("75.00")),
            )

            assert updated.amount == Decimal("75.00")
            assert updated.purchased_on == display_today()


# ── Delete Tests ──────────────────────────────────────────────────


class TestDeleteEntry:
    """Tests for entry_service.delete_entry()."""

    def test_delete_entry(self, app, db, seed_user, seed_entry_template):
        """Hard-delete an entry. Entry no longer exists in the database."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])
            entry_id = entry.id

            result = entry_service.delete_entry(entry_id, seed_user["user"].id)

            assert result == txn.id
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_delete_entry_returns_transaction_id(
        self, app, db, seed_user, seed_entry_template,
    ):
        """delete_entry returns the parent transaction_id for CC Payback sync."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            result = entry_service.delete_entry(entry.id, seed_user["user"].id)
            assert result == txn.id

    def test_delete_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when deleting an entry the user does not own."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(NotFoundError):
                entry_service.delete_entry(
                    entry.id, seed_second_user["user"].id,
                )

    def test_delete_entry_nonexistent(self, app, db, seed_user):
        """NotFoundError when entry_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.delete_entry(999999, seed_user["user"].id)


# ── Get Entries Tests ─────────────────────────────────────────────


class TestGetEntries:
    """Tests for entry_service.get_entries_for_transaction()."""

    def test_get_entries_ordered_by_date(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Entries returned in purchased_on ASC order."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            # Create entries out of chronological order.
            _make_entry(txn, user, description="Third",
                        purchased_on=date(2026, 1, 10))
            _make_entry(txn, user, description="First",
                        purchased_on=date(2026, 1, 3))
            _make_entry(txn, user, description="Second",
                        purchased_on=date(2026, 1, 7))

            entries = entry_service.get_entries_for_transaction(
                txn.id, user.id,
            )

            assert len(entries) == 3
            assert entries[0].description == "First"
            assert entries[1].description == "Second"
            assert entries[2].description == "Third"

    def test_get_entries_empty(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Empty list when transaction has no entries."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entries = entry_service.get_entries_for_transaction(
                txn.id, seed_user["user"].id,
            )
            assert entries == []

    def test_get_entries_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when requesting entries for another user's transaction."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            with pytest.raises(NotFoundError):
                entry_service.get_entries_for_transaction(
                    txn.id, seed_second_user["user"].id,
                )

    def test_get_entries_nonexistent_transaction(self, app, db, seed_user):
        """NotFoundError when transaction does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.get_entries_for_transaction(
                    999999, seed_user["user"].id,
                )


# ── Computation Tests ──────────────────────────────���──────────────


class TestComputeEntrySums:
    """Tests for entry_service.compute_entry_sums()."""

    def test_compute_entry_sums_all_debit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Sum debit entries: (180, 0).

        $50 + $100 + $30 = $180 debit, $0 credit.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="30.00"),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("180.00")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_mixed(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Mixed entries: (150, 80).

        Debit: $100 + $50 = $150.  Credit: $80.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="80.00", is_credit=True),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("150.00")
            assert sum_credit == Decimal("80.00")

    def test_compute_entry_sums_empty(self, app):
        """Empty list returns (0, 0)."""
        with app.app_context():
            sum_debit, sum_credit = entry_service.compute_entry_sums([])

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_single_debit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single debit entry: (50, 0)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            sum_debit, sum_credit = entry_service.compute_entry_sums([entry])

            assert sum_debit == Decimal("50.00")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_single_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single credit entry: (0, 75)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="75.00", is_credit=True,
            )

            sum_debit, sum_credit = entry_service.compute_entry_sums([entry])

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("75.00")

    def test_compute_entry_sums_all_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """All credit entries: (0, 250).

        $100 + $150 = $250 credit, $0 debit.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="100.00", is_credit=True),
                _make_entry(txn, user, amount="150.00", is_credit=True),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("250.00")


class TestComputeRemaining:
    """Tests for entry_service.compute_remaining()."""

    def test_compute_remaining_under_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Under budget: remaining = 500 - 330 = 170.

        Entries: $200 debit + $130 debit = $330 total.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="130.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("170.00")

    def test_compute_remaining_over_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Over budget: remaining = 500 - 530 = -30.

        Negative remaining means overspent.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="300.00"),
                _make_entry(txn, user, amount="230.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("-30.00")

    def test_compute_remaining_zero(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Exactly on budget: remaining = 500 - 500 = 0."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="300.00"),
                _make_entry(txn, user, amount="200.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("0")

    def test_compute_remaining_with_credit_entries(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Credit entries count toward budget consumption.

        remaining = 500 - (200 debit + 100 credit) = 200.
        Payment method doesn't affect remaining budget.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="100.00", is_credit=True),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("200.00")

    def test_compute_remaining_zero_estimated(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Estimated amount of 0 with entries: remaining goes negative.

        remaining = 0 - 50 = -50.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            remaining = entry_service.compute_remaining(
                Decimal("0"), [entry],
            )

            assert remaining == Decimal("-50.00")

    def test_a_REFUND_carries_remaining_ABOVE_the_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """`$100.00` budget, one `-$50.00` refund: remaining is `$150.00`.

        Developer ruling 2026-09-01, ruling **bank_import:R-II**, plan step
        ``bank_import:X-gj-2b-3``.  A merchant credit files as a NEGATIVE
        purchase, so this figure is UNBOUNDED ABOVE and the base is a NET cash
        target: `$150.00` of net spending may still be recorded against a
        `$100.00` plan, because `-$50.00` of it has already happened.

        Capping it at the budget was put to the developer with these numbers
        and refused -- it breaks ``sum(entries) + remaining == budget``, which
        every surface that renders this depends on, and it would make the
        dashboard disagree with the balance reservation beside it.  Asserted
        because a cap is the change somebody will reach for when they meet
        `$150.00` on a `$100.00` envelope and read it as a bug.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="-50.00",
                description="Amazon refund",
            )

            remaining = entry_service.compute_remaining(
                Decimal("100.00"), [entry],
            )

            assert remaining == Decimal("150.00")
            # THE IDENTITY the figure exists inside.
            assert remaining + entry.amount == Decimal("100.00")

    def test_a_PARTLY_refunded_envelope_nets_below_its_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The control: `$80.00` spent and `$30.00` back is `$50.00` consumed.

        Without it the case above is satisfied by an implementation that
        ignores negative entries entirely, which would answer `$100.00` there
        and `$20.00` here.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entries = [
                _make_entry(txn, user, amount="80.00"),
                _make_entry(
                    txn, user, amount="-30.00", description="Amazon refund",
                ),
            ]

            assert entry_service.compute_remaining(
                Decimal("100.00"), entries,
            ) == Decimal("50.00")

    def test_compute_remaining_empty_entries(self, app):
        """No entries: remaining equals the estimated amount."""
        with app.app_context():
            remaining = entry_service.compute_remaining(
                Decimal("500.00"), [],
            )

            assert remaining == Decimal("500.00")

    def test_compute_remaining_large_amounts(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Large amounts within Numeric(12,2) precision.

        remaining = 9999999999.99 - 9999999999.98 = 0.01.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="9999999999.98",
            )

            remaining = entry_service.compute_remaining(
                Decimal("9999999999.99"), [entry],
            )

            assert remaining == Decimal("0.01")

    def test_compute_remaining_anchors_on_estimated_only(
        self, app, db, seed_user, seed_entry_template,
    ):
        """C30-3 (E-21 / MED-03 / F-028 / F-056): compute_remaining
        takes only ``estimated_amount`` and therefore cannot anchor on
        ``actual_amount`` or switch on status -- the structural
        guarantee that the entry-tracked bill row's remaining always
        shares the declared E-21 budget base with the row's amount
        cell.

        Worked example: estimated=$120 (the E-21 base), entries
        summing $80; ``actual_amount`` could be anything (e.g. $100)
        and the result MUST stay anchored on $120.
            remaining = 120.00 - 80.00 = 40.00
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entries = [
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="30.00"),
            ]

            # Even if a hypothetical caller passed actual_amount, the
            # function would compute against that value -- the
            # signature accepts no actual or status, so anchoring on
            # estimated is structural.  Pass estimated and assert the
            # E-21 result.
            remaining = entry_service.compute_remaining(
                Decimal("120.00"), entries,
            )

            assert remaining == Decimal("40.00")

    def test_compute_remaining_signature_excludes_actual_and_status(self):
        """C30-3 partner: the function signature cannot consult
        ``actual_amount`` or ``status`` -- it accepts only the declared
        ``budget`` and ``entries`` -- so a future change
        cannot silently shift the entry-tracked row's base away from
        the E-21 / MED-03 base without an explicit signature
        change (which would surface in review).

        **The first parameter is named ``budget`` since plan step X-au-c2b**,
        where it was ``estimated_amount``.  That rename is the assertion's
        subject rather than incidental: a parameter named after a COLUMN
        invites the next caller to pass that column, and under the amount model
        a derived row's is ``NULL``.  The base is what the row's amount
        RESOLVES to, and the name says so.
        """
        # pylint: disable=import-outside-toplevel
        import inspect
        sig = inspect.signature(entry_service.compute_remaining)
        # E-21 base contract: the only inputs are the declared base and the
        # entries to subtract.  No txn, no status, no actual.
        assert list(sig.parameters) == ["budget", "entries"]


class TestPurchasesTotal:
    """Tests for ``row_valuation.purchases_total()``.

    It was ``entry_service.compute_actual_from_entries`` until plan step
    **X-au-c3**, which moved it to :mod:`app.services.row_valuation`: the
    settlement record's own accessor needs it, that module sits under both the
    cash and loan tiers, and the old name referred to a column the step removed.
    The tests stay here, beside the envelope behaviour they describe.
    """

    def test_purchases_total_includes_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Actual = sum of all entries (debit + credit).

        $200 debit + $100 debit + $100 credit = $400.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="100.00", is_credit=True),
            ]

            actual = purchases_total(entries)
            assert actual == Decimal("400.00")

    def test_purchases_total_empty(self, app):
        """Empty entries: actual = 0."""
        with app.app_context():
            actual = purchases_total([])
            assert actual == Decimal("0")

    def test_purchases_total_single_entry(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single entry: actual equals that entry's amount."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="42.50")

            actual = purchases_total([entry])
            assert actual == Decimal("42.50")

    def test_purchases_total_all_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """All credit entries: actual still equals their sum.

        $80 + $120 = $200 (total spending regardless of payment method).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="80.00", is_credit=True),
                _make_entry(txn, user, amount="120.00", is_credit=True),
            ]

            actual = purchases_total(entries)
            assert actual == Decimal("200.00")


# ── Schema Tests ──────────────────────────────────────────────────


class TestEntryCreateSchema:
    """Tests for EntryCreateSchema validation."""

    def test_valid_create_data(self, app):
        """All required fields present and valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Kroger",
                "purchased_on": "2026-01-05",
            })
            assert data["amount"] == Decimal("50.00")
            assert data["description"] == "Kroger"
            assert data["purchased_on"] == date(2026, 1, 5)
            assert data["is_credit"] is False

    def test_schema_rejects_zero_amount(self, app):
        """Amount of 0 is rejected (min is 0.01)."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "0",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_rejects_negative_amount(self, app):
        """Negative amount is rejected."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "-10.00",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_accepts_minimum_amount(self, app):
        """Amount of exactly 0.01 is accepted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "0.01",
                "description": "Penny",
                "purchased_on": "2026-01-05",
            })
            assert data["amount"] == Decimal("0.01")

    def test_schema_rejects_empty_description(self, app):
        """Empty description is rejected (min length 1)."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "50.00",
                    "description": "",
                    "purchased_on": "2026-01-05",
                })
            # strip_empty_strings removes "" -> missing required field
            assert "description" in exc_info.value.messages

    def test_schema_rejects_description_too_long(self, app):
        """Description over 200 characters is rejected."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "50.00",
                    "description": "A" * 201,
                    "purchased_on": "2026-01-05",
                })
            assert "description" in exc_info.value.messages

    def test_schema_accepts_single_char_description(self, app):
        """Description of exactly 1 character is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "10.00",
                "description": "X",
                "purchased_on": "2026-01-05",
            })
            assert data["description"] == "X"

    def test_schema_accepts_max_length_description(self, app):
        """Description of exactly 200 characters is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            desc = "B" * 200
            data = schema.load({
                "amount": "10.00",
                "description": desc,
                "purchased_on": "2026-01-05",
            })
            assert data["description"] == desc

    def test_schema_is_credit_default_false(self, app):
        """is_credit defaults to False when omitted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "purchased_on": "2026-01-05",
            })
            assert data["is_credit"] is False

    def test_schema_is_credit_true(self, app):
        """is_credit can be set to True."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "purchased_on": "2026-01-05",
                "is_credit": True,
            })
            assert data["is_credit"] is True

    def test_schema_strips_empty_strings(self, app):
        """Empty string fields are stripped (HTML form pattern)."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Kroger",
                "purchased_on": "2026-01-05",
                "is_credit": "",  # Empty string from unchecked checkbox
            })
            # is_credit reverts to default (False) after strip
            assert data["is_credit"] is False

    def test_schema_amount_quantized(self, app):
        """Amount with extra decimal places is quantized to 2 places.

        Marshmallow's Decimal(places=2) handles this by rounding.
        """
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.999",
                "description": "Test",
                "purchased_on": "2026-01-05",
            })
            # Marshmallow with places=2 quantizes to 2 decimal places.
            assert data["amount"] == Decimal("51.00")


class TestNeitherHandDoorTakesATypedSign:
    """R-II's tier split, as the developer RE-RULED it on 2026-09-01.

    Ruling **bank_import:R-II** put the row invariant (``amount <> 0``) at the
    service tier, where the bank-import door meets it too
    (``entry_service._refusals._reject_zero_amount``), and moved POSITIVITY off
    the table onto the hand-entry door.

    **This class asserted the OPPOSITE of what it asserts now, and the reason
    is a measurement rather than a preference.**  It read *the update schema
    ACCEPTS a negative because a refund is one*, and defended the asymmetry as
    R-II's point.  ``EntryUpdateSchema`` is reached ONLY by the human PATCH
    route -- the bank-import door writes through ``entry_service`` and passes
    no schema at all -- so both doors carrying this rule are doors a person
    types at, and one of them had no bound.  A typed ``-45.00`` where ``45.00``
    was meant booked a REFUND in silence and moved the projection by twice the
    figure, while the identical keystroke on the ADD form was a 422.

    **Both doors take a MAGNITUDE now**, and the direction is a control
    (``entry_service.purchase_amount``).  The editable-refund problem the old
    asymmetry solved is solved better: the edit form renders the magnitude and
    preselects the direction, so re-describing a stored refund changes no
    figure and needs no negative in the box.
    """

    def test_create_schema_still_refuses_a_typed_negative(self, app):
        """The ADD form's rule survives R-II: a typed negative is a typo."""
        with app.app_context():
            schema = EntryCreateSchema()
            with pytest.raises(MarshmallowValidationError) as exc_info:
                schema.load({
                    "amount": "-28.29",
                    "description": "Amazon refund",
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_update_schema_ALSO_refuses_a_typed_negative(self, app):
        """The EDIT door's bound, restored on the developer's 2026-09-01 ruling.

        It is the same rule as the create door's now, and stating BOTH in one
        class is what keeps a future edit from removing one and leaving the
        other -- which is the shape that produced the silent refund.
        """
        with app.app_context():
            schema = EntryUpdateSchema()
            with pytest.raises(MarshmallowValidationError) as exc_info:
                schema.load({"amount": "-28.29"})
            assert "amount" in exc_info.value.messages

    def test_a_refund_is_stated_by_the_DIRECTION_on_both_doors(self, app):
        """The control the two refusals above must not have deleted.

        A refund is still recordable -- that is ruling **R-II** -- and this is
        how: a magnitude plus ``direction``, composed by
        :func:`~app.services.entry_service.purchase_amount`.  Without this case
        the pair above is satisfied by a door that cannot record a refund at
        all.
        """
        with app.app_context():
            for schema in (EntryCreateSchema(), EntryUpdateSchema()):
                data = schema.load({
                    "amount": "28.29", "direction": "refund",
                    "description": "Amazon refund",
                    "purchased_on": "2026-01-05",
                })
                assert data["amount"] == Decimal("28.29")
                assert data["direction"] == "refund"
            assert entry_service.purchase_amount(
                Decimal("28.29"), records_a_refund=True,
            ) == Decimal("-28.29")
            assert entry_service.purchase_amount(
                Decimal("28.29"), records_a_refund=False,
            ) == Decimal("28.29")

    def test_neither_door_lets_a_zero_through_to_the_database(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Zero is refused as a ValidationError, never as an IntegrityError.

        The CHECK is the backstop; a zero reaching it surfaces to the user as
        *Something went wrong* over a traceback.  Both write doors ask
        ``_reject_zero_amount`` instead, so the refusal names the field.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            with pytest.raises(ValidationError, match="cannot be zero"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=user.id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("0.00")),
                        description="Nothing",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("10.00")),
                    description="Real purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.commit()

            with pytest.raises(ValidationError, match="cannot be zero"):
                entry_service.update_entry(
                    entry.id, user.id, figure=typed(Decimal("0.00")),
                )

    def test_a_refund_round_trips_through_the_service_door(
        self, app, db, seed_user, seed_entry_template,
    ):
        """A negative purchase is created and STORED as a negative.

        The act plan step ``bank_import:X-gj-2b`` exists to make possible,
        asserted at the tier the bank-import door actually uses -- it calls
        ``create_entry`` directly and never passes through a Marshmallow schema.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("-28.29")),
                    description="Amazon refund",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.commit()

            assert entry.amount == Decimal("-28.29")
            assert db.session.get(
                TransactionEntry, entry.id,
            ).amount == Decimal("-28.29")


class TestEntryUpdateSchema:
    """Tests for EntryUpdateSchema validation."""

    def test_update_partial_amount(self, app):
        """Only amount provided -- valid partial update."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({"amount": "75.00"})
            assert data["amount"] == Decimal("75.00")
            assert "description" not in data

    def test_update_partial_is_credit_only(self, app):
        """Only is_credit provided -- valid partial update."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({"is_credit": True})
            assert data["is_credit"] is True
            assert "amount" not in data

    def test_update_no_fields(self, app):
        """No fields provided -- empty dict (service handles this error)."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({})
            assert data == {}

    def test_update_rejects_zero_amount(self, app):
        """Amount of 0 is rejected in update schema too.

        The half of the old bound that SURVIVED ruling **bank_import:R-II**: a
        purchase worth nothing is not a purchase, and that is the row's own
        invariant (``amount <> 0``) rather than a statement about the sign.
        Kept on the schema so it answers 422 -- the service gate under it
        (``_reject_zero_amount``) answers 400, which is the right code for a
        caller that reached the service without a form.
        """
        with app.app_context():
            schema = EntryUpdateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({"amount": "0"})
            assert "amount" in exc_info.value.messages

    # **The negative-amount case that stood here is GONE, not moved** (plan
    # step ``bank_import:X-gj-2b-3``).  It was a second copy of
    # ``TestNeitherHandDoorTakesATypedSign``'s subject -- adversarial test-quality
    # review found the pair -- and both asserted the contract the developer
    # re-ruled on 2026-09-01: the edit door takes a MAGNITUDE, and a refund is
    # stated by ``direction``.  One class states that now, with the control
    # that a refund is still recordable, rather than two that agreed.

    def test_update_unknown_fields_excluded(self, app):
        """Unknown fields are excluded by BaseSchema (Meta.unknown = EXCLUDE)."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({
                "amount": "50.00",
                "bogus_field": "ignored",
            })
            assert "bogus_field" not in data
            assert data["amount"] == Decimal("50.00")


# ── pct_complete (Commit 9 / F-23) ────────────────────────────────


class TestPctComplete:
    """Tests for entry_service.pct_complete().

    Locks the MED-04 / E-16 standard: money math is service-layer
    Decimal, not route-layer float.  The companion route used to cast
    the entry-percentage to float before handing it to the template;
    the helper now keeps the result as a Decimal end-to-end, mirroring
    the shape of dashboard_service._safe_pct_complete.
    """

    def test_pct_complete_normal_case(self):
        """C9-1: total $50, target $100 -> Decimal("50.00").

        Hand arithmetic: 50 / 100 * 100 = 50, quantised to 2dp = 50.00.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("100"),
        ) == Decimal("50.00")

    def test_pct_complete_clamps_at_100(self):
        """C9-2: over-budget total $150 against target $100 -> Decimal("100.00").

        Hand arithmetic: 150 / 100 * 100 = 150, clamped at 100 because
        the progress-bar width has no defined meaning past 100%.
        """
        assert entry_service.pct_complete(
            Decimal("150"), Decimal("100"),
        ) == Decimal("100.00")

    def test_pct_complete_target_zero_guard(self):
        """C9-3: target $0 -> Decimal("0") (no divide-by-zero).

        A zero target is a degenerate budget line; returning 0 (rather
        than raising) lets the companion view render the row without
        special-casing in the template.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("0"),
        ) == Decimal("0")

    def test_pct_complete_target_negative_guard(self):
        """Negative target -> Decimal("0") (same guard path as zero).

        Defensive: ``estimated_amount`` is CHECK-constrained to >= 0
        at the storage tier, but the helper should not produce a
        misleading negative percentage if a future caller violates
        that invariant in an in-memory object.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("-10"),
        ) == Decimal("0")

    def test_pct_complete_zero_total(self):
        """Zero spending against a positive target -> Decimal("0.00").

        Hand arithmetic: 0 / 100 * 100 = 0.00.
        """
        assert entry_service.pct_complete(
            Decimal("0"), Decimal("100"),
        ) == Decimal("0.00")

    def test_pct_complete_returns_decimal_not_float(self):
        """MED-04 / E-16: the return type is Decimal, not float.

        Locks the post-F-23 contract that no caller has to do a
        ``float(Decimal_expression)`` cast at the route layer.
        """
        result = entry_service.pct_complete(
            Decimal("55.50"), Decimal("100.00"),
        )
        assert isinstance(result, Decimal)
        # Hand arithmetic: 55.50 / 100 * 100 = 55.50.
        assert result == Decimal("55.50")


def _outstanding_debit(txn, seed_user, amount="50.00",
                       purchased_on=date(2026, 1, 5)):
    """Attach one debit purchase with NO recorded posting day.

    Kept here for the helper-guard class below when the outstanding-set tests
    moved to ``test_reconcile_service`` at plan step X-f2-c1: that class grades
    ``tests._test_helpers.mark_purchase_settled``, which is about a purchase
    with no settle day, and building one is all it needs from the reconcile
    subject.
    """
    return _make_entry(
        txn, seed_user["user"], amount=amount,
        description="Kroger", purchased_on=purchased_on,
    )


class TestTheMarkPurchaseSettledHelperGuardsItsPrecondition:
    """``tests._test_helpers.mark_purchase_settled`` -- the helper's own guard.

    A TEST helper, graded here because its failure mode is what finding
    N-132 / R8 is about and an untested failure mode is one a future edit
    weakens for free.  It is the successor to ``add_entry(..., is_cleared=True)``,
    and the flag it replaced was an UNCONDITIONAL claim that a purchase was
    inside the anchor -- so fixtures set it on accounts whose only assertion
    PREDATED the purchase by months, a state production cannot reach, and those
    fixtures passed for years while silently no longer discriminating the case
    they named.

    The helper does what the flag did AND asserts the precondition the flag let
    fixtures skip.  These are its two refusals, each with the failure message
    checked, because a helper that failed with a bare ``AssertionError`` would
    send the next reader hunting through the balance fold instead of at the
    fixture.
    """

    def test_it_refuses_when_the_assertion_predates_the_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The N-132 shape itself: settled AFTER the account's latest assertion.

        The account asserts its balance for the first period's start day; a
        purchase settled a week later is not inside it, however the fixture is
        spelled, so a suite expecting the settled bucket is asking for a state
        its own account cannot be in.  The message names BOTH days so the fix
        -- assert again, or accept the outstanding bucket -- is visible without
        reading the helper.

        **The asserted day is stated here rather than inherited** (plan step
        X-f3c-2c): the seeded account carries only its ORIGINATION assertion,
        dated on the bootstrap day before the calendar, so a case that turns on
        which day was last asserted says which day that is.
        """
        with app.app_context():
            reassert_balance_on(
                db.session, seed_user["account"],
                settle_instant_on(seed_periods[0].start_date),
            )
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()
            too_late = seed_periods[0].start_date + timedelta(days=7)

            with pytest.raises(AssertionError) as excinfo:
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                    settled_on=too_late,
                )

            message = str(excinfo.value)
            assert too_late.isoformat() in message
            assert seed_periods[0].start_date.isoformat() in message
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_it_refuses_when_the_account_has_asserted_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """No assertion at all means there is nothing to be inside of.

        ``ReconciledThrough.covers`` is total in both the argument and the
        boundary and answers False for a missing assertion, so a fixture that skipped this check would
        build a purchase the projection reads as outstanding while the test
        asserted the settled figure -- failing later, in the balance, with no
        indication that the ACCOUNT was the problem.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            # **Built rather than emptied** (plan step X-f3c-2c): an assertion
            # is append-only at the database tier, so an account that has
            # asserted nothing is one the assertion factory never touched.  The
            # guard reads the ACCOUNT it is handed, which is what lets this
            # case grade it against an account other than the purchase's.
            account = account_never_asserted(
                seed_user, db.session, name="Silent",
                opening_equity=Decimal("0.00"),
            )
            db.session.commit()

            with pytest.raises(AssertionError) as excinfo:
                mark_purchase_settled(db.session, account, entry)

            assert "has asserted no balance" in str(excinfo.value)

    def test_it_records_the_day_when_the_assertion_covers_it(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The positive control: it still does what the flag did.

        Without this the two refusals above would be satisfied by a helper that
        refused everything.  A purchase settled on the account's own asserted
        day passes, and ``settled_on`` defaults to the purchase's own
        ``purchased_on`` -- "it posted the day I bought it", the shape a
        same-day debit has.  The asserted day is stated here for the reason the
        case above states it (plan step X-f3c-2c).
        """
        with app.app_context():
            reassert_balance_on(
                db.session, seed_user["account"],
                settle_instant_on(seed_periods[0].start_date),
            )
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(
                txn, seed_user, purchased_on=seed_periods[0].start_date,
            )
            db.session.commit()

            mark_purchase_settled(db.session, seed_user["account"], entry)
            db.session.commit()

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == seed_periods[0].start_date


class TestASettledRowsPurchasesAreClosed:
    """Finding **N-229**, widened to the settled BAND at plan step X-au-c3.

    A settled row's money has MOVED, and every entry door would RE-COST it:
    adding a purchase grows what the row cost, deleting one shrinks it, and
    re-pricing one does either.  So all three refuse on Paid and Received as
    well as on the terminal ``Settled`` (developer ruling, 2026-08-17).

    **The refusal is FIELD-AWARE, and the one field it admits is
    ``settled_on``** -- the day the BANK took a purchase.  That is an
    observation about money which has ALREADY moved rather than a restatement of
    how much moved: it re-dates that purchase's cash and changes no total.  The
    last two cases below are that half of the rule; ``_COST_BEARING_FIELDS`` is
    where it is stated.

    **What decides the width is carry-forward.**  It rolls an envelope's unspent
    remainder into the NEXT period's row and then settles the source at what was
    spent -- so a purchase recorded against the closed source afterwards raises
    its cost while the later row still holds the rolled-forward money, and the
    same dollars are counted twice.  A forgotten purchase belongs in the period
    that now holds the money; the way to record it here is to put the row back
    to Projected, add it, and close the row again.

    **It was the ARCHIVE status alone until this step**, because a Paid envelope
    re-derived its figure from its entries and late-posting purchases were
    therefore meant to land on one.  That re-derivation is deleted: it moved
    money in the OPTIMISTIC direction with no human act, crashing a ``$500``
    close to ``$50`` on the first back-filled purchase and handing ``$450`` of
    already-spent money back to the projection.

    Before plan step X-ap a new purchase against an ARCHIVED row was ACCEPTED,
    persisted, and half-processed: ``actual_amount`` was not recomputed (that
    half graded ``is_done`` -- exactly Paid) while the postings WERE reconciled
    (that half graded the settled BAND), so the ledger moved and the figure it
    was derived from did not.
    """

    @staticmethod
    def _close(txn):
        """Move *txn* Projected -> Paid through the real seam."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
        )
        db.session.flush()

    def test_update_is_refused(self, app, db, seed_user, seed_entry_template):
        """An existing purchase on a settled row cannot be re-priced.

        The row was closed carrying a $50.00 purchase; re-pricing it to
        $500.00 would rewrite what the books already say the row cost.

        Specimen was the terminal ``Settled`` ARCHIVE until plan step
        **balance:X-am** deleted that status.  ``_reject_settled_parent`` reads
        the BAND and never the member -- that is this class's whole subject --
        so the case moves to Paid unchanged.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id, figure=typed(Decimal("500.00")),
                )

            # No rollback: the guard runs BEFORE the setattr loop, so a refused
            # call stages nothing -- which is the property being asserted.
            assert entry.amount == Decimal("50.00")

    def test_delete_of_an_undated_purchase_is_admitted(
        self, app, db, seed_user, seed_entry_template,
    ):
        """An UNDATED purchase CAN be removed from a settled ``purchases`` row.

        **The refusal's SENTENCE moved twice and then its arm was lifted.**
        Plan step ``bank_import:X-f6f`` gave the ARCHIVE one of its own; plan
        step **balance:X-am** deleted the archive and that sentence; and plan
        step ``balance:X-bi-4a`` lifted the arithmetic arm itself (ruling
        **R-BAL77**): removing an UNDATED debit purchase used to shrink what
        the row recorded as costing on a past day, because the row's own leg
        booked it on the close day.  A plan row books nothing of its own
        now, so the removal removes a movement in flight and nothing else --
        the ordinary direction of every other removal.  The STORED-figure
        refusal beside it stands (``test_a_PAID_row_refuses_a_late_purchase``
        and its siblings).
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            entry_id = entry.id

            entry_service.delete_entry(entry_id, seed_user["user"].id)
            db.session.flush()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_a_PAID_row_refuses_a_late_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The refusal is the settled BAND, not the archive status alone.

        **This asserted the opposite until plan step X-au-c3**: a Paid envelope
        accepted a late purchase and re-derived its figure from it, so a
        ``$50.00`` purchase recorded after a close at the row's ``$500.00``
        estimate re-priced the row to ``$50.00`` -- putting ``$450`` of
        already-spent money back in the projection with no human act, and
        double-counting it against the leftover carry-forward had already rolled
        into the next period.

        The row is left exactly as the close left it: worth what it recorded,
        with no purchase persisted.  Shown to FIRE: narrowing the guard back to
        ``is_archived`` let the entry through -- that predicate is itself
        deleted at plan step **balance:X-am** with the status it named, so the
        narrowing this case refuses is no longer expressible.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            recorded_before = settled_figure(txn)

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")),
                        description="Late purchase",
                        purchased_on=display_today(),
                    ),
                )

            assert settled_figure(txn) == recorded_before
            # No purchase landed; the covering movement is not one (X-bi-3a).
            assert purchases_of(txn.id) == []

    def test_the_BANK_POSTING_DAY_is_still_recordable(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The one edit a closed row admits, and the reason the guard is FIELD-AWARE.

        **Everything above is an argument about what the row COST.  The day the
        BANK took a purchase is not that** (developer ruling, 2026-08-17).
        Recording it changes no total: a purchase is a movement of its own
        and the row books nothing (ruling **R-BAL80**), so what the family
        moves is the same before and after -- the purchase leaves the
        in-flight tier for the dated stream.  (Through plan step ``X-bi-3e``
        the row's own leg subtracted every POSTED purchase from the close, so
        the two summed to the row's whole debit either way.)  What moves is
        the DAY, which is what a paper statement is reconciled against.

        Refusing it would strand already-spent money on the day the envelope
        happened to be closed, with no door to correct it: measured on the
        2026-08-17 production dump, 28 closed envelopes hold 61 debit purchases
        carrying no posting day, ``$4,360.07`` between them.

        That split is this step's own three-lifetime model read one level down.
        A purchase's amount is WHAT MOVED and its posting day is an ASSERTION
        about when -- the same two facts ``settled_amount`` and ``settled_on``
        are on the parent, with the same answer: the assertion may be recorded,
        corrected and withdrawn long after the figure is final.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            recorded_before = settled_figure(txn)
            posted_on = display_today()

            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=an_entered_day(posted_on),
            )
            db.session.flush()

            assert entry.settled_on == posted_on
            # What the row COST is untouched -- the whole point of admitting
            # this field and no other.
            assert settled_figure(txn) == recorded_before

            # And it can be WITHDRAWN again, which is what "the statement does
            # not actually show it" means.
            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=None,
            )
            db.session.flush()
            assert entry.settled_on is None
            assert settled_figure(txn) == recorded_before

    def test_a_posting_day_edit_that_also_RE_PRICES_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard grades the whole submission, not its most innocent field.

        A caller cannot smuggle a re-price past a closed row by sending it
        alongside the one field the row admits.  ``_reject_settled_parent``
        refuses when ANY cost-bearing field is present, so the mixed submission
        is refused whole and neither field lands.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    settle_day=an_entered_day(display_today()),
                    figure=typed(Decimal("500.00")),
                )

            # The guard runs BEFORE the setattr loop, so nothing is staged.
            assert entry.amount == Decimal("50.00")
            assert entry.settled_on is None

    def test_the_PURCHASE_DAY_is_recordable_on_a_closed_row(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The second field the guard admits, and the one it was WRONG about.

        ``purchased_on`` is the BUDGET clock -- the day the purchase was made.
        A census of every reader in ``app/`` finds no money rule among them:
        ``Transaction.entries``' ordering, the out-of-period WARNING, the
        reconcile panel's sort and its offer predicate, the matcher's
        ``expected_on``, and three template fields.  What the row COST is
        ``amount`` and ``is_credit``, and neither moves here.

        **It was refused until plan step ``bank_import:X-f6a-3b``**, because
        :data:`~app.services.entry_service._doors._COST_BEARING_FIELDS` was
        spelled ``_UPDATABLE_FIELDS - {"settled_on"}`` -- a set defined by what
        it excludes, which silently claimed two fields nothing prices.  Ruling
        **R-FW**'s purchase-day correction submits this field beside
        ``settled_on`` in ONE ``update_entry`` call, so on the developer's own
        statement 13 of the 15 corrections the review screen offered were
        refused: the screen rendered an Accept button that could never succeed.

        Shown to FIRE: restoring ``purchased_on`` to the cost-bearing set makes
        this raise.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            made_on = display_today() - timedelta(days=5)
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00", purchased_on=made_on,
            )
            self._close(txn)
            recorded_before = settled_figure(txn)

            corrected = made_on - timedelta(days=3)
            entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=corrected,
            )
            db.session.flush()

            assert entry.purchased_on == corrected
            # What the row COST is untouched, which is the whole reason the
            # field is admitted.
            assert settled_figure(txn) == recorded_before

    def test_the_DESCRIPTION_is_editable_on_a_closed_row(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The other field the corrected set stopped claiming.

        Nothing outside ``app/templates/`` reads it, so renaming a purchase on
        a closed row changes no figure and no derivation.  It was refused for
        the same reason ``purchased_on`` was: the set was defined by
        subtraction rather than by what prices a row.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            recorded_before = settled_figure(txn)

            entry_service.update_entry(
                entry.id, seed_user["user"].id, description="Food Lion",
            )
            db.session.flush()

            assert entry.description == "Food Lion"
            assert settled_figure(txn) == recorded_before


class TestASettledRowMayStillGAINAPurchase:
    """Plan step ``bank_import:X-f6a-3b``: the ADD is its own rule.

    **Adding a purchase and removing one are not the same question**, and
    keeping them under one refusal made the evidenced direction share a guard
    with the optimistic one.  Removing a purchase shrinks a recorded cost on
    nothing but the user's second thoughts.  Adding one to a row whose figure IS
    its purchases raises that cost by exactly the figure a bank statement just
    showed -- which is the whole of what the statement importer does.

    **The rule is about the row's FIGURE, not its status**, and the two bases
    behave oppositely (measured on a production clone 2026-08-18):

      * a ``purchases`` settlement stores no figure, so the close IS
        ``Sigma(entries)`` and has no covering movement.  A new posted
        purchase is a movement of its own on its own day and the row books
        nothing (ruling **R-BAL80**), so nothing else moves.  Adding `$18.64`
        to one 2026-05-21 close shrank that day's anchor true-up by exactly
        `$18.64`;
      * a ``derived`` or ``corrected`` settlement stores its figure, fixed
        before the purchase existed, and its covering movement carries the
        whole close.  The purchase's own movement would then be counted
        BESIDE it -- a `$500.00` close with a `$600.00` purchase inserted
        reads `-1,100.00` -- a double count of spending the close never held.
        (Under the row-leg fold of the time the same insert read the money
        back the other way: `-163.95` became **`+203.67`**, an expense row
        publishing an inflow, while the true-up moved `$0.00` so the
        spending was not recorded at all.)

    :func:`~app.services.entry_service._doors._reject_settled_addition` is the
    only thing that makes the second state unrepresentable --
    ``cash_ledger.movement_cash_leg`` states no precondition and cannot see
    one.
    """

    @staticmethod
    def _close(txn):
        """Move *txn* Projected -> Paid through the real seam."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(
                txn, ref_cache.status_id(StatusEnum.DONE),
            ),
        )
        db.session.flush()

    def test_a_purchases_basis_close_ADMITS_a_new_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The row's recorded cost GROWS by exactly the purchase.

        That is not a side effect to be tolerated -- it is what the row's
        settlement record MEANS.  A ``purchases`` basis stores no figure, so
        saying "this envelope also paid for that" is saying "it cost this much
        more", and the bank is the evidence.

        **The purchase states the day the bank took it**, which is what the
        rule requires of an addition to a closed row: without it the amount
        would come out of the envelope's own leg on the day the row closed
        rather than booking its own dated cash.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            assert txn.covering_movements == []
            recorded_before = settled_figure(txn)

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                    settle_day=an_entered_day(display_today()),
                ),
            )
            db.session.flush()

            assert settled_figure(txn) == recorded_before + Decimal("30.00")
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 2

    def test_a_POSTED_addition_adds_its_own_movement_and_moves_nothing_else(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The money property the whole rule rests on.

        A purchase carrying a bank posting day is a cash movement of its own
        (ruling **R-FM**), and on a ``purchases`` basis the row books nothing
        of its own (ruling **R-BAL80**; it is worth nothing to the matcher
        either, ruling **R-BAL81**).  So the import RECORDS the movement
        instead of redistributing one it already had: the dated stream gains
        exactly the new purchase on its own day, and the `$50.00` already in
        flight stays there untouched.

        Through plan step ``balance:X-bi-4a``'s first cut this pinned the
        row's own leg -- ``gross`` and ``Sigma(posted)`` rose together, so
        ``settled_cash_leg`` read `-50.00` before and after -- and its firing
        control was that term; R-BAL81 deleted the leg, and the control is
        the composition of the two streams.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            held = _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)
            account_id, scenario_id = txn.account_id, txn.scenario_id
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0.00")
            assert [
                (item.entry_id, item.delta)
                for item in cash_ledger.in_flight_movements(account_id, scenario_id)
            ] == [(held.id, Decimal("-50.00"))]
            dated_before = [
                (fact.settled_on, fact.delta)
                for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)
            ]

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                    settle_day=an_entered_day(display_today()),
                ),
            )
            db.session.flush()
            db.session.expire(txn)

            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0.00")
            assert [
                (item.entry_id, item.delta)
                for item in cash_ledger.in_flight_movements(account_id, scenario_id)
            ] == [(held.id, Decimal("-50.00"))]
            assert [
                (fact.settled_on, fact.delta)
                for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)
            ] == dated_before + [(display_today(), Decimal("-30.00"))]
            assert cash_ledger.posted_purchase_sum(txn) == Decimal("30.00")

    def test_a_STORED_FIGURE_close_refuses_a_new_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The arm that would fabricate an inflow, refused at the door.

        The row closed with no purchases, so its settlement stores `$500.00`
        and nothing can raise it.  Recording a `$600.00` purchase against it
        would take `$600.00` out of a `$500.00` gross.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            assert len(txn.covering_movements) == 1

            with pytest.raises(ValidationError, match="records a fixed figure"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("600.00")),
                        description="BJs",
                        purchased_on=display_today(),
                        settle_day=an_entered_day(display_today()),
                    ),
                )

            # No purchase landed; the covering movement is not one (X-bi-3a).
            assert purchases_of(txn.id) == []

    def test_a_close_of_NOTHING_admits_a_new_purchase_and_then_records_it(
        self, app, db, seed_user, seed_entry_template,
    ):
        """A ``$0.00`` close holds no covering movement, so nothing doubles.

        Ruling **R-BAL82** (plan step ``balance:X-bi-4b-1``): the rule reads
        the covering movement, and ``ck_transaction_entries_positive_amount``
        admits no movement of nothing -- so a row closed at a typed ``$0.00``
        is a close with no entries, the door ADMITS a later purchase, the row
        then records exactly that purchase (``settled_figure``), the family
        moves it once, and its removal is admitted by the same reading.  The
        column read this replaced filed the row under "a fixed figure" and
        refused both.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
            )
            db.session.flush()
            assert txn.covering_movements == []
            assert settled_figure(txn) == Decimal("0")
            account_id, scenario_id = txn.account_id, txn.scenario_id
            facts_before = sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            )

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.00")),
                    description="Late fee after all",
                    purchased_on=display_today(),
                    settle_day=an_entered_day(display_today()),
                ),
            )
            db.session.flush()

            assert purchases_of(txn.id) == [entry]
            assert settled_figure(txn) == Decimal("12.00")
            assert sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            ) == facts_before - Decimal("12.00")
            assert entry_service.removal_refusal(txn) is None

    def test_the_state_the_refusal_prevents_publishes_a_DOUBLE_COUNT(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard's firing control, built the only way it can be reached.

        ``movement_cash_leg`` is TOTAL in every other direction and states no
        precondition here, so nothing but
        :func:`~app.services.entry_service._doors._reject_settled_addition`
        keeps this unrepresentable.  A test that only asserted the refusal
        would pass just as well if the underlying state were harmless -- so
        this one builds it through the ORM, past every door, and measures what
        the money rule then answers.

        The family reads `-1,100.00` for a close that recorded `$500.00`: the
        covering movement carries the whole close and the illegal purchase's
        own movement is counted BESIDE it (ruling **R-BAL80**), `$600.00` of
        spending the close never held.  Every reader of the family -- the fold
        (``settled_cash_facts``) and the ledger writer -- sees the double
        count; the row's own valuation does not move (ruling **R-BAL81**: it
        is worth its covering movement, still `-500.00`), which is why the
        matcher alone would be blind to it.

        Through plan step ``balance:X-bi-4a``'s first cut the composition
        under test was the row's OWN leg, which read the whole illegal
        purchase back as an INFLOW (`0 - 600` beside the covering movement;
        `-500 + 600 = +100` before X-bi-3a); that leg is deleted.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            self._close(txn)
            account_id, scenario_id = txn.account_id, txn.scenario_id
            # The covering movement carries the `$500.00`; the row is worth
            # it (R-BAL81) and the family moves it.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("-500.00")
            assert sum(
                (cash_ledger.movement_cash_leg(txn, entry) for entry in txn.entries),
                Decimal("0.00"),
            ) == Decimal("-500.00")
            facts_before = sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            )

            # PAST the door on purpose -- see the docstring.
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("600.00"), description="BJs",
                purchased_on=display_today(), **settle_day_columns(display_today()),
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()
            db.session.expire(txn)

            # The family now moves `$1,100.00` for a `$500.00` close, and the
            # fold reads the same: the illegal purchase's own fact beside the
            # covering movement's.
            assert sum(
                (cash_ledger.movement_cash_leg(txn, entry) for entry in txn.entries),
                Decimal("0.00"),
            ) == Decimal("-1100.00")
            assert sum(
                (fact.delta for fact in cash_ledger.settled_cash_facts(account_id, scenario_id)),
                Decimal("0.00"),
            ) == facts_before - Decimal("600.00")
            # ...while the row's own valuation is untouched, so only a reader
            # of the family can see the double count.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("-500.00")

    def test_an_UNDATED_purchase_is_admitted_on_a_closed_row_and_is_in_flight(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The un-dated arm is LIFTED (ruling R-BAL77, plan step balance:X-bi-4a).

        The developer's 2026-08-19 ruling refused an undated purchase on a
        closed envelope because the row's own leg then booked it ON THE DAY
        THE ROW CLOSED -- a past day the owner may already have checked
        against a statement, with no external evidence for the movement.  A
        plan row books nothing of its own now (ruling **R-BAL80**): the
        purchase is a movement IN FLIGHT, held by the projection from
        tomorrow and absent from the actual until the bank is seen to take
        it, exactly as one under an open envelope is.  The second half
        measures that: the settled stream reads nothing for it and the
        in-flight tier reads its figure.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            _make_entry(txn, seed_user["user"], amount="50.00")
            self._close(txn)

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                ),
            )
            db.session.flush()
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 2

            # Nothing dated on the row, so the settled stream holds nothing of
            # it; both purchases are in flight at their own figures.
            facts = cash_ledger.settled_cash_facts(txn.account_id, txn.scenario_id)
            assert [fact for fact in facts if fact.transaction_id == txn.id] == []
            in_flight = {
                item.delta
                for item in cash_ledger.in_flight_movements(
                    txn.account_id, txn.scenario_id,
                )
                if item.transaction_id == txn.id
            }
            assert in_flight == {Decimal("-50.00"), Decimal("-30.00")}


class TestAPurchaseMayBeBornCarryingItsPostingDay:
    """``EntryDetails.settled_on``, plan step ``bank_import:X-f6a-3b``.

    The field was deliberately absent on the premise that *at the moment a
    purchase is recorded there is nothing to have observed*.  True of the
    add-purchase form; false of a purchase created FROM a bank statement line,
    where the observation is what caused the record to exist.

    **Both of the update door's posting-day rules come with it**, because a
    door that accepts a field and leaves its rules to the other door is a
    boundary that holds on one and not the other.
    """

    def test_the_posting_day_is_written_at_create(
        self, app, db, seed_user, seed_entry_template,
    ):
        """One act, not two.

        A follow-up ``update_entry`` would re-run the payback sync and the
        posting reconcile against an intermediate state in which a purchase the
        bank has already taken looks outstanding -- and would leave that state
        committed if anything after it refused.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            posted = display_today()
            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=posted - timedelta(days=2),
                    settle_day=an_entered_day(posted),
                ),
            )
            db.session.flush()

            assert entry.settled_on == posted
            assert entry.purchased_on == posted - timedelta(days=2)

    def test_omitting_it_leaves_the_purchase_OUTSTANDING(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The default is unchanged: a hand-typed purchase observes nothing."""
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")),
                    description="Food Lion",
                    purchased_on=display_today(),
                ),
            )
            db.session.flush()

            assert entry.settled_on is None

    def test_a_posting_day_BEFORE_the_purchase_day_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Money cannot leave the account before it is spent.

        ``ck_transaction_entries_settled_not_before_purchase`` is the backstop;
        this is the door, so the user gets both dates rather than a 500 from an
        ``IntegrityError``.  Shown to FIRE: without the call the insert reaches
        the CHECK.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            with pytest.raises(
                ValidationError, match="cannot reach your bank before",
            ):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("30.00")),
                        description="Food Lion",
                        purchased_on=display_today(),
                        settle_day=an_entered_day(display_today() - timedelta(days=1)),
                    ),
                )
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_a_FUTURE_posting_day_is_refused(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Ruling **R-FM**'s bound, applied at both doors rather than one.

        A day the bank has not reached releases the purchase's reservation now
        and books its cash later.
        """
        with app.app_context():
            txn = db.session.get(
                Transaction, seed_entry_template["transaction"].id,
            )
            with pytest.raises(ValidationError, match="cannot be in the future"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("30.00")),
                        description="Food Lion",
                        purchased_on=display_today(),
                        settle_day=an_entered_day(display_today() + timedelta(days=1)),
                    ),
                )
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0
