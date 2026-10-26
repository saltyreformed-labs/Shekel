> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8D-3: Cloudflare Tunnel, Access, WAF & Runbook -- Implementation Plan

## Overview

This plan implements the final sub-phase of Phase 8D from the Phase 8 Hardening & Ops Plan. It covers Cloudflare Tunnel setup, Cloudflare Access zero-trust policy, Cloudflare WAF rate limiting, Promtail/Loki log shipping validation, and consolidation of all operational documentation into a unified runbook.

**Pre-existing infrastructure discovered during planning:**

- Nginx reverse proxy listens on HTTP port 80 (`nginx/nginx.conf:117`) and forwards to Gunicorn on port 8000. TLS is explicitly deferred to Cloudflare Tunnel (comment on lines 9-11). Proxy headers (`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`) are set on lines 148-151, but **no `set_real_ip_from` or `real_ip_header` directives exist** -- this is a critical gap that must be fixed before Cloudflare traffic flows correctly.
- Flask-Limiter is configured with `key_func=get_remote_address` (`app/extensions.py:30-31`), which reads `X-Forwarded-For` automatically. Rate limits are 5 per 15 minutes on both `/login` POST (`app/routes/auth.py:26`) and `/mfa/verify` POST (`app/routes/auth.py:133`).
- Gunicorn trusts forwarded headers from all upstreams (`gunicorn.conf.py:74`: `forwarded_allow_ips = "*"`).
- Structured JSON logging is fully configured (`app/utils/logging_config.py`). Request summaries log `remote_addr` from `request.remote_addr` (line 157). Health checks are excluded from logging (line 104).
- Promtail configuration exists at `monitoring/promtail-config.yml` (71 lines). It scrapes the `shekel-app` container via Docker socket discovery and parses JSON fields (`level`, `event`, `category`, `request_id`, `user_id`). A monitoring stack README with Loki/Grafana/Promtail docker-compose exists at `monitoring/README.md`.
- Docker Compose (`docker-compose.yml`) defines three services: `db`, `app`, `nginx`. Networks: `frontend` (bridge, Nginx only) and `backend` (internal, all services). No `cloudflared` service exists.
- Backup/restore runbook exists at `docs/backup_runbook.md` (353 lines). Secret management runbook exists at `docs/runbook_secrets.md` (94 lines). No unified runbook exists.
- `.env.example` (128 lines) documents all current environment variables. No Cloudflare-related variables exist.
- No references to Cloudflare, `cloudflared`, or `CF-Connecting-IP` exist anywhere in the codebase.

**Key decisions documented in this plan:**

1. Cloudflared deployment model (host systemd vs. Docker container)
2. Real IP propagation strategy (full chain analysis and Nginx fix)
3. Cloudflare WAF rate limit thresholds (complementing app-level limits)

---

## Pre-Existing Infrastructure

### Nginx Configuration

**File:** `nginx/nginx.conf` (169 lines)

| Aspect | Current State | Impact on 8D-3 |
|--------|--------------|-----------------|
| Listen port | HTTP 80 (line 117) | Cloudflared will connect to `http://localhost:80` (host mode) or `http://nginx:80` (container mode) |
| Proxy to Gunicorn | `proxy_pass http://gunicorn` → `app:8000` (lines 109-111, 143) | No change needed |
| `X-Forwarded-For` header | Set via `$proxy_add_x_forwarded_for` (line 150) | Appends Nginx's view of `$remote_addr` to the chain -- but `$remote_addr` will be cloudflared's IP, not the client's, without `set_real_ip_from` |
| `X-Real-IP` header | Set to `$remote_addr` (line 149) | Same problem: will contain cloudflared's IP |
| `set_real_ip_from` | **MISSING** | Must be added to trust cloudflared's IP and extract the real client IP from incoming headers |
| `real_ip_header` | **MISSING** | Must be added to tell Nginx which header carries the real client IP |
| JSON access log | `json_combined` format to stdout (lines 34-49) | `$remote_addr` in Nginx logs will also be incorrect without the fix |
| Static files | Served at `/static/` with 7-day cache (lines 123-138) | No change needed |
| Gzip | Enabled at level 6 (lines 67-85) | No change needed |

### Rate Limiting (App-Level)

**File:** `app/routes/auth.py`

| Endpoint | Route | Decorator | Line |
|----------|-------|-----------|------|
| Login | `/login` (POST) | `@limiter.limit("5 per 15 minutes", methods=["POST"])` | 26 |
| MFA Verify | `/mfa/verify` (POST) | `@limiter.limit("5 per 15 minutes", methods=["POST"])` | 133 |

**File:** `app/extensions.py` (lines 30-31)

```python
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
```

`get_remote_address` (from `flask_limiter.util`) checks `X-Forwarded-For` first, then falls back to `request.remote_addr`. This means rate limiting will key on the correct client IP **only if** Nginx passes the real IP through `X-Forwarded-For`.

### Gunicorn Configuration

**File:** `gunicorn.conf.py`

| Setting | Value | Line | Impact |
|---------|-------|------|--------|
| `forwarded_allow_ips` | `"*"` | 74 | Trusts `X-Forwarded-*` headers from any upstream -- required since Nginx and Gunicorn are on the same Docker network |
| Bind | `0.0.0.0:8000` | 46 | Internal only (not exposed to host) |
| Workers | 2 | 34 | Sufficient for single-user |
| Access log | Disabled (`None`) | 54 | Flask's `_log_request_summary()` handles request logging |

### Structured Logging

**File:** `app/utils/logging_config.py`

The `_log_request_summary()` after-request hook (lines 129-175) logs:

```python
extra_fields = {
    "event": event,
    "category": "performance",
    "method": request.method,
    "path": request.path,
    "status": response.status_code,
    "request_duration": round(duration_ms, 2),
    "remote_addr": request.remote_addr,  # ← line 157: this is what we need to verify
}
```

`request.remote_addr` in Flask reads the `REMOTE_ADDR` WSGI variable. When Gunicorn's `forwarded_allow_ips = "*"` is set and `X-Forwarded-For` is present, Gunicorn rewrites `REMOTE_ADDR` to the value from `X-Forwarded-For`. Therefore, the logged `remote_addr` will be correct **if Nginx passes the true client IP in `X-Forwarded-For`**.

### Promtail/Loki Configuration

**File:** `monitoring/promtail-config.yml` (71 lines)

- Pushes to `http://loki:3100/loki/api/v1/push` (line 41)
- Discovers containers via Docker socket (line 46)
- Filters to `com.docker.compose.service=app` (line 50)
- Parses JSON fields: `level`, `event`, `category`, `request_id`, `user_id` (lines 57-63)
- Extracts timestamps in RFC3339 format (lines 68-70)

**File:** `monitoring/README.md` (150 lines)

- Documents full monitoring stack setup: create `monitoring` Docker network, run Loki/Grafana/Promtail via `monitoring/docker-compose.yml`, configure Grafana data source
- Documents LogQL queries for auth events, errors, slow requests, etc.
- Documents that the Shekel app container needs to join the `monitoring` network

**Gap:** The Shekel `docker-compose.yml` does not yet reference the external `monitoring` network. The app container must join this network for Promtail to discover it via Docker labels.

### Docker Compose

**File:** `docker-compose.yml` (118 lines)

Three services: `db` (PostgreSQL 16-alpine), `app` (Gunicorn Flask), `nginx` (Nginx 1.27-alpine). Two networks: `frontend` (bridge), `backend` (internal). Nginx is on both networks. The app container is on `backend` only.

The `monitoring/README.md` (lines 27-39) documents that the app service needs a `monitoring` network added. This has not been done yet.

### Existing Documentation

**Directory:** `docs/` (15 files)

| File | Purpose | Lines | Consolidation Target |
|------|---------|-------|---------------------|
| `docs/backup_runbook.md` | Backup, restore, verify, retention procedures | 353 | Runbook §Backup & Restore |
| `docs/runbook_secrets.md` | Secret inventory, rotation, DR reconstruction | 94 | Runbook §Security Operations |
| `monitoring/README.md` | Loki/Grafana/Promtail setup, LogQL queries | 150 | Runbook §Monitoring |
| `docs/phase_8d1_implementation_plan.md` | Docker, Nginx, Gunicorn (reference for deploy) | -- | Runbook §Deployment (selected excerpts) |
| `docs/phase_8d2_implementation_plan.md` | CI, deploy script, .env setup (reference) | -- | Runbook §Deployment (selected excerpts) |

**Scripts with built-in documentation:**

| Script | Help/Usage | Consolidation Target |
|--------|-----------|---------------------|
| `scripts/deploy.sh` | Lines 11-19: comprehensive usage comments | Runbook §Deployment |
| `scripts/backup.sh` | Usage documented in backup_runbook.md | Runbook §Backup & Restore |
| `scripts/restore.sh` | Usage documented in backup_runbook.md | Runbook §Backup & Restore |
| `scripts/verify_backup.sh` | Usage documented in backup_runbook.md | Runbook §Backup & Restore |
| `scripts/backup_retention.sh` | Usage documented in backup_runbook.md | Runbook §Backup & Restore |
| `scripts/reset_mfa.py` | Line 8: `python scripts/reset_mfa.py <user_email>` | Runbook §Security Operations |
| `scripts/integrity_check.py` | `--verbose`, `--category` flags | Runbook §Backup & Restore |
| `scripts/audit_cleanup.py` | Used via cron | Runbook §Monitoring |

### Real IP Propagation Chain Analysis

The full request chain with Cloudflare Tunnel:

```
[Client: 203.0.113.45]
        │
        ▼
[Cloudflare Edge]
  Adds: CF-Connecting-IP: 203.0.113.45
  Adds: X-Forwarded-For: 203.0.113.45
        │
        ▼
[cloudflared]
  Preserves CF-Connecting-IP and X-Forwarded-For.
  Connects to Nginx via localhost:80 (host mode) or nginx:80 (container mode).
  Source IP seen by Nginx: 127.0.0.1 (host mode) or 172.x.x.x (Docker bridge).
        │
        ▼
[Nginx :80]
  CURRENT BEHAVIOR (BROKEN):
    $remote_addr = 127.0.0.1 (cloudflared's IP)
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for
      → sends "203.0.113.45, 127.0.0.1" to Gunicorn
    proxy_set_header X-Real-IP $remote_addr
      → sends "127.0.0.1" to Gunicorn

  FIXED BEHAVIOR (after WU-1):
    set_real_ip_from 127.0.0.1 + real_ip_header CF-Connecting-IP
    $remote_addr = 203.0.113.45 (extracted from CF-Connecting-IP)
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for
      → sends "203.0.113.45" to Gunicorn
    proxy_set_header X-Real-IP $remote_addr
      → sends "203.0.113.45" to Gunicorn
        │
        ▼
[Gunicorn :8000]
  forwarded_allow_ips = "*" → trusts X-Forwarded-For
  Sets REMOTE_ADDR = first IP from X-Forwarded-For = 203.0.113.45
        │
        ▼
[Flask]
  request.remote_addr = 203.0.113.45  ✓
  Flask-Limiter get_remote_address() reads request.remote_addr  ✓
  Structured logging logs request.remote_addr  ✓
```

**Why `CF-Connecting-IP` instead of `X-Forwarded-For`:** Cloudflare always sets `CF-Connecting-IP` to the true client IP (single value, not a chain). `X-Forwarded-For` can contain multiple IPs if the client itself sent a spoofed header. Using `CF-Connecting-IP` as the `real_ip_header` is the Cloudflare-recommended approach and avoids IP spoofing via `X-Forwarded-For` manipulation.

---

## Cloudflared Deployment Model

**Decision: Install `cloudflared` on the Proxmox host, managed by systemd.**

### Option A: On the Proxmox Host (Recommended)

| Aspect | Assessment |
|--------|-----------|
| Tunnel availability | Survives `docker compose down/up` restarts -- the tunnel stays up while containers cycle |
| Networking | Connects to `http://localhost:${NGINX_PORT:-80}` -- no Docker network complexity |
| Management | Managed by systemd (`systemctl start/stop/status cloudflared`), consistent with Linux service management |
| Logging | Logs to journald (`journalctl -u cloudflared`), standard sysadmin tooling |
| Precedent | Backup scripts already run on the host via cron (established in 8C); `deploy.sh` runs on the host (8D-2) |
| Upgrades | Updated via package manager independently of the application stack |
| Credential storage | Tunnel credentials stored in `/root/.cloudflared/` with `chmod 600` -- standard for host-level secrets |

### Option B: Docker Container in docker-compose.yml

| Aspect | Assessment |
|--------|-----------|
| Tunnel availability | Goes down with `docker compose down` -- tunnel drops during deployments, meaning the app is unreachable externally until containers restart |
| Networking | Must be on the `frontend` network to reach Nginx, or Nginx port must be exposed to host |
| Management | Managed via docker-compose, consistent with the containerized app stack |
| Credentials | Tunnel token must be in `.env` or mounted as a volume -- adds another secret to manage |
| Coupling | If docker-compose has an issue, both the app and the external tunnel go down simultaneously |

### Recommendation

**Option A.** The primary advantage is operational independence: the tunnel survives application deployments. When `deploy.sh` runs `docker compose down && docker compose up -d`, external users see a brief "service unavailable" from the app but the tunnel itself stays up and can serve Cloudflare's waiting page. With Option B, the tunnel drops entirely and Cloudflare returns a generic error. Additionally, host-level tooling has precedent in this project (cron backups, deploy script), so the operational model is consistent.

---

## Cloudflare Rate Limit Thresholds

**Decision: 20 requests per 10 seconds per IP on `/login` and `/auth/mfa/verify`.**

### Layered Rate Limiting Architecture

| Layer | Scope | Threshold | Purpose |
|-------|-------|-----------|---------|
| **Cloudflare WAF** (outer) | Per source IP, global | 20 requests / 10 seconds | Stops volumetric brute force (botnets, credential stuffing) before traffic reaches the origin |
| **Flask-Limiter** (inner) | Per source IP, per endpoint | 5 requests / 15 minutes | Fine-grained protection for legitimate users; catches slow, distributed attacks |

### Threshold Rationale

- **Cloudflare 20/10s:** A legitimate user might submit 2-3 login attempts in quick succession (typo, wrong password). Even the fastest human clicker would not exceed 20 requests in 10 seconds. This threshold catches automated tools (hydra, burp intruder) while never triggering for humans. The short window (10 seconds) means the block is temporary and self-resolving.
- **Flask-Limiter 5/15m:** This is the fine-grained layer. A user who enters their password wrong 5 times in 15 minutes is either guessing or has forgotten their password. The 15-minute window forces a cooldown.
- **Why not match the thresholds?** The two layers serve different purposes. Cloudflare blocks high-volume automated attacks cheaply (no origin load). Flask-Limiter blocks low-volume persistent attacks that slip under Cloudflare's radar. If both had the same threshold, the inner layer would be redundant.

### Endpoints to Protect

| Path | Method | Cloudflare Expression |
|------|--------|-----------------------|
| `/login` | POST | `http.request.uri.path eq "/login" and http.request.method eq "POST"` |
| `/auth/mfa/verify` | POST | `http.request.uri.path eq "/auth/mfa/verify" and http.request.method eq "POST"` |

---

## Work Units

The implementation is organized into 6 work units. Each unit has explicit dependencies and a test gate. Infrastructure units (WU-1, WU-2) come first to establish the foundation. Configuration units (WU-3, WU-4) build on the tunnel. Validation (WU-5) confirms observability. Documentation (WU-6) consolidates everything.

### Dependency Graph

```
WU-1: Real IP Propagation
  │
  ▼
WU-2: Cloudflared Installation & Tunnel
  │
  ├──────────┐
  ▼          ▼
WU-3:      WU-4:
Access     WAF Rate
Policy     Limiting
  │          │
  ├──────────┘
  ▼
WU-5: Log Shipping Validation
  │
  ▼
WU-6: Runbook Consolidation
```

WU-3 and WU-4 are independent of each other and can be done in either order (both depend on WU-2). WU-5 depends on WU-2 (needs live traffic through the tunnel for end-to-end validation). WU-6 depends on all prior units (consolidates their documentation).

---

### WU-1: Real IP Propagation

**Goal:** Ensure the real client IP propagates through the full chain (Cloudflare → cloudflared → Nginx → Gunicorn → Flask) so that Flask-Limiter rate limits by actual client IP, structured logs contain actual client IP, and Nginx access logs contain actual client IP.

**Depends on:** Nothing. This is foundational and must be completed before any Cloudflare work.

#### Files to Modify

**`nginx/nginx.conf`** -- Add `set_real_ip_from` and `real_ip_header` directives in the `http` block, after the request size limit section (after line 104) and before the upstream block (line 106):

Current (lines 100-106):
```nginx
    # ── Request Size Limits ──────────────────────────────────────
    # Maximum allowed size of the client request body.  Shekel does
    # not handle file uploads; this limit prevents oversized POST
    # payloads.
    client_max_body_size 5m;

    # ── Upstream (Gunicorn) ──────────────────────────────────────
```

New (insert between line 104 and line 106):
```nginx
    # ── Request Size Limits ──────────────────────────────────────
    # Maximum allowed size of the client request body.  Shekel does
    # not handle file uploads; this limit prevents oversized POST
    # payloads.
    client_max_body_size 5m;

    # ── Real IP from Cloudflare Tunnel ───────────────────────────
    # cloudflared connects from the local host (systemd service) or
    # Docker bridge network.  Trust these sources and extract the
    # real client IP from the CF-Connecting-IP header, which
    # Cloudflare always sets to the true client IP (single value,
    # not a chain -- immune to X-Forwarded-For spoofing).
    #
    # 127.0.0.1     -- cloudflared running on the host (connects via localhost)
    # 172.16.0.0/12 -- Docker bridge networks (if cloudflared runs in a container)
    # 192.168.0.0/16 -- local network (direct access without tunnel during testing)
    # 10.0.0.0/8    -- alternative private network ranges
    set_real_ip_from 127.0.0.1;
    set_real_ip_from 172.16.0.0/12;
    set_real_ip_from 192.168.0.0/16;
    set_real_ip_from 10.0.0.0/8;

    # Use CF-Connecting-IP as the source of truth for the client IP.
    # Cloudflare always sets this to a single IP (the connecting client).
    # Falls back gracefully: if the header is absent (direct local access),
    # $remote_addr remains unchanged.
    real_ip_header CF-Connecting-IP;

    # Do not recurse through chained proxies -- CF-Connecting-IP is
    # always a single value, so recursion is unnecessary.
    real_ip_recursive off;

    # ── Upstream (Gunicorn) ──────────────────────────────────────
```

**Rationale:**

- `CF-Connecting-IP` is preferred over `X-Forwarded-For` because it is always a single IP set by Cloudflare's edge, not a chain that can be spoofed by the client.
- `set_real_ip_from` trusts private IP ranges because cloudflared always runs on the local network (whether on the host or in a container). Traffic from the public internet cannot reach Nginx directly -- only through cloudflared.
- `real_ip_recursive off` because `CF-Connecting-IP` is a single value, not a comma-separated chain.
- When `CF-Connecting-IP` is absent (e.g., direct LAN access during development), `$remote_addr` is unchanged -- the directive is a no-op when the header is missing.

**Impact on downstream components:**

After this change, `$remote_addr` in Nginx becomes the true client IP. This flows through:

1. **Nginx JSON access log** (line 37): `"remote_addr":"$remote_addr"` → now shows real client IP.
2. **`proxy_set_header X-Real-IP $remote_addr`** (line 149) → sends real client IP to Gunicorn.
3. **`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`** (line 150) → `$proxy_add_x_forwarded_for` appends `$remote_addr` (now the real IP) to the chain. Gunicorn reads this and sets `REMOTE_ADDR`.
4. **Flask `request.remote_addr`** → reads `REMOTE_ADDR` set by Gunicorn → real client IP.
5. **Flask-Limiter `get_remote_address()`** → reads `request.remote_addr` → rate limits by real client IP.
6. **Structured logging** (`logging_config.py:157`) → logs `request.remote_addr` → real client IP in logs.

**No changes needed to Flask, Flask-Limiter, Gunicorn, or logging configuration.** The fix is entirely in Nginx.

#### Nginx Module Dependency

The `ngx_http_realip_module` (which provides `set_real_ip_from`, `real_ip_header`, `real_ip_recursive`) is compiled into the default `nginx:1.27-alpine` Docker image. No additional installation is required.

Verify with:
```bash
docker exec shekel-nginx nginx -V 2>&1 | grep -o 'http_realip_module'
```

Expected output: `http_realip_module`

#### Test Gate

- [ ] `nginx -t` passes configuration validation inside the container
- [ ] `nginx -V` confirms `http_realip_module` is present
- [ ] Nginx starts successfully with the updated config
- [ ] When accessed directly (LAN, no tunnel), `$remote_addr` in Nginx logs shows the LAN client IP (not 127.0.0.1)
- [ ] Flask structured logs show the correct `remote_addr` for direct LAN access

#### Verification Procedure

**Step 1: Validate Nginx config syntax.**
```bash
docker compose down nginx && docker compose up -d nginx
docker exec shekel-nginx nginx -t
```
Expected: `nginx: the configuration file /etc/nginx/nginx.conf syntax is ok`

**Step 2: Verify module is available.**
```bash
docker exec shekel-nginx nginx -V 2>&1 | grep -o 'http_realip_module'
```
Expected: `http_realip_module`

**Step 3: Test with direct LAN access (before tunnel is set up).**
```bash
# From a different machine on the LAN (e.g., 192.168.1.100):
curl -s http://<proxmox-ip>/health

# Check Nginx access log for the correct remote_addr:
docker logs shekel-nginx --tail 5

# Check Flask structured log for the correct remote_addr:
docker logs shekel-app --tail 5 | python -m json.tool | grep remote_addr
```
Expected: `remote_addr` shows `192.168.1.100`, not `127.0.0.1` or `172.x.x.x`.

**Step 4: Test with simulated CF-Connecting-IP header (from the Proxmox host).**
```bash
# Simulate what cloudflared will send:
curl -s -H "CF-Connecting-IP: 198.51.100.42" http://localhost/health

# Check Flask logs:
docker logs shekel-app --tail 3 | python -m json.tool | grep remote_addr
```
Expected: `remote_addr` shows `198.51.100.42`.

---

### WU-2: Cloudflared Installation and Tunnel Configuration

**Goal:** Install `cloudflared` on the Proxmox host, create a named tunnel, configure DNS routing, and verify basic connectivity through the tunnel.

**Depends on:** WU-1 (real IP propagation must be working before tunnel traffic flows).

#### Files to Create

**`cloudflared/config.yml`** -- Template configuration file with placeholder values. The user fills in tunnel-specific values after creating the tunnel.

```yaml
# Cloudflare Tunnel Configuration for Shekel Budget App
#
# This file configures cloudflared to route traffic from a Cloudflare
# subdomain to the local Nginx reverse proxy.
#
# Prerequisites:
#   1. cloudflared installed on the Proxmox host
#   2. Authenticated: cloudflared tunnel login
#   3. Tunnel created: cloudflared tunnel create shekel
#   4. DNS record created: cloudflared tunnel route dns shekel <DOMAIN>
#
# Usage:
#   cloudflared tunnel --config /path/to/config.yml run shekel
#
# After setup, enable the systemd service:
#   sudo cloudflared service install
#   sudo systemctl enable cloudflared
#   sudo systemctl start cloudflared

# Tunnel identity. Replace with your tunnel's UUID.
# Find it with: cloudflared tunnel list
tunnel: <TUNNEL_ID>

# Path to the tunnel credentials file.
# Created automatically by `cloudflared tunnel create`.
# Default location: ~/.cloudflared/<TUNNEL_ID>.json
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json

# Ingress rules define how traffic is routed from Cloudflare to local services.
ingress:
  # Route all traffic for the configured domain to the local Nginx
  # reverse proxy.  Nginx handles static files, proxying to Gunicorn,
  # and all application routing.
  - hostname: <DOMAIN>
    service: http://localhost:80
    originRequest:
      # Do not verify TLS on the origin -- Nginx listens on plain HTTP.
      noTLSVerify: true
      # Pass the connecting client's IP to the origin via headers.
      # cloudflared sets CF-Connecting-IP and X-Forwarded-For by default.
      # Nginx reads CF-Connecting-IP via the real_ip_header directive.

  # Optional: staging subdomain for testing before production cutover.
  # Uncomment and configure during initial setup, then remove once
  # production is verified.
  # - hostname: <STAGING_DOMAIN>
  #   service: http://localhost:80
  #   originRequest:
  #     noTLSVerify: true

  # Catch-all rule (required by cloudflared).
  # Returns 404 for any request that doesn't match a hostname above.
  - service: http_status:404

# Logging configuration.
# Logs go to systemd journal by default (journalctl -u cloudflared).
# Uncomment to also log to a file:
# logfile: /var/log/cloudflared.log

# Metrics server for monitoring (optional).
# Uncomment to expose Prometheus metrics on localhost:2000.
# metrics: localhost:2000
```

**Placeholder values to fill in:**

| Placeholder | How to Obtain | Example |
|-------------|--------------|---------|
| `<TUNNEL_ID>` | Output of `cloudflared tunnel create shekel` | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| `<DOMAIN>` | Your chosen subdomain | `budget.example.com` |
| `<STAGING_DOMAIN>` | Staging subdomain for testing (Risk R6) | `staging-budget.example.com` |

#### Runbook Section: Cloudflare Tunnel Setup

The following step-by-step procedure is documented in full here and will be included in the consolidated runbook (WU-6).

##### Step 1: Install cloudflared

```bash
# On Arch Linux (Proxmox host):
# Option A: From the AUR
yay -S cloudflared-bin

# Option B: Direct download from Cloudflare
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# Verify installation:
cloudflared --version
```

##### Step 2: Authenticate with Cloudflare

```bash
# Opens a browser for Cloudflare login. Stores credentials in ~/.cloudflared/.
cloudflared tunnel login
```

This creates `~/.cloudflared/cert.pem` -- the account-level certificate used to manage tunnels.

##### Step 3: Create the tunnel

```bash
# Create a named tunnel. The name is a human-readable identifier.
cloudflared tunnel create shekel
```

Output will include the tunnel UUID (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`). This creates a credentials file at `~/.cloudflared/<TUNNEL_ID>.json`.

```bash
# Verify the tunnel exists:
cloudflared tunnel list
```

##### Step 4: Configure DNS routing

```bash
# Create a CNAME record pointing your subdomain to the tunnel.
cloudflared tunnel route dns shekel budget.example.com
```

This creates a CNAME record in your Cloudflare DNS zone: `budget.example.com → <TUNNEL_ID>.cfargotunnel.com`.

For Risk R6 (staging-first approach), also create a staging record:
```bash
cloudflared tunnel route dns shekel staging-budget.example.com
```

##### Step 5: Deploy the config file

```bash
# Copy the template to cloudflared's config directory.
sudo mkdir -p /etc/cloudflared
sudo cp /path/to/shekel/cloudflared/config.yml /etc/cloudflared/config.yml

# Edit the config file: replace <TUNNEL_ID> and <DOMAIN> with real values.
sudo nano /etc/cloudflared/config.yml
```

##### Step 6: Test the tunnel manually

```bash
# Run the tunnel in the foreground to verify connectivity.
cloudflared tunnel --config /etc/cloudflared/config.yml run shekel
```

In a separate terminal (or from another device):
```bash
# Test via the staging subdomain first (if configured):
curl -v https://staging-budget.example.com/health

# Expected response:
# HTTP/2 200
# {"status":"healthy","timestamp":"..."}
```

##### Step 7: Install as a systemd service

```bash
# Install the systemd service (uses /etc/cloudflared/config.yml by default).
sudo cloudflared service install

# Enable and start:
sudo systemctl enable cloudflared
sudo systemctl start cloudflared

# Verify:
sudo systemctl status cloudflared
```

##### Step 8: Verify the full chain

```bash
# From an external network (e.g., phone on cellular data):
curl -s https://budget.example.com/health
# Expected: {"status":"healthy","timestamp":"..."}

# Check that the real client IP appears in Flask logs:
docker logs shekel-app --tail 10 | grep remote_addr
# Expected: "remote_addr": "<your-external-IP>" (not 127.0.0.1)
```

#### Staging Subdomain Test Procedure (Risk R6)

Before pointing the production subdomain to the tunnel:

1. **Create a staging DNS record** (Step 4 above) for `staging-budget.example.com`.
2. **Uncomment the staging ingress rule** in `cloudflared/config.yml`.
3. **Test all critical flows** through `https://staging-budget.example.com`:
   - Health endpoint: `curl https://staging-budget.example.com/health`
   - Login page loads: open in browser, verify CSS/JS load correctly
   - Login succeeds: authenticate with credentials
   - MFA flow works (if enabled): verify TOTP prompt appears
   - Rate limiting: submit 6 rapid login attempts, verify 429 response
   - Real IP: check Flask logs for your actual IP
4. **Once staging is verified**, update the config to use the production domain and restart cloudflared.
5. **Remove the staging DNS record** and comment out the staging ingress rule.

#### Test Gate

- [ ] `cloudflared --version` runs successfully on the Proxmox host
- [ ] `cloudflared tunnel list` shows the `shekel` tunnel
- [ ] `cloudflared/config.yml` template exists in the repository with placeholder values
- [ ] The tunnel routes traffic: `curl https://<domain>/health` returns 200
- [ ] The health endpoint returns `{"status":"healthy"}` through the tunnel
- [ ] The login page renders correctly through the tunnel (CSS/JS load from `/static/`)
- [ ] Real client IP appears in Flask logs (not 127.0.0.1 or Docker bridge IP)
- [ ] `systemctl status cloudflared` shows the service running and enabled
- [ ] Staging subdomain test procedure completed successfully before production cutover

---

### WU-3: Cloudflare Access Policy

**Goal:** Configure a Cloudflare Access (zero-trust) policy that restricts access to the Shekel application to allowed email addresses. Unauthorized users are blocked before requests reach the origin server.

**Depends on:** WU-2 (the tunnel must be routing traffic so Access can be tested).

#### How Cloudflare Access Works

Cloudflare Access sits between the client and the tunnel. When a user navigates to the protected domain:

1. Cloudflare intercepts the request.
2. If the user has no valid Access session, Cloudflare shows a login page (configurable: email OTP, Google, GitHub, etc.).
3. The user authenticates via the configured identity provider.
4. If the user's identity matches an Access policy (e.g., email is in the allowed list), Cloudflare issues a session token (JWT cookie) and forwards the request to the tunnel.
5. Subsequent requests include the JWT cookie and bypass the Access login until the session expires.

This provides an additional authentication layer before requests even reach Nginx/Flask. An attacker who does not have an allowed email address cannot reach the login page, the health endpoint, or any other route.

#### Configuration Procedure

All configuration is done in the Cloudflare dashboard (Zero Trust section). No code changes are required.

##### Step 1: Navigate to Zero Trust Dashboard

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Select your account.
3. In the left sidebar, click **Zero Trust** (or navigate to `https://one.dash.cloudflare.com`).

##### Step 2: Create an Access Application

1. Navigate to **Access** → **Applications**.
2. Click **Add an application**.
3. Select **Self-hosted**.
4. Configure the application:

| Field | Value | Notes |
|-------|-------|-------|
| **Application name** | `Shekel Budget App` | Human-readable name shown in the Access dashboard |
| **Session Duration** | `24 hours` | How long the Access session lasts before re-authentication. 24 hours is reasonable for a daily-use personal app. |
| **Application domain** | `budget.example.com` | Must match the domain configured in the tunnel. Use your actual domain. |
| **Path** | *(leave empty)* | Protects the entire domain. Do not set a path unless you want to protect only a subdirectory. |

5. Click **Next**.

##### Step 3: Configure the Access Policy

1. **Policy name:** `Allowed Users`
2. **Action:** `Allow`
3. **Configure rules:**

| Rule Type | Selector | Value |
|-----------|----------|-------|
| **Include** | Emails | `your-email@example.com` |

To add additional users later (e.g., family members), add more email addresses to the Include rule.

4. Click **Next**.

##### Step 4: Configure Authentication Method

1. Under **Authentication**, select the identity providers to enable.
2. For a personal app, **One-time PIN (email OTP)** is the simplest option:
   - Cloudflare sends a 6-digit code to the user's email address.
   - No external identity provider setup required.
   - The allowed email address receives the OTP and enters it to gain access.
3. Alternatively, enable **Google** or **GitHub** if you prefer social login:
   - Requires configuring an OAuth application in the respective provider's dashboard.
   - See Cloudflare's documentation for provider-specific setup.

**Recommended for single-user:** One-time PIN. It requires no external provider configuration and the email address is the only identity you need to verify.

##### Step 5: Review and Save

1. Review the application configuration.
2. Click **Save**.
3. The Access policy is now active. Requests to `budget.example.com` will be intercepted by Cloudflare Access.

##### Step 6: Bypass Health Endpoint (Optional but Recommended)

The `/health` endpoint is used by `deploy.sh` and Docker health checks. If these checks come from the Proxmox host through the tunnel, they will be blocked by Access. However, in the recommended setup (cloudflared on the host, health checks via localhost), health checks bypass the tunnel entirely. This bypass is only needed if external monitoring services (e.g., UptimeRobot) need to reach `/health`.

To bypass Access for `/health`:

1. In the Access application settings, click **Add a policy**.
2. **Policy name:** `Health Check Bypass`
3. **Action:** `Bypass`
4. **Configure rules:**

| Rule Type | Selector | Value |
|-----------|----------|-------|
| **Include** | Everyone | *(no value needed)* |

5. Under **Additional settings** → **Path**, set: `/health`
6. Ensure this Bypass policy is **above** the Allow policy in the policy order (policies are evaluated top-to-bottom).

##### Step 7: Test the Access Policy

**Test 1: Unauthorized access is blocked.**
```bash
# From an external network, without an Access session:
curl -v https://budget.example.com/login
```
Expected: HTTP 302 redirect to the Cloudflare Access login page (URL contains `cdn-cgi/access/login`), **not** the Flask login page.

**Test 2: Authorized access succeeds.**
1. Open `https://budget.example.com` in a browser.
2. Cloudflare shows the Access login page.
3. Enter your allowed email address.
4. Check your email for the OTP code.
5. Enter the code.
6. You should be redirected to the Flask login page.
7. Log in with your Shekel credentials.

**Test 3: Unauthorized email is rejected.**
1. Open `https://budget.example.com` in a private/incognito browser window.
2. On the Access login page, enter an email **not** in the allowed list (e.g., `unauthorized@example.com`).
3. Enter the OTP code sent to that email (if you control it) or observe that the email is not in the allowed list.
4. Expected: Access denies the request with a "You do not have access" message.

**Test 4: Health endpoint bypass (if configured).**
```bash
curl -s https://budget.example.com/health
```
Expected: `{"status":"healthy","timestamp":"..."}` (no Access challenge).

#### Adding a New Authorized User

To grant access to a new user (e.g., a family member):

1. Navigate to **Zero Trust** → **Access** → **Applications**.
2. Click on **Shekel Budget App**.
3. Edit the **Allowed Users** policy.
4. Under **Include** → **Emails**, add the new email address.
5. Click **Save**.
6. The new user can now authenticate via Cloudflare Access and reach the Shekel login page.

**Note:** This only grants access through the Cloudflare layer. The user still needs a Shekel account (created via seed script or future registration) to log into the application itself.

#### Test Gate

- [ ] Cloudflare Access application is configured for the Shekel domain
- [ ] Unauthenticated requests to the domain are redirected to the Access login page
- [ ] Authenticated requests with an allowed email reach the Flask application
- [ ] Requests with a non-allowed email are denied by Access
- [ ] Health endpoint bypass works (if configured)

---

### WU-4: Cloudflare WAF Rate Limiting

**Goal:** Configure Cloudflare WAF rate limiting rules on `/login` and `/auth/mfa/verify` to provide an outer layer of brute-force protection that blocks volumetric attacks before they reach the origin server.

**Depends on:** WU-2 (the tunnel must be routing traffic so WAF rules can be tested).

#### Configuration Procedure

All configuration is done in the Cloudflare dashboard (WAF section). No code changes are required.

##### Step 1: Navigate to WAF Rules

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Select your domain (e.g., `example.com`).
3. In the left sidebar, navigate to **Security** → **WAF**.
4. Click the **Rate limiting rules** tab.

##### Step 2: Create Rate Limit Rule for Login

1. Click **Create rule**.
2. Configure the rule:

| Field | Value | Notes |
|-------|-------|-------|
| **Rule name** | `Login brute force protection` | Descriptive name for the dashboard |
| **If incoming requests match...** | *(use Expression Editor)* | See expression below |
| **Expression** | `http.request.uri.path eq "/login" and http.request.method eq "POST"` | Matches only POST requests to /login |
| **With the same characteristics...** | `IP` | Rate limit per source IP address |
| **Rate** | `20 requests` | Threshold per period |
| **Period** | `10 seconds` | Time window |
| **Then take action...** | `Block` | Block requests that exceed the limit |
| **For duration** | `60 seconds` | How long the block lasts after the limit is exceeded |
| **With response type** | `Default Cloudflare rate limiting response` | Returns a Cloudflare-branded 429 page |

3. Click **Deploy**.

##### Step 3: Create Rate Limit Rule for MFA Verify

1. Click **Create rule**.
2. Configure the rule:

| Field | Value | Notes |
|-------|-------|-------|
| **Rule name** | `MFA brute force protection` | Descriptive name for the dashboard |
| **Expression** | `http.request.uri.path eq "/auth/mfa/verify" and http.request.method eq "POST"` | Matches only POST requests to MFA verify |
| **With the same characteristics...** | `IP` | Rate limit per source IP address |
| **Rate** | `20 requests` | Same threshold as login |
| **Period** | `10 seconds` | Same window as login |
| **Then take action...** | `Block` | Block exceeding requests |
| **For duration** | `60 seconds` | Same block duration |
| **With response type** | `Default Cloudflare rate limiting response` | Returns a 429 page |

3. Click **Deploy**.

##### Step 4: Verify Rule Order

In the WAF Rate limiting rules list, both rules should be active (green toggle). Rule order does not matter here since they match different paths.

##### Step 5: Test Rate Limiting

**Test 1: Normal usage is not affected.**
```bash
# Submit 3 login attempts (should all succeed, or return normal 401/302):
for i in 1 2 3; do
  curl -s -o /dev/null -w "%{http_code}" \
    -X POST -d "email=test@test.com&password=wrong" \
    https://budget.example.com/login
  echo ""
done
```
Expected: HTTP 200 or 302 for each request (not 429).

**Test 2: Rate limit triggers on rapid requests.**
```bash
# Submit 25 rapid POST requests (exceeds 20/10s threshold):
for i in $(seq 1 25); do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST -d "email=test@test.com&password=wrong" \
    https://budget.example.com/login
done
echo ""
```
Expected: First ~20 requests return 200/302. Remaining requests return 429 (Cloudflare rate limit page).

**Test 3: Block duration expires.**
```bash
# Wait 60 seconds after triggering the rate limit, then retry:
sleep 65
curl -s -o /dev/null -w "%{http_code}" \
  -X POST -d "email=test@test.com&password=wrong" \
  https://budget.example.com/login
echo ""
```
Expected: Request succeeds (200/302), confirming the 60-second block has expired.

**Test 4: GET requests are not rate limited.**
```bash
# Rapid GET requests to the login page should not trigger the rule:
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code} " https://budget.example.com/login
done
echo ""
```
Expected: All requests return 200 (the rate limit rule only matches POST).

#### Interaction with App-Level Rate Limiting

The two rate limiting layers work together:

```
Request → Cloudflare WAF (20/10s) → Cloudflare Access → cloudflared → Nginx → Flask-Limiter (5/15m) → Route
```

- A fast automated attack (>20 POST requests in 10 seconds) is blocked at Cloudflare -- it never reaches the origin server, so no Flask resources are consumed.
- A slow, persistent attack (1 request every 3 minutes, but wrong password each time) passes Cloudflare's rate limit but hits Flask-Limiter's 5/15m limit on the 6th attempt.
- A legitimate user with a few typos (2-3 attempts) is never rate limited by either layer.

#### Updating WAF Rules

To modify thresholds after deployment:

1. Navigate to **Security** → **WAF** → **Rate limiting rules**.
2. Click the rule name (e.g., `Login brute force protection`).
3. Click **Edit**.
4. Adjust the **Rate** or **Period** fields.
5. Click **Save**.

Changes take effect within seconds -- no deployment or restart required.

#### Test Gate

- [ ] Login rate limit rule is active in Cloudflare WAF dashboard
- [ ] MFA verify rate limit rule is active in Cloudflare WAF dashboard
- [ ] Normal login attempts (3-5) are not blocked
- [ ] Rapid login attempts (>20 in 10 seconds) trigger a 429 from Cloudflare
- [ ] Rate limit block expires after 60 seconds
- [ ] GET requests to `/login` are not rate limited

---

### WU-5: Log Shipping Validation

**Goal:** Verify that the Promtail configuration from Phase 8B correctly scrapes Shekel container logs and ships them to Loki, where they are queryable via Grafana. If adjustments are needed, document them.

**Depends on:** WU-2 (needs live traffic through the tunnel to generate realistic logs for validation).

#### Pre-existing State

The Promtail configuration (`monitoring/promtail-config.yml`) and monitoring stack setup guide (`monitoring/README.md`) were delivered in Phase 8B. The monitoring stack (Loki, Grafana, Promtail) runs as a separate docker-compose stack on the Proxmox host, connected to the Shekel stack via an external Docker network named `monitoring`.

#### Files to Modify

**`docker-compose.yml`** -- Add the external `monitoring` network to the `app` service so Promtail can discover and scrape its logs. This change was documented in `monitoring/README.md` (lines 27-39) but has not been applied to the production compose file.

Current `app` service networks (line 66-67):
```yaml
    networks:
      - backend
```

New:
```yaml
    networks:
      - backend
      - monitoring
```

Current networks section (lines 110-117):
```yaml
networks:
  # Frontend network: Nginx only.  Externally accessible via port mapping.
  frontend:
    driver: bridge
  # Backend network: all services.  Internal only -- not reachable from host.
  backend:
    driver: bridge
    internal: true
```

New (add after the `backend` network definition):
```yaml
networks:
  # Frontend network: Nginx only.  Externally accessible via port mapping.
  frontend:
    driver: bridge
  # Backend network: all services.  Internal only -- not reachable from host.
  backend:
    driver: bridge
    internal: true
  # Monitoring network: shared with the Loki/Grafana/Promtail stack.
  # Created externally: docker network create monitoring
  # The app service joins this network so Promtail can discover and scrape
  # its logs via Docker socket SD.
  monitoring:
    external: true
```

**Rationale:** Promtail uses Docker socket service discovery (SD) to find containers matching the label `com.docker.compose.service=app`. Docker SD returns container metadata regardless of which network the container is on -- the Docker socket provides access to all containers on the host. However, if Promtail needs to make HTTP connections to containers (for some scrape configs), they must share a network. More importantly, adding the `monitoring` network follows the architecture documented in `monitoring/README.md` and ensures consistency between documentation and configuration.

**Note:** The `monitoring` network must be created before starting the Shekel stack:
```bash
docker network create monitoring
```
If the network does not exist, `docker compose up` will fail with an error about the external network not being found. This is intentional -- it forces the operator to set up the monitoring network before deploying.

#### Validation Procedure

##### Step 1: Ensure the monitoring stack is running

```bash
# Check that the monitoring network exists:
docker network ls | grep monitoring

# If it doesn't exist:
docker network create monitoring

# Start the monitoring stack:
cd /path/to/monitoring
docker compose up -d

# Verify all three containers are running:
docker ps --filter "name=loki" --filter "name=promtail" --filter "name=grafana"
```

##### Step 2: Restart the Shekel stack with the monitoring network

```bash
cd /path/to/shekel
docker compose down
docker compose up -d

# Verify the app container is on the monitoring network:
docker network inspect monitoring | grep shekel-app
```

##### Step 3: Generate log traffic

```bash
# Access the app through the tunnel to generate realistic logs:
curl -s https://budget.example.com/health
curl -s https://budget.example.com/login

# Generate an auth event (failed login):
curl -s -X POST -d "email=test@test.com&password=wrong" \
  https://budget.example.com/login
```

##### Step 4: Verify logs appear in container stdout

```bash
# Confirm the app container outputs JSON logs:
docker logs shekel-app --tail 5
```

Expected: JSON lines with `level`, `logger`, `message`, `request_id`, `event`, `category`, `remote_addr` fields.

Example:
```json
{"level": "INFO", "logger": "app.routes.auth", "message": "Login failed", "request_id": "...", "event": "login_failed", "category": "auth", "remote_addr": "203.0.113.45"}
```

##### Step 5: Verify Promtail is scraping

```bash
# Check Promtail logs for errors:
docker logs promtail --tail 20

# Check Promtail targets (should show the shekel-app container):
curl -s http://localhost:9080/targets | python -m json.tool
```

Expected: Promtail shows the `shekel-app` container as an active target with `state: "Active"`.

##### Step 6: Query logs in Grafana

1. Open Grafana: `http://<proxmox-ip>:3000`
2. Navigate to **Explore** (compass icon in the left sidebar).
3. Select **Loki** as the data source.
4. Run the following LogQL queries:

| Query | Expected Result |
|-------|----------------|
| `{container="shekel-app"}` | All Shekel app logs appear |
| `{container="shekel-app"} \| json \| category="auth"` | Auth events (login, logout) appear |
| `{container="shekel-app"} \| json \| event="login_failed"` | The failed login from Step 3 appears |
| `{container="shekel-app"} \| json \| level="ERROR"` | Any error-level logs (may be empty if no errors) |
| `{container="shekel-app"} \| json \| event="request_complete"` | Completed requests appear with duration |

5. Verify that parsed fields are available as labels in the log entries: `level`, `event`, `category`.

##### Step 7: Verify real client IP in Grafana

Run this LogQL query:
```
{container="shekel-app"} | json | event="login_failed" | line_format "{{.remote_addr}}"
```

Expected: The output shows the actual client IP (e.g., `203.0.113.45`), not `127.0.0.1` or a Docker bridge IP.

#### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No logs in Grafana | Promtail not discovering the container | Check `docker logs promtail` for errors. Verify Docker socket is mounted: `docker inspect promtail \| grep docker.sock` |
| Promtail shows target but no logs | Label filter mismatch | Verify the container has label `com.docker.compose.service=app`: `docker inspect shekel-app \| grep com.docker.compose.service` |
| JSON fields not parsed | Pipeline stage mismatch | Check that `docker logs shekel-app` outputs valid JSON. Verify the Promtail pipeline stages match the JSON field names |
| `remote_addr` shows wrong IP | WU-1 not applied correctly | Re-verify the Nginx `real_ip_header` configuration per WU-1 |
| Grafana cannot connect to Loki | Network issue | Verify Grafana and Loki are on the same Docker network: `docker network inspect monitoring` |

#### Test Gate

- [ ] Application logs appear in JSON format in container stdout (`docker logs shekel-app`)
- [ ] Promtail discovers and scrapes the Shekel app container
- [ ] Logs appear in Grafana/Loki when queried via LogQL
- [ ] JSON fields (`level`, `event`, `category`, `request_id`) are parsed and available as labels
- [ ] Real client IP (from tunnel traffic) appears in the `remote_addr` field in Grafana
- [ ] Auth events (login_success, login_failed) are queryable in Grafana

---

### WU-6: Runbook Consolidation

**Goal:** Consolidate all operational documentation produced across Phases 8A through 8D into a single `docs/runbook.md`. Organize by topic, cross-reference scripts, and write for the user's future self who has forgotten the setup details.

**Depends on:** All prior work units (WU-1 through WU-5). The runbook references procedures documented in each.

#### Files to Create

**`docs/runbook.md`** -- Unified operations runbook. This is the single document an operator should consult for any operational task.

Structure:

```markdown
# Shekel Operations Runbook

## Table of Contents

1. Quick Reference
2. Deployment
3. Backup & Restore
4. Security Operations
5. Monitoring & Observability
6. Cloudflare Management
7. Troubleshooting

---

## 1. Quick Reference

### Service Architecture

[Diagram: Client → Cloudflare Edge → Cloudflare Access → cloudflared (host) → Nginx :80 → Gunicorn :8000 → Flask → PostgreSQL]

### Key Paths on the Proxmox Host

| Path | Purpose |
|------|---------|
| `/opt/shekel/` | Application directory (clone of the git repository) |
| `/opt/shekel/.env` | Environment configuration (secrets, settings) |
| `/opt/shekel/docker-compose.yml` | Production Docker Compose |
| `/etc/cloudflared/config.yml` | Cloudflare Tunnel configuration |
| `/root/.cloudflared/` | Tunnel credentials |
| `/var/backups/shekel/` | Local backup storage |
| `/mnt/nas/backups/shekel/` | NAS backup storage |
| `/var/log/shekel_backup.log` | Backup cron log |

### Script Inventory

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/deploy.sh` | Deploy new version with rollback | `./scripts/deploy.sh [--skip-pull] [--skip-backup]` |
| `scripts/backup.sh` | Create database backup | `./scripts/backup.sh [--no-nas]` |
| `scripts/restore.sh` | Restore database from backup | `./scripts/restore.sh <backup_file>` |
| `scripts/verify_backup.sh` | Verify backup integrity | `./scripts/verify_backup.sh <backup_file>` |
| `scripts/backup_retention.sh` | Prune old backups | `./scripts/backup_retention.sh [--dry-run]` |
| `scripts/integrity_check.py` | Validate database integrity | `docker exec shekel-app python scripts/integrity_check.py [--verbose]` |
| `scripts/audit_cleanup.py` | Clean old audit log entries | `docker exec shekel-app python scripts/audit_cleanup.py` |
| `scripts/reset_mfa.py` | Emergency MFA reset | `docker exec shekel-app python scripts/reset_mfa.py <email>` |

### Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| 2:00 AM daily | `backup.sh` | Database backup |
| 2:30 AM daily | `backup_retention.sh` | Prune old backups |
| 3:00 AM daily | `audit_cleanup.py` | Audit log retention |
| 3:00 AM Sunday | `verify_backup.sh` | Weekly backup verification |
| 3:30 AM Sunday | `integrity_check.py` | Weekly integrity check |

---

## 2. Deployment

### Deploying a New Version

[deploy.sh usage, flags, what it does step-by-step, how rollback works]

### Manual Deployment

[Step-by-step: git pull, docker compose build, flask db upgrade, restart, health check]

### Rolling Back

[How deploy.sh auto-rolls back on health check failure. Manual rollback: docker compose up -d with previous image]

### First-Time Setup

[Clone repo, copy .env.example to .env, fill in secrets, create monitoring network, docker compose up -d, seed database]

---

## 3. Backup & Restore

[Consolidated from docs/backup_runbook.md -- backup strategy, automated setup, cron configuration, NAS mount, encryption, manual backup, retention policy, restore procedure, verification, integrity checks, troubleshooting]

---

## 4. Security Operations

### Secret Management

[Consolidated from docs/runbook_secrets.md -- secret inventory, rotation procedures, disaster recovery]

### Resetting MFA for a User

When: A user has lost their TOTP device and exhausted all backup codes.

1. SSH into the Proxmox host.
2. Run: `docker exec shekel-app python scripts/reset_mfa.py <user_email>`
3. The script disables MFA for the user. They can log in with just email + password.
4. The user should re-enable MFA via Settings > Security after logging in.

### Reviewing Audit Logs

[How to query system.audit_log via psql. How to check auth events in Grafana/Loki.]

### Changing Application Secrets

[Cross-reference to secret rotation procedures. Impact of each rotation.]

---

## 5. Monitoring & Observability

### Checking Application Logs

[docker logs shekel-app, JSON format, key fields to look for]

### Querying Logs in Grafana

[Open Grafana URL, select Loki, LogQL query reference table from monitoring/README.md]

### Key LogQL Queries

| Purpose | Query |
|---------|-------|
| All auth events | `{container="shekel-app"} \| json \| category="auth"` |
| Login failures | `{container="shekel-app"} \| json \| event="login_failed"` |
| Slow requests | `{container="shekel-app"} \| json \| event="slow_request"` |
| All errors | `{container="shekel-app"} \| json \| level="ERROR"` |
| By user | `{container="shekel-app"} \| json \| user_id="1"` |
| By request | `{container="shekel-app"} \| json \| request_id="<uuid>"` |

### Monitoring Stack Management

[Start/stop Loki/Grafana/Promtail. Verify Promtail targets. Grafana data source config.]

### Health Checks

[/health endpoint. Docker health checks. deploy.sh health verification.]

---

## 6. Cloudflare Management

### Tunnel Status

[systemctl status cloudflared. journalctl -u cloudflared. cloudflared tunnel list.]

### Restarting the Tunnel

[sudo systemctl restart cloudflared. When to restart (after config changes).]

### Adding a New Authorized User (Cloudflare Access)

[Step-by-step: Zero Trust dashboard → Access → Applications → edit policy → add email]

### Updating WAF Rate Limit Rules

[Step-by-step: Cloudflare dashboard → Security → WAF → Rate limiting rules → edit]

### Tunnel Configuration Changes

[Edit /etc/cloudflared/config.yml. Restart cloudflared. Test connectivity.]

### Rotating Tunnel Credentials

[cloudflared tunnel delete + recreate. Update config.yml with new tunnel ID. Restart.]

---

## 7. Troubleshooting

### Common Issues

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| App unreachable externally | cloudflared service down | `sudo systemctl status cloudflared` → `sudo systemctl restart cloudflared` |
| App unreachable externally | Docker containers down | `docker compose ps` → `docker compose up -d` |
| "Access Denied" on all requests | Cloudflare Access misconfigured | Check Access policy in Zero Trust dashboard; verify email is in allowed list |
| Cloudflare 502 error | Nginx or app container unhealthy | `docker compose ps` → check health. `docker logs shekel-nginx` / `docker logs shekel-app` |
| 429 on first login attempt | Cloudflare rate limit too aggressive | Check WAF rate limit rules in Cloudflare dashboard; increase threshold |
| 429 after a few attempts | Flask-Limiter rate limit | Wait 15 minutes. Or restart app container to clear in-memory rate limit state. |
| Wrong IP in logs (127.0.0.1) | Nginx real IP config missing | Verify `set_real_ip_from` and `real_ip_header` in nginx/nginx.conf |
| No logs in Grafana | Promtail not scraping | `docker logs promtail`. Verify monitoring network exists. Verify app is on monitoring network. |
| Database backup failed | Container down or disk full | `docker ps` to check shekel-db. `df -h` to check disk. |
| NAS backup failed | NAS not mounted | `mount \| grep nas`. `sudo mount -a`. |
| Health check returns 500 | Database connection issue | `docker logs shekel-app --tail 20`. `docker exec shekel-db pg_isready` |
| CSS/JS not loading | Static files volume issue | `docker exec shekel-nginx ls /var/www/static/` → verify files exist |

### Log Locations

| Log | Command | Contents |
|-----|---------|----------|
| Flask app (JSON) | `docker logs shekel-app` | Request logs, auth events, business events |
| Nginx (JSON) | `docker logs shekel-nginx` | HTTP access logs, errors |
| PostgreSQL | `docker logs shekel-db` | Database server logs |
| Cloudflared | `journalctl -u cloudflared` | Tunnel connection logs |
| Backups | `cat /var/log/shekel_backup.log` | Cron job output |
| Grafana | `docker logs grafana` | Grafana server logs |

### Emergency Procedures

#### App is down -- restore service quickly
1. `docker compose ps` -- identify which container is unhealthy
2. `docker compose restart <service>` -- restart the unhealthy service
3. `curl http://localhost/health` -- verify recovery
4. If the app container won't start: `docker logs shekel-app --tail 50` -- check for errors

#### Database is corrupted -- restore from backup
1. Identify the latest good backup: `ls -lht /var/backups/shekel/`
2. Run the restore: `./scripts/restore.sh <backup_file>`
3. Verify: `docker exec shekel-app python scripts/integrity_check.py`

#### Locked out of the app (MFA lost)
1. SSH to the Proxmox host
2. `docker exec shekel-app python scripts/reset_mfa.py <your-email>`
3. Log in with email + password (MFA is now disabled)
4. Re-enable MFA in Settings > Security

#### Locked out of Cloudflare Access
1. Log into the Cloudflare dashboard directly (this is independent of the tunnel)
2. Navigate to Zero Trust → Access → Applications
3. Temporarily set the policy to allow everyone, or add your current email
4. Access the app and fix the underlying issue
5. Restore the original policy
```

The above is the structural outline. The full content of each section is written by pulling from the existing documentation sources and the procedures documented in WU-1 through WU-5 of this plan. Specifically:

- **§2 Deployment:** From `scripts/deploy.sh` usage comments (lines 11-19), `docs/phase_8d2_implementation_plan.md`, and `.env.example`.
- **§3 Backup & Restore:** From `docs/backup_runbook.md` (incorporated in full with minor editorial adjustments for consistency).
- **§4 Security Operations:** From `docs/runbook_secrets.md` (incorporated in full) plus MFA reset documentation from the Phase 8A plan.
- **§5 Monitoring:** From `monitoring/README.md` (LogQL queries, Grafana setup) plus WU-5 validation results.
- **§6 Cloudflare Management:** From WU-2 (tunnel setup), WU-3 (Access policy), and WU-4 (WAF rules) of this plan.
- **§7 Troubleshooting:** Consolidated from all existing runbooks plus new Cloudflare-specific issues.

#### Handling Existing Documentation Files

After the consolidated runbook is complete:

- **`docs/backup_runbook.md`**: Retain as-is. The consolidated runbook's §3 references it for the detailed backup procedures, or incorporates its content directly. Either approach works -- the key is that `docs/runbook.md` is the single entry point.
- **`docs/runbook_secrets.md`**: Retain as-is. Referenced from §4 of the consolidated runbook.
- **`monitoring/README.md`**: Retain as-is. Referenced from §5 for monitoring stack setup details.

The consolidated runbook acts as an index and quick-reference that links to the detailed sub-documents where appropriate, while including all essential procedures directly so the operator does not need to cross-reference multiple files for common operations.

#### Test Gate

- [ ] `docs/runbook.md` exists and covers all seven sections
- [ ] Every script in `scripts/` is referenced in the runbook with usage examples
- [ ] Cloudflare tunnel, Access, and WAF management procedures are documented
- [ ] Secret rotation procedures are documented
- [ ] MFA reset procedure is documented
- [ ] Backup/restore procedures are documented
- [ ] Troubleshooting table covers all common failure modes
- [ ] Emergency procedures are documented for: app down, database corrupt, MFA lockout, Access lockout

---

## Complete Test Plan

### Manual Verification Runbook

This is a numbered checklist to be executed sequentially after all work units are complete. It covers the full production stack.

1. **Nginx config validation.** Run `docker exec shekel-nginx nginx -t`. Expect: syntax OK.
2. **Real IP module present.** Run `docker exec shekel-nginx nginx -V 2>&1 | grep http_realip_module`. Expect: `http_realip_module`.
3. **Container health.** Run `docker compose ps`. Expect: all three services (db, app, nginx) show `healthy`.
4. **Health endpoint (local).** Run `curl -s http://localhost/health`. Expect: `{"status":"healthy","timestamp":"..."}`.
5. **Tunnel service status.** Run `sudo systemctl status cloudflared`. Expect: `active (running)`.
6. **Health endpoint (external).** Run `curl -s https://<domain>/health`. Expect: `{"status":"healthy","timestamp":"..."}`.
7. **Access policy blocks unauthenticated request.** Run `curl -sI https://<domain>/login`. Expect: HTTP 302 redirect to `cdn-cgi/access/login`.
8. **Access policy allows authenticated request.** Open `https://<domain>` in browser, complete Access login with allowed email. Expect: Flask login page appears.
9. **Login works through tunnel.** Submit valid credentials through the browser. Expect: redirect to budget grid.
10. **MFA works through tunnel (if enabled).** After password step, TOTP prompt appears. Enter valid code. Expect: login completes.
11. **Real client IP in Flask logs.** Run `docker logs shekel-app --tail 10 | grep remote_addr`. Expect: your actual external IP, not 127.0.0.1.
12. **Real client IP in Nginx logs.** Run `docker logs shekel-nginx --tail 10 | grep remote_addr`. Expect: your actual external IP.
13. **WAF rate limit on login.** From a test machine, run 25 rapid POST requests to `/login`. Expect: first ~20 succeed, remaining return 429.
14. **WAF rate limit block expires.** Wait 65 seconds, retry one POST to `/login`. Expect: request succeeds.
15. **Flask-Limiter rate limit on login.** Submit 6 login attempts within 15 minutes (below Cloudflare threshold). Expect: 6th attempt returns 429 from Flask (with `Retry-After: 900` header).
16. **JSON logs in container stdout.** Run `docker logs shekel-app --tail 3`. Expect: valid JSON with `level`, `logger`, `message`, `request_id` fields.
17. **Promtail target active.** Run `curl -s http://localhost:9080/targets`. Expect: `shekel-app` container listed with `Active` state.
18. **Logs in Grafana/Loki.** Open Grafana → Explore → Loki. Query: `{container="shekel-app"}`. Expect: recent log entries appear.
19. **Auth events in Grafana.** Query: `{container="shekel-app"} | json | category="auth"`. Expect: login events from steps 9-10 appear.
20. **Parsed fields available.** In Grafana log entries, verify that `level`, `event`, `category` appear as labels (not just raw JSON).
21. **Runbook exists.** Verify `docs/runbook.md` exists and opens correctly.
22. **Static files through tunnel.** In the browser, verify CSS and JS load correctly (page is styled, HTMX works).

### End-to-End Smoke Test Sequence

Execute this sequence after completing all Phase 8D-3 work to verify the full production stack.

| # | Check | Method | Expected Result | Covers |
|---|-------|--------|-----------------|--------|
| 1 | Cloudflare Tunnel routes traffic | `curl https://<domain>/health` | HTTP 200 with JSON body | 8D test gate: tunnel |
| 2 | Access blocks unauthorized users | `curl -sI https://<domain>/login` (no Access cookie) | HTTP 302 to Access login | 8D test gate: Access |
| 3 | Access allows authorized users | Browser login with allowed email OTP | Flask login page loads | 8D test gate: Access |
| 4 | App login works end-to-end | Submit credentials through browser | Budget grid loads | Functional |
| 5 | WAF rate limit triggers | 25 rapid POST to `/login` | 429 after ~20 requests | 8D scope: WAF |
| 6 | Flask-Limiter triggers | 6 slow POST to `/login` in 15m | 429 with Retry-After: 900 | 8A/8D interaction |
| 7 | JSON logs in stdout | `docker logs shekel-app --tail 3` | Valid JSON | 8D test gate: JSON logs |
| 8 | Promtail scrapes logs | `curl http://localhost:9080/targets` | Active target | 8D test gate: Promtail |
| 9 | Logs in Grafana/Loki | Grafana → Loki → `{container="shekel-app"}` | Entries appear | 8D test gate: Grafana |
| 10 | Real client IP in logs | grep `remote_addr` in Flask logs | External IP, not 127.0.0.1 | IP propagation |
| 11 | Health endpoint full chain | `curl https://<domain>/health` | `{"status":"healthy"}` | Full stack |
| 12 | Static files served | Browser: inspect network tab for CSS/JS | 200 from Nginx, cache headers | Nginx |

---

## Phase 8D-3 Test Gate Checklist

These are the master plan (Phase 8D) test gate items that 8D-3 is responsible for, mapped to specific verification steps.

- [ ] **Cloudflare Tunnel routes traffic to the app**
  - Verification: Smoke test #1 -- `curl https://<domain>/health` returns 200
  - Work unit: WU-2

- [ ] **Cloudflare Access blocks unauthenticated requests**
  - Verification: Smoke test #2 -- unauthenticated `curl` is redirected to Access login; smoke test #3 -- authorized user reaches Flask
  - Work unit: WU-3

- [ ] **Application logs appear in JSON format in container stdout**
  - Verification: Smoke test #7 -- `docker logs shekel-app` outputs valid JSON with structured fields
  - Work unit: WU-5 (validation only; logging was implemented in 8B)

- [ ] **Promtail (or equivalent) scrapes logs and they appear in Grafana/Loki**
  - Verification: Smoke test #8 (Promtail target active) + #9 (logs appear in Grafana)
  - Work unit: WU-5

---

## File Summary

### New Files (2)

| File | Type | WU |
|------|------|----|
| `cloudflared/config.yml` | YAML template (placeholders) | 2 |
| `docs/runbook.md` | Operations documentation | 6 |

### Modified Files (2)

| File | Changes | WU |
|------|---------|-----|
| `nginx/nginx.conf` | Add `set_real_ip_from`, `real_ip_header CF-Connecting-IP`, `real_ip_recursive off` directives (insert after line 104) | 1 |
| `docker-compose.yml` | Add `monitoring` external network to `app` service and `networks` section | 5 |
