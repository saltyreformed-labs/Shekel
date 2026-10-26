# Shekel Backup & Disaster Recovery Runbook

## 1. Overview

This runbook covers all backup, restore, retention, and verification procedures for the Shekel
budget application. The backup strategy uses `pg_dump` for PostgreSQL database dumps with gzip
compression, tiered retention, optional NAS replication, and optional GPG encryption.

### Script Inventory

| Script | Purpose | Schedule |
|--------|---------|----------|
| `scripts/backup.sh` | Create compressed database backup | Daily, 2:00 AM |
| `scripts/backup_retention.sh` | Prune old backups per retention policy | Daily, 2:30 AM |
| `scripts/restore.sh` | Restore database from a backup file | Manual |
| `scripts/verify_backup.sh` | Verify backup integrity via temp database | Weekly, Sunday 3:00 AM |
| `scripts/integrity_check.py` | Validate database referential and logical integrity | Weekly, Sunday 3:30 AM |

### Backup Strategy

- **Daily automated backups** at 2:00 AM via cron
- **Tiered retention**: daily (7 days), weekly/Sunday (4 weeks), monthly/1st (6 months)
- **Local + NAS storage**: backups copied to both `/var/backups/shekel/` and the NAS mount
- **Optional encryption**: AES-256 via GPG symmetric encryption
- **Weekly verification**: automated restore to a temp database with sanity and integrity checks

---

## 2. Prerequisites

- **Host**: Arch Linux with Docker and Docker Compose installed
- **Containers**: `shekel-prod-db` (PostgreSQL 16) running via `docker-compose.yml`
- **App container** (production only): `shekel-prod-app` running via `docker-compose.yml`
- **Disk space**: sufficient local storage for 7+ days of backups
- **NAS** (optional): network share mounted on the host

---

## 3. Automated Backup Setup

### 3.1 Cron Configuration

Add these entries to the host's crontab (`crontab -e`):

```cron
# ── Shekel Backups ───────────────────────────────────────────────

# Daily backup at 2:00 AM.
0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1

# Daily retention cleanup at 2:30 AM.
30 2 * * * /path/to/shekel/scripts/backup_retention.sh >> /var/log/shekel_backup.log 2>&1

# Weekly backup verification (Sunday 3:00 AM).
0 3 * * 0 /path/to/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1

# Weekly integrity check on production database (Sunday 3:30 AM).
30 3 * * 0 docker exec shekel-prod-app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1

# Daily audit log retention cleanup at 3:00 AM.
0 3 * * * docker exec shekel-prod-app python scripts/audit_cleanup.py >> /var/log/shekel_backup.log 2>&1
```

Replace `/path/to/shekel/` with the actual path to the Shekel project directory on the host.

### 3.2 NAS Mount Configuration

#### NFS Option

```bash
# Install NFS utilities.
sudo pacman -S nfs-utils

# Create mount point.
sudo mkdir -p /mnt/nas/backups/shekel

# Add to /etc/fstab (replace nas-ip and volume path with your NAS details).
# The soft,timeo=150,retrans=3 options prevent the host from hanging if the NAS is unreachable.
echo 'nas-ip:/volume1/backups/shekel  /mnt/nas/backups/shekel  nfs  defaults,soft,timeo=150,retrans=3  0  0' | sudo tee -a /etc/fstab

# Mount and verify.
sudo mount -a
touch /mnt/nas/backups/shekel/.mount_test && rm /mnt/nas/backups/shekel/.mount_test
echo "NFS mount verified."
```

#### CIFS/SMB Option

```bash
# Install CIFS utilities.
sudo pacman -S cifs-utils

# Create mount point.
sudo mkdir -p /mnt/nas/backups/shekel

# Create credentials file (not world-readable).
sudo tee /root/.nas-credentials <<EOF
username=your_nas_user
password=your_nas_password
EOF
sudo chmod 600 /root/.nas-credentials

# Add to /etc/fstab (replace nas-ip and share name with your NAS details).
echo '//nas-ip/backups/shekel  /mnt/nas/backups/shekel  cifs  credentials=/root/.nas-credentials,uid=root,gid=root,file_mode=0600,dir_mode=0700  0  0' | sudo tee -a /etc/fstab

# Mount and verify.
sudo mount -a
touch /mnt/nas/backups/shekel/.mount_test && rm /mnt/nas/backups/shekel/.mount_test
echo "CIFS mount verified."
```

### 3.3 Encryption Setup (Optional)

GPG symmetric encryption protects backup files at rest. When enabled, backup files are encrypted
with AES-256 before being written to disk.

```bash
# Generate a strong passphrase.
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Set the passphrase in the environment (add to your shell profile or .env).
export BACKUP_ENCRYPTION_PASSPHRASE="your-generated-passphrase"
```

**Critical**: Store the passphrase separately from the backups. If you store backups on a NAS, the
passphrase should be in a password manager or on the host -- not on the NAS. A lost passphrase means
unrecoverable backups.

---

## 4. Manual Backup

```bash
# Standard backup (local + NAS).
./scripts/backup.sh

# Local only (skip NAS copy).
./scripts/backup.sh --no-nas

# Custom local directory.
./scripts/backup.sh --local-dir /tmp/my_backup --no-nas

# Verify the backup file.
ls -lh /var/backups/shekel/shekel_backup_*.sql.gz
zcat /var/backups/shekel/shekel_backup_LATEST.sql.gz | head -20
```

---

## 5. Retention Policy

| Tier | Criteria | Kept For |
|------|----------|----------|
| Daily | All backups | 7 days |
| Weekly | Backups from Sundays | 4 weeks |
| Monthly | Backups from the 1st of the month | 6 months |

Classification is based on the date in the backup filename (`shekel_backup_YYYYMMDD_HHMMSS.sql.gz`),
not the file modification time. A file that qualifies for a higher tier (e.g., a Sunday that is also
the 1st) is always retained by the highest applicable tier.

```bash
# Preview what would be deleted.
./scripts/backup_retention.sh --dry-run

# Run retention cleanup.
./scripts/backup_retention.sh

# Adjust retention periods via environment variables.
RETENTION_DAILY_DAYS=14 RETENTION_WEEKLY_WEEKS=8 ./scripts/backup_retention.sh
```

---

## 6. Restore Procedure

### 6.1 Identify the Backup to Restore

```bash
# List local backups (newest first).
ls -lht /var/backups/shekel/

# List NAS backups.
ls -lht /mnt/nas/backups/shekel/

# Find a backup from a specific date.
ls /var/backups/shekel/shekel_backup_20260315_*.sql.gz*
```

### 6.2 Run the Restore

```bash
# Restore from a local backup.
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# The script will display a confirmation prompt:
#   WARNING: This will REPLACE ALL DATA in the Shekel database
#   Are you sure you want to continue? [y/N]
# Type "y" and press Enter to proceed.

# For encrypted backups, set the passphrase first.
export BACKUP_ENCRYPTION_PASSPHRASE="your-passphrase"
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz.gpg
```

The restore script will:

1. Stop the application container (if it exists)
2. Drop and recreate the database
3. Restore from the backup file
4. Start the application container (which runs Alembic migrations via the entrypoint)
5. Run basic verification checks

### 6.3 Verify the Restore

```bash
# Check the application.
curl -s http://localhost:5000/login | head -5

# Verify database contents.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT COUNT(*) FROM auth.users;"
docker exec shekel-prod-db psql -U shekel_user -d shekel -c "SELECT COUNT(*) FROM budget.pay_periods;"

# Run integrity checks.
docker exec shekel-prod-app python scripts/integrity_check.py --verbose
```

### 6.4 Restoring from NAS

If local backups are unavailable, copy from the NAS first:

```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

---

## 7. Backup Verification

The verification script restores a backup to a temporary database (`shekel_verify`), runs sanity
queries and integrity checks, then drops the temporary database. The production database is never
touched.

```bash
# Verify the most recent backup.
./scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1)
```

### What It Checks

**Sanity queries** (via `psql`):

- `auth.users` has at least one row
- `budget.pay_periods` row count and date range
- `budget.transactions` row count (informational)
- `budget.accounts` has at least one row
- `ref.account_types` is populated
- `system.audit_log` table exists
- `public.alembic_version` has a value

**Integrity checks** (via `integrity_check.py`):

- 15 referential integrity checks (FK violations)
- 5 orphan detection checks
- 3 balance anomaly checks
- 11 data consistency checks

### Expected Output

| Status | Meaning |
|--------|---------|
| **PASS** | All sanity and integrity checks passed |
| **PASS WITH WARNINGS** | Sanity checks passed; integrity checks found warnings but no critical failures |
| **FAIL** | One or more sanity or critical integrity checks failed |

**Recommended schedule**: weekly, Sunday at 3:00 AM (after the daily backup and retention run).

---

## 8. Integrity Checks

The integrity check script validates the database without modifying any data.

```bash
# Run all checks (inside the app container in production).
docker exec shekel-prod-app python scripts/integrity_check.py

# Run all checks with verbose output.
docker exec shekel-prod-app python scripts/integrity_check.py --verbose

# Run only referential integrity checks.
docker exec shekel-prod-app python scripts/integrity_check.py --category referential

# Run locally in development.
DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5432/shekel \
    python scripts/integrity_check.py --verbose
```

### Check Categories

| Category | Checks | Severity | What It Detects |
|----------|--------|----------|-----------------|
| `referential` | FK-01 to FK-16 (no FK-03) | Critical | Foreign key references to nonexistent rows |
| `orphan` | OR-01, OR-03 to OR-06 | Warning | Records disconnected from the data model |
| `balance` | BA-01, BA-05, BA-06 | Mixed | Anchor balance and pay period anomalies |
| `consistency` | DC-02 to DC-11 | Mixed | Cross-table logical inconsistencies |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | One or more critical checks failed |
| 2 | Warnings only (no critical failures) |
| 3 | Script error (connection failure, bad arguments) |

### What to Do When a Check Fails

- **Critical failures (FK-*, DC-01, DC-06, DC-07, DC-08)**: investigate immediately. These indicate
  data corruption or a broken restore. Check recent changes, restore from a known-good backup if
  needed.
- **Warnings (OR-*, BA-*, DC-02 to DC-05, DC-09)**: investigate when convenient. These indicate
  potential issues but the application will function correctly. Common causes: unused categories
  from initial seed, accounts not yet configured with type-specific parameters.

---

## 9. Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Database container is not running" | `shekel-prod-db` stopped | `docker start shekel-prod-db` |
| "NAS directory does not exist" | NAS not mounted | `sudo mount -a` and verify with `mount \| grep nas` |
| "Backup file is empty" | Disk full or pg_dump failure | Check `df -h` and `docker logs shekel-prod-db` |
| "Encrypted backup but no passphrase" | `BACKUP_ENCRYPTION_PASSPHRASE` not set | `export BACKUP_ENCRYPTION_PASSPHRASE="..."` |
| "Restore failed: permission denied" | `PGUSER` lacks CREATEDB privilege | Run: `docker exec shekel-prod-db psql -U postgres -c "ALTER USER shekel_user CREATEDB;"` |
| "Integrity CRITICAL failures after restore" | Corrupt or partial backup | Try restoring from an older backup |
| "verify_backup.sh: shekel_verify not cleaned up" | Previous run crashed before trap | Script auto-cleans on next run; or manually: `docker exec shekel-prod-db psql -U shekel_user -d postgres -c "DROP DATABASE IF EXISTS shekel_verify;"` |

### Log Files

| Log | Location | Contents |
|-----|----------|----------|
| Backup/retention/verify logs | `/var/log/shekel_backup.log` | Cron job output (if redirected) |
| Application structured logs | `docker logs shekel-prod-app` | JSON-formatted request and event logs |
| PostgreSQL logs | `docker logs shekel-prod-db` | Database server logs |

---

## 10. Environment Variables Reference

| Variable | Default | Used By | Description |
|----------|---------|---------|-------------|
| `BACKUP_LOCAL_DIR` | `/var/backups/shekel` | backup.sh, retention.sh | Local backup storage directory |
| `BACKUP_NAS_DIR` | `/mnt/nas/backups/shekel` | backup.sh, retention.sh | NAS backup storage directory |
| `BACKUP_ENCRYPTION_PASSPHRASE` | *(none)* | backup.sh, restore.sh, verify.sh | GPG encryption passphrase (optional) |
| `DB_CONTAINER` | `shekel-prod-db` | backup.sh, restore.sh, verify_backup.sh | PostgreSQL Docker container name. No other script reads this name: `deploy/shekel-deploy.sh` reads `SHEKEL_DB_CONTAINER` and `scripts/test.sh` reads `TEST_DB_CONTAINER`. Pointing it at a non-production container is still the documented purpose of the override -- what it can no longer do is reach the deploy pipeline or the test runner |
| `APP_CONTAINER` | `shekel-prod-app` | restore.sh, verify.sh | Application Docker container name |
| `PGUSER` | `shekel_user` | all scripts | PostgreSQL user |
| `PGDATABASE` | `shekel` | all scripts | PostgreSQL database name |
| `VERIFY_DB` | `shekel_verify` | verify.sh | Temporary database name for verification |
| `RETENTION_DAILY_DAYS` | `7` | retention.sh | Days to keep daily backups |
| `RETENTION_WEEKLY_WEEKS` | `4` | retention.sh | Weeks to keep Sunday backups |
| `RETENTION_MONTHLY_MONTHS` | `6` | retention.sh | Months to keep 1st-of-month backups |
