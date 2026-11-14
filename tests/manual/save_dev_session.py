"""Save a logged-in Playwright session for the dev Flask server.

Run this once before invoking ``verify_mobile_grid_commit6.py`` (or any
future mobile verification script).  The script prompts for the dev
user's password via ``getpass`` -- the value is read directly from the
TTY, never echoed, never stored in shell history or this script's
output.  It drives a headless Chromium against the local dev Flask
server (``http://172.32.0.1:5000``, the shekel-frontend bridge
gateway) to perform the login, then writes Playwright's
``storage_state`` -- cookies + localStorage -- to
``tests/manual/.dev_session_state.json``.

Every account signs in with its password and then a two-factor code
(plan#3452).  The dev copy's account has the dev key as its two-factor,
written by ``tools/dev_two_factor.py``, and this script signs in through that
module's one sign-in, which types that key's current code at ``/mfa/verify``
(``SHEKEL_DEV_TOTP_SECRET``, that module's docstring).

Subsequent verification scripts launch Playwright with
``storage_state=<that file>`` and skip the login flow entirely.  The
file is gitignored (see .gitignore "Manual verification harness").

Usage::

    .venv/bin/python tests/manual/save_dev_session.py
    # Email [josh@saltyreformed.com]: <enter or override>
    # Password: <typed silently>
    # Saved session to tests/manual/.dev_session_state.json

Re-run whenever the saved cookie expires (Flask-Login's session
default is ~31 days; whichever Shekel uses) or after any logout.
"""

from __future__ import annotations

import getpass
import pathlib
import sys
from playwright.sync_api import sync_playwright


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so the repository root is added explicitly -- the same line every
# harness in this directory carries.
sys.path.insert(0, str(REPO_ROOT))

# Pylint: ``wrong-import-position`` -- this import must FOLLOW the ``sys.path``
# line above, which is what makes this checkout's ``tools`` the one imported.
# pylint: disable=wrong-import-position
from tools.dev_two_factor import (  # noqa: E402
    sign_in_with_dev_key, why_not_signed_in,
)
# pylint: enable=wrong-import-position

STATE_FILE = REPO_ROOT / "tests" / "manual" / ".dev_session_state.json"
DEV_BASE_URL = "http://172.32.0.1:5000"
DEFAULT_EMAIL = "josh@saltyreformed.com"
#: How long each sign-in form's answer may take to settle.
SIGN_IN_TIMEOUT_MS = 10_000


def main() -> int:
    """Drive headless Chromium through the login form and persist state."""
    email_prompt = f"Email [{DEFAULT_EMAIL}]: "
    email = input(email_prompt).strip() or DEFAULT_EMAIL
    password = getpass.getpass("Password: ")
    if not password:
        print("Password is empty; aborting.", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        if not sign_in_with_dev_key(
            page, DEV_BASE_URL, email, password, SIGN_IN_TIMEOUT_MS,
        ):
            # The page's body follows, for a form error the sentence does
            # not name.
            print(
                f"Login did not finish.  {why_not_signed_in(page.url, email)}  "
                "The response:",
                file=sys.stderr,
            )
            print(page.content()[:2000], file=sys.stderr)
            browser.close()
            return 1

        # Hit /grid once so any post-login redirect chain settles
        # and the storage_state captures everything the verification
        # script will need.
        page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
        assert page.url.endswith("/grid"), (
            f"Expected to land on /grid, got {page.url}."
        )

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(STATE_FILE))
        browser.close()

    print(f"Saved session to {STATE_FILE}")
    print(
        "Next: .venv/bin/python tests/manual/verify_mobile_grid_commit6.py",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
