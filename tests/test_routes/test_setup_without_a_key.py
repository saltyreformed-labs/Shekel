"""With no TOTP key, setup says so in place instead of looping a held user.

Plan#2407, task plan#3514.  With ``TOTP_ENCRYPTION_KEY`` unset the app boots
with a warning (``app/__init__.py``) and no two-factor key can be made or read.
Setup and its confirmation once answered that by redirecting to the Security
page, which the login gate refuses a user it holds at setup, so it sent her
straight back: her page, setup, Security, setup, round and round, the
operator's flash never shown and the flashes piling up in her cookie.  The
hold was a companion's then; it is every account's since plan#3452, so an
owner without two-factor is held there too.

Now ``/mfa/setup`` says so in place, a 200 with no key, QR code or form, for
every user, and each key failure at ``/mfa/confirm`` lands on it.  With the key
set, setup is what it was.
"""

from urllib.parse import urlsplit

import pytest

from app.services import mfa_service
from tests._seed_owner_fixtures import OWNERS_PAGE
from tests.test_routes.test_auth_required import spy_on_the_hold
from tests._two_factor_sign_in import (
    code_now, sign_in_seed_companion_by_password, two_factor_row_of,
)

_SETUP = "/mfa/setup"
_CONFIRM = "/mfa/confirm"
_HER_PAGE = "/companion/"
_HER_ENDPOINT = "companion.index"

#: The operator's message, as the setup page shows it, whitespace collapsed.
_KEY_MISSING = (
    "MFA is not available. The server administrator must set "
    "TOTP_ENCRYPTION_KEY before MFA can be enabled."
)

#: The setup page's sentence for a user held there (the "why she is here").
_WHY_HELD = "Your account requires two-factor authentication. Set it up here to"

#: What only a page that can set two-factor up carries.
_QR_CODE = 'alt="TOTP QR Code"'
_APP_LINK = "Open in my authenticator app"
_CONFIRM_FORM = 'action="/mfa/confirm"'


@pytest.fixture(name="holds")
def _holds(monkeypatch):
    """Record ``(method, path, endpoint)`` for each request the hold ends.

    Named by ``name=`` so a test's ``holds`` argument shadows no module name.

    Yields:
        The spy's live list (:func:`spy_on_the_hold`).
    """
    yield spy_on_the_hold(monkeypatch)


def _unset_the_key(monkeypatch):
    """Remove ``TOTP_ENCRYPTION_KEY``, as an app booted without it runs.

    Raises ``KeyError`` when it is already unset, so a test never claims to
    remove a key the suite did not hold (``tests/conftest.py`` writes it
    before the app is imported).
    """
    monkeypatch.delenv("TOTP_ENCRYPTION_KEY")


def _page(response):
    """Return *response*'s HTML with its whitespace collapsed."""
    return " ".join(response.get_data(as_text=True).split())


def _assert_says_the_key_is_missing(response):
    """Assert *response* is setup's in-place message: a 200, no way to set it up.

    Returns:
        The page, its whitespace collapsed.
    """
    assert response.status_code == 200, response.status_code
    assert "Location" not in response.headers
    page = _page(response)
    assert _KEY_MISSING in page
    assert _QR_CODE not in page
    assert _APP_LINK not in page
    assert _CONFIRM_FORM not in page
    return page


def _assert_no_flashes(browser):
    """Assert the browser's session holds no flashed message."""
    with browser.session_transaction() as session:
        assert "_flashes" not in session, session.get("_flashes")


class TestWithTheKeyUnset:
    """Setup says so in place, and nothing sends a held user round a loop."""

    def test_a_held_companion_gets_setups_message_in_place(
        self, app, seed_companion, monkeypatch, holds,
    ):
        """Her setup is a 200 saying why she is here and that the key is missing.

        Nothing is written: no key could be made, so no row is staged.
        """
        browser = sign_in_seed_companion_by_password(app)
        _unset_the_key(monkeypatch)

        response = browser.get(_SETUP)

        assert _WHY_HELD in _assert_says_the_key_is_missing(response)
        assert holds == []
        assert two_factor_row_of(seed_companion["user"].id) is None
        _assert_no_flashes(browser)

    @pytest.mark.usefixtures("seed_companion")
    def test_her_page_sends_her_to_setup_once_and_it_stops_there(
        self, app, monkeypatch, holds,
    ):
        """Her page is one hop to setup, which answers in place: no loop, no flash."""
        browser = sign_in_seed_companion_by_password(app)
        _unset_the_key(monkeypatch)

        response = browser.get(_HER_PAGE, follow_redirects=True)

        assert [
            urlsplit(hop.headers["Location"]).path for hop in response.history
        ] == [_SETUP]
        assert response.request.path == _SETUP
        _assert_says_the_key_is_missing(response)
        assert holds == [("GET", _HER_PAGE, _HER_ENDPOINT)]
        _assert_no_flashes(browser)

    def test_an_owners_page_sends_him_to_setup_once_which_says_the_same(
        self, no_two_factor_client, seed_user, monkeypatch, holds,
    ):
        """An owner without two-factor is held too (plan#3452): one hop, the same answer.

        His dashboard sends him to setup, which says why he is there and that
        the key is missing, in place: no loop, no flash.  Nothing is written:
        his row stays as the reset left it, disabled and holding no key.
        """
        _unset_the_key(monkeypatch)

        response = no_two_factor_client.get(OWNERS_PAGE, follow_redirects=True)

        assert [
            urlsplit(hop.headers["Location"]).path for hop in response.history
        ] == [_SETUP]
        assert _WHY_HELD in _assert_says_the_key_is_missing(response)
        assert holds == [("GET", OWNERS_PAGE, "dashboard.page")]
        row = two_factor_row_of(seed_user["user"].id)
        assert row.is_enabled is False
        assert not mfa_service.has_mfa_material(row)
        _assert_no_flashes(no_two_factor_client)

    def test_confirm_with_the_key_gone_lands_on_setup_which_says_so(
        self, app, seed_companion, monkeypatch, holds,
    ):
        """The key unset between setup and confirm: confirm clears and sends her to setup.

        The pending secret it can no longer read is cleared, two-factor stays
        off, and the one redirect lands on setup's in-place message.
        """
        companion_id = seed_companion["user"].id
        browser = sign_in_seed_companion_by_password(app)
        assert _QR_CODE in _page(browser.get(_SETUP))
        secret = mfa_service.decrypt_secret(
            two_factor_row_of(companion_id).pending_secret_encrypted,
        )
        _unset_the_key(monkeypatch)

        response = browser.post(_CONFIRM, data={"totp_code": code_now(secret)})

        assert response.status_code == 302, response.status_code
        assert urlsplit(response.headers["Location"]).path == _SETUP
        config = two_factor_row_of(companion_id)
        assert config.pending_secret_encrypted is None
        assert config.pending_secret_expires_at is None
        assert config.is_enabled is False
        assert config.totp_secret_encrypted is None
        _assert_says_the_key_is_missing(browser.get(_SETUP))
        assert holds == []
        _assert_no_flashes(browser)

    def test_confirm_whose_promotion_cannot_encrypt_lands_on_setup(
        self, app, seed_companion, monkeypatch, holds,
    ):
        """The key unset between confirm's decrypt and its re-encrypt: setup says so.

        The code is right and the decrypt worked, then the key goes before
        the secret is re-encrypted for keeps: two-factor stays off, and the one
        redirect lands on setup's in-place message.
        """
        companion_id = seed_companion["user"].id
        browser = sign_in_seed_companion_by_password(app)
        browser.get(_SETUP)
        secret = mfa_service.decrypt_secret(
            two_factor_row_of(companion_id).pending_secret_encrypted,
        )
        real_encrypt = mfa_service.encrypt_secret

        def encrypt_after_the_key_goes(plaintext):
            """Unset the key, then encrypt as the service does: it raises.

            Setup's own encrypt, on the load after confirm, comes here too,
            the key already gone.
            """
            monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
            return real_encrypt(plaintext)

        monkeypatch.setattr(mfa_service, "encrypt_secret", encrypt_after_the_key_goes)

        response = browser.post(_CONFIRM, data={"totp_code": code_now(secret)})

        assert response.status_code == 302, response.status_code
        assert urlsplit(response.headers["Location"]).path == _SETUP
        config = two_factor_row_of(companion_id)
        assert config.is_enabled is False
        assert config.totp_secret_encrypted is None
        _assert_says_the_key_is_missing(browser.get(_SETUP))
        assert holds == []
        _assert_no_flashes(browser)


class TestWithTheKeySet:
    """Setup is unchanged: the app link, the QR code and the form, no message."""

    @pytest.mark.usefixtures("seed_companion")
    def test_a_held_companions_setup_can_set_it_up(self, app, holds):
        """Her setup carries every way in and says nothing of a missing key."""
        browser = sign_in_seed_companion_by_password(app)

        response = browser.get(_SETUP)

        assert response.status_code == 200, response.status_code
        page = _page(response)
        assert _WHY_HELD in page
        assert _APP_LINK in page
        assert _QR_CODE in page
        assert _CONFIRM_FORM in page
        assert _KEY_MISSING not in page
        assert holds == []

    def test_an_owners_setup_can_set_it_up(self, no_two_factor_client):
        """An owner's setup, two-factor reset, says why and carries every way in."""
        response = no_two_factor_client.get(_SETUP)

        assert response.status_code == 200, response.status_code
        page = _page(response)
        assert _WHY_HELD in page
        assert _APP_LINK in page
        assert _QR_CODE in page
        assert _CONFIRM_FORM in page
        assert _KEY_MISSING not in page
