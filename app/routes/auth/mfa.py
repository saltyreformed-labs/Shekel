"""
Shekel Budget App -- Auth route package: MFA lifecycle.

The TOTP multi-factor family, deliberately co-located: login-time
verification (``/mfa/verify``, consuming the pending state the login
route in :mod:`~app.routes.auth.credentials` establishes), enrolment
(``/mfa/setup`` + ``/mfa/confirm``), backup-code regeneration, and
disablement (GET confirmation + POST).  Each route registers against
the shared ``auth_bp`` from :mod:`~app.routes.auth._bp`; the pending-
state freshness/cleanup helpers, the replay-logging TOTP verifier, the
backup-code consumer, and the setup TTL constant come from
:mod:`~app.routes.auth._helpers` so this module and the step-up
``/reauth`` flow enforce one replay-prevention policy.
"""

import logging
from datetime import datetime, timezone
from functools import wraps

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user

from cryptography.fernet import InvalidToken
from marshmallow import ValidationError as MarshmallowValidationError

from app.db_transaction import bind_sign_in_owner, write_transaction
from app.extensions import db
from app.models.known_device import TRUST_LIFETIME
from app.models.user import MfaConfig, User
from app.schemas.validation import (
    MfaConfirmSchema,
    MfaDisableSchema,
    MfaVerifySchema,
)
from app.services import auth_service, mfa_service
from app.exceptions import AuthError
from app.rate_limits import limiter
from app.routes.auth._bp import auth_bp
from app.routes.auth._helpers import (
    MFA_SETUP_PENDING_TTL,
    _MFA_PENDING_NEXT_KEY,
    _MFA_PENDING_USER_ID_KEY,
    _clear_mfa_pending_state,
    _complete_sign_in,
    _consume_backup_code,
    _known_device,
    _mfa_pending_is_fresh,
    _spend_a_try,
    _totp_accepted_or_key_failure,
    _verify_totp_with_replay_logging,
)
from app.utils.auth_helpers import fresh_login_required
from app.utils.log_events import (
    AUTH,
    EVT_BACKUP_CODES_REGENERATED,
    EVT_DEVICE_TRUSTED,
    EVT_MFA_DISABLED,
    EVT_MFA_ENABLED,
    EVT_LOGIN_FAILED,
    EVT_MFA_LOGIN_SUCCESS,
    EVT_REAUTH_FAILED,
    log_event,
)
from app.utils.security_events import (
    SecurityEventKind,
    record_security_event,
)
from app.utils.session_helpers import invalidate_other_sessions

logger = logging.getLogger(__name__)


def _check_mfa_code(mfa_config, user_id, totp_code, backup_code):
    """Verify a submitted TOTP or backup code; return (valid, used_backup_code).

    Checks the TOTP code if one was submitted, else the backup code (the
    backup path delegates to :func:`_consume_backup_code` so the
    verify+remove+commit stays one operation and length-agnostic across the
    pre-C-03 8-hex and post-C-03 28-hex code formats).  What makes each check
    and its write atomic against another sign-in or "Regenerate backup codes"
    is the signing-in owner's lock :func:`mfa_verify` takes before
    *mfa_config* is read (ruling **R-CC121**); without it both read-then-write
    pairs raced, and one code signed in two devices.  Neither code
    submitted -> ``(False, False)``.  The caller wraps this in the
    decrypt try/except, so a missing/rotated encryption key (RuntimeError
    / InvalidToken, raised by the up-front decrypt or the TOTP verifier's
    internal decrypt) surfaces there, not here.
    """
    if totp_code:
        return _verify_totp_with_replay_logging(mfa_config, totp_code, user_id), False
    if backup_code:
        used = _consume_backup_code(mfa_config, backup_code)
        return used, used
    return False, False


def _render_verify_form():
    """Render the code form, from its three render sites.

    Passes the trust's length in days, so the "Trust this device" box states
    :data:`~app.models.known_device.TRUST_LIFETIME` rather than a second
    spelling of it.

    Returns:
        The rendered ``/mfa/verify`` page.
    """
    return render_template(
        "auth/mfa_verify.html", trust_days=TRUST_LIFETIME.days,
    )


def _flash_backup_codes_left(mfa_config: MfaConfig) -> None:
    """Tell a backup-code sign-in how many codes are left and how long it has.

    Re-confirm takes no backup code, so someone without their phone can
    replace their codes only inside the fresh-login window this sign-in just
    opened, the one Regenerate's ``@fresh_login_required()`` reads (ruling
    plan#3035).  The minutes are that config value, never a second spelling.

    Args:
        mfa_config: The signing-in user's config, the spent code already
            removed from its ``backup_codes``.
    """
    minutes = current_app.config["FRESH_LOGIN_MAX_AGE_MINUTES"]
    unit = "minute" if minutes == 1 else "minutes"
    flash(
        f"You signed in with a backup code and have "
        f"{len(mfa_config.backup_codes)} left. For the next {minutes} {unit} "
        f"you can replace them on the Security page without your "
        f"authenticator app.",
        "warning",
    )


@auth_bp.route("/mfa/verify", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def mfa_verify():  # pylint: disable=too-many-return-statements
    """Display the MFA verification form and handle code submission.

    Requires a pending MFA user_id in the session (set by the login
    route after successful password verification). Completes the login
    on valid TOTP or backup code.  An authenticator code with "Trust this
    device" ticked also trusts this browser for the user (plan#2173); a
    backup code never does, whatever the form posts, and instead says how
    many codes are left and how long there is to replace them.

    Pending state freshness is enforced on every entry to this view
    (both GET and POST) via :func:`_mfa_pending_is_fresh`.  A pending
    state older than ``_MFA_PENDING_MAX_AGE`` is cleared and the user
    is bounced back to /login with a flash message -- the F-002
    cookie-replay window closes at exactly this gate.

    A wrong, replayed or empty code and a wrong backup code count toward the
    lock, and a stranger is refused here while the account is locked, even
    with the right code.  A known browser counts on its own record and passes
    the account's lock, as at the password (plan#2772).  Not counted: a form
    the schema rejects, which cannot be a right answer, and a failed
    encryption key, which is the operator's fault.

    Two limits of its own apply, beside the app's defaults.  Every POST spends
    its address's.  Each code checked also spends a try (:func:`_spend_a_try`):
    a known browser's own, or else the account's, which every other browser
    shares.  So moving between addresses buys a stranger no more guesses, and
    a guesser who spends the account's tries still leaves the owner's browser
    its own (ruling plan#3023).  A request that checks no code spends no try:
    one with no live pending sign-in, a form the schema rejects, a pending
    user gone or without two-factor, and a stranger the lock refuses.

    Pylint: ``too-many-return-statements`` (9/6) -- each early return is a
    distinct semantic exit (no pending state, stale state, GET render,
    malformed form, missing user-or-config, locked, key failure, invalid code,
    success), each with its own clear/flash/redirect side effects, so
    they cannot be collapsed without merging distinct security messaging.
    The explicit returns are the readable form.  (``too-many-branches`` is
    no longer suppressed -- the TOTP/backup-code branching was extracted to
    :func:`_check_mfa_code`.)
    """
    pending_user_id = flask_session.get(_MFA_PENDING_USER_ID_KEY)
    if not pending_user_id:
        # No pending state at all -- silently redirect.  This is the
        # normal case for a user who navigated directly to /mfa/verify
        # without completing the password step; no flash required.
        return redirect(url_for("auth.login"))

    if not _mfa_pending_is_fresh():
        # Pending state exists but is stale, malformed, or future-dated
        # (see _mfa_pending_is_fresh for the full failure list).  Clear
        # every pending key so a fresh /login starts from a clean slate
        # and surface a user-visible reason for the bounce -- without
        # the flash, a user who paused on the verify page for >5 min
        # would see only an unexplained redirect to /login.  F-002.
        _clear_mfa_pending_state()
        flash(
            "Two-factor authentication timed out. Please log in again.",
            "warning",
        )
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return _render_verify_form()

    # POST -- validate shape, then verify the submitted code.  F-163:
    # MfaVerifySchema caps backup_code at 32 characters so a
    # megabyte-sized string cannot reach bcrypt verification.  Both
    # fields default to "" so the "neither code typed" UX stays the
    # same -- the route below treats both empty as "Invalid
    # verification code." rather than as missing-field validation
    # error.  See commit C-26.
    try:
        verify_data = MfaVerifySchema().load(request.form)
    except MarshmallowValidationError:
        flash("Invalid verification code.", "danger")
        return _render_verify_form()
    totp_code = verify_data["totp_code"]
    backup_code = verify_data["backup_code"]

    user = db.session.get(User, pending_user_id)
    mfa_config = None
    device = None
    if user:
        # The signing-in owner's lock BEFORE the codes are read (ruling
        # R-CC121, "Lock, then check"): the code check rewrites
        # ``backup_codes`` and ``last_totp_timestep``, and "Regenerate backup
        # codes" writes the same row under this lock.  Unlocked, a backup-code
        # sign-in racing a regenerate wrote the old codes back over the new
        # ones, and one code signed in two devices.  Under the lock, read once
        # whose attempt this is: the lock, the tries a code spends, the count
        # and what the completion clears all turn on it (plan#2772).
        bind_sign_in_owner(user.id, user.data_owner_id)
        device = _known_device(user)
        mfa_config = (
            db.session.query(MfaConfig)
            .filter_by(user_id=user.id, is_enabled=True)
            .first()
        )
    if not user or not mfa_config:
        # Either the user was deleted between login steps or MFA was
        # disabled out from under the pending session.  Both are race
        # conditions that resolve the same way: clear pending state
        # and silently bounce to /login.  No flash because a fresh
        # password entry will explain itself (invalid credentials if
        # the user is gone, no MFA prompt if MFA is disabled).
        _clear_mfa_pending_state()
        return redirect(url_for("auth.login"))

    # Read once, under the owner's lock taken above: whether the lock refuses
    # this attempt, and what a finished sign-in clears if the record still
    # holds it, since a stranger's guess judged during the code check takes
    # no owner lock (ruling plan#3110).
    reading = auth_service.read_the_lock(user, device=device)
    if reading.locked:
        # This person has the password, so naming the lock tells them
        # nothing a stranger could use.  Back to /login, where the lock
        # refuses them until it ends.  A known browser is never here: the
        # lock refuses only strangers.
        log_event(
            logger, logging.WARNING, EVT_LOGIN_FAILED, AUTH,
            "Login failed: account locked at the code form",
            user_id=pending_user_id, ip=request.remote_addr,
        )
        _clear_mfa_pending_state()
        flash(auth_service.LOCKED_OUT_MESSAGE, "danger")
        return redirect(url_for("auth.login"))

    # A code is about to be checked: spend one of this browser's tries, its
    # own when it is known (ruling plan#3023).  Past the limit this raises,
    # and the 429 handler answers before any code is checked or counted.
    _spend_a_try(user, device)

    # Single try/except wraps both the up-front decrypt (preserving
    # the pre-C-09 fail-fast behaviour: a missing encryption key
    # disables BOTH the TOTP and backup-code paths) and the verifier
    # (_check_mfa_code decrypts again internally on the TOTP path).
    # Any of three failure modes -- decrypt-now error, verifier
    # decrypt error, or impossible-but-defensive key swap mid-flow --
    # collapses into the same operator-side error message and
    # redirect.
    try:
        mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
        valid, used_backup_code = _check_mfa_code(
            mfa_config, user.id, totp_code, backup_code,
        )
    except (RuntimeError, InvalidToken):
        _clear_mfa_pending_state()
        flash(
            "MFA verification failed. The encryption key may have been "
            "changed or removed. Contact your administrator.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    if not valid:
        auth_service.record_failed_attempt(user, device=device)
        log_event(
            logger, logging.WARNING, EVT_LOGIN_FAILED, AUTH,
            "Login failed: bad code", user_id=pending_user_id,
            ip=request.remote_addr,
        )
        flash("Invalid verification code.", "danger")
        return _render_verify_form()

    # Login completion -- both password and TOTP/backup code are verified.
    # The pending ``next`` is read here, before the completion clears the
    # pending state, and validated again there (defense in depth -- it was
    # also validated at storage time in the login route).  The completion
    # stamps every lifecycle key from one instant.  The backup-code branch
    # below ALSO calls ``invalidate_other_sessions``, which writes its own
    # internal ``now`` to BOTH ``_session_created_at`` and the user's
    # ``session_invalidated_at``.  The two are equal, and the load_user check
    # is strict less-than, so this session survives while every earlier one
    # is refused.
    trust = verify_data["trust_device"] and not used_backup_code
    response = _complete_sign_in(
        user, flask_session.get(_MFA_PENDING_NEXT_KEY),
        checked=reading.held, trust=trust,
    )

    if used_backup_code:
        # Backup-code consumption is the canonical "I lost my
        # authenticator" signal -- the credential the user just spent
        # exists precisely because the original device is unavailable
        # or compromised.  Force every other live session for this
        # user to re-authenticate so an attacker who has the same
        # password+device can no longer ride along.  F-003.
        #
        # Called after login_user / _session_created_at so the helper
        # refreshes the cookie (with its single ``now``) AFTER
        # Flask-Login has populated the session, ensuring the
        # current request survives the bump.
        # It also forgets every trust of the user (the bump outdates them),
        # so it never trusts this browser, which stays known all the same
        # (ruling plan#2997): it has just finished a sign-in.
        invalidate_other_sessions(user, "backup_code_consumed")
        _flash_backup_codes_left(mfa_config)
    elif trust:
        log_event(logger, logging.INFO, EVT_DEVICE_TRUSTED, AUTH,
                  "Device trusted", user_id=user.id)

    log_event(logger, logging.INFO, EVT_MFA_LOGIN_SUCCESS, AUTH,
              "MFA login succeeded", user_id=user.id)
    return response


@auth_bp.route("/mfa/setup", methods=["GET"])
def mfa_setup():
    """Display the MFA setup page with QR code and manual key.

    Generates a fresh TOTP secret, encrypts it under the application's
    Fernet/MultiFernet key, and persists the ciphertext on the user's
    ``MfaConfig`` row in ``pending_secret_encrypted`` (paired with a
    15-minute expiry in ``pending_secret_expires_at``) instead of in
    the user's signed-but-unencrypted Flask session cookie.  See audit
    finding F-031 / commit C-05.

    Each visit overwrites any previous pending secret -- legitimate
    re-visits after invalid-code retries get a new QR code, and a
    stale pending row from an abandoned setup is replaced rather than
    accumulated.

    If MFA is already enabled the user is redirected back to security
    settings.  If the encryption key is missing the user is redirected
    with a flash explaining the operator-side prerequisite.

    **A GET that WRITES, and it says so** (plan step balance:X-i3).  Every
    other request method declares itself; this route is the one place where a
    safe method genuinely records something, because the pending secret must
    be persisted before the QR that encodes it can be shown.  It therefore
    holds its write inside :func:`app.db_transaction.write_transaction` rather
    than being exempted from the rule -- the block is a command, the rest of
    the request is a query, and the row it stages cannot be created outside
    the block because the surrounding transaction is read-only.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if mfa_config and mfa_config.is_enabled:
        flash("Two-factor authentication is already enabled.", "info")
        return redirect(url_for("auth.security"))

    secret = mfa_service.generate_totp_secret()
    # Encrypt before any DB mutation so a missing TOTP_ENCRYPTION_KEY
    # leaves the database state untouched -- no orphan pending row, no
    # half-initialized MfaConfig.  encrypt_secret() raises RuntimeError
    # when the key is unset (see app/services/mfa_service.py:_build_fernet_list).
    try:
        encrypted_pending = mfa_service.encrypt_secret(secret)
    except RuntimeError:
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("auth.security"))

    # The row is CONSTRUCTED inside the block, not before it: opening the
    # command rolls the query's snapshot back, and a pending instance staged
    # ahead of that would be expunged with it.
    with write_transaction():
        if mfa_config is None:
            mfa_config = MfaConfig(user_id=current_user.id)
            db.session.add(mfa_config)

        mfa_config.pending_secret_encrypted = encrypted_pending
        mfa_config.pending_secret_expires_at = (
            datetime.now(timezone.utc) + MFA_SETUP_PENDING_TTL
        )

    qr_data_uri = mfa_service.generate_qr_code_data_uri(
        mfa_service.get_totp_uri(secret, current_user.email)
    )
    return render_template(
        "auth/mfa_setup.html",
        qr_data_uri=qr_data_uri,
        manual_key=secret,
    )


@auth_bp.route("/mfa/confirm", methods=["POST"])
def mfa_confirm():
    """Verify a TOTP code and enable MFA for the current user.

    Reads the encrypted pending secret persisted by ``/mfa/setup``
    (commit C-05 moved this off the Flask session cookie), checks that
    it has not expired, decrypts it, and verifies the submitted code.
    On success the pending secret is re-encrypted under the current
    primary key and promoted to ``totp_secret_encrypted``; the pending
    columns are cleared in the same commit so a replay of the
    confirmation form cannot re-enrol a now-stale device.

    Failure modes handled here:

      * No ``MfaConfig`` row, or one without a pending secret -- the
        user has not started setup or the previous setup has been
        consumed/cleared.  Redirect to /mfa/setup with a flash.
      * Pending expiry has elapsed -- reject and clear the pending
        columns so the next /mfa/setup call starts cleanly.
      * Encryption key is missing or the pending ciphertext does not
        decrypt under any configured key -- redirect with a flash that
        names the operator-side fix.
      * Invalid TOTP code -- flash and redirect; pending state is
        retained until expiry so a typo does not force a re-scan.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    # Single guard for "no usable pending secret".  Covers three cases
    # that share the same user-facing recovery (start over): no row, a
    # row with no pending columns set, or a row whose expiry has
    # elapsed.  When the row had stale data we clear it in the same
    # commit so a request that races a fresh /mfa/setup GET does not
    # see the same expired ciphertext twice.
    has_pending = (
        mfa_config is not None
        and mfa_config.pending_secret_encrypted is not None
        and mfa_config.pending_secret_expires_at is not None
    )
    if not has_pending or (
            mfa_config.pending_secret_expires_at
            < datetime.now(timezone.utc)
    ):
        if has_pending:
            mfa_config.pending_secret_encrypted = None
            mfa_config.pending_secret_expires_at = None
            db.session.commit()
        flash("MFA setup session expired. Please start again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    try:
        secret = mfa_service.decrypt_secret(mfa_config.pending_secret_encrypted)
    except RuntimeError:
        # TOTP_ENCRYPTION_KEY is unset.  Sending the user back to
        # /mfa/setup would only loop them through the same failure
        # (encrypt_secret would raise RuntimeError too), so clear the
        # pending state and bounce to the security settings page where
        # the user can see the status and wait for the operator-side
        # fix.  Mirrors the recovery path used in /mfa/disable.
        mfa_config.pending_secret_encrypted = None
        mfa_config.pending_secret_expires_at = None
        db.session.commit()
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("auth.security"))
    except InvalidToken:
        # The pending ciphertext is unreadable under the current Fernet
        # key list -- typically because the key it was written under has
        # been pruned from TOTP_ENCRYPTION_KEY_OLD between /mfa/setup
        # and /mfa/confirm.  /mfa/setup itself still works (the primary
        # key is present), so direct the user there to start over.
        mfa_config.pending_secret_encrypted = None
        mfa_config.pending_secret_expires_at = None
        db.session.commit()
        flash(
            "MFA setup could not be verified. Please start again.",
            "danger",
        )
        return redirect(url_for("auth.mfa_setup"))

    # Schema-level validation caps the totp_code length to prevent
    # an oversized payload reaching ``verify_totp_setup_code`` (which
    # would still reject it, but at the cost of pyotp processing).
    # See commit C-26.
    # Both "the submitted code is unusable" outcomes -- a schema rejection
    # (oversized/malformed payload, per the C-26 cap above) and a code that
    # matches no step -- converge on one flash+redirect.  matched_step stays
    # None on a schema failure (the except leaves it untouched), so the single
    # guard below covers both.  Pending state is preserved either way, so the
    # user may retry until the 15-minute expiry without re-scanning the QR code.
    matched_step = None
    try:
        confirm_data = MfaConfirmSchema().load(request.form)
        matched_step = mfa_service.verify_totp_setup_code(
            secret, confirm_data["totp_code"],
        )
    except MarshmallowValidationError:
        pass
    if matched_step is None:
        flash("Invalid code. Please try again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    # Re-encrypt under the current primary key rather than copying the
    # bytes verbatim from the pending column.  If TOTP_ENCRYPTION_KEY
    # rotated during the setup window the pending ciphertext could
    # decrypt under a retired key only; promoting that ciphertext as-is
    # would leave the active credential dependent on a key the operator
    # is about to remove from TOTP_ENCRYPTION_KEY_OLD.  Re-encrypt
    # binds the active record to the current primary every time.
    try:
        mfa_config.totp_secret_encrypted = mfa_service.encrypt_secret(secret)
    except RuntimeError:
        # decrypt_secret() succeeded above so MultiFernet was usable a
        # moment ago.  This branch is only hit if TOTP_ENCRYPTION_KEY
        # was unset between the two calls -- defensive, not expected.
        flash(
            "MFA is not available. The server administrator must set "
            "TOTP_ENCRYPTION_KEY before MFA can be enabled.",
            "danger",
        )
        return redirect(url_for("auth.security"))

    # Single ``now`` flows into both ``confirmed_at`` and the
    # security-event timestamp so an analyst correlating the audit
    # log row against the banner trigger sees identical microseconds
    # rather than two near-identical clock samples.  Audit F-091 /
    # C-16.
    enrolled_at = datetime.now(timezone.utc)
    mfa_config.pending_secret_encrypted = None
    mfa_config.pending_secret_expires_at = None
    mfa_config.is_enabled = True
    mfa_config.confirmed_at = enrolled_at
    record_security_event(
        current_user, SecurityEventKind.MFA_ENABLED, now=enrolled_at,
    )
    # Seed replay-prevention state from the confirming code's step so
    # the same code cannot be replayed at /mfa/verify in the seconds
    # immediately after enrolment.  Without this seed the first
    # ~30 seconds of an MFA-protected account would still be vulnerable
    # to the F-005 attack against an attacker who observed the confirm
    # POST.  See commit C-09.
    mfa_config.last_totp_timestep = matched_step

    codes = mfa_service.generate_backup_codes()
    mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)
    db.session.commit()

    log_event(logger, logging.INFO, EVT_MFA_ENABLED, AUTH,
              "MFA enabled", user_id=current_user.id)
    return render_template("auth/mfa_backup_codes.html", backup_codes=codes)


@auth_bp.route("/mfa/regenerate-backup-codes", methods=["POST"])
@fresh_login_required()
def regenerate_backup_codes():
    """Generate and display new backup codes, replacing the old ones.

    Takes a step-up (ruling plan#3010): new codes void the old ones, so a
    stolen session must not be able to mint them.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config or not mfa_config.is_enabled:
        flash("Two-factor authentication is not enabled.", "danger")
        return redirect(url_for("auth.security"))

    codes = mfa_service.generate_backup_codes()
    mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)
    # Stamp the security-event columns in the same transaction as
    # the backup-code rotation so a rollback rolls back both.  Audit
    # F-091 / C-16: the banner the user sees on next page load is
    # the legitimate "your backup codes were regenerated, was that
    # you?" alert.
    record_security_event(
        current_user, SecurityEventKind.BACKUP_CODES_REGENERATED,
    )
    db.session.commit()

    log_event(logger, logging.INFO, EVT_BACKUP_CODES_REGENERATED, AUTH,
              "Backup codes regenerated", user_id=current_user.id)
    return render_template("auth/mfa_backup_codes.html", backup_codes=codes)


def _refused_where_required(view):
    """Refuse a view that turns off two-factor to a user who must have it.

    Ruling plan#3434: every companion must use two-factor, and there is no
    switch, so ``/mfa/disable`` refuses her on GET and POST alike, before the
    view reads or counts any password or code.  The Security page hides the
    button and says it is required; this answers the URL typed or a stale
    link.  ``scripts/reset_mfa.py`` still clears it, and she meets setup at
    her next sign-in.

    Args:
        view: The view function to guard.

    Returns:
        The guarded view, which redirects such a user to the Security page,
        saying why, and runs *view* for anyone else.
    """
    @wraps(view)
    def guarded(*args, **kwargs):
        """Redirect a user who must have two-factor; else run the view."""
        if current_user.must_have_two_factor:
            flash(
                "Two-factor authentication is required for your account and "
                "cannot be turned off.",
                "warning",
            )
            return redirect(url_for("auth.security"))
        return view(*args, **kwargs)
    return guarded


@auth_bp.route("/mfa/disable", methods=["GET"])
@_refused_where_required
def mfa_disable():
    """Display the MFA disable confirmation page.

    Requires MFA to be currently enabled. Redirects to security
    settings if MFA is not enabled.  A user who must have two-factor never
    reaches it (:func:`_refused_where_required`).
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config or not mfa_config.is_enabled:
        flash("Two-factor authentication is not enabled.", "info")
        return redirect(url_for("auth.security"))

    return render_template("auth/mfa_disable.html")


@auth_bp.route("/mfa/disable", methods=["POST"])
@_refused_where_required
def mfa_disable_confirm():
    """Process MFA disable after verifying password and TOTP code.

    Clears every piece of stored MFA material -- the TOTP secret,
    backup codes, the recorded replay-prevention step, and any pending
    setup ciphertext -- and sets is_enabled to False, via the
    :func:`mfa_service.clear_mfa_material` rule shared with
    ``scripts/reset_mfa.py`` so the route and the emergency CLI cannot
    drift on the field set.  The TOTP step is verified through
    :func:`mfa_service.verify_totp_code` with replay prevention
    enforced -- an attacker who has captured the user's password and a
    recently-used TOTP code cannot use that code to disable MFA, the
    same defence that ``/mfa/verify`` provides at login.

    A wrong password or code counts toward the lock, and the lock refuses
    the right ones (ruling plan#3008), with sign-in's one rule for whose
    attempt it is (plan#2772, ruling plan#3034): a known browser counts on
    its own record and passes the account's lock, a stranger counts on the
    account and meets it.  Success does not clear the count: that is a
    finished sign-in's or re-confirm's (ruling plan#3007).

    A user who must have two-factor never reaches it
    (:func:`_refused_where_required`), so a wrong password or code there
    counts nothing and a right pair turns nothing off.
    """
    # Schema validates shape before bcrypt is invoked.  current_password
    # accepts any historical length (legacy short passwords still work
    # for verification); totp_code is capped at 6 characters so a
    # DoS-sized string cannot reach verify_totp_code.  See commit C-26.
    try:
        disable_data = MfaDisableSchema().load(request.form)
    except MarshmallowValidationError:
        flash("Invalid password.", "danger")
        return redirect(url_for("auth.mfa_disable"))

    current_password = disable_data["current_password"]
    totp_code = disable_data["totp_code"]
    device = _known_device(current_user)

    # Verify the user's current password first.
    try:
        auth_service.check_signed_in_password(
            current_user, current_password, "Invalid password.",
            device=device,
        )
    except AuthError as exc:
        log_event(
            logger, logging.WARNING, EVT_REAUTH_FAILED, AUTH,
            f"Disable two-factor failed: {exc}",
            user_id=current_user.id, ip=request.remote_addr,
        )
        flash(str(exc), "danger")
        return redirect(url_for("auth.mfa_disable"))

    # Load MFA config and verify TOTP code.
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not mfa_config or not mfa_config.is_enabled:
        flash("Two-factor authentication is not enabled.", "danger")
        return redirect(url_for("auth.security"))

    # Same verifier as /mfa/verify underneath: emits
    # ``totp_replay_rejected`` on REPLAY and commits
    # ``last_totp_timestep`` on ACCEPTED so a code captured at
    # /mfa/verify cannot be reused at /mfa/disable (and vice versa).
    # The shared step-up wrapper flashes the operator-side key-failure
    # message so it wins over the generic invalid-code flash.
    accepted, key_failure = _totp_accepted_or_key_failure(
        mfa_config, totp_code, current_user.id,
    )
    if key_failure:
        return redirect(url_for("auth.security"))

    if not accepted:
        auth_service.record_failed_attempt(current_user, device=device)
        log_event(
            logger, logging.WARNING, EVT_REAUTH_FAILED, AUTH,
            "Disable two-factor failed: bad code",
            user_id=current_user.id, ip=request.remote_addr,
        )
        flash("Invalid authentication code.", "danger")
        return redirect(url_for("auth.mfa_disable"))

    # Clear all MFA material + disable, via the rule shared with the
    # emergency reset CLI (see clear_mfa_material's docstring for the
    # per-field rationale, including the last_totp_timestep reset and
    # the pending-setup ciphertext).
    mfa_service.clear_mfa_material(mfa_config)
    # Stamp the security-event columns in the same transaction as
    # the MFA-clear so the banner the user sees on next page load
    # is anchored to the same moment the mfa_configs row was
    # updated.  Audit F-091 / C-16: an attacker who pivots into the
    # session and disables MFA cannot prevent the legitimate user
    # from seeing the alert -- the banner state lives on the
    # auth.users row, not in the attacker's session cookie.
    record_security_event(
        current_user, SecurityEventKind.MFA_DISABLED,
    )
    db.session.commit()

    # MFA disable is a security-relevant state change: a user who
    # disables MFA because they suspect a session is compromised has
    # done nothing about that session yet.  Force every other live
    # session to re-authenticate so the disable cannot be silently
    # exploited by an attacker who already holds a valid cookie.
    # Called AFTER the MFA-clear commit so a transient DB error in
    # the helper does not leave MFA-disabled rows tied to a
    # non-bumped session_invalidated_at.  F-032.
    invalidate_other_sessions(current_user, "mfa_disabled")

    log_event(logger, logging.INFO, EVT_MFA_DISABLED, AUTH,
              "MFA disabled", user_id=current_user.id)
    flash("Two-factor authentication has been disabled.", "success")
    return redirect(url_for("auth.security"))
