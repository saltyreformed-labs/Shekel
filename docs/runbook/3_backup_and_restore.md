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

1. Check, before it changes anything, that the database container holds its provisioning script and
   that the compose project the app container was made from still parses, read from the container's
   own labels
2. Validate the backup file: it decrypts, its gzip stream is whole, and it is not empty
3. Display a confirmation prompt (skip with `--skip-confirm`)
4. Take a safety dump of the current database (skip with `--skip-safety-dump`)
5. Stop the application container
6. Drop and recreate the database with schemas
7. Restore from the backup file
8. Hand every restored object to the owner login, `shekel_owner`, with the database container's
   provisioning script: a backup holds no owners or grants, so the restore makes every object the
   superuser's
9. Start the app the way docker compose made it (`docker compose up --pull never -d app`, in the
   project, directory and files its labels name), which runs the deploy in the migrate service
   first: the migrations, then the web login's grants
10. Run basic sanity checks

If a step fails once the database has been dropped, the script prints the state it left and what to
run next: the safety dump to restore while the restore is incomplete, or the hand-over or the start
once the data is back.

**Post-restore verification:**

```bash
curl -s http://localhost/health
cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never app \
    python scripts/integrity_check.py --verbose
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

This restores the backup to a temporary database (`shekel_verify_<pid>`), owned and restored by the
owner login, `shekel_owner`, runs sanity queries, runs the integrity check in the stack's migrate
service as the owner against the copy, then drops the temporary database. The production database is
never touched, and no password leaves any container.

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

The check only reads, so it runs as the web login, in a one-shot container of the app service (1's
Script Inventory says why not `docker exec`):

```bash
cd /opt/docker/shekel

# Run all checks.
docker compose run --rm --no-deps --pull never app python scripts/integrity_check.py

# Verbose output (shows every check, not just failures).
docker compose run --rm --no-deps --pull never app python scripts/integrity_check.py --verbose

# Run only one category: referential, orphan, balance, or consistency.
docker compose run --rm --no-deps --pull never app \
    python scripts/integrity_check.py --category referential
```

**Exit codes:** 0 = all passed, 1 = critical failures, 2 = warnings only, 3 = script error.
