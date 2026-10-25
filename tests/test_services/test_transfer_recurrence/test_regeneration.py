"""Transfer recurrence tests: regenerate_for_template, and its deletions routed through the
transfer service.
"""

import logging
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app import ref_cache
from app.enums import StatusEnum
from app.services import transfer_recurrence, transfer_service
from app.exceptions import RecurrenceConflict
from app.utils.log_events import EVT_TRANSFER_HARD_DELETED
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import APP_PACKAGE_DIR, create_account_of_type, make_cadence_rule
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from app.services.amount_ownership import state_own_amount
from tests._test_helpers import state_template_price
from tests._test_helpers import transfer_amount

from ._helpers import make_template_with_rule, _LogCapture


class TestTransferRegeneration:
    """Tests for regenerate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Delegate to the module-level helper (see `make_template_with_rule`)."""
        return make_template_with_rule(seed_user, cadence, **rule_kwargs)

    def test_regenerate_maintains_its_rows_in_place(
        self, app, db, seed_user, seed_periods
    ):
        """A changed amount reaches the rows the rule already generated.

        **RE-RULED at plan step R10-b.**  This case read the count of rows a
        pass CREATED as the count of rows the rule names, and asserted that
        every id had CHANGED -- both true only while a regeneration rebuilt
        everything it touched.  It now asserts what a maintain pass promises,
        which is strictly more: the same rows, carrying the new figure, and
        nothing created or destroyed.  It read each row's two shadow
        transactions too until plan step ``balance:X-bi-6-4d-2``: a transfer
        stores no leg row since migration 82f7 (ruling **R-BAL167** class 1).

        Keeping the id is the point rather than a detail: the delete it replaces
        took the transfer's ``notes`` and any settlement record it had retained
        through a revert with it, and stranded its ledger attribution.
        """
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
            old_ids = {xfer.id for xfer in created}
            assert len(old_ids) == 10

            # Change the template amount.
            template.default_amount = Decimal("200.00")
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            new_created = transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Nothing was created: every period the rule names already had its
            # row, and the pass brought each one into line instead.
            assert new_created == []

            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert {xfer.id for xfer in live} == old_ids
            for xfer in live:
                assert transfer_amount(xfer) == Decimal("200.00")

    def test_regenerate_restores_a_drifted_due_date_in_place(
        self, app, db, seed_user, seed_periods
    ):
        """A maintain pass brings a row's DUE DATE back to what the rule says.

        The due date is one of the six columns a definition derives, and a
        delete-and-recreate re-derived it for free.  A maintain pass has to
        apply it, so this drives the one field whose value comes from the RULE
        rather than the template's own columns: the row is moved off its
        computed date through ``transfer_service.update_transfer``, and the
        next pass puts it back -- on the SAME row.

        The edit does not set ``is_override`` (that is the transfers PATCH
        route's doing, not the service's), so the row stays the rule's own and
        the pass may correct it.
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
            db.session.flush()
            drifted = created[0]
            computed = drifted.due_date
            transfer_service.update_transfer(
                drifted.id, seed_user["user"].id, due_date=date(2030, 12, 25),
            )
            db.session.flush()
            assert drifted.due_date == date(2030, 12, 25)

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            restored = db.session.get(Transfer, drifted.id)
            assert restored is not None, "the drifted row was destroyed"
            assert restored.due_date == computed

    def test_regenerate_produces_computed_due_dates(
        self, app, db, seed_user, seed_periods
    ):
        """Maintained monthly transfers carry the computed day_of_month due date.

        **RE-RULED at plan step R10-b**: it read the pass's return value, which
        was every row while a regeneration rebuilt them and is the rows it
        CREATED now.  The property is unchanged and is asserted over the rows
        the template actually has (here the 15th of each month -- see
        test_monthly_due_date_placed_on_day_of_month for the period math).

        **The due-date list is INHERITED from generation, and an adversarial
        review of R10-b measured that.**  What still requires the pass to have
        run is the amount.  The case that pins the maintain path's own handling
        of ``due_date`` is
        ``test_regenerate_restores_a_drifted_due_date_in_place``; this one pins
        that a maintain pass does not DISTURB a date generation placed
        correctly, which is the complementary half and is worth having beside
        it.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, day_of_month=15,
            )
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            template.default_amount = Decimal("200.00")
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            live = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert sorted(x.due_date for x in live) == [
                date(2026, 1, 15),
                date(2026, 2, 15),
                date(2026, 3, 15),
                date(2026, 4, 15),
                date(2026, 5, 15),
            ]
            for xfer in live:
                assert transfer_amount(xfer) == Decimal("200.00")

    def test_regenerate_raises_conflict_for_overridden(
        self, app, db, seed_user, seed_periods
    ):
        """Regenerate with overridden entry raises RecurrenceConflict."""
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

            # Override one entry.
            overridden_id = created[0].id
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            db.session.flush()

            with pytest.raises(RecurrenceConflict) as exc_info:
                transfer_recurrence.regenerate_for_template(
                    template, GenerationSchedule.for_period_ids(
                        BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                    ), seed_user["scenario"].id,
                )

            assert overridden_id in exc_info.value.overridden

    def test_regenerate_preserves_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Done transfers survive regeneration with original amount."""
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

            # Mark the first one as done (Paid).
            done_id_val = ref_cache.status_id(StatusEnum.DONE)
            created[0].status_id = done_id_val
            original_amount = created[0].amount
            done_id = created[0].id
            db.session.flush()

            # Change template amount and regenerate.
            template.default_amount = Decimal("200.00")
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transfer should still exist unchanged.
            done_xfer = db.session.get(Transfer, done_id)
            assert done_xfer is not None
            assert done_xfer.amount == original_amount


class TestRegenerateDeletionRoutedThroughService:
    """Commit 34 / LOW-02 / B6-03 -- regen deletes must use the canonical
    transfer_service.delete_transfer path so one EVT_TRANSFER_HARD_DELETED
    audit event is emitted per deleted transfer.  This asserts forensic
    completeness, not arithmetic correctness.  The orphan-verification
    self-check and the shadow pair it counted went at plan step
    ``balance:X-bi-6-4d-2``: a transfer stores no leg row since migration
    82f7 (ruling **R-BAL167**).
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + transfer template."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings C34",
            anchor_balance=Decimal("500.00"),
        )

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Commit 34 Transfer",
            default_amount=Decimal("100.00"),
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
        return template

    def _retire_every_row(self, seed_user, seed_periods, template):
        """Clear the recurrence, then run the pass that retires what it left.

        **The setup these cases needed from plan step R10-b onwards.**
        Each was written against a regeneration that deleted every
        non-overridden row in the window and rebuilt it, so an AMOUNT change was
        enough to observe a deletion.  A maintain pass deletes only what the
        rule NO LONGER names, so the retirement has to be caused rather than
        assumed -- and clearing the recurrence is the shape that causes the most
        of it, which is the "Does not repeat" edit
        ``regenerate_or_conflict_chooser`` documents.

        The subject of both cases that call it is unchanged and is what
        matters: every deletion this engine performs goes through
        ``transfer_service.delete_transfer``.

        Args:
            seed_user: The seeded user fixture.
            seed_periods: The seeded pay periods.
            template: The template whose rows are to be retired.

        Returns:
            ``(retired_ids, capture)`` -- the ids the pass should have removed
            and the :class:`_LogCapture` covering the pass.
        """
        retired_ids = {
            xfer.id for xfer in db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            )
        }
        template.recurrence_rule = None
        db.session.flush()
        with _LogCapture("app.services.transfer_service") as cap:
            created = transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
        assert created == [], (
            "A cleared recurrence names no period, so it can create nothing."
        )
        return retired_ids, cap

    def test_regen_delete_emits_one_hard_delete_event_per_deletion(
        self, app, db, seed_user, seed_periods
    ):
        """C34-1: every transfer this engine deletes emits exactly one
        ``EVT_TRANSFER_HARD_DELETED`` record on the
        ``app.services.transfer_service`` logger -- proof the bare
        ``db.session.delete(xfer)`` shortcut is gone and the canonical
        service path is the writer.

        **RE-RULED at plan step R10-b**: the deletion is now caused by the rule
        no longer naming the row's period (see :meth:`_retire_every_row`), where
        it used to be caused by any edit at all.  What is asserted is
        unchanged.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == 10, (
                "Setup expected 10 transfers to retire; got "
                f"{len(created)}."
            )

            deleted_ids, cap = self._retire_every_row(
                seed_user, seed_periods, template,
            )

            hard_delete_records = cap.find_all(EVT_TRANSFER_HARD_DELETED)
            assert len(hard_delete_records) == len(deleted_ids), (
                f"Expected {len(deleted_ids)} {EVT_TRANSFER_HARD_DELETED} "
                f"events (one per deleted transfer); observed "
                f"{len(hard_delete_records)}."
            )

            observed_transfer_ids = {r.transfer_id for r in hard_delete_records}
            assert observed_transfer_ids == deleted_ids

            for record in hard_delete_records:
                assert record.levelno == logging.INFO
                assert record.user_id == seed_user["user"].id

            # The rows really are gone, not merely reported.
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count() == 0

    def test_regen_delete_runs_orphan_verification(
        self, app, db, seed_user, seed_periods
    ):
        """C34-2: every retired transfer's hard delete is reported, once.

        The service path ran an orphan-verification self-check
        (``query(Transaction).filter_by(transfer_id=...).count()``) and
        surfaced it as the event's ``orphan_count`` until plan step
        ``balance:X-bi-6-4d-2``, which deleted the twins it counted and made
        the database refuse a row naming a transfer (ruling **R-BAL258**,
        graded by ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``), so no
        count of such rows can fail here.  What the event still says -- one
        hard delete per retired transfer -- is asserted (ruling **R-BAL167**
        class 1).

        **RE-RULED at plan step R10-b**: see :meth:`_retire_every_row` for why
        the deletion is now caused rather than assumed.
        """
        with app.app_context():
            template = self._make_template_with_rule(seed_user, EVERY_PERIOD)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            retired_ids, cap = self._retire_every_row(
                seed_user, seed_periods, template,
            )

            hard_delete_records = cap.find_all(EVT_TRANSFER_HARD_DELETED)
            assert hard_delete_records, (
                "No EVT_TRANSFER_HARD_DELETED records were emitted by the "
                "canonical service path."
            )
            assert {r.transfer_id for r in hard_delete_records} == retired_ids

    # ``test_regen_delete_leaves_no_orphan_shadows`` (C34-3) was deleted at plan
    # step ``balance:X-bi-6-4d-2`` (ruling **R-BAL320**): without its twin reads
    # it asserted nothing but its helper's ``created == []``.
    # ``test_regen_delete_emits_one_hard_delete_event_per_deletion`` runs the
    # same ``_retire_every_row`` and reads one event per deletion and every
    # retired transfer gone, failing first when the retire arm skips
    # ``delete_transfer`` (measured).

    def test_transfer_recurrence_module_has_no_bare_delete(self):
        """C34-4: lock the structural fix -- the source of
        ``app/services/transfer_recurrence.py`` must contain no bare
        ``db.session.delete(xfer)`` (or ``db.session.delete(transfer)``).
        Pins Transfer Invariant 4 literally: every deletion writer-path
        into ``budget.transfers`` is ``transfer_service.delete_transfer``.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree, and the read must hold text: a "not in" over an
        # empty read passes on nothing.
        module_path = APP_PACKAGE_DIR / "services" / "transfer_recurrence.py"
        source = module_path.read_text(encoding="utf-8")
        assert source, f"read nothing from {module_path}"
        assert "db.session.delete(xfer)" not in source, (
            "Found db.session.delete(xfer) in transfer_recurrence.py -- "
            "regen must route through transfer_service.delete_transfer "
            "(B6-03 / LOW-02 / Transfer Invariant 4)."
        )
        assert "db.session.delete(transfer)" not in source, (
            "Found db.session.delete(transfer) in transfer_recurrence.py "
            "-- regen must route through transfer_service.delete_transfer."
        )
