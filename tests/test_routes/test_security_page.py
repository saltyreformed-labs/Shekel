"""The Security page is every user's own, out of Settings (ruling plan#3435).

Plan#2407, task plan#3444.  Each user's own sign-in, an owner's and a
companion's alike, is one page at ``/security``, opened from the menu under
their name: password, two-factor, trusted devices and sessions.  Settings keeps
a link to it, and its old ``?section=security`` link redirects there.

**Every return path lands a companion on a page she can open.**  Until this
task each one ended on Settings > Security (or, for ``/reauth`` and the banner's
dismissal, the owner's dashboard), which ``@require_owner`` answers a companion
with 404: the backup-codes page's "I've Saved My Codes", the "was this you?"
banner's link, a password change (ruling plan#3437: she changes her own),
logging out her other sessions, forgetting a device, re-confirming her
password, and dismissing the banner with no referring page.

**The companion signs in through her shared sign-in, on a browser she trusts**
(:func:`_companion_with_two_factor`), which types no code, so the one code a
test types (a re-confirm) is the first her secret ever accepts: replay
prevention refuses a second code inside one thirty-second step.  Trusted
devices are made through the service, as a trusting sign-in would make them.
"""

import sqlalchemy

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import auth_service, mfa_service
from tests._companions import text_of
from tests._trusted_devices import SAFARI_IPHONE, is_trusted, trust
from tests._two_factor_sign_in import (
    SEED_COMPANION_PASSWORD, code_now, sign_in_seed_companion,
    sign_in_seed_companion_by_password, two_factor_secret_of,
)


#: The Security page's four cards, by the title each one shows.
_CARDS = (
    b"Change Password", b"Two-Factor Authentication", b"Trusted Devices",
    b"Log Out All Other Sessions",
)

#: Where a sign-in lands a companion: her own page.
_HER_PAGE = "/companion/"


def _companion_with_two_factor(app, companion_id):
    """Return a signed-in companion browser and her two-factor secret.

    Her shared sign-in types no code, so none has been typed for her secret
    yet (the module docstring says why).
    """
    return sign_in_seed_companion(app), two_factor_secret_of(companion_id)


def _lands_on_the_security_page(browser, response):
    """Assert *response* redirects to the Security page and that it opens."""
    assert response.status_code == 302, response.status_code
    assert response.headers["Location"] == "/security", (
        response.headers["Location"]
    )
    page = browser.get("/security")
    assert page.status_code == 200
    return page


class TestThePage:
    """The page renders for both roles, and both reach it from their menu."""

    def test_an_owner_sees_every_card(self, app, auth_client):
        """An owner's Security page holds the four cards Settings > Security held."""
        with app.app_context():
            response = auth_client.get("/security")

            assert response.status_code == 200
            assert b"<title>Security -- Shekel</title>" in response.data
            for card in _CARDS:
                assert card in response.data, card

    def test_a_companion_sees_every_card_her_password_included(
        self, app, seed_companion,
    ):
        """A companion's page holds the same four cards, Change Password too.

        Ruling plan#3437: she changes her own password here; the owner can
        still reset it from Companions.
        """
        with app.app_context():
            browser, _ = _companion_with_two_factor(
                app, seed_companion["user"].id,
            )

            response = browser.get("/security")

            assert response.status_code == 200
            for card in _CARDS:
                assert card in response.data, card

    def test_the_name_menu_links_it_for_an_owner(self, app, auth_client):
        """The menu under the owner's name has a Security item."""
        with app.app_context():
            response = auth_client.get("/dashboard")

            assert response.status_code == 200
            assert b'class="dropdown-item" href="/security"' in response.data

    def test_the_name_menu_links_it_for_a_companion(self, app, seed_companion):
        """The menu under a companion's name has a Security item, on her page."""
        with app.app_context():
            browser, _ = _companion_with_two_factor(
                app, seed_companion["user"].id,
            )

            response = browser.get(_HER_PAGE)

            assert response.status_code == 200
            assert b'class="dropdown-item" href="/security"' in response.data

    def test_settings_keeps_a_link_and_none_to_the_old_section(
        self, app, auth_client,
    ):
        """Settings links the page, and no link names the old section."""
        with app.app_context():
            response = auth_client.get("/settings")

            assert response.status_code == 200
            assert b'href="/security"' in response.data
            assert b"section=security" not in response.data

    def test_the_old_section_link_redirects_to_it(self, app, auth_client):
        """``/settings?section=security`` lands on the page, as old bookmarks do."""
        with app.app_context():
            response = auth_client.get("/settings?section=security")

            _lands_on_the_security_page(auth_client, response)


class TestWhatForgetsEveryDevice:
    """The Trusted Devices card names what forgets them for the reader's role.

    Plan#3521: a companion cannot turn two-factor off (ruling plan#3434), and
    the owner's reset forgets her devices (ruling plan#3441).
    """

    _OWNERS = (
        "Changing your password, logging out all other sessions, turning off "
        "two-factor authentication or signing in with a backup code forgets "
        "every device."
    )
    _COMPANIONS = (
        "Changing your password, logging out all other sessions or signing in "
        "with a backup code forgets every device, and so does the budget's "
        "owner resetting your two-factor authentication or setting you a new "
        "password."
    )

    def test_an_owner_is_told_turning_it_off_does(self, app, auth_client):
        """The owner's card names turning two-factor off, and no owner's reset."""
        with app.app_context():
            page = text_of(auth_client.get("/security").get_data(as_text=True))

            assert self._OWNERS in page
            assert self._COMPANIONS not in page

    def test_a_companion_is_told_the_owners_reset_does(self, app, seed_companion):
        """Her card names the owner's reset and password, and no turning it off."""
        with app.app_context():
            browser, _ = _companion_with_two_factor(app, seed_companion["user"].id)

            page = text_of(browser.get("/security").get_data(as_text=True))

            assert self._COMPANIONS in page
            assert "turning off two-factor" not in page


class TestEveryReturnPathLandsOnAPageSheCanOpen:
    """A companion's every way back from a security action opens for her."""

    def test_setting_up_two_factor_ends_on_her_security_page(
        self, app, seed_companion,
    ):
        """Setup's "I've Saved My Codes" and the "was this you?" banner link her page.

        She sets up two-factor on her own, and both links the flow leaves her
        with (the backup-codes page's button, and the banner the enrolment
        raises on her next page) name the Security page, which opens.
        """
        with app.app_context():
            companion_id = seed_companion["user"].id
            browser = sign_in_seed_companion_by_password(app)
            assert browser.get("/mfa/setup").status_code == 200
            config = db.session.execute(
                sqlalchemy.select(MfaConfig).where(MfaConfig.user_id == companion_id)
            ).scalar_one()
            secret = mfa_service.decrypt_secret(config.pending_secret_encrypted)

            codes_page = browser.post(
                "/mfa/confirm", data={"totp_code": code_now(secret)},
            )

            assert codes_page.status_code == 200
            assert b"Save Your Backup Codes" in codes_page.data
            assert b'href="/security" class="btn btn-primary"' in codes_page.data
            her_page = browser.get(_HER_PAGE)
            assert her_page.status_code == 200
            assert b"security-event-banner" in her_page.data
            assert b'class="alert-link ms-2" href="/security"' in her_page.data
            security = browser.get("/security")
            assert security.status_code == 200
            assert b"Enabled" in security.data

    def test_changing_her_password_lands_on_her_page_and_changes_it(
        self, app, seed_companion,
    ):
        """Her password change takes effect and lands on her page (ruling plan#3437)."""
        with app.app_context():
            companion_id = seed_companion["user"].id
            browser, _ = _companion_with_two_factor(app, companion_id)
            new_password = "a brand new companion passphrase"

            response = browser.post("/change-password", data={
                "current_password": SEED_COMPANION_PASSWORD,
                "new_password": new_password,
                "confirm_password": new_password,
            })

            page = _lands_on_the_security_page(browser, response)
            assert b"Password changed successfully." in page.data
            db.session.expire_all()
            companion = db.session.get(User, companion_id)
            assert auth_service.verify_password(
                new_password, companion.password_hash,
            )
            assert not auth_service.verify_password(
                SEED_COMPANION_PASSWORD, companion.password_hash,
            )

    def test_logging_out_her_other_sessions_lands_on_her_page(
        self, app, seed_companion,
    ):
        """"Log Out All Other Sessions" lands her back on her page."""
        with app.app_context():
            browser, _ = _companion_with_two_factor(
                app, seed_companion["user"].id,
            )

            response = browser.post("/invalidate-sessions")

            page = _lands_on_the_security_page(browser, response)
            assert b"All other sessions have been logged out" in page.data

    def test_forgetting_her_device_lands_on_her_page(self, app, seed_companion):
        """Forgetting one of her trusted devices clears it and lands on her page."""
        with app.app_context():
            companion_id = seed_companion["user"].id
            browser, _ = _companion_with_two_factor(app, companion_id)
            device_id = trust(companion_id, SAFARI_IPHONE)
            listed = browser.get("/security")
            assert f"/trusted-devices/{device_id}/forget".encode() in listed.data

            response = browser.post(f"/trusted-devices/{device_id}/forget")

            page = _lands_on_the_security_page(browser, response)
            assert b"Device forgotten." in page.data
            assert not is_trusted(device_id)

    def test_re_confirming_lands_her_on_her_own_page(self, app, seed_companion):
        """``/reauth`` lands a companion on her page, never the owner's dashboard."""
        with app.app_context():
            browser, secret = _companion_with_two_factor(
                app, seed_companion["user"].id,
            )

            response = browser.post("/reauth", data={
                "password": SEED_COMPANION_PASSWORD, "totp_code": code_now(secret),
            })

            assert response.status_code == 302
            assert response.headers["Location"] == _HER_PAGE
            assert browser.get(_HER_PAGE).status_code == 200

    def test_the_re_confirm_forms_cancel_goes_to_her_own_page(
        self, app, seed_companion,
    ):
        """The re-confirm form's Cancel link names her page for a companion."""
        with app.app_context():
            browser, _ = _companion_with_two_factor(
                app, seed_companion["user"].id,
            )

            response = browser.get("/reauth")

            assert response.status_code == 200
            assert f'href="{_HER_PAGE}" class="small text-muted"'.encode() in (
                response.data
            )

    def test_the_re_confirm_forms_cancel_goes_to_the_owners_dashboard(
        self, app, auth_client,
    ):
        """The same Cancel link names the dashboard for an owner, as it did."""
        with app.app_context():
            response = auth_client.get("/reauth")

            assert response.status_code == 200
            assert b'href="/dashboard" class="small text-muted"' in response.data

    def test_dismissing_the_banner_with_no_referer_lands_her_on_her_page(
        self, app, seed_companion,
    ):
        """A no-script dismissal with no referring page lands her on her page."""
        with app.app_context():
            browser, _ = _companion_with_two_factor(
                app, seed_companion["user"].id,
            )

            response = browser.post("/security-event/dismiss")

            assert response.status_code == 302
            assert response.headers["Location"] == _HER_PAGE

    def test_dismissing_the_banner_with_no_referer_lands_an_owner_on_the_dashboard(
        self, app, auth_client,
    ):
        """The same dismissal lands an owner on the dashboard, as it did."""
        with app.app_context():
            response = auth_client.post("/security-event/dismiss")

            assert response.status_code == 302
            assert response.headers["Location"] == "/dashboard"
