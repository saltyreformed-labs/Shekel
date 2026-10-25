"""Transfer route tests: the grid cell partials, and the leg's grid cell each transfer handler
renders.
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transfer import Transfer
from app.models.ref import AccountType
from app.services import account_service
from tests._test_helpers import field_is_disabled, transfer_side_settle_day
from tests._test_helpers import rendered_transfer_amount
from tests._test_helpers import transfer_amount

from ._helpers import (
    _create_savings_account,
    _create_template,
    _create_transfer,
    _create_other_user_with_template,
)


# ── Grid Cell Routes ───────────────────────────────────────────────


class TestGridCells:
    """Tests for grid cell HTMX partial endpoints."""

    def test_get_cell(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/cell/<id> returns the cell partial."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/cell/{xfer.id}")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b"200" in response.data

    def test_get_quick_edit(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/quick-edit/<id> returns the quick-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/quick-edit/{xfer.id}")

            assert response.status_code == 200
            assert b'name="amount"' in response.data
            assert b"200" in response.data

    @pytest.mark.parametrize(
        ("url_template", "expects_a_money_box"),
        [
            ("/transfers/cell/{}", False),
            ("/transfers/quick-edit/{}", True),
            ("/transfers/{}/full-edit", True),
        ],
        ids=["cell", "quick-edit", "full-edit"],
    )
    def test_a_DERIVED_transfer_renders_its_definitions_price_not_its_column(
        self, app, auth_client, seed_user, seed_periods_today,
        url_template, expects_a_money_box,
    ):
        """A transfer that stores NO figure still shows one, and it is the series'.

        **The control plan step balance:X-au-f-1 exists to make possible**, and
        the one that discriminates: it FAILS on the code this leaf replaces.
        Every transfer in production today owns its figure, so a test built on
        one grades nothing -- the column and the amount model return the same
        `Decimal`, and reverting the leaf leaves it green. This one declares the
        transfer DERIVED, which is the state plan step `X-au-f-3`'s migration
        creates for 169 production rows: `budget.transfers.amount` is NULL, and a
        template reading that column renders Jinja's `None` (finding **N-452**).

        **The series states TWO prices and the assertion picks the one governing
        the transfer's OWN due date**, so a fragment that resolved on the wrong
        date -- today, the pay period's start, the newest version -- fails here
        rather than agreeing by accident. `$450.00` is stated after the
        transfer's due date and `$400.00` before it, so the answer is `$400.00`.

        The money boxes are SCRAPED out of the response rather than compared
        against a hand-built payload: the box and its `amount_as_rendered`
        companion must read ONE expression, which is what ruling **R-JR**'s
        authorship comparison rests on -- a box and a companion that could
        resolve differently would report a re-price nobody made.
        """
        # Pylint: ``import-outside-toplevel`` (1/1) -- the module's import block
        # is models and services; the amount model's write door and its enum are
        # named only by this case.
        # pylint: disable-next=import-outside-toplevel
        from app.enums import AmountSourceEnum
        # pylint: disable-next=import-outside-toplevel
        from app.services import template_amount_service
        # pylint: disable-next=import-outside-toplevel
        from app.services.amount_ownership import declare_derived

        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(
                seed_user, seed_periods_today, savings, template=template,
            )
            due = seed_periods_today[0].start_date
            template_amount_service.set_amount(
                template, Decimal("400.00"),
                effective_on=due - timedelta(days=30),
            )
            template_amount_service.set_amount(
                template, Decimal("450.00"),
                effective_on=due + timedelta(days=30),
            )
            xfer.due_date = due
            # The state X-au-f-3's migration writes: the definition prices this
            # row, so the row stores no figure at all.
            declare_derived(xfer, AmountSourceEnum.TEMPLATE)
            db.session.commit()
            assert xfer.amount is None

            response = auth_client.get(url_template.format(xfer.id))

            assert response.status_code == 200
            html = response.data.decode()
            # The failure this guards is Jinja rendering the empty column into
            # an attribute.  Scoped to `value="None"` rather than the bare word:
            # the popover's category select legitimately offers `-- None --`,
            # and a test that fails on THAT is failing for the wrong reason.
            assert 'value="None"' not in html
            if not expects_a_money_box:
                # The display cell prints the figure without cents.
                assert "400" in html
                return
            box = re.search(r'name="amount"[^>]*\bvalue="([^"]*)"', html)
            companion = re.search(
                r'name="amount_as_rendered"[^>]*\bvalue="([^"]*)"', html,
            )
            assert box is not None, "the amount box rendered no value at all"
            assert companion is not None, "no amount_as_rendered companion"
            assert Decimal(box.group(1)) == Decimal("400.00")
            assert box.group(1) == companion.group(1)

    def test_get_full_edit(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/<id>/full-edit returns the full-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/{xfer.id}/full-edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b'name="amount"' in response.data

    def test_quick_edit_disables_amount_on_finalised_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The transfer inline quick-edit disables the amount input on a
        finalised transfer and shows the revert hint (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            resp = auth_client.get(f"/transfers/quick-edit/{xfer.id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert field_is_disabled(html, "amount")
            assert "Finalised" in html
            assert "autofocus" not in html

    def test_full_edit_locks_money_fields_on_finalised_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The transfer full-edit disables amount / period / category /
        due-date on a finalised transfer and shows the revert notice, while
        the Status dropdown and Notes stay editable (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "This transfer is finalised" in html
            assert field_is_disabled(html, "amount")
            assert field_is_disabled(html, "pay_period_id")
            assert field_is_disabled(html, "category_id")
            assert field_is_disabled(html, "due_date")
            assert not field_is_disabled(html, "status_id")
            assert not field_is_disabled(html, "notes")

    def test_full_edit_editable_on_projected_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A projected transfer's full-edit money fields stay editable with no
        finalised notice (no regression)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "finalised" not in html.lower()
            assert not field_is_disabled(html, "amount")

    def test_full_edit_from_a_grid_leg_renders_due_date_input_and_targets_the_leg(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transfers/<id>/full-edit?leg_account_id= renders the editable due date.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87).**  It
        asked ``GET /transactions/<shadow>/full-edit``, the transaction door
        that detected a SHADOW row and answered the transfer form; the grid
        draws a transfer's LEG off its parent now and the leg's cell asks the
        TRANSFER's own door, naming the account the leg is on.  The claim is
        the same -- a transfer with no due date renders the input (empty) so
        the user can add one -- plus what the leg needs: the form targets the
        leg's cell and posts the leg back so the route re-renders that cell.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            checking_id = seed_user["account"].id

            response = auth_client.get(
                f"/transfers/{xfer.id}/full-edit?leg_account_id={checking_id}",
            )

            assert response.status_code == 200
            html = response.data.decode()
            assert 'name="due_date"' in html
            assert 'type="date"' in html
            assert f'name="leg_account_id" value="{checking_id}"' in html
            assert f'hx-target="#xfer-leg-{xfer.id}-{checking_id}"' in html

    def test_get_cell_other_users_transfer(self, app, auth_client, seed_user):
        """GET /transfers/cell/<id> for another user's transfer returns 404.

        Read-path IDOR: response must not leak the other user's transfer data.
        """
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.get(f"/transfers/cell/{other['transfer'].id}")

            assert response.status_code == 404
            # Verify no leakage of the other user's transfer data.
            assert b"Other Transfer" not in response.data
            assert b"100.00" not in response.data


# ── Shadow Context Response Tests (H1 fix) ────────────────────────


class TestLegContextResponse:
    """Verify that transfer route handlers render the LEG's grid cell
    (not _transfer_cell.html) when the request includes ``leg_account_id``,
    indicating the form was opened from a transfer leg's cell in the grid.

    **Was ``TestShadowContextResponse`` until plan step balance:X-bi-6-1
    (ruling R-BAL87)**: the marker was ``source_txn_id``, the SHADOW row's id,
    and the response was that row's transaction cell.  The grid draws a
    transfer's leg off its parent now, so the marker is the leg's account and
    the response is the leg's cell -- the same partial, drawing a
    ``TransferLeg``, whose opener carries ``data-xfer-id`` and
    ``data-leg-account-id`` and whose wrapper is ``xfer-leg-<id>-<account>``.

    Each case's leg is the transfer's FROM-side, read off the transfer.  A
    file-local ``_get_expense_shadow`` found it as the expense twin's account
    until plan step ``balance:X-bi-6-4d-2``, which births no twin; the twin
    sat on the from-side (ruling R-BAL167 class 4, plan step
    balance:X-bi-6-4d-2).

    Fixes H1, L2, L3 from transfer_rework_verification.md.
    """

    def test_update_from_leg_renders_the_leg_cell(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with leg_account_id renders the leg's grid cell.

        When the transfer full edit popover is opened from a leg's cell in
        the grid, the response must be that leg's cell (the grid's partial,
        keyed by the leg's account) so the cell remains interactive after
        the update -- and NOT the transfers page's cell.

        The amount is read off the transfer, re-read from the database.  The
        expense twin's amount was read too until plan step
        ``balance:X-bi-6-4d-2``, which births none: each side's figure is the
        transfer's, projected (graded field by field by
        ``test_transfer_legs/test_what_a_side_shows.py::TestBothSidesShowTheTransfer``; ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            leg_account_id = xfer.from_account_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "amount": "300.00",
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "leg_account_id": str(leg_account_id),
                },
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Must render the LEG's cell: the grid partial's opener names the
            # transfer and the leg's account, and its Mark Paid is the
            # transfer's door.
            assert f'data-xfer-id="{xfer.id}"' in html
            assert f'data-leg-account-id="{leg_account_id}"' in html
            assert f'data-cell="xfer-leg-{xfer.id}-{leg_account_id}"' in html
            assert "/transactions/" not in html
            # Must NOT render _transfer_cell.html (has transfer routes).
            assert "xfer-cell-" not in html
            assert "transfers/quick-edit" not in html.replace("transfers/instance", "")

            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            # Verify the transfer amount was actually updated.
            db.session.expire_all()
            assert transfer_amount(
                db.session.get(Transfer, xfer.id),
            ) == Decimal("300.00")

    def test_mark_done_from_leg_renders_the_leg_cell_with_grid_refresh(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST mark-done with leg_account_id renders the leg's cell
        and triggers gridRefresh (not balanceChanged).

        Status changes affect subtotal rows and cell visibility, so the
        transfer route must match the transaction route pattern of
        triggering gridRefresh when called from a grid cell context.  The
        cell it returns is SETTLED: its opener's label says Paid and the
        Mark Paid button is gone.  The transfer's status is the one status
        since plan step ``balance:X-bi-6-4d-2``, so its two sides are graded
        on their payment records, each dated, where the twins' statuses were
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            leg_account_id = xfer.from_account_id

            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={"leg_account_id": str(leg_account_id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # The leg's cell, not the transfers page's cell.
            assert f'data-leg-account-id="{leg_account_id}"' in html
            assert "xfer-cell-" not in html
            assert "-- Paid" in html
            assert 'class="paybtn"' not in html

            # Must trigger gridRefresh for status changes.
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            # Verify the transfer is done and both its sides recorded it.
            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"
            assert all(
                transfer_side_settle_day(db.session, xfer.id, account_id)
                is not None
                for account_id in (xfer.from_account_id, xfer.to_account_id)
            )

    def test_cancel_from_leg_renders_the_leg_cell_with_grid_refresh(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST cancel with leg_account_id renders the leg's cell
        and triggers gridRefresh.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            leg_account_id = xfer.from_account_id

            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/cancel",
                data={"leg_account_id": str(leg_account_id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            assert f'data-leg-account-id="{leg_account_id}"' in html
            assert "xfer-cell-" not in html
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_update_without_leg_account_id_renders_transfer_cell(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH without leg_account_id renders _transfer_cell.html (regression).

        When the transfer management page (not the grid) submits an
        update, there is no leg_account_id.  The response must render
        the transfer cell template with ``xfer-cell-`` IDs as before.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "350.00", "amount_as_rendered": rendered_transfer_amount(xfer)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Must render _transfer_cell.html (management page context).
            assert f"xfer-cell-{xfer.id}" in html or "xfer-cell-" in html

            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("350.00")

    def test_invalid_leg_account_id_falls_back_gracefully(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with a nonexistent leg_account_id falls back to the transfer cell.

        If leg_account_id names no account (tampered or stale), the
        handler must not crash.  It falls back to the transfer cell
        template as a safe default.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "amount": "400.00",
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "leg_account_id": "999999",
                },
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Falls back to transfer cell (safe default).
            assert "xfer-cell-" in html

            # Data still updated correctly.
            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("400.00")

    def test_mismatched_leg_account_id_falls_back_gracefully(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with a leg_account_id on NEITHER endpoint falls back.

        If leg_account_id names a real account of the owner's that this
        transfer does not touch -- a stale page whose transfer was
        re-pointed meanwhile -- the handler must not render a leg the
        transfer has no side on.  It falls back to the transfer cell
        template.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(
                seed_user, seed_periods_today, savings,
                amount=Decimal("200.00"), name="Transfer A",
            )
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            elsewhere = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Elsewhere",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(elsewhere)
            db.session.commit()

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "amount": "450.00",
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "leg_account_id": str(elsewhere.id),
                },
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Falls back to transfer cell (mismatch detected).
            assert "xfer-cell-" in html

            # The transfer still updated correctly.
            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("450.00")
