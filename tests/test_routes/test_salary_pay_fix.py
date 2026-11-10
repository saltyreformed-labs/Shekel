"""The salary's pay list doors: create by pay per paycheck, and Fix (plan step salary:X-av-3a).

Rulings **R-SAL59** (the stored fact is what one paycheck pays from a dated
payday on) and **R-SAL61** ("'Fix' edits one": an entry's amount, its payday,
or both).  Every POST here is what the page RENDERS, read off the page by
the shared :func:`tests._form_controls.rendered_form_controls` (this file's
own reader until plan step salary:X-av-3b, finding SAL-593) -- a hand-written
payload is written by the same author as the template and agrees with it
about a mistake as readily as about the truth.  Every figure is made up.

* create: the form's pay a paycheck and its payday become the first entry;
* Fix: the amount, the payday, both; a payday that is not one, or that another
  entry holds, is refused and nothing moves; an unchanged payday is not asked
  again (R-SAL53's shape); a stale form is refused;
* ownership: another owner's entry is a 404, paired with the owner's own POST
  to the same URL shape resolving, so the 404 is the gate's and not the URL
  map's;
* both doors take the stub door's payday rule (R-SAL48's bound, ruling
  **R-SAL90**, "Up to next payday"): a payday later than the owner's next
  one is refused, in the pay doors' own words (ruling **R-SAL93**, "Each
  door names its own"): "Pay can be recorded up to your next payday, ...",
  never the stub door's "A stub can be entered ...".

Today is pinned to 2026-03-20 (the stub tests' day): inside the seeded
schedule (biweekly from 2026-01-02), 03-13 the current payday and 03-27 the
next.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy

from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services import pay_era_write, pay_list_service
from tests._salary_forms import create_through_the_form, rendered_page_form
from tests._stub_period import stub_period_on
from tests._test_helpers import era_of, freeze_today, start_test_pay_list


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: the 03-13 payday current, 03-27 the next."""
    freeze_today(monkeypatch, date(2026, 3, 20))


def _fix_url(entry):
    """The Fix door's URL for *entry*."""
    return f"/salary/pay/{entry.id}/fix"


def _fix_form(client, entry):
    """The Fix form of *entry* as the salary page renders it."""
    return rendered_page_form(
        client, f"/salary/{entry.salary_profile_id}/edit", _fix_url(entry),
    )


def _state(entry_id):
    """Return ``(payday, amount, version)`` of the entry as the database holds it."""
    db.session.expire_all()
    entry = db.session.get(SalaryPayEntry, entry_id)
    return entry.payday, entry.amount, entry.version_id


class TestCreateByPayPerPaycheck:
    """The create form's two pay fields are the profile's first entry."""

    def test_the_form_writes_the_first_entry(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``$2,000.00`` from 2026-01-16's payday: one entry, exactly that."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[1].start_date.isoformat(),
            )
            assert [
                (entry.payday, entry.amount) for entry in profile.pay_entries
            ] == [(seed_periods[1].start_date, Decimal("2000.00"))]

    def test_the_payday_defaults_to_the_current_payday(
        self, app, auth_client, seed_periods,
    ):
        """On 03-20 the rendered payday box holds 03-13, the current payday (R-SAL90).

        R-SAL90's picked text: the first entry's "payday defaults to your
        current one" -- not an empty box, and not the first saved payday, which
        would date pay typed today before this year's raises and apply them
        again on top.
        """
        with app.app_context():
            controls = rendered_page_form(auth_client, "/salary/new", "/salary")
            assert date(2026, 3, 13) in {period.start_date for period in seed_periods}
            assert controls["pay_payday"] == ["2026-03-13"]
            assert "pay_amount" in controls

    def test_a_day_that_is_not_a_payday_creates_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """2026-01-03 is the day after a payday: refused with the stub door's words."""
        with app.app_context():
            controls = rendered_page_form(auth_client, "/salary/new", "/salary")
            controls.update({
                "name": "Day Job", "pay_amount": "2000.00",
                "pay_payday": "2026-01-03", "state_code": "NC",
                # A payday, so the pay box's refusal is the one graded.
                **stub_period_on("2026-01-02"),
            })
            response = auth_client.post(
                "/salary", data=controls, follow_redirects=True,
            )
            assert b"2026-01-03 is not one of your paydays." in response.data
            assert db.session.query(SalaryProfile).filter_by(
                user_id=seed_user["user"].id,
            ).count() == 0


class TestUpToTheNextPayday:
    """Neither door takes a payday past the owner's next one (R-SAL48's bound).

    Each refusal is pinned WHOLE, in the pay doors' words (R-SAL93): the rule
    is the stub door's, the words are not.
    """

    #: What both pay doors say of 04-10 on 03-20 (R-SAL93, "Each door names its own").
    REFUSAL = (
        b"2026-04-10 has not been paid yet.  Pay can be recorded up to your "
        b"next payday, 2026-03-27."
    )

    def test_create_refuses_a_payday_after_the_next(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """04-10 has not been paid yet on 03-20: the pay doors' words; no profile."""
        with app.app_context():
            controls = rendered_page_form(auth_client, "/salary/new", "/salary")
            controls.update({
                "name": "Day Job", "pay_amount": "2000.00",
                "pay_payday": "2026-04-10", "state_code": "NC",
                # A paid payday, so the pay box's refusal is the one graded.
                **stub_period_on("2026-01-02"),
            })
            response = auth_client.post(
                "/salary", data=controls, follow_redirects=True,
            )
            assert self.REFUSAL in response.data
            assert b"A stub can be entered" not in response.data
            assert db.session.query(SalaryProfile).filter_by(
                user_id=seed_user["user"].id,
            ).count() == 0

    def test_create_takes_the_next_payday(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """03-27 is the next payday: the first entry is written on it."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", "2026-03-27",
            )
            assert [entry.payday for entry in profile.pay_entries] == [
                date(2026, 3, 27),
            ]

    def test_fix_refuses_a_payday_after_the_next(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A Fix to 04-10 on 03-20 is refused; the entry is untouched."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            before = _state(entry.id)
            controls = _fix_form(auth_client, entry)
            controls["payday"] = "2026-04-10"

            response = auth_client.post(
                _fix_url(entry), data=controls, follow_redirects=True,
            )

            assert self.REFUSAL in response.data
            assert b"A stub can be entered" not in response.data
            assert _state(entry.id) == before


class TestFix:
    """Fix corrects one entry; what it refuses moves nothing."""

    def test_fix_the_amount(self, app, auth_client, seed_user, seed_periods):
        """``$2,000.00`` -> ``$2,070.00``; the page shows the new yearly ``$53,820.00`` (x 26)."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            controls = _fix_form(auth_client, entry)
            controls["amount"] = "2070.00"

            response = auth_client.post(_fix_url(entry), data=controls)

            assert response.status_code == 302
            assert _state(entry.id)[:2] == (
                seed_periods[0].start_date, Decimal("2070.00"),
            )
            page = auth_client.get(f"/salary/{profile.id}/edit")
            assert b"53,820.00" in page.data

    def test_fix_the_payday(self, app, auth_client, seed_user, seed_periods):
        """Moved to the third payday: the amount stays, the day moves."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            controls = _fix_form(auth_client, entry)
            controls["payday"] = seed_periods[2].start_date.isoformat()

            auth_client.post(_fix_url(entry), data=controls)

            assert _state(entry.id)[:2] == (
                seed_periods[2].start_date, Decimal("2000.00"),
            )

    def test_a_payday_that_is_not_one_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """2026-01-03: the stub door's words, and the entry is untouched."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            before = _state(entry.id)
            controls = _fix_form(auth_client, entry)
            controls.update({"payday": "2026-01-03", "amount": "2070.00"})

            response = auth_client.post(
                _fix_url(entry), data=controls, follow_redirects=True,
            )

            assert b"2026-01-03 is not one of your paydays." in response.data
            assert _state(entry.id) == before

    def test_a_payday_another_entry_holds_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two entries; moving the second onto the first's payday is refused by name."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            later = SalaryPayEntry(
                payday=seed_periods[3].start_date, amount=Decimal("2070.00"),
            )
            profile.pay_entries.append(later)
            db.session.commit()
            before = _state(later.id)
            controls = _fix_form(auth_client, later)
            controls["payday"] = seed_periods[0].start_date.isoformat()

            response = auth_client.post(
                _fix_url(later), data=controls, follow_redirects=True,
            )

            assert (
                f"Your pay from {seed_periods[0].start_date.isoformat()} is "
                "already recorded; fix that entry instead."
            ).encode() in response.data
            assert _state(later.id) == before

    def test_an_unchanged_payday_is_not_asked_again(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An entry whose payday is no longer a payday stays fixable in place.

        The state is planted around the door (the calendar moved after the
        entry was written); a Fix of the amount alone must not be refused for
        a payday it does not change.
        """
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            entry.payday = date(2026, 1, 3)
            db.session.commit()
            controls = _fix_form(auth_client, entry)
            assert controls["payday"] == ["2026-01-03"]
            controls["amount"] = "2070.00"

            auth_client.post(_fix_url(entry), data=controls)

            assert _state(entry.id)[:2] == (date(2026, 1, 3), Decimal("2070.00"))

    def test_a_stale_form_is_refused(self, app, auth_client, seed_user, seed_periods):
        """A form rendered before another tab's Fix is refused; that Fix stands."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            stale = _fix_form(auth_client, entry)
            fresh = dict(stale, amount="2070.00")
            auth_client.post(_fix_url(entry), data=fresh)
            after_the_first = _state(entry.id)

            stale["amount"] = "1990.00"
            response = auth_client.post(
                _fix_url(entry), data=stale, follow_redirects=True,
            )

            assert b"changed by another action" in response.data
            assert _state(entry.id) == after_the_first
            assert after_the_first[1] == Decimal("2070.00")

    def test_a_concurrent_fix_is_reported_not_a_500(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Another tab's Fix lands between the version check and the write: a flash.

        The race the form's version pin cannot see: the pre-check passes, then
        the row's version moves before the UPDATE runs.  The UPDATE is pinned
        to the version read, matches no row, and raises ``StaleDataError`` at
        the flush -- which must run inside the route's guard.  Planted by
        bumping the version with autoflush held off, right after the service
        stages its change.
        """
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            entry_id = entry.id
            controls = _fix_form(auth_client, entry)
            controls["amount"] = "2070.00"
            real_fix = pay_list_service.fix_entry

            def fix_then_lose_the_race(*args, **kwargs):
                staged = real_fix(*args, **kwargs)
                with db.session.no_autoflush:
                    db.session.execute(sqlalchemy.text(
                        "UPDATE salary.pay_entries SET version_id = version_id + 1 "
                        "WHERE id = :id"
                    ), {"id": entry_id})
                return staged

            with patch.object(
                pay_list_service, "fix_entry", side_effect=fix_then_lose_the_race,
            ):
                response = auth_client.post(
                    _fix_url(entry), data=controls, follow_redirects=True,
                )

            assert response.status_code == 200
            assert b"changed by another action" in response.data
            assert _state(entry_id)[1] == Decimal("2000.00")

    def test_an_amount_not_above_zero_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``0.00`` is refused by the schema; nothing moves."""
        with app.app_context():
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            )
            entry = profile.pay_entries[0]
            before = _state(entry.id)
            controls = _fix_form(auth_client, entry)
            controls["amount"] = "0.00"

            response = auth_client.post(
                _fix_url(entry), data=controls, follow_redirects=True,
            )

            assert b"Please correct the highlighted errors" in response.data
            assert _state(entry.id) == before


class TestOwnership:
    """Another owner's entry is a 404 from the gate, not from the URL map."""

    def test_another_owners_entry_is_a_404_and_untouched(
        self, app, auth_client, seed_user, seed_periods,
        seed_second_user, seed_second_periods,
    ):
        """The second owner's entry: 404, unchanged; the first owner's own: 302."""
        with app.app_context():
            victim = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_second_user["user"].id,
                scenario_id=seed_second_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                name="Victim Job",
            )
            db.session.add(victim)
            theirs = start_test_pay_list(
                victim, Decimal("3000.00"), seed_second_periods[0].start_date,
            )
            db.session.commit()
            before = _state(theirs.id)
            mine = create_through_the_form(
                auth_client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
            ).pay_entries[0]
            controls = _fix_form(auth_client, mine)

            hostile = dict(
                controls, amount="1.00", version_id=str(theirs.version_id),
                payday=seed_second_periods[0].start_date.isoformat(),
            )
            assert auth_client.post(_fix_url(theirs), data=hostile).status_code == 404
            assert _state(theirs.id) == before

            # The pair: the same URL shape resolves for the owner's own entry.
            assert auth_client.post(_fix_url(mine), data=controls).status_code == 302

    def test_an_entry_that_does_not_exist_is_a_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No such entry: the same 404."""
        with app.app_context():
            response = auth_client.post("/salary/pay/999999/fix", data={
                "amount": "1.00", "payday": "2026-01-02", "version_id": "1",
            })
            assert response.status_code == 404


class TestACutReadsAsACut:
    """A recorded pay cut renders amber with a down arrow; an increase stays green (R-SAL85).

    The second entry is recorded through the Record door's rendered form
    (plan step salary:X-av-3b; planted through the model until that door
    existed).  Made-up: $2,000.00 from 2026-01-02, then $1,930.00 (a cut) or
    $2,070.00 (an increase) from 2026-02-13, the fourth seeded payday.
    """

    def _profile_changing_to(self, client, seed_user, seed_periods, amount):
        """The profile, its first entry through the create form, the second through Record."""
        profile = create_through_the_form(
            client, seed_user, "2000.00", seed_periods[0].start_date.isoformat(),
        )
        record = f"/salary/{profile.id}/pay"
        controls = rendered_page_form(client, f"/salary/{profile.id}/edit", record)
        controls.update({
            "payday": seed_periods[3].start_date.isoformat(), "amount": amount,
        })
        assert client.post(record, data=controls).status_code == 302
        db.session.expire_all()
        assert [entry.amount for entry in profile.pay_entries] == [
            Decimal("2000.00"), Decimal(amount),
        ]
        return profile

    def test_the_projection_row_and_badge_of_a_cut_are_amber_with_a_down_arrow(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """02-13's row: ``sal-row-cut``, and its badge ``sal-badge-cut`` with the arrow."""
        with app.app_context():
            profile = self._profile_changing_to(
                auth_client, seed_user, seed_periods, "1930.00",
            )
            page = auth_client.get(f"/salary/{profile.id}/projection").get_data(
                as_text=True,
            )
            assert 'class="sal-row-cut"' in page
            assert (
                '<span class="sal-badge-cut"><i class="bi bi-arrow-down" '
                'aria-hidden="true"></i> Pay -$70.00</span>'
            ) in page
            assert "sal-row-raise" not in page

    def test_an_increase_keeps_the_green_row_and_no_arrow(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$2,070.00: ``sal-row-raise``, ``sal-badge-raise``, 'Pay +$70.00', no cut class."""
        with app.app_context():
            profile = self._profile_changing_to(
                auth_client, seed_user, seed_periods, "2070.00",
            )
            page = auth_client.get(f"/salary/{profile.id}/projection").get_data(
                as_text=True,
            )
            assert 'class="sal-row-raise"' in page
            assert '<span class="sal-badge-raise">Pay +$70.00</span>' in page
            assert "sal-row-cut" not in page
            assert "sal-badge-cut" not in page

    def test_the_cockpit_banner_of_a_cut_says_pay_cut(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The 02-13 paycheck's anatomy: the amber 'Pay cut:' banner, not 'Raise:'."""
        with app.app_context():
            profile = self._profile_changing_to(
                auth_client, seed_user, seed_periods, "1930.00",
            )
            response = auth_client.get(
                f"/salary?profile={profile.id}&period={seed_periods[3].id}",
            )
            assert response.status_code == 200
            page = response.get_data(as_text=True)
            assert "<strong>Pay cut:</strong> Pay -$70.00" in page
            assert "<strong>Raise:</strong>" not in page


class TestTheRhythmNotice:
    """The salary page names a change of rhythm no pay is recorded from (R-SAL82).

    "Yearly pay carries, said": the ruled sentence is the page's, so it is
    graded as the page renders it, not only as
    ``PayrollBasis.rhythm_changes_without_pay`` returns it (an adversarial
    review of this step removed the notice from the template and every test
    passed).  Made-up: $2,000.00 a paycheck every two weeks from today's
    payday, and a weekly rhythm recorded four weeks after the saved record.
    """

    def test_the_page_says_the_carried_pay_and_its_yearly_figure(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """2,000.00 x 26 / 52 = 1,000.00 weekly, from the yearly 52,000.00."""
        with app.app_context():
            switch = max(period.start_date for period in seed_periods) + timedelta(days=28)
            pay_era_write.mint_era(seed_user["user"].id, era_of(switch, 7))
            db.session.commit()
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", "2026-03-13",
            )

            page = auth_client.get(f"/salary/{profile.id}/edit")

            assert page.status_code == 200
            text = " ".join(page.get_data(as_text=True).split())
            assert (
                f"Your pay rhythm changes on {switch.isoformat()} and no pay is "
                "recorded from then: priced at $1,000.00 from your yearly "
                "$52,000.00."
            ) in text

    def test_pay_recorded_from_the_switch_is_not_named(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An entry on the switch's payday prices it: the page names no carry.

        The second entry is planted through the model, as
        :class:`TestACutReadsAsACut`'s is: no door of this leaf records one.
        """
        with app.app_context():
            switch = max(period.start_date for period in seed_periods) + timedelta(days=28)
            pay_era_write.mint_era(seed_user["user"].id, era_of(switch, 7))
            db.session.commit()
            profile = create_through_the_form(
                auth_client, seed_user, "2000.00", "2026-03-13",
            )
            profile.pay_entries.append(SalaryPayEntry(
                payday=switch, amount=Decimal("1000.00"),
            ))
            db.session.commit()

            page = auth_client.get(f"/salary/{profile.id}/edit")

            assert page.status_code == 200
            assert "Your pay rhythm changes on" not in page.get_data(as_text=True)
