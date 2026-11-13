# Deployment Configurations

This directory holds the version-controlled deployment configs for Shekel. Two deployment modes are
supported. Each mode has a distinct set of files in this directory; the wrong mode's files are inert
in the other mode.

| Mode | Reverse proxy | Scope | Active files |
|------|---------------|-------|--------------|
| Bundled | `shekel-prod-nginx` (in-stack) | Single-stack hosts, fresh-host bring-up, the README Quick Start | `nginx-bundled/nginx.conf`, `valkey/` |
| Shared | A separate Nginx managed outside this stack | Hosts that already run a homelab Nginx in front of multiple services | `nginx-shared/nginx.conf`, `nginx-shared/conf.d/shekel.conf`, `docker-compose.prod.yml`, `valkey/` |

The maintainer's production homelab runs **shared mode**. Generic deployments default to
**bundled mode**.

## Directory Layout

```text
deploy/
├── README.md                           This file.
├── nginx-bundled/
│   └── nginx.conf                      Mounted into the bundled
│                                       shekel-prod-nginx service by
│                                       the repo's docker-compose.yml.
├── nginx-shared/
│   ├── nginx.conf                      Mirror of /opt/docker/nginx/
│   │                                   nginx.conf on the homelab host.
│   └── conf.d/
│       └── shekel.conf                 Mirror of /opt/docker/nginx/
│                                       conf.d/shekel.conf (per-service
│                                       vhost included by the file
│                                       above).
├── postgres/
│   ├── provision_roles.sh              The database container's
│   │                                   first-boot script, mounted in
│   │                                   every compose file: makes the
│   │                                   owner and web logins and hands
│   │                                   the owner every object.  A
│   │                                   production host keeps a copy at
│   │                                   /opt/docker/shekel/deploy/
│   │                                   postgres/ (0755).
│   └── README.md                       The shared-mode TLS cert and key
│                                       kept beside it.
├── valkey/
│   ├── users.acl                       Valkey's ACL users and the
│   │                                   commands each may run, the one
│   │                                   home of those rules (ruling
│   │                                   plan#2350).
│   └── start.sh                        The valkey service's command in
│                                       every compose file: adds the
│                                       app user's password hash to the
│                                       rules and starts the server.
│                                       Both are mounted at
│                                       /etc/valkey; a production host
│                                       keeps a copy at /opt/docker/
│                                       shekel/deploy/valkey/.
└── docker-compose.prod.yml             Compose override that selects
                                        shared mode (joins the app to
                                        the dedicated shekel-frontend
                                        bridge and parks the bundled
                                        nginx in the "disabled"
                                        profile).
```

## Bundled Mode (default)

Used by the README Quick Start and by anyone bringing up Shekel on a host that does not already have
a reverse proxy.

```bash
cd /opt/shekel
docker compose up -d
```

The repo's `docker-compose.yml` mounts `deploy/nginx-bundled/nginx.conf` into the
`shekel-prod-nginx` container at `/etc/nginx/nginx.conf:ro`. Edits to that file take effect on the
next `docker compose up -d`.

To customize without forking, drop a sibling `docker-compose.override.yml` next to
`docker-compose.yml` and adjust the `nginx.volumes` block. Do not edit the repo file in place on the
host -- the repo file is the source of truth and is reset on every `git pull`.

## Shared Mode (homelab production)

Used when a separate, longer-lived Nginx already fronts other services on the host (Jellyfin, Unifi,
etc.). The shared Nginx is defined in a *different* compose file on the host
(`/opt/docker/docker-compose.yml`) and is *not* in this repo.

```bash
cd /opt/shekel
docker compose \
  -f docker-compose.yml \
  -f deploy/docker-compose.prod.yml \
  up -d
```

In shared mode:

- `deploy/docker-compose.prod.yml` joins `shekel-prod-app` to the external `shekel-frontend` bridge
  network (NOT the wider `homelab` network) and parks the bundled `shekel-prod-nginx` service in the
  `disabled` profile, so `docker compose up -d` starts only `db`, `valkey`, the one-shot `migrate`
  (the deploy, which exits) and `app`.
- The shared Nginx (managed under `/opt/docker/nginx/` on the host) proxies traffic to
  `shekel-prod-app:8000` over the `shekel-frontend` bridge, using the vhost in
  `deploy/nginx-shared/conf.d/shekel.conf`. Co-tenants like Jellyfin, Immich, and UniFi remain on
  `homelab` and cannot reach the Shekel app directly (audit findings F-020/F-129 closed in Commit
  C-33).
- `deploy/nginx-shared/nginx.conf` is the main config for the shared Nginx. The repo file is the
  source of truth; the host copy at `/opt/docker/nginx/nginx.conf` must match.

The `shekel-frontend` network must exist before `docker compose up`, with the subnet pinned so the
`FORWARDED_ALLOW_IPS` literal Gunicorn and the app read matches and Nginx's `set_real_ip_from`
directive applies:

```bash
docker network ls --filter name=shekel-frontend
# If missing:
docker network create shekel-frontend \
    --driver bridge \
    --subnet 172.32.0.0/24
```

The shared `/opt/docker/nginx` and `/opt/docker/cloudflared` containers must also join
`shekel-frontend` (in addition to the `homelab` network they already use). Edit
`/opt/docker/docker-compose.yml` and add the service-level `networks:` and the file-level
`networks: shekel-frontend: external: true` block, then `docker compose up -d nginx cloudflared` to
recreate them. The detailed step list is in `deploy/docker-compose.prod.yml` under "OPERATOR
PRE-FLIGHT".

`/opt/docker/cloudflared/config.yml` must also be updated so the Shekel ingress rule routes through
`http://nginx:80` instead of straight at `http://shekel-prod-app:8000` (audit finding F-063 closed
in C-33). See the bundled-mode `cloudflared/config.yml` template for the example shared-mode rule.

## Sync Procedure (Shared Mode)

The shared-mode files in this directory must stay in step with the host. The repo is the source of
truth.

1. Make the change in the repo on the `dev` branch and commit.
2. On the host, pull the change and copy each updated file to its runtime path:

   ```bash
   cd /opt/shekel
   git pull --ff-only
   sudo cp deploy/nginx-shared/nginx.conf            /opt/docker/nginx/nginx.conf
   sudo cp deploy/nginx-shared/conf.d/shekel.conf    /opt/docker/nginx/conf.d/shekel.conf
   sudo cp docker-compose.yml                        /opt/docker/shekel/docker-compose.yml
   sudo cp deploy/docker-compose.prod.yml            /opt/docker/shekel/docker-compose.override.yml
   sudo cp deploy/valkey/users.acl deploy/valkey/start.sh /opt/docker/shekel/deploy/valkey/
   sudo cp deploy/postgres/provision_roles.sh        /opt/docker/shekel/deploy/postgres/provision_roles.sh
   ```

   `cp` onto the file that is there keeps its mode (0755, which the database image needs to run it)
   and the file the running `db` container mounted, so a changed `provision_roles.sh` is the one its
   next run by hand there uses (`docs/runbook_secrets.md`, "Rotating APP_ROLE_PASSWORD and
   OWNER_ROLE_PASSWORD").
3. Validate the Nginx config before reloading:

   ```bash
   sudo docker exec nginx nginx -t
   ```

4. Reload Nginx without dropping connections:

   ```bash
   sudo docker exec nginx nginx -s reload
   ```

5. If a compose file or `deploy/valkey/` changed, recreate the affected containers. Valkey reads its
   rules when it starts, and a rules change alters no compose setting, so restart it by name:

   ```bash
   cd /opt/docker/shekel
   docker compose up -d
   docker compose up -d --force-recreate valkey   # when deploy/valkey/ changed
   ```

A change that needs more than a copy (a renamed `.env` variable, a new file under `secrets/`)
carries host steps of its own, in `docs/runbook/2_deployment.md` 2.6, which `/release` runs.

See `docs/runbook/2_deployment.md` -> "Shared-mode deployment" for full details including the
rollback procedure and the planned `scripts/config_audit.py` drift check.

## Image Digest Pinning (Commit C-36)

Audit findings F-060 (`:latest` lets a single registry push silently swap the running image) and
F-155 (no Cosign / image-signature verification) are closed by pinning the production image to an
immutable `@sha256:<digest>` reference and verifying the signature before deploy.

### Where the pin lives

`deploy/docker-compose.prod.yml` overrides the base file's
`image: ghcr.io/saltyreformed-labs/shekel:latest` with:

```yaml
image: ghcr.io/saltyreformed-labs/shekel@${SHEKEL_IMAGE_DIGEST:?...}
```

The `:?` syntax FAILS the compose parse loudly when `SHEKEL_IMAGE_DIGEST` is unset, so a deployment
cannot accidentally fall back to `:latest`. The host's `.env` (under `/opt/docker/shekel/.env` in
the maintainer's homelab layout) supplies the value.

### Updating the digest

1. Build and push a new image. CI (`.github/workflows/docker-publish.yml`) runs on every push to
   `main` and prints the digest in the "Image digest" workflow step output.

2. Verify the signature on the new digest before pinning it. CI signs keyless, through sigstore's
   OIDC flow, so there is no key to keep:

   ```bash
   cosign verify \
       --certificate-identity-regexp \
       '^https://github\.com/saltyreformed-labs/Shekel/\.github/workflows/docker-publish\.yml@refs/heads/main$' \
       --certificate-oidc-issuer https://token.actions.githubusercontent.com \
       ghcr.io/saltyreformed-labs/shekel@sha256:<digest>
   ```

3. Once the verify succeeds, edit the host `.env`:

   ```bash
   sudo nano /opt/docker/shekel/.env
   # Set: SHEKEL_IMAGE_DIGEST=sha256:abc123...
   ```

4. Roll the app container to the new digest:

   ```bash
   cd /opt/docker/shekel
   docker compose pull app
   docker compose up -d app
   ```

5. Confirm the running image matches the pin:

   ```bash
   docker inspect shekel-prod-app --format '{{.Image}}'
   # Output digest must equal the value pinned in .env.
   ```

### Rollback

Restore the previous digest by editing `.env` and running steps 4-5 above. Because every successful
deploy records the digest in the host's `.env`, the on-host file is itself the rollback log; commit
your `.env` changes to a private operator-only repo if you need an audit trail.

## See Also

- `docs/runbook.md` -- full operational runbook (deploy, restart, rollback, troubleshooting).
- `scripts/config_audit.py` -- skeleton drift-check script that will compare the on-host runtime
  configs against the repo copies.
- `shekel-audits/security-2026-04-15/scans/` -- audit-window snapshots of the on-host configs,
  retained as historical evidence and to verify that the runtime config has not drifted further
  since the audit.
