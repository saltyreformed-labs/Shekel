"""Configuration tests: the settings each configuration class carries outside production's refusals
-- the upload ceiling, DevConfig, TestConfig, the login manager's session protection, and the
driver arguments every configuration connects with.
"""

import pytest
from flask import request
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import BaseConfig, DevConfig, DevContainerConfig, ProdConfig
from app.config import TestConfig as _TestConfig
from app.extensions import login_manager


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
        assert _TestConfig.MAX_CONTENT_LENGTH == BaseConfig.MAX_CONTENT_LENGTH

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
        assert _TestConfig.SQLALCHEMY_DATABASE_URI is not None
        assert _TestConfig.SQLALCHEMY_DATABASE_URI != ""

    def test_database_uri_is_valid_postgresql_uri(self):
        """The test URI is a valid PostgreSQL connection string."""
        uri = _TestConfig.SQLALCHEMY_DATABASE_URI
        assert uri.startswith("postgresql")

    def test_csrf_disabled(self):
        """TestConfig disables CSRF for test convenience."""
        assert _TestConfig.WTF_CSRF_ENABLED is False

    def test_testing_flag(self):
        """TestConfig has TESTING=True."""
        assert _TestConfig.TESTING is True

    def test_testconfig_loads_without_default(self, monkeypatch):
        """TestConfig has no SECRET_KEY fallback default (F-016).

        Same rationale as ``test_devconfig_loads_without_default``.
        TestConfig must load with ``SECRET_KEY=None`` so unit tests
        that explicitly clear the value do not see a hidden fallback.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", None)
        config = _TestConfig()
        assert config.SECRET_KEY is None


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

    @pytest.mark.parametrize(
        "config", [DevConfig, DevContainerConfig, _TestConfig, ProdConfig]
    )
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
        # Pylint: ``import-outside-toplevel`` -- used by these two
        # database cases alone, so the module's other tests import nothing
        # of the driver layer.
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import text

        with app.app_context(), db.engine.connect() as connection:
            for _ in range(6):
                connection.execute(text("SELECT 1 WHERE 1 = :one"), {"one": 1})
            prepared = connection.execute(
                text("SELECT count(*) FROM pg_prepared_statements"),
            ).scalar()
        assert prepared == 0

    @pytest.mark.usefixtures("app")
    def test_the_control_sees_the_default_threshold_prepare(self, db):
        """Under psycopg's default threshold the same six runs DO prepare one."""
        # Pylint: ``import-outside-toplevel`` -- the driver itself, for this
        # control alone, as the case above imports its one name.
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
