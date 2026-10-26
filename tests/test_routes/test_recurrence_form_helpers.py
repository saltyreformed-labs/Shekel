"""
Unit tests for ``app.routes._recurrence_form_helpers`` (F-24, F-26).

Pins helper-internal contracts so future edits to any of the four
helpers surface as unit-test failures rather than as integration
drift in the templates / transfers CRUD route suites.  Test IDs
C2-1 through C2-6 map to the F-24 commit's E section; C3-1 and
C3-2 map to the F-26 commit's E section.  Both commit specs live
in ``remediation_follow_up_F24_F25_F26_plan.md`` Section 7.

The tests use real templates / transfers blueprint endpoints
(``templates.new_template``, ``templates.edit_template``,
``transfers.list_transfer_templates``) for the helper's redirect
target rather than fabricating a test-only endpoint, because the
session-scoped ``app`` fixture is frozen by the time these tests
run (Flask refuses ``app.add_url_rule`` once a request has been
handled).
"""
import logging
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from flask import Response, get_flashed_messages

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    StaleConflictContext,
    handle_stale_conflict,
    handle_stale_form_conflict,
)
from app.routes._recurrence_form_helpers import (
    recurrence_spec_from_form,
    resolve_recurrence_rule_for_update,
    update_recurrence_rule_from_form,
)
from app.routes._recurrence_form_refusals import RecurrenceFormContext
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import end_bound_before_start_message
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    EMPTY,
    NEVER_ENDS,
    EndsOnDate,
    RecurrenceResolutionError,
    RecurrenceSpec,
    build_transient_rule,
    recurrence_spec,
    resolve,
)
from app.services.recurring_definition import resolved_definition
from app.routes._recurrence_form_render import (
    create_form_default_starts_on,
)
from tests._test_helpers import (
    bind_rule_to_loan,
    create_loan_account,
    derived_span,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_loan_payment_template,
    transient_cadence_rule,
    validated_cadence,
)
from app.services import balance_at, loan_recurrence_sync
from tests.oracles.recurrence_baseline import (
    EVERY_N_PERIODS,
    MONTHLY,
    QUARTERLY,
)


class _Definition(SimpleNamespace):
    """A definition's stand-in, answering ``recurs`` off its own rule.

    The refusals and the dispatcher read ``recurs`` since plan step
    ``balance:X-ci-1`` -- the one accessor for "does this definition repeat",
    a ``DerivedFlag`` over ``recurrence_rule`` on all three definition models
    -- so a stand-in carrying a bare ``recurrence_rule`` no longer answers
    what they ask.  A PROPERTY rather than a frozen attribute, because the
    dispatcher re-points and clears the rule it is handed and a value fixed at
    construction would answer for the rule the stand-in used to hold.
    """

    @property
    def recurs(self):
        return self.recurrence_rule is not None


class TestBuildRecurrenceRuleFromForm:
    """Helper :func:`recurrence_spec_from_form` contract tests."""

    def test_no_pattern_returns_none_and_pops_all_keys(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-1: no pattern -> None, every recurrence key popped.

        So the caller's ``TransactionTemplate`` constructor receives no
        recurrence key as a stray kwarg.  (Its transfer twin and the
        ``due_day_of_month`` pop left at plan step R5-a with the field,
        ruling R-R96: both forms now post the same recurrence keys.)
        """
        with app.test_request_context():
            # "Does not repeat" is a submitted-empty UNIT, and the placement
            # arrives beside it as an explicit ``None`` -- both fields are
            # ``allow_none``, so ``_normalize_empty_inputs`` keeps the key.
            data = {
                "recurrence_unit": None,
                "recurrence_placement": None,
                "interval_n": 1,
                "end_date": None,
                # The OPENING bound, which a BROWSER posts on this branch even
                # though no hand-written payload ever did: the box is hidden
                # with #recurrence-fields and a hidden input still submits.
                # Leaving it in ``data`` sent it into
                # ``TransactionTemplate(**data)``, whose constructor has no
                # such keyword -- a 500 on every "Does not repeat" save, green
                # across the whole suite, found by the browser drive
                # (tests/manual/verify_recurrence_form.py).
                "starts_on": None,
                "name": "Should survive",  # non-recurrence key
            }
            result = recurrence_spec_from_form(
                data,
                user_id=seed_user["user"].id,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget("templates.new_template"),
                ),
            )
            assert result is None
            assert data == {"name": "Should survive"}

    def test_every_n_periods_auto_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-3: EVERY_N_PERIODS + a stated "Starts on" -> offset derived.

        Hand-arithmetic: the bound falls in the period whose
        ``period_index`` is 1 (the second seeded period), and
        ``interval_n = 4``, so ``offset_periods = 1 % 4 = 1``.

        The bound named that paycheck through a "First paycheck" ``<select>``
        until plan step R7b-4 and is the paycheck's own payday now; the
        arithmetic and the assertion are unchanged.
        """
        with app.test_request_context():
            # Find a period with a known period_index for the
            # arithmetic.  seed_periods_today seeds an indexed range
            # around today; pick one with a non-zero index.
            chosen = next(
                (p for p in seed_periods_today if derived_span(p).period_index == 1),
                None,
            )
            assert chosen is not None, "fixture missing period_index=1"

            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=4,
                ),
                "end_date": None,
            }
            data["starts_on"] = chosen.start_date
            result = recurrence_spec_from_form(
                data,
                user_id=seed_user["user"].id,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget("templates.new_template"),
                ),
            )
            assert isinstance(result, RecurrenceSpec)
            # 1 % 4 = 1.  Read through the resolver since plan step
            # R7c-c dropped the column -- see ``_phase_of``.
            assert _phase_of_spec(result) == 1
            assert result.interval_n == 4
            assert result.unit is RecurrenceUnitEnum.PERIOD
            # Every recurrence key should have been popped from data.
            assert data == {}
            # Roll back so the test does not pollute the session.
            db.session.rollback()

    def test_the_builder_has_no_user_input_failure_left(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """It returns a rule or ``None``, never a redirect (plan step R7b-4).

        **Two IDOR regressions used to live here and have MOVED, not gone**
        (C2-4 and C2-7, deep-quality-hunt #21).  This helper owner-checked a
        submitted ``start_period_id`` for every pattern, because that field was
        the recurrence's "First paycheck" and a foreign period both persisted
        as a cross-user FK and shifted this owner's generation timing.  The
        recurrence takes a DATE now -- which names nothing of anyone else's --
        so the field belongs to the ONE job it still has, placing a
        non-repeating transfer, and its ownership probe sits in the route that
        reads it.  The refusal is asserted there:
        ``test_transfers.test_one_time.TestOneTimeTransfer.test_recurring_transfer_idor_period``.

        What this asserts is the property that makes the move safe rather than
        merely tidy: a kind-agnostic helper that cannot fail on a submission
        has no failure a caller can forget to propagate.  A ``Response`` here
        would mean the coupling came back.
        """
        with app.test_request_context():
            data = {
                **validated_cadence(unit=RecurrenceUnitEnum.MONTH),
                "end_date": None,
                # A foreign period id cannot be expressed: the transaction
                # schema no longer declares the field, and the helper takes no
                # such argument.  A stray key would simply survive in ``data``.
                # The first occurrence rides in from ``validated_cadence``,
                # which states one whenever it states a cadence -- the schema
                # requires the pair on a create (plan step R7c-b).
            }
            result = recurrence_spec_from_form(
                data,
                user_id=seed_user["user"].id,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget("templates.new_template"),
                ),
            )

            assert isinstance(result, RecurrenceSpec)
            assert not isinstance(result, Response)
            assert result.starts_on == create_form_default_starts_on()
            assert data == {}
            db.session.rollback()

    def test_a_stated_start_date_is_persisted_without_phasing_a_month_rule(
        self, app, seed_user, seed_periods_today,
    ):
        """C2-8: a MONTHLY rule keeps its bound and takes no phase.

        The bound is stored whatever the cadence, and ``offset_periods`` stays
        0 -- the paycheck-ordinal derivation is scoped to the ``PERIOD`` unit,
        so a month-scale rule never acquires one even when its bound falls in
        a paycheck with a non-zero index.

        It stated the bound as a start-period FK until plan step R7b-4; the
        two assertions that survive are the ones about the RULE rather than
        about the affordance.
        """
        with app.test_request_context():
            own_period = next(
                (p for p in seed_periods_today if derived_span(p).period_index == 1),
                None,
            )
            assert own_period is not None, "fixture missing period_index=1"
            data = {
                **validated_cadence(unit=RecurrenceUnitEnum.MONTH),
                "end_date": None,
                "starts_on": own_period.start_date,
            }
            result = recurrence_spec_from_form(
                data,
                user_id=seed_user["user"].id,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget("templates.new_template"),
                ),
            )
            assert isinstance(result, RecurrenceSpec)
            assert result.starts_on == own_period.start_date
            # The phase is PERIOD-unit only, whatever the bound's index.
            assert _phase_of_spec(result) == 0
            assert result.unit is RecurrenceUnitEnum.MONTH
            db.session.rollback()

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
            update_recurrence_rule_from_form(
                rule,
                data,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                calendar=calendar_for(seed_user["user"].id),
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
            update_recurrence_rule_from_form(
                rule,
                data,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                calendar=calendar_for(seed_user["user"].id),
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
                data,
                ctx=RecurrenceFormContext(
                    end_bound=None,
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
                recurrence_spec_from_form(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.PERIOD, interval_n=3,
                        ),
                        "starts_on": seed_periods[2].start_date,
                    },
                    user_id=seed_user["user"].id,
                    ctx=RecurrenceFormContext(
                        end_bound=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                    ),
                ),
                calendar_for(seed_user["user"].id),
            )
            assert _phase_of(rule) == 2

            update_recurrence_rule_from_form(
                rule,
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.PERIOD, interval_n=3,
                    ),
                    "starts_on": seed_periods[2].start_date,
                },
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                calendar=calendar_for(seed_user["user"].id),
            )

            assert _phase_of(rule) == 2
            assert rule.starts_on == seed_periods[2].start_date


class TestHandleStaleConflict:
    """Helper :func:`handle_stale_conflict` contract tests."""

    def test_logs_flashes_and_redirects(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-6: rollback + log + flash + 302 redirect.

        Pins the canonical handler shape so a regression that drops
        any of the four side effects (rollback, log, flash, redirect)
        surfaces here.
        """
        with app.test_request_context():
            test_logger = logging.getLogger("test_handle_stale_conflict")
            # The helper expects to be invoked from inside an
            # ``except`` block where a commit just raised.  No
            # commit happened here, so rollback is a no-op -- the
            # assertion focuses on the redirect contract.
            response = handle_stale_conflict(StaleConflictContext(
                logger=test_logger,
                log_label="test_route",
                log_id=123,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="test object",
                ),
                redirect=RedirectTarget(
                    "templates.edit_template",
                    {"template_id": 123},
                ),
            ))
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/123" in response.headers["Location"]

    def test_stale_message_templates_render_expected_strings(self):
        """C2-6 (variant): the two flash templates render expected copy.

        Pins the user-facing wording so a copy-edit that breaks the
        ``{noun}`` substitution or rewords the canonical line
        surfaces as a unit-test failure.
        """
        editing = STALE_EDITING_MESSAGE.format(noun="recurring transaction")
        assert "while you were editing" in editing
        assert "recurring transaction" in editing
        assert "Please reload and try again." in editing

        action = STALE_ACTION_MESSAGE.format(noun="recurring transfer")
        assert "while you were editing" not in action
        assert "recurring transfer" in action
        assert "Please reload and try again." in action


class TestHandleStaleFormConflict:
    """F-26 helper :func:`handle_stale_form_conflict` contract tests.

    Pre-flush optimistic-locking mirror of
    :func:`handle_stale_conflict`; logs both submitted and current
    version counters so post-mortem analysis can reconstruct the
    race.  Does NOT roll back (no DB write attempted at the call
    site).
    """

    def test_logs_both_counters_flashes_and_redirects(
        self, app, auth_client, seed_user, caplog,  # pylint: disable=unused-argument
    ):
        """C3-1: log at INFO with submitted + current; flash; 302.

        Pins the canonical handler shape so a regression that drops
        either counter from the log (or rewords the canonical line)
        surfaces here.
        """
        with app.test_request_context():
            test_logger = logging.getLogger(
                "test_handle_stale_form_conflict",
            )
            with caplog.at_level(
                logging.INFO,
                logger="test_handle_stale_form_conflict",
            ):
                response = handle_stale_form_conflict(
                    StaleConflictContext(
                        logger=test_logger,
                        log_label="update_template",
                        log_id=42,
                        flash_message=STALE_EDITING_MESSAGE.format(
                            noun="recurring transaction",
                        ),
                        redirect=RedirectTarget(
                            "templates.edit_template",
                            {"template_id": 42},
                        ),
                    ),
                    submitted=7,
                    current=9,
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/42" in response.headers["Location"]
            # The log record must carry BOTH the submitted and the
            # current counters -- the post-mortem-reconstruction
            # rationale fails if either is missing.
            log_msg = caplog.records[-1].getMessage()
            assert "update_template" in log_msg
            assert "id=42" in log_msg
            assert "submitted=7" in log_msg
            assert "current=9" in log_msg


def _phase_of(rule):
    """Return the cycle phase *rule* resolves to.

    **Read through the RESOLVER since plan step R7c-c**, which dropped
    ``budget.recurrence_rules.offset_periods``.  Every case below asserted the
    column; the phase is derived from the rule's own first occurrence on every
    read now, which is what makes defect **D1**'s stale-phase shape
    unconstructible rather than guarded -- so the resolved answer is both what
    the cases meant and the only thing left to read.

    Args:
        rule: The rule to resolve.

    Returns:
        int: Its ``offset_periods`` phase.
    """
    return resolve(
        recurrence_spec(rule), calendar_for(rule.user_id),
    ).offset_periods


def _phase_of_spec(spec):
    """Return the cycle phase *spec* resolves to.

    :func:`_phase_of`'s sibling for the cases that read what the FORM
    authored rather than what a row stored -- which is what
    ``recurrence_spec_from_form`` answers since plan step R-F6 split reading a
    submission from writing it.  Same producer, same question, one hop earlier.

    Args:
        spec: The :class:`~app.services.recurrence.RecurrenceSpec` to resolve.

    Returns:
        int: Its ``offset_periods`` phase.
    """
    return resolve(spec, calendar_for(spec.user_id)).offset_periods


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
            update_recurrence_rule_from_form(
                rule, data,
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                calendar=calendar_for(seed_user["user"].id),
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

        **The unauthorable cadence MOVED at plan step R7c-c**: it was any month
        interval the closed set could not name, and it is now a
        ``(unit, placement)`` pair with no first-occurrence derivation -- the
        WEEK unit, and a year-scale cadence deferred onto a month's first
        paycheck, both plan step R8's.

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
                **validated_cadence(unit=RecurrenceUnitEnum.WEEK),
            }
            with pytest.raises(RecurrenceResolutionError) as excinfo:
                update_recurrence_rule_from_form(
                    rule, data,
                    ctx=RecurrenceFormContext(
                        end_bound=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                    ),
                    calendar=calendar_for(seed_user["user"].id),
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

class TestAnUpdateMayNotInvertTheWindow:
    """``refuse_inverted_window``: the UPDATE door's half of one rule (R7c-b).

    ``ck_recurrence_rules_valid_window`` was drafted for this step and held
    back on a developer ruling -- the column carried DERIVED loan-payment
    windows as well as authored ones, and an empty derived window is a correct
    answer a constraint cannot tell from a user's mistake -- until plan step
    R7d-g deleted the derived writer and landed it (see
    ``tests/test_models/test_recurrence_window_check.py``).  The rule still
    lives at the two AUTHORING doors, because a CHECK answers with an
    ``IntegrityError`` and a door with a sentence; this class is the one the
    schema could not hold: on an update either half of the pair may be the
    STORED value, which no schema sees.

    Both directions are cases the review found only one of.  Each was an
    unhandled ``CheckViolation`` out of ``update_template``'s autoflush while
    the CHECK was drafted in, and each is a silent zero-generation without a
    door.
    """

    @staticmethod
    def _template_with(rule, user_id):
        """Return the minimal stand-in the refusal reads of a template.

        Args:
            rule: The :class:`RecurrenceRule` it owns.  TRANSIENT here, which
                is why the owner is stated separately: a rule reports its
                ``user_id`` through the definition owning it since plan step
                R-F6, and an unsaved rule has no owner to report.
            user_id: The owner the stand-in claims.

        Returns:
            A :class:`_Definition` carrying the three attributes the refusal
            touches, and answering ``recurs`` off the first.  A real
            ``TransactionTemplate`` would drag its own FK graph in for a
            question about two dates.
        """
        return _Definition(
            recurrence_rule=rule,
            to_account_id=None,
            user_id=user_id,
        )

    @staticmethod
    def _ctx(end_bound):
        """Return a form context carrying *end_bound* and a real redirect.

        Args:
            end_bound: The submitted closing bound, or ``None``.

        Returns:
            The :class:`RecurrenceFormContext`.
        """
        return RecurrenceFormContext(
            end_bound=end_bound,
            redirect=RedirectTarget(
                "templates.edit_template", {"template_id": 1},
            ),
        )

    def _monthly_rule(self, seed_user, starts_on, end_bound=NEVER_ENDS):
        """Author a monthly rule starting on *starts_on*.

        Args:
            seed_user: The ``seed_user`` fixture dict.
            starts_on: The rule's first occurrence.
            end_bound: Its closing bound.

        Returns:
            The flushed :class:`RecurrenceRule`.
        """
        return build_transient_rule(
            recurrence_spec_from_form(
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.MONTH, starts_on=starts_on,
                    ),
                    "starts_on": starts_on,
                },
                user_id=seed_user["user"].id,
                ctx=self._ctx(end_bound),
            ),
            calendar_for(seed_user["user"].id),
        )

    def test_a_loan_cleared_before_its_first_installment_stores_no_inverted_pair(
        self, app, auth_client, seed_user, db, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """The EMPTY derived window is a VALUE the door composes, never a stored pair (plan step R7d-g).

        The same ruling that held ``ck_recurrence_rules_valid_window`` back
        (developer, 2026-08-15) named this loan: it originates 2026-06-20 with
        a ``payment_day`` of 15 -- first installment 2026-07-15 -- and is
        cleared the day after origination, so its window is
        ``[2026-07-15, 2026-06-21]``, correct at nought occurrences.  Until
        R7d-g the sync WROTE that pair into the columns and this case held
        that the update door's refusal carried a skip for it, because the
        owner had no control that could repair the app's own inverted pair.

        Nothing writes it now.  The column stays NULL after the loan clears,
        the composed door answers ``EMPTY`` for the definition, and an
        ordinary cadence edit -- which states neither bound -- is not
        refused, with no skip to pass on: the stored pair is not inverted
        because there is no stored pair.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 7, 1))
            loan = create_loan_account(
                seed_user, db.session, name="Cleared Early",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=15,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()

            rule = tpl.recurrence_rule
            db.session.refresh(rule)
            assert rule.starts_on == date(2026, 7, 15)
            assert rule.end_date is None and rule.max_occurrences is None, (
                "a writer stored the derived window: "
                f"starts_on={rule.starts_on} end_date={rule.end_date}"
            )
            pass_ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.is_standing_loan_payment(tpl, pass_ctx), (
                "precondition: this is the loan's standing payment"
            )
            assert resolved_definition(tpl, pass_ctx).closing.derived == EMPTY

            with app.test_request_context():
                refusal = resolve_recurrence_rule_for_update(
                    tpl,
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            states_a_start=False,
                        ),
                    },
                    ctx=self._ctx(None),
                    pass_ctx=pass_ctx,
                )

            assert refusal is None, (
                "an ordinary cadence edit of a loan payment whose loan cleared "
                "early was refused: something stored an inverted pair"
            )

    def test_clearing_the_start_while_setting_an_earlier_end_is_refused(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The cleared date box drops the key, so the STORED start applies.

        ``starts_on`` is not ``allow_none``, so an emptied control arrives
        ABSENT rather than as a stated ``None`` -- which the update door reads
        as "leave the stored date alone".  The submitted bound then lands below
        a date the payload never mentioned, and no schema can see the pair.
        """
        with app.test_request_context():
            rule = self._monthly_rule(seed_user, date(2026, 6, 1))

            refusal = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                    ),
                },
                ctx=self._ctx(EndsOnDate(on=date(2026, 5, 31))),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert isinstance(refusal, Response)
            assert rule.end_date is None, "the rule must be left untouched"

    def test_a_cadence_switch_that_lifts_the_stored_start_past_the_stop_is_refused(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The pair the DOOR would store inverts though the authored pair does not (plan step R7d-g).

        A MONTH rule ``[2025-06-01, 2025-12-01]`` is ordered and stored.  The
        owner switches "Repeats" to every paycheck and restates neither bound:
        ``refuse_inverted_window`` grades the stored pair, still ordered, and
        passes; the re-author then normalises the start onto the owner's
        FIRST payday, 2026-01-02, past the stop.  The write door refuses off
        the pair it would have stored, this dispatcher words it with the
        inverted-window sentence naming that payday, and the rule reads as it
        did.  Until R7d-g-1's review this was ``ck_recurrence_rules_valid_window``
        answering the flush with a 500.
        """
        with app.test_request_context():
            rule = self._monthly_rule(
                seed_user, date(2025, 6, 1), EndsOnDate(on=date(2025, 12, 1)),
            )
            calendar = calendar_for(seed_user["user"].id)
            assert calendar.opening_bound() == date(2026, 1, 2)

            refusal = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.PERIOD, states_a_start=False,
                    ),
                },
                ctx=self._ctx(None),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )
            flashed = get_flashed_messages()

            assert isinstance(refusal, Response)
            assert end_bound_before_start_message(
                date(2025, 12, 1), date(2026, 1, 2),
            ) in flashed, flashed
            assert rule.starts_on == date(2025, 6, 1)
            assert rule.end_date == date(2025, 12, 1)
            assert ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH) == rule.unit_id

    def test_moving_the_start_PAST_a_stored_end_is_refused(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The direction no reviewer listed, and the schema cannot see it AT ALL.

        The form states a new "Starts on" and says nothing about the "Ends"
        control, so the payload carries no bound -- and the stored one is what
        the save keeps.  ``require_end_bound_after_start`` runs only when the
        SUBMISSION states both, so it returns early here.
        """
        with app.test_request_context():
            rule = self._monthly_rule(
                seed_user, date(2026, 6, 1),
                end_bound=EndsOnDate(on=date(2026, 7, 1)),
            )

            refusal = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.MONTH,
                        starts_on=date(2026, 9, 1),
                    ),
                    "starts_on": date(2026, 9, 1),
                },
                ctx=self._ctx(None),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert isinstance(refusal, Response)
            assert rule.starts_on == date(2026, 6, 1), (
                "the rule must be left untouched"
            )

    def test_an_end_ON_the_new_start_is_ALLOWED(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The boundary, and the control for both cases above.

        A rule whose closing bound is its own first occurrence fires exactly
        once, which is a real cadence.  Without this arm a door that refused
        every stated bound would pass the two refusals.
        """
        with app.test_request_context():
            rule = self._monthly_rule(seed_user, date(2026, 6, 1))

            outcome = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.MONTH,
                        starts_on=date(2026, 6, 1),
                    ),
                    "starts_on": date(2026, 6, 1),
                },
                ctx=self._ctx(EndsOnDate(on=date(2026, 6, 1))),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert outcome is None
            assert rule.end_date == date(2026, 6, 1)

    def test_an_amount_only_edit_states_neither_and_is_not_graded(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """A partial update carries no recurrence keys and must stay savable.

        The rule's own stored window was checked when it was written, so
        re-grading it on an edit that mentions neither value would refuse an
        edit that changed nothing about the recurrence.
        """
        with app.test_request_context():
            rule = self._monthly_rule(
                seed_user, date(2026, 6, 1),
                end_bound=EndsOnDate(on=date(2026, 7, 1)),
            )

            outcome = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                {"default_amount": Decimal("10.00")},
                ctx=self._ctx(None),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert outcome is None
            assert rule.end_date == date(2026, 7, 1)


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
                recurrence_spec_from_form(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "nominal_day": 31,
                    },
                    user_id=seed_user["user"].id,
                    ctx=RecurrenceFormContext(
                        end_bound=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                    ),
                ),
                calendar_for(seed_user["user"].id),
            )
            assert rule.nominal_day == 31

            update_recurrence_rule_from_form(
                rule,
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.PERIOD, states_a_start=False,
                    ),
                },
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                calendar=calendar_for(seed_user["user"].id),
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
                recurrence_spec_from_form(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "nominal_day": 31,
                    },
                    user_id=seed_user["user"].id,
                    ctx=RecurrenceFormContext(
                        end_bound=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                    ),
                ),
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
                recurrence_spec_from_form(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 4, 30),
                            nominal_day=31,
                        ),
                        "starts_on": date(2026, 4, 30),
                        "nominal_day": 31,
                    },
                    user_id=seed_user["user"].id,
                    ctx=RecurrenceFormContext(
                        end_bound=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                    ),
                ),
                calendar_for(seed_user["user"].id),
            )

            update_recurrence_rule_from_form(
                rule,
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
                    "nominal_day": 31,
                },
                ctx=RecurrenceFormContext(
                    end_bound=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                ),
                calendar=calendar_for(seed_user["user"].id),
            )

            assert rule.nominal_day == 31
