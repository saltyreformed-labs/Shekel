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
from datetime import timedelta
from typing import NamedTuple

import bcrypt
import requests

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from app.exceptions import AuthError, ValidationError
from app.services import guess_record_service, known_device_service
from app.utils.log_events import (
    AUTH,
    EVT_ACCOUNT_LOCKED,
    EVT_HIBP_CHECK_FAILED,
    EVT_HIBP_CHECK_REJECTED,
    EVT_KNOWN_DEVICE_DELETED,
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
      via the lockout-counter increment at sign-in
      (:func:`check_strangers_password`) and the structured
      ``account_locked`` log event once the threshold trips.

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


# Timing-equalization dummy hash (deep-hunt #27).  A stranger's try at an
# email no account has is checked against this hash
# (:func:`check_strangers_password`), so it costs the same bcrypt work as a
# try at a real account, whose own hash is checked, locked or not (ruling
# plan#3110).  Without it the no-account try would return before any bcrypt
# work while a real account's pays a full ``bcrypt.checkpw``, letting an
# attacker tell by timing ``/login``, despite the constant response message,
# that an address is registered.  Generated once at import with the same
# default cost factor as real password hashes (``bcrypt.gensalt()`` in
# :func:`hash_password`), so if bcrypt's default rounds change the
# equalization cost tracks it automatically.  The plaintext is irrelevant --
# only the ``bcrypt.checkpw`` cost matters, and a try at an email no account
# has is wrong whatever the check answers.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"shekel-timing-equalization-placeholder", bcrypt.gensalt()
).decode("utf-8")

# What the sign-in checks refuse with, a disabled account's refusal aside, and
# what ``/login`` flashes for every refusal, that one included, so no reply
# tells a stranger which of them it was.
INVALID_SIGN_IN_MESSAGE = "Invalid email or password."


class PasswordProof(NamedTuple):
    """A stranger's right password at ``/login``, judged and committed, not yet confirmed.

    What :func:`check_strangers_password` hands the sign-in for the owner's
    lock and for :func:`confirm_strangers_password` under it (ruling
    plan#3110).  Read before the check's commit expired the account's row, so
    taking the lock reads nothing first.

    Attributes:
        email: The email typed, whose record judged the try.
        user_id: The account the password is right for.
        owner_id: Its :attr:`~app.models.user.User.data_owner_id`, whose lock
            the sign-in takes: ``None`` for a companion with no linked owner,
            who takes none.
        password_hash: The hash the password was checked against.
    """

    email: str
    user_id: int
    owner_id: int | None
    password_hash: str


def find_sign_in_user(email):
    """Return the user who signs in with *email*, or ``None``: sign-in's read before any lock.

    The first act of sign-in, split from the password check at plan step
    ``balance:X-bn`` so the route can choose, between the two, whether and
    when to take the account's owner lock
    (:func:`app.db_transaction.bind_sign_in_owner`): a browser known to the
    account takes it before its check (:func:`authenticate_known_browser`),
    and a stranger only after its check has committed, on the way to signing
    in (:func:`check_strangers_password`, ruling plan#3110).  This layer takes
    no lock itself (the lock's home is :mod:`app.db_transaction`).

    Args:
        email: The address typed at the login form.

    Returns:
        The matching :class:`~app.models.user.User`, or ``None`` when no
        account has that address.
    """
    return db.session.query(User).filter_by(email=email).first()


def is_locked_out(user: User, *, device: KnownDevice | None) -> bool:
    """Return True while the lock refuses this attempt on *user*.

    The lock is the one on the record of the account's email (ruling
    plan#3110, :func:`~app.services.guess_record_service.is_locked`), and it
    refuses only strangers (plan#2772): a known browser, *device*, passes it,
    so no one who knows the email can keep the owner out of a browser they
    have signed in on.  A known browser's own wrong guesses count on its
    record instead (:func:`record_failed_attempt`).

    The record's ``locked_until`` is an exclusive upper bound: at the instant
    it equals ``now`` the lock is over, and a value in the past is the same as
    no lock to every check.

    Args:
        user: The account being checked.
        device: This browser's record for *user*, as
            :func:`~app.services.known_device_service.find_browser` read it
            under the owner's lock, or ``None`` for a stranger.  Keyword-only
            with no default, as in every public function below that takes
            it: a default would let a door that forgot the browser judge a
            known browser as a stranger.

    Returns:
        True while the account's lock window is open and the attempt is a
        stranger's.
    """
    return device is None and guess_record_service.is_locked(user.email)


def record_failed_attempt(user: User, *, device: KnownDevice | None) -> None:
    """Count one wrong password or code on *user*, from a stranger or a known browser.

    The count of a wrong guess at every door but a stranger's ``/login``
    password, which judges its every try in :func:`check_strangers_password`:
    the code form, the signed-in password and code checks, and a known
    browser's sign-in password (:func:`authenticate_known_browser`).  The
    caller holds the owner's lock (ruling **R-CC121**), so a known browser's
    two guesses cannot count once; a stranger's are serialised by the
    record's own row lock.  Whose count it is turns on the browser
    (plan#2772), at one threshold (``LOCKOUT_THRESHOLD``, default 10):

    * A stranger's guess counts on the record of the account's email
      (:func:`_judge_strangers_try`).  A guess the lock refuses is not
      counted: every door that calls this asks :func:`is_locked_out` first,
      so that is a guess that met a lock another try opened since.
    * A known browser's guess counts on its own record, *device*, and never on
      the account.  At the threshold the record is deleted, so the browser is
      a stranger again and the user's other browsers are untouched (ruling
      plan#2996), and the structured ``known_device_deleted`` event is
      emitted.

    Commits, even short of the threshold, so the count survives the caller's
    refusal.

    Args:
        user: The account the guess was against.
        device: This browser's record for *user*, as
            :func:`~app.services.known_device_service.find_browser` read it
            under the owner's lock, or ``None`` for a stranger.
    """
    if device is None:
        _judge_strangers_try(user.email, user.id, wrong=True)
    else:
        _count_on_the_browser(user, device)
    db.session.commit()


def _judge_strangers_try(email: str, user_id: int | None, *, wrong: bool) -> bool:
    """Judge one stranger's try at *email* on its record; return whether the lock refused it.

    The ONE home of a stranger's count and trip, whichever door the try came
    to (:func:`~app.services.guess_record_service.judge_try`, ruling
    plan#3110): a wrong try counts, and at the threshold the lock opens for
    ``LOCKOUT_DURATION_MINUTES`` and the count is zeroed, so a second lock
    takes another threshold-many failures rather than one more.  A lock that
    opens at an account's email emits the structured ``account_locked``
    event; one at an email no account has has no account to name.

    Writes the record; the caller commits.

    Args:
        email: The email the try named.
        user_id: The account that email names, or ``None`` when none does.
        wrong: Whether the password or code was wrong.

    Returns:
        True when the record's lock was open, so the try was refused and not
        counted.
    """
    outcome = guess_record_service.judge_try(
        email, wrong=wrong,
        threshold=_get_lockout_threshold(), duration=_get_lockout_duration(),
    )
    if outcome.opened_lock_until is not None and user_id is not None:
        log_event(
            logger, logging.WARNING, EVT_ACCOUNT_LOCKED, AUTH,
            "Account locked after consecutive wrong passwords or codes",
            user_id=user_id, lockout_until=outcome.opened_lock_until.isoformat(),
        )
    return outcome.refused


def _count_on_the_browser(user: User, device: KnownDevice) -> None:
    """Count a known browser's wrong guess on its record, deleting it at the threshold.

    Args:
        user: The account the guess was against.
        device: That browser's record for *user*.
    """
    device_id = device.id
    if known_device_service.count_failed_attempt(device, _get_lockout_threshold()):
        log_event(
            logger, logging.WARNING, EVT_KNOWN_DEVICE_DELETED, AUTH,
            "Known browser made a stranger after consecutive wrong guesses",
            user_id=user.id, device_id=device_id,
        )


def clear_failed_attempts(user: User) -> None:
    """Clear the account's count and any lock, once a stranger's checks have all passed.

    Called at a stranger's finished sign-in and finished re-confirm, and
    nowhere else (ruling plan#3007).  A correct password alone does not clear
    it: someone holding the password but not the second factor could otherwise
    re-enter it between wrong codes, and the lock would never fire.

    Never at a known browser's: the account's count and lock are the
    strangers', and clearing them there would refill a stranger's guesses
    (plan#2772).  A known browser's own count clears where its record is
    renewed with a new token, at a finished sign-in
    (:func:`~app.services.known_device_service.record_sign_in`) or re-confirm
    (:func:`~app.services.known_device_service.record_re_confirm`).  So this
    takes no browser: the two completions that call it are the stranger's
    side of a choice they each make once.

    The clear deletes the record of the account's email
    (:func:`~app.services.guess_record_service.clear`).  Commits only when
    there was a record: a stranger's sign-in finds the one its own try's
    judgment wrote (ruling plan#3110), unless another finished sign-in
    cleared it since, while a re-confirm on an account with no failure
    history finds none, and the commit would write nothing.

    Args:
        user: The account whose checks have all passed.
    """
    if guess_record_service.clear(user.email):
        db.session.commit()


def check_signed_in_password(
    user: User, password: str, wrong_password_message: str,
    *, device: KnownDevice | None,
) -> None:
    """Check a signed-in door's password, counting a wrong one toward the lock.

    The one password check of ``/reauth``, ``/change-password`` and
    ``/mfa/disable``, with sign-in's one rule for whose attempt it is
    (plan#2772, ruling plan#3034): a known browser, *device*, passes the
    account's lock and counts a wrong password on its own record, and a
    stranger meets the lock and counts on the account.  The lock is checked
    first, so bcrypt never runs on a locked attempt.  Unlike ``/login`` the
    refusal names the lock: whoever is
    here holds a live session on this account, so the lock's existence tells
    them nothing a stranger could use, and neither does how fast it is said.
    ``/login`` hides the lock, so there a stranger's every try runs the same
    steps, locked or not (:func:`check_strangers_password`, ruling
    plan#3110).

    Args:
        user: The signed-in account.  Its row was reloaded under the owner's
            lock at the request's boundary.
        password: The password typed.
        wrong_password_message: What this door says to a wrong password.
        device: This browser's record for *user*, as
            :func:`~app.services.known_device_service.find_browser` read it
            under the owner's lock, or ``None`` for a stranger.

    Raises:
        AuthError: With :data:`LOCKED_OUT_MESSAGE` while the lock refuses
            this attempt, else with *wrong_password_message* when the password
            is wrong.
    """
    if is_locked_out(user, device=device):
        raise AuthError(LOCKED_OUT_MESSAGE)
    if not verify_password(password, user.password_hash):
        record_failed_attempt(user, device=device)
        raise AuthError(wrong_password_message)


def check_strangers_password(
    email: str, user: User | None, password: str,
) -> PasswordProof:
    """Check a stranger's password at ``/login``, judge the try, and commit.

    Every stranger's try runs the same steps, whatever the email and the
    answer (ruling plan#3110): one password check, against the account's hash
    or, where no account has *email*, the dummy one; the guess record's one
    statement, which judges the lock and counts the guess
    (:func:`_judge_strangers_try`); and a commit.  So a wrong password at a
    real account costs what one at an email no account has does, a locked
    account's reply costs what an unlocked one's does, and a right password
    and a wrong one cost the same at a locked account, so a lock cannot be
    used to confirm a guess.  The lock is judged after the check, by that one
    statement, so no try waits on another try at the same email while bcrypt
    runs.  ``verify_password`` short-circuits empty and non-string passwords
    the same way against either hash, so every input shape costs the same on
    every path.

    No owner lock is needed here: the count is the record's and its row lock
    serialises it.  ``/login`` calls this with none held, but for a browser
    whose record its own guesses deleted after the route found it known and
    took the lock (the route's ``_check_the_password``).  Only a right
    password the lock let
    through goes on: the caller takes the owner's lock of
    :attr:`PasswordProof.owner_id`, then calls
    :func:`confirm_strangers_password`.  The count is NOT cleared here: for
    an account with a second factor this is only the first check, and the
    count clears when every check has passed (:func:`clear_failed_attempts`,
    ruling plan#3007).

    Every refusal says :data:`INVALID_SIGN_IN_MESSAGE`, so a stranger
    probing whether an email is real or locked learns nothing from the
    words either.

    An *email* that is not a non-empty string names no record and no account,
    and is refused before any check, as :func:`verify_password` refuses such a
    password: the login form's schema never passes one, so only a broken
    caller reaches it, and a refusal is safer than a crash computing its
    digest.

    Args:
        email: The email typed, as the form normalised it.
        user: The account :func:`find_sign_in_user` found for it, or
            ``None``.
        password: The password typed.

    Returns:
        The :class:`PasswordProof` the sign-in confirms under the owner's
        lock.

    Raises:
        AuthError: No account has *email*, the password is wrong, the lock on
            the record of *email* refused the try, right password or not, or
            *email* is not a non-empty string.
    """
    if not isinstance(email, str) or not email:
        raise AuthError(INVALID_SIGN_IN_MESSAGE)
    password_hash = _DUMMY_PASSWORD_HASH if user is None else user.password_hash
    right = verify_password(password, password_hash)
    proof = None if user is None else PasswordProof(
        email=email, user_id=user.id, owner_id=user.data_owner_id,
        password_hash=password_hash,
    )
    refused = _judge_strangers_try(
        email, None if proof is None else proof.user_id,
        wrong=proof is None or not right,
    )
    db.session.commit()
    if proof is None or refused or not right:
        raise AuthError(INVALID_SIGN_IN_MESSAGE)
    return proof


def confirm_strangers_password(user: User, proof: PasswordProof) -> User:
    """Confirm, under the owner's lock, that a stranger's right password still signs in.

    The second half of a stranger's sign-in (ruling plan#3110).  The caller
    took the lock of ``proof.owner_id`` after :func:`check_strangers_password`
    committed, and that commit expired *user*'s row, so each column read here
    is read afresh: under the lock, for every account but a companion with no
    linked owner, who has no owner to lock (``owner_id`` is ``None``).  A
    password changed while the check ran leaves a hash other than the one
    checked, and the try counts as a wrong guess on the record of the email
    typed and is refused: the password typed was checked against a hash the
    account no longer holds.  That try is judged twice, so it writes two of
    the record's audit rows, where every other try writes one.

    Args:
        user: The account :func:`check_strangers_password` checked.
        proof: What it returned.

    Returns:
        *user*, signed in by its password.

    Raises:
        AuthError: The account's hash changed since the check, or the
            account is disabled (:func:`_refuse_a_disabled_account`).
    """
    if user.password_hash != proof.password_hash:
        _judge_strangers_try(proof.email, proof.user_id, wrong=True)
        db.session.commit()
        raise AuthError(INVALID_SIGN_IN_MESSAGE)
    _refuse_a_disabled_account(user)
    return user


def authenticate_known_browser(
    user: User, password: str, *, device: KnownDevice,
) -> User:
    """Check *password* for *user* from a browser known to it, under the owner's lock.

    The caller took *user*'s owner lock and then read *device*, this
    browser's record, under it (ruling **R-CC121**, "Lock, then check"), so
    the browser's own count cannot interleave with another of its guesses.
    A known browser passes the lock strangers' guesses open on the account,
    and a wrong password counts on its own record, never on the account
    (plan#2772, :func:`record_failed_attempt`).  The count is NOT cleared
    here, for the reason :func:`check_strangers_password` gives.

    Args:
        user: The account :func:`find_sign_in_user` found.
        password: The password typed.
        device: This browser's record for *user*, as
            :func:`~app.services.known_device_service.find_browser` read it
            under the owner's lock.

    Returns:
        *user*, signed in by its password.

    Raises:
        AuthError: The password is wrong, or the account is disabled
            (:func:`_refuse_a_disabled_account`).
    """
    if not verify_password(password, user.password_hash):
        record_failed_attempt(user, device=device)
        raise AuthError(INVALID_SIGN_IN_MESSAGE)
    _refuse_a_disabled_account(user)
    return user


def _refuse_a_disabled_account(user: User) -> None:
    """Refuse a sign-in to *user* while the account is disabled.

    Asked only once the password is proven, so naming the disabled state is
    not an information leak: someone without the password never reaches it.

    Args:
        user: The account whose password was right.

    Raises:
        AuthError: ``"Account is disabled."`` when ``is_active`` is False.
    """
    if not user.is_active:
        raise AuthError("Account is disabled.")


def change_password(
    user: User, current_password: str, new_password: str,
    *, device: KnownDevice | None,
) -> None:
    """Change a user's password after verifying the current one.

    Args:
        user: The User object whose password is being changed.
        current_password: The user's current plaintext password.
        new_password: The new plaintext password (must be >= 12 chars).
        device: This browser's record for *user*, or ``None`` for a stranger:
            whose attempt the current password is
            (:func:`check_signed_in_password`).

    Returns:
        None on success.

    Raises:
        AuthError: If the lock refuses this attempt, or current_password does
            not match the stored hash (:func:`check_signed_in_password`).
        ValidationError: If new_password is shorter than 12 characters.
    """
    check_signed_in_password(
        user, current_password, "Current password is incorrect.",
        device=device,
    )
    if len(new_password) < 12:
        raise ValidationError("New password must be at least 12 characters.")
    if len(new_password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")
    user.password_hash = hash_password(new_password)
