"""The setup page opens the authenticator app with the key filled in (ruling plan#3442).

Plan#2407, task plan#3449.  A companion setting up two-factor on her phone
cannot scan the QR code the phone itself shows, so ``/mfa/setup`` has an "Open
in my authenticator app" link to the ``otpauth://`` URI the QR code encodes:
the phone hands it to her authenticator app, which adds Shekel with the key
filled in.  The QR code and the manual key stay, for setting up from a
computer.  The route builds the URI once and hands it to both.
"""

import html
import re
import time
from urllib.parse import parse_qs, urlsplit

import pyotp

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from tests._two_factor_sign_in import (
    SEED_COMPANION_EMAIL, sign_in_seed_companion_by_password,
)

_SETUP = "/mfa/setup"


def _app_link(page):
    """Return the URL the page's "Open in my authenticator app" link goes to."""
    match = re.search(
        r'<a [^>]*href="([^"]+)"[^>]*>\s*<i [^>]*></i>\s*Open in my authenticator app',
        page,
    )
    assert match, "the setup page has no Open in my authenticator app link"
    return html.unescape(match.group(1))


def _qr_src(page):
    """Return the QR code image's ``src``, a PNG data URI."""
    return html.unescape(
        re.search(r'<img src="([^"]+)" alt="TOTP QR Code"', page).group(1),
    )


def test_the_link_is_the_qr_codes_uri_for_the_pending_secret_and_it_confirms(
    app, seed_companion,
):
    """The link's URI is the QR code's, for the pending secret; a code from it confirms.

    A companion, the user the button is for: held at setup, she opens it on
    her phone.  The QR code is compared by regenerating it from the link's
    URI, since the image is the URI's deterministic rendering.
    """
    companion_id = seed_companion["user"].id
    browser = sign_in_seed_companion_by_password(app)
    page = browser.get(_SETUP).get_data(as_text=True)

    link = _app_link(page)

    pending = mfa_service.decrypt_secret(
        db.session.query(MfaConfig).filter_by(user_id=companion_id).one()
        .pending_secret_encrypted,
    )
    assert link == mfa_service.get_totp_uri(pending, SEED_COMPANION_EMAIL)
    assert link.startswith("otpauth://totp/")
    assert _qr_src(page) == mfa_service.generate_qr_code_data_uri(link)
    assert f"<code>{pending}</code>" in page
    # What the app reads from the link: the key, and Shekel as the issuer.
    query = parse_qs(urlsplit(link).query)
    assert query["secret"] == [pending]
    assert query["issuer"] == ["Shekel"]

    confirmed = browser.post("/mfa/confirm", data={
        "totp_code": pyotp.TOTP(query["secret"][0]).at(int(time.time())),
    })

    assert confirmed.status_code == 200
    assert b"Save Your Backup Codes" in confirmed.data
    db.session.expire_all()
    assert db.session.query(MfaConfig).filter_by(
        user_id=companion_id,
    ).one().is_enabled is True
