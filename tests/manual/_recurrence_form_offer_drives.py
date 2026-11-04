"""The recurrence form harness: which controls each cadence shows, and the refusals a crafted POST meets."""
import time
from urllib.parse import parse_qs, urlsplit

from _recurrence_form_checks import (
    DEV_BASE_URL,
    MARK,
    POST_SPACING_SECONDS,
    _check,
    _posted_intervals,
    _selectable_unit,
    _set_interval,
    _settle,
    _sql,
    _unit_ids,
    _visible,
)

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
    previews: list[str] = []

    def capture(request) -> None:
        if "preview-recurrence" in request.url:
            previews.append(request.url)

    page.on("request", capture)
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
    page.remove_listener("request", capture)


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
