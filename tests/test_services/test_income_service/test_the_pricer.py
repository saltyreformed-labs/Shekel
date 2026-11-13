"""The pricer answers past the saved horizon, prices a payday once, refuses another owner."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.services.pay_calendar import calendar_for, paydays_in_year_before
from app.services import income_service
from tests._test_helpers import counting_calls, pricing_over

from ._helpers import _create_profile


class TestThePricerAnswersPastTheSavedHORIZON:
    """A projected payday is priced by the same code and the same rules.

    **The capability plan step salary:S3-d exists for, and nothing else in the
    tree exercises it.**  Every production caller of
    :meth:`~app.services.income_service.ProfilePaychecks.over` hands it
    ``calendar.saved()``, so without this class the whole argument for the
    step -- that the engine's four calendar judgements come off
    ``basis.calendar``, which runs forward at the owner's cadence past the
    schedule's horizon -- would be asserted in a docstring and executed zero
    times.  Plan step **salary:S3-e** deletes
    :class:`~app.services.investment_projection.AccountPayrollFeed`'s hold on
    the strength of it, so it is graded here first.

    An adversarial review of S3-d found the gap; these are its cases.
    """

    @staticmethod
    def _projected(calendar, days_past_horizon):
        """Return the first projected payday at least *days_past_horizon* out.

        Taken off :meth:`~app.services.pay_calendar.PayCalendar.axis`, the
        producer that projects at the recorded cadence, rather than computed
        here -- an oracle that stepped the cadence itself would be a second
        spelling of the rule under test.
        """
        horizon = calendar.horizon()
        wanted = horizon + timedelta(days=days_past_horizon)
        beyond = calendar.axis(
            calendar.opening_bound(), wanted + timedelta(days=400),
        )
        return next(p for p in beyond if p.start_date > wanted)

    def test_a_projected_payday_prices_and_says_it_has_no_ROW(
        self, app, db, seed_user, seed_periods,
    ):
        """It answers a paycheck, and its ``period_id`` is ``None``.

        Both halves matter.  The figure proves the engine ran rather than
        refusing; the ``None`` proves the paycheck still says it is not a row
        a ``transactions.pay_period_id`` can point at -- which is the whole
        reason :attr:`~app.services.paycheck_calculator.PeriodInfo.period_id`
        was widened rather than dropped.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            projected = self._projected(calendar, 0)
            breakdown = pricing_over(
                calendar,
            ).for_profile(profile).at(projected)

            assert breakdown.period.period_id is None
            assert breakdown.period.payday == projected.start_date
            # $104,000 over 26 paychecks, no raise and no deduction, so the
            # gross is the same rate every payday in every year.
            assert breakdown.earnings.gross_biweekly == Decimal("4000.00")

    def test_the_wage_base_CUMULATIVE_runs_past_the_horizon(
        self, app, db, seed_user, seed_periods,
    ):
        """A projected payday's year-to-date is counted, not reset to zero.

        **This is the case that could not fail on a docstring.**  The FICA
        Social Security cumulative is the judgement most visibly wrong if the
        rhythm stopped at the schedule's horizon: a projected payday would see
        an empty year, so the wage base would never be reached and Social
        Security would be charged on wages above it.

        Graded through the engine's own producer rather than through a second
        spelling: the paydays the cumulative walks are
        :func:`~app.services.pay_calendar.paydays_in_year_before`'s answer, so
        the assertion is that a projected payday's count matches what that
        producer reports for it and is not zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            horizon = calendar.horizon()
            # A payday in a calendar year the SAVED schedule never reaches, so
            # every payday of its year is itself a projection.  The first
            # projected payday would not do: its year is still full of saved
            # ones, and a rhythm that stopped at the horizon would answer the
            # same non-empty list.  A first draft of this case asserted on it
            # and could not fail.
            deep = self._projected(calendar, 400)
            assert deep.start_date.year > horizon.year, (
                f"payday {deep.start_date} is in the horizon's own year "
                f"({horizon}); this case needs one past it"
            )
            earlier = paydays_in_year_before(calendar, deep.start_date)

            assert earlier, (
                "the rhythm reported NO payday before a projected one in its "
                "own calendar year -- a cumulative reset to zero past the "
                "horizon, which is what charges Social Security above the "
                "wage base for the whole tail"
            )
            assert earlier[-1] < deep.start_date
            assert min(earlier) > horizon, (
                "every payday of this year is past the saved schedule, so "
                "the cumulative walked here is entirely projected"
            )
            # And the paycheck itself prices, on that same forward rhythm.
            assert pricing_over(calendar).for_profile(
                profile,
            ).at(deep).earnings.gross_biweekly == Decimal("4000.00")

    def test_a_projected_payday_and_a_saved_one_agree_on_the_RATE(
        self, app, db, seed_user, seed_periods,
    ):
        """Same salary, same cadence, no raise between -> the same gross.

        The invariance control for the widening: nothing about crossing the
        schedule's horizon may change what the job pays, because the gross is
        a function of the salary and the cadence alone (ruling
        **balance:R-HW**).  A projected payday reading a different rate would
        mean the pricer had lost the profile or the cadence past the horizon.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            paychecks = pricing_over(calendar)
            saved = paychecks.for_profile(profile).at(calendar.saved()[-1])
            projected = paychecks.for_profile(profile).at(
                self._projected(calendar, 0),
            )

            assert projected.earnings.gross_biweekly == (
                saved.earnings.gross_biweekly
            )


class TestAPaydayIsPricedONCEPerPricer:
    """The per-payday memo is load bearing, so it is measured rather than read.

    **Plan step salary:S3-d made this hotter, not cooler.**
    :meth:`~app.services.income_service.SalaryPricing.net_for` prices the one
    period a ROW names, so it is called once per salary row where the producer
    it replaced ran once per profile.  If
    :meth:`~app.services.income_service.ProfilePaychecks.over`'s memo silently
    stopped hitting, every other test in this suite would still pass and every
    salary row on the grid would re-run the engine -- restoring exactly the
    cost this step removes, invisibly.

    ``TestOnePaycheckProjectionPerProfilePerRender`` cannot see it: that gate
    counts PRICERS built per render, and one pricer with a broken memo builds
    exactly one.  An adversarial review of S3-d found the hole.
    """

    def test_asking_twice_prices_once(self, app, db, seed_user, seed_periods):
        """A second ask over the same window runs the engine zero more times."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            saved = list(calendar.saved())
            paychecks = pricing_over(calendar)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                paychecks.for_profile(profile).over(saved)
                first = counts["calculate_paycheck"]
                paychecks.for_profile(profile).over(saved)
                paychecks.for_profile(profile).over(saved[:3])
                paychecks.for_profile(profile).at(saved[0])

            assert first == len(saved), (
                f"the first ask priced {first} paychecks over a "
                f"{len(saved)}-payday window; it must price each exactly once"
            )
            assert counts["calculate_paycheck"] == first, (
                f"re-asking priced {counts['calculate_paycheck'] - first} "
                "more paychecks; every payday after the first ask is a memo "
                "hit and the engine must not run again"
            )

    def test_an_overlapping_ask_prices_only_the_DIFFERENCE(
        self, app, db, seed_user, seed_periods,
    ):
        """Two overlapping spans cost the union, never the overlap twice."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            saved = list(calendar.saved())
            assert len(saved) >= 4, "fixture too short to overlap two spans"
            paychecks = pricing_over(calendar)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                paychecks.for_profile(profile).over(saved[:3])
                paychecks.for_profile(profile).over(saved[1:4])

            assert counts["calculate_paycheck"] == 4, (
                f"two spans covering 4 distinct paydays priced "
                f"{counts['calculate_paycheck']} paychecks; the 2 they share "
                "must be priced once"
            )

    def test_a_payday_named_twice_in_ONE_ask_prices_once(
        self, app, db, seed_user, seed_periods,
    ):
        """A duplicate inside one call misses the memo and must still price once.

        The memo makes a REPEAT ask free; two periods naming one payday inside
        a SINGLE ask would both miss it, so the dedupe is a separate rule and
        this is its case.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            first = calendar.saved()[0]
            paychecks = pricing_over(calendar)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                answers = paychecks.for_profile(profile).over(
                    [first, first, first],
                )

            assert counts["calculate_paycheck"] == 1, (
                f"one payday named three times priced "
                f"{counts['calculate_paycheck']} paychecks"
            )
            assert len(answers) == 3
            assert answers[0] is answers[1] is answers[2]


class TestThePricerREFUSESAMismatchedOwner:
    """A profile and a calendar from two owners is refused, not answered.

    **The mispairing is SILENT without the refusal**, which is why it is a
    case: the profile would be priced as one owner's (its owner's tax rows
    loaded, until plan step salary:X-at-1 moved the law into the code) while
    every payday came from the other's schedule, and the engine's own cross-owner guard
    (``paycheck_calculator._month_ordinal``) cannot fire, because it refuses a
    payday its calendar cannot PLACE and a calendar places its own paydays
    perfectly well.  An adversarial review of plan step salary:S3-d found the
    unguarded pairing.
    """

    def test_a_foreign_calendar_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Pricing one owner's profile against another's calendar raises."""
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()
            foreign = calendar_for(seed_second_user["user"].id)

            with pytest.raises(ValueError, match="belongs to user"):
                pricing_over(foreign).for_profile(profile)

    def test_a_source_answering_another_owners_calendar_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A pricer pinned to owner A whose source yields B's calendar raises.

        The owner is pinned BESIDE the calendar source since plan step
        salary:C12, so the amount model can scope a profile lookup without
        deriving a calendar; the pair is checked at the one moment both
        halves exist, the first derivation.  Refused rather than answered,
        because a pricer that priced A's profiles against B's paydays would
        answer plausible figures off the wrong schedule -- the same silence
        the case above refuses one tier down.
        """
        with app.app_context():
            own = seed_user["user"].id
            foreign = calendar_for(seed_second_user["user"].id)
            pricer = income_service.paycheck_pricing(
                own, lambda: foreign,
            )
            assert pricer.user_id == own
            with pytest.raises(ValueError, match="belonging to user"):
                _ = pricer.calendar
