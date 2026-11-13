"""
Shekel Budget App -- An App With the Rate Limiter On, for a Test About a Limit

The suite's app runs with the limiter off (``TestConfig.RATELIMIT_ENABLED``),
so a test about a limit builds an app of its own with it on from the start, as
production runs it (:func:`limited_app`), and sends each request from the
address production would see: a client's, arriving through nginx
(:func:`through_nginx`).

A fixture that uses :func:`limited_app` requests the suite's ``app`` fixture
first, though it never reads it: each ``create_app`` re-initialises the one
global ``limiter``, and the last app built decides whether it is on.

Two browsers a test about a signed-in budget needs: the owner's, signed in
(:func:`signed_in_browser`), and a copy of its session with nothing else
(:func:`stolen_session`).
"""

from contextlib import contextmanager

from app import CONFIG_MAP, create_app
from app.extensions import db
from app.rate_limits import limiter
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD

#: An address on ``TestConfig.FORWARDED_ALLOW_IPS``, as nginx is in production.
NGINX_ADDRESS = "172.32.0.4"


@contextmanager
def limited_app(monkeypatch):
    """Build an app with the limiter on from the start, and turn it off after.

    Args:
        monkeypatch: The test's ``monkeypatch``, which undoes the change to the
            config class once the test ends.

    Yields:
        The app.
    """
    # ``app.CONFIG_MAP``, the class ``create_app`` reads, whichever class a
    # test that reloads ``app.config`` has left ``app.config.TestConfig`` as.
    monkeypatch.setattr(CONFIG_MAP["testing"], "RATELIMIT_ENABLED", True)
    limited = create_app("testing")
    try:
        yield limited
    finally:
        with limited.app_context():
            db.engine.dispose()
        # Pylint: ``protected-access`` -- the limiter's ``storage`` asserts its
        # store was built, and nothing public asks whether it was.
        if limiter._storage is not None:  # pylint: disable=protected-access
            limiter.reset()
        limiter.enabled = False


def through_nginx(client_address):
    """Return request arguments for *client_address* arriving through nginx.

    Args:
        client_address: The address nginx saw.

    Returns:
        Keyword arguments for a test client's request method.
    """
    return {
        "environ_base": {"REMOTE_ADDR": NGINX_ADDRESS},
        "headers": {"X-Forwarded-For": client_address},
    }


def signed_in_browser(app, address):
    """Return a browser that finished the seed user's sign-in from *address*, still signed in.

    The account has no code to ask (its test reset its two-factor first,
    ``tests._two_factor_sign_in.reset_two_factor_of``), so the password
    finishes the sign-in, and the browser holds the cookie of the record it
    made: it is known.

    Args:
        app: The app under test.
        address: The address nginx sees, and so where the session answers.

    Returns:
        The signed-in test client.
    """
    browser = app.test_client()
    response = browser.post(
        "/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD},
        **through_nginx(address),
    )
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/login" not in location and "/mfa/verify" not in location
    return browser


def stolen_session(app, owner):
    """Return a new browser holding a copy of *owner*'s session cookie and nothing else.

    Signed in as the owner, and a stranger at every door: the browser's own
    cookie stayed with the owner.  A session answers only at the address and
    browser it signed in from (``session_protection = "strong"``), so a copy
    is used from the owner's address.

    Args:
        app: The app under test.
        owner: The signed-in browser whose session is copied.

    Returns:
        The copy's test client.
    """
    name = app.config["SESSION_COOKIE_NAME"]
    thief = app.test_client()
    thief.set_cookie(name, owner.get_cookie(name).value)
    return thief
