"""A trusted device, made and read the way a test of one needs.

A test that needs more trusted devices than its one real code sign-in makes
(replay prevention refuses a second code inside one thirty-second step, so a
test types one) makes the rest through the service, as a trusting sign-in
would: :func:`trust`, then :func:`move_trust_to` to date the trust where a
test needs it.  :func:`is_trusted` reads a device's trust fresh from the
database, as a later request would.  The Security page's tests, its trusted
devices' tests and the owner's view of a companion's devices
(ruling plan#3439) share these.

:func:`trust_browser` makes a test client one an account trusts, as that
sign-in would leave it: the record and its cookie.  Every shared test sign-in
starts from one (ruling plan#3621), so it types no code and leaves the test
every code of the account's thirty-second step.
"""

from datetime import datetime, timezone

import sqlalchemy

from app.extensions import db
from app.models.known_device import KnownDevice
from app.services import known_device_service

#: Three browsers' User-Agent headers, each named as the device list names it
#: (``app.utils.browser_label``): "Firefox on Linux", "Safari on iPhone" and
#: "Chrome on Windows".
FIREFOX_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 "
    "Safari/604.1"
)
CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def device_cookie_name(user_id):
    """Return the name of *user_id*'s device cookie as the test app writes it.

    The test configuration's session cookie is not Secure, so the name takes no
    ``__Host-`` prefix (``app.utils.device_cookie``).
    """
    return f"shekel_device_{user_id}"


def _record_a_trust(user_id, user_agent):
    """Record a trusting sign-in for *user_id* through the service, committed.

    Returns:
        The new record's token, as its cookie would carry it.
    """
    token = known_device_service.record_sign_in(
        user_id, None, user_agent, now=datetime.now(timezone.utc), trust=True,
    )
    db.session.commit()
    return token


def trust_browser(browser, user_id):
    """Make *browser* one *user_id* trusts, as a code sign-in asking to be trusted leaves it.

    Records the trust through the service and gives *browser* the record's
    cookie.  The record names no browser until a sign-in on it does, which
    writes its User-Agent.

    Args:
        browser: The test client.
        user_id: The ``auth.users.id`` that trusts it.
    """
    browser.set_cookie(device_cookie_name(user_id), _record_a_trust(user_id, ""))


def trust(user_id, user_agent):
    """Trust a device for *user_id* through the service, committed; return its id."""
    _record_a_trust(user_id, user_agent)
    return db.session.execute(
        sqlalchemy.select(sqlalchemy.func.max(KnownDevice.id))
        .where(KnownDevice.user_id == user_id)
    ).scalar_one()


def is_trusted(device_id):
    """Return whether *device_id*'s row holds a trust, read fresh."""
    return db.session.execute(
        sqlalchemy.select(KnownDevice.trusted_at)
        .where(KnownDevice.id == device_id)
    ).scalar_one() is not None


def move_trust_to(device_id, instant):
    """Set *device_id*'s ``trusted_at`` to *instant*, committed."""
    db.session.execute(
        sqlalchemy.update(KnownDevice)
        .where(KnownDevice.id == device_id)
        .values(trusted_at=instant)
    )
    db.session.commit()
