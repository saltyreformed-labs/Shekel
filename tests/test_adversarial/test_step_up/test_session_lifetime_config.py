"""C-10's configuration tests (F-035): the built-in defaults of the session lifetime, the idle
timeout and the fresh-login window, and each one's environment override.
"""

from datetime import timedelta

from ._helpers import _C10_ENV_VARS, _reload_config_with_clean_env, _restore_config_module


# ---------------------------------------------------------------------------
# F-035: PERMANENT_SESSION_LIFETIME
# ---------------------------------------------------------------------------


class TestC10ConfigDefaults:
    """The three C-10 config defaults are baked into ``BaseConfig``.

    Each test stubs ``load_dotenv`` and clears the matching env var
    before reloading ``app.config``, so the assertion measures the
    BUILT-IN default rather than whatever the developer happens to
    have in their local .env.  Without this isolation a developer
    whose .env sets ``IDLE_TIMEOUT_MINUTES=30`` (the original
    value) would see a green test that hides a real regression.

    A teardown ``_restore_config_module`` call reloads the module
    after monkeypatch undoes its changes, so sibling tests that
    import BaseConfig directly see the env-driven values they
    expect.
    """

    def test_session_lifetime_default_is_12_hours(self, monkeypatch):
        """``PERMANENT_SESSION_LIFETIME`` defaults to 12 hours (F-035).

        Without this explicit default the cookie would carry Flask's
        31-day fallback and a stolen browser profile would have a
        month of valid auth.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.PERMANENT_SESSION_LIFETIME == (
                timedelta(hours=12)
            )
        finally:
            _restore_config_module()

    def test_idle_timeout_default_is_12_hours(self, monkeypatch):
        """``IDLE_TIMEOUT_MINUTES`` defaults to 720 (12h).

        Widened from the original 30 minutes: this is a single-owner,
        low-sensitivity budget app, and a short idle window mostly
        punished the legitimate owner by bouncing them to a full
        password + MFA login whenever they stepped away from an open
        tab.  Without this constant, ``_idle_session_is_fresh`` in
        ``app/__init__.py`` would have no threshold to compare against
        and the entire idle-timeout check would degrade to "always
        allow".
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.IDLE_TIMEOUT_MINUTES == 720
        finally:
            _restore_config_module()

    def test_fresh_login_max_age_default_is_5_minutes(self, monkeypatch):
        """``FRESH_LOGIN_MAX_AGE_MINUTES`` defaults to 5 (F-045).

        ASVS L2 V4.3.3 step-up window.  Without this constant the
        ``fresh_login_required`` decorator would have no threshold
        and would either always reject or always accept.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.FRESH_LOGIN_MAX_AGE_MINUTES == 5
        finally:
            _restore_config_module()


class TestC10ConfigEnvOverride:
    """Env vars override the C-10 defaults at module import time."""

    def test_session_lifetime_hours_env_override(self, monkeypatch):
        """``SESSION_LIFETIME_HOURS=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("SESSION_LIFETIME_HOURS", "48")
            for var in _C10_ENV_VARS:
                if var != "SESSION_LIFETIME_HOURS":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.PERMANENT_SESSION_LIFETIME == (
                timedelta(hours=48)
            )
        finally:
            _restore_config_module()

    def test_idle_timeout_minutes_env_override(self, monkeypatch):
        """``IDLE_TIMEOUT_MINUTES=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("IDLE_TIMEOUT_MINUTES", "15")
            for var in _C10_ENV_VARS:
                if var != "IDLE_TIMEOUT_MINUTES":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.IDLE_TIMEOUT_MINUTES == 15
        finally:
            _restore_config_module()

    def test_fresh_login_max_age_env_override(self, monkeypatch):
        """``FRESH_LOGIN_MAX_AGE_MINUTES=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("FRESH_LOGIN_MAX_AGE_MINUTES", "10")
            for var in _C10_ENV_VARS:
                if var != "FRESH_LOGIN_MAX_AGE_MINUTES":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.FRESH_LOGIN_MAX_AGE_MINUTES == 10
        finally:
            _restore_config_module()
