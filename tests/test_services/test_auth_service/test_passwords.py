"""Auth service tests: hashing a password and verifying one."""

from decimal import Decimal

import pytest

from app.services import auth_service
from app.exceptions import AuthError


class TestHashPassword:
    """Tests for the hash_password() function."""

    def test_hash_password_returns_bcrypt_hash(self):
        """hash_password() returns a valid bcrypt hash string."""
        hashed = auth_service.hash_password("mypassword")

        # bcrypt hashes start with $2b$ (or $2a$) and are 60 chars.
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60


class TestVerifyPassword:
    """Tests for the verify_password() function."""

    def test_verify_password_returns_true_for_correct_password(self):
        """verify_password() returns True when the password matches the hash."""
        hashed = auth_service.hash_password("correcthorse")

        assert auth_service.verify_password("correcthorse", hashed) is True

    def test_verify_password_returns_false_for_wrong_password(self):
        """verify_password() returns False when the password does not match."""
        hashed = auth_service.hash_password("correcthorse")

        assert auth_service.verify_password("wronghorse", hashed) is False


class TestVerifyPasswordC44Hardening:
    """Type and value guards added by commit C-44 / audit finding F-083.

    Before C-44, ``verify_password`` short-circuited only when
    ``plain_password is None``.  Any other falsy-but-not-None value
    (empty string, ``bytes``, ``int``, ``Decimal``, ``list``, ...)
    reached ``.encode("utf-8")`` and raised ``AttributeError`` that
    propagated as a Flask 500.  C-44 tightens the guard so every
    non-string and every empty-string input returns ``False``, and
    a stored hash that does not parse as bcrypt returns ``False``
    instead of letting ``bcrypt.checkpw`` raise ``ValueError``.

    Each test below feeds one of those previously-crashing inputs
    and asserts the new defensive contract.  Together they pin the
    rule that ``verify_password`` is ``False`` on every malformed
    call -- the indistinguishability is load-bearing: an attacker
    probing with exotic payloads must not be able to use response
    shape to fingerprint caller-side bugs.
    """

    def test_non_string_plain_password_returns_false_without_raising(self):
        """Every non-string ``plain_password`` payload returns ``False``.

        The audit finding lists ``bytes``, ``int``, and ``Decimal``
        as the realistic accidental-caller cases (the original guard
        passed all three straight through to ``.encode("utf-8")``).
        ``list`` and ``dict`` extend the coverage to container types
        that a caller might splat from a request payload.  ``None``
        is already covered by the existing
        :class:`TestNegativeAndBoundaryPaths` suite but is included
        here so this class also pins it as a contract rather than
        an incidental of the previous implementation.
        """
        hashed = auth_service.hash_password("validpass1234")
        for bad_plain in (
            None,
            b"validpass1234",
            bytearray(b"validpass1234"),
            12345,
            Decimal("1.0"),
            ["validpass1234"],
            {"password": "validpass1234"},
            object(),
        ):
            assert auth_service.verify_password(bad_plain, hashed) is False, (
                f"verify_password({bad_plain!r}, <valid hash>) should return "
                f"False; type={type(bad_plain).__name__}"
            )

    def test_empty_plain_password_returns_false(self):
        """An empty plaintext returns ``False`` even against the matching hash.

        Pre-C-44 the empty string round-tripped because
        ``"".encode("utf-8") == b""`` and ``bcrypt.checkpw(b"", <hash
        of "">)`` returns ``True``.  Post-C-44 the empty plaintext
        is rejected before bcrypt -- every authentication route
        validates ``min=1`` in its Marshmallow schema, so an empty
        plaintext can only arrive when the caller is broken.
        """
        hashed = auth_service.hash_password("")
        # The hash exists and is well-formed.
        assert hashed.startswith("$2b$")
        # But verify rejects the empty plaintext.
        assert auth_service.verify_password("", hashed) is False

    def test_non_string_password_hash_returns_false_without_raising(self):
        """Every non-string ``password_hash`` payload returns ``False``.

        A non-string stored hash is not a realistic data path
        (``User.password_hash`` is a ``String`` column) but the
        guard hardens against a future caller that fabricates a
        ``password_hash`` argument by accident.  Returning ``False``
        keeps the behaviour symmetric with the plaintext guard --
        every malformed call yields the same result regardless of
        which argument was bad.
        """
        for bad_hash in (
            None,
            b"$2b$12$abcdefghijklmnopqrstuvwxyz0123456789abcdef0123456789abcdef",
            12345,
            Decimal("1.0"),
            ["$2b$12$..."],
            {"hash": "$2b$12$..."},
            object(),
        ):
            assert auth_service.verify_password("validpass1234", bad_hash) is False, (
                f"verify_password(<valid plain>, {bad_hash!r}) should return "
                f"False; type={type(bad_hash).__name__}"
            )

    def test_empty_password_hash_returns_false(self):
        """An empty ``password_hash`` returns ``False`` before reaching bcrypt.

        Empty string is a valid ``str`` so the ``isinstance`` guard
        would pass it through; the explicit ``not password_hash``
        clause keeps the call from reaching ``bcrypt.checkpw`` where
        it would raise ``ValueError`` ("Invalid salt").  Catching
        the ``ValueError`` further down would also suffice but the
        cheaper short-circuit is preferred.
        """
        assert auth_service.verify_password("validpass1234", "") is False

    def test_malformed_password_hash_returns_false_without_raising(self):
        """A non-bcrypt ``password_hash`` returns ``False`` via the ValueError catch.

        Strings that look like hashes but do not parse as bcrypt
        (corrupted DB row, hash from an older incompatible scheme,
        a manual operator typo) raise ``ValueError`` ("Invalid
        salt") inside ``bcrypt.checkpw``.  The catch in
        ``verify_password`` converts that to ``False`` so a single
        corrupted row cannot crash the login flow with a 500.
        """
        for malformed_hash in (
            "not-a-bcrypt-hash",
            "$1$old-md5-crypt$abcdefg",  # MD5-crypt prefix.
            "$5$sha256-crypt$abc",        # SHA-256-crypt prefix.
            "$6$sha512-crypt$abc",        # SHA-512-crypt prefix.
            "$argon2id$v=19$m=65536,t=3,p=4$abc$def",  # argon2id prefix.
            "$2b$",                       # Truncated bcrypt prefix.
            "$2b$ab$",                    # Bad cost factor.
            " " * 60,                     # All-whitespace.
            "x" * 60,                     # Right length, wrong content.
        ):
            assert auth_service.verify_password("validpass1234", malformed_hash) is False, (
                f"verify_password(<valid plain>, {malformed_hash!r}) "
                f"should return False"
            )

    def test_valid_inputs_still_round_trip(self):
        """The happy path is unchanged by the C-44 guards.

        Regression guard: every hardening pass risks tightening the
        rules so far that the legitimate success case stops working.
        Each pair below covers a different boundary that the suite
        elsewhere asserts in isolation; folding them into a single
        test pins the contract that the guards do not interfere with
        any of the previously-passing inputs.
        """
        cases = [
            ("a", "single character"),
            ("validpass1234", "typical password"),
            ("a" * 72, "exactly the bcrypt 72-byte ceiling"),
            ("p@ssw0rd_with_unicode_£€¥", "non-ASCII characters"),
            ("password with spaces", "internal whitespace"),
            ("emoji-password-\U0001f600", "supplementary-plane codepoint"),
        ]
        for plain, label in cases:
            hashed = auth_service.hash_password(plain)
            assert auth_service.verify_password(plain, hashed) is True, (
                f"verify_password should return True for {label}"
            )
            assert auth_service.verify_password(plain + "x", hashed) is False, (
                f"verify_password should return False for wrong password "
                f"({label})"
            )

    def test_authenticate_handles_non_string_password_via_verify_guard(
        self, app, db, seed_user,
    ):
        """End-to-end: a non-string password through ``authenticate`` raises AuthError.

        Before C-44, calling ``authenticate(email, b"bytes")`` would
        crash inside ``verify_password`` with ``AttributeError`` and
        bubble up as a Flask 500.  After C-44, the bytes payload is
        rejected by the type guard so ``verify_password`` returns
        ``False`` and ``authenticate`` raises the standard
        ``AuthError`` -- the user-visible response is now identical
        to a wrong-password attempt, which is the right side-channel
        posture for the auth path.
        """
        with app.app_context():
            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate(auth_service.find_sign_in_user("test@shekel.local"), b"testpass")

    def test_failure_modes_are_indistinguishable(self):
        """Every failure mode returns the same ``False`` value, not a tagged sentinel.

        The auth path depends on the absence of a tagged failure mode:
        wrong-password, wrong-type, and corrupted-hash must all look
        identical to the caller so an attacker cannot probe for
        caller-side bugs by submitting exotic payloads and observing
        the response shape.  This test pins the invariant in one
        place.
        """
        hashed = auth_service.hash_password("validpass1234")
        wrong_password = auth_service.verify_password("WRONGpass1234", hashed)
        wrong_type = auth_service.verify_password(b"validpass1234", hashed)
        empty_plain = auth_service.verify_password("", hashed)
        empty_hash = auth_service.verify_password("validpass1234", "")
        bad_hash = auth_service.verify_password(
            "validpass1234", "not-a-bcrypt-hash",
        )
        assert wrong_password is False
        assert wrong_type is False
        assert empty_plain is False
        assert empty_hash is False
        assert bad_hash is False
        # All five branches converge on the identical sentinel.
        assert wrong_password is wrong_type is empty_plain is empty_hash is bad_hash
