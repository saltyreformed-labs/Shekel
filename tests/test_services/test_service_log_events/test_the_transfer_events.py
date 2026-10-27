"""The transfer service's one event per public mutation (create, update, soft delete, hard delete,
restore) and transfer recurrence's generate event.
"""

import logging
from decimal import Decimal

from app.services import transfer_recurrence, transfer_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_CREATED,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RECURRENCE_GENERATED,
    EVT_TRANSFER_RESTORED,
    EVT_TRANSFER_SOFT_DELETED,
    EVT_TRANSFER_UPDATED,
)
from tests._test_helpers import make_every_period_rule
from app.models.amount_ownership import AmountOwnership

from ._helpers import _LogCapture


def _make_transfer(td, **overrides):
    """Helper: create a transfer using the standard test data."""
    kwargs = dict(
        user_id=td["user"].id,
        from_account_id=td["account"].id,
        to_account_id=td["savings"].id,
        pay_period_id=td["periods"][0].id,
        scenario_id=td["scenario"].id,
        amount_ownership=AmountOwnership.own(Decimal("100.00")),
        status_id=td["projected"].id,
        category_id=td["category"].id,
    )
    kwargs.update(overrides)
    return transfer_service.create_transfer(transfer_service.TransferSpec(**kwargs))


class TestTransferServiceLogging:
    """transfer_service emits one event per public mutation."""

    def test_create_transfer_emits_event(self, app, db, _transfer_setup):
        """create_transfer emits ``transfer_created``, naming the transfer.

        The event no longer carries the two shadow-id fields it carried until
        plan step ``balance:X-bi-6-4d-2``.
        """
        td = _transfer_setup
        with app.app_context(), _LogCapture(
            "app.services.transfer_service",
        ) as cap:
            xfer = _make_transfer(td)

        record = cap.find(EVT_TRANSFER_CREATED)
        assert record is not None, (
            f"Missing {EVT_TRANSFER_CREATED}; observed: {cap.event_summary()}"
        )
        assert record.levelno == logging.INFO
        assert record.category == BUSINESS
        assert record.user_id == td["user"].id
        assert record.transfer_id == xfer.id
        assert record.amount == "100.00"
        # A transfer is one row since plan step ``balance:X-bi-6-4d-2``
        # (ruling R-BAL166), so a forensic query pivots on ``transfer_id``
        # alone (read above): each side's payment records name it through
        # their side link.  The two reads below pin only that the shadow-id
        # fields the event carried until then are not put back; no shadow can
        # be stored to name (no transaction has a transfer column since plan
        # step balance:X-bi-6-5; ``ck_transactions_names_no_transfer`` refused
        # one until then).
        assert not hasattr(record, "expense_shadow_id")
        assert not hasattr(record, "income_shadow_id")

    def test_update_transfer_emits_event(self, app, db, _transfer_setup):
        """update_transfer emits ``transfer_updated`` with sorted fields_changed."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("123.45")),
                    notes="updated notes",
                )

        record = cap.find(EVT_TRANSFER_UPDATED)
        assert record is not None
        assert record.user_id == td["user"].id
        assert record.transfer_id == xfer.id
        # Sorted alphabetically -- amount_ownership, notes.  The field the
        # caller states is the OWNERSHIP since ruling **R-BAL11** (plan step
        # X-au-f), where it was a figure plus two flags; the audit names what
        # was actually stated.
        assert record.fields_changed == ["amount_ownership", "notes"]

    def test_soft_delete_transfer_emits_event(self, app, db, _transfer_setup):
        """delete_transfer(soft=True) emits ``transfer_soft_deleted``."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.delete_transfer(
                    xfer.id, td["user"].id, soft=True,
                )

        record = cap.find(EVT_TRANSFER_SOFT_DELETED)
        assert record is not None
        assert record.user_id == td["user"].id
        assert record.transfer_id == xfer.id
        # The event no longer carries the count of shadows the delete hid,
        # which it carried until plan step ``balance:X-bi-6-4d-2``: there is
        # none to hide.  The read pins that field's absence only.
        assert not hasattr(record, "shadow_count")

    def test_hard_delete_transfer_emits_event(self, app, db, _transfer_setup):
        """delete_transfer(soft=False) emits ``transfer_hard_deleted``."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            xid = xfer.id
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.delete_transfer(xid, td["user"].id)

        record = cap.find(EVT_TRANSFER_HARD_DELETED)
        assert record is not None
        assert record.transfer_id == xid
        assert not hasattr(record, "orphan_count")

    def test_restore_transfer_emits_event(self, app, db, _transfer_setup):
        """restore_transfer emits ``transfer_restored`` after soft-delete."""
        td = _transfer_setup
        with app.app_context():
            xfer = _make_transfer(td)
            transfer_service.delete_transfer(
                xfer.id, td["user"].id, soft=True,
            )
            with _LogCapture("app.services.transfer_service") as cap:
                transfer_service.restore_transfer(xfer.id, td["user"].id)

        record = cap.find(EVT_TRANSFER_RESTORED)
        assert record is not None
        assert record.transfer_id == xfer.id
        # The event no longer carries the count of shadows the restore
        # brought back, which it carried until plan step
        # ``balance:X-bi-6-4d-2``: there is none.  The read pins that field's
        # absence only.
        assert not hasattr(record, "shadow_count")


# ── Transfer recurrence ────────────────────────────────────────────


class TestTransferRecurrenceLogging:
    """transfer_recurrence emits the analogous events for transfers."""

    def test_generate_emits_event(self, app, db, _transfer_setup, seed_periods):
        """generate_for_template emits ``transfer_recurrence_generated``."""
        td = _transfer_setup

        # Build a transfer template and rule.
        from app.models.transfer_template import TransferTemplate  # noqa: WPS433

        template = TransferTemplate(
            user_id=td["user"].id,
            from_account_id=td["account"].id,
            to_account_id=td["savings"].id,
            category_id=td["category"].id,
            name="Recurring Transfer",
            default_amount=Decimal("75.00"),
            is_active=True,
        )
        db.session.add(template)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_every_period_rule(db.session, template)

        with app.app_context(), _LogCapture(
            "app.services.transfer_recurrence",
        ) as cap:
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods[:2]},
                ), td["scenario"].id,
            )

        assert len(created) == 2
        record = cap.find(EVT_TRANSFER_RECURRENCE_GENERATED)
        assert record is not None
        assert record.user_id == td["user"].id
        assert record.template_id == template.id
        assert record.count == 2
