"""Recurrence engine tests: resolve_conflicts(), and the regular-row control beside its
transfer-shadow guard.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import AmountSourceEnum, TxnTypeEnum
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._test_helpers import (
    record_paydays_across_a_hole,
    one_off_row_of,
    payback_row_of,
    make_cadence_rule,
    make_every_period_rule,
    resolved_amount,
    rhythm_of,
    state_template_price,
)
from app.services.amount_ownership import state_own_amount


class TestResolveConflicts:
    """DB integration tests for resolve_conflicts()."""

    def _make_template_with_rule(
        self, seed_user, cadence, category_key=None, **rule_kwargs,
    ):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"][category_key or "Car Payment"].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_resolve_keep_no_changes(self, app, db, seed_user, seed_periods):
        """action='keep' leaves overridden transaction unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # Resolve as 'keep'.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="keep", ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_resolve_update_hands_the_row_back_to_its_definition(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' clears the flags and writes a DECLARATION, no figure.

        Plan step balance:X-au-e, ruling **R-JD**.  "Use the template's amount"
        used to write the template's CURRENT ``default_amount`` onto the row;
        there is no figure to write now, and the act is to stop overriding so
        the definition's own series prices the row again.

        The assertion is on all THREE columns the act touches, because two of
        them are what makes it a hand-back rather than a re-price: the flag
        clears, the figure goes to ``None``, and ``amount_source_id`` names the
        template.  Asserting the resolved money alone would pass on a row that
        still owned the very same number.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            # The hand-back itself: no figure, and the relation that prices it.
            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            # And what it is now WORTH is the definition's stated price, not
            # the $999.99 the owner had typed.
            assert resolved_amount(txn) == Decimal("100.00")

    def test_a_row_that_names_NO_DEFINITION_is_skipped_not_declared(
        self, app, db, seed_user, seed_periods
    ):
        """A row cannot be handed back to a definition it does not name.

        Declaring such a row would write ``amount_source_id = template`` with
        no template to read -- the state ledger row **N-440** describes,
        which ``_rule_within_definition`` answers TEMPLATE for and
        ``_stated_amount`` then refuses, in a money path.  The row here is a
        CC payback: it names its credit source and no definition, and since
        the one-definition cutover (plan step ``balance:X-bi-7d-2``) a
        transfer shadow and a payback are the only rows with no definition --
        the orphan this case used to plant (``template_id`` nulled by the
        SET NULL key) is unstorable under ``ck_transactions_one_pricing_link``
        at ``= 1``.

        **Unreachable from the route** (the Apply POST's conflict set is
        built by selecting on ``template_id``), so this drives the service
        entry directly: the guard is defence in depth for a published
        function, and a guard nothing exercises is a guard nobody has seen
        work.
        """
        with app.app_context():
            envelope = one_off_row_of(
                seed_periods[0], name="Card envelope", amount="200.00",
                user_id=seed_user["user"].id, account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("999.99"),
                seed_periods[0].start_date,
            )
            payback.is_override = True
            db.session.flush()
            assert payback.template_id is None
            assert payback.amount_source_id is None

            recurrence_engine.resolve_conflicts(
                [payback.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()
            db.session.refresh(payback)

            # Untouched: still the owner's figure, still flagged, never
            # declared -- the one answer that stays true.
            assert payback.amount_source_id is None
            assert payback.estimated_amount == Decimal("999.99")
            assert payback.is_override is True

    def test_resolve_update_prices_a_past_row_from_ITS_date_not_todays(
        self, app, db, seed_user, seed_periods
    ):
        """A handed-back row takes the price in force on its OWN due date.

        **Finding N-244 as a regression test.**  The old "use" arm wrote the
        template's CURRENT ``default_amount`` onto whatever row it was given,
        so resolving an override on a row whose due date preceded a price rise
        back-dated today's figure onto it -- and cleared ``is_override``, the
        one flag that would have marked the row as touched.  A $100 history
        with one row resolved to $120 then read as THREE price changes where
        one had occurred.

        Nothing writes a figure now, so the row resolves through the series on
        its own due date and the rise that came after it cannot reach back.
        The test states the rise AFTER the row's due date and asserts the row
        is worth the OLD price; on the deleted arm it would be worth the new
        one.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            assert txn.due_date is not None
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # The series has to ANCHOR before the row, or the rise below
            # becomes the earliest version and ``amount_as_of`` holds IT flat
            # backwards -- which is the shape that made a first draft of this
            # test read $120.00 and call the app wrong.
            state_template_price(
                template, Decimal("100.00"),
                effective_on=txn.due_date - timedelta(days=1),
            )
            # The price rises the day AFTER this row's due date.
            state_template_price(
                template, Decimal("120.00"),
                effective_on=txn.due_date + timedelta(days=1),
            )
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.estimated_amount is None
            # The price in force on the row's OWN due date, never the newest.
            assert resolved_amount(txn) == Decimal("100.00")

    def test_cross_user_update_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """update with wrong user_id silently skips the transaction."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # Attempt resolve as second_user -- should be blocked.
            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                ctx=BalanceContext.build(second_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_cross_user_keep_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """keep with wrong user_id leaves transaction unchanged."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            # 'keep' with wrong user -- no-op by design (keep never modifies).
            recurrence_engine.resolve_conflicts(
                [txn.id], action="keep",
                ctx=BalanceContext.build(second_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is True
            assert txn.estimated_amount == Decimal("999.99")

    def test_same_user_update_succeeds(
        self, app, db, seed_user, seed_periods
    ):
        """update with correct user_id modifies the transaction."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            # Handed back to its definition rather than given a figure
            # (plan step balance:X-au-e, ruling R-JD): no stored amount,
            # and what it is worth is the DEFINITION's price, never the figure
            # a caller used to pass in.
            assert txn.estimated_amount is None
            assert resolved_amount(txn) == Decimal("100.00")

    def test_mixed_ownership_list(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Only owned transactions are modified in a mixed-ownership list."""
        with app.app_context():
            # Create template and transaction for user A.
            template_a = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created_a = recurrence_engine.generate_for_template(
                template_a, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_a.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            txn_a = created_a[0]
            txn_a.is_override = True
            state_own_amount(txn_a, Decimal("999.99"))

            # Create template and transaction for user B (second_user).
            # second_user needs their own periods and template.
            from app.services import pay_period_service
            periods_b = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=10, rhythm=rhythm_of(14),
            )
            template_b = self._make_template_with_rule(
                second_user, EVERY_PERIOD, category_key="Rent",
            )
            created_b = recurrence_engine.generate_for_template(
                template_b, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_b.user_id), {p.id for p in periods_b},
                ), second_user["scenario"].id,
            )
            db.session.flush()
            txn_b = created_b[0]
            txn_b.is_override = True
            state_own_amount(txn_b, Decimal("888.88"))
            db.session.flush()

            # Resolve as user A -- only txn_a should be modified.
            recurrence_engine.resolve_conflicts(
                [txn_a.id, txn_b.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn_a)
            db.session.refresh(txn_b)
            assert txn_a.is_override is False
            # Handed back to its definition rather than given a figure
            # (plan step balance:X-au-e, ruling R-JD): no stored amount,
            # and what it is worth is the DEFINITION's price, never the figure
            # a caller used to pass in.
            assert txn_a.estimated_amount is None
            assert resolved_amount(txn_a) == Decimal("100.00")
            assert txn_b.is_override is True
            assert txn_b.estimated_amount == Decimal("888.88")


class TestResolveConflictsHandsARegularRowBack:
    """The control C-20 / F-007's guard left behind: a regular row resolves.

    C-20 / F-007: ``resolve_conflicts`` refused to mutate a transfer shadow
    transaction (``transfer_id IS NOT NULL``), closing CLAUDE.md Transfer
    invariant 4 for this loop.

    The guard (``app/services/recurrence_engine/_conflicts.py``) was
    unreachable from plan step ``balance:X-bi-6-4d-2``, where a transaction
    naming a transfer was refused at the INSERT
    (``ck_transactions_names_no_transfer``), and plan step
    ``balance:X-bi-6-5`` deleted the guard and then the column it read.  The
    two cases that drove it were deleted at plan
    step ``balance:X-bi-6-4d-2`` (ruling **R-BAL320**):
    ``test_update_action_on_shadow_raises_validation_error`` planted the
    refused row, and ``test_cross_user_shadow_silently_skipped`` read two
    things.  Its nothing-written read is ``TestResolveConflicts``'s
    ``test_cross_user_update_blocked`` on a regular row, which fails first when
    the cross-user skip's ``continue`` is dropped (measured).  Its no-raise read
    graded the ORDER -- the cross-user skip before this guard -- which no state
    could show without a twin, and went unchecked until ``balance:X-bi-6-5``
    deleted the guard.  The deleted case read no access event; the warning is
    logged before the ``continue``, so dropping it leaves
    ``tests/test_services/test_service_log_events/test_the_period_and_recurrence_events.py``'s
    ``test_resolve_conflicts_cross_user_emits_access_event`` passing (measured).
    The control below stays.
    """

    def test_an_overridden_row_is_handed_back_to_its_definition(
        self, app, db, seed_user, seed_periods,
    ):
        """An overridden regular row's update clears its flag and prices it by its definition.

        With a regular transaction (the only kind since plan step
        ``balance:X-bi-6-4d-2``), ``resolve_conflicts`` keeps its
        semantics: clear the flags and hand the row back to its
        definition's price.  Companion to ``TestResolveConflicts``; it
        was the control beside the C-20 shadow guard, which plan step
        ``balance:X-bi-6-5`` deleted.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense")
                .one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                name="Regular Recurring",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            # The definition first, then the cadence onto it (plan step R-F6).
            rule = make_every_period_rule(db.session, template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            txn = created[0]
            txn.is_override = True
            state_own_amount(txn, Decimal("999.99"))
            db.session.flush()

            recurrence_engine.resolve_conflicts(
                [txn.id],
                action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.is_override is False
            assert txn.is_deleted is False
            # Handed back to its definition rather than given a figure
            # (plan step balance:X-au-e, ruling R-JD): no stored amount,
            # and what it is worth is the DEFINITION's price, never the figure
            # a caller used to pass in.
            assert txn.estimated_amount is None
            assert resolved_amount(txn) == Decimal("100.00")
