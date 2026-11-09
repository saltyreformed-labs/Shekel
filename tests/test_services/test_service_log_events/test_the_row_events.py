"""The events a row's own workflows emit: mark-as-credit and unmark, an entry's create, update and
delete, a reconcile that lands (and none when it stamps nothing), an entry payback's create,
update and delete, and the settle from entries.
"""

from datetime import date
from decimal import Decimal

from dataclasses import replace

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.utils.dates import display_today
from app.models.transaction import Transaction
from app.services import (
    cash_ledger,
    credit_workflow,
    entry_service,
    reconcile_service,
    transaction_service,
)
from app.services.pay_calendar import calendar_for
from app.utils.log_events import (
    BUSINESS,
    EVT_CREDIT_MARKED,
    EVT_CREDIT_UNMARKED,
    EVT_ENTRIES_SETTLED_DAY_RECORDED,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_PAYBACK_CREATED,
    EVT_ENTRY_PAYBACK_DELETED,
    EVT_ENTRY_PAYBACK_UPDATED,
    EVT_ENTRY_UPDATED,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
)
from tests._test_helpers import add_txn, typed

from ._helpers import _LogCapture
from tests._cards import add_card
from tests._legacy_credit import plant_old_credit


class TestCreditWorkflowLogging:
    """The Credit press and unmark_credit emit one event each."""

    def test_mark_as_credit_emits_event(self, app, db, seed_user, _projected_expense):
        """The press emits ``credit_marked`` naming the card, the day and the figure.

        It named the payback it wrote until plan task plan#2929, which puts
        the row on the card and writes none (rulings plan#2887 / plan#2888).
        """
        with app.app_context():
            card = add_card(seed_user)
            row = db.session.get(Transaction, _projected_expense.id)
            with _LogCapture("app.services.transaction_service._credit") as cap:
                transaction_service.mark_as_credit(row)

            record = cap.find(EVT_CREDIT_MARKED)
            assert record is not None
            assert record.category == BUSINESS
            assert record.user_id == seed_user["user"].id
            assert record.transaction_id == _projected_expense.id
            assert record.card_account_id == card.id
            assert record.settled_on == row.settled_on.isoformat()
            assert record.amount == "50.00"

    @pytest.mark.parametrize("door", ["undo_cc", "status_projected"])
    def test_taking_a_row_off_the_card_emits_event(
        self, app, db, seed_user, _projected_expense, door,
    ):
        """Undo CC, or Status set back to Projected, logs ``credit_unmarked`` naming the card.

        Plan task plan#3055: the two doors are one act, so the seam logs the
        move where both reach it (``status_seam._covering._move_home``); the
        row goes back to Projected with its payment on checking, and the
        event names the card it left.
        """
        with app.app_context():
            card = add_card(seed_user)
            row = db.session.get(Transaction, _projected_expense.id)
            transaction_service.mark_as_credit(row)
            db.session.commit()
            with _LogCapture("app.services.status_seam._covering") as cap:
                if door == "undo_cc":
                    transaction_service.undo_credit(row)
                else:
                    transaction_service.apply_requested_status(
                        row, ref_cache.status_id(StatusEnum.PROJECTED),
                    )

            record = cap.find(EVT_CREDIT_UNMARKED)
            assert record is not None
            assert record.category == BUSINESS
            assert record.user_id == seed_user["user"].id
            assert record.transaction_id == _projected_expense.id
            assert record.card_account_id == card.id

    def test_undo_cc_on_a_bill_that_lives_on_the_card_emits_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A bill on the card, Paid there and Undo CC'd: its payment stays, so nothing is logged."""
        with app.app_context():
            card = add_card(seed_user)
            row = add_txn(
                db.session, seed_user, seed_periods[0], "Claude Max",
                Decimal("100.00"), account=card,
            )
            db.session.commit()
            transaction_service.settle_transaction(row)
            db.session.commit()
            with _LogCapture("app.services.status_seam._covering") as cap:
                transaction_service.undo_credit(row)

            assert cap.find(EVT_CREDIT_UNMARKED) is None
            assert [m.account_id for m in row.entries] == [card.id]

    def test_unmark_credit_emits_event(self, app, db, seed_user, _projected_expense):
        """unmark_credit emits ``credit_unmarked`` with deleted_payback_id."""
        with app.app_context():
            payback = plant_old_credit(db.session.get(Transaction, _projected_expense.id))
            db.session.commit()
            with _LogCapture("app.services.credit_workflow") as cap:
                credit_workflow.unmark_credit(
                    _projected_expense.id, seed_user["user"].id,
                )

        record = cap.find(EVT_CREDIT_UNMARKED)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.transaction_id == _projected_expense.id
        assert record.deleted_payback_id == payback.id


class TestEntryServiceLogging:
    """entry_service emits one event per mutation."""

    def test_create_entry_emits_event(self, app, db, seed_user, _envelope_transaction):
        """create_entry emits ``entry_created`` and propagates payback signal."""
        with app.app_context(), _LogCapture(
            "app.services.entry_service",
        ) as cap:
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Coffee",
                    purchased_on=date(2026, 1, 15),
                    is_credit=False,
                ),
            )

        record = cap.find(EVT_ENTRY_CREATED)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.owner_id == seed_user["user"].id
        assert record.transaction_id == _envelope_transaction.id
        assert record.entry_id == entry.id
        assert record.amount == "12.50"
        assert record.is_credit is False

    def test_update_entry_emits_event(self, app, db, seed_user, _envelope_transaction):
        """update_entry emits ``entry_updated`` with sorted fields_changed."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Coffee",
                    purchased_on=date(2026, 1, 15),
                ),
            )
            db.session.commit()
            with _LogCapture("app.services.entry_service") as cap:
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    figure=typed(Decimal("15.00")),
                    description="Coffee + tip",
                )

        record = cap.find(EVT_ENTRY_UPDATED)
        assert record is not None
        assert record.entry_id == entry.id
        # The door's own keys, as ``settle_day`` already logs for the day:
        # ``figure`` is the amount and who wrote it (plan step X-bi-3e-1).
        assert record.fields_changed == ["description", "figure"]

    def test_delete_entry_emits_event(self, app, db, seed_user, _envelope_transaction):
        """delete_entry emits ``entry_deleted``."""
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Coffee",
                    purchased_on=date(2026, 1, 15),
                ),
            )
            db.session.commit()
            entry_id = entry.id
            with _LogCapture("app.services.entry_service") as cap:
                entry_service.delete_entry(entry_id, seed_user["user"].id)

        record = cap.find(EVT_ENTRY_DELETED)
        assert record is not None
        assert record.entry_id == entry_id


# ── The reconcile writer ───────────────────────────────────────────


class TestReconcileServiceLogging:
    """reconcile_service emits one BUSINESS event per reconcile that lands.

    Split out of ``TestEntryServiceLogging`` at plan step X-f2-c1, when the
    writer these two cases exercise moved to its own module: a class named for
    one service whose cases call another is the shape the move exists to end.
    """

    def test_record_settled_days_emits_the_reconcile_event(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """record_settled_days emits the reconcile event when rows match.

        The SUCCESSOR to the two events the retired ``is_cleared`` flag needed
        (plan step S1-c, ruling R-DH (d)): the bulk clear that fired at every
        anchor true-up and the manual per-entry toggle both deleted with the
        flag, and one event now records what the user OBSERVED on a statement.

        Both counts are asserted because they are the pair an operator reads:
        ``requested_count`` is what the form submitted and ``recorded_count``
        is what the outstanding-set re-scope actually stamped.  A silent gap
        between them is how a forged or already-recorded id would show up in
        the log, so an event carrying only one of the two could not report it.
        """
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Coffee",
                    purchased_on=date(2026, 1, 1),
                ),
            )
            db.session.commit()
            observed_on = date(2026, 1, 5)
            # The account's REAL governing assertion, presented for the test's
            # statement day: the writer stamps its id, and the composite key
            # ``fk_transaction_entries_reconciled_by`` refuses a fabricated one
            # (plan step X-f3a-1, ruling **R-FL**).
            statement = replace(
                cash_ledger.governing_anchor(seed_user["account"].id),
                observed_on=observed_on,
            )
            with _LogCapture("app.services.reconcile_service") as cap:
                count = reconcile_service.record_settled_days(
                    reconcile_service.Statement(
                        calendar_for(seed_user["user"].id),
                        seed_user["account"].id, statement,
                    ),
                    {entry.id},
                )

        assert count == 1
        record = cap.find(EVT_ENTRIES_SETTLED_DAY_RECORDED)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.account_id == seed_user["account"].id
        assert record.observed_on == observed_on.isoformat()
        assert record.recorded_count == 1
        assert record.requested_count == 1

    def test_no_event_when_the_reconcile_stamps_nothing(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """An id that matches nothing is silent -- no event, no exception.

        The negative control for the test above, and the property that makes
        the log trustworthy: the event fires on what CHANGED, so a submission
        the outstanding-set re-scope rejects in full leaves no trace claiming a
        reconciliation happened.  Here the id belongs to a purchase made AFTER
        the day the balance was read, which cannot be inside it.
        """
        with app.app_context():
            entry = entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Coffee",
                    purchased_on=date(2026, 1, 15),
                ),
            )
            db.session.commit()
            statement = replace(
                cash_ledger.governing_anchor(seed_user["account"].id),
                observed_on=date(2026, 1, 5),
            )
            with _LogCapture("app.services.reconcile_service") as cap:
                count = reconcile_service.record_settled_days(
                    reconcile_service.Statement(
                        calendar_for(seed_user["user"].id),
                        seed_user["account"].id, statement,
                    ),
                    {entry.id},
                )

        assert count == 0
        assert cap.find(EVT_ENTRIES_SETTLED_DAY_RECORDED) is None
        assert entry.settled_on is None


# ── Entry credit workflow ──────────────────────────────────────────


class TestEntryCreditWorkflowLogging:
    """entry_credit_workflow emits create/update/delete payback events."""

    def test_payback_create_update_delete_cycle(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """A credit entry creates, updates, then deletes the payback."""
        with app.app_context():
            # CREATE: first credit entry triggers ``entry_payback_created``.
            with _LogCapture(
                "app.services.entry_credit_workflow",
            ) as cap:
                entry1 = entry_service.create_entry(
                    transaction_id=_envelope_transaction.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("25.00")),
                        description="Card 1",
                        purchased_on=date(2026, 1, 10),
                        is_credit=True,
                    ),
                )
            assert cap.find(EVT_ENTRY_PAYBACK_CREATED) is not None

            # UPDATE: a second credit entry bumps the payback amount.
            with _LogCapture(
                "app.services.entry_credit_workflow",
            ) as cap:
                entry_service.create_entry(
                    transaction_id=_envelope_transaction.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("10.00")),
                        description="Card 2",
                        purchased_on=date(2026, 1, 11),
                        is_credit=True,
                    ),
                )
            update_record = cap.find(EVT_ENTRY_PAYBACK_UPDATED)
            assert update_record is not None
            assert update_record.new_amount == "35.00"
            assert update_record.previous_amount == "25.00"

            # DELETE: removing the credit entries deletes the payback.
            entry_service.delete_entry(
                entry1.id, seed_user["user"].id,
            )
            db.session.commit()
            with _LogCapture(
                "app.services.entry_credit_workflow",
            ) as cap:
                # Toggling the remaining credit entry off (via update)
                # zeroes total_credit and triggers the delete branch.
                entries = entry_service.get_entries_for_transaction(
                    _envelope_transaction.id, seed_user["user"].id,
                )
                assert len(entries) == 1
                entry_service.update_entry(
                    entries[0].id, seed_user["user"].id,
                    is_credit=False,
                )
            assert cap.find(EVT_ENTRY_PAYBACK_DELETED) is not None


# ── Transaction service ────────────────────────────────────────────


class TestTransactionServiceLogging:
    """transaction_service.settle_from_entries emits the settle event."""

    def test_settle_from_entries_emits_event(
        self, app, db, seed_user, _envelope_transaction,
    ):
        """settle_from_entries emits ``transaction_settled_from_entries``."""
        with app.app_context():
            entry_service.create_entry(
                transaction_id=_envelope_transaction.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("33.00")),
                    description="Test entry",
                    purchased_on=date(2026, 1, 10),
                ),
            )
            db.session.commit()

            with _LogCapture(
                "app.services.transaction_service",
            ) as cap:
                # Reload to ensure relationships are fresh.
                txn = db.session.get(Transaction, _envelope_transaction.id)
                transaction_service.settle_from_entries(txn)

        record = cap.find(EVT_TRANSACTION_SETTLED_FROM_ENTRIES)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.transaction_id == _envelope_transaction.id
        assert record.settled_amount == "33.00"
        # The recorded settle DAY, replacing the ``explicit_paid_at`` boolean
        # the event carried while the helper took a (dead) ``paid_at`` knob --
        # a flag about the CALL is not a fact about the money, and plan step
        # X-f1 deleted both the knob and the flag.
        assert record.settled_on == display_today().isoformat()
