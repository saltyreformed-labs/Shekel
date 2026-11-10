"""
Shekel Budget App -- the pay stub entry door's routes (plan step salary:S11-b).

The five routes of :mod:`app.routes.salary.stubs` and the paycheck-line delete
refusal, driven through the test client:

* the entry flow picks the payday first (ruling **R-SAL50**): a non-payday is
  refused at step 1, a held payday opens its stub, a free one renders the form
  with the lines the app takes that payday first;
* a record, an edit and the "Use for pricing" switch each write through the
  service and redirect; a refusal re-renders the form with every figure the
  owner typed (a 422), and writes nothing;
* every door is the owner's: another owner's profile or stub is a 404 -- each
  paired with the owner's own request to the SAME ROUTE succeeding, so a 404
  from the URL map cannot pass for the ownership gate.  The attacker is
  ``auth_client`` against the second owner's rows, the shape the suite's own
  IDOR cases use (``test_savings``' ``test_goal_idor_view_blocked``);
* the form POSTED here is the form the template EMITS: its field names are
  read off the rendered page and compared with the payload's;
* deleting a paycheck line a stub names is refused with **R-SAL51** (c)'s
  wording, and the line stays;
* the job's answer to "my stub's gross includes after-tax earnings"
  (**R-SAL102**) reaches the door, the line under the gross box says what
  this job's gross counts, a refusal naming the setting ends in a link to it
  (**R-SAL104**, **R-SAL106**), and both links open a new tab (**R-SAL107**)
  -- changing the setting there leaves the open stub form submittable.  The
  words are **R-SAL114** to **R-SAL117**'s (the approved list, **R-SAL115**);
* a saved stub's page says a one-off can affect its taxes exactly when the
  engine's picker treats it as one that does (**R-SAL123**), in **R-SAL126**'s
  words.

The figures are the service suite's worked example
(``tests/test_services/test_pay_stub_service.py``): the 03-27 stub prints a
gross of ``$2,999.62`` and nets ``$2,102.62``.  Today is frozen at 2026-03-20,
so 03-27 is the next payday.
"""

import re
from datetime import date
from decimal import Decimal
from html import unescape

import pytest

from app import ref_cache
from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.extensions import db
from app.models.pay_stub import PayStub
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.services import pay_stub_service
from app.services.balance_at import BalanceContext
from tests._form_controls import rendered_form_controls
from tests._test_helpers import (
    build_pay_stub_world,
    freeze_today,
    make_flat_paycheck_line,
    make_income_template,
    make_salary_profile,
)

_PAYDAY = "2026-03-27"


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: inside the seeded schedule, the 03-27 payday next."""
    freeze_today(monkeypatch, date(2026, 3, 20))


@pytest.fixture(name="world")
def _world(seed_user, seed_periods):  # pylint: disable=unused-argument
    """The worked example's profile and lines, committed; ids only.

    Pylint: ``unused-argument`` -- ``seed_periods`` is requested for the pay
    schedule it writes, which the Dental line's rule and every payday need.
    """
    profile, lines = build_pay_stub_world(seed_user)
    return {
        "profile_id": profile.id,
        "lines": {key: line.id for key, line in lines.items()},
        "kinds": {key: line.paycheck_line_kind_id for key, line in lines.items()},
    }


@pytest.fixture(name="victim")
def _victim(seed_second_user):
    """The SECOND owner's profile, one line and one stub, written past the door."""
    profile = make_salary_profile(seed_second_user, db.session, name="Victim Job")
    db.session.flush()
    make_flat_paycheck_line(
        profile, "Victim Line", "10.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    )
    stub = PayStub(salary_profile_id=profile.id, payday=date(2026, 3, 27),
                   base_pay=Decimal("1000.00"))
    db.session.add(stub)
    db.session.commit()
    return {"profile_id": profile.id, "stub_id": stub.id}


def _tax_field(member):
    """The form field of one tax."""
    return f"tax-{ref_cache.withholding_kind_id(member)}"


def _payload(world, *, payday=_PAYDAY, printed_net="2102.62", printed_gross="2999.62",
             roth="110.00", one_off=("Retro pay", "55.00")):
    """The worked example as the form posts it: every field the template emits.

    Vision is left blank (the stub does not print it).  Every line posts its
    kind select at the line's own kind, as the form pre-sets it (ruling
    R-SAL58; :func:`_printed_under` moves one).  A new stub's form renders two
    one-off rows; the first carries the one-off and the second is posted
    blank, as a browser posts it.
    """
    lines = world["lines"]
    taxable = str(ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.TAXABLE_EARNING))
    return {
        "payday": payday,
        "base_pay": "2884.62",
        f"line-{lines['health']}": "280.00",
        f"line-{lines['vision']}": "",
        f"line-{lines['dental']}": "35.00",
        f"line-{lines['roth']}": roth,
        f"line-{lines['phone']}": "60.00",
        **{f"line-kind-{lines[key]}": str(world["kinds"][key]) for key in lines},
        _tax_field(WithholdingKindEnum.FEDERAL_INCOME): "150.00",
        _tax_field(WithholdingKindEnum.STATE_INCOME): "100.00",
        _tax_field(WithholdingKindEnum.SOCIAL_SECURITY): "180.00",
        _tax_field(WithholdingKindEnum.MEDICARE): "42.00",
        "one_off_name": [one_off[0], ""],
        "one_off_kind": [taxable, ""],
        "one_off_amount": [one_off[1], ""],
        "notes": "",
        "printed_gross": printed_gross,
        "printed_net": printed_net,
    }


def _printed_under(world: dict, payload: dict, **kinds) -> dict:
    """*payload* with each named line's kind select on another heading (ruling R-SAL58).

    Args:
        world: The worked example's world.
        payload: A :func:`_payload`.
        **kinds: ``line key=PaycheckLineKindEnum`` for each line the stub
            prints under a heading other than the line's own.
    """
    return {
        **payload,
        **{
            f"line-kind-{world['lines'][key]}": str(ref_cache.paycheck_line_kind_id(member))
            for key, member in kinds.items()
        },
    }


def _record(client, world, **overrides):
    """POST the record door; return the response."""
    return client.post(
        f"/salary/{world['profile_id']}/stubs", data=_payload(world, **overrides),
    )


def _stub():
    """The one stub, freshly read."""
    db.session.expire_all()
    return db.session.query(PayStub).one()


def _stub_on(day):
    """The stub dated *day*, freshly read."""
    db.session.expire_all()
    return db.session.query(PayStub).filter_by(payday=day).one()


def _field_names(html):
    """Every form control name the stub form renders, CSRF excepted."""
    start = html.index('<form method="POST"\n          action="/salary/')
    form = html[start:html.index("</form>", start)]
    return set(re.findall(r'name="([^"]+)"', form)) - {"csrf_token"}


class TestTheEntryFlow:
    """R-SAL50: the payday first, then the form for that payday."""

    def test_step_one_asks_for_the_payday(self, auth_client, world):
        """No payday in the query: the date step."""
        response = auth_client.get(f"/salary/{world['profile_id']}/stubs/new")
        assert response.status_code == 200
        assert b"Which payday is the stub for?" in response.data

    def test_a_day_that_is_not_a_payday_is_refused_at_step_one(self, auth_client, world):
        """03-20 is inside a paycheck: step 1 again, with the reason, as a 422."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday=2026-03-20",
        )
        assert response.status_code == 422
        assert b"2026-03-20 is not one of your paydays." in response.data

    def test_a_payday_after_the_next_is_refused_at_step_one(self, auth_client, world):
        """04-10 has not been paid yet on 03-20 (R-SAL48)."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday=2026-04-10",
        )
        assert response.status_code == 422
        assert b"2026-04-10 has not been paid yet." in response.data

    def test_a_free_payday_lists_the_lines_the_app_takes_first(self, auth_client, world):
        """03-27: Dental (a month's first paycheck only) is under the other lines."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        assert response.status_code == 200
        html = response.data.decode()
        taken, others = html.split("Your other paycheck lines")
        assert "Health Insurance" in taken and "Dental" not in taken
        assert "Dental" in others

    def test_the_payload_is_the_form_the_template_emits(self, auth_client, world):
        """Every name the rendered form carries, and no other, is posted."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        assert _field_names(response.data.decode()) == set(_payload(world))

    def test_a_held_payday_the_record_no_longer_holds_still_opens(
        self, auth_client, world,
    ):
        """A stub dated 03-20 (written past the door) opens instead of being refused."""
        stub = PayStub(salary_profile_id=world["profile_id"], payday=date(2026, 3, 20),
                       base_pay=Decimal("2884.62"))
        db.session.add(stub)
        db.session.commit()
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday=2026-03-20",
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/salary/stubs/{stub.id}")

    def test_a_held_payday_opens_its_stub(self, auth_client, world):
        """Entering 03-27 again goes to the saved stub (fork 4)."""
        assert _record(auth_client, world).status_code == 302
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/salary/stubs/{_stub().id}")


class TestRecording:
    """The record door: written on a clean form, refused whole on a bad one."""

    def test_a_clean_form_records_the_stub_and_shows_its_report(self, auth_client, world):
        """Saved, redirected to its page, which reports three disagreeing lines."""
        response = _record(auth_client, world)
        assert response.status_code == 302
        stub = _stub()
        assert stub.payday == date(2026, 3, 27)
        assert stub.salary_profile_id == world["profile_id"]
        assert len(stub.line_amounts) == 4
        assert [o.name for o in stub.one_offs] == ["Retro pay"]
        page = auth_client.get(response.headers["Location"])
        assert page.status_code == 200
        assert b"$2,102.62" in page.data
        assert b"3 paycheck lines" in page.data

    def test_a_wrong_printed_net_rerenders_the_typed_form_and_writes_nothing(
        self, auth_client, world,
    ):
        """$2,102.63 is refused as a 422 that keeps every figure typed."""
        response = _record(auth_client, world, printed_net="2102.63")
        assert response.status_code == 422
        assert b"The lines add up to $2,102.62, but the stub prints $2,102.63" in response.data
        assert b'value="110.00"' in response.data
        assert b'value="Retro pay"' in response.data
        assert db.session.query(PayStub).count() == 0

    def test_a_gross_typed_as_base_pay_is_refused_with_its_reason(self, auth_client, world):
        """R-SAL99 (SAL-590): the printed gross in the Base pay box is named, nothing written.

        Base pay 2999.62 + Phone 60.00 + Retro pay 55.00 = 3114.62 against the
        printed 2999.62: the $115.00 of earnings is counted twice, and asked
        about with its twin, an earning the stub does not print (R-SAL111), by
        the base pay the stub shows (R-SAL112) once both typed totals are
        checked (R-SAL120; worded by R-SAL114 and R-SAL117).  It names no
        setting, so the page draws no link to it: the line under the gross box
        holds the page's only one (delta review LOW-1).
        """
        payload = _payload(world)
        payload["base_pay"] = "2999.62"
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 422
        html = response.data.decode()
        assert (
            "Base pay plus your taxable earnings come to $3,114.62, but the stub's Gross "
            "Pay is $2,999.62.  Check the Gross Pay and Net Pay you typed.  If they're "
            "right, what base pay does the stub show?  $2,884.62: you typed the Gross Pay "
            "into Base pay.  Type $2,884.62 there instead.  $2,999.62: you entered extra "
            "pay this stub doesn't list.  Remove it."
        ) in unescape(html)
        assert html.count(
            f'href="/salary/{world["profile_id"]}/edit#stub_gross_includes_after_tax"',
        ) == 1
        gross_input = html[html.index('name="printed_gross"'):]
        gross_input = gross_input[:gross_input.index(">")]
        assert "is-invalid" in gross_input
        assert 'value="2999.62"' in gross_input
        base_input = html[html.index('name="base_pay"'):]
        assert 'value="2999.62"' in base_input[:base_input.index(">")]
        assert db.session.query(PayStub).count() == 0

    def test_a_form_without_its_printed_gross_is_refused_on_that_field(
        self, auth_client, world,
    ):
        """The box is required: a post without it is a 422 naming it, never a 500."""
        payload = _payload(world)
        del payload["printed_gross"]
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 422
        html = response.data.decode()
        gross_input = html[html.index('name="printed_gross"'):]
        assert "is-invalid" in gross_input[:gross_input.index(">")]
        assert db.session.query(PayStub).count() == 0

    def test_a_missing_tax_is_refused(self, auth_client, world):
        """Every tax is required ($0.00 allowed)."""
        payload = _payload(world)
        payload[_tax_field(WithholdingKindEnum.MEDICARE)] = ""
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 422
        assert b"Enter the stub&#39;s Medicare ($0.00 if none)." in response.data
        assert db.session.query(PayStub).count() == 0

    def test_a_one_off_named_like_a_line_is_refused_on_its_row(self, auth_client, world):
        """'health insurance' is refused, and the message sits in that row."""
        response = _record(auth_client, world, one_off=("health insurance", "0.00"),
                           printed_net="2047.62", printed_gross="2944.62")
        assert response.status_code == 422
        html = response.data.decode()
        row = html.index('value="health insurance"')
        message = html.index("enter it on the line instead.")
        next_row = html.index('name="one_off_name"', row)
        assert row < message < next_row
        assert db.session.query(PayStub).count() == 0

    def test_a_held_payday_is_refused_with_a_link_and_the_typed_figures(
        self, auth_client, world,
    ):
        """R-SAL52: a second 03-27 form (Roth 100) is refused; the stub keeps Roth 110."""
        _record(auth_client, world)
        response = _record(auth_client, world, roth="100.00", printed_net="2112.62")
        assert response.status_code == 422
        html = response.data.decode()
        assert "Your 2026-03-27 stub was entered while this form was open" in html
        assert f'href="/salary/stubs/{_stub().id}"' in html
        roth = world["lines"]["roth"]
        roth_input = html[html.index(f'name="line-{roth}"'):]
        roth_input = roth_input[:roth_input.index(">")]
        assert 'value="100.00"' in roth_input
        assert "110.00" not in roth_input
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("110.00")
        )

    def test_a_record_racing_past_the_check_meets_the_same_refusal(
        self, auth_client, world, monkeypatch,
    ):
        """Two records of 03-27 that both pass the check: the unique key answers R-SAL52."""
        _record(auth_client, world)
        monkeypatch.setattr(pay_stub_service, "stub_on", lambda profile, day: None)
        response = _record(auth_client, world, roth="100.00", printed_net="2112.62")
        assert response.status_code == 422
        assert b"Your 2026-03-27 stub was entered while this form was open" in response.data
        monkeypatch.undo()
        assert db.session.query(PayStub).count() == 1

    def test_a_date_past_the_calendar_is_refused_not_a_crash(self, auth_client, world):
        """9999-12-31 would overflow the calendar's projection; it is a 422."""
        url = f"/salary/{world['profile_id']}/stubs"
        assert auth_client.get(f"{url}/new?payday=9999-12-31").status_code == 422
        assert _record(auth_client, world, payday="9999-12-31").status_code == 422
        assert db.session.query(PayStub).count() == 0

    def test_an_input_for_another_profiles_line_is_not_read(
        self, auth_client, world, seed_user,
    ):
        """A posted field named for a foreign line is ignored, never stored."""
        other = make_salary_profile(seed_user, db.session, name="Side Job")
        db.session.flush()
        foreign = make_flat_paycheck_line(
            other, "Foreign", "1.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        db.session.commit()
        payload = _payload(world)
        payload[f"line-{foreign.id}"] = "1.00"
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 302
        assert foreign.id not in {row.paycheck_line_id for row in _stub().line_amounts}

    def test_another_owners_profile_is_not_found(self, auth_client, world, victim):
        """The owner's record and entry requests on the SECOND owner's profile 404.

        Paired with the same two routes on the owner's own profile succeeding,
        so the 404 is the ownership gate's and not the URL map's.
        """
        foreign = f"/salary/{victim['profile_id']}/stubs"
        refused = auth_client.post(foreign, data=_payload(world))
        assert refused.status_code == 404, refused.headers.get("Location")
        assert auth_client.get(f"{foreign}/new?payday={_PAYDAY}").status_code == 404
        assert db.session.query(PayStub).filter_by(
            salary_profile_id=victim["profile_id"],
        ).count() == 1
        own = f"/salary/{world['profile_id']}/stubs"
        assert auth_client.get(f"{own}/new?payday={_PAYDAY}").status_code == 200
        assert _record(auth_client, world).status_code == 302


class TestEditing:
    """The edit door, version-checked."""

    def _edit(self, client, world, stub, **overrides):
        """POST the edit door with the stub's current version unless overridden."""
        payload = _payload(world, **{k: v for k, v in overrides.items() if k != "version_id"})
        payload["version_id"] = str(overrides.get("version_id", stub.version_id))
        return client.post(f"/salary/stubs/{stub.id}/edit", data=payload)

    def test_the_payload_is_the_form_the_edit_page_emits(self, auth_client, world):
        """The saved stub's page renders the same controls plus the version."""
        _record(auth_client, world)
        page = auth_client.get(f"/salary/stubs/{_stub().id}")
        assert _field_names(page.data.decode()) == set(_payload(world)) | {"version_id"}

    def test_the_edit_form_asks_for_the_printed_totals_again(self, auth_client, world):
        """R-SAL42, R-SAL99: neither printed total is stored, so a saved stub's form asks both."""
        _record(auth_client, world)
        html = auth_client.get(f"/salary/stubs/{_stub().id}").data.decode()
        for name in ("printed_gross", "printed_net"):
            control = html[html.index(f'name="{name}"'):]
            assert 'value=""' in control[:control.index(">")], name

    def test_an_edit_rewrites_the_stub(self, auth_client, world):
        """Roth 110 -> 100, so the stub nets $2,112.62."""
        _record(auth_client, world)
        response = self._edit(auth_client, world, _stub(), roth="100.00",
                              printed_net="2112.62")
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("100.00")
        )

    def test_a_stale_invalid_form_is_turned_away_as_stale(self, auth_client, world):
        """A version-1 form with an unreadable base pay, after the stub moved to 2: stale.

        The route refuses 'abc' itself, so were the fields read first the
        answer would be a 422 re-render re-pinned to version 2, and that
        form's next save would overwrite the other edit unseen.
        """
        _record(auth_client, world)
        stub = _stub()
        assert self._edit(auth_client, world, stub, roth="100.00",
                          printed_net="2112.62").status_code == 302
        payload = _payload(world)
        payload["base_pay"] = "abc"
        payload["version_id"] = "1"
        response = auth_client.post(f"/salary/stubs/{stub.id}/edit", data=payload)
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("100.00")
        )

    def test_an_edit_onto_a_held_payday_links_that_stub(self, auth_client, world):
        """Moving the 03-27 stub onto 03-13, which holds one, is refused with its link."""
        _record(auth_client, world, payday="2026-03-13")
        held = _stub_on(date(2026, 3, 13))
        _record(auth_client, world)
        stub = _stub_on(date(2026, 3, 27))
        response = self._edit(auth_client, world, stub, payday="2026-03-13")
        assert response.status_code == 422
        assert b"Your 2026-03-13 stub already exists" in response.data
        assert f'href="/salary/stubs/{held.id}"'.encode() in response.data

    def test_a_form_without_its_version_is_a_bad_request(self, auth_client, world):
        """The edit form always carries its version; one without is refused."""
        _record(auth_client, world)
        response = auth_client.post(f"/salary/stubs/{_stub().id}/edit", data=_payload(world))
        assert response.status_code == 400

    def test_a_stale_version_is_a_conflict_and_writes_nothing(self, auth_client, world):
        """A form claiming version 7 of a version-1 stub is turned away."""
        _record(auth_client, world)
        response = self._edit(auth_client, world, _stub(), roth="100.00",
                              printed_net="2112.62", version_id=7)
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("110.00")
        )

    def test_another_owners_stub_is_not_found(self, auth_client, world, victim):
        """The SECOND owner's stub 404s on view and edit; the owner's own is served."""
        _record(auth_client, world)
        own = db.session.query(PayStub).filter_by(
            salary_profile_id=world["profile_id"],
        ).one()
        payload = _payload(world)
        payload["version_id"] = "1"
        assert auth_client.get(f"/salary/stubs/{victim['stub_id']}").status_code == 404
        assert auth_client.post(
            f"/salary/stubs/{victim['stub_id']}/edit", data=payload,
        ).status_code == 404
        db.session.expire_all()
        assert db.session.get(PayStub, victim["stub_id"]).base_pay == Decimal("1000.00")
        assert auth_client.get(f"/salary/stubs/{own.id}").status_code == 200


class TestTheSwitch:
    """R-SAL51 (a): each stub's own "Use for pricing" button."""

    def _switch(self, client, stub_id, state, version):
        """POST the switch."""
        return client.post(
            f"/salary/stubs/{stub_id}/pricing",
            data={"use_for_pricing": state, "version_id": str(version)},
        )

    def test_off_then_on_keeps_the_stub(self, auth_client, world):
        """Off keeps the stub; on restores the switch; nothing is deleted."""
        _record(auth_client, world)
        stub = _stub()
        assert self._switch(auth_client, stub.id, "off", 1).status_code == 302
        assert _stub().use_for_pricing is False
        assert self._switch(auth_client, stub.id, "on", 2).status_code == 302
        assert _stub().use_for_pricing is True
        assert db.session.query(PayStub).count() == 1

    def test_a_stale_switch_is_a_conflict(self, auth_client, world):
        """A button rendered at version 5 moves nothing."""
        _record(auth_client, world)
        assert self._switch(auth_client, _stub().id, "off", 5).status_code == 302
        assert _stub().use_for_pricing is True

    def test_a_malformed_switch_is_a_bad_request(self, auth_client, world):
        """A state other than on/off is refused before any read of it."""
        _record(auth_client, world)
        assert self._switch(auth_client, _stub().id, "maybe", 1).status_code == 400

    def test_another_owners_switch_is_not_found(self, auth_client, world, victim):
        """The SECOND owner's switch 404s and stays on; the owner's own turns."""
        _record(auth_client, world)
        own_id = db.session.query(PayStub).filter_by(
            salary_profile_id=world["profile_id"],
        ).one().id
        assert self._switch(auth_client, victim["stub_id"], "off", 1).status_code == 404
        db.session.expire_all()
        assert db.session.get(PayStub, victim["stub_id"]).use_for_pricing is True
        assert self._switch(auth_client, own_id, "off", 1).status_code == 302


    def test_the_profile_page_lists_the_stub_with_its_button(self, auth_client, world):
        """The Pay stubs card: the payday, its net and the switch."""
        _record(auth_client, world)
        page = auth_client.get(f"/salary/{world['profile_id']}/edit")
        assert page.status_code == 200
        assert b"Pay stubs" in page.data
        assert b"2026-03-27" in page.data
        assert b"$2,102.62 net" in page.data
        assert b"Used for pricing: turn off" in page.data


@pytest.fixture(name="paid_world")
def _paid_world(world, seed_user):
    """The worked example's profile paid into a salary template, so a write regenerates.

    The salary template is what the paycheck rows belong to; the plain
    ``world`` profile has none, and the regeneration skips a profile without
    one.  The template's stated price, ``$2,000.00``, is made up and is no
    paycheck's net.
    """
    profile = db.session.get(SalaryProfile, world["profile_id"])
    template = make_income_template(db.session, seed_user, name="Day Job pay")
    profile.template_id = template.id
    db.session.commit()
    return {**world, "template_id": template.id}


def _todays_net(world):
    """Today's paycheck net, priced by a pass built now -- what a write must move."""
    db.session.expire_all()
    profile = db.session.get(SalaryProfile, world["profile_id"])
    ctx = BalanceContext.build(profile.user_id)
    period = ctx.calendar().period_containing(ctx.as_of)
    return ctx.paychecks().for_profile(profile).at(period).earnings.net_pay


class TestAStubWriteMovesTodaysPaycheck:
    """Each stub door moves today's paycheck, as a pass built after it prices it.

    Plan step salary:S11-c-2c: a stub prices the taxes of every paycheck on
    or after its payday (ruling R-SAL100), so recording, editing or switching
    one moves today's paycheck, the figure the Recurring and savings pages
    show (``obligations_aggregator.todays_price``), as the calibration doors
    it replaced did.  The stub here is dated 03-13, the payday of today's
    paycheck (today is 03-20), so it prices that paycheck; every door is
    driven with the form the page emits (the worked example's payload,
    pinned to the rendered controls above).  The figure is the pricer's own,
    read by a pass built after the write, and each case proves the write
    moved it.  **These cases graded the doors' regeneration (review M3)
    through the amount it stored on the salary template, until plan step
    X-bp deleted that re-statement**: the paycheck is priced live, so what a
    stub write must do is reach the pricer, and a stub changes no row the
    regeneration redraws.
    """

    _TODAY_PAYDAY = "2026-03-13"

    def test_recording_a_stub_moves_the_paycheck_it_prices(self, auth_client, paid_world):
        """The formulas' net before; the stub-priced net after."""
        formulas_net = _todays_net(paid_world)

        response = _record(auth_client, paid_world, payday=self._TODAY_PAYDAY)

        assert response.status_code == 302
        assert _todays_net(paid_world) != formulas_net

    def test_editing_a_stub_moves_the_paycheck_it_now_prices(self, auth_client, paid_world):
        """Federal $150.00 -> $250.00 on the stub moves today's paycheck."""
        _record(auth_client, paid_world, payday=self._TODAY_PAYDAY)
        before = _todays_net(paid_world)
        stub = _stub()
        payload = _payload(paid_world, payday=self._TODAY_PAYDAY, printed_net="2002.62")
        payload[_tax_field(WithholdingKindEnum.FEDERAL_INCOME)] = "250.00"
        payload["version_id"] = str(stub.version_id)

        response = auth_client.post(f"/salary/stubs/{stub.id}/edit", data=payload)

        assert response.status_code == 302
        assert _todays_net(paid_world) != before

    def test_switching_a_stub_off_and_on_moves_each_paycheck(self, auth_client, paid_world):
        """Off: the formulas' net; on again: the stub's."""
        formulas_net = _todays_net(paid_world)
        _record(auth_client, paid_world, payday=self._TODAY_PAYDAY)
        stub_net = _todays_net(paid_world)
        assert stub_net != formulas_net
        stub_id = _stub().id

        off = auth_client.post(
            f"/salary/stubs/{stub_id}/pricing",
            data={"use_for_pricing": "off", "version_id": "1"},
        )
        assert off.status_code == 302
        assert _todays_net(paid_world) == formulas_net

        on = auth_client.post(
            f"/salary/stubs/{stub_id}/pricing",
            data={"use_for_pricing": "on", "version_id": "2"},
        )
        assert on.status_code == 302
        assert _todays_net(paid_world) == stub_net


def _selected(html, name):
    """The values of the options selected in the select named *name*."""
    start = html.index(f'name="{name}"')
    select = html[start:html.index("</select>", start)]
    return re.findall(r'<option value="([^"]+)" selected>', select)


class TestTheKindAStubPrints:
    """R-SAL58: each line's kind is the one the STUB prints it under, pre-set to the line's."""

    def test_the_new_form_presets_each_line_to_its_own_kind(self, auth_client, world):
        """All five lines, taken or not, offer their own kind selected."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        html = response.data.decode()
        for key, line_id in world["lines"].items():
            assert _selected(html, f"line-kind-{line_id}") == [str(world["kinds"][key])], key

    def test_a_line_printed_under_another_heading_is_recorded_and_reported(
        self, auth_client, world,
    ):
        """Phone posted as an AFTER-TAX earning: stored so, listed, and pre-set so on its page.

        The net is $2,102.62 either way (an earning joins the deposit whether
        taxed or not, once the taxes are typed), and R-SAL99 reads the printed
        gross as base pay plus the TAXABLE earnings, $2,939.62 here, so the
        record passes both checks and the REPORT is what shows the heading
        differs.
        """
        after_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.AFTER_TAX_EARNING)
        response = auth_client.post(
            f"/salary/{world['profile_id']}/stubs",
            data=_printed_under(
                world, _payload(world, printed_gross="2939.62"),
                phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
            ),
        )
        assert response.status_code == 302
        phone = world["lines"]["phone"]
        assert {
            r.paycheck_line_id: r.paycheck_line_kind_id for r in _stub().line_amounts
        }[phone] == after_tax
        page = auth_client.get(response.headers["Location"]).data.decode()
        assert "$2,102.62" in page
        assert "4 paycheck lines" in page
        assert "After-tax earning</span> on the stub" in page
        assert "kind differs" in page
        assert _selected(page, f"line-kind-{phone}") == [str(after_tax)]

    def test_a_line_whose_kind_and_figure_both_differ_says_so(self, auth_client, world):
        """Phone printed as an AFTER-TAX earning of $65.00; the app takes $60.00 taxable.

        gross 2884.62 + 55.00 = 2939.62; net 2939.62 - 315.00 - 472.00 - 110.00
        + 65.00 = 2107.62: the printed gross and net the record carries.
        """
        phone = world["lines"]["phone"]
        payload = _printed_under(
            world, _payload(world, printed_net="2107.62", printed_gross="2939.62"),
            phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        payload[f"line-{phone}"] = "65.00"
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 302
        page = auth_client.get(response.headers["Location"]).data.decode()
        assert "$2,107.62" in page
        assert "kind and figure differ" in page

    def test_a_filled_line_without_its_kind_is_refused_on_that_field(
        self, auth_client, world,
    ):
        """A crafted post dropping Phone's kind is a 422 on that select, and nothing is written."""
        payload = _payload(world)
        phone = world["lines"]["phone"]
        del payload[f"line-kind-{phone}"]
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 422
        html = response.data.decode()
        select = html[html.index(f'name="line-kind-{phone}"') - 200:]
        assert "is-invalid" in select[:select.index("</select>")]
        assert "Not a valid id." in html
        assert db.session.query(PayStub).count() == 0

    def test_the_stub_page_keeps_its_kinds_after_its_line_is_re_kinded(
        self, auth_client, world,
    ):
        """Finding SAL-567: Phone's LINE turns post-tax; the stub still nets $2,102.62.

        Its page lists the heading mismatch, and its form offers the kind the
        STUB recorded, not the line's new one.
        """
        taxable = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.TAXABLE_EARNING)
        _record(auth_client, world)
        phone = world["lines"]["phone"]
        db.session.get(PaycheckLine, phone).paycheck_line_kind_id = (
            ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION)
        )
        db.session.commit()
        page = auth_client.get(f"/salary/stubs/{_stub().id}").data.decode()
        assert "$2,102.62" in page
        assert "Taxable earning</span> on the stub" in page
        assert "Post-tax deduction</span> in the app" in page
        assert _selected(page, f"line-kind-{phone}") == [str(taxable)]


#: Ruling R-SAL126's words (amending R-SAL125's, which amended R-SAL123's), byte
#: for byte: the stub page's sentence under a one-off that changes the stub's taxes.
_ONE_OFF_SENTENCE = (
    b"This one-off can affect taxes, so this stub won't match any paycheck. "
    b"Paychecks use a matching stub first."
)


class TestTheOneOffSentence:
    """R-SAL123's rule on the saved stub's page, in R-SAL126's words (plan step S11-c-2c).

    The page shows the sentence exactly when the engine's picker treats the
    stub as one of other lines: a one-off of a kind a tax formula reads -- a
    taxable earning or a pre-tax deduction -- carrying money.  The service flag
    behind it is graded in ``test_pay_stub_service.py``; this grades what the
    page RENDERS, which the delta review measured no test reading (three
    template mutations survived every stub test).  No earlier wording is on
    the page: fork 8b's "matches no normal paycheck", R-SAL123's "with your
    usual lines" (false for a switched-off stub and for a paycheck whose lines
    are not the usual ones) and R-SAL125's "with the same lines as that
    paycheck" (false where both stubs carry such a one-off).  R-SAL126 gives
    the gist in plain words and leaves the exact answer to the card that
    names each paycheck's stub; the developer refused a full statement of the
    rule as too long for the page.
    """

    @pytest.mark.parametrize(("kind", "amount", "gross", "net", "shown"), [
        # gross 2884.62 + Phone 60.00 + 55.00 = 2999.62;
        # net 2999.62 - 315.00 - 472.00 - 110.00 = 2102.62
        (PaycheckLineKindEnum.TAXABLE_EARNING, "55.00", "2999.62", "2102.62", True),
        # gross 2884.62 + 60.00 = 2944.62; net 2944.62 - 315.00 - 55.00 - 472.00 - 110.00
        # = 1992.62
        (PaycheckLineKindEnum.PRE_TAX_DEDUCTION, "55.00", "2944.62", "1992.62", True),
        # gross 2944.62; net 2944.62 - 315.00 - 472.00 - 110.00 - 55.00 = 1992.62
        (PaycheckLineKindEnum.POST_TAX_DEDUCTION, "55.00", "2944.62", "1992.62", False),
        # gross 2944.62 (outside it on a "no" job); net 2944.62 - 315.00 - 472.00
        # - 110.00 + 55.00 = 2102.62
        (PaycheckLineKindEnum.AFTER_TAX_EARNING, "55.00", "2944.62", "2102.62", False),
        # a $0.00 one-off moves no tax: gross 2944.62; net 2944.62 - 315.00 - 472.00
        # - 110.00 = 2047.62
        (PaycheckLineKindEnum.TAXABLE_EARNING, "0.00", "2944.62", "2047.62", False),
    ])
    def test_the_sentence_shows_only_under_a_one_off_that_changes_the_taxes(
        self, auth_client, world, kind, amount, gross, net, shown,
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Recorded through the door with a one-off of *kind*, the saved page says so or not.

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments`` --
        two fixtures and the five columns of one parametrized case.
        """
        payload = _payload(world, one_off=("Thing", amount), printed_gross=gross,
                           printed_net=net)
        payload["one_off_kind"] = [str(ref_cache.paycheck_line_kind_id(kind)), ""]
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 302, response.data.decode()[:2000]
        page = auth_client.get(response.headers["Location"])
        assert page.status_code == 200
        assert b"One-offs on this stub:" in page.data
        assert (_ONE_OFF_SENTENCE in page.data) is shown
        assert b"matches no normal paycheck" not in page.data
        assert b"with your usual lines" not in page.data
        assert b"with the same lines as that paycheck" not in page.data


class TestTheLineDelete:
    """Fork 10: a line a stub names is refused, worded by R-SAL51 (c)."""

    def test_a_line_a_stub_names_is_refused_and_stays(self, auth_client, world):
        """Health Insurance is on the 03-27 stub."""
        _record(auth_client, world)
        health = world["lines"]["health"]
        response = auth_client.post(
            f"/salary/lines/{health}/delete", follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Health Insurance is on your 2026-03-27 stub; end it instead." in response.data
        db.session.expire_all()
        assert db.session.get(PaycheckLine, health) is not None

    def test_the_refusal_shows_inside_the_htmx_swap(self, auth_client, world):
        """The trash button posts over HTMX; the refusal is in the swapped section."""
        _record(auth_client, world)
        health = world["lines"]["health"]
        response = auth_client.post(
            f"/salary/lines/{health}/delete", headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert 'id="lines-section"' in html
        assert "Health Insurance is on your 2026-03-27 stub; end it instead." in html
        db.session.expire_all()
        assert db.session.get(PaycheckLine, health) is not None

    def test_a_line_no_stub_names_is_still_deleted(self, auth_client, world):
        """Vision is on no stub."""
        _record(auth_client, world)
        vision = world["lines"]["vision"]
        assert auth_client.post(f"/salary/lines/{vision}/delete").status_code == 302
        db.session.expire_all()
        assert db.session.get(PaycheckLine, vision) is None


def _say_yes(world):
    """Set the world's job to "my stub's gross includes after-tax earnings" (R-SAL102)."""
    db.session.get(SalaryProfile, world["profile_id"]).stub_gross_includes_after_tax = True
    db.session.commit()


def _reimbursed_payload(world, printed_gross, *, roth="110.00", printed_net="2067.62"):
    """The worked example with a $20.00 AFTER-TAX one-off "Reimbursement" for Retro pay.

    gross (base + Phone) 2884.62 + 60.00 = 2944.62; net 2944.62 - 315.00 -
    472.00 - 110.00 + 20.00 = 2067.62.  A stub whose gross holds the
    reimbursement prints 2964.62.
    """
    payload = _payload(world, one_off=("Reimbursement", "20.00"), printed_net=printed_net,
                       printed_gross=printed_gross, roth=roth)
    after_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.AFTER_TAX_EARNING)
    payload["one_off_kind"] = [str(after_tax), ""]
    return payload


def _setting_link(world, words):
    """The anchor the stub form draws to the profile's setting, worded *words*.

    Both setting links open a new tab (ruling R-SAL107).
    """
    return (
        f'<a href="/salary/{world["profile_id"]}/edit#stub_gross_includes_after_tax" '
        f'target="_blank" rel="noopener">{words}</a>'
    )


def _say(client, world, answer):
    """Switch the job's answer through the PROFILE's own door, as another tab would."""
    profile = db.session.get(SalaryProfile, world["profile_id"])
    response = client.post(f"/salary/{world['profile_id']}", data={
        "name": profile.name, "version_id": profile.version_id,
        "stub_gross_includes_after_tax": answer,
    })
    assert response.status_code == 302


class TestTheJobsGrossSetting:
    """R-SAL102, R-SAL104 to R-SAL107 and R-SAL110 at the stub door and on the profile page.

    In the words of R-SAL114 to R-SAL116.
    """

    def test_a_refusal_naming_the_setting_links_to_it(self, auth_client, world):
        """A "no" job, gross 2964.62 holding the reimbursement: a 422 asking both, with the link.

        The setting's sentence ends in its link, then the full stop, inside
        the printed gross's own feedback (rulings R-SAL114 to R-SAL116), and
        nothing is written.
        """
        response = auth_client.post(
            f"/salary/{world['profile_id']}/stubs",
            data=_reimbursed_payload(world, "2964.62"),
        )
        assert response.status_code == 422
        page = unescape(response.data.decode())
        start = page.index("Base pay plus your taxable earnings come to $2,944.62")
        feedback = " ".join(page[start:page.index("</div>", start)].split())
        assert (
            "$20.00 less, the same as your untaxed earnings. Check the Gross Pay you "
            "typed. If it's right, is one of those earnings taxed on your stub? Then "
            "choose Taxable earning for it. Or, if your stub counts untaxed pay in its "
            "Gross Pay, " + _setting_link(world, "change that on your salary profile") + "."
        ) in feedback
        assert db.session.query(PayStub).count() == 0

    def test_a_yes_job_whose_gross_leaves_them_out_is_linked_to_the_setting(
        self, auth_client, world,
    ):
        """"yes", the stub prints 2944.62 (no reimbursement in it), net exact.

        The refusal names the typed gross or the setting, the setting's
        sentence ending in the link (worded by R-SAL114 and R-SAL115).
        """
        _say_yes(world)
        response = auth_client.post(
            f"/salary/{world['profile_id']}/stubs",
            data=_reimbursed_payload(world, "2944.62"),
        )
        assert response.status_code == 422
        page = unescape(response.data.decode())
        start = page.index(
            "Base pay plus your earnings, taxable and untaxed, come to $2,964.62",
        )
        feedback = " ".join(page[start:page.index("</div>", start)].split())
        assert (
            "$20.00 more, the same as your untaxed earnings. Check the Gross Pay you "
            "typed. If it's right, your stub leaves untaxed pay out of its Gross Pay: "
            + _setting_link(world, "change that on your salary profile") + "."
        ) in feedback

    def test_a_new_stub_form_survives_the_setting_changed_elsewhere(self, auth_client, world):
        """R-SAL107: refused on "no", the job switched in another tab, the SAME form saves.

        The refused page's own form -- every control it re-renders, read off
        the page before the switch -- is what is posted after it: the form
        carries nothing that pins the profile, and the door reads the answer
        afresh.
        """
        url = f"/salary/{world['profile_id']}/stubs"
        refused = auth_client.post(url, data=_reimbursed_payload(world, "2964.62"))
        assert refused.status_code == 422
        original = rendered_form_controls(refused.data.decode(), url)
        assert original["printed_gross"] == ["2964.62"]
        _say(auth_client, world, "true")
        assert auth_client.post(url, data=original).status_code == 302
        assert [(o.name, o.amount) for o in _stub().one_offs] == [
            ("Reimbursement", Decimal("20.00")),
        ]

    def test_an_edit_form_survives_the_setting_changed_elsewhere(self, auth_client, world):
        """R-SAL107 on the edit door: the stub's own version is all its form pins.

        Recorded on "yes" (gross 2964.62).  Its page's form is read off the
        page and typed into -- Roth 110 -> 100, which moves the net to
        2077.62, and the gross a "no" job checks, base + Phone = 2944.62 --
        then the job is switched to "no" elsewhere, and exactly that form is
        posted: saved, not stale.
        """
        _say_yes(world)
        url = f"/salary/{world['profile_id']}/stubs"
        assert auth_client.post(
            url, data=_reimbursed_payload(world, "2964.62"),
        ).status_code == 302
        stub = _stub()
        edit_url = f"/salary/stubs/{stub.id}/edit"
        original = rendered_form_controls(
            auth_client.get(f"/salary/stubs/{stub.id}").data.decode(), edit_url,
        )
        original[f"line-{world['lines']['roth']}"] = ["100.00"]
        original["printed_gross"] = ["2944.62"]
        original["printed_net"] = ["2077.62"]
        _say(auth_client, world, "false")
        response = auth_client.post(edit_url, data=original)
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("100.00")
        )

    def test_a_yes_job_records_that_stub(self, auth_client, world):
        """The same form on a "yes" job saves: base + taxable + after-tax is 2964.62."""
        _say_yes(world)
        response = auth_client.post(
            f"/salary/{world['profile_id']}/stubs",
            data=_reimbursed_payload(world, "2964.62"),
        )
        assert response.status_code == 302
        assert [(o.name, o.amount) for o in _stub().one_offs] == [
            ("Reimbursement", Decimal("20.00")),
        ]

    def test_the_line_under_the_gross_box_says_what_this_job_counts(self, auth_client, world):
        """"no": the taxable earnings; "yes": taxed and untaxed; both link the setting.

        Worded by R-SAL114 (the approved list, R-SAL115).
        """
        url = f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}"
        link = _setting_link(world, "change that")

        def help_line(page):
            """The line under the gross box, its whitespace collapsed."""
            start = page.index("Checks your typing and isn't saved.")
            return " ".join(page[start:page.index("</div>", start)].split())

        opening = "Checks your typing and isn't saved. Base pay plus your "
        closing = (
            " should equal it, because your salary profile says that's what your "
            f"stub's Gross Pay includes ({link})."
        )
        page = auth_client.get(url).data.decode()
        assert help_line(page) == f"{opening}taxable earnings{closing}"
        _say_yes(world)
        page = auth_client.get(url).data.decode()
        assert help_line(page) == f"{opening}earnings, taxable and untaxed,{closing}"

    def test_the_link_lands_on_the_setting(self, auth_client, world):
        """The profile page carries the control the link's fragment names."""
        page = auth_client.get(f"/salary/{world['profile_id']}/edit").data.decode()
        assert 'id="stub_gross_includes_after_tax"' in page
