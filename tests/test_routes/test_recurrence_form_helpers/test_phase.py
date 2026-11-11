"""Recurrence form helper tests: an edit cannot re-phase a rule (D8), and an edit keeps the phase
of a rule that states its start (D1).
"""

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.routes._recurrence_form_helpers import resolve_recurrence_rule_for_update
from app.routes._recurrence_form_refusals import RecurrenceFormContext
from app.routes._redirect_target import RedirectTarget
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.recurrence import build_transient_rule, reauthor_rule
from tests._test_helpers import transient_cadence_rule, validated_cadence
from tests.oracles.recurrence_baseline import EVERY_N_PERIODS
from tests.test_routes._recurrence_submission import submission_from

from ._helpers import _Definition, _phase_of


class TestAnEditCannotRePhaseARule:
    """Defect **D8**, and the surface it survived on until plan step R7b-4.

    **The subject of this class has changed TWICE, and both changes are the
    point.**  It first pinned that the update path took the SUBMITTED
    ``offset_periods`` verbatim -- the honest reading while the schemas
    declared the field.  Plan step R7b-2 deleted the field, so there was no
    submitted value left to take, and the rule's own STORED phase rode through
    untouched instead.

    Plan step R7b-4 removed the last of it: a phase has ONE source now, the
    paycheck the rule's opening bound falls in, so there is no stored value to
    ride through either.  An edit cannot re-phase a rule because an edit that
    does not move the bound has not changed the phase's only input -- which is
    stronger than "the payload is ignored", and it is what these tests pin, on
    both the direct-update and dispatcher paths.

    A rule carrying a non-zero phase and NO bound was the shape the earlier
    version of this class built.  The write door cannot produce one: it writes
    the resolver's answer, and with no bound that answer is 0 for every
    interval.  So the cases below state a bound, which is what a real row has.
    """

    def test_a_cadence_edit_re_derives_the_phase_from_the_unchanged_bound(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Changing the interval does not move which paychecks the rule opens on.

        The rule starts in period index 3, so at an interval of 4 it phases at
        ``3 % 4 == 3``.  The edit widens the cycle to 6 and the bound does not
        move, so the phase is ``3 % 6 == 3`` -- the same paycheck it always
        opened on.  Writing the old schema default here moved every future
        occurrence three paychecks earlier.
        """
        with app.test_request_context():
            rule = transient_cadence_rule(
                seed_user["user"].id,
                EVERY_N_PERIODS,
                interval_n=4,
                starts_on=seed_periods[3].start_date,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=6,
                ),
                "starts_on": seed_periods[3].start_date,
            }
            reauthor_rule(
                rule,
                submission_from(data).applied_to(rule),
                calendar_for(seed_user["user"].id),
            )
            assert _phase_of(rule) == 3, (
                "the edit re-phased a rule whose phase nothing else states"
            )
            assert rule.interval_n == 6
            assert rule.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.PERIOD,
            )
            # All recurrence keys popped so the caller's setattr loop
            # never sees a stray kwarg.
            assert data == {}

    def test_an_offset_in_the_payload_is_not_read_at_all(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A hand-crafted ``offset_periods`` cannot reach the rule.

        The schemas no longer DECLARE the field, so marshmallow's ``EXCLUDE``
        drops it and no such key can arrive through a route -- this drives the
        helper directly to prove the second half: even handed one, the helper
        does not read it.  The key is deliberately left unpopped, which is what
        says it is not a recurrence key any more rather than one this helper
        happens to ignore.
        """
        with app.test_request_context():
            rule = transient_cadence_rule(
                seed_user["user"].id,
                EVERY_N_PERIODS,
                interval_n=4,
                starts_on=seed_periods[3].start_date,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=4,
                ),
                "offset_periods": 0,
                "starts_on": seed_periods[3].start_date,
            }
            reauthor_rule(
                rule,
                submission_from(data).applied_to(rule),
                calendar_for(seed_user["user"].id),
            )
            assert _phase_of(rule) == 3
            assert data == {"offset_periods": 0}

    def test_the_phase_survives_through_the_dispatcher(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The same, through the door the update routes actually take.

        ``resolve_recurrence_rule_for_update`` takes the in-place update
        branch when the template already owns a rule and a cadence is
        submitted, which is the real path
        ``update_template`` / ``update_transfer_template`` follow.

        The rule starts in period index 2 and the edit widens the interval to
        7, so the phase is ``2 % 7 == 2``: the same opening paycheck.
        """
        with app.test_request_context():
            rule = transient_cadence_rule(
                seed_user["user"].id,
                EVERY_N_PERIODS,
                interval_n=4,
                starts_on=seed_periods[2].start_date,
            )
            template = _Definition(
                recurrence_rule=rule,
                user_id=seed_user["user"].id,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=7,
                ),
                "starts_on": seed_periods[2].start_date,
            }
            result = resolve_recurrence_rule_for_update(
                template,
                ctx=RecurrenceFormContext(
                    submission=submission_from(data),
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert result is None
            assert _phase_of(rule) == 2
            assert rule.interval_n == 7
            assert rule.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.PERIOD,
            )


class TestUpdateKeepsTheStatedStartsPhase:
    """Defect **D1**, closed at plan step R2c-1, at its own surface.

    The sibling class above pins that an edit cannot move a phase whose input
    it did not move.  This class covers the case D1 was MEASURED on and that
    45 of the 50 live rules were in -- a rule that states an opening bound.
    The phase is a derived fact there, and the pre-seam update path overwrote
    it with the payload's default, shifting every future occurrence by one pay
    period on an edit that changed only the amount.
    """

    def test_an_edit_does_not_re_phase_a_rule_with_a_stated_start(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The phase stays ``period_index % interval_n`` across an edit.

        A bound falling in period index 2 with an interval of 3 phases the
        rule at ``2 % 3 == 2``.  Before R2c-1 the edit wrote the payload's
        offset default of 0 onto the rule and every future occurrence moved a
        pay period earlier; since plan step R7b-2 there is no such key in the
        payload at all (defect **D8**), and since plan step R7b-4 there is no
        such FIELD -- the phase is re-derived from the bound the rule states.

        **The edit RE-SUBMITS the bound**, because the form renders it and a
        rendered control posts.  That is the shape worth pinning: an edit that
        restates the same start must not move the rule, which is what makes
        the derivation safe to run on every write.
        """
        with app.test_request_context():
            # The helper READS the submission and ``build_transient_rule``
            # writes it (plan step R-F6): the two halves used to be one call,
            # and they split because a rule cannot be written before the
            # definition owning it.  Transient here -- what this case grades is
            # the phase, which is a property of the resolution.
            rule = build_transient_rule(
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.PERIOD, interval_n=3,
                        ),
                        "starts_on": seed_periods[2].start_date,
                    },
                ).authored(seed_user["user"].id),
                calendar_for(seed_user["user"].id),
            )
            assert _phase_of(rule) == 2

            reauthor_rule(
                rule,
                submission_from(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.PERIOD, interval_n=3,
                        ),
                        "starts_on": seed_periods[2].start_date,
                    },
                ).applied_to(rule),
                calendar_for(seed_user["user"].id),
            )

            assert _phase_of(rule) == 2
            assert rule.starts_on == seed_periods[2].start_date
