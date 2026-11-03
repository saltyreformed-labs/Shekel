"""The recurrence form's cadence drives: which rows each choice shows and what posts.

The drives ``verify_recurrence_form.py`` runs once per form kind (the
transaction and the transfer create forms): the unit, interval and funding
rows (:func:`_drive_visibility`, the week unit's own arm in
:func:`_drive_week_visibility`), the "Starts on" swap
(:func:`_drive_opening_bound`), the "repeating on" control's day options
(:func:`_drive_nominal_day`; its weekday readings are
``_recurrence_form_weekday.py``'s), the per-month ceiling
(:func:`_drive_month_ceiling`) and the "Ends" control
(:func:`_drive_end_bound`).  The module docstring of
``verify_recurrence_form.py`` says why a browser grades them.

Split out of that script when it passed 1,000 lines (plan#2680).
"""
from urllib.parse import parse_qs, urlsplit

from _recurrence_form_kit import (
    DEV_BASE_URL,
    _check,
    _posted_bound,
    _posted_intervals,
    _posted_opening,
    _select_end_mode,
    _selectable_unit,
    _set_interval,
    _settle,
    _unit_ids,
    _visible,
    _watch_previews,
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


def _offered_days(page) -> list[str]:
    """Return the nominal days the "repeating on" control offers, as the tokens they post.

    Offered means neither hidden nor disabled: ``syncRepeatsOn`` sets the two
    together, and a check on one would pass an option the script left half
    on.  Only the DAY options (``data-day``) are read; the weekday readings
    beside them are :func:`_recurrence_form_weekday._offered`'s.

    Args:
        page: The Playwright page.

    Returns:
        The offered days' tokens, in the control's order.
    """
    return page.evaluate(
        """() => Array.from(document.getElementById('repeats_on').options)
              .filter(o => o.hasAttribute('data-day') && !o.hidden && !o.disabled)
              .map(o => o.value)"""
    )


def _drive_nominal_day(page, kind: str, url: str) -> None:
    """Check the "repeating on" control's DAY options appear and post only where they mean.

    **Plan step R7c-b's control, and the same defect class one field
    over.**  A nominal day records the day a rule MEANS when its first
    occurrence's own month was too short to hold it -- 2026-04-30 is "the 30th"
    or "the last day of the month", and those are different cadences from May
    on.  So a day option is offered ONLY where the chosen date leaves that
    question open, and it must be DISABLED as well as hidden everywhere else:
    a stale day beside a date that never clamped is exactly what
    ``ck_recurrence_rules_nominal_day`` refuses, and a rendered-HTML
    assertion cannot tell "hidden" from "hidden and still submitting".  Only
    the days ABOVE the chosen one are meaningful, so 2026-04-30 must offer the
    31st and not the 30th, and moving to a month that holds them all must
    clear the selection rather than leave it stating a day the rule no longer
    fires on.

    **Plan step recurrence:R8-c changed what this drive can say about the
    ROW.**  The control became one ``<select>`` (``repeats_on``) offering the
    weekday readings beside the day (rulings plan#2668, plan#2670), and every
    month or year date has one -- the 1st-28th counted from the month's
    start, the last seven days from its end -- so the row is shown for every
    date on those units and posts the same day of the month (``""``) where no
    day is open.  Until R8-c this drive asserted the row HIDDEN, posting
    nothing, on 2026-04-15 and 2026-05-31; those two checks now read the day
    options instead, and the row's own visibility per unit is
    :func:`_recurrence_form_weekday._drive_weekday_reading`'s.  A unit with no
    day of a month still hides AND disables the whole control, so a day
    chosen for months posts nothing for weeks.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        url: The create form's path.
    """
    print(f"\n=== {kind} nominal day: {url} ===")
    page.goto(f"{DEV_BASE_URL}{url}", wait_until="domcontentloaded")
    page.wait_for_selector("#recurrence_unit")
    units = _unit_ids(page)

    # A MONTHLY cadence, one of the two units with a day-of-month
    # coordinate at all.
    page.locator("#recurrence_unit").select_option(units["months"])
    _settle(page)

    # --- a date its month CAN hold: no day to ask ------------------------
    page.fill("#starts_on", "2026-04-15")
    _settle(page)
    _check(f"{kind} N: no nominal day is offered for a mid-month date",
           _offered_days(page) == [], str(_offered_days(page)))
    _check(f"{kind} N: a mid-month date posts the same day of the month",
           _posted_opening(page)["repeats_on"] == [""],
           str(_posted_opening(page)["repeats_on"]))

    # --- a month's LAST day in a short month: the question is open -------
    page.fill("#starts_on", "2026-04-30")
    _settle(page)
    _check(f"{kind} N: repeating-on is SHOWN on a short month's last day",
           _visible(page, "field-repeats-on"), "hidden")
    _check(f"{kind} N: only the days ABOVE the 30th are offered",
           _offered_days(page) == ["31"], str(_offered_days(page)))

    page.select_option("#repeats_on", "31")
    _settle(page)
    _check(f"{kind} N: a chosen nominal day posts exactly once",
           _posted_opening(page)["repeats_on"] == ["31"],
           str(_posted_opening(page)["repeats_on"]))

    # --- back to a month that holds every day: the choice must GO --------
    page.fill("#starts_on", "2026-05-31")
    _settle(page)
    _check(f"{kind} N: no nominal day is offered on a 31-day month's end",
           _offered_days(page) == [], str(_offered_days(page)))
    _check(f"{kind} N: the stale nominal day resets to the same day after the move",
           _posted_opening(page)["repeats_on"] == [""],
           str(_posted_opening(page)["repeats_on"]))

    # --- weeks (plan step R8-b): a unit with no day of the month ---------
    # The question is re-opened on the MONTHLY cadence first, a day chosen,
    # so that "hidden for weeks" below is a change the script made rather
    # than a control that was never shown.
    weeks = _selectable_unit(page, units, "weeks")
    if weeks is None:
        print("   SKIPPED (weeks arm): no live weeks option; the visibility pass fails it")
        return
    page.fill("#starts_on", "2026-04-30")
    _settle(page)
    page.select_option("#repeats_on", "31")
    _settle(page)
    _check(f"{kind} N: precondition -- months on Apr 30 shows repeating-on, 31 posting",
           _visible(page, "field-repeats-on")
           and _posted_opening(page)["repeats_on"] == ["31"],
           str(_posted_opening(page)["repeats_on"]))
    page.locator("#recurrence_unit").select_option(weeks)
    _settle(page)
    _check(f"{kind} N: repeating-on is hidden for weeks on the same date",
           not _visible(page, "field-repeats-on"), "visible")
    _check(f"{kind} N: the day chosen for months posts NOTHING for weeks",
           _posted_opening(page)["repeats_on"] == [],
           str(_posted_opening(page)["repeats_on"]))


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
    occurrences can repeat within a month (paychecks, and weeks since plan
    step R8-b), so
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

    # --- weeks (plan step R8-b): a weekly cadence can fire more than once in
    # a month, so the ceiling is the user's here too -----------------------
    weeks = _selectable_unit(page, units, "weeks")
    if weeks is None:
        print("   SKIPPED (weeks arm): no live weeks option; the visibility pass fails it")
        return
    previews.clear()
    page.locator("#recurrence_unit").select_option(weeks)
    _settle(page)
    _check(f"{kind} C: the ceiling row is SHOWN for weeks",
           _visible(page, "field-max-per-month"), "hidden")
    _check(f"{kind} C: the typed ceiling carries over to weeks and posts once",
           _posted_ceiling(page) == ["2"], str(_posted_ceiling(page)))
    _check(f"{kind} C: the weekly preview carries the ceiling",
           bool(previews) and "max_per_month=2" in previews[-1],
           previews[-1] if previews else "no preview request was made")
    _check(f"{kind} C: the preview survived the weekly ceiling",
           "Could not load preview" not in page.inner_text("#recurrence-preview"),
           page.inner_text("#recurrence-preview"))


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


def _one_interval(page, kind: str, label: str) -> list[str]:
    """Check exactly one interval control submits, and return what it posts.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        label: The case letter.

    Returns:
        Every value the form would post under ``interval_n``.
    """
    posted = _posted_intervals(page)
    _check(f"{kind} {label}: exactly one interval_n posts",
           len(posted) <= 1, f"posted={posted}")
    return posted


def _placement_help(page, kind: str, label: str, fixed: bool) -> None:
    """Check the funding row is SHOWN and says which state the user is in.

    Plan ledger row **D32**, developer ruling 2026-08-16.  The row used to
    hide itself whenever the cadence admitted one placement, which is how a
    bill's funding rule came to change with nothing on screen saying so.
    Rendered HTML cannot tell a row a script left visible from one it hid,
    which is why this check is here rather than in the suite.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
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


def _choose_placement(page, index: int) -> None:
    """Choose the funding placement at *index* and let the script re-link.

    By INDEX, in the order the picker renders the placements: 0 is the
    paycheck containing the occurrence, 1 the first paycheck on or after it.

    Args:
        page: The Playwright page.
        index: The option's position in ``#recurrence_placement``.
    """
    page.evaluate(
        """(index) => { const s = document.getElementById('recurrence_placement');
                        s.selectedIndex = index;
                        s.dispatchEvent(new Event('change', {bubbles: true})); }""",
        index,
    )
    _settle(page)


def _check_week_preview(page, kind: str, previews: list[str],
                        reading: dict[str, str]) -> None:
    """Check the act just taken re-fetched the preview for *reading*, and it answered.

    The request is the evidence, not the fragment.  On a 14-day schedule every
    other week previews exactly the paychecks every paycheck does (measured
    2026-11-02; it is why the suite's weekly preview case uses three weeks),
    so the text left from the paychecks step reads the same whether or not a
    weekly fetch happened; and
    :func:`_settle` is a fixed wait, which cannot say one did.  The last
    request since the caller cleared *previews* must carry every key of
    *reading* exactly, parsed rather than matched as a substring, since
    ``recurrence_unit=5`` is a substring of ``recurrence_unit=50``.

    Args:
        page: The Playwright page.
        kind: "transaction" or "transfer", for the labels.
        previews: The preview URLs requested since the caller's last act.
        reading: The query keys the request must carry, each to one value.
    """
    query = parse_qs(urlsplit(previews[-1]).query) if previews else {}
    named = ", ".join(f"{key}={value}" for key, value in reading.items())
    _check(f"{kind} W: the preview re-fetched for {named}",
           all(query.get(key) == [value] for key, value in reading.items()),
           previews[-1] if previews else "no preview request was made")
    shown = page.inner_text("#recurrence-preview")
    _check(f"{kind} W: that preview lists occurrences",
           "occurrences:" in shown, shown)


def _drive_week_visibility(page, kind: str, units: dict[str, str]) -> None:
    """Check the WEEK unit's controls, on one form kind (plan step R8-b).

    The unit the closed pattern set could never express, offered on both
    forms since a weekly row is dated from its occurrence, so the option must
    be SELECTABLE (:func:`_selectable_unit`), not merely present.  A weekly
    occurrence is neither a payday nor a day of the month, so BOTH
    placements change which paycheck funds it, and both must enable and post.
    The cadence is left at every other week, funded by the paycheck
    containing each occurrence.

    **Each preview check reads the REQUEST the act fired**
    (:func:`_check_week_preview`), as :func:`_drive_month_ceiling` does: clear
    the captured URLs, act, then require a fetch naming the week unit and,
    for the second, the deferring placement.  Both checks read only that
    "occurrences:" was on screen until the adversarial review of R8-b, and the
    paychecks preview left there passed both whether or not a fetch happened.

    Args:
        page: The Playwright page, on the create form, a cadence chosen.
        kind: "transaction" or "transfer", for the labels.
        units: The offered units, as :func:`_unit_ids` read them.
    """
    weeks = _selectable_unit(page, units, "weeks")
    _check(f"{kind} W: the weeks option is offered and selectable", weeks is not None,
           f"offered units: {sorted(units)}")
    if weeks is None:
        return
    previews, stop_watching = _watch_previews(page)
    page.locator("#recurrence_unit").select_option(weeks)
    _settle(page)
    _check(f"{kind} W: interval box enabled",
           page.evaluate("() => !document.getElementById('interval_n').disabled"),
           "disabled")
    placements = page.evaluate(
        """() => Array.from(document.getElementById('recurrence_placement').options)
                 .map(o => o.value)"""
    )
    # Cleared BEFORE the act: the fetch fires on the change event itself (the
    # ordering ``_drive_month_ceiling`` measured).
    previews.clear()
    _set_interval(page, 2)
    _check(f"{kind} W: every other week posts its own interval",
           _one_interval(page, kind, "W") == ["2"], "the box lost the typed interval")
    _placement_help(page, kind, "W", fixed=False)
    offered_placements = page.evaluate(
        """() => Array.from(document.getElementById('recurrence_placement').options)
                 .filter(o => !o.hidden && !o.disabled).length"""
    )
    _check(f"{kind} W: both funding choices are enabled for weeks",
           offered_placements == 2, f"{offered_placements} enabled")
    _check_week_preview(page, kind, previews, {
        "recurrence_unit": weeks, "interval_n": "2",
        "recurrence_placement": placements[0],
    })
    previews.clear()
    _choose_placement(page, 1)
    _check(f"{kind} W: a weekly cadence funded from a later paycheck posts that choice",
           page.evaluate(
               """() => {
                   const s = document.getElementById('recurrence_placement');
                   const posted = new FormData(s.form).getAll('recurrence_placement');
                   return posted.length === 1 && posted[0] === s.options[1].value;
               }"""),
           "the deferring placement does not post on the WEEK unit")
    _check_week_preview(page, kind, previews, {
        "recurrence_unit": weeks, "interval_n": "2",
        "recurrence_placement": placements[1],
    })
    _choose_placement(page, 0)
    stop_watching()


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
    :func:`_drive_opening_bound`) and ``repeats_on`` (shown for a month or
    year unit -- :func:`_drive_nominal_day` for its days, and
    ``_recurrence_form_weekday.py`` for its weekday readings since plan step
    recurrence:R8-c).  What is left cadence-dependent, and what this
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

    # Does not repeat: the form's own empty option, not a cadence.
    unit.select_option("")
    _settle(page)
    _check(f"{kind} A: interval row hidden", not _visible(page, "field-interval"), "shown")
    _check(f"{kind} A: placement row hidden", not _visible(page, "field-placement"), "shown")
    _check(f"{kind} A: interval box disabled",
           page.evaluate("() => document.getElementById('interval_n').disabled"),
           "enabled beside no unit, so half a cadence can post")
    _one_interval(page, kind, "A")

    # Paychecks: the placement is INERT here, so the row explains itself.
    unit.select_option(units["paychecks"])
    _settle(page)
    _check(f"{kind} B: interval box enabled",
           page.evaluate("() => !document.getElementById('interval_n').disabled"),
           "disabled")
    _placement_help(page, kind, "B", fixed=True)
    _one_interval(page, kind, "B")

    _drive_week_visibility(page, kind, units)

    # Months at 1: anchors on the calendar.
    unit.select_option(units["months"])
    _settle(page)
    _set_interval(page, 1)
    _placement_help(page, kind, "C", fixed=False)
    _one_interval(page, kind, "C")

    # Months at 1, funded from the month's FIRST paycheck: anchors on a
    # paycheck, so it reads no day of the month.
    _choose_placement(page, 1)

    # Months at 3, still funded from the month's first paycheck.  **This is
    # plan ledger row D32's defect ceasing to exist**: the closed set had no
    # quarterly first-paycheck twin, so raising the interval used to reassign
    # the placement and HIDE the row.  The choice must survive.
    _set_interval(page, 3)
    _placement_help(page, kind, "E", fixed=False)
    _check(f"{kind} E: the first-paycheck funding SURVIVED the interval change",
           page.evaluate(
               "() => document.getElementById('recurrence_placement')"
               ".selectedIndex") == 1,
           "the funding choice was silently reassigned")
    _one_interval(page, kind, "E")

    # Months at 2 -- the cadence the closed pattern set could never name, and
    # the whole point of plan step R7c-c.  Typed rather than chosen, because
    # there is no <select> left to choose it from.
    _choose_placement(page, 0)
    _set_interval(page, 2)
    _check(f"{kind} F: every-other-month posts its own interval",
           _one_interval(page, kind, "F") == ["2"], "the free box lost the typed interval")

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
    _placement_help(page, kind, "G", fixed=False)
    _check(f"{kind} G: posts interval 1",
           _one_interval(page, kind, "G") == ["1"], "wrong interval")

    # A year-scale cadence funded from the month's FIRST paycheck: the reading
    # plan step R8-a admitted, driven end to end because a placement the server
    # OFFERS and the script leaves disabled is invisible to the suite -- and
    # because this pair was a REFUSAL case in ``_drive_refusals`` until this
    # step, which is as wrong as it sounds in the other direction.
    _choose_placement(page, 1)
    _check(f"{kind} H: a yearly cadence CAN be funded from a later paycheck",
           page.evaluate(
               "() => document.getElementById('recurrence_placement')"
               ".selectedIndex") == 1,
           "the deferring placement is still not selectable on the YEAR unit")
    _placement_help(page, kind, "H", fixed=False)

    preview = page.locator("#recurrence-preview").inner_text().strip()
    _check(f"{kind}: the live preview answered",
           "Could not load preview" not in preview, preview)
