"""The settings section that manages the schedule: a companion's 404 on extend, the period list and
action forms, the truncate select's id values, the history and rolling prefills, the reset form
shown and hidden, and a period list that answers from the paydays and the owner's civil day
(C2-f3b).
"""

from __future__ import annotations

from datetime import date

from app.enums import StatusEnum
from app.utils.dates import display_today
from app.services import pay_period_write, pay_schedule_service
from app.routes import settings as settings_routes
from tests._test_helpers import rhythm_of, add_txn, all_periods, record_paydays_across_a_hole

from ._helpers import _future_periods


class TestOwnerOnlyAndUi:
    """Owner-only access and the manage-UI render."""

    def test_companion_cannot_extend(self, app, companion_client):
        """A companion is not the owner -- the route 404s."""
        with app.app_context():
            resp = companion_client.post(
                "/pay-periods/extend", data={"num_periods": "2"},
            )
            assert resp.status_code == 404

    def test_settings_section_renders_manage_ui(
        self, app, db, auth_client, seed_user,
    ):
        """The pay-periods section shows the period list and action forms."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Manage Schedule" in resp.data
            assert b"Extend forward" in resp.data
            assert b"Remove the tail" in resp.data
            assert b"Regenerate the tail" in resp.data
            # The rolling-window controls render too.
            assert b"Continuous rolling window" in resp.data
            assert b'name="rolling_target_periods"' in resp.data
            # And the pay-history card (plan step balance:X-bh-2), which is
            # the only door an already-registered owner has onto that column.
            assert b"When your paychecks started" in resp.data
            assert b'name="history_opens_on"' in resp.data

    def test_the_truncate_select_offers_ids_not_ordinals(
        self, app, db, auth_client, seed_user,
    ):
        """The destructive select's option VALUES are pay-period ids.

        Finding **P13**'s gate at the surface that produces it.  The service
        refusing an unresolvable id is only half the fix: if this select went
        back to rendering ``period_index`` as its value, every posted ordinal
        would resolve to no period and the control would break loudly rather
        than destroy the wrong rows -- but it would break.  Asserting the
        rendered value keeps the form and the schema on one key.

        The seeded owner's periods deliberately have ids that are NOT their
        ordinals (the bootstrap period is created first, and ids come from a
        shared sequence), so an ordinal render cannot coincidentally pass.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            owner_periods = all_periods(user_id)
            # The ids are NOT the ordinals: the bootstrap period is created
            # first and ids come from a shared sequence, so an ordinal render
            # cannot coincidentally pass.
            assert [period.id for period in owner_periods] != list(
                range(len(owner_periods)),
            )

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'name="keep_through_period_id"' in resp.data
            for period in owner_periods:
                assert f'<option value="{period.id}">'.encode() in resp.data

    def test_the_history_card_is_prefilled_from_the_schedule_row(
        self, app, db, auth_client, seed_user,
    ):
        """A stored opening comes back in the control, not as a blank box.

        Plan step **balance:X-bh-2**.  A form that renders empty over a stored
        value teaches the owner they have not answered, and the next Save
        CLEARS it -- the field's empty box is a real answer, so an unprefilled
        control is a silent write rather than a cosmetic bug.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _future_periods(db.session, seed_user, count=3)
            pay_schedule_service.set_history_opening(user_id, date(2023, 6, 3))
            db.session.commit()

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'value="2023-06-03"' in resp.data

    def test_the_history_card_renders_empty_when_nothing_is_stored(
        self, app, db, auth_client, seed_user,
    ):
        """THE CONTROL: the prefill is read, not a literal in the template."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            pay_schedule_service.set_history_opening(
                seed_user["user"].id, None,
            )
            db.session.commit()

            resp = auth_client.get("/settings?section=pay-periods")

            assert resp.status_code == 200
            assert b'id="history_opens_on"' in resp.data
            assert b'value="2023-06-03"' not in resp.data

    def test_rolling_controls_prefilled_from_schedule(
        self, app, db, auth_client, seed_user,
    ):
        """The rolling controls reflect the saved schedule (checked + target)."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            pay_schedule_service.ensure_schedule_row(seed_user["user"].id)
            pay_schedule_service.set_rolling(
                seed_user["user"].id, enabled=True, target_periods=40,
            )
            db.session.commit()
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            # Switch reflects enabled state; target input reflects 40.
            assert b"checked" in resp.data
            assert b'value="40"' in resp.data

    def test_reset_form_shown_when_no_settled(
        self, app, db, auth_client, seed_user,
    ):
        """With no settled txns, the full-reset form (with confirm) is offered."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Reset entire schedule" in resp.data
            assert b'name="confirm"' in resp.data
            assert b"/pay-periods/reset" in resp.data

    def test_reset_form_hidden_when_settled(
        self, app, db, auth_client, seed_user,
    ):
        """A settled txn hides the reset form and shows the Regenerate note."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            add_txn(
                db.session, seed_user, periods[1], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Reset is unavailable" in resp.data
            assert b'name="confirm"' not in resp.data


class TestTheManageListIsTheDerivation:
    """The rendered period list answers from the paydays, not the columns.

    Plan step **C2-f3b**.  This page is where a user reviews the schedule
    BEFORE pressing a destructive button on it, and the button's service decides
    against the derivation -- so the list beside it has to describe the same
    periods or the confirmation is about a different schedule from the one that
    changes.  Both halves of a rendered row are graded: the LABEL, which the ORM
    accessor built from the stored ``end_date`` and now derives, and the lock
    BADGE, whose
    "Past" chip was decided on the process clock.

    *The label half no longer has TWO sources to choose between -- plan step
    ``pay_calendar:C4-c`` deleted the stored column -- so it grades the two
    DERIVATION rules against each other instead.*
    """

    def test_the_label_ends_a_period_at_the_NEXT_PAYDAY(
        self, app, db, auth_client, seed_user,
    ):
        """A period's label ends the day before its successor's payday.

        **The schedule is deliberately OFF-CADENCE, and that is the whole
        control.**  Paydays 2026-07-03 and 2026-07-24 at a stored cadence of
        14: the first paycheck runs to 07-23, because that is the day before
        the next payday, while ``start + cadence - 1`` would say 07-16.  Both
        strings are asserted, so a render that took the projection rule for
        every period -- the pre-normalization defect -- fails on the negative
        half, and one that dropped the label entirely fails on the positive.

        *This case doctored a stored ``end_date`` down to 07-05 and asserted
        the label ignored it, until plan step ``pay_calendar:C4-c`` dropped the
        column.  There is no second value to ignore now, so the discriminator
        moved to the two DERIVATION rules -- which is the sharper question the
        doctored fixture was standing in for.*
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id, first_payday=date(2026, 7, 3),
                num_periods=1, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id, first_payday=date(2026, 7, 24),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "07/03 - 07/23" in html
            assert "07/03 - 07/16" not in html

    def test_the_past_badge_follows_the_owners_civil_day(
        self, app, db, auth_client, seed_user, monkeypatch,
    ):
        """"Past" is decided on ``display_today``, ruled 2026-08-19.

        The clocks are pinned APART: the process clock stays on this module's
        2026-06-15 and the display clock reads 2026-07-17.  TWO "Past" chips must
        render -- an exact count, so a badge map that marked everything
        historical fails here too.

        **Both chips are the display clock's, and an adversarial review of this
        step corrected a comment that said otherwise.**  ``_future_periods``
        records paydays through the writer, which re-materialises the whole
        calendar, so the 2024 bootstrap ends the day before the first generated
        payday -- 2026-07-02, not its seeded 2024-01-18.  On the process clock
        it has NOT ended either, which is why the mutation this case exists to
        kill reports ``0 == 2`` rather than ``1 == 2``.
        """
        monkeypatch.setattr(
            settings_routes, "display_today", lambda: date(2026, 7, 17),
        )
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The bootstrap (derived end 2026-07-02) and the first generated
            # period (2026-07-16); neither has ended on the process clock.
            assert html.count(">Past<") == 2
