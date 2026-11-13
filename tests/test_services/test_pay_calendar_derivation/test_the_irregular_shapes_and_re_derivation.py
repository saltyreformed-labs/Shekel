"""The shapes the live data does not supply, each derived to its hand-computed values, tiling, and
projecting only its last end; whether yesterday's derived end holds after another payday is
written; and the shape catalogue's refusal of an unknown label.
"""

from datetime import date, timedelta

import pytest

from app.services.pay_calendar import DerivedPeriod, derive_periods
from tests._test_helpers import era_of, eras_of
from tests.oracles.pay_calendar_derivation import IRREGULAR_SHAPES, shape

from ._helpers import _OPENING


# ---------------------------------------------------------------------------
# TestIrregularShapeSweep
# ---------------------------------------------------------------------------


class TestIrregularShapeSweep:
    """The generated sweep over schedules the live data cannot supply."""

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_the_shape_derives_its_hand_computed_values(self, irregular):
        """Every derived index, end and projection flag matches by hand.

        The expected values in ``IRREGULAR_SHAPES`` were worked out on paper
        with the arithmetic written beside them, so this asserts VALUES rather
        than agreeing with whatever the code produced.
        """
        assert derive_periods(
            irregular.paydays, eras_of(irregular.paydays, irregular.cadence_days),
        ) == irregular.expected

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_the_derived_spans_tile_the_calendar(self, irregular):
        """Consecutive derived periods abut exactly: no gap, no overlap.

        This is the normalization's whole claim, asserted rather than argued.
        Three of these shapes were the payday sets behind a stored hole, a
        shared boundary day and an ordinal out of date order, and the
        derivation of each tiles -- because a set of distinct sorted dates
        cannot produce anything else.  That is why plan step
        ``pay_calendar:C4-c`` could delete the three ``integrity_check``
        anomalies and the four ``_pp_assert_structure`` invariants that
        policed those states: none of them is expressible.
        """
        derived = derive_periods(irregular.paydays, eras_of(irregular.paydays, irregular.cadence_days))
        for earlier, later in zip(derived, derived[1:]):
            assert earlier.end_date + timedelta(days=1) == later.start_date
            assert earlier.period_index + 1 == later.period_index

    @pytest.mark.parametrize(
        "irregular", IRREGULAR_SHAPES, ids=lambda s: s.label,
    )
    def test_exactly_the_last_end_is_projected(self, irregular):
        """Only the final period's end comes from the cadence.

        The flag is the one thing the two columns plan step
        ``pay_calendar:C4-c`` dropped could never have said.  True for the last
        period of a non-empty calendar and for no other -- and for none at all
        when the calendar is empty.
        """
        derived = derive_periods(irregular.paydays, eras_of(irregular.paydays, irregular.cadence_days))
        projected = [
            period for period in derived if period.end_is_projected
        ]
        assert len(projected) == (1 if derived else 0)
        if derived:
            assert projected[0] is derived[-1]

    def test_the_input_order_does_not_change_the_answer(self):
        """A calendar is a property of the payday SET, not of the query order.

        The reads that feed this order by ``start_date``; the derivation sorts
        for itself so a caller's query order cannot change the answer.
        """
        paydays = [
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 16)),
            (3, date(2026, 1, 30)),
        ]
        assert derive_periods(
            reversed(paydays), eras_of(paydays, 14),
        ) == derive_periods(paydays, eras_of(paydays, 14))

    def test_a_single_payday_derives_one_wholly_projected_period(self):
        """The state registration leaves a new owner in, spelled out.

        ``registration_service.register_user`` writes one bootstrap payday, so this is
        every new account's first calendar: one period, its end projected off
        the cadence because there is no second payday to close it.
        """
        assert derive_periods([(7, date(2026, 3, 26))], (era_of(date(2026, 3, 26), 14),)) == (
            # 2026-03-26 + (14 - 1) days = 2026-04-08.
            DerivedPeriod(
                period_id=7,
                period_index=0,
                start_date=date(2026, 3, 26),
                end_date=date(2026, 4, 8),
                end_is_projected=True,
            ),
        )

    def test_an_empty_payday_set_derives_an_empty_calendar(self):
        """A companion holds no paydays of its own, and that is not an error.

        Measured on production 2026-08-08: user 2 is a companion with zero
        paydays, so no step may assume every user row has a schedule.
        """
        assert derive_periods([], (era_of(_OPENING, 14),)) == ()


# ---------------------------------------------------------------------------
# TestReDerivationStability
# ---------------------------------------------------------------------------


class TestReDerivationStability:
    """Does yesterday's answer still hold after another payday is written?

    The axis every other test here is blind to, and the only one on which a
    derived end can behave differently from the stored column it replaces: a
    stored ``end_date`` cannot move when a neighbour is written, a derived one
    can.  Named by adversarial review of this step, 2026-08-08.

    The four cases below partition every append: exactly one cadence later (the
    only stable one, and the one ``extend_pay_periods`` takes), earlier than
    that, later than that, and anywhere at all for the rows that are not last.
    """

    #: The schedule every case re-derives from: two biweekly paydays, so the
    #: second period's end is the projected one under test.
    _PAYDAYS = ((1, date(2026, 1, 2)), (2, date(2026, 1, 16)))

    def _end_of(self, paydays, payday):
        """Return the derived end of *payday* within *paydays*.

        Args:
            paydays: The payday set to derive.
            payday: The payday whose period's end to return.

        Returns:
            The derived ``end_date``.
        """
        derived = derive_periods(paydays, eras_of(paydays, 14))
        return next(
            period.end_date for period in derived
            if period.start_date == payday
        )

    def test_appending_exactly_one_cadence_later_moves_no_end(self):
        """The extend path's append is the only stable one.

        ``pay_period_admin.extend_pay_periods`` records the plan's own next
        payday (``pay_period_write.continue_paydays``, plan step ``C17-c-2b``;
        it stepped the NOMINAL grid one cadence after the last recorded
        payday from ``C14-d`` until then, and read ``last.end_date + 1`` off
        the calendar before that -- the same day while nothing displaces a
        payday).  The old last period's end is then ``lead - 1``, the same day
        the projection gave it -- so nothing moves and only the FLAG changes.
        """
        before = derive_periods(self._PAYDAYS, eras_of(self._PAYDAYS, 14))
        assert before[-1].end_date == date(2026, 1, 29)
        assert before[-1].end_is_projected is True

        after = derive_periods([*self._PAYDAYS, (3, date(2026, 1, 30))], eras_of([*self._PAYDAYS, (3, date(2026, 1, 30))], 14))
        assert after[1].start_date == date(2026, 1, 16)
        # lead - 1 = 2026-01-30 - 1 = 2026-01-29, the day the projection gave.
        assert after[1].end_date == date(2026, 1, 29)
        assert after[1].end_is_projected is False

    def test_appending_inside_the_projected_span_shortens_the_last_end(self):
        """A forward-only append can still move an end BACKWARD.

        2026-01-28 is later than every existing payday, so it is forward-only
        by the plan's own definition and is not the mid-schedule insert C6
        rules on.  It still moves the second period's end from 2026-01-29 to
        2026-01-27.  A row dated 2026-01-28 and pointed at that period is not
        left outside it -- ``DerivedPeriod.attribution_day`` clamps -- so it
        RENDERS on 2026-01-27 instead, which is plan finding P10's damage
        reached through a door P10 does not cover.

        ``_reject_overlapping_batch`` blocks this write today because it
        compares against the STORED end; plan step C3 deletes that guard.
        """
        assert self._end_of(self._PAYDAYS, date(2026, 1, 16)) == date(2026, 1, 29)
        after = [*self._PAYDAYS, (3, date(2026, 1, 28))]
        # lead - 1 = 2026-01-28 - 1 = 2026-01-27, two days earlier.
        assert self._end_of(after, date(2026, 1, 16)) == date(2026, 1, 27)

    def test_appending_beyond_the_projected_span_lengthens_the_last_end(self):
        """The mirror case, and the reason the stable append is a single day.

        2026-02-05 leaves a would-be hole under the old model; under this one
        the preceding period simply runs on to 2026-02-04.  Correct, and still
        a moved end -- so "append forward and nothing changes" is false in both
        directions, not only downward.
        """
        after = [*self._PAYDAYS, (3, date(2026, 2, 5))]
        # lead - 1 = 2026-02-05 - 1 = 2026-02-04, six days later.
        assert self._end_of(after, date(2026, 1, 16)) == date(2026, 2, 4)

    @pytest.mark.parametrize(
        "appended",
        [(3, date(2026, 1, 28)), (3, date(2026, 1, 30)), (3, date(2026, 2, 5))],
        ids=["inside", "exactly_one_cadence", "beyond"],
    )
    def test_no_end_but_the_last_one_can_move(self, appended):
        """Every non-final end is fixed for as long as its successor is.

        The first period's end is ``lead(2026-01-16) - 1`` whatever is appended
        after it, so it is 2026-01-15 in all three cases.  That is the bound on
        the instability: only the last period's span is exposed, which is what
        makes ``end_is_projected`` the marker for it.
        """
        after = [*self._PAYDAYS, appended]
        assert self._end_of(after, date(2026, 1, 2)) == date(2026, 1, 15)
        assert self._end_of(self._PAYDAYS, date(2026, 1, 2)) == date(2026, 1, 15)


# ---------------------------------------------------------------------------
# TestTheShapeCatalogueRefusesAnUnknownLabel
# ---------------------------------------------------------------------------


class TestTheShapeCatalogueRefusesAnUnknownLabel:
    """An instrument that lies is worse than none.

    *This class held the COMPARATOR's guards -- two owners' rows merged into
    one payday set, and an unsaved row carrying no owner -- until plan step
    ``pay_calendar:C4-c`` deleted the comparator with the columns it compared.
    It also held the oracle's verdict rule, which decided whether a run over a
    production clone counted; there is no such run any more.  What survives is
    the one guard that is about the shape CATALOGUE rather than about the
    comparison.*
    """

    def test_an_unknown_shape_label_raises_rather_than_skipping(self):
        """A renamed shape must not silently drop the test that used it."""
        with pytest.raises(KeyError, match="no IrregularShape labelled"):
            shape("no_such_shape")
