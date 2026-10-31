"""Headless mobile verification for Commit 14 of the v3 plan.

Drives Playwright Chromium against the local dev Flask server and
asserts the Add Transaction modal's ``modal-fullscreen-sm-down``
behaviour introduced in Commit 14 of the mobile-first v3
implementation:

  - The modal-dialog markup carries ``modal-fullscreen-sm-down``
    on both viewports (the class is unconditional in the template;
    Bootstrap's CSS media-queries gate the visual behaviour).
  - On a mobile viewport (375 x 812) after opening the modal:
      * the dialog's computed bounding box fills the viewport edge
        to edge (Bootstrap's fullscreen-sm-down zeroes the margin
        and stretches width / height to 100 %),
      * ``.modal-content`` border-radius is 0 (Bootstrap removes
        the rounded corners for the fullscreen variant),
      * ``.modal-footer`` carries the custom
        ``.modal-fullscreen-sm-down .modal-footer`` rule from
        grid.css: ``position: sticky``, ``bottom: 0`` (so the
        Save button stays parked at the bottom of the modal's
        scroll area above the iOS on-screen keyboard).
      * the modal's estimated_amount input carries
        ``inputmode="decimal"`` (regression lock for Commit 11; this
        commit's plan calls the attribute out explicitly).
  - On a desktop viewport (1920 x 1080) after opening the same
    modal, the dialog does NOT fill the viewport (Bootstrap's
    fullscreen rules are gated by ``@media (max-width: 575.98px)``
    so the >= 576 px case falls back to the default centred
    dialog).  The modal-footer is not sticky on desktop.

This script complements the static-render coverage in
``tests/test_routes/test_grid.py``: pytest verifies the server emits
the right HTML; this script verifies Bootstrap's CSS classes
actually produce the right computed style on a real headless
browser at the mobile vs desktop viewports, and confirms our custom
sticky-footer rule is loaded and effective.

Prerequisites:

- Playwright installed in the venv
  (the lock's dev group: ``uv sync``).
- Chromium headless-shell downloaded
  (``.venv/bin/playwright install chromium``).
- The dev Flask server running, bound to ``172.32.0.1:5000``
  (``flask run --host 172.32.0.1``).
- A logged-in session saved to
  ``tests/manual/.dev_session_state.json`` via
  ``save_dev_session.py``.

The script is intentionally NOT a pytest test -- it needs an
external process (the dev server) and an external state file
(the cookie), and the OS-level Chromium dependency is not available
in CI.

Usage::

    .venv/bin/python tests/manual/verify_mobile_grid_commit14.py

Exit code 0 = all assertions passed; non-zero = something failed.
Screenshots are written to ``tests/manual/screenshots/``.

Side effects: NONE on the dev DB.  The script opens the Add
Transaction modal and inspects it; it never submits the form.
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

# The "sm" breakpoint that gates modal-fullscreen-sm-down is 576 px.
# Our custom sticky-footer @media block uses 575.98 px as the upper
# bound so it lines up with Bootstrap's own threshold.
SM_BREAKPOINT_PX = 576

# Tolerance for "fills the viewport" assertions: Bootstrap may add a
# 1-2 px scroll-bar accommodation depending on the host browser, so
# compare against viewport size minus a small slop instead of
# requiring exact equality.
VIEWPORT_FILL_SLOP_PX = 4


@dataclass
class CheckResult:
    """Single assertion outcome.  ``ok`` is the success flag,
    ``detail`` is a short note shown in the summary."""

    name: str
    ok: bool
    detail: str = ""


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
    path = SHOT_DIR / f"commit14_{label}.png"
    page.screenshot(path=str(path), full_page=True)


def _open_add_transaction_modal(page: Page) -> None:
    """Click the Add Transaction button and wait for the modal to
    finish its show animation.

    Bootstrap toggles ``show`` on the modal once the fade-in
    transition is past its first frame; waiting for that class
    avoids racing the bounding-box assertions against a still-
    animating dialog (the dialog reaches its final size only after
    the fade completes, ~150 ms).
    """
    page.click("a[data-bs-target='#addTransactionModal']")
    page.wait_for_selector(
        "#addTransactionModal.show", state="visible", timeout=2000,
    )
    # Bootstrap dispatches 'shown.bs.modal' once the transition ends;
    # waiting one animation frame past show=visible is enough for the
    # computed style to settle.
    page.wait_for_timeout(200)


def _dialog_box(page: Page) -> dict[str, float]:
    """Return the bounding box of ``#addTransactionModal .modal-dialog``."""
    box = page.evaluate(
        """() => {
            const el = document.querySelector(
                '#addTransactionModal .modal-dialog'
            );
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return { x: r.x, y: r.y, width: r.width, height: r.height };
        }"""
    )
    if box is None:
        raise AssertionError(".modal-dialog not found in #addTransactionModal")
    return box


def _computed_style(page: Page, selector: str, prop: str) -> str:
    """Return ``getComputedStyle`` for ``selector``'s ``prop``."""
    return str(page.evaluate(
        """(args) => {
            const el = document.querySelector(args.selector);
            if (!el) return '';
            return getComputedStyle(el).getPropertyValue(args.prop);
        }""",
        {"selector": selector, "prop": prop},
    ))


# ---- verification scenarios ----------------------------------------------


def verify_dialog_has_fullscreen_class(
    page: Page, results: list[CheckResult],
) -> None:
    """The ``modal-dialog`` element carries the
    ``modal-fullscreen-sm-down`` class regardless of viewport (the
    class is always present in the template; Bootstrap's CSS gates
    the visual behaviour by viewport)."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#addTransactionModal", state="attached")

    check(
        results,
        "dialog_has_fullscreen_class",
        lambda: "ok" if page.evaluate(
            "() => {"
            "  const d = document.querySelector("
            "    '#addTransactionModal .modal-dialog'"
            "  );"
            "  return !!d && d.classList.contains('modal-fullscreen-sm-down');"
            "}",
        )
        else (_ for _ in ()).throw(
            AssertionError(
                ".modal-dialog missing class 'modal-fullscreen-sm-down'"
            ),
        ),
    )


def verify_mobile_dialog_fills_viewport(
    page: Page, results: list[CheckResult],
) -> None:
    """On the mobile viewport the dialog's computed bounding box
    spans the full width and height of the viewport (Bootstrap's
    fullscreen-sm-down zeroes the margin and sets width/height to
    100 %)."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    _open_add_transaction_modal(page)
    shot(page, "01_mobile_modal_open")

    box = _dialog_box(page)
    vp_w = MOBILE_VIEWPORT["width"]
    vp_h = MOBILE_VIEWPORT["height"]

    check(
        results,
        "mobile_dialog_width_fills_viewport",
        lambda: f"width={box['width']} (viewport={vp_w})"
        if box["width"] >= vp_w - VIEWPORT_FILL_SLOP_PX
        else (_ for _ in ()).throw(
            AssertionError(
                f"dialog width={box['width']} did not fill viewport "
                f"width={vp_w} (slop={VIEWPORT_FILL_SLOP_PX})"
            ),
        ),
    )

    check(
        results,
        "mobile_dialog_height_fills_viewport",
        lambda: f"height={box['height']} (viewport={vp_h})"
        if box["height"] >= vp_h - VIEWPORT_FILL_SLOP_PX
        else (_ for _ in ()).throw(
            AssertionError(
                f"dialog height={box['height']} did not fill viewport "
                f"height={vp_h} (slop={VIEWPORT_FILL_SLOP_PX})"
            ),
        ),
    )

    # Bootstrap's fullscreen variant zeroes the dialog margin and
    # snaps the dialog to the top-left of the viewport.  A non-zero
    # x or y here would mean the centring behaviour kicked in
    # (regression: someone removed the class).
    check(
        results,
        "mobile_dialog_anchored_top_left",
        lambda: f"x={box['x']}, y={box['y']}"
        if abs(box["x"]) <= VIEWPORT_FILL_SLOP_PX
        and abs(box["y"]) <= VIEWPORT_FILL_SLOP_PX
        else (_ for _ in ()).throw(
            AssertionError(
                f"dialog not anchored at top-left: x={box['x']}, y={box['y']}"
            ),
        ),
    )

    # Bootstrap removes the modal-content border-radius for the
    # fullscreen variant.  A non-zero radius here would mean the
    # rules were overridden somewhere downstream.
    radius = _computed_style(
        page, "#addTransactionModal .modal-content", "border-top-left-radius",
    )
    check(
        results,
        "mobile_modal_content_no_rounded_corners",
        lambda: f"border-top-left-radius={radius!r}"
        if radius.strip() in ("0px", "0")
        else (_ for _ in ()).throw(
            AssertionError(
                f"modal-content border-top-left-radius={radius!r}, "
                f"expected 0px on fullscreen-sm-down"
            ),
        ),
    )


def verify_mobile_footer_is_sticky(
    page: Page, results: list[CheckResult],
) -> None:
    """The ``.modal-fullscreen-sm-down .modal-footer`` rule in
    app/static/css/grid.css sets the modal-footer to
    ``position: sticky; bottom: 0`` at < 576 px so the Save button
    stays reachable above the iOS on-screen keyboard."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    _open_add_transaction_modal(page)

    pos = _computed_style(
        page, "#addTransactionModal .modal-footer", "position",
    )
    check(
        results,
        "mobile_footer_position_sticky",
        lambda: f"position={pos!r}" if pos.strip() == "sticky"
        else (_ for _ in ()).throw(
            AssertionError(
                f"modal-footer position={pos!r}, expected 'sticky' "
                f"at viewport < {SM_BREAKPOINT_PX} px"
            ),
        ),
    )

    bottom = _computed_style(
        page, "#addTransactionModal .modal-footer", "bottom",
    )
    check(
        results,
        "mobile_footer_bottom_zero",
        lambda: f"bottom={bottom!r}" if bottom.strip() in ("0px", "0")
        else (_ for _ in ()).throw(
            AssertionError(
                f"modal-footer bottom={bottom!r}, expected 0px"
            ),
        ),
    )

    # The border-top is part of the same rule; confirms the whole
    # block applied (not just position/bottom).
    border_top = _computed_style(
        page, "#addTransactionModal .modal-footer", "border-top-style",
    )
    check(
        results,
        "mobile_footer_border_top_solid",
        lambda: f"border-top-style={border_top!r}"
        if border_top.strip() == "solid"
        else (_ for _ in ()).throw(
            AssertionError(
                f"modal-footer border-top-style={border_top!r}, "
                f"expected 'solid' (full rule from grid.css applied)"
            ),
        ),
    )

    shot(page, "02_mobile_footer_sticky")


def verify_mobile_amount_input_inputmode(
    page: Page, results: list[CheckResult],
) -> None:
    """The amount input inside the modal carries
    ``inputmode="decimal"`` (Commit 11 regression lock; the prompt
    explicitly calls this out as a Commit 14 verification gate)."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    _open_add_transaction_modal(page)

    mode = page.evaluate(
        """() => {
            const el = document.querySelector(
                '#addTransactionModal input[name="estimated_amount"]'
            );
            if (!el) return null;
            return el.getAttribute('inputmode');
        }"""
    )
    check(
        results,
        "mobile_amount_input_inputmode_decimal",
        lambda: f"inputmode={mode!r}" if mode == "decimal"
        else (_ for _ in ()).throw(
            AssertionError(
                f"estimated_amount input inputmode={mode!r}, "
                f"expected 'decimal'"
            ),
        ),
    )


def verify_desktop_dialog_centred(
    page: Page, results: list[CheckResult],
) -> None:
    """On the desktop viewport Bootstrap's ``modal-fullscreen-sm-down``
    rules do NOT apply (they are gated by
    ``@media (max-width: 575.98px)``).  The dialog falls back to
    Bootstrap's default centred layout: max-width ~500 px, non-zero
    margin, and the modal-footer is not sticky."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    _open_add_transaction_modal(page)
    shot(page, "03_desktop_modal_open")

    box = _dialog_box(page)
    vp_w = DESKTOP_VIEWPORT["width"]

    # Bootstrap's default modal-dialog max-width at >= sm is 500 px.
    # A width near the viewport width would mean fullscreen leaked
    # past its breakpoint.
    check(
        results,
        "desktop_dialog_not_fullscreen_width",
        lambda: f"width={box['width']} (viewport={vp_w})"
        if box["width"] < vp_w / 2
        else (_ for _ in ()).throw(
            AssertionError(
                f"dialog width={box['width']} >= half of viewport "
                f"{vp_w} -- fullscreen-sm-down leaked past breakpoint"
            ),
        ),
    )

    pos = _computed_style(
        page, "#addTransactionModal .modal-footer", "position",
    )
    check(
        results,
        "desktop_footer_position_not_sticky",
        lambda: f"position={pos!r}" if pos.strip() != "sticky"
        else (_ for _ in ()).throw(
            AssertionError(
                f"modal-footer position={pos!r}, expected non-sticky "
                f"at viewport >= {SM_BREAKPOINT_PX} px"
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
        # on a physical device; viewport width < 576 px makes
        # Bootstrap's modal-fullscreen-sm-down rules active.
        mobile_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=MOBILE_VIEWPORT,
            has_touch=True,
            is_mobile=True,
        )
        mobile_page = mobile_ctx.new_page()

        verify_dialog_has_fullscreen_class(mobile_page, results)
        verify_mobile_dialog_fills_viewport(mobile_page, results)
        verify_mobile_footer_is_sticky(mobile_page, results)
        verify_mobile_amount_input_inputmode(mobile_page, results)
        mobile_ctx.close()

        # Desktop context.  Wide viewport puts us above Bootstrap's
        # sm breakpoint so the fullscreen variant should not apply
        # and our custom sticky-footer rule should be out of scope.
        desktop_ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            viewport=DESKTOP_VIEWPORT,
            has_touch=False,
            is_mobile=False,
        )
        desktop_page = desktop_ctx.new_page()
        verify_desktop_dialog_centred(desktop_page, results)
        desktop_ctx.close()

        browser.close()

    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 14 mobile verification results")
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
