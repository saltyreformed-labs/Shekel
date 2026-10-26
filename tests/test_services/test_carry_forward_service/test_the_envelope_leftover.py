"""An envelope's leftover: the source settles at its purchases and the unspent budget rolls into
the target's row; nothing rolls on an exact spend or an overspend, and a refund rolls more than
the budget.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.models.transaction import Transaction
from app.services import carry_forward_service
from app.services.balance_at import BalanceContext
from app.services.row_valuation import settled_figure
from tests._test_helpers import generate_row_of

from ._helpers import _create_envelope_template, _plan_of, _add_entry


class TestCarryForwardEnvelopePartialSpend:
    """Source spent some -- leftover rolls forward to target.

    The wife's spending money envelope is the canonical example: $65
    of $100 spent, $35 unspent.  Source settles at $65 (Paid), target
    bumps to $135 with is_override=True.  Exactly one row exists per
    (template, period) afterwards so cell == period subtotal ==
    balance projection.
    """

    def test_partial_spend_settles_source_and_bumps_target(
        self, app, db, seed_user, seed_periods,
    ):
        """Source DONE at entries_sum, target +leftover, is_override=True.

        Setup: $100 envelope in source period 0, $65 of debit entries.
        Target period 1 has the rule-generated canonical at $100.

        Expected:
          source.status_id      == DONE
          settled_figure(source)  == 65.00
          _plan_of(source)      == 100.00 (untouched: still the rule's)
          source.pay_period_id  == seed_periods[0].id (NOT moved)
          source.is_override    == False (envelope source is settled,
                                         not relocated)
          target.estimated      == 135.00 (100 + 35 leftover)
          target.is_override    == True
          target.pay_period_id  == seed_periods[1].id
          row count in target   == 1 (no sibling created)
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 1

            db.session.refresh(source)
            db.session.refresh(target)

            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source.status_id == done_id
            # Worked example: 65 of 100 spent.  The source's PLAN is untouched:
            # still the rule's (no figure of its own), still resolving to 100.
            assert settled_figure(source) == Decimal("65.00")
            assert source.estimated_amount is None
            assert _plan_of(source) == Decimal("100.00")
            assert source.pay_period_id == seed_periods[0].id
            assert source.is_override is False

            # Target absorbed the leftover: 100 + (100 - 65) = 135.
            assert target.estimated_amount == Decimal("135.00")
            assert target.is_override is True
            assert target.pay_period_id == seed_periods[1].id

            target_rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(target_rows) == 1, (
                "Envelope rollover must produce ONE row per "
                "(template, period) -- found "
                f"{len(target_rows)} rows in target."
            )

    def test_partial_spend_with_credit_entries_in_sum(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit entries count toward entries_sum (mirrors helper contract).

        purchases_total sums BOTH debit and credit entries
        for analytics correctness.  That is the contract the carry-
        forward branch inherits.

        Setup: $40 debit + $25 credit = $65 against $100 envelope.
        Expected: leftover = 100 - 65 = 35, target bumped by 35.
        """
        with app.app_context():
            template = _create_envelope_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "40.00",
                       description="Kroger debit")
            _add_entry(source, seed_user, "25.00",
                       description="Amazon credit", is_credit=True)
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            # 40 + 25 = 65, leftover = 100 - 65 = 35.
            assert settled_figure(source) == Decimal("65.00")
            assert target.estimated_amount == Decimal("135.00")
            assert target.is_override is True


class TestCarryForwardEnvelopeZeroEntries:
    """Source spent nothing -- full envelope rolls forward."""

    def test_zero_entries_rolls_full_envelope(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entries: source DONE at $0, target += full estimated.

        Setup: $200 envelope, no entries against source.
        Expected:
          source.status         == DONE
          settled_figure(source)  == 0.00 (NOT a fallback to estimated)
          target.estimated      == 200 + 200 = 400.00
          target.is_override    == True
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="200.00",
            )
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source.status_id == done_id
            assert settled_figure(source) == Decimal("0.00")
            # 200 + (200 - 0) = 400.
            assert target.estimated_amount == Decimal("400.00")
            assert target.is_override is True


class TestCarryForwardEnvelopeOverspend:
    """Source spent more than estimated -- no rollover, target untouched."""

    def test_overspend_settles_source_without_bumping_target(
        self, app, db, seed_user, seed_periods,
    ):
        """entries_sum > estimated: leftover clamps to 0, target unchanged.

        Setup: $80 + $40 = $120 entries against $100 envelope.
        Expected:
          settled_figure(source)  == 120.00 (truth, not clamped)
          target.estimated      == 100.00 (untouched)
          target.is_override    == False (untouched)
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "80.00")
            _add_entry(source, seed_user, "40.00",
                       description="Overspend purchase")
            db.session.commit()

            target_plan_before = _plan_of(target)
            target_is_override_before = target.is_override

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            # 80 + 40 = 120 -- exceeds the 100 estimate.
            assert settled_figure(source) == Decimal("120.00")
            # Target is untouched because leftover = max(0, 100-120) = 0:
            # still the rule's row, planning what it planned.
            assert target.estimated_amount is None
            assert _plan_of(target) == target_plan_before == Decimal("100.00")
            assert target.is_override == target_is_override_before
            assert target.is_override is False

    def test_exact_spend_settles_source_without_bumping_target(
        self, app, db, seed_user, seed_periods,
    ):
        """entries_sum == estimated: leftover = 0, target unchanged.

        Setup: $50 + $50 = $100 entries against $100 envelope.
        Expected: source DONE at 100, target untouched.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "50.00")
            _add_entry(source, seed_user, "50.00")
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            assert settled_figure(source) == Decimal("100.00")
            assert target.estimated_amount is None
            assert _plan_of(target) == Decimal("100.00")
            assert target.is_override is False


class TestCarryForwardEnvelopeRefundedBelowZero:
    """A refund carries the source's entries NEGATIVE, so the leftover EXCEEDS.

    Ruling **bank_import:R-II**, developer ruling 2026-09-01, plan step
    ``bank_import:X-gj-2b-3``.  A merchant credit files as a NEGATIVE purchase,
    so ``entries_sum`` can be below zero and ``max(0, budget - entries_sum)``
    can exceed the budget.  The `max` clamps an OVERSPENT envelope and was
    never a bound against a refunded one -- and it must not become one:
    ``entry_service.compute_remaining`` answers the same figure for the same
    row on screen, so a cap here would make the rollover disagree with the
    number the owner reads beside it.

    **What the pair conserves is the PLAN.**  A `$100.00` envelope holding one
    `-$50.00` refund settles at `-$50.00` and rolls `$150.00` -- `$100.00`
    across the two periods, which is the one budget the owner declared.  That
    is the assertion below, and it is what makes the figure a decision rather
    than an artefact of an unguarded subtraction.

    **The COST is a known one** (same ruling): a refund of a purchase made in
    an EARLIER period lands here on the cash basis, so it enlarges an envelope
    whose own purchases it did not reverse.
    """

    def test_a_refunded_envelope_rolls_MORE_than_its_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """`$100.00` budget, one `-$50.00` refund: `$150.00` rolls, source `-$50.00`."""
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(
                source, seed_user, "-50.00", description="Amazon refund",
            )
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            assert settled_figure(source) == Decimal("-50.00"), (
                "the source records what its entries came to, refunds and all"
            )
            assert target.estimated_amount == Decimal("250.00"), (
                "100.00 of the target's own plan plus a 150.00 leftover"
            )
            assert target.is_override is True
            # THE CONSERVED FIGURE: what the two periods plan between them is
            # the two budgets the owner declared, unchanged by the refund
            # passing through.  A cap at the source budget would read 200.00
            # here and lose 50.00 of plan.
            assert (
                settled_figure(source) + target.estimated_amount
            ) == Decimal("200.00")

    def test_a_PARTLY_refunded_envelope_still_rolls_its_net_leftover(
        self, app, db, seed_user, seed_periods,
    ):
        """The control between this class and the overspend one.

        `$80.00` spent and `$30.00` refunded is `$50.00` of net spend against a
        `$100.00` budget, so `$50.00` rolls -- a figure BELOW the budget, which
        a cap could not be told apart from.  Without this case an
        implementation that simply ignored negative entries would satisfy the
        one above by arithmetic accident on a source holding only a refund.
        """
        with app.app_context():
            template = _create_envelope_template(
                seed_user, default_amount="100.00",
            )
            source = generate_row_of(template, seed_periods[0])
            target = generate_row_of(template, seed_periods[1])
            _add_entry(source, seed_user, "80.00")
            _add_entry(
                source, seed_user, "-30.00", description="Amazon refund",
            )
            db.session.commit()

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.refresh(source)
            db.session.refresh(target)
            assert settled_figure(source) == Decimal("50.00")
            assert target.estimated_amount == Decimal("150.00")
