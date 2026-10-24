"""The schedule's own doors: the rolling window's config, when the owner's paychecks started
(balance:X-bh-2, R-IA) and the bounded full reset, each with its refusals and a companion's 404,
and the grid and dashboard loads that top the rolling window up.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.enums import StatusEnum
from app.models.account import Account
from app.services import pay_schedule_service
from tests._test_helpers import (
    shift_form_value,
    rhythm_of,
    add_txn,
    all_periods,
    record_paydays_across_a_hole,
)
from app.services import cash_ledger
from app.services.pay_calendar import calendar_for

from ._helpers import FROZEN_TODAY, _future_periods, _period_count, _starts


def _future_count(user_id):
    """How many paychecks have not ENDED before ``FROZEN_TODAY``.

    The rolling target's own question, asked of the derived calendar.  It
    counted ``PayPeriod.end_date >= FROZEN_TODAY`` in SQL until plan step
    ``pay_calendar:C4-c`` dropped that column.
    """
    return len(calendar_for(user_id).current_and_future(FROZEN_TODAY).periods)


class TestScheduleRoute:
    """POST /pay-periods/schedule (continuous-rolling-window config)."""

    def test_saves_rolling_config_and_redirects(
        self, app, db, auth_client, seed_user,
    ):
        """A valid post enables rolling and stores the target on the row."""
        with app.app_context():
            # A schedule row must exist first (generation captures cadence).
            pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
            db.session.commit()
            resp = auth_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "30",
                },
            )
            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            schedule = pay_schedule_service.get_schedule(seed_user["user"].id)
            assert schedule.rolling_enabled is True
            assert schedule.rolling_target_periods == 30

    def test_rejects_out_of_range_target(
        self, app, db, auth_client, seed_user,
    ):
        """target_periods = 0 fails validation; the row stays unchanged."""
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
            db.session.commit()
            resp = auth_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "0",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            schedule = pay_schedule_service.get_schedule(seed_user["user"].id)
            assert schedule.rolling_enabled is False

    def test_no_schedule_row_flashes_error(
        self, app, bare_auth_client, bare_user,
    ):
        """Configuring rolling before generating a schedule is refused.

        **The owner is ``bare_user``, and which fixture holds this state is
        the point** (plan step ``pay_calendar:C4-b-1``).  It read
        ``seed_user``, which carried a bootstrap pay period and NO
        ``budget.pay_schedule`` row -- a pairing no application door can
        produce, because ``record_paydays`` writes the schedule row in the
        same call that records a payday.  ``bare_user`` has neither, which is
        the real state this guard exists for: somebody who has never generated
        a schedule.  The rolling window keeps periods generated ahead and
        needs a cadence to grow at, so the service refuses and the route
        flashes it; nothing is created.
        """
        with app.app_context():
            assert pay_schedule_service.get_schedule(
                bare_user["user"].id,
            ) is None
            resp = bare_auth_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "10",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Generate a pay-period schedule" in resp.data
            assert pay_schedule_service.get_schedule(
                bare_user["user"].id,
            ) is None

    def test_companion_cannot_set_schedule(self, app, companion_client):
        """A companion is not the owner -- the schedule route 404s (IDOR)."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/schedule",
                data={
                    "rolling_enabled": "true",
                    "rolling_target_periods": "10",
                },
            )
            assert resp.status_code == 404


class TestHistoryRoute:
    """POST /pay-periods/history (when the owner's paychecks started).

    Plan step **balance:X-bh-2**, ruling **balance:R-IA**.  The SECOND door
    onto ``budget.pay_schedule.history_opens_on``, and the only one an
    existing owner has: registration asks the question once, and every owner
    who signed up before the column existed holds ``NULL`` with no sign-up
    form left to revisit.
    """

    def test_it_saves_the_day_and_redirects(
        self, app, db, auth_client, seed_user,
    ):
        """The ordinary save, and the CONTROL for the refusals below.

        **Re-read on a FRESH session, which is the whole difference between
        this asserting a COMMIT and asserting a flush.**  An adversarial review
        of plan step balance:X-bh-2 deleted this route's ``db.session.commit()``
        and 817 cases stayed green: the test wraps its post in
        ``with app.app_context()``, Flask reuses an already-pushed app context
        rather than pushing the request's own, so ``teardown_appcontext`` never
        fires, Flask-SQLAlchemy's ``session.remove()`` never runs, and the
        request's FLUSH is still visible to a query in the same session.  In
        production the answer would be discarded at teardown while the page
        still flashed "Saved when your paychecks started."

        ``db.session.remove()`` discards anything uncommitted, so what the
        assertion below reads came off the database.  This is the project's
        own recorded lesson -- a staged mutation check cannot tell a rollback
        from a flush -- in a new instance.
        """
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "2023-06-03"},
            )

            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            db.session.remove()
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ).history_opens_on == date(2023, 6, 3)

    def test_an_empty_box_CLEARS_a_stored_day(
        self, app, db, auth_client, seed_user,
    ):
        """A cleared control is a real answer, not a missing one.

        An HTML form submits every control it renders, so the untouched field
        arrives as ``""`` -- and it has to become ``NULL`` rather than "leave
        it alone", because clearing it is how an owner says "I have been paid
        this way longer than the app needs to know".  A door that read the
        empty box as no-change would make the field unclearable from the UI.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_schedule_service.ensure_schedule_row(user_id)
            pay_schedule_service.set_history_opening(user_id, date(2023, 6, 3))
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/history", data={"history_opens_on": ""},
            )

            assert resp.status_code == 302
            # Fresh session: the CLEAR has to be committed too, and a flushed
            # NULL is indistinguishable from a committed one in the session
            # the request left behind.  See the case above.
            db.session.remove()
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None

    def test_a_day_outside_the_apps_calendar_flashes_and_stores_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """The schema bound as a rendered message, never an IntegrityError 500.

        An ``<input type="date">`` accepts a five-digit year, so this arrives
        from an ordinary browser rather than from a crafted post.
        """
        with app.app_context():
            pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
            db.session.commit()

            resp = auth_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "9999-01-01"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert pay_schedule_service.get_schedule(
                seed_user["user"].id,
            ).history_opens_on is None

    def test_a_day_after_the_first_payday_flashes_the_services_message(
        self, app, db, auth_client, seed_user,
    ):
        """Paychecks cannot have begun after the first one the app holds.

        The service's own sentence reaches the page, so the owner is told
        which day it conflicts with rather than being sent to look.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            record_paydays_across_a_hole(
                user_id=user_id,
                first_payday=date(2026, 7, 3),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            opening = min(p.start_date for p in all_periods(user_id))

            resp = auth_client.post(
                "/pay-periods/history",
                data={
                    "history_opens_on": (
                        opening + timedelta(days=1)
                    ).isoformat(),
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert opening.isoformat().encode() in resp.data
            assert pay_schedule_service.get_schedule(
                user_id,
            ).history_opens_on is None

    def test_no_schedule_row_flashes_error(
        self, app, bare_auth_client, bare_user,
    ):
        """A floor bounds a rhythm, and a row-less owner has no cadence.

        ``bare_user`` rather than ``seed_user`` for the reason
        ``TestScheduleRoute``'s sibling case gives: an owner holding a payday
        and no ``budget.pay_schedule`` row is a pairing no door can produce,
        and this guard is about somebody who has never generated a schedule.
        """
        with app.app_context():
            assert pay_schedule_service.get_schedule(
                bare_user["user"].id,
            ) is None

            resp = bare_auth_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "2023-06-03"},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Generate a pay-period schedule" in resp.data
            assert pay_schedule_service.get_schedule(
                bare_user["user"].id,
            ) is None

    def test_companion_cannot_set_the_history_opening(
        self, app, companion_client,
    ):
        """A companion is not the owner -- the route 404s (IDOR)."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/history",
                data={"history_opens_on": "2023-06-03"},
            )

            assert resp.status_code == 404

    def test_the_route_EXISTS_for_a_signed_in_owner(self, app, auth_client):
        """Pairs with the 404 above, which a moved route would leave passing.

        A 404 from the URL map and a 404 from ``require_owner`` are
        indistinguishable, so the ownership case alone would go on passing if
        this endpoint were renamed or removed.
        """
        with app.app_context():
            resp = auth_client.post(
                "/pay-periods/history", data={"history_opens_on": ""},
            )

            assert resp.status_code != 404


class TestResetRoute:
    """POST /pay-periods/reset (bounded full-schedule reset)."""

    def test_reset_rebuilds_and_reanchors(self, app, db, auth_client, seed_user):
        """A confirmed reset wipes everything and rebuilds from index 0.

        It proved the DEFERRED-FK commit path end to end through HTTP; rulings
        R-EH and R-EO deleted the FK, so what it proves now is that the rebuild
        succeeds over HTTP and the user's asserted balance survives it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account_id = seed_user["account"].id
            _future_periods(db.session, seed_user, count=4)
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
            assert "pay-periods" in resp.headers["Location"]
            db.session.expire_all()
            assert {p.start_date for p in all_periods(user_id)} == {
                date(2026, 6, 5), date(2026, 6, 19),
                date(2026, 7, 3), date(2026, 7, 17),
            }
            account = db.session.get(Account, account_id)
            assert cash_ledger.resolve_anchor(account).balance == Decimal("1000.00")

    def test_unconfirmed_reset_refused(self, app, db, auth_client, seed_user):
        """Without the confirm box, reset changes nothing."""
        with app.app_context():
            user_id = seed_user["user"].id
            _future_periods(db.session, seed_user, count=4)
            before = _starts(user_id)
            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "new_start_date": "2026-06-05",
                    "num_periods": "4",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(),
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Confirm the reset" in resp.data
            assert _starts(user_id) == before

    def test_settled_blocks_reset(self, app, db, auth_client, seed_user):
        """A settled transaction makes the service refuse; nothing changes."""
        with app.app_context():
            user_id = seed_user["user"].id
            periods = _future_periods(db.session, seed_user, count=4)
            add_txn(
                db.session, seed_user, periods[1], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()
            before = _starts(user_id)
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
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"settled transaction" in resp.data
            assert _starts(user_id) == before

    def test_schema_rejection_changes_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """A missing required field fails validation; nothing changes."""
        with app.app_context():
            user_id = seed_user["user"].id
            _future_periods(db.session, seed_user, count=4)
            before = _starts(user_id)
            resp = auth_client.post(
                "/pay-periods/reset",
                data={
                    "num_periods": "4", "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(), "confirm": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            assert _starts(user_id) == before

    def test_companion_cannot_reset(self, app, companion_client):
        """A companion is not the owner -- the reset route 404s (IDOR)."""
        with app.app_context():
            resp = companion_client.post(
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
            assert resp.status_code == 404


class TestRollingTriggerHooks:
    """The grid + dashboard entry points top up the rolling window."""

    def _setup_rolling_with_deficit(self, db_session, seed_user, target):
        """Current + one future period, rolling on at ``target`` (a deficit).

        idx 1 spans the frozen today (06-08..06-21) so a current period
        exists; idx 2 is the next future period.  A period not yet ended
        counts both, so the window starts at 2 and is short of ``target``.
        """
        record_paydays_across_a_hole(
            user_id=seed_user["user"].id, first_payday=date(2026, 6, 8),
            num_periods=2, rhythm=rhythm_of(14),
        )
        pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
        pay_schedule_service.set_rolling(
            seed_user["user"].id, enabled=True, target_periods=target,
        )
        db_session.commit()

    def test_grid_load_tops_up_window(self, app, db, auth_client, seed_user):
        """GET /grid with rolling on + a deficit fills the window to target."""
        with app.app_context():
            self._setup_rolling_with_deficit(db.session, seed_user, target=5)
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert _future_count(seed_user["user"].id) == 5

    def test_dashboard_load_tops_up_window(
        self, app, db, auth_client, seed_user,
    ):
        """GET /dashboard with rolling on + a deficit fills the window."""
        with app.app_context():
            self._setup_rolling_with_deficit(db.session, seed_user, target=5)
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert _future_count(seed_user["user"].id) == 5

    def test_grid_load_disabled_creates_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """GET /grid with rolling disabled leaves the schedule unchanged."""
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id, first_payday=date(2026, 6, 8),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
            db.session.commit()
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert _period_count(db.session, seed_user["user"].id) == before
