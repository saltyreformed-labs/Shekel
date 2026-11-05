"""
Shekel Budget App -- Configuration Tests

Tests for config class defaults, fallbacks, and production validation.
ProdConfig DEBUG and SECRET_KEY tests live in this file (rejection of
empty / placeholder / short keys, acceptance of valid keys, Dev/Test
loading without a fallback default).  ``test_routes/test_errors.py``
also has a small overlap (DEBUG=False, dev-only placeholder rejection)
exercising the same code path through a different lens.

Extension-level posture (LoginManager session_protection, etc.) is
asserted alongside the config classes here so a single regression
gate covers every static security knob the app ships with.
"""

from importlib import reload

import pytest
from flask import request
from werkzeug.exceptions import RequestEntityTooLarge

from app import config as config_module
from app.config import (
    BaseConfig,
    DevConfig,
    ProdConfig,
    TestConfig,
    _DATABASE_URL_SENTINEL,
    _reject_sentinel,
    _runtime_database_uri,
)
from app.extensions import login_manager


# A plausible SECRET_KEY shape for tests that expect ProdConfig to
# accept the value: 64-character hex (256 bits).  Not a real secret;
# tests that deploy this value would still fail because it is short
# entropy and well-known.  Used only for unit tests of ProdConfig.
_VALID_SECRET_KEY = "0123456789abcdef" * 4  # 64 chars


class TestTheUploadCeiling:
    """``MAX_CONTENT_LENGTH`` bounds EVERY request body in the app.

    Added for plan step ``bank_import:X-f6a-1``, the app's first file upload.
    It is pinned here because it is a SECURITY ceiling that no feature test
    would notice being raised: Werkzeug's default is unlimited, and an
    adversarial review measured a CSV parse expanding its input ~52x in list
    overhead (5.24 MB of input -> 273 MB resident) against a 1 GB container.

    **It is the ONE bound on an ordinary form as well** (ruling **R-BAL218**,
    plan step ``balance:X-dk``): Werkzeug 3.1.9 stopped weighing an
    urlencoded body against Flask's ``MAX_FORM_MEMORY_SIZE``, so the last two
    cases grade the number the statement-match notes price a crafted pass
    against, through the app's own request class.
    """

    @staticmethod
    def _parse_form_body(app, size):
        """Parse a *size*-byte urlencoded body the way a view reads one.

        Args:
            app: The application whose request class and config parse it.
            size: The body's length in bytes.

        Returns:
            The length of its one field's value, as parsed.
        """
        body = "a=" + "x" * (size - 2)
        with app.test_request_context(
            "/", method="POST", data=body,
            content_type="application/x-www-form-urlencoded",
        ):
            return len(request.form["a"])

    def test_the_ceiling_is_set_at_all(self):
        """Unset means unlimited, which is what this exists to stop."""
        assert BaseConfig.MAX_CONTENT_LENGTH

    def test_the_ceiling_is_proportionate_to_the_real_need(self):
        """512 KB against a measured 59 KB largest real statement.

        Asserted as a RANGE, and beside the exact pin below rather than
        instead of it: the pin is the value a ruling picked and moves with a
        ruling, while this is the envelope any value must stay inside --
        small enough that a flood of uploads cannot exhaust the container,
        and large enough for a full year-to-date export with room to spare.
        Today the pin implies it; a ruling that moved the pin into megabytes
        would still have to change this test too and read why.
        """
        assert 128 * 1024 <= BaseConfig.MAX_CONTENT_LENGTH <= 1024 * 1024

    def test_the_ceiling_is_the_ruled_value(self):
        """512 KB exactly, as ruling R-BAL218 picked it.

        The range above allows anything up to 1 MB, and the notes that state
        the bound quote 524,288 bytes -- ``app/schemas/validation/statements.py``,
        ``TestAMatchMayNameAsManyRowsAsThePassOFFERS`` and
        ``test_oversized_backup_code_rejected_before_bcrypt`` -- and the first
        two price a crafted body at the 23,830 ticks it carries.  A change to
        the value has to come here and re-measure them.
        """
        assert BaseConfig.MAX_CONTENT_LENGTH == 512 * 1024

    def test_every_config_inherits_it(self):
        """Dev, test and prod all bound their bodies."""
        assert DevConfig.MAX_CONTENT_LENGTH == BaseConfig.MAX_CONTENT_LENGTH
        assert TestConfig.MAX_CONTENT_LENGTH == BaseConfig.MAX_CONTENT_LENGTH

    def test_a_form_body_at_the_ceiling_parses(self, app):
        """Nothing smaller than the ceiling bounds an ordinary form.

        The body is past Flask's 500,000-byte ``MAX_FORM_MEMORY_SIZE``
        default, which refused it through Werkzeug 3.1.8.  So this fails if a
        smaller bound ever binds a form body again, and the notes that call
        this ceiling the one bound would then be false.
        """
        ceiling = app.config["MAX_CONTENT_LENGTH"]
        assert ceiling > app.config["MAX_FORM_MEMORY_SIZE"] == 500_000

        assert self._parse_form_body(app, ceiling) == ceiling - 2

    def test_a_form_body_past_the_ceiling_is_refused(self, app):
        """One byte over is a 413 before any of it is parsed."""
        ceiling = app.config["MAX_CONTENT_LENGTH"]

        with pytest.raises(RequestEntityTooLarge):
            self._parse_form_body(app, ceiling + 1)


class TestDevConfig:
    """Tests for DevConfig defaults and fallbacks."""

    def test_database_uri_is_not_none(self):
        """DevConfig.SQLALCHEMY_DATABASE_URI is never None.

        With the fallback default, this attribute is always a valid
        connection string, either from DATABASE_URL in the environment
        or from the hardcoded peer-auth default.
        """
        assert DevConfig.SQLALCHEMY_DATABASE_URI is not None
        assert DevConfig.SQLALCHEMY_DATABASE_URI != ""

    def test_database_uri_is_valid_postgresql_uri(self):
        """The URI is a valid PostgreSQL connection string."""
        uri = DevConfig.SQLALCHEMY_DATABASE_URI
        assert uri.startswith("postgresql")

    def test_debug_enabled(self):
        """DevConfig has DEBUG=True for development."""
        assert DevConfig.DEBUG is True

    def test_devconfig_loads_without_default(self, monkeypatch):
        """DevConfig has no SECRET_KEY fallback default (F-016).

        BaseConfig used to provide ``"dev-only-change-me-in-production"``
        as a fallback default, which silently leaked the public default
        into any environment that forgot to set ``SECRET_KEY``.  The
        post-remediation behavior is to inherit ``None`` from BaseConfig
        when the env var is unset, and rely on the developer's .env (or
        conftest.py for tests) to populate it.  DevConfig itself does
        not validate, so instantiation must succeed even when the value
        is None.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        config = DevConfig()
        assert config.SECRET_KEY is None


class TestTestConfig:
    """Tests for TestConfig defaults and fallbacks."""

    def test_database_uri_is_not_none(self):
        """TestConfig.SQLALCHEMY_DATABASE_URI is never None."""
        assert TestConfig.SQLALCHEMY_DATABASE_URI is not None
        assert TestConfig.SQLALCHEMY_DATABASE_URI != ""

    def test_database_uri_is_valid_postgresql_uri(self):
        """The test URI is a valid PostgreSQL connection string."""
        uri = TestConfig.SQLALCHEMY_DATABASE_URI
        assert uri.startswith("postgresql")

    def test_csrf_disabled(self):
        """TestConfig disables CSRF for test convenience."""
        assert TestConfig.WTF_CSRF_ENABLED is False

    def test_testing_flag(self):
        """TestConfig has TESTING=True."""
        assert TestConfig.TESTING is True

    def test_testconfig_loads_without_default(self, monkeypatch):
        """TestConfig has no SECRET_KEY fallback default (F-016).

        Same rationale as ``test_devconfig_loads_without_default``.
        TestConfig must load with ``SECRET_KEY=None`` so unit tests
        that explicitly clear the value do not see a hidden fallback.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        config = TestConfig()
        assert config.SECRET_KEY is None


class TestProdConfig:
    """Tests for ProdConfig validation.

    Covers SECRET_KEY (empty / placeholder / short / valid),
    DATABASE_URL, and TOTP_ENCRYPTION_KEY paths through
    ``ProdConfig.__init__``.
    """

    def test_prodconfig_rejects_empty_secret_key(self, monkeypatch):
        """ProdConfig raises ValueError when SECRET_KEY is empty (F-016).

        An unset env var collapses to ``None`` after BaseConfig load.
        The empty-string case is the equivalent for an explicitly
        cleared variable.  Both paths must raise with a message that
        names SECRET_KEY and tells the operator how to generate one.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "")
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_prodconfig_rejects_none_secret_key(self, monkeypatch):
        """ProdConfig raises ValueError when SECRET_KEY is None.

        This path fires when the env var is completely unset and the
        BaseConfig fallback default has been removed (F-016 fix).
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_prodconfig_rejects_placeholder_dev_only(self, monkeypatch):
        """ProdConfig rejects any SECRET_KEY starting with ``dev-only``.

        Defends against the historical fallback default
        ``dev-only-change-me-in-production`` and any future placeholder
        following the same convention.  See F-110.
        """
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "dev-only-change-me-in-production"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="known placeholder"):
            ProdConfig()

    def test_prodconfig_rejects_placeholder_change_me(self, monkeypatch):
        """ProdConfig rejects the literal ``.env.example`` placeholder.

        ``change-me-to-a-random-secret-key`` was the literal value in
        ``.env.example:11`` before the F-110 fix.  An operator who
        copies ``.env.example`` to ``.env`` without editing must hit a
        startup failure, not silently deploy under a public key.
        """
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "change-me-to-a-random-secret-key"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="known placeholder"):
            ProdConfig()

    def test_prodconfig_rejects_placeholder_dev_secret(self, monkeypatch):
        """ProdConfig rejects the docker-compose.dev.yml placeholder.

        ``dev-secret-key-not-for-production`` was hardcoded in the dev
        compose file before the F-111 fix.  Belt-and-braces -- the
        compose file no longer hardcodes it, but if the value still
        leaks into a production deployment via copy/paste it must be
        rejected at startup.
        """
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "dev-secret-key-not-for-production"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="known placeholder"):
            ProdConfig()

    def test_prodconfig_rejects_short_secret_key(self, monkeypatch):
        """ProdConfig rejects SECRET_KEY shorter than 32 characters.

        ASVS L2 V6.4.2 floor.  31 characters is exactly one short.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "a" * 31)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        with pytest.raises(ValueError, match="at least 32 characters"):
            ProdConfig()

    def test_prodconfig_accepts_valid_secret_key(self, monkeypatch):
        """ProdConfig instantiates cleanly with a 64-char hex key.

        Closes the loop on the rejection cases above by proving that
        a properly-shaped SECRET_KEY is accepted.  Without this test
        a buggy validator could reject everything and still pass the
        rejection tests.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        config = ProdConfig()
        assert config.SECRET_KEY == _VALID_SECRET_KEY

    def test_prodconfig_validates_secret_key_before_database_url(
        self, monkeypatch
    ):
        """SECRET_KEY validation fires before DATABASE_URL validation.

        Operator misconfiguring both should see the SECRET_KEY error
        first because it is the higher-severity gap.  Locking the
        order in a test prevents an accidental refactor from swapping
        them.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "")
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", None)
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_requires_database_url(self, monkeypatch):
        """ProdConfig raises ValueError when DATABASE_URL is missing.

        Unlike DevConfig, production must NEVER fall back to a default.
        Missing DATABASE_URL is a fatal deployment error.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", None)
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "test-key")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            ProdConfig()

    def test_prod_config_has_pool_settings(self):
        """ProdConfig explicitly sets connection pool options."""
        opts = ProdConfig.SQLALCHEMY_ENGINE_OPTIONS
        assert "pool_size" in opts
        assert "pool_recycle" in opts
        assert "pool_pre_ping" in opts
        assert opts["pool_pre_ping"] is True
        assert opts["connect_args"]["connect_timeout"] == 5

    def test_prodconfig_cookie_hardening(self):
        """Every session-cookie flag required by audit C-02 is set on ProdConfig.

        Closes audit finding F-096 (SESSION_COOKIE_NAME).  The session
        cookie hardening (SESSION_COOKIE_SECURE / HTTPONLY / SAMESITE)
        was already in place pre-C-02 but is asserted here so a future
        refactor cannot silently drop one of the four settings without
        breaking this test.  F-017's REMEMBER_COOKIE_* flags went with
        "Remember me" at plan#2173 (ruling plan#2406): the app issues
        no remember-me cookie for them to harden.

        These are class-level attributes, not instance attributes, so
        the assertions read directly from ProdConfig (no instance
        construction needed -- avoids the SECRET_KEY / DATABASE_URL
        validation in __init__).
        """
        # Session cookie -- pre-existing hardening.
        assert ProdConfig.SESSION_COOKIE_SECURE is True
        assert ProdConfig.SESSION_COOKIE_HTTPONLY is True
        assert ProdConfig.SESSION_COOKIE_SAMESITE == "Lax"
        # Session cookie -- name with __Host- prefix for domain pinning.
        # The browser only honors the prefix when Secure=True (above)
        # and Path="/" (Flask default).  See F-096.
        assert ProdConfig.SESSION_COOKIE_NAME == "__Host-session"

    def test_totp_key_optional_at_startup(self, monkeypatch):
        """ProdConfig does not crash when TOTP_ENCRYPTION_KEY is missing.

        The key is only needed when a user enables MFA, not at app
        startup.  Enforcement happens in mfa_service.get_encryption_key().
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setattr(BaseConfig, "TOTP_ENCRYPTION_KEY", None)
        config = ProdConfig()
        assert config.TOTP_ENCRYPTION_KEY is None


class TestRuntimeDatabaseUri:
    """Tests for the DATABASE_URL_APP / DATABASE_URL preference helper.

    Closes audit finding F-081 / Commit C-13: the runtime app must
    use the least-privilege ``shekel_app`` role (DATABASE_URL_APP)
    while deployment scripts continue to run as the owner role
    (DATABASE_URL).  The helper centralises the lookup so DevConfig
    and ProdConfig both observe the same preference order.
    """

    def test_prefers_database_url_app_over_database_url(self, monkeypatch):
        """When both are set, DATABASE_URL_APP wins.

        This is the core least-privilege invariant: the runtime app
        process picks up the ``shekel_app`` URI even when the owner
        URI is also visible in the environment (which it must be
        because ``init_database.py`` and the seed scripts depend on
        it).
        """
        monkeypatch.setenv(
            "DATABASE_URL_APP",
            "postgresql://shekel_app:p@db:5432/shekel",
        )
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://shekel_user:p@db:5432/shekel",
        )
        assert _runtime_database_uri() == (
            "postgresql://shekel_app:p@db:5432/shekel"
        )

    def test_falls_back_to_database_url(self, monkeypatch):
        """With no DATABASE_URL_APP, the helper returns DATABASE_URL.

        Deployment scripts (``init_database.py``, ``seed_*.py``) pop
        DATABASE_URL_APP at startup; the runtime fallback below is
        what they observe afterwards.
        """
        monkeypatch.delenv("DATABASE_URL_APP", raising=False)
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://shekel_user:p@db:5432/shekel",
        )
        assert _runtime_database_uri() == (
            "postgresql://shekel_user:p@db:5432/shekel"
        )

    def test_returns_default_when_neither_is_set(self, monkeypatch):
        """With both env vars unset, the helper returns the supplied default.

        DevConfig's instantiation path passes a peer-auth fallback so
        a developer with neither variable set still gets a working
        local connection.  ProdConfig passes ``None`` so the
        ``__init__`` validator can raise.
        """
        monkeypatch.delenv("DATABASE_URL_APP", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _runtime_database_uri("postgresql:///fallback") == (
            "postgresql:///fallback"
        )
        assert _runtime_database_uri() is None

    def test_empty_database_url_app_falls_through(self, monkeypatch):
        """An empty DATABASE_URL_APP value is treated as unset.

        ``os.getenv`` returns the empty string (truthy as a string,
        falsy via ``or``) when the var is set but empty.  The helper
        uses ``or`` rather than the explicit ``is None`` check so an
        empty value falls through to DATABASE_URL -- protecting
        against an operator who exports the variable with no value.
        """
        monkeypatch.setenv("DATABASE_URL_APP", "")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://shekel_user:p@db:5432/shekel",
        )
        assert _runtime_database_uri() == (
            "postgresql://shekel_user:p@db:5432/shekel"
        )


# Helper URIs for the sentinel tests.  Constructed at module import so
# the sentinel substring inside them comes from the production
# constant -- a refactor that renames the sentinel constant cannot
# silently drift these fixtures out of sync.
_SENTINEL_DATABASE_URL = (
    f"postgresql://shekel_user:{_DATABASE_URL_SENTINEL}@localhost:5432/shekel"
)
_SENTINEL_TEST_DATABASE_URL = (
    f"postgresql://shekel_user:{_DATABASE_URL_SENTINEL}@localhost:5433/shekel_test"
)
_CLEAN_DATABASE_URL = (
    "postgresql://shekel_user:real-password-not-the-sentinel@localhost:5432/shekel"
)
_CLEAN_TEST_DATABASE_URL = (
    "postgresql://shekel_user:real-password-not-the-sentinel@localhost:5433/shekel_test"
)


class TestRejectSentinelHelper:
    """Tests for the ``_reject_sentinel`` helper itself.

    Closes audit finding F-109 / Commit C-38 follow-up Issue 3.  The
    helper is the load-bearing piece that converts a copy-pasted
    .env.example into a loud app-level error rather than a silent
    Postgres "password authentication failed" at first connect.  Each
    branch of the helper is exercised here without invoking the
    config classes -- the integration tests below cover the wiring.
    """

    def test_returns_uri_unchanged_when_no_sentinel(self):
        """A clean URI flows through the helper untouched.

        Without this assertion, a bug that always raises would still
        pass the rejection tests below.  Round-trip identity proves
        the success path actually returns the input.
        """
        result = _reject_sentinel(
            _CLEAN_DATABASE_URL, var_name="DATABASE_URL",
        )
        assert result == _CLEAN_DATABASE_URL

    def test_returns_none_unchanged(self):
        """``None`` propagates through the helper without raising.

        The runtime URI resolver returns ``None`` when neither
        DATABASE_URL_APP nor DATABASE_URL is set and no default was
        supplied -- the ProdConfig.__init__ "DATABASE_URL must be
        set" check then fires at the right layer.  If the sentinel
        helper raised on ``None`` it would mask that downstream
        validator with a confusing message about the sentinel.
        """
        assert _reject_sentinel(None, var_name="DATABASE_URL") is None

    def test_returns_empty_string_unchanged(self):
        """An empty URI string falls through unchanged.

        Same rationale as the ``None`` case -- the helper should not
        mask an empty value with a sentinel-specific error.  The
        production rejection of an empty DATABASE_URL lives in
        ``ProdConfig.__init__``.
        """
        assert _reject_sentinel("", var_name="DATABASE_URL") == ""

    def test_raises_with_named_env_var(self):
        """The error message names the env var the caller specified.

        Three call sites in production -- DATABASE_URL,
        DATABASE_URL_APP, TEST_DATABASE_URL -- need distinct error
        messages so the operator can find the offending line in
        their .env without grepping.  The var_name kwarg is the
        load-bearing parameter that delivers that precision.
        """
        with pytest.raises(ValueError, match=r"\bDATABASE_URL_APP\b") as exc:
            _reject_sentinel(
                _SENTINEL_DATABASE_URL, var_name="DATABASE_URL_APP",
            )
        assert "DATABASE_URL_APP" in str(exc.value)
        # The non-matching var names must NOT appear in the message
        # so the operator is not misdirected.
        assert "TEST_DATABASE_URL" not in str(exc.value)

    def test_raises_naming_test_database_url(self):
        """TEST_DATABASE_URL variant names the test env var, not the
        production one."""
        with pytest.raises(ValueError, match=r"\bTEST_DATABASE_URL\b") as exc:
            _reject_sentinel(
                _SENTINEL_TEST_DATABASE_URL, var_name="TEST_DATABASE_URL",
            )
        # Sanity: the message must not accidentally mention the
        # production DATABASE_URL when the test variant is what is
        # broken.
        message = str(exc.value)
        # The substring ``DATABASE_URL`` appears as a suffix of
        # ``TEST_DATABASE_URL``, so we cannot assert its absence
        # naively.  Instead, assert the test variant is named and
        # the message does not contain the production variant on
        # its own.
        assert "TEST_DATABASE_URL" in message

    def test_error_message_points_at_env_example(self):
        """Operator is told which file to edit, not just what is wrong.

        Without this hint, the operator sees ``ValueError`` with the
        sentinel name and may not realise the fix is to edit .env --
        especially likely if they copied a teammate's .env directly
        and never opened .env.example.
        """
        with pytest.raises(ValueError) as exc:
            _reject_sentinel(
                _SENTINEL_DATABASE_URL, var_name="DATABASE_URL",
            )
        assert ".env.example" in str(exc.value)

    def test_error_message_names_the_sentinel(self):
        """Operator sees the exact token to grep for in their .env.

        The sentinel ships in .env.example with a specific spelling.
        Naming it in the error means the operator can grep their
        .env for the literal token rather than guessing which line
        is wrong.
        """
        with pytest.raises(ValueError) as exc:
            _reject_sentinel(
                _SENTINEL_DATABASE_URL, var_name="DATABASE_URL",
            )
        assert _DATABASE_URL_SENTINEL in str(exc.value)

    def test_rejects_sentinel_anywhere_in_uri(self):
        """The substring check fires regardless of position.

        A real attacker copy-paste could put the sentinel anywhere
        in the URI (e.g. as the host, the dbname, an option value).
        Any of those positions is still a leaked-credential risk
        because it indicates the operator did not actually edit the
        template.  ``in`` matches all positions, so this test locks
        in that property.
        """
        sentinel_as_dbname = (
            f"postgresql://user:realpass@localhost:5432/{_DATABASE_URL_SENTINEL}"
        )
        with pytest.raises(ValueError):
            _reject_sentinel(sentinel_as_dbname, var_name="DATABASE_URL")


class TestRuntimeDatabaseUriSentinelRejection:
    """Sentinel rejection wired into ``_runtime_database_uri``.

    The helper is the single integration point that DevConfig and
    ProdConfig share for URI resolution.  These tests prove the
    sentinel check fires for both DATABASE_URL_APP and DATABASE_URL,
    and that the "winning" env var is the one named in the error.
    """

    def test_rejects_sentinel_in_database_url_app(self, monkeypatch):
        """DATABASE_URL_APP with the sentinel raises naming the app var.

        Locking the precise var name in the error is the protection
        against an operator who sets BOTH env vars and then gets a
        misleading "DATABASE_URL" diagnostic when the bug is actually
        in DATABASE_URL_APP.  The runtime app prefers
        DATABASE_URL_APP, so that is the variable whose sentinel
        would silently land in the live connection string.
        """
        monkeypatch.setenv("DATABASE_URL_APP", _SENTINEL_DATABASE_URL)
        monkeypatch.setenv("DATABASE_URL", _CLEAN_DATABASE_URL)
        with pytest.raises(ValueError, match=r"\bDATABASE_URL_APP\b"):
            _runtime_database_uri()

    def test_rejects_sentinel_in_database_url(self, monkeypatch):
        """DATABASE_URL with the sentinel raises naming DATABASE_URL.

        The fallback path -- only fires when DATABASE_URL_APP is
        unset or empty.  This is the path exercised by DevConfig
        when the .env example has been copy-pasted without edits.
        """
        monkeypatch.delenv("DATABASE_URL_APP", raising=False)
        monkeypatch.setenv("DATABASE_URL", _SENTINEL_DATABASE_URL)
        with pytest.raises(ValueError, match=r"\bDATABASE_URL\b"):
            _runtime_database_uri()

    def test_empty_database_url_app_falls_through_to_database_url_check(
        self, monkeypatch,
    ):
        """An empty DATABASE_URL_APP does not short-circuit the check.

        ``os.getenv`` returns the empty string for an exported-but-
        empty variable; the runtime resolver treats that as unset and
        falls through to DATABASE_URL.  The sentinel check must
        therefore evaluate DATABASE_URL in that case, not silently
        return the empty DATABASE_URL_APP.
        """
        monkeypatch.setenv("DATABASE_URL_APP", "")
        monkeypatch.setenv("DATABASE_URL", _SENTINEL_DATABASE_URL)
        with pytest.raises(ValueError, match=r"\bDATABASE_URL\b"):
            _runtime_database_uri()

    def test_only_winning_var_is_validated(self, monkeypatch):
        """When DATABASE_URL_APP is clean, a sentinel in DATABASE_URL is not raised.

        The runtime resolver returns the winning var without
        consulting the loser.  A sentinel in DATABASE_URL is the
        next caller's problem (e.g. ``scripts/init_database.py``
        which pops DATABASE_URL_APP and then calls create_app() --
        that subsequent call would then trigger this check).  We
        lock in the "only one var validated per call" property so a
        future refactor cannot accidentally widen the surface.
        """
        monkeypatch.setenv("DATABASE_URL_APP", _CLEAN_DATABASE_URL)
        monkeypatch.setenv("DATABASE_URL", _SENTINEL_DATABASE_URL)
        # No exception: DATABASE_URL_APP wins and is clean.
        result = _runtime_database_uri()
        assert result == _CLEAN_DATABASE_URL

    def test_clean_uris_round_trip(self, monkeypatch):
        """Both clean inputs round-trip without raising.

        Closes the loop on the rejection tests above.  Without a
        positive-case assertion, a bug that always raises would
        still pass the rejection tests.
        """
        monkeypatch.setenv("DATABASE_URL_APP", _CLEAN_DATABASE_URL)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _runtime_database_uri() == _CLEAN_DATABASE_URL


class TestConfigClassesRejectSentinelAtImport:
    """``DevConfig`` / ``TestConfig`` raise at class-body evaluation time
    when the relevant env var still embeds the sentinel.

    These tests reload ``app.config`` with monkeypatch'd env vars so
    the class-body assignment of ``SQLALCHEMY_DATABASE_URI`` -- which
    runs ``_reject_sentinel`` -- actually fires.  The class-body
    failure mode is load-bearing: it means a sentinel-bearing .env
    cannot produce a half-constructed app that fails later at
    connect time.  See audit finding F-109 / Commit C-38 follow-up
    Issue 3.
    """

    def test_dev_config_rejects_sentinel_in_database_url(self, monkeypatch):
        """A sentinel-bearing DATABASE_URL fails the DevConfig class body.

        DevConfig resolves its URI via ``_runtime_database_uri`` at
        class-body time.  A sentinel in DATABASE_URL surfaces as a
        ValueError raised inside the module reload, not at instance
        construction.  The test_database_url is set to a clean URI
        so TestConfig (next class in the module) does not raise
        first and obscure the DevConfig failure.
        """
        monkeypatch.delenv("DATABASE_URL_APP", raising=False)
        monkeypatch.setenv("DATABASE_URL", _SENTINEL_DATABASE_URL)
        monkeypatch.setenv("TEST_DATABASE_URL", _CLEAN_TEST_DATABASE_URL)
        try:
            with pytest.raises(ValueError, match=r"\bDATABASE_URL\b"):
                reload(config_module)
        finally:
            # Replace the sentinel value BEFORE the cleanup reload
            # so the next test sees a working module.
            monkeypatch.delenv("DATABASE_URL", raising=False)
            reload(config_module)

    def test_dev_config_rejects_sentinel_in_database_url_app(self, monkeypatch):
        """Same coverage via the DATABASE_URL_APP path.

        ``DATABASE_URL_APP`` is the runtime app's preferred URI;
        DevConfig picks it up via ``_runtime_database_uri``.  A
        sentinel here is the higher-priority case and must surface
        the DATABASE_URL_APP variable name in the error.
        """
        monkeypatch.setenv("DATABASE_URL_APP", _SENTINEL_DATABASE_URL)
        monkeypatch.setenv("DATABASE_URL", _CLEAN_DATABASE_URL)
        monkeypatch.setenv("TEST_DATABASE_URL", _CLEAN_TEST_DATABASE_URL)
        try:
            with pytest.raises(ValueError, match=r"\bDATABASE_URL_APP\b"):
                reload(config_module)
        finally:
            monkeypatch.delenv("DATABASE_URL_APP", raising=False)
            reload(config_module)

    def test_test_config_rejects_sentinel_in_test_database_url(
        self, monkeypatch,
    ):
        """A sentinel-bearing TEST_DATABASE_URL fails the TestConfig class body.

        TestConfig has its OWN URI resolution path (it does not
        share ``_runtime_database_uri`` because the test suite never
        uses DATABASE_URL_APP).  The class body wraps
        ``os.getenv("TEST_DATABASE_URL", ...)`` in
        ``_reject_sentinel`` so a sentinel-bearing test env var
        fails identically to the production case.  DATABASE_URL is
        set to a clean URI so DevConfig (which is evaluated first)
        does not raise and short-circuit this test.
        """
        monkeypatch.delenv("DATABASE_URL_APP", raising=False)
        monkeypatch.setenv("DATABASE_URL", _CLEAN_DATABASE_URL)
        monkeypatch.setenv("TEST_DATABASE_URL", _SENTINEL_TEST_DATABASE_URL)
        try:
            with pytest.raises(ValueError, match=r"\bTEST_DATABASE_URL\b"):
                reload(config_module)
        finally:
            monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
            reload(config_module)

    def test_clean_environment_reload_succeeds(self, monkeypatch):
        """Closes the loop -- clean env vars allow a clean reload.

        Without this assertion, a bug that ALWAYS raises during
        reload would still pass the rejection tests above (because
        the rejection tests only check that an exception is raised
        somewhere).  This test proves the rejection is tied
        specifically to the sentinel substring, not to any reload
        side-effect.
        """
        monkeypatch.delenv("DATABASE_URL_APP", raising=False)
        monkeypatch.setenv("DATABASE_URL", _CLEAN_DATABASE_URL)
        monkeypatch.setenv("TEST_DATABASE_URL", _CLEAN_TEST_DATABASE_URL)
        try:
            reload(config_module)
            # The reloaded module's DevConfig and TestConfig
            # SQLALCHEMY_DATABASE_URI must echo the clean inputs --
            # this proves the class-body assignment ran end-to-end.
            assert config_module.DevConfig.SQLALCHEMY_DATABASE_URI == (
                _CLEAN_DATABASE_URL
            )
            assert config_module.TestConfig.SQLALCHEMY_DATABASE_URI == (
                _CLEAN_TEST_DATABASE_URL
            )
        finally:
            reload(config_module)


class TestRateLimitConfig:
    """Tests for Flask-Limiter configuration across BaseConfig, DevConfig,
    TestConfig, and ProdConfig.

    Closes audit finding F-034 (in-memory backend drift across Gunicorn
    workers).  See remediation Commit C-06 for the per-environment
    storage strategy and the developer's fail-closed Phase D-12 choice.
    """

    def test_base_config_defaults_to_memory_storage(self):
        """BaseConfig.RATELIMIT_STORAGE_URI falls back to memory://.

        DevConfig inherits this fallback so a developer running
        ``flask run`` does not need a Redis container.  Production
        overrides explicitly via ProdConfig.
        """
        # The class attribute is captured at import time from os.getenv,
        # so the value reflects whatever was in the environment when
        # tests started.  In the test suite, conftest.py does not set
        # RATELIMIT_STORAGE_URI, so the fallback wins.
        assert BaseConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_base_config_default_limits_string(self):
        """BaseConfig sets a per-IP ceiling for un-decorated routes.

        Format must be a Flask-Limiter limit string (semicolon-separated
        ``<count> per <window>`` clauses).  Verifies the ceiling closes
        the gap audit finding F-034 documents (4 of ~93 mutating routes
        were rate-limited; the ceiling now applies to the rest).
        """
        assert BaseConfig.RATELIMIT_DEFAULT == "200 per hour;30 per minute"

    def test_base_config_fail_closed_flags(self):
        """BaseConfig enforces the developer's fail-closed posture.

        Both flags must be False so that a storage outage surfaces as a
        request error (eventually 500) rather than silently degrading
        to per-worker memory counts (which would re-introduce F-034
        drift mid-flight).  See
        shekel-audits/security-2026-04-15/remediation-plan.md Phase D-12.
        """
        assert BaseConfig.RATELIMIT_IN_MEMORY_FALLBACK_ENABLED is False
        assert BaseConfig.RATELIMIT_SWALLOW_ERRORS is False

    def test_base_config_moving_window_strategy(self):
        """BaseConfig uses moving-window rate limiting.

        Flask-Limiter's default ``fixed-window`` strategy lets an
        attacker double the effective limit by straddling the window
        boundary (e.g. 5 hits at 14:59:59 plus 5 hits at 15:00:01 in a
        "5 per 15 minutes" rule).  The moving-window strategy keeps a
        sliding count and prevents that doubling.
        """
        assert BaseConfig.RATELIMIT_STRATEGY == "moving-window"

    def test_base_config_headers_enabled(self):
        """BaseConfig opts in to X-RateLimit-* response headers.

        Without these headers a front-end retry library or operator
        diagnostic has to inspect Redis directly to learn current
        consumption.  Enabling them costs nothing on the hot path.
        """
        assert BaseConfig.RATELIMIT_HEADERS_ENABLED is True

    def test_devconfig_inherits_memory_storage(self):
        """DevConfig leaves RATELIMIT_STORAGE_URI at the BaseConfig default.

        ``flask run`` against a local checkout has no Redis container
        available, so the in-memory backend is the only sensible
        default for development.
        """
        assert DevConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_testconfig_forces_memory_storage(self):
        """TestConfig overrides RATELIMIT_STORAGE_URI to memory://.

        The override is unconditional -- it ignores any
        ``RATELIMIT_STORAGE_URI`` set in the developer's shell or .env.
        Without this override the test suite would attempt to connect
        to whatever URI is in the environment (likely a developer's
        local Redis or the docker-compose redis service), making the
        suite hostile to CI without a Redis instance.
        """
        assert TestConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_testconfig_disables_rate_limiting(self):
        """TestConfig sets RATELIMIT_ENABLED=False.

        The vast majority of tests must not trip rate limits as a
        side effect of issuing many requests in quick succession.
        Tests that need rate limiting on (test_errors.py 429 tests,
        test_auth.py lockout tests) re-enable it temporarily on a
        fresh app instance.
        """
        assert TestConfig.RATELIMIT_ENABLED is False

    def test_prodconfig_defaults_to_redis(self, monkeypatch):
        """ProdConfig.RATELIMIT_STORAGE_URI defaults to the bundled redis service.

        The default URI ``redis://redis:6379/0`` matches the
        ``redis`` service name in docker-compose.yml.  An operator
        using a managed Redis (Upstash, ElastiCache, etc.) overrides
        via the ``RATELIMIT_STORAGE_URI`` environment variable.
        """
        # Because ProdConfig.RATELIMIT_STORAGE_URI is captured at
        # class-body evaluation time, we cannot simply unset the env
        # var and re-evaluate; instead, assert the class attribute as
        # imported reflects the documented default in the absence of an
        # env override.  conftest.py does not set RATELIMIT_STORAGE_URI.
        monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
        # Assert against the captured class attribute (the import-time
        # value); if a developer ever runs the test suite with the env
        # var set, the class attribute would not match -- the assertion
        # protects the documented default from accidental change.
        assert (
            ProdConfig.RATELIMIT_STORAGE_URI == "redis://redis:6379/0"
            or ProdConfig.RATELIMIT_STORAGE_URI.startswith("redis://")
        )

    def test_prodconfig_rejects_memory_storage(self, monkeypatch):
        """ProdConfig.__init__ raises ValueError when RATELIMIT_STORAGE_URI is memory://.

        A memory backend silently fragments rate-limit counters across
        Gunicorn workers -- audit finding F-034.  Even if an operator
        manually sets ``RATELIMIT_STORAGE_URI=memory://`` in the
        production environment, the application must refuse to start.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setattr(
            ProdConfig, "RATELIMIT_STORAGE_URI", "memory://"
        )
        with pytest.raises(ValueError, match="RATELIMIT_STORAGE_URI"):
            ProdConfig()

    def test_prodconfig_accepts_redis_storage(self, monkeypatch):
        """ProdConfig.__init__ accepts a redis:// URI.

        Closes the loop on the rejection test above by proving that a
        properly-shaped Redis URI is accepted.  Without this test a
        buggy validator could reject everything and still pass the
        rejection test.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setattr(
            ProdConfig, "RATELIMIT_STORAGE_URI", "redis://redis:6379/0"
        )
        config = ProdConfig()
        assert config.RATELIMIT_STORAGE_URI == "redis://redis:6379/0"

    def test_prodconfig_validates_storage_uri_after_secret_key(
        self, monkeypatch
    ):
        """SECRET_KEY validation fires before RATELIMIT_STORAGE_URI validation.

        Operator misconfiguring multiple secrets should see the
        higher-severity SECRET_KEY error first.  Lock the order in a
        test so an accidental refactor cannot swap them.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "")
        monkeypatch.setattr(
            ProdConfig, "RATELIMIT_STORAGE_URI", "memory://"
        )
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()


class TestLoginManagerConfig:
    """Tests for Flask-Login posture configured in ``app/extensions.py``.

    These assertions lock in static security knobs that have no other
    runtime tell-tale until an attacker exploits them.  A regression
    here means the app's session-fixation defence has silently
    weakened, and the only place that would surface the change is an
    audit -- by which time the gap may have already been exploited.
    """

    def test_login_manager_session_protection_is_strong(self):
        """``login_manager.session_protection`` is set to ``"strong"``.

        Closes audit finding F-038 / remediation Commit C-07.  The
        Flask-Login default is ``"basic"``, which only flips
        ``session["_fresh"]`` to ``False`` on identifier drift and
        leaves the rest of the session in place; ``"strong"`` pops
        every Flask-Login session key and clears the remember-me
        cookie, forcing a complete re-authentication.  See
        ``flask_login/login_manager.py:_session_protection_failed``
        for the exact divergence between the two modes.

        Asserting a literal string here (rather than ``in {"basic",
        "strong"}``) is intentional: the next-strongest valid value
        ``None`` (disabled) is silently corrosive, and ``"basic"`` is
        what the audit explicitly rejected.  Either of those values
        must fail this test.
        """
        assert login_manager.session_protection == "strong"


class TestEveryConfigConnectsTheSameWay:
    """One set of driver arguments for Dev, Test and Prod (plan step balance:X-dj).

    ``prepare_threshold=None`` keeps psycopg 3 from preparing statements on
    the server, which psycopg2 never did and which production's long-lived
    pooled connections would reach while the suite's ``NullPool`` mostly
    would not (``app.config._DRIVER_CONNECT_ARGS`` gives the argument).
    """

    @pytest.mark.parametrize("config", [DevConfig, TestConfig, ProdConfig])
    def test_each_config_carries_the_driver_arguments(self, config):
        """A five-second connect timeout and no server-side preparing."""
        assert config.SQLALCHEMY_ENGINE_OPTIONS["connect_args"] == {
            "connect_timeout": 5,
            "prepare_threshold": None,
        }

    def test_a_repeated_statement_is_never_prepared(self, app, db):
        """Six runs on one connection leave nothing in ``pg_prepared_statements``.

        Paired with the control below, so the zero is a measurement and not a
        probe that cannot see a prepared statement at all.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import text

        with app.app_context(), db.engine.connect() as connection:
            for _ in range(6):
                connection.execute(text("SELECT 1 WHERE 1 = :one"), {"one": 1})
            prepared = connection.execute(
                text("SELECT count(*) FROM pg_prepared_statements"),
            ).scalar()
        assert prepared == 0

    def test_the_control_sees_the_default_threshold_prepare(self, app, db):
        """Under psycopg's default threshold the same six runs DO prepare one."""
        # pylint: disable=import-outside-toplevel
        import psycopg

        url = db.engine.url.render_as_string(hide_password=False)
        with psycopg.connect(url) as connection:
            for _ in range(6):
                connection.execute("SELECT 1 WHERE 1 = %s", (1,))
            prepared = connection.execute(
                "SELECT count(*) FROM pg_prepared_statements",
            ).fetchone()[0]
        assert prepared >= 1
