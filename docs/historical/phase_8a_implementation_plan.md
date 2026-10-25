> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8A: Security Hardening -- Implementation Plan

## Overview

This plan implements Sub-Phase 8A from the Phase 8 Hardening & Ops Plan. It covers CSRF audit documentation, password management, MFA/TOTP with backup codes, rate limiting on MFA, session management improvements, and error page verification.

**Pre-existing infrastructure discovered during planning:**

- CSRF protection is fully in place. All 70 data-modifying forms are protected (43 traditional POST forms with explicit `csrf_token()`, 9 HTMX forms with explicit tokens, 18 HTMX forms via `X-CSRFToken` header injection in `app/static/js/app.js:54-60`). `CSRFProtect` is initialized in `app/extensions.py:28` and bound in `app/__init__.py:46`. No missing tokens were found.
- Error pages (404, 429, 500) already exist in `app/templates/errors/` with handlers registered in `app/__init__.py:197-210`.
- Flask-Limiter is already installed (`requirements.txt:4`) and configured on the login endpoint (`app/routes/auth.py:23`) with in-memory storage (`app/extensions.py:31`).
- Structured JSON logging with `request_id` is already configured in `app/utils/logging_config.py`.
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options, etc.) are already set in `app/__init__.py:213-232`.
- The `auth.mfa_configs` table stub already exists as a model in `app/models/user.py:88-108` with the correct schema (columns: `totp_secret_encrypted`, `is_enabled`, `backup_codes`, `confirmed_at`).

**New dependencies required:** `pyotp`, `qrcode[pil]`, `cryptography`

**Alembic migration required:** Add `session_invalidated_at` column to `auth.users` table.

---

## CSRF Audit Results

The audit was performed during plan creation. No remediation is needed. The findings are recorded here as the deliverable required by the Phase 8 plan.

### CSRF Infrastructure

| Component | Status | Location |
|-----------|--------|----------|
| CSRFProtect initialization | Active | `app/extensions.py:28`, `app/__init__.py:46` |
| Meta tag in base template | Present | `app/templates/base.html:8` |
| HTMX header injection | Active | `app/static/js/app.js:54-60` |
| CSRF disabled in tests | Correct | `app/config.py:48` (`WTF_CSRF_ENABLED = False`) |
| CSRF exemptions | None | No routes are exempt |

### Form Inventory (70 total, 100% protected)

#### Traditional POST Forms (43 forms, all with explicit `csrf_token()`)

| Template | Line | Method | Purpose |
|----------|------|--------|---------|
| `app/templates/auth/login.html` | 12 | POST | User login |
| `app/templates/pay_periods/generate.html` | 25 | POST | Generate pay periods |
| `app/templates/categories/_category_row.html` | 4 | POST | Category management |
| `app/templates/categories/list.html` | 25 | POST | Delete category |
| `app/templates/categories/list.html` | 47 | POST | Create category |
| `app/templates/templates/form.html` | 28 | POST | Create/edit transaction template |
| `app/templates/templates/list.html` | 97 | POST | Delete template |
| `app/templates/templates/list.html` | 106 | POST | Reactivate template |
| `app/templates/salary/form.html` | 28 | POST | Create/edit salary profile |
| `app/templates/salary/list.html` | 78 | POST | Delete salary profile |
| `app/templates/salary/tax_config.html` | 100 | POST | Update FICA config |
| `app/templates/salary/tax_config.html` | 172 | POST | Update tax config |
| `app/templates/settings/settings.html` | 20 | POST | Update user settings (legacy) |
| `app/templates/settings/_general.html` | 7 | POST | Update general settings |
| `app/templates/settings/_categories.html` | 12 | POST | Delete category (settings) |
| `app/templates/settings/_categories.html` | 34 | POST | Create category (settings) |
| `app/templates/settings/_pay_periods.html` | 12 | POST | Generate pay periods (settings) |
| `app/templates/settings/_tax_config.html` | 80 | POST | Update FICA config (settings) |
| `app/templates/settings/_tax_config.html` | 152 | POST | Update tax config (settings) |
| `app/templates/settings/_account_types.html` | 8 | POST | Update account type |
| `app/templates/settings/_account_types.html` | 27 | POST | Delete account type |
| `app/templates/settings/_account_types.html` | 42 | POST | Create account type |
| `app/templates/settings/_retirement.html` | 3 | POST | Update retirement settings |
| `app/templates/transfers/form.html` | 31 | POST | Create/edit transfer template |
| `app/templates/transfers/list.html` | 91 | POST | Delete transfer template |
| `app/templates/transfers/list.html` | 100 | POST | Reactivate transfer template |
| `app/templates/savings/goal_form.html` | 21 | POST | Create/edit savings goal |
| `app/templates/accounts/form.html` | 21 | POST | Create/edit account |
| `app/templates/accounts/hysa_detail.html` | 72 | POST | Update HYSA parameters |
| `app/templates/accounts/list.html` | 62 | POST | Deactivate account |
| `app/templates/accounts/list.html` | 71 | POST | Reactivate account |
| `app/templates/mortgage/setup.html` | 23 | POST | Create mortgage parameters |
| `app/templates/mortgage/dashboard.html` | 114 | POST | Update mortgage parameters |
| `app/templates/auto_loan/setup.html` | 23 | POST | Create auto loan parameters |
| `app/templates/auto_loan/dashboard.html` | 83 | POST | Update auto loan parameters |
| `app/templates/investment/dashboard.html` | 154 | POST | Update investment parameters |
| `app/templates/retirement/pension_form.html` | 55 | POST | Delete pension profile |
| `app/templates/retirement/pension_form.html` | 77 | POST | Create/edit pension profile |

#### HTMX Forms with Explicit CSRF Token (9 forms)

| Template | Line | Method | Purpose |
|----------|------|--------|---------|
| `app/templates/mortgage/_escrow_list.html` | 29 | hx-post | Delete escrow component |
| `app/templates/mortgage/_escrow_list.html` | 48 | hx-post | Add escrow component |
| `app/templates/mortgage/_rate_history.html` | 29 | hx-post | Add rate change |

#### HTMX Forms Protected via Header Injection (18 forms)

These forms use the `X-CSRFToken` header injected by `app/static/js/app.js:54-60`.

| Template | Line | Method | Purpose |
|----------|------|--------|---------|
| `app/templates/grid/_transaction_quick_edit.html` | 7 | hx-patch | Quick edit transaction amount |
| `app/templates/grid/_transaction_quick_create.html` | 11 | hx-post | Quick create transaction |
| `app/templates/grid/_transaction_full_create.html` | 22 | hx-post | Full create transaction |
| `app/templates/grid/_transaction_full_edit.html` | 16 | hx-patch | Full edit transaction |
| `app/templates/grid/_transaction_full_edit.html` | 76 | hx-post | Mark transaction done |
| `app/templates/grid/_transaction_full_edit.html` | 86 | hx-post | Mark transaction as credit |
| `app/templates/grid/_transaction_full_edit.html` | 94 | hx-delete | Unmark credit card |
| `app/templates/grid/_transaction_full_edit.html` | 104 | hx-post | Cancel transaction |
| `app/templates/grid/_transaction_full_edit.html` | 114 | hx-post | Mark income received |
| `app/templates/salary/_raises_section.html` | 52 | hx-post | Delete raise |
| `app/templates/salary/_raises_section.html` | 74 | hx-post | Add raise |
| `app/templates/salary/_deductions_section.html` | 53 | hx-post | Delete deduction |
| `app/templates/salary/_deductions_section.html` | 75 | hx-post | Add deduction |
| `app/templates/transfers/_transfer_quick_edit.html` | 8 | hx-patch | Quick edit transfer |
| `app/templates/transfers/_transfer_full_edit.html` | 16 | hx-patch | Full edit transfer |
| `app/templates/transfers/_transfer_full_edit.html` | 67 | hx-post | Mark transfer done |
| `app/templates/transfers/_transfer_full_edit.html` | 74 | hx-post | Cancel transfer |
| `app/templates/mortgage/dashboard.html` | 217 | hx-post | Payoff calculate (button) |
| `app/templates/mortgage/dashboard.html` | 250 | hx-post | Payoff calculate (form) |

### CSRF Audit Conclusion

No remediation required. All forms are protected. The global HTMX header injection pattern in `app.js` covers all dynamic requests. CSRFProtect is properly initialized and no exemptions exist.

---

## Rate Limiting Recommendation

**Decision: Continue using Flask-Limiter (already installed).**

Rationale:

- Flask-Limiter (`Flask-Limiter==4.1.1`) is already in `requirements.txt` and configured in `app/extensions.py:31`.
- It is already applied to the login endpoint (`app/routes/auth.py:23`) with `"5 per 15 minutes"`.
- In-memory storage (`storage_uri="memory://"`) is appropriate for single-instance deployment.
- Adding rate limiting to the MFA verify endpoint requires only a single decorator -- no new infrastructure.
- A custom middleware would duplicate functionality that Flask-Limiter already provides and would need its own testing.

No changes needed to the rate limiting infrastructure. The only work is adding the `@limiter.limit` decorator to the new MFA verification route.

---

## Work Units

The implementation is organized into 7 work units. Each unit leaves the app in a working state with all existing tests passing. Dependencies between units are noted.

### Dependency Graph

```
WU-1: Dependencies + Migration
  |
  v
WU-2: Password Change Flow
  |
  v
WU-3: Session Management
  |
  v
WU-4: MFA Service + Setup Flow
  |
  v
WU-5: MFA Login Flow
  |
  v
WU-6: MFA Disable + Recovery Script
  |
  v
WU-7: Error Pages + Production Verification
```

WU-7 is independent and can be done at any point, but is listed last for logical ordering.

---

### WU-1: Dependencies and Migration

**Goal:** Add new Python packages and the database migration for `session_invalidated_at`. No functional changes.

#### Files to Modify

**`requirements.txt`** -- Add three new dependencies after the `bcrypt` line (line 15):

```
pyotp==2.9.0
qrcode[pil]==8.0
cryptography==44.0.0
```

- `pyotp`: TOTP code generation and verification (RFC 6238).
- `qrcode[pil]`: QR code image generation for authenticator app setup. The `[pil]` extra pulls in Pillow for PNG rendering.
- `cryptography`: Fernet symmetric encryption for storing TOTP secrets at rest in the database.

**`app/models/user.py`** -- Add `session_invalidated_at` column to the `User` model (after `updated_at`, line 29):

```python
session_invalidated_at = db.Column(db.DateTime(timezone=True), nullable=True)
```

This column stores the timestamp of the most recent "log out all sessions" or password change event. The user loader compares it against the session creation time to invalidate stale sessions.

**`migrations/versions/<hash>_add_session_invalidated_at.py`** -- New Alembic migration:

- `op.add_column("users", sa.Column("session_invalidated_at", sa.DateTime(timezone=True), nullable=True), schema="auth")`

**`tests/conftest.py`** -- No changes needed. The `seed_user` fixture creates users with `session_invalidated_at=None` by default (nullable column).

#### Test Gate

- [ ] `pip install -r requirements.txt` succeeds
- [ ] `flask db upgrade` applies the migration
- [ ] `pytest` passes (all 769 existing tests)

---

### WU-2: Password Change Flow

**Goal:** Add a password change form to the Security section of the settings dashboard.

**Depends on:** WU-1 (the `session_invalidated_at` column must exist, though this unit does not use it yet -- that happens in WU-3).

#### Files to Create

**`app/templates/settings/_security.html`** -- New partial template for the Security settings section.

Structure:
```
<h5> with bi-shield-lock icon and "Security" heading
<hr> divider
<h6> "Change Password" subheading
<form method="POST" action="{{ url_for('auth.change_password') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  Current password input (type="password", name="current_password", required)
  New password input (type="password", name="new_password", required, minlength="12")
  Confirm password input (type="password", name="confirm_password", required)
  Help text: "Minimum 12 characters."
  Submit button: "Change Password"
</form>
<hr> divider
{# MFA section placeholder -- filled in WU-4 #}
{# Log out all sessions button placeholder -- filled in WU-3 #}
```

The template follows the same pattern as `settings/_retirement.html`: a `<h5>` heading with icon, then form content. No Jinja2 macros from `_form_macros.html` are needed since the password fields are simple inputs that do not match the existing macro signatures (which are designed for number/text/select fields).

#### Files to Modify

**`app/services/auth_service.py`** -- Add `change_password()` function after `authenticate()` (after line 63):

```python
def change_password(user, current_password, new_password):
    """Change a user's password after verifying the current one.

    Args:
        user: The User object whose password is being changed.
        current_password: The user's current plaintext password.
        new_password: The new plaintext password (must be >= 12 chars).

    Returns:
        None on success.

    Raises:
        AuthError: If current_password does not match the stored hash.
        ValidationError: If new_password is shorter than 12 characters.
    """
```

Implementation:
1. Call `verify_password(current_password, user.password_hash)`. If False, raise `AuthError("Current password is incorrect.")`.
2. If `len(new_password) < 12`, raise `ValidationError("New password must be at least 12 characters.")`.
3. Set `user.password_hash = hash_password(new_password)`.
4. No `db.session.commit()` -- the caller (route layer) handles the commit.

**`app/routes/auth.py`** -- Add `change_password()` route after `logout()` (after line 58):

```python
@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    """Process a password change request from the Security settings section."""
```

Implementation:
1. Extract `current_password`, `new_password`, `confirm_password` from `request.form`.
2. If `new_password != confirm_password`, flash "New password and confirmation do not match." (danger), redirect to `settings.show` with `section=security`.
3. Call `auth_service.change_password(current_user, current_password, new_password)`.
4. On success: `db.session.commit()`, log the event (`logger.info("action=password_changed user_id=%s", current_user.id)`), flash "Password changed successfully." (success), redirect to `settings.show` with `section=security`.
5. Catch `AuthError`: flash the error message (danger), redirect.
6. Catch `ValidationError`: flash the error message (danger), redirect.

**`app/routes/settings.py`** -- Add "security" to `_VALID_SECTIONS` (line 25) and add the section's `elif` block:

- Line 25: Add `"security"` to the list: `["general", "categories", "pay-periods", "tax", "account-types", "retirement", "security"]`
- After line 106 (the `# elif section == "retirement"` comment), add: `# elif section == "security": no data loading needed.`
- No additional context variables needed. The security template uses only `url_for()` calls.

**`app/templates/settings/dashboard.html`** -- Add the Security sidebar link and content include:

Sidebar (insert after the Retirement link, before the closing `</div>` on line 44):
```html
<a href="{{ url_for('settings.show', section='security') }}"
   class="list-group-item list-group-item-action{{ ' active' if active_section == 'security' }}">
  <i class="bi bi-shield-lock"></i> Security
</a>
```

Content area (insert after the retirement include, before `{% endif %}` on line 65):
```html
{% elif active_section == 'security' %}
  {% include 'settings/_security.html' %}
```

**`app/exceptions.py`** -- No changes needed. `AuthError` and `ValidationError` already exist (lines 21, 17).

#### Test Gate

- [ ] `pytest` passes (all existing tests, including `test_settings_dashboard_invalid_section_defaults_to_general`)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_routes/test_auth.py`** -- Add `TestPasswordChange` class:

```python
class TestPasswordChange:
    """Tests for POST /change-password."""

    def test_change_password_success(self, app, auth_client, seed_user):
        """POST /change-password with valid data changes the password."""

    def test_change_password_wrong_current(self, app, auth_client, seed_user):
        """POST /change-password with wrong current password shows error."""

    def test_change_password_mismatch(self, app, auth_client, seed_user):
        """POST /change-password with mismatched new/confirm shows error."""

    def test_change_password_too_short(self, app, auth_client, seed_user):
        """POST /change-password with password under 12 chars shows error."""

    def test_change_password_requires_login(self, app, client):
        """POST /change-password without login redirects to login."""
```

**`tests/test_services/test_auth_service.py`** -- Add `TestChangePassword` class:

```python
class TestChangePassword:
    """Tests for auth_service.change_password()."""

    def test_change_password_success(self, app, db, seed_user):
        """change_password() updates the password hash."""

    def test_change_password_wrong_current_raises(self, app, db, seed_user):
        """change_password() raises AuthError for wrong current password."""

    def test_change_password_too_short_raises(self, app, db, seed_user):
        """change_password() raises ValidationError for short password."""
```

**`tests/test_routes/test_settings.py`** -- Add one test to `TestSettingsDashboard`:

```python
def test_settings_dashboard_security_section(self, app, auth_client, seed_user):
    """GET /settings?section=security renders the Security section."""
```

---

### WU-3: Session Management

**Goal:** Add "Log out all sessions" functionality and integrate session invalidation with the password change flow.

**Depends on:** WU-1 (migration for `session_invalidated_at`), WU-2 (password change route).

#### Files to Modify

**`app/__init__.py`** -- Modify the `load_user` callback (lines 52-55) to check `session_invalidated_at`:

Current:
```python
@login_manager.user_loader
def load_user(user_id):
    """Load a user by ID for Flask-Login session hydration."""
    return db.session.get(User, int(user_id))
```

New logic:
```python
@login_manager.user_loader
def load_user(user_id):
    """Load a user by ID for Flask-Login session hydration.

    Returns None (forcing re-login) if the user's sessions have been
    invalidated after the current session was created.
    """
    user = db.session.get(User, int(user_id))
    if user is None:
        return None
    if user.session_invalidated_at is not None:
        from flask import session  # pylint: disable=import-outside-toplevel
        session_created = session.get("_session_created_at")
        if session_created is not None:
            from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel
            created_dt = datetime.fromisoformat(session_created)
            if created_dt < user.session_invalidated_at:
                return None
    return user
```

Also add a `before_request` handler (or extend the existing one) to stamp `_session_created_at` into the session on login. This is done in the login route instead (see below).

**`app/routes/auth.py`** -- Two changes:

1. In the `login()` function, after `login_user(user, remember=remember)` (line 37), add:
   ```python
   from flask import session as flask_session  # at top of file
   flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
   ```
   Import `datetime` and `timezone` from the `datetime` module at the top of the file.

2. Add a new route for "Log out all sessions":
   ```python
   @auth_bp.route("/invalidate-sessions", methods=["POST"])
   @login_required
   def invalidate_sessions():
       """Invalidate all sessions for the current user except the current one.

       Sets session_invalidated_at to now, which causes load_user() to
       reject any session created before this timestamp. The current
       session is refreshed with a new creation timestamp.
       """
   ```
   Implementation:
   1. Set `current_user.session_invalidated_at = datetime.now(timezone.utc)`.
   2. `db.session.commit()`.
   3. Refresh the current session's `_session_created_at` to now (so the current session is not invalidated).
   4. Log the event.
   5. Flash "All other sessions have been logged out." (success).
   6. Redirect to `settings.show` with `section=security`.

3. In the `change_password()` route (added in WU-2), after `db.session.commit()`, add session invalidation:
   ```python
   current_user.session_invalidated_at = datetime.now(timezone.utc)
   db.session.commit()
   flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
   ```
   This ensures all other sessions are invalidated on password change.

**`app/templates/settings/_security.html`** -- Add the "Log out all sessions" section after the password change form:

```html
<hr>
<h6>Active Sessions</h6>
<p class="text-muted small">
  Log out of all other browser sessions. Your current session will remain active.
</p>
<form method="POST" action="{{ url_for('auth.invalidate_sessions') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <button type="submit" class="btn btn-outline-warning btn-sm">
    <i class="bi bi-box-arrow-right"></i> Log Out All Other Sessions
  </button>
</form>
```

**Session cleanup note:** The app uses Flask's default cookie-based sessions (client-side, encrypted). There is no server-side session store, so no session cleanup mechanism is needed. The `session_invalidated_at` timestamp on the User record is the invalidation mechanism; stale session cookies are rejected at the `load_user()` level.

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_routes/test_auth.py`** -- Add `TestSessionManagement` class:

```python
class TestSessionManagement:
    """Tests for session invalidation functionality."""

    def test_invalidate_sessions(self, app, auth_client, seed_user):
        """POST /invalidate-sessions sets session_invalidated_at on user."""

    def test_invalidate_sessions_current_session_survives(self, app, auth_client, seed_user):
        """Current session remains valid after invalidation."""

    def test_stale_session_rejected(self, app, client, seed_user):
        """A session created before session_invalidated_at is rejected."""

    def test_password_change_invalidates_sessions(self, app, auth_client, seed_user):
        """Password change sets session_invalidated_at."""

    def test_invalidate_sessions_requires_login(self, app, client):
        """POST /invalidate-sessions without login redirects to login."""
```

#### Impact on Existing Tests

The `auth_client` fixture (`tests/conftest.py:230-240`) logs in via `client.post("/login", ...)`. After WU-3, the login route will also stamp `_session_created_at` into the session. This is additive and does not break any existing behavior because:

- `session_invalidated_at` defaults to `None` on the test user (created by `seed_user` fixture).
- When `session_invalidated_at` is `None`, `load_user()` skips the timestamp check entirely.
- No existing test modifies `session_invalidated_at`.

Therefore, **no existing test fixtures or tests need modification** for this work unit.

---

### WU-4: MFA Service and Setup Flow

**Goal:** Implement TOTP setup, QR code generation, backup code generation, and the MFA setup UI in the Security settings section.

**Depends on:** WU-2 (Security section in settings dashboard), WU-1 (pyotp, qrcode, cryptography packages).

#### Files to Create

**`app/services/mfa_service.py`** -- New service module for MFA operations.

```python
"""
Shekel Budget App -- MFA Service

Handles TOTP secret generation, verification, backup code management,
and secret encryption/decryption.  No Flask imports -- pure service module.
"""
```

Functions:

```python
def get_encryption_key():
    """Load the Fernet encryption key from the TOTP_ENCRYPTION_KEY env var.

    Returns:
        A Fernet instance initialized with the key.

    Raises:
        RuntimeError: If TOTP_ENCRYPTION_KEY is not set.
    """
```
Implementation: Read `os.getenv("TOTP_ENCRYPTION_KEY")`. If not set, raise `RuntimeError`. Return `Fernet(key)`.

```python
def generate_totp_secret():
    """Generate a new random TOTP secret.

    Returns:
        A base32-encoded secret string suitable for pyotp.
    """
```
Implementation: `return pyotp.random_base32()`

```python
def encrypt_secret(plaintext_secret):
    """Encrypt a TOTP secret for database storage.

    Args:
        plaintext_secret: The base32-encoded TOTP secret string.

    Returns:
        Encrypted bytes suitable for storing in a LargeBinary column.
    """
```
Implementation: `return get_encryption_key().encrypt(plaintext_secret.encode("utf-8"))`

```python
def decrypt_secret(encrypted_secret):
    """Decrypt a TOTP secret from database storage.

    Args:
        encrypted_secret: The encrypted bytes from the database.

    Returns:
        The plaintext base32-encoded TOTP secret string.
    """
```
Implementation: `return get_encryption_key().decrypt(encrypted_secret).decode("utf-8")`

```python
def get_totp_uri(secret, email, issuer="Shekel"):
    """Build the otpauth:// URI for QR code generation.

    Args:
        secret: The base32-encoded TOTP secret.
        email: The user's email address (used as the account name).
        issuer: The application name shown in authenticator apps.

    Returns:
        An otpauth:// URI string.
    """
```
Implementation: `return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)`

```python
def generate_qr_code_data_uri(uri):
    """Generate a base64-encoded PNG data URI for a QR code.

    Args:
        uri: The otpauth:// URI to encode.

    Returns:
        A data:image/png;base64,... string suitable for an <img> src attribute.
    """
```
Implementation: Use `qrcode.make(uri)` to create the image, write to a `BytesIO` buffer as PNG, base64-encode, return as data URI.

```python
def verify_totp_code(secret, code):
    """Verify a 6-digit TOTP code against a secret.

    Args:
        secret: The base32-encoded TOTP secret.
        code: The 6-digit code string from the user.

    Returns:
        True if the code is valid (within the default 30-second window),
        False otherwise.
    """
```
Implementation: `return pyotp.TOTP(secret).verify(code, valid_window=1)`. The `valid_window=1` allows one period of clock drift (30 seconds before/after).

```python
def generate_backup_codes(count=10):
    """Generate a set of single-use backup codes.

    Args:
        count: Number of codes to generate (default 10).

    Returns:
        A list of 8-character alphanumeric plaintext code strings.
    """
```
Implementation: Use `secrets.token_hex(4)` for each code (produces 8 hex characters). Return the list.

```python
def hash_backup_codes(codes):
    """Hash a list of plaintext backup codes for database storage.

    Args:
        codes: List of plaintext backup code strings.

    Returns:
        A list of bcrypt hash strings.
    """
```
Implementation: `return [bcrypt.hashpw(c.encode("utf-8"), bcrypt.gensalt()).decode("utf-8") for c in codes]`

```python
def verify_backup_code(code, hashed_codes):
    """Check a backup code against the list of stored hashes.

    Args:
        code: The plaintext backup code to verify.
        hashed_codes: List of bcrypt hash strings from the database.

    Returns:
        The index of the matching hash if found, or -1 if no match.
    """
```
Implementation: Iterate `hashed_codes`, call `bcrypt.checkpw()` on each. Return the index of the first match, or -1.

**`app/templates/settings/_mfa_setup.html`** -- New partial template for MFA setup.

Structure (three states, controlled by template variables):

**State 1: MFA not enabled (`mfa_enabled` is False, `mfa_setup_qr` is None)**
```
<h6>Two-Factor Authentication</h6>
<p>Two-factor authentication adds an extra layer of security to your account.</p>
<a href="{{ url_for('auth.mfa_setup') }}" class="btn btn-primary btn-sm">
  <i class="bi bi-shield-plus"></i> Set Up Two-Factor Authentication
</a>
```

**State 2: MFA enabled (`mfa_enabled` is True)**
```
<h6>Two-Factor Authentication</h6>
<span class="badge bg-success">Enabled</span>
<p>Your account is protected with two-factor authentication.</p>
<div class="d-flex gap-2">
  <form method="POST" action="{{ url_for('auth.regenerate_backup_codes') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <button class="btn btn-outline-secondary btn-sm">
      <i class="bi bi-arrow-repeat"></i> Regenerate Backup Codes
    </button>
  </form>
  <a href="{{ url_for('auth.mfa_disable') }}" class="btn btn-outline-danger btn-sm">
    <i class="bi bi-shield-x"></i> Disable Two-Factor Authentication
  </a>
</div>
```

**`app/templates/auth/mfa_setup.html`** -- Full page template for MFA setup flow (not a settings partial, since it involves QR code display and confirmation).

Extends `base.html`. Structure:
```
Breadcrumbs: Home > Settings > Security > Set Up 2FA
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card">
      <div class="card-body">
        <h5>Set Up Two-Factor Authentication</h5>
        <p>Scan this QR code with your authenticator app:</p>
        <div class="text-center mb-3">
          <img src="{{ qr_data_uri }}" alt="TOTP QR Code" class="img-fluid" style="max-width: 200px;">
        </div>
        <p>Or enter this key manually: <code>{{ manual_key }}</code></p>
        <hr>
        <form method="POST" action="{{ url_for('auth.mfa_confirm') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <input type="hidden" name="setup_token" value="{{ setup_token }}">
          <label>Enter the 6-digit code from your authenticator app:</label>
          <input type="text" name="totp_code" class="form-control" maxlength="6"
                 pattern="[0-9]{6}" inputmode="numeric" autocomplete="one-time-code" required>
          <button type="submit" class="btn btn-primary mt-3 w-100">Verify and Enable</button>
        </form>
      </div>
    </div>
  </div>
</div>
```

Note on the `setup_token`: The TOTP secret must be temporarily stored between the setup page render and the confirmation POST. Options:
- Store the encrypted secret in the Flask session (`session["_mfa_setup_secret"]`).
- The `setup_token` hidden field is a signed token (using `itsdangerous.URLSafeTimedSerializer` or Flask's `SECRET_KEY`) that contains the encrypted secret. This prevents the secret from being tampered with client-side.

Recommended approach: Store the encrypted secret in the Flask session. The `setup_token` hidden field is not needed. This simplifies the flow and avoids exposing the secret in the HTML.

**`app/templates/auth/mfa_backup_codes.html`** -- Full page template shown once after MFA setup.

Extends `base.html`. Structure:
```
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card border-warning">
      <div class="card-body">
        <h5><i class="bi bi-exclamation-triangle text-warning"></i> Save Your Backup Codes</h5>
        <p>These codes can be used to access your account if you lose your authenticator device.
           Each code can only be used once. Store them in a safe place.</p>
        <p><strong>You will not be able to see these codes again.</strong></p>
        <div class="bg-dark p-3 rounded font-monospace">
          {% for code in backup_codes %}
          <div>{{ code }}</div>
          {% endfor %}
        </div>
        <a href="{{ url_for('settings.show', section='security') }}" class="btn btn-primary mt-3 w-100">
          I've Saved My Codes
        </a>
      </div>
    </div>
  </div>
</div>
```

#### Files to Modify

**`app/routes/auth.py`** -- Add MFA setup, confirm, and backup code regeneration routes:

```python
@auth_bp.route("/mfa/setup", methods=["GET"])
@login_required
def mfa_setup():
    """Display the MFA setup page with QR code and manual key.

    Generates a TOTP secret and stores it in the session for
    confirmation in the next step.
    """
```
Implementation:
1. Check if MFA is already enabled. If so, flash message and redirect to security settings.
2. Generate secret via `mfa_service.generate_totp_secret()`.
3. Store in `session["_mfa_setup_secret"] = secret`.
4. Generate QR code data URI via `mfa_service.generate_qr_code_data_uri(mfa_service.get_totp_uri(secret, current_user.email))`.
5. Render `auth/mfa_setup.html` with `qr_data_uri` and `manual_key=secret`.

```python
@auth_bp.route("/mfa/confirm", methods=["POST"])
@login_required
def mfa_confirm():
    """Verify the TOTP code and enable MFA for the user.

    Reads the setup secret from the session, verifies the submitted
    code, and if valid, encrypts and stores the secret, generates
    backup codes, and redirects to the backup codes page.
    """
```
Implementation:
1. Retrieve `secret = session.pop("_mfa_setup_secret", None)`. If None, flash error and redirect.
2. Extract `totp_code` from `request.form`.
3. Verify via `mfa_service.verify_totp_code(secret, totp_code)`. If invalid, re-store secret in session, flash "Invalid code. Please try again.", redirect to `auth.mfa_setup`.
4. Get or create `MfaConfig` for `current_user.id`.
5. Set `mfa_config.totp_secret_encrypted = mfa_service.encrypt_secret(secret)`.
6. Set `mfa_config.is_enabled = True`.
7. Set `mfa_config.confirmed_at = datetime.now(timezone.utc)`.
8. Generate backup codes: `codes = mfa_service.generate_backup_codes()`.
9. Store hashed codes: `mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)`.
10. `db.session.commit()`.
11. Log the event.
12. Render `auth/mfa_backup_codes.html` with `backup_codes=codes`.

```python
@auth_bp.route("/mfa/regenerate-backup-codes", methods=["POST"])
@login_required
def regenerate_backup_codes():
    """Generate a new set of backup codes, replacing the old ones."""
```
Implementation:
1. Get `MfaConfig` for `current_user.id`. If not found or not enabled, flash error and redirect.
2. Generate new codes: `codes = mfa_service.generate_backup_codes()`.
3. Store hashed: `mfa_config.backup_codes = mfa_service.hash_backup_codes(codes)`.
4. `db.session.commit()`.
5. Log the event.
6. Render `auth/mfa_backup_codes.html` with `backup_codes=codes`.

**`app/routes/settings.py`** -- Add MFA status data loading for the security section:

In the `show()` function, add an `elif section == "security"` block (after the retirement comment on line 106):

```python
elif section == "security":
    from app.models.user import MfaConfig  # pylint: disable=import-outside-toplevel
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    mfa_enabled = mfa_config.is_enabled if mfa_config else False
```

Add `mfa_enabled=False` to the default variables at the top of the function (alongside `errors = {}`, etc.), and pass `mfa_enabled=mfa_enabled` to `render_template()`.

**`app/templates/settings/_security.html`** -- Add the MFA section below the "Log out all sessions" section (from WU-3):

```html
<hr>
{% include 'settings/_mfa_setup.html' %}
```

**`app/config.py`** -- Add `TOTP_ENCRYPTION_KEY` to config:

In `BaseConfig` (after line 21):
```python
TOTP_ENCRYPTION_KEY = os.getenv("TOTP_ENCRYPTION_KEY")
```

In `ProdConfig.__init__()` (after line 70), add validation:
```python
if not os.getenv("TOTP_ENCRYPTION_KEY"):
    raise ValueError("TOTP_ENCRYPTION_KEY must be set in production.")
```

**`.env.example`** (or document in the existing `.env`): Add `TOTP_ENCRYPTION_KEY` entry. The key can be generated with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**`app/__init__.py`** -- Update CSP `img-src` directive. Currently `img-src 'self' data:` (line 229). This already allows `data:` URIs, so the QR code base64 image will work without changes.

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_services/test_mfa_service.py`** -- New test file:

```python
class TestGenerateSecret:
    """Tests for mfa_service.generate_totp_secret()."""

    def test_generates_base32_string(self):
        """generate_totp_secret() returns a valid base32 string."""

    def test_generates_unique_secrets(self):
        """Two calls return different secrets."""


class TestEncryptDecrypt:
    """Tests for mfa_service.encrypt_secret() and decrypt_secret()."""

    def test_round_trip(self, monkeypatch):
        """Encrypting then decrypting returns the original secret."""

    def test_encrypted_differs_from_plaintext(self, monkeypatch):
        """Encrypted output is not the same as the plaintext."""


class TestVerifyTotpCode:
    """Tests for mfa_service.verify_totp_code()."""

    def test_valid_code_accepted(self):
        """verify_totp_code() returns True for the current valid code."""

    def test_invalid_code_rejected(self):
        """verify_totp_code() returns False for a wrong code."""


class TestBackupCodes:
    """Tests for backup code generation, hashing, and verification."""

    def test_generate_backup_codes_count(self):
        """generate_backup_codes() returns the requested number of codes."""

    def test_generate_backup_codes_format(self):
        """Each backup code is an 8-character hex string."""

    def test_hash_and_verify_round_trip(self):
        """A generated code matches its own hash via verify_backup_code()."""

    def test_verify_wrong_code_returns_negative(self):
        """verify_backup_code() returns -1 for an unrecognized code."""

    def test_verify_returns_correct_index(self):
        """verify_backup_code() returns the index of the matching hash."""


class TestGetTotpUri:
    """Tests for mfa_service.get_totp_uri()."""

    def test_uri_format(self):
        """get_totp_uri() returns an otpauth:// URI with correct parameters."""


class TestGenerateQrCode:
    """Tests for mfa_service.generate_qr_code_data_uri()."""

    def test_returns_data_uri(self):
        """generate_qr_code_data_uri() returns a data:image/png;base64 string."""
```

**`tests/test_routes/test_auth.py`** -- Add `TestMfaSetup` class:

```python
class TestMfaSetup:
    """Tests for the MFA setup flow."""

    def test_mfa_setup_page_renders(self, app, auth_client, seed_user, monkeypatch):
        """GET /mfa/setup renders the QR code and manual key."""

    def test_mfa_setup_redirects_if_already_enabled(self, app, auth_client, seed_user):
        """GET /mfa/setup redirects if MFA is already enabled."""

    def test_mfa_confirm_valid_code(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm with valid TOTP code enables MFA and shows backup codes."""

    def test_mfa_confirm_invalid_code(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/confirm with invalid code shows error and redirects."""

    def test_mfa_confirm_no_session_secret(self, app, auth_client, seed_user):
        """POST /mfa/confirm without setup secret in session shows error."""

    def test_regenerate_backup_codes(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes generates new codes."""

    def test_regenerate_backup_codes_requires_mfa_enabled(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes without MFA enabled shows error."""
```

Note: Tests that involve TOTP code generation will use `monkeypatch` to mock `pyotp.TOTP.now()` to return a known code, and `monkeypatch` to set the `TOTP_ENCRYPTION_KEY` environment variable.

**`tests/conftest.py`** -- Add a fixture for the encryption key:

```python
@pytest.fixture(autouse=True)
def set_totp_key(monkeypatch):
    """Set a test TOTP encryption key for all tests."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
```

Note: This must be a function-scoped fixture since `monkeypatch` is function-scoped. The session-scoped `app` fixture does not need the key at creation time (the key is only read when `mfa_service` functions are called).

---

### WU-5: MFA Login Flow

**Goal:** Modify the login flow to require a TOTP code as a second step when MFA is enabled.

**Depends on:** WU-4 (MFA service and MfaConfig model must be populated).

#### Design Decisions

**No partial auth state:** The Phase 8 plan specifies "The session is not created until both steps pass; no partial auth state." This means:

1. After step 1 (email + password), the user is NOT logged in. Instead, the user's ID is stored in the Flask session as `_mfa_pending_user_id`, along with the `remember` preference.
2. The user is redirected to a TOTP verification page.
3. After successful TOTP verification, `login_user()` is called.
4. If the user navigates away from the TOTP page, the pending state expires (session-based, no database changes).

**Impact on existing tests:** The `auth_client` fixture logs in via `client.post("/login", ...)`. Since MFA is optional per user and the test user (`seed_user`) does not have MFA enabled, the existing login flow completes in one step. **No changes to `auth_client` or any existing test are needed.** The two-step flow only activates when `mfa_config.is_enabled` is True for the authenticating user.

#### Files to Create

**`app/templates/auth/mfa_verify.html`** -- MFA verification page.

Extends `base.html`. Structure:
```
<div class="row justify-content-center mt-5">
  <div class="col-md-4 col-lg-3">
    <div class="card shadow-sm">
      <div class="card-body">
        <div class="text-center mb-4">
          <img src="{{ url_for('static', filename='img/shekel_logo.png') }}" alt="Shekel" height="40" style="width: auto;">
        </div>
        <h5 class="text-center mb-3">Two-Factor Verification</h5>
        <form method="POST" action="{{ url_for('auth.mfa_verify') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <div class="mb-3">
            <label for="totp_code" class="form-label">Authentication Code</label>
            <input type="text" class="form-control" id="totp_code" name="totp_code"
                   maxlength="6" pattern="[0-9]{6}" inputmode="numeric"
                   autocomplete="one-time-code" required autofocus
                   placeholder="000000">
          </div>
          <button type="submit" class="btn btn-primary w-100">Verify</button>
        </form>
        <hr>
        <form method="POST" action="{{ url_for('auth.mfa_verify') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <div class="mb-3">
            <label for="backup_code" class="form-label">Or use a backup code</label>
            <input type="text" class="form-control" id="backup_code" name="backup_code"
                   maxlength="8" autocomplete="off"
                   placeholder="Backup code">
          </div>
          <button type="submit" class="btn btn-outline-secondary w-100">Use Backup Code</button>
        </form>
      </div>
    </div>
  </div>
</div>
```

This template mirrors the `login.html` card layout for visual consistency.

#### Files to Modify

**`app/routes/auth.py`** -- Modify the `login()` function and add `mfa_verify()`:

Changes to `login()` (lines 22-48):

After `user = auth_service.authenticate(email, password)` (line 36), add an MFA check before `login_user()`:

```python
# Check if MFA is enabled for this user.
mfa_config = (
    db.session.query(MfaConfig)
    .filter_by(user_id=user.id, is_enabled=True)
    .first()
)
if mfa_config:
    # Store pending auth state in session (user is NOT logged in yet).
    flask_session["_mfa_pending_user_id"] = user.id
    flask_session["_mfa_pending_remember"] = remember
    flask_session["_mfa_pending_next"] = request.args.get("next")
    return redirect(url_for("auth.mfa_verify"))

# No MFA -- complete login immediately.
login_user(user, remember=remember)
flask_session["_session_created_at"] = datetime.now(timezone.utc).isoformat()
```

Add the MFA verify route:

```python
@auth_bp.route("/mfa/verify", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def mfa_verify():
    """Display the MFA verification form and handle code submission.

    Requires a pending MFA user_id in the session (set by the login
    route after successful password verification). Completes the login
    on valid TOTP or backup code.
    """
```
Implementation:
1. Check `flask_session.get("_mfa_pending_user_id")`. If not present, redirect to `auth.login`.
2. On GET: render `auth/mfa_verify.html`.
3. On POST:
   a. Get `totp_code` and `backup_code` from `request.form`.
   b. Load user by `_mfa_pending_user_id`.
   c. Load `MfaConfig` for the user.
   d. If `totp_code` is provided: verify via `mfa_service.verify_totp_code(decrypted_secret, totp_code)`.
   e. If `backup_code` is provided: verify via `mfa_service.verify_backup_code(backup_code, mfa_config.backup_codes)`. If valid, remove the used hash from the list and update the database.
   f. If neither code is valid: flash generic error "Invalid verification code." (danger), re-render the page. Do not reveal whether the code was wrong or expired (per Phase 8 plan).
   g. If valid: clear pending session keys, call `login_user(user, remember=remember)`, stamp `_session_created_at`, log the event, redirect to `next_page` or `grid.index`.

**`app/routes/auth.py`** -- Add import for `MfaConfig` at the top:

```python
from app.models.user import MfaConfig
from app.services import mfa_service
```

#### Test Gate

- [ ] `pytest` passes (all 769+ existing tests unchanged)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_routes/test_auth.py`** -- Add `TestMfaLogin` class:

```python
class TestMfaLogin:
    """Tests for the two-step MFA login flow."""

    def test_login_with_mfa_redirects_to_verify(self, app, client, seed_user, monkeypatch):
        """POST /login with MFA enabled redirects to /mfa/verify instead of grid."""

    def test_mfa_verify_page_renders(self, app, client, seed_user, monkeypatch):
        """GET /mfa/verify renders the verification form when pending user exists."""

    def test_mfa_verify_no_pending_redirects_to_login(self, app, client):
        """GET /mfa/verify without pending user redirects to login."""

    def test_mfa_verify_valid_totp(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with valid TOTP code completes login."""

    def test_mfa_verify_invalid_totp(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with invalid TOTP code shows generic error."""

    def test_mfa_verify_valid_backup_code(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with valid backup code completes login and consumes the code."""

    def test_mfa_verify_invalid_backup_code(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with invalid backup code shows generic error."""

    def test_mfa_verify_backup_code_consumed(self, app, client, seed_user, monkeypatch):
        """A used backup code cannot be reused."""

    def test_login_without_mfa_unchanged(self, app, client, seed_user):
        """POST /login without MFA enabled completes in one step (existing behavior)."""
```

These tests create an `MfaConfig` with `is_enabled=True` and a known TOTP secret (via `monkeypatch` to control `pyotp.TOTP.now()`). The `seed_user` fixture does not enable MFA, so existing tests using `auth_client` are unaffected.

#### Impact on Existing Tests

**None.** The key design decision is that MFA is opt-in per user. The test user created by `seed_user` has no `MfaConfig` row (or has `is_enabled=False`). The modified `login()` route only redirects to MFA verify when `mfa_config` exists and `is_enabled=True`. All existing tests that use `auth_client` continue to log in with a single POST.

---

### WU-6: MFA Disable and Recovery Script

**Goal:** Allow users to disable MFA from the Security settings (requires password + TOTP code) and provide a CLI script for emergency MFA reset.

**Depends on:** WU-5 (MFA login flow must be complete so disable can be tested end-to-end).

#### Files to Create

**`app/templates/auth/mfa_disable.html`** -- Full page template for MFA disable confirmation.

Extends `base.html`. Structure:
```
Breadcrumbs: Home > Settings > Security > Disable 2FA
<div class="row justify-content-center">
  <div class="col-md-5">
    <div class="card border-danger">
      <div class="card-body">
        <h5><i class="bi bi-shield-x text-danger"></i> Disable Two-Factor Authentication</h5>
        <p>Enter your current password and a code from your authenticator app to confirm.</p>
        <form method="POST" action="{{ url_for('auth.mfa_disable_confirm') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <div class="mb-3">
            <label class="form-label">Current Password</label>
            <input type="password" name="current_password" class="form-control" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Authentication Code</label>
            <input type="text" name="totp_code" class="form-control" maxlength="6"
                   pattern="[0-9]{6}" inputmode="numeric" autocomplete="one-time-code" required>
          </div>
          <button type="submit" class="btn btn-danger w-100">Disable Two-Factor Authentication</button>
        </form>
      </div>
    </div>
  </div>
</div>
```

**`scripts/reset_mfa.py`** -- CLI script for emergency MFA reset.

```python
"""
Shekel Budget App -- MFA Reset Script

Emergency script to disable MFA for a user when backup codes are
exhausted and the TOTP device is lost.  Requires direct database access.

Usage:
    python scripts/reset_mfa.py <user_email>

Example:
    python scripts/reset_mfa.py admin@shekel.local
"""
```

Structure:
```python
import sys

def reset_mfa(email):
    """Disable MFA for the user with the given email address.

    Args:
        email: The email address of the user to reset.

    Prints status messages to stdout.
    Exits with code 1 if the user is not found.
    """
```
Implementation:
1. Import `create_app` and create the app.
2. Within `app.app_context()`:
   a. Query `User` by email. If not found, print error and `sys.exit(1)`.
   b. Query `MfaConfig` by `user_id`. If not found or not enabled, print "MFA is not enabled for this user." and exit.
   c. Set `mfa_config.totp_secret_encrypted = None`.
   d. Set `mfa_config.is_enabled = False`.
   e. Set `mfa_config.backup_codes = None`.
   f. Set `mfa_config.confirmed_at = None`.
   g. `db.session.commit()`.
   h. Print "MFA has been disabled for {email}."

Main block:
```python
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/reset_mfa.py <user_email>")
        sys.exit(1)
    reset_mfa(sys.argv[1])
```

#### Files to Modify

**`app/routes/auth.py`** -- Add MFA disable routes:

```python
@auth_bp.route("/mfa/disable", methods=["GET"])
@login_required
def mfa_disable():
    """Display the MFA disable confirmation page.

    Requires MFA to be currently enabled.
    """
```
Implementation: Check MFA is enabled, render `auth/mfa_disable.html`.

```python
@auth_bp.route("/mfa/disable", methods=["POST"])
@login_required
def mfa_disable_confirm():
    """Process MFA disable after verifying password and TOTP code.

    Clears the TOTP secret, backup codes, and sets is_enabled to False.
    """
```
Implementation:
1. Extract `current_password` and `totp_code` from `request.form`.
2. Verify password via `auth_service.verify_password(current_password, current_user.password_hash)`. If False, flash "Invalid password." (danger), redirect back.
3. Load `MfaConfig`, decrypt secret, verify TOTP code. If invalid, flash "Invalid authentication code." (danger), redirect back.
4. Clear MFA: set `totp_secret_encrypted = None`, `is_enabled = False`, `backup_codes = None`, `confirmed_at = None`.
5. `db.session.commit()`.
6. Log the event.
7. Flash "Two-factor authentication has been disabled." (success).
8. Redirect to `settings.show` with `section=security`.

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_routes/test_auth.py`** -- Add `TestMfaDisable` class:

```python
class TestMfaDisable:
    """Tests for the MFA disable flow."""

    def test_mfa_disable_page_renders(self, app, auth_client, seed_user):
        """GET /mfa/disable renders the confirmation form when MFA is enabled."""

    def test_mfa_disable_redirects_if_not_enabled(self, app, auth_client, seed_user):
        """GET /mfa/disable redirects if MFA is not enabled."""

    def test_mfa_disable_success(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/disable with valid password + TOTP disables MFA."""

    def test_mfa_disable_wrong_password(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/disable with wrong password shows error."""

    def test_mfa_disable_wrong_totp(self, app, auth_client, seed_user, monkeypatch):
        """POST /mfa/disable with wrong TOTP code shows error."""
```

**`tests/test_scripts/test_reset_mfa.py`** -- New test file:

```python
class TestResetMfa:
    """Tests for scripts/reset_mfa.py."""

    def test_reset_mfa_disables_for_user(self, app, db, seed_user):
        """reset_mfa() clears MFA config for the given email."""

    def test_reset_mfa_user_not_found(self, app, db, capsys):
        """reset_mfa() prints error and exits for unknown email."""

    def test_reset_mfa_not_enabled(self, app, db, seed_user, capsys):
        """reset_mfa() prints message when MFA is not enabled."""
```

---

### WU-7: Error Pages and Production Verification

**Goal:** Verify that existing error pages and production configuration work correctly. Add the `Retry-After` header to 429 responses. Ensure `DEBUG=False` suppresses stack traces.

**Depends on:** None (independent of other work units).

#### Pre-existing State

Error pages and handlers already exist:

- `app/templates/errors/404.html` -- "Page Not Found" with link to budget grid
- `app/templates/errors/429.html` -- "Too Many Requests" with link to login
- `app/templates/errors/500.html` -- "Something Went Wrong" with link to budget grid
- Handlers registered in `app/__init__.py:197-210`

Production configuration already sets `DEBUG = False` (`app/config.py:56`), secure cookies (lines 59-61), and validates `SECRET_KEY` and `DATABASE_URL` on instantiation (lines 63-70).

#### Files to Modify

**`app/__init__.py`** -- Modify the 429 error handler (line 204-206) to include the `Retry-After` header:

Current:
```python
@app.errorhandler(429)
def rate_limit_exceeded(e):
    return render_template("errors/429.html"), 429
```

New:
```python
@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Return the 429 error page with a Retry-After header."""
    response = app.make_response(
        (render_template("errors/429.html"), 429)
    )
    response.headers["Retry-After"] = "900"  # 15 minutes in seconds
    return response
```

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_routes/test_errors.py`** -- New test file:

```python
class TestErrorPages:
    """Tests for custom error page rendering."""

    def test_404_renders_custom_page(self, app, auth_client):
        """GET /nonexistent-path returns 404 with custom template."""

    def test_404_contains_navigation(self, app, auth_client):
        """404 page contains a link back to the budget grid."""

    def test_429_renders_custom_page(self, app, seed_user):
        """Rate-limited request returns 429 with custom template."""

    def test_429_includes_retry_after_header(self, app, seed_user):
        """429 response includes Retry-After header."""

    def test_500_renders_custom_page(self, app, client):
        """500 error returns the custom error template."""

    def test_production_debug_false(self):
        """ProdConfig has DEBUG=False."""

    def test_production_validates_secret_key(self):
        """ProdConfig raises ValueError for default secret key."""
```

---

## Complete Test Plan

### Existing Tests (no changes required)

All 769 existing tests continue to pass without modification across all 7 work units. The key reasons:

1. **MFA is opt-in.** The test user created by `seed_user` has no MfaConfig with `is_enabled=True`. The login flow only adds the MFA redirect when MFA is enabled.
2. **`session_invalidated_at` defaults to None.** The `load_user()` check skips validation when the timestamp is None.
3. **CSRF is disabled in tests.** `TestConfig` sets `WTF_CSRF_ENABLED = False`, so new CSRF tokens in templates do not affect tests.
4. **`auth_client` fixture is unchanged.** It logs in via `POST /login` with email + password. Without MFA enabled, this completes in one step, same as before.

### New Test Files and Functions

| Test File | Class | Function | WU |
|-----------|-------|----------|----|
| `tests/test_services/test_auth_service.py` | `TestChangePassword` | `test_change_password_success` | 2 |
| | | `test_change_password_wrong_current_raises` | 2 |
| | | `test_change_password_too_short_raises` | 2 |
| `tests/test_routes/test_auth.py` | `TestPasswordChange` | `test_change_password_success` | 2 |
| | | `test_change_password_wrong_current` | 2 |
| | | `test_change_password_mismatch` | 2 |
| | | `test_change_password_too_short` | 2 |
| | | `test_change_password_requires_login` | 2 |
| `tests/test_routes/test_settings.py` | `TestSettingsDashboard` | `test_settings_dashboard_security_section` | 2 |
| `tests/test_routes/test_auth.py` | `TestSessionManagement` | `test_invalidate_sessions` | 3 |
| | | `test_invalidate_sessions_current_session_survives` | 3 |
| | | `test_stale_session_rejected` | 3 |
| | | `test_password_change_invalidates_sessions` | 3 |
| | | `test_invalidate_sessions_requires_login` | 3 |
| `tests/test_services/test_mfa_service.py` | `TestGenerateSecret` | `test_generates_base32_string` | 4 |
| | | `test_generates_unique_secrets` | 4 |
| | `TestEncryptDecrypt` | `test_round_trip` | 4 |
| | | `test_encrypted_differs_from_plaintext` | 4 |
| | `TestVerifyTotpCode` | `test_valid_code_accepted` | 4 |
| | | `test_invalid_code_rejected` | 4 |
| | `TestBackupCodes` | `test_generate_backup_codes_count` | 4 |
| | | `test_generate_backup_codes_format` | 4 |
| | | `test_hash_and_verify_round_trip` | 4 |
| | | `test_verify_wrong_code_returns_negative` | 4 |
| | | `test_verify_returns_correct_index` | 4 |
| | `TestGetTotpUri` | `test_uri_format` | 4 |
| | `TestGenerateQrCode` | `test_returns_data_uri` | 4 |
| `tests/test_routes/test_auth.py` | `TestMfaSetup` | `test_mfa_setup_page_renders` | 4 |
| | | `test_mfa_setup_redirects_if_already_enabled` | 4 |
| | | `test_mfa_confirm_valid_code` | 4 |
| | | `test_mfa_confirm_invalid_code` | 4 |
| | | `test_mfa_confirm_no_session_secret` | 4 |
| | | `test_regenerate_backup_codes` | 4 |
| | | `test_regenerate_backup_codes_requires_mfa_enabled` | 4 |
| `tests/test_routes/test_auth.py` | `TestMfaLogin` | `test_login_with_mfa_redirects_to_verify` | 5 |
| | | `test_mfa_verify_page_renders` | 5 |
| | | `test_mfa_verify_no_pending_redirects_to_login` | 5 |
| | | `test_mfa_verify_valid_totp` | 5 |
| | | `test_mfa_verify_invalid_totp` | 5 |
| | | `test_mfa_verify_valid_backup_code` | 5 |
| | | `test_mfa_verify_invalid_backup_code` | 5 |
| | | `test_mfa_verify_backup_code_consumed` | 5 |
| | | `test_login_without_mfa_unchanged` | 5 |
| `tests/test_routes/test_auth.py` | `TestMfaDisable` | `test_mfa_disable_page_renders` | 6 |
| | | `test_mfa_disable_redirects_if_not_enabled` | 6 |
| | | `test_mfa_disable_success` | 6 |
| | | `test_mfa_disable_wrong_password` | 6 |
| | | `test_mfa_disable_wrong_totp` | 6 |
| `tests/test_scripts/test_reset_mfa.py` | `TestResetMfa` | `test_reset_mfa_disables_for_user` | 6 |
| | | `test_reset_mfa_user_not_found` | 6 |
| | | `test_reset_mfa_not_enabled` | 6 |
| `tests/test_routes/test_errors.py` | `TestErrorPages` | `test_404_renders_custom_page` | 7 |
| | | `test_404_contains_navigation` | 7 |
| | | `test_429_renders_custom_page` | 7 |
| | | `test_429_includes_retry_after_header` | 7 |
| | | `test_500_renders_custom_page` | 7 |
| | | `test_production_debug_false` | 7 |
| | | `test_production_validates_secret_key` | 7 |

**Total new tests: 58**

---

## Phase 8A Test Gate Checklist (Expanded)

From the Phase 8 plan, with specific test references:

- [ ] `pytest` passes (all existing 769 tests + 58 new tests)
- [ ] Every form in the app includes a CSRF token (verified by audit in this document -- 70/70, 100%)
- [ ] HTMX requests include CSRF header (verified: `app/static/js/app.js:54-60`)
- [ ] Password change works: `TestPasswordChange.test_change_password_success`, `TestChangePassword.test_change_password_success`
- [ ] Correct current password required: `TestPasswordChange.test_change_password_wrong_current`, `TestChangePassword.test_change_password_wrong_current_raises`
- [ ] New password hashed: verified in `test_change_password_success` by re-authenticating with the new password
- [ ] Sessions invalidated on password change: `TestSessionManagement.test_password_change_invalidates_sessions`
- [ ] MFA setup: QR code displayed: `TestMfaSetup.test_mfa_setup_page_renders`
- [ ] MFA setup: confirmation code validates: `TestMfaSetup.test_mfa_confirm_valid_code`
- [ ] MFA setup: backup codes generated: `TestMfaSetup.test_mfa_confirm_valid_code` (verifies backup codes are shown)
- [ ] MFA login: two-step flow works: `TestMfaLogin.test_login_with_mfa_redirects_to_verify`, `TestMfaLogin.test_mfa_verify_valid_totp`
- [ ] MFA login: backup code works: `TestMfaLogin.test_mfa_verify_valid_backup_code`
- [ ] MFA login: failed code rejected: `TestMfaLogin.test_mfa_verify_invalid_totp`
- [ ] MFA disable: requires password + TOTP: `TestMfaDisable.test_mfa_disable_success`, `TestMfaDisable.test_mfa_disable_wrong_password`, `TestMfaDisable.test_mfa_disable_wrong_totp`
- [ ] Rate limiting: 6th failed login attempt returns 429: `TestLogin.test_rate_limiting_after_5_attempts` (existing test)
- [ ] Rate limiting: MFA verify endpoint is rate limited: `TestMfaLogin` tests with the `@limiter.limit` decorator on `mfa_verify()`
- [ ] Custom error pages render for 404, 500, 429: `TestErrorPages.test_404_renders_custom_page`, `TestErrorPages.test_500_renders_custom_page`, `TestErrorPages.test_429_renders_custom_page`
- [ ] 429 response includes Retry-After header: `TestErrorPages.test_429_includes_retry_after_header`
- [ ] Manual test: `scripts/reset_mfa.py` successfully disables MFA for a user

---

## File Summary

### New Files (9)

| File | Type | WU |
|------|------|----|
| `app/services/mfa_service.py` | Service module | 4 |
| `app/templates/settings/_security.html` | Jinja2 partial | 2 |
| `app/templates/settings/_mfa_setup.html` | Jinja2 partial | 4 |
| `app/templates/auth/mfa_setup.html` | Jinja2 template | 4 |
| `app/templates/auth/mfa_backup_codes.html` | Jinja2 template | 4 |
| `app/templates/auth/mfa_verify.html` | Jinja2 template | 5 |
| `app/templates/auth/mfa_disable.html` | Jinja2 template | 6 |
| `scripts/reset_mfa.py` | CLI script | 6 |
| `migrations/versions/<hash>_add_session_invalidated_at.py` | Alembic migration | 1 |

### New Test Files (3)

| File | Tests | WU |
|------|-------|----|
| `tests/test_services/test_mfa_service.py` | 13 | 4 |
| `tests/test_scripts/test_reset_mfa.py` | 3 | 6 |
| `tests/test_routes/test_errors.py` | 7 | 7 |

### Modified Files (9)

| File | Changes | WU |
|------|---------|-----|
| `requirements.txt` | Add pyotp, qrcode[pil], cryptography | 1 |
| `app/models/user.py` | Add `session_invalidated_at` column to `User` | 1 |
| `app/services/auth_service.py` | Add `change_password()` function | 2 |
| `app/routes/auth.py` | Add password change, session invalidation, MFA setup/confirm/verify/disable routes | 2-6 |
| `app/routes/settings.py` | Add "security" to `_VALID_SECTIONS`, add data loading | 2, 4 |
| `app/templates/settings/dashboard.html` | Add Security sidebar link and content include | 2 |
| `app/__init__.py` | Update `load_user()` for session invalidation, update 429 handler | 3, 7 |
| `app/config.py` | Add `TOTP_ENCRYPTION_KEY` config | 4 |
| `app/exceptions.py` | No changes (AuthError and ValidationError already exist) | -- |

### Modified Test Files (3)

| File | Changes | WU |
|------|---------|-----|
| `tests/test_routes/test_auth.py` | Add TestPasswordChange, TestSessionManagement, TestMfaSetup, TestMfaLogin, TestMfaDisable | 2-6 |
| `tests/test_services/test_auth_service.py` | Add TestChangePassword | 2 |
| `tests/test_routes/test_settings.py` | Add `test_settings_dashboard_security_section` | 2 |
