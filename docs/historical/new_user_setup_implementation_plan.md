> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# New-User Setup -- Implementation Plan

**Based on:** `docs/new_user_setup_audit.md` (2026-03-21)
**Goal:** A non-technical user can go from the GitHub repo page to a working Shekel instance using only Docker, without cloning the repo or running Python commands.

---

## Traceability Matrix

Every audit finding maps to at least one work unit.

| Audit Finding | Work Unit(s) | Phase |
|---|---|---|
| C-001 No Docker Quick Start in README | WU-07 | 2 |
| C-002 docker-compose.yml uses `build: .` | WU-01, WU-02, WU-03 | 1 |
| C-003 Entrypoint ordering bug | WU-04, WU-05 | 1 |
| C-004 TOTP key generation requires Python | WU-06, WU-09 | 1, 2 |
| M-001 Port inconsistency (80 vs 5000) | WU-07 | 2 |
| M-002 SECRET_KEY generation requires Python | WU-09 | 2 |
| M-003 No update path documented | WU-07 | 2 |
| M-004 Monitoring network unexplained | WU-01 | 1 |
| M-005 `set -e` kills container on seed failure | WU-10 | 2 |
| M-006 TOTP key marked REQUIRED but optional for initial setup | WU-06 | 1 |
| M-007 no_setup.html misleading for Docker users | WU-11 | 2 |
| N-001 Stale counts in README | WU-12 | 3 |
| N-002 Personal email in reset_mfa.py | WU-13 | 3 |
| N-003 Old credentials in design docs | WU-13 | 3 |
| N-004 Hardcoded credentials in alembic.ini | WU-14 | 3 |
| N-005 Dockerfile uses Python 3.14-slim | WU-15 | 3 |
| D-001 README has no Docker section | WU-07 | 2 |
| D-002 No compose file references GHCR | WU-01 | 1 |
| D-003 Entrypoint ordering contradicts design doc | WU-04 | 1 |
| D-004 Update comment says `build` not `pull` | WU-01 | 1 |
| D-005 Template count stale | WU-12 | 3 |
| D-006 Test file count stale | WU-12 | 3 |
| D-007 TOTP key "REQUIRED" vs actually optional | WU-06, WU-09 | 1, 2 |
| D-008 Entrypoint comment claims parity between seed and register | WU-05 | 1 |

---

## Phase 1 -- Fix Critical Blockers

These changes unblock the Docker setup path entirely. They are tightly coupled and should be landed together in a single branch.

---

### WU-01: Create end-user docker-compose.yml with GHCR image

**Audit findings addressed:** C-002, M-004, D-002, D-004

**Problem:** `docker-compose.yml` uses `build: .` requiring the full source tree. End users who download only this file cannot run `docker compose up`. The CI workflow publishes to `ghcr.io/saltyreformed/shekel:latest` but no compose file references it.

**Approach:** Convert the existing `docker-compose.yml` to reference the GHCR image. Create a small `docker-compose.build.yml` override for self-hosted deployments that need to build from source (used by `deploy.sh`).

**Files to modify:**

1. **`docker-compose.yml`** -- The end-user-facing compose file.

   Changes:
   - Line 40: Replace `build: .` with `image: ghcr.io/saltyreformed/shekel:latest`
   - Lines 14-16: Update the quick-start comment block. Replace:
     ```
     # Update:
     #   docker compose build && docker compose up -d
     ```
     with:
     ```
     # Update:
     #   docker compose pull && docker compose up -d
     ```
   - Lines 69-71: Remove the `monitoring` network from the `app` service's `networks:` list. End users don't have a Loki/Grafana stack.
   - Lines 122-127: Remove the `monitoring` network definition entirely from the `networks:` section.

   Resulting `app` service networks: `backend` only (Nginx handles external access via `frontend`).

2. **`docker-compose.build.yml`** -- New file. Override for self-hosted deployments that build from source.

   ```yaml
   # Shekel Budget App -- Build Override
   #
   # Use with docker-compose.yml when building from source:
   #   docker compose -f docker-compose.yml -f docker-compose.build.yml up -d
   #
   # End users pulling from GHCR do not need this file.

   services:
     app:
       build: .
       # Reconnect to monitoring network for self-hosted deployments
       # with the Loki/Grafana/Promtail stack.
       networks:
         - backend
         - monitoring

   networks:
     monitoring:
       name: monitoring
       driver: bridge
   ```

3. **`scripts/deploy.sh`** -- Update to use the build override.

   Changes at lines 188 and 205:
   - Line 188: Change `docker compose build app` to:
     ```bash
     docker compose -f docker-compose.yml -f docker-compose.build.yml build app
     ```
   - Line 205: Change `docker compose up -d --no-deps --force-recreate app` to:
     ```bash
     docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --no-deps --force-recreate app
     ```
   - Line 270: Change `docker compose stop app` to use the same `-f` flags.
   - Line 275-276: Change `docker compose images app` to use the same `-f` flags.
   - Line 286: Change `docker compose up -d --no-deps --force-recreate app` (rollback) to use the same `-f` flags.
   - Add a variable near the top (after line 41) for DRY:
     ```bash
     COMPOSE_FILES="-f docker-compose.yml -f docker-compose.build.yml"
     ```
     Then use `docker compose ${COMPOSE_FILES} ...` everywhere.

**Dependency check:** `nginx/nginx.conf` references `app:8000` by Docker DNS name -- this is the service name, not the container name, so it's unaffected by the image change. Backup/restore scripts use container names (`shekel-db`, `shekel-app`) which are unchanged. `monitoring/promtail-config.yml` filters by `com.docker.compose.service=app` -- unaffected.

**GHCR visibility prerequisite:** The GHCR package `ghcr.io/saltyreformed/shekel` must be set to **public** visibility on GitHub. This is a manual step: GitHub repo → Packages → shekel → Package settings → Change visibility → Public. Without this, `docker compose pull` will fail with an authentication error. Document this in the README as a maintainer note.

**Verification:**
- `docker compose -f docker-compose.yml config` shows `image:` not `build:`
- `docker compose -f docker-compose.yml -f docker-compose.build.yml config` shows both `image:` and `build:`
- `deploy.sh --skip-pull --skip-backup` still builds and deploys successfully

---

### WU-02: Verify GHCR image is published and public

**Audit findings addressed:** C-002

**Problem:** Before end users can pull the image, we need to confirm the CI workflow has successfully published it and that the package is publicly accessible.

**Steps (manual, not code changes):**

1. Check GitHub Actions: go to the repo → Actions → "Build & Publish Docker Image" workflow. Verify the most recent run on `main` succeeded.
2. Check package existence: go to the repo → Packages → `shekel`. Verify the `latest` tag exists.
3. Check visibility: Package settings → Danger Zone → Visibility. If "Private", change to "Public".
4. Verify pull works from a clean environment:
   ```bash
   docker pull ghcr.io/saltyreformed/shekel:latest
   ```
   If this fails with 401/403, the package is still private.

**If the workflow has never run successfully on `main`:** Merge the current `dev` branch (or push to `main`) to trigger the workflow. Then repeat steps 1-4.

---

### WU-03: Update docker-compose.dev.yml to use build override pattern consistently

**Audit findings addressed:** C-002 (consistency)

**Problem:** `docker-compose.dev.yml` independently defines `build: .` and its own service configuration. After WU-01, the self-hosted build pattern uses overrides. The dev compose file should remain self-contained (it's already separate from production), but its quick-start comment should reference the correct workflow.

**Files to modify:**

1. **`docker-compose.dev.yml`** -- Minor comment update only.

   Lines 5-7: Update the comment to clarify this file is for developers who have the full source tree:
   ```yaml
   # Shekel Budget App -- Development Docker Compose
   #
   # For developers with the full source tree. End users should use
   # docker-compose.yml with the pre-built GHCR image instead.
   ```

   No structural changes -- this file is already correct for the dev workflow.

---

### WU-04: Fix entrypoint ordering -- seed_user before seed_tax_brackets

**Audit findings addressed:** C-003, D-003

**Problem:** `entrypoint.sh` runs `seed_tax_brackets.py` (step 5) before `seed_user.py` (step 6). On first boot, `seed_tax_brackets.py` finds no users and skips. The user created by `seed_user.py` has no federal tax brackets, FICA config, or state tax config until the container is restarted.

**Root cause traced:**
- `scripts/seed_tax_brackets.py:40-42`: `users = db.session.query(User).all()` -- returns empty list on first boot → function returns early with "No users found."
- `scripts/seed_user.py` does NOT call `auth_service._seed_tax_data_for_user()`. It creates user, settings, account, scenario, and categories directly via ORM -- but not tax data.
- By contrast, `auth_service.register_user()` (the `/register` web route) DOES call `_seed_tax_data_for_user()` at line 412.

**Fix:** Swap steps 5 and 6 in `entrypoint.sh`.

**File to modify:**

1. **`entrypoint.sh`** -- Swap the seed_user and seed_tax_brackets blocks.

   Current order (lines 29-51):
   ```
   # ── 4. Seed reference data
   # ── 5. Seed tax brackets
   # ── 6. Seed initial user (optional)
   ```

   New order:
   ```
   # ── 4. Seed reference data
   # ── 5. Seed initial user (optional, first run only)
   # ── 6. Seed tax brackets
   ```

   Specifically:
   - Move lines 38-51 (the `seed_user` conditional block) to immediately after line 36 (`echo "Seeding complete."` after `seed_ref_tables`).
   - Move lines 33-36 (the `seed_tax_brackets` block) to after the seed_user block.
   - Renumber the step comments to match the new order.
   - Update the `echo "Seeding complete."` line to come after seed_tax_brackets (the last seed step).

   Also update the comment on the seed_user block (currently line 44) which says:
   ```
   # Note: the /register route creates the same bootstrap data as this
   # script (user, settings, account, scenario, categories) plus default
   # tax configuration.
   ```
   This comment is accurate and should remain -- it documents the known gap that seed_user.py doesn't create tax data (seed_tax_brackets.py handles that in step 6).

**Why swapping is sufficient:** After the swap, the execution order becomes:
1. `seed_ref_tables.py` -- creates ref data (no dependencies)
2. `seed_user.py` -- creates user, settings, account, scenario, categories (depends on ref data for AccountType lookup)
3. `seed_tax_brackets.py` -- creates tax brackets for all existing users (depends on ref data for FilingStatus/TaxType lookups AND on users existing)

All dependencies are now satisfied in order.

**Alternative considered and rejected:** Making `seed_user.py` call `_seed_tax_data_for_user()` directly. This was rejected because:
- It would create a coupling between the seed script and a private function in `auth_service.py`
- `seed_tax_brackets.py` already handles this correctly with upsert/idempotency
- The simple swap achieves the same result with minimal code change

**Verification:**
- Delete the Docker volume (`docker volume rm shekel_pgdata`) and run `docker compose up -d` for a clean first boot.
- Check `docker logs shekel-app` for: "Seeding initial user..." BEFORE "Seeding tax configuration..."
- Log in as the seed user → navigate to Salary → Paycheck Calculator → verify tax deductions appear (non-zero federal/state/FICA).

---

### WU-05: Update entrypoint comment to document seed_user vs register_user gap

**Audit findings addressed:** D-008

**Problem:** The entrypoint comment at (current) line 44 says the `/register` route creates "the same bootstrap data as this script... plus default tax configuration." This is accurate but the "plus" framing buries the important fact. After WU-04 fixes the ordering, the comment should clearly explain why seed_tax_brackets.py runs after seed_user.py.

**File to modify:**

1. **`entrypoint.sh`** -- Update the comment block on the seed_user section (after WU-04 reordering).

   New comment:
   ```bash
   # seed_user.py creates: user, settings, checking account, baseline
   # scenario, and default categories.  It does NOT create tax data --
   # that is handled by seed_tax_brackets.py in the next step.
   #
   # The /register web route creates all of the above PLUS tax data in
   # a single transaction via auth_service.register_user().
   ```

---

### WU-06: Make TOTP_ENCRYPTION_KEY optional at startup

**Audit findings addressed:** C-004, M-006, D-007

**Problem:** `TOTP_ENCRYPTION_KEY` is enforced at three levels:
1. `docker-compose.yml:61` -- `${TOTP_ENCRYPTION_KEY:?Set TOTP_ENCRYPTION_KEY in .env}` (Docker refuses to start)
2. `app/config.py:105-106` -- `ProdConfig.__init__` raises `ValueError` (Flask refuses to start)
3. `app/services/mfa_service.py:27-29` -- `get_encryption_key()` raises `RuntimeError` (MFA operation fails)

Layer 3 is the correct enforcement point -- it fires only when a user actually tries to enable MFA. Layers 1 and 2 are overly strict for initial setup since MFA is opt-in.

**Files to modify:**

1. **`app/config.py`** -- Remove the TOTP_ENCRYPTION_KEY validation from `ProdConfig.__init__`.

   Lines 105-106: Delete:
   ```python
   if not os.getenv("TOTP_ENCRYPTION_KEY"):
       raise ValueError("TOTP_ENCRYPTION_KEY must be set in production.")
   ```

   Add a startup warning instead. In `create_app()` in `app/__init__.py`, after the config is loaded, log a warning if the key is missing. This alerts operators without blocking startup.

2. **`app/__init__.py`** -- Add a startup warning after the config is loaded (after line 37):

   ```python
   if not app.config.get("TOTP_ENCRYPTION_KEY"):
       app.logger.warning(
           "TOTP_ENCRYPTION_KEY is not set. MFA/TOTP will be unavailable "
           "until this key is configured. See .env.example for details."
       )
   ```

   Note: `app.logger` is available after `setup_logging(app)` on line 40, so this warning must come after that call. Insert it after line 40 (after `setup_logging`), before the extensions are initialized.

3. **`docker-compose.yml`** -- Change the TOTP_ENCRYPTION_KEY line from required to optional.

   Line 61: Change:
   ```yaml
   TOTP_ENCRYPTION_KEY: ${TOTP_ENCRYPTION_KEY:?Set TOTP_ENCRYPTION_KEY in .env}
   ```
   to:
   ```yaml
   TOTP_ENCRYPTION_KEY: ${TOTP_ENCRYPTION_KEY:-}
   ```

   This matches how `docker-compose.dev.yml:89` already handles it.

4. **`app/routes/auth.py`** -- Add graceful handling when TOTP key is missing and a user tries to enable MFA.

   The MFA setup route (`POST /mfa/confirm`, around line 359) calls `mfa_service.encrypt_secret()` which calls `get_encryption_key()`. If the key is missing, an unhandled `RuntimeError` propagates to a generic 500 page. Instead, catch it and show a meaningful flash message.

   In the `mfa_confirm()` function, wrap the encrypt call:
   ```python
   try:
       mfa_config.totp_secret_encrypted = mfa_service.encrypt_secret(secret)
   except RuntimeError:
       flash("MFA is not available. The server administrator must set "
             "TOTP_ENCRYPTION_KEY before MFA can be enabled.", "danger")
       return redirect(url_for("settings.security_settings"))
   ```

   Similarly, in `mfa_verify()` (around line 260) and `mfa_disable()` (around line 439) where `decrypt_secret()` is called, add the same pattern. However, these paths are only reachable if MFA was previously enabled (which requires the key), so the error is less likely. Still, handle it gracefully for the case where the key was set, MFA was enabled, and then the key was removed from the environment:
   ```python
   try:
       decrypted = mfa_service.decrypt_secret(mfa_config.totp_secret_encrypted)
   except RuntimeError:
       flash("MFA decryption failed. Contact your administrator -- the "
             "TOTP_ENCRYPTION_KEY may have changed or been removed.", "danger")
       return redirect(url_for("auth.login_form"))
   ```

5. **`.env.example`** -- Update the TOTP_ENCRYPTION_KEY comment.

   Lines 37-41: Change:
   ```
   # ── Authentication & Security ────────────────────────────────────
   # REQUIRED. Fernet key for encrypting TOTP secrets at rest.
   # Generate with:
   #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   TOTP_ENCRYPTION_KEY=
   ```
   to:
   ```
   # ── Authentication & Security ────────────────────────────────────
   # Fernet key for encrypting TOTP secrets at rest.
   # Optional for initial setup. Required before any user enables MFA.
   # The app will start without it but MFA/TOTP will be unavailable.
   #
   # Generate with (choose one):
   #   docker run --rm python:3.12-slim python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   #   openssl rand -base64 32
   #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   TOTP_ENCRYPTION_KEY=
   ```

   Note: `openssl rand -base64 32` produces a 44-character base64 string which is NOT a valid Fernet key (Fernet requires a specific 32-byte URL-safe base64 key). The only reliable way to generate a Fernet key is via the `cryptography` library. The Docker one-liner is the best option for non-technical users since `cryptography` is pre-installed in the app image. Update the comment:
   ```
   # Generate with:
   #   docker run --rm ghcr.io/saltyreformed/shekel:latest python -c \
   #     "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   This uses the app's own image (which has `cryptography` installed) so the user needs nothing beyond Docker.

**Tests to update:**

6. **`tests/test_config.py`** -- Add a test confirming ProdConfig starts without TOTP_ENCRYPTION_KEY.

   Add to `TestProdConfig`:
   ```python
   def test_starts_without_totp_key(self, monkeypatch):
       """ProdConfig no longer requires TOTP_ENCRYPTION_KEY at startup."""
       monkeypatch.setattr(BaseConfig, "SECRET_KEY", "secure-key-for-test")
       monkeypatch.setattr(
           ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://localhost/shekel"
       )
       monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
       config = ProdConfig()  # Should not raise
       assert config.TOTP_ENCRYPTION_KEY is None
   ```

7. **`tests/test_routes/test_auth.py`** -- Add a test for the MFA-enable-without-key graceful error.

   Add to `TestMfaSetup`:
   ```python
   def test_mfa_confirm_without_totp_key(self, app, auth_client, seed_user, monkeypatch):
       """POST /mfa/confirm flashes error when TOTP_ENCRYPTION_KEY is missing."""
       monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
       # Set up session with a valid secret
       with auth_client.session_transaction() as sess:
           sess["_mfa_setup_secret"] = pyotp.random_base32()
       response = auth_client.post("/mfa/confirm", data={"totp_code": "000000"})
       assert response.status_code == 302  # Redirects, not 500
   ```

**Verification:**
- Remove `TOTP_ENCRYPTION_KEY` from `.env` → `docker compose up -d` → app starts successfully
- Check `docker logs shekel-app` for the warning: "TOTP_ENCRYPTION_KEY is not set..."
- Log in → navigate to Settings → Security → Enable MFA → see flash message "MFA is not available..."
- Set `TOTP_ENCRYPTION_KEY` in `.env` → `docker compose up -d` → MFA works normally

---

## Phase 2 -- Documentation and UX Improvements

These changes improve the user experience and documentation accuracy. They can be landed incrementally.

---

### WU-07: Write Docker Quick Start section in README

**Audit findings addressed:** C-001, M-001, M-003, D-001

**Problem:** The README contains only a developer-focused "Quick Start (Arch Linux)" section. There is no documentation for the Docker-based end-user setup path.

**File to modify:**

1. **`README.md`** -- Major restructure of the Quick Start area.

   **Restructure outline** (preserving all existing content, reorganizing):

   ```markdown
   ## Quick Start (Docker)

   The fastest way to run Shekel. Requires only Docker and Docker Compose.

   ### 1. Prerequisites

   Install Docker Engine and the Compose plugin:
   - **Linux:** https://docs.docker.com/engine/install/
   - **macOS / Windows:** Install Docker Desktop

   Verify:
   ```bash
   docker compose version   # should print v2.x+
   ```

   ### 2. Download Configuration Files

   Download these two files from the repository into a new directory:

   ```bash
   mkdir shekel && cd shekel
   curl -O https://raw.githubusercontent.com/SaltyReformed/Shekel/main/docker-compose.yml
   curl -O https://raw.githubusercontent.com/SaltyReformed/Shekel/main/.env.example
   cp .env.example .env
   ```

   ### 3. Configure Environment

   Edit `.env` and set these values:

   | Variable | Required? | How to Set |
   |---|---|---|
   | `POSTGRES_PASSWORD` | Yes | Choose any strong password |
   | `SECRET_KEY` | Yes | Run: `openssl rand -hex 32` |
   | `TOTP_ENCRYPTION_KEY` | No (until MFA) | See `.env.example` for instructions |
   | `SEED_USER_EMAIL` | No | Default: `admin@shekel.local` |
   | `SEED_USER_PASSWORD` | No | Default: `ChangeMe!2026` (12+ chars) |

   The remaining variables in `.env` have sensible defaults and can be left as-is.

   ### 4. Start the Application

   ```bash
   docker compose up -d
   ```

   First startup takes ~30 seconds (database initialization, migrations, seeding).
   Check progress:
   ```bash
   docker compose logs -f app
   ```

   Look for: `=== Starting Application ===` followed by Gunicorn startup messages.

   ### 5. Log In

   Open **http://localhost** in your browser.

   - **Email:** `admin@shekel.local` (or your `SEED_USER_EMAIL`)
   - **Password:** `ChangeMe!2026` (or your `SEED_USER_PASSWORD`)

   ### 6. First-Time Setup in the App

   After logging in, you'll see a welcome banner with setup steps:

   1. **Generate Pay Periods** -- Click the link in the banner. Enter your next payday and click Generate.
   2. **Set Up Salary Profile** -- Enter your gross salary, pay frequency, and tax filing status.
   3. **Create Recurring Transactions** -- Add your regular bills, subscriptions, and income.
   4. **Set Anchor Balance** -- On the budget grid, click the balance display and enter your current checking balance.

   ### Updating

   ```bash
   docker compose pull && docker compose up -d
   ```

   The entrypoint automatically runs database migrations on every start.

   ### Troubleshooting

   | Symptom | Fix |
   |---|---|
   | Browser shows "connection refused" | Wait 30s for startup, then check `docker compose logs app` |
   | `POSTGRES_PASSWORD` error on startup | Ensure `POSTGRES_PASSWORD` is set in `.env` |
   | `SECRET_KEY` error on startup | Ensure `SECRET_KEY` is set in `.env` |
   | App starts but login fails | Check `SEED_USER_EMAIL` and `SEED_USER_PASSWORD` in `.env` |
   | MFA enable fails with error | Set `TOTP_ENCRYPTION_KEY` in `.env` (see instructions in file) |

   ---

   ## Developer Setup (from source)

   For contributing to Shekel or running from source on Arch Linux.

   [existing content from "Quick Start (Arch Linux)" goes here, with the
    title changed and minor wording tweaks]
   ```

   The existing "Quick Start (Arch Linux)" content at lines 9-76 moves below and is renamed to "Developer Setup (from source)". All existing content is preserved.

   Also update line 74 from "Open http://localhost:5000" to include the note that this is the development server URL (not Docker).

   Move the "First-Time Setup in the App" section (lines 79-84) into the Docker Quick Start (above) and add a cross-reference from the Developer section: "See the Docker Quick Start for first-time in-app setup steps."

**Verification:**
- Read the Docker Quick Start section as a non-technical user -- can you complete every step without guessing?
- `curl` commands produce the correct files
- The troubleshooting table covers every common failure mode

---

### WU-08: Restructure README sections and update project description

**Audit findings addressed:** C-001 (supporting WU-07)

**Problem:** After adding the Docker Quick Start, the README needs a brief intro paragraph that distinguishes the two setup paths before the user scrolls.

**File to modify:**

1. **`README.md`** -- Update the introductory text (lines 1-7).

   After the existing description paragraph, add:
   ```markdown
   **Two ways to run Shekel:**
   - **Docker (recommended):** Download two files, run `docker compose up`. No Python or PostgreSQL needed. See [Quick Start (Docker)](#quick-start-docker).
   - **From source:** Clone the repo, set up Python and PostgreSQL locally. See [Developer Setup](#developer-setup-from-source).
   ```

---

### WU-09: Provide non-Python key generation commands in .env.example

**Audit findings addressed:** C-004, M-002, D-007

**Problem:** Both `SECRET_KEY` and `TOTP_ENCRYPTION_KEY` generation commands in `.env.example` require Python on the host. Non-technical Docker users won't have Python.

**File to modify:**

1. **`.env.example`** -- Update generation instructions for both keys.

   SECRET_KEY (line 9): Change:
   ```
   # REQUIRED in production. Generate with:
   #   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   to:
   ```
   # REQUIRED in production. Generate with one of:
   #   openssl rand -hex 32
   #   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   `openssl` is available on virtually all Linux and macOS systems, and Docker Desktop includes it.

   TOTP_ENCRYPTION_KEY (lines 38-41): Already addressed in WU-06. The Docker one-liner uses the app's own image:
   ```
   #   docker run --rm ghcr.io/saltyreformed/shekel:latest python -c \
   #     "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

---

### WU-10: Improve entrypoint error messaging for seed script failures

**Audit findings addressed:** M-005

**Problem:** `set -e` in `entrypoint.sh` causes the container to exit immediately on any seed script failure. The user gets no app, no browser output, and must know to check `docker logs`.

**Approach:** Keep `set -e` (fail-fast is correct for data integrity), but add a trap that prints troubleshooting guidance when the entrypoint exits non-zero.

**File to modify:**

1. **`entrypoint.sh`** -- Add an ERR trap near the top (after `set -e`).

   After line 2 (`set -e`), add:
   ```bash
   trap 'echo ""; echo "=== Shekel entrypoint failed ==="; echo "Check the output above for error details."; echo "Common fixes: verify .env values, ensure PostgreSQL is accessible."; echo "Docs: https://github.com/SaltyReformed/Shekel#troubleshooting"' ERR
   ```

   This prints a clear message with a link to the troubleshooting section when any command in the entrypoint fails.

---

### WU-11: Improve no_setup.html for Docker deployment context

**Audit findings addressed:** M-007

**Problem:** `no_setup.html` says "no baseline budget scenario was found" and only offers "Create a new account." For Docker users where `seed_user.py` should have created the baseline scenario, this is confusing -- they already have an account.

**Approach:** Add a secondary action that creates the missing baseline scenario for the current user, rather than forcing them to register a new account.

**Files to modify:**

1. **`app/templates/grid/no_setup.html`** -- Add a second button.

   Replace the current content (lines 9-15) with:
   ```html
   <h3><i class="bi bi-exclamation-triangle text-warning"></i> Setup Incomplete</h3>
   <p class="mt-3">
     Your account is missing a baseline budget scenario. This can happen
     if your account was created outside the normal registration process.
   </p>
   <div class="d-flex justify-content-center gap-2">
     <form method="post" action="{{ url_for('grid.create_baseline') }}">
       <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
       <button type="submit" class="btn btn-primary">
         <i class="bi bi-plus-circle"></i> Create Baseline Scenario
       </button>
     </form>
     <a href="{{ url_for('auth.register') }}" class="btn btn-outline-secondary">
       <i class="bi bi-person-plus"></i> Create a new account instead
     </a>
   </div>
   ```

2. **`app/routes/grid.py`** -- Add a `create_baseline` route.

   Add after the `index()` function:
   ```python
   @grid_bp.route("/create-baseline", methods=["POST"])
   @login_required
   def create_baseline():
       """Create a missing baseline scenario for the current user."""
       existing = (
           db.session.query(Scenario)
           .filter_by(user_id=current_user.id, is_baseline=True)
           .first()
       )
       if existing:
           return redirect(url_for("grid.index"))

       scenario = Scenario(
           user_id=current_user.id,
           name="Baseline",
           is_baseline=True,
       )
       db.session.add(scenario)
       db.session.commit()
       return redirect(url_for("grid.index"))
   ```

   This is POST-only with CSRF protection. If a baseline already exists, it's a no-op redirect.

3. **`tests/test_routes/test_grid.py`** -- Add tests for the new route.

   ```python
   def test_create_baseline_creates_scenario(self, app, auth_client, seed_user):
       """POST /create-baseline creates a baseline scenario."""
       # Delete existing baseline
       Scenario.query.filter_by(user_id=seed_user.id, is_baseline=True).delete()
       db.session.commit()

       response = auth_client.post("/create-baseline", follow_redirects=True)
       assert response.status_code == 200
       baseline = Scenario.query.filter_by(
           user_id=seed_user.id, is_baseline=True
       ).first()
       assert baseline is not None

   def test_create_baseline_idempotent(self, app, auth_client, seed_user):
       """POST /create-baseline is a no-op when baseline exists."""
       response = auth_client.post("/create-baseline", follow_redirects=True)
       assert response.status_code == 200
       count = Scenario.query.filter_by(
           user_id=seed_user.id, is_baseline=True
       ).count()
       assert count == 1
   ```

---

## Phase 3 -- Minor Cleanup

Low-risk, low-effort fixes. Can be landed individually or batched.

---

### WU-12: Update stale counts in README project structure

**Audit findings addressed:** N-001, D-005, D-006

**Problem:** README says "78 files, 17 directories" for templates (actual: 80 files, 16 subdirectories), "1533 test functions, 61 test files" (actual: 63 test files).

**File to modify:**

1. **`README.md`** -- Update the project structure section (lines 170-200).

   Run these commands to get current counts:
   ```bash
   find app/templates -name '*.html' | wc -l          # template files
   find app/templates -mindepth 1 -type d | wc -l      # template subdirs
   find tests -name 'test_*.py' | wc -l                # test files
   timeout 660 pytest --collect-only -q 2>/dev/null | tail -1  # test count
   ```

   Update the counts in the project structure tree comments:
   - `templates/` line: update file and directory counts
   - `tests/` line: update test function count and file count
   - `static/` line: verify JS file count with `find app/static -name '*.js' | wc -l`
   - `migrations/` line: verify version count with `ls migrations/versions/*.py | wc -l`

   Also update line 162 ("**Test suite:** 1533 test functions across 61 test files").

---

### WU-13: Replace personal email in scripts and docs

**Audit findings addressed:** N-002, N-003

**Problem:** `scripts/reset_mfa.py:11` uses `josh@saltyreformed.com` in the example. Several design docs also reference this email and "Josh Grubb."

**Files to modify:**

1. **`scripts/reset_mfa.py`** -- Line 11: Change:
   ```
       python scripts/reset_mfa.py josh@saltyreformed.com
   ```
   to:
   ```
       python scripts/reset_mfa.py admin@shekel.local
   ```

2. **`docs/phase_8a_implementation_plan.md`** -- Line 1188: Same substitution. These are historical design docs -- only change the example command, not the narrative context.

   Note: `docs/plans/2026-03-08-docker-implementation-plan.md:123,125` references old defaults in code snippets that were part of the original plan. These are historical artifacts showing what was planned vs. what was implemented. Leave these unchanged -- they document the evolution of the project.

---

### WU-14: Replace hardcoded credentials in alembic.ini

**Audit findings addressed:** N-004

**Problem:** `alembic.ini:33` contains `postgresql://shekel_user:shekel_pass@localhost:5432/shekel`. This is a fallback that Flask-Migrate overrides at runtime, but it's a credential in a committed file.

**File to modify:**

1. **`alembic.ini`** -- Line 33: Change:
   ```
   sqlalchemy.url = postgresql://shekel_user:shekel_pass@localhost:5432/shekel
   ```
   to:
   ```
   # Overridden by Flask-Migrate at runtime. This fallback uses peer auth.
   sqlalchemy.url = postgresql:///shekel
   ```

---

### WU-15: Pin Dockerfile to stable Python version

**Audit findings addressed:** N-005

**Problem:** `Dockerfile:6,20` uses `python:3.14-slim`. Python 3.14 is pre-release. The project requires Python 3.12+.

**File to modify:**

1. **`Dockerfile`** -- Lines 6 and 20: Change both:
   ```dockerfile
   FROM python:3.14-slim AS builder
   ```
   and:
   ```dockerfile
   FROM python:3.14-slim
   ```
   to:
   ```dockerfile
   FROM python:3.13-slim AS builder
   ```
   and:
   ```dockerfile
   FROM python:3.13-slim
   ```

   Python 3.13 is the latest stable release as of March 2026 and satisfies the 3.12+ requirement.

**Verification:**
- `docker compose -f docker-compose.yml -f docker-compose.build.yml build app` succeeds
- Run the full test suite against the new image

---

## Implementation Order

The work units have the following dependency chain:

```
Phase 1 (Critical -- single branch, land together):
  WU-01 (GHCR compose)
  WU-02 (verify GHCR image -- manual, prerequisite for WU-01 verification)
  WU-03 (dev compose comment)
  WU-04 (entrypoint ordering fix)
  WU-05 (entrypoint comment)
  WU-06 (TOTP key optional) ← includes test updates

Phase 2 (Documentation/UX -- can be incremental):
  WU-07 (Docker Quick Start in README) ← depends on WU-01
  WU-08 (README intro restructure) ← depends on WU-07
  WU-09 (.env.example key generation) ← depends on WU-06
  WU-10 (entrypoint error messaging)
  WU-11 (no_setup.html improvement) ← includes new route + tests

Phase 3 (Cleanup -- independent, any order):
  WU-12 (README counts)
  WU-13 (personal email cleanup)
  WU-14 (alembic.ini credentials)
  WU-15 (Dockerfile Python version)
```

**Suggested commit sequence:**
1. `fix: swap entrypoint seed ordering so tax brackets run after user creation` (WU-04, WU-05)
2. `feat: make TOTP_ENCRYPTION_KEY optional at startup, required only for MFA` (WU-06)
3. `feat: add end-user docker-compose.yml with GHCR image and build override` (WU-01, WU-02, WU-03)
4. `docs: add Docker Quick Start and restructure README for end users` (WU-07, WU-08, WU-09)
5. `fix: improve entrypoint error messaging and no_setup.html recovery` (WU-10, WU-11)
6. `chore: update stale counts, replace personal email, clean up credentials` (WU-12, WU-13, WU-14, WU-15)

---

## Files Changed Summary

| File | Work Units | Change Type |
|---|---|---|
| `docker-compose.yml` | WU-01, WU-06 | Modify (image, TOTP, monitoring network) |
| `docker-compose.build.yml` | WU-01 | **Create** |
| `docker-compose.dev.yml` | WU-03 | Modify (comment only) |
| `entrypoint.sh` | WU-04, WU-05, WU-10 | Modify (reorder, comments, trap) |
| `app/config.py` | WU-06 | Modify (remove TOTP validation) |
| `app/__init__.py` | WU-06 | Modify (add startup warning) |
| `app/routes/auth.py` | WU-06 | Modify (graceful MFA error handling) |
| `app/routes/grid.py` | WU-11 | Modify (add create_baseline route) |
| `app/templates/grid/no_setup.html` | WU-11 | Modify (add second button) |
| `.env.example` | WU-06, WU-09 | Modify (comments, generation commands) |
| `README.md` | WU-07, WU-08, WU-12 | Modify (major restructure) |
| `scripts/deploy.sh` | WU-01 | Modify (compose file flags) |
| `scripts/reset_mfa.py` | WU-13 | Modify (example email) |
| `docs/phase_8a_implementation_plan.md` | WU-13 | Modify (example email) |
| `alembic.ini` | WU-14 | Modify (remove credentials) |
| `Dockerfile` | WU-15 | Modify (Python version) |
| `tests/test_config.py` | WU-06 | Modify (add TOTP optional test) |
| `tests/test_routes/test_auth.py` | WU-06 | Modify (add MFA-without-key test) |
| `tests/test_routes/test_grid.py` | WU-11 | Modify (add create_baseline tests) |

**Total:** 19 files (1 new, 18 modified)
