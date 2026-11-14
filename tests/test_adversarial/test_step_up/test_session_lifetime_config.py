"""C-10's configuration tests (F-035): Flask's cookie age, which every configuration sets from the
thirty days a session lasts, and the built-in defaults of the idle timeout and the fresh-login
window, with each one's environment override.
"""

import pytest

from app.config import CONFIG_MAP
from app.utils.sign_in_lifetimes import KNOWN_LIFETIME

from ._helpers import _C10_ENV_VARS, _reload_config_with_clean_env, _restore_config_module


# ---------------------------------------------------------------------------
# F-035: PERMANENT_SESSION_LIFETIME
# ---------------------------------------------------------------------------


class TestFlasksCookieAge:
    """Flask's cookie age is the thirty days a session lasts (ruling plan#3926)."""

    @pytest.mark.parametrize("config_name", sorted(CONFIG_MAP))
    def test_every_config_sets_it_from_known_lifetime(self, config_name):
        """``PERMANENT_SESSION_LIFETIME`` is ``KNOWN_LIFETIME`` itself, in every configuration.

        The same object, not an equal one: a second spelling of thirty days
        agrees with the first only until one of them moves.  Flask refuses a
        session cookie signed longer ago than this, permanent or not, so
        anything shorter is an idle rule of its own beside
        ``IDLE_TIMEOUT_MINUTES``, as its old 12-hour default was.  No
        environment variable sets it, so there is no override to clear.
        """
        assert CONFIG_MAP[config_name].PERMANENT_SESSION_LIFETIME is KNOWN_LIFETIME


class TestC10ConfigDefaults:
    """The two C-10 config defaults the environment can move are baked into ``BaseConfig``.

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
