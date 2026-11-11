"""Recurrence form helper tests: the create builder, ``RecurrenceSubmission.authored`` reached
through the schema's own consumer.
"""

from flask import Response

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.services.recurrence import RecurrenceSpec
from app.routes._recurrence_form_render import create_form_default_starts_on
from tests._test_helpers import derived_span, submission_from, validated_cadence

from ._helpers import _phase_of_spec


class TestBuildRecurrenceRuleFromForm:
    """The create builder's contract tests.

    :meth:`~app.services.recurrence.RecurrenceSubmission.authored` since plan
    step R7e, reached through the schema's own consumer
    (:func:`~tests._test_helpers.submission_from`); the route helper
    ``recurrence_spec_from_form`` until then.
    """

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
            result = submission_from(data).authored(seed_user["user"].id)
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
            result = submission_from(data).authored(seed_user["user"].id)
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
            result = submission_from(data).authored(seed_user["user"].id)

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
            result = submission_from(data).authored(seed_user["user"].id)
            assert isinstance(result, RecurrenceSpec)
            assert result.starts_on == own_period.start_date
            # The phase is PERIOD-unit only, whatever the bound's index.
            assert _phase_of_spec(result) == 0
            assert result.unit is RecurrenceUnitEnum.MONTH
            db.session.rollback()
