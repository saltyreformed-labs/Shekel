"""Transfer recurrence tests: what a regeneration does to the transfers it already generated."""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app import ref_cache
from app.enums import StatusEnum
from app.services import transfer_recurrence, transfer_service
from app.exceptions import RecurrenceConflict
from app.utils.log_events import (
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
    EVT_TRANSFER_UPDATED,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    typed,
    create_account_of_type,
    last_covered_day,
    make_cadence_rule,
    transfer_side_record,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._test_helpers import state_template_price
from tests._test_helpers import transfer_amount

from ._helpers import _LogCapture


class TestTransferMaintain:
    """What a regeneration does to the transfers it ALREADY generated.

    Plan step R10-b, ruling **R-R19**: a pass MAINTAINS the rows the rule still
    names instead of destroying and rebuilding them, retires a row the rule has
    stopped naming, and RETAINS -- untouched, reported -- a row the owner has
    records against.  The transaction engine's twin is
    ``TestRegenerateForTemplate`` in ``test_recurrence_engine/``, split across its
    ``test_regenerate_retention.py`` and ``test_regenerate_conflicts.py``.
    """

    def _template_with_rows(self, seed_user, seed_periods, name="Maintained"):
        """Create a recurring transfer template and generate its rows.

        Args:
            seed_user: The seeded user fixture.
            seed_periods: The seeded pay periods.
            name: The template's name, so two templates can coexist.

        Returns:
            ``(template, savings, rows)`` -- the definition, its destination
            account, and the transfers it generated, oldest first.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", f'{name} Savings',
            anchor_balance=Decimal("500.00"),
        )
        db.session.flush()
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name=name,
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        make_cadence_rule(template, EVERY_PERIOD, interval_n=1)
        db.session.refresh(template)
        rows = transfer_recurrence.generate_for_template(
            template, self._schedule(template, seed_periods),
            seed_user["scenario"].id,
        )
        db.session.flush()
        assert len(rows) == 10
        return template, savings, sorted(rows, key=lambda x: x.id)

    def _schedule(self, template, seed_periods):
        """Return the whole-schedule GenerationSchedule for this owner."""
        return GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id), {p.id for p in seed_periods},
        )

    def _regenerate(self, template, seed_user, seed_periods):
        """Run one maintain pass, returning its conflict or ``None``."""
        try:
            transfer_recurrence.regenerate_for_template(
                template, self._schedule(template, seed_periods),
                seed_user["scenario"].id,
            )
            return None
        except RecurrenceConflict as conflict:
            return conflict
        finally:
            db.session.flush()

    @staticmethod
    def _side_records(xfer):
        """Return *xfer*'s from-side and to-side records, read by their own SQL.

        Each side's record hangs off the TRANSFER since plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where the class read
        each leg's covering movement until then (ruling **R-BAL167** class 4).
        """
        return [
            transfer_side_record(db.session, xfer.id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        ]

    def _settle_then_revert(self, xfer, seed_user, figure):
        """Leave *xfer* Projected while both sides still record what moved.

        The state ruling X-au-c3 creates deliberately: ``status_seam`` releases
        the ASSERTION on the way out of the settled band (``settled_on``,
        ``reconciled_by_id``) and KEEPS what moved (each side's covering
        movement, un-dated, and the figure the TRANSFER stores, its one home
        since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half, ruling
        **R-BAL366**, where each movement carried a copy until then), because
        the full-edit popover instructs the owner to revert in order to
        edit.  So the row is the rule's own again -- and it carries a figure
        read off a bank statement.

        Args:
            xfer: The transfer to settle and revert.
            seed_user: The seeded user fixture.
            figure: The settled amount to record.
        """
        transfer_service.settle_transfer(
            xfer.id, seed_user["user"].id, submitted=typed(figure),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.flush()
        records = self._side_records(xfer)
        # The figure is the transfer's, read once per side holding a record.
        assert [
            None if record is None else xfer.settled_figure
            for record in records
        ] == [figure, figure], (
            "setup: both sides must retain the figure through the revert"
        )
        assert all(record.settled_on is None for record in records), (
            "setup: the revert must release the assertion"
        )

    def test_an_empty_row_the_rule_stops_naming_is_retired(
        self, app, db, seed_user, seed_periods
    ):
        """A row carrying nothing follows the definition out of existence.

        Clearing the recurrence leaves the rule naming no period at all, so
        every generated row is an orphan.  One holding nothing of the owner's
        is removed -- through ``transfer_service.delete_transfer``, so the
        ledger is reconciled first.  It read each row's two shadow rows gone
        too until plan step ``balance:X-bi-6-4d-2``: a transfer stores no leg
        row since migration 82f7 (ruling **R-BAL167** class 1).
        """
        with app.app_context():
            template, _, _ = self._template_with_rows(
                seed_user, seed_periods,
            )

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None, "an empty row raises nothing"
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 0

    def test_one_pass_updates_creates_and_retires_together(
        self, app, db, seed_user, seed_periods
    ):
        """All three outcomes in one pass, on the rows each one is about.

        The cases above drive one outcome each, which is how a classifier comes
        to be right about each branch and wrong about the WHOLE: ``create_in``
        is computed from the periods no row occupies, and ``retire`` from the
        rows no period names, so the two are complementary and a defect in
        either shows up as the other's set being wrong.  This drives all three
        at once and asserts the resulting row set exactly.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            # Empty period 3: its row is deleted outright, so the rule names a
            # period holding nothing and the pass must CREATE there.
            gap_period_id = rows[3].pay_period_id
            transfer_service.delete_transfer(
                rows[3].id, seed_user["user"].id, soft=False,
            )
            db.session.flush()
            # Narrow to the first SEVEN periods, so periods 7-9 lose their rows
            # and retire, and move the amount so the survivors are updated.
            # The NAME is what makes the survivors UPDATE: a re-price alone
            # writes no row since plan step X-au-f, because the row reads its
            # definition's series rather than storing a copy of it.  The price
            # moves too, and every survivor is asserted worth it below.
            template.recurrence_rule.end_date = last_covered_day(seed_periods[6])
            template.name = "Narrowed Definition"
            template.default_amount = Decimal("155.00")
            state_template_price(template, Decimal("155.00"))
            db.session.flush()

            with _LogCapture("app.services.transfer_recurrence") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            [event] = cap.find_all(EVT_TRANSFER_RECURRENCE_REGENERATED)
            # Periods 0-6 are named: six hold a row (updated) and period 3 is
            # empty (created).  Periods 7-9 are no longer named and empty
            # (removed).
            assert event.updated_count == 6
            assert event.created_count == 1
            assert event.deleted_count == 3
            assert event.retained_conflict_count == 0

            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert len(live) == 7
            assert {x.pay_period_id for x in live} == {
                p.id for p in seed_periods[:7]
            }
            assert all(transfer_amount(x) == Decimal("155.00") for x in live)
            # The created row is a NEW id in the emptied period, and it is the
            # only one: the pass created exactly where nothing was.
            created_rows = [x for x in live if x.pay_period_id == gap_period_id]
            assert len(created_rows) == 1
            assert created_rows[0].id not in {x.id for x in rows}

    def test_a_pass_into_another_user_s_scenario_writes_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods
    ):
        """The cross-user defense still guards the maintain path.

        ``check_scenario_ownership`` runs before the plan is resolved, and it
        also DISAMBIGUATES that plan: a ``None`` plan means "this template no
        longer recurs" and nothing else, because ownership was settled first.
        Get that order wrong and a cross-user probe would look like a cleared
        recurrence and RETIRE every row the template has.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            before = {xfer.id for xfer in rows}

            created = transfer_recurrence.regenerate_for_template(
                template, self._schedule(template, seed_periods),
                seed_second_user["scenario"].id,
            )
            db.session.flush()

            assert created == []
            assert {
                xfer.id for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                )
            } == before, "a cross-user pass retired this owner's rows"


    def test_a_row_holding_a_note_is_retained_when_the_rule_stops_naming_it(
        self, app, db, seed_user, seed_periods
    ):
        """A note the owner typed keeps its row alive and reports it.

        ``Transfer.notes`` is free text no writer derives, and
        ``create_transfer`` is never handed one -- so the delete-and-recreate
        sweep this replaces dropped it silently on every template edit.  The
        row is left EXACTLY as it was found, and named in the raise so the
        route can tell the owner.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            noted = rows[3]
            transfer_service.update_transfer(
                noted.id, seed_user["user"].id,
                notes="ask the credit union about this one",
            )
            db.session.flush()

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [noted.id]
            assert conflict.overridden == []
            assert conflict.deleted == []
            survivor = db.session.get(Transfer, noted.id)
            assert survivor is not None
            assert survivor.notes == "ask the credit union about this one"
            # Every OTHER row was empty, so the same pass retired all nine.
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 1

    def test_a_whitespace_only_note_does_not_retain_its_row(
        self, app, db, seed_user, seed_periods
    ):
        """Blank text is not a record, so it does not block a definition edit.

        The firing control for the case above: if the predicate tested
        ``notes is not None`` rather than what the owner actually typed, a
        stray space would freeze a row against every future edit and report a
        conflict the owner cannot act on.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            transfer_service.update_transfer(
                rows[3].id, seed_user["user"].id, notes="   ",
            )
            db.session.flush()

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 0

    def test_a_row_that_kept_its_settlement_record_is_retained(
        self, app, db, seed_user, seed_periods
    ):
        """A figure read off a statement survives a definition edit.

        The money defect this step closes.  A transfer settled and then
        REVERTED -- which is what the full-edit popover instructs, and which
        ``status_seam`` answers by releasing the assertion and KEEPING what
        moved -- is Projected, not overridden, and so was the sweep's to
        delete.  Reproduced on a production clone before this step: `$321.45`
        on both legs, destroyed by a rename with no prompt.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[5]
            self._settle_then_revert(
                recorded, seed_user, Decimal("321.45"),
            )

            template.recurrence_rule = None
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [recorded.id]
            survivor = db.session.get(Transfer, recorded.id)
            assert survivor is not None
            # Each SIDE's record since plan step balance:X-bi-6-4d-2, where
            # each leg's was read (ruling R-BAL167 class 1), and the figure
            # the TRANSFER stores since that step's 4d-4 half (ruling
            # R-BAL366), read once per side holding a record.
            assert [
                None if record is None else survivor.settled_figure
                for record in self._side_records(survivor)
            ] == [
                Decimal("321.45"), Decimal("321.45"),
            ]

    def test_an_endpoint_move_applies_to_a_row_holding_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """A definition's account change reaches its rows IN PLACE.

        The old sweep applied it by destroying every row and re-creating it at
        the new endpoints; the pass now moves the transfer, keeping each row's
        id.  It read each row's two shadow rows too -- the income leg on the new
        account and both legs' display names -- until plan step
        ``balance:X-bi-6-4d-2``: a transfer stores no leg row since migration
        82f7, so each side's account is its parent's endpoint, graded once by
        ``tests.test_services.test_transfer_legs`` ::
        ``test_both_sides_show_the_row_in_every_field_the_table_names``, and
        its name is composed from the endpoints' names -- graded in memory by
        the same module's ``TestALegsDisplayProjections`` ::
        ``test_the_label_is_composed_from_the_endpoints_current_names``, while
        a saved transfer of a definition, this case's rows, is asked its label
        by no case (that test's own docstring; ruling **R-BAL167** class 1).
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            elsewhere = create_account_of_type(
                seed_user, db.session, "Savings", "Elsewhere",
                anchor_balance=Decimal("0.00"),
            )
            db.session.flush()
            old_ids = {xfer.id for xfer in rows}

            template.to_account_id = elsewhere.id
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert {xfer.id for xfer in live} == old_ids
            for xfer in live:
                assert xfer.to_account_id == elsewhere.id

    def test_an_endpoint_move_is_retained_on_a_row_holding_records(
        self, app, db, seed_user, seed_periods
    ):
        """A recorded figure is not re-filed against accounts nobody asserted.

        A side's record is what moved between the OLD pair of accounts, and
        any statement link it carries is scoped BY account
        (``fk_transaction_entries_reconciled_by``; the record hangs off the
        TRANSFER since plan step ``balance:X-bi-6-4d-2``, ruling **R-BAL88**,
        and held the link on its shadow, ``fk_transactions_reconciled_by``,
        until then), so applying an endpoint move to such a row would
        re-attribute both.  The pass leaves it exactly as found and asks --
        while every row holding nothing follows the definition in the same
        pass.
        """
        with app.app_context():
            template, savings, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[2]
            self._settle_then_revert(recorded, seed_user, Decimal("77.10"))
            elsewhere = create_account_of_type(
                seed_user, db.session, "Savings", "Elsewhere",
                anchor_balance=Decimal("0.00"),
            )
            db.session.flush()

            template.to_account_id = elsewhere.id
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [recorded.id]
            held = db.session.get(Transfer, recorded.id)
            assert held.to_account_id == savings.id, (
                "the retained row must be exactly as the pass found it"
            )
            moved = [
                xfer for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                ) if xfer.id != recorded.id
            ]
            assert len(moved) == 9
            assert all(x.to_account_id == elsewhere.id for x in moved)

    def test_every_derived_column_is_maintained_not_just_the_amount(
        self, app, db, seed_user, seed_periods
    ):
        """All SIX columns a definition states reach the rows it generated.

        **Found missing by an adversarial review of plan step R10-b**, which
        measured it: excluding ``name`` and ``category_id`` from the maintain
        diff left the WHOLE suite green, so a rename of a recurring transfer
        silently not reaching its rows would have shipped.  Only ``amount``,
        ``due_date`` and the endpoint pair were pinned.

        This is the transfer twin of the transaction engine's
        ``test_every_derived_column_is_maintained_not_just_the_amount``: move
        every field at once and read every one back, on the rows the pass kept.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            elsewhere = create_account_of_type(
                seed_user, db.session, "Savings", "All Six Destination",
                anchor_balance=Decimal("0.00"),
            )
            other_source = create_account_of_type(
                seed_user, db.session, "Savings", "All Six Source",
                anchor_balance=Decimal("900.00"),
            )
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Rebalance", sort_order=95,
            )
            db.session.add(category)
            db.session.flush()
            old_ids = {xfer.id for xfer in rows}

            template.from_account_id = other_source.id
            template.to_account_id = elsewhere.id
            template.name = "Every Column Moved"
            template.category_id = category.id
            template.default_amount = Decimal("212.12")
            state_template_price(template, Decimal("212.12"))
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert {xfer.id for xfer in live} == old_ids, (
                "the pass rebuilt rows instead of maintaining them"
            )
            for xfer in live:
                assert xfer.from_account_id == other_source.id
                assert xfer.to_account_id == elsewhere.id
                assert xfer.name == "Every Column Moved"
                assert xfer.category_id == category.id
                assert transfer_amount(xfer) == Decimal("212.12")
                # The sixth is the rule's, and every-paycheck dates a row from
                # its period's start.
                assert xfer.due_date == db.session.get(
                    PayPeriod, xfer.pay_period_id,
                ).start_date

    def test_a_SOURCE_move_is_retained_on_a_row_holding_records(
        self, app, db, seed_user, seed_periods
    ):
        """Reattribution is a question about the PAIR, not the destination.

        **Found missing by an adversarial review of plan step R10-b**: every
        endpoint test moved ``to_account_id``, so comparing only the destination
        left the suite green -- and a template whose SOURCE moves would have
        silently re-filed a settled EXPENSE leg's figure onto an account nobody
        asserted it on.  That is the same money defect in the other leg.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[1]
            self._settle_then_revert(recorded, seed_user, Decimal("64.20"))
            new_source = create_account_of_type(
                seed_user, db.session, "Savings", "Moved Source",
                anchor_balance=Decimal("800.00"),
            )
            db.session.flush()
            was_from = recorded.from_account_id

            template.from_account_id = new_source.id
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [recorded.id]
            held = db.session.get(Transfer, recorded.id)
            assert held.from_account_id == was_from
            moved = [
                xfer for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                ) if xfer.id != recorded.id
            ]
            assert len(moved) == 9
            assert all(x.from_account_id == new_source.id for x in moved)

    def test_a_NAMED_row_holding_records_still_takes_the_new_amount(
        self, app, db, seed_user, seed_periods
    ):
        """Records retain a row from a RE-ATTRIBUTION, not from every edit.

        The boundary beside this step's headline fix, and an adversarial review
        found no transfer case pinning it.  Retention is
        ``reattributed AND with_records``: a settlement record kept through a
        revert does NOT freeze the row against an ordinary price change, because
        an amount change re-files nothing -- the row stays on the accounts its
        figure was read against.  Freezing it would make a recorded figure stop
        the definition reaching every row it names, which is a different defect
        from the one being fixed.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            recorded = rows[6]
            self._settle_then_revert(recorded, seed_user, Decimal("58.00"))

            template.default_amount = Decimal("175.00")
            state_template_price(template, Decimal("175.00"))
            db.session.flush()
            conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None, "an amount change re-attributes nothing"
            held = db.session.get(Transfer, recorded.id)
            assert transfer_amount(held) == Decimal("175.00")
            # The record it kept is untouched by the re-price: what MOVED and
            # what is PLANNED are different facts (plan step X-au-c3).  Each
            # SIDE's record since plan step balance:X-bi-6-4d-2, where each
            # leg's was read (ruling R-BAL167 class 1), and the figure the
            # TRANSFER stores since that step's 4d-4 half (ruling R-BAL366),
            # read once per side holding a record.
            assert [
                None if record is None else held.settled_figure
                for record in self._side_records(held)
            ] == [
                Decimal("58.00"), Decimal("58.00"),
            ]

    def test_a_pass_that_changes_nothing_writes_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """Re-running a pass over rows already equal to their definition is silent.

        ``update_transfer`` reconciles the posting ledger, re-derives a loan's
        genesis when an endpoint is one, moves the pair's optimistic-lock
        counter and emits an audit row -- so calling it for a row that already
        matches is that whole cost, per row, for nothing.  Measured on a
        production clone: all 99 sweepable rows across the four live recurring
        templates came back with zero fields differing, and the two loan
        templates' passes fell from 1.58 s and 1.14 s to about 4 ms each.

        The version counters are the assertion that matters: a bumped one
        invalidates every open edit form for that transfer.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            versions = {xfer.id: xfer.version_id for xfer in rows}

            with _LogCapture("app.services.transfer_service") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert cap.find_all(EVT_TRANSFER_UPDATED) == []
            assert cap.find_all(EVT_TRANSFER_HARD_DELETED) == []
            db.session.expire_all()
            for xfer_id, version in versions.items():
                assert db.session.get(Transfer, xfer_id).version_id == version

    def test_the_SECOND_pass_after_a_real_change_writes_nothing(
        self, app, db, seed_user, seed_periods
    ):
        """A pass that applied a change is a no-op when re-run immediately.

        **Convergence, asked the second time.**  A maintain pass decides what to
        write by comparing each row against what its definition says, so it is
        correct only if applying it makes that comparison come back EMPTY.  A
        field written in a form the comparison cannot recognise -- a Decimal at
        a different scale, a date the door coerces, a value the service
        normalises on the way in -- would leave the pass writing the same row on
        every edit forever, bumping its version counter and re-reconciling its
        ledger each time, while every single-pass test stayed green.

        This project has paid for that shape before: a producer correct on the
        first write and inverted on the repeat.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            template.default_amount = Decimal("133.70")
            state_template_price(template, Decimal("133.70"))
            template.name = "Renamed Mid-Life"
            db.session.flush()

            with _LogCapture("app.services.transfer_service") as first:
                self._regenerate(template, seed_user, seed_periods)
            assert len(first.find_all(EVT_TRANSFER_UPDATED)) == len(rows), (
                "setup: the first pass must actually have written"
            )
            db.session.expire_all()
            versions = {
                xfer.id: xfer.version_id
                for xfer in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                )
            }

            with _LogCapture("app.services.transfer_service") as second:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert second.find_all(EVT_TRANSFER_UPDATED) == [], (
                "the pass did not converge: it rewrites the same rows forever"
            )
            db.session.expire_all()
            for xfer_id, version in versions.items():
                assert db.session.get(Transfer, xfer_id).version_id == version

    def test_a_pass_that_changes_one_field_touches_only_that_field(
        self, app, db, seed_user, seed_periods
    ):
        """The firing control for the silent pass above.

        If the diff were dropped and the whole definition sent every time, the
        test above would still be green on the version counters only by luck --
        so this pins the other side: a real field change DOES reach the door,
        once per row, naming exactly the field that moved.

        **The field is the NAME, and it was the AMOUNT until plan step X-au-f.**
        That is not a substitution for convenience: a RE-PRICE no longer moves a
        row at all, because the row carries an ownership naming its definition
        and the definition's own series answers the figure -- so the diff sees
        nothing changed and the pass correctly writes nothing.  The second arm
        below asserts exactly that, which is the property this cutover is FOR
        and which the old spelling of this case could not have stated.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )

            template.name = "Renamed Definition"
            db.session.flush()
            with _LogCapture("app.services.transfer_service") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            updates = cap.find_all(EVT_TRANSFER_UPDATED)
            assert len(updates) == len(rows)
            assert {tuple(r.fields_changed) for r in updates} == {("name",)}

            # **A RE-PRICE writes NO row**, and every row is worth the new
            # figure regardless: one producer, reached by reading rather than by
            # copying.  Before this step the same act wrote every row and the
            # copy was what made them agree.
            state_template_price(template, Decimal("140.00"))
            db.session.flush()
            with _LogCapture("app.services.transfer_service") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is None
            assert cap.find_all(EVT_TRANSFER_UPDATED) == []
            for xfer in db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ):
                assert transfer_amount(xfer) == Decimal("140.00")

    def test_the_audit_event_separates_updated_created_and_removed(
        self, app, db, seed_user, seed_periods
    ):
        """One pass, one event, and its counts mean what they say.

        ``deleted_count`` used to count every non-overridden row in the window
        and ``created_count`` counted the same rows again, because the pass
        rebuilt everything it touched.  They now name disjoint sets -- a
        forensic reader comparing across this step must not treat the two as
        the same number -- and ``updated_count`` /
        ``retained_conflict_count`` are new.
        """
        with app.app_context():
            template, _, rows = self._template_with_rows(
                seed_user, seed_periods,
            )
            # The note goes on a row the narrowed rule will STOP naming, so the
            # one pass produces all three outcomes at once.
            noted = rows[5]
            assert noted.pay_period_id == seed_periods[5].id
            transfer_service.update_transfer(
                noted.id, seed_user["user"].id, notes="keep me",
            )
            db.session.flush()
            # Narrow the rule to the first FIVE periods and move the amount.
            # Periods 0-4 keep their rows and take the new figure (5 updated);
            # periods 5-9 lose theirs -- four are empty and retire, and the
            # noted one is retained (4 removed, 1 retained).  Nothing is
            # created: every period the narrowed rule names already has a row.
            # The NAME is what makes the survivors UPDATE; see the sibling
            # case for why a re-price alone no longer writes a row.
            template.recurrence_rule.end_date = last_covered_day(seed_periods[4])
            template.name = "Narrowed Definition"
            template.default_amount = Decimal("111.00")
            state_template_price(template, Decimal("111.00"))
            db.session.flush()

            with _LogCapture("app.services.transfer_recurrence") as cap:
                conflict = self._regenerate(template, seed_user, seed_periods)

            assert conflict is not None
            assert conflict.retained == [noted.id]
            [event] = cap.find_all(EVT_TRANSFER_RECURRENCE_REGENERATED)
            assert event.created_count == 0
            assert event.updated_count == 5
            assert event.deleted_count == 4
            assert event.retained_conflict_count == 1
            assert event.overridden_conflict_count == 0
            assert event.deleted_conflict_count == 0
            # The counts describe rows, not just each other.
            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert len(live) == 6
            # **All six, the RETAINED one included, and that changed at plan
            # step X-au-f.**  The retained row kept ``$100.00`` while it STORED
            # a figure the pass declined to rewrite; it stores none now, so its
            # plan follows its definition like every other row's.  Being
            # retained protects what the row RECORDED and its place in the
            # ledger -- it is what stops the pass RETIRING a row holding an
            # owner's note -- and a plan is not a record (plan step X-au-c3).
            # An OVERRIDE would still keep its own figure; this row is not one.
            assert sorted(transfer_amount(x) for x in live) == (
                [Decimal("111.00")] * 6
            )
