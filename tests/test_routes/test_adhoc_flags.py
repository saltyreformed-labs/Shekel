"""
Shekel Budget App -- One-off transaction flag tests (F2 / F3)

A ONE-OFF is a rule-less definition plus its placed row (plan step
``balance:X-bi-7b``, ruling **R-BAL20**), and its ``is_envelope`` (purchase
tracking) and ``companion_visible`` flags are the DEFINITION's, read by the
row through ``Transaction.tracks_purchases`` / ``visible_to_companion``
(ruling **R-BAL36**; the item-edit door is graded in
``test_one_off_row_doors``).  Until the family's cutover (plan step
``balance:X-bi-7d-2``) this module graded the LEGACY link-less row, which
stated both flags in cells of its own; the cutover minted every such row a
definition and dropped the cells, so every case here that was about a
one-off's BEHAVIOUR moved onto the producer's row
(:func:`~tests._test_helpers.one_off_row_of`), and the one case whose
subject was the legacy shape itself -- an envelope with no definition
moving WHOLE at carry-forward -- retired (a placed envelope takes the
rollover, ``test_carry_forward_service.
test_a_rule_less_definitions_envelope_takes_the_rollover``).  These tests
cover:

  * F3 -- purchase tracking on a one-off: entry creation, the expense-only
    guard, settle-from-entries on mark-done, the Credit-status block, the
    popover / create-form controls, and the checkbox-persistence semantics
    of the item update schema.
  * F2 -- companion visibility of a one-off: the companion query and the
    entry-access check resolve the definition's flag.

Resolution of the underlying properties is unit-tested in
tests/test_models/test_transaction_flag_resolution.py.
"""
import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    figure_source_columns,
    one_off_row_of,
    resolved_amount,
)


def _make_one_off(seed_user, period, *, is_envelope=False, companion_visible=False,
                  income=False, name="One-off", amount="100.00",
                  status=StatusEnum.PROJECTED):
    """Place and commit a ONE-OFF's row: a rule-less definition carrying the flags.

    Through the producer (:func:`~tests._test_helpers.one_off_row_of`), so
    the row is what the grid's create doors make; the two flags land on the
    definition and the row reads them there.
    """
    type_enum = TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE
    txn = one_off_row_of(
        period, name=name, amount=amount,
        user_id=period.user_id, account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        category_id=list(seed_user["categories"].values())[0].id,
        is_envelope=is_envelope, companion_visible=companion_visible,
    )
    txn.status_id = ref_cache.status_id(status)
    db.session.commit()
    return txn


def _add_entry(txn, seed_user, amount, description, purchased_on=None):
    """Attach a debit entry to a transaction directly via ORM."""
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(str(amount)),
        description=description,
        purchased_on=purchased_on or date.today(),
        is_credit=False,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


# ── F3: purchase tracking on one-offs ────────────────────────────────


class TestOneOffPurchaseTracking:
    """Entry tracking works on a one-off whose definition is an envelope."""

    def test_entry_create_succeeds_on_a_one_off_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST an entry on a one-off envelope row creates it (200)."""
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=True,
            )
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "40.00",
                    "description": "Kroger",
                    "purchased_on": seed_periods_today[0].start_date.isoformat(),
                },
            )
            assert resp.status_code == 200
            assert b"Kroger" in resp.data
            entries = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id).all()
            )
            assert len(entries) == 1
            assert entries[0].amount == Decimal("40.00")

    def test_entry_create_rejected_on_a_one_off_without_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST an entry on a non-envelope one-off is rejected (400)."""
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=False,
            )
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "40.00",
                    "description": "Kroger",
                    "purchased_on": seed_periods_today[0].start_date.isoformat(),
                },
            )
            assert resp.status_code == 400
            assert b"does not support individual purchase tracking" in resp.data
            assert (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id).count() == 0
            )

    def test_mark_done_settles_a_one_off_envelope_from_entries(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """mark-done on a one-off envelope settles it at sum(entries).

        Two debit entries of 30.00 + 20.00 -> actual_amount 50.00, and
        the status becomes Paid (Done).
        """
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=True,
                amount="500.00",
            )
            _add_entry(txn, seed_user, "30.00", "Store A")
            _add_entry(txn, seed_user, "20.00", "Store B")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200

            db.session.refresh(txn)
            # A ``purchases`` record stores no figure: the row's entries state
            # it (plan step X-au-c3).
            assert settled_figure(txn) == Decimal("50.00")  # 30.00 + 20.00
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert txn.status_id == done_id

    def test_credit_status_blocked_on_a_one_off_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Setting Credit status on a one-off envelope row is rejected (400).

        Credit is per-entry on tracked rows, never per-transaction, and the
        refusal says so in the status door's words since plan task plan#3057
        (``transaction_service.verify_requested_status``).
        """
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=True,
            )
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": credit_id, "version_id": txn.version_id},
            )
            assert resp.status_code == 400
            assert b"each purchase goes on the card by its own CC tick" in resp.data


class TestOneOffFlagUI:
    """The flag controls render only where they apply."""

    def test_full_edit_popover_shows_controls_for_a_one_off_expense(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off expense's popover renders both flag checkboxes."""
        with app.app_context():
            txn = _make_one_off(seed_user, seed_periods_today[0])
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b'name="is_envelope"' in resp.data
            assert b'name="companion_visible"' in resp.data

    def test_full_edit_popover_hides_tracking_for_a_one_off_income(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off income's popover hides is_envelope but shows companion_visible.

        Purchase tracking is expense-only; companion visibility applies
        to income too.
        """
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], income=True, name="Side gig",
            )
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b'name="is_envelope"' not in resp.data
            assert b'name="companion_visible"' in resp.data

    def test_full_edit_popover_checkbox_reflects_the_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The tracking box renders checked exactly when the row tracks.

        The control reads ``txn.tracks_purchases`` since plan step
        ``balance:X-bi-1`` -- the one accessor -- which is the DEFINITION's
        flag for a placed row, and this is the case that says so for BOTH
        states.  The tag is matched by its
        own ``id``, because ``companion_visible``'s box on the same card
        renders ``checked`` too and a page-wide search would read it.
        """
        with app.app_context():
            tracking = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=True,
                name="Tracking",
            )
            plain = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=False,
                name="Plain",
            )
            box = re.compile(r'<input[^>]*id="is_envelope"[^>]*>')

            resp = auth_client.get(f"/transactions/{tracking.id}/full-edit")
            assert resp.status_code == 200
            tag = box.search(resp.data.decode())
            assert tag is not None
            assert "checked" in tag.group(0)

            resp = auth_client.get(f"/transactions/{plain.id}/full-edit")
            assert resp.status_code == 200
            tag = box.search(resp.data.decode())
            assert tag is not None
            assert "checked" not in tag.group(0)

    def test_full_edit_popover_visibility_checkbox_reflects_the_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The visibility box renders checked exactly when the row is visible.

        The tracking box's twin: the control reads
        ``txn.visible_to_companion`` since plan step ``balance:X-bi-1b`` --
        the one accessor, the DEFINITION's flag for a placed row.  Matched
        by its own ``id`` for the reason the tracking case gives.
        """
        with app.app_context():
            visible = _make_one_off(
                seed_user, seed_periods_today[0], companion_visible=True,
                name="Visible",
            )
            private = _make_one_off(
                seed_user, seed_periods_today[0], companion_visible=False,
                name="Private",
            )
            box = re.compile(r'<input[^>]*id="companion_visible"[^>]*>')

            resp = auth_client.get(f"/transactions/{visible.id}/full-edit")
            assert resp.status_code == 200
            tag = box.search(resp.data.decode())
            assert tag is not None
            assert "checked" in tag.group(0)

            resp = auth_client.get(f"/transactions/{private.id}/full-edit")
            assert resp.status_code == 200
            tag = box.search(resp.data.decode())
            assert tag is not None
            assert "checked" not in tag.group(0)

    def test_full_edit_popover_shows_purchases_when_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off envelope's popover renders the Purchases entry list."""
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], is_envelope=True,
            )
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" in resp.data
            assert f'id="entry-list-{txn.id}"'.encode() in resp.data

    def test_full_create_form_renders_flag_controls(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The one-off full-create popover renders the flag checkboxes."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.get(
                "/transactions/new/full"
                f"?category_id={category.id}"
                f"&period_id={seed_periods_today[0].id}"
                f"&account_id={seed_user['account'].id}"
                f"&transaction_type_id={expense_id}"
            )
            assert resp.status_code == 200
            assert b'name="is_envelope"' in resp.data
            assert b'name="companion_visible"' in resp.data

    def test_add_transaction_modal_renders_flag_controls(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The grid's Add Transaction modal renders both flag checkboxes.

        The tracking row carries the data-adhoc-envelope-row hook that
        app.js uses to hide it when an income type is selected.
        """
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b'name="is_envelope"' in resp.data
            assert b'name="companion_visible"' in resp.data
            assert b"data-adhoc-envelope-row" in resp.data


class TestOneOffFlagValidation:
    """Income guard and create-time flag handling."""

    def test_inline_create_expense_sets_flags(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_inline lands is_envelope / companion_visible on the definition."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
                "companion_visible": "true",
            })
            assert resp.status_code == 201
            txn = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods_today[0].id)
                .order_by(Transaction.id.desc()).first()
            )
            assert txn.template.is_envelope is True
            assert txn.template.companion_visible is True
            assert txn.tracks_purchases is True
            assert txn.visible_to_companion is True

    def test_inline_create_defaults_flags_off(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An inline create omitting the flags defaults both to off."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_id,
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 201
            txn = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods_today[0].id)
                .order_by(Transaction.id.desc()).first()
            )
            assert txn.template.is_envelope is False
            assert txn.template.companion_visible is False
            assert txn.tracks_purchases is False
            assert txn.visible_to_companion is False

    def test_inline_create_income_rejects_is_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_inline rejects is_envelope on an income transaction (422)."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": income_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
            })
            assert resp.status_code == 422
            assert (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods_today[0].id).count() == 0
            )

    def test_create_transaction_expense_sets_flags(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_transaction (the Add Transaction modal endpoint) lands the flags on the definition."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.post("/transactions", data={
                "name": "Modal Expense",
                "estimated_amount": "60.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
                "companion_visible": "true",
            })
            assert resp.status_code == 201
            txn = (
                db.session.query(Transaction)
                .filter_by(name="Modal Expense").one()
            )
            assert txn.template.is_envelope is True
            assert txn.template.companion_visible is True
            assert txn.tracks_purchases is True
            assert txn.visible_to_companion is True

    def test_create_transaction_income_rejects_is_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_transaction rejects is_envelope on an income transaction (422)."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            resp = auth_client.post("/transactions", data={
                "name": "Modal Income",
                "estimated_amount": "60.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": income_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
            })
            assert resp.status_code == 422
            assert (
                db.session.query(Transaction)
                .filter_by(name="Modal Income").count() == 0
            )

    def test_update_income_rejects_is_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH cannot enable tracking on a one-off income row (400)."""
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], income=True, name="Side gig",
            )
            resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "is_envelope": "true",
                "version_id": txn.version_id,
            })
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.tracks_purchases is False
            assert txn.template.is_envelope is False


class TestOneOffFlagPersistence:
    """The item update schema must not clobber flags it was not sent."""

    def test_quick_edit_does_not_clear_flags(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A PATCH omitting the flag fields leaves them untouched.

        The quick-edit form sends only estimated_amount; without a
        load_default on the item update schema, the absent flags are not
        applied, so a one-off envelope stays an envelope.  The typed figure
        restates the definition's price in place (ruling **R-BAL29**), so
        it is read through the resolver rather than off the row.
        """
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=True,
            )
            resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "estimated_amount": "150.00",
                # What the quick-edit box was rendered with (R-JR): the
                # RESOLVED figure, since a placed row stores none of its own;
                # differing from the submitted figure is what makes this a
                # real retype.
                "estimated_amount_as_rendered": str(resolved_amount(txn)),
                "version_id": txn.version_id,
            })
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert resolved_amount(txn) == Decimal("150.00")
            assert txn.tracks_purchases is True
            assert txn.visible_to_companion is True

    def test_unchecking_flag_persists_false(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An unchecked box (hidden 'false' only) clears the flag.

        Mirrors the popover markup: when the box is unchecked the form
        submits only the hidden ``companion_visible=false``.
        """
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0], companion_visible=True,
            )
            resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "companion_visible": "false",
                "version_id": txn.version_id,
            })
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.visible_to_companion is False
            assert txn.template.companion_visible is False


# ── F2: companion visibility of one-offs ─────────────────────────────


class TestOneOffCompanionVisibility:
    """A companion sees a one-off by its definition's companion_visible flag."""

    def test_companion_sees_a_visible_one_off(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """A one-off whose definition is companion_visible appears in the companion view."""
        with app.app_context():
            _make_one_off(
                seed_user, seed_periods_today[0],
                companion_visible=True, name="Shared Dinner",
            )
            resp = companion_client.get(
                f"/companion/period/{seed_periods_today[0].id}",
            )
            assert resp.status_code == 200
            assert b"Shared Dinner" in resp.data

    def test_companion_cannot_see_a_hidden_one_off(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """A one-off whose definition is not companion_visible is hidden."""
        with app.app_context():
            _make_one_off(
                seed_user, seed_periods_today[0],
                companion_visible=False, name="Secret Gift",
            )
            resp = companion_client.get(
                f"/companion/period/{seed_periods_today[0].id}",
            )
            assert resp.status_code == 200
            assert b"Secret Gift" not in resp.data

    def test_companion_entry_access_on_a_visible_one_off(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """A companion may read entries on a visible one-off envelope."""
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=True,
            )
            _add_entry(txn, seed_user, "25.00", "Lunch")
            resp = companion_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Lunch" in resp.data

    def test_companion_entry_access_denied_on_a_hidden_one_off(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """A companion cannot read entries on a hidden one-off (404)."""
        with app.app_context():
            txn = _make_one_off(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=False,
            )
            _add_entry(txn, seed_user, "25.00", "Lunch")
            resp = companion_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 404
