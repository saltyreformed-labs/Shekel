# Shekel Budget App -- Multi-Stage Dockerfile
# Stage 0: uv, the installer (its binary only).
# Stage 1: Build Python dependencies (includes gcc for psycopg's C module).
# Stage 2: Slim runtime image (no build tools).
#
# BASE-IMAGE PINNING (audit findings F-025, F-060, F-062, F-120 / Commit C-36)
# ---------------------------------------------------------------------------
# Both stages pin the base image by sha256 digest, not by floating tag.
# The digest references the multi-arch image index for ``python:3.14-slim``
# that Dependabot moved it to on 2026-10-16 (#569), which carries, measured
# from the image 2026-10-30:
#   * Python 3.14.8
#   * Debian 13.7 (trixie) with libssl3t64 / openssl / openssl-provider-legacy
#     at 3.5.7-1~deb13u3 -- past the CVE-2026-28390 (HIGH) fix that the
#     2026-05-08 digest first carried (audit F-025).
#   * pip 26.2.1 -- past the CVE-2026-1703 path-traversal fix
#     (audit F-120); the build itself runs no pip (uv installs the lock).
#
# The digest is the immutable identity; the ``:3.14-slim`` tag in the
# reference is informational so a casual reader can tell the line
# refers to the rolling 3.14.x slim variant.  When refreshing the
# digest:
#   1. Pull the new image:
#        docker pull python:3.14-slim
#   2. Capture the new index digest:
#        docker buildx imagetools inspect python:3.14-slim
#      The line ``Digest: sha256:...`` at the top is the OCI image
#      index digest; that is the value to paste below.
#   3. Verify the openssl/pip versions in the new image match or
#      exceed the constraints documented above.
#   4. Update the digest on BOTH FROM lines below in the same commit
#      so the builder and runtime stages stay in lockstep.
#
# OPENSSL DEFENSE-IN-DEPTH
# ------------------------
# Even with the digest pin, both stages run ``apt-get upgrade -y
# openssl libssl3t64 openssl-provider-legacy`` so a CVE that lands in
# Debian's trixie repos between digest refreshes is picked up on the
# next image build.  Belt-and-braces: the digest gives reproducibility,
# the apt upgrade gives currency.

# -- Stage 0: uv ------------------------------------------------------
# uv installs ``uv.lock`` (ruling balance:R-BAL294): every package, indirect
# ones included, at the version and sha256 the lock records.  Its image is
# pinned by digest like the base image's, and only its static binary is
# copied into the builder; uv never reaches the runtime stage.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# -- Stage 1: Builder -------------------------------------------------
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder

# Apply Debian security upgrades to the OpenSSL packages and install
# the build-only deps (libpq headers + ``pg_config`` + a C toolchain)
# that ``psycopg-c`` -- psycopg 3's C speed-up, the ``[c]`` extra in
# requirements.txt (ruling balance:R-BAL209) -- needs to compile from
# source against Debian's libpq.  Combined into a single RUN so the apt
# cache is removed in the same layer.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
        openssl libssl3t64 openssl-provider-legacy \
    && apt-get install -y --no-install-recommends \
        libpq-dev gcc libc-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

# The venv the runtime stage copies.  uv makes it from this image's own
# Python, the one ``.python-version`` names: with downloads off, a
# ``.python-version`` this base image does not satisfy fails the build
# instead of fetching another interpreter.  No pip runs in this build and
# none is seeded into the venv, so the pip CVE fix audit finding F-120
# pinned here has nothing left to guard in /opt/venv.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# Explicit builder working directory so the lock COPY and the install
# below do not depend on the base image's default working directory
# (also clears hadolint DL3045).
WORKDIR /build

COPY pyproject.toml uv.lock .python-version ./
# Install exactly the lock's runtime set plus the ``server`` group
# (gunicorn), every file checked against its sha256 (``--locked`` also
# refuses a lock that no longer matches pyproject.toml).  The first sync
# adds the ``build`` group so psycopg-c compiles against the locked,
# hashed setuptools (pyproject.toml's ``no-build-isolation-package``);
# ``--require-build-hashes`` refuses any other source build.  The second
# sync removes the build tools again, so the venv holds what runs and
# nothing that only built it, and compiles its bytecode as pip did, since
# the read-only runtime cannot write it.
RUN uv sync --locked --require-build-hashes --no-install-project \
        --no-default-groups --group build --group server \
    && uv sync --locked --compile-bytecode --no-install-project \
        --no-default-groups --group server

# -- Stage 2: Runtime -------------------------------------------------
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

# Apply the same Debian OpenSSL upgrade to the runtime stage.  The
# runtime image carries libssl3t64 (pulled in transitively by
# postgresql-client below); without this upgrade the CVE-fixed
# package would live only in the builder stage.  Runtime-only deps:
# libpq5 (the libpq psycopg-c links) and postgresql-client (psql in
# entrypoint.sh).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
        openssl libssl3t64 openssl-provider-legacy \
    && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create the non-root user at a FIXED uid/gid.  1000 is what Debian's
# useradd handed out anyway (production has run as uid 1000 since the
# first deploy), and pinning it lets the USER line below be numeric,
# which a host or orchestrator enforcing run-as-non-root can verify
# without reading this image's /etc/passwd (hadolint DL3066).
RUN groupadd --gid 1000 shekel && useradd --create-home --uid 1000 --gid 1000 shekel
WORKDIR /home/shekel/app

# Copy virtualenv from builder.  The venv carries the lock's runtime
# set and gunicorn and no installer at all -- uv stayed in the builder
# and no pip was seeded -- and the runtime stage installs nothing.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# PSYCOPG'S C IMPLEMENTATION IS REQUIRED, NOT PREFERRED (ruling
# balance:R-BAL209, plan step balance:X-dj).  psycopg 3 loads the best
# implementation it can import and falls back to pure Python SILENTLY, so an
# image without psycopg-c would start and serve every query through the
# slower fallback with nothing in the logs.
# With PSYCOPG_IMPL=c the import FAILS instead: ``create_app`` builds the
# engine, the engine imports the driver, and the app refuses to start.
ENV PSYCOPG_IMPL=c

# THE PROCESS LOCALE IS PINNED HERE (ruling recurrence:R-R92, extending R-R54;
# closes finding F-15).  Month and weekday names from ``strftime`` and
# ``calendar`` follow the process locale.  This image set no locale variable,
# so they read English: CPython's PEP 538 coercion sets only LC_CTYPE (to
# C.UTF-8) and LC_TIME stays C -- even a ``setlocale(LC_ALL, "")`` resolved to
# C.  That held by the ABSENCE of a setting, which any future environment line
# (a LANG in a compose file, a base image that sets one) could end.  Pinning
# LC_ALL makes ``setlocale(LC_ALL, "")`` resolve to C.UTF-8 whatever else is
# set, and ``create_app`` refuses to start under any other value, so every
# locale-sensitive site -- and every future one -- is covered by this line
# instead of by each call site remembering.  Measured 2026-09-23 in the
# production container: its installed locales are C, C.utf8 and POSIX, so
# C.UTF-8 is the one UTF-8 locale this image can adopt.
ENV LC_ALL=C.UTF-8

# Copy application code.  entrypoint.sh ships in the build context (see
# .dockerignore -- it is a must-ship file), so this single COPY already
# places it at /home/shekel/app/entrypoint.sh with shekel ownership; no
# separate COPY for it is needed.
COPY --chown=shekel:shekel . .

# Ensure the static-files mount point exists.  /var/www/static is a
# shared volume Nginx reads from.  Logs go to stdout (captured by
# Docker's json-file driver and shipped off-host by the Alloy
# collector documented in observability.md) so no /home/shekel/app/logs
# directory is created or written -- see Commit C-15 / findings F-082
# and F-150.
#
# /home/shekel/app/state is a small writable volume mount target the
# seed sentinel lives under.  Pre-creating the directory in the image
# guarantees it is shekel-owned when Docker first creates the volume
# (the volume inherits the contents and ownership of the underlying
# image path on first creation) so entrypoint.sh's ``touch`` runs as
# the unprivileged shekel user without an in-line chown step.  See
# audit finding F-022 and remediation Commit C-34.
RUN mkdir -p /var/www/static /home/shekel/app/state \
    && chown shekel:shekel /home/shekel/app/state /var/www/static

# Numeric on purpose (DL3066); the passwd entry above still resolves it,
# so HOME and ownership are those of the shekel user.
USER 1000:1000
EXPOSE 8000

# Health check: verify the app is responding and database is reachable.
# Uses Python's built-in urllib (curl/wget are not in the slim image).
# --start-period gives entrypoint.sh time to run migrations and seeding
# (schema creation + Alembic + ref data + user + tax brackets can take
# well over 30 seconds on a fresh database).  Exec (JSON) form, hadolint
# DL3025: no shell is spawned, and a non-zero exit -- urlopen raising on
# a refused connection or a non-2xx status -- is what marks the container
# unhealthy, so the shell form's trailing ``|| exit 1`` added nothing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
