"""Recurrence engine tests: regenerate_for_template()'s refusals and conflicts, and the rows it
maintains in place beside an override.
"""

import pytest
from decimal import Decimal

from app.models.transaction import Transaction
from app.services import recurrence_engine
from app.exceptions import RecurrenceConflict
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._test_helpers import resolved_amount, cover_bare_settled_row, state_template_price
from app.services.settle_day import record_settle_day
from app.services.amount_ownership import state_own_amount

from ._helpers import _TestRegenerateForTemplateMembers


class TestRegenerateForTemplate(_TestRegenerateForTemplateMembers):
    """Refusals, conflicts, in-place maintenance; members on _TestRegenerateForTemplateMembers."""

    def test_a_cross_user_scenario_is_refused_and_retires_nothing(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """The ownership guard is load-bearing, and now it is tested.

        Removing the early return at the top of ``regenerate_for_template``
        passed the full 9,477-test suite -- an adversarial review's measurement
        of plan step R10-a.  The guard matters MORE under this step than
        before, by its own docstring's argument: it is what makes the plan's
        ``None`` mean "the rule was cleared" and nothing else.  Without it a
        foreign-scenario call resolves no plan, which this code would read as
        "retire everything".
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
            row_ids = sorted(txn.id for txn in created)
            assert row_ids

            # User B's scenario, with user A's template.
            result = recurrence_engine.regenerate_for_template(
                template, schedule, second_user["scenario"].id,
            )
            db.session.flush()

            assert result == []
            surviving = sorted(
                txn.id for txn in db.session.query(Transaction)
                .filter(Transaction.template_id == template.id).all()
            )
            assert surviving == row_ids, (
                "a cross-user call retired this template's rows"
            )

    def test_no_second_row_is_created_beside_an_overridden_or_deleted_one(
        self, app, db, seed_user, seed_periods
    ):
        """The claim predicate is the maintain path's whole creation rule.

        ``OccurrenceClaims`` counts EVERY row's claim, whatever state it is in,
        and this had no engine-level test: mutating it to ignore soft-deleted
        rows left this file green.  The database will NOT backstop a mistake
        here -- both generation indexes are PARTIAL
        (``WHERE is_deleted = FALSE AND is_override = FALSE``), so a duplicate
        beside a soft-deleted or overridden row inserts silently and the owner
        sees the same bill twice.
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

            softdeleted, overridden = created[0], created[1]
            softdeleted.is_deleted = True
            overridden.is_override = True
            db.session.flush()
            period_ids = (softdeleted.pay_period_id, overridden.pay_period_id)

            with pytest.raises(RecurrenceConflict):
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            for period_id in period_ids:
                rows = (
                    db.session.query(Transaction)
                    .filter(
                        Transaction.template_id == template.id,
                        Transaction.pay_period_id == period_id,
                        Transaction.scenario_id == seed_user["scenario"].id,
                    )
                    .all()
                )
                assert len(rows) == 1, (
                    f"period {period_id} holds {len(rows)} rows -- a second "
                    "was created beside the owner's"
                )

    def test_a_settlement_record_alone_retains_an_orphaned_row(
        self, app, db, seed_user, seed_periods
    ):
        """The third arm of the records predicate, which was untested.

        Neutering it passed the full suite.

        **The arm reads the settlement RECORD since plan step X-au-c3**, where
        it read ``actual_amount is not None``.  That column meant "a human typed
        a figure" only because it carried both the settled figure and the fact
        that a human had supplied it; a row that has SETTLED records what moved
        whoever said so, and that is the fact worth holding a row for.  The
        fixture settles the row rather than writing a figure onto a projected
        one, which the record's CHECKs now refuse.
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

            priced, blank = created[0], created[1]
            priced_id, blank_id = priced.id, blank.id
            # The RETAINED state, which is what a REVERT leaves behind: the
            # record kept, the ASSERTION released (``settled_on`` cleared, and
            # ``reconciled_by_id`` with it).
            # ``status_seam.apply_status_change`` writes exactly this shape on
            # the way OUT of the settled band.
            #
            # **That is a LIVE path, not a structural backstop.**  A settled
            # status is immutable to this sweep, so before plan step X-au-c3 --
            # when leaving the band destroyed the record along with the
            # assertion -- no row this pass could see ever carried one and the
            # arm was unreachable.  Retention put a real row in front of it: the
            # owner settled this row, read a figure off a statement, and set it
            # back to Projected in order to edit it.  Retiring it now would
            # delete that figure, which is what retention exists to keep.
            #
            # The record is the covering movement, kept un-dated across a
            # revert (plan step X-bi-3e-2; the row's one home for it since
            # X-bi-4b-2): the seam's own writer lays it, and the row's day
            # pair stays released.
            record_settle_day(priced, None)
            cover_bare_settled_row(
                db.session, priced, Decimal("41.10"), submitted=Decimal("41.10"),
            )
            db.session.flush()

            template.recurrence_rule = None
            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [priced_id]
            assert db.session.get(Transaction, priced_id) is not None
            assert db.session.get(Transaction, blank_id) is None

    def test_the_three_conflict_lists_are_reported_together(
        self, app, db, seed_user, seed_periods
    ):
        """One pass can retain, override AND soft-delete at once.

        Each retention test asserts its own list and nothing about the other
        two, so nothing pinned the shape the route actually branches on -- and
        that is the shape that decides whether the chooser renders beside the
        retained notice.  It is the combination the H1 defect lived in.
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

            spent_on, overridden, softdeleted = created[0], created[1], created[2]
            spent_id = spent_on.id
            overridden_id, softdeleted_id = overridden.id, softdeleted.id
            self._record_purchase(spent_on, seed_user)
            overridden.is_override = True
            softdeleted.is_deleted = True
            db.session.flush()

            template.recurrence_rule = None
            template.recurrence_rule = None
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.retained == [spent_id]
            assert raised.value.overridden == [overridden_id]
            assert raised.value.deleted == [softdeleted_id]

    def test_a_rule_row_survives_beside_a_carried_forward_override(
        self, app, db, seed_user, seed_periods
    ):
        """A period holding BOTH rows keeps both, and that is a change.

        **The one case where maintaining is not equivalent to the old
        delete-and-recreate**, found by adversarial review: the old sweep
        deleted the rule's own row, then the skip predicate saw the override
        sibling and declined to recreate it, so an unrelated edit silently
        removed a period's own bill.  Carry-forward produces exactly this shape
        -- ``carry_forward_service`` moves an unpaid row into the target period
        with ``is_override = True`` so it sits BESIDE the rule-generated one.
        (What makes the pair STORABLE differs by shape: two dated rows answer
        two occurrences, and the occurrence index has carried no
        ``is_override`` clause since plan step X-au-h; only the undated index
        still exempts the flag.  What the flag does here is keep the maintain
        pass off the row.)

        Measured at 0 live instances on a production clone, so nothing moved
        when this shipped; the new answer is also the correct one, since the
        period genuinely owes both. Pinned here because nothing else pins it.
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

            # The shape carry-forward leaves behind: the PREVIOUS paycheck's
            # row carried into this one as an override sibling, beside the
            # rule's own row.  Both rows are the engine's (plan step
            # balance:X-cf); the carry is the move door's two acts, and the
            # sibling's own figure is the owner's re-price, so the maintain
            # pass's new price cannot reach it.
            rule_row = created[1]
            rule_row_id = rule_row.id
            carried = created[0]
            carried.pay_period_id = rule_row.pay_period_id
            carried.is_override = True
            state_own_amount(carried, Decimal("55.00"))
            db.session.flush()
            carried_id = carried.id

            # Through the app's one write door: since plan step
            # balance:X-au-e the SERIES is what prices the rows, so moving
            # the scalar alone states a price no row can read.
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as raised:
                recurrence_engine.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert raised.value.overridden == [carried_id]
            survivor = db.session.get(Transaction, rule_row_id)
            assert survivor is not None, (
                "the rule's own row was destroyed beside its override sibling"
            )
            assert resolved_amount(survivor) == Decimal("200.00")
            assert db.session.get(
                Transaction, carried_id,
            ).estimated_amount == Decimal("55.00")

    def test_regenerate_maintains_unmodified_rows_in_place(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with a changed amount re-prices the SAME rows.

        **RE-RULED at plan step R10-a** (ruling **R-R19**, finding **N-292**).
        It asserted the opposite -- ``len(new_created) == 10`` and ``txn.id not
        in old_ids``, that is, that a regeneration DESTROYS every row and
        builds replacements.  That contract is what took the purchases recorded
        against a part-spent envelope with it, since ``transaction_entries``
        CASCADE from their parent, so the developer ruled it out: a row the
        rule still names is now maintained, keeping its id and everything
        hanging off it.

        The assertion is STRONGER than the one it replaces, not weaker.  The
        old one could not tell a re-priced row from a rebuilt one -- both give
        ten rows at $200.00 -- and this pins the identity of all ten.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            # Generate initial entries.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            old_ids = sorted(txn.id for txn in created)
            assert len(old_ids) == 10

            # Change the template amount.
            # Through the app's one write door: since plan step
            # balance:X-au-e the SERIES is what prices the rows, so moving
            # the scalar alone states a price no row can read.
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            # Regenerate.  Every period the rule names already holds the rule's
            # own row, so there is nothing to CREATE and the return is empty.
            new_created = recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert new_created == []

            surviving = (
                db.session.query(Transaction)
                .filter(Transaction.template_id == template.id)
                .all()
            )
            assert sorted(txn.id for txn in surviving) == old_ids
            for txn in surviving:
                assert resolved_amount(txn) == Decimal("200.00")

    def test_regenerate_raises_conflict_for_deleted_entries(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with soft-deleted entry raises RecurrenceConflict."""
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

            # Soft-delete one entry.
            deleted_id = created[0].id
            created[0].is_deleted = True
            db.session.flush()

            # Regenerate -- should raise with deleted list.
            with pytest.raises(RecurrenceConflict) as exc_info:
                recurrence_engine.regenerate_for_template(
                    template, GenerationSchedule.for_period_ids(
                        BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                    ), seed_user["scenario"].id,
                )

            assert deleted_id in exc_info.value.deleted
