"""Carrying into a period that already holds the definition's rule-generated row: the carried row
lands beside it as an override sibling, and a later recurrence pass adds no third row, for a
discrete definition and for a transfer's.
"""

from decimal import Decimal

from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import carry_forward_service, recurrence_engine, transfer_recurrence
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import generate_row_of, generate_transfer_of, make_transfer_template
from tests._test_helpers import make_every_period_rule

from ._helpers import _create_savings, _create_template


class TestCarryForwardOverrideSibling:
    """Regression for the production bug: carry-forward into a target
    period that already holds a rule-generated row from the same
    template.  The undated partial unique index permits a carried
    is_override=True row to coexist with the rule-generated parent.

    The rule-generated rows ARE rule-generated: the engine writes them
    (plan step balance:X-cf), one per paycheck.  **The first case then
    clears both rows' ``occurs_on``**, because the index whose exemption it
    grades is the UNDATED one: a dated pair is storable by its two
    occurrences whatever the flag says (the occurrence index has carried
    no ``is_override`` clause since plan step X-au-h), so on dated rows the
    carry could write the period and the flag in two statements and
    nothing would collide.  The undated shape is a live one -- the backfill
    leaves NULL every row no occurrence claims -- and it is the one the
    production traceback came from.  Measured by the X-cf-3 review: the
    discrete UPDATE split into two statements passes on a dated pair and
    fails on this one with ``UniqueViolation`` on
    ``idx_transactions_template_scenario_undated``.
    """

    def test_carries_into_target_with_existing_rule_generated(
        self, app, db, seed_user, seed_periods
    ):
        """Override sibling coexists with rule-generated parent.

        Reproduces the production traceback:
            UniqueViolation on the generation index
            Key (template_id, pay_period_id, scenario_id)=(N, target, S)
            already exists.

        Under the undated index the carried row is permitted because
        is_override=True is excluded from the partial uniqueness
        predicate -- and only because the carry writes the period and the
        flag in ONE statement, which is what the pair being undated grades.
        """
        with app.app_context():
            template = _create_template(seed_user)

            # Rule-generated row in the source period (period 0) -- this
            # is what the user wants to carry forward.
            source = generate_row_of(template, seed_periods[0])
            # Rule-generated row already in the target period (period 1)
            # -- placed by the recurrence engine before the user clicked
            # "Carry Fwd."  is_override=False because it is the canonical
            # next-period instance.
            target_existing = generate_row_of(template, seed_periods[1])
            # The pre-R17 shape, laid on bare: each row claims its PAYCHECK
            # rather than an occurrence (see the class docstring).
            source.occurs_on = None
            target_existing.occurs_on = None
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # One carried regular transaction.
            assert count == 1

            # Both rows now live in the target period.
            db.session.refresh(source)
            db.session.refresh(target_existing)
            assert source.pay_period_id == seed_periods[1].id
            assert target_existing.pay_period_id == seed_periods[1].id

            # The carried row is the override sibling; the
            # pre-existing rule-generated row keeps is_override=False.
            assert source.is_override is True
            assert target_existing.is_override is False

            # Both rows retain the template link so the companion view
            # and recurrence engine can still see them.
            assert source.template_id == template.id
            assert target_existing.template_id == template.id

            # Sanity: target period now has exactly two non-deleted
            # transactions for this template/scenario.
            rows = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            assert len(rows) == 2
            override_flags = sorted(r.is_override for r in rows)
            assert override_flags == [False, True]

    def test_balance_calculator_sums_both_override_sibling_rows(
        self, app, db, seed_user, seed_periods
    ):
        """Balance calculator reflects the doubled obligation.

        After carry-forward, the target period's projected expense
        subtotal should include both the rule-generated and the
        override-sibling row.  Balance projections must drop by the
        full sum, not just one row's amount.  The pair is DATED here --
        two occurrences, the shape the engine writes today -- so this case
        grades the money and its sibling above grades the index.
        """
        with app.app_context():
            template = _create_template(seed_user, amount="250.00")
            generate_row_of(template, seed_periods[0])
            generate_row_of(template, seed_periods[1])

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Pull every non-deleted transaction in the target period: the
            # two rows the balance below must count.
            target_txns = (
                db.session.query(Transaction)
                .filter_by(
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).all()
            )
            account = seed_user["account"]
            # Plan step X-g4b: read what the app renders.  The fold loads the
            # account's own rows, so the period-window query above is no longer
            # threaded in -- it stays only as this test's own statement of what
            # it put there.  ``as_of`` is pinned inside period 0 so ruling R-G
            # does not clamp the still-Projected rows out of the window.
            assert len(target_txns) == 2
            balances = balance_at.cash_balance_map(
                account,
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )

            # Two $250 expenses pull the period balance down by $500
            # vs. the anchor's starting position.
            target_period_balance = balances[seed_periods[1].id]
            anchor_balance = balances[seed_periods[0].id]
            assert anchor_balance - target_period_balance == Decimal(
                "500.00"
            )

    def test_recurrence_engine_does_not_double_generate(
        self, app, db, seed_user, seed_periods
    ):
        """A subsequent recurrence-engine pass must not add a third row.

        The engine treats override siblings as a "this period is
        already handled" signal
        (``recurrence_engine._generate.generate_for_template``, whose second
        step skips a period that already holds an overridden row), so
        re-running generation after carry-forward must leave the count
        at two.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            # Authored through the write door (plan step R7c-b): the
            # two-axis columns are NOT NULL, so a rule naming only a pattern
            # cannot be stored.  It starts on the schedule's opening payday,
            # which is where an unbounded rule always resolved to.
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Recurring with rule",
                default_amount=Decimal("400.00"),
            )
            db.session.add(template)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, template)
            db.session.refresh(template)

            # Initial generation populates rule-generated rows for
            # periods 0 and 1.
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods[:2]},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Carry forward the period 0 row into period 1.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Pre-generation snapshot: target has rule-generated +
            # override sibling (= 2 rows).
            pre_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert pre_count == 2

            # Re-running the engine must NOT add a third row -- the
            # override sibling signals the period is handled.
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
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert post_count == 2


# ── Override-sibling Carry Forward Tests for Transfers ─────────────


class TestCarryForwardOverrideSiblingTransfers:
    """Mirror TestCarryForwardOverrideSibling for transfers, exercising
    the relaxed idx_transfers_template_scenario_undated index.

    The two transfers are the ENGINE's (:func:`generate_transfer_of`, plan
    step balance:X-ch), one per paycheck, and the first case then clears
    both transfers' ``occurs_on`` for the reason
    TestCarryForwardOverrideSibling's docstring gives: the index whose
    exemption it grades is the UNDATED one, and a dated pair is storable by
    its two occurrences whatever the flag says.
    """

    def test_carries_transfer_into_target_with_existing_rule_generated(
        self, app, db, seed_user, seed_periods
    ):
        """Override-sibling transfer coexists with rule-generated parent."""
        with app.app_context():
            savings = _create_savings(seed_user)
            template = make_transfer_template(db.session, seed_user, savings)

            # Rule-generated transfer in source period (period 0).
            source_xfer = generate_transfer_of(template, seed_periods[0])
            # Rule-generated transfer already in target period (period 1)
            # -- the recurrence engine has already produced this period's
            # instance.
            target_xfer = generate_transfer_of(template, seed_periods[1])
            # The pre-R17 shape, laid on bare: each transfer claims its
            # PAYCHECK rather than an occurrence (see the class docstring).
            source_xfer.occurs_on = None
            target_xfer.occurs_on = None
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 1

            db.session.refresh(source_xfer)
            db.session.refresh(target_xfer)
            assert source_xfer.pay_period_id == seed_periods[1].id
            assert target_xfer.pay_period_id == seed_periods[1].id
            assert source_xfer.is_override is True
            assert target_xfer.is_override is False
            assert source_xfer.transfer_template_id == template.id
            assert target_xfer.transfer_template_id == template.id

    def test_transfer_recurrence_does_not_double_generate(
        self, app, db, seed_user, seed_periods
    ):
        """transfer_recurrence skips a period that already has an
        override-sibling transfer.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            # Authored through the write door (plan step R7c-b): the
            # two-axis columns are NOT NULL, so a rule naming only a pattern
            # cannot be stored.  It starts on the schedule's opening payday,
            # which is where an unbounded rule always resolved to.
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                category_id=seed_user["categories"]["Rent"].id,
                name="Recurring Transfer with rule",
                default_amount=Decimal("300.00"),
            )
            db.session.add(template)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, template)
            db.session.refresh(template)

            # Initial generation: rule-generated transfers in periods 0
            # and 1.
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods[:2]},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Carry forward period 0 into period 1.  Period 1 now has a
            # rule-generated transfer + an override sibling.
            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            pre_count = (
                db.session.query(Transfer)
                .filter_by(
                    transfer_template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert pre_count == 2

            # Re-run transfer recurrence -- must not add a third row.
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods[:2]},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            post_count = (
                db.session.query(Transfer)
                .filter_by(
                    transfer_template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).count()
            )
            assert post_count == 2
