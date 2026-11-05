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
``tests/test_routes/test_templates/test_a_loan_payments_recurrence.py``
instead, because it is a property of the SERVER's render rather than of the
script.

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

**Plan step R8-b made the WEEK unit authorable**, and each control it reaches
is graded by the drive that owns that control: the week option is offered and
selectable, its interval box and BOTH funding choices enable and the preview
re-fetches for each reading and lists its occurrences (:func:`_drive_visibility`);
the per-month ceiling row shows
for it (:func:`_drive_month_ceiling`); and the "repeating on" day control
stays hidden beside it on a date that opens that question for a monthly
cadence (:func:`_drive_nominal_day`).  The two WEEK refusal cases went with
the refusal (:func:`_drive_refusals` says what replaced them).

**Plan step recurrence:R8-c gave the "repeating on" control its weekday
readings** ("the third Tuesday", "the last Tuesday"), worded by the script for
the date being typed, and ``_recurrence_form_weekday.py`` grades them on both
forms: which readings each date offers and in which words, a choice kept or
reset as the date moves, the units that show the control, and the preview's
request and paychecks (:func:`_drive_weekday_reading`).

**By default it writes nothing.**  Every check reads the form's own DOM; the
crafted POSTs in the refusal pass are all expected to be REFUSED, and the pass
asserts that no template and no recurrence rule was persisted by any of them.
**``VERIFY_WRITE=1`` adds one pass that writes** (plan#2680): a third-Tuesday
definition saved through each real form and read back from the rule, its
rows, the Recurring page and the edit form, then deleted through the app's own
permanent-delete doors (:func:`_drive_weekday_round_trip`).  It is refused
against the shared ``shekel``, as the line form's write pass is.  Run against a
clone either way.

Preconditions:
  * the containerized dev app is up and answering on ``DEV_BASE_URL``
    (``docker compose -f docker-compose.dev.yml up -d`` from THIS checkout --
    the app bind-mounts the directory compose runs from, so a dev app started
    elsewhere serves that code and this proves nothing about yours);
  * ``tests/manual/.dev_session_state.json`` exists and is unexpired
    (``python tests/manual/save_dev_session.py``).

Usage:
    python tests/manual/verify_recurrence_form.py
    VERIFY_WRITE=1 VERIFY_DEV_DATABASE=<clone> python tests/manual/verify_recurrence_form.py

Exit code 0 when every check passes, 1 on a failure, 2 when the preconditions
are not met.  It is paced: the dev app runs the real Valkey limiter (30/minute
per IP, prod behaviour), and a burst meets 429s.

**Its drives live in three sibling modules** since plan#2680 split this
script at 1,000 lines: ``_recurrence_form_kit.py`` (the shared instrument:
where the app answers, the check record and the ``FormData`` readers, which
``verify_line_cadence_form.py`` reads too), ``_recurrence_form_cadence.py``
(the drives run once per form kind) and ``_recurrence_form_loans.py`` (the
transfer form's loan locks).  This file runs them, and holds the refusal pass.
"""
import sys
import time

from playwright.sync_api import sync_playwright

from _recurrence_form_cadence import (
    _drive_end_bound,
    _drive_month_ceiling,
    _drive_nominal_day,
    _drive_opening_bound,
    _drive_visibility,
)
from _recurrence_form_kit import (
    DEV_BASE_URL,
    MARK,
    POST_SPACING_SECONDS,
    SESSION_STATE,
    _check,
    _failures,
    _sql,
)
from _recurrence_form_loans import _drive_edit_form_locks, _drive_loan_destination_lock
from _recurrence_form_weekday import _drive_weekday_reading, _drive_weekday_round_trip


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
        "name": f"{MARK}-refused", "amount": "10.00",
        "category_id": ids["category"], "account_id": ids["account"],
        "transaction_type_id": ids["expense"],
        "starts_on": "2026-03-15",
    }
    rules_before = _sql("SELECT count(*) FROM budget.recurrence_rules")[0]

    cases = [
        # **The unauthorable SET has MOVED THREE TIMES, and this list is
        # where that shows.**  It was every month or year INTERVAL the closed
        # pattern set could not name -- "every other month", "quarterly funded
        # from the first paycheck" -- because STORAGE was the binding
        # constraint, and freeing the interval at R7c-c is what this arc exists
        # for.  It was then the two ``(unit, placement)`` pairs
        # ``anchor_family`` had no first-occurrence derivation for, and plan
        # step **R8-a** measured one of those STALE: ruling R-R16 made the
        # first occurrence AUTHORED at R7c-b and deleted the derivation the
        # refusal cited, so a year-scale cadence funded from a later paycheck
        # SAVES now.  R8-a replaced that case with the WEEK unit's other
        # placement, beside the one already here: both withheld because a
        # weekly row had no date to carry.  Plan step **R8-b** dates a row
        # from its occurrence and offers weeks at both placements, so those
        # two cases went too.
        #
        # **Leaving a stale case here is not harmless.**  These payloads carry
        # the whole ``base_form``, and ``POST /templates`` GENERATES on create
        # -- so an accepted "refusal" writes a template, a rule and its
        # projected rows into whatever database this is pointed at, and then
        # trips the two persistence checks at the end of this pass, which is
        # how a real regression comes to be read as "the known week failure".
        # What ``authorable_cadences`` withholds now is ONE reading: a
        # pay-period cadence funded from a LATER paycheck.  Its placement is
        # inert -- a payday is carried back to its own paycheck either way --
        # so storing the choice would store a control the engine ignores.
        ("a pay-period cadence funded from a later paycheck (an inert placement)",
         {"recurrence_unit": ids["period"], "interval_n": "1",
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
            _drive_weekday_reading(page, kind, url)
            _drive_month_ceiling(page, kind, url)
            _drive_end_bound(page, kind, url)
        # Transfer-only: the transaction form has no destination account, so
        # its definition can never be a loan payment.
        _drive_loan_destination_lock(page)
        _drive_edit_form_locks(page)
        _drive_refusals(context, page)
        # After the refusals: their pass counts every recurrence rule, and
        # this one makes rules (and deletes them).
        _drive_weekday_round_trip(context, page)

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
