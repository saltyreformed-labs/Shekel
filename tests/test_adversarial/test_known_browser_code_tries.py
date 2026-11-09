"""Shekel Budget App -- Adversarial Tests: a known browser's own tries, at the code form and re-confirm.

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

Re-confirm spends its tries by the same rule, from counters of its own (ruling
**plan#3071**, :class:`TestAKnownBrowserHasItsOwnReConfirmTries`): someone
holding a copy of the owner's session, but not the browser's cookie, is a
stranger there, and spends the account's five.  A session answers only at the
address and browser it signed in from (``session_protection = "strong"``), so
a copy is used from the owner's address, and re-confirm has no address limit
that could answer instead of the counters.
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


_INVALID_PASSWORD = b"Invalid password."

#: Where the owner signs in, and so where a copy of their session answers.
_OWNERS_ADDRESS = "7.7.7.7"

_WRONG_PASSWORD = "not-the-password"

#: Strangers' wrong passwords in a row that lock the account: spelled here as
#: an oracle.
_LOCK_THRESHOLD = 10

_LOCKED_OUT = b"Too many wrong attempts. Try again later."


def _re_confirm(browser, address, password=SEED_USER_PASSWORD):
    """POST *password* at ``/reauth`` from *address*; return the response."""
    return browser.post(
        "/reauth", data={"password": password}, **through_nginx(address),
    )


def _re_confirmed(response):
    """Return whether *response* is a finished re-confirm's redirect."""
    return (
        response.status_code == 302
        and response.headers["Location"].endswith("/dashboard")
    )


def _re_confirm_refused_for_tries(response):
    """Return whether *response* is the limit's refusal page, not a checked password."""
    return (
        response.status_code == 429
        and _TOO_MANY in response.data
        and _INVALID_PASSWORD not in response.data
    )


def _signed_in_owner(rate_app, address):
    """Return a browser that has finished a sign-in with no code, still signed in."""
    browser = rate_app.test_client()
    assert _signed_in(browser.post(
        "/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD},
        **through_nginx(address),
    ))
    return browser


def _stolen_session(rate_app, owner):
    """Return a new browser holding a copy of *owner*'s session cookie and nothing else.

    Signed in as the owner, and a stranger at every door: the browser's own
    cookie stayed with the owner.
    """
    name = rate_app.config["SESSION_COOKIE_NAME"]
    thief = rate_app.test_client()
    thief.set_cookie(name, owner.get_cookie(name).value)
    return thief


def _wrong_passwords_checked(browser, count):
    """Re-confirm *count* wrong passwords from the owner's address; assert each was checked."""
    for _ in range(count):
        response = _re_confirm(browser, _OWNERS_ADDRESS, _WRONG_PASSWORD)
        assert response.status_code == 200
        assert _INVALID_PASSWORD in response.data


class TestAKnownBrowserHasItsOwnReConfirmTries:
    """Re-confirm's five per counter: a known browser's own, else the account's (ruling plan#3071).

    The owner's sign-in was at ``/login``, so it spent none of re-confirm's
    tries: each door's counters are its own.
    """

    def test_a_stolen_session_that_spends_the_accounts_tries_cannot_refuse_the_owner(
        self, rate_app, seed_user,
    ):
        """Five wrong passwords spend the account's five, and the owner still re-confirms.

        A sixth wrong one from the stolen session is refused unchecked, so the
        account still counts five.  Then the owner's own browser, which the
        account's counter does not answer for, re-confirms with the right
        password.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            owner = _signed_in_owner(rate_app, _OWNERS_ADDRESS)
            thief = _stolen_session(rate_app, owner)
            _wrong_passwords_checked(thief, _CODE_TRIES)
            assert _re_confirm_refused_for_tries(
                _re_confirm(thief, _OWNERS_ADDRESS, _WRONG_PASSWORD),
            )
            db.session.expire_all()
            assert db.session.get(User, user_id).failed_login_count == _CODE_TRIES

            assert _re_confirmed(_re_confirm(owner, _OWNERS_ADDRESS))

    def test_a_known_browsers_sixth_re_confirm_is_refused(self, rate_app, seed_user):
        """Five wrong passwords are checked and counted on its record; a sixth is refused.

        The refused sixth, wrong too, is never checked, so its record still
        counts five.
        And none of the five was the account's: a stolen session then still
        has its password checked.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            owner = _signed_in_owner(rate_app, _OWNERS_ADDRESS)
            _wrong_passwords_checked(owner, _CODE_TRIES)

            assert _re_confirm_refused_for_tries(
                _re_confirm(owner, _OWNERS_ADDRESS, _WRONG_PASSWORD),
            )
            assert _device_count(user_id, owner) == _CODE_TRIES
            thief = _stolen_session(rate_app, owner)
            checked = _re_confirm(thief, _OWNERS_ADDRESS, _WRONG_PASSWORD)
            assert checked.status_code == 200
            assert _INVALID_PASSWORD in checked.data

    def test_a_strangers_browser_and_a_made_up_cookie_share_the_accounts_tries(
        self, rate_app, seed_user,
    ):
        """Once a stolen session spends the account's five, both are refused before any check.

        A second copy of the session is a stranger's browser, refused the right
        password.  A third carries a device cookie it made up, which names no
        record and so buys no counter of its own: a key built from what a
        browser sends would give five new tries for each one it made up.  Its
        wrong password is refused unchecked, so the account still counts the
        five wrong ones and no more.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            owner = _signed_in_owner(rate_app, _OWNERS_ADDRESS)
            _wrong_passwords_checked(_stolen_session(rate_app, owner), _CODE_TRIES)

            strangers = _stolen_session(rate_app, owner)
            made_up = _stolen_session(rate_app, owner)
            made_up.set_cookie(f"shekel_device_{user_id}", secrets.token_urlsafe(32))

            assert _re_confirm_refused_for_tries(_re_confirm(strangers, _OWNERS_ADDRESS))
            assert _re_confirm_refused_for_tries(
                _re_confirm(made_up, _OWNERS_ADDRESS, _WRONG_PASSWORD),
            )
            db.session.expire_all()
            assert db.session.get(User, user_id).failed_login_count == _CODE_TRIES

    def test_the_form_page_keeps_the_default_limits_after_a_re_confirm(
        self, rate_app, seed_user,
    ):
        """After a re-confirm has spent a try, opening the form still spends the defaults.

        Each GET counts, one apiece, against a limit whose headers the page
        carries: the tries' limit adds to the app's defaults, never replacing
        them for the whole page.
        """
        with rate_app.app_context():
            owner = _signed_in_owner(rate_app, _OWNERS_ADDRESS)
            assert _re_confirmed(_re_confirm(owner, _OWNERS_ADDRESS))

            first = owner.get("/reauth", **through_nginx(_OWNERS_ADDRESS))
            second = owner.get("/reauth", **through_nginx(_OWNERS_ADDRESS))

            assert first.status_code == second.status_code == 200
            assert (
                int(first.headers["X-RateLimit-Remaining"])
                - int(second.headers["X-RateLimit-Remaining"])
            ) == 1

    def test_the_code_forms_tries_are_not_re_confirms(self, rate_app, seed_user):
        """Guessers spend the account's five at the code form; re-confirm's are untouched.

        The owner's own code was a stranger's, so it spent one of the code
        form's five, and four guesses spend the rest (1 + 4 = 5).  A stolen
        session, a stranger at re-confirm too, still has its password checked
        there: each door's counters are its own.
        """
        with rate_app.app_context():
            secret = enable_mfa(db.session, seed_user["user"].id)
            owner = rate_app.test_client()
            _type_password(owner, _OWNERS_ADDRESS)
            assert _signed_in(_try_code(owner, _OWNERS_ADDRESS, _code(secret)))
            _guesses_checked(rate_app, _CODE_TRIES - 1, "8.8.8")
            assert _refused_for_tries(_guess(rate_app, "8.8.9.0"))

            checked = _re_confirm(
                _stolen_session(rate_app, owner), _OWNERS_ADDRESS, _WRONG_PASSWORD,
            )

            assert checked.status_code == 200
            assert _INVALID_PASSWORD in checked.data

    def test_a_form_the_schema_rejects_spends_no_try(self, rate_app, seed_user):
        """Five forms with no password, then a wrong one: it is checked, and counts one.

        The schema answers before the try is spent, so none of the five spent
        one, and the sixth POST is the browser's first try.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            owner = _signed_in_owner(rate_app, _OWNERS_ADDRESS)
            for _ in range(_CODE_TRIES):
                rejected = owner.post(
                    "/reauth", data={}, **through_nginx(_OWNERS_ADDRESS),
                )
                assert rejected.status_code == 200

            checked = _re_confirm(owner, _OWNERS_ADDRESS, _WRONG_PASSWORD)

            assert checked.status_code == 200
            assert _INVALID_PASSWORD in checked.data
            assert _device_count(user_id, owner) == 1

    def test_a_stranger_the_lock_refuses_spends_a_try(self, rate_app, seed_user):
        """Strangers lock the account; a stolen session's five right passwords are refused by it.

        Each spent one of the account's five, because the try is spent before
        the lock is asked, so the sixth is the limit's refusal.  Ten strangers'
        wrong passwords at ``/login``, each from a new browser and address,
        put the lock on, as a stranger who knows the email would.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            owner = _signed_in_owner(rate_app, _OWNERS_ADDRESS)
            for octet in range(_LOCK_THRESHOLD):
                rate_app.test_client().post(
                    "/login",
                    data={"email": SEED_USER_EMAIL, "password": _WRONG_PASSWORD},
                    **through_nginx(f"8.8.8.{octet}"),
                )
            db.session.expire_all()
            assert db.session.get(User, user_id).locked_until is not None
            thief = _stolen_session(rate_app, owner)
            for _ in range(_CODE_TRIES):
                refused = _re_confirm(thief, _OWNERS_ADDRESS)
                assert refused.status_code == 200
                assert _LOCKED_OUT in refused.data

            sixth = _re_confirm(thief, _OWNERS_ADDRESS)

            assert sixth.status_code == 429
            assert _TOO_MANY in sixth.data
