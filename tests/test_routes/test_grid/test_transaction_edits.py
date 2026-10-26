"""Grid route tests: what the card withholds, credit paybacks, and the full edit form's due date
and period.
"""

import re
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.ref import Status, TransactionType
from app.enums import MovementFigureSourceEnum
from app.services import status_seam
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from app.services.pay_calendar import calendar_for

from tests._test_helpers import generate_row_of, make_expense_template, resolved_amount
from app.services.row_valuation import settled_contribution, settled_figure

from ._helpers import _generate_first_row, _TestTransactionCRUDMembers


class TestTransactionCRUD(_TestTransactionCRUDMembers):
    """Card refusals, paybacks, the full edit form; members on _TestTransactionCRUDMembers."""

    def test_the_card_says_a_generated_row_will_not_come_back(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A template row soft-deletes, and *undoable* would be a lie.

        The two arms of the route's own fork are two different promises to the
        owner, so the dialog states whichever one applies rather than one
        sentence that is true of neither.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Template",
                category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods_today[0])
            db.session.commit()

            html = auth_client.get(
                f"/transactions/{txn.id}/full-edit",
            ).data.decode()

            assert "This occurrence stays deleted" in html
            assert "This cannot be undone." not in html

    def test_the_card_withholds_the_delete_from_a_CC_payback(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A payback is half of a credit assertion, so it does not leave alone.

        The card renders the SERVICE's sentence
        (``transaction_service.deletion_refusal``) and the route re-asks the
        same function, so what the screen withholds and what the door refuses
        cannot drift.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id, is_deleted=False)
                .one()
            )

            html = auth_client.get(
                f"/transactions/{payback.id}/full-edit",
            ).data.decode()
            assert "Delete this row" not in html
            assert "Undo CC" in html

            refused = auth_client.delete(f"/transactions/{payback.id}")

            assert refused.status_code == 400
            assert refused.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            assert db.session.get(Transaction, payback.id) is not None

    def test_mark_done_without_a_correction_records_the_derived_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark-done with nothing typed still RECORDS what moved.

        **This asserted the opposite until plan step X-au-c3** -- "sets status
        only", with ``actual_amount`` left NULL -- and that was the defect the
        step removes rather than a contract it breaks.  A settled row whose
        record was empty sent every reader to its PLAN, and a plan is a
        derivation that can move after the money has not.  A settle now always
        states the figure it booked and how that figure is known: ``derived``,
        because the app resolved it and no human corrected it.

        The figure is the row's resolved plan -- its definition's price,
        read through the resolver because a one-off's row stores none
        (ruling R-BAL60) -- which is what the old fall-back answered for it,
        so no balance moves.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            planned = resolved_amount(txn)

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == planned

    def test_cancel_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel sets status to cancelled."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert settled_contribution(txn) == Decimal("0")

    def test_mark_credit_creates_payback(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-credit creates payback in next period."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # A payback transaction should exist in the next period.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods_today[1].id,
            ).first()
            assert payback is not None, "Payback transaction was not created"
            assert payback.name == "CC Payback: Test Expense"
            assert payback.estimated_amount == Decimal("123.45")
            assert payback.status.name == "Projected"
            assert payback.pay_period_id == seed_periods_today[1].id
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_reverts_and_deletes_payback(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id>/unmark-credit reverts to projected and deletes payback."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark as credit.
            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # Now unmark.
            response = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"

            # Payback should be deleted.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods_today[1].id,
            ).first()
            assert payback is None

    def test_hard_delete_credit_source_deletes_payback(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE on a one-off Credit source removes the live payback too.

        Without the delete-side cleanup the ``SET NULL`` FK keeps the
        payback alive with its link nulled, silently inflating the next
        period's projected expenses with no offsetting credit row.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-credit")
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn_id, is_deleted=False,
            ).one()
            payback_id = payback.id

            response = auth_client.delete(f"/transactions/{txn_id}")
            assert response.status_code == 200

            # Source hard-deleted (a one-off does not recur) and the payback with it.
            assert db.session.get(Transaction, txn_id) is None
            assert db.session.get(Transaction, payback_id) is None

    def test_soft_delete_credit_source_deletes_payback(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE on a template-linked Credit source removes the payback.

        The source itself only soft-deletes (the recurrence engine needs
        the row), but the payback is an ad-hoc projected expense that
        must not survive its now-invisible source.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="123.45", name="Template",
                category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods_today[0])
            db.session.commit()

            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn.id, is_deleted=False,
            ).one()
            payback_id = payback.id

            response = auth_client.delete(f"/transactions/{txn.id}")
            assert response.status_code == 200

            # Source soft-deleted, payback hard-deleted.
            db.session.refresh(txn)
            assert txn.is_deleted is True
            assert db.session.get(Transaction, payback_id) is None

    def test_create_transaction_full_form(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with all fields creates a complete transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            response = auth_client.post("/transactions", data={
                "name": "Full Form Expense",
                "estimated_amount": "250.00",
                "pay_period_id": seed_periods_today[2].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Car Payment"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
            })
            assert response.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="Full Form Expense"
            ).one()
            assert resolved_amount(txn) == Decimal("250.00")
            assert txn.pay_period_id == seed_periods_today[2].id
            assert txn.category_id == seed_user["categories"]["Car Payment"].id

    def test_full_edit_due_date_input_renders_and_a_move_persists(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A one-off the modal made is DATED at birth, offers the input, and a move sticks.

        **It was "renders and persists when UNSET" until plan step
        balance:X-bi-7b**: a one-off is due on its paycheck's start unless the
        owner states a day (ruling R-BAL22), so a row the door creates with no
        date is no longer undated -- it carries the start.  What survives of
        the case is the property: the popover offers the input on a row no
        cadence generates, and a date saved through it persists.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            auth_client.post("/transactions", data={
                "name": "No Due Date Yet",
                "estimated_amount": "75.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
            })
            txn = db.session.query(Transaction).filter_by(
                name="No Due Date Yet"
            ).one()
            assert txn.due_date == seed_periods_today[0].start_date

            # The input renders: no cadence generates this row.
            edit_resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert edit_resp.status_code == 200
            assert b'name="due_date"' in edit_resp.data

            # Saving a date persists it on this non-transfer transaction.
            save_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "due_date": "2026-02-20",
                "version_id": txn.version_id,
            })
            assert save_resp.status_code == 200
            db.session.refresh(txn)
            assert txn.due_date == date(2026, 2, 20)

    def test_clearing_a_GENERATED_rows_due_date_is_refused(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A generated row's due date is its definition's (**R-JD**'s sibling).

        Plan step balance:X-au-e prices such a row from its definition's
        series ON ITS OWN DUE DATE, so a cleared date leaves a row no rule can
        answer -- and ``routes/grid/page`` prices every row it loads with no
        handler, so ONE of them 500s the whole grid. Reproduced on a clone of
        production before the fix: 926 rows priced before the cutover and
        raised after it.

        **The last assertion is the one that matters**: asserting only the
        refusal here would grade the door and not the thing that breaks, so
        this re-renders the grid afterwards.  (The sibling case used to clear
        the date on an ad-hoc row and assert 200; since plan step
        balance:X-bi-7b a one-off's date is priced on too and the clear is
        refused there as well, with its own sentence.)
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            row = _generate_first_row(template, seed_user, seed_periods_today)
            assert row is not None, "the fixture generated no rows"
            # The precondition the refusal exists for: this row is DERIVED.
            assert row.amount_source_id is not None
            assert row.due_date is not None
            was = row.due_date

            resp = auth_client.patch(f"/transactions/{row.id}", data={
                "due_date": "",
                "version_id": row.version_id,
            })

            assert resp.status_code == 400
            assert b"recurring transaction" in resp.data
            db.session.refresh(row)
            assert row.due_date == was
            # And the grid still renders, which is what the refusal protects.
            assert auth_client.get("/grid").status_code == 200

    def test_moving_a_GENERATED_rows_due_date_is_refused_too(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Keyed on the FIELD, not on emptiness.

        A moved date is not harmless: it re-prices the row against a different
        point in its definition's series, silently, and the next regeneration
        puts it back. Without this case the gate could be written as a
        clear-only test and still pass.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            row = _generate_first_row(template, seed_user, seed_periods_today)
            was = row.due_date

            resp = auth_client.patch(f"/transactions/{row.id}", data={
                "due_date": "2026-02-20",
                "version_id": row.version_id,
            })

            assert resp.status_code == 400
            db.session.refresh(row)
            assert row.due_date == was

    def test_a_NOTES_only_save_does_not_take_a_GENERATED_rows_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Finding **N-248**, closed by ruling **R-JR** (plan step X-au-h).

        The popover renders the Estimated box on every correctable row and an
        HTML form posts every input it renders, so the door's old test -- *is
        ``estimated_amount`` PRESENT?* -- was true of a save that touched only
        the notes. The row then OWNED a figure nobody chose and stopped
        tracking its definition, silently and with no control that undid it.

        **The payload here is read out of the rendered popover rather than
        assembled**, which is the whole point: `feedback_a_route_test_must_post_
        what_the_template_emits` records that a hand-picked payload once graded
        an arm that was dead in a browser, and this defect lives precisely in
        the gap between what a form sends and what a test remembers to send.
        Extracting both values also grades the template half -- if the popover
        ever stops emitting the companion, this fails here rather than silently
        landing every save on the fail-closed arm.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            row = _generate_first_row(template, seed_user, seed_periods_today)
            assert row is not None, "the fixture generated no rows"
            # The precondition: this row is DERIVED, so ownership is a real
            # state change rather than a no-op on a row that already owns.
            assert row.amount_source_id is not None
            assert row.estimated_amount is None
            assert row.is_override is False

            edit = auth_client.get(f"/transactions/{row.id}/full-edit")
            assert edit.status_code == 200
            body = edit.data.decode()
            shown = re.search(
                r'name="estimated_amount"[^>]*value="([^"]*)"', body,
            )
            companion = re.search(
                r'name="estimated_amount_as_rendered"[^>]*value="([^"]*)"', body,
            )
            assert shown is not None, "the popover renders an amount box"
            assert companion is not None, (
                "the popover must post what it rendered (R-JR); without the "
                "companion every save lands on the fail-closed arm and takes "
                "ownership, which is the defect this case exists for"
            )
            assert companion.group(1) == shown.group(1), (
                "the companion and the box must render from ONE expression"
            )

            resp = auth_client.patch(f"/transactions/{row.id}", data={
                "estimated_amount": shown.group(1),
                "estimated_amount_as_rendered": companion.group(1),
                "pay_period_id": str(row.pay_period_id),
                "status_id": str(row.status_id),
                "notes": "rent went up in March",
                "version_id": row.version_id,
            })
            assert resp.status_code == 200, resp.data

            db.session.refresh(row)
            assert row.notes == "rent went up in March", "the save landed"
            assert row.is_override is False, (
                "a notes-only save must not make the row the owner's"
            )
            assert row.amount_source_id is not None, (
                "a notes-only save must not take the row's amount"
            )
            assert row.estimated_amount is None

    def test_a_RETYPED_amount_on_a_generated_row_still_takes_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The control for the case above, and its fail set must be disjoint.

        Closing **N-248** by refusing every figure would be the same defect
        pointed the other way -- an owner could no longer re-price one instance
        -- and a suite that only asserted "ownership was not taken" would call
        that a pass. This asserts the door still hears a real retype.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()
            row = _generate_first_row(template, seed_user, seed_periods_today)
            assert row.amount_source_id is not None

            edit = auth_client.get(f"/transactions/{row.id}/full-edit")
            companion = re.search(
                r'name="estimated_amount_as_rendered"[^>]*value="([^"]*)"',
                edit.data.decode(),
            )
            assert companion is not None
            typed = Decimal(companion.group(1)) + Decimal("10.00")

            resp = auth_client.patch(f"/transactions/{row.id}", data={
                "estimated_amount": str(typed),
                "estimated_amount_as_rendered": companion.group(1),
                "pay_period_id": str(row.pay_period_id),
                "status_id": str(row.status_id),
                "notes": "",
                "version_id": row.version_id,
            })
            assert resp.status_code == 200, resp.data

            db.session.refresh(row)
            assert row.estimated_amount == typed, "the typed figure is stored"
            assert row.amount_source_id is None, "and the row now OWNS it"
            assert row.is_override is True, "so the row is the owner's"

    def test_full_edit_cannot_clear_a_one_offs_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Emptying the pre-filled due_date input is REFUSED, with the reason.

        **It asserted the clear LANDED until plan step balance:X-bi-7b.**  A
        one-off is a rule-less definition's row since that step and its
        definition prices it AS OF its due date (amount rule 3), so the date
        may move but never be cleared (ruling R-BAL22; the storage-tier
        sentence is ``ck_transactions_template_row_needs_due_date``).  The
        route gate ``_reject_generated_due_date_edit`` answers the empty
        submit with the reason rather than the generic invalid-reference
        sentence, and the date stands.  The popover stops OFFERING the clear
        at the family's second leaf (7b-2); until then this is what the box
        meets.  The schema still maps the empty submit to an explicit
        ``None`` -- the nullable-field rule the case used to grade -- which
        is exactly what reaches the gate.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            auth_client.post("/transactions", data={
                "name": "Clearable Due Date",
                "estimated_amount": "75.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
                "due_date": "2026-02-20",
            })
            txn = db.session.query(Transaction).filter_by(
                name="Clearable Due Date"
            ).one()
            assert txn.due_date == date(2026, 2, 20)

            save_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "due_date": "",
                "version_id": txn.version_id,
            })
            assert save_resp.status_code == 400
            assert b"can be moved but not cleared" in save_resp.data
            db.session.refresh(txn)
            assert txn.due_date == date(2026, 2, 20)

    def test_full_edit_period_selector_renders_and_moves_transaction(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The full-edit popover renders a pay-period selector and saving a
        different period reassigns the transaction (F1 -- change pay period).

        The dropdown is filtered to current + future periods, but always
        includes the row's own period so a row sitting in a past period
        stays selected.  seed_periods_today places today in period index
        4, so index 0 is past (the row's own, included) and index 5 is
        future (a valid move target); index 2 is past and NOT the row's
        own, so it must be excluded.

        The non-transfer update path applies pay_period_id via its
        generic setattr loop after re-verifying ownership (F-029), so the
        move sticks; a period move returns gridRefresh, an in-place edit
        balanceChanged.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            source_period = seed_periods_today[0]   # past -- the row's own
            target_period = seed_periods_today[5]   # future -- valid target
            excluded_past = seed_periods_today[2]   # past, not the row's own
            auth_client.post("/transactions", data={
                "name": "Movable Expense",
                "estimated_amount": "120.00",
                "pay_period_id": source_period.id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
            })
            txn = db.session.query(Transaction).filter_by(
                name="Movable Expense"
            ).one()
            assert txn.pay_period_id == source_period.id

            # The selector renders with the row's own (past) period
            # included and pre-selected, a future period offered, and a
            # different past period excluded.  Isolate the pay-period
            # <select> so option-value assertions cannot collide with the
            # status select (whose ids overlap the period ids).
            edit_resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert edit_resp.status_code == 200
            html = edit_resp.data.decode()
            assert 'name="pay_period_id"' in html
            sel_start = html.index('name="pay_period_id"')
            period_select = html[sel_start:html.index("</select>", sel_start)]
            # Own past period present and selected.  The labels come off the
            # CALENDAR since pay-calendar plan step C4-a-5, which deleted
            # ``PayPeriod.label``: the ``<option>`` renders
            # ``DerivedPeriod.label``, so that is what an assertion about the
            # markup may be built from.
            calendar = calendar_for(seed_user["user"].id)
            assert calendar.period_by_id(
                source_period.id,
            ).label in period_select
            assert f'value="{source_period.id}" selected' in period_select
            # Future period offered.
            assert calendar.period_by_id(
                target_period.id,
            ).label in period_select
            # A past period that is not the row's own is excluded.
            assert calendar.period_by_id(
                excluded_past.id,
            ).label not in period_select

            # Saving a future period reassigns the transaction and asks
            # the client for a full grid refresh so the row relocates to
            # the new period (an in-place cell swap cannot move it).
            save_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "pay_period_id": target_period.id,
                "version_id": txn.version_id,
            })
            assert save_resp.status_code == 200
            assert save_resp.headers.get("HX-Trigger") == "gridRefresh"
            db.session.refresh(txn)
            assert txn.pay_period_id == target_period.id

            # An edit that does NOT move the period keeps the lightweight
            # balanceChanged trigger (no full reload).
            inplace_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "estimated_amount": "130.00",
                # What the popover RENDERS is the resolved figure (the row
                # states none since plan step balance:X-bi-7b).
                "estimated_amount_as_rendered": str(resolved_amount(txn)),
                "pay_period_id": target_period.id,
                "version_id": txn.version_id,
            })
            assert inplace_resp.status_code == 200
            assert inplace_resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_create_inline_no_scenario(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/new/quick with no baseline scenario returns 400.

        The route returns the plain text error 'No baseline scenario' when
        no baseline scenario exists for the user.
        """
        with app.app_context():
            from app.models.scenario import Scenario

            # Delete the baseline scenario.
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            response = auth_client.get(
                f"/transactions/new/quick"
                f"?category_id={seed_user['categories']['Rent'].id}"
                f"&period_id={seed_periods_today[0].id}"
                f"&transaction_type_id={expense_type.id}"
                f"&account_id={seed_user['account'].id}"
            )
            assert response.status_code == 400
            assert b"No baseline scenario" in response.data
