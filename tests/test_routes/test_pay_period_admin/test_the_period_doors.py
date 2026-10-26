"""The doors that change how many paychecks the owner holds, end to end through HTTP: extend
appends and validates its count, truncate removes the tail behind a settled period's lock and
the confirm panel a hand-entered row raises (its re-POST, a regenerate panel's round trip and a
stale id among them), regenerate rebuilds the tail and refuses a start that skips a paycheck,
and generate persists the cadence.
"""

from __future__ import annotations

import re
from datetime import date

from app.enums import BusinessDayShiftEnum, StatusEnum
from app.services.pay_rhythm import FixedDays
from app.services import pay_period_admin, pay_schedule_service
from tests._test_helpers import shift_form_value, add_txn, all_periods

from ._helpers import _future_periods, _spanning_periods, _period_count, _starts


class TestExtendRoute:
    """POST /pay-periods/extend."""

    def test_adds_periods_and_redirects(self, app, db, auth_client, seed_user):
        """A valid extend appends periods and redirects to the section."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/extend", data={"num_periods": "2"},
            )
            assert resp.status_code == 302
            assert "pay-periods" in resp.headers["Location"]
            assert _period_count(
                db.session, seed_user["user"].id,
            ) == before + 2

    def test_rejects_out_of_range_count(self, app, db, auth_client, seed_user):
        """num_periods = 0 fails validation; nothing is added."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/extend", data={"num_periods": "0"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the form" in resp.data
            assert _period_count(db.session, seed_user["user"].id) == before


class TestTruncateRoute:
    """POST /pay-periods/truncate."""

    def test_removes_tail_and_redirects(self, app, db, auth_client, seed_user):
        """A valid truncate deletes everything past the named period."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            keep = periods[2].id  # the 3rd future period
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(keep)},
            )
            assert resp.status_code == 302
            assert max(_starts(seed_user["user"].id)) == periods[2].start_date

    def test_settled_period_blocked_nothing_deleted(
        self, app, db, auth_client, seed_user,
    ):
        """A settled period in the window flashes a lock error, deletes nothing."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(
                db.session, seed_user, periods[3], "Paid", "100.00",  # index 4
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(periods[1].id)},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"locked" in resp.data
            assert _period_count(db.session, seed_user["user"].id) == before

    def test_discard_shows_confirm_panel(
        self, app, db, auth_client, seed_user,
    ):
        """A hand-entered row triggers the 422 confirm panel, deletes nothing."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(db.session, seed_user, periods[3], "Cash", "50.00")
            db.session.commit()
            before = _period_count(db.session, seed_user["user"].id)
            resp = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(periods[1].id)},
            )
            assert resp.status_code == 422
            assert b"permanently discard" in resp.data
            assert b"Confirm" in resp.data
            # The panel must echo the ID the user chose, not its ordinal --
            # this hidden field IS the browser round trip finding P13 is
            # about, and asserting only the prose let it regress silently.
            assert (
                f'name="keep_through_period_id" value="{periods[1].id}"'
                .encode() in resp.data
            )
            assert _period_count(db.session, seed_user["user"].id) == before

    def test_the_regenerate_confirm_panel_round_trips_every_param(
        self, app, db, auth_client, seed_user,
    ):
        """The panel's hidden inputs are re-POSTable, ``shift`` included.

        **Plan step ``pay_calendar:C14-b`` made this reachable to get wrong.**
        The regenerate schema REQUIRES ``shift``, and the confirm banner
        re-posts the params dict the 422 handed it as hidden inputs -- so a
        params dict that omitted the field, or that echoed the enum MEMBER
        rather than the wire id a browser sends, would refuse every
        discard-confirm with "shift: Missing data for required field" while
        every other test stayed green.  The route converts the member back to
        its id for exactly this reason and nothing graded that conversion:
        the truncate twin above round-trips params that were all wire-native
        strings already.

        The values are parsed back out of the 422 body rather than passed by
        hand, for that test's reason -- passing them by hand tests the service
        twice and the round trip never.
        """
        with app.app_context():
            # A block the started 06-05 paycheck anchors, so the rebuild opens
            # inside the writer's window (plan step C17-c-2a): the plan's next
            # payday is 06-19 and Monday 06-22 moves it by three days.  Not
            # Saturday 06-20: posted with ``prior`` it would be displaced past
            # Juneteenth to Thursday 06-18, below the floor -- and a refusal
            # also redirects, which is why the success flash is asserted.
            _spanning_periods(db.session, seed_user, count=6)
            add_txn(db.session, seed_user, all_periods(
                seed_user["user"].id,
            )[-1], "Cash", "50.00")
            db.session.commit()
            start = date(2026, 6, 22).isoformat()

            confirm = auth_client.post("/pay-periods/regenerate", data={
                "new_start_date": start,
                "num_periods": "3",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(BusinessDayShiftEnum.PRIOR),
            })
            assert confirm.status_code == 422

            echoed = dict(re.findall(
                rb'<input type="hidden" name="([a-z_]+)" value="([^"]*)"',
                confirm.data,
            ))
            assert b"shift" in echoed, (
                "the confirm panel dropped the shift the owner chose, so the "
                "re-post cannot satisfy the regenerate schema"
            )
            assert echoed[b"shift"] == shift_form_value(
                BusinessDayShiftEnum.PRIOR,
            ).encode(), "the panel echoed something a browser would not post"

            resp = auth_client.post("/pay-periods/regenerate", data={
                key.decode(): value.decode() for key, value in echoed.items()
            } | {"confirm_discard": "true"}, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Rebuilt the schedule" in resp.data
            db.session.expire_all()
            assert pay_schedule_service.resolve_schedule(
                seed_user["user"].id,
            ).rhythm.shift is BusinessDayShiftEnum.PRIOR

    def test_confirm_discard_proceeds(self, app, db, auth_client, seed_user):
        """Re-posting what the confirm PANEL rendered completes the truncate.

        The id is parsed back out of the 422 body rather than passed by hand,
        so the assertion covers the whole round trip the finding is about: what
        the panel put on the wire is what the second post acts on.  Passing it
        by hand tested the service twice and the round trip never.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            add_txn(db.session, seed_user, periods[3], "Cash", "50.00")
            db.session.commit()
            confirm = auth_client.post(
                "/pay-periods/truncate",
                data={"keep_through_period_id": str(periods[1].id)},
            )
            assert confirm.status_code == 422
            echoed = re.search(
                rb'name="keep_through_period_id" value="(\d+)"', confirm.data,
            )
            assert echoed is not None

            resp = auth_client.post(
                "/pay-periods/truncate",
                data={
                    "keep_through_period_id": echoed.group(1).decode(),
                    "confirm_discard": "true",
                },
            )
            assert resp.status_code == 302
            assert max(_starts(seed_user["user"].id)) == periods[1].start_date

    def test_a_stale_period_id_is_refused_and_deletes_nothing(
        self, app, db, auth_client, seed_user,
    ):
        """The confirm panel's re-submitted id no longer names a period.

        Finding **P13**'s live shape at the HTTP boundary: the discard-confirm
        422 echoes the chosen period into a hidden field and the user posts it
        back, so the value crosses a request boundary that ``user_write_lock``
        cannot span.  Keyed on ``id`` (plan step C3-a) a period deleted in
        between is REFUSED; keyed on the old ordinal the same post named
        whichever period had since slid into that position.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            # The user reviews period #4 in the confirm panel; a concurrent
            # truncate then removes it and everything after.
            reviewed_id = periods[3].id
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()
            before = _period_count(db.session, user_id)

            resp = auth_client.post(
                "/pay-periods/truncate",
                data={
                    "keep_through_period_id": str(reviewed_id),
                    "confirm_discard": "true",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"no longer exists" in resp.data
            assert _period_count(db.session, user_id) == before


class TestRegenerateRoute:
    """POST /pay-periods/regenerate."""

    def test_rebuilds_tail_and_redirects(self, app, db, auth_client, seed_user):
        """Regenerate rebuilds the future tail from the corrected start."""
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=6)
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
            # Bootstrap and the started 06-05 paycheck survive; the 5 future
            # periods become 3.
            assert len(all_periods(
                seed_user["user"].id,
            )) == 5

    def test_a_start_that_skips_a_paycheck_is_refused_whatever_the_form_posts(
        self, app, db, auth_client, seed_user,
    ):
        """A hole is REFUSED, and no field confirms it (plan step C17-c-2a).

        Until this step the route rendered a second confirmation banner and
        re-posted ``confirm_gap=true``; the field is ignored now (the
        schema's P29 disposition), the writer's ceiling refuses the batch,
        and the flash carries the day the owner must stay before.  The tail
        is untouched: every refusal in ``record_paydays`` runs before its
        first statement, so nothing was staged to roll back.
        """
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=6)
            before = {p.start_date for p in all_periods(seed_user["user"].id)}
            resp = auth_client.post(
                "/pay-periods/regenerate",
                data={
                    "new_start_date": "2026-07-03",
                    "num_periods": "3",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "14",
                    "shift": shift_form_value(),
                    "confirm_gap": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"must fall before 2026-07-03" in resp.data
            assert b"Confirm &amp; leave the gap" not in resp.data
            db.session.expire_all()
            assert {
                p.start_date for p in all_periods(seed_user["user"].id)
            } == before


class TestGenerateRoute:
    """POST /pay-periods/generate persists the cadence."""

    def test_generate_persists_cadence(self, app, bare_auth_client, bare_user):
        """Generating captures the cadence in a pay_schedule row.

        **Re-pointed from ``seed_user`` to ``bare_user`` at plan step
        ``pay_calendar:C14-f``** (ruling **R-PC63**).  The SUBJECT is
        unchanged -- establishing a rhythm still captures its cadence -- and
        what moved is which owner reaches that branch.  ``seed_user`` already
        holds paydays, so this door now CONTINUES their rhythm and never
        touches ``cadence_days``; asserting otherwise would be asserting that
        an owner may restate a phase they already have, which is what ledger
        row **P80** was.  ``bare_user`` holds none, so this is the ESTABLISH
        path the case always meant to cover.  That an owner WITH a rhythm no
        longer moves the cadence here is pinned in
        ``test_pay_periods.py::test_generate_can_only_WIDEN_the_covered_interval``.
        """
        with app.app_context():
            resp = bare_auth_client.post(
                "/pay-periods/generate",
                data={
                    "start_date": "2027-01-01",
                    "num_periods": "4",
                    "cadence_kind": "fixed_days",
                    "cadence_days": "10",
                    "shift": shift_form_value(),
                },
            )
            assert resp.status_code == 302
            schedule = pay_schedule_service.get_schedule(bare_user["user"].id)
            assert schedule is not None
            assert pay_schedule_service.resolve_cadence(
                bare_user["user"].id,
            ) == FixedDays(10)
