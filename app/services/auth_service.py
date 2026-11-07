"""
Shekel Budget App -- Authentication Service

Handles password hashing, verification, breached-password checking,
account lockout, and user registration.  No Flask imports -- this is a
pure service module.  Configuration that needs to be runtime-tunable
(lockout threshold, lockout duration, HIBP toggle) is read via
``os.getenv`` at function-call time so the test suite can override
each value through ``monkeypatch.setenv`` without going through the
Flask config object.  Defaults documented alongside the matching
``BaseConfig`` settings in ``app/config.py``.
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import requests

from app.extensions import db
from app.models.user import User
from app.exceptions import AuthError, ValidationError
from app.utils.log_events import (
    AUTH,
    EVT_ACCOUNT_LOCKED,
    EVT_HIBP_CHECK_FAILED,
    EVT_HIBP_CHECK_REJECTED,
    log_event,
)


logger = logging.getLogger(__name__)


# ----- Account-lockout helpers (audit finding F-033 / commit C-11) -----
#
# Read on every call rather than at module import so the test suite can
# adjust thresholds via ``monkeypatch.setenv``.  Defaults match
# ``BaseConfig.LOCKOUT_THRESHOLD`` and ``BaseConfig.LOCKOUT_DURATION_MINUTES``.

_LOCKOUT_THRESHOLD_DEFAULT = 10
_LOCKOUT_DURATION_MINUTES_DEFAULT = 15

# What the code page and the signed-in doors say while the account is locked.
# ``/login`` never says it: there a stranger could learn the account exists.
LOCKED_OUT_MESSAGE = "Too many wrong attempts. Try again later."


def _get_lockout_threshold():
    """Read the consecutive-failure threshold for triggering a lockout.

    Returns the integer threshold from ``LOCKOUT_THRESHOLD`` in the
    environment, defaulting to ``10`` if unset.  A non-positive value
    is rejected because it would never trip (zero or negative threshold
    means "lock immediately on the first wrong password" -- a config
    accident, not a deliberate posture; legitimate users would lock
    themselves out with their first typo).

    Returns:
        int: Number of consecutive failures required to trip a lockout.

    Raises:
        ValueError: If the configured value is not a positive integer.
    """
    value = int(os.getenv("LOCKOUT_THRESHOLD", str(_LOCKOUT_THRESHOLD_DEFAULT)))
    if value <= 0:
        raise ValueError(
            f"LOCKOUT_THRESHOLD must be a positive integer; got {value}."
        )
    return value


def _get_lockout_duration():
    """Read the lockout window length as a ``timedelta``.

    Returns the duration from ``LOCKOUT_DURATION_MINUTES`` in the
    environment, defaulting to ``15`` minutes if unset.  A non-positive
    value is rejected because a zero-duration lockout never blocks any
    login attempt (the ``locked_until > now`` check is strict greater-
    than) and a negative value is logically meaningless.

    Returns:
        timedelta: How long an account remains locked after the
            threshold trips.

    Raises:
        ValueError: If the configured value is not a positive integer.
    """
    minutes = int(
        os.getenv(
            "LOCKOUT_DURATION_MINUTES",
            str(_LOCKOUT_DURATION_MINUTES_DEFAULT),
        )
    )
    if minutes <= 0:
        raise ValueError(
            "LOCKOUT_DURATION_MINUTES must be a positive integer; "
            f"got {minutes}."
        )
    return timedelta(minutes=minutes)


# ----- HIBP breached-password helpers (audit finding F-086 / C-11) -----

# Public k-anonymity endpoint.  ``api.pwnedpasswords.com`` accepts a
# 5-character SHA-1 prefix and returns every full SHA-1 hash beginning
# with that prefix, separated by suffix-and-count pairs.  The plaintext
# password is never sent to HIBP -- only the prefix, which is shared by
# at minimum 2^15 ~ 32k possible passwords, so the lookup leaks no
# information about which password is being checked.
_HIBP_ENDPOINT = "https://api.pwnedpasswords.com/range/{prefix}"

# Default network timeout if ``HIBP_TIMEOUT_SECONDS`` is unset.  Three
# seconds is short enough that a registration form does not appear to
# hang on a slow upstream and long enough to absorb normal jitter on
# api.pwnedpasswords.com.
_HIBP_TIMEOUT_SECONDS_DEFAULT = 3.0


def _hibp_check_enabled():
    """Return True when the HIBP breached-password check should run.

    Reads ``HIBP_CHECK_ENABLED`` from the environment; truthy strings
    (``"true"``, ``"1"``, ``"yes"``, case-insensitive) enable the check.
    Anything else disables it.  The default when unset is ``True`` so
    that an operator who simply runs the app in production gets the
    breach check; explicit ``HIBP_CHECK_ENABLED=false`` is required to
    opt out.

    The conftest in ``tests/conftest.py`` flips this off for the whole
    suite via an autouse fixture so individual tests do not have to
    mock ``requests.get`` on every fixture path that calls
    ``hash_password``.  Tests that exercise HIBP behaviour explicitly
    flip it back on through ``monkeypatch.setenv``.

    Returns:
        bool: True iff the check should be performed.
    """
    return os.getenv("HIBP_CHECK_ENABLED", "true").lower() in (
        "true", "1", "yes",
    )


def _check_pwned_password(plain_password):
    """Reject a password that has appeared in a public breach dataset.

    Uses HIBP's k-anonymity API: only the first five characters of the
    password's SHA-1 hash leave the host.  The full SHA-1 is never
    transmitted, and the response contains 800 to 1000 candidate
    suffixes per prefix (out of which at most one will be the real
    match), so the lookup leaks no usable information about which
    password was being checked.  See https://haveibeenpwned.com/API/v3
    section "Searching by range".

    Behaviour matrix:

      * Check disabled (``HIBP_CHECK_ENABLED=false``) -> return without
        contacting HIBP.  Used by the test suite and by operators who
        intentionally disable the outbound dependency.
      * Network error, timeout, or non-2xx response -> log a warning
        and return.  Fail-open: a transient HIBP outage must not stop
        a legitimate user from registering or rotating their password.
        The warning is the operator-side signal that breach checking
        is currently degraded.
      * Password's full SHA-1 suffix is present in the response with
        a count > 0 -> raise ``ValidationError``.  The user-facing
        message names the breach class and instructs the user to pick
        a different password; it does NOT reveal the breach count or
        any HIBP-specific terminology that would leak which dataset
        the password was matched against.

    Args:
        plain_password: The plaintext password to check.  Encoded as
            UTF-8 before hashing; non-string input would surface as a
            ``UnicodeDecodeError`` from ``encode`` and is the caller's
            responsibility to avoid.

    Returns:
        None on accept (either the check ran and the password was not
        breached, or the check was disabled, or HIBP was unreachable
        and the call failed open).

    Raises:
        ValidationError: The password's SHA-1 hash matches a known
            breached entry.
    """
    if not _hibp_check_enabled():
        return

    sha1 = hashlib.sha1(  # nosec B324 -- SHA-1 is mandated by the HIBP API
        plain_password.encode("utf-8"),
    ).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    timeout = float(
        os.getenv(
            "HIBP_TIMEOUT_SECONDS",
            str(_HIBP_TIMEOUT_SECONDS_DEFAULT),
        )
    )

    try:
        response = requests.get(
            _HIBP_ENDPOINT.format(prefix=prefix),
            timeout=timeout,
            # Add-Padding asks HIBP to pad the response with random
            # bogus suffixes so a passive network observer cannot use
            # the response size to narrow the candidate set.  The
            # responses still parse correctly because every padded line
            # has a count of zero, which the loop below ignores.
            headers={"Add-Padding": "true"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        # Catches timeouts, connection errors, DNS failures, and HTTP
        # error responses (4xx/5xx).  Fail-open with a structured log
        # event so the operator can wire an alert on the
        # ``hibp_check_failed`` event without having to grep
        # WARNING-level lines.
        log_event(
            logger, logging.WARNING, EVT_HIBP_CHECK_FAILED, AUTH,
            "HIBP breached-password check failed; allowing password",
            error_class=type(exc).__name__,
        )
        return

    for line in response.text.splitlines():
        # Each line is "<35-char SHA-1 suffix>:<count>".  A line with no
        # colon is a protocol violation by HIBP rather than a security
        # risk; skip it and continue scanning.  A malicious response
        # that omits the colon would be safer to ignore than to crash
        # the form.
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        record_suffix = parts[0].strip().upper()
        if record_suffix != suffix:
            continue
        # The suffix matches; the count decides whether it is a real
        # breach record.  Honour the Add-Padding protocol requested
        # above: padded lines carry a count of 0 and are bogus noise, so
        # only a positive count is a genuine breach.  A non-integer
        # count is a malformed line -- skip it rather than treat a
        # matching suffix as a confirmed breach.
        try:
            count = int(parts[1].strip())
        except ValueError:
            continue
        if count > 0:
            log_event(
                logger, logging.INFO, EVT_HIBP_CHECK_REJECTED, AUTH,
                "HIBP rejected breached password at hash time",
            )
            raise ValidationError(
                "This password has appeared in a known data breach. "
                "Please choose a different one."
            )


def hash_password(plain_password, rounds=None):
    """Hash a plaintext password using bcrypt, after a HIBP breach check.

    The breach check runs before the bcrypt salt is generated so that a
    rejected password does not pay the bcrypt cost (small win on
    legitimate flow, larger win on automated abuse) and so the
    ``ValidationError`` raised on a breached password reaches the
    caller before any side effects.  When ``HIBP_CHECK_ENABLED`` is
    unset or truthy (production default) the check contacts HIBP via
    its k-anonymity endpoint; see :func:`_check_pwned_password` for
    the protocol and the fail-open semantics on network error.

    Args:
        plain_password: The plaintext password string.
        rounds: Optional bcrypt cost factor (log2 iterations).
            Defaults to bcrypt's built-in default if not specified.

    Returns:
        The bcrypt hash as a string.

    Raises:
        ValidationError: If the password exceeds 72 bytes (bcrypt
            input limit) or if HIBP reports the password has appeared
            in a known breach.
    """
    if len(plain_password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")
    # Breached-password check before bcrypt so a rejected password
    # avoids the (deliberately slow) hash work and the caller sees the
    # validation error before any persistent side effects.  Fail-open
    # on HIBP outage; see :func:`_check_pwned_password`.
    _check_pwned_password(plain_password)
    salt = bcrypt.gensalt(rounds=rounds) if rounds else bcrypt.gensalt()
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), salt
    ).decode("utf-8")


def verify_password(plain_password, password_hash):
    """Verify a plaintext password against a bcrypt hash.

    Defensive hardening (commit C-44 / audit finding F-083): the
    previous guard only short-circuited on ``plain_password is None``,
    so any other falsy-but-not-None value (empty string, ``bytes``,
    ``int``, ``Decimal``, ``list``, ...) reached ``.encode("utf-8")``
    and raised ``AttributeError`` which propagated as a 500.  This
    function now fails closed on every non-string input.

    Specifically:

    * ``plain_password`` must be a non-empty ``str``.  Non-string
      types (``None``, ``bytes``, ``int``, ``Decimal``, sequences,
      mappings) and the empty string both return ``False`` without
      reaching bcrypt.  Every authentication route gates on a
      Marshmallow schema with ``min=1`` length (``LoginSchema``,
      ``ReauthSchema``, ``MfaDisableSchema``, ``ChangePasswordSchema``),
      so an empty plaintext only reaches this function when the
      caller is broken; treating it as a non-match is safer than
      round-tripping bcrypt's empty-string hash.
    * ``password_hash`` must be a non-empty ``str``.  Same rejection
      set as ``plain_password`` -- a non-string or empty hash cannot
      be a valid bcrypt digest, so the lookup never had to reach
      bcrypt anyway.
    * A ``password_hash`` that is a non-empty ``str`` but does not
      parse as a bcrypt hash (corrupted DB row, hash from an older
      incompatible scheme, manual operator typo) raises
      ``ValueError`` ("Invalid salt") inside ``bcrypt.checkpw``.
      The catch below converts it to ``False`` so a corrupted row
      cannot crash the login flow.  Operators observe the corruption
      via the lockout-counter increment in :func:`authenticate` and
      the structured ``account_locked`` log event once the threshold
      trips.

    All failure modes return the same ``False`` value -- "wrong
    type", "empty string", "corrupted hash", and "wrong password"
    are indistinguishable at the call site so an attacker probing
    with exotic payloads cannot use response shape to fingerprint
    caller-side bugs.

    Args:
        plain_password: The plaintext password to check.  Expected
            to be a non-empty ``str``.
        password_hash:  The stored bcrypt hash.  Expected to be a
            non-empty ``str`` containing a parseable bcrypt digest.

    Returns:
        True if the inputs are well-formed and ``bcrypt.checkpw``
        confirms the plaintext matches the hash; False otherwise.
    """
    if not isinstance(plain_password, str) or not plain_password:
        return False
    if not isinstance(password_hash, str) or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        # bcrypt.checkpw raises ValueError ("Invalid salt") when
        # password_hash is a valid str but not a parseable bcrypt
        # digest.  Fail closed so a corrupted row or a hash from an
        # older incompatible scheme cannot crash the login flow with
        # a 500.  See module docstring above for the audit-trail
        # consequences (lockout counter still increments, surfacing
        # the corruption through the existing forensic path).
        return False


# Timing-equalization dummy hash (deep-hunt #27).  ``authenticate`` runs a
# throwaway bcrypt verification against this hash on the no-user branch so
# an unknown email costs the same wall-clock time as a known email with a
# wrong password.  Without it the no-user branch returns before any bcrypt
# work while the wrong-password branch pays a full ``bcrypt.checkpw``,
# letting an attacker enumerate registered addresses by timing ``/login``
# despite the constant response message.  Generated once at import with the
# same default cost factor as real password hashes (``bcrypt.gensalt()`` in
# :func:`hash_password`), so if bcrypt's default rounds change the
# equalization cost tracks it automatically.  The plaintext is irrelevant --
# only the ``bcrypt.checkpw`` cost matters and the verification result is
# always discarded.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"shekel-timing-equalization-placeholder", bcrypt.gensalt()
).decode("utf-8")


def find_sign_in_user(email):
    """Return the user who signs in with *email*, or ``None``: sign-in's read before its lock.

    The first half of sign-in, split from :func:`authenticate` at plan step
    ``balance:X-bn`` (ruling **R-CC121**, "Lock, then check"): the route finds
    the account here, takes that account's owner lock
    (:func:`app.db_transaction.bind_sign_in_owner`), and only then calls
    :func:`authenticate`, whose reads of the lockout columns and the password
    hash happen under the lock.  One service call doing both could not put the
    lock between them, and this layer takes no lock itself (the lock's home is
    :mod:`app.db_transaction`).

    Args:
        email: The address typed at the login form.

    Returns:
        The matching :class:`~app.models.user.User`, or ``None`` when no
        account has that address.
    """
    return db.session.query(User).filter_by(email=email).first()


def is_locked_out(user: User) -> bool:
    """Return True while *user*'s lock window is open.

    ``locked_until`` is an exclusive upper bound: at the instant it equals
    ``now`` the lock is over.  A value in the past is therefore the same as
    ``None`` to every check, and stays on the row until the next
    :func:`clear_failed_attempts`.

    Args:
        user: The account being checked.

    Returns:
        True while the account refuses every password and code.
    """
    return (
        user.locked_until is not None
        and user.locked_until > datetime.now(timezone.utc)
    )


def record_failed_attempt(user: User) -> None:
    """Count one wrong password or code against *user*, tripping the lock at the threshold.

    The ONE home of the count and the trip, whichever check the guess failed:
    sign-in's password and code, and the signed-in password and code checks.
    The caller holds the owner's lock (ruling **R-CC121**), so two guesses
    cannot count once.

    At the threshold (``LOCKOUT_THRESHOLD``, default 10) this stamps
    ``locked_until = now + LOCKOUT_DURATION_MINUTES``, zeroes the count, and
    emits the structured ``account_locked`` event.  It zeroes the count so a
    second lock takes another threshold-many failures rather than one more:
    otherwise an account whose failures already crossed the threshold would
    lock again on the very next attempt after the window expires.

    Commits, even short of the threshold, so the count survives the caller's
    refusal.

    Args:
        user: The account the guess was against.
    """
    # Tolerate a defensive ``None`` (a row predating the column or a manual
    # reset) by coalescing to 0; any future schema-level NOT NULL guarantee
    # makes the coalesce a no-op rather than a crash.
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= _get_lockout_threshold():
        user.locked_until = datetime.now(timezone.utc) + _get_lockout_duration()
        user.failed_login_count = 0
        log_event(
            logger, logging.WARNING, EVT_ACCOUNT_LOCKED, AUTH,
            "Account locked after consecutive wrong passwords or codes",
            user_id=user.id, lockout_until=user.locked_until.isoformat(),
        )
    db.session.commit()


def clear_failed_attempts(user: User) -> None:
    """Clear *user*'s count and any lock, once every check they face has passed.

    Called at a finished sign-in and a finished re-confirm, and nowhere else
    (ruling plan#3007).  A correct password alone does not clear it: someone
    holding the password but not the second factor could otherwise re-enter it
    between wrong codes, and the lock would never fire.

    Commits only when there is something to clear: the common path is a user
    with no failure history, for whom the commit would be a no-op write.

    Args:
        user: The account whose checks have all passed.
    """
    if user.failed_login_count != 0 or user.locked_until is not None:
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()


def check_signed_in_password(
    user: User, password: str, wrong_password_message: str,
) -> None:
    """Check a signed-in door's password, counting a wrong one toward the lock.

    The one password check of ``/reauth``, ``/change-password`` and
    ``/mfa/disable``.  The lock is checked first, so bcrypt never runs on a
    locked account, as at sign-in.  Unlike ``/login`` the refusal names the
    lock: whoever is here holds a live session on this account, so the lock's
    existence tells them nothing a stranger could use.

    Args:
        user: The signed-in account.  Its row was reloaded under the owner's
            lock at the request's boundary.
        password: The password typed.
        wrong_password_message: What this door says to a wrong password.

    Raises:
        AuthError: With :data:`LOCKED_OUT_MESSAGE` while locked, else with
            *wrong_password_message* when the password is wrong.
    """
    if is_locked_out(user):
        raise AuthError(LOCKED_OUT_MESSAGE)
    if not verify_password(password, user.password_hash):
        record_failed_attempt(user)
        raise AuthError(wrong_password_message)


def authenticate(user, password):
    """Check *password* for *user*, enforcing account lockout.

    *user* is what :func:`find_sign_in_user` returned, and the caller has
    taken its owner lock in between (ruling **R-CC121**), so the lockout
    columns read below and the counter this writes cannot interleave with
    another sign-in's or a signed-in door's write of the same row.

    Lockout flow (audit finding F-033 / commit C-11):

      * If *user* is ``None`` (no account has the address) -- run one throwaway bcrypt
        verification against ``_DUMMY_PASSWORD_HASH`` (timing
        equalization, deep-hunt #27) so this branch costs the same as a
        wrong-password attempt, then raise ``AuthError`` with the same
        generic message used for wrong-password.  This closes the
        response-content AND the response-timing email-enumeration
        oracle.
      * If the user row's ``locked_until`` is set and still in the
        future -- raise ``AuthError`` WITHOUT calling
        :func:`verify_password`.  Skipping the bcrypt step means an
        attacker observing response timing cannot distinguish "locked
        and password is wrong" from "locked and password is right",
        so a lockout window cannot be used as a side-channel oracle.
      * If the password is wrong -- :func:`record_failed_attempt`.
      * If the password is right but ``is_active`` is False -- raise
        ``AuthError`` with a message that explicitly says the account
        is disabled.  We have already proven knowledge of the
        password, so this branch is not an information leak.
      * If the password is right and the account is active -- return the
        user.  The count is NOT cleared here: for an account with a second
        factor this is only the first check, and the count clears when every
        check has passed (:func:`clear_failed_attempts`, ruling plan#3007).

    All branches share the same generic ``"Invalid email or password."``
    message except for the explicit "Account is disabled." branch
    above.  In particular, the lockout-rejection branch deliberately
    uses the generic message so an attacker probing whether an account
    is locked sees the same response as wrong-password and learns
    nothing.

    Args:
        user:     The account :func:`find_sign_in_user` found, or ``None``.
        password: The plaintext password.

    Returns:
        The :class:`~app.models.user.User` object on successful auth.

    Raises:
        AuthError: If no account was found, the account is in an
            active lockout window, the password is wrong, or the
            account is disabled.
    """
    if user is None:
        # Timing equalization (deep-hunt #27): pay the same bcrypt cost as
        # the wrong-password branch below so the no-user response is not
        # measurably faster, which would let an attacker enumerate
        # registered emails by timing /login.  verify_password mirrors the
        # real branch's input-validation short-circuits exactly (empty /
        # non-string passwords stay fast on both paths), so timing matches
        # for every input shape.  The result is intentionally discarded.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthError("Invalid email or password.")

    # Lockout gate FIRST so we never run bcrypt on a locked account.
    # See module docstring above for the timing-oracle rationale.
    if is_locked_out(user):
        raise AuthError("Invalid email or password.")

    if not verify_password(password, user.password_hash):
        record_failed_attempt(user)
        raise AuthError("Invalid email or password.")

    if not user.is_active:
        # Past the password gate, so naming the disabled state here is
        # not an information leak: an attacker without the password
        # never reaches this branch.
        raise AuthError("Account is disabled.")

    return user


def change_password(user, current_password, new_password):
    """Change a user's password after verifying the current one.

    Args:
        user: The User object whose password is being changed.
        current_password: The user's current plaintext password.
        new_password: The new plaintext password (must be >= 12 chars).

    Returns:
        None on success.

    Raises:
        AuthError: If the account is locked, or current_password does not
            match the stored hash (:func:`check_signed_in_password`).
        ValidationError: If new_password is shorter than 12 characters.
    """
    check_signed_in_password(
        user, current_password, "Current password is incorrect.",
    )
    if len(new_password) < 12:
        raise ValidationError("New password must be at least 12 characters.")
    if len(new_password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")
    user.password_hash = hash_password(new_password)
