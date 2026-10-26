"""The batch shape: which paydays a call records and what the calendar says, the last period's
derived end read from the STORED cadence (P28), the cadence a batch stores only when it records
a payday (P12, P29), and the bound on what one call may create (X-ad-a).
"""

from datetime import date, timedelta

import pytest

from app.exceptions import ValidationError
from app.models.pay_period import PayPeriod
from app.models.pay_era import CADENCE_DAYS_MIN
from app.services import (
    pay_period_admin,
    pay_period_rolling,
    pay_period_write,
    pay_schedule_service,
)
from app.schemas.validation import PayPeriodGenerateSchema
from tests._test_helpers import rhythm_of, all_periods
from app.services.pay_rhythm import FixedDays

from ._helpers import _paydays


class TestItRecordsPaydays:
    """The batch shape: which paydays a call records, and what the calendar says."""

    def test_it_records_the_requested_paydays(self, app, db, bare_user):
        """Five paydays a fortnight apart, in the order they were asked for."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=5,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [period.start_date for period in periods] == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
                date(2026, 2, 13), date(2026, 2, 27),
            ]

    def test_the_calendar_the_batch_leaves_runs_0_to_n_minus_1(
        self, app, db, bare_user,
    ):
        """The owner's whole calendar, end to end through the writer.

        The ordinal is the payday's position in the owner's sorted set and the
        end is the day before the next payday, so index order and date order
        cannot disagree -- there is no second value.  Before plan step C3-b the
        writer ASSIGNED ``max_index + 1`` and ``start + cadence - 1``; plan step
        ``pay_calendar:C4-c`` deleted both columns, so what the writer leaves
        behind is a payday set and this is what it derives to.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Each end is the day before the next payday; the LAST is the one
            # projection, 2026-01-30 + 13.
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
                (date(2026, 1, 30), date(2026, 2, 12), 2),
            ]

    def test_an_existing_payday_is_skipped_rather_than_duplicated(
        self, app, db, bare_user,
    ):
        """A re-run naming days already on the table records only the new ones."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            again = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [p.start_date for p in again] == [
                date(2026, 1, 30), date(2026, 2, 13),
            ]
            assert len(all_periods(user_id)) == 4
            assert [row[2] for row in _paydays(user_id)] == [0, 1, 2, 3]

    def test_a_second_batch_appends_and_keeps_payday_order(
        self, app, db, bare_user,
    ):
        """Appending after the current coverage extends the schedule forward."""
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()

            new = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 13),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [p.start_date for p in new] == [
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            starts = [row[0] for row in _paydays(user_id)]
            assert starts == sorted(starts)
            assert [row[2] for row in _paydays(user_id)] == [0, 1, 2, 3, 4]

    def test_num_periods_one_records_one_payday(self, app, db, bare_user):
        """The smallest legal batch."""
        with app.app_context():
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert len(periods) == 1
            assert periods[0].start_date == date(2026, 1, 2)
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
            ]

    def test_a_large_batch_stays_correct_at_scale(self, app, db, bare_user):
        """104 periods -- two years of fortnights, the production shape."""
        with app.app_context():
            start = date(2026, 1, 2)
            periods = pay_period_write.record_paydays(
                user_id=bare_user["user"].id, first_payday=start,
                num_periods=104, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert len(periods) == 104
            assert periods[-1].start_date == start + timedelta(days=103 * 14)
            derived = _paydays(bare_user["user"].id)
            assert [row[2] for row in derived] == list(range(104))
            for payday, last_day, _ordinal in derived:
                assert last_day == payday + timedelta(days=13)

    def test_a_non_date_payday_is_refused(self, app, db, bare_user):
        """A string, and a ``datetime``, are both refused as form errors.

        ``datetime`` is a ``date`` SUBCLASS, so the ``isinstance`` check this
        replaced accepted one and every derived end silently carried a time
        component -- comparing unequal to the ``DATE`` column it is stored in.
        ``pay_calendar._validated`` refuses the same value, but as a
        ``PayCalendarError`` no route catches; refusing here makes it the 422
        it actually is.
        """
        with app.app_context():
            for bad in ("2026-01-02", __import__("datetime").datetime(2026, 1, 2)):
                with pytest.raises(ValidationError, match="must be a date"):
                    pay_period_write.record_paydays(
                        user_id=bare_user["user"].id, first_payday=bad,
                        num_periods=1, rhythm=rhythm_of(14),
                    )
            db.session.rollback()


class TestTheDerivedHorizonFollowsTheStoredCadence:
    """The LAST period's end is a projection, and it reads one number.

    Every other end is dictated by the next payday and is a fact.  The last has
    no successor, so it is ``start_date + cadence_days - 1`` read from
    ``budget.pay_schedule`` -- which is why finding **P28** ("the horizon the
    app projects" disagreeing with "the end stored on the last row") has no
    subject since plan step ``pay_calendar:C4-c``: there is one value and no
    column for it to come apart from.
    """

    def test_truncate_reprojects_the_new_last_end(self, app, db, bare_user):
        """Deleting the tail moves the survivor's end from a fact to a projection.

        Paydays ``[2026-01-02, 2026-01-16, 2026-02-11]``: the January 16
        paycheck runs to 2026-02-10 because its successor opens then.  Truncate
        that successor away and it is the LAST period, so its end falls back to
        the cadence projection, 2026-01-29.  An on-cadence fixture cannot see
        this at all, because ``lead(start) - 1`` and ``start + cadence - 1``
        coincide there.

        **The off-grid successor is a DIRECT row since plan step
        ``C17-c-2b``**, the shape the tree reserves for a row no door writes
        (ruling **R-PC47**: payroll moved a payday for a reason the
        convention does not model).  It was recorded through the writer,
        which since ``C17-a`` mints a phase-corrected ERA at 02-11 -- and
        under the seam rule **R-PC75** an era outlives the truncate and its
        first payday replaces the old era's 01-30, so the survivor's
        projected end would be 02-10 either way and the case would grade
        nothing.  Off the plan and with one era, the fact (02-10) and the
        projection (01-29) still part, which is this case's subject.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.add(PayPeriod(user_id=user_id, start_date=date(2026, 2, 11)))
            db.session.commit()
            assert _paydays(user_id)[1] == (
                date(2026, 1, 16), date(2026, 2, 10), 1,
            )

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=created[1].id,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 15), 0),
                (date(2026, 1, 16), date(2026, 1, 29), 1),
            ]

    def test_the_last_end_follows_the_STORED_cadence(self, app, db, bare_user):
        """Finding **P28** closed at the root: one cadence, not two.

        Here the batch records nothing new -- every payday already exists -- so
        the cadence rule does NOT fire, and the stored 14 is what the horizon
        keeps even though the call named 30.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            # ONE payday, and it already exists -- the reachable shape
            # finding P12 names.  Two would step by 30 and create a new one.
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(30),
            )
            db.session.commit()

            assert created == []
            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(14)
            assert _paydays(user_id)[-1] == (
                date(2026, 1, 16), date(2026, 1, 29), 1,
            )


class TestTheCadenceRule:
    """A batch that RECORDS a payday stores its cadence; one that records none does not.

    Findings **P12** (a batch creating nothing still rewrote the cadence) and
    **P29** (the extend door generated at a cadence it never persisted).  The
    trigger weighed and rejected was "at least TWO paydays, or the owner's
    first": it silently discards a REQUIRED form input, because a regenerate at
    ``num_periods=1, cadence_days=30`` would then build a 14-day paycheck and
    say nothing.
    """

    def test_a_batch_that_records_a_payday_stores_its_cadence(
        self, app, db, bare_user,
    ):
        """Including a ONE-payday batch, whose cadence the user typed.

        The value is deliberately not 14: an on-default fixture cannot tell
        "the cadence was stored" from "the default was".
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(7),
            )
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(7)
            assert _paydays(user_id) == [
                (date(2026, 1, 2), date(2026, 1, 8), 0),
            ]

    def test_a_batch_that_records_nothing_leaves_the_cadence_alone(
        self, app, db, bare_user,
    ):
        """Finding **P12**, closed exactly and no wider.

        The reachable path the finding names: a ``num_periods=1`` post naming
        an existing payday.  It creates zero rows, and before this step it
        still reached ``upsert_schedule`` -- so a form post moved the horizon
        every later extend and rolling top-up continue from, while flashing
        "Generated 0 pay periods."
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(30),
            )
            db.session.commit()

            assert created == []
            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(14)

    def test_a_REFUSED_batch_leaves_the_cadence_alone(
        self, app, db, bare_user,
    ):
        """A batch that does not get written does not move the forecast either.

        The control the retired ``TestEstablishSchedule`` carried, restored
        because an adversarial review of this step found the writer upserting
        the cadence BEFORE its last refusal could fire.  The upsert is now the
        second-to-last statement, after both refusals, so a refused batch
        leaves the schedule row exactly as it was -- which matters because
        ``cadence_days`` sets the derived horizon every extend and rolling
        top-up continues from.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 20),
                    num_periods=2, rhythm=rhythm_of(7),
                )
            db.session.rollback()

            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(14)

    def test_extend_takes_no_cadence_at_all(self, app, db, bare_user):
        """Finding **P29**, closed by DELETION rather than by a new write.

        ``extend_pay_periods`` accepted a ``cadence_days`` the extend card
        renders no control for, generated at it, and never stored it -- so a
        direct POST wrote 7-day paychecks while the schedule still said 14.
        Extend CONTINUES a schedule, so the question is not its to ask; the
        parameter is gone, which is what finding **P30** objected to answering
        with a write.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            with pytest.raises(TypeError):
                pay_period_admin.extend_pay_periods(
                    user_id, 1, cadence_days=7,
                )

            pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(14)
            assert _paydays(user_id)[-1] == (
                date(2026, 1, 30), date(2026, 2, 12), 2,
            )


class TestTheWriterBoundsWhatOneCallMayCreate:
    """``reject_out_of_range_batch_size`` -- plan step X-ad-a, as C4-c left it.

    ``num_periods`` was bounded by no service at all, so a non-form caller
    could ask for zero (failing several statements later under a message about
    accounts) or for a hundred thousand.  That is the whole of what this door
    refuses now.

    **The cadence FLOOR that used to sit beside it is gone, and the first case
    below is its inverse.**  It refused a cadence of 1 because the last
    period's stored end would be its own ``start_date`` and
    ``ck_pay_periods_date_order`` would refuse it -- an unhandled
    ``IntegrityError`` 500, reproduced on both the settings form and the
    registration form.  A one-day pay cycle was always legitimate; what could
    not hold one was a stored column.  Plan step ``pay_calendar:C4-c`` dropped
    it, closing pay-calendar findings **P9** and **P33**.
    """

    def test_a_one_day_cadence_is_ACCEPTED(self, app, db, bare_user):
        """Finding **P9**, closed: two paydays a day apart are two paychecks.

        This case asserted a ``ValidationError`` until plan step
        ``pay_calendar:C4-c``, and inverting it is the point.  Three paydays a
        day apart derive three one-day periods -- each end is the day before
        the next payday, and the LAST is ``start + (1 - 1)``, its own payday.
        ``ck_pay_periods_date_order`` refused exactly this shape.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(1),
            )
            db.session.commit()

            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 2), 0),
                (date(2026, 1, 3), date(2026, 1, 3), 1),
                (date(2026, 1, 4), date(2026, 1, 4), 2),
            ]

    def test_a_one_day_cadence_survives_the_read_path_top_up(
        self, app, db, bare_user,
    ):
        """Finding **P33**, closed structurally rather than swallowed.

        ``top_up_rolling_window`` runs on ``/grid`` and ``/dashboard`` with no
        handler of its own, so for an owner holding cadence 1 the writer's
        refusal was a permanent 500 on both screens.  The refusal is gone, so
        the top-up simply extends the schedule -- the state was never illegal,
        only unstorable.

        Both paydays fall after this module's frozen today (2025-12-01), so the
        window holds two current-and-future paychecks against a target of four
        and the deficit is two.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(1),
            )
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=4,
            )
            db.session.commit()

            created = pay_period_rolling.top_up_rolling_window(user_id)
            db.session.commit()

            assert [period.start_date for period in created] == [
                date(2026, 1, 4), date(2026, 1, 5),
            ]

    def test_a_two_day_cadence_is_accepted(self, app, db, bare_user):
        """Two paydays two days apart give two-day periods.

        01-02..01-03 and 01-04..01-05 -- the case that was the INCLUSIVE-floor
        control while a floor existed, kept because it is the ordinary short
        cycle and the arithmetic is worth pinning either way.
        """
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                rhythm=rhythm_of(2),
            )
            db.session.commit()
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 3), 0),
                (date(2026, 1, 4), date(2026, 1, 5), 1),
            ]

    @pytest.mark.parametrize("count", [0, -1, 261, 100_000])
    def test_a_batch_size_outside_the_policy_is_refused(
        self, app, db, bare_user, count,
    ):
        """Zero, negative and oversized batches refuse and write nothing.

        Zero is the one that mattered: it created no periods, no error, and
        surfaced far downstream.  100000 is 383 years of fortnights in one
        transaction.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 260"):
                pay_period_write.record_paydays(
                    user_id=bare_user["user"].id,
                    first_payday=date(2026, 1, 2),
                    num_periods=count,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert all_periods(
                bare_user["user"].id,
            ) == []

    def test_the_form_bound_and_the_writer_bound_accept_the_SAME_cadence(
        self, app, db, bare_user,
    ):
        """What the generate form admits is exactly what the writer materialises.

        There were TWO cadence floors between plan steps X-ad-a and
        ``pay_calendar:C4-c``: the column admitted 1 and the schema admitted 2,
        because the writer could not put a one-day period into a stored
        ``end_date``.  ``CADENCE_DAYS_FORM_MIN`` was that second number and it
        is deleted -- a bound the schema restates is a bound that can drift
        from the column.

        Graded end to end rather than by comparing two constants: the schema's
        own ``load`` produces the payload and the writer consumes it, so a
        floor that came back on either side fails here.  It is also what keeps
        ``routes/pay_periods.generate``'s error attribution provable -- the
        schema bounds the cadence and the batch to exactly what the writer
        accepts, so the only ``ValidationError`` that can reach that handler is
        the forward-only one it renders on ``start_date``.

        **That last sentence needed a SECOND rule to stay true**, added at plan
        step ``pay_calendar:C14-b``.  The writer gained a fifth refusal -- a
        payday convention the cadence beside it cannot carry -- which no bound
        on either field alone can answer, so the schema gained
        ``validate_derivable_rhythm`` to ask it as a cross-field rule.  The
        payload below therefore threads the schema's own ``shift`` into the
        rhythm rather than defaulting one: a test that supplied ``none`` by
        hand would grade the cadence agreement and leave the pair rule
        untested at exactly the boundary this case exists to hold.

        **The schema hands the rhythm over as the VALUE since plan step
        ``pay_calendar:C17-d-3``** (ruling **R-PC84**), so the writer is
        given ``loaded["rhythm"]`` whole -- the same object the routes now
        pass -- rather than a pair re-assembled from two wire keys the
        payload no longer carries.
        """
        with app.app_context():
            loaded = PayPeriodGenerateSchema().load({
                "start_date": "2026-01-02",
                "num_periods": "2",
                "cadence_days": str(CADENCE_DAYS_MIN),
            })
            pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=loaded["start_date"],
                num_periods=loaded["num_periods"],
                rhythm=loaded["rhythm"],
            )
            db.session.commit()

            assert loaded["rhythm"].cadence == FixedDays(1)
            assert _paydays(bare_user["user"].id) == [
                (date(2026, 1, 2), date(2026, 1, 2), 0),
                (date(2026, 1, 3), date(2026, 1, 3), 1),
            ]

    def test_an_unstorable_cadence_creates_no_periods(self, app, db, bare_user):
        """A cadence the schedule column refuses writes nothing at all.

        The refusal happens before any row is added -- which is what makes the
        generate route's 422 clean.  ``establish_schedule`` used to compose the
        two calls in the other order, and a naive composition would have left
        the owner with 366-day pay periods and no schedule row.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            with pytest.raises(ValidationError, match="between 1 and 365"):
                pay_period_write.record_paydays(
                    user_id=user_id,
                    first_payday=date(2026, 1, 2),
                    num_periods=2,
                    rhythm=rhythm_of(366),
                )
            db.session.rollback()
            assert all_periods(user_id) == []
            assert pay_schedule_service.get_schedule(user_id) is None
