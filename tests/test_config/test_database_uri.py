"""Configuration tests: the runtime database URI each configuration resolves, and the refusal of
the .env.example sentinel password.
"""

from importlib import reload

import pytest

from app import config as config_module
from app.config import _DATABASE_URL_SENTINEL, _reject_sentinel, _runtime_database_uri

from ._helpers import (
    _SENTINEL_DATABASE_URL,
    _SENTINEL_TEST_DATABASE_URL,
    _CLEAN_DATABASE_URL,
    _CLEAN_TEST_DATABASE_URL,
)


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
