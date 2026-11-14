"""
Shekel Budget App -- the pay calendar's RHYTHM: how many paydays a span holds.

Plan step **balance:X-bh-1** added
:func:`~app.services.pay_calendar.paydays_in_month_through` and
:func:`~app.services.pay_calendar.paydays_in_year_before`, the two producers
the paycheck engine's four calendar judgements and the analytics year overview
now share.  Those two surfaces exercise them end to end
(``test_paycheck_calculator``, ``test_calendar_service``); this module grades
the SPAN ITSELF -- its boundaries, its empty answers and the seam between the
saved schedule and the forward projection -- which an adversarial review of
that step found nothing covered directly.

No database: a :class:`~app.services.pay_calendar.PayCalendar` is a pure value
over a payday set, which is what lets these cases state a schedule in one line.
"""

import random
from datetime import date, timedelta

import pytest

from app.enums import BusinessDayShiftEnum
from app.utils.business_days import (
    is_business_day,
    shift_to_business_day,
    shortest_collision_free_cadence,
)
from app.services.pay_calendar._rhythm import (
    _backdated_paydays,
    _paydays_between,
)
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    paydays_in_month_through,
    paydays_in_year_before,
    projection_paydays_in_month_through,
)


from tests._test_helpers import (
    era_of,
    eras_of,
)
#: A biweekly rhythm anchored on a Friday, three paydays in January 2026
#: (the 2nd, 16th and 30th) and two in every other month it reaches.
_JANUARY_OPENING = date(2026, 1, 2)
_CADENCE = 14


def _calendar(
    count, opening=_JANUARY_OPENING, cadence=_CADENCE, user_id=1,
    history_opens_on=None, shift=BusinessDayShiftEnum.NONE,
):
    """A calendar of *count* paydays every *cadence* days from *opening*.

    *history_opens_on* defaults to ``None`` because that is what the nullable
    column defaults to, and since ruling **balance:R-IA**'s 2026-08-31
    amendment that means NOT STATED: an owner nobody has asked, whose rhythm is
    their RECORD.  So a case that says nothing here gets no backward half at
    all, and a case about the backward rhythm has to state a floor -- which is
    the safe direction for a default, because forgetting it cannot silently
    invent paychecks.
    """
    return PayCalendar.from_paydays(
        [(index + 1, opening + timedelta(days=cadence * index))
         for index in range(count)],
        (era_of(opening, cadence, shift),),
        user_id=user_id,
        history_opens_on=history_opens_on,
    )


class TestPaydaysInMonthThrough:
    """The month span: where a payday sits, and how many a month holds."""

    def test_counts_the_paydays_at_or_before_the_day(self):
        """A payday is counted; the ones after it in its month are not.

        January 2026 holds 2 / 16 / 30.  Asked at the 16th the answer is the
        first two, which is what makes the length that payday's ordinal.
        """
        assert paydays_in_month_through(_calendar(26), date(2026, 1, 16)) == (
            date(2026, 1, 2), date(2026, 1, 16),
        )

    def test_asked_at_a_month_end_it_is_the_month_size(self):
        """The other half of one producer: how many paydays the month holds."""
        assert len(paydays_in_month_through(
            _calendar(26), date(2026, 1, 31),
        )) == 3

    def test_a_neighbouring_month_is_excluded_at_both_ends(self):
        """The span is the calendar MONTH, not a window around the day.

        2025-12-19 and 2026-02-13 are both within one cadence of January's
        paydays, so a window would sweep them in.
        """
        assert paydays_in_month_through(
            _calendar(26), date(2026, 2, 28),
        ) == (date(2026, 2, 13), date(2026, 2, 27))

    def test_a_month_below_the_record_is_counted_from_the_RHYTHM(self):
        """Below the opening payday the rhythm continues, and this is X-bh-2.

        The schedule opens 2026-01-02, so December 2025 holds no recorded
        payday -- and the owner was really paid in it, twice, on the 5th and
        the 19th.  Until plan step **balance:X-bh-2** this answered ``()``
        for everyone, and that empty tuple is what made a 12-per-year
        deduction land on the wrong paycheck and a year-to-date open at the
        record's boundary instead of the owner's (ledger row **N-390**).

        The owner has to SAY so: ``history_opens_on`` is what turns the
        backward half on, and the sibling below is the same calendar for an
        owner who has said nothing.

        The case is stated on the MONTH question rather than the year one
        because it is the whole of what changed: one span search answers both,
        and the month is where a wrong count moves a deduction.
        """
        assert paydays_in_month_through(
            _calendar(26, history_opens_on=date(2025, 1, 1)),
            date(2025, 12, 31),
        ) == (date(2025, 12, 5), date(2025, 12, 19))

    def test_an_UNSTATED_history_counts_only_the_RECORD(self):
        """THE CONTROL, and the default every owner starts at.

        The same calendar and the same month for an owner nobody has asked.
        ``NULL`` is NOT a claim that they have always been paid this way
        (ruling **balance:R-IA** as amended 2026-08-31), so nothing is
        projected below the record and the answer is what it was before this
        step existed.  Without this case the one above would pass against a
        producer that ignored the floor entirely.
        """
        assert paydays_in_month_through(
            _calendar(26), date(2025, 12, 31),
        ) == ()

    def test_a_long_cadence_leaves_months_with_no_payday(self):
        """A cadence longer than a month is legal and empties some months.

        ``budget.pay_schedule`` admits 1..365 days.  At 45 days from
        2026-01-02 the paydays are 01-02, 02-16, 04-02 -- so March holds none,
        and that is an answer rather than an error.
        """
        calendar = _calendar(3, cadence=45)
        assert paydays_in_month_through(calendar, date(2026, 3, 31)) == ()
        assert paydays_in_month_through(calendar, date(2026, 2, 28)) == (
            date(2026, 2, 16),
        )

    def test_an_empty_calendar_holds_no_paydays(self):
        """No payday means no anchor, so there is nothing to count from.

        *The calendar carried ``cadence_days=None`` until plan step
        ``pay_calendar:C4-d``, when a cadence became required; the owner that
        stood for -- no ``budget.pay_schedule`` row -- has no calendar at all
        now.  An EMPTY calendar is still ordinary, and it is this one: a
        schedule row and zero paydays.*
        """
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=1, history_opens_on=None,
        )
        assert paydays_in_month_through(empty, date(2026, 1, 31)) == ()


class TestPaydaysInYearBefore:
    """The year span: what this owner has been paid before a given payday."""

    def test_sums_the_years_earlier_paydays_only(self):
        """Strictly before the day, and only within its own calendar year."""
        assert paydays_in_year_before(_calendar(26), date(2026, 2, 13)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )

    def test_the_years_first_payday_has_nothing_before_it(self):
        """A cumulative of zero, which is the correct answer and not an error."""
        assert paydays_in_year_before(_calendar(26), date(2026, 1, 2)) == ()

    def test_the_previous_year_is_not_counted(self):
        """The wage base and the annual cap are both CALENDAR-year windows.

        This schedule opens 2025-12-05 and runs into 2026, so a span that
        leaked across the boundary would carry December's paydays into
        January's cumulative and reach the SS cap early.
        """
        calendar = _calendar(6, opening=date(2025, 12, 5))
        assert paydays_in_year_before(calendar, date(2026, 1, 16)) == (
            date(2026, 1, 2),
        )

    def test_the_first_of_january_names_a_crossed_span(self):
        """Asked on 1 January the span ends the day before it opens.

        `[Jan 1, Dec 31 of the previous year]` is crossed, and the honest
        answer to "which paydays fell this year before its first day" is none
        -- where ``overlapping_window`` REFUSES a crossed range, because there
        a reversed pair is a caller defect rather than an ordinary question.
        """
        calendar = _calendar(6, opening=date(2026, 1, 1))
        assert paydays_in_year_before(calendar, date(2026, 1, 1)) == ()


class TestTheSeamBetweenSavedAndProjected:
    """Where the saved schedule stops and the owner's cadence carries on."""

    def test_the_span_tiles_across_the_horizon(self):
        """A month straddling the horizon holds BOTH halves exactly once.

        Two saved paydays put the horizon at 2026-01-29, so 2026-01-30 is the
        first projected one.  January must answer 2 / 16 / 30 -- neither
        dropping the projected payday nor counting the saved ones twice.
        """
        calendar = _calendar(2)
        assert calendar.saved()[-1].end_date == date(2026, 1, 29)
        assert paydays_in_month_through(calendar, date(2026, 1, 31)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )

    def test_a_span_wholly_past_the_horizon_is_projected(self):
        """The rhythm continues at the owner's cadence (``R-PC9``)."""
        assert paydays_in_month_through(_calendar(2), date(2026, 3, 31)) == (
            date(2026, 3, 13), date(2026, 3, 27),
        )

    def test_a_span_wholly_inside_the_schedule_projects_nothing(self):
        """The projection is not walked when the saved schedule covers the span."""
        assert paydays_in_month_through(_calendar(26), date(2026, 3, 31)) == (
            date(2026, 3, 13), date(2026, 3, 27),
        )

    def test_a_far_future_span_costs_the_answer_and_not_the_distance(self):
        """THE FIRING CONTROL for the projection's start argument.

        An adversarial review of plan step **balance:X-bh-1** measured the
        first cut stepping from the horizon to the span, which put **442 ms**
        of pure CPU on one ``/analytics/calendar/2100?view=year`` render at the
        one-day cadence ``budget.pay_schedule`` admits.  The producer now jumps
        arithmetically, so this answers a month 74 years past the horizon
        without walking to it.  Asserted as a BOUND on the work rather than as
        a duration, because a wall-clock assertion is a flake on a loaded box:
        the daily rhythm puts 31 paydays in the month and the walk may not
        exceed them by more than the one period ``from_day`` admits.
        """
        calendar = _calendar(3, cadence=1)
        answer = paydays_in_month_through(calendar, date(2100, 5, 31))
        assert len(answer) == 31
        assert answer[0] == date(2100, 5, 1)
        assert answer[-1] == date(2100, 5, 31)

    def test_a_year_to_date_past_the_horizon_counts_the_projection(self):
        """The YEAR span projects too, which nothing else covers.

        The month span's seam is pinned above; an adversarial review of plan
        step **balance:X-bh-1** noted the year span had no direct case, and it
        is the one the FICA wage-base cumulative reads.  Two saved paydays put
        the horizon at 2026-01-29, so a 2026-04-10 paycheck has seven paydays
        before it in its year -- 01-02 and 01-16 recorded, then 01-30, 02-13,
        02-27, 03-13 and 03-27 projected -- where counting saved rows alone
        would answer two and reach the wage base five paychecks late.
        """
        assert paydays_in_year_before(_calendar(2), date(2026, 4, 10)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
            date(2026, 2, 13), date(2026, 2, 27), date(2026, 3, 13),
            date(2026, 3, 27),
        )

    def test_the_projection_stops_at_the_applications_calendar(self):
        """Past ``CALENDAR_DATE_MAX`` this application has no calendar."""
        assert paydays_in_month_through(
            _calendar(3), date(2101, 1, 31),
        ) == ()


class TestTheBackwardRhythmAndItsFloor:
    """Plan step **balance:X-bh-2**, ruling **balance:R-IA**: how far back.

    The forward continuation is bounded by the APPLICATION
    (``CALENDAR_DATE_MAX``) and the backward one by the OWNER
    (``budget.pay_schedule.history_opens_on``).  These grade the second bound:
    that it is read, that it is a FLOOR rather than an anchor, that ``NULL``
    means the app's own calendar floor, and that the two halves of the rhythm
    meet without a gap or a repeat.
    """

    def test_a_stated_floor_drops_the_paydays_below_it(self):
        """The bound is READ, and dropping it changes the count.

        January 2026's rhythm runs 01-02, 01-16, 01-30 forward and 2025-12-19,
        12-05, 11-21 ... backward.  An owner who says their paychecks began
        2025-12-10 is paid once in December, not twice.
        """
        assert paydays_in_month_through(
            _calendar(26, history_opens_on=date(2025, 12, 10)),
            date(2025, 12, 31),
        ) == (date(2025, 12, 19),)

    def test_a_floor_ON_the_opening_payday_counts_nothing_below_the_record(self):
        """The owner whose first payday is the first one they were ever paid.

        ``pay_calendar:R-PC14`` calls that an ordinary state -- the Generate
        form asks for "your next (or first) payday" -- and it is the state
        ruling R-IA's rejected alternative could not express: projecting
        backward with no stored bound fabricates an employment history for
        them.
        """
        assert paydays_in_month_through(
            _calendar(26, history_opens_on=_JANUARY_OPENING),
            date(2025, 12, 31),
        ) == ()

    def test_a_floor_ON_a_rhythm_payday_KEEPS_that_payday(self):
        """The floor is INCLUSIVE, and this is the arm nothing measured.

        An adversarial review of plan step balance:X-bh-2 mutated
        ``while day >= lower`` to ``while day > lower`` and the whole suite --
        817 cases -- stayed green, because every floor case here used a day
        deliberately OFF the rhythm and the two on-rhythm cases returned early
        at ``upper < lower`` without reaching the loop.

        It is not an equivalent mutant and it moves money: the owner who types
        the exact day of their first paycheck -- the natural answer to the
        question both forms ask -- would have that paycheck silently dropped,
        shifting every later ordinal in its month down by one.  A 24-per-year
        deduction would then be TAKEN on a paycheck it should skip.

        2025-12-19 is a real rhythm day of a schedule opening 2026-01-02.
        """
        exact = _calendar(26, history_opens_on=date(2025, 12, 19))
        # (a stated floor ON the rhythm -- the whole subject of the case)

        assert paydays_in_month_through(exact, date(2025, 12, 31)) == (
            date(2025, 12, 19),
        )
        # And the day one cadence below it is excluded, so the bound is a
        # FLOOR rather than "everything in the month".
        assert date(2025, 12, 5) not in paydays_in_month_through(
            exact, date(2025, 12, 31),
        )

    def test_a_backdated_payday_ON_new_years_day_is_counted(self):
        """The same boundary where a calendar YEAR opens, which is the costly one.

        A year-to-date span opens on 1 January, so a backdated payday landing
        exactly there sits on ``lower``.  Dropping it takes a whole paycheck
        out of the FICA wage base and out of every ``annual_cap`` -- and 1
        January is precisely where the developer's own rhythm lands (ledger
        row **N-397**), so this is the arm the residual is measured on.
        """
        # A schedule opening 2026-01-15 has 2026-01-01 on its rhythm exactly.
        calendar = _calendar(
            26, opening=date(2026, 1, 15), history_opens_on=date(2025, 1, 1),
        )

        assert (date(2026, 1, 15) - date(2026, 1, 1)).days == _CADENCE
        assert paydays_in_year_before(calendar, date(2026, 1, 15)) == (
            date(2026, 1, 1),
        )
        assert paydays_in_month_through(calendar, date(2026, 1, 14)) == (
            date(2026, 1, 1),
        )

    def test_the_floor_is_not_an_ANCHOR(self):
        """A floor off the rhythm truncates it; it does not re-phase it.

        2025-11-30 is not a payday of a biweekly rhythm anchored on
        2026-01-02, and the answer does not pretend it is: the days returned
        are the rhythm's own, the stated day never appears, and every gap is
        still one cadence.  Anchoring on the stated day instead would put a
        short interval at the seam with the recorded half.

        **The floor leaves TWO days below the record deliberately.**  An
        adversarial review of this step measured the first draft using
        2025-12-10, which leaves exactly ONE -- so ``zip(walked, walked[1:])``
        iterated zero times and the gap assertion, the whole point of the
        case, could never fail.
        """
        calendar = _calendar(26, history_opens_on=date(2025, 11, 30))
        walked = paydays_in_month_through(calendar, date(2025, 12, 31))

        assert walked == (date(2025, 12, 5), date(2025, 12, 19))
        assert date(2025, 11, 30) not in walked  # the stated day itself
        assert len(walked) > 1, "the gap assertion below must have a pair"
        assert all(
            (later - earlier).days == _CADENCE
            for earlier, later in zip(walked, walked[1:])
        )

    def test_a_floor_after_the_opening_payday_is_answered_not_refused(self):
        """A stated floor above the record leaves no backward rhythm.

        Both write doors refuse this pairing (``set_history_opening`` and
        registration, through one shared rule), so it arrives only from legacy
        data or from ``/pay-periods/reset`` rebuilding the schedule from an
        EARLIER first payday -- which moves the record's opening DOWN past a
        floor that was legal when it was written, and leaves
        ``budget.pay_schedule`` untouched.  *An adversarial review of plan step
        balance:X-bh-2 caught this sentence naming the opposite direction: a
        reset to a LATER opening cannot produce the state, because the floor
        was already at or below the old opening and the new one is higher
        still.*  The producer stays TOTAL for it: an empty backward half is the
        honest answer, where a refusal would 500 a salary render on a state no
        owner can see.
        """
        assert paydays_in_month_through(
            _calendar(26, history_opens_on=date(2026, 6, 1)),
            date(2025, 12, 31),
        ) == ()

    def test_the_floor_bounds_the_PROJECTION_and_never_the_RECORD(self):
        """A SAVED payday below a stated floor is still counted.

        The state the case above describes, from the other side.  After a reset
        to an earlier first payday the record can hold paydays below the stored
        floor, and those are facts the owner entered -- the bound is a
        statement about the UNRECORDED past, so it may not delete them.  This
        is what makes the contradictory pairing degrade to "no backward
        rhythm" rather than to a schedule with a hole in it.
        """
        # January 2026 holds three RECORDED paydays: 01-02, 01-16, 01-30.
        calendar = _calendar(26, history_opens_on=date(2026, 6, 1))

        assert paydays_in_month_through(calendar, date(2026, 1, 31)) == (
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
        )

    def test_a_floor_at_the_apps_own_calendar_reaches_as_far_as_it_can(self):
        """``CALENDAR_DATE_MIN`` is a BOUND on the column, not a meaning.

        Ruling **balance:R-IA** first had ``NULL`` read as "back to
        2000-01-01"; the 2026-08-31 amendment made ``NULL`` an absence and left
        that date as nothing but the window
        ``ck_pay_schedule_history_opens_range`` admits.  An owner may still
        state it, and then the rhythm reaches it and stops: 2026-01-02 less 678
        fortnights lands on 2000-01-07, so January 2000 holds two rhythm days
        and December 1999 holds none.

        Nobody needs to state it -- the only two readers of this rhythm ask
        over one calendar month or one calendar year, so it never reaches
        twelve months down.  The case exists because the column admits the
        value and the walk must terminate on it.
        """
        calendar = _calendar(26, history_opens_on=date(2000, 1, 1))

        assert (date(2026, 1, 2) - date(2000, 1, 7)).days == 678 * _CADENCE
        assert paydays_in_month_through(calendar, date(2000, 1, 31)) == (
            date(2000, 1, 7), date(2000, 1, 21),
        )
        assert paydays_in_month_through(calendar, date(1999, 12, 31)) == ()

    def test_the_backward_and_saved_halves_meet_with_no_gap_or_repeat(self):
        """One rhythm in three segments, and the seam is where it could break.

        A year span that opens below the record and closes above it walks the
        backward continuation, the saved rows and nothing else.  Ascending,
        distinct, and every gap exactly one cadence: a duplicated seam payday
        would inflate every ordinal and every year-to-date, and an off-by-one
        would drop one.
        """
        # The calendar below opens 2026-07-03, so a 2026 year-to-date asked at
        # year end opens BELOW the record (January is backdated), runs through
        # the seam at 07-03 and closes inside the saved rows.
        days = paydays_in_year_before(
            _calendar(
                26, opening=date(2026, 7, 3),
                history_opens_on=date(2025, 1, 1),
            ),
            date(2026, 12, 31),
        )

        assert days == tuple(sorted(set(days)))
        assert days[0] == date(2026, 1, 2)
        assert date(2026, 7, 3) in days
        assert all(
            (later - earlier).days == _CADENCE
            for earlier, later in zip(days, days[1:])
        )

    def test_ALL_THREE_segments_answer_one_span(self):
        """Backward, saved and projected in one question -- the untested path.

        A short record priced late in its year opens the span below the record
        AND closes it past the horizon, which is the only shape that reaches
        :func:`~._views.projected_paychecks` with a ``from_day`` BELOW the
        horizon.  Nothing else in the suite combines the two.

        Three saved paydays from 2026-06-05 reach 2026-07-16, so a 2026
        year-to-date asked at year end walks all three segments.
        """
        calendar = _calendar(
            3, opening=date(2026, 6, 5), history_opens_on=date(2025, 1, 1),
        )
        days = paydays_in_year_before(calendar, date(2026, 12, 31))

        assert days == tuple(sorted(set(days)))
        assert days[0] == date(2026, 1, 2)          # backdated
        assert date(2026, 6, 5) in days             # saved
        assert date(2026, 12, 18) in days           # projected
        assert all(
            (later - earlier).days == _CADENCE
            for earlier, later in zip(days, days[1:])
        )

    def test_an_IRREGULAR_record_anchors_the_backward_half_on_its_FIRST_payday(
        self,
    ):
        """A saved set need not be an arithmetic progression, and this one is not.

        ``derive_periods`` accepts any distinct sorted payday set -- gaps and
        off-cadence days are legal -- so the two continuations can be out of
        phase with each other.  That is correct rather than a defect: neither
        is ever compared to the other, because the saved rows sit between
        them.  What must hold is that the BACKWARD half anchors on
        ``periods[0]`` and not on the last saved payday.
        """
        calendar = PayCalendar.from_paydays(
            [(1, date(2026, 3, 10)), (2, date(2026, 3, 20)),
             (3, date(2026, 4, 30))],
            (era_of(date(2026, 3, 10), _CADENCE),),
            user_id=1, history_opens_on=date(2025, 1, 1),
        )

        # Anchored on 03-10: 02-24, 02-10 ... not on 04-30 (which would give
        # 02-19, 02-05).
        assert paydays_in_month_through(calendar, date(2026, 2, 28)) == (
            date(2026, 2, 10), date(2026, 2, 24),
        )

    def test_the_cadence_extremes_the_schedule_admits(self):
        """``budget.pay_schedule`` accepts 1..365 and both reach this producer.

        Neither bound had a backward case.  At one day a month holds every
        day; at 365 a month holds at most one and most hold none, and the
        walk must terminate at both.
        """
        daily = _calendar(
            3, opening=date(2026, 4, 1), cadence=1,
            history_opens_on=date(2025, 1, 1),
        )
        annual = _calendar(
            3, opening=date(2026, 4, 1), cadence=365,
            history_opens_on=date(2020, 1, 1),
        )

        assert len(paydays_in_month_through(daily, date(2026, 3, 31))) == 31
        assert paydays_in_month_through(annual, date(2025, 4, 30)) == (
            date(2025, 4, 1),
        )
        assert paydays_in_month_through(annual, date(2025, 5, 31)) == ()

    def test_an_empty_calendar_has_no_rhythm_to_run_backward(self):
        """No payday means no anchor, so there is nothing to step back from."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=1, history_opens_on=date(2020, 1, 1),
        )

        assert paydays_in_month_through(empty, date(2026, 1, 31)) == ()
        assert paydays_in_year_before(empty, date(2026, 6, 1)) == ()


class TestProjectionPaydaysInMonthThrough:
    """The card's twin: the paydays a FORWARD projection holds (**balance:R-IB**).

    The analytics month card reads this one, because it shows a payday only
    where it holds money for it: the saved paydays, and since task plan#3945
    the rhythm continued past the horizon, where the cash tier estimates each
    paycheck, but never the rhythm stepped back below the first payday, where
    nothing is estimated.  It counted the SAVED paydays alone until that task.
    **It absorbed the questions ``earliest_start_in_month`` used to answer**
    when plan step **balance:X-bh-2** deleted that producer as unreached
    (ledger row **N-396**): its length-1 case IS "when does this month's
    first paycheck land".  *Four of the five moved cleanly and the fifth -- a
    month holding exactly ONE payday -- was missed, which an adversarial
    review of the same step caught; it is the third case below.*  The month
    the record opens in is graded on the card itself
    (``test_third_paycheck_and_year.py``).
    """

    def test_it_never_counts_the_rhythm_before_the_first_payday(self):
        """The twin's whole difference from the engine's count.

        December 2025 holds two paydays of the owner's stated rhythm and none
        a forward projection reaches: the record opens 2026-01-02.
        """
        calendar = _calendar(26, history_opens_on=date(2025, 1, 1))

        assert projection_paydays_in_month_through(
            calendar, date(2025, 12, 31),
        ) == ()
        assert paydays_in_month_through(calendar, date(2025, 12, 31)) != ()

    def test_the_first_payday_of_a_month_holding_two(self):
        """January 2026 opens on the 2nd; February on the 13th."""
        calendar = _calendar(26)

        assert projection_paydays_in_month_through(
            calendar, date(2026, 1, 31),
        )[0] == date(2026, 1, 2)
        assert projection_paydays_in_month_through(
            calendar, date(2026, 2, 28),
        )[0] == date(2026, 2, 13)

    def test_the_only_payday_of_a_month_holding_ONE(self):
        """The fifth case ``earliest_start_in_month`` used to carry.

        An adversarial review of plan step balance:X-bh-2 found this shape was
        the one of that producer's five NOT re-homed: the replacement's
        fixture puts two paydays in every month it reaches, so "a month
        holding exactly one" was covered by nothing.  At a 21-day cadence from
        2026-01-05 the paydays are 01-05, 01-26, 02-16 -- February holds one.
        """
        calendar = _calendar(3, opening=date(2026, 1, 5), cadence=21)

        assert projection_paydays_in_month_through(
            calendar, date(2026, 2, 28),
        ) == (date(2026, 2, 16),)

    def test_a_month_the_schedule_covers_but_opens_no_payday_in(self):
        """"Which paycheck covers this day" and "does one START here" differ.

        At a 45-day cadence from 2026-01-02 the paydays are 01-02, 02-16 and
        04-02, so a paycheck COVERS 2026-03-31 while March opens none.
        """
        calendar = _calendar(3, cadence=45)

        assert calendar.period_containing(date(2026, 3, 31)) is not None
        assert projection_paydays_in_month_through(
            calendar, date(2026, 3, 31),
        ) == ()

    def test_a_month_past_the_horizon_counts_the_projected_paydays(self):
        """Past the record the rhythm continues, as the cash tier does (**N-394**).

        Three paydays from 2026-01-02 reach 2026-01-30, so the saved horizon
        is 2026-02-12 and June 2026 is past it.  Continued every 14 days from
        01-30 the rhythm reaches 06-05 and 06-19 in June, and each is a
        paycheck the cash tier estimates, so the card counts both.  The saved
        count this replaced read ``()`` here.
        """
        calendar = _calendar(3)

        assert projection_paydays_in_month_through(
            calendar, date(2026, 6, 30),
        ) == (date(2026, 6, 5), date(2026, 6, 19))
        assert paydays_in_month_through(calendar, date(2026, 6, 30)) == (
            date(2026, 6, 5), date(2026, 6, 19),
        )

    def test_both_halves_of_the_month_key_are_read(self):
        """One careless filter would confuse the same month in another year."""
        # ``history_opens_on`` is ``None``: no backward half, so the two
        # Januaries differ only by year.
        calendar = PayCalendar.from_paydays(
            [(30, date(2025, 1, 9)), (31, date(2026, 1, 23))],
            (era_of(date(2025, 1, 9), 14),), user_id=1, history_opens_on=None,
        )

        assert projection_paydays_in_month_through(
            calendar, date(2025, 1, 31),
        ) == (date(2025, 1, 9),)
        assert projection_paydays_in_month_through(
            calendar, date(2026, 1, 31),
        ) == (date(2026, 1, 23),)

    def test_an_empty_calendar_holds_no_paydays(self):
        """No saved payday means no first payday to project from, a real answer."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=1, history_opens_on=None,
        )

        assert projection_paydays_in_month_through(
            empty, date(2026, 1, 31),
        ) == ()


class TestTheEngineRefusesAPaydayItsCalendarCannotPlace:
    """The (period, calendar) mismatch `PayrollBasis` cannot make unrepresentable."""

    def test_a_foreign_payday_is_refused_rather_than_miscounted(self):
        """Measured 0, 1 and 2 for three foreign paydays before this refusal.

        ``PayrollBasis`` binds the profile to a calendar, but the PERIOD
        arrives separately, so a paycheck can still be priced against the
        wrong owner's schedule.  The count is over the CALENDAR's paydays, so
        such a pairing used to answer silently -- and 0 is the reading that
        drops a 12-per-year deduction and takes a 24-per-year one on a third
        paycheck.
        """
        # Pylint: ``import-outside-toplevel`` -- the engine's private ordinal
        # is imported here rather than at module scope so this file reads as a
        # test of the pay-calendar producers, with one case reaching across to
        # the refusal built on them.
        from app.services.paycheck_calculator._calendar_questions import (  # pylint: disable=import-outside-toplevel
            _month_ordinal,
        )

        calendar = _calendar(26)
        with pytest.raises(PayCalendarError, match="is not paid on"):
            _month_ordinal(calendar, date(2026, 1, 9))

    def test_a_payday_of_this_calendar_is_placed(self):
        """The refusal above fires on the mismatch and not on every call."""
        # Pylint: ``import-outside-toplevel`` -- see above.
        from app.services.paycheck_calculator._calendar_questions import (  # pylint: disable=import-outside-toplevel
            _month_ordinal,
        )

        assert _month_ordinal(_calendar(26), date(2026, 1, 30)) == 3


class TestTheBackwardRhythmAnchorsOnTheErasPhase:
    """Plan step **C17-b-2** (ruling **R-PC66**): the earliest era runs backward from its PHASE.

    ``_backdated_paydays`` stepped its grid from the first RECORDED payday,
    which ``C14-e-3``'s writer records DISPLACED -- so an owner whose record
    opens on a closed day had every backdated payday off by the displacement,
    in the half that feeds the FICA wage base and every ``annual_cap``
    (ledger row **PC-502**, N-495's mirror at the other end).  The era's
    ``effective_from`` is the nominal opening payroll INTENDED; the record is
    what the bank did.
    """

    #: Thanksgiving 2026 as the era's first nominal payday; ``prior`` pays it
    #: on Wednesday 11-25, and that is the day the record opens on.
    _PHASE = date(2026, 11, 26)
    _OPENING = date(2026, 11, 25)

    def _holiday_opened(self):
        """An owner whose first paycheck was Thanksgiving's, paid the day before."""
        return PayCalendar.from_paydays(
            [(1, self._OPENING), (2, date(2026, 12, 10)), (3, date(2026, 12, 24))],
            (era_of(self._PHASE, _CADENCE, BusinessDayShiftEnum.PRIOR),),
            user_id=1, history_opens_on=date(2026, 1, 1),
        )

    def test_the_grid_below_the_record_is_the_eras_not_the_records(self):
        """Hand-computed: the phase's grid is 11-12, 10-29, 10-15 ...; the record's would be 11-11, 10-28.

        Every Thursday here is open, so the backdated days are the nominal
        grid itself, and the one-day offset of the recorded opening is what
        the old anchor propagated down the whole rhythm.
        """
        calendar = self._holiday_opened()
        assert is_business_day(date(2026, 11, 12))

        assert paydays_in_month_through(calendar, date(2026, 11, 30)) == (
            date(2026, 11, 12), self._OPENING,
        )
        assert paydays_in_month_through(calendar, date(2026, 10, 31)) == (
            date(2026, 10, 1), date(2026, 10, 15), date(2026, 10, 29),
        )

    def test_the_recorded_opening_is_not_counted_twice(self):
        """The era's own first payday IS the recorded opening; the saved half owns it."""
        calendar = self._holiday_opened()
        year = paydays_in_year_before(calendar, date(2026, 12, 31))
        assert year.count(self._OPENING) == 1
        assert self._PHASE not in year
        assert len(year) == len(set(year))


class TestTheBackwardRhythmDISPLACES:
    """Plan step **pay_calendar:C14-e-3**: the rhythm below the record is PAID days.

    ``_backdated_paydays`` open-coded the progression twice and returned grid
    days; it asks
    :func:`~app.services.pay_calendar.projected_payday` now, so a rhythm day
    landing on a closed one is counted on the day payroll really paid it.

    **This is the half that closes ledger row N-398**, and the forward half
    does not touch it.  That row's subject is the 2026-01-01 payroll paid
    2025-12-31: the two days sit in different TAX YEARS, so the FICA wage base
    and every ``annual_cap`` turn over between them, and a year-to-date that
    counts the nominal day charges a paycheck to the wrong year.  Both readers
    of this rhythm ask over a calendar month or a calendar year, which is
    exactly where a displacement across a boundary shows up.

    The schedule is the developer's own: first RECORDED payday 2026-03-26 at a
    fourteen-day cadence, whose backward rhythm passes through 2026-01-01 six
    cadences down (84 days).
    """

    _OPENING = date(2026, 3, 26)
    _NOMINAL_NEW_YEAR = date(2026, 1, 1)

    def _developer(self, shift, history_opens_on=date(2025, 1, 1)):
        """The production owner's schedule under *shift*, with a stated history."""
        return _calendar(
            26, opening=self._OPENING, history_opens_on=history_opens_on,
            shift=shift,
        )

    def test_the_grid_really_passes_through_new_years_day(self):
        """The premise every case below rests on, asserted rather than assumed."""
        assert (self._OPENING - self._NOMINAL_NEW_YEAR).days == 6 * _CADENCE
        assert self._NOMINAL_NEW_YEAR.strftime("%A") == "Thursday"
        assert shift_to_business_day(
            self._NOMINAL_NEW_YEAR, BusinessDayShiftEnum.PRIOR,
        ) == date(2025, 12, 31)

    def test_a_holiday_payday_is_counted_in_the_YEAR_IT_WAS_PAID(self):
        """N-398, worked: nine 2026 paychecks under ``prior``, ten under ``none``.

        The year-to-date at 2026-05-21 spans ``[2026-01-01, 2026-05-20]``.
        Under ``none`` the rhythm's 2026-01-01 sits on the span's opening day
        and is counted, giving ten.  Under ``prior`` payroll paid it
        2025-12-31, which is in the 2025 tax year and outside the span, giving
        nine -- **the true count, and the figure ledger row N-398 states**.

        ``next`` gives ten as well, and that is not the same ten: the payday is
        2026-01-02, still inside 2026, so the count is right for a different
        reason than ``none``'s is.  Asserted as a DAY and not only a count,
        because two answers of ten made of different days is precisely what a
        bare length would hide.
        """
        spans = {
            shift: paydays_in_year_before(
                self._developer(shift), date(2026, 5, 21),
            )
            for shift in BusinessDayShiftEnum
        }

        assert len(spans[BusinessDayShiftEnum.PRIOR]) == 9
        assert date(2025, 12, 31) not in spans[BusinessDayShiftEnum.PRIOR]
        assert self._NOMINAL_NEW_YEAR not in spans[BusinessDayShiftEnum.PRIOR]

        assert len(spans[BusinessDayShiftEnum.NONE]) == 10
        assert spans[BusinessDayShiftEnum.NONE][0] == self._NOMINAL_NEW_YEAR

        assert len(spans[BusinessDayShiftEnum.NEXT]) == 10
        assert spans[BusinessDayShiftEnum.NEXT][0] == date(2026, 1, 2)

    def test_a_grid_day_ABOVE_the_span_is_counted_when_it_is_PAID_inside(self):
        """December 2025 holds THREE paydays under ``prior``, and one is January's.

        The walk's upper widening, which is the half that is necessary: the
        2026-01-01 grid day is outside the December span, and payroll pays it
        2025-12-31, which is inside.  A walk that stopped at the last grid day
        within the span would answer two and drop a whole paycheck from that
        month's ordinal -- the number a 24-per-year deduction reads.
        """
        month = paydays_in_month_through(
            self._developer(BusinessDayShiftEnum.PRIOR), date(2025, 12, 31),
        )

        assert month == (
            date(2025, 12, 4), date(2025, 12, 18), date(2025, 12, 31),
        )
        assert paydays_in_month_through(
            self._developer(BusinessDayShiftEnum.NONE), date(2025, 12, 31),
        ) == (date(2025, 12, 4), date(2025, 12, 18))

    def test_a_grid_day_BELOW_the_span_is_counted_when_it_is_PAID_inside(self):
        """``next`` pushes the 2026-01-01 grid day onto a floor of 2026-01-02.

        The other direction, reached through the stated history floor rather
        than the month bound: with ``history_opens_on`` at 2026-01-02 the grid
        day 2026-01-01 falls below the floor, and its payday does not.  It is
        counted, because the floor is a statement about days money moved.
        """
        counted = paydays_in_month_through(
            self._developer(
                BusinessDayShiftEnum.NEXT, history_opens_on=date(2026, 1, 2),
            ),
            date(2026, 1, 31),
        )

        assert counted == (date(2026, 1, 2), date(2026, 1, 15), date(2026, 1, 29))

    def test_the_backward_half_still_MEETS_the_record_exactly(self):
        """No day is counted twice and none is skipped at the seam.

        The saved half is authoritative from ``periods[0].start_date`` up, and
        the backward half stops strictly below it.  Displacement cannot break
        that -- a recorded payday is a day money moved and so is its own
        displacement, and the grid day one cadence below it cannot be pushed up
        a whole cadence -- but the property is asserted rather than argued,
        over a span straddling the seam under every convention.
        """
        for shift in BusinessDayShiftEnum:
            calendar = self._developer(shift)
            # March 2026 straddles the seam: 2026-03-12 comes from the
            # backward walk and 2026-03-26 is the first RECORDED payday.
            span = paydays_in_month_through(calendar, date(2026, 3, 31))
            recorded = {period.start_date for period in calendar.periods}

            assert span == (date(2026, 3, 12), self._OPENING), shift
            assert len(span) == len(set(span)), shift
            assert list(span) == sorted(span), shift
            assert (span[1] - span[0]).days == _CADENCE, shift
            assert span[0] not in recorded and span[1] in recorded, shift

    def test_the_walk_matches_a_BRUTE_FORCE_reference_over_randomised_spans(
        self,
    ):
        """The bound is exhaustive AND minimal, swept rather than argued.

        The reference walks every grid day from far below the span to far above
        it and filters on the PAYDAY, with no cleverness about where to start
        or stop; the producer jumps to two divisions and takes one extra step
        above the span and none below.  Both displacing conventions, cadences
        from the collision floor to 40, spans up to 800 days below the record.

        Seeded, so a failure names the same case twice.  Randomised rather than
        catalogued because what is graded is a BOUND, and a catalogue tests the
        shapes whoever wrote it thought of -- which is the one thing an
        off-by-one in a walk bound survives.

        **Two counters make it non-vacuous.**  One case must need the step
        ABOVE the span (a grid day outside it, paid inside) or the widening is
        ungraded; and one payday must differ from its own grid day, or the
        whole sweep ran on the nominal path.
        """
        rng = random.Random(1439)
        floor = shortest_collision_free_cadence()
        # **The OPENING is randomised, and an adversarial review of
        # ``C14-e-3`` is why.**  A first form of this sweep fixed it at a
        # Thursday -- a business day, hence its own displacement -- which is
        # the ONE axis the partition between the two halves rests on.  With it
        # held constant the sweep could not see the phantom paycheck a CLOSED
        # opening produced under ``prior``, and the seam case beside it opened
        # on a business day too, so neither could.  ``_OPENINGS`` carries both
        # kinds and the counters below assert both were drawn.
        openings = (date(2029, 6, 21), date(2026, 11, 26), date(2026, 7, 3))
        mismatches, needed_the_step_above, displaced_any = [], 0, 0
        closed_openings = 0

        for _ in range(4000):
            cadence = rng.randint(floor, 40)
            shift = rng.choice(
                [BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT],
            )
            opening = rng.choice(openings)
            closed_openings += not is_business_day(opening)
            first_day = opening - timedelta(days=rng.randint(1, 800))
            last_day = min(
                first_day + timedelta(days=rng.randint(0, 400)),
                opening - timedelta(days=1),
            )
            calendar = _calendar(
                4, opening=opening, cadence=cadence,
                history_opens_on=first_day, shift=shift,
            )

            grid = [
                opening + timedelta(days=step * cadence)
                for step in range(-(900 // cadence) - 2, 3)
            ]
            reference = tuple(
                day for day in (
                    shift_to_business_day(g, shift)
                    for g in grid if g < opening
                )
                if first_day <= day <= last_day
            )
            produced = _backdated_paydays(calendar, first_day, last_day)
            # The two halves may never both claim a day.  The reference above
            # excludes grid INDEX 0 -- the recorded opening, which the saved
            # half owns -- so a producer that emitted its displacement would
            # mismatch; this asserts the same thing of the composed answer,
            # which is what a consumer actually reads.
            whole = _paydays_between(calendar, first_day, last_day)
            assert len(whole) == len(set(whole)), (cadence, shift.name, opening)

            where = (cadence, shift.name, first_day, last_day)
            if produced != reference:
                mismatches.append(where)
            needed_the_step_above += any(
                g > last_day and shift_to_business_day(g, shift) in reference
                for g in grid
            )
            displaced_any += any(
                shift_to_business_day(g, shift) != g and
                shift_to_business_day(g, shift) in reference
                for g in grid
            )

        assert not mismatches, mismatches[:5]
        assert needed_the_step_above > 0, (
            "no case needed the grid step ABOVE the span, so the sweep never "
            "graded the widening it exists to check"
        )
        assert displaced_any > 0, "the sweep never displaced a payday"
        assert closed_openings > 0, (
            "every case opened on a business day, so the sweep held constant "
            "the one axis the two halves' partition rests on"
        )
