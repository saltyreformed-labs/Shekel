"""Auth service tests: registering a user, its validation and default categories."""

from decimal import Decimal

import pytest

from app.models.user import User, UserSettings
from app.models.category import Category
from app.models.scenario import Scenario
from app.services import auth_service, registration_service
from app.exceptions import ConflictError, ValidationError
from tests._test_helpers import registration_spec


class TestRegisterUser:
    """Tests for registration_service.register_user()."""

    def test_register_user_creates_user(self, app, db):
        """register_user() creates a User, UserSettings, and baseline Scenario.

        Verifies all three records exist in the database with the correct
        field values after a successful registration.
        """
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="newuser@example.com", password="securepass123",
                display_name="New User",
            ))
            db.session.flush()

            assert user.id is not None
            assert user.email == "newuser@example.com"
            assert user.display_name == "New User"

            # Exactly one UserSettings row for this user.
            settings = db.session.query(UserSettings).filter_by(
                user_id=user.id
            ).all()
            assert len(settings) == 1

            # Exactly one baseline Scenario for this user.
            scenarios = db.session.query(Scenario).filter_by(
                user_id=user.id
            ).all()
            assert len(scenarios) == 1
            assert scenarios[0].is_baseline is True
            assert scenarios[0].name == "Baseline"

    def test_register_user_password_is_bcrypt_hashed(self, app, db):
        """register_user() stores a bcrypt hash, not the plaintext password.

        Verifies the hash starts with $2b$ (bcrypt identifier) and that
        verify_password() can round-trip the original plaintext.
        """
        with app.app_context():
            plaintext = "securepass123"
            user = registration_service.register_user(registration_spec(
                email="hash@example.com", password=plaintext,
                display_name="Hash Test",
            ))
            db.session.flush()

            assert user.password_hash != plaintext
            assert user.password_hash.startswith("$2b$")
            assert auth_service.verify_password(plaintext, user.password_hash)

    def test_register_user_settings_have_correct_defaults(self, app, db):
        """register_user() creates UserSettings with the model-defined defaults.

        Checks each default value matches the column definition in the
        UserSettings model.
        """
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="defaults@example.com", password="securepass123",
                display_name="Defaults Test",
            ))
            db.session.flush()

            settings = db.session.query(UserSettings).filter_by(
                user_id=user.id
            ).one()

            assert settings.default_inflation_rate == Decimal("0.0300")
            assert settings.grid_default_periods == 6
            assert settings.low_balance_threshold == 500
            assert settings.safe_withdrawal_rate == Decimal("0.0400")

    def test_register_user_email_is_lowercased(self, app, db):
        """register_user() lowercases the email before storage.

        Email addresses are case-insensitive per RFC 5321, so storage
        must be normalized to prevent login mismatches.
        """
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="UPPER@EXAMPLE.COM", password="securepass123",
                display_name="Upper Test",
            ))
            db.session.flush()

            assert user.email == "upper@example.com"

    def test_register_user_email_is_stripped(self, app, db):
        """register_user() strips whitespace from email before storage."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="  spaced@example.com  ", password="securepass123",
                display_name="Spaced Test",
            ))
            db.session.flush()

            assert user.email == "spaced@example.com"

    def test_register_user_display_name_is_stripped(self, app, db):
        """register_user() strips whitespace from display_name before storage."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="strip@example.com", password="securepass123",
                display_name="  Padded Name  ",
            ))
            db.session.flush()

            assert user.display_name == "Padded Name"

    def test_register_user_duplicate_email_raises_conflict(
        self, app, db, seed_user
    ):
        """register_user() raises ConflictError for a duplicate email.

        Uses the seed_user fixture which has email test@shekel.local.
        """
        with app.app_context():
            with pytest.raises(ConflictError, match="already exists"):
                registration_service.register_user(registration_spec(
                    email="test@shekel.local", password="securepass123",
                    display_name="Dup Test",
                ))

    def test_register_user_duplicate_email_case_insensitive(
        self, app, db, seed_user
    ):
        """register_user() detects duplicates even with different casing.

        Verifies that email lowercasing happens before the uniqueness
        check, so 'TEST@SHEKEL.LOCAL' conflicts with 'test@shekel.local'.
        """
        with app.app_context():
            with pytest.raises(ConflictError):
                registration_service.register_user(registration_spec(
                    email="TEST@SHEKEL.LOCAL", password="securepass123",
                    display_name="Case Test",
                ))

    def test_register_user_short_password_raises_validation(self, app, db):
        """register_user() raises ValidationError for passwords under 12 chars."""
        with app.app_context():
            with pytest.raises(ValidationError, match="at least 12 characters"):
                registration_service.register_user(registration_spec(
                    email="short@example.com", password="12345678901",
                    display_name="Short Test",
                ))

    def test_register_user_exactly_12_chars_succeeds(self, app, db):
        """register_user() accepts a password that is exactly 12 characters."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="exact@example.com", password="123456789012",
                display_name="Exact Test",
            ))
            db.session.flush()

            assert user.id is not None

    def test_register_user_invalid_email_no_at_sign(self, app, db):
        """register_user() rejects an email with no @ sign."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                registration_service.register_user(registration_spec(
                    email="notanemail", password="securepass123",
                    display_name="No At Test",
                ))

    def test_register_user_invalid_email_no_domain(self, app, db):
        """register_user() rejects an email with no domain after @."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                registration_service.register_user(registration_spec(
                    email="user@", password="securepass123",
                    display_name="No Domain Test",
                ))

    def test_register_user_invalid_email_no_tld(self, app, db):
        """register_user() rejects an email with no TLD (no dot in domain)."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                registration_service.register_user(registration_spec(
                    email="user@domain", password="securepass123",
                    display_name="No TLD Test",
                ))

    def test_register_user_invalid_email_spaces(self, app, db):
        """register_user() rejects an email with internal spaces.

        After stripping, 'user @example.com' still has an internal space
        which the regex rejects.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                registration_service.register_user(registration_spec(
                    email="user @example.com", password="securepass123",
                    display_name="Space Test",
                ))

    def test_register_user_empty_email_raises_validation(self, app, db):
        """register_user() raises ValidationError for an empty email."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Invalid email format"):
                registration_service.register_user(registration_spec(
                    email="", password="securepass123",
                    display_name="Empty Email Test",
                ))

    def test_register_user_empty_display_name_raises_validation(self, app, db):
        """register_user() raises ValidationError for an empty display name."""
        with app.app_context():
            with pytest.raises(ValidationError, match="Display name is required"):
                registration_service.register_user(registration_spec(
                    email="empty@example.com", password="securepass123",
                    display_name="",
                ))

    def test_register_user_whitespace_display_name_raises_validation(
        self, app, db
    ):
        """register_user() raises ValidationError for a whitespace-only display name.

        After stripping, the display name becomes empty and is rejected.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="Display name is required"):
                registration_service.register_user(registration_spec(
                    email="ws@example.com", password="securepass123",
                    display_name="   ",
                ))

    def test_register_user_does_not_commit(self, app, db):
        """register_user() does not commit the transaction.

        The caller (route) is responsible for committing. Rolling back
        after register_user() returns should discard the new user.
        """
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="nocommit@example.com", password="securepass123",
                display_name="No Commit",
            ))
            db.session.rollback()

            # The user should not exist after rollback.
            found = db.session.query(User).filter_by(
                email="nocommit@example.com"
            ).first()
            assert found is None

    def test_register_user_validation_order_email_before_password(
        self, app, db
    ):
        """register_user() validates email format before password length.

        When both email and password are invalid, the email error fires
        first because validation runs in a defined order.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="email") as exc_info:
                registration_service.register_user(registration_spec(
                    email="notvalid", password="short",
                    display_name="Order Test",
                ))
            # Confirm it is the email error, not the password error.
            assert "12 characters" not in str(exc_info.value)

    def test_register_user_creates_default_categories(self, app, db):
        """register_user() creates 24 default categories for the new user."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="cats@example.com", password="securepass123",
                display_name="Category Test",
            ))
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            assert len(categories) == 24

    def test_register_user_categories_have_correct_groups(self, app, db):
        """register_user() creates categories spanning all expected groups."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="groups@example.com", password="securepass123",
                display_name="Group Test",
            ))
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            groups = {c.group_name for c in categories}
            assert groups == {
                "Income", "Home", "Auto", "Family",
                "Health", "Financial", "Transfers", "Credit Card",
            }

    def test_register_user_categories_include_income_salary(self, app, db):
        """register_user() creates the Income: Salary category needed for salary profiles."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="salary@example.com", password="securepass123",
                display_name="Salary Cat Test",
            ))
            db.session.flush()

            salary_cat = db.session.query(Category).filter_by(
                user_id=user.id, group_name="Income", item_name="Salary"
            ).first()
            assert salary_cat is not None

    def test_default_categories_include_transfers(self, app, db):
        """DEFAULT_CATEGORIES list contains the two transfer categories."""
        from app.services.registration_service import DEFAULT_CATEGORIES
        assert ("Transfers", "Incoming") in DEFAULT_CATEGORIES
        assert ("Transfers", "Outgoing") in DEFAULT_CATEGORIES

    def test_seed_user_creates_transfer_categories(self, app, db):
        """register_user() creates both Transfers: Incoming and Transfers: Outgoing."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="xfer_cats@example.com", password="securepass123",
                display_name="Transfer Cat Test",
            ))
            db.session.flush()

            transfer_cats = db.session.query(Category).filter_by(
                user_id=user.id, group_name="Transfers"
            ).all()
            assert len(transfer_cats) == 2
            item_names = {c.item_name for c in transfer_cats}
            assert item_names == {"Incoming", "Outgoing"}

    def test_transfer_categories_have_valid_sort_order(self, app, db):
        """Transfer categories have unique, non-null sort_order values."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="xfer_sort@example.com", password="securepass123",
                display_name="Transfer Sort Test",
            ))
            db.session.flush()

            all_cats = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            all_orders = [c.sort_order for c in all_cats]

            # All sort_order values are non-null.
            assert all(o is not None for o in all_orders)
            # All sort_order values are unique (no collisions).
            assert len(all_orders) == len(set(all_orders))

            # Transfer categories specifically have valid sort_order.
            transfer_cats = [c for c in all_cats if c.group_name == "Transfers"]
            for cat in transfer_cats:
                assert cat.sort_order is not None

    def test_register_user_categories_have_sort_order(self, app, db):
        """register_user() assigns sequential sort_order to categories."""
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="sort@example.com", password="securepass123",
                display_name="Sort Test",
            ))
            db.session.flush()

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).order_by(Category.sort_order).all()
            orders = [c.sort_order for c in categories]
            assert orders == list(range(24))

    def test_register_user_categories_rollback_on_failure(self, app, db):
        """register_user() categories are discarded on transaction rollback."""
        with app.app_context():
            registration_service.register_user(registration_spec(
                email="rollback@example.com", password="securepass123",
                display_name="Rollback Test",
            ))
            db.session.rollback()

            count = db.session.query(Category).filter_by(
                group_name="Income", item_name="Salary"
            ).count()
            assert count == 0
