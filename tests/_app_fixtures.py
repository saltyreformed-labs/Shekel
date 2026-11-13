"""The app, the per-test database, and the fixtures every test runs under.

``tests/conftest.py`` registers each fixture here by importing it by name, so each keeps
the conftest's directory as its base, and its name, scope and autouse.
"""

import os
# ``datetime.time`` is aliased, NOT imported under its own name, so the bare
# name ``time`` can only ever be the stdlib MODULE.  ``tests/conftest.py`` held
# this import beside ``import time`` until plan#3565 split it, and its profile
# harness (now ``tests/_fixture_profile.py``) calls ``time.perf_counter()``: an
# unaliased ``from datetime import time`` shadowed it two lines after it was
# imported, which is exactly what happened between 2026-07-26 (commit d3489728)
# and this line: every test errored with
# ``AttributeError: type object 'datetime.time' has no attribute
# 'perf_counter'`` the moment ``SHEKEL_TEST_FIXTURE_PROFILE=1`` was set, so the
# one instrument this project has for diagnosing test performance was dead for
# three weeks and nothing reported it (``tests/`` is outside the pylint scope
# that would have flagged the reimport).
from datetime import date, datetime, time as dt_time

import pytest
from flask import Flask

from app import create_app, tax_law as app_tax_law
from app.extensions import db as _db
from app.utils.dates import DISPLAY_TIMEZONE
from tests._fixture_profile import (
    _profile_new_timings,
    _profile_step,
    _profile_write_row,
)
from tests._per_request_globals import PerRequestGlobalsClient
from tests._seed_owner_fixtures import log_in_seed_user
from tests._seeded_start_states import (
    _SEEDED_STATE_BUILDERS,
    _refresh_ref_cache_and_jinja_globals,
    built_worlds,
    declared_seeded_state,
)
from tests._test_helpers import (
    bind_db_clock_rewriter,
    step_down_through_the_transfer_column_drop,
)
from tests._worker_database import (
    _WORKER_ADMIN_URL,
    _WORKER_DB_NAME,
    _clone_worker_database,
    _drop_worker_database,
)


# --- App & DB Fixtures ---------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def fast_bcrypt():
    """Use minimum bcrypt rounds (4) for all tests.

    Bcrypt's default work factor (12) makes each hash take ~250ms.
    Rounds=4 reduces this to ~2ms, saving 10+ seconds across the
    full suite without affecting test correctness.
    """
    import bcrypt as _bcrypt  # pylint: disable=import-outside-toplevel
    _original_gensalt = _bcrypt.gensalt

    def _fast_gensalt(rounds=4, prefix=b"2b"):
        """Generate a bcrypt salt with minimum work factor."""
        return _original_gensalt(rounds=rounds, prefix=prefix)

    _bcrypt.gensalt = _fast_gensalt
    yield
    _bcrypt.gensalt = _original_gensalt


@pytest.fixture(scope="session", autouse=True)
def set_totp_key():
    """Hold ONE two-factor encryption key for the whole test process (ruling plan#3625).

    A seeded start state is built once per worker and cloned into each test
    that declares it, so its owner's two-factor row is encrypted under the key
    of the test that built it.  A key per test makes that row unreadable from
    the second test on, so a test that decrypts a world's secret (to type its
    owner's code) would fail; so the key lives as long as the worker does.
    *The 2026-11-12 measurement behind the ruling (267 cases of one route sweep
    errored ``InvalidToken``) was taken while the shared sign-in typed a code;
    the trusted browser chosen instead decrypts nothing, so no sweep meets it
    now.*  ``TestAWorldsTwoFactorSecretIsReadInEveryTest``
    (``tests/test_arch/test_a_seeded_start_state_is_built_once.py``) fails
    under a key per test.  A test that needs a key of its own, or none,
    still sets or deletes it with ``monkeypatch``, which puts this one back
    after it.
    """
    from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
        yield


@pytest.fixture(autouse=True)
def disable_hibp_check(monkeypatch):
    """Disable the HIBP breached-password check by default.

    ``hash_password`` is invoked from dozens of fixtures (every
    ``seed_user`` variant, plus per-test registration helpers) and
    making each one perform an outbound HTTP call would (a) break the
    suite's hermeticity, (b) slow it by an order of magnitude, and
    (c) silently mask test results during HIBP outages.

    Tests that exercise HIBP behaviour explicitly flip this back on
    via ``monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")`` after
    mocking ``requests.get``.  ``monkeypatch`` is function-scoped so
    the override is local to a single test even when the autouse
    fixture has already run.

    See audit finding F-086 / commit C-11 for the production posture
    (default-on) and ``app/services/auth_service.py:_check_pwned_password``
    for the runtime read.
    """
    monkeypatch.setenv("HIBP_CHECK_ENABLED", "false")


@pytest.fixture(scope="session", autouse=True)
def _calendar_sweep():
    """Run the whole session as if today were ``SHEKEL_FAKE_TODAY``.

    **Opt-in, and a no-op unless the variable is set**, so an ordinary run is
    byte-identical to one without this fixture.

    Four separate defects in this suite had exactly one trigger: the day of the
    month the suite happened to run on (findings N-131, N-132, R8, and the two
    loan cross-surface tests that failed on 2026-08-01).  Each was found by a
    merge gate rather than by a test, because nothing here could ask "does this
    still pass on the 1st?".  This is that instrument::

        SHEKEL_FAKE_TODAY=2026-09-01 ./scripts/test.sh

    ``tick=True`` keeps the clock RUNNING from the faked instant rather than
    freezing it, because a frozen clock makes every ``created_at`` in a session
    identical and the fold's assertion ordering (ruling R-DH: two assertions
    sharing a civil day apply in recording order) then has no order to read.

    **What it can and cannot move, stated because the gap decides how to read a
    failure.**  It moves the PYTHON clock -- ``date.today()``,
    ``datetime.now()`` and therefore :func:`app.utils.dates.display_today`.  It
    does NOT move POSTGRES: ``created_at`` / ``updated_at`` are
    ``server_default=db.func.now()`` (``app/models/mixins.py:237-263``),
    evaluated in the database.  (The settle day was a fourth such reach until
    plan step X-f1; the seam stamps ``display_today()`` into a ``DATE`` column
    now, which this instrument DOES move.)  So under a fake date every
    server-stamped row
    carries the REAL instant, and a test comparing a fixture-built date against
    a server-stamped one fails by the offset between them.  That failure is an
    artifact of this instrument, not a defect in the test -- see
    ``docs/testing-standards.md`` for how to tell the two apart.

    The instant is built in ``DISPLAY_TIMEZONE`` at midday, so the faked civil
    day is unambiguous in the user's zone and cannot straddle midnight in either
    direction.
    """
    fake = os.environ.get("SHEKEL_FAKE_TODAY")
    if not fake:
        yield
        return

    # Pylint: ``import-outside-toplevel`` -- a test-only dependency imported
    # only when the sweep is switched on, so an ordinary run never loads it.
    # pylint: disable=import-outside-toplevel
    import time_machine

    target = datetime.combine(
        date.fromisoformat(fake), dt_time(12, 0), tzinfo=DISPLAY_TIMEZONE,
    )
    with time_machine.travel(target, tick=True):
        yield


@pytest.fixture(scope="session", autouse=True)
def each_request_has_its_own_g():
    """Give every test-client request a ``flask.g`` of its own, as production does.

    The ``db`` fixture below runs each test inside ONE application context,
    and a test-client request REUSES that context -- and its ``g`` -- rather
    than pushing its own.  Flask-Login caches the signed-in user on ``g``, so
    before this, once any request had cached one, every later request in the
    test was answered as that user, whichever client sent it (ledger row
    ``balance:BAL-521``, plan step ``balance:X-cr``).
    :class:`tests._per_request_globals.PerRequestGlobalsClient` gives each
    request a fresh ``g`` and puts the test's back afterwards; its module
    docstring carries the measurement and the design.

    **Set on the ``Flask`` CLASS, not on the session's app**, because the
    suite builds more apps than that one: a test that calls
    ``create_app("testing")`` itself and requests inside its own
    ``with that_app.app_context():`` shares a ``g`` across those requests in
    exactly the same way.  Session-scoped and autouse so it is in place before
    any fixture or test makes a client, and undone when the session ends.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Flask, "test_client_class", PerRequestGlobalsClient)
        yield


@pytest.fixture(scope="session")
def app():
    """Create the Flask application configured for testing."""
    application = create_app("testing")
    yield application


@pytest.fixture(scope="session", autouse=True)
def setup_database(app):
    """One-time per-session prep: refresh the in-process ref cache.

    The per-session PostgreSQL database was cloned from
    ``shekel_test_template`` at conftest module-load time (see
    :func:`_bootstrap_worker_database`).  Schemas, tables, audit
    infrastructure, indexes, and reference seed data are therefore
    already present in the database when this fixture runs; the only
    Python-side initialisation remaining is the in-process ref_cache
    and the Jinja globals that mirror the seeded IDs (the templates
    read these at render time -- a missing entry would break every
    page that references one).

    **Then it drops that start-up database**, with the engine released
    first, as the ``db`` fixture's teardown releases it.  It served no
    test, and a test's database lives exactly as long as the test
    (ruling plan#3870): each test's ``db`` clones under this worker's
    name with no drop before it, so the first one must find the name
    free, and a world's build takes the name as its scratch the same
    way.  ``pytest_sessionfinish`` (``tests/conftest.py``) still drops
    the name if a session ends with a database standing under it.

    It also binds the frozen-clock statement rewriter to the engine
    (finding N-65, balance plan step X-h).  **Here, and not lazily when a
    test first flushes**: binding on first flush made the rewriter's
    installation depend on some earlier test in the same worker process
    having flushed ORM state under a frozen clock, so a frozen test whose
    only writes were bulk ``query.update(...)`` silently got the real wall
    clock.  Same test, same assertion, opposite result depending on
    scheduling -- a fail-OPEN gate.  The listener is inert until a test
    freezes, so binding it before any test runs costs nothing and removes
    the order dependence.
    """
    with app.app_context():
        _refresh_ref_cache_and_jinja_globals(app)
        bind_db_clock_rewriter(_db.engine)
        _db.session.remove()
        _db.engine.dispose()
    _drop_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)
    yield


@pytest.fixture(scope="session")
def _seeded_snapshots(request, app, setup_database):  # pylint: disable=unused-argument
    """Every world this run's tests declare, built ONCE on this worker, in one pass.

    Requested only by :func:`_declared_world`, from inside the ``db`` fixture of
    the first test on this worker that declares a world, before that test's
    clone exists, so it runs with the worker's database name free and the
    test's environment fixtures applied, which a build needs
    (:func:`tests._seeded_start_states.built_worlds`).  A test that named
    it as an argument would set it up before those fixtures, and the build
    would refuse.  A run whose tests declare no world never builds one.

    Returns:
        ``{name: (snapshot_database_name, ids)}``, every snapshot dropped at
        this worker's session end.
    """
    with built_worlds(request.session.items, app) as worlds:
        yield worlds


def _declared_world(request):
    """``(snapshot_database_name, ids)`` of the world *request*'s test declared.

    The first call for a declaring test on this worker builds every declared
    world (:func:`_seeded_snapshots`); each later one reads what that built.

    Args:
        request: The test's pytest request.

    Returns:
        The pair, or ``None`` when the test declared no world.
    """
    name = declared_seeded_state(request.node)
    if name is None:
        return None
    return request.getfixturevalue("_seeded_snapshots")[name]


@pytest.fixture(autouse=True)
def db(app, setup_database, request, set_totp_key, disable_hibp_check):  # pylint: disable=unused-argument
    """Provide a freshly-cloned database for each test.

    **It DEPENDS on the two environment fixtures rather than merely coexisting
    with them** (plan step balance:X-be-2).  All three are autouse, so every
    test got them either way, and all three were function-scoped until
    ``set_totp_key`` became session-scoped (ruling plan#3625) -- but pytest builds
    the fixture list from ``dir(module)``, which is ALPHABETICAL, and
    ``"db" < "disable_hibp_check" < "set_totp_key"``.  That is the whole of
    why this one used to run first: not scope, and nothing about what it does.
    Harmless while it issued nothing but DDL; it can now run a seeded start
    state's builder, which is ordinary application code, and the first attempt
    built its owner with the HIBP check still live -- ``hash_password``
    refused the suite's own seed password with a message about a data breach.

    **So the dependency edge is the only thing pinning this order**, and
    renaming any of the three would otherwise silently re-sort them.  The
    edge is what makes the ordering a stated requirement rather than an
    alphabetical accident, and ``_refuse_a_build_the_environment_is_not_ready_for``
    is what says so out loud if the next builder needs a fixture nobody added
    here.

    Clones the per-worker DB from ``shekel_test_template`` via
    ``CREATE DATABASE ... TEMPLATE ... STRATEGY WAL_LOG``
    (Phase 3b of test-performance-implementation-plan.md), and drops
    it when the test ends.  Each test gets bit-for-bit the same start
    state the prior TRUNCATE+reseed cycle provided:

      * ``system.audit_log`` empty -- the template carries zero
        rows by construction; ``scripts/build_test_template.py``
        truncates the log after the seed commits.
      * No rows in ``budget.*`` / ``auth.*`` / ``salary.*`` -- the
        template is freshly migrated and seeded with reference
        data only.
      * Full ref-data seed in ``ref.*`` including the 19
        ``ref.account_types`` built-ins.
      * In-process ``ref_cache`` and Jinja globals re-seated to
        match the cloned DB's row IDs (which equal the template's
        IDs because ``CREATE DATABASE TEMPLATE`` preserves them).

    **A test that declares a SEEDED START STATE gets the first, third and
    fourth of those and not the second** (plan step balance:X-be-2): its
    database is cloned from that world's snapshot, so it starts holding the
    rows the world's builder wrote.  ``system.audit_log`` is still empty --
    the build truncates it, for the reason the template builder does -- and
    the ref data and cached ids are still the template's, because the snapshot
    is itself a clone of the template.  **What does NOT change is the
    isolation**: the clone below, and the drop when the test ends, run for
    every test either way, so a test still cannot see another's writes.  Only
    the database it is cloned FROM differs.

    **A test's database lives exactly as long as the test** (ruling
    plan#3870).  The clone is made with no drop before it, onto the name the
    previous test's teardown (or ``setup_database``, for the first test)
    left free, and dropped in the ``finally`` that follows it, so nothing a
    test writes is there for the next one.  A drop that failed leaves the
    name taken, and the next clone refuses it (``already exists``) rather
    than reuse the database standing there.

    Mechanism, in order:

      1. Resolve the clone source: the empty template, or the
         snapshot of the world the test declared.  The first
         declaring test on this worker builds every declared world
         here (``_seeded_snapshots``).
      2. ``setup_clone_template`` -- admin-DSN ``CREATE DATABASE
         {worker_db} TEMPLATE {source} STRATEGY WAL_LOG``.  See
         ``_clone_worker_database`` for why the strategy is named
         explicitly and what ``FILE_COPY`` cost.
      3. ``setup_refresh_ref_cache`` -- re-seat the in-process
         ref_cache and Jinja globals against the cloned DB.  The
         row IDs are identical to the template's (CLONE preserves
         them), but reseating costs ~5-7 ms and covers the edge
         case where a future migration changes the seeded ID set
         without anyone updating the in-process cache eagerly.
      4. ``call`` -- the test body.
      5. ``teardown`` -- release the engine (``session.remove()``
         detaches the scoped session; ``engine.dispose()`` closes
         every pooled connection), then admin-DSN ``DROP DATABASE IF
         EXISTS {worker_db} WITH (FORCE)``.  Disposing first keeps
         the pool from holding a connection to a dropped database;
         FORCE severs a backend the test opened itself.

    Why the worker DB name is stable across the session: the URL
    the Flask-SQLAlchemy engine binds to at app-creation time is
    derived from ``TEST_DATABASE_URL`` set by
    ``_bootstrap_worker_database``; that URL remains valid across
    every test because each test's clone takes the same name,
    never a new URL.  ``engine.dispose()`` at each teardown forces
    the pool to reconnect on the next session access, and the
    connection re-establishes against the next test's clone at the
    same URL.

    The ``_profile_step`` wrappers below are no-ops when
    ``SHEKEL_TEST_FIXTURE_PROFILE`` is unset; when set they capture
    per-step elapsed time for the Phase 0 harness (see the block
    comment near ``_FIXTURE_PROFILE_ENABLED`` at the top of
    ``tests/_fixture_profile.py``).
    """
    if _WORKER_DB_NAME is None or _WORKER_ADMIN_URL is None:
        raise RuntimeError(
            "db fixture invoked from a process that skipped "
            "_bootstrap_worker_database (xdist master?).  The "
            "master should not run tests; check pytest-xdist's "
            "scheduling configuration."
        )

    timings = _profile_new_timings()
    # nodeid is only used by the profile CSV writer; skip the
    # attribute lookup when profiling is disabled so the default
    # path adds zero work beyond the existing fixture body.
    nodeid = request.node.nodeid if timings is not None else None

    with app.app_context():
        # WHICH database this test is cloned from -- the empty template, or
        # the snapshot of a world it declared (plan step balance:X-be-2).
        # Resolved BEFORE the clone because the first declaring test on this
        # worker builds every declared world here, and a build takes this
        # worker's database name as its scratch, which it needs free: with
        # this test's clone made first, the build's own clone would refuse
        # the name.  ``None`` clones the empty template.
        world = _declared_world(request)
        clone_source = None if world is None else world[0]

        with _profile_step(timings, "setup_clone_template"):
            _clone_worker_database(
                _WORKER_DB_NAME, _WORKER_ADMIN_URL, clone_source,
            )

        # ``try``/``finally`` from the clone on, so the drop runs however
        # the rest of setup or the test ends: the database lives exactly as
        # long as the test.  The teardown timer and the CSV row write run
        # in it too -- a profile harness that silently dropped rows for
        # failing tests would bias the summary toward the passing path.
        # The ``with _profile_step(..., "call")`` captures the elapsed time
        # of the ``yield _db`` (i.e. the test body itself); context manager
        # exit fires after pytest sends back to this generator, so the timer
        # covers the test exactly.
        try:
            with _profile_step(timings, "setup_refresh_ref_cache"):
                # Re-seat the in-process ref_cache and Jinja globals
                # against the cloned DB.  The cloned IDs equal the
                # template IDs (CREATE DATABASE TEMPLATE preserves
                # them) so the cache is normally a no-op refresh, but
                # the explicit reseat covers the edge case where a
                # future migration changes the seeded ID set and an
                # unaware test would otherwise hit a Jinja Undefined.
                # First access to _db.session here triggers a fresh
                # pool connection to the cloned DB at the (unchanged)
                # URL the engine has been bound to since app-create.
                _refresh_ref_cache_and_jinja_globals(app)

            with _profile_step(timings, "call"):
                yield _db
        finally:
            with _profile_step(timings, "teardown"):
                # Detach the scoped session and close the engine pool,
                # so the pool holds no connection to the database the
                # drop removes; FORCE severs any backend a test opened
                # outside the pool.  The drop is in a ``finally`` of its
                # own: a release that raises (a session rolling back a
                # connection its test severed) must not leave the
                # database standing for the next test's clone to refuse.
                try:
                    _db.session.remove()
                    _db.engine.dispose()
                finally:
                    _drop_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)
            _profile_write_row(nodeid, timings)


@pytest.fixture()
def below_the_transfer_column_drop(db):
    """The case's database, stepped down through revisions ``1712c506ee99`` and ``c956c1d83108``.

    For a migration case of a revision below plan step ``balance:X-bi-6-5``'s
    column drop, requested as ``pytest.mark.usefixtures``;
    :func:`tests._test_helpers.step_down_through_the_transfer_column_drop`
    says what it restores and why it does not come back up.
    """
    step_down_through_the_transfer_column_drop(db.session)


@pytest.fixture()
def owner_client(client):
    """A client logged in as the seeded owner a WORLD already contains.

    The counterpart of ``auth_client`` for a test that declares a seeded start
    state: the owner is already in that world, so seeding a second one would
    collide on the unique email.  Both fixtures log in through the same
    :func:`log_in_seed_user`, so neither can drift into posting credentials
    the other does not write.

    Used on an ordinary test that declares no world, the login simply fails
    its assertion -- loud and immediate, naming the missing owner.

    Returns:
        The logged-in test client.
    """
    return log_in_seed_user(client)


@pytest.fixture()
def seeded_world(request, app, db):  # pylint: disable=unused-argument
    """The world this test declared, as the plain data its builder returned.

    Depends on ``db`` so it can only be read after this test's own copy of the
    world exists; the worlds it reads were built before ``db`` cloned it, so
    asking costs nothing.

    Returns:
        A per-test copy of the builder's dict -- ids and scalars.  A copy, as
        the dict is built once and shared, and a test that mutated it would be
        editing every later test's view of the world;
        ``_plain_data_or_raise`` is what makes a SHALLOW copy sufficient.

    Raises:
        LookupError: The test asked for a world without declaring one.  Not a
            defaulted empty dict: a test reading ids out of a world it never
            asked for would get ``KeyError`` on a name that IS registered, and
            the marker it is missing would be nowhere in that traceback.
    """
    world = _declared_world(request)
    if world is None:
        raise LookupError(
            f"{request.node.nodeid} asked for `seeded_world` without "
            f"declaring one. Add @pytest.mark.seeded_start_state(\"<name>\") "
            f"to the test or its class; known worlds: "
            f"{sorted(_SEEDED_STATE_BUILDERS)}"
        )
    _snapshot, ids = world
    return dict(ids)


@pytest.fixture()
def client(app, db):
    """Provide a Flask test client."""
    return app.test_client()


@pytest.fixture()
def tax_law(monkeypatch):
    """Return an installer that makes a test price under the tax law it names.

    **The ONE way a test changes the tax law** (ruling salary:R-SAL80, "Real
    law, tests may swap").  Every test prices under :data:`app.tax_law.LAW`,
    the law the app ships, unless it calls ``tax_law(law)``: a test whose
    figures were worked with no tax installs
    ``tests._test_helpers.NO_TAX_LAW``, and one worked on made-up figures
    installs those (the builders beside it), so its expected figures do not
    move when a new year of the real law is published.  The law a test
    installs must list its profiles' state, or the engine refuses them (plan
    step salary:X-at-3); ``EMPTY_TAX_LAW`` lists none and prices no profile.

    The installer replaces the module attribute, which is what the resolver
    (:mod:`app.services.tax_config_service`) and the Settings page read at
    call time; ``monkeypatch`` puts the shipped law back after the test.

    Returns:
        A function taking a :class:`app.tax_law.TaxLaw`, installing it, and
        returning it.
    """
    def install(law):
        monkeypatch.setattr(app_tax_law, "LAW", law)
        return law

    return install
