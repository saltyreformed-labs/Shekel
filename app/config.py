"""
Shekel Budget App -- Configuration Classes

Provides Dev, Test, and Prod configuration classes loaded by the
application factory based on the FLASK_ENV environment variable.
"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

# Load .env file if present (development convenience).
load_dotenv()


# Known placeholder SECRET_KEY values that have appeared in this
# repository's history, .env.example, or docker-compose files.  Any
# value in this set must be rejected at production startup because it
# is publicly known and cannot provide cryptographic confidentiality
# for session cookies or itsdangerous-signed tokens.  See audit
# findings F-001, F-016, F-110, F-111.
#
# ``replaced_by_docker_secret`` is the placeholder the audit's Commit
# C-38 runbook tells operators to leave in ``.env`` when migrating
# to Docker secrets (Posture 2).  In steady-state Posture 2 the
# entrypoint loader overwrites the env value with the real secret
# from ``/run/secrets/secret_key`` before this check runs, so the
# placeholder is not seen here.  But if the secret file is missing
# or unreadable, the placeholder remains -- catching it here closes
# the gap where the operator believes they are on Posture 2 but is
# actually running on the publicly-known placeholder.
_KNOWN_DEFAULT_SECRETS = frozenset({
    "dev-only-change-me-in-production",
    "change-me-to-a-random-secret-key",
    "dev-secret-key-not-for-production",
    "replaced_by_docker_secret",
})

# Minimum acceptable SECRET_KEY length for production.  32 characters
# matches the output of ``secrets.token_hex(16)`` (16 bytes / 128 bits
# of entropy) which is the floor recommended by ASVS L2 V6.4.2.  The
# generation command in .env.example produces a 64-char hex string
# (32 bytes / 256 bits) which is well above this floor.
_MIN_SECRET_KEY_LENGTH = 32

# Sentinel password embedded in the ``.env.example`` DATABASE_URL and
# TEST_DATABASE_URL templates so a fresh checkout cannot accidentally
# boot under the historically-leaked default ``shekel_pass``.  An
# operator who copies ``.env.example`` to ``.env`` and forgets to
# substitute their own password is met with a loud app-level
# ``ValueError`` from ``_reject_sentinel`` below, rather than a
# generic Postgres "password authentication failed" at connect time.
# See audit finding F-109 and the C-38 follow-up Issue 3 in
# ``shekel-audits/security-2026-04-15/c-38-followups.md``.
#
# The token is intentionally specific (uppercase, hyphen-delimited,
# 37 chars) so that a real password is extremely unlikely to contain
# it as a substring.  The substring check is correct here because the
# token surfaces inside a postgres URI ``user:password@host`` form;
# parsing the URI would be heavier and is unnecessary given the
# specificity of the marker.
_DATABASE_URL_SENTINEL = "REPLACE-ME-WITH-YOUR-POSTGRES-PASSWORD"


def _reject_sentinel(uri: str | None, *, var_name: str) -> str | None:
    """Raise ``ValueError`` when a connection URI still embeds the sentinel.

    Companion of ``_DATABASE_URL_SENTINEL``.  Callers pass the value
    they just resolved from ``os.getenv(var_name)`` (or its fallback)
    and either get the URI back unchanged or a clear app-level error
    that names the offending env var and points at ``.env.example``.

    Args:
        uri: A SQLAlchemy connection URI, or ``None``.  ``None`` and
            empty strings fall through unchanged so the caller's own
            missing-URI validation (e.g. ``ProdConfig.__init__``'s
            "DATABASE_URL must be set in production" branch) still
            fires at the right layer.
        var_name: The environment-variable name to mention in the
            error message -- ``"DATABASE_URL"``, ``"DATABASE_URL_APP"``,
            or ``"TEST_DATABASE_URL"``.  Naming the precise variable
            lets the operator find the offending entry in their
            ``.env`` without grepping.

    Returns:
        The ``uri`` unchanged when it does not embed the sentinel
        (including the ``None`` and empty-string cases).

    Raises:
        ValueError: When ``uri`` contains ``_DATABASE_URL_SENTINEL``.
            The message names both the env var and the sentinel so
            the remediation is actionable from the error text alone.
    """
    if uri and _DATABASE_URL_SENTINEL in uri:
        raise ValueError(
            f"{var_name} still embeds the .env.example sentinel "
            f"password '{_DATABASE_URL_SENTINEL}'.  Copy "
            ".env.example to .env and replace the sentinel with a "
            "real PostgreSQL password before starting the "
            "application or the test suite.  See audit finding "
            "F-109 / Commit C-38 follow-up Issue 3."
        )
    return uri


#: The driver arguments EVERY configuration connects with (plan step
#: balance:X-dj): one dict, so development, the suite and production cannot
#: open their connections in different ways.
#:
#: ``connect_timeout``: fail in 5 seconds when the database host is
#: unreachable instead of waiting for the system TCP timeout (120+ seconds).
#:
#: ``prepare_threshold=None`` turns OFF psycopg 3's automatic server-side
#: prepared statements -- by default it prepares a statement once a single
#: connection has run it five times.  psycopg2 never prepared, and the suite is
#: not built to grade the path: TestConfig's ``NullPool`` closes a connection
#: at check-in, so a statement must repeat five times inside ONE checkout to be
#: prepared there, while production's ``QueuePool`` keeps a connection across
#: requests for up to ``pool_recycle`` -- the two would run the prepared path
#: at different rates, and production would own failure modes no test sees (a
#: generic plan PostgreSQL may choose for a prepared statement, and a prepared
#: statement whose result columns DDL on another connection has changed, which
#: PostgreSQL refuses with "cached plan must not change result type" rather
#: than re-planning).  What it gives up is the parse each REPEAT of a
#: statement would have skipped once prepared, and the planning too once
#: PostgreSQL settled on a generic plan for it.  Ruled as an application of
#: R-BAL207 by the coordinating session, 2026-10-05.
_DRIVER_CONNECT_ARGS = {"connect_timeout": 5, "prepare_threshold": None}


class BaseConfig:
    """Shared configuration defaults across all environments."""

    # Flask core.  No fallback default: production must fail closed
    # when SECRET_KEY is missing.  Dev and test paths set this via
    # the developer's .env file or, in the test suite, conftest.py.
    SECRET_KEY = os.getenv("SECRET_KEY")

    # MFA -- Fernet key for encrypting TOTP secrets at rest.
    TOTP_ENCRYPTION_KEY = os.getenv("TOTP_ENCRYPTION_KEY")

    # MFA -- optional comma-separated list of retired Fernet keys.
    # Used by ``mfa_service.get_encryption_key`` to build a MultiFernet
    # that decrypts ciphertexts written under a previous primary key.
    # Set this transiently during a TOTP_ENCRYPTION_KEY rotation; the
    # operator removes it again after running scripts/rotate_totp_key.py.
    # Optional and may be absent or empty -- the empty case is the
    # steady-state production posture.  See docs/runbook_secrets.md.
    TOTP_ENCRYPTION_KEY_OLD = os.getenv("TOTP_ENCRYPTION_KEY_OLD")

    # SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Inherited by DevConfig; TestConfig and ProdConfig state their own pool
    # options and carry the same driver arguments.
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": dict(_DRIVER_CONNECT_ARGS)}

    # ---- Session lifetime + idle timeout + step-up auth -----------------
    #
    # Three cooperating settings bound the "unattended access" blast
    # radius for a stolen session cookie or a forgotten signed-in
    # browser.  See audit findings F-006, F-035, F-045 / commit C-10.

    # Hard cap on the lifetime of a permanent (logged-in) session
    # cookie.  Flask's default is 31 days, which is too generous for a
    # money app: a stolen browser profile would carry valid auth for a
    # month with no further interaction required.  12 hours covers a
    # normal work day; "remember me" is the supported path for users
    # who want a longer window (see REMEMBER_COOKIE_DURATION below).
    # Operators can override via SESSION_LIFETIME_HOURS in .env -- the
    # range is intentionally wide to support short-lived test envs and
    # longer-lived dev shells.  NOTE: this cap only bites on a
    # ``permanent`` session, and the app deliberately leaves sessions
    # non-permanent so Flask-Login's ``strong`` session protection
    # (F-038 / C-07) keeps wiping the session on IP / User-Agent drift
    # -- a permanent session is exempt from that wipe.  The effective
    # bound on an open session is therefore IDLE_TIMEOUT_MINUTES below,
    # not this value; "remember me" is the supported cross-restart path.
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=int(os.getenv("SESSION_LIFETIME_HOURS", "12"))
    )

    # Maximum gap between authenticated requests before ``load_user``
    # rejects the session.  This is a single-owner budget app holding
    # only account balances and budget items -- no bank links, no
    # account numbers, no PII beyond an email -- reached only behind
    # full authentication.  The unattended-access blast radius of a
    # stale-but-still-open browser is therefore low, and a short idle
    # window mostly punishes the legitimate owner: a 30-minute timeout
    # bounced them back to a full password + MFA login whenever they
    # stepped away from an open tab and came back.  A 12-hour (720 min)
    # window covers a normal day of on-and-off use without that
    # friction, while still expiring a forgotten session overnight.
    # Operators who want a tighter window can override via
    # IDLE_TIMEOUT_MINUTES in .env.  Refreshed by the ``before_request``
    # hook in ``app/__init__.py`` on every authenticated request;
    # checked by ``load_user`` via ``_session_last_activity_at``.
    IDLE_TIMEOUT_MINUTES = int(os.getenv("IDLE_TIMEOUT_MINUTES", "720"))

    # Maximum age of the most recent password verification before
    # ``fresh_login_required`` redirects to ``/reauth``.  Five
    # minutes is the default ASVS L2 V4.3.3 step-up window: long
    # enough that a sequence of related high-value operations (e.g.
    # adjust anchor balance, then add a deduction, then update tax
    # config) does not require multiple re-auths in a row, short
    # enough that a session-hijack attacker who lacks the password
    # cannot ride a stolen cookie into a destructive operation.
    FRESH_LOGIN_MAX_AGE_MINUTES = int(
        os.getenv("FRESH_LOGIN_MAX_AGE_MINUTES", "5")
    )

    # Flask-Login "remember me" cookie lifetime.  Shortened from the
    # historical 30-day default to 7 days per ASVS L2 guidance for
    # financial apps -- a stolen remember-me cookie is a password-
    # equivalent credential, and 7 days is the right tradeoff between
    # legitimate "stay logged in on my home machine" UX and stolen-
    # device blast radius.  Operators who need a different window can
    # set REMEMBER_COOKIE_DURATION_DAYS in .env without code changes;
    # the ProdConfig further hardens this cookie with Secure, HttpOnly,
    # and SameSite flags (see audit finding F-017).
    REMEMBER_COOKIE_DURATION = timedelta(
        days=int(os.getenv("REMEMBER_COOKIE_DURATION_DAYS", "7"))
    )

    # Largest request body the app will read, in bytes.  Set because plan
    # step ``bank_import:X-f6a`` gives the app its FIRST file upload (a bank
    # statement), and Werkzeug's default is unlimited: without a ceiling, one
    # authenticated POST can be made to consume as much memory as the sender
    # is willing to send.  Werkzeug raises ``RequestEntityTooLarge`` (413)
    # above it; ``app.error_handlers`` turns that into a sentence.
    #
    # **512 KB against a measured need of 59 KB, and the number is small on
    # purpose.**  An adversarial review measured what a CSV parse costs: a
    # payload of many one-cell rows expands ~52x in list overhead while it is
    # being read (5.24 MB of input -> 273 MB resident).  The app container is
    # capped at 1 GB with two Gunicorn workers and a ~250 MB baseline, so a
    # multi-megabyte ceiling put an OOM kill within reach of two concurrent
    # uploads.  512 KB is ~9x the developer's own full year-to-date export and
    # bounds the worst case to tens of megabytes.  Nginx caps the body at 5 MB
    # independently (``deploy/nginx-*``), so this is the binding limit.
    #
    # **It is the ONE bound on every request body, form or upload** (ruling
    # R-BAL218, developer 2026-10-06).  Werkzeug weighs a body against it
    # before anything parses it, whatever its type.  Since Werkzeug 3.1.9 it
    # is the ONLY bound on an ordinary (urlencoded) form: Flask's
    # ``MAX_FORM_MEMORY_SIZE`` (500,000 bytes, its default, never set here)
    # was the tighter bound on such a body through 3.1.8, and now weighs only
    # a multipart TEXT part; the one multipart form here (the upload) carries
    # two short ones.  The arguments this app makes about a crafted body rest
    # on this number -- ``StatementMatchSchema``'s note in
    # ``app/schemas/validation/statements.py`` prices a crafted Reconcile
    # pass against it -- and ``tests/test_config.py``'s
    # ``TestTheUploadCeiling`` pins the value and grades it on a form body,
    # at the ceiling and one byte past it.
    MAX_CONTENT_LENGTH = 512 * 1024

    # Budget defaults
    DEFAULT_PAY_PERIOD_HORIZON = 52  # ~2 years of biweekly periods
    DEFAULT_PAY_CADENCE_DAYS = 14  # biweekly

    # Logging
    SLOW_REQUEST_THRESHOLD_MS = int(
        os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500")
    )

    # Registration toggle -- set to 'false' to disable public /register.
    REGISTRATION_ENABLED = os.getenv(
        "REGISTRATION_ENABLED", "true"
    ).lower() in ("true", "1", "yes")

    # Audit
    AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))

    # ---- Flask-Limiter -------------------------------------------------
    #
    # Storage backend URI for rate-limit counters.  BaseConfig defaults
    # to "memory://" so that a developer running ``flask run`` against
    # a local checkout does not need a Redis container.  ProdConfig
    # overrides to ``redis://redis:6379/0`` so that counters are shared
    # across Gunicorn workers and survive ``docker compose restart app``
    # cycles -- see audit finding F-034 and remediation Commit C-06.
    # TestConfig forces ``memory://`` so the test suite does not require
    # a running Redis instance.
    #
    # The env-var override always wins, in any environment, so an
    # operator can point dev or staging at a real Redis without code
    # changes.
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

    # Default per-IP ceiling applied to every route that does NOT carry
    # an explicit ``@limiter.limit`` decorator.  As of audit verification
    # (Phase A 2026-04-15) only 4 of ~93 mutating routes have an explicit
    # rate limit.  Without this ceiling, an authenticated attacker could
    # spam any unprotected mutating endpoint at full request rate.  The
    # numbers are conservative enough to permit normal HTMX-driven grid
    # editing (per-action fan-out is roughly 2 requests per mark-done)
    # while still capping abuse.  Format: semicolon-separated list of
    # ``<count> per <window>`` strings, parsed by Flask-Limiter into
    # individual Limit objects.
    RATELIMIT_DEFAULT = "200 per hour;30 per minute"

    # Fail-closed posture: if Redis becomes unreachable, do NOT silently
    # drop rate-limit checks (which would let an attacker brute-force
    # auth during a Redis outage), and do NOT fall back to per-worker
    # in-memory counting (which re-introduces the multi-worker drift
    # documented in F-034).  Both flags are explicitly False so the
    # storage exception bubbles up to the 500 handler -- the operator
    # sees an auth outage, not a silent posture degradation.  This is
    # the developer's deliberate choice over the operator-friendly
    # fail-open default; see shekel-audits/security-2026-04-15/
    # remediation-plan.md Phase D-12.
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = False
    RATELIMIT_SWALLOW_ERRORS = False

    # Emit X-RateLimit-* headers on every limited response.  Helps the
    # operator (and any front-end retry logic) understand current
    # consumption without inspecting Redis directly.
    RATELIMIT_HEADERS_ENABLED = True

    # Use the moving-window strategy so that bursts spread across the
    # window boundary cannot double the effective limit.  fixed-window
    # is Flask-Limiter's default but is easy to game (4 requests at
    # 14:59:59 + 4 requests at 15:00:01 = 8 requests in 2 seconds under
    # a "5 per 15 minutes" rule).  moving-window keeps a sliding count.
    RATELIMIT_STRATEGY = "moving-window"

    # ---- Account lockout (audit finding F-033 / commit C-11) ----------
    #
    # Per-account brute-force throttling that cannot be bypassed by IP
    # rotation.  See ``app/models/user.py`` for the column-level
    # documentation and ``app/services/auth_service.py:authenticate``
    # for the enforcement path.  These settings are documented here for
    # operator discovery; the service itself reads ``os.getenv`` at call
    # time (matching the ``mfa_service.TOTP_ENCRYPTION_KEY`` pattern) so
    # tests can adjust thresholds via ``monkeypatch.setenv`` without
    # going through the Flask config object.
    #
    # Threshold of 10 leaves a comfortable margin for typo storms while
    # still clamping a credential-stuffing attack to a single attempt
    # per 15-minute window per account.  An attacker who guesses 10
    # wrong passwords burns one lockout window and gains nothing.
    LOCKOUT_THRESHOLD = int(os.getenv("LOCKOUT_THRESHOLD", "10"))

    # Lockout duration of 15 minutes.  Long enough that an attacker
    # cannot trivially wait it out across many accounts; short enough
    # that a legitimate user who locked themselves out via typos can
    # try again within a coffee-break window without administrator
    # intervention.
    LOCKOUT_DURATION_MINUTES = int(
        os.getenv("LOCKOUT_DURATION_MINUTES", "15")
    )

    # ---- Breached-password check (audit finding F-086 / commit C-11) --
    #
    # Toggle for the HIBP k-anonymity check at password-set time.
    # Defaults to enabled in every non-test environment so that
    # registration, password change, and companion-account creation
    # reject passwords that have appeared in a public breach.  The
    # service reads this via ``os.getenv("HIBP_CHECK_ENABLED", "true")``
    # so tests can enable or disable it per-test through
    # ``monkeypatch.setenv`` without touching the Flask config.
    HIBP_CHECK_ENABLED = os.getenv(
        "HIBP_CHECK_ENABLED", "true",
    ).lower() in ("true", "1", "yes")

    # Network timeout for the HIBP API in seconds.  Short enough that a
    # registration form does not hang on a slow upstream; long enough
    # to absorb normal jitter on api.pwnedpasswords.com.  The service
    # treats a timeout as fail-open (logs a warning and accepts the
    # password) so the registration flow continues to work during
    # transient HIBP outages.  Operators who want fail-closed behavior
    # can run a self-hosted Pwned Passwords mirror and point
    # ``HIBP_ENDPOINT`` at it; that is documented as a future option
    # in the C-11 plan rather than implemented here.
    HIBP_TIMEOUT_SECONDS = float(
        os.getenv("HIBP_TIMEOUT_SECONDS", "3")
    )


def _runtime_database_uri(default: str | None = None) -> str | None:
    """Return the database URI the runtime app should connect under.

    Production deploys provision two PostgreSQL roles:

    * ``shekel_user`` (owner) -- holds DDL rights and runs migrations,
      seeds, and audit cleanup.  The owner URL is exposed as
      ``DATABASE_URL`` so the entrypoint scripts and migration
      tooling pick it up by default.
    * ``shekel_app`` (least-privilege) -- DML-only role with no
      ability to ``DROP TABLE``, ``ALTER TABLE``, or otherwise mutate
      the schema.  An attacker with RCE in the Gunicorn process
      cannot use ``shekel_app`` to remove the audit triggers that
      this commit adds.  The least-privilege URL is exposed as
      ``DATABASE_URL_APP``.

    The runtime app prefers ``DATABASE_URL_APP`` whenever it is set,
    falling back to ``DATABASE_URL`` otherwise.  Deployment scripts
    (``scripts/init_database.py``, ``scripts/build_test_template.py``)
    override ``DATABASE_URL_APP`` to the empty string at startup --
    empty-as-unset per the resolver below -- so they always run as
    the owner role even when a ``.env`` line would re-populate a
    popped key via this module's ``load_dotenv()``.  See the
    file-level docstring of ``scripts/init_database.py`` for the
    rationale.

    Args:
        default: Fallback URI used when neither ``DATABASE_URL_APP``
            nor ``DATABASE_URL`` is set in the environment.  ``None``
            in production (forcing the ``ProdConfig.__init__`` check
            to raise), a peer-auth local URL in development.

    Returns:
        The URI as a string, or ``default`` when neither env var is
        set.  Callers must propagate ``None`` to whichever validation
        the relevant config class performs.

    Raises:
        ValueError: When the resolved URI still embeds the
            ``.env.example`` sentinel password -- see
            ``_reject_sentinel`` for the failure-mode rationale.  The
            error names ``DATABASE_URL_APP`` or ``DATABASE_URL``
            depending on which env var contributed the sentinel.
    """
    app_uri = os.getenv("DATABASE_URL_APP")
    if app_uri:
        # The empty-string case is intentionally treated as unset --
        # ``os.getenv`` returns ``""`` for an exported-but-empty var,
        # and the project policy (see ``test_empty_database_url_app_
        # falls_through``) is to fall through to DATABASE_URL in that
        # case.  Sentinel-rejection only runs when the value is the
        # one actually about to be returned, so an empty
        # DATABASE_URL_APP never triggers a false positive against a
        # sentinel buried in DATABASE_URL -- the next return is what
        # validates DATABASE_URL.
        return _reject_sentinel(app_uri, var_name="DATABASE_URL_APP")
    return _reject_sentinel(
        os.getenv("DATABASE_URL", default),
        var_name="DATABASE_URL",
    )


class DevConfig(BaseConfig):
    """Development configuration -- debug mode, local PostgreSQL.

    Cookie-flag pragma (audit finding F-112):
        DevConfig deliberately does NOT set ``SESSION_COOKIE_SECURE``,
        ``REMEMBER_COOKIE_SECURE``, or the ``__Host-`` session cookie
        prefix that ProdConfig sets.  ``flask run`` serves over plain
        HTTP on ``http://localhost:5000``: a ``Secure``-flagged cookie
        would never ride that connection, so an authenticated dev
        session would silently break -- ``/login`` would 200 but the
        cookie never reaches the client and every subsequent request
        would loop back to ``/login``.  Likewise the ``__Host-``
        prefix is enforced by browsers only when ``Secure=True`` and
        ``Path="/"`` are set.

        The Flask defaults DevConfig inherits (``SESSION_COOKIE_SECURE
        = False``, ``SESSION_COOKIE_HTTPONLY = True``,
        ``SESSION_COOKIE_SAMESITE = None``) are the right posture for
        local HTTP development -- HttpOnly remains on so a dev XSS
        does not trivially exfiltrate the session, but Secure stays
        off so the cookie actually rides ``http://``.  Production
        traffic terminates TLS at the reverse proxy and the cookie
        flags ProdConfig sets are correct for that path.

        Operators who run dev under TLS (e.g. behind a local
        reverse proxy with mkcert) can opt in by setting
        ``FLASK_ENV=production`` for that shell, which switches the
        config map to ProdConfig and applies the hardened flags.
        The DevConfig class is for the supported HTTP-localhost
        workflow only.
    """

    DEBUG = True
    # Falls back to peer-auth local connection if neither
    # DATABASE_URL_APP nor DATABASE_URL is set in .env.  Matches the
    # Quick Start instructions in README.md.
    SQLALCHEMY_DATABASE_URI = _runtime_database_uri("postgresql:///shekel")


class TestConfig(BaseConfig):
    """Test configuration -- separate database, no CSRF, WTF disabled."""

    TESTING = True
    # Falls back to peer-auth local test database if TEST_DATABASE_URL
    # is not set in .env.  ``_reject_sentinel`` runs at class-body
    # evaluation time so a copy-pasted ``.env.example`` that still
    # embeds the sentinel password surfaces as an app-level
    # ``ValueError`` at test-suite startup, never as a generic
    # Postgres "password authentication failed" at first connect.
    # See audit finding F-109 / Commit C-38 follow-up Issue 3.
    SQLALCHEMY_DATABASE_URI = _reject_sentinel(
        os.getenv("TEST_DATABASE_URL", "postgresql:///shekel_test"),
        var_name="TEST_DATABASE_URL",
    )
    WTF_CSRF_ENABLED = False
    LOGIN_DISABLED = False
    RATELIMIT_ENABLED = False

    # Pin the idle window regardless of any IDLE_TIMEOUT_MINUTES in the
    # developer's shell or .env (same hermetic-suite rationale as the
    # RATELIMIT_STORAGE_URI override below).  The dev environment runs a
    # 7-day window for the persistent Playwright auth state, which made
    # test_stale_activity_rejected (13h-stale session vs the 12h
    # default) fail suite-wide whenever the suite inherited it.
    IDLE_TIMEOUT_MINUTES = 720

    # Force the in-memory backend in the test suite regardless of any
    # ``RATELIMIT_STORAGE_URI`` set in the developer's shell or .env.
    # The few tests that flip ``RATELIMIT_ENABLED`` back on (test_errors,
    # test_auth lockout suite) test rate-limit *behavior*, not Redis
    # plumbing -- so an unconditional override here keeps the suite
    # hermetic and runnable on any laptop without a Redis container.
    RATELIMIT_STORAGE_URI = "memory://"

    # Lower bcrypt cost for faster test execution. Default is 12;
    # 4 is the minimum and makes auth/MFA tests ~100x faster.
    BCRYPT_LOG_ROUNDS = 4

    # NullPool closes connections immediately after use -- no pooling.
    # Prevents stale/leaked connections from holding locks that block
    # TRUNCATE between tests.  The driver arguments are every
    # configuration's: see ``_DRIVER_CONNECT_ARGS``.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": NullPool,
        "connect_args": dict(_DRIVER_CONNECT_ARGS),
    }


class ProdConfig(BaseConfig):
    """Production configuration -- no debug, require real secret key."""

    DEBUG = False
    # Prefers DATABASE_URL_APP (least-privilege ``shekel_app`` role)
    # when set, falling back to DATABASE_URL (owner ``shekel_user``).
    # ``__init__`` below rejects a missing URI; deployment scripts
    # blank DATABASE_URL_APP before ``create_app`` (empty-as-unset,
    # dotenv-proof) to force themselves
    # onto the owner role.  See ``_runtime_database_uri`` and
    # ``scripts/init_database.py`` for the full policy.
    SQLALCHEMY_DATABASE_URI = _runtime_database_uri()

    # Connection pool settings for production.  Made explicit rather than
    # relying on SQLAlchemy defaults to prevent surprises under load.
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Pool size per Gunicorn worker.  With 2 workers and pool_size=5,
        # the app uses up to 10 base connections + overflow.
        "pool_size": 5,
        # Allow 2 overflow connections per worker for burst traffic.
        # Total max per worker: pool_size + max_overflow = 7.
        # Total across 2 workers: 14 (well within PostgreSQL's default 100).
        "max_overflow": 2,
        # Seconds to wait for a connection from the pool before raising.
        # 30s is generous -- if the pool is exhausted for 30s, something
        # is very wrong and failing fast is better than queueing forever.
        "pool_timeout": 30,
        # Recycle connections after 30 minutes (1800s) to avoid using
        # connections that PostgreSQL or a firewall may have closed.
        # Prevents "connection reset by peer" errors after idle periods.
        "pool_recycle": 1800,
        # Pre-ping: test each connection before using it.  Catches stale
        # connections that pool_recycle missed (e.g. database restart
        # between recycle intervals).  Small overhead (~1ms per checkout)
        # but eliminates "server closed the connection unexpectedly" errors.
        "pool_pre_ping": True,
        # The driver arguments every configuration connects with -- the
        # 5-second connect timeout and no server-side prepared statements;
        # see ``_DRIVER_CONNECT_ARGS``.
        "connect_args": dict(_DRIVER_CONNECT_ARGS),
    }

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # __Host- prefix domain-pins the session cookie to this exact origin.
    # The prefix is enforced by the browser only when Secure=True is set
    # (already True above), Path="/" (Flask default), and the cookie has
    # NO Domain attribute (Flask never emits one when SESSION_COOKIE_DOMAIN
    # is unset, which is the default and the case here).  See audit
    # finding F-096.  Modern browsers (Chrome 49+, Firefox 49+, Safari
    # 11+) honor the prefix; older browsers ignore it gracefully and
    # treat it as an opaque cookie name.
    SESSION_COOKIE_NAME = "__Host-session"

    # Remember-me cookie hardening.  Flask-Login's Secure/HttpOnly/SameSite
    # defaults are False/False/None, so a 30-day authentication credential
    # would otherwise leak in cleartext on any HTTP fall-through and ride
    # cross-site requests in a login-CSRF chain.  Mirror the session
    # cookie's flags exactly so the longer-lived credential is at least
    # as protected.  See audit finding F-017.
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Production rate-limit storage: shared Redis on the backend Docker
    # network.  Defaults assume the bundled docker-compose ``redis``
    # service; an operator using a managed Redis can override via
    # ``RATELIMIT_STORAGE_URI`` in the environment.  See audit finding
    # F-034 and remediation Commit C-06.  ProdConfig.__init__ rejects
    # the ``memory://`` backend at import time -- a memory backend in
    # production silently re-introduces the multi-worker drift this
    # commit set out to close.
    RATELIMIT_STORAGE_URI = os.getenv(
        "RATELIMIT_STORAGE_URI", "redis://redis:6379/0"
    )

    # Production registration posture: defaults to disabled even when
    # the env var is completely absent (defense-in-depth against an
    # operator deleting the docker-compose interpolation default).
    # BaseConfig defaults to "true" so DevConfig and TestConfig retain
    # the open-registration ergonomics required by the test suite and
    # by manual local exploration; ProdConfig narrows that to "false".
    # See audit finding F-053 and remediation Commit C-34.
    REGISTRATION_ENABLED = os.getenv(
        "REGISTRATION_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    def __init__(self):
        """Validate production-critical settings on instantiation.

        Raises:
            ValueError: If ``SECRET_KEY`` is missing, matches a known
                placeholder, or is shorter than the minimum acceptable
                length, if ``DATABASE_URL`` is missing, or if
                ``RATELIMIT_STORAGE_URI`` resolves to the in-memory
                backend (which would silently disable shared rate
                limiting across Gunicorn workers).  Each branch emits
                a distinct, actionable error message so the operator
                knows exactly which secret is misconfigured.
        """
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY is required in production. "
                "Generate with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if (
            self.SECRET_KEY in _KNOWN_DEFAULT_SECRETS
            or self.SECRET_KEY.startswith("dev-only")
            or self.SECRET_KEY.startswith("replaced_by_docker_secret")
        ):
            raise ValueError(
                "SECRET_KEY matches a known placeholder; "
                "rotate to a secure random value before deploy."
            )
        if len(self.SECRET_KEY) < _MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                "SECRET_KEY must be at least "
                f"{_MIN_SECRET_KEY_LENGTH} characters."
            )
        if not self.SQLALCHEMY_DATABASE_URI:
            raise ValueError("DATABASE_URL must be set in production.")
        if self.RATELIMIT_STORAGE_URI.startswith("memory:"):
            raise ValueError(
                "RATELIMIT_STORAGE_URI must point to a shared backend "
                "(e.g. redis://redis:6379/0) in production. The in-memory "
                "backend silently fragments rate-limit counters across "
                "Gunicorn workers -- see audit finding F-034."
            )


# Map environment names to config classes for the factory.
CONFIG_MAP = {
    "development": DevConfig,
    "testing": TestConfig,
    "production": ProdConfig,
}
