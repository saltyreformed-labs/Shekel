"""Drive the recurrence form in a real browser, on the real app (R7b-2).

Plan step R7b-2 rewrote the recurrence picker into three LINKED controls -- a
unit ``<select>``, an interval control, and a placement ``<select>`` -- driven
by ``app/static/js/recurrence_form.js``.  What that script decides is which
controls are VISIBLE and which of the two ``interval_n`` inputs is ENABLED, and
pytest can see neither: the suite asserts over rendered HTML, where a control
hidden by a class, an option hidden by a script, and a style the browser
REFUSED to apply all look identical to one that is fine.

**Both defects this script exists for were found by running it, after the
suite was green and two adversarial reviews had passed the code.**

1. The fixed-interval ``<select>`` was synchronised BY VALUE, and interval
   values are not unique across units -- "1" is offered by paychecks, months
   AND years.  Choosing "months" left the selection on the hidden, disabled
   "1 paycheck" option, so the control rendered BLANK and, because a disabled
   option submits nothing, the form posted no ``interval_n`` at all.  A
   validity test comparing values agreed the selection was fine.
2. ``style="max-width: 6rem"`` on the free-interval box violated
   ``style-src 'self'``.  The width never applied and every render of both
   forms logged a blocked-inline-style error.

Neither is visible to a rendered-HTML assertion, which is why this file is
here and not in ``tests/``.

**Plan step R7b-3 added a FOURTH control and the same coverage for it**
(:func:`_drive_end_bound`): an "Ends" select whose three shapes -- never, on a
date, after N occurrences -- each enable at most one value input.  It is the
same defect class one control over: a date the user typed and then moved off
would reach the write door beside a mode that does not name it, and rendered
HTML cannot tell a hidden input from a disabled one.  The transfer form's
LOCKED case (a loan payment, whose bound the app derives) is asserted in
``tests/test_routes/test_templates.py`` instead, because it is a property of
the SERVER's render rather than of the script.

**Plan step R7b-4 replaced the "First paycheck" ``<select>`` with a "Starts on"
DATE and gave the surviving ``<select>`` one job**
(:func:`_drive_opening_bound`).  Two new instances of this file's whole defect
class arrived with it, one on each side of the swap:

* the new date box lives INSIDE ``#recurrence-fields`` and carries no
  ``d-none`` of its own -- every other row in that container has one, so
  copying the idiom would have shipped a control that never appears, and a
  rendered-HTML assertion cannot tell "hidden by a class no script removes"
  from "shown";
* the pay-period ``<select>`` now belongs to the NON-repeating case alone, and
  hiding it is not enough -- a hidden control still SUBMITS, so choosing a
  cadence after choosing a period would post a period the recurrence has no
  use for.  It has to be DISABLED, and only a real ``FormData`` says whether
  it is.

**Plan step R7d-f-5 drives the EDIT form's locks** (:func:`_drive_edit_form_locks`):
the same two lists reach the edit form, computed for the definition being
edited, and a definition pinned to its loan (ruling R-R76) renders its
destination control disabled -- a disabled control posts nothing, which only a
real ``FormData`` can say.

**It writes nothing.**  Every check reads the form's own DOM; the crafted POSTs
in the refusal pass are all expected to be REFUSED, and the pass asserts that
no template and no recurrence rule was persisted by any of them.  Run it
against a clone all the same.

Preconditions:
  * the containerized dev app is up and answering on ``DEV_BASE_URL``
    (``docker compose -f docker-compose.dev.yml up -d`` from THIS checkout --
    the app bind-mounts the directory compose runs from, so a dev app started
    elsewhere serves that code and this proves nothing about yours);
  * ``tests/manual/.dev_session_state.json`` exists and is unexpired
    (``python tests/manual/save_dev_session.py``).

Usage:
    python tests/manual/verify_recurrence_form.py

Exit code 0 when every check passes, 1 on a failure, 2 when the preconditions
are not met.  It is paced: the dev app runs the real Valkey limiter (30/minute
per IP, prod behaviour), and a burst meets 429s.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

#: Where the dev app answers.  The bridge-gateway bind, matching
#: ``save_dev_session.py`` -- the cookie is host-scoped THERE, so shooting
#: 127.0.0.1 silently returns the login page.
DEV_BASE_URL = "http://172.32.0.1:5000"

#: The database THE APP IS POINTED AT, which is what this script must query.
#:
#: **It was hard-coded to ``shekel`` and that made two checks vacuous** (found
#: at plan step R7c-c, by running this script against a database COPY).  A
#: branch whose migration the shared dev database must not receive runs the dev
#: app against ``CREATE DATABASE ... TEMPLATE shekel``; the app then writes
#: there while this script kept counting rows in the original, so
#: "no template was persisted by any refusal" and "no recurrence rule was
#: persisted" passed by reading a database nothing had written to.  They would
#: have passed with every refusal accepted.
#:
#: Set ``VERIFY_DEV_DATABASE`` to whatever ``DATABASE_URL`` names when the app
#: is overridden.  The ``ref`` ids this script looks up are the same either way
#: -- a TEMPLATE copy carries them -- which is exactly why the mismatch was
#: invisible.
DEV_DATABASE = os.environ.get("VERIFY_DEV_DATABASE", "shekel")

#: The storage state ``save_dev_session.py`` writes.
SESSION_STATE = Path(__file__).resolve().parent / ".dev_session_state.json"

#: Marks every row this script creates, so the cleanup pass can find them all
#: even after an aborted run.
MARK = "ZZVERIFY-RECURRENCE"

#: How long to wait after a field change that fires a preview fetch.
#:
#: **The limiter being REAL is the point**, so the run is paced to it rather
#: than the other way round: the dev app runs prod's Valkey limiter at 30
#: requests a minute per IP, ``recurrence_form.js`` fetches the occurrence
#: preview on every field change, and this script changes a field about sixty
#: times.  At 2.5s that is 24 a minute, the same headroom
#: :data:`POST_SPACING_SECONDS` leaves the crafted POSTs.
#:
#: Plan step R7c-b is what made it necessary: it added three driver passes
#: (``_drive_nominal_day`` on both forms, ``_drive_loan_destination_lock``),
#: and the run then met nine 429s -- all of them preview fetches, all reported
#: as console errors, none of them a product defect.  Loosening the limiter
#: would have hidden a class of defect the console check exists to catch.
FIELD_SPACING_MS = 2500

#: How long to wait between crafted POSTs.  See the module docstring.
POST_SPACING_SECONDS = 2.5

_failures: list[str] = []


def _sql(query: str) -> list[str]:
    """Return the rows of one query against the dev database.

    Args:
        query: SQL to run as ``shekel_user`` against :data:`DEV_DATABASE` in
            the ``shekel-dev-db`` container -- the database the app under test
            is pointed at, not necessarily ``shekel``.

    Returns:
        The non-empty output lines, pipe-separated.
    """
    completed = subprocess.run(
        ["docker", "exec", "shekel-dev-db", "psql", "-U", "shekel_user",
         "-d", DEV_DATABASE, "-tAF|", "-c", query],
        capture_output=True, text=True, check=True,
    )
    return [line for line in completed.stdout.strip().split("\n") if line]


def _check(label: str, passed: bool, detail: str = "") -> None:
    """Record one assertion and print it.

    Args:
        label: What was checked.
        passed: Whether it holds.
        detail: What was seen instead, printed only on failure.
    """
    print(f"   {'PASS' if passed else 'FAIL'}  {label}"
          + ("" if passed else f" -- {detail}"))
    if not passed:
        _failures.append(f"{label}: {detail}")


def _settle(page) -> None:
    """Let the script's last field change settle, PACED to the rate limiter.

    Every change fires an occurrence-preview fetch, so the wait is both a DOM
    settle and the run's rate budget; see :data:`FIELD_SPACING_MS`.

    Args:
        page: The Playwright page.
    """
    page.wait_for_timeout(FIELD_SPACING_MS)


def _visible(page, element_id: str) -> bool:
    """Return whether an element is actually rendered to the user.

    Asked of the layout rather than of the class list: ``d-none`` is how this
    form hides a row today, and a check keyed on the class name would pass a
    row hidden some other way and fail a row shown some other way.

    Args:
        page: The Playwright page.
        element_id: The element's ``id``.

    Returns:
        ``True`` when the element occupies space.
    """
    return page.evaluate(
        """(id) => {
            const el = document.getElementById(id);
            if (!el) return null;
            return !!(el.offsetWidth || el.offsetHeight ||
                      el.getClientRects().length);
        }""",
        element_id,
    )


def _posted_intervals(page) -> list[str]:
    """Return the ``interval_n`` values the form would actually submit.

    Read from a real ``FormData``, which is the only thing that accounts for
    BOTH halves of the rule: a disabled control does not submit, and neither
    does a select whose selected option is disabled.

    Args:
        page: The Playwright page.

    Returns:
        Every value the form would post under that name.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            return new FormData(form).getAll('interval_n');
        }"""
    )


def _set_interval(page, interval_n: int) -> None:
    """Type an interval into the one interval box.

    **There were TWO controls posting ``interval_n`` until plan step R7c-c** --
    a free number box and a ``<select>`` of the month intervals the closed
    pattern set could name -- and this file carried two helpers to drive the
    second: one reading the chosen option's ``data-unit`` and one selecting by
    INDEX, because three options carried ``value="1"`` and choosing by value
    landed on another unit's entry (defect 1 in this module's docstring).  Every
    interval is authorable on every unit now, so the ``<select>`` is deleted and
    that whole class of defect with it.

    Args:
        page: The Playwright page.
        interval_n: The interval to type.
    """
    page.fill("#interval_n", str(interval_n))
    page.evaluate(
        """() => document.getElementById('interval_n')
                 .dispatchEvent(new Event('change', {bubbles: true}))"""
    )
    _settle(page)


def _unit_ids(page) -> dict[str, str]:
    """Return ``{label: ref id}`` for the offered units, read off the form.

    Args:
        page: The Playwright page.

    Returns:
        The mapping, excluding the empty "Does not repeat" entry.
    """
    return {
        label.lower(): value
        for value, label in page.evaluate(
            """() => Array.from(
                   document.getElementById('recurrence_unit').options)
                 .map(o => [o.value, o.textContent.trim()])"""
        )
        if value
    }


def _posted_bound(page) -> dict[str, list[str]]:
    """Return the closing-bound keys the form would actually submit.

    From a real ``FormData``, for the reason :func:`_posted_intervals` reads
    one: a control hidden by a class still SUBMITS, and a disabled one does
    not, and rendered HTML cannot tell those apart.  Plan step R7b-3's "Ends"
    control turns on exactly that -- the shape the user chose must be the only
    shape whose value reaches the door.

    Args:
        page: The Playwright page.

    Returns:
        Each bound key mapped to every value posted under it.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            const data = new FormData(form);
            return {
                recurrence_end_mode: data.getAll('recurrence_end_mode'),
                end_date: data.getAll('end_date'),
                max_occurrences: data.getAll('max_occurrences'),
            };
        }"""
    )


def _posted_opening(page) -> dict[str, list[str]]:
    """Return the opening-bound keys the form would actually submit.

    From a real ``FormData``, for the reason :func:`_posted_intervals` reads
    one: a control hidden by a class still SUBMITS and a disabled one does
    not, and rendered HTML cannot tell those apart.  Plan step R7b-4 turns on
    exactly that for the pay-period ``<select>``, which must post its value
    when the definition does NOT repeat and nothing at all when it does.

    Args:
        page: The Playwright page.

    Returns:
        Each opening key mapped to every value posted under it.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            const data = new FormData(form);
            return {
                starts_on: data.getAll('starts_on'),
                nominal_day: data.getAll('nominal_day'),
                start_period_id: data.getAll('start_period_id'),
            };
        }"""
    )


def _drive_opening_bound(page, kind: str, url: str) -> None:
    """Check the "Starts on" box and the pay-period select swap correctly.

    The two halves of plan step R7b-4's control swap, both invisible to a
    rendered-HTML assertion.  See the module docstring for why each is here.

    The transaction form places no pay-period select at all -- a rule-less
    transaction template materialises nothing, so it needs no period -- and
    the absence is asserted rather than skipped: a select that reappeared
    there would submit a field that schema no longer declares.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} opening bound: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    has_period_select = page.locator("#field-start-period").count() > 0

    _check(f"{kind} K: the pay-period select is present only on the transfer form",
           has_period_select == (kind == "transfer"),
           f"present={has_period_select}")

    # --- does NOT repeat -------------------------------------------------
    page.locator("#recurrence_unit").select_option("")
    _settle(page)
    _check(f"{kind} K: Starts on is hidden when it does not repeat",
           not _visible(page, "field-starts-on"), "visible")
    posted = _posted_opening(page)
    _check(f"{kind} K: no starts_on posts when it does not repeat",
           posted["starts_on"] == [], str(posted["starts_on"]))
    if has_period_select:
        _check(f"{kind} K: the pay-period row IS shown when it does not repeat",
               _visible(page, "field-start-period"), "hidden")
        _check(f"{kind} K: the pay period posts exactly once",
               len(posted["start_period_id"]) == 1,
               str(posted["start_period_id"]))

    # --- repeats ---------------------------------------------------------
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} L: Starts on is SHOWN for a repeating definition",
           _visible(page, "field-starts-on"), "hidden")
    if has_period_select:
        _check(f"{kind} L: the pay-period row is hidden when it repeats",
               not _visible(page, "field-start-period"), "visible")
        _check(f"{kind} L: the pay period posts NOTHING when it repeats",
               _posted_opening(page)["start_period_id"] == [],
               str(_posted_opening(page)["start_period_id"]))

    # An untouched box posts the form's own DEFAULT -- TODAY since plan step
    # R7c-b -- and that is load-bearing rather than cosmetic: the control this
    # replaced was a <select> with no empty option preselecting the current
    # period, so every create was bounded.  Defaulting to EMPTY silently made
    # "unbounded" the default, and the create routes generate over every period
    # the owner has: a rent template created today wrote projected debits into
    # pay periods that had already closed.  Found by an adversarial review of
    # plan step R7b-4; asserted here because only a real render shows what the
    # box actually holds.
    posted_default = _posted_opening(page)["starts_on"]
    _check(f"{kind} L: an untouched Starts on posts the form's default",
           len(posted_default) == 1 and posted_default[0] != "",
           str(posted_default))
    page.fill("#starts_on", "2026-09-15")
    _settle(page)
    _check(f"{kind} L: a typed Starts on posts that date",
           _posted_opening(page)["starts_on"] == ["2026-09-15"],
           str(_posted_opening(page)["starts_on"]))
    _check(f"{kind} L: the preview survived the opening bound",
           "Could not load preview" not in page.inner_text("#recurrence-preview"),
           page.inner_text("#recurrence-preview"))


def _drive_nominal_day(page, kind: str, url: str) -> None:
    """Check the "repeating on" control appears and posts only where it means.

    **Plan step R7c-b's new control, and the same defect class one field
    over.**  ``nominal_day`` records the day a rule MEANS when its first
    occurrence's own month was too short to hold it -- 2026-04-30 is "the 30th"
    or "the 31st", and those are different cadences from May on.  The control
    therefore renders ONLY where the chosen date leaves that question open, and
    it must be DISABLED as well as hidden everywhere else: a hidden select
    still submits, and a stale day beside a date that never clamped is exactly
    what ``ck_recurrence_rules_nominal_day`` refuses.  A rendered-HTML
    assertion cannot tell "hidden" from "hidden and still submitting".

    The per-OPTION half matters too and pytest cannot see it either: only the
    days ABOVE the chosen one are meaningful, so choosing 2026-04-30 must offer
    31 and not 30, and moving to a month that holds them all must clear the
    selection rather than leave it stating a day the rule no longer fires on.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} nominal day: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)

    # A MONTHLY cadence, which is the only family with a day-of-month
    # coordinate at all.
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)

    # --- a date its month CAN hold: nothing to ask -----------------------
    page.fill("#starts_on", "2026-04-15")
    _settle(page)
    _check(f"{kind} N: repeating-on is hidden for a mid-month date",
           not _visible(page, "field-nominal-day"), "visible")
    _check(f"{kind} N: repeating-on posts NOTHING for a mid-month date",
           _posted_opening(page)["nominal_day"] == [],
           str(_posted_opening(page)["nominal_day"]))

    # --- a month's LAST day in a short month: the question is open -------
    page.fill("#starts_on", "2026-04-30")
    _settle(page)
    _check(f"{kind} N: repeating-on is SHOWN on a short month's last day",
           _visible(page, "field-nominal-day"), "hidden")
    enabled = page.evaluate(
        """() => Array.from(document.getElementById('nominal_day').options)
              .filter(o => o.value !== '' && !o.disabled)
              .map(o => o.value)"""
    )
    _check(f"{kind} N: only the days ABOVE the 30th are offered",
           enabled == ["31"], str(enabled))

    page.select_option("#nominal_day", "31")
    _settle(page)
    _check(f"{kind} N: a chosen nominal day posts exactly once",
           _posted_opening(page)["nominal_day"] == ["31"],
           str(_posted_opening(page)["nominal_day"]))

    # --- back to a month that holds every day: the choice must GO --------
    page.fill("#starts_on", "2026-05-31")
    _settle(page)
    _check(f"{kind} N: repeating-on is hidden again on a 31-day month's end",
           not _visible(page, "field-nominal-day"), "visible")
    _check(f"{kind} N: the stale nominal day posts nothing after the move",
           _posted_opening(page)["nominal_day"] == [],
           str(_posted_opening(page)["nominal_day"]))


def _posted_ceiling(page) -> list[str]:
    """Return the ``max_per_month`` values the form would actually submit.

    From a real ``FormData``, for the reason every sibling reads one: the
    ceiling's row is hidden by a class and its input DISABLED beside a unit
    that cannot repeat within a month, and only the disabling stops it
    posting -- a stale ceiling beside a monthly unit is the pair the write
    door refuses.

    Args:
        page: The Playwright page.

    Returns:
        Every value the form would post under ``max_per_month``.
    """
    return page.evaluate(
        """() => {
            const form = document.getElementById('recurrence_unit').form;
            return new FormData(form).getAll('max_per_month');
        }"""
    )


def _drive_month_ceiling(page, kind: str, url: str) -> None:
    """Check the "at most N a month" control appears and posts only where it means.

    **Plan step salary:R15-a's control, and this file's whole defect class one
    field over.**  The per-month ceiling is meaningful only on a unit whose
    occurrences can repeat within a month (paychecks today), so
    ``recurrence_form.js`` reveals the row for that unit and hides AND
    DISABLES it for a monthly or yearly one; rendered HTML cannot tell a
    hidden input from a disabled one, and only a real ``FormData`` says which
    the browser would post.  The typed value must SURVIVE a round trip through
    a monthly unit (the row hides, the value stays, the row returns), because
    the update door reads an absent key as "leave the stored one alone" and
    a control that cleared itself would silently drop a saved ceiling on an
    unrelated edit.

    The live preview's fetch must carry the ceiling exactly when an enabled
    control holds a value, and the fragment it answers must omit the third
    paycheck: "what saving would produce" is the preview's whole contract.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} month ceiling: {url} ===")
    previews: list[str] = []
    page.on("request",
            lambda r: previews.append(r.url) if "preview-recurrence" in r.url
            else None)
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)

    # --- does not repeat: hidden, disabled, posts nothing -----------------
    _check(f"{kind} C: the ceiling row is hidden while nothing repeats",
           not _visible(page, "field-max-per-month"), "visible")
    _check(f"{kind} C: the ceiling posts NOTHING while nothing repeats",
           _posted_ceiling(page) == [], str(_posted_ceiling(page)))

    # --- paychecks: shown, enabled, an empty box posts an empty string ---
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} C: the ceiling row is SHOWN for paychecks",
           _visible(page, "field-max-per-month"), "hidden")
    _check(f"{kind} C: an untouched box posts an empty ceiling (no ceiling)",
           _posted_ceiling(page) == [""], str(_posted_ceiling(page)))

    page.fill("#max_per_month", "2")
    previews.clear()
    page.evaluate(
        """() => document.getElementById('max_per_month')
                 .dispatchEvent(new Event('change', {bubbles: true}))"""
    )
    _settle(page)
    _check(f"{kind} C: a typed ceiling posts exactly once",
           _posted_ceiling(page) == ["2"], str(_posted_ceiling(page)))
    _check(f"{kind} C: typing a ceiling fetched a preview carrying it",
           bool(previews) and "max_per_month=2" in previews[-1],
           previews[-1] if previews else "no preview request was made")
    _check(f"{kind} C: the preview survived the ceiling",
           "Could not load preview" not in page.inner_text("#recurrence-preview"),
           page.inner_text("#recurrence-preview"))

    # --- months: hidden, disabled, the typed value is kept ---------------
    # Cleared BEFORE the select, as ``_drive_preview_destination`` does: the
    # fetch fires on the change event itself, so a clear after it discards
    # the very request the next check reads.  The first run of this drive
    # cleared after and reported "no preview request was made" on both
    # forms -- an instrument fault, measured by the ordering alone.
    previews.clear()
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    _check(f"{kind} C: the ceiling row is hidden for months",
           not _visible(page, "field-max-per-month"), "visible")
    _check(f"{kind} C: the ceiling posts NOTHING for months (disabled, not merely hidden)",
           _posted_ceiling(page) == [], str(_posted_ceiling(page)))
    _check(f"{kind} C: the monthly preview does not carry a ceiling",
           bool(previews) and "max_per_month" not in previews[-1],
           previews[-1] if previews else "no preview request was made")

    # --- back to paychecks: the value typed before the detour survives ---
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} C: the ceiling row returns for paychecks",
           _visible(page, "field-max-per-month"), "hidden")
    _check(f"{kind} C: the typed ceiling survived the monthly detour",
           _posted_ceiling(page) == ["2"], str(_posted_ceiling(page)))


def _drive_loan_destination_lock(page) -> None:
    """Check a payment-less loan destination locks "Starts on" on the CREATE form.

    **Plan step R7c-b, and the create-side half of a rule the edit form has
    carried since R7b-4.**  A recurring loan payment's first occurrence is the
    loan's first contractual installment, which the app derives -- so asking
    the user for it and discarding the answer is the defect
    ``LOAN_PAYMENT_BOUND_IS_DERIVED`` closes on the edit path.  The create form
    cannot know at render which destination will be chosen, so the server ships
    the SET of loan accounts and ``recurrence_form.js`` applies it.  **ONE set
    since plan step R7d-g-2** (ruling R-R81): the loans holding no active
    payment, which derive both bounds; a loan that already holds one takes a
    SECOND transfer with its owner's start and stop, so it is in no set and
    locks nothing -- the arms that drove a paid loan are gone with it.

    **Plan step R7d-f-3 added the "Ends" row's half** (:func:`_drive_ends_lock`,
    driven from here on the same page): a loan holding no active payment makes
    the definition being created its payment, whose stop the app derives, so
    the server ships that SUBSET too and the script locks the "Ends" row for
    it -- blank, disabled, posting nothing -- and hands it back, selection
    restored, when the loan is deselected.  The door refuses a stop stated for
    such a loan whatever the script does (ruling R-R60); this checks the
    affordance the suite cannot see.

    Only a real ``FormData`` says whether the control is disabled, and only a
    real render says whether the help text swapped -- both are exactly the
    difference this file exists for.

    **It also covers the defect this step SHIPPED and reading caught**: the
    script read ``startsOn.readOnly`` while the template emitted ``disabled``,
    so ``startsOnLocked`` was false on every locked form and ``syncStartsOn``
    re-enabled the control the moment the page settled.  The edit-form arm
    below is that regression's control.

    Skipped with a printed note when the owner has no payment-less loan
    account -- the dev clone's loans both hold a payment (the recipe creates
    one through the loan setup form) -- and a check that silently passes on
    an empty set is worse than one that says it did not run.

    Args:
        page: The Playwright page.
    """
    print("\n=== transfer loan-destination lock: /transfers/new ===")
    page.goto(f"{DEV_BASE_URL}/transfers/new", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    loan_ids = _lock_set(page)
    _check("transfer M: the every-loan set is gone (R-R81)",
           page.evaluate("() => document.getElementById('recurrence-fields')"
                         ".getAttribute('data-loan-account-ids') === null"),
           "data-loan-account-ids is still emitted")
    # The loader behind the set includes INACTIVE loans; the <select> offers
    # active accounts only, so drive an id the control can actually take.
    offered = page.evaluate(
        """() => Array.from(
              document.getElementById('to_account_id').options
           ).map(o => o.value)"""
    )
    loan_ids = [lid for lid in loan_ids if lid in offered]
    if not loan_ids:
        print("   SKIPPED: this owner has no offered loan holding no payment")
        return

    units = _unit_ids(page)
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)

    # --- a NON-loan destination: the date is the user's ------------------
    non_loan = page.evaluate(
        """(loanIds) => Array.from(
              document.getElementById('to_account_id').options
           ).map(o => o.value).find(v => v && loanIds.indexOf(v) === -1)""",
        loan_ids,
    )
    _check("transfer M: the form offers a non-loan destination to compare",
           non_loan is not None, "every destination is a loan")
    if non_loan is None:
        return
    page.select_option("#to_account_id", non_loan)
    _settle(page)
    _check("transfer M: Starts on is the user's for a non-loan destination",
           not page.locator("#starts_on").is_disabled(), "disabled")
    _check("transfer M: it posts the date for a non-loan destination",
           len(_posted_opening(page)["starts_on"]) == 1,
           str(_posted_opening(page)["starts_on"]))

    # --- a payment-less LOAN destination: the app derives it -------------
    page.select_option("#to_account_id", loan_ids[0])
    _settle(page)
    _check("transfer M: Starts on is DISABLED for a payment-less loan",
           page.locator("#starts_on").is_disabled(), "enabled")
    _check("transfer M: it posts NOTHING for a payment-less loan",
           _posted_opening(page)["starts_on"] == [],
           str(_posted_opening(page)["starts_on"]))
    _check("transfer M: the help text says the loan sets it",
           "loan's first payment" in page.inner_text("#starts-on-help"),
           page.inner_text("#starts-on-help"))

    # --- and BACK: the lock is not one-way -------------------------------
    page.select_option("#to_account_id", non_loan)
    _settle(page)
    _check("transfer M: Starts on is handed back when the loan is deselected",
           not page.locator("#starts_on").is_disabled(), "still disabled")

    _drive_ends_lock(page, loan_ids, non_loan)
    _drive_preview_destination(page, loan_ids, non_loan)


def _drive_preview_destination(page, loan_ids: list[str], non_loan: str) -> None:
    """Check the live preview's fetch CARRIES the destination (plan step R7d-f-2).

    Plan ledger row REC-515: the preview endpoint narrows the walk by the
    destination's derived stop, so a transfer into a loan previews to the
    loan's payoff -- but only if the script SENDS ``to_account_id``.  The
    suite grades the endpoint with the argument present; this grades the
    half the suite cannot see, the request the browser actually makes.
    Three things are checked off the captured requests: the fetch fired on
    the destination change alone (the edit form's and a loan-less owner's
    listener, ``fetchPreview`` where the create form's lock path is
    ``toggleFields``), it named the newly chosen destination, and the
    preview answered rather than breaking.  Whether the payoff BITES depends
    on the loan's remaining life against the first five dates and is not a
    property this owner's data is known to have, so it is not asserted.

    Args:
        page: The Playwright page, on ``/transfers/new`` with a MONTH cadence
            chosen and *non_loan* selected.
        loan_ids: Every loan destination the form emitted.
        non_loan: A destination that is not a loan.
    """
    print("\n=== transfer preview destination: /transfers/new ===")
    previews: list[str] = []
    page.on("request",
            lambda r: previews.append(r.url) if "preview-recurrence" in r.url
            else None)

    page.select_option("#to_account_id", loan_ids[0])
    _settle(page)
    _check("transfer P: choosing a loan fetched a preview",
           bool(previews), "no preview request was made")
    _check("transfer P: the fetch names the chosen destination",
           bool(previews) and f"to_account_id={loan_ids[0]}" in previews[-1],
           previews[-1] if previews else "no request")

    previews.clear()
    page.select_option("#to_account_id", non_loan)
    _settle(page)
    _check("transfer P: deselecting the loan fetched again, naming the new one",
           bool(previews) and f"to_account_id={non_loan}" in previews[-1],
           previews[-1] if previews else "no request")
    _check("transfer P: the preview survived the destination changes",
           "Could not load preview" not in page.inner_text("#recurrence-preview"),
           page.inner_text("#recurrence-preview"))


def _drive_ends_lock(page, loan_ids: list[str], non_loan: str) -> None:
    """Check a payment-less loan destination locks "Ends" on the CREATE form.

    Plan step R7d-f-3.  Drives one of the payment-less loans the server
    emitted (``data-loan-account-ids-without-payment``) and the way back.
    The row must post nothing while locked (a real ``FormData``, the only
    thing that can say so), show a BLANK box under the locked help sentence,
    and come back with the shape the user had chosen.  The arm that drove a
    loan holding a payment went at plan step R7d-g-2 (ruling R-R81): such a
    loan is in no set and locks neither row, so the page cannot name one.

    Args:
        page: The Playwright page, on ``/transfers/new`` with a MONTH cadence
            chosen and *non_loan* selected as the destination.
        loan_ids: The offered payment-less loans the form emitted (non-empty:
            the caller skipped otherwise).
        non_loan: A destination that is not a loan.
    """
    print("\n=== transfer loan-destination Ends lock: /transfers/new ===")

    # A shape chosen BEFORE the lock, so the restore has something to show.
    _select_end_mode(page, "on_date")
    _check("transfer E: On a date is chosen for a non-loan destination",
           _posted_bound(page)["recurrence_end_mode"] == ["on_date"],
           str(_posted_bound(page)))

    # --- a loan with NO payment: the app derives the stop -----------------
    page.select_option("#to_account_id", loan_ids[0])
    _settle(page)
    _check("transfer E: Ends is DISABLED for a payment-less loan",
           page.locator("#recurrence_end_mode").is_disabled(), "enabled")
    _check("transfer E: it posts NOTHING for a payment-less loan",
           all(v == [] for v in _posted_bound(page).values()),
           str(_posted_bound(page)))
    _check("transfer E: the box is blank while locked",
           page.evaluate(
               "() => document.getElementById('recurrence_end_mode')"
               ".selectedIndex === -1"
           ), "a shape is still selected")
    _check("transfer E: the help text says the loan sets it",
           "projected payoff" in page.inner_text("#end-bound-help"),
           page.inner_text("#end-bound-help"))
    _check("transfer E: the date box is hidden while locked",
           not _visible(page, "field-end-date"), "shown")

    # --- and BACK: the shape chosen before the lock is restored -----------
    page.select_option("#to_account_id", non_loan)
    _settle(page)
    _check("transfer E: Ends is handed back when the loan is deselected",
           not page.locator("#recurrence_end_mode").is_disabled(),
           "still disabled")
    _check("transfer E: the shape chosen before the lock is restored",
           _posted_bound(page)["recurrence_end_mode"] == ["on_date"],
           str(_posted_bound(page)))
    _check("transfer E: the help text is the user's again",
           "generated past" in page.inner_text("#end-bound-help"),
           page.inner_text("#end-bound-help"))
    _select_end_mode(page, "never")


def _edit_form_ids() -> dict[str, str | None]:
    """Return the ids of the three EDIT-form shapes this pass drives, or None each.

    Read off the database THE APP IS POINTED AT (:data:`DEV_DATABASE`), for the
    owner the saved session belongs to: the loan-payment table's oldest
    active recurring transfer into a loan (the standing payment, pinned), an
    active repeating transfer whose destination is NOT a loan (free to move),
    and an active transfer into a loan that does not repeat yet (about to
    author).  The third seldom exists on a clone -- the recipe creates one
    through the create form -- so its arm prints SKIPPED rather than passing
    on nothing.

    Returns:
        ``{"pinned": id, "repeating": id, "one_off": id}``, each ``None`` when
        the copy holds no such definition.
    """
    owner = _sql(
        "SELECT user_id FROM budget.transfer_templates WHERE is_active "
        "ORDER BY id LIMIT 1"
    )
    if not owner:
        return {"pinned": None, "repeating": None, "one_off": None}
    loans = "(SELECT account_id FROM budget.loan_params)"
    # ``IS NOT NULL``, because a transaction template's rule leaves this column
    # NULL and ``x NOT IN (..., NULL)`` is never true: without it the one-off
    # arm SKIPPED on a copy that held its definition (found by running this).
    rules = ("(SELECT transfer_template_id FROM budget.recurrence_rules "
             "WHERE transfer_template_id IS NOT NULL)")
    base = (f"SELECT id FROM budget.transfer_templates WHERE is_active "
            f"AND user_id = {owner[0]} ")
    pinned = _sql(base + f"AND to_account_id IN {loans} AND id IN {rules} "
                  "ORDER BY id LIMIT 1")
    repeating = _sql(base + f"AND to_account_id NOT IN {loans} AND id IN {rules} "
                     "ORDER BY id LIMIT 1")
    one_off = _sql(base + f"AND to_account_id IN {loans} AND id NOT IN {rules} "
                   "ORDER BY id LIMIT 1")
    return {
        "pinned": pinned[0] if pinned else None,
        "repeating": repeating[0] if repeating else None,
        "one_off": one_off[0] if one_off else None,
    }


def _lock_set(page) -> list[str]:
    """Return the ONE lock set the page's container carries, as the script reads it."""
    return page.evaluate(
        """() => (document.getElementById('recurrence-fields')
              .getAttribute('data-loan-account-ids-without-payment') || '')
              .split(',').filter(Boolean)"""
    )


def _posted_destination(page) -> list[str]:
    """Return every value the form would post under ``to_account_id``."""
    return page.evaluate(
        """() => new FormData(document.getElementById('recurrence_unit').form)
                 .getAll('to_account_id')"""
    )


def _drive_edit_form_locks(page) -> None:
    """Check the EDIT form's lock affordance, computed for THIS edit (plan step R7d-f-5).

    The create form's set reaches the edit form since R7d-f-5 (ruling
    R-R79), computed the way the update door decides the same edit, and a
    definition R-R76 pins to its loan ships an EMPTY set beside a DISABLED
    destination control.  ONE set since plan step R7d-g-2 (ruling R-R81):
    the payment-less loans lock both rows, a paid loan locks neither.  Three
    forms, one per shape the producer distinguishes (:func:`_edit_form_ids`):

    * a REPEATING transfer into savings: both rows the owner's on load; a
      payment-less loan locks both (posting nothing, help swapped, "Ends"
      blank), and the original destination hands both back with the stored
      "Ends" shape restored;
    * the STANDING payment: an empty set, the destination select disabled
      and posting nothing, the R-R76 sentence as its help, both bound rows
      still server-locked;
    * a ONE-TIME transfer into a loan: choosing a cadence locks both rows
      where its own stored loan holds no payment, and neither where it does.

    Only a real ``FormData`` says what a disabled control posts, and only a
    real render says whether the help text swapped -- the difference this
    file exists for.  It writes nothing.

    Args:
        page: The Playwright page.
    """
    ids = _edit_form_ids()

    print("\n=== transfer edit-form locks: a repeating savings transfer ===")
    if ids["repeating"] is None:
        print("   SKIPPED: this owner has no active repeating transfer into a non-loan")
    else:
        page.goto(f"{DEV_BASE_URL}/transfers/{ids['repeating']}/edit",
                  wait_until="domcontentloaded")
        page.wait_for_selector("#recurrence_unit")
        loan_ids = _lock_set(page)
        original = page.evaluate("() => document.getElementById('to_account_id').value")
        # A precondition of the hand-back below, not a test of the producer's
        # exclusion: a savings destination is never a loan.  The exclusion (a
        # repeating SECOND transfer into a paid loan) is graded by the suite,
        # tests/test_routes/test_transfer_edit_form_locks.py.
        _check("edit R: precondition -- the stored (savings) destination is not a loan",
               original not in loan_ids, f"{original} in {loan_ids}")
        _check("edit R: the destination is the owner's",
               not page.locator("#to_account_id").is_disabled(), "disabled")
        _check("edit R: Starts on is the owner's on load",
               not page.locator("#starts_on").is_disabled(), "disabled")
        _check("edit R: Ends is the owner's on load",
               not page.locator("#recurrence_end_mode").is_disabled(), "disabled")
        stored_mode = _posted_bound(page)["recurrence_end_mode"]
        _check("edit R: the stored Ends shape is posted on load",
               len(stored_mode) == 1, str(stored_mode))
        offered = page.evaluate(
            "() => Array.from(document.getElementById('to_account_id').options)"
            ".map(o => o.value)")
        unpaid = next((lid for lid in loan_ids if lid in offered), None)
        if unpaid is None:
            print("   SKIPPED (payment-less arm): every offered loan holds a payment")
        else:
            page.select_option("#to_account_id", unpaid)
            _settle(page)
            _check("edit R: a payment-less loan locks Starts on",
                   page.locator("#starts_on").is_disabled(), "enabled")
            _check("edit R: it posts NOTHING for the start",
                   _posted_opening(page)["starts_on"] == [],
                   str(_posted_opening(page)["starts_on"]))
            _check("edit R: the Starts on help says the loan sets it",
                   "loan's first payment" in page.inner_text("#starts-on-help"),
                   page.inner_text("#starts-on-help"))
            _check("edit R: a payment-less loan locks Ends too",
                   page.locator("#recurrence_end_mode").is_disabled(), "enabled")
            _check("edit R: it posts NOTHING for the stop",
                   all(v == [] for v in _posted_bound(page).values()),
                   str(_posted_bound(page)))
            _check("edit R: the Ends box is blank while locked",
                   page.evaluate("() => document.getElementById('recurrence_end_mode')"
                                 ".selectedIndex === -1"), "a shape is still selected")
            _check("edit R: the Ends help says the loan sets it",
                   "projected payoff" in page.inner_text("#end-bound-help"),
                   page.inner_text("#end-bound-help"))
        page.select_option("#to_account_id", original)
        _settle(page)
        _check("edit R: the original destination hands Starts on back",
               not page.locator("#starts_on").is_disabled(), "still disabled")
        _check("edit R: the original destination hands Ends back",
               not page.locator("#recurrence_end_mode").is_disabled(), "still disabled")
        _check("edit R: the stored Ends shape is restored",
               _posted_bound(page)["recurrence_end_mode"] == stored_mode,
               str(_posted_bound(page)["recurrence_end_mode"]))
        _check("edit R: the Ends help is the owner's again",
               "generated past" in page.inner_text("#end-bound-help"),
               page.inner_text("#end-bound-help"))

    print("\n=== transfer edit-form locks: the loan's standing payment (pinned) ===")
    if ids["pinned"] is None:
        print("   SKIPPED: this owner has no active recurring transfer into a loan")
    else:
        page.goto(f"{DEV_BASE_URL}/transfers/{ids['pinned']}/edit",
                  wait_until="domcontentloaded")
        page.wait_for_selector("#recurrence_unit")
        _check("edit P: the set is EMPTY", _lock_set(page) == [], str(_lock_set(page)))
        _check("edit P: the destination select is DISABLED",
               page.locator("#to_account_id").is_disabled(), "enabled")
        _check("edit P: it posts NOTHING for the destination",
               _posted_destination(page) == [], str(_posted_destination(page)))
        _check("edit P: the help carries the R-R76 sentence",
               "pays the loan it was set up for" in page.inner_text("#to-account-help"),
               page.inner_text("#to-account-help"))
        _check("edit P: Starts on is still server-locked",
               page.locator("#starts_on").is_disabled(), "enabled")
        _check("edit P: Ends is still server-locked",
               page.locator("#recurrence_end_mode").is_disabled(), "enabled")

    print("\n=== transfer edit-form locks: a one-time transfer into a loan ===")
    if ids["one_off"] is None:
        print("   SKIPPED: this owner has no active one-time transfer into a loan "
              "(the recipe creates one through /transfers/new)")
        return
    page.goto(f"{DEV_BASE_URL}/transfers/{ids['one_off']}/edit",
              wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    loan_ids = _lock_set(page)
    stored = page.evaluate("() => document.getElementById('to_account_id').value")
    _check("edit O: the destination is the owner's",
           not page.locator("#to_account_id").is_disabled(), "disabled")
    page.locator("#recurrence_unit").select_option(_unit_ids(page)["months"])
    _settle(page)
    if stored in loan_ids:
        _check("edit O: choosing a cadence locks Starts on for its own payment-less loan",
               page.locator("#starts_on").is_disabled(), "enabled")
        _check("edit O: it posts NOTHING for the start",
               _posted_opening(page)["starts_on"] == [],
               str(_posted_opening(page)["starts_on"]))
        _check("edit O: the Starts on help says the loan sets it",
               "loan's first payment" in page.inner_text("#starts-on-help"),
               page.inner_text("#starts-on-help"))
        _check("edit O: its payment-less loan locks Ends too",
               page.locator("#recurrence_end_mode").is_disabled(), "enabled")
        _check("edit O: it posts NOTHING for the stop",
               all(v == [] for v in _posted_bound(page).values()),
               str(_posted_bound(page)))
    else:
        _check("edit O: its paid loan leaves Starts on the owner's (R-R81)",
               not page.locator("#starts_on").is_disabled(), "disabled")
        _check("edit O: its paid loan leaves Ends the owner's",
               not page.locator("#recurrence_end_mode").is_disabled(), "disabled")
    page.locator("#recurrence_unit").select_option("")
    _settle(page)


def _select_end_mode(page, token: str) -> None:
    """Choose one "Ends" shape and let the script re-link the value inputs.

    By VALUE here, unlike the interval select: a bound token IS unique across
    the offer set (it is the shape's own name), which the interval values are
    not.

    Args:
        page: The Playwright page.
        token: The shape's token -- ``never``, ``on_date``,
            ``after_occurrences``.
    """
    page.select_option("#recurrence_end_mode", token)
    _settle(page)


def _drive_end_bound(page, kind: str, url: str) -> None:
    """Check the "Ends" control shows and posts exactly one shape's value.

    The property pytest cannot see, and the one plan step R7b-2 was bitten by
    twice: which controls are VISIBLE and which are ENABLED.  A stale date
    left in a box the user has moved off would otherwise reach the door beside
    a mode that does not name it.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} ends control: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)

    _check(f"{kind} G: the Ends row is shown for a repeating definition",
           _visible(page, "field-end-bound"), "hidden")

    # Never: neither value input shows, and NEITHER posts.
    _select_end_mode(page, "never")
    posted = _posted_bound(page)
    _check(f"{kind} G: never shows no value input",
           not _visible(page, "field-end-date")
           and not _visible(page, "field-max-occurrences"),
           "a value input is shown for the unbounded shape")
    _check(f"{kind} G: never posts only the mode",
           posted["end_date"] == [] and posted["max_occurrences"] == [],
           f"posted={posted}")

    # On a date: the date box shows and posts; the count does neither.
    _select_end_mode(page, "on_date")
    page.fill("#end_date", "2030-01-01")
    posted = _posted_bound(page)
    _check(f"{kind} H: the date box is shown",
           _visible(page, "field-end-date"), "hidden")
    _check(f"{kind} H: the count box is hidden",
           not _visible(page, "field-max-occurrences"), "shown")
    _check(f"{kind} H: only the date posts",
           posted["end_date"] == ["2030-01-01"]
           and posted["max_occurrences"] == [],
           f"posted={posted}")

    # After N: the count box shows and posts, and the DATE the user typed a
    # moment ago must not follow it.
    _select_end_mode(page, "after_occurrences")
    page.fill("#max_occurrences", "6")
    posted = _posted_bound(page)
    _check(f"{kind} I: the count box is shown",
           _visible(page, "field-max-occurrences"), "hidden")
    _check(f"{kind} I: the date box is hidden",
           not _visible(page, "field-end-date"), "shown")
    _check(f"{kind} I: only the count posts",
           posted["max_occurrences"] == ["6"] and posted["end_date"] == [],
           f"a stale value from the shape the user moved off still posts: "
           f"{posted}")
    _check(f"{kind} I: exactly one mode posts",
           posted["recurrence_end_mode"] == ["after_occurrences"],
           f"posted={posted}")

    # Back to "does not repeat": the whole control goes, and posts NOTHING --
    # a hidden-but-enabled control is the defect class this file exists for.
    page.locator("#recurrence_unit").select_option("")
    _settle(page)
    posted = _posted_bound(page)
    _check(f"{kind} J: a non-repeating definition posts no bound at all",
           posted["recurrence_end_mode"] == []
           and posted["end_date"] == []
           and posted["max_occurrences"] == [],
           f"posted={posted}")

    _check(f"{kind}: the preview survived the bound changes",
           "Could not load preview"
           not in page.locator("#recurrence-preview").inner_text(),
           "preview broke")


def _drive_visibility(page, kind: str, url: str) -> None:
    """Check which controls each cadence shows, on one form kind.

    **Re-pointed onto ``field-due-dom`` at plan step R7c-b, and the previous
    version is the lesson.**  It drove ``field-dom`` (Day of Month) and
    ``field-moy`` (Month), the two controls that step DELETED -- ruling R-R16
    put the cycle's day and its month on ``starts_on``.  So its five
    ``VISIBLE`` assertions failed correctly, and, far worse, its six ``hidden``
    assertions PASSED VACUOUSLY: a non-existent element is not visible, so
    those read green while proving nothing.

    The day question they asked is now answered in two places and both have
    their own driver: ``starts_on`` (always shown --
    :func:`_drive_opening_bound`) and ``nominal_day`` (conditionally shown --
    :func:`_drive_nominal_day`).  What is left cadence-dependent, and what this
    function was about until plan step recurrence:R5-a, was ``field-due-dom``:
    the bill's separate REAL due day.  That step dropped the control with its
    column (ruling R-R96: a rule's own day is the day its rows are due), so
    this function now asserts it is GONE -- an absence stated as the goal, not
    a hidden-element check that passes vacuously -- and drives the interval,
    placement and ceiling rows every cadence still toggles.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind}: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    unit = page.locator("#recurrence_unit")
    # The Due Day of Month control left BOTH forms at plan step R5-a (ruling
    # R-R96).  Asserted as an absence by NAME and by ROW id, because a control
    # that came back under either would post a key the schema now drops.
    _check(f"{kind}: no Due Day of Month control (R-R96)",
           page.locator("[name='due_day_of_month'], #field-due-dom").count() == 0,
           "a due-day control is rendered")

    def one_interval(label: str) -> list[str]:
        """Exactly one interval control submits, in every state."""
        posted = _posted_intervals(page)
        _check(f"{kind} {label}: exactly one interval_n posts",
               len(posted) <= 1, f"posted={posted}")
        return posted

    def placement_help(label: str, fixed: bool) -> None:
        """The funding row is SHOWN and says which state the user is in.

        Plan ledger row **D32**, developer ruling 2026-08-16.  The row used to
        hide itself whenever the cadence admitted one placement, which is how a
        bill's funding rule came to change with nothing on screen saying so.
        Rendered HTML cannot tell a row a script left visible from one it hid,
        which is why this check is here rather than in the suite.

        Args:
            label: The case letter.
            fixed: Whether this cadence admits exactly one placement.
        """
        _check(f"{kind} {label}: funding row VISIBLE",
               _visible(page, "field-placement"),
               "the Funded-from row is hidden, so a funding change is silent")
        shown_text = page.locator("#placement-help").inner_text().strip()
        expected = (
            "This cadence has one funding rule" if fixed
            else "Which paycheck pays for each occurrence"
        )
        _check(f"{kind} {label}: funding help says "
               f"{'FIXED' if fixed else 'a choice'}",
               expected in shown_text, shown_text)

    # Does not repeat: the form's own empty option, not a cadence.
    unit.select_option("")
    _settle(page)
    _check(f"{kind} A: interval row hidden", not _visible(page, "field-interval"), "shown")
    _check(f"{kind} A: placement row hidden", not _visible(page, "field-placement"), "shown")
    _check(f"{kind} A: interval box disabled",
           page.evaluate("() => document.getElementById('interval_n').disabled"),
           "enabled beside no unit, so half a cadence can post")
    one_interval("A")

    # Paychecks: the placement is INERT here, so the row explains itself.
    unit.select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} B: interval box enabled",
           page.evaluate("() => !document.getElementById('interval_n').disabled"),
           "disabled")
    placement_help("B", fixed=True)
    one_interval("B")

    # Months at 1: anchors on the calendar.
    unit.select_option(units["months"])
    _settle(page)
    _set_interval(page, 1)
    placement_help("C", fixed=False)
    one_interval("C")

    # Months at 1, funded from the month's FIRST paycheck: anchors on a
    # paycheck, so it reads no day of the month.
    page.evaluate(
        """() => { const s = document.getElementById('recurrence_placement');
                   s.selectedIndex = 1;
                   s.dispatchEvent(new Event('change', {bubbles: true})); }""")
    _settle(page)

    # Months at 3, still funded from the month's first paycheck.  **This is
    # plan ledger row D32's defect ceasing to exist**: the closed set had no
    # quarterly first-paycheck twin, so raising the interval used to reassign
    # the placement and HIDE the row.  The choice must survive.
    _set_interval(page, 3)
    placement_help("E", fixed=False)
    _check(f"{kind} E: the first-paycheck funding SURVIVED the interval change",
           page.evaluate(
               "() => document.getElementById('recurrence_placement')"
               ".selectedIndex") == 1,
           "the funding choice was silently reassigned")
    one_interval("E")

    # Months at 2 -- the cadence the closed pattern set could never name, and
    # the whole point of plan step R7c-c.  Typed rather than chosen, because
    # there is no <select> left to choose it from.
    page.evaluate(
        """() => { const s = document.getElementById('recurrence_placement');
                   s.selectedIndex = 0;
                   s.dispatchEvent(new Event('change', {bubbles: true})); }""")
    _set_interval(page, 2)
    _check(f"{kind} F: every-other-month posts its own interval",
           one_interval("F") == ["2"], "the free box lost the typed interval")

    # Years: the interval box carries over, so it is typed back to 1.
    #
    # **``fixed=True`` until plan step R8-a, and the flip is the step.**  The
    # YEAR unit admitted one placement while ``anchor_family`` refused its
    # deferring reading -- on a first-occurrence derivation ruling R-R16 had
    # already deleted -- so the row explained that there was nothing to choose.
    # It offers both now, so the help text must be the CHOICE sentence, and
    # this is the one check that reads which of the two the script swapped in.
    unit.select_option(units["years"])
    _settle(page)
    _set_interval(page, 1)
    placement_help("G", fixed=False)
    _check(f"{kind} G: posts interval 1", one_interval("G") == ["1"], "wrong interval")

    # A year-scale cadence funded from the month's FIRST paycheck: the reading
    # plan step R8-a admitted, driven end to end because a placement the server
    # OFFERS and the script leaves disabled is invisible to the suite -- and
    # because this pair was a REFUSAL case in ``_drive_refusals`` until this
    # step, which is as wrong as it sounds in the other direction.
    page.evaluate(
        """() => { const s = document.getElementById('recurrence_placement');
                   s.selectedIndex = 1;
                   s.dispatchEvent(new Event('change', {bubbles: true})); }""")
    _settle(page)
    _check(f"{kind} H: a yearly cadence CAN be funded from a later paycheck",
           page.evaluate(
               "() => document.getElementById('recurrence_placement')"
               ".selectedIndex") == 1,
           "the deferring placement is still not selectable on the YEAR unit")
    placement_help("H", fixed=False)

    preview = page.locator("#recurrence-preview").inner_text().strip()
    _check(f"{kind}: the live preview answered",
           "Could not load preview" not in preview, preview)


def _drive_refusals(context, page) -> None:
    """POST the payloads the form cannot produce, and read the refusal.

    Read off the POST's OWN response body rather than a later page load: the
    API context follows the redirect, and that hop CONSUMES the flash.

    Args:
        context: The Playwright browser context (for its request API).
        page: A page, for the CSRF token.
    """
    print("\n=== refusals: payloads the form cannot produce ===")
    page.goto(f"{DEV_BASE_URL}/templates/new", wait_until="domcontentloaded")
    token = page.evaluate(
        "() => document.querySelector('input[name=csrf_token]').value")

    ids = {
        "category": _sql("SELECT id FROM budget.categories LIMIT 1")[0],
        # By NAME, not LIMIT 1: an unordered pick lands on the Van Loan, which
        # a transaction template cannot target, and the refusal under test is
        # then indistinguishable from that one.
        "account": _sql(
            "SELECT id FROM budget.accounts WHERE name = 'Checking'")[0],
        "expense": _sql(
            "SELECT id FROM ref.transaction_types WHERE name = 'Expense'")[0],
        "month": _sql(
            "SELECT id FROM ref.recurrence_units WHERE name = 'month'")[0],
        "period": _sql(
            "SELECT id FROM ref.recurrence_units WHERE name = 'period'")[0],
        "week": _sql(
            "SELECT id FROM ref.recurrence_units WHERE name = 'week'")[0],
        "year": _sql(
            "SELECT id FROM ref.recurrence_units WHERE name = 'year'")[0],
        "covering": _sql("SELECT id FROM ref.period_placements "
                         "WHERE name = 'containing_date'")[0],
        "first_pay": _sql("SELECT id FROM ref.period_placements "
                          "WHERE name = 'period_starting_on_or_after'")[0],
    }
    # ``starts_on`` rides in the BASE form from plan step R7c-c, so each case
    # below carries exactly ONE fault.  Without it every payload here was also
    # missing a first occurrence, which the create schema requires beside any
    # chosen cadence -- so a case could pass by meeting a refusal it was not
    # written for, and two of them did the moment their own refusal stopped
    # existing.
    base_form = {
        "name": f"{MARK}-refused", "default_amount": "10.00",
        "category_id": ids["category"], "account_id": ids["account"],
        "transaction_type_id": ids["expense"],
        "starts_on": "2026-03-15",
    }
    rules_before = _sql("SELECT count(*) FROM budget.recurrence_rules")[0]

    cases = [
        # **The unauthorable SET has MOVED TWICE, and this list is where that
        # shows.**  It was every month or year INTERVAL the closed pattern set
        # could not name -- "every other month", "quarterly funded from the
        # first paycheck" -- because STORAGE was the binding constraint, and
        # freeing the interval at R7c-c is what this arc exists for.  It was
        # then the two ``(unit, placement)`` pairs ``anchor_family`` had no
        # first-occurrence derivation for, and plan step **R8-a** measured one
        # of those STALE: ruling R-R16 made the first occurrence AUTHORED at
        # R7c-b and deleted the derivation the refusal cited, so a year-scale
        # cadence funded from a later paycheck SAVES now.
        #
        # **Leaving that case here would not have been harmless.**  These
        # payloads carry the whole ``base_form``, and ``POST /templates``
        # GENERATES on create -- so an accepted "refusal" writes a template, a
        # rule and its projected rows into whatever database this is pointed
        # at, and then trips the two persistence checks at the end of this
        # pass, which is how a real regression comes to be read as "the known
        # year failure".  It is replaced by the WEEK unit's OTHER placement:
        # the withholding is a property of the unit
        # (``_offer.has_row_date_coordinate``), so both readings must be
        # refused and a list pinning one would pass against a rule that had
        # started admitting the other.
        ("the WEEK unit names no date a generated row can carry",
         {"recurrence_unit": ids["week"], "interval_n": "1",
          "recurrence_placement": ids["covering"]},
         "That repeat schedule cannot be saved yet"),
        ("the WEEK unit is refused under the DEFERRING placement too",
         {"recurrence_unit": ids["week"], "interval_n": "2",
          "recurrence_placement": ids["first_pay"]},
         "That repeat schedule cannot be saved yet"),
        # The interval box is a free number input from R7c-c, so a CLEARED one
        # is a payload the form really can produce -- and it defaulted to 1,
        # which re-cadenced a quarterly bill to monthly on save.  Driven here
        # because only a browser proves the box submits "" rather than nothing.
        ("a CLEARED interval box beside a chosen cadence",
         {"recurrence_unit": ids["month"], "interval_n": "",
          "recurrence_placement": ids["covering"]},
         "Say how often this repeats"),
        ("an interval past the column's domain",
         {"recurrence_unit": ids["period"], "interval_n": "2147483648",
          "recurrence_placement": ids["covering"]},
         "Must be greater than or equal to 1"),
        ("a unit with no placement key",
         {"recurrence_unit": ids["period"], "interval_n": "1"},
         "Choose which paycheck funds each occurrence"),
        ("a unit with an EMPTY placement",
         {"recurrence_unit": ids["period"], "interval_n": "1",
          "recurrence_placement": ""},
         "Choose which paycheck funds each occurrence"),
        ("an unmodelled unit id",
         {"recurrence_unit": "999999", "interval_n": "1",
          "recurrence_placement": ids["covering"]},
         "Invalid repeat unit"),
        ("an unmodelled placement id",
         {"recurrence_unit": ids["period"], "interval_n": "1",
          "recurrence_placement": "999999"},
         "Invalid funding choice"),
        # R7c-b authored this refusal and nothing carried it to the user until
        # R7c-c put the key on the flash allowlist; it is driven here because a
        # message that reaches no surface is invisible to the suite.
        ("a chosen cadence with no first occurrence",
         {"recurrence_unit": ids["month"], "interval_n": "1",
          "recurrence_placement": ids["covering"], "starts_on": ""},
         "Choose the date this first happens"),
    ]
    for label, cadence, expected in cases:
        response = context.request.post(
            f"{DEV_BASE_URL}/templates",
            form={**base_form, **cadence, "csrf_token": token},
            headers={"Referer": f"{DEV_BASE_URL}/templates/new"},
        )
        if response.status == 429:
            _check(label, False, "429 rate-limited; raise POST_SPACING_SECONDS")
        else:
            _check(f"{label} -> its own message", expected in response.text(),
                   f"status={response.status}")
        time.sleep(POST_SPACING_SECONDS)

    made = _sql("SELECT count(*) FROM budget.transaction_templates "
                f"WHERE name LIKE '{MARK}%'")[0]
    rules_after = _sql("SELECT count(*) FROM budget.recurrence_rules")[0]
    _check("no template was persisted by any refusal", made == "0", f"{made} rows")
    _check("no recurrence rule was persisted by any refusal",
           rules_before == rules_after, f"{rules_before} -> {rules_after}")


def main() -> int:
    """Drive both forms and report.

    Returns:
        0 when every check passes, 1 on failure, 2 when the preconditions are
        not met.
    """
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

        page.goto(f"{DEV_BASE_URL}/templates/new", wait_until="domcontentloaded")
        if "/login" in page.url:
            print("Session expired. Re-run save_dev_session.py", file=sys.stderr)
            browser.close()
            return 2

        for kind, url in (("transaction", "/templates/new"),
                          ("transfer", "/transfers/new")):
            _drive_visibility(page, kind, url)
            _drive_opening_bound(page, kind, url)
            _drive_nominal_day(page, kind, url)
            _drive_month_ceiling(page, kind, url)
            _drive_end_bound(page, kind, url)
        # Transfer-only: the transaction form has no destination account, so
        # its definition can never be a loan payment.
        _drive_loan_destination_lock(page)
        _drive_edit_form_locks(page)
        _drive_refusals(context, page)

        # A blocked inline style is a console error and nothing else, which is
        # how defect 2 shipped past a green suite.
        print(f"\n=== console errors: {len(console)} ===")
        for message in console:
            print(f"   {message}")
        _check("no console errors", not console, "; ".join(console[:2]))
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
