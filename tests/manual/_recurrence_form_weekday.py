"""The "repeating on" control's weekday reading, in a real browser (plan step R8-c).

Plan step **recurrence:R8-c** (rulings plan#2668, plan#2670) made the
"repeating on" control ONE ``<select>`` (``repeats_on``) that reads a monthly
or yearly rule's first date one of four ways: the same day of the month, a
clamped month-end day, the weekday counted from the month's start ("the third
Tuesday"), or the month's last such weekday ("the last Tuesday").  A weekday
option's WORDS depend on the date the user is still typing, so the server
ships every phrase the option can show and ``recurrence_form.js``
(``syncRepeatsOn``) picks one, enables the readings the date allows, and hides
and disables the rest.  pytest sees the server's render for ONE date; it
cannot see the script re-word, re-enable or reset the control as the date
moves, nor what a real ``FormData`` posts after it did.  This module grades
that half, on the transaction and transfer forms:

* :func:`_drive_weekday_reading`: the control shows for a month and a year
  unit and hides AND disables for a paycheck and a week unit; the options each
  typed date allows are the only ones offered, worded for that date
  (:data:`EXPECTED_OFFERS`, hand-computed); choosing a weekday posts the one
  key, so a nominal day beside it is a pair no form can post; a move keeps a
  choice the new date allows (re-worded) and resets one it does not; a date
  typed digit by digit is judged by where it ends, not by the dates it passes
  through, and leaving the box makes a reset final; a move that asks the date
  nothing (a cadence with no day of a month, a cleared date) keeps it for when
  the control returns; and the live preview's request carries the reading and
  lists the paychecks the hand-computed dates fall in.
* :func:`_drive_weekday_round_trip`, under ``VERIFY_WRITE=1`` against a copy
  of the dev database: a save through the real form stores the reading,
  generates its rows on the hand-computed dates, reads "Monthly (third
  Tuesday)" in the Recurring page's Recurrence cell, and re-opens the edit
  form on the same choice.  What it made is deleted through the app's own
  permanent-delete doors (:func:`_cleanup_weekday`), never by SQL: a
  transfer is written only through ``transfer_service``.

The payroll-line form takes the same control, and ``verify_line_cadence_form.py``
drives it.
"""
import os
import time
from urllib.parse import parse_qs, urlsplit

from _recurrence_form_cadence import _choose_placement
from _recurrence_form_kit import (
    DEV_BASE_URL,
    DEV_DATABASE,
    POST_SPACING_SECONDS,
    _check,
    _leave_date,
    _posted_opening,
    _selectable_unit,
    _set_interval,
    _settle,
    _sql,
    _unit_ids,
    _visible,
    _watch_previews,
)

#: Whether the round trip runs: it WRITES, so it is opt-in and refused against
#: the shared ``shekel`` (:func:`_drive_weekday_round_trip`), the rule
#: ``verify_line_cadence_form.py``'s write pass set.
WRITE = os.environ.get("VERIFY_WRITE") == "1"

#: Names every definition the round trip creates, so :func:`_cleanup_weekday`
#: finds them after an aborted run.  Not the kit's ``MARK``: the refusal pass
#: counts ``MARK``-named templates as refusals that were accepted.
WEEKDAY_MARK = "ZZVERIFY-WEEKDAY"

#: Tue 2026-11-17.  November 2026's Tuesdays are the 3rd, 10th, 17th and
#: 24th, so the 17th is the THIRD counted from the start; counted from the
#: end (30 days) it is second-to-last, so the last-weekday reading is closed.
THIRD_TUESDAY = "2026-11-17"

#: Tue 2026-11-24: the FOURTH Tuesday from the start, and the LAST (24 + 7 is
#: past the 30th), so both weekday readings are open.
LAST_TUESDAY = "2026-11-24"

#: Thu 2026-04-30: April's last day in a 30-day month, so the 31st is open as
#: a nominal day; its LAST Thursday; and past the 28th, so no from-start week
#: (no fifth weekday, ruling plan#2670).
SHORT_MONTH_END = "2026-04-30"

#: Tue 2026-11-10: the SECOND Tuesday, the from-start reading re-worded.
SECOND_TUESDAY = "2026-11-10"

#: Mon 2026-11-30: past the 28th, so a from-start choice made on another date
#: must reset; its last-Monday reading stays open, and so does the 31st,
#: because the 30th is the LAST day of a 30-day month.  (The first run of
#: this drive, 2026-11-02, expected the 31st closed and was wrong: the app
#: offered it, as offerable_nominal_days says it must.)
MONTH_END_MONDAY = "2026-11-30"

#: What each typed date offers on a month unit, as ``(token, words)`` in the
#: control's order, hand-computed from the three dates above.  The tokens are
#: the wire's (``_picker.MonthReading.token``): ``""`` the same day of the
#: month, a nominal day's digits, a weekday reading's stored value.
EXPECTED_OFFERS: dict[str, list[tuple[str, str]]] = {
    THIRD_TUESDAY: [
        ("", "the same day of the month"),
        ("from_start", "the third Tuesday"),
    ],
    LAST_TUESDAY: [
        ("", "the same day of the month"),
        ("from_start", "the fourth Tuesday"),
        ("from_end", "the last Tuesday"),
    ],
    SHORT_MONTH_END: [
        ("", "the same day of the month"),
        ("31", "the last day of the month"),
        ("from_end", "the last Thursday"),
    ],
}

#: The first five third Tuesdays from :data:`THIRD_TUESDAY`, by hand: Nov 17;
#: Dec 1, 8, 15; Jan 5, 12, 19; Feb 2, 9, 16; Mar 2, 9, 16.
THIRD_TUESDAYS = ["2026-11-17", "2026-12-15", "2027-01-19", "2027-02-16", "2027-03-16"]

#: Dec 29, 2026, a Tuesday and its month's last, as a keyboard enters it.  Typed
#: over :data:`LAST_TUESDAY`, the month's first digit makes Jan 24, outside its
#: month's last seven days: the date a keystroke passes through that refuses a
#: last-weekday reading.
LAST_TUESDAY_TYPED = "12292026"

#: The date that first digit passes through.
LAST_TUESDAY_PASSES_THROUGH = "2026-01-24"

#: Nov 10, 2026, as a keyboard enters it: a date that ENDS on a refusal of the
#: last Tuesday, where :data:`LAST_TUESDAY_TYPED` only passes through one.
SECOND_TUESDAY_TYPED = "11102026"

#: The pause between a typed date's digits.
KEY_SPACING_MS = 120

#: The live preview's request, for :func:`_type_date` to answer.
PREVIEW_REQUESTS = "**/preview-recurrence*"

#: The same five months read as the 17th: what the weekday reading replaces.
SEVENTEENTHS = ["2026-11-17", "2026-12-17", "2027-01-17", "2027-02-17", "2027-03-17"]

#: What the Recurring page's Recurrence cell says of a monthly third-Tuesday
#: rule (``_describe``'s cadence words).
THIRD_TUESDAY_CADENCE = "Monthly (third Tuesday)"


def _options(page) -> list[dict]:
    """Return every "repeating on" option as the script left it.

    Args:
        page: The Playwright page.

    Returns:
        One ``{"token", "words", "hidden", "disabled"}`` per option, in order.
    """
    return page.evaluate(
        """() => Array.from(document.getElementById('repeats_on').options)
              .map(o => ({token: o.value, words: o.textContent.trim(),
                          hidden: o.hidden, disabled: o.disabled}))"""
    )


def _offered(page) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return the options a user can see and the options that can post, as ``(token, words)``.

    Read as TWO lists because ``syncRepeatsOn`` sets ``hidden`` and
    ``disabled`` together, and a check on one would pass an option the
    script left half on: a shown, disabled option the user cannot choose, or
    a hidden, enabled one a stale selection still posts.

    Args:
        page: The Playwright page.

    Returns:
        ``(shown, enabled)``.
    """
    options = _options(page)
    shown = [(o["token"], o["words"]) for o in options if not o["hidden"]]
    enabled = [(o["token"], o["words"]) for o in options if not o["disabled"]]
    return shown, enabled


def _posted_reading(page) -> list[str]:
    """Return every value the form would post under ``repeats_on``."""
    return _posted_opening(page)["repeats_on"]


def _chosen_reading(page) -> list[str]:
    """Return the chosen "repeating on" option as ``[token, words]``."""
    return page.evaluate(
        """() => { const s = document.getElementById('repeats_on');
                   return [s.value, s.options[s.selectedIndex].textContent.trim()]; }"""
    )


def _choose_reading(page, token: str) -> None:
    """Choose the "repeating on" option whose token is *token*, or record that a user cannot.

    Asked first, for the reason :func:`_selectable_unit` asks of a unit:
    Playwright's ``select_option`` waits on a disabled option until its
    timeout and then RAISES, which ends the run rather than reporting it.
    Measured here 2026-11-02: a script that never offered a weekday reading
    stopped this drive at its first choice, with every later check unrun.

    Args:
        page: The Playwright page.
        token: The option's token.
    """
    # The CONTROL as well as the option: a select the script disabled
    # refuses every option, the always-enabled "same day" one included.
    control_enabled = page.evaluate(
        "() => !document.getElementById('repeats_on').disabled")
    options = _options(page)
    if not control_enabled or not any(
            o["token"] == token and not o["hidden"] and not o["disabled"]
            for o in options):
        _check(f"the 'repeating on' option {token!r} can be chosen", False,
               f"control enabled={control_enabled}; offered="
               + str([(o["token"], o["words"]) for o in options
                      if not o["hidden"] and not o["disabled"]]))
        return
    page.select_option("#repeats_on", token)
    _settle(page)


def _form_owner(page) -> str:
    """Return the id of the user whose form this is, read off its account control.

    The pay calendar the preview lists is that user's, and the copy can hold
    two users; the account a form offers is the owner's by construction.

    Args:
        page: The Playwright page, on a create or edit form.

    Returns:
        The owner's user id.
    """
    # Any offered account will do, and the first non-empty one exists even
    # where the select opens on an empty "choose" entry.
    account = page.evaluate(
        """() => Array.from(document.querySelector(
                     '[name=account_id], [name=from_account_id]').options)
                 .map(o => o.value).find(v => v)"""
    )
    return _sql(f"SELECT user_id FROM budget.accounts WHERE id = {int(account)}")[0]


def _paychecks_holding(owner: str, days: list[str]) -> list[str]:
    """Return the saved paycheck holding each of *days*, as the preview words one.

    A saved period's end is the day before the next payday (the
    pay-calendar's derivation for a saved row), and the preview renders each
    as ``"Nov 05, 2026 - Nov 18, 2026"``.  The DATES are this module's own
    hand computation; the paydays are the owner's data.

    Args:
        owner: The user id.
        days: ISO dates, each inside the saved schedule.

    Returns:
        One period per day, in the order given.
    """
    values = ", ".join(f"({i}, DATE '{day}')" for i, day in enumerate(days))
    return _sql(
        "WITH p AS (SELECT start_date, "
        "lead(start_date) OVER (ORDER BY start_date) - 1 AS end_date "
        f"FROM budget.pay_periods WHERE user_id = {int(owner)}) "
        "SELECT to_char(p.start_date, 'Mon DD, YYYY') || ' - ' "
        "|| to_char(p.end_date, 'Mon DD, YYYY') "
        f"FROM (VALUES {values}) AS d(i, day) "
        "JOIN p ON d.day BETWEEN p.start_date AND p.end_date ORDER BY d.i"
    )


def _preview_items(page) -> list[str]:
    """Return the periods the live preview lists, as rendered."""
    return [text.strip() for text in page.evaluate(
        """() => Array.from(document.querySelectorAll('#recurrence-preview li'))
                 .map(li => li.textContent)"""
    )]


def _drive_offers(page, kind: str, units: dict[str, str]) -> None:
    """Check each hand-computed date offers exactly its readings, worded for it.

    Args:
        page: The Playwright page, on the create form.
        kind: "transaction" or "transfer", for the labels.
        units: The offered units, as :func:`_unit_ids` read them.
    """
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    for day, expected in EXPECTED_OFFERS.items():
        page.fill("#starts_on", day)
        _settle(page)
        shown, enabled = _offered(page)
        _check(f"{kind} WR: {day} shows exactly {[t for t, _ in expected]}, worded",
               shown == expected, str(shown))
        _check(f"{kind} WR: {day} enables exactly the options it shows",
               enabled == expected, str(enabled))

    # --- choosing a weekday: the ONE key, so no nominal day rides beside it -
    # Apr 30 offers both a nominal day and a weekday reading; the day is
    # chosen first so the weekday choice REPLACES a posted day rather than
    # meeting an empty control.
    _choose_reading(page, "31")
    _check(f"{kind} WR: precondition -- the 31st posts on Apr 30",
           _posted_reading(page) == ["31"], str(_posted_reading(page)))
    _choose_reading(page, "from_end")
    _check(f"{kind} WR: choosing the last Thursday posts that one reading, once",
           _posted_reading(page) == ["from_end"], str(_posted_reading(page)))
    _check(f"{kind} WR: no control posts a nominal day under another name",
           page.evaluate(
               """() => document.querySelectorAll('[name=nominal_day]').length === 0"""),
           "a nominal_day control is rendered beside the reading")


def _drive_moves(page, kind: str) -> None:
    """Check a move keeps a choice the new date allows, re-worded, and resets one it cannot hold.

    Args:
        page: The Playwright page, on the create form, a month unit chosen.
        kind: "transaction" or "transfer", for the labels.
    """
    page.fill("#starts_on", THIRD_TUESDAY)
    _settle(page)
    _leave_date(page)
    _choose_reading(page, "from_start")
    page.fill("#starts_on", SECOND_TUESDAY)
    _settle(page)
    _check(f"{kind} WR: moving to Nov 10 keeps the from-start choice, re-worded",
           _chosen_reading(page) == ["from_start", "the second Tuesday"],
           str(_chosen_reading(page)))
    _check(f"{kind} WR: and it still posts once",
           _posted_reading(page) == ["from_start"], str(_posted_reading(page)))
    # A box holding no complete date has answered nothing, so the choice waits.
    page.fill("#starts_on", "")
    _settle(page)
    _check(f"{kind} WR: a cleared date posts no reading",
           _posted_reading(page) == [], str(_posted_reading(page)))
    page.fill("#starts_on", SECOND_TUESDAY)
    _settle(page)
    _check(f"{kind} WR: the same date retyped keeps the choice",
           _chosen_reading(page) == ["from_start", "the second Tuesday"]
           and _posted_reading(page) == ["from_start"],
           f"{_chosen_reading(page)}; posts {_posted_reading(page)}")
    # In the box, Nov 30 may be a date on the way to another: what the control
    # shows and posts is the date's answer at once, and the choice can return.
    page.fill("#starts_on", MONTH_END_MONDAY)
    _settle(page)
    _check(f"{kind} WR: moving to Nov 30 (no from-start week) shows and posts the same day",
           _chosen_reading(page) == ["", "the same day of the month"]
           and _posted_reading(page) == [""],
           f"{_chosen_reading(page)}; posts {_posted_reading(page)}")
    page.fill("#starts_on", SECOND_TUESDAY)
    _settle(page)
    _check(f"{kind} WR: a later date in the box that allows it brings the choice back",
           _chosen_reading(page) == ["from_start", "the second Tuesday"]
           and _posted_reading(page) == ["from_start"],
           f"{_chosen_reading(page)}; posts {_posted_reading(page)}")
    # Out of the box, Nov 30 was the date the user meant: the reset is final.
    page.fill("#starts_on", MONTH_END_MONDAY)
    _settle(page)
    _leave_date(page)
    page.fill("#starts_on", SECOND_TUESDAY)
    _settle(page)
    _check(f"{kind} WR: leaving the box on Nov 30 makes the reset final",
           _posted_reading(page) == [""], str(_posted_reading(page)))
    page.fill("#starts_on", MONTH_END_MONDAY)
    _settle(page)
    shown, _ = _offered(page)
    _check(f"{kind} WR: Nov 30 offers the same day, the month's last day and the last Monday",
           shown == [("", "the same day of the month"), ("31", "the last day of the month"),
                     ("from_end", "the last Monday")],
           str(shown))


def _type_date(page, digits: str) -> list[list[str]]:
    """Enter a date in "Starts on" a digit at a time, as a keyboard does.

    ``page.fill`` sets the value whole.  A keyboard does not: the browser fires
    ``change`` for each digit that leaves a complete date.  Measured 2026-11-05:
    Nov 24 typed as 12/29/2026 changed to Jan 24, Dec 24, Dec 2, Dec 29, then
    three dates in years 2, 20 and 202, then Dec 29, 2026.  Each change fetches
    a preview, eight within a second, past what the run's pacing leaves under
    the limiter's 30 a minute, so they are answered here and never reach the
    app.  What is graded is the control, which no preview's answer touches.

    Args:
        page: The Playwright page.
        digits: The month, day and year digits, in the order they are typed.

    Returns:
        One ``[date, reading]`` per ``change``, read after the script's own
        listener ran, so a check can say the date did pass through.
    """
    _leave_date(page)
    page.route(PREVIEW_REQUESTS, lambda route: route.fulfill(
        status=200, content_type="text/html", body=""))
    page.evaluate(
        """() => {
            const box = document.getElementById('starts_on');
            window.typedDate = {changes: [], listener: () => window.typedDate.changes.push(
                [box.value, document.getElementById('repeats_on').value])};
            box.addEventListener('change', window.typedDate.listener);
        }"""
    )
    page.locator("#starts_on").focus()
    for digit in digits:
        page.keyboard.press(digit)
        page.wait_for_timeout(KEY_SPACING_MS)
    changes = page.evaluate(
        """() => {
            document.getElementById('starts_on')
                .removeEventListener('change', window.typedDate.listener);
            return window.typedDate.changes;
        }"""
    )
    page.unroute(PREVIEW_REQUESTS)
    return changes


def _drive_typed_date(page, kind: str) -> None:
    """Check a typed date is judged by where it ends, not by the dates it passes through.

    Args:
        page: The Playwright page, on the create form, a month unit chosen.
        kind: "transaction" or "transfer", for the labels.
    """
    page.fill("#starts_on", LAST_TUESDAY)
    _settle(page)
    _choose_reading(page, "from_end")
    changes = _type_date(page, LAST_TUESDAY_TYPED)
    _check(f"{kind} WR: precondition -- typing passes through Jan 24, clearing the choice",
           [LAST_TUESDAY_PASSES_THROUGH, ""] in changes, str(changes))
    _check(f"{kind} WR: typing it ends on Dec 29",
           _posted_opening(page)["starts_on"] == ["2026-12-29"],
           str(_posted_opening(page)["starts_on"]))
    _check(f"{kind} WR: typing Dec 29 through Jan 24 keeps the last Tuesday",
           _chosen_reading(page) == ["from_end", "the last Tuesday"]
           and _posted_reading(page) == ["from_end"],
           f"{_chosen_reading(page)}; posts {_posted_reading(page)}")
    _type_date(page, SECOND_TUESDAY_TYPED)
    _leave_date(page)
    _check(f"{kind} WR: typing a date that ends on a refusal resets it",
           _posted_reading(page) == [""], str(_posted_reading(page)))


def _drive_units(page, kind: str, units: dict[str, str]) -> None:
    """Check the control shows for months and years and hides AND disables for paychecks and weeks.

    Each cadence without a day of a month is reached from a month unit with a
    weekday chosen, so "hidden" is a change the script made, and "posts
    nothing" is a choice it withdrew rather than one never made.  The way back
    is graded too: none of those moves asked the date, so the choice returns
    with the control, and only a date the choice does not fit resets it.

    Args:
        page: The Playwright page, on the create form.
        kind: "transaction" or "transfer", for the labels.
        units: The offered units, as :func:`_unit_ids` read them.
    """
    page.fill("#starts_on", THIRD_TUESDAY)
    _settle(page)
    _leave_date(page)
    for label in ("months", "years"):
        page.locator("#recurrence_unit").select_option(units[label])
        _settle(page)
        _check(f"{kind} WU: the control is shown for {label}",
               _visible(page, "field-repeats-on"), "hidden")
        _check(f"{kind} WU: {label} offers the third Tuesday on Nov 17",
               ("from_start", "the third Tuesday") in _offered(page)[1],
               str(_offered(page)[1]))
    weeks = _selectable_unit(page, units, "weeks")
    others = [("paychecks", units["paychecks"])] + ([("weeks", weeks)] if weeks else [])
    others.append(("no cadence", ""))
    if weeks is None:
        print("   SKIPPED (weeks arm): no live weeks option; the visibility pass fails it")
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    _choose_reading(page, "from_start")
    for label, unit in others:
        page.locator("#recurrence_unit").select_option(unit)
        _settle(page)
        _check(f"{kind} WU: the control is hidden for {label}",
               not _visible(page, "field-repeats-on"), "visible")
        _check(f"{kind} WU: the control is disabled for {label}",
               page.locator("#repeats_on").is_disabled(), "enabled")
        _check(f"{kind} WU: the weekday chosen for months posts NOTHING for {label}",
               _posted_reading(page) == [], str(_posted_reading(page)))
        page.locator("#recurrence_unit").select_option(units["months"])
        _settle(page)
        _check(f"{kind} WU: the third Tuesday comes back with months after {label}",
               _chosen_reading(page) == ["from_start", "the third Tuesday"]
               and _posted_reading(page) == ["from_start"],
               f"{_chosen_reading(page)}; posts {_posted_reading(page)}")
    page.locator("#recurrence_unit").select_option(units["paychecks"])
    _settle(page)
    page.fill("#starts_on", MONTH_END_MONDAY)
    _settle(page)
    _leave_date(page)
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    _check(f"{kind} WU: a date moved while it was hidden still answers: Nov 30 resets it",
           _posted_reading(page) == [""], str(_posted_reading(page)))
    # That refusal met an unfocused box, so nothing remembers the choice.
    page.fill("#starts_on", THIRD_TUESDAY)
    _settle(page)
    _check(f"{kind} WU: a reset made outside the box stays final on a date allowing it",
           _posted_reading(page) == [""], str(_posted_reading(page)))


def _drive_preview(page, kind: str, units: dict[str, str]) -> None:
    """Check the preview's request carries the reading and lists the hand-computed paychecks.

    Both readings are previewed from Nov 17 and each list compared with the
    paychecks its own hand-computed dates fall in.  That is a test of the
    reading only where the two lists PART, which is a property of the
    owner's paydays, so it is checked as a precondition rather than assumed:
    on the production copy of 2026-11-02 (biweekly Thursdays from Mar 26) the
    third Tuesday Dec 15 is in the Dec 3 paycheck and Dec 17 in its own.  The
    REQUEST is read too, as :func:`_check_week_preview` reads one: the text
    left on screen cannot say whether a fetch happened.

    Args:
        page: The Playwright page, on the create form.
        kind: "transaction" or "transfer", for the labels.
        units: The offered units, as :func:`_unit_ids` read them.
    """
    owner = _form_owner(page)
    weekday_periods = _paychecks_holding(owner, THIRD_TUESDAYS)
    day_periods = _paychecks_holding(owner, SEVENTEENTHS)
    _check(f"{kind} WP: precondition -- every hand-computed date is in a saved paycheck",
           len(weekday_periods) == len(THIRD_TUESDAYS) == len(day_periods),
           f"{weekday_periods} / {day_periods}")
    _check(f"{kind} WP: precondition -- on this calendar the two readings part",
           weekday_periods != day_periods,
           "the third Tuesdays and the 17ths fall in the same paychecks here: "
           "choose dates where they part")

    previews, stop_watching = _watch_previews(page)
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)
    _set_interval(page, 1)
    _choose_placement(page, 0)
    page.fill("#starts_on", THIRD_TUESDAY)
    _settle(page)

    previews.clear()
    _choose_reading(page, "from_start")
    query = parse_qs(urlsplit(previews[-1]).query) if previews else {}
    _check(f"{kind} WP: choosing the third Tuesday re-fetched the preview carrying it",
           query.get("repeats_on") == ["from_start"]
           and query.get("starts_on") == [THIRD_TUESDAY],
           previews[-1] if previews else "no preview request was made")
    _check(f"{kind} WP: the preview lists the paychecks of the hand-computed third Tuesdays",
           _preview_items(page) == weekday_periods,
           f"{_preview_items(page)} != {weekday_periods}")

    previews.clear()
    _choose_reading(page, "")
    query = parse_qs(urlsplit(previews[-1]).query) if previews else {}
    _check(f"{kind} WP: choosing the same day re-fetched the preview without a reading",
           bool(previews) and "repeats_on" not in query,
           previews[-1] if previews else "no preview request was made")
    _check(f"{kind} WP: the preview lists the paychecks of the 17ths",
           _preview_items(page) == day_periods,
           f"{_preview_items(page)} != {day_periods}")
    stop_watching()


def _drive_weekday_reading(page, kind: str, url: str) -> None:
    """Check the weekday reading's offers, moves, units and preview on one create form.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} weekday reading: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)
    _drive_offers(page, kind, units)
    _drive_moves(page, kind)
    _drive_typed_date(page, kind)
    _drive_units(page, kind, units)
    _drive_preview(page, kind, units)


def _csrf(page) -> str:
    """Return the CSRF token the page's form carries."""
    return page.evaluate(
        "() => document.querySelector('input[name=csrf_token]').value")


def _marked(table: str) -> list[str]:
    """Return the ids of the definitions in *table* the round trip named."""
    return _sql(f"SELECT id FROM budget.{table} WHERE name LIKE '{WEEKDAY_MARK}%' ORDER BY id")


def _cleanup_weekday(context, page) -> None:
    """Delete every definition the round trip named, through the app's permanent-delete doors.

    The doors' own refusals (paid history, a held movement, a merchant rule)
    cannot apply to a definition this pass made a moment ago and never
    settled, so a survivor is a failure to report, which the caller checks.

    Args:
        context: The Playwright browser context (for its request API).
        page: A page on a form, for the CSRF token.
    """
    doors = (("transaction_templates", "templates"), ("transfer_templates", "transfers"))
    if not any(_marked(table) for table, _ in doors):
        return
    token = _csrf(page)
    for table, prefix in doors:
        for definition_id in _marked(table):
            context.request.post(
                f"{DEV_BASE_URL}/{prefix}/{definition_id}/hard-delete",
                form={"csrf_token": token},
                headers={"Referer": f"{DEV_BASE_URL}/templates"},
            )
            print(f"   cleanup: deleted {prefix} {definition_id}")
            time.sleep(POST_SPACING_SECONDS)


def _fill_definition(page, kind: str, name: str) -> None:
    """Fill the round trip's non-cadence controls: a name, an amount, its accounts.

    The source is the owner's ``Checking`` (the refusal pass's idiom) and a
    transfer's destination the first offered account of the plain
    ``Savings`` type: a loan destination would lock the bounds this pass
    states, and a card's would make it a payment.

    Args:
        page: The Playwright page, on the create form.
        kind: "transaction" or "transfer".
        name: The definition's name.
    """
    page.fill("#name", name)
    page.fill("#amount", "1.00")
    checking = _sql("SELECT id FROM budget.accounts WHERE name = 'Checking' "
                    f"AND user_id = {int(_form_owner(page))}")[0]
    if kind == "transaction":
        page.select_option("#account_id", checking)
        category = page.evaluate(
            """() => Array.from(document.getElementById('category_id').options)
                     .map(o => o.value).find(v => v)""")
        page.select_option("#category_id", category)
        return
    savings = _sql(
        "SELECT a.id FROM budget.accounts a JOIN ref.account_types t "
        "ON t.id = a.account_type_id WHERE t.name = 'Savings' AND a.is_active "
        f"AND a.user_id = {int(_form_owner(page))} ORDER BY a.id LIMIT 1")[0]
    page.select_option("#from_account_id", checking)
    page.select_option("#to_account_id", savings)
    _settle(page)


def _check_stored(kind: str, definition_id: str, rows_table: str) -> None:
    """Check the rule one round trip's save wrote, and the rows it generated.

    Args:
        kind: "transaction" or "transfer", for the labels and the links.
        definition_id: The saved definition's id.
        rows_table: The table its generated rows live in.
    """
    # A transaction's rule and rows link to it under two names; a
    # transfer's under one.
    rule_link, row_link = (
        ("transaction_template_id", "template_id") if kind == "transaction"
        else ("transfer_template_id", "transfer_template_id")
    )
    rule = _sql("SELECT starts_on, coalesce(nominal_day::text, ''), "
                "coalesce(weekday_reading, '') FROM budget.recurrence_rules "
                f"WHERE {rule_link} = {int(definition_id)}")
    _check(f"{kind} WT: the rule stores Nov 17, no nominal day, and the from-start reading",
           rule == [f"{THIRD_TUESDAY}||from_start"], str(rule))
    due = _sql(f"SELECT due_date FROM budget.{rows_table} "
               f"WHERE {row_link} = {int(definition_id)} "
               "AND NOT is_deleted ORDER BY due_date LIMIT 5")
    _check(f"{kind} WT: its first five rows are due on the hand-computed third Tuesdays",
           due == THIRD_TUESDAYS, str(due))


def _check_shown(page, kind: str, name: str, edit_path: str) -> None:
    """Check the Recurring page words the saved rule, and its edit form re-opens on it.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        name: The saved definition's name.
        edit_path: Its edit form's path.
    """
    page.goto(f"{DEV_BASE_URL}/templates", wait_until="domcontentloaded")
    # Name, then category (a transfer: its accounts), amount, Recurrence.
    cell = page.locator(f'tr[data-sort-name="{name.lower()}"] td').nth(3)
    words = cell.inner_text().strip().splitlines()[0] if cell.count() else "no such row"
    _check(f"{kind} WT: the Recurrence cell reads {THIRD_TUESDAY_CADENCE!r}",
           words == THIRD_TUESDAY_CADENCE, words)

    page.goto(f"{DEV_BASE_URL}{edit_path}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    _check(f"{kind} WT: the edit form re-opens on the third Tuesday",
           _chosen_reading(page) == ["from_start", "the third Tuesday"],
           str(_chosen_reading(page)))
    _check(f"{kind} WT: the edit form shows the control and posts the reading once",
           _visible(page, "field-repeats-on") and _posted_reading(page) == ["from_start"],
           str(_posted_reading(page)))


def _check_saved(page, kind: str, where: tuple[str, str, str], said: str) -> None:
    """Check what one round trip's save stored, generated, listed and re-opens.

    Args:
        page: The Playwright page, on the page the save answered with.
        kind: "transaction" or "transfer", for the labels.
        where: The definition's table, the table its generated rows live in,
            and its edit form's path prefix (``templates`` or ``transfers``).
        said: The alerts and field errors the save answered with, shown when
            it made nothing.
    """
    table, rows_table, edit_prefix = where
    name = f"{WEEKDAY_MARK}-{kind}"
    ids = _sql(f"SELECT id FROM budget.{table} WHERE name = '{name}'")
    _check(f"{kind} WT: the save made one definition", len(ids) == 1,
           f"{ids}; the page said: {said or 'nothing'}")
    if len(ids) != 1:
        return
    _check_stored(kind, ids[0], rows_table)
    _check_shown(page, kind, name, f"/{edit_prefix}/{ids[0]}/edit")


def _drive_weekday_round_trip(context, page) -> None:
    """Save a third-Tuesday definition through each real form, and read it back everywhere.

    Refused against the shared ``shekel`` like the line form's write pass:
    a save GENERATES rows, and the copy the app is pointed at is the one to
    write.  Every definition is named :data:`WEEKDAY_MARK` and deleted
    before and after (:func:`_cleanup_weekday`).

    Args:
        context: The Playwright browser context (for its request API).
        page: The Playwright page.
    """
    print("\n=== weekday round trip (VERIFY_WRITE=1) ===")
    if not WRITE:
        print("   SKIPPED: set VERIFY_WRITE=1 against a copy of the dev database to run it")
        return
    if DEV_DATABASE == "shekel":
        _check("the weekday round trip runs against a copy, never the shared 'shekel'",
               False, "point the app at a copy and set VERIFY_DEV_DATABASE to its name")
        return
    for kind, url, table, rows_table, prefix in (
            ("transaction", "/templates/new?type=expense", "transaction_templates",
             "transactions", "templates"),
            ("transfer", "/transfers/new", "transfer_templates", "transfers", "transfers")):
        page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
        page.wait_for_selector("#recurrence_unit")
        _cleanup_weekday(context, page)
        units = _unit_ids(page)
        _fill_definition(page, kind, f"{WEEKDAY_MARK}-{kind}")
        page.locator("#recurrence_unit").select_option(units["months"])
        _settle(page)
        _set_interval(page, 1)
        _choose_placement(page, 0)
        page.fill("#starts_on", THIRD_TUESDAY)
        _settle(page)
        _choose_reading(page, "from_start")
        _check(f"{kind} WT: the form posts the third Tuesday before the save",
               _posted_reading(page) == ["from_start"], str(_posted_reading(page)))
        # The definition's own form: the page holds other POST forms (the
        # navigation's), and the first submit button on it may be theirs.
        with page.expect_navigation():
            page.click("form:has(#recurrence_unit) button[type=submit]")
        time.sleep(POST_SPACING_SECONDS)
        said = " | ".join(page.locator(".alert, .invalid-feedback").all_inner_texts())
        _check_saved(page, kind, (table, rows_table, prefix), said)
        _cleanup_weekday(context, page)
    _check("the round trip left no marked definition behind",
           not _marked("transaction_templates") and not _marked("transfer_templates"),
           f"{_marked('transaction_templates')} / {_marked('transfer_templates')}")
