"""The PRODUCER (``pay_calendar.opening_rephase``), pure: the exact inverse of ``earlier_paydays``'
re-phase over every cadence kind and convention, and ``None`` where a later era pays the
opening.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.pay_calendar import (
    earlier_paydays,
    first_payday_of,
    nominal_payday,
    opening_rephase,
    planned_paydays_after,
    projected_payday,
)
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, SemiMonthly

from ._helpers import NONE, PRIOR, NEXT


#: One era per cadence kind and convention -- the set C18-b's producer is
#: graded over, for the inverse's sake: 2030-12-12 on a fortnight steps back
#: over Thanksgiving under both displacing conventions, and the month cases
#: step back into a February that clamps the day.
ERA_CASES = {
    "every 14 days, none": Era(date(2026, 1, 2), Rhythm(FixedDays(14), NONE)),
    "every 14 days, prior, over Thanksgiving": Era(
        date(2030, 12, 12), Rhythm(FixedDays(14), PRIOR),
    ),
    "every 14 days, next, over Thanksgiving": Era(
        date(2030, 12, 12), Rhythm(FixedDays(14), NEXT),
    ),
    "every 7 days": Era(date(2026, 1, 2), Rhythm(FixedDays(7), NONE)),
    "monthly on the 31st": Era(date(2026, 3, 31), Rhythm(Monthly(31), NONE)),
    "15th and 31st, from a 31st": Era(
        date(2026, 3, 31), Rhythm(SemiMonthly((15, 31)), NONE),
    ),
    "1st and 15th": Era(date(2026, 3, 1), Rhythm(SemiMonthly((1, 15)), NONE)),
}


#: The fortnight-then-weekly owner R-PC110's question worked: every 14 days
#: from 2026-01-08 (01-08, 01-22, 02-05, 02-19), then weekly from 03-12,
#: which replaces the fortnight's 03-05 (R-PC75).
TWO_ERAS = (
    Era(date(2026, 1, 8), Rhythm(FixedDays(14), NONE)),
    Era(date(2026, 3, 12), Rhythm(FixedDays(7), NONE)),
)


class TestTheProducerIsAddEarliersInverse:
    """``pay_calendar.opening_rephase``: the phase moved up onto the new opening."""

    @pytest.mark.parametrize("case", sorted(ERA_CASES))
    @pytest.mark.parametrize("count", (1, 2, 5, 13))
    def test_it_undoes_the_rephase_add_earlier_made(self, case, count):
        """Add *count* below the era's first payday, then open on that payday again.

        ``earlier_paydays`` hands back the era re-phased DOWN onto the
        earliest added payday; asked of the re-phased era with the original
        opening, this producer must hand back the era exactly as it was --
        the phase and, for a month kind, the day the phase means.
        """
        era = ERA_CASES[case]
        opening = first_payday_of(era)
        rephased, _added = earlier_paydays((era,), opening, count)

        assert opening_rephase((rephased,), opening) == era

    @pytest.mark.parametrize("case", sorted(ERA_CASES))
    @pytest.mark.parametrize("steps", (1, 2, 7))
    def test_the_moved_era_plans_every_payday_it_planned(self, case, steps):
        """Up *steps* grid steps: the phase lands on that step's NOMINAL day.

        The same grid, so the plan past any day is unchanged; under a
        displacing convention the opening is the step's cash day, and the
        phase is the nominal day it was paid for.
        """
        era = ERA_CASES[case]
        opening = projected_payday(era.effective_from, era.rhythm, steps)

        moved = opening_rephase((era,), opening)

        assert moved == Era(
            nominal_payday(era.effective_from, era.rhythm.cadence, steps),
            era.rhythm,
        )
        far = date(2031, 6, 1)
        before = planned_paydays_after((era,), far)
        after = planned_paydays_after((moved,), far)
        assert [next(before) for _ in range(30)] == [next(after) for _ in range(30)]

    def test_a_later_eras_paycheck_moves_nothing(self):
        """03-12 is the weekly era's first payday: the fortnight would pay none."""
        assert opening_rephase(TWO_ERAS, date(2026, 3, 12)) is None
        assert opening_rephase(TWO_ERAS, date(2026, 3, 19)) is None

    def test_the_earliest_eras_last_paycheck_is_the_highest_move(self):
        """02-19 is the fortnight's last planned payday before the seam."""
        assert opening_rephase(TWO_ERAS, date(2026, 2, 19)) == Era(
            date(2026, 2, 19), TWO_ERAS[0].rhythm,
        )

    def test_a_record_paid_a_day_early_for_the_next_era_is_that_eras(self):
        """03-11 is the 03-12 paycheck paid early (R-PC47), not the fortnight's.

        The record is matched to the planned paycheck it stands for over the
        piecewise plan, so the opening is the weekly era's and the removal
        that leaves it first would take every fortnightly paycheck.
        """
        assert opening_rephase(TWO_ERAS, date(2026, 3, 11)) is None
