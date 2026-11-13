"""Transaction entry route tests: an entry that must belong to the URL's transaction, a companion's
access, and the edit popover's entries section.
"""

from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.ref import TransactionType

from tests._test_helpers import generate_row_of, make_expense_template, one_off_row_of
from tests._two_factor_sign_in import sign_in_seed_companion

from ._helpers import _add_entry, _create_visible_tracked_txn, _create_other_user_txn


# ---- Entry / transaction ID mismatch ------------------------------------

class TestEntryTransactionMismatch:
    """Tests that entry_id must belong to the txn_id in the URL."""

    @staticmethod
    def _create_second_tracked_txn(seed_periods, seed_entry_template):
        """Generate the fixture's definition a second row, in the second paycheck.

        Returns:
            Transaction object in the second pay period.
        """
        txn2 = generate_row_of(seed_entry_template["template"], seed_periods[1])
        db.session.commit()
        return txn2

    def test_patch_entry_wrong_txn_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with entry_id from a different transaction returns 404."""
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            txn2 = self._create_second_tracked_txn(
                seed_periods, seed_entry_template,
            )
            # Entry belongs to txn2.
            entry = _add_entry(txn2, seed_user, "50.00", "Aldi")

            # Try to PATCH via txn1's URL.
            resp = auth_client.patch(
                f"/transactions/{txn1.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 404

    def test_delete_entry_wrong_txn_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE with entry_id from a different transaction returns 404."""
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            txn2 = self._create_second_tracked_txn(
                seed_periods, seed_entry_template,
            )
            entry = _add_entry(txn2, seed_user, "50.00", "Aldi")

            resp = auth_client.delete(
                f"/transactions/{txn1.id}/entries/{entry.id}",
            )
            assert resp.status_code == 404

    def test_companion_mismatch_blocks_non_visible_entry(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template, seed_companion,
    ):
        """Companion cannot modify entries from non-visible txns via URL confusion.

        Attack scenario: companion sends PATCH to a visible transaction's
        URL but includes an entry_id belonging to a non-visible transaction.
        The entry-transaction mismatch guard returns 404.
        """
        with app.app_context():
            # Make the first template companion-visible.
            template_visible = seed_entry_template["template"]
            template_visible.companion_visible = True
            db.session.commit()

            txn_visible = seed_entry_template["transaction"]

            # Create a second definition that is NOT companion-visible, and
            # its own row beside the visible one's.
            template_hidden = make_expense_template(
                db.session, seed_user, amount="300.00",
                name="Secret Groceries", category_key="Groceries",
                is_envelope=True, companion_visible=False,
            )
            txn_hidden = generate_row_of(template_hidden, seed_periods[0])
            db.session.commit()

            # Entry on the hidden transaction.
            entry = _add_entry(txn_hidden, seed_user, "50.00", "Secret Store")

            # Companion logs in and uses visible txn URL with hidden entry ID.
            comp = sign_in_seed_companion(app)
            resp = comp.patch(
                f"/transactions/{txn_visible.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 404


# ---- Companion access ----------------------------------------------------

class TestCompanionAccess:
    """Tests for companion user access to entry routes."""

    def test_companion_lists_entries_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can list entries on a companion-visible transaction."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            _add_entry(txn, seed_user, "50.00", "Kroger")

            comp = sign_in_seed_companion(app)
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Kroger" in resp.data

    def test_companion_creates_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can create entries; entry.user_id is the companion's ID."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]

            comp = sign_in_seed_companion(app)
            resp = comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "45.00",
                    "description": "Aldi",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.user_id == seed_companion["user"].id

    def test_companion_updates_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can update entries on companion-visible transactions."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            comp = sign_in_seed_companion(app)
            resp = comp.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "60.00", "direction": "charge"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.amount == Decimal("60.00")

    def test_companion_deletes_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can delete entries on companion-visible transactions."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            entry_id = entry.id

            comp = sign_in_seed_companion(app)
            resp = comp.delete(
                f"/transactions/{txn.id}/entries/{entry_id}",
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_companion_rejected_for_non_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template, seed_companion,
    ):
        """Companion gets 404 for transactions not flagged companion_visible."""
        with app.app_context():
            # companion_visible defaults to False.
            txn = seed_entry_template["transaction"]
            comp = sign_in_seed_companion(app)
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 404

    def test_companion_rejected_for_other_owner_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion gets 404 for transactions belonging to a different owner."""
        with app.app_context():
            other = _create_other_user_txn()
            comp = sign_in_seed_companion(app)
            resp = comp.get(
                f"/transactions/{other['transaction'].id}/entries",
            )
            assert resp.status_code == 404

    def test_both_users_see_all_entries(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Both owner and companion see all entries on a shared transaction."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            # Owner creates an entry.
            _add_entry(txn, seed_user, "50.00", "Kroger")
            # Companion creates an entry via route.
            comp = sign_in_seed_companion(app)
            comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "30.00",
                    "description": "Aldi",
                    "purchased_on": "2026-01-06",
                },
            )

            # Owner sees both.
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert b"Kroger" in resp.data
            assert b"Aldi" in resp.data

            # Companion sees both.
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert b"Kroger" in resp.data
            assert b"Aldi" in resp.data


# ---- Popover integration ------------------------------------------------

class TestPopoverIntegration:
    """Tests for entry section rendering in the full edit popover."""

    def test_tracked_txn_has_entries_section(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Full edit popover for tracked transaction contains entries section."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" in resp.data
            # Verify the lazy-load hx-get URL is present.
            expected_url = f"/transactions/{txn.id}/entries"
            assert expected_url.encode() in resp.data

    def test_non_tracked_txn_no_entries_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Full edit popover for non-tracked transaction has no entries section."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Phone Bill",
                amount=Decimal("80.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" not in resp.data
