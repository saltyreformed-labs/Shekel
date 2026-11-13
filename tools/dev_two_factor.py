"""The dev copy's two-factor key: written onto a copy's account, typed by the dev-app tools.

Why this exists
---------------
The dev database is a copy of production.  The owner's two-factor secret there
(``auth.mfa_configs.totp_secret_encrypted``) is encrypted under production's
``TOTP_ENCRYPTION_KEY``, which dev does not hold, so the dev app cannot decrypt
it and ``/mfa/verify`` refuses every code.  Sessions once deleted the copy's
two-factor row and signed in with the password alone; once every account must
use two-factor (plan#3452), an account without it reaches only setup.  So the
copy's account gets a key of dev's own instead: one this module writes under
the dev app's ``TOTP_ENCRYPTION_KEY``, and whose current code the dev-app tools
type at ``/mfa/verify``, each through this module's one sign-in
(:func:`sign_in_with_dev_key`).

The key is the base32 secret in ``SHEKEL_DEV_TOTP_SECRET``, read from the
process environment, then from the checkout's ``.env`` (git-ignored, never
committed).  Make one with ``.venv/bin/python -c "import pyotp;
print(pyotp.random_base32())"``.

**Production's two-factor is never touched.**  The writer refuses any database
it does not reach on this host's loopback (:func:`refuse_unless_loopback`), and
production's database publishes no port to the host: it is reached only on its
own docker network (``/opt/docker/shekel/docker-compose.yml``), while the dev
database binds ``127.0.0.1`` (``docker-compose.dev.yml``).  ``tools/`` is also
outside the production image (``.dockerignore``), so the writer cannot run
there.

Usage::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_<copy> \\
        .venv/bin/python -m tools.dev_two_factor <email>

It builds the app under the development configuration, whose database is
``DATABASE_URL_APP`` when set, else ``DATABASE_URL`` (``app/config.py``).  Run
it with the same ``TOTP_ENCRYPTION_KEY`` the dev app runs with (the checkout's
``.env`` feeds both).  Run again, it writes the key again: the account's
two-factor is the dev key afterwards either way.
"""

from __future__ import annotations

import argparse
import binascii
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pyotp
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.engine import make_url

if TYPE_CHECKING:
    from playwright.sync_api import Page

#: The environment variable holding the dev key, a base32 TOTP secret.
SECRET_VARIABLE = "SHEKEL_DEV_TOTP_SECRET"

#: The checkout's ``.env``, the key's second source and the tools' credentials'.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

#: The hosts a database the writer may write is reached at: this host's
#: loopback, where the dev database binds and production's does not.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: The sign-in form, the code form a password leads to, and the prefix of every
#: two-factor page: a page under it (the code form, or the setup an account
#: without two-factor is held at) is not signed in yet, whatever the cookie says.
LOGIN_PATH = "/login"
CODE_PATH = "/mfa/verify"
TWO_FACTOR_PREFIX = "/mfa/"

#: The sign-in form's fields and button (``app/templates/auth/login.html``).
_EMAIL_FIELD = "#email"
_PASSWORD_FIELD = "#password"
_PASSWORD_SUBMIT = 'button[type="submit"]'

#: The code form's field and its own submit button on ``/mfa/verify``
#: (``app/templates/auth/mfa_verify.html``): the page holds a second form,
#: for a backup code, with a submit button of its own.
_CODE_FIELD = "#totp_code"
_CODE_SUBMIT = 'form:has(#totp_code) button[type="submit"]'


def read_env_file(keys: tuple[str, ...]) -> dict[str, str]:
    """Return the requested ``KEY=VALUE`` pairs found in the checkout's ``.env``.

    A minimal dependency-free parser: only ``KEY=VALUE`` lines are honoured,
    ``#`` comments and blanks are skipped, and surrounding quotes are stripped.
    Missing file or missing keys simply yield an absent entry.

    Args:
        keys: The env-var names to extract.

    Returns:
        A mapping of found key to value (only keys present in the file appear).
    """
    wanted = set(keys)
    found: dict[str, str] = {}
    if not ENV_FILE.exists():
        return found
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name in wanted:
            found[name] = value.strip().strip("'\"")
    return found


def dev_secret() -> str:
    """Return the dev key, from the process environment, then the checkout's ``.env``.

    Returns:
        The base32 TOTP secret.

    Raises:
        SystemExit: When neither source sets it, or it is not base32, saying
            how to make one.
    """
    secret = os.getenv(SECRET_VARIABLE) or read_env_file((SECRET_VARIABLE,)).get(
        SECRET_VARIABLE,
    )
    if not secret:
        raise SystemExit(
            f"{SECRET_VARIABLE} is not set in the environment or in {ENV_FILE}.  "
            "Make a key with: .venv/bin/python -c \"import pyotp; "
            "print(pyotp.random_base32())\", put it in .env as "
            f"{SECRET_VARIABLE}=<key>, then write it onto the copy's account with "
            "python -m tools.dev_two_factor <email>."
        )
    try:
        pyotp.TOTP(secret).byte_secret()
    except binascii.Error as exc:
        raise SystemExit(
            f"{SECRET_VARIABLE} is not a base32 secret ({exc}); make one with "
            ".venv/bin/python -c \"import pyotp; print(pyotp.random_base32())\"."
        ) from exc
    return secret


def type_dev_code(page: Page) -> None:
    """Type the dev key's current code into ``/mfa/verify`` and submit it.

    The one step the dev-app tools take at the code form, after the password
    form sent the browser there.  The browser is not trusted (the form's
    "Trust this device" stays clear), so each sign-in types a code.

    Args:
        page: A Playwright page showing ``/mfa/verify``.
    """
    page.fill(_CODE_FIELD, pyotp.TOTP(dev_secret()).now())
    page.click(_CODE_SUBMIT)


def signed_in(url: str) -> bool:
    """Return whether a settled *url* is past the sign-in doors.

    Not the sign-in form, and not a two-factor page: the code form a password
    leads to, or the setup an account without two-factor is held at.

    Args:
        url: The page's settled URL.

    Returns:
        True when the browser is on one of the account's pages.
    """
    path = urlsplit(url).path
    return path != LOGIN_PATH and not path.startswith(TWO_FACTOR_PREFIX)


def sign_in_with_dev_key(
    page: Page, base_url: str, email: str, password: str, timeout_ms: int,
) -> bool:
    """Sign *page* in to the dev app as *email*: the password, then the dev key's code.

    The one sign-in every dev-app tool performs (``tools/playwright/
    shekel_shot.py``, ``tests/manual/save_dev_session.py``,
    ``tests/manual/verify_anchor_ack.py``).  The password sends the browser to
    ``/mfa/verify``, where :func:`type_dev_code` types the code.  CSRF needs no
    handling: the browser submits each rendered form's own token.

    Args:
        page: An open Playwright page.
        base_url: The dev app's origin, e.g. ``http://127.0.0.1:5000``.
        email: The account's email.
        password: Its password.
        timeout_ms: How long each form's answer may take to settle.

    Returns:
        True when the browser reached the account's pages.  False when it
        stopped at the sign-in form, the code form or setup;
        :func:`why_not_signed_in` says which and its fix, from ``page.url``.
    """
    page.goto(base_url + LOGIN_PATH, wait_until="domcontentloaded")
    page.fill(_EMAIL_FIELD, email)
    page.fill(_PASSWORD_FIELD, password)
    page.click(_PASSWORD_SUBMIT)
    try:
        page.wait_for_url(
            lambda url: urlsplit(url).path != LOGIN_PATH, timeout=timeout_ms,
        )
        if urlsplit(page.url).path == CODE_PATH:
            type_dev_code(page)
            page.wait_for_url(signed_in, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False
    return signed_in(page.url)


def why_not_signed_in(url: str, email: str) -> str:
    """Return where a sign-in that did not finish stopped, why, and its fix.

    Args:
        url: The page's settled URL, one :func:`signed_in` says is not past
            the sign-in doors.
        email: The account's email, for the fix's command.

    Returns:
        One sentence naming the page and what to do.
    """
    path = urlsplit(url).path
    if path == LOGIN_PATH:
        return (
            f"It stopped at {path}: the email or password was refused; check "
            "the credentials."
        )
    fix = f"python -m tools.dev_two_factor {email}"
    if path == CODE_PATH:
        return (
            f"It stopped at {path}: the code was refused.  Either a sign-in in "
            "the same 30 seconds used it already (retry after them), or the "
            "copy's account does not have the dev key, as a production copy's "
            f"does not (its secret is under production's key): {fix}"
        )
    return (
        f"It stopped at {path}: the account has no two-factor, so it is held "
        f"at its setup: {fix}"
    )


def refuse_unless_loopback(database_uri: str) -> None:
    """Refuse a database this host does not reach on its loopback.

    Production's database publishes no port to the host (the module docstring
    says where each binds), so a database at a loopback address is never it.
    A unix socket, a docker network address or a remote host is refused, as
    nothing the writer is for lives there.

    Args:
        database_uri: The SQLAlchemy URI the writer would write through.

    Raises:
        SystemExit: When its host is not a loopback address, naming the host.
    """
    host = make_url(database_uri).host
    if host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"Refused: the database is at host {host!r}, not this host's loopback "
            f"({', '.join(sorted(LOOPBACK_HOSTS))}).  The dev key is written only "
            "onto a dev copy, which binds 127.0.0.1; production's database is "
            "never reached from here."
        )


def write_dev_key(email: str, secret: str) -> None:
    """Make *secret* the two-factor of the account *email*, on, and commit.

    Every piece of the account's earlier two-factor material is cleared first
    by the one clear rule (:func:`app.services.mfa_service.clear_mfa_material`):
    production's secret and backup codes, its replay step and any pending
    setup.  So the account's one way past ``/mfa/verify`` afterwards is a code
    of the dev key, and its first code is never refused as a replay of
    production's last step.  Call inside an app context.

    Args:
        email: The account's email.
        secret: The base32 dev key.

    Raises:
        SystemExit: When no account has *email*.
        RuntimeError: When ``TOTP_ENCRYPTION_KEY`` is not set
            (:func:`app.services.mfa_service.get_encryption_key`).
    """
    # Pylint: import-outside-toplevel -- importing anything under ``app``
    # executes ``app.config``, which loads ``.env`` and reads the environment
    # at import time; deferring to the write keeps the dev-app tools, which
    # import only the code typing above, free of the app.
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.models.user import MfaConfig, User
    from app.services import mfa_service
    # pylint: enable=import-outside-toplevel

    user = db.session.query(User).filter_by(email=email).one_or_none()
    if user is None:
        raise SystemExit(f"No account has the email {email!r} on this database.")
    mfa_config = db.session.query(MfaConfig).filter_by(user_id=user.id).one_or_none()
    if mfa_config is None:
        mfa_config = MfaConfig(user_id=user.id)
        db.session.add(mfa_config)
    mfa_service.clear_mfa_material(mfa_config)
    mfa_config.totp_secret_encrypted = mfa_service.encrypt_secret(secret)
    mfa_config.is_enabled = True
    mfa_config.confirmed_at = datetime.now(timezone.utc)
    db.session.commit()


def main(argv: list[str] | None = None) -> int:
    """Write the dev key onto one account of a dev copy.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 once written.
    """
    parser = argparse.ArgumentParser(
        description="Make the dev key (SHEKEL_DEV_TOTP_SECRET) the two-factor of "
        "one account on a dev copy of production.",
    )
    parser.add_argument("email", help="The account's email.")
    args = parser.parse_args(argv)
    secret = dev_secret()

    # Pylint: import-outside-toplevel -- the reason write_dev_key gives.
    # pylint: disable=import-outside-toplevel
    from app import create_app
    from app.config import DevConfig
    # pylint: enable=import-outside-toplevel

    # The development configuration always, whatever FLASK_ENV says: the key
    # is for a dev copy.  Its database is judged BEFORE the app is built,
    # because building it runs startup DDL against that database
    # (``app/__init__.py``, ``_ensure_schemas``).
    refuse_unless_loopback(DevConfig.SQLALCHEMY_DATABASE_URI)
    app = create_app("development")
    with app.app_context():
        write_dev_key(args.email, secret)
    print(f"{args.email}'s two-factor is the dev key now; its tools type the code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
