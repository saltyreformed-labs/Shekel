"""Remove a pay entry, never the only one (plan step salary:X-av-3b, ruling R-SAL68).

"Remove, except the last one."  Every POST here is what the salary page
RENDERS, read off it by :func:`tests._salary_forms.rendered_page_form`; every
figure is made up.

* the paychecks a removed entry covered are priced from the entry before it,
  with the forecast raises landing since that one opens; removing the FIRST
  entry hands the paychecks before the next one to it (ruling **R-SAL59**);
* the only entry renders no Remove, and a Remove of it is refused in words;
* a Remove rendered before another tab's Fix is refused, and one losing the
  race at the DELETE is reported, never a 500;
* another owner's entry is a 404, paired with the owner's own POST to the
  same URL shape resolving, so the 404 is the gate's and not the URL map's.

Today is pinned to 2026-03-20, inside the seeded schedule (biweekly Fridays
from 2026-01-02).  Every job ends its pay periods six days before payday, so
02-13's paycheck pays Sunday 01-25 through Saturday 02-07 (weekdays 01-26 to
01-30 and 02-02 to 02-06) and 02-27's pays 02-08 through 02-21.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services import pay_list_service
from app.services.balance_at import BalanceContext
from app.services.payroll_basis import PayrollBasis
from tests._salary_forms import create_through_the_form, rendered_page_form
from tests._test_helpers import freeze_today, make_salary_profile


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20."""
    freeze_today(monkeypatch, date(2026, 3, 20))


def _edit(profile_id):
    """The salary page of *profile_id*."""
    return f"/salary/{profile_id}/edit"


def _remove_url(entry_id):
    """The Remove door's URL for the entry *entry_id*."""
    return f"/salary/pay/{entry_id}/remove"


def _two_entries(client, seed_user):
    """A job of $2,000.00 from 01-02, then $2,100.00 recorded from 02-13.

    Returns:
        ``(profile_id, first_entry_id, second_entry_id)``.
    """
    profile = create_through_the_form(client, seed_user, "2000.00", "2026-01-02")
    record = f"/salary/{profile.id}/pay"
    controls = rendered_page_form(client, _edit(profile.id), record)
    controls.update({"payday": "2026-02-13", "amount": "2100.00"})
    assert client.post(record, data=controls).status_code == 302
    first, second = _entries(profile.id)
    return profile.id, first.id, second.id


def _entries(profile_id):
    """The profile's pay entries as the database holds them, payday ascending."""
    db.session.expire_all()
    return (
        db.session.query(SalaryPayEntry)
        .filter_by(salary_profile_id=profile_id)
        .order_by(SalaryPayEntry.payday)
        .all()
    )


def _pay_list(profile_id):
    """``[(payday, amount), ...]`` of the profile's pay list."""
    return [(entry.payday, entry.amount) for entry in _entries(profile_id)]


def _remove(client, profile_id, entry_id, follow=False):
    """POST the entry's Remove form as the salary page renders it."""
    controls = rendered_page_form(client, _edit(profile_id), _remove_url(entry_id))
    return client.post(_remove_url(entry_id), data=controls, follow_redirects=follow)


def _base_on(profile_id, user_id, payday):
    """What the paycheck on *payday* pays as base, priced off the stored pay list."""
    db.session.expire_all()
    profile = db.session.get(SalaryProfile, profile_id)
    calendar = BalanceContext.build(user_id).calendar()
    return PayrollBasis(profile, calendar).base_pay_on(payday).per_paycheck


class TestTheCoveredPaychecksFallBack:
    """What a removed entry covered is priced from the entries left."""

    def test_they_fall_back_to_the_entry_before_with_its_raises(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 3% raise from 02-01; removing 02-13's $2,100.00 prices 02-13 from 01-02's $2,000.00.

        With both entries 02-13's paycheck opens at $2,100.00 and the raise
        reaches its five weekdays from 02-02: 1,050.00 + 5/10 of 2,163.00 =
        1,081.50, so $2,131.50.  Removed, the paycheck falls back to the
        $2,000.00 from 01-02, the raise landing after that entry opens:
        1,000.00 + 5/10 of 2,060.00 = 1,030.00, so $2,030.00, and 02-27
        pays the whole $2,060.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", "2026-01-02",
            )
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
                effective_date=date(2026, 2, 1),
                percentage=Decimal("0.0300"),
            ))
            db.session.commit()
            record = f"/salary/{profile.id}/pay"
            controls = rendered_page_form(auth_client, _edit(profile.id), record)
            controls.update({"payday": "2026-02-13", "amount": "2100.00"})
            auth_client.post(record, data=controls)
            second = _entries(profile.id)[1]
            assert _base_on(profile.id, user_id, date(2026, 2, 13)) == Decimal("2131.50")

            response = _remove(auth_client, profile.id, second.id, follow=True)

            assert b"Your pay from 2026-02-13 is removed." in response.data
            assert _pay_list(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]
            assert _base_on(profile.id, user_id, date(2026, 2, 13)) == Decimal("2030.00")
            assert _base_on(profile.id, user_id, date(2026, 2, 27)) == Decimal("2060.00")

    def test_removing_the_first_hands_the_earlier_paychecks_to_the_next(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Removing 01-02's $2,000.00 prices 01-02 and 01-16 at 02-13's $2,100.00 (R-SAL59)."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile_id, first_id, _ = _two_entries(auth_client, seed_user)

            _remove(auth_client, profile_id, first_id)

            assert _pay_list(profile_id) == [(date(2026, 2, 13), Decimal("2100.00"))]
            assert _base_on(profile_id, user_id, date(2026, 1, 2)) == Decimal("2100.00")
            assert _base_on(profile_id, user_id, date(2026, 1, 16)) == Decimal("2100.00")


class TestTheOnlyEntry:
    """The only entry is never removed."""

    def test_it_renders_no_remove_and_two_entries_render_one_each(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """One entry: no Remove form.  Two: each posts its own version pin."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", "2026-01-02",
            )
            only = _entries(profile.id)[0]
            page = auth_client.get(_edit(profile.id)).get_data(as_text=True)
            assert _remove_url(only.id) not in page

            record = f"/salary/{profile.id}/pay"
            controls = rendered_page_form(auth_client, _edit(profile.id), record)
            controls.update({"payday": "2026-02-13", "amount": "2100.00"})
            assert auth_client.post(record, data=controls).status_code == 302
            entries = _entries(profile.id)
            assert len(entries) == 2
            for entry in entries:
                controls = rendered_page_form(
                    auth_client, _edit(profile.id), _remove_url(entry.id),
                )
                assert controls["version_id"] == [str(entry.version_id)]

    def test_a_remove_of_it_is_refused_in_words(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A crafted POST for the only entry: the door's sentence, and the entry stands."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", "2026-01-02",
            )
            only = _entries(profile.id)[0]

            response = auth_client.post(
                _remove_url(only.id),
                data={"version_id": str(only.version_id)},
                follow_redirects=True,
            )

            assert (
                b"Your pay from 2026-01-02 is your only pay entry, and every "
                b"paycheck is priced from it, so it can&#39;t be removed.  Fix "
                b"it instead." in response.data
            )
            assert _pay_list(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]


class TestRaces:
    """A Remove never deletes pay the owner has not seen."""

    def test_a_remove_rendered_before_another_tabs_fix_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """02-13's Remove form, then a Fix of 02-13 in another tab: the Remove is refused."""
        with app.app_context():
            profile_id, _, second_id = _two_entries(auth_client, seed_user)
            stale = rendered_page_form(
                auth_client, _edit(profile_id), _remove_url(second_id),
            )
            fix = f"/salary/pay/{second_id}/fix"
            fix_controls = rendered_page_form(auth_client, _edit(profile_id), fix)
            fix_controls["amount"] = "2150.00"
            assert auth_client.post(fix, data=fix_controls).status_code == 302

            response = auth_client.post(
                _remove_url(second_id), data=stale, follow_redirects=True,
            )

            assert b"changed by another action" in response.data
            assert _pay_list(profile_id) == [
                (date(2026, 1, 2), Decimal("2000.00")),
                (date(2026, 2, 13), Decimal("2150.00")),
            ]

    def test_a_remove_losing_the_race_at_the_delete_is_reported_not_a_500(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The entry's version moves between the check and the DELETE: a flash.

        Planted by bumping the version with autoflush held off, right after
        the service stages the DELETE, which is then pinned to a version no
        row holds and raises ``StaleDataError`` at the flush -- inside the
        route's guard.
        """
        with app.app_context():
            profile_id, _, second_id = _two_entries(auth_client, seed_user)
            real_remove = pay_list_service.remove_entry

            def remove_then_lose_the_race(*args, **kwargs):
                real_remove(*args, **kwargs)
                with db.session.no_autoflush:
                    db.session.execute(sqlalchemy.text(
                        "UPDATE salary.pay_entries SET version_id = version_id + 1 "
                        "WHERE id = :id"
                    ), {"id": second_id})

            with patch.object(
                pay_list_service, "remove_entry", side_effect=remove_then_lose_the_race,
            ):
                response = _remove(auth_client, profile_id, second_id, follow=True)

            assert response.status_code == 200
            assert b"changed by another action" in response.data
            assert [entry.id for entry in _entries(profile_id)][1] == second_id


class TestOwnership:
    """Another owner's entry is a 404 from the gate, not from the URL map."""

    def test_another_owners_entry_is_a_404_and_stands(
        self, app, auth_client, seed_user, seed_periods,
        seed_second_user, seed_second_periods,
    ):
        """The second owner's entry: 404, still there; the owner's own: 302."""
        with app.app_context():
            victim = make_salary_profile(
                seed_second_user, db.session, name="Victim Job",
                pay=Decimal("3000.00"), pay_from=seed_second_periods[0].start_date,
            )
            db.session.add(SalaryPayEntry(
                salary_profile_id=victim.id, payday=seed_second_periods[2].start_date,
                amount=Decimal("3100.00"),
            ))
            db.session.commit()
            theirs = _entries(victim.id)[1]
            profile_id, _, mine = _two_entries(auth_client, seed_user)
            controls = rendered_page_form(auth_client, _edit(profile_id), _remove_url(mine))

            hostile = dict(controls, version_id=str(theirs.version_id))
            assert auth_client.post(_remove_url(theirs.id), data=hostile).status_code == 404
            assert len(_entries(victim.id)) == 2

            # The pair: the same URL shape resolves for the owner's own entry.
            assert auth_client.post(_remove_url(mine), data=controls).status_code == 302

    def test_an_entry_that_does_not_exist_is_a_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No such entry: the same 404."""
        with app.app_context():
            response = auth_client.post(_remove_url(999999), data={"version_id": "1"})
            assert response.status_code == 404
