"""The seed companion signed in as she now must: her password, then her code.

Ruling plan#3434: every companion must use two-factor, and the login gate holds
a signed-in companion without it at setup (``app/login_gate.py``), so a browser
signed in with her password alone reaches no page of hers.  A test that needs
her on her pages signs her in through :func:`sign_in_seed_companion`, which
turns two-factor on through the role-neutral :func:`tests._test_helpers.enable_mfa`
and then signs a fresh browser in with her password and a code, as the login
form and ``/mfa/verify`` take them.  ``seed_companion`` itself stays without
two-factor: a companion just created, which is what a test of the hold, or of
her setup, starts from.

The one spelling of her credentials is here, and ``seed_companion`` writes
them.

**Replay prevention refuses a second code for one secret inside a 30-second
step** (``mfa_service.verify_totp_code``), so a test signed in this way types
no second code for her; a test that must, enrols her itself after a password
sign-in and types its one code then (``tests/test_routes/test_security_page.py``).

**The one home of what the two-factor tests type and read** (plan#3517):
:func:`code_now`, the code to type for a secret, and its clock rule;
:func:`post_her_password`; :func:`redirect_path`; and
:func:`two_factor_row_of`, a user's two-factor row read fresh.
"""

import time
from urllib.parse import urlsplit

import pyotp

from app.extensions import db
from app.models.user import MfaConfig, User
from tests._test_helpers import enable_mfa

SEED_COMPANION_EMAIL = "companion@shekel.local"
SEED_COMPANION_PASSWORD = "companionpass"

#: Where a finished sign-in lands a companion: her own page.
_HER_PAGE = "/companion/"


def code_now(secret):
    """Return the authenticator code for *secret* at the verifier's instant.

    The instant ``time.time()`` reads, which is the clock the verifier steps
    by: ``pyotp``'s ``now()`` reads ``datetime`` instead, which a test that
    fakes the calendar moves (measured 2026-11-11: nine ``test_entries`` cases
    signed in with a code their fake day refused).
    """
    return pyotp.TOTP(secret).at(int(time.time()))


def redirect_path(response):
    """Return the path a redirect sends the browser to, or ``""``."""
    return urlsplit(response.headers.get("Location", "")).path


def two_factor_row_of(user_id):
    """Return *user_id*'s ``auth.mfa_configs`` row, read fresh, or ``None``."""
    db.session.expire_all()
    return db.session.query(MfaConfig).filter_by(user_id=user_id).one_or_none()


def post_her_password(browser):
    """POST the seed companion's correct password to the sign-in form.

    Returns:
        The sign-in form's response, its redirect for the caller to read.
    """
    return browser.post("/login", data={
        "email": SEED_COMPANION_EMAIL, "password": SEED_COMPANION_PASSWORD,
    })


def sign_in_seed_companion_by_password(app):
    """Sign a fresh browser in as the seed companion with her password alone.

    The sign-in a companion without two-factor finishes: the login form signs
    her in and lands her on her page, and the login gate then holds her at
    setup.  So the browser this returns reaches setup and sign-out only,
    until a test gives her two-factor (``tests._test_helpers.enable_mfa``) or
    she confirms setup.  Call inside an app context, after ``seed_companion``.

    Args:
        app: The Flask application.

    Returns:
        A fresh test client signed in as the seed companion.
    """
    browser = app.test_client()
    response = post_her_password(browser)
    assert redirect_path(response) == _HER_PAGE, (
        f"Setup failed: her password answered {response.status_code} to "
        f"{response.headers.get('Location')!r}, not her page"
    )
    return browser


def sign_in_seed_companion(app):
    """Turn two-factor on for the seed companion, then sign a browser in with it.

    Call inside an app context, after ``seed_companion`` has created her.  A
    second call in one test would fail at ``enable_mfa``: she has one
    two-factor row.

    Args:
        app: The Flask application.

    Returns:
        A fresh test client signed in as the seed companion, which reaches her
        pages.
    """
    companion = db.session.query(User).filter_by(email=SEED_COMPANION_EMAIL).one()
    secret = enable_mfa(db.session, companion.id)
    browser = app.test_client()

    password = post_her_password(browser)
    assert redirect_path(password) == "/mfa/verify", (
        f"Setup failed: her password answered {password.status_code} to "
        f"{password.headers.get('Location')!r}, not the code form"
    )
    code = browser.post("/mfa/verify", data={"totp_code": code_now(secret)})
    assert redirect_path(code) == _HER_PAGE, (
        f"Setup failed: her code answered {code.status_code} to "
        f"{code.headers.get('Location')!r}, not her page"
    )
    return browser
