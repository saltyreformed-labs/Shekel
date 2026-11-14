"""What the derivation refuses and why: a cadence out of the stored range, not a plain int or of no
known kind, a repeated payday, a value that is not a date, and an absent cadence with paydays or
without, the cadence graded before the payday set; and the empty calendar a required cadence
still builds.
"""

from datetime import date, datetime, timedelta

import pytest

from app.enums import BusinessDayShiftEnum
from app.services import pay_rhythm
from app.services.pay_calendar import (
    PayCalendar,
    PayCalendarError,
    cadence_steps_to,
    derive_periods,
    nominal_payday,
)
from tests._test_helpers import era_of, eras_of

from ._helpers import _OPENING


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
        owner has no schedule at all" -- whose legality depended on whether
        they had paydays until plan step C4-d refused it beside either payday
        set.  ``TestTheCadenceIsRequired`` below, which replaced
        ``TestTheCadenceIsRequiredOnlyBesideAPayday`` at that step, owns the
        refusal in both payday shapes.
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
