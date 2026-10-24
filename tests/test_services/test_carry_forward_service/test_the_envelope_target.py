"""Where an envelope's leftover lands: a target row the engine generates, a separate row beside a
settled one, a fresh override row when no row can take it, a chain and two sources into one
target, and the refusal of a doubled target.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import carry_forward_service, transaction_service
from app.services.balance_at import BalanceContext
from app.services.row_valuation import settled_figure
from tests._test_helpers import generate_row_of, moved_by_the_owner

from ._helpers import _create_envelope_template, _fires_in_no_seed_paycheck, _plan_of, _add_entry


class TestCarryForwardEnvelopeMissingTarget:
    """Target canonical does not yet exist -- engine generates it."""

    def test_engine_creates_canonical_when_missing(
        self, app, db, seed_user, seed_periods,
    ):
        """No target row + active rule: engine creates canonical, branch bumps it.

        Setup: $100 envelope with EVERY_PERIOD rule.  Source row in
        period 0 has a $30 entry.  Period 1 has NO row (recurrence
        engine has not run for period 1 yet).

        Expected: branch calls generate_for_template, engine creates
        a canonical at $100, branch bumps it by 70 -> $170 with
        is_override=True.  The newly-created row's pay_period_id ==
        seed_periods[1].id and template_id == template.id.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            # Sanity: target period has no row before carry-forward.
            pre_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                ).count()
            )
            assert pre_count == 0

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            # Exactly one row in target after the engine generated +
            # the branch bumped.
            assert len(target_rows) == 1
            target = target_rows[0]
            # 100 (engine-generated canonical) + 70 (leftover) = 170.
            assert target.estimated_amount == Decimal("170.00")
            assert target.is_override is True

    def test_template_inactive_in_target_creates_override_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A rule that names no row in the target + empty target: create a row.

        The engine places nothing there, so there is no canonical to bump.
        Instead of refusing, the branch settles the source and creates a
        fresh ``is_override`` row in the target carrying the leftover -- the
        Father's Day case (a yearly envelope rolling into an off-anniversary
        period).  The source is the engine's row under the every-paycheck
        cadence; the owner then re-authors the cadence to a yearly one
        (:func:`_fires_in_no_seed_paycheck`).

        Source: $100 envelope, $30 spent -> settles Paid at $30,
        leftover $70.  Target had no row -> one new Projected override
        row at $70 appears.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            _fires_in_no_seed_paycheck(template)
            _add_entry(source, seed_user, "30.00")
            db.session.commit()
            source_id = source.id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1

            # Source settled Paid at its actual spend ($30).
            source_after = db.session.get(Transaction, source_id)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source_after.status_id == done_id
            assert settled_figure(source_after) == Decimal("30.00")

            # A single fresh override row carries the $70 leftover.
            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1
            target = target_rows[0]
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            # 0 (fresh row) + 70 (leftover) = 70.
            assert target.estimated_amount == Decimal("70.00")
            assert target.is_override is True
            assert target.status_id == projected_id


class TestCarryForwardEnvelopeSettledTarget:
    """Target canonical is finalised -- leftover rolls into a separate row."""

    def test_settled_target_keeps_paid_row_and_creates_leftover_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Target Paid: Paid row untouched, leftover lands in a new row.

        The user already marked the target's canonical Paid.  Bumping it
        would overwrite that decision, so the branch leaves the Paid row
        alone and creates a separate Projected override row carrying the
        leftover -- the user's chosen "add as separate row" rule.

        Setup: $100 envelope, source in period 0 with $40 entries
        (leftover $60), target canonical in period 1 already Paid.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            transaction_service.settle_transaction(target)
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            source_id = source.id
            target_id = target.id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1

            # Source settled Paid at its $40 actual spend.
            source_after = db.session.get(Transaction, source_id)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source_after.status_id == done_id
            assert settled_figure(source_after) == Decimal("40.00")

            # The pre-existing Paid row is untouched: still the rule's, its
            # plan still 100, and what it booked still 100.
            target_after = db.session.get(Transaction, target_id)
            assert target_after.status_id == done_id
            assert target_after.estimated_amount is None
            assert _plan_of(target_after) == Decimal("100.00")
            assert settled_figure(target_after) == Decimal("100.00")
            assert target_after.is_override is False

            # A separate Projected override row carries the $60 leftover.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            new_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                    is_override=True,
                ).all()
            )
            assert len(new_rows) == 1
            # 0 (fresh row) + 60 (leftover) = 60.
            assert new_rows[0].estimated_amount == Decimal("60.00")
            assert new_rows[0].status_id == projected_id


class TestCarryForwardEnvelopeMultiHop:
    """Chain A -> B -> C: each hop settles + bumps cleanly."""

    def test_two_hop_chain_settles_each_and_bumps_each(
        self, app, db, seed_user, seed_periods,
    ):
        """A->B then B->C: both hops behave independently and correctly.

        Setup: $100 envelope per period 0, 1, 2.  No entries on any
        row.

        First call: source A (period 0, $0 entries) settles at $0,
        target B's canonical bumps to $200 (100 + 100 leftover).
        is_override flips True on B.

        Second call: source = B's canonical (now $200, is_override=True,
        Projected) -- still envelope-tracked by template flag.  $0
        entries.  Settles at $0, target C's canonical bumps by 200 ->
        $300.  is_override flips True on C.

        Net forward cash flow over A+B+C: A excluded (settled), B
        excluded (settled), C reduces balance by $300.  All three
        $100 envelopes accounted for in C.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            row_a = generate_row_of(template, seed_periods[0])
            row_b = generate_row_of(template, seed_periods[1])
            row_c = generate_row_of(template, seed_periods[2])
            db.session.commit()

            # Hop 1: A -> B.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(row_a)
            db.session.refresh(row_b)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert row_a.status_id == done_id
            assert settled_figure(row_a) == Decimal("0.00")
            # B got the full $100 leftover from A.
            assert row_b.estimated_amount == Decimal("200.00")
            assert row_b.is_override is True
            assert row_b.status_id == projected_id

            # Hop 2: B (now $200) -> C.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[1].id, seed_periods[2].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(row_b)
            db.session.refresh(row_c)
            assert row_b.status_id == done_id
            assert settled_figure(row_b) == Decimal("0.00")
            # C absorbs B's full $200.
            assert row_c.estimated_amount == Decimal("300.00")
            assert row_c.is_override is True
            assert row_c.status_id == projected_id

            # Sanity: still exactly one row per period for this template.
            for period in (seed_periods[0], seed_periods[1], seed_periods[2]):
                rows = (
                    db.session.query(Transaction)
                    .filter_by(
                        template_id=template.id,
                        pay_period_id=period.id,
                        scenario_id=seed_user["scenario"].id,
                        is_deleted=False,
                    ).all()
                )
                assert len(rows) == 1, (
                    f"Period {period.id}: expected 1 envelope row, "
                    f"got {len(rows)}."
                )


class TestCarryForwardEnvelopeMultipleSourcesToSameTarget:
    """Two sources from different past periods bump the same target.

    The target canonical was promoted to is_override=True by the
    first carry-forward.  The second carry-forward must find the
    SAME row (despite is_override=True) and re-bump it, NOT create a
    new sibling.  This test demonstrates why the lookup omits the
    is_override filter.
    """

    def test_two_sources_compound_into_single_target_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Carry A->C then B->C: target absorbs both leftovers.

        Setup: $100 envelope.  Sources in periods 0 and 1.  Target in
        period 2 (rule-generated canonical at $100).  No entries on
        either source.

        First call: A -> C.  Target canonical at $100, lookup finds
        is_override=False row, bumps to $200, sets is_override=True.

        Second call: B -> C.  Target now at $200, is_override=True.
        Lookup MUST find this row (not skip it) and bump it again to
        $300.

        Expected: one row in target with estimated=$300, is_override=
        True.  Both sources DONE at $0.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            row_a = generate_row_of(template, seed_periods[0])
            row_b = generate_row_of(template, seed_periods[1])
            row_c = generate_row_of(template, seed_periods[2])
            db.session.commit()

            # Hop 1: A -> C.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[2].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            db.session.refresh(row_c)
            assert row_c.estimated_amount == Decimal("200.00")
            assert row_c.is_override is True

            # Hop 2: B -> C.  C is now is_override=True; lookup must
            # still find it.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[1].id, seed_periods[2].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(row_a)
            db.session.refresh(row_b)
            db.session.refresh(row_c)

            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert row_a.status_id == done_id
            assert row_b.status_id == done_id
            # Two $100 leftovers compounded onto the original $100.
            assert row_c.estimated_amount == Decimal("300.00")
            assert row_c.is_override is True

            # Single row in target -- the second hop did NOT create a
            # sibling.
            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[2].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1


class TestCarryForwardEnvelopeCorruptDoubledRow:
    """Legacy doubled-row pre-fix state: refuse and require manual cleanup."""

    def test_two_non_deleted_rows_in_target_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """Two non-deleted target rows for same envelope: ValidationError.

        Reproduces the production state the user has from pre-Phase-4
        carry-forwards (relaxed unique index allowed is_override=True
        + canonical to coexist).  The branch refuses rather than
        bumping one and ignoring the other; the user must clean up
        manually.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            # The target's canonical, and an override the owner moved in.
            generate_row_of(template, seed_periods[1])
            moved_by_the_owner(
                generate_row_of(template, seed_periods[2]),
                into=seed_periods[1],
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            with pytest.raises(ValidationError) as exc_info:
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.rollback()

            assert "duplicate" in str(exc_info.value).lower()
            assert str(seed_periods[1].id) in str(exc_info.value)


class TestCarryForwardEnvelopeCreatesRowWhenNoCanonical:
    """No mutable canonical to bump -> create a fresh override row.

    Covers the cases that used to refuse: an inactive (e.g. yearly)
    template, and a destination whose only row is finalised or
    soft-deleted.  The leftover is never dropped; it lands in a new
    Projected is_override row that subsequent carry-forwards top up
    rather than duplicate.
    """

    def test_zero_spend_inactive_template_creates_full_leftover_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Father's Day: yearly envelope, $0 spent -> $100 rolls to a new row.

        A once-yearly envelope (a rule that fires in no seed paycheck)
        budgeted $100 with no purchases.  Source settles Paid at $0; the
        full $100 leftover lands in a fresh Projected override row in the
        target period.  The source is the engine's row under the
        every-paycheck cadence the owner then re-authored to yearly.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, name="Father's Day",
            )
            source = generate_row_of(template, seed_periods[0])
            _fires_in_no_seed_paycheck(template)
            db.session.commit()
            source_id = source.id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1

            # Source settled Paid at $0 (no purchases).
            source_after = db.session.get(Transaction, source_id)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source_after.status_id == done_id
            assert settled_figure(source_after) == Decimal("0.00")

            # The full $100 lands in one fresh override row.
            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert target_rows[0].estimated_amount == Decimal("100.00")
            assert target_rows[0].is_override is True
            assert target_rows[0].status_id == projected_id

    def test_repeated_carry_forward_tops_up_created_row_no_duplicate(
        self, app, db, seed_user, seed_periods,
    ):
        """Second carry-forward bumps the created row, never duplicates it.

        Target period already has a Paid canonical.  A first carry-
        forward creates a separate $100 override row beside it; a second
        carry-forward from another source finds that one mutable row and
        bumps it to $200 -- the destination keeps exactly two rows (the
        Paid canonical + one override), proving idempotency.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            # Target (period 2) canonical already Paid -> not bumpable.
            paid = generate_row_of(template, seed_periods[2])
            transaction_service.settle_transaction(paid)
            db.session.commit()
            paid_id_pk = paid.id

            # Hop 1: period 0 -> target.  Creates a $100 override row.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[2].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            # Hop 2: period 1 -> target.  Tops up the same override row.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[1].id, seed_periods[2].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[2].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            # Exactly two: the untouched Paid canonical + one override.
            assert len(rows) == 2
            overrides = [r for r in rows if r.is_override]
            assert len(overrides) == 1
            # 0 + 100 (hop 1) + 100 (hop 2) = 200.
            assert overrides[0].estimated_amount == Decimal("200.00")
            # The Paid canonical was never touched: still the rule's row.
            paid_after = db.session.get(Transaction, paid_id_pk)
            assert paid_after.is_override is False
            assert paid_after.estimated_amount is None
            assert _plan_of(paid_after) == Decimal("100.00")

    def test_only_soft_deleted_target_creates_override_row(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleted-only target: create a fresh override row beside it.

        The engine refuses to regenerate over a soft-deleted row.  The
        branch creates an is_override row (excluded from the partial
        unique index) carrying the leftover, leaving the soft-deleted
        row untouched.

        Source: $100 envelope, $30 spent -> leftover $70.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            deleted_target = generate_row_of(template, seed_periods[1])
            deleted_target.is_deleted = True
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1
            live_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(live_rows) == 1
            # 0 (fresh row) + 70 (leftover) = 70.
            assert live_rows[0].estimated_amount == Decimal("70.00")
            assert live_rows[0].is_override is True
