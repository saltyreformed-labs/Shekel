"""Every route the application serves refuses an anonymous request, or is named.

**The sweep's subject is the route table, not a list.**  Until plan step
``bank_import:X-gi-4`` this file was a hand-typed ``PROTECTED_ENDPOINTS`` of
124 ``(method, path)`` pairs whose docstring said a new route "must be added"
to it, and finding **N-402** measured what that sentence is worth: the
application served 222 pairs, 213 of them behind ``@login_required``, and the
list had drifted by 89 with every check green -- a gate whose coverage is a
list somebody remembers to update reports clean over exactly what it cannot
see.  The remedy the developer chose (ruling **bank_import:R-BI4**) removed
the thing being counted: ``app/login_gate.py`` refuses every request that is
not authenticated and not declared public, in ONE ``before_request`` hook,
and no view carries a decorator any more.  So this file grades the gate,
and its cases are ENUMERATED from ``url_map`` the way
``test_no_baseline_policy`` enumerates its own: a route added a year from now
is a case here without its author knowing this file exists.

What is graded, and how each arm is kept honest:

* **every ``(method, rule)`` outside :data:`~app.login_gate.PUBLIC_ENDPOINTS`
  bounces an anonymous request to the login page**, with ``next`` carrying
  the path.  One test per pair, so a failure names the route.
* **every public pair is NOT decided by the gate** -- asserted on the gate's
  DECISION rather than on the response shape, because ``/mfa/verify`` with no
  pending second factor redirects to the login page on its own, and a
  "not a login redirect" arm would read the view's redirect as the gate's.
  The decision is observed by counting calls to the one function the gate
  can end a request with, :meth:`~flask_login.LoginManager.unauthorized`.
  The allowlist is therefore a CLAIM with its own firing arm, not a skip.
* **the allowlist's exact contents are pinned**, so opening a route is a
  visible diff in two files; and every name in it resolves in the route
  table, so a renamed view cannot leave a stale entry exempting nothing.
* **the table the cases were derived from is the table the fixture app
  serves**, pair for pair, so the enumeration cannot silently shrink.
* **an unmatched URL is bounced too** (the one place the gate is stricter
  than the decorator it replaced, chosen by the developer 2026-09-11), and an
  authenticated user still gets Flask's 404 or 405 there.
* **the two exemptions the decorator had are the gate's** -- ``OPTIONS`` and
  ``LOGIN_DISABLED`` -- each with the arm that proves the switch is a switch.
* **the hook's place in the request** is pinned: after CSRF and the rate
  limiter, before the session-activity stamp.
* **a signed-in companion without two-factor is held at its setup** (ruling
  plan#3434): the hold's own set is pinned and resolves, every served pair
  outside it and the front door sends her to setup by the hold's decision,
  and none inside them is decided by the hold.  Its behaviour on a page, an
  htmx request and a POST is ``tests/test_routes/test_two_factor_required.py``.
"""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import request

from app import create_app
from app.config import TestConfig
from app.extensions import db as _db, login_manager
from app.rate_limits import limiter
from app import login_gate
from app.login_gate import PUBLIC_ENDPOINTS, TWO_FACTOR_SETUP_ENDPOINTS
from tests._two_factor_sign_in import sign_in_seed_companion_by_password
from tests.test_integration.test_rate_limiter import _disable_limiter
from tests.test_routes.test_no_baseline_policy import _route_table_app

#: The seven views an anonymous visitor may reach, plus Flask's own static
#: route -- the front door and what it needs.  **A second spelling on
#: purpose**: ``PUBLIC_ENDPOINTS`` is the policy the gate enforces, and this is
#: the pin that makes adding a name to it a diff in two files rather than a
#: quiet one.  A test that read the policy back from the module would grade
#: nothing.
_THE_FRONT_DOOR = frozenset({
    "auth.login",
    "auth.register_form",
    "auth.register",
    "auth.mfa_verify",
    "health.health_check",
    "static",
    "static_pass.web_manifest",
    "static_pass.service_worker",
})

#: What a signed-in user who owes two-factor reaches beside the front door
#: (ruling plan#3434): setup, its confirmation and signing out.  A second
#: spelling of ``TWO_FACTOR_SETUP_ENDPOINTS`` on purpose, as
#: :data:`_THE_FRONT_DOOR` is of the public set.
_THE_SETUP_DOOR = frozenset({
    "auth.mfa_setup",
    "auth.mfa_confirm",
    "auth.logout",
})

#: What each converter kind is filled with to make a concrete URL.  The gate
#: decides before any view runs, so the id need not exist -- but it must ROUTE,
#: and ``RowIdConverter`` refuses ``0``.  A converter kind absent here is a
#: collection ERROR rather than a skipped rule (:func:`_concrete`).
_CONVERTER_FILL = {"int": "99999", "path": "x"}

_CONVERTER = re.compile(r"<(?:(?P<kind>[^:<>]+):)?(?P<name>[^<>]+)>")

#: The request methods a rule can be asked with, less the two Werkzeug adds
#: to every rule on its own (``HEAD`` and ``OPTIONS``).  ``OPTIONS`` has its
#: own case: it is exempt, and the exemption is graded rather than swept.
_IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})


def _concrete(rule):
    """Return a URL that routes to *rule*, every converter filled.

    Args:
        rule: A :class:`werkzeug.routing.Rule`.

    Returns:
        The path with each ``<kind:name>`` replaced from
        :data:`_CONVERTER_FILL`.

    Raises:
        KeyError: For a converter kind this file does not know how to fill.
            Raised rather than skipped, because a rule that cannot be requested
            is a rule this sweep is not grading, and the previous version of
            this file was silent about 89 of them.
    """
    def fill(match):
        kind = match.group("kind") or "int"
        if kind not in _CONVERTER_FILL:
            raise KeyError(
                f"{rule.rule} carries a <{kind}:> converter this sweep cannot "
                f"fill; add it to _CONVERTER_FILL rather than skipping the rule"
            )
        return _CONVERTER_FILL[kind]
    return _CONVERTER.sub(fill, rule.rule)


def _every_pair(app):
    """Every ``(method, path, endpoint)`` the application serves, in rule order.

    Args:
        app: Any application whose ``url_map`` is the one to grade.

    Returns:
        One triple per explicit method per rule, the path already concrete.
    """
    with app.app_context():
        rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    return [
        (method, _concrete(rule), rule.endpoint)
        for rule in rules
        for method in sorted(rule.methods - _IMPLICIT_METHODS)
    ]


_PAIRS = _every_pair(_route_table_app())
_PROTECTED = [
    (m, p, e) for m, p, e in _PAIRS if e not in PUBLIC_ENDPOINTS
]
_PUBLIC = [(m, p, e) for m, p, e in _PAIRS if e in PUBLIC_ENDPOINTS]


def _request(client, method, path):
    """Send *method* to *path* as the form would, with an empty body.

    Args:
        client: The anonymous test client.
        method: ``GET``, ``POST``, ``PUT``, ``PATCH`` or ``DELETE``.
        path: The concrete URL.

    Returns:
        The response.
    """
    dispatch = {
        "GET": client.get,
        "POST": lambda p: client.post(p, data={}),
        "PUT": lambda p: client.put(p, data={}),
        "PATCH": lambda p: client.patch(p, data={}),
        "DELETE": client.delete,
    }
    return dispatch[method](path)


@pytest.fixture()
def gate_decisions(monkeypatch):
    """Count how often the gate ends a request, without changing what it does.

    :meth:`~flask_login.LoginManager.unauthorized` is the ONE function the
    gate returns to end a request, and nothing else in ``app/`` calls it
    (censused 2026-09-11: ``login_required`` is gone, and the views redirect
    with ``url_for`` of their own).  Wrapping it observes the gate's DECISION
    directly, which is what lets the public arm below assert "the gate let
    this through" on a view that then redirects to the login page itself.

    **It records the ENDPOINT beside the path**, because under the
    unmatched-URL rule a refused route and a URL that matched nothing are
    bounced identically: a concrete URL :func:`_concrete` filled that failed
    to route would satisfy the redirect assertion and a path-only record
    alike, reporting green for a route the sweep never reached.  The
    endpoint is ``None`` exactly when nothing matched, so the protected arm
    asserts the pair and the unmatched arms assert the ``None``.

    Yields:
        A list the wrapper appends ``(path, endpoint)`` to on every refusal.
    """
    refused = []
    # Patched on the CLASS (finding BAL-569): monkeypatch on the
    # process-lived ``login_manager`` INSTANCE restores by writing the bound
    # method onto it, shadowing any later class-level patch of
    # ``unauthorized`` (``test_commit_helpers._spy_rollback`` says how).
    original = type(login_manager).unauthorized

    def counting(manager):
        """Record the refusal, then answer exactly as the original does."""
        refused.append((request.path, request.endpoint))
        return original(manager)

    monkeypatch.setattr(type(login_manager), "unauthorized", counting)
    yield refused


def spy_on_the_hold(monkeypatch):
    """Record each request the two-factor hold ends, without changing its answer.

    :func:`app.login_gate._hold_at_two_factor_setup` is the one answer the
    hold ends a request with, so wrapping it observes the hold's DECISION, as
    :func:`gate_decisions` observes the anonymous gate's: a view that
    redirects to setup on its own (``/mfa/confirm`` does) is never read as
    the hold's.

    Args:
        monkeypatch: The test's monkeypatch, which restores the gate.

    Returns:
        A list the wrapper appends ``(method, path, endpoint)`` to on every
        hold.
    """
    # Pylint: ``protected-access`` -- the hold's one answer is private to the
    # gate, and spying on it is how a test observes the gate's decision.
    # pylint: disable=protected-access
    original = login_gate._hold_at_two_factor_setup
    held = []

    def counting():
        """Record the hold, then answer exactly as the original does."""
        held.append((request.method, request.path, request.endpoint))
        return original()

    monkeypatch.setattr(login_gate, "_hold_at_two_factor_setup", counting)
    return held


def _assert_bounced_to_login(response, path):
    """The gate's answer: a redirect to the login page naming *path* as next.

    Args:
        response: What the anonymous request got back.
        path: The path that was requested.
    """
    assert response.status_code in (302, 303), (
        f"{path} answered {response.status_code} to an anonymous request; "
        f"expected the login redirect"
    )
    location = urlsplit(response.headers.get("Location", ""))
    assert location.path == "/login", (
        f"{path} redirected an anonymous request to {location.geturl()}, "
        f"expected /login"
    )
    assert parse_qs(location.query).get("next") == [path], (
        f"{path}'s login redirect carries next={location.query!r}; the "
        f"login flow sends the visitor back there afterwards, so it must "
        f"name the path they asked for"
    )


class TestEveryRouteIsGated:
    """The sweep proper: one case per served pair, protected or public."""

    @pytest.mark.parametrize(
        "method,path,endpoint", _PROTECTED,
        ids=[f"{m}-{p}" for m, p, _ in _PROTECTED],
    )
    def test_an_anonymous_request_is_bounced_to_login(
        self, client, gate_decisions, method, path, endpoint,
    ):
        """Every pair not declared public redirects anonymous callers.

        Both halves are asserted: the observable answer (the redirect, with
        ``next``), and that it was the GATE that answered -- a view that
        redirected to the login page on its own would satisfy the first and
        not the second.
        """
        response = _request(client, method, path)

        _assert_bounced_to_login(response, path)
        assert gate_decisions == [(path, endpoint)], (
            f"{endpoint} was not refused by the gate itself at {path}; a "
            f"None endpoint means the filled URL matched no route at all"
        )

    @pytest.mark.parametrize(
        "method,path,endpoint", _PUBLIC,
        ids=[f"{m}-{p}" for m, p, _ in _PUBLIC],
    )
    def test_a_public_endpoint_is_not_decided_by_the_gate(
        self, client, gate_decisions, method, path, endpoint,
    ):
        """Every declared public pair reaches its view anonymously.

        The allowlist's own firing arm: drop a name from
        ``PUBLIC_ENDPOINTS`` and this case fails for it, so the list is a
        claim graded in both directions rather than a skip.  What the view
        then answers is its own business -- ``/mfa/verify`` redirects to the
        login page when no second factor is pending, ``/static/x`` is a 404
        -- and is not asserted here.
        """
        response = _request(client, method, path)

        assert gate_decisions == [], (
            f"{endpoint} is declared public and the gate refused it anyway"
        )
        # A floor, not a shape: the view may redirect or 404, but a public
        # door that crashes for an anonymous visitor is not "reached".
        assert response.status_code < 500, (
            f"{endpoint} answered {response.status_code} anonymously"
        )


class TestTheAllowlistIsExactlyTheFrontDoor:
    """The policy's contents and its referential integrity."""

    def test_the_public_set_is_pinned(self):
        """Adding a name to ``PUBLIC_ENDPOINTS`` is a two-file diff.

        The sweep above would happily assert a newly-listed ``grid.index``
        answers anonymously; this is the only arm that refuses the listing.
        """
        assert PUBLIC_ENDPOINTS == _THE_FRONT_DOOR

    def test_every_public_name_resolves_in_the_route_table(self, app):
        """A stale name exempts nothing, and is refused rather than ignored.

        Renaming ``auth.mfa_verify`` would gate the second factor's page
        (fail-closed) while the list still read as complete; this arm makes
        the stale entry itself the failure.
        """
        served = {rule.endpoint for rule in app.url_map.iter_rules()}
        assert PUBLIC_ENDPOINTS <= served, (
            f"declared public but served by no route: "
            f"{sorted(PUBLIC_ENDPOINTS - served)}"
        )


class TestTheEnumerationIsTheServedTable:
    """The cases were derived from one table; the fixture app serves another."""

    def test_the_swept_pairs_are_the_fixture_apps_pairs(self, app):
        """Pair for pair, so the sweep cannot shrink without this failing.

        ``_route_table_app`` registers the blueprints and nothing else;
        ``create_app`` could add a rule of its own or register a blueprint
        the table app does not.  Asserted as set equality in both directions.
        """
        assert set(_every_pair(app)) == set(_PAIRS)

    def test_the_sweep_is_not_empty_in_either_arm(self):
        """Both parametrized lists hold cases -- a vacuous sweep is green.

        The public arm's floor is the pinned set's size, because every
        public endpoint serves at least one pair; an exact pair count would
        be a third spelling of the policy, rotting on a method change.
        """
        assert len(_PROTECTED) > 200, len(_PROTECTED)
        assert len(_PUBLIC) >= len(_THE_FRONT_DOOR), [p for _, p, _ in _PUBLIC]


class TestAnUnmatchedUrlIsBouncedToo:
    """The one deliberate difference from the decorator this gate replaced."""

    def test_an_anonymous_request_for_no_route_is_bounced(
        self, client, gate_decisions,
    ):
        """A typo path answers the login redirect, not Flask's 404."""
        response = client.get("/this-page-does-not-exist")

        _assert_bounced_to_login(response, "/this-page-does-not-exist")
        assert gate_decisions == [("/this-page-does-not-exist", None)]

    def test_an_anonymous_GET_of_a_POST_only_door_is_bounced(
        self, client, gate_decisions,
    ):
        """A method Werkzeug would refuse with 405 is refused by the gate first."""
        response = client.get("/logout")

        _assert_bounced_to_login(response, "/logout")
        assert gate_decisions == [("/logout", None)]

    def test_an_authenticated_user_still_gets_404_and_405(
        self, auth_client, gate_decisions,
    ):
        """The strictness is for anonymous callers; a session sees Flask's own."""
        assert auth_client.get("/this-page-does-not-exist").status_code == 404
        assert auth_client.get("/logout").status_code == 405
        assert gate_decisions == []


class TestTheDecoratorsExemptionsAreTheGates:
    """``login_required`` exempted two things; the gate exempts the same two."""

    def test_HEAD_is_not_exempt(self, client, gate_decisions):
        """``HEAD`` is bounced like ``GET``, as it was by the decorator.

        Werkzeug adds ``HEAD`` to every ``GET`` rule, so the sweep above
        leaves it out of its enumeration; this is the one case that asks
        with it, because ``EXEMPT_METHODS`` names ``OPTIONS`` alone and an
        exemption that quietly grew would open every page's headers.
        """
        response = client.head("/grid")

        assert response.status_code in (302, 303)
        assert urlsplit(response.headers["Location"]).path == "/login"
        assert gate_decisions == [("/grid", "grid.index")]

    def test_OPTIONS_is_exempt(self, client, gate_decisions):
        """A preflight is answered by Flask's automatic OPTIONS, not bounced.

        ``flask_login.config.EXEMPT_METHODS`` is read by the gate rather than
        re-spelled, so this case grades that the read is there at all.
        """
        response = client.options("/grid")

        assert response.status_code == 200
        assert "GET" in response.headers.get("Allow", "")
        assert gate_decisions == []

    def test_an_undeclared_route_on_a_fresh_app_is_gated(self):
        """A route registered by nobody's blueprint is gated all the same.

        The whole point of a gate over a decorator: this ad-hoc view has no
        decorator and is in no list, and an anonymous request is bounced.
        Paired with the ``LOGIN_DISABLED`` case below on an identical app, so
        the two differ in the switch alone.
        """
        probe_app = create_app("testing")

        @probe_app.route("/probe")
        def probe():
            """A view with no gate of its own."""
            return "reached"

        try:
            response = probe_app.test_client().get("/probe")
            assert response.status_code in (302, 303)
            assert urlsplit(response.headers["Location"]).path == "/login"
        finally:
            with probe_app.app_context():
                _db.engine.dispose()

    def test_LOGIN_DISABLED_switches_the_gate_off(self):
        """Flask-Login's own switch turns the gate off, as it did the decorator.

        No shipped configuration sets it; a throwaway app whose subject is an
        error page or a rate ceiling does, which is why the parity matters.
        """
        probe_app = create_app("testing")
        probe_app.config["LOGIN_DISABLED"] = True

        @probe_app.route("/probe")
        def probe():
            """A view with no gate of its own."""
            return "reached"

        try:
            response = probe_app.test_client().get("/probe")
            assert response.status_code == 200
            assert response.data == b"reached"
        finally:
            with probe_app.app_context():
                _db.engine.dispose()


class TestWhereTheGateSitsInTheRequest:
    """After CSRF and the limiter, before the activity stamp -- pinned."""

    def test_the_hook_order_is_the_decorators_order(self, monkeypatch):
        """The request runs through the same checks in the same order.

        When the check lived on the view it ran after every app-level hook:
        the request id, CSRF, the rate limiter, the activity stamp.  What
        carries behaviour is the gate following CSRF and the limiter --
        registered ahead of them it would answer an anonymous POST before
        CSRF could and let an anonymous flood skip the rate ceiling.  Its
        place before the stamp is behaviourally immaterial (the stamp writes
        only for an authenticated session) and is pinned so the order is one
        thing rather than two.

        **The owner's write lock sits between the limiter and the gate**
        (plan step ``balance:X-bn``, ruling **R-CC122**: after the form-token
        check and the app-wide rate limit, so a request either one refuses never
        waits for the lock or holds it).  Its place before the gate is not
        immaterial, and the developer approved this list knowing it (rule 5,
        2026-09-25): taking the lock expires the signed-in user's row, so the
        gate's ``is_authenticated`` -- Flask-Login's ``is_active`` -- is read
        again AFTER the wait, and a companion deactivated while their save
        waited is turned away rather than saving.

        **Built with the limiter ENABLED at construction**, because Flask-
        Limiter registers its hook only then and ``TestConfig`` switches it
        off -- so the fixture app carries no limiter hook to order against,
        and a test over it would pin all but the limiter's position while
        reading as if it pinned every one.  Enabling it on the config class
        before ``create_app`` is what production does; enabling it afterwards
        with a second ``init_app`` (the rate-limiter tests' pattern) appends
        the hook AFTER the gate and would pin the wrong order.  Torn down the
        way those tests tear down, so the module-level limiter is off again
        for the next test in this worker.
        """
        monkeypatch.setattr(TestConfig, "RATELIMIT_ENABLED", True)
        ordered_app = create_app("testing")
        try:
            names = [
                hook.__name__ for hook in ordered_app.before_request_funcs[None]
            ]
            assert names == [
                "_attach_request_id",
                "csrf_protect",
                "_check_request_limit",
                "_take_the_request_owner_s_lock",
                "_require_login",
                "_refresh_last_activity",
            ], names
        finally:
            _disable_limiter(limiter, ordered_app)


class TestTheTwoFactorHold:
    """A signed-in companion without two-factor reaches setup and sign-out only."""

    def test_the_setup_set_is_pinned(self):
        """Adding a name to ``TWO_FACTOR_SETUP_ENDPOINTS`` is a two-file diff."""
        assert TWO_FACTOR_SETUP_ENDPOINTS == _THE_SETUP_DOOR

    def test_every_setup_name_resolves_in_the_route_table(self, app):
        """A stale name lets nothing through, and is refused rather than ignored."""
        served = {rule.endpoint for rule in app.url_map.iter_rules()}
        assert TWO_FACTOR_SETUP_ENDPOINTS <= served, (
            f"named for the hold but served by no route: "
            f"{sorted(TWO_FACTOR_SETUP_ENDPOINTS - served)}"
        )

    @pytest.mark.usefixtures("seed_companion")
    def test_every_other_pair_sends_her_to_setup(
        self, app, monkeypatch,
    ):
        """Every served pair outside both sets is held, by the hold's decision.

        One case over the whole table, rather than one per pair, because each
        case would need a companion signed in, and the anonymous sweep above
        already names each route.  The pairs the hold missed are listed whole.
        """
        held = spy_on_the_hold(monkeypatch)
        with app.app_context():
            browser = sign_in_seed_companion_by_password(app)
            swept, missed = 0, []
            for method, path, endpoint in _PROTECTED:
                if endpoint in TWO_FACTOR_SETUP_ENDPOINTS:
                    continue
                held.clear()
                response = _request(browser, method, path)
                swept += 1
                location = urlsplit(response.headers.get("Location", "")).path
                if held != [(method, path, endpoint)] or location != "/mfa/setup":
                    missed.append((method, path, response.status_code, location))

        assert not missed, missed
        assert swept > 200, swept

    @pytest.mark.usefixtures("seed_companion")
    def test_the_setup_door_and_the_front_door_are_not_held(
        self, app, monkeypatch,
    ):
        """No pair of either set is decided by the hold, sign-out last.

        Sign-out ends her session, so it runs after every other; what each
        view then answers is its own business, above a crash.
        """
        held = spy_on_the_hold(monkeypatch)
        let_through = sorted(
            (pair for pair in _PAIRS
             if pair[2] in TWO_FACTOR_SETUP_ENDPOINTS | PUBLIC_ENDPOINTS),
            key=lambda pair: pair[2] == "auth.logout",
        )
        assert let_through[-1][2] == "auth.logout", let_through
        with app.app_context():
            browser = sign_in_seed_companion_by_password(app)
            for method, path, endpoint in let_through:
                response = _request(browser, method, path)
                assert response.status_code < 500, (endpoint, response.status_code)

        assert not held, held
