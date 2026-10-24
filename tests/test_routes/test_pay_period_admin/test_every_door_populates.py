"""Every HTTP door that creates a pay period fills it with its templates, the coverage that makes
ruling R-R38 safe: extend, regenerate, reset, generate, and the grid's and the dashboard's
rolling top-ups.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.services import pay_schedule_service
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import (
    shift_form_value,
    rhythm_of,
    all_periods,
    create_savings_account,
    make_expense_template,
    make_transfer_template,
    record_paydays_across_a_hole,
    resolved_amount,
)

from ._helpers import _future_periods, _spanning_periods


class TestEveryDoorThatCreatesAPeriodPopulatesIt:
    """Every HTTP door that creates a pay period fills it with its templates.

    **This class is what makes ruling R-R38 safe.**  Until plan step R7d-c-1
    the recurring rows were generated INSIDE ``extend_pay_periods`` /
    ``regenerate_pay_periods`` / ``reset_pay_periods``, so no caller could get
    them wrong.  They are now a second call the ROUTE makes -- the read pass
    the recurrence resolves in may only be opened at the HTTP boundary, and
    only after the paydays exist -- and a route that made the first call and
    not the second would ship paydays with no rent, no paycheck and no
    recurring transfer in them, with every period-count assertion in this file
    still green.

    So the coverage is stated where the composition now lives: one case per
    door, each asserting the ROWS rather than the count, each driven through
    the real HTTP request the browser makes.  ``test_pay_period_extend`` and
    its siblings grade the two halves separately; this grades that the doors
    call both.

    SIX doors create a pay period through HTTP: extend, regenerate, reset and
    generate directly, and the rolling top-up through ``GET /grid`` and
    ``GET /dashboard``. The sixth, ``POST /pay-periods/generate``, populated
    NOTHING until this step -- ledger row **D58**, pre-existing and found by
    censusing the writers R-R38 split. It reads as a first-time-only door and
    is not: ``record_paydays``' forward-only rule accepts any payday past the
    owner's last, so on an owner who already had a schedule it appended
    periods and skipped every template, measured through this same HTTP door
    at 3 appended periods holding 0 template rows.

    The seventh writer, ``registration_service.register_user``, has no HTTP door of
    its own here and is correct as it stands: no template can exist at
    registration, and the baseline scenario is created after that call, so a
    repopulation would return 0 on ``ctx.scenario is None``.
    """

    _AMOUNT = Decimal("1200.00")

    def _rows_in(self, db_session, period):
        """The template-linked transactions sitting in *period*."""
        return (
            db_session.query(Transaction)
            .filter_by(pay_period_id=period.id)
            .filter(Transaction.template_id.isnot(None))
            .all()
        )

    def _assert_each_period_holds_the_rent(self, db_session, periods):
        """Every period in *periods* holds exactly one row at the template's amount."""
        assert periods, "no period was created, so nothing is graded"
        for period in periods:
            rows = self._rows_in(db_session, period)
            assert len(rows) == 1, (
                f"pay period {period.id} (payday {period.start_date}) holds "
                f"{len(rows)} template rows, not 1 -- the door created the "
                f"period and its caller did not populate it"
            )
            assert resolved_amount(rows[0]) == self._AMOUNT

    def test_the_extend_route_populates_what_it_appends(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/extend leaves no empty paycheck behind.

        **The one case of the six that seeds BOTH engines**, because the
        producer runs two loops and a caller that ran only the transaction one
        would leave every other case here green -- "a new period never silently
        misses a recurring transfer" is that producer's own stated invariant.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            before = {p.id for p in all_periods(seed_user["user"].id)}

            resp = auth_client.post(
                "/pay-periods/extend", data={"num_periods": "2"},
            )
            assert resp.status_code == 302

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            assert len(appended) == 2
            self._assert_each_period_holds_the_rent(db.session, appended)
            for period in appended:
                transfers = (
                    db.session.query(Transfer)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(transfers) == 1, (
                    f"pay period {period.id} holds {len(transfers)} recurring "
                    f"transfers, not 1 -- the transfer engine did not run over "
                    f"this period, or placed its transfer in another one"
                )

    def test_the_regenerate_route_populates_the_tail_it_rebuilds(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/regenerate fills the tail it just rebuilt."""
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=6)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()
            before = {p.id for p in all_periods(seed_user["user"].id)}

            resp = auth_client.post(
                "/pay-periods/regenerate",
                data={
                    "new_start_date": "2026-06-20",
                    "num_periods": "3",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(),
                },
            )
            assert resp.status_code == 302

            db.session.expire_all()
            rebuilt = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            assert len(rebuilt) == 3
            self._assert_each_period_holds_the_rent(db.session, rebuilt)

    def test_the_reset_route_populates_the_schedule_it_rebuilds(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/reset fills every period of the new schedule.

        The reset is the door whose populate moved FURTHEST at R-R38: it used
        to run between the rebuild and the two posting re-syncs and now runs
        after both, so this case is also what grades that reordering through
        the real door.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=4)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "new_start_date": "2026-06-05",
                    "num_periods": "4",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(),
                    "confirm": "true",
                },
            )
            assert resp.status_code == 302

            db.session.expire_all()
            periods = all_periods(seed_user["user"].id)
            assert len(periods) == 4
            self._assert_each_period_holds_the_rent(db.session, periods)

    def test_the_generate_route_populates_what_it_appends(
        self, app, db, auth_client, seed_user,
    ):
        """POST /pay-periods/generate fills the periods it records.

        **Ledger row D58, and the fixture is the shape that made it a defect
        rather than a no-op**: the owner ALREADY has a schedule and an active
        template, which is the state ``record_paydays``' forward-only rule
        admits and which this door used to append into without generating a
        row. A brand-new owner cannot exercise it -- nothing to generate.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()
            before = {p.id for p in all_periods(seed_user["user"].id)}
            latest = max(p.start_date for p in all_periods(seed_user["user"].id))

            resp = auth_client.post(
                "/pay-periods/generate",
                data={
                    "start_date": (latest + timedelta(days=14)).isoformat(),
                    "num_periods": "3",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(),
                },
            )
            assert resp.status_code == 302

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            assert len(appended) == 3, (
                f"the door appended {len(appended)} periods, not 3; the "
                f"forward-only rule must have refused, and then this case "
                f"grades nothing"
            )
            self._assert_each_period_holds_the_rent(db.session, appended)

    def _rolling_deficit(self, db_session, seed_user, target=5):
        """A rolling owner short of *target*, holding an every-period template."""
        record_paydays_across_a_hole(
            user_id=seed_user["user"].id, first_payday=date(2026, 6, 8),
            num_periods=2, rhythm=rhythm_of(14),
        )
        make_expense_template(db_session, seed_user, amount="1200.00")
        pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
        pay_schedule_service.set_rolling(
            seed_user["user"].id, enabled=True, target_periods=target,
        )
        db_session.commit()
        return {p.id for p in all_periods(seed_user["user"].id)}

    def test_the_grid_render_populates_what_the_top_up_appends(
        self, app, db, auth_client, seed_user,
    ):
        """GET /grid fills the paydays its rolling top-up just appended."""
        with app.app_context():
            before = self._rolling_deficit(db.session, seed_user)
            resp = auth_client.get("/grid")
            assert resp.status_code == 200

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            self._assert_each_period_holds_the_rent(db.session, appended)

    def test_the_dashboard_render_populates_what_the_top_up_appends(
        self, app, db, auth_client, seed_user,
    ):
        """GET /dashboard fills the paydays its rolling top-up just appended."""
        with app.app_context():
            before = self._rolling_deficit(db.session, seed_user)
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200

            db.session.expire_all()
            appended = [
                p for p in all_periods(seed_user["user"].id)
                if p.id not in before
            ]
            self._assert_each_period_holds_the_rent(db.session, appended)
