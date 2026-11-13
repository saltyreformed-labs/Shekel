"""
Shekel Budget App -- Pay Calendar Derivation Tests (plan steps C1 and C4-c)

``pay_calendar.derive_periods`` is the ONE answer to "which paycheck, and how
far does it run", and since plan step ``pay_calendar:C4-c`` it is the only one:
that step dropped ``budget.pay_periods.end_date`` and ``period_index``, so
nothing stores a second opinion.  This suite is what holds the derivation to
its hand-computed values.

*It was the suite half of a two-dataset proof until then.*  C1 required the
derivation to reproduce the stored columns before anything read it, wrote it,
or dropped them, so ``tests/manual/verify_pay_calendar_derivation.py`` drove
the shared oracle over a clone of production while this file drove it over
schedules the live data cannot supply.  The comparator, its payday control and
its verdict went with the columns, and so did that script; the proof they were
is in migration ``b7a41e2c9d63``'s docstring, measured on production itself.

What is left is the half that was never about the columns:

* the shapes the live data does not supply -- a one-day period, a 90-day
  cadence, a payday jump, a thirteen-day period, a single-payday schedule,
  paydays handed in out of order, a mid-schedule cadence change, and the empty
  calendar (``IRREGULAR_SHAPES``, each with hand-computed expected values);
* what the derivation REFUSES, which a clean database never exercises;
* the writer's output at seven cadences spanning the whole storable range (1,
  2, 7, 14, 30, 90, 365 -- ``ck_pay_schedule_cadence_range`` permits 1..365 and
  C4-c removed the stored-column floor that excluded 1), each pinned to a
  hand-computed end on BOTH branches;
* **re-derivation stability** -- whether yesterday's answer still holds after
  another payday is written.  That is the axis on which a derived end behaves
  differently from the stored column it replaced, and no other test here asks
  about it;
* the CADENCE control, which measures the one branch a regular schedule cannot
  show;
* **the boundary arithmetic plan step ``C14-c`` corrected**, which needs a
  payday that MOVES.  **Those cases used to SUBSTITUTE the producer and now
  state a displacing convention on the rhythm they pass** -- plan step
  ``C14-e-3`` shipped the displacement, so ``rhythm_of(cadence, shift)``
  reaches the same mechanism through the real function and the double it
  replaced is DELETED.  A substitution that simulates a shipped producer is a
  fence with a subject, and the subject went when the producer landed.  The
  candidate window, the end rule and the selector are still what is graded,
  rather than the arithmetic estimate alone.  Their control is
  ``test_the_OLD_end_rule_would_NOT_have_tiled``: the deleted
  ``start + cadence - 1`` agrees with the surviving rule everywhere no payday
  moves, so a suite that never displaced one would pass against the defect --
  which is what made ``C2-a``'s first ``P14`` test vacuous.

**No date here is read from a clock.**  Every date is a literal or is derived
from one by explicit ``timedelta`` arithmetic; nothing calls ``date.today()``
or ``display_today()``, so these pass identically under
``TZ=Pacific/Kiritimati`` and under the weekly ``SHEKEL_FAKE_TODAY`` sweep
(``docs/test-suite-clocks.md``).  The derivation has no clock, and a test that
gave it one would be testing the fixture.

**The boundary-arithmetic cases reach a package-PRIVATE module on purpose.**
``covering_projection`` and ``project_period_after`` live in
``pay_calendar._projection`` and have no caller outside the PACKAGE, so
exporting them would widen its curated surface for a test's convenience.
The predicate that surface is drawn on is the package boundary, which is also
what the W9910 gate (``shekel-private-module-import``) enforces; that gate
runs over ``app/``, ``scripts/`` and ``tools/pylint/shekel_checkers/``, not
over ``tests/`` (``tests/manual/`` is held to E and F alone), and three parts
of the ``tests/test_services/test_spending_report_service/`` package reach a
sibling package's ``_window`` the same way.  ``projected_payday`` was
package-private too until plan step ``C14-d`` gave it an application caller;
it is imported publicly, and ``pay_period_batch.requested_paydays`` and
``registration_service._reject_impossible_first_payday`` call it.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.enums import BusinessDayShiftEnum
from app.services import pay_period_write, pay_rhythm
from app.services.pay_calendar import (
    MAX_CADENCE_DAYS,
    DerivedPeriod,
    PayCalendar,
    PayCalendarError,
    PeriodWindow,
    cadence_steps_to,
    derive_periods,
    nominal_payday,
    payday_after,
    projected_payday,
    schedule_for,
)
from app.services.pay_calendar import _projection
from app.services.pay_calendar._projection import covering_projection
from app.utils.business_days import (
    shift_to_business_day,
    shortest_collision_free_cadence,
)
from tests._test_helpers import (
    all_periods,
    era_of,
    eras_of,
    rhythm_of,
)
from tests.oracles.pay_calendar_derivation import (
    IRREGULAR_SHAPES,
    cadence_control,
    next_grid_payday_after,
    shape,
)

#: The phase the refusal cases give their one era: every payday they state
#: opens on it, so the era is on the record's grid and only the value under
#: test is wrong.
_OPENING = date(2026, 1, 2)

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
# TestDerivationRefusals
# ---------------------------------------------------------------------------


class TestDerivationRefusals:
    """What the derivation will not accept, and why each refusal exists."""

    @pytest.mark.parametrize("cadence_days", [0, -1, -14, 366, 10_000])
    def test_a_cadence_outside_the_stored_range_is_refused(self, cadence_days):
        """1..365 is what ``ck_pay_schedule_cadence_range`` permits.

        Below the floor the last period would end before its own payday; above
        the ceiling the value cannot have come from a schedule row, so
        projecting a horizon off it would mean trusting a number no write door
        could have produced.
        """
        with pytest.raises(PayCalendarError, match="at least 1 day and at most 365"):
            derive_periods([(1, date(2026, 1, 2))], (era_of(_OPENING, cadence_days),))

    @pytest.mark.parametrize(
        "cadence_days", [True, False, 14.0, 14.9, "14"],
    )
    def test_a_cadence_that_is_not_a_plain_int_is_refused(self, cadence_days):
        """``bool`` and ``float`` are the dangerous two, and both are refused.

        ``True`` is an ``int`` subclass and would pass as a one-day cadence.  A
        ``float`` is silently TRUNCATED -- ``date.__add__`` reads only
        ``timedelta.days`` -- so 14.9 would produce the same calendar as 14 and
        the error would be invisible.

        **``None`` left this parametrization at plan step C2-b1** and is not an
        omission: it stopped being a type error and became a MEANING -- "this
        owner has no schedule at all" -- whose legality depends on whether they
        have paydays.  ``TestTheCadenceIsRequiredOnlyBesideAPayday`` below owns
        both directions of that rule.
        """
        with pytest.raises(PayCalendarError, match="must be a plain int"):
            derive_periods([(1, date(2026, 1, 2))], (era_of(_OPENING, cadence_days),))

    def test_the_cadence_is_validated_before_the_paydays_are_read(self):
        """An unusable cadence is refused even for an empty payday set.

        The caller has to resolve a cadence to get here at all, so a bad one is
        a bad caller whether or not this owner has paydays yet.  Refusing it
        only when the data happens to reach the projection branch would hide it
        until the day the user records their first payday.
        """
        with pytest.raises(PayCalendarError, match="at least 1 day and at most 365"):
            derive_periods([], (era_of(_OPENING, 0),))

    def test_a_repeated_payday_is_refused(self):
        """Two periods cannot share an opening day.

        The first of them would derive ``end_date = start_date - 1`` and cover
        no day at all.  ``uq_pay_periods_user_start`` makes this unreachable
        from the table; a caller assembling a payday set in memory -- which is
        exactly what C3's writer will do, from a form batch plus existing rows
        -- can still do it.
        """
        with pytest.raises(PayCalendarError, match="appears twice"):
            derive_periods(
                [
                    (1, date(2026, 1, 2)),
                    (2, date(2026, 1, 16)),
                    (3, date(2026, 1, 2)),
                ],
                (era_of(_OPENING, 14),),
            )

    def test_a_datetime_is_refused(self):
        """``datetime`` is a ``date`` subclass and must not pass as one.

        Accepted, it would give every derived end a time component -- unequal
        to the DATE column it reproduces, and placing a day's money by the
        process timezone rather than the app's civil day.
        """
        with pytest.raises(PayCalendarError, match="must be a datetime.date"):
            derive_periods([(1, datetime(2026, 1, 2, 9, 30))], (era_of(_OPENING, 14),))

    @pytest.mark.parametrize("payday", ["2026-01-02", 20260102, None])
    def test_a_value_that_is_not_a_date_is_refused(self, payday):
        """An ISO string, an integer and ``None`` are all refused by type."""
        with pytest.raises(PayCalendarError, match="must be a datetime.date"):
            derive_periods([(1, payday)], (era_of(_OPENING, 14),))

    @pytest.mark.parametrize("period_id", ["41", 41.0, True, date(2026, 1, 2)])
    def test_a_period_id_that_is_not_an_int_or_none_is_refused(
        self, period_id,
    ):
        """The id is checked even though nothing derives from it.

        It rides onto ``DerivedPeriod.period_id``, whose whole purpose is to be
        what a foreign key points at, so a wrong type would surface as a failed
        lookup far from the caller that supplied it.  ``True`` is refused for
        the same reason a ``bool`` cadence is: it is an ``int`` subclass and
        would pass as row 1.
        """
        with pytest.raises(PayCalendarError, match="must be an int or None"):
            derive_periods([(period_id, date(2026, 1, 2))], (era_of(_OPENING, 14),))

    def test_a_period_id_of_none_is_accepted(self):
        """``None`` is how a period that is not materialised says so.

        A projection past the owner's horizon has no row for a foreign key to
        point at, and plan step C2 has to be able to build one.
        """
        derived = derive_periods([(None, date(2026, 1, 2))], (era_of(_OPENING, 14),))
        assert derived[0].period_id is None


# ---------------------------------------------------------------------------
# TestTheCadenceIsRequired
# ---------------------------------------------------------------------------


class TestTheCadenceIsRequired:
    """Plan step C4-d (ruling **R-PC45**): a calendar HAS a cadence, always.

    **This class replaces ``TestTheCadenceIsRequiredOnlyBesideAPayday``, and
    the rename is the whole change.**  ``cadence_days`` was ``int | None``:
    ``None`` legal beside an empty payday set, refused beside a non-empty one,
    and the pairing policed here at runtime because the type would not express
    it.  What that absence stood for was an owner with no
    ``budget.pay_schedule`` row, and ``pay_calendar._loader.calendar_for``
    refuses that owner now rather than building an empty calendar carrying no
    cadence -- so nothing constructs the pair and there is nothing conditional
    left to grade.

    What is graded instead is the unconditional rule and the two things it
    could quietly get wrong: that the refusal no longer DEPENDS on the payday
    set (a rule stated for one input shape and not the other is the shape this
    class used to have), and that an EMPTY calendar is still buildable and
    still answers -- it is an owner with a schedule row and zero paydays, which
    ``pay_period_admin.reset_pay_periods`` passes through, and it is the case a
    "cadence required" rule could break by refusing everything.
    """

    def test_no_paydays_and_a_cadence_derive_an_empty_calendar(self):
        """The empty calendar stays buildable, and now carries a real cadence.

        ``recurrence._reading.resolved_recurrence`` answers ``None`` for an
        owner with no periods so the Recurring surface still renders; raising
        here would take that page to a 500 for the one owner it is written for.
        """
        assert derive_periods([], (era_of(_OPENING, 14),)) == ()

    def test_an_absent_cadence_is_refused_beside_a_payday(self):
        """P8's state: a payday exists and its period's end cannot be derived.

        Every alternative invents a horizon the owner never chose, so the
        refusal names the invariant rather than clamping.  The message is
        ``validate_cadence``'s since plan step C4-d -- ``None`` is one more
        wrong TYPE beside ``bool`` and ``float`` rather than a state with a
        refusal of its own.

        **Since plan step ``C17-d-1`` this ``None`` is a fixed-days cadence's
        DAY COUNT** -- ``era_of(_OPENING, None)`` builds
        ``Rhythm(cadence=FixedDays(None))`` -- and so are the two cases below
        it.  A bare ``None`` where the cadence VALUE should be is the other
        shape of absence, refused by a branch of its own, and
        :class:`TestTheCadenceIsAValueOfAKnownKind` grades that one.
        """
        with pytest.raises(PayCalendarError, match="must be a plain int") as excinfo:
            derive_periods([(1, date(2026, 1, 2))], (era_of(_OPENING, None),))

        # The message NAMES what it refused, which is this project's
        # error-message rule and which the deleted refusal used to carry (it
        # counted the paydays).  Eight ``raises`` sites inherited the new
        # message and an adversarial review found none of them asserting it
        # says anything at all about the offending value.
        assert "NoneType" in str(excinfo.value)

    def test_an_absent_cadence_is_refused_with_NO_paydays_TOO(self):
        """The refusal stopped depending on the payday set, which IS the step.

        ``derive_periods([], (era_of(_OPENING, None),))`` was LEGAL before plan step C4-d, and it is
        the exact pair that made ``cadence_days`` optional at five tiers.
        Asserted beside the case above rather than instead of it: what changed
        is that one rule now covers both payday shapes, and a test of only the
        non-empty shape would pass identically against the old conditional.
        """
        with pytest.raises(PayCalendarError, match="must be a plain int"):
            derive_periods([], (era_of(_OPENING, None),))

    def test_the_cadence_is_graded_BEFORE_the_payday_set(self):
        """Order of refusals, INVERTED by plan step C4-d and pinned as such.

        A caller handing in both faults -- a duplicate payday and no cadence --
        used to hear "appears twice", because the absent cadence was checked
        after the payday set was sorted and de-duplicated.  The cadence is
        validated eagerly now, before the paydays are looked at, which is what
        ``derive_periods`` already did for every value except ``None`` and what
        its own ``Args:`` has always said: a bad cadence is a bad caller
        whether or not this particular owner has paydays yet.  Pinned so the
        two refusals cannot start racing.
        """
        with pytest.raises(PayCalendarError, match="must be a plain int"):
            derive_periods(
                [(1, date(2026, 1, 2)), (2, date(2026, 1, 2))],
                (era_of(_OPENING, None),),
            )


class TestTheCadenceIsAValueOfAKnownKind:
    """Plan step ``C17-d-1``: the cadence is a value whose KIND is its type.

    ``Rhythm.cadence`` was a bare ``int`` until this leaf; it is a value of one
    of the kinds ``pay_rhythm`` declares now, and the grid dispatches its
    arithmetic on the value's type through ONE table (``_grid._ARITHMETIC``)
    whose keys are ``_grid.KINDS``.  Two refusals guard the seam and each is
    graded here, because an adversarial review of the leaf found both
    written and neither exercised: ``validate_cadence`` refuses a value of no
    known kind where a calendar is built (the package's error), and the grid
    itself refuses one with Python's own ``TypeError`` for a caller that
    reached it without building a calendar.
    """

    @pytest.mark.parametrize(
        "cadence", [None, 14, "14", 14.0, (14,)],
        ids=["None", "bare-int", "str", "float", "tuple"],
    )
    def test_a_cadence_of_no_known_kind_is_refused_where_a_calendar_is_built(self, cadence):
        """A bare ``None`` -- and the OLD ``int`` spelling -- are of no kind.

        The old spelling is the case that matters: a caller still handing the
        grid a day count where a value is expected must be refused loudly,
        not answered by whatever ``int`` arithmetic happens to do.
        """
        era = pay_rhythm.Era(
            effective_from=_OPENING,
            rhythm=pay_rhythm.Rhythm(cadence=cadence, shift=BusinessDayShiftEnum.NONE),
        )
        with pytest.raises(
            PayCalendarError, match="a cadence must be a value of one of the kinds",
        ) as excinfo:
            derive_periods([(1, date(2026, 1, 2))], (era,))
        # The message NAMES what it refused, as the plain-int refusal does.
        assert type(cadence).__name__ in str(excinfo.value)

    def test_the_kind_set_is_the_grids_own_table(self):
        """``validate_cadence`` gates on the grid's keys, so the set has ONE spelling.

        Every kind the grid can project is accepted here and nothing else is;
        at this leaf that set is exactly ``{FixedDays}``.  A kind added to
        the table at ``C17-d-2`` is accepted by this gate with nothing written
        in it, which is the point of reading the table rather than restating
        it.
        """
        from app.services.pay_calendar import _grid  # pylint: disable=import-outside-toplevel

        assert _grid.KINDS == frozenset({
            pay_rhythm.FixedDays, pay_rhythm.Monthly, pay_rhythm.SemiMonthly,
        })
        assert derive_periods([], (era_of(_OPENING, 14),)) == ()

    @pytest.mark.parametrize("cadence", [None, 14, "14"], ids=["None", "bare-int", "str"])
    def test_the_grid_itself_refuses_a_cadence_of_no_known_kind(self, cadence):
        """Reached without a calendar, the grid refuses with ``TypeError``.

        The grid cannot raise the package's error (it sits below the module
        that declares it), so the wrong-argument-type contract Python already
        has is the one it keeps; every path that builds a calendar meets
        ``validate_cadence`` first and never reaches this.
        """
        with pytest.raises(TypeError, match="no pay grid arithmetic"):
            nominal_payday(_OPENING, cadence, 1)
        with pytest.raises(TypeError, match="no pay grid arithmetic"):
            cadence_steps_to(_OPENING, cadence, _OPENING + timedelta(days=20))

    def test_a_duplicate_payday_is_still_refused_beside_a_GOOD_cadence(self):
        """The payday-set grading survived the reorder, which is what could break.

        The case above proves the cadence is graded first; on its own that is
        also what a ``derive_periods`` which had stopped checking paydays
        entirely would report.  This is the other side: a good cadence, and the
        duplicate is still named.
        """
        with pytest.raises(PayCalendarError, match="appears twice"):
            derive_periods(
                [(1, date(2026, 1, 2)), (2, date(2026, 1, 2))],
                (era_of(_OPENING, 14),),
            )

    def test_a_present_cadence_is_still_graded_beside_an_empty_set(self):
        """A WRONG value is refused whatever the payday set.

        ``0`` means "the schedule says zero", which no write door could have
        produced -- and the eager validation is what makes an empty payday set
        no excuse for it.
        """
        with pytest.raises(PayCalendarError, match="at least 1 day and at most 365"):
            derive_periods([], (era_of(_OPENING, 0),))

    def test_an_empty_calendar_answers_every_question_it_has_an_answer_for(self):
        """An empty calendar is ordinary, and every search still answers ``None``.

        Asserted over the whole public surface rather than the two methods that
        obviously touch the cadence: the claim is that a "cadence required"
        rule did not make the empty calendar unbuildable or turn its searches
        into refusals.

        **The cadence read is asserted too, and it is the assertion this case
        did not have before plan step C4-d**: the class it replaces built this
        calendar with ``cadence_days=None`` and its whole point was that no
        method READ the cadence.  A calendar carries one now, so the honest
        version checks that reading it ANSWERS -- which is
        ``PayCalendar.cadence`` having become total.
        """
        calendar = PayCalendar.from_paydays(
            paydays=[], eras=eras_of([], 14), user_id=1,
            history_opens_on=None,
        )
        day = date(2026, 1, 2)

        assert calendar.cadence.cadence.days == 14
        assert calendar.periods == ()
        assert calendar.opening_bound() is None
        assert calendar.horizon() is None
        assert calendar.period_containing(day) is None
        assert calendar.span_containing(day) is None
        assert calendar.period_starting_on_or_after(day) is None
        assert calendar.period_starting_on_or_before(day) is None
        assert calendar.period_by_id(1) is None
        assert len(calendar.window(0, 6)) == 0
        assert len(calendar.overlapping(day, date(2027, 1, 1))) == 0
        assert len(calendar.axis(day, date(2027, 1, 1))) == 0
        # The one method that MUST refuse: it exists to name a row a NOT NULL
        # foreign key can point at, and there is none.
        with pytest.raises(PayCalendarError, match="no materialised pay period"):
            calendar.filing_period(day)


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

