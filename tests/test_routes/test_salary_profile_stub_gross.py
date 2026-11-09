"""
Shekel Budget App -- a salary job's "my stub's gross includes after-tax earnings".

Plan step **salary:S11-c-2b**: rulings **R-SAL102** ("A yes/no on each job",
finding SAL-592) and **R-SAL105** ("Profile card, new + edit"), driven through
the salary profile's own form:

* the new and the edit form both carry the answer as a two-option select in
  the Profile card, "no" first and chosen for a new job;
* what the CREATE form posts is stored, and what the EDIT form posts is too --
  "no" over a stored "yes" included, which is the case a checkbox could not
  carry: an unticked box posts nothing, and the update door reads an absent
  field as "leave it";
* an update that does not carry the field leaves the stored answer alone.

The values posted are the ones the rendered select offers, read off the page,
and the edit door is posted the WHOLE form as the page renders it, so a
template whose option values the schema cannot read fails here.
"""

import re
from datetime import date

import pytest

from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from tests._stub_period import stub_period_on
from tests._test_helpers import freeze_today, rendered_form_controls

_FIELD = "stub_gross_includes_after_tax"


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: inside the seeded schedule, as the salary route tests freeze it."""
    freeze_today(monkeypatch, date(2026, 3, 20))


def _options(html):
    """The setting's select as the salary form renders it: ``[(value, label, selected)]``."""
    start = html.index(f'<select id="{_FIELD}"')
    select = html[start:html.index("</select>", start)]
    return [
        (value, label.strip(), bool(selected))
        for value, selected, label in re.findall(
            r'<option value="([^"]*)"\s*(selected)?\s*>([^<]*)</option>', select,
        )
    ]


def _value_of(html, label_start):
    """The value the rendered select posts for the option whose label starts *label_start*."""
    return next(value for value, label, _ in _options(html) if label.startswith(label_start))


def _create(client, name, **extra):
    """POST the create door as the new form posts it: the first payday, $2,884.62 a paycheck."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    response = client.post("/salary", data={
        "name": name,
        "pay_amount": "2884.62",
        "pay_payday": "2026-01-02",
        **stub_period_on("2026-01-02"),
        "filing_status_id": filing_status.id,
        "state_code": "NC",
        **extra,
    })
    assert response.status_code == 302, response.get_data(as_text=True)
    return name


def _answer(user_id, name):
    """The stored answer of the owner's profile named *name*, freshly read."""
    db.session.expire_all()
    return db.session.query(SalaryProfile).filter_by(
        user_id=user_id, name=name,
    ).one().stub_gross_includes_after_tax


@pytest.mark.usefixtures("seed_periods")
class TestTheSetting:
    """R-SAL102 / R-SAL105 through the salary profile's form.

    ``seed_periods`` writes the schedule the create door seats a paycheck on.
    """

    def test_the_new_form_offers_no_first_and_chooses_it(self, auth_client):
        """Two options, "no" chosen, under their label and help line.

        The words are R-SAL114's (the approved list, R-SAL115).
        """
        page = auth_client.get("/salary/new").data.decode()
        assert _options(page) == [
            ("false", "Base pay and taxable earnings", True),
            ("true", "Also untaxed pay, such as a mileage reimbursement", False),
        ]
        assert (
            f'<label for="{_FIELD}" class="form-label">What your pay stub\'s Gross Pay '
            "includes</label>"
        ) in page
        assert (
            '<div class="form-text">The pay stub form uses this to check the Gross Pay '
            "you type.</div>"
        ) in page

    def test_create_stores_what_the_form_posts(self, auth_client, seed_user):
        """The chosen option makes a "no" job; the other a "yes" one; a post without it, "no".

        Each option is found by its words, which are R-SAL114's (the approved
        list, R-SAL115).
        """
        page = auth_client.get("/salary/new").data.decode()
        user_id = seed_user["user"].id
        _create(auth_client, "Chosen", **{_FIELD: _value_of(page, "Base pay")})
        _create(auth_client, "Other", **{_FIELD: _value_of(page, "Also untaxed")})
        _create(auth_client, "Silent")
        assert _answer(user_id, "Chosen") is False
        assert _answer(user_id, "Other") is True
        assert _answer(user_id, "Silent") is False

    def test_the_edit_form_shows_yes_and_saves_no_over_it(self, auth_client, seed_user):
        """A "yes" job's form chooses "yes"; posting its "no" option stores "no".

        The case a checkbox loses: "no" must arrive as a VALUE, because the
        update door leaves an absent field alone.
        """
        user_id = seed_user["user"].id
        _create(auth_client, "Day Job", **{_FIELD: "true"})
        profile = db.session.query(SalaryProfile).filter_by(
            user_id=user_id, name="Day Job",
        ).one()
        page = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
        assert [value for value, _, chosen in _options(page) if chosen] == ["true"]
        controls = rendered_form_controls(page, f"/salary/{profile.id}")
        assert controls[_FIELD] == ["true"]
        controls[_FIELD] = [_value_of(page, "Base pay")]
        response = auth_client.post(f"/salary/{profile.id}", data=controls)
        assert response.status_code == 302
        assert _answer(user_id, "Day Job") is False

    def test_an_update_without_the_field_leaves_the_answer(self, auth_client, seed_user):
        """A rename that does not carry the field keeps the stored "yes"."""
        user_id = seed_user["user"].id
        _create(auth_client, "Day Job", **{_FIELD: "true"})
        profile = db.session.query(SalaryProfile).filter_by(
            user_id=user_id, name="Day Job",
        ).one()
        response = auth_client.post(f"/salary/{profile.id}", data={
            "name": "Renamed Job", "version_id": profile.version_id,
        })
        assert response.status_code == 302
        assert _answer(user_id, "Renamed Job") is True
