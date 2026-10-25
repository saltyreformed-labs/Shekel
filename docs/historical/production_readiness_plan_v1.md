> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Production Readiness -- Implementation Plan

## Overview

This plan addresses all 18 findings from the Production Readiness Audit Report across 14 work units:

- **3 Blockers** (WU-01 through WU-03)
- **6 High Priority** (WU-04 through WU-08)
- **4 Medium Priority** (WU-09 through WU-12)
- **5 Low Priority** (WU-13 through WU-14)

Some related findings are combined into single work units where they touch the same files or share a logical theme. The plan is ordered strictly by priority tier, then by risk within each tier.

---

## Work Units

### WU-01: Validate `next` Parameter in Login Redirect to Prevent Open Redirect

**Findings addressed:** B-001
**Risk:** An attacker can craft a login URL that redirects authenticated users to a phishing site after successful login.
**Depends on:** None

**Files:**

- Modify: `app/routes/auth.py` (lines 10, 60-61, 50, 231)

**Steps:**

1. Add `urlparse` to the imports at line 10 of `app/routes/auth.py`:

   ```python
   from urllib.parse import urlparse
   ```

2. Add a helper function after the `auth_bp` definition (after line 21) that validates redirect targets:

   ```python
   def _is_safe_redirect(target):
       """Check that a redirect target is a safe relative URL.

       Rejects absolute URLs (with scheme or netloc) to prevent open
       redirect attacks.  Returns False for empty/None targets.
       """
       if not target:
           return False
       parsed = urlparse(target)
       return not parsed.scheme and not parsed.netloc
   ```

3. Replace lines 60-61 (standard login redirect) with:

   ```python
   next_page = request.args.get("next")
   if not _is_safe_redirect(next_page):
       next_page = None
   return redirect(next_page or url_for("grid.index"))
   ```

4. Replace line 50 (MFA pending next storage) with:

   ```python
   pending_next = request.args.get("next")
   flask_session["_mfa_pending_next"] = (
       pending_next if _is_safe_redirect(pending_next) else None
   )
   ```

5. Replace line 231 (MFA verify redirect) with:
   ```python
   next_page = flask_session.pop("_mfa_pending_next", None)
   if not _is_safe_redirect(next_page):
       next_page = None
   ```
   The existing `return redirect(next_page or url_for("grid.index"))` on line 239 remains unchanged.

**New tests:**

All tests go in `tests/test_routes/test_auth.py`:

- `TestLogin::test_open_redirect_absolute_url_blocked` -- Asserts that `POST /login?next=https://evil.com` after valid credentials redirects to the grid, not to evil.com.
- `TestLogin::test_open_redirect_protocol_relative_blocked` -- Asserts that `POST /login?next=//evil.com` after valid credentials redirects to the grid.
- `TestLogin::test_safe_next_redirect_allowed` -- Asserts that `POST /login?next=/templates` after valid credentials redirects to `/templates`.
- `TestMfaVerify::test_mfa_open_redirect_blocked` -- Asserts that after MFA verification with a pending `next` of `https://evil.com`, the redirect goes to the grid.
- `TestMfaVerify::test_mfa_safe_next_redirect_allowed` -- Asserts that after MFA verification with a pending `next` of `/templates`, the redirect goes to `/templates`.

**Verification:**

- Run: `pytest tests/test_routes/test_auth.py -v -k "open_redirect or safe_next"`
- Expected: All new tests pass.
- Manual: Start the dev server, navigate to `/login?next=https://evil.com`, log in, and confirm redirect goes to `/` (grid), not `evil.com`.

**Commit:** `fix: validate next parameter in login redirect to prevent open redirect`

---

### WU-02: Split Requirements Into Prod and Dev, Replace psycopg2-binary

**Findings addressed:** B-003, H-004
**Risk:** Production Docker image contains pytest, pylint, and factory-boy (unnecessary attack surface and image bloat). `psycopg2-binary` may have version mismatches with the system libpq.
**Depends on:** None

**Files:**

- Modify: `requirements.txt` (remove dev/test/lint packages, replace `psycopg2-binary` with `psycopg2`)
- Create: `requirements-dev.txt` (dev/test/lint packages that reference `requirements.txt`)
- Modify: `Dockerfile` (line 16 -- already installs `libpq-dev` and `gcc` in builder, so `psycopg2` will compile)
- Modify: `.github/workflows/ci.yml` (line 77 -- install both files)

**Steps:**

1. Rewrite `requirements.txt` to contain only production dependencies. Replace `psycopg2-binary==2.9.11` with `psycopg2==2.9.11`:

   ```
   # Shekel Budget App - Production Dependencies
   # Flask core
   Flask==3.1.3
   Flask-Limiter==4.1.1
   Flask-Login==0.6.3
   Flask-SQLAlchemy==3.1.1
   Flask-Migrate==4.1.0
   Flask-WTF==1.2.2

   # Database
   SQLAlchemy==2.0.48
   psycopg2==2.9.11
   alembic==1.18.4

   # Authentication
   bcrypt==5.0.0
   pyotp==2.9.0
   qrcode[pil]==8.2
   cryptography==46.0.5

   # Input validation
   marshmallow==3.26.2

   # Environment
   python-dotenv==1.2.2

   # Logging
   python-json-logger==4.0.0
   ```

2. Create `requirements-dev.txt`:

   ```
   # Shekel Budget App - Development & Testing Dependencies
   # Includes all production dependencies.
   -r requirements.txt

   # Testing
   pytest==9.0.2
   pytest-flask==1.3.0
   pytest-cov==7.0.0
   pytest-timeout==2.4.0
   factory-boy==3.3.3

   # Linting
   pylint==4.0.5
   pylint-flask==0.6
   pylint-flask-sqlalchemy==0.2.0
   ```

3. The `Dockerfile` line 16 (`RUN pip install --no-cache-dir -r requirements.txt`) requires no change -- it already references `requirements.txt`, which now contains only production dependencies. The builder stage already installs `libpq-dev` and `gcc` (line 9), so `psycopg2` will compile from source successfully.

4. Update `.github/workflows/ci.yml` lines 74-77. Change the install step to:

   ```yaml
   - name: Install dependencies
     run: |
       python -m pip install --upgrade pip
       pip install -r requirements-dev.txt
   ```

5. Update the pip cache key on line 69 of `ci.yml` to hash both files:
   ```yaml
   key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt', 'requirements-dev.txt') }}
   ```

**New tests:**
No new application tests required. This is a packaging change.

**Verification:**

- Run: `pip install -r requirements-dev.txt` (in local venv) -- confirms both files install correctly.
- Run: `timeout 660 pytest -v --tb=short` -- confirms full test suite passes with `psycopg2` instead of `psycopg2-binary`.
- Build Docker image: `docker build -t shekel-test .` -- confirms production image builds without dev dependencies.
- Verify no dev packages in image: `docker run --rm shekel-test pip list | grep -i pytest` -- should return nothing.

**Commit:** `fix: split requirements into prod and dev, replace psycopg2-binary with psycopg2`

---

### WU-03: Fix External Docker Network and Add Seed User to Entrypoint

**Findings addressed:** B-002, H-003
**Risk:** `docker compose up` fails on a fresh host because the `monitoring` network does not exist. Fresh deployments create a database with no user, making the app unusable.
**Depends on:** None

**Files:**

- Modify: `docker-compose.yml` (lines 54-56, 122-124)
- Modify: `entrypoint.sh` (add seed user step after line 29)
- Modify: `.env.example` (line 48 -- update default seed password)

**Steps:**

1. In `docker-compose.yml`, replace lines 119-124 (the `monitoring` network definition) with:

   ```yaml
   # Monitoring network: shared with the Loki/Grafana/Promtail stack.
   # Uses an explicit name so both stacks reference the same network.
   # Docker Compose creates it automatically if it does not exist.
   monitoring:
     name: monitoring
     driver: bridge
   ```

   This removes `external: true` so Docker Compose creates the network if it does not exist, and sets an explicit `name` to prevent project-name prefixing.

2. In `entrypoint.sh`, add a new step between the current step 5 (seed tax brackets, line 29) and step 6 (copy static files, line 31). Insert after line 29 (`echo "Seeding complete."`):

   ```bash
   # ── 6. Seed initial user (first run only) ─────────────────────
   # Only runs if SEED_USER_EMAIL is set and non-empty.
   # seed_user.py is idempotent -- skips if the user already exists.
   if [ -n "${SEED_USER_EMAIL}" ]; then
       echo "Seeding initial user..."
       python scripts/seed_user.py
       echo "User seeding complete."
   else
       echo "SEED_USER_EMAIL not set, skipping user seed."
   fi
   ```

   Renumber subsequent step comments (old step 6 becomes step 7, old step 7 becomes step 8).

3. In `.env.example`, update line 48 to use a password that meets the 12-character minimum. Change:
   ```
   SEED_USER_PASSWORD=changeme
   ```
   to:
   ```
   SEED_USER_PASSWORD=ChangeMe!2026
   ```
   Also update line 45 comment to remove the inaccurate claim that entrypoint creates the user "automatically":
   ```
   # Set these before first `docker compose up`. The initial user is
   # created by entrypoint.sh when SEED_USER_EMAIL is set.
   ```

**New tests:**
No new application tests required. Docker entrypoint changes are verified via manual deployment testing.

**Verification:**

- Create a fresh Docker network test: `docker network ls | grep monitoring && docker network rm monitoring || true` -- remove existing monitoring network.
- Run: `docker compose up -d` -- should succeed without pre-creating the monitoring network.
- Verify user creation: After containers are healthy, check logs: `docker logs shekel-app 2>&1 | grep "Seeding initial user"` -- should show user creation messages.
- Verify idempotency: `docker compose restart app` -- logs should show "already exists. Skipping."
- Verify no-seed behavior: Set `SEED_USER_EMAIL=` (empty) in `.env`, restart. Logs should show "SEED_USER_EMAIL not set, skipping user seed."

**Commit:** `fix: remove external network requirement and add seed user to entrypoint`

---

### WU-04: Add 400 and 403 Error Handlers

**Findings addressed:** H-001
**Risk:** CSRF failures and permission denials show raw Werkzeug error pages instead of styled app pages, confusing users and leaking framework details.
**Depends on:** None

**Files:**

- Modify: `app/__init__.py` (lines 236-255 -- the `_register_error_handlers` function)
- Create: `app/templates/errors/400.html`
- Create: `app/templates/errors/403.html`

**Steps:**

1. Create `app/templates/errors/400.html` following the existing error page pattern (matching 404.html style):

   ```html
   {% extends "base.html" %} {% block title %}Bad Request -- Shekel{% endblock %}
   {% block content %}
   <div class="row justify-content-center mt-5">
     <div class="col-md-6 text-center">
       <h1 class="display-1 text-muted">400</h1>
       <h4>Bad Request</h4>
       <p class="text-muted">
         The request could not be processed. Please try again.
       </p>
       <a href="{{ url_for('grid.index') }}" class="btn btn-primary mt-3">
         <i class="bi bi-grid-3x3"></i> Back to Budget Grid
       </a>
     </div>
   </div>
   {% endblock %}
   ```

2. Create `app/templates/errors/403.html`:

   ```html
   {% extends "base.html" %} {% block title %}Forbidden -- Shekel{% endblock %}
   {% block content %}
   <div class="row justify-content-center mt-5">
     <div class="col-md-6 text-center">
       <h1 class="display-1 text-muted">403</h1>
       <h4>Access Denied</h4>
       <p class="text-muted">You do not have permission to access this page.</p>
       <a href="{{ url_for('grid.index') }}" class="btn btn-primary mt-3">
         <i class="bi bi-grid-3x3"></i> Back to Budget Grid
       </a>
     </div>
   </div>
   {% endblock %}
   ```

3. In `app/__init__.py`, add 400 and 403 handlers inside `_register_error_handlers` (insert before the existing 404 handler at line 239):

   ```python
   @app.errorhandler(400)
   def bad_request(e):
       return render_template("errors/400.html"), 400

   @app.errorhandler(403)
   def forbidden(e):
       return render_template("errors/403.html"), 403
   ```

**New tests:**

All tests go in `tests/test_routes/test_auth.py` (or a new `tests/test_routes/test_error_handlers.py` if the file is already large):

- `TestErrorHandlers::test_400_renders_custom_page` -- Sends a request that triggers a 400 (e.g., malformed form data or CSRF failure) and asserts the response contains "Bad Request" and status 400.
- `TestErrorHandlers::test_403_renders_custom_page` -- Uses `app.test_client()` to hit a route that returns `abort(403)` and asserts the response contains "Access Denied" and status 403.

Note: Since TestConfig has `WTF_CSRF_ENABLED = False`, triggering a real CSRF 400 in tests requires either enabling CSRF for that test or manually calling `abort(400)`. The simplest approach is to add a temporary test route via `app.add_url_rule` in the test fixture that calls `abort(400)` and `abort(403)`.

**Verification:**

- Run: `pytest tests/test_routes/test_error_handlers.py -v`
- Expected: Both tests pass.
- Manual: Start the dev server, navigate to a non-existent URL -- 404 page renders. Verify similar styling consistency for 400/403 by testing with CSRF disabled in browser (e.g., blocking JS).

**Commit:** `fix: add custom 400 and 403 error handlers`

---

### WU-05: Add "settled" Status to Test Conftest Ref Data

**Findings addressed:** H-002
**Risk:** Tests run against a different reference data set than production. Tests that interact with the "settled" status would fail or silently skip behavior.
**Depends on:** None

**Files:**

- Modify: `tests/conftest.py` (line 896)

**Steps:**

1. In `tests/conftest.py`, change line 896 from:
   ```python
   (Status, ["projected", "done", "received", "credit", "cancelled"]),
   ```
   to:
   ```python
   (Status, ["projected", "done", "received", "credit", "cancelled", "settled"]),
   ```
   This matches the production `_seed_ref_tables()` in `app/__init__.py` line 313.

**New tests:**
No new tests required. This fixes existing test infrastructure to match production. Existing tests that reference status values will now have access to "settled."

**Verification:**

- Run: `timeout 660 pytest -v --tb=short` -- full suite passes with the updated ref data.
- Grep to confirm alignment: `grep -n "settled" app/__init__.py tests/conftest.py` -- both files should list "settled" in their Status arrays.

**Commit:** `fix: add settled status to test conftest ref data to match production`

---

### WU-06: Fix Amortization Engine Double Decimal Conversion

**Findings addressed:** H-005
**Risk:** Unnecessary `Decimal(str(payment))` conversion on a value that is already Decimal. While currently harmless, it obscures intent and could mask type errors if the upstream expression changes.
**Depends on:** None

**Files:**

- Modify: `app/services/amortization_engine.py` (line 77)

**Steps:**

1. In `app/services/amortization_engine.py`, replace line 77:
   ```python
   return Decimal(str(payment)).quantize(TWO_PLACES, ROUND_HALF_UP)
   ```
   with:
   ```python
   return payment.quantize(TWO_PLACES, ROUND_HALF_UP)
   ```
   The variable `payment` on line 76 is already a `Decimal` (computed from `principal * (monthly_rate * factor) / (factor - 1)` where all operands are `Decimal`). The `Decimal(str(...))` wrapper is a no-op double conversion.

**New tests:**
No new tests required. The existing `tests/test_services/test_amortization_engine.py` covers this function. The change is a pure simplification with identical output.

**Verification:**

- Run: `pytest tests/test_services/test_amortization_engine.py -v` -- all existing tests pass.

**Commit:** `fix: remove unnecessary Decimal(str()) double conversion in amortization engine`

---

### WU-07: Add CSRF Hidden Inputs to Salary Delete Forms

**Findings addressed:** H-006
**Risk:** If JavaScript fails to load, the salary raise/deduction delete forms submit via standard POST without a CSRF token. Flask-WTF rejects the request with a 400 error. The HTMX path works because `app.js` injects the token as a header, but this provides no graceful degradation.
**Depends on:** WU-04 (so the 400 error page exists if CSRF still fails for other reasons)

**Files:**

- Modify: `app/templates/salary/_raises_section.html` (line 57, inside the delete form)
- Modify: `app/templates/salary/_deductions_section.html` (line 58, inside the delete form)

**Steps:**

1. In `app/templates/salary/_raises_section.html`, add a CSRF hidden input inside the delete form. Insert after line 57 (`hx-disabled-elt="find button[type=submit]">`):

   ```html
   <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
   ```

   The complete form block (lines 52-61) becomes:

   ```html
   <form
     method="POST"
     action="{{ url_for('salary.delete_raise', raise_id=r.id) }}"
     hx-post="{{ url_for('salary.delete_raise', raise_id=r.id) }}"
     hx-target="#raises-section"
     hx-swap="outerHTML"
     hx-disabled-elt="find button[type=submit]"
   >
     <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
     <button type="submit" class="btn btn-sm btn-outline-danger" title="Remove">
       <i class="bi bi-trash"></i>
     </button>
   </form>
   ```

2. In `app/templates/salary/_deductions_section.html`, add the same CSRF hidden input inside the delete form. Insert after line 58 (`hx-disabled-elt="find button[type=submit]">`):
   ```html
   <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
   ```
   The complete form block (lines 53-62) becomes:
   ```html
   <form
     method="POST"
     action="{{ url_for('salary.delete_deduction', ded_id=d.id) }}"
     hx-post="{{ url_for('salary.delete_deduction', ded_id=d.id) }}"
     hx-target="#deductions-section"
     hx-swap="outerHTML"
     hx-disabled-elt="find button[type=submit]"
   >
     <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
     <button type="submit" class="btn btn-sm btn-outline-danger" title="Remove">
       <i class="bi bi-trash"></i>
     </button>
   </form>
   ```

**New tests:**
No new Python tests required. CSRF validation is disabled in the test environment (`WTF_CSRF_ENABLED = False`), and the existing salary route tests already cover the delete functionality. The fix is a template-only change for graceful degradation.

**Verification:**

- Run: `pytest tests/test_routes/test_salary.py -v` -- all existing salary tests pass.
- Manual: With dev server running, inspect the delete raise/deduction forms in browser DevTools. Confirm each form contains a hidden `csrf_token` input.
- Manual: Disable JavaScript in browser, attempt to delete a raise. Confirm the form submits successfully via standard POST (the CSRF token is in the form body).

**Commit:** `fix: add CSRF hidden inputs to salary delete forms for graceful degradation`

---

### WU-08: Add Missing `TestMfaVerify` Class Tests for Open Redirect (Stub)

**Findings addressed:** (Covered by WU-01, but the MFA verify test class may need to be created)
**Note:** This work unit exists only if `TestMfaVerify` does not already exist in `test_auth.py`. If it does, the tests from WU-01 are added to the existing class. Verify during WU-01 implementation.

This is absorbed into WU-01. See WU-01 for MFA verify test details.

---

### WU-09: Isolate `float()` Conversions at Presentation Boundary

**Findings addressed:** M-001
**Risk:** No financial harm (conversions happen at the Chart.js serialization boundary), but the undocumented pattern could lead a future developer to use `float()` in calculation code.
**Depends on:** None

**Files:**

- Modify: `app/services/chart_data_service.py` (add module-level helper, update lines 307, 389, 441-442, 536-537, 691-692, 707-708)
- Modify: `app/routes/auto_loan.py` (line 54)
- Modify: `app/routes/mortgage.py` (line 64)
- Modify: `app/routes/retirement.py` (lines 319, 337)

**Steps:**

1. In `app/services/chart_data_service.py`, add a module-level helper function near the top of the file (after imports):

   ```python
   def _to_chart_float(value):
       """Convert a Decimal to float for Chart.js JSON serialization.

       Chart.js requires native Python floats for numeric datasets.
       This conversion is safe because it happens at the presentation
       boundary -- no further arithmetic is performed on these values.
       All financial calculations use Decimal exclusively.
       """
       return float(value)
   ```

2. Replace all `float(...)` calls in `chart_data_service.py` with `_to_chart_float(...)`:
   - Line 307: `float(balances.get(p.id, Decimal("0")))` -> `_to_chart_float(balances.get(p.id, Decimal("0")))`
   - Line 389: `float(g[1])` -> `_to_chart_float(g[1])`
   - Lines 441-442: `float(estimated_totals.get(...))` -> `_to_chart_float(estimated_totals.get(...))`
   - Lines 536-537: `float(row.principal)` / `float(row.interest)` -> `_to_chart_float(row.principal)` / `_to_chart_float(row.interest)`
   - Lines 691-692: `float(breakdown.net_pay)` / `float(breakdown.gross_biweekly)` -> `_to_chart_float(breakdown.net_pay)` / `_to_chart_float(breakdown.gross_biweekly)`
   - Lines 707-708: `float(breakdowns[-1].net_pay)` / `float(breakdowns[-1].gross_biweekly)` -> `_to_chart_float(breakdowns[-1].net_pay)` / `_to_chart_float(breakdowns[-1].gross_biweekly)`

3. In the route files, add inline comments explaining the `float()` usage rather than importing the helper (these are one-off occurrences):
   - `app/routes/auto_loan.py` line 54: Add comment above:
     ```python
     # float() for Chart.js serialization -- presentation only, no arithmetic.
     balances.append(float(row.remaining_balance))
     ```
   - `app/routes/mortgage.py` line 64: Same comment pattern.
   - `app/routes/retirement.py` lines 319, 337: Same comment pattern.

**New tests:**
No new tests required. The helper is a trivial wrapper and the existing chart/route tests cover the serialization paths. The change is behavioral no-op.

**Verification:**

- Run: `pytest tests/test_services/test_chart_data_service.py -v` -- all existing tests pass (if this test file exists).
- Run: `pytest tests/test_routes/test_mortgage.py tests/test_routes/test_auto_loan.py tests/test_routes/test_retirement.py -v` -- all pass.
- Run: `pylint app/services/chart_data_service.py app/routes/auto_loan.py app/routes/mortgage.py app/routes/retirement.py` -- no new lint errors.

**Commit:** `refactor: isolate float() conversions at Chart.js presentation boundary`

---

### WU-10: Add Dedicated carry_forward_service Tests

**Findings addressed:** M-002
**Risk:** Edge cases in carry-forward logic (same-period carry, template override flagging, cross-user rejection) are not explicitly tested and could regress silently.
**Depends on:** None

**Files:**

- Create: `tests/test_services/test_carry_forward_service.py`

**Steps:**

1. Create `tests/test_services/test_carry_forward_service.py` with the following test class and methods. Tests use fixtures from `conftest.py` (`app`, `db`, `seed_user`, `second_user`):

   ```python
   """
   Shekel Budget App -- Carry Forward Service Tests

   Dedicated unit tests for carry_forward_unpaid() covering ownership
   verification, edge cases, and template override flagging.
   """

   from app.extensions import db
   from app.models.pay_period import PayPeriod
   from app.models.transaction import Transaction
   from app.models.ref import Status, TransactionType
   from app.services.carry_forward_service import carry_forward_unpaid
   from app.exceptions import NotFoundError
   import pytest
   from datetime import date
   ```

   Test class `TestCarryForwardUnpaid`:
   - `test_moves_projected_transactions` -- Creates two pay periods and a projected transaction in the source. Calls `carry_forward_unpaid`. Asserts the transaction's `pay_period_id` is now the target, and return value is 1.

   - `test_skips_done_transactions` -- Creates a transaction with status "done" in the source period. Calls carry forward. Asserts return value is 0 and the transaction remains in the source period.

   - `test_skips_cancelled_transactions` -- Same as above but with "cancelled" status.

   - `test_skips_credit_transactions` -- Same as above but with "credit" status.

   - `test_same_period_returns_zero` -- Calls `carry_forward_unpaid` with `source_period_id == target_period_id`. Asserts return value is 0.

   - `test_template_linked_flagged_as_override` -- Creates a transaction with a non-null `template_id` in the source. After carry forward, asserts `txn.is_override` is True.

   - `test_non_template_not_flagged_as_override` -- Creates a transaction with `template_id=None`. After carry forward, asserts `txn.is_override` remains False (or unchanged).

   - `test_source_period_wrong_user_raises` -- Creates a period owned by `seed_user` and attempts carry forward with `second_user`'s ID. Asserts `NotFoundError` is raised.

   - `test_target_period_wrong_user_raises` -- Source period belongs to user, target period belongs to `second_user`. Asserts `NotFoundError`.

   - `test_nonexistent_source_raises` -- Calls with `source_period_id=99999`. Asserts `NotFoundError`.

   - `test_nonexistent_target_raises` -- Calls with valid source but `target_period_id=99999`. Asserts `NotFoundError`.

   - `test_skips_deleted_transactions` -- Creates a projected transaction with `is_deleted=True`. Asserts it is not moved.

   - `test_multiple_transactions_moved` -- Creates 3 projected transactions in the source. Asserts return value is 3 and all three are in the target period.

**Verification:**

- Run: `pytest tests/test_services/test_carry_forward_service.py -v`
- Expected: All 13 tests pass.

**Commit:** `test: add dedicated carry_forward_service unit tests`

---

### WU-11: Add DevConfig DATABASE_URL Fallback with Clear Error

**Findings addressed:** M-003
**Risk:** If `DATABASE_URL` is not set in `.env`, `DevConfig.SQLALCHEMY_DATABASE_URI` is `None`, and SQLAlchemy raises an unclear error at connection time.
**Depends on:** None

**Files:**

- Modify: `app/config.py` (line 52)

**Steps:**

1. In `app/config.py`, replace line 52:
   ```python
   SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
   ```
   with:
   ```python
   SQLALCHEMY_DATABASE_URI = os.getenv(
       "DATABASE_URL", "postgresql:///shekel"
   )
   ```
   This provides a sensible fallback for local development using PostgreSQL peer authentication (Unix socket, default database name). Developers who use password auth will still set `DATABASE_URL` in `.env`. This mirrors the pattern shown in the README (lines 47-49).

**New tests:**

- `tests/test_config.py::TestDevConfig::test_database_uri_fallback` -- Temporarily unsets `DATABASE_URL` env var (using `monkeypatch`), instantiates `DevConfig`, and asserts `SQLALCHEMY_DATABASE_URI == "postgresql:///shekel"`.

Note: If `tests/test_config.py` does not exist, create it.

**Verification:**

- Run: `pytest tests/test_config.py -v` (if created).
- Manual: Temporarily remove `DATABASE_URL` from `.env`, run `flask run` -- app should attempt to connect to local `shekel` database via peer auth instead of crashing with a `None` URI error.

**Commit:** `fix: add DevConfig DATABASE_URL fallback for local development`

---

### WU-12: Enforce Minimum Password Length in Seed User Script

**Findings addressed:** M-004
**Risk:** Default seed password `changeme` (8 chars) is below the app's 12-character minimum. A user who keeps this password cannot change it through the UI (which enforces the minimum), and it sets a poor security baseline.
**Depends on:** WU-03 (which updates the `.env.example` default password)

**Files:**

- Modify: `scripts/seed_user.py` (lines 35, 47)

**Steps:**

1. In `scripts/seed_user.py`, change line 35 default password from `"changeme"` to `"ChangeMe!2026"`:

   ```python
   password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")
   ```

2. Add password length validation after line 36 (after `display_name` assignment). Insert:

   ```python
   # Enforce the same minimum as the registration flow.
   if len(password) < 12:
       print(f"Error: SEED_USER_PASSWORD must be at least 12 characters (got {len(password)}).")
       print("Set SEED_USER_PASSWORD in .env or environment.")
       sys.exit(1)
   ```

3. Line 47 (`password_hash=hash_password(password)`) remains unchanged -- `hash_password` handles the bcrypt encoding.

**New tests:**
No new automated tests. The seed script is a CLI tool, not importable library code. The validation is tested via the manual verification step.

**Verification:**

- Run: `SEED_USER_PASSWORD=short python scripts/seed_user.py` -- should print error and exit with code 1.
- Run: `SEED_USER_PASSWORD=ValidPassword12 python scripts/seed_user.py` -- should succeed (or print "already exists" if user exists).

**Commit:** `fix: enforce 12-character minimum password in seed user script`

---

### WU-13: Update MfaConfig Model Docstring

**Findings addressed:** L-001
**Risk:** Misleading docstring says "Stub table for Phase 6+ MFA/TOTP feature. Schema only -- no logic yet." MFA is fully implemented. A developer reading this would incorrectly think MFA is not functional.
**Depends on:** None

**Files:**

- Modify: `app/models/user.py` (line 92)

**Steps:**

1. In `app/models/user.py`, replace line 92:
   ```python
   """Stub table for Phase 6+ MFA/TOTP feature.  Schema only -- no logic yet."""
   ```
   with:
   ```python
   """MFA/TOTP configuration for a user.  Stores encrypted TOTP secrets and backup codes."""
   ```

**New tests:**
No tests required. This is a docstring-only change.

**Verification:**

- Run: `pylint app/models/user.py` -- no lint errors.

**Commit:** `docs: update MfaConfig docstring to reflect implemented state`

---

### WU-14: Replace f-string SQL with Parameterized Patterns and Add Documentation Comments

**Findings addressed:** L-002, L-003, L-004, L-005
**Risk:** L-002/L-003: f-string SQL patterns are a bad example even though values are hardcoded. A copy-paste to user-controlled context would create SQL injection. L-004/L-005: Undocumented architectural decisions may confuse future maintainers.
**Depends on:** None

**Files:**

- Modify: `app/__init__.py` (lines 283-284)
- Modify: `tests/conftest.py` (lines 107-108)
- Modify: `gunicorn.conf.py` (line 74 -- add comment)
- Modify: `docker-compose.yml` (line 52 -- add comment)

**Steps:**

1. In `app/__init__.py`, replace lines 282-285 (the `_ensure_schemas` loop body). The schema names are a hardcoded tuple, so they cannot be parameterized via `db.text()` bind parameters (DDL does not support parameterized identifiers). Instead, validate the names against an allowlist. Replace:

   ```python
   for schema_name in ("ref", "auth", "budget", "salary", "system"):
       db.session.execute(
           db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
       )
   ```

   with:

   ```python
   _ALLOWED_SCHEMAS = frozenset({"ref", "auth", "budget", "salary", "system"})

   for schema_name in _ALLOWED_SCHEMAS:
       # DDL identifiers cannot use bind parameters.  Schema names are
       # validated against a hardcoded allowlist above.
       db.session.execute(
           db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
       )
   ```

   Move `_ALLOWED_SCHEMAS` to module level (above `_ensure_schemas`) so the allowlist is visible and auditable. The f-string remains because PostgreSQL DDL does not support bind parameters for identifiers, but the comment explains why it is safe.

2. In `tests/conftest.py`, apply the same pattern to lines 106-109. Replace:

   ```python
   for schema_name in ("ref", "auth", "budget", "salary", "system"):
       _db.session.execute(
           _db.text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
       )
   ```

   with:

   ```python
   for schema_name in ("ref", "auth", "budget", "salary", "system"):
       # DDL identifiers cannot use bind parameters.  Schema names
       # are from a hardcoded tuple -- not user input.
       _db.session.execute(
           _db.text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
       )
   ```

3. In `gunicorn.conf.py`, expand the comment at line 71-74 to document why `*` is acceptable:

   ```python
   # ── Forwarded Headers ────────────────────────────────────────────
   # Trust X-Forwarded-* headers from any source.  This is safe in the
   # current architecture because Gunicorn is only reachable on the Docker
   # internal backend network -- Nginx is the sole client.  If Gunicorn is
   # ever exposed directly, restrict this to the Nginx container IP.
   forwarded_allow_ips = "*"
   ```

4. In `docker-compose.yml`, add a comment at line 52 to document the `DB_PASSWORD` coupling:
   ```yaml
   # DB_PASSWORD must match POSTGRES_PASSWORD above. Used by
   # entrypoint.sh (PGPASSWORD) to create schemas and run seeds.
   DB_PASSWORD: ${POSTGRES_PASSWORD}
   ```

**New tests:**
No new tests required. These are comment and documentation changes.

**Verification:**

- Run: `pylint app/__init__.py` -- no lint errors from the module-level constant.
- Run: `timeout 660 pytest -v --tb=short` -- full suite passes.
- Read: Review comments in `gunicorn.conf.py` and `docker-compose.yml` for clarity.

**Commit:** `docs: document f-string SQL safety, Gunicorn forwarded_allow_ips, and DB_PASSWORD coupling`

---

## Final Integration Verification

After all 14 work units are complete, perform these end-to-end checks:

### 1. Full Test Suite

```bash
source venv/bin/activate
timeout 660 pytest -v --tb=short
```

Expected: All tests pass (existing + new tests from WU-01, WU-04, WU-10, WU-11).

### 2. Lint

```bash
pylint app/
```

Expected: No errors (E) or fatal (F) messages.

### 3. Docker Build from Clean Clone

```bash
# From a clean directory (or CI):
git clone <repo> /tmp/shekel-test && cd /tmp/shekel-test
docker build -t shekel-verify .
# Verify no dev packages:
docker run --rm shekel-verify pip list | grep -iE "pytest|pylint|factory"
# Should return empty.
```

### 4. Docker Compose Up on Fresh Host

```bash
# Remove any pre-existing monitoring network:
docker network rm monitoring 2>/dev/null || true
# Copy .env.example to .env and set required values:
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, SECRET_KEY, TOTP_ENCRYPTION_KEY, SEED_USER_EMAIL, SEED_USER_PASSWORD
docker compose up -d
# Wait for health checks:
docker compose ps  # all services should show "healthy"
```

### 5. Seed User Verification

```bash
docker logs shekel-app 2>&1 | grep -E "Created user|already exists"
# Should show either "Created user: admin@shekel.local" or "already exists"
```

### 6. Login Flow Verification

- Open `http://localhost` (or configured `NGINX_PORT`).
- Log in with the seed user credentials from `.env`.
- Confirm redirect goes to the budget grid.
- Navigate to `/login?next=https://evil.com`, log in again -- confirm redirect goes to grid, NOT evil.com.

### 7. MFA Flow Verification

- Go to Settings > Security > Enable MFA.
- Scan QR code with authenticator app, enter code.
- Log out, log back in -- verify MFA prompt appears.
- Enter valid TOTP code -- verify redirect to grid.

### 8. Health Endpoint

```bash
curl -s http://localhost/health | python -m json.tool
# Should return {"status": "healthy", ...}
```

### 9. Error Page Verification

- Navigate to `http://localhost/nonexistent-page` -- should show styled 404 page.
- Confirm 400 and 403 pages exist by checking templates: `ls app/templates/errors/`
  - Should list: `400.html`, `403.html`, `404.html`, `429.html`, `500.html`

---

## Traceability Matrix

| Finding | Work Unit | Description                                        |
| ------- | --------- | -------------------------------------------------- |
| B-001   | WU-01     | Open redirect in login flow                        |
| B-002   | WU-03     | External Docker network blocks fresh deployment    |
| B-003   | WU-02     | Dev/test dependencies in production image          |
| H-001   | WU-04     | Missing 400 and 403 error handlers                 |
| H-002   | WU-05     | Test/production ref data mismatch (settled status) |
| H-003   | WU-03     | No seed user created by entrypoint                 |
| H-004   | WU-02     | psycopg2-binary in production                      |
| H-005   | WU-06     | Amortization engine double Decimal conversion      |
| H-006   | WU-07     | CSRF tokens missing from salary delete forms       |
| M-001   | WU-09     | float() conversions in chart data service          |
| M-002   | WU-10     | No carry_forward_service dedicated tests           |
| M-003   | WU-11     | DevConfig missing DATABASE_URL fallback            |
| M-004   | WU-12     | Seed user password below minimum                   |
| L-001   | WU-13     | MfaConfig model docstring says "Stub"              |
| L-002   | WU-14     | f-string SQL in \_ensure_schemas                   |
| L-003   | WU-14     | f-string SQL in conftest                           |
| L-004   | WU-14     | forwarded_allow_ips = "\*" undocumented            |
| L-005   | WU-14     | DB_PASSWORD coupling undocumented                  |

All 18 findings are accounted for across 13 active work units (WU-08 is absorbed into WU-01).
