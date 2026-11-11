"""Recurrence form helper tests: a cadence edit at the update door. The authored interval reaches
the column, and a switch to a cadence with no day of month drops a stored nominal day.
"""

from datetime import date

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    MonthReading,
    RecurrenceResolutionError,
    build_transient_rule,
    reauthor_rule,
    recurrence_spec,
    resolve,
)
from tests._test_helpers import transient_cadence_rule, validated_cadence
from tests.oracles.recurrence_baseline import EVERY_N_PERIODS, MONTHLY, QUARTERLY
from tests.test_routes._recurrence_submission import submission_from


class TestTheColumnSaysWhatTheCadenceSays:
    """An authored interval reaches the column, for every unit.

    **This class asserted the OPPOSITE until plan step R7c-c, deliberately.**
    ``encode_cadence`` wrote ``1`` for every pattern whose interval was baked
    into its NAME, so an authored "every 3 months" stored ``interval_n = 1``
    beside ``pattern_id = Quarterly`` and every reader recovered the 3 through
    the pattern.  The migration re-points the column and drops the pattern, so
    the value a user typed is the value stored -- and these cases say so, with
    the stored 4 kept deliberately hostile: it is what a rule that used to be
    every-4-paychecks leaves behind, so an assertion cannot pass merely because
    the column was never touched.
    """

    @staticmethod
    def _edit(
        app, seed_user, *, unit, interval, stored_cadence, stored_interval,
        placement=None,
    ):
        """Drive the UPDATE door once and return the re-authored rule.

        Args:
            app: The app fixture.
            seed_user: The owner fixture.
            unit: The cadence unit to state.
            interval: The interval to state.
            stored_cadence: The cadence the rule starts on, as one of the
                baseline oracle's constants.
            stored_interval: The interval it starts on.
            placement: The placement to state, or ``None`` for the default.

        Returns:
            The re-authored :class:`RecurrenceRule`.
        """
        with app.test_request_context():
            rule = transient_cadence_rule(
                seed_user["user"].id, stored_cadence,
                interval_n=stored_interval, fires_on_day=21,
            )
            stated = (
                {} if placement is None else {"placement": placement}
            )
            data = {
                **validated_cadence(
                    unit=unit, interval_n=interval, **stated,
                ),
            }
            reauthor_rule(
                rule,
                submission_from(data).applied_to(rule),
                calendar_for(seed_user["user"].id),
            )
            assert "interval_n" not in data, (
                "interval_n must be popped whether or not it is written, so "
                "the caller's setattr loop never sees a stray kwarg"
            )
            return rule

    def test_an_authored_three_months_stores_three(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """3 months in, 3 in the column, 3 months back out."""
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.MONTH, interval=3,
            stored_cadence=EVERY_N_PERIODS,
            stored_interval=4,
        )

        assert rule.interval_n == 3, (
            "the stored 4 survived an edit that stated 3 -- that bill would "
            "generate on a rhythm the user never chose"
        )
        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 3, (
            "the authored interval did not survive the round trip; that bill "
            "would generate every month -- 3x the spend"
        )
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_an_authored_six_months_stores_six(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The same round trip against the six-month cadence."""
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.MONTH, interval=6,
            stored_cadence=EVERY_N_PERIODS,
            stored_interval=4,
        )

        assert rule.interval_n == 6
        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 6, "6x the spend if this regresses"
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_an_interval_the_closed_set_could_never_name_round_trips(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """"Every 2 months" survives the edit door, which it could not before.

        The cadence this arc exists for.  It resolved and walked correctly from
        plan step R3 and the closed pattern set had no NAME for it, so the door
        REFUSED it -- the case two below this one used to pin that refusal.
        Freeing the interval is the whole of plan step R7c-c, and this is the
        edit door's half of it.
        """
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.MONTH, interval=2,
            stored_cadence=EVERY_N_PERIODS,
            stored_interval=4,
        )

        assert rule.interval_n == 2
        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 2
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_a_first_paycheck_placement_at_three_months_round_trips(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Plan ledger row **D32** at the edit door.

        ``(3, MONTH, first paycheck)`` had no closed-set twin, so the door
        refused it and the form silently reassigned the placement instead.  The
        MONTH unit admits both placements at every interval now, so the pair
        the user chose is the pair that is stored -- which is the defect
        ceasing to exist rather than being warned about.
        """
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.MONTH, interval=3,
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            stored_cadence=MONTHLY,
            stored_interval=1,
        )

        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 3
        assert resolved.placement is (
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
        )

    def test_a_cadence_the_resolver_cannot_anchor_is_refused(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """An unauthorable cadence raises at the door rather than being coerced.

        **The unauthorable cadence has MOVED with each step that closed a
        gap**: any month interval the closed set could not name until plan
        step R7c-c, a year-scale cadence deferred onto a later paycheck until
        R8-a, the WEEK unit until R8-b.  What is left is a pay-period cadence
        funded from a LATER paycheck, whose placement is inert.

        **The disposition matters more than the refusal**: coercing such a
        cadence to the nearest authorable one would silently re-price a bill.
        Nothing offers it -- the picker's options come from the same producer
        -- and ``validate_authorable_cadence`` turns a hand-crafted POST into a
        field error before any of this runs; this is the last line.
        """
        with app.test_request_context():
            rule = transient_cadence_rule(
                seed_user["user"].id, QUARTERLY, fires_on_day=21,
            )
            before = rule.unit_id
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            }
            with pytest.raises(RecurrenceResolutionError) as excinfo:
                reauthor_rule(
                    rule,
                    submission_from(data).applied_to(rule),
                    calendar_for(seed_user["user"].id),
                )
            assert "not one this application can author" in str(excinfo.value)
            assert rule.unit_id == before, (
                "the row was re-pointed before the refusal"
            )

    def test_a_paycheck_cadence_keeps_its_interval_in_the_column(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """The one cadence whose interval IS the column, and it is unaffected.

        The neighbouring case a too-broad rule would break: ``Every N
        Periods`` is the single pattern that names no interval, so the
        authored 5 must land on the column verbatim.
        """
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.PERIOD, interval=5,
            stored_cadence=EVERY_N_PERIODS,
            stored_interval=2,
        )
        assert rule.interval_n == 5, (
            "the encoding swallowed the user's own choice for the one pattern "
            "whose interval lives in a column"
        )


class TestSwitchingToACadenceWithNoDayOfMonth:
    """A stored nominal day is DROPPED, not carried into a refusal (R7c-b).

    ``recurrence_spec_with_cadence`` builds the rule's authored state under the
    SUBMITTED cadence, and the ``(starts_on, nominal_day)`` pair is only valid
    against a cadence that HAS a day-of-month coordinate.  Reading the stored
    day verbatim made ``RecurrenceSpec.__post_init__`` refuse the intermediate
    value before the caller's ``replace`` could apply the submitted one -- an
    unhandled ``RecurrenceResolutionError`` on an ordinary cadence change.

    On a LOAN payment it was worse than an intermediate: the "Starts on"
    control renders disabled, so the submission states no start, the stored
    pair is KEPT, and the final value was contradictory too.
    """

    def test_a_month_end_rule_switched_to_paychecks_drops_its_day(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """"The last day of every month" has no reading in paycheck space.

        April has no 31st, so a day-31 rule first occurring there stores
        ``starts_on = 2026-04-30`` with ``nominal_day = 31``.  Switching the
        cadence to "every paycheck" leaves that day naming a coordinate the new
        cadence does not have, so it is dropped -- the same disposition
        ``_author`` already takes for the legacy ``day_of_month`` column and
        ``loan_cadence_start`` for a day-less loan payment.
        """
        with app.test_request_context():
            rule = build_transient_rule(
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "repeats_on": MonthReading(nominal_day=31),
                    },
                ).authored(seed_user["user"].id),
                calendar_for(seed_user["user"].id),
            )
            assert rule.nominal_day == 31

            reauthor_rule(
                rule,
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.PERIOD, states_a_start=False,
                        ),
                    },
                ).applied_to(rule),
                calendar_for(seed_user["user"].id),
            )

            assert rule.nominal_day is None
            assert rule.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.PERIOD,
            )

    def test_reading_a_month_end_rule_under_its_OWN_cadence_keeps_the_day(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The control: dropping is conditional on the cadence, not general.

        Without this arm a reader that dropped every nominal day would pass the
        case above -- and dropping it under a MONTH cadence is exactly the
        wrong-money defect this step's own script half caused, a month-end bill
        decaying to the 30th forever.
        """
        with app.test_request_context():
            rule = build_transient_rule(
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "repeats_on": MonthReading(nominal_day=31),
                    },
                ).authored(seed_user["user"].id),
                calendar_for(seed_user["user"].id),
            )

            assert recurrence_spec(rule).nominal_day == 31

    def test_a_MONTHLY_FIRST_cadence_KEEPS_it(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The cadence the two day questions disagree about.

        ``fires_on_day_of_month`` is ``False`` for ``Monthly First`` because it
        anchors on a PAYCHECK, but its occurrences are still days of the month
        and the walk reads ``day_of_month`` for it.  A reader keyed on the
        anchor question would drop the day here and move a month-end bill to
        the 30th for good.
        """
        with app.test_request_context():
            rule = build_transient_rule(
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "repeats_on": MonthReading(nominal_day=31),
                    },
                ).authored(seed_user["user"].id),
                calendar_for(seed_user["user"].id),
            )

            reauthor_rule(
                rule,
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            placement=(
                                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
                            ),
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "repeats_on": MonthReading(nominal_day=31),
                    },
                ).applied_to(rule),
                calendar_for(seed_user["user"].id),
            )

            assert rule.nominal_day == 31
