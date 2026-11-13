"""A trusted device, made and read the way a test of one needs.

A test that needs more trusted devices than its one real code sign-in makes
(replay prevention refuses a second code inside one thirty-second step, so a
test types one) makes the rest through the service, as a trusting sign-in
would: :func:`trust`, then :func:`move_trust_to` to date the trust where a
test needs it.  :func:`is_trusted` reads a device's trust fresh from the
database, as a later request would.  The Security page's tests, its trusted
devices' tests and the owner's view of a companion's devices
(ruling plan#3439) share these.
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


def trust(user_id, user_agent):
    """Trust a device for *user_id* through the service, committed; return its id."""
    known_device_service.record_sign_in(
        user_id, None, user_agent, now=datetime.now(timezone.utc), trust=True,
    )
    db.session.commit()
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
