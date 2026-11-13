"""Tests for the typed recurrence submission (plan step R7e, finding D36).

:class:`~app.services.recurrence.RecurrenceSubmission` is what one recurrence
form submission STATES: each field stated, cleared, or not mentioned
(:data:`~app.services.recurrence.UNSET`).  Its :meth:`applied_to` is the one
merge of a submission onto a stored rule -- the submitted value where the form
stated one, the stored value where it did not -- and :meth:`authored` is what a
create authors from it.

Each case authors a real rule through the write door and reads it back with
:func:`~app.services.recurrence.recurrence_spec`, so "the stored value rides
through" is graded against what the row actually holds rather than against the
fixture's own arguments.
"""
from dataclasses import replace
from datetime import date

import pytest

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.ref import RecurrenceUnit
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    UNSET,
    EndsAfterOccurrences,
    EndsOnDate,
    MonthReading,
    RecurrenceResolutionError,
    RecurrenceSpec,
    RecurrenceSubmission,
    SubmittedCadence,
    WeekdayReading,
    author_rule,
    recurrence_spec,
)
from tests._test_helpers import bare_expense_template

#: The stored rule's first occurrence: Tuesday 2026-02-10, the month's SECOND
#: Tuesday, so it can be read either by its day (the 10th) or by its weekday.
_STORED_START = date(2026, 2, 10)

#: The stored rule's stop, a real date so "kept" and "replaced" differ.
_STORED_STOP = EndsOnDate(on=date(2026, 12, 31))

_MONTHLY = SubmittedCadence(
    unit=RecurrenceUnitEnum.MONTH,
    interval_n=1,
    placement=PeriodPlacementEnum.CONTAINING_DATE,
)

_EVERY_PAYCHECK = SubmittedCadence(
    unit=RecurrenceUnitEnum.PERIOD,
    interval_n=1,
    placement=PeriodPlacementEnum.CONTAINING_DATE,
)


def _stored_monthly_rule(db, seed_user, *, weekday_reading=WeekdayReading.FROM_START):
    """Author and flush a monthly rule read by its WEEKDAY, with a stop.

    "The second Tuesday of every month from 2026-02-10 until 2026-12-31" --
    a stored reading, a stored start and a stored bound, each distinct from
    what any case below states, so each case can tell kept from replaced.

    Args:
        db: The session fixture.
        seed_user: The owner fixture.
        weekday_reading: The stored reading; ``None`` reads the date by its
            day of the month, which a case re-pointing the row's unit onto
            one with no day of the month needs (the reading's CHECK refuses
            it there).

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    user_id = seed_user["user"].id
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=_STORED_START,
            interval_n=1,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            weekday_reading=weekday_reading,
            end_bound=_STORED_STOP,
        ),
        calendar_for(user_id),
        bare_expense_template(db.session, seed_user),
    )
    db.session.flush()
    return rule


def _stored_capped_paycheck_rule(db, seed_user):
    """Author and flush "every paycheck, at most 2 a month".

    Args:
        db: The session fixture.
        seed_user: The owner fixture.

    Returns:
        The flushed rule, whose stored ceiling is 2.
    """
    user_id = seed_user["user"].id
    calendar = calendar_for(user_id)
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=calendar.opening_bound(),
            interval_n=1,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            max_per_month=2,
        ),
        calendar,
        bare_expense_template(db.session, seed_user, "Capped Line"),
    )
    db.session.flush()
    return rule


@pytest.mark.usefixtures("seed_periods")
class TestAnUnmentionedFieldRidesThrough:
    """A field the form did not mention keeps the value the rule stores."""

    def test_a_cadence_alone_restates_the_rule_exactly(
        self, seed_user, db,
    ):
        """Restating the stored cadence and nothing else changes nothing.

        The amount-only edit's shape on a form that posts the cadence row: the
        start, its reading and the stop are all unmentioned, so the spec the
        rule re-authors is the one it already authors -- every field of it.
        """
        rule = _stored_monthly_rule(db, seed_user)

        applied = RecurrenceSubmission(cadence=_MONTHLY).applied_to(rule)

        assert applied == recurrence_spec(rule)
        assert applied.starts_on == _STORED_START
        assert applied.weekday_reading is WeekdayReading.FROM_START
        assert applied.end_bound == _STORED_STOP

    def test_a_reading_with_no_start_is_not_applied(
        self, seed_user, db,
    ):
        """"Repeating on" rides on the date, so alone it moves nothing.

        It still counts as a stated bound -- the set a loan's standing payment
        may not state -- because a reading states the start as surely as the
        date does.
        """
        rule = _stored_monthly_rule(db, seed_user)
        submission = RecurrenceSubmission(
            cadence=_MONTHLY, reading=MonthReading(),
        )

        applied = submission.applied_to(rule)

        assert applied.starts_on == _STORED_START
        assert applied.weekday_reading is WeekdayReading.FROM_START
        assert submission.states_a_bound


@pytest.mark.usefixtures("seed_periods")
class TestAStatedFieldReplacesTheStoredOne:
    """A field the form stated replaces the stored value, whatever it was."""

    def test_a_stated_start_is_read_by_its_own_day_by_default(
        self, seed_user, db,
    ):
        """A new date with no reading drops the stored weekday reading.

        The date and its reading are one statement: a stored "second Tuesday"
        surviving beside a new date would read a weekday nobody chose for it.
        """
        rule = _stored_monthly_rule(db, seed_user)

        applied = RecurrenceSubmission(
            cadence=_MONTHLY, starts_on=date(2026, 3, 5),
        ).applied_to(rule)

        assert applied.starts_on == date(2026, 3, 5)
        assert applied.weekday_reading is None
        assert applied.nominal_day is None
        assert applied.end_bound == _STORED_STOP

    def test_a_stated_start_carries_its_stated_reading(
        self, seed_user, db,
    ):
        """Thursday 2026-03-05 read as "the first Thursday" is stored so."""
        rule = _stored_monthly_rule(db, seed_user)

        applied = RecurrenceSubmission(
            cadence=_MONTHLY,
            starts_on=date(2026, 3, 5),
            reading=MonthReading(weekday_reading=WeekdayReading.FROM_START),
        ).applied_to(rule)

        assert applied.starts_on == date(2026, 3, 5)
        assert applied.weekday_reading is WeekdayReading.FROM_START

    @pytest.mark.parametrize(
        "stated",
        [NEVER_ENDS, EndsAfterOccurrences(count=3), EndsOnDate(on=date(2026, 6, 30))],
    )
    def test_a_stated_stop_replaces_the_stored_one(
        self, seed_user, db, stated,
    ):
        """"Never" is a stated stop too: it clears the stored date."""
        rule = _stored_monthly_rule(db, seed_user)

        applied = RecurrenceSubmission(
            cadence=_MONTHLY, end_bound=stated,
        ).applied_to(rule)

        assert applied.end_bound == stated
        assert applied.starts_on == _STORED_START

    def test_the_stated_cadence_replaces_the_stored_one(
        self, seed_user, db,
    ):
        """Every 3 months, funded from the paycheck starting on or after the date."""
        rule = _stored_monthly_rule(db, seed_user)
        quarterly = SubmittedCadence(
            unit=RecurrenceUnitEnum.MONTH,
            interval_n=3,
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        )

        applied = RecurrenceSubmission(cadence=quarterly).applied_to(rule)

        assert (applied.unit, applied.interval_n, applied.placement) == (
            RecurrenceUnitEnum.MONTH, 3, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        )


@pytest.mark.usefixtures("seed_periods")
class TestThePerMonthCeiling:
    """The ceiling's three states: stated, cleared, and not mentioned."""

    @pytest.mark.parametrize(
        ("stated", "expected"),
        [(UNSET, 2), (None, None), (1, 1)],
        ids=["not-mentioned-keeps", "cleared-clears", "stated-replaces"],
    )
    def test_each_state_of_the_box(
        self, seed_user, db, stated, expected,
    ):
        """The stored ceiling is 2; the box decides what the rule carries."""
        rule = _stored_capped_paycheck_rule(db, seed_user)

        applied = RecurrenceSubmission(
            cadence=replace(_EVERY_PAYCHECK, max_per_month=stated),
        ).applied_to(rule)

        assert applied.max_per_month == expected

    def test_an_unmentioned_ceiling_is_dropped_under_a_monthly_cadence(
        self, seed_user, db,
    ):
        """A monthly cadence fires once a month, so a stored 2 cannot ride.

        The form disables the box beside a monthly unit, so switching a capped
        paycheck line to monthly posts no ceiling at all; the stored one is
        dropped rather than carried into the pair ``RecurrenceSpec`` refuses.
        """
        rule = _stored_capped_paycheck_rule(db, seed_user)

        applied = RecurrenceSubmission(
            cadence=_MONTHLY, starts_on=date(2026, 3, 5),
        ).applied_to(rule)

        assert applied.max_per_month is None


@pytest.mark.usefixtures("seed_periods")
class TestTheMergeReadsNoStoredCadence:
    """An unreadable stored cadence still reaches its repair save."""

    def test_a_rule_whose_unit_is_unmodelled_is_re_pointed(
        self, seed_user, db,
    ):
        """The form tells the user to choose a cadence; choosing one works.

        The stored unit is a real ``ref.recurrence_units`` row no enum member
        names -- a seed the enums diverged from -- so reading the rule's own
        cadence raises.  The merge reads with the STATED one and does not.
        """
        rule = _stored_monthly_rule(db, seed_user, weekday_reading=None)
        surplus = RecurrenceUnit(name="Blue Moon")
        db.session.add(surplus)
        db.session.flush()
        rule.unit_id = surplus.id
        rule.unit_has_day_of_month = surplus.has_day_of_month
        db.session.flush()
        with pytest.raises(RecurrenceResolutionError):
            recurrence_spec(rule)

        applied = RecurrenceSubmission(cadence=_MONTHLY).applied_to(rule)

        assert applied.unit is RecurrenceUnitEnum.MONTH
        assert applied.starts_on == _STORED_START


@pytest.mark.usefixtures("seed_periods")
class TestOnlyANamedCadenceIsApplied:
    """A submission naming no cadence re-authors nothing."""

    @pytest.mark.parametrize("cadence", [None, UNSET], ids=["cleared", "unmentioned"])
    def test_applying_one_is_a_callers_broken_invariant(
        self, seed_user, db, cadence,
    ):
        """Raised, naming the state, rather than authoring a guessed cadence."""
        rule = _stored_monthly_rule(db, seed_user)

        with pytest.raises(ValueError, match="only a submission that names a cadence"):
            RecurrenceSubmission(cadence=cadence).applied_to(rule)


class TestACreateAuthorsFromTheSubmission:
    """What a create authors: the stated fields, and the defaults for the rest."""

    def test_an_unmentioned_field_takes_the_create_default(self, seed_user):
        """Read by its own day, no ceiling, never ends."""
        user_id = seed_user["user"].id

        spec = RecurrenceSubmission(
            cadence=_EVERY_PAYCHECK, starts_on=date(2026, 1, 16),
        ).authored(user_id)

        assert spec == RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=date(2026, 1, 16),
            interval_n=1,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            nominal_day=None,
            weekday_reading=None,
            max_per_month=None,
            end_bound=NEVER_ENDS,
        )

    def test_every_stated_field_is_authored(self, seed_user):
        """A capped paycheck line that stops after 6 occurrences."""
        user_id = seed_user["user"].id

        spec = RecurrenceSubmission(
            cadence=replace(_EVERY_PAYCHECK, max_per_month=2),
            starts_on=date(2026, 1, 16),
            end_bound=EndsAfterOccurrences(count=6),
        ).authored(user_id)

        assert spec.max_per_month == 2
        assert spec.end_bound == EndsAfterOccurrences(count=6)

    @pytest.mark.parametrize("cadence", [None, UNSET], ids=["cleared", "unmentioned"])
    def test_no_named_cadence_authors_no_rule(self, cadence):
        """"Does not repeat", and a form silent about the cadence, author none."""
        assert RecurrenceSubmission(
            cadence=cadence, starts_on=date(2026, 1, 16),
        ).authored(1) is None

    def test_an_unmentioned_start_is_refused_by_the_spec(self):
        """The spec names the missing first occurrence rather than guessing one."""
        with pytest.raises(RecurrenceResolutionError, match="states no starts_on"):
            RecurrenceSubmission(cadence=_MONTHLY).authored(1)


class TestThePredicates:
    """The questions a door asks of a submission, each spelled once."""

    @pytest.mark.parametrize(
        ("submission", "mentions", "clears", "named"),
        [
            (RecurrenceSubmission(), False, False, None),
            (RecurrenceSubmission(cadence=None), True, True, None),
            (RecurrenceSubmission(cadence=_MONTHLY), True, False, _MONTHLY),
        ],
        ids=["unmentioned", "does-not-repeat", "named"],
    )
    def test_the_cadence_questions(self, submission, mentions, clears, named):
        """Not mentioned, cleared and named are three different answers."""
        assert submission.mentions_the_cadence is mentions
        assert submission.clears_the_recurrence is clears
        assert submission.named_cadence == named

    @pytest.mark.parametrize(
        ("submission", "states"),
        [
            (RecurrenceSubmission(cadence=_MONTHLY), False),
            (RecurrenceSubmission(starts_on=date(2026, 3, 5)), True),
            (RecurrenceSubmission(reading=MonthReading()), True),
            (RecurrenceSubmission(end_bound=NEVER_ENDS), True),
        ],
        ids=["cadence-only", "a-start", "a-lone-reading", "never-ends"],
    )
    def test_which_submissions_state_a_bound(self, submission, states):
        """A stated "Never" and a lone reading each state a bound."""
        assert submission.states_a_bound is states

    def test_the_over_readers_fall_back_to_the_stored_value(self):
        """Not mentioned reads as the stored value; stated reads as itself."""
        silent = RecurrenceSubmission()
        stated = RecurrenceSubmission(
            starts_on=date(2026, 3, 5), end_bound=NEVER_ENDS,
        )

        assert silent.starts_on_over(_STORED_START) == _STORED_START
        assert silent.end_bound_over(_STORED_STOP) == _STORED_STOP
        assert silent.end_bound_over(None) is None
        assert stated.starts_on_over(_STORED_START) == date(2026, 3, 5)
        assert stated.end_bound_over(_STORED_STOP) == NEVER_ENDS
