"""
Shekel Budget App -- Auth route package: MFA lifecycle.

The TOTP multi-factor family, deliberately co-located: login-time
verification (``/mfa/verify``, consuming the pending state the login
route in :mod:`~app.routes.auth.credentials` establishes), enrolment
(``/mfa/setup`` + ``/mfa/confirm``) and backup-code regeneration.  There
is no disablement: no account can turn two-factor off (ruling plan#3604),
and ``scripts/reset_mfa.py`` is the recovery for a lost authenticator, a
companion's also the owner's Reset on Settings > Companions.  Each route
registers against
the shared ``auth_bp`` from :mod:`~app.routes.auth._bp`; the pending-
state freshness/cleanup helpers, the replay-logging TOTP verifier, the
backup-code consumer, and the setup TTL constant come from
:mod:`~app.routes.auth._helpers` so this module and the step-up
``/reauth`` flow enforce one replay-prevention policy.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import NamedTuple

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
from app.models.user import MfaConfig, User
from app.schemas.validation import (
    MfaConfirmSchema,
    MfaVerifySchema,
)
from app.services import auth_service, mfa_service, sign_in_service
from app.rate_limits import limiter
from app.routes.auth._bp import auth_bp
from app.routes.auth._helpers import (
    MFA_SETUP_PENDING_TTL,
    _MFA_PENDING_NEXT_KEY,
    _MFA_PENDING_USER_ID_KEY,
    _MFA_SETUP_FINGERPRINT_KEY,
    _clear_mfa_pending_state,
    _complete_sign_in,
    _consume_backup_code,
    _known_device,
    _mfa_pending_is_fresh,
    _spend_a_try,
    _verify_totp_with_replay_logging,
)
from app.utils.auth_helpers import fresh_login_required
from app.utils.log_events import (
    AUTH,
    EVT_BACKUP_CODES_REGENERATED,
    EVT_DEVICE_TRUSTED,
    EVT_MFA_ENABLED,
    EVT_LOGIN_FAILED,
    EVT_MFA_LOGIN_SUCCESS,
    EVT_OTHER_SESSIONS_INVALIDATED,
    log_event,
)
from app.utils.security_events import (
    SecurityEventKind,
    record_security_event,
)
from app.utils.sign_in_lifetimes import TRUST_LIFETIME

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
    :data:`~app.utils.sign_in_lifetimes.TRUST_LIFETIME` rather than a second
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
        # Either the user was deleted between login steps or their
        # two-factor was reset out from under the pending session
        # (scripts/reset_mfa.py, or the owner's Reset of a companion).
        # Both are race conditions that resolve the same way: clear
        # pending state and silently bounce to /login.  No flash because
        # a fresh password entry will explain itself (invalid credentials
        # if the user is gone, the hold at two-factor setup if it was
        # reset).
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

    trust = verify_data["trust_device"] and not used_backup_code
    if used_backup_code:
        # Backup-code consumption is the canonical "I lost my
        # authenticator" signal -- the credential the user just spent
        # exists precisely because the original device is unavailable
        # or compromised.  Every other session of this user ends, so an
        # attacker who has the same password+device can no longer ride
        # along (F-003), and every trust of theirs ends with them, by the
        # one act (ruling plan#3927).  It runs BEFORE the completion, so
        # the sign-in this code finishes is recorded after it and never
        # among the ones it ends.  So it never trusts this browser, which
        # stays known all the same (ruling plan#2997): it is about to
        # finish a sign-in.
        sign_in_service.sign_out_everywhere(
            user, keeping=None, now=datetime.now(timezone.utc),
        )
    # Login completion -- both password and TOTP/backup code are verified.
    # The pending ``next`` is read here, before the completion clears the
    # pending state, and validated again there (defense in depth -- it was
    # also validated at storage time in the login route).  The completion
    # records the sign-in and stamps every lifecycle key from one instant.
    response = _complete_sign_in(
        user, flask_session.get(_MFA_PENDING_NEXT_KEY),
        checked=reading.held, trust=trust,
    )

    if used_backup_code:
        log_event(
            logger, logging.INFO, EVT_OTHER_SESSIONS_INVALIDATED, AUTH,
            "Other sessions invalidated",
            user_id=user.id, reason="backup_code_consumed",
        )
        _flash_backup_codes_left(mfa_config)
    elif trust:
        log_event(logger, logging.INFO, EVT_DEVICE_TRUSTED, AUTH,
                  "Device trusted", user_id=user.id)

    log_event(logger, logging.INFO, EVT_MFA_LOGIN_SUCCESS, AUTH,
              "MFA login succeeded", user_id=user.id)
    return response


def _live_pending_ciphertext(mfa_config: MfaConfig | None) -> bytes | None:
    """Return the pending setup's ciphertext while its window is open, or ``None``.

    The ONE statement of when a setup is pending, which ``/mfa/setup`` and
    ``/mfa/confirm`` both read: the row holds a pending secret and its expiry
    has not passed.  The expiry is ``MFA_SETUP_PENDING_TTL`` from the latest
    load of setup in the sign-in that made the key (ruling plan#3523, which
    replaced ruling plan#3470's window counted from when the key was made),
    and the window is open through its last instant: it closes once ``now``
    is past it.

    Which browser may be SHOWN the key is not asked here but in
    :func:`_readable_pending_secret`.  ``/mfa/confirm`` asks only whether a
    key is pending, and takes a right code for it from any signed-in browser
    of the account: a code proves its typist holds the key, and only the
    sign-in that made the key is shown it.

    Args:
        mfa_config: The signed-in user's two-factor row, or ``None``.

    Returns:
        The pending ``pending_secret_encrypted``, or ``None`` for no row, no
        pending secret, or one whose window has closed.
    """
    if (
        mfa_config is None
        or mfa_config.pending_secret_encrypted is None
        or mfa_config.pending_secret_expires_at is None
        or mfa_config.pending_secret_expires_at < datetime.now(timezone.utc)
    ):
        return None
    return mfa_config.pending_secret_encrypted


def _setup_fingerprint(ciphertext: bytes) -> str:
    """Return the fingerprint by which a session names the setup key it made.

    The SHA-256 hex digest of the pending key's ciphertext (ruling
    plan#3523).  It tells the browser that made a key from every other
    browser of the account without the session cookie, which is signed but
    not encrypted, holding the key or its ciphertext (audit finding F-031 /
    commit C-05): the digest is one-way, so it gives back neither.  Fernet
    puts a fresh random IV in every ciphertext, so a fingerprint names one
    key and never a later one.

    Args:
        ciphertext: A pending ``pending_secret_encrypted``.

    Returns:
        Its 64-character lowercase hex SHA-256 digest.
    """
    return hashlib.sha256(ciphertext).hexdigest()


def _readable_pending_secret(mfa_config: MfaConfig | None) -> str | None:
    """Return the pending setup's secret for the sign-in that made it, or ``None``.

    The key is shown again only where it was made (ruling plan#3523): while
    a setup is pending (:func:`_live_pending_ciphertext`) AND this browser's
    session holds the fingerprint ``/mfa/setup`` stored with the key
    (:func:`_setup_fingerprint`).  So a second browser of the account, which
    someone else holding her password may be using, never sees the key she
    scans.  Every finished sign-in clears the fingerprint
    (:func:`~app.routes.auth._helpers._complete_sign_in`), so neither does
    whoever signs in next on her browser.  The fingerprint is compared
    plainly, not in constant time: the session is signed, so its holder
    cannot choose the value compared, and a timing difference has nothing to
    tell them.

    ``None`` when no setup is pending; when the session holds no fingerprint
    or another key's (another browser made the pending key, or replaced this
    one's since); and when the ciphertext does not decrypt: an
    ``InvalidToken`` (the key it was written under was pruned since) or a
    ``RuntimeError`` (no key is set).  Every ``None`` makes ``/mfa/setup``
    make a new key, which replaces the pending one.  With no key set that
    attempt's own encrypt raises too, and the setup page then says in place
    that two-factor is unavailable until the key is set, so no failure is
    hidden here.

    Args:
        mfa_config: The signed-in user's two-factor row, or ``None``.

    Returns:
        The plaintext base32 secret of the pending setup, or ``None``.
    """
    ciphertext = _live_pending_ciphertext(mfa_config)
    if ciphertext is None:
        return None
    if flask_session.get(_MFA_SETUP_FINGERPRINT_KEY) != _setup_fingerprint(ciphertext):
        return None
    try:
        return mfa_service.decrypt_secret(ciphertext)
    except (RuntimeError, InvalidToken):
        return None


class _SetupKey(NamedTuple):
    """The key a load of ``/mfa/setup`` shows, as :func:`_key_to_show` picks it."""

    #: The plaintext base32 secret the page shows.
    secret: str
    #: Its new ciphertext when this load made the key, for the row and the
    #: session's fingerprint; ``None`` when it is the pending key shown again.
    new_ciphertext: bytes | None


def _key_to_show(mfa_config: MfaConfig | None) -> _SetupKey | None:
    """Return the key this load of setup shows: the pending one again, or a new one.

    The pending key when this browser's sign-in may be shown it
    (:func:`_readable_pending_secret`); else a new TOTP secret and its
    ciphertext, which the caller writes over any pending one.  It encrypts
    before the caller mutates anything, so a missing ``TOTP_ENCRYPTION_KEY``
    leaves the database untouched: no orphan pending row, no half-initialized
    ``MfaConfig``.  :func:`mfa_service.encrypt_secret` raises
    ``RuntimeError`` when the key is unset (see
    ``app/services/mfa_service.py:_build_fernet_list``).

    Args:
        mfa_config: The signed-in user's two-factor row, read inside the
            setup's command, or ``None``.

    Returns:
        The key to show, or ``None`` when no key can be made because
        ``TOTP_ENCRYPTION_KEY`` is unset.
    """
    secret = _readable_pending_secret(mfa_config)
    if secret is not None:
        return _SetupKey(secret, None)
    secret = mfa_service.generate_totp_secret()
    try:
        return _SetupKey(secret, mfa_service.encrypt_secret(secret))
    except RuntimeError:
        return None


@auth_bp.route("/mfa/setup", methods=["GET"])
def mfa_setup():
    """Display the MFA setup page: its app link, QR code and manual key.

    Three ways to put one key in the authenticator app: on the phone itself,
    the "Open in my authenticator app" link opens the app with the key filled
    in (ruling plan#3442); from a computer, the QR code; and by hand, the
    manual key.  The link and the QR code carry the one ``otpauth://`` URI.

    **The pending key is kept only for the browser that made it, and each
    load there gives it 15 fresh minutes** (ruling plan#3523, which replaced
    ruling plan#3470's window counted from when the key was made).  In that
    browser every load while the key is pending shows the same QR code and
    manual key, and moves the expiry to ``MFA_SETUP_PENDING_TTL`` from now.
    So a wrong code at ``/mfa/confirm`` (which sends the user back here), a
    refresh, the login gate holding a user here, or the phone reloading
    the tab on the way back from the authenticator app all show the key
    already scanned, and the entry made from it keeps working, however late
    in its window it was reached.  Until ruling plan#3470 each load made a
    new key, and each of those reloads killed the entry just scanned.

    Every other load makes a new key, replacing any pending one: none is
    pending, the pending one's window has closed, its ciphertext no longer
    decrypts, or this browser's session does not hold its fingerprint
    (:func:`_readable_pending_secret`).  The last is a second browser of the
    account: someone who signed in with her password on a laptop and opened
    setup cannot see the key her phone's setup shows, because her phone's
    load replaced the laptop's key, and the laptop's next load replaces hers
    rather than showing it.  Under ruling plan#3470 the phone showed the
    laptop's key, and the laptop's user could make her codes from then on.
    A sign-in clears the fingerprint, so the key is kept for the sign-in that
    made it: a new sign-in in the same browser, hers or anyone's, gets a new
    key.

    A new key is a fresh TOTP secret, encrypted under the application's
    Fernet/MultiFernet key and persisted on the user's ``MfaConfig`` row in
    ``pending_secret_encrypted`` (paired with its expiry in
    ``pending_secret_expires_at``) instead of in the user's
    signed-but-unencrypted Flask session cookie.  See audit finding F-031 /
    commit C-05.  The session holds only the key's fingerprint
    (:func:`_setup_fingerprint`), stored after the commit that writes the
    key, so a write that fails stores none.

    If MFA is already enabled the user is redirected back to security
    settings.  **If the encryption key is missing the page says so in place**
    (plan#3514): a 200 naming the operator-side prerequisite, with no key, QR
    code or form, and nothing written.  It once redirected to the Security
    page with a flash, which the login gate refuses a user it holds here, so
    they were sent straight back, round and round, the flash never shown.
    ``/mfa/confirm`` sends its own key failures here for the same reason.

    **A GET that WRITES, and it says so** (plan step balance:X-i3).  Every
    other request method declares itself; this route is the one place where a
    safe method genuinely records something, because a new pending secret
    must be persisted before the QR that encodes it can be shown.  It
    therefore holds its write inside :func:`app.db_transaction.write_transaction`
    rather than being exempted from the rule -- the block is a command, the
    rest of the request is a query, and the row it stages cannot be created
    outside the block because the surrounding transaction is read-only.
    Every load that shows a key writes: a new key and its expiry, or the
    shown key's renewed expiry.
    """
    # The whole read-decide-write is ONE command, the row read inside it
    # (app/db_transaction.py: a write's deciding reads sit inside its block,
    # which holds the owner's lock).  /mfa/confirm and the owner's reset on
    # Companions take that lock at their own request's start, so neither
    # commits between this read and this write.  Read before the block, a load
    # racing her confirm could put a new pending key on her just-enabled row,
    # which a later confirm would promote over her secret, and a renewal
    # racing a confirm or a reset wrote an expiry onto a row whose key they
    # had cleared.  The row is also CONSTRUCTED inside the block: opening the
    # command rolls the query's snapshot back, and a pending instance staged
    # ahead of that would be expunged with it.  The page renders after it.
    with write_transaction():
        mfa_config = (
            db.session.query(MfaConfig)
            .filter_by(user_id=current_user.id)
            .first()
        )
        already_enabled = mfa_config is not None and mfa_config.is_enabled
        shown = None if already_enabled else _key_to_show(mfa_config)
        if shown is not None:
            # No row means no key was pending, so a new one was just made.
            if mfa_config is None:
                mfa_config = MfaConfig(user_id=current_user.id)
                db.session.add(mfa_config)
            if shown.new_ciphertext is not None:
                mfa_config.pending_secret_encrypted = shown.new_ciphertext
            # A fresh window on every load that shows a key, the one shown
            # again and a new one alike (ruling plan#3523).
            mfa_config.pending_secret_expires_at = (
                datetime.now(timezone.utc) + MFA_SETUP_PENDING_TTL
            )

    if already_enabled:
        flash("Two-factor authentication is already enabled.", "info")
        return redirect(url_for("auth.security"))
    if shown is None:
        # The page says so here, in place, never by a redirect: this is the
        # one page a user without two-factor is held at (plan#3514).
        return render_template("auth/mfa_setup.html", key_missing=True)
    if shown.new_ciphertext is not None:
        # After the commit, so a write that fails stores no fingerprint.
        flask_session[_MFA_SETUP_FINGERPRINT_KEY] = _setup_fingerprint(
            shown.new_ciphertext,
        )

    # The one provisioning URI, built once and handed to both ways in (ruling
    # plan#3442): the QR code a computer's page shows for a phone to scan, and
    # the "Open in my authenticator app" link a phone's page follows itself.
    totp_uri = mfa_service.get_totp_uri(shown.secret, current_user.email)
    return render_template(
        "auth/mfa_setup.html",
        key_missing=False,
        totp_uri=totp_uri,
        qr_data_uri=mfa_service.generate_qr_code_data_uri(totp_uri),
        manual_key=shown.secret,
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
      * Encryption key is missing, at the decrypt or at the promotion's
        re-encrypt -- redirect to /mfa/setup, which says in place that the
        operator must set it (plan#3514), never to a page the login gate
        refuses a user it holds at setup.
      * The pending ciphertext does not decrypt under any configured key
        -- clear it and redirect to /mfa/setup with a flash to start again.
      * Invalid TOTP code -- flash and redirect; pending state is
        retained until expiry so a typo does not force a re-scan.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    # Single guard for "no usable pending secret", the one statement of a
    # pending setup /mfa/setup reads too (_live_pending_ciphertext).  Covers
    # three cases that share the same user-facing recovery (start over): no
    # row, a row with no pending columns set, or a row whose expiry has
    # elapsed.  When the row had stale data we clear it in the same
    # commit so a request that races a fresh /mfa/setup GET does not
    # see the same expired ciphertext twice.
    ciphertext = _live_pending_ciphertext(mfa_config)
    if ciphertext is None:
        if (
            mfa_config is not None
            and mfa_config.pending_secret_encrypted is not None
        ):
            mfa_config.pending_secret_encrypted = None
            mfa_config.pending_secret_expires_at = None
            db.session.commit()
        flash("MFA setup session expired. Please start again.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    try:
        secret = mfa_service.decrypt_secret(ciphertext)
    except RuntimeError:
        # TOTP_ENCRYPTION_KEY is unset.  Clear the pending state it can no
        # longer read and send the user to /mfa/setup, whose own encrypt
        # fails the same way and which then says in place that the operator
        # must set the key (plan#3514).  No flash: setup's page is the one
        # place that says it, and a redirect anywhere else loops a user
        # the login gate holds at setup.
        mfa_config.pending_secret_encrypted = None
        mfa_config.pending_secret_expires_at = None
        db.session.commit()
        return redirect(url_for("auth.mfa_setup"))
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
        # was unset between the two calls -- defensive, not expected.  Sent
        # to /mfa/setup, which says so in place (plan#3514), as the decrypt's
        # key failure above is.  Nothing was written: the assignment above
        # raised before it took.
        return redirect(url_for("auth.mfa_setup"))

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

    The codes are written onto the two-factor row the login gate read, which
    is on: the gate holds an account without two-factor at setup, so this
    door asks nothing again (plan#3775, ruling plan#3826).  It once had a
    refusal of its own, "Two-factor authentication is not enabled.", which
    no request could reach: its query returned the row the gate had loaded,
    unrefreshed (measured 2026-11-13).
    """
    mfa_config = current_user.mfa_config
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
