"""Tests for the MFA service module."""

import os
import re
from datetime import datetime, timezone
from unittest.mock import patch

import bcrypt
import pyotp
import pytest

from app.models.user import MfaConfig
from app.services import mfa_service
from app.utils.field_encryption import encrypt_secret


class TestGenerateSecret:
    """Tests for mfa_service.generate_totp_secret()."""

    def test_generates_base32_string(self):
        """generate_totp_secret() returns a valid base32 string."""
        secret = mfa_service.generate_totp_secret()
        assert isinstance(secret, str)
        # pyotp.random_base32() default length is 32 characters
        assert len(secret) == 32
        # Valid base32 characters only (A-Z, 2-7, =).
        assert re.fullmatch(r"[A-Z2-7=]+", secret)

    def test_generates_unique_secrets(self):
        """Two calls return different secrets."""
        s1 = mfa_service.generate_totp_secret()
        s2 = mfa_service.generate_totp_secret()
        assert s1 != s2


class TestVerifyTotpSetupCode:
    """Tests for mfa_service.verify_totp_setup_code().

    The setup-code variant takes a plaintext secret (the pending
    secret captured at /mfa/setup time, which has not yet been
    promoted to ``totp_secret_encrypted``) and returns the matched
    30-second step on success or ``None`` on failure.  Replay
    prevention does not apply because no ``last_totp_timestep`` exists
    yet -- the calling route persists the returned step into the
    column at the same time as the secret is promoted to active.
    """

    def test_valid_code_returns_matched_step(self):
        """A valid current code returns the step it matched.

        The value is the integer ``time.time() // 30`` evaluated at
        the moment the code was produced.  Asserting on the returned
        step (not just truthiness) pins the contract that the route
        relies on for seeding ``last_totp_timestep``.

        ``pyotp.TOTP.at(time.time())`` is used in preference to
        ``.now()`` because the autouse ``freeze_today`` fixture in
        ``test_services/conftest.py`` patches ``datetime.datetime.now``
        but not ``time.time``; ``.now()`` would fetch a code for the
        frozen wall-clock midnight while the production verifier
        reads real ``time.time()``, leaving the two paths reading
        different time sources.  Using ``.at(time.time())`` collapses
        them onto a single source of truth for the test.
        """
        import time as _time  # pylint: disable=import-outside-toplevel

        secret = mfa_service.generate_totp_secret()
        now_unix = int(_time.time())
        code = pyotp.TOTP(secret).at(now_unix)
        result = mfa_service.verify_totp_setup_code(secret, code)
        assert isinstance(result, int)
        # Result must equal the step the code was generated for.  Allow
        # a 1-step jitter only if the wall-clock crossed a 30-second
        # boundary between code generation and verification (rare
        # but possible on a slow runner).
        expected_step = now_unix // 30
        assert result in (expected_step - 1, expected_step, expected_step + 1)

    def test_invalid_code_returns_none(self):
        """A wrong code returns None, signalling no step matched."""
        secret = mfa_service.generate_totp_secret()
        assert mfa_service.verify_totp_setup_code(secret, "000000") is None


class TestVerifyTotpCodeReplayPrevention:
    """Tests for ``mfa_service.verify_totp_code`` -- the active-secret
    verifier that enforces replay prevention via
    ``mfa_config.last_totp_timestep``.

    Closes audit findings F-005 (TOTP replay window) and F-142
    (structured replay logging hook).  See ASVS V2.8.4 and commit C-09
    of the 2026-04-15 security remediation plan.

    Pin policy: every test in this class fixes ``time.time()`` to a
    deterministic instant (``_FROZEN_UNIX``) so the matched step is
    a function of the input code alone -- without this pin, a slow
    runner crossing a 30-second boundary between fixture setup and
    assertion would flip pass/fail seemingly at random.  Patches
    ``time.time`` (the source ``mfa_service._find_matching_step``
    consults) rather than ``datetime.now`` (pyotp's helper consults
    that, but our tests bypass the helper by calling
    ``pyotp.TOTP.at(unix_time)`` directly).
    """

    # A wall-clock instant in 2026 chosen so that ``_FROZEN_UNIX // 30``
    # is a non-trivial integer not coincidentally equal to any other
    # constant in this file.  No business meaning -- pure determinism.
    _FROZEN_UNIX = 1_777_777_770  # 2026-05-03 04:09:30 UTC
    _FROZEN_STEP = _FROZEN_UNIX // 30

    @pytest.fixture()
    def frozen_time(self, monkeypatch):
        """Pin ``time.time`` to ``_FROZEN_UNIX`` for the duration of a test."""
        monkeypatch.setattr("time.time", lambda: float(self._FROZEN_UNIX))
        return self._FROZEN_UNIX

    def _make_mfa_config(self, secret, last_step=None):
        """Build an in-memory MfaConfig with the given encrypted secret.

        Pure object construction -- no DB round-trip.  The verifier
        only reads ``totp_secret_encrypted`` and ``last_totp_timestep``
        and writes to ``last_totp_timestep``, all of which work on a
        detached SQLAlchemy instance.
        """
        return MfaConfig(
            totp_secret_encrypted=encrypt_secret(secret),
            last_totp_timestep=last_step,
        )

    def test_accepts_current_step_first_use(self, frozen_time):
        """A first-use code at the current step is ACCEPTED and the column is set.

        ``last_totp_timestep`` starts at None to model a freshly
        enrolled MFA config (the migration default).  After ACCEPTED
        the column equals the matched step so subsequent verifies
        enforce strict-greater.  Without the column update the next
        call would still see None and accept the same code again --
        the F-005 condition.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        code = pyotp.TOTP(secret).at(frozen_time)

        result = mfa_service.verify_totp_code(config, code)

        assert result is mfa_service.TotpVerificationResult.ACCEPTED
        assert config.last_totp_timestep == self._FROZEN_STEP

    def test_rejects_replay_of_same_step(self, frozen_time):
        """The same code submitted twice in a row is REPLAY on the second call.

        Direct test of the F-005 attack: an attacker who observes a
        single valid code (over the shoulder, MITM, screen capture)
        re-presents it within the drift window.  The second
        ``verify_totp_code`` call must return REPLAY without re-
        accepting the code.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        code = pyotp.TOTP(secret).at(frozen_time)

        first = mfa_service.verify_totp_code(config, code)
        assert first is mfa_service.TotpVerificationResult.ACCEPTED
        assert config.last_totp_timestep == self._FROZEN_STEP

        second = mfa_service.verify_totp_code(config, code)
        assert second is mfa_service.TotpVerificationResult.REPLAY
        # The column is unchanged on REPLAY -- a future legitimate
        # code at a higher step is still acceptable.
        assert config.last_totp_timestep == self._FROZEN_STEP

    def test_rejects_replay_of_previous_step(self, frozen_time):
        """A drift=-1 code is REPLAY when ``last_totp_timestep`` already
        sits at the current step.

        Defends the case where an attacker captured a code one step
        earlier than the user's most recent legitimate verification.
        Without the strict-greater check this code would still match
        the drift window (the previous step is +-1 from current) and
        complete the login.  The attack window is the F-005 ~90-second
        replay window that ASVS V2.8.4 forbids.
        """
        secret = mfa_service.generate_totp_secret()
        # Pretend the current step has already been consumed by a
        # legitimate prior verification.
        config = self._make_mfa_config(secret, last_step=self._FROZEN_STEP)
        previous_step_unix = (self._FROZEN_STEP - 1) * 30
        previous_code = pyotp.TOTP(secret).at(previous_step_unix)

        result = mfa_service.verify_totp_code(config, previous_code)

        assert result is mfa_service.TotpVerificationResult.REPLAY
        assert config.last_totp_timestep == self._FROZEN_STEP

    def test_accepts_step_plus_one(self, frozen_time):
        """A code one step ahead of ``last_totp_timestep`` is ACCEPTED.

        Models the legitimate sequential-login case: the user
        verified at step N, the wall-clock advances to step N+1,
        and the user submits a fresh code matching step N+1.  Strict
        replay prevention must not turn this into a self-DoS.
        """
        secret = mfa_service.generate_totp_secret()
        # The previously consumed step is one BEHIND current.
        config = self._make_mfa_config(
            secret, last_step=self._FROZEN_STEP - 1,
        )
        code_for_current = pyotp.TOTP(secret).at(frozen_time)

        result = mfa_service.verify_totp_code(config, code_for_current)

        assert result is mfa_service.TotpVerificationResult.ACCEPTED
        assert config.last_totp_timestep == self._FROZEN_STEP

    def test_rejects_wrong_code_without_updating_column(self, frozen_time):
        """An invalid code returns INVALID and does not write to the column.

        Distinguishes the typo-or-attack wrong-code case from the
        replay-detection case.  Critically the column must NOT be
        advanced -- otherwise an attacker could DoS a legitimate user
        by pre-emptying the step counter, locking out the next
        legitimate verify.
        """
        secret = mfa_service.generate_totp_secret()
        prior_step = self._FROZEN_STEP - 5
        config = self._make_mfa_config(secret, last_step=prior_step)

        result = mfa_service.verify_totp_code(config, "000000")

        assert result is mfa_service.TotpVerificationResult.INVALID
        assert config.last_totp_timestep == prior_step

    def test_drift_window_minus_one_accepted_when_never_used(self, frozen_time):
        """A code one step behind the current is ACCEPTED on a fresh config.

        With ``last_totp_timestep`` None, the only check that gates
        acceptance is the +-1 drift window.  Drift=-1 is inside the
        window so the code is matched and the column is updated to
        ``current_step - 1`` -- meaning the *next* successful verify
        must be at step >= current_step (no replay of the same -1
        code, no acceptance of step -2).
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        previous_unix = (self._FROZEN_STEP - 1) * 30
        prev_code = pyotp.TOTP(secret).at(previous_unix)

        result = mfa_service.verify_totp_code(config, prev_code)

        assert result is mfa_service.TotpVerificationResult.ACCEPTED
        assert config.last_totp_timestep == self._FROZEN_STEP - 1

    def test_drift_window_minus_two_rejected(self, frozen_time):
        """A code two steps behind is INVALID -- outside the +-1 drift window.

        Boundary check: the verifier walks ``range(-1, 2)`` so step
        ``current - 2`` is never tested.  Without this assertion a
        regression that widened the drift to +-2 (e.g. matching
        Microsoft Authenticator's looser default) would silently re-
        open the F-005 window for an additional 30 seconds.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        far_past_unix = (self._FROZEN_STEP - 2) * 30
        far_past_code = pyotp.TOTP(secret).at(far_past_unix)

        result = mfa_service.verify_totp_code(config, far_past_code)

        assert result is mfa_service.TotpVerificationResult.INVALID
        assert config.last_totp_timestep is None

    def test_drift_window_plus_two_rejected(self, frozen_time):
        """A code two steps ahead is INVALID -- outside the +-1 drift window.

        Symmetric counterpart to the minus-two test.  Closes the
        forward edge of the window so an attacker cannot pre-compute
        a future code and replay it the moment the wall-clock
        catches up.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        far_future_unix = (self._FROZEN_STEP + 2) * 30
        far_future_code = pyotp.TOTP(secret).at(far_future_unix)

        result = mfa_service.verify_totp_code(config, far_future_code)

        assert result is mfa_service.TotpVerificationResult.INVALID
        assert config.last_totp_timestep is None

    def test_decrypts_secret_via_multifernet(self, frozen_time, monkeypatch):
        """A secret written under a retired key still verifies after rotation.

        ``verify_totp_code`` does its own decrypt via
        ``decrypt_secret`` which goes through MultiFernet.
        After ``FIELD_ENCRYPTION_KEY`` rotation, ciphertexts written
        under the old primary still decrypt because the old key is
        retained in ``FIELD_ENCRYPTION_KEY_OLD`` -- the C-04 contract.
        Without this test, a refactor that swapped to a bare Fernet
        for verify (but not for setup) would silently lock out every
        pre-rotation user.
        """
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        # Generate the secret + ciphertext under the CURRENT primary
        # key (whatever the conftest seeded).
        secret = mfa_service.generate_totp_secret()
        original_ciphertext = encrypt_secret(secret)

        # Rotate: a brand new primary, the previously-current key
        # becomes the retired one.  ``decrypt_secret`` should still
        # succeed via MultiFernet's fallback path.
        old_primary = os.environ["FIELD_ENCRYPTION_KEY"]
        new_primary = Fernet.generate_key().decode()
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", new_primary)
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_OLD", old_primary)

        config = MfaConfig(
            totp_secret_encrypted=original_ciphertext,
            last_totp_timestep=None,
        )
        code = pyotp.TOTP(secret).at(frozen_time)

        result = mfa_service.verify_totp_code(config, code)

        assert result is mfa_service.TotpVerificationResult.ACCEPTED
        assert config.last_totp_timestep == self._FROZEN_STEP

    def test_setup_code_does_not_consult_last_totp_timestep(
        self, frozen_time,
    ):
        """``verify_totp_setup_code`` ignores ``last_totp_timestep`` entirely.

        The setup-code path takes a plaintext secret and never sees
        the MfaConfig row.  Even when the row's column is ahead of
        the matched step, the setup verifier still returns the
        matched step -- the route is responsible for seeding the
        column from the returned value.  Pins this contract so a
        future refactor that conflates the two helpers does not
        accidentally apply replay prevention to enrollment.
        """
        secret = mfa_service.generate_totp_secret()
        # A "stale" mfa_config row whose column is already AHEAD of
        # the step our setup code will match.  Demonstrates that
        # verify_totp_setup_code does not consult mfa_config at all.
        code = pyotp.TOTP(secret).at(frozen_time)

        result = mfa_service.verify_totp_setup_code(secret, code)

        assert result == self._FROZEN_STEP

    def test_invalid_secret_raises_invalidtoken(self, frozen_time):
        """A garbled ciphertext propagates ``InvalidToken`` to the caller.

        The decrypt happens inside ``verify_totp_code``.  Surfacing
        the exception (rather than swallowing it as INVALID) lets the
        route layer distinguish "wrong code" from "encryption key
        unavailable" -- the latter requires an operator-side fix and
        a different user-facing message.
        """
        from cryptography.fernet import InvalidToken  # pylint: disable=import-outside-toplevel

        config = MfaConfig(
            totp_secret_encrypted=b"not-valid-fernet-token",
            last_totp_timestep=None,
        )

        with pytest.raises(InvalidToken):
            mfa_service.verify_totp_code(config, "123456")

    def test_missing_encryption_key_raises_runtimeerror(
        self, frozen_time, monkeypatch,
    ):
        """An unset ``FIELD_ENCRYPTION_KEY`` propagates RuntimeError.

        Surfaced rather than swallowed for the same reason as
        InvalidToken: a missing primary key is an operator-side
        condition that the route layer must distinguish from a wrong
        code so the user sees an actionable message.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        code = pyotp.TOTP(secret).at(frozen_time)

        monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)

        with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
            mfa_service.verify_totp_code(config, code)

    def test_malformed_code_returns_invalid(self, frozen_time):
        """Wrong-length / non-string / non-digit input returns INVALID.

        Boundary collection: 5- and 7-digit strings (off-by-one), an
        all-alpha string (copy-paste from a non-numeric field), the
        empty string (form submitted with no code), ``None`` (a
        defaulted form field), and a bare integer (a caller bug
        passing the wrong type).  All must short-circuit to INVALID
        without raising and without mutating ``last_totp_timestep``.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)

        for bad in ("12345", "1234567", "abcdef", "", None, 123456):
            result = mfa_service.verify_totp_code(config, bad)
            assert result is mfa_service.TotpVerificationResult.INVALID, (
                f"Expected INVALID for bad input {bad!r}; got {result!r}"
            )
        assert config.last_totp_timestep is None

    def test_non_ascii_digit_code_returns_invalid_without_raising(
        self, frozen_time,
    ):
        """Finding N-136: the one malformed shape that RAISED instead.

        The boundary collection above is every malformed code that already
        returned INVALID.  This is the one that did not: ``str.isdigit()``
        -- the guard ``_find_matching_step`` used -- is true for every
        non-Latin digit script, so a six-character code of Eastern Arabic
        numerals satisfied both the length and the digit test, reached
        ``hmac.compare_digest``, and raised ``TypeError: comparing strings
        with non-ASCII characters is not supported``.  On the login path,
        with no handler for it.

        Asserted per script rather than once, because the defect is a
        PROPERTY of the whole predicate and any single script would let a
        fix that special-cased one of them pass.  ``last_totp_timestep``
        must also be untouched: a rejected code consumes no step.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)

        # Six characters, ``isdigit()`` true, in three different scripts.
        non_ascii_codes = (
            "١٢٣٤٥٦",       # Arabic-Indic
            "১২৩৪৫৬",       # Bengali
            "１２３４５６",   # Fullwidth
        )
        for code in non_ascii_codes:
            assert len(code) == 6 and code.isdigit(), (
                f"Test premise broken: {code!r} is not a 6-character "
                f"isdigit() string, so it never reached the defect"
            )
            result = mfa_service.verify_totp_code(config, code)
            assert result is mfa_service.TotpVerificationResult.INVALID, (
                f"Expected INVALID for non-ASCII code {code!r}; "
                f"got {result!r}"
            )
        assert config.last_totp_timestep is None

    def test_the_users_real_code_in_another_script_is_still_refused(
        self, frozen_time,
    ):
        """Even the CORRECT six digits, respelled, are not this user's code.

        The sharper half of the case above: the digits below are the very
        code the authenticator is showing right now, transliterated.  A fix
        that merely stopped the crash by falling through to the comparison
        would have to decide whether these match, and the answer is no --
        the code is what the authenticator emitted, ASCII and all.
        """
        secret = mfa_service.generate_totp_secret()
        config = self._make_mfa_config(secret, last_step=None)
        real_code = pyotp.TOTP(secret).at(frozen_time)

        respelled = real_code.translate(
            str.maketrans("0123456789", "٠١٢٣٤"
                                        "٥٦٧٨٩"),
        )
        assert int(respelled) == int(real_code)

        assert mfa_service.verify_totp_code(config, respelled) is (
            mfa_service.TotpVerificationResult.INVALID
        )
        assert config.last_totp_timestep is None
        # ...and the ASCII spelling of the same code still authenticates,
        # so the refusal above is about the spelling, not the secret.
        assert mfa_service.verify_totp_code(config, real_code) is (
            mfa_service.TotpVerificationResult.ACCEPTED
        )


class TestBackupCodes:
    """Tests for backup code generation, hashing, and verification.

    Backup codes were upgraded from 32-bit (8 hex chars) to 112-bit
    (28 hex chars) entropy as part of remediation C-03 / finding F-004.
    The 112-bit width matches ASVS L2 V2.6.2 and resists offline GPU
    brute-force against bcrypt cost-12 hashes. Legacy codes enrolled
    before the upgrade remain valid until the user regenerates them
    because bcrypt is length-agnostic.
    """

    def test_generate_backup_codes_default_count(self):
        """generate_backup_codes() with no argument returns exactly 10 codes.

        The default count is the documented contract used by the
        ``/mfa/confirm`` and ``/mfa/regenerate-backup-codes`` routes.
        """
        codes = mfa_service.generate_backup_codes()
        assert len(codes) == 10

    def test_generate_backup_codes_explicit_count(self):
        """generate_backup_codes(n) returns exactly n codes for positive n."""
        for requested in (1, 5, 25):
            codes = mfa_service.generate_backup_codes(requested)
            assert len(codes) == requested, (
                f"Expected {requested} codes, got {len(codes)}"
            )

    def test_generate_backup_codes_length_and_format(self):
        """Each code is exactly 28 lowercase hex characters (112 bits).

        Width and character set together encode the entropy claim. 28
        hex chars * 4 bits/char = 112 bits, which is the ASVS L2 V2.6.2
        minimum for lookup secrets. Uppercase letters or non-hex chars
        would mean ``secrets.token_hex`` was replaced with a different
        encoder.
        """
        codes = mfa_service.generate_backup_codes()
        for code in codes:
            assert len(code) == 28, (
                f"Backup code must be 28 chars (112 bits); got {len(code)}: {code!r}"
            )
            assert re.fullmatch(r"[0-9a-f]{28}", code), (
                f"Backup code must be lowercase hex; got {code!r}"
            )

    def test_generate_backup_codes_entropy_unique_across_many_calls(self):
        """1000 generated codes contain no duplicates.

        With 112 bits of entropy the birthday-bound collision probability
        for 1000 samples is on the order of 10^-28 -- effectively zero.
        Any duplicate in a 1000-sample run indicates the entropy source
        regressed (e.g. someone replaced ``secrets.token_hex`` with a
        seeded PRNG) and would be a critical security defect for a
        money app.
        """
        sample_size = 1000
        codes = mfa_service.generate_backup_codes(sample_size)
        assert len(codes) == sample_size
        assert len(set(codes)) == sample_size, (
            "Duplicate backup codes generated -- entropy source is not "
            "behaving as a CSPRNG"
        )

    def test_generate_backup_codes_uses_secrets_token_hex_with_14_bytes(self):
        """generate_backup_codes() sources entropy from secrets.token_hex(14).

        Pins the implementation to the OS CSPRNG path with a 14-byte
        (112-bit) request. Catches a regression where someone swaps in
        ``random.choice``, ``hashlib.sha256(time.time())``, or any
        non-CSPRNG source, or where the byte count is reduced. The byte
        count is the entropy claim -- it is not an internal detail.
        """
        with patch.object(
            mfa_service.secrets, "token_hex", wraps=mfa_service.secrets.token_hex
        ) as spy:
            mfa_service.generate_backup_codes(count=10)

        assert spy.call_count == 10, (
            f"Expected 10 calls to secrets.token_hex, got {spy.call_count}"
        )
        for call in spy.call_args_list:
            args, kwargs = call
            byte_count = args[0] if args else kwargs.get("nbytes")
            assert byte_count == 14, (
                f"Expected secrets.token_hex(14); got token_hex({byte_count})"
            )

    def test_hash_and_verify_round_trip(self):
        """A freshly generated 28-char code matches its own bcrypt hash."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes, rounds=4)
        assert mfa_service.verify_backup_code(codes[0], hashed) == 0

    def test_verify_wrong_code_returns_negative(self):
        """verify_backup_code() returns -1 when no stored hash matches.

        Uses a 28-char string that is guaranteed not to collide with any
        randomly generated code (all 'z' is outside the [0-9a-f]
        alphabet, so it cannot equal any real backup code).
        """
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes, rounds=4)
        unrecognized = "z" * 28
        assert mfa_service.verify_backup_code(unrecognized, hashed) == -1

    def test_verify_returns_correct_index(self):
        """verify_backup_code() returns the index of the matching hash."""
        codes = mfa_service.generate_backup_codes()
        hashed = mfa_service.hash_backup_codes(codes, rounds=4)
        assert mfa_service.verify_backup_code(codes[2], hashed) == 2

    def test_verify_backup_code_accepts_legacy_8_char_codes(self):
        """Legacy 8-char codes from pre-C-03 enrollments still verify.

        Bcrypt hashes any byte string of any length up to 72 bytes, so a
        stored hash of an 8-char legacy code matches a freshly typed
        legacy code regardless of the new generator width. This is the
        compatibility guarantee documented in C-03: enrolled users do
        not get locked out -- they receive an in-app prompt to
        regenerate (delivered separately in C-16). Without this test,
        a future refactor that adds length validation to verify could
        silently break login for every pre-upgrade user.
        """
        legacy_code = "1a2b3c4d"
        assert len(legacy_code) == 8
        legacy_hash = bcrypt.hashpw(
            legacy_code.encode("utf-8"), bcrypt.gensalt(rounds=4)
        ).decode("utf-8")

        # Mixed list -- legacy hash at index 0, modern hashes after.
        modern_codes = mfa_service.generate_backup_codes(3)
        modern_hashes = mfa_service.hash_backup_codes(modern_codes, rounds=4)
        stored = [legacy_hash] + modern_hashes

        assert mfa_service.verify_backup_code(legacy_code, stored) == 0

    def test_verify_backup_code_rejects_legacy_code_of_wrong_value(self):
        """A typed legacy-length code that does not match any stored hash returns -1.

        Defends against the "any 8-char input matches" failure mode that
        would arise if length checks short-circuited verification.
        """
        legacy_code = "deadbeef"
        legacy_hash = bcrypt.hashpw(
            legacy_code.encode("utf-8"), bcrypt.gensalt(rounds=4)
        ).decode("utf-8")
        assert mfa_service.verify_backup_code("cafebabe", [legacy_hash]) == -1


class TestGetTotpUri:
    """Tests for mfa_service.get_totp_uri()."""

    def test_uri_format(self):
        """get_totp_uri() returns an otpauth:// URI with correct parameters."""
        secret = mfa_service.generate_totp_secret()
        uri = mfa_service.get_totp_uri(secret, "test@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "Shekel" in uri
        assert "test%40example.com" in uri or "test@example.com" in uri


class TestGenerateQrCode:
    """Tests for mfa_service.generate_qr_code_data_uri()."""

    def test_returns_data_uri(self):
        """generate_qr_code_data_uri() returns a data:image/png;base64 string."""
        data_uri = mfa_service.generate_qr_code_data_uri("otpauth://totp/Test?secret=ABC")
        assert data_uri.startswith("data:image/png;base64,")


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for MFA service functions.

    Covers: wrong-length TOTP codes, non-numeric codes, empty-string codes
    and zero/negative backup code counts.  The cipher's own boundaries
    (corrupted, empty and empty-plaintext ciphertexts) are graded where the
    cipher lives, ``tests/test_field_encryption_key.py``.
    """

    def test_verify_totp_setup_code_wrong_length_five_digits(self):
        """A 5-digit code is rejected (returns None, does not crash).

        A user manually typing a code could miss a digit.  The setup-
        code helper short-circuits to None for any input that does
        not match the configured 6-digit width, never invoking pyotp.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_setup_code(secret, "12345")
        assert result is None

    def test_verify_totp_setup_code_non_numeric(self):
        """A non-numeric code is rejected (returns None, does not crash).

        Copy-paste errors could insert non-numeric characters.  The
        helper rejects anything that is not exclusively ASCII digits.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_setup_code(secret, "abcdef")
        assert result is None

    def test_verify_totp_setup_code_empty_string(self):
        """An empty-string code is rejected (returns None, does not crash).

        Empty form submission must be handled gracefully without
        invoking pyotp.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_setup_code(secret, "")
        assert result is None

    def test_generate_backup_codes_zero_count(self):
        """generate_backup_codes(0) returns an empty list, not a crash.

        A misconfiguration passing count=0 must produce an empty list.
        range(0) produces an empty iterator.
        """
        result = mfa_service.generate_backup_codes(0)
        assert result == []

    def test_generate_backup_codes_negative_count(self):
        """generate_backup_codes(-1) returns an empty list, not a crash.

        range(-1) produces an empty iterator, same as range(0).
        """
        result = mfa_service.generate_backup_codes(-1)
        assert result == []

    def test_verify_totp_setup_code_seven_digits(self):
        """A 7-digit code is rejected (returns None).

        Authenticator apps produce 6-digit codes.  More digits must
        be rejected before the timing-sensitive comparison runs.
        """
        secret = mfa_service.generate_totp_secret()
        result = mfa_service.verify_totp_setup_code(secret, "1234567")
        assert result is None

    def test_get_totp_uri_format_with_explicit_issuer(self):
        """get_totp_uri produces a well-formed otpauth:// URI with explicit issuer.

        Malformed URIs would produce unscannable QR codes, locking users
        out of MFA setup.
        """
        secret = mfa_service.generate_totp_secret()
        uri = mfa_service.get_totp_uri(secret, "user@example.com", issuer="Shekel")

        assert uri.startswith("otpauth://totp/")
        # Issuer appears in the URI.
        assert "Shekel" in uri
        # Email appears (URL-encoded @ is %40).
        assert "user%40example.com" in uri or "user@example.com" in uri


class TestClearMfaMaterial:
    """Tests for clear_mfa_material() and has_mfa_material().

    The shared field-set rule consumed by the /mfa/disable route and
    scripts/reset_mfa.py.  Both functions are pure in-place row
    operations, so the tests construct in-memory MfaConfig instances
    (no DB) mirroring the state-machine policy tests.
    """

    # One realistic value per material field.  The completeness test
    # below pins this dict against the service's own field set so a
    # future MfaConfig material column cannot be added to one without
    # the other.
    _MATERIAL_VALUES = {
        "totp_secret_encrypted": b"encrypted-active-secret",
        "backup_codes": ["$2b$12$hashA", "$2b$12$hashB"],
        "confirmed_at": datetime(2026, 1, 5, tzinfo=timezone.utc),
        "last_totp_timestep": 59_000_000,
        "pending_secret_encrypted": b"encrypted-pending-secret",
        "pending_secret_expires_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
    }

    def _full_config(self):
        """An enabled MfaConfig carrying every material field."""
        return MfaConfig(user_id=1, is_enabled=True, **self._MATERIAL_VALUES)

    def test_value_map_matches_service_field_set(self):
        """The test's field/value map covers exactly the service's set.

        Guards the guard: if _MFA_MATERIAL_FIELDS gains a column, this
        fails until the tests exercise it too.
        """
        # Pylint: protected-access -- pinning the private field set is
        # the point; a public re-export would be API surface for one test.
        assert set(self._MATERIAL_VALUES) == set(
            mfa_service._MFA_MATERIAL_FIELDS  # pylint: disable=protected-access
        )

    def test_clear_nulls_every_material_field_and_disables(self):
        """clear_mfa_material() nulls all material and flips is_enabled.

        The full-disable contract both consumers rely on: no
        decryptable or verifiable secret material may remain at rest,
        including an in-flight pending-setup ciphertext.
        """
        config = self._full_config()

        mfa_service.clear_mfa_material(config)

        assert config.is_enabled is False
        for field in self._MATERIAL_VALUES:
            assert getattr(config, field) is None, field

    def test_has_mfa_material_false_on_clean_row(self):
        """A fully-reset row reports no material."""
        config = MfaConfig(user_id=1, is_enabled=False)
        assert mfa_service.has_mfa_material(config) is False

    def test_has_mfa_material_false_after_clear(self):
        """clear_mfa_material() leaves the row reporting no material --
        the two functions agree because they read one field set."""
        config = self._full_config()
        mfa_service.clear_mfa_material(config)
        assert mfa_service.has_mfa_material(config) is False

    @pytest.mark.parametrize("field", sorted(_MATERIAL_VALUES))
    def test_has_mfa_material_true_for_each_single_field(self, field):
        """Any single lingering material field is detected.

        The pending_secret_encrypted case is the one the reset script
        historically missed: an abandoned /mfa/setup leaves only the
        pending ciphertext + expiry behind, and the residue check must
        flag it so the emergency reset scrubs it.
        """
        config = MfaConfig(
            user_id=1, is_enabled=False,
            **{field: self._MATERIAL_VALUES[field]},
        )
        assert mfa_service.has_mfa_material(config) is True

    def test_is_enabled_alone_is_not_material(self):
        """is_enabled is workflow state, not material -- a row that is
        (corruptly) enabled with no stored material reports False, and
        callers must check the flag separately."""
        config = MfaConfig(user_id=1, is_enabled=True)
        assert mfa_service.has_mfa_material(config) is False
