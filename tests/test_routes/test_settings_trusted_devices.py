"""Settings > Security lists the trusted devices, each one forgettable.

Plan#2173, task plan#2411, ruling **plan#2406**.  The Security section shows a
"Trusted Devices" card: each live device of the signed-in user, named by its
browser, with the day it was trusted and the day it expires in the display
timezone, and the browser the page is rendered on marked.  Each row's form
forgets its device, after which signing in on that browser asks for the code
again.  Another user's device and a missing one are one 404, each paired with
a case proving the URL still routes (a URL-map 404 would pass the same
assertion while guarding nothing).

**One code per user per test**: replay prevention refuses a second code inside
its thirty-second step, so each test that signs in through the code form does
so once.  Devices a test needs beyond the one a real sign-in trusts are made
through the service, as the trust itself would make them.
"""

import logging
import re
import time
from datetime import datetime, time as clock_time, timedelta, timezone

import pyotp
import sqlalchemy

from app.extensions import db
from app.models.trusted_device import TrustedDevice
from app.services import trusted_device_service
from tests._test_helpers import enable_mfa
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_FIREFOX_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
)
_SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 "
    "Safari/604.1"
)
_CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

_SECURITY_PAGE = "/settings?section=security"

#: The day format the page shows, the ``local_datetime`` filter's default,
#: spelled here as an oracle.
_DAY_FORMAT = "%b %-d, %Y"


def _code(secret):
    """Return the authenticator code for *secret* at this instant."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _password(browser):
    """POST the seed owner's correct password to the sign-in form."""
    return browser.post("/login", data={
        "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
    })


def _asked_the_code(response):
    """Return whether *response* sends the browser to the code form."""
    return (
        response.status_code == 302
        and response.headers["Location"].endswith("/mfa/verify")
    )


def _signed_in(response):
    """Return whether *response* is a completed sign-in's redirect."""
    if response.status_code != 302:
        return False
    location = response.headers["Location"]
    return "/login" not in location and "/mfa/verify" not in location


def _firefox_that_trusted_itself(app, secret):
    """Return a Firefox browser signed in with a code and "Trust this device".

    The User-Agent is set on the client, so every request it makes sends the
    same one: strong session protection ends a session whose browser changes.
    """
    browser = app.test_client()
    browser.environ_base["HTTP_USER_AGENT"] = _FIREFOX_LINUX
    assert _asked_the_code(_password(browser))
    response = browser.post("/mfa/verify", data={
        "totp_code": _code(secret), "trust_device": "1",
    })
    assert _signed_in(response), (
        f"Setup failed: the trusting sign-in did not complete "
        f"({response.status_code}, {response.headers.get('Location')!r})"
    )
    return browser


def _trust(user_id, user_agent):
    """Trust a device for *user_id* through the service, committed; return its id."""
    trusted_device_service.trust_device(user_id, user_agent)
    db.session.commit()
    return db.session.execute(
        sqlalchemy.select(sqlalchemy.func.max(TrustedDevice.id))
        .where(TrustedDevice.user_id == user_id)
    ).scalar_one()


def _move_trust_to(device_id, instant):
    """Set *device_id*'s ``trusted_at`` to *instant*, committed."""
    db.session.execute(
        sqlalchemy.update(TrustedDevice)
        .where(TrustedDevice.id == device_id)
        .values(trusted_at=instant)
    )
    db.session.commit()


def _device_ids_of(user_id):
    """Return the ids of every device row *user_id* holds, live or dead."""
    return sorted(db.session.execute(
        sqlalchemy.select(TrustedDevice.id)
        .where(TrustedDevice.user_id == user_id)
    ).scalars())


def _text(html):
    """Return *html*'s text: tags dropped, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _card(page):
    """Return the Trusted Devices card's HTML out of the Security page."""
    start = page.index("bi-laptop")
    end = page.index("form-card__title", start)
    return page[start:end]


def _rows(page):
    """Return the HTML of each device row in the Trusted Devices card, in order."""
    return _card(page).split('<div class="list-group-item">')[1:]


def _forget_action(row):
    """Return the URL the row's Forget form posts to."""
    return re.search(r'action="([^"]+)"', row).group(1)


class TestTheList:
    """Each live device of the user, named, dated, and this browser marked."""

    def test_each_device_shows_its_browser_and_its_two_days(
        self, auth_client, seed_user,
    ):
        """Newest first; days in the display timezone, not UTC's.

        The older device is trusted at 03:00 UTC two days ago, which is the
        evening BEFORE on the display clock (22:00 or 23:00 Eastern), so a
        page that dated it by UTC would show the wrong day.  It expires
        thirty days later at 03:00 UTC: again the evening before, Eastern.
        """
        user_id = seed_user["user"].id
        older = _trust(user_id, _FIREFOX_LINUX)
        utc_day = datetime.now(timezone.utc).date() - timedelta(days=2)
        _move_trust_to(older, datetime.combine(
            utc_day, clock_time(3, 0), tzinfo=timezone.utc,
        ))
        _trust(user_id, _SAFARI_IPHONE)
        # The display days: one before each UTC day, by the arithmetic above.
        trusted_day = utc_day - timedelta(days=1)
        expires_day = utc_day + timedelta(days=30) - timedelta(days=1)

        page = auth_client.get(_SECURITY_PAGE).get_data(as_text=True)

        rows = [_text(row) for row in _rows(page)]
        assert len(rows) == 2
        assert rows[0].startswith("Safari on iPhone")
        assert rows[1].startswith("Firefox on Linux")
        assert (
            f"Trusted {trusted_day.strftime(_DAY_FORMAT)}; "
            f"expires {expires_day.strftime(_DAY_FORMAT)}"
        ) in rows[1]
        # This browser holds no device cookie, so neither row is marked.
        assert "This browser" not in _card(page)

    def test_the_browser_the_page_is_on_is_marked(self, app, seed_user):
        """The device this browser trusted is marked; the other one is not."""
        user_id = seed_user["user"].id
        browser = _firefox_that_trusted_itself(
            app, enable_mfa(db.session, user_id),
        )
        _trust(user_id, _SAFARI_IPHONE)

        page = browser.get(_SECURITY_PAGE).get_data(as_text=True)

        rows = [_text(row) for row in _rows(page)]
        assert len(rows) == 2
        assert rows[0].startswith("Safari on iPhone")
        assert "This browser" not in rows[0]
        assert rows[1].startswith("Firefox on Linux This browser")
        assert _card(page).count("This browser") == 1

    def test_only_the_users_live_devices_are_listed(
        self, auth_client, seed_user, seed_second_user,
    ):
        """An expired device and another user's are absent; the live one shows."""
        user_id = seed_user["user"].id
        _trust(user_id, _FIREFOX_LINUX)
        expired = _trust(user_id, _SAFARI_IPHONE)
        _move_trust_to(
            expired,
            datetime.now(timezone.utc) - timedelta(days=30, minutes=1),
        )
        _trust(seed_second_user["user"].id, _CHROME_WINDOWS)

        page = auth_client.get(_SECURITY_PAGE).get_data(as_text=True)

        rows = [_text(row) for row in _rows(page)]
        assert len(rows) == 1
        assert rows[0].startswith("Firefox on Linux")
        assert "Safari on iPhone" not in page
        assert "Chrome on Windows" not in page

    def test_no_device_with_two_factor_on_says_how_to_trust_one(
        self, auth_client, seed_user,
    ):
        """The empty state names the box that trusts a device."""
        enable_mfa(db.session, seed_user["user"].id)

        page = auth_client.get(_SECURITY_PAGE).get_data(as_text=True)

        assert _rows(page) == []
        assert (
            "No trusted devices. When you type a two-factor code at sign-in, "
            "tick \"Trust this device for 30 days\""
        ) in _text(page).replace("&quot;", '"')

    def test_no_device_with_two_factor_off_says_it_needs_it(self, auth_client):
        """Without two-factor no device can be trusted, and the card says so."""
        page = auth_client.get(_SECURITY_PAGE).get_data(as_text=True)

        assert _rows(page) == []
        assert (
            "No trusted devices. A device can be trusted once two-factor "
            "authentication is on."
        ) in _text(page)


class TestTheTrustLength:
    """The card states the trust length from its one constant."""

    def test_the_card_follows_the_constant(
        self, auth_client, seed_user, monkeypatch,
    ):
        """With the lifetime at 7 days, both sentences say 7 and none says 30.

        Two-factor is on, so the empty state that quotes the box's label is
        the one rendered beside the card's opening sentence.
        """
        enable_mfa(db.session, seed_user["user"].id)
        monkeypatch.setattr(
            "app.routes.settings.TRUST_LIFETIME", timedelta(days=7),
        )

        card = _text(_card(auth_client.get(_SECURITY_PAGE).get_data(as_text=True)))

        assert "A device stays trusted for 7 days" in card
        assert "&quot;Trust this device for 7 days&quot;" in card
        assert "30 days" not in card


class TestForgetting:
    """A user forgets their own device, and nobody else's."""

    def test_a_forgotten_device_asks_the_code_at_its_next_sign_in(
        self, app, seed_user,
    ):
        """Forget this browser from its own row; the next sign-in asks the code."""
        user_id = seed_user["user"].id
        browser = _firefox_that_trusted_itself(
            app, enable_mfa(db.session, user_id),
        )
        browser.post("/logout")
        # Control: before the forget, this browser skips the code.
        assert _signed_in(_password(browser))
        page = browser.get(_SECURITY_PAGE).get_data(as_text=True)
        (row,) = _rows(page)

        response = browser.post(_forget_action(row))

        assert response.status_code == 302
        assert response.headers["Location"].endswith(_SECURITY_PAGE)
        assert _device_ids_of(user_id) == []
        after = browser.get(_SECURITY_PAGE).get_data(as_text=True)
        assert "Device forgotten." in after
        assert _rows(after) == []
        browser.post("/logout")
        assert _asked_the_code(_password(browser))

    def test_another_users_device_and_a_missing_one_are_one_404(
        self, auth_client, seed_user, seed_second_user,
    ):
        """Both refused alike and nothing deleted; the user's own id still routes."""
        user_id = seed_user["user"].id
        mine = _trust(user_id, _FIREFOX_LINUX)
        theirs = _trust(seed_second_user["user"].id, _CHROME_WINDOWS)
        missing = max(mine, theirs) + 1

        theirs_response = auth_client.post(f"/trusted-devices/{theirs}/forget")
        missing_response = auth_client.post(f"/trusted-devices/{missing}/forget")

        assert theirs_response.status_code == 404
        assert missing_response.status_code == 404
        assert theirs_response.get_data() == missing_response.get_data()
        assert _device_ids_of(seed_second_user["user"].id) == [theirs]
        assert _device_ids_of(user_id) == [mine]
        # The pair: the same URL, with the user's own device, routes and forgets.
        mine_response = auth_client.post(f"/trusted-devices/{mine}/forget")
        assert mine_response.status_code == 302
        assert _device_ids_of(user_id) == []

    def test_the_forget_and_the_refusal_are_logged(
        self, auth_client, seed_user, seed_second_user, caplog,
    ):
        """``trusted_device_forgotten`` for a forget; the refusal's lookup event."""
        mine = _trust(seed_user["user"].id, _FIREFOX_LINUX)
        theirs = _trust(seed_second_user["user"].id, _CHROME_WINDOWS)

        with caplog.at_level(logging.INFO):
            auth_client.post(f"/trusted-devices/{theirs}/forget")
            auth_client.post(f"/trusted-devices/{mine}/forget")

        forgotten = [
            r for r in caplog.records
            if getattr(r, "event", None) == "trusted_device_forgotten"
        ]
        refused = [
            r for r in caplog.records
            if getattr(r, "event", None) == "resource_not_found"
            and getattr(r, "model", None) == "TrustedDevice"
        ]
        assert [r.device_id for r in forgotten] == [mine]
        assert [r.pk for r in refused] == [theirs]


class TestTheSessionsCard:
    """"Log out all other sessions" says it forgets every trusted device."""

    def test_the_card_and_its_message_say_so(self, auth_client):
        """The card's text before, and the message after."""
        page = auth_client.get(_SECURITY_PAGE).get_data(as_text=True)

        assert (
            "Log out of all other browser sessions and forget every trusted "
            "device, this one included."
        ) in _text(page)
        after = auth_client.post("/invalidate-sessions", follow_redirects=True)
        assert (
            "All other sessions have been logged out, and every trusted "
            "device forgotten."
        ) in _text(after.get_data(as_text=True))
