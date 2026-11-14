"""
Shekel Budget App -- application-level error handlers.

Every response the application produces for a condition no single route owns:
the six HTTP error pages, the ONE answer to a user with no baseline scenario
(plan step X-v, ruling R-BW), to a pay calendar that cannot be derived (plan
step pay_calendar:C4-b-2), and to a salary profile in a state the tax law does
not list (plan step salary:X-at-3).

**Its own module because the factory has a line ceiling and this is a
concern, not plumbing.**  ``app/__init__.py`` already extracted its Jinja
filters to :mod:`app.jinja_filters` for exactly that reason; the
``BaselineMissingError`` handler is what pushed the factory over 1,000 lines,
and a registration helper that renders nine responses is not the app factory's
job.  ``create_app`` calls :func:`register_error_handlers` and owns nothing
about what any of them says.
"""

import logging

from flask import current_app, render_template, request
from flask.typing import ResponseReturnValue
from flask_login import current_user

from app.exceptions import BaselineMissingError, UnsupportedStateError
from app.extensions import db
from app.services.pay_calendar import PayCalendarError
from app.utils.log_events import (
    ACCESS,
    ERROR,
    EVT_BASELINE_MISSING,
    EVT_PAY_CALENDAR_UNDERIVABLE,
    EVT_RATE_LIMIT_EXCEEDED,
    EVT_SALARY_STATE_UNSUPPORTED,
    log_event,
)


#: This module's logger.  Shared by every handler below rather than one
#: constant per handler.
_LOGGER = logging.getLogger(__name__)

#: HTTP methods that change nothing, so a fragment answering them may safely
#: swap nothing.  A MUTATING request that swaps nothing is a button that did
#: not work and said so to no one -- see :func:`register_error_handlers`'s
#: no-baseline handler.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _recovery_response(template_name: str, **context: object) -> ResponseReturnValue:
    """Answer a recoverable SETUP state, honouring what htmx will swap.

    **The shared half of the three application-error handlers below**,
    extracted when plan step ``pay_calendar:C4-b-2`` added the second one
    rather than copying its three lines; plan step salary:X-at-3 added the
    third.  **A safe-method htmx request is answered with nothing, a
    background load and a USER's press alike**, because the request does not
    say which it was: the Analytics page's first tab load (``hx-trigger=
    "load"``) stays on its loading spinner, and a pressed tab pill
    highlights while the previous tab's figures stay under it.  And the
    MUTATING branch's "card" is the recovery template, which extends
    ``base.html``, so htmx swaps a whole page's body -- its script tags
    included, which run again and bind their listeners twice -- into the
    target (unmeasured in a browser).  Ledger row salary:SAL-600 owns both,
    for all three (the developer, 2026-10-08, "Keep the rule; file it").
    The rule it encodes belongs to the CLIENT, not to any of the three
    errors: htmx swaps only 2xx responses and swaps them into
    whatever target the request named, so both facts about the response are
    decided by what the request was rather than by which state was found.

    * **A SAFE-method htmx request gets ``204 No Content``.**  An idempotent
      poll must leave the live DOM alone; swapping a setup card into a balance
      cell because a refresh happened to run is worse than showing nothing.
    * **A MUTATING htmx request gets the card**, because 204 there is a button
      the user pressed that did nothing, silently and forever -- measured on
      ``POST /debt-strategy/calculate`` during plan step X-v2's adversarial
      review.
    * **Everything else gets the full page.**

    **Status ``200`` in both non-204 cases, and htmx is the reason rather than
    precedent.**  Any honest-looking 4xx or 5xx would make htmx swap NOTHING,
    which is the silence these handlers exist to remove.  What carries the
    failure signal is each handler's own ``ERROR`` event -- the channel an
    operator alerts on.  A status code no client acts on is not one.

    Args:
        template_name: The recovery template to render for a full page or a
            mutating fragment.
        **context: What the template names (the unsupported-state page names
            the profile); none for the two pages that name nothing.

    Returns:
        ``("", 204)`` for a safe-method htmx request, else the rendered
        template at the implicit ``200``.
    """
    if request.headers.get("HX-Request") and request.method in _SAFE_METHODS:
        return "", 204
    return render_template(template_name, **context)


def register_error_handlers(app):
    """Register every application-level error response on *app*.

    Args:
        app: The Flask application being built by
            :func:`app.create_app`.
    """

    @app.errorhandler(400)
    def bad_request(_e):
        """Handle 400 Bad Request errors.

        Common triggers: CSRF token validation failure (Flask-WTF
        rejects the request), malformed form data, or invalid
        request syntax.
        """
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def forbidden(_e):
        """Handle 403 Forbidden errors.

        Common triggers: permission denied, accessing a resource
        that exists but the user is not authorized to view.
        """
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def page_not_found(_e):
        """Handle 404 Not Found errors.

        Triggers when the requested URL does not match any route.
        """
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def payload_too_large(_e):
        """Handle 413 Payload Too Large.

        Werkzeug raises ``RequestEntityTooLarge`` when a request body
        exceeds ``MAX_CONTENT_LENGTH``, which the app sets because plan
        step ``bank_import:X-f6a-1`` gives it its first file upload.
        Without this handler that is Werkzeug's own bare HTML page --
        outside the app's layout, with no navigation back and no mention
        of what the limit actually is.

        The limit is read from the live config rather than restated, so
        the page cannot come to disagree with the ceiling it describes.
        """
        return render_template(
            "errors/413.html",
            max_upload_kb=current_app.config["MAX_CONTENT_LENGTH"] // 1024,
        ), 413

    @app.errorhandler(429)
    def rate_limit_exceeded(_e):
        """Return the 429 error page with a Retry-After header.

        Also emits a structured ``rate_limit_exceeded`` log event
        (audit Commit C-15 / finding F-146) so an operator can alert
        on sustained rate-limit pressure from the observability
        stack.  Without this event, a slow credential-stuffing
        campaign that stays under each individual route's per-window
        ceiling would still trigger the global default ceiling
        (``200 per hour;30 per minute``) repeatedly with no signal
        for incident response -- the rate limit successfully blocks
        the attack from succeeding, but no human ever sees the
        spike.

        The event runs under WARNING level (not ERROR -- a single
        rate-limit hit is not in itself an outage), under the
        ACCESS category so it groups with the other access-control
        events the SOC dashboard already filters on.  ``path`` and
        ``remote_addr`` go into the structured payload so a Loki
        query can pivot on either; the IP comes from
        ``request.remote_addr``, which is the client's own address:
        :func:`app.utils.client_address.believe_forwarded_address_from`
        puts it there for a caller on ``FORWARDED_ALLOW_IPS``.
        """
        log_event(
            _LOGGER,
            logging.WARNING,
            EVT_RATE_LIMIT_EXCEEDED,
            ACCESS,
            "Rate limit exceeded",
            path=request.path,
            method=request.method,
            remote_addr=request.remote_addr,
        )
        response = app.make_response(
            (render_template("errors/429.html"), 429)
        )
        # 900 seconds = 15 minutes, matching the rate limit window.
        response.headers["Retry-After"] = "900"
        return response

    @app.errorhandler(500)
    def internal_server_error(_e):
        """Handle 500 Internal Server Error.

        Triggers on unhandled exceptions in route handlers or
        service layer code.  The rollback clears any failed transaction
        so the layout's own queries (the onboarding checklist's, as the
        template reads it) can run and the custom error template renders
        instead of a blank page.
        """
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.errorhandler(BaselineMissingError)
    def baseline_missing(error):
        """Answer "this user has no baseline scenario" -- once, for the whole app.

        THE no-baseline policy (plan step X-v, ruling R-BW).  Every balance the
        app produces is scoped to a baseline scenario, so a user without one has
        no balance any surface can answer.  Before this, each surface that could
        reach that state decided for itself, and between them they decided
        several different things -- a full-page recovery card, a ``204``, a 404,
        a blank cockpit reporting a fabricated ``$0.00`` net worth, an anchor
        cache column presented as a current balance, a balance sheet asserting
        ``in_balance`` over a ledger it could not read, a hidden chip, and three
        surfaces that raised into a 500.  Plan step X-v's own entry carries the
        census; this is the one answer that replaced them:

        * **A SAFE-method HTMX request gets 204 No Content**, which is the
          contract the grid's self-refresh partials already shipped: an
          idempotent poll must leave the live DOM alone, never swap a setup card
          into a balance cell.
        * **A MUTATING HTMX request gets the card**, because 204 there means the
          user pressed a button and nothing happened, silently and forever --
          measured on ``POST /debt-strategy/calculate``, which answered ``204``
          with an empty body until X-v2's adversarial review caught it.  The
          card swapping into the results target says why the action did nothing
          and offers the repair.
        * **Everything else gets the recovery page.**

        **Status ``200``, and the reason is htmx, not precedent.**  htmx swaps
        only 2xx responses, so any honest-looking 4xx/5xx would make the two
        HTMX branches above render NOTHING -- the silence they exist to fix.
        The response is also a complete, actionable page rather than a failure
        the browser should surface as one.  What carries the failure signal is
        the ERROR event below, which is the channel an operator alerts on; a
        status code that no client acts on is not.

        **It is quiet on screen and LOUD in the log.**  No code path produces a
        baseline-less owner -- registration writes one, nothing deletes or
        un-baselines one, no path promotes a companion (measured 2026-07-28,
        and asserted by ``scripts/integrity_check`` DC-08) -- so an occurrence
        is either data changed outside the application or a caller resolving a
        context for the WRONG user.  The event carries BOTH ids for exactly that
        second case: ``user_id`` is who asked, ``context_user_id`` is who the
        raise was resolved for, and they differ only when a caller has the wrong
        one.  Logging only the requester would have made the event blind in the
        one failure it exists to diagnose (X-v2's adversarial design review).

        The rollback matches the 500 handler's: an exception may have left the
        session in a failed transaction, and the recovery page's own context
        processors query.

        Args:
            error: The raised
                :class:`~app.exceptions.BaselineMissingError`; its message
                names the repair and it carries the user id it was resolved
                for.  Logged, never shown.

        Returns:
            ``("", 204)`` for a safe-method HTMX request, else the rendered
            recovery page.
        """
        db.session.rollback()
        log_event(
            _LOGGER, logging.ERROR, EVT_BASELINE_MISSING, ERROR,
            "Balance requested for a user with no baseline scenario",
            # ``auth_helpers._safe_user_id`` is this same read with the same
            # rationale, and is deliberately NOT imported: that module does
            # ``from app import ref_cache``, so importing it here would close a
            # cycle back into this package's own initialisation.
            user_id=getattr(current_user, "id", None),
            context_user_id=error.user_id,
            path=request.path,
            method=request.method,
            detail=str(error),
        )
        return _recovery_response("errors/no_baseline.html")

    @app.errorhandler(PayCalendarError)
    def pay_calendar_underivable(error):
        """Answer "this user's pay calendar cannot be derived" -- once, for the app.

        **Plan step ``pay_calendar:C4-b-2``, and it is the deferral ledger row
        P35 recorded rather than a new idea.**  That row weighed a
        ``BaselineMissingError``-shaped handler and deferred it to the owner of
        the step that would delete the fallback; this is that step, and the
        developer ruled on 2026-09-01 that the handler ships with it rather
        than being filed again.

        Until now ``PayCalendarError`` had no arm at all, so it reached the
        browser through the bare ``500`` handler: an unstyled page on a money
        screen, and inside an htmx fragment something worse -- htmx does not
        swap a 500, so a click did nothing and said nothing.  Fourteen raise
        sites across ``pay_calendar`` and ``paycheck_calculator`` shared that
        answer (AST census 2026-09-01; a grep over-counts, because the class's
        own docstrings name it).

        **Two states reach it and the page deliberately does not distinguish
        them.**  One is ordinary and repairable -- an owner with no
        ``budget.pay_schedule`` row, which since ``fk_pay_periods_schedule``
        means an owner with no paydays: a companion, or anyone before their
        first batch.  The other is a broken invariant no write door produces, a
        payday set that cannot define a calendar.  The OWNER can act on the
        first and can do nothing about the second, and both leave every
        per-period figure unanswerable, so the page offers the one repair that
        exists and the LOG carries which state it was.  That is the same split
        the no-baseline handler makes: quiet on screen, loud in the log.

        **This does not silence the invariant.**  ``PayCalendarError`` is still
        raised, still an ``ERROR`` event, and still names the path and the
        message that says which state fired.  What changed is that the user
        gets a page instead of a stack trace, and a fragment gets something
        rather than nothing.

        The rollback matches the two handlers above it: the raise may have left
        the session in a failed transaction and the recovery page's own context
        processors query.

        Args:
            error: The raised
                :class:`~app.services.pay_calendar.PayCalendarError`.  Its
                message names which of the two states fired; logged, never
                shown, because it is written for an operator.

        Returns:
            ``("", 204)`` for a safe-method htmx request, else the rendered
            recovery page (:func:`_recovery_response`).
        """
        db.session.rollback()
        log_event(
            _LOGGER, logging.ERROR, EVT_PAY_CALENDAR_UNDERIVABLE, ERROR,
            "A pay calendar could not be derived for this request",
            # The same read, and the same reason for not importing
            # ``auth_helpers._safe_user_id``, that the handler above gives.
            user_id=getattr(current_user, "id", None),
            path=request.path,
            method=request.method,
            detail=str(error),
        )
        return _recovery_response("errors/no_pay_calendar.html")

    @app.errorhandler(UnsupportedStateError)
    def salary_state_unsupported(error):
        """Answer "this salary profile is in a state the tax law does not list" -- once.

        **Plan step salary:X-at-3** (ruling **salary:R-SAL78**; the developer,
        2026-10-08, "One fix-it page", amending R-SAL130).  The paycheck engine
        refuses such a profile rather than pricing its state tax at ``$0.00``
        (:func:`app.services.tax_config_service.profile_tax_series`), and every
        full page that prices it -- the grid, the dashboards, the salary pages
        -- would otherwise answer with the bare ``500`` page, whose one button
        leads to another page that prices the same profile.  The repair is the
        profile's own edit page, which prices nothing, so this page names the
        profile and links there (a companion is told to ask the owner): the
        shape of the two handlers above, and the reason the class is answered
        here rather than left to the 500 page as
        :class:`~app.exceptions.RequiredRecordMissing` is (that one has no
        repair the owner can make on a page).  An htmx ``GET`` -- the
        Analytics tabs, the Taxes tab among them -- is answered with nothing,
        and a mutating htmx request with the whole page swapped into its
        target, by :func:`_recovery_response`'s rule (ledger row
        salary:SAL-600).

        **Quiet on screen, loud in the log**, as the two above: both profile
        doors refuse such a state, so an occurrence is a row older than a
        release that dropped a state, or one written around the doors.  The
        event names the profile and its state.

        The rollback matches the handlers above: the raise may have left the
        session in a failed transaction -- a profile edit whose regeneration
        refused, say, which must not commit -- and the page's own context
        processors query.

        Args:
            error: The raised :class:`~app.exceptions.UnsupportedStateError`,
                which carries the profile's id, name and stored state.

        Returns:
            ``("", 204)`` for a safe-method htmx request, else the fix-it page
            (:func:`_recovery_response`).
        """
        db.session.rollback()
        log_event(
            _LOGGER, logging.ERROR, EVT_SALARY_STATE_UNSUPPORTED, ERROR,
            "A salary profile is in a state the tax law does not list",
            # The same read, and the same reason for not importing
            # ``auth_helpers._safe_user_id``, that ``baseline_missing`` gives.
            user_id=getattr(current_user, "id", None),
            profile_id=error.profile_id,
            state_code=error.state_code,
            path=request.path,
            method=request.method,
            detail=str(error),
        )
        return _recovery_response("errors/unsupported_state.html", error=error)
