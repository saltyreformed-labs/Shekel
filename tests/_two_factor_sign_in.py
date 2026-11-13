"""The shared test sign-in, the seed companion's credentials, and what the two-factor tests type and read.

**Every shared test sign-in is** :func:`sign_in_from_a_trusted_browser`
(rulings plan#3621, plan#3623): the account has two-factor, its browser is one
it trusts, and the password signs it in with no code, as a person signs in on
their own browser every day.  A code is accepted once per thirty-second step
(``mfa_service.verify_totp_code``), so a sign-in that typed one would make
every code its test then types in that step a replay; this one types none.  A
test of the code form itself types codes, on a browser of its own.

Ruling plan#3434: every companion must use two-factor, and the login gate holds
a signed-in companion without it at setup (``app/login_gate.py``), so a browser
signed in with her password alone reaches no page of hers.  A test that needs
her on her pages signs her in through :func:`sign_in_seed_companion`, which
turns two-factor on through the role-neutral :func:`tests._test_helpers.enable_mfa`
and then signs a fresh browser in through the shared sign-in.
``seed_companion`` itself stays without two-factor: a companion her owner has
just made, which is what a test of the hold, or of her setup, starts from.

The one spelling of her credentials is here, and ``seed_companion`` writes
them.

**The one home of what the two-factor tests type and read** (plan#3517):
:func:`code_now`, the code to type for a secret, and its clock rule;
:func:`post_her_password`; :func:`redirect_path`;
:func:`two_factor_row_of`, a user's two-factor row read fresh; and
:func:`two_factor_secret_of`, the secret that row holds.

**An account without two-factor** is one a test makes itself (the companion
here), or a shared one whose two-factor :func:`reset_two_factor_of` took away
through the one reset act, as ``scripts/reset_mfa.py`` does.  Either signs in
with its password alone (:func:`sign_in_by_password`), as production signs in
an account before its setup.
"""

import time
from urllib.parse import urlsplit

import pyotp

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from tests._test_helpers import enable_mfa
from tests._trusted_devices import trust_browser

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


def two_factor_secret_of(user_id):
    """Return the authenticator secret *user_id*'s two-factor row holds.

    Read from the row, the one place it is stored, so a test types codes for
    the secret the app checks rather than a copy the fixture kept.

    Raises:
        AssertionError: *user_id* has no two-factor turned on.
    """
    row = two_factor_row_of(user_id)
    assert row is not None and row.is_enabled, (
        f"user {user_id} has no two-factor turned on, so it has no secret"
    )
    return mfa_service.decrypt_secret(row.totp_secret_encrypted)


def reset_two_factor_of(user_id):
    """Take *user_id*'s two-factor away through the one reset act, and commit.

    ``mfa_service.reset_two_factor``, what ``scripts/reset_mfa.py`` runs: the
    row is cleared and disabled, and every session and trusted device of the
    account ends.  So call it before signing the account in.

    Raises:
        AssertionError: *user_id* had no two-factor to reset.
    """
    assert mfa_service.reset_two_factor(db.session.get(User, user_id)), (
        f"user {user_id} had no two-factor to reset"
    )
    db.session.commit()


def sign_in_by_password(browser, email, password, lands_on):
    """Sign *browser* in with a password alone: the sign-in of an account without two-factor.

    Args:
        browser: A test client, which this signs in.
        email: The account's email.
        password: The account's password.
        lands_on: The path a finished sign-in sends the account to.

    Returns:
        *browser*, signed in.
    """
    response = browser.post("/login", data={"email": email, "password": password})
    assert redirect_path(response) == lands_on, (
        f"Setup failed: {email}'s password answered {response.status_code} to "
        f"{response.headers.get('Location')!r}, not {lands_on!r}"
    )
    return browser


def sign_in_from_a_trusted_browser(browser, email, password, lands_on, **request_arguments):
    """Sign *browser* in to the account *email* names, on a browser it trusts.

    THE shared test sign-in (rulings plan#3621, plan#3623).  The account must
    already have two-factor, as every account past setup does.  This trusts
    *browser* through the service, as a code sign-in that asked to be trusted
    leaves it (``tests._trusted_devices.trust_browser``), then posts the
    password, which ``/login`` then signs in with no code.  It types none, so
    every code of the account's current step is the test's to type.

    Call inside an app context.

    Args:
        browser: A test client, which this signs in.
        email: The account's email.
        password: The account's password.
        lands_on: The path a finished sign-in sends the account to.
        **request_arguments: Passed to the password's POST, as a test whose
            browser signs in from an address of its own passes them
            (``tests._limited_app.through_nginx``).

    Returns:
        *browser*, signed in.
    """
    user = db.session.query(User).filter_by(email=email).one()
    has_two_factor = db.session.query(
        db.session.query(MfaConfig)
        .filter_by(user_id=user.id, is_enabled=True)
        .exists()
    ).scalar()
    assert has_two_factor, (
        f"Setup failed: {email} has no two-factor, so no browser of theirs can "
        f"be trusted; give it two-factor where the account is made"
    )
    trust_browser(browser, user.id)
    response = browser.post(
        "/login", data={"email": email, "password": password}, **request_arguments,
    )
    assert redirect_path(response) == lands_on, (
        f"Setup failed: signing {email} in on a trusted browser answered "
        f"{response.status_code} to {response.headers.get('Location')!r}, "
        f"not {lands_on!r}"
    )
    return browser


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
    return sign_in_by_password(
        app.test_client(), SEED_COMPANION_EMAIL, SEED_COMPANION_PASSWORD, _HER_PAGE,
    )


def sign_in_seed_companion(app):
    """Turn two-factor on for the seed companion, then sign a browser in as her.

    Call inside an app context, after ``seed_companion`` has created her.  A
    second call in one test would fail at ``enable_mfa``: she has one
    two-factor row.  Her browser is one she trusts
    (:func:`sign_in_from_a_trusted_browser`), so her code is the test's to type
    (:func:`two_factor_secret_of`).

    Args:
        app: The Flask application.

    Returns:
        A fresh test client signed in as the seed companion, which reaches her
        pages.
    """
    companion = db.session.query(User).filter_by(email=SEED_COMPANION_EMAIL).one()
    enable_mfa(db.session, companion.id)
    return sign_in_from_a_trusted_browser(
        app.test_client(), SEED_COMPANION_EMAIL, SEED_COMPANION_PASSWORD, _HER_PAGE,
    )
