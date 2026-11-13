"""The paydays the real writer records, derived to hand-computed ends on both branches:
production's own 63-payday shape, every storable cadence and a second contiguous batch; and the
cadence control that separates the two end branches.
"""

from datetime import date, timedelta

import pytest

from app.services import pay_period_write
from app.services.pay_calendar import MAX_CADENCE_DAYS, derive_periods, schedule_for
from tests._test_helpers import all_periods, rhythm_of
from tests.oracles.pay_calendar_derivation import cadence_control, shape


#: Production's own schedule, measured on ``shekel-prod-db`` 2026-09-01: 63
#: paydays from 2026-03-26 at a 14-day cadence, the last on 2028-08-10 running
#: to 2028-08-23.  Reproduced here so the suite asserts the real shape rather
#: than a convenient one.
_LIVE_FIRST_PAYDAY = date(2026, 3, 26)


_LIVE_PERIOD_COUNT = 63


_LIVE_CADENCE_DAYS = 14


_LIVE_LAST_PAYDAY = date(2028, 8, 10)


_LIVE_LAST_END = date(2028, 8, 23)


#: ``(cadence_days, first period's end, last period's end)`` for six periods
#: opening 2026-01-02.  The FIRST end exercises the ``lead(start) - 1`` branch
#: and the LAST the ``start + cadence - 1`` projection, so each row pins both.
#:
#: **Cadence 1 is here because plan step ``pay_calendar:C4-c`` legalised it**
#: (plan finding **P9**).  It was excluded while the writer stored
#: ``end_date = start_date`` for a one-day cycle and
#: ``ck_pay_periods_date_order CHECK (start_date < end_date)`` rejected the
#: row; the derivation always handled it, and now the writer can record it.
#: Six paydays a day apart open 2026-01-02 and run to 2026-01-07, and every
#: period covers exactly its own payday.
_CADENCE_ANCHORS = (
    (1, date(2026, 1, 2), date(2026, 1, 7)),
    (2, date(2026, 1, 3), date(2026, 1, 13)),
    (7, date(2026, 1, 8), date(2026, 2, 12)),
    (14, date(2026, 1, 15), date(2026, 3, 26)),
    (30, date(2026, 1, 31), date(2026, 6, 30)),
    (90, date(2026, 4, 1), date(2027, 6, 25)),
    (365, date(2027, 1, 1), date(2031, 12, 31)),
)


# ---------------------------------------------------------------------------
# TestTheWritersOwnScheduleDerives
# ---------------------------------------------------------------------------


class TestTheWritersOwnScheduleDerives:
    """The derivation against the paydays the real writer actually records.

    **This class compared the derivation with the STORED columns until plan
    step ``pay_calendar:C4-c``**, which dropped them.  What it grades now is
    the other half of that pairing and the half that was always load-bearing:
    the writer's own output, driven through
    ``pay_period_write.record_paydays`` and pinned to dates worked out by hand.
    A cutover that quietly changed which paydays the writer records fails here.
    """

    def test_the_production_schedule_derives_its_known_shape(
        self, app, db, bare_user,
    ):
        """Production's own 63-payday shape, rebuilt through the writer.

        Measured on ``shekel-prod-db`` 2026-09-01: 63 paydays from 2026-03-26
        at cadence 14, the last on 2028-08-10.  ``2026-03-26 + 62 * 14 days``
        is that day, and its period's projected end is thirteen days later.
        """
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=_LIVE_FIRST_PAYDAY,
                num_periods=_LIVE_PERIOD_COUNT,
                rhythm=rhythm_of(_LIVE_CADENCE_DAYS),
            )
            db.session.commit()

            assert len(periods) == _LIVE_PERIOD_COUNT
            assert periods[-1].start_date == _LIVE_LAST_PAYDAY

            derived = derive_periods(
                [(period.id, period.start_date) for period in periods],
                schedule_for(bare_user["user"].id).eras,
            )
            assert len(derived) == _LIVE_PERIOD_COUNT
            assert derived[-1].end_date == _LIVE_LAST_END
            assert derived[-1].end_is_projected is True
            assert [period.period_index for period in derived] == list(
                range(_LIVE_PERIOD_COUNT),
            )

    @pytest.mark.parametrize(
        "cadence_days,first_end,last_end",
        _CADENCE_ANCHORS,
        ids=[f"cadence_{row[0]}" for row in _CADENCE_ANCHORS],
    )
    def test_every_storable_cadence_derives_hand_computed_ends(
        self, app, db, bare_user, cadence_days, first_end, last_end,
    ):
        """The writer's paydays derive to known dates on BOTH branches.

        The writer spaces its paydays exactly one cadence apart, so
        ``lead(start) - 1`` and ``start + cadence - 1`` agree on every row it
        writes -- which is why each cadence pins the FIRST end (the ``lead``
        branch) and the LAST (the projection) by hand rather than pinning that
        the two agree.  A derivation that took the projection branch everywhere
        would still be caught, by the shapes in
        :class:`TestIrregularShapeSweep` and by
        :class:`TestTheCadenceControl`.

        **Cadence 1 is one of the seven rows since plan step
        ``pay_calendar:C4-c``** (plan finding **P9**): the writer refused it
        while a stored ``end_date`` could not hold a one-day period.
        """
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=6,
                rhythm=rhythm_of(cadence_days),
            )
            db.session.commit()

            derived = derive_periods(
                [(period.id, period.start_date) for period in periods],
                schedule_for(bare_user["user"].id).eras,
            )
            assert derived[0].end_date == first_end
            assert derived[0].end_is_projected is False
            assert derived[-1].end_date == last_end
            assert derived[-1].end_is_projected is True

    def test_a_second_contiguous_batch_continues_the_rhythm(
        self, app, db, bare_user,
    ):
        """The extend path's shape -- a batch opening one cadence after the last.

        ``pay_period_admin.extend_pay_periods`` asks a PRODUCER where the next
        payday falls rather than doing arithmetic of its own (plan step
        C2-f3b; the plan's own sequence since ``C17-c-2b``, the NOMINAL grid
        from ``C14-d`` until then).  This is the append that moves no
        previously-derived end; see :class:`TestReDerivationStability`.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            first = pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            pay_period_write.record_paydays(
                user_id=user_id,
                first_payday=first[-1].start_date + timedelta(days=14),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            derived = derive_periods(
                [
                    (period.id, period.start_date)
                    for period in all_periods(user_id)
                ],
                schedule_for(user_id).eras,
            )
            assert [period.start_date for period in derived] == [
                date(2026, 1, 2) + timedelta(days=14 * step)
                for step in range(6)
            ]
            assert [period.period_index for period in derived] == list(range(6))
            # Every end but the last is its successor's payday minus a day.
            for earlier, later in zip(derived, derived[1:]):
                assert earlier.end_date == later.start_date - timedelta(days=1)
            assert derived[-1].end_date == derived[-1].start_date + timedelta(
                days=13,
            )


# ---------------------------------------------------------------------------
# TestTheCadenceControl
# ---------------------------------------------------------------------------


class TestTheCadenceControl:
    """The control that separates the two end branches.

    On a regular schedule ``lead(start) - 1`` and the grid's next payday agree
    on every row, so a run over one cannot say which branch produced an end --
    production's 63 paydays are exactly that shape.  Re-deriving at a
    neighbouring cadence separates them: exactly one end may move, and it
    moves to the day before the PROBE grid's next payday.  *That was "by
    exactly one day" until plan step ``C17-b-2`` anchored the last end on the
    era's grid (ruled 2026-09-11): the literal was the recorded anchor's
    arithmetic, and the expected day is read off a listing of the grid now.*
    """

    def test_exactly_one_end_moves_to_the_probe_grids_next_payday(self):
        """The correct outcome, on the ordinary schedule.

        Hand-computed.  The grid from 2026-01-02 at 15 days is 01-17, 02-01,
        02-16, 03-03, 03-18; the last record 02-27 is four days from 03-03 and
        eleven from 02-16, so it stands for 03-03 and the probe closes the
        period on 03-17.  At 14 days it closes on 03-12: five days.
        """
        control = cadence_control(shape("biweekly_five").paydays, 14)
        assert control.applicable is True
        assert control.probe_cadence == 15
        assert control.expected_shift_days == 5
        # Only the last period reads the cadence, so only its end moves.
        assert control.moved == ((date(2026, 2, 27), 5),)
        assert control.fired is True

    def test_it_probes_downward_at_the_top_of_the_stored_range(self):
        """365 is the ceiling, so the probe has to go the other way.

        Without this the control would ask for a cadence the schema forbids and
        the derivation would refuse it, turning the instrument into a crash.
        """
        control = cadence_control(
            [(1, date(2026, 1, 2)), (2, date(2027, 1, 2))], MAX_CADENCE_DAYS,
        )
        assert control.probe_cadence == MAX_CADENCE_DAYS - 1
        # Hand-computed: the 364-day grid from 2026-01-02 pays 2027-01-01 and
        # 2027-12-31; the 2027-01-02 record stands for 2027-01-01, so the
        # probe closes on 2027-12-30 where 365 closes on 2028-01-01.
        assert control.expected_shift_days == -2
        assert control.moved == ((date(2027, 1, 2), -2),)
        assert control.fired is True

    def test_it_is_inapplicable_to_an_empty_calendar(self):
        """No periods, no end to move -- reported, not scored."""
        control = cadence_control([], 14)
        assert control.applicable is False
        assert control.fired is False
        assert control.moved == ()

    def test_it_fires_on_a_single_payday_schedule(self):
        """One period, whose only end is the projected one.

        Every fresh signup is a one-payday owner (``registration_service.register_user``
        writes one bootstrap payday), so this is the common shape rather than a
        corner.
        """
        control = cadence_control([(1, date(2026, 3, 26))], 14)
        assert control.moved == ((date(2026, 3, 26), 1),)
        assert control.fired is True
