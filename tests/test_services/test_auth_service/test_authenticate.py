"""Auth service tests: authenticating, changing a password, and their edge paths."""

import pytest

from app.models.user import User
from app.services import auth_service
from app.exceptions import AuthError, ValidationError
from tests._sign_in import stranger_signs_in


class TestAuthenticate:
    """A stranger's sign-in password (``check_strangers_password``, then its confirm)."""

    def test_authenticate_returns_user_on_valid_credentials(
        self, app, db, seed_user
    ):
        """A stranger's sign-in returns the User object for valid email + password."""
        with app.app_context():
            user = stranger_signs_in("test@shekel.local", "testpass")

            assert user.id == seed_user["user"].id
            assert user.email == "test@shekel.local"

    def test_authenticate_raises_auth_error_on_wrong_email(
        self, app, db, seed_user
    ):
        """A stranger's sign-in raises AuthError when the email does not exist."""
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                stranger_signs_in("nobody@shekel.local", "testpass")

    def test_authenticate_raises_auth_error_on_wrong_password(
        self, app, db, seed_user
    ):
        """A stranger's sign-in raises AuthError when the password is wrong."""
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                stranger_signs_in("test@shekel.local", "wrongpass")

    def test_authenticate_unknown_email_runs_timing_equalization_bcrypt(
        self, app, db, seed_user, monkeypatch
    ):
        """An unknown email pays the same bcrypt cost as a wrong password.

        deep-quality-hunt #27: without timing equalization the no-user
        branch returns before any bcrypt work while the wrong-password
        branch runs a full bcrypt.checkpw, leaking account existence via
        response timing despite the constant response message.
        ``check_strangers_password`` runs one throwaway verify_password
        against the fixed dummy hash on the no-user branch.  This pins that the dummy
        verification actually happens (by spying on verify_password)
        rather than measuring wall-clock time, which would be flaky.
        """
        with app.app_context():
            calls = []

            def _spy(plain, password_hash):
                calls.append((plain, password_hash))
                return False

            monkeypatch.setattr(auth_service, "verify_password", _spy)

            with pytest.raises(
                AuthError, match="Invalid email or password"
            ):
                stranger_signs_in("nobody@shekel.local", "anypw")

            # Exactly one dummy verification ran, using the fixed
            # timing-equalization hash and the submitted password.
            assert len(calls) == 1
            assert calls[0] == ("anypw", auth_service._DUMMY_PASSWORD_HASH)

    def test_dummy_password_hash_is_valid_bcrypt(self):
        """The timing-equalization dummy hash is a real bcrypt digest.

        deep-quality-hunt #27: the equalization only closes the timing
        oracle if bcrypt.checkpw actually runs against the dummy hash.  A
        malformed hash would hit verify_password's ValueError fast-path
        and return immediately, leaving the no-user branch fast again.
        Pin that the constant is a parseable 60-char bcrypt hash and that
        verify_password runs it to completion (returns False, no raise).
        """
        dummy = auth_service._DUMMY_PASSWORD_HASH
        assert isinstance(dummy, str)
        assert dummy.startswith("$2")
        assert len(dummy) == 60
        # Real bcrypt work: a non-matching password verifies to False
        # without raising, proving checkpw parsed the hash.
        assert auth_service.verify_password("not-the-password", dummy) is False

    def test_authenticate_raises_auth_error_on_disabled_account(
        self, app, db, seed_user
    ):
        """A stranger's sign-in raises AuthError when the account is disabled."""
        with app.app_context():
            # Disable the user account.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.flush()

            with pytest.raises(AuthError, match="Account is disabled"):
                stranger_signs_in("test@shekel.local", "testpass")


class TestChangePassword:
    """Tests for auth_service.change_password()."""

    def test_change_password_success(self, app, db, seed_user):
        """change_password() updates the password hash."""
        with app.app_context():
            user = seed_user["user"]
            auth_service.change_password(user, "testpass", "newpassword12", device=None)
            db.session.flush()

            # Verify the new password works and old one does not.
            assert auth_service.verify_password("newpassword12", user.password_hash)
            assert not auth_service.verify_password("testpass", user.password_hash)

    def test_change_password_wrong_current_raises(self, app, db, seed_user):
        """change_password() raises AuthError for wrong current password."""
        with app.app_context():
            user = seed_user["user"]
            with pytest.raises(AuthError, match="Current password is incorrect"):
                auth_service.change_password(user, "wrongpass", "newpassword12", device=None)

    def test_change_password_too_short_raises(self, app, db, seed_user):
        """change_password() raises ValidationError for short password."""
        with app.app_context():
            user = seed_user["user"]
            with pytest.raises(ValidationError, match="at least 12 characters"):
                auth_service.change_password(user, "testpass", "short", device=None)


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for auth service functions.

    Covers: empty/long passwords, None inputs to a sign-in, password
    change boundary conditions, and bcrypt truncation behavior.
    """

    def test_hash_password_empty_string(self):
        """Hashing an empty string returns a valid bcrypt hash, but
        the matching verify call now rejects the empty plaintext.

        ``hash_password`` remains permissive so the function can be
        unit-tested in isolation without seeding a user row.
        ``verify_password`` was tightened in commit C-44 (audit
        finding F-083): an empty plaintext can only reach the
        verification path when the caller is broken because every
        authentication route validates ``min=1`` via Marshmallow
        before calling ``verify_password``.  Failing closed at the
        service layer turns "caller bug feeds empty string to
        verify" from a 500 round-trip into a deterministic auth
        rejection.
        """
        hashed = auth_service.hash_password("")
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60

        # Post-C-44 asymmetry: hash_password accepts the empty
        # plaintext (so this test can stay self-contained), but
        # verify_password refuses to round-trip it.
        assert auth_service.verify_password("", hashed) is False

    def test_hash_password_long_bcrypt_limit(self):
        """hash_password() rejects passwords exceeding 72 bytes.

        bcrypt only processes the first 72 bytes of a password. Rather than
        silently truncating, we raise a ValidationError so the user knows
        their full password is not being used.
        """
        long_password = "a" * 73  # One byte over bcrypt's 72-byte limit.
        with pytest.raises(ValidationError, match="72 characters or fewer"):
            auth_service.hash_password(long_password)

    def test_hash_password_exactly_72_bytes(self):
        """A password of exactly 72 bytes is accepted."""
        password_72 = "a" * 72
        hashed = auth_service.hash_password(password_72)
        assert hashed.startswith("$2b$")
        assert auth_service.verify_password(password_72, hashed) is True

    def test_authenticate_none_email(self, app, db, seed_user):
        """Authenticating with None email raises AuthError, not a TypeError.

        A malformed request could pass None. The service must raise AuthError,
        not an AttributeError or TypeError. No user has email=None, and the
        stranger's check refuses an email that is not a non-empty string
        before computing its record's digest, with the generic error.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                stranger_signs_in(None, "testpass")

    def test_authenticate_none_password(self, app, db, seed_user):
        """Authenticating with None password raises AuthError, not AttributeError.

        verify_password() guards against None input by returning False,
        which causes the sign-in to raise AuthError normally.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                stranger_signs_in("test@shekel.local", None)

    def test_change_password_new_equals_old(self, app, db, seed_user):
        """Changing to the same password succeeds -- no password-history enforcement.

        The service does not prevent changing to the same password. This is a
        UX concern, not a security one. bcrypt generates a new salt each time,
        so the hash differs even though the password is identical.
        """
        with app.app_context():
            user = seed_user["user"]
            # First change to a valid-length password.
            auth_service.change_password(user, "testpass", "validpass1234", device=None)
            db.session.flush()
            old_hash = user.password_hash

            # Now change to the same password.
            auth_service.change_password(user, "validpass1234", "validpass1234", device=None)
            db.session.flush()

            # The hash changed (new salt) even though the password is the same.
            assert user.password_hash != old_hash
            assert auth_service.verify_password("validpass1234", user.password_hash) is True

    def test_change_password_exactly_12_characters(self, app, db, seed_user):
        """A new password of exactly 12 characters passes the length check.

        Off-by-one on the minimum length is a classic validation bug. The
        guard is ``len(new_password) < 12``, so exactly 12 should pass.
        """
        with app.app_context():
            user = seed_user["user"]
            # "exactly12chr" is exactly 12 characters.
            auth_service.change_password(user, "testpass", "exactly12chr", device=None)
            db.session.flush()

            assert auth_service.verify_password("exactly12chr", user.password_hash) is True

    def test_change_password_11_characters_raises(self, app, db, seed_user):
        """A new password of 11 characters raises ValidationError.

        Ensures the 12-character boundary is enforced correctly.
        """
        with app.app_context():
            user = seed_user["user"]
            with pytest.raises(ValidationError, match="at least 12 characters"):
                auth_service.change_password(user, "testpass", "short11char", device=None)

    def test_authenticate_empty_string_email(self, app, db, seed_user):
        """Authenticating with empty-string email raises AuthError.

        Empty string is different from None. Both must be handled.
        No user has an empty email, and the stranger's check refuses one
        before any check, with the generic AuthError.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                stranger_signs_in("", "testpass")
