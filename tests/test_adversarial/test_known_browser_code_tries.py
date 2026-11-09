"""Shekel Budget App -- Adversarial Tests: a known browser's own code tries.

Plan#2772, ruling **plan#3023**.  ``/mfa/verify`` checks at most five codes in
fifteen minutes from one counter.  Every browser that is not known shares its
account's, so moving between addresses or browsers buys a stranger no more
guesses (plan#2170).  A browser that has finished a sign-in in the last thirty
days spends its own five instead, so someone holding the password but not the
phone, who spends the account's five, cannot refuse the owner's code there.
Beside both, every POST spends its address's five.

The counter comes from the record the browser's cookie names, verified for the
signing-in user: a made-up cookie, another user's cookie and a cookie thirty
days old are all strangers', and share the account's.

These run on an app built with the limiter on, as in production; the suite's
app has it off.  Each guess comes from an address of its own, so the address
limit never answers in a test about the counters.  A browser's own sign-in
before it was known was a stranger's, so it spent one of the account's five:
the arithmetic in each test counts it.

The account's own counter, by itself, is
``tests/test_routes/test_every_guess_counts.py``
(``TestTheCodeFormIsLimitedByThePendingAccount``).
"""

import secrets
import time
from datetime import timedelta

import pyotp
import pytest

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from app.services import known_device_service
from tests._limited_app import limited_app, through_nginx
from tests._test_helpers import enable_mfa
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_SECOND_EMAIL = "second@shekel.local"
_SECOND_PASSWORD = "secondpass12"

#: The code form's tries per counter in its window, spelled here as an oracle.
_CODE_TRIES = 5

#: Fits the form and can match no secret, for a test about the tries, not the code.
_NEVER_A_CODE = "xxxxxx"

#: The length of one authenticator step.
_TOTP_STEP_SECONDS = 30

_INVALID_CODE = b"Invalid verification code"

_TOO_MANY = b"made too many attempts"


@pytest.fixture(name="rate_app")
def _rate_app(app, monkeypatch):
    """A fresh app with the limiter on from the start, as in production.

    Yields:
        The app.
    """
    # Pylint: ``unused-argument`` -- ``app`` is asked for so the suite's app is
    # built first: the last app built decides whether the one limiter is on.
    # pylint: disable=unused-argument
    with limited_app(monkeypatch) as limited:
        yield limited


def _code(secret):
    """Return the code valid now for *secret*."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _next_code(secret):
    """Return a code for *secret* one step past now's, for a second sign-in.

    A code's step must be later than the last one accepted, so a second code
    typed within the same thirty seconds is refused as a replay.  The next
    step's code is inside the verifier's one-step drift window and later than
    any step accepted before it.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + _TOTP_STEP_SECONDS)


def _type_password(browser, address, email=SEED_USER_EMAIL, password=SEED_USER_PASSWORD):
    """Type the right password at ``/login`` from *address*, reaching the code form."""
    response = browser.post(
        "/login", data={"email": email, "password": password},
        **through_nginx(address),
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mfa/verify")


def _try_code(browser, address, code):
    """POST *code* at ``/mfa/verify`` from *address*; return the response."""
    return browser.post(
        "/mfa/verify", data={"totp_code": code}, **through_nginx(address),
    )


def _signed_in(response):
    """Return whether *response* is a completed sign-in's redirect."""
    if response.status_code != 302:
        return False
    location = response.headers["Location"]
    return "/login" not in location and "/mfa/verify" not in location


def _refused_for_tries(response):
    """Return whether *response* is the limit's refusal page, not a checked code."""
    return (
        response.status_code == 429
        and _TOO_MANY in response.data
        and _INVALID_CODE not in response.data
    )


def _known_browser(rate_app, secret, address):
    """Return a browser that finished a sign-in with its code from *address*, then signed out.

    It was not known yet, so that code spent one of the account's tries.
    """
    browser = rate_app.test_client()
    _type_password(browser, address)
    assert _signed_in(_try_code(browser, address, _code(secret)))
    browser.post("/logout", **through_nginx(address))
    return browser


def _guess(rate_app, address):
    """A guesser with the password and not the phone: a new browser, a wrong code.

    Returns:
        The code form's response.
    """
    browser = rate_app.test_client()
    _type_password(browser, address)
    return _try_code(browser, address, _NEVER_A_CODE)


def _guesses_checked(rate_app, count, prefix):
    """Have a guesser try *count* codes, each from a new address; assert each was checked.

    Args:
        rate_app: The app under test.
        count: How many guesses.
        prefix: The first three parts of each guess's address.
    """
    for octet in range(count):
        response = _guess(rate_app, f"{prefix}.{octet}")
        assert response.status_code == 200
        assert _INVALID_CODE in response.data


def _device_count(user_id, browser):
    """Return the wrong guesses counted on the record *browser*'s cookie names."""
    db.session.expire_all()
    token = browser.get_cookie(f"shekel_device_{user_id}").value
    return known_device_service.find_browser(user_id, token).device.failed_login_count


class TestAKnownBrowserHasItsOwnTries:
    """A browser signed in from before spends its own five; every other shares the account's."""

    def test_a_guesser_who_spends_the_accounts_tries_cannot_refuse_the_owner(
        self, rate_app, seed_user,
    ):
        """The account's five are gone, and the owner's browser still signs in.

        Its own sign-in before it was known spent one of the account's five, so
        four guesses spend the rest (1 + 4 = 5) and a fifth is refused.  Then
        the owner types the password and the code there, from the same browser,
        and its code is checked and signs it in.
        """
        with rate_app.app_context():
            secret = enable_mfa(db.session, seed_user["user"].id)
            owner = _known_browser(rate_app, secret, "7.7.7.7")
            _guesses_checked(rate_app, _CODE_TRIES - 1, "8.8.8")
            assert _refused_for_tries(_guess(rate_app, "8.8.9.0"))

            _type_password(owner, "7.7.7.7")
            response = _try_code(owner, "7.7.7.7", _next_code(secret))

            assert _signed_in(response)

    def test_a_known_browsers_sixth_try_is_refused(self, rate_app, seed_user):
        """Five wrong codes are checked and counted on its record; a sixth is refused.

        Each from a new address, so it is the browser's own counter that
        answers.  The refused sixth, wrong too, is never checked, so its record
        still counts five.  And none of the five was the account's: a stranger then
        still has its code checked, the account having spent only the one the
        owner's own sign-in did.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            secret = enable_mfa(db.session, user_id)
            owner = _known_browser(rate_app, secret, "7.7.7.7")
            _type_password(owner, "7.7.7.7")

            for octet in range(_CODE_TRIES):
                response = _try_code(owner, f"6.6.6.{octet}", _NEVER_A_CODE)
                assert response.status_code == 200
                assert _INVALID_CODE in response.data
            sixth = _try_code(owner, "6.6.7.0", _NEVER_A_CODE)

            assert _refused_for_tries(sixth)
            assert _device_count(user_id, owner) == _CODE_TRIES
            stranger = _guess(rate_app, "9.9.9.9")
            assert stranger.status_code == 200
            assert _INVALID_CODE in stranger.data

    def test_a_new_browser_and_a_made_up_cookie_share_the_accounts_tries(
        self, rate_app, seed_user,
    ):
        """Once guessers spend the account's five, both are refused before any check.

        A new browser is refused the right code.  A cookie the browser made up
        names no record, so it buys no counter of its own: a key built from
        what a browser sends would give five new tries for each one it made
        up.  Its wrong code is refused unchecked, so the account still counts
        the guessers' five wrong codes and no more.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            secret = enable_mfa(db.session, user_id)
            _guesses_checked(rate_app, _CODE_TRIES, "8.8.8")

            new_browser = rate_app.test_client()
            _type_password(new_browser, "9.9.9.1")
            made_up = rate_app.test_client()
            made_up.set_cookie(f"shekel_device_{user_id}", secrets.token_urlsafe(32))
            _type_password(made_up, "9.9.9.2")

            assert _refused_for_tries(_try_code(new_browser, "9.9.9.1", _code(secret)))
            assert _refused_for_tries(_try_code(made_up, "9.9.9.2", _NEVER_A_CODE))
            db.session.expire_all()
            assert db.session.get(User, user_id).failed_login_count == _CODE_TRIES

    def test_another_users_cookie_and_a_cookie_thirty_days_old_share_them(
        self, rate_app, seed_user, seed_second_user,
    ):
        """A record of another user's, and one past its thirty days, are strangers' here.

        The second user's browser holds a live record, sent under the first
        user's cookie name; the owner's browser holds the first user's record,
        thirty days and a minute old.  The owner's sign-in spent one of the
        account's five, four guesses spend the rest (1 + 4 = 5), and both
        browsers are refused, even with the right code.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            secret = enable_mfa(db.session, user_id)
            borrowed = rate_app.test_client()
            assert _signed_in(borrowed.post(
                "/login",
                data={"email": _SECOND_EMAIL, "password": _SECOND_PASSWORD},
                **through_nginx("5.5.5.5"),
            ))
            borrowed.post("/logout", **through_nginx("5.5.5.5"))
            borrowed.set_cookie(
                f"shekel_device_{user_id}",
                borrowed.get_cookie(
                    f"shekel_device_{seed_second_user['user'].id}",
                ).value,
            )
            owner = _known_browser(rate_app, secret, "7.7.7.7")
            device = db.session.query(KnownDevice).filter_by(user_id=user_id).one()
            device.last_signed_in_at -= timedelta(days=30, minutes=1)
            db.session.commit()
            _guesses_checked(rate_app, _CODE_TRIES - 1, "8.8.8")

            _type_password(borrowed, "5.5.5.5")
            _type_password(owner, "7.7.7.7")

            assert _refused_for_tries(_try_code(borrowed, "5.5.5.5", _next_code(secret)))
            assert _refused_for_tries(_try_code(owner, "7.7.7.7", _next_code(secret)))
