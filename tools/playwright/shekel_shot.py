#!/usr/bin/env python3
"""Persistent Playwright auth + screenshot helper for the Shekel dev app.

Why this exists
---------------
The dev app deliberately issues a *non-permanent* session cookie
(``app/config.py``: sessions are left non-permanent so Flask-Login's
``strong`` protection keeps working) and runs Flask-Login with
``session_protection = "strong"`` (``app/extensions.py``).  Together that means:

* the session cookie has no ``Max-Age`` -- it lives only as long as the browser
  context is open, so every fresh Playwright context starts logged out; and
* the session is wiped on any User-Agent / source-IP drift.

The result is that naive screenshot automation re-authenticates on every run.
This helper captures an authenticated Playwright ``storage_state`` **once** and
reuses it across runs, so screenshots do not trigger a re-login each time.  The
reuse window is bounded by ``IDLE_TIMEOUT_MINUTES`` (dev: 7 days), and every
reuse refreshes that window, so a session used at least weekly never expires.

The saved state contains a live dev auth cookie, so it is written to
``tools/playwright/.auth/`` which is git-ignored -- it must never be committed.

Credentials
-----------
MFA is disabled on the dev users, so login is email + password only.  The email
and password are resolved, in order, from:

1. ``SHEKEL_DEV_LOGIN_EMAIL`` / ``SHEKEL_DEV_LOGIN_PASSWORD`` in the process env;
2. the same two keys in the repo-root ``.env``;
3. ``SEED_USER_EMAIL`` / ``SEED_USER_PASSWORD`` in the repo-root ``.env``.

If none resolve (or the password no longer matches the cloned dev user), fall
back to a one-time manual capture: ``capture --manual`` opens a real browser
window for you to log in by hand, then saves the state.

Usage
-----
    # One-time (or whenever the saved session goes stale):
    python tools/playwright/shekel_shot.py capture            # automated, headless
    python tools/playwright/shekel_shot.py capture --manual   # log in by hand

    # Take a screenshot (auto-refreshes a stale session when creds are present):
    python tools/playwright/shekel_shot.py shot /dashboard out.png
    python tools/playwright/shekel_shot.py shot /analytics out.png --full-page

    # Report whether a valid saved session exists:
    python tools/playwright/shekel_shot.py status

Run with the repo venv's Python so Playwright and its browsers resolve:
``.venv/bin/python tools/playwright/shekel_shot.py ...``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# ``tools/playwright/shekel_shot.py`` -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
STATE_PATH = Path(__file__).resolve().parent / ".auth" / "state.json"

DEFAULT_BASE_URL = "http://127.0.0.1:5000"
LOGIN_PATH = "/login"
# A page that requires authentication; an unauthenticated hit 302-redirects to
# ``/login?next=...`` so "/login" appearing in the settled URL means "logged
# out".
AUTHED_PROBE_PATH = "/"

# Navigation timeout for normal page loads and the automated-login redirect.
NAV_TIMEOUT_MS = 20_000
# How long a ``--manual`` capture waits for a human to finish logging in.
MANUAL_LOGIN_TIMEOUT_MS = 180_000
# Default screenshot viewport (matches a typical desktop breakpoint).
VIEWPORT = {"width": 1440, "height": 900}

# Pinned User-Agent, applied to EVERY context (capture and reuse alike).
# Flask-Login's ``strong`` session protection binds the session to
# ``sha512(remote_addr + User-Agent)`` and wipes it on any drift.  A headed
# capture browser reports ``Chrome/...`` while a headless reuse browser reports
# ``HeadlessChrome/...``; without pinning, that mismatch silently invalidates
# the session on the first reuse request.  Pinning one fixed string (the headed
# default this tool first captured with) keeps the identifier stable across
# headed/headless and across chromium upgrades.  The exact value only has to be
# self-consistent; this one is a realistic desktop Chrome UA.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


def _read_env_file(keys: tuple[str, ...]) -> dict[str, str]:
    """Return the requested ``KEY=VALUE`` pairs found in the repo-root ``.env``.

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


def _resolve_credentials() -> tuple[str | None, str | None, str]:
    """Resolve the dev login email/password and the source they came from.

    Resolution order is process env override, then ``.env`` override, then the
    ``.env`` seed user.  See the module docstring for the rationale.

    Returns:
        ``(email, password, source_label)``.  ``email``/``password`` are None
        when nothing resolved; ``source_label`` names where they came from (or
        "none").
    """
    env_email = os.getenv("SHEKEL_DEV_LOGIN_EMAIL")
    env_password = os.getenv("SHEKEL_DEV_LOGIN_PASSWORD")
    if env_email and env_password:
        return env_email, env_password, "process env (SHEKEL_DEV_LOGIN_*)"

    override = _read_env_file(("SHEKEL_DEV_LOGIN_EMAIL", "SHEKEL_DEV_LOGIN_PASSWORD"))
    if override.get("SHEKEL_DEV_LOGIN_EMAIL") and override.get("SHEKEL_DEV_LOGIN_PASSWORD"):
        return (
            override["SHEKEL_DEV_LOGIN_EMAIL"],
            override["SHEKEL_DEV_LOGIN_PASSWORD"],
            ".env (SHEKEL_DEV_LOGIN_*)",
        )

    seed = _read_env_file(("SEED_USER_EMAIL", "SEED_USER_PASSWORD"))
    if seed.get("SEED_USER_EMAIL") and seed.get("SEED_USER_PASSWORD"):
        return seed["SEED_USER_EMAIL"], seed["SEED_USER_PASSWORD"], ".env (SEED_USER_*)"

    return None, None, "none"


def _is_authenticated(page: Page, base_url: str) -> bool:
    """Return True if ``page`` can load an authenticated route without redirect.

    Navigates to the auth-only probe path and checks that the settled URL did
    not bounce to ``/login``.

    Args:
        page: An open Playwright page.
        base_url: The dev app origin, e.g. ``http://127.0.0.1:5000``.

    Returns:
        True when still authenticated, False when redirected to login.
    """
    page.goto(base_url + AUTHED_PROBE_PATH, wait_until="domcontentloaded")
    return LOGIN_PATH not in page.url


def _perform_login(page: Page, base_url: str, email: str, password: str) -> bool:
    """Fill and submit the login form, returning whether it authenticated.

    The form has no "Remember me" box since plan#2173 deleted it; the saved
    storage state carries the session cookie itself, which is what reuse rests
    on.  CSRF needs no special handling: the real browser submits the rendered form's
    hidden token with the matching session cookie.

    Args:
        page: An open Playwright page.
        base_url: The dev app origin.
        email: Login email.
        password: Login password.

    Returns:
        True when the submit redirected away from ``/login`` (success), False
        when it stayed on the login page (bad credentials or a form error).
    """
    page.goto(base_url + LOGIN_PATH, wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    try:
        page.wait_for_url(lambda url: LOGIN_PATH not in url, timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return False
    return True


def _save_state_after_auth(page: Page, context, base_url: str, source: str) -> None:
    """Verify authentication then persist the storage state, or raise.

    Args:
        page: The page used for the login/probe.
        context: The browser context whose cookies + storage are saved.
        base_url: The dev app origin.
        source: Human-readable description of how auth was obtained (for errors).

    Raises:
        SystemExit: If the context is not actually authenticated after login.
    """
    if not _is_authenticated(page, base_url):
        raise SystemExit(
            f"Login via {source} did not yield an authenticated session; "
            "state not saved."
        )
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(STATE_PATH))


def capture(base_url: str, manual: bool, headed: bool) -> Path:
    """Obtain a fresh authenticated storage state and write it to disk.

    In automated mode (default) logs in headlessly with resolved credentials.
    In ``--manual`` mode opens a visible browser and waits for a human to log
    in.  Automated mode with no resolvable credentials is a hard error that
    points at ``--manual``.

    Args:
        base_url: The dev app origin.
        manual: When True, wait for a human login in a visible browser.
        headed: When True (and not manual), run the automated login visibly.

    Returns:
        The path the storage state was written to.

    Raises:
        SystemExit: On missing credentials (automated mode) or a failed login.
    """
    email, password, source = _resolve_credentials()
    if not manual and not (email and password):
        raise SystemExit(
            "No dev credentials resolved (checked SHEKEL_DEV_LOGIN_* in env "
            "and .env, then SEED_USER_* in .env).  Either set "
            "SHEKEL_DEV_LOGIN_EMAIL / SHEKEL_DEV_LOGIN_PASSWORD in .env, or run "
            "with --manual to log in by hand."
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not (manual or headed))
        context = browser.new_context(viewport=VIEWPORT, user_agent=USER_AGENT)
        page = context.new_page()
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        try:
            if manual:
                page.goto(base_url + LOGIN_PATH, wait_until="domcontentloaded")
                print(
                    "A browser window is open. Log in there; waiting up to "
                    f"{MANUAL_LOGIN_TIMEOUT_MS // 1000}s...",
                    file=sys.stderr,
                )
                page.wait_for_url(
                    lambda url: LOGIN_PATH not in url,
                    timeout=MANUAL_LOGIN_TIMEOUT_MS,
                )
                _save_state_after_auth(page, context, base_url, "manual login")
            else:
                if not _perform_login(page, base_url, email, password):
                    raise SystemExit(
                        f"Automated login failed for {email} (source: {source}). "
                        "The saved dev password may be stale -- set "
                        "SHEKEL_DEV_LOGIN_PASSWORD in .env, or use --manual."
                    )
                _save_state_after_auth(page, context, base_url, f"login as {email}")
        finally:
            context.close()
            browser.close()

    print(f"Saved authenticated session to {STATE_PATH}", file=sys.stderr)
    return STATE_PATH


def status(base_url: str) -> bool:
    """Print and return whether a valid saved session exists.

    Args:
        base_url: The dev app origin.

    Returns:
        True when ``STATE_PATH`` exists and still authenticates.
    """
    if not STATE_PATH.exists():
        print(f"No saved session at {STATE_PATH}", file=sys.stderr)
        return False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STATE_PATH), viewport=VIEWPORT, user_agent=USER_AGENT
        )
        page = context.new_page()
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        try:
            valid = _is_authenticated(page, base_url)
        finally:
            context.close()
            browser.close()
    email, _, source = _resolve_credentials()
    creds = f"{email} ({source})" if email else "none resolved"
    print(
        f"Saved session at {STATE_PATH} is "
        f"{'VALID (authenticated)' if valid else 'STALE (redirects to login)'}. "
        f"Auto-refresh credentials: {creds}.",
        file=sys.stderr,
    )
    return valid


def _screenshot_once(playwright, target: str, out_path: Path, full_page: bool) -> bool:
    """Load ``target`` with the saved state and screenshot it, unless logged out.

    Args:
        playwright: An active ``sync_playwright`` instance.
        target: Absolute URL to screenshot.
        out_path: Where to write the PNG.
        full_page: Whether to capture the full scrollable page.

    Returns:
        True if the screenshot was taken; False if the saved state was stale
        (the target redirected to ``/login``), in which case nothing is written.
    """
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=str(STATE_PATH), viewport=VIEWPORT, user_agent=USER_AGENT
    )
    page = context.new_page()
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    try:
        try:
            page.goto(target, wait_until="networkidle")
        except PlaywrightTimeoutError:
            # HTMX polling can keep the network busy; the DOM is loaded enough
            # to shoot, so fall through rather than fail the screenshot.
            pass
        if LOGIN_PATH in page.url:
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out_path), full_page=full_page)
        return True
    finally:
        context.close()
        browser.close()


def shot(url: str, out: str, base_url: str, full_page: bool) -> Path:
    """Screenshot a dev-app path, reusing (and auto-refreshing) the saved state.

    If no saved state exists, or the existing one is stale, it is (re)captured
    first -- automatically when credentials resolve, otherwise a hard error
    pointing at ``capture --manual``.

    Args:
        url: A path (``/dashboard``) or absolute URL to screenshot.
        out: Output PNG path.
        base_url: The dev app origin.
        full_page: Whether to capture the full scrollable page.

    Returns:
        The output path written.

    Raises:
        SystemExit: If a fresh session cannot be obtained without manual login.
    """
    if not STATE_PATH.exists():
        capture(base_url, manual=False, headed=False)

    target = url if url.startswith("http") else base_url + url
    out_path = Path(out)

    with sync_playwright() as playwright:
        if _screenshot_once(playwright, target, out_path, full_page):
            print(f"Wrote {out_path}", file=sys.stderr)
            return out_path

    # Saved state was stale: refresh (auto-login) and retry exactly once.
    print("Saved session was stale; refreshing...", file=sys.stderr)
    capture(base_url, manual=False, headed=False)
    with sync_playwright() as playwright:
        if _screenshot_once(playwright, target, out_path, full_page):
            print(f"Wrote {out_path}", file=sys.stderr)
            return out_path

    raise SystemExit(
        "Still unauthenticated after a refresh. Run "
        "'python tools/playwright/shekel_shot.py capture --manual' and retry."
    )


def _build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Persistent Playwright auth + screenshot helper for the "
        "Shekel dev app.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_BASE_URL,
        help=f"Dev app origin (default: {DEFAULT_BASE_URL}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="Log in and save an authenticated session.")
    cap.add_argument(
        "--manual",
        action="store_true",
        help="Open a visible browser and wait for a human to log in.",
    )
    cap.add_argument(
        "--headed",
        action="store_true",
        help="Run the automated login visibly (debugging).",
    )

    shot_parser = sub.add_parser("shot", help="Screenshot a path using the saved session.")
    shot_parser.add_argument("target", help="Path (/dashboard) or absolute URL.")
    shot_parser.add_argument("out", help="Output PNG path.")
    shot_parser.add_argument(
        "--full-page",
        action="store_true",
        help="Capture the full scrollable page, not just the viewport.",
    )

    sub.add_parser("status", help="Report whether a valid saved session exists.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 success, 1 on a handled failure).
    """
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "capture":
            capture(args.url, manual=args.manual, headed=args.headed)
        elif args.command == "shot":
            shot(args.target, args.out, args.url, full_page=args.full_page)
        elif args.command == "status":
            return 0 if status(args.url) else 1
    except PlaywrightError as exc:
        print(f"Playwright error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
