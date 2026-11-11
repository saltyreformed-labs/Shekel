"""The seeded owner's pay calendar is written by the doors that own it.

Plan step **pay_calendar:C4-b-1**.  ``tests/conftest.py`` used to build the
seeded owner's pay periods by hand -- ``PayPeriod(user_id=..., start_date=...,
end_date=..., period_index=...)`` in three fixtures -- and then un-build them by
hand, in a 135-line helper that deleted the opening period, renumbered every
survivor with raw SQL and re-posted the anchor corrections that delete had
disposed.

Two things were wrong with that and both are properties, not opinions.

**A hand-written period has no ``budget.pay_schedule`` row beside it.**  No
application door can produce that state: ``pay_period_write.record_paydays``
upserts the owner's cadence in the same call that records a payday (the cadence
rule, plan step C3-b), and ``registration_service.register_user`` reaches the table only
through it.  So the seeded owner was the one shape production does not have --
paydays with no recorded cadence, pay-calendar finding **P8** -- and that shape
is what plan step ``pay_calendar:C4-b`` makes unconstructible with a foreign
key.  :class:`TestEverySeededOwnerHasARecordedCadence` is the control for it,
and it is the one class here that FAILS on the tree this step replaced.

**The two columns a fixture set by hand are the two this arc deletes.**
``end_date`` and ``period_index`` are derived from the payday set
(``docs/plans/implementation_plan_pay_calendar.md`` section 1); a fixture
computing them is a second implementation of the derivation, and the raw
``SET period_index = period_index - 1`` the deleted helper ran was a third.

The remaining classes are REGRESSION guards rather than discriminators: they
pass on both trees, and they are here because this step changed WHO writes
these rows and the rows themselves had to be measured not to move.
"""

from datetime import timedelta

import pytest

from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.services import pay_schedule_service
from tests._test_helpers import (
    derived_span,
    governing_opening_row,
    last_covered_day,
)
from tests._seed_owner_fixtures import (
    SEED_USER_BOOTSTRAP_START,
    SEED_USER_CADENCE_DAYS,
    _reset_seed_calendar,
)


def _owners_holding_paydays_without_a_schedule(db):
    """Return every ``user_id`` with a pay period and no schedule row.

    The predicate plan step ``pay_calendar:C4-b`` turns into a foreign key.
    Asked of the WHOLE database rather than of one fixture's owner, so a second
    owner some fixture builds on the side is in the answer too.

    Args:
        db: The Flask-SQLAlchemy extension.

    Returns:
        The offending user ids, ascending.
    """
    return sorted(
        row[0] for row in
        db.session.query(PayPeriod.user_id)
        .outerjoin(PaySchedule, PaySchedule.user_id == PayPeriod.user_id)
        .filter(PaySchedule.user_id.is_(None))
        .distinct()
        .all()
    )


class TestEverySeededOwnerHasARecordedCadence:
    """No fixture owner holds a payday without a ``budget.pay_schedule`` row.

    **The discriminating class, and it takes TWO mutations because its two
    halves are fed by two different fixtures.**  Measured 2026-09-01, and the
    fail sets are DISJOINT -- a first draft of this paragraph claimed all four
    cases fail on the tree this step replaced, and three of them do:

    * make the OPENING payday a hand-built row again (no schedule row beside
      it, the pre-step shape) and the three single-owner cases fail while
      ``test_no_owner_anywhere_holds_paydays_without_one`` still PASSES -- each
      periods fixture resets the calendar through the writer, which upserts the
      row the bootstrap withheld;
    * strip the schedule row after each calendar RESET and only those four
      parametrisations fail.

    So neither mutation alone grades this class, and the whole-database case
    below is a statement about the state after a periods fixture rather than
    about the bootstrap.
    """

    def test_the_seeded_owner_has_one(self, app, db, seed_user):  # pylint: disable=unused-argument
        """``seed_user`` alone -- no periods fixture -- carries the row."""
        schedule = pay_schedule_service.get_schedule(seed_user["user"].id)
        assert schedule is not None, (
            "the seeded owner holds a payday and no budget.pay_schedule row, "
            "which is pay-calendar finding P8 and a state no application door "
            "can produce"
        )
        assert pay_schedule_service.ScheduleFacts.of(
            schedule,
        ).rhythm.cadence.days == SEED_USER_CADENCE_DAYS

    def test_the_cadence_is_READ_rather_than_inferred(
        self, app, db, seed_user,  # pylint: disable=unused-argument
    ):
        """``resolve_schedule`` answers from the stored row, not the fallback.

        The distinction is the whole point of the step: ``resolve_schedule``
        returns the same 14 either way, so asserting the ANSWER proves nothing.
        What is asserted is that the row exists, which is the branch it takes.
        """
        assert pay_schedule_service.get_schedule(seed_user["user"].id) is not None
        assert pay_schedule_service.resolve_schedule(
            seed_user["user"].id,
        ).rhythm.cadence.days == SEED_USER_CADENCE_DAYS

    @pytest.mark.parametrize("periods_fixture", [
        "seed_periods", "seed_periods_today", "seed_periods_52",
        "seed_second_periods",
    ])
    def test_no_owner_anywhere_holds_paydays_without_one(
        self, request, app, db, periods_fixture,  # pylint: disable=unused-argument
    ):
        """The predicate C4-b-2 makes a foreign key holds after each world.

        Over the WHOLE database, so the second owner a two-user fixture builds
        is graded with the first.  **It does NOT grade the bootstrap** -- see
        the class docstring's second mutation -- because every one of these
        fixtures resets the calendar through the writer, which would restore a
        schedule row the opening payday had failed to write.
        """
        request.getfixturevalue(periods_fixture)
        assert _owners_holding_paydays_without_a_schedule(db) == []

    def test_the_second_owners_have_one_too(
        self, app, db, second_user, seed_second_user,  # pylint: disable=unused-argument
    ):
        """Both isolation owners carry a row, built by the same helper."""
        for owner in (second_user, seed_second_user):
            assert pay_schedule_service.get_schedule(
                owner["user"].id,
            ) is not None


class TestTheOpeningPeriodIsTheRowItAlwaysWas:
    """The seeded owner's opening period did not move when its writer changed.

    A regression guard: it passes on both trees.  It is here because the step
    replaced a hand-computed ``end_date`` and ``period_index`` with the
    writer's derivation, and "byte-identical" was a claim that had to be
    measured.
    """

    def test_the_opening_period_is_unchanged(
        self, app, db, seed_user,  # pylint: disable=unused-argument
    ):
        """One period, opening on the contract day, ending 13 days later."""
        periods = (
            db.session.query(PayPeriod)
            .filter_by(user_id=seed_user["user"].id)
            .order_by(PayPeriod.start_date)
            .all()
        )
        assert len(periods) == 1
        assert periods[0].start_date == SEED_USER_BOOTSTRAP_START
        assert last_covered_day(periods[0]) == (
            SEED_USER_BOOTSTRAP_START
            + timedelta(days=SEED_USER_CADENCE_DAYS - 1)
        )
        assert derived_span(periods[0]).period_index == 0
        assert periods[0].id == seed_user["bootstrap_period"].id


class TestTheResetLeavesTheWritersDerivation:
    """A periods fixture returns the whole calendar, indexed from zero.

    The deleted helper produced this by RENUMBERING: it recorded the new batch
    beside the opening period, took the opening period out, and pulled every
    survivor's ``period_index`` down by one in raw SQL.  The reset door retires
    and records in one ``record_paydays`` call, so the writer derives 0..N-1
    when it writes them and nothing renumbers anything.
    """

    def test_the_calendar_is_exactly_what_was_asked_for(
        self, app, db, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Ten periods, no residue of the opening one."""
        assert len(seed_periods) == 10
        assert [derived_span(period).period_index for period in seed_periods] == list(range(10))
        assert SEED_USER_BOOTSTRAP_START not in {
            period.start_date for period in seed_periods
        }
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id,
        ).count() == 10

    def test_the_fixture_returns_its_periods_in_PAYDAY_order(
        self, app, db, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The list a fixture hands a test is sorted by payday.

        Every case that indexes ``seed_periods[3]`` means "the fourth
        paycheck", so the order the fixture returns is load-bearing rather than
        incidental.

        **It asserted the ORDINAL order beside this until plan step
        ``pay_calendar:C4-c``, and that second assertion became a theorem**
        (adversarial review, 2026-09-01).  ``period_index`` is now the payday's
        position in the owner's sorted set (``_derive.derive_periods``), and
        ``uq_pay_periods_user_start`` makes paydays unique -- so the ordinal is
        a strictly increasing function of ``start_date`` and sorting by one
        cannot differ from sorting by the other, for any input.  What that
        assertion used to grade was a STORED ordinal agreeing with payday
        order, which is finding **P1**'s defect and is not expressible now.
        """
        assert seed_periods == sorted(
            seed_periods, key=lambda period: period.start_date,
        )


class TestTheResetDoesNotMoveTheBooks:
    """A calendar reset leaves the seeded account's opening day where it was.

    **This is the measurement behind deleting a call, not an argument for it.**
    The removed helper restated the books to
    ``min(governing.opened_on, new_anchor.start_date - 1 day)`` -- backward
    only.  Every owner ``tests/conftest.py`` builds opens its books the day
    before :data:`SEED_USER_BOOTSTRAP_START`, and every calendar the periods
    fixtures ask for starts later than that, so the ``min`` always chose the
    day already stored and the call could not move a day in any world this
    file builds.  These assert the day directly, so a future reset that DID
    move it fails here rather than in whatever balance case noticed first.
    """

    def test_the_books_open_the_day_before_the_opening_payday(
        self, app, db, seed_user,  # pylint: disable=unused-argument
    ):
        """The pre-reset state, so the post-reset assertion has a baseline."""
        governing = governing_opening_row(db.session, seed_user["account"])
        assert governing is not None
        assert governing.opened_on == SEED_USER_BOOTSTRAP_START - timedelta(days=1)

    def test_a_later_calendar_does_not_move_them(
        self, app, db, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """After a reset to 2026, the books still open in 2024."""
        governing = governing_opening_row(db.session, seed_user["account"])
        assert governing is not None
        assert governing.opened_on == SEED_USER_BOOTSTRAP_START - timedelta(days=1)
        assert governing.opened_on < seed_periods[0].start_date


class TestACalendarMayNotOpenAtOrBeforeTheBooks:
    """The guard that replaced two accidental protections, SHOWN to fire.

    **An adversarial review of this step found that it had removed a guard and
    its compensator in one commit.**  While the periods fixtures APPENDED
    beside the owner's opening payday, ``pay_period_batch.
    reject_backward_payday`` refused any first payday earlier than one cadence
    after it; and the books restatement :func:`_reset_seed_calendar` argues
    against carrying over would have moved the books back to meet an earlier
    one anyway.  The reset door retires every surviving payday in the same call
    that records the new batch, so that refusal returns early on an empty
    surviving set and every opening day became legal.

    A pay period opening at or before its owner's books contradicts ruling
    ``balance:R-HG`` -- an opening equity is the CLOSING balance for its own
    day.  No live caller reaches it, which is exactly why it needs a case:
    ``verification.md`` item 4 says a guard whose control does not fire is not
    a guard.
    """

    def test_the_guard_refuses_a_calendar_opening_before_the_books(
        self, app, db, seed_user,  # pylint: disable=unused-argument
    ):
        """Ask for a calendar opening the day the books open; be refused."""
        governing = governing_opening_row(db.session, seed_user["account"])
        assert governing is not None
        with pytest.raises(AssertionError, match="books open"):
            _reset_seed_calendar(
                seed_user, governing.opened_on, 10, SEED_USER_CADENCE_DAYS,
            )

    def test_it_admits_the_first_day_it_legally_can(
        self, app, db, seed_user,  # pylint: disable=unused-argument
    ):
        """The day AFTER the books open is legal, so the bound is not too wide.

        The second direction, which the refusal alone does not establish: a
        guard that refused everything would pass the case above.
        """
        governing = governing_opening_row(db.session, seed_user["account"])
        periods = _reset_seed_calendar(
            seed_user,
            governing.opened_on + timedelta(days=1),
            2,
            SEED_USER_CADENCE_DAYS,
        )
        assert len(periods) == 2
        assert periods[0].start_date == governing.opened_on + timedelta(days=1)
