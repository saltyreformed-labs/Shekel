"""Configuration tests: ProdConfig's refusals and defaults, and create_app's build of a
configuration, which is what runs them at every production boot.
"""

import pytest

from app import create_app
from app.config import BaseConfig, ProdConfig

from ._helpers import _VALID_SECRET_KEY, _STORE_PASSWORD


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
        monkeypatch.setenv("VALKEY_PASSWORD", _STORE_PASSWORD)
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
        monkeypatch.setenv("VALKEY_PASSWORD", _STORE_PASSWORD)
        config = ProdConfig()
        assert config.TOTP_ENCRYPTION_KEY is None


class TestCreateAppBuildsTheConfiguration:
    """``create_app`` builds its configuration, so ProdConfig's refusals run (finding plan#2652).

    It once handed ``from_object`` the class itself, which copies attributes
    and instantiates nothing, so no boot ran ``ProdConfig.__init__``: measured
    2026-11-02, ``create_app("production")`` booted with a one-character
    SECRET_KEY.  Each case refuses in ``create_app`` before any extension
    binds, so none touches a database or the limiter.
    """

    def test_a_short_secret_key_stops_the_production_boot(self, monkeypatch):
        """A one-character SECRET_KEY: ``create_app`` raises, naming it."""
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "x")
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setenv("VALKEY_PASSWORD", _STORE_PASSWORD)
        with pytest.raises(ValueError, match="SECRET_KEY must be at least"):
            create_app("production")

    def test_a_missing_store_password_stops_the_production_boot(self, monkeypatch):
        """Every other setting valid and no store password: ``create_app`` raises, naming it."""
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.delenv("VALKEY_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="VALKEY_PASSWORD is not set"):
            create_app("production")

    def test_a_missing_store_password_stops_the_dev_container_boot(self, monkeypatch):
        """The containerized dev app's configuration refuses in ``create_app`` too."""
        monkeypatch.delenv("VALKEY_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="VALKEY_PASSWORD is not set"):
            create_app("development-container")
