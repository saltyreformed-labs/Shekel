# Shekel Operations Runbook: Monitoring & Observability

Section 5 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

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

The stack was built to the plan in `observability.md` (2026-05-06). The configuration as it runs is
`/opt/docker/monitoring/docker-compose.yml` and `/opt/docker/monitoring/alloy/config/config.alloy`
on the host, which have moved on since: each public app has its own edge bridge, and cloudflared has
`edge-tunnel` (plan#2181). This runbook section assumes Phase 0 -- 5 of that plan are complete.

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

The Loki / Grafana / Alloy stack runs from `/opt/docker/monitoring/`, built to the
`observability.md` plan. The Shekel stack does NOT need to share a Docker network with it -- Alloy
reads container logs via the docker socket, which works regardless of the source container's network
membership.

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
- **`shekel-deploy`:** After each deployment, by the container's health status (240s timeout, 5s
  interval)
