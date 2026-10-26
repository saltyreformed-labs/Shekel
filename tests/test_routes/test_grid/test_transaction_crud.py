"""Grid route tests: a transaction's edit locks, creating, updating, marking, deleting it, and the
action card.
"""

import re
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services import status_seam

from tests._test_helpers import (
    field_is_disabled,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    resolved_amount,
    settlement_if_settling,
)
from app.services.row_valuation import settled_figure

from ._helpers import _TestTransactionCRUDMembers


class TestTransactionCRUD(_TestTransactionCRUDMembers):
    """Edit locks, create, mark, delete, the card; members on _TestTransactionCRUDMembers."""

    def test_quick_edit_disables_amount_on_finalised_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The inline quick-edit disables the amount input on a finalised row
        and shows the revert hint, so the cell never offers an amount edit the
        route guard (#26) would reject.  autofocus marks the editable case."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            auth_client.post(f"/transactions/{txn.id}/mark-done")

            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert field_is_disabled(html, "estimated_amount")
            assert "Finalised" in html
            assert "autofocus" not in html

    def test_quick_edit_amount_editable_on_projected_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A projected row's quick-edit amount stays editable (no lock, no
        regression for the common inline-edit path)."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert not field_is_disabled(html, "estimated_amount")
            assert "Finalised" not in html
            assert "autofocus" in html

    def test_full_edit_locks_money_fields_on_finalised_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The finalised lock covers BUDGET DECISIONS and nothing else.

        The estimate, the period and the due date are decisions the user made,
        locked so a paid movement cannot be retroactively rewritten (#26).  The
        Status dropdown and Notes stay editable, and so do the two OBSERVED
        FACTS -- what the bank took and the day it took it -- because a lock
        protects a decision and an observation gets corrected when the statement
        disagrees (developer ruling, 2026-08-17).

        **The Actual assertion here was the opposite until that ruling**: it
        asserted the box was not drawn at all.  A draft of plan step X-au-c3 had
        rendered it gated on ``locked``, so it appeared ``disabled`` on every
        settled row -- every settled status is also immutable -- and a disabled
        input is never submitted.  The box was deleted as unreachable, and the
        reasoning had the defect backwards: being disabled WAS the defect, and
        the consequence was that correcting a figure required reverting the row,
        which silently re-booked the retained figure over a re-planned amount.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            auth_client.post(f"/transactions/{txn.id}/mark-done")

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "This transaction is finalised" in html
            # Locked money / period / due-date fields.
            assert field_is_disabled(html, "estimated_amount")
            assert field_is_disabled(html, "pay_period_id")
            assert field_is_disabled(html, "due_date")
            # ``settled_amount`` IS drawn and is NOT disabled: it is what the
            # bank took, an observation rather than a budget decision, so it is
            # correctable in place.  Both halves are asserted -- a box that is
            # absent and a box that is present-but-disabled fail the user the
            # same way, and the second is the shape that shipped once already.
            assert 'name="settled_amount"' in html
            assert not field_is_disabled(html, "settled_amount")
            # The revert path and display fields stay editable.
            assert not field_is_disabled(html, "status_id")
            assert not field_is_disabled(html, "notes")

    def test_full_edit_money_fields_editable_on_projected_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A projected row's full-edit money fields stay editable with no
        finalised notice (no regression for the common edit path)."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "finalised" not in html.lower()
            assert not field_is_disabled(html, "estimated_amount")
            assert not field_is_disabled(html, "due_date")

    def test_full_edit_paid_button_gated_by_settled_status(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The desktop full-edit card offers Paid on a projected expense but
        suppresses it once the row is settled (TPLB-06).

        The old predicate ``status_id != STATUS_DONE`` is true for a settled
        row, so Paid was offered where mark_done is an invalid state-machine
        transition (route 400).  ``Status.is_settled`` is the canonical
        'already paid' guard, matching _mobile_card_actions.html.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Projected: the Paid action is offered.
            projected_html = auth_client.get(
                f"/transactions/{txn.id}/full-edit"
            ).data.decode()
            assert "Mark as paid" in projected_html

            # SETTLED: the Paid action is suppressed.  Driven through the
            # SEAM rather than by assigning ``status_id``: since plan step
            # X-au-c3 a settle records what moved in the same call, and a raw
            # column write produces a settled row that records nothing -- a
            # state no door can create and one
            # ``row_valuation.settled_figure`` refuses outright, so the page
            # under test would 500 on a row the app could never have.
            #
            # It walked Paid -> the terminal ``Settled`` archive until plan
            # step **balance:X-am** deleted that status.  The button is gated
            # on ``Status.is_settled``, so the first step was always the one
            # that suppressed it.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            db.session.commit()
            settled_html = auth_client.get(
                f"/transactions/{txn.id}/full-edit"
            ).data.decode()
            assert "Mark as paid" not in settled_html

    def test_create_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions creates a new one-off (a rule-less definition and its row)."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            response = auth_client.post("/transactions", data={
                "name": "New Expense",
                "estimated_amount": "99.99",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert response.status_code == 201

            # Verify the transaction was persisted correctly.
            txn = db.session.query(Transaction).filter_by(
                name="New Expense",
                scenario_id=seed_user["scenario"].id,
            ).one()
            assert resolved_amount(txn) == Decimal("99.99")
            assert txn.pay_period_id == seed_periods_today[0].id
            assert txn.category_id == seed_user["categories"]["Groceries"].id
            assert txn.status.name == "Projected"

    def test_update_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> updates fields."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "estimated_amount": "200.00",
                    "estimated_amount_as_rendered": "123.45",
                },
            )
            assert response.status_code == 200
            assert b"200" in response.data

    def test_mark_expense_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets status to done for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "120.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert settled_figure(txn) == Decimal("120.00")

    def test_mark_income_received(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets status to received for income."""
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = one_off_row_of(
                seed_periods_today[0],
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "2050.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Received"

    def test_soft_delete_template_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id> soft-deletes template-linked items."""
        with app.app_context():
            # A definition's own row (the engine's, plan step balance:X-cf-4).
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Template",
                category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods_today[0])
            db.session.commit()

            response = auth_client.delete(f"/transactions/{txn.id}")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.is_deleted is True

    def test_hard_delete_one_off_transaction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """DELETE /transactions/<id> hard-deletes a one-off, and its definition with it.

        The fork is ``recurs`` (``_delete._leaves_the_books``): a row that
        does not recur leaves the table, and a rule-less definition with no
        row defines nothing, so it goes in the same act (ruling R-BAL27).
        Re-aimed at plan step balance:X-bi-7c: the hand-built link-less row
        this graded took the same arm, and the disposal in full (the price
        series with the definition) is ``test_one_off_row_doors``' delete
        cases'.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id
            definition_id = txn.template_id

            response = auth_client.delete(f"/transactions/{txn_id}")
            assert response.status_code == 200

            # The row is gone from the table, and so is its definition.
            assert db.session.get(Transaction, txn_id) is None
            assert db.session.get(TransactionTemplate, definition_id) is None

    def test_a_zero_row_still_draws_a_click_target(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Finding **N-344**'s second limb, MEASURED rather than read.

        The cell suppressed a zero figure, so a `$0.00` Projected row rendered
        a ``.txn-open`` span containing only whitespace -- an inline-flex
        element with no content is zero pixels wide, so the row could not be
        clicked and its action card could not be opened.  That is the exact
        population this step's delete control has to reach: an envelope emptied
        of its purchases and set back to Projected prices at `$0.00`.

        The ``aria-label`` was always right, which is why the defect was
        visual-only and why asserting on it would have proved nothing -- the
        assertion is on the SPAN'S CONTENT.

        **It read ``0`` and reads ``$0`` since plan step
        ``bank_import:X-gj-2b-3``** (developer ruling 2026-09-01): the four
        figures in this cell go through the ``money`` macro that gate B7 makes
        the one currency formatter, rather than through a second spelling of
        it that could not show a sign while every purchase was positive.  What
        this case grades is unchanged -- that the span has CONTENT.
        """
        with app.app_context():
            # A one-off ENVELOPE priced at $0.00 by its definition: the
            # producer's own state (the flag and the figure are the
            # definition's, R-BAL36 / R-BAL29), not a row-cell write.
            txn = one_off_row_of(
                seed_periods_today[0],
                name="Test Expense",
                amount=Decimal("0.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            db.session.commit()

            html = auth_client.get(f"/transactions/{txn.id}/cell").data.decode()

            start = html.index('<span class="txn-open"')
            inner = html[html.index(">", start) + 1:html.index("<button", start)]
            visible = re.sub(r"<[^>]*>", "", inner).strip()

            assert visible != "", (
                "a row with nothing inside .txn-open cannot be clicked"
            )
            assert visible == "$0", (
                f"the chip should read the row's own figure, got {visible!r}"
            )

    def test_a_refunded_envelope_draws_a_signed_figure_not_a_bare_minus(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The cell's progress display through the ``money`` macro (gate B7).

        Plan step ``bank_import:X-gj-2b-3``, developer ruling 2026-09-01.
        Ruling **bank_import:R-II** made a merchant credit a NEGATIVE purchase,
        so an envelope's entry total can be negative -- and this cell formatted
        it with a raw ``"{:,.0f}".format`` while the mobile card beside it
        (``grid/_grid_row_macros.html``) rendered the SAME two figures through
        ``money``.  The raw spelling could not show a sign at all while every
        purchase was positive, so the two agreed until this step and then
        stopped: it printed ``-87 / 100`` under ``-$87 / $100``.

        Asserts the FULL pair, so restoring either spelling fails: the raw one
        drops both currency marks.
        """
        with app.app_context():
            # A one-off ENVELOPE priced at $100.00 by its definition (the
            # producer's own state, R-BAL36 / R-BAL29).
            txn = one_off_row_of(
                seed_periods_today[0],
                name="Test Expense",
                amount=Decimal("100.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id,
                account_id=txn.account_id,
                owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                # A refund larger than the envelope holds, which is the shape
                # a cross-period merchant credit files (ruling R-II).
                amount=Decimal("-86.67"),
                description="Amazon refund",
                purchased_on=seed_periods_today[0].start_date,
                is_credit=False,
            ))
            db.session.commit()

            html = auth_client.get(f"/transactions/{txn.id}/cell").data.decode()

            assert "-$87 / $100" in html, (
                "the cell's two progress figures go through the money macro"
            )

    def test_the_delete_response_reloads_the_grid(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A delete takes a ROW off the grid, which no cell swap can express.

        ``balanceChanged`` refreshes the balance row and the subtotals in
        place; the row itself would stay on screen with an emptied cell.
        ``gridRefresh`` is what ``cancel_transaction`` and ``unmark_credit``
        already return for the same reason -- they add or remove rows -- and
        this route returned ``balanceChanged`` for as long as it had no caller
        to be wrong for (finding **N-344**, plan step ``bank_import:X-gb``).
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.delete(f"/transactions/{txn.id}")

            assert response.status_code == 200
            assert response.headers["HX-Trigger"] == "gridRefresh"
            assert response.data == b"", (
                "there is no cell left to render"
            )

    def test_the_action_card_offers_the_delete_with_its_consequences(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control exists AND its dialog names what goes.

        Finding **N-344** is that ``DELETE /transactions/<id>`` shipped with
        full teardown logic and zero UI callers.  A test that only asserted the
        button renders would pass over a dialog reading *"Are you sure?"*, which
        is the disclosure shape **N-345** records one screen over -- so this
        asserts the figures too.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.get(f"/transactions/{txn.id}/full-edit")

            assert response.status_code == 200
            html = response.data.decode()
            assert f'hx-delete="/transactions/{txn.id}"' in html
            assert "Delete this row" in html
            assert 'hx-target="#txn-cell-' in html
            # The dialog: what it is, what it is worth, and that it is final.
            assert "Delete &quot;Test Expense&quot;?" in html
            assert "$123.45" in html, "the dialog must name the figure"
            assert "still expects" in html, "a projected row has no record yet"
            assert "This cannot be undone." in html

    def test_a_quote_in_the_row_name_does_not_break_the_dialog(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The whole question survives a name the owner typed.

        **A firing control over a defect this step shipped and caught.**  The
        first draft wrote the name between LITERAL quotes, and a ``{% set %}``
        block is Markup -- so those quotes reached the attribute unescaped and
        CLOSED it: ``hx-confirm="Delete "`` with the rest of the sentence
        parsed as three more attributes on the button.  The dialog then asked
        "Delete" over a row worth `$123.45` and named neither the figure nor
        the bank lines, which is the disclosure failure the control exists to
        avoid.  A name carrying its own quote is the case that would have made
        it permanent.
        """
        with app.app_context():
            # The name is the DEFINITION's (a placed row reads it), so it is
            # given to the producer rather than written on the row.
            txn = self._create_test_txn(
                seed_user, seed_periods_today, name='Kayla\'s "Fun" Money',
            )
            db.session.commit()

            html = auth_client.get(
                f"/transactions/{txn.id}/full-edit",
            ).data.decode()

            confirm = re.search(r'hx-confirm="([^"]*)"', html)
            assert confirm is not None
            question = confirm.group(1)
            assert "&#34;Fun&#34;" in question, (
                "the owner's own quotes are escaped, not dropped"
            )
            assert "$123.45" in question, (
                "the attribute must not end before the figure"
            )
            assert "This cannot be undone." in question, (
                "the attribute must not end before the last sentence"
            )
