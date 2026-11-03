"""Route tests for ``POST /pay-periods/earlier`` ("Add earlier paychecks").

Plan step ``pay_calendar:C18-b`` (ruling **R-PC87**).  The door, the
refusals and the population it owes, through the HTTP layer: what the
settings card renders and posts, the flash each outcome carries, that a
refusal writes nothing, that the new paychecks are populated where an
account's books reach them and left empty where they do not (ruling R-R38;
the books bound, ``pay_calendar:C18-a``), and owner-only access.  The service
contract is graded in ``tests/test_services/test_pay_period_earlier.py``.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.exceptions import ValidationError
from app.routes import pay_periods as pay_periods_routes
from app.services import pay_period_write, pay_schedule_service
from tests._test_helpers import (
    all_periods,
    freeze_today,
    make_cadence_rule,
    make_expense_template,
    restate_account_opening,
    rhythm_of,
    shift_form_value,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY


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


def _the_add_earlier_form(page):
    """Return the ``(action, fields)`` the settings card's form would submit.

    Parsed out of the rendered page rather than hand-picked, so the case
    posts what a browser posts (a form submits every control it renders).
    """
    form = re.search(
        r'<form method="POST" action="([^"]*/pay-periods/earlier)">(.*?)</form>',
        page, re.S,
    )
    assert form is not None, "the Add earlier paychecks form is not rendered"
    fields = dict(re.findall(
        r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form.group(2),
    ))
    return form.group(1), fields


class TestTheAddEarlierRoute:
    """POST /pay-periods/earlier."""

    def test_the_settings_card_posts_one_earlier_paycheck(
        self, app, db, bare_auth_client, bare_user,
    ):
        """The card as rendered adds one paycheck below the first and says so."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            page = bare_auth_client.get(
                "/settings?section=pay-periods",
            ).get_data(as_text=True)
            assert "Add earlier paychecks" in page
            action, fields = _the_add_earlier_form(page)
            assert fields.get("num_periods") == "1"

            resp = bare_auth_client.post(action, data=fields, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Added 1 earlier paycheck(s)." in resp.data
            assert _paydays(user_id)[:2] == [date(2025, 12, 19), date(2026, 1, 2)]
            assert [era.effective_from for era in pay_schedule_service.resolve_schedule(
                user_id,
            ).eras] == [date(2025, 12, 19)]

    def test_a_count_outside_the_bound_is_refused_and_adds_nothing(
        self, app, db, bare_auth_client, bare_user,
    ):
        """The schema's bound flashes; the schedule is as it was."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            before = _paydays(user_id)

            resp = bare_auth_client.post(
                "/pay-periods/earlier", data={"num_periods": "0"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            assert _paydays(user_id) == before

    def test_a_payday_below_the_stated_history_flashes_the_ruling_and_adds_nothing(
        self, app, db, bare_auth_client, bare_user,
    ):
        """R-PC104's sentence reaches the page; the refusal writes nothing.

        The history is asked before any statement, so this is not the case
        that grades the route's rollback --
        :meth:`test_a_refusal_after_the_door_wrote_is_rolled_back` is.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            pay_schedule_service.set_history_opening(user_id, date(2025, 12, 10))
            db.session.commit()
            before = _paydays(user_id)

            resp = bare_auth_client.post(
                "/pay-periods/earlier", data={"num_periods": "2"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert (
                b"2025-12-05 is before 2025-12-10, the day you saved as when "
                b"your paychecks started. Change that date first, or add "
                b"fewer." in resp.data
            )
            db.session.remove()
            assert _paydays(user_id) == before

    def test_regenerating_from_before_the_first_paycheck_points_here(
        self, app, db, bare_auth_client, bare_user, monkeypatch,
    ):
        """R-PC103: the stated form's refusal names the door that does this.

        A rebuild from a day before the first paycheck used to be told it
        would split a paycheck (PC-499); it is told where to go instead.
        Today is pinned inside the first paycheck so the rebuild KEEPS it --
        with nothing kept, a regenerate is a whole rebuild and has no first
        paycheck to fall before.
        """
        freeze_today(monkeypatch, date(2026, 6, 15))
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id, first_payday=date(2026, 6, 5))
            before = _paydays(user_id)

            resp = bare_auth_client.post(
                "/pay-periods/regenerate",
                data={
                    "new_start_date": "2026-05-22",
                    "num_periods": "3",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(),
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert (
                b"2026-05-22 is before your first paycheck (2026-06-05). To add "
                b"paychecks before it, use Add earlier paychecks in Settings "
                b"&gt; Pay Periods." in resp.data
            )
            assert _paydays(user_id) == before

    def test_a_refusal_after_the_door_wrote_is_rolled_back(
        self, app, db, bare_auth_client, bare_user, monkeypatch,
    ):
        """The route's rollback undoes what the door had already flushed.

        The door's own refusals come before its first statement; the
        populate step after it can refuse once the paycheck row and the
        moved era phase are flushed.  Forced to refuse there, the route must
        roll both back -- graded by committing and re-reading, because a
        flushed row a rollback missed would be persisted by the next commit
        on this session and ``session.dirty`` cannot tell the two apart.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record_schedule(db.session, user_id)
            before = _paydays(user_id)

            def refuse_after_the_write(*_args, **_kwargs):
                raise ValidationError("populate refused after the write")

            monkeypatch.setattr(
                pay_periods_routes, "populate_new_periods", refuse_after_the_write,
            )
            resp = bare_auth_client.post(
                "/pay-periods/earlier", data={"num_periods": "2"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"populate refused after the write" in resp.data
            db.session.commit()
            db.session.remove()
            assert _paydays(user_id) == before
            assert [era.effective_from for era in pay_schedule_service.resolve_schedule(
                user_id,
            ).eras] == [date(2026, 1, 2)]



def _monthly_bill_from(seed_user, starts_on):
    """Author a priced monthly bill on the seed account whose first bill is *starts_on*.

    A CALENDAR-space rule, because only such a rule can name a day below
    the record: a paycheck-space rule stores its first occurrence at or
    above the opening payday it was authored against
    (``recurrence._resolution``), so an every-paycheck bill never reaches a
    paycheck added below the record.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Rent",
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template, Decimal("1200.00"))
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    return template


class TestTheNewPaychecksArePopulated:
    """R-R38 through this door: the route fills what the books reach.

    Every case adds the paycheck of 2023-12-22 below the seeded owner's
    2024-01-05 opening.  Which recurring rows it may hold is decided above
    this door -- the rule's own first occurrence, and the books bound
    (``pay_calendar:C18-a``) -- so the cases vary those and grade that the
    door POPULATED what they admit.
    """

    def test_a_bill_whose_rule_and_books_reach_the_paycheck_is_generated(
        self, app, db, auth_client, seed_user,
    ):
        """A monthly bill from 2023-12-28 on books opened 2023-06-01 lands there."""
        with app.app_context():
            user_id = seed_user["user"].id
            restate_account_opening(db.session, seed_user["account"], date(2023, 6, 1))
            template = _monthly_bill_from(seed_user, date(2023, 12, 28))
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/earlier", data={"num_periods": "1"},
            )

            assert resp.status_code == 302
            earliest = all_periods(user_id)[0]
            assert earliest.start_date == date(2023, 12, 22)
            assert db.session.query(Transaction).filter_by(
                pay_period_id=earliest.id, template_id=template.id,
            ).count() == 1

    def test_the_same_bill_before_the_books_is_left_empty(
        self, app, db, auth_client, seed_user,
    ):
        """The seeded books open 2024-01-04: the bill's 2023-12-28 is before them."""
        with app.app_context():
            user_id = seed_user["user"].id
            template = _monthly_bill_from(seed_user, date(2023, 12, 28))
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/earlier", data={"num_periods": "1"},
            )

            assert resp.status_code == 302
            earliest = all_periods(user_id)[0]
            assert earliest.start_date == date(2023, 12, 22)
            assert db.session.query(Transaction).filter_by(
                pay_period_id=earliest.id, template_id=template.id,
            ).count() == 0
            assert db.session.query(PayPeriod).filter_by(user_id=user_id).count() == 2

    def test_an_every_paycheck_bill_authored_on_the_record_is_left_empty(
        self, app, db, auth_client, seed_user,
    ):
        """Books reaching the paycheck are not enough: the rule starts at the record.

        The every-paycheck rule was authored against the 2024-01-05 opening,
        so its first occurrence is that paycheck, and a paycheck added below
        it is before the rule -- the shape every paycheck-space bill an
        owner already holds has.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            restate_account_opening(db.session, seed_user["account"], date(2023, 6, 1))
            template = make_expense_template(db.session, seed_user)
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/earlier", data={"num_periods": "1"},
            )

            assert resp.status_code == 302
            earliest = all_periods(user_id)[0]
            assert earliest.start_date == date(2023, 12, 22)
            assert db.session.query(Transaction).filter_by(
                pay_period_id=earliest.id, template_id=template.id,
            ).count() == 0


class TestOwnerOnly:
    """Owner-only access, paired with the route's existence."""

    def test_a_companion_gets_404(self, app, companion_client):
        """A companion is not the owner -- the route 404s."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/earlier", data={"num_periods": "1"},
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
                "/pay-periods/earlier", data={"num_periods": "0"},
            )

            assert resp.status_code != 404
