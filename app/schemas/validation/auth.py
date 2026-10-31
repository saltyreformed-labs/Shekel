"""Auth-blueprint and companion-management validation schemas.

Shared email / password / TOTP constants and helpers plus every
login, registration, password-change, MFA, reauth, and companion
schema.  Self-contained: the auth-only constants and helpers live
here rather than in the shared ``_helpers`` module."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.config import BaseConfig
from app.schemas.validation._helpers import BaseSchema
from app.schemas.validation._pay_rhythm import DefaultedRhythmFields
from app.schemas.validation.pay_periods import (
    history_opens_on_field,
    num_periods_field,
    payday_field,
)


# --- Auth and companion user management ----------------------------------
#
# Email regex and password length rules are shared between the auth
# blueprint (login, register, change_password, MFA flows) and the
# companion-management routes so both code paths accept the same set
# of addresses and enforce the same bcrypt-bounded password rules.
# The 72-byte ceiling is bcrypt's hard input cap (see
# ``auth_service.hash_password``); inputs longer than that would be
# silently truncated and could not be reproduced at verify time
# without the same truncation.  The 12-character minimum matches
# ``registration_service.register_user`` and ``auth_service.change_password``.
#
# Commit C-26 of the 2026-04-15 security remediation plan promotes
# these constants out of the companion-only namespace so every auth
# schema can reuse them.  Companion-specific schemas continue to use
# the same constants.
#
# F-163 (Low) bounds backup_code at 32 characters so a megabyte-sized
# string cannot reach the bcrypt verifier on the /mfa/verify path; the
# real backup codes are 28 hex characters, so 32 is generous without
# admitting any DoS surface.  TOTP codes are exactly 6 digits.

_AUTH_EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_AUTH_EMAIL_MAX_LENGTH = 255
_AUTH_DISPLAY_NAME_MAX_LENGTH = 100
_AUTH_PASSWORD_MIN_LENGTH = 12
_AUTH_PASSWORD_MAX_BYTES = 72
_AUTH_PASSWORD_MAX_CHARS = 72

# F-163: TOTP codes are exactly six decimal digits but the verifier
# accepts whatever the user types (and reports "Invalid code") so the
# schema enforces a length cap rather than a strict shape.  The cap
# matches the on-screen input width and prevents a megabyte-sized
# string from reaching the verifier.
_TOTP_CODE_MAX_LENGTH = 6

# F-163: Backup codes are 28 hex characters (post-C-03 issue width);
# a 32-character cap accommodates that with a small margin and rejects
# DoS-sized inputs before bcrypt is invoked on the verify path.
_BACKUP_CODE_MAX_LENGTH = 32


def _normalize_auth_form(data):
    """Strip whitespace and lowercase email for any auth form payload.

    Works with both Werkzeug ImmutableMultiDict (from ``request.form``)
    and plain dicts.  Leaves password and code fields untouched because
    leading or trailing spaces in those values are handled at the route
    boundary (passwords compare byte-for-byte; code fields are stripped
    in the schema's per-field ``@pre_load``).  Missing keys are left
    missing so required-field validation produces the correct error.

    Used by every schema in the auth blueprint plus the companion-
    management schemas so the email-normalization rule is identical in
    both code paths.
    """
    cleaned = dict(data)
    if "email" in cleaned and isinstance(cleaned["email"], str):
        cleaned["email"] = cleaned["email"].strip().lower()
    if "display_name" in cleaned and isinstance(cleaned["display_name"], str):
        cleaned["display_name"] = cleaned["display_name"].strip()
    return cleaned


def _auth_email_field():
    """Construct the standard email field used by every auth schema.

    Centralises the field definition so a future change to the email
    rules (e.g. tightening the regex, adding a deny-list of throwaway
    domains) lands in one place.  Returns a fresh ``fields.String``
    each call because Marshmallow field instances carry per-schema
    metadata and cannot be safely shared across class declarations.

    Both validators surface the same "Invalid email format." message
    so the user-facing flash stays consistent between empty-string,
    over-length, and malformed-shape failures.  An attacker probing
    the registration form can therefore not distinguish "no such
    address pattern" from "address too long" from response wording.
    """
    return fields.String(
        required=True,
        validate=[
            validate.Length(
                min=1, max=_AUTH_EMAIL_MAX_LENGTH,
                error="Invalid email format.",
            ),
            validate.Regexp(_AUTH_EMAIL_REGEX, error="Invalid email format."),
        ],
    )


def _verify_password_bytes(password, *, field_name="password"):
    """Reject passwords longer than bcrypt's 72-byte UTF-8 limit.

    Used by every schema that accepts a *new* password (register,
    change-password, companion create/edit).  A separate, dedicated
    validator is kept for *login* / *current_password* / *reauth*
    paths -- those accept any historical password length up to the
    same byte cap so a legacy short password can still be entered for
    verification.

    Raises:
        ValidationError: If the UTF-8 encoding of ``password`` exceeds
            ``_AUTH_PASSWORD_MAX_BYTES``.
    """
    if password is None:
        return
    if len(password.encode("utf-8")) > _AUTH_PASSWORD_MAX_BYTES:
        raise ValidationError(
            "Password is too long. Please use 72 characters or fewer.",
            field_name,
        )


# --- Auth blueprint schemas (commit C-26) --------------------------------
#
# Every POST handler in the app/routes/auth/ package validates its form payload
# through one of the schemas below before invoking auth_service or
# mfa_service.  Schema-level validation is the only line of defence
# against megabyte-sized backup codes hitting bcrypt (F-163), and it
# keeps the route handlers free of inline ``request.form.get`` plumbing
# (F-041).
#
# Login/current-password/reauth paths accept any historical password
# length (min=1) up to the bcrypt 72-byte ceiling so a legacy short
# password registered before the 12-character rule was tightened can
# still be entered for verification.  Routes that mint a *new*
# password (register, change-password, companion create/edit) enforce
# the full 12-character minimum plus the 72-byte UTF-8 cap.


class _AuthFormSchema(BaseSchema):
    """Base for every auth-blueprint schema.

    Strips and lowercases the email field on load so each subclass
    inherits the canonical normalization without re-declaring the
    ``@pre_load``.  Subclasses add their own field validators and
    cross-field rules; the normalization is purely shape-level.
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip whitespace and lowercase the email before field validation."""
        return _normalize_auth_form(data)


class LoginSchema(_AuthFormSchema):
    """Validates POST data for /login.

    Accepts any historical password length up to the bcrypt 72-byte
    ceiling (min=1, max=72 characters) so users with passwords
    registered before the 12-character minimum was tightened can still
    log in for verification.  The 72-character cap doubles as F-163 DoS
    protection: bcrypt would silently truncate any longer input, so
    accepting it would let an attacker pay no cost while the server
    paid the bcrypt cost on a hash they can never reproduce.

    There is no "Remember me" field: plan#2173 deleted the checkbox and
    the cookie it asked for (ruling plan#2406), and ``BaseSchema``'s
    ``EXCLUDE`` drops a ``remember`` a stale cached form still posts.

    The ``next`` parameter is intentionally NOT in this schema -- it
    is a query-string argument validated through ``_is_safe_redirect``
    in the route, where the open-redirect rule lives.
    """

    email = _auth_email_field()
    password = fields.String(
        required=True,
        validate=validate.Length(min=1, max=_AUTH_PASSWORD_MAX_CHARS),
    )


class RegisterSchema(DefaultedRhythmFields, _AuthFormSchema):
    """Validates POST data for /register.

    Required fields: email, display_name, password, confirm_password,
    last_payday.  Enforces the same 12-character minimum / 72-byte UTF-8
    maximum as ``registration_service.register_user`` so the schema layer rejects bad
    input before the service is called.  Email uniqueness is enforced
    by the service (it needs a live DB session), so the schema only
    validates shape.

    **The three pay-calendar fields arrived at plan step X-ad-a** (ruling
    **R-DB**), which deleted the fabricated sign-up pay period finding
    **N-123** traces, and a FOURTH arrived at plan step **balance:X-bh-2**
    (ruling **balance:R-IA**): ``history_opens_on``, how far back the owner's
    paychecks reach.  They carry SHAPE only: the dates are dates and the
    integers are bounded, while whether the stated payday is one the owner
    could have been LAST paid on -- and whether the stated opening precedes it
    -- is ``auth_service``'s, because both need the user's clock or the other
    field, and a schema is not where this package reads a clock.  Every one of
    them takes the shared declaration the ``/pay-periods`` forms use, so
    the doors onto one calendar cannot bound their inputs differently.
    **The rhythm's KIND and its arms' inputs arrived at plan step
    ``pay_calendar:C17-d-3``** (ruling **R-PC84**), declared ONCE with the
    generate door's in
    :class:`~app.schemas.validation._pay_rhythm.DefaultedRhythmFields` and
    collapsed into ``rhythm`` by its ``@post_load``; its ``@pre_load`` is
    also what clears this form's untouched ``history_opens_on`` to ``None``.

    **The pay-calendar fields are declared AFTER the credential ones on
    purpose.**  Marshmallow accumulates errors in field-declaration order and
    ``_first_validation_message`` flashes the first one, so the credential
    messages a user is most likely to hit keep winning over a missing date.
    *This read "``last_payday`` is declared LAST" and had not been true since
    X-ad-a itself, which put two more fields below it; the rule it was stating
    is the one above, and plan step balance:X-bh-2 adds a fourth field under
    the same rule.*  **The rhythm's six fields are the exception, and a
    stated one**: they are ``DefaultedRhythmFields``', which marshmallow
    orders BEFORE this class's own, and that mixin's docstring records the
    one browser-producible error (exponent notation in a number box) that
    now flashes ahead of a credential one.
    """

    email = _auth_email_field()
    display_name = fields.String(
        required=True,
        validate=[
            validate.Length(min=1, error="Display name is required."),
            validate.Length(
                max=_AUTH_DISPLAY_NAME_MAX_LENGTH,
                error=(
                    "Display name must be at most "
                    f"{_AUTH_DISPLAY_NAME_MAX_LENGTH} characters."
                ),
            ),
        ],
    )
    password = fields.String(
        required=True,
        validate=[
            validate.Length(
                min=_AUTH_PASSWORD_MIN_LENGTH,
                error="Password must be at least 12 characters.",
            ),
            validate.Length(
                max=_AUTH_PASSWORD_MAX_CHARS,
                error="Password is too long. Please use 72 characters or fewer.",
            ),
        ],
    )
    confirm_password = fields.String(required=True)
    # The grid question is attributed to this box (RhythmFormMixin): a
    # monthly rhythm stated from a day it does not pay is the payday's
    # error, not the kind's.
    payday_control = "last_payday"
    last_payday = payday_field(
        required=True,
        error_messages={"required": "Enter the day you were last paid."},
    )
    num_periods = num_periods_field(
        load_default=BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
    )
    history_opens_on = history_opens_on_field(load_default=None)

    # The cleared-optional-input half of this form's ``@pre_load`` -- which
    # plan step balance:X-bh-2 put in an override of
    # ``_AuthFormSchema.normalize_inputs`` so the two halves ran in a stated
    # order -- is ``RhythmFormMixin.clear_blank_rhythm_inputs`` since plan
    # step pay_calendar:C17-d-3, because the rhythm's arms need the same
    # clearing on every door.  The credential half stays the base class's.
    # The two hooks commute (one strips and lowercases the credential
    # strings, the other maps "" to None on the NULLABLE fields only), so
    # marshmallow's unstated hook order is safe here; the override's warning
    # against ``_normalize_empty_inputs`` now lives on the mixin's hook.

    @validates_schema
    def validate_password_bytes(self, data, **kwargs):
        """Reject passwords longer than bcrypt's 72-byte UTF-8 limit."""
        _verify_password_bytes(data.get("password"))

    @validates_schema
    def validate_confirm_matches(self, data, **kwargs):
        """Require ``confirm_password`` to equal ``password``."""
        if data.get("password") != data.get("confirm_password"):
            raise ValidationError(
                "Password and confirmation do not match.",
                "confirm_password",
            )

class ChangePasswordSchema(BaseSchema):
    """Validates POST data for /change-password.

    ``current_password`` is verified against the stored hash by
    ``auth_service.change_password``; the schema only enforces shape
    (presence and bcrypt byte cap) so a legacy short password registered
    before the 12-character rule was tightened can still be supplied
    here for verification.

    ``new_password`` and ``confirm_password`` enforce the same
    12-character minimum / 72-byte maximum as ``auth_service``.  The
    cross-field validator runs only when both new and confirm are
    populated; if either is missing the per-field ``required=True``
    error wins.

    No email field on this form -- the user is already authenticated.
    """

    current_password = fields.String(
        required=True,
        validate=validate.Length(min=1, max=_AUTH_PASSWORD_MAX_CHARS),
    )
    new_password = fields.String(
        required=True,
        validate=[
            validate.Length(
                min=_AUTH_PASSWORD_MIN_LENGTH,
                error="New password must be at least 12 characters.",
            ),
            validate.Length(
                max=_AUTH_PASSWORD_MAX_CHARS,
                error="Password is too long. Please use 72 characters or fewer.",
            ),
        ],
    )
    confirm_password = fields.String(required=True)

    @validates_schema
    def validate_new_password_bytes(self, data, **kwargs):
        """Reject new passwords longer than bcrypt's 72-byte UTF-8 limit."""
        _verify_password_bytes(data.get("new_password"), field_name="new_password")

    @validates_schema
    def validate_confirm_matches(self, data, **kwargs):
        """Require ``confirm_password`` to equal ``new_password``."""
        if data.get("new_password") != data.get("confirm_password"):
            raise ValidationError(
                "New password and confirmation do not match.",
                "confirm_password",
            )


def _strip_code_field(data, key):
    """Strip whitespace from a code field if it is a string.

    Mirrors the ``.strip()`` calls the route layer used to do inline
    on ``totp_code`` and ``backup_code``; centralising the strip in the
    schema means every auth route reads already-normalised values.
    """
    if key in data and isinstance(data[key], str):
        data[key] = data[key].strip()
    return data


class MfaVerifySchema(BaseSchema):
    """Validates POST data for /mfa/verify.

    Both fields default to the empty string because the form has two
    one-of inputs (TOTP code or backup code) and the user submits only
    one.  ``totp_code`` is capped at 6 characters and ``backup_code``
    at 32 characters -- F-163 DoS protection that prevents a megabyte-
    sized backup code from reaching bcrypt verification.  The route
    layer handles the "neither field present" case (renders "Invalid
    verification code.").
    """

    totp_code = fields.String(
        load_default="",
        validate=validate.Length(max=_TOTP_CODE_MAX_LENGTH),
    )
    backup_code = fields.String(
        load_default="",
        validate=validate.Length(max=_BACKUP_CODE_MAX_LENGTH),
    )

    @pre_load
    def strip_codes(self, data, **kwargs):
        """Strip whitespace from totp_code and backup_code before validation.

        The schema's max-length checks would otherwise reject a code
        with leading/trailing whitespace; matching the route's prior
        ``.strip()`` calls keeps user-pasted codes accepted while still
        capping the post-strip length.
        """
        cleaned = dict(data)
        cleaned = _strip_code_field(cleaned, "totp_code")
        cleaned = _strip_code_field(cleaned, "backup_code")
        return cleaned


class MfaConfirmSchema(BaseSchema):
    """Validates POST data for /mfa/confirm.

    Used during MFA enrolment after /mfa/setup has stored the
    encrypted pending secret server-side.  Required because confirming
    enrolment without a code is meaningless; the 6-character cap
    matches the TOTP shape.
    """

    totp_code = fields.String(
        load_default="",
        validate=validate.Length(max=_TOTP_CODE_MAX_LENGTH),
    )

    @pre_load
    def strip_codes(self, data, **kwargs):
        """Strip whitespace from totp_code before length validation."""
        return _strip_code_field(dict(data), "totp_code")


class MfaDisableSchema(BaseSchema):
    """Validates POST data for /mfa/disable.

    Requires both the user's current password (verified by
    ``auth_service.verify_password``) and a current TOTP code (verified
    by ``mfa_service.verify_totp_code`` with replay protection).
    Backup codes are intentionally NOT accepted here -- the disable
    flow is the canonical "I can still log in normally" path; the
    "I lost my authenticator" path goes through /login + backup code
    instead.

    ``current_password`` accepts any historical password length so a
    legacy short password can be entered for verification (same rule
    as ``ChangePasswordSchema.current_password``).
    """

    current_password = fields.String(
        required=True,
        validate=validate.Length(min=1, max=_AUTH_PASSWORD_MAX_CHARS),
    )
    totp_code = fields.String(
        load_default="",
        validate=validate.Length(max=_TOTP_CODE_MAX_LENGTH),
    )

    @pre_load
    def strip_codes(self, data, **kwargs):
        """Strip whitespace from totp_code before length validation."""
        return _strip_code_field(dict(data), "totp_code")


class ReauthSchema(BaseSchema):
    """Validates POST data for /reauth (step-up re-authentication).

    Used when a high-value action (e.g. password change, MFA disable,
    salary edit) requires fresh proof of identity.  Mirrors the /login
    flow: password is required and totp_code is optional (only verified
    if the user has MFA enabled).  Backup codes are intentionally NOT
    accepted here -- they are single-use recovery credentials for the
    "I lost my authenticator" scenario at primary login.

    ``password`` accepts any historical length so a legacy short
    password can be entered for verification.
    """

    password = fields.String(
        required=True,
        validate=validate.Length(min=1, max=_AUTH_PASSWORD_MAX_CHARS),
    )
    totp_code = fields.String(
        load_default="",
        validate=validate.Length(max=_TOTP_CODE_MAX_LENGTH),
    )

    @pre_load
    def strip_codes(self, data, **kwargs):
        """Strip whitespace from totp_code before length validation."""
        return _strip_code_field(dict(data), "totp_code")


# --- Companion user management -------------------------------------------
#
# Companion schemas reuse the auth-level email/password constants and
# the shared ``_normalize_auth_form`` helper.  The companion paths have
# two extra rules: ``password_confirm`` rather than ``confirm_password``
# (matching the existing template), and the edit schema treats blank
# password fields as "no change" rather than "missing required field."


class CompanionCreateSchema(BaseSchema):
    """Validates POST data for creating a new companion user account.

    Required fields: email, display_name, password, password_confirm.
    Email is lowercased, stripped, and matched against a simple format
    regex.  Passwords must be at least 12 characters and at most 72
    UTF-8 bytes (bcrypt's ceiling), and password_confirm must match.

    Uniqueness of the email address is enforced by the calling route,
    not by the schema -- it needs a live database session.
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip whitespace and lowercase the email before field validation."""
        return _normalize_auth_form(data)

    email = _auth_email_field()
    display_name = fields.String(
        required=True,
        validate=validate.Length(min=1, max=_AUTH_DISPLAY_NAME_MAX_LENGTH),
    )
    password = fields.String(
        required=True,
        validate=validate.Length(min=_AUTH_PASSWORD_MIN_LENGTH),
    )
    password_confirm = fields.String(required=True)

    @validates_schema
    def validate_password_bytes(self, data, **kwargs):
        """Reject passwords longer than bcrypt's 72-byte UTF-8 limit."""
        password = data.get("password") or ""
        if len(password.encode("utf-8")) > _AUTH_PASSWORD_MAX_BYTES:
            raise ValidationError(
                "Password must be at most "
                f"{_AUTH_PASSWORD_MAX_BYTES} bytes.",
                "password",
            )

    @validates_schema
    def validate_password_match(self, data, **kwargs):
        """Require password_confirm to equal password."""
        if data.get("password") != data.get("password_confirm"):
            raise ValidationError(
                "Passwords do not match.", "password_confirm",
            )


class CompanionEditSchema(BaseSchema):
    """Validates POST data for editing an existing companion account.

    Email and display_name are required (same rules as the create
    schema).  Password fields are optional: blank means "keep the
    current password unchanged."  When a new password is supplied the
    same 12-character / 72-byte rules apply and password_confirm must
    match.  Email uniqueness is enforced by the calling route.
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip whitespace and lowercase the email before field validation."""
        return _normalize_auth_form(data)

    email = _auth_email_field()
    display_name = fields.String(
        required=True,
        validate=validate.Length(min=1, max=_AUTH_DISPLAY_NAME_MAX_LENGTH),
    )
    password = fields.String(load_default="")
    password_confirm = fields.String(load_default="")

    @validates_schema
    def validate_password_change(self, data, **kwargs):
        """Validate the password fields only when a new password is given.

        Blank password fields mean "no change" and pass silently.  Any
        non-blank password must satisfy the same length rules as the
        create schema and match its confirmation.
        """
        password = data.get("password") or ""
        confirm = data.get("password_confirm") or ""
        if not password and not confirm:
            return
        if len(password) < _AUTH_PASSWORD_MIN_LENGTH:
            raise ValidationError(
                "Password must be at least "
                f"{_AUTH_PASSWORD_MIN_LENGTH} characters.",
                "password",
            )
        if len(password.encode("utf-8")) > _AUTH_PASSWORD_MAX_BYTES:
            raise ValidationError(
                "Password must be at most "
                f"{_AUTH_PASSWORD_MAX_BYTES} bytes.",
                "password",
            )
        if password != confirm:
            raise ValidationError(
                "Passwords do not match.", "password_confirm",
            )
