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
"""

from contextlib import contextmanager

from app import CONFIG_MAP, create_app
from app.extensions import db, limiter

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
