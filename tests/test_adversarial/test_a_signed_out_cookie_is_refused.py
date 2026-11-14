"""Shekel Budget App -- Adversarial Tests: a signed-out cookie is refused by the server.

Plan#2171, ruling **plan#3925**: each finished sign-in has a record on the
server, the session cookie carries its token, and signing out deletes the
record.  These tests drive the real app, with a second test client holding a
copy of a browser's session cookie, as someone who copied it would:

* :class:`TestACopyDiesWithTheSignOut` -- the copy works until the sign-out
  and never after, the user's other browser stays signed in, and every trust
  stays.
* :class:`TestTheRecord` -- a sign-in writes one record and a sign-out
  deletes it; a new sign-in on a browser replaces the record its old session
  named, and purges the user's records past thirty days.

The copy shares the browser's address and User-Agent (``tests._session_copies``),
so what refuses it here is the server's record alone.  (Faking the address is
nginx's half of the finding, plan#2169.)
"""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.services import sign_in_service
from app.utils.bearer_tokens import token_hash
from app.utils.session_helpers import SESSION_LAST_ACTIVITY_KEY, SESSION_TOKEN_KEY
from tests._seed_owner_fixtures import log_in_seed_user
from tests._session_copies import (
    copy_of, is_signed_in, sign_ins_of, token_of, trusted_device_ids,
)

#: Ruling plan#3069's thirty days, spelled here as an oracle the model's
#: constant cannot move silently.
_THIRTY_DAYS = timedelta(days=30)

#: Past the twelve-hour idle window the test configuration pins.
_IDLE_PAST_THE_WINDOW = timedelta(hours=13)


def _sign_out(browser):
    """POST ``/logout`` from *browser*, asserting it lands on the sign-in page."""
    response = browser.post("/logout")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


class TestACopyDiesWithTheSignOut:
    """A copy taken before the sign-out is refused after it, and only it."""

    def test_a_copy_works_until_the_sign_out_and_never_after(self, app, seed_user):
        """The control is the same copy signed in a request before the sign-out."""
        browser = log_in_seed_user(app.test_client())
        copy = copy_of(app, browser)
        assert is_signed_in(copy)

        _sign_out(browser)

        assert not is_signed_in(browser)
        assert not is_signed_in(copy)

    def test_the_users_other_browser_stays_signed_in_and_every_trust_stays(
        self, app, seed_user,
    ):
        """Signing out ends this browser's sign-in alone (ruling plan#3925)."""
        user_id = seed_user["user"].id
        browser = log_in_seed_user(app.test_client())
        other = log_in_seed_user(app.test_client())
        trusted = trusted_device_ids(user_id)
        assert len(trusted) == 2

        _sign_out(browser)

        assert is_signed_in(other)
        assert trusted_device_ids(user_id) == trusted

    def test_a_session_whose_record_is_gone_is_refused(self, app, seed_user):
        """However good its cookie: the record, not the cookie, answers.

        The record is deleted as a sign-out on another copy would delete it.
        """
        browser = log_in_seed_user(app.test_client())
        assert is_signed_in(browser)

        sign_in_service.end_sign_in(token_of(browser))
        db.session.commit()

        assert not is_signed_in(browser)


class TestTheRecord:
    """One record per live sign-in, written by the sign-in and deleted by the sign-out."""

    def test_a_sign_in_writes_one_record_and_a_sign_out_deletes_it(
        self, app, seed_user,
    ):
        """The record holds the hash of the token the session carries."""
        user_id = seed_user["user"].id
        browser = log_in_seed_user(app.test_client())
        assert sign_ins_of(user_id) == {token_hash(token_of(browser))}

        _sign_out(browser)

        assert sign_ins_of(user_id) == set()
        with browser.session_transaction() as sess:
            assert SESSION_TOKEN_KEY not in sess

    def test_a_new_sign_in_replaces_the_record_the_browsers_old_session_named(
        self, app, seed_user,
    ):
        """A copy kept active dies when the browser it was taken from signs in again.

        The browser's own session idles out while the copy keeps using its
        cookie; the browser then signs in again, and its old session's record
        goes with the old session.
        """
        user_id = seed_user["user"].id
        browser = log_in_seed_user(app.test_client())
        old_token = token_of(browser)
        copy = copy_of(app, browser)
        with browser.session_transaction() as sess:
            sess[SESSION_LAST_ACTIVITY_KEY] = (
                datetime.now(timezone.utc) - _IDLE_PAST_THE_WINDOW
            ).isoformat()
        assert not is_signed_in(browser)
        assert is_signed_in(copy)

        log_in_seed_user(browser)

        assert token_of(browser) != old_token
        assert sign_ins_of(user_id) == {token_hash(token_of(browser))}
        assert not is_signed_in(copy)
        assert is_signed_in(browser)

    def test_a_sign_in_purges_the_users_records_past_thirty_days(
        self, app, seed_user,
    ):
        """Thirty days to the second is dead, and goes; a second younger stays."""
        user_id = seed_user["user"].id
        now = datetime.now(timezone.utc)
        dead = sign_in_service.record_sign_in(user_id, None, now=now - _THIRTY_DAYS)
        young = sign_in_service.record_sign_in(
            user_id, None, now=now - _THIRTY_DAYS + timedelta(minutes=1),
        )
        db.session.commit()

        browser = log_in_seed_user(app.test_client())

        assert sign_ins_of(user_id) == {
            token_hash(young), token_hash(token_of(browser)),
        }
        assert token_hash(dead) not in sign_ins_of(user_id)
