> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8D-1: Health Endpoint, Docker Finalization, Nginx, and Gunicorn -- Implementation Plan

## Overview

This plan implements the first sub-phase of 8D (Production Deployment) from the Phase 8 Hardening & Ops Plan. It covers the `/health` endpoint, Gunicorn configuration, Dockerfile finalization, Nginx reverse proxy, production and development docker-compose files, and build validation from a clean GitHub clone.

**Pre-existing infrastructure discovered during planning:**

- Gunicorn is already installed in the Docker image (`Dockerfile:17`: `pip install gunicorn`), started via `entrypoint.sh` with minimal inline flags (bind `0.0.0.0:5000`, workers from env var). No `gunicorn.conf.py` configuration file exists. Gunicorn is NOT in `requirements.txt` (Docker-only dependency -- this is correct).
- The Dockerfile already uses a multi-stage build with a non-root `shekel` user, `python:3.14-slim` base image (pinned to minor version), and production-only dependencies. Missing: HEALTHCHECK instruction, correct port (currently 5000, should be 8000).
- `docker-compose.yml` has two services (`db` and `app`) but NO Nginx service, NO network isolation, NO app health check. Uses a container registry image (`ghcr.io/saltyreformed/shekel:latest`) instead of local build.
- `docker-compose.dev.yml` has `db`, `test-db`, and `app` services. The `app` service incorrectly uses `FLASK_ENV: production` and does not mount source code for live reload. The primary dev workflow is `docker compose -f docker-compose.dev.yml up db` + local `flask run`.
- Security headers are set by Flask in `app/__init__.py:239-254` via an `after_request` hook (5 headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Content-Security-Policy`).
- Structured JSON logging with `request_id` and `request_duration` is configured in `app/utils/logging_config.py:100-164`. The health endpoint must be excluded from these hooks.
- Authentication is per-route via `@login_required` decorators. No global `before_request` hook enforces authentication. The health endpoint will be accessible without authentication by simply omitting `@login_required`.
- No `/health` route, `health.py` Blueprint, `nginx/` directory, or `gunicorn.conf.py` exists.
- `.dockerignore` is comprehensive and needs no changes.

**New dependencies required:** None.

**Alembic migration required:** None.

---

## Pre-Existing Infrastructure

### Dockerfile (`Dockerfile`)

| Aspect | Current State | Required State | Gap |
|--------|--------------|----------------|-----|
| Base image | `python:3.14-slim` (minor pinned) | Pinned version (not `latest`) | **Compliant.** Already pinned to minor. |
| Build stages | Two-stage (builder + runtime) | Multi-stage for slim image | **Compliant.** |
| Non-root user | `shekel` user (`Dockerfile:28`) | Non-root user | **Compliant.** |
| Dependencies | Production only in builder (`Dockerfile:16-17`) | Production only | **Compliant.** |
| Gunicorn | `pip install gunicorn` in builder (`Dockerfile:17`) | Gunicorn available | **Compliant.** |
| Exposed port | `EXPOSE 5000` (`Dockerfile:44`) | `EXPOSE 8000` | **Gap: change to 8000.** |
| HEALTHCHECK | Not present | Must point to `/health` | **Gap: add HEALTHCHECK.** |
| DEBUG | Not set in Dockerfile | `DEBUG=False` | No gap. Controlled by `FLASK_ENV` in compose; `ProdConfig` sets `DEBUG=False` (`app/config.py:65`). |
| Entrypoint | `entrypoint.sh` → inline gunicorn args (`entrypoint.sh:38-44`) | Gunicorn via config file | **Gap: update invocation.** |
| CMD | Not present (entrypoint `exec`s gunicorn directly) | Separate CMD for dev override | **Gap: restructure ENTRYPOINT/CMD.** |

### `entrypoint.sh`

Current gunicorn invocation (`entrypoint.sh:38-44`):

```bash
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --access-logfile "" \
    --error-logfile - \
    --log-level info \
    run:app
```

Issues:
1. Port 5000 instead of 8000.
2. No config file reference (inline args only).
3. Gunicorn is `exec`'d directly, preventing CMD override in dev compose.
4. `--access-logfile ""` disables access log (actually correct -- Flask middleware handles request logging -- but should be documented in config file).

### `docker-compose.yml` (Production)

Current services: `db` (postgres:16-alpine), `app` (ghcr.io/saltyreformed/shekel:latest).

| Aspect | Current State | Required State | Gap |
|--------|--------------|----------------|-----|
| Services | db, app (2 services) | db, app, nginx (3 services) | **Gap: add nginx.** |
| App image | `ghcr.io/saltyreformed/shekel:latest` | `build: .` (no CI/CD yet) | **Gap: switch to local build.** |
| App port | `${APP_PORT:-5000}:5000` | No external port (Nginx proxies) | **Gap: remove external mapping.** |
| App health check | Not present | Health check on `/health` | **Gap: add health check.** |
| Network isolation | Not present (default bridge) | Internal + external networks | **Gap: add networks.** |
| Static volume | Not present | Shared volume for Nginx | **Gap: add static volume.** |
| Nginx service | Not present | Reverse proxy + static serving | **Gap: add nginx service.** |

### `docker-compose.dev.yml` (Development)

Current services: `db`, `test-db`, `app`.

| Aspect | Current State | Required State | Gap |
|--------|--------------|----------------|-----|
| App `FLASK_ENV` | `production` (`docker-compose.dev.yml:55`) | `development` | **Gap: fix FLASK_ENV.** |
| Source volume | Not mounted | Volume mount for live reload | **Gap: add volume mount.** |
| App command | Uses `entrypoint.sh` → Gunicorn | Flask dev server | **Gap: override command.** |
| Debug mode | Not enabled (uses production config) | Debug enabled | **Gap: fix via FLASK_ENV + FLASK_DEBUG.** |
| `TOTP_ENCRYPTION_KEY` | Not set | Must be set (required by MFA) | **Gap: add env var.** |
| Container name | `shekel-app` (conflicts with production) | Unique name | **Gap: rename to `shekel-app-dev`.** |
| Nginx | Not present | Not present (correct for dev) | **Compliant.** |

### `.dockerignore`

Complete and well-maintained. Excludes `.git`, `.env`, `__pycache__`, `.pytest_cache`, `.venv`, `tests/`, `logs/`, `docs/`, `*.md`, `.github/`, `.vscode/`, `docker-compose*.yml`, `Dockerfile`, `.dockerignore`, `.pylintrc`. **No changes needed.**

### Security Headers -- Overlap Analysis

**Flask currently sets** (in `app/__init__.py:239-254` via `_register_security_headers`):

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'` |

**Master plan says Nginx should set:**

| Header | Currently in Flask? | Risk |
|--------|-------------------|------|
| `X-Content-Type-Options` | **Yes** | Duplicate -- harmless but untidy |
| `X-Frame-Options` | **Yes** | Duplicate -- harmless but untidy |
| `X-XSS-Protection` | No | Deprecated header, ignored by modern browsers |
| `Strict-Transport-Security` | No | Transport-level, but Cloudflare handles TLS |
| `Content-Security-Policy` | **Yes** | **DANGEROUS: duplicate CSP headers break pages** |

**Duplication risk for CSP:** Per the CSP specification, when a browser receives multiple `Content-Security-Policy` headers, it enforces ALL of them (intersection). This is almost always stricter than intended and will likely break the application (e.g., blocking CDN scripts or inline styles that only one policy allows). This is the most critical overlap to prevent.

**Headers Flask sets that the Nginx plan does not mention:**
- `Referrer-Policy` -- Flask only
- `Permissions-Policy` -- Flask only

### Structured Logging (`app/utils/logging_config.py`)

**JSON format** (`logging_config.py:78-86`):

```python
"formatters": {
    "json": {
        "class": "pythonjsonlogger.json.JsonFormatter",
        "format": "%(levelname)s %(name)s %(message)s",
        "rename_fields": {"levelname": "level", "name": "logger"},
        "timestamp": True,
    },
},
```

Fields in every log entry: `timestamp`, `level`, `logger`, `message`, `request_id`.

**Request tracking hooks** (`logging_config.py:100-164`):
- `_attach_request_id()` (before_request, line 100): Generates UUID `request_id`, stores `request_start` time, sets `SET LOCAL app.current_user_id` for audit triggers.
- `_log_request_summary()` (after_request, line 122): Logs `method`, `path`, `status`, `request_duration`, `remote_addr`, `user_id`, `event`, `category`. Adds `X-Request-Id` response header.

**Health endpoint exclusion points:**
- `_attach_request_id()` (line 100): Add early return for `/health` to skip `request_id` generation and audit `user_id` propagation.
- `_log_request_summary()` (line 122): Add early return for `/health` to skip request logging and `X-Request-Id` header.

### Flask Authentication Enforcement

Authentication is enforced per-route via `@login_required` decorators across all 16 route Blueprints. No global `before_request` hook blocks unauthenticated access. Verified by:

1. `app/__init__.py`: No `before_request` hooks related to authentication.
2. `app/extensions.py:24`: `login_manager.login_view = "auth.login"` only triggers on `@login_required` routes.
3. All route files use per-route `@login_required` (verified via grep across `app/routes/`).

The health endpoint will be accessible without authentication simply by omitting `@login_required`.

### CSRF and Health Endpoint

`CSRFProtect` is initialized globally (`app/extensions.py:28`). However, CSRF validation only applies to POST/PUT/PATCH/DELETE requests, not GET. The health endpoint is GET-only, so no CSRF exemption is needed.

### Audit Trigger Interaction

The health endpoint executes `SELECT 1`, which does not touch any audited table. Audit triggers are attached to INSERT/UPDATE/DELETE operations on `budget.*`, `salary.*`, and `auth.*` tables. No audit log writes will be generated by health checks. **Confirmed: no exclusion needed.**

### Shell Script Conventions (from 8C scripts)

Scripts in `scripts/` follow these conventions:
- Shebang: `#!/bin/bash`
- Error handling: `set -euo pipefail`
- Logging function: `log() { local level="$1"; shift; echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"; }`
- Configuration section with env var defaults
- Functions for each major step
- `main()` function with argument parsing
- Comment headers with usage, options, exit codes, cron examples

---

## Security Header Ownership

**Decision: Flask retains ownership of all security headers. Nginx sets none on proxied responses.**

### Analysis

**(a) Nginx owns all headers, Flask removes its hook in production.**
- Requires conditional logic in Flask to detect Nginx (env var or proxy header check).
- Development (no Nginx) loses all security headers unless carefully implemented.
- Risk: Configuration drift between environments.

**(b) Flask keeps its headers, Nginx adds only headers Flask does not set.**
- Nginx would add `Strict-Transport-Security` and `X-XSS-Protection`.
- **HSTS is inappropriate here:** Nginx does NOT terminate TLS. Cloudflare Tunnel handles TLS (configured in 8D-3). Setting HSTS at Nginx would be misleading and incorrect.
- `X-XSS-Protection` is deprecated (Chrome removed support in 2019, other browsers followed).
- Result: Nginx has **zero** headers to add on proxied responses.

**(c) Nginx sets all headers, Flask detects and skips.**
- Same complexity as (a) with no benefit.

### Recommendation: Option (b), simplified

Flask continues to set all security headers via its `after_request` hook. **Nginx sets no security headers on proxied responses.** This is correct because:

1. **No duplication risk.** Flask is the single source of truth. CSP is never duplicated.
2. **Development parity.** Headers work identically in dev (no Nginx) and production (behind Nginx).
3. **No conditional logic needed.** No environment detection, no config flags.
4. **HSTS is not Nginx's job.** Cloudflare handles TLS termination and HSTS at the edge.
5. **X-XSS-Protection is deprecated.** Not worth adding anywhere.

**For Nginx-served static files** (`/static/`): These responses bypass Flask entirely. Nginx adds `X-Content-Type-Options: nosniff` in the `/static/` location block only. This is the only security header Nginx sets, and only for static files that Flask never touches.

### Changes Required

- **`app/__init__.py`**: **No changes.** `_register_security_headers()` remains as-is.
- **`nginx/nginx.conf`**: Static location block includes `add_header X-Content-Type-Options nosniff;`. No security headers on the `location /` proxy block.

---

## Docker Compose Structure

**Decision: Standalone files. `docker-compose.yml` for production, `docker-compose.dev.yml` for development.**

### Analysis

**(a) Override pattern:** `docker-compose.yml` (base) + `docker-compose.dev.yml` (override). Run with `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`.
- Requires careful factoring of shared vs. overridden settings.
- Dev and production environments are substantially different (different services, different commands, different env vars).
- Override files cannot remove services defined in the base (e.g., cannot remove Nginx for dev).

**(b) Standalone files:** Each file is self-contained.
- Simple mental model: one file per environment.
- No accidental inheritance of production settings in dev.
- This is the current pattern and works well.

**(c) Profile-based:** Single compose file with `--profile dev` / `--profile prod`.
- Mixes production and development config in one file, reducing readability.

### Recommendation: Option (b), standalone files

This matches the current pattern. Each compose file is self-contained and purpose-built.

### Commands

**Production:**

```bash
# Start all production services
docker compose up -d

# Rebuild after code changes
docker compose build && docker compose up -d

# View logs
docker compose logs -f app
```

**Development:**

```bash
# Start database only (primary dev workflow)
docker compose -f docker-compose.dev.yml up db

# Start database + test database
docker compose -f docker-compose.dev.yml up db test-db

# Full stack (containerized dev app with live reload)
docker compose -f docker-compose.dev.yml up

# Then run Flask locally (connects to containerized Postgres)
flask run
```

---

## Static File Serving in Nginx

**Decision: Shared named volume populated by the app container at startup.**

### Analysis

**(a) Shared named volume:** App copies static files to a named volume during `entrypoint.sh`. Nginx mounts the same volume read-only.
- Works with pre-built images and local builds.
- Files are updated on every container restart.
- Small overhead (one `cp -r` during startup).

**(b) Multi-stage Nginx build:** Build a custom Nginx Dockerfile that copies static files from the app build stage using `COPY --from`.
- Tightly couples Nginx and app builds.
- Unnecessarily complex for a compose setup.

**(c) Host bind mount:** Mount `./app/static` from the host into Nginx.
- Only works when source code is on the host (not with pre-built images).
- Fragile if the app directory structure changes.

### Recommendation: Option (a), shared named volume

**Implementation:**

1. In `entrypoint.sh` (before server start): `cp -r /home/shekel/app/app/static/* /var/www/static/`
2. In `Dockerfile`: `RUN mkdir -p /var/www/static && chown shekel:shekel /var/www/static`
3. In `docker-compose.yml`:

```yaml
volumes:
  static_files:

services:
  app:
    volumes:
      - static_files:/var/www/static
  nginx:
    volumes:
      - static_files:/var/www/static:ro
```

4. Nginx serves `/static/` from `/var/www/static/`.

**Startup sequence guarantees correctness:**
1. Docker creates empty `static_files` volume.
2. App container starts; `entrypoint.sh` copies static files to `/var/www/static/`.
3. App starts Gunicorn, becomes healthy.
4. Nginx starts (`depends_on: app: condition: service_healthy`), mounts volume.
5. Nginx can now serve static files.

On subsequent restarts, the app re-copies static files, ensuring they're current after image updates.

---

## Work Units

The implementation is organized into 6 work units. Each unit leaves the app in a working state with all existing tests passing.

### Dependency Graph

```
WU-1: Health Endpoint
  |
  v
WU-2: Gunicorn Configuration
  |
  v
WU-3: Dockerfile Finalization ──────────────────┐
  |                                               |
  v                                               v
WU-4: Nginx Configuration              WU-6: Build Validation &
  |                                     Security Header Integration
  v                                               |
WU-5: docker-compose Production & Dev ────────────┘
```

WU-4 and WU-6 can be done in parallel after WU-3.

WU-5 depends on WU-3, WU-4, and implicitly on WU-1 and WU-2 (compose references their artifacts).

---

### WU-1: Health Endpoint

**Goal:** Create the `/health` Blueprint, register it, exclude it from request logging and audit user_id propagation, and add pytest tests.

**Depends on:** None. This is the foundation that Dockerfile HEALTHCHECK and docker-compose health checks depend on.

#### Files to Create

**`app/routes/health.py`** -- New Blueprint with a single GET `/health` route.

```python
"""
Shekel Budget App -- Health Check Endpoint

Provides a lightweight health check for Docker HEALTHCHECK, load
balancers, and external monitoring.  Returns database connectivity
status as JSON.

This endpoint:
- Requires no authentication (external monitors must reach it).
- Is excluded from request logging (avoid noise from frequent checks).
- Does not trigger audit log writes (SELECT 1 touches no audited tables).
"""

import logging

from flask import Blueprint, jsonify

from app.extensions import db

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health_check():
    """Return application and database health status.

    Returns:
        200 JSON: {"status": "healthy", "database": "connected"}
        500 JSON: {"status": "unhealthy", "database": "error", "detail": "..."}
    """
    try:
        # Verify database connectivity with a lightweight query.
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Health check failed: %s", exc)
        return jsonify({
            "status": "unhealthy",
            "database": "error",
            "detail": str(exc),
        }), 500
```

**`tests/test_routes/test_health.py`** -- Pytest tests for the health endpoint.

```python
"""Tests for the /health endpoint."""

import logging
from unittest.mock import patch

from app.extensions import db


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200_when_healthy(self, app, client, db):
        """GET /health returns 200 with healthy status when DB is reachable."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

    def test_health_returns_json_content_type(self, app, client, db):
        """GET /health returns application/json content type."""
        response = client.get("/health")
        assert "application/json" in response.content_type

    def test_health_requires_no_authentication(self, app, client, db):
        """GET /health is accessible without login (client is not authenticated)."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_500_on_db_failure(self, app, client, db):
        """GET /health returns 500 with error details when DB is unreachable."""
        with patch.object(
            db.session, "execute", side_effect=Exception("connection refused")
        ):
            response = client.get("/health")
            assert response.status_code == 500
            data = response.get_json()
            assert data["status"] == "unhealthy"
            assert data["database"] == "error"
            assert "connection refused" in data["detail"]

    def test_health_not_logged_in_request_summary(self, app, client, db, caplog):
        """GET /health does not produce a request_complete log entry."""
        with caplog.at_level(logging.DEBUG):
            client.get("/health")
        # The request logging middleware skips /health.
        request_logs = [
            r for r in caplog.records
            if hasattr(r, "event")
            and r.event in ("request_complete", "slow_request")
            and hasattr(r, "path")
            and r.path == "/health"
        ]
        assert len(request_logs) == 0

    def test_health_no_request_id_header(self, app, client, db):
        """GET /health does not return X-Request-Id header (logging skipped)."""
        response = client.get("/health")
        assert "X-Request-Id" not in response.headers
```

#### Files to Modify

**`app/__init__.py`** -- Register the health Blueprint in `_register_blueprints()`.

Add import after `from app.routes.charts import charts_bp` (line 194):

```python
    from app.routes.health import health_bp
```

Add registration after `app.register_blueprint(charts_bp)` (line 211):

```python
    app.register_blueprint(health_bp)
```

**`app/utils/logging_config.py`** -- Exclude `/health` from request logging hooks.

**Change 1:** Modify `_attach_request_id()` (line 100). Add early return for health checks.

Current (`logging_config.py:100-117`):

```python
    @app.before_request
    def _attach_request_id():
        g.request_id = str(uuid.uuid4())
        g.request_start = time.perf_counter()

        # Propagate the application user_id into the PostgreSQL session
        # so audit triggers can capture who made the change.
        # Uses SET LOCAL (transaction-scoped, not session-scoped).
        try:
            from flask_login import current_user  # pylint: disable=import-outside-toplevel
            if current_user.is_authenticated:
                from app.extensions import db  # pylint: disable=import-outside-toplevel
                db.session.execute(
                    db.text("SET LOCAL app.current_user_id = :uid"),
                    {"uid": str(current_user.id)},
                )
        except Exception:  # pylint: disable=broad-except
            pass
```

New:

```python
    @app.before_request
    def _attach_request_id():
        # Skip request tracking for health checks (avoid log noise
        # from frequent Docker/monitoring polls).
        if request.path == "/health":
            g.skip_request_logging = True
            return
        g.skip_request_logging = False

        g.request_id = str(uuid.uuid4())
        g.request_start = time.perf_counter()

        # Propagate the application user_id into the PostgreSQL session
        # so audit triggers can capture who made the change.
        # Uses SET LOCAL (transaction-scoped, not session-scoped).
        try:
            from flask_login import current_user  # pylint: disable=import-outside-toplevel
            if current_user.is_authenticated:
                from app.extensions import db  # pylint: disable=import-outside-toplevel
                db.session.execute(
                    db.text("SET LOCAL app.current_user_id = :uid"),
                    {"uid": str(current_user.id)},
                )
        except Exception:  # pylint: disable=broad-except
            pass
```

**Change 2:** Modify `_log_request_summary()` (line 122). Add early return for skipped requests.

Current (`logging_config.py:122-124`):

```python
    @app.after_request
    def _log_request_summary(response):
        duration_ms = (time.perf_counter() - g.request_start) * 1000
```

New:

```python
    @app.after_request
    def _log_request_summary(response):
        # Skip logging for health checks and other excluded paths.
        if getattr(g, "skip_request_logging", False):
            return response

        duration_ms = (time.perf_counter() - g.request_start) * 1000
```

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] `GET /health` returns 200 with `{"status": "healthy", "database": "connected"}`
- [ ] `GET /health` is accessible without authentication
- [ ] `GET /health` returns 500 with error details when database is unreachable
- [ ] Health check requests do not appear in request logging
- [ ] Health check responses do not include `X-Request-Id` header

#### Impact on Existing Tests

The `skip_request_logging` flag defaults to `False` via `getattr(g, "skip_request_logging", False)`. All existing tests continue to have full request logging. The health Blueprint registration is additive and does not affect other routes. **No existing tests need modification.**

---

### WU-2: Gunicorn Configuration

**Goal:** Create `gunicorn.conf.py` with production-appropriate settings. Update `entrypoint.sh` to use the config file, change the ENTRYPOINT/CMD pattern, and bind to port 8000.

**Depends on:** None (can be done in parallel with WU-1, but listed second for logical ordering since WU-3 depends on it).

#### Files to Create

**`gunicorn.conf.py`** -- Complete Gunicorn configuration file.

```python
"""
Shekel Budget App -- Gunicorn Configuration

Production WSGI server settings for running behind Nginx.
All values can be overridden via environment variables.

Usage:
    gunicorn --config gunicorn.conf.py run:app
"""

import os


# ── Binding ──────────────────────────────────────────────────────
# Listen on all interfaces, port 8000.  Nginx reverse-proxies to
# this port.  Not exposed externally -- only reachable on the Docker
# backend network.
bind = f"0.0.0.0:{os.getenv('GUNICORN_PORT', '8000')}"

# ── Workers ──────────────────────────────────────────────────────
# Number of worker processes.  2 is appropriate for a single-user
# personal finance app on modest hardware (Proxmox VM).
# Formula for higher load: (2 * CPU cores) + 1.
workers = int(os.getenv("GUNICORN_WORKERS", "2"))

# ── Timeouts ─────────────────────────────────────────────────────
# Seconds to wait for a worker to finish handling a request.
# 120s accommodates slow operations like 2-year recurrence
# regeneration.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))

# Seconds to wait for a worker to gracefully shut down after
# receiving a restart signal (SIGHUP).
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "120"))

# Seconds to wait for the next request on a Keep-Alive connection.
# Slightly higher than Nginx's keepalive_timeout (65s) to let Nginx
# close idle connections first, avoiding race conditions.
keepalive = 5

# ── Logging ──────────────────────────────────────────────────────
# Access log: DISABLED.  Flask's after_request middleware in
# app/utils/logging_config.py already logs every request with
# structured JSON fields (request_id, duration, user_id, method,
# path, status).  Enabling Gunicorn's access log would produce
# duplicate request entries in container stdout.
accesslog = None

# Error log: sent to stderr, captured by Docker as container logs.
# Covers startup messages, worker lifecycle events, and unhandled
# exceptions.  Low-volume output.
errorlog = "-"

# Log level for Gunicorn's own process-level messages.
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# ── Process Naming ───────────────────────────────────────────────
# Identifies the master and worker processes in `ps` output.
proc_name = "shekel"

# ── Request Limits ───────────────────────────────────────────────
# Maximum size of the HTTP request line (URL + query string).
limit_request_line = 8190

# Maximum number of HTTP request headers.
limit_request_fields = 100

# Maximum size of a single HTTP request header.
limit_request_field_size = 8190

# ── Forwarded Headers ────────────────────────────────────────────
# Trust X-Forwarded-* headers from Nginx.  Required for correct
# remote_addr logging and HTTPS detection behind a reverse proxy.
forwarded_allow_ips = "*"
```

#### Files to Modify

**`entrypoint.sh`** -- Restructure to use ENTRYPOINT/CMD pattern and remove inline gunicorn args.

Current ending (`entrypoint.sh:37-44`):

```bash
echo "=== Starting Gunicorn ==="
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --access-logfile "" \
    --error-logfile - \
    --log-level info \
    run:app
```

New ending:

```bash
# ── 7. Copy static files to shared volume ────────────────────────
# Nginx serves /static/ directly from this volume.  Copying on every
# start ensures files are current after image updates.
echo "Copying static files to shared volume..."
cp -r /home/shekel/app/app/static/* /var/www/static/ 2>/dev/null || true
echo "Static files ready."

# ── 8. Start the application server ──────────────────────────────
# exec "$@" runs the Dockerfile CMD (gunicorn in production, or the
# docker-compose command override in development).
echo "=== Starting Application ==="
exec "$@"
```

The `exec "$@"` pattern delegates the server command to the Dockerfile CMD, which can be overridden by docker-compose. The `2>/dev/null || true` guard on the static file copy handles the case where the shared volume is not mounted (standalone container without docker-compose).

**`Dockerfile`** -- Add CMD instruction after ENTRYPOINT.

Current (`Dockerfile:46`):

```dockerfile
ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
```

New (`Dockerfile:46-47`):

```dockerfile
ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
```

#### Test Gate

- [ ] `gunicorn.conf.py` is valid Python (no syntax errors)
- [ ] `entrypoint.sh` ends with `exec "$@"` (ENTRYPOINT/CMD pattern)
- [ ] Gunicorn binds to port 8000 (verified in WU-5 docker-compose testing)
- [ ] Flask request logging continues to function (Gunicorn access log disabled, Flask middleware handles it)

#### Impact on Existing Tests

None. Tests use the Flask test client, not Gunicorn. The `gunicorn.conf.py` file is only used in production Docker containers.

---

### WU-3: Dockerfile Finalization

**Goal:** Update the Dockerfile to expose port 8000, add a HEALTHCHECK instruction, create the shared static files directory, and add the CMD instruction.

**Depends on:** WU-1 (health endpoint must exist for HEALTHCHECK), WU-2 (gunicorn.conf.py and entrypoint.sh changes).

#### Files to Modify

**`Dockerfile`** -- Four changes.

**Change 1:** Update EXPOSE from 5000 to 8000.

Current (`Dockerfile:44`):

```dockerfile
EXPOSE 5000
```

New:

```dockerfile
EXPOSE 8000
```

**Change 2:** Create the shared static files directory.

Current (`Dockerfile:39-41`):

```dockerfile
# Ensure writable directories exist (logs is excluded by .dockerignore).
RUN mkdir -p /home/shekel/app/logs \
    && chown -R shekel:shekel /home/shekel/app
```

New:

```dockerfile
# Ensure writable directories exist for logs and shared static files.
# /var/www/static is a shared volume mount point -- Nginx reads from it.
RUN mkdir -p /home/shekel/app/logs /var/www/static \
    && chown -R shekel:shekel /home/shekel/app /var/www/static
```

**Change 3:** Add HEALTHCHECK after EXPOSE.

Add after `EXPOSE 8000`:

```dockerfile
# Health check: verify the app is responding and database is reachable.
# Uses Python's built-in urllib (curl/wget are not in the slim image).
# --start-period gives entrypoint.sh time to run migrations and seeding.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
```

**Change 4:** Add CMD instruction (from WU-2).

Current (`Dockerfile:46`):

```dockerfile
ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
```

New:

```dockerfile
ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
```

#### Complete Dockerfile (after all changes)

```dockerfile
# Shekel Budget App -- Multi-Stage Dockerfile
# Stage 1: Build Python dependencies (includes gcc for psycopg2).
# Stage 2: Slim runtime image (no build tools).

# ── Stage 1: Builder ────────────────────────────────────────────
FROM python:3.14-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# ── Stage 2: Runtime ────────────────────────────────────────────
FROM python:3.14-slim

# Runtime-only PostgreSQL client library + CLI tools for entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user.
RUN useradd --create-home shekel
WORKDIR /home/shekel/app

# Copy virtualenv from builder.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code and entrypoint.
COPY --chown=shekel:shekel . .
COPY --chown=shekel:shekel entrypoint.sh /home/shekel/app/entrypoint.sh

# Ensure writable directories exist for logs and shared static files.
# /var/www/static is a shared volume mount point -- Nginx reads from it.
RUN mkdir -p /home/shekel/app/logs /var/www/static \
    && chown -R shekel:shekel /home/shekel/app /var/www/static

USER shekel
EXPOSE 8000

# Health check: verify the app is responding and database is reachable.
# Uses Python's built-in urllib (curl/wget are not in the slim image).
# --start-period gives entrypoint.sh time to run migrations and seeding.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
```

#### Complete `entrypoint.sh` (after WU-2 and WU-3 changes)

```bash
#!/bin/bash
set -e

echo "=== Shekel Entrypoint ==="

# ── 1. Wait for PostgreSQL ──────────────────────────────────────
echo "Waiting for PostgreSQL..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
    sleep 1
done
echo "PostgreSQL is ready."

# ── 2. Create schemas ──────────────────────────────────────────
echo "Creating database schemas..."
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -d "${DB_NAME:-shekel}" -f scripts/init_db.sql -q
echo "Schemas ready."

# ── 3. Initialize database (fresh) or run migrations (existing) ─
echo "Initializing database..."
python scripts/init_database.py

# ── 4. Seed reference data ─────────────────────────────────────
echo "Seeding reference data..."
python scripts/seed_ref_tables.py

# ── 5. Create seed user (first run only) ───────────────────────
if [ -n "$SEED_USER_EMAIL" ]; then
    echo "Checking for seed user..."
    python scripts/seed_user.py
fi

# ── 6. Seed tax brackets (requires users to exist) ─────────────
echo "Seeding tax configuration..."
python scripts/seed_tax_brackets.py
echo "Seeding complete."

# ── 7. Copy static files to shared volume ────────────────────────
# Nginx serves /static/ directly from this volume.  Copying on every
# start ensures files are current after image updates.
echo "Copying static files to shared volume..."
cp -r /home/shekel/app/app/static/* /var/www/static/ 2>/dev/null || true
echo "Static files ready."

# ── 8. Start the application server ──────────────────────────────
# exec "$@" runs the Dockerfile CMD (gunicorn in production, or the
# docker-compose command override in development).
echo "=== Starting Application ==="
exec "$@"
```

#### Test Gate

- [ ] `docker build .` succeeds
- [ ] Container starts and Gunicorn binds to port 8000
- [ ] HEALTHCHECK passes after start-period (container shows `healthy` in `docker ps`)
- [ ] `/var/www/static/` is populated with static files inside the container

#### Impact on Existing Tests

None. Tests use the Flask test client and do not interact with Docker.

---

### WU-4: Nginx Configuration

**Goal:** Create the Nginx configuration file for reverse-proxying to Gunicorn, serving static files directly, gzip compression, request size limits, and connection timeouts.

**Depends on:** WU-3 (static file volume and port 8000 must be defined).

#### Files to Create

**`nginx/nginx.conf`** -- Complete Nginx configuration.

```nginx
# Shekel Budget App -- Nginx Reverse Proxy Configuration
#
# Nginx sits in front of Gunicorn and handles:
#   - Reverse proxying application requests to Gunicorn (port 8000)
#   - Serving static files directly (bypassing Flask/Gunicorn)
#   - Gzip compression for text-based responses
#   - Request size limits and connection timeouts
#
# TLS is NOT terminated here.  Cloudflare Tunnel handles TLS
# (configured in Phase 8D-3).  Nginx listens on HTTP only and
# receives traffic from cloudflared on the local Docker network.

# ── Worker Configuration ─────────────────────────────────────────
# auto = one worker per CPU core.  Suitable for a low-traffic
# personal finance app.
worker_processes auto;

# Maximum number of simultaneous connections per worker.
events {
    worker_connections 1024;
}

http {
    # ── MIME Types ────────────────────────────────────────────────
    # Load standard MIME type mappings so Nginx sets correct
    # Content-Type headers for static files (CSS, JS, images, fonts).
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    # ── Logging ──────────────────────────────────────────────────
    # JSON log format for consistency with the application's
    # structured logging (Phase 8B).  Fields align with Flask's
    # request logging where applicable.
    log_format json_combined escape=json
        '{'
            '"timestamp":"$time_iso8601",'
            '"remote_addr":"$remote_addr",'
            '"method":"$request_method",'
            '"path":"$uri",'
            '"status":$status,'
            '"body_bytes_sent":$body_bytes_sent,'
            '"request_time":$request_time,'
            '"http_referer":"$http_referer",'
            '"http_user_agent":"$http_user_agent",'
            '"upstream_response_time":"$upstream_response_time"'
        '}';

    # Send access logs to stdout (captured by Docker as container logs).
    access_log /dev/stdout json_combined;

    # Send error logs to stderr (captured by Docker as container logs).
    error_log  /dev/stderr warn;

    # ── Performance ──────────────────────────────────────────────
    # Use sendfile for efficient static file serving (kernel-level copy).
    sendfile on;

    # Wait to send data until a full packet is ready (reduces
    # small-packet overhead).
    tcp_nopush on;

    # Disable Nagle's algorithm for low-latency responses.
    tcp_nodelay on;

    # ── Gzip Compression ─────────────────────────────────────────
    # Compress text-based responses to reduce bandwidth.
    gzip on;

    # Minimum response size to compress (skip tiny responses).
    gzip_min_length 1024;

    # Compression level (1-9).  6 balances CPU usage vs compression
    # ratio.
    gzip_comp_level 6;

    # MIME types to compress.  Binary formats (images, fonts) are
    # already compressed and should not be gzipped.
    gzip_types
        text/plain
        text/css
        text/javascript
        application/javascript
        application/json
        application/xml
        text/xml;

    # ── Timeouts ─────────────────────────────────────────────────
    # Time to keep an idle client connection open.
    keepalive_timeout 65;

    # Time to wait for the client to send the request body.
    client_body_timeout 30;

    # Time to wait for the client to send request headers.
    client_header_timeout 30;

    # Time to wait for the response to be fully sent to the client.
    send_timeout 30;

    # ── Request Size Limits ──────────────────────────────────────
    # Maximum allowed size of the client request body.  Shekel does
    # not handle file uploads; this limit prevents oversized POST
    # payloads.
    client_max_body_size 5m;

    # ── Upstream (Gunicorn) ──────────────────────────────────────
    # Define the Gunicorn backend.  The hostname "app" is resolved by
    # Docker's internal DNS to the app container's IP address.
    upstream gunicorn {
        server app:8000;
    }

    # ── Server Block ─────────────────────────────────────────────
    server {
        # Listen on port 80 (HTTP only).  TLS is handled by
        # Cloudflare Tunnel (Phase 8D-3).
        listen 80;
        server_name _;

        # ── Static Files ─────────────────────────────────────────
        # Serve static files directly from the shared volume,
        # bypassing Gunicorn.  Faster and reduces app server load.
        location /static/ {
            alias /var/www/static/;

            # Cache static assets in the browser for 7 days.
            expires 7d;
            add_header Cache-Control "public, immutable";

            # Prevent MIME type sniffing on static files.  This is
            # the only security header Nginx sets -- Flask owns all
            # others on proxied responses (see Security Header
            # Ownership decision in the implementation plan).
            add_header X-Content-Type-Options nosniff;

            # Disable access logging for static files (reduces noise).
            access_log off;
        }

        # ── Application Proxy ────────────────────────────────────
        # Forward all other requests to Gunicorn.
        location / {
            proxy_pass http://gunicorn;

            # Pass the original client IP and protocol to the app.
            # Required for correct remote_addr in Flask request
            # logging and for secure cookie decisions behind a proxy.
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # Timeout for connecting to the upstream (Gunicorn).
            proxy_connect_timeout 10;

            # Timeout for reading the response from the upstream.
            # 120s accommodates slow operations (recurrence
            # regeneration across a 2-year horizon).
            proxy_read_timeout 120;

            # Timeout for sending the request to the upstream.
            proxy_send_timeout 120;

            # Disable buffering for HTMX streaming responses.
            proxy_buffering off;
        }
    }
}
```

#### Test Gate

- [ ] `nginx -t` passes syntax check (verified in WU-5 via docker-compose)
- [ ] Static files served by Nginx (response has no `X-Request-Id`, has `Cache-Control`)
- [ ] Application requests proxied to Gunicorn (response has `X-Request-Id`)
- [ ] Gzip compression active for text/html responses
- [ ] Requests larger than 5MB rejected with 413 status

#### Impact on Existing Tests

None. Nginx is not used in the test environment.

---

### WU-5: docker-compose Production and Development

**Goal:** Finalize `docker-compose.yml` with Nginx, network isolation, health checks, and static file volume. Fix `docker-compose.dev.yml` for proper development workflow with live reload, debug mode, and no Nginx.

**Depends on:** WU-1 (health endpoint), WU-2 (gunicorn.conf.py, ENTRYPOINT/CMD pattern), WU-3 (Dockerfile with port 8000 and HEALTHCHECK), WU-4 (nginx.conf).

#### Files to Modify

**`docker-compose.yml`** -- Complete rewrite for production with Nginx.

```yaml
# Shekel Budget App -- Production Docker Compose
#
# Architecture:
#   [Client] --> [Nginx :80] --> [Gunicorn :8000] --> [PostgreSQL :5432]
#
# Networks:
#   frontend: Nginx (externally accessible)
#   backend:  Nginx + App + DB (internal only)
#
# Quick start:
#   1. Copy .env.example to .env and fill in values
#   2. docker compose up -d
#   3. Open http://localhost (via Nginx)
#
# Update:
#   docker compose build && docker compose up -d

services:
  # ── PostgreSQL Database ──────────────────────────────────────────
  db:
    image: postgres:16-alpine
    container_name: shekel-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: shekel_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: shekel
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks:
      - backend
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shekel_user -d shekel"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ── Flask Application (Gunicorn) ─────────────────────────────────
  app:
    build: .
    container_name: shekel-app
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      FLASK_ENV: production
      SECRET_KEY: ${SECRET_KEY:?Set SECRET_KEY in .env}
      DATABASE_URL: postgresql://shekel_user:${POSTGRES_PASSWORD}@db:5432/shekel
      DB_HOST: db
      DB_USER: shekel_user
      DB_PASSWORD: ${POSTGRES_PASSWORD}
      DB_NAME: shekel
      SEED_USER_EMAIL: ${SEED_USER_EMAIL:-}
      SEED_USER_PASSWORD: ${SEED_USER_PASSWORD:-}
      SEED_USER_DISPLAY_NAME: ${SEED_USER_DISPLAY_NAME:-Budget Admin}
      GUNICORN_WORKERS: ${GUNICORN_WORKERS:-2}
      TOTP_ENCRYPTION_KEY: ${TOTP_ENCRYPTION_KEY:?Set TOTP_ENCRYPTION_KEY in .env}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      SLOW_REQUEST_THRESHOLD_MS: ${SLOW_REQUEST_THRESHOLD_MS:-500}
      AUDIT_RETENTION_DAYS: ${AUDIT_RETENTION_DAYS:-365}
    volumes:
      - applogs:/home/shekel/app/logs
      # Shared volume: app copies static files here for Nginx to serve.
      - static_files:/var/www/static
    networks:
      - backend
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      start_period: 30s
      retries: 3

  # ── Nginx Reverse Proxy ──────────────────────────────────────────
  nginx:
    image: nginx:1.27-alpine
    container_name: shekel-nginx
    restart: unless-stopped
    depends_on:
      app:
        condition: service_healthy
    ports:
      # External access: host port 80 → Nginx port 80.
      # Override with NGINX_PORT in .env if port 80 is in use.
      - "${NGINX_PORT:-80}:80"
    volumes:
      # Nginx configuration file (read-only).
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      # Static files served directly by Nginx (populated by app).
      - static_files:/var/www/static:ro
    networks:
      - frontend
      - backend
    healthcheck:
      test: ["CMD-SHELL", "wget -qO /dev/null http://localhost/health || exit 1"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3

volumes:
  # PostgreSQL data persistence.
  pgdata:
  # Application log files.
  applogs:
  # Static files shared between app and Nginx containers.
  static_files:

networks:
  # Frontend network: Nginx only.  Externally accessible via port mapping.
  frontend:
    driver: bridge
  # Backend network: all services.  Internal only -- not reachable from host.
  backend:
    driver: bridge
    internal: true
```

**`docker-compose.dev.yml`** -- Fix for proper development workflow.

Current state: `app` service uses `FLASK_ENV: production`, no source mount, no live reload, no `TOTP_ENCRYPTION_KEY`, conflicting container name.

```yaml
# Shekel Budget App -- Development Docker Compose
#
# Provides PostgreSQL for local development.
#
# Primary workflow (recommended):
#   1. docker compose -f docker-compose.dev.yml up db
#   2. flask run    (from host, connects to containerized Postgres)
#
# Full stack (containerized dev app with live reload):
#   docker compose -f docker-compose.dev.yml up
#
# Both workflows:
#   docker compose -f docker-compose.dev.yml up db test-db

services:
  db:
    image: postgres:16-alpine
    container_name: shekel-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: shekel_user
      POSTGRES_PASSWORD: shekel_pass
      POSTGRES_DB: shekel
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shekel_user -d shekel"]
      interval: 10s
      timeout: 5s
      retries: 5

  test-db:
    image: postgres:16-alpine
    container_name: shekel-test-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: shekel_user
      POSTGRES_PASSWORD: shekel_pass
      POSTGRES_DB: shekel_test
    ports:
      - "5433:5432"
    volumes:
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shekel_user -d shekel_test"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build: .
    container_name: shekel-app-dev
    depends_on:
      db:
        condition: service_healthy
    environment:
      FLASK_APP: run.py
      FLASK_ENV: development
      FLASK_DEBUG: "1"
      SECRET_KEY: dev-secret-key-not-for-production
      DATABASE_URL: postgresql://shekel_user:shekel_pass@db:5432/shekel
      DB_HOST: db
      DB_USER: shekel_user
      DB_PASSWORD: shekel_pass
      DB_NAME: shekel
      SEED_USER_EMAIL: admin@shekel.local
      SEED_USER_PASSWORD: changeme
      SEED_USER_DISPLAY_NAME: Budget Admin
      TOTP_ENCRYPTION_KEY: ${TOTP_ENCRYPTION_KEY:-}
    ports:
      # Flask dev server on port 5000, mapped directly to host.
      # No Nginx in development.
      - "5000:5000"
    volumes:
      # Mount source code for live reload.  Flask's reloader detects
      # file changes and restarts automatically.
      - .:/home/shekel/app
    # Override: use Flask dev server instead of Gunicorn.
    # The entrypoint.sh still runs (DB init, seeding), then exec's
    # this command instead of the Dockerfile CMD (gunicorn).
    command: ["flask", "run", "--host=0.0.0.0", "--port=5000"]

volumes:
  pgdata:
```

**Key changes to `docker-compose.dev.yml`:**

| Change | Before | After | Rationale |
|--------|--------|-------|-----------|
| `FLASK_ENV` | `production` | `development` | Enables debug mode, dev config |
| `FLASK_DEBUG` | not set | `"1"` | Explicit debug flag for Flask 3.x |
| `FLASK_APP` | not set | `run.py` | Required for `flask run` command |
| `TOTP_ENCRYPTION_KEY` | not set | `${TOTP_ENCRYPTION_KEY:-}` | Prevents crash on MFA operations; MFA is optional in dev |
| Container name | `shekel-app` | `shekel-app-dev` | Avoids conflict with production container |
| `restart` | `unless-stopped` | removed | Dev app should not auto-restart on crash |
| Source volume | not mounted | `.:/home/shekel/app` | Enables live reload |
| Command | (default entrypoint) | `flask run --host=0.0.0.0 --port=5000` | Flask dev server with auto-reload instead of Gunicorn |
| Port | `5000:5000` | `5000:5000` | Direct access, no Nginx |

#### Test Gate

- [ ] `docker compose up -d` starts all 3 production services (db, app, nginx)
- [ ] `docker compose ps` shows all services as `healthy`
- [ ] App is reachable via Nginx at `http://localhost/` (or configured `NGINX_PORT`)
- [ ] `/health` returns 200 via Nginx
- [ ] Static files served by Nginx (verify with `curl -I http://localhost/static/css/base.css` -- response has `Server: nginx` header, no `X-Request-Id`)
- [ ] App not directly reachable from host (no port mapping on app service)
- [ ] `docker compose -f docker-compose.dev.yml up db` starts only the database
- [ ] `docker compose -f docker-compose.dev.yml up` starts db + app with Flask dev server
- [ ] Dev app has live reload (change a Python file, see restart in logs)
- [ ] Dev app is accessible at `http://localhost:5000`

#### Impact on Existing Tests

None. Tests do not use docker-compose.

---

### WU-6: Build Validation and Security Header Integration

**Goal:** Validate the Docker build from a clean GitHub clone (Risk R5). Verify security headers are not duplicated in production. Document the validation procedure.

**Depends on:** WU-3 (Dockerfile), WU-4 (nginx.conf), WU-5 (docker-compose).

#### Build Validation Procedure

This addresses Risk R5 from the master plan: "Docker build fails from GitHub (missing local files)."

```bash
# ── Build Validation from Clean Clone ────────────────────────────
# Run this from any directory (not the development checkout).
# Confirms that docker build succeeds with only files from the repo
# (no local-only dependencies, no missing files in .dockerignore).

# 1. Clone to a temporary directory.
cd /tmp
git clone git@github.com:saltyreformed/shekel.git shekel-build-test
cd shekel-build-test

# 2. Build the Docker image.
docker build -t shekel-build-test .

# 3. Verify the image was built and gunicorn is available.
docker run --rm shekel-build-test python -c "print('Build verified: Python OK')"
docker run --rm shekel-build-test gunicorn --version

# 4. Verify gunicorn.conf.py is valid.
docker run --rm shekel-build-test python -c "exec(open('gunicorn.conf.py').read()); print('Config OK')"

# 5. Verify static files are present in the image.
docker run --rm shekel-build-test ls app/static/css/base.css

# 6. Clean up.
cd /
rm -rf /tmp/shekel-build-test
docker rmi shekel-build-test
```

Expected output:
- Step 2: Build completes with no errors.
- Step 3: Prints "Build verified: Python OK" and gunicorn version.
- Step 4: Prints "Config OK".
- Step 5: Lists the file without error.

#### Security Header Verification

After `docker compose up -d` (from WU-5), verify headers are not duplicated:

```bash
# 1. Check security headers on a proxied response (via Nginx).
curl -sI http://localhost/ | grep -iE "content-security-policy|x-frame-options|x-content-type"

# Expected: Each header appears EXACTLY ONCE (set by Flask, passed
# through Nginx without duplication).
#   X-Content-Type-Options: nosniff
#   X-Frame-Options: DENY
#   Content-Security-Policy: default-src 'self'; ...

# 2. Check headers on a static file response (served by Nginx directly).
curl -sI http://localhost/static/css/base.css | grep -iE "content-security-policy|x-frame-options|x-content-type|cache-control|server"

# Expected: Only X-Content-Type-Options and Cache-Control (set by Nginx).
# No CSP, no X-Frame-Options (those are Flask-only, on proxied responses).
#   Server: nginx/1.27.x
#   X-Content-Type-Options: nosniff
#   Cache-Control: public, immutable

# 3. Check that the health endpoint works through Nginx.
curl -s http://localhost/health | python -m json.tool

# Expected: {"status": "healthy", "database": "connected"}
```

#### Changes to `app/__init__.py`

**No changes required.** Per the Security Header Ownership decision, Flask retains all security headers unchanged. Nginx does not duplicate them. The `_register_security_headers` function in `app/__init__.py:236-255` remains as-is.

#### Test Gate

- [ ] `docker build` succeeds from a clean clone of the GitHub repo
- [ ] `gunicorn.conf.py` validates inside the built image
- [ ] Static files are present in the built image
- [ ] Security headers appear exactly once on proxied responses (no duplication)
- [ ] Static file responses have `X-Content-Type-Options` but NOT `Content-Security-Policy`
- [ ] Application logs appear in JSON format in container stdout (`docker compose logs app`)

#### Impact on Existing Tests

None. This work unit is entirely manual verification.

---

## Complete Test Plan

### pytest Tests

| Test File | Class | Method | WU |
|-----------|-------|--------|----|
| `tests/test_routes/test_health.py` | `TestHealthEndpoint` | `test_health_returns_200_when_healthy` | 1 |
| | | `test_health_returns_json_content_type` | 1 |
| | | `test_health_requires_no_authentication` | 1 |
| | | `test_health_returns_500_on_db_failure` | 1 |
| | | `test_health_not_logged_in_request_summary` | 1 |
| | | `test_health_no_request_id_header` | 1 |

**Total new tests: 6**

### Manual Verification Runbook

All manual steps are performed after `docker compose up -d` completes with all services healthy.

| # | Step | Command | Expected Result | WU |
|---|------|---------|-----------------|-----|
| 1 | All services healthy | `docker compose ps` | db, app, nginx all show `healthy` | 5 |
| 2 | Health endpoint via Nginx | `curl -s http://localhost/health` | `{"status": "healthy", "database": "connected"}` | 1, 5 |
| 3 | Health endpoint direct (inside network) | `docker exec shekel-app python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"` | `{"status": "healthy", "database": "connected"}` | 1, 3 |
| 4 | App reachable via Nginx | `curl -sI http://localhost/` | 200 or 302 (redirect to login) | 5 |
| 5 | Static CSS via Nginx | `curl -sI http://localhost/static/css/base.css` | 200, `Server: nginx`, `Cache-Control: public, immutable` | 4, 5 |
| 6 | Static JS via Nginx | `curl -sI http://localhost/static/js/app.js` | 200, `Server: nginx` | 4, 5 |
| 7 | No X-Request-Id on static | `curl -sI http://localhost/static/css/base.css \| grep X-Request-Id` | No output (header absent) | 4 |
| 8 | X-Request-Id on proxied | `curl -sI http://localhost/` | `X-Request-Id: <uuid>` present | 1 |
| 9 | CSP header once only | `curl -sI http://localhost/ \| grep -c Content-Security-Policy` | `1` (not 2) | 6 |
| 10 | X-Frame-Options once only | `curl -sI http://localhost/ \| grep -c X-Frame-Options` | `1` | 6 |
| 11 | X-Content-Type-Options on static | `curl -sI http://localhost/static/css/base.css \| grep X-Content-Type` | `X-Content-Type-Options: nosniff` | 4 |
| 12 | Gzip on HTML | `curl -sI -H "Accept-Encoding: gzip" http://localhost/ \| grep Content-Encoding` | `Content-Encoding: gzip` (if response > 1024 bytes) | 4 |
| 13 | Request size limit | `curl -s -o /dev/null -w "%{http_code}" -X POST -d "$(python -c "print('x'*6000000)")" http://localhost/login` | `413` | 4 |
| 14 | JSON logs in stdout | `docker compose logs app --tail 5` | JSON-formatted log lines | 2 |
| 15 | Nginx JSON logs | `docker compose logs nginx --tail 5` | JSON-formatted access log | 4 |
| 16 | App not exposed on host | `curl -s http://localhost:8000/health` | Connection refused (port not mapped) | 5 |
| 17 | Docker healthcheck status | `docker inspect shekel-app --format='{{.State.Health.Status}}'` | `healthy` | 3 |
| 18 | Build from clean clone | (see WU-6 procedure) | Build succeeds | 6 |
| 19 | Dev compose starts | `docker compose -f docker-compose.dev.yml up -d db && sleep 3 && docker compose -f docker-compose.dev.yml up -d` | db + app start, app uses Flask dev server | 5 |
| 20 | Dev app accessible | `curl -s http://localhost:5000/health` | 200 | 5 |

---

## Phase 8D-1 Test Gate Checklist

From the master plan test gate, mapped to specific tests and verification steps:

- [ ] **`docker build` succeeds from a clean clone of the GitHub repo**
  - Verification: WU-6 build validation procedure (manual step 18)

- [ ] **`docker-compose up` starts all services (app, Postgres, Nginx) and the app is reachable via Nginx**
  - Verification: Manual steps 1 and 4

- [ ] **`/health` returns 200 with database connected**
  - pytest: `test_health_returns_200_when_healthy`
  - Verification: Manual steps 2 and 3

- [ ] **Static files served by Nginx (check response headers)**
  - Verification: Manual steps 5, 6, 7

- [ ] **Security headers present on all responses**
  - Verification: Manual steps 9, 10, 11
  - Note: Flask sets security headers on proxied responses (steps 9-10). Nginx sets `X-Content-Type-Options` on static responses (step 11). No duplication.

- [ ] **Application logs appear in JSON format in container stdout**
  - Verification: Manual steps 14, 15

---

## File Summary

### New Files (4)

| File | Type | WU |
|------|------|----|
| `app/routes/health.py` | Flask Blueprint | 1 |
| `gunicorn.conf.py` | Gunicorn configuration | 2 |
| `nginx/nginx.conf` | Nginx configuration | 4 |
| `tests/test_routes/test_health.py` | pytest tests (6 tests) | 1 |

### Modified Files (4)

| File | Changes | WU |
|------|---------|-----|
| `app/__init__.py` | Register `health_bp` Blueprint (2 lines added in `_register_blueprints`) | 1 |
| `app/utils/logging_config.py` | Add `/health` exclusion to `_attach_request_id` and `_log_request_summary` hooks | 1 |
| `Dockerfile` | Change EXPOSE to 8000, add HEALTHCHECK, add `/var/www/static` dir, add CMD | 3 |
| `entrypoint.sh` | Add static file copy step, change to `exec "$@"` pattern | 2, 3 |

### Rewritten Files (2)

| File | Changes | WU |
|------|---------|-----|
| `docker-compose.yml` | Add nginx service, network isolation, app health check, static volume, local build | 5 |
| `docker-compose.dev.yml` | Fix FLASK_ENV, add source mount, override CMD to Flask dev server, fix container name | 5 |
