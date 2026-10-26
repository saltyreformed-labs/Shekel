"""Headless mobile verification for Commit 6 of the v3 plan.

Drives Playwright Chromium at 375x812 (iPhone XS portrait) against the
local dev Flask server and asserts the structural / interactive
behaviour of the new "This Period" tab and partial.  This is the
runtime counterpart to the static-render coverage in
``tests/test_routes/test_grid/test_mobile_partials.py::TestMobileThisPeriodPartial``:
pytest proves the HTML the server emits; this script proves the headless
browser puts the right pixels in the right place, the Bootstrap tab
JS responds to taps, and the prev/next arrows actually navigate.

Prerequisites:

- Playwright installed in the venv
  (``.venv/bin/pip install playwright``).
- Chromium headless-shell downloaded
  (``.venv/bin/playwright install chromium``).
- The dev Flask server running, bound to ``172.32.0.1:5000``
  (``flask run --host 172.32.0.1``).
- A logged-in session saved to ``tests/manual/.dev_session_state.json``
  via ``save_dev_session.py``.

The script is intentionally NOT a pytest test -- it needs an external
process (the dev server) and an external state file (the cookie), and
the OS-level Chromium dependency is not available in CI.

Usage::

    .venv/bin/python tests/manual/verify_mobile_grid_commit6.py

Exit code 0 = all assertions passed; non-zero = something failed.
Screenshots are written to ``tests/manual/screenshots/``.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Page, sync_playwright


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILE = REPO_ROOT / "tests" / "manual" / ".dev_session_state.json"
SHOT_DIR = REPO_ROOT / "tests" / "manual" / "screenshots"
DEV_BASE_URL = "http://172.32.0.1:5000"
VIEWPORT = {"width": 375, "height": 812}


@dataclass
class CheckResult:
    """Single assertion outcome.  ``ok`` is the success flag, ``detail``
    is a short note shown in the summary."""

    name: str
    ok: bool
    detail: str = ""


def check(results: list[CheckResult], name: str, fn: Callable[[], str]) -> None:
    """Run ``fn`` and record success/failure.

    ``fn`` should return a short success detail string on pass and
    raise ``AssertionError`` (or any exception) on fail; the exception
    message is recorded.  Wrapping every check this way lets the
    script run every assertion even when an earlier one failed --
    useful for catching cascading regressions in one pass.
    """
    try:
        detail = fn() or ""
        results.append(CheckResult(name=name, ok=True, detail=detail))
    except (AssertionError, Exception) as exc:  # pylint: disable=broad-except
        results.append(
            CheckResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}"),
        )


def shot(page: Page, label: str) -> None:
    """Save a screenshot under SHOT_DIR with a stable per-step name."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"commit6_{label}.png"
    page.screenshot(path=str(path), full_page=True)


def verify_initial_state(page: Page, results: list[CheckResult]) -> None:
    """Default-active tab, single-period partial structure, arrow hrefs."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-grid", state="attached")
    shot(page, "01_initial_load")

    # The d-md-none wrapper has the mobile grid; d-none d-md-block
    # holds the desktop table.  At 375 px viewport, only the mobile
    # branch is visible.
    check(
        results,
        "mobile_grid_visible",
        lambda: (
            "visible" if page.locator("#mobile-grid").is_visible()
            else (_ for _ in ()).throw(AssertionError("#mobile-grid not visible"))
        ),
    )

    # The two tab pills exist.
    check(
        results,
        "tab_pills_present",
        lambda: f"this-period={page.locator('#mobile-tab-this-period').count()}, "
                f"plan={page.locator('#mobile-tab-plan').count()}",
    )

    # "This Period" pill carries the active class.
    check(
        results,
        "this_period_pill_active",
        lambda: _assert_class(page, "#mobile-tab-this-period", "active"),
    )

    # "Plan" pill is not active.
    check(
        results,
        "plan_pill_not_active",
        lambda: _assert_no_class(page, "#mobile-tab-plan", "active"),
    )

    # The This Period pane is visible.
    check(
        results,
        "this_period_pane_visible",
        lambda: _assert_pane_visible(page, "#mobile-this-period"),
    )

    # The Plan pane exists but is hidden.
    check(
        results,
        "plan_pane_hidden",
        lambda: _assert_pane_hidden(page, "#mobile-plan"),
    )

    # The four content sections are inside the This Period pane.
    pane = page.locator("#mobile-this-period")
    check(
        results,
        "income_section_present",
        lambda: f"count={pane.locator('.mobile-section-income').count()}"
        if pane.locator(".mobile-section-income").count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected exactly 1 .mobile-section-income inside #mobile-this-period, "
                f"got {pane.locator('.mobile-section-income').count()}"
            ),
        ),
    )
    check(
        results,
        "expense_section_present",
        lambda: f"count={pane.locator('.mobile-section-expense').count()}"
        if pane.locator(".mobile-section-expense").count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected exactly 1 .mobile-section-expense inside #mobile-this-period, "
                f"got {pane.locator('.mobile-section-expense').count()}"
            ),
        ),
    )
    check(
        results,
        "net_cash_flow_present",
        lambda: "found" if pane.get_by_text("Net Cash Flow").count() >= 1
        else (_ for _ in ()).throw(AssertionError("'Net Cash Flow' missing")),
    )
    check(
        results,
        "projected_balance_present",
        lambda: "found" if pane.get_by_text("Projected Balance").count() >= 1
        else (_ for _ in ()).throw(AssertionError("'Projected Balance' missing")),
    )

    # Prev/next arrow hrefs at start_offset=0.
    prev = pane.locator('a[aria-label="Previous period"]').first
    nxt = pane.locator('a[aria-label="Next period"]').first
    check(
        results,
        "prev_arrow_href",
        lambda: _assert_href_endswith(prev, "/grid?periods=1&offset=-1#this-period"),
    )
    check(
        results,
        "next_arrow_href",
        lambda: _assert_href_endswith(nxt, "/grid?periods=1&offset=1#this-period"),
    )


def verify_tab_switching(page: Page, results: list[CheckResult]) -> None:
    """Click each pill and confirm the active pane swaps."""
    page.locator("#mobile-tab-plan").click()
    page.wait_for_timeout(400)  # Bootstrap fade transition
    shot(page, "02_plan_tab_clicked")
    check(
        results,
        "plan_pane_active_after_click",
        lambda: _assert_pane_visible(page, "#mobile-plan"),
    )
    check(
        results,
        "this_period_pane_hidden_after_plan_click",
        lambda: _assert_pane_hidden(page, "#mobile-this-period"),
    )

    page.locator("#mobile-tab-this-period").click()
    page.wait_for_timeout(400)
    shot(page, "03_this_period_back")
    check(
        results,
        "this_period_active_after_return",
        lambda: _assert_pane_visible(page, "#mobile-this-period"),
    )


def verify_arrow_navigation(page: Page, results: list[CheckResult]) -> None:
    """Tap the next arrow; confirm URL + landing tab."""
    page.locator('#mobile-this-period a[aria-label="Next period"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(400)  # hash-routing handler delay
    shot(page, "04_after_next_arrow")

    check(
        results,
        "url_offset_advanced",
        lambda: (
            "ok" if page.url.endswith("/grid?periods=1&offset=1#this-period")
            else (_ for _ in ()).throw(
                AssertionError(f"unexpected URL after Next: {page.url}"),
            )
        ),
    )

    check(
        results,
        "this_period_active_after_arrow",
        lambda: _assert_class(page, "#mobile-tab-this-period", "active"),
    )
    check(
        results,
        "this_period_pane_visible_after_arrow",
        lambda: _assert_pane_visible(page, "#mobile-this-period"),
    )

    pane = page.locator("#mobile-this-period")
    prev = pane.locator('a[aria-label="Previous period"]').first
    nxt = pane.locator('a[aria-label="Next period"]').first
    check(
        results,
        "prev_href_advances",
        lambda: _assert_href_endswith(prev, "/grid?periods=1&offset=0#this-period"),
    )
    check(
        results,
        "next_href_advances",
        lambda: _assert_href_endswith(nxt, "/grid?periods=1&offset=2#this-period"),
    )


def verify_desktop_unaffected(page: Page, results: list[CheckResult]) -> None:
    """Resize to desktop, confirm mobile grid is hidden + desktop table renders."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_timeout(300)
    shot(page, "05_desktop_view")

    check(
        results,
        "mobile_grid_hidden_on_desktop",
        lambda: (
            "ok" if not page.locator("#mobile-grid").is_visible()
            else (_ for _ in ()).throw(AssertionError("#mobile-grid visible at 1280px"))
        ),
    )
    check(
        results,
        "desktop_table_visible",
        lambda: (
            "ok" if page.locator(".grid-table").is_visible()
            else (_ for _ in ()).throw(AssertionError(".grid-table not visible at 1280px"))
        ),
    )


# ---- assertion helpers ----------------------------------------------------


def _assert_class(page: Page, selector: str, klass: str) -> str:
    """Confirm an element's classList contains ``klass``."""
    el = page.locator(selector).first
    cls = (el.get_attribute("class") or "").split()
    if klass not in cls:
        raise AssertionError(
            f"expected class '{klass}' on {selector}, got: {cls}",
        )
    return f"class={cls}"


def _assert_no_class(page: Page, selector: str, klass: str) -> str:
    """Confirm an element's classList does NOT contain ``klass``."""
    el = page.locator(selector).first
    cls = (el.get_attribute("class") or "").split()
    if klass in cls:
        raise AssertionError(
            f"unexpected class '{klass}' on {selector}: {cls}",
        )
    return f"class={cls}"


def _assert_pane_visible(page: Page, selector: str) -> str:
    """A Bootstrap tab-pane is 'visible' when it has the 'show' AND
    'active' classes (Bootstrap uses CSS to gate display on these)."""
    cls = (page.locator(selector).get_attribute("class") or "").split()
    if not ("show" in cls and "active" in cls):
        raise AssertionError(f"pane {selector} not visible: classes={cls}")
    return f"classes={cls}"


def _assert_pane_hidden(page: Page, selector: str) -> str:
    """Inactive tab-pane has 'fade' but not 'show'/'active'."""
    cls = (page.locator(selector).get_attribute("class") or "").split()
    if "show" in cls or "active" in cls:
        raise AssertionError(f"pane {selector} appears visible: classes={cls}")
    return f"classes={cls}"


def _assert_href_endswith(locator, suffix: str) -> str:
    """Confirm an anchor's href ends with ``suffix``."""
    href = locator.get_attribute("href")
    if href is None or not href.endswith(suffix):
        raise AssertionError(f"expected href to end with {suffix!r}, got {href!r}")
    return f"href={href}"


# ---- main -----------------------------------------------------------------


def main() -> int:
    """Run the verification harness; print a summary; return exit code."""
    if not STATE_FILE.exists():
        print(
            f"Missing {STATE_FILE}.  Run save_dev_session.py first.",
            file=sys.stderr,
        )
        return 2

    results: list[CheckResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=VIEWPORT,
        )
        page = context.new_page()

        verify_initial_state(page, results)
        verify_tab_switching(page, results)
        verify_arrow_navigation(page, results)
        verify_desktop_unaffected(page, results)

        browser.close()

    # Print summary.
    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 6 mobile verification results")
    print("=" * (width + 30))
    for r in results:
        marker = "OK  " if r.ok else "FAIL"
        print(f"  [{marker}] {r.name:<{width}} {r.detail}")
    print("=" * (width + 30))
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print(f"  Screenshots: {SHOT_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
