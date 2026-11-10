"""Drive a refused raise save in a real browser over the real redraw (task plan#3136).

Ruling plan#3134: a raise the app refuses redraws the Raises card with the
reason in a box and the form open, in the mode it was posted in, holding
what was typed.  The route suite (``tests/test_routes/test_salary/
test_raise_form.py``) grades the 422, the designed-fragment header, the
reason and the ``data-raise-*`` attributes the box carries; it cannot see
what the browser then DOES with them, and three things here exist only in a
browser:

1. **htmx swaps the 422 at all.**  ``base.html``'s ``responseHandling``
   drops a 4xx body unless ``app.js``'s designed-fragment listener opts it
   back in; a redirect instead was followed and the whole edit page swapped
   INSIDE the card (measured in Chromium, htmx 2.0.10, before this task:
   two navigation bars, the flash toast hidden).
2. **The form stays OPEN.**  ``base.html`` settles the ``class`` attribute,
   so a collapse opened by a script at ``htmx:afterSwap`` is put back to
   the drawn class about 20 ms later.  ``app.js`` fills the form at
   ``htmx:afterSettle`` for that reason, and only a browser runs the
   settle.
3. **One fill serves both sources**, the edit button and the reason box,
   and the Add Raise button's ``form.reset()`` still CLEARS the form after a
   refusal, because what was typed is never a rendered ``value``.  A
   refused edit, corrected, saves on its own edit URL: the fill re-points
   ``hx-post`` inside the settle, and only a press shows htmx bound it.

**The browser talks to the app in-process, not to the dev container.**
Every request Chromium makes -- the edit page, ``app.js``, the vendored
htmx and Bootstrap, each htmx POST -- is fulfilled through Playwright's
request interception by the Flask test client of the suite's own fixtures,
on the private cluster ``./scripts/test.sh`` gives the run.  So the page,
the scripts and the redraw are the branch's own, nothing is read from or
written to a shared database, and no saved session is needed.  The headers
forwarded are htmx's (``HX-*``) and the body's type: the browser's
``User-Agent`` is withheld, because strong session protection binds the
session to the client that logged in.

Usage (a module pytest collects only when named, as ``verify_*`` matches
no ``python_files`` pattern)::

    ./scripts/test.sh tests/manual/verify_raise_refusal_redraw.py -p no:randomly

Preconditions: Playwright's Chromium for the pinned version
(``playwright install chromium``).
"""

from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.extensions import db
from app.models.salary_raise import SalaryRaise
from tests._test_helpers import freeze_today, make_recurring_raise, make_salary_profile

#: The origin the page is served from; every request to it is intercepted.
ORIGIN = "http://shekel.test"

#: The leap-day rule's sentence (ruling plan#3090), the add case's refusal.
LEAP = "A raise that repeats every year can't start on February 29"

#: The one-method rule's sentence (ruling plan#3135), the edit case's refusal.
ONE_METHOD = "Enter the raise as either a percentage or a flat amount a year, but not both."

#: The unanswered end year's sentence.
UNANSWERED = "Say how long this recurring raise is believed"

#: Longer than htmx's settle delay (20 ms) by a wide margin, so a class the
#: settle would put back has been put back before the form is read.
AFTER_SETTLE_MS = 500

#: What the browser shows of the raise form, read in one evaluation.
_FORM_STATE = """() => {
    const form = document.getElementById('raise-form');
    const collapse = document.getElementById('add-raise-form');
    const box = document.getElementById('raise-refusal');
    const value = (name) => form.querySelector('[name=' + name + ']').value;
    const toasts = Array.from(document.querySelectorAll('.toast'));
    return {
        navbars: document.querySelectorAll('nav.shekel-navbar').length,
        sections: document.querySelectorAll('#raises-section').length,
        open: collapse.classList.contains('show') && collapse.offsetHeight > 0,
        box: box ? box.textContent.trim() : null,
        boxVisible: box ? box.offsetHeight > 0 : false,
        boxClass: box ? box.className : null,
        action: form.getAttribute('action'),
        hxPost: form.getAttribute('hx-post'),
        icon: document.getElementById('raise-submit-btn').innerHTML.trim(),
        type: value('raise_type_id'),
        date: value('effective_date'),
        percentage: value('percentage'),
        flat: value('flat_amount'),
        recurring: document.getElementById('raise-recurring').checked,
        endGroupShown: !document.getElementById('raise-end-year-group').hidden,
        modeNone: document.getElementById('raise-end-mode-none').checked,
        modeYear: document.getElementById('raise-end-mode-year').checked,
        year: value('terminal_year'),
        version: value('version_id'),
        toastsShown: toasts.filter((t) => t.classList.contains('show')).length,
        rows: document.querySelectorAll('#raises-section tbody tr').length,
    };
}"""


@pytest.fixture(name="profile")
def profile_fixture(monkeypatch, seed_user, seed_periods):  # pylint: disable=unused-argument
    """A salary profile on the seeded calendar, today frozen inside it, with one raise.

    The raise is a recurring 3% from 2027-01-14 with no end year, the row
    the edit case opens.  Today is frozen as the salary route package's own
    conftest freezes it (``tests/test_routes/test_salary/conftest.py``),
    which this module sits outside of.

    Pylint: ``unused-argument`` -- ``seed_periods`` is the calendar the
    profile's paychecks are generated over, needed for its rows, never read.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
    profile = make_salary_profile(seed_user, db.session)
    make_recurring_raise(
        profile.id, db.session, effective_date=date(2027, 1, 14), percentage=Decimal("0.03"),
    )
    db.session.commit()
    return profile


def _serve_through(client):
    """A Playwright route handler answering every request with *client*'s response.

    A redirect is followed HERE, and the browser is answered with where it
    lands: Playwright does not route the request a redirect makes, so the
    browser would send it to the network instead, and an htmx request never
    sees a redirect anyway (its XHR follows one and hands htmx the page it
    lands on).  So a door that answered an htmx save with a redirect shows
    here as it shows in production: the whole page swapped into the card.
    """
    def handle(route):
        request = route.request
        parts = urlsplit(request.url)
        headers = {
            name: value for name, value in request.headers.items()
            if name.lower().startswith("hx-")
        }
        if "content-type" in request.headers:
            headers["Content-Type"] = request.headers["content-type"]
        response = client.open(
            parts.path + (f"?{parts.query}" if parts.query else ""),
            method=request.method,
            headers=headers,
            data=request.post_data_buffer,
            follow_redirects=True,
        )
        route.fulfill(
            status=response.status_code,
            headers=dict(response.headers),
            body=response.get_data(),
        )
    return handle


def _state(page):
    """The form's state once any settle has run."""
    page.wait_for_timeout(AFTER_SETTLE_MS)
    return page.evaluate(_FORM_STATE)


def _save_and_wait(page):
    """Press Save and wait for the htmx swap to settle."""
    with page.expect_response(lambda response: response.request.method == "POST"):
        page.click("#raise-submit-btn")
    return _state(page)


def _refuse_an_add(page, add_url):
    """A yearly raise from February 29, refused: the card redrawn open, as an add, as typed."""
    page.click('[data-toggle-target="add-raise-form"]')
    page.fill("#raise-effective-date", "2028-02-29")
    page.fill('[name="percentage"]', "3")
    page.check("#raise-recurring")
    page.check("#raise-end-mode-none")
    refused = _save_and_wait(page)
    assert (refused["navbars"], refused["sections"]) == (1, 1), refused
    assert refused["box"] is not None and LEAP in refused["box"], refused
    assert refused["boxVisible"] and "alert-danger" in refused["boxClass"], refused
    assert refused["open"], f"the refused form is closed: {refused}"
    assert (refused["action"], refused["hxPost"]) == (add_url, add_url), refused
    assert "bi-plus" in refused["icon"], refused
    assert (
        refused["date"], refused["percentage"], refused["flat"], refused["recurring"],
        refused["endGroupShown"], refused["modeNone"], refused["modeYear"],
        refused["year"], refused["version"],
    ) == ("2028-02-29", "3", "", True, True, True, False, "", ""), refused
    assert refused["toastsShown"] == 0, refused


def _save_the_correction(page, profile, stored):
    """The refused add, moved to February 28 in the form the box filled, saves as an add."""
    page.fill("#raise-effective-date", "2028-02-28")
    saved = _save_and_wait(page)
    assert saved["box"] is None and saved["rows"] == 2, saved
    db.session.expire_all()
    added = (
        db.session.query(SalaryRaise)
        .filter(SalaryRaise.salary_profile_id == profile.id, SalaryRaise.id != stored.id)
        .one()
    )
    assert (added.effective_date, added.percentage, added.is_recurring) == (
        date(2028, 2, 28), Decimal("0.03"), True,
    )


def _refuse_an_edit(page, stored, edit_url):
    """A flat amount beside the stored percentage, refused: redrawn open, as the edit, as typed."""
    page.click(f'[data-raise-edit="{stored.id}"]')
    page.fill('[name="flat_amount"]', "1000")
    refused = _save_and_wait(page)
    assert refused["box"] is not None and ONE_METHOD in refused["box"], refused
    assert refused["open"], f"the refused edit is closed: {refused}"
    assert (refused["action"], refused["hxPost"]) == (edit_url, edit_url), refused
    assert "bi-check-lg" in refused["icon"], refused
    assert (
        refused["percentage"], refused["flat"], refused["recurring"],
        refused["modeNone"], refused["version"],
    ) == ("3.00", "1000", True, True, str(stored.version_id)), refused


def _add_raise_clears(page, add_url):
    """Add Raise closes the refused form and clears it; reopened, it is an empty add."""
    page.click('[data-toggle-target="add-raise-form"]')
    closed = _state(page)
    assert closed["box"] is None and not closed["open"], closed
    page.click('[data-toggle-target="add-raise-form"]')
    cleared = _state(page)
    assert cleared["open"] and cleared["box"] is None, cleared
    assert (cleared["action"], cleared["hxPost"]) == (add_url, add_url), cleared
    assert (
        cleared["date"], cleared["percentage"], cleared["flat"], cleared["recurring"],
        cleared["endGroupShown"], cleared["modeNone"], cleared["modeYear"],
        cleared["year"], cleared["version"],
    ) == ("", "", "", False, False, False, False, "", ""), cleared


def _refuse_an_unanswered_end_year(page):
    """A recurring raise saying nothing of its end, refused: redrawn with neither answer picked."""
    page.fill("#raise-effective-date", "2027-07-01")
    page.fill('[name="percentage"]', "2")
    page.check("#raise-recurring")
    refused = _save_and_wait(page)
    assert refused["box"] is not None and UNANSWERED in refused["box"], refused
    assert refused["open"] and refused["recurring"] and refused["endGroupShown"], refused
    assert (refused["modeNone"], refused["modeYear"]) == (False, False), refused


def _edit_takes_the_box_away(page, stored, edit_url):
    """An edit button pressed under a reason box fills the form and removes the box."""
    page.click(f'[data-raise-edit="{stored.id}"]')
    edited = _state(page)
    assert edited["box"] is None and edited["open"], edited
    assert (edited["action"], edited["percentage"], edited["flat"]) == (
        edit_url, "3.00", "",
    ), edited


def _save_a_corrected_edit(page, stored, edit_url):
    """A refused edit, corrected in the form the box filled, saves ON its edit URL.

    The fill re-points the form's ``hx-post`` and calls ``htmx.process``
    inside ``htmx:afterSettle``; this is the press that shows htmx bound the
    edit URL rather than the add form's.
    """
    page.fill('[name="flat_amount"]', "1000")
    refused = _save_and_wait(page)
    assert refused["box"] is not None and ONE_METHOD in refused["box"], refused
    page.fill('[name="flat_amount"]', "")
    page.fill('[name="percentage"]', "4")
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith(edit_url),
    ):
        page.click("#raise-submit-btn")
    saved = _state(page)
    assert saved["box"] is None and saved["rows"] == 2, saved
    db.session.expire_all()
    updated = db.session.get(SalaryRaise, stored.id)
    assert (updated.percentage, updated.flat_amount) == (Decimal("0.04"), None)


def test_a_refused_raise_redraws_the_card_in_a_browser(auth_client, profile):
    """Add refused, then saved; edit refused; Add Raise clears; edit clears the box; edit saved."""
    [stored] = profile.raises
    add_url = f"/salary/{profile.id}/raises"
    edit_url = f"/salary/raises/{stored.id}/edit"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(f"{ORIGIN}/**", _serve_through(auth_client))
            page.goto(f"{ORIGIN}/salary/{profile.id}/edit")
            _refuse_an_add(page, add_url)
            _save_the_correction(page, profile, stored)
            _refuse_an_edit(page, stored, edit_url)
            _add_raise_clears(page, add_url)
            _refuse_an_unanswered_end_year(page)
            _edit_takes_the_box_away(page, stored, edit_url)
            _save_a_corrected_edit(page, stored, edit_url)
            assert not errors, errors
        finally:
            browser.close()
