"""Headless mobile verification for Commit 7 of the v3 plan.

Drives Playwright Chromium at 375x812 (iPhone XS portrait) against the
local dev Flask server and asserts the per-card action bar behaviour
introduced in Commit 7 of the mobile-first v3 implementation:

  - Tap on a ``.mobile-txn-card`` opens its sibling
    ``.mobile-card-expansion`` collapse (no longer opens the bottom
    sheet directly).
  - The bar shows ``[Mark Paid]`` ``[Edit Amount]`` ``[Open Full]``
    on a Projected expense card; ``[Mark Paid]`` is absent on a
    Done / Settled card.
  - Tapping a second card collapses the first bar before opening
    the new one (single-open invariant).
  - ``[Mark Paid]`` posts to ``transactions.mark_done`` and the
    page reloads via the existing ``HX-Trigger: gridRefresh`` --
    the same card now carries the done badge.
  - ``[Edit Amount]`` swaps the cell content with the inline
    quick-edit input (existing ``transactions.get_quick_edit`` flow).
  - ``[Open Full]`` triggers the existing bottom-sheet popover via
    the delegated ``txn-expand-btn`` + ``data-txn-id`` handler in
    ``grid_edit.js``.
  - The Plan tab inherits the same action-bar behaviour through
    the shared ``_mobile_plan.html`` partial.

This script is the runtime counterpart to the static-render
coverage in
``tests/test_routes/test_grid/test_mobile_partials.py::TestMobileCardActionBar``:
pytest proves the partial emits the right HTML; this script proves
the headless browser routes taps through Bootstrap's Collapse API,
that HTMX wiring fires the right endpoints, and that the existing
bottom-sheet path still works through the new entry point.

Prerequisites:

- Playwright installed in the venv
  (``.venv/bin/pip install playwright``).
- Chromium headless-shell downloaded
  (``.venv/bin/playwright install chromium``).
- The dev Flask server running, bound to ``172.32.0.1:5000``
  (``flask run --host 172.32.0.1``).
- A logged-in session saved to ``tests/manual/.dev_session_state.json``
  via ``save_dev_session.py``.
- At least one Projected expense transaction visible in the
  current period (the "This Period" tab default) AND in the next
  period (the second panel of the "Plan" tab) so the
  tab-switching test can exercise an action bar there too.  A
  typical seeded user satisfies both via the standing templates.

The script is intentionally NOT a pytest test -- it needs an external
process (the dev server) and an external state file (the cookie), and
the OS-level Chromium dependency is not available in CI.

Usage::

    .venv/bin/python tests/manual/verify_mobile_grid_commit7.py

Exit code 0 = all assertions passed; non-zero = something failed.
Screenshots are written to ``tests/manual/screenshots/``.

Side effects: the Mark Paid check posts to
``/transactions/<id>/mark-done`` against the dev database and leaves
that transaction in the Done state.  Subsequent runs will skip that
transaction (it is no longer Projected) and pick the next available
one.  If you exhaust your Projected transactions, set up another
period or re-seed.
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

# Bootstrap's Collapse default transition is ~350 ms; pad to 500 ms
# so the show / hide animation finishes before the assertion runs.
COLLAPSE_TRANSITION_MS = 500


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
    path = SHOT_DIR / f"commit7_{label}.png"
    page.screenshot(path=str(path), full_page=True)


def _find_projected_card_id(page: Page, pane_selector: str) -> str:
    """Return the ``data-mobile-txn-id`` of the first Projected expense
    card in the given pane.

    Projected cards are those whose adjacent action-bar shows a Mark
    Paid form (the bar's existence implies ``status_id`` is not Done /
    Settled).  Returns the first match; raises ``AssertionError`` if
    nothing in the pane qualifies.
    """
    cards = page.locator(
        f"{pane_selector} .mobile-card-wrapper:has(.mobile-card-expansion form) "
        ".mobile-txn-card[data-mobile-txn-id]"
    )
    count = cards.count()
    if count == 0:
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


def _scope_card(page: Page, pane_selector: str, txn_id: str):
    """Return the card locator scoped to a single tab pane.

    Both ``#mobile-this-period`` and ``#mobile-plan`` render the same
    overlapping window of pay periods, so a bare
    ``.mobile-txn-card[data-mobile-txn-id="X"]`` selector matches two
    elements (one per tab) and Playwright's strict mode rejects it.
    Pane-scoping picks the one we mean.
    """
    return page.locator(
        f'{pane_selector} .mobile-txn-card[data-mobile-txn-id="{txn_id}"]'
    ).first


def _action_bar_for(card):
    """Return the action-bar locator that is the sibling of the given card.

    Mirrors the DOM walk that `mobile_grid.js`'s tap handler uses:
    `card.closest('.mobile-card-wrapper').querySelector('.mobile-card-expansion')`.
    This is ID-agnostic so it works regardless of the per-tab
    `id_prefix` (`tp` / `plan`) on the bar element.
    """
    wrapper = card.locator(
        'xpath=ancestor::div[contains(concat(" ", @class, " "), " mobile-card-wrapper ")][1]'
    )
    return wrapper.locator(".mobile-card-expansion").first


def _button_in_bar(card, button_selector: str):
    """Return a button locator scoped to the action bar of the given card.

    Keeps the harness independent of the per-tab `id_prefix` on the
    bar element by walking up to the wrapper and back down to the
    bar, rather than constructing `#card-expansion-<prefix>-<id>`
    directly.
    """
    return _action_bar_for(card).locator(button_selector)


def verify_tap_opens_action_bar(page: Page, results: list[CheckResult]) -> None:
    """Tap a Projected card; the sibling action bar collapses open."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")
    shot(page, "01_initial_load")

    # Sanity: the action-bar markup is present somewhere on the page
    # (lock the render_row_card -> include wiring).
    check(
        results,
        "action_bar_markup_present",
        lambda: f"count={page.locator('.mobile-card-expansion').count()}"
        if page.locator(".mobile-card-expansion").count() >= 1
        else (_ for _ in ()).throw(
            AssertionError("no .mobile-card-expansion in rendered DOM"),
        ),
    )

    # Locate a Projected card in the This Period pane and tap it.
    try:
        txn_id = _find_projected_card_id(page, "#mobile-this-period")
    except AssertionError as exc:
        results.append(CheckResult(
            name="found_projected_card",
            ok=False,
            detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="found_projected_card",
        ok=True,
        detail=f"txn_id={txn_id}",
    ))

    card = _scope_card(page, "#mobile-this-period", txn_id)
    action_bar = _action_bar_for(card)

    # Pre-tap: bar is collapsed (no `show` class).
    check(
        results,
        "bar_closed_before_tap",
        lambda: _assert_no_class_locator(action_bar, "show"),
    )

    card.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)
    shot(page, "02_after_tap_card")

    # Post-tap: bar carries `show`.
    check(
        results,
        "bar_open_after_tap",
        lambda: _assert_class_locator(action_bar, "show"),
    )

    # Three buttons present (Mark Paid form + Edit Amount + Open Full).
    check(
        results,
        "three_buttons_visible",
        lambda: _assert_button_labels(
            action_bar,
            expected={"Mark Paid", "Edit Amount", "Open Full"},
        ),
    )


def verify_second_tap_collapses_first(
    page: Page, results: list[CheckResult],
) -> None:
    """Tap card A then card B: A's bar collapses, B's bar opens."""
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")

    cards = page.locator(
        "#mobile-this-period .mobile-card-wrapper:has(.mobile-card-expansion) "
        ".mobile-txn-card[data-mobile-txn-id]"
    )
    if cards.count() < 2:
        results.append(CheckResult(
            name="two_cards_available",
            ok=False,
            detail=(
                f"need >=2 cards with action bars in This Period; got "
                f"{cards.count()}.  Seed more transactions before re-running."
            ),
        ))
        return
    results.append(CheckResult(
        name="two_cards_available",
        ok=True,
        detail=f"count={cards.count()}",
    ))

    card_a = cards.nth(0)
    card_b = cards.nth(1)
    action_bar_a = _action_bar_for(card_a)
    action_bar_b = _action_bar_for(card_b)

    card_a.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)
    check(
        results,
        "bar_a_open",
        lambda: _assert_class_locator(action_bar_a, "show"),
    )

    card_b.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)
    shot(page, "03_second_card_tapped")

    check(
        results,
        "bar_a_closed_after_b",
        lambda: _assert_no_class_locator(action_bar_a, "show"),
    )
    check(
        results,
        "bar_b_open_after_b",
        lambda: _assert_class_locator(action_bar_b, "show"),
    )


def verify_mark_paid_settles_card(
    page: Page, results: list[CheckResult],
) -> None:
    """Tap Mark Paid: the page reloads (gridRefresh) and the card
    shows the done badge.

    Note: this leaves the transaction in the Done state on the dev
    database.  Subsequent runs skip Done cards because
    ``_find_projected_card_id`` looks for cards whose bar carries a
    Mark Paid form (absent on Done / Settled).
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")

    try:
        txn_id = _find_projected_card_id(page, "#mobile-this-period")
    except AssertionError as exc:
        results.append(CheckResult(
            name="mark_paid_found_projected_card",
            ok=False,
            detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="mark_paid_found_projected_card",
        ok=True,
        detail=f"txn_id={txn_id}",
    ))

    card = _scope_card(page, "#mobile-this-period", txn_id)
    card.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)
    shot(page, "04_action_bar_before_mark_paid")

    # Tap the Mark Paid submit button.  The form's hx-post fires
    # mark_done; the route returns HX-Trigger: gridRefresh which
    # app.js routes to window.location.reload().
    mark_paid_btn = _button_in_bar(
        card,
        f'form[hx-post*="/transactions/{txn_id}/mark-done"] button[type="submit"]',
    )
    check(
        results,
        "mark_paid_button_present",
        lambda: "ok" if mark_paid_btn.count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected exactly 1 Mark Paid submit button for txn {txn_id}, "
                f"got {mark_paid_btn.count()}"
            ),
        ),
    )
    if mark_paid_btn.count() != 1:
        return

    mark_paid_btn.click()

    # The HX-Trigger gridRefresh causes window.location.reload().
    # Wait for the navigation to complete.
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(800)  # Padding for the reload to settle.
    shot(page, "05_after_mark_paid_reload")

    # Post-reload: the same txn-id card should no longer have a
    # Mark Paid form in its bar (Done badge appears in the cell).
    # Scope to This Period so we don't double-count the Plan tab.
    refreshed_card = _scope_card(page, "#mobile-this-period", txn_id)
    refreshed_bar_form = _button_in_bar(
        refreshed_card,
        f'form[hx-post*="/transactions/{txn_id}/mark-done"]',
    )
    check(
        results,
        "mark_paid_button_gone_after_reload",
        lambda: "ok" if refreshed_bar_form.count() == 0
        else (_ for _ in ()).throw(
            AssertionError(
                f"Mark Paid form still present for txn {txn_id} after reload; "
                f"the mark-done POST may have failed silently"
            ),
        ),
    )

    # The cell now shows the done badge (a `.badge-done` span emitted
    # by _transaction_cell.html for settled rows).  Since the desktop
    # grid is hidden at mobile viewport but still in the DOM, restrict
    # the search to the mobile branch's This Period pane.  The card's
    # display content in render_row_card uses `<span class="badge-done">`
    # for the settled badge.
    refreshed_badge = page.locator(
        f'#mobile-this-period .mobile-txn-card[data-mobile-txn-id="{txn_id}"] '
        f'.badge-done'
    )
    check(
        results,
        "done_badge_visible_on_card",
        lambda: f"count={refreshed_badge.count()}"
        if refreshed_badge.count() >= 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"no .badge-done child of mobile card for txn {txn_id} "
                f"after mark-done"
            ),
        ),
    )


def verify_edit_amount_swaps_cell(
    page: Page, results: list[CheckResult],
) -> None:
    """Tap Edit Amount: the cell content is replaced with the
    inline quick-edit input.

    The Edit Amount button's hx-get targets ``#txn-cell-<id>`` with
    ``hx-swap=innerHTML``.  The response is ``_transaction_quick_edit.html``
    which is a ``<form class="txn-quick-edit">`` containing a
    ``<input type="number" name="estimated_amount">``.

    The target id ``#txn-cell-<id>`` lives in the (hidden) desktop
    grid markup; the swap replaces THAT content with the quick-edit
    form.  Visual smoke on mobile is that the request fires and the
    response renders without an HTTP error; we assert the swap by
    inspecting the target div.
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")

    try:
        txn_id = _find_projected_card_id(page, "#mobile-this-period")
    except AssertionError as exc:
        results.append(CheckResult(
            name="edit_amount_found_projected_card",
            ok=False,
            detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="edit_amount_found_projected_card",
        ok=True,
        detail=f"txn_id={txn_id}",
    ))

    card = _scope_card(page, "#mobile-this-period", txn_id)
    card.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)

    edit_btn = _button_in_bar(
        card,
        f'button[hx-get*="/transactions/{txn_id}/quick-edit"]',
    )
    check(
        results,
        "edit_amount_button_present",
        lambda: "ok" if edit_btn.count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected exactly 1 Edit Amount button for txn {txn_id}, "
                f"got {edit_btn.count()}"
            ),
        ),
    )
    if edit_btn.count() != 1:
        return

    edit_btn.click()
    page.wait_for_timeout(400)  # HTMX swap settle.
    shot(page, "06_after_edit_amount_swap")

    # The target #txn-cell-<id> now contains a `.txn-quick-edit` form
    # with a numeric input.
    swap_form = page.locator(f'#txn-cell-{txn_id} form.txn-quick-edit')
    check(
        results,
        "quick_edit_form_in_target",
        lambda: "ok" if swap_form.count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"#txn-cell-{txn_id} did not receive a .txn-quick-edit "
                f"form swap; count={swap_form.count()}"
            ),
        ),
    )
    swap_input = page.locator(
        f'#txn-cell-{txn_id} input[type="number"][name="estimated_amount"]'
    )
    check(
        results,
        "quick_edit_input_present",
        lambda: "ok" if swap_input.count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"#txn-cell-{txn_id} has no numeric estimated_amount input "
                f"after Edit Amount swap"
            ),
        ),
    )


def verify_open_full_opens_bottom_sheet(
    page: Page, results: list[CheckResult],
) -> None:
    """Tap Open Full: the existing bottom-sheet popover opens.

    Open Full carries ``class="txn-expand-btn" data-txn-id="<id>"``
    which the delegated handler in ``grid_edit.js:482`` picks up and
    routes to ``openFullEdit``.  On mobile (<768px viewport),
    ``positionPopover`` creates a backdrop element and reveals the
    ``#txn-popover`` div by removing the ``d-none`` class.
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-this-period", state="attached")

    try:
        txn_id = _find_projected_card_id(page, "#mobile-this-period")
    except AssertionError as exc:
        results.append(CheckResult(
            name="open_full_found_projected_card",
            ok=False,
            detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="open_full_found_projected_card",
        ok=True,
        detail=f"txn_id={txn_id}",
    ))

    card = _scope_card(page, "#mobile-this-period", txn_id)
    card.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)

    open_full_btn = _button_in_bar(
        card,
        f'button.txn-expand-btn[data-txn-id="{txn_id}"]',
    )
    check(
        results,
        "open_full_button_present",
        lambda: "ok" if open_full_btn.count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                f"expected exactly 1 Open Full button for txn {txn_id}, "
                f"got {open_full_btn.count()}"
            ),
        ),
    )
    if open_full_btn.count() != 1:
        return

    open_full_btn.click()
    # The fetch + render path inside openFullEdit needs more time
    # than a pure CSS toggle.  Wait for the popover to lose `d-none`.
    page.wait_for_function(
        "() => { const el = document.getElementById('txn-popover');"
        "        return el && !el.classList.contains('d-none'); }",
        timeout=3000,
    )
    shot(page, "07_after_open_full")

    check(
        results,
        "bottom_sheet_visible",
        lambda: "ok" if not page.locator("#txn-popover").evaluate(
            "el => el.classList.contains('d-none')"
        )
        else (_ for _ in ()).throw(
            AssertionError("#txn-popover still has 'd-none' after Open Full"),
        ),
    )
    check(
        results,
        "bottom_sheet_backdrop_present",
        lambda: "ok" if page.locator("#bottom-sheet-backdrop").count() == 1
        else (_ for _ in ()).throw(
            AssertionError(
                "expected #bottom-sheet-backdrop after Open Full; "
                f"got count={page.locator('#bottom-sheet-backdrop').count()}"
            ),
        ),
    )


def verify_plan_tab_same_behavior(
    page: Page, results: list[CheckResult],
) -> None:
    """Switch to Plan tab; tap a card; assert the action bar opens.

    The Plan tab uses the same ``render_row_card`` macro and the same
    ``_mobile_card_actions.html`` partial as the This Period tab, so
    the structural and JS-toggle behaviour should be identical.
    """
    page.goto(f"{DEV_BASE_URL}/grid", wait_until="domcontentloaded")
    page.wait_for_selector("#mobile-plan", state="attached")

    page.locator("#mobile-tab-plan").click()
    page.wait_for_timeout(500)  # Bootstrap fade transition.
    shot(page, "08_plan_tab_active")

    try:
        txn_id = _find_projected_card_id(page, "#mobile-plan")
    except AssertionError as exc:
        results.append(CheckResult(
            name="plan_tab_found_projected_card",
            ok=False,
            detail=str(exc),
        ))
        return
    results.append(CheckResult(
        name="plan_tab_found_projected_card",
        ok=True,
        detail=f"txn_id={txn_id}",
    ))

    card = _scope_card(page, "#mobile-plan", txn_id)
    action_bar = _action_bar_for(card)

    card.click()
    page.wait_for_timeout(COLLAPSE_TRANSITION_MS)
    shot(page, "09_plan_tab_bar_open")

    check(
        results,
        "plan_tab_bar_open_after_tap",
        lambda: _assert_class_locator(action_bar, "show"),
    )


# ---- assertion helpers ----------------------------------------------------


def _assert_class_locator(locator, klass: str) -> str:
    """Confirm a Playwright locator's element has ``klass`` in classList."""
    cls = (locator.get_attribute("class") or "").split()
    if klass not in cls:
        raise AssertionError(
            f"expected class '{klass}', got: {cls}",
        )
    return f"class={cls}"


def _assert_no_class_locator(locator, klass: str) -> str:
    """Confirm a Playwright locator's element does NOT have ``klass``."""
    cls = (locator.get_attribute("class") or "").split()
    if klass in cls:
        raise AssertionError(
            f"unexpected class '{klass}': {cls}",
        )
    return f"class={cls}"


def _assert_button_labels(action_bar_locator, expected: set[str]) -> str:
    """Confirm the bar contains buttons / submits with each label.

    Looks at the text content of every ``button`` inside the bar.
    The Mark Paid label lives on a ``<button type="submit">`` inside
    a ``<form>``; Edit Amount and Open Full are bare ``<button
    type="button">``.  Text matches are substring (button labels may
    carry leading icon whitespace from ``<i class="bi">``).
    """
    buttons = action_bar_locator.locator("button")
    n = buttons.count()
    found = set()
    for i in range(n):
        text = (buttons.nth(i).inner_text() or "").strip()
        for label in expected:
            if label in text:
                found.add(label)
    missing = expected - found
    if missing:
        raise AssertionError(
            f"missing button labels {missing}; saw {n} buttons in bar"
        )
    return f"found {sorted(found)}"


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

        verify_tap_opens_action_bar(page, results)
        verify_second_tap_collapses_first(page, results)
        verify_edit_amount_swaps_cell(page, results)
        verify_open_full_opens_bottom_sheet(page, results)
        verify_plan_tab_same_behavior(page, results)
        # Mark Paid is the last check because it mutates state on the
        # dev database; running it earlier would shrink the available
        # Projected pool for subsequent checks.
        verify_mark_paid_settles_card(page, results)

        browser.close()

    # Print summary.
    width = max(len(r.name) for r in results) + 2
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    print()
    print("Commit 7 mobile verification results")
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
