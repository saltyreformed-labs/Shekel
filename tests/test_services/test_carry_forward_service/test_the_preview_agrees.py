"""The preview's discrete and transfer plans, its envelope-first ordering, and its parity with
carry_forward_unpaid on the same input.
"""

import pytest

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import carry_forward_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import generate_row_of, moved_by_the_owner

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

    def test_transfer_plan_dedupes_shadows(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer's two shadows produce ONE transfer plan, not two.

        Mirrors the de-duplication carry_forward_unpaid does in the
        shadow loop.
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
        ``target_estimated_after`` is computed from
        ``template.default_amount`` and must agree with the engine's
        actual generation (which uses the same value for non-salary-
        linked templates -- the only kind that can be is_envelope).
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
