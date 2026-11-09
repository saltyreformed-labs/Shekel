"""
Shekel Budget App -- a job's pay periods through its own form (plan#3048, task plan#3099).

Ruling **plan#3096**: "Your job's salary page gets two date boxes, plus a line
showing your next paychecks and the days each pays for ... a new job asks for
the two dates when you set it up."  Driven through the salary profile's form
over ``seed_periods`` (every 14 days from Friday 2026-01-02, ten paydays to
05-08), today frozen at Friday 2026-03-20:

* :class:`TestTheCreateForm` -- the new form asks the two dates; what it posts
  becomes the gap, and each refusal is in words with no job made;
* :class:`TestTheEditForm` -- the edit form shows the gap back as the latest
  payday (03-13) and its period end, lists the next paychecks with their days,
  re-saves to the same gap as rendered, takes a new pair, and refuses one date
  alone or a Period End after its Pay Date, staging nothing.
"""

import re
from datetime import date

import pytest

from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from tests._test_helpers import freeze_today, rendered_form_controls


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: inside the seeded schedule, as the salary route tests freeze it."""
    freeze_today(monkeypatch, date(2026, 3, 20))


def _create(client, name, pay_date, period_end, follow=False):
    """POST the create door as the new form posts it, with one stub's two dates."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    return client.post("/salary", data={
        "name": name,
        "pay_amount": "2884.62",
        "pay_payday": "2026-01-02",
        "filing_status_id": filing_status.id,
        "state_code": "NC",
        "stub_pay_date": pay_date,
        "stub_period_end": period_end,
    }, follow_redirects=follow)


def _profile(user_id, name):
    """The owner's profile named *name*, freshly read, or ``None``."""
    db.session.expire_all()
    return db.session.query(SalaryProfile).filter_by(
        user_id=user_id, name=name,
    ).one_or_none()


def _input_tag(page, control_id):
    """The rendered ``<input>`` tag whose id is *control_id*, whitespace collapsed."""
    start = page.index(f'<input type="date" id="{control_id}"')
    return " ".join(page[start:page.index(">", start) + 1].split())


def _upcoming_lines(page):
    """The next-paychecks list's lines, whitespace collapsed."""
    start = page.index('<ul class="list-unstyled small mt-2 mb-0" id="upcoming_pay_periods">')
    block = page[start:page.index("</ul>", start)]
    return [" ".join(item.split()) for item in re.findall(r"<li>(.*?)</li>", block, re.S)]


@pytest.mark.usefixtures("seed_periods")
class TestTheCreateForm:
    """A new job states its two dates."""

    def test_the_new_form_asks_both_dates_blank_and_required(self, auth_client):
        """Two date boxes, nothing filled in, both required; no paycheck list yet."""
        page = auth_client.get("/salary/new").data.decode()
        assert _input_tag(page, "stub_pay_date") == (
            '<input type="date" id="stub_pay_date" name="stub_pay_date" '
            'class="form-control" required value="">'
        )
        assert _input_tag(page, "stub_period_end") == (
            '<input type="date" id="stub_period_end" name="stub_period_end" '
            'class="form-control" required value="">'
        )
        assert '<label for="stub_pay_date" class="form-label">Pay Date on a pay stub</label>' in page
        assert '<label for="stub_period_end" class="form-label">Period End on that stub</label>' in page
        assert 'id="upcoming_pay_periods"' not in page

    def test_the_two_dates_become_the_gap(self, auth_client, seed_user):
        """01-16 and 01-10 store 6; 01-16 and 01-13 store 3."""
        user_id = seed_user["user"].id
        assert _create(auth_client, "Six", "2026-01-16", "2026-01-10").status_code == 302
        assert _create(auth_client, "Three", "2026-01-16", "2026-01-13").status_code == 302
        assert _profile(user_id, "Six").period_end_days_before_payday == 6
        assert _profile(user_id, "Three").period_end_days_before_payday == 3

    def test_a_pay_date_that_is_not_a_payday_makes_no_job(self, auth_client, seed_user):
        """01-17 is the day after a payday: the salary doors' words, no profile."""
        response = _create(auth_client, "Day Job", "2026-01-17", "2026-01-10", follow=True)
        assert b"2026-01-17 is not one of your paydays." in response.data
        assert _profile(seed_user["user"].id, "Day Job") is None

    def test_a_period_end_after_the_pay_date_makes_no_job(self, auth_client, seed_user):
        """Period End 01-17 after Pay Date 01-16: refused in words, no profile."""
        response = _create(auth_client, "Day Job", "2026-01-16", "2026-01-17", follow=True)
        assert (
            b"Period End 2026-01-17 is after the Pay Date 2026-01-16.  Copy both "
            b"dates from one pay stub: a paycheck pays for days that have ended "
            b"by its payday."
        ) in response.data
        assert _profile(seed_user["user"].id, "Day Job") is None

    def test_a_create_without_the_dates_makes_no_job(self, auth_client, seed_user):
        """Both boxes blank: the form's validation refusal, no profile."""
        response = _create(auth_client, "Day Job", "", "", follow=True)
        assert b"Please correct the highlighted errors" in response.data
        assert _profile(seed_user["user"].id, "Day Job") is None


@pytest.mark.usefixtures("seed_periods")
class TestTheEditForm:
    """The edit form shows the gap as dates, and takes a new pair."""

    @pytest.fixture()
    def profile(self, auth_client, seed_user):
        """A job created through the form from the 01-16 stub: a gap of 6."""
        assert _create(auth_client, "Day Job", "2026-01-16", "2026-01-10").status_code == 302
        return _profile(seed_user["user"].id, "Day Job")

    def _page(self, client, profile):
        """The job's edit page, rendered."""
        return client.get(f"/salary/{profile.id}/edit").data.decode()

    def test_it_shows_the_latest_payday_and_its_period_end(self, auth_client, profile):
        """On 03-20 the latest payday is 03-13, and 6 days before it is 03-07."""
        controls = rendered_form_controls(self._page(auth_client, profile), f"/salary/{profile.id}")
        assert controls["stub_pay_date"] == ["2026-03-13"]
        assert controls["stub_period_end"] == ["2026-03-07"]

    def test_the_boxes_are_not_required_on_the_edit_form(self, auth_client, profile):
        """Blank together leaves the stored gap alone, so the browser may post them blank."""
        page = self._page(auth_client, profile)
        assert " required " not in _input_tag(page, "stub_pay_date")
        assert " required " not in _input_tag(page, "stub_period_end")

    def test_it_lists_the_next_paychecks_and_their_days(self, auth_client, profile):
        """03-27, 04-10 and 04-24, each paying the fortnight that ended 6 days before.

        03-27 - 6 = Sat 03-21, from the day after 03-13 - 6 = 03-07.
        """
        assert _upcoming_lines(self._page(auth_client, profile)) == [
            "Your Mar 27 paycheck pays Sun Mar 8 through Sat Mar 21",
            "Your Apr 10 paycheck pays Sun Mar 22 through Sat Apr 4",
            "Your Apr 24 paycheck pays Sun Apr 5 through Sat Apr 18",
        ]

    def test_posted_as_rendered_it_keeps_the_gap(self, auth_client, seed_user, profile):
        """The shown-back pair re-saves to 6."""
        controls = rendered_form_controls(self._page(auth_client, profile), f"/salary/{profile.id}")
        assert auth_client.post(f"/salary/{profile.id}", data=controls).status_code == 302
        assert _profile(seed_user["user"].id, "Day Job").period_end_days_before_payday == 6

    def test_a_new_pair_stores_a_new_gap(self, auth_client, seed_user, profile):
        """03-13 and 03-10 store 3, and the list moves with it."""
        controls = rendered_form_controls(self._page(auth_client, profile), f"/salary/{profile.id}")
        controls["stub_period_end"] = ["2026-03-10"]
        assert auth_client.post(f"/salary/{profile.id}", data=controls).status_code == 302
        assert _profile(seed_user["user"].id, "Day Job").period_end_days_before_payday == 3
        assert _upcoming_lines(self._page(auth_client, profile))[0] == (
            "Your Mar 27 paycheck pays Wed Mar 11 through Tue Mar 24"
        )

    def test_both_left_out_leaves_the_gap(self, auth_client, seed_user, profile):
        """A rename that carries neither date keeps the stored 6."""
        response = auth_client.post(f"/salary/{profile.id}", data={
            "name": "Renamed Job", "version_id": profile.version_id,
        })
        assert response.status_code == 302
        assert _profile(seed_user["user"].id, "Renamed Job").period_end_days_before_payday == 6

    def test_one_date_alone_is_refused_and_nothing_saves(self, auth_client, seed_user, profile):
        """The Period End cleared: refused in words; the rename beside it is not saved."""
        controls = rendered_form_controls(self._page(auth_client, profile), f"/salary/{profile.id}")
        controls["stub_period_end"] = [""]
        controls["name"] = ["Renamed Job"]
        response = auth_client.post(
            f"/salary/{profile.id}", data=controls, follow_redirects=True,
        )
        assert b"Copy both the Pay Date and the Period End from one pay stub." in response.data
        assert _profile(seed_user["user"].id, "Renamed Job") is None
        assert _profile(seed_user["user"].id, "Day Job").period_end_days_before_payday == 6

    def test_a_period_end_after_the_pay_date_is_refused(self, auth_client, seed_user, profile):
        """Period End 03-14 after Pay Date 03-13: refused in words, the gap kept."""
        controls = rendered_form_controls(self._page(auth_client, profile), f"/salary/{profile.id}")
        controls["stub_period_end"] = ["2026-03-14"]
        response = auth_client.post(
            f"/salary/{profile.id}", data=controls, follow_redirects=True,
        )
        assert (
            b"Period End 2026-03-14 is after the Pay Date 2026-03-13." in response.data
        )
        assert _profile(seed_user["user"].id, "Day Job").period_end_days_before_payday == 6
