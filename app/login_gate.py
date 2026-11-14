"""
Shekel Budget App -- The login gate: every request is refused unless declared

**An anonymous visitor reaches the endpoints named in :data:`PUBLIC_ENDPOINTS`
and nothing else.**  One ``before_request`` hook decides it for the whole
application, so a route is protected by EXISTING rather than by remembering a
decorator: there is no ``@login_required`` in ``app/`` any more, and a view
that wants to be reachable without a session says so HERE, by name, where a
reviewer reads the entire policy in one frozenset.

**Why a gate and not 213 decorators** (plan step ``bank_import:X-gi-4``,
finding **N-402**, developer ruling **bank_import:R-BI4**).  The decorator
model made "which routes are protected" a property that had to be re-stated
on every view and then RE-COUNTED by a hand-maintained sweep,
``tests/test_routes/test_auth_required.py``, whose own docstring said a new
route "must be added" to its list.  Censused 2026-09-11: the application
served 222 ``(method, rule)`` pairs, 213 of them decorated, and the sweep
listed 124 -- it had drifted by 89 pairs with every check green, because a
gate whose coverage is a list somebody remembers to update reports clean over
exactly what it cannot see.  Every decorator was in fact present, so the
behaviour was right and only the PROOF was missing; this module makes the
proof unnecessary by making the defect unrepresentable.  A route added a year
from now is gated without its author knowing this module exists.

**It is Flask-Login's own ``login_required``, hoisted** -- the same three
tests in the same order, calling the same
:meth:`~flask_login.LoginManager.unauthorized`, so the redirect, the flashed
sentence and the ``next`` parameter are exactly what every view produced
before.  ``OPTIONS`` is exempt because the decorator exempted it
(:data:`flask_login.config.EXEMPT_METHODS`, read from the library rather than
re-spelled), and ``LOGIN_DISABLED`` switches the gate off because it switched
the decorator off -- a throwaway app whose subject is an error page or a rate
ceiling sets it, and no shipped configuration does.

**Where it differs, deliberately: a URL that matches NO route is refused
too.**  The decorator lived on the view, so an anonymous request for a path
Werkzeug could not match -- a typo, or a POST-only door hit with GET -- was
answered by Flask's own 404 or 405 before any view ran, and an anonymous
prober could tell which paths exist by the status code.  A gate keyed on the
endpoint sees that request as ``endpoint is None``, which is not a declared
public endpoint, and bounces it to the login page like every other.  The
developer chose the fail-closed reading on 2026-09-11; an authenticated user
still gets the 404 or 405.

**Where it sits.**  Registered AFTER ``csrf`` and ``limiter`` bind their own
``before_request`` hooks, which is the part of the order that carries
behaviour: an anonymous POST with no token still meets the CSRF refusal
first, and an anonymous flood on a route is still counted against the rate
ceiling before it is bounced, exactly as when the check ran inside the view
(a flood on NO route is limiter-exempt, as it was when Flask answered 404).
It sits after the owner's write lock too, whose hook asks a signed-in
command's sign-in again once the lock is held and answers one that ended
during the wait as this gate answers a signed-out request, before the gate
runs (:mod:`app.db_transaction`, ruling plan#4070).  It
also sits BEFORE the session-activity stamp, and that half is a pin rather
than a safeguard: the stamp writes only for an authenticated session, so
nothing would differ if the gate followed it -- when the check lived on the
view it DID follow it, the view being dispatched after every hook.  The
order is pinned so that it is one thing, in
``tests/test_routes/test_auth_required.py``.

**A signed-in user without two-factor is held at its setup**, whatever their
role: every account must use two-factor, and there is no switch (plan#3452,
widening ruling plan#3434 from companions to every account), and no account
can turn it off (ruling plan#3604).  Such a user reaches the endpoints in
:data:`TWO_FACTOR_SETUP_ENDPOINTS` -- setup, its confirmation, and signing out
-- and the public ones, and nothing else: every other request is sent to
setup without running, an htmx one by ``HX-Redirect``
(:func:`~app.utils.auth_helpers.full_page_redirect`), so a POST is never
performed.  The check is per request and reads the database's answer,
:attr:`~app.models.user.User.has_two_factor`, so a session signed in before
the rule existed is held at its next request like a fresh sign-in, and a user
whose two-factor is cleared (``scripts/reset_mfa.py``, or the owner's Reset
for a companion, each of which ends their sessions too) meets the hold at
their next sign-in.  An unmatched URL is held too, as an anonymous one is
bounced: the hold has no exception the route table does not name.
"""

from flask import Response, request, url_for
from flask_login import current_user
from flask_login.config import EXEMPT_METHODS

from app.extensions import login_manager
from app.utils.auth_helpers import full_page_redirect

#: Every endpoint an anonymous visitor may reach, by ENDPOINT NAME -- the
#: whole of the policy.  Keyed by endpoint rather than by path for the reason
#: the baseline sweep excludes Flask's static route by endpoint: a path prefix
#: also swallows every future path that happens to start the same way, and
#: a name either resolves in the route table or it does not.
#:
#: The seven views are the front door and what it needs: the login and
#: registration forms with their POSTs, the second factor's verify page (its
#: visitor has a password accepted and no session yet, which is the one state
#: between anonymous and authenticated this application has), the container
#: health probe, and the two PWA files a browser fetches before any session
#: exists.  ``static`` is Flask's own: the login page's CSS and JS.
#:
#: **Adding a name here is the only way to open a route**, and
#: ``tests/test_routes/test_auth_required.py`` pins this set's exact
#: contents, so an addition is a visible diff in two files and never a quiet
#: one -- and asserts every name resolves, so a renamed view cannot leave a
#: stale entry exempting nothing.
PUBLIC_ENDPOINTS = frozenset({
    "auth.login",
    "auth.register_form",
    "auth.register",
    "auth.mfa_verify",
    "health.health_check",
    "static",
    "static_pass.web_manifest",
    "static_pass.service_worker",
})

#: What a signed-in user without two-factor may reach beside the public
#: endpoints, by ENDPOINT NAME (ruling plan#3434): the setup page, the POST
#: that confirms it, and signing out.  ``tests/test_routes/test_auth_required.py``
#: pins this set's exact contents and that every name resolves, as it does
#: :data:`PUBLIC_ENDPOINTS`.
TWO_FACTOR_SETUP_ENDPOINTS = frozenset({
    "auth.mfa_setup",
    "auth.mfa_confirm",
    "auth.logout",
})


def _hold_at_two_factor_setup() -> Response:
    """Send a user without two-factor to its setup, the request not performed.

    The one answer the hold ends a request with, so a test observes the
    hold's decision by counting calls here, as the anonymous sweep counts
    :meth:`~flask_login.LoginManager.unauthorized`.

    Returns:
        The redirect to ``/mfa/setup``, by ``HX-Redirect`` for htmx.
    """
    return full_page_redirect(url_for("auth.mfa_setup"))


def register_login_gate(app):
    """Refuse every request that is not authenticated and not declared public.

    And hold a signed-in user without two-factor at its setup (the module
    docstring says what that lets through).

    Args:
        app: The Flask application being built.  The hook reads ITS config
            for ``LOGIN_DISABLED`` rather than ``current_app``'s: they are the
            same object inside a request this app is serving, and the bound
            name needs no proxy.
    """

    @app.before_request
    def _require_login():
        """Bounce an anonymous request unless its endpoint is public; hold one without two-factor.

        Returns:
            ``None`` to let the request through, the login redirect
            :meth:`~flask_login.LoginManager.unauthorized` builds, or the
            hold's redirect to two-factor setup -- a ``before_request`` hook
            returning a response ends the request there, which is what stops
            every later hook and the view.
        """
        if request.method in EXEMPT_METHODS or app.config.get("LOGIN_DISABLED"):
            return None
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if request.endpoint in TWO_FACTOR_SETUP_ENDPOINTS:
            return None
        if not current_user.has_two_factor:
            return _hold_at_two_factor_setup()
        return None
