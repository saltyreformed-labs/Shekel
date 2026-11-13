"""The day-of-month cadence kinds: ``Monthly`` and ``SemiMonthly``, graded against a listing.

Plan step ``pay_calendar:C17-d-2`` (rulings **R-PC79**, **R-PC80**, aliasing
``recurrence:R13``).  A monthly era pays on one owner-chosen day of the month,
a semi-monthly era on two, and a day the month is too short to hold means its
last day.  The grid arithmetic (:mod:`app.services.pay_calendar._grid`) is
O(1) and walks nothing, so nothing in it is a listing of paydays -- which is
why every case here grades it AGAINST one: a brute-force walk over calendar
months, written with ``calendar.monthrange`` and nothing from the package, is
the oracle, exactly as the ``test_pay_calendar_derivation/`` package grades
the fixed-days grid.

**Each exhaustive sweep is ONE test that loops rather than a parametrised
case per pair**, deliberately: every item in this suite is one database
clone (about 0.076 s, ``tests/conftest.py``'s ``db`` fixture is autouse), so
378 pairs as 378 items would spend 30 s cloning databases these pure cases
never touch.  A failing assertion names the pair, the anchor and the day.

What each class is here to catch:

  1. **The grid's contract**, on both month kinds, over every legal pair
     and day and anchors at both positions of the pair:
     ``payday(steps_to(d)) <= d < payday(steps_to(d) + 1)`` on every day,
     ``payday(0) == anchor``, and ``steps_to(payday(k)) == k`` in both
     directions.
  2. **The values**: the pair is sorted, so two eras stating one rhythm
     compare equal; the shortest gap's closed form matches a brute-force
     walk over 2024-2027 for every pair and is a floor for every monthly
     day; each kind phrases itself.
  3. **The bounds** a calendar holds a month kind to
     (:func:`~app.services.pay_calendar._eras.validate_cadence`), one case per
     refusal, and the structural fact that every kind the grid can project
     has a bound.
  4. **``PayCadence``** answers 12 and 24 with nothing divided, and a span
     of *n* months holds exactly *n* or *2n* paychecks (ruling **R-R31**).
  5. **A calendar refuses an era phased off its own grid**
     (:func:`~app.services.pay_calendar._derive.validate_eras`).
  6. **The ruling's own figure**: a 1st/15th owner is paid 24 times in 2026
     where the 15-day walk R-R28 kept pays 25.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import (
    PayCadence,
    PayCalendar,
    PayCalendarError,
    cadence_steps_to,
    nominal_payday,
)
from app.services.pay_calendar import _eras, _grid
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, SemiMonthly
from app.utils.dates import SHORTEST_MONTH_DAYS

#: Every legal semi-monthly pair: a lower day of at most 27 and a distinct
#: upper day.
_PAIRS = [
    (lower, upper) for lower in range(1, 28) for upper in range(lower + 1, 32)
]

#: The months the grid is anchored in: one of each length -- 31, 28 and 30
#: days -- so every clamp shape a meant day can take is an anchor.
_ANCHOR_MONTHS = (1, 2, 4)


def _month_days(day, first_year, last_year):
    """Return *day* clamped into every month of ``first_year..last_year``, ascending."""
    return [
        date(year, month, min(day, monthrange(year, month)[1]))
        for year in range(first_year, last_year + 1)
        for month in range(1, 13)
    ]


def _monthly_listing(day):
    """The oracle: a monthly grid on *day*, listed over 2020-2032."""
    return _month_days(day, 2020, 2032)


def _semi_monthly_listing(lower, upper):
    """The oracle: a semi-monthly grid on *lower* and *upper*, listed over 2020-2032."""
    listing = []
    for year in range(2020, 2033):
        for month in range(1, 13):
            listing.append(date(year, month, lower))
            listing.append(date(year, month, min(upper, monthrange(year, month)[1])))
    return listing


def _grade_against(cadence, listing, anchors, first_day, last_day, steps):
    """Hold the grid to *listing* from each of *anchors*: contract, identity, round trip."""
    for anchor in anchors:
        anchor_index = listing.index(anchor)
        assert nominal_payday(anchor, cadence, 0) == anchor
        for k in range(-steps, steps + 1):
            expected = listing[anchor_index + k]
            assert nominal_payday(anchor, cadence, k) == expected, (cadence, anchor, k)
            assert cadence_steps_to(anchor, cadence, expected) == k, (cadence, anchor, k)
        day = first_day
        while day <= last_day:
            steps_to = cadence_steps_to(anchor, cadence, day)
            before = nominal_payday(anchor, cadence, steps_to)
            after = nominal_payday(anchor, cadence, steps_to + 1)
            assert before <= day < after, (cadence, anchor, day, before, after)
            day += timedelta(days=1)


class TestTheMonthGridsHoldTheContractAgainstAListing:
    """Case 1: every pair, every day, both anchor positions, both directions."""

    def test_the_monthly_grid_matches_the_listing_on_every_day(self):
        """Every day 1..31; anchors in a 31-, a 28- and a 30-day month of 2025; 2025-2026 daily.

        The three month lengths are the three ways a meant day can clamp,
        so an anchor in each is an anchor on every clamp shape the grid
        must read back; 2025-2026 spans a non-leap February and every
        30-day month, and 30 steps each way reach 2022 and 2028.
        """
        for day in range(1, 32):
            listing = _monthly_listing(day)
            anchors = [
                d for d in listing if d.year == 2025 and d.month in _ANCHOR_MONTHS
            ]
            _grade_against(
                Monthly(day), listing, anchors,
                date(2025, 1, 1), date(2026, 12, 31), steps=30,
            )

    def test_the_semi_monthly_grid_matches_the_listing_on_every_pair(self):
        """Every legal pair; anchors at BOTH positions in the same three months; 2025 daily.

        An anchor on a clamped upper day (February, the 30-day months) is
        the case the position reading has to get right, so each of the
        three month lengths anchors the grid at both of the pair's days;
        27 steps each way reach 2024 and 2026.  About 800,000 contract
        checks over the 378 pairs, a few seconds of one worker.
        """
        for pair in _PAIRS:
            listing = _semi_monthly_listing(*pair)
            anchors = [
                d for d in listing if d.year == 2025 and d.month in _ANCHOR_MONTHS
            ]
            _grade_against(
                SemiMonthly(pair), listing, anchors,
                date(2025, 1, 1), date(2025, 12, 31), steps=27,
            )

    def test_the_ruled_example_a_day_31_era_opening_in_february(self):
        """R-PC79: 2026-02-28 meaning the 31st projects 03-31, 04-30, 05-31."""
        anchor = date(2026, 2, 28)
        assert [nominal_payday(anchor, Monthly(31), k) for k in range(4)] == [
            date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31),
        ]
        assert nominal_payday(anchor, Monthly(31), -1) == date(2026, 1, 31)

    def test_a_pair_reads_its_anchors_position_off_the_date(self):
        """From the 15th of a 1st/15th pair the next payday is the 1st; from the 1st, the 15th."""
        pair = SemiMonthly((1, 15))
        assert nominal_payday(date(2026, 1, 15), pair, 1) == date(2026, 2, 1)
        assert nominal_payday(date(2026, 1, 1), pair, 1) == date(2026, 1, 15)
        assert nominal_payday(date(2026, 1, 1), pair, -1) == date(2025, 12, 15)

    def test_every_kind_keyed_table_names_the_grids_kinds(self):
        """Structural: every table dispatched on a kind's class keys exactly the grid's set.

        The grid's arithmetic, the calendar's bounds, the write door's
        bounds, the writer's columns and the two ``PayCadence`` counts are
        six tables keyed by the value's class; a kind present in one and
        absent from another is a ``KeyError`` 500 on the path that reaches
        the gap, so the sets are held equal here rather than discovered.
        """
        # pylint: disable=protected-access,import-outside-toplevel
        from app.services import pay_era_write, pay_schedule_service
        from app.services.pay_calendar import _cadence

        kinds = frozenset({FixedDays, Monthly, SemiMonthly})
        assert _grid.KINDS == kinds
        for table in (
            _eras._PARAMETER_CHECKS,
            pay_schedule_service._RANGE_REFUSALS,
            pay_era_write._COLUMNS_OF,
            _cadence._PERIODS_PER_YEAR,
            _cadence._PAYCHECKS_WITHIN,
        ):
            assert frozenset(table) == kinds


class TestTheValues:
    """Case 2: what a kind says about itself, with no calendar in hand."""

    def test_a_pair_is_sorted_so_one_rhythm_is_one_value(self):
        """"15th and 1st" and "1st and 15th" are the same rhythm at era_to_mint's equality."""
        assert SemiMonthly((15, 1)) == SemiMonthly((1, 15))
        assert SemiMonthly((15, 1)).days == (1, 15)
        assert Rhythm(SemiMonthly((15, 1)), BusinessDayShiftEnum.NONE) == Rhythm(
            SemiMonthly((1, 15)), BusinessDayShiftEnum.NONE,
        )

    def test_the_semi_monthly_shortest_gap_matches_a_brute_force_walk_on_every_pair(self):
        """R-PC79's closed form, checked over 2024-2027 pair by pair: 0 disagreements."""
        for pair in _PAIRS:
            lower, upper = pair
            listing = [
                d for d in _semi_monthly_listing(lower, upper) if 2024 <= d.year <= 2027
            ]
            brute = min((b - a).days for a, b in zip(listing, listing[1:]))
            assert SemiMonthly(pair).shortest_gap == brute, pair

    def test_the_monthly_shortest_gap_is_a_floor_for_every_day(self):
        """28 for every day: exact for 1..28 and 31, one under for 29 and 30 (stated, R-PC79)."""
        for day in range(1, 32):
            listing = _month_days(day, 2024, 2027)
            brute = min((b - a).days for a, b in zip(listing, listing[1:]))
            assert Monthly(day).shortest_gap == SHORTEST_MONTH_DAYS, day
            assert Monthly(day).shortest_gap <= brute, day
            assert brute - Monthly(day).shortest_gap == (1 if day in (29, 30) else 0), day

    def test_the_fixed_days_shortest_gap_is_its_day_count(self):
        """The one property the floor reads, on the kind every owner held."""
        assert FixedDays(14).shortest_gap == 14

    @pytest.mark.parametrize("cadence, phrase", [
        (FixedDays(14), "every 14 days"),
        (FixedDays(1), "every day"),
        (Monthly(15), "monthly on day 15"),
        (Monthly(31), "monthly on day 31"),
        (SemiMonthly((15, 1)), "twice a month on days 1 and 15"),
    ])
    def test_each_kind_phrases_itself(self, cadence, phrase):
        """The one spelling every refusal names a rhythm by (rulings R-PC82, R-PC83)."""
        assert cadence.phrase == phrase


class TestTheBoundsACalendarHoldsAMonthKindTo:
    """Case 3: ``validate_cadence``'s month arms, one case per refusal."""

    @pytest.mark.parametrize("cadence", [
        Monthly(1), Monthly(31), SemiMonthly((1, 31)), SemiMonthly((27, 28)),
        SemiMonthly((15, 30)),
    ])
    def test_a_legal_month_kind_is_admitted(self, cadence):
        """Every edge the CHECKs admit is admitted here too."""
        _eras.validate_cadence(cadence)

    @pytest.mark.parametrize("cadence, fragment", [
        (Monthly(0), "day of the month"),
        (Monthly(32), "day of the month"),
        (Monthly(15.0), "plain int"),
        (Monthly(True), "plain int"),
        (Monthly(None), "plain int"),
        (SemiMonthly((0, 15)), "day of the month"),
        (SemiMonthly((1, 32)), "day of the month"),
        (SemiMonthly((1.0, 15)), "plain int"),
        (SemiMonthly((15, 15)), "two DIFFERENT days"),
        (SemiMonthly((28, 31)), "lower day must be at most 27"),
        (SemiMonthly((28, 29)), "lower day must be at most 27"),
    ])
    def test_a_month_kind_outside_its_bounds_is_refused(self, cadence, fragment):
        """Each refusal names what is wrong, with the package's error."""
        with pytest.raises(PayCalendarError, match=fragment):
            _eras.validate_cadence(cadence)

    def test_a_pair_that_is_not_two_days_is_refused(self):
        """A one- or three-tuple built by hand is refused before it is unpacked."""
        with pytest.raises(PayCalendarError, match="exactly two days"):
            _eras.validate_cadence(SemiMonthly((1, 15, 30)))

    def test_the_value_and_the_calendar_refuse_the_same_pair(self):
        """PayCadence validates through the same function, so it cannot admit more."""
        with pytest.raises(PayCalendarError, match="lower day must be at most 27"):
            PayCadence(SemiMonthly((28, 31)))


class TestPayCadenceAnswersWithoutDividing:
    """Case 4: 12 and 24 exactly, and exact counts within a span (R-R31)."""

    @pytest.mark.parametrize("cadence, per_year", [
        (Monthly(1), Decimal("12")),
        (Monthly(31), Decimal("12")),
        (SemiMonthly((1, 15)), Decimal("24")),
        (SemiMonthly((15, 31)), Decimal("24")),
    ])
    def test_periods_per_year_is_exact(self, cadence, per_year):
        """No division, no rounding: an integral Decimal every consumer divides by."""
        assert PayCadence(cadence).periods_per_year == per_year

    @pytest.mark.parametrize("months, monthly, semi", [
        (3, 3, 6), (6, 6, 12), (12, 12, 24), (24, 24, 48), (0, 0, 0),
    ])
    def test_paychecks_within_a_span_is_the_spans_month_count(self, months, monthly, semi):
        """A monthly owner's third paycheck arrives ON the three-month day: counted."""
        assert PayCadence(Monthly(15)).paychecks_within(months) == monthly
        assert PayCadence(SemiMonthly((1, 15))).paychecks_within(months) == semi

    def test_a_31_day_owner_and_a_monthly_owner_part_at_three_months(self):
        """The difference the kind makes: 2 paychecks within 3 months at 31 days, 3 monthly."""
        assert PayCadence(FixedDays(31)).paychecks_within(3) == 2
        assert PayCadence(Monthly(1)).paychecks_within(3) == 3

    def test_the_fixed_days_answers_are_unchanged(self):
        """The dispatch did not move the kind every owner holds."""
        biweekly = PayCadence(FixedDays(14))
        assert biweekly.periods_per_year == Decimal("26")
        assert [biweekly.paychecks_within(m) for m in (3, 6, 12, 24)] == [6, 13, 26, 52]


def _calendar_on(era, paydays):
    """A pure calendar over *paydays* on ONE era."""
    return PayCalendar.from_paydays(
        paydays=[(None, d) for d in paydays], eras=(era,), user_id=1,
        history_opens_on=None,
    )


class TestACalendarRefusesAPhaseOffItsGrid:
    """Case 5: ``validate_eras`` asks the grid's round trip of every era."""

    @pytest.mark.parametrize("effective_from, cadence, held", [
        (date(2026, 1, 10), Monthly(15), "2026-01-15"),
        (date(2026, 1, 3), SemiMonthly((1, 15)), "2026-01-15"),
        (date(2026, 2, 27), Monthly(31), "2026-02-28"),
    ])
    def test_an_era_phased_off_its_stated_day_is_refused(
        self, effective_from, cadence, held,
    ):
        """The message names the grid day the anchor's month does hold."""
        era = Era(effective_from, Rhythm(cadence, BusinessDayShiftEnum.NONE))
        with pytest.raises(PayCalendarError, match=held):
            _calendar_on(era, [effective_from])

    @pytest.mark.parametrize("effective_from, cadence", [
        (date(2026, 1, 15), Monthly(15)),
        (date(2026, 2, 28), Monthly(31)),
        (date(2026, 1, 1), SemiMonthly((1, 15))),
        (date(2026, 1, 15), SemiMonthly((1, 15))),
        (date(2026, 4, 30), SemiMonthly((15, 31))),
    ])
    def test_an_era_phased_on_its_stated_day_derives(self, effective_from, cadence):
        """Both positions of a pair, and a clamped upper day, are on the grid."""
        era = Era(effective_from, Rhythm(cadence, BusinessDayShiftEnum.NONE))
        calendar = _calendar_on(era, [effective_from])
        assert calendar.periods[0].start_date == effective_from

    def test_a_fixed_days_era_is_on_its_grid_from_any_day(self):
        """The refusal cannot fire on the kind every owner held: a control."""
        era = Era(date(2026, 1, 10), Rhythm(FixedDays(14), BusinessDayShiftEnum.NONE))
        assert _calendar_on(era, [date(2026, 1, 10)]).periods[0].start_date == date(
            2026, 1, 10,
        )


class TestTheRulingsFigure:
    """Case 6: the count R-PC79 re-derived, read off the projection."""

    @staticmethod
    def _paydays_in_2026(cadence):
        """The grid days of 2026 on *cadence* anchored 2026-01-01, the anchor included."""
        anchor = date(2026, 1, 1)
        paydays = []
        steps = 0
        while nominal_payday(anchor, cadence, steps).year == 2026:
            paydays.append(nominal_payday(anchor, cadence, steps))
            steps += 1
        return paydays

    def test_a_first_and_fifteenth_owner_is_paid_24_times_where_the_walk_paid_25(self):
        """24 paydays on the 1st and 15th; 25 on the 15-day walk, the last on 12-27."""
        semi = self._paydays_in_2026(SemiMonthly((1, 15)))
        walk = self._paydays_in_2026(FixedDays(15))
        assert len(semi) == 24
        assert all(d.day in (1, 15) for d in semi)
        assert len(walk) == 25
        assert walk[-1] == date(2026, 12, 27)

    def test_a_day_31_owner_is_paid_on_every_months_last_day(self):
        """Twelve paydays, each the last day of its month, none decayed to the 28th."""
        paydays = self._paydays_in_2026(Monthly(31))
        assert len(paydays) == 12
        assert [d.day for d in paydays] == [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
