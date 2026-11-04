"""The recurrence form harness: a repeat on each statement's due date (CC-6).

Only a transfer into a configured card may repeat by it, so the transfer form
offers the unit exactly while the chosen destination has due dates, and what
offering it means is the browser's: an option hidden by a script, a box that is
read-only and still posts, and a selection the script must NOT move all look
the same in rendered HTML (plan step credit_card:CC-6, its task plan#2788).

Needs a configured card on the database the app is pointed at, and for the
edit-form arm a stored repeat; each arm prints SKIPPED rather than passing on
nothing.  Create them through the app on a clone: the card's terms on its
account page, then this form.
"""
import time

from _recurrence_form_checks import (
    DEV_BASE_URL,
    MARK,
    POST_SPACING_SECONDS,
    _check,
    _posted_intervals,
    _select_unit_by_id,
    _selectable_unit,
    _settle,
    _sql,
    _unit_ids,
    _visible,
)

#: The label the unit's option carries (``recurrence._picker._UNIT_LABELS``).
DUE_DATES_LABEL = "statement due dates"


def _owner_ids() -> dict[str, str | None]:
    """Return a configured card, a non-card and a stored repeat of the session's owner.

    Read off the database THE APP IS POINTED AT, for the owner of the oldest
    active definition, as ``_edit_form_ids`` reads its own.

    Returns:
        ``{"card": id, "non_card": id, "repeat": id}``, each ``None`` when the
        copy holds no such row.
    """
    owner = _sql(
        "SELECT user_id FROM budget.transfer_templates WHERE is_active "
        "ORDER BY id LIMIT 1"
    )
    if not owner:
        return {"card": None, "non_card": None, "repeat": None}
    cards = (
        "SELECT a.id FROM budget.accounts a "
        "JOIN ref.account_types t ON t.id = a.account_type_id "
        f"WHERE a.user_id = {owner[0]} AND a.is_active AND t.has_revolving_credit "
        "AND EXISTS (SELECT 1 FROM budget.credit_card_params p "
        "WHERE p.account_id = a.id) ORDER BY a.id"
    )
    card = _sql(cards + " LIMIT 1")
    non_card = _sql(
        f"SELECT id FROM budget.accounts WHERE user_id = {owner[0]} AND is_active "
        f"AND id NOT IN ({cards}) ORDER BY id LIMIT 1"
    )
    repeat = _sql(
        "SELECT t.id FROM budget.transfer_templates t "
        "JOIN budget.recurrence_rules r ON r.transfer_template_id = t.id "
        "JOIN ref.recurrence_units u ON u.id = r.unit_id "
        f"WHERE t.user_id = {owner[0]} AND t.is_active AND u.name = 'due_date' "
        "ORDER BY t.id LIMIT 1"
    )
    return {
        "card": card[0] if card else None,
        "non_card": non_card[0] if non_card else None,
        "repeat": repeat[0] if repeat else None,
    }


def _choose_destination(page, account_id: str) -> None:
    """Choose the destination, firing the change the script listens for.

    Args:
        page: The Playwright page.
        account_id: The account to choose.
    """
    page.select_option("#to_account_id", account_id)
    _settle(page)


def _posted_units(page) -> list[str]:
    """Return every value the form would post under ``recurrence_unit``."""
    return page.evaluate(
        """() => new FormData(document.getElementById('recurrence_unit').form)
                 .getAll('recurrence_unit')"""
    )


def _interval_read_only(page) -> bool:
    """Return whether the interval box refuses typing."""
    return page.evaluate("() => document.getElementById('interval_n').readOnly")


def _drive_the_offer(page, ids: dict[str, str | None], units: dict[str, str]) -> None:
    """Grade the offer against the destination, the fixed interval and the rows.

    Args:
        page: On the transfer create form.
        ids: :func:`_owner_ids`'s answer, with a card and a non-card.
        units: The form's offered units.
    """
    due_dates = units[DUE_DATES_LABEL]
    _choose_destination(page, ids["non_card"])
    _check("beside a non-card the unit is not on offer",
           _selectable_unit(page, units, DUE_DATES_LABEL) is None)
    _check("beside a non-card with nothing chosen no sentence shows",
           not _visible(page, "unit-needs-a-card"))

    _choose_destination(page, ids["card"])
    _check("beside a configured card the unit is on offer",
           _selectable_unit(page, units, DUE_DATES_LABEL) == due_dates)

    # A typed interval comes back when the unit that fixed it is left.
    _select_unit_by_id(page, units["months"])
    page.fill("#interval_n", "3")
    _settle(page)
    _select_unit_by_id(page, due_dates)
    _check("the interval box is fixed at 1 and posts it",
           _interval_read_only(page) and _posted_intervals(page) == ["1"],
           f"read-only={_interval_read_only(page)} posted={_posted_intervals(page)}")
    placements = page.evaluate(
        """() => Array.from(document.getElementById('recurrence_placement').options)
                 .filter(o => !o.hidden && !o.disabled).map(o => o.textContent.trim())"""
    )
    _check("one funding choice: the paycheck covering the date",
           placements == ["The paycheck that covers the date"], str(placements))
    _check("the funding row stays on screen, explaining itself",
           _visible(page, "field-placement"))
    _check("no per-month ceiling beside it", not _visible(page, "field-max-per-month"))
    _check("no 'repeating on' day beside it", not _visible(page, "field-nominal-day"))
    help_text = page.inner_text("#starts-on-help")
    _check("Starts on says what the date means for this unit",
           "next statement due date" in help_text, help_text)
    _check("no sentence beside a card", not _visible(page, "unit-needs-a-card"))
    preview = page.inner_text("#recurrence-preview")
    _check("the preview lists the paychecks covering the dates",
           "occurrences" in preview, preview)

    # Moved off a card, the choice stays and says why it cannot be saved.
    _choose_destination(page, ids["non_card"])
    _check("the chosen unit is NOT moved when the destination stops being a card",
           page.input_value("#recurrence_unit") == due_dates
           and _posted_units(page) == [due_dates],
           f"posted={_posted_units(page)}")
    _check("the sentence says why", _visible(page, "unit-needs-a-card"))

    _select_unit_by_id(page, units["months"])
    _check("leaving the unit retires its option",
           _selectable_unit(page, units, DUE_DATES_LABEL) is None)
    _check("leaving the unit retires the sentence",
           not _visible(page, "unit-needs-a-card"))
    _check("leaving the unit hands the box and its typed interval back",
           not _interval_read_only(page) and _posted_intervals(page) == ["3"],
           f"read-only={_interval_read_only(page)} posted={_posted_intervals(page)}")


def _drive_the_edit_form(page, repeat_id: str, due_dates: str) -> None:
    """Grade that an edit form opens on its stored repeat.

    Args:
        page: The Playwright page.
        repeat_id: A stored repeat on due dates.
        due_dates: The unit's ref id.
    """
    page.goto(f"{DEV_BASE_URL}/transfers/{repeat_id}/edit",
              wait_until="domcontentloaded")
    _settle(page)
    _check("an edit form preselects a stored repeat",
           _posted_units(page) == [due_dates], str(_posted_units(page)))
    _check("its interval box is fixed and posts 1",
           _interval_read_only(page) and _posted_intervals(page) == ["1"],
           f"posted={_posted_intervals(page)}")


def _drive_the_refusal(context, page, ids: dict[str, str | None], due_dates: str) -> None:
    """Grade the door behind the affordance: a crafted POST into a non-card.

    Args:
        context: The Playwright browser context, whose request client posts.
        page: On the transfer create form, read for the controls it posts.
        ids: :func:`_owner_ids`'s answer.
        due_dates: The unit's ref id.
    """
    form = page.evaluate(
        """() => Object.fromEntries(
               new FormData(document.getElementById('recurrence_unit').form))"""
    )
    response = context.request.post(
        f"{DEV_BASE_URL}/transfers",
        form={
            **form,
            "name": f"{MARK}-DUE-DATES",
            "amount": "1.00",
            "to_account_id": ids["non_card"],
            "recurrence_unit": due_dates,
            "interval_n": "1",
        },
        headers={"Referer": f"{DEV_BASE_URL}/transfers/new"},
    )
    _check("the door refuses it in plain words",
           "so it has no statement due dates" in response.text(),
           f"status={response.status}")
    made = _sql("SELECT count(*) FROM budget.transfer_templates "
                f"WHERE name LIKE '{MARK}-DUE-DATES%'")[0]
    _check("the refusal wrote nothing", made == "0", f"{made} rows")
    time.sleep(POST_SPACING_SECONDS)


def _drive_due_date_offer(context, page) -> None:
    """Grade the due-date repeat on both forms.

    Args:
        context: The Playwright browser context.
        page: The Playwright page.
    """
    print("\n=== a repeat on each statement's due date ===")
    page.goto(f"{DEV_BASE_URL}/templates/new", wait_until="domcontentloaded")
    _settle(page)
    _check("the transaction form never offers the unit",
           DUE_DATES_LABEL not in _unit_ids(page), str(_unit_ids(page)))

    page.goto(f"{DEV_BASE_URL}/transfers/new", wait_until="domcontentloaded")
    _settle(page)
    units = _unit_ids(page)
    _check("the transfer form's control carries the unit", DUE_DATES_LABEL in units,
           str(units))
    ids = _owner_ids()
    if ids["card"] is None or ids["non_card"] is None:
        print("   SKIPPED  the offer: this database holds no configured card")
    else:
        _drive_the_offer(page, ids, units)
        _drive_the_refusal(context, page, ids, units[DUE_DATES_LABEL])
    if ids["repeat"] is None:
        print("   SKIPPED  the edit form: this database holds no stored repeat")
    else:
        _drive_the_edit_form(page, ids["repeat"], units[DUE_DATES_LABEL])
