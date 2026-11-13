"""
Shekel Budget App -- the Jinja context processors.

The five processors Flask runs for each template the application renders, each
adding its values to what the route passed: the welcome checklist, the
companion role's id, the security-event banner, the MFA enrolment nag and the
tax-law notice.  The last two read one owner gate,
:func:`_current_user_is_owner`.  ``create_app`` calls
:func:`register_context_processors` and owns nothing about what any of them
computes.

**Its own module because the factory has a line ceiling and this is a concern,
not plumbing**, the reason ``app/__init__.py`` already gave its Jinja filters
(:mod:`app.jinja_filters`) and its error handlers (:mod:`app.error_handlers`)
theirs.  Merging ``dev`` into plan#1183's branch on 2026-11-02 took the
factory to 1,001 lines against pylint's 1,000, and these processors were its
largest single concern.

Each import a processor needs at request time stays inside it, as it did in
the factory.  This module is imported with the ``app`` package, so a top-level
import here would load at package import the modules the processors read:
:mod:`app.services.onboarding_service`, :mod:`app.utils.security_events` and
:mod:`app.services.tax_law_alarm`, none of which package import loads
(measured 2026-11-02).
"""

from flask import Flask, request


def register_context_processors(app: Flask) -> None:
    """Register the welcome-checklist, companion-role, security-event, MFA-nag
    and tax-law-notice context processors on *app*, in that order.

    Args:
        app: The Flask application being built.
    """

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
          * The user has no two-factor (``User.has_two_factor``: no
            ``MfaConfig`` row with ``is_enabled=True``).
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

        # The user's one two-factor answer, loaded once a request
        # (``User.has_two_factor``), so the nag asks nothing the login gate or
        # the page has already asked.
        return {"mfa_nag_visible": not current_user.has_two_factor}

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


def _current_user_is_owner() -> bool:
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
