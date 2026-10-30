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
# Must be higher than Nginx's keepalive_timeout (default 65s) so
# that Nginx closes idle connections first, avoiding 502 errors
# from Gunicorn dropping a connection Nginx tries to reuse.
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "70"))

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
# Trust X-Forwarded-* headers ONLY from the IPs/CIDRs declared in
# FORWARDED_ALLOW_IPS.  No fallback default -- a missing or empty
# value fails the gunicorn config load before the master process
# binds its socket, so a misconfigured deploy is impossible to
# overlook.  The previous default of "172.16.0.0/12,192.168.0.0/16,
# 10.0.0.0/8" trusted every RFC 1918 private subnet, which let any
# co-tenant container on the homelab Docker bridge forge
# X-Forwarded-For / CF-Connecting-IP and bypass per-IP rate limits.
# See audit finding F-015 (proxy header spoofing) and Commit C-33.
#
# Set FORWARDED_ALLOW_IPS to:
#   - the trusted reverse-proxy container's IP, or
#   - the Docker bridge CIDR that contains ONLY the trusted
#     reverse proxy (and the app itself -- the app does not talk
#     to itself via this surface).
#
# Bundled mode (docker-compose.yml):  the in-stack nginx and the
#   app share the pinned ``backend`` bridge (172.31.0.0/24); the
#   compose env block defaults FORWARDED_ALLOW_IPS to that CIDR.
#
# Shared mode (deploy/docker-compose.prod.yml):  the externally-
#   managed shared nginx and the app meet on the pinned
#   ``shekel-frontend`` bridge (172.32.0.0/24); the override env
#   block sets FORWARDED_ALLOW_IPS to that CIDR.
#
# Local-only invocations of ``gunicorn --config gunicorn.conf.py``
# (rare; ``flask run`` is the dev path) must export
# FORWARDED_ALLOW_IPS=127.0.0.1 explicitly.  The hard fail surfaces
# the misconfig before Gunicorn starts honouring forged headers.
_forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "").strip()
if not _forwarded_allow_ips:
    raise RuntimeError(
        "FORWARDED_ALLOW_IPS is not set.  Gunicorn refuses to start "
        "without an explicit allow list because trusting all RFC 1918 "
        "ranges (the previous default) lets any co-tenant on the same "
        "Docker bridge forge X-Forwarded-For and bypass rate limiting. "
        "Set FORWARDED_ALLOW_IPS to the trusted reverse proxy's "
        "container IP or the Docker bridge subnet that contains only "
        "the proxy and the app.  See gunicorn.conf.py and "
        "shekel-audits/security-2026-04-15/remediation-plan.md (Commit "
        "C-33) for the full rationale."
    )
forwarded_allow_ips = _forwarded_allow_ips
