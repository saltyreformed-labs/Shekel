# Shekel Operations Runbook: Security Operations

Section 4 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

## 4. Security Operations

### 4.1 Secret Inventory

The application requires three secrets for production operation. All are stored in `.env` on the
Proxmox host.

| Secret | Purpose | Generation Command |
|--------|---------|-------------------|
| `SECRET_KEY` | Flask session cookie encryption | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `TOTP_ENCRYPTION_KEY` | Fernet encryption of TOTP secrets in database | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` | PostgreSQL database authentication | Any strong password generator |

For complete secret rotation procedures, see `docs/runbook_secrets.md`.

### 4.2 Rotating SECRET_KEY

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `SECRET_KEY` in `.env`
3. Restart the app: `docker compose restart app`
4. **Impact:** All active sessions are invalidated. Users must log in again.

### 4.3 Rotating TOTP_ENCRYPTION_KEY

**WARNING: Changing this key makes ALL existing MFA enrollments unreadable.**

1. Disable MFA for all users: `docker exec shekel-prod-app python scripts/reset_mfa.py --all`
2. Generate a new key:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
3. Update `TOTP_ENCRYPTION_KEY` in `.env`
4. Restart the app: `docker compose restart app`
5. Users re-enroll MFA via Settings > Security

### 4.4 Rotating POSTGRES_PASSWORD

1. Generate a new password
2. Update `POSTGRES_PASSWORD` in `.env`
3. Restart both containers: `docker compose down && docker compose up -d`

### 4.5 Resetting MFA for a User

**When:** A user has lost their TOTP device and exhausted all backup codes.

```bash
# SSH into the Proxmox host.
docker exec shekel-prod-app python scripts/reset_mfa.py admin@shekel.local
```

Output: `MFA has been disabled for admin@shekel.local.`

The user can now log in with email + password only. They should re-enable MFA via Settings >
Security after logging in.

### 4.6 Reviewing Audit Logs

**Via database (psql):**

```bash
# Recent changes to transactions.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
  "SELECT executed_at, operation, row_id, changed_fields
   FROM system.audit_log
   WHERE table_name = 'transactions'
   ORDER BY executed_at DESC LIMIT 20;"

# Changes by a specific user.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c \
  "SELECT executed_at, table_name, operation, row_id
   FROM system.audit_log
   WHERE user_id = 1
   ORDER BY executed_at DESC LIMIT 20;"
```

**Via Grafana/Loki (application-level auth events):**

See [§5.3 Key LogQL Queries](5_monitoring.md#53-key-logql-queries).

### 4.7 Audit Log Retention

Old audit log rows are cleaned up automatically by cron (daily at 3:00 AM). The default retention
period is 365 days, configurable via the `AUDIT_RETENTION_DAYS` environment variable.

```bash
# Preview what would be deleted.
docker exec shekel-prod-app python scripts/audit_cleanup.py --dry-run

# Manual cleanup with a custom retention period.
docker exec shekel-prod-app python scripts/audit_cleanup.py --days 90
```

### 4.8 Backing Up the .env File

The `.env` file contains all three production secrets. Include it in your backup strategy:

```bash
# Add to the backup cron job (after the database backup):
cp /opt/shekel/.env /mnt/nas/backups/shekel/env_backup
```

Alternatively, store the three secrets in a password manager (e.g., Bitwarden, 1Password) as a
separate recovery path.

### 4.9 HSTS Preload Decision

The Flask after-request hook in `app/__init__.py:_register_security_headers` emits
`Strict-Transport-Security: max-age=31536000; includeSubDomains` on every response. The `preload`
directive is intentionally OFF.

**Why this matters.** `preload` is an instruction to browsers to honor HSTS for the domain even on
the very first visit -- before any HTTP response could carry the header. It works by submitting the
domain to a list maintained by the Chromium project (consumed by Chrome, Firefox, Safari, Edge).
Once a domain is on the list, **delisting takes 6-12 weeks AND requires removing the `preload`
directive from the HSTS header for the duration**. During that window, every subdomain MUST be
HTTPS-only.

**To enable preload (ONLY when you are certain you are committing for years).**

1. Confirm every subdomain you operate is HTTPS-only. Test with
   `curl -I http://<subdomain>.<your-domain>/` and verify a 301 to <https://>.
2. Edit `app/__init__.py` and add `; preload` to the `Strict-Transport-Security` value.
3. Deploy. Run for at least one week to confirm no users hit `https://<subdomain>` and get a TLS
   error.
4. Submit the apex domain at <https://hstspreload.org/>. Wait for the email confirmation (typically
   4-12 weeks for inclusion in browser releases).

**To disable preload after submission.**

Removing `preload` from the header is a precondition for delisting; it does not cause delisting on
its own. Submit a removal request at <https://hstspreload.org/removal/>. Allow 6-12 weeks before any
subdomain can go back to HTTP.

### 4.10 CDN Vendor Refresh Procedure

The application serves Bootstrap, Bootstrap Icons, htmx, Chart.js, Inter, and JetBrains Mono from
`app/static/vendor/` rather than a CDN (audit F-037). The CSP forbids external script/style/font
origins so a refresh requires both fetching the new file and updating the manifest.

**Manifest.** `app/static/vendor/VERSIONS.txt` records each upstream URL and its SHA-384 hash. The
hash is the source of truth for which exact bytes are served.

**Refresh procedure.**

1. **Bootstrap, Bootstrap Icons, htmx, Chart.js**

   ```bash
   cd app/static/vendor
   # Replace the file with the desired version.
   curl -fL <upstream URL> -o <vendor-path>
   # Compute the new SHA-384.
   openssl dgst -sha384 -binary <vendor-path> | openssl base64 -A
   ```

   Update the matching line in `VERSIONS.txt` with the new URL and hash.

2. **Inter / JetBrains Mono fonts.** Run the helper script:

   ```bash
   python scripts/vendor_google_fonts.py
   ```

   The script fetches the upstream Google Fonts CSS, downloads the latin and latin-ext woff2 files,
   rewrites URLs to local paths, and emits a fresh `app/static/vendor/fonts/fonts.css`. After
   running, recompute the SHA-384 hashes for `fonts.css` and each `*.woff2` and update
   `VERSIONS.txt`.

3. **Verify.** Start the app and exercise every dashboard (analytics, debt strategy, investment,
   loan, retirement) plus the budget grid. The browser DevTools Network tab should show every asset
   loading from `/static/vendor/...` with no CSP violations in the Console.

4. **Test.** Run the security-headers and cache-control test suites:

   ```bash
   pytest tests/test_integration/test_security_headers.py \
          tests/test_adversarial/test_cache_control.py -v
   ```

   The `test_static_asset_path_resolves` test ensures every vendored asset referenced by templates
   exists at its expected path.

5. **Commit.** One commit per refresh; commit message names the package and version (e.g.
   `chore(vendor): bump Chart.js 4.4.7 -> 4.5.0`).

### 4.11 Docker Daemon Hardening Defaults (Commit C-35)

The compose files in this repo apply per-container hardening
(`security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true`, resource caps, log
rotation) to every Shekel service. This section documents the matching daemon-level defaults the
operator should apply on the host so co-tenant containers managed outside this repo (jellyfin,
immich, unifi) inherit the same posture without per-stack edits. Audit findings F-055 (daemon-level
no-new-privileges) and F-116 (default log rotation), docker-bench checks 2.5 and 2.14.

**File path.** `/etc/docker/daemon.json` on the Proxmox host. This file is read by the Docker daemon
at startup and applies its defaults to every container the daemon launches, including those managed
by `/opt/docker/docker-compose.yml` outside this project. Changes to this file require a daemon
reload (or restart) to take effect; existing containers continue with the settings they were created
under and pick up the new defaults only on `up --force-recreate`.

**Recommended baseline.** Create or merge into `daemon.json`:

```json
{
  "no-new-privileges": true,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true,
  "userland-proxy": false
}
```

Field-by-field rationale:

| Field | Purpose |
|-------|---------|
| `no-new-privileges` | Blocks privilege elevation via setuid binaries inside any container.  Per-container `security_opt` in compose is still set explicitly (defense-in-depth against the daemon default being removed). |
| `log-driver` + `log-opts` | Default log rotation for containers that do not declare their own `logging:` block.  Matches Shekel's per-service settings; affects co-tenant containers like immich_redis whose authors did not configure rotation. |
| `live-restore` | Lets containers keep running across `systemctl restart docker`.  Avoids unnecessary downtime during daemon upgrades. |
| `userland-proxy` | Disables the userland docker-proxy and routes via iptables NAT instead.  Reduces attack surface; the proxy is mostly relevant for IPv6 hairpinning that Shekel does not need. |

**Apply procedure (one-time, host-side).**

1. Back up the existing file (if any):

   ```bash
   sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak.$(date +%Y%m%d) 2>/dev/null || true
   ```

2. Write the recommended config:

   ```bash
   sudo install -m 0644 -o root -g root /dev/stdin /etc/docker/daemon.json <<'EOF'
   {
     "no-new-privileges": true,
     "log-driver": "json-file",
     "log-opts": {
       "max-size": "10m",
       "max-file": "3"
     },
     "live-restore": true,
     "userland-proxy": false
   }
   EOF
   ```

3. Validate the JSON parses cleanly:

   ```bash
   sudo docker info --format '{{json .}}' >/dev/null  # confirms daemon still reads its config
   sudo dockerd --validate --config-file /etc/docker/daemon.json
   ```

4. Reload the daemon:

   ```bash
   sudo systemctl reload docker
   ```

   `systemctl reload` re-reads `daemon.json` without killing containers (the live-restore setting
   above is what allows this). Use `systemctl restart docker` only if reload reports the option is
   unsupported on the installed Docker version.

5. Verify the daemon picked up the new defaults:

   ```bash
   docker info | grep -E '(no-new-privileges|Live Restore|Logging Driver|Default Runtime)'
   ```

6. Force-recreate the Shekel stack to pick up the daemon-level logging defaults on existing
   containers:

   ```bash
   cd /opt/docker/shekel
   sudo docker compose up -d --force-recreate
   ```

   The `live-restore` change is daemon-only; it does not require container recreation. Per-container
   `security_opt` in `docker-compose.yml` already sets `no-new-privileges` on each Shekel service,
   so the daemon default is redundant for this stack -- it is still set so that co-tenant containers
   and any future stack on this host gets the same posture by default.

**Rollback.** Restore the backup taken in step 1 and reload the daemon:

```bash
sudo cp /etc/docker/daemon.json.bak.<DATE> /etc/docker/daemon.json
sudo systemctl reload docker
```

### 4.12 Postgres TLS for Shared Mode (Commit C-37)

The shared-mode override at `deploy/docker-compose.prod.yml` enables `ssl=on` on the Postgres
service so the Gunicorn -> Postgres hop is encrypted on the wire. The base `docker-compose.yml`
keeps Postgres on plaintext TCP for the README Quick Start, where a fresh-host operator does not yet
have a cert generated. Audit finding F-154.

**Scope.** TLS applies only when the shared-mode override is active. Bundled-mode deployments (the
README Quick Start) intentionally skip this section and continue running plaintext within the
internal-only `backend` Docker bridge.

**File path.** `deploy/postgres/server.crt` and `deploy/postgres/server.key` on the operator's host.
Both files are gitignored; the directory is committed empty (apart from a `README.md`) so the
bind-mount source path resolves on a fresh clone.

#### 4.12.1 Generating the certificate (one-time, before first up)

```bash
cd /opt/shekel
sudo ./scripts/generate_pg_cert.sh
```

Sudo is required because `server.key` must be chowned to uid 70 (the `postgres` user inside
`postgres:18-alpine`) for Postgres to accept it under the mandatory 0600 mode. The script:

1. Generates an RSA-2048 keypair via `openssl req -x509 -nodes`.
2. Embeds a Subject Alternative Name list covering `shekel-prod-db`, `db`, and `localhost` so a
   future upgrade to `sslmode=verify-full` works without regeneration.
3. Sets `server.crt` to mode 0644 (root-owned, world-readable) and `server.key` to mode 0600 (uid
   70, group 70).
4. Re-reads both files and verifies the cert parses cleanly, the key passes `openssl rsa -check`,
   and the public keys match.
5. Prints the not-after date so the operator can diary the rotation.

Defaults: 825 days, CN `shekel-prod-db`. Pass `--days N`, `--cn HOSTNAME`, or `--output-dir DIR` to
override; `--help` lists every flag.

#### 4.12.2 Bringing Postgres up with TLS

```bash
cd /opt/shekel
docker compose \
    -f docker-compose.yml \
    -f deploy/docker-compose.prod.yml \
    up -d
```

The override:

- Mounts the cert/key read-only into `/etc/postgresql/certs/` inside the db container.
- Adds this to the db service's `command` so the postgres process loads the cert at startup:

  ```text
  postgres -c ssl=on -c ssl_cert_file=... -c ssl_key_file=... -c ssl_min_protocol_version=TLSv1.2 -c ssl_ciphers=...
  ```

- Overrides `DATABASE_URL` on the app service with `?sslmode=require` so the SQLAlchemy engine
  refuses any connection the server cannot upgrade to TLS.
- Sets `DB_SSLMODE=require` so `entrypoint.sh` constructs `DATABASE_URL_APP` (the least-privilege
  `shekel_app` role's URL) with the same `?sslmode=require` posture.
- Sets `PGSSLMODE=require` so every `psql` call in `entrypoint.sh` picks up the same setting via the
  standard libpq env var.

#### 4.12.3 Verifying the TLS channel

After the stack starts, confirm Postgres negotiated TLS for the app's connection pool:

```bash
# View the server-side ssl setting -- must report "on".
docker exec shekel-prod-db psql -U shekel_user -d shekel \
    -c "SHOW ssl;"

# View the active TLS context for connected backends.  Shekel uses
# 2-4 connections (Gunicorn workers + idle pool) so this lists
# each one with its negotiated TLS version and cipher.
docker exec shekel-prod-db psql -U shekel_user -d shekel \
    -c "SELECT datname, usename, ssl, version, cipher
        FROM pg_stat_ssl
        JOIN pg_stat_activity USING (pid)
        WHERE datname = 'shekel';"
```

Expected output:

- `SHOW ssl;` returns `on`.
- `pg_stat_ssl` rows show `ssl = t`, a `version` of `TLSv1.2` or `TLSv1.3`, and a non-empty
  `cipher`.

If `ssl = off` after the override is applied, see Troubleshooting §7.4 below.

#### 4.12.4 Rotating the certificate

The script's not-after date is the rotation trigger. Rotate well before expiry to avoid an outage
when libssl rejects an expired cert:

```bash
cd /opt/shekel
sudo ./scripts/generate_pg_cert.sh --force
docker compose \
    -f docker-compose.yml \
    -f deploy/docker-compose.prod.yml \
    restart db
```

A `restart db` is enough; the postgres process re-reads `ssl_cert_file` / `ssl_key_file` on every
startup. The app does NOT need to be restarted -- the restart closes the app's pooled connections,
`ProdConfig`'s `pool_pre_ping` replaces each closed one at its next checkout, and the brief 1-2s
outage is well under the app's healthcheck `start_period`.

**Rotation impact.** Active connections drop during the restart but the SQLAlchemy engine reconnects
on the next request. Login sessions survive (sessions live in Flask's secure cookie, not the DB
connection). No user-visible downtime beyond the restart window.

#### 4.12.5 Cleartext fallback (emergency only)

If the cert is corrupted or the operator needs to rule out TLS as a cause of an outage, fall back to
cleartext by removing the override's TLS environment from a fresh shell:

```bash
# Comment out (or delete) the db.command, db.volumes mount lines,
# and app.environment.{DATABASE_URL, DB_SSLMODE, PGSSLMODE} entries
# in deploy/docker-compose.prod.yml on the host (NOT the repo
# copy -- this is an emergency-only override).
docker compose \
    -f docker-compose.yml \
    -f deploy/docker-compose.prod.yml \
    up -d --force-recreate db app
```

Restore the override from the repo copy as soon as the underlying issue is resolved:

```bash
cd /opt/shekel
git checkout dev -- deploy/docker-compose.prod.yml
docker compose \
    -f docker-compose.yml \
    -f deploy/docker-compose.prod.yml \
    up -d --force-recreate db app
```

### 4.13 Migration to Per-Service Hardening (Commit C-35)

When bringing an existing Shekel deployment up under the C-35 hardening (`user: postgres` on db,
`user: nginx` on nginx, both under `cap_drop: ALL` and `read_only: true`), two pre-existing state
items can trip the first start. Address them before issuing `docker compose up -d --force-recreate`
to recreate the containers.

**1. Verify the `shekel-prod-pgdata` volume is owned by uid 70.**

The pinned `user: postgres` directive on the db service is what lets the container start under
`cap_drop: ALL` (without CAP_CHOWN the entrypoint cannot chown PGDATA on the way in -- see the
in-line compose comment). Production volumes initialised under the previous root-mode entrypoint
should already be postgres-owned because that older entrypoint did chown them on first init, but
verify before recreating:

```bash
sudo docker run --rm \
    -v shekel-prod-pgdata:/d \
    --entrypoint sh \
    postgres:18-alpine \
    -c 'stat -c "%u:%g %n" /d /d/PG_VERSION'
# Expected: 70:70 /d
#           70:70 /d/PG_VERSION
```

If the output reports `0:0` (root-owned), chown the volume in a disposable container before
recreating:

```bash
sudo docker run --rm \
    -v shekel-prod-pgdata:/d \
    --entrypoint sh \
    postgres:18-alpine \
    -c 'chown -R 70:70 /d'
```

**2. Verify the `shekel-prod-app-state` volume is shekel-owned.**

The migrate service runs as the `shekel` user (Dockerfile USER directive) and writes the
seed-complete sentinel under `/home/shekel/app/state`. The volume was created under Commit C-34 and
inherits the in-image directory ownership, so this is mostly defensive. Verify once:

```bash
sudo docker run --rm \
    -v shekel-prod-app-state:/d \
    --entrypoint sh \
    ghcr.io/saltyreformed-labs/shekel:latest \
    -c 'stat -c "%u:%g %n" /d'
# Expected: <shekel uid>:<shekel gid> /d
```

If the volume came up root-owned, chown it the same way as the PGDATA volume above (substituting the
correct uid/gid for `shekel`).

**3. Bring up the stack and watch for healthy state.**

```bash
cd /opt/docker/shekel
sudo docker compose up -d --force-recreate
sudo docker compose ps
```

Each service should report `healthy` within a minute or two. If db restarts in a loop, run
`sudo docker logs shekel-prod-db` -- the first error line names the missing permission and points to
the volume that needs chown. If nginx restarts, run `sudo docker logs shekel-prod-nginx` -- the
master logs the specific path it cannot write (most often a missed tmpfs entry in the compose file).
