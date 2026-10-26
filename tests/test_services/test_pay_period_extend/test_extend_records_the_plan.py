"""Extend records a prefix of the planned paydays (C17-c-2b, R-PC75, R-PC78): the covering era's
remaining paydays and then a later era's first, minting and retiring no era, with its event
naming the covering era and its batch bound asked before any read.
"""

from __future__ import annotations

import logging
from datetime import date
from itertools import islice

import pytest

from app.exceptions import ValidationError
from app.services import (
    pay_era_write,
    pay_period_admin,
    pay_period_batch,
    pay_period_rolling,
    pay_period_write,
    pay_schedule_service,
)
from app.services.pay_calendar import calendar_for, planned_paydays_after, schedule_for
from tests._test_helpers import (
    record_paydays_across_a_hole,
    rhythm_of,
    assert_pay_period_invariants,
)


class TestExtendMaterialisesThePlan:
    """Plan step **C17-c-2b** (rulings **R-PC75**, **R-PC78**): extend records the plan.

    ``pay_period_admin.extend_pay_periods`` is the lock and one call to
    ``pay_period_write.continue_paydays``, which records a prefix of
    :func:`~app.services.pay_calendar.planned_paydays_after` -- the one
    sequence the writer's floor reads its first day from and its ceiling the
    second.  The door states no first payday and no rhythm, so it mints and
    retires nothing, and for an owner truncated BELOW a later era it records
    the covering era's remaining paydays and then the later era's first,
    exactly as the regenerate that minted the later era wrote them (ledger
    row **PC-509**, closed; ruling **R-PC76**'s transitional hazard, closed).

    The fixture is PC-509's own: a 14-day era from 2030-01-03, a 7-day era
    from 02-22 (off the 14-day grid), the record cut back to 01-17.  The
    plan after 01-17 is 01-31 (the 14-day era's own next payday), then
    02-22 (the 7-day era's first, which REPLACES the 14-day era's 02-14 --
    R-PC75), then 03-01.
    """

    _ERAS = [(date(2030, 1, 3), 14), (date(2030, 2, 22), 7)]

    def _piecewise_owner_cut_to_the_first_era(self, db_session, user_id):
        """Record 14 days x2 from 01-03, 7 days x2 from 02-22, truncate to 01-17.

        Args:
            db_session: The test ``db.session``.
            user_id: The owner.
        """
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2030, 1, 3),
            num_periods=2, rhythm=rhythm_of(14),
        )
        # 2030-02-22 is 36 days after 01-17: off the 14-day grid, so the
        # writer would mint there -- and at or past the plan's second payday
        # (02-14), so the writer refuses it; the tree helper builds it.
        record_paydays_across_a_hole(
            user_id=user_id, first_payday=date(2030, 2, 22),
            num_periods=2, rhythm=rhythm_of(7),
        )
        db_session.commit()
        keep = next(
            period for period in calendar_for(user_id).saved()
            if period.start_date == date(2030, 1, 17)
        )
        pay_period_admin.truncate_pay_periods(user_id, keep.period_id)
        db_session.commit()
        assert self._eras_of(user_id) == self._ERAS

    @staticmethod
    def _eras_of(user_id):
        """Return ``[(effective_from, cadence_days)]`` for *user_id*, ascending."""
        return [
            (era.effective_from, era.rhythm.cadence.days)
            for era in schedule_for(user_id).eras
        ]

    def test_it_records_the_covering_eras_paydays_then_the_later_eras_first(
        self, app, db, bare_user,
    ):
        """01-31 first; then 02-22 and 03-01 -- what the regenerate wrote, re-recorded.

        Two extends rather than one of three, because "the door continues
        from where the last one stopped" is a claim about the SECOND call:
        after 01-31 the covering era's plan is spent (02-14 is replaced), so
        the next call opens the 7-day era at its own first payday.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._piecewise_owner_cut_to_the_first_era(db.session, user_id)

            first = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()
            second = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()

            assert [p.start_date for p in first] == [date(2030, 1, 31)]
            assert [p.start_date for p in second] == [
                date(2030, 2, 22), date(2030, 3, 1),
            ]
            calendar = calendar_for(user_id)
            assert calendar.periods[-2].end_date == date(2030, 2, 28)
            assert calendar.periods[-1].end_date == date(2030, 3, 7)
            assert_pay_period_invariants(db.session, user_id)

    def test_it_mints_and_retires_NOTHING(self, app, db, bare_user):
        """The eras describe the plan before and after: PC-509's mint-and-retire is gone.

        The old door recorded 02-01, minted a third era there and retired
        the stated 02-22 one; across both calls here the two eras the owner
        stated are exactly what stands, and the ``pay_periods_generated``
        event says so.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._piecewise_owner_cut_to_the_first_era(db.session, user_id)

            pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert self._eras_of(user_id) == self._ERAS

    def test_the_days_are_a_prefix_of_the_sequence_both_fences_read(
        self, app, db, bare_user,
    ):
        """The floor and the ceiling hold by IDENTITY, so neither is asked.

        The door's days are ``planned_paydays_after`` over the stored eras
        from the last recorded payday; the floor is that sequence's first
        element and the ceiling its second, so the earliest day recorded is
        the floor itself and falls before the ceiling.  Both fences are run
        here over the door's own batch exactly as ``record_paydays`` runs
        them -- the floor over the STORED eras, the ceiling over the eras
        the batch leaves STANDING (``eras_describing``, which for this owner
        excludes the 7-day era until its first payday is recorded) -- and
        accept it: the tautology, stated once so a later change to either
        side fires here.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._piecewise_owner_cut_to_the_first_era(db.session, user_id)
            facts = schedule_for(user_id)
            record = {
                period.start_date for period in calendar_for(user_id).saved()
            }
            standing = pay_era_write.eras_describing(facts, record)
            assert [era.rhythm.cadence.days for era in standing] == [14]
            expected = list(islice(
                planned_paydays_after(facts.eras, max(record)), 3,
            ))

            recorded = [
                p.start_date for p in pay_period_admin.extend_pay_periods(user_id, 3)
            ]
            db.session.commit()

            assert recorded == expected == [
                date(2030, 1, 31), date(2030, 2, 22), date(2030, 3, 1),
            ]
            pay_period_batch.reject_backward_payday(record, recorded, facts.eras)
            pay_period_batch.reject_skipped_paycheck(record, recorded, standing)

    def test_the_top_up_below_a_LONGER_cadence_era_no_longer_meets_the_ceiling(
        self, app, db, bare_user,
    ):
        """Ruling R-PC76's transitional hazard, closed: no refusal on the read path.

        A weekly era from 2030-01-04, a 30-day era minted at 02-01 (the plan's
        next payday after the weekly 01-25 -- the writer accepts it), the
        record cut back to 01-18.  The old door restated the 30-day era from
        the horizon and offered 02-01, skipping the weekly 01-25, and
        ``reject_skipped_paycheck`` refused it from ``/grid`` with no handler
        -- the CONTROL below runs that batch through the fence and it still
        refuses.  The top-up records the plan now: 01-25, then 02-01.
        (2030, so the truncate's lock classifier reads nothing as history.)
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 1, 4),
                num_periods=4, rhythm=rhythm_of(7),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 2, 1),
                num_periods=2, rhythm=rhythm_of(30),
            )
            db.session.commit()
            keep = next(
                period for period in calendar_for(user_id).saved()
                if period.start_date == date(2030, 1, 18)
            )
            pay_period_admin.truncate_pay_periods(user_id, keep.period_id)
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=3,
            )
            db.session.commit()
            record = {date(2030, 1, 4), date(2030, 1, 11), date(2030, 1, 18)}
            standing = pay_era_write.eras_describing(schedule_for(user_id), record)
            with pytest.raises(ValidationError, match="whole paycheck missing"):
                pay_period_batch.reject_skipped_paycheck(
                    record, [date(2030, 2, 1), date(2030, 3, 3)], standing,
                )

            appended = pay_period_rolling.top_up_rolling_window(
                user_id, as_of=date(2030, 1, 18),
            )
            db.session.commit()

            assert [p.start_date for p in appended] == [
                date(2030, 1, 25), date(2030, 2, 1),
            ]
            assert self._eras_of(user_id) == [
                (date(2030, 1, 4), 7), (date(2030, 2, 1), 30),
            ]

    def test_the_event_names_the_era_COVERING_the_first_day_recorded(
        self, app, db, bare_user, caplog,
    ):
        """``cadence_days=14`` for an owner whose LATEST era is the 7-day one.

        The event's rhythm is the one the first recorded payday was planned
        on; the latest era's would be wrong for exactly the owner this step
        exists for.  ``era_minted_from`` is ``None`` and ``retired`` is 0,
        which is the door's whole claim about the eras in one record.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._piecewise_owner_cut_to_the_first_era(db.session, user_id)

            with caplog.at_level(logging.INFO):
                pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_generated"
            ]
            # The rhythm as its kind's PHRASE (ruling R-PC82, plan step
            # pay_calendar:C17-d-2), on this emitter as on record_paydays'.
            assert events[-1].cadence == "every 14 days"
            assert events[-1].era_minted_from is None
            assert events[-1].retired == 0
            assert events[-1].count == 1
            assert events[-1].start_date == "2030-01-31"

    def test_the_batch_bound_is_asked_before_any_read(self, app, bare_user):
        """A caller mistake is refused first, on an owner with no schedule at all.

        ``bare_user`` holds no ``budget.pay_schedule`` row, so a door that
        read the schedule first would raise ``PayCalendarError`` here; the
        batch-size refusal arrives instead, as it does at ``record_paydays``.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_write.continue_paydays(bare_user["user"].id, 0)
