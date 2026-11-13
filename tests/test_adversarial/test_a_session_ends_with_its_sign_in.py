"""Shekel Budget App -- Adversarial Tests: a session ends thirty days after its sign-in.

Plan#2772, ruling **plan#3069**: a session ends thirty days after its sign-in,
however active, the same thirty days a browser stays known after it (ruling
**plan#2995**), so a browser that is signed in is a known one, unless its own
ten wrong guesses removed its record.  These tests drive the real app:

* :class:`TestThirtyDaysAfterItsSignIn` -- the boundary, to the second, and a
  session with no sign-in stamp, which every session open at the release is.
* :class:`TestNothingButASignInRenewsIt` -- Log out all, Change password and
  Disable two-factor each move ``_session_created_at`` to now, and none moves
  the sign-in, so a session that pressed one near day 30 still ends on day 30.
* :class:`TestTheSessionAndItsRecord` -- the session's sign-in is the record's
  ``last_signed_in_at``, and a browser kept active for 29 days still gets past
  a stranger's lock.

The seed owner is made with two-factor (plan#3600), so a browser signs in with
no code owed only on a browser the owner trusts, the shared sign-in
(``tests._seed_owner_fixtures.log_in_seed_user``); the code tests type the
owner's own (``tests._two_factor_sign_in.two_factor_secret_of``).
"""

import time
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
import time_machine

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import MfaConfig, User
from app.services import auth_service
from app.utils.session_helpers import (
    SESSION_CREATED_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_SIGNED_IN_AT_KEY,
)
from tests._guess_records import locked_until_of
from tests._seed_owner_fixtures import (
    SEED_USER_EMAIL,
    SEED_USER_PASSWORD,
    log_in_seed_user,
)
from tests._two_factor_sign_in import code_now, two_factor_secret_of

#: Ruling plan#3069's thirty days, spelled here as an oracle the model's
#: constant cannot move silently.
_THIRTY_DAYS = timedelta(days=30)

_ONE_SECOND = timedelta(seconds=1)

#: How long before day thirty an act is pressed, and how long after it the
#: session is asked for again: two minutes later is past day thirty by one.
_BEFORE_DAY_THIRTY = timedelta(minutes=1)
_LATER = timedelta(minutes=2)

#: Wrong passwords in a row that lock the account: spelled here as an oracle.
_THRESHOLD = 10

#: The length of one authenticator step.
_TOTP_STEP_SECONDS = 30

_NEW_PASSWORD = "a-brand-new-passphrase"


def _password(client, password=SEED_USER_PASSWORD):
    """POST the sign-in form for the seed owner; return the response."""
    return client.post(
        "/login", data={"email": SEED_USER_EMAIL, "password": password},
    )


def _signed_in(response):
    """Return whether *response* is a completed sign-in's redirect."""
    if response.status_code != 302:
        return False
    location = response.headers["Location"]
    return "/login" not in location and "/mfa/verify" not in location


def _sign_in(app):
    """Return a browser that has just finished a sign-in with no code owed.

    A browser the owner trusts, through the shared sign-in, which asserts its
    own redirect.
    """
    return log_in_seed_user(app.test_client())


def _is_signed_in(browser):
    """Return whether *browser*'s session still loads, by a signed-in page.

    Refused, the login gate sends it to ``/login``; any other answer is a
    failure of the probe rather than of the session.
    """
    response = browser.get("/dashboard")
    if response.status_code == 200:
        return True
    location = response.headers.get("Location", "")
    assert response.status_code == 302 and "/login" in location, (
        f"The probe answered {response.status_code} ({location!r})"
    )
    return False


def _stamp(browser, **stamps):
    """Write each ``key=datetime`` in *stamps* into *browser*'s session."""
    with browser.session_transaction() as sess:
        for key, moment in stamps.items():
            sess[key] = moment.isoformat()


def _next_code(secret):
    """Return a code for *secret* one step past now's, so it is no replay."""
    return pyotp.TOTP(secret).at(int(time.time()) + _TOTP_STEP_SECONDS)


class TestThirtyDaysAfterItsSignIn:
    """The session ends at the record's own boundary, however active."""

    @pytest.mark.parametrize(
        "age, still_signed_in",
        [
            pytest.param(_THIRTY_DAYS, False, id="thirty-days-to-the-second"),
            pytest.param(_THIRTY_DAYS - _ONE_SECOND, True, id="a-second-younger"),
        ],
    )

    def test_the_boundary_is_thirty_days_to_the_second(
        self, app, seed_user, age, still_signed_in,
    ):
        """Active this instant, signed in *age* ago: refused at thirty days exactly.

        The clock is frozen, so the age is exact.  It is the record's own
        boundary (``known_device_service._known``): a last sign-in thirty days
        ago to the second is a stranger's.
        """
        browser = _sign_in(app)
        moment = datetime.now(timezone.utc)
        with time_machine.travel(moment, tick=False):
            _stamp(browser, **{
                SESSION_SIGNED_IN_AT_KEY: moment - age,
                SESSION_LAST_ACTIVITY_KEY: moment,
            })

            assert _is_signed_in(browser) is still_signed_in

    def test_a_session_without_its_sign_in_stamp_is_refused(self, app, seed_user):
        """Every session open at the release has none, so each signs in once then.

        The control is the same session with its stamp, a request before.
        """
        browser = _sign_in(app)
        assert _is_signed_in(browser)

        with browser.session_transaction() as sess:
            del sess[SESSION_SIGNED_IN_AT_KEY]

        assert not _is_signed_in(browser)


def _the_session_after(browser, act):
    """Press *act* a minute before day thirty; return whether the session lasts past it.

    The session is stamped as signed in thirty days less a minute ago, as a
    sign-in then would have stamped it, and the act runs.  The act must move
    ``_session_created_at`` (each of these does, to survive the bump it
    writes) and leave the session signed in; two minutes later, kept active,
    the session is asked for again.

    Args:
        browser: A signed-in browser.
        act: Called with *browser*: one door that writes the bump.

    Returns:
        Whether the session still loads two minutes later.
    """
    signed_in = datetime.now(timezone.utc) - _THIRTY_DAYS + _BEFORE_DAY_THIRTY
    _stamp(browser, **{
        SESSION_SIGNED_IN_AT_KEY: signed_in,
        SESSION_CREATED_AT_KEY: signed_in,
    })

    act(browser)

    with browser.session_transaction() as sess:
        assert datetime.fromisoformat(sess[SESSION_CREATED_AT_KEY]) > signed_in, (
            "The act did not move _session_created_at, so this test grades nothing"
        )
    assert _is_signed_in(browser)
    later = datetime.now(timezone.utc) + _LATER
    with time_machine.travel(later, tick=False):
        _stamp(browser, **{SESSION_LAST_ACTIVITY_KEY: later})
        return _is_signed_in(browser)


class TestNothingButASignInRenewsIt:
    """Each door that moves ``_session_created_at`` leaves the thirty days running.

    Were the bound read off ``_session_created_at``, or the door to write the
    sign-in stamp, the session would be two minutes old by its stamp and stay
    signed in past day thirty while its browser stops being known.
    """

    def test_logging_out_all_other_sessions(self, app, seed_user):
        """``/invalidate-sessions`` near day thirty: the session still ends on it."""
        def log_out_all(browser):
            assert browser.post("/invalidate-sessions").status_code == 302

        assert _the_session_after(_sign_in(app), log_out_all) is False

    def test_changing_the_password(self, app, seed_user):
        """``/change-password`` near day thirty: the session still ends on it."""
        user_id = seed_user["user"].id

        def change_password(browser):
            browser.post("/change-password", data={
                "current_password": SEED_USER_PASSWORD,
                "new_password": _NEW_PASSWORD,
                "confirm_password": _NEW_PASSWORD,
            })
            db.session.expire_all()
            assert auth_service.verify_password(
                _NEW_PASSWORD, db.session.get(User, user_id).password_hash,
            )

        assert _the_session_after(_sign_in(app), change_password) is False


class TestTheSessionAndItsRecord:
    """One sign-in starts both, from one instant."""

    def test_the_sessions_sign_in_is_the_records_last_sign_in(self, app, seed_user):
        """To the microsecond, so the session cannot outlive the record by a gap."""
        user_id = seed_user["user"].id
        browser = _sign_in(app)

        with browser.session_transaction() as sess:
            signed_in = datetime.fromisoformat(sess[SESSION_SIGNED_IN_AT_KEY])
        db.session.expire_all()
        (device,) = db.session.query(KnownDevice).filter_by(user_id=user_id).all()

        assert device.last_signed_in_at == signed_in

    def test_kept_active_for_29_days_it_passes_a_strangers_lock(self, app, seed_user):
        """Signed in 29 days ago and in use since: Change password gets past the lock.

        The session and the record are both 29 days old, as one sign-in then
        would have made them, and strangers' wrong passwords lock the account.
        """
        user_id = seed_user["user"].id
        browser = _sign_in(app)
        db.session.expire_all()
        (device,) = db.session.query(KnownDevice).filter_by(user_id=user_id).all()
        device.last_signed_in_at -= timedelta(days=29)
        db.session.commit()
        _stamp(browser, **{
            SESSION_SIGNED_IN_AT_KEY: device.last_signed_in_at,
            SESSION_CREATED_AT_KEY: device.last_signed_in_at,
        })
        for _ in range(_THRESHOLD):
            assert not _signed_in(_password(app.test_client(), "not-the-password"))
        assert locked_until_of(SEED_USER_EMAIL) > datetime.now(timezone.utc)

        browser.post("/change-password", data={
            "current_password": SEED_USER_PASSWORD,
            "new_password": _NEW_PASSWORD,
            "confirm_password": _NEW_PASSWORD,
        })

        db.session.expire_all()
        assert auth_service.verify_password(
            _NEW_PASSWORD, db.session.get(User, user_id).password_hash,
        )
