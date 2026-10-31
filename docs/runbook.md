# Shekel Operations Runbook

This is the single operational reference for the Shekel budget application. It covers deployment,
backup and restore, security operations, monitoring, Cloudflare management, and troubleshooting. For
detailed backup procedures, see also `docs/backup_runbook.md`. For secret management details, see
also `docs/runbook_secrets.md`.

---

## Table of Contents

1. [Quick Reference](#1-quick-reference)
2. [Deployment](#2-deployment)
3. [Backup & Restore](#3-backup--restore)
4. [Security Operations](#4-security-operations)
5. [Monitoring & Observability](#5-monitoring--observability)
6. [Cloudflare Management](#6-cloudflare-management)
7. [Troubleshooting](#7-troubleshooting)

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
       +------> [Valkey :6379]    -- Docker container, Flask-Limiter counters
       |                              (no persistence; counters evaporate on
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
| `shekel-prod-valkey` | `valkey/valkey:<version>-alpine` (digest-pinned in `docker-compose.yml`; Valkey replaced Redis under ruling R-BAL298) | backend | `valkey-cli ping` as the `healthcheck` ACL user every 10s |
| `shekel-prod-app` | `ghcr.io/saltyreformed-labs/shekel@sha256:...`, the digest `shekel-deploy` pins in `.env` | backend, monitoring | `GET /health` every 30s |
| `shekel-prod-nginx` | the `nginx:<minor>-alpine` tag `docker-compose.yml` pins (`1.29-alpine` since #434) -- **bundled mode only**. In shared mode (this host) there is no such container; the shared `nginx` reverse proxy fronts it | frontend, backend | `wget /health` every 30s |

### Script Inventory

| Script | Purpose | Usage |
|--------|---------|-------|
| `shekel-deploy` | **Production deploy** (digest-pinned GHCR image + cosign) | `shekel-deploy [sha256:... ] [--dry-run] [--no-verify]`, or `shekel-deploy --dump-only` |
| `deploy/verify_prod_deploy.sh` | Read-only check of production after a deploy | `deploy/verify_prod_deploy.sh [SINCE]` |
| `scripts/deploy.sh` | Self-hosted **source-build** deploy (NOT the production path) | `./scripts/deploy.sh [--skip-pull] [--skip-backup]` |
| `scripts/backup.sh` | Create database backup | `./scripts/backup.sh [--no-nas] [--local-dir DIR]` |
| `scripts/restore.sh` | Restore database from backup | `./scripts/restore.sh <backup_file>` |
| `scripts/verify_backup.sh` | Verify backup integrity | `./scripts/verify_backup.sh <backup_file>` |
| `scripts/backup_retention.sh` | Prune old backups | `./scripts/backup_retention.sh [--dry-run]` |
| `scripts/integrity_check.py` | Validate database integrity | `docker exec shekel-prod-app python scripts/integrity_check.py [--verbose] [--category CAT]` |
| `scripts/audit_cleanup.py` | Clean old audit log entries | `docker exec shekel-prod-app python scripts/audit_cleanup.py [--days N] [--dry-run]` |
| `scripts/reset_mfa.py` | Emergency MFA reset for a user | `docker exec shekel-prod-app python scripts/reset_mfa.py <email>` |
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

---

## 2. Deployment

### 2.1 Deploying a New Version

**There are two deploy paths, and only one of them is production.** Pick by how the image is
produced, not by which script you remember.

| | Production (maintainer) | Self-hosted source build |
|---|---|---|
| Script | `shekel-deploy` | `scripts/deploy.sh` |
| Canonical source | `deploy/shekel-deploy.sh` in the repo, symlinked to `/opt/docker/scripts/` | `scripts/deploy.sh` |
| Stack | `/opt/docker/shekel` (hardened, digest-pinned) | `/opt/shekel` (`docker-compose.yml` + `docker-compose.build.yml`) |
| Image | `ghcr.io/saltyreformed-labs/shekel@sha256:...`, cosign-verified | built locally from the working tree |
| Rollback | digest revert, decided by the re-read schema stamp (2.3) | `:previous` image tag |

`scripts/deploy.sh` **refuses to run when `/opt/docker/shekel` exists** (`--force-self-hosted`
overrides): running it on the maintainer's host would replace the pinned, hardened container with an
unpinned locally-built one. If you are the maintainer, you want `shekel-deploy`; 2.1.2 below is for
self-hosted installs that build from source.

#### 2.1.1 Production deploy (`shekel-deploy`)

```bash
shekel-deploy                  # pull :latest, deploy if the digest is new
shekel-deploy sha256:abc...    # deploy one specific digest
shekel-deploy --dry-run        # resolve, verify, report the pre-flight, change nothing
shekel-deploy --dump-only      # step 4's dump alone, its path printed last; nothing else
```

`--dump-only` takes no other argument (`-h` aside). Beyond the daemon check, the database's
running-check, the dump and its readback it makes no call: no pull, no pre-flight, no pin written,
and no container started, stopped or recreated. It names its dump
`shekel_prod_dump_<current pin>_<ts>.dump`, never `predeploy`, so a release rehearsal's same-day
dump is never mistaken for a deploy's restore point.

**What it does (in order):**

1. Resolves the target digest (pulls `:latest`, or takes an explicit `sha256:...`). No-ops if it
   already matches the current pin.
2. `cosign verify` against the keyless OIDC identity of `.github/workflows/docker-publish.yml`.
   Aborts if `cosign` is missing; `--no-verify` is for emergencies only.
3. **Migration pre-flight.** Lists `migrations/versions` in *both* images, reads the database's
   Alembic stamp, and refuses up front a target that cannot resolve it (an image older than the
   database). It reports the revisions the target adds: a **migration-bearing** release. `--dry-run`
   reports all of this without deploying.
4. **`pg_dump -Fc` of the whole database** into `~/shekel-backups/` (override with
   `SHEKEL_BACKUP_DIR`), written to a `.part` file and renamed only after `pg_restore -f /dev/null`
   decodes every data block of it (a table-of-contents read passes a truncated archive).
   **No dump, no deploy** -- a dump failure aborts *before* the pin is touched.
5. Rewrites `SHEKEL_IMAGE_DIGEST` in `/opt/docker/shekel/.env` and `docker compose up -d app`.
   Entrypoint step 3 runs the migrations, the reference seed, the three ledger hooks and the
   audit-trigger check in ONE transaction: they commit together or not at all.
6. Polls the container healthcheck for up to 4 minutes; ntfy ping either way.
7. **On failure**, stops the new container, re-reads the stamp, and either re-pins the previous
   digest or refuses (2.3).

After a deploy, `deploy/verify_prod_deploy.sh <the deploy's start time>`, run from this checkout,
checks production read-only: health, the login gate's redirects, and the log since the deploy. Its
header names its checks, and the one false failure it knows (BAL-628, after an in-place restart).

**The canonical copy is `deploy/shekel-deploy.sh` in this repo**, and
`/opt/docker/scripts/shekel-deploy.sh` is a symlink to it. That means
**the checked-out working tree is what rolls production** -- check out the revision you intend to
deploy from before running it. Install or repair the link with:

```bash
sudo ln -sfn /home/josh/projects/Shekel/deploy/shekel-deploy.sh \
             /opt/docker/scripts/shekel-deploy.sh
```

#### 2.1.2 Self-hosted source-build deploy (`scripts/deploy.sh`)

The deploy script automates the full deployment workflow with automatic rollback on failure.

```bash
cd /opt/shekel
./scripts/deploy.sh
```

**What it does (in order):**

1. Pulls the latest code from GitHub (`git pull --ff-only`)
2. Runs a pre-deploy database backup (`scripts/backup.sh`)
3. Tags the current Docker image as `:previous` for rollback
4. Builds the new Docker image (`docker compose build app`)
5. Restarts the app container (`docker compose up -d --no-deps --force-recreate app`)
6. Waits for the health endpoint to report healthy (default: 60s timeout)
7. On health check failure: rolls back to the `:previous` image automatically

**Options:**

| Flag | Effect |
|------|--------|
| `--skip-pull` | Skip `git pull` (deploy from current working tree) |
| `--skip-backup` | Skip pre-deploy database backup |
| `--health-timeout N` | Seconds to wait for health check (default: 60) |
| `--health-interval N` | Seconds between health check retries (default: 5) |

**Example: deploy from a specific branch without backup:**

```bash
cd /opt/shekel
git checkout feature-branch
./scripts/deploy.sh --skip-pull --skip-backup
```

### 2.2 Manual Deployment

If the deploy script is unavailable or you need more control:

```bash
cd /opt/shekel

# 1. Pull latest code.
git pull --ff-only

# 2. Back up the database.
./scripts/backup.sh

# 3. Build the new image.
docker compose build app

# 4. Restart the app container (migrations run automatically via entrypoint.sh).
docker compose up -d --no-deps --force-recreate app

# 5. Verify health.
curl -s http://localhost/health
# Expected: {"status":"healthy","timestamp":"..."}
```

### 2.3 Rolling Back

#### Production (`shekel-deploy`): the re-read stamp decides

When the new image fails (compose rejects it, its container reports unhealthy, or it is not healthy
within 4 minutes), `shekel-deploy` stops the new container, so its entrypoint can commit nothing
more, then re-reads the database's Alembic stamp. That stamp, not the release, decides what happens:

- **The previous image can resolve the stamp.** The release migrated nothing, or entrypoint step 3
  failed and rolled back: since plan step `X-cv` the migrations, the reference seed, the ledger
  hooks and the audit-trigger check are ONE transaction, so a failure there leaves the stamp unmoved
  even for a migration-bearing release. The script saves the failed container's log beside the dump
  (its name with `.failed-container.log` for `.dump`) and re-pins the previous digest. After a
  health failure it then waits for that image to be healthy; after a compose failure it does not
  wait. Nothing is restored. When the previous image does not come up either, see below.
- **The previous image cannot resolve the stamp.** Step 3 COMMITTED and something later failed (the
  first-boot user seed, the static copy, or the app never became healthy). Re-pinning would give a
  second dead container -- reproduced as `CommandError: Can't locate revision identified by ...`
  (finding F-8) -- so the script **refuses**, leaves the pin at the new digest, saves the log, and
  names the ways out:
  1. **Start the new pin again**, offered when the new image resolves the stamp (it wrote it):
     `cd /opt/docker/shekel && docker compose up -d app`. Nothing is restored and nothing is lost.
     The right first move when the log shows a transient failure, such as a release that was only
     slower than the health window.
  2. **Restore the pre-deploy dump** and re-pin the previous digest (below). This discards
     everything written since the dump.
- **The stamp could not be re-read** (the database container did not answer). The script claims
  nothing about a commit and leaves the pin. Once the database answers, roll back through
  `shekel-deploy <old-digest>`: its pre-flight reads the stamp and refuses up front, naming the
  restore, if the previous image cannot resolve it.
- **The new container could not be stopped.** No stamp read is final, so the script refuses and
  leaves the pin: `docker stop shekel-prod-app`, then `shekel-deploy <old-digest>`.

**Read the saved log first** (the path is in the output and the ntfy alert). It names the step that
failed, and whether a restart can clear it.

**When the re-pinned previous image does not come up either.** After a health failure the script
then ends `rollback container also unhealthy; manual intervention required` (it prints the same line
when the re-pin's own `compose up` failed and usually nothing started, finding BAL-540;
`docker compose ps` tells the two apart). The previous image knows the stamp's revision, so the
migration level is not the cause; something both images meet is: schema drift the stamp does not
record, the data, or the environment. Whether the dump can help turns on step 3. For a release since
plan step `X-cv`: if the saved log has no `Database initialised: ONE transaction, committed.`, the
failed release committed nothing past entrypoint steps 2 and 2b (idempotent schema and role
provisioning, which every image re-runs on each boot), so the fault predates the deploy and the dump
holds it too: restoring cannot help, and the fault is repaired in place. If step 3 did commit, what
it wrote may be the fault, and restoring the dump (below) is a candidate. An image older than `X-cv`
never prints that line and commits its seeds after step 3 (entrypoint steps 4 and 6), so for one the
dump is a candidate either way. A previous container that crash-loops re-runs its entrypoint on
every pass (an image older than plan step `X-cv` also commits its hooks and seeds each time). Docker
waits between passes, from a tenth of a second up to about a minute, so `docker compose ps` may show
it `Up` for seconds at a time with `(health: starting)` during a pass or `Restarting` during a wait.
The tell is `docker inspect shekel-prod-app --format '{{.State.Status}} {{.RestartCount}}'`: a state
of `restarting`, or a restart count that grows between two readings two minutes apart. Stop it
before repairing and start it after:

```bash
cd /opt/docker/shekel
docker compose stop app      # it retries on its own under `restart: unless-stopped`
# ... repair what the saved log names ...
docker compose up -d app
```

A missing audit trigger is the case the X-cv rehearsal measured: every image since C-13 counts them
at start, so the release refuses and the re-pinned image refuses the same database. Since ruling
`R-BAL129` the release's refusal names each audited table missing its trigger and, for a trigger
dropped by hand, prints the statements that re-create them, each ending in `;`. Each has this form
and runs as the database owner (the app role has DML only), between the stop and the start above:

```sql
CREATE TRIGGER audit_<table> AFTER INSERT OR UPDATE OR DELETE ON <schema>.<table>
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func();
```

```bash
docker exec shekel-prod-db psql -U shekel_user -d shekel -v ON_ERROR_STOP=1 \
    -c '<the statements from the saved log>'
```

If the refusal names a table newly added to `AUDITED_TABLES`, its migration did not attach the
trigger, and the migration is the fix. An image older than `R-BAL129` prints only a count, and its
`flask db upgrade` advice does not re-create a dropped trigger. (Measured 2026-09-23 in the X-cv
rehearsal's second pass, on a copy of production's data with one audit trigger dropped and the 6-3
release image `876fbf5c5bf7` as the previous image: the release's saved log named
`budget.transactions` and printed one statement; `docker compose ps` showed the crash-looping
previous container as `Up 4 seconds (health: starting)` 51 seconds after the re-pin created it,
after 5 restarts: its first pass ran 11 seconds and the next four about 8 seconds each, and the
waits between them grew from 0.1 to 1.6 seconds, where an earlier rehearsal of the same image and
fault ran 11 to 12 second passes with waits of at most a quarter of a second, so that container was
up nearly all the time; the stop, that statement run through `psql` as printed, and the start above
brought it up healthy in about 15 seconds, with the re-created trigger identical to the dropped one
and no table changed.)

**Restoring discards everything written since the dump was taken** -- minutes of real entries in a
budgeting app. Capture the failed state first so that window stays recoverable.

```bash
cd /opt/docker/shekel

# 1. STOP THE APP FIRST.  It runs `restart: unless-stopped` and re-runs
#    `alembic upgrade head` on every start, so restoring underneath a
#    crash-looping container means restoring while it migrates.
docker compose stop app

# 2. Keep the failed state before overwriting it.
docker exec shekel-prod-db pg_dump -U shekel_user -d shekel -Fc \
    >'/home/josh/shekel-backups/failed-state.dump'

# 3. Restore the dump shekel-deploy named in its output.
#    --single-transaction --exit-on-error are NOT optional: without them a
#    stream that ends early leaves the database EMPTY after --clean has
#    already dropped everything, with no transaction to roll back (OPS/SH-01).
docker exec -i shekel-prod-db pg_restore -U shekel_user \
    -d shekel --clean --if-exists --single-transaction --exit-on-error \
    <'/home/josh/shekel-backups/shekel_prod_predeploy_<digest>_<ts>.dump'

# 4. --clean drops only what the archive knows about, so a table the failed
#    migration created survives and will collide when it is retried.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c '\dt budget.*'

# 5. Only now put the pin back.
sed -i 's|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=<old-digest>|' /opt/docker/shekel/.env
docker compose up -d app
```

**Automatic rollback:** For a self-hosted source build, `scripts/deploy.sh` automatically rolls back
if the health check fails after restart. No manual action is needed. For production, see the stamp
rule above.

**Manual rollback, SELF-HOSTED source-build stacks only** (`/opt/shekel`, `scripts/deploy.sh`). It
does not apply to the maintainer's production stack, which has no `:previous` tag and no
`/opt/shekel` -- for that, use the digest sequence above:

```bash
cd /opt/shekel

# Option A: Roll back to a specific git commit.
git log --oneline -5          # Find the commit to roll back to
git checkout <commit-hash>
docker compose build app
docker compose up -d --no-deps --force-recreate app

# Option B: If the previous image is still tagged.
# deploy.sh tags the in-place image as ${APP_CONTAINER}:previous
# (so shekel-prod-app:previous) before each deploy, then on rollback
# re-tags it as <compose_image>:latest.  The compose image for the
# bundled deployment is ghcr.io/saltyreformed-labs/shekel:latest -- adjust
# below if you build a custom image with a different name.
docker tag shekel-prod-app:previous ghcr.io/saltyreformed-labs/shekel:latest
docker compose up -d --no-deps --force-recreate app
```

After rollback, verify health:

```bash
curl -s http://localhost/health
```

### 2.4 First-Time Setup

**Prerequisites:** Docker, Docker Compose v2, and git installed on the Proxmox host.

```bash
# 1. Clone the repository.
git clone https://github.com/<your-repo>/shekel.git /opt/shekel
cd /opt/shekel

# 2. Create the environment file.
cp .env.example .env
nano .env
# Fill in REQUIRED values: SECRET_KEY, POSTGRES_PASSWORD, TOTP_ENCRYPTION_KEY
# See .env.example for generation commands.

# 3. Create the monitoring network (required by docker-compose.yml).
docker network create monitoring

# 4. Start the stack.
docker compose up -d

# 5. Wait for all services to be healthy.
docker compose ps
# All three services (db, app, nginx) should show "healthy".

# 6. Seed the first user (first run only).  The reference seed already ran
#    inside entrypoint step 3, as it does on every start.  (No tax data is
#    seeded: the tax law ships in the code, app/tax_law/.)
docker exec shekel-prod-app python scripts/seed_user.py

# 7. Verify the application.
curl -s http://localhost/health
# Open http://localhost in a browser and log in.
# Default credentials: admin@shekel.local / ChangeMe!2026

# 8. Set up cron jobs (see §1 Cron Schedule above).
crontab -e
```

### 2.5 Shared-mode Deployment and Config Sync

The maintainer's homelab runs Shekel in **shared mode**: the bundled `shekel-prod-nginx` service is
parked in the `disabled` profile and a separately-managed Nginx at `/opt/docker/nginx/` proxies
traffic from the dedicated `shekel-frontend` Docker bridge to `shekel-prod-app:8000`. The Shekel app
is NOT on the wider `homelab` network: that network hosts unrelated co-tenants (Jellyfin, Immich,
UniFi) and would expose Gunicorn directly to any of their compromise paths (audit findings
F-020/F-129 closed in Commit C-33). The version-controlled copies of the runtime configs live under
`deploy/`:

| Repo path | Runtime path on the host |
|-----------|--------------------------|
| `deploy/docker-compose.prod.yml` | `/opt/docker/shekel/docker-compose.override.yml` |
| `deploy/nginx-shared/nginx.conf` | `/opt/docker/nginx/nginx.conf` |
| `deploy/nginx-shared/conf.d/shekel.conf` | `/opt/docker/nginx/conf.d/shekel.conf` |

The repo is the source of truth. The on-host copies must match byte-for-byte; drift between them is
a deployment-integrity bug (security-2026-04-15 finding F-021).

#### Bringing up shared mode (first-time)

```bash
# On the host:
cd /opt/shekel
git checkout dev && git pull --ff-only

# Ensure the dedicated shekel-frontend network exists.  The subnet
# is pinned so Gunicorn's FORWARDED_ALLOW_IPS literal and the shared
# Nginx's set_real_ip_from directive both reference the same CIDR.
# Audit finding F-015 + F-020 (Commit C-33).
docker network ls --filter name=shekel-frontend
# If missing:
docker network create shekel-frontend \
    --driver bridge \
    --subnet 172.32.0.0/24

# Edit /opt/docker/docker-compose.yml so the shared nginx and
# cloudflared services attach to shekel-frontend in addition to the
# homelab bridge they already join.  Apply with:
#   cd /opt/docker && docker compose up -d nginx cloudflared

# Edit /opt/docker/cloudflared/config.yml so the shekel ingress rule
# points to ``http://nginx:80`` instead of straight at
# ``http://shekel-prod-app:8000``.  Restart cloudflared after the
# edit:
#   cd /opt/docker && docker compose up -d cloudflared

# The runtime override location is /opt/docker/shekel/.  Either:
#  (a) keep that directory as a thin wrapper that copies the files
#      from the repo on each pull, or
#  (b) invoke compose against the repo files directly.
docker compose \
  -f /opt/shekel/docker-compose.yml \
  -f /opt/shekel/deploy/docker-compose.prod.yml \
  up -d
```

#### Editing the shared-Nginx configuration

```bash
# 1. Edit and commit on the dev branch.
#    (work happens in the repo, NOT on the runtime files)
$EDITOR deploy/nginx-shared/nginx.conf
$EDITOR deploy/nginx-shared/conf.d/shekel.conf
git add deploy/nginx-shared/
git commit -m "deploy(nginx): <what changed>"
git push

# 2. On the host, pull and copy into place.
cd /opt/shekel
git pull --ff-only
sudo cp deploy/nginx-shared/nginx.conf            /opt/docker/nginx/nginx.conf
sudo cp deploy/nginx-shared/conf.d/shekel.conf    /opt/docker/nginx/conf.d/shekel.conf

# 3. Validate before reload.
sudo docker exec nginx nginx -t

# 4. Reload without dropping connections.
sudo docker exec nginx nginx -s reload

# 5. Confirm the change is live.
curl -sSI https://shekel.saltyreformed.com | head
```

If `nginx -t` reports an error in step 3, do not reload. Restore the previous file with
`git restore` on the host or `git checkout HEAD~1 -- deploy/nginx-shared/...`, copy it back into
place, and rerun `nginx -t`.

#### Editing the shared-mode compose override

```bash
# 1. Edit and commit on the dev branch.
$EDITOR deploy/docker-compose.prod.yml
git add deploy/docker-compose.prod.yml
git commit -m "deploy(compose): <what changed>"
git push

# 2. On the host, pull and copy into place.
cd /opt/shekel
git pull --ff-only
sudo cp deploy/docker-compose.prod.yml /opt/docker/shekel/docker-compose.override.yml

# 3. Validate the merged compose.
cd /opt/docker/shekel
docker compose config >/dev/null

# 4. Recreate affected services.
docker compose up -d
```

#### Verifying the host matches the repo

A drift-check helper is reserved at `scripts/config_audit.py` (implemented in remediation commit
C-49). Until that lands, verify manually:

```bash
diff -u /opt/shekel/deploy/nginx-shared/nginx.conf            /opt/docker/nginx/nginx.conf
diff -u /opt/shekel/deploy/nginx-shared/conf.d/shekel.conf    /opt/docker/nginx/conf.d/shekel.conf
diff -u /opt/shekel/deploy/docker-compose.prod.yml            /opt/docker/shekel/docker-compose.override.yml
```

Any non-empty diff is an incident: investigate before re-syncing, because the host change may carry
an undocumented production fix that must be brought into the repo first.

#### One-shot reconciliation script

`scripts/reconcile_prod_to_canonical.sh` bundles the seven host-side steps that bring a drifted
`/opt/docker/shekel/` runtime back to the repo's canonical state. Use this when the host has
diverged substantially (multiple files, secrets directory missing, network attachment wrong) or to
bootstrap a fresh shared-mode deployment from a host that previously ran bundled-mode.

```bash
# On the host, from the repo root:
cd /opt/shekel
git pull --ff-only
bash scripts/reconcile_prod_to_canonical.sh
# sudo will prompt once -- the script needs root only for the cert
# chown to uid 70 (postgres) on deploy/postgres/server.key.
```

The script:

1. Generates the Postgres TLS cert if not present (`deploy/postgres/server.{crt,key}`)
2. Creates `/opt/docker/shekel/{secrets,deploy/postgres}` directories
3. Migrates the four high-sensitivity secrets from the current `.env` into per-file entries under
   `secrets/` (mode 0600), and writes Valkey's password file `secrets/valkey_password` (mode 0640,
   for the valkey container's gid) from `.env`'s `SHEKEL_VALKEY_PASSWORD`
4. Rewrites `/opt/docker/shekel/.env` with `replaced_by_docker_secret` placeholders for the four
   secrets, plus `SHEKEL_IMAGE_DIGEST`, `SHEKEL_VALKEY_PASSWORD`, and `REGISTRATION_ENABLED=false`.
   Backs up the old `.env` to `.env.bak.<timestamp>`.
5. Creates the `shekel-frontend` bridge with the pinned subnet (172.32.0.0/24) if missing
6. Snapshots `/opt/docker/nginx/nginx.conf` and prints critical- directive presence (limit_req_zone,
   set_real_ip_from, timeouts) before overwriting from `deploy/nginx-shared/`. The pre-overwrite
   diff is saved to `/opt/docker/nginx/.reconcile-snapshot-<ts>/`.
7. Copies the repo's `docker-compose.yml` and `deploy/docker-compose.prod.yml` to
   `/opt/docker/shekel/`, and `deploy/valkey/` to `/opt/docker/shekel/deploy/valkey/`. Snapshots the
   old files for rollback.

The script STOPS short of `docker compose down && up -d` so you can review the merged config before
pulling the trigger. Its trailing output prints the exact post-script commands.

##### Gotchas that triggered hotfixes in this session

- **Docker secrets `uid`/`gid`/`mode` are ignored in non-Swarm Compose v2.** The default secret
  mount inherits the host file's ownership and mode. The `postgres_password` file must be readable
  by uid 70 (postgres) inside the db container; since the host file is josh:josh, the script writes
  it world-readable (`chmod 0644 /opt/docker/shekel/secrets/postgres_password`) so the postgres user
  can read via the "other" bits. Directory containment (`/opt/docker/shekel/secrets/` is mode 0700
  josh-only) keeps the value protected from host-side enumeration. The other three secrets
  (`secret_key`, `app_role_password`, `totp_encryption_key`) are read by uid 1000 (shekel) inside
  the app container, which matches the host file owner, so 0600 works for those.

- **Repo `deploy/nginx-shared/nginx.conf` drift.** The audit B7 hardening (`limit_req_zone`,
  `client_*_timeout`, `client_max_body_size`, narrowed `set_real_ip_from`) was applied to the
  on-host file but never back-ported. A blind repo->host sync clobbers them and crashes nginx with
  `zero size shared memory zone "public"`. The script's Step 6 snapshots the host file first and
  prints critical- directive presence before overwriting. After running the script the operator
  should diff the snapshot against the new file and confirm no audit-fix directive was lost.

- **Backend network subnet pin.** The repo file pins `backend` to 172.31.0.0/24. The on-host file
  under prior versions used the auto-assigned 172.25.0.0/16. `docker compose up -d` after the
  override copy tries to recreate the backend network and fails with `Resource still in use` because
  `shekel-postgres-exporter` in the monitoring stack is attached. Stop the exporter first:
  `cd /opt/docker/monitoring && docker compose stop shekel-postgres-exporter`, let the Shekel stack
  come up under the new subnet, then
  `docker compose up -d --force-recreate shekel-postgres-exporter` to rejoin.

- **Image entrypoint must support `_load_secret`.** The C-38 docker secrets pre-date the entrypoint
  code that reads them. Before flipping `.env` to placeholders, confirm the deployed image's
  entrypoint contains the `_load_secret` function:

  ```bash
  docker run --rm --entrypoint sh ghcr.io/saltyreformed-labs/shekel@sha256:<pinned> \
      -c 'grep -c _load_secret /home/shekel/app/entrypoint.sh'
  # Output must be >= 5 (one definition + four call sites).
  ```

  An image without `_load_secret` reads the placeholders as the literal SECRET_KEY value and fails
  the 32-char minimum check at every boot. Rebuild via CI (push to `main`) and update
  `SHEKEL_IMAGE_DIGEST` in `/opt/docker/shekel/.env` before the flip.

##### Post-script verification

```bash
# Re-read the merged config (the script also writes it to
# /opt/docker/shekel/.reconcile-snapshot-<ts>/merged-config.yml).
cd /opt/docker/shekel
docker compose config | less

# When ready to recreate:
cd /opt/docker && docker compose up -d nginx cloudflared  # attach to shekel-frontend
docker exec nginx nginx -t && docker exec nginx nginx -s reload
cd /opt/docker/shekel && docker compose down && docker compose up -d

# Health checks:
docker compose ps                                            # all four healthy
docker inspect shekel-prod-app --format '{{.HostConfig.ReadonlyRootfs}}'    # → true
docker logs shekel-prod-app 2>&1 | grep '^Loaded '            # → 4 lines, one per secret
curl -sI https://shekel.saltyreformed.com/login | head -5     # → 200 + sec headers
docker exec jellyfin bash -c 'getent hosts shekel-prod-app'   # → fails (isolation confirmed)
```

---

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

---

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

See [§5.3 Key LogQL Queries](#53-key-logql-queries).

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

The app service runs as the `shekel` user (Dockerfile USER directive) and writes the seed-complete
sentinel under `/home/shekel/app/state`. The volume was created under Commit C-34 and inherits the
in-image directory ownership, so this is mostly defensive. Verify once:

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

---

## 5. Monitoring & Observability

### 5.0 Architecture (Commit C-15)

Shekel logs flow through a four-stage pipeline:

```text
[Flask app]
   |   structured JSON to stdout (one record per line)
   v
[Docker json-file driver]
   |   short-term local rotation (10 MiB x 5 files)
   |   readable via "docker logs shekel-prod-app"
   v
[Grafana Alloy]                <-- runs in /opt/docker/monitoring/
   |   reads container logs via /var/run/docker.sock (read-only mount)
   |   parses JSON via the loki.process.shekel stage
   |   tags records with compose_service, level, logger, event labels
   v
[Loki]                          <-- runs on the "monitoring" network
   |   filesystem-backed storage on /opt/docker/monitoring/loki/data
   |   30-day retention (744h) configured in loki.yaml
   v
[Grafana]                       <-- https://grafana.saltyreformed.com
       (LAN-only via the existing nginx + wildcard cert)
```

**Tamper-resistance property.** The Shekel app container shares no volume and no network with the
Loki storage volume. An attacker who gains RCE in Gunicorn can spam new log records (which Alloy
will faithfully ingest) but cannot delete or rewrite records already shipped to Loki. The local
`json-file` driver buffer at `/var/lib/docker/containers/<id>/<id>-json.log` IS rewritable by a
host-root attacker, but anything Alloy already scraped from it is immutable in Loki. This satisfies
ASVS V7.3.3 / V7.3.4 to the level appropriate for a single-host deployment; the previous `applogs`
Docker volume that lived in the same trust boundary as the app was removed in Commit C-15 (audit
findings F-082, F-150). For an absolute tamper-evident trail (off-site, write-once), see the
deferred S3-with-Object-Lock option in the C-15 architectural decision notes.

The full collector and dashboard configuration is documented in `observability.md`. This runbook
section assumes Phase 0 -- 5 of that plan are complete.

### 5.1 Checking Application Logs

```bash
# View recent Flask application logs (JSON format).
docker logs shekel-prod-app --tail 20

# Follow logs in real time.
docker logs shekel-prod-app -f

# View Nginx access logs (JSON format).
docker logs shekel-prod-nginx --tail 20

# View PostgreSQL logs.
docker logs shekel-prod-db --tail 20

# View cloudflared tunnel logs.
journalctl -u cloudflared --no-pager -n 20

# View backup cron logs.
tail -50 /var/log/shekel_backup.log
```

**Flask log format (JSON, RFC3339Nano timestamps):** Each line is a single JSON object with these
stable keys; additional structured fields appear when the call site supplies them via `extra={...}`.

- `timestamp` -- RFC3339Nano UTC with microsecond precision and `Z` suffix, e.g.
  `2026-05-05T19:36:45.139287Z`.
- `level` -- `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- `logger` -- Python logger name (e.g. `app.routes.auth`).
- `message` -- Human-readable description.
- `request_id` -- UUID4 correlating every log line from a single HTTP request. Returned to the
  client in the `X-Request-Id` header so a user-reported issue can be looked up directly.
- `event` -- Structured event name (e.g. `login_success`, `rate_limit_exceeded`, `slow_request`).
  The full registry lives in `app/utils/log_events.py:EVENT_REGISTRY`.
- `category` -- One of `auth`, `business`, `access`, `audit`, `error`, `performance`.
- `remote_addr` -- Client IP (forwarded by nginx via `X-Forwarded-For`).
- `user_id` -- Authenticated user ID, omitted on anonymous requests.

The Alloy `loki.process.shekel` stage promotes `level`, `logger`, and `event` to Loki labels so
dashboards can filter on them without a `| json` parser stage in every query.

### 5.2 Querying Logs in Grafana

1. Open Grafana: `https://grafana.saltyreformed.com` (LAN-only).
2. Log in with the admin account (password in
   `/opt/docker/monitoring/secrets/grafana_admin_password`).
3. Navigate to **Explore** (compass icon in the left sidebar).
4. Select **Loki** as the data source. (Provisioned automatically per `observability.md` Phase 4
   datasources.yaml.)
5. Enter a LogQL query (see below) and click **Run query**.

### 5.3 Key LogQL Queries

The Alloy pipeline exposes both the raw Docker container labels (`compose_service`, `container`,
`compose_project`) and the JSON-extracted labels (`level`, `logger`, `event`). Queries below prefer
`compose_service` because it survives container renames.

| Purpose | Query |
|---------|-------|
| All app logs | `{compose_service="shekel-prod-app"}` |
| All auth events | `{compose_service="shekel-prod-app"} \| json \| category="auth"` |
| All access events (incl. rate-limit) | `{compose_service="shekel-prod-app"} \| json \| category="access"` |
| Login failures | `{compose_service="shekel-prod-app", event="login_failed"}` |
| Login successes | `{compose_service="shekel-prod-app", event="login_success"}` |
| Password changes | `{compose_service="shekel-prod-app", event="password_changed"}` |
| MFA events | `{compose_service="shekel-prod-app"} \| json \| event=~"mfa_.*"` |
| Rate-limit hits (F-146) | `{compose_service="shekel-prod-app", event="rate_limit_exceeded"}` |
| Rate-limit by path | `{compose_service="shekel-prod-app", event="rate_limit_exceeded"} \| json \| line_format "{{.path}} {{.remote_addr}}"` |
| Account lockouts | `{compose_service="shekel-prod-app", event="account_locked"}` |
| Slow requests | `{compose_service="shekel-prod-app", event="slow_request"}` |
| All errors | `{compose_service="shekel-prod-app", level="ERROR"}` |
| Business events | `{compose_service="shekel-prod-app"} \| json \| category="business"` |
| By user ID | `{compose_service="shekel-prod-app"} \| json \| user_id="1"` |
| Trace a request | `{compose_service="shekel-prod-app"} \| json \| request_id="<uuid>"` |

The `request_id` derived field declared in `datasources.yaml` makes a UUID in any log line
clickable -- it copies the value into a prefilled trace-a-request query so a user-reported
`X-Request-Id` lookup is one click.

### 5.4 Monitoring Stack Management

The Loki / Grafana / Alloy stack runs from `/opt/docker/monitoring/` per `observability.md`. The
Shekel stack does NOT need to share a Docker network with it -- Alloy reads container logs via the
docker socket, which works regardless of the source container's network membership.

```bash
# Check monitoring stack status.
docker ps --filter "name=alloy" --filter "name=loki" --filter "name=grafana"

# Start / restart the monitoring stack.
cd /opt/docker/monitoring
docker compose up -d
docker compose restart alloy   # e.g. after editing alloy/config/config.alloy

# Verify Alloy is scraping containers.
docker exec alloy wget -qO- 'http://localhost:12345/api/v0/component/discovery.docker.containers/debug/info' | head -c 600

# Confirm Shekel records are arriving in Loki.
docker exec loki wget -qO- 'http://localhost:3100/loki/api/v1/labels'
docker exec loki wget -qO- \
  'http://localhost:3100/loki/api/v1/query?query=%7Bcompose_service%3D%22shekel-prod-app%22%7D' \
  | head -c 400

# Check Alloy logs for parser failures (a JSON shape regression
# would surface here -- "could not parse" / "skip due to error").
docker logs alloy --tail 50
```

If a deploy lands and Alloy's `loki.process.shekel` stage suddenly shows `failed to parse` errors,
the most likely cause is a regression in `app/utils/logging_config.py` -- the formatter must emit
the keys `timestamp`, `level`, `logger`, `message`, `event`, `request_id` for the parser to map
fields cleanly. Re-run `pytest tests/test_utils/test_logging_config.py` to catch shape drifts before
they reach production.

### 5.5 Alerting on Rate-Limit Pressure

Rate-limit hits are emitted as `event="rate_limit_exceeded"` records under `category="access"`
(audit Commit C-15 / finding F-146). The intended alert in Grafana (provision under
`/opt/docker/monitoring/grafana/provisioning/alerting/`) is:

- **Datasource:** Loki
- **Query:**
  `count_over_time({compose_service="shekel-prod-app", event="rate_limit_exceeded"} [5m])`
- **Condition:** is above 10 (tune after a week of baseline data)
- **Evaluation:** every 1 minute, for at least 5 minutes

A burst of 10+ rate-limit hits in 5 minutes is well above the single-user steady-state (effectively
zero outside test windows) and is the earliest queryable signal of a credential-stuffing campaign
that the per-route 5-per-15min ceiling is otherwise silently absorbing. Pair the rule with Grafana
contact-point delivery (email or webhook) per the operator's preference.

### 5.5 Health Checks

The `/health` endpoint returns the application and database status:

```bash
# Via localhost (bypasses Cloudflare):
curl -s http://localhost/health
# Expected: {"status":"healthy","timestamp":"..."}

# Via Cloudflare Tunnel (full chain):
curl -s https://<domain>/health
# Expected: same response (may require Access bypass; see §6.4)
```

Health checks are also performed automatically by:

- **Docker:** Every 30 seconds for the `app` and `nginx` containers
- **deploy.sh:** After each deployment (60s timeout, 5s interval)

---

## 6. Cloudflare Management

### 6.1 Tunnel Status

```bash
# Check the systemd service.
sudo systemctl status cloudflared

# View recent tunnel logs.
journalctl -u cloudflared --no-pager -n 30

# List all tunnels on the account.
cloudflared tunnel list

# Check tunnel connectivity (should show active connections).
cloudflared tunnel info shekel
```

### 6.2 Restarting the Tunnel

```bash
sudo systemctl restart cloudflared
```

**When to restart:**

- After editing `/etc/cloudflared/config.yml`
- After a cloudflared package update
- If the tunnel shows connection errors in `journalctl`

**Note:** Restarting cloudflared causes a brief (<5 second) interruption in external access.
Internal (LAN) access via `http://localhost` is not affected.

### 6.3 Tunnel Configuration Changes

```bash
# Edit the config file.
sudo nano /etc/cloudflared/config.yml

# Validate the config (dry run).
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate

# Restart to apply changes.
sudo systemctl restart cloudflared

# Verify the tunnel is running.
sudo systemctl status cloudflared
curl -s https://<domain>/health
```

### 6.4 Adding a New Authorized User (Cloudflare Access)

Cloudflare Access controls who can reach the application. Only email addresses in the Access policy
can pass through.

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Navigate to **Zero Trust** (or `https://one.dash.cloudflare.com`)
3. Go to **Access** > **Applications**
4. Click on **Shekel Budget App**
5. Edit the **Allowed Users** policy
6. Under **Include** > **Emails**, add the new email address
7. Click **Save**

The new user can now authenticate via Cloudflare Access (email OTP or configured identity provider)
and reach the Shekel login page.

**Note:** This only grants access through the Cloudflare layer. The user still needs a Shekel
account (via seed script or future registration) to log into the application.

### 6.4a Attaching an Access Policy at the cloudflared Level (Commit C-37)

Sections 6.4 and 6.5 cover the dashboard side of Access management. This subsection covers the
matching `cloudflared/config.yml` ingress block that is required for the audit fix to take effect.
Audit finding F-061.

The committed `cloudflared/config.yml` carries an `originRequest.access` block:

```yaml
originRequest:
  noTLSVerify: true
  access:
    required: true
    teamName: <TEAM_NAME>
    audTag:
      - <AUD_TAG>
```

Before the first `cloudflared` start (or after rotating the Access application), replace the
placeholders:

1. **`<TEAM_NAME>`** -- the subdomain of `cloudflareaccess.com` for your Zero Trust team. Find it
   under **Zero Trust** > **Settings** > **Custom Pages** (top of the page) or in the URL of any
   Access application page (`https://<TEAM>.cloudflareaccess.com/...`).

2. **`<AUD_TAG>`** -- the Application Audience tag. Each Access application has its own AUD. Find it
   under:
   - **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
   - Click into the application; the **Overview** tab lists **Application Audience (AUD) Tag** as a
     64-character hex string.

3. Apply the placeholders on the host:

   ```bash
   sudo $EDITOR /etc/cloudflared/config.yml
   # Replace <TEAM_NAME> and <AUD_TAG> with the values from steps 1 and 2.

   # Validate the config syntax before reload.
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate

   # Apply.
   sudo systemctl restart cloudflared
   ```

4. Verify the policy is enforced. From a browser without an active Access session:

   ```text
   https://<DOMAIN>/health
   ```

   The expected response is the Cloudflare Access login page. A direct `200 OK` with the JSON health
   payload would mean the policy is NOT applied; recheck the AUD tag and the ``required: true``
   flag.

**What `required: true` does.** cloudflared validates the `Cf-Access-Jwt-Assertion` header on every
request. Without a valid JWT for the AUD above, cloudflared returns 403 at the edge -- the request
never reaches Nginx. This closes the credential-stuffing surface on `/login`: even an attacker with
a leaked Shekel password cannot reach the login form without a valid Access JWT first.

**Operator emergency bypass.** If the Cloudflare Access dashboard is unreachable (rare; depends on
Cloudflare's own auth chain) and you need to log into Shekel, use the LAN bypass:

```bash
# From a machine on the LAN, reach Nginx directly without going
# through cloudflared.  This skips the Access check.
curl -sI http://<LAN_HOST>/health
```

Then connect to `http://<LAN_HOST>` in a browser; you are now past cloudflared and only Shekel's own
login + MFA stand between you and the app. Restore the Access posture as soon as the dashboard is
reachable again.

### 6.4b Cloudflared Metrics Endpoint Binding (Commit C-37)

The committed `cloudflared/config.yml` pins the metrics endpoint to loopback only. Audit finding
F-128.

```yaml
metrics: 127.0.0.1:2000
```

**Why this matters.** The default `cloudflared` behaviour binds the Prometheus metrics endpoint on
`0.0.0.0:2000` inside the container, making it reachable from every other peer on whatever Docker
bridge cloudflared is attached to. An attacker landing in any sibling container could poll
`/metrics` for tunnel health, request counts, and connection state -- operational data that should
not leak laterally even within the trusted homelab subnet.

Pinning to `127.0.0.1` keeps the endpoint reachable only from inside the cloudflared container
itself.

**Verifying the bind.** From inside the cloudflared container, the endpoint must answer; from any
sibling container, it must not.

```bash
# Inside cloudflared -- expect a 200 with metrics output.
docker exec cloudflared wget -qO- http://127.0.0.1:2000/metrics | head

# From the app container -- expect a connection refused.
# (This catches an accidental rebind to 0.0.0.0.)
docker exec shekel-prod-app sh -c \
    'wget -qO- --timeout=2 http://cloudflared:2000/metrics' \
    && echo "FAIL: metrics endpoint reachable from app" \
    || echo "OK: metrics endpoint not reachable from app"
```

The second probe should print `OK:`. If it prints `FAIL:`, the metrics directive in
`cloudflared/config.yml` is missing or has been overridden somewhere in the Cloudflare dashboard or
the systemd unit file -- check those before opening an incident.

### 6.5 Removing an Authorized User

1. Navigate to **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
2. Edit the **Allowed Users** policy
3. Remove the email address from the **Include** > **Emails** list
4. Click **Save**

The user's existing Cloudflare Access session will expire (default: 24 hours). To revoke access
immediately:

1. Navigate to **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
2. Click the **Overview** tab
3. Under **Active Sessions**, find and revoke the user's session

### 6.6 Updating WAF Rate Limit Rules

The Cloudflare WAF rate limits protect `/login` and `/auth/mfa/verify` against brute-force attacks
at the network edge (before traffic reaches the origin).

Current configuration:

| Rule | Path | Method | Rate | Block Duration |
|------|------|--------|------|---------------|
| Login brute force protection | `/login` | POST | 20 per 10 seconds | 60 seconds |
| MFA brute force protection | `/auth/mfa/verify` | POST | 20 per 10 seconds | 60 seconds |

To modify thresholds:

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Select your domain
3. Navigate to **Security** > **WAF** > **Rate limiting rules**
4. Click the rule name (e.g., `Login brute force protection`)
5. Click **Edit**
6. Adjust the **Rate** or **Period** fields
7. Click **Save**

Changes take effect within seconds.

### 6.7 Rotating Tunnel Credentials

If tunnel credentials are compromised:

```bash
# Delete the old tunnel.
cloudflared tunnel delete shekel

# Create a new tunnel (generates new credentials).
cloudflared tunnel create shekel

# Update the DNS record.
cloudflared tunnel route dns shekel <domain>

# Update /etc/cloudflared/config.yml with the new tunnel ID.
sudo nano /etc/cloudflared/config.yml
# Replace the tunnel UUID and credentials-file path.

# Restart cloudflared.
sudo systemctl restart cloudflared

# Verify.
curl -s https://<domain>/health
```

---

## 7. Troubleshooting

### 7.1 Common Issues

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| App unreachable externally | cloudflared service down | `sudo systemctl status cloudflared` then `sudo systemctl restart cloudflared` |
| App unreachable externally | Docker containers down | `docker compose ps` then `docker compose up -d` |
| "Access Denied" on all requests | Cloudflare Access misconfigured | Check Access policy in Zero Trust dashboard; verify email is in the allowed list |
| Cloudflare 502 Bad Gateway | Nginx or app container unhealthy | `docker compose ps` to check health; `docker logs shekel-prod-nginx` and `docker logs shekel-prod-app` for errors |
| Cloudflare 522 Connection Timed Out | cloudflared cannot reach Nginx | Verify Nginx is running: `docker compose ps`. Check cloudflared logs: `journalctl -u cloudflared -n 20` |
| 429 on first login attempt | Cloudflare rate limit too aggressive | Check WAF rules in Cloudflare dashboard (Security > WAF > Rate limiting rules); increase threshold |
| 429 after a few login attempts | Flask-Limiter rate limit (5/15min) | Wait 15 minutes. To clear immediately: `docker restart shekel-prod-valkey` -- counters are stored in Valkey's memory alone (persistence off), so restarting it clears them, while restarting the app does NOT. No Valkey user may flush: the app's ACL user runs only the limiter's commands (`deploy/valkey/users.acl`). Limited requests fail closed for the seconds it is down |
| 500 on every login attempt | Valkey container down or unreachable, or the app's Valkey password wrong | App is configured fail-closed: rate-limit storage outage rejects every limited request. `docker compose ps valkey` to check status; `docker logs shekel-prod-valkey --tail 50` for errors (start.sh names an empty password or a missing `user shekel` rule); `docker compose up -d valkey` to restart. A healthy Valkey with 500s still is a password mismatch: `.env`'s `SHEKEL_VALKEY_PASSWORD` and `secrets/valkey_password` must match (two homes until plan#2168). Login resumes immediately once Valkey accepts the app's login |
| Wrong IP in logs (127.0.0.1) | Nginx real IP config issue | Verify `set_real_ip_from` and `real_ip_header CF-Connecting-IP` in the active Nginx config: `deploy/nginx-bundled/nginx.conf` (bundled mode) or `deploy/nginx-shared/nginx.conf` (shared mode -- the runtime copy is `/opt/docker/nginx/nginx.conf`) |
| No logs in Grafana | Promtail not scraping | `docker logs promtail`. Verify `monitoring` network exists. Verify app is on the network: `docker network inspect monitoring` |
| CSS/JS not loading | Static files volume issue | `docker exec shekel-prod-nginx ls /var/www/static/` to verify files exist. Rebuild app: `docker compose build app && docker compose up -d` |
| Health check returns 500 | Database connection issue | `docker exec shekel-prod-db pg_isready -U shekel_user -d shekel`. Check `docker logs shekel-prod-app --tail 20` |
| Database backup failed | Container down or disk full | `docker ps` to check shekel-prod-db. `df -h` to check disk space |
| NAS backup failed | NAS not mounted | `mount \| grep nas`. Remount: `sudo mount -a` |
| Deploy script rollback failed | No previous image tagged | Manual intervention: `docker logs shekel-prod-app` to diagnose, then fix and redeploy |
| App fails with `connection requires SSL` | Shared-mode TLS not staged | Run `sudo ./scripts/generate_pg_cert.sh` (Commit C-37). See §4.12 Postgres TLS for the full procedure. |
| Postgres logs `could not load private key file` | `server.key` mode not 0600 or wrong owner | Re-run `sudo ./scripts/generate_pg_cert.sh --force`; the chown step is what closes this. |
| Postgres logs `private key file has group or world access` | Same as above | Same fix as above. |
| `SHOW ssl;` returns `off` after override applied | db service did not pick up the override | `docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --force-recreate db` |
| Cloudflare Access never prompts | Missing `originRequest.access` block | See §6.4a; check that `<TEAM_NAME>` and `<AUD_TAG>` placeholders are replaced and `cloudflared --config ... ingress validate` passes. |
| Metrics endpoint reachable from app container | Missing `metrics: 127.0.0.1:2000` directive | See §6.4b. Restart cloudflared after editing `/etc/cloudflared/config.yml`. |

### 7.2 Log Locations

| Log | Command | Contents |
|-----|---------|----------|
| Flask app (JSON) | `docker logs shekel-prod-app` | Request logs, auth events, business events, errors |
| Nginx (JSON) | `docker logs shekel-prod-nginx` | HTTP access logs, upstream errors |
| PostgreSQL | `docker logs shekel-prod-db` | Database server logs, connection errors |
| Cloudflared | `journalctl -u cloudflared` | Tunnel connection logs, reconnects |
| Backups | `cat /var/log/shekel_backup.log` | Cron job output for backup, retention, verify, integrity |
| Grafana | `docker logs grafana` | Grafana server logs |
| Promtail | `docker logs promtail` | Log scraper status, discovery errors |

### 7.3 Emergency Procedures

#### Application is down -- restore service quickly

```bash
# 1. Identify which container is unhealthy.
docker compose ps

# 2. Check container logs for the failing service.
docker logs shekel-prod-app --tail 50
docker logs shekel-prod-nginx --tail 20
docker logs shekel-prod-db --tail 20

# 3. Restart the failing service.
docker compose restart app    # or: nginx, db

# 4. If the app container won't start, restart the full stack.
docker compose down && docker compose up -d

# 5. Verify recovery.
curl -s http://localhost/health
```

#### Database is corrupted -- restore from backup

```bash
# 1. Identify the latest good backup.
ls -lht /var/backups/shekel/

# 2. Restore (will prompt for confirmation).
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# 3. Verify the restore.
curl -s http://localhost/health
docker exec shekel-prod-app python scripts/integrity_check.py --verbose
```

If local backups are corrupted, restore from NAS:

```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

#### Locked out of the app -- MFA device lost

```bash
# SSH to the Proxmox host.
docker exec shekel-prod-app python scripts/reset_mfa.py your-email@example.com
# Output: MFA has been disabled for your-email@example.com.

# Log in with email + password (MFA is now disabled).
# Re-enable MFA in Settings > Security after logging in.
```

#### Locked out of Cloudflare Access

1. Log into the Cloudflare dashboard directly at `https://dash.cloudflare.com` (this is independent
   of the tunnel and Access policy)
2. Navigate to **Zero Trust** > **Access** > **Applications**
3. Either:
   - Add your current email to the **Allowed Users** policy, or
   - Temporarily set the policy action to **Bypass** (allows everyone)
4. Access the app and verify the fix
5. Restore the original policy

#### Complete disaster recovery -- host is lost

1. Provision a new Proxmox host with Docker and Docker Compose
2. Clone the repository: `git clone <repo-url> /opt/shekel`
3. Reconstruct `.env` from `.env.example`:
   - `SECRET_KEY`: generate new (users must re-login)
   - `TOTP_ENCRYPTION_KEY`: use the backed-up key from password manager, or generate new (users must
     re-enroll MFA)
   - `POSTGRES_PASSWORD`: use the password from the backup, or set new
4. Create the monitoring network: `docker network create monitoring`
5. Start the stack: `docker compose up -d`
6. Restore from NAS backup: `./scripts/restore.sh /mnt/nas/backups/shekel/<latest>.sql.gz`
7. Re-install cloudflared and configure the tunnel (see `cloudflared/config.yml`)
8. Re-configure cron jobs (see §1 Cron Schedule)
9. Verify: `curl -s http://localhost/health` and `curl -s https://<domain>/health`

See `docs/runbook_secrets.md` for detailed secret reconstruction procedures.
