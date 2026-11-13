# Shekel Operations Runbook

This is the single operational reference for the Shekel budget application. It covers deployment,
backup and restore, security operations, monitoring, Cloudflare management, and troubleshooting. For
detailed backup procedures, see also `docs/backup_runbook.md`. For secret management details, see
also `docs/runbook_secrets.md`.

---

## Table of Contents

1. [Quick Reference](#1-quick-reference)
2. [Deployment](runbook/2_deployment.md#2-deployment)
3. [Backup & Restore](runbook/3_backup_and_restore.md#3-backup--restore)
4. [Security Operations](runbook/4_security_operations.md#4-security-operations)
5. [Monitoring & Observability](runbook/5_monitoring.md#5-monitoring--observability)
6. [Cloudflare Management](runbook/6_cloudflare.md#6-cloudflare-management)
7. [Troubleshooting](runbook/7_troubleshooting.md#7-troubleshooting)

---

## 1. Quick Reference

### Service Architecture

```text
[Client Browser]
       |
       v
[Cloudflare Edge]          -- TLS termination, Access policy, WAF rate limiting
       |
       v
[cloudflared]              -- Proxmox host, systemd service, encrypted tunnel
       |
       v
[Nginx :80]                -- Docker container, reverse proxy, static files
       |
       v
[Gunicorn :8000]           -- Docker container, WSGI server, 2 workers
       |
       v
[Flask Application]        -- Shekel budget app, structured JSON logging
       |
       +------> [Valkey]          -- Docker container, Flask-Limiter counters
       |                              (no network: reached over a socket file
       |                              in a volume the app shares; no
       |                              persistence, counters evaporate on
       |                              restart by design)
       v
[PostgreSQL :5432]         -- Docker container, multi-schema, audit triggers
```

### Key Paths on the Proxmox Host

| Path | Purpose |
|------|---------|
| `/opt/shekel/` | Application directory (git repository clone) |
| `/opt/shekel/.env` | Environment configuration (secrets, settings) |
| `/opt/shekel/docker-compose.yml` | Base compose file (bundled mode) |
| `/opt/shekel/deploy/docker-compose.prod.yml` | Shared-mode override (committed copy) |
| `/opt/shekel/deploy/nginx-bundled/nginx.conf` | Bundled-Nginx config (committed copy) |
| `/opt/shekel/deploy/nginx-shared/nginx.conf` | Shared-Nginx main config (committed copy) |
| `/opt/shekel/deploy/nginx-shared/conf.d/shekel.conf` | Shared-Nginx vhost (committed copy) |
| `/opt/docker/shekel/docker-compose.override.yml` | Runtime shared-mode override; mirrors `deploy/docker-compose.prod.yml` |
| `/opt/docker/nginx/nginx.conf` | Runtime shared-Nginx main config; mirrors `deploy/nginx-shared/nginx.conf` |
| `/opt/docker/nginx/conf.d/shekel.conf` | Runtime shared-Nginx vhost; mirrors `deploy/nginx-shared/conf.d/shekel.conf` |
| `/etc/cloudflared/config.yml` | Cloudflare Tunnel configuration |
| `/root/.cloudflared/` | Tunnel credentials (cert.pem, tunnel JSON) |
| `/var/backups/shekel/` | Local backup storage |
| `/mnt/nas/backups/shekel/` | NAS backup storage (off-site copy) |
| `/var/log/shekel_backup.log` | Backup and maintenance cron log |

### Container Inventory

Measured against the running maintainer host 2026-08-08. The nginx row differs by mode: see 2.5.

| Container | Image | Network(s) | Health Check |
|-----------|-------|------------|--------------|
| `shekel-prod-db` | `postgres:18-alpine` (digest-pinned; server 18.4) | backend | `pg_isready` every 10s |
| `shekel-prod-valkey` | `valkey/valkey:<version>-alpine` (digest-pinned in `docker-compose.yml`; Valkey replaced Redis under ruling R-BAL298) | none: the app reaches it over a socket file in the `valkey-socket` volume (ruling plan#2612) | `valkey-cli ping` over the socket as the `healthcheck` ACL user every 10s |
| `shekel-prod-app` | `ghcr.io/saltyreformed-labs/shekel@sha256:...`, the digest `shekel-deploy` pins in `.env` | backend, monitoring | `GET /health` every 30s |
| `shekel-prod-migrate` | the app's image and digest. From the compose files since card plan#2166, not measured on the host | backend | none: a one-shot that runs the deploy as the database owner and exits, so `docker compose ps -a` shows it `Exited (0)` between deploys |
| `shekel-prod-nginx` | the `nginx:<minor>-alpine` tag `docker-compose.yml` pins (`1.29-alpine` since #434) -- **bundled mode only**. In shared mode (this host) there is no such container; the shared `nginx` reverse proxy fronts it | frontend, backend | `wget /health` every 30s |

### Script Inventory

Each Python script runs from `/opt/docker/shekel` in a one-shot container of the service that holds
the login it needs, and only that one: `docker compose run --rm --no-deps --pull never <service>`.
The migrate service logs in as the database owner, `shekel_owner`; the app service as the web login,
`shekel_app`, which reads and writes data and cannot delete an audit row or change the schema.
`--no-deps` runs no deploy first, and `--pull never` keeps the run off the registry (the app
service's `pull_policy: always` would otherwise pull). `docker exec shekel-prod-app` reaches neither
login: an exec'd process gets the container's stored environment, which holds the placeholders, not
the secrets the entrypoint loaded.

| Script | Purpose | Usage |
|--------|---------|-------|
| `shekel-deploy` | **Production deploy** (digest-pinned GHCR image + cosign) | `shekel-deploy [sha256:... ] [--dry-run] [--no-verify]`, or `shekel-deploy --dump-only` |
| `deploy/verify_prod_deploy.sh` | Read-only check of production after a deploy | `deploy/verify_prod_deploy.sh [SINCE]` |
| `scripts/backup.sh` | Create database backup | `./scripts/backup.sh [--no-nas] [--local-dir DIR]` |
| `scripts/restore.sh` | Restore database from backup | `./scripts/restore.sh <backup_file>` |
| `scripts/verify_backup.sh` | Verify backup integrity | `./scripts/verify_backup.sh <backup_file>` |
| `scripts/backup_retention.sh` | Prune old backups | `./scripts/backup_retention.sh [--dry-run]` |
| `scripts/integrity_check.py` | Validate database integrity (reads only) | `docker compose run --rm --no-deps --pull never app python scripts/integrity_check.py [--verbose] [--category CAT]` |
| `scripts/audit_cleanup.py` | Clean old audit log entries (the owner's: the web login cannot delete them) | `docker compose run --rm --no-deps --pull never migrate python scripts/audit_cleanup.py [--days N] [--dry-run]` |
| `scripts/reset_mfa.py` | Emergency MFA reset for a user | `docker compose run --rm --no-deps --pull never app python scripts/reset_mfa.py [--force] <email>` |
| `scripts/rotate_sessions.py` | End every session (after a `SECRET_KEY` rotation) | `docker compose run --rm --no-deps --pull never app python scripts/rotate_sessions.py --confirm` |
| `scripts/rotate_totp_key.py` | Re-encrypt TOTP secrets under the new key | `docker compose run --rm --no-deps --pull never app python scripts/rotate_totp_key.py --confirm` |
| `scripts/seed_ref_tables.py` | Seed reference lookup tables (every deploy runs it) | `docker compose run --rm --no-deps --pull never migrate python scripts/seed_ref_tables.py` |
| `scripts/seed_user.py` | Create initial seed user (the deploy's last step runs it) | `docker compose run --rm --no-deps --pull never migrate python scripts/seed_user.py`, with `SEED_USER_*` in `.env` |

Measured 2026-11-12 on a throwaway stack built from card plan#2166's branch, with production's
secret files mounted: each command above exited 0 in the service it names; the audit cleanup deleted
a row older than its retention as the owner, a delete the web login was refused; and the audit
cleanup run in the app service, and the TOTP rotation run in the migrate service, each exited 1,
naming the setting that service lacks (`DATABASE_URL`, `TOTP_ENCRYPTION_KEY`).

### Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| 2:00 AM daily | `backup.sh` | Database backup (local + NAS) |
| 2:30 AM daily | `backup_retention.sh` | Prune old backups per retention policy |
| 3:30 AM daily | `audit_cleanup.py` | Delete audit log rows older than `AUDIT_RETENTION_DAYS` (365), by the systemd timer below |
| 3:00 AM Sunday | `verify_backup.sh` | Restore latest backup to temp DB, run checks |
| 3:30 AM Sunday | `integrity_check.py` | Validate production DB integrity |

Crontab entries (replace `/opt/shekel` with your actual path):

```cron
# ── Shekel Backups & Maintenance ─────────────────────────────────
0 2 * * * /opt/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
30 2 * * * /opt/shekel/scripts/backup_retention.sh >> /var/log/shekel_backup.log 2>&1
0 3 * * 0 /opt/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1
30 3 * * 0 cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1
```

The audit cleanup is not a crontab line but the systemd unit `shekel-audit-cleanup.service` and its
`shekel-audit-cleanup.timer`, daily at 03:30, after the backup window, so every row it deletes is in
that night's backup. The installed copies are under `/etc/systemd/system/`, and reference copies
beside the compose files in `/opt/docker/shekel/`. The service runs, from `/opt/docker/shekel`:

```bash
/usr/bin/docker compose run --rm --no-deps --pull never migrate python scripts/audit_cleanup.py
```

Its log is `journalctl -u shekel-audit-cleanup.service`.
