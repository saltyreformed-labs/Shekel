"""
Shekel Budget App -- Auth route package: primary credentials.

The password-credential lifecycle: login (including the handoff into
the MFA pending state that :mod:`~app.routes.auth.mfa` consumes, and the
skip of that state on a trusted device), registration, and logout.  Each
route registers against the shared ``auth_bp`` from
:mod:`~app.routes.auth._bp`; the browser's standing, the sign-in
completion, the open-redirect guard and the form-error flattener come from
:mod:`~app.routes.auth._helpers`.
"""

import logging
from datetime import datetime, timezone
from typing import NamedTuple

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user, logout_user

from marshmallow import ValidationError as MarshmallowValidationError

from app import ref_cache
from app.db_transaction import bind_sign_in_owner
from app.enums import RoleEnum
from app.extensions import db, limiter
from app.models.user import MfaConfig, User
from app.schemas.validation import LoginSchema, RegisterSchema
from app.services import auth_service, registration_service
from app.services.guess_record_service import Held
from app.services.known_device_service import BrowserStanding
from app.exceptions import AuthError, ConflictError, ValidationError
from app.routes.auth._bp import auth_bp
from app.routes.auth._helpers import (
    _browser_standing,
    _complete_sign_in,
    _first_validation_message,
    _is_safe_redirect,
    _users_knowing_this_browser,
)
from app.utils.log_events import (
    AUTH,
    EVT_LOGIN_FAILED,
    EVT_LOGIN_SUCCESS,
    EVT_LOGOUT,
    EVT_TRUSTED_DEVICE_SIGN_IN,
    EVT_USER_REGISTERED,
    log_event,
)

logger = logging.getLogger(__name__)


def _render_register_form():
    """Render the registration form, from its four render sites.

    The GET, the already-authenticated GET, the schema-error re-render and
    the service-error re-render go through here so a fifth cannot drift.
    *It threaded ``today`` -- the USER's civil day, ruling R-DH (b) -- as a
    ``max`` on the most-recent-payday input until plan step
    ``pay_calendar:C17-c-2a``: the form asks for the SCHEDULED payday now
    (ledger row **PC-504**), which under "pay the business day before" can be
    tomorrow for a paycheck already paid today, so the cap refused a legal
    answer and went; ``registration_service._reject_impossible_first_payday``
    holds the real bound at both ends.*

    The cadence and horizon bounds are NOT passed here.  They are module
    constants, so they are Jinja globals registered once in ``create_app``
    (``jinja_globals.register_pay_calendar_bound_globals``) and read by this
    template and the settings generate form alike -- threading them through
    every renderer would be the plumbing that lets two forms drift.

    Returns:
        The rendered registration page.
    """
    return render_template("auth/register.html")


class _PasswordChecked(NamedTuple):
    """What ``/login``'s password check decided, for the rest of the sign-in.

    Attributes:
        user: The account the password signs in to.
        standing: This browser's standing for it, or ``None`` for a stranger.
        checked: For a stranger, the record of the email as its try's
            judgment left it, which a sign-in this password finishes clears
            if it still holds it; ``None`` for a known browser, which never
            reads the strangers' record.
    """

    user: User
    standing: BrowserStanding | None
    checked: Held | None


def _check_the_password(email: str, password: str) -> _PasswordChecked:
    """Check the password typed at ``/login``; return the account, the browser, and what to clear.

    Two flows, chosen by what this browser's cookie is worth for the account
    typed (plan#2772):

    * **A browser known to the account** checks its password under the
      account's owner lock, taken first (ruling **R-CC121**, "Lock, then
      check"): its wrong guesses count on its own record, and the lock keeps
      two of them from counting once.  Whether it is known is asked first,
      before the account is found, of every device cookie the browser sent,
      with no lock held and no record loaded
      (:func:`_users_knowing_this_browser`): so a made-up cookie never makes
      a stranger take the lock, and the email typed never changes that
      question's work (ruling plan#3130).  Under the lock the browser is read
      again (:func:`_browser_standing`), and a browser whose record its own
      guesses deleted between the two reads goes on as a stranger, with the
      lock it already took still held through its check: it held a live
      cookie for this account a moment ago, so its wait is no clue to
      whether the email has one.
    * **Every other try is a stranger's** and runs the same steps whatever the
      email and the answer, with no lock held (ruling plan#3110): the
      password check, the guess record's one statement and a commit
      (:func:`~app.services.auth_service.check_strangers_password`).  So a
      wrong password at a real account costs what one at an email no account
      has does, and no try waits on another try at the same email through its
      check.  Only a right password the lock let through takes the owner's
      lock, on the way to signing in, and under it the account is read again
      and its hash confirmed
      (:func:`~app.services.auth_service.confirm_strangers_password`).

    Args:
        email: The email typed, as the form normalised it.
        password: The password typed.

    Returns:
        The :class:`_PasswordChecked`.

    Raises:
        AuthError: The password does not sign in, for any of the reasons the
            two flows refuse it.
    """
    known_to = _users_knowing_this_browser()
    user = auth_service.find_sign_in_user(email)
    if user is not None and user.id in known_to:
        bind_sign_in_owner(user.id, user.data_owner_id)
        standing = _browser_standing(user)
        if standing is not None:
            return _PasswordChecked(
                auth_service.authenticate_known_browser(
                    user, password, device=standing.device,
                ),
                standing, None,
            )
    proof = auth_service.check_strangers_password(email, user, password)
    bind_sign_in_owner(proof.user_id, proof.owner_id)
    return _PasswordChecked(
        auth_service.confirm_strangers_password(user, proof), None, proof.held,
    )


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
    """Display the login form and handle authentication.

    Design note: each early return is a distinct semantic exit
    (companion-already-logged-in, owner-already-logged-in, trusted-device
    sign-in, MFA-pending redirect, no-MFA sign-in, generic failure
    render).  Consolidating these into one return path
    would force a state-machine in the function body that hides
    per-mode behaviour; the explicit returns are the readable form.
    """
    # Already logged in -- redirect to the appropriate landing page.
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))

    if request.method == "POST":
        # Schema-level validation runs before bcrypt is invoked so a
        # malformed or DoS-sized payload (F-163: megabyte-sized
        # password) is rejected at the route boundary.
        # ``MarshmallowValidationError`` and ``AuthError`` collapse to
        # the same generic "Invalid email or password." flash so an
        # attacker cannot distinguish schema-rejection from auth-
        # rejection by response wording.  See commit C-26.
        email = ""
        try:
            login_data = LoginSchema().load(request.form)
            email = login_data["email"]
            # The password, a known browser's under the owner's lock and a
            # stranger's with none held until it is right (ruling plan#3110).
            # What this browser's cookie is worth for the account is read
            # once, and the count and the code's skip below turn on it
            # (plan#2772).
            user, standing, checked = _check_the_password(
                email, login_data["password"],
            )

            # Check if MFA is enabled for this user.
            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=user.id, is_enabled=True)
                .first()
            )
            if mfa_config:
                # A trusted device skips only the code, never this
                # password (plan#2173, ruling plan#2406).
                if standing is not None and standing.trusted:
                    device_id = standing.device.id
                    response = _complete_sign_in(
                        user, request.args.get("next"), checked=None,
                    )
                    log_event(
                        logger, logging.INFO, EVT_TRUSTED_DEVICE_SIGN_IN, AUTH,
                        "User logged in on a trusted device",
                        user_id=user.id, device_id=device_id,
                    )
                    return response
                # Store pending auth state in session (user is NOT logged in yet).
                flask_session["_mfa_pending_user_id"] = user.id
                # Validate the next parameter at storage time (defense in depth).
                pending_next = request.args.get("next")
                flask_session["_mfa_pending_next"] = (
                    pending_next if _is_safe_redirect(pending_next) else None
                )
                # Stamp the pending state with a timestamp so /mfa/verify
                # can reject cookies older than _MFA_PENDING_MAX_AGE.
                # Without this, a captured session cookie remains a valid
                # MFA pending state for the entire 31-day default Flask
                # session lifetime -- audit finding F-002.
                flask_session["_mfa_pending_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                return redirect(url_for("auth.mfa_verify"))

            # No MFA -- complete login immediately, through the one
            # completion every sign-in shares (it stamps the lifecycle
            # keys and validates ``next``).  A stranger's clear compares
            # against what its try's judgment left; a known browser's clears
            # nothing.  With a code, the code form reads the lock again and
            # clears against that instead.
            response = _complete_sign_in(
                user, request.args.get("next"), checked=checked,
            )
            log_event(logger, logging.INFO, EVT_LOGIN_SUCCESS, AUTH,
                      "User logged in", user_id=user.id, email=email)
            return response

        except MarshmallowValidationError:
            # F-163 / F-041: schema layer rejected payload shape or
            # length; treated as a failed login attempt for forensic
            # logging purposes (rate-limited like any other failure).
            log_event(
                logger, logging.WARNING, EVT_LOGIN_FAILED, AUTH,
                "Login failed: schema validation",
                ip=request.remote_addr,
            )
            flash(auth_service.INVALID_SIGN_IN_MESSAGE, "danger")
        except AuthError:
            log_event(logger, logging.WARNING, EVT_LOGIN_FAILED, AUTH,
                      "Login failed", email=email, ip=request.remote_addr)
            flash(auth_service.INVALID_SIGN_IN_MESSAGE, "danger")

    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET"])
@limiter.limit("10 per hour")
def register_form():
    """Display the registration form."""
    # Registration toggle: return 404 (not 403) to avoid confirming
    # the endpoint exists when disabled.  See audit finding H6.
    if not current_app.config["REGISTRATION_ENABLED"]:
        abort(404)
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))
    return _render_register_form()


@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per hour")
def register():
    """Process a new user registration.

    Rate-limited to 3 per hour to prevent automated mass account
    creation.  See audit finding H5.
    """
    if not current_app.config["REGISTRATION_ENABLED"]:
        abort(404)
    if current_user.is_authenticated:
        companion_id = ref_cache.role_id(RoleEnum.COMPANION)
        if current_user.role_id == companion_id:
            return redirect(url_for("companion.index"))
        return redirect(url_for("dashboard.page"))

    # Schema-level validation enforces email shape, display-name
    # presence, password length (12-72 chars), bcrypt's 72-byte UTF-8
    # cap, and the password/confirm-match rule.  ``auth_service.
    # register_user`` re-validates email/length/byte rules so the
    # service remains correct even when called outside the route
    # layer (e.g. companion creation).  See commit C-26.
    try:
        register_data = RegisterSchema().load(request.form)
    except MarshmallowValidationError as exc:
        # Surface the first message for the first error field so the
        # existing user-facing error strings ("Invalid email format.",
        # "Display name is required.", "Password must be at least 12
        # characters.", "Password and confirmation do not match.")
        # remain stable.  ``_first_validation_message`` flattens
        # Marshmallow's nested dict to a single human-readable line.
        flash(_first_validation_message(exc), "danger")
        return _render_register_form()

    email = register_data["email"]

    try:
        # Capture the user so the structured ``user_registered`` event
        # can include the assigned user_id.  ``register_user`` already
        # returned the user (audit-finding F-085 / commit C-14
        # required no signature change here -- the route was simply
        # discarding the value).
        user = registration_service.register_user(registration_service.RegistrationSpec(
            email=email,
            password=register_data["password"],
            display_name=register_data["display_name"],
            first_payday=register_data["last_payday"],
            # Built by the schema from the kind control and its arm's
            # inputs (plan step pay_calendar:C17-d-3, ruling R-PC84).
            rhythm=register_data["rhythm"],
            num_periods=register_data["num_periods"],
            history_opens_on=register_data["history_opens_on"],
        ))
        db.session.commit()
        log_event(
            logger, logging.INFO, EVT_USER_REGISTERED, AUTH,
            "User registered",
            user_id=user.id, email=email,
        )
        flash("Account created. Please sign in.", "success")
        return redirect(url_for("auth.login"))
    except (ConflictError, ValidationError) as e:
        # ROLLED BACK before the re-render, which the four pay-period doors
        # already do and this one did not until plan step balance:X-bh-2.
        # ``register_user`` states that every refusal happens before the
        # ``User`` row is added, and that property held by ARGUMENT once this
        # step put ``set_history_opening`` -- a raising producer -- after the
        # row: its three refusal arms are each unreachable there, but two of
        # those arms rest on premises stated nowhere (that PERIOD_BATCH_MIN is
        # at least 1, so a schedule row exists; that a new owner's first
        # recorded payday is the one the form stated).  The rollback makes the
        # property hold structurally instead, and the re-render below then
        # reads committed state rather than a session holding a half-built
        # owner.
        db.session.rollback()
        flash(str(e), "danger")
        return _render_register_form()


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """End the user's session and redirect to login."""
    log_event(logger, logging.INFO, EVT_LOGOUT, AUTH,
              "User logged out", user_id=current_user.id)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
