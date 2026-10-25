> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# New-User Setup Audit Report

**Audit date:** 2026-03-21
**Scope:** Non-technical end-user setup experience, from GitHub discovery to working app
**Auditor:** Claude Code (automated codebase audit)

---

## 1. Executive Summary

A non-technical user cannot currently get Shekel running without outside help. The fundamental blocker is that the intended happy path -- download `docker-compose.yml` and `.env.example`, run `docker compose up` -- does not exist. The `docker-compose.yml` uses `build: .` (requiring the full source tree) rather than pulling a pre-built image from GHCR, and the `README.md` contains only a developer-oriented Quick Start that assumes Arch Linux, Python, PostgreSQL, and `git` experience. Beyond this structural gap, three additional issues would block or confuse a first-time user: an entrypoint ordering bug that skips tax bracket seeding on first boot, a required `TOTP_ENCRYPTION_KEY` whose generation command requires Python (which a Docker-only user won't have), and the absence of any Docker-focused setup documentation in the README.

The project infrastructure is otherwise well-engineered: the entrypoint, seed scripts, health checks, and idempotency patterns are solid. Fixing the issues below would make the project genuinely accessible to non-technical users.

---

## 2. Critical Issues

### C-001 -- No Docker Quick Start in README

- **Files:** `README.md:9-76`
- **Problem:** The README's only Quick Start section is titled "Quick Start (Arch Linux)" and walks through cloning the repo, creating a Python venv, installing pip dependencies, running Flask migrations, and executing seed scripts manually. There is zero mention of the Docker-based setup path anywhere in the README. A non-technical user arriving from GitHub would not know that Docker deployment exists, let alone how to use it.
- **Impact:** Complete blocker. The user has no instructions to follow.
- **Suggested fix:** Add a "Quick Start (Docker)" section before the existing Arch Linux section. It should cover: prerequisites (Docker + Docker Compose), downloading the compose file and env template, editing `.env`, running `docker compose up -d`, checking logs, and logging in. Rename the existing section to "Developer Setup (from source)" and place it after the Docker section.

### C-002 -- docker-compose.yml Uses `build: .`, Not a GHCR Image

- **Files:** `docker-compose.yml:40`, `.github/workflows/docker-publish.yml:14-16,42-43`
- **Problem:** The intended happy path says users should only need `docker-compose.yml` and `.env.example`. However, `docker-compose.yml:40` specifies `build: .`, which requires the full source tree to be present. The CI workflow (`docker-publish.yml`) publishes images to `ghcr.io/saltyreformed/shekel:latest`, but no compose file references this image. A user who downloads only the two files and runs `docker compose up` will get a build error.
- **Impact:** Complete blocker for the "download two files" flow.
- **Suggested fix:** Create a separate `docker-compose.prod.yml` (or modify the existing one) that uses `image: ghcr.io/saltyreformed/shekel:latest` instead of `build: .`. Alternatively, add a commented-out `image:` line with instructions on when to use `build` vs `image`. The Docker Quick Start docs should reference the image-based compose file.

### C-003 -- Entrypoint Ordering Bug: Tax Brackets Seeded Before User Exists

- **Files:** `entrypoint.sh:29-51`, `scripts/seed_tax_brackets.py:40-42`, `scripts/seed_user.py`
- **Problem:** The entrypoint runs scripts in this order:
  1. `seed_ref_tables.py` (step 4)
  2. `seed_tax_brackets.py` (step 5)
  3. `seed_user.py` (step 6, conditional)

  `seed_tax_brackets.py:40-42` queries `User.query.all()` and skips if no users exist. On first boot, no users exist yet (seed_user hasn't run), so tax brackets are not seeded. The user created by `seed_user.py` therefore has **no federal tax brackets, no FICA config, and no state tax config** until the container is restarted (when seed_tax_brackets finds the now-existing user).

  Note: The `/register` route (`auth_service.register_user:411-412`) calls `_seed_tax_data_for_user()` internally, so users who register via the web UI get tax data. But the seed_user.py path does not call this function.

- **Impact:** On first boot, the paycheck calculator will produce incorrect results (no tax deductions). The user would need to restart the container or manually trigger tax seeding.
- **Suggested fix:** Swap steps 5 and 6 in `entrypoint.sh` so that `seed_user.py` runs before `seed_tax_brackets.py`. Alternatively, have `seed_user.py` call `_seed_tax_data_for_user()` directly after creating the user.

### C-004 -- TOTP_ENCRYPTION_KEY Generation Requires Python + cryptography

- **Files:** `.env.example:38-41`, `docker-compose.yml:61`, `app/config.py:105-106`
- **Problem:** `TOTP_ENCRYPTION_KEY` is **required** -- `docker-compose.yml:61` uses `${TOTP_ENCRYPTION_KEY:?Set TOTP_ENCRYPTION_KEY in .env}` and `ProdConfig.__init__` raises `ValueError` if it's missing. The generation command in `.env.example:40` is:
  ```
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  A non-technical user running only Docker will not have Python or the `cryptography` library installed on their host.
- **Impact:** The user cannot generate this required value without installing Python and pip dependencies, contradicting the "just Docker" premise.
- **Suggested fix:** Either:
  1. Provide the generation command as a Docker one-liner: `docker run --rm python:3.14-slim pip install cryptography && python -c "..."` (or use the app image itself).
  2. Pre-generate a default key in `.env.example` with a prominent "CHANGE THIS" warning (less secure but unblocks setup).
  3. Make TOTP_ENCRYPTION_KEY optional at startup, auto-generating one if not provided, and only requiring it if MFA is enabled. Note: since MFA is opt-in per user, requiring the key at boot time is overly strict for initial setup.

---

## 3. Moderate Issues

### M-001 -- docker-compose.yml Quick Start Comment Inconsistent with README

- **Files:** `docker-compose.yml:13`, `README.md:74`
- **Problem:** The quick-start comment in `docker-compose.yml:13` says "Open http://localhost (via Nginx)" (port 80). The README says "Open http://localhost:5000." These are different ports for different deployment modes (Docker via Nginx vs. local Flask dev server), but there's no clear separation explaining which URL applies to which setup.
- **Suggested fix:** The Docker Quick Start should say `http://localhost` (port 80). The Developer Quick Start should say `http://localhost:5000`. Each section should only mention its own URL.

### M-002 -- SECRET_KEY Generation Also Requires Python

- **Files:** `.env.example:9`
- **Problem:** The `SECRET_KEY` generation command is `python -c "import secrets; print(secrets.token_hex(32))"`. Same issue as C-004 -- a Docker-only user may not have Python. Unlike TOTP_ENCRYPTION_KEY, the `secrets` module is in Python's stdlib so no pip install is needed, but Python itself may be absent.
- **Suggested fix:** Provide an alternative: `openssl rand -hex 32` (OpenSSL is commonly available) or `head -c 32 /dev/urandom | xxd -p` (works on any Linux). Better yet, provide all three alternatives.

### M-003 -- No Update/Upgrade Path Documented

- **Files:** `docker-compose.yml:16`, `README.md`
- **Problem:** The docker-compose.yml quick-start comment says `docker compose build && docker compose up -d` for updates, which is correct for the `build: .` flow but wrong for the intended GHCR image flow (which would use `docker compose pull && docker compose up -d`). The README has no update instructions at all.
- **Suggested fix:** Document the update path in the Docker Quick Start section: `docker compose pull && docker compose up -d`. The entrypoint already handles migrations on restart, so this should work seamlessly.

### M-004 -- `monitoring` Network May Cause Docker Compose Warning

- **Files:** `docker-compose.yml:122-127`
- **Problem:** The `monitoring` network is defined with `name: monitoring` and connected to the `app` service. If a user doesn't have a Loki/Grafana/Promtail stack running, Docker Compose will create this network but it serves no purpose. More importantly, the network is not `internal: true`, meaning it's externally routable -- this could be confusing or a mild security concern.
- **Suggested fix:** Document that the monitoring network is optional and only needed if running the Loki/Grafana stack. Consider making it conditional or moving it to a `docker-compose.monitoring.yml` override file.

### M-005 -- `set -e` in Entrypoint Means Any Seed Failure Kills the Container

- **Files:** `entrypoint.sh:2`
- **Problem:** `set -e` causes the entrypoint to exit on any command failure. If `seed_ref_tables.py` or `seed_tax_brackets.py` fails (e.g., due to a transient DB issue after `pg_isready` succeeds), the container exits with no app running. The user sees nothing in the browser and must check `docker logs` to diagnose. For a non-technical user, this is opaque.
- **Impact:** Not a blocker per se (the scripts are robust), but worth noting that error recovery is poor.
- **Suggested fix:** Consider wrapping seed scripts with retry logic or more descriptive error messages. At minimum, document that users should check `docker logs shekel-app` if the app doesn't start.

### M-006 -- .env.example TOTP_ENCRYPTION_KEY Marked REQUIRED but Should Be Optional for Initial Setup

- **Files:** `.env.example:38`, `app/services/mfa_service.py:27-29`
- **Problem:** `.env.example:38` says "REQUIRED. Fernet key for encrypting TOTP secrets at rest." and the value is empty. `ProdConfig` enforces this at startup. However, MFA is entirely optional -- `mfa_service.get_encryption_key()` is only called when a user enables MFA. For a first-time user who just wants to try the app, requiring this key adds unnecessary friction.
- **Suggested fix:** Consider making `TOTP_ENCRYPTION_KEY` optional at startup (only required when a user actually enables MFA). This would require removing the `ProdConfig.__init__` validation for this key and letting `mfa_service.get_encryption_key()` raise at MFA-enable time instead. Document that the key must be set before enabling MFA.

### M-007 -- `no_setup.html` Template Could Be Misleading for Docker Users

- **Files:** `app/templates/grid/no_setup.html:9-15`, `app/routes/grid.py:49-50`
- **Problem:** If the seed_user.py script runs but somehow fails to create a baseline scenario (or if the user clears their data), they land on `no_setup.html` which says "no baseline budget scenario was found" and suggests "Create a new account" via the register link. For a Docker deployment where the user already has an account (from the seed script), this message is confusing -- they already have an account, so why register a new one?
- **Suggested fix:** Consider adding a secondary option: "If you already have an account, contact your administrator" or add a check to see if the current user exists but has no scenario, and offer to create one automatically.

---

## 4. Minor Issues

### N-001 -- Project Structure Counts Are Stale

- **Files:** `README.md:182-190`
- **Problem:** Several counts in the README project structure diagram don't match the actual codebase:
  - Templates: README says "78 files, 17 directories" -- actual is **80 files, 16 subdirectories**
  - Tests: README says "1533 test functions, 61 test files" -- actual test files matching `test_*.py` is **63**
- **Suggested fix:** Update the counts or remove specific numbers (they go stale quickly).

### N-002 -- `scripts/reset_mfa.py` Uses Personal Email in Example

- **Files:** `scripts/reset_mfa.py:11`
- **Problem:** The docstring example uses `josh@saltyreformed.com` as the example email. This is a personal email that should be replaced with the generic default.
- **Suggested fix:** Change to `python scripts/reset_mfa.py admin@shekel.local`.

### N-003 -- Design Docs Reference Old Developer Credentials

- **Files:** `docs/plans/2026-03-08-docker-implementation-plan.md:123,125`, `docs/phase_8a_implementation_plan.md:1188`
- **Problem:** Old design docs still reference `josh@saltyreformed.com` and `Josh Grubb` as seed user defaults. These are planning documents and not user-facing, but could confuse someone reading the project history.
- **Suggested fix:** Low priority. These are historical docs. If cleaning up, search for `josh@saltyreformed.com` and `Josh Grubb` across all docs.

### N-004 -- `alembic.ini` Contains Hardcoded Credentials

- **Files:** `alembic.ini:33`
- **Problem:** `sqlalchemy.url = postgresql://shekel_user:shekel_pass@localhost:5432/shekel` is a hardcoded fallback. Flask-Migrate overrides this at runtime, so it's not a functional issue, but `shekel_pass` is a credential in a committed file.
- **Suggested fix:** Replace with a placeholder: `sqlalchemy.url = postgresql:///shekel` (peer auth fallback) or add a comment explaining it's overridden.

### N-005 -- Dockerfile Uses Python 3.14-slim

- **Files:** `Dockerfile:6,20`
- **Problem:** The Dockerfile uses `python:3.14-slim` as the base image. Python 3.14 is in pre-release as of March 2026. If a new user pulls this image and Python 3.14 has breaking changes, the build could fail unpredictably.
- **Suggested fix:** Consider pinning to `python:3.12-slim` or `python:3.13-slim` (stable releases matching the project's stated Python 3.12+ requirement in CLAUDE.md).

---

## 5. Documentation-Code Mismatches

| #     | Documentation Says                                                                                            | Code Actually Does                                                                                                                                                       | Files                                                                                    |
| ----- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| D-001 | README has no Docker setup section                                                                            | `docker-compose.yml`, `entrypoint.sh`, `Dockerfile` all exist and are production-ready                                                                                   | `README.md` vs `docker-compose.yml`, `entrypoint.sh`, `Dockerfile`                       |
| D-002 | Intended flow: user downloads only `docker-compose.yml` + `.env.example`                                      | `docker-compose.yml:40` uses `build: .` requiring full source tree; no compose file references the GHCR image                                                            | `docker-compose.yml:40` vs `.github/workflows/docker-publish.yml:42-43`                  |
| D-003 | Design doc (Task 9) says entrypoint ordering was fixed to run seed_user before seed_tax_brackets              | `entrypoint.sh:33-51` runs seed_tax_brackets (step 5) BEFORE seed_user (step 6)                                                                                          | `entrypoint.sh:33-47` vs `docs/plans/2026-03-19-first-run-onboarding-design.md`          |
| D-004 | `docker-compose.yml:16` says update with `docker compose build && docker compose up -d`                       | Design docs planned GHCR-based updates with `docker compose pull && docker compose up -d`                                                                                | `docker-compose.yml:16` vs `docs/plans/2026-03-08-docker-containerization-design.md:156` |
| D-005 | README says "78 files, 17 directories" for templates                                                          | Actual: 80 HTML files, 16 subdirectories                                                                                                                                 | `README.md:182` vs `find app/templates -name '*.html'`                                   |
| D-006 | README says "1533 test functions across 61 test files"                                                        | Actual: 63 test files (matching `test_*.py`); function count likely also changed                                                                                         | `README.md:162` vs `find tests -name 'test_*.py'`                                        |
| D-007 | `.env.example:38` marks TOTP_ENCRYPTION_KEY as "REQUIRED"                                                     | Only actually required at app startup in production mode (`ProdConfig.__init__`); MFA features work without it until a user tries to enable TOTP                         | `.env.example:38` vs `app/services/mfa_service.py:27-29`                                 |
| D-008 | `entrypoint.sh:44` comment says "/register creates the same bootstrap data... plus default tax configuration" | `seed_user.py` does NOT create tax configuration (register_user does via `_seed_tax_data_for_user` but seed_user.py calls `hash_password` directly, not `register_user`) | `entrypoint.sh:44` vs `scripts/seed_user.py:66-112`                                      |

---

## 6. Recommendations (Prioritized)

### Priority 1: Unblock the Docker setup path (fixes C-001, C-002)

1. **Create an end-user docker-compose file** that uses `image: ghcr.io/saltyreformed/shekel:latest` instead of `build: .`. This could be:
   - A `docker-compose.yml` rename (current one becomes `docker-compose.build.yml`)
   - Or a clearly documented override
2. **Add a Docker Quick Start section to README.md** before the developer section, covering the complete 5-step path: install Docker, download two files, edit `.env`, run compose, open browser.
3. **Provide non-Python key generation commands** for SECRET_KEY and TOTP_ENCRYPTION_KEY (e.g., `openssl` commands or a Docker one-liner).

### Priority 2: Fix the entrypoint ordering bug (fixes C-003, D-003)

4. **Swap steps 5 and 6 in `entrypoint.sh`** so seed_user runs before seed_tax_brackets. This is a one-line change.

### Priority 3: Reduce TOTP_ENCRYPTION_KEY friction (fixes C-004, M-006)

5. **Make TOTP_ENCRYPTION_KEY optional at startup.** Remove the ProdConfig validation for this key. Let MFA service raise a clear error if a user tries to enable MFA without the key set. This removes the biggest friction point for initial setup.
6. If keeping it required, provide a Docker-based generation command in `.env.example` and the README.

### Priority 4: Documentation accuracy (fixes D-005, D-006, N-001)

7. **Update stale counts in README.md** (template files, test files, test function count) or remove exact numbers.
8. **Add `docker logs shekel-app` troubleshooting guidance** to the Docker Quick Start.
9. **Document the update path**: `docker compose pull && docker compose up -d`.

### Priority 5: Cleanup (fixes N-002, N-003, N-004)

10. **Replace personal email in `scripts/reset_mfa.py`** example with `admin@shekel.local`.
11. **Consider pinning Dockerfile base image** to a stable Python release (3.12 or 3.13).
