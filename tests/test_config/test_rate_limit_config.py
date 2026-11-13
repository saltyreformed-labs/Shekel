"""Configuration tests: the rate limiter's settings, and the store each configuration names and
logs in to.
"""

from importlib import reload

import pytest
from flask import Config

from app import config as config_module
from app.config import BaseConfig, DevConfig, DevContainerConfig, ProdConfig
from app.config import TestConfig as _TestConfig

from ._helpers import _VALID_SECRET_KEY, _STORE_PASSWORD, _STORE_SOCKET


class TestRateLimitConfig:
    """Tests for Flask-Limiter configuration across BaseConfig, DevConfig,
    TestConfig, and ProdConfig.

    Closes audit finding F-034 (in-memory backend drift across Gunicorn
    workers).  See remediation Commit C-06 for the per-environment
    storage strategy and the developer's fail-closed Phase D-12 choice.
    """

    def test_base_config_stores_counters_in_memory(self):
        """BaseConfig.RATELIMIT_STORAGE_URI is memory://.

        DevConfig and TestConfig keep it, so host ``flask run`` and the
        suite need no Valkey; the configurations beside Valkey name its
        socket instead (ruling plan#2633).
        """
        assert BaseConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_no_environment_variable_moves_the_store(self, monkeypatch):
        """RATELIMIT_STORAGE_URI in the environment changes no configuration.

        Ruling plan#2633: the deployment fixes where the store is, so the
        app names it in code and reads no setting for it.  Each class is
        re-evaluated under the variable (module reload), and each still
        names its own store.
        """
        monkeypatch.setenv("RATELIMIT_STORAGE_URI", "valkey://elsewhere:6379/0")
        try:
            reload(config_module)
            stores = {
                name: getattr(config_module, name).RATELIMIT_STORAGE_URI
                for name in ("BaseConfig", "DevConfig", "TestConfig",
                             "DevContainerConfig", "ProdConfig")
            }
        finally:
            monkeypatch.delenv("RATELIMIT_STORAGE_URI")
            reload(config_module)
        assert stores == {
            "BaseConfig": "memory://",
            "DevConfig": "memory://",
            "TestConfig": "memory://",
            "DevContainerConfig": _STORE_SOCKET,
            "ProdConfig": _STORE_SOCKET,
        }

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
        diagnostic has to inspect Valkey directly to learn current
        consumption.  Enabling them costs nothing on the hot path.
        """
        assert BaseConfig.RATELIMIT_HEADERS_ENABLED is True

    def test_devconfig_stays_in_memory_given_a_password(self, monkeypatch):
        """Host ``flask run`` (DevConfig) is in memory even with a password.

        Ruling plan#2619: host ``flask run`` cannot reach dev's Valkey,
        which listens only on a socket in a volume.  Ruling plan#2651: the
        containerized dev app has a configuration of its own, so a store
        password in the host's environment switches nothing here.
        """
        monkeypatch.setenv("VALKEY_PASSWORD", _STORE_PASSWORD)
        config = DevConfig()
        assert config.RATELIMIT_STORAGE_URI == "memory://"
        assert not hasattr(config, "RATELIMIT_STORAGE_OPTIONS")

    def test_testconfig_stores_counters_in_memory(self):
        """TestConfig keeps BaseConfig's memory://, so the suite needs no Valkey."""
        assert _TestConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_testconfig_disables_rate_limiting(self):
        """TestConfig sets RATELIMIT_ENABLED=False.

        The vast majority of tests must not trip rate limits as a
        side effect of issuing many requests in quick succession.
        Tests that need rate limiting on (test_errors.py 429 tests,
        test_auth/test_login_and_logout.py lockout tests) re-enable it
        temporarily on a fresh app instance.
        """
        assert _TestConfig.RATELIMIT_ENABLED is False

    @pytest.mark.parametrize("config", [DevContainerConfig, ProdConfig])
    def test_the_configs_beside_valkey_name_its_socket(self, config):
        """The store's address, in code: Valkey's socket file.

        The socket ``deploy/valkey/start.sh`` makes in the volume the app
        mounts at /run/valkey (ruling plan#2612), and a scheme that makes
        limits use the valkey client, the one the lock holds (ruling
        R-BAL298), over a socket file.
        """
        assert config.RATELIMIT_STORAGE_URI == _STORE_SOCKET

    @pytest.mark.parametrize("given", [None, ""])
    def test_devcontainerconfig_refuses_to_build_without_the_password(
        self, monkeypatch, given
    ):
        """The containerized dev app refuses to boot without the store's password.

        Ruling plan#2651: it needs the store as production does, so a
        missing or empty password stops it by name rather than leaving
        every rate-limited request to fail on a store it cannot log in to.
        """
        if given is None:
            monkeypatch.delenv("VALKEY_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("VALKEY_PASSWORD", given)
        with pytest.raises(ValueError, match="VALKEY_PASSWORD is not set"):
            DevContainerConfig()

    def test_devcontainerconfig_logs_in_as_shekel_with_the_password(
        self, monkeypatch
    ):
        """Its store options: the ``shekel`` ACL user and the password read.

        The password rides as a storage option beside the address, never
        inside it, so no character it holds can change what the address
        names (plan#2586's review finding 7).
        """
        monkeypatch.setenv("VALKEY_PASSWORD", "p/a?s#s@w:o%rd")
        config = DevContainerConfig()
        assert config.RATELIMIT_STORAGE_OPTIONS == {
            "username": "shekel", "password": "p/a?s#s@w:o%rd",
        }
        assert config.RATELIMIT_STORAGE_URI == _STORE_SOCKET

    @pytest.mark.parametrize("given", [None, ""])
    def test_prodconfig_refuses_to_build_without_the_password(
        self, monkeypatch, given
    ):
        """ProdConfig refuses a missing or empty store password by name.

        Ruling plan#2633: with the limiter fail-closed, an app that could
        not log in to its store would fail every rate-limited request
        while the boot and /health passed.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        if given is None:
            monkeypatch.delenv("VALKEY_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("VALKEY_PASSWORD", given)
        with pytest.raises(ValueError, match="VALKEY_PASSWORD is not set"):
            ProdConfig()

    def test_prodconfig_logs_in_as_shekel_with_the_password(self, monkeypatch):
        """The control for the refusals above: a password is accepted and carried."""
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel"
        )
        monkeypatch.setenv("VALKEY_PASSWORD", _STORE_PASSWORD)
        config = ProdConfig()
        assert config.RATELIMIT_STORAGE_OPTIONS == {
            "username": "shekel", "password": _STORE_PASSWORD,
        }

    @pytest.mark.parametrize(("secret_key", "database_uri", "named"), [
        ("", "postgresql:///shekel", "SECRET_KEY is required"),
        (_VALID_SECRET_KEY, None, "DATABASE_URL"),
    ])
    def test_prodconfig_checks_the_password_after_its_other_settings(
        self, monkeypatch, secret_key, database_uri, named
    ):
        """SECRET_KEY and DATABASE_URL are named before the store's password.

        An operator misconfiguring several settings sees the
        higher-severity error first; locking the order here stops a
        refactor from swapping them.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", secret_key)
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", database_uri)
        monkeypatch.delenv("VALKEY_PASSWORD", raising=False)
        with pytest.raises(ValueError, match=named):
            ProdConfig()

    def test_the_options_reach_flask_config(self, monkeypatch):
        """A configuration built, as ``create_app`` builds one, hands Flask its options.

        ``from_object`` copies a configuration object's upper-case
        attributes, the ones set when it was built among them, so the
        store's login reaches ``app.config`` for Flask-Limiter to read.
        """
        monkeypatch.setenv("VALKEY_PASSWORD", _STORE_PASSWORD)
        config = Config(root_path=".")
        config.from_object(DevContainerConfig())
        assert config["RATELIMIT_STORAGE_URI"] == _STORE_SOCKET
        assert config["RATELIMIT_STORAGE_OPTIONS"] == {
            "username": "shekel", "password": _STORE_PASSWORD,
        }
