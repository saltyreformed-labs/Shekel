"""
Shekel Budget App -- Application Factory

Creates and configures the Flask application.  Call create_app()
with an optional config_name ('development', 'testing', 'production')
to get a fully wired Flask instance.
"""

import importlib
import os
from datetime import datetime, timedelta, timezone

from flask import Flask, request, session as flask_session

from app.config import CONFIG_MAP
from app.db_transaction import register_transaction_boundary
from app.error_handlers import register_error_handlers
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.jinja_filters import register_template_filters
from app.login_gate import register_login_gate
from app.routes.static_pass import static_file_version
from app.url_converters import register_url_converters
from app.utils.logging_config import setup_logging
from app.utils.session_helpers import (
    SESSION_LAST_ACTIVITY_KEY,
    sign_in_created_at,
)


def create_app(config_name=None, *, init_ref_cache=True):
    """Build and return the configured Flask application.

    Args:
        config_name: One of 'development', 'testing', 'production'.
                     Defaults to the FLASK_ENV environment variable
                     or 'development' if unset.
        init_ref_cache: When True (the default -- used by Gunicorn and the
                     dev/test server), eagerly populate ``ref_cache`` and
                     register the ref-id Jinja globals at app creation.  Set
                     False ONLY by the two hosts that write ref rows before
                     reading them: the deploy (``scripts/init_database.py``),
                     which runs BEFORE the migrations and the seed that bring
                     new ref rows, and the manual repair seed
                     (``scripts/seed_ref_tables.py``, ruling R-BAL123).
                     ``ref_cache.init`` treats a missing row in an existing
                     ref table as fatal (a genuine seed/data drift), so
                     eager-initing it before the seed would raise -- exactly
                     the window both hosts must run through.
                     The runtime guard is unchanged: Gunicorn always inits.

    Returns:
        A fully configured Flask app instance.

    Raises:
        ValueError: When ``LC_ALL`` is not :data:`PINNED_LOCALE` (see
            :func:`_require_pinned_locale`), or ``config_name`` names no
            configuration.
    """
    # FIRST, before anything is built: the dev/test arm below writes schemas
    # and reference rows, so a refusal placed after it would refuse after
    # side effects.
    _require_pinned_locale()
    app = Flask(__name__)

    # --- Configuration ---------------------------------------------------
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")
    config_class = CONFIG_MAP.get(config_name)
    if config_class is None:
        raise ValueError(f"Unknown config_name: {config_name!r}")
    app.config.from_object(config_class)

    # --- Logging ---------------------------------------------------------
    setup_logging(app)

    if not app.config.get("TOTP_ENCRYPTION_KEY"):
        app.logger.warning(
            "TOTP_ENCRYPTION_KEY is not set. MFA/TOTP will be unavailable "
            "until this key is configured. See .env.example for details."
        )

    # --- Extensions -------------------------------------------------------
    _bind_extensions(app)

    # Flask-Login user loader callback.
    # Pylint: ``import-outside-toplevel`` -- app-factory pattern: importing a
    # model eagerly loads the whole ``app.models`` graph (every model is
    # registered there for Alembic discovery), so it is deferred into create_app
    # rather than run at ``app``-package import time.  (Verified not a cycle:
    # ``app.models.user`` imports cleanly on its own.)
    from app.models.user import User  # pylint: disable=import-outside-toplevel

    @login_manager.user_loader
    def load_user(user_id):
        """Load a user by ID for Flask-Login session hydration.

        Returns None (forcing re-login) for any of:
          * No sign-in of THIS user stamped the session: none at all, or
            another user's, as a remember-cookie restore lands on
            (plan#2173; ``sign_in_created_at`` in session_helpers).
          * Unknown user ID.
          * Deactivated user (``is_active=False``).
          * Session created before the most recent
            ``session_invalidated_at`` bump (commit C-08 -- F-002,
            F-003, F-032).
          * Session whose last activity is older than
            ``IDLE_TIMEOUT_MINUTES`` or absent (commit C-10 -- F-006).

        A None return triggers Flask-Login's "logged out" flow on the
        next request, redirecting to the configured ``login_view``.
        """
        session_created = sign_in_created_at(user_id)
        if session_created is None:
            return None
        user = db.session.get(User, int(user_id))
        if user is None:
            return None
        # Flask-Login's UserMixin.is_authenticated returns ``is_active``
        # (0.6.3), so a deactivated user loaded here would read as signed
        # out anyway.  Rejected explicitly all the same, so that deactivating
        # a user invalidates all of their existing sessions by this loader's
        # own rule rather than by a library default a mixin override or an
        # upgrade could change.  That default still carries one guarantee:
        # a save that waited on its owner's write lock is turned away when its
        # user was deactivated during the wait, because the login gate re-reads
        # ``is_active`` through it after this loader ran (plan step
        # balance:X-bn; pinned by tests/test_routes/test_xbn_sign_in_lock.py).
        if not user.is_active:
            return None
        # Check whether this session was created before the most recent
        # "log out all sessions" or password change event.
        invalidated_at = user.session_invalidated_at
        if invalidated_at is not None and (
            datetime.fromisoformat(session_created) < invalidated_at
        ):
            return None
        # Idle-timeout check (commit C-10 / F-006).  Reject the
        # session if ``_session_last_activity_at`` is older than
        # ``IDLE_TIMEOUT_MINUTES``.  See ``_idle_session_is_fresh``
        # docstring for the per-state policy (missing -> stale,
        # malformed/naive -> stale, future-dated -> fresh,
        # within-window -> fresh, beyond-window -> stale).
        if not _idle_session_is_fresh(app):
            return None
        return user

    # --- Template Filters --------------------------------------------------
    # Presentation-only filters live in app.jinja_filters; registering them
    # here keeps the factory under its statement/line ceiling.
    register_template_filters(app)

    # --- Context Processors -----------------------------------------------
    _register_context_processors(app)

    # --- URL converters ---------------------------------------------------
    # MUST precede the blueprints: Werkzeug resolves a rule's converters when
    # the rule is added to the map, so registering after would leave every
    # existing rule on the stock lax ``int`` (plan step X-ae, finding N-140).
    register_url_converters(app)

    # --- Blueprints ------------------------------------------------------
    _register_blueprints(app)

    # --- Error Handlers ---------------------------------------------------
    # Every response for a condition no single route owns -- the five HTTP
    # error pages plus the no-baseline answer -- lives in its own module, the
    # same extraction the Jinja filters took above and for the same reason.
    register_error_handlers(app)

    # --- Login gate (plan step bank_import:X-gi-4, ruling R-BI4) ---------
    # Every request is refused unless authenticated or declared public, in
    # ONE hook: no view carries ``@login_required``.  Registered here, after
    # ``csrf`` and ``limiter`` bound their own hooks, so an anonymous request
    # still meets the CSRF refusal and the rate ceiling before it is bounced,
    # as it did when the check ran inside the view.  Its place before the
    # activity stamp below is a pin, not a safeguard: the stamp writes only
    # for an authenticated session.
    register_login_gate(app)

    # --- Session activity refresh (commit C-10 / F-006) -------------------
    _register_session_activity_refresh(app)

    # --- Security Headers -------------------------------------------------
    _register_security_headers(app)

    # --- Static asset URL versioning (cache busting) ----------------------
    _register_static_versioning(app)

    # --- Create schemas & seed ref data (development convenience) --------
    # In production, schemas are managed by Alembic migrations and
    # reference data is seeded by the Docker entrypoint.
    if config_name in ("development", "testing"):
        with app.app_context():
            _ensure_schemas()
            _seed_ref_tables()

    # --- Reference Cache & Jinja Globals ---------------------------------
    # Initialize the ref_cache after seeding so enum members resolve to
    # database IDs.  Then expose cached status IDs as Jinja globals so
    # templates can compare status_id without querying the database.
    #
    # Skipped entirely when ``init_ref_cache`` is False: the deploy
    # (``scripts/init_database.py``) and the manual ref seed
    # (``scripts/seed_ref_tables.py``) run BEFORE the rows they seed exist,
    # and read the cache only after seeding, if at all.  ``ref_cache.init``
    # treats a missing row in an existing ref table as fatal, so
    # eager-initing it on a pre-migration database would raise and abort
    # the deploy (see the create_app docstring).  Gunicorn,
    # the dev server, and the test app always init (the default).
    #
    # ``ref_cache.init`` is resilient to missing ref tables during the
    # bootstrap window (a pending migration that creates a new ref
    # table, run via ``flask db upgrade``).  It returns the list of
    # tables that were unavailable; when that list is non-empty we
    # skip the Jinja globals registration because some accessors
    # would otherwise raise ``KeyError``.  Production never hits this
    # branch -- migrations have already run by the time Gunicorn
    # imports the app.
    # The pay-calendar input bounds every schedule form renders its ``min`` /
    # ``max`` from (plan step X-ad-a).  Registered UNCONDITIONALLY and outside
    # the ref-cache branch below: they are module constants with no database
    # precondition, and the three forms that read them must render correctly
    # in the bootstrap window too.
    # Pylint: ``import-outside-toplevel`` -- an app-factory deferral, matching
    # the ref-cache registration below; the module imports cleanly on its own.
    from app.jinja_globals import (  # pylint: disable=import-outside-toplevel
        register_pay_calendar_bound_globals,
    )
    register_pay_calendar_bound_globals(app)

    if init_ref_cache:
        # Pylint: ``import-outside-toplevel`` -- imported here, after the seed and
        # migration-check above and inside the app context below, so ``ref_cache.init``
        # populates against ready ref tables (an app-factory timing deferral, not a
        # cycle -- ``app.ref_cache`` imports cleanly on its own).
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- registered at the same post-seed point
        # as ref_cache above; the Jinja globals it adds read the populated cache.
        from app.jinja_globals import register_ref_id_globals  # pylint: disable=import-outside-toplevel
        with app.app_context():
            unavailable_ref_tables = ref_cache.init(db.session)

        if not unavailable_ref_tables:
            register_ref_id_globals(app)
        else:
            app.logger.warning(
                "ref_cache partial init: %d ref table(s) unavailable (%s). "
                "Jinja globals skipped; will populate on next app start after "
                "migrations run.",
                len(unavailable_ref_tables), ", ".join(unavailable_ref_tables),
            )

    app.logger.info("Shekel app created with config=%s", config_name)
    return app


#: The process locale every Shekel process runs under (ruling
#: ``recurrence:R-R92``, extending ``R-R54``; closes finding F-15).
#:
#: The same literal is set where each kind of process starts, because an
#: environment variable can only be set there: the image (``Dockerfile``),
#: the suite (``scripts/test.sh``, which CI runs through) and a host run
#: (``.env``, from ``.env.example``).  :func:`_require_pinned_locale` makes
#: any of them disagreeing with this one fail every start rather than drift.
PINNED_LOCALE = "C.UTF-8"


def _require_pinned_locale():
    """Refuse to build the application unless ``LC_ALL`` is :data:`PINNED_LOCALE`.

    Month and weekday names from ``strftime`` and :mod:`calendar` follow the
    process locale, at every site finding F-15 counted in ``app/`` and the
    templates and at every one added since.  They read English today
    because CPython never calls ``setlocale`` for ``LC_TIME``, which stays
    ``C`` whatever the environment says; so the
    NAMES cannot be the thing checked, because they would pass while
    measuring nothing.  What this checks is what ``setlocale(LC_ALL, "")``
    would adopt the day anything calls it -- a library or a future edit --
    and ``LC_ALL`` outranks every other locale variable, so pinning it pins
    that answer for every category at once.

    The comparison is exact: the pin sets the string ``C.UTF-8``, and a
    near-miss such as ``C.utf8`` means the process was started somewhere the
    pin was not.

    Raises:
        ValueError: When ``LC_ALL`` is unset or names any other locale.  The
            message names each place the pin is set.
    """
    actual = os.environ.get("LC_ALL")
    if actual != PINNED_LOCALE:
        seen = "unset" if actual is None else f"{actual!r}"
        raise ValueError(
            f"LC_ALL is {seen}; Shekel requires LC_ALL={PINNED_LOCALE} "
            "(ruling recurrence:R-R92).  The Dockerfile sets it, so a "
            "container whose image was built before that line must be "
            "rebuilt; ./scripts/test.sh exports it; for a host "
            f"'flask run' or script, add LC_ALL={PINNED_LOCALE} to .env "
            "(see .env.example)."
        )


def _bind_extensions(app):
    """Bind every Flask extension, and the transaction boundary, to *app*.

    Extracted from :func:`create_app` at plan step balance:X-i3, and the reason
    is a measurement rather than tidiness: adding
    :func:`~app.db_transaction.register_transaction_boundary`'s one call inline
    takes the factory to **51 statements against pylint's 50**, which CI fails
    on. Reproduce with ``pylint app/__init__.py`` after moving the call back --
    and use the project's own ``.pylintrc``, because an adversarial review of
    this step measured 49 with ``--rcfile=/dev/null --disable=all`` and
    concluded the extraction was unforced. Two configurations, two counts; the
    one the gate runs is the one that binds.

    It is the same extraction ``_register_context_processors`` and the four
    helpers beside it already are, so the factory reads as a list of what an
    application HAS rather than as the wiring of each.

    **The boundary's position in this function decides ONE thing, and it is
    not the request's kind.** The kind is decided by a ``request_started``
    receiver, which Flask sends before it runs ANY before-request hook, so no
    registration order here can put a statement in front of it (the paragraph
    this once replaced argued the opposite, and plan step balance:X-i3's
    correction refuted it). What the position DOES decide, since plan step
    balance:X-bn, is that the boundary's one before-request hook -- the one
    that takes a signed-in request's owner lock -- is registered after
    ``csrf`` and ``limiter``'s, so Flask runs it after the form-token check and
    the app-wide rate limit, and a request either one refuses never waits for
    that lock or holds it (ruling R-CC122). Moving the call above
    ``csrf.init_app``, or between it and ``limiter.init_app``, would undo that
    silently: the hook-order list in ``tests/test_routes/test_auth_required.py``
    fails on either move, and the form-token arm of
    ``tests/test_routes/test_xbn_sign_in_lock.py`` on the first (``TestConfig``
    switches the limiter off, so no behavioural arm reaches the second).

    ``setup_logging``'s hook still RESOLVES the acting user and hands it to
    :func:`app.db_transaction.bind_request_actor`, which tells the transaction
    already open and lets the boundary's listener tell every later one. That
    division does depend on the resolution happening before the route writes,
    which any before-request hook satisfies.

    Args:
        app: The Flask application being built.
    """
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    register_transaction_boundary(app)


def _register_context_processors(app):
    """Register Jinja2 context processors."""

    @app.context_processor
    def inject_onboarding():
        """Inject the welcome checklist so base.html can show/hide the banner.

        Hands the template an
        :class:`~app.services.onboarding_service.OnboardingChecklist`, which
        queries nothing until the template reads a fact (ruling
        ``balance:R-BAL117``, ledger row balance:N-328): a fragment that never draws
        the layout asks none of them.
        """
        # Pylint: ``import-outside-toplevel`` -- imported inside the request-time
        # context processor (app-factory pattern), kept out of ``app``-package
        # import.
        from flask_login import current_user  # pylint: disable=import-outside-toplevel

        if not current_user.is_authenticated:
            return {}

        # Onboarding is meaningless for companion users -- they share the
        # linked owner's budget data via linked_owner_id and cannot create
        # their own accounts, categories, pay periods, salary profiles, or
        # templates.  Omit the checklist entirely so the banner's `onboarding
        # is defined` guard in base.html evaluates False, and no checklist
        # fact is ever asked on a companion page.
        #
        # Pylint: ``import-outside-toplevel`` -- imported inside the request-time
        # context processor (app-factory pattern), kept out of ``app``-package
        # import.
        from app import ref_cache as _rc  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- RoleEnum imported lazily here for
        # the same app-factory deferral as the ref_cache import above.
        from app.enums import RoleEnum as _RoleEnum  # pylint: disable=import-outside-toplevel
        try:
            if current_user.role_id == _rc.role_id(_RoleEnum.COMPANION):
                return {}
        except (RuntimeError, KeyError):
            # ref_cache not yet initialized (e.g. during migration).  Fall
            # through to the checklist; owner users are the common case
            # during those windows and its queries still give the right
            # answer.
            pass

        # Pylint: ``import-outside-toplevel`` -- the checklist's service pulls in
        # the ``app.models`` graph, so it is imported inside the request-time
        # context processor (app-factory pattern), kept out of ``app``-package
        # import.
        from app.services.onboarding_service import (  # pylint: disable=import-outside-toplevel
            OnboardingChecklist,
        )

        return {"onboarding": OnboardingChecklist(current_user.id)}

    @app.context_processor
    def inject_role_ids():
        """Make role IDs available in all templates for role-based rendering."""
        # Pylint: ``import-outside-toplevel`` -- imported inside the request-time
        # context processor (app-factory pattern), kept out of ``app``-package
        # import.
        from app import ref_cache as _rc  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- RoleEnum imported lazily here for
        # the same app-factory deferral as the ref_cache import above.
        from app.enums import RoleEnum as _RoleEnum  # pylint: disable=import-outside-toplevel
        try:
            return {"COMPANION_ROLE_ID": _rc.role_id(_RoleEnum.COMPANION)}
        except (RuntimeError, KeyError):
            # ref_cache not yet initialized (e.g. during migration).
            return {"COMPANION_ROLE_ID": None}

    @app.context_processor
    def inject_security_event_banner():
        """Compute whether the security-event "was this you?" banner renders.

        Returns three template variables consumed by ``base.html`` and
        ``_security_event_banner.html``:

          * ``security_event_visible`` (bool) -- whether the partial
            should be included at all.  False for unauthenticated
            visitors and for users with no recorded event or with a
            dismissal more recent than the latest event.
          * ``security_event_kind`` (str | None) -- the bare kind
            string used by the partial to look up display copy.
            None when the banner is not visible.
          * ``security_event_display`` (dict) -- the
            ``KIND_DISPLAY`` mapping.  Always set so the partial can
            do ``security_event_display.get(...)`` without an extra
            None guard.

        The visibility decision is delegated entirely to
        :func:`~app.utils.security_events.banner_visible_for` so the
        same comparison drives both the rendering side and the
        dismiss-route's idempotency guard.

        Failure modes return ``security_event_visible=False`` so the
        banner errs on the side of NOT appearing during ambiguous
        startup windows.  Showing a banner that points to a broken
        endpoint would be worse than briefly missing the alert.

        Audit reference: F-091 / commit C-16 of the 2026-04-15
        security remediation plan.
        """
        # Pylint: ``import-outside-toplevel`` -- imported inside the request-time
        # context processor (app-factory pattern); security_events pulls in the
        # ``app.models`` graph, kept out of ``app``-package import.
        # pylint: disable=import-outside-toplevel
        from flask_login import current_user
        from app.utils.security_events import (
            KIND_DISPLAY, banner_visible_for,
        )

        # ``security_event_display`` is exposed unconditionally so the
        # partial can run a ``.get(kind)`` without a separate
        # ``defined`` check.  The dict reference itself carries no PII
        # and is cheap to ship into every template render.
        defaults = {
            "security_event_visible": False,
            "security_event_kind": None,
            "security_event_display": KIND_DISPLAY,
        }

        if not current_user.is_authenticated:
            return defaults

        # ``banner_visible_for`` reads two columns off the user row;
        # both are already loaded by Flask-Login's user_loader so
        # this is in-memory, not an extra query.
        if not banner_visible_for(current_user):
            return defaults

        return {
            "security_event_visible": True,
            "security_event_kind": current_user.last_security_event_kind,
            "security_event_display": KIND_DISPLAY,
        }

    @app.context_processor
    def inject_mfa_nag_visible():
        """Compute whether the owner-role MFA enrollment nag should render.

        Returns a single template variable, ``mfa_nag_visible``,
        consumed by ``base.html`` to decide whether to ``{% include %}``
        ``dashboard/_mfa_nag.html``.

        Visible iff ALL of the following hold:

          * The current request has an authenticated user
            (anonymous visitors hitting ``/login`` and friends never
            see the banner -- they cannot act on it).
          * The current user's ``role_id`` matches the cached owner
            role.  Companions are excluded by design: the audit
            scopes the nag to the de facto administrator role, and
            companions cannot reach ``/settings/companions`` or any
            other owner-only action that the finding cited as
            unprotected.
          * The user has no ``MfaConfig`` row with ``is_enabled=True``.
            A row with ``is_enabled=False`` (e.g. a setup that was
            started but never confirmed) still counts as "no MFA" and
            keeps the banner visible.
          * The current request endpoint is not part of the MFA
            enrolment / management flow itself (``auth.mfa_*``).
            Suppressing on those endpoints prevents the banner from
            stacking on top of the page that fulfils the nag (e.g.
            ``/mfa/setup`` already shows the QR code; a banner above
            it just adds noise).

        Failure modes are handled by returning ``mfa_nag_visible=False``
        so the banner errs on the side of NOT appearing during
        ambiguous startup windows -- showing a CTA that points to a
        broken endpoint would be worse than briefly missing the nag.

        Audit reference: F-095 / commit C-12 of the 2026-04-15
        security remediation plan.
        """
        # Pylint: ``import-outside-toplevel`` -- the imports in this context
        # processor run inside the request-time closure (app-factory pattern) and
        # pull in app extensions/models kept out of ``app``-package import.
        # pylint: disable=import-outside-toplevel
        from flask_login import current_user
        if not _current_user_is_owner():
            return {"mfa_nag_visible": False}

        # Suppress on the MFA enrolment / management endpoints.  ``request``
        # is bound during request handling; context processors only run in
        # that scope, so a missing ``endpoint`` (e.g. a 404 before route
        # matching) is treated as "show the banner" rather than swallowing
        # silently.
        endpoint = request.endpoint or ""
        if endpoint.startswith("auth.mfa_"):
            return {"mfa_nag_visible": False}

        from sqlalchemy import exists
        from app.models.user import MfaConfig
        has_enabled_mfa = db.session.query(
            exists().where(
                MfaConfig.user_id == current_user.id,
                MfaConfig.is_enabled.is_(True),
            )
        ).scalar()
        return {"mfa_nag_visible": not has_enabled_mfa}

    @app.context_processor
    def inject_tax_law_notice():
        """Hand ``base.html`` what the tax-law notice names, for the owner only.

        Returns one template variable, ``tax_law_gaps``: what
        :func:`app.services.tax_law_alarm.alarm` names as missing at the
        NOTICE stage on the owner's display-timezone today.  It is empty --
        and ``base.html`` draws no banner -- when the law is complete through
        the year that day calls for.  From November 1 that is next year, so
        the banner appears on every owner page until next year's law is added
        (rulings salary:R-SAL74, R-SAL86 and R-SAL87: every page, owner only,
        no close button).

        Owner only, as the MFA nag above is: the owner is the one who can have
        the law added.  It queries nothing -- the law is a module constant --
        so an owner's render, a partial that never draws the layout included,
        pays one clock read and a walk of the law's few years.
        """
        if not _current_user_is_owner():
            return {"tax_law_gaps": ()}
        # Pylint: ``import-outside-toplevel`` -- request-time imports inside the
        # context processor (app-factory pattern), kept out of ``app``-package
        # import like every processor above.
        # pylint: disable=import-outside-toplevel
        from app import tax_law
        from app.services import tax_law_alarm
        from app.utils import dates
        return {
            "tax_law_gaps": tax_law_alarm.alarm(
                tax_law.LAW, dates.display_today(), tax_law_alarm.Stage.NOTICE,
            ),
        }


def _current_user_is_owner():
    """Return True iff the request has an authenticated OWNER-role user.

    The one gate both owner-only banners read (the MFA nag and the tax-law
    notice).  It fails CLOSED: while the reference cache cannot name the owner
    role (mid-migration or mid-startup) nobody is confirmed an owner, so
    neither banner shows -- a banner in an ambiguous startup window is worse
    than one missed for a moment.

    Returns:
        bool: Whether the current user is authenticated and holds the owner
            role.
    """
    # Pylint: ``import-outside-toplevel`` -- request-time imports (app-factory
    # pattern), kept out of ``app``-package import like the processors'.
    # pylint: disable=import-outside-toplevel
    from flask_login import current_user
    from app import ref_cache as _rc
    from app.enums import RoleEnum as _RoleEnum
    if not current_user.is_authenticated:
        return False
    try:
        owner_role_id = _rc.role_id(_RoleEnum.OWNER)
    except (RuntimeError, KeyError):
        # ref_cache not yet initialised (e.g. during migration or
        # mid-startup).  Fail closed -- absent role data, we cannot
        # confirm the user is an owner.
        return False
    return current_user.role_id == owner_role_id


# Route modules registered (IN ORDER) by ``_register_blueprints``.  Each
# exposes a ``<name>_bp`` Flask Blueprint.  They are imported lazily inside the
# function (every route module imports ``app``, so a module-level import here
# would cycle).  Registration order is significant -- URL-rule precedence and
# error-handler resolution -- so this tuple IS the canonical order.  Adding a
# route module is a one-line append here; a module that breaks the ``<name>_bp``
# convention fails loud (``AttributeError`` at startup), never silently.
_BLUEPRINT_MODULES = (
    "auth", "grid", "transactions", "templates", "pay_periods", "accounts",
    "categories", "settings", "salary", "transfers", "savings", "loan",
    "card", "investment", "retirement", "charts", "analytics", "dashboard",
    "debt_strategy", "obligations", "health", "entries", "companion",
    "static_pass",
)


def _register_blueprints(app):
    """Import (lazily) and register every route blueprint.

    Lazy per-module import avoids the blueprint<->``app`` cycle that a
    module-level import would create.  See ``_BLUEPRINT_MODULES`` for the
    canonical registration order and the ``<name>_bp`` naming convention.
    """
    for name in _BLUEPRINT_MODULES:
        module = importlib.import_module(f"app.routes.{name}")
        app.register_blueprint(getattr(module, f"{name}_bp"))


def _idle_session_is_fresh(app):
    """Return True iff ``_session_last_activity_at`` is within the
    configured ``IDLE_TIMEOUT_MINUTES`` window.

    Fail-closed for unparseable input (commit C-10 / F-006).  False
    is returned -- never an exception -- for any of:

      * Malformed (non-ISO-8601) timestamp -- a tampered cookie that
        would otherwise raise ``ValueError`` from ``fromisoformat``
        and 500 the request.  ``flask.session`` is signed but not
        encrypted; an attacker who somehow forged a signature could
        still write garbage into a key, and the failure mode here
        must be "log in again", not "stack trace".

      * Naive (timezone-less) timestamp -- would raise ``TypeError``
        on the timezone-aware subtraction below.  Reject explicitly
        so the failure mode is consistent with the malformed case.

      * Age exceeds the configured window -- the legitimate "user
        walked away from their desk" case the constant exists to
        enforce.

    Missing key returns False: every sign-in stamps it in the request
    that signs in, so a session without one is not the app's (plan#2173).

    Future-dated timestamps (``elapsed < 0``) are treated as FRESH
    rather than rejected.  The two real-world causes are:

      * A backwards clock jump on the server (NTP correction, manual
        adjustment, VM resume from suspend).  Logging every active
        user out after such a jump is bad UX and the project's
        existing posture in MFA verify (which DOES reject) is
        load-bearing only because that gate is single-use; an
        always-on idle check should be more forgiving so a 100ms NTP
        slew does not invalidate every cookie in the system.

      * A forged future-dated cookie.  Forgery requires the SECRET_KEY
        signature, and an attacker with SECRET_KEY can already mint
        any cookie value; rejecting future timestamps adds no
        defensive value beyond the signature itself.

    Args:
        app: The Flask application instance whose
            ``IDLE_TIMEOUT_MINUTES`` config value drives the
            comparison.  Passed in (not pulled from
            ``flask.current_app``) so the helper is testable in a
            request context bound to a specific app.

    Returns:
        bool: True if the session may continue; False if ``load_user``
            must reject it.
    """
    raw = flask_session.get(SESSION_LAST_ACTIVITY_KEY)
    if raw is None:
        return False
    try:
        last_activity = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if last_activity.tzinfo is None:
        return False
    elapsed = datetime.now(timezone.utc) - last_activity
    if elapsed < timedelta(0):
        # Future-dated last-activity is from a backwards clock jump
        # or (with SECRET_KEY compromise) a forged cookie.  Treat as
        # fresh -- see docstring for the threat-model rationale.
        return True
    threshold = timedelta(minutes=app.config["IDLE_TIMEOUT_MINUTES"])
    return elapsed <= threshold


def _register_session_activity_refresh(app):
    """Update ``_session_last_activity_at`` on every authenticated request.

    Pairs with the idle-timeout check in ``load_user``: the loader
    rejects the session when the gap to ``now()`` exceeds
    ``IDLE_TIMEOUT_MINUTES``; this hook keeps refreshing the gap to
    zero on every interaction so an actively-used session never
    crosses the threshold.

    Skips:

      * Static asset requests (``request.endpoint == 'static'``) --
        these would burn a Set-Cookie write per CSS / JS file with
        no auth signal in return.  Static files are not gated on
        authentication anyway.

      * Unauthenticated requests -- ``current_user.is_authenticated``
        triggers ``load_user``, which is a no-op when no session
        cookie is present.  Writing a stamp for an unauthenticated
        visitor would create a session cookie out of thin air.

    The authenticated branch writes the stamp UNCONDITIONALLY (every
    request, not just every Nth) because ``flask.session`` does not
    expose a "session.modified=False if value already current" path:
    the only way to keep the cookie's last-activity in sync with
    real activity is to write it on every request.  Cookie size
    impact is negligible (one ISO-8601 string).
    """
    # Pylint: ``import-outside-toplevel`` -- imported inside this registration
    # helper (app-factory pattern), kept out of ``app``-package import.
    # pylint: disable=import-outside-toplevel
    from flask_login import current_user

    @app.before_request
    def _refresh_last_activity():
        """Stamp ``_session_last_activity_at`` for the current request.

        Runs before every route.  See the enclosing
        ``_register_session_activity_refresh`` docstring for the
        skip conditions.
        """
        if request.endpoint == "static":
            return
        # Accessing current_user.is_authenticated triggers load_user
        # (which performs the idle-timeout check using the PRE-update
        # value -- correct: we want to reject sessions whose LAST
        # activity, not THIS one, is stale).  If the loader rejects
        # the session this comparison evaluates to False and we skip
        # the stamp; the in-flight request still proceeds as
        # unauthenticated, and the stale cookie is naturally replaced
        # by Flask-Login's logout flow on the next request.
        if not current_user.is_authenticated:
            return
        flask_session[SESSION_LAST_ACTIVITY_KEY] = (
            datetime.now(timezone.utc).isoformat()
        )


# Content Security Policy directives.  Bound at module load (not on
# every request) because the policy is static -- there is no per-request
# nonce or hash.  Built as a tuple so the join order is stable and
# reviewable in diffs.
#
# Each origin must be 'self'.  All third-party JS, CSS, and fonts are
# vendored under app/static/vendor/ (see app/static/vendor/VERSIONS.txt)
# so external origins can be dropped entirely.  See audit findings
# F-036, F-037, F-097.
_CSP_DIRECTIVES = (
    "default-src 'self'",
    # Scripts: self only.  No CDN origins, no 'unsafe-inline', no
    # 'unsafe-eval'.  Inline event handlers (onclick=, onchange=) and
    # inline <script> blocks are blocked by this policy; all behaviour
    # is in external JS files under app/static/js/.
    "script-src 'self'",
    # Styles: self only.  No 'unsafe-inline' (closes the CSS attribute-
    # selector keylogging path documented in F-036).  No CDN origins.
    # Inline style="..." attributes are blocked.  Dynamic per-element
    # styling (e.g. progress-bar widths) lives behind data-* attributes
    # and a tiny JS module that sets el.style.* via DOM property setters
    # (which are allowed by script-src 'self', not style-src).
    "style-src 'self'",
    # Fonts: self only.  Inter and JetBrains Mono are vendored; Bootstrap
    # Icons font travels with its CSS.
    "font-src 'self'",
    # Images: self plus data: URIs (used for inline favicons and the
    # MFA QR code rendered as a data: URL by mfa_service).
    "img-src 'self' data:",
    # XHR / fetch / WebSocket: self only.  HTMX uses fetch under the hood.
    "connect-src 'self'",
    # Modern clickjacking control.  Authoritative on browsers that
    # implement CSP Level 2; X-Frame-Options: DENY (set below) is the
    # legacy fallback.  See audit finding F-097.
    "frame-ancestors 'none'",
    # Locks the document base URL to this origin so injected <base href=>
    # cannot redirect relative URLs through an attacker-controlled prefix.
    "base-uri 'self'",
    # Form posts must target this origin.  Defends against an injected
    # <form action="https://evil/"> exfiltrating credentials.
    "form-action 'self'",
)
_CSP_HEADER = "; ".join(_CSP_DIRECTIVES)


def _register_security_headers(app):
    """Add security headers to every response.

    The headers and the CSP closed in this hook implement the audit
    Phase-1 hardening bundle (Commit C-02).  See findings F-017, F-018,
    F-019, F-036, F-037, F-096, F-097 for the per-control rationale.
    """

    @app.after_request
    def set_security_headers(response):
        # Defense-in-depth headers.  X-Content-Type-Options blocks MIME
        # sniffing; X-Frame-Options is the legacy clickjacking control
        # superseded by CSP frame-ancestors but still useful for older
        # browsers.  Referrer-Policy avoids leaking full URLs to
        # third-party origins on outbound clicks.  Permissions-Policy
        # disables three sensors the app never uses.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = _CSP_HEADER

        # HSTS: 1 year max-age, includeSubDomains.  All Shekel traffic
        # already arrives via HTTPS (Cloudflare Tunnel + nginx), so the
        # header simply locks browsers into that posture for a year.
        # 'preload' is intentionally OFF.  Adding 'preload' is a one-way
        # commitment to the public HSTS preload list (delisting takes
        # months); see docs/runbook.md "HSTS preload" for the procedure
        # to enable it later if desired.  Audit finding F-018.
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        # Cache-Control: no-store on every dynamic response so a
        # logged-out user cannot reconstruct authenticated pages from
        # the browser back button.  Audit finding F-019.
        #
        # Static assets are intentionally excluded -- they carry no
        # session data and must stay cacheable so the user does not
        # re-fetch Bootstrap on every navigation.  Who serves them
        # depends on the deploy mode: the bundled stack's nginx serves
        # /static/ itself (``expires 7d`` + ``immutable``) before Flask
        # sees the request, while the shared-mode stack (the actual
        # production host) proxies /static/ through to Flask's static
        # handler, so the headers set here ARE production's static
        # headers there.
        #
        # A static request whose ``v=`` parameter matches the file's
        # current content hash (the parameter
        # _register_static_versioning appends to every
        # url_for('static') URL) is immutable by construction -- a
        # changed file changes its URL -- so it gets a year-long
        # public lifetime.  The hash is verified against the file
        # first so a stale or forged ``v=`` cannot pin wrong bytes;
        # unversioned or mismatched requests keep Flask's default
        # conditional caching (no-cache + ETag revalidation).
        if request.endpoint == "static":
            requested_version = request.args.get("v")
            filename = (request.view_args or {}).get("filename", "")
            if requested_version and requested_version == static_file_version(
                app.static_folder, filename
            ):
                response.headers["Cache-Control"] = (
                    f"public, max-age={_VERSIONED_STATIC_MAX_AGE_SECONDS}, "
                    "immutable"
                )
        else:
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate"
            )
            # HTTP/1.0 fallback for caches that ignore Cache-Control.
            response.headers["Pragma"] = "no-cache"
        return response


# Lifetime for content-versioned static responses: one year, the
# conventional ceiling for immutable assets.  Safe at this length
# because the ``v=`` content hash in the URL changes whenever the
# file's bytes change, so a cached entry can never be served against
# a page that references newer content.
_VERSIONED_STATIC_MAX_AGE_SECONDS = 31536000


def _register_static_versioning(app):
    """Append a content-hash ``v=`` parameter to static asset URLs.

    Every ``url_for('static', filename=...)`` URL the app emits (each
    stylesheet, script, image, and font link in the templates) gains a
    short content hash of the file's bytes:
    ``/static/css/base.css?v=ab12cd34ef56``.  The URL changes exactly
    when the content changes, which closes the cache-busting gap
    recorded in ``docs/design/css_architecture_audit.md``: the bundled
    deploy's nginx serves /static/ with a 7-day ``immutable`` lifetime,
    and the shared deploy's after-request hook above grants
    verified-versioned requests a year-long lifetime -- both safe only
    when URLs are content-addressed.  A file that does not resolve
    emits its URL unversioned, preserving the previous behavior.
    """

    @app.url_defaults
    def add_static_version(endpoint: str, values: dict[str, object]) -> None:
        """Inject ``v=<content hash>`` into static URL values."""
        if endpoint != "static":
            return
        filename = values.get("filename")
        if not isinstance(filename, str) or "v" in values:
            return
        version = static_file_version(app.static_folder, filename)
        if version is not None:
            values["v"] = version


# Hardcoded allowlist of PostgreSQL schemas used by the application.
# Used by _ensure_schemas() for DDL statements that cannot use bind
# parameters.  Any addition here requires a corresponding Alembic
# migration and updates to tests/conftest.py and scripts/init_db.sql.
_ALLOWED_SCHEMAS = frozenset({"ref", "auth", "budget", "salary", "system"})


def _ensure_schemas():
    """Create PostgreSQL schemas if they do not exist (dev/test only).

    Schema names are validated against _ALLOWED_SCHEMAS, a hardcoded
    frozenset.  DDL identifiers (schema names, table names) cannot use
    bind parameters in PostgreSQL, so an f-string is required.  The
    allowlist ensures only known-safe values are interpolated.
    """
    for schema_name in _ALLOWED_SCHEMAS:
        # DDL identifiers cannot use bind parameters.  Schema names
        # are from _ALLOWED_SCHEMAS -- not user input.
        db.session.execute(
            db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        )
    db.session.commit()


def _seed_ref_tables():
    """Seed reference lookup tables if empty (dev/test only).

    In production the deploy seeds inside entrypoint step 3's one
    transaction (``scripts/init_database.py``).  Idempotent -- skips rows that
    already exist.  Silently skips the entire seed if the tables
    haven't been created yet (e.g. first test-session run before
    ``create_all()``).

    Delegates to ``app.ref_seeds.seed_reference_data`` -- the single
    source of truth for ref-table seeding across the application
    factory (this function), the production deploy script, the test
    fixture stack, and the test-template builder.  The outer
    tolerance of a SCHEMA BEHIND THE CODE is the only thing this
    wrapper contributes -- it handles the boot-time race where the
    factory runs the seed before a pending migration has created a
    table or added a column (test setup, the development entrypoint,
    ``flask db upgrade``).  It is read off the SQLSTATE
    (:func:`~app.utils.db_errors.is_schema_behind_code`), not the
    exception class, so a malformed statement, an absent privilege or a
    trigger's refusal propagates instead of being skipped as a schema
    that is merely behind (plan step balance:X-dj).
    """
    # Pylint: ``import-outside-toplevel`` -- ref_seeds (which imports the
    # ``app.models`` graph) is loaded only on the dev/test bootstrap path, not at
    # every ``app``-package import (app-factory deferral, not a cycle).
    # pylint: disable=import-outside-toplevel
    from sqlalchemy.exc import ProgrammingError

    from app.ref_seeds import seed_reference_data
    from app.utils.db_errors import is_schema_behind_code

    try:
        seed_reference_data(db.session)
        db.session.commit()
    except ProgrammingError as exc:
        db.session.rollback()
        if not is_schema_behind_code(exc):
            raise
