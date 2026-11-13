"""Tests for the shared calendar date-arithmetic helpers (deep-hunt #34).

``months_between`` centralizes the "whole calendar months between two
dates, day ignored" convention that was inlined at five financial sites
(escrow inflation, loan amortization remaining/payoff, savings-goal
required-monthly, rate-period numbering).  This file pins the helper's
contract so every caller shares one tested definition:

- The day-of-month is disregarded (only year/month boundaries count).
- The result is signed (``end`` before ``start`` yields a negative).
- Cross-year deltas accumulate 12 per year.
- The inclusive ``+ 1`` and zero-floor that individual callers add stay
  the caller's concern (verified here by composing them with the helper).
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.utils.dates import (
    DISPLAY_TIMEZONE,
    add_months,
    clamped_day,
    day_label,
    display_today,
    grid_month_after,
    grid_month_on_or_after,
    grid_month_on_or_before,
    grid_months_within,
    month_ordinal,
    months_between,
    pay_period_label,
    pay_period_range_label,
    to_display_date,
    to_display_tz,
)
from tests._test_helpers import freeze_today


class TestAddMonths:
    """``add_months`` is the two month primitives, and the fold kept its answers.

    Plan step ``pay_calendar:C17-d-3`` folded the function onto
    :func:`~app.utils.dates.month_ordinal` and
    :func:`~app.utils.dates.clamped_day` (ledger row **PC-513**: it spelled
    the clamp a second time inside the module that owns it).  No case read
    it directly before; these pin the answers the fold was checked against
    -- 17,204 (start, months) pairs agreed with the old spelling, and the
    boundary ones are stated here so a later edit is graded, not trusted.
    """

    def test_a_day_the_target_month_lacks_clamps_to_its_last_day(self):
        """31 January plus one month is 28 February, not an error."""
        assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_a_leap_february_keeps_the_29th(self):
        """The clamp is the target month's own length."""
        assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_the_clamp_is_to_the_start_days_own_day_each_time(self):
        """13 months on from 31 January lands on 28 February of the next year."""
        assert add_months(date(2026, 1, 31), 13) == date(2027, 2, 28)

    def test_zero_months_is_the_start(self):
        """The identity, so a horizon of no months asks nothing of the clamp."""
        assert add_months(date(2026, 6, 15), 0) == date(2026, 6, 15)

    def test_a_year_boundary_needs_no_special_case(self):
        """November plus three months is February of the next year."""
        assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)

    def test_past_year_9999_is_the_sentinel_not_an_error(self):
        """The overflow guard survives the fold, at its exact edge."""
        assert add_months(date(9999, 6, 30), 6) == date(9999, 12, 30)
        assert add_months(date(9999, 6, 30), 7) == date.max
        assert add_months(date(9999, 12, 31), 1) == date.max


def _grid_days(nominal_day: int, first: date, last: date) -> list[date]:
    """Every grid day of *nominal_day* in ``[first, last]``, found by SCANNING days.

    The independent oracle for :class:`TestTheMonthlyGridSet`: a day is on the
    grid when it is the nominal day, or the month's last day in a month too
    short to hold it -- stated from the calendar's own month lengths, sharing
    no code with :func:`~app.utils.dates.clamped_day` or the set.
    """
    days = []
    day = first
    while day <= last:
        next_month_first = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_of_month = next_month_first - timedelta(days=1)
        if day.day == nominal_day or (day == last_of_month and nominal_day > day.day):
            days.append(day)
        day += timedelta(days=1)
    return days


class TestTheMonthlyGridSet:
    """The monthly-grid set beside the clamp (plan step recurrence:R25, ruling R-R122).

    A nominal day's grid is that day clamped into every month.  The loan
    calendar, the card statement, the pay grid and the recurrence walk each
    spelled "the latest grid day on or before", "the first on or after", "the
    first strictly after" and "the grid days between two dates" over the clamp
    until that step (finding REC-547); these pin the set's answers against a
    day-by-day scan that shares no code with it, over every day of a span that
    crosses a leap February, and every nominal day.
    """

    _FIRST = date(2027, 11, 1)
    _LAST = date(2028, 5, 31)

    @pytest.mark.parametrize("nominal_day", range(1, 32))
    def test_each_question_matches_a_scan_of_the_grid(self, nominal_day):
        """Every day of seven months, leap February inside: all four answers agree."""
        grid = _grid_days(nominal_day, date(2027, 9, 1), date(2028, 7, 31))
        day = self._FIRST
        while day <= self._LAST:
            on_or_before = max(g for g in grid if g <= day)
            on_or_after = min(g for g in grid if g >= day)
            after = min(g for g in grid if g > day)
            assert clamped_day(
                grid_month_on_or_before(day, nominal_day), nominal_day,
            ) == on_or_before
            assert clamped_day(
                grid_month_on_or_after(day, nominal_day), nominal_day,
            ) == on_or_after
            assert clamped_day(
                grid_month_after(day, nominal_day), nominal_day,
            ) == after
            day += timedelta(days=1)
        assert [
            clamped_day(ordinal, nominal_day)
            for ordinal in grid_months_within(nominal_day, self._FIRST, self._LAST)
        ] == _grid_days(nominal_day, self._FIRST, self._LAST)

    def test_a_31st_in_a_leap_february_is_the_29th(self):
        """Due the 31st: the latest grid day on or before Mar 10, 2028 is Feb 29."""
        ordinal = grid_month_on_or_before(date(2028, 3, 10), 31)
        assert ordinal == month_ordinal(date(2028, 2, 1))
        assert clamped_day(ordinal, 31) == date(2028, 2, 29)

    def test_a_day_on_the_grid_is_its_own_on_or_before_and_on_or_after(self):
        """Feb 29, 2028 for a 31st: both inclusive answers are that day, 'after' is Mar 31."""
        day = date(2028, 2, 29)
        assert clamped_day(grid_month_on_or_before(day, 31), 31) == day
        assert clamped_day(grid_month_on_or_after(day, 31), 31) == day
        assert clamped_day(grid_month_after(day, 31), 31) == date(2028, 3, 31)

    def test_a_span_holding_no_grid_day_is_empty(self):
        """The 22nd between Mar 23 and Apr 21 holds none; a reversed span holds none."""
        assert not grid_months_within(22, date(2026, 3, 23), date(2026, 4, 21))
        assert not grid_months_within(22, date(2026, 5, 1), date(2026, 3, 1))

    def test_the_span_is_inclusive_at_both_ends(self):
        """Mar 22 .. May 22 holds Mar 22, Apr 22 and May 22."""
        assert [
            clamped_day(ordinal, 22)
            for ordinal in grid_months_within(22, date(2026, 3, 22), date(2026, 5, 22))
        ] == [date(2026, 3, 22), date(2026, 4, 22), date(2026, 5, 22)]


class TestMonthsBetween:
    """Pin the whole-month delta convention shared across the engines."""

    def test_same_month_is_zero_regardless_of_day(self):
        """Two dates in the same calendar month are 0 months apart."""
        # Day ignored: Jan 1 -> Jan 31 still spans no month boundary.
        assert months_between(date(2026, 1, 1), date(2026, 1, 31)) == 0

    def test_one_month_boundary_ignores_day(self):
        """Crossing one month boundary counts as 1, even day 31 -> day 1."""
        # 2026-01-31 -> 2026-02-01: one boundary crossed = 1.
        assert months_between(date(2026, 1, 31), date(2026, 2, 1)) == 1

    def test_full_year_is_twelve(self):
        """Exactly one calendar year is 12 months."""
        assert months_between(date(2026, 1, 1), date(2027, 1, 1)) == 12

    def test_partial_year_day_ignored(self):
        """Mid-month start to start-of-next-year is a whole-month count."""
        # (2027-2026)*12 + (1-1) = 12; the day (15) is disregarded.
        assert months_between(date(2026, 1, 15), date(2027, 1, 1)) == 12

    def test_multi_year_accumulates_twelve_per_year(self):
        """Cross-year deltas add 12 per year plus the month remainder."""
        # (2028-2026)*12 + (3-1) = 26.
        assert months_between(date(2026, 1, 10), date(2028, 3, 31)) == 26

    def test_negative_when_end_before_start(self):
        """An ``end`` earlier than ``start`` yields a negative count."""
        assert months_between(date(2026, 6, 1), date(2026, 1, 1)) == -5

    def test_inclusive_plus_one_composes(self):
        """The payoff site's inclusive ``+ 1`` rides on top of the helper."""
        # amortization payoff uses months_between(...) + 1.
        base = months_between(date(2026, 1, 1), date(2026, 4, 1))
        assert base == 3
        assert base + 1 == 4

    def test_zero_floor_composes(self):
        """The remaining-months floor (max(0, ...)) rides on the helper."""
        # calculate_remaining_months clamps term - elapsed at 0.
        elapsed = months_between(date(2020, 1, 1), date(2026, 1, 1))  # 72
        assert elapsed == 72
        assert max(0, 60 - elapsed) == 0  # past-term loan: no months left


class TestDisplayTimezone:
    """Pin the presentation-layer UTC -> America/New_York conversion.

    Storage stays UTC; these helpers express a stored instant in the
    user's wall clock at the display boundary.  The headline case is the
    motivating bug: a late-evening Eastern true-up whose UTC ``created_at``
    has already rolled to the next calendar day must still display as the
    Eastern day the user performed it.
    """

    def test_summer_instant_converts_to_edt(self):
        """A UTC instant in summer renders in EDT (UTC-4)."""
        # 2026-06-12 14:00 UTC - 4h (EDT) = 2026-06-12 10:00 Eastern.
        utc = datetime(2026, 6, 12, 14, 0, tzinfo=timezone.utc)
        local = to_display_tz(utc)
        assert local.utcoffset().total_seconds() == -4 * 3600
        assert (local.year, local.month, local.day, local.hour) == (
            2026, 6, 12, 10,
        )

    def test_winter_instant_converts_to_est(self):
        """A UTC instant in winter renders in EST (UTC-5), proving DST awareness."""
        # 2026-01-15 14:00 UTC - 5h (EST) = 2026-01-15 09:00 Eastern.
        utc = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
        local = to_display_tz(utc)
        assert local.utcoffset().total_seconds() == -5 * 3600
        assert (local.year, local.month, local.day, local.hour) == (
            2026, 1, 15, 9,
        )

    def test_naive_instant_assumed_utc(self):
        """A naive datetime is treated as UTC, not the server's local zone."""
        # 2026-06-12 14:00 (naive, == UTC) -> 10:00 EDT, same as the aware case.
        local = to_display_tz(datetime(2026, 6, 12, 14, 0))
        assert local.tzinfo == DISPLAY_TIMEZONE
        assert (local.month, local.day, local.hour) == (6, 12, 10)

    def test_display_date_rolls_back_late_evening_summer(self):
        """THE BUG: 9:30 PM EDT true-up stored as next-UTC-day shows the EDT day.

        A true-up at 2026-06-11 21:30 Eastern (EDT, UTC-4) is stored as
        2026-06-12 01:30 UTC.  Truncating the UTC instant gives June 12
        (the old wrong behavior); converting to Eastern first gives the
        June 11 the user actually performed it.
        """
        stored_utc = datetime(2026, 6, 12, 1, 30, tzinfo=timezone.utc)
        assert to_display_date(stored_utc) == date(2026, 6, 11)

    def test_display_date_rolls_back_late_evening_winter(self):
        """Same roll-back holds in winter (EST, UTC-5)."""
        # 2026-01-15 20:00 EST + 5h = 2026-01-16 01:00 UTC -> Eastern Jan 15.
        stored_utc = datetime(2026, 1, 16, 1, 0, tzinfo=timezone.utc)
        assert to_display_date(stored_utc) == date(2026, 1, 15)

    def test_display_date_same_day_when_no_boundary_cross(self):
        """A daytime UTC instant maps to the same calendar day in Eastern."""
        # 2026-06-12 18:00 UTC -> 14:00 EDT: still June 12.
        stored_utc = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
        assert to_display_date(stored_utc) == date(2026, 6, 12)

    def test_display_date_none_passthrough(self):
        """A None instant (anchor never set) returns None, not an error."""
        assert to_display_date(None) is None


class TestDisplayToday:
    """Pin ``display_today`` -- the user's wall-clock 'today' in the display tz.

    The presentation-layer "now" that civil-window surfaces (the analytics Taxes
    tab, the loan YTD chips) select their year / month by, so they follow the
    user's Eastern clock rather than the server's UTC day at the New Year (and
    every late-evening) boundary.
    """

    def test_new_year_boundary_is_the_eastern_day(self, monkeypatch):
        """Midnight UTC on Jan 1 is still Dec 31 in Eastern, so the year is 2026.

        ``at_time=time.min`` pins ``datetime.now(utc)`` to 2027-01-01 00:00
        UTC.  That instant is 7:00 PM EST on 2026-12-31, so ``display_today()``
        is Dec 31 2026 -- year 2026 -- even though ``date.today()`` (UTC) has
        already rolled to 2027.  This is the exact skew the loan YTD chips must
        follow to agree with the Taxes tab.

        The instant is stated rather than inherited: ``freeze_today``'s default
        is NOON, the same instant it pins the database clock to, so that a test
        meaning "today is D" gets D from every reader including the display-tz
        ones.  A test meaning a specific instant asks for one.
        """
        freeze_today(
            monkeypatch, date(2027, 1, 1),
            modules=["app.utils.dates"], at_time=time.min,
        )
        assert display_today() == date(2026, 12, 31)
        assert display_today().year == 2026

    def test_daytime_utc_is_the_same_eastern_day(self, monkeypatch):
        """A mid-day UTC instant maps to the same calendar day in Eastern.

        A manual mid-afternoon stub, independent of ``freeze_today`` entirely:
        2026-06-12 18:00 UTC is 14:00 EDT, still June 12, so
        ``display_today()`` is 2026-06-12.  ``freeze_today`` would also serve
        now that its default is NOON; the hand-rolled stub predates that and is
        kept because pinning the instant explicitly is what this case is about.
        """
        class _FixedDateTime(datetime):
            """A datetime whose ``now`` is a fixed mid-day UTC instant."""

            @classmethod
            def now(cls, tz=None):
                """Return 2026-06-12 18:00 in the requested tz (UTC by default)."""
                fixed = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr("app.utils.dates.datetime", _FixedDateTime)
        assert display_today() == date(2026, 6, 12)


class TestPayPeriodRangeLabel:
    """The WIDE period register, and the one place it is now written.

    Plan step **C2-f3a** collapsed three copies of this string --
    ``ledger_report_service._income_statement._window_label``,
    ``spending_report_service._window._window_label`` and inline Jinja in
    ``analytics/_income_statement.html`` -- onto
    :func:`~app.utils.dates.pay_period_range_label`, and then onto
    ``spending_analysis.window_label`` one level up when ``duplicate-code``
    showed the two service functions were the same three-arm dispatch.  Ledger
    row **P47**'s duplicate half.

    **These were BYTE-EXACT pins of what all three copies produced**, which is
    what made the collapse provably display-neutral rather than a rendering
    change smuggled in behind a DRY fix.  **One of them has since moved**: the
    developer ruled ledger row **P67** on 2026-08-25 and a straddling period
    now names both years, which is the rendering change the collapse
    deliberately did not make.
    """

    def test_it_reproduces_the_register_the_three_copies_produced(self):
        """``Feb 21 - Mar 06, 2026``: abbreviated month, padded day, end year."""
        assert pay_period_range_label(
            date(2026, 2, 21), date(2026, 3, 6),
        ) == "Feb 21 - Mar 06, 2026"

    def test_a_period_inside_one_month_keeps_the_same_shape(self):
        """No special case for a period that does not cross a month."""
        assert pay_period_range_label(
            date(2026, 3, 13), date(2026, 3, 26),
        ) == "Mar 13 - Mar 26, 2026"

    def test_a_STRADDLING_period_names_BOTH_years(self):
        """P67's remedy: the year is carried on both halves across a straddle.

        **This test pinned the DEFECT until 2026-08-25**, deliberately and with
        its reason stated: C2-f3a collapsed three copies of this register and
        reproduced their output byte for byte, so that a rendering change was
        not smuggled in behind a DRY fix.  ``"Dec 26 - Jan 08, 2027"`` reads as
        a December in 2027 and is a December in 2026.

        The developer ruled ledger row **P67** on 2026-08-25 (option A of four:
        both years on a straddle, the end year alone otherwise), so the pinned
        behaviour is no longer the intended one and the pin moves with it.  The
        rule is the NARROW register's, unchanged -- see
        :meth:`test_the_narrow_register_already_carries_both_years` -- which is
        the whole point: two registers differ in WIDTH, not in when a year
        disambiguates.
        """
        assert pay_period_range_label(
            date(2026, 12, 26), date(2027, 1, 8),
        ) == "Dec 26, 2026 - Jan 08, 2027"

    def test_a_period_inside_one_year_still_names_only_the_END_year(self):
        """The non-vacuity control for the straddle rule above.

        A year is carried on both halves ONLY where it disambiguates.  Without
        this, a change that always printed both years would pass the straddle
        test while making every other label noisier -- and 25 of a year's 26
        periods do not straddle.
        """
        assert pay_period_range_label(
            date(2026, 2, 21), date(2026, 3, 6),
        ) == "Feb 21 - Mar 06, 2026"

    def test_the_narrow_register_already_carries_both_years(self):
        """:func:`pay_period_label` disambiguates the straddle; this one does not.

        Stated side by side because it is the whole of P67's argument: the
        rule exists in this module already, applied by the sibling register to
        the identical input, so the wide one is inconsistent with its own
        neighbour rather than merely terse.
        """
        assert pay_period_label(
            date(2026, 12, 26), date(2027, 1, 8),
        ) == "12/26/26 - 01/08/27"


class TestPayPeriodLabel:
    """The NARROW register (``"02/21 - 03/06"``), graded on the FUNCTION.

    These four cases were ``tests/test_models/test_computed_properties.py``'s
    ``TestPayPeriodLabel``, driven through the ORM accessor
    ``PayPeriod.label`` -- which pay-calendar plan step **C4-a-5** deleted with
    the stored ``end_date`` it formatted.  The RULE survives that deletion, so
    the cases move here rather than going with it, and they get faster and
    stricter on the way: each built a real ``budget.pay_periods`` row (one
    database clone apiece) to assert a pure function of two dates, and three of
    them carried a comment about ``uq_pay_periods_user_index`` that had nothing
    to do with what they were testing.

    The surviving reader is
    :attr:`~app.services.pay_calendar.DerivedPeriod.label`, which calls this
    same function -- ``tests/test_services/test_pay_calendar_value.py`` grades
    that it does.
    """

    def test_a_period_inside_one_month(self):
        """Both halves are ``MM/DD`` and neither carries a year."""
        assert pay_period_label(
            date(2026, 3, 1), date(2026, 3, 14),
        ) == "03/01 - 03/14"

    def test_a_period_crossing_a_MONTH_carries_both_months(self):
        """The ordinary biweekly shape: the month changes, the year does not."""
        assert pay_period_label(
            date(2026, 1, 25), date(2026, 2, 7),
        ) == "01/25 - 02/07"

    def test_the_seed_schedules_opening_period(self):
        """The value every fixture-built schedule opens on, pinned.

        ``seed_periods`` starts 2026-01-02 at a 14-day cadence, so this is the
        label a reader sees first on almost every screen in the suite.
        """
        assert pay_period_label(
            date(2026, 1, 2), date(2026, 1, 15),
        ) == "01/02 - 01/15"

    def test_a_period_crossing_a_YEAR_carries_a_two_digit_year_on_BOTH(self):
        """The one case a year disambiguates, and it goes on both halves.

        ``"12/26/26 - 01/08/27"`` says WHICH January; ``"12/26 - 01/08"`` does
        not.  Carrying it on the end alone would read as a December in 2027 --
        which is the defect ledger row **P67** found in the WIDE register and
        this one never had.

        **The same call and the same expected string appear once more in this
        file**, as ``TestPayPeriodRangeLabel``'s
        ``test_the_narrow_register_already_carries_both_years`` -- flagged by
        an adversarial review of ``pay_calendar:C4-a-5``, and kept rather than
        collapsed.  That one is evidence INSIDE the wide register's class: P67
        argued the wide register was inconsistent with its own neighbour, and
        the neighbour's answer has to be visible there to make the argument.
        This one is the narrow register's own year case, in the class that
        owns it.  Deleting either leaves a class that does not state its own
        rule; that is the reason, and it is written down so the next reader
        does not collapse them.
        """
        assert pay_period_label(
            date(2026, 12, 26), date(2027, 1, 8),
        ) == "12/26/26 - 01/08/27"


class TestDayLabel:
    """The narrow register's ONE-DAY sibling (``"07/13"``), plan step ``bank_import:X-gz``.

    Stated once so the Reconcile MATCH pane can print a purchase day beside
    the paycheck label in one register; :func:`pay_period_label`'s
    non-straddling branch composes it, so the two cannot drift apart on the
    zero-padding or the separator.
    """

    def test_it_is_zero_padded_month_and_day_and_no_year(self):
        """``07/13``, the grid's own spelling of a purchase day."""
        assert day_label(date(2026, 7, 13)) == "07/13"

    def test_the_first_of_january_pads_both_halves(self):
        """The non-vacuity control for the padding: ``01/01``, not ``1/1``."""
        assert day_label(date(2027, 1, 1)) == "01/01"

    def test_the_paycheck_label_is_two_of_these_joined(self):
        """One rule under both: the period label IS two day labels."""
        first, last = date(2026, 7, 16), date(2026, 7, 29)

        assert pay_period_label(first, last) == (
            f"{day_label(first)} - {day_label(last)}"
        )
