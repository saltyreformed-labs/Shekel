"""
Shekel Budget App -- Auth route package: session security.

The authenticated-session security surface: the Security page itself
(``/security``, every user's own, ruling plan#3435), password change,
bulk session invalidation, forgetting one trusted device, step-up
re-authentication (``/reauth``), and the security-event banner
acknowledgement.  Each route registers
against the shared ``auth_bp`` from :mod:`~app.routes.auth._bp`; the
TOTP replay-prevention helper, open-redirect guard, and form-error
flattener come from :mod:`~app.routes.auth._helpers` so ``/reauth``
enforces the same replay policy as the MFA routes.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from marshmallow import ValidationError as MarshmallowValidationError

from app.extensions import db
from app.models.known_device import TRUST_LIFETIME
from app.models.user import MfaConfig
from app.schemas.validation import ChangePasswordSchema, ReauthSchema
from app.services import auth_service, known_device_service
from app.exceptions import AuthError, NotFoundError, ValidationError
from app.routes.auth._bp import auth_bp
from app.routes.auth._helpers import (
    _first_validation_message,
    _is_safe_redirect,
    _known_device,
    _landing_page,
    _spend_a_try,
    _totp_accepted_or_key_failure,
)
from app.utils.auth_helpers import log_refused_lookup
from app.utils.browser_label import browser_label
from app.utils.device_cookie import device_token, set_device_cookie
from app.utils.log_events import (
    AUTH,
    EVT_PASSWORD_CHANGED,
    EVT_REAUTH_FAILED,
    EVT_REAUTH_SUCCESS,
    EVT_SESSIONS_INVALIDATED,
    EVT_TRUSTED_DEVICE_FORGOTTEN,
    log_event,
)
from app.utils.security_events import (
    SecurityEventKind,
    acknowledge_security_event,
    banner_visible_for,
    record_security_event,
)
from app.utils.session_helpers import (
    stamp_password_change,
    stamp_reauth_session,
    stamp_session_refresh,
)

logger = logging.getLogger(__name__)


def trusted_device_rows(user_id: int) -> list[dict]:
    """Return *user_id*'s live trusted devices as rows a template only displays.

    The ONE builder of a trusted-device row (plan#2173, ruling plan#2406):
    the browser's name read from its stored User-Agent, the two instants it
    is dated by (stored UTC; the template's ``local_datetime`` filter shows
    each as a display-timezone day), and whether it is the browser this page
    is rendered on, judged against the list already read rather than by a
    second lookup.

    Args:
        user_id: The ``auth.users.id`` whose devices to list.

    Returns:
        One dict per live device, most recently trusted first, with ``id``,
        ``browser``, ``trusted_at``, ``expires_at`` and ``is_this_browser``.
    """
    devices = known_device_service.list_trusted_devices(user_id)
    this_browser = known_device_service.device_named_by(
        devices, device_token(user_id),
    )
    return [
        {
            "id": device.id,
            "browser": browser_label(device.user_agent),
            "trusted_at": device.trusted_at,
            "expires_at": device.trust_expires_at,
            "is_this_browser": device is this_browser,
        }
        for device in devices
    ]


@auth_bp.route("/security", methods=["GET"])
def security():
    """Display the signed-in user's Security page (ruling plan#3435).

    Each user's own sign-in: password, two-factor, trusted devices and
    sessions.  No owner gate: every route the page posts to acts on the
    signed-in user alone, and a companion manages her own here, her password
    included (ruling plan#3437).  It lived in Settings, which is the
    household's and the owner's alone, until that ruling.

    Returns:
        The rendered page.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    return render_template(
        "auth/security.html",
        mfa_enabled=mfa_config.is_enabled if mfa_config else False,
        # How long a trusted device stays trusted, which the page states in
        # its sentences: the one value, never a second spelling of it.
        trust_days=TRUST_LIFETIME.days,
        trusted_devices=trusted_device_rows(current_user.id),
    )


@auth_bp.route("/change-password", methods=["POST"])
def change_password():
    """Process a password change request from the Security settings section."""
    # Schema-level validation enforces shape (current_password
    # presence, new_password length and byte cap, confirm match)
    # before bcrypt is invoked.  ``auth_service.change_password``
    # re-validates the new-password length so the service remains
    # correct outside the route boundary.  See commit C-26.
    try:
        change_data = ChangePasswordSchema().load(request.form)
    except MarshmallowValidationError as exc:
        flash(_first_validation_message(exc), "danger")
        return redirect(url_for("auth.security"))

    current_password = change_data["current_password"]
    new_password = change_data["new_password"]

    try:
        auth_service.change_password(
            current_user, current_password, new_password,
            device=_known_device(current_user),
        )
        db.session.commit()
        # Invalidate all other sessions after password change.  A
        # single ``now`` value flows into both the DB column and
        # every cookie key so the strict-less-than comparison in
        # ``load_user`` accepts this session and rejects every
        # parallel one.  ``stamp_password_change`` ALSO refreshes
        # ``_fresh_login_at``: the user just typed their current
        # password, so this counts as a step-up re-auth and the
        # five-minute fresh-login window restarts here.  It is not a
        # sign-in, so the session's thirty days run on from its own
        # (ruling plan#3069).
        #
        # The same ``now`` is also written to the security-event
        # columns (audit F-091 / C-16) so the "was this you?" banner
        # uses the identical timestamp the audit_log row carries --
        # an analyst correlating the two cannot see drift between
        # the password-change moment and the banner-trigger moment.
        # The stamp lives BEFORE the second commit so both writes
        # land in one transaction.
        now = datetime.now(timezone.utc)
        current_user.session_invalidated_at = now
        record_security_event(
            current_user, SecurityEventKind.PASSWORD_CHANGED, now=now,
        )
        db.session.commit()
        stamp_password_change(now)
        log_event(logger, logging.INFO, EVT_PASSWORD_CHANGED, AUTH,
                  "Password changed", user_id=current_user.id)
        flash("Password changed successfully.", "success")
    except AuthError as e:
        log_event(
            logger, logging.WARNING, EVT_REAUTH_FAILED, AUTH,
            f"Change password failed: {e}",
            user_id=current_user.id, ip=request.remote_addr,
        )
        flash(str(e), "danger")
    except ValidationError as e:
        flash(str(e), "danger")

    return redirect(url_for("auth.security"))


@auth_bp.route("/invalidate-sessions", methods=["POST"])
def invalidate_sessions():
    """Invalidate all sessions for the current user except the current one.

    Sets session_invalidated_at to now, which causes load_user() to
    reject any session created before this timestamp.  The current
    session is refreshed via :func:`stamp_session_refresh` so that
    ``_session_created_at`` advances past the new invalidation point
    (and survives the loader's strict-less-than check).
    ``_fresh_login_at`` is deliberately left untouched: the user
    pressed a "log everyone else out" button without re-typing a
    password, so the step-up re-auth window must NOT silently
    extend.
    """
    now = datetime.now(timezone.utc)
    current_user.session_invalidated_at = now
    db.session.commit()
    stamp_session_refresh(now)
    log_event(logger, logging.INFO, EVT_SESSIONS_INVALIDATED, AUTH,
              "All sessions invalidated", user_id=current_user.id)
    flash(
        "All other sessions have been logged out, and every trusted device "
        "forgotten.",
        "success",
    )
    return redirect(url_for("auth.security"))


@auth_bp.route("/trusted-devices/<int:device_id>/forget", methods=["POST"])
def forget_trusted_device(device_id):
    """Forget one of the signed-in user's trusted devices (ruling plan#2406).

    The next sign-in on that browser asks for the two-factor code again.  A
    device of another user and an id no device holds are one 404, the
    security response rule, and the refusal is logged the way every
    owner-scoped lookup's is (:func:`~app.utils.auth_helpers.log_refused_lookup`):
    the service looks the id up within the user's own devices, so it cannot
    tell the two apart, and neither can the response.

    No step-up re-auth, as ``invalidate_sessions`` asks none: forgetting a
    device takes a privilege away and grants none.  No owner gate either:
    a device is its own user's, whatever their role.
    """
    try:
        known_device_service.forget_trust(current_user.id, device_id)
    except NotFoundError:
        log_refused_lookup("KnownDevice", device_id)
        abort(404)
    db.session.commit()
    log_event(logger, logging.INFO, EVT_TRUSTED_DEVICE_FORGOTTEN, AUTH,
              "Trusted device forgotten", user_id=current_user.id,
              device_id=device_id)
    flash(
        "Device forgotten. Signing in on it will ask for your two-factor "
        "code again.",
        "success",
    )
    return redirect(url_for("auth.security"))


def _render_reauth_form(safe_next: str | None):
    """Render the re-confirm form, from its five render sites.

    Its Cancel link goes where a sign-in lands this user (``_landing_page``):
    the owner's dashboard, or a companion's own page, who reaches this form
    from "Regenerate Backup Codes" on her Security page and whom the
    dashboard answers 404 (plan#2407).

    Args:
        safe_next: The validated ``next`` the form posts back, or ``None``.

    Returns:
        The rendered ``/reauth`` page.
    """
    return render_template(
        "auth/reauth.html", next=safe_next,
        home=_landing_page(current_user, None),
    )


@auth_bp.route("/reauth", methods=["GET", "POST"])
def reauth():
    """Step-up re-authentication for high-value operations.

    Users land here when ``fresh_login_required`` (commit C-10 /
    F-045) determines that their last password verification is older
    than ``FRESH_LOGIN_MAX_AGE_MINUTES``.  Successful POST stamps
    ``_fresh_login_at`` to ``now`` via
    :func:`~app.utils.session_helpers.stamp_reauth_session` and
    redirects to the validated ``next`` URL.

    Verification mirrors the /login flow:

      * Password is verified through
        ``auth_service.check_signed_in_password`` -- the same bcrypt path
        used at primary login, refused while the lock refuses this browser,
        and a wrong one counts toward the lock.  Whose attempt it is follows
        sign-in's one rule (plan#2772, ruling plan#3034): a known browser
        passes the account's lock and counts on its own record, a stranger
        meets it and counts on the account.
      * If the user has MFA enabled, a TOTP code is required.  A wrong or
        replayed one counts toward the lock.  The
        same ``_verify_totp_with_replay_logging`` helper used by
        /mfa/verify and /mfa/disable enforces replay prevention --
        a code captured at primary login cannot be reused here, and
        vice versa, because the helper bumps and persists
        ``mfa_config.last_totp_timestep`` on every ACCEPTED match.
      * Backup codes are intentionally NOT accepted here.  They are
        single-use and reserved for the "I lost my authenticator"
        scenario at primary login.  Spending one on a step-up
        prompt would burn a recovery credential for the wrong
        purpose; the user can /logout and /login with a backup code
        if their device is unavailable.
      * Decryption errors (missing TOTP_ENCRYPTION_KEY, ciphertext
        unreadable under any current Fernet) flash an
        operator-actionable message and re-render the form, mirroring
        /mfa/verify and /mfa/disable.

    Each POST whose form the schema accepts spends a try
    (:func:`~app.routes.auth._helpers._spend_a_try`) before its password is
    checked, five in fifteen minutes per counter: a known browser's own, else
    the account's, which every other browser signed in to it shares (ruling
    plan#3071).  So someone holding a stolen session who spends the account's
    tries cannot keep the owner's browser from re-confirming, and from what
    sits behind it.  The try is spent before the lock is asked, so a stranger
    the lock refuses spends one; a form the schema rejects spends none.

    Success clears the count the guesses went to (ruling plan#3007): every
    check this account faces has passed.  A stranger's re-confirm clears the
    account's count and lock if the record still holds what the read of the
    lock found, so a stranger's guess judged during these checks stands, and
    gets no cookie: it has no record.  A known
    browser's renews its record
    (:func:`~app.services.known_device_service.record_re_confirm`): a new
    cookie and its own count cleared, so a stolen copy of the old cookie
    stops working here, as at a sign-in (ruling plan#3070).  The record's
    thirty days do not move, and nor does the session's: a re-confirm is not
    a sign-in (ruling plan#3069).  The strangers' count and lock are left
    alone.  The record is read again for the renewal, after the code's commit
    and under the owner's lock that the next transaction takes again: a record
    a copy of the cookie deleted in between gets nothing, and the account's
    count is not cleared in its place.

    The ``next`` parameter is validated through ``_is_safe_redirect``
    on every read (storage time and post-success redirect) so an
    attacker cannot stage an open-redirect via the step-up flow.
    ``fresh_login_required`` sends ``request.url``, which is absolute, so
    ``_is_safe_redirect`` refuses it and the user lands where a finished
    sign-in lands them, to re-issue their action, as does direct navigation
    to /reauth: the one landing rule (``_landing_page``), so an owner lands
    on the dashboard and a companion on her own page, never on the owner's
    dashboard, which answers her 404 (plan#2407).  An owner's relative
    ``next`` is honoured; a companion always lands on her own page, as at
    sign-in.
    """
    next_url = request.args.get("next")
    safe_next = next_url if _is_safe_redirect(next_url) else None

    if request.method == "GET":
        return _render_reauth_form(safe_next)

    # Schema validation rejects oversized/missing fields before bcrypt
    # is invoked.  Failures are reported as the same "Invalid password."
    # flash the wrong-password branch uses below so the response cannot
    # be used as a side-channel oracle.  See commit C-26.
    try:
        reauth_data = ReauthSchema().load(request.form)
    except MarshmallowValidationError:
        log_event(
            logger, logging.WARNING, EVT_REAUTH_FAILED, AUTH,
            "Step-up re-auth failed: schema validation",
            user_id=current_user.id, ip=request.remote_addr,
        )
        flash("Invalid password.", "danger")
        return _render_reauth_form(safe_next)

    device = _known_device(current_user)
    # A password is about to be checked: spend one of this browser's tries,
    # its own when it is known (ruling plan#3071).  Past the limit this
    # raises, and the 429 handler answers before anything is checked.
    _spend_a_try(current_user, device)
    try:
        # What the strangers' record held when the lock was read: a stranger's
        # success clears it only if it still holds that, since a stranger's
        # guess judged during these checks takes no owner lock (ruling
        # plan#3110).
        checked = auth_service.check_signed_in_password(
            current_user, reauth_data["password"], "Invalid password.",
            device=device,
        )
    except AuthError as exc:
        log_event(
            logger, logging.WARNING, EVT_REAUTH_FAILED, AUTH,
            f"Step-up re-auth failed: {exc}",
            user_id=current_user.id, ip=request.remote_addr,
        )
        flash(str(exc), "danger")
        return _render_reauth_form(safe_next)

    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id, is_enabled=True)
        .first()
    )
    if mfa_config:
        totp_code = reauth_data["totp_code"]
        accepted, key_failure = _totp_accepted_or_key_failure(
            mfa_config, totp_code, current_user.id,
        )
        if key_failure:
            return _render_reauth_form(safe_next)
        if not accepted:
            auth_service.record_failed_attempt(current_user, device=device)
            log_event(
                logger, logging.WARNING, EVT_REAUTH_FAILED, AUTH,
                "Step-up re-auth failed: bad TOTP",
                user_id=current_user.id, ip=request.remote_addr,
            )
            flash("Invalid authentication code.", "danger")
            return _render_reauth_form(safe_next)

    # Every check has passed.  ``device`` is asked only whether it was None:
    # with two-factor, the code's commit has expired it, and a copy of this
    # cookie may have deleted its row since, so it is never read again here.
    token = None
    if device is None:
        auth_service.clear_failed_attempts(checked)
    else:
        token = known_device_service.record_re_confirm(
            current_user.id, device_token(current_user.id),
        )
        db.session.commit()
    stamp_reauth_session(datetime.now(timezone.utc))
    log_event(
        logger, logging.INFO, EVT_REAUTH_SUCCESS, AUTH,
        "Step-up re-auth succeeded", user_id=current_user.id,
    )
    response = redirect(_landing_page(current_user, safe_next))
    if token is not None:
        set_device_cookie(response, current_user.id, token)
    return response


@auth_bp.route("/security-event/dismiss", methods=["POST"])
def dismiss_security_event():
    """Acknowledge the in-app security-event banner for ``current_user``.

    The banner is rendered by ``base.html`` whenever
    :func:`~app.utils.security_events.banner_visible_for` returns True
    for the authenticated user.  This handler clears the banner by
    stamping ``last_security_event_acknowledged_at`` to the current
    moment; the visibility check then returns False on the next page
    load (and on every subsequent load, on every device, until a new
    security event is recorded).

    The route is a POST (CSRF-protected via Flask-WTF's global
    ``csrf.init_app`` plus the ``csrf_token()`` field rendered in the
    banner partial) because it changes server state.  ``hx-post`` from
    the banner's dismiss button targets the same URL with the same CSRF
    token; an HTMX request gets a 204 No Content swap directive that
    removes the banner element from the DOM, while a non-HTMX request
    redirects back to the referring page (or, with no usable referer,
    where a sign-in lands the user) so users without JavaScript still
    see a sane response.

    Acknowledgement is intentionally idempotent.  A user who clicks
    dismiss twice (double-click, retry on a flaky network) writes a
    second timestamp on top of the first; the visibility comparison
    against ``last_security_event_at`` resolves the same way.

    Audit reference: F-091 (Low) / commit C-16 of the 2026-04-15
    security remediation plan.
    """
    # Defensive guard: if the user reaches this endpoint without a
    # currently visible banner, the dismiss is a no-op rather than an
    # error.  This handles the race where two browser tabs both POST
    # the dismiss after observing the banner; the second tab's POST
    # simply re-stamps acknowledged_at.
    if banner_visible_for(current_user):
        acknowledge_security_event(current_user)
        db.session.commit()

    # HTMX requests prefer an empty 204 swap; the banner partial uses
    # ``hx-target="closest .security-event-banner"`` and
    # ``hx-swap="outerHTML"`` so the empty body removes the element.
    if request.headers.get("HX-Request") == "true":
        return ("", 204)

    # Non-HTMX fallback: bounce to the referring page so the user
    # sees the banner gone on reload.  Two layers of validation guard
    # against an open-redirect via a tampered Referer:
    #
    #   1. Reject any non-empty scheme that is not ``http`` / ``https``.
    #      A ``javascript:`` Referer parses to scheme=``javascript``,
    #      path=``alert(1)`` -- using ``parsed.path`` directly would
    #      hand the JS source string to ``redirect()`` and trip the
    #      same open-redirect surface the safe-redirect helper exists
    #      to close.
    #   2. Run the candidate path through ``_is_safe_redirect`` so a
    #      ``\\evil.com`` or whitespace-prefixed path is rejected.
    #
    # Either failure mode, and a request with no Referer, falls back to where
    # a sign-in lands this user (``_landing_page``): the owner's dashboard,
    # or a companion's own page, since the banner shows to her too and the
    # dashboard answers her 404 (plan#2407).
    home = _landing_page(current_user, None)
    referer = request.headers.get("Referer", "")
    parsed = urlparse(referer)
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        return redirect(home)
    next_target = parsed.path or home
    if parsed.query:
        next_target = f"{next_target}?{parsed.query}"
    if not _is_safe_redirect(next_target):
        next_target = home
    return redirect(next_target)
