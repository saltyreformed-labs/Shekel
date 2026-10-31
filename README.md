# Shekel -- Personal Budget App

A paycheck-centric budget application that replaces a biweekly-paycheck-based spreadsheet. Organizes
finances around **pay periods** rather than calendar months, mapping every expense to a specific
paycheck and projecting balances forward over a ~2-year horizon.

**Stack:** Flask - Jinja2 - HTMX - Bootstrap 5 - PostgreSQL

**Two ways to run Shekel:**

- **Docker (recommended):** Download two files, create a volume, run `docker compose up`. No Python
  or PostgreSQL install needed. See [Quick Start (Docker)](#quick-start-docker).
- **From source:** Clone the repo, set up Python, use Docker for databases. See
  [Developer Setup (from source)](#developer-setup-from-source).

---

## Quick Start (Docker)

### 1. Prerequisites

Install [Docker Engine](https://docs.docker.com/engine/install/) (Linux) or Docker Desktop
(macOS/Windows). Verify the installation:

```bash
docker compose version
```

### 2. Download Configuration Files

```bash
mkdir -p shekel/deploy/nginx-bundled && cd shekel
curl -O https://raw.githubusercontent.com/saltyreformed-labs/Shekel/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/saltyreformed-labs/Shekel/main/.env.example
curl -o deploy/nginx-bundled/nginx.conf https://raw.githubusercontent.com/saltyreformed-labs/Shekel/main/deploy/nginx-bundled/nginx.conf
cp .env.example .env
```

### 3. Configure Environment

Edit `.env` and set these values:

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | Yes | Choose a strong database password. |
| `SECRET_KEY` | Yes | Run `openssl rand -hex 32` and paste the output. |
| `TOTP_ENCRYPTION_KEY` | No | Required before enabling MFA/TOTP. See [MFA Setup](#mfa-setup). |
| `REGISTRATION_ENABLED` | No | Set to `true` to enable the `/register` endpoint. Default in production: `false` (see [Security](#security)). |
| `SEED_USER_EMAIL` | No | Login email. Default: `admin@shekel.local`. **Remove from `.env` after the first successful boot** (see [Security](#security)). |
| `SEED_USER_PASSWORD` | No | Login password (min 12 characters). Default: `ChangeMe!2026`. **Remove from `.env` after the first successful boot** (see [Security](#security)). |
| `SEED_USER_LAST_PAYDAY` | Only with `SEED_USER_EMAIL` | The day the seeded owner was last paid, ISO `YYYY-MM-DD`. No default -- Shekel budgets by paycheck and will not invent one. Must fall within one pay cadence of the first boot. |
| `SEED_USER_CADENCE_DAYS` | No | Days between the seeded owner's paydays. Default: `14`. |
| `SEED_USER_NUM_PERIODS` | No | How many pay periods to generate ahead. Default: `52`. |

### 4. Create the Database Volume and Start

```bash
# One-time setup: create the database volume.
# This volume is external -- "docker compose down -v" will NOT delete it.
# Your financial data is safe from accidental cleanup commands.
docker volume create shekel-prod-pgdata

docker compose up -d
```

First startup takes 1-2 minutes while the database is initialized. Check progress with:

```bash
docker compose logs -f app
```

Look for `=== Starting Application ===` to confirm the app is running.

### 5. Log In

Open <http://localhost> and log in with the credentials from your `.env` file (or the defaults
above).

### 6. First-Time Setup in the App

After logging in you will see a **Welcome to Shekel!** banner with a setup checklist. Your account,
budget categories and pay periods are already created: the pay periods come from the payday and
cadence given at registration (`SEED_USER_LAST_PAYDAY` and `SEED_USER_CADENCE_DAYS` for the seeded
owner). Complete the remaining steps:

1. **Set Up a Salary Profile** -- Go to Salary and create your income profile with deductions and
   tax info.
2. **Create Recurring Transactions** -- Go to Templates and add your regular income and expenses
   with their recurrence patterns.

Once both steps are done, the welcome banner dismisses and the budget grid populates with your
projected transactions. You can then set your **anchor balance** (click the balance display on the
grid) to calibrate projections against your real checking account balance.

### Updating

```bash
docker compose pull && docker compose up -d
```

Database migrations run automatically on startup.

### Stopping and Removing

```bash
# Stop containers (data is preserved in the external volume):
docker compose down

# Stop containers and remove logs/static volumes (database is safe):
docker compose down -v

# To permanently delete ALL data including the database:
docker compose down -v
docker volume rm shekel-prod-pgdata
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `POSTGRES_PASSWORD` error on startup | Set `POSTGRES_PASSWORD` in your `.env` file. |
| `SECRET_KEY` error on startup | Set `SECRET_KEY` in your `.env` file. Run `openssl rand -hex 32` to generate one. |
| `shekel-prod-pgdata ... not found` on first run | Run `docker volume create shekel-prod-pgdata` before `docker compose up`. |
| MFA enable fails with "TOTP_ENCRYPTION_KEY" message | Set `TOTP_ENCRYPTION_KEY` in `.env`. See [MFA Setup](#mfa-setup) for generation instructions. |
| `/register` returns 404 | `REGISTRATION_ENABLED` is `false` (the production default). Set `REGISTRATION_ENABLED=true` in `.env` to re-enable. |
| Nginx fails with "mount ... not a directory" | The `deploy/nginx-bundled/nginx.conf` file is missing. Re-run the download step: `mkdir -p deploy/nginx-bundled && curl -o deploy/nginx-bundled/nginx.conf https://raw.githubusercontent.com/saltyreformed-labs/Shekel/main/deploy/nginx-bundled/nginx.conf` |
| App does not start or shows blank page | Run `docker compose logs app` and check for error messages. |
| Container keeps restarting | Run `docker compose logs app` -- a missing required variable or database connection issue is the most common cause. |
| Container marked unhealthy during first startup | First-time initialization (schema creation, migrations, seeding) can take over 60 seconds. The healthcheck `start_period` allows 120 seconds before failures count. Wait and check `docker compose logs -f app`. |
| `curl` not found inside the container | The slim image does not include curl. Use Python instead: `docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"` |
| `Can't locate revision identified by ...` | The GHCR image may be older than your database. Rebuild from source (`docker compose build`) or pull the latest image (`docker compose pull`). |

### Deployment Architecture

Shekel supports two deployment modes. Both are version-controlled under `deploy/`. Pick the one that
matches your host.

| Mode | When to use | Reverse proxy | Active files |
|------|-------------|---------------|--------------|
| **Bundled** (default) | The host has no other reverse proxy. The Quick Start above runs in this mode. | `shekel-prod-nginx` (in this stack) | `deploy/nginx-bundled/nginx.conf` |
| **Shared** | The host already runs a central Nginx (or Traefik/Caddy) in front of multiple services. | A separate Nginx managed outside this stack | `deploy/nginx-shared/nginx.conf`, `deploy/nginx-shared/conf.d/shekel.conf`, `deploy/docker-compose.prod.yml` |

In bundled mode the repo's `docker-compose.yml` mounts `deploy/nginx-bundled/nginx.conf` into the
bundled `shekel-prod-nginx` container. Nothing under `deploy/nginx-shared/` is read.

In shared mode the bundled `shekel-prod-nginx` is parked in the `disabled` profile by
`deploy/docker-compose.prod.yml`, so only `db`, `valkey`, and `app` start. The shared Nginx (defined
and managed outside this stack) reaches the app over an external `homelab` Docker network.

For full details and the file layout under `deploy/`, see [`deploy/README.md`](deploy/README.md).

### Deploying Behind an Existing Reverse Proxy (Shared Mode)

If you already run a central Nginx (or Traefik/Caddy) on your Docker host, use shared mode. The
compose override and shared Nginx files are checked into `deploy/` so disaster recovery is a
`git clone` away.

**1. Use the version-controlled compose override.** Either invoke compose with both files
explicitly:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/docker-compose.prod.yml \
  up -d
```

Or symlink/copy `deploy/docker-compose.prod.yml` to `docker-compose.override.yml` in the same
directory as `docker-compose.yml` and let compose auto-load it:

```bash
cp deploy/docker-compose.prod.yml docker-compose.override.yml
docker compose up -d
```

The override joins the app container to an external `homelab` network and parks the bundled Nginx
service in the `disabled` profile. Replace `homelab` in `deploy/docker-compose.prod.yml` if your
shared network has a different name.

**2. Add a server block to your central Nginx.** A reference vhost is in
`deploy/nginx-shared/conf.d/shekel.conf`. The minimal version is:

```nginx
server {
    listen 80;
    server_name shekel.example.com;   # your domain or LAN hostname

    location / {
        proxy_pass         http://shekel-prod-app:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120;
    }

    location /static/ {
        # Optional: serve static files directly if you mount the
        # static_files volume into your Nginx container.
        # Otherwise, Gunicorn serves them (slightly slower but simpler).
        proxy_pass http://shekel-prod-app:8000;
    }
}
```

**3. Start and verify:**

```bash
# (using whichever invocation you chose in step 1)
docker compose up -d

# Confirm the app container joined the shared network:
docker network inspect homelab --format '{{range .Containers}}{{.Name}} {{end}}'
# Should include "shekel-prod-app"

# Test the health endpoint from inside the container:
docker compose exec app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```

### Post-Deploy Verification Checklist

After any deployment or update, verify:

```bash
# 1. All containers are healthy
docker compose ps

# 2. Health endpoint responds
docker compose exec app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```

---

## Backups

Shekel stores all financial data in a PostgreSQL Docker volume.
**If this volume is lost, corrupted, or the host fails, your data is gone.** Set up automated
backups before entering real financial data.

See [docs/backup_runbook.md](docs/backup_runbook.md) for complete instructions covering:

- Automated daily backups via `pg_dump` with configurable retention
- Off-site backup to NAS or remote storage
- Backup encryption with GPG
- Restore procedures and verification

### Quick Backup Setup

```bash
# Run a manual backup now
./scripts/backup.sh

# Add to crontab for daily automated backups (2:00 AM)
crontab -e
# Add: 0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
```

See the runbook for retention policies, NAS configuration, and encryption setup.

---

## Security

### LAN-Only Deployment

If Shekel is only accessible on your local network, the default configuration is sufficient. You
should still:

- Change the default seed password on first login (Settings > Security > Change Password).
- Set up automated backups (see [Backups](#backups)).

### External Access (Cloudflare Tunnel, Tailscale, etc.)

If you expose Shekel outside your local network, take these additional steps:

1. **Verify public registration is disabled.** `REGISTRATION_ENABLED` defaults to `false` in
   production (set in `docker-compose.yml` and enforced by `ProdConfig`). Confirm with
   `docker exec shekel-prod-app env | grep REGISTRATION_ENABLED` -- the value should be `false`.
2. **Enable MFA for all users.** Go to Settings > Security > Enable TOTP. This requires
   `TOTP_ENCRYPTION_KEY` to be set (see [MFA Setup](#mfa-setup) below).
3. **Verify HTTPS.** Cloudflare Tunnel and Tailscale handle TLS automatically. If using a different
   method, ensure your reverse proxy terminates HTTPS.
4. **Change the default seed password immediately** if you used the default `ChangeMe!2026`.
5. **Scrub seed credentials from `.env` after the first successful boot.** Remove the
   `SEED_USER_EMAIL` and `SEED_USER_PASSWORD` lines from `.env`, then run
   `docker compose up -d --force-recreate app` so Docker's stored `Container.Config.Env` no longer
   carries the password. The seed user already exists in the database; the env values are no longer
   needed. Verify with `docker exec shekel-prod-app env | grep -c SEED_USER_PASSWORD` -- the count
   should be `0`.

### General Recommendations

- Back up your database before entering real financial data. See [Backups](#backups).
- Keep your `.env` file secure. It contains your database password and encryption keys. Never commit
  it to version control.
- The application sets security headers (CSP, HSTS-ready, X-Frame-Options) automatically in
  production mode.

### MFA Setup

Multi-factor authentication (TOTP) requires an encryption key for storing secrets at rest.

Generate a key using one of these methods:

```bash
# Using the Shekel Docker container (recommended):
docker exec shekel-prod-app python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Using a local Python environment with cryptography installed:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output into your `.env` file as `TOTP_ENCRYPTION_KEY=<key>`, then restart the app:

```bash
docker compose restart app
```

You can then enable MFA in Settings > Security > Enable TOTP.

**Do not use `openssl rand` as a substitute.** It produces an incompatible key format. Only the
Fernet method above generates a valid key.

---

## Developer Setup (from source)

For contributing to Shekel or running from source. Uses Docker for databases and the host for the
Python application.

### 1. Prerequisites

- **Docker Engine** -- provides the dev and test PostgreSQL databases
- **uv** -- installs the Python `.python-version` names and every package `uv.lock` pins

```bash
docker compose version
uv --version
```

### 2. Clone & Set Up Python Environment

```bash
cd ~/projects  # or wherever you keep code
git clone https://github.com/saltyreformed-labs/Shekel.git
cd Shekel

# Create .venv and install exactly what uv.lock pins: the runtime packages plus
# the test, lint and build tools (no new package without the developer's approval)
uv sync --locked
source .venv/bin/activate
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```text
SECRET_KEY=<any random string for dev>
```

The default `DATABASE_URL` in `.env.example` points to `localhost:5432`, which matches the dev
Docker database started in the next step. There is no `TEST_DATABASE_URL` to set:
`./scripts/test.sh` exports the test DSNs at the throwaway cluster it starts for each run.

### 4. Start the Dev Database

```bash
docker compose -f docker-compose.dev.yml up -d db
```

This starts one PostgreSQL container:

- `shekel-dev-db` on port 5432 (development database)

**There is no test database to start.** `./scripts/test.sh` creates a throwaway postgres container
of its own for every run and removes it afterwards, so the suite needs nothing running beforehand.

**Important:** This container uses project name `shekel-dev` and is fully isolated from production.
Running `docker compose down -v` from the production directory cannot affect them, and vice versa.

### 5. Initialize the Database

```bash
# Apply all migrations
flask db upgrade

# Seed reference data and the initial user
python scripts/seed_ref_tables.py
python scripts/seed_user.py
```

### 6. Run the App

```bash
flask run
# or
python run.py
```

Open <http://localhost:5000> (development server) and log in with the seed user credentials, or
register a new account at <http://localhost:5000/register>.

- **Default Email:** `admin@shekel.local`
- **Default Password:** `ChangeMe!2026`

### 7. First-Time Setup in the App

See [Quick Start (Docker) > First-Time Setup in the App](#6-first-time-setup-in-the-app) for initial
configuration steps.

---

## Development

### Running Tests

Run the suite through `./scripts/test.sh`, never bare `pytest`. The wrapper starts a throwaway
PostgreSQL container for the run -- the test template baked into it -- exports the test DSNs at its
unix socket, and removes it afterwards. A bare `pytest` has no database to reach.

```bash
# Run all tests (the full suite is minutes, not seconds)
./scripts/test.sh

# Run with coverage
./scripts/test.sh --cov=app --cov-report=term-missing

# Run specific test files (fast feedback during development)
./scripts/test.sh tests/test_services/test_cash_fold.py -v
./scripts/test.sh tests/test_routes/test_grid/test_view.py -v
```

**Prerequisite: a rootless Docker daemon.** The wrapper refuses a non-rootless one rather than
spawning a container per run on the daemon that serves production, so without it every invocation
exits 2 with instructions. Start it with `systemctl --user start docker.service` and
`loginctl enable-linger $USER`; the full host setup is in `docs/test-harness-isolation.md`.

Do not wrap the wrapper in `timeout`: it forwards INT, TERM and HUP to pytest and tears its
container down, and an outer `timeout`'s SIGKILL escalation defeats both.

### Database Migrations

```bash
# After changing models, generate a new migration
flask db migrate -m "describe what changed"

# Apply migrations
flask db upgrade

# Rollback one migration
flask db downgrade
```

### Linting

```bash
# Python (Pylint)
pylint app/
```

---

## Payday Workflow

The core interaction loop the app supports:

1. **Open the app** -- grid loads with current period as leftmost column.
2. **True-up balance** -- click the anchor balance, enter real checking balance.
3. **Mark paycheck received** -- click income row, mark as received, enter actual.
4. **Carry forward unpaid** -- click "Carry Fwd" on past periods with unpaid items.
5. **Mark cleared expenses** -- set done with actual amounts as they post.
6. **Mark credit card** -- expenses charged to CC become payback items next period.
7. **Check projections** -- scan future balances for any danger zones.

---

## Build Status

Last evaluated: 2026-03-23

| Phase                    | Name                           | Status      | Notes                                               |
| ------------------------ | ------------------------------ | ----------- | --------------------------------------------------- |
| 1                        | Replace the Spreadsheet        | Complete    | Grid, templates, recurrence, balance, status        |
| 2                        | Paycheck Calculator            | Complete    | Salary, raises, deductions, federal/state/FICA      |
| 3                        | HYSA & Accounts Reorganization | Complete    | HYSA interest, category grouping, account dashboard |
| 4                        | Debt Accounts                  | Complete    | Mortgage (fixed+ARM), auto loan, escrow, payoff     |
| 5                        | Investments & Retirement       | Complete    | 401(k), IRA, pension, growth engine, gap analysis   |
| 6                        | Visualization                  | Complete    | Charts dashboard, interactive sliders, theming      |
| 7                        | Scenarios                      | Deferred    | Model stub exists; clone/compare not built          |
| 8A                       | Security Hardening             | Complete    | MFA/TOTP, rate limiting, session mgmt, CSP          |
| 8B                       | Audit & Structured Logging     | Complete    | PG triggers on 22 tables, JSON logs, Promtail       |
| 8C                       | Backups & Disaster Recovery    | Complete    | pg_dump, retention, restore, integrity checks       |
| 8D                       | Production Deployment          | Complete    | Docker, Nginx, Cloudflare Tunnel, CI, deploy.sh     |
| 8E                       | Multi-User Groundwork          | Complete    | Registration, user_id audit, data isolation tests   |
| UI/UX                    | Remediation                    | Complete    | Nav restructure, settings consolidation, polish     |
| --                       | Production Readiness Audit     | Complete    | IDOR fixes, ownership guards, pool config, docs     |
| **Status key:** Complete | In Progress                    | Not Started | Deferred                                            |

See [docs/project_roadmap_v5.md](docs/project_roadmap_v5.md) for the project roadmap.

---

## Project Structure

```text
shekel/
├── app/
│   ├── __init__.py              # Application factory (create_app)
│   ├── config.py                # Dev / Test / Prod configuration
│   ├── extensions.py            # SQLAlchemy, Migrate, LoginManager, Limiter
│   ├── exceptions.py            # Domain-specific exceptions
│   ├── models/                  # SQLAlchemy models (21 files, 5 PG schemas)
│   ├── routes/                  # Flask Blueprints (17 route modules)
│   ├── services/                # Business logic (21 service modules)
│   ├── schemas/                 # Marshmallow validation (consolidated schema)
│   ├── utils/                   # Logging config, structured log events, auth helpers
│   ├── templates/               # Jinja2 HTML templates (80 files, 16 directories)
│   └── static/                  # CSS, JS (16 chart/grid/form scripts), images
├── migrations/                  # Alembic database migrations (19 versions)
├── monitoring/                  # Promtail config and Grafana/Loki runbook
├── deploy/                      # Version-controlled deployment configs
│   ├── nginx-bundled/           #   Bundled-mode Nginx config (default)
│   ├── nginx-shared/            #   Shared-mode Nginx + vhost (homelab)
│   └── docker-compose.prod.yml  #   Compose override that selects shared mode
├── cloudflared/                 # Cloudflare Tunnel configuration
├── .github/workflows/           # CI (lint + test) and Docker image publishing
├── scripts/                     # Seed, backup/restore, integrity check, ops scripts
├── tests/                       # pytest test suite (65 files, 1827 tests)
├── docs/                        # Plans, progress tracking, runbooks
├── docker-compose.yml           # Production Docker Compose (app + PG + Nginx)
├── docker-compose.dev.yml       # Development Docker Compose (dev DB + test DB)
├── Dockerfile                   # Multi-stage production container
├── gunicorn.conf.py             # Gunicorn WSGI server configuration
├── entrypoint.sh                # Container startup (DB init, migrate, seed)
├── .python-version              # The Python version uv, CI and the Dockerfile follow
├── pyproject.toml               # Python dependencies and the ranges each may move in
├── uv.lock                      # Every package's exact version and sha256
└── run.py                       # Entry point
```
