"""The recurrence form harness: what a loan destination locks, on the create form and the edit form."""
from _recurrence_form_checks import (
    DEV_BASE_URL,
    _check,
    _posted_bound,
    _posted_opening,
    _select_end_mode,
    _settle,
    _sql,
    _unit_ids,
    _visible,
)

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
