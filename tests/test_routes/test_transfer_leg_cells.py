"""
Shekel Budget App -- The grid draws a transfer as a LEG read off its parent.

Plan step **balance:X-bi-6-1**, rulings **R-BAL86** (the decomposition) and
**R-BAL87** (the leg's identity).  Until this leaf the grid loaded a
transfer's two SHADOW rows in ``budget.transactions`` and drew each as an
ordinary cell whose doors were the transaction routes; now it loads the set's
OWN rows plus one :class:`~app.services.transfer_legs.TransferLeg` per
transfer it shows, keyed ``(transfer id, account id)``, priced by the leg map
producers and drawn by the same cell partial, and the cell's doors are the
transfer's own routes carrying ``leg_account_id``.

**What each control here grades, and why it is a control rather than a
reading.**  The byte-identity harness (``tests/manual/verify_grid_cells.py``)
proved the leaf moved no cell's words on the production restore; it cannot
show WHICH relation a cell was drawn from, because on that data the shadow's
stored name and the endpoints' names agree.  So the first case below is the
one the design's own sentence names -- a leg's label follows an account
RENAME, where a shadow's stored ``name`` went stale -- and it fails on the
shadow-reading grid by construction.  The rest grade the states the restore
does not exercise in the default window (a settled leg's recorded figure, a
reverted leg's retained figure, the mobile-card arm of the transfer door) and
the refusals (a leg on neither endpoint).
"""

import re
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.ref import AccountType, Status
from app.services import account_service, transfer_service
from app.services.grid_view_service import leg_dom_id
from tests._test_helpers import (
    create_account_of_type,
    open_books_before_the_first_assertion,
)


def _create_savings(seed_user, name="Savings"):
    """A second account for the test user, books opened before today."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.flush()
    open_books_before_the_first_assertion(db.session, acct)
    db.session.commit()
    return acct


def _create_transfer(seed_user, period, savings, amount=Decimal("200.00")):
    """An ad-hoc checking -> savings transfer in *period*, owning *amount*.

    Callers pass ``seed_periods_today[4]``, TODAY's paycheck: the grid's
    default window starts there, and a transfer filed in a past period is
    drawn by no default render.
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="Monthly Savings",
        ),
    )
    db.session.commit()
    return xfer


def _cell(html, cell_id):
    """The desktop cell block whose wrapper is *cell_id*, to its ``</td>``."""
    start = html.index(f'id="{cell_id}"')
    return html[start:html.index("</td>", start)]


class TestTheGridDrawsALegOffItsParent:
    """The window holds the leg, and the leg is the parent's."""

    def test_the_cell_is_the_leg(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Checking's grid draws ``xfer-leg-<id>-<checking>``, carrying the transfer's doors.

        The window is the set's OWN rows plus legs
        (``cash_flow_set.own_rows_clause`` + ``grid_transfer_legs``).  Named
        ``test_the_cell_is_the_leg_and_no_shadow_row_is_drawn`` until plan
        step ``balance:X-bi-6-4d-2``, which births no shadow: it also
        asserted the expense shadow's ``txn-cell-<shadow id>`` absent, and
        with no such row there is no id to look for (ruling R-BAL167 class
        1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            xfer_id = xfer.id

            html = auth_client.get("/grid").get_data(as_text=True)

        leg_id = leg_dom_id(xfer_id, checking_id)
        assert f'id="{leg_id}"' in html
        cell = _cell(html, leg_id)
        assert f'data-xfer-id="{xfer_id}"' in cell
        assert f'data-leg-account-id="{checking_id}"' in cell
        assert f'data-cell="{leg_id}"' in cell
        assert f"/transfers/instance/{xfer_id}/mark-done" in cell
        assert "/transactions/" not in cell
        assert "Transfer to Savings: $200.00 -- Projected" in cell

    def test_a_legs_label_follows_an_account_rename(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Rename the savings account and the leg's row label follows.

        **The leaf's one visible behaviour change, and the control that fails
        on the shadow-reading grid**: a shadow's ``name`` was written at
        creation ("Transfer to Savings") and no rename touched it, so the old
        grid kept showing the stale name; a leg's label is composed from the
        endpoints' CURRENT names (``transfer_legs.leg_label``) at every
        render.  What makes the negative assertion mean something is the
        grid BEFORE the rename, which drew the old name.  It also read the
        shadow's stored name, still the old one, until plan step
        ``balance:X-bi-6-4d-2``, which births no shadow; the label
        assertions are kept, the case being one of three database-backed pins
        that an in-session rename re-labels the derived leg (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            before = auth_client.get("/grid").get_data(as_text=True)
            assert "Transfer to Savings: $200.00" in before

            savings.name = "Emergency Fund"
            db.session.commit()

            after = auth_client.get("/grid").get_data(as_text=True)
            cell = _cell(after, leg_dom_id(xfer.id, checking_id))

        assert "Transfer to Emergency Fund: $200.00" in cell
        assert "Transfer to Savings" not in cell
        # The row header strips the composed prefix, so the label shows the
        # account alone -- and the new name.
        assert re.search(r'row-label[^>]*>\s*Emergency Fund\s*<', after)
        assert not re.search(r'row-label[^>]*>\s*Savings\s*<', after)

    def test_a_settled_leg_draws_its_recorded_figure_and_no_pay_button(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """After a settle the leg's cell says Paid, shows what moved, offers no Mark Paid.

        The figure is the transfer's (``settled_amounts_by_key`` values the
        leg by its parent, ``Transfer.settled_figure``; the leg's record
        holds none); the settle here
        typed a correction so the recorded figure DIFFERS from the plan and
        the cell's strike-through arm is exercised, not just its presence.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.DONE)),
                    "settled_amount": "187.50",
                    "leg_account_id": str(checking_id),
                },
            )
            assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
            html = auth_client.get("/grid").get_data(as_text=True)
            cell = _cell(html, leg_dom_id(xfer.id, checking_id))

        assert "-- Paid" in cell
        assert "$187.50" in cell
        assert "st-done" in cell
        assert 'class="paybtn"' not in cell

    def test_a_reverted_leg_draws_what_a_re_settle_would_book(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Settle with a typed figure, revert: the cell says what marking paid records.

        The retained figure is the transfer's, kept across the revert beside
        each side's kept, un-dated record (``retained_amounts_by_key``, the
        leg valued by its transfer through ``status_seam.honoured_correction``),
        the one map a plain row's cell reads too (ruling plan#1983).
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            done = ref_cache.status_id(StatusEnum.DONE)
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            settle = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(done), "settled_amount": "187.50"},
            )
            assert settle.status_code == 200, settle.get_data(as_text=True)[:300]
            revert = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(projected)},
            )
            assert revert.status_code == 200, revert.get_data(as_text=True)[:300]
            html = auth_client.get("/grid").get_data(as_text=True)
            cell = _cell(html, leg_dom_id(xfer.id, checking_id))

        assert "-- Projected" in cell
        assert "marking paid records $187.50" in cell
        assert 'class="paybtn"' in cell


class TestAPaymentToTheOtherCardSeenFromACard:
    """Two cards, a checking -> card B payment, the grid seen from card A.

    The cash-flow set is checking plus BOTH cards whichever member's balance
    line renders (``account_resolver``), so from card A the payment has both
    endpoints in the set and the balance line on neither -- the arm the
    leaf's first cut turned from "drawn nowhere" (the row rule's answer) into
    a 500 (the leg loader asked ``leg_of`` for a leg on A).  Pins the page
    rendering and the payment drawn nowhere on it, which is today's rule and
    an open question for the developer (R-CC23 never named this arm).
    """

    def test_the_grid_renders_and_the_payment_is_drawn_nowhere(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            checking = seed_user["account"]
            card_a = create_account_of_type(
                seed_user, db.session, "Credit Card", "Card A",
            )
            card_b = create_account_of_type(
                seed_user, db.session, "Credit Card", "Card B",
            )
            db.session.commit()
            payment = _create_transfer(
                seed_user, seed_periods_today[4], card_b, amount=Decimal("165.00"),
            )
            xfer_id, a_id, b_id, checking_id = (
                payment.id, card_a.id, card_b.id, checking.id,
            )
            resp = auth_client.get(f"/grid?account_id={a_id}")
            from_checking = auth_client.get("/grid")

        assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
        html = resp.get_data(as_text=True)
        for account_id in (checking_id, a_id, b_id):
            assert f'id="{leg_dom_id(xfer_id, account_id)}"' not in html
        # From checking's own line the same payment shows once, from checking.
        assert from_checking.status_code == 200
        assert f'id="{leg_dom_id(xfer_id, checking_id)}"' in from_checking.get_data(as_text=True)


class TestTheTransferDoorsServeTheLeg:
    """The leg's cell and card come back from the transfer's own doors."""

    def test_full_edit_refuses_a_leg_on_neither_endpoint(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?leg_account_id=`` naming an account the transfer does not touch is 404."""
        with app.app_context():
            savings = _create_savings(seed_user)
            elsewhere = _create_savings(seed_user, name="Elsewhere")
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            ok = auth_client.get(
                f"/transfers/{xfer.id}/full-edit?leg_account_id={savings.id}",
            )
            refused = auth_client.get(
                f"/transfers/{xfer.id}/full-edit?leg_account_id={elsewhere.id}",
            )
        assert ok.status_code == 200
        assert refused.status_code == 404

    def test_the_mobile_card_mark_paid_swaps_the_legs_card_in_place(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``render=mobile_card`` on the transfer door returns the leg's CARD.

        The card's Mark Paid form posts ``render=mobile_card`` and its tab
        prefix; the response is one card (``card-tp-xfer-<id>-<account>``)
        drawn settled, with ``HX-Trigger: mobileCardSettled`` so the This
        Period summary blocks its self-refresh -- the transaction card's
        shape, on the leg's door.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={
                    "leg_account_id": str(checking_id),
                    "render": "mobile_card",
                    "card_prefix": "tp",
                    "can_edit": "1",
                },
            )
            xfer_id = xfer.id
        assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
        html = resp.get_data(as_text=True)
        assert f'id="card-tp-xfer-{xfer_id}-{checking_id}"' in html
        assert f'data-mobile-leg="{xfer_id}-{checking_id}"' in html
        assert "badge-done" in html
        assert "Mark Paid" not in html
        assert resp.headers.get("HX-Trigger") == "mobileCardSettled"

    def test_a_refused_mobile_mark_paid_on_a_cancelled_leg_keeps_the_cards_id(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A stale card's Mark Paid on a CANCELLED transfer: a 400 banner in the card's wrapper.

        A cancelled leg yields no row key (the card lists filter cancelled
        items out), so the refusal swaps in the banner-only wrapper that
        keeps the requesting card's id -- ``_mobile_card_error.html``, whose
        id now composes through ``card_dom_id`` for a leg as for a row.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()
            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={
                    "leg_account_id": str(checking_id),
                    "render": "mobile_card",
                    "card_prefix": "tp",
                    "can_edit": "1",
                },
            )
            xfer_id = xfer.id
        assert resp.status_code == 400
        html = resp.get_data(as_text=True)
        assert f'id="card-tp-xfer-{xfer_id}-{checking_id}"' in html
        assert "Invalid transfer status transition" in html

    def test_a_stale_card_of_a_cancelled_leg_answers_the_legs_desktop_cell(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A stale card's conflict on a CANCELLED transfer: the leg's desktop cell.

        A stale PATCH's 409 names no refusal, and a cancelled leg yields no
        card row key, so the one card renderer falls back to the item's
        desktop cell -- the rule a row's card takes (finding BAL-565's race),
        written once for both shapes by ruling plan#1983's route fold and
        decided by the lane.  The transfer twin drew the banner-only wrapper
        here with no message: a red banner reading ``None``.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()
            xfer_id = xfer.id
            # A card form pinned to a version the cancel has since bumped.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "leg_account_id": str(checking_id),
                    "version_id": "1",
                    "render": "mobile_card",
                    "card_prefix": "tp",
                    "can_edit": "1",
                },
            )
        assert resp.status_code == 409, resp.get_data(as_text=True)[:300]
        html = resp.get_data(as_text=True)
        assert f'data-cell="{leg_dom_id(xfer_id, checking_id)}"' in html
        assert "$200" in html
        assert "mobile-card-wrapper" not in html
        assert "<span>None</span>" not in html

    def test_one_card_renderer_falls_back_alike_for_a_row_and_a_leg(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No card row key and no refusal: a row's card and a leg's answer their desktop cells.

        The two shapes' cards are ONE renderer since ruling plan#1983
        (``routes/_mobile_card.render_mobile_card``), so the fallback is one
        rule: a cancelled row and a cancelled transfer's leg, each drawn as a
        card with no refusal to show, come back as the cell the desktop grid
        draws for that item -- its own ``data-cell`` id and its own figure.
        """
        # pylint: disable=import-outside-toplevel
        from flask_login import login_user

        from app.enums import TxnTypeEnum
        from app.models.transaction import Transaction
        from app.models.transfer import Transfer
        from app.routes._mobile_card import render_mobile_card
        from app.services.transfer_legs import leg_of
        from tests._test_helpers import one_off_row_of

        with app.app_context():
            period = seed_periods_today[4]
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, period, savings)
            row = one_off_row_of(
                period, name="Water", amount=Decimal("61.00"),
                user_id=period.user_id, account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()
            row_id, xfer_id = row.id, xfer.id
            checking_id = seed_user["account"].id
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()
            cancelled_row = auth_client.post(f"/transactions/{row_id}/cancel")
            assert cancelled_row.status_code == 200, (
                cancelled_row.get_data(as_text=True)[:300]
            )
            with app.test_request_context("/"):
                login_user(seed_user["user"])
                row_card = render_mobile_card(
                    db.session.get(Transaction, row_id),
                    card_prefix="tp", can_edit=True,
                )
                leg_card = render_mobile_card(
                    leg_of(
                        db.session.get(Transfer, xfer_id), checking_id,
                    ),
                    card_prefix="tp", can_edit=True,
                )
        assert f'data-cell="txn-cell-{row_id}"' in row_card
        assert "$61" in row_card
        assert f'data-cell="{leg_dom_id(xfer_id, checking_id)}"' in leg_card
        assert "$200" in leg_card
        for card in (row_card, leg_card):
            assert "mobile-card-wrapper" not in card

    def test_the_popover_from_a_leg_carries_the_leg_back_on_every_door(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The form, Paid and Cancel buttons all target the leg's cell and post its account."""
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            checking_id = seed_user["account"].id
            html = auth_client.get(
                f"/transfers/{xfer.id}/full-edit?leg_account_id={checking_id}",
            ).get_data(as_text=True)
            xfer_id = xfer.id
        target = f'hx-target="#{leg_dom_id(xfer_id, checking_id)}"'
        assert html.count(target) == 3, html.count(target)
        assert f'name="leg_account_id" value="{checking_id}"' in html
        assert html.count(f'hx-vals=\'{{"leg_account_id": "{checking_id}"}}\'') == 2
        assert 'name="amount_as_rendered"' in html


class TestTheTransfersPageCellShowsTheRecord:
    """Findings N-303 and N-420, closed where R-BAL87 said: the transfers page's cell.

    The cell was handed ``budgets`` alone, so after a corrected settle it
    painted the PLAN over the figure the same click's popover had just
    recorded (N-303), and it printed that figure through a raw format
    string rather than ``money`` (N-420).  It reads the popover's two maps
    now and draws a settled transfer as the grid draws a settled row.
    """

    def test_a_corrected_settle_shows_the_record_struck_against_the_plan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            settle = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.DONE)),
                    "settled_amount": "187.50",
                },
            )
            assert settle.status_code == 200, settle.get_data(as_text=True)[:300]
            # The transfers page's own cell, with no grid context.
            cell = auth_client.get(f"/transfers/cell/{xfer.id}").get_data(as_text=True)
        assert "$187.50 (est: $200.00) (Paid)" in cell
        assert "text-decoration-line-through" in cell
        assert "$188" in cell and "$200" in cell
        assert '"{:,.0f}".format' not in cell

    def test_a_reverted_transfer_says_what_a_re_settle_would_book(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.DONE)),
                    "settled_amount": "187.50",
                },
            )
            revert = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(ref_cache.status_id(StatusEnum.PROJECTED))},
            )
            assert revert.status_code == 200
            cell = auth_client.get(f"/transfers/cell/{xfer.id}").get_data(as_text=True)
        assert "$200.00 -- marking paid records $187.50 (Projected)" in cell

    def test_the_conflict_cell_of_a_deleted_transfer_still_renders(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rival's delete won: the stale response draws the plan, not a 500.

        The pair loader refuses a deleted parent, so the renderer publishes
        the two maps empty for one rather than asking; this pins that the
        designed conflict fragment survives the maps' arrival.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[4], savings)
            xfer_id = xfer.id
            transfer_service.delete_transfer(xfer_id, seed_user["user"].id, soft=True)
            db.session.commit()
            # A form pinned to a version the delete has since bumped.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={"notes": "late edit", "version_id": "1"},
            )
        assert resp.status_code == 409, resp.get_data(as_text=True)[:300]
        html = resp.get_data(as_text=True)
        assert "changed by another action" in html
        assert "$200.00" in html

