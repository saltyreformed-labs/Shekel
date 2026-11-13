"""The schedule these tests rest on, the write window, the start period bound, the prediction."""

from __future__ import annotations

from datetime import date

from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import derived_span, populate_in_a_fresh_pass
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY_FIRST

from ._helpers import (
    _JANUARY_THIRD_PAYCHECK_INDEX,
    _LAST_SEEDED_INDEX,
    _make_template,
    _rows,
    _append_period,
)


class TestTheScheduleShapeTheseTestsRestOn:
    """The fixture facts every class below depends on, asserted not assumed."""

    def test_january_holds_three_paychecks(self, app, seed_user, seed_periods):
        """Index 2 (2026-01-30) is January's THIRD payday.

        The D25 class needs a third paycheck to exist; a fixture change that
        removed one would otherwise make those tests pass vacuously.
        """
        with app.app_context():
            january = [
                period for period in seed_periods
                if period.start_date.year == 2026
                and period.start_date.month == 1
            ]
            assert [derived_span(period).period_index for period in january] == [0, 1, 2]
            assert (
                seed_periods[_JANUARY_THIRD_PAYCHECK_INDEX].start_date
                == date(2026, 1, 30)
            )

    def test_appending_one_period_lands_in_an_already_covered_month(
        self, app, db, seed_user, seed_periods,
    ):
        """The appended period opens in MAY, which index 9 already covers.

        The D22 class needs the new period to fall in a month the rule has
        already fired in; on a schedule where it did not, no duplicate could
        arise and the test would prove nothing.
        """
        with app.app_context():
            assert seed_periods[_LAST_SEEDED_INDEX].start_date == date(2026, 5, 8)
            appended = _append_period(seed_user, seed_periods)
            assert appended[0].start_date == date(2026, 5, 22)
            assert appended[0].start_date.month == 5


class TestAnExtendDoesNotDuplicateAMonthlyFirstRow:
    """D22: the window's own first payday is not the owner's."""

    def test_appending_a_period_to_a_covered_month_adds_no_second_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A month already holding the rule's row gains nothing on an extend.

        ``Monthly First`` fires on the 1st of each month and places on the
        first PAYCHECK at or after it, so May 2026 is owed exactly one row --
        the 2026-05-08 one.  Resolved against the extend's own window the
        anchor became 2026-05-01 all over again, because 2026-05-22 is the
        window's earliest May payday, and the placement then produced a second
        row.  Measured on production: 3 such rows, $39.54 each.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, MONTHLY_FIRST,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()
            before = _rows(template, scenario_id)
            assert date(2026, 5, 8) in before, (
                "May's first paycheck must already hold the row, or the "
                "duplicate this test guards against cannot arise"
            )

            appended = _append_period(seed_user, seed_periods)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in appended})
            db.session.flush()

            after = _rows(template, scenario_id)
            may_rows = [
                start for start in after
                if start.year == 2026 and start.month == 5
            ]
            assert may_rows == [date(2026, 5, 8)], (
                f"May 2026 is owed one row and holds {sorted(may_rows)}"
            )
            assert len(after) == len(before)

    def test_a_month_the_extend_first_reaches_is_still_generated(
        self, app, db, seed_user, seed_periods,
    ):
        """The narrowing removes duplicates, not the rows that are owed.

        The paired half of the test above: an extend that opens a NEW calendar
        month must still generate that month's row, or the fix would have
        traded a duplicate for a silent omission.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, MONTHLY_FIRST,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()

            # Two appends: 2026-05-22 (May, covered) then 2026-06-05, which
            # opens June.
            first = _append_period(seed_user, seed_periods)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in first})
            second = _append_period(seed_user, seed_periods + first)
            assert second[0].start_date == date(2026, 6, 5)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in second})
            db.session.flush()

            after = _rows(template, scenario_id)
            assert date(2026, 6, 5) in after, (
                "June's first paycheck is owed a row and did not get one"
            )
            assert len(after[date(2026, 6, 5)]) == 1


class TestTheWindowStillNarrowsWhatIsWritten:
    """The schedule widened; the write window must not have."""

    def test_a_windowed_pass_writes_only_into_its_window(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-paycheck rule, generated into ONE period, writes one row.

        Resolving against the owner's whole schedule names all ten periods.
        Were the narrowing dropped, this pass would write ten rows -- and a
        real extend would re-walk every historical period on every run.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {seed_periods[4].id},
                ),
                scenario_id,
            )
            db.session.flush()

            assert len(created) == 1
            assert created[0].pay_period_id == seed_periods[4].id
            assert list(_rows(template, scenario_id)) == [
                seed_periods[4].start_date,
            ]


class TestTheStartPeriodBoundSurvivesTheWindow:
    """D2: the chosen "First paycheck" is part of the rule, not of the pass."""

    def test_a_windowed_pass_with_its_own_boundary_still_honours_the_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The batch-boundary shape: an explicit ``effective_from`` + a window.

        **This is the only shape that reproduces D2**, and getting that wrong
        cost this file a false claim.  A window alone does not: the pre-R4b-1
        code defaulted ``effective_from`` to the rule's start period whenever
        the caller passed ``None``, which reproduced the bound by accident.  It
        only vanished for a caller that supplied its OWN boundary -- and
        ``period_population`` supplies exactly that, the new batch's opening
        payday, on every schedule extend.

        With the caller's boundary set, the old code took the explicit value,
        resolved the rule against the window, failed to find start period 5 in
        it, dropped the bound entirely, and generated into a paycheck the user
        had excluded.  Resolved against the owner's schedule the start period
        is found and the bound holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                EVERY_PERIOD,
                starts_on=seed_periods[5].start_date,
            )
            window = seed_periods[2:4]

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {p.id for p in window},
                ),
                scenario_id,
                effective_from=window[0].start_date,
            )
            db.session.flush()

            assert created == []
            assert _rows(template, scenario_id) == {}

    def test_the_extend_path_itself_honours_the_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The same shape through the real repopulation entry point.

        ``populate_periods_from_active_templates`` is what an extend runs, so
        this drives D2's actual production path rather than a reconstruction
        of it.  A rule whose first paycheck is index 5 must gain nothing when
        periods 2-3 are (re)populated.

        **It said "and it defaults its boundary to the new batch's opening
        payday" until pay-calendar plan step C2-f3c**, which deleted that
        boundary as a provable no-op: every period of a write window ends on
        or after that window's own opening payday, so bounding placements by
        it could never drop one.  What holds this case up is therefore the
        RULE's own opening bound resolved against the owner's whole calendar,
        which is what D2 was about; the caller-supplied-boundary shape is
        still covered by the sibling case above, which passes one explicitly.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                EVERY_PERIOD,
                starts_on=seed_periods[5].start_date,
            )

            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in seed_periods[2:4]})
            db.session.flush()

            assert _rows(template, scenario_id) == {}
            assert scenario_id == seed_user["scenario"].id

    def test_the_same_rule_still_generates_from_its_stated_start_on(
        self, app, db, seed_user, seed_periods,
    ):
        """The bound is honoured, not merely restrictive.

        Without this the test above would pass against a rule that generates
        nothing at all.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                EVERY_PERIOD,
                starts_on=seed_periods[5].start_date,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {seed_periods[5].id},
                ),
                scenario_id,
            )
            db.session.flush()

            assert [row.pay_period_id for row in created] == [
                seed_periods[5].id,
            ]


class TestThePredictionIsTheGenerationCall:
    """The engine's prediction once answered from a one-period schedule.

    The prediction is ``rows_to_generate_in_period`` since plan#2735 deleted
    ``can_generate_in_period``, the bool this class graded until then.
    """

    def test_the_predictor_agrees_with_generation_in_every_period(
        self, app, db, seed_user, seed_periods,
    ):
        """For a ``Monthly First`` rule the two answers must coincide.

        The predictor drives the carry-forward GENERATE branch.  Reading a
        one-period schedule it saw one month holding one payday and answered
        "the engine fires here" for EVERY period: measured on production, 32 of
        61 for the live ``Phone Allowance`` rule, where the true answer is each
        month's first paycheck only.

        ONE schedule serves all ten questions, which is also the shape the
        carry-forward context now threads: building one per call would repeat
        the schedule query and the forward walk once per envelope row.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, MONTHLY_FIRST,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id))

            predicted = [
                period.start_date for period in seed_periods
                if recurrence_engine.rows_to_generate_in_period(
                    template, period.id, scenario_id, schedule=schedule,
                )
            ]

            recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            generated = sorted(_rows(template, scenario_id))

            assert predicted == generated
            # Stated absolutely too: two equal-but-wrong answers would satisfy
            # the comparison above.  Each 2026 month in the fixture window is
            # owed exactly one row, on its FIRST payday.
            assert generated == [
                date(2026, 1, 2), date(2026, 2, 13), date(2026, 3, 13),
                date(2026, 4, 10), date(2026, 5, 8),
            ]
