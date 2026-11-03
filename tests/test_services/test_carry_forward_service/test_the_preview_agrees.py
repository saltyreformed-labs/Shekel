"""The preview's discrete and transfer plans, its envelope-first ordering, and its parity with
carry_forward_unpaid on the same input.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import carry_forward_service, template_amount_service
from app.services.balance_at import BalanceContext
from app.utils.dates import display_today
from tests._test_helpers import (
    definition_firing_twice_in_a_paycheck,
    generate_row_of,
    moved_by_the_owner,
    state_template_price,
)

from ._helpers import (
    _create_transfer_in_period,
    _create_template,
    _create_envelope_template,
    _add_entry,
)


class TestPreviewCarryForwardDiscreteAndTransfer:
    """Discrete and transfer plans are always actionable."""

    def test_discrete_plan_is_actionable(
        self, app, db, seed_user, seed_periods,
    ):
        """Discrete row -> single actionable plan."""
        with app.app_context():
            template = _create_template(seed_user, name="Rent",
                                        amount="1200.00",
                                        category_key="Rent")
            generate_row_of(template, seed_periods[0])
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert len(preview.plans) == 1
            plan = preview.plans[0]
            assert plan.kind == carry_forward_service.PLAN_KIND_DISCRETE
            assert plan.blocked is False
            assert plan.entries_sum is None
            assert plan.leftover is None
            assert preview.discrete_count == 1
            assert preview.envelope_count == 0
            assert preview.transfer_count == 0

    def test_a_transfer_is_one_unblocked_transfer_plan(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer is ONE transfer plan, unblocked, and counted once.

        It asserted the preview de-duplicated the transfer's two shadows into
        one plan, as ``carry_forward_unpaid``'s shadow loop did, until plan
        step ``balance:X-bi-6-4c-2`` walked the transfers themselves.
        """
        with app.app_context():
            _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert len(preview.plans) == 1
            plan = preview.plans[0]
            assert plan.kind == carry_forward_service.PLAN_KIND_TRANSFER
            assert plan.blocked is False
            assert preview.transfer_count == 1


class TestPreviewCarryForwardOrdering:
    """Plans are ordered envelope -> discrete -> transfer for UX clarity."""

    def test_mixed_batch_orders_envelope_first_transfer_last(
        self, app, db, seed_user, seed_periods,
    ):
        """Plans appear envelope first (can block), then discrete, then transfer.

        The modal's UX places blocking-capable rows at the top so the
        user sees the action items before the routine moves.  Same
        ordering as the mutating service's loops.
        """
        with app.app_context():
            envelope_t = _create_envelope_template(
                seed_user, category_key="Groceries",
            )
            envelope_source = generate_row_of(envelope_t, seed_periods[0])
            generate_row_of(envelope_t, seed_periods[1])
            _add_entry(envelope_source, seed_user, "10.00")

            discrete_t = _create_template(
                seed_user, name="Rent", amount="1200.00",
                category_key="Rent",
            )
            generate_row_of(discrete_t, seed_periods[0])

            _create_transfer_in_period(seed_user, seed_periods, 0)

            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert len(preview.plans) == 3
            assert (
                preview.plans[0].kind
                == carry_forward_service.PLAN_KIND_ENVELOPE
            )
            assert (
                preview.plans[1].kind
                == carry_forward_service.PLAN_KIND_DISCRETE
            )
            assert (
                preview.plans[2].kind
                == carry_forward_service.PLAN_KIND_TRANSFER
            )


class TestPreviewCarryForwardParityWithMutating:
    """Preview agrees with carry_forward_unpaid on the same input.

    This is the load-bearing invariant: any case that would
    successfully execute via carry_forward_unpaid produces a non-
    blocked preview, and any case that would raise ValidationError
    produces a blocked preview with the matching reason code.

    These tests are paired: each scenario runs the preview, then
    runs the mutating call on the same fixture state and confirms
    the prediction.  Without this parity the modal can't be trusted
    to reflect the actual carry-forward behaviour.
    """

    def test_actionable_preview_matches_successful_execution(
        self, app, db, seed_user, seed_periods,
    ):
        """Preview not-blocked -> mutating call succeeds + numbers match.

        Strong parity check: preview's predicted ``target_estimated_after``
        equals the actual target row's ``estimated_amount`` after the
        mutating call.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert preview.any_blocked is False
            predicted_after = preview.plans[0].target_estimated_after

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1
            db.session.refresh(target)
            assert target.estimated_amount == predicted_after, (
                "Preview's predicted target estimate must match what "
                "carry_forward_unpaid actually wrote."
            )

    def test_blocked_preview_matches_validation_error_on_execution(
        self, app, db, seed_user, seed_periods,
    ):
        """Preview blocked (AMBIGUOUS) -> mutating call raises ValidationError.

        The remaining blocks are the AMBIGUOUS target states (an override
        beside the canonical, or two rows answering one occurrence); the
        preview and the mutating call must agree the batch refuses.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            # An override beside the canonical -> AMBIGUOUS.
            generate_row_of(template, seed_periods[1])
            moved_by_the_owner(
                generate_row_of(template, seed_periods[2]),
                into=seed_periods[1],
            )
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert preview.any_blocked is True
            assert preview.plans[0].block_reason_code == (
                carry_forward_service.BLOCK_AMBIGUOUS_TARGETS
            )

            with pytest.raises(ValidationError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.rollback()

    def test_predicted_target_after_matches_after_engine_generation(
        self, app, db, seed_user, seed_periods,
    ):
        """When target is missing and engine generates: prediction matches.

        Stronger than the prior parity test because it covers the
        will-be-generated branch -- the preview's
        ``target_estimated_after`` is computed from what the row the engine
        would generate resolves to (plan step X-bp) and must agree with the
        engine's actual generation.  A price that changes after the target
        paycheck is :class:`TestTheGeneratedTargetIsPricedAsItsRow`.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            _add_entry(source, seed_user, "20.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            predicted_after = preview.plans[0].target_estimated_after
            assert preview.plans[0].target_will_be_generated is True

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            target = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).one()
            )
            assert target.estimated_amount == predicted_after


class TestTheGeneratedTargetIsPricedAsItsRow:
    """The GENERATE arm's "before" is the generated row's own price (plan step X-bp).

    The preview read the template's ``default_amount`` there, the NEWEST
    stated price, while the mutating path bumps the row the engine generates,
    which is priced by its definition's series on its OWN due date (amount
    rule 3).  A price stated to start after that day parted the two: the
    preview promised the newest price plus the leftover and the bump wrote the
    in-effect one plus the leftover.  Each case below fails on that code.
    """

    def test_a_price_scheduled_after_the_target_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """A rise dated after the target paycheck: preview and execution name $100.

        The opening price is stated on the owner's today (the create door's
        date) and the rise on the later of the day after it and the next
        paycheck's payday, so the rise is the NEWEST version and in effect on
        no row in either paycheck, whatever day the suite runs.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            rise_on = max(
                display_today() + timedelta(days=1),
                seed_periods[2].start_date,
            )
            state_template_price(
                template, Decimal("150.00"), effective_on=rise_on,
            )
            source = generate_row_of(template, seed_periods[0])
            _add_entry(source, seed_user, "20.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            plan = preview.plans[0]
            assert plan.target_will_be_generated is True
            # Leftover: 100 budget (the source's price on its due date) - 20
            # spent = 80.  The generated row is due inside the target
            # paycheck, before the rise: 100.  After: 100 + 80 = 180.  The
            # newest price would have read 150 and 230.
            assert plan.leftover == Decimal("80.00")
            assert plan.target_estimated_before == Decimal("100.00")
            assert plan.target_estimated_after == Decimal("180.00")

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            target = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).one()
            )
            assert target.estimated_amount == Decimal("180.00")

    def test_a_price_change_between_the_targets_two_occurrences(
        self, app, db, seed_user,
    ):
        """Two rows generated in the target: the preview names the FIRST one's price.

        A monthly definition on the pinned 60-day calendar
        (:func:`definition_firing_twice_in_a_paycheck`) names 2026-04-01 in
        the first paycheck and 2026-05-01 and 2026-06-01 in the second, and a
        calendar cadence funded by the paycheck containing it is due on the
        occurrence itself (``compute_due_date``, ruling R-R94), so a rise on
        2026-05-15 prices the two generated rows apart.  The mutating path
        bumps the first row the engine creates, the 2026-05-01 one, and the
        preview must name that row's price, not the second's and not the
        newest.
        """
        with app.app_context():
            template, first_paycheck, target_period = (
                definition_firing_twice_in_a_paycheck(
                    db.session, seed_user, name="Spending Money",
                )
            )
            template.is_envelope = True
            # The series: the create door's 100 on the owner's today (this
            # package freezes it at 2026-03-20, ``tests/test_services/
            # conftest.py``) and a rise to 150 from 2026-05-15.  The premises
            # are asserted, so a clock that moved the opening version past
            # either occurrence fails here rather than passing vacuously.
            state_template_price(
                template, Decimal("150.00"), effective_on=date(2026, 5, 15),
            )
            assert template_amount_service.amount_as_of(
                template, date(2026, 5, 1),
            ) == Decimal("100.00"), "fixture: the first target row prices 100"
            assert template_amount_service.amount_as_of(
                template, date(2026, 6, 1),
            ) == Decimal("150.00"), "fixture: the second target row prices 150"
            assert template_amount_service.amount_versions(template)[-1].amount == (
                Decimal("150.00")
            ), "fixture: the newest stated price is 150"
            source = generate_row_of(template, first_paycheck)
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                first_paycheck.id, target_period.id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            plan = preview.plans[0]
            assert plan.target_will_be_generated is True
            # Leftover: 100 budget (the 2026-04-01 row's price) - 30 spent =
            # 70.  The first generated row, due 2026-05-01: 100.  After:
            # 100 + 70 = 170.  The newest price, or the 2026-06-01 row's,
            # would have read 150 and 220.
            assert plan.leftover == Decimal("70.00")
            assert plan.target_estimated_before == Decimal("100.00")
            assert plan.target_estimated_after == Decimal("170.00")

            carry_forward_service.carry_forward_unpaid(
                first_paycheck.id, target_period.id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            first, second = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id, pay_period_id=target_period.id,
                    is_deleted=False,
                )
                .order_by(Transaction.occurs_on)
                .all()
            )
            assert first.occurs_on == date(2026, 5, 1)
            assert first.estimated_amount == Decimal("170.00")
            # The later row is still its definition's, priced on its own day.
            assert second.is_override is False
            assert second.estimated_amount is None
