---
paths:
  - "deploy/**/*"
  - "docker-compose*"
  - "Dockerfile"
---

# Deployment and compose rules

Docker container (Gunicorn + Nginx + Cloudflare Tunnel) on bare-metal Arch Linux.
No Ubuntu packages, no exposed ports, no systemd. `.env` config: `DATABASE_URL`,
`SECRET_KEY`, `FIELD_ENCRYPTION_KEY`.

## Compose conventions (use these on every new service)

- **Resource caps:** `deploy.resources.limits: { cpus, memory, pids }` plus
  `reservations` for long-running services. Do not use the legacy
  `mem_limit`/`pids_limit`/`cpus` top-level keys.
- **Image pinning:** `image: name:tag@sha256:<digest>`. The tag is human-readable;
  the digest is immutable. For production enforcement, use `${VAR:?msg}`
  interpolation so a missing digest fails the compose parse loud.
- **Hardening defaults:** `security_opt: [no-new-privileges:true]`,
  `cap_drop: [ALL]`, `read_only: true`, non-root `user:`, `tmpfs:` for any path
  the process writes. Add caps back one at a time with a comment explaining the
  specific entrypoint step that needs them.
- **Docker secrets (compose v2, non-Swarm):** `uid:`/`gid:`/`mode:` in the secret
  reference are silently ignored; the container sees the HOST file's ownership and
  mode. If the process runs as uid X, the host file must be readable by uid X --
  chown to X (sudo) or `chmod 0644` and rely on directory containment (mode 0700
  on `secrets/`).
- **Networks:** pin subnets explicitly (`ipam.config.subnet`) for any bridge that
  `FORWARDED_ALLOW_IPS` or `set_real_ip_from` reference -- otherwise an
  auto-assigned subnet silently drifts on recreate and the trust boundary breaks.
- **External named volumes:** `external: true` on the pgdata volume (or any
  irreplaceable state) so `docker compose down -v` cannot destroy it.
- **`name:` field at top of file:** explicit project name. Defaults to the
  directory basename otherwise, which is brittle.

## Prod-override sync

When editing the shared-mode override `deploy/docker-compose.prod.yml` and the
runtime copy at `/opt/docker/shekel/docker-compose.override.yml`, they MUST match.
`scripts/reconcile_prod_to_canonical.sh` is the one-shot sync. The
`deploy/nginx-shared/nginx.conf` has historically drifted behind the on-host file;
before any repo->host sync, diff the host file and back-port any host-only
hardening the repo is missing.
