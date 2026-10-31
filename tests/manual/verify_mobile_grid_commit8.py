"""Headless mobile verification for Commit 8 of the v3 plan.

Drives Playwright Chromium against the local dev Flask server and asserts
the bottom-sheet drag-to-dismiss and visualViewport keyboard-avoidance
behaviour introduced in Commit 8 of the mobile-first v3 implementation:

  - On a mobile viewport (375 x 812, has_touch=True), opening the full-edit
    bottom sheet injects a ``.bottom-sheet-handle`` element as the first
    child of ``#txn-popover``.
  - On a desktop viewport (1920 x 1080, has_touch=False), the same open
    path does NOT inject a handle and does NOT attach a visualViewport
    listener.
  - touchstart on the handle adds the ``dragging`` class to the popover;
    touchend removes it.
  - touchmove updates ``popover.style.transform`` to
    ``translateY(<dy>px)`` (clamped at 0).
  - On touchend, if the drag distance is below 30 % of the sheet's height,
    the inline transform resets to ``translateY(0)`` (snap-back).
  - On touchend, if the drag distance exceeds 30 % of the sheet's height,
    closeFullEdit() runs: the popover regains ``d-none``, the inline
    transform is cleared, and the visualViewport listener ref stored on
    ``popover._adjustForKeyboard`` is deleted.

This script complements the static-render coverage in
``tests/test_routes/test_grid.py``: pytest verifies the server emits the
right HTML; this script verifies the browser-side JS (touch handlers,
visualViewport listener wiring, closeFullEdit teardown) actually fires.

Prerequisites:

- Playwright installed in the venv
  (the lock's dev group: ``uv sync``).
- Chromium headless-shell downloaded
  (``.venv/bin/playwright install chromium``).
- The dev Flask server running, bound to ``172.32.0.1:5000``
  (``flask run --host 172.32.0.1``).
- A logged-in session saved to ``tests/manual/.dev_session_state.json``
  via ``save_dev_session.py``.
- At least one Projected expense transaction visible in the current
  period (the "This Period" tab default).  Required so the mobile
  action-bar flow can find a card to open.

The script is intentionally NOT a pytest test -- it needs an external
process (the dev server) and an external state file (the cookie), and
the OS-level Chromium dependency is not available in CI.

Usage::

    .venv/bin/python tests/manual/verify_mobile_grid_commit8.py

Exit code 0 = all assertions passed; non-zero = something failed.
Screenshots are written to ``tests/manual/screenshots/``.

Side effects: opens and dismisses the bottom sheet several times but
does not save any form, so the dev database is unchanged.
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
MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1920, "height": 1080}

# Bootstrap's Collapse default transition is ~350 ms; pad to 500 ms so
# the action bar's show animation finishes before the Open Full click.
COLLAPSE_TRANSITION_MS = 500
# The CSS transition on .txn-full-edit-popover is 200 ms; pad to 300 ms
# so the snap-back has fully resolved before reading inline styles.
POPOVER_TRANSITION_MS = 300


@dataclass
class CheckResult:
    """Single assertion outcome.  ``ok`` is the success flag, ``detail``
    is a short note shown in the summary."""

    name: str
    ok: bool
    detail: str = ""


def check(results: list[CheckResult], name: str, fn: Callable[[], str]) -> None:
    """Run ``fn`` and record success/failure.

    ``fn`` should return a short success detail string on pass and raise
    ``AssertionError`` (or any exception) on fail; the exception message
    is recorded.  Wrapping every check this way lets the script run every
    assertion even when an earlier one failed -- useful for catching
    cascading regressions in one pass.
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
    path = SHOT_DIR / f"commit8_{label}.png"
    page.screenshot(path=str(path), full_page=True)


# ---- popover open helpers -------------------------------------------------


def _find_projected_card_id(page: Page, pane_selector: str) -> str:
    """Return the ``data-mobile-txn-id`` of the first Projected expense
    card in the given pane.

    Projected cards are those whose adjacent action-bar carries a Mark
    Paid form -- the bar's form-element existence implies ``status_id``
    is not Done / Settled.  Returns the first match; raises
    ``AssertionError`` if nothing in the pane qualifies.
    """
    cards = page.locator(
        f"{pane_selector} .mobile-card-wrapper:has(.mobile-card-expansion form) "
        ".mobile-txn-card[data-mobile-txn-id]"
    )
    if cards.count() == 0:
        raise AssertionError(
            f"no Projected expense card found inside {pane_selector}; "
            "seed a Projected expense in the visible period before re-running"
        )
    txn_id = cards.first.get_attribute("data-mobile-txn-id")
    if not txn_id:
        raise AssertionError(
            f"first card in {pane_selector} has no data-mobile-txn-id attr"
        )
    return txn_id


def _open_bottom_sheet_mobile(page: Page, txn_id: str) -> None:
    """Tap the card with ``txn_id`` then tap its Open Full button.

    Mirrors the production flow: mobile_grid.js opens the per-card action
    bar on the first tap, the Open Full button in that bar fires the
    delegated ``txn-expand-btn`` click handler which calls
    ``openFullEdit`` -> ``positionPopover`` -> ``showPopover``.  Waits
    for the popover to lose ``d-none`` before returning.
    """
    card = page.locator(
        f'#mobile-this-period .mobile-txn-card[data-mobile-txn-id="{txn_id}"]'
    ).first
    card.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)

    open_full_btn = (
        card.locator(
            'xpath=ancestor::div[contains(concat(" ", @class, " "), " mobile-card-wrapper ")][1]'
        )
        .locator(".mobile-card-expansion")
        .first.locator(f'button.txn-expand-btn[data-txn-id="{txn_id}"]')
    )
    if open_full_btn.count() != 1:
        raise AssertionError(
            f"expected exactly 1 Open Full button for txn {txn_id}, "
            f"got {open_full_btn.count()}"
        )
    open_full_btn.click()
    page.wait_for_function(
        "() => { const el = document.getElementById('txn-popover');"
        "        return el && !el.classList.contains('d-none'); }",
        timeout=3000,
    )


def _open_bottom_sheet_desktop(page: Page) -> str:
    """Drive openFullEdit() directly on any visible desktop transaction
    cell.

    The desktop grid renders each cell wrapped in ``#txn-cell-<id>``
    inside a ``<td>``; passing that wrapper to ``openFullEdit`` as the
    trigger element satisfies ``positionPopover``'s
    ``triggerEl.closest('td')`` lookup.  Returns the txn_id used.

    Bypassing the click-cell -> swap-quick-edit -> click-expand flow
    keeps the desktop check independent of the mobile-only action bar
    that Commit 7 introduced.  The code path we are verifying lives in
    ``showPopover`` (and ``positionPopover``); both run regardless of
    which trigger surface opened the popover.
    """
    txn_id_str = page.evaluate(
        """() => {
            const el = document.querySelector('div[id^="txn-cell-"]');
            return el ? el.id.replace('txn-cell-', '') : null;
        }"""
    )
    if not txn_id_str:
        raise AssertionError("no #txn-cell-<id> div in DOM on desktop /grid")
    txn_id = int(txn_id_str)
    page.evaluate(
        """([id]) => {
            const trig = document.getElementById('txn-cell-' + id);
            if (!trig) throw new Error('no #txn-cell-' + id + ' in DOM');
            window.openFullEdit(id, trig);
        }""",
        [txn_id],
    )
    page.wait_for_function(
        "() => { const el = document.getElementById('txn-popover');"
        "        return el && !el.classList.contains('d-none'); }",
        timeout=3000,
    )
    return str(txn_id)


# ---- touch event synthesis ------------------------------------------------


def _touch_start(page: Page) -> float:
    """Dispatch touchstart at the handle's center; return the start Y.

    The browser-level handler reads ``e.touches[0].clientY`` to capture
    ``startY``; returning it here lets follow-up touchmove/touchend calls
    construct events with the same anchor so the production handler's
    ``dy = current - startY`` arithmetic matches the test's intent.
    """
    return page.evaluate(
        """() => {
            const h = document.querySelector('#txn-popover .bottom-sheet-handle');
            if (!h) throw new Error('no .bottom-sheet-handle on #txn-popover');
            const r = h.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const y = r.top + r.height / 2;
            const t = new Touch({
                identifier: 0, target: h, clientX: x, clientY: y,
            });
            h.dispatchEvent(new TouchEvent('touchstart', {
                bubbles: true, cancelable: true,
                touches: [t], targetTouches: [t], changedTouches: [t],
            }));
            return y;
        }"""
    )


def _touch_move(page: Page, start_y: float, dy: int) -> None:
    """Dispatch touchmove with clientY = start_y + dy."""
    page.evaluate(
        """([startY, deltaY]) => {
            const h = document.querySelector('#txn-popover .bottom-sheet-handle');
            if (!h) throw new Error('no .bottom-sheet-handle on #txn-popover');
            const r = h.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const t = new Touch({
                identifier: 0, target: h, clientX: x, clientY: startY + deltaY,
            });
            h.dispatchEvent(new TouchEvent('touchmove', {
                bubbles: true, cancelable: true,
                touches: [t], targetTouches: [t], changedTouches: [t],
            }));
        }""",
        [start_y, dy],
    )


def _touch_end(page: Page, last_y: float) -> None:
    """Dispatch touchend on the handle; touches is empty, changedTouches
    carries the last Touch.

    The production handler reads no touches at touchend; the
    changedTouches entry exists only to satisfy the constructor."""
    page.evaluate(
        """([lastY]) => {
            const h = document.querySelector('#txn-popover .bottom-sheet-handle');
            if (!h) throw new Error('no .bottom-sheet-handle on #txn-popover');
            const r = h.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const t = new Touch({
                identifier: 0, target: h, clientX: x, clientY: lastY,
            });
            h.dispatchEvent(new TouchEvent('touchend', {
                bubbles: true, cancelable: true,
                touches: [], targetTouches: [], changedTouches: [t],
            }));
        }""",
        [last_y],
    )


# ---- DOM accessors --------------------------------------------------------


def _popover_height(page: Page) -> int:
    """Return ``#txn-popover.offsetHeight`` (integer px)."""
    return int(
        page.evaluate(
            "() => document.getElementById('txn-popover').offsetHeight"
        )
    )


def _popover_has_class(page: Page, klass: str) -> bool:
    """Return whether ``#txn-popover`` carries ``klass`` in its classList."""
    return bool(
        page.evaluate(
            "(k) => document.getElementById('txn-popover').classList.contains(k)",
            klass,
        )
    )


def _popover_transform(page: Page) -> str:
    """Return ``#txn-popover.style.transform`` (inline style, not computed)."""
    return page.evaluate(
        "() => document.getElementById('txn-popover').style.transform"
    )


def _popover_has_handle(page: Page) -> bool:
    """Return whether ``#txn-popover`` contains a ``.bottom-sheet-handle``."""
    return bool(
        page.evaluate(
            "() => !!document.querySelector('#txn-popover .bottom-sheet-handle')"
        )
    )


def _popover_has_adjust_for_keyboard(page: Page) -> bool:
    """Return whether ``popover._adjustForKeyboard`` is defined.

    The function is stashed by ``applyMobileBottomSheetBehavior`` so
    ``closeFullEdit`` can remove the visualViewport.resize listener
    without re-deriving the closure.  Presence/absence of this
    property is the test's proxy for "listener attached".
    """
    return bool(
        page.evaluate(
            "() => typeof document.getElementById('txn-popover')._adjustForKeyboard === 'function'"
        )
    )


# ---- verification scenarios ----------------------------------------------


def verify_mobile_open_injects_handle(
    page: Page, results: list[CheckResult]
) -> None:
    """On mobile open, the drag handle is the first child of the popover."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    shot(page, "01_mobile_initial_load")

    try:
        txn_id = _find_projected_card_id(page, "#mobile-this-period")
    except AssertionError as exc:
        results.append(CheckResult(
            name="mobile_found_projected_card",
            ok=False, detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="mobile_found_projected_card",
        ok=True, detail=f"txn_id={txn_id}",
    ))

    try:
        _open_bottom_sheet_mobile(page, txn_id)
    except AssertionError as exc:
        results.append(CheckResult(
            name="mobile_open_bottom_sheet",
            ok=False, detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="mobile_open_bottom_sheet",
        ok=True, detail="popover lost d-none",
    ))
    shot(page, "02_mobile_sheet_open_with_handle")

    check(
        results,
        "handle_present_on_mobile",
        lambda: "ok" if _popover_has_handle(page)
        else (_ for _ in ()).throw(
            AssertionError(
                ".bottom-sheet-handle missing from #txn-popover on mobile open"
            ),
        ),
    )

    check(
        results,
        "handle_is_first_child",
        lambda: "ok" if page.evaluate(
            "() => document.getElementById('txn-popover').firstElementChild"
            ".classList.contains('bottom-sheet-handle')"
        )
        else (_ for _ in ()).throw(
            AssertionError(
                "first child of #txn-popover is not the bottom-sheet-handle"
            ),
        ),
    )

    check(
        results,
        "adjust_for_keyboard_attached",
        lambda: "ok" if _popover_has_adjust_for_keyboard(page)
        else (_ for _ in ()).throw(
            AssertionError(
                "popover._adjustForKeyboard is not a function "
                "(visualViewport listener was not stashed)"
            ),
        ),
    )


def verify_drag_snaps_back(
    page: Page, results: list[CheckResult]
) -> None:
    """Drag the handle 50 px and release: sheet stays open, transform
    resets to translateY(0)."""
    # Bottom sheet must be open from the previous scenario; if not, reopen.
    if _popover_has_class(page, "d-none") or not _popover_has_handle(page):
        page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
        page.wait_for_selector("#mobile-this-period", state="attached")
        try:
            txn_id = _find_projected_card_id(page, "#mobile-this-period")
            _open_bottom_sheet_mobile(page, txn_id)
        except AssertionError as exc:
            results.append(CheckResult(
                name="drag_snap_back_reopen",
                ok=False, detail=str(exc),
            ))
            return

    y0 = _touch_start(page)
    check(
        results,
        "dragging_class_added_on_touchstart",
        lambda: "ok" if _popover_has_class(page, "dragging")
        else (_ for _ in ()).throw(
            AssertionError(
                "popover did not gain .dragging class on touchstart"
            ),
        ),
    )

    _touch_move(page, y0, dy=50)
    check(
        results,
        "transform_updates_on_touchmove",
        lambda: f"transform={_popover_transform(page)}"
        if "translateY(50px)" in _popover_transform(page)
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected transform translateY(50px) after 50 px drag, "
                f"got: {_popover_transform(page)!r}"
            ),
        ),
    )

    _touch_end(page, last_y=y0 + 50)
    # CSS transition is 200 ms; wait for snap-back render.
    page.wait_for_timeout(POPOVER_TRANSITION_MS)
    shot(page, "03_mobile_sheet_after_snap_back")

    check(
        results,
        "dragging_class_removed_on_touchend",
        lambda: "ok" if not _popover_has_class(page, "dragging")
        else (_ for _ in ()).throw(
            AssertionError(
                "popover still has .dragging class after touchend"
            ),
        ),
    )

    check(
        results,
        "sheet_still_open_after_snap_back",
        lambda: "ok" if not _popover_has_class(page, "d-none")
        else (_ for _ in ()).throw(
            AssertionError(
                "popover gained d-none after a sub-30%% drag (should snap back)"
            ),
        ),
    )

    check(
        results,
        "transform_reset_to_zero_after_snap_back",
        lambda: f"transform={_popover_transform(page)}"
        if "translateY(0" in _popover_transform(page)
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected transform translateY(0) after snap-back, "
                f"got: {_popover_transform(page)!r}"
            ),
        ),
    )


def verify_drag_past_threshold_dismisses(
    page: Page, results: list[CheckResult]
) -> None:
    """Drag the handle past 30 % of the sheet's height: closeFullEdit
    runs (d-none returns; visualViewport listener torn down)."""
    if _popover_has_class(page, "d-none") or not _popover_has_handle(page):
        page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
        page.wait_for_selector("#mobile-this-period", state="attached")
        try:
            txn_id = _find_projected_card_id(page, "#mobile-this-period")
            _open_bottom_sheet_mobile(page, txn_id)
        except AssertionError as exc:
            results.append(CheckResult(
                name="drag_dismiss_reopen",
                ok=False, detail=str(exc),
            ))
            return

    sheet_h = _popover_height(page)
    # Drag 60 % of the sheet height -- comfortably past the 30 % threshold
    # so the test does not flake on small content-height variations.
    dy = int(sheet_h * 0.60)
    results.append(CheckResult(
        name="captured_sheet_height",
        ok=True, detail=f"offsetHeight={sheet_h} dy={dy}",
    ))

    y0 = _touch_start(page)
    _touch_move(page, y0, dy=dy)
    _touch_end(page, last_y=y0 + dy)
    page.wait_for_timeout(POPOVER_TRANSITION_MS)
    shot(page, "04_mobile_sheet_after_dismiss")

    check(
        results,
        "sheet_dismissed_after_30pct_drag",
        lambda: "ok" if _popover_has_class(page, "d-none")
        else (_ for _ in ()).throw(
            AssertionError(
                f"popover lacks d-none after drag of {dy} px ("
                f"threshold was {int(sheet_h * 0.30)} px)"
            ),
        ),
    )

    check(
        results,
        "transform_cleared_after_dismiss",
        lambda: f"transform={_popover_transform(page)!r}"
        if _popover_transform(page) == ""
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected empty inline transform after dismiss, "
                f"got: {_popover_transform(page)!r}"
            ),
        ),
    )

    check(
        results,
        "adjust_for_keyboard_removed_after_dismiss",
        lambda: "ok" if not _popover_has_adjust_for_keyboard(page)
        else (_ for _ in ()).throw(
            AssertionError(
                "popover._adjustForKeyboard still defined after dismiss "
                "(visualViewport listener teardown skipped)"
            ),
        ),
    )


def verify_desktop_open_no_handle(
    page: Page, results: list[CheckResult]
) -> None:
    """At desktop viewport, opening the full-edit popover does NOT inject
    a drag handle and does NOT attach a visualViewport listener.

    Runs in a separate Playwright context (no touch support, wider
    viewport) so the ``window.innerWidth < 768`` gate inside
    ``applyMobileBottomSheetBehavior`` short-circuits.
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#txn-popover", state="attached")
    shot(page, "05_desktop_initial_load")

    try:
        txn_id = _open_bottom_sheet_desktop(page)
    except AssertionError as exc:
        results.append(CheckResult(
            name="desktop_open_popover",
            ok=False, detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="desktop_open_popover",
        ok=True, detail=f"opened via #txn-cell-{txn_id}",
    ))
    shot(page, "06_desktop_popover_open")

    check(
        results,
        "no_handle_on_desktop",
        lambda: "ok" if not _popover_has_handle(page)
        else (_ for _ in ()).throw(
            AssertionError(
                ".bottom-sheet-handle present on desktop open "
                "(mobile-only branch leaked)"
            ),
        ),
    )

    check(
        results,
        "no_adjust_for_keyboard_on_desktop",
        lambda: "ok" if not _popover_has_adjust_for_keyboard(page)
        else (_ for _ in ()).throw(
            AssertionError(
                "popover._adjustForKeyboard is set on desktop open "
                "(visualViewport listener leaked from mobile branch)"
            ),
        ),
    )


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

        # Mobile context first.  has_touch=True enables the global Touch
        # and TouchEvent constructors that our synthetic-touch helpers
        # rely on; viewport width < 768 makes the JS mobile branch fire.
        mobile_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=MOBILE_VIEWPORT,
            has_touch=True,
            is_mobile=True,
        )
        mobile_page = mobile_ctx.new_page()
        verify_mobile_open_injects_handle(mobile_page, results)
        verify_drag_snaps_back(mobile_page, results)
        verify_drag_past_threshold_dismisses(mobile_page, results)
        mobile_ctx.close()

        # Desktop context.  Wide viewport + no touch keeps the mobile
        # branch dormant so the no-handle / no-listener invariants hold.
        desktop_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=DESKTOP_VIEWPORT,
            has_touch=False,
            is_mobile=False,
        )
        desktop_page = desktop_ctx.new_page()
        verify_desktop_open_no_handle(desktop_page, results)
        desktop_ctx.close()

        browser.close()

    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 8 mobile verification results")
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
