"""Extend continues the owner's payday rhythm: the appended paydays stay on the nominal grid
whatever a business-day convention displaces (C14-d), and the grid is stepped from the
schedule's stored phase, not its last recorded cash day (C14-e-2).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.enums import BusinessDayShiftEnum
from app.models.pay_period import PayPeriod
from app.services import pay_era_write, pay_period_admin, pay_period_write, pay_schedule_service
from app.services import pay_calendar
from app.services.pay_calendar import PayCalendarError, calendar_for, schedule_for
from tests._test_helpers import record_paydays_across_a_hole, rhythm_of


#: The last recorded payday :class:`TestTheExtendAnchorIsTheNOMINALGrid` builds
#: from, and the closed day its rhythm walks into.
#:
#: 2030-11-14 is an ordinary Thursday and sits on the grid; one cadence later
#: is 2030-11-28, the fourth Thursday of November and therefore Thanksgiving.
#: So the owner's next NOMINAL payday is a day no money moves on, which is the
#: only shape in which this door's two candidate answers differ at all.
_ON_GRID_PAYDAY = date(2030, 11, 14)


_CLOSED_NEXT_PAYDAY = date(2030, 11, 28)


class TestTheExtendAnchorIsTheNOMINALGrid:
    """Plan step **C14-d**: extend continues the rhythm, it does not read the cash day.

    ``extend_pay_periods`` asked
    :meth:`~app.services.pay_calendar.PayCalendar.span_containing` for the day
    past the horizon and handed it to
    :func:`~app.services.pay_period_write.record_paydays`, which spaces the
    whole batch by flat cadence arithmetic from whatever day it is given.  That
    is one answer while nothing can move a payday.  From ``C14-e`` the
    calendar's answer is the CASH day -- the nominal day displaced onto a
    business day -- so the batch would carry the displacement on every payday
    in it AND into the anchor the next extend reads.  **R-PC54** calls that
    feeding a cash date back into the rhythm.

    **These cases STATE a displacing convention on the owner's schedule row**,
    which since plan step ``C14-e-3`` is all it takes: the shipped producer
    reads it.  They drove a substituted producer until then, because with the
    convention off the grid and the projection agree on every day and the door
    could not be shown reading the right one.  The double is DELETED -- a
    stand-in for a shipped producer is a fence whose subject has landed.

    **Since plan step ``C17-c-2b`` the door records the plan's own paydays**
    (``pay_period_write.continue_paydays``, reading
    :func:`~app.services.pay_calendar.planned_paydays_after`), which are
    CASH days -- the era's nominal grid, anchored on the era's own phase,
    displaced.  What these cases grade from here is the same property by
    a different mechanism: the plan steps from the ERA's phase and matches
    the record to it rather than stepping from the record, so the recorded
    cash days below are whole cadences from the phase and the next extend
    cannot inherit a displacement.
    """

    def _last_payday_before_a_holiday(
        self, db_session, user_id, shift=BusinessDayShiftEnum.NONE,
    ):
        """Record 2030-11-14 alone under *shift*; the next nominal day is Thanksgiving.

        The convention is STORED and not merely simulated, which an adversarial
        review of this step required: ``extend_pay_periods`` builds its rhythm
        from the owner's stored row, and the producer ``C14-e`` ships reads
        that same row.  A case that displaced globally while the row said
        ``none`` would pin a world the shipped step cannot reproduce.
        *It read the convention through* ``pay_schedule_service.resolve_shift``
        *until plan step* ``C14-e-1``, *which put the pair on the*
        :class:`~app.services.pay_calendar.PayCalendar` *the door already
        builds and deleted that function with its duplicate query.*
        The recorded row is 2030-11-14 under every convention, because that day
        is an ordinary Thursday and so is its own displacement -- which is what
        keeps this one fixture now that ``requested_paydays`` DISPLACES what
        it records (``C14-e-3``).
        """
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=_ON_GRID_PAYDAY,
            num_periods=1, rhythm=rhythm_of(14, shift),
        )
        db_session.commit()

    def test_the_appended_paydays_stay_on_the_grid(
        self, app, db, bare_user,
    ):
        """Three appended paydays, hand-computed from the grid, under ``prior``.

        2030-11-14 + 14, + 28, + 42.  The calendar would have answered
        2030-11-27 for the first of them, which the case asserts directly
        rather than trusting the description: an anchor a day earlier makes all
        THREE wrong, and the next extend would read the third as its own
        anchor, so the error is permanent rather than confined to the batch.

        **``C14-e-3`` moved exactly ONE of them** -- the batch runs on the
        nominal grid and each element is RECORDED displaced (ledger row
        **PC-497** fault 1), so the 2030-11-28 grid day becomes the 2030-11-27
        payroll really pays.  Only the first, because 2030-12-12 and 2030-12-26
        are ordinary Thursdays and displace to themselves.  *This paragraph was
        written at ``C14-d`` PREDICTING these three days, which is the shape
        CLAUDE.md rule 5 asks for: a failing test means the code is wrong
        unless the developer has confirmed the behaviour changed, and the
        confirmation was recorded a step before the change.*
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.PRIOR,
            )
            calendar = calendar_for(user_id)
            cash_day = calendar.span_containing(
                calendar.horizon() + timedelta(days=1),
            ).start_date
            assert cash_day == date(2030, 11, 27), (
                "the door's OLD expression must answer a different day here, "
                "or this case grades the two spellings agreeing"
            )

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert [period.start_date for period in new_periods] == [
                date(2030, 11, 27), date(2030, 12, 12), date(2030, 12, 26),
            ]

    def test_the_batch_does_not_inherit_a_displacement(
        self, app, db, bare_user,
    ):
        """Every appended payday is a whole number of cadences off the anchor.

        The property rather than the three dates: a batch anchored on a cash
        day is still evenly spaced, so spacing alone proves nothing -- what
        this asserts is that the spacing runs from the RECORDED payday the
        owner already had, which is what makes the rhythm reproducible from the
        table.  A cash anchor fails it by exactly the displacement.

        **It opens by checking that the substitution is LIVE**, because on the
        nominal path the deleted expression produces these same offsets: all of
        this case's discriminating power rests on the projection having moved,
        and an adversarial review of this step found it the one new case that
        would go quiet rather than red if the patch ever stopped reaching the
        producer.

        **The FIRST offset is 13 since ``C14-e-3``** -- the writer records each
        element displaced (ledger row **PC-497** fault 1) and the 2030-11-28
        grid day is paid 2030-11-27.  Every LATER offset is a whole cadence,
        which is the assertion: ``[13, 27, 41, 55, 69, 83]`` -- what a first
        draft of this paragraph predicted -- is what a batch ANCHORED on the
        cash day gives, the whole progression shifted, and the defect this case
        exists to refuse.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.PRIOR,
            )
            assert pay_calendar.projected_payday(
                _ON_GRID_PAYDAY, rhythm_of(14, BusinessDayShiftEnum.PRIOR), 1,
            ) == date(2030, 11, 27), (
                "the projection must have MOVED here, or these offsets are the "
                "nominal path and grade nothing"
            )

            new_periods = pay_period_admin.extend_pay_periods(user_id, 6)
            db.session.commit()

            offsets = [
                (period.start_date - _ON_GRID_PAYDAY).days
                for period in new_periods
            ]
            assert offsets == [13, 28, 42, 56, 70, 84]

    def test_a_SECOND_extend_reads_an_anchor_the_first_left_on_the_grid(
        self, app, db, bare_user,
    ):
        """"Permanently" is a claim about the NEXT extend, so it takes two.

        Each extend anchors on the last recorded payday, so a batch that ended
        off the grid hands the next one an off-grid anchor and the error never
        washes out.  Every other case here runs ONE extend and therefore grades
        the batch rather than the compounding; an adversarial review of this
        step named that gap.  Two extends of three, and the sixth appended
        payday is still a whole number of cadences from the payday the owner
        started with.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.PRIOR,
            )
            pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            second = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert [
                (period.start_date - _ON_GRID_PAYDAY).days for period in second
            ] == [56, 70, 84]

    def test_a_FORWARD_convention_is_ACCEPTED_and_that_CLOSES_PC_497(
        self, app, db, bare_user,
    ):
        """The pin ``C14-d`` left, INVERTED by ``C14-e-3``.

        **It was a refusal, and the refusal was the finding.**  Until this step
        ``record_paydays`` recorded the days it was handed and spaced them
        nominally, so under a displacing convention it wrote NOMINAL paydays
        where the projection showed CASH ones.  Under ``next`` the derivation
        runs the 2030-11-14 paycheck through 2030-11-28 -- payroll pays
        2030-11-29 -- so the floor ``C14-d`` corrected refused an extend
        offering the nominal 2030-11-28, correctly: a payday there really would
        split the paycheck the calendar derives.  The case asserted that
        refusal, and said in its own docstring that what would make it fail is
        ``C14-e`` landing without the writer's half.

        **The remedy it named is what shipped**: ``requested_paydays`` runs
        the progression on the grid and records each element DISPLACED, which
        lands 2030-11-29 -- exactly the floor -- and is accepted.  So this
        asserts the ACCEPTANCE and the three recorded days, and the ``$0.00``
        control below is what keeps it from grading the nominal path.

        **Where the refusal landed while it stood**: ``top_up_rolling_window``
        reaches this door from ``/grid`` and ``/dashboard`` with no handler,
        and ``app/error_handlers.py`` registers none for ``ValidationError``,
        so it was a 500 on both -- ledger row **N-494**'s shape through a
        second trigger, and the reason ledger row **PC-497** fault 1 was worth
        a pin rather than a note.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(
                db.session, user_id, BusinessDayShiftEnum.NEXT,
            )
            # The floor the old spelling was refused BY, asserted before the
            # door runs: without this the acceptance below could be a floor
            # that moved rather than a writer that displaced.
            assert pay_calendar.projected_payday(
                _ON_GRID_PAYDAY, rhythm_of(14, BusinessDayShiftEnum.NEXT), 1,
            ) == date(2030, 11, 29)

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert [period.start_date for period in new_periods] == [
                date(2030, 11, 29), date(2030, 12, 12), date(2030, 12, 26),
            ]

    def test_it_is_ZERO_DOLLARS_while_the_convention_displaces_nothing(
        self, app, db, bare_user,
    ):
        """The ``none`` path: the two producers agree, so nothing moves.

        The control for every case above, and the step's own ``$0.00`` claim --
        which since ``C14-e-3`` is a claim about an owner who has not answered
        the convention question rather than about an unwired producer.  Under
        ``none`` the displacement is the identity, so the door appends the
        NOMINAL grid days, Thanksgiving included.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._last_payday_before_a_holiday(db.session, user_id)

            calendar = calendar_for(user_id)
            assert calendar.span_containing(
                calendar.horizon() + timedelta(days=1),
            ).start_date == _CLOSED_NEXT_PAYDAY

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            assert [period.start_date for period in new_periods] == [
                _CLOSED_NEXT_PAYDAY, date(2030, 12, 12), date(2030, 12, 26),
            ]


class TestTheGridIsSteppedFromTheSTOREDPHASE:
    """Plan step **C14-e-2**: extend continues the SCHEDULE's grid, not its own output.

    ``extend_pay_periods`` stepped one cadence from the owner's LAST RECORDED
    payday.  That is exact only while every recorded payday sits on the
    arithmetic grid, which is true while no convention displaces one.  From
    ``C14-e-3`` the writer records the DISPLACED day, so the anchor becomes a
    cash date and each batch re-phases the rhythm by that displacement --
    permanently, because the next extend reads THIS batch's last cash day.
    Measured over production's cadence and opening payday at a batch of one,
    the rolling top-up's steady state: **178 of 301** recorded paydays wrong
    under ``prior`` with **8 days** of final drift, against **0 of 301**
    anchored on the stored phase (ledger row **PC-497** fault 2, developer
    direction **R-PC61**) -- which since plan step ``pay_calendar:C17-a`` is
    the latest ERA's ``effective_from``.

    These cases state the convention on the era -- ``C14-e-3`` ships the
    producer that reads it -- and hand the owner a displaced recorded payday
    BY HAND while leaving the era's phase where it was.  No door produces
    that pairing: a batch that mints an era writes its own ``first_payday``
    as the era's day, so a record that has advanced past it is the PIECEWISE
    owner ledger row **N-492** describes, and building it directly is what
    ``pay_period_write``'s own module docstring reserves the suite the right to
    do.  It is the state the plan must survive: since plan step ``C17-c-2b``
    the record is MATCHED to the era's grid
    (:func:`~app.services.pay_calendar._eras.matched_step`) and the plan
    continues from the matched step, so a phase that does not place the
    record on its own grid is ordinary rather than a refusal.
    """

    def _owner_anchored_at(self, db_session, user_id, shift):
        """Record 2030-11-14 alone, which stores it as the owner's grid phase."""
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=_ON_GRID_PAYDAY,
            num_periods=1, rhythm=rhythm_of(14, shift),
        )
        db_session.commit()

    def test_the_writer_co_writes_the_phase_with_the_cadence(
        self, app, db, bare_user,
    ):
        """The batch's own first payday IS the era's day, and one row holds both.

        Everything below rests on this: the phase is DERIVED from the batch
        rather than accepted from a door, so a phase that is not on the batch's
        grid is unrepresentable rather than refused.  It is also what keeps the
        phase meaningful when the CADENCE changes -- an era is one row carrying
        both, which is what ``budget.pay_schedule``'s two columns could not
        state per era (ledger row **N-492**, ruling **R-PC58**, plan step
        ``C17-a``).
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.NONE)

            facts = pay_schedule_service.resolve_schedule(user_id)

            assert facts.latest_era.effective_from == _ON_GRID_PAYDAY
            assert facts.rhythm.cadence.days == 14

    def test_a_DISPLACED_recorded_payday_does_not_move_the_grid(
        self, app, db, bare_user,
    ):
        """The batch continues the stored grid, not the cash day it was handed.

        2030-11-14 is the stored phase.  The owner's next nominal payday,
        2030-11-28, is Thanksgiving, so under ``prior`` payroll pays 11-27 and
        ``C14-e-3``'s writer records that.  Anchored on the RECORD the next
        payday would be ``11-27 + 14 = 2030-12-11``, which is off the grid and
        would take every later payday with it.  Anchored on the stored phase it
        is 2030-12-12 -- an ordinary Thursday, and the day payroll really pays.

        **The case is the discriminator rather than an example**: the two
        answers differ by exactly the displacement, and 12-11 against 12-12 is
        what the whole column buys.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.PRIOR)
            # The displaced cash day this step's writer records, added
            # directly so the STORED PHASE stays at 2030-11-14 -- a door would
            # advance it to this batch's own first payday, and the anchor
            # standing still while the record moves is the discriminator.
            db.session.add(
                PayPeriod(user_id=user_id, start_date=date(2030, 11, 27)),
            )
            db.session.commit()

            new_periods = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()

            assert [p.start_date for p in new_periods] == [
                date(2030, 12, 12), date(2030, 12, 26),
            ]

    def test_it_skips_a_grid_INDEX_the_owner_already_holds(
        self, app, db, bare_user,
    ):
        """Which index is next is a CASH question, and the search asks it.

        The estimate is the last grid day at or before the last recorded
        payday: from the 2030-11-14 phase, the recorded cash day 2030-11-27
        gives step 0 (11-14 is 13 days below it).  Steps 0 and 1 are both
        already paid -- 11-14 as itself and 11-28 as the 11-27 the owner holds
        -- so the answer is step 2, and a search that stopped at the estimate
        or at estimate + 1 would offer a payday the owner already has.

        Asserted as the STEP COUNT rather than as the date, because the date is
        the case above; what this pins is that the loop advanced twice, which
        is the arm ``C14-e-3`` makes reachable.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.PRIOR)
            db.session.add(
                PayPeriod(user_id=user_id, start_date=date(2030, 11, 27)),
            )
            db.session.commit()

            new_periods = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert (
                new_periods[0].start_date - _ON_GRID_PAYDAY
            ).days == 28, "the search must advance TWO steps past the estimate"

    def test_a_schedule_that_states_no_era_is_REFUSED(
        self, app, db, bare_user,
    ):
        """An owner holding paydays and no era is refused, not phased by guess.

        The migration backfills an era for every owner holding a payday and
        ``record_paydays`` mints one when none covers its batch, so no door
        produces this owner.  Built here by retiring the era underneath a
        schedule that HAS paydays: the point is that the door refuses rather
        than inventing a phase, because an invented phase generates wrong
        paydays silently where a refusal is read.  Since plan step ``C17-a``
        the refusal is the CALENDAR's -- an owner with no era has no rhythm
        to derive a calendar from, exactly as one with no schedule row -- and
        it reaches the "Pay Calendar Unavailable" page rather than the card's
        flash.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.NONE)
            pay_era_write.retire_eras(user_id, ())
            db.session.commit()

            with pytest.raises(PayCalendarError, match="has no pay calendar"):
                pay_period_admin.extend_pay_periods(user_id, 1)

    def test_a_PIECEWISE_owner_whose_tail_was_truncated_can_still_extend(
        self, app, db, bare_user,
    ):
        """The state an adversarial review of this step built, through real doors.

        A stored phase describes the grid of the batch that WROTE it, which is
        not always the grid the owner's surviving last payday sits on.  Three
        shipped doors reach that state together: record at one cadence, record
        again at another (*correct my cadence going forward*, which
        ``record_paydays`` permits and ledger row **N-492** records), then
        truncate back across the change so the surviving latest payday belongs
        to the FIRST era while the stored phase belongs to the second.

        Asked against the last recorded PAYDAY the door answered 2030-01-18,
        which falls inside the paycheck the owner still holds and which
        ``reject_backward_payday`` refuses -- permanently, and on a read path
        with no handler, because ``top_up_rolling_window`` reaches this door
        from ``/grid`` and ``/dashboard``.  Asked against the paycheck's END,
        which is the floor's own subject, it answered a day past the floor and
        the write was accepted.  **The case is the discriminator**: the two
        answers differ and only one of them is a day this app can record.

        **Which end, and which day, was plan step ``C17-b-2``'s (ruling
        R-PC72), and the day is ``C17-c-2b``'s (rulings R-PC75, R-PC78;
        ledger row PC-509 closed).**  The surviving last payday 2030-01-17 is
        the 14-day era's, so the calendar closes its paycheck on that era's
        grid -- 01-30, the day before 01-31 -- and the floor is 01-31.  Until
        ``C17-c-2b`` this door restated the LATEST era from the horizon: the
        7-day grid through 02-22 has 01-25 and 02-01 near it, 01-25 is below
        the floor, so it recorded 02-01, minted a third era there and retired
        the 02-22 one -- the calendar projected 01-31 and the door recorded
        02-01, a one-day disagreement this case pinned both sides of.  The
        door records the plan now: 01-31, the 14-day era's own next payday,
        and the two eras the owner stated stand untouched.  *Until
        ``C17-b-2`` the horizon stepped one 7-day cadence from the recorded
        01-17, the floor was 01-24 and the day recorded 01-25.*
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 1, 3),
                num_periods=2, rhythm=rhythm_of(14),
            )
            # A second era, deliberately OFF the first grid: 2030-02-22 is 36
            # days after 2030-01-17, and 36 is not a multiple of 7.
            record_paydays_across_a_hole(
                user_id=user_id, first_payday=date(2030, 2, 22),
                num_periods=2, rhythm=rhythm_of(7),
            )
            db.session.commit()
            keep = next(
                period for period in calendar_for(user_id).saved()
                if period.start_date == date(2030, 1, 17)
            )
            pay_period_admin.truncate_pay_periods(user_id, keep.period_id)
            db.session.commit()
            # The calendar's own answer for the surviving 14-day paycheck.
            calendar = calendar_for(user_id)
            assert calendar.periods[-1].end_date == date(2030, 1, 30)
            assert calendar.span_containing(date(2030, 1, 31)).start_date == (
                date(2030, 1, 31)
            )

            new_periods = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert [p.start_date for p in new_periods] == [date(2030, 1, 31)]
            assert [
                (era.effective_from, era.rhythm.cadence.days)
                for era in schedule_for(user_id).eras
            ] == [(date(2030, 1, 3), 14), (date(2030, 2, 22), 7)]

    def test_it_is_ZERO_DOLLARS_while_nothing_displaces(
        self, app, db, bare_user,
    ):
        """The shipped path: the stored phase and the record agree exactly.

        This step's own ``$0.00`` claim, and it is structural rather than a
        fact about stored data: with no displacement live every recorded payday
        IS a grid day, so stepping from the phase and stepping from the record
        name the same day.  Two extends, because the drift the column deletes
        only shows across batches.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._owner_anchored_at(db.session, user_id, BusinessDayShiftEnum.NONE)

            first = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()
            second = pay_period_admin.extend_pay_periods(user_id, 2)
            db.session.commit()

            assert [
                (p.start_date - _ON_GRID_PAYDAY).days for p in first + second
            ] == [14, 28, 42, 56]
