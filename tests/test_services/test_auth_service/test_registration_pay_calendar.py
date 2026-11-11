"""Auth service tests: what registration gives the pay calendar."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import BusinessDayShiftEnum
from app.models.account import Account, AccountAnchorHistory
from app.models.user import User
from app.services import pay_calendar, pay_schedule_service, registration_service
from app.exceptions import ValidationError
from app.services.pay_rhythm import FixedDays, Monthly, Rhythm
from app.utils.dates import display_today
from tests._test_helpers import all_periods, freeze_today, last_covered_day, registration_spec


class TestRegistrationBuildsARealPayCalendar:
    """Plan step X-ad-a, ruling R-DB -- registration asks instead of inventing.

    **What it replaced, which is finding N-123.**  ``register_user`` used to
    write one ``PayPeriod`` covering ``[today, today + 13]`` and nothing else,
    because ``account_service.create_account`` refused an owner with no pay
    period (until ruling plan#3318).  The consequence was measured at the service tier: on the form
    that then asked for the owner's real payday, ``today + 1`` through
    ``today + 13`` were REFUSED outright by
    ``pay_period_service._reject_overlapping_batch``, and every answer from
    ``today + 14`` on was accepted leaving a permanent hole -- a stretch of
    days funded by no paycheck, which the cash fold cannot place and which the
    grid's reconciliation identity then fails by whatever landed in it
    (finding N-128, ``-$140.63`` on the gapped clone).

    The remedy is structural rather than a better guess: the owner states the
    day they were LAST paid, and every one of the fourteen days that used to be
    unenterable is now the ordinary answer.
    """

    @pytest.mark.parametrize("days_back", list(range(14)))
    def test_every_payday_in_the_window_is_now_enterable(
        self, app, db, days_back,
    ):
        """All 14 paydays of a biweekly cycle register cleanly, with no hole.

        This is N-123's measurement inverted.  ``days_back`` walks the whole
        accepted window: 0 is today (the one answer the old path also took),
        1..13 are the thirteen the old path REFUSED.  Each must produce a
        contiguous calendar whose first period contains sign-up day.
        """
        signup_day = display_today()
        payday = signup_day - timedelta(days=days_back)
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email=f"window-{days_back}@example.com",
                display_name=f"Window {days_back}",
                first_payday=payday,
                cadence_days=14,
                num_periods=5,
            ))
            db.session.flush()

            periods = all_periods(user.id)
            assert len(periods) == 5
            assert periods[0].start_date == payday
            # Sign-up day sits inside the first paycheck at every offset --
            # the property the "LAST payday, not next" ruling buys.
            assert periods[0].start_date <= signup_day <= last_covered_day(periods[0])
            # Contiguous: each period opens the day after the previous closed.
            # A hole is exactly what the old bootstrap left behind, so its
            # absence is the assertion, not a formality.
            for earlier, later in zip(periods, periods[1:]):
                assert later.start_date == last_covered_day(earlier) + timedelta(days=1)

    def test_the_schedule_row_is_written_with_the_stated_cadence(
        self, app, db,
    ):
        """The owner's cadence is persisted, not inferred later.

        Pay-calendar finding **P8**: registration wrote a payday with no
        ``budget.pay_schedule`` row, so ``resolve_cadence`` had to infer the
        cadence from the last period's stored LENGTH -- reading back the value
        the writer had produced.  A weekly cadence is used here because a
        14-day one is the app default and could not tell a stored value from a
        defaulted one.
        """
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="weekly@example.com", display_name="Weekly",
                first_payday=display_today() - timedelta(days=3),
                cadence_days=7, num_periods=4,
            ))
            db.session.flush()

            schedule = pay_schedule_service.get_schedule(user.id)
            assert schedule is not None
            assert pay_schedule_service.resolve_cadence(user.id) == FixedDays(7)
            periods = all_periods(user.id)
            assert all(
                last_covered_day(p) == p.start_date + timedelta(days=6) for p in periods
            )

    def test_the_opening_assertion_is_dated_signup_day_and_files_cleanly(
        self, app, db,
    ):
        """The opening balance is asserted for TODAY and has a paycheck to file under.

        The assertion carries SIGN-UP day, not the schedule's opening payday:
        the owner is stating what their account holds now, and under the old
        fabricated period those two dates happened to coincide, which is
        exactly the conflation this step separates.

        **It books no line, and that is the documented rule rather than a gap
        in this test**: the worked-out ledger books nothing for a ``$0.00``
        opening (a correction whose anchor balance equals ``ledger_before``
        "books NOTHING", ``ledger_lines._account_anchors``), and registration's
        default Checking account is always ``$0.00``.  So no line is available
        as evidence, and the property it would have evidenced is asserted
        directly instead: the calendar CONTAINS sign-up day, so
        :meth:`~app.services.pay_calendar.PayCalendar.filing_period` files a
        record dated today under period 0 by containment rather than by its
        clamp -- the clamp exists for a loan opened years before the owner's
        first payday, not for a balance asserted at sign-up.
        """
        signup_day = display_today()
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="opening@example.com", display_name="Opening",
                first_payday=signup_day - timedelta(days=10),
                cadence_days=14, num_periods=3,
            ))
            db.session.flush()

            account = db.session.query(Account).filter_by(
                user_id=user.id, name="Checking",
            ).one()
            assertion = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).one()
            assert assertion.observed_on == signup_day
            assert assertion.anchor_balance == Decimal("0.00")

            first_period = all_periods(user.id)[0]
            calendar = pay_calendar.calendar_for(user.id)
            # Containment, not the clamp: period_containing answers None
            # outside every span, so a non-None answer here is the evidence.
            assert (
                calendar.period_containing(signup_day).period_id
                == first_period.id
            )
            assert (
                calendar.filing_period(signup_day).period_id
                == first_period.id
            )

    def test_a_future_payday_is_refused_and_creates_no_user(self, app, db):
        """Tomorrow is not a day the owner was last paid on.

        The refusal has to happen before the ``User`` row is added: a
        registration that fails halfway would otherwise leave a partly-built
        owner in the session whose only protection is that nobody commits it.
        """
        with app.app_context():
            before = db.session.query(User).count()
            with pytest.raises(ValidationError, match="has not happened yet"):
                registration_service.register_user(registration_spec(
                    email="future@example.com", display_name="Future",
                    first_payday=display_today() + timedelta(days=1),
                ))
            assert db.session.query(User).count() == before

    def test_a_payday_older_than_one_cadence_is_refused(self, app, db):
        """A payday 14 days back contradicts a 14-day cadence.

        At cadence 14 the accepted window is ``[today - 13, today]``, so
        ``today - 14`` is the first day outside it: another payday would have
        arrived since.  Accepting it would open the schedule with a period
        that has already closed and never covered sign-up day.
        """
        with app.app_context():
            before = db.session.query(User).count()
            with pytest.raises(ValidationError, match="has already ended"):
                registration_service.register_user(registration_spec(
                    email="stale@example.com", display_name="Stale",
                    first_payday=display_today() - timedelta(days=14),
                    cadence_days=14,
                ))
            assert db.session.query(User).count() == before

    def test_the_window_scales_with_the_cadence(self, app, db):
        """A weekly owner's window is 7 days wide, not 14.

        The bound is ``today - cadence + 1``, so the same date that is
        perfectly legal at a 14-day cadence is refused at a 7-day one.  A
        hardcoded fortnight would pass the previous test and fail here, which
        is the point of running the pair.
        """
        eight_days_back = display_today() - timedelta(days=8)
        with app.app_context():
            with pytest.raises(ValidationError, match="has already ended"):
                registration_service.register_user(registration_spec(
                    email="weekly-stale@example.com",
                    display_name="Weekly Stale",
                    first_payday=eight_days_back, cadence_days=7,
                ))
            user = registration_service.register_user(registration_spec(
                email="biweekly-ok@example.com",
                display_name="Biweekly OK",
                first_payday=eight_days_back, cadence_days=14,
                num_periods=2,
            ))
            db.session.flush()
            assert (
                all_periods(user.id)[0].start_date
                == eight_days_back
            )

    @pytest.mark.parametrize("cadence", [0, 366])
    def test_an_unstorable_cadence_is_refused_and_creates_no_user(
        self, app, db, cadence,
    ):
        """The schedule column's bound is enforced before anything is written.

        Registration asks ``pay_schedule_service.reject_out_of_range_cadence``
        up front rather than discovering the bound when the upsert hits
        ``ck_pay_schedule_cadence_range`` -- which would be an
        ``IntegrityError`` 500 several statements after the owner was created.
        """
        with app.app_context():
            before = db.session.query(User).count()
            with pytest.raises(ValidationError, match="between 1 and 365"):
                registration_service.register_user(registration_spec(
                    email=f"cadence-{cadence}@example.com",
                    display_name="Bad Cadence", cadence_days=cadence,
                ))
            assert db.session.query(User).count() == before

    @pytest.mark.parametrize("num_periods", [0, 261])
    def test_an_unwritable_batch_size_is_refused_before_the_user_exists(
        self, app, db, num_periods,
    ):
        """The WRITER's bound is asked up front too, not just the column's.

        A ``num_periods`` of 0 created nothing and surfaced downstream as
        ``create_account`` complaining about missing pay periods; 261 was
        refused only by a form field, so no non-form caller was bounded at all.

        The ``User`` count is the assertion that matters: ``register_user``
        claims every refusal happens before the row is added, and these were
        the values that could have falsified it.

        *A third case -- ``cadence_days=1``, refused with "at least 2" -- stood
        here until plan step ``pay_calendar:C4-c``.  That bound was about the
        stored ``end_date``, which could not hold a one-day period, and the
        case below is its inverse: the cycle is legal and registration
        RECORDS it.*
        """
        with app.app_context():
            before = db.session.query(User).count()
            with pytest.raises(ValidationError, match="between 1 and 260"):
                registration_service.register_user(registration_spec(
                    email=f"batch-{num_periods}@example.com",
                    display_name="Bad Batch", num_periods=num_periods,
                ))
            db.session.rollback()
            assert db.session.query(User).count() == before

    def test_a_ONE_DAY_cadence_registers_and_records_its_paydays(
        self, app, db,
    ):
        """Finding **P9**, closed at the door that could not express it.

        Registration refused ``cadence_days=1`` with "at least 2" because a
        stored ``end_date`` cannot hold a period whose end is its own start --
        ``ck_pay_periods_date_order`` required ``start_date < end_date``, and
        the value reached it as an unhandled ``CheckViolation`` 500 before the
        writer learned to refuse it politely.  Plan step ``pay_calendar:C4-c``
        dropped the column, so two paydays a day apart are simply two
        paychecks.

        The owner is asserted to hold the paydays the spec asked for, not
        merely to exist: a registration that silently recorded a different
        rhythm would pass a bare "no exception" check.

        The opening payday is the OWNER's today rather than a literal, because
        ``_reject_impossible_first_payday`` bounds it within one cadence of the
        clock and one cadence is one DAY here -- a fixed date would be refused
        on every run but one (``.claude/rules/testing.md``).
        """
        with app.app_context():
            today = display_today()
            user = registration_service.register_user(registration_spec(
                email="one-day-cadence@example.com",
                display_name="Daily Pay",
                first_payday=today,
                num_periods=3,
                cadence_days=1,
            ))
            db.session.commit()

            assert [
                period.start_date for period in all_periods(user.id)
            ] == [today, today + timedelta(days=1), today + timedelta(days=2)]

    def test_the_window_follows_the_DERIVATION_under_a_displacing_convention(
        self, app, db, monkeypatch,
    ):
        """Plan step **pay_calendar:C14-e-3**: the bound is the real paycheck's end.

        **The case this repair exists for, hand-computed.**  An owner paid
        every 14 days under ``prior`` signs up on Friday 2026-01-16 and
        truthfully states Monday 2026-01-05 -- an ordinary business day, so
        their answer is exactly what the form asks for.  Their next grid day is
        2026-01-19, Martin Luther King, Jr. Day, so payroll really pays Friday
        2026-01-16 and the paycheck opened on 01-05 runs only through
        **2026-01-15**.  Sign-up day is not in it.

        The deleted arithmetic accepted that answer: it read the paycheck as
        ``[01-05, 01-05 + 13]`` = through 01-18, so 2026-01-05 sat inside the
        window ``[today - 13, today]``.  Measured over every sign-up day of
        2026-2030, **112 of 1,826** admit such an answer at this cadence under
        ``prior`` (110 at 7, 105 at 28, and 0 under ``next``), and at
        ``num_periods`` = ``PERIOD_BATCH_MIN`` the owner then holds no paycheck
        covering their own sign-up day at all --
        :meth:`~app.services.pay_calendar.PayCalendar.filing_period` clamps the
        opening assertion into one that does not contain it.

        The ``next`` arm is the CONTROL: the same day and the same cadence are
        accepted there, because that convention moves the boundary the other
        way (2026-01-19 is paid 2026-01-20, so the paycheck runs to 01-19 and
        covers sign-up day).  Without it this case would pass for a rule that
        refused everything near a holiday.
        """
        freeze_today(monkeypatch, date(2026, 1, 16))
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                registration_service.register_user(registration_spec(
                    email="mlk-prior@example.com", display_name="MLK Prior",
                    first_payday=date(2026, 1, 5), cadence_days=14,
                    shift=BusinessDayShiftEnum.PRIOR,
                ))
            message = str(exc.value)
            assert "has already ended" in message
            # The paycheck's REAL end, which the deleted rule read as 01-18.
            assert "2026-01-15" in message
            assert "2026-01-18" not in message

            user = registration_service.register_user(registration_spec(
                email="mlk-next@example.com", display_name="MLK Next",
                first_payday=date(2026, 1, 5), cadence_days=14,
                num_periods=1, shift=BusinessDayShiftEnum.NEXT,
            ))
            db.session.flush()
            periods = all_periods(user.id)
            assert periods[0].start_date == date(2026, 1, 5)
            assert last_covered_day(periods[0]) == date(2026, 1, 19)

    def test_a_scheduled_payday_of_tomorrow_paid_today_under_prior_is_accepted(
        self, app, db, monkeypatch,
    ):
        """The form asks for the SCHEDULED day, and that day may be tomorrow.

        Plan step ``pay_calendar:C17-c-2a``, ledger row **PC-504**: the
        writer reads the stated payday as a point on the nominal grid, so the
        door asks for the scheduled day rather than the day the money moved.
        Sign-up Friday 2026-01-02; the scheduled payday is Saturday 01-03 and
        under ``prior`` payroll paid it on Friday.  The upper bound is "the
        paycheck this day opens has started", which is true -- it opened
        today -- so the registration goes through, the recorded payday is the
        CASH day 01-02 and the era's phase is the scheduled 01-03.  Under
        ``none`` the same answer is a paycheck that has not happened yet, the
        control that shows the bound reads the convention.
        """
        freeze_today(monkeypatch, date(2026, 1, 2))
        with app.app_context():
            with pytest.raises(ValidationError, match="has not happened yet"):
                registration_service.register_user(registration_spec(
                    email="sat-none@example.com", display_name="Sat None",
                    first_payday=date(2026, 1, 3), cadence_days=14,
                    shift=BusinessDayShiftEnum.NONE,
                ))
            user = registration_service.register_user(registration_spec(
                email="sat-prior@example.com", display_name="Sat Prior",
                first_payday=date(2026, 1, 3), cadence_days=14,
                num_periods=1, shift=BusinessDayShiftEnum.PRIOR,
            ))
            db.session.flush()
            assert all_periods(user.id)[0].start_date == date(2026, 1, 2)
            facts = pay_schedule_service.resolve_schedule(user.id)
            assert facts.eras[0].effective_from == date(2026, 1, 3)

    def test_the_refusal_message_names_the_paycheck_not_the_owners_arithmetic(
        self, app, db,
    ):
        """A stale payday is explained by the paycheck, not blamed on the user.

        The message read "one of the two does not match the other" until an
        adversarial review of this step found a user whose two answers match
        perfectly: on payday itself, before the deposit posts, "the day money
        landed" is honestly one cadence ago.  The FORM was the defect and was
        reworded to ask for the date on the paycheck; this message stopped
        accusing them either way, and names the span that ended plus the
        payday that works.

        **Which payday it names moved at plan step ``pay_calendar:C17-d-2``
        (ruling R-PC83).**  It named ``E - cadence_days``, the earliest day
        whose paycheck covers today -- a spelling no month grid has -- and
        names the payday on the owner's STATED rhythm whose paycheck covers
        today: ``stale + 14`` here, where it read ``today - 13``.  The
        refusal set is unchanged; the worked figures are in the producer's
        docstring.
        """
        today = display_today()
        stale = today - timedelta(days=20)
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                registration_service.register_user(registration_spec(
                    email="worded@example.com", display_name="Worded",
                    first_payday=stale, cadence_days=14,
                ))
            message = str(exc.value)
            assert "has already ended" in message
            # The span it covered, and the payday on the stated rhythm whose
            # paycheck covers today.
            assert (stale + timedelta(days=13)).isoformat() in message
            assert (stale + timedelta(days=14)).isoformat() in message
            assert (today - timedelta(days=13)).isoformat() not in message

    def test_a_month_rhythm_stated_from_a_day_it_does_not_pay_is_refused_first(
        self, app, db, monkeypatch,
    ):
        """The phase refusal runs BEFORE the grid is asked of the stated day.

        Plan step ``pay_calendar:C17-d-2``'s adversarial review drove
        ``Monthly(5)`` from the 10th through registration: without the
        refusal in the precondition block the "already ended" rule ran the
        grid from a day the rhythm never pays, ACCEPTED the sign-up and
        recorded 2026-01-05, a payday the owner never typed.  No form can
        state a month kind until ``C17-d-3``; the service door can, and this
        is the state that door must refuse before a ``User`` row exists.
        """
        freeze_today(monkeypatch, date(2026, 1, 12))
        with app.app_context():
            with pytest.raises(ValidationError, match="2026-01-05"):
                registration_service.register_user(registration_spec(
                    email="offgrid@example.com", display_name="Off Grid",
                    first_payday=date(2026, 1, 10),
                    rhythm=Rhythm(Monthly(5), BusinessDayShiftEnum.NONE),
                ))
            assert db.session.query(User).filter_by(
                email="offgrid@example.com",
            ).first() is None

    def test_a_monthly_owner_is_named_the_one_payday_that_works(
        self, app, db, monkeypatch,
    ):
        """R-PC83 on a month kind: the named day is the unique legal answer.

        Paid monthly on the 15th, sign-up 2026-03-20 stating 2026-01-15:
        that paycheck ended 02-14, and the only first payday whose paycheck
        covers 03-20 is 03-15 -- the grid is fixed by the stated day, so the
        message can name it outright.  Stating it is accepted.
        """
        freeze_today(monkeypatch, date(2026, 3, 20))
        monthly = Rhythm(Monthly(15), BusinessDayShiftEnum.NONE)
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                registration_service.register_user(registration_spec(
                    email="monthly-stale@example.com", display_name="Stale",
                    first_payday=date(2026, 1, 15), rhythm=monthly,
                ))
            message = str(exc.value)
            assert "has already ended" in message
            assert "2026-02-14" in message
            assert "2026-03-15" in message

            user = registration_service.register_user(registration_spec(
                email="monthly-ok@example.com", display_name="Monthly",
                first_payday=date(2026, 3, 15), num_periods=2, rhythm=monthly,
            ))
            db.session.flush()
            assert [p.start_date for p in all_periods(user.id)] == [
                date(2026, 3, 15), date(2026, 4, 15),
            ]


class TestRegistrationAsksHowFarBackThePaychecksGo:
    """Plan step **balance:X-bh-2**, ruling **balance:R-IA**: the fourth input.

    The sign-up form already asks the payday and the cadence; this is the
    third half of the same rhythm -- where counting BACKWARD stops.  The app
    knows the cadence and cannot derive when a job began, so it asks; a blank
    answer is a statement rather than a skipped question, and means "count my
    paychecks back as far as the app's calendar reaches".
    """

    def test_a_stated_opening_is_persisted_on_the_schedule_row(self, app, db):
        """The answer reaches ``budget.pay_schedule``, which is the only reader.

        Written AFTER ``record_paydays``, because that call is what creates the
        row (the cadence rule, plan step C3-b) and ``set_history_opening``
        refuses an owner without one.
        """
        signup_day = display_today()
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="history-stated@example.com",
                display_name="History Stated",
                first_payday=signup_day,
                history_opens_on=signup_day - timedelta(days=200),
            ))
            db.session.flush()

            assert pay_schedule_service.get_schedule(
                user.id,
            ).history_opens_on == signup_day - timedelta(days=200)

    def test_saying_nothing_stores_NULL_and_that_is_an_ANSWER(self, app, db):
        """The commonest sign-up, and the value every existing owner holds.

        NULL is not "unknown": the rhythm runs back to
        ``CALENDAR_DATE_MIN``, which is right for a job that predates the
        record and is what ruling R-IA rejected the no-column alternative for
        being unable to distinguish from the other case.
        """
        signup_day = display_today()
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="history-blank@example.com",
                display_name="History Blank",
                first_payday=signup_day,
                history_opens_on=None,
            ))
            db.session.flush()

            assert pay_schedule_service.get_schedule(
                user.id,
            ).history_opens_on is None

    def test_an_opening_ON_the_stated_payday_is_accepted(self, app, db):
        """The owner whose first paycheck is the one they just entered.

        ``pay_calendar:R-PC14`` calls this an ordinary state, and it is the
        state a backward projection with no stored bound could not express:
        it would fabricate an employment history for them.
        """
        signup_day = display_today()
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="history-equal@example.com",
                display_name="History Equal",
                first_payday=signup_day,
                history_opens_on=signup_day,
            ))
            db.session.flush()

            assert pay_schedule_service.get_schedule(
                user.id,
            ).history_opens_on == signup_day

    def test_an_opening_after_the_stated_payday_creates_NO_user(self, app, db):
        """Refused in the up-front block, before the ``User`` row is added.

        That placement is ``register_user``'s standing property and it is what
        this asserts: a validation firing later would leave a half-built owner
        in the session, protected only by nobody committing.  The same rule is
        asked again at the write door, of the payday the schedule RECORDS --
        one function, two sources -- and reaching THAT one here would be the
        defect.
        """
        signup_day = display_today()
        with app.app_context():
            with pytest.raises(ValidationError, match="cannot have started"):
                registration_service.register_user(registration_spec(
                    email="history-late@example.com",
                    display_name="History Late",
                    first_payday=signup_day - timedelta(days=3),
                    history_opens_on=signup_day,
                ))

            assert db.session.query(User).filter_by(
                email="history-late@example.com",
            ).first() is None

    def test_an_opening_outside_the_apps_calendar_creates_NO_user(
        self, app, db,
    ):
        """The storage bound, refused as a message rather than a 500.

        ``ck_pay_schedule_history_opens_range`` would raise an
        ``IntegrityError`` from inside the transaction; an HTML date input
        accepts a five-digit year, so this is the ordinary path.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="2100-12-31"):
                registration_service.register_user(registration_spec(
                    email="history-absurd@example.com",
                    display_name="History Absurd",
                    history_opens_on=date(9999, 1, 1),
                ))

            assert db.session.query(User).filter_by(
                email="history-absurd@example.com",
            ).first() is None

    def test_the_stated_opening_reaches_the_owners_CALENDAR(self, app, db):
        """End to end: the column the form writes is the bound the rhythm reads.

        The point of the whole step is that this number changes what the
        paycheck engine counts, so the seam between the stored fact and the
        loaded calendar is asserted rather than assumed -- a registration that
        stored the day and a loader that dropped it would look identical from
        the row.
        """
        signup_day = display_today()
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="history-loaded@example.com",
                display_name="History Loaded",
                first_payday=signup_day,
                history_opens_on=signup_day - timedelta(days=365),
            ))
            db.session.flush()

            assert pay_calendar.calendar_for(
                user.id,
            ).history_opens_on == (
                signup_day - timedelta(days=365)
            )
