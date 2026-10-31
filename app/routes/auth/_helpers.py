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
centralised at all.  :func:`_complete_sign_in` is the ONE completion of a
sign-in, whichever proof finished it: a password with no second factor, a
password and a code, or a password on a trusted device (plan#2173), so the
three cannot drift on what a signed-in session carries or where it lands.
:func:`_verify_totp_with_replay_logging` (and its
backup-code sibling :func:`_consume_backup_code`) is shared by
``/mfa/verify``, ``/mfa/disable``, and the step-up ``/reauth`` so all
three enforce one replay-prevention policy.
:func:`_first_validation_message` and :func:`_is_safe_redirect` are the
cross-module form-error and open-redirect utilities.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import flash, redirect, request, session as flask_session, url_for
from flask_login import login_user
from werkzeug.wrappers import Response

from cryptography.fernet import InvalidToken
from marshmallow import ValidationError as MarshmallowValidationError

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.services import mfa_service
from app.utils.log_events import (
    AUTH,
    EVT_TOTP_REPLAY_REJECTED,
    log_event,
)
from app.utils.session_helpers import stamp_login_session

logger = logging.getLogger(__name__)

# Maximum time between scanning the QR/typing the manual key on /mfa/setup
# and submitting the verification code on /mfa/confirm.  Bounds the
# server-side window in which an unconfirmed TOTP secret remains
# promotable to an active credential.  Long enough to accommodate users
# who fumble with an authenticator app and short enough that an attacker
# who briefly compromised the account cannot revisit /mfa/confirm later
# to silently enrol their own device.  See audit finding F-031 / commit
# C-05 of the 2026-04-15 security remediation plan.
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
# Centralised here so the login route, the verify route, and the
# pending-state helpers below stay in sync; a typo on any one key
# would silently break the flow.
_MFA_PENDING_KEYS = (
    "_mfa_pending_user_id",
    "_mfa_pending_next",
    "_mfa_pending_at",
)


def _clear_mfa_pending_state():
    """Pop every MFA pending key from the request session.

    Used by every exit branch in :func:`~app.routes.auth.mfa.mfa_verify`
    -- the timeout rejection, the missing-user branch, the disabled-MFA
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


def _complete_sign_in(user, next_page: str | None) -> Response:
    """Sign *user* in and return the redirect to where they land.

    The one completion of a sign-in, called once the proof is in: the
    ``/login`` password with no second factor, ``/mfa/verify``'s code, or
    the ``/login`` password on a trusted device (plan#2173).  It clears any
    pending code state (a completed sign-in leaves none behind), calls
    ``login_user``, stamps every lifecycle key from one instant so the idle
    and fresh-login checks start where the session did (commit C-10), and
    chooses the landing page.  What differs per proof stays with its caller:
    the log event, a backup code's sign-out of every other session, and a
    device's trust, each written to the response this returns.

    Args:
        user: The :class:`~app.models.user.User` whose proof was accepted.
        next_page: Where the sign-in was asked for from, or ``None``.
            Validated here, at redirect time, whatever validated it before.

    Returns:
        A redirect: a companion to ``companion.index`` always, anyone else to
        a safe *next_page* or the dashboard.
    """
    _clear_mfa_pending_state()
    login_user(user)
    stamp_login_session(datetime.now(timezone.utc))
    if user.role_id == ref_cache.role_id(RoleEnum.COMPANION):
        return redirect(url_for("companion.index"))
    if not _is_safe_redirect(next_page):
        next_page = None
    return redirect(next_page or url_for("dashboard.page"))


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
# Shared by /reauth and /mfa/disable via
# :func:`_totp_accepted_or_key_failure`; /mfa/verify keeps its own
# variant because its recovery differs (pending login state is cleared
# and the user is bounced to /login).
_MFA_KEY_FAILURE_MESSAGE = (
    "MFA could not be verified because the encryption key has changed "
    "or been removed. Contact your administrator."
)


def _totp_accepted_or_key_failure(mfa_config, totp_code, user_id):
    """Run the step-up TOTP check, flashing the key-failure message.

    The shared shape of the two already-authenticated TOTP gates --
    step-up ``/reauth`` and ``/mfa/disable`` -- which verify through
    :func:`_verify_totp_with_replay_logging` and report a decrypt
    failure (missing ``TOTP_ENCRYPTION_KEY``, ciphertext unreadable
    under any current Fernet) with the identical operator-actionable
    danger flash.  Only the failure *response* differs per route
    (re-render the reauth form vs redirect to security settings), so
    the caller keeps that decision and this helper owns the
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

    Reads ``flask_session["_mfa_pending_at"]`` (an ISO-8601 string
    written by /login when MFA is required) and compares it against
    ``datetime.now(timezone.utc)``.  Returns False, NEVER raising,
    for any of the following adversarial conditions:

      * No ``_mfa_pending_at`` key in the session -- pending state
        was constructed without a timestamp (pre-C-08 cookie still
        in flight, or a tampered/forged cookie missing the field).
      * The value is not parseable as ISO-8601 -- malformed cookie.
      * The parsed datetime is naive (no timezone) -- ill-formed
        cookie that would otherwise raise on the comparison.
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
    raw = flask_session.get("_mfa_pending_at")
    if not raw:
        return False
    try:
        pending_at = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if pending_at.tzinfo is None:
        # A naive datetime would raise on the timezone-aware
        # subtraction below.  Reject explicitly so the failure mode
        # is "expired pending state" (UX redirect to /login) rather
        # than "500 from the auth route".
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
