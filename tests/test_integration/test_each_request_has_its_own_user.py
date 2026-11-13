"""
Shekel Budget App -- Every Test Request Reads Its Own Signed-In User

The guard for ledger row ``balance:BAL-521`` (plan step ``balance:X-cr``):
**a test client never sees another client's identity.**

The defect it guards.  The ``db`` fixture runs every test inside ONE
application context, and Flask 3.1's ``RequestContext.push`` REUSES an
already-pushed application context of the same app instead of pushing a
fresh one.  ``flask.g`` lives on the application context, so before the fix
every request a test issued shared one ``g`` -- with each other and with the
test body.  Flask-Login caches the request's user in ``g._login_user`` the
first time ``current_user`` is read -- ``login_user`` and ``logout_user``
overwrite it -- and never re-reads the cookie while that attribute exists, so
once any request had cached a user, every later request was answered as that
user, whichever client sent it.  ``second_auth_client``'s own login POST was
answered "already signed in" and never signed the second user in at all.
Production never had this: each request there gets an application context,
and so a ``g``, of its own.

The fix is in the harness, not the application:
``tests/_per_request_globals.py`` gives every test-client request a ``g`` of
its own for exactly the length of the request, and puts the test's own ``g``
back afterwards.  ``tests/conftest.py`` installs it on the ``Flask`` class, so
an app a test builds for itself gets it too.

How these cases read identity.  ``base.html`` renders the signed-in user's
full display name in the user menu's one ``dropdown-header``, from the
``current_user`` that request resolved -- so the header IS whom the request
ran as.  A page that names nobody is an anonymous answer.
"""

import re

import pytest
from flask import Flask, g, redirect
from flask_login import login_user

from tests._other_user_fixtures import (
    SECOND_USER_EMAIL,
    SECOND_USER_PASSWORD,
    SEED_SECOND_USER_EMAIL,
    SEED_SECOND_USER_PASSWORD,
)
from tests._seed_owner_fixtures import OWNERS_PAGE
from tests._two_factor_sign_in import sign_in_from_a_trusted_browser

#: The user menu's header, which renders the request's ``current_user``.
_SIGNED_IN_AS = re.compile(r'<h6 class="dropdown-header">([^<]*)</h6>')

#: A form's CSRF token as ``base.html``'s logout form renders it.
_CSRF_TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')

#: A page every signed-in owner can open and an anonymous request is turned
#: away from by the login gate.
_PAGE = "/settings"


def _signed_in_as(response):
    """Return the display name *response*'s page was rendered for, or None.

    Args:
        response: A test-client response.

    Returns:
        The one name in the user menu's header, or ``None`` when the page
        names nobody (an anonymous answer, or a redirect).
    """
    names = _SIGNED_IN_AS.findall(response.get_data(as_text=True))
    assert len(names) <= 1, f"a page named more than one user: {names}"
    return names[0] if names else None


def _assert_runs_as(client, display_name):
    """Assert that a request from *client* is answered as *display_name*.

    Args:
        client: The test client to request from.
        display_name: The display name the request must run as.
    """
    response = client.get(_PAGE)
    assert response.status_code == 200, (
        f"{_PAGE} answered {response.status_code} for a client that should "
        f"be signed in as {display_name!r}"
    )
    seen = _signed_in_as(response)
    assert seen == display_name, (
        f"a client signed in as {display_name!r} was answered as {seen!r}"
    )


def _assert_runs_anonymously(client):
    """Assert that a request from *client* is answered as nobody.

    Args:
        client: The test client to request from.
    """
    response = client.get(_PAGE)
    assert response.status_code == 302, (
        f"{_PAGE} answered {response.status_code} for a client that is "
        f"signed in as nobody; the page named {_signed_in_as(response)!r}"
    )
    assert "/login" in response.headers["Location"]


def _log_in(client, email, password):
    """Sign *client* in through the shared sign-in, on a browser the account trusts.

    Its redirect to the owner's page alone proves nothing -- an
    already-signed-in request is answered with the same redirect -- so every
    caller asserts the identity next.

    Args:
        client: The test client to sign in.
        email: The account's email.
        password: The account's password.
    """
    sign_in_from_a_trusted_browser(client, email, password, OWNERS_PAGE)


@pytest.fixture(name="signed_in_clients")
def _signed_in_clients(auth_client, second_auth_client, seed_user, seed_second_user):
    """The two signed-in clients, keyed ``"A"`` and ``"B"``, with their users.

    Returns:
        ``{"A": (auth_client, name), "B": (second_auth_client, name)}`` --
        each client beside the display name its requests must run as, read
        off the seeding fixture's own user row rather than retyped here.
    """
    return {
        "A": (auth_client, seed_user["user"].display_name),
        "B": (second_auth_client, seed_second_user["user"].display_name),
    }


#: The request orders the two-client cases run, as ``signed_in_clients`` keys.
_ORDERS = [
    pytest.param(("A", "B"), id="first-then-second"),
    pytest.param(("B", "A"), id="second-then-first"),
    pytest.param(("A", "B", "A"), id="interleaved"),
]


class TestTwoSignedInClients:
    """Two clients signed in as two users each run as their own user."""

    @pytest.mark.parametrize("order", _ORDERS)
    def test_each_client_runs_as_its_own_user(self, order, signed_in_clients):
        """Under the test's own application context, in every order."""
        for key in order:
            _assert_runs_as(*signed_in_clients[key])

    @pytest.mark.parametrize("order", _ORDERS)
    def test_each_client_runs_as_its_own_user_in_a_nested_context(
        self, order, app, signed_in_clients,
    ):
        """Inside an explicit ``with app.app_context():``, in every order.

        Many route tests wrap their requests in one; the nested context is the
        one every request inside it would otherwise share.
        """
        with app.app_context():
            for key in order:
                _assert_runs_as(*signed_in_clients[key])


class TestClientsMadeAndSignedInMidTest:
    """Clients made, signed out and signed in during the test body."""

    def test_a_client_made_mid_test_runs_as_its_own_user(
        self, app, signed_in_clients, second_user,
    ):
        """A third client signed in after two others have requested."""
        _assert_runs_as(*signed_in_clients["A"])
        _assert_runs_as(*signed_in_clients["B"])

        third_client = app.test_client()
        _log_in(third_client, SECOND_USER_EMAIL, SECOND_USER_PASSWORD)
        _assert_runs_as(third_client, second_user["user"].display_name)

        _assert_runs_as(*signed_in_clients["A"])
        _assert_runs_as(*signed_in_clients["B"])

    def test_one_client_signs_out_then_in_as_another_user(
        self, app, auth_client, seed_user, seed_second_user,
    ):
        """One client switches users; a client that never signed in is nobody.

        The last request is the one the shared cache answered as the
        switched-to user: a client that never signed in at all.
        """
        _assert_runs_as(auth_client, seed_user["user"].display_name)

        assert auth_client.post("/logout").status_code == 302
        _assert_runs_anonymously(auth_client)

        _log_in(auth_client, SEED_SECOND_USER_EMAIL, SEED_SECOND_USER_PASSWORD)
        _assert_runs_as(auth_client, seed_second_user["user"].display_name)

        _assert_runs_anonymously(app.test_client())

    def test_a_companion_runs_as_the_companion(
        self, auth_client, companion_client, seed_user, seed_companion,
    ):
        """A companion signed in beside its owner is not answered as the owner."""
        _assert_runs_as(auth_client, seed_user["user"].display_name)

        response = companion_client.get("/companion/")
        assert response.status_code == 200, (
            f"/companion/ answered {response.status_code} for the companion"
        )
        seen = _signed_in_as(response)
        assert seen == seed_companion["user"].display_name, (
            f"the companion's request was answered as {seen!r}"
        )


class TestNothingARequestCachesOutlivesIt:
    """A request's ``g`` dies with it, as it does in production."""

    def test_a_request_leaves_the_test_s_g_as_it_found_it(self, auth_client):
        """The test body's ``g`` holds nothing a request put there.

        Every value a request caches on ``g`` -- the signed-in user, the CSRF
        token, the request id and start time -- would otherwise follow the
        request out into the test body and on into every later request.
        """
        before = dict(vars(g))
        response = auth_client.get(_PAGE)
        assert response.status_code == 200
        assert dict(vars(g)) == before

    def test_an_app_a_test_builds_for_itself_gets_the_same_guarantee(self):
        """An app built in the test body, requested inside its own context.

        A test that calls ``create_app("testing")`` and requests inside
        ``with that_app.app_context():`` shares one ``g`` across those
        requests exactly as the suite's app did, which is why the client is
        installed on the ``Flask`` class rather than on the session's app.  A
        bare ``Flask`` app asks the same question, of a redirect hop as well:
        each hop is a request of its own.
        """
        built = Flask(__name__)

        @built.route("/mark")
        def mark():
            """Mark ``g`` and answer what an earlier request left there."""
            left_behind = getattr(g, "marked", None)
            g.marked = "by an earlier request"
            return str(left_behind)

        @built.route("/hop")
        def hop():
            """Mark ``g``, then send the client on to a request that reads it."""
            g.marked = "by the hop before"
            return redirect("/read")

        @built.route("/read")
        def read():
            """Answer what an earlier request left on ``g``."""
            return str(getattr(g, "marked", None))

        with built.app_context():
            client = built.test_client()
            assert client.get("/mark").get_data(as_text=True) == "None"
            assert client.get("/mark").get_data(as_text=True) == "None"
            followed = client.get("/hop", follow_redirects=True)
            assert followed.request.path == "/read"
            assert followed.get_data(as_text=True) == "None"

    def test_a_request_s_csrf_token_is_its_own(
        self, app, monkeypatch, auth_client, second_auth_client,
    ):
        """A second client's page carries a form token its own session accepts.

        Flask-WTF caches the rendered token on ``g``.  Shared, the second
        client's page would carry the FIRST client's token, signed over a
        session the second client does not hold, and the second client could
        not submit its own page's form -- a ``g`` value other than the user
        reaching across clients.
        """
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

        first_page = auth_client.get(_PAGE)
        second_page = second_auth_client.get(_PAGE)
        first_token = _CSRF_TOKEN.search(first_page.get_data(as_text=True))
        second_token = _CSRF_TOKEN.search(second_page.get_data(as_text=True))
        assert first_token is not None and second_token is not None
        assert second_token.group(1) != first_token.group(1)

        response = second_auth_client.post(
            "/logout", data={"csrf_token": second_token.group(1)},
        )
        assert response.status_code == 302, (
            f"the second client's own form token was refused with "
            f"{response.status_code}"
        )
        _assert_runs_anonymously(second_auth_client)

    def test_a_user_the_test_body_signs_in_is_not_a_request_s(
        self, app, seed_user, seed_second_user, second_auth_client,
    ):
        """A user the test body resolved for itself does not answer a request.

        The test body's ``g`` is not a request's either: a sign-in performed
        under ``app.test_request_context()`` caches that user on the test's
        own ``g``, and a request that STARTED from the test's ``g`` would be
        answered as that user whatever its cookie says.
        """
        with app.test_request_context():
            login_user(seed_user["user"])

        _assert_runs_as(
            second_auth_client, seed_second_user["user"].display_name,
        )
