"""Recurrence engine tests: which rows regenerate_for_template() keeps, retires and carries, and
the ledger it reconciles as it maintains them.
"""

import pytest
from decimal import Decimal

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.journal_entry import JournalEntry, Posting
from app.models.ref import AccountType, TransactionType
from app.services import recurrence_engine
from app.exceptions import RecurrenceConflict
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, MONTHLY_FIRST
from tests._test_helpers import (
    make_cadence_rule,
    open_books_before_the_first_assertion,
    resolved_amount,
    state_template_price,
)

from ._helpers import _TestRegenerateForTemplateMembers


class TestRegenerateForTemplate(_TestRegenerateForTemplateMembers):
    """Keeps, retires, carries, reconciles; members on _TestRegenerateForTemplateMembers."""

    def test_regeneration_keeps_the_purchases_recorded_against_a_row(
        self, app, db, seed_user, seed_periods
    ):
        """A template edit must not destroy what the owner recorded buying.

        **Finding N-292, the defect this step closes** (ruling **R-R19**).
        Measured on a production clone before the fix: editing template 38
        ("Groceries") destroyed txn 2282 and CASCADE took its 3 purchase
        records worth ``$499.82`` with it, with no prompt and an
        ``overridden_conflict_count`` of 0.

        Three assertions, and each fails against the old delete-and-recreate:
        the purchase still exists, it still belongs to the SAME row (the old
        code gave the period a new row with a new id), and the row was re-priced
        rather than rebuilt.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on = created[0]
            txn_id = spent_on.id
            entry_id = self._record_purchase(spent_on, seed_user).id

            # Through the app's one write door: since plan step
            # balance:X-au-e the SERIES is what prices the rows, so moving
            # the scalar alone states a price no row can read.
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            entry = db.session.get(TransactionEntry, entry_id)
            assert entry is not None, "the purchase was destroyed"
            assert entry.transaction_id == txn_id
            assert entry.amount == Decimal("12.79")

            txn = db.session.get(Transaction, txn_id)
            assert txn is not None, "the row the purchase hangs off was destroyed"
            assert resolved_amount(txn) == Decimal("200.00")

    def test_a_row_the_rule_STOPPED_naming_is_retired_though_the_rule_remains(
        self, app, db, seed_user, seed_periods
    ):
        """The RETIRE branch fires for a LIVE rule, not only a cleared one.

        **The firing control for the sweep's domain** (pay-calendar plan step
        C2-f3c).  A maintain pass decides what to do with each row it can see,
        and it can only RETIRE a row whose period the rule no longer names --
        so the set of rows it looks at has to be strictly WIDER than the set of
        periods the rule names.  Narrowing the row select to the plan's own
        periods would make the branch unreachable while every other assertion
        in this class still passed, which is why the domain is the pass's WRITE
        WINDOW (``_recurrence_common.rows_this_pass_may_maintain``) and not the
        plan.

        The neighbouring case covers the CLEARED rule, where the plan is
        ``None`` and every row is an orphan.  This one keeps a live rule and
        narrows what it names -- an every-paycheck definition edited down to
        the first paycheck of each month -- which is the shape a template edit
        actually produces and the one a plan-shaped domain would silently stop
        handling.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(template.user_id),
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            assert len(created) == 10, "the premise: a row in every paycheck"
            by_period = {row.pay_period_id: row.id for row in created}

            # The definition narrows: the first paycheck of each month only.
            # The old cadence is DELETED before the new one is authored -- a
            # rule carries its definition's FK since recurrence plan step
            # **R-F6**, and one partial unique index per kind holds a template
            # to one rule, so replacing means replacing.
            db.session.delete(template.recurrence_rule)
            db.session.flush()
            make_cadence_rule(template, MONTHLY_FIRST)
            db.session.refresh(template)

            recurrence_engine.regenerate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            named = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, scenario_id, None,
                    block_message="test",
                ).placements
            }
            # The premise, asserted rather than assumed: the edit really did
            # drop paychecks, so there is something for the branch to retire.
            assert named, "the narrowed rule must still name some paychecks"
            assert set(by_period) - named, (
                "the narrowed rule must stop naming some paycheck, or this "
                "case grades an empty set"
            )

            # **Every original row is retired, and that is plan step R17's
            # semantics rather than a regression.**  The old cadence answered
            # each paycheck's own PAYDAY (the ``PERIOD`` unit's occurrence);
            # the narrowed one answers the 1st of each month.  Those are
            # different occurrences, so no original row is still NAMED and the
            # pass replaces rather than maintains -- the developer's ruling of
            # 2026-08-28, which chose replace-and-ask over re-pointing a row at
            # whatever occurrence happened to be left in its paycheck.
            for period_id, row_id in by_period.items():
                assert db.session.get(Transaction, row_id) is None, (
                    f"period {period_id}'s original row answers an occurrence "
                    f"the narrowed rule no longer names, so it must retire"
                )

            # **The firing control this case exists for, unchanged**: a row in
            # a paycheck the rule NO LONGER names is gone.  That is only
            # reachable because the sweep's domain is the pass's WRITE WINDOW
            # (``_recurrence_common.rows_this_pass_may_maintain``) -- a
            # plan-shaped domain would never have seen those rows, and every
            # other assertion in this class would still have passed.
            surviving = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id, is_deleted=False)
                .all()
            )
            assert surviving, "the narrowed rule must still write its own rows"
            assert {row.pay_period_id for row in surviving} <= named, (
                "a row survived in a paycheck the rule no longer names -- the "
                "sweep never saw it"
            )

    def test_a_row_the_rule_no_longer_names_is_retained_when_it_holds_a_purchase(
        self, app, db, seed_user, seed_periods
    ):
        """Clearing a recurrence must not take a part-spent envelope with it.

        The second shape of **N-292**: with no rule left, every existing row is
        an orphan, and the old sweep deleted each one.  A row carrying the
        owner's purchases is now RETAINED instead -- untouched, and named in
        the raise so the route can say so.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on, untouched = created[0], created[1]
            spent_id, empty_id = spent_on.id, untouched.id
            entry_id = self._record_purchase(spent_on, seed_user).id

            # "Does not repeat" -- the rule is gone, so the rule names nothing.
            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [spent_id]
            assert db.session.get(TransactionEntry, entry_id) is not None
            assert db.session.get(Transaction, spent_id) is not None
            # The CONTROL: an orphan with nothing on it is still removed, so
            # the retention above is the purchase's doing and not a blanket
            # refusal to delete.
            assert db.session.get(Transaction, empty_id) is None

    def test_an_account_move_carries_a_row_holding_a_purchase_and_leaves_the_purchase(
        self, app, db, seed_user, seed_periods
    ):
        """A moved row LEAVES its purchases where their money moved (ruling **R-CC36**).

        RE-EXPRESSED at plan step ``credit_card:CC-5-2`` under CLAUDE.md rule
        5 with the developer's confirmation (2026-09-20, the option picked:
        *"Retire it and re-point on re-settle"*).  Through ``CC-5-1`` this
        case pinned the opposite -- the row was RETAINED and reported --
        because ``fk_transaction_entries_parent_account`` dragged every
        purchase onto the new account and invalidated the account-scoped
        clearing link each carried.  That key is dropped (**R-BAL76**), every
        reader asks the purchase's own account (design 3.2), and a purchase's
        link is scoped by the PURCHASE's account, which its row moving never
        touches.  So the row follows its definition, no conflict is raised
        for it, and the purchase's account is exactly what it was.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on, untouched = created[0], created[1]
            spent_id, empty_id = spent_on.id, untouched.id
            original_account_id = spent_on.account_id
            purchase = self._record_purchase(spent_on, seed_user)
            purchase_id = purchase.id

            moved_to = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Second Checking",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(moved_to)
            db.session.flush()
            # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md
            # rule 5, developer-confirmed 2026-09-22 ("Yes: open books before
            # rows"): the new account's books open before the schedule, as
            # the seeded account's do.  ``create_account`` opened them TODAY,
            # after these rows, and since ruling R-PC85 a definition's
            # occurrences start above its books -- so the maintain pass would
            # retire or conflict the moved rows before this test reached
            # R-CC36, and the edit door now refuses that move (R-PC90).
            open_books_before_the_first_assertion(db.session, moved_to)
            template.account_id = moved_to.id
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            db.session.expire_all()

            # The row moved; its purchase did not.
            assert db.session.get(Transaction, spent_id).account_id == moved_to.id
            assert db.session.get(TransactionEntry, purchase_id).account_id == (
                original_account_id
            )
            # The row still holds the purchase -- nothing was dropped or
            # re-created on the way.
            assert [e.id for e in db.session.get(Transaction, spent_id).purchases] == [
                purchase_id,
            ]
            # And a row with no purchases follows exactly as it always did.
            assert db.session.get(Transaction, empty_id).account_id == moved_to.id

    def test_a_note_alone_retains_an_orphaned_row(
        self, app, db, seed_user, seed_periods
    ):
        """Purchases are not the only thing a template cannot reconstruct.

        ``notes`` is free text the owner typed and no writer derives, so the
        old delete-and-recreate dropped it exactly as it dropped purchases.
        Measured at 0 rows on the production clone -- luck, not safety, which
        is why the predicate covers it rather than waiting for the first note.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            annotated, blank = created[0], created[1]
            annotated_id, blank_id = annotated.id, blank.id
            annotated.notes = "split with Kayla"
            # The CONTROL for the whitespace arm: a note that is only spaces is
            # not a record worth blocking an edit over.
            blank.notes = "   "
            db.session.flush()

            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [annotated_id]
            assert db.session.get(Transaction, annotated_id) is not None
            assert db.session.get(Transaction, blank_id) is None

    def test_every_derived_column_is_maintained_not_just_the_amount(
        self, app, db, seed_user, seed_periods
    ):
        """A maintained row takes ALL SIX derived columns from its template.

        **The keystone claim of plan step R10-a**, and it was untested until an
        adversarial review measured it: skipping ``name``, ``category_id``,
        ``transaction_type_id`` and ``due_date`` in the update loop -- four of
        the six ``DerivedRowFields`` at once -- passed the entire 9,477-test
        suite.  Only ``account_id`` and ``estimated_amount`` were pinned.

        ``transaction_type_id`` is the one that moves money: it carries the
        row's SIGN, so an Expense-to-Income template edit that failed to reach
        a maintained row would leave that row subtracting what it should add.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            new_category = seed_user["categories"]["Groceries"]
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()
            assert created, "no rows to maintain -- the fixture proves nothing"
            row_ids = [txn.id for txn in created]
            assert all(txn.due_date.day == 5 for txn in created)

            template.name = "Renamed Bill"
            template.category_id = new_category.id
            template.transaction_type_id = income_type.id
            # The DUE DATE is planted STALE and the rule is left alone, so the
            # occurrence -- what names a row since plan step **R17** -- does
            # not move and every row is maintained rather than retired.  Until
            # plan step R5-a a rule EDIT moved it: ``due_day_of_month`` was the
            # knob the walk never read and ``compute_due_date`` preferred.  That
            # column is gone (ruling R-R96) and a rule now dates a row from the
            # occurrence it answers, so no edit to a day-naming rule's DAY can
            # move a maintained row's date without moving its occurrence.  (A
            # FUNDING switch can, and moves it off the row's own paycheck --
            # a behaviour R5-a keeps rather than introduces, reported with it.)
            # A stored date that disagrees with the derivation is what the
            # update loop must still overwrite, which is the claim this test
            # pins.
            for txn in created:
                txn.due_date = txn.due_date.replace(day=7)
            db.session.flush()

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            maintained = (
                db.session.query(Transaction)
                .filter(Transaction.id.in_(row_ids))
                .all()
            )
            assert len(maintained) == len(row_ids), "a row was destroyed"
            for txn in maintained:
                assert txn.name == "Renamed Bill"
                assert txn.category_id == new_category.id
                assert txn.transaction_type_id == income_type.id
                assert txn.due_date.day == 5

    def test_maintaining_a_row_reconciles_the_ledger_its_purchase_posted(
        self, app, db, seed_user, seed_periods
    ):
        """A purchase's counter leg follows its parent's category.

        **The ledger half of plan step R10-a, which had NO coverage**: an
        adversarial review replaced the whole ``sync_transaction_postings``
        loop with ``pass`` and the full suite still passed.  The cause was the
        fixture -- every test purchase was built without a ``settled_on``, so
        ``purchase_posts()`` returned False and no test in the suite ever
        booked a ledger leg at all.

        A purchase carrying a recorded bank posting day IS a cash movement
        (ruling R-FM), debited against its envelope's category.  Move the
        template's category and the counter leg must move with it, while the
        cash leg stays where the money actually left.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            spent_on = created[0]
            self._record_purchase(
                spent_on, seed_user, amount="120.00",
                settled_on=spent_on.pay_period.start_date,
            )
            db.session.flush()

            def legs():
                """Every posting on this row's family, by ledger account."""
                rows = (
                    db.session.query(Posting.ledger_account_id, Posting.amount)
                    .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                    .filter(JournalEntry.transaction_entry_id.isnot(None))
                    .all()
                )
                totals = {}
                for ledger_account_id, amount in rows:
                    totals[ledger_account_id] = (
                        totals.get(ledger_account_id, Decimal("0.00")) + amount
                    )
                return {k: v for k, v in totals.items() if v != Decimal("0.00")}

            before = legs()
            assert before, (
                "the purchase booked no ledger legs -- the fixture cannot see "
                "the behaviour this test exists for"
            )
            assert sum(before.values()) == Decimal("0.00")

            template.category_id = seed_user["categories"]["Groceries"].id
            db.session.flush()
            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()

            after = legs()
            # Still balanced, and the counter leg actually MOVED: the set of
            # ledger accounts holding a non-zero net is different, while the
            # cash side is untouched.
            assert sum(after.values()) == Decimal("0.00")
            assert after != before, (
                "the category move did not reach the purchase's counter leg"
            )
            moved_off = set(before) - set(after)
            moved_onto = set(after) - set(before)
            assert moved_off and moved_onto, (
                f"expected the counter leg to move accounts, got "
                f"{before} -> {after}"
            )

    def test_propagating_to_a_rule_less_definition_reconciles_the_ledger_too(
        self, app, db, seed_user, seed_periods
    ):
        """The transaction twin of ``propagate_to_unruled_template``, ledger half.

        Plan step balance:X-bi-7a.  ``propagate_to_unruled_definition`` is
        what a rule-less definition's edit reaches its rows by, and it takes
        the same two acts ``_apply_maintain_work`` takes after its splat: a
        flush, then ``sync_transaction_postings`` on every updated row.  The
        case above is why that loop is graded rather than trusted -- deleting
        it once passed the whole suite.  Here the definition's cadence is
        CLEARED after its row books a purchase leg, its category moves, and
        the purchase's counter leg has to move with it.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {seed_periods[0].id},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()
            (spent_on,) = created
            self._record_purchase(
                spent_on, seed_user, amount="120.00",
                settled_on=spent_on.pay_period.start_date,
            )
            template.recurrence_rule = None
            db.session.flush()
            assert spent_on.recurs is False

            def legs():
                """Every posting on this row's family, by ledger account."""
                rows = (
                    db.session.query(Posting.ledger_account_id, Posting.amount)
                    .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                    .filter(JournalEntry.transaction_entry_id.isnot(None))
                    .all()
                )
                totals = {}
                for ledger_account_id, amount in rows:
                    totals[ledger_account_id] = (
                        totals.get(ledger_account_id, Decimal("0.00")) + amount
                    )
                return {k: v for k, v in totals.items() if v != Decimal("0.00")}

            before = legs()
            assert before and sum(before.values()) == Decimal("0.00")

            template.category_id = seed_user["categories"]["Groceries"].id
            db.session.flush()
            retained = recurrence_engine.propagate_to_unruled_definition(
                template, [spent_on],
            )
            db.session.flush()

            assert retained == []
            assert spent_on.category_id == template.category_id
            after = legs()
            assert sum(after.values()) == Decimal("0.00")
            assert after != before, (
                "the category move did not reach the purchase's counter leg"
            )
            assert (set(before) - set(after)) and (set(after) - set(before))

    def test_propagating_carries_a_row_holding_records_when_its_account_moves(
        self, app, db, seed_user, seed_periods
    ):
        """The same decision the regular pass makes, on the same two functions.

        RE-EXPRESSED at plan step ``credit_card:CC-5-2`` under CLAUDE.md rule
        5 with the developer's confirmation (2026-09-20, ruling **R-CC36**).
        It pinned the RETENTION through ``CC-5-1``: moving the row then
        dragged the purchase's leg onto an account the money never left.  A
        purchase names its own account now, so a row holding one follows its
        rule-less definition's account move exactly as an empty row does, the
        purchase stays where its money moved, and the route is handed nothing
        to report.
        """
        with app.app_context():
            template = self._make_envelope_template(seed_user)
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id),
                    {seed_periods[0].id, seed_periods[1].id},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()
            holding, empty = sorted(created, key=lambda row: row.pay_period_id)
            self._record_purchase(holding, seed_user, amount="12.79")
            template.recurrence_rule = None
            db.session.flush()
            old_account = template.account_id
            checking = db.session.query(AccountType).filter_by(name="Checking").one()
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking.id,
                    name="Other Checking",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(other)
            db.session.flush()

            template.account_id = other.id
            db.session.flush()
            purchase_id = holding.purchases[0].id
            retained = recurrence_engine.propagate_to_unruled_definition(
                template, [holding, empty],
            )
            db.session.flush()

            assert retained == []
            assert holding.account_id == other.id
            assert empty.account_id == other.id
            assert db.session.get(TransactionEntry, purchase_id).account_id == (
                old_account
            )
