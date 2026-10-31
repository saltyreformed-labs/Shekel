#!/bin/sh
# start.sh -- starts Valkey, the rate limiter's counter store, under the ACL
# users in users.acl beside this file (ruling plan#2350).
#
# Every compose file runs this as the valkey service's command, with this
# directory mounted read-only at /etc/valkey.  The password is read as the
# app's entrypoint reads a secret (entrypoint.sh's _load_secret): the file
# /run/secrets/valkey_password when one is mounted, else the environment
# variable VALKEY_PASSWORD.
#   docker-compose.yml               VALKEY_PASSWORD, from .env's
#                                    SHEKEL_VALKEY_PASSWORD
#   deploy/docker-compose.prod.yml   production's password file, mounted as
#                                    that secret, VALKEY_PASSWORD blanked
#   docker-compose.dev.yml           VALKEY_PASSWORD, the committed dev-only
#                                    password
# A read-only service cannot take compose's environment-sourced secret
# (compose refuses: it would copy the file in), so the variable is how a
# password with no file behind it arrives.  The command substitution drops a
# trailing newline left in a file by an editor or echo, as _load_secret
# does, so the hash matches the password the app sends.  The variable is
# unset before the server starts, so the server's own environment holds no
# password.
#
# Valkey refuses users in its own config beside an ACL file, so the rules and
# the password meet in ONE generated file: the rules with their comment lines
# dropped (the ACL file format refuses them) and the shekel user's password
# added as its SHA-256 hash, the form an ACL file holds instead of the
# password.  It is written to /tmp, the container's tmpfs, under the image
# entrypoint's umask of 0077, so only the valkey user can read it.
#
# It refuses to start Valkey when the password is empty or when users.acl
# names no shekel user: either way the app could not log in, and with the
# limiter fail-closed every rate-limited request would fail while the
# healthcheck, which logs in as its own user, still passed.
#
# The server flags, the one home of each:
#   --save ''                       no RDB snapshots and
#   --appendonly no                 no AOF: the counters are short-lived, and
#                                   writing them to disk would add IO with no
#                                   benefit, leave client addresses on disk
#                                   and slow a restart.
#   --maxmemory 64mb                far above the per-IP counters of a
#                                   single-user app.
#   --maxmemory-policy allkeys-lru  evict the least recently used key when
#                                   full, rather than refuse a write, which
#                                   the fail-closed limiter would turn into
#                                   a 500.
set -eu

rules=/etc/valkey/users.acl
secret=/run/secrets/valkey_password
acl=/tmp/users.acl

if [ -f "$secret" ]; then
    password=$(cat "$secret")
    origin=$secret
else
    password=${VALKEY_PASSWORD:-}
    origin=VALKEY_PASSWORD
fi
unset VALKEY_PASSWORD
if [ -z "$password" ]; then
    echo "start.sh: $origin is empty or unset, so the shekel user would have no password; refusing to start" >&2
    exit 1
fi
hash=$(printf '%s' "$password" | sha256sum | cut -d ' ' -f 1)
unset password

sed -e '/^[[:space:]]*#/d' -e "s/^user shekel /user shekel #$hash /" "$rules" >"$acl"
if ! grep -q "^user shekel #$hash " "$acl"; then
    echo "start.sh: $rules has no line starting 'user shekel ', so the app could not log in; refusing to start" >&2
    exit 1
fi

exec valkey-server \
    --save '' \
    --appendonly no \
    --maxmemory 64mb \
    --maxmemory-policy allkeys-lru \
    --aclfile "$acl"
