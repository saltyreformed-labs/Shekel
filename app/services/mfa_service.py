"""Shekel Budget App -- MFA Service.

Handles TOTP secret generation, verification and backup code management.
No Flask imports -- pure service module.  The cipher the TOTP secret is
stored under is :mod:`app.utils.field_encryption`'s (``encrypt_secret`` /
``decrypt_secret``), the one home of every ciphertext column's key; it
lived here until plan step ``bank_import:X-f6b-2`` gave that key a second
column to protect (``budget.bank_feeds``, **R-BI12**; the move **R-BI24**).
"""

import hmac
import secrets
import time
from base64 import b64encode
from enum import Enum
from io import BytesIO

import bcrypt
import pyotp
import qrcode

from app.models.user import MfaConfig
from app.utils.digit_strings import is_ascii_digits
from app.utils.field_encryption import decrypt_secret


# Width in seconds of one TOTP time-step.  RFC 6238 leaves this
# implementation-defined; ``pyotp.TOTP`` defaults to 30 seconds and
# every popular authenticator app (Google Authenticator, Authy, 1Password)
# matches that.  Pinned as a module constant so the verification path,
# the replay-prevention path, and the test fixtures all reference the
# same value -- a divergence here would silently produce off-by-30s
# step numbers and either accept replays or reject legitimate codes.
_TOTP_STEP_SECONDS = 30

# Number of step-widths of clock drift accepted in either direction
# during verification.  ``pyotp.TOTP.verify(..., valid_window=1)`` was
# the previous default; preserved here so the replay-prevention rewrite
# does not narrow the verify window for users with skewed device
# clocks.  Together with strict replay prevention this means an
# observed code is replayable for at most one step (until the user
# submits a fresh code that bumps ``last_totp_timestep``) rather than
# the +-1 step window.
_TOTP_DRIFT_STEPS = 1

# Number of digits in a TOTP code.  ``pyotp.random_base32`` and
# ``pyotp.TOTP`` default to RFC 6238's 6-digit codes, and the user-
# facing form fields enforce the same width.  Codes of any other
# length are short-circuited as INVALID so a malformed cookie or a
# user typo never reaches the timing-sensitive ``totp.at`` comparison.
_TOTP_CODE_LENGTH = 6


class TotpVerificationResult(Enum):
    """Outcome of a TOTP code verification with replay prevention.

    The enum exists -- rather than a plain ``bool`` -- so route
    handlers can distinguish a wrong/malformed code (a typo, a stale
    code that has already rotated past the drift window) from a code
    that matches a previously consumed step (an active replay attack
    or a same-second double-submit).  Audit finding F-142 requires the
    second case to emit a structured ``totp_replay_rejected`` event;
    conflating the two on the wire would either spam the log with
    benign typos or hide the attack signal in the noise of valid-but-
    not-quite-right codes.

    ACCEPTED: The code matches a step strictly greater than
        ``mfa_config.last_totp_timestep``.  ``mfa_config`` has been
        mutated in place to record the new step; the caller MUST
        commit the SQLAlchemy session for replay prevention to
        persist across requests.

    REPLAY: The code matches a valid TOTP step within the drift
        window, but the matched step is less than or equal to the
        already-consumed ``last_totp_timestep``.  Treated as
        unauthorised by the route layer; emits
        ``totp_replay_rejected``.

    INVALID: The code does not match any step within the drift
        window, or the input was malformed (wrong length, non-string,
        not ASCII decimal digits).  Indistinguishable from a typo or a
        code from another secret.
    """

    ACCEPTED = "accepted"
    REPLAY = "replay"
    INVALID = "invalid"


def generate_totp_secret():
    """Generate a random base32-encoded TOTP secret.

    Returns:
        str: A random base32 string suitable for TOTP provisioning.
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str, issuer: str = "Shekel") -> str:
    """Build an otpauth:// provisioning URI for QR code generation.

    Args:
        secret: The base32-encoded TOTP secret.
        email: The user's email address (used as the account name).
        issuer: The service name shown in authenticator apps.

    Returns:
        str: An otpauth://totp/ URI string.
    """
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def generate_qr_code_data_uri(uri: str) -> str:
    """Generate a base64-encoded PNG data URI from an otpauth:// URI.

    Args:
        uri: The otpauth:// provisioning URI to encode as a QR code.

    Returns:
        str: A data:image/png;base64,... string suitable for an <img> src attribute.
    """
    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format="PNG")
    encoded = b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def _find_matching_step(secret: str, code: str) -> int | None:
    """Locate the time-step at which ``code`` matches ``secret``.

    Walks the +-1 step drift window around the current 30-second
    epoch step.  Each candidate step is converted to its anchoring
    Unix timestamp (``step * 30``) and ``pyotp.TOTP.at`` regenerates
    the OTP that the user's authenticator would have produced at
    that wall-clock instant; ``hmac.compare_digest`` performs the
    comparison in constant time relative to the input length so the
    matching step is not leaked through a timing side-channel.

    **The shape check is ASCII-strict, and that is load-bearing rather
    than tidy** (plan step X-ae, finding N-136).  ``hmac.compare_digest``
    raises ``TypeError`` on a ``str`` holding any non-ASCII character, and
    ``str.isdigit`` -- what this guard used to ask -- is true for every
    non-Latin digit script.  A 6-character code of Eastern Arabic numerals
    therefore passed the guard and raised out of the comparison below, on
    the LOGIN path, where nothing catches it: ``MfaVerifySchema.totp_code``
    validates only ``Length(max=6)``.  It failed closed (no authentication
    was granted) but as an unhandled 500.
    :func:`~app.utils.digit_strings.is_ascii_digits` is the predicate the
    standard library does not offer, and the route layer's id parsing asks
    it too, so there is one definition of "a digit string" rather than one
    per door.

    Returns the matched step on the first hit because two different
    steps cannot produce the same 6-digit OTP within a 90-second
    window: the OTP space is 10**6, and at one OTP every 30 seconds
    the next collision is on the order of 347 days out -- far
    outside the +-30s drift window we examine.

    Args:
        secret: Base32-encoded TOTP secret as plaintext.
        code: Candidate 6-digit code from the user.  Anything that
            is not exactly six ASCII decimal digits is rejected
            without invoking ``pyotp`` so malformed input cannot
            influence timing.

    Returns:
        int | None: The integer time-step (Unix-seconds // 30) at
            which the code matches, or ``None`` if no candidate
            within the drift window matches.
    """
    if not isinstance(code, str):
        return None
    if len(code) != _TOTP_CODE_LENGTH or not is_ascii_digits(code):
        return None
    totp = pyotp.TOTP(secret)
    current_step = int(time.time()) // _TOTP_STEP_SECONDS
    for drift in range(-_TOTP_DRIFT_STEPS, _TOTP_DRIFT_STEPS + 1):
        candidate_step = current_step + drift
        candidate_otp = totp.at(candidate_step * _TOTP_STEP_SECONDS)
        if hmac.compare_digest(candidate_otp, code):
            return candidate_step
    return None


def verify_totp_code(mfa_config: MfaConfig, code: str) -> TotpVerificationResult:
    """Verify a TOTP code against the active secret with replay prevention.

    Implements ASVS V2.8.4: a successfully matched 30-second time-step
    must be strictly greater than ``mfa_config.last_totp_timestep``.
    Without this check, the +-1 step drift window built into TOTP
    leaves any observed code replayable for ~90 seconds after
    observation -- the F-005 finding.  See commit C-09 of the
    2026-04-15 security remediation plan.

    Side effects on ACCEPTED:
        ``mfa_config.last_totp_timestep`` is mutated in place to the
        step at which the code matched.  The caller is responsible
        for committing the SQLAlchemy session so the new value
        persists; without a commit, the same code remains replayable
        for the duration of the drift window.

    No side effects on REPLAY or INVALID -- the row is unchanged and
    a subsequent retry with a fresh code may still succeed.

    Args:
        mfa_config: The user's MfaConfig row.
            ``totp_secret_encrypted`` is decrypted internally;
            ``last_totp_timestep`` is read for the replay check and
            written on success.
        code: The 6-digit code string from the user's
            authenticator app.  Non-string, wrong-length, and
            non-ASCII-digit inputs are rejected as INVALID without
            invoking ``pyotp``.  Note that the secret IS decrypted
            first, so a malformed code still surfaces
            ``InvalidToken`` / ``RuntimeError`` when the encryption
            key is missing or rotated away -- an earlier wording here
            claimed the shape check ran "without consulting the
            secret", which the decrypt on the line above disproves.

    Returns:
        TotpVerificationResult: ACCEPTED if the code matched a step
            > ``last_totp_timestep`` (or ``last_totp_timestep`` was
            ``None``); REPLAY if the matched step was already
            consumed; INVALID otherwise.

    Raises:
        cryptography.fernet.InvalidToken: If the ciphertext in
            ``totp_secret_encrypted`` cannot be decrypted under any
            currently configured Fernet key.  Surfaced to the route
            layer so the user sees the operator-side error rather
            than a silent INVALID that would suggest a typo.
        RuntimeError: If ``FIELD_ENCRYPTION_KEY`` is unset.  Same
            rationale -- propagated, not swallowed.
    """
    secret = decrypt_secret(mfa_config.totp_secret_encrypted)
    matched_step = _find_matching_step(secret, code)
    if matched_step is None:
        return TotpVerificationResult.INVALID
    if (mfa_config.last_totp_timestep is not None
            and matched_step <= mfa_config.last_totp_timestep):
        return TotpVerificationResult.REPLAY
    mfa_config.last_totp_timestep = matched_step
    return TotpVerificationResult.ACCEPTED


def verify_totp_setup_code(secret: str, code: str) -> int | None:
    """Verify a TOTP code against a setup-pending secret.

    Used by ``/mfa/confirm`` where the secret being verified lives
    in ``mfa_config.pending_secret_encrypted`` and has not yet been
    promoted to the active credential.  Replay prevention does not
    apply at this stage -- there is no ``last_totp_timestep`` to
    compare against, the secret has never been used, and the user
    is mid-enrolment in a flow gated by an authenticated session
    plus a 15-minute pending-state expiry.

    The matched step is RETURNED (not stored) so the calling route
    can persist it as ``mfa_config.last_totp_timestep`` in the same
    commit that promotes the pending secret to active.  That
    handoff closes the only window in which the confirming code
    could otherwise be replayed: the ~30 seconds between
    ``/mfa/confirm`` and the user's first ``/mfa/verify`` after
    enrolment.

    Args:
        secret: Base32-encoded TOTP secret as plaintext
            (decrypted from ``mfa_config.pending_secret_encrypted``
            by the caller, since the encrypted payload may decrypt
            under a retired key in the MultiFernet list).
        code: The 6-digit code string from the user's
            authenticator.

    Returns:
        int | None: The matched step on success, or ``None`` if no
            step within the drift window matched.
    """
    return _find_matching_step(secret, code)


def generate_backup_codes(count: int = 10) -> list[str]:
    """Generate a list of single-use backup codes.

    Each code is 14 random bytes rendered as 28 lowercase hex characters,
    yielding 112 bits of entropy. Bytes are sourced from
    ``secrets.token_hex`` which wraps the operating system's CSPRNG
    (``os.urandom``).

    The 112-bit width is the ASVS L2 V2.6.2 minimum for lookup secrets and
    matches the threat model used for this app: the bcrypt hashes (cost 12)
    leak via a backup or host compromise, and an attacker mounts an offline
    GPU brute-force. At ~10^12 bcrypt cost-12 hashes/second, exhausting
    2^112 candidates per code averages on the order of millions of years.
    The previous 32-bit width was crackable in seconds on a consumer GPU.

    Args:
        count: Number of backup codes to generate. Defaults to 10. Values
            <= 0 produce an empty list because ``range(count)`` is empty.

    Returns:
        list[str]: ``count`` plaintext backup code strings, each exactly
            28 characters of lowercase hexadecimal (``[0-9a-f]``). The
            caller is responsible for hashing them with
            :func:`hash_backup_codes` before persistence.
    """
    return [secrets.token_hex(14) for _ in range(count)]


def hash_backup_codes(codes: list[str], rounds: int | None = None) -> list[str]:
    """Hash a list of plaintext backup codes with bcrypt.

    Args:
        codes: List of plaintext backup code strings.
        rounds: Optional bcrypt cost factor (default 12, use 4 for tests).

    Returns:
        list[str]: Bcrypt hash strings, one per code.
    """
    return [
        bcrypt.hashpw(
            c.encode("utf-8"),
            bcrypt.gensalt(rounds=rounds) if rounds else bcrypt.gensalt(),
        ).decode("utf-8")
        for c in codes
    ]


def verify_backup_code(code: str, hashed_codes: list[str]) -> int:
    """Check a plaintext backup code against a list of bcrypt hashes.

    Args:
        code: The plaintext backup code to verify.
        hashed_codes: List of bcrypt hash strings to check against.

    Returns:
        int: The index of the matching hash, or -1 if no match found.
    """
    for idx, hashed in enumerate(hashed_codes):
        if bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8")):
            return idx
    return -1


# Every MfaConfig column that holds MFA material -- secret ciphertexts,
# backup-code hashes, and the timestamps/counters recorded against them.
# The single definition of "MFA material" shared by clear_mfa_material
# and has_mfa_material so the clear and the residue check can never
# disagree on the field set (the historical app-vs-script drift left an
# orphaned totp_secret_encrypted behind exactly because two hand-rolled
# copies of this list diverged).  ``is_enabled`` is deliberately NOT
# material: it is workflow state, handled separately by the clear and
# checked separately by callers.
_MFA_MATERIAL_FIELDS = (
    "totp_secret_encrypted",
    "backup_codes",
    "confirmed_at",
    "last_totp_timestep",
    "pending_secret_encrypted",
    "pending_secret_expires_at",
)


def clear_mfa_material(mfa_config: MfaConfig) -> None:
    """Disable MFA and clear every piece of stored MFA material.

    The single clear rule shared by the app's ``/mfa/disable`` route
    and the emergency ``scripts/reset_mfa.py`` CLI, so the two paths
    cannot drift on which fields a disable must scrub.  Sets
    ``is_enabled`` to ``False`` and nulls every
    :data:`_MFA_MATERIAL_FIELDS` column:

    * ``totp_secret_encrypted`` / ``backup_codes`` -- the credentials
      themselves; no decryptable or verifiable secret may remain at
      rest after a disable.
    * ``confirmed_at`` -- the enrolment timestamp of the now-cleared
      secret.
    * ``last_totp_timestep`` -- records the highest step consumed
      against the cleared secret; carrying it forward to a
      re-enrolment under a fresh secret could reject every new code
      as a replay of the old secret's last step (commit C-09).
    * ``pending_secret_encrypted`` / ``pending_secret_expires_at`` --
      an in-flight or abandoned ``/mfa/setup`` ciphertext.  The
      expiry only gates promotion at ``/mfa/confirm``; without this
      clear the encrypted pending secret would survive at rest until
      the next setup overwrites it.

    Mutates the row in place and does NOT commit -- the clear joins
    the caller's transaction so it lands atomically with the caller's
    own bookkeeping (the route's security-event stamp, the script's
    standalone commit).

    Args:
        mfa_config: The user's MfaConfig row to scrub.
    """
    for field in _MFA_MATERIAL_FIELDS:
        setattr(mfa_config, field, None)
    mfa_config.is_enabled = False


def has_mfa_material(mfa_config: MfaConfig) -> bool:
    """Report whether any clearable MFA material remains on the row.

    The residue-detection companion to :func:`clear_mfa_material`,
    reading the same :data:`_MFA_MATERIAL_FIELDS` set.  Used by
    ``scripts/reset_mfa.py`` to decide whether a row that is already
    ``is_enabled=False`` still carries material an emergency reset
    must scrub (an orphaned secret from a manual DB intervention, or
    a pending ciphertext from an abandoned setup).  ``is_enabled``
    itself is workflow state, not material -- callers check it
    separately.

    Args:
        mfa_config: The user's MfaConfig row to inspect.

    Returns:
        True when at least one material field is non-NULL.
    """
    return any(
        getattr(mfa_config, field) is not None
        for field in _MFA_MATERIAL_FIELDS
    )
