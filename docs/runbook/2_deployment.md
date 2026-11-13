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

### 2.2 Updating a Self-Hosted Install

A self-hosted install is the README's Quick Start: the image CI builds and signs on `main`, and the
configuration files of the commit that image was built from. A release changes both, so an update
takes both, by the README's Updating section. Migrations run on the new container's start. The
README's steps check no signature; `deploy/README.md`'s `cosign verify` command does.

### 2.3 Rolling Back

**In production, `shekel-deploy`'s re-read stamp decides.**

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
3. Migrates the four high-sensitivity secrets from the current `.env` into per-file entries under
   `secrets/` (mode 0600), and writes Valkey's password file `secrets/valkey_password` (mode 0640,
   for the valkey container's gid) from `.env`'s `SHEKEL_VALKEY_PASSWORD`
4. Rewrites `/opt/docker/shekel/.env` with `replaced_by_docker_secret` placeholders for the four
   secrets and `SHEKEL_VALKEY_PASSWORD`, plus `SHEKEL_IMAGE_DIGEST` and
   `REGISTRATION_ENABLED=false`. Backs up the old `.env` to `.env.bak.<timestamp>`.
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
      -c "grep -cE '^_load_secret (SECRET_KEY|POSTGRES_PASSWORD|APP_ROLE_PASSWORD|TOTP_ENCRYPTION_KEY|VALKEY_PASSWORD) ' /home/shekel/app/entrypoint.sh"
  # Output must be 5: one call for each secret the script turns into a placeholder.
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
docker logs shekel-prod-app 2>&1 | grep '^Loaded '            # → 5 lines, one per secret
curl -sI https://shekel.saltyreformed.com/login | head -5     # → 200 + sec headers
docker exec jellyfin bash -c 'getent hosts shekel-prod-app'   # → fails (isolation confirmed)
```

### 2.6 Host Steps a Release Carries

`shekel-deploy` changes one thing in production, the image pin, and its automatic rollback re-pins
the previous image onto whatever host files are in place. It copies none of 2.5's host copies and
writes nothing under `/opt/docker/shekel/secrets/`. A card whose change needs the host changed too
writes the steps here, one subsection naming the card, each with its rollback and what was measured;
`/release` runs them at its step 11 for each card the release carries, after the developer's "Logged
out, deploy now", from `~/projects/Shekel` at `main`. A step marked as coming before the merge into
`main` runs at `/release` step 5 instead. Once production runs a card, its subsection is history,
and the next change to this section deletes it.

#### The passkey origin (plan#2538)

The release carrying plan#2538 names the app's passkey origin, `PASSKEY_ORIGIN`, in
`deploy/docker-compose.prod.yml`, and `docker-compose.yml` passes a bundled install's through
(plan#3488). The new image's production configuration refuses to start without it, and
`shekel-deploy` copies neither file (2.5), so these steps copy both from `main`:

- The new image on the host's old files dies at entrypoint step 3, before its migrations commit
  (`ValueError: PASSKEY_ORIGIN is not set: ...`), and `shekel-deploy`'s rollback restores the old
  image, healthy.
- The old image reads no `PASSKEY_ORIGIN`, so it runs on the new files: `shekel-deploy`'s own
  rollback, which re-pins it onto them, needs nothing of these steps undone.
- The copy carries every change `main` made to the two files since the host last took them, not this
  card's alone. On 2026-11-13 that was also the database's and Valkey's images, which release
  2026-11-10-2 moved and nothing copied (finding plan#2567). Compose recreates a service whose
  settings moved, and `shekel-deploy`'s `up -d app` recreates the app's dependencies with it: a
  database that could not start on the new files there left the old app removed, the stamp
  unreadable and the pin on the new image (`REFUSING to roll back`). So step 1 restarts the database
  and Valkey on the new files itself, after a dump, and the deploy then changes the app alone
  (ruling plan#3743).

**Before the merge into `main`: the read-back.** It reads only and asks the developer nothing;
`/release` step 5 runs it for its approval question:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
: "${CUT:?}"
P=/opt/docker/shekel
for f in docker-compose.yml:docker-compose.yml \
         deploy/docker-compose.prod.yml:docker-compose.override.yml; do
    r=${f%%:*} h=$P/${f#*:}
    b=$(git hash-object "$h")
    o=$(git log --format=%h --find-object="$b" "$CUT" origin/main -- "$r" | tail -1)
    echo "== $h: the repository's from ${o:-NO COMMIT}"
    git show "$CUT:$r" | diff -u "$h" -
done
```

Each `==` line must name a commit, the one that wrote the version the host holds, so the copy loses
no change made on the host alone; `NO COMMIT` is a STOP (2.5: the host change may carry a fix the
repository lacks). `diff` exits 1 when the files differ, as they will. Its hunks are what step 1
changes on the host, for the approval question to name in plain words, and a moved `image:` line
names a service step 1 restarts on a new image.

**1. Host steps**, before `shekel-deploy`, in one call:

```bash
P=/opt/docker/shekel; Q=~/projects/Shekel
S="$P/.passkey-origin-$(date +%Y%m%d-%H%M%S)"
shekel-deploy --dump-only \
    && install -d -m 0700 "$S" \
    && cp -p "$P/docker-compose.yml" "$P/docker-compose.override.yml" "$S/" \
    && echo "snapshot=$S" \
    && install -m 0644 "$Q/docker-compose.yml" "$P/docker-compose.yml" \
    && install -m 0644 "$Q/deploy/docker-compose.prod.yml" "$P/docker-compose.override.yml" \
    && (cd "$P" && docker compose config -q \
        && docker compose config --format json | jq -er .services.app.environment.PASSKEY_ORIGIN \
        && docker compose up -d --wait db valkey); echo "exit=$?"
```

It dumps the database (2.1's `--dump-only`, its path printed last), snapshots both compose files
into `$S` (0700) and prints `snapshot=$S`, copies both in from `main`, has compose parse the result
and print the app's merged `PASSKEY_ORIGIN`, which must be `https://shekel.saltyreformed.com`, and
recreates the database and Valkey where their settings moved, returning once both are healthy. The
app is neither recreated nor restarted, and answers again once the database does. Exit 0 goes on to
step 2. Any other exit is a STOP: with no `snapshot=` line nothing was written; with one, step 3
puts the files back first.

**2. Deploy and verify**, as every release does (`/release` step 11). `shekel-deploy` recreates the
app alone.

**3. Rollback**, only for a step 1 that printed `snapshot=` and did not exit 0, so before
`shekel-deploy` has run. With step 1's snapshot path:

```bash
P=/opt/docker/shekel; S=<step 1's snapshot path>
[ -s "$S/docker-compose.yml" ] && [ -s "$S/docker-compose.override.yml" ] \
    && cp -p "$S/docker-compose.yml" "$S/docker-compose.override.yml" "$P/" \
    && (cd "$P" && docker compose config -q && docker compose up -d --wait db valkey); echo "exit=$?"
```

It puts both files back and recreates the database and Valkey on them, leaving the app and the pin
as step 1 found them. Then `verify_prod_deploy.sh`, with SINCE this block's start, must print
`VERIFY OK`.

After step 2 nothing of these steps is undone. `rollback healthy` leaves the old image on the new
files, which it runs on. `rollback REFUSED`, `revert compose up also failed`,
`rollback container also unhealthy`, or `VERIFY FAILED` after `deploy OK`, are the app's, as in any
release (2.3), and a STOP for the developer. No way out puts the old files back: the new image
refuses them, and the old image runs on the new ones.

**4. After step 2**, whatever it printed, or after step 3's `VERIFY OK`, the snapshot has no use, so
it goes: `rm -r <step 1's snapshot path>`.

**Measured 2026-11-13**, in two runs on a throwaway stack on the rootless daemon, the second running
every block as it stands: production's own two compose files (the host's copies, from 348c74a34 and
667f9bc10) with production's image `4f1980e01c65`, then this card's files and an image built from it
at `f30f00915`, both app images by digest from a local registry; `main`'s `shekel-deploy.sh` and
`verify_prod_deploy.sh`, which this card does not change, aimed at the stack by their own overrides;
the read-back run on the host's own files; blocks 1, 3 and 4 verbatim but for `P` and `Q` (and
`shekel-deploy` naming the script), run in zsh as `/release` runs them; and a third compose file
renaming the containers, the database volume and the shared network and pointing the secrets and the
app's registry at the rehearsal.

- The new image on the old files died at entrypoint step 3 with that `ValueError`, the stamp still
  `08fda2e0b18b`, and `shekel-deploy` printed `rollback healthy`; `VERIFY OK`.
- The read-back named 348c74a34 and 667f9bc10, and its hunks were the database's and Valkey's
  `image:` lines, the two `PASSKEY_ORIGIN` entries and their comments, and comment lines the
  runbook's split re-pointed. A copy of the override with one line added by hand read `NO COMMIT`.
- With one setting Postgres does not know added to the database's command in a copy of the new
  files, step 1 printed its dump, `snapshot=` and the origin, then exited 1 at
  `container ... is unhealthy`, the pin and the app container unchanged. Step 3 exited 0, the files
  byte for byte production's and both services on their old images again, the app the same
  container, and `VERIFY OK`; the app logged one failed health check while the database was down in
  the first run, none in the second. The same fault met by the deploy's `up -d app`, in the first
  run, recreated all three containers, left the app `Created` and not running, and ended
  `REFUSING to roll back` with the pin on the new image.
- Step 1 on the true files exited 0, printed `https://shekel.saltyreformed.com`, and recreated the
  database on `postgres:18-alpine@...10a5c69a8303` and Valkey on `9.1.3`; the old app kept its
  container, start time and restart count, and `VERIFY OK` counted no error.
- With a rehearsal-only empty `PASSKEY_ORIGIN`, the new image died at step 3, and `shekel-deploy`
  re-pinned the old image onto the new files, recreating the app alone: `rollback healthy`, then
  `VERIFY OK`. The old image recreated from the true new files held the origin in its environment
  and gave `VERIFY OK`.
- Step 2 on the true files gave `deploy OK` (the database and Valkey `Running`, the app recreated),
  `VERIFY OK`, the origin in the app's environment, the stamp at `1ea44adfeec8` and `auth.passkeys`
  in place. Step 4 removed each run's snapshot.

Where the rehearsal differs: its secrets were 0644, as the rootless daemon maps uids; its database
was a fresh one the old image's first boot made, not production's data; `shekel-deploy` ran with
`--no-verify`, as no local image carries a signature, a health wait of 150 seconds where
production's is 240, and its alerts off; its compose files were named by `COMPOSE_FILE`; and in the
second run its backend network took its own subnet, as another lane's stack held production's on
that daemon.
