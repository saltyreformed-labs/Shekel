"""Grid route tests: a transaction's negative paths, and schema validation."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.transaction import Transaction
from app.models.ref import TransactionType
from app.services.auth_service import hash_password
from app.services import pay_period_write, status_seam
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER, ROW_NO_LONGER_EXISTS_MSG

from tests._test_helpers import one_off_row_of, resolved_amount, rhythm_of


class TestTransactionNegativePaths:
    """Tests for transaction route error handling, validation, and edge cases."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Helper: create and return a projected expense."""
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = one_off_row_of(
            seed_periods_today[0],
            name="Test Expense",
            amount=Decimal("123.45"),
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.commit()
        return txn

    # ── Nonexistent ID tests ──────────────────────────────────────

    def test_update_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/999999 returns 404 for nonexistent transaction.

        The body is ruling **credit_card:R-CC104**'s nameless sentence since
        plan step CC-5-4a-4 (developer 2026-09-23: a row the app cannot name
        -- an erased one-off, one that never existed, another user's -- shows
        "This transaction no longer exists.  Reload the page."), in the
        designed cell rather than a bare "Not found" htmx drops.  The status
        is the 404 it always was.
        """
        with app.app_context():
            resp = auth_client.patch(
                "/transactions/999999", data={"estimated_amount": "200.00"}
            )
            assert resp.status_code == 404
            assert resp.headers.get("Shekel-Designed-Fragment") == "1"
            assert ROW_NO_LONGER_EXISTS_MSG.encode() in resp.data

    def test_mark_done_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/mark-done returns 404 for nonexistent transaction.

        The body is ruling **credit_card:R-CC104**'s nameless sentence since
        plan step CC-5-4a-4 (see ``test_update_nonexistent_transaction``).
        """
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-done")
            assert resp.status_code == 404
            assert resp.headers.get("Shekel-Designed-Fragment") == "1"
            assert ROW_NO_LONGER_EXISTS_MSG.encode() in resp.data

    def test_cancel_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/cancel returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/cancel")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_delete_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/mark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_unmark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/999999/unmark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999/unmark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_get_cell_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/cell returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/cell")
            assert resp.status_code == 404

    def test_get_quick_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/quick-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/quick-edit")
            assert resp.status_code == 404

    def test_get_full_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/full-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/full-edit")
            assert resp.status_code == 404

    # ── Schema validation failure tests ───────────────────────────

    def test_create_transaction_missing_name(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions without required 'name' field returns 422 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 422
            resp_json = resp.get_json()
            assert "name" in resp_json["errors"]

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                scenario_id=seed_user["scenario"].id,
            ).count()
            assert count == 0

    def test_create_transaction_negative_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with negative estimated_amount returns 422."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Bad Amount",
                "estimated_amount": "-100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 422
            resp_json = resp.get_json()
            assert "estimated_amount" in resp_json["errors"]

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                name="Bad Amount",
            ).count()
            assert count == 0

    def test_create_transaction_zero_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with estimated_amount=0.00 succeeds (Range min=0 is inclusive)."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Zero Amount",
                "estimated_amount": "0.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            # Range(min=0) is inclusive by default -- 0.00 is accepted.
            assert resp.status_code == 201

            txn = db.session.query(Transaction).filter_by(name="Zero Amount").one()
            assert resolved_amount(txn) == Decimal("0.00")

    def test_create_transaction_missing_pay_period_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions without required pay_period_id returns 422 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "No Period",
                "estimated_amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 422
            resp_json = resp.get_json()
            assert "pay_period_id" in resp_json["errors"]

    def test_create_transaction_with_other_users_pay_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions with another user's pay_period_id returns 404."""
        with app.app_context():
            # Create a second user with pay periods for IDOR testing.
            other_user = User(
                email="other@shekel.local",
                password_hash=hash_password("otherpass"),
                display_name="Other User",
            )
            db.session.add(other_user)
            db.session.flush()

            settings = UserSettings(user_id=other_user.id)
            db.session.add(settings)

            other_periods = pay_period_write.record_paydays(
                user_id=other_user.id,
                first_payday=date(2026, 1, 2),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky",
                "estimated_amount": "100.00",
                "pay_period_id": other_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(name="Sneaky").count()
            assert count == 0

    def test_update_transaction_invalid_amount(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> with non-numeric amount returns 422."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"estimated_amount": "not_a_number"},
            )
            assert resp.status_code == 422

            # Verify the transaction's amount was NOT changed -- read through
            # the resolver: the row is a ONE-OFF, priced by its definition,
            # so the raw column is None (ruling R-BAL60).
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert resolved_amount(txn_after) == Decimal("123.45")

    # ── State transition edge cases ───────────────────────────────

    def test_mark_done_already_done_expense(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done is idempotent for already-done transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark-done.
            resp1 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp1.status_code == 200

            # NOTE: mark_done is idempotent -- no guard against double mark-done.
            # The route unconditionally sets status to done/received regardless
            # of current status.
            resp2 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_cancel_already_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/cancel is idempotent for already-cancelled transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First cancel.
            resp1 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp1.status_code == 200

            # NOTE: cancel is idempotent -- no guard against double cancel.
            resp2 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_mark_done_cancelled_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done on a cancelled transaction is rejected.

        After the C-21 follow-up the mark_done endpoint runs every
        status change through ``verify_transition``.  Cancelled may
        only revert to Projected; a direct jump to Paid would
        resurrect the row without the explicit revert audit step.
        Was previously a 200 with a comment noting "UI hides the Done
        button for non-projected statuses, but the API endpoint does
        not enforce this"; the API now enforces it.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Cancel first.
            auth_client.post(f"/transactions/{txn.id}/cancel")
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_cancel_done_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel on a done transaction is now rejected.

        After the C-21 follow-up the cancel endpoint runs every status
        change through ``app.services.state_machine.verify_transition``.
        Done -> Cancelled is illegal -- the user must revert to
        Projected first so the audit trail records both the revert
        and the subsequent cancellation.  Was previously a 200 with a
        comment noting "UI hides the Cancel button for done status";
        the API now enforces the same contract the UI was relying on.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done first.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_mark_done_with_invalid_actual_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done with non-numeric actual_amount returns 422.

        Pre-C-27: the route caught ``InvalidOperation`` and returned
        the literal string ``"Invalid actual amount"`` with status
        400.  Post-C-27: :class:`MarkDoneSchema` rejects the value at
        the schema tier.  Refit 2026-07-11 for the marker-header
        convention (closeout plan session 4, developer-ruled): the 422
        body changed from a JSON errors dict to a DESIGNED fragment --
        the requesting cell re-rendered with the flattened field
        message -- carrying the marker header so htmx swaps it.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "not_a_number"},
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "settled_amount" in body
            assert "txn-chip" in body

            # ``MarkDoneSchema`` runs before the route's status
            # mutation (commit C-27 reordered the parse to the
            # top of the function), so a rollback is no longer
            # required to keep the row clean.  The assertions
            # remain to guard against regression.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == "Projected"
            assert status_seam.recorded_settlement(txn_after) is None

    def test_mark_done_with_negative_actual_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done rejects negative actual_amount.

        Two layers reject this value:
          * Pre-C-27 only the DB CHECK constraint
            ``actual_amount >= 0`` rejected the row, surfacing as
            a 500 IntegrityError without the route's catch.
          * Post-C-27 (commit C-27 of the 2026-04-15 security
            remediation plan): :class:`MarkDoneSchema`'s
            ``Range(min=0)`` rejects the value at the schema tier
            so the route returns 400 before the row is touched.

        The DB CHECK remains as the storage-tier backstop (L-01)
        for any future caller that bypasses the schema.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            original_status_id = txn.status_id

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "-50.00"},
            )
            assert resp.status_code == 422

            db.session.expire_all()
            db.session.refresh(txn)
            assert txn.status_id == original_status_id, (
                "schema-tier rejection must not transition the row"
            )

    # ── XSS protection test ──────────────────────────────────────

    def test_create_transaction_xss_in_name(self, app, auth_client, seed_user, seed_periods_today):
        """Transaction name with script tag is stored but auto-escaped in rendered output."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "<script>alert(1)</script>",
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="<script>alert(1)</script>",
            ).one()

            # Verify Jinja2 auto-escaping prevents XSS in the cell partial.
            cell_resp = auth_client.get(f"/transactions/{txn.id}/cell")
            assert cell_resp.status_code == 200
            assert b"<script>" not in cell_resp.data
            assert b"&lt;script&gt;" in cell_resp.data


class TestSchemaValidation:
    """Tests for due_date schema validation."""

    def test_schema_due_date_on_transaction_update(self, app):
        """due_date accepted as a valid Date field in TransactionUpdateSchema."""
        from app.schemas.validation import TransactionUpdateSchema
        with app.app_context():
            schema = TransactionUpdateSchema()
            errors = schema.validate({"due_date": "2026-04-15"})
            assert "due_date" not in errors

    def test_schema_loads_a_settle_day_only_from_the_edit_door(self, app):
        """The UPDATE schema accepts a settle day; the two CREATE schemas do not.

        **It refused every settle day until plan step X-f1c** (this test's
        predecessor asserted exactly that and named X-f1c as what would break
        it).  Ruling **R-ED** opened the correction door: a settle day is an
        OBSERVED FACT about the user's bank, and an observed fact must be
        correctable when the statement disagrees.

        The CREATE halves stay shut, and that is the half still worth pinning:
        a transaction is born Projected -- ``status_seam.apply_status_change``
        is the only path into a settled status -- so a submitted day at create
        time would either date an unsettled row (breaking the settled-iff-dated
        invariant) or mint a born-settled row that bypassed the state machine
        and posted nothing to the ledger.
        """
        from app.schemas.validation import (
            InlineTransactionCreateSchema,
            TransactionCreateSchema,
            TransactionUpdateSchema,
        )
        with app.app_context():
            loaded = TransactionUpdateSchema().load({"settled_on": "2026-04-15"})
            assert loaded == {"settled_on": date(2026, 4, 15)}

            for create_schema in (
                TransactionCreateSchema(), InlineTransactionCreateSchema(),
            ):
                assert "settled_on" not in create_schema.fields, (
                    f"{type(create_schema).__name__} declares settled_on: a "
                    "created row is Projected and carries no settle day"
                )
