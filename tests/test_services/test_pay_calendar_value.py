"""
Shekel Budget App -- The pay calendar VALUE (plan step C2-a)

C1 proved the derivation; this proves the value built on it.  The step's whole
point is that ``app/`` holds SIX implementations of "which pay period contains
this date" (ledger row **P6**) which disagree at the edges, so these tests are
written at the edges rather than in the middle:

* the three QUESTIONS answer differently, and each difference is pinned --
  containment says ``None`` past the horizon where the span PROJECTS and the
  filing rule CLAMPS.  A test that only asked mid-schedule would pass against
  all six of the implementations this value replaces;
* the FILING rule is proven equal to the chain it deleted
  (``loan_ledger.find_period_containing_date`` composed with
  ``resolve_anchor_pay_period``) over every MATERIALISED shape, and separately
  over STORED-style period rows that a calendar cannot express at all -- a
  hole, two holes, an overlap, a shared boundary day, a runaway end.  Those
  are unconstructible through :class:`PayCalendar` because derived periods
  TILE, which is the value's own invariant, so they are built by hand.
  **That second half used to live in a session probe outside the repository**
  while four docstrings cited it as proof; plan step C2-d landed it here,
  along with the counterexample that states the equivalence's PRECONDITION
  (index order must agree with date order) rather than leaving it implied;
* the TILING invariant is asserted directly, because it is what made the
  recurrence arc's ``PeriodCalendar.__post_init__`` refusals unconstructible
  rather than merely unused -- plan step C2-b2 then deleted that class, so this
  is now the only place the property is asserted at all;
* a WINDOW keeps the ends the whole calendar derived (ledger row **P14**), and
  the control shows the value a window would report if it were re-derived --
  the ``$150,000.00`` shape;
* production's own numbers appear as the four ``loan_opening`` entries dated
  years before the first payday, which is the state the filing clamp exists for
  and the one a contiguous test schedule never reaches.

**No date here is read from a clock.**  Every date is a literal or explicit
``timedelta`` arithmetic on one, so these pass identically under
``TZ=Pacific/Kiritimati`` and under the ``SHEKEL_FAKE_TODAY`` sweep.  The value
has no clock; a test that gave it one would be testing the fixture.
"""

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from itertools import islice
from types import SimpleNamespace

import pytest

from app.services.pay_calendar import (
    DerivedPeriod,
    FiledRow,
    PayCalendar,
    PayCalendarError,
    PeriodWindow,
    containing_period,
    latest_started_period,
    paychecks_from,
    span_starting_on_or_after,
)
from app.utils.dates import CALENDAR_DATE_MAX

from tests._test_helpers import (
    eras_of,
)

#: A contiguous biweekly schedule -- production's shape, four paydays of it.
#: Ids are deliberately not 0-based so a test cannot pass by confusing an id
#: with an ordinal, which is the confusion ledger row P13 is about.
BIWEEKLY = [
    (10, date(2026, 1, 2)),
    (11, date(2026, 1, 16)),
    (12, date(2026, 1, 30)),
    (13, date(2026, 2, 13)),
]

#: Paydays that are NOT one cadence apart, so ``lead(start) - 1`` and
#: ``start + cadence - 1`` give different answers.  Every test about a derived
#: end needs this shape: on ``BIWEEKLY`` the two rules coincide, so an
#: assertion written there passes against the defect it is meant to catch.
OFF_CADENCE = [
    (10, date(2026, 1, 2)),
    (11, date(2026, 1, 16)),
    (12, date(2026, 1, 20)),
]

#: A calendar holding an UNSAVED candidate payday, which
#: :func:`~app.services.pay_calendar.derive_periods` accepts by design and plan
#: step C3's writer will build.  ``filing_period`` must not hand one of these
#: to a ``NOT NULL`` foreign key.
WITH_UNSAVED = [
    (None, date(2026, 1, 2)),
    (11, date(2026, 1, 16)),
]

#: The same shape with the candidate INSIDE the saved set rather than at its
#: head, which is the axis ``WITH_UNSAVED`` does not vary and the only one on
#: which :meth:`PayCalendar.saved` behaves differently: filtering the candidate
#: out leaves a hole where slicing never can.  ``pay_period_write`` cannot
#: build it today (its floor keeps candidates after the last saved payday) and
#: plan step **C6** builds it by design.
WITH_INTERIOR_UNSAVED = [
    (10, date(2026, 1, 2)),
    (None, date(2026, 1, 16)),
    (12, date(2026, 1, 30)),
]

#: The shapes whose paydays are all SAVED.  The filing-rule equivalence is
#: claimed over these only: the chain it replaces reads ORM rows.
MATERIALISED_SHAPES = [
    ("contiguous biweekly", BIWEEKLY, 14),
    ("off-cadence paydays", OFF_CADENCE, 14),
    ("one payday only", [(1, date(2026, 1, 2))], 14),
    (
        "paydays a day apart",
        [(1, date(2026, 1, 2)), (2, date(2026, 1, 3)), (3, date(2026, 1, 4))],
        14,
    ),
    ("a long stretch between paydays",
     [(1, date(2026, 1, 2)), (2, date(2026, 3, 20))], 14),
    ("monthly cadence", [(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], 30),
]

#: Every schedule shape the payday model can express, including the ones
#: production cannot supply.  Each is a ``(name, paydays, cadence)`` triple.
SHAPES = [
    ("contiguous biweekly", BIWEEKLY, 14),
    ("off-cadence paydays", OFF_CADENCE, 14),
    ("an unsaved candidate payday", WITH_UNSAVED, 14),
    ("one payday only", [(1, date(2026, 1, 2))], 14),
    (
        "paydays a day apart -- the one-day period "
        "ck_pay_periods_date_order forbade and C4-c legalised",
        [(1, date(2026, 1, 2)), (2, date(2026, 1, 3)), (3, date(2026, 1, 4))],
        14,
    ),
    (
        "a long stretch between paydays",
        [(1, date(2026, 1, 2)), (2, date(2026, 3, 20))],
        14,
    ),
    ("monthly cadence", [(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], 30),
]


def calendar(paydays=None, cadence=14, user_id=1):
    """Build a calendar, defaulting to the contiguous biweekly shape.

    Args:
        paydays: ``(period_id, payday)`` pairs; ``BIWEEKLY`` when omitted.
        cadence: Days between paydays.
        user_id: The owner.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar`.
    """
    paydays = BIWEEKLY if paydays is None else paydays
    return PayCalendar.from_paydays(
        paydays, eras_of(paydays, cadence), user_id, history_opens_on=None,
    )


def _filed(*, table="budget.transactions", row_id=77, period_id):
    """Build the :class:`FiledRow` a ``require_period`` test places.

    Keyword-only for the value's own reason: these tests would otherwise be
    the first place two bare ids get written in a fixed order again.
    """
    return FiledRow(table=table, row_id=row_id, period_id=period_id)


class TestThePeriodsAreDerivedAndCannotBeSuppliedOrChanged:
    """The value stores the paydays and derives everything else."""

    def test_periods_are_not_an_init_argument(self):
        """Supplying periods is refused, so no caller can plant a hole.

        The whole tiling argument rests on the periods coming from the
        derivation.  If they could be passed in, every invariant below would be
        a claim about a well-behaved caller.
        """
        with pytest.raises(TypeError):
            PayCalendar(
                user_id=1, paydays=(), cadence_days=14, periods=(),
            )

    def test_the_value_is_frozen(self):
        """A derived value that could be reassigned would need a reconciler."""
        cal = calendar()
        with pytest.raises(AttributeError):
            cal.periods = ()

    def test_paydays_are_canonicalised_so_input_order_does_not_leak(self):
        """One payday SET is one value, whatever order it arrived in.

        The firing control for a real defect: before ``__post_init__``
        rewrote ``paydays`` off the derivation, these two compared UNEQUAL
        while every answer they gave was identical.
        """
        forward = calendar(BIWEEKLY)
        backward = calendar(list(reversed(BIWEEKLY)))
        assert forward == backward
        assert forward.paydays == backward.paydays == tuple(BIWEEKLY)

    def test_equality_is_on_the_facts(self):
        """Owner and cadence are facts; changing either is a different calendar."""
        assert calendar() != calendar(user_id=2)
        assert calendar() != calendar(cadence=15)

    def test_a_bad_payday_set_is_refused_at_construction(self):
        """Validation happens once, where the fact enters."""
        with pytest.raises(PayCalendarError):
            calendar([(1, date(2026, 1, 2)), (2, date(2026, 1, 2))])


class TestThePeriodsTile:
    """``[opening_bound(), horizon()]`` is covered once, with no hole."""

    @pytest.mark.parametrize(
        "name,paydays,cadence", SHAPES, ids=[s[0][:40] for s in SHAPES],
    )
    def test_every_day_in_the_saved_span_has_exactly_one_period(
        self, name, paydays, cadence,
    ):
        """Consecutive paydays define adjacent intervals, by construction.

        This is what made the recurrence arc's ``PeriodCalendar.__post_init__``
        refusals -- an overlapping or reversed schedule -- unconstructible here
        rather than merely unchecked; plan step **C2-b2** then deleted that
        class, so this assertion is what the property rests on now.  A fence
        whose subject went, followed by the fence.
        """
        cal = calendar(paydays, cadence)
        day = cal.opening_bound()
        while day <= cal.horizon():
            covering = [
                period for period in cal.periods
                if period.start_date <= day <= period.end_date
            ]
            assert len(covering) == 1, f"{name}: {day} covered by {len(covering)}"
            day += timedelta(days=1)

    def test_a_gap_between_paydays_is_absorbed_not_left_as_a_hole(self):
        """The state five runtime fences police is not expressible here.

        Paydays 2026-01-02, 01-16 then a jump to 02-20 -- a five-day hole under
        the STORED model, where ``end_date`` would sit at 01-29 and nothing
        would cover 01-30..02-19.  Derived, the second period simply runs to
        the day before the next payday.
        """
        cal = calendar(
            [(1, date(2026, 1, 2)), (2, date(2026, 1, 16)), (3, date(2026, 2, 20))],
        )
        assert cal.periods[1].end_date == date(2026, 2, 19)
        for day in (date(2026, 1, 30), date(2026, 2, 19)):
            assert cal.period_containing(day) is cal.periods[1]


class TestTheThreeQuestionsAnswerDifferently:
    """Containment, the span, and the filing rule diverge past the horizon."""

    def test_at_the_horizon_all_three_agree(self):
        """Inside the schedule there is one answer, which is why P6 went unnoticed."""
        cal = calendar()
        last_day = cal.horizon()
        assert cal.period_containing(last_day) is cal.periods[-1]
        assert cal.span_containing(last_day) is cal.periods[-1]
        assert cal.filing_period(last_day) is cal.periods[-1]

    def test_one_day_past_the_horizon_they_split(self):
        """The edge the six implementations disagreed at, pinned.

        Containment refuses, the span projects a period no foreign key can
        point at, and the filing rule clamps back onto a saved row.
        """
        cal = calendar()
        beyond = cal.horizon() + timedelta(days=1)

        assert cal.period_containing(beyond) is None

        span = cal.span_containing(beyond)
        assert span is not None
        assert span.period_id is None
        assert span.period_index == cal.periods[-1].period_index + 1
        assert span.start_date == beyond
        assert span.end_date == beyond + timedelta(days=13)
        assert span.end_is_projected is True

        filed = cal.filing_period(beyond)
        assert filed is cal.periods[-1]
        assert filed.period_id == 13

    def test_a_period_says_itself_whether_it_is_projected(self):
        """``is_projected`` is the named form of *this period has no row*.

        Ruling **R-SAL18** (plan step salary:S3-e-2): a consumer that must
        know whether the schedule has reached a period asks the period, by
        name, rather than being handed a boundary date -- and never reads
        ``period_id`` raw, because pay-calendar plan step C2-f2c stopped that
        consumer knowing how a period spells its key.  Every saved period of
        a calendar answers ``False``; every period a projection axis adds
        past the horizon answers ``True``; and the boundary between the two
        is the horizon exactly, on both the saved-only value and the axis
        that projects beyond it.
        """
        cal = calendar()
        assert all(period.is_projected is False for period in cal.periods)
        assert cal.saved()[-1].is_projected is False

        axis = cal.projection_axis(
            cal.opening_bound(), cal.horizon() + timedelta(days=100),
        )
        flags = [period.is_projected for period in axis]
        assert flags[:len(cal.periods)] == [False] * len(cal.periods)
        assert flags[len(cal.periods):] == [True] * (len(axis) - len(cal.periods))
        assert len(axis) > len(cal.periods)
        first_projected = axis[len(cal.periods)]
        assert first_projected.start_date == cal.horizon() + timedelta(days=1)
        assert first_projected is not cal.span_containing(cal.horizon())

    def test_before_the_first_payday_the_span_refuses_and_filing_clamps(self):
        """Nothing is projected backwards; a record still needs a paycheck.

        This is production's own state: four ``loan_opening`` entries are dated
        2018-12-01 and 2023-02-14 against a first payday of 2026-03-26.
        """
        cal = calendar()
        long_before = date(2018, 12, 1)
        assert cal.period_containing(long_before) is None
        assert cal.span_containing(long_before) is None
        assert cal.filing_period(long_before) is cal.periods[0]

    def test_the_span_is_total_from_the_opening_payday_onward(self):
        """Two years of days, every one covered by the span it reports."""
        cal = calendar()
        day = cal.opening_bound()
        end = day + timedelta(days=730)
        while day <= end:
            span = cal.span_containing(day)
            assert span is not None, day
            assert span.start_date <= day <= span.end_date, day
            day += timedelta(days=1)

    def test_projected_spans_are_contiguous_with_the_saved_ones(self):
        """The projection continues the schedule rather than restarting it."""
        cal = calendar()
        previous = cal.periods[-1]
        for step in range(1, 30):
            day = cal.horizon() + timedelta(days=step * 14 - 13)
            span = cal.span_containing(day)
            assert span.start_date == previous.end_date + timedelta(days=1)
            assert span.period_index == previous.period_index + 1
            previous = span

    def test_the_span_projects_at_the_owners_cadence_not_a_fixed_fortnight(self):
        """Ledger row P20: the axis this replaces was hardcoded to 14 days.

        Hand-computed on the era's grid (plan step ``C17-b-2``): every 30
        days from 2026-01-01 is 01-31, 03-02, 04-01, 05-01, so 04-15 falls in
        the paycheck 04-01 .. 04-30.  *It read 04-02 .. 05-01 while the
        projection stepped from the recorded 02-01, which is the 01-31
        paycheck paid a day late.*
        """
        cal = calendar([(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], cadence=30)
        span = cal.span_containing(date(2026, 4, 15))
        assert span.start_date == date(2026, 4, 1)
        assert span.end_date == date(2026, 4, 30)

    def test_containment_SKIPS_an_unsaved_candidate(self):
        """The materialisation filter on the containment search itself.

        ``period_containing`` said "the SAVED period" in its first line and
        searched all of them until plan step **C2-f2b**, the last of the five
        searches here to rest on the argument that no calendar holding an
        unsaved candidate reaches it.  That argument was true and this package
        has twice ruled true is not structural -- ``filing_period`` and
        ``period_starting_after`` were each corrected after an adversarial
        review fed them a candidate and got a ``period_id`` of ``None`` back
        for a ``NOT NULL`` column.

        The consumers are why it matters, and both write or filter that same
        column: ``recurrence._occurrence`` PLACES a generated row on this
        answer, and ``companion_service`` SCOPES its transaction query by it --
        where ``pay_period_id == None`` is not an error but ``IS NULL``, which
        returns no rows silently.  ``WITH_INTERIOR_UNSAVED`` puts the candidate
        BETWEEN two saved paydays, the position a head-or-tail shape cannot
        test.

        ``span_containing`` is the control on the same day: it answers, because
        being TOTAL is its whole contract -- so this pins the filter rather
        than the calendar's contents.
        """
        cal = PayCalendar.from_paydays(WITH_INTERIOR_UNSAVED, eras_of(WITH_INTERIOR_UNSAVED, 14), user_id=1, history_opens_on=None)
        candidate = cal.periods[1]
        assert candidate.period_id is None
        inside = candidate.start_date + timedelta(days=3)

        assert cal.period_containing(inside) is None

        span = cal.span_containing(inside)
        assert span is not None
        assert span.period_id is None

        # The saved periods either side still answer, so the filter removed
        # the candidate and nothing else.
        assert cal.period_containing(cal.periods[0].start_date) is cal.periods[0]
        assert cal.period_containing(cal.periods[2].start_date) is cal.periods[2]


class TestTheFilingRuleEqualsTheChainItDeletes:
    """The 2026-08-10 ruling's evidence, re-run as a test."""

    @staticmethod
    def chain(periods, day):
        """Answer as ``loan_ledger`` did: containment, else two fallbacks.

        A transcription of ``find_period_containing_date`` composed with
        ``resolve_anchor_pay_period``, kept here rather than imported because
        plan step C2-d DELETED both -- so this is the only surviving statement
        of the rule the clamp replaced, and the equivalence has to be graded
        against it rather than against a live function.

        **It reduces by ``period_index``, and that is not decoration.**  Both
        fallbacks take the highest index and the last resort takes
        ``periods[0]`` -- position in an index-ordered list.  The clamp reduces
        by ``start_date``.  On a schedule where those two orders disagree the
        rules part company, which is what
        :meth:`test_index_order_disagreeing_with_date_order_is_the_one_divergence`
        pins.

        Takes a period SEQUENCE rather than a calendar, so the same
        transcription grades both datasets: the shapes a
        :class:`~app.services.pay_calendar.PayCalendar` can hold, and the
        stored-style shapes below that it cannot.

        Args:
            periods: The periods to answer over, as the stored rows would be --
                index order, whatever their dates do.
            day: The date to file.

        Returns:
            The :class:`~app.services.pay_calendar.DerivedPeriod` the old chain
            would choose.
        """
        containing, fallback = None, None
        for period in periods:
            if period.start_date <= day <= period.end_date:
                if containing is None or period.period_index > containing.period_index:
                    containing = period
            elif period.end_date < day:
                if fallback is None or period.period_index > fallback.period_index:
                    fallback = period
        located = containing if containing is not None else fallback
        return located if located is not None else periods[0]

    @staticmethod
    def clamp(periods, day):
        """Answer as the SHIPPED rule does, over a bare period sequence.

        :meth:`~app.services.pay_calendar.PayCalendar.filing_period` cannot be
        asked these shapes -- a calendar DERIVES its periods from paydays, so
        it tiles and can hold neither a hole nor an overlap.  This composes the
        two shipped pieces that method is built from
        (:func:`~app.services.pay_calendar.latest_started_period` plus the
        clamp to the earliest), so the rule under test is production code and
        not a second transcription.

        Args:
            periods: The periods to answer over, in any order.
            day: The date to file.

        Returns:
            The :class:`~app.services.pay_calendar.DerivedPeriod` the clamp
            chooses.
        """
        by_date = tuple(sorted(periods, key=lambda p: p.start_date))
        located = latest_started_period(by_date, day)
        return located if located is not None else by_date[0]

    @pytest.mark.parametrize(
        "name,paydays,cadence", MATERIALISED_SHAPES,
        ids=[s[0][:40] for s in MATERIALISED_SHAPES],
    )
    def test_the_two_rules_agree_on_every_day_of_every_shape(
        self, name, paydays, cadence,
    ):
        """One clamp replaces three branches over two functions.

        Over MATERIALISED shapes only, and the scope is the honest one:
        the chain being deleted reads ``PayPeriod`` ORM rows, every one of
        which is saved, so it has no behaviour over an unsaved candidate to
        be equivalent TO.  Where the calendar holds one the new rule
        deliberately differs -- it skips it, because the answer feeds a
        ``NOT NULL`` foreign key.
        """
        cal = calendar(paydays, cadence)
        day = cal.opening_bound() - timedelta(days=40)
        end = cal.horizon() + timedelta(days=200)
        while day <= end:
            assert cal.filing_period(day) == self.chain(cal.periods, day), f"{name} {day}"
            day += timedelta(days=1)

    def test_the_comparison_can_fail(self):
        """The firing control: the old chain and a WRONG rule must not agree.

        Without this the test above would pass over any rule that happened to
        equal the transcription, including a transcription that had drifted.
        """
        cal = calendar()
        beyond = cal.horizon() + timedelta(days=1)
        assert self.chain(cal.periods, beyond) is cal.periods[-1]
        assert cal.periods[0] != self.chain(cal.periods, beyond)

    def test_filing_never_returns_an_unsaved_or_projected_period(self):
        """A ``NOT NULL`` foreign key cannot point at ``period_id = None``.

        ``WITH_UNSAVED`` is the shape that made this fire.  Before an
        adversarial review of this step, ``filing_period`` searched every
        payday and returned the unsaved one -- on the ordinary containment path
        as well as the clamp -- because "not a projection" was mistaken for
        "saved".
        """
        for paydays in (BIWEEKLY, OFF_CADENCE, WITH_UNSAVED):
            cal = calendar(paydays)
            for offset in (-500, 0, 5, 400, 5000):
                filed = cal.filing_period(
                    cal.opening_bound() + timedelta(days=offset),
                )
                assert filed.period_id is not None, (paydays, offset)

    def test_filing_raises_when_every_payday_is_an_unsaved_candidate(self):
        """There is no id to point at, so there is no safe value to invent."""
        cal = calendar([(None, date(2026, 1, 2)), (None, date(2026, 1, 16))])
        with pytest.raises(PayCalendarError, match="no materialised pay period"):
            cal.filing_period(date(2026, 1, 5))

    def test_filing_on_an_empty_calendar_raises_rather_than_guessing(self):
        """The companion role holds no paydays, and production has one."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=2, history_opens_on=None)
        with pytest.raises(PayCalendarError, match="no materialised pay period"):
            empty.filing_period(date(2026, 1, 1))

    # ---- the shapes a calendar CANNOT hold ---------------------------

    #: STORED-style period rows: a hole, two holes, an overlap, a stored end
    #: far past the cadence, and index order disagreeing with date order.  A
    #: :class:`PayCalendar` can express none of them -- it derives its periods
    #: from paydays, so they tile -- which is why the equivalence over these
    #: shapes had lived only in the session probe that drove the 2026-08-10
    #: ruling, an artifact outside the repository.  Built as
    #: :class:`~app.services.pay_calendar.DerivedPeriod` values by hand,
    #: bypassing the derivation on purpose: the point is to feed both rules a
    #: schedule the DELETED chain could receive from ``budget.pay_periods`` and
    #: the new one never builds.
    STORED_SHAPES = [
        (
            "a 14-day hole -- the P27 shape",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 15)),
             (2, 1, date(2026, 1, 30), date(2026, 2, 12))],
        ),
        (
            "two holes",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 15)),
             (2, 1, date(2026, 2, 1), date(2026, 2, 14)),
             (3, 2, date(2026, 3, 1), date(2026, 3, 14))],
        ),
        (
            "an overlap -- a stored end past the next payday",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 20)),
             (2, 1, date(2026, 1, 16), date(2026, 1, 29))],
        ),
        (
            "a shared boundary day -- the pair BA-04 cannot see",
            [(1, 0, date(2026, 1, 2), date(2026, 1, 15)),
             (2, 1, date(2026, 1, 15), date(2026, 1, 28))],
        ),
        (
            "a stored end 400 days past the cadence",
            [(1, 0, date(2026, 1, 2), date(2027, 3, 1)),
             (2, 1, date(2027, 3, 2), date(2027, 3, 15))],
        ),
    ]

    @staticmethod
    def _rows(spec):
        """Build stored-style periods from ``(id, index, start, end)`` tuples.

        Args:
            spec: The tuples, in the index order the old loader returned.

        Returns:
            A list of :class:`~app.services.pay_calendar.DerivedPeriod`.
        """
        return [
            DerivedPeriod(
                period_id=pid, period_index=idx, start_date=start,
                end_date=end, end_is_projected=False,
            )
            for pid, idx, start, end in spec
        ]

    @pytest.mark.parametrize(
        "name,spec", STORED_SHAPES, ids=[s[0][:40] for s in STORED_SHAPES],
    )
    def test_the_two_rules_agree_over_shapes_a_calendar_cannot_hold(
        self, name, spec,
    ):
        """A hole, an overlap and a runaway end are all invisible to the clamp.

        **This is the half of the equivalence the suite could not previously
        state**, and its absence was the load-bearing gap: the ruling's
        gapped evidence lived in a session probe nobody could re-run, while the
        docstrings cited it as proof.  A hole is what plan row **P27** is
        about, and it is the reason the arc's four OTHER cutover leaves wait on
        ``balance:X-ad`` and ``C3``.  C2-d is exempt because the clamp asks
        which period most recently OPENED, and a hole changes only which period
        a day is INSIDE.

        Every day from 60 before the first payday to 60 past the last stored
        end, so both fallback branches and the pre-schedule clamp are crossed.
        """
        rows = self._rows(spec)
        day = min(p.start_date for p in rows) - timedelta(days=60)
        end = max(p.end_date for p in rows) + timedelta(days=60)
        while day <= end:
            assert self.clamp(rows, day) == self.chain(rows, day), f"{name} {day}"
            day += timedelta(days=1)

    def test_index_order_disagreeing_with_date_order_is_the_one_divergence(self):
        """The precondition, stated as the counterexample that violates it.

        The two rules are equal on a schedule that is non-overlapping AND
        index-ordered by date.  Both halves matter, and an earlier draft of this
        step's prose named only the first.  Here the stored ordinals run
        BACKWARDS against the dates: the old chain's last resort is
        ``periods[0]`` -- lowest INDEX -- while the clamp takes the earliest
        DATE, so every day below both paydays parts them.

        Unreachable today, and the citation matters more than the reachability:
        ``pay_period_service`` holds the property with the batch guard
        ``_reject_overlapping_batch`` plus a tail-append at ``max_index + 1``,
        and **plan step C3 deletes the first of those**.  That is safe only
        because the chain needing it dies at C2-d -- which is a claim C3 must
        not have to rediscover.
        """
        rows = self._rows([
            (1, 0, date(2026, 3, 1), date(2026, 3, 14)),
            (2, 1, date(2026, 1, 2), date(2026, 1, 15)),
        ])
        below = date(2025, 12, 1)
        # The chain falls to periods[0], the row with the LOWEST index, whose
        # payday is the LATER of the two; the clamp takes the earlier payday.
        assert self.chain(rows, below).period_id == 1
        assert self.clamp(rows, below).period_id == 2
        # And they agree everywhere the two orders happen to coincide -- inside
        # each period, which is the containment branch.
        for inside in (date(2026, 1, 8), date(2026, 3, 8)):
            assert self.chain(rows, inside) == self.clamp(rows, inside)


class TestTheTwoOrderingSearchesAreMirrors:
    """``on_or_after`` and ``on_or_before`` bracket any day."""

    def test_on_or_before_is_the_missing_mirror(self):
        """Three modules open-coded this; one of them as a scan with fallbacks."""
        cal = calendar()
        assert cal.period_starting_on_or_before(date(2026, 1, 15)) is cal.periods[0]
        assert cal.period_starting_on_or_before(date(2026, 1, 16)) is cal.periods[1]
        assert cal.period_starting_on_or_before(date(2025, 1, 1)) is None

    def test_on_or_after_answers_the_next_paycheck(self):
        """The half the recurrence placement axis already used."""
        cal = calendar()
        assert cal.period_starting_on_or_after(date(2026, 1, 3)) is cal.periods[1]
        assert cal.period_starting_on_or_after(date(2026, 1, 16)) is cal.periods[1]
        assert cal.period_starting_on_or_after(date(2030, 1, 1)) is None

    def test_they_bracket_every_day_inside_the_schedule(self):
        """A day is between the paycheck that opened and the one that follows."""
        cal = calendar()
        day = cal.opening_bound()
        while day <= cal.horizon():
            before = cal.period_starting_on_or_before(day)
            after = cal.period_starting_on_or_after(day)
            assert before is not None
            assert before.start_date <= day
            if after is not None:
                assert after.start_date >= day
                assert after.period_index - before.period_index in (0, 1)
            day += timedelta(days=1)

    def test_on_or_before_and_containment_differ_only_past_the_horizon(self):
        """Where they differ is exactly where P6's implementations disagreed."""
        cal = calendar()
        day = cal.opening_bound()
        while day <= cal.horizon():
            assert cal.period_containing(day) is cal.period_starting_on_or_before(day)
            day += timedelta(days=1)
        beyond = cal.horizon() + timedelta(days=1)
        assert cal.period_containing(beyond) is None
        assert cal.period_starting_on_or_before(beyond) is cal.periods[-1]


class TestPeriodStartingAfter:
    """The STRICT pair, which plan step C2-f1 moved here with two deleted queries.

    ``pay_period_service.get_next_period`` asked ``period_index + 1`` and
    ``companion_service.get_previous_period`` asked ``period_index - 1`` -- one
    rule written twice, on the stored ordinal.  The assertions below are the
    ones those two carried, re-pointed at the value, plus the property their
    ordinal form could not state: on a DERIVED calendar the ordinal IS payday
    order, so stepping by index and stepping by payday cannot disagree.
    """

    def test_next_of_a_period_is_the_one_after_it(self):
        """``get_next_period(periods[N])`` was ``periods[N + 1]``; it still is."""
        cal = calendar()
        assert cal.period_starting_after(cal.periods[1].start_date) is cal.periods[2]
        assert cal.period_starting_after(cal.periods[0].start_date) is cal.periods[1]

    def test_next_of_the_last_period_is_none(self):
        """Past the last payday the schedule has not reached there yet."""
        cal = calendar()
        assert cal.period_starting_after(cal.periods[-1].start_date) is None

    def test_previous_of_the_first_period_is_none(self):
        """The mirror's end of the schedule, which the companion nav hides."""
        cal = calendar()
        assert cal.period_starting_before(cal.periods[0].start_date) is None
        assert cal.period_starting_before(cal.periods[2].start_date) is cal.periods[1]

    def test_the_two_are_inverses_across_the_whole_schedule(self):
        """Step forward then back and land where you started, at every payday.

        The property the two ordinal queries could not have: they read a
        STORED ``period_index``, so a schedule whose index order disagreed with
        its payday order would step forward into one period and back into a
        different one.  Here the ordinal is derived from payday order, so the
        round trip is closed by construction -- this asserts it rather than
        assuming it.
        """
        cal = calendar()
        for period in cal.periods[:-1]:
            forward = cal.period_starting_after(period.start_date)
            assert forward is not None
            assert cal.period_starting_before(forward.start_date) is period

    def test_strict_is_what_separates_them_from_the_inclusive_pair(self):
        """A period's own payday is excluded -- the off-by-one five callers shared.

        ``period_starting_on_or_after(payday)`` answers that period ITSELF, so
        a caller that reached for the inclusive search to mean "the next one"
        would file a credit-card payback in the very period it pays back.
        """
        cal = calendar()
        payday = cal.periods[1].start_date
        assert cal.period_starting_on_or_after(payday) is cal.periods[1]
        assert cal.period_starting_after(payday) is cal.periods[2]
        assert cal.period_starting_on_or_before(payday) is cal.periods[1]
        assert cal.period_starting_before(payday) is cal.periods[0]

    def test_they_SKIP_an_unsaved_candidate(self):
        """The materialisation filter, which is what makes the FK write safe.

        ``create_cc_payback_transaction`` writes this answer's ``period_id``
        into ``transactions.pay_period_id``, which is ``NOT NULL``, with no
        guard of its own -- because the SEARCH cannot answer a period that has
        none.  A projection is not the risk (these never project); the risk is
        the other way ``period_id`` is ``None``, an unsaved candidate, which
        ``derive_periods`` accepts by design and which plan step C3's writer
        builds.  ``WITH_INTERIOR_UNSAVED`` puts one BETWEEN two saved paydays,
        which is the position a head-or-tail shape cannot test.

        The INCLUSIVE pair is the control: it still answers the candidate, so
        this pins the filter rather than the calendar's contents.
        """
        cal = PayCalendar.from_paydays(WITH_INTERIOR_UNSAVED, eras_of(WITH_INTERIOR_UNSAVED, 14), user_id=1, history_opens_on=None)
        candidate = cal.periods[1]
        assert candidate.period_id is None

        after = cal.period_starting_after(cal.periods[0].start_date)
        assert after is cal.periods[2]
        assert after.period_id == 12

        before = cal.period_starting_before(cal.periods[2].start_date)
        assert before is cal.periods[0]
        assert before.period_id == 10

        # The control: the inclusive searches are unfiltered and unchanged,
        # which is what the recurrence engine reads.
        assert cal.period_starting_on_or_after(candidate.start_date) is candidate
        assert cal.period_starting_on_or_before(candidate.start_date) is candidate

    def test_no_materialised_period_at_all_answers_none(self):
        """Every payday an unsaved candidate leaves nothing to answer with."""
        cal = PayCalendar.from_paydays([(None, date(2026, 1, 2)), (None, date(2026, 1, 16))], eras_of([(None, date(2026, 1, 2)), (None, date(2026, 1, 16))], 14), user_id=1,
            history_opens_on=None,
        )
        assert cal.period_starting_after(date(2026, 1, 2)) is None
        assert cal.period_starting_before(date(2026, 1, 16)) is None

    def test_a_day_before_the_first_payday_answers_the_first_period(self):
        """The forward search's other end, which the deleted query never reached.

        ``get_next_period`` took a PERIOD, so it could not be asked about a day
        below the schedule at all; the calendar can, and the answer is the
        opening paycheck rather than ``None``.
        """
        cal = calendar()
        assert cal.period_starting_after(date(2020, 1, 1)) is cal.periods[0]
        assert cal.period_starting_before(date(2020, 1, 1)) is None

    def test_they_answer_off_cadence_from_a_day_inside_a_period(self):
        """Asked mid-period rather than on a payday, on an irregular schedule.

        OFF_CADENCE on purpose: the searches key on ``start_date``, so a
        schedule whose paydays are one cadence apart cannot distinguish "the
        period after this payday" from "the period one cadence later".
        """
        cal = PayCalendar.from_paydays(OFF_CADENCE, eras_of(OFF_CADENCE, 14), user_id=1, history_opens_on=None)
        mid = cal.periods[1].start_date + timedelta(days=3)
        assert cal.period_starting_after(mid) is cal.periods[2]
        assert cal.period_starting_before(mid) is cal.periods[1]


class TestAWindowIsAViewAndKeepsTheRealEnds:
    """Ledger row P14, made structural by the type."""

    def test_a_window_keeps_the_end_the_whole_calendar_derived(self):
        """The defect: re-deriving over a slice moves its last period's end.

        **OFF-CADENCE on purpose.**  A first cut of this test used ``BIWEEKLY``,
        where the paydays sit exactly one cadence apart so ``lead(start) - 1``
        and ``start + cadence - 1`` COINCIDE -- the assertion held identically
        against a ``window()`` that re-derived its slice, so it graded nothing.
        An adversarial review of this step caught it.  Here the third payday is
        four days after the second, so the two rules disagree by ten days.
        """
        cal = calendar(OFF_CADENCE)
        window = cal.window(first_index=0, count=2)
        assert [period.end_date for period in window] == [
            date(2026, 1, 15), date(2026, 1, 19),
        ]

    def test_the_control_shows_what_re_deriving_the_slice_would_give(self):
        """The firing control for the test above -- the ``$150,000.00`` shape.

        Building a CALENDAR from the same two paydays projects the second one's
        end off the cadence instead of reading it from the payday that follows,
        and it lands TEN DAYS LATER than the truth.  One period, two answers,
        decided by which window asked -- which is what a window being a
        different TYPE prevents.
        """
        cal = calendar(OFF_CADENCE)
        re_derived = calendar(OFF_CADENCE[:2])
        assert cal.periods[1].end_date == date(2026, 1, 19)
        assert re_derived.periods[1].end_date == date(2026, 1, 29)
        assert re_derived.periods[1].end_date - cal.periods[1].end_date == (
            timedelta(days=10)
        )
        assert re_derived.periods[1].end_is_projected is True
        assert cal.periods[1].end_is_projected is False

    def test_a_window_is_not_a_calendar(self):
        """It carries no cadence and no owner, so nothing can derive from it."""
        window = calendar().window(0, 2)
        assert isinstance(window, PeriodWindow)
        assert not isinstance(window, PayCalendar)
        assert not hasattr(window, "cadence_days")

    def test_a_window_answers_containment_within_itself(self):
        """Scoped on purpose: a day outside the reported columns has no column."""
        cal = calendar()
        window = cal.window(first_index=1, count=2)
        assert window.containing(date(2026, 1, 20)) is cal.periods[1]
        assert window.containing(date(2026, 1, 2)) is None
        assert window.containing(date(2026, 2, 20)) is None

    def test_containing_index_answers_WHERE_in_the_view_that_period_sits(self):
        """The offset a consumer plotting one point per period needs (C2-f2c).

        ``window[window.containing_index(d)] is window.containing(d)`` wherever
        either answers, which is what makes the two incapable of disagreeing:
        they run one bisect between them.
        """
        cal = calendar()
        window = cal.window(first_index=1, count=2)
        assert window.containing_index(date(2026, 1, 20)) == 0
        assert window.containing_index(date(2026, 2, 1)) == 1
        assert window[window.containing_index(date(2026, 2, 1))] is (
            window.containing(date(2026, 2, 1))
        )

    def test_containing_index_is_the_VIEWS_ordinal_not_the_calendars(self):
        """The firing control for the test above.

        A window opening at calendar ordinal 1 answers ``0`` for a day in its
        own first period.  A consumer deriving the offset as
        ``found.period_index - window[0].period_index`` gets the same number
        here; one that read ``found.period_index`` alone would get ``1``, plot
        its marker one point to the right, and be wrong by more the further
        into the schedule the window opens.
        """
        window = calendar().window(first_index=1, count=2)
        assert window.containing_index(date(2026, 1, 20)) == 0
        assert window.containing(date(2026, 1, 20)).period_index == 1

    def test_containing_index_answers_None_outside_the_window(self):
        """Scoped exactly as :meth:`containing` is, and for the same reason.

        Both bounds, because a day BEFORE the view and a day AFTER it take
        different branches of the bisect: the first has no candidate at all,
        the second has one whose ``end_date`` the day is past.  ``0`` is a
        legal answer here, so a caller testing the result for truthiness rather
        than for ``None`` would drop the view's own first period -- which is
        why both of these assert ``is None``.
        """
        window = calendar().window(first_index=1, count=2)
        assert window.containing_index(date(2026, 1, 2)) is None
        assert window.containing_index(date(2026, 2, 20)) is None

    def test_containing_index_survives_a_window_supplied_out_of_order(self):
        """The bisect's ordering precondition is the TYPE's, not the caller's.

        ``__post_init__`` sorts, so this cannot be got wrong -- the same
        argument the containment test one class down makes, asserted for the
        index answer too because a silently wrong OFFSET moves a chart marker
        rather than raising.
        """
        cal = calendar()
        window = PeriodWindow(periods=tuple(reversed(cal.periods)))
        assert window.containing_index(date(2026, 1, 2)) == 0
        assert window.containing_index(date(2026, 2, 20)) == 3

    def test_an_interior_window_is_exactly_the_ordinals_asked_for(self):
        """``window(1, 2)`` is ordinals 1 and 2, and no neighbour of theirs.

        **Inherited coverage, named as such.**  This was
        ``test_pay_period_service.test_returns_correct_window_by_index``,
        which asserted ``[p.period_index ...] == [2, 3, 4]`` against the SQL
        ``get_periods_in_range``; plan step **C2-f2b** deleted that reader
        whole and its tests with it.  The three cases beside it were already
        covered here (the empty window past the end, the zero count, the
        partial window the calendar ends first), and the negative
        ``first_index`` got its own test above -- but nothing asserted an
        INTERIOR window's exact ordinal list, which is the ordinary case
        every ``/grid`` render is.
        """
        cal = calendar()
        assert [p.period_index for p in cal.window(1, 2)] == [1, 2]
        assert [p.period_id for p in cal.window(1, 2)] == [11, 12]

    def test_windows_past_the_end_and_of_no_periods_are_empty_not_errors(self):
        """"No periods requested" and "the calendar ends first" are answers."""
        cal = calendar()
        assert len(cal.window(first_index=99, count=3)) == 0
        assert len(cal.window(first_index=0, count=0)) == 0
        assert len(cal.window(first_index=2, count=10)) == 2

    def test_a_negative_first_index_spends_slots_it_cannot_fill(self):
        """A window opening below ordinal 0 comes back SHORT, not re-based.

        ``first_index`` is an absolute ordinal on the owner's schedule, not an
        offset to clamp: ``window(-1, 3)`` asks for ordinals -1, 0 and 1, and
        the calendar holds two of them.  Two periods come back, not three.

        **This case is inherited coverage and is named as such.**  It was the
        only assertion on ``pay_period_service.get_periods_in_range``'s
        negative-start behaviour (``period_index >= -1 AND period_index < 2``
        in SQL), and plan step **C2-f2b** deleted that reader whole -- all
        three of its ``app/`` call sites were the grid's, and the grid asks
        this method now.  The reader's own test went with it, so the property
        is pinned here or nowhere.

        It is reachable rather than hypothetical: the grid's leftmost ordinal
        is ``current_period.period_index + start_offset`` and ``offset`` is a
        user-supplied query parameter, so ``/grid?offset=-99`` lands here on
        every schedule.  A page one column short is the honest answer -- the
        alternative, silently sliding the window forward to fill the count,
        would show a window the URL did not ask for.
        """
        cal = calendar()
        assert [period.period_index for period in cal.window(-1, 3)] == [0, 1]
        assert len(cal.window(-1, 1)) == 0

    def test_overlapping_takes_both_bounds_inclusively(self):
        """The calendar-month slice the reporting surfaces ask for."""
        cal = calendar()
        window = cal.overlapping(date(2026, 1, 15), date(2026, 1, 16))
        assert [period.period_id for period in window] == [10, 11]

    def test_a_crossed_range_is_refused_rather_than_answered_empty(self):
        """An empty range and a crossed one look identical in the result."""
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            calendar().overlapping(date(2026, 2, 1), date(2026, 1, 1))


class TestCurrentAndFuture:
    """"How many paychecks are left", counting the one the day falls in.

    Plan step **C4**, finding **P70**: the rolling top-up compared its target
    against a ``PayPeriod.end_date >= as_of`` count in SQL, which was the last
    query in ``pay_period_admin`` naming a column plan step C4-c dropped.  The rule
    is this method now, so it is graded here rather than only through the door.
    """

    def test_the_period_containing_the_day_is_counted(self):
        """"Keep N ahead" counts the current period as one of the N.

        The boundary is INCLUSIVE at both ends of the current period: asked on
        a period's own last covered day it is still counted, and asked on the
        day after, it is not.  Both are asserted, because a reader off by one
        passes the first alone.
        """
        cal = calendar()
        assert [p.period_id for p in cal.current_and_future(date(2026, 1, 15))] == [
            10, 11, 12, 13,
        ]
        assert [p.period_id for p in cal.current_and_future(date(2026, 1, 16))] == [
            11, 12, 13,
        ]

    def test_it_reads_the_DERIVED_end_and_not_the_cadence_projection(self):
        """Graded on ``OFF_CADENCE``, where the two rules disagree.

        On ``BIWEEKLY`` ``lead(start) - 1`` and ``start + cadence - 1`` give
        the same day, so an assertion written there passes against the defect
        it is meant to catch.  Here the 01-16 period ends 01-19 (the day before
        the 01-20 payday) and NOT 01-29 (its own payday plus a cadence), so a
        count taken on 01-20 drops it -- and a reader using the projection
        would keep it.

        The second assertion is a SEPARATE defect's control and is why one day
        is not enough: on 01-20 a reader comparing ``start_date`` instead of
        the end gives the same ``[12]`` and passes.  On 01-17 -- a day INSIDE
        the 01-16 period rather than on a payday -- the end rule keeps that
        period and the start rule drops it, so the two days together pin both.
        """
        cal = calendar(OFF_CADENCE)
        assert [p.period_id for p in cal.current_and_future(date(2026, 1, 20))] == [
            12,
        ]
        assert [p.period_id for p in cal.current_and_future(date(2026, 1, 17))] == [
            11, 12,
        ]

    def test_past_the_horizon_it_ANSWERS_empty_where_overlapping_REFUSES(self):
        """The one behaviour that makes this its own producer.

        ``overlapping(day, horizon())`` is the same question with its bounds
        written out, and it is the wrong door: past the horizon those bounds
        cross, which that producer treats as a caller defect because an empty
        range and a crossed one are indistinguishable in ITS answer.  Here they
        are not -- "every paycheck has already ended" is exactly the state the
        rolling top-up exists to repair, so it must come back as a count of
        zero rather than as a 500 on ``/grid`` and ``/dashboard``.

        The control is the pair: the refusal is shown FIRING on the same
        calendar and the same day, so this cannot pass by both doors being
        lenient.
        """
        cal = calendar()
        past_horizon = date(2026, 2, 27)
        assert cal.horizon() == date(2026, 2, 26)
        assert len(cal.current_and_future(past_horizon)) == 0
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            cal.overlapping(past_horizon, cal.horizon())

    def test_an_empty_calendar_answers_an_empty_window(self):
        """An owner with no payday has no paycheck left, which is not an error."""
        assert len(PayCalendar.from_paydays([], eras_of([], 14), 7, history_opens_on=None,
        ).current_and_future(date(2026, 1, 2))) == 0

    @pytest.mark.parametrize("name,paydays,cadence", SHAPES + [
        # The one shape ``SHAPES`` omits and the only one on which this
        # claim BITES: an unsaved candidate BETWEEN two saved paydays is
        # where ``saved()`` legitimately leaves a hole and a slice cannot
        # (ledger row **P39**).  Naming it in the docstring and not running
        # it was found by an adversarial review of plan step C4.
        ("an INTERIOR unsaved candidate", WITH_INTERIOR_UNSAVED, 14),
    ])
    def test_the_result_is_a_SUFFIX_of_the_calendar_on_every_shape(
        self, name, paydays, cadence,
    ):
        """A slice of a tiling tiles, so no shape can produce a gapped window.

        The window type refuses a hole, and this producer FILTERS rather than
        slicing an index range -- so the claim that it cannot leave one rests
        on the ends ascending with the paydays.  That is asserted directly
        here, over every shape the payday model can express including the
        interior unsaved candidate on which ``saved()`` legitimately refuses.

        **Each period is probed on its LAST covered day as well as its first,
        and the last is what carries the case.**  Probing openings alone made
        this vacuous against a reader comparing ``start_date``: asking on a
        payday, "starts on or after it" and "ends on or after it" name the same
        suffix, and a mutation run measured this test passing on that defect.
        A period's own end is INSIDE it, so only the end rule keeps it there.

        Args:
            name: The shape's name, for the failure message.
            paydays: Its ``(period_id, payday)`` pairs.
            cadence: Its cadence.
        """
        cal = calendar(paydays, cadence)
        for period in cal.periods:
            expected = [
                p for p in cal.periods if p.period_index >= period.period_index
            ]
            for probe in (period.start_date, period.end_date):
                assert list(cal.current_and_future(probe)) == expected, (
                    name, period.period_index, probe,
                )


class TestPaychecksFromContinuesPastTheSavedSchedule:
    """:func:`~app.services.pay_calendar.paychecks_from`, plan step **R16-b-1**.

    ``current_and_future``'s TOTAL companion: the saved producer answers where
    the schedule reaches, this one keeps naming paydays past it at the owner's
    cadence.  It exists because its absence was a SILENT wrong answer one
    package over -- ``recurrence._occurrence._period_walk`` iterated the saved
    periods, so ``occurrences(..., through=X)`` returned fewer dates than *X*
    asked for and raised nothing.

    Graded HERE and not only through that consumer.  A producer tested only
    through the thing that calls it is the hole plan step R16-a's adversarial
    review measured: replacing ``_charges_for`` (the forward plan's charge
    calendar, since deleted at plan step recurrence:R16-c-2) wholesale left
    5,427 tests green, because every control built its input by hand.
    """

    def test_the_saved_run_ends_exactly_where_the_saved_producer_does(self):
        """The saved prefix is ``current_and_future``, and the SEAM is graded.

        The equality itself is structural since this method yields
        ``current_and_future_window`` rather than restating its admission test,
        so asserting only that would grade nothing.  What is graded here is the
        JOIN: the saved run is exactly as long as the saved producer's answer,
        and the very next value is a PROJECTION that continues it -- adjacent,
        one ordinal on, and carrying no ``period_id``.  A composition that
        dropped a saved period, repeated one, or restarted the projection at the
        wrong payday fails on the element after the prefix, which is the one
        place the two halves meet.
        """
        for name, paydays, cadence in SHAPES:
            cal = calendar(paydays, cadence)
            for probe in (cal.opening_bound(), cal.horizon()):
                saved = list(cal.current_and_future(probe))
                assert len(saved) > 0, (name, probe)
                run = list(islice(paychecks_from(cal, probe), len(saved) + 1))
                assert run[:-1] == saved, (name, probe)
                seam, last_saved = run[-1], saved[-1]
                assert seam.period_id is None, (name, probe)
                assert seam.end_is_projected, (name, probe)
                assert seam.start_date == last_saved.end_date + timedelta(days=1), (
                    name, probe, last_saved, seam,
                )
                assert seam.period_index == last_saved.period_index + 1, (
                    name, probe,
                )

    def test_it_projects_at_the_cadence_once_the_saved_run_ends(self):
        """Past the horizon the paydays continue, marked as projections.

        The dates are computed from the last SAVED payday plus 14n rather than
        read off the value: ``BIWEEKLY`` ends at ``2026-02-13`` (index 3), so
        the next two paychecks open ``2026-02-27`` and ``2026-03-13`` at
        indices 4 and 5.  ``period_id`` is ``None`` on both, which is what
        stops a caller writing a projection into a foreign key.
        """
        cal = calendar()
        first_six = list(islice(paychecks_from(cal, date(2026, 1, 2)), 6))

        assert [p.period_id for p in first_six] == [10, 11, 12, 13, None, None]
        assert [p.period_index for p in first_six] == [0, 1, 2, 3, 4, 5]
        assert [p.start_date for p in first_six[4:]] == [
            date(2026, 2, 27), date(2026, 3, 13),
        ]
        assert [p.end_date for p in first_six[4:]] == [
            date(2026, 3, 12), date(2026, 3, 26),
        ]
        assert all(p.end_is_projected for p in first_six[4:])

    def test_asked_far_past_the_horizon_it_opens_at_the_covering_paycheck(self):
        """The projection is arithmetic, so a distant opening costs one step.

        ``2030-01-01`` is 101 cadences past ``BIWEEKLY``'s last payday
        (``2026-02-13 + 14 x 101 = 2029-12-28``, which is why the index is
        ``3 + 101``).  The
        first value yielded is the paycheck COVERING that day -- opening
        ``2029-12-28`` at index 104 -- rather than the sequence walking there a
        fortnight at a time and yielding every paycheck in between.
        """
        cal = calendar()
        first = next(iter(paychecks_from(cal, date(2030, 1, 1))))

        assert first.start_date == date(2029, 12, 28)
        assert first.end_date == date(2030, 1, 10)
        assert first.period_index == 104
        assert first.period_id is None
        # It COVERS the day asked for -- the admission test is "has not ended
        # before it", so the covering paycheck qualifies and is the first.
        assert first.start_date <= date(2030, 1, 1) <= first.end_date

    def test_a_day_below_the_opening_bound_yields_the_whole_schedule(self):
        """Nothing is projected BACKWARDS (the 2026-08-10 ruling).

        Before an owner's first payday there is no paycheck, so a *day* under
        the opening bound cannot pull earlier ones into existence -- it simply
        admits every paycheck there is, starting at the first saved one.
        """
        cal = calendar()
        taken = list(islice(paychecks_from(cal, date(2020, 1, 1)), 5))

        assert [p.period_id for p in taken] == [10, 11, 12, 13, None]
        assert taken[0].start_date == date(2026, 1, 2)

    def test_an_owner_with_no_payday_is_answered_nothing(self):
        """An empty calendar has no last payday to continue from.

        The same answer :meth:`PayCalendar.current_and_future` gives them.  The
        sequence returns before the projection, so the cadence this calendar
        carries is never read -- *which is what the paragraph here used to
        argue made ``cadence_days is None`` SAFE beside an empty payday set.
        Plan step ``pay_calendar:C4-d`` made a cadence required, so the
        property is now that the walk is empty rather than that an absent value
        goes unread.*
        """
        empty = PayCalendar.from_paydays([], eras_of([], 14), 7, history_opens_on=None)

        assert list(paychecks_from(empty, date(2026, 1, 1))) == []
        assert len(empty.current_and_future(date(2026, 1, 1))) == 0

    def test_the_sequence_is_finite_and_stops_at_the_apps_last_calendar_day(self):
        """It ENDS, so a consumer that forgets to stop pulling does not hang.

        Bounded at :data:`~app.utils.dates.CALENDAR_DATE_MAX` exactly as
        ``recurrence._months.walk_months`` is -- the comment on that constant
        names THIS projection as the ``OverflowError`` it was introduced for.
        ``2026-02-13 + 14 x 1953 = 2100-12-24``; the next payday, ``2101-01-07``,
        lies outside the calendar this application can express and is never
        named.
        """
        cal = calendar()
        every = list(paychecks_from(cal, date(2026, 1, 2)))

        assert every[-1].start_date == date(2100, 12, 24)
        assert every[-1].period_index == 1956
        assert len(every) == 1957
        assert all(p.start_date <= CALENDAR_DATE_MAX for p in every)

    def test_every_paycheck_is_ascending_and_adjacent(self):
        """The projected run tiles exactly as the saved run does.

        The tiling invariant :class:`PayCalendar` makes structural for its
        saved periods has to hold across the seam too, or a consumer walking
        this sequence would meet a gap or an overlap at the horizon -- the
        states ledger row **P25** and three retired runtime fences are about.
        Asserted over every shape, and the seam is inside the slice taken.
        """
        for name, paydays, cadence in SHAPES:
            cal = calendar(paydays, cadence)
            run = list(islice(paychecks_from(cal, cal.opening_bound()), 12))
            for earlier, later in zip(run, run[1:]):
                assert earlier.end_date + timedelta(days=1) == later.start_date, (
                    name, earlier, later,
                )
                assert later.period_index == earlier.period_index + 1, (
                    name, earlier, later,
                )


class TestSpanStartingOnOrAfterProjectsPastTheHorizon:
    """:func:`~app.services.pay_calendar.span_starting_on_or_after`, plan step **R16-b-2**.

    ``period_starting_on_or_after``'s TOTAL companion, the pairing
    ``span_containing`` already makes against ``period_containing``.  The
    balance seam's ESTIMATED loan tier places a ``Monthly First`` occurrence on
    "the NEXT paycheck" whether or not the schedule has reached it (ruling
    **R-R69**), so the search has to keep answering past the horizon at the
    owner's cadence.  Graded here and not only through that consumer, for the
    reason the sibling class above gives.

    ``BIWEEKLY`` saves paydays 01-02, 01-16, 01-30 and 02-13 (ids 10-13); the
    last saved period covers 02-13..02-26 and the projection opens 02-27.
    """

    def test_inside_the_saved_schedule_it_is_the_saved_search(self):
        """Where the schedule reaches, the answer is the saved period, id and all."""
        cal = calendar()
        found = span_starting_on_or_after(cal, date(2026, 1, 17))

        assert found == cal.period_starting_on_or_after(date(2026, 1, 17))
        assert found.period_id == 12
        assert found.start_date == date(2026, 1, 30)

    def test_a_day_after_the_last_saved_payday_opens_the_first_projection(self):
        """Inside the LAST saved period, past its payday, the next paycheck is projected.

        02-20 sits in the saved 02-13..02-26 period, so no saved period opens
        on or after it; the answer is the first projected paycheck, which opens
        the day after the saved horizon.
        """
        cal = calendar()
        found = span_starting_on_or_after(cal, date(2026, 2, 20))

        assert found.period_id is None
        assert found.end_is_projected
        assert found.start_date == cal.horizon() + timedelta(days=1)
        assert found.start_date == date(2026, 2, 27)
        assert found.period_index == 4

    def test_a_day_inside_a_projected_span_steps_to_the_next_projection(self):
        """Past the horizon and past a projected payday, the NEXT projection answers.

        03-01 falls inside the projected 02-27..03-12 paycheck, so the first
        span opening on or after it is the one after: 03-13.
        """
        cal = calendar()
        found = span_starting_on_or_after(cal, date(2026, 3, 1))

        assert found.period_id is None
        assert found.start_date == date(2026, 3, 13)
        assert found.end_date == date(2026, 3, 26)
        assert found.period_index == 5

    def test_a_projected_payday_itself_is_admitted(self):
        """"On or after" is inclusive past the horizon exactly as it is inside it."""
        cal = calendar()
        found = span_starting_on_or_after(cal, date(2026, 3, 13))

        assert found.start_date == date(2026, 3, 13)
        assert found.period_index == 5

    def test_it_agrees_with_the_walk_on_every_shape(self):
        """The answer is what :func:`paychecks_from` yields first with ``start_date >= day``.

        Two producers of "the next paycheck" would be ledger row **P6**'s
        shape; this pins that the arithmetic search and the walk name the same
        paycheck on every shape, on the horizon, one day past it, and a year
        past it.
        """
        for name, paydays, cadence in SHAPES:
            cal = calendar(paydays, cadence)
            horizon = cal.horizon()
            for probe in (
                horizon, horizon + timedelta(days=1), horizon + timedelta(days=365),
            ):
                found = span_starting_on_or_after(cal, probe)
                walked = next(
                    period for period in paychecks_from(cal, probe)
                    if period.start_date >= probe
                )
                assert found == walked, (name, probe, found, walked)

    def test_a_day_below_the_opening_bound_is_the_first_saved_period(self):
        """Nothing is projected BACKWARDS: the first paycheck opens on or after any earlier day."""
        cal = calendar()
        found = span_starting_on_or_after(cal, date(2020, 1, 1))

        assert found.period_id == 10
        assert found.start_date == date(2026, 1, 2)

    def test_an_owner_with_no_payday_is_answered_none(self):
        """An empty calendar has no payday to continue from."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), 7, history_opens_on=None)

        assert span_starting_on_or_after(empty, date(2026, 1, 1)) is None


class TestTheWindowTypeEnforcesItsOwnTwoInvariants:
    """Ledger rows **P24** and **P32**, made properties of the type (C2-c).

    The window was a plain frozen tuple until plan step C2-c gave it a
    consumer: the balance seam's per-period entries, whose columns ARE these
    periods.  Two things had to stop being conventions at that point, and they
    are enforced differently ON PURPOSE.

    ORDER is DERIVED -- the constructor sorts, so a caller cannot state it and
    therefore cannot state it wrongly.  CONTIGUITY is CHECKED, because it is a
    property of the input that no constructor can compute its way out of.
    """

    def test_the_periods_are_sorted_at_construction(self):
        """P24: order is not a precondition the caller has to satisfy."""
        cal = calendar()
        backwards = PeriodWindow(periods=tuple(reversed(cal.periods)))
        assert backwards.periods == cal.periods

    def test_the_control_shows_what_an_unsorted_window_would_answer(self):
        """The firing control: containment BISECTS, so disorder is silent.

        Given the periods newest-first, an unsorted bisect misses the day the
        FIRST one covers and answers correctly for a later one by accident --
        a wrong column with no error anywhere.  ``containing_period`` is the
        primitive the window delegates to, driven here on a raw tuple so the
        control is the search rather than the type that now sorts for it.
        """
        cal = calendar()
        unsorted_periods = tuple(reversed(cal.periods))
        assert containing_period(unsorted_periods, date(2026, 1, 2)) is None
        assert PeriodWindow(periods=unsorted_periods).containing(
            date(2026, 1, 2),
        ) is cal.periods[0]

    def test_a_gapped_window_is_refused_with_the_hole_named(self):
        """P32: a column set with a hole renders a balance that does not add up.

        Built by hand, because no calendar VIEW can produce one -- derived
        periods tile, so ``window``, ``overlapping``, ``axis`` and ``saved``
        all return contiguous runs.  That is why the refusal is the negative
        control for a fifth view producer rather than a fence over a live
        state.
        """
        cal = calendar()
        with pytest.raises(PayCalendarError) as excinfo:
            PeriodWindow(periods=(cal.periods[0], cal.periods[2]))
        assert "unbroken span" in str(excinfo.value)
        assert "14 day(s) in no column" in str(excinfo.value)

    def test_an_overlapping_window_is_refused_too(self):
        """The other way two periods can fail to meet, and it double-counts.

        Unconstructible from a calendar for the same reason a hole is, and
        refused by the same rule: adjacency is ``next.start == prev.end + 1``,
        which fails in both directions rather than only upward.
        """
        overlapping = (
            DerivedPeriod(
                period_id=1, period_index=0, start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 20), end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2, period_index=1, start_date=date(2026, 1, 16),
                end_date=date(2026, 1, 29), end_is_projected=True,
            ),
        )
        with pytest.raises(PayCalendarError, match="unbroken span"):
            PeriodWindow(periods=overlapping)

    def test_the_empty_and_single_period_windows_are_legal(self):
        """Vacuously contiguous, and both are real answers."""
        assert len(PeriodWindow(periods=())) == 0
        assert len(PeriodWindow(periods=(calendar().periods[0],))) == 1

    def test_every_view_that_SLICES_the_calendar_is_contiguous(self):
        """A slice of a tiling tiles, over every shape the model can express.

        The claim three of the four view producers rest on.  It is asserted
        with a COUNTER, because the interesting body is inside a
        ``zip(periods, periods[1:])`` that a one-period window never enters --
        which is exactly how the first draft of this test passed while
        checking nothing on the axis that matters (an adversarial review of
        C2-c found it).
        """
        pairs = 0
        for name, paydays, cadence in SHAPES:
            cal = calendar(paydays, cadence)
            first = cal.opening_bound()
            last = cal.horizon()
            views = [cal.window(0, len(cal.periods)), cal.window(1, 2)]
            if first is not None:
                views.append(cal.overlapping(first, last))
                views.append(cal.axis(first, last + timedelta(days=90)))
            for view in views:
                periods = list(view)
                for earlier, later in zip(periods, periods[1:]):
                    pairs += 1
                    assert later.start_date == (
                        earlier.end_date + timedelta(days=1)
                    ), name
        # The loop is not vacuous: every shape contributed adjacency pairs.
        assert pairs > 50

    def test_saved_FILTERS_and_so_can_leave_a_hole_a_slice_cannot(self):
        """The fourth producer, and the one the refusal is a live guard on.

        ``saved()`` drops the periods no foreign key can point at, and dropping
        an INTERIOR one leaves the days it covered in no column -- a filter
        does not preserve a tiling the way a slice does.  The refusal fires
        rather than the seam reporting over the hole, which is the whole point:
        a balance column with a 14-day gap in it does not add up.

        Unreachable through ``calendar_for`` (it reads saved rows only) and
        through ``pay_period_write`` (its forward-only floor keeps candidates
        after the last saved payday); plan step **C6** makes it reachable.
        """
        cal = calendar(WITH_INTERIOR_UNSAVED)
        assert [period.period_id for period in cal.periods] == [10, None, 12]

        with pytest.raises(PayCalendarError) as excinfo:
            cal.saved()

        assert "unbroken span" in str(excinfo.value)
        assert "14 day(s) in no column" in str(excinfo.value)

    def test_a_candidate_at_the_HEAD_or_TAIL_leaves_no_hole(self):
        """The control: it is the candidate's POSITION that decides, not its
        existence.

        Filtering the first or last period off a tiling leaves a shorter
        tiling, so ``saved()`` answers normally -- which is why the shape at
        the head (``WITH_UNSAVED``) could never have caught the case above.
        """
        assert [
            period.period_id for period in calendar(WITH_UNSAVED).saved()
        ] == [11]
        tail_candidate = [
            (10, date(2026, 1, 2)), (11, date(2026, 1, 16)),
            (None, date(2026, 1, 30)),
        ]
        assert [
            period.period_id for period in calendar(tail_candidate).saved()
        ] == [10, 11]


class TestTheSavedWindowIsTheBalanceSeamsDomain:
    """``PayCalendar.saved`` -- what every per-period seam entry reports over."""

    def test_it_holds_every_saved_period_in_payday_order(self):
        """The whole schedule, which is what all eight callers used to pass."""
        assert [period.period_id for period in calendar().saved()] == [
            10, 11, 12, 13,
        ]

    def test_an_unsaved_candidate_is_left_out(self):
        """The seam's maps are keyed by ``pay_periods.id``.

        A period with no id would key every one of them under ``None`` and
        collapse them onto each other, which is ledger row **P21**'s shape.
        ``derive_periods`` accepts an unsaved candidate by design and
        ``pay_period_write`` builds a calendar out of them on every write, so
        the filter is what keeps that calendar out of a balance map.
        """
        window = calendar(WITH_UNSAVED).saved()
        assert [period.period_id for period in window] == [11]

    def test_an_empty_calendar_answers_an_empty_window(self):
        """A brand-new owner has no columns, which is an answer not an error."""
        assert len(PayCalendar.from_paydays([], eras_of([], 14), 1, history_opens_on=None,
        ).saved()) == 0


class TestTheAxisReplacesTheSyntheticProjection:
    """Ledger rows P17 and P20."""

    def test_the_axis_is_saved_where_it_can_be_and_projected_beyond(self):
        """No fabricated id ever enters the real ``pay_periods.id`` namespace."""
        cal = calendar()
        axis = cal.axis(date(2026, 2, 20), date(2026, 4, 10))
        assert [period.period_id for period in axis] == [13, None, None, None, None]
        assert [period.period_index for period in axis] == [3, 4, 5, 6, 7]

    def test_the_axis_tiles_the_range_it_is_asked_for(self):
        """Every day between the bounds falls in exactly one axis period."""
        cal = calendar()
        first, last = date(2026, 2, 20), date(2027, 6, 30)
        axis = cal.axis(first, last)
        day = first
        while day <= last:
            covering = [
                period for period in axis
                if period.start_date <= day <= period.end_date
            ]
            assert len(covering) == 1, f"{day} covered by {len(covering)}"
            day += timedelta(days=1)

    def test_the_axis_of_a_monthly_owner_is_monthly(self):
        """The defect P20 prices at $588,959.22 over twenty years.

        The replaced producer took ``cadence_days=14`` at all six call sites,
        so a monthly-paid owner was credited 26 contributions a year instead of
        12.  Here the count follows the owner's own cadence.
        """
        cal = calendar([(1, date(2026, 1, 1)), (2, date(2026, 2, 1))], cadence=30)
        axis = cal.axis(date(2026, 1, 1), date(2026, 12, 31))
        assert len(axis) == 13
        biweekly = calendar()
        assert len(biweekly.axis(date(2026, 1, 2), date(2026, 12, 31))) == 26

    def test_an_axis_wholly_inside_the_schedule_projects_nothing(self):
        """No projection where the saved calendar already answers."""
        cal = calendar()
        axis = cal.axis(date(2026, 1, 2), date(2026, 2, 12))
        assert all(period.period_id is not None for period in axis)

    def test_an_empty_calendar_yields_an_empty_axis(self):
        """Nothing to project FROM, so nothing is invented."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=2, history_opens_on=None)
        assert len(empty.axis(date(2026, 1, 1), date(2027, 1, 1))) == 0


class TestTheAxisRefusesARangeItCanOnlyHalfCover:
    """Ledger row **P23**, ruled 2026-08-14 (developer) at plan step C2-e.

    ``axis`` used to answer a range opening below the owner's first payday by
    returning the part above it -- silently, with a summary line ("the spans
    covering ``[first_day, last_day]``") that was false whenever it happened
    and a ``Returns`` block that covered only the wholly-before case.  A
    truncated axis and a complete one are indistinguishable in the result, so
    the refusal is the same argument :meth:`PayCalendar.overlapping` already
    makes for a CROSSED range, applied to the other end.

    Nothing is projected backwards (ruled 2026-08-10: before an owner's first
    payday there is no paycheck), so covering the range was never an option --
    which leaves refusing as the only answer that is not a half-truth.
    """

    def test_a_range_opening_below_the_first_payday_is_refused(self):
        """The state P23 measured: 13 days that would be covered by nothing."""
        cal = calendar()
        with pytest.raises(PayCalendarError, match="opens before user"):
            cal.axis(date(2025, 12, 20), date(2026, 3, 1))

    def test_the_refusal_names_the_bound_and_the_days_it_would_drop(self):
        """The message has to be actionable: which day, and how far short."""
        cal = calendar()
        with pytest.raises(PayCalendarError) as raised:
            cal.axis(date(2025, 12, 20), date(2026, 3, 1))
        assert "2025-12-20" in str(raised.value)
        assert "2026-01-02" in str(raised.value)
        assert "13 day(s)" in str(raised.value)

    def test_opening_exactly_ON_the_first_payday_is_accepted(self):
        """The firing control: the bound is inclusive, so this is not refused.

        Without it the test above passes against a refusal that fires one day
        too early and truncates nothing.
        """
        cal = calendar()
        axis = cal.axis(date(2026, 1, 2), date(2026, 3, 1))
        assert axis[0].start_date == date(2026, 1, 2)

    def test_an_empty_calendar_still_answers_rather_than_refusing(self):
        """No first payday means no PARTIAL coverage to hide.

        An owner with no paydays at all gets an empty window, which is what
        :meth:`PayCalendar.saved` answers them too.  The refusal is about a
        range half-covered, not about a calendar that covers nothing.
        """
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=2, history_opens_on=None)
        assert len(empty.axis(date(2020, 1, 1), date(2027, 1, 1))) == 0

    def test_a_crossed_range_is_still_refused_first(self):
        """The two refusals do not shadow each other."""
        cal = calendar()
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            cal.axis(date(2026, 3, 1), date(2026, 2, 1))


class TestTheWindowIsASequence:
    """``PeriodWindow`` indexes and slices, added at plan step C2-e.

    Its consumers -- the seed read at the axis's opening day, the readiness
    chart's downsampled points, the growth engine's own tests -- need the i-th
    period.  Reaching through to :attr:`PeriodWindow.periods` for it is the one
    move that lets a run of periods escape the type guaranteeing their order
    and their tiling, so the type does it.
    """

    def test_an_integer_index_returns_that_period(self):
        cal = calendar()
        window = cal.saved()
        assert window[0] is window.periods[0]
        assert window[-1] is window.periods[-1]

    def test_the_index_is_the_WINDOWS_ordinal_not_the_calendars(self):
        """A window is a VIEW: ``[0]`` is where it starts, not the schedule."""
        window = calendar().window(first_index=2, count=2)
        assert window[0].period_index == 2

    def test_an_index_past_the_end_raises(self):
        with pytest.raises(IndexError):
            calendar().saved()[99]  # pylint: disable=expression-not-assigned

    def test_SLICING_is_refused_outright(self):
        """No consumer in ``app/`` slices a window, and one slice lied.

        A first cut returned a :class:`PeriodWindow` for a slice and let
        ``__post_init__`` refuse the stepped ones.  Two adversarial reviews of
        plan step C2-e landed on it: ``[::2]`` was refused as intended, but
        ``[::-1]`` TILES, so it passed the contiguity check and came back
        silently re-sorted into payday order -- a wrong answer to "walk this
        backwards", given without a word.
        """
        window = calendar().saved()
        for attempt in (slice(None, 2), slice(None, None, 2), slice(None, None, -1)):
            with pytest.raises(TypeError, match="cannot be sliced"):
                window[attempt]  # pylint: disable=expression-not-assigned

    def test_reversed_walks_it_backwards(self):
        """What a caller wanting the whole window in reverse writes instead.

        Free from :meth:`__getitem__` plus :meth:`__len__`, and the reason the
        slice branch has nothing left to serve.
        """
        window = calendar().saved()
        assert [period.period_id for period in reversed(window)] == [
            13, 12, 11, 10,
        ]


class TestTheClampedProjectionAxis:
    """``projection_axis``: ``axis`` with ONE clamp, ruled 2026-08-14.

    The pairing :meth:`PayCalendar.filing_period` already makes against
    :meth:`PayCalendar.period_starting_on_or_before` -- the strict search
    refuses or answers, and the TOTAL companion beside it states its clamp in
    the open.  Every projecting surface calls the companion, which is what lets
    the strict one refuse at all (ledger row **P23**).
    """

    def test_a_range_opening_below_the_first_payday_is_RAISED_not_refused(self):
        """The owner whose first payday has not happened yet.

        An ordinary state: the Generate form asks for "your next (or first)
        payday", so a read pass whose clock precedes the whole schedule is what
        a new owner looks like on the day they set it up.
        """
        cal = calendar()
        axis = cal.projection_axis(date(2025, 12, 20), date(2026, 3, 1))
        assert axis[0].start_date == date(2026, 1, 2)
        # The firing control: the strict sibling refuses that same range, so
        # this cannot pass by the clamp having been dropped.
        with pytest.raises(PayCalendarError, match="opens before user"):
            cal.axis(date(2025, 12, 20), date(2026, 3, 1))

    def test_a_range_already_inside_the_schedule_is_untouched(self):
        """The clamp raises nothing it does not have to."""
        cal = calendar()
        assert list(cal.projection_axis(date(2026, 1, 16), date(2026, 3, 1))) == (
            list(cal.axis(date(2026, 1, 16), date(2026, 3, 1)))
        )

    def test_a_CROSSED_range_is_refused_rather_than_emptied(self):
        """A caller with its bounds the wrong way round is a defect.

        The distinction the clamp must not swallow: a range the CLAMP empties
        is a real answer (the /retirement lever page's ``past_horizon``), while
        a crossed one is a bug.  Folding them together is the hole
        :meth:`PayCalendar.overlapping` refuses to leave open one level down,
        and an adversarial code review of this step caught a first cut doing
        exactly that.
        """
        cal = calendar()
        with pytest.raises(PayCalendarError, match="ends before it starts"):
            cal.projection_axis(date(2026, 3, 1), date(2026, 2, 1))

    def test_a_last_day_behind_the_first_payday_is_EMPTY(self):
        """The clamped-empty case, which is an answer and not a refusal."""
        cal = calendar()
        assert len(cal.projection_axis(
            date(2025, 11, 1), date(2025, 12, 1),
        )) == 0

    def test_an_empty_calendar_yields_an_empty_axis(self):
        """Nothing to project FROM, so nothing is invented -- and no refusal."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=2, history_opens_on=None)
        assert len(empty.projection_axis(
            date(2026, 1, 1), date(2027, 1, 1),
        )) == 0


class TestTheRemainingLookupsMovedIntact:
    """What the recurrence arc's calendar already answered, unchanged."""

    def test_the_bounds_of_an_empty_calendar_are_none_not_an_error(self):
        """The companion role, which by design holds no paydays of its own."""
        empty = PayCalendar.from_paydays([], eras_of([], 14), user_id=2, history_opens_on=None)
        assert empty.opening_bound() is None
        assert empty.horizon() is None
        assert empty.period_containing(date(2026, 1, 1)) is None
        assert empty.span_containing(date(2026, 1, 1)) is None

    def test_the_horizon_is_the_saved_end_and_does_not_move_with_the_projection(self):
        """The recurrence engine bounds generation by it to tell hole from "not yet"."""
        cal = calendar()
        assert cal.horizon() == date(2026, 2, 26)
        cal.span_containing(date(2030, 1, 1))
        assert cal.horizon() == date(2026, 2, 26)


class TestPeriodByIdIsIdentityNotASearch:
    """Plan step C2-b1: which STORED paycheck a rule's start period names."""

    def test_it_finds_the_period_carrying_the_id(self):
        """Keyed on ``period_id``, which is not the ordinal and not the index."""
        found = calendar().period_by_id(12)

        assert found is not None
        assert found.start_date == date(2026, 1, 30)
        # The fixture's ids start at 10 on purpose: an implementation that
        # confused the id with the ordinal would answer the FIRST period here.
        assert found.period_index == 2

    def test_none_in_is_none_out(self):
        """A rule may legitimately name no start period."""
        assert calendar().period_by_id(None) is None

    def test_an_id_that_names_no_period_answers_none(self):
        """A stale in-memory id outliving its ``ON DELETE SET NULL`` row."""
        assert calendar().period_by_id(9999) is None

    def test_it_answers_over_an_off_cadence_schedule_too(self):
        """Identity does not depend on the spacing, unlike every other lookup."""
        found = calendar(OFF_CADENCE).period_by_id(12)

        assert found is not None
        assert found.start_date == date(2026, 1, 20)

    def test_the_searchable_id_space_holds_only_saved_periods(self):
        """Every projection carries ``period_id = None``, so identity is saved-only.

        Ledger row **P21**: an ``{p.id: ...}`` map over a projected axis
        collapses because the projections SHARE that ``None``.  Here the
        consequence is the safe one -- a lookup cannot hand an unsaved period to
        a caller about to write a foreign key.

        Asserted structurally rather than by round-tripping the projection's own
        ``period_id``, which an adversarial review of this step showed was
        vacuous: that value IS ``None``, so the call returned at the guard
        without ever reaching the scan, and the test would have passed against a
        completely broken one.
        """
        cal = calendar()
        projected = cal.span_containing(date(2027, 1, 1))

        assert projected is not None and projected.period_id is None
        assert projected not in cal.periods
        assert all(period.period_id is not None for period in cal.periods)


#: :data:`SHAPES` plus the one axis it does not vary.  Identity indexing is
#: blind to payday SPACING, so six of those seven shapes re-measure one thing;
#: what adds information is a candidate INSIDE the saved set, which
#: ``WITH_UNSAVED`` puts at the head and :data:`WITH_INTERIOR_UNSAVED` puts in
#: the middle.  Appended here rather than to :data:`SHAPES`, which three other
#: classes parametrize over for their own reasons.
_INDEX_SHAPES = SHAPES + [
    ("an interior unsaved candidate", WITH_INTERIOR_UNSAVED, 14),
]


class TestTheSavedIndexIsBOTHTheScopeAndTheLookup:
    """``saved_by_id`` -- ``period_by_id`` in BULK (plan step **C4-a-4**).

    Two callers hold a whole ROW SET rather than one id --
    ``statement_match._candidates`` and both of
    ``reconcile_service._rows``'s scope properties -- and each wrote its own
    ``period_id is not None`` comprehension until this step.  What the one
    accessor buys is not the comprehension: it is that a caller's query SCOPE
    and its per-row LOOKUP come from ONE value, so a row the query returns and
    the mapping cannot place is unconstructible rather than guarded against.

    The cases below grade the value.  **The property that needs a caller is
    graded at the caller** -- that ``destinations_for`` filters on this
    mapping and indexes the same one -- in
    ``test_statement_match/test_create.py``.
    """

    def test_it_keys_on_the_ID_and_not_on_the_ordinal(self):
        """The fixture's ids start at 10, so the two cannot be confused.

        An implementation keyed on ``period_index`` would answer the FIRST
        period for key 0 and hold nothing at 10.
        """
        index = calendar().saved_by_id()

        assert sorted(index) == [10, 11, 12, 13]
        assert index[12].start_date == date(2026, 1, 30)
        assert index[12].period_index == 2

    def test_every_value_IS_the_period_the_scanning_twin_answers(self):
        """One derivation behind both, so an indexer and a scanner agree.

        The whole reason the accessor is on the calendar rather than spelled
        at each caller: two ways of asking "which paycheck is id N" that came
        from two places could drift, and this asserts they are the same
        object rather than merely equal.
        """
        cal = calendar()

        for period_id, period in cal.saved_by_id().items():
            assert period is cal.period_by_id(period_id)

    def test_it_answers_over_an_OFF_CADENCE_schedule_too(self):
        """The end a caller reads is the DERIVED one, on the shape that shows it.

        ``OFF_CADENCE``'s second payday is four days after the first, so
        ``lead(start) - 1`` and ``start + cadence - 1`` disagree -- which is
        exactly the divergence ``pay_periods.end_date`` stored and plan step
        C4-c dropped.  On ``BIWEEKLY`` the two rules coincide and this assertion
        would pass against either.
        """
        index = calendar(OFF_CADENCE).saved_by_id()

        assert index[10].end_date == date(2026, 1, 15)
        assert index[11].end_date == date(2026, 1, 19)

    def test_an_UNSAVED_candidate_is_not_in_it(self):
        """A projection and a candidate carry no id, so neither may be a key.

        Ledger row **P21**: an ``{p.id: ...}`` map over a set holding more
        than one of them collapses, because they SHARE that ``None``.  The
        filter is ``materialised_periods``' -- the package's one "is this
        period SAVED" rule -- rather than a fourth spelling of it.
        """
        cal = calendar(WITH_UNSAVED)
        index = cal.saved_by_id()

        assert sorted(index) == [11]
        assert None not in index
        assert any(period.period_id is None for period in cal.periods)

    def test_an_INTERIOR_unsaved_candidate_is_not_in_it_either(self):
        """The candidate INSIDE the saved set, which the head case cannot show.

        ``WITH_UNSAVED`` puts the candidate FIRST, so an implementation that
        dropped ``periods[0]`` rather than filtering would pass it; this one
        cannot be passed that way, and it is the shape plan step **C6** builds
        by design.  The key set is asserted EXACTLY rather than by absence:
        "the candidate is not in it" is also true of an empty mapping.

        **It replaces a case that could not fail** (adversarial test-quality
        review 2026-08-31): the old one asked whether a period from
        ``span_containing`` -- a PROJECTION, ``period_id`` ``None`` -- was
        among the values, which holds for ``return {}`` and for any keying
        whatever, because ``saved_by_id`` is only ever shown ``self.periods``
        and a projection is never in those.  That is the same vacuity
        ``test_the_searchable_id_space_holds_only_saved_periods`` above records
        a review catching on this very producer pair.
        """
        cal = calendar(WITH_INTERIOR_UNSAVED)
        index = cal.saved_by_id()

        assert sorted(index) == [10, 12]
        assert index[10].start_date == date(2026, 1, 2)
        assert index[12].start_date == date(2026, 1, 30)
        assert any(period.period_id is None for period in cal.periods)

    def test_an_EMPTY_calendar_answers_an_empty_mapping(self):
        """Which as a SCOPE admits nothing, and that is the right answer.

        An owner with no paydays has no rows to offer, so a filter built from
        this returns none -- rather than a scope that is missing and reads as
        unbounded.
        """
        assert calendar(paydays=[]).saved_by_id() == {}

    def test_the_mapping_is_READ_ONLY_and_the_same_one_every_call(self):
        """MEMOIZED, and immutable so the sharing cannot be turned against it.

        One review pass asks this twice -- a candidate scope and a destination
        scope -- and each apply door builds two passes, so a request asks four
        times; that is what the memo is for, and an adversarial design review
        measured the "once per pass" claim a first cut rested on as false
        (2026-08-31).

        **Memoizing and handing out a plain ``dict`` would be worse than not
        memoizing**: the value is a query SCOPE deciding whose budget rows may
        be offered money, so one producer clearing or narrowing it would
        silently narrow the other's. The proxy makes that unconstructible.
        Both halves are asserted, because a memo that returned a fresh copy
        would pass the mutation half alone.
        """
        cal = calendar()
        first = cal.saved_by_id()

        assert cal.saved_by_id() is first
        # The attack this is for is NARROWING, not emptying: dropping one id
        # from a shared scope removes exactly that paycheck's rows from the
        # other producer's offer set, and nothing downstream would say so.
        with pytest.raises(TypeError):
            del first[10]
        with pytest.raises(TypeError):
            first[99] = cal.periods[0]
        assert sorted(cal.saved_by_id()) == [10, 11, 12, 13]

    def test_the_memo_is_PER_CALENDAR_and_not_shared_between_two(self):
        """Two owners' calendars answer their own ids, memo or no memo.

        The slot is an instance field, but a memo written wrong -- on the class,
        or keyed on nothing -- would hand the second calendar the first one's
        scope, which is one owner's rows offered under another's name.
        """
        mine = calendar(paydays=[(1, date(2026, 1, 2))], user_id=1)
        theirs = calendar(paydays=[(81, date(2026, 1, 9))], user_id=2)

        assert sorted(mine.saved_by_id()) == [1]
        assert sorted(theirs.saved_by_id()) == [81]
        assert sorted(mine.saved_by_id()) == [1]

    @pytest.mark.parametrize(
        "name,paydays,cadence", _INDEX_SHAPES,
        ids=[s[0][:40] for s in _INDEX_SHAPES],
    )
    def test_it_equals_the_comprehension_it_replaced_on_every_shape(
        self, name, paydays, cadence,
    ):
        """The accessor and the open-coded map agree over every payday shape.

        The expectation is the comprehension the three retired sites wrote
        out, SPELLED HERE, so the two sides come from two places -- the
        discipline ``TestTheContainmentRuleOnOnePeriod``'s own sweep states,
        for the same reason: an expectation derived from the producer under
        test measures nothing.
        """
        cal = calendar(paydays, cadence)
        expected = {
            period.period_id: period
            for period in cal.periods
            if period.period_id is not None
        }

        assert cal.saved_by_id() == expected, name


class TestTheHistoryBoundIsAFactOfTheCALENDAR:
    """Plan step **balance:X-bh-2**: the second bound the rhythm is read to.

    The value carries ``history_opens_on`` rather than taking it at each
    question, for the reason it carries ``cadence_days``: the paydays and the
    bounds on them are one owner's one rhythm.  What that buys is graded here;
    what the bound DOES is graded in ``test_pay_calendar_rhythm.py``.

    *This class replaced ``TestEarliestStartInMonthIsWhatMonthlyFirstAsks``,
    whose subject the same step deleted as unreached in ``app/`` (ledger row
    **N-396**).  Its five cases were not dropped: they moved to
    ``test_pay_calendar_rhythm.py::TestSavedPaydaysInMonthThrough``, the
    producer that answers the same question over the same set.*
    """

    def test_it_is_carried_and_read_back(self):
        """The fact the loader threads is the fact the rhythm reads."""
        stated = date(2020, 6, 1)

        assert calendar().history_opens_on is None
        assert PayCalendar.from_paydays(BIWEEKLY, eras_of(BIWEEKLY, 14), user_id=1, history_opens_on=stated,
        ).history_opens_on == stated

    def test_two_calendars_differing_only_in_it_are_NOT_equal(self):
        """It is a FACT, so it is compared, and the derivation is not.

        ``periods`` is excluded from equality because it is derived; this is
        an input, and two owners with the same paydays and different stated
        histories are answered differently by the engine.  Equality that
        ignored it would let a memo hand back the wrong one.
        """
        unbounded = PayCalendar.from_paydays(BIWEEKLY, eras_of(BIWEEKLY, 14), user_id=1, history_opens_on=None,
        )
        bounded = PayCalendar.from_paydays(BIWEEKLY, eras_of(BIWEEKLY, 14), user_id=1, history_opens_on=date(2026, 1, 2),
        )

        assert unbounded != bounded
        assert unbounded.periods == bounded.periods

    def test_the_constructor_REQUIRES_it(self):
        """No default, because ``None`` is a real answer as well as an easy one.

        A defaulted argument would let a calendar claim an unbounded rhythm
        its owner never stated -- a wrong figure rather than an error, which
        is the expensive direction.  The rule is graded here rather than
        trusted, because the whole point is that forgetting it must not be
        possible.
        """
        with pytest.raises(TypeError):
            PayCalendar.from_paydays(BIWEEKLY, eras_of(BIWEEKLY, 14), user_id=1)


class TestTheDerivedPeriodContract:
    """What every consumer reads off the value."""

    def test_exactly_one_saved_period_reports_a_projected_end(self):
        """C1's flag, unchanged: the last one, and only when there is one."""
        cal = calendar()
        assert [period.end_is_projected for period in cal.periods] == [
            False, False, False, True,
        ]
        assert PayCalendar.from_paydays([], eras_of([], 14), 1, history_opens_on=None).periods == ()

    def test_a_projected_span_is_a_derived_period_like_any_other(self):
        """One shape for saved and projected, discriminated only by ``period_id``."""
        span = calendar().span_containing(date(2027, 1, 1))
        assert isinstance(span, DerivedPeriod)
        assert span.period_id is None


class TestTheContainmentRuleOnOnePeriod:
    """Pin ``DerivedPeriod.covers``, the one-period span test (**C4-a-3**).

    Ruled at **R-PC31** and landed with plan step C4-a-3 for the three sites
    that open-coded ``start_date <= day <= end_date``: the purchase-date
    warning (``entry_service._sums.entry_list_view``), the recurrence engine's
    base-month scan (``recurrence.compute_due_date``) and this
    package's own ``_searches.containing_index``.

    **The five point cases below came from
    ``entry_service.check_purchase_date_in_period``**, which this method
    replaced and which was DELETED with the ORM read it made: it asked
    ``transaction.pay_period`` for the STORED ``end_date`` plan step C4-c
    drops.  They assert exactly what they asserted there -- inside, before,
    after, on the payday, on the last covered day -- against the DERIVED span
    rather than the column, which is the whole of the move.

    The class does not stop at those five, because five point assertions on
    one biweekly shape cannot say the rule holds for the one-day period, the
    long stretch or the cadence-projected end.  The sweep at the bottom does.
    """

    def test_a_day_inside_the_period_is_covered(self):
        """The ordinary answer: a purchase made mid-paycheck is in period."""
        period = calendar().periods[0]

        assert period.start_date == date(2026, 1, 2)
        assert period.end_date == date(2026, 1, 15)
        assert period.covers(date(2026, 1, 5)) is True

    def test_a_day_BEFORE_the_payday_is_not_covered(self):
        """The lower bound excludes, so the previous paycheck keeps its days."""
        assert calendar().periods[0].covers(date(2025, 12, 31)) is False

    def test_a_day_AFTER_the_last_covered_day_is_not_covered(self):
        """The upper bound excludes, so the next paycheck keeps its days."""
        assert calendar().periods[0].covers(date(2026, 1, 20)) is False

    def test_BOTH_boundary_days_are_covered(self):
        """Inclusive at both ends -- the payday and the day before the next.

        Writing the rule out twice is how a chained comparison comes to carry
        ``<`` on one end, so both ends are asserted rather than one.
        """
        period = calendar().periods[0]

        assert period.covers(date(2026, 1, 2)) is True
        assert period.covers(date(2026, 1, 15)) is True

    def test_the_PROJECTED_last_period_answers_on_its_projected_end(self):
        """The last period's end comes from the cadence, and it still bounds.

        A consumer holding one period cannot tell a fact-derived end from a
        cadence-projected one, which is why this method does not ask: a
        purchase is in or out of its own paycheck's span either way.
        """
        cal = calendar()
        last = cal.periods[-1]

        assert last.end_is_projected is True
        assert last.covers(last.end_date) is True
        assert last.covers(last.end_date + timedelta(days=1)) is False

    def test_it_does_NOT_ask_whether_the_period_is_SAVED(self):
        """An unsaved candidate covers its span; the SEARCH still skips it.

        The two questions are different and this pins the difference rather
        than leaving the search's filter to look like part of the containment
        rule.  ``period_containing`` answers a period a ``NOT NULL``
        ``pay_period_id`` can point at, so it filters to the materialised
        subset first (plan step C2-f2b); ``covers`` is asked OF a period a
        caller is already holding and answers about its span alone.
        """
        cal = calendar(WITH_UNSAVED)
        candidate = next(p for p in cal.periods if p.period_id is None)

        assert candidate.covers(candidate.start_date) is True
        assert cal.period_containing(candidate.start_date) is None

    @pytest.mark.parametrize(
        "name,paydays,cadence", SHAPES, ids=[s[0][:40] for s in SHAPES],
    )
    def test_it_equals_the_predicate_it_replaced_on_every_day_of_every_shape(
        self, name, paydays, cadence,
    ):
        """The method and the open-coded pair agree, day by day, everywhere.

        ``TestTheFilingRuleEqualsTheChainItDeletes``' shape, one rule smaller:
        the expectation is the comparison the three retired sites wrote out,
        SPELLED HERE, so the two sides come from two places.  That matters
        because the arm a reader would reach for first cannot work --
        ``_searches.containing_index`` now DELEGATES to this method, so an
        assertion that the search and ``covers`` agree has one producer behind
        both sides and stays green through any mutation of the rule (measured:
        flipping the upper bound to ``<`` left exactly that arm passing).

        Walked from a fortnight below the opening payday to a fortnight past
        the horizon, over every shape the payday model can express, so the
        one-day period and the cadence-projected last end are both in range.
        """
        cal = calendar(paydays, cadence)
        first = min(day for _pid, day in paydays)
        last = max(period.end_date for period in cal.periods)

        day = first - timedelta(days=14)
        while day <= last + timedelta(days=14):
            for period in cal.periods:
                expected = period.start_date <= day <= period.end_date
                assert period.covers(day) is expected, (
                    f"{name}: period {period.period_index} "
                    f"({period.start_date}..{period.end_date}) answered "
                    f"{period.covers(day)} for {day}"
                )
            day += timedelta(days=1)


class TestTheAttributionClamp:
    """Pin the shared BUDGET-attribution rule (plan step **C4-a-2**).

    ``DerivedPeriod.attribution_day`` is the one rule the calendar's day cells,
    the balance seam's planned tier and the reconcile panel's offer bound all
    use, so no two of them can place one row on different days.  An item lands
    on its ``due_date`` (fallback: the period's ``start_date``), clamped into
    the period's inclusive span so a period's flows always sum by its
    ``end_date`` -- the calendar/grid reconciliation invariant.

    **It was ``utils.dates.attribution_date(preferred, start, end)`` and these
    cases came with it**, unchanged in what they assert and rewritten to ask
    the period rather than to pass its two dates.  That is the whole point of
    the move: the three-argument form let a caller pair one period's item with
    another period's bounds, which is not a crash but a row rendered on the
    wrong day.  The pairing is asserted directly below rather than left as
    prose, since the signature is now the only thing enforcing it.

    Asserted on the SPAN a calendar derives, never on a hand-built pair: the
    projected last period is included for exactly that reason -- its end comes
    from the cadence rather than from a following payday, and a consumer
    holding one cannot tell the difference.
    """

    def test_a_due_date_inside_the_period_is_the_landing_day_verbatim(self):
        """No clamp applies, so the item lands where it is dated."""
        period = calendar().periods[0]

        assert period.start_date == date(2026, 1, 2)
        assert period.end_date == date(2026, 1, 15)
        assert period.attribution_day(date(2026, 1, 6)) == date(2026, 1, 6)

    def test_no_due_date_falls_back_to_the_PAYDAY(self):
        """A row with no ``due_date`` is budgeted on the day money arrived."""
        period = calendar().periods[0]

        assert period.attribution_day(None) == date(2026, 1, 2)

    def test_a_due_date_BEFORE_the_period_clamps_up_to_the_payday(self):
        """A stray early date does not escape onto the previous paycheck.

        The recurrence engine can date a row just outside its own period's
        range; pulling it back is what keeps that period's running balance
        summing to the period-end figure the grid shows.
        """
        period = calendar().periods[1]

        assert period.start_date == date(2026, 1, 16)
        assert period.attribution_day(date(2026, 1, 9)) == date(2026, 1, 16)

    def test_a_due_date_AFTER_the_period_clamps_down_to_its_last_day(self):
        """The same rule at the other boundary, which is the load-bearing one.

        Every contributing item must fall on or before ``end_date`` or the
        period's daily sum stops equalling its period-end balance.
        """
        period = calendar().periods[1]

        assert period.end_date == date(2026, 1, 29)
        assert period.attribution_day(date(2026, 2, 4)) == date(2026, 1, 29)

    def test_both_boundary_days_are_THEMSELVES_valid_landing_days(self):
        """The clamp is inclusive at both ends, so neither boundary moves."""
        period = calendar().periods[1]

        assert period.attribution_day(date(2026, 1, 16)) == date(2026, 1, 16)
        assert period.attribution_day(date(2026, 1, 29)) == date(2026, 1, 29)

    def test_the_PROJECTED_last_period_clamps_to_its_projected_end(self):
        """A cadence-derived end bounds the clamp as a fact-derived one does.

        The one span whose end no payday dictates, and the one a caller reading
        ``pay_periods.end_date`` would have answered differently the moment the
        stored cadence moved (plan finding **P12**).
        """
        period = calendar().periods[-1]

        assert period.end_is_projected is True
        assert period.end_date == date(2026, 2, 26)
        assert period.attribution_day(date(2026, 3, 20)) == date(2026, 2, 26)

    def test_the_SPAN_it_clamps_against_is_THIS_periods_and_no_other(self):
        """The mis-pairing the deleted three-argument signature allowed.

        One date, asked of three adjacent periods of one calendar, answers
        three different days -- and each answer is inside the period that was
        asked.  A caller that could supply the bounds separately could produce
        the WRONG one of these with no error, which is why the rule moved onto
        the value that carries the span.
        """
        cal = calendar()
        stray = date(2026, 1, 20)

        assert cal.periods[0].attribution_day(stray) == date(2026, 1, 15)
        assert cal.periods[1].attribution_day(stray) == date(2026, 1, 20)
        assert cal.periods[2].attribution_day(stray) == date(2026, 1, 30)


class TestTheLabelIsTheDERIVEDSpan:
    """``DerivedPeriod.label`` names the span this value derives, not a column.

    Pay-calendar plan step **C4-a-5**, and the reason it is graded on an
    OFF-CADENCE shape: on a contiguous biweekly schedule ``lead(start) - 1``
    and ``start + cadence - 1`` agree, so a label asserted there passes against
    the stored-column reader this step deletes.  ``OFF_CADENCE``'s second
    period is the shape that tells them apart.

    The FORMAT itself is ``utils.dates.pay_period_label``'s and is graded in
    ``tests/test_utils/test_dates.py``; what is graded here is which two dates
    reach it.
    """

    def test_the_end_is_the_next_paydays_eve_not_the_cadence_projection(self):
        """The middle period of ``OFF_CADENCE``, where the two rules differ.

        Paydays 01-02, 01-16, 01-20 at a 14-day cadence.  The second period
        ends 01-19 -- the day before the NEXT payday -- where a cadence
        projection off its own start would say 01-29.  The label carries the
        first.
        """
        cal = calendar(paydays=OFF_CADENCE)

        assert cal.periods[1].label == "01/16 - 01/19"
        assert cal.periods[1].label != "01/16 - 01/29"

    def test_the_LAST_periods_label_follows_its_projection(self):
        """The one end that IS a projection, so the label moves with it.

        The last period has no RECORDED next payday, so its end is the day
        before the PROJECTED one and :attr:`end_is_projected` says so.
        Asserted because it is the end the STORED column disagreed with most
        often -- plan findings **P12** and **P28** both move this one and only
        this one.  *It read ``start + cadence - 1`` until plan step
        ``pay_calendar:C14-c`` made both ends one rule; the VALUE asserted
        below is unchanged, because a projected payday is displaced only once
        ``C14-e`` turns the convention on.*  Plan step ``C17-b-2`` then moved
        it: the era's grid from 01-02 pays 01-16 and 01-30, the 01-20 record
        is the 01-16 paycheck paid four days late, and the period runs to the
        day before 01-30 rather than one cadence past 01-20.
        """
        cal = calendar(paydays=OFF_CADENCE)

        assert cal.periods[-1].end_is_projected is True
        assert cal.periods[-1].label == "01/20 - 01/29"

    def test_a_period_straddling_a_year_carries_the_year(self):
        """The shared rule reaches this type too, not just the format string."""
        cal = calendar(
            paydays=((1, date(2026, 12, 26)), (2, date(2027, 1, 9))),
        )

        assert cal.periods[0].label == "12/26/26 - 01/08/27"


class TestTheRaisingTwinOfTheIdentityLookup:
    """``require_period`` REFUSES where ``period_by_id`` answers ``None``.

    Pay-calendar plan step **C4-a-1**.  The two methods answer the same
    question for two different callers, and only prose separates them: a
    caller holding an id a user typed or a nullable column holds gets ``None``,
    because "no such period of yours" is a real answer there; a caller holding
    a STORED row's ``pay_period_id`` -- NOT NULL, ``ON DELETE CASCADE`` -- gets
    a refusal, because for that caller ``None`` is never "not found" and
    answering it hands a money surface a decision with no basis.

    Graded HERE, on the value, rather than only through a caller.  It was
    reachable only through the cash fold's plan load when it shipped, so one of
    its two documented states -- a row filed in another owner's pay period --
    had no direct exercise at all; an adversarial review named that, and the
    tests below are the answer.
    """

    def test_it_answers_the_period_where_the_twin_does(self):
        """The precondition: on a period the calendar holds, the two agree."""
        cal = calendar()
        held = cal.periods[1].period_id

        assert cal.require_period(
            _filed(period_id=held),
        ) == cal.period_by_id(held)

    def test_a_period_this_calendar_does_not_hold_RAISES(self):
        """The twin answers ``None`` for the same id; this refuses.

        Both halves are asserted, because the refusal is only meaningful
        against a lookup that would otherwise have answered quietly.
        """
        cal = calendar()
        foreign = 9_999

        assert cal.period_by_id(foreign) is None
        with pytest.raises(RuntimeError):
            cal.require_period(_filed(period_id=foreign))

    def test_the_message_names_the_ROW_the_PERIOD_and_the_OWNER(self):
        """All four, because they are what identifies the broken pairing.

        A refusal naming only the period cannot tell an investigator WHICH row
        is filed against it, and one naming neither owner cannot distinguish
        the two states the method documents -- a cross-owner filing from two
        reads taken at different moments.  **The TABLE joined them at plan step
        C4-a-5**: ``budget.transactions``, ``budget.transfers`` and
        ``budget.journal_entries`` all carry a ``pay_period_id`` and their id
        spaces overlap, so "id=77" alone sends a reader to whichever row of
        that number they happen to look at.
        """
        cal = calendar(user_id=42)

        with pytest.raises(RuntimeError) as raised:
            cal.require_period(_filed(period_id=9_999))

        message = str(raised.value)
        assert "budget.transactions id=77 " in message
        assert "pay period id=9999," in message
        assert "user 42's pay calendar" in message

    def test_the_message_names_the_TABLE_the_row_came_from(self):
        """A TRANSFER's refusal says transfer, which is C4-a-5's own reason.

        The message was ``f"transaction id={...}"`` outright until that step
        gave the recurrence conflict chooser -- which builds rows for
        transactions AND transfers -- its first non-transaction caller.  A
        transfer described as a transaction sends an investigator to
        ``budget.transactions`` id=77, a DIFFERENT row that very likely exists.
        """
        cal = calendar(user_id=42)

        with pytest.raises(RuntimeError) as raised:
            cal.require_period(
                _filed(table="budget.transfers", period_id=9_999),
            )

        assert "budget.transfers id=77 " in str(raised.value)

    def test_it_refuses_ANOTHER_owners_period_by_the_same_rule(self):
        """The second documented state, which had no direct exercise.

        ``budget.transactions`` carries no ``user_id`` -- its owner IS its pay
        period's, and nothing in the schema requires that owner to be its
        ACCOUNT's -- so a row can name a period this owner legitimately does
        not hold.  Two calendars over DISJOINT payday sets is that state on the
        value: each answers for its own ids and refuses the other's.
        """
        mine = calendar(paydays=((1, date(2026, 1, 2)), (2, date(2026, 1, 16))))
        theirs = calendar(
            paydays=((81, date(2026, 1, 9)), (82, date(2026, 1, 23))),
            user_id=2,
        )

        assert mine.require_period(_filed(period_id=1)).period_id == 1
        assert theirs.require_period(
            _filed(row_id=78, period_id=81),
        ).period_id == 81
        with pytest.raises(RuntimeError):
            mine.require_period(_filed(row_id=78, period_id=81))
        with pytest.raises(RuntimeError):
            theirs.require_period(_filed(period_id=1))


class TestTheFiledRowCannotBeMispaired:
    """``FiledRow`` is the pair ``require_period`` used to take loose.

    Pay-calendar plan step **C4-a-5**.  The signature was
    ``require_period(period_id, transaction_id)`` -- two ``int``s in a fixed
    order, held to one row by nothing.  A crossed pair does not raise: it finds
    a DIFFERENT period, and ``balance_at._cash_plan._cash_plan`` dates the
    daily balance line with the answer.  These grade the two properties that
    replace the old signature's inspection: it cannot be built positionally,
    and the constructor every entity-holding caller uses reads both ids off ONE
    object.
    """

    def test_two_bare_integers_do_not_construct_one(self):
        """``FiledRow(1, 2)`` is a ``TypeError``, not a filed row.

        The keyword-only fields ARE the guarantee.  Without them the value
        would be a rename of the defect: a caller could still write the two
        ids in the wrong order and get an object back.
        """
        with pytest.raises(TypeError):
            FiledRow("budget.transactions", 1, 2)  # pylint: disable=too-many-function-args  # noqa: E501  # the refusal under test

    def test_for_row_reads_both_ids_off_ONE_object(self):
        """The constructor a caller holding a mapped row uses.

        It takes the row, so there is no second object whose id could be
        substituted -- which is the whole difference from the two-argument
        call it replaces.  Driven with a stand-in carrying the three attributes
        a mapped row has, because this value is in the model-free half of the
        package and the property under test is that it reads rather than
        imports.
        """
        class _Row:  # pylint: disable=too-few-public-methods  # a stand-in row
            __table__ = SimpleNamespace(fullname="budget.transfers")
            id = 501
            pay_period_id = 88

        filed = FiledRow.for_row(_Row())

        assert filed == FiledRow(
            table="budget.transfers", row_id=501, period_id=88,
        )

    def test_it_is_frozen_so_a_holder_cannot_re_point_it(self):
        """One value, one row: rebinding a field would restore the crossing."""
        filed = FiledRow(
            table="budget.transactions", row_id=1, period_id=2,
        )

        with pytest.raises(FrozenInstanceError):
            filed.period_id = 3
