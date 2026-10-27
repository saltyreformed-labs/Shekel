"""Transfer recurrence tests: resolve_conflicts, and its routing through the transfer service."""

from decimal import Decimal

from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence, transfer_service
from app.utils.log_events import (
    EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_TRANSFER_RESTORED,
    EVT_TRANSFER_UPDATED,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    record_paydays_across_a_hole,
    rhythm_of,
    create_account_of_type,
    create_transfer,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from app.services.amount_ownership import state_own_amount
from tests._test_helpers import state_template_price
from tests._test_helpers import transfer_amount
from tests.test_routes.test_the_chooser_restores_what_the_unarchive_would import (
    _transfer_as_stored,
)

from ._helpers import make_template_with_rule, _LogCapture


class TestTransferResolveConflicts:
    """Tests for resolve_conflicts()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Delegate to the module-level helper (see `make_template_with_rule`)."""
        return make_template_with_rule(seed_user, cadence, **rule_kwargs)

    def test_resolve_keep_no_changes(self, app, db, seed_user, seed_periods):
        """action='keep' leaves overridden transfer unchanged."""
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
            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("999.99"))
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="keep", ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert transfer_amount(xfer) == Decimal("999.99")

    def test_resolve_update_clears_flags_and_applies_amount(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' clears the flags and hands the row back to its definition.

        **"Use" states no FIGURE for either kind since plan step X-au-f**
        (ruling **R-JD**): a generated transfer stores none, so the offer is
        *hand this row back*, and the definition's own effective-dated series
        prices it on the row's due date.  It passed ``new_amount=$200.00`` and
        asserted the row moved to it until then; the definition states
        ``$200.00`` here instead, so the figure asserted is unchanged and it
        now reaches the row by the mechanism that actually carries it.
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

            # Override one entry.
            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

# **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("200.00"))
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is False
            assert xfer.is_deleted is False
            assert transfer_amount(xfer) == Decimal("200.00")

    def test_cross_user_update_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """update with wrong user_id silently skips the transfer."""
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

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

            # Attempt resolve as second_user -- should be blocked.
            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("999.99"))
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                ctx=BalanceContext.build(second_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert transfer_amount(xfer) == Decimal("999.99")

    def test_cross_user_keep_blocked(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """keep with wrong user_id leaves transfer unchanged."""
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

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

            # 'keep' with wrong user -- no-op by design (keep never modifies).
            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("999.99"))
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="keep",
                ctx=BalanceContext.build(second_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is True
            assert transfer_amount(xfer) == Decimal("999.99")

    def test_same_user_update_succeeds(
        self, app, db, seed_user, seed_periods
    ):
        """update with correct user_id modifies the transfer."""
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

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

# **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("50.00"))
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert xfer.is_override is False
            assert transfer_amount(xfer) == Decimal("50.00")

    def test_mixed_ownership_list(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Only owned transfers are modified in a mixed-ownership list."""
        with app.app_context():
            # Create transfer for user A.
            template_a = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created_a = transfer_recurrence.generate_for_template(
                template_a, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_a.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            xfer_a = created_a[0]
            xfer_a.is_override = True
            state_own_amount(xfer_a, Decimal("999.99"))

            # Create transfer for user B (needs their own periods).
            from app.services import pay_period_service
            periods_b = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=10, rhythm=rhythm_of(14),
            )
            template_b = self._make_template_with_rule(
                second_user, EVERY_PERIOD
            )
            created_b = transfer_recurrence.generate_for_template(
                template_b, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template_b.user_id), {p.id for p in periods_b},
                ), second_user["scenario"].id,
            )
            db.session.flush()
            xfer_b = created_b[0]
            xfer_b.is_override = True
            state_own_amount(xfer_b, Decimal("888.88"))
            db.session.flush()

            # Resolve as user A -- only xfer_a should be modified.
# **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template_a, Decimal("50.00"))
            transfer_recurrence.resolve_conflicts(
                [xfer_a.id, xfer_b.id], action="update",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer_a)
            db.session.refresh(xfer_b)
            assert xfer_a.is_override is False
            assert transfer_amount(xfer_a) == Decimal("50.00")
            assert xfer_b.is_override is True
            assert transfer_amount(xfer_b) == Decimal("888.88")

    def test_a_transfer_whose_definition_is_gone_is_skipped_and_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer naming no definition is never handed back (ledger row N-440).

        Ad-hoc ones here: a transfer that never had a definition and one
        whose definition is gone are the same state to the rule.

        The transaction twin's guard, which this resolver lacked until it
        moved into the one rule both ask
        (``definition_unarchive.restored_by_use``, its ``orphaned``): a LIVE
        one raised ``ck_transfers_adhoc_owns_amount`` on the TEMPLATE
        declaration, and a DELETED one was counted as held by its books with
        no notice.  Both are skipped, counted, and left exactly as found.
        Unreachable from the route (the conflict set selects on the
        definition), so this drives the service entry directly.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("500.00"),
            )
            live = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            gone = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            transfer_service.delete_transfer(gone.id, seed_user["user"].id, soft=True)
            db.session.commit()
            ids = [live.id, gone.id]
            assert all(
                db.session.get(Transfer, xfer_id).transfer_template_id is None
                for xfer_id in ids
            )
            before = [_transfer_as_stored(seed_user, xfer_id) for xfer_id in ids]

            with _LogCapture("app.services.transfer_recurrence") as cap:
                notice = transfer_recurrence.resolve_conflicts(
                    ids, "update", BalanceContext.build(seed_user["user"].id),
                )
            db.session.commit()

            assert notice is None
            assert [_transfer_as_stored(seed_user, xfer_id) for xfer_id in ids] == before
            (event,) = [
                record for record in cap.records
                if getattr(record, "event", None)
                == EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED
            ]
            assert (
                event.resolved_count, event.skipped_count, event.kept_deleted_count,
            ) == (0, 2, 0)


# ── Service Routing Tests (L1 fix) ──────────────────────────────


class TestResolveConflictsServiceRouting:
    """Verify that resolve_conflicts routes through the transfer service
    instead of writing the transfer's columns directly.

    Closes L1 from transfer_rework_verification.md.  The routing's proof was
    the two shadow transactions following their parent until plan step
    ``balance:X-bi-6-4d-2``; a transfer stores no leg row since migration
    82f7, and a direct write of the same columns leaves every value the cases
    re-read equal, so each case reads the service's own audit events
    (``EVT_TRANSFER_UPDATED``, ``EVT_TRANSFER_RESTORED``) beside its row
    re-reads (ruling **R-BAL207**, the coordinator 2026-10-18).
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Helper: create a savings account + recurrence rule + template."""

        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Savings L1",
            anchor_balance=Decimal("500.00"),
        )

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Test Transfer L1",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
        )
        db.session.refresh(template)
        return template

    def test_update_action_routes_amount_through_service(
        self, app, db, seed_user, seed_periods
    ):
        """action='update' hands the transfer back through update_transfer.

        Graded by the one ``EVT_TRANSFER_UPDATED`` the service emits for this
        transfer, read first: a direct write of ``is_override`` and the
        ownership emits none.  It graded both shadow rows following the parent
        until plan step ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 1).
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

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("999.99"))
            db.session.flush()

            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("200.00"))
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_recurrence.resolve_conflicts(
                    [xfer.id], action="update",
                    ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.flush()

            assert [
                record.transfer_id for record in cap.find_all(EVT_TRANSFER_UPDATED)
            ] == [xfer.id], "the hand-back did not go through update_transfer"

            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("200.00")
            assert xfer.is_override is False

    def test_update_restores_soft_deleted_transfer(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts with action='update' restores a
        soft-deleted transfer by routing through restore_transfer, then
        updates it through update_transfer.

        Graded by the service's two audit events for this transfer, read
        first and in order: ``EVT_TRANSFER_RESTORED``, then
        ``EVT_TRANSFER_UPDATED``.  A direct write of ``is_deleted`` emits
        neither.  It graded both shadow rows following the parent until plan
        step ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 1).
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

            xfer = created[0]
            xfer_id = xfer.id
            # Soft-delete the transfer.
            from app.services import transfer_service as ts
            ts.delete_transfer(xfer_id, seed_user["user"].id, soft=True)
            db.session.flush()

            # Confirm soft-deleted state.
            db.session.refresh(xfer)
            assert xfer.is_deleted is True

            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("300.00"))
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_recurrence.resolve_conflicts(
                    [xfer_id], action="update",
                    ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.flush()

            assert [
                (record.event, record.transfer_id) for record in cap.records
                if getattr(record, "event", None)
                in (EVT_TRANSFER_RESTORED, EVT_TRANSFER_UPDATED)
            ] == [
                (EVT_TRANSFER_RESTORED, xfer_id),
                (EVT_TRANSFER_UPDATED, xfer_id),
            ], "the restore and the hand-back did not both go through the service"

            db.session.refresh(xfer)
            assert xfer.is_deleted is False
            assert xfer.is_override is False
            assert transfer_amount(xfer) == Decimal("300.00")

    def test_keep_action_preserves_user_override(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts with action='keep' preserves the
        user's overridden amount and does not reset it to the template
        default.  The user chose to override; the system must respect it.
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

            xfer = created[0]
            xfer.is_override = True
            state_own_amount(xfer, Decimal("350.00"))
            db.session.flush()

            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("350.00"))
            transfer_recurrence.resolve_conflicts(
                [xfer.id], action="keep",
                ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("350.00")
            assert xfer.is_override is True

    # ``test_all_five_invariants_hold_after_resolution`` was deleted at plan step
    # ``balance:X-bi-6-4d-2`` (ruling **R-BAL320**): its one assertion read the
    # transfer's twin rows, which the database refused to store
    # (``ck_transactions_names_no_transfer``, until plan step balance:X-bi-6-5
    # dropped the column), so it read none (measured 0);
    # its act is ``test_update_action_routes_amount_through_service``'s.

    def test_multiple_transfers_each_routed_through_service(
        self, app, db, seed_user, seed_periods
    ):
        """Verify that resolve_conflicts correctly processes multiple
        transfers in a single call, routing each through the transfer
        service independently.

        Graded by the service's audit events, read first: one
        ``EVT_TRANSFER_UPDATED`` per transfer and one ``EVT_TRANSFER_RESTORED``
        for the soft-deleted one.  It graded each transfer's shadow rows until
        plan step ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 1).
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
            assert len(created) >= 3

            # Override two transfers, soft-delete a third.
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            created[1].is_override = True
            state_own_amount(created[1], Decimal("888.88"))
            db.session.flush()

            from app.services import transfer_service as ts
            ts.delete_transfer(
                created[2].id, seed_user["user"].id, soft=True
            )
            db.session.flush()

            ids = [created[0].id, created[1].id, created[2].id]
            # **"Use" carries no figure of its own since ruling R-JD** (plan step
            # X-au-f), so the DEFINITION states the price this case asserts and
            # the row lands on it by reading its series.
            state_template_price(template, Decimal("250.00"))
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_recurrence.resolve_conflicts(
                    ids, action="update",
                    ctx=BalanceContext.build(seed_user["user"].id),
                )
            db.session.flush()

            assert sorted(
                record.transfer_id for record in cap.find_all(EVT_TRANSFER_UPDATED)
            ) == sorted(ids), "a hand-back did not go through update_transfer"
            assert [
                record.transfer_id for record in cap.find_all(EVT_TRANSFER_RESTORED)
            ] == [created[2].id], "the restore did not go through restore_transfer"

            for xfer_id in ids:
                xfer = db.session.get(Transfer, xfer_id)
                assert xfer.is_deleted is False
                assert xfer.is_override is False
                assert transfer_amount(xfer) == Decimal("250.00")
