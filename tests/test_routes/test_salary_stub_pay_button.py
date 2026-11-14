"""The Pay Stub page's pay button and its door (plan step salary:X-av-4).

Ruling R-SAL61, "the stub screen offers 'Use this as my pay from this
payday'", as rulings plan#3959 (no button on a paycheck a raise starts
inside), plan#3962 (a Fix where the payday holds a pay amount) and plan#3965
(the confirm names the move) settle it.  Every POST here is what the stub's
page RENDERS, read off it by :func:`tests._salary_forms.rendered_page_form`:

* a Record from a payday wholly after a raise closes the gap, holding the
  raise and moving no paycheck before it;
* a Fix of the amount a payday holds closes the gap;
* each refusal writes nothing and says why: a raise that now starts inside
  the paycheck, a gap another tab closed, a stub changed since the page
  rendered, a Fix whose amount changed, an offer that changed kind in either
  direction, and a Record racing another onto the payday at the flush;
* another owner's stub is a 404, paired with the owner's own stub routing.

Every figure is made up.  Today is pinned to 2026-03-20, inside the seeded
schedule (biweekly Fridays from 2026-01-02): 03-13 the current payday and
03-27 the next.  Each paycheck pays Sunday through the Saturday six days
before it, so 03-13 pays 02-22..03-07 and 03-27 pays 03-08..03-21.  Each
stub is written past its own door, base pay alone, and kept off pricing so
its missing taxes price no paycheck: the button reads its base and payday.
"""

from datetime import date
from decimal import Decimal
from html import unescape
from unittest.mock import patch

import pytest
import sqlalchemy

from app.extensions import db
from app.models.pay_stub import PayStub
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services import pay_list_service, stub_pay_offer
from app.services.balance_at import BalanceContext
from app.services.payroll_basis import PayrollBasis
from tests._salary_forms import create_through_the_form, rendered_page_form
from tests._test_helpers import freeze_today, make_recurring_raise, make_salary_profile

_TODAY = date(2026, 3, 20)


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: the 03-13 payday current, 03-27 the next."""
    freeze_today(monkeypatch, _TODAY)


#: The door's words for a page left open while the pay it offered changed.
_PAY_CHANGED = (
    "Your pay was changed by another action while this page was open, so "
    "this stub was not used.  Please review it and try again."
)


@pytest.fixture(name="victim")
def _victim(seed_second_user, seed_second_periods):
    """The SECOND owner's job and a stub whose base differs from it, committed; ids only.

    $3,000.00 a paycheck, and a $3,010.00 stub on the second owner's sixth
    payday, so the stub's page would offer the button were it the owner's.
    """
    profile = make_salary_profile(
        seed_second_user, db.session, name="Victim Job",
        pay=Decimal("3000.00"), pay_from=seed_second_periods[0].start_date,
    )
    db.session.commit()
    stub = _stub(profile, seed_second_periods[5].start_date, "3010.00")
    return {"profile_id": profile.id, "stub_id": stub.id}


def _profile(client, seed_user, seed_periods):
    """The owner's job, made through the create form: $2,000.00 a paycheck from 2026-01-02."""
    return create_through_the_form(
        client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
    )


def _stub(profile, payday, base):
    """A saved stub of *base* on *payday*, kept off pricing, committed."""
    stub = PayStub(
        salary_profile_id=profile.id, payday=payday, base_pay=Decimal(base),
        use_for_pricing=False,
    )
    db.session.add(stub)
    db.session.commit()
    return stub


def _page(stub_id):
    """The stub's page URL."""
    return f"/salary/stubs/{stub_id}"


def _door(stub_id):
    """The pay button's door for the stub."""
    return f"/salary/stubs/{stub_id}/use-as-pay"


def _button_form(client, stub_id):
    """The pay button's form as the stub's page renders it."""
    return rendered_page_form(client, _page(stub_id), _door(stub_id))


def _press(client, stub_id, controls):
    """POST the rendered button form and follow it back; the page's text, unescaped."""
    response = client.post(_door(stub_id), data=controls, follow_redirects=True)
    assert response.status_code == 200
    return unescape(response.get_data(as_text=True))


def _page_text(client, stub_id):
    """The stub's page as the owner reads it, unescaped."""
    response = client.get(_page(stub_id))
    assert response.status_code == 200
    return unescape(response.get_data(as_text=True))


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


def _another_tab_records(profile_id, user_id, amount, payday):
    """Another tab's Record of *amount* from *payday*, committed."""
    profile = db.session.get(SalaryProfile, profile_id)
    pay_list_service.record_entry(
        profile, BalanceContext.build(user_id), Decimal(amount), payday, _TODAY,
    )
    db.session.commit()


def _entry_on(profile_id, payday):
    """The pay entry starting on *payday*, freshly read."""
    db.session.expire_all()
    return db.session.query(SalaryPayEntry).filter_by(
        salary_profile_id=profile_id, payday=payday,
    ).one()


class TestRecord:
    """A payday no amount starts on: the button records the stub's base from it."""

    def test_a_record_from_a_payday_wholly_after_a_raise_closes_the_gap(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 3% raise from Sunday 02-01, then a 03-13 stub of $2,061.50: recorded, and "the same".

        02-01 starts inside 02-13's paycheck (01-25..02-07), so 03-13's
        (02-22..03-07) is paid wholly at the raised pay: 2,000.00 x 1.03 =
        $2,060.00, a gap of $1.50.  Recorded from 03-13, the amount holds the
        raise (its paycheck opens after it, ruling plan#3091): 03-13 and
        03-27 pay $2,061.50, not 2,061.50 x 1.03, and 02-27, before the
        entry, still pays $2,060.00.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            user_id = seed_user["user"].id
            make_recurring_raise(
                profile.id, db.session, effective_date=date(2026, 2, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.commit()
            stub = _stub(profile, date(2026, 3, 13), "2061.50")

            before = _page_text(auth_client, stub.id)
            assert "a gap of $1.50" in before
            assert "Use $2,061.50 as my pay from 2026-03-13" in before
            assert (
                'data-confirm="Use $2,061.50 as your pay from 2026-03-13? '
                '$2,060.00 to $2,061.50, every paycheck from 2026-03-13 on."'
            ) in before
            controls = _button_form(auth_client, stub.id)
            assert set(controls) == {"csrf_token", "version_id"}

            after = _press(auth_client, stub.id, controls)

            assert "Pay recorded from 2026-03-13." in after
            assert "(the same)" in after
            assert f'action="{_door(stub.id)}"' not in after
            assert _entries(profile.id) == [
                (date(2026, 1, 2), Decimal("2000.00")),
                (date(2026, 3, 13), Decimal("2061.50")),
            ]
            assert _base_on(profile.id, user_id, date(2026, 2, 27)) == Decimal("2060.00")
            assert _base_on(profile.id, user_id, date(2026, 3, 13)) == Decimal("2061.50")
            assert _base_on(profile.id, user_id, date(2026, 3, 27)) == Decimal("2061.50")


class TestFix:
    """A payday an amount starts on: the button fixes that amount (ruling plan#3962)."""

    def test_a_fix_of_the_first_amount_closes_the_gap(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 01-02 stub of $2,000.04 against the first amount's $2,000.00: fixed, and "the same".

        The Record door would refuse 01-02, which the first amount holds, so
        the button fixes it; as the first amount it prices every paycheck,
        and 03-27 pays $2,000.04 after.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            user_id = seed_user["user"].id
            stub = _stub(profile, date(2026, 1, 2), "2000.04")
            entry = _entry_on(profile.id, date(2026, 1, 2))

            before = _page_text(auth_client, stub.id)
            assert "Fix your pay from 2026-01-02: $2,000.00 to $2,000.04" in before
            assert (
                'data-confirm="Fix your pay from 2026-01-02? $2,000.00 to '
                '$2,000.04, every paycheck from 2026-01-02 on, and every one '
                'before 2026-01-02."'
            ) in before
            controls = _button_form(auth_client, stub.id)
            assert set(controls) == {"csrf_token", "version_id", "entry_version_id"}
            assert controls["entry_version_id"] == [str(entry.version_id)]

            after = _press(auth_client, stub.id, controls)

            assert "Your pay from 2026-01-02 is fixed." in after
            assert "(the same)" in after
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.04"))]
            assert _base_on(profile.id, user_id, date(2026, 3, 27)) == Decimal("2000.04")


class TestWhatItRefuses:
    """A page left open while its offer changed writes nothing, and says why."""

    def test_a_raise_that_now_starts_inside_the_paycheck(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 03-27 Record rendered, then a 5% raise from 03-11 added: refused, and 04-10 named.

        Without the raise the app pays $2,000.00 on 03-27, so a $2,010.00
        stub offers a Record.  The raise starts inside 03-27's paycheck
        (03-08..03-21), so the press is refused in the offer's words
        (ruling plan#3959), and the page links the 04-10 stub, the first
        paid wholly at the new pay.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            stub = _stub(profile, date(2026, 3, 27), "2010.00")
            controls = _button_form(auth_client, stub.id)
            make_recurring_raise(profile.id, db.session, effective_date=date(2026, 3, 11))
            db.session.commit()

            after = _press(auth_client, stub.id, controls)

            # The words are the offer's, graded in the service's suite
            # (``tests/test_services/test_stub_pay_offer.py``); here, that the
            # door says them whole, and that they name this raise.
            refusal = stub_pay_offer.offer(
                db.session.get(SalaryProfile, profile.id),
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 27), Decimal("2010.00"), _TODAY,
            )
            assert refusal.button is False
            assert refusal.reason.startswith("Your Merit +5% raise from 2026-03-11 starts")
            assert refusal.reason in after
            assert f'action="{_door(stub.id)}"' not in after
            assert (
                f'href="/salary/{profile.id}/stubs/new?payday=2026-04-10">'
                f"Open your 2026-04-10 stub</a>"
            ) in after
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_a_gap_another_tab_closed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 03-13 Record rendered, then another tab records $2,010.00 there: nothing more."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            stub = _stub(profile, date(2026, 3, 13), "2010.00")
            controls = _button_form(auth_client, stub.id)
            _another_tab_records(profile.id, seed_user["user"].id, "2010.00", date(2026, 3, 13))

            after = _press(auth_client, stub.id, controls)

            assert (
                "Your pay was not changed: the app holds no paycheck on "
                "2026-03-13 that differs from this stub."
            ) in after
            assert _entries(profile.id) == [
                (date(2026, 1, 2), Decimal("2000.00")),
                (date(2026, 3, 13), Decimal("2010.00")),
            ]

    def test_a_stub_changed_since_the_page_rendered(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $2,010.00 stub's button rendered, then the stub re-saved at $2,020.00: refused.

        The base the door would write is the stub's as it stands, $2,020.00,
        which the owner never saw on the button; the stub's version is what
        the page posted, and it moved.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            stub = _stub(profile, date(2026, 3, 13), "2010.00")
            controls = _button_form(auth_client, stub.id)
            stub.base_pay = Decimal("2020.00")
            db.session.commit()

            after = _press(auth_client, stub.id, controls)

            assert (
                "This pay stub was changed by another action, so your pay was "
                "not changed.  Please review it and try again."
            ) in after
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_a_fix_whose_amount_another_tab_changed(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 01-02 Fix rendered against $2,000.00, then fixed to $2,000.02 elsewhere: refused.

        The confirm named $2,000.00 as the figure replaced; the entry's
        version moved, and $2,000.02 stands.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            stub = _stub(profile, date(2026, 1, 2), "2000.04")
            controls = _button_form(auth_client, stub.id)
            entry = _entry_on(profile.id, date(2026, 1, 2))
            pay_list_service.fix_entry(
                entry, BalanceContext.build(seed_user["user"].id),
                Decimal("2000.02"), entry.payday, _TODAY,
            )
            db.session.commit()

            after = _press(auth_client, stub.id, controls)

            assert _PAY_CHANGED in after
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.02"))]

    def test_a_record_that_is_now_a_fix(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 03-13 Record rendered, then another tab records $2,005.00 there: refused.

        Asked again, the offer is a Fix of the other tab's amount, which the
        owner never confirmed; the page posted no amount's version, and
        $2,005.00 stands.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            stub = _stub(profile, date(2026, 3, 13), "2010.00")
            controls = _button_form(auth_client, stub.id)
            _another_tab_records(profile.id, seed_user["user"].id, "2005.00", date(2026, 3, 13))

            after = _press(auth_client, stub.id, controls)

            assert _PAY_CHANGED in after
            assert _entries(profile.id) == [
                (date(2026, 1, 2), Decimal("2000.00")),
                (date(2026, 3, 13), Decimal("2005.00")),
            ]

    def test_a_fix_that_is_now_a_record(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A Fix of the 03-13 amount rendered, then that amount removed: refused, nothing recorded.

        Asked again, the offer is a Record of a new amount, which the owner
        never confirmed; the page posted the removed amount's version.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            _another_tab_records(profile.id, seed_user["user"].id, "2005.00", date(2026, 3, 13))
            stub = _stub(profile, date(2026, 3, 13), "2010.00")
            controls = _button_form(auth_client, stub.id)
            assert "entry_version_id" in controls
            pay_list_service.remove_entry(_entry_on(profile.id, date(2026, 3, 13)))
            db.session.commit()

            after = _press(auth_client, stub.id, controls)

            assert _PAY_CHANGED in after
            assert _entries(profile.id) == [(date(2026, 1, 2), Decimal("2000.00"))]

    def test_a_record_racing_another_onto_the_payday_is_reported_not_a_500(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Another tab records 03-13 between the offer and the write: the held payday's sentence.

        The race the offer cannot see: it finds 03-13 free, then another
        request's entry lands before this one's INSERT, which meets
        ``uq_pay_entries_profile_payday`` at the flush inside the door's
        guard.  Planted by inserting the other entry with autoflush held off,
        right after the service stages this one.
        """
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            profile_id = profile.id
            stub = _stub(profile, date(2026, 3, 13), "2010.00")
            controls = _button_form(auth_client, stub.id)
            real_record = pay_list_service.record_entry

            def record_then_lose_the_race(*args, **kwargs):
                staged = real_record(*args, **kwargs)
                with db.session.no_autoflush:
                    db.session.execute(sqlalchemy.text(
                        "INSERT INTO salary.pay_entries "
                        "(salary_profile_id, payday, amount) "
                        "VALUES (:profile, '2026-03-13', 2050.00)"
                    ), {"profile": profile_id})
                return staged

            with patch.object(
                pay_list_service, "record_entry", side_effect=record_then_lose_the_race,
            ):
                after = _press(auth_client, stub.id, controls)

            assert (
                "Your pay from 2026-03-13 is already recorded; fix that entry "
                "instead."
            ) in after
            # This request wrote nothing.  The planted entry shares its
            # transaction, so the guard's rollback takes it too.
            assert _entries(profile_id) == [(date(2026, 1, 2), Decimal("2000.00"))]


class TestThePage:
    """The page offers the button only where there is a gap to close."""

    def test_a_stub_at_the_apps_figure_renders_no_button(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A 03-13 stub of $2,000.00: "the same", and no form posts to the door."""
        with app.app_context():
            profile = _profile(auth_client, seed_user, seed_periods)
            stub = _stub(profile, date(2026, 3, 13), "2000.00")

            page = _page_text(auth_client, stub.id)

            assert "(the same)" in page
            assert f'action="{_door(stub.id)}"' not in page


class TestOwnership:
    """Another owner's stub is a 404 from the gate, not from the URL map."""

    def test_another_owners_stub_is_a_404_and_writes_nothing(
        self, app, auth_client, seed_user, seed_periods, victim,
    ):
        """The second owner's differing stub: 404, their pay list unchanged; the owner's: 302."""
        with app.app_context():
            before = _entries(victim["profile_id"])
            mine = _profile(auth_client, seed_user, seed_periods)
            my_stub = _stub(mine, date(2026, 3, 13), "2010.00")
            controls = _button_form(auth_client, my_stub.id)

            assert auth_client.post(_door(victim["stub_id"]), data=controls).status_code == 404
            assert _entries(victim["profile_id"]) == before

            # The pair: the same URL shape resolves for the owner's own stub.
            assert auth_client.post(_door(my_stub.id), data=controls).status_code == 302

    def test_a_stub_that_does_not_exist_is_a_404(self, app, auth_client):
        """No such stub: the same 404."""
        with app.app_context():
            response = auth_client.post(_door(999999), data={"version_id": "1"})
            assert response.status_code == 404
