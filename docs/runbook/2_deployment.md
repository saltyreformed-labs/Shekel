# Shekel Operations Runbook: Deployment

Section 2 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

## 2. Deployment

### 2.1 Deploying a New Version

Production deploys with `shekel-deploy`, which pins `ghcr.io/saltyreformed-labs/shekel@sha256:...`,
cosign-verified, on the hardened stack at `/opt/docker/shekel`. A self-hosted install runs the image
CI builds and signs, and updates by 2.2. Development is `docker-compose.dev.yml`.

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
   database). It reports the revisions the target adds: a **migration-bearing** release. It also
   asks the previous image for `scripts/owner_entrypoint.sh`, which the migrate service runs: an
   image built before card plan#2166 has none, so it cannot run under these compose files, and a
   failure is then not rolled back by a re-pin (2.3). `--dry-run` reports all of this without
   deploying.
4. **`pg_dump -Fc` of the whole database** into `~/shekel-backups/` (override with
   `SHEKEL_BACKUP_DIR`), written to a `.part` file and renamed only after `pg_restore -f /dev/null`
   decodes every data block of it (a table-of-contents read passes a truncated archive).
   **No dump, no deploy** -- a dump failure aborts *before* the pin is touched.
5. Rewrites `SHEKEL_IMAGE_DIGEST` in `/opt/docker/shekel/.env` and `docker compose up -d app`.
   Compose runs the one-shot migrate service first, from the same image, and starts the app only
   once it exits 0. The deploy runs there: its step 3 runs the migrations, the reference seed, the
   three ledger hooks and the audit-trigger check in ONE transaction, committed together or not at
   all. A deploy that fails fails the `up`, with the app's new container created and not started.
6. Polls the app container's healthcheck for up to 4 minutes; ntfy ping either way.
7. **On failure**, stops the migrate container if it is still running the deploy, and the new app
   container, re-reads the stamp, and either re-pins the previous digest or refuses (2.3).

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

### 2.2 Updating a Self-Hosted Install

A self-hosted install is the README's Quick Start: the image CI builds and signs on `main`, and the
configuration files of the commit that image was built from. A release changes both, so an update
takes both, by the README's Updating section. Migrations run in the migrate service, which
`docker compose up` runs from the new image before it starts the app. The README's steps check no
signature; `deploy/README.md`'s `cosign verify` command does.

### 2.3 Rolling Back

**In production, `shekel-deploy`'s re-read stamp decides.**

When the new image fails (compose rejects it, as it does when the deploy fails in the migrate
service; its app container reports unhealthy; or it is not healthy within 4 minutes),
`shekel-deploy` first stops the migrate container if it is still running the deploy, so step 3 can
commit nothing more, and stops the new app container, so a failed release is not left serving. Then
it re-reads the database's Alembic stamp. That stamp, not the release, decides what happens:

- **The previous image can resolve the stamp.** The release migrated nothing, or the deploy's step 3
  failed and rolled back: since plan step `X-cv` the migrations, the reference seed, the ledger
  hooks and the audit-trigger check are ONE transaction, so a failure there leaves the stamp unmoved
  even for a migration-bearing release. The script saves the failed containers' logs beside the
  dump, the migrate container's then the app's, in one file (the dump's name with
  `.failed-container.log` for `.dump`), and re-pins the previous digest. After a health failure it
  then waits for that image to be healthy; after a compose failure it does not wait. Nothing is
  restored. When the previous image does not come up either, see below.
- **The previous image cannot resolve the stamp.** Step 3 COMMITTED and something later failed (the
  deploy's step 4, the first user's seed; or the app's start, its static copy, or the app never
  became healthy). Re-pinning would give a second failed deploy -- reproduced as
  `CommandError: Can't locate revision identified by ...` (finding F-8) -- so the script
  **refuses**, leaves the pin at the new digest, saves the logs, and names the ways out:
  1. **Start the new pin again**, offered when the new image resolves the stamp (it wrote it):
     `cd /opt/docker/shekel && docker compose up -d app`, which runs the deploy again first and then
     starts the app. Nothing is restored and nothing is lost. The right first move when the log
     shows a transient failure, such as a release that was only slower than the health window.
  2. **Restore the pre-deploy dump** and re-pin the previous digest (below). This discards
     everything written since the dump.
- **The stamp could not be re-read** (the database container did not answer). The script claims
  nothing about a commit and leaves the pin. Once the database answers, roll back through
  `shekel-deploy <old-digest>`: its pre-flight reads the stamp and refuses up front, naming the
  restore, if the previous image cannot resolve it.
- **The migrate container could not be stopped.** It may still be inside step 3, so no stamp read is
  final: the script refuses and leaves the pin. `docker stop shekel-prod-migrate`, then
  `shekel-deploy <old-digest>`. An app container that will not stop is a warning only (ruling
  plan#3539): it runs no migration and cannot move the stamp, so the decision goes on, and the
  output says it may still be serving the new image.
- **The previous image cannot run under the compose files in place.** An image built before card
  plan#2166 has no `scripts/owner_entrypoint.sh`, which the migrate service runs, so re-pinning it
  would bring nothing up; the first release of that card is this case. The pre-flight asks, and
  `--dry-run` says so. On a failure the script refuses and leaves the pin at the new digest, and the
  way back it names is the compose files that image ran under as well as its digest, with what the
  re-read stamp shows: whether that image resolves it or the dump must be restored first.

**Read the saved log first** (the path is in the output and the ntfy alert). The migrate container's
log comes first and names the deploy step that failed; the app's follows. Together they say whether
a restart can clear it.

**When the re-pinned previous image does not come up either.** After a health failure the script
then ends `rollback container also unhealthy; manual intervention required`. It prints the same line
when the re-pin's own `compose up` failed and usually nothing started (finding BAL-540); after a
compose failure it prints `WARN: revert compose up also failed; container state unknown.` instead.
The re-pin's `up` runs the previous image's deploy in the migrate service first, so a fault both
images meet in the deploy fails that `up` as well, with the app created and not started:
`docker compose ps -a` shows `shekel-prod-migrate` exited with a nonzero code, and
`docker logs shekel-prod-migrate` names the step. The previous image knows the stamp's revision, so
the migration level is not the cause; something both images meet is: schema drift the stamp does not
record, the data, or the environment. Whether the dump can help turns on step 3: if the saved log
has no `Database initialised: ONE transaction, committed.`, the failed release committed nothing
past the deploy's steps 1 and 2 (idempotent: the schemas and the web login's grants, which every
deploy re-runs), so the fault predates the deploy and the dump holds it too: restoring cannot help,
and the fault is repaired in place. If step 3 did commit, what it wrote may be the fault, and
restoring the dump (below) is a candidate.

An app whose deploy passed but whose own start fails crash-loops under `restart: unless-stopped`,
each pass restarting gunicorn alone: since card plan#2166 the app's start runs no deploy. Docker
waits between passes, from a tenth of a second up to about a minute, so `docker compose ps` may show
it `Up` for seconds at a time with `(health: starting)` during a pass or `Restarting` during a wait.
The tell is `docker inspect shekel-prod-app --format '{{.State.Status}} {{.RestartCount}}'`: a state
of `restarting`, or a restart count that grows between two readings two minutes apart. Stop it
before repairing and start it after, with an `up` that runs the deploy again first:

```bash
cd /opt/docker/shekel
docker compose stop app      # a crash-looping app retries on its own under `restart: unless-stopped`
# ... repair what the saved log names ...
docker compose up -d app     # the deploy, in the migrate service, then the app
```

A missing audit trigger is the case the X-cv rehearsal measured: every image since C-13 counts them
at every deploy, since card plan#2166 in the migrate service, so the release's deploy refuses and
the re-pinned image's deploy refuses the same database, and no app is left running. Since ruling
`R-BAL129` the release's refusal names each audited table missing its trigger and, for a trigger
dropped by hand, prints the statements that re-create them, each ending in `;`. Each has this form
and runs as the database owner (the app role has DML only), between the stop and the start above:

```sql
CREATE TRIGGER audit_<table> AFTER INSERT OR UPDATE OR DELETE ON <schema>.<table>
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func();
```

```bash
docker exec shekel-prod-db psql -U shekel_owner -d shekel -v ON_ERROR_STOP=1 \
    -c '<the statements from the saved log>'
```

The owner logs in over the database container's own socket, which asks no password. (Measured
2026-11-13 on a throwaway stack from card plan#2166's branch: as the owner this form dropped and
re-created `audit_transactions`, and as the web login `CREATE TRIGGER` was refused,
`permission denied for table transactions`.)

If the refusal names a table newly added to `AUDITED_TABLES`, its migration did not attach the
trigger, and the migration is the fix. An image older than `R-BAL129` prints only a count, and its
`flask db upgrade` advice does not re-create a dropped trigger. (Measured 2026-09-23 in the X-cv
rehearsal's second pass, before card plan#2166, when the app's own start ran the deploy, on a copy
of production's data with one audit trigger dropped and the 6-3 release image `876fbf5c5bf7` as the
previous image: the release's saved log named `budget.transactions` and printed one statement;
`docker compose ps` showed the crash-looping previous container as `Up 4 seconds (health: starting)`
51 seconds after the re-pin created it, after 5 restarts: its first pass ran 11 seconds and the next
four about 8 seconds each, and the waits between them grew from 0.1 to 1.6 seconds, where an earlier
rehearsal of the same image and fault ran 11 to 12 second passes with waits of at most a quarter of
a second, so that container was up nearly all the time; the stop, that statement run through `psql`
as printed, and the start above brought it up healthy in about 15 seconds, with the re-created
trigger identical to the dropped one and no table changed.)

**Restoring discards everything written since the dump was taken** -- minutes of real entries in a
budgeting app. Capture the failed state first so that window stays recoverable.

```bash
cd /opt/docker/shekel

# 1. STOP THE APP FIRST.  A live app writes during the restore, and under
#    `restart: unless-stopped` a crash-looping one comes back up until it
#    is stopped.
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

#    Then hand every object to the owner login.  A dump from before the
#    cutover names the superuser as each one's owner, which the owner's
#    deploy cannot change; on a later dump this changes nothing.
docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh

# 4. --clean drops only what the archive knows about, so a table the failed
#    migration created survives and will collide when it is retried.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c '\dt budget.*'

# 5. Only now put the pin back.
sed -i 's|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=<old-digest>|' /opt/docker/shekel/.env
docker compose up -d app
```

### 2.4 First-Time Setup

A new install follows the README's Quick Start. The host-side jobs of 1's Cron Schedule
(`backup.sh`, `backup_retention.sh`, `verify_backup.sh`) are neither among the files it downloads
nor in the image, so they run from a checkout of this repository.

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
| `docker-compose.yml` | `/opt/docker/shekel/docker-compose.yml` |
| `deploy/docker-compose.prod.yml` | `/opt/docker/shekel/docker-compose.override.yml` |
| `deploy/valkey/` (`users.acl`, `start.sh`) | `/opt/docker/shekel/deploy/valkey/` |
| `deploy/postgres/provision_roles.sh` (0755) | `/opt/docker/shekel/deploy/postgres/provision_roles.sh` |
| `deploy/nginx-shared/nginx.conf` | `/opt/docker/nginx/nginx.conf` |
| `deploy/nginx-shared/conf.d/shekel.conf` | `/opt/docker/nginx/conf.d/shekel.conf` |

The repo is the source of truth. The on-host copies must match byte-for-byte; drift between them is
a deployment-integrity bug (security-2026-04-15 finding F-021). `shekel-deploy` copies none of them:
a release that changes one runs its host steps (2.6).

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
diff -u /opt/shekel/docker-compose.yml                        /opt/docker/shekel/docker-compose.yml
diff -u /opt/shekel/deploy/docker-compose.prod.yml            /opt/docker/shekel/docker-compose.override.yml
diff -ru /opt/shekel/deploy/valkey                            /opt/docker/shekel/deploy/valkey
diff -u /opt/shekel/deploy/postgres/provision_roles.sh        /opt/docker/shekel/deploy/postgres/provision_roles.sh
```

Any non-empty diff is an incident: investigate before re-syncing, because the host change may carry
an undocumented production fix that must be brought into the repo first.

#### One-shot reconciliation script

`scripts/reconcile_prod_to_canonical.sh` bundles the seven host-side steps that bring a drifted
`/opt/docker/shekel/` runtime back to the repo's canonical state. Use this when the host has
diverged substantially (multiple files, secrets directory missing, network attachment wrong) or to
bootstrap a fresh shared-mode deployment from a host that previously ran bundled-mode. It reads
`SHEKEL_VALKEY_PASSWORD` from `.env` and stops at step 3 without it, so on a host from before the
Valkey switch, whose `.env` names `SHEKEL_REDIS_PASSWORD`, rename that variable first.

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
3. Writes each secret from the current `.env` to its file under `secrets/` when the file is missing
   or empty: `secret_key`, `totp_encryption_key`, `postgres_password`, `app_role_password` and
   `valkey_password` (from `SHEKEL_VALKEY_PASSWORD`). It refuses one whose `.env` value is absent,
   empty or the placeholder a previous run left, since it must be the value in use. It also makes
   `owner_role_password`, the database owner's: `.env`'s `OWNER_ROLE_PASSWORD` when that holds a
   real value, else a new URL-safe password, never printed. On every run, a file already there
   included, it sets each file's mode for the container user that reads it (the gotcha below)
4. Rewrites `/opt/docker/shekel/.env` with `replaced_by_docker_secret` placeholders for the five
   secrets, `OWNER_ROLE_PASSWORD` among them, and `SHEKEL_VALKEY_PASSWORD`, plus
   `SHEKEL_IMAGE_DIGEST` and `REGISTRATION_ENABLED=false`. Backs up the old `.env` to
   `.env.bak.<timestamp>`.
5. Creates the `shekel-frontend` bridge with the pinned subnet (172.32.0.0/24) if missing
6. Snapshots `/opt/docker/nginx/nginx.conf` and prints critical- directive presence (limit_req_zone,
   set_real_ip_from, timeouts) before overwriting from `deploy/nginx-shared/`. The pre-overwrite
   diff is saved to `/opt/docker/nginx/.reconcile-snapshot-<ts>/`.
7. Copies the repo's `docker-compose.yml` and `deploy/docker-compose.prod.yml` to
   `/opt/docker/shekel/`, `deploy/valkey/` to `/opt/docker/shekel/deploy/valkey/`, and the database
   container's provisioning script `deploy/postgres/provision_roles.sh` to
   `/opt/docker/shekel/deploy/postgres/` at 0755 (the postgres image sources a non-executable hook
   instead of running it). Snapshots the old files for rollback, of `deploy/postgres/` the script
   alone: the TLS key beside it is uid 70's and unreadable to the operating user.

The script STOPS short of recreating the stack so you can review the merged config before pulling
the trigger. Its trailing output prints the exact post-script commands: the database comes up first
and runs its provisioning script, which makes the owner's login on a database made before the script
existed and changes nothing on one it already ran on, and only then the rest of the stack, whose
migrate service logs in as the owner.

##### Gotchas that triggered hotfixes in this session

- **Docker secrets `uid`/`gid`/`mode` are ignored in non-Swarm Compose v2.** The default secret
  mount inherits the host file's ownership and mode. The db container reads three files as uid 70
  (postgres): `postgres_password`, and the two logins' passwords its provisioning script sets,
  `app_role_password` and `owner_role_password`. Since the host files are josh:josh, the script
  makes those three 0644, so the postgres user reads them through the "other" bits. Directory
  containment (`/opt/docker/shekel/secrets/` is mode 0700 josh-only) keeps the values protected from
  host-side enumeration. `secret_key` and `totp_encryption_key` are read by uid 1000 (shekel) inside
  the app container alone, which matches the host file owner, so they are 0600, and
  `valkey_password` is 0640 for the valkey container's gid 1000. The script sets each mode on every
  run, so a file left at another mode is put right: an `app_role_password` an earlier run wrote
  0600, when the app alone read it, becomes 0644.

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
  code that reads them. Before flipping `.env` to placeholders, confirm the deployed image's two
  entrypoints load every secret the script turns into a placeholder that they read:

  ```bash
  docker run --rm --entrypoint sh ghcr.io/saltyreformed-labs/shekel@sha256:<pinned> -c "
      grep -cE '^_load_secret (SECRET_KEY|APP_ROLE_PASSWORD|TOTP_ENCRYPTION_KEY|VALKEY_PASSWORD) ' /home/shekel/app/entrypoint.sh
      grep -cE '^_load_secret OWNER_ROLE_PASSWORD ' /home/shekel/app/scripts/owner_entrypoint.sh"
  # Output must be 4, the web server's, then 1, the deploy's.
  ```

  The other two placeholders are read by other containers: `POSTGRES_PASSWORD` by the database image
  itself, from `POSTGRES_PASSWORD_FILE`, and `SHEKEL_VALKEY_PASSWORD` by Valkey's `start.sh` as well
  as by the app. An image without `_load_secret` reads the placeholders as the literal SECRET_KEY
  value and fails the 32-char minimum check at every boot; one built before card plan#2166 has no
  `owner_entrypoint.sh`, and the second grep says so by name. Rebuild via CI (push to `main`) and
  update `SHEKEL_IMAGE_DIGEST` in `/opt/docker/shekel/.env` before the flip. (Measured 2026-11-13 on
  an image built from card plan#2166's branch: 4, then 1.)

##### Post-script verification

```bash
# Re-read the merged config (the script also writes it to
# /opt/docker/shekel/.reconcile-snapshot-<ts>/merged-config.yml).
cd /opt/docker/shekel
docker compose config | less

# When ready to recreate:
cd /opt/docker && docker compose up -d nginx cloudflared  # attach to shekel-frontend
docker exec nginx nginx -t && docker exec nginx nginx -s reload
cd /opt/docker/shekel && docker compose down && docker compose up -d --wait db
docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh
docker compose up -d

# Health checks:
docker compose ps                                            # db, valkey and app healthy
docker compose ps -a migrate                                 # → Exited (0): the deploy passed
docker inspect shekel-prod-app --format '{{.HostConfig.ReadonlyRootfs}}'    # → true
docker logs shekel-prod-app 2>&1 | grep '^Loaded '            # → 4 lines, one per secret the app reads
docker logs shekel-prod-migrate 2>&1 | grep '^Loaded '        # → 1 line, the owner's password
curl -sI https://shekel.saltyreformed.com/login | head -5     # → 200 + sec headers
docker exec jellyfin bash -c 'getent hosts shekel-prod-app'   # → fails (isolation confirmed)
```

### 2.6 Host Steps a Release Carries

`shekel-deploy` changes one thing in production, the image pin, and its automatic rollback re-pins
the previous image onto whatever host files are in place, or refuses when that image cannot run
under them (2.3). It copies none of 2.5's host copies and writes nothing under
`/opt/docker/shekel/secrets/`. A card whose change needs the host changed too writes the steps here,
one subsection naming the card, each with its rollback and what was measured; `/release` runs them
at its step 11 for each card the release carries, after the developer's "Logged out, deploy now",
from `~/projects/Shekel` at `main`. A step marked as coming before the merge into `main` runs at
`/release` step 5 instead. Once production runs a card, its subsection is history, and the next
change to this section deletes it.

#### The deploy moves out of the web container (plan#2166)

The release carrying plan#2166 moves the deploy (the migrations, the seeds, the web login's grants)
out of the web container into the one-shot migrate service, run as a new login that is not a
superuser, the database owner `shekel_owner`; the web server is left the web login's password and no
other database credential. The host files and the image move together, in this order:

- **The files before the image.** The new image's web entrypoint runs no deploy, and only the new
  compose files have the migrate service that runs one.
- **Today's image cannot run under the new files.** Their migrate service runs
  `scripts/owner_entrypoint.sh`, which today's image does not hold. `shekel-deploy`'s pre-flight
  asks, and its dry run prints `CANNOT run under the compose files in place`: expected here, and the
  reason a failed deploy is not re-pinned. The script refuses instead, leaves the pin at the new
  digest and names the way back, step 4.
- **The database moves once, and needs nothing to go back.** The provisioning script makes
  `shekel_owner` and hands it every object outside the system catalogs. It changes no row and keeps
  each grant, re-made in the owner's name, so today's app goes on serving through it; and today's
  image, which deploys as the superuser, still reaches every table, whoever owns it.

**1. Host steps**, before `shekel-deploy`, from the main checkout at `main`:

```bash
cd ~/projects/Shekel && bash scripts/reconcile_prod_to_canonical.sh; echo "exit=$?"
```

This is 2.5's script, run unchanged. Against today's host it makes `secrets/owner_role_password` (a
new password, never printed), sets each secret file's mode for its readers (`app_role_password` from
0600 to 0644, as the database container now reads it too), rewrites `.env` with
`OWNER_ROLE_PASSWORD=replaced_by_docker_secret` added and the pin and the settings it carries
unchanged (it carries a fixed list, which held every variable production's `.env` held on
2026-11-13), and copies from `main` both compose files, `deploy/valkey/` and
`deploy/postgres/provision_roles.sh` (0755), and the shared Nginx files where they differ. This card
changes `nginx.conf` and `users.acl` by one comment line each, the runbook path they name, so
neither Nginx nor Valkey needs a reload for it. The script prints its snapshot directory
(`snapshot saved to /opt/docker/shekel/.reconcile-snapshot-<ts>/`) and the old `.env`'s copy
(`backed up to /opt/docker/shekel/.env.bak.<ts>`): keep both paths for step 4.

Exit 0 goes on to step 2. Any other exit is a STOP: the script stops at the step that failed and
prints what it changed before it, and no container has changed.
**Do not run the recreate its closing text prints**: on today's pin, that `up -d` starts the migrate
service from today's image, which cannot run it, and leaves nothing serving.

**2. The database**, in one call:

```bash
cd /opt/docker/shekel && docker compose up -d --wait db \
    && docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh; echo "exit=$?"
```

Compose makes the database container again with its new mounts (the provisioning script, the two
logins' password files) and no other: the running app, today's image, reconnects once the database
is back. The script then makes `shekel_owner` and hands it every object in one transaction, ending
`NOTICE: shekel_owner and shekel_app provisioned; <n> object(s) handed to shekel_owner.` On
2026-11-13 production held no kind of object the script refuses (a view, a free-standing sequence, a
type of its own): every sequence belonged to a column (read-only census). Exit 0 goes on to step
3. Any other exit is a STOP: a refusal names the object and changes nothing, and production is
still today's image on a database it reaches.

**3. Deploy and verify**, as every release does (`/release` step 11). `shekel-deploy` pins the new
image and runs `docker compose up -d app`: the migrate service runs the deploy as the owner and
exits 0, and only then does the app start, holding the web login's password alone. Exit 0 and
`VERIFY OK` go on to step 5. A failure goes to step 4.

**4. Rollback**, when step 3 fails. `shekel-deploy` refuses to re-pin, leaves the pin at the new
digest, and says what the re-read stamp needs: `it needs no restore`, or
`the dump must be restored before it starts` when the new image's deploy committed its migrations
and something after them failed.

When the dump must be restored, run 2.3's restore through its step 4 first, while the database
container is still the new files' (2.3's step 3 runs the provisioning script, which only that
container holds). Then, in place of 2.3's step 5, and directly when no restore is needed, with step
1's two paths:

```bash
P=/opt/docker/shekel; S=<step 1's snapshot directory>; B=<step 1's .env copy>
[ -s "$B" ] && [ -s "$S/docker-compose.yml.pre-reconcile" ] && [ -s "$S/docker-compose.override.yml.pre-reconcile" ] \
    && cp -p "$B" "$P/.env" \
    && cp -p "$S/docker-compose.yml.pre-reconcile" "$P/docker-compose.yml" \
    && cp -p "$S/docker-compose.override.yml.pre-reconcile" "$P/docker-compose.override.yml" \
    && cp -p "$S/valkey.pre-reconcile/"* "$P/deploy/valkey/" \
    && (cd "$P" && docker compose config -q && docker compose up -d --wait --remove-orphans); echo "exit=$?"
```

It puts back today's pin, both compose files and the Valkey copy, makes the database container again
without the new mounts and the app on today's image, whose own start deploys as the superuser, and
removes the migrate container, an orphan to today's files. Then `verify_prod_deploy.sh` must print
`VERIFY OK`. The database needs no change: `shekel_owner` keeps every object, and the superuser
reaches them all. What step 1 added stays, as today's files read none of it: the owner's password
file, which the database's `shekel_owner` holds and a later run of step 1 keeps, and the
provisioning script's copy. A later deploy of today's image makes any new table the superuser's,
which a retry's step 2 hands to the owner. Production is then behind `main`, as after any
rolled-back release.

**5. After `VERIFY OK`, the nightly audit cleanup.** Its unit,
`/etc/systemd/system/shekel-audit-cleanup.service` (its reference copy beside the compose files),
runs the cleanup in the app service, where today's image ran it as the superuser. Under the new
files the app holds no owner login, and the cleanup exits 1 there, naming `DATABASE_URL`: move it to
the migrate service, then preview a run:

```bash
U=/etc/systemd/system/shekel-audit-cleanup.service; R=/opt/docker/shekel/shekel-audit-cleanup.service
OLD='--pull never app python scripts/audit_cleanup.py'; NEW='--pull never migrate python scripts/audit_cleanup.py'
sudo sed -i "s|$OLD|$NEW|" "$U" && sed -i "s|$OLD|$NEW|" "$R" \
    && [ "$(grep -c -- "$NEW" "$U")" = 1 ] && [ "$(grep -c -- "$NEW" "$R")" = 1 ] \
    && sudo systemctl daemon-reload \
    && (cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never migrate python scripts/audit_cleanup.py --dry-run); echo "exit=$?"
```

The reference copy is the operating user's, so it is edited without `sudo`, which would leave it
root's. The preview prints the count of rows the next run deletes and deletes none. Exit 0 ends the
cutover. The unit's comment still says the app's image loads the owner's URL; the line it runs is
what changed. Should production later go back to today's image, step 4 is joined by the same `sed`
the other way, `migrate` to `app`.

**The dev stack** needs the provisioning script once too, as the shared dev database has no owner
login; who runs it, and when, is question plan#3513's.

**Measured**, of the pieces, on throwaway stacks from this card's branch on the rootless daemon: the
provisioning script on a database the superuser owned (2026-11-12), a grant to the web login kept
through the hand-over (2026-11-13: the web login wrote a table the script had just handed to the
owner), the owner's audit cleanup and the web login's refusal of it (2026-11-12), and
`shekel-deploy`'s layout refusal (plan#3424). The blocks in sequence, from today's image and files
through step 4's way back, are rehearsed before this card's pull request (plan#3669).
