"""A batch across kinds and what it must keep: envelope, discrete and transfer rows each take their
own branch and roll back together, cell equals subtotal equals balance, an income definition
takes the discrete path, and a topped-up target blocks a later recurrence run.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import carry_forward_service, recurrence_engine
from app.services import balance_at, status_seam
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.cash_ledger import contribution_of
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    amount_basis_for,
    generate_row_of,
    make_income_template,
    moved_by_the_owner,
)

from ._helpers import (
    _create_transaction,
    _create_transfer_in_period,
    _create_template,
    _create_envelope_template,
    _add_entry,
)


class TestCarryForwardEnvelopeMixedBatch:
    """Heterogeneous source period: each row takes the correct branch."""

    def test_mixed_envelope_discrete_transfer_batch(
        self, app, db, seed_user, seed_periods,
    ):
        """One envelope + one discrete + one transfer + one ad-hoc.

        Verifies that each row takes the correct branch and that a
        batch with all four flavors completes without cross-bucket
        interference.

        Setup (period 0):
          * Envelope template ($100), source row, $40 entry.
          * Discrete template ($50), source row.
          * Ad-hoc transaction ($30).
          * Transfer ($200 to savings).

        Only the envelope has a target canonical in period 1 (its
        rule-generated row at the default amount).  The discrete definition
        generates its period-0 source alone, so that row lands in period 1
        with no sibling.
        """
        with app.app_context():
            envelope_template = _create_envelope_template(
                seed_user, name="Envelope Spending",
                default_amount="100.00", category_key="Groceries",
            )
            envelope_source = generate_row_of(envelope_template, seed_periods[0])
            envelope_target = generate_row_of(envelope_template, seed_periods[1])
            _add_entry(envelope_source, seed_user, "40.00")

            # Discrete template: is_envelope defaults to False.
            discrete_template = _create_template(
                seed_user, name="Recurring Bill",
                amount="50.00", category_key="Rent",
            )
            discrete_source = generate_row_of(
                discrete_template, seed_periods[0],
            )

            # Ad-hoc: a one-off, placed with a rule-less definition.
            adhoc = _create_transaction(
                seed_user, seed_periods,
                name="Ad-hoc Expense",
                amount="30.00",
            )

            # Transfer, Projected in period 0.
            transfer = _create_transfer_in_period(
                seed_user, seed_periods, period_index=0,
            )

            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            # Count semantics: 1 per envelope + 1 per discrete +
            # 1 per ad-hoc + 1 per transfer.
            assert count == 4

            # Envelope source settled, target bumped.
            db.session.refresh(envelope_source)
            db.session.refresh(envelope_target)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert envelope_source.status_id == done_id
            assert settled_figure(envelope_source) == Decimal("40.00")
            assert envelope_source.pay_period_id == seed_periods[0].id
            # 100 + (100 - 40) = 160.
            assert envelope_target.estimated_amount == Decimal("160.00")
            assert envelope_target.is_override is True

            # Discrete source moved with is_override=True.
            db.session.refresh(discrete_source)
            assert discrete_source.pay_period_id == seed_periods[1].id
            assert discrete_source.is_override is True
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert discrete_source.status_id == projected_id

            # Ad-hoc moved without is_override: its definition does not recur,
            # and the move keys the flag on ``recurs`` (plan step
            # balance:X-bi-7a), not on the link.
            db.session.refresh(adhoc)
            assert adhoc.pay_period_id == seed_periods[1].id
            assert adhoc.is_override is False

            # Transfer moved; no flag, as the ad-hoc transaction above takes
            # none: the transfer move keys on ``recurs`` since plan step
            # balance:X-ci-1 (ruling R-BAL93), and this transfer names no
            # definition.  It asserted ``True`` until that leaf.
            db.session.refresh(transfer)
            assert transfer.pay_period_id == seed_periods[1].id
            assert transfer.is_override is False

    def test_envelope_failure_rolls_back_other_branches(
        self, app, db, seed_user, seed_periods,
    ):
        """Ambiguous envelope target -- atomic rollback affects every branch.

        Setup: the prior test's envelope and discrete rows in period 0 (no
        ad-hoc row, no transfer), and the envelope target in period 1 holds
        an owner-moved override beside its canonical (the state
        ``_leftover_recipient`` refuses).  When the envelope branch refuses
        with AMBIGUOUS, the discrete move (which would have happened
        mid-batch in the no_autoflush block) and the envelope's settle must
        NOT persist.

        The route's rollback restores every prior pending mutation;
        this test verifies the atomicity contract end-to-end.
        """
        with app.app_context():
            envelope_template = _create_envelope_template(
                seed_user, name="Envelope Spending",
                default_amount="100.00", category_key="Groceries",
            )
            envelope_source = generate_row_of(envelope_template, seed_periods[0])
            # An override beside the canonical -> AMBIGUOUS.
            generate_row_of(envelope_template, seed_periods[1])
            moved_by_the_owner(
                generate_row_of(envelope_template, seed_periods[2]),
                into=seed_periods[1],
            )
            _add_entry(envelope_source, seed_user, "40.00")

            discrete_template = _create_template(
                seed_user, name="Recurring Bill",
                amount="50.00", category_key="Rent",
            )
            discrete_source = generate_row_of(
                discrete_template, seed_periods[0],
            )

            db.session.commit()

            envelope_source_id = envelope_source.id
            discrete_source_id = discrete_source.id

            with pytest.raises(ValidationError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.rollback()
            db.session.expire_all()

            # Discrete source must NOT have moved or flipped is_override.
            discrete_after = db.session.get(
                Transaction, discrete_source_id,
            )
            assert discrete_after.pay_period_id == seed_periods[0].id
            assert discrete_after.is_override is False

            # Envelope source must NOT have settled.
            envelope_after = db.session.get(
                Transaction, envelope_source_id,
            )
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert envelope_after.status_id == projected_id
            assert status_seam.recorded_settlement(envelope_after) is None


class TestCarryForwardEnvelopeBalanceInvariant:
    """Cell == period subtotal == balance projection invariant.

    The whole point of Option F: every consumer of the post-carry
    state reads the same number for the envelope.  This test reads the
    account's balances on the post-carry state
    (``balance_at.cash_balance_map``, what the app renders) and asserts the
    forward cash flow matches the period subtotal.
    """

    def test_post_carry_balance_matches_subtotal(
        self, app, db, seed_user, seed_periods,
    ):
        """The source's in-flight purchase holds in period 0; the bumped target reduces period 1.

        Setup: $100 envelope, $65 entries, target rule-generated.

        Post-carry expected balance trajectory (anchor period 0,
        starting balance $1000 from seed_user, ``as_of`` pinned to period 0's
        start):
          period 0: the source is settled, and its $65 purchase -- made in
                    period 0, not yet seen to leave -- is IN FLIGHT from
                    ``as_of + 1`` (ruling **R-BAL77**, plan step
                    ``balance:X-bi-4a``) -> end_balance = 1000 - 65 = 935
          period 1: target $135 expense -> end_balance = 935 - 135 = 800

        Through ``X-bi-3e`` the close booked the $65 as the row's own leg on
        the close day -- TODAY, two years past the window -- so period 0 read
        $1,000.00 and money spent in period 0 never showed in it.

        Period 1 subtotal: $135 (just the bumped canonical).
        Both subtotal and balance reduction agree at $135.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            # Pull every non-deleted transaction across the two periods.  The
            # balance below loads the account's own rows, so this query only
            # feeds the period-1 subtotal.
            txns = (
                db.session.query(Transaction)
                .filter(
                    Transaction.scenario_id == seed_user["scenario"].id,
                    Transaction.is_deleted.is_(False),
                    Transaction.pay_period_id.in_(
                        [seed_periods[0].id, seed_periods[1].id]
                    ),
                ).all()
            )

            account = seed_user["account"]
            # Plan step X-g4b: read what the app renders.  ``as_of`` is pinned
            # inside period 0 so ruling R-G does not clamp the still-Projected
            # target out of the window.
            assert txns
            balances = balance_at.cash_balance_map(
                account,
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )

            # Anchor balance from seed_user is $1000; the settled source's
            # un-dated $65 purchase is in flight inside period 0.
            assert balances[seed_periods[0].id] == Decimal("935.00")
            # Target now $135; balance drops by exactly that amount.
            assert balances[seed_periods[1].id] == Decimal("800.00")
            # Forward cash flow matches the bumped envelope exactly.
            assert (
                balances[seed_periods[0].id]
                - balances[seed_periods[1].id]
            ) == Decimal("135.00")

            # Period subtotal: sum what the PROJECTED rows in period 1
            # contribute.  Same projected-only filter, same answer.
            #
            # Asked of the amount model since plan step X-bx: every row in this
            # sum is Projected by the filter one line down, and
            # ``row_valuation.settled_contribution`` refuses a row that has not
            # settled rather than reading its plan column (finding BAL-465).
            # ``contribution_of`` is the producer for a row that may not have
            # settled, and it always was.
            period_1_txns = [
                t for t in txns
                if t.pay_period_id == seed_periods[1].id
            ]
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            subtotal = sum(
                (contribution_of(t, amount_basis_for(t)) for t in period_1_txns
                 if t.status_id == projected_id),
                Decimal("0"),
            )
            assert subtotal == Decimal("135.00")


class TestCarryForwardEnvelopeIncomeFalse:
    """Income templates may exist with is_envelope=False (Phase 2 default).

    Phase 2 rejects ``is_envelope=True`` on income at the schema layer,
    so income templates always take the discrete branch.  This test
    confirms the carry-forward branching honors that contract.
    """

    def test_income_template_takes_discrete_path(
        self, app, db, seed_user, seed_periods,
    ):
        """Income + is_envelope=False: row moved whole, is_override=True.

        Setup: an income TransactionTemplate with is_envelope=False
        (the only legal income state per Phase 2).  Projected income
        row in period 0.

        Expected: discrete branch fires -- pay_period flips to target,
        is_override=True.  No settle, no entries-sum logic.
        """
        with app.app_context():
            template = make_income_template(
                db.session, seed_user, amount="2500.00",
                name="Paycheck", category_key="Salary", is_envelope=False,
            )
            source = generate_row_of(template, seed_periods[0])
            db.session.commit()
            source_id = source.id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1
            db.session.refresh(source)
            # Discrete behaviour: row moved, is_override=True, status
            # unchanged (still Projected -- not settled).
            assert source.pay_period_id == seed_periods[1].id
            assert source.is_override is True
            assert source.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            # No settlement recorded: the discrete branch never calls
            # settle_from_entries.
            assert status_seam.recorded_settlement(source) is None
            assert source.settled_on is None

            # No new rows generated in target -- the source itself is
            # the row in target now.
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
            assert target_rows[0].id == source_id


class TestCarryForwardEnvelopeRecurrenceSkip:
    """Bumped canonical (is_override=True) blocks future recurrence runs."""

    def test_recurrence_engine_does_not_double_generate_after_bump(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-running the engine on a bumped envelope target does not add a sibling.

        Mirrors the existing TestCarryForwardOverrideSibling test for
        the envelope path: after carry-forward bumps target's
        is_override=True, the engine's skip-on-override clause keeps
        the row count at 1.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "20.00")
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            # Pre-engine: source DONE in period 0 + bumped canonical
            # in period 1 = 2 rows total for the template/scenario.
            pre_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert pre_count == 2

            # Run recurrence engine for periods 0 + 1 again -- it must
            # not add a third row.  The DONE source in period 0 has
            # is_immutable status; the bumped canonical in period 1
            # has is_override=True.  Both trigger skip clauses.
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods[:2]},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            post_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert post_count == 2
