"""
Shekel Budget App -- the pay calendar's ERA readers (plan step C17-b-2).

Rulings **R-PC66** and **R-PC72** (developer, 2026-09-11): a pay schedule is
a sequence of eras, an era governs from its first payday to the next era's,
and every reader of the calendar anchors on the phase of the era covering its
own day rather than on a recorded cash payday.  This module grades the
producers that rule is built from -- :mod:`app.services.pay_calendar._eras` --
and the piecewise projection over them, which no other suite drives across a
seam between two eras.

Three things are pinned here that the derivation and value suites cannot see:

* **the matching rule** -- a recorded payday stands for the planned payday
  NEAREST to it, a tie exactly half a cadence off going to the LATER one --
  against a brute-force listing of the grid rather than the producer's loop;
* **the seam** -- the next era's first payday REPLACES the old era's last
  planned payday at or before it (ruling **R-PC75**, plan step ``C17-c-2b``,
  revising R-PC72's clause), measured in CASH days, so a day is never paid
  twice and never left in no paycheck where two conventions meet, and the
  projection reproduces what the regenerate that minted the later era wrote;
* **the ordinal** -- a projected ``period_index`` continues the saved sequence
  across the seam by exactly the number of paydays between.

The projection sweep at the bottom is a brute-force reference: it LISTS every
era's cash paydays inside its window and walks
:func:`~app.services.pay_calendar._views.projected_paychecks` against the list,
over randomised era sequences whose seams the write door's floor would admit.

**No date here is read from a clock.**  Every date is a literal or is derived
from one by explicit arithmetic, so these pass identically under
``TZ=Pacific/Kiritimati`` and the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).
"""

import random
from datetime import date, timedelta
from itertools import islice

import pytest

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    PeriodWindow,
    derive_periods,
    era_index_at,
    payday_after,
    planned_paydays_after,
    projected_payday,
)
# Package-PRIVATE on purpose: the producers below have no caller outside the
# package, and the W9910 gate (``shekel-private-module-import``) runs over
# ``app/`` and ``scripts/``, not this tree -- the same reach
# ``test_pay_calendar_derivation.py`` takes for ``_projection``.
from app.services.pay_calendar._eras import (
    first_payday_of,
    horizon_step,
    last_step_of,
    matched_step,
    step_after,
)
from app.services.pay_calendar._projection import project_period_after
from app.services.pay_calendar._views import projected_paychecks
from app.services.pay_rhythm import era_covering
from app.utils.business_days import (
    is_business_day,
    shift_to_business_day,
    shortest_collision_free_cadence,
)
from tests._test_helpers import era_of, rhythm_of
from tests.oracles.pay_calendar_derivation import next_grid_payday_after

PRIOR, NEXT, NONE = (
    BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT,
    BusinessDayShiftEnum.NONE,
)

#: The worked example the step was ruled on.  ``A`` pays every 14 days from
#: Friday 2026-01-02; ``B`` pays every 7 days from Wednesday 2026-07-01, a day
#: OFF ``A``'s grid (its nearest are 06-19 and 07-03).  Truncated back to
#: 03-13 the owner holds both eras and a record that ends inside ``A``.
#: **Under R-PC75 (``C17-c-2b``) ``A``'s last paycheck is 06-05, not 06-19**:
#: 07-01 replaces the 06-19 the regenerate that minted ``B`` deleted, so the
#: 06-05 paycheck runs 26 days to 06-30.  The developer confirmed the pinned
#: dates move (CLAUDE.md rule 5's exception), and every 06-19 below moved.
ERA_A = era_of(date(2026, 1, 2), 14)
ERA_B = era_of(date(2026, 7, 1), 7)
TRUNCATED_RECORD = [(1, date(2026, 1, 2)), (2, date(2026, 3, 13))]

#: 2030-11-28 is the fourth Thursday of November 2030 -- Thanksgiving -- and
#: this era's grid passes through it: 2030-01-10 + 23 fortnights.  Under
#: ``prior`` payroll pays it 2030-11-27, which is the specification's own
#: worked example (C17-b-2's specification, shipped at ``06fc0d33``).
THANKSGIVING_ERA = era_of(date(2030, 1, 10), 14, PRIOR)


def _calendar(paydays, eras, history_opens_on=None):
    """Build a calendar over *paydays* and *eras* for owner 1."""
    return PayCalendar.from_paydays(
        paydays, eras, user_id=1, history_opens_on=history_opens_on,
    )


# ---------------------------------------------------------------------------
# The matching rule
# ---------------------------------------------------------------------------


class TestARecordedPaydayStandsForItsNearestPlannedOne:
    """``matched_step``: which planned paycheck a recorded payday IS.

    Ruled 2026-09-11 on the fork the step presented: the plan is the truth for
    the future, the record for the past, and a record is matched to the
    planned payday nearest it.  The alternative -- the first grid payday
    strictly after the record -- names the very paycheck an early payment
    already stands for, and projects income the owner never receives.
    """

    def test_a_payday_on_the_grid_is_its_own_step(self):
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 30)) == 2

    def test_a_payday_paid_EARLY_stands_for_the_planned_one_it_precedes(self):
        """The catalogue's thirteen-day shape: 01-15 IS the 01-16 paycheck."""
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 15)) == 1

    def test_a_payday_paid_LATE_stands_for_the_planned_one_it_follows(self):
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 20)) == 1

    def test_a_tie_goes_to_the_LATER_planned_payday(self):
        """Seven days off a fortnight: read as the next paycheck paid early.

        The poorer forecast -- the following paycheck lands further out.  ONE
        rule for both halves: the backward walk reads the same answer for the
        opening payday, where it is the over-counting direction, which the
        producer's docstring names as the price of a record being one
        paycheck.  Pinned in both directions so ``<`` and ``<=`` cannot be
        swapped silently.
        """
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 9)) == 1
        assert matched_step(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 8)) == 0

    def test_it_measures_from_the_DISPLACED_payday_not_the_nominal_one(self):
        """Thanksgiving 2030 under ``prior``: paid 11-27, stands for nominal 11-28.

        Measured against the nominal grid the record would sit a day BEFORE
        its own step; measured against the cash day it sits ON it.
        """
        era = THANKSGIVING_ERA
        nominal_steps = (date(2030, 11, 28) - era.effective_from).days // 14
        assert projected_payday(era.effective_from, era.rhythm, nominal_steps) == (
            date(2030, 11, 27)
        )
        assert matched_step(era.effective_from, era.rhythm, date(2030, 11, 27)) == (
            nominal_steps
        )

    def test_it_agrees_with_a_brute_force_listing_over_randomised_records(self):
        """The loop is graded against an explicit grid, not against itself.

        Cadences below the collision floor (legal under ``none`` only) and
        the 365-day ceiling are drawn beside the floor..45 range: the
        residue sweep ``C14-e-2`` wrote for the extend door's grid search
        covered those tails, and plan step ``C17-c-2b`` deleted that search
        -- this producer is where the record meets the grid now.
        """
        rng = random.Random(1702)
        floor = shortest_collision_free_cadence()
        phase = date(2027, 3, 5)
        for _ in range(3000):
            cadence = rng.choice([
                rng.randint(1, floor - 1), rng.randint(floor, 45), 365,
            ])
            shift = NONE if cadence < floor else rng.choice([PRIOR, NEXT, NONE])
            record = phase + timedelta(days=rng.randint(-200, 900))
            rhythm = rhythm_of(cadence, shift)

            expected_next = next_grid_payday_after(phase, cadence, shift, record)
            matched = matched_step(phase, rhythm, record)

            assert projected_payday(phase, rhythm, matched + 1) == expected_next, (
                cadence, shift.name, record,
            )


class TestTheStepAfterADerivedDay:
    """``step_after``: the first grid step PAID strictly after a day."""

    def test_it_answers_the_step_whose_payday_clears_the_day(self):
        assert step_after(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 15)) == 1
        assert step_after(date(2026, 1, 2), rhythm_of(14), date(2026, 1, 16)) == 2

    def test_it_reads_the_displaced_payday(self):
        """Thanksgiving 2030 under ``prior`` is paid 11-27, so 11-27 is cleared only by the step after it."""
        era = THANKSGIVING_ERA
        steps = step_after(era.effective_from, era.rhythm, date(2030, 11, 27))
        assert projected_payday(era.effective_from, era.rhythm, steps) == date(2030, 12, 12)

    def test_it_refuses_when_no_step_within_two_cadences_clears_the_day(self):
        """N-493's reported hole, stated once here and inherited by every caller.

        A one-day cadence under ``prior`` across a weekend: three consecutive
        nominal days pay on the same Friday, so no candidate clears it.
        """
        friday = date(2026, 1, 9)
        with pytest.raises(PayCalendarError, match="within two cadences"):
            step_after(friday, rhythm_of(1, PRIOR), friday)


# ---------------------------------------------------------------------------
# Which era covers a day
# ---------------------------------------------------------------------------


class TestWhichEraCoversADay:
    """``era_index_at`` reads the rule in CASH days; ``era_covering`` in nominal."""

    def test_a_day_before_every_era_is_the_earliest_eras(self):
        assert era_index_at((ERA_A, ERA_B), date(2025, 6, 1)) == 0

    def test_a_day_between_two_eras_belongs_to_the_earlier(self):
        assert era_index_at((ERA_A, ERA_B), date(2026, 6, 30)) == 0
        assert era_index_at((ERA_A, ERA_B), date(2026, 7, 1)) == 1

    def test_an_era_minted_on_a_holiday_under_prior_owns_its_first_paycheck(self):
        """The cash and nominal readings PART on a displaced first payday.

        An era minted on Thanksgiving 2030 under ``prior`` pays its first
        paycheck on 11-27, a day before its own ``effective_from``.  That
        paycheck is the new era's -- the nominal reading hands it to the old
        era, which would close it on the old grid and project the new era's
        first payday on top of it.
        """
        old = era_of(date(2030, 1, 5), 7)
        new = era_of(date(2030, 11, 28), 14, PRIOR)
        assert first_payday_of(new) == date(2030, 11, 27)

        assert era_index_at((old, new), date(2030, 11, 27)) == 1
        assert era_covering((old, new), date(2030, 11, 27)) is old

    def test_the_two_readings_agree_wherever_no_first_payday_is_displaced(self):
        for day in (date(2026, 3, 1), date(2026, 6, 30), date(2026, 7, 1),
                    date(2027, 1, 1)):
            assert (ERA_A, ERA_B)[era_index_at((ERA_A, ERA_B), day)] is (
                era_covering((ERA_A, ERA_B), day)
            )


# ---------------------------------------------------------------------------
# The next payday after the record
# ---------------------------------------------------------------------------


class TestTheNextPaydayAfterTheRecord:
    """``payday_after`` and ``horizon_step``: where the record's continuation opens."""

    def test_the_specifications_worked_example(self):
        """A last recorded 2030-11-27 projects 12-12, where the recorded anchor gave 12-11.

        The step's own worked example (C17-b-2's specification, shipped at
        ``06fc0d33``): the record is the nominal 11-28 Thanksgiving paid a day
        early under ``prior``; anchored
        on the record the next payday came a day early too, and every
        boundary after it.
        """
        eras = (THANKSGIVING_ERA,)
        assert payday_after(eras, date(2030, 11, 27)) == date(2030, 12, 12)
        assert projected_payday(date(2030, 11, 27), THANKSGIVING_ERA.rhythm, 1) == (
            date(2030, 12, 11)
        )

    def test_it_is_strictly_after_the_record_below_the_collision_floor(self):
        """Ledger row PC-505, closed: a sub-floor pairing cannot derive a reversed period.

        The row's own case -- paydays 2030-11-13 and 11-15 at a two-day
        cadence under ``prior`` -- derived ``start 11-15, end 11-14`` while
        the last end stepped one cadence from the record and displaced it
        BACK.  The next payday is now found strictly after the record it
        follows, so the period closes on or after the day it opens.
        """
        derived = derive_periods(
            [(1, date(2030, 11, 13)), (2, date(2030, 11, 15))],
            (era_of(date(2030, 11, 13), 2, PRIOR),),
        )
        assert derived[-1].end_date >= derived[-1].start_date
        assert derived[-1].end_date == date(2030, 11, 18)

    def test_it_rolls_into_the_next_era_when_the_covering_eras_grid_is_spent(self):
        """A's last planned payday is 06-05; the payday after it is B's first.

        *A record ending 06-19 is unreachable under R-PC75 -- the regenerate
        that minted B deleted that payday -- so this case moved to the record
        the regenerate leaves.*
        """
        assert horizon_step((ERA_A, ERA_B), date(2026, 6, 5)) == (1, 0)
        assert payday_after((ERA_A, ERA_B), date(2026, 6, 5)) == date(2026, 7, 1)

    def test_a_record_on_the_payday_the_seam_replaced_is_read_as_the_next_eras_first(
        self,
    ):
        """06-19 is not a planned payday of A any more, so a record there stands for B's 07-01.

        The state no door leaves (the regenerate that minted B deleted
        06-19), pinned so the degradation is a stated one rather than a
        surprise: matched within A's grid it is step 12, past A's last step
        11, and over the planned list the nearer of A's 06-05 (14 days) and
        B's 07-01 (12 days) is B's first paycheck, paid early.
        """
        assert horizon_step((ERA_A, ERA_B), date(2026, 6, 19)) == (1, 1)
        assert payday_after((ERA_A, ERA_B), date(2026, 6, 19)) == date(2026, 7, 8)

    def test_it_stays_on_the_covering_eras_grid_while_that_grid_reaches(self):
        assert horizon_step((ERA_A, ERA_B), date(2026, 3, 13)) == (0, 6)
        assert payday_after((ERA_A, ERA_B), date(2026, 3, 13)) == date(2026, 3, 27)

    def test_the_planned_paydays_after_the_record_are_one_sequence(self):
        """``payday_after`` is the FIRST of ``planned_paydays_after``; the batch ceiling is the second.

        Plan step ``pay_calendar:C17-c-2a``: the floor and its mirror read
        one sequence, so they cannot come apart -- and since ``C17-c-2b``
        the continue door (``pay_period_write.continue_paydays``) records a
        prefix of the same sequence, so what it writes cannot come apart
        from either.  Worked on the ruled example with the record ending
        03-13, and again from A's last planned payday 06-05, where the
        sequence crosses the seam at 07-01 (R-PC75: 06-19 is not A's).
        """
        eras = (ERA_A, ERA_B)
        planned = planned_paydays_after(eras, date(2026, 3, 13))
        first = next(planned)
        assert first == payday_after(eras, date(2026, 3, 13)) == date(2026, 3, 27)
        assert next(planned) == date(2026, 4, 10)
        assert list(islice(planned_paydays_after(eras, date(2026, 6, 5)), 3)) == [
            date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15),
        ]

    def test_an_eras_last_step_is_the_one_before_the_payday_the_seam_replaces(self):
        """``A`` pays 06-05 as its last: B's 07-01 REPLACES A's 06-19 (R-PC75).

        The step before the last of A's planned paydays at or before 07-01
        -- 06-19 is step 12, so the answer is 11.  *It was 12 under R-PC72's
        literal seam, which put 06-19 back after any truncate below 07-01.*
        """
        assert last_step_of((ERA_A, ERA_B), 0) == 11
        assert projected_payday(ERA_A.effective_from, ERA_A.rhythm, 11) == date(2026, 6, 5)
        assert projected_payday(ERA_A.effective_from, ERA_A.rhythm, 12) == date(2026, 6, 19)
        assert last_step_of((ERA_A, ERA_B), 1) is None

    def test_a_first_payday_ON_the_old_grid_replaces_that_payday_and_no_other(self):
        """B opening exactly on A's 06-19 leaves A paying through 06-05, as with 07-01.

        The ``at or before`` half: 06-19 is at or before itself, so it is the
        payday the seam replaces, and the one before it is A's last.
        """
        on_grid = era_of(date(2026, 6, 19), 7)
        assert last_step_of((ERA_A, on_grid), 0) == 11
        assert payday_after((ERA_A, on_grid), date(2026, 6, 5)) == date(2026, 6, 19)

    def test_a_record_is_matched_over_the_PLANNED_list_across_the_seam(self):
        """The 02-02 paycheck paid a day early stands for the NEXT era's first, not the old era's last.

        Eras 14 days from 01-02 and 7 days from 02-02; the record ends 02-01.
        Inside the first era's grid alone the nearest planned payday is
        01-30 (two days), so the record would close on 02-01 and a paycheck
        would be projected on 02-02 -- the phantom the matching rule exists to
        refuse.  Over the planned list the nearest is the next era's 02-02
        (one day), and the paycheck runs to 02-08.  *An adversarial review of
        this step built the case.*
        """
        eras = (era_of(date(2026, 1, 2), 14), era_of(date(2026, 2, 2), 7))
        derived = derive_periods(
            [(1, date(2026, 1, 2)), (2, date(2026, 1, 16)), (3, date(2026, 2, 1))],
            eras,
        )
        assert derived[-1].end_date == date(2026, 2, 8)
        assert payday_after(eras, date(2026, 2, 1)) == date(2026, 2, 9)
        assert horizon_step(eras, date(2026, 2, 1)) == (1, 1)

    def test_the_first_paycheck_of_a_holiday_minted_era_closes_on_its_own_grid(self):
        """The prior-displaced first payday belongs to the new era, so its period does too."""
        old = era_of(date(2030, 1, 5), 7)
        new = era_of(date(2030, 11, 28), 14, PRIOR)
        assert payday_after((old, new), date(2030, 11, 27)) == date(2030, 12, 12)


# ---------------------------------------------------------------------------
# What the calendar refuses
# ---------------------------------------------------------------------------


class TestTheEraSequenceIsValidated:
    """``validate_eras``, through the derivation: what no calendar can be derived from."""

    def test_no_era_is_refused(self):
        with pytest.raises(PayCalendarError, match="at least one era"):
            derive_periods([], ())

    def test_eras_out_of_order_are_refused(self):
        with pytest.raises(PayCalendarError, match="strictly ascending"):
            derive_periods([], (ERA_B, ERA_A))

    def test_a_repeated_effective_from_is_refused(self):
        with pytest.raises(PayCalendarError, match="strictly ascending"):
            derive_periods([], (ERA_A, era_of(ERA_A.effective_from, 7)))

    def test_first_paydays_that_cross_are_refused(self):
        """Ascending phases whose FIRST PAYDAYS invert: one era would pay nothing.

        ``next`` pushes a Saturday phase to Monday; ``prior`` pulls the
        following Sunday's back to Friday.  Refused loudly at the value
        boundary, as a repeated payday is; which DOOR keeps a stored sequence
        out of this state is the writer's question (the floor bounds the
        batch's NEW paydays, not the era's first, and an adversarial review
        of this step drove a sequence through the pure door functions that
        reaches it -- see the ledger row the tick files).  *Since R-PC75 the
        refusal is the seam rule's own -- the earlier era's last step is
        below zero -- and its message says so.*
        """
        saturday, sunday = date(2026, 11, 28), date(2026, 11, 29)
        crossed = (era_of(saturday, 14, NEXT), era_of(sunday, 7, PRIOR))
        assert first_payday_of(crossed[0]) > first_payday_of(crossed[1])
        with pytest.raises(PayCalendarError, match="would pay nothing"):
            derive_periods([], crossed)

    def test_an_era_within_one_cadence_of_the_previous_ones_first_payday_is_refused(self):
        """R-PC75's own refusal: the first payday would replace the ONLY payday before it.

        A 7-day era from 01-09 after a 14-day era from 01-02: 01-09 is at or
        before no planned payday of the first era but its 01-02, which it
        replaces, so the first era pays nothing.  First paydays ascend here,
        which is what the check this replaced accepted; from 01-16 -- the
        first era's second planned payday, on which it pays exactly its first
        -- the sequence is legal.
        """
        first = era_of(date(2026, 1, 2), 14)
        with pytest.raises(PayCalendarError, match="would pay nothing"):
            derive_periods([], (first, era_of(date(2026, 1, 9), 7)))
        legal = (first, era_of(date(2026, 1, 16), 7))
        derive_periods([], legal)
        assert last_step_of(legal, 0) == 0

    def test_every_eras_cadence_is_validated(self):
        with pytest.raises(PayCalendarError, match="at least 1 day"):
            derive_periods([], (ERA_A, era_of(date(2027, 1, 1), 0)))


# ---------------------------------------------------------------------------
# The piecewise projection
# ---------------------------------------------------------------------------


class TestTheProjectionIsPiecewise:
    """The ruled worked example, read off the calendar the app builds."""

    def test_the_last_saved_paycheck_closes_on_ITS_eras_grid(self):
        """03-13 is A's paycheck, so it runs to 03-26, not to a day on B's grid."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        assert calendar.periods[-1].end_date == date(2026, 3, 26)
        assert calendar.periods[-1].end_is_projected is True

    def test_april_to_june_project_on_A_and_july_onward_on_B(self):
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        projected = list(
            period for period in projected_paychecks(calendar.periods, calendar.eras)
            if period.start_date <= date(2026, 7, 22)
        )
        assert [p.start_date for p in projected] == [
            date(2026, 3, 27), date(2026, 4, 10), date(2026, 4, 24),
            date(2026, 5, 8), date(2026, 5, 22), date(2026, 6, 5),
            date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15),
            date(2026, 7, 22),
        ]

    def test_the_seam_closes_As_last_paycheck_the_day_before_B_opens(self):
        """A's last paycheck opens 06-05 and runs 26 days to 06-30 (R-PC75)."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        last_of_a = calendar.span_containing(date(2026, 6, 30))
        first_of_b = calendar.span_containing(date(2026, 7, 1))
        assert (last_of_a.start_date, last_of_a.end_date) == (
            date(2026, 6, 5), date(2026, 6, 30),
        )
        assert calendar.span_containing(date(2026, 6, 19)) == last_of_a
        assert (first_of_b.start_date, first_of_b.end_date) == (
            date(2026, 7, 1), date(2026, 7, 7),
        )

    def test_the_ordinal_continues_across_the_seam(self):
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        assert calendar.periods[-1].period_index == 1
        assert calendar.span_containing(date(2026, 3, 27)).period_index == 2
        assert calendar.span_containing(date(2026, 6, 5)).period_index == 7
        assert calendar.span_containing(date(2026, 6, 19)).period_index == 7
        assert calendar.span_containing(date(2026, 7, 1)).period_index == 8
        assert calendar.span_containing(date(2026, 7, 8)).period_index == 9

    def test_the_axis_tiles_across_the_seam(self):
        """``PeriodWindow`` refuses a hole and an overlap; the seam produces neither."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        window = calendar.projection_axis(date(2026, 3, 13), date(2026, 9, 30))
        assert window.periods[0].start_date == date(2026, 3, 13)
        assert window.periods[-1].end_date >= date(2026, 9, 30)
        assert PeriodWindow(periods=window.periods).periods == window.periods

    def test_a_single_era_owner_reads_exactly_as_before(self):
        """Production's shape: one era, ``none`` -- ``$0.00`` by construction."""
        paydays = [
            (index + 1, date(2026, 3, 26) + timedelta(days=14 * index))
            for index in range(63)
        ]
        calendar = _calendar(paydays, (era_of(date(2026, 3, 26), 14),))
        assert calendar.periods[-1].end_date == date(2028, 8, 23)
        span = calendar.span_containing(date(2030, 1, 1))
        # 2026-03-26 + 98 fortnights is 2029-12-27 (1,372 days; 2028 is a
        # leap year), the 99th paycheck, running to the day before 2030-01-10.
        assert span.start_date == date(2029, 12, 27)
        assert span.end_date == date(2030, 1, 9)
        assert span.period_index == 98
        assert span.start_date == date(2026, 3, 26) + timedelta(days=14 * 98)

    def test_it_matches_a_brute_force_listing_over_randomised_era_sequences(self):
        """The seam and the ordinal, swept rather than argued.

        The reference LISTS each era's cash paydays -- its grid displaced
        under its own convention, from its first payday while the planned
        payday AFTER each is at or before the next era's first, which is
        the one the next era's first payday replaces (ruling **R-PC75**) --
        and concatenates the eras; the producer walks
        :func:`~app.services.pay_calendar._views.projected_paychecks` from the
        record.  Starts, ends and ordinals must agree period for period.

        The sequences are built the way the doors build them, on the
        nominal grid: each later era's phase lands at or after the previous
        era's NEXT payday past the record it left (the floor) and within one
        of its cadences (the gap rule, R-PC67 -- approximately, since a
        displaced phase can land its cash day at the ceiling the writer
        would refuse; the reference reads R-PC75 either way), and the record
        is a prefix of the earliest
        era's planned paydays under that same rule -- the record a
        regenerate leaves and a truncate exposes -- ending inside an era
        that is not the last, so every case crosses at least one seam.
        Cadences run from the collision floor to 40, both displacing
        conventions and ``none`` are drawn, and the counters below assert
        that a seam was actually displaced, that a case rolled straight into
        the next era from the record, and that a case's record ended inside
        a seam paycheck longer than its era's cadence (the state R-PC72's
        literal seam projected a phantom payday into).
        """
        rng = random.Random(4471)
        floor = shortest_collision_free_cadence()
        mismatches, displaced_seams, rolled_at_record = [], 0, 0
        long_seam_paychecks = 0

        for _ in range(600):
            eras = [era_of(
                date(2027, 1, 1) + timedelta(days=rng.randint(0, 27)),
                rng.randint(floor, 40), rng.choice([PRIOR, NEXT, NONE]),
            )]
            for _more in range(rng.randint(1, 2)):
                previous = eras[-1]
                # The record the previous era left: its first payday plus a
                # few of its own paychecks; the next era opens between the
                # following payday and the one after it.
                left = previous.effective_from + timedelta(
                    days=previous.rhythm.cadence.days * rng.randint(1, 4),
                )
                lower = next_grid_payday_after(
                    previous.effective_from, previous.rhythm.cadence.days,
                    previous.rhythm.shift, left,
                )
                candidate_shift = rng.choice([PRIOR, NEXT, NONE])
                candidate_cadence = rng.randint(floor, 40)
                # Every phase inside the window the writer admits, drawn
                # at random rather than the first that fits: a first-fit
                # draw put F on the floor itself nearly every time, and the
                # second half of the window is where R-PC75's rule and the
                # rejected NEAREST rule part.
                phases = [
                    nominal
                    for offset in range(previous.rhythm.cadence.days)
                    for nominal in (lower + timedelta(days=offset),)
                    if nominal > previous.effective_from
                    and shift_to_business_day(nominal, candidate_shift) >= lower
                ]
                if not phases:
                    break
                eras.append(era_of(rng.choice(phases), candidate_cadence, candidate_shift))
            if len(eras) < 2:
                continue
            eras = tuple(eras)
            try:
                derive_periods([], eras)
            except PayCalendarError:
                # A generated sequence the calendar refuses (first paydays
                # crossing at a seam two conventions share, or an era the
                # seam leaves no payday) is a sequence no door writes; the
                # reference has nothing to say about it.
                continue

            # The reference, per era: the grid displaced under the era's own
            # convention, while the planned payday AFTER each is at or
            # before the next era's first payday (R-PC75).  The latest era
            # runs to the end of the sweep's window.
            planned = []
            for index, era in enumerate(eras):
                bound = (
                    first_payday_of(eras[index + 1])
                    if index + 1 < len(eras) else date(2028, 12, 31)
                )
                paydays, steps = [], 0
                while True:
                    payday = projected_payday(era.effective_from, era.rhythm, steps)
                    following = projected_payday(
                        era.effective_from, era.rhythm, steps + 1,
                    )
                    if following > bound:
                        break
                    paydays.append(payday)
                    steps += 1
                planned.append(paydays)
            assert planned[0], "a validated non-latest era pays at least one payday"
            # The record: a prefix of the earliest era's planned paydays --
            # what the regenerate that minted the next era left and a
            # truncate exposes -- so the horizon sits inside an era that is
            # not the last.
            recorded = planned[0][:rng.randint(1, 3)]
            calendar = _calendar(
                [(i + 1, d) for i, d in enumerate(recorded)], eras,
            )
            reference = [d for era_paydays in planned for d in era_paydays]
            reference = [d for d in reference if d > recorded[-1]]
            reference = reference[:40]
            walked = []
            for period in projected_paychecks(calendar.periods, calendar.eras):
                if len(walked) == len(reference):
                    break
                walked.append(period)

            where = tuple((e.effective_from, e.rhythm.cadence.days, e.rhythm.shift.name) for e in eras)
            expected_ends = [
                following - timedelta(days=1)
                for following in reference[1:]
            ]
            # The walk asks the arithmetic jump only at each payday; the
            # LAST DAY of every period is where the estimate overshoots the
            # seam (``project_period_after``'s clamp), so it is asked there
            # too, through the calendar's own door.
            if (
                [p.start_date for p in walked] != reference
                or [p.end_date for p in walked[:-1]] != expected_ends
                or [p.period_index for p in walked] != list(
                    range(len(recorded), len(recorded) + len(walked)),
                )
                or any(
                    calendar.span_containing(p.end_date) != p for p in walked
                )
            ):
                mismatches.append((where, recorded))
            displaced_seams += any(
                not is_business_day(e.effective_from) for e in eras[1:]
            )
            rolled_at_record += horizon_step(eras, recorded[-1])[0] > 0
            long_seam_paychecks += (
                recorded[-1] == planned[0][-1]
                and (first_payday_of(eras[1]) - recorded[-1]).days
                > eras[0].rhythm.cadence.days
            )

        assert not mismatches, mismatches[:3]
        assert displaced_seams > 0, "no seam fell on a closed day"
        assert rolled_at_record > 0, "no record ended on an era's last payday"
        assert long_seam_paychecks > 0, (
            "no record ended on a seam paycheck longer than its cadence"
        )

    def test_a_projected_period_is_never_offered_a_step_outside_its_era(self):
        """A day just past the seam is B's step 0, not A's step 13 or B's step -1."""
        calendar = _calendar(TRUNCATED_RECORD, (ERA_A, ERA_B))
        found = project_period_after(calendar.periods, calendar.eras, date(2026, 7, 2))
        assert found.start_date == date(2026, 7, 1)
        assert found.end_date == date(2026, 7, 7)


# ---------------------------------------------------------------------------
# The seam is where the door drew it (R-PC75)
# ---------------------------------------------------------------------------


class TestTheSeamIsWhereTheDoorDrewIt:
    """Ruling **R-PC75** (plan step ``C17-c-2b``), on the cases it was ruled on.

    A regenerate that mints a later era keeps the record through ``P0``,
    retires the tail, and states a first payday ``F`` in ``[P1, P2)`` -- the
    writer's floor and ceiling -- so it DELETES ``P1`` and the old era's last
    paycheck runs ``[P0, F - 1]``.  The projection must reproduce that after
    a truncate exposes the seam, because the continue door materialises it.
    Each case here is driven through the real producers, with the record cut
    below the later era.
    """

    def test_a_truncate_below_the_seam_does_not_re_invent_the_deleted_payday(self):
        """PC-509's shape: 14 days from 2030-01-03, 7 from 02-22, record cut to 01-17.

        The regenerate that minted the 7-day era wrote 01-31 and then 02-22
        -- a 22-day paycheck -- and deleted 02-14.  R-PC72's literal seam
        projected 01-31, **02-14**, 02-22 after the truncate; this projects
        what the door wrote.
        """
        eras = (era_of(date(2030, 1, 3), 14), era_of(date(2030, 2, 22), 7))
        assert list(islice(planned_paydays_after(eras, date(2030, 1, 17)), 4)) == [
            date(2030, 1, 31), date(2030, 2, 22), date(2030, 3, 1), date(2030, 3, 8),
        ]
        calendar = _calendar([(1, date(2030, 1, 3)), (2, date(2030, 1, 17))], eras)
        seam_paycheck = calendar.span_containing(date(2030, 2, 14))
        assert (seam_paycheck.start_date, seam_paycheck.end_date) == (
            date(2030, 1, 31), date(2030, 2, 21),
        )
        assert seam_paycheck.period_index == 2
        assert calendar.span_containing(date(2030, 2, 22)).period_index == 3

    def test_a_first_payday_the_day_after_a_planned_one_projects_no_one_day_paycheck(self):
        """19 days from 01-19, 10 from 02-08, record cut to 01-19: 02-08 next, not 02-07..02-07.

        The old era's next planned payday is 02-07; the 10-day era opened
        the day after it, replacing it.  The literal seam projected a
        one-day paycheck 02-07..02-07 and then 02-08.
        """
        eras = (era_of(date(2030, 1, 19), 19), era_of(date(2030, 2, 8), 10))
        assert list(islice(planned_paydays_after(eras, date(2030, 1, 19)), 3)) == [
            date(2030, 2, 8), date(2030, 2, 18), date(2030, 2, 28),
        ]
        calendar = _calendar([(1, date(2030, 1, 19))], eras)
        assert calendar.periods[-1].end_date == date(2030, 2, 7)
        assert calendar.span_containing(date(2030, 2, 7)) == calendar.periods[-1]

    def test_the_seam_holds_in_the_second_half_of_the_window(self):
        """14 days from 2030-01-03, 7 from 02-24: F is nearer P2 than P1, and still replaces P1.

        The NEAREST rule the developer rejected would match 02-24 to 02-28
        and re-invent 02-14; the door deleted 02-14 whichever half F fell in.
        """
        eras = (era_of(date(2030, 1, 3), 14), era_of(date(2030, 2, 24), 7))
        assert list(islice(planned_paydays_after(eras, date(2030, 1, 17)), 3)) == [
            date(2030, 1, 31), date(2030, 2, 24), date(2030, 3, 3),
        ]

    def test_a_day_late_in_the_seam_paycheck_is_projected_when_the_estimate_overshoots(self):
        """The candidate window is CLAMPED to the era's last step.

        An old 14-day era from Saturday 2030-03-02 under ``next`` pays
        Mondays 03-04, 03-18, 04-01, 04-15; a 7-day era under ``none`` opens
        Sunday 04-14, inside ``[04-01, 04-15)``, so the old era's last
        planned payday is 03-18 and its last paycheck runs to 04-13.  For
        Saturday 04-13 the arithmetic estimate is step 3 (three whole
        cadences from 03-02), two past the era's last step of 1, and
        neither neighbour of 3 is inside the era's window either -- so
        without the clamp no candidate covers the day and the projection
        refuses a legal sequence on a read path.
        """
        old = era_of(date(2030, 3, 2), 14, NEXT)
        new = era_of(date(2030, 4, 14), 7)
        assert first_payday_of(old) == date(2030, 3, 4)
        assert last_step_of((old, new), 0) == 1
        calendar = _calendar([(1, date(2030, 3, 4))], (old, new))

        found = calendar.span_containing(date(2030, 4, 13))

        assert (found.start_date, found.end_date) == (
            date(2030, 3, 18), date(2030, 4, 13),
        )
        assert found.period_index == 1
        assert calendar.span_containing(date(2030, 4, 14)).start_date == date(2030, 4, 14)

    def test_every_seam_paycheck_whose_estimate_overshoots_is_covered_to_its_last_day(self):
        """The clamp, enumerated over every shape that can overshoot.

        The overshoot needs two consecutive nominal paydays of the old era
        on closed days -- a cadence that is a multiple of seven, anchored on
        a Saturday, under ``next`` -- and a ``none`` era opening on one of
        those closed days past the second nominal one.  The randomised sweep
        above draws that shape too rarely to grade (the adversarial review
        of this step measured it blind to a deleted clamp), so it is
        enumerated: every Saturday anchor of early 2030, every such cadence,
        every legal opening day of the new era inside the old era's window,
        and the calendar must answer every day of the seam paycheck --
        its LAST day is where the estimate names a step two past the era's
        last.  The counter asserts the overshoot was actually reached.
        """
        overshoots, cases = 0, 0
        for anchor_offset in range(0, 28, 7):
            anchor = date(2030, 3, 2) + timedelta(days=anchor_offset)
            assert anchor.weekday() == 5, "the old era must be anchored on a Saturday"
            for cadence in (7, 14, 21, 28, 35):
                old = era_of(anchor, cadence, NEXT)
                # The record is the old era's FIRST payday alone, so the seam
                # paycheck is PROJECTED rather than derived from the record
                # (a saved last period is closed by ``payday_after`` and never
                # reaches the arithmetic jump).  The new era opens anywhere
                # from the old era's third payday to the day before its
                # fourth -- the window a regenerate keeping the second
                # payday would be bounded to -- and under ``none`` every
                # closed day in it is a legal opening day.
                record = [projected_payday(anchor, old.rhythm, 0)]
                floor_day = projected_payday(anchor, old.rhythm, 2)
                ceiling = projected_payday(anchor, old.rhythm, 3)
                opening = floor_day
                while opening < ceiling:
                    new = era_of(opening, 7)
                    eras = (old, new)
                    last = last_step_of(eras, 0)
                    calendar = _calendar(
                        [(i + 1, d) for i, d in enumerate(record)], eras,
                    )
                    seam_start = projected_payday(anchor, old.rhythm, last)
                    assert calendar.periods[-1].end_date < seam_start, (
                        "the seam paycheck must be projected, not saved"
                    )
                    day = seam_start
                    while day < opening:
                        found = calendar.span_containing(day)
                        assert (found.start_date, found.end_date) == (
                            seam_start, opening - timedelta(days=1),
                        ), (anchor, cadence, opening, day)
                        overshoots += (
                            (day - anchor).days // cadence > last + 1
                        )
                        day += timedelta(days=1)
                    cases += 1
                    opening += timedelta(days=1)
        assert cases > 100
        assert overshoots > 0, "no enumerated day overshot the era's last step by two"
