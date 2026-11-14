"""
Shekel Budget App -- Auth route package: shared constants and helpers.

Single source for the security constants and private helpers the auth
sub-modules co-own.  The MFA pending-state cluster
(``_MFA_PENDING_KEYS`` / ``_MFA_PENDING_MAX_AGE`` /
:func:`_clear_mfa_pending_state` / :func:`_mfa_pending_is_fresh`) spans
the two halves of the login flow -- the password POST in
:mod:`~app.routes.auth.credentials` writes the keys, ``/mfa/verify`` in
:mod:`~app.routes.auth.mfa` enforces freshness and clears them -- so it
lives here rather than in either route module; a typo on any one key
would silently break the flow, which is why the key names are
centralised at all.  :func:`_browser_standing` (and :func:`_known_device`)
is the one read, per request, of what the browser's cookie is worth to its
checks (plan#2772); a finished sign-in or re-confirm reads the record once
more to renew it, and :func:`_browser_standing` says why the two can disagree
only harmlessly.  :func:`_users_knowing_this_browser` is the one question
``/login`` asks of every cookie the browser sent, before the account is found,
and :func:`_spend_a_try` the one spending of a door's tries from it, at the
code form and at re-confirm.
:func:`_complete_sign_in` is the ONE completion of a sign-in, whichever proof
finished it: a password with no second factor, a password and a code, or a
password on a trusted device (plan#2173), so the three cannot drift on what a
signed-in session carries, what the browser holds afterwards (plan#2772) or
where it lands.
:func:`_verify_totp_with_replay_logging` (and its
backup-code sibling :func:`_consume_backup_code`) is shared by
``/mfa/verify`` and the step-up ``/reauth`` so both enforce one
replay-prevention policy.
:func:`_first_validation_message` and :func:`_is_safe_redirect` are the
cross-module form-error and open-redirect utilities.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import flash, g, redirect, request, session as flask_session, url_for
from flask_login import login_user
from werkzeug.wrappers import Response

from cryptography.fernet import InvalidToken
from marshmallow import ValidationError as MarshmallowValidationError

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from app.rate_limits import limiter, whose_attempt
from app.services import (
    auth_service, guess_record_service, known_device_service, mfa_service,
    sign_in_service,
)
from app.services.known_device_service import BrowserStanding
from app.utils.device_cookie import device_token, device_tokens, set_device_cookie
from app.utils.log_events import (
    AUTH,
    EVT_TOTP_REPLAY_REJECTED,
    log_event,
)
from app.utils.session_helpers import (
    session_stamp, session_token, stamp_login_session,
)

logger = logging.getLogger(__name__)

# How long a pending setup key stays promotable after the latest load of
# /mfa/setup that showed it: each such load, in the sign-in that made the
# key, moves its expiry to this from now (ruling plan#3523), and /mfa/confirm
# refuses it once that has passed.  Bounds the server-side window in which an
# unconfirmed TOTP secret remains promotable to an active credential.  Long
# enough to accommodate users who fumble with an authenticator app and short
# enough that an attacker who briefly compromised the account cannot revisit
# /mfa/confirm later to silently enrol their own device.  See audit finding
# F-031 / commit C-05 of the 2026-04-15 security remediation plan.
MFA_SETUP_PENDING_TTL = timedelta(minutes=15)

# Maximum time between the password POST that establishes pending MFA
# state on /login and the verification POST on /mfa/verify that
# consumes it.  Bounds the cookie-replay window for an attacker who
# captured a session cookie immediately after the victim's password
# step but before the MFA step.  Five minutes is long enough for a
# real user to reach for their phone and type a code, short enough
# that a stolen cookie is uninteresting -- the TOTP code itself
# rotates every 30 seconds, so the only useful attack window is the
# one this constant closes.  See audit finding F-002 / commit C-08.
_MFA_PENDING_MAX_AGE = timedelta(minutes=5)

# Session-cookie keys used to carry MFA pending state across the two
# halves of the login flow (password POST -> /mfa/verify POST).
# Each is named once here, and the login route, the verify route and
# the pending-state helpers below read and write it only by that name:
# a key misspelt at one site would read ``None`` and silently break the
# flow, where a misspelt name is an undefined variable pylint refuses.
_MFA_PENDING_USER_ID_KEY = "_mfa_pending_user_id"
_MFA_PENDING_NEXT_KEY = "_mfa_pending_next"
_MFA_PENDING_AT_KEY = "_mfa_pending_at"
_MFA_PENDING_KEYS = (
    _MFA_PENDING_USER_ID_KEY,
    _MFA_PENDING_NEXT_KEY,
    _MFA_PENDING_AT_KEY,
)

# Session-cookie key naming the browser that made a pending setup key (ruling
# plan#3523): ``/mfa/setup`` stores a fingerprint of the key's ciphertext here
# when it makes the key, and shows the key again only to a session holding it.
# A fingerprint, never the key or the ciphertext, because the cookie is signed
# but not encrypted (audit finding F-031 / commit C-05).  Every finished
# sign-in clears it (:func:`_complete_sign_in`), so the key is kept for the
# sign-in that made it: whoever signs in next on that browser, the user or
# anyone else with their password, is shown a new key.  Not one of
# ``_MFA_PENDING_KEYS``, which carry a sign-in's pending code: the code
# form's failed exits clear those too, and a setup has nothing to do with them.
_MFA_SETUP_FINGERPRINT_KEY = "_mfa_setup_fingerprint"


def _clear_mfa_pending_state():
    """Pop every MFA pending key from the request session.

    Used by every exit branch in :func:`~app.routes.auth.mfa.mfa_verify`
    -- the timeout rejection, the missing-user-or-reset-two-factor
    branch, the encryption-key-failure branch, and the
    successful-verification branch.  Centralising the cleanup means a
    future addition to ``_MFA_PENDING_KEYS`` is automatically picked up
    by every exit path; an inline ``flask_session.pop(...)`` block at
    each site would have to be updated in five places, with the usual
    miss-one-and-leak-state bug.

    Idempotent: safe to call when no pending state is present.
    """
    for key in _MFA_PENDING_KEYS:
        flask_session.pop(key, None)


def _browser_standing(user) -> BrowserStanding | None:
    """Return what this browser's cookie is worth for *user*, or ``None``.

    Read once per request for its checks, under the owner's lock, by every
    door with a password or code to check: ``/login`` right after it takes the
    lock for a browser :func:`_users_knowing_this_browser` found known,
    ``/mfa/verify`` right after it takes it, and the signed-in doors
    (``/reauth``, ``/change-password``), whose request took
    it at its boundary.  So everything the checks turn on judges the browser
    at one moment (plan#2772): the lock, the count and what a success clears,
    at ``/login`` the trusted skip of the code, and at ``/mfa/verify`` and
    ``/reauth`` whose tries are spent (plan#3025, ruling plan#3071).

    A finished sign-in or re-confirm reads the record once more, to renew it
    (:func:`~app.services.known_device_service.record_sign_in`,
    :func:`~app.services.known_device_service.record_re_confirm`).  At
    ``/login``, and at ``/reauth`` with no code, that read is in the same
    transaction as this one.  At ``/mfa/verify``, and at ``/reauth`` with a
    code, it is in a later one: the code's commit let the lock go, and the
    next transaction takes it again first.  The two reads can disagree only
    one way.  A record this read found may be gone by the second: a copy of
    the cookie's tenth wrong guess deleted it, or its thirty days ran out.
    The reverse cannot happen, because no write makes a token name a record
    it did not: only the renewal at a finished sign-in or re-confirm sets a
    record's token or moves its last sign-in, and it always sets a new token.
    When the record has gone, a sign-in gets a new row, as any browser that
    finishes one does (ruling plan#2994), and a re-confirm renews nothing.
    Neither clears the account's count in the record's place, since what a
    known browser's checks saw of it is nothing.

    It is the cookie for THAT user, judged by
    :func:`~app.services.known_device_service.find_browser`, so no cookie, a
    token no record holds, another user's token and a browser past its thirty
    days all answer ``None``.  So does a signed-in browser whose record has
    gone: being signed in is not being known, by one rule for every door
    (ruling plan#3034).

    Args:
        user: The :class:`~app.models.user.User` signing in or signed in,
            whose owner's lock is held.

    Returns:
        The browser's
        :class:`~app.services.known_device_service.BrowserStanding`, or
        ``None`` for a stranger.
    """
    return known_device_service.find_browser(user.id, device_token(user.id))


def _users_knowing_this_browser() -> frozenset[int]:
    """Return the users this browser is known to, asked before the account is found.

    ``/login``'s first question, which decides whether to take the owner's
    lock before the password check: a browser known to the account typed
    checks under it, a stranger with none held (ruling plan#3110).  So a
    made-up cookie never makes a stranger take the lock.  It asks of every
    device cookie the browser sent (:func:`~app.utils.device_cookie.device_tokens`)
    and never of the email, so a real email and an email no account has cost
    the same work given the same cookies (ruling plan#3130).  It loads no
    record (:func:`~app.services.known_device_service.known_to`), and the
    answer decides nothing else: under the lock :func:`_browser_standing`
    reads the browser again, and a record its own guesses deleted meanwhile
    is a stranger's.

    Returns:
        The ids of the users whose live record one of the browser's cookies
        names.
    """
    return known_device_service.known_to(device_tokens())


def _known_device(user) -> KnownDevice | None:
    """Return this browser's record for *user*, or ``None`` for a stranger.

    :func:`_browser_standing` for a door that asks only whose attempt it is,
    as the lock and the count do, and not whether the browser is trusted.

    Args:
        user: The :class:`~app.models.user.User` signing in or signed in,
            whose owner's lock is held.

    Returns:
        The known browser's :class:`~app.models.known_device.KnownDevice`, or
        ``None``.
    """
    standing = _browser_standing(user)
    return None if standing is None else standing.device


#: How many passwords or codes one counter lets a door check in its window:
#: the code form's and re-confirm's alike (rulings plan#3023, plan#3071).
_TRIES = "5 per 15 minutes"

#: Where :func:`_spend_a_try` leaves, on ``g``, the counter this request's try
#: is spent from.
_TRIES_KEY = "_tries_key"


def _tries_key() -> str:
    """Return the counter this request's try is spent from.

    The limit on :func:`_count_a_try` reads it, and :func:`_spend_a_try` put it
    there a moment before.  Read without a default: Flask-Limiter skips a limit
    whose key is empty, so a missing key must raise, never wave a try through.

    Returns:
        ``known-device:<id>`` or ``user:<id>``.
    """
    return getattr(g, _TRIES_KEY)


#: The name the tries' counters carry beside their door's endpoint.
_TRIES_SCOPE = "tries"


@limiter.limit(
    _TRIES, key_func=_tries_key, scope=_TRIES_SCOPE, override_defaults=False,
)
def _count_a_try() -> None:
    """Spend one try from the counter :func:`_tries_key` names.

    The body is empty because the decorator does the work: Flask-Limiter
    spends from the limit at each call and, past it, raises
    :class:`~flask_limiter.RateLimitExceeded`, which the 429 handler answers.
    A limit on a function the view calls, rather than on the view, is what
    lets it run after the view has read the browser's record under the
    owner's lock.  Its counters are scoped by the endpoint, as a limit on the
    view would be, so each door's tries are its own.  And by
    :data:`_TRIES_SCOPE`, because the tries count by the rule the app's other
    limits on a signed-in request count by
    (:func:`~app.rate_limits.whose_attempt`): a counter is its key, scope and
    limit, and ``/mfa/verify``'s own limit is the tries' limit, so without it
    the two would be one counter for any key they shared.  They share one
    only for a browser signed in with a code pending, which no flow of the
    app leaves (only a finished sign-in signs a browser in, and it clears
    the code's pending state), so the scope makes the two counters separate
    by construction rather than by that flow.

    It adds to the app's default limits rather than replacing them
    (``override_defaults=False``).  Once a request has spent a try here,
    Flask-Limiter ties this limit to the door's endpoint, and a limit that
    replaced the defaults would then leave the door's other requests under
    none: measured 2026-11-09, re-confirm's form page answered to no limit at
    all after the first re-confirm, where it had answered to the defaults.
    """


def _spend_a_try(user: User, device: KnownDevice | None) -> None:
    """Spend one of this door's tries before its password or code is checked.

    The code form and re-confirm each check at most :data:`_TRIES` from one
    counter.  A known browser spends its own, so someone who spends the
    account's cannot refuse the owner's browser there: a guesser with the
    password but not the phone at the code form (ruling plan#3023), or
    someone holding a stolen session at re-confirm (ruling plan#3071).  Every
    other browser spends the account's, which they all share, so moving
    between addresses or browsers buys no more guesses.  The rule is
    :func:`~app.rate_limits.whose_attempt`, the one the app's
    default limits on a signed-in page count by too (ruling plan#3108).

    The counter comes from *device*, the record
    :func:`~app.services.known_device_service.find_browser` verified under the
    owner's lock, never from the cookie the browser sent: a key built from
    that would give a guesser five new tries for each made-up cookie.

    Args:
        user: The :class:`~app.models.user.User` signing in or signed in,
            whose owner's lock is held.
        device: This browser's record, as the view read it under that lock,
            or ``None`` for a stranger.

    Raises:
        flask_limiter.RateLimitExceeded: The counter has no try left in this
            window.
    """
    counter = whose_attempt(user.id, None if device is None else device.id)
    setattr(g, _TRIES_KEY, counter)
    _count_a_try()


def _complete_sign_in(
    user, next_page: str | None, *,
    checked: guess_record_service.Held | None, trust: bool = False,
) -> Response:
    """Sign *user* in and return the redirect to where they land.

    The one completion of a sign-in, called once the proof is in: the
    ``/login`` password with no second factor, ``/mfa/verify``'s code, or
    the ``/login`` password on a trusted device (plan#2173).  It clears any
    pending code state (a completed sign-in leaves none behind) and any setup
    key's fingerprint (a pending setup key is shown again only in the sign-in
    that made it, ruling plan#3523), clears the
    account's wrong-guess count now that every check has passed (ruling
    plan#3007) if the record still holds what the checks saw, *checked*,
    records the browser (plan#2772,
    ruling plan#2994) so a password alone never makes one known on an account
    with a code, records the sign-in on the server from the same instant
    (ruling plan#3925), replacing the record this browser's previous session
    named, so a sign-out can end it and it ends when the browser stops being
    known (ruling plan#3069), calls ``login_user``, stamps the sign-in's token
    and every lifecycle key from that instant, so the idle and fresh-login
    checks start where the session did (commit C-10), and the record, which
    holds its user, lets no other user's id be loaded on it (plan#2173), and
    chooses the landing page.  What differs per proof stays with its caller:
    the log event, and a backup code's sign-out of every other session, which
    runs before this.

    A known browser's sign-in writes nothing to the account's count or lock,
    so it refills no stranger's guesses; the browser's own count clears in its
    record.  A stranger's finished sign-in does clear the account's count and
    lock, every check it faced having passed (ruling plan#3007).  A stranger's
    wrong guess judged after this sign-in's checks takes no owner lock (ruling
    plan#3110), so the clear leaves a record that has counted one since
    (:func:`auth_service.clear_failed_attempts`).

    Both records are committed before their tokens ride the response, so a
    browser never holds a token whose record a failed commit lost.

    Args:
        user: The :class:`~app.models.user.User` whose proof was accepted.
        next_page: Where the sign-in was asked for from, or ``None``.
            Validated here, at redirect time, whatever validated it before.
        checked: What the strangers' record of the account's email held when
            this sign-in's checks were judged: the ``/login`` judgment's
            (:attr:`~app.services.auth_service.PasswordProof.held`) or the
            code form's read of the lock
            (:func:`~app.services.auth_service.read_the_lock`).  ``None`` for
            a known browser, whose sign-in leaves the strangers' record alone,
            and for a stranger whose checks found no record.  Keyword-only
            with no default, so no caller can forget it.
        trust: Whether the user typed a code and asked to trust this browser.

    Returns:
        A redirect carrying the device cookie: a companion to
        ``companion.index`` always, anyone else to a safe *next_page* or the
        dashboard.
    """
    _clear_mfa_pending_state()
    # A pending setup key is shown again only in the sign-in that made it
    # (ruling plan#3523): this one, whoever's, starts with no fingerprint.
    flask_session.pop(_MFA_SETUP_FINGERPRINT_KEY, None)
    auth_service.clear_failed_attempts(checked)
    # One instant for both records and the session's stamps: a session ends
    # KNOWN_LIFETIME after its sign-in's record (ruling plan#3069), and one
    # read a moment after the browser's would outlive the browser's standing.
    now = datetime.now(timezone.utc)
    token = known_device_service.record_sign_in(
        user.id, device_token(user.id),
        request.headers.get("User-Agent", ""), now=now, trust=trust,
    )
    sign_in_token = sign_in_service.record_sign_in(
        user.id, session_token(), now=now,
    )
    db.session.commit()
    login_user(user)
    stamp_login_session(now, sign_in_token)
    response = redirect(_landing_page(user, next_page))
    set_device_cookie(response, user.id, token)
    return response


def _landing_page(user, next_page: str | None) -> str:
    """Return where a signed-in *user* lands.

    Args:
        user: The :class:`~app.models.user.User` just signed in.
        next_page: Where the sign-in was asked for from, or ``None``.

    Returns:
        ``companion.index`` for a companion always, else a safe *next_page* or
        the dashboard.
    """
    if user.role_id == ref_cache.role_id(RoleEnum.COMPANION):
        return url_for("companion.index")
    if not _is_safe_redirect(next_page):
        next_page = None
    return next_page or url_for("dashboard.page")


def _verify_totp_with_replay_logging(mfa_config, code, user_id):
    """Verify a TOTP code, log replays, and commit on success.

    Wraps :func:`mfa_service.verify_totp_code` to keep the
    replay-rejection logging and the post-success commit out of the
    route's main flow -- :func:`~app.routes.auth.mfa.mfa_verify` was
    over its ``too-many-branches`` budget after the C-09 changes added
    the REPLAY/ACCEPTED branches inline.  Pulling the three-way enum
    handling into a helper restores the route to a flat sequence of
    business steps and keeps each function within Pylint's limits.

    On REPLAY, emits the structured ``totp_replay_rejected`` event
    that F-142 requires.  The event includes ``user_id`` and
    ``ip`` so SOC tooling can correlate replay attempts to a
    specific account and source address.

    On ACCEPTED, commits the SQLAlchemy session so
    ``mfa_config.last_totp_timestep`` -- which the verifier mutated
    in place -- persists across requests.  Without this commit, a
    crash later in the route would leave the matched step
    un-recorded and replayable on a retry.

    Args:
        mfa_config: The user's :class:`~app.models.user.MfaConfig`
            row.  Mutated in place on ACCEPTED.
        code: The 6-digit TOTP code submitted by the user.
        user_id: The :attr:`User.id` whose pending login is being
            verified.  Carried into the replay-rejected log event.

    Returns:
        bool: True if the code was ACCEPTED.  False if it was
            REPLAY or INVALID.

    Raises:
        cryptography.fernet.InvalidToken: If the encrypted secret
            cannot be decrypted under any current Fernet key.  Caller
            handles via the encryption-key-failure redirect.
        RuntimeError: If ``TOTP_ENCRYPTION_KEY`` is unset.  Same
            handling.
    """
    result = mfa_service.verify_totp_code(mfa_config, code)
    if result is mfa_service.TotpVerificationResult.REPLAY:
        log_event(
            logger, logging.WARNING, EVT_TOTP_REPLAY_REJECTED, AUTH,
            "TOTP replay attempt rejected",
            user_id=user_id, ip=request.remote_addr,
        )
    if result is mfa_service.TotpVerificationResult.ACCEPTED:
        db.session.commit()
        return True
    return False


# Operator-facing flash for an already-authenticated TOTP step that
# cannot run because the encryption key is missing or rotated away.
# Raised by /reauth through
# :func:`_totp_accepted_or_key_failure`; /mfa/verify keeps its own
# variant because its recovery differs (pending login state is cleared
# and the user is bounced to /login).
_MFA_KEY_FAILURE_MESSAGE = (
    "MFA could not be verified because the encryption key has changed "
    "or been removed. Contact your administrator."
)


def _totp_accepted_or_key_failure(mfa_config, totp_code, user_id):
    """Run the step-up TOTP check, flashing the key-failure message.

    The already-authenticated TOTP gate of step-up ``/reauth``, which
    verifies through :func:`_verify_totp_with_replay_logging` and
    reports a decrypt failure (missing ``TOTP_ENCRYPTION_KEY``,
    ciphertext unreadable under any current Fernet) with an
    operator-actionable danger flash.  The caller keeps its failure
    *response* (re-render the reauth form), and this helper owns the
    verify + key-failure-flash pair.

    Args:
        mfa_config: The user's :class:`~app.models.user.MfaConfig` row;
            forwarded to the replay-logging verifier (mutated +
            committed on ACCEPTED).
        totp_code: The submitted 6-digit TOTP code.
        user_id: The verifying user's id, carried into the replay log
            event.

    Returns:
        ``(accepted, key_failure)``: ``(True, False)`` when the code
        was ACCEPTED, ``(False, False)`` when it was REPLAY/INVALID,
        and ``(False, True)`` when the encryption key failed -- the
        flash has already been emitted in that case and the caller
        returns its route-specific failure response.
    """
    try:
        accepted = _verify_totp_with_replay_logging(
            mfa_config, totp_code, user_id,
        )
    except (RuntimeError, InvalidToken):
        flash(_MFA_KEY_FAILURE_MESSAGE, "danger")
        return False, True
    return accepted, False


def _consume_backup_code(mfa_config, plaintext):
    """Verify a backup code against stored hashes and consume on match.

    Single-use backup codes work as a one-time bypass for the TOTP
    requirement.  Single-use holds only because the caller took the
    signing-in owner's lock before *mfa_config* was read (ruling
    **R-CC121**): this rewrites the list it was handed, so two sign-ins
    reading it together would each spend the same code, and one racing
    "Regenerate backup codes" would write the old list back over the new.
    This helper handles the verify-and-remove pair as
    one operation so :func:`~app.routes.auth.mfa.mfa_verify` does not
    have to inline the list-rebuild and commit (one less branch on its
    R0912 budget, and the consume step lives next to the verify step
    that motivates it).

    Args:
        mfa_config: The user's :class:`~app.models.user.MfaConfig`
            row; ``backup_codes`` is mutated in place on a match
            and the change is committed.
        plaintext: User-submitted backup code in plaintext.  Length-
            agnostic: pre-C-03 codes (8 hex chars) and post-C-03
            codes (28 hex chars) verify identically because bcrypt
            ignores length below the 72-byte input cap.

    Returns:
        True if ``plaintext`` matched a stored hash and was consumed.
        False if no hash matched (no DB change in that case).
    """
    idx = mfa_service.verify_backup_code(plaintext, mfa_config.backup_codes)
    if idx < 0:
        return False
    mfa_config.backup_codes = [
        h for i, h in enumerate(mfa_config.backup_codes) if i != idx
    ]
    db.session.commit()
    return True


def _mfa_pending_is_fresh():
    """Return True iff the session's ``_mfa_pending_at`` is recent.

    Reads ``_mfa_pending_at`` (an ISO-8601 string written by /login
    when MFA is required) through
    :func:`~app.utils.session_helpers.session_stamp`, the one parse of
    a session stamp, and compares it against
    ``datetime.now(timezone.utc)``.  Returns False, NEVER raising,
    for any of the following adversarial conditions:

      * No readable stamp: the key is missing (pending state was
        constructed without a timestamp: a pre-C-08 cookie still in
        flight, or a tampered/forged cookie missing the field), or its
        value is not ISO-8601 or is naive, each of which
        :func:`~app.utils.session_helpers.session_stamp` reads as
        ``None``.
      * The pending timestamp is in the future -- clock skew or
        tampered cookie; treat as invalid rather than honouring an
        impossible state.
      * The age exceeds ``_MFA_PENDING_MAX_AGE`` -- the legitimate
        case the constant exists to enforce.

    Fail-closed across the board.  The cost of a false rejection is
    one extra password retry by the user; the cost of a false
    acceptance is the entire F-002 finding (a stolen cookie can
    complete login weeks after the password step).
    """
    pending_at = session_stamp(_MFA_PENDING_AT_KEY)
    if pending_at is None:
        return False
    elapsed = datetime.now(timezone.utc) - pending_at
    if elapsed < timedelta(0):
        # Future-dated pending state -- only reachable via a tampered
        # cookie (the SECRET_KEY signature would have to be forged)
        # or a backwards clock jump on the server.  Either way the
        # value cannot be trusted.
        return False
    return elapsed <= _MFA_PENDING_MAX_AGE


def _first_validation_message(exc):
    """Return the first user-facing message from a Marshmallow ValidationError.

    Marshmallow accumulates errors in a nested dict keyed by field
    name (or ``_schema`` for cross-field validators).  The auth blueprint's
    user-facing flow surfaces a single message at a time -- the user
    fixes the first problem, resubmits, and sees the next one if any
    remain -- so this helper flattens the dict to one string and falls
    back to a generic message if the structure cannot be parsed (which
    should be impossible for the schemas in this package but is
    defended against to keep the route from raising 500 on a future
    schema change).

    The traversal is depth-first and prefers the first leaf string it
    finds, mirroring Marshmallow's own iteration order.  Field-level
    errors win over ``_schema`` cross-field errors only when both are
    present at the top level; otherwise whichever is encountered first
    is used.
    """
    messages = exc.messages
    if isinstance(messages, str):
        return messages
    if isinstance(messages, list) and messages:
        first = messages[0]
        return first if isinstance(first, str) else "Invalid input."
    if isinstance(messages, dict):
        for value in messages.values():
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, str):
                    return first
            if isinstance(value, dict):
                # Nested dict -- recurse with a synthetic exception so the
                # same flattening logic applies all the way down.
                inner = MarshmallowValidationError(value)
                return _first_validation_message(inner)
    return "Invalid input."


def _is_safe_redirect(target):
    """Check that a redirect target is a safe, relative URL.

    Prevents open redirect attacks by rejecting any URL that contains
    a scheme (http, https, javascript, data, etc.) or a network
    location (netloc).  Also rejects protocol-relative URLs (//evil.com),
    backslash-prefixed URLs (\\\\evil.com -- some browsers normalize \\\\ to //),
    and targets with embedded newlines or whitespace that could bypass
    parsing.

    Args:
        target: The redirect URL string from request.args or session.

    Returns:
        True if the target is a safe relative path (e.g. /templates,
        /settings?section=security).  False for None, empty strings,
        absolute URLs, protocol-relative URLs, and malformed inputs.
    """
    if not target:
        return False

    # Strip leading/trailing whitespace -- browsers may normalize this.
    stripped = target.strip()
    if not stripped:
        return False

    # Reject targets containing newlines, carriage returns, or tabs
    # (header injection / parser confusion), and backslash-prefixed paths
    # (some browsers normalize \\ to //, making \\evil.com a protocol-
    # relative URL).
    if any(c in stripped for c in ("\n", "\r", "\t")) or stripped.startswith("\\"):
        return False

    parsed = urlparse(stripped)

    # Reject any URL with a scheme (http, https, javascript, data, ftp,
    # etc.) or a network location (//evil.com parses with netloc="evil.com"
    # and no scheme).
    if parsed.scheme or parsed.netloc:
        return False

    return True
