"""Salary route tests: the raise form as a browser posts it, and its refusals in words.

Task plan#3125 (plan#3048), found by plan#3061's review: the raise doors
flashed "Please correct the highlighted errors and try again." for every
refusal, on a form that highlights nothing, and no test pinned the form's
controls, so a renamed control would have passed a suite whose posts were
typed by hand.

* :class:`TestTheRenderedFormPosts` -- the add form's controls and boxes, as
  rendered, filled and posted, make the raise; the edit button's data
  attributes, filled as ``app.js`` fills them, update it; the date and
  end-year controls take the schema's window on both renders of the card.
* :class:`TestARefusalIsSaidInWords` -- each refusal, posted as the form
  posts it, flashes the schema's own sentence through the shared loader, and
  saves nothing.
* :class:`TestARefusalRedrawsTheCard` -- task plan#3136 (ruling plan#3134):
  on an htmx request the refusal is the Raises card at 422, marked as a
  designed fragment, the reason in a box carrying what was typed and the
  mode it was posted in, and no flash left for a later page; the box,
  filled as ``app.js`` fills it, posts a raise that saves once corrected.
  That the browser fills and opens the form is graded in a real browser
  (``tests/manual/verify_raise_refusal_redraw.py``).

**No date here is read from a clock**: the package's conftest freezes today
at 2026-03-20.
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from markupsafe import escape

from app.extensions import db
from app.models.ref import RaiseType
from app.models.salary_raise import SalaryRaise
from app.services.salary_raises import RAISE_YEAR_MAX, RAISE_YEAR_MIN
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from tests._form_controls import rendered_form_choices, rendered_form_controls

from ._helpers import _create_profile

#: The one-method rule's sentence (ruling plan#3135), said for both and neither.
ONE_METHOD = "Enter the raise as either a percentage or a flat amount a year, but not both."

#: The generic prompt the raise doors flashed for every refusal until task plan#3125.
GENERIC = b"Please correct the highlighted errors"


def _page(client, profile):
    """The profile's edit page, rendered."""
    return client.get(f"/salary/{profile.id}/edit").data.decode()


def _add_url(profile):
    """The add form's action, as the page renders it."""
    return f"/salary/{profile.id}/raises"


def _the_add_form(client, profile):
    """The add form's posted controls and its boxes, read off the edit page."""
    return _the_add_form_on(_page(client, profile), profile)


def _the_add_form_on(page, profile):
    """The add form's posted controls and its boxes, read off *page*."""
    return (
        rendered_form_controls(page, _add_url(profile)),
        rendered_form_choices(page, _add_url(profile)),
    )


def _the_next_page(client, profile, sentence):
    """The edit page loaded next, which must not flash *sentence* a second time."""
    page = _page(client, profile)
    assert str(escape(sentence)) not in page, "the refusal was flashed for a later page too"
    return page


def _fill(controls, choices, values):
    """Set each control the form renders, and tick each box it renders.

    A name the form does not render fails here rather than being posted as a
    control it lacks, which is what pins the markup.
    """
    for name, value in values.items():
        if name in choices:
            assert value in choices[name], f"the raise form offers no {name}={value!r}"
        else:
            assert name in controls, f"the raise form renders no {name!r}"
        controls[name] = [value]
    return controls


def _tag(page, pattern):
    """The one rendered tag matching *pattern*, whitespace collapsed."""
    found = re.findall(pattern, page, re.S)
    assert len(found) == 1, f"{len(found)} tags match {pattern}"
    return " ".join(found[0].split())


def _edit_button(page, raise_id):
    """The edit button's ``data-raise-*`` attributes for *raise_id*, by name."""
    tag = _tag(page, rf'<button[^>]*data-raise-edit="{raise_id}"[^>]*>')
    return dict(re.findall(r'(data-raise-[a-z-]+)="([^"]*)"', tag))


def _refusal_box(section):
    """The reason box of a redrawn card: its ``data-raise-*`` attributes, class and words."""
    [(tag, words)] = re.findall(r'(<div[^>]*id="raise-refusal"[^>]*>)(.*?)</div>', section, re.S)
    attributes = dict(re.findall(r'(data-raise-[a-z-]+)="([^"]*)"', tag))
    [classes] = re.findall(r'class="([^"]*)"', tag)
    return attributes, classes, words


def _filled_as_app_js_fills(source, controls, choices):
    """The form's controls once ``_fillRaiseForm`` has filled them from *source*.

    *source* is an edit button's or a reason box's ``data-raise-*``
    attributes.  The Recur box is ticked for ``"true"``; the end-year answer
    is the radio its ``data-raise-end-mode`` names, none for empty; and a
    one-time raise posts neither an answer nor a year, because
    ``_syncRaiseEndYearVisibility`` clears both when Recur is unticked.
    """
    recurring = source["data-raise-recurring"] == "true"
    values = {
        "raise_type_id": source["data-raise-type-id"],
        "effective_date": source["data-raise-date"],
        "percentage": source["data-raise-percentage"],
        "flat_amount": source["data-raise-flat"],
        "terminal_year": source["data-raise-terminal-year"] if recurring else "",
        "version_id": source["data-raise-version-id"],
    }
    if recurring:
        values["is_recurring"] = "on"
        if source["data-raise-end-mode"]:
            values["raise_end_mode"] = source["data-raise-end-mode"]
    return _fill(controls, choices, values)


def _raises_of(profile):
    """The profile's raises, freshly read."""
    db.session.expire_all()
    return db.session.query(SalaryRaise).filter_by(salary_profile_id=profile.id).all()


@pytest.fixture(name="profile")
def profile_fixture(seed_user, seed_periods):  # pylint: disable=unused-argument
    """A salary profile on the seeded calendar, with no raise.

    Pylint: ``unused-argument`` -- ``seed_periods`` is the calendar the
    profile's paychecks are generated over, needed for its rows, never read.
    """
    return _create_profile(seed_user)


class TestTheRenderedFormPosts:
    """What the raise form renders is what it posts."""

    def test_the_add_form_as_rendered_makes_the_raise(self, auth_client, profile):
        """Every control the form renders, filled for a 3% yearly raise from 2027-01-14."""
        controls, choices = _the_add_form(auth_client, profile)
        assert set(controls) == {
            "csrf_token", "version_id", "raise_type_id", "effective_date",
            "percentage", "flat_amount", "terminal_year",
        }
        assert choices == {"is_recurring": ["on"], "raise_end_mode": ["none", "year"]}
        _fill(controls, choices, {
            "effective_date": "2027-01-14", "percentage": "3",
            "is_recurring": "on", "raise_end_mode": "none",
        })
        response = auth_client.post(_add_url(profile), data=controls, follow_redirects=True)
        assert b"Raise added." in response.data
        [saved] = _raises_of(profile)
        assert (
            saved.raise_type_id, saved.effective_date, saved.percentage,
            saved.flat_amount, saved.is_recurring, saved.terminal_year,
        ) == (
            int(controls["raise_type_id"][0]), date(2027, 1, 14), Decimal("0.03"),
            None, True, None,
        )

    def test_the_edit_button_fills_the_form_that_saves_it(self, auth_client, profile):
        """The button's attributes, filled as ``app.js`` fills them, with a new start day.

        ``_fillRaiseForm`` sets each control from the button and posts to its
        edit URL; a recurring raise ending 2030 answers "ends after", which
        the button states (``data-raise-end-mode``, task plan#3136).
        """
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        stored = SalaryRaise(
            salary_profile_id=profile.id, raise_type_id=merit.id,
            effective_date=date(2027, 1, 14), percentage=Decimal("0.03"),
            is_recurring=True, terminal_year=2030,
        )
        db.session.add(stored)
        db.session.commit()
        page = _page(auth_client, profile)
        button = _edit_button(page, stored.id)
        controls, choices = _the_add_form(auth_client, profile)
        _filled_as_app_js_fills(button, controls, choices)
        assert (controls["is_recurring"], controls["raise_end_mode"]) == (["on"], ["year"])
        assert controls["effective_date"] == ["2027-01-14"]
        controls["effective_date"] = ["2027-02-11"]
        response = auth_client.post(
            button["data-raise-edit-url"], data=controls, follow_redirects=True,
        )
        assert b"Raise updated." in response.data
        [saved] = _raises_of(profile)
        assert (saved.effective_date, saved.percentage, saved.terminal_year) == (
            date(2027, 2, 11), Decimal("0.03"), 2030,
        )

    @pytest.mark.parametrize("is_recurring, terminal_year, end_mode", (
        pytest.param(True, 2030, "year", id="a-recurring-raise-ending-2030-ends-after"),
        pytest.param(True, None, "none", id="a-recurring-raise-with-no-end-year"),
        pytest.param(False, None, "", id="a-one-time-raise-is-asked-nothing"),
    ))
    def test_the_edit_button_states_the_stored_end_year_answer(
        self, auth_client, profile, is_recurring, terminal_year, end_mode,
    ):
        """``data-raise-end-mode`` is the radio the stored raise answers, or none."""
        stored = _stored_raise(profile, is_recurring=is_recurring, terminal_year=terminal_year)
        button = _edit_button(_page(auth_client, profile), stored.id)
        assert button["data-raise-end-mode"] == end_mode

    def test_the_date_and_end_year_controls_take_the_schemas_window(self, auth_client, profile):
        """The years are ``RAISE_YEAR_MIN`` / ``_MAX``, on the page and in the HTMX section."""
        expected_date = (
            '<input type="date" name="effective_date" id="raise-effective-date" '
            'class="form-control form-control-sm" '
            f'min="{RAISE_YEAR_MIN}-01-01" max="{RAISE_YEAR_MAX}-12-31" required>'
        )
        expected_year = (
            '<input type="number" name="terminal_year" id="raise-terminal-year" '
            'class="form-control form-control-sm input-rem-7" '
            f'min="{RAISE_YEAR_MIN}" max="{RAISE_YEAR_MAX}" placeholder="year" '
            'aria-label="Last year this raise is believed">'
        )
        controls, choices = _the_add_form(auth_client, profile)
        _fill(controls, choices, {"effective_date": "2027-01-14", "percentage": "3"})
        section = auth_client.post(
            _add_url(profile), data=controls, headers={"HX-Request": "true"},
        ).data.decode()
        for page in (_page(auth_client, profile), section):
            assert _tag(page, r'<input type="date" name="effective_date".*?>') == expected_date
            assert _tag(page, r'<input type="number" name="terminal_year".*?>') == expected_year


#: Each refusal the raise schema says, and the form's answers that reach it.
_REFUSALS = (
    pytest.param(
        "A raise that repeats every year can't start on February 29, because "
        "most years don't have one. Start it on February 28 or March 1.",
        {"effective_date": "2028-02-29", "percentage": "3",
         "is_recurring": "on", "raise_end_mode": "none"},
        id="a-yearly-raise-from-february-29",
    ),
    pytest.param(
        "A one-time raise happens once, so it cannot carry an end year.",
        {"effective_date": "2027-01-14", "percentage": "3", "terminal_year": "2030"},
        id="an-end-year-on-a-one-time-raise",
    ),
    pytest.param(
        "Say how long this recurring raise is believed: pick an end year, or "
        "say it has none.",
        {"effective_date": "2027-01-14", "percentage": "3", "is_recurring": "on"},
        id="an-unanswered-end-year",
    ),
    pytest.param(
        "Enter the last year this raise is believed to happen.",
        {"effective_date": "2027-01-14", "percentage": "3",
         "is_recurring": "on", "raise_end_mode": "year"},
        id="ends-after-with-no-year",
    ),
    pytest.param(
        "A raise cannot end before it starts: it takes effect in 2027.",
        {"effective_date": "2027-01-14", "percentage": "3", "is_recurring": "on",
         "raise_end_mode": "year", "terminal_year": "2026"},
        id="an-end-before-the-start",
    ),
    pytest.param(
        ONE_METHOD,
        {"effective_date": "2027-01-14", "percentage": "3", "flat_amount": "1000"},
        id="both-a-percentage-and-a-flat-amount",
    ),
    pytest.param(
        ONE_METHOD, {"effective_date": "2027-01-14"},
        id="neither-a-percentage-nor-a-flat-amount",
    ),
)


class TestARefusalIsSaidInWords:
    """A refused raise flashes the schema's sentence, and nothing is saved."""

    @pytest.mark.parametrize("sentence, values", _REFUSALS)
    def test_the_add_door_says_why(self, auth_client, profile, sentence, values):
        """Posted as the add form posts it: the sentence, not the generic prompt."""
        controls, choices = _the_add_form(auth_client, profile)
        response = auth_client.post(
            _add_url(profile), data=_fill(controls, choices, values), follow_redirects=True,
        )
        assert str(escape(sentence)).encode() in response.data
        assert GENERIC not in response.data
        assert not _raises_of(profile)

    def test_the_update_door_says_why(self, auth_client, profile):
        """A flat amount beside the stored percentage: the one-method sentence, the raise kept."""
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        stored = SalaryRaise(
            salary_profile_id=profile.id, raise_type_id=merit.id,
            effective_date=date(2027, 1, 14), percentage=Decimal("0.03"),
        )
        db.session.add(stored)
        db.session.commit()
        button = _edit_button(_page(auth_client, profile), stored.id)
        controls, choices = _the_add_form(auth_client, profile)
        _fill(controls, choices, {
            "raise_type_id": button["data-raise-type-id"],
            "effective_date": button["data-raise-date"],
            "percentage": button["data-raise-percentage"],
            "flat_amount": "1000",
            "version_id": button["data-raise-version-id"],
        })
        response = auth_client.post(
            button["data-raise-edit-url"], data=controls, follow_redirects=True,
        )
        assert str(escape(ONE_METHOD)).encode() in response.data
        assert GENERIC not in response.data
        [kept] = _raises_of(profile)
        assert (kept.percentage, kept.flat_amount) == (Decimal("0.03"), None)


#: What the update door says of a form another action made stale.
STALE = (
    "This raise was changed by another action while you were editing.  "
    "Please reload and try again."
)

#: The header htmx sends with every request it makes.
HTMX = {"HX-Request": "true"}

#: Each control the raise form posts, by the reason box's attribute that holds it.
_BOX_NAMES = {
    "raise_type_id": "data-raise-type-id",
    "effective_date": "data-raise-date",
    "percentage": "data-raise-percentage",
    "flat_amount": "data-raise-flat",
    "raise_end_mode": "data-raise-end-mode",
    "terminal_year": "data-raise-terminal-year",
    "version_id": "data-raise-version-id",
}


def _stored_raise(profile, **columns):
    """A merit raise of 3% from 2027-01-14 on *profile*, *columns* over it, committed."""
    merit = db.session.query(RaiseType).filter_by(name="merit").one()
    stored = SalaryRaise(
        salary_profile_id=profile.id, raise_type_id=merit.id,
        effective_date=date(2027, 1, 14), percentage=Decimal("0.03"), **columns,
    )
    db.session.add(stored)
    db.session.commit()
    return stored


def _the_card(response):
    """The body of a designed 422 that is the Raises card alone, never a page."""
    assert response.status_code == 422
    assert response.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
    body = response.data.decode()
    assert body.count('id="raises-section"') == 1
    assert "<html" not in body and "<nav" not in body
    return body


def _the_card_after_a_save(response):
    """The body of the Raises card an htmx save answers with."""
    assert response.status_code == 200
    body = response.data.decode()
    assert body.count('id="raises-section"') == 1
    return body


def _typed(controls):
    """What the reason box should carry for *controls* as posted."""
    box = {attribute: controls.get(name, [""])[0] for name, attribute in _BOX_NAMES.items()}
    box["data-raise-recurring"] = "true" if "is_recurring" in controls else "false"
    return box


class TestARefusalRedrawsTheCard:
    """An htmx refusal is the Raises card redrawn, holding what was typed."""

    @pytest.mark.parametrize("sentence, values", _REFUSALS)
    def test_the_add_door_redraws_the_card(self, auth_client, profile, sentence, values):
        """The card at 422, the sentence in a red box carrying the typed add, no flash kept.

        Filled from the box as ``app.js`` fills it, the form posts again what
        was posted -- but a one-time raise's year, which a browser cannot
        post (the end-year group is hidden and cleared while Recur is
        unticked).
        """
        controls, choices = _the_add_form(auth_client, profile)
        _fill(controls, choices, values)
        body = _the_card(auth_client.post(_add_url(profile), data=controls, headers=HTMX))
        box, classes, words = _refusal_box(body)
        assert classes == "alert alert-danger"
        assert str(escape(sentence)) in words
        assert GENERIC.decode() not in body
        assert box == _typed(controls)
        page = _the_next_page(auth_client, profile, sentence)
        refilled = _filled_as_app_js_fills(box, *_the_add_form_on(page, profile))
        posted = dict(controls)
        if "is_recurring" not in posted:
            posted["terminal_year"] = [""]
        del refilled["csrf_token"], posted["csrf_token"]
        assert refilled == posted
        assert not _raises_of(profile)

    @pytest.mark.parametrize("posted, ticked", (
        pytest.param("on", "true", id="on-as-a-browser-posts-it"),
        pytest.param("true", "true", id="true"),
        pytest.param("false", "false", id="false"),
    ))
    def test_the_box_reads_recur_as_the_schema_does(self, auth_client, profile, posted, ticked):
        """A hand-made ``is_recurring=false`` reopens the form unticked, as it was read."""
        body = _the_card(auth_client.post(_add_url(profile), data={
            "raise_type_id": str(db.session.query(RaiseType).filter_by(name="merit").one().id),
            "effective_date": "2027-01-14", "percentage": "3", "flat_amount": "1000",
            "is_recurring": posted,
        }, headers=HTMX))
        box, _, _ = _refusal_box(body)
        assert box["data-raise-recurring"] == ticked

    def test_a_plain_post_still_flashes_and_redirects(self, auth_client, profile):
        """Without htmx the refusal is the flash on the edit page, as before."""
        controls, choices = _the_add_form(auth_client, profile)
        _fill(controls, choices, {"effective_date": "2027-01-14"})
        response = auth_client.post(_add_url(profile), data=controls)
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/salary/{profile.id}/edit")
        assert DESIGNED_FRAGMENT_HEADER not in response.headers
        page = auth_client.get(response.headers["Location"]).data.decode()
        assert str(escape(ONE_METHOD)) in page
        assert 'id="raise-refusal"' not in page

    def test_a_corrected_add_saves_from_the_box(self, auth_client, profile):
        """A yearly raise from February 29, refused, then moved to the 28th from the box."""
        controls, choices = _the_add_form(auth_client, profile)
        _fill(controls, choices, {
            "effective_date": "2028-02-29", "percentage": "3",
            "is_recurring": "on", "raise_end_mode": "none",
        })
        body = _the_card(auth_client.post(_add_url(profile), data=controls, headers=HTMX))
        box, _, _ = _refusal_box(body)
        assert "data-raise-edit-url" not in box
        page = _the_next_page(auth_client, profile, "February 29")
        refilled = _filled_as_app_js_fills(box, *_the_add_form_on(page, profile))
        refilled["effective_date"] = ["2028-02-28"]
        saved = _the_card_after_a_save(auth_client.post(
            _add_url(profile), data=refilled, headers=HTMX,
        ))
        assert 'id="raise-refusal"' not in saved
        [stored] = _raises_of(profile)
        assert (stored.effective_date, stored.percentage, stored.is_recurring) == (
            date(2028, 2, 28), Decimal("0.03"), True,
        )

    def test_the_update_door_redraws_the_card_in_edit_mode(self, auth_client, profile):
        """A flat amount beside the stored percentage: the box names the edit and its version.

        Corrected from the box -- the flat amount cleared -- it saves on the
        edit URL the box carries, as the edit it was.
        """
        stored = _stored_raise(profile)
        button = _edit_button(_page(auth_client, profile), stored.id)
        controls, choices = _the_add_form(auth_client, profile)
        _filled_as_app_js_fills(button, controls, choices)
        controls["flat_amount"] = ["1000"]
        body = _the_card(auth_client.post(
            button["data-raise-edit-url"], data=controls, headers=HTMX,
        ))
        box, classes, words = _refusal_box(body)
        assert classes == "alert alert-danger"
        assert str(escape(ONE_METHOD)) in words
        assert box == _typed(controls) | {"data-raise-edit-url": button["data-raise-edit-url"]}
        assert box["data-raise-version-id"] == button["data-raise-version-id"] != ""
        [kept] = _raises_of(profile)
        assert (kept.percentage, kept.flat_amount) == (Decimal("0.03"), None)
        page = _the_next_page(auth_client, profile, ONE_METHOD)
        refilled = _filled_as_app_js_fills(box, *_the_add_form_on(page, profile))
        refilled["flat_amount"], refilled["percentage"] = [""], ["4"]
        _the_card_after_a_save(auth_client.post(
            box["data-raise-edit-url"], data=refilled, headers=HTMX,
        ))
        [updated] = _raises_of(profile)
        assert (updated.id, updated.percentage, updated.flat_amount) == (
            stored.id, Decimal("0.04"), None,
        )

    def test_a_stale_form_redraws_the_card_with_a_warning(self, auth_client, profile):
        """Two tabs open on one raise: the second save is refused in a yellow box, kept stale.

        The box holds the version the stale form POSTED, so saving it again
        is refused again rather than written over the first tab's 4%.
        """
        stored = _stored_raise(profile)
        button = _edit_button(_page(auth_client, profile), stored.id)
        first, choices = _the_add_form(auth_client, profile)
        _filled_as_app_js_fills(button, first, choices)
        first["percentage"] = ["4"]
        assert auth_client.post(button["data-raise-edit-url"], data=first).status_code == 302
        second, choices = _the_add_form(auth_client, profile)
        _filled_as_app_js_fills(button, second, choices)
        second["percentage"] = ["5"]
        body = _the_card(auth_client.post(
            button["data-raise-edit-url"], data=second, headers=HTMX,
        ))
        box, classes, words = _refusal_box(body)
        assert classes == "alert alert-warning"
        assert str(escape(STALE)) in words
        assert box == _typed(second) | {"data-raise-edit-url": button["data-raise-edit-url"]}
        page = _the_next_page(auth_client, profile, STALE)
        again = _filled_as_app_js_fills(box, *_the_add_form_on(page, profile))
        _the_card(auth_client.post(box["data-raise-edit-url"], data=again, headers=HTMX))
        [kept] = _raises_of(profile)
        assert kept.percentage == Decimal("0.04")
