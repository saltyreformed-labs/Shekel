"""A copy of a browser's session cookie, and the probes the sign-out tests ask of one.

Plan#2171 (rulings plan#3925, plan#3927, plan#4010): a session is live exactly
while its sign-in's record on the server is, so a copy of a session cookie,
taken as someone who copied it would take it, works until a sign-out deletes
that record.  The tests of every sign-out read the same few things, stated
once here: a copy of a browser, whether a browser is signed in, the token its
session carries, the records of a user's sign-ins and the user's trusted
devices.

A copy shares its browser's address and User-Agent, as the test client's
requests all do, so Flask-Login's strong protection passes it: what refuses
it in these tests is the server's record alone.
"""

import sqlalchemy

from app.extensions import db
from app.models.sign_in import SignIn
from app.services import known_device_service
from app.utils.session_helpers import SESSION_TOKEN_KEY
from tests._seed_owner_fixtures import OWNERS_PAGE


def copy_of(app, browser):
    """Return a new browser holding a copy of *browser*'s session cookie, and nothing else.

    Not its device cookie: a copy of the session cookie is what signs in.

    Args:
        app: The Flask application under test.
        browser: A test client holding a session cookie.

    Returns:
        A fresh test client carrying that cookie's value.
    """
    name = app.config["SESSION_COOKIE_NAME"]
    copy = app.test_client()
    copy.set_cookie(name, browser.get_cookie(name).value)
    return copy


def is_signed_in(browser, page=OWNERS_PAGE):
    """Return whether *browser*'s session still loads, by a signed-in *page*.

    Refused, the login gate sends it to ``/login``; any other answer is a
    failure of the probe rather than of the session.

    Args:
        browser: A test client.
        page: A page its user reads when signed in: the owner's dashboard by
            default, ``/companion/`` for a companion, whom the dashboard
            answers 404.

    Returns:
        True for a 200, False for the login gate's bounce.
    """
    response = browser.get(page)
    if response.status_code == 200:
        return True
    location = response.headers.get("Location", "")
    assert response.status_code == 302 and "/login" in location, (
        f"The probe answered {response.status_code} ({location!r})"
    )
    return False


def token_of(browser):
    """Return the sign-in token *browser*'s session carries."""
    with browser.session_transaction() as sess:
        return sess[SESSION_TOKEN_KEY]


def sign_ins_of(user_id):
    """Return the hashes of *user_id*'s sign-in records, read fresh."""
    db.session.expire_all()
    return set(db.session.execute(
        sqlalchemy.select(SignIn.token_hash).where(SignIn.user_id == user_id)
    ).scalars())


def trusted_device_ids(user_id):
    """Return the ids of *user_id*'s trusted devices, read fresh."""
    db.session.expire_all()
    return {device.id for device in known_device_service.list_trusted_devices(user_id)}
