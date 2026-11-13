"""The boundary arithmetic plan step C14-c corrected, under a displacing convention: the projected
payday's one producer, the covering probe that tolerates a moved boundary, the one-neighbour
theorem swept, and the record that closes the day before the plan's first payday.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.enums import BusinessDayShiftEnum
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCalendarError,
    PeriodWindow,
    derive_periods,
    payday_after,
    projected_payday,
)
from app.services.pay_calendar import _projection
from app.services.pay_calendar._projection import covering_projection
from app.utils.business_days import shift_to_business_day, shortest_collision_free_cadence
from tests._test_helpers import era_of, eras_of, rhythm_of
from tests.oracles.pay_calendar_derivation import IRREGULAR_SHAPES, next_grid_payday_after


# ---------------------------------------------------------------------------
# The boundary arithmetic (plan step C14-c)
# ---------------------------------------------------------------------------

#: The production owner's real horizon and rhythm, measured on
#: ``shekel-prod-db`` and ``shekel-dev-db`` 2026-09-05: 63 paydays, every one a
#: Thursday, the last 2028-08-10, cadence 14.  The displacement cases below run
#: off THIS anchor rather than a convenient one.
_HORIZON = date(2028, 8, 10)


#: The first payday past ``_HORIZON`` that either convention displaces, and it
#: is the SAME nominal day for both: 2028-08-10 + 60 * 14 = 2030-11-28, which
#: is the fourth Thursday of November 2030 and so Thanksgiving.  ``prior`` pays
#: it 2030-11-27 (Wednesday), ``next`` pays it 2030-11-29 (Friday).
_THANKSGIVING_STEPS = 60


_THANKSGIVING_NOMINAL = date(2030, 11, 28)


def _eras(
    anchor: date, cadence_days: int, shift=BusinessDayShiftEnum.NONE,
) -> "tuple":
    """Return ONE era phased on *anchor*, as the calendar's era sequence.

    Args:
        anchor: The era's first nominal payday -- the grid's phase.
        cadence_days: Days between paydays.
        shift: The convention.

    Returns:
        A one-element tuple, as ``derive_periods`` and
        ``project_period_after`` take it.
    """
    return (era_of(anchor, cadence_days, shift),)


def _saved(
    anchor: date, cadence_days: int, shift=BusinessDayShiftEnum.NONE,
) -> "tuple[DerivedPeriod, ...]":
    """Return a one-period saved calendar opening on *anchor*.

    Derived under the SAME era the projection cases then pass, so the saved
    period's end and the first projected payday are one seam.

    Args:
        anchor: The last recorded payday, which is also the era's phase.
        cadence_days: Days between paydays.
        shift: The convention.

    Returns:
        The derived periods, as ``project_period_after`` takes them.
    """
    return derive_periods([(1, anchor)], _eras(anchor, cadence_days, shift))


#: A LEGAL shape whose last end really moves, which the shared catalogue has
#: none of.
#:
#: **Why it has to exist, and an adversarial review of ``C14-e-3`` is why.**
#: :data:`~tests.oracles.pay_calendar_derivation.IRREGULAR_SHAPES` was written
#: for the nominal world: measured 2026-09-06, not one of its ten non-empty
#: shapes has a next projected payday on a closed day, EXCEPT
#: ``one_day_periods`` -- whose cadence of 1 is below
#: :func:`~app.utils.business_days.shortest_collision_free_cadence` and so may
#: not carry a displacing convention at all (**R-PC59**).  So a sweep that
#: paired the catalogue with ``prior`` graded exactly one shape, and that shape
#: was an illegal pairing deriving a REVERSED period.  This one is ordinary:
#: two fortnightly paydays whose next projection is 2030-11-28, Thanksgiving,
#: which ``prior`` pays 2030-11-27 and ``next`` pays 2030-11-29.
_THANKSGIVING_SHAPE = SimpleNamespace(
    label="thanksgiving_projection",
    paydays=((1, date(2030, 10, 31)), (2, date(2030, 11, 14))),
    cadence_days=14,
)


class TestTheProjectedPaydayHasOneProducer:
    """``projected_payday`` is the rhythm's forward step, written once.

    Plan step ``C14-c``.  The expression was at three sites -- the last saved
    period's end in :func:`derive_periods`, and the opening and closing paydays
    in ``project_period_after`` -- which is rule 14's tell: one value with
    three homes that agree only because nothing can move a payday yet.
    """

    def test_it_steps_whole_cadences_from_the_anchor(self):
        """Hand-computed: 2028-08-10 at a 14-day cadence."""
        assert projected_payday(_HORIZON, rhythm_of(14), 0) == date(2028, 8, 10)
        assert projected_payday(_HORIZON, rhythm_of(14), 1) == date(2028, 8, 24)
        assert projected_payday(_HORIZON, rhythm_of(14), 2) == date(2028, 9, 7)

    def test_it_reads_the_rhythm_backwards_too(self):
        """A negative step is the rhythm below the anchor, and it is REACHED.

        Not a courtesy: ``span_containing`` asks the projection about a day
        inside an unsaved INTERIOR candidate -- its materialisation filter
        skips that candidate -- and ``cadence_steps_to`` answers such a day
        with a negative count, which arrives here.  *An earlier form of this
        docstring justified the direction by ``_rhythm``, which open-codes the
        same arithmetic and does NOT call this; an adversarial review of
        ``C14-c`` struck that, and ``projected_payday`` now names the
        duplication rather than claiming a monopoly it does not have.*
        """
        assert projected_payday(_HORIZON, rhythm_of(14), -1) == date(2028, 7, 27)

    def test_it_does_not_COMPOUND(self):
        """Every step is measured from ONE anchor, never from the last answer.

        The hazard :func:`shift_to_business_day`'s own docstring hands to the
        caller: feeding a displaced answer back in as the next anchor moves
        every later payday permanently.  Asserted as the property that makes it
        structural -- ``n`` steps of one cadence equals one step of ``n``
        cadences -- so plan step ``C14-e`` can displace the RESULT here without
        the progression drifting.
        """
        assert projected_payday(_HORIZON, rhythm_of(14), 60) == _THANKSGIVING_NOMINAL
        assert projected_payday(_HORIZON, rhythm_of(14), 60) == projected_payday(
            projected_payday(_HORIZON, rhythm_of(14), 59), rhythm_of(14), 1,
        )

    @pytest.mark.parametrize(
        "shift", [BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT],
        ids=lambda s: s.name.lower(),
    )
    def test_the_last_derived_end_FOLLOWS_the_producer(self, shift):
        """The ONE end rule, over every shape in the catalogue, DISPLACED.

        *The first form of this test compared ``derive_periods``' last end
        against ``projected_payday`` on the nominal path -- an equality whose
        two sides share ONE producer, which an adversarial review of ``C14-c``
        showed grades nothing: reverting the last end to the deleted
        ``start + cadence - 1`` left it PASSING, because on the nominal grid
        the two agree.*  Displacing the producer separates them, so this now
        asserts what it always claimed to: the last end follows the payday the
        producer answers, whatever that payday is.

        The catalogue's own hand-computed ``expected`` values are the
        independent oracle for the nominal case
        (``test_the_shape_derives_its_hand_computed_values``); this is the
        oracle for the moved one.

        **The reference is a brute-force MATCH, not the producer** (plan step
        ``C17-b-2``).  The last end is the day before the era grid's next
        payday after the one the last record STANDS FOR -- its nearest grid
        payday, a tie to the later -- and the reference below lists the grid
        explicitly, picks that payday by inspection and displaces the next,
        so it shares no body with :func:`~app.services.pay_calendar._eras.matched_step`.
        Until ``C17-b-2`` the expected value was the record displaced one
        cadence on; the two agree wherever the last record is on the grid,
        which is every catalogue shape but ``thirteen_day_period``.
        """
        shapes = (*IRREGULAR_SHAPES, _THANKSGIVING_SHAPE)
        nominal = {
            irregular.label: derive_periods(
                irregular.paydays,
                eras_of(irregular.paydays, irregular.cadence_days),
            )
            for irregular in shapes
        }
        moved = 0
        floor = shortest_collision_free_cadence()

        for irregular in shapes:
            # A shape below the collision floor is swept under ``none``: the
            # other pairing is one NO DOOR ADMITS (**R-PC59** --
            # ``reject_shift_on_short_cadence``), because two nominal paydays
            # would displace onto one.  An adversarial review of ``C14-e-3``
            # found this sweep pairing the catalogue's ONE-DAY shape with
            # ``prior`` and passing green on a period derived
            # ``start 2030-11-15, end 2030-11-14`` -- reversed, because a
            # sub-floor displacement can pull the next payday below its own
            # start.  The ordering arm below is what stops that being green;
            # this line is what keeps the sweep inside the world the app holds.
            legal = (
                shift if irregular.cadence_days >= floor
                else BusinessDayShiftEnum.NONE
            )
            derived = derive_periods(
                irregular.paydays,
                eras_of(irregular.paydays, irregular.cadence_days, legal),
            )
            if not derived:
                continue
            last = derived[-1]
            assert last.end_date == next_grid_payday_after(
                derived[0].start_date, irregular.cadence_days, legal,
                last.start_date,
            ) - timedelta(days=1), irregular.label
            # No period closes before it opens.  The next payday is found
            # STRICTLY after the record it follows, so a reversed period is
            # unrepresentable here whatever the pairing -- ledger row
            # **PC-505**, closed at ``C17-b-2``; asserted rather than argued.
            assert all(
                period.end_date >= period.start_date for period in derived
            ), irregular.label
            # Every OTHER end is dictated by a recorded payday, so displacing
            # the producer must not touch one.
            assert derived[:-1] == nominal[irregular.label][:-1], irregular.label
            moved += last.end_date != nominal[irregular.label][-1].end_date

        assert moved, (
            f"no shape's last end moved under {shift.name}, so this graded the "
            f"nominal path a second time"
        )


class TestTheCoveringProbeToleratesAMovedBoundary:
    """The probe, driven through the shipped producer under a real convention.

    Ruling **R-PC57**: ``C14-c`` corrects the boundary arithmetic and the
    containment probe tolerates a moved boundary.  With the convention at
    ``none`` the arithmetic estimate is right on every call, so each
    displacement case STATES a displacing one on the rhythm it passes and then
    calls the shipped
    :func:`~app.services.pay_calendar._projection.project_period_after` -- which
    grades the candidate WINDOW and the end rule, not only the selector.
    """

    def test_the_estimate_wins_when_no_payday_moved(self):
        """The ordinary call, and the one every call is today.

        No substitution: this is the nominal path.  2028-08-10 + 2 * 14 =
        2028-09-07, a Thursday, so the estimate for 2028-09-10 is step 2 and
        step 2 is the answer, running to the day before 2028-09-21.
        """
        found = _projection.project_period_after(
            _saved(_HORIZON, 14), _eras(_HORIZON, 14), date(2028, 9, 10),
        )

        assert found.start_date == date(2028, 9, 7)
        assert found.end_date == date(2028, 9, 20)
        assert found.period_index == 2
        assert found.period_id is None
        assert found.end_is_projected is True

    def test_a_payday_paid_EARLY_is_found_at_the_LATER_neighbour(self):
        """``prior`` pulls Thanksgiving 2030 back, and the division misses low.

        Hand-computed.  2028-08-10 + 60 * 14 = 2030-11-28, the fourth Thursday
        of November 2030 and so Thanksgiving; ``prior`` pays it 2030-11-27.
        Asked to place 2030-11-27, the division answers
        ``(2030-11-27 - 2028-08-10) / 14 = 839 / 14 = 59`` -- one period SHORT,
        because the paycheck arrived a day before the nominal grid allows.  The
        LATER neighbour is the one that covers it, and its span runs to the day
        before the next payday, 2030-12-12.
        """
        day = date(2030, 11, 27)

        found = _projection.project_period_after(
            _saved(_HORIZON, 14, BusinessDayShiftEnum.PRIOR),
            _eras(_HORIZON, 14, BusinessDayShiftEnum.PRIOR), day,
        )

        assert (day - _HORIZON).days // 14 == _THANKSGIVING_STEPS - 1
        assert found.period_index == _THANKSGIVING_STEPS
        assert found.start_date == day
        assert found.end_date == date(2030, 12, 11)

    def test_a_payday_paid_LATE_is_found_at_the_EARLIER_neighbour(self):
        """``next`` pushes the same payday forward, and the division misses high.

        The mirror of the case above, off the same anchor and the same nominal
        day.  ``next`` pays Thanksgiving 2030-11-28 on 2030-11-29, so on
        2030-11-28 the owner has NOT been paid yet: the division answers step
        60, whose period has not opened, and the covering paycheck is still
        step 59's -- which now runs 2030-11-14 to 2030-11-28 rather than
        stopping at 2030-11-27.
        """
        day = _THANKSGIVING_NOMINAL

        found = _projection.project_period_after(
            _saved(_HORIZON, 14, BusinessDayShiftEnum.NEXT),
            _eras(_HORIZON, 14, BusinessDayShiftEnum.NEXT), day,
        )

        assert (day - _HORIZON).days // 14 == _THANKSGIVING_STEPS
        assert found.period_index == _THANKSGIVING_STEPS - 1
        assert found.start_date == date(2030, 11, 14)
        assert found.end_date == day

    def test_the_displaced_projection_TILES_the_calendar(self):
        """No day falls in two paychecks and none falls in none.

        The property the deleted ``start + cadence - 1`` end breaks and this
        step exists to restore, asserted over the whole displaced stretch
        rather than argued.  Built the way ``_views.projected_paychecks``
        builds it -- step to ``end_date + 1`` and ask again -- so the walk this
        asserts about is the one the application takes, and handed to
        :class:`~app.services.pay_calendar.PeriodWindow`, which refuses a hole
        and an overlap.
        """
        for shift in (BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT):
            displacing = _eras(_HORIZON, 14, shift)
            saved = _saved(_HORIZON, 14, shift)
            walked, opens_at = [], saved[-1].end_date + timedelta(days=1)
            while len(walked) < 80:
                period = _projection.project_period_after(saved, displacing, opens_at)
                walked.append(period)
                opens_at = period.end_date + timedelta(days=1)

            assert PeriodWindow(periods=tuple(walked)).periods == tuple(walked)
            assert [p.period_index for p in walked] == list(range(1, 81))
            assert any(
                p.end_date - p.start_date != timedelta(days=13)
                for p in walked
            ), f"{shift.name} displaced nothing in 80 paychecks"

    @pytest.mark.parametrize(
        ("shift", "real_end", "damage"),
        [
            # ``prior`` pulls the 2030-11-28 payday back to 2030-11-27, so the
            # deleted rule's end for the paycheck opening 2030-11-14 IS the day
            # the next one arrives: two paychecks claim 2030-11-27.
            (BusinessDayShiftEnum.PRIOR, date(2030, 11, 26), "overlap"),
            # ``next`` pushes the same payday to 2030-11-29, so the deleted
            # rule stops that paycheck a day EARLY and 2030-11-28 falls in no
            # period -- the failure R-PC54's own worked example names.
            (BusinessDayShiftEnum.NEXT, date(2030, 11, 28), "hole"),
        ],
        ids=["prior_double_covers", "next_leaves_a_hole"],
    )
    def test_the_OLD_end_rule_would_NOT_have_tiled(
        self, shift, real_end, damage,
    ):
        """The control, forced to fire in BOTH directions.

        Without this the tiling test grades nothing -- it would pass just as
        well against the rule the step removed, because wherever no payday
        moves the two expressions agree on every row (the shape that made
        ``C2-a``'s first ``P14`` test vacuous).  Parametrised because the
        deleted rule fails ASYMMETRICALLY, and a control that saw one direction
        would report the other as covered: on the production owner's own
        anchor, ``prior`` makes the paycheck opening 2030-11-14 end on the day
        the NEXT one arrives (2030-11-27 claimed twice), while ``next`` stops
        it a day early and leaves 2030-11-28 in no period.  Both arms end at
        ``PeriodWindow``, which refuses a hole and an overlap alike.
        """
        displacing = _eras(_HORIZON, 14, shift)
        saved = _saved(_HORIZON, 14, shift)
        opening = _projection.project_period_after(
            saved, displacing, date(2030, 11, 14),
        )
        following = _projection.project_period_after(
            saved, displacing, date(2030, 11, 30),
        )
        old_rule_end = opening.start_date + timedelta(days=13)

        assert opening.start_date == date(2030, 11, 14)
        assert opening.end_date == real_end
        assert old_rule_end == date(2030, 11, 27)
        assert (old_rule_end == following.start_date) is (damage == "overlap")
        assert (
            old_rule_end < following.start_date - timedelta(days=1)
        ) is (damage == "hole")
        with pytest.raises(PayCalendarError, match="unbroken span"):
            PeriodWindow(periods=(
                DerivedPeriod(
                    period_id=None,
                    period_index=opening.period_index,
                    start_date=opening.start_date,
                    end_date=old_rule_end,
                    end_is_projected=True,
                ),
                following,
            ))

    def test_the_last_SAVED_end_follows_the_displaced_next_payday(self):
        """``derive_periods`` reads the same producer, so its last end moves too.

        The other half of the end rule, and the half a projection test cannot
        reach: the last RECORDED period has no successor row, so its end is the
        projected payday's eve.  Under ``prior`` the payday after 2030-11-14
        arrives 2030-11-27, so a calendar ending on 2030-11-14 runs to
        2030-11-26 -- not to 2030-11-27, which the deleted expression answers.
        """
        derived = derive_periods(
            [(1, date(2030, 11, 14))],
            _eras(date(2030, 11, 14), 14, BusinessDayShiftEnum.PRIOR),
        )

        assert derived[-1].end_is_projected is True
        assert derived[-1].end_date == date(2030, 11, 26)
        assert derived[-1].end_date != date(2030, 11, 14) + timedelta(days=13)

    def test_it_refuses_when_no_candidate_covers_the_day(self):
        """A day outside every candidate is refused, never answered wrongly.

        The backstop ruling **R-PC59** reports rather than closes: a write-time
        refusal cannot see a stored row a later holiday-set change made
        illegal.  Answering with a period that does not contain the day it was
        asked about is the alternative, and it would place money silently.
        """
        candidates = _saved(_HORIZON, 14)

        with pytest.raises(PayCalendarError, match="no projected pay period"):
            covering_projection(candidates, date(2029, 1, 1))


class TestOneNeighbourEitherSideIsEnough:
    """The theorem the three-candidate window rests on, swept exhaustively.

    A displacement is bounded by the longest run of consecutive closed days,
    and :func:`~app.utils.business_days.shortest_collision_free_cadence` is
    that run PLUS ONE -- the floor a displacing convention is held to
    (**R-PC59**).  So no payday moves by a whole cadence, which puts the true
    covering index within one of the arithmetic estimate.  Swept rather than
    argued, because the window is the whole reason the probe stays O(1).
    """

    #: Anchors spanning a leap year, a Monday, a Saturday, and both sides of
    #: the Juneteenth addition ``business_days.JUNETEENTH_FIRST_YEAR`` records.
    #: A NON-business anchor is deliberate: a recorded payday need not be one
    #: (**R-PC47**), and here it is also the era's phase, so the era's first
    #: payday and the record it stands for are one displacement apart.
    _ANCHORS = (
        date(2020, 1, 2), date(2024, 2, 29), date(2028, 8, 10),
        date(2026, 5, 16),
    )

    def test_the_probe_finds_a_covering_period_at_every_cadence(self):
        """The window is wide enough, asserted against the SHIPPED function.

        Every cadence from the collision floor to a year, both displacing
        conventions, four anchors, and steps either side of the anchor -- the
        negative half included, because ``span_containing`` reaches the
        projection there.  The floor is READ from
        :func:`~app.utils.business_days.shortest_collision_free_cadence` rather
        than written down: a literal would be a second home for it, and the
        stale copy would fail SILENTLY, in the direction that stops sweeping
        the tightest cadences.

        *A first form of this swept a LOCAL re-implementation of the estimate
        and the true index, so it graded the theorem and not the code, while
        its docstring claimed "every cadence from the floor up" over eight
        sampled ones.  An adversarial review of ``C14-c`` caught both.*  It now
        calls :func:`~app.services.pay_calendar._projection.project_period_after`
        under the producer ``C14-e`` will ship, and asserts three things of the
        answer: it COVERS the day, its span is the two projected paydays either
        side, and the neighbour arms were actually exercised in both
        directions -- a sweep that never displaced anything would pass against
        a probe offering no neighbours at all.
        """
        floor = shortest_collision_free_cadence()
        cadences = (*range(floor, 46), 60, 90, 180, 364, 365)
        seen_low = seen_high = 0

        for shift in (BusinessDayShiftEnum.PRIOR, BusinessDayShiftEnum.NEXT):
            payday = (
                lambda anchor, cadence, steps: shift_to_business_day(
                    anchor + timedelta(days=steps * cadence), shift,
                )
            )
            for anchor in self._ANCHORS:
                for cadence in cadences:
                    saved = _saved(anchor, cadence, shift)
                    for steps in range(-3, 8):
                        opens = payday(anchor, cadence, steps)
                        closes = payday(anchor, cadence, steps + 1)
                        for day in (opens, opens + (closes - opens) // 2,
                                    closes - timedelta(days=1)):
                            found = _projection.project_period_after(
                                saved, _eras(anchor, cadence, shift), day,
                            )
                            where = (shift, anchor, cadence, steps, day)
                            assert found.covers(day), where
                            assert found.start_date == opens, where
                            assert found.end_date == closes - timedelta(
                                days=1,
                            ), where
                            # NOT asserted: ``abs(period_index - estimate) <=
                            # 1``.  ``project_period_after`` only ever offers
                            # those three, so that comparison is true by
                            # construction and would grade nothing.  What the
                            # window is graded by is ``covers`` above -- a
                            # window too narrow makes the answer a REFUSAL, not
                            # a far-off index.  The counters below are the
                            # separate question of whether the arms ran at all.
                            estimate = (day - anchor).days // cadence
                            seen_low += found.period_index == estimate + 1
                            seen_high += found.period_index == estimate - 1

        assert seen_low > 0, "the sweep never saw a payday paid EARLY"
        assert seen_high > 0, "the sweep never saw a payday paid LATE"


class TestTheRecordClosesWhereThePlanOpens:
    """The derived last end and the plan's first payday are ONE producer.

    Plan step **C14-e-2** wrote this class for ``nominal_payday_after``, the
    grid search the extend door continued a rhythm from, and swept it over
    every residue of ``(horizon - phase) % cadence``.  Plan step
    ``C17-c-2b`` deleted that producer with the door's computation: extend
    materialises :func:`~app.services.pay_calendar.planned_paydays_after`
    now (``pay_period_write.continue_paydays``), so the residue axis moved:
    the record is placed on the era's grid by
    :func:`~app.services.pay_calendar._eras.matched_step`, whose randomised
    sweep in ``test_pay_calendar_eras.py`` draws records at random residues
    over cadences 1 to 45 and 365, and the writer's floor is the plan's
    first element by construction.  What survives here is the seam that
    pairing rested on and the new one still does.
    """

    def test_the_record_closes_the_day_before_the_plans_first_payday(self):
        """The seam the whole pairing rests on, pinned so a split would fire.

        :func:`derive_periods` closes the last saved period the day before
        ``payday_after(eras, latest)``; ``pay_period_batch.reject_backward_payday``
        bounds a stated batch at that same day, and since plan step
        ``C17-c-2b`` the continue door RECORDS it as its first payday.  Those
        are one value only because all three read one producer -- so if a
        step ever moved one side and not the other, every other case here
        would stay green and only one door case would notice.  An adversarial
        review of ``C14-e-2`` named that hole; this is the assertion that
        fills it.
        """
        eras = _eras(date(2026, 4, 27), 14)
        latest = date(2026, 5, 11)

        derived = derive_periods([(1, date(2026, 4, 27)), (2, latest)], eras)

        assert derived[-1].end_date == payday_after(eras, latest) - timedelta(
            days=1,
        )
