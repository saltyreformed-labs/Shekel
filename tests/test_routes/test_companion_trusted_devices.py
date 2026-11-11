"""Companions shows each companion's two-factor and trusted devices, each forgettable.

Plan#2407, task plan#3446, ruling **plan#3439**: the owner sees each active
companion's trusted devices on Settings > Companions and can forget each, and
the row says whether her two-factor is set up.  Her devices are the rows the
Security page lists one's own as (``app/routes/_trusted_devices.py``), drawn by
the one macro (``auth/_trusted_device.html``), and the owner's Forget runs the
act her own Forget does: only the trust goes, and her browser stays known.

Another owner's companion, a user who is not a companion, a device of another
user and an id no device holds are one 404, each paired with a case proving
the URL still routes (a URL-map 404 would pass the same assertion while
guarding nothing).  A companion cannot reach the owner's Forget.

**One code per user per test**: replay prevention refuses a second code inside
its thirty-second step, so a test that signs her in through the code form does
so once, and makes any other device she trusted through the service
(:mod:`tests._trusted_devices`).
"""

import logging
import re
from datetime import datetime, time as clock_time, timedelta, timezone

import pytest
import sqlalchemy

from app.extensions import db
from app.models.known_device import KnownDevice
from tests._companions import (
    COMPANIONS_PAGE, another_owners_companion, companion_row,
    landed_on_companions, text_of,
)
from tests._csrf_tokens import without_csrf_tokens
from tests._test_helpers import enable_mfa
from tests._trusted_devices import (
    CHROME_WINDOWS, FIREFOX_LINUX, SAFARI_IPHONE, is_trusted, move_trust_to, trust,
)
from tests._two_factor_sign_in import code_now, post_her_password, redirect_path
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

#: The day format the page shows, the ``local_datetime`` filter's default,
#: spelled here as an oracle.
_DAY_FORMAT = "%b %-d, %Y"

#: What her row says while she has no two-factor.
_NOT_SET_UP = "Two-factor not set up"
_ASKED_NEXT_TIME = "They will be asked to set it up the next time they use Shekel."


def _forget_url(companion_id, device_id):
    """Return the owner's Forget URL for one of a companion's devices."""
    return f"/settings/companions/{companion_id}/devices/{device_id}/forget"


def _device_entries(row):
    """Return the HTML of each device listed in a companion's row, in order.

    Each entry is a device's summary and its Forget form, which the form's
    closing tag ends.
    """
    if "Trusted devices" not in row:
        return []
    listed = row.split("Trusted devices", 1)[1]
    return listed.split("</form>")[:-1]


def _forget_action(entry):
    """Return the URL an entry's Forget form posts to."""
    return re.search(r'action="([^"]+)"', entry).group(1)


def _her_firefox_that_trusted_itself(app, secret):
    """Return a Firefox browser she signed in on with a code and "Trust this device".

    The User-Agent is set on the client, so every request it makes sends the
    same one: strong session protection ends a session whose browser changes.
    """
    browser = app.test_client()
    browser.environ_base["HTTP_USER_AGENT"] = FIREFOX_LINUX
    assert redirect_path(post_her_password(browser)) == "/mfa/verify"
    response = browser.post("/mfa/verify", data={
        "totp_code": code_now(secret), "trust_device": "1",
    })
    assert redirect_path(response) == "/companion/", (
        f"Setup failed: her trusting sign-in answered {response.status_code} "
        f"to {response.headers.get('Location')!r}"
    )
    return browser


class TestHerTwoFactor:
    """Her row says whether her two-factor is set up."""

    @pytest.mark.usefixtures("seed_companion")
    def test_without_it_the_row_says_she_will_be_asked(self, auth_client):
        """Not set up, and when she sets it up; no device list."""
        row = text_of(companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True)))

        assert _NOT_SET_UP in row
        assert _ASKED_NEXT_TIME in row
        assert "Two-factor on" not in row
        assert "Trusted devices" not in row
        assert "No trusted devices." not in row

    def test_with_it_the_row_says_it_is_on(self, auth_client, seed_companion):
        """On, and no trusted devices yet."""
        enable_mfa(db.session, seed_companion["user"].id)

        row = text_of(companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True)))

        assert "Two-factor on" in row
        assert "No trusted devices." in row
        assert _NOT_SET_UP not in row
        assert _ASKED_NEXT_TIME not in row


class TestHerDevices:
    """Her live trusted devices, named, dated, and the owner's browser marked."""

    def test_each_device_shows_its_browser_and_its_two_days(
        self, auth_client, seed_companion,
    ):
        """Newest first; days in the display timezone, not UTC's.

        The older device is trusted at 03:00 UTC two days ago, which is the
        evening BEFORE on the display clock (22:00 or 23:00 Eastern), so a
        page that dated it by UTC would show the wrong day.  It expires
        thirty days later at 03:00 UTC: again the evening before, Eastern.
        """
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        older = trust(companion_id, FIREFOX_LINUX)
        utc_day = datetime.now(timezone.utc).date() - timedelta(days=2)
        move_trust_to(older, datetime.combine(
            utc_day, clock_time(3, 0), tzinfo=timezone.utc,
        ))
        newer = trust(companion_id, SAFARI_IPHONE)
        # The display days: one before each UTC day, by the arithmetic above.
        trusted_day = utc_day - timedelta(days=1)
        expires_day = utc_day + timedelta(days=30) - timedelta(days=1)

        row = companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True))

        entries = _device_entries(row)
        texts = [text_of(entry) for entry in entries]
        assert len(entries) == 2
        assert texts[0].startswith("Safari on iPhone")
        assert texts[1].startswith("Firefox on Linux")
        assert (
            f"Trusted {trusted_day.strftime(_DAY_FORMAT)}; "
            f"expires {expires_day.strftime(_DAY_FORMAT)}"
        ) in texts[1]
        assert [_forget_action(entry) for entry in entries] == [
            _forget_url(companion_id, newer), _forget_url(companion_id, older),
        ]
        # The owner's browser holds no cookie of hers, so neither is marked.
        assert "This browser" not in row
        assert "No trusted devices." not in row

    def test_only_her_live_devices_are_listed(
        self, auth_client, seed_user, seed_companion,
    ):
        """Her expired device and the owner's own are absent; her live one shows."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        trust(companion_id, FIREFOX_LINUX)
        expired = trust(companion_id, SAFARI_IPHONE)
        move_trust_to(
            expired,
            datetime.now(timezone.utc) - timedelta(days=30, minutes=1),
        )
        trust(seed_user["user"].id, CHROME_WINDOWS)

        page = auth_client.get(COMPANIONS_PAGE).get_data(as_text=True)

        texts = [text_of(entry) for entry in _device_entries(companion_row(page))]
        assert len(texts) == 1
        assert texts[0].startswith("Firefox on Linux")
        assert "Safari on iPhone" not in page
        assert "Chrome on Windows" not in page

    def test_the_owners_browser_is_marked_where_she_trusted_it(
        self, app, seed_companion,
    ):
        """She trusted the browser the owner then signs in on: that device is marked.

        The one builder judges "this browser" by the cookie the browser holds
        for her, so on the owner's page it says the owner's own browser is one
        she trusted.
        """
        companion_id = seed_companion["user"].id
        browser = _her_firefox_that_trusted_itself(
            app, enable_mfa(db.session, companion_id),
        )
        assert browser.post("/logout").status_code == 302
        owner = browser.post("/login", data={
            "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
        })
        assert redirect_path(owner) not in ("/login", "/mfa/verify"), owner.headers
        trust(companion_id, SAFARI_IPHONE)

        row = companion_row(browser.get(COMPANIONS_PAGE).get_data(as_text=True))

        texts = [text_of(entry) for entry in _device_entries(row)]
        assert len(texts) == 2
        assert texts[0].startswith("Safari on iPhone")
        assert "This browser" not in texts[0]
        assert texts[1].startswith("Firefox on Linux This browser")


class TestTheOwnerForgetsOne:
    """The owner's Forget clears that device's trust and nothing else."""

    def test_forget_clears_that_trust_and_no_other(
        self, auth_client, seed_user, seed_companion,
    ):
        """Her other device and the owner's own stay trusted; the row stays known."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        kept = trust(companion_id, FIREFOX_LINUX)
        forgotten = trust(companion_id, SAFARI_IPHONE)
        owners = trust(seed_user["user"].id, CHROME_WINDOWS)
        row = companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True))
        # What the page's own form posts to, not a hand-built URL.
        action = _forget_action(_device_entries(row)[0])
        assert action == _forget_url(companion_id, forgotten)

        response = auth_client.post(action, follow_redirects=True)

        page = landed_on_companions(response)
        assert (
            "Device forgotten. Signing in on it as Companion User will ask "
            "for the two-factor code again."
        ) in text_of(page)
        assert not is_trusted(forgotten)
        assert is_trusted(kept)
        assert is_trusted(owners)
        # Her browser stays known: only the trust went.
        assert db.session.execute(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(KnownDevice)
            .where(KnownDevice.id == forgotten)
        ).scalar_one() == 1
        texts = [text_of(entry) for entry in _device_entries(companion_row(page))]
        assert len(texts) == 1
        assert texts[0].startswith("Firefox on Linux")

    def test_her_next_sign_in_there_asks_her_code_again(
        self, app, auth_client, seed_companion,
    ):
        """Before the owner's Forget her password alone signs her in; after, the code.

        The first half is the pair: it proves the device was trusted, so the
        second half's code form is the Forget's doing.
        """
        companion_id = seed_companion["user"].id
        browser = _her_firefox_that_trusted_itself(
            app, enable_mfa(db.session, companion_id),
        )
        browser.post("/logout")
        assert redirect_path(post_her_password(browser)) == "/companion/"
        browser.post("/logout")
        row = companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True))
        (entry,) = _device_entries(row)

        assert auth_client.post(_forget_action(entry)).status_code == 302

        assert redirect_path(post_her_password(browser)) == "/mfa/verify"

    def test_the_forget_is_logged_with_who_pressed_it(
        self, auth_client, seed_user, seed_companion, caplog,
    ):
        """``trusted_device_forgotten``: the owner pressed it, the device was hers."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        device = trust(companion_id, SAFARI_IPHONE)

        with caplog.at_level(logging.INFO):
            auth_client.post(_forget_url(companion_id, device))

        (record,) = [
            r for r in caplog.records
            if getattr(r, "event", None) == "trusted_device_forgotten"
        ]
        assert record.user_id == seed_user["user"].id
        assert record.device_user_id == companion_id
        assert record.device_id == device


class TestTheRefusals:
    """Every door but the owner's own companion and her device is one 404."""

    def test_another_owners_companion_is_404(
        self, auth_client, seed_companion, seed_second_user,
    ):
        """Refused, and her trust kept; the owner's own companion's URL routes."""
        foreign = another_owners_companion(seed_second_user["user"].id)
        theirs = trust(foreign, SAFARI_IPHONE)
        companion_id = seed_companion["user"].id
        mine = trust(companion_id, FIREFOX_LINUX)

        response = auth_client.post(_forget_url(foreign, theirs))

        assert response.status_code == 404
        assert is_trusted(theirs)
        # The pair: the same URL, with the owner's own companion and her device.
        assert auth_client.post(_forget_url(companion_id, mine)).status_code == 302
        assert not is_trusted(mine)

    def test_a_user_who_is_not_a_companion_is_404(
        self, auth_client, seed_user, seed_companion,
    ):
        """The owner's own id names no companion: refused, the owner's trust kept."""
        owner_id = seed_user["user"].id
        owners = trust(owner_id, CHROME_WINDOWS)
        companion_id = seed_companion["user"].id
        hers = trust(companion_id, FIREFOX_LINUX)

        response = auth_client.post(_forget_url(owner_id, owners))

        assert response.status_code == 404
        assert is_trusted(owners)
        # The pair.
        assert auth_client.post(_forget_url(companion_id, hers)).status_code == 302
        assert not is_trusted(hers)

    def test_another_users_device_and_a_missing_one_are_one_404(
        self, auth_client, seed_user, seed_companion,
    ):
        """Both refused alike and no trust forgotten; her own device still routes.

        "Alike" is the two pages with their CSRF tokens blanked: each token is
        signed with the second it is minted in (finding plan#3244).
        """
        companion_id = seed_companion["user"].id
        hers = trust(companion_id, FIREFOX_LINUX)
        owners = trust(seed_user["user"].id, CHROME_WINDOWS)
        missing = max(hers, owners) + 1

        owners_response = auth_client.post(_forget_url(companion_id, owners))
        missing_response = auth_client.post(_forget_url(companion_id, missing))

        assert owners_response.status_code == 404
        assert missing_response.status_code == 404
        assert without_csrf_tokens(
            owners_response.get_data(as_text=True),
        ) == without_csrf_tokens(missing_response.get_data(as_text=True))
        assert is_trusted(owners)
        assert is_trusted(hers)
        # The pair: the same URL, with her own device, routes and forgets.
        assert auth_client.post(_forget_url(companion_id, hers)).status_code == 302
        assert not is_trusted(hers)

    def test_a_companion_cannot_reach_the_owners_forget(
        self, auth_client, companion_client, seed_companion,
    ):
        """Her own device by the owner's URL: 404 to her, a forget to the owner."""
        companion_id = seed_companion["user"].id
        hers = trust(companion_id, SAFARI_IPHONE)

        response = companion_client.post(_forget_url(companion_id, hers))

        assert response.status_code == 404
        assert is_trusted(hers)
        # The pair: the owner's browser, the same URL.
        assert auth_client.post(_forget_url(companion_id, hers)).status_code == 302
        assert not is_trusted(hers)
