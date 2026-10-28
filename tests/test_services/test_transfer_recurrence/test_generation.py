"""Transfer recurrence tests: generate_for_template, the occurrence pairs it shares with the
transaction engine, the rows a generation creates, and the occurrence a transfer records.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import pay_period_write, transfer_recurrence
from app.services.recurrence_engine import resolve_generation_plan
from app.exceptions import RecurrenceConflict
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    record_paydays_across_a_hole,
    rhythm_of,
    create_account_of_type,
    last_covered_day,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from app.services.amount_ownership import state_own_amount
from tests._test_helpers import state_template_price
from tests._test_helpers import transfer_amount

from ._helpers import make_template_with_rule


class TestTransferGeneration:
    """Tests for generate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Delegate to the module-level helper (see `make_template_with_rule`)."""
        return make_template_with_rule(seed_user, cadence, **rule_kwargs)

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transfer in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for xfer in created:
                assert transfer_amount(xfer) == Decimal("100.00")
                assert xfer.name == "Test Transfer"
                # Every-period has no day_of_month, so the due date falls
                # back to the pay-period start (payday) -- the prior
                # behaviour is preserved for day-less patterns.
                assert xfer.due_date == xfer.pay_period.start_date

    def test_monthly_due_date_placed_on_day_of_month(
        self, app, db, seed_user, seed_periods
    ):
        """Monthly transfers are due on the rule's day_of_month, not payday.

        seed_periods is 10 biweekly periods from 2026-01-02, each spanning
        [start, start+13].  A Monthly rule with day_of_month=15 matches the
        period containing the 15th of each month:
            P0 [01-02..01-15] -> 2026-01-15
            P3 [02-13..02-26] -> 2026-02-15
            P5 [03-13..03-26] -> 2026-03-15
            P7 [04-10..04-23] -> 2026-04-15
            P9 [05-08..05-21] -> 2026-05-15
        so the transfers land on the true monthly due date (matching a loan
        card's monthly_due_date) instead of the pay-period start.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert sorted(x.due_date for x in created) == [
                date(2026, 1, 15),
                date(2026, 2, 15),
                date(2026, 3, 15),
                date(2026, 4, 15),
                date(2026, 5, 15),
            ]
            for xfer in created:
                assert xfer.due_date.day == 15

    def test_no_rule_returns_empty(self, app, db, seed_user, seed_periods):
        """Template with recurrence_rule=None returns empty list."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("500.00"),
            )

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="No Rule Transfer",
                default_amount=Decimal("50.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            db.session.refresh(template)

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_a_rule_less_template_returns_empty(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer template that does not repeat auto-generates nothing.

        The transfer-side twin of ``test_recurrence_engine.test_generation``'s
        ``test_a_rule_less_template_generates_nothing``;
        both engines share ``resolve_generation_plan``'s gate.  Named the
        ``Once`` PATTERN until plan step R2e-3 retired it -- and the single
        Transfer such a template stands for is created by the transfer ROUTE,
        not by this engine, which is exactly why the engine must return empty.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            # ONE statement, exactly as
            # ``_recurrence_form_helpers._clear_recurrence_rule`` does since
            # plan step R-F6: dis-associating the rule is what deletes it,
            # because the relationship carries ``delete-orphan`` and the rule
            # holds the owning FK.  It was three statements -- null both sides,
            # then delete the row -- while the FK sat on the template, and an
            # explicit delete after this one now reports
            # ``expected to delete 1 row(s); 0 were matched``, because the
            # dis-association already removed it.
            template.recurrence_rule = None
            db.session.flush()
            assert template.recurrence_rule is None

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            first_run = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            second_run = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_skips_overridden_and_deleted(self, app, db, seed_user, seed_periods):
        """Overridden and soft-deleted entries are not duplicated on re-generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Override one entry.
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            # Soft-delete another.
            created[1].is_deleted = True
            db.session.flush()

            second_run = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0


class TestTransferGenerationSharesTheOccurrencePairs:
    """The transfer engine's half of plan step R4b-2, driven rather than assumed.

    Both engines take their ``(occurrence, pay period)`` pairs from the one
    shared preamble (``recurrence_engine.resolve_generation_plan``) and their
    pre-write step from the one shared helper
    (``_recurrence_common.occurrences_to_write``), so the BEHAVIOUR
    cannot drift.  The COVERAGE could: a neutral review found the repeat
    refusal exercised only on the transaction side, which would let a
    transfer-specific regression -- the wrong FK column handed to the shared
    query, say -- ship green.  These drive the transfer path to the same two
    places.
    """

    def test_a_transfer_repeating_inside_one_paycheck_writes_each_occurrence(
        self, app, db, seed_user, seed_periods,
    ):
        """``idx_transfers_template_scenario_occurrence`` is the twin index.

        At a 90-day cadence a monthly transfer legitimately falls inside one
        paycheck three times.  While ``budget.transfers`` held one row per
        ``(template, period, scenario)`` those three could not be stored and
        the pass REFUSED (plan ledger row D19).  Plan step **R17** re-keyed the
        index onto ``(template, scenario, occurs_on)``, so three occurrences
        are three keys and all three transfers are written.  Each kept a
        shadow pair, read here, until plan step ``balance:X-bi-6-4d-2``: a
        transfer stores no leg row since migration 82f7 (ruling **R-BAL167**
        class 1).
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                rhythm=rhythm_of(90),
            )
            db.session.flush()
            template = TestTransferGeneration()._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )

            created = transfer_recurrence.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id),
                    {p.id for p in long_periods},
                ),
                seed_user["scenario"].id,
            )
            # The flush is the assertion: under the paycheck-keyed index these
            # three transfers were an IntegrityError.
            db.session.flush()

            paycheck = long_periods[0]
            expected = tuple(
                day
                for day in (
                    date(year, month, 15)
                    for year in range(
                        paycheck.start_date.year, last_covered_day(paycheck).year + 1,
                    )
                    for month in range(1, 13)
                )
                if paycheck.start_date <= day <= last_covered_day(paycheck)
            )
            assert len(expected) == 3, (
                "a 90-day paycheck must cover the 15th of three months, or this "
                "fixture no "
                "longer exercises the repeat"
            )
            in_paycheck = [
                row for row in created if row.pay_period_id == paycheck.id
            ]
            assert sorted(
                row.occurs_on for row in in_paycheck
            ) == list(expected)
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
                pay_period_id=paycheck.id,
            ).count() == 3

    def test_a_transfer_occurrence_in_an_absorbed_hole_is_written_too(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """The transfer engine takes the absorption identically (row **P27**).

        **This test asserted the opposite until plan step C2-b2.**  A day no
        pay period covered used to produce ``PlacementOutcome.SCHEDULE_GAP``,
        which ``report_schedule_gaps`` logged from the TRANSFER engine's own
        logger, and the occurrence generated nothing.  A calendar now derives
        each period's end from the next payday, so the preceding paycheck
        absorbs those days -- the hole is not a state a reader can see, and the
        report went with it.

        What the absorption leaves is an OVER-LONG paycheck holding the 15th
        twice,
        which the paycheck-keyed index could not hold, so the
        pass refuses and writes nothing.  Identical to the transaction engine's
        answer, which is the point: the two are deliberate parallels and this
        asserts they did not diverge across the cutover.  The transaction twin
        is ``test_recurrence_engine.test_legacy_schedule_hole.TestALegacyScheduleHole``.

        **The whole fixture is the real writer's**, and since plan step
        ``pay_calendar:C4-c`` nothing has to be undone afterwards: a batch
        opening 43 days past the horizon is a legal write, and the days between
        belong to the paycheck before them because ``end_date`` is derived
        rather than stored.  *This test re-opened the hole by hand on its last
        fixture line while that column existed, because C3-b's writer closed
        it on every write.*
        """
        with app.app_context():
            last_covered = calendar_for(seed_user["user"].id).horizon()
            later_start = last_covered + timedelta(days=43)
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=later_start,
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            gap_start = last_covered + timedelta(days=1)
            gap_end = later_start - timedelta(days=1)
            template = TestTransferGeneration()._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )

            with caplog.at_level(
                logging.WARNING, logger="app.services.transfer_recurrence",
            ):
                created = transfer_recurrence.generate_for_template(
                    template,
                    GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
                    seed_user["scenario"].id,
                )
                db.session.flush()

            absorbed = [
                date(year, month, 15)
                for year in range(gap_start.year, gap_end.year + 1)
                for month in range(1, 13)
                if gap_start <= date(year, month, 15) <= gap_end
            ]
            assert len(absorbed) == 1, "the fixture built no absorbed occurrence"
            # The absorbed date is ANSWERED by a transfer of its own, beside
            # the one the over-long paycheck already owed -- so nothing is
            # dropped and nothing is refused (plan step R17).
            assert absorbed[0] in {row.occurs_on for row in created}
            absorbing = [
                row for row in created if row.occurs_on == absorbed[0]
            ][0].pay_period_id
            both = [row for row in created if row.pay_period_id == absorbing]
            assert len(both) == 2
            assert len({row.occurs_on for row in both}) == 2
            # And nothing is logged -- the gap report went with plan step C2-b2.
            assert [
                record for record in caplog.records
                if getattr(record, "event", None)
                == "recurrence_occurrence_unplaced"
            ] == []


# ── Generated Transfer Tests ──────────────────────────────────────


class TestAGeneratedTransfersCategoryAndKeptRows:
    """A generated transfer's category, and the rows a maintain pass keeps.

    It graded each transfer's two shadow (twin) transactions until plan
    step ``balance:X-bi-6-4d-2``, and a transfer stores no leg row since
    migration 82f7 (ruling **R-BAL167**).
    """

    def _make_template(self, seed_user, cadence, category_id=None,
                       **rule_kwargs):
        """Helper: create savings account + rule + template with optional category."""

        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings Shadow",
            anchor_balance=Decimal("500.00"),
        )

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Shadow Test Transfer",
            default_amount=Decimal("150.00"),
            category_id=category_id,
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
            fires_on_day=rule_kwargs.get("day_of_month"),
            fires_in_month=rule_kwargs.get("month_of_year"),
        )
        db.session.refresh(template)
        return template, savings

    # ``test_generated_transfers_have_shadows`` was deleted at plan step
    # ``balance:X-bi-6-4d-2`` (ruling **R-BAL320**): without its twin read it
    # counted ``created`` against ``seed_periods``, which
    # ``TestTransferGeneration::test_every_period_generates_for_all`` counts on
    # the same act, failing first when generation skips a period (measured).

    def test_category_id_passed_from_template(
        self, app, db, seed_user, seed_periods
    ):
        """Template category_id flows to every generated transfer.

        It read the expense shadow's category beside the transfer's until plan
        step ``balance:X-bi-6-4d-2``: a transfer stores no leg row since
        migration 82f7, so the transfer's own column is the read (ruling
        **R-BAL167** class 1).
        """
        with app.app_context():
            rent_cat = seed_user["categories"]["Rent"]
            template, _ = self._make_template(
                seed_user, EVERY_PERIOD, category_id=rent_cat.id
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            for xfer in created:
                assert xfer.category_id == rent_cat.id

    def test_maintaining_keeps_the_rows_it_already_has(
        self, app, db, seed_user, seed_periods
    ):
        """An amount change reaches every transfer without replacing any.

        **RE-RULED at plan step R10-b.**  It asserted every shadow was GONE
        after a regeneration -- true only while the pass hard-deleted each
        parent and let ``transactions.transfer_id``'s CASCADE take the pair.
        The property worth pinning is the opposite one: the rows the owner
        already has follow the definition, keeping their own ids.

        It read each transfer's two shadow rows too, and was named
        ``test_maintaining_keeps_the_shadows_it_already_has``, until plan step
        ``balance:X-bi-6-4d-2``: a transfer stores no leg row since migration
        82f7 (ruling **R-BAL167** class 1).  What it reads that
        ``test_regeneration.py``'s ``test_regenerate_maintains_its_rows_in_place``
        does not: each row re-read after a COMMIT and an ``expire_all``, so the
        id and the figure are the database's rather than the session's.
        """
        with app.app_context():
            template, _ = self._make_template(seed_user, EVERY_PERIOD)
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            old_ids = [xfer.id for xfer in created]

            # Change amount and regenerate.
            template.default_amount = Decimal("300.00")
            state_template_price(template, Decimal("300.00"))
            db.session.flush()
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.commit()
            db.session.expire_all()

            for xid in old_ids:
                xfer = db.session.get(Transfer, xid)
                assert xfer is not None, (
                    f"Transfer {xid} was destroyed by a maintain pass."
                )
                assert transfer_amount(xfer) == Decimal("300.00")

    # ``test_no_orphaned_shadows_after_regeneration`` was deleted at plan step
    # ``balance:X-bi-6-4d-2`` (ruling **R-BAL320**): its one read was a loop over
    # the transactions naming a transfer, which the database refused to store
    # (``ck_transactions_names_no_transfer``, until plan step balance:X-bi-6-5
    # dropped the column), so it looped over none
    # (measured 0 after its regeneration).
    #
    # ``test_resolve_update_syncs_shadow_amounts`` was deleted there too: its
    # act and the transfer's figure are ``test_resolve_conflicts.py``'s
    # ``TestTransferResolveConflicts::test_resolve_update_clears_flags_and_applies_amount``,
    # which fails first when "update" leaves ``is_override`` set (measured).


class TestATransferRecordsItsOccurrence:
    """``Transfer.occurs_on`` -- plan step **R17**, ledger row **D57**.

    The transfer half of the same leaf.  Both engines write the occurrence from
    the same ``PlannedOccurrence`` through the same shared helpers.  A case here
    asserted that a transfer's two SHADOW transactions kept ``occurs_on`` NULL,
    so mirroring could not put a second writer on the column, until plan step
    ``balance:X-bi-6-4d-2``: a transfer stores no leg row since migration 82f7,
    so the transfer is the column's one home (ruling **R-BAL320**, below).
    """

    def _make_template_with_rule(self, seed_user, cadence):
        """Helper: savings account + transfer template with an authored rule."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings",
            anchor_balance=Decimal("500.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Occurrence Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        make_cadence_rule(template, cadence, interval_n=1)
        db.session.refresh(template)
        return template

    def test_generate_writes_the_occurrence_on_the_transfer(
        self, app, db, seed_user, seed_periods
    ):
        """Each generated transfer carries the date its cadence names."""
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            plan = resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )
            created = transfer_recurrence.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            assert created, "the control needs the engine to create something"
            assert (
                sorted(row.occurs_on for row in created)
                == sorted(p.occurrence for p in plan.placements)
            )

    # ``test_the_shadows_carry_no_occurrence`` was deleted at plan step
    # ``balance:X-bi-6-4d-2`` (ruling **R-BAL320**): the twins it read were rows
    # the database refused to store (``ck_transactions_names_no_transfer``,
    # ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``'s
    # ``test_the_database_refuses_a_twin``) until plan step balance:X-bi-6-5
    # dropped the column, so it read none (measured 0), and
    # the column's one writer is graded by the case above.

    def test_a_maintain_pass_retains_an_undated_transfer_and_deletes_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """Finding **REC-516**, the TRANSFER half -- the destructive one.

        The shared classifier
        (``_recurrence_common.classify_maintain_work``, both engines since
        plan step R10-b) read *answers no occurrence* as *the rule dropped
        this occurrence* and routed an undated row to ``work.retire``.  Here
        that arm is ``transfer_service.delete_transfer(..., soft=False)``,
        which hard-deletes the transfer.

        **This engine is where the finding bites hardest.**  Production held
        54 undated transfers against 6 undated transactions, the developer's
        dev database 175 against 622, and the app's one live writer of undated
        rows is a transfer one
        (``routes/transfers/_instances._materialize_one_time_transfer``, which
        passes no ``occurs_on``).  The defect was reproduced on this engine
        directly: ``"event": "transfer_recurrence_regenerated",
        "deleted_count": 1, "created_count": 1``.

        It counted the retained transfer's two shadow rows too (Transfer
        Invariant 1) until plan step ``balance:X-bi-6-4d-2``: a transfer
        stores no leg row since migration 82f7, so the parent's own re-read is
        the control (ruling **R-BAL167** class 1).
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            )
            created = transfer_recurrence.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) >= 2

            victim = created[0]
            victim_id = victim.id
            victim_period = victim.pay_period_id
            victim.occurs_on = None
            db.session.flush()
            assert victim.is_override is False
            assert victim.is_deleted is False
            assert victim.status.is_immutable is False
            assert victim.notes is None

            with pytest.raises(RecurrenceConflict) as conflict:
                transfer_recurrence.regenerate_for_template(
                    template, schedule, seed_user["scenario"].id,
                )
            db.session.flush()

            assert victim_id in conflict.value.retained
            assert db.session.query(Transfer).filter_by(
                id=victim_id,
            ).one_or_none() is not None, (
                f"REC-516: the maintain pass HARD DELETED undated transfer "
                f"{victim_id} through delete_transfer(soft=False)"
            )
            # The retained transfer still HOLDS its paycheck, so nothing was
            # written into that period a second time.
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == len(created)
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id, pay_period_id=victim_period,
            ).count() == 1
