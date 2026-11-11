"""
Shekel Budget App -- The Deploy's Configuration

Tests for :class:`app.config.DeployConfig`, the configuration the deploy builds
(``FLASK_ENV=deploy``, ruling plan#3451): the migrations, the reference rows,
the first user's seed and the owner-login maintenance scripts.  It connects by
the owner's ``DATABASE_URL`` alone, never ``DATABASE_URL_APP``, and asks for no
web secret, so the deploy container can hold one password, the owner's
(rulings plan#3413, plan#3450).

Kept out of ``tests/test_config.py``, which is past the 1,000-line ceiling.
Every app built here is built with ``init_ref_cache=False``, as
``scripts/init_database.py`` builds its own, so nothing here opens a database
connection: the URLs below name no reachable server.
"""

import logging
import secrets

import pytest

from app import create_app
from app.config import (
    BaseConfig,
    CONFIG_MAP,
    DeployConfig,
    ProdConfig,
    _DATABASE_URL_SENTINEL,
    configuration_name,
)


#: The owner's URL the deploy is given.  Port 1 on loopback: nothing listens,
#: and nothing here connects.
_OWNER_URL = "postgresql://shekel_owner:owner-password@127.0.0.1:1/shekel"

#: The web login's URL, set beside the owner's to show it is never read.
_APP_URL = "postgresql://shekel_app:app-password@127.0.0.1:1/shekel"

#: The warning ``create_app`` logs when a configuration has no TOTP key.
_TOTP_WARNING = "TOTP_ENCRYPTION_KEY is not set"


@pytest.fixture(name="no_web_secret")
def _no_web_secret(monkeypatch):
    """Strip every web secret from the configuration and the environment.

    ``BaseConfig`` reads ``SECRET_KEY`` and the TOTP key when the module is
    imported, so the class attributes are cleared beside the variables.
    """
    monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
    monkeypatch.setattr(BaseConfig, "TOTP_ENCRYPTION_KEY", None)
    monkeypatch.setattr(BaseConfig, "TOTP_ENCRYPTION_KEY_OLD", None)
    for name in ("SECRET_KEY", "VALKEY_PASSWORD", "TOTP_ENCRYPTION_KEY",
                 "TOTP_ENCRYPTION_KEY_OLD", "DATABASE_URL_APP"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(name="app_warnings")
def _app_warnings():
    """Collect what the ``app`` logger warns, for the duration of a test.

    On the ``app`` logger itself rather than through ``caplog``: ``create_app``
    runs ``setup_logging``, whose ``dictConfig`` replaces the ROOT logger's
    handlers, which is where ``caplog`` listens.

    Yields:
        list[str]: Each warning's message, in order.
    """
    seen: list[str] = []

    class _Collect(logging.Handler):
        """Keep each WARNING record's message."""

        def emit(self, record):
            if record.levelno == logging.WARNING:
                seen.append(record.getMessage())

    handler = _Collect()
    logger = logging.getLogger("app")
    logger.addHandler(handler)
    try:
        yield seen
    finally:
        logger.removeHandler(handler)


@pytest.mark.usefixtures("no_web_secret")
class TestTheDeployConfigurationNeedsTheOwnerAlone:
    """It builds from ``DATABASE_URL`` and nothing else."""

    def test_it_is_the_deploy_name(self):
        """``FLASK_ENV=deploy`` names it, the name the deploy's start sets."""
        assert CONFIG_MAP["deploy"] is DeployConfig

    def test_it_builds_with_no_web_secret(self, monkeypatch):
        """No ``SECRET_KEY``, no store password, no TOTP key: it builds.

        ``ProdConfig`` refuses this environment by name, so the deploy could
        not run under it; that refusal is the control.
        """
        monkeypatch.setenv("DATABASE_URL", _OWNER_URL)

        config = DeployConfig()

        assert config.SQLALCHEMY_DATABASE_URI == _OWNER_URL
        assert config.SECRET_KEY is None
        assert config.TOTP_ENCRYPTION_KEY is None
        assert not hasattr(config, "RATELIMIT_STORAGE_OPTIONS")
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", _OWNER_URL)
        with pytest.raises(ValueError, match="SECRET_KEY is required"):
            ProdConfig()

    def test_it_reads_the_owners_url_never_the_web_logins(self, monkeypatch):
        """With both URLs set it connects by ``DATABASE_URL``.

        Every other configuration that reads both prefers ``DATABASE_URL_APP``
        (``_runtime_database_uri``), which is what put the deploy's scripts
        behind an override that blanked it.
        """
        monkeypatch.setenv("DATABASE_URL", _OWNER_URL)
        monkeypatch.setenv("DATABASE_URL_APP", _APP_URL)

        assert DeployConfig().SQLALCHEMY_DATABASE_URI == _OWNER_URL

    def test_the_web_logins_url_alone_is_refused(self, monkeypatch):
        """``DATABASE_URL_APP`` set and ``DATABASE_URL`` not: no fallback to it."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL_APP", _APP_URL)

        with pytest.raises(ValueError, match=r"^DATABASE_URL is not set"):
            DeployConfig()

    @pytest.mark.parametrize("given", [None, ""])
    def test_a_missing_owner_url_is_refused_by_name(self, monkeypatch, given):
        """Unset or empty, the refusal names ``DATABASE_URL``."""
        if given is None:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DATABASE_URL", given)

        with pytest.raises(ValueError, match=r"^DATABASE_URL is not set"):
            DeployConfig()

    def test_the_example_files_sentinel_password_is_refused(self, monkeypatch):
        """A ``DATABASE_URL`` still holding ``.env.example``'s sentinel is named."""
        monkeypatch.setenv(
            "DATABASE_URL",
            f"postgresql://shekel_owner:{_DATABASE_URL_SENTINEL}@db:5432/shekel",
        )

        with pytest.raises(ValueError, match=r"^DATABASE_URL still embeds"):
            DeployConfig()

    def test_the_url_is_read_when_built_not_when_imported(self, monkeypatch):
        """A URL set after the module loaded is the one the configuration takes.

        The deploy's scripts and ``scripts/_script_lib``'s ``--database-url``
        set the variable after ``app.config`` may already be imported.
        """
        monkeypatch.setenv("DATABASE_URL", _OWNER_URL)
        first = DeployConfig().SQLALCHEMY_DATABASE_URI
        monkeypatch.setenv("DATABASE_URL", _APP_URL)

        assert (first, DeployConfig().SQLALCHEMY_DATABASE_URI) == (
            _OWNER_URL, _APP_URL,
        )


class TestTheDeployServesNoRequest:
    """The limiter is off, so the deploy needs no store."""

    def test_the_limiter_is_off(self):
        """``RATELIMIT_ENABLED`` is False, on the in-memory store it never uses."""
        assert DeployConfig.RATELIMIT_ENABLED is False
        assert DeployConfig.RATELIMIT_STORAGE_URI == "memory://"

    def test_it_connects_as_every_configuration_does(self):
        """The driver arguments every configuration carries (plan step balance:X-dj)."""
        assert DeployConfig.SQLALCHEMY_ENGINE_OPTIONS["connect_args"] == {
            "connect_timeout": 5,
            "prepare_threshold": None,
        }


@pytest.mark.usefixtures("no_web_secret")
class TestCreateAppBuildsTheDeploy:
    """``create_app`` builds the deploy's app as the deploy's scripts do."""

    def test_flask_env_deploy_builds_it(self, monkeypatch):
        """``FLASK_ENV=deploy`` and no name given: the deploy's app, the owner's URL.

        What the entrypoint's ``FLASK_ENV=deploy python scripts/init_database.py``
        does, with ``DATABASE_URL_APP`` set beside it as the web container sets it.
        """
        monkeypatch.setenv("FLASK_ENV", "deploy")
        monkeypatch.setenv("DATABASE_URL", _OWNER_URL)
        monkeypatch.setenv("DATABASE_URL_APP", _APP_URL)

        app = create_app(init_ref_cache=False)

        assert configuration_name() == "deploy"
        assert app.config["SQLALCHEMY_DATABASE_URI"] == _OWNER_URL
        assert app.config["RATELIMIT_ENABLED"] is False
        assert app.config["SECRET_KEY"] is None

    def test_the_deploy_logs_no_missing_totp_key(self, monkeypatch, app_warnings):
        """Under the deploy, no warning that MFA will be unavailable.

        The deploy serves no sign-in and holds no web secret, so the warning
        would be false in every deploy's log.
        """
        monkeypatch.setenv("DATABASE_URL", _OWNER_URL)

        create_app("deploy", init_ref_cache=False)

        assert not [w for w in app_warnings if _TOTP_WARNING in w]

    def test_the_control_sees_the_warning_in_production(
        self, monkeypatch, app_warnings,
    ):
        """The same build under production, with no TOTP key, does warn.

        So the absence above is a measurement and not a handler that cannot see
        the warning at all.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", secrets.token_hex(32))
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", _APP_URL)
        monkeypatch.setenv("VALKEY_PASSWORD", "a-store-password")

        create_app("production", init_ref_cache=False)

        assert [w for w in app_warnings if _TOTP_WARNING in w]


class TestTheConfigurationName:
    """``configuration_name`` is the one reading of ``FLASK_ENV``."""

    def test_unset_is_development(self, monkeypatch):
        """With no ``FLASK_ENV``, the host-run configuration, as ``create_app`` builds."""
        monkeypatch.delenv("FLASK_ENV", raising=False)

        assert configuration_name() == "development"

    def test_set_is_what_it_names(self, monkeypatch):
        """With ``FLASK_ENV`` set, its value, unchecked: ``create_app`` refuses an unknown one."""
        monkeypatch.setenv("FLASK_ENV", "no-such-configuration")

        assert configuration_name() == "no-such-configuration"
