"""
Shekel Budget App -- Flask-Limiter Integration Tests

Asserts that the rate-limit posture introduced by audit Commit C-06
(F-034) survives end-to-end:

- The per-IP default ceiling (200/hour, 30/minute) applies to routes
  with NO explicit ``@limiter.limit`` decorator, closing the gap that
  4 of ~93 mutating routes were rate-limited pre-remediation.
- The ``/health`` endpoint is exempt so the Docker / Nginx healthcheck
  loops do not burn through the per-IP budget every 30 seconds.
- The configured key_func is ``get_remote_address`` so per-IP
  separation actually works.
- A storage backend outage (Redis unreachable in production)
  fail-closed surfaces as a server error, NOT a silent degradation
  to per-worker memory or to allowing the request through (which
  would let an attacker brute-force auth during a Redis outage).
- 429 responses include the ``Retry-After`` header so a well-behaved
  client knows when to retry.
- ``X-RateLimit-*`` headers are emitted when the limiter is active.

The tests use the same "fresh app + flip RATELIMIT_ENABLED" pattern
already in tests/test_routes/test_errors.py and tests/test_routes/
test_auth.py, so a single change to the toggle pattern would surface
in all rate-limit suites at once.

Storage backend: TestConfig forces ``memory://`` regardless of the
operator's environment, so these tests run on any laptop without a
Redis instance.  The behavior under Redis unreachability is verified
by patching the storage's ``incr`` method to raise -- this exercises
the same code path Flask-Limiter takes when redis-py raises a real
ConnectionError (see flask_limiter._extension.py:1142-1169).
"""

# pylint: disable=redefined-outer-name

from unittest.mock import patch

import pytest

from app import create_app


# Window math for the default ceiling.  RATELIMIT_DEFAULT in BaseConfig
# is "200 per hour;30 per minute"; the per-minute clause is the one a
# pytest-time loop will trip first.  31 hits ensures the 31st is the
# rejection (1-indexed: hits 1..30 succeed, hit 31 returns 429).
_DEFAULT_PER_MINUTE = 30
_HITS_TO_TRIP_DEFAULT = _DEFAULT_PER_MINUTE + 1


def _enable_limiter_on_fresh_app():
    """Build a fresh test app with RATELIMIT_ENABLED=True.

    Returns the (app, rate_client, limiter) triple.  Caller is
    responsible for calling ``_disable_limiter(limiter, app)`` when done
    to dispose the engine and reset the module-level limiter state for
    subsequent tests.

    The fresh-app pattern is required because TestConfig sets
    RATELIMIT_ENABLED=False at the class level, and Flask-Limiter
    short-circuits the entire decorator stack when ``enabled`` is False;
    flipping it on the live app would not take effect until the next
    init_app call.
    """
    rate_app = create_app("testing")
    rate_app.config["RATELIMIT_ENABLED"] = True

    # pylint: disable=import-outside-toplevel
    from app.extensions import limiter

    limiter.enabled = True
    limiter.init_app(rate_app)

    rate_client = rate_app.test_client()
    return rate_app, rate_client, limiter


def _disable_limiter(limiter, rate_app):
    """Tear down the per-test limiter / app state.

    Disposes the secondary app's SQLAlchemy engine to release database
    connections, clears the rate-limit storage's counter state so a
    later test cannot inherit stale counts via the module-level
    singleton, then disables the limiter so the *next* test starts
    with rate-limiting off (TestConfig default).

    Counter reset rationale: ``limiter.init_app(rate_app)`` set
    ``limiter._storage`` to a fresh MemoryStorage for the test's
    rate_app.  Calling ``init_app`` again on a *different* fresh app
    in a later test would build a brand-new MemoryStorage, so the
    counters do not normally bleed across tests.  But the module-
    level limiter is a singleton, and a future test that flipped
    ``limiter.enabled = True`` *without* calling ``init_app`` would
    inherit the storage left here -- including any non-zero counters
    from this test's requests.  ``limiter.reset()`` clears those
    counters defensively so that pattern (currently unused but
    architecturally possible) cannot reintroduce the order-dependent
    false-confidence failure mode the testing-standards "zero
    tolerance for failing tests" rule is designed to prevent.  See
    shekel-audits/security-2026-04-15/c-38-followups.md Issue 2a.
    """
    with rate_app.app_context():
        # pylint: disable=import-outside-toplevel
        from app.extensions import db as _db

        _db.engine.dispose()
    # ``limiter.reset()`` asserts ``_storage is not None`` internally;
    # the helper is only ever called after ``_enable_limiter_on_fresh_
    # app`` has run ``init_app`` (which populates ``_storage``), so the
    # invariant holds.  Guard explicitly anyway so an accidental call
    # from a teardown that did not run init_app would not crash the
    # test runner -- a clean no-op is the right defensive posture.
    if limiter._storage is not None:  # pylint: disable=protected-access
        limiter.reset()
    limiter.enabled = False


class TestDefaultLimitCeiling:
    """The default ceiling (BaseConfig.RATELIMIT_DEFAULT) applies to
    routes that lack an explicit ``@limiter.limit`` decorator.

    Closes audit finding F-034: only 4 of ~93 mutating routes had any
    rate limit pre-remediation.  Without the ceiling, an authenticated
    attacker could pound any unprotected mutating endpoint at full
    request rate.
    """

    def test_default_per_minute_limit_trips_on_undecorated_route(
        self, app, seed_user
    ):
        """A route with no @limiter.limit gets the BaseConfig per-minute ceiling.

        Registers a temporary route on a fresh app, hits it 31 times,
        and asserts the 31st response is 429.  The route is added at
        construction time before any requests fire, satisfying Flask's
        "no late route registration" rule.
        """
        # pylint: disable=unused-argument
        rate_app = create_app("testing")
        rate_app.config["RATELIMIT_ENABLED"] = True
        # The login gate (plan step bank_import:X-gi-4) would bounce the
        # anonymous hits below before the limiter counted them past the
        # redirect; Flask-Login's own switch turns it off, and the subject
        # here is the ceiling.
        rate_app.config["LOGIN_DISABLED"] = True

        # Register an undecorated route BEFORE init_app fires.
        @rate_app.route("/test-undecorated-route")
        def undecorated():
            """Undecorated test route -- inherits default ceiling only."""
            return "ok", 200

        # pylint: disable=import-outside-toplevel
        from app.extensions import limiter

        limiter.enabled = True
        limiter.init_app(rate_app)

        rate_client = rate_app.test_client()

        try:
            with rate_app.app_context():
                # Hit the route up to and past the per-minute ceiling.
                # 1..30 must succeed; 31 must be rate-limited.
                last_response = None
                for hit in range(_HITS_TO_TRIP_DEFAULT):
                    last_response = rate_client.get("/test-undecorated-route")
                    if hit < _DEFAULT_PER_MINUTE:
                        assert last_response.status_code == 200, (
                            f"hit {hit + 1} unexpectedly returned "
                            f"{last_response.status_code}"
                        )
                # The hit AFTER the ceiling must be 429.
                assert last_response is not None
                assert last_response.status_code == 429
        finally:
            _disable_limiter(limiter, rate_app)

    def test_default_limit_emits_retry_after_header(self, app, seed_user):
        """A 429 from the default ceiling carries a Retry-After header.

        Required by RFC 6585 so a well-behaved client knows when to
        attempt again instead of polling immediately.  The value is set
        by Flask-Limiter from the limit window (60 seconds for the
        per-minute clause); the existing 429 error handler in
        app/__init__.py overrides to 900 (15 minutes) only if no value
        is already present.
        """
        # pylint: disable=unused-argument
        rate_app = create_app("testing")
        rate_app.config["RATELIMIT_ENABLED"] = True
        # The login gate (plan step bank_import:X-gi-4) would bounce the
        # anonymous hits below before the limiter counted them past the
        # redirect; Flask-Login's own switch turns it off, and the subject
        # here is the ceiling.
        rate_app.config["LOGIN_DISABLED"] = True

        @rate_app.route("/test-retry-after-route")
        def retry_after():
            """Undecorated test route for Retry-After header check."""
            return "ok", 200

        # pylint: disable=import-outside-toplevel
        from app.extensions import limiter

        limiter.enabled = True
        limiter.init_app(rate_app)

        rate_client = rate_app.test_client()

        try:
            with rate_app.app_context():
                last_response = None
                for _ in range(_HITS_TO_TRIP_DEFAULT):
                    last_response = rate_client.get("/test-retry-after-route")
                assert last_response is not None
                assert last_response.status_code == 429
                # Retry-After is mandatory on 429 per RFC 6585 section 4.
                assert "Retry-After" in last_response.headers
                # Value must be a non-empty integer (seconds) or HTTP-date.
                # Flask-Limiter emits seconds; assert numeric and positive.
                retry_after_value = last_response.headers["Retry-After"]
                assert retry_after_value.strip() != ""
                # The custom 429 handler in app/__init__.py sets 900
                # when no value is already present; either path produces
                # a non-empty positive integer here.
                assert int(retry_after_value) > 0
        finally:
            _disable_limiter(limiter, rate_app)


class TestHealthEndpointExemption:
    """The /health endpoint is exempt from rate limiting (audit C-06).

    Docker fires the healthcheck every 30 seconds from a single source
    IP (127.0.0.1 inside the container).  Without exemption, /health
    would consume the per-IP budget that defends auth and mutating
    routes from brute-force, making the operator's monitoring loop a
    self-inflicted DoS on rate limiting.
    """

    def test_health_bypasses_per_minute_default_ceiling(self, app, seed_user):
        """/health must succeed past the 30/minute default ceiling.

        Hits /health 60 times -- twice the per-minute ceiling.  Every
        response must be 200 with the expected JSON body.  If any
        response is 429, the @limiter.exempt decorator on the route
        has been silently dropped or the exemption infrastructure is
        broken.
        """
        # pylint: disable=unused-argument
        rate_app, rate_client, limiter = _enable_limiter_on_fresh_app()

        try:
            with rate_app.app_context():
                # 2x the per-minute ceiling.  Any 429 in this loop is a
                # hard regression of the exemption.
                for hit in range(2 * _DEFAULT_PER_MINUTE):
                    response = rate_client.get("/health")
                    assert response.status_code == 200, (
                        f"hit {hit + 1} on /health returned "
                        f"{response.status_code} -- exemption broken"
                    )
                    body = response.get_json()
                    assert body is not None
                    assert body["status"] == "healthy"
        finally:
            _disable_limiter(limiter, rate_app)


class TestFailClosedOnStorageOutage:
    """When the rate-limit storage backend is unreachable, requests to
    rate-limited routes must NOT silently proceed.

    BaseConfig sets ``RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = False``
    and ``RATELIMIT_SWALLOW_ERRORS = False``.  Flask-Limiter
    (_extension.py:1142-1169) raises the storage exception when both
    flags are False; Flask catches it and returns 500 via the existing
    error handler.  This is the developer's deliberate fail-closed
    posture (Phase D-12 architectural decision) -- the alternative
    (silent fall-through) would let an attacker brute-force auth
    during a Redis outage.
    """

    def test_storage_outage_returns_5xx_not_200(self, app, seed_user):
        """A storage exception during a rate-limit check produces 5xx.

        Patches the active limiter storage's ``acquire_entry`` and
        ``get_moving_window`` methods (the moving-window strategy's
        write/read paths -- see limits/strategies.py:85-117) to raise
        a ConnectionError.  This mirrors what redis-py raises when its
        socket is closed.  The request to /login must NOT succeed
        because that would mean the rate-limit check was silently
        skipped, which is exactly the failure mode F-034's fail-closed
        posture exists to prevent.

        Test setup notes:

        - PROPAGATE_EXCEPTIONS is forced False so the test client sees
          Flask's production behavior (500 via the registered handler)
          rather than re-raising into pytest.  Flask's TESTING=True
          implicitly sets PROPAGATE_EXCEPTIONS=True, which is great
          for normal test introspection but hides the exact code path
          this fail-closed assertion is checking.
        - We patch at the storage-method layer rather than the strategy
          layer so the test exercises the same exception path Flask-
          Limiter follows in production: storage method raises ->
          strategy method raises -> _check_request_limit re-raises
          (because in_memory_fallback_enabled=False and swallow_errors=
          False) -> Flask error handler runs.
        """
        # pylint: disable=unused-argument
        rate_app = create_app("testing")
        rate_app.config["RATELIMIT_ENABLED"] = True
        # Force production-style exception handling so the storage
        # error renders as 500 via the registered handler instead of
        # being re-raised into the test runner.  See test_errors.py
        # test_400_renders_custom_page for the same pattern.
        rate_app.config["PROPAGATE_EXCEPTIONS"] = False

        # pylint: disable=import-outside-toplevel
        from app.extensions import limiter

        limiter.enabled = True
        limiter.init_app(rate_app)

        rate_client = rate_app.test_client()

        def raise_connection_error(*_args, **_kwargs):
            """Simulate a Redis socket failure on every counter write."""
            raise ConnectionError("Simulated rate-limit storage outage")

        try:
            with rate_app.app_context():
                with patch.object(
                    limiter._storage,  # pylint: disable=protected-access
                    "acquire_entry",
                    side_effect=raise_connection_error,
                ), patch.object(
                    limiter._storage,  # pylint: disable=protected-access
                    "get_moving_window",
                    side_effect=raise_connection_error,
                ):
                    response = rate_client.post(
                        "/login",
                        data={
                            "email": "test@shekel.local",
                            "password": "wrongpassword",
                        },
                    )

                # Fail-closed: the storage outage must surface as 5xx,
                # NOT 200/302/401.  Auth-failure responses (4xx) would
                # mean the rate-limit check was silently skipped and
                # the auth code ran -- exactly the failure mode the
                # developer's Phase D-12 choice rejects.
                assert 500 <= response.status_code < 600, (
                    f"storage outage returned {response.status_code}; "
                    "expected 5xx (fail-closed posture). A 4xx response "
                    "indicates the rate-limit check was silently "
                    "swallowed -- audit F-034 regression."
                )
        finally:
            _disable_limiter(limiter, rate_app)


class TestKeyFunctionWiresRemoteAddress:
    """Limiter must key on the request's remote_addr so per-IP
    separation actually works.  Without this, every request would key
    on the same constant and rate limits would be shared across all
    users (effectively turning the limit into an application-wide cap).
    """

    def test_key_func_is_get_remote_address(self):
        """The module-level limiter is constructed with key_func=get_remote_address.

        The limiter is instantiated at module load time in
        ``app/extensions.py``.  ``key_func`` is the only constructor
        argument we pass (everything else flows through app.config in
        init_app), so a regression that drops the argument would
        silently key every request on a fixed string.  This test
        guards that exact construction.
        """
        # pylint: disable=import-outside-toplevel
        from flask_limiter.util import get_remote_address

        from app.extensions import limiter

        # Flask-Limiter stores the key_func as ``_key_func`` on the
        # Limiter instance.  Comparing function identity (is) is
        # stronger than name comparison and catches re-imports.
        assert limiter._key_func is get_remote_address  # pylint: disable=protected-access


class TestRateLimitHeaders:
    """``X-RateLimit-*`` response headers are emitted when the limiter
    is active.  BaseConfig sets ``RATELIMIT_HEADERS_ENABLED = True``
    so a front-end retry library or operator diagnostic does not have
    to inspect Redis directly to learn current consumption.
    """

    def test_x_ratelimit_headers_present_on_limited_route(
        self, app, seed_user
    ):
        """A limited request carries X-RateLimit-Limit and -Remaining headers.

        The /login POST route has an explicit @limiter.limit("5 per 15
        minutes") decorator.  The first response on a fresh app must
        carry both X-RateLimit-Limit and X-RateLimit-Remaining headers
        with sensible numeric values.
        """
        # pylint: disable=unused-argument
        rate_app, rate_client, limiter = _enable_limiter_on_fresh_app()

        try:
            with rate_app.app_context():
                response = rate_client.post(
                    "/login",
                    data={
                        "email": "test@shekel.local",
                        "password": "wrongpassword",
                    },
                )
                # The login route is reached and rate-limit middleware
                # ran, so headers must be present regardless of body.
                assert "X-RateLimit-Limit" in response.headers
                assert "X-RateLimit-Remaining" in response.headers
                # Limit comes from the route's "5 per 15 minutes"
                # decorator; first hit consumes 1, leaving 4.
                assert int(response.headers["X-RateLimit-Limit"]) == 5
                assert int(response.headers["X-RateLimit-Remaining"]) == 4
        finally:
            _disable_limiter(limiter, rate_app)


class TestStorageBackendIsConfigDriven:
    """The Limiter's storage backend is resolved from app.config in
    init_app(), NOT from a constructor argument in app/extensions.py.

    This is the linchpin behavior that lets TestConfig force the
    in-memory backend regardless of the operator's environment, and
    lets ProdConfig default to Redis without code changes.  Flask-
    Limiter v4 (_extension.py:371-376) gives the constructor's
    storage_uri precedence over app.config -- so a constructor URI
    would silently override every environment's choice.
    """

    def test_extensions_module_does_not_pin_storage_uri(self):
        """app/extensions.py must NOT pass storage_uri at construction.

        A non-None ``_storage_uri`` on the Limiter instance after
        module load means the constructor pinned a backend, which would
        break per-environment overrides.  Storage is set by init_app()
        from app.config, so the attribute may be populated AFTER
        init_app -- but at module-import time it must be None.

        We test by reimporting in a way that sees the construction
        state: the module-level limiter has its ``_storage_uri`` set
        only by init_app calls.  The assertion below uses the actual
        module-level instance and verifies that the value does NOT
        match a hardcoded production URI -- which would be the
        symptom of a regression that re-pinned at construction.
        """
        # pylint: disable=import-outside-toplevel,protected-access
        from app.extensions import limiter

        # At this point the test session has already imported app and
        # called init_app(testing), which sets _storage_uri to the
        # TestConfig override ("memory://").  A regression that pins
        # the constructor URI would manifest as _storage_uri being a
        # production URI (redis://...) instead.
        assert limiter._storage_uri is None or limiter._storage_uri == "memory://", (
            "Limiter._storage_uri should be None (constructor) or "
            "memory:// (TestConfig override).  A redis:// or other "
            "production URI here means app/extensions.py is pinning "
            "storage at construction, which would break "
            "TestConfig.RATELIMIT_STORAGE_URI override."
        )

    def test_test_config_forces_memory_backend(self):
        """TestConfig.RATELIMIT_STORAGE_URI=memory:// yields MemoryStorage.

        Asserts that ``init_app`` on a fresh app built from TestConfig
        produces a MemoryStorage instance.  This is the storage-class-
        level assertion that complements the URI-level test above.

        The fresh-app pattern is required because TestConfig sets
        ``RATELIMIT_ENABLED=False`` and Flask-Limiter's ``init_app``
        short-circuits at the head of the method when ``enabled=False``
        (see flask_limiter/_extension.py ``init_app`` second ``if``
        block).  The short-circuit returns BEFORE storage is
        constructed, so the session-level limiter has ``_storage=None``
        in isolation.  Asserting on the session limiter's ``_storage``
        would therefore pass only when a sibling test had previously
        flipped ``enabled=True`` and triggered a populating
        ``init_app`` call -- an order dependency that
        ``testing-standards.md`` forbids ("Tests must be independent").

        The helper below builds a fresh app with ``RATELIMIT_ENABLED
        =True`` so ``init_app`` proceeds past the short-circuit and
        constructs storage from the TestConfig URI.  The test then
        verifies what its name claims: that the URI string the test
        config dictates resolves to a MemoryStorage instance.
        """
        # pylint: disable=import-outside-toplevel,protected-access
        from limits.storage import MemoryStorage

        rate_app, _rate_client, limiter_inst = _enable_limiter_on_fresh_app()
        try:
            assert isinstance(limiter_inst._storage, MemoryStorage), (
                f"limiter._storage is "
                f"{type(limiter_inst._storage).__name__}; "
                "TestConfig.RATELIMIT_STORAGE_URI=memory:// should produce "
                "a MemoryStorage instance."
            )
        finally:
            _disable_limiter(limiter_inst, rate_app)


# Pytest module marker -- conftest.py's seed_user fixture is required
# for several tests in this module.  Importing it implicitly via
# function arguments wires the fixture chain; an explicit pytest.mark
# is not needed.
pytestmark = pytest.mark.usefixtures("setup_database")
