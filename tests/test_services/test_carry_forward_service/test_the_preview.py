"""preview_carry_forward's short circuits, its envelope plans (read-only), and the destination it
predicts for each envelope row, the blocked ambiguous target among them.
"""

from decimal import Decimal

import pytest

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import carry_forward_service, transaction_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    definition_firing_twice_in_a_paycheck,
    generate_row_of,
    moved_by_the_owner,
    populate_in_a_fresh_pass,
    repriced_by_the_owner,
)

from ._helpers import (
    _create_transaction,
    _create_envelope_template,
    _fires_in_no_seed_paycheck,
    _add_entry,
)


# ── Carry-Forward Preview (Phase 5) ────────────────────────────────


def _read_only_session_snapshot():
    """Return a coarse fingerprint of the session state.

    Used by preview tests to assert the service made no mutations.
    Captures the count of dirty/new/deleted objects in the session;
    a clean snapshot before AND after calling preview proves the
    function is read-only at the session level.
    """
    return (
        len(db.session.dirty),
        len(db.session.new),
        len(db.session.deleted),
    )


class TestPreviewCarryForwardEmptyAndShortCircuits:
    """Boundary cases: empty period, same-period, missing periods."""

    @pytest.mark.usefixtures("db")
    def test_empty_source_returns_empty_plans(
        self, app, seed_user, seed_periods,
    ):
        """No projected rows in source -> preview returns empty plans list.

        The carry-forward UI button is only rendered for periods in
        the past; an empty past period is unusual but legal (e.g. a
        user who manually settled every row).  Preview must not
        crash, must produce a renderable preview, and must mark
        Confirm as inactive (any_blocked is False but plans is empty,
        which the modal template treats as "nothing to do").
        """
        with app.app_context():
            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert preview.plans == []
            assert preview.any_blocked is False
            assert preview.envelope_count == 0
            assert preview.discrete_count == 0
            assert preview.transfer_count == 0
            assert preview.blocked_count == 0
            assert preview.source_period.period_id == seed_periods[0].id
            assert preview.target_period.period_id == seed_periods[1].id

    def test_same_period_returns_empty_plans(
        self, app, db, seed_user, seed_periods,
    ):
        """source == target short-circuits identically to carry_forward_unpaid.

        The mutating service returns 0 in this case; the preview
        returns an empty plans list so the modal renders cleanly
        rather than blowing up.
        """
        with app.app_context():
            # Add a row to the period to prove the short-circuit
            # ignores rows when source == target.
            _create_transaction(
                seed_user, seed_periods,
                name="Should be ignored on same-period preview",
            )
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[0].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert preview.plans == []
            assert preview.any_blocked is False

    @pytest.mark.usefixtures("db")
    def test_missing_source_period_raises_not_found(
        self, app, seed_user, seed_periods,
    ):
        """Unknown source_period_id raises NotFoundError (route returns 404)."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.preview_carry_forward(
                    9_999_999, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )

    @pytest.mark.usefixtures("db", "seed_second_user")
    def test_unowned_source_period_raises_not_found(
        self, app, seed_user, seed_periods, seed_second_periods,
    ):
        """Cross-user source period raises NotFoundError (404 at route).

        Defense-in-depth: the route enforces ownership before calling
        the service, but the service repeats the check so a future
        misuse cannot leak data.  Mirrors the security response rule
        in CLAUDE.md.
        """
        with app.app_context():
            other_period_id = seed_second_periods[0].id
            with pytest.raises(NotFoundError):
                carry_forward_service.preview_carry_forward(
                    other_period_id, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )


class TestPreviewCarryForwardEnvelopePlans:
    """Each envelope-row outcome in carry_forward_unpaid has a matching plan."""

    def test_partial_spend_plan_actionable_with_target_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Mirror of TestCarryForwardEnvelopePartialSpend, no mutations.

        Worked example: $100 envelope, $65 entries, target rule-
        generated.  Preview reports:
          plan.kind                     == "envelope"
          plan.blocked                  == False
          plan.entries_sum              == 65
          plan.leftover                 == 35
          plan.target_estimated_before  == 100
          plan.target_estimated_after   == 135
          plan.target_will_be_generated == False
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            before = _read_only_session_snapshot()
            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            after = _read_only_session_snapshot()
            assert before == after, (
                "preview_carry_forward must not mutate the session."
            )

            assert len(preview.plans) == 1
            plan = preview.plans[0]
            assert plan.kind == carry_forward_service.PLAN_KIND_ENVELOPE
            assert plan.blocked is False
            assert plan.block_reason_code is None
            assert plan.entries_sum == Decimal("65.00")
            assert plan.leftover == Decimal("35.00")
            assert plan.target_estimated_before == Decimal("100.00")
            assert plan.target_estimated_after == Decimal("135.00")
            assert plan.target_will_be_generated is False

            # Counts.
            assert preview.any_blocked is False
            assert preview.envelope_count == 1
            assert preview.discrete_count == 0
            assert preview.transfer_count == 0

    def test_zero_entries_plan_rolls_full_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entries: plan.entries_sum == 0, leftover == estimated."""
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="200.00",
            )
            generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.entries_sum == Decimal("0.00")
            assert plan.leftover == Decimal("200.00")
            # 200 + 200 = 400.
            assert plan.target_estimated_after == Decimal("400.00")
            assert plan.blocked is False

    def test_overspend_plan_zero_leftover_no_target_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Overspend: leftover == 0, target_estimated_* are None.

        With no rollover the target row is irrelevant -- the modal
        does not render before/after numbers, so the service
        deliberately leaves them None.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "80.00")
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.entries_sum == Decimal("120.00")
            assert plan.leftover == Decimal("0.00")
            assert plan.target_estimated_before is None
            assert plan.target_estimated_after is None
            assert plan.target_will_be_generated is False
            assert plan.blocked is False

    def test_missing_target_with_active_rule_predicts_generation(
        self, app, db, seed_user, seed_periods,
    ):
        """No target row + active rule: plan.target_will_be_generated == True.

        Mirrors TestCarryForwardEnvelopeMissingTarget, with no
        mutations.  Predicts the same numbers the engine would
        produce: canonical at template default + leftover.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            # Sanity: target period truly empty.
            target_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                ).count()
            )
            assert target_count == 0

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is False
            assert plan.target_will_be_generated is True
            assert plan.target_estimated_before == Decimal("100.00")
            # Leftover: 100 estimated - 30 entries = 70.
            # Target after: 100 (engine default) + 70 = 170.
            assert plan.entries_sum == Decimal("30.00")
            assert plan.leftover == Decimal("70.00")
            assert plan.target_estimated_after == Decimal("170.00")

            # Confirm the engine still hasn't run -- preview must
            # not actually generate the canonical.
            target_count_after = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                ).count()
            )
            assert target_count_after == 0


class TestPreviewCarryForwardEnvelopeTargetResolution:
    """Preview mirrors the destination decision the mutating path takes.

    The cases that used to block (finalised, inactive, soft-deleted
    target) are now actionable -- the preview predicts a freshly created
    row.  Only the AMBIGUOUS target states still block: an override beside
    the canonical, or two rows answering one occurrence.
    """

    def test_settled_target_is_actionable_creates_separate_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Target Paid -> actionable plan predicting a separate new row.

        Mirrors TestCarryForwardEnvelopeSettledTarget but read-only.
        The Paid row cannot be bumped, so the preview predicts a fresh
        row carrying the $60 leftover (from $0).
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            transaction_service.settle_transaction(target)
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert preview.any_blocked is False
            assert preview.blocked_count == 0
            assert preview.envelope_count == 1

            plan = preview.plans[0]
            assert plan.blocked is False
            assert plan.target_will_be_generated is True
            assert plan.target_estimated_before == Decimal("0.00")
            # Leftover: 100 estimated - 40 entries = 60.
            assert plan.leftover == Decimal("60.00")
            assert plan.target_estimated_after == Decimal("60.00")

    def test_template_inactive_is_actionable_creates_row(
        self, app, db, seed_user, seed_periods,
    ):
        """No target + a rule that fires elsewhere: actionable, a fresh row.

        The Father's Day shape: the source is the engine's row under the
        every-paycheck cadence the owner then re-authored to yearly.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            _fires_in_no_seed_paycheck(template)
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is False
            assert plan.target_will_be_generated is True
            assert plan.target_estimated_before == Decimal("0.00")
            # Leftover: 100 estimated - 30 entries = 70.
            assert plan.leftover == Decimal("70.00")
            assert plan.target_estimated_after == Decimal("70.00")

    def test_two_mutable_targets_marked_blocked_ambiguous(
        self, app, db, seed_user, seed_periods,
    ):
        """An override beside the canonical -> blocked, BLOCK_AMBIGUOUS_TARGETS.

        Two mutable rows a CADENCE names are no longer ambiguous (the case
        below); a row the OWNER moved in is, whatever its occurrence, because
        the tie-break may not choose a row the owner priced.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            moved_by_the_owner(
                generate_row_of(template, seed_periods[2]),
                into=seed_periods[1],
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is True
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_AMBIGUOUS_TARGETS
            )
            assert "open row" in plan.block_reason.lower()

    def test_two_targets_answering_different_occurrences_top_up_the_earliest(
        self, app, db, seed_user,
    ):
        """A cadence that names one paycheck twice is NOT ambiguous.

        **The developer's ruling of 2026-08-28**, and the case plan step R17
        created: the re-keyed unique index
        (``idx_transactions_template_scenario_occurrence``) lets one paycheck
        hold two generated rows answering two different occurrences -- a
        monthly bill at a pay cadence of 30 days or more, measured at 2 of the
        developer's templates at cadence 30 and 11 at cadence 31.  The old
        guard called any second mutable row corrupt, because the paycheck-keyed
        index made it so; reading it that way now would refuse the whole
        carry-forward batch over a correct state.

        **The EARLIEST occurrence receives the leftover**: the unspent money
        rolls into the paycheck to meet the next obligation, and the next
        obligation is the first occurrence in it.

        The pair is the ENGINE's (plan step balance:X-cf-3b): a monthly
        definition on the pinned 60-day calendar
        (:func:`definition_firing_twice_in_a_paycheck`), its one row in the
        first paycheck as the source and its two in the second as the target.
        **Which row was chosen is read off the ROW after the mutating call**,
        not off a figure: both rows are dated on the same due date
        (``compute_due_date`` reads the paycheck, not the occurrence -- ledger
        row D18, plan step R5), so the definition's series prices them alike
        and no preview figure can tell them apart.  The preview grades the
        ruling (not ambiguous); the execution grades WHICH of the two received
        the money.
        """
        with app.app_context():
            template, first_paycheck, target_period = (
                definition_firing_twice_in_a_paycheck(
                    db.session, seed_user, name="Spending Money",
                )
            )
            template.is_envelope = True
            source = generate_row_of(template, first_paycheck)
            populate_in_a_fresh_pass(
                seed_user["user"].id, [target_period.id],
            )
            first, second = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id, pay_period_id=target_period.id,
                )
                .order_by(Transaction.occurs_on)
                .all()
            )
            assert first.occurs_on < second.occurs_on, "fixture: two occurrences"
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                first_paycheck.id, target_period.id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is False, (
                "two rows answering two occurrences is a correct state, not "
                "an ambiguous one"
            )
            assert plan.target_estimated_before == Decimal("100.00")

            carry_forward_service.carry_forward_unpaid(
                first_paycheck.id, target_period.id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(first)
            db.session.refresh(second)
            # 100 of its own plan + (100 - 30) leftover = 170, on the EARLIEST
            # occurrence; the later one is still the rule's, untouched.
            assert first.is_override is True
            assert first.estimated_amount == Decimal("170.00"), (
                "the leftover must top up the EARLIEST occurrence in the "
                "target paycheck"
            )
            assert second.is_override is False
            assert second.estimated_amount is None

    def test_an_override_sibling_in_the_target_still_blocks(
        self, app, db, seed_user, seed_periods,
    ):
        """A row the OWNER owns is never chosen by an occurrence tie-break.

        An adversarial review of plan step R17 found this: a row moved into the
        target paycheck through the PATCH door carries ``is_override = True``
        and its own EARLIER ``occurs_on``, so ranking candidates by occurrence
        would select it over the paycheck's own canonical -- and the caller
        then writes ``estimated_amount = resolve + leftover`` and clears
        ``amount_source_id``, overwriting a figure the owner typed by hand.

        The 2026-08-28 ruling was about two rows a CADENCE names.  It was never
        about a row the owner owns, and the guard this leaf relaxed used to
        refuse here.  It still does.

        The moved row is the engine's row of the FIRST paycheck, which the
        owner moved into the third and re-priced (plan step balance:X-cf-3b),
        so its occurrence really is the earlier one; the carry runs from the
        second paycheck into the third.  A row moved in from a LATER paycheck
        would carry a later occurrence, and the tie-break would then pick the
        canonical whether or not the override term existed.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[1])
            # The paycheck's own canonical, and a row the owner moved in --
            # earlier occurrence, hand-priced, and theirs.
            canonical = generate_row_of(template, seed_periods[2])
            moved = repriced_by_the_owner(
                moved_by_the_owner(
                    generate_row_of(template, seed_periods[0]),
                    into=seed_periods[2],
                ),
                "777.77",
            )
            assert moved.occurs_on < canonical.occurs_on, "fixture: earlier"
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[1].id, seed_periods[2].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is True, (
                "the leftover was about to be folded into a row the owner "
                "priced by hand"
            )
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_AMBIGUOUS_TARGETS
            )

    def test_two_targets_answering_the_SAME_occurrence_still_block(
        self, app, db, seed_user, seed_periods,
    ):
        """The corrupt state the guard was actually written for still refuses.

        The companion to the case above, and what keeps that relaxation from
        having deleted the guard: two rows answering ONE occurrence is a state
        no cadence produces and the dated unique index forbids, so which of
        them to credit is a guess.  Without this, widening
        ``_leftover_recipient`` to "just take the earliest" would pass every
        other carry-forward test in this file.

        The pair is staged the way DC-06's duplicate case stages its own: the
        target's canonical, and the engine's row of the NEXT paycheck filed
        back into the target WITHOUT the override flag a move would have set,
        rewritten to answer the canonical's occurrence once the index that
        forbids it is dropped.  **The index is not recreated afterwards**:
        every case clones its own database (``tests.conftest.db``), so the
        drop cannot outlive this one, and the ``CREATE UNIQUE INDEX`` that
        used to stand here was a second spelling of the schema -- in the shape
        migration ``e7c3a1f9b482`` (plan step X-au-h) had already retired.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            first = generate_row_of(template, seed_periods[1])
            second = generate_row_of(template, seed_periods[2])
            db.session.execute(db.text(
                "DROP INDEX budget.idx_transactions_template_scenario_occurrence"
            ))
            second.pay_period_id = seed_periods[1].id
            second.occurs_on = first.occurs_on
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is True
            assert plan.block_reason_code == (
                carry_forward_service.BLOCK_AMBIGUOUS_TARGETS
            )

    def test_only_soft_deleted_target_is_actionable_creates_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Only soft-deleted rows in target -> actionable, predicts a fresh row.

        The engine refuses to regenerate over a soft-deleted row, so
        there is no canonical to bump.  The preview predicts a freshly
        created override row carrying the leftover (the created row is
        is_override=True, so it never collides with the soft-deleted one).
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            target.is_deleted = True
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            plan = preview.plans[0]
            assert plan.blocked is False
            assert plan.target_will_be_generated is True
            assert plan.target_estimated_before == Decimal("0.00")
            # Leftover: 100 estimated - 30 entries = 70.
            assert plan.target_estimated_after == Decimal("70.00")

    def test_blocked_plans_excluded_from_actionable_count(
        self, app, db, seed_user, seed_periods,
    ):
        """Blocked envelope plans do not count toward envelope_count.

        envelope_count names ACTIONABLE plans for the modal's summary
        line -- the user wants to know "how many will run if I
        confirm."  Blocked plans go in blocked_count instead.  Only the
        AMBIGUOUS target states still block: an override beside the
        canonical, or two rows answering one occurrence.
        """
        with app.app_context():
            # One actionable + one blocked (an override beside the canonical).
            template_a = _create_envelope_template(
                seed_user, name="Envelope A",
                category_key="Groceries",
            )
            source_a = generate_row_of(template_a, seed_periods[0])
            generate_row_of(template_a, seed_periods[1])
            _add_entry(source_a, seed_user, "20.00")

            template_b = _create_envelope_template(
                seed_user, name="Envelope B",
                category_key="Rent",
            )
            source_b = generate_row_of(template_b, seed_periods[0])
            generate_row_of(template_b, seed_periods[1])
            moved_by_the_owner(
                generate_row_of(template_b, seed_periods[2]),
                into=seed_periods[1],
            )
            _add_entry(source_b, seed_user, "50.00")

            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            assert len(preview.plans) == 2
            assert preview.any_blocked is True
            assert preview.blocked_count == 1
            assert preview.envelope_count == 1
