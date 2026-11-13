"""Tests for remediation Commit C-37: internal TLS and Cloudflare Access.

Covers audit findings:

    F-061  cloudflared has no Access policy and uses noTLSVerify: true
    F-128  cloudflared metrics endpoint reachable from homelab
    F-154  TLS not on internal connections (DB plain TCP)

The artifacts under audit are:

    cloudflared/config.yml                Adds metrics: 127.0.0.1:2000 +
                                          originRequest.access block.
    deploy/docker-compose.prod.yml        Adds db service TLS command +
                                          volumes for cert/key +
                                          DB_SSLMODE + PGSSLMODE on the
                                          app and migrate services.
    scripts/_entrypoint_lib.sh            Honours DB_SSLMODE in the
                                          database URL each entrypoint
                                          builds (its behaviour is
                                          tested in test_entrypoints.py).
    scripts/generate_pg_cert.sh           Generates the self-signed
                                          cert/key with proper modes
                                          and ownership.
    deploy/postgres/                      Bind-mount source directory
                                          for the cert/key (gitignored
                                          contents).
    .gitignore                            Excludes the private key.

Tests are filesystem/text-based: the fast unit suite must not depend
on a live Docker daemon, openssl install, or sudo privileges. A
separate subprocess test (``TestMergedComposeCarriesTLS``) runs
``docker compose config`` to exercise the merge interpolation
behaviour and skips when Docker is not available.

The runtime-behaviour tests (real openssl-generated cert, postgres
container with ssl=on, psycopg sslmode=require connection) are the
manual verification step in the remediation plan and are not
automated here -- automating them would require sudo and a working
Docker daemon on every test host.
"""
