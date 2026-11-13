# Shekel Operations Runbook: Backup & Restore

Section 3 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

## 3. Backup & Restore

This section summarizes the key procedures. For complete details including NAS mount setup,
encryption configuration, retention policy mechanics, and verification internals, see
`docs/backup_runbook.md`.

### 3.1 Creating a Backup

```bash
# Standard backup (local + NAS).
./scripts/backup.sh

# Local only (skip NAS copy).
./scripts/backup.sh --no-nas

# Custom local directory.
./scripts/backup.sh --local-dir /tmp/my_backup --no-nas
```

Backups are written to `/var/backups/shekel/` as `shekel_backup_YYYYMMDD_HHMMSS.sql.gz`. If
`BACKUP_ENCRYPTION_PASSPHRASE` is set in the environment, the file is encrypted with GPG
(`.sql.gz.gpg`).

### 3.2 Restoring from a Backup

```bash
# List available backups (newest first).
ls -lht /var/backups/shekel/

# Restore from a specific backup.
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# For encrypted backups, set the passphrase first.
export BACKUP_ENCRYPTION_PASSPHRASE="your-passphrase"
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz.gpg
```

The restore script will:

1. Display a confirmation prompt (skip with `--skip-confirm`)
2. Stop the application container
3. Drop and recreate the database with schemas
4. Restore from the backup file
5. Restart the application container (entrypoint runs Alembic migrations)
6. Run basic sanity checks

**Post-restore verification:**

```bash
curl -s http://localhost/health
docker exec shekel-prod-app python scripts/integrity_check.py --verbose
```

### 3.3 Restoring from NAS

If local backups are unavailable:

```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

### 3.4 Verifying a Backup

```bash
# Verify the most recent backup.
./scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1)
```

This restores the backup to a temporary database (`shekel_verify`), runs sanity queries and
integrity checks, then drops the temporary database. The production database is never touched.

### 3.5 Retention Policy

| Tier | Criteria | Kept For |
|------|----------|----------|
| Daily | All backups | 7 days |
| Weekly | Sunday backups | 4 weeks |
| Monthly | 1st of month backups | 6 months |

```bash
# Preview what would be deleted.
./scripts/backup_retention.sh --dry-run

# Run retention cleanup.
./scripts/backup_retention.sh

# Override retention periods.
RETENTION_DAILY_DAYS=14 RETENTION_WEEKLY_WEEKS=8 ./scripts/backup_retention.sh
```

### 3.6 Integrity Checks

```bash
# Run all checks inside the app container.
docker exec shekel-prod-app python scripts/integrity_check.py

# Verbose output (shows every check, not just failures).
docker exec shekel-prod-app python scripts/integrity_check.py --verbose

# Run only one category: referential, orphan, balance, or consistency.
docker exec shekel-prod-app python scripts/integrity_check.py --category referential
```

**Exit codes:** 0 = all passed, 1 = critical failures, 2 = warnings only, 3 = script error.
