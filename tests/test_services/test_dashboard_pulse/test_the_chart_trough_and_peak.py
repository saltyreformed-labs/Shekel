"""The projected end-balance chart and its threshold, and the lowest and highest projected end
balances over the full forward horizon.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.services import dashboard_service
from app.services.dashboard_service import _pulse
from app.services.pay_calendar import PayCadence
from tests._test_helpers import (
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    last_covered_day,
    dashboard_section,
    period_window,
)
from app.services.pay_rhythm import FixedDays

from ._helpers import _CURRENT_IDX


# ── Chart: 13-point shape, degradation, threshold ──────────────────


#: The developer's own cadence, threaded into every ``_chart`` unit case.
#:
#: ``_chart`` took no cadence until plan step **R-F17** -- its window was the
#: hardcoded ``_CHART_HORIZON_PERIODS = 13``.  Passing the real value object
#: rather than a stand-in keeps these cases on the production derivation, and
#: at 14 days it answers the same 13 points they were written against.
_BIWEEKLY = PayCadence(FixedDays(14))


class TestPulseChart:
    """The projected end-balance chart series and threshold passthrough."""

    def _periods_and_balances(self, periods, per_period_balance="100.00"):
        """Build a forward DERIVED-period list and a flat end-balance map.

        The producer's forward set is ``balance_ctx.reported_periods()`` since
        pay-calendar plan step C2-f2e -- a window of
        :class:`~app.services.pay_calendar.DerivedPeriod`, not the ORM rows --
        so the helper it feeds is handed the same type production hands it,
        and the map is keyed by ``period_id`` as the seam's own maps are.
        """
        derived = list(period_window(periods))
        end_balances = {
            p.period_id: Decimal(per_period_balance) for p in derived
        }
        return derived, end_balances

    def test_the_chart_window_follows_the_owners_cadence(
        self, app, seed_user, db,
    ):
        """A WEEKLY owner gets 26 points, not 13 -- the same SIX MONTHS.

        Plan step **R-F17**, ledger row **F-17**.  The window was
        ``_CHART_HORIZON_PERIODS = 13`` while ``dashboard/_pulse.html``'s
        canvas announced "the next six months" to every screen reader beside
        it: at this cadence 13 periods is 91 days, so the chart plotted three
        months under a label saying six.  Both adversarial reviews of this step
        found it by re-grepping the ledger row's own predicate rather than the
        diff -- the row could not honestly close while it stood.

        26 weekly periods is 182 days, the same span the biweekly owner's 13
        cover, which is the whole point.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=40,
                rhythm=rhythm_of(7),
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            chart = _pulse._chart(
                forward, balances, None, PayCadence(FixedDays(7)),
            )

            assert len(chart["points"]) == 26
            # 26 x 7 == 182 == 13 x 14: one span, two cadences.
            span = (
                chart["points"][-1]["end_date"] - periods[0].start_date
            ).days + 1
            assert span == 182

    def test_a_paycheck_longer_than_the_span_still_draws_one_point(
        self, app, seed_user, db,
    ):
        """A 300-day cadence reaches no paycheck inside six months: draw one.

        The same POLICY the mobile Plan tab keeps and the grid's range buttons
        do NOT: a chart on the dashboard must draw something, where a button
        may simply not be offered (ruling **R-R31**).
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=4,
                rhythm=rhythm_of(300),
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            assert PayCadence(FixedDays(300)).paychecks_within(6) == 0
            chart = _pulse._chart(
                forward, balances, None, PayCadence(FixedDays(300)),
            )

            assert len(chart["points"]) == 1

    def test_chart_caps_at_the_owners_paychecks_in_the_span(
        self, app, seed_user, db,
    ):
        """13 points at a BIWEEKLY cadence, even with more periods available.

        Generate 20 forward periods and assert the chart returns exactly 13 --
        which is how many paychecks fall inside
        :data:`~app.services.dashboard_service._pulse._CHART_HORIZON_MONTHS`
        for this owner, and exactly the ``_CHART_HORIZON_PERIODS = 13`` literal
        that stood here until plan step **R-F17**.  13 x 14 = 182 days, so the
        canvas's "next six months" aria-label is true for them.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=20,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 13
            # The points are the first 13 periods, in order.
            assert [pt["end_date"] for pt in chart["points"]] == [
                last_covered_day(p) for p in periods[:13]
            ]

    def test_chart_fewer_periods_degrades(self, app, seed_user, seed_periods, db):
        """With only 5 forward periods the chart returns 5 points, not 13.

        seed_periods[5..9] is 5 periods; the chart returns one point each
        (fewer-than-13 degradation), each carrying that period's end
        balance.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            assert len(forward) == 5
            balances = {p.period_id: Decimal("250.00") for p in forward}

            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 5
            assert all(
                pt["balance"] == Decimal("250.00") for pt in chart["points"]
            )

    def test_chart_threshold_passthrough(self, app, seed_user, seed_periods, db):
        """The user's low_balance_threshold flows through as a Decimal.

        seed_user's settings default low_balance_threshold to 500 (a
        whole-dollar integer column); the chart surfaces it as
        Decimal("500").
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            balances = {p.period_id: Decimal("100.00") for p in forward}
            settings = seed_user["settings"]

            chart = _pulse._chart(
                forward, balances, settings, _BIWEEKLY,
)
            assert chart["low_balance_threshold"] == Decimal("500")

    def test_chart_threshold_tracks_configured_value(
        self, app, seed_user, seed_periods, db,
    ):
        """The chart threshold tracks the configured setting, not the default.

        Setting low_balance_threshold to 800 (a non-default whole-dollar
        integer) surfaces Decimal("800") on the chart -- the dashed line
        updates dynamically with the user's configured value.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            balances = {p.period_id: Decimal("100.00") for p in forward}
            settings = seed_user["settings"]
            settings.low_balance_threshold = 800
            db.session.commit()

            chart = _pulse._chart(
                forward, balances, settings, _BIWEEKLY,
)
            assert chart["low_balance_threshold"] == Decimal("800")

    def test_chart_threshold_none_without_settings(
        self, app, seed_user, seed_periods, db,
    ):
        """With no settings the threshold is None (no dashed line drawn)."""
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            balances = {p.period_id: Decimal("100.00") for p in forward}

            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert chart["low_balance_threshold"] is None


# ── Trough: full-horizon minimum, beyond-chart dip, offset ──────────


class TestPulseTrough:
    """The lowest projected end balance over the full forward horizon."""

    def test_trough_all_positive(self, app, seed_user, seed_periods, db):
        """All-positive horizon -> the smallest positive end balance.

        Five forward periods (5..9) with balances 500, 400, 300, 350, 450.
        The minimum is 300 at period 7, offset 7 - 5 = 2.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            values = ["500.00", "400.00", "300.00", "350.00", "450.00"]
            balances = {p.period_id: Decimal(v) for p, v in zip(forward, values)}

            trough = _pulse._trough(
                forward, balances, forward[0],
            )
            assert trough["balance"] == Decimal("300.00")
            assert trough["end_date"] == last_covered_day(seed_periods[_CURRENT_IDX + 2])
            # offset = period_index(7) - current period_index(5) = 2.
            assert trough["offset"] == 2

    def test_trough_negative_beyond_chart_window(self, app, seed_user, db):
        """A negative dip BEYOND the 13-chart window is still found.

        Build a 15-period forward horizon, all balances positive EXCEPT
        the 15th (index 14 in the forward list), which is negative.  The
        chart caps at 13 points (all positive), so a chart-only scan would
        miss the dip; the trough scans the FULL horizon and catches it.
        The trough's offset is the dip period's index minus the current
        (forward[0]) period's index.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=15,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Treat the whole generated run as the forward horizon and its
            # first period as the current (offset origin) -- this is a pure
            # _trough / _chart test, independent of today.
            forward = list(period_window(periods))
            current = forward[0]
            assert len(forward) == 15  # 15 forward periods; chart caps at 13.

            balances = {p.period_id: Decimal("500.00") for p in forward}
            dip_period = forward[14]  # the 15th forward period (beyond chart)
            balances[dip_period.period_id] = Decimal("-250.00")

            # The chart sees only the first 13 -- all positive.
            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 13
            assert all(pt["balance"] > Decimal("0") for pt in chart["points"])

            # The trough scans all 15 and finds the dip beyond the chart.
            trough = _pulse._trough(
                forward, balances, current,
            )
            assert trough["balance"] == Decimal("-250.00")
            assert trough["end_date"] == dip_period.end_date
            # offset = dip period_index - current period_index = 14.
            assert trough["offset"] == dip_period.period_index - current.period_index
            assert trough["offset"] == 14

    def test_trough_none_without_a_forward_period(
        self, app, seed_user, seed_periods, db,
    ):
        """An EMPTY forward window -> trough is None.

        **This case graded an empty end-balance MAP until C2-f2e's adversarial
        code review** (2026-08-18), and that state is now unconstructible:
        ``cash_balance_map`` is a TOTAL fold over ``reported_periods()`` and
        ``forward_periods`` is a slice of that same window, so every key is
        present by construction and the scan INDEXES rather than skipping. A
        test that builds a state production cannot reach grades the skip it
        depends on and nothing else -- which is why deleting the skip is what
        found it.

        What remains is the arm that CAN answer ``None``: no forward period at
        all. ``compute_pulse_section``'s own current-period guard refuses that
        owner before this helper is reached, so the arm is defensive -- and it
        is pinned rather than deleted because the helper is public to its
        module and its ``Returns`` block states it.
        """
        with app.app_context():
            current = list(period_window(seed_periods[_CURRENT_IDX:]))[0]
            trough = _pulse._trough([], {}, current)
            assert trough is None


# ── Peak: full-horizon maximum, beyond-chart rise, offset ───────────


class TestPulsePeak:
    """The highest projected end balance over the full forward horizon.

    The exact mirror of :class:`TestPulseTrough`: same full-horizon scan,
    same offset basis, same ``None`` degradation -- maximum instead of
    minimum.
    """

    def test_peak_all_positive(self, app, seed_user, seed_periods, db):
        """All-positive horizon -> the largest end balance at its offset.

        Five forward periods (5..9) with balances 300, 400, 500, 450, 350.
        The maximum is 500 at period 7, offset 7 - 5 = 2 -- a non-zero
        offset, proving the scan finds the true peak, not just the first
        point.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            values = ["300.00", "400.00", "500.00", "450.00", "350.00"]
            balances = {p.period_id: Decimal(v) for p, v in zip(forward, values)}

            peak = _pulse._peak(
                forward, balances, forward[0],
            )
            assert peak["balance"] == Decimal("500.00")
            assert peak["end_date"] == last_covered_day(seed_periods[_CURRENT_IDX + 2])
            # offset = period_index(7) - current period_index(5) = 2.
            assert peak["offset"] == 2

    def test_peak_equals_hero_when_first_period_is_max(
        self, app, seed_user, seed_periods, db,
    ):
        """When the current period is the maximum, the peak sits at offset 0.

        Five forward periods (5..9) with balances 500, 400, 300, 350, 450.
        The maximum is 500 at period 5 (the current period), offset
        5 - 5 = 0 -- the peak coincides with the hero / chart's first
        point, so the "highest point ahead" stat deep-links the current
        period.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            values = ["500.00", "400.00", "300.00", "350.00", "450.00"]
            balances = {p.period_id: Decimal(v) for p, v in zip(forward, values)}

            peak = _pulse._peak(
                forward, balances, forward[0],
            )
            assert peak["balance"] == Decimal("500.00")
            assert peak["end_date"] == last_covered_day(seed_periods[_CURRENT_IDX])
            # offset = period_index(5) - current period_index(5) = 0.
            assert peak["offset"] == 0

    def test_peak_beyond_chart_window(self, app, seed_user, db):
        """A peak BEYOND the 13-chart window is still found (full scan).

        Build a 15-period forward horizon, all balances equal EXCEPT the
        15th (index 14 in the forward list), which is the highest.  The
        chart caps at 13 points (all equal, none the peak), so a chart-only
        scan would miss the rise; the peak scans the FULL horizon and
        catches it.  The peak's offset is the rise period's index minus the
        current (forward[0]) period's index.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=15,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Treat the whole generated run as the forward horizon and its
            # first period as the current (offset origin) -- this is a pure
            # _peak / _chart test, independent of today.
            forward = list(period_window(periods))
            current = forward[0]
            assert len(forward) == 15  # 15 forward periods; chart caps at 13.

            balances = {p.period_id: Decimal("500.00") for p in forward}
            rise_period = forward[14]  # the 15th forward period (beyond chart)
            balances[rise_period.period_id] = Decimal("1250.00")

            # The chart sees only the first 13 -- all 500.00, none the peak.
            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 13
            assert all(
                pt["balance"] == Decimal("500.00") for pt in chart["points"]
            )

            # The peak scans all 15 and finds the rise beyond the chart.
            peak = _pulse._peak(
                forward, balances, current,
            )
            assert peak["balance"] == Decimal("1250.00")
            assert peak["end_date"] == rise_period.end_date
            # offset = rise period_index - current period_index = 14.
            assert peak["offset"] == rise_period.period_index - current.period_index
            assert peak["offset"] == 14

    def test_peak_none_without_a_forward_period(
        self, app, seed_user, seed_periods, db,
    ):
        """An EMPTY forward window -> peak is None.

        **This case graded an empty end-balance MAP until C2-f2e's adversarial
        code review** (2026-08-18), and that state is now unconstructible:
        ``cash_balance_map`` is a TOTAL fold over ``reported_periods()`` and
        ``forward_periods`` is a slice of that same window, so every key is
        present by construction and the scan INDEXES rather than skipping. A
        test that builds a state production cannot reach grades the skip it
        depends on and nothing else -- which is why deleting the skip is what
        found it.

        What remains is the arm that CAN answer ``None``: no forward period at
        all. ``compute_pulse_section``'s own current-period guard refuses that
        owner before this helper is reached, so the arm is defensive -- and it
        is pinned rather than deleted because the helper is public to its
        module and its ``Returns`` block states it.
        """
        with app.app_context():
            current = list(period_window(seed_periods[_CURRENT_IDX:]))[0]
            peak = _pulse._peak([], {}, current)
            assert peak is None

    def test_peak_wired_through_compute_pulse_section(
        self, app, seed_user, seed_periods, db,
    ):
        """compute_pulse_section returns a populated peak from the real walk.

        A $1,200.00 projected income in the current period (5) lifts that
        period's projected end balance to 1,000.00 + 1,200.00 = 2,200.00,
        the highest point in the all-else-flat horizon (the later periods
        carry the same 2,200.00 balance forward, so the peak is the FIRST
        such period -- the current one, offset 0).  This proves the
        producer wires _peak through, not just the helper in
        isolation.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("1200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            peak = result["peak"]
            # 1,000.00 anchor + 1,200.00 income = 2,200.00 end balance.
            assert peak["balance"] == Decimal("2200.00")
            assert peak["end_date"] == last_covered_day(current)
            # The current period is the first to reach the high; offset 0.
            assert peak["offset"] == 0
