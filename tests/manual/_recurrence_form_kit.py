"""The shared instrument of the recurrence-form browser drives.

What every drive in ``verify_recurrence_form.py`` and its sibling
``verify_line_cadence_form.py`` reads the form through: where the dev app
answers and which database it writes, the record of checks, the paced settle,
and the readers that ask a real ``FormData`` what the browser would post.

Split out of ``verify_recurrence_form.py`` when that script passed 1,000 lines
(plan#2680; ruling R-IR, the session that breaks a module splits it).  Every
name kept its leading underscore and its docstring, so a reader of either
drive finds the reason each reader exists beside it.
"""
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

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
#:
#: **The limit is also 200 an HOUR per route** (``RATELIMIT_DEFAULT``,
#: ``app/config.py``), a moving window this pacing cannot reach: it bounds
#: how often a whole run can happen, not how fast one goes.  Measured
#: 2026-11-02 at plan#2680, which added the weekday drives: one whole run
#: (``VERIFY_WRITE=1``) makes 136 preview fetches; a run started within the
#: hour after other runs met 132 preview 429s through every preview check,
#: and the same code passed every check in a fresh window.  Measured again
#: 2026-11-05 at plan#2935, with every loan arm running on a copy holding a
#: payment-less loan and a one-time transfer into it: 185 fetches, so a drive
#: adding more than 15 would meet 429s in a fresh window too.  Run it once an
#: hour; the preview route's counter is
#: ``LIMITS:LIMITER/<ip>/templates.preview_recurrence/200/1/hour`` in the
#: dev Valkey, its length the fetches still in the window.
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


def _leave_date(page) -> None:
    """Move focus out of "Starts on", which is what says the date in it is final.

    ``page.fill`` leaves the box focused, and ``recurrence_form.js`` remembers a
    "repeating on" choice a date refused until the box is left, because a
    focused box may only be passing through that date.  A person leaves the box
    by clicking the next control; a drive that goes on to another date has to
    say so, or the choice comes back.  It fires no preview.

    Args:
        page: The Playwright page.
    """
    page.locator("#starts_on").blur()


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


def _selectable_unit(page, units: dict[str, str], label: str) -> str | None:
    """Return the id of the unit offered as *label*, or None unless a user can choose it.

    SELECTABLE, not merely present: an option the server renders and the page
    hides or disables reads the same as a live one in rendered HTML, and
    Playwright's ``select_option`` waits on a disabled option until its
    timeout and then raises, which would end the run rather than report it.

    Args:
        page: The Playwright page.
        units: The offered units, as :func:`_unit_ids` read them.
        label: The unit's plural label, e.g. ``"weeks"``.

    Returns:
        The unit's ref id, or ``None`` when no live option carries it.
    """
    if label not in units:
        return None
    live = page.evaluate(
        """(id) => Array.from(document.getElementById('recurrence_unit').options)
              .some(o => o.value === id && !o.hidden && !o.disabled)""",
        units[label])
    return units[label] if live else None


def _posted_opening(page) -> dict[str, list[str]]:
    """Return the opening-bound keys the form would actually submit.

    From a real ``FormData``, for the reason :func:`_posted_intervals` reads
    one: a control hidden by a class still SUBMITS and a disabled one does
    not, and rendered HTML cannot tell those apart.  Plan step R7b-4 turns on
    exactly that for the pay-period ``<select>``, which must post its value
    when the definition does NOT repeat and nothing at all when it does.

    ``repeats_on`` is the "repeating on" choice, the key plan step
    recurrence:R8-c gave the control when it began offering the weekday
    readings beside the day (the key was ``nominal_day`` until then).

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
                repeats_on: data.getAll('repeats_on'),
                start_period_id: data.getAll('start_period_id'),
            };
        }"""
    )


def _watch_previews(page) -> tuple[list[str], Callable[[], None]]:
    """Start recording the live preview's requests; return the record and its stop.

    The REQUEST is the evidence that an act re-fetched the preview, where
    the text left on screen is not: it reads the same whether or not a fetch
    happened.  The stop removes the listener, so a later drive on the same
    page records into no list nobody reads.  One recorder for the week
    unit's drive and the weekday reading's (plan#2680), which had each
    spelled it.

    Args:
        page: The Playwright page.

    Returns:
        The preview URLs requested from now on, appended as each is made, and
        the function that stops recording.
    """
    previews: list[str] = []

    def capture(request) -> None:
        if "preview-recurrence" in request.url:
            previews.append(request.url)

    page.on("request", capture)
    return previews, lambda: page.remove_listener("request", capture)


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
