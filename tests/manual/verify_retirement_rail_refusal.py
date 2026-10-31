"""Drive the /retirement rail's refused what-if in a real browser (S3-f-4).

Plan step salary:S3-f-4 (rulings **R-SAL33**, **R-SAL34**) made the readiness
what-if GET answer a refused input with the assumptions RAIL at 422 -- the
Save's own body, RETARGETED at ``#assumptions-region`` by htmx's
``HX-Retarget`` header because the request targets the readiness CARD -- and
made ``retirement_controls.js`` retire a row's refusal the moment one of its
what-if controls is edited.  pytest can see neither: the suite asserts the
headers and the body, and only a browser says where htmx PUT the body, that
the card kept its picture, that the page did not reload (the post-save
reload listens on the same region), that a keystroke cleared the message,
and that a corrected year then redrew the card.

**It writes nothing.**  No Save is pressed; the rail's stored end years are
read before and after and must match.  Run it against a clone all the same.

Preconditions:
  * the containerized dev app is up and answering on ``DEV_BASE_URL``
    (``docker compose -f docker-compose.dev.yml up -d`` from THIS checkout);
  * ``tests/manual/.dev_session_state.json`` exists and is unexpired
    (``python tests/manual/save_dev_session.py``);
  * the signed-in owner has at least one recurring raise on an active salary
    profile (the rail renders a row for it), else the script exits 2.

Usage:
    VERIFY_DEV_DATABASE=<the app's database> python tests/manual/verify_retirement_rail_refusal.py

Exit code 0 when every check passes, 1 on a failure, 2 when the preconditions
are not met.  Paced to the dev app's real Valkey limiter (30/minute per IP):
every field change fires one readiness GET after the 500 ms debounce.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright

#: Where the dev app answers -- the bridge-gateway bind ``save_dev_session.py``
#: scopes its cookie to.
DEV_BASE_URL = "http://172.32.0.1:5000"

#: The database THE APP IS POINTED AT (see ``verify_recurrence_form.py`` for
#: why this must not be hard-coded to ``shekel``).
DEV_DATABASE = os.environ.get("VERIFY_DEV_DATABASE", "shekel")

#: The storage state ``save_dev_session.py`` writes.
SESSION_STATE = Path(__file__).resolve().parent / ".dev_session_state.json"

#: The debounce is 500 ms and the readiness GET derives several pictures;
#: this is the wait for one change to be answered and swapped, and the run's
#: rate budget (about eight changes in all).
SETTLE_MS = 3000

#: One recurring-raise row of the rail (a Save form since salary:S3-f-3).
ROW = 'form[data-assumption="raise_terminal_year"]'

#: How long the in-flight step holds each readiness answer at the network
#: layer, so a keystroke can land while a request is out.  Longer than the
#: 500 ms debounce plus the in-browser keystroke at :data:`KEYSTROKE_AT_MS`.
HOLD_SECONDS = 1.2

#: When, after the first digit, the in-browser timer types the second one:
#: after the debounce has issued the first request and while its answer is
#: held.
KEYSTROKE_AT_MS = 900

#: When the in-browser timer records what the box shows: after the FIRST
#: answer would have landed on a tree that lets it, before the second one
#: can (the second request is issued at ~1400 ms and held 1.2 s).
OBSERVE_AT_MS = 2300

#: What Chromium and htmx each log for ONE designed 422 from the readiness
#: GET: the browser's resource line and htmx's ``htmx:responseError`` line.
#: Every refusal this script provokes is expected to produce exactly these
#: two, and nothing else is expected on the console at all.
DESIGNED_422_LINES = (
    "Failed to load resource: the server responded with a status of 422",
    "Response Status Error Code 422 from /retirement/readiness",
)

#: What htmx logs for ONE aborted request (both through its error logger):
#: the request's own afterRequest, raised as an error, and sendAbort.
DESIGNED_ABORT_LINES = ("htmx:afterRequest", "htmx:sendAbort")

_failures: list[str] = []


def _sql(query: str) -> list[str]:
    """Return the rows of one query against the dev database, pipe-separated."""
    completed = subprocess.run(
        ["docker", "exec", "shekel-dev-db", "psql", "-U", "shekel_user",
         "-d", DEV_DATABASE, "-tAF|", "-c", query],
        capture_output=True, text=True, check=True,
    )
    return [line for line in completed.stdout.strip().split("\n") if line]


def _check(label: str, passed: bool, detail: str = "") -> None:
    """Record one assertion and print it."""
    print(f"   {'PASS' if passed else 'FAIL'}  {label}"
          + ("" if passed else f" -- {detail}"))
    if not passed:
        _failures.append(f"{label}: {detail}")


def _row_state(page, raise_id: str) -> dict:
    """What the browser shows on one raise row: classes, message, values."""
    return page.evaluate(
        """(id) => {
            const row = document.querySelector(
                'form[data-assumption="raise_terminal_year"][data-raise-id="' + id + '"]');
            if (!row) return null;
            const year = row.querySelector('input[name="raise_end_year_' + id + '"]');
            const mode = row.querySelector('select[name="raise_end_mode_' + id + '"]');
            const feedback = row.querySelector('.invalid-feedback');
            return {
                yearInvalid: year.classList.contains('is-invalid'),
                modeInvalid: mode.classList.contains('is-invalid'),
                message: feedback ? feedback.textContent.trim() : null,
                year: year.value, mode: mode.value, min: year.min,
                focused: document.activeElement === year,
            };
        }""",
        raise_id,
    )


def _swr_state(page) -> dict:
    """The SWR box and its what-if mirror as the browser holds them."""
    return page.evaluate(
        """() => {
            const box = document.getElementById('assump-swr');
            const mirror = document.getElementById('swr-whatif-mirror');
            const feedback = box.closest('form').querySelector('.invalid-feedback');
            return {
                invalid: box.classList.contains('is-invalid'),
                value: box.value, mirror: mirror.value,
                message: feedback ? feedback.textContent.trim() : null,
            };
        }""",
    )


def _card(page) -> str:
    """The readiness card's markup -- what a refusal must leave untouched."""
    return page.evaluate(
        "() => document.getElementById('readiness-panel').innerHTML",
    )


def _has_deltas(page) -> bool:
    """Whether the card states a what-if delta (a probe that applied)."""
    return page.evaluate(
        "() => !!document.querySelector('[data-readiness=\"deltas\"]')",
    )


def _drive_refusal(page, raise_id: str, effective: int, at_load: dict) -> None:
    """1. A year before the raise's effective year: the rail comes back with
    the message on THIS row, retargeted -- the card untouched, no reload, the
    SWR box as it was, focus where it was."""
    page.select_option(f'select[name="raise_end_mode_{raise_id}"]', "year")
    page.wait_for_timeout(SETTLE_MS)
    page.fill(f'input[name="raise_end_year_{raise_id}"]', str(effective - 1))
    page.wait_for_timeout(SETTLE_MS)
    refused = _row_state(page, raise_id)
    _check("the refused year is marked invalid on its row", refused["yearInvalid"], str(refused))
    _check("the row carries the ONE end-year rule's sentence",
           bool(refused["message"]) and "cannot end before it starts" in refused["message"],
           str(refused["message"]))
    _check("the refused year is echoed", refused["year"] == str(effective - 1), refused["year"])
    _check("the submitted mode is echoed", refused["mode"] == "year", refused["mode"])
    _check("the readiness card kept its picture", _card(page) == at_load["card"])
    _check("the page did not reload on the 422", bool(page.evaluate("() => window.__driveMarker")))
    _check("the SWR box was not reset by the refusal",
           _swr_state(page)["value"] == at_load["swr"]["value"], str(_swr_state(page)))
    _check("focus survived the rail swap", refused["focused"], "the year box lost focus")


def _drive_correction(
    page, raise_id: str, effective: int, probe_year: int, at_load: dict,
) -> None:
    """2. Editing the refused control retires the message BEFORE any answer
    (R-SAL34; the DOM is read at once, no wait); 3. a year the rule accepts
    redraws the card at the probe."""
    page.type(f'input[name="raise_end_year_{raise_id}"]', "0")
    at_once = _row_state(page, raise_id)
    _check("the refusal retired on the first keystroke", not at_once["yearInvalid"]
           and at_once["message"] is None, str(at_once))
    # htmx restored focus to the swapped-in box by id; a number input has no
    # selection to restore, so WHERE the caret landed is the browser's call
    # and only a keystroke can say: at the end, the digit appends.
    _check("the caret survived the rail swap at the end of the value",
           at_once["year"] == f"{effective - 1}0", at_once["year"])
    page.fill(f'input[name="raise_end_year_{raise_id}"]', str(probe_year))
    page.wait_for_timeout(SETTLE_MS)
    accepted = _row_state(page, raise_id)
    _check("an accepted year leaves the row clean", not accepted["yearInvalid"]
           and accepted["message"] is None, str(accepted))
    _check("the card redrew at the accepted probe", _card(page) != at_load["card"])
    _check("the card states the what-if delta", _has_deltas(page))


def _drive_swr(page, raise_id: str, at_load: dict) -> None:
    """4. The SWR box: 150 is refused on the box (the ``max`` fences only a
    form submit); the raise row stays clean; restoring it clears at once."""
    page.fill("#assump-swr", "150")
    page.wait_for_timeout(SETTLE_MS)
    swr = _swr_state(page)
    _check("an SWR outside its range is refused on the box", swr["invalid"]
           and bool(swr["message"]), str(swr))
    _check("the refused SWR is echoed in the box and its mirror",
           swr["value"] == "150" and swr["mirror"] == "150", str(swr))
    _check("the raise row is clean beside the SWR refusal",
           not _row_state(page, raise_id)["yearInvalid"])
    # The clear on edit is presentation only: a value still outside the range
    # is refused again by the server's next answer (R-SAL34).
    page.fill("#assump-swr", "151")
    _check("the SWR refusal retired on edit", not _swr_state(page)["invalid"])
    page.wait_for_timeout(SETTLE_MS)
    _check("the server re-states a refusal the edit retired while the value is still bad",
           _swr_state(page)["invalid"], str(_swr_state(page)))
    page.fill("#assump-swr", at_load["swr"]["value"])
    _check("the SWR refusal retired on the correcting edit", not _swr_state(page)["invalid"])
    page.wait_for_timeout(SETTLE_MS)


def _drive_in_flight(page, raise_id: str, probe_year: int) -> None:
    """4b. A superseded request's answer never lands (an adversarial review
    of S3-f-4).  Every readiness answer is held :data:`HOLD_SECONDS` at the
    network layer; the script types "2" (refused: before 2000), an in-browser
    timer types "0" while that answer is held, and a second timer records the
    box after the first answer would have landed.  On a tree that lets it
    land, the box reads "2" again -- the "0" clobbered -- until the answer
    for "20" arrives; on this tree the keystroke aborted the request."""
    year_box = f'input[name="raise_end_year_{raise_id}"]'
    aborted: list[str] = []
    page.on("requestfailed", lambda request: aborted.append(request.url)
            if "/retirement/readiness" in request.url else None)

    def hold(route):
        time.sleep(HOLD_SECONDS)
        try:
            route.continue_()
        except PlaywrightError:
            pass  # the browser aborted this request while it was held

    page.route(lambda url: "/retirement/readiness" in url, hold)
    page.evaluate(
        """([selector, keystrokeAt, observeAt]) => {
            window.__midway = null;
            setTimeout(() => {
                const box = document.querySelector(selector);
                box.value = box.value + "0";
                box.dispatchEvent(new Event("input", { bubbles: true }));
            }, keystrokeAt);
            setTimeout(() => {
                window.__midway = document.querySelector(selector).value;
            }, observeAt);
        }""",
        [year_box, KEYSTROKE_AT_MS, OBSERVE_AT_MS],
    )
    page.fill(year_box, "2")
    page.wait_for_timeout(OBSERVE_AT_MS + 2 * int(HOLD_SECONDS * 1000) + SETTLE_MS)
    midway = page.evaluate("() => window.__midway")
    _check("the keystroke aborted the request in flight", len(aborted) == 1,
           f"{len(aborted)} aborted readiness requests")
    _check("the held answer for the first digit never put it back in the box",
           midway == "20", f"box read {midway!r} midway")
    final = _row_state(page, raise_id)
    _check("the answer that landed is the latest input's, refused on the row",
           final["year"] == "20" and final["yearInvalid"], str(final))
    page.unroute(lambda url: "/retirement/readiness" in url, hold)
    page.fill(year_box, str(probe_year))
    page.wait_for_timeout(SETTLE_MS)
    _check("the corrected year is accepted after the in-flight sequence",
           not _row_state(page, raise_id)["yearInvalid"] and _has_deltas(page))


def _drive_restore(page, raise_id: str, before: dict) -> None:
    """5. The stored pair, submitted verbatim, is the stored plan again."""
    page.select_option(f'select[name="raise_end_mode_{raise_id}"]', before["mode"])
    page.fill(f'input[name="raise_end_year_{raise_id}"]', before["year"])
    page.wait_for_timeout(SETTLE_MS)
    _check("the restored pair is the stored plan again", not _has_deltas(page))


def _drive(page) -> tuple[int, int]:
    """The scripted session; see the module docstring for what each step grades.

    Returns:
        ``(refusals that landed, requests aborted)``, for the console check.
    """
    page.goto(f"{DEV_BASE_URL}/retirement", wait_until="domcontentloaded")
    row = page.query_selector(ROW)
    if row is None:
        print("The rail renders no recurring-raise row for this owner; "
              "record a recurring raise first.", file=sys.stderr)
        sys.exit(2)
    raise_id = row.get_attribute("data-raise-id")
    before = _row_state(page, raise_id)
    stored_end_years = _sql("SELECT id, coalesce(terminal_year::text, '') "
                            "FROM salary.salary_raises ORDER BY id")
    at_load = {"card": _card(page), "swr": _swr_state(page)}
    page.evaluate("() => { window.__driveMarker = true; }")
    effective = int(before["min"])
    # A probe equal to the stored year IS the stored plan (no delta line), so
    # the accepted year is the effective year unless that is what is stored.
    probe_year = effective if before["year"] != str(effective) else effective + 1
    print(f"\n=== raise {raise_id}: effective {effective}, stored mode "
          f"{before['mode']!r}, year {before['year']!r}; probing {probe_year} ===")

    _drive_refusal(page, raise_id, effective, at_load)
    _drive_correction(page, raise_id, effective, probe_year, at_load)
    _drive_swr(page, raise_id, at_load)
    _drive_in_flight(page, raise_id, probe_year)
    _drive_restore(page, raise_id, before)

    after = _sql("SELECT id, coalesce(terminal_year::text, '') "
                 "FROM salary.salary_raises ORDER BY id")
    _check("no end year was written by any what-if", after == stored_end_years,
           f"{stored_end_years} -> {after}")
    # The refusals that LANDED: the year before the effective year, the SWR
    # at 150 and again at 151, the in-flight step's "20" (its "2" was aborted
    # and its answer dropped: the one abort), and -- when the stored answer
    # was "no end year" -- the "ends after" selected over an empty year box,
    # which the rule refuses too.
    return 4 + (1 if before["mode"] == "none" else 0), 1


def main() -> int:
    """Drive the rail and report."""
    if not SESSION_STATE.exists():
        print(f"No saved session at {SESSION_STATE}.\n"
              "Run: python tests/manual/save_dev_session.py", file=sys.stderr)
        return 2

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(storage_state=str(SESSION_STATE))
        page = context.new_page()
        console: list[str] = []
        page.on("pageerror", lambda e: console.append(str(e)))
        page.on("console",
                lambda m: console.append(m.text) if m.type == "error" else None)

        page.goto(f"{DEV_BASE_URL}/retirement", wait_until="domcontentloaded")
        if "/login" in page.url:
            print("Session expired. Re-run save_dev_session.py", file=sys.stderr)
            browser.close()
            return 2

        provoked, aborts = _drive(page)

        # A designed 422 is reported by the browser and by htmx, two lines
        # per refusal, and an abort by htmx, two lines per abort; both are
        # counted, not excused.  Anything else -- a blocked inline style, a
        # script error, a 500, a 429 from the limiter -- fails the run.
        designed = [m for m in console if m.startswith(DESIGNED_422_LINES)]
        aborted = [m for m in console if m in DESIGNED_ABORT_LINES]
        other = [m for m in console
                 if not m.startswith(DESIGNED_422_LINES) and m not in DESIGNED_ABORT_LINES]
        print(f"\n=== console: {len(designed)} designed-422 lines, "
              f"{len(aborted)} abort lines, {len(other)} other ===")
        for message in other:
            print(f"   {message}")
        _check(f"the {provoked} provoked refusals are the only 422s reported",
               len(designed) == 2 * provoked, f"{len(designed)} lines")
        _check(f"the {aborts} abort is the only one reported",
               len(aborted) == 2 * aborts, f"{len(aborted)} lines")
        _check("no other console errors", not other, "; ".join(other[:2]))
        browser.close()

    print("\n" + "=" * 62)
    if _failures:
        print(f"FAILURES ({len(_failures)}):")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
