"""Drive the deduction form's cadence controls in a real browser (salary:R15-c).

Plan step **salary:R15-c** (ruling **R-SAL31**) gave the paycheck-deduction
form on the salary edit page the shared recurrence cadence controls --
``recurrence_cadence_controls`` from ``_recurrence_fields.html``, linked by
``app/static/js/recurrence_form.js`` -- in place of the 26 / 24 / 12 select.
What the script decides is which rows are VISIBLE and which inputs are
ENABLED, and pytest can see neither: a control hidden by a class, a control
disabled by the script and a control that never appears all look alike in
rendered HTML, and only a real ``FormData`` says what a browser would post.

**Three things about THIS form that the template forms' drive
(``verify_recurrence_form.py``) cannot see**, each a way a green suite could
ship a dead control:

1. the form is ONE inline add/edit form, filled for an edit by ``app.js``
   from the row's ``data-line-*`` attributes and dispatching one ``change`` so
   the recurrence script re-links -- a prefill that set the unit AFTER the
   placement, or dispatched nothing, would show a monthly line as
   "Does not repeat" and save the deletion of its rule;
2. the whole ``#lines-section`` -- form included -- is swapped by htmx
   after every add, edit and delete, so the recurrence script has to RE-RUN
   for the new elements or the next add posts no interval and is refused;
3. the form places the partial's SPAN rows -- "Starts on" and the closing
   bound, inside ``#recurrence-fields`` (plan step salary:R18-c, ruling
   R-SAL38 (2)) -- but no due day and no preview, so the script runs with
   the container and without those two; a "Does not repeat" save must post
   NOTHING under the span keys, a cadence must show the rows, and the "Starts
   on" box must open BLANK (blank is the opening payday).

It is paced to the real Valkey limiter the dev app runs (30 a minute per IP),
exactly as its sibling is.

**It WRITES, unlike its sibling, and only when asked to**: the swap in (2)
exists only after a real add, so ``VERIFY_WRITE=1`` adds one line marked
:data:`MARK` through the real form, drives the swapped-in form and the new
row's edit, changes its cadence, and deletes it -- and the cleanup pass
removes anything marked that an aborted run left behind.  Run it against a
CLONE of the dev database (``CREATE DATABASE ... TEMPLATE shekel``; point the
app at it and set ``VERIFY_DEV_DATABASE`` to its name), never against
``shekel``.  Without the flag it drives the add form and the existing rows'
edit prefill and writes nothing.

Preconditions:
  * the containerized dev app is up and answering on ``DEV_BASE_URL`` from
    THIS checkout (the bind mount serves the code compose was run from);
  * ``tests/manual/.dev_session_state.json`` exists and is unexpired
    (``python tests/manual/save_dev_session.py``);
  * the session's user owns at least one active salary profile.

Usage:
    python tests/manual/verify_line_cadence_form.py
    VERIFY_WRITE=1 VERIFY_DEV_DATABASE=<clone> python tests/manual/verify_line_cadence_form.py

Exit code 0 when every check passes, 1 on a failure, 2 when the preconditions
are not met.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

import verify_recurrence_form as shared
from verify_recurrence_form import (
    DEV_BASE_URL,
    POST_SPACING_SECONDS,
    SESSION_STATE,
    _check,
    _settle,
    _sql,
    _unit_ids,
    _visible,
)

#: Marks every row this script creates, so the cleanup pass can find them
#: all even after an aborted run.
MARK = "ZZVERIFY-DEDUCTION"

#: Whether the write pass runs.  See the module docstring.
WRITE = os.environ.get("VERIFY_WRITE") == "1"


def _posted(page) -> dict[str, list[str]]:
    """Return every cadence key the deduction form would actually submit.

    From a real ``FormData``, for the reason the sibling drive reads one: a
    control hidden by a class still SUBMITS and a disabled one does not.
    The span keys are read too (plan step salary:R18-c): absent under "Does
    not repeat", present -- ``starts_on`` blank unless typed, the bound's
    mode ``never`` with neither value input posting -- once a cadence is
    chosen.

    Args:
        page: The Playwright page.

    Returns:
        Each key mapped to every value posted under it.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('line-form');
            const data = new FormData(form);
            return {
                recurrence_unit: data.getAll('recurrence_unit'),
                interval_n: data.getAll('interval_n'),
                recurrence_placement: data.getAll('recurrence_placement'),
                max_per_month: data.getAll('max_per_month'),
                starts_on: data.getAll('starts_on'),
                recurrence_end_mode: data.getAll('recurrence_end_mode'),
                end_date: data.getAll('end_date'),
                max_occurrences: data.getAll('max_occurrences'),
            };
        }"""
    )


def _open_add_form(page) -> None:
    """Reveal the collapsed add form the way the user does: the Add Deduction button.

    The button also RESETS the form (``data-line-reset``), which is the path
    under test after an edit: a reset form must post what a fresh one posts.
    The form sits in a Bootstrap ``collapse``, so every wait for its controls
    in this file is for ATTACHED, never visible: the first run of this drive
    timed out waiting for a hidden unit select to become visible on its own.

    Args:
        page: The Playwright page.
    """
    page.click('[data-toggle-target="add-line-form"]')
    page.wait_for_timeout(400)
    if not page.evaluate(
        """() => document.getElementById('add-line-form')
                 .classList.contains('show')"""
    ):
        page.click('[data-toggle-target="add-line-form"]')
        page.wait_for_timeout(400)


def _drive_add_form(page, profile_id: int) -> None:
    """Check the add form's cadence controls show, hide and post as the script links them."""
    print(f"\n=== deduction add form: /salary/{profile_id}/edit ===")
    page.goto(f"{DEV_BASE_URL}/salary/{profile_id}/edit", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit", state="attached")
    _open_add_form(page)
    units = _unit_ids(page)
    _check("A: the form offers paychecks, months AND years (R-SAL37)",
           {"paychecks", "months", "years"} <= set(units), str(sorted(units)))
    _check("A: the #recurrence-fields container holds the span rows (R18-c)",
           page.evaluate(
               """() => ['recurrence-fields', 'starts_on', 'recurrence_end_mode',
                         'end_date', 'max_occurrences', 'nominal_day']
                     .every(id => document.getElementById(id) !== null)"""),
           "a span control is missing")
    _check("A: no due day and no preview control is rendered",
           page.evaluate(
               """() => ['due_day_of_month', 'field-due-dom', 'recurrence-preview']
                     .every(id => document.getElementById(id) === null)"""),
           "one of them exists")
    _check("A: the Starts on box opens BLANK (blank is the opening payday)",
           page.evaluate("() => document.getElementById('starts_on').value === ''"),
           "a date is prefilled")

    # --- does not repeat: the dependent rows hidden, nothing posted ------
    posted = _posted(page)
    _check("A: 'Does not repeat' is selected on a fresh form",
           posted["recurrence_unit"] == [""], str(posted))
    _check("A: the interval row is hidden and posts nothing",
           not _visible(page, "field-interval") and posted["interval_n"] == [],
           str(posted))
    _check("A: the ceiling row is hidden and posts nothing",
           not _visible(page, "field-max-per-month") and posted["max_per_month"] == [],
           str(posted))
    _check("A: the placement posts nothing beside no unit (disabled, not merely hidden)",
           posted["recurrence_placement"] == [], str(posted))
    _check("A: nothing posts under the span keys while 'Does not repeat'",
           posted["starts_on"] == [] and posted["recurrence_end_mode"] == []
           and posted["end_date"] == [] and posted["max_occurrences"] == [],
           str(posted))
    _check("A: the span rows are hidden while 'Does not repeat'",
           not _visible(page, "recurrence-fields"), "the container is visible")

    # --- paychecks: interval + ceiling shown, one inert placement -------
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    posted = _posted(page)
    _check("A: paychecks shows the span rows; the blank start posts empty, the bound posts never",
           _visible(page, "recurrence-fields") and posted["starts_on"] == [""]
           and posted["recurrence_end_mode"] == ["never"]
           and posted["end_date"] == [] and posted["max_occurrences"] == [],
           str(posted))
    page.locator("#recurrence_end_mode").select_option("on_date")
    _settle(page)
    posted = _posted(page)
    _check("A: choosing 'on a date' reveals the date box alone and posts it (empty until typed)",
           _visible(page, "field-end-date") and posted["end_date"] == [""]
           and posted["max_occurrences"] == [], str(posted))
    page.locator("#recurrence_end_mode").select_option("never")
    _settle(page)
    _check("A: paychecks shows the interval row, posting 1",
           _visible(page, "field-interval") and posted["interval_n"] == ["1"],
           str(posted))
    _check("A: paychecks shows the ceiling row; an untouched box posts an empty ceiling",
           _visible(page, "field-max-per-month") and posted["max_per_month"] == [""],
           str(posted))
    _check("A: paychecks posts exactly one placement",
           len(posted["recurrence_placement"]) == 1, str(posted))
    page.fill("#max_per_month", "2")
    page.evaluate(
        """() => document.getElementById('max_per_month')
                 .dispatchEvent(new Event('change', {bubbles: true}))"""
    )
    _settle(page)
    _check("A: a typed ceiling of 2 posts exactly once",
           _posted(page)["max_per_month"] == ["2"], str(_posted(page)))

    # --- months: ceiling hidden AND disabled, two placements to choose ---
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    posted = _posted(page)
    _check("A: months hides the ceiling row and posts NO ceiling",
           not _visible(page, "field-max-per-month") and posted["max_per_month"] == [],
           str(posted))
    enabled_placements = page.evaluate(
        """() => Array.from(document.getElementById('recurrence_placement').options)
                 .filter(o => !o.disabled).length"""
    )
    _check("A: months offers two funding placements",
           enabled_placements == 2, str(enabled_placements))
    _check("A: the placement row is shown for months",
           _visible(page, "field-placement"), "hidden")

    # --- years: the same shape as months ---------------------------------
    page.locator("#recurrence_unit").select_option(units["years"])
    _settle(page)
    posted = _posted(page)
    _check("A: years hides the ceiling row and posts NO ceiling",
           not _visible(page, "field-max-per-month") and posted["max_per_month"] == [],
           str(posted))
    _check("A: years posts an interval and a placement",
           posted["interval_n"] == ["1"] and len(posted["recurrence_placement"]) == 1,
           str(posted))

    # --- back to paychecks: the ceiling typed before the detour survives -
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    _check("A: the typed ceiling survived the months/years detour",
           _posted(page)["max_per_month"] == ["2"], str(_posted(page)))

    # --- the Add Deduction toggle RESETS the form, script state included -
    page.click('[data-toggle-target="add-line-form"]')
    page.wait_for_timeout(400)
    page.click('[data-toggle-target="add-line-form"]')
    _settle(page)
    posted = _posted(page)
    _check("A: after the reset the unit is 'Does not repeat' again",
           posted["recurrence_unit"] == [""], str(posted))
    _check("A: after the reset the interval row is hidden and posts nothing",
           not _visible(page, "field-interval") and posted["interval_n"] == [],
           str(posted))
    _check("A: after the reset the ceiling row is hidden and posts nothing",
           not _visible(page, "field-max-per-month") and posted["max_per_month"] == [],
           str(posted))


def _rows(page) -> list[dict[str, str]]:
    """Return every line row's id, its Frequency cell and its prefill attributes, span included."""
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-line-edit]')).map(b => ({
            id: b.dataset.lineEdit,
            name: b.dataset.lineName,
            unit: b.dataset.lineUnitId,
            unit_id: b.dataset.lineUnitId,
            interval: b.dataset.lineInterval,
            placement: b.dataset.linePlacementId,
            ceiling: b.dataset.lineMaxPerMonth,
            starts_on: b.dataset.lineStartsOn,
            end_mode: b.dataset.lineEndMode,
            end_date: b.dataset.lineEndDate,
            max_occurrences: b.dataset.lineMaxOccurrences,
            phrase: document.querySelector('[data-line-cadence="' + b.dataset.lineEdit + '"]')
                    .textContent.trim(),
        }))"""
    )


def _drive_edit_prefill(page, row: dict[str, str], label: str) -> None:
    """Click one row's edit button and check the controls start on its cadence."""
    page.click(f'[data-line-edit="{row["id"]}"]')
    _settle(page)
    posted = _posted(page)
    _check(f"{label}: the unit control starts on the row's unit",
           posted["recurrence_unit"] == [row["unit"]], str(posted))
    if row["unit"]:
        _check(f"{label}: the interval row is shown and posts the row's interval",
               _visible(page, "field-interval") and posted["interval_n"] == [row["interval"]],
               str(posted))
        _check(f"{label}: the placement posts the row's placement",
               posted["recurrence_placement"] == [row["placement"]], str(posted))
        if row["ceiling"]:
            _check(f"{label}: the ceiling row is shown and posts the row's ceiling",
                   _visible(page, "field-max-per-month")
                   and posted["max_per_month"] == [row["ceiling"]],
                   str(posted))
        else:
            _check(f"{label}: no ceiling is posted for a unit that cannot hold one, "
                   f"or an empty one where it can",
                   posted["max_per_month"] in ([], [""]), str(posted))
    else:
        _check(f"{label}: a line with no rule posts no interval and no ceiling",
               posted["interval_n"] == [] and posted["max_per_month"] == [],
               str(posted))
    _check(f"{label}: the prefill posts the row's start (blank for a line with no rule)",
           posted["starts_on"] == ([row["starts_on"]] if row["unit_id"] else []),
           str(posted))
    _check(f"{label}: the prefill posts the row's bound mode",
           posted["recurrence_end_mode"] == ([row["end_mode"]] if row["unit_id"] else []),
           str(posted))
    # The bound's VALUE: the one input its shape needs posts the row's value
    # and the other posts nothing (disabled, not merely hidden) -- and under
    # "Does not repeat" neither posts.  app.js fills the value AFTER the unit's
    # change enables the input the mode revealed (plan step salary:R18-c).
    wanted_end_date = [row["end_date"]] if row["unit_id"] and row["end_mode"] == "on_date" else []
    wanted_count = (
        [row["max_occurrences"]] if row["unit_id"] and row["end_mode"] == "after_n" else []
    )
    _check(f"{label}: the prefill posts the bound's value under its own shape alone",
           posted["end_date"] == wanted_end_date and posted["max_occurrences"] == wanted_count,
           str(posted))
    _check(f"{label}: the submit button reads Update",
           "Update" in page.inner_text("#line-submit-btn"), page.inner_text("#line-submit-btn"))


def _drive_existing_rows(page, profile_id: int) -> None:
    """Check every existing line's edit prefill, whatever cadence it carries."""
    print(f"\n=== deduction edit prefill: /salary/{profile_id}/edit ===")
    page.goto(f"{DEV_BASE_URL}/salary/{profile_id}/edit", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit", state="attached")
    rows = _rows(page)
    _check("E: the page lists at least one deduction row", bool(rows), "no rows")
    for row in rows:
        _drive_edit_prefill(page, row, f"E[{row['name']!r} / {row['phrase']}]")
        # Back to add mode between rows, the way the user gets there.
        page.click('[data-toggle-target="add-line-form"]')
        page.wait_for_timeout(400)


def _fill_line(page, name: str, units: dict[str, str], ceiling: str) -> None:
    """Fill the non-cadence controls and a paychecks-with-ceiling cadence."""
    page.fill('#line-form [name=name]', name)
    page.fill('#line-form [name=amount]', "1.00")
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    page.fill("#max_per_month", ceiling)
    page.evaluate(
        """() => document.getElementById('max_per_month')
                 .dispatchEvent(new Event('change', {bubbles: true}))"""
    )
    _settle(page)


def _submit_and_settle(page) -> None:
    """Submit the deduction form through htmx and wait for the section to be swapped."""
    page.click("#line-submit-btn")
    page.wait_for_timeout(int(POST_SPACING_SECONDS * 1000))
    page.wait_for_selector("#recurrence_unit", state="attached")


def _drive_write_pass(page, profile_id: int) -> None:
    """Add, re-cadence and delete one marked line through the real form and its swaps."""
    print(f"\n=== deduction write pass (VERIFY_WRITE=1): /salary/{profile_id}/edit ===")
    page.goto(f"{DEV_BASE_URL}/salary/{profile_id}/edit", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit", state="attached")
    _open_add_form(page)
    units = _unit_ids(page)
    before = len(_rows(page))

    # --- add: the 24 shape, through the real submit --------------------
    _fill_line(page, MARK, units, "2")
    _submit_and_settle(page)
    rows = [r for r in _rows(page) if r["name"] == MARK]
    _check("W: the add swapped the section in with the new row", len(rows) == 1,
           f"{len(rows)} marked rows; {len(_rows(page))} rows (was {before})")
    if not rows:
        return
    added = rows[0]
    _check("W: the new row's Frequency cell reads the 24 shape",
           added["phrase"] == "Every paycheck (at most 2 a month)", added["phrase"])
    _check("W: the new row carries its prefill (paychecks, 1, ceiling 2)",
           added["unit"] == units["paychecks"] and added["interval"] == "1"
           and added["ceiling"] == "2", str(added))
    stored = _sql(
        "SELECT r.max_per_month, r.starts_on FROM budget.recurrence_rules r "
        "JOIN salary.paycheck_lines d ON d.id = r.paycheck_line_id "
        f"WHERE d.name = '{MARK}'",
    )
    _check("W: one rule with a ceiling of 2 was written for the line",
           len(stored) == 1 and stored[0].startswith("2|"), str(stored))

    # --- the SWAPPED-IN add form is linked: the script re-ran ------------
    _open_add_form(page)
    posted = _posted(page)
    _check("W: the swapped-in add form opens on 'Does not repeat' with nothing else posted",
           posted["recurrence_unit"] == [""] and posted["interval_n"] == []
           and posted["max_per_month"] == [], str(posted))
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    posted = _posted(page)
    _check("W: after the swap, choosing paychecks shows the interval row and posts 1",
           _visible(page, "field-interval") and posted["interval_n"] == ["1"], str(posted))
    _check("W: after the swap, choosing paychecks shows the ceiling row",
           _visible(page, "field-max-per-month"), "hidden")

    # --- give the new row a SPAN through its edit form (R18-c) -----------
    # A typed start on a paycheck cadence is placed on the payday of the
    # period holding it, so the stored start is read back off the row rather
    # than compared to what was typed; the stop is stored as typed.
    _drive_edit_prefill(page, added, "W[edit prefill after swap]")
    page.fill("#starts_on", "2026-10-01")
    page.evaluate(
        """() => document.getElementById('starts_on')
                 .dispatchEvent(new Event('change', {bubbles: true}))"""
    )
    _settle(page)
    page.locator("#recurrence_end_mode").select_option("on_date")
    _settle(page)
    page.fill("#end_date", "2026-11-30")
    _settle(page)
    posted = _posted(page)
    _check("W: the span edit posts the typed start, the on_date mode and the date alone",
           posted["starts_on"] == ["2026-10-01"] and posted["recurrence_end_mode"] == ["on_date"]
           and posted["end_date"] == ["2026-11-30"] and posted["max_occurrences"] == [],
           str(posted))
    _submit_and_settle(page)
    rows = [r for r in _rows(page) if r["name"] == MARK]
    _check("W: the span edit swapped the section in", len(rows) == 1, str(rows))
    if not rows:
        return
    spanned = rows[0]
    stored = _sql(
        "SELECT r.starts_on, r.end_date, r.max_occurrences, r.max_per_month "
        "FROM budget.recurrence_rules r "
        "JOIN salary.paycheck_lines d ON d.id = r.paycheck_line_id "
        f"WHERE d.name = '{MARK}'",
    )
    _check("W: the SAME rule now carries a start on or before Oct 1, the stop, no count, ceiling 2",
           len(stored) == 1 and stored[0].split("|")[0] <= "2026-10-01"
           and stored[0].split("|")[0] >= "2026-09-01"
           and stored[0].endswith("|2026-11-30||2"), str(stored))
    _check("W: the row's prefill carries the stored start, the on_date mode and the date",
           spanned["starts_on"] == stored[0].split("|")[0] and spanned["end_mode"] == "on_date"
           and spanned["end_date"] == "2026-11-30" and spanned["max_occurrences"] == "",
           str(spanned))
    _check("W: the row's Frequency cell words the span beside the cadence",
           spanned["phrase"].startswith("Every paycheck (at most 2 a month), from ")
           and spanned["phrase"].endswith(", until Nov 30, 2026"), spanned["phrase"])
    # The edit prefill of a SPANNED row: start, mode and the date, one dispatch.
    _drive_edit_prefill(page, spanned, "W[edit prefill of the spanned row]")
    page.click('[data-toggle-target="add-line-form"]')
    page.wait_for_timeout(400)

    # --- edit the new row to monthly, through the swapped-in form -------
    # With the start box CLEARED and the bound put back to never: a cleared
    # box is the derived default again (the 1st of the opening's month), and
    # the stop goes with the mode -- the stored row says both.
    _drive_edit_prefill(page, spanned, "W[edit prefill before the monthly edit]")
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    page.fill("#starts_on", "")
    page.evaluate(
        """() => document.getElementById('starts_on')
                 .dispatchEvent(new Event('change', {bubbles: true}))"""
    )
    page.locator("#recurrence_end_mode").select_option("never")
    _settle(page)
    posted = _posted(page)
    _check("W: switching the edit to months posts no ceiling",
           posted["max_per_month"] == [], str(posted))
    _check("W: the cleared start posts empty and the never mode posts no date",
           posted["starts_on"] == [""] and posted["recurrence_end_mode"] == ["never"]
           and posted["end_date"] == [] and posted["max_occurrences"] == [],
           str(posted))
    _submit_and_settle(page)
    rows = [r for r in _rows(page) if r["name"] == MARK]
    _check("W: the edit swapped the section in", len(rows) == 1, str(rows))
    if rows:
        _check("W: the row's Frequency cell now reads the monthly shape and no span",
               rows[0]["phrase"].startswith("Monthly") and " from " not in rows[0]["phrase"]
               and " until " not in rows[0]["phrase"], rows[0]["phrase"])
        _check("W: the row's prefill now names the months unit with no ceiling",
               rows[0]["unit"] == units["months"] and rows[0]["ceiling"] == "",
               str(rows[0]))
        _check("W: the row's prefill carries the derived 1st and the never mode",
               rows[0]["starts_on"].endswith("-01") and rows[0]["end_mode"] == "never"
               and rows[0]["end_date"] == "", str(rows[0]))
    stored = _sql(
        "SELECT r.max_per_month, r.starts_on, r.end_date FROM budget.recurrence_rules r "
        "JOIN salary.paycheck_lines d ON d.id = r.paycheck_line_id "
        f"WHERE d.name = '{MARK}'",
    )
    _check("W: the SAME rule row now starts on a 1st with no ceiling and no stop",
           len(stored) == 1 and stored[0].startswith("|")
           and stored[0].endswith("-01|"), str(stored))

    # --- delete it through the row's form ------------------------------
    # The confirmation is the project's own modal (confirm.js intercepts
    # htmx:confirm and asks through #confirmModal; the browser's confirm()
    # is only its fallback), so the Yes button is what a user clicks.  The
    # first run of this drive accepted a native dialog that never opened and
    # reported the row surviving a delete that was never sent.
    page.click(f'form[action$="/lines/{added["id"]}/delete"] button[type=submit]')
    page.wait_for_selector("#confirmModalYes", state="visible")
    page.click("#confirmModalYes")
    page.wait_for_timeout(int(POST_SPACING_SECONDS * 1000))
    page.wait_for_selector("#recurrence_unit", state="attached")
    _check("W: the delete swapped the section in without the row",
           not [r for r in _rows(page) if r["name"] == MARK], "the row survived")
    _check("W: the delete took the rule with it",
           _sql(
               "SELECT count(*) FROM budget.recurrence_rules r "
               "JOIN salary.paycheck_lines d ON d.id = r.paycheck_line_id "
               f"WHERE d.name = '{MARK}'",
           ) == ["0"], "a rule survived")


def _cleanup() -> None:
    """Remove every marked line an aborted run left behind (its rule cascades)."""
    left = _sql(f"SELECT count(*) FROM salary.paycheck_lines WHERE name = '{MARK}'")
    if left != ["0"]:
        _sql(f"DELETE FROM salary.paycheck_lines WHERE name = '{MARK}'")
        print(f"   cleanup: removed {left[0]} marked line(s)")


def main() -> int:
    """Drive the deduction form and report.

    Returns:
        0 when every check passes, 1 on failure, 2 when the preconditions are
        not met.
    """
    if not SESSION_STATE.exists():
        print(f"No saved session at {SESSION_STATE}.\n"
              "Run: python tests/manual/save_dev_session.py", file=sys.stderr)
        return 2
    if WRITE and shared.DEV_DATABASE == "shekel":
        print("VERIFY_WRITE=1 against the shared dev database 'shekel' is refused: "
              "point the app at a clone and set VERIFY_DEV_DATABASE to its name.",
              file=sys.stderr)
        return 2

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(storage_state=str(SESSION_STATE))
        page = context.new_page()
        console: list[str] = []
        page.on("pageerror", lambda e: console.append(str(e)))
        page.on("console",
                lambda m: console.append(m.text) if m.type == "error" else None)

        page.goto(f"{DEV_BASE_URL}/salary", wait_until="domcontentloaded")
        if "/login" in page.url:
            print("Session expired. Re-run save_dev_session.py", file=sys.stderr)
            browser.close()
            return 2
        edit_links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/salary/"]'))
                     .map(a => a.getAttribute('href'))
                     .filter(h => /\\/salary\\/\\d+\\/edit$/.test(h))"""
        )
        if not edit_links:
            print("The session's user has no salary profile to edit.", file=sys.stderr)
            browser.close()
            return 2
        profile_id = int(edit_links[0].rstrip("/").split("/")[-2])

        _drive_add_form(page, profile_id)
        _drive_existing_rows(page, profile_id)
        if WRITE:
            _cleanup()
            _drive_write_pass(page, profile_id)
            time.sleep(POST_SPACING_SECONDS)
            _cleanup()
        else:
            print("\n(write pass skipped: set VERIFY_WRITE=1 against a clone to run it)")

        print(f"\n=== console errors: {len(console)} ===")
        for message in console:
            print(f"   {message}")
        _check("no console errors", not console, "; ".join(console[:2]))
        browser.close()

    print("\n" + "=" * 62)
    if shared._failures:  # pylint: disable=protected-access
        print(f"FAILURES ({len(shared._failures)}):")  # pylint: disable=protected-access
        for failure in shared._failures:  # pylint: disable=protected-access
            print(f"  - {failure}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
