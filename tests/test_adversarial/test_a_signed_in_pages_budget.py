"""Shekel Budget App -- Adversarial Tests: a signed-in page spends its known browser's own budget.

Plan#2772, ruling **plan#3108**, fixing finding **plan#3107**.  The app's
default limits, thirty a minute and two hundred an hour on each page, count a
signed-in request against its browser's own budget when the browser is known,
else against its account's, which every other browser signed in to the
account shares.  So someone holding a copy of the owner's session, but not
the browser's cookie, spends the account's budget and leaves the owner's
browser its own.  Before, every browser of the account shared one budget, and
a copy could keep the owner off every page for up to an hour, Change password
and Log out all other sessions included, and keep it that way.

A session answers only at the address and browser it signed in from
(``session_protection = "strong"``), so a copy is used from the owner's
address.  The budget comes from the record the browser's cookie names, never
from the cookie: a made-up cookie names no record and shares the account's.
A copy that took the browser's cookie too shares the browser's budget, as at
every per-browser defense.  And the owner's own browser spends the account's
budget on a page it opens from another site, a link in an email for one:
the device cookie is SameSite=Strict, so the browser sends it only on this
app's own navigations, and the next page opened from this one is the
browser's own again.

These run on an app built with the limiter on, as in production; the suite's
app has it off.  Each spends the thirty-a-minute clause of one page: the
security section of Settings, where Change password and Log out all other
sessions are.  The owner's sign-in was at ``/login``, so it spent none of this
page's budget: each page's counters are its own.
"""

import secrets

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.known_device import KnownDevice
from tests._limited_app import (
    limited_app, signed_in_browser, stolen_session, through_nginx,
)
from tests._test_helpers import capture_sql_statements

#: The default limit's clause that trips first: thirty a minute, spelled here
#: as an oracle rather than read from the configuration.
_PER_MINUTE = 30

#: Where the owner signs in, and so where a copy of their session answers.
_OWNERS_ADDRESS = "7.7.7.7"

#: The page every test spends, holding Change password and Log out all other
#: sessions: the Security page, a page of its own since ruling plan#3435.
_SECURITY_PAGE = "/security"

#: What the security page shows, so a 200 is the page and not some other one.
_THE_PAGE = b"Log Out All Other Sessions"

#: What the 429 page says.
_TOO_MANY = b"made too many attempts"

_WRONG_PASSWORD = "not-the-password"

#: What re-confirm says to a wrong password it checked.
_INVALID_PASSWORD = b"Invalid password."


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


def _open(browser, address=_OWNERS_ADDRESS):
    """GET the security page from *address*; return the response."""
    return browser.get(_SECURITY_PAGE, **through_nginx(address))


def _shown(response):
    """Return whether *response* is the security page itself."""
    return response.status_code == 200 and _THE_PAGE in response.data


def _refused(response):
    """Return whether *response* is the limit's refusal page."""
    return response.status_code == 429 and _TOO_MANY in response.data


def _spend_the_minute(browser):
    """Open the security page thirty times from the owner's address; assert each was shown."""
    for hit in range(_PER_MINUTE):
        response = _open(browser)
        assert _shown(response), f"hit {hit + 1} answered {response.status_code}"


class TestASignedInPagesBudget:
    """A known browser's own budget, else the account's, on every signed-in page."""

    def test_a_stolen_session_that_spends_the_accounts_budget_cannot_refuse_the_owner(
        self, rate_app, seed_user,
    ):
        """A copy of the session spends the account's thirty; the owner's browser still opens it.

        The copy's thirty-first is refused, so it was counted, and then the
        owner's own browser, which the account's budget does not answer for,
        is shown the page.
        """
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            thief = stolen_session(rate_app, owner)
            _spend_the_minute(thief)
            assert _refused(_open(thief))

            assert _shown(_open(owner))

    def test_a_known_browsers_own_budget_runs_out_on_its_own(self, rate_app, seed_user):
        """The owner's browser spends its own thirty, and the account's is untouched.

        Its thirty-first is refused, so its budget is a real one and not a
        pass.  A copy of the session is then shown the page: none of the
        thirty was the account's.
        """
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            _spend_the_minute(owner)
            assert _refused(_open(owner))

            assert _shown(_open(stolen_session(rate_app, owner)))

    def test_a_second_copy_and_a_made_up_cookie_share_the_accounts_budget(
        self, rate_app, seed_user,
    ):
        """Once one copy spends the account's thirty, the other strangers are refused too.

        A second copy of the session is another stranger's browser.  A third
        carries a device cookie it made up, which names no record and so buys
        no budget of its own: a key built from what a browser sends would
        give a fresh thirty for each cookie it made up.  The owner's browser
        is still shown the page.
        """
        with rate_app.app_context():
            user_id = seed_user["user"].id
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            _spend_the_minute(stolen_session(rate_app, owner))

            second_copy = stolen_session(rate_app, owner)
            made_up = stolen_session(rate_app, owner)
            made_up.set_cookie(f"shekel_device_{user_id}", secrets.token_urlsafe(32))

            assert _refused(_open(second_copy))
            assert _refused(_open(made_up))
            assert _shown(_open(owner))

    def test_a_signed_out_visitor_still_counts_by_address(self, rate_app, seed_user):
        """A signed-out flood from the owner's address spends that address's thirty alone.

        Each of its thirty is sent to sign in, having been counted before the
        login gate, and its thirty-first is refused.  A signed-out visitor
        from another address is still sent to sign in, and the owner's
        browser and a copy of the session, at the flooded address, are each
        shown the page: neither the browser's budget nor the account's was
        the address's.
        """
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            visitor = rate_app.test_client()
            for hit in range(_PER_MINUTE):
                response = _open(visitor)
                assert response.status_code == 302, f"hit {hit + 1}"
                assert "/login" in response.headers["Location"]
            assert _refused(_open(visitor))

            elsewhere = _open(rate_app.test_client(), address="9.9.9.9")
            assert elsewhere.status_code == 302
            assert "/login" in elsewhere.headers["Location"]
            assert _shown(_open(owner))
            assert _shown(_open(stolen_session(rate_app, owner)))

    def test_a_signed_in_page_reads_the_browsers_record_once(self, rate_app, seed_user):
        """The cost: one statement for the known browser's record, none for no cookie.

        The page reads no record of its own, and the app's two default
        limits each ask for the key, so a second read would show here.  A
        copy of the session sends no device cookie and so asks nothing.
        """
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            thief = stolen_session(rate_app, owner)

            owners, owners_statements = capture_sql_statements(
                lambda: owner.get("/settings", **through_nginx(_OWNERS_ADDRESS)),
            )
            thiefs, thiefs_statements = capture_sql_statements(
                lambda: thief.get("/settings", **through_nginx(_OWNERS_ADDRESS)),
            )

            assert owners.status_code == thiefs.status_code == 200
            assert _reads_of_records(owners_statements) == 1, owners_statements
            assert _reads_of_records(thiefs_statements) == 0, thiefs_statements


class TestTheBudgetsReadComesBeforeTheLock:
    """The key's read of the browser's record, before the owner's lock, loads no record.

    The default limits run before the owner's lock (ruling R-CC122), so a
    record loaded there would hold its count as it stood before the lock,
    and a wrong guess counted on that copy under the lock would write back
    over one counted meanwhile.
    """

    def test_re_confirm_loads_the_record_only_under_the_lock(self, rate_app, seed_user):
        """A known browser's wrong re-confirm: the id read, then the lock, then the record.

        Statements and record loads are logged in one order.  The session is
        emptied first, because a record already in it would be returned by a
        load without the load event firing, and the door's own read under the
        lock is the load the log must show, so it shows the log can see one.
        """
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            db.session.expunge_all()
            happened = []

            def on_statement(_conn, _cursor, statement, *_rest):
                if "pg_advisory_xact_lock" in statement:
                    happened.append("lock")
                elif "known_devices" in statement:
                    happened.append("read")

            def on_load(_session, instance):
                if isinstance(instance, KnownDevice):
                    happened.append("load")

            event.listen(db.engine, "before_cursor_execute", on_statement)
            event.listen(Session, "loaded_as_persistent", on_load)
            try:
                response = owner.post(
                    "/reauth", data={"password": _WRONG_PASSWORD},
                    **through_nginx(_OWNERS_ADDRESS),
                )
            finally:
                event.remove(db.engine, "before_cursor_execute", on_statement)
                event.remove(Session, "loaded_as_persistent", on_load)

            assert response.status_code == 200
            assert _INVALID_PASSWORD in response.data
            lock = happened.index("lock")
            assert happened[:lock] == ["read"], happened
            assert "load" in happened[lock:], happened


def _reads_of_records(statements):
    """Return how many of *statements* read ``auth.known_devices``."""
    return len([statement for statement, _ in statements if "known_devices" in statement])
