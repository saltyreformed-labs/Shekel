"""The recurrence form harness: the opening bound, the nominal day, the per-month ceiling and the closing bound."""
from _recurrence_form_checks import (
    DEV_BASE_URL,
    _check,
    _posted_bound,
    _posted_ceiling,
    _posted_opening,
    _select_end_mode,
    _selectable_unit,
    _settle,
    _unit_ids,
    _visible,
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
    page.select_option("#nominal_day", "31")
    _settle(page)
    _check(f"{kind} N: precondition -- months on Apr 30 shows repeating-on, 31 posting",
           _visible(page, "field-nominal-day")
           and _posted_opening(page)["nominal_day"] == ["31"],
           str(_posted_opening(page)["nominal_day"]))
    page.locator("#recurrence_unit").select_option(weeks)
    _settle(page)
    _check(f"{kind} N: repeating-on is hidden for weeks on the same date",
           not _visible(page, "field-nominal-day"), "visible")
    _check(f"{kind} N: the day chosen for months posts NOTHING for weeks",
           _posted_opening(page)["nominal_day"] == [],
           str(_posted_opening(page)["nominal_day"]))


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
