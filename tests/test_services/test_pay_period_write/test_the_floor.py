"""The forward-only FLOOR (R-PC1's structural half): no payday between two existing ones, the floor
where the last paycheck ends (C14-d), read under the STORED convention and not the batch's, and
on the era's grid (C17-b-2).
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.exceptions import ValidationError
from app.enums import BusinessDayShiftEnum
from app.services import pay_calendar, pay_period_admin, pay_period_write, pay_schedule_service
from app.services.pay_calendar import calendar_for
from tests._test_helpers import rhythm_of, all_periods
from app.services.pay_rhythm import FixedDays

from ._helpers import _paydays


class TestTheForwardOnlyFloor:
    """Ruling **R-PC1**'s structural half: no payday between two existing ones.

    It replaced ``_reject_overlapping_batch``, which bounded a batch on
    ``max(end_date)`` -- a column plan step C4-c dropped -- and which was doing
    this job by accident.  **The only thing left to refuse is a payday landing
    inside a paycheck the owner already has**, because a gap and an overlap
    stopped being expressible once the columns are derived.  Plan step C6 owns
    that insert, behind ledger row **P10**'s two unruled questions, and C6 is
    what deletes this rule.

    **The floor is ONE CADENCE, not two days, and this class carries the
    control for the correction.**  C3-b's first cut bounded at
    ``latest_payday + 2`` (the writer's cadence floor, itself deleted at plan
    step C4-c), which is a paycheck too low: the LAST period runs to ``latest_payday + cadence - 1``, so every
    day between the two split it -- reachable, and P10 says it is not.
    """

    def _two_fortnightly_periods(self, user_id):
        """Record 2026-01-02 and 2026-01-16; coverage runs to 2026-01-29."""
        return pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2026, 1, 2),
            num_periods=2, rhythm=rhythm_of(14),
        )

    def test_a_payday_between_two_existing_ones_is_refused(
        self, app, db, bare_user,
    ):
        """2026-01-09 splits the first paycheck in half; refused, nothing written."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="Choose 2026-01-30 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 9),
                    num_periods=4, rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 2

    def test_the_floor_is_one_CADENCE_after_the_latest_payday(
        self, app, db, bare_user,
    ):
        """The day before the next cadence payday refuses; that day is accepted.

        Paydays 2026-01-02 and 2026-01-16 at cadence 14: the last paycheck runs
        to 2026-01-29, so the floor is 2026-01-30.  2026-01-29 is refused --
        it would split that paycheck -- and 2026-01-30 goes through, which is
        the schedule simply continuing.

        The accepted case is the control: without it a rule that refused
        everything would pass the first half.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="Choose 2026-01-30 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 29),
                    num_periods=1, rhythm=rhythm_of(14),
                )
            db.session.rollback()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
                (date(2026, 1, 30), date(2026, 2, 12), 2),
            ]

    def test_a_payday_inside_the_last_periods_span_is_REFUSED(
        self, app, db, bare_user,
    ):
        """The regression C3-b's first cut shipped, and its control.

        With a floor of ``latest_payday + 2`` this was ACCEPTED, and an
        adversarial review measured what it did: recording 2026-01-23 shrank
        the 2026-01-16 paycheck from 01-29 to 01-22, moved a row due 01-25 from
        rendering on 01-25 to rendering on 01-22, and split one paycheck in two
        -- left EMPTY by ``/pay-periods/generate`` (P10's "income understated")
        and repopulated by ``regenerate`` beside the row the shrunk half kept
        (P10's "a monthly billed twice").  Ledger row **P10** says that state is
        not constructible and plan step **C6** owns making it so.

        The submitted cadence is 7 deliberately: the floor reads the cadence the
        owner's last paycheck ALREADY runs at, not the one this batch proposes,
        so a shorter one cannot buy its way in.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="Choose 2026-01-30 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 23),
                    num_periods=2, rhythm=rhythm_of(7),
                )
            db.session.rollback()

            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
            ]

    def test_the_cadence_may_still_be_SHORTENED_going_forward(
        self, app, db, bare_user,
    ):
        """What the floor costs, and what it does not.

        An owner moving from fortnightly to weekly records their next real
        payday one FORTNIGHT out -- the paycheck they are in has not ended --
        and the schedule then continues at a week.  So the "correct my cadence"
        case the implementation plan's section 6 names survives; what is refused
        is only doing it retroactively, inside a paycheck that already exists.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=3, rhythm=rhythm_of(7),
            )
            db.session.commit()

            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
                (date(2026, 1, 30), date(2026, 2, 5), 2),
                (date(2026, 2, 6), date(2026, 2, 12), 3),
                (date(2026, 2, 13), date(2026, 2, 19), 4),
            ]
            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(7)

    def test_a_first_batch_has_no_floor(self, app, db, bare_user):
        """An owner with no paydays can open a schedule on any day."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=date(2020, 3, 1),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert periods[0].start_date == date(2020, 3, 1)


#: What the floor becomes for each convention, on the schedule
#: :class:`TestTheFloorFollowsTheProducer` records.
#:
#: The last recorded payday is 2025-12-18, an ordinary Thursday, and the
#: nominal next one is 2026-01-01 -- New Year's Day, which is closed.  So the
#: three conventions put the next paycheck's opening day in three different
#: places, and the floor is that day in each: **the R-PC47 case, worked**.
#: Hand-computed from the federal holiday set, not read back from
#: :func:`~app.utils.business_days.shift_to_business_day`, so this table is an
#: independent oracle rather than the mechanism restated.
#:
#: The third column is the NOMINAL day a batch must state to land its payday
#: one day BELOW the floor, and it is not ``floor - 1`` on every arm because
#: since plan step ``C14-e-3`` the writer records the day DISPLACED.  Under
#: ``next`` no nominal day at all lands on 2026-01-01 -- New Year's Day is
#: closed, so every grid day near it is paid 2026-01-02 or later -- and the
#: highest payday below that floor is 2025-12-31.  Stating ``floor - 1`` there
#: would ask for a payday of 2026-01-02, which IS the floor, and the arm would
#: grade an acceptance while claiming to grade a refusal.
_FLOOR_PER_CONVENTION = [
    (BusinessDayShiftEnum.NONE, date(2026, 1, 1), date(2025, 12, 31)),
    (BusinessDayShiftEnum.PRIOR, date(2025, 12, 31), date(2025, 12, 30)),
    (BusinessDayShiftEnum.NEXT, date(2026, 1, 2), date(2025, 12, 31)),
]


class TestTheFloorFollowsTheProducer:
    """Plan step **C14-d**: the floor is where the last paycheck ENDS.

    ``reject_backward_payday`` open-coded ``latest_payday + cadence_days`` and
    a sentence in its own docstring held that equal to the last saved period's
    derived end -- rule 14's tell, one value with two homes and a maintenance
    contract.  It calls
    :func:`~app.services.pay_calendar.projected_payday` now, the same producer
    :func:`~app.services.pay_calendar.derive_periods` closes that period with,
    so the fence and the boundary it guards cannot come apart.

    **These cases STATE a displacing convention on the owner's schedule row**
    and drive the shipped producer, which is plan step ``C14-e-3``.  They ran
    against a substituted producer until then, because with the convention at
    ``none`` the two spellings agree on every day and a test of the real one
    would have graded the deleted one just as well -- not hypothetical, since
    an adversarial review of ``C14-c`` measured its sibling equality passing
    with the OLD end rule restored.  The double is DELETED with this step.

    The ``none`` row of :data:`_FLOOR_PER_CONVENTION` is now a real ``$0.00``
    control as well as the within-table one: it fixes the floor the displacing
    rows are read against, AND it runs the same shipped producer they do, so
    the three arms differ only in the convention the schedule row states.
    *That second half was not true until this step, and an adversarial review
    of ``C14-d`` struck a sentence claiming it was -- ``none`` substituted an
    identity DOUBLE, so the shipped producer was not called on that arm
    either.*
    """

    def _fortnightly_through_december(self, user_id, shift):
        """Record 2025-12-04 and 2025-12-18 UNDER *shift*; the second is last.

        **The convention is stored, and an adversarial review of ``C14-d`` is
        why.**  A first form seeded every arm under ``none`` and argued that a
        payday is a RECORDED FACT (**R-PC47**) so the rows are the same either
        way.  What differed was the SCHEDULE ROW: it said ``none`` while the
        case simulated a displacing convention, and the shipped producer reads
        that row -- so the arm pinned a world ``C14-e`` could not reproduce for
        this owner.

        The two recorded days ARE the same under all three conventions, and
        since ``C14-e-3`` that is a fact about these two DAYS rather than about
        the writer: 2025-12-04 and 2025-12-18 are ordinary Thursdays, so each
        is its own displacement.  ``requested_paydays`` records the DISPLACED
        day now (ledger row **PC-497** fault 1), and a fixture built on a
        closed day would record something else.
        """
        return pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2025, 12, 4),
            num_periods=2, rhythm=rhythm_of(14, shift),
        )

    @pytest.mark.parametrize(
        ("shift", "floor", "below"), _FLOOR_PER_CONVENTION,
        ids=lambda value: getattr(value, "name", str(value)).lower(),
    )
    def test_the_day_below_the_floor_is_refused_and_the_floor_is_taken(
        self, app, db, bare_user, shift, floor, below,
    ):
        """Both sides of the boundary, wherever the convention puts it.

        Asserting the refusal alone would pass for a rule that refused
        everything, and asserting the acceptance alone for one that refused
        nothing; the pair pins the floor to a day.  Across the three arms that
        day MOVES IN BOTH DIRECTIONS -- ``prior`` pulls it back to 2025-12-31
        and ``next`` pushes it out to 2026-01-02 -- so a floor that ignored the
        producer and kept answering 2026-01-01 fails two of the three.

        *below* is a NOMINAL day and the refusal is about the day it is PAID
        on, which is the distinction ``C14-e-3`` introduced; the assertion
        below states that payday so no arm can refuse for the wrong reason.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._fortnightly_through_december(user_id, shift)
            db.session.commit()
            # *below* is the LAST nominal day whose PAYDAY is under the
            # floor, stated as the pair that says exactly that: its own payday
            # falls short, and the next grid day's does not.  Under ``next``
            # that payday is 2025-12-31 rather than ``floor - 1``, because no
            # nominal day is paid on the closed 2026-01-01 -- which is why the
            # column is in the table rather than computed here.
            displacing = rhythm_of(14, shift)
            assert pay_calendar.projected_payday(below, displacing, 0) < floor
            assert pay_calendar.projected_payday(
                below + timedelta(days=1), displacing, 0,
            ) >= floor, (
                "the refused arm must ask for the LAST payday under the "
                "floor, or it grades a day the floor was never near"
            )

            with pytest.raises(
                ValidationError, match=f"Choose {floor.isoformat()} or later",
            ):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=below,
                    num_periods=1, rhythm=rhythm_of(14, shift),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 2

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=floor,
                num_periods=1, rhythm=rhythm_of(14, shift),
            )
            db.session.commit()
            assert [period.start_date for period in all_periods(user_id)] == [
                date(2025, 12, 4), date(2025, 12, 18), floor,
            ]

    def test_the_accepted_payday_is_the_one_the_LAST_PAYCHECK_ends_before(
        self, app, db, bare_user,
    ):
        """The floor and the derived end are ONE value, shown as a calendar.

        Under ``prior`` the 2025-12-18 paycheck runs to 2025-12-30, because the
        payday that closes it is the 2026-01-01 payroll really pays on
        2025-12-31.  The floor accepts exactly 2025-12-31, so the recorded
        payday opens the day after that end and the calendar tiles -- which is
        the property the open-coded floor could only promise.  Its own
        ``latest + cadence`` would have refused this write outright, leaving
        the owner unable to record a payday they were really paid.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._fortnightly_through_december(
                user_id, BusinessDayShiftEnum.PRIOR,
            )
            db.session.commit()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2025, 12, 31),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
            )
            db.session.commit()

            assert _paydays(user_id) == [
                (date(2025, 12, 4), date(2025, 12, 17), 0),
                (date(2025, 12, 18), date(2025, 12, 30), 1),
                (date(2025, 12, 31), date(2026, 1, 13), 2),
            ]

    def test_the_floor_follows_a_RETIRED_tail_down(self, app, db, bare_user):
        """The floor reads what the operation LEAVES, not what the table holds.

        ``record_paydays`` takes the retirements and the recordings in ONE call
        so every refusal judges the payday set the operation actually leaves
        behind, and the floor is the case that most depends on it: retire
        2025-12-18 and the last surviving payday becomes 2025-12-04, so the
        floor drops a whole cadence.  Added by an adversarial review of this
        step, which named this the one path where ``max(surviving_paydays)``
        and *the payday the derivation closes the calendar on* could come apart
        -- the equality the new floor claims cannot.

        Under ``prior`` the floor is then the day 2025-12-18's own payroll
        would really have landed on, which is 2025-12-18 itself: an ordinary
        Thursday, so the displacement is the identity and the arithmetic is
        visible.  2025-12-17 splits the surviving paycheck and is refused.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = self._fortnightly_through_december(
                user_id, BusinessDayShiftEnum.PRIOR,
            )
            db.session.commit()
            doomed = {created[-1].id}

            with pytest.raises(ValidationError, match="Choose 2025-12-18 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2025, 12, 17),
                    num_periods=1,
                    rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                    retiring_ids=doomed,
                )
            db.session.rollback()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2025, 12, 18),
                num_periods=1,
                rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                retiring_ids=doomed,
            )
            db.session.commit()
            assert [period.start_date for period in all_periods(user_id)] == [
                date(2025, 12, 4), date(2025, 12, 18),
            ]

    def test_a_displaced_ANCHOR_still_refuses_a_real_payday_and_that_is_N_495(
        self, app, db, bare_user,
    ):
        """What asking the producer does NOT fix, pinned so it cannot be missed.

        :func:`~app.services.pay_calendar.projected_payday` steps from the last
        RECORDED payday, and **R-PC47** says a recorded payday may itself have
        been moved.  Under ``next`` the 2030-11-28 payroll is really paid
        2030-11-29, so a schedule whose last row is that Friday projects its
        next payday from the Friday: the floor lands on 2030-12-13 while the
        real payday is 2030-12-12, and the write is refused.

        Measured across production's own rhythm -- 1,951 paydays from
        2026-03-26 at cadence 14 out to ``CALENDAR_DATE_MAX``, probed
        2026-09-05 -- the open-coded floor refuses **58** of them under either
        convention; asking the producer takes ``prior`` to **0** and leaves
        ``next`` at those same 58, every one of them anchored on a payday that
        was itself displaced.  That residue is ledger row **N-495**, owner
        ``C14-e``, and this case is here so a later reader does not read the
        step's own ``prior`` result as the whole of it.

        **The displacement below is INERT and the case is not, which an
        adversarial review of this step made explicit.**  2030-12-13 is a
        Friday and an ordinary business day, so the floor is that day with the
        substitution, without it, and under the deleted expression alike -- the
        substitution is here to state the world, not to produce the refusal.
        What produces it is the ANCHOR: 2030-11-29 is not on the owner's grid,
        so ``latest + cadence`` lands a day past the real payday however the
        floor is spelled.  That is precisely why N-495 is an anchor repair and
        not a floor repair, and it is why this case survived the mutation that
        killed the three above it.

        It also states the direction: the floor is now wrong exactly where the
        DERIVED END is wrong, and no longer anywhere else.  The 2030-11-29
        paycheck really is derived as running through 2030-12-12, so refusing a
        payday on that day is this fence agreeing with the calendar rather than
        contradicting it -- which is why the repair belongs to the anchor and
        not here.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 11, 29),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.NEXT),
            )
            db.session.commit()

            # The owner's TRUE grid, and the day the projection answers off the
            # RECORD, differ by exactly the displacement.  Without this the
            # case is green against a nominal producer -- an adversarial review
            # of ``C14-e-3`` found the arm vacuous when the substitution it
            # used to carry was deleted and nothing replaced it.
            forward = rhythm_of(14, BusinessDayShiftEnum.NEXT)
            assert pay_calendar.projected_payday(
                date(2030, 11, 28), forward, 0,
            ) == date(2030, 11, 29), "the recorded payday IS a displaced one"
            assert pay_calendar.projected_payday(
                date(2030, 11, 28), forward, 1,
            ) == date(2030, 12, 12), "and the owner's real next payday is this"

            with pytest.raises(ValidationError, match="Choose 2030-12-13 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2030, 12, 12),
                    num_periods=1,
                    rhythm=rhythm_of(14, BusinessDayShiftEnum.NEXT),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 1


class TestTheFloorReadsTheSTOREDConventionAndNotTheBatchS:
    """The obligation ``C14-d`` wrote down, ``C14-e-1`` moved, and this step GRADES.

    ``reject_backward_payday`` asks how far the owner's EXISTING calendar
    already reaches, so it must read the convention ``budget.pay_schedule``
    holds and not the one the incoming batch carries.  A batch that CHANGES the
    convention would otherwise compute its floor under the new one while
    :func:`~app.services.pay_calendar.derive_periods` still closes the existing
    calendar under the old -- the disagreement between fence and boundary
    ``C14-d`` exists to end, re-entering through the argument list.

    **Nothing could grade it until now, and that is why it stayed an
    obligation.**  While ``projected_payday`` returned the nominal grid day the
    stored convention and the batch's selected the SAME floor, so no test could
    tell the correct function from one reading the wrong half -- and an
    obligation marked discharged with nothing grading it is worse than one left
    open, because the next reader stops looking.  ``C14-e-3`` switches the
    displacement on and these two cases are the ones that distinguish them.

    **Both directions, because a rule read off the wrong value fails
    asymmetrically.**  Reading the batch's ``next`` where ``prior`` is stored
    REFUSES a write the calendar admits; reading the batch's ``prior`` where
    ``next`` is stored ACCEPTS one that splits an existing paycheck.  A case
    for only one of them would report the other as covered.
    """

    #: An ordinary Thursday on the grid; one cadence later is Thanksgiving
    #: 2030, so the three conventions put the next paycheck's opening day on
    #: three different days and the stored half is visible.
    _ANCHOR = date(2030, 11, 14)

    def _recorded_under(self, user_id, shift):
        """Store 2030-11-14 as the owner's only payday, under *shift*."""
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=self._ANCHOR,
            num_periods=1, rhythm=rhythm_of(14, shift),
        )

    def test_a_batch_changing_to_NEXT_is_bounded_by_the_stored_PRIOR(
        self, app, db, bare_user,
    ):
        """Stored ``prior`` closes the paycheck on 2030-11-26, so 11-27 is legal.

        Hand-computed.  The owner is paid ``prior``, so their 2030-11-28
        payroll really lands 2030-11-27 and ``derive_periods`` runs the
        2030-11-14 paycheck through 2030-11-26.  A batch that switches them to
        ``next`` and states the 2030-11-27 they were really paid must be
        ACCEPTED: the floor is where the existing calendar ends.

        Read off the BATCH's ``next`` the floor would be 2030-11-29 and this
        write would be refused -- an owner unable to record a payday they
        actually received, on a door ``top_up_rolling_window`` reaches from
        ``/grid`` with no handler.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._recorded_under(user_id, BusinessDayShiftEnum.PRIOR)
            db.session.commit()
            assert calendar_for(user_id).horizon() == date(2030, 11, 26)

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 11, 27),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.NEXT),
            )
            db.session.commit()

            assert [period.start_date for period in created] == [
                date(2030, 11, 27),
            ]

    def test_a_batch_changing_to_PRIOR_is_bounded_by_the_stored_NEXT(
        self, app, db, bare_user,
    ):
        """Stored ``next`` closes the paycheck on 2030-11-28, so 11-27 SPLITS it.

        The mirror, and the direction a floor reading the batch's own half gets
        wrong in the dangerous direction.  The owner is paid ``next``, so their
        2030-11-28 payroll lands 2030-11-29 and the 2030-11-14 paycheck runs
        through 2030-11-28.  A batch switching them to ``prior`` and stating
        2030-11-27 must be REFUSED -- that day is inside a paycheck they
        already hold, which is the only thing this fence still exists to stop
        (plan step **C6**).

        Read off the batch's ``prior`` the floor would be 2030-11-27 and the
        write would be accepted, splitting the paycheck in half.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._recorded_under(user_id, BusinessDayShiftEnum.NEXT)
            db.session.commit()
            assert calendar_for(user_id).horizon() == date(2030, 11, 28)

            with pytest.raises(
                ValidationError, match="Choose 2030-11-29 or later",
            ):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2030, 11, 27),
                    num_periods=1,
                    rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                )
            db.session.rollback()
            assert [period.start_date for period in all_periods(user_id)] == [
                self._ANCHOR,
            ]


class TestTheFloorAnchorsOnTheEra:
    """Plan step **C17-b-2** (rulings R-PC66, R-PC72): the floor is on the ERA's grid.

    ``reject_backward_payday`` stepped one cadence from the last RECORDED
    payday, which **R-PC47** says may fall off the cadence -- so an owner paid
    a day early was bounded a day early, and a batch on the grid's own next
    payday was admitted a day before the paycheck it stood for had run its
    course (ledger row **N-495** at the writer).  It reads
    :func:`~app.services.pay_calendar.payday_after` now: the record is
    matched to the planned payday it stands for and the floor is the planned
    payday after that, which is the same call
    :func:`~app.services.pay_calendar.derive_periods` closes the last saved
    period with.

    The off-grid record is put there by SQL -- the doors record grid days
    displaced under the convention, and an early payment payroll made for a
    reason the convention does not model is the shape **R-PC47** says a
    record may hold and the integrity sweep warns on.
    """

    def _paid_a_day_early(self, user_id):
        """Three fortnightly paydays from 2026-01-02, the last paid 01-29 not 01-30."""
        pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2026, 1, 2),
            num_periods=3, rhythm=rhythm_of(14),
        )
        db_session = pay_period_write.db.session
        db_session.flush()
        db_session.execute(text(
            "UPDATE budget.pay_periods SET start_date = :early "
            " WHERE user_id = :uid AND start_date = :planned"
        ), {"early": date(2026, 1, 29), "planned": date(2026, 1, 30), "uid": user_id})
        db_session.commit()
        assert [p.start_date for p in all_periods(user_id)] == [
            date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 29),
        ]

    def test_the_planned_payday_after_the_early_one_is_the_floor(
        self, app, db, bare_user,
    ):
        """02-12 is refused and 02-13 accepted: the 01-29 record IS the 01-30 paycheck.

        Stepped from the record the floor was 02-12, and a batch on 02-12
        split nothing the OLD rule could see -- while the calendar, anchored
        the same way, closed the 01-29 paycheck on 02-11.  Anchored on the
        era the paycheck runs to 02-12 and the floor is 02-13, the grid's own
        next payday, which the extend door offers.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._paid_a_day_early(user_id)

            assert calendar_for(user_id).periods[-1].end_date == date(2026, 2, 12)
            with pytest.raises(ValidationError, match="Choose 2026-02-13 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 12),
                    num_periods=1, rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 3

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 13),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [date(2026, 2, 13)]

    def test_the_extend_door_offers_exactly_the_floor(self, app, db, bare_user):
        """The horizon is on the grid, so the day extend hands the writer is admitted.

        ``C14-c``'s premise restored (ledger row **N-495**): the horizon the
        door asks against, the floor the writer applies and the grid day the
        door offers are one anchor's arithmetic.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._paid_a_day_early(user_id)

            appended = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert [p.start_date for p in appended] == [date(2026, 2, 13)]
            assert calendar_for(user_id).periods[-1].end_date == date(2026, 2, 26)

    def test_a_minted_eras_first_payday_is_bounded_even_when_the_record_holds_it(
        self, app, db, bare_user,
    ):
        """The seam the calendar refuses is unwritable: a mint below the floor is refused.

        An adversarial review of this step drove this sequence through the
        pure door functions and reached a stored era sequence whose first
        paydays INVERT, which ``validate_eras`` then refused on every page.
        Every ingredient is a legal input: a two-day cadence under ``none``
        (below the collision floor, so legal only undisplaced), a rebuild
        from Saturday 2026-01-17 at 14 days under ``next`` (paid Tuesday
        01-20, Monday being Martin Luther King Day), a truncate back to 01-18,
        then a rebuild from that Monday at 7 days under ``prior`` -- whose
        first payday is Friday 01-16, a day the record ALREADY holds, so the
        floor over the batch's new paydays (01-26 alone) never saw it.  The
        floor now bounds a minting batch at its era's first payday as well
        (ruling 2026-09-11): 01-16 is inside the paycheck opening 01-18, and
        the batch is refused.  The control below is the same rebuild from the
        floor itself, accepted.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 14),
                num_periods=3, rhythm=rhythm_of(2),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 17),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.NEXT),
            )
            db.session.commit()
            assert [p.start_date for p in all_periods(user_id)] == [
                date(2026, 1, 14), date(2026, 1, 16), date(2026, 1, 18),
                date(2026, 1, 20),
            ]
            doomed = {
                p.id for p in all_periods(user_id)
                if p.start_date == date(2026, 1, 20)
            }
            pay_period_write.retire_paydays(user_id, doomed)
            db.session.commit()

            with pytest.raises(ValidationError, match="Choose 2026-01-20 or later"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 19),
                    num_periods=2,
                    rhythm=rhythm_of(7, BusinessDayShiftEnum.PRIOR),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 3
            # And every page still derives: the stored sequence is in order.
            assert [e.effective_from for e in calendar_for(user_id).eras] == [
                date(2026, 1, 14), date(2026, 1, 17),
            ]

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 20),
                num_periods=2, rhythm=rhythm_of(7, BusinessDayShiftEnum.PRIOR),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [
                date(2026, 1, 20), date(2026, 1, 27),
            ]
            assert calendar_for(user_id).periods[-1].start_date == date(2026, 1, 27)

    def test_the_refusal_names_the_covering_eras_cadence(self, app, db, bare_user):
        """A piecewise owner is told the cadence of the era their last paycheck runs at."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=2, rhythm=rhythm_of(7),
            )
            db.session.commit()

            # The covering era's rhythm, named by its kind's phrase since
            # plan step pay_calendar:C17-d-2 ("at a 7-day cycle" until then).
            with pytest.raises(ValidationError, match="paid every 7 days"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 10),
                    num_periods=1, rhythm=rhythm_of(7),
                )
            db.session.rollback()
