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
| `shekel-prod-nginx` | the `nginx:<minor>-alpine` tag `docker-compose.yml` pins (`1.29-alpine` since #434) -- **bundled mode only**. In shared mode (this host) there is no such container; the shared `nginx` reverse proxy fronts it | frontend, backend | `wget /health` every 30s |

### Script Inventory

| Script | Purpose | Usage |
|--------|---------|-------|
| `shekel-deploy` | **Production deploy** (digest-pinned GHCR image + cosign) | `shekel-deploy [sha256:... ] [--dry-run] [--no-verify]`, or `shekel-deploy --dump-only` |
| `deploy/verify_prod_deploy.sh` | Read-only check of production after a deploy | `deploy/verify_prod_deploy.sh [SINCE]` |
| `scripts/backup.sh` | Create database backup | `./scripts/backup.sh [--no-nas] [--local-dir DIR]` |
| `scripts/restore.sh` | Restore database from backup | `./scripts/restore.sh <backup_file>` |
| `scripts/verify_backup.sh` | Verify backup integrity | `./scripts/verify_backup.sh <backup_file>` |
| `scripts/backup_retention.sh` | Prune old backups | `./scripts/backup_retention.sh [--dry-run]` |
| `scripts/integrity_check.py` | Validate database integrity | `docker exec shekel-prod-app python scripts/integrity_check.py [--verbose] [--category CAT]` |
| `scripts/audit_cleanup.py` | Clean old audit log entries | `docker exec shekel-prod-app python scripts/audit_cleanup.py [--days N] [--dry-run]` |
| `scripts/reset_mfa.py` | Emergency MFA reset for a user: also removes their passkeys, ends their sessions and forgets their trusted devices | `docker exec shekel-prod-app python scripts/reset_mfa.py <email>` |
| `scripts/seed_ref_tables.py` | Seed reference lookup tables | `docker exec shekel-prod-app python scripts/seed_ref_tables.py` |
| `scripts/seed_user.py` | Create initial seed user | `docker exec shekel-prod-app python scripts/seed_user.py` |

### Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| 2:00 AM daily | `backup.sh` | Database backup (local + NAS) |
| 2:30 AM daily | `backup_retention.sh` | Prune old backups per retention policy |
| 3:00 AM daily | `audit_cleanup.py` | Delete audit log rows older than 365 days |
| 3:00 AM Sunday | `verify_backup.sh` | Restore latest backup to temp DB, run checks |
| 3:30 AM Sunday | `integrity_check.py` | Validate production DB integrity |

Crontab entries (replace `/opt/shekel` with your actual path):

```cron
# ── Shekel Backups & Maintenance ─────────────────────────────────
0 2 * * * /opt/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
30 2 * * * /opt/shekel/scripts/backup_retention.sh >> /var/log/shekel_backup.log 2>&1
0 3 * * * docker exec shekel-prod-app python scripts/audit_cleanup.py >> /var/log/shekel_backup.log 2>&1
0 3 * * 0 /opt/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1
30 3 * * 0 docker exec shekel-prod-app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1
```
