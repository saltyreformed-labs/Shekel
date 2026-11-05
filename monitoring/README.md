# Shekel Log Monitoring

## Overview

Shekel emits structured JSON logs to stdout (see `app/logging_config.py` and
`docs/observability.md`). Docker's `json-file` driver captures the stream; a host-level collector
ships it to Loki for querying in Grafana.

## Architecture (deployed)

```text
Shekel App (JSON stdout) -> Docker json-file driver -> Grafana Alloy -> Loki -> Grafana
```

The collector stack (Alloy + Loki + Grafana + Prometheus) is part of the host-level compose project
at `/opt/docker/monitoring/` on the deployment host -- it is operator infrastructure shared by every
service on the machine, not part of this repository. Alloy discovers containers over the Docker
socket (via a socket proxy) and ships each container's log stream to Loki; no shared "monitoring"
network and no per-app scrape config are required on the Shekel side.

Historical note: an earlier iteration of this document described a Promtail-based pipeline that
required attaching the app container to a shared `monitoring` network. That pipeline was replaced by
Alloy; the app-side network coupling was removed from `docker-compose.build.yml` in the same cleanup
(parity audit 2026-06-12, finding M06 in `shekel-audits/dev-prod-parity/findings.md`).

## What the Shekel side guarantees

- Every log line on stdout is single-line JSON with `timestamp`, `level`, `logger`, `message`, and
  `request_id` fields; domain events add `event`, `category`, and `user_id` (see `log_event()`
  usage).
- Gunicorn's native access log is disabled (`accesslog = None` in `gunicorn.conf.py`); Flask's
  `_log_request_summary()` emits the JSON request log instead.
- Container logs rotate locally (json-file, 10 MiB x 5 for the app) so the host disk cannot fill;
  Loki holds the long-term record.

## Useful LogQL Queries

The examples assume the collector labels each stream with the Docker container name (Alloy's
`discovery.docker` default); the production container is `shekel-prod-app`.

| Purpose | Query |
|---------|-------|
| All auth events | `{container="shekel-prod-app"} \| json \| category="auth"` |
| Login failures | `{container="shekel-prod-app"} \| json \| event="login_failed"` |
| Slow requests | `{container="shekel-prod-app"} \| json \| event="slow_request"` |
| All errors | `{container="shekel-prod-app"} \| json \| level="ERROR"` |
| Specific user | `{container="shekel-prod-app"} \| json \| user_id="1"` |
| Trace a request | `{container="shekel-prod-app"} \| json \| request_id="<uuid>"` |
| Password changes | `{container="shekel-prod-app"} \| json \| event="password_changed"` |
| MFA events | `{container="shekel-prod-app"} \| json \| event=~"mfa_.*"` |
| Business events | `{container="shekel-prod-app"} \| json \| category="business"` |

## Audit Log Retention

`scripts/audit_cleanup.py` deletes `system.audit_log` rows older than `AUDIT_RETENTION_DAYS`
(default 365). It is NOT run automatically by the app -- the operator must schedule it. The deployed
schedule is the `shekel-audit-cleanup.timer` systemd unit (reference copies in
`/opt/docker/shekel/`, daily at 03:30, deliberately after the backup window so pruned rows are
always in that night's snapshots), whose service runs:

```bash
cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never \
    app python scripts/audit_cleanup.py
```

`docker compose run` matters: the entrypoint loads the docker secrets and rebuilds the owner-role
`DATABASE_URL` first. A bare `docker exec shekel-prod-app python scripts/...` does NOT work
post-C-38 -- exec'd processes get the container's stored placeholder env, not the secret values the
entrypoint loaded (and the least-privilege role deliberately cannot DELETE audit rows).

To preview what would be deleted without actually deleting:

```bash
cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never \
    app python scripts/audit_cleanup.py --dry-run
```

## Troubleshooting

- **No logs in Grafana:** Check that `docker logs shekel-prod-app` shows JSON output, then check the
  collector's own logs in the `/opt/docker/monitoring` stack.
- **Gunicorn access logs appearing:** `gunicorn.conf.py` disables the native access log
  (`accesslog = None`); if raw access lines show up, that config is no longer being loaded.
