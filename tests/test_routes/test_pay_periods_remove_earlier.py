"""Route tests for ``POST /pay-periods/remove-earlier`` ("Remove earlier paychecks").

Plan step ``pay_calendar:C21`` (rulings **R-PC108** to **R-PC111**).  The door
through the HTTP layer: what the settings card renders (the ruled preview's
title, text, label and button, and a select of every paycheck whose first
option removes nothing), what it posts, the flash each outcome carries, that a
refusal writes nothing, an id that is not the owner's, and owner-only access
paired with the route's existence.  The service contract is graded in
``tests/test_services/test_pay_period_remove_earlier/``.
"""
from __future__ import annotations

import html
import re
from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.pay_stub import PayStub
from app.services import pay_period_admin, pay_period_write, pay_schedule_service
from tests._test_helpers import add_txn, all_periods, make_salary_profile, rhythm_of


def _record_schedule(db_session, user_id, first_payday=date(2026, 1, 2), count=5):
    """Record a first fortnightly schedule through the writer and commit it."""
    pay_period_write.record_paydays(
        user_id=user_id, first_payday=first_payday, num_periods=count,
        rhythm=rhythm_of(14),
    )
    db_session.commit()


def _paydays(user_id):
    """Return the owner's recorded paydays, ascending."""
    return [period.start_date for period in all_periods(user_id)]


def _the_remove_earlier_form(page):
    """Return ``(action, csrf, options)`` of the rendered Remove earlier card.

    Parsed out of the rendered page rather than hand-picked, so a case posts
    what a browser posts: the form's hidden inputs and one ``<option>``
    value of its select, labelled as the owner reads it.
    """
    form = re.search(
        r'<form method="POST" action="([^"]*/pay-periods/remove-earlier)">(.*?)</form>',
        page, re.S,
    )
    assert form is not None, "the Remove earlier paychecks form is not rendered"
    body = form.group(2)
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', body)
    select = re.search(
        r'<select id="start_from_period_id" name="start_from_period_id"[^>]*>(.*?)</select>',
        body, re.S,
    )
    assert select is not None, "the card's select is not rendered"
    options = [
        (value, " ".join(html.unescape(label).split()))
        for value, label in re.findall(
            r'<option value="([^"]*)">(.*?)</option>', select.group(1), re.S,
        )
    ]
    return form.group(1), (csrf.group(1) if csrf else None), options


def _post(client, action, csrf, value):
    """Post the card's fields as a browser would, following the redirect."""
    fields = {"start_from_period_id": value}
    if csrf is not None:
        fields["csrf_token"] = csrf
    return client.post(action, data=fields, follow_redirects=True)


class TestTheCard:
    """The settings card is the ruled preview."""

    def test_the_card_renders_the_ruled_words_and_every_paycheck(
        self, app, db, bare_auth_client, bare_user,
    ):
        """Title, text, label, button; one option per paycheck, the first first."""
        with app.app_context():
            _record_schedule(db.session, bare_user["user"].id, count=3)
            page = bare_auth_client.get(
                "/settings?section=pay-periods",
            ).get_data(as_text=True)

            assert "Remove earlier paychecks" in page
            assert (
                "Delete every paycheck before the one you start from. Paychecks\n"
                "          holding money are never removed."
            ) in page
            assert "Start from paycheck" in page
            _action, _csrf, options = _the_remove_earlier_form(page)
            assert [label for _value, label in options] == [
                "#0 (01/02 - 01/15)", "#1 (01/16 - 01/29)", "#2 (01/30 - 02/12)",
            ]
            assert "Remove earlier" in page


class TestTheRemoveEarlierRoute:
    """POST /pay-periods/remove-earlier."""

    def test_the_card_removes_the_paychecks_before_the_one_picked(
        self, app, db, bare_auth_client, bare_user,
    ):
        """Undo an add: add two, pick the old first, and they go."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            pay_period_admin.add_earlier_pay_periods(user_id, 2)
            db.session.commit()
            page = bare_auth_client.get(
                "/settings?section=pay-periods",
            ).get_data(as_text=True)
            action, csrf, options = _the_remove_earlier_form(page)
            value = next(value for value, label in options if "01/02" in label)

            resp = _post(bare_auth_client, action, csrf, value)

            assert resp.status_code == 200
            assert b"Removed 2 earlier paycheck(s)." in resp.data
            assert _paydays(user_id)[0] == date(2026, 1, 2)
            assert [era.effective_from for era in pay_schedule_service.resolve_schedule(
                user_id,
            ).eras] == [date(2026, 1, 2)]

    def test_the_default_option_removes_nothing(
        self, app, db, bare_auth_client, bare_user,
    ):
        """The first option is the current first paycheck: a no-op, said so."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            before = _paydays(user_id)
            page = bare_auth_client.get(
                "/settings?section=pay-periods",
            ).get_data(as_text=True)
            action, csrf, options = _the_remove_earlier_form(page)

            resp = _post(bare_auth_client, action, csrf, options[0][0])

            assert b"Removed 0 earlier paycheck(s)." in resp.data
            assert _paydays(user_id) == before

    def test_a_paycheck_holding_money_flashes_the_ruling_and_removes_nothing(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """R-PC109's own example through the card: Groceries is named."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_admin.add_earlier_pay_periods(user_id, 1)
            add_txn(db.session, seed_user, created[0], "Groceries", "45.00")
            db.session.commit()
            before = _paydays(user_id)

            resp = auth_client.post(
                "/pay-periods/remove-earlier",
                data={"start_from_period_id": str(seed_periods[0].id)},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert (
                b"The 2025-12-19 paycheck holds 1 item you entered or changed "
                b"(Groceries). Delete or move it first."
            ) in resp.data
            assert _paydays(user_id) == before

    def test_another_owners_paycheck_is_refused_and_theirs_stands(
        self, app, db, bare_auth_client, bare_user, seed_user, seed_periods,
    ):
        """An id from another owner: the unresolved flash, nothing removed anywhere."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            mine, theirs = _paydays(user_id), _paydays(seed_user["user"].id)

            resp = bare_auth_client.post(
                "/pay-periods/remove-earlier",
                data={"start_from_period_id": str(seed_periods[5].id)},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"choose the paycheck to start from" in resp.data
            assert _paydays(user_id) == mine
            assert _paydays(seed_user["user"].id) == theirs

    def test_a_malformed_id_is_refused_by_the_form(
        self, app, db, bare_auth_client, bare_user,
    ):
        """Not a row id: the schema's flash, the schedule as it was."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            before = _paydays(user_id)

            resp = bare_auth_client.post(
                "/pay-periods/remove-earlier",
                data={"start_from_period_id": "007"},
                follow_redirects=True,
            )

            assert b"correct the form" in resp.data
            assert _paydays(user_id) == before


class TestTheRouteCatchesTheDoorsRefusalsOnly:
    """The route flashes ``PayPeriodRemovalRefused`` and nothing broader (review 3)."""

    def test_a_pay_stub_refusal_is_flashed(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """A ruled refusal raised before any write reaches the owner as a flash."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = pay_period_admin.add_earlier_pay_periods(user_id, 1)
            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            db.session.add(PayStub(
                salary_profile_id=profile.id, payday=created[0].start_date,
                base_pay=Decimal("2884.62"),
            ))
            db.session.commit()
            before = _paydays(user_id)

            resp = auth_client.post(
                "/pay-periods/remove-earlier",
                data={"start_from_period_id": str(seed_periods[0].id)},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"A pay stub is saved for 2025-12-19." in resp.data
            assert _paydays(user_id) == before

    def test_a_re_syncs_own_refusal_is_not_flashed_as_advice(
        self, app, db, bare_auth_client, bare_user, monkeypatch,
    ):
        """A ValidationError from below the door is a defect: it surfaces, unflashed.

        The ledger re-syncs run inside the door; the route's catch is the
        door's class, so a refusal of theirs propagates (the test client
        re-raises it; production answers 500 and rolls back).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id, count=3)
            pay_period_admin.add_earlier_pay_periods(user_id, 1)
            db.session.commit()

            def _refusing(_user_id):
                raise ValidationError("a re-sync refused")

            monkeypatch.setattr(pay_period_admin, "_refile_ledger", _refusing)
            first = next(
                period for period in all_periods(user_id)
                if period.start_date == date(2026, 1, 2)
            )

            with pytest.raises(ValidationError, match="a re-sync refused"):
                bare_auth_client.post(
                    "/pay-periods/remove-earlier",
                    data={"start_from_period_id": str(first.id)},
                )


class TestOwnerOnly:
    """Owner-only access, paired with the route's existence."""

    def test_a_companion_gets_404(self, app, companion_client):
        """A companion is not the owner -- the route 404s."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/remove-earlier", data={"start_from_period_id": "1"},
            )

            assert resp.status_code == 404

    def test_the_route_EXISTS_for_a_signed_in_owner(self, app, auth_client):
        """Pairs with the 404 above, which a moved route would leave passing.

        A 404 from the URL map and a 404 from ``require_owner`` look the
        same, so the ownership case alone would pass for a route that was
        renamed or removed.
        """
        with app.app_context():
            resp = auth_client.post(
                "/pay-periods/remove-earlier", data={"start_from_period_id": "007"},
            )

            assert resp.status_code != 404
