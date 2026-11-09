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
    page = _page(client, profile)
    return (
        rendered_form_controls(page, _add_url(profile)),
        rendered_form_choices(page, _add_url(profile)),
    )


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

        ``_populateRaiseForm`` sets each control from the button and posts to
        its edit URL; a recurring raise ending 2030 answers "ends after".
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
        _fill(controls, choices, {
            "raise_type_id": button["data-raise-type-id"],
            "effective_date": button["data-raise-date"],
            "percentage": button["data-raise-percentage"],
            "flat_amount": button["data-raise-flat"],
            "terminal_year": button["data-raise-terminal-year"],
            "version_id": button["data-raise-version-id"],
            "is_recurring": "on",
            "raise_end_mode": "year",
        })
        assert button["data-raise-recurring"] == "true"
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
