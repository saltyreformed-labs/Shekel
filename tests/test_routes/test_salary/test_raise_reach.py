"""Salary route tests: the raise doors refuse a raise no paycheck gets, and the card labels one pay holds.

Task plan#3169 (plan step salary:X-av-3b), ruling **R-SAL95** ("Refuse it,
label held") on the day an entry opens (ruling **plan#3091**), in the words of
rulings **plan#3374** and **plan#3375**.  Each raise is posted as the rendered
form posts it.  The rules themselves are graded on a made-up calendar in
``tests/test_services/test_raise_in_pay.py``; this file grades the doors and
the card: a refusal stores nothing and is said where it was made, a raise a
paycheck gets is saved, and both renders of the card label what recorded pay
holds.

**The calendar**: the seeded owner's paydays every 14 days from Friday
2026-01-02, each paycheck paying the Sunday-to-Saturday period that ends six
days before it.  The profile's one entry is from 2026-01-02, whose paycheck
opens on Sunday 2025-12-14; an entry from 2026-02-13 opens on Sunday 01-25.
**No date here is read from a clock**: the package's conftest freezes today at
2026-03-20.
"""

from datetime import date
from decimal import Decimal

import pytest
from markupsafe import escape

from app.extensions import db
from app.models.ref import RaiseType
from app.models.salary_raise import SalaryRaise
from app.services import pay_list_service
from app.services.balance_at import BalanceContext
from app.utils.dates import display_today
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from tests._form_controls import rendered_form_choices, rendered_form_controls

from ._helpers import _create_profile

HTMX = {"HX-Request": "true"}

#: Ruling plan#3374's words for a raise the one entry, from 2026-01-02, holds.
HELD_BY_THE_FIRST = (
    "A raise starting by 2025-12-14 is already in your pay from 2026-01-02. "
    "Start it after 2025-12-14, or fix your pay."
)


def _page(client, profile):
    """The profile's edit page, rendered."""
    return client.get(f"/salary/{profile.id}/edit").data.decode()


def _filled(page, action, values):
    """The controls of the form posting to *action* on *page*, filled with *values*.

    A name the form does not render, or a choice it does not offer, fails
    here rather than being posted as a control it lacks.
    """
    controls = rendered_form_controls(page, action)
    choices = rendered_form_choices(page, action)
    for name, value in values.items():
        if name in choices:
            assert value in choices[name], f"the raise form offers no {name}={value!r}"
        else:
            assert name in controls, f"the raise form renders no {name!r}"
        controls[name] = [value]
    return controls


def _add(client, profile, headers=None, **values):
    """Post the add form, as the page renders it, with a 3% merit raise and *values*."""
    action = f"/salary/{profile.id}/raises"
    merit = db.session.query(RaiseType).filter_by(name="merit").one()
    controls = _filled(_page(client, profile), action, {
        "raise_type_id": str(merit.id), "percentage": "3", **values,
    })
    return client.post(action, data=controls, headers=headers or {})


def _the_card(response, status):
    """The Raises card an htmx save answers with, at *status*."""
    assert response.status_code == status
    body = response.data.decode()
    assert body.count('id="raises-section"') == 1
    assert "<html" not in body
    return body


def _raises_of(profile):
    """The profile's raises, freshly read: what the database holds."""
    db.session.expire_all()
    return db.session.query(SalaryRaise).filter_by(salary_profile_id=profile.id).all()


def _record(profile, payday):
    """Record $2,950.00 a paycheck from *payday*, through the Record door's writer, and commit."""
    pay_list_service.record_entry(
        profile, BalanceContext.build(profile.user_id), Decimal("2950.00"), payday,
        display_today(),
    )
    db.session.commit()


@pytest.fixture(name="profile")
def profile_fixture(seed_user, seed_periods):  # pylint: disable=unused-argument
    """A salary profile on the seeded calendar, its one pay entry from 2026-01-02, with no raise.

    Pylint: ``unused-argument`` -- ``seed_periods`` is the calendar the
    profile's paychecks and pay periods are read from, never read here.
    """
    return _create_profile(seed_user)


class TestTheAddDoor:
    """The add door refuses a raise no paycheck gets and saves one a paycheck gets."""

    def test_a_raise_the_entry_holds_is_refused_on_the_card(self, auth_client, profile):
        """From 2025-12-14, the day 2026-01-02's paycheck opens: the card at 422, nothing stored.

        The reason box carries the date as typed, so the form reopens on it.
        """
        body = _the_card(_add(auth_client, profile, HTMX, effective_date="2025-12-14"), 422)

        assert str(escape(HELD_BY_THE_FIRST)) in body
        assert 'data-raise-date="2025-12-14"' in body
        assert not _raises_of(profile)

    def test_a_plain_post_flashes_the_refusal(self, auth_client, profile):
        """Without htmx the refusal is the flash on the edit page, and nothing is stored."""
        response = _add(auth_client, profile, effective_date="2025-12-01")

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/salary/{profile.id}/edit")
        assert DESIGNED_FRAGMENT_HEADER not in response.headers
        assert str(escape(HELD_BY_THE_FIRST)) in _page(auth_client, profile)
        assert not _raises_of(profile)

    def test_a_raise_the_day_after_the_entry_opens_is_saved_unlabelled(
        self, auth_client, profile,
    ):
        """From Monday 2025-12-15: 2026-01-02's paycheck pays its weekdays at the raised pay."""
        body = _the_card(_add(auth_client, profile, HTMX, effective_date="2025-12-15"), 200)

        assert "in your pay from" not in body.lower()
        [stored] = _raises_of(profile)
        assert stored.effective_date == date(2025, 12, 15)

    def test_a_yearly_raise_every_year_of_which_is_held_is_refused(self, auth_client, profile):
        """Every 06-01 from 2024 through 2025: both years land before 2025-12-14."""
        body = _the_card(_add(
            auth_client, profile, HTMX, effective_date="2024-06-01",
            is_recurring="on", raise_end_mode="year", terminal_year="2025",
        ), 422)

        assert str(escape(HELD_BY_THE_FIRST)) in body
        assert not _raises_of(profile)

    def test_a_yearly_raise_with_a_later_year_is_saved_and_labelled(self, auth_client, profile):
        """Every 06-01 from 2024 with no end: 2024-2025 are in the pay, 2026 on reaches it (plan#3375)."""
        body = _the_card(_add(
            auth_client, profile, HTMX, effective_date="2024-06-01",
            is_recurring="on", raise_end_mode="none",
        ), 200)

        assert "2024-2025 in your pay from 2026-01-02" in body
        assert len(_raises_of(profile)) == 1


class TestTheEditDoor:
    """The edit door refuses an edit leaving a raise no paycheck gets, and writes nothing."""

    def test_an_edit_into_the_held_pay_is_refused_and_the_row_kept(self, auth_client, profile):
        """A raise from 2026-03-01 moved to 2025-12-01: refused in edit mode, the row as it was."""
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        stored = SalaryRaise(
            salary_profile=profile, raise_type_id=merit.id,
            effective_date=date(2026, 3, 1), percentage=Decimal("0.0300"),
        )
        db.session.add(stored)
        db.session.commit()
        raise_id, version = stored.id, stored.version_id
        action = f"/salary/raises/{raise_id}/edit"
        page = _page(auth_client, profile)
        controls = _filled(page, f"/salary/{profile.id}/raises", {
            "raise_type_id": str(merit.id), "effective_date": "2025-12-01",
            "percentage": "3.00", "version_id": str(version),
        })

        body = _the_card(auth_client.post(action, data=controls, headers=HTMX), 422)

        assert str(escape(HELD_BY_THE_FIRST)) in body
        assert f'data-raise-edit-url="{action}"' in body
        # The card lists the raise as stored, not as the refused edit left it
        # in the session: the door rolls the edit back before redrawing.
        assert "Mar 1, 2026" in body and "Dec 1, 2025" not in body
        [kept] = _raises_of(profile)
        assert (kept.effective_date, kept.version_id) == (date(2026, 3, 1), version)


class TestTheCardLabelsWhatThePayHolds:
    """Both renders of the card mark a raise a later entry holds (ruling R-SAL95)."""

    def test_a_raise_between_two_entries_is_saved_and_labelled(self, auth_client, profile):
        """With an entry from 02-13 too, a raise from Tuesday 01-20 reaches 01-30 and 02-13 holds it.

        01-30's paycheck pays 01-11..01-24 from 2026-01-02's entry, so its
        weekdays from 01-20 are paid at the raise; 02-13's entry opens 01-25.
        Saved, and labelled on the card the add door answers with.
        """
        _record(profile, date(2026, 2, 13))

        body = _the_card(_add(auth_client, profile, HTMX, effective_date="2026-01-20"), 200)

        assert "In your pay from 2026-02-13" in body
        assert len(_raises_of(profile)) == 1

    def test_a_saved_raise_a_later_record_holds_stays_labelled(self, auth_client, profile):
        """A raise from 01-25, saved while one entry stands, stays when 02-13's entry comes to hold it.

        Recording 02-13 makes it a raise the add door would now refuse (02-13
        opens 01-25), but a saved raise is kept and the edit page marks it.
        """
        _the_card(_add(auth_client, profile, HTMX, effective_date="2026-01-25"), 200)
        _record(profile, date(2026, 2, 13))

        page = _page(auth_client, profile)

        assert "In your pay from 2026-02-13" in page
        [kept] = _raises_of(profile)
        assert kept.effective_date == date(2026, 1, 25)
