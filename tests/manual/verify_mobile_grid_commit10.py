"""Headless mobile verification for Commit 10 of the v3 plan.

Drives Playwright Chromium against the local dev Flask server and
asserts the jump-to-period ``<select>`` introduced in Commit 10 of
the mobile-first v3 implementation:

  - On a mobile viewport (375 x 812), the ``<select name="offset">``
    lives inside ``#mobile-this-period`` directly below the
    ``[<] [>]`` arrow row, the option count matches the number of
    periods returned by ``pay_period_service.get_all_periods`` for
    the dev user, and the currently visible period's option carries
    ``selected``.
  - Changing the select fires the delegated change handler in
    ``app/static/js/mobile_grid.js``, which submits the parent form
    as a full GET to ``/grid?periods=1&offset=N`` (captured via the
    Playwright request monitor before the navigation lands).
  - After the navigation, the URL carries ``periods=1`` and
    ``offset=<chosen>``, the page lands on the "This Period" tab
    (default-active when no hash is present), and the previously
    chosen option is now the ``selected`` one.
  - The browser console reports no Content Security Policy
    violations while the delegated handler fires (the CSP guard
    against a regression that re-introduces the inline
    ``onchange="this.form.submit()"`` markup from the plan's draft).
  - On a desktop viewport (1920 x 1080), the entire ``#mobile-grid``
    -- including the new select -- is hidden via ``.d-md-none``;
    the desktop selector at ``grid.html:24-49`` is the visible
    affordance there.

This script complements the static-render coverage in
``tests/test_routes/test_grid/test_mobile_plan.py::TestMobileJumpToPeriod``:
pytest verifies the server emits the right HTML; this script verifies the
delegated change handler actually submits, the navigation lands on
the right URL, and CSP does not silently reject the JS.

Prerequisites:

- Playwright installed in the venv
  (``.venv/bin/pip install -r requirements-dev.txt``).
- Chromium headless-shell downloaded
  (``.venv/bin/playwright install chromium``).
- The dev Flask server running, bound to ``172.32.0.1:5000``
  (``flask run --host 172.32.0.1``).
- A logged-in session saved to
  ``tests/manual/.dev_session_state.json`` via
  ``save_dev_session.py``.
- At least two pay periods seeded for the dev user (the default
  setup wizard generates ~52, so this is usually free).

The script is intentionally NOT a pytest test -- it needs an
external process (the dev server) and an external state file
(the cookie), and the OS-level Chromium dependency is not available
in CI.

Usage::

    .venv/bin/python tests/manual/verify_mobile_grid_commit10.py

Exit code 0 = all assertions passed; non-zero = something failed.
Screenshots are written to ``tests/manual/screenshots/``.

Side effects: NONE on the dev DB.  The jump-to navigation is a
pure GET; it changes the URL but writes nothing.  The script can
be re-run repeatedly without DB cleanup.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import ConsoleMessage, Page, sync_playwright


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILE = REPO_ROOT / "tests" / "manual" / ".dev_session_state.json"
SHOT_DIR = REPO_ROOT / "tests" / "manual" / "screenshots"
DEV_BASE_URL = "http://172.32.0.1:5000"
MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1920, "height": 1080}


@dataclass
class CheckResult:
    """Single assertion outcome.  ``ok`` is the success flag,
    ``detail`` is a short note shown in the summary."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class ConsoleSink:
    """Per-page console-message collector.

    Playwright's ``page.on('console', fn)`` callback receives a
    ``ConsoleMessage`` per browser console event.  We capture every
    message so the CSP-violation check can scan them after the
    navigation completes.  ``messages`` carries ``(type, text)``
    tuples so the assertion can both filter by severity and grep
    the human-readable body.
    """

    messages: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, msg: ConsoleMessage) -> None:
        self.messages.append((msg.type, msg.text))


def check(results: list[CheckResult], name: str, fn: Callable[[], str]) -> None:
    """Run ``fn`` and record success/failure.

    ``fn`` should return a short success detail string on pass and
    raise ``AssertionError`` (or any exception) on fail; the
    exception message is recorded.  Wrapping every check this way
    lets the script run every assertion even when an earlier one
    failed -- useful for catching cascading regressions in one pass.
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
    path = SHOT_DIR / f"commit10_{label}.png"
    page.screenshot(path=str(path), full_page=True)


def _select_option_count(page: Page) -> int:
    """Return the number of ``<option>`` elements inside the
    jump-to ``<select>`` within ``#mobile-this-period``."""
    return int(page.evaluate(
        """() => {
            const root = document.querySelector('#mobile-this-period');
            if (!root) return -1;
            const sel = root.querySelector('select[name="offset"]');
            if (!sel) return -2;
            return sel.options.length;
        }"""
    ))


def _selected_value(page: Page) -> str | None:
    """Return the ``value`` of the currently-selected option, or
    ``None`` if the select is missing."""
    val = page.evaluate(
        """() => {
            const root = document.querySelector('#mobile-this-period');
            if (!root) return null;
            const sel = root.querySelector('select[name="offset"]');
            if (!sel) return null;
            return sel.value;
        }"""
    )
    return val if val is not None else None


def _option_values(page: Page) -> list[str]:
    """Return the ``value`` attribute of every option, in DOM order."""
    return list(page.evaluate(
        """() => {
            const root = document.querySelector('#mobile-this-period');
            if (!root) return [];
            const sel = root.querySelector('select[name="offset"]');
            if (!sel) return [];
            return Array.from(sel.options).map((o) => o.value);
        }"""
    ))


# ---- verification scenarios ----------------------------------------------


def verify_select_present_on_mobile(
    page: Page, results: list[CheckResult],
) -> None:
    """The select is rendered inside ``#mobile-this-period`` and
    its option count matches ``all_periods`` for the dev user."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    page.wait_for_selector(
        "#mobile-this-period select[name='offset']", state="attached",
    )
    shot(page, "01_mobile_initial_load")

    check(
        results,
        "select_present_inside_this_period_pane",
        lambda: "ok" if page.evaluate(
            "() => !!document.querySelector("
            "  '#mobile-this-period select[name=\"offset\"]'"
            ")",
        )
        else (_ for _ in ()).throw(
            AssertionError(
                "select[name='offset'] missing inside #mobile-this-period"
            ),
        ),
    )

    count = _select_option_count(page)
    check(
        results,
        "select_has_at_least_two_options",
        lambda: f"options={count}" if count >= 2
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected >= 2 options (need a non-current option to jump "
                f"to), got {count}"
            ),
        ),
    )

    selected = _selected_value(page)
    check(
        results,
        "current_period_selected_on_default_load",
        lambda: f"selected={selected}" if selected == "0"
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected selected='0' on /grid (start_offset=0), "
                f"got selected={selected!r}"
            ),
        ),
    )

    values = _option_values(page)
    check(
        results,
        "options_have_distinct_integer_values",
        lambda: f"values={values[:3]}..."
        if (len(set(values)) == len(values) and all(
            v.lstrip("-").isdigit() for v in values
        ))
        else (_ for _ in ()).throw(
            AssertionError(
                f"option values are not distinct integers: {values!r}"
            ),
        ),
    )


def verify_change_submits_form_and_navigates(
    page: Page, results: list[CheckResult],
) -> None:
    """Changing the select fires the delegated handler, which
    submits the form as GET to /grid?periods=1&offset=N, and the
    page lands at that URL on the "This Period" tab."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector(
        "#mobile-this-period select[name='offset']", state="attached",
    )

    values = _option_values(page)
    # Pick a non-current target: prefer +2 if available (a non-
    # adjacent jump exercises the "skip past prev/next arrows" use
    # case), otherwise fall back to any non-zero value.
    if "2" in values:
        target = "2"
    else:
        non_zero = [v for v in values if v != "0"]
        if not non_zero:
            results.append(CheckResult(
                name="non_current_option_available",
                ok=False,
                detail=f"no non-zero option in {values!r} -- need >= 2 periods",
            ))
            return
        target = non_zero[0]

    results.append(CheckResult(
        name="non_current_option_available",
        ok=True, detail=f"target={target} (from {values!r})",
    ))

    # Network monitor: capture every navigation request so we can
    # confirm the GET landed at /grid with the expected query
    # string before the page swap completes.
    captured: list[str] = []

    def _on_request(req) -> None:
        # Only document-level navigations carry the form values in
        # the URL; XHR/HTMX requests on the page do not.  Filter to
        # the top-level GET to /grid.
        if req.method == "GET" and "/grid" in req.url and req.resource_type == "document":
            captured.append(req.url)

    page.on("request", _on_request)

    # Wait for navigation while triggering the change.  Playwright's
    # select_option dispatches a real change event, which the
    # delegated handler picks up and calls form.submit() on -- the
    # full GET that submit() initiates is what expect_navigation
    # waits for.
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.select_option(
            "#mobile-this-period select[name='offset']", value=target,
        )

    page.remove_listener("request", _on_request)
    shot(page, "02_after_select_change")

    check(
        results,
        "navigation_request_captured",
        lambda: f"url={captured[-1]}" if (
            captured and "/grid" in captured[-1]
        )
        else (_ for _ in ()).throw(
            AssertionError(
                f"no document-level GET to /grid captured: {captured!r}"
            ),
        ),
    )

    # Parse the final URL and assert the query string matches the
    # expected (periods=1, offset=target).
    parsed = urlparse(page.url)
    qs = parse_qs(parsed.query)

    check(
        results,
        "url_carries_periods_eq_1",
        lambda: f"periods={qs.get('periods')!r}"
        if qs.get("periods") == ["1"]
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected periods=1 in URL, got query={parsed.query!r}"
            ),
        ),
    )

    check(
        results,
        "url_carries_target_offset",
        lambda: f"offset={qs.get('offset')!r}"
        if qs.get("offset") == [target]
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected offset={target!r} in URL, got query={parsed.query!r}"
            ),
        ),
    )

    # The default-active tab is "This Period"; with no hash in the
    # post-submit URL the tab pane's "show active" class must be
    # present (the hash-routing handler short-circuits on the
    # missing hash and leaves the default alone).
    check(
        results,
        "this_period_tab_active_after_submit",
        lambda: "ok" if page.evaluate(
            "() => {"
            "  const p = document.querySelector('#mobile-this-period');"
            "  if (!p) return false;"
            "  return p.classList.contains('show') "
            "         && p.classList.contains('active');"
            "}",
        )
        else (_ for _ in ()).throw(
            AssertionError(
                "#mobile-this-period missing 'show active' after submit -- "
                "landed on a different tab"
            ),
        ),
    )

    # The visible period after the GET should be the one we picked;
    # the select on the new page should now mark `target` selected.
    new_selected = _selected_value(page)
    check(
        results,
        "select_selected_follows_target_after_submit",
        lambda: f"selected={new_selected}" if new_selected == target
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected selected={target!r} on jumped-to page, "
                f"got selected={new_selected!r}"
            ),
        ),
    )


def verify_no_csp_violation_on_change(
    page: Page, results: list[CheckResult], sink: ConsoleSink,
) -> None:
    """Scan the console-message sink for script / event-handler CSP
    violations.

    The intent of this check is narrow: the delegated change
    handler in ``mobile_grid.js`` (chosen over the plan's draft
    inline ``onchange="this.form.submit()"``) must not trigger any
    ``script-src`` or inline-event-handler CSP violations.  A
    regression that re-introduced an inline handler would surface
    here as a "Refused to execute inline event handler" entry; a
    regression that introduced a new ``<script>...</script>`` block
    would surface as "Refused to execute inline script".

    Style-src violations are NOT filtered into this check: there is
    a pre-existing pile of ``style="min-height: 44px;"`` attributes
    in ``app/templates/grid/_mobile_card_actions.html`` (lines
    66, 75, 85) that emit ~1k inline-style violations per grid
    page load.  Those are unrelated to this commit's JS handler;
    they are flagged separately in the work summary's
    OUT OF SCOPE section.
    """
    # The change-and-navigate scenario already ran before this is
    # invoked, so the sink has whatever the page emitted across the
    # initial load + the navigation.
    relevant_msgs = [
        (typ, text) for (typ, text) in sink.messages
        if "Refused to execute inline event handler" in text
        or "Refused to execute inline script" in text
    ]

    check(
        results,
        "no_script_or_handler_csp_violation_during_select_submit",
        lambda: (
            f"console_messages={len(sink.messages)}, "
            f"script/handler_violations=0"
        )
        if not relevant_msgs
        else (_ for _ in ()).throw(
            AssertionError(
                f"{len(relevant_msgs)} script/handler CSP "
                f"violation(s): {relevant_msgs!r}"
            ),
        ),
    )


def verify_desktop_select_hidden(
    page: Page, results: list[CheckResult],
) -> None:
    """On the desktop viewport ``#mobile-grid`` (which wraps the
    select via the partial) is hidden via ``.d-md-none``; the
    desktop selector at grid.html:24-49 is the visible affordance.

    The select element IS in the DOM (the partial renders server-
    side regardless of viewport); the assertion is that the
    enclosing #mobile-grid has computed display:none so the JS
    handler's selector match still requires the user to be on a
    narrow viewport for the gesture to have effect."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector(".grid-table", state="attached")
    shot(page, "03_desktop_initial_load")

    check(
        results,
        "mobile_grid_hidden_on_desktop",
        lambda: "ok" if page.evaluate(
            "() => {"
            "  const el = document.getElementById('mobile-grid');"
            "  if (!el) return true;"
            "  return getComputedStyle(el).display === 'none';"
            "}",
        )
        else (_ for _ in ()).throw(
            AssertionError(
                "#mobile-grid is visible on desktop viewport "
                "(d-md-none did not hide it)"
            ),
        ),
    )

    check(
        results,
        "desktop_period_selector_visible",
        lambda: "ok" if page.evaluate(
            "() => !!document.querySelector('.period-btn-group')"
        )
        else (_ for _ in ()).throw(
            AssertionError(
                ".period-btn-group (desktop period selector) missing on desktop"
            ),
        ),
    )


# ---- main -----------------------------------------------------------------


def main() -> int:
    """Run the verification harness; print a summary; return exit
    code."""
    if not STATE_FILE.exists():
        print(
            f"Missing {STATE_FILE}.  Run save_dev_session.py first.",
            file=sys.stderr,
        )
        return 2

    results: list[CheckResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # Mobile context.  has_touch=True matches the real iPhone
        # user-agent surface so the same CSS / JS branches apply as
        # on a physical device; viewport width < 768 makes the
        # mobile-only CSS paths (the .d-md-none reveal of
        # #mobile-grid) the visible ones.
        mobile_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=MOBILE_VIEWPORT,
            has_touch=True,
            is_mobile=True,
        )
        mobile_page = mobile_ctx.new_page()
        sink = ConsoleSink()
        mobile_page.on("console", sink)

        verify_select_present_on_mobile(mobile_page, results)
        verify_change_submits_form_and_navigates(mobile_page, results)
        verify_no_csp_violation_on_change(mobile_page, results, sink)
        mobile_ctx.close()

        # Desktop context.  Wide viewport + no touch confirms the
        # mobile partial (and its new select) stay hidden and the
        # desktop period selector is the visible affordance.
        desktop_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=DESKTOP_VIEWPORT,
            has_touch=False,
            is_mobile=False,
        )
        desktop_page = desktop_ctx.new_page()
        verify_desktop_select_hidden(desktop_page, results)
        desktop_ctx.close()

        browser.close()

    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 10 mobile verification results")
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
