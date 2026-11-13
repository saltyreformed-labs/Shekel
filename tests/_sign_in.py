"""A stranger's sign-in, as a service test runs it outside a request; a known browser, its count.

``/login`` checks a stranger's password in two halves, with the account's
owner lock taken between them on the way to signing in (ruling plan#3110):
:func:`~app.services.auth_service.check_strangers_password`, then
:func:`~app.services.auth_service.confirm_strangers_password`.  The lock is the
route's (:func:`app.db_transaction.bind_sign_in_owner`), and taking it outside
a request would arm every later transaction of the test with that owner's
lock, so a service test runs the two halves in order with none; the lock's own
order is graded on the route (``tests/test_routes/test_xbn_sign_in_lock.py``).
"""

from urllib.parse import urlsplit

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from app.services import auth_service
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests._two_factor_sign_in import code_now, redirect_path, two_factor_secret_of


def stranger_signs_in(email, password) -> User:
    """Sign in with *email* and *password* from a browser the account does not know.

    Args:
        email: The email typed.  Any value, so a test can hand it what a
            broken caller would.
        password: The password typed, likewise.

    Returns:
        The account the password signs in to.

    Raises:
        app.exceptions.AuthError: Whatever either half refuses.
    """
    user = auth_service.find_sign_in_user(email)
    proof = auth_service.check_strangers_password(email, user, password)
    return auth_service.confirm_strangers_password(user, proof)


def make_known(client) -> None:
    """Make *client* a browser known to the seed user, signed out again.

    A finished sign-in sets the browser's record and cookie (ruling
    plan#2994); signing out keeps the cookie.  The seed user has two-factor
    (plan#3600), so the sign-in finishes at the code form, with the user's own
    code and without trusting the browser: it is known, and holds no trust.
    It types the current step's code, so a test that calls this types no
    other in that step.

    Args:
        client: The test client, as a browser.
    """
    response = client.post(
        "/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD},
    )
    assert redirect_path(response) == "/mfa/verify"
    user_id = db.session.query(User.id).filter_by(email=SEED_USER_EMAIL).scalar()
    response = client.post(
        "/mfa/verify", data={"totp_code": code_now(two_factor_secret_of(user_id))},
    )
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path not in ("/login", "/mfa/verify")
    client.post("/logout")


def browser_count(user_id) -> int:
    """The wrong guesses counted on *user_id*'s one known browser, read fresh.

    Args:
        user_id: The user, who has signed in on one browser.

    Returns:
        That browser record's count.
    """
    db.session.expire_all()
    return db.session.query(KnownDevice).filter_by(user_id=user_id).one().failed_login_count


def make_a_stranger(user_id) -> None:
    """Delete *user_id*'s browser records, so a browser signed in to it is a stranger.

    A signed-in browser stays known only while its record lives.  The record
    cannot age out while the session lives, since a session ends when its
    sign-in is thirty days old, the instant the record stops being known
    (ruling plan#3069); only ten wrong guesses in a row on its cookie delete
    it, this browser's or those of a browser holding a copy of the cookie,
    and then it is a stranger at every door (ruling plan#3034) and counts on
    the account.  Deleting the records here stands in for those guesses.

    Args:
        user_id: The user.
    """
    db.session.query(KnownDevice).filter_by(user_id=user_id).delete()
    db.session.commit()
