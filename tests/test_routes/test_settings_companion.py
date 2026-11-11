"""
Shekel Budget App -- Settings Companion Management Tests

Route-level integration tests for the companion account management
UI in the settings dashboard (OP-2).  Verifies form validation,
creation, editing, deactivation, reactivation, and the security
guards that protect these owner-only routes.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services.auth_service import verify_password
from app.exceptions import AuthError
from tests._sign_in import stranger_signs_in
from tests._test_helpers import figure_source_columns, generate_row_of, make_expense_template


# ── Helpers ──────────────────────────────────────────────────────────


def _companion_form(email="new@shekel.local", display_name="New Companion",
                    password="securepass123", confirm=None):
    """Build a valid companion create-form payload.

    Default values all pass validation; individual tests override the
    fields they want to exercise.
    """
    return {
        "email": email,
        "display_name": display_name,
        "password": password,
        "password_confirm": confirm if confirm is not None else password,
    }


def _find_companion(email):
    """Return the User row with the given email, or None."""
    return (
        db.session.query(User)
        .filter(db.func.lower(User.email) == email.lower())
        .first()
    )


# ── Create companion tests ───────────────────────────────────────────


class TestCreateCompanion:
    """POST /settings/companions creates a companion for the current owner."""

    def test_create_companion_with_valid_data(self, auth_client, db, seed_user):
        """Valid form creates the companion with correct role and owner link.

        Verifies that role_id is set to COMPANION, linked_owner_id
        points to the current user, password is stored as a bcrypt
        hash (not plaintext), is_active is True, and a UserSettings
        row was created alongside the User.
        """
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(
                email="Alice@Example.com",
                display_name="Alice",
                password="alicealice12",
            ),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(
            "/settings?section=companions"
        )

        # Email is normalized to lowercase.
        created = _find_companion("alice@example.com")
        assert created is not None
        assert created.email == "alice@example.com"
        assert created.display_name == "Alice"
        assert created.role_id == ref_cache.role_id(RoleEnum.COMPANION)
        assert created.linked_owner_id == seed_user["user"].id
        assert created.is_active is True
        # Password is hashed, never plaintext.
        assert created.password_hash != "alicealice12"
        assert verify_password("alicealice12", created.password_hash) is True

        # UserSettings created alongside the User.
        settings_row = (
            db.session.query(UserSettings)
            .filter_by(user_id=created.id)
            .first()
        )
        assert settings_row is not None

    def test_create_companion_rejects_duplicate_of_owner_email(
        self, auth_client, db, seed_user,
    ):
        """Cannot create a companion with the owner's own email address.

        Email uniqueness is enforced against ALL users, not just
        companions.  The owner's own email (seed_user: test@shekel.local)
        must be rejected.
        """
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(email="test@shekel.local"),
        )
        assert resp.status_code == 400
        assert b"already in use" in resp.data
        # No new user created.
        assert db.session.query(User).count() == 1

    def test_create_companion_rejects_duplicate_of_other_user_email(
        self, auth_client, db, seed_user, second_user,
    ):
        """Email uniqueness is enforced across all users, including other owners."""
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(email="other@shekel.local"),
        )
        assert resp.status_code == 400
        assert b"already in use" in resp.data
        # second_user still exists but no companion created.
        other = _find_companion("other@shekel.local")
        assert other is not None
        assert other.role_id != ref_cache.role_id(RoleEnum.COMPANION)

    def test_create_companion_rejects_duplicate_case_insensitive(
        self, auth_client, db, seed_user,
    ):
        """Email duplication check is case-insensitive.

        TEST@SHEKEL.LOCAL and test@shekel.local are the same address.
        """
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(email="TEST@SHEKEL.LOCAL"),
        )
        assert resp.status_code == 400
        assert b"already in use" in resp.data

    def test_create_companion_rejects_mismatched_passwords(
        self, auth_client, db, seed_user,
    ):
        """Password confirmation must match the password field."""
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(
                password="aaaaaaaaaaaa",
                confirm="bbbbbbbbbbbb",
            ),
        )
        assert resp.status_code == 400
        assert b"do not match" in resp.data
        assert _find_companion("new@shekel.local") is None

    def test_create_companion_rejects_short_password(
        self, auth_client, db, seed_user,
    ):
        """Passwords shorter than 12 characters are rejected."""
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(password="short", confirm="short"),
        )
        assert resp.status_code == 400
        # marshmallow validate.Length error message.
        assert b"Shorter than minimum" in resp.data or b"at least" in resp.data.lower()
        assert _find_companion("new@shekel.local") is None

    def test_create_companion_rejects_missing_email(
        self, auth_client, db, seed_user,
    ):
        """Missing email fails validation; no user created."""
        data = _companion_form()
        data["email"] = ""
        resp = auth_client.post("/settings/companions", data=data)
        assert resp.status_code == 400
        assert _find_companion("new@shekel.local") is None

    def test_create_companion_rejects_missing_display_name(
        self, auth_client, db, seed_user,
    ):
        """Missing display name fails validation; no user created."""
        data = _companion_form()
        data["display_name"] = ""
        resp = auth_client.post("/settings/companions", data=data)
        assert resp.status_code == 400
        assert _find_companion("new@shekel.local") is None

    def test_create_companion_rejects_invalid_email_format(
        self, auth_client, db, seed_user,
    ):
        """Bare strings without @ are rejected by the regex validator."""
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(email="not-an-email"),
        )
        assert resp.status_code == 400
        assert b"Invalid email format" in resp.data

    def test_create_companion_rejects_display_name_over_limit(
        self, auth_client, db, seed_user,
    ):
        """Display name longer than 100 characters (the column limit) is rejected."""
        long_name = "A" * 101
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(display_name=long_name),
        )
        assert resp.status_code == 400
        assert _find_companion("new@shekel.local") is None

    def test_create_companion_accepts_display_name_at_boundary(
        self, auth_client, db, seed_user,
    ):
        """Display name of exactly 100 characters is accepted (boundary)."""
        boundary_name = "B" * 100
        resp = auth_client.post(
            "/settings/companions",
            data=_companion_form(display_name=boundary_name),
        )
        assert resp.status_code == 302
        created = _find_companion("new@shekel.local")
        assert created is not None
        assert created.display_name == boundary_name

    def test_create_companion_created_can_authenticate(
        self, app, auth_client, db, seed_user,
    ):
        """Companion created via settings can log in with the chosen password.

        Signs in as a stranger's browser does with the companion's new
        credentials and verifies that (a) the call succeeds, (b) the
        returned user has the companion role, and (c) is_active is True.
        """
        auth_client.post(
            "/settings/companions",
            data=_companion_form(
                email="login@shekel.local",
                password="loginpassword12",
            ),
        )
        with app.app_context():
            user = stranger_signs_in("login@shekel.local", "loginpassword12")
            assert user.role_id == ref_cache.role_id(RoleEnum.COMPANION)
            assert user.is_active is True


# ── Edit companion tests ─────────────────────────────────────────────


class TestEditCompanion:
    """POST /settings/companions/<id>/edit updates fields."""

    def test_edit_companion_display_name(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Updating display_name writes the new value and preserves others."""
        comp_id = seed_companion["user"].id
        original_hash = seed_companion["user"].password_hash

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": seed_companion["user"].email,
                "display_name": "Renamed Companion",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.display_name == "Renamed Companion"
        assert updated.email == seed_companion["user"].email
        assert updated.password_hash == original_hash

    def test_edit_companion_email_to_new_unique(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Changing email to an unused address succeeds."""
        comp_id = seed_companion["user"].id

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": "renamed@shekel.local",
                "display_name": "Companion User",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.email == "renamed@shekel.local"

    def test_edit_companion_email_to_duplicate_rejected(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Cannot rename a companion to another user's email."""
        comp_id = seed_companion["user"].id

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": "test@shekel.local",
                "display_name": "Companion User",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 400
        assert b"already in use" in resp.data

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.email == "companion@shekel.local"

    def test_edit_companion_allows_keeping_own_email(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Submitting the same email without changes is allowed.

        The edit uniqueness check excludes the companion's own row,
        so a no-op email change should pass.
        """
        comp_id = seed_companion["user"].id

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": "companion@shekel.local",
                "display_name": "Updated Name",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.display_name == "Updated Name"
        assert updated.email == "companion@shekel.local"

    def test_edit_companion_password_updates_hash(
        self, app, auth_client, db, seed_user, seed_companion,
    ):
        """Setting a new password changes the hash; old password stops working."""
        comp_id = seed_companion["user"].id
        original_hash = seed_companion["user"].password_hash

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": seed_companion["user"].email,
                "display_name": seed_companion["user"].display_name,
                "password": "brandnewpass12",
                "password_confirm": "brandnewpass12",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.password_hash != original_hash

        # New password authenticates.
        with app.app_context():
            user = stranger_signs_in(updated.email, "brandnewpass12")
            assert user.id == comp_id

            # Old password no longer works.
            with pytest.raises(AuthError):
                stranger_signs_in(updated.email, "companionpass")

    def test_edit_companion_blank_password_keeps_hash(
        self, app, auth_client, db, seed_user, seed_companion,
    ):
        """Leaving password blank keeps the existing hash unchanged."""
        comp_id = seed_companion["user"].id
        original_hash = seed_companion["user"].password_hash

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": seed_companion["user"].email,
                "display_name": "Different Name",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.password_hash == original_hash
        # Old password still works.
        with app.app_context():
            user = stranger_signs_in(updated.email, "companionpass")
            assert user.id == comp_id

    def test_edit_companion_password_change_sets_session_invalidated_at(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Changing the password stamps session_invalidated_at.

        This forces any active companion sessions to re-login on
        their next request, matching the owner's change_password flow.
        """
        comp_id = seed_companion["user"].id
        assert seed_companion["user"].session_invalidated_at is None

        auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": seed_companion["user"].email,
                "display_name": seed_companion["user"].display_name,
                "password": "sessionbustpwd",
                "password_confirm": "sessionbustpwd",
            },
        )

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.session_invalidated_at is not None

    def test_edit_companion_rejects_mismatched_password_confirm(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Mismatched password_confirm fails validation."""
        comp_id = seed_companion["user"].id
        original_hash = seed_companion["user"].password_hash

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": seed_companion["user"].email,
                "display_name": seed_companion["user"].display_name,
                "password": "newpassword12",
                "password_confirm": "differentpass",
            },
        )
        assert resp.status_code == 400
        assert b"do not match" in resp.data

        db.session.expire_all()
        assert db.session.get(User, comp_id).password_hash == original_hash

    def test_edit_companion_rejects_short_password(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Short passwords rejected on edit too."""
        comp_id = seed_companion["user"].id
        original_hash = seed_companion["user"].password_hash

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/edit",
            data={
                "email": seed_companion["user"].email,
                "display_name": seed_companion["user"].display_name,
                "password": "short",
                "password_confirm": "short",
            },
        )
        assert resp.status_code == 400

        db.session.expire_all()
        assert db.session.get(User, comp_id).password_hash == original_hash


# ── Deactivate / Reactivate tests ────────────────────────────────────


class TestDeactivateCompanion:
    """POST /settings/companions/<id>/deactivate flips is_active to False."""

    def test_deactivate_sets_is_active_false(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Deactivation sets is_active to False and stamps session_invalidated_at."""
        comp_id = seed_companion["user"].id

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/deactivate",
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.is_active is False
        assert updated.session_invalidated_at is not None

    def test_deactivate_prevents_login(
        self, app, auth_client, db, seed_user, seed_companion,
    ):
        """Deactivated companions cannot authenticate through auth_service."""
        comp_id = seed_companion["user"].id

        auth_client.post(f"/settings/companions/{comp_id}/deactivate")

        with app.app_context():
            # A stranger's sign-in raises AuthError for inactive users.
            with pytest.raises(AuthError, match="disabled"):
                stranger_signs_in("companion@shekel.local", "companionpass")

    def test_deactivate_preserves_entries(
        self, app, db, auth_client, seed_user, seed_companion, seed_periods_today,
    ):
        """Deactivating a companion leaves their TransactionEntries intact.

        This is the safety guarantee that justifies soft-delete over
        hard-delete: credit card paybacks stay in sync because the
        underlying entries persist.
        """
        # Seed a definition, its engine-generated row and an entry by the
        # companion.
        comp = seed_companion["user"]
        template = make_expense_template(
            db.session, seed_user, amount="500.00", name="Groceries",
            category_key=next(iter(seed_user["categories"])),
            is_envelope=True, companion_visible=True,
        )
        txn = generate_row_of(template, seed_periods_today[0])

        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=comp.id,
            amount=Decimal("42.50"),
            description="Kroger",
            purchased_on=date.today(),
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id

        # Now deactivate the companion.
        resp = auth_client.post(
            f"/settings/companions/{comp.id}/deactivate",
        )
        assert resp.status_code == 302

        # Entry still exists with the original data.
        db.session.expire_all()
        preserved = db.session.get(TransactionEntry, entry_id)
        assert preserved is not None
        assert preserved.amount == Decimal("42.50")
        assert preserved.user_id == comp.id

    def test_deactivate_idempotent(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Deactivating an already-deactivated companion returns 302 and no-ops."""
        comp_id = seed_companion["user"].id
        auth_client.post(f"/settings/companions/{comp_id}/deactivate")

        db.session.expire_all()
        first_stamp = db.session.get(User, comp_id).session_invalidated_at

        # Second deactivation should be a no-op.
        resp = auth_client.post(
            f"/settings/companions/{comp_id}/deactivate",
        )
        assert resp.status_code == 302

        db.session.expire_all()
        second = db.session.get(User, comp_id)
        assert second.is_active is False
        # Timestamp is not updated on no-op.
        assert second.session_invalidated_at == first_stamp


class TestReactivateCompanion:
    """POST /settings/companions/<id>/reactivate restores access."""

    def test_reactivate_sets_is_active_true(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Reactivation flips is_active back to True."""
        comp_id = seed_companion["user"].id

        # Deactivate first.
        auth_client.post(f"/settings/companions/{comp_id}/deactivate")

        # Reactivate.
        resp = auth_client.post(
            f"/settings/companions/{comp_id}/reactivate",
        )
        assert resp.status_code == 302

        db.session.expire_all()
        updated = db.session.get(User, comp_id)
        assert updated.is_active is True

    def test_reactivated_companion_can_login(
        self, app, auth_client, db, seed_user, seed_companion,
    ):
        """After reactivation the companion can log in with the old password."""
        comp_id = seed_companion["user"].id
        auth_client.post(f"/settings/companions/{comp_id}/deactivate")
        auth_client.post(f"/settings/companions/{comp_id}/reactivate")

        with app.app_context():
            user = stranger_signs_in("companion@shekel.local", "companionpass")
            assert user.id == comp_id
            assert user.is_active is True

    def test_reactivate_preserves_session_invalidated_at(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Reactivation does NOT clear session_invalidated_at.

        Old sessions from before deactivation must stay invalid --
        the companion should log in fresh, not rehydrate a stale
        session.
        """
        comp_id = seed_companion["user"].id

        auth_client.post(f"/settings/companions/{comp_id}/deactivate")
        db.session.expire_all()
        deactivation_stamp = db.session.get(User, comp_id).session_invalidated_at
        assert deactivation_stamp is not None

        auth_client.post(f"/settings/companions/{comp_id}/reactivate")
        db.session.expire_all()
        stamp_after = db.session.get(User, comp_id).session_invalidated_at
        assert stamp_after == deactivation_stamp

    def test_reactivate_idempotent(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Reactivating an already-active companion is a no-op."""
        comp_id = seed_companion["user"].id

        resp = auth_client.post(
            f"/settings/companions/{comp_id}/reactivate",
        )
        assert resp.status_code == 302

        db.session.expire_all()
        assert db.session.get(User, comp_id).is_active is True


# ── Security / guard tests ───────────────────────────────────────────


class TestCompanionMgmtGuards:
    """Route-level authorization on the companion management endpoints."""

    def test_companion_cannot_access_settings_page(self, companion_client):
        """A logged-in companion cannot access /settings (blocked by @require_owner)."""
        resp = companion_client.get("/settings")
        assert resp.status_code == 404

    def test_companion_cannot_create_other_companion(self, companion_client):
        """A companion cannot POST to /settings/companions (blocked by @require_owner)."""
        resp = companion_client.post(
            "/settings/companions",
            data=_companion_form(),
        )
        assert resp.status_code == 404

    def test_companion_cannot_edit_themselves(
        self, companion_client, seed_companion,
    ):
        """Companions cannot POST to /settings/companions/<id>/edit on themselves."""
        resp = companion_client.post(
            f"/settings/companions/{seed_companion['user'].id}/edit",
            data={
                "email": "x@y.z",
                "display_name": "X",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 404

    def test_companion_cannot_deactivate_themselves(
        self, companion_client, seed_companion,
    ):
        """Companions cannot self-deactivate."""
        resp = companion_client.post(
            f"/settings/companions/{seed_companion['user'].id}/deactivate",
        )
        assert resp.status_code == 404

    def test_unauthenticated_request_redirected_to_login(self, client):
        """Unauthenticated POST to companion routes redirects to login (not 404)."""
        resp = client.post(
            "/settings/companions",
            data=_companion_form(),
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_owner_cannot_edit_another_owner(
        self, auth_client, db, seed_user, second_user,
    ):
        """Editing a non-companion user via the companion route returns 404.

        The target's role_id is OWNER, which fails the
        _load_companion_or_404 guard.  This prevents using the
        companion route to modify any owner account.
        """
        other_owner_id = second_user["user"].id
        resp = auth_client.post(
            f"/settings/companions/{other_owner_id}/edit",
            data={
                "email": "hacked@shekel.local",
                "display_name": "Hacked",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(User, other_owner_id).email == "other@shekel.local"

    def test_owner_cannot_edit_self_via_companion_route(
        self, auth_client, db, seed_user,
    ):
        """The owner cannot edit their own user row through the companion route.

        current_user has role_id OWNER, not COMPANION, so the guard
        returns 404.  This prevents accidental self-edit via a
        forged companion id.
        """
        self_id = seed_user["user"].id
        resp = auth_client.post(
            f"/settings/companions/{self_id}/edit",
            data={
                "email": "hacked@shekel.local",
                "display_name": "Hacked",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(User, self_id).email == "test@shekel.local"

    def test_owner_cannot_deactivate_self_via_companion_route(
        self, auth_client, db, seed_user,
    ):
        """The owner cannot deactivate themselves through the companion route."""
        self_id = seed_user["user"].id
        resp = auth_client.post(
            f"/settings/companions/{self_id}/deactivate",
        )
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(User, self_id).is_active is True

    def test_owner_cannot_edit_other_owners_companion(
        self, app, db, auth_client, seed_user, second_user,
    ):
        """One owner cannot edit a companion linked to another owner.

        _load_companion_or_404 rejects any target whose
        linked_owner_id does not match current_user.id.
        """
        # Create a companion owned by second_user (not the auth_client owner).
        from app.services.auth_service import hash_password  # noqa

        foreign_companion = User(
            email="foreign@shekel.local",
            password_hash=hash_password("foreignpass"),
            display_name="Foreign Companion",
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
            linked_owner_id=second_user["user"].id,
        )
        db.session.add(foreign_companion)
        db.session.flush()
        db.session.add(UserSettings(user_id=foreign_companion.id))
        db.session.commit()
        foreign_id = foreign_companion.id

        # auth_client (seed_user owner) tries to edit.
        resp = auth_client.post(
            f"/settings/companions/{foreign_id}/edit",
            data={
                "email": "hacked@shekel.local",
                "display_name": "Hacked",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(User, foreign_id).email == "foreign@shekel.local"
        assert db.session.get(User, foreign_id).display_name == "Foreign Companion"

    def test_owner_cannot_deactivate_other_owners_companion(
        self, auth_client, db, seed_user, second_user,
    ):
        """Cross-owner deactivation is blocked by linked_owner_id guard."""
        from app.services.auth_service import hash_password  # noqa

        foreign_companion = User(
            email="foreign@shekel.local",
            password_hash=hash_password("foreignpass"),
            display_name="Foreign Companion",
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
            linked_owner_id=second_user["user"].id,
        )
        db.session.add(foreign_companion)
        db.session.flush()
        db.session.add(UserSettings(user_id=foreign_companion.id))
        db.session.commit()
        foreign_id = foreign_companion.id

        resp = auth_client.post(
            f"/settings/companions/{foreign_id}/deactivate",
        )
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(User, foreign_id).is_active is True

    def test_nonexistent_companion_returns_404(
        self, auth_client, db, seed_user,
    ):
        """Editing a companion id that does not exist returns 404."""
        resp = auth_client.post(
            "/settings/companions/999999/edit",
            data={
                "email": "x@y.z",
                "display_name": "X",
                "password": "",
                "password_confirm": "",
            },
        )
        assert resp.status_code == 404


# ── Settings page rendering ──────────────────────────────────────────


class TestCompanionSectionRendering:
    """GET /settings?section=companions renders the companion list."""

    def test_section_renders_empty_state(self, auth_client, db, seed_user):
        """With no companions, the section shows the empty-state message."""
        resp = auth_client.get("/settings?section=companions")
        assert resp.status_code == 200
        assert b"Companion Accounts" in resp.data
        assert b"No active companion accounts" in resp.data

    def test_section_renders_companion_row(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Existing companion appears in the active list with email and name."""
        resp = auth_client.get("/settings?section=companions")
        assert resp.status_code == 200
        assert b"companion@shekel.local" in resp.data
        assert b"Companion User" in resp.data

    def test_section_renders_inactive_companion(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Deactivated companions appear in the inactive (collapsed) section."""
        comp_id = seed_companion["user"].id
        auth_client.post(f"/settings/companions/{comp_id}/deactivate")

        resp = auth_client.get("/settings?section=companions")
        assert resp.status_code == 200
        assert b"Inactive" in resp.data
        assert b"Deactivated" in resp.data
        assert b"Reactivate" in resp.data

    def test_edit_query_param_renders_edit_form(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """?edit=<id> renders the inline edit form with pre-filled values."""
        comp_id = seed_companion["user"].id
        resp = auth_client.get(
            f"/settings?section=companions&edit={comp_id}",
        )
        assert resp.status_code == 200
        assert b"Edit Companion Account" in resp.data
        assert b"value=\"companion@shekel.local\"" in resp.data
        assert b"value=\"Companion User\"" in resp.data

    def test_edit_query_param_ignores_invalid_id(
        self, auth_client, db, seed_user,
    ):
        """?edit=invalid or ?edit=<nonexistent> renders the create form instead."""
        resp = auth_client.get(
            "/settings?section=companions&edit=nonnumeric",
        )
        assert resp.status_code == 200
        assert b"Create Companion Account" in resp.data

        resp = auth_client.get("/settings?section=companions&edit=99999")
        assert resp.status_code == 200
        assert b"Create Companion Account" in resp.data

    def test_edit_query_param_ignores_a_digit_int_cannot_convert(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """Finding N-136: this door 500'd, but only for an owner WITH companions.

        The case ``edit=nonnumeric`` above never reached: ``str.isdigit()``
        is true for 888 characters and 128 of them make ``int()`` raise, so
        a superscript two passed the guard and raised out of the conversion.
        Reachable only with at least one active companion, because the
        ``int()`` sat inside the generator's predicate and an empty list
        never evaluates it -- which is why ``seed_companion`` is the whole
        point of this test and its sibling above does without.
        """
        resp = auth_client.get(
            "/settings?section=companions&edit=\N{SUPERSCRIPT TWO}",
        )
        assert resp.status_code == 200
        assert b"Create Companion Account" in resp.data
        assert b"Edit Companion Account" not in resp.data

    def test_edit_query_param_ignores_an_id_in_another_digit_script(
        self, auth_client, db, seed_user, seed_companion,
    ):
        """One companion id has ONE spelling (X-ae's ASCII ruling).

        Eastern Arabic numerals pass ``isdigit()`` and convert cleanly, so a
        crash-only fix would have opened this companion's edit form from a
        spelling no link of ours emits.
        """
        comp_id = seed_companion["user"].id
        eastern_arabic = str(comp_id).translate(
            str.maketrans("0123456789", "٠١٢٣٤"
                                        "٥٦٧٨٩"),
        )
        assert int(eastern_arabic) == comp_id

        resp = auth_client.get(
            f"/settings?section=companions&edit={eastern_arabic}",
        )
        assert resp.status_code == 200
        assert b"Create Companion Account" in resp.data
        assert b"Edit Companion Account" not in resp.data

    def test_edit_query_param_ignores_foreign_companion(
        self, auth_client, db, seed_user, second_user,
    ):
        """Cannot open the edit form for another owner's companion via ?edit=<id>."""
        from app.services.auth_service import hash_password  # noqa

        foreign = User(
            email="foreign@shekel.local",
            password_hash=hash_password("foreignpass"),
            display_name="Foreign",
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
            linked_owner_id=second_user["user"].id,
        )
        db.session.add(foreign)
        db.session.flush()
        db.session.add(UserSettings(user_id=foreign.id))
        db.session.commit()

        resp = auth_client.get(
            f"/settings?section=companions&edit={foreign.id}",
        )
        assert resp.status_code == 200
        # Falls back to create form because the foreign row is never
        # loaded into active_companions for the current user.
        assert b"Create Companion Account" in resp.data
        # The foreign email is NOT exposed on this owner's page.
        assert b"foreign@shekel.local" not in resp.data


# ── Regression tests ─────────────────────────────────────────────────


class TestSettingsRegression:
    """Existing settings functionality is unaffected by the companion section."""

    def test_general_section_still_loads(self, auth_client):
        """?section=general continues to render."""
        resp = auth_client.get("/settings?section=general")
        assert resp.status_code == 200
        assert b"General Settings" in resp.data

    def test_security_section_still_loads(self, auth_client):
        """?section=security continues to render."""
        resp = auth_client.get("/settings?section=security")
        assert resp.status_code == 200
        assert b"Change Password" in resp.data

    def test_settings_update_still_works(self, auth_client, db, seed_user):
        """POST /settings (user preferences update) is unaffected."""
        resp = auth_client.post("/settings", data={
            "grid_default_periods": "8",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        user = db.session.get(User, seed_user["user"].id)
        assert user.settings.grid_default_periods == 8

    def test_companions_sidebar_link_present(self, auth_client):
        """The sidebar exposes a link to the new companions section."""
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        assert b"section=companions" in resp.data
        assert b"Companions" in resp.data
