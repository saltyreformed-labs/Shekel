"""Record a pay change (plan step salary:X-av-3b, ruling R-SAL61).

"'Record a pay change' adds one from a payday you pick (like the stub
screen)."  Every POST here is what the salary page RENDERS, read off it by
:func:`tests._form_controls.rendered_form_controls`; every figure is made up.

* the rendered form states no payday and no amount: the owner picks both;
* it records the entry, and the paychecks it covers are priced from it:
  a raise dated by the first day its paycheck pays for is held in the
  recorded pay, one dated after it still reaches that paycheck's later
  weekdays (ruling **plan#3091**), and a paycheck before it is untouched;
* it refuses, in words and writing nothing: a day that is not a payday, one
  later than the next payday (rulings **R-SAL90**, **R-SAL93**), one another
  entry holds (one entry per payday, Fix's own sentence), and pay not above
  zero; a Record racing another onto one payday is reported, never a 500;
* another owner's profile is a 404, paired with the owner's own POST to the
  same URL shape resolving, so the 404 is the gate's and not the URL map's.

Today is pinned to 2026-03-20: inside the seeded schedule (biweekly Fridays
from 2026-01-02), 03-13 the current payday and 03-27 the next.  Every profile
here ends its pay periods six days before payday, so 03-27's paycheck pays
Sunday 03-08 through Saturday 03-21: ten weekdays, 03-09 to 03-20.
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
    """Today is 2026-03-20: the 03-13 payday current, 03-27 the next."""
    freeze_today(monkeypatch, date(2026, 3, 20))


#: What the pay doors say of 04-10 on 03-20 (R-SAL93, "Each door names its own").
_AFTER_THE_NEXT_PAYDAY = (
    b"2026-04-10 has not been paid yet.  Pay can be recorded up to your "
    b"next payday, 2026-03-27."
)


def _profile(client, seed_user, seed_periods):
    """The owner's job, made through the create form: $2,000.00 a paycheck from 2026-01-02."""
    return create_through_the_form(
        client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
    )


def _record_url(profile):
    """The Record door's URL for *profile*."""
    return f"/salary/{profile.id}/pay"


def _record_form(client, profile):
    """The Record form as the salary page renders it."""
    return rendered_page_form(client, f"/salary/{profile.id}/edit", _record_url(profile))


def _record(client, profile, payday, amount, follow=False):
    """POST the rendered Record form, the owner having typed *payday* and *amount*."""
    controls = _record_form(client, profile)
    controls.update({"payday": payday, "amount": amount})
    return client.post(_record_url(profile), data=controls, follow_redirects=follow)


def _entries(profile_id):
    """``[(payday, amount), ...]`` of the profile's pay list as the database holds it."""
    db.session.expire_all()
    return [
        (entry.payday, entry.amount)
        for entry in db.session.query(SalaryPayEntry)
        .filter_by(salary_profile_id=profile_id)
        .order_by(SalaryPayEntry.payday)
    ]


def _base_on(profile_id, user_id, payday):
    """What the paycheck on *payday* pays as base, priced off the stored pay list."""
    db.session.expire_all()
    profile = db.session.get(SalaryProfile, profile_id)
    calendar = BalanceContext.build(user_id).calendar()
    return PayrollBasis(profile, calendar).base_pay_on(payday).per_paycheck


def _merit_raise(profile, starts_on):
    """A one-time 3% merit raise from *starts_on*, committed."""
    db.session.add(SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        effective_date=starts_on,
        percentage=Decimal("0.0300"),
    ))
    db.session.commit()


class TestTheForm:
    """The Pay card renders a Record form the owner fills in whole."""

    def test_the_form_states_no_payday_and_no_amount(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Both boxes render empty: the payday is picked, like the stub screen's.

        R-SAL61's words are "a payday you pick (like the stub screen)", and
        the stub screen's payday box renders empty: a default would date pay
        the owner did not choose to date.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            controls = _record_form(auth_client, profile)
            assert controls["payday"] == [""]
            assert controls["amount"] == [""]
            assert set(controls) == {"csrf_token", "payday", "amount"}


class TestRecord:
    """A Record adds one entry, from its payday on."""

    def test_it_records_the_pay_change(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``$2,100.00`` from 03-27, the next payday: a second entry, and a flash."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)

            response = _record(auth_client, profile, "2026-03-27", "2100.00", follow=True)

            assert b"Pay recorded from 2026-03-27." in response.data
            assert _entries(profile.id) == [
                (date(2026, 1, 2), Decimal("2000.00")),
                (date(2026, 3, 27), Decimal("2100.00")),
            ]
            # The page lists it, beside its yearly figure: 2,100.00 x 26.
            assert b"54,600.00" in response.data

    def test_a_raise_dated_by_the_day_its_paycheck_opens_is_held(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 3% raise from 03-01, then $2,100.00 recorded from 03-27: the raise is in it.

        03-27's paycheck opens 03-08, and the raise starts before that, so the
        recorded pay already holds it (ruling plan#3091): 03-27 and 04-10 both
        pay exactly $2,100.00, not $2,163.00 (2,100.00 x 1.03).
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            _merit_raise(profile, date(2026, 3, 1))
            user_id = seed_user["user"].id

            _record(auth_client, profile, "2026-03-27", "2100.00")

            assert _base_on(profile.id, user_id, date(2026, 3, 27)) == Decimal("2100.00")
            assert _base_on(profile.id, user_id, date(2026, 4, 10)) == Decimal("2100.00")

    def test_a_raise_dated_after_that_day_reaches_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 3% raise from 03-10, then $2,100.00 recorded from 03-27: the raise reaches it.

        03-27's paycheck opens 03-08, before the raise, so the recorded pay
        does not hold it, and it raises nine of the paycheck's ten weekdays
        (03-10 to 03-20) to 2,100.00 x 1.03 = 2,163.00: 1/10 of 2,100.00 =
        210.00 plus 9/10 of 2,163.00 = 1,946.70, so 03-27 pays $2,156.70 and
        04-10 the whole $2,163.00.  03-13's paycheck (02-22 to 03-07) is
        before the entry and before the raise: still $2,000.00.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            _merit_raise(profile, date(2026, 3, 10))
            user_id = seed_user["user"].id

            _record(auth_client, profile, "2026-03-27", "2100.00")

            assert _base_on(profile.id, user_id, date(2026, 3, 13)) == Decimal("2000.00")
            assert _base_on(profile.id, user_id, date(2026, 3, 27)) == Decimal("2156.70")
            assert _base_on(profile.id, user_id, date(2026, 4, 10)) == Decimal("2163.00")

    def test_the_projection_reads_the_recorded_pay_at_once(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """After the Record the projection prices 03-27 at $2,100.00 and badges the change.

        The badge compares pay with pay (ruling plan#3091, amending R-SAL84):
        the entry against the $2,000.00 in force the day before its paycheck
        opens, rendered "Pay +$100.00".  Before the Record the page holds
        neither.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            projection = f"/salary/{profile.id}/projection"
            before = auth_client.get(projection).get_data(as_text=True)
            assert "2,100.00" not in before
            assert "Pay +$100.00" not in before

            _record(auth_client, profile, "2026-03-27", "2100.00")

            after = auth_client.get(projection).get_data(as_text=True)
            assert "Pay +$100.00" in after
            assert "2,100.00" in after
            assert "54,600.00" in after  # 2,100.00 x 26, the yearly column


class TestWhatItRefuses:
    """Each refusal is said in words, and writes nothing."""

    def test_a_day_that_is_not_a_payday(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """03-20 is a Friday between paydays: refused by name."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)

            response = _record(auth_client, profile, "2026-03-20", "2100.00", follow=True)

            assert b"2026-03-20 is not one of your paydays." in response.data
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_a_payday_after_the_next(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """04-10 has not been paid yet on 03-20: the pay doors' words, whole."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)

            response = _record(auth_client, profile, "2026-04-10", "2100.00", follow=True)

            assert _AFTER_THE_NEXT_PAYDAY in response.data
            assert b"A stub can be entered" not in response.data
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_a_payday_an_entry_holds(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """01-02 holds the first entry: refused with Fix's sentence; $2,000.00 stands."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)

            response = _record(auth_client, profile, "2026-01-02", "2100.00", follow=True)

            assert (
                b"Your pay from 2026-01-02 is already recorded; fix that entry "
                b"instead." in response.data
            )
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_pay_not_above_zero(self, app, auth_client, seed_user, seed_periods):
        """``0.00`` is refused by the schema's one pay bound; nothing is written."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)

            response = _record(auth_client, profile, "2026-03-27", "0.00", follow=True)

            assert b"Please correct the highlighted errors" in response.data
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_a_record_racing_another_onto_the_payday_is_reported_not_a_500(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Another tab records 03-27 between the check and the write: a flash.

        The race the door's own check cannot see: it finds 03-27 free, then
        another request's entry lands before this one's INSERT, which meets
        ``uq_pay_entries_profile_payday`` at the flush -- inside the route's
        guard.  Planted by inserting the other entry with autoflush held off,
        right after the service stages this one.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            profile_id = profile.id
            real_record = pay_list_service.record_entry

            def record_then_lose_the_race(*args, **kwargs):
                staged = real_record(*args, **kwargs)
                with db.session.no_autoflush:
                    db.session.execute(sqlalchemy.text(
                        "INSERT INTO salary.pay_entries "
                        "(salary_profile_id, payday, amount) "
                        "VALUES (:profile, '2026-03-27', 2050.00)"
                    ), {"profile": profile_id})
                return staged

            with patch.object(
                pay_list_service, "record_entry", side_effect=record_then_lose_the_race,
            ):
                response = _record(
                    auth_client, profile, "2026-03-27", "2100.00", follow=True,
                )

            assert response.status_code == 200
            assert (
                b"Your pay from 2026-03-27 is already recorded; fix that entry "
                b"instead." in response.data
            )
            # This request wrote nothing.  The planted entry shares its
            # transaction, so the guard's rollback takes it too; another
            # tab's would be its own, committed.
            assert _entries(profile_id) == [(date(2026, 1, 2), Decimal("2000.00"))]


class TestOwnership:
    """Another owner's profile is a 404 from the gate, not from the URL map."""

    def test_another_owners_profile_is_a_404_and_untouched(
        self, app, auth_client, seed_user, seed_periods,
        seed_second_user, seed_second_periods,
    ):
        """The second owner's profile: 404, its pay list unchanged; the owner's own: 302."""
        with app.app_context():
            victim = make_salary_profile(
                seed_second_user, db.session, name="Victim Job",
                pay=Decimal("3000.00"), pay_from=seed_second_periods[0].start_date,
            )
            db.session.commit()
            before = _entries(victim.id)
            mine = _profile(auth_client, seed_user, seed_periods)
            controls = _record_form(auth_client, mine)
            controls.update({"payday": "2026-03-27", "amount": "1.00"})

            assert auth_client.post(_record_url(victim), data=controls).status_code == 404
            assert _entries(victim.id) == before

            # The pair: the same URL shape resolves for the owner's own profile.
            assert auth_client.post(_record_url(mine), data=controls).status_code == 302

    def test_a_profile_that_does_not_exist_is_a_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No such profile: the same 404."""
        with app.app_context():
            response = auth_client.post("/salary/999999/pay", data={
                "amount": "1.00", "payday": "2026-03-27",
            })
            assert response.status_code == 404
