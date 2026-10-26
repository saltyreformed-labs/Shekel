> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Docker Containerization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Package Shekel as a production-ready Docker image on GHCR with fully automatic first-boot setup.

**Architecture:** Multi-stage Dockerfile (builder + runtime), entrypoint.sh for idempotent DB init, GitHub Actions CI/CD to GHCR, separate dev/prod compose files.

**Tech Stack:** Docker, Docker Compose, GitHub Actions, GHCR, Gunicorn, PostgreSQL 16, Python 3.14

---

### Task 1: Create `.dockerignore`

**Files:**
- Create: `.dockerignore`

**Step 1: Create the file**

```
# Version control
.git
.gitignore

# Environment & secrets
.env
.env.*
!.env.example

# Python artifacts
__pycache__
*.pyc
*.pyo
.pytest_cache

# Test suite
tests/

# Logs & data
logs/
pgdata/

# Documentation
docs/
*.md

# CI/CD & IDE
.github/
.vscode/
.idea/
*.swp
*.swo

# Docker files (don't nest)
docker-compose*.yml
Dockerfile
.dockerignore

# Linting config
.pylintrc
```

**Step 2: Verify it works**

Run: `docker build --no-cache -t shekel-test . 2>&1 | head -5`
Expected: Build starts successfully, context should be small (~5-10MB)

**Step 3: Commit**

```bash
git add .dockerignore
git commit -m "chore: add .dockerignore to exclude unnecessary files from image"
```

---

### Task 2: Create `scripts/init_db.sql`

This SQL file is used by the entrypoint to create PostgreSQL schemas before Alembic runs.

**Files:**
- Create: `scripts/init_db.sql`

**Step 1: Create the file**

```sql
-- Shekel Budget App -- Database Schema Initialization
-- Creates the PostgreSQL schemas required by the application.
-- Safe to run multiple times (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS budget;
CREATE SCHEMA IF NOT EXISTS salary;
CREATE SCHEMA IF NOT EXISTS system;
```

**Step 2: Verify syntax**

Run: `psql "$DATABASE_URL" -f scripts/init_db.sql`
Expected: Five `CREATE SCHEMA` statements succeed (or skip if schemas exist)

**Step 3: Commit**

```bash
git add scripts/init_db.sql
git commit -m "chore: add init_db.sql for Docker schema initialization"
```

---

### Task 3: Update `scripts/seed_user.py` defaults for shareable product

The current defaults contain personal credentials. Change them to generic defaults suitable for a shareable product. The script is already idempotent -- no logic changes needed.

**Files:**
- Modify: `scripts/seed_user.py:61-63`

**Step 1: Update the defaults**

Change lines 61-63 from:
```python
    email = os.getenv("SEED_USER_EMAIL", "josh@saltyreformed.com")
    password = os.getenv("SEED_USER_PASSWORD", "Tit4nnc4twaiCJ")
    display_name = os.getenv("SEED_USER_DISPLAY_NAME", "Josh Grubb")
```

To:
```python
    email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")
    password = os.getenv("SEED_USER_PASSWORD", "changeme")
    display_name = os.getenv("SEED_USER_DISPLAY_NAME", "Budget Admin")
```

**Step 2: Run existing tests to make sure nothing breaks**

Run: `pytest tests/ -x -q 2>&1 | tail -5`
Expected: All tests pass (seed_user.py defaults aren't used in tests)

**Step 3: Commit**

```bash
git add scripts/seed_user.py
git commit -m "chore: update seed_user.py defaults to generic credentials"
```

---

### Task 4: Create `entrypoint.sh`

The entrypoint script runs on every container start. It initializes the database (idempotently) then starts Gunicorn.

**Files:**
- Create: `entrypoint.sh`

**Step 1: Create the file**

```bash
#!/bin/bash
set -e

echo "=== Shekel Entrypoint ==="

# ── 1. Wait for PostgreSQL ──────────────────────────────────────
echo "Waiting for PostgreSQL..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
    sleep 1
done
echo "PostgreSQL is ready."

# ── 2. Create schemas ──────────────────────────────────────────
echo "Creating database schemas..."
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -d "${DB_NAME:-shekel}" -f scripts/init_db.sql -q
echo "Schemas ready."

# ── 3. Run migrations ──────────────────────────────────────────
echo "Running database migrations..."
flask db upgrade
echo "Migrations complete."

# ── 4. Seed reference data ─────────────────────────────────────
echo "Seeding reference data..."
python scripts/seed_ref_tables.py
python scripts/seed_tax_brackets.py
echo "Reference data ready."

# ── 5. Create seed user (first run only) ───────────────────────
if [ -n "$SEED_USER_EMAIL" ]; then
    echo "Checking for seed user..."
    python scripts/seed_user.py
fi

echo "=== Starting Gunicorn ==="
exec gunicorn --bind 0.0.0.0:5000 --workers "${GUNICORN_WORKERS:-2}" --access-logfile - run:app
```

**Step 2: Make it executable**

Run: `chmod +x entrypoint.sh`

**Step 3: Test syntax**

Run: `bash -n entrypoint.sh`
Expected: No output (no syntax errors)

**Step 4: Commit**

```bash
git add entrypoint.sh
git commit -m "feat: add entrypoint.sh for automatic database init on container start"
```

---

### Task 5: Rewrite `Dockerfile` as multi-stage build

**Files:**
- Modify: `Dockerfile` (full rewrite)

**Step 1: Rewrite the Dockerfile**

```dockerfile
# Shekel Budget App -- Multi-Stage Dockerfile
# Stage 1: Build Python dependencies (includes gcc for psycopg2).
# Stage 2: Slim runtime image (no build tools).

# ── Stage 1: Builder ────────────────────────────────────────────
FROM python:3.14-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# ── Stage 2: Runtime ────────────────────────────────────────────
FROM python:3.14-slim

# Runtime-only PostgreSQL client library + CLI tools for entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user.
RUN useradd --create-home shekel
WORKDIR /home/shekel/app

# Copy virtualenv from builder.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code and entrypoint.
COPY . .
COPY entrypoint.sh /home/shekel/app/entrypoint.sh

# Own everything as shekel user.
RUN chown -R shekel:shekel /home/shekel

USER shekel
EXPOSE 5000

ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
```

**Step 2: Build the image and verify size**

Run: `docker build -t shekel:test . 2>&1 | tail -5`
Expected: Build succeeds

Run: `docker images shekel:test --format '{{.Size}}'`
Expected: ~150-180MB (smaller than the previous ~250MB single-stage)

**Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat: rewrite Dockerfile as multi-stage build for smaller image"
```

---

### Task 6: Rewrite `docker-compose.yml` for production (GHCR)

Replace the current compose file with the production version that pulls from GHCR. Create a separate dev compose file.

**Files:**
- Modify: `docker-compose.yml` (full rewrite -- production)
- Create: `docker-compose.dev.yml` (development)

**Step 1: Rewrite `docker-compose.yml` for production**

```yaml
# Shekel Budget App -- Production Docker Compose
#
# Quick start:
#   1. Copy .env.example to .env and fill in values
#   2. docker compose up -d
#   3. Open http://localhost:5000
#
# Update:
#   docker compose pull && docker compose up -d

services:
  db:
    image: postgres:16-alpine
    container_name: shekel-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: shekel_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: shekel
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shekel_user -d shekel"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    image: ghcr.io/saltyreformed/shekel:latest
    container_name: shekel-app
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      FLASK_ENV: production
      SECRET_KEY: ${SECRET_KEY:?Set SECRET_KEY in .env}
      DATABASE_URL: postgresql://shekel_user:${POSTGRES_PASSWORD}@db:5432/shekel
      DB_HOST: db
      DB_USER: shekel_user
      DB_PASSWORD: ${POSTGRES_PASSWORD}
      DB_NAME: shekel
      SEED_USER_EMAIL: ${SEED_USER_EMAIL:-}
      SEED_USER_PASSWORD: ${SEED_USER_PASSWORD:-}
      SEED_USER_DISPLAY_NAME: ${SEED_USER_DISPLAY_NAME:-Budget Admin}
      GUNICORN_WORKERS: ${GUNICORN_WORKERS:-2}
    ports:
      - "${APP_PORT:-5000}:5000"
    volumes:
      - applogs:/home/shekel/app/logs

volumes:
  pgdata:
  applogs:
```

**Step 2: Create `docker-compose.dev.yml` for development**

```yaml
# Shekel Budget App -- Development Docker Compose
#
# Provides PostgreSQL for local development.
# Usage:
#   Database only:   docker compose -f docker-compose.dev.yml up db
#   Full stack:      docker compose -f docker-compose.dev.yml up
#   Then run Flask:  flask run

services:
  db:
    image: postgres:16-alpine
    container_name: shekel-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: shekel_user
      POSTGRES_PASSWORD: shekel_pass
      POSTGRES_DB: shekel
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shekel_user -d shekel"]
      interval: 10s
      timeout: 5s
      retries: 5

  test-db:
    image: postgres:16-alpine
    container_name: shekel-test-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: shekel_user
      POSTGRES_PASSWORD: shekel_pass
      POSTGRES_DB: shekel_test
    ports:
      - "5433:5432"
    volumes:
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shekel_user -d shekel_test"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build: .
    container_name: shekel-app
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      FLASK_ENV: production
      SECRET_KEY: dev-secret-key-not-for-production
      DATABASE_URL: postgresql://shekel_user:shekel_pass@db:5432/shekel
      DB_HOST: db
      DB_USER: shekel_user
      DB_PASSWORD: shekel_pass
      DB_NAME: shekel
      SEED_USER_EMAIL: admin@shekel.local
      SEED_USER_PASSWORD: changeme
      SEED_USER_DISPLAY_NAME: Budget Admin
    ports:
      - "5000:5000"

volumes:
  pgdata:
```

**Step 3: Verify dev compose syntax**

Run: `docker compose -f docker-compose.dev.yml config --quiet`
Expected: No output (valid syntax)

**Step 4: Commit**

```bash
git add docker-compose.yml docker-compose.dev.yml
git commit -m "feat: split docker-compose into production (GHCR) and development configs"
```

---

### Task 7: Update `.env.example`

Update the example environment file to document all Docker-relevant variables.

**Files:**
- Modify: `.env.example`

**Step 1: Rewrite `.env.example`**

```
# Shekel Budget App -- Environment Configuration
# Copy this file to .env and fill in your values.

# ── Flask ────────────────────────────────────────────────────────
FLASK_APP=run.py
FLASK_ENV=development
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=change-me-to-a-random-secret-key

# ── Database ─────────────────────────────────────────────────────
# Development (local Flask + Docker PostgreSQL)
DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5432/shekel
TEST_DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/shekel_test

# Docker Compose (used by docker-compose.yml)
POSTGRES_PASSWORD=shekel_pass

# ── Session ──────────────────────────────────────────────────────
REMEMBER_COOKIE_DURATION_DAYS=30

# ── Seed User (first run only) ───────────────────────────────────
# Set these before first `docker compose up`. The initial user is
# created automatically. Remove or leave empty after first run.
SEED_USER_EMAIL=admin@shekel.local
SEED_USER_PASSWORD=changeme
SEED_USER_DISPLAY_NAME=Budget Admin

# ── Gunicorn (Docker production only) ────────────────────────────
GUNICORN_WORKERS=2
APP_PORT=5000
```

**Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: update .env.example with Docker and seed user variables"
```

---

### Task 8: Create GitHub Actions workflow

**Files:**
- Create: `.github/workflows/docker-publish.yml`

**Step 1: Create directory**

Run: `mkdir -p .github/workflows`

**Step 2: Create the workflow file**

```yaml
# Build and publish Shekel Docker image to GitHub Container Registry.
#
# Triggers:
#   - Push to main → tags as "latest"
#   - Git tag v* → tags as version + "latest"

name: Build & Publish Docker Image

on:
  push:
    branches: [main]
    tags: ["v*"]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata (tags, labels)
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=sha

      - name: Build and push image
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

**Step 3: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/docker-publish.yml'))"`
Expected: No error (requires PyYAML -- if not available, skip this check)

**Step 4: Commit**

```bash
git add .github/workflows/docker-publish.yml
git commit -m "ci: add GitHub Actions workflow to build and publish Docker image to GHCR"
```

---

### Task 9: Fix `seed_tax_brackets.py` for first-boot ordering

Currently `seed_tax_brackets.py` requires users to exist first and exits early with "No users found" if the user table is empty. But in the entrypoint, seed_user.py runs AFTER seed_tax_brackets.py won't find users yet.

Two options:
- (A) Reorder: run seed_user.py before seed_tax_brackets.py in the entrypoint
- (B) Make seed_tax_brackets.py gracefully skip when no users exist (it already does this -- prints a message and returns)

**Resolution:** Reorder the entrypoint so seed_user runs BEFORE seed_tax_brackets. Update `entrypoint.sh` steps 4 and 5:

**Files:**
- Modify: `entrypoint.sh` (swap step 4 and step 5)

**Step 1: Update entrypoint.sh**

Replace the seed section (after "Running database migrations..." block) with:

```bash
# ── 4. Seed reference data ─────────────────────────────────────
echo "Seeding reference data..."
python scripts/seed_ref_tables.py

# ── 5. Create seed user (first run only) ───────────────────────
if [ -n "$SEED_USER_EMAIL" ]; then
    echo "Checking for seed user..."
    python scripts/seed_user.py
fi

# ── 6. Seed tax brackets (requires users to exist) ─────────────
echo "Seeding tax configuration..."
python scripts/seed_tax_brackets.py
echo "Seeding complete."
```

**Step 2: Commit**

```bash
git add entrypoint.sh
git commit -m "fix: reorder entrypoint seeding so user exists before tax brackets"
```

---

### Task 10: End-to-end local test

Build and run the full stack locally to verify everything works.

**Step 1: Build the image**

Run: `docker build -t shekel:local-test .`
Expected: Multi-stage build succeeds

**Step 2: Start the stack with dev compose**

Run: `docker compose -f docker-compose.dev.yml up -d`
Expected: Both `shekel-db` and `shekel-app` containers start

**Step 3: Check entrypoint logs**

Run: `docker logs shekel-app 2>&1`
Expected output includes:
```
=== Shekel Entrypoint ===
PostgreSQL is ready.
Creating database schemas...
Schemas ready.
Running database migrations...
Migrations complete.
Seeding reference data...
Ref table seeding complete.
Checking for seed user...
Created user: admin@shekel.local
Seeding tax configuration...
Tax bracket seeding complete.
=== Starting Gunicorn ===
```

**Step 4: Test the app**

Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/login`
Expected: `200`

**Step 5: Test login**

Run: `curl -s -c cookies.txt -d "email=admin@shekel.local&password=changeme" -L http://localhost:5000/login -o /dev/null -w "%{http_code}"`
Expected: `200` (redirects to grid/dashboard after login)

**Step 6: Verify restart idempotency**

Run: `docker compose -f docker-compose.dev.yml restart app && sleep 5 && docker logs shekel-app --tail 20 2>&1`
Expected: Entrypoint runs again, all seed steps show "already exists, skipping" messages, no errors

**Step 7: Clean up**

Run: `docker compose -f docker-compose.dev.yml down -v && rm -f cookies.txt`

**Step 8: Commit any fixes discovered during testing**

If any issues were found and fixed, commit them now.

---

### Task 11: Verify image size

**Step 1: Compare image sizes**

Run: `docker images shekel:local-test --format '{{.Size}}'`
Expected: ~150-180MB

**Step 2: Inspect image layers**

Run: `docker history shekel:local-test --no-trunc --format "{{.Size}}\t{{.CreatedBy}}" | head -15`
Expected: No gcc, no libpq-dev in final image layers

---

### Task 12: Final commit and push

**Step 1: Verify all files are committed**

Run: `git status`
Expected: Clean working tree

**Step 2: Push to main**

Run: `git push origin main`
Expected: Push succeeds. GitHub Actions workflow triggers automatically.

**Step 3: Verify GitHub Actions build**

Check: `https://github.com/SaltyReformed/Shekel/actions`
Expected: "Build & Publish Docker Image" workflow runs and succeeds

**Step 4: Verify GHCR image**

Run: `docker pull ghcr.io/saltyreformed/shekel:latest`
Expected: Image pulls successfully

**Step 5: Test the GHCR image end-to-end**

Create a temporary directory and test the production compose:
```bash
mkdir /tmp/shekel-test && cd /tmp/shekel-test
# Copy docker-compose.yml and .env.example from the repo
cp ~/GitHub/Shekel/docker-compose.yml .
cp ~/GitHub/Shekel/.env.example .env
# Edit .env: set real SECRET_KEY and POSTGRES_PASSWORD
sed -i 's/change-me-to-a-random-secret-key/'"$(python -c 'import secrets;print(secrets.token_hex(32))')"'/' .env
sed -i 's/POSTGRES_PASSWORD=shekel_pass/POSTGRES_PASSWORD=test-secure-pass/' .env
docker compose up -d
# Wait for health check and entrypoint
sleep 15
curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/login
# Expected: 200
docker compose down -v
rm -rf /tmp/shekel-test
```
