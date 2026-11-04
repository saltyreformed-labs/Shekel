"""What every drive of the recurrence form harness shares: where the app answers, the pacing, the one check and the readers of what the form would post."""
import os
import subprocess
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
#: than the other way round: the dev app runs prod's Redis limiter at 30
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
