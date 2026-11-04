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

**The drives live beside this file since plan step credit_card:CC-6** (a pure
move, when it stood at 1,683 lines): ``_recurrence_form_checks`` is what they
share, and ``_recurrence_form_bound_drives``, ``_recurrence_form_loan_drives``
and ``_recurrence_form_offer_drives`` hold the drives this docstring names, and
``_recurrence_form_due_date_drive`` grades the repeat on each statement's due
date, which only a transfer into a configured card may take.

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
are not met.  It is paced: the dev app runs the real Redis limiter (30/minute
per IP, prod behaviour), and a burst meets 429s.
"""
import sys

from playwright.sync_api import sync_playwright

from _recurrence_form_bound_drives import (
    _drive_end_bound,
    _drive_month_ceiling,
    _drive_nominal_day,
    _drive_opening_bound,
)
from _recurrence_form_checks import (
    DEV_BASE_URL,
    SESSION_STATE,
    _check,
    _failures,
)
from _recurrence_form_due_date_drive import _drive_due_date_offer
from _recurrence_form_loan_drives import (
    _drive_edit_form_locks,
    _drive_loan_destination_lock,
)
from _recurrence_form_offer_drives import (
    _drive_refusals,
    _drive_visibility,
)


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
        _drive_due_date_offer(context, page)
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
